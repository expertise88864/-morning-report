# -*- coding: utf-8 -*-
"""**預期→結果的閉環**(縱深第四批 D,2026-08-10)。

schema 一直有觀察點的**寫入端**(`watch_triggers`)—— 但寫進信裡就被
遺忘,沒有任何東西隔天回頭問「觸發了沒」。使用者要的「原因/預期/
結果/後續影響」裡,「結果」正是缺的那一段。

閉環四段,判準逐段:存(recap 留 `watch`)→ 派號(`usable_watch`,
同日重跑閘門)→ 進 packet(`yesterday_watch`,消毒)→ 驗收
(`watch_review` 全覆蓋、已觸發要有今天的證據)→ 信裡渲染。
"""
from __future__ import annotations

import analysis_recap as rc
import evidence_packet as ep


def _analysis(**over):
    obj = {"watch_triggers": [
        {"trigger": "美光財報上修 HBM 出貨", "why": "驗證 AI 需求",
         "horizon": "1w", "claim_ids": ["c1"]},
        {"trigger": "台積電法說資本支出", "why": "擴產定調",
         "horizon": "1m", "claim_ids": []},
    ]}
    obj.update(over)
    return obj


# ---------------------------------------------------------------- 存

def test_extract_keeps_the_watch_list():
    """觀察點要進 recap —— 檔案只留最新一天,不存就沒有明天的回顧。"""
    rec = rc.extract(_analysis(), {"target_session_date": "2026-08-10"})
    assert [w["trigger"] for w in rec["watch"]] == [
        "美光財報上修 HBM 出貨", "台積電法說資本支出"]
    assert rec["watch"][0]["why"] == "驗證 AI 需求"


def test_watch_is_capped_and_junk_is_dropped():
    """上限與空 trigger:存十條等於逼明天寫十條回顧。"""
    many = [{"trigger": f"觀察{i}", "why": "", "horizon": "1d"}
            for i in range(9)] + [{"trigger": "", "why": "x"}, "垃圾"]
    rec = rc.extract(_analysis(watch_triggers=many), {})
    assert len(rec["watch"]) == rc.WATCH_MAX


def test_a_watch_only_day_is_still_saved(tmp_path):
    """觀點空、觀察點不空的日子仍要存 —— 回顧的閉環不能斷一天。"""
    out = rc.save(tmp_path / "r.json", _analysis(),
                  {"target_session_date": "2026-08-10", "news": [],
                   "news_clusters": {"clusters": []}})
    assert out == rc.SAVED
    assert rc.load(tmp_path / "r.json")["watch"]


# ---------------------------------------------------------------- 派號

def test_usable_watch_assigns_ids_and_gates_same_day_reruns():
    """代號由 Python 派;**同日重跑不得自比** —— 拿今天剛寫的觀察點
    回顧,每一條都會「已觸發」(它就是照今天的新聞寫的)。"""
    recap = {"date": "2026-08-09",
             "watch": [{"trigger": "A", "why": "", "horizon": "1d"},
                       {"trigger": "B", "why": "b", "horizon": "1w"}]}
    got = rc.usable_watch(recap, "2026-08-10")
    assert [(w["watch_id"], w["trigger"]) for w in got] == [
        ("w1", "A"), ("w2", "B")]
    assert got[0]["date"] == "2026-08-09"
    assert rc.usable_watch(recap, "2026-08-09") == []   # 同日重跑
    assert rc.usable_watch(recap, "") == []
    assert rc.usable_watch({"date": "2026-08-09"}, "2026-08-10") == []


# ---------------------------------------------------------------- 進 packet

def _packet(sanitize=lambda s, *a: s, date="2026-08-10"):
    return ep.build({"ANALYSIS_RECAP": {
        "date": "2026-08-09", "items": [],
        "watch": [{"trigger": "美光財報上修 HBM 出貨",
                   "why": "驗證 AI 需求", "horizon": "1w"}]}},
        {}, {}, [], [], {}, as_of=f"{date} 06:00",
        target_session_date=date, sanitize=sanitize)


