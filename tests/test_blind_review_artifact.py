# -*- coding: utf-8 -*-
"""**盲評卡要真的被產生、真的是盲的、真的取得回來**(r4 #2、r5 #1/#2/#3)。

十配對達標後的判讀文字明文要求「人工 A/B 盲評」。這個要求歷經三次落空:

  1. (r4 #2)`blind_review_pair` **沒有任何生產呼叫端** —— 影子的文字算完
     指標就被丟掉,兩份文字再也湊不齊。帳本宣告「可以做判讀」的那一刻,
     判讀所需的東西已經不存在。
  2. (r5 #2)卡片產生了,但**解碼表和 A/B 內容在同一個 JSON 裡** ——
     評審點開檔案第一眼就看得到哪一邊是誰,「盲評」只剩名字。
  3. (r5 #1)卡片是盲的了,但 `sink` 只被寫進 manifest,**沒有任何分派或
     搬運行為** —— 卡片只存在於 runner 上、job 結束即消失,十天後一張都
     取不回,而 manifest 看起來像是有在交付。

三次都是同一個形狀:機制存在、看起來在運作、實際產不出東西。所以這個檔的
判準全部訂在**可觀察的結果**上(檔案在不在、身分露不露、job 結束後拿不拿
得到),不是「有沒有呼叫某個函式」。

## 通道是使用者的決定

卡片含兩份完整分析文字,而本 repo 是公開的 —— 公開 repo 的 Actions artifact
任何人拿到網址都能下載。所以預設 `local`(取不回,而那個事實會被記下來),
`artifact` 要使用者明確開啟;README 寫明外洩範圍讓那個選擇是知情的。
"""
import ast
import json
from pathlib import Path

import blind_review as br
import llm_experiment as lx
import morning_report as mr

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "morning_report.py"


def _calls(src: Path, name: str) -> set:
    """哪些名字**真的被呼叫**。用 AST 而不是子字串 —— 散文裡也會出現函式名。"""
    tree = ast.parse(src.read_text(encoding="utf-8"))
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == name)
    out = set()
    for sub in ast.walk(fn):
        if isinstance(sub, ast.Call):
            f = sub.func
            out.add(f.id if isinstance(f, ast.Name) else getattr(f, "attr", ""))
    return out


def _write(monkeypatch, tmp_path, *, sink="local"):
    monkeypatch.setattr(mr, "BLIND_REVIEW_DIR", tmp_path / "blind_review")
    monkeypatch.setattr(mr, "LLM_EXPERIMENT_ID", "luna-vs-deepseek")
    monkeypatch.setattr(mr, "LLM_BLIND_REVIEW_SINK", sink)
    mr._RUN_MANIFEST.pop("llm_experiment_review", None)


# ------------------------------------------------------------ 有沒有產出

def test_the_card_has_a_production_caller():
    """影子路徑必須真的去產卡 —— 這一段只有靜態關係可查。"""
    assert "_write_blind_review_card" in _calls(_SRC, "_run_llm_shadow"), (
        "影子路徑沒有產生盲評卡 —— 文字算完指標就丟了,"
        "十配對達標時人工盲評無法執行")


def test_the_production_path_reaches_the_pairing_itself(tmp_path, monkeypatch):
    """**生產路徑吐出來的,就是 `blind_review_pair` 算出來的那份。**

    落地流程用注入的方式接組裝器(`build=_br.card_files`),AST 追不過那個
    接縫 —— 而追不過就只能改用行為判準,不能改成「看起來有呼叫就算」。
    前一版把判準釘在「`_write_blind_review_card` 直接呼叫
    `blind_review_pair`」,組裝一搬家就指向不存在的形狀:
    **搬個家就失效的守衛守不住東西。**
    """
    _write(monkeypatch, tmp_path)
    mr._write_blind_review_card("主分析內容", "影子內容", "2026-08-05")
    got = json.loads((tmp_path / "blind_review" / "2026-08-05.json")
                     .read_text(encoding="utf-8"))
    want, _ = br.split_card(
        br.blind_review_pair("主分析內容", "影子內容", seed="2026-08-05"))
    # 經過 JSON 往返再比:`criteria` 在記憶體裡是 tuple,落地後是 list。
    # 比的是**內容**,不是型別。
    assert got == json.loads(json.dumps(want, ensure_ascii=False)), \
        "落地的卡片與配對本體算出來的不一致"


