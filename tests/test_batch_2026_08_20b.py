# -*- coding: utf-8 -*-
"""repo-wide 外審 2026-08-20(base 435e604→28bc34c)的五條驗收回歸。

P1-1 佐證 sentinel、P1-2 主體身分三套權威互打(生產已誤刪 UAE/台電)、
P2-1 抽取器逐批重撞失效端點。
"""
import sys

import requests

import morning_report as mr
import news_events as ne
import state_migrations as sm
import story_ledger as sl

sys.path.insert(0, "tests")

_KN = {"2330": ("台積電",)}


def test_aligned_corroboration_passes_the_validator():
    """外審反例 1:multi_source + caveat=「無」—— 完全照 schema 寫的模型
    輸出,降級後不得再被 validator 以「沒有 caveat」駁回;寫了矛盾內文
    (「兩家媒體已證實」)也一律換成確定性警語。"""
    import analysis_schema as sch
    pk = {"news": [{"source_item_id": "n1", "title": "x", "summary": "",
                    "importance": "normal"}],
          "news_clusters": {"clusters": [
              {"cluster_id": "c1", "member_source_ids": ["n1"],
               "corroboration": "single_source"}]},
          "market": {}}
    for cav in ("", "無", "無。", "N/A", "none", "兩家媒體已證實"):
        obj = {"top_news_analysis": [
            {"source_item_id": "n1",
             "corroboration_assessment": "multi_source",
             "source_caveat": cav}]}
        mr._align_corroboration(obj, pk)
        row = obj["top_news_analysis"][0]
        assert row["corroboration_assessment"] == "single_source", (cav, row)
        assert row["source_caveat"].startswith("僅單一來源"), (cav, row)
        probs = [p for p in sch.validate(obj, pk)
                 if "佐證" in p or "source_caveat" in p]
        assert not probs, (cav, probs)


def test_uae_and_taipower_legacy_rows_survive_migration():
    """外審反例 2(生產實證):同一班 migration 刪 `UAE|阿聯控伊朗…`、
    producer 卻對同一則判 `阿聯` literal 保留 —— 一邊刪一邊寫。
    重驗走 subject_identity 之後,跨語言 legacy 列升級保留、鍵不動。"""
    rows = [{"key": "e:uae|l:geopolitical|202608", "entity": "UAE",
             "subject_basis": "unverified",
             "headline": "阿聯控伊朗發射彈道飛彈", "timeline": []},
            {"key": "e:taipower|l:policy|202608", "entity": "Taipower",
             "subject_basis": "unverified",
             "headline": "10月電價拚續凍 台電盼立院同意撥補", "timeline": []},
            {"key": "e:usiranwar|l:geopolitical|202608",
             "entity": "US-Iran War", "subject_basis": "unverified",
             "headline": "美伊戰爭/60天談判期限到", "timeline": []}]
    keep, dropped = sm.purge_misattributed_stories(rows, _KN)
    kept = {r["entity"]: r["subject_basis"] for r in keep}
    assert kept == {"UAE": "alias", "Taipower": "alias"}, (kept, dropped)
    assert [r["entity"] for r in dropped] == ["US-Iran War"]


def test_legacy_russian_story_adopts_english_follow_up():
    """外審反例 3:昨天的 story entity=俄羅斯、今天英文續報 entity=Russia
    —— 必須接回同一條 story,不得開新條。"""
    vocab = {"2330": "台積電"}
    ev0 = {"entity": "俄羅斯", "entity_name": "", "event_type": "geopolitical",
           "direction": -1, "lifecycle": "confirmed", "confidence": 0.8,
           "title": "俄羅斯宣布新一輪動員", "source": "x", "source_grade": "B",
           "published": "2026-08-19T01:00:00+00:00"}
    led = sl.update_ledger([], [ev0], "2026-08-19", vocab)
    n0 = len(led)
    ev1 = dict(ev0, entity="Russia",
               title="Russia escalates mobilization, officials say",
               published="2026-08-20T01:00:00+00:00")
    led2 = sl.update_ledger(led, [ev1], "2026-08-20", vocab)
    assert len(led2) == n0, ("英文續報開了新 story",
                             [r.get("key") for r in led2])


def test_story_candidate_matching_bridges_languages():
    """直接量 `_match_open_story` 那道主體閘(孤立測試:上一條走的是
    timeline 鍵正規化,量不到這裡):同主體的跨語言寫法不得被原樣比對
    擋下 —— 昨天 entity=俄羅斯 的線索,今天 entity=Russia 的續報要配得到。"""
    story = {"key": "e:俄羅斯|l:geopolitical|202608", "entity": "俄羅斯",
             "entity_name": "", "headline": "俄羅斯宣布新一輪動員 情勢升級",
             "state": "developing", "timeline": [], "last_update": "2026-08-19"}
    ev = {"entity": "Russia", "entity_name": "",
          "title": "俄羅斯宣布新一輪動員 官員證實情勢升級",
          "event_type": "geopolitical",
          "published": "2026-08-20T01:00:00+00:00"}
    got = sl._match_open_story(ev, {story["key"]: story})
    assert got == story["key"], f"跨語言續報沒配回既有線索:{got!r}"


