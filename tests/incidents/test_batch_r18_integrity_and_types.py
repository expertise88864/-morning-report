# -*- coding: utf-8 -*-
"""r18 外審:一個 P1 + 三個 P2,主題是**兩個狀態被折成同一個**。

  * `model_history` 的 manifest **壞掉**被讀成**不存在** → 替被改過的歷史重新簽名
  * NumPy 純量不是 Python 純量 → 數字悄悄變字串
  * `set` 用 `str` 當排序鍵不是全序 → 同一份證據兩種表示
  * normalization 診斷只進 telemetry,沒有任何判準消費它
"""
import morning_report as mr  # noqa: E402
import decimal
import gzip
import json
import sys
from pathlib import Path

import numpy as np
import pytest
import yaml

_ROOT = Path(mr.__file__).resolve().parent
sys.path.insert(0, str(_ROOT))
import evidence_serialize as es                                # noqa: E402
import model_history_store as mh                               # noqa: E402
import run_quality as rq                                       # noqa: E402

_WF_WD = _ROOT / ".github" / "workflows" / "report-watchdog-b.yml"


# ------------------------------------------------------------------ P1
def _partition(d: Path, name: str, rows: list) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_bytes(gzip.compress(json.dumps(rows, ensure_ascii=False).encode()))
    return p


_ROW = {"session_date": "2026-07-01", "taiex_close": 24000.0,
        "stocks": {}, "model_version": "v1"}


def test_a_corrupt_manifest_is_not_read_as_no_manifest(tmp_path):
    """**壞掉 ≠ 沒有。** 這是 `state_store` 早就立好、而這裡最後一個沒有
    套用的規則。"""
    d = tmp_path / "mh"
    _partition(d, "2026-07.json.gz", [_ROW])
    assert mh._read_manifest_partitions(d) == {}          # 真的沒有 → 可初始化
    mh.write_partition_manifest(d)

    for broken in ("{ 不是 JSON", "[]", '{"partitions": []}', '"字串"'):
        (d / mh.MANIFEST_NAME).write_text(broken, encoding="utf-8")
        with pytest.raises(mh.HistoryIntegrityError):
            mh._read_manifest_partitions(d)


def test_a_corrupt_manifest_never_re_signs_the_history(tmp_path):
    """**這是 P1 的核心反例。**

    manifest 壞掉的那一天,`old` 先前會是 `{}` —— writer 於是把磁碟上
    **每一個**分區都當成「全新分區」,拿現在的內容重算 sha256 寫成新基線。
    也就是說:被竄改過的分區會**被重新簽名**,完整性驗證隔天就變綠。
    那不是偵測到歷史被改,是替被改過的歷史背書。
    """
    d = tmp_path / "mh"
    part = _partition(d, "2026-07.json.gz", [_ROW])
    mh.write_partition_manifest(d)
    good_manifest = (d / mh.MANIFEST_NAME).read_text(encoding="utf-8")
    good_sha = json.loads(good_manifest)["partitions"]["2026-07.json.gz"]["sha256"]

    # 分區被改過(仍是合法 JSON),而 manifest 同時壞掉
    _partition(d, "2026-07.json.gz", [dict(_ROW, taiex_close=99999.0)])
    tampered_bytes = part.read_bytes()
    (d / mh.MANIFEST_NAME).write_text("{ 壞掉了", encoding="utf-8")

    with pytest.raises(mh.HistoryIntegrityError):
        mh.write_partition_manifest(d, rewritten=set())

    # 壞掉的 manifest 原封不動(它是證據),分區也沒被碰
    assert (d / mh.MANIFEST_NAME).read_text(encoding="utf-8") == "{ 壞掉了"
    assert part.read_bytes() == tampered_bytes
    # **最關鍵**:沒有產生一份「與被改過的內容相符」的新簽名
    assert good_sha != mh.payload_sha256([dict(_ROW, taiex_close=99999.0)])
    assert not (d / (mh.MANIFEST_NAME + ".tmp")).exists()


def test_the_writer_still_initializes_a_genuinely_new_store(tmp_path):
    """不可以矯枉過正:真的沒有 manifest 時照樣要能建立第一份基線。"""
    d = tmp_path / "mh"
    _partition(d, "2026-07.json.gz", [_ROW])
    m = mh.write_partition_manifest(d)
    assert m["partitions"]["2026-07.json.gz"]["row_count"] == 1
    assert mh.verify_history_integrity(d, strict=False)["ok"]