def test_the_card_is_written_while_both_texts_exist(tmp_path, monkeypatch):
    """行為驗證:兩份文字都在的時候,卡片與解碼表都要落地。

    刻意用 `artifact`:先前這條在預設的 `local` 下斷言 `is True`,
    而 `local` 是「寫得成功、拿不回來」—— 那句斷言等於把 r6 #1 的缺陷
    釘成通過條件。**測試寫下的期待也是一種宣稱,會出錯。**
    """
    _write(monkeypatch, tmp_path, sink="artifact")
    assert mr._write_blind_review_card(
        "主分析的完整文字", "影子的完整文字", "2026-08-05")["review_ok"] is True
    card = json.loads((tmp_path / "blind_review" / "2026-08-05.json")
                      .read_text(encoding="utf-8"))
    assert {card["A"], card["B"]} == {"主分析的完整文字", "影子的完整文字"}
    key = json.loads((tmp_path / "blind_review" / "2026-08-05.key.json")
                     .read_text(encoding="utf-8"))
    assert br.blind_review_is_decodable(key), \
        "沒有解碼表 —— 評完的分數對不回模型,整天的盲評作廢"


# ------------------------------------------------------------ r5 #2:是不是盲的

def test_the_reviewer_payload_hides_the_identities(tmp_path, monkeypatch):
    """**評審會打開的那份不得含解碼表**(r5 Codex,#2)。

    先前兩者同在一個 JSON;而當時的測試要求「同一檔案 decodable」,
    反而把「身分和內容擺在一起」固化成通過條件。
    """
    _write(monkeypatch, tmp_path)
    mr._write_blind_review_card("主分析", "影子", "2026-08-05")
    card = json.loads((tmp_path / "blind_review" / "2026-08-05.json")
                      .read_text(encoding="utf-8"))
    assert br.blind_review_is_blind(card), \
        f"評審看的那份帶了解碼表,盲評名存實亡:{sorted(card)}"
    assert "primary" not in json.dumps(card) and "shadow" not in json.dumps(card)


def test_the_key_lives_in_a_separate_file(tmp_path, monkeypatch):
    """解碼表要另存 —— 分開存才談得上分開授權。"""
    _write(monkeypatch, tmp_path)
    mr._write_blind_review_card("主分析", "影子", "2026-08-05")
    assert (tmp_path / "blind_review" / "2026-08-05.key.json").exists()


# ------------------------------------------------------------ r5 #1:取不取得回來

def test_the_default_sink_is_recorded_as_unretrievable(tmp_path, monkeypatch):
    """**`local` 取不回來,那是事實,不是實作細節。**

    先前 sink 只被寫進 manifest,沒有任何 consumer:十天後一張卡都取不回,
    而 manifest 看起來像是有在交付。現在那個「拿不到」要被記成降級。
    """
    _write(monkeypatch, tmp_path, sink="local")
    saved = list(mr._DEGRADED_STEPS)
    try:
        mr._DEGRADED_STEPS.clear()
        mr._write_blind_review_card("主分析", "影子", "2026-08-05")
        rec = mr._RUN_MANIFEST.get("llm_experiment_review") or {}
        assert rec.get("retrievable_after_job") is False
        assert any(s.startswith("blind_review:not_retrievable")
                   for s in mr._DEGRADED_STEPS), mr._DEGRADED_STEPS
    finally:
        mr._DEGRADED_STEPS[:] = saved


def test_an_implemented_sink_is_recorded_as_retrievable(tmp_path, monkeypatch):
    """反向:真的有 consumer 的 sink 不該被記成取不回。"""
    _write(monkeypatch, tmp_path, sink="artifact")
    mr._write_blind_review_card("主分析", "影子", "2026-08-05")
    rec = mr._RUN_MANIFEST.get("llm_experiment_review") or {}
    assert rec.get("retrievable_after_job") is True