def test_timeline_lifecycle_continues_across_languages():
    """外審反例 4:歷史 lifecycle 鍵是 `…:俄羅斯:…`,英文續報不得裂成
    新 lifecycle、重拿 full weight —— 鍵先過 canonical 顯示名。"""
    base = {"event_type": "geopolitical", "title": "x",
            "published": "2026-08-20T00:00:00+00:00"}
    k_en = ne._event_timeline_key(dict(base, entity="Russia"))
    k_zh = ne._event_timeline_key(dict(base, entity="俄羅斯"))
    assert k_en == k_zh == ("俄羅斯", "geopolitical|2026-08"), (k_en, k_zh)
    k_uae = ne._event_timeline_key(dict(base, entity="UAE"))
    assert k_uae[0] == "阿聯", k_uae


def test_circuit_breaker_skips_dead_primary(monkeypatch):
    """外審反例 5(生產實證):三批各撞一次 DeepSeek 45.5s timeout 才換
    Gemini(~136s 純浪費+三筆未計量計費)。第一批傳輸失敗即開路,
    其餘批直接走備援;manifest 記開路點與略過次數。"""
    ds_calls, gm_calls = [], []

    def dead_deepseek(_p):
        ds_calls.append(1)
        raise requests.exceptions.ConnectionError("read timed out")

    monkeypatch.setattr(mr, "_call_deepseek_extractor", dead_deepseek)
    monkeypatch.setattr(mr, "_call_gemini",
                        lambda p, role="primary": gm_calls.append(1) or "[]")
    monkeypatch.setattr(mr, "_extractor_provider", lambda: "deepseek")
    monkeypatch.setattr(mr, "DEEPSEEK_API_KEY", "x")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "x")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("LLM_EVENT_EXTRACTION", "1")
    news = [{"title": f"台積電消息{i}", "summary": "", "source": "鉅亨台股",
             "published": "2026-08-20T06:00:00+08:00"} for i in range(35)]
    mr.call_llm_event_extractor(news, [])
    assert len(ds_calls) == 1, f"失效端點被撞了 {len(ds_calls)} 次(應 1 次)"
    assert len(gm_calls) == 3, f"備援呼叫 {len(gm_calls)} 次(應 3 批各 1)"
    stat = mr._RUN_MANIFEST.get("llm_extractor") or {}
    circ = stat.get("circuit") or {}
    assert circ.get("open_after_batch") == 0 and \
        circ.get("primary_attempts_skipped") == 2, circ


# ------------------------------------------------- 同批外審 r2:兩個 finding


def test_org_subjects_share_one_lineage_across_languages():
    """event_identity 的主體正規化走單一權威(r2 F1):Pentagon 與
    五角大廈必須收斂成同一個 lineage 主體,法域行為不變。"""
    import event_identity as ei
    assert ei.canonical_subject("Pentagon") == ei.canonical_subject("五角大廈") \
        == "五角大廈"
    assert ei.canonical_subject("Russia") == "俄羅斯"
    # 2026-08-22 外審 P1:**這一行原本釘的是相反的決策**(「公司鍵慣例是
    # 代號、不收斂」)。生產 state 裡的 `export_controls:輝達:2026-08`
    # 反證了那個前提 —— 公司中文名早就是持久身分,而不收斂讓
    # 輝達/NVIDIA/NVDA 成為三條 lifecycle。現在公司也走同一權威。
    assert ei.canonical_subject("2330") == ei.canonical_subject("TSMC") \
        == "台積電"


def test_current_schema_org_keys_are_renamed_without_losing_days():
    """生產現存的兩種形狀(3 段 Pentagon、4 段 ICC 對象段)要**改名不
    丟資料**:天數保留、row 主體同步正規化、可重入。"""
    tl = {"geopolitical:Pentagon:2026-08": {
              "entity": "Pentagon", "subjects": ["Pentagon"], "days": 3,
              "latest_title": "伊朗戰爭衝擊五角大廈"},
          "geopolitical:sanction:International Criminal C:2026-08": {
              "entity": "International Criminal Court",
              "subjects": ["International Criminal Court"], "days": 2,
              "latest_title": "ICC 制裁案"}}
    out, renamed, _ = sm.migrate_cross_language_timeline_keys(tl)
    assert len(renamed) == 2, renamed
    assert out["geopolitical:五角大廈:2026-08"]["days"] == 3
    assert out["geopolitical:五角大廈:2026-08"]["entity"] == "五角大廈"
    icc = [k for k in out if k.startswith("geopolitical:sanction:")]
    assert icc and "國際刑事法院" in icc[0], icc
    out2, renamed2, _ = sm.migrate_cross_language_timeline_keys(out)
    assert not renamed2, "改過名的鍵又被改一次(不可重入)"