def test_a_present_manifest_that_omits_a_file_is_not_a_blank_slate(tmp_path):
    """**Codex deep 第一輪 P1:我的修正只補了一半。**

    上一版只擋「解析/型別壞掉」;而 `{"partitions": {}}` 或漏記某些分區的
    manifest 仍然回 `{}` —— 與「真的沒有 manifest」完全一樣,於是那些沒被
    記到的既有分區照樣走「全新分區」那條路,拿**現在的內容**重算 sha256。
    重新簽名的路徑還在,只是入口換了一個。

    而 `verify_history_integrity()` 對同一種情形報 `extra_partition`
    —— **已經是完整性違規**。兩邊不可以對同一份 state 說不同的話。
    """
    d = tmp_path / "mh"
    part = _partition(d, "2026-07.json.gz", [_ROW])
    (d / mh.MANIFEST_NAME).write_text(
        json.dumps({"schema_version": mh.HISTORY_SCHEMA_VERSION,
                    "partitions": {}}), encoding="utf-8")
    assert mh._manifest_state(d) == (True, {}), "「有但空的」與「沒有」分不開"

    # 分區被改過,而 manifest 沒有它的紀錄、本次也沒有重寫它
    _partition(d, "2026-07.json.gz", [dict(_ROW, taiex_close=99999.0)])
    m = mh.write_partition_manifest(d, rewritten=set())
    assert "2026-07.json.gz" not in m["partitions"], (
        "沒登錄的既有分區被憑現況簽名了", m)
    # 留白之後,verify 要**繼續**抓得到它(不是靜靜放行)
    kinds = {i["kind"] for i in mh.verify_history_integrity(d)["issues"]}
    assert "extra_partition" in kinds, kinds

    # 但「本次刻意重寫」仍然要簽得下去(新月份走的正是這條)
    m2 = mh.write_partition_manifest(d, rewritten={"2026-07.json.gz"})
    assert "2026-07.json.gz" in m2["partitions"]
    assert part.exists()


def test_the_writer_side_also_refuses_to_bless_an_unrecorded_file(tmp_path,
                                                                  monkeypatch):
    """**同一個洞有兩個入口。**

    `morning_report` 合併前的竄改偵測讀的是同一份 `old`:
    `_rec = _old_manifest.get(name)` 拿不到就不設 `tampered`,分區於是進了
    `rewritten_names` —— writer 那邊擋住了,這邊卻自己把它送成「刻意重寫」。
    兩邊都要擋,否則修一半等於沒修。
    """
    import morning_report as mr
    d = tmp_path / "mh"
    _partition(d, "2026-07.json.gz", [dict(_ROW, taiex_close=99999.0)])
    (d / mh.MANIFEST_NAME).write_text(
        json.dumps({"schema_version": mh.HISTORY_SCHEMA_VERSION,
                    "partitions": {}}), encoding="utf-8")
    monkeypatch.setattr(mr, "MODEL_HISTORY_DIR", d)
    monkeypatch.setattr(mr, "MODEL_HISTORY_FILE", tmp_path / "legacy.json")

    mr.save_model_history_records([dict(_ROW, session_date="2026-07-02")])

    m = json.loads((d / mh.MANIFEST_NAME).read_text(encoding="utf-8"))
    assert "2026-07.json.gz" not in m["partitions"], (
        "沒登錄的既有分區被當成『本次刻意重寫』簽下去了", m)


def test_an_unparseable_omitted_partition_is_not_rebuilt_into_a_clean_one(
        tmp_path, monkeypatch):
    """**隔壁那條分支用的還是舊判準**(Codex deep 第二輪 P1)。

    分區既解析不動、manifest 又沒登錄它時,`_has_old_entry` 正好是 False
    —— 於是殘缺的記憶體重建版會被寫回去、進 `rewritten_names`、拿到一個
    嶄新而且相符的 checksum。**丟失的歷史列就藏在一個合法的簽名後面。**
    我上一輪修好了「解析得動」那條,沒看隔壁。
    """
    import morning_report as mr
    d = tmp_path / "mh"
    d.mkdir(parents=True)
    (d / "2026-07.json.gz").write_bytes(b"not a gzip file at all")
    (d / mh.MANIFEST_NAME).write_text(
        json.dumps({"schema_version": mh.HISTORY_SCHEMA_VERSION,
                    "partitions": {}}), encoding="utf-8")
    monkeypatch.setattr(mr, "MODEL_HISTORY_DIR", d)
    monkeypatch.setattr(mr, "MODEL_HISTORY_FILE", tmp_path / "legacy.json")

    raw_before = (d / "2026-07.json.gz").read_bytes()
    mr._DEGRADED_STEPS.clear()
    mr.save_model_history_records([dict(_ROW, session_date="2026-07-02")])

    m = json.loads((d / mh.MANIFEST_NAME).read_text(encoding="utf-8"))
    assert "2026-07.json.gz" not in m["partitions"], (
        "殘缺重建版被簽成乾淨的了", m)
    # **擋住簽名只擋住「假裝乾淨」,沒擋住資料消失**(Codex deep 第三輪):
    # 那個檔案還可能救得回來,不可以被殘缺重建版覆蓋。
    assert (d / "2026-07.json.gz").read_bytes() == raw_before, (
        "壞檔被覆寫了 —— 還救得回來的歷史就這樣沒了")
    assert any("state:corrupt:model_history" in x for x in mr._DEGRADED_STEPS), (
        "跳過了卻沒有留痕 —— 那個月從此靜靜地不再更新", mr._DEGRADED_STEPS)