def test_every_retrievable_sink_has_a_workflow_consumer():
    """**宣稱取得回來的 sink,workflow 裡要有真的把它搬走的步驟。**

    這是 r5 #1 的核心:`sinks` 表自己說了不算 —— 表裡填 True 而 workflow
    沒有對應步驟,就又回到「機制存在但取不回」。
    """
    wf = (_ROOT / ".github" / "workflows" / "morning-report.yml").read_text(
        encoding="utf-8")
    for sink, retrievable in br.SINKS.items():
        if not retrievable:
            continue
        assert f"vars.LLM_BLIND_REVIEW_SINK == '{sink}'" in wf, \
            f"sink={sink} 宣稱取得回來,workflow 卻沒有對應的 consumer 步驟"
        assert "upload-artifact" in wf and "artifacts/blind_review" in wf


def test_an_unknown_sink_degrades_explicitly(tmp_path, monkeypatch):
    """認不得的 sink 要**明確降級**,不能當成 metadata 靜靜放過。"""
    _write(monkeypatch, tmp_path, sink="dropbox")
    saved = list(mr._DEGRADED_STEPS)
    try:
        mr._DEGRADED_STEPS.clear()
        rec = mr._write_blind_review_card("主分析", "影子", "2026-08-05")
        assert rec["review_ok"] is False
        assert rec.get("ok") is False and "dropbox" in str(rec.get("error"))
        assert "blind_review:write_failed" in mr._DEGRADED_STEPS
    finally:
        mr._DEGRADED_STEPS[:] = saved


def test_the_readme_states_the_exposure():
    """`artifact` 在公開 repo 上等同公開 —— 使用者的選擇要是**知情**的。"""
    readme = (_ROOT / "README.md").read_text(encoding="utf-8")
    row = [ln for ln in readme.splitlines() if "LLM_BLIND_REVIEW_SINK" in ln]
    assert row, "README 沒有記載這個開關"
    assert "公開" in row[0] and "取不回" in row[0], \
        f"README 沒有寫明外洩範圍與 local 取不回:{row[0]}"


# ------------------------------------------------------------ r5 #3:失敗留不留痕

def test_a_write_failure_leaves_a_structured_trace(tmp_path, monkeypatch):
    """**寫失敗要留結構化痕跡**(r5 Codex,#3)。

    先前只印一行 stderr 就回去繼續寫帳本:那天的配對照樣入帳、卡片卻永遠
    缺席,manifest 與降級清單都看不出來。沒有痕跡的失敗等於沒發生過。
    """
    _write(monkeypatch, tmp_path)
    monkeypatch.setattr(mr, "_atomic_write_text",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("磁碟滿")))
    saved = list(mr._DEGRADED_STEPS)
    try:
        mr._DEGRADED_STEPS.clear()
        assert mr._write_blind_review_card(
            "主分析", "影子", "2026-08-05")["review_ok"] is False
        rec = mr._RUN_MANIFEST.get("llm_experiment_review") or {}
        assert rec.get("ok") is False, f"失敗沒有進 manifest:{rec}"
        assert "磁碟滿" in str(rec.get("error"))
        assert "blind_review:write_failed" in mr._DEGRADED_STEPS
    finally:
        mr._DEGRADED_STEPS[:] = saved


def test_a_pair_without_material_is_visible_in_the_verdict():
    """**判讀不得要求一件做不到的事。**

    達標的文字明說「仍需人工盲評」;若那幾天的卡片缺席或取不回,
    這句話就是空話 —— 進度要數得出來,判讀要說得出口。
    """
    def _row(day, review_ok):
        return lx.build_record(
            today=day, experiment_id="e",
            primary={"profile": "luna", "ok": True},
            shadow={"profile": "deepseek_legacy", "ok": True},
            evidence_sha_primary="a", evidence_sha_shadow="a",
            core_sha_primary="c", core_sha_shadow="c",
            review={"review_ok": review_ok, "review_expires": "2099-01-01"})

    ledger = [_row(f"2026-08-{d:02d}", d <= 3) for d in range(1, 11)]
    prog = lx.pair_progress(ledger, target=10, as_of="")
    assert prog["comparable_pairs"] == 10 and prog["ready"] is True
    assert prog["pairs_with_review"] == 3
    text = lx.verdict(prog)
    assert "3/10" in text, f"判讀沒有說出盲評材料的缺口:{text}"
    assert "無法補做" in text


