# -*- coding: utf-8 -*-
"""r15 外審:**同一個 truth 要被所有 consumer 使用。**

這一批的兩條 finding 是同一種形狀:

  * 看門狗的 rc / 告警 / 紅綠讀 checkout 的舊快照,只有**補寄**讀
    `origin/main` 當下的證據 —— 於是同一天,補寄正確地不補、告警卻宣告
    一場沒有發生的事故。
  * model_history 的**語意**契約只住在 `tests/`,正式 strict consumer
    (`load_model_history(strict=True)`)看不到 —— publish gate 比消費端嚴。

(後者的測試在 `tests/test_state_schema_contract.py`,與它驗的東西放一起。)
"""
import datetime as dt
import json
import sys
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "tools"))
import report_watchdog as w                                    # noqa: E402

_WF = _ROOT / ".github" / "workflows" / "report-watchdog-b.yml"


def _today() -> str:
    return dt.datetime.now(w.TPE).strftime("%Y-%m-%d")


def _yesterday() -> str:
    return (dt.datetime.now(w.TPE) - dt.timedelta(days=1)).strftime("%Y-%m-%d")


def _manifest(day: str, *, delivered=True, run_id="1") -> dict:
    """一份**寄成功**(或明確沒寄成)的 manifest。"""
    delivery = {"attempted": True, "success": True,
                "delivered_at": f"{day}T07:37:52+08:00",
                "first_delivered_at": f"{day}T07:37:52+08:00",
                "run_kind": "schedule"}
    if not delivered:
        delivery = {"attempted": True, "success": False}
    return {"date": f"{day} 05:20", "manifest_schema": 2,
            "github_run_id": run_id, "delivery": delivery}


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """把看門狗的三個證據來源都指向暫存目錄(不碰真實 state)。"""
    checkout = tmp_path / "run_manifest.json"
    monkeypatch.setattr(w, "MANIFEST", checkout)
    monkeypatch.setattr(w, "quality_findings", lambda *a, **k: [])
    monkeypatch.delenv(w.FRESH_RECEIPT_ENV, raising=False)
    monkeypatch.delenv(w.FRESH_MANIFEST_ENV, raising=False)
    # 預設:今天沒有晨報 run 在跑(要碰網路的那一支一律注入,測試不連外)
    monkeypatch.setattr(
        w, "_default_get_json",
        lambda: lambda url: {"status": "completed", "workflow_runs": []})

    def _put(which: str, payload: dict) -> None:
        if which == "checkout":
            checkout.write_text(json.dumps(payload), encoding="utf-8")
            return
        env = (w.FRESH_RECEIPT_ENV if which == "receipt"
               else w.FRESH_MANIFEST_ENV)
        path = tmp_path / f"wd_{which}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        monkeypatch.setenv(env, str(path))
    return _put


def test_a_stale_checkout_alone_still_reads_as_not_delivered(wired):
    """對照組:沒有新鮮證據時,昨天的 manifest 本來就該判 rc=1。

    沒有這一條,下面幾個測試證明不了任何事 —— 它們可能只是因為
    「這個 fixture 本來就不會紅」而通過。
    """
    wired("checkout", _manifest(_yesterday()))
    assert w.main() == w.RC_NOT_DELIVERED


def test_the_verdict_uses_origin_main_not_the_checkout_snapshot(wired):
    """排程觸發的 checkout 是**排程事件建立當時**的快照。

    主班在那之後才寄成功(9/2 實況:主班被 GitHub 拖了 2 小時 07 分)時,
    看門狗手上的 manifest 當然是昨天的 —— 而 `origin/main` 上已經有今天的
    結論了。先前只有 `--rescue` 會去看它。
    """
    wired("checkout", _manifest(_yesterday()))
    wired("manifest", _manifest(_today()))
    assert w.main() == w.RC_OK


def test_a_fresh_receipt_alone_is_enough_to_stop_the_false_incident(wired):
    """**收據是獨立 push 的**(`publish_receipt_from_remote_base()`)。

    整批 state 沒推上去的日子(state 契約擋下、push 失敗),`origin/main`
    上只有收據會是今天的。那仍然是「信寄到了」的鐵證 ——
    rc=1 的意思是「今天沒有信」,而信明明在收件匣裡。
    """
    wired("checkout", _manifest(_yesterday()))
    wired("receipt", _manifest(_today()))
    rc = w.main()
    assert rc != w.RC_NOT_DELIVERED, "又宣告了一場沒有發生的事故"
    assert rc == w.RC_QUALITY_DEFECT, "state 沒跟上仍然是缺陷,不可以靜音"


def _runs(*statuses):
    """假的 Actions runs 清單(今天建立的)。"""
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"workflow_runs": [{"status": st, "created_at": stamp}
                              for st in statuses]}