def test_a_nested_lossy_conversion_is_not_reported_as_lossless():
    """**Codex deep 第一輪 P2**:`.item()` 與 Enum 兩條先前寫
    `normalize_json(v, path)[0]` —— 只取樹、把內層的診斷扔掉。

    於是 `np.complex128(...).item()` 回一個 Python `complex`、再掉到
    `str()` 的那種情形,對外只報成「無損」:計數看不到 lossy、
    `run_quality` 也就不會報,而模型收到的已經是字串。
    """
    tree, hits = es.normalize_json({"c": np.complex128(1 + 2j)})
    assert isinstance(tree["c"], str)        # 它確實變成了字串
    assert hits == [(es.NORM_LOSSY, "c(complex)")], hits
    assert es.summarize_normalization(hits).get("lossy") == 1
    # 一路接到判準:這一筆要真的變成 defect
    codes = {f["code"] for f in rq.assess(
        {"date": "2026-09-03 07:14", "manifest_schema": 2,
         "llm": {"evidence_normalized": es.summarize_normalization(hits)}})}
    assert "evidence_value_stringified" in codes
    # 正常的 numpy 不可以被這條帶壞(仍然是無損)
    assert es.normalize_json({"i": np.int64(5)})[1] == [
        (es.NORM_LOSSLESS, "i(int64)")]


# ------------------------------------------------------------------ P2 numpy
def test_numpy_scalars_stay_numbers(tmp_path):
    """`isinstance(np.int64(5), int)` 是 **False** —— 它們先前全部掉到
    最後那行 `str(node)`:`5` 變 `"5"`、`True` 變 `"True"`。
    不會炸、測試全綠、型別已經改了(9/3 的 `date` 是同一族的另一種),
    而 numpy / pandas 是這個 repo 的硬依賴,不是理論情境。"""
    tree, hits = es.normalize_json({
        "i": np.int64(5), "f": np.float32(1.25), "b": np.bool_(True),
        "d": np.datetime64("2026-09-04"), "nan": np.float64("nan")})
    assert tree["i"] == 5 and isinstance(tree["i"], int)
    assert tree["f"] == 1.25 and isinstance(tree["f"], float)
    assert tree["b"] is True, "np.bool_ 變成了字串"
    assert tree["d"] == "2026-09-04"
    assert tree["nan"] is None
    assert es.normalize_json(tree) == (tree, []), "不冪等"
    json.dumps(tree)                        # 不靠 default 也送得出去
    # 原生型別不可以被這條路帶壞(bool 是 Integral 的子型別)
    assert es.normalize_json({"a": 1, "b": 1.5, "c": True, "d": None})[1] == []
    assert dict(hits)      # 每一筆都要留痕


# ------------------------------------------------------------------ P2 set
def test_set_ordering_is_a_total_order():
    """`{1, "1"}` 兩個元素的 `str()` 完全相同 —— 穩定排序會保留 **set 的
    迭代順序**,而那個順序跨 process 不保證一致(字串雜湊有隨機種子)。
    同一份證據因此可能有兩種表示,而它會進 prompt、進指紋。"""
    for a, b in (({1, "1"}, {"1", 1}),
                 ({True, "True"}, {"True", True}),
                 ({decimal.Decimal("1"), "1"}, {"1", decimal.Decimal("1")})):
        assert es.normalize_json({"s": a})[0] == es.normalize_json({"s": b})[0]
    # 排序判準要與鍵那邊同一支(型別名 + 字串形式)
    assert es.normalize_json({"s": {"b", "a"}})[0] == {"s": ["a", "b"]}