def test_key_collision_uses_the_incident_policy_not_days():
    """r2 外審 P1:base key 刻意粗,撞鍵不能只用天數裁決 ——
    Pentagon 與 五角大廈 若是**不同樁**(辨識詞不重疊),兩條都要活
    (輸家掛 sibling);同一樁(MATCH)才併、留天數多者。"""
    distinct = {"geopolitical:五角大廈:2026-08": {
                    "entity": "五角大廈", "days": 5,
                    "incident_tokens": ["預算", "審查", "國會"],
                    "latest_title": "五角大廈預算審查"},
                "geopolitical:Pentagon:2026-08": {
                    "entity": "Pentagon", "days": 3,
                    "incident_tokens": ["中東", "駐軍", "重新評估"],
                    "latest_title": "Pentagon re-evaluates"}}
    out, _, _ = sm.migrate_cross_language_timeline_keys(distinct)
    assert len(out) == 2, f"另一樁被天數裁決滅掉:{sorted(out)}"
    assert any("#" in k for k in out), sorted(out)
    same = {"geopolitical:五角大廈:2026-08": {
                "entity": "五角大廈", "days": 5,
                "incident_tokens": ["中東", "駐軍", "重新評估"]},
            "geopolitical:Pentagon:2026-08": {
                "entity": "Pentagon", "days": 3,
                "incident_tokens": ["中東", "駐軍", "重新評估"]}}
    out2, _, _ = sm.migrate_cross_language_timeline_keys(same)
    assert len(out2) == 1
    assert out2["geopolitical:五角大廈:2026-08"]["days"] == 5
    # r3:**插入順序不得決定誰活** —— 英文列在前(先被改名放進 canonical
    # 鍵)、中文列在後(未改名、走早退分支)也要兩條都活。
    reversed_order = {"geopolitical:Pentagon:2026-08": {
                          "entity": "Pentagon", "days": 3,
                          "incident_tokens": ["中東", "駐軍", "重新評估"],
                          "latest_title": "Pentagon re-evaluates"},
                      "geopolitical:五角大廈:2026-08": {
                          "entity": "五角大廈", "days": 5,
                          "incident_tokens": ["預算", "審查", "國會"],
                          "latest_title": "五角大廈預算審查"}}
    out3, _, _ = sm.migrate_cross_language_timeline_keys(reversed_order)
    assert len(out3) == 2, f"反序時另一樁被滅:{sorted(out3)}"


def test_gemini_transport_failure_opens_the_circuit(monkeypatch):
    """r2 F2:主抽取器設 Gemini 時,傳輸失敗被 adapter 包成 RuntimeError,
    斷路器永遠不開 —— 各 adapter 要把傳輸失敗翻成中立的
    ExtractorTransportError,斷路器才接得到。"""
    posts, ds_calls = [], []

    def dead_post(url, json=None, timeout=None, headers=None):
        posts.append(1)
        raise requests.exceptions.ConnectionError("read timed out")

    monkeypatch.setattr(mr.requests, "post", dead_post)
    monkeypatch.setattr(mr, "_call_deepseek_extractor",
                        lambda p: ds_calls.append(1) or "[]")
    monkeypatch.setattr(mr, "_extractor_provider", lambda: "gemini")
    monkeypatch.setattr(mr, "GEMINI_API_KEY", "k")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "x")
    monkeypatch.setenv("LLM_EVENT_EXTRACTION", "1")
    monkeypatch.setattr(mr, "_llm_sleep", lambda s: None)
    news = [{"title": f"台積電消息{i}", "summary": "", "source": "鉅亨台股",
             "published": "2026-08-20T06:00:00+08:00"} for i in range(35)]
    mr.call_llm_event_extractor(news, [])
    stat = mr._RUN_MANIFEST.get("llm_extractor") or {}
    circ = stat.get("circuit") or {}
    assert circ.get("open_after_batch") == 0, (circ, len(posts))
    assert circ.get("primary_attempts_skipped") == 2, circ
    assert len(ds_calls) == 3, f"備援呼叫 {len(ds_calls)} 次(應 3 批各 1)"


def test_anthropic_transport_error_is_neutralized(monkeypatch):
    """Anthropic 的 SDK 連線例外也要翻成中立型別。"""
    import sys as _sys
    import types

    class _FakeConnErr(Exception):
        pass

    fake = types.SimpleNamespace(
        APIConnectionError=_FakeConnErr,
        Anthropic=lambda **k: types.SimpleNamespace(
            messages=types.SimpleNamespace(
                create=lambda **kw: (_ for _ in ()).throw(
                    _FakeConnErr("conn reset")))))
    monkeypatch.setitem(_sys.modules, "anthropic", fake)
    monkeypatch.setattr(mr, "ANTHROPIC_API_KEY", "k")
    try:
        mr._call_anthropic("p")
        raise AssertionError("沒有拋例外")
    except mr.ExtractorTransportError:
        pass