def test_only_the_receipts_own_run_can_silence_the_state_gap(wired,
                                                             monkeypatch):
    """**問題是「寫下這份收據的那一班還在跑嗎」,不是「今天有沒有人在跑」。**

    晨報一天本來就有三個排程班(05:07 / 05:52 / 06:42)。收據那一班已經
    結束、state 真的沒落地,而補漏跑還在排隊 —— 用「今天有 run 在跑」當
    判準的話,整個告警就被那個無關的 run 靜音了。而補漏跑看到「今天已寄過」
    會空轉結束,它**不會**去補寫那份 manifest(Codex deep 第二輪)。
    """
    wired("checkout", _manifest(_yesterday()))
    wired("receipt", _manifest(_today(), run_id="A"))

    def _api(status_of_A, others=()):
        def _get(url):
            if "/actions/runs/A" in url:
                return {"status": status_of_A}
            return _runs(*others)
        return lambda: _get

    # 收據那一班自己還在跑 → 正常的中間狀態
    monkeypatch.setattr(w, "_default_get_json", _api("in_progress"))
    assert w.main() == w.RC_OK

    # 收據那一班已經結束,但**別的** run 在排隊 → 仍然是缺陷
    monkeypatch.setattr(w, "_default_get_json", _api("completed", ("queued",)))
    assert w.main() == w.RC_QUALITY_DEFECT, (
        "無關的補漏跑把一場真的 state 沒落地靜音了")

    # 查不出那一班的狀態 → 照樣說出來(少報一次沒有人會知道)
    monkeypatch.setattr(w, "_default_get_json", _api(""))
    assert w.main() == w.RC_QUALITY_DEFECT


def test_a_running_producer_is_not_a_state_defect(wired, monkeypatch):
    """**收據早於整批 state 是設計,不是故障。**

    `publish_receipt_from_remote_base()` 在 SMTP 成功的當下就獨立 push,
    而整批 state 要等這一班跑完剩下的工作、過 schema 契約、品質自評,才在
    最後一步 commit/push。所以「收據是今天的、manifest 還不是」是**正常的
    中間狀態** —— 那一班還在跑的時候把它判成缺陷,就是把剛修好的假事故
    換了一個名字再發一次(Codex deep + r16 外審同時指到這一條)。

    那一班已經結束才是缺陷 —— 用的是既有的那把尺:有人在跑就不插隊。
    """
    wired("checkout", _manifest(_yesterday()))
    wired("receipt", _manifest(_today()))
    monkeypatch.setattr(w, "_default_get_json",
                        lambda: lambda url: {"status": "in_progress"})
    assert w.main() == w.RC_OK, "那一班還在寫 state,這不是事故"


def test_an_intentional_skip_is_never_reported_as_delivered(wired,
                                                            monkeypatch,
                                                            capsys, tmp_path):
    """**收據也會為「刻意不寄」而寫**(週日無新內容那條路)。

    所以這個改判分支不可以一律說「信已經寄達」—— 那天本來就不該有信,
    而告警信的主旨會照著說「信寄出了,但有段落沒跑成」:一封宣告
    「有一封不存在的信品質不好」的信(Codex deep P3)。
    """
    skipped = _manifest(_today())
    skipped["delivery"] = {"attempted": False, "success": False,
                           "skipped_reason": "weekend_no_new_content"}
    wired("checkout", _manifest(_yesterday()))
    wired("receipt", skipped)
    out = tmp_path / "gha_out.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))

    assert w.main() == w.RC_QUALITY_DEFECT
    said = capsys.readouterr().err
    assert "刻意不寄" in said and "寄達" not in said, said
    # 主旨那一層也要分得出來 —— 判準知道、信卻說反話,等於沒分。
    assert "state_gap=skipped" in out.read_text(encoding="utf-8")


def test_a_skip_after_an_earlier_delivery_is_its_own_case(wired, monkeypatch,
                                                          tmp_path):
    """**收據記的是「這一班」的結論,不是「今天」的歷史。**

    手動觸發豁免同日冪等(`already_delivered_today`:「只擋排程」),
    而 `_day_first_delivery()` 會把稍早那一班的 `first_delivered_at`
    沿用下去。所以「早上寄過、後來一班手動跑而沒有新內容於是刻意不寄」
    是合法的一天 —— 對那天說「今天沒有寄過任何信」是假的
    (Codex deep 第三輪)。
    """
    skipped = _manifest(_today())
    skipped["delivery"] = {"attempted": False, "success": False,
                           "skipped_reason": "weekend_no_new_content",
                           "first_delivered_at": f"{_today()}T07:37:52+08:00"}
    wired("checkout", _manifest(_yesterday()))
    wired("receipt", skipped)
    out = tmp_path / "gha_out.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    assert w.main() == w.RC_QUALITY_DEFECT
    assert f"state_gap={w.GAP_SKIPPED_AFTER_DELIVERY}" in out.read_text(
        encoding="utf-8"), "把「今天稍早寄過」與「今天沒寄過」說成同一件事"