def test_set_ordering_is_stable_across_processes():
    """**同一個 process 裡量不到這條。**

    `{1, "1"}` 與 `{"1", 1}` 在同一次執行中迭代順序相同 —— 真正的不確定性
    來自**字串雜湊的隨機種子**,只有跨 process 才看得見。實測:退回
    `key=str` 時,六個種子會產生**兩種**輸出;用全序則六個都一樣。
    (反例要能分勝負,不是「看起來有測到」。)
    """
    import os
    import subprocess
    code = (
        "import json,sys;sys.path.insert(0,%r);"
        "import evidence_serialize as es;"
        "print(json.dumps(es.normalize_json({'s':{1,'1'}})[0]))" % str(_ROOT))
    seen = set()
    for seed in ("0", "1", "2", "3", "4", "5"):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        out = subprocess.run([sys.executable, "-c", code], cwd=str(_ROOT),
                             env=env, capture_output=True, encoding="utf-8",
                             timeout=120)
        assert out.returncode == 0, out.stderr[-400:]
        seen.add(out.stdout.strip())
    assert len(seen) == 1, ("跨 process 不穩定 —— 同一份證據兩種表示", seen)


# ------------------------------------------------------------------ P2 gate
def test_normalization_severity_is_not_one_bucket():
    """`date → ISO` 與 `未知物件 → str()` 不是同一件事。混在同一個清單
    再取前 8 個,第 9 個才嚴重的那一筆根本不會被記下來。"""
    class _X:
        pass

    hits = [(es.NORM_LOSSLESS, f"a{i}(date)") for i in range(12)]
    hits.append((es.NORM_LOSSY, "z(_X)"))
    got = es.summarize_normalization(hits)
    assert got["lossless"] == 12 and got["lossy"] == 1
    assert got["samples"]["lossy"] == ["z(_X)"], (
        "嚴重的那一類被無損那類擠掉了")
    _, real = es.normalize_json({"x": _X()})
    assert real and real[0][0] == es.NORM_LOSSY


def test_the_quality_gate_consumes_the_normalization_report():
    """**跑完了 ≠ 送進去的東西沒被改過。** 有損轉型不會炸,先前也沒有
    任何判準消費它 —— 於是 strict 是綠的,而證據語意已經漂了。"""
    def _codes(norm):
        m = {"date": "2026-09-03 07:14", "manifest_schema": 2,
             "llm": {"evidence_normalized": norm}}
        return {f["code"]: f["severity"] for f in rq.assess(m)}

    assert "evidence_value_stringified" not in _codes({"lossless": 30})
    lossy = _codes({"lossy": 2, "samples": {"lossy": ["market.x(X)"]}})
    assert lossy.get("evidence_value_stringified") == "defect"
    assert _codes({"collision": 1}).get("evidence_key_collision") == "defect"
    assert _codes({"dropped": 3}).get("evidence_value_dropped") == "degraded"
    # 這三個是**接線**壞了,不是「信的內容不夠好」—— 刻意不寄的日子也要驗
    for code in ("evidence_value_stringified", "evidence_key_collision",
                 "evidence_value_dropped"):
        assert rq.finding_domain(code) == rq.DOMAIN_CONTROL_PLANE, code


# --------------------------------------------- r19:fresh data + stale interpreter
def test_a_future_schema_is_not_judged_by_an_older_validator(tmp_path,
                                                             monkeypatch):
    """**排程 checkout 的 commit 與 state 的 commit 不是同一個。**

    前幾輪修好了「stale state」(改讀 origin/main 當下那份),卻造出
    **fresh data + stale interpreter**:bump `manifest_schema` 的那一天,
    看門狗會拿舊判準去解讀新欄位。`run_quality` 對這種情形只報一個
    `degraded`,然後照樣往下判 —— log 誠實地寫著「不認得」,自動化卻繼續
    做決策(要不要補寄、要不要染紅、要不要寄品質信)。

    「不認得」既不是健康也不是降級,是**判不動**。
    """
    import sys as _sys
    _sys.path.insert(0, str(_ROOT / "tools"))
    import report_watchdog as w
    import run_manifest as rm

    today = __import__("datetime").datetime.now(w.TPE).strftime("%Y-%m-%d")
    fresh = tmp_path / "wd_manifest.json"

    def _put(schema):
        fresh.write_text(json.dumps({
            "date": f"{today} 05:20", "manifest_schema": schema,
            "github_run_id": "R",
            "delivery": {"attempted": True, "success": True,
                         "delivered_at": f"{today}T07:33:55+08:00",
                         "first_delivered_at": f"{today}T07:33:55+08:00",
                         "run_kind": "schedule"}}), encoding="utf-8")
        monkeypatch.setenv(w.FRESH_MANIFEST_ENV, str(fresh))
        monkeypatch.delenv(w.FRESH_RECEIPT_ENV, raising=False)
        monkeypatch.setattr(w, "quality_findings", lambda *a, **k: [])
        monkeypatch.setattr(w, "_default_get_json",
                            lambda: lambda url: {"status": "completed"})
        return w.main()

    assert _put(rm.MANIFEST_SCHEMA) == w.RC_OK          # 同版:照常判
    assert _put(rm.MANIFEST_SCHEMA + 1) == w.RC_VALIDATOR_TOO_OLD, (
        "舊判準對著新 schema 給了一個它證明不了的結論")
    # **不可以掉進補寄那一格**:不知道的時候多寄一封是收不回來的那一邊
    assert w.RC_VALIDATOR_TOO_OLD != w.RC_NOT_DELIVERED