def test_a_full_house_reads_cleanly():
    """反向:材料齊全時不該掛一句多餘的警告。"""
    def _row(day):
        return lx.build_record(
            today=day, experiment_id="e",
            primary={"profile": "luna", "ok": True},
            shadow={"profile": "deepseek_legacy", "ok": True},
            evidence_sha_primary="a", evidence_sha_shadow="a",
            core_sha_primary="c", core_sha_shadow="c",
            review={"review_ok": True, "review_expires": "2099-01-01"})

    prog = lx.pair_progress([_row(f"2026-08-{d:02d}") for d in range(1, 11)],
                            target=10, as_of="")
    assert "無法補做" not in lx.verdict(prog)


# ------------------------------------------------------------ 隱私線

def test_the_card_never_lands_in_state():
    """**state 會被 commit 進公開 repo,卡片不得落在那裡。**

    判準訂在**原始碼的宣告**上,不是執行期的值 —— 第一版寫
    `mr.STATE_ROOT not in mr.BLIND_REVIEW_DIR.parents`,而 conftest 的
    autouse fixture 會把 `*_DIR` 這類路徑改指到 tmp、`STATE_ROOT` 卻留在
    `state`:比對於是變成「tmp 路徑不在 state 底下」—— 恆真。
    把宣告改成由 `STATE_ROOT` 衍生之後測試照樣綠(突變當場抓到)。

    **被測試框架改寫過的值,不能拿來當隱私守衛的判準。**
    """
    tree = ast.parse(_SRC.read_text(encoding="utf-8"))
    decl = [n for n in tree.body
            if isinstance(n, ast.Assign)
            and any(getattr(t, "id", "") == "BLIND_REVIEW_DIR" for t in n.targets)]
    assert len(decl) == 1, f"BLIND_REVIEW_DIR 的宣告有 {len(decl)} 處"
    names = {n.id for n in ast.walk(decl[0].value) if isinstance(n, ast.Name)}
    assert "STATE_ROOT" not in names, (
        "盲評卡的目錄由 STATE_ROOT 衍生 —— 兩份完整分析文字會被 "
        "commit 進公開 repo")
    assert not any("blind_review" in str(p) for p in mr._state_push_paths()), \
        "盲評卡被登錄進 state push"


def test_the_card_directory_is_gitignored():
    """就算不在 state 底下,也不能被 `git add -A` 掃進去。

    本 repo 有過先例:沒被登錄的檔案照樣被 `git add -A` 提交(批#71 r1)。
    """
    ignored = (_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "artifacts/" in ignored, \
        "artifacts/ 沒有被 gitignore —— 盲評卡可能被提交進公開 repo"


def test_the_default_sink_does_not_publish():
    """預設通道必須是不外送的那個。通道是使用者的決定,不是預設值該替他做的。"""
    import llm_config
    assert llm_config.CONFIG_SOURCE_SPEC["LLM_BLIND_REVIEW_SINK"][1] == "local"
    assert br.SINKS["local"] is False


def test_the_manifest_records_existence_not_text(tmp_path, monkeypatch):
    """manifest 會進公開 state —— 只能記存在性,不能記文字,也不能記解碼表。"""
    _write(monkeypatch, tmp_path)
    mr._write_blind_review_card("主分析的機密文字", "影子的機密文字",
                                "2026-08-05")
    rec = mr._RUN_MANIFEST.get("llm_experiment_review") or {}
    assert rec.get("ok") is True
    blob = json.dumps(rec, ensure_ascii=False)
    assert "機密文字" not in blob, f"manifest 記了分析文字:{rec}"
    assert "_key" not in blob, f"manifest 記了解碼表:{rec}"


def test_no_card_without_an_experiment(tmp_path, monkeypatch):
    """沒在跑實驗就不該產生卡片 —— 它只為配對判讀而存在。"""
    _write(monkeypatch, tmp_path)
    monkeypatch.setattr(mr, "LLM_EXPERIMENT_ID", "")
    assert not mr._write_blind_review_card("主分析", "影子", "2026-08-05")
    assert not (tmp_path / "blind_review").exists()


def test_a_missing_side_produces_no_card(tmp_path, monkeypatch):
    """單邊的卡片不是盲評,是誤導。"""
    _write(monkeypatch, tmp_path)
    assert not mr._write_blind_review_card("只有主分析", "", "2026-08-05")
    assert not (tmp_path / "blind_review" / "2026-08-05.json").exists()


def test_card_failure_does_not_break_the_report(tmp_path, monkeypatch):
    """觀測用的東西壞掉不得弄壞晨報 —— 這是本 repo 的第一原則。"""
    _write(monkeypatch, tmp_path)
    monkeypatch.setattr(mr, "_atomic_write_text",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("磁碟滿")))
    saved = list(mr._DEGRADED_STEPS)
    try:
        mr._write_blind_review_card("主分析", "影子", "2026-08-05")   # 不得拋
    finally:
        mr._DEGRADED_STEPS[:] = saved