def test_a_state_gap_after_delivery_is_labelled_for_the_subject(wired,
                                                                monkeypatch,
                                                                tmp_path):
    """rc=2 的主旨預設說「信寄出了,但有段落沒跑成」—— 信其實好好的,
    掉的是 state。判準要把這件事傳到最醒目的那一層。"""
    wired("checkout", _manifest(_yesterday()))
    wired("receipt", _manifest(_today()))
    out = tmp_path / "gha_out.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    assert w.main() == w.RC_QUALITY_DEFECT
    assert "state_gap=delivered" in out.read_text(encoding="utf-8")


def test_yesterdays_fresh_evidence_never_overrides_today(wired):
    """新鮮證據**只有今天的才算**:否則昨天的收據會蓋掉今天的真事故。"""
    wired("checkout", _manifest(_today()))
    wired("manifest", _manifest(_yesterday(), delivered=False))
    wired("receipt", _manifest(_yesterday()))
    assert w.main() == w.RC_OK

    wired("checkout", _manifest(_today(), delivered=False))
    assert w.main() == w.RC_NOT_DELIVERED, "昨天的收據把今天的事故蓋掉了"


def test_the_alert_and_the_rescue_decision_cannot_disagree(wired):
    """**四件事要基於同一份事實**:rc、告警文字、紅綠、補寄。

    `rescue_decision()` 的第一條就是「`fresh_verdict` 非空就不補」。
    只要那個條件成立而 rc 仍然是 1,operator 收到的就是
    「今天的晨報可能沒有跑起來」+ 一個紅色的 job,而補寄那一步同時
    (正確地)判斷今天已經寄過了。
    """
    for case in ("receipt", "manifest"):
        wired("checkout", _manifest(_yesterday()))
        wired(case, _manifest(_today()))
        verdict = w.fresh_conclusion(dt.datetime.now(w.TPE))
        assert verdict, case
        go, why = w.rescue_decision(w.RC_NOT_DELIVERED, 0, active_runs=0,
                                    fresh_verdict=verdict)
        assert not go and why == verdict
        assert w.main() != w.RC_NOT_DELIVERED, (
            f"{case}:補寄說「今天已經寄了」,rc 卻說「今天沒跑起來」")


def test_the_ack_check_asks_about_the_authoritative_run(wired, monkeypatch):
    """降級去重要查**主班那一次**的品質告警 job。

    `_manifest_run_id()` 先前一律讀 checkout 的檔 —— 那上面是**昨天**那一班
    的 run id。拿它去問 `alert-on-quality` 的狀態,問的是錯的那次執行:
    昨天成功 → 今天沒寄的那封被判成「已經有人收到了」而靜音。
    """
    asked = []

    def _get(url):
        asked.append(url)
        return {"jobs": [{"name": "alert-on-quality", "status": "completed",
                          "conclusion": "success"}]}

    monkeypatch.setattr(w, "_default_get_json", lambda: _get)
    monkeypatch.setattr(w, "quality_findings", lambda *a, **k: [
        {"code": "recap_not_previous_session", "severity": "degraded",
         "detail": "前一天的後果", "domain": "content"}])
    wired("checkout", _manifest(_yesterday(), run_id="OLD"))
    wired("manifest", _manifest(_today(), run_id="NEW"))

    assert w.main() == w.RC_QUALITY_DEGRADED
    assert asked and all("OLD" not in u for u in asked), asked
    assert any("NEW" in u for u in asked), asked


def test_the_workflow_gives_the_verdict_step_the_same_evidence(monkeypatch):
    """**接上去了才算數**:Python 端會讀環境變數,但 workflow 要真的傳。

    先前 `Check last run` 的 env 只有 `GITHUB_TOKEN`,那兩份 fetch 出來的
    檔案只進 `Auto rescue` —— 判準拿不到,於是「補寄用新鮮證據、rc 用舊
    快照」在 YAML 這一層就分岔了。
    """
    doc = yaml.safe_load(_WF.read_text(encoding="utf-8"))
    steps = {s.get("name"): s for s in doc["jobs"]["check"]["steps"]}
    check, rescue = steps["Check last run"], steps["Auto rescue"]
    for env in (w.FRESH_RECEIPT_ENV, w.FRESH_MANIFEST_ENV):
        assert env in (check.get("env") or {}), f"判準拿不到 {env}"
        assert check["env"][env] == rescue["env"][env], (
            f"{env}:判準與補寄讀的不是同一個檔")

    # 那兩個檔名要真的是前一步 fetch 出來的那兩個。
    fetch = steps["讀取 origin/main 當下的寄送紀錄"]["run"]
    for env in (w.FRESH_RECEIPT_ENV, w.FRESH_MANIFEST_ENV):
        name = check["env"][env].rsplit("/", 1)[-1]
        assert name in fetch, f"{env} 指向的 {name} 根本沒有被 fetch 出來"