def test_the_stale_validator_alert_can_actually_be_sent():
    """**我加的告警自己把自己弄成寄不出去的**(Codex r1 P1)。

    rc=6 設了 Subject,然後又掉進 `is_quality` 那條再設一次 ——
    而 `EmailMessage` 只允許一個 Subject,第二次會 raise
    `ValueError: There may be at most 1 Subject headers`。
    於是那封「判不動」的信**完全寄不出去**,而測試只驗了 workflow 條件裡
    有沒有 `rc == '6'`,從來沒有真的執行過那段寄信程式。
    """
    import sys as _sys
    _sys.path.insert(0, str(_ROOT / "tests"))
    from test_batch_watchdog_contract_0901 import _run_alert_script

    m = _run_alert_script(6)
    assert m is not None, "rc=6 沒有寄出任何信"
    assert "判不動" in m["Subject"], m["Subject"]
    assert m["To"] == "ops@example.com", "維運訊號寄給了一般收件人"
    body = m.get_content()
    assert "不自動補寄" in body and "收據" in body
    # 其他碼不可以被這次改動帶壞(同一條 if/elif 鏈)
    for rc, want in ((1, "沒有跑起來"), (2, "沒跑成"), (4, "只有降級")):
        other = _run_alert_script(rc)
        assert other is not None and want in other["Subject"], (rc, other)


def test_the_stale_validator_note_only_trusts_the_receipt(tmp_path,
                                                          monkeypatch):
    """判不動那條路的「信寄到了沒」**只能看收據**(Codex r1 P2)。

    先前用 `fresh_conclusion()`,而它讀不到收據時會**回退到 manifest** ——
    回頭解讀我上一行才剛宣稱判不動的那份檔,再把結論標成「收據說」。
    既解讀了不該解讀的東西,也說了一句假話。
    """
    import sys as _sys
    _sys.path.insert(0, str(_ROOT / "tools"))
    import report_watchdog as w
    now = __import__("datetime").datetime.now(w.TPE)
    today = now.strftime("%Y-%m-%d")
    fut = tmp_path / "wd_manifest.json"
    fut.write_text(json.dumps({
        "date": f"{today} 05:20", "manifest_schema": 99,
        "delivery": {"attempted": True, "success": True,
                     "run_kind": "schedule"}}), encoding="utf-8")
    monkeypatch.setenv(w.FRESH_MANIFEST_ENV, str(fut))
    monkeypatch.delenv(w.FRESH_RECEIPT_ENV, raising=False)
    note = w._receipt_note(now)
    assert "收據也讀不到" in note, ("拿 manifest 冒充收據", note)
    assert "已寄出" not in note

    rcpt = tmp_path / "wd_receipt.json"
    rcpt.write_text(json.dumps({
        "date": f"{today} 05:20",
        "delivery": {"attempted": True, "success": True,
                     "run_kind": "schedule"}}), encoding="utf-8")
    monkeypatch.setenv(w.FRESH_RECEIPT_ENV, str(rcpt))
    assert "已寄出" in w._receipt_note(now)


def test_the_stale_validator_state_alerts_and_is_visible():
    """判不動要有人知道(告警),而且要看得見(染紅);但**不補寄**。"""
    doc = yaml.safe_load(_WF_WD.read_text(encoding="utf-8"))
    steps = {s.get("name"): s for s in doc["jobs"]["check"]["steps"]}
    alert = " ".join(steps["Alert"]["if"].split())
    fail = " ".join(
        steps["Fail the run so it is visible in the Actions list"]["if"].split())
    rescue = " ".join(steps["Auto rescue"]["if"].split())
    assert "rc == '6'" in alert and "rc == '6'" in fail, (alert, fail)
    assert "rc == '6'" not in rescue, "判不動的時候還會自動補寄"