# ------------------------------------------------------------ r6:交付得成嗎

def test_a_written_but_unretrievable_card_is_not_material(tmp_path, monkeypatch):
    """**寫成功 ≠ 有材料**(r6 Codex,#1)。

    `local` 明明 job 結束就消失,先前卻因為「寫成功」而回 `review_ok=True`
    —— 每一天都被算進 `pairs_with_review`。帳本可以顯示 10/10 有材料、
    警語不出現,而實際一張卡都不存在:r5 修好的東西被自己的回傳值繞過去。
    """
    _write(monkeypatch, tmp_path, sink="local")
    saved = list(mr._DEGRADED_STEPS)
    try:
        entry = mr._write_blind_review_card("主分析", "影子", "2026-08-05")
        assert entry["ok"] is True, "檔案本身應該有寫成功"
        assert entry["review_ok"] is False, (
            "拿不回來的卡片被算成有材料 —— 進度會顯示 10/10 而實際一張都沒有")
        assert entry["review_expires"] == "", "拿不回來就沒有到期日可言"
    finally:
        mr._DEGRADED_STEPS[:] = saved


def test_a_retrievable_card_carries_an_expiry(tmp_path, monkeypatch):
    """能取回的卡片要記到期日 —— 沒有它,過期與否事後判不出來。"""
    _write(monkeypatch, tmp_path, sink="artifact")
    entry = mr._write_blind_review_card("主分析", "影子", "2026-08-05")
    assert entry["review_ok"] is True
    assert entry["review_expires"] == br.card_expiry("2026-08-05",
                                                     br.RETENTION_DAYS)


def test_expired_material_stops_counting():
    """**過期的材料不算材料**(r6 Codex,#3)。

    十配對的分母是成功配對數,失敗與跳過不推進它 —— 累積期因此可以遠長於
    保留期。而 `review_ok` 是寫下當天的事實,不會自己失效。
    """
    def _row(day, expires):
        return lx.build_record(
            today=day, experiment_id="e",
            primary={"profile": "luna", "ok": True},
            shadow={"profile": "deepseek_legacy", "ok": True},
            evidence_sha_primary="a", evidence_sha_shadow="a",
            core_sha_primary="c", core_sha_shadow="c",
            review={"review_ok": True, "review_expires": expires})

    ledger = ([_row(f"2026-01-{d:02d}", "2026-04-01") for d in range(1, 5)]
              + [_row(f"2026-08-{d:02d}", "2026-11-01") for d in range(1, 7)])
    prog = lx.pair_progress(ledger, target=10, as_of="2026-08-10")
    assert prog["comparable_pairs"] == 10, "可比配對本身不該因為過期而減少"
    assert prog["pairs_with_review"] == 6, "過期的卡片被算成還在"
    assert prog["pairs_review_expired"] == 4
    assert "6/10" in lx.verdict(prog) and "過期" in lx.verdict(prog)


def test_rows_without_an_expiry_are_not_presumed_dead():
    """schema 演進前的舊列沒有到期日 —— 保守地當作還在,不憑空判它死。"""
    row = lx.build_record(
        today="2026-08-05", experiment_id="e",
        primary={"profile": "luna", "ok": True},
        shadow={"profile": "deepseek_legacy", "ok": True},
        evidence_sha_primary="a", evidence_sha_shadow="a",
        core_sha_primary="c", core_sha_shadow="c",
        review={"review_ok": True})
    prog = lx.pair_progress([row], target=1, as_of="2030-01-01")
    assert prog["pairs_with_review"] == 1


def test_the_key_ships_in_its_own_artifact():
    """**解碼表不得和卡片裝在同一包**(r6 Codex,#2)。

    先前整個目錄一起上傳:評審下載後 `.key.json` 就躺在旁邊,
    「分開存檔」被運送方式抵銷。分開存卻一起送,等於沒有分開。
    """
    wf = (_ROOT / ".github" / "workflows" / "morning-report.yml").read_text(
        encoding="utf-8")
    assert "blind-review-cards-" in wf and "blind-review-keys-" in wf,         "卡片與解碼表沒有分成兩個 artifact"
    assert "!artifacts/blind_review/*.key.json" in wf,         "卡片那一包沒有把解碼表排除掉 —— 評審下載後身分就在旁邊"


