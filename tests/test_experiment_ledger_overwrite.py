# -*- coding: utf-8 -*-
"""**跳過的那一列不得蓋掉真正的失敗紀錄**(r4 Codex,#1)。

## 我做錯什麼

r3 收到的 finding 是「跳過也要留一列」—— 不留的話,可靠度只量得到跑得完的
那些天。我照做了,但守衛加得比成功分支寬:成功分支要求
`packet is not None and LLM_EXPERIMENT_ID`,跳過分支兩個都沒要求。

於是這條路徑會發生:

  1. Luna 主分析失敗 → 記下一列**真正的失敗**(shadow_ok=False, primary_ok=False)
  2. 落回 legacy DeepSeek → 成功
  3. legacy 呼叫端呼叫影子時**不傳 packet**;預算不夠 → 跳過分支
  4. `_experiment_row(None, primary_ok=True, …)` 捏出一列 Luna profile 的成功
  5. upsert 依 `(date, experiment_id)` 覆蓋 → **第 1 步那列失敗消失了**

失敗日從可靠度的分母裡消失,而可靠度正是判讀要用的東西。修 r3 的問題
反而造出一個更糟的:原本只是漏記,現在是**改寫已經記下的事實**。

## 這個檔盯什麼

`(date, experiment_id)` 是覆蓋鍵,所以任何「沒有 packet 也寫」的分支都會
變成一支覆蓋槍。判準訂在行為上(記下的那列還在不在),不是「有沒有加 if」。
"""
import datetime as dt

import experiment_ledger as xl
import llm_experiment as lx
import morning_report as mr


def _row(date, *, primary_ok, shadow_ok, reason="", run=None):
    return lx.build_record(
        run=run,
        today=date, experiment_id="luna-vs-deepseek",
        primary={"profile": "luna", "ok": primary_ok},
        shadow={"profile": "deepseek_legacy", "ok": shadow_ok},
        evidence_sha_primary="abc", evidence_sha_shadow="abc",
        core_sha_primary="abc", core_sha_shadow="abc",
        failure_reason=reason)


def test_appending_a_second_attempt_does_not_replace_the_first():
    """**這條的前提在第十二輪 P1-4 被推翻了。**

    原本它確立的是「覆蓋鍵是 `(date, experiment_id)`」—— 那個行為本身就是
    缺陷:06:00 排程失敗、09:00 人工重跑成功,失敗那列會消失,
    `primary_ok_rate` 從 0.0 變成 1.0。

    現在的性質相反:**兩次嘗試各留一列。** 收斂成一天一筆是判讀時的事,
    原始紀錄一旦被蓋掉就補不回來。
    """
    led = xl.append([], _row("2026-08-05", primary_ok=False, shadow_ok=False,
                             reason="luna_failed:timeout",
                             run={"run_id": "1", "run_attempt": 1,
                                  "run_kind": xl.SCHEDULED}))
    led = xl.append(led, _row("2026-08-05", primary_ok=True, shadow_ok=True,
                              run={"run_id": "2", "run_attempt": 1,
                                   "run_kind": xl.MANUAL}))
    assert len(led) == 2, "第二次嘗試把第一次蓋掉了"
    assert any(r["primary_ok"] is False for r in led), "失敗那列不見了"


def test_rewriting_the_same_attempt_is_idempotent():
    """同一次嘗試重寫要覆寫 —— 否則 job 重試會把同一件事記兩遍。"""
    run = {"run_id": "1", "run_attempt": 1, "run_kind": xl.SCHEDULED}
    led = xl.append([], _row("2026-08-05", primary_ok=False, shadow_ok=False,
                             run=run))
    led = xl.append(led, _row("2026-08-05", primary_ok=True, shadow_ok=True,
                              run=run))
    assert len(led) == 1 and led[0]["primary_ok"] is True


def _stub_shadow(monkeypatch, *, budget_ok):
    """把影子路徑架起來,只留「有沒有 packet」這一個變數。"""
    monkeypatch.setattr(mr, "LLM_SHADOW_PROVIDER", "deepseek")
    monkeypatch.setattr(mr, "LLM_SHADOW_MODEL", "deepseek-v4-pro")
    monkeypatch.setattr(mr, "LLM_EXPERIMENT_ID", "luna-vs-deepseek")
    monkeypatch.setattr(mr, "_run_budget_ok", lambda *a, **k: budget_ok)
    written = []
    monkeypatch.setattr(mr, "_persist_experiment_record",
                        lambda rec, today: written.append(rec))
    return written


def test_a_packetless_skip_writes_nothing(monkeypatch):
    """**legacy 呼叫端不傳 packet 時,跳過分支不得寫帳。**

    它沒有證據 sha、沒有 profile 語意,寫出去的是一列捏造的 Luna 成功。
    """
    written = _stub_shadow(monkeypatch, budget_ok=False)
    mr._run_llm_shadow("prompt", "legacy 的分析文字",
                       dt.datetime(2026, 8, 5, 6, 0), packet=None)
    assert written == [], f"沒有 packet 卻寫了帳:{written}"


def test_the_real_failure_survives_the_legacy_fallback(monkeypatch):
    """**端到端**:Luna 失敗 → legacy 成功 → 預算不足跳過影子。

    審查說沒有測試走完這條路;而這正是覆蓋會發生的那條。
    帳本最後必須還是那列失敗。
    """
    ledger = xl.append([], _row("2026-08-05", primary_ok=False, shadow_ok=False,
                                reason="luna_failed:timeout",
                                run={"run_id": "1", "run_attempt": 1,
                                     "run_kind": xl.SCHEDULED}))

    written = _stub_shadow(monkeypatch, budget_ok=False)
    # legacy 落回路徑:主分析文字有了,但 packet 沒有跟著傳下來
    mr._run_llm_shadow("prompt", "legacy 的分析文字",
                       dt.datetime(2026, 8, 5, 6, 0), packet=None)
    for rec in written:
        ledger = xl.append(ledger, rec)

    assert len(ledger) == 1
    assert ledger[0]["primary_ok"] is False, (
        "真正的 Luna 失敗被一列捏造的成功蓋掉了 —— "
        "失敗日會從可靠度的分母消失")
    assert "luna_failed" in str(ledger[0].get("failure_reason") or "")


def test_a_skip_with_a_real_packet_still_records(monkeypatch):
    """反向:**別為了擋覆蓋而把 r3 修好的東西弄回去。**

    有 packet 的那一天,跳過仍然要留一列 —— 否則可靠度又只量得到
    跑得完的日子。
    """
    written = _stub_shadow(monkeypatch, budget_ok=False)
    packet = {"schema_version": 1, "evidence": [], "generated_for": "2026-08-05"}
    mr._run_llm_shadow("prompt", "主分析文字",
                       dt.datetime(2026, 8, 5, 6, 0), packet=packet,
                       primary_profile="luna", shadow_profile="deepseek_legacy")
    assert len(written) == 1, "有 packet 的跳過日沒有留下紀錄"
    assert written[0]["shadow_ok"] is False
    assert "run_budget" in str(written[0].get("failure_reason") or "")