def test_the_packet_declares_yesterdays_watch():
    pk = _packet()
    assert pk["yesterday_watch"][0]["watch_id"] == "w1"
    assert pk["yesterday_watch"][0]["trigger"] == "美光財報上修 HBM 出貨"


def test_the_watch_passes_through_the_sanitizer():
    """觀察點是**跨日回流的模型輸出**,與 story_arcs 同一條高風險路徑
    (存放式注入)—— 每一格都要是 `sanitize_tree` 掃得到的葉節點。"""
    pk = _packet(sanitize=lambda s, *a: f"S:{s}")
    w = pk["S:yesterday_watch"][0]
    assert w["S:trigger"].startswith("S:")
    assert w["S:why"].startswith("S:")


def test_no_recap_degrades_to_an_empty_list():
    """晨報不可斷:沒有 recap(第一天/壞檔)是空清單,不是例外。"""
    pk = ep.build({}, {}, {}, [], [], {}, as_of="x",
                  target_session_date="y", sanitize=lambda s, *a: s)
    assert pk["yesterday_watch"] == []


# ---------------------------------------------------------------- 驗收

def _validate(obj_over, watch=None):
    import analysis_validate as av
    import fixtures_analysis as fx
    pk = {"news": [{"source_item_id": "n1", "title": "t", "entities": []}],
          "news_clusters": {"clusters": []},
          "yesterday_watch": watch if watch is not None else [
              {"watch_id": "w1", "trigger": "A", "why": "", "horizon": "1d",
               "date": "2026-08-09"}]}
    obj = fx.valid_analysis()
    obj.update(obj_over)
    return [p for p in av.validate(obj, pk) if "watch" in p or "觀察點" in p]


def test_every_declared_watch_must_be_reviewed():
    """**缺一條,「逐日追蹤」就是宣稱而不是性質。**"""
    assert _validate({"watch_review": []})
    ok = _validate({"watch_review": [
        {"watch_id": "w1", "status": "not_triggered",
         "what_happened": "還在等財報", "evidence_ids": []}]})
    assert not ok, ok


def test_triggered_needs_todays_evidence():
    """「已觸發」不引今天的證據,就只是一句話。"""
    bad = _validate({"watch_review": [
        {"watch_id": "w1", "status": "triggered",
         "what_happened": "財報上修", "evidence_ids": []}]})
    assert any("已觸發" in p for p in bad), bad
    ok = _validate({"watch_review": [
        {"watch_id": "w1", "status": "triggered",
         "what_happened": "財報上修", "evidence_ids": ["n1"]}]})
    assert not ok, ok


def test_invented_and_duplicated_reviews_are_rejected():
    """編造的代號比漏掉更危險 —— 它看起來有回顧;重複會稀釋逐條的意思。"""
    bad = _validate({"watch_review": [
        {"watch_id": "w9", "status": "not_triggered",
         "what_happened": "", "evidence_ids": []}]})
    assert any("不存在" in p for p in bad), bad
    dup = _validate({"watch_review": [
        {"watch_id": "w1", "status": "not_triggered",
         "what_happened": "", "evidence_ids": []},
        {"watch_id": "w1", "status": "triggered",
         "what_happened": "x", "evidence_ids": ["n1"]}]})
    assert any("兩次" in p for p in dup), dup


def test_a_day_without_declared_watch_requires_nothing():
    """第一天(沒有 yesterday_watch)不得逼模型編回顧。"""
    assert not _validate({"watch_review": []}, watch=[])
    # 沒有宣告卻寫了回顧 → 編造
    assert _validate({"watch_review": [
        {"watch_id": "w1", "status": "not_triggered",
         "what_happened": "", "evidence_ids": []}]}, watch=[])