def test_the_workflow_retention_matches_the_code():
    """**兩邊的保留天數不准分岔。**

    程式用 `RETENTION_DAYS` 算到期日,workflow 用 `retention-days` 真的保留。
    分岔的症狀是帳本說還在、artifact 早就沒了 —— 而那正是 r6 #3。
    """
    wf = (_ROOT / ".github" / "workflows" / "morning-report.yml").read_text(
        encoding="utf-8")
    import re
    got = {int(m) for m in re.findall(r"retention-days:\s*(\d+)", wf)}
    assert got == {br.RETENTION_DAYS}, (
        f"workflow 的保留天數 {sorted(got)} 與 blind_review.RETENTION_DAYS "
        f"{br.RETENTION_DAYS} 不一致")


def test_the_separation_is_documented_as_procedural():
    """公開 repo 上兩包的權限一樣 —— **不得把流程分離講成存取控制**。"""
    wf = (_ROOT / ".github" / "workflows" / "morning-report.yml").read_text(
        encoding="utf-8")
    assert "不是存取控制" in wf,         "workflow 沒有講明這只是流程分離 —— 公開 repo 上兩包誰都下載得到"


# ------------------------------------------------------------ r7:接上了沒有

def _pair(day, expires):
    return lx.build_record(
        today=day, experiment_id="e",
        primary={"profile": "luna", "ok": True},
        shadow={"profile": "deepseek_legacy", "ok": True},
        evidence_sha_primary="a", evidence_sha_shadow="a",
        core_sha_primary="c", core_sha_shadow="c",
        review={"review_ok": True, "review_expires": expires})


def test_expiry_is_applied_through_the_production_entry(tmp_path):
    """**經由 `record_day` 也要判到期**(r7 Codex)。

    到期判定寫好了,接線時卻沒把 `as_of` 傳下去 —— 而它預設空字串,
    於是在生產路徑上從第一天起就是個 no-op:`pairs_review_expired` 恆為零、
    過期的 artifact 照樣被算成有材料。

    先前的到期測試直接呼叫 `pair_progress(..., as_of=...)` 並自己傳值,
    **繞過了唯一的生產呼叫端** —— 那是本 repo 反覆栽的同一個地方:
    測試要用生產的呼叫形狀。
    """
    ledger = ([_pair(f"2026-01-{d:02d}", "2026-04-01") for d in range(1, 5)]
              + [_pair(f"2026-08-{d:02d}", "2026-11-01") for d in range(1, 6)])
    store = {"ledger": ledger}
    prog = lx.record_day(
        record=_pair("2026-08-06", "2026-11-01"), today="2026-08-06",
        ledger_path=tmp_path / "l.json",
        read_ledger=lambda p: store["ledger"],
        write_ledger=lambda p, v: store.update(ledger=v),
        target=10, log=lambda m: None)
    assert prog["comparable_pairs"] == 10, "可比配對不該因為過期而減少"
    assert prog["pairs_with_review"] == 6, (
        "經由 record_day 時過期沒有被判掉 —— as_of 沒有接上去")
    assert prog["pairs_review_expired"] == 4
    assert "過期" in prog["verdict"]


def test_the_expiry_switch_cannot_be_forgotten():
    """**`as_of` 必須是必填的。**

    這一條盯的是缺陷的形狀,不是缺陷本身:一個預設值等於「關閉」的選用
    參數,就是一個等著被忘記的開關 —— 而忘了傳不會有任何錯誤訊息,
    只會讓判定安靜地永遠通過。改成必填之後,忘記傳會當場拋。
    """
    import inspect
    sig = inspect.signature(lx.pair_progress)
    p = sig.parameters["as_of"]
    assert p.kind is inspect.Parameter.KEYWORD_ONLY, "as_of 要是關鍵字參數"
    assert p.default is inspect.Parameter.empty, (
        "as_of 有預設值 —— 忘了傳就會靜靜地不判到期,"
        "而那正是這個缺陷在生產路徑上活下來的方式")