# ------------------------------------------------ r18:缺陷日的重複品質信
def _quality(monkeypatch, findings, ack_job):
    """跑 `_quality_exit`,注入 findings 與主班那封品質信的 job 狀態。"""
    monkeypatch.setattr(w, "quality_findings", lambda *a, **k: findings)
    monkeypatch.setattr(w, "_manifest_run_id", lambda *a, **k: "RUN")
    return w._quality_exit("2026-09-03 07:14", get_json=lambda url: ack_job,
                           sleep=lambda _s: None)


_DEFECT = [{"code": "luna_rejected", "severity": "defect",
            "detail": "特化輸出被驗證擋下", "domain": "content"}]
_DEGRADED = [{"code": "analysis_not_specialized", "severity": "degraded",
              "detail": "落 legacy", "domain": "content"}]
_SENT = {"jobs": [{"name": "alert-on-quality", "status": "completed",
                   "conclusion": "success"}]}
_FAILED = {"jobs": [{"name": "alert-on-quality", "status": "completed",
                     "conclusion": "failure"}]}


def test_a_defect_day_does_not_send_the_same_letter_twice(monkeypatch):
    """**9/3 自然重現**:那天有 `luna_rejected`(defect),產出者 07:42
    自評後寄了一封,看門狗 09:30 又寄了一封**內容完全相同**的。

    r11 立的原則是「去重要基於『收到了』,不是『應該收到了』」—— 但那套
    ACK 機制先前只接在「只有降級」那條路,缺陷這條在它之前就 return 了。
    """
    assert _quality(monkeypatch, _DEFECT, _SENT) == w.RC_QUALITY_DEFECT_ACKED


def test_a_defect_day_still_turns_the_run_red(monkeypatch):
    """**紅與信是兩件事。** rc=3 是「連紅都不必」,rc=5 是「紅要,信不必」
    —— 缺陷本來就該在 Actions 上看得見,那不因為信已經寄過而消失。"""
    text = _WF.read_text(encoding="utf-8")
    doc = yaml.safe_load(text)
    steps = {s.get("name"): s for s in doc["jobs"]["check"]["steps"]}
    fail = steps["Fail the run so it is visible in the Actions list"]["if"]
    alert = steps["Alert"]["if"]
    assert "'5'" in fail, "rc=5 沒有染紅 —— 缺陷在 Actions 上會變成綠色"
    assert "'5'" not in alert, "rc=5 仍然會寄第二封信"


def test_an_unconfirmed_alert_still_gets_a_second_letter(monkeypatch):
    """**只有「確認寄成」才省下那封信。** 沒送成、或查不出來,一律照寄 ——
    漏一封的代價是「判準說的話沒有人收到」,比重複一封糟。"""
    assert _quality(monkeypatch, _DEFECT, _FAILED) == w.RC_QUALITY_DEFECT
    # 查不出來(job 還沒出現、run 也還沒結束)→ 也要寄
    assert _quality(monkeypatch, _DEFECT, {"jobs": []}) == w.RC_QUALITY_DEFECT
    # 只有降級那條路不可以被這次改動帶壞
    assert _quality(monkeypatch, _DEGRADED, _SENT) == w.RC_QUALITY_DEGRADED
    assert _quality(monkeypatch, _DEGRADED, _FAILED) == (
        w.RC_QUALITY_DEGRADED_UNSENT)


def test_the_skip_path_never_dedupes_against_a_letter_nobody_sent(monkeypatch):
    """**刻意不寄的日子沒有產出者那封信可以去重。**

    產出者的品質自評條件是 `run_outcome == 'delivered'` —— 那天根本不跑。
    在那條路上套用 ACK 去重,就是把控制面的缺陷降成一行綠色的 job log
    (r10 第二輪對 rc=3 立過同一條)。
    """
    # **控制面**的缺陷才留得下來(內容類那天本來就該被濾掉:沒有信)
    import run_quality as rq
    monkeypatch.setattr(w, "quality_findings", lambda *a, **k: [
        {"code": "manifest_schema_invalid", "severity": "defect",
         "detail": "世代標記壞掉", "domain": rq.DOMAIN_CONTROL_PLANE}])
    monkeypatch.setattr(w, "_default_get_json", lambda: lambda url: _SENT)
    assert w._control_plane_exit("2026-09-03 07:14") == w.RC_QUALITY_DEFECT