# ---------------------------------------------------------------- 渲染

def test_the_email_shows_the_review_with_the_original_trigger():
    """信裡要看得到「昨天預期 → 今天結果」—— trigger 原文從 packet 查,
    模型只回代號(代號進信等於沒寫)。"""
    import analysis_render as ar
    import fixtures_analysis as fx
    obj = fx.valid_analysis()
    obj["watch_review"] = [
        {"watch_id": "w1", "status": "triggered",
         "what_happened": "美光財報如期上修", "evidence_ids": ["n1"]}]
    pk = {"yesterday_watch": [
        {"watch_id": "w1", "trigger": "美光財報上修 HBM 出貨",
         "why": "", "horizon": "1w", "date": "2026-08-09"}]}
    text = ar.render(obj, pk)
    assert "昨日觀察點回顧" in text
    assert "美光財報上修 HBM 出貨：已觸發（美光財報如期上修）" in text
    # 沒有回顧就沒有這一節(不要空標題)
    obj["watch_review"] = []
    assert "昨日觀察點回顧" not in ar.render(obj, pk)


def test_the_prompt_declares_the_review_rule():
    """prompt 要說出「逐條回顧、已觸發要引證據、不是證據」——
    沒說的話,schema 只是一個沒人知道怎麼填的欄位。"""
    import io
    from pathlib import Path
    src = io.open(Path(__file__).resolve().parents[1] / "prompt_profiles.py",
                  encoding="utf-8").read()
    anchor = "EVIDENCE.yesterday_watch"
    assert anchor in src
    seg = src[src.index(anchor):src.index(anchor) + 600]
    assert "watch_review" in seg and "逐條" in seg, seg
    assert "不是證據" in seg, seg


# ===== 外審第一輪 =====

def test_an_empty_what_happened_is_rejected_for_every_status():
    """**空的 `what_happened` 讓閉環有形無實**(外審 F1):strict schema
    只保證欄位在,空字串是合法 JSON —— 信裡那一行會只剩狀態三個字。"""
    for status, extra in (("triggered", {"evidence_ids": ["n1"]}),
                          ("not_triggered", {}),
                          ("no_longer_relevant", {})):
        bad = _validate({"watch_review": [dict(
            {"watch_id": "w1", "status": status, "what_happened": "  ",
             "evidence_ids": []}, **extra)]})
        assert any("what_happened" in p for p in bad), (status, bad)


def test_triggered_cannot_rest_only_on_stale_evidence():
    """**不同步的資料不得單獨支撐「已觸發」**(外審 F2):美股休市日拿
    `market:QQQ.*` 當唯一根據,「今天出現了」根本不是今天的觀察。
    判準與高重要性 claim 同一條;混一筆今天的就放行(引用不禁止)。"""
    import analysis_validate as av
    import fixtures_analysis as fx
    pk = ep.build({"QQQ": {"change_pct": 1.2},
                   "US_HOLIDAY": {"detected": True}}, {}, {},
                  [{"source_item_id": "n1", "title": "台積電新聞",
                    "entities": ["台積電"], "source": "經濟日報"}],
                  [], {}, as_of="2026-08-10 06:00",
                  target_session_date="2026-08-10",
                  sanitize=lambda s, *a: s)
    pk["yesterday_watch"] = [{"watch_id": "w1", "trigger": "A", "why": "",
                              "horizon": "1d", "date": "2026-08-09"}]
    obj = fx.valid_analysis()

    def _wr(ids):
        obj["watch_review"] = [{"watch_id": "w1", "status": "triggered",
                                "what_happened": "如期發生",
                                "evidence_ids": ids}]
        return [p for p in av.validate(obj, pk) if "watch_review" in p]

    stale_only = _wr(["market:QQQ.change_pct"])
    assert any("不同步" in p for p in stale_only), stale_only
    assert not _wr(["market:QQQ.change_pct", "n1"])

