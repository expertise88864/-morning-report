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

def test_new_watches_enter_the_ledger_with_stable_ids():
    """觀察點要進帳本,而且**代號跨日穩定**(第三十輪外審 P1-2)——
    每天重新編號的話,昨天的 `w1` 明天指到另一件事。"""
    led, seq = rc.carry_watch({}, _analysis(), "2026-08-10")
    assert [w["trigger"] for w in led] == [
        "美光財報上修 HBM 出貨", "台積電法說資本支出"]
    assert [w["watch_id"] for w in led] == ["w1", "w2"] and seq == 2
    assert led[0]["why"] == "驗證 AI 需求"
    assert led[0]["status"] == rc.WATCH_OPEN
    assert led[0]["created"] == "2026-08-10"
    # 續開時序號接著跑(不重用號碼 —— 重用會讓昨天的 w1 明天指到別件事)
    led2, _ = rc.carry_watch(
        {"watch": led, "watch_seq": seq},
        {"watch_triggers": [{"trigger": "新的一條", "why": "",
                             "horizon": "1-5d"}]}, "2026-08-11")
    assert [w["watch_id"] for w in led2] == ["w1", "w2", "w3"], led2


def test_ids_are_never_reused_after_one_closes():
    """**序號是帳本的,不是清單位置的**(這條反例只靠序號分勝負):
    w1 關掉之後再開一條 —— 用「清單長度 + 1」的話新的那條會叫 w2,
    與還開著的 w2 撞號,而模型的回顧是按代號對帳的。"""
    prior = {"watch": [_w("w1", "A"), _w("w2", "B")], "watch_seq": 2}
    led, seq = rc.carry_watch(
        prior,
        {"watch_triggers": [{"trigger": "新的一條", "why": "",
                             "horizon": "1-5d"}],
         "watch_review": [{"watch_id": "w1", "status": "triggered",
                           "what_happened": "發生了", "evidence_ids": ["n1"]}]},
        "2026-08-10")
    ids = [w["watch_id"] for w in led]
    assert ids == ["w2", "w3"], ids
    assert len(set(ids)) == len(ids) and seq == 3


def test_watch_is_capped_and_junk_is_dropped():
    """上限與空 trigger:開十條等於逼明天寫十條回顧。"""
    many = [{"trigger": f"觀察{i}", "why": "", "horizon": "1-5d"}
            for i in range(9)] + [{"trigger": "", "why": "x"}, "垃圾"]
    led, _ = rc.carry_watch({}, _analysis(watch_triggers=many), "2026-08-10")
    assert len(led) == rc.WATCH_MAX          # 單日新增仍受 WATCH_MAX 限制
    assert all(w["trigger"] for w in led)


def test_a_watch_only_day_is_still_saved(tmp_path):
    """觀點空、觀察點不空的日子仍要存 —— 回顧的閉環不能斷一天。"""
    out = rc.save(tmp_path / "r.json", _analysis(),
                  {"target_session_date": "2026-08-10", "news": [],
                   "news_clusters": {"clusters": []}})
    assert out == rc.SAVED
    assert rc.load(tmp_path / "r.json")["watch"]


# ---------------------------------------------------------------- 派號

def _led(*rows):
    return {"date": "2026-08-09", "watch": list(rows), "watch_seq": len(rows)}


def _w(wid, trig, created="2026-08-09", status=rc.WATCH_OPEN,
       deadline="2026-09-06", horizon="1-4w"):
    return {"watch_id": wid, "trigger": trig, "why": "", "horizon": horizon,
            "status": status, "created": created, "deadline": deadline,
            "last_reviewed": ""}


def test_usable_watch_reports_open_ones_and_gates_same_day_creations():
    """代號由帳本帶著走;**同日建立的不回顧** —— 拿今天剛寫的觀察點
    當「昨天的預期」,每一條都會「已觸發」(它就是照今天的新聞寫的)。
    逐條比 `created`,因為帳本現在同時帶著不同天建立的觀察點。"""
    recap = _led(_w("w1", "A"), _w("w2", "B"),
                 _w("w9", "今天剛寫的", created="2026-08-10"))
    got = rc.usable_watch(recap, "2026-08-10")
    assert [(w["watch_id"], w["trigger"]) for w in got] == [
        ("w1", "A"), ("w2", "B")]
    assert got[0]["date"] == "2026-08-09"
    assert rc.usable_watch(recap, "") == []
    assert rc.usable_watch({"date": "2026-08-09"}, "2026-08-10") == []
    # 已關閉的不再要求回顧
    assert rc.usable_watch(_led(_w("w1", "A", status="triggered")),
                           "2026-08-10") == []


# ---------------------------------------------------------------- 進 packet

def _packet(sanitize=lambda s, *a: s, date="2026-08-10"):
    return ep.build({"ANALYSIS_RECAP": {
        "date": "2026-08-09", "items": [],
        "watch": [{"watch_id": "w1", "trigger": "美光財報上修 HBM 出貨",
                   "why": "驗證 AI 需求", "horizon": "1-4w",
                   "status": "open", "created": "2026-08-09",
                   "deadline": "2026-09-06", "last_reviewed": ""}]}},
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


# ===== 第三十輪外審 P1-2:觀察點的生命週期 =====

def test_a_not_triggered_watch_survives_to_the_next_day():
    """**`not_triggered` 不是終局**(外審 P1-2):上一版每天用今天的
    `watch_triggers` 整個覆寫 —— horizon 寫著 1–4 週的觀察點活到隔天為止,
    狀態機實際上是「建立 → 回顧一次 → 消失」。"""
    prior = _led(_w("w1", "等美國商務部公告新限制"))
    obj = {"watch_triggers": [],
           "watch_review": [{"watch_id": "w1", "status": "not_triggered",
                             "what_happened": "還在等", "evidence_ids": []}]}
    led, _ = rc.carry_watch(prior, obj, "2026-08-10")
    assert [w["watch_id"] for w in led] == ["w1"], led
    assert led[0]["status"] == rc.WATCH_OPEN
    assert led[0]["last_reviewed"] == "2026-08-10"


def test_a_long_horizon_watch_survives_several_reviews():
    """1–4 週的觀察點要撐過連續多天的「還沒」。"""
    state = _led(_w("w1", "等商務部公告", created="2026-08-01",
                    deadline="2026-08-29"))
    review = {"watch_triggers": [],
              "watch_review": [{"watch_id": "w1", "status": "not_triggered",
                                "what_happened": "還在等",
                                "evidence_ids": []}]}
    for day in ("2026-08-02", "2026-08-03", "2026-08-04", "2026-08-05"):
        led, seq = rc.carry_watch(state, review, day)
        state = {"date": day, "watch": led, "watch_seq": seq}
    assert [w["watch_id"] for w in state["watch"]] == ["w1"]
    assert state["watch"][0]["last_reviewed"] == "2026-08-05"


def test_triggered_and_no_longer_relevant_close_the_watch():
    """模型的兩種關閉判斷都要真的把它從帳本移除。"""
    for status in ("triggered", "no_longer_relevant"):
        led, _ = rc.carry_watch(
            _led(_w("w1", "等財報")),
            {"watch_triggers": [],
             "watch_review": [{"watch_id": "w1", "status": status,
                               "what_happened": "x",
                               "evidence_ids": ["n1"]}]},
            "2026-08-10")
        assert led == [], (status, led)


def test_python_expires_a_watch_by_its_horizon():
    """**過期是時間的函數,不問模型**:模型每天看到的是不同的今天,
    而 deadline 是建立當天就算好的。"""
    prior = _led(_w("w1", "intraday 的觀察", created="2026-08-01",
                    deadline="2026-08-02"))
    led, _ = rc.carry_watch(prior, {"watch_triggers": []}, "2026-08-03")
    assert led == [], led
    # 還沒到期的照樣留著(反例只靠 deadline 分勝負)
    keep, _ = rc.carry_watch(prior, {"watch_triggers": []}, "2026-08-02")
    assert [w["watch_id"] for w in keep] == ["w1"]


def test_horizon_decides_the_deadline():
    """三種 horizon 各自的到期日由宣告表決定;認不出來用短的
    (過期只是少追一條,永不過期會累積成沒有人看的清單)。"""
    def _deadline(h):
        led, _ = rc.carry_watch(
            {}, {"watch_triggers": [{"trigger": f"觀察{h}", "why": "",
                                     "horizon": h}]}, "2026-08-10")
        return led[0]["deadline"]
    assert _deadline("intraday") == "2026-08-11"
    assert _deadline("1-5d") == "2026-08-15"
    assert _deadline("1-4w") == "2026-09-07"
    assert _deadline("亂寫的") == "2026-08-15"      # 預設 5 天


def test_a_same_day_rerun_does_not_duplicate_or_age_the_watch():
    """同日重跑:同一句 trigger 不重複開,`created` 不變(年齡不會被
    重跑洗掉 —— 那會讓 1–4 週的觀察點每跑一次就延壽一次)。"""
    led1, seq1 = rc.carry_watch({}, _analysis(), "2026-08-10")
    led2, seq2 = rc.carry_watch({"date": "2026-08-10", "watch": led1,
                                 "watch_seq": seq1}, _analysis(),
                                "2026-08-10")
    assert [w["watch_id"] for w in led2] == [w["watch_id"] for w in led1]
    assert [w["created"] for w in led2] == ["2026-08-10"] * len(led1)
    assert [w["deadline"] for w in led2] == [w["deadline"] for w in led1]
    assert seq2 == seq1


def test_the_producer_records_the_watch_counters(tmp_path):
    """**閉環要看得見**:manifest 記開著幾條 —— 沒有計數的話,
    「觀察點真的在追蹤」只是一句宣稱。"""
    import sys
    sys.path.insert(0, "tests")
    import fixtures_analysis as fx
    man = {}
    obj = fx.valid_analysis()
    obj["watch_triggers"] = [{"trigger": "等台積電法說", "why": "",
                              "horizon": "1-5d", "claim_ids": []}]
    pk = {"target_session_date": "2026-08-10", "news": [],
          "news_clusters": {"clusters": []}}
    assert rc.save(tmp_path / "r.json", obj, pk, manifest=man) == rc.SAVED
    assert man["llm"]["watch_open"] == 1
    saved = rc.load(tmp_path / "r.json")
    assert saved["watch"][0]["watch_id"] == "w1"
    assert saved["watch_seq"] == 1


def test_closing_a_watch_as_irrelevant_needs_todays_evidence():
    """**關閉一條觀察點是今天的事實判斷**(第三十輪外審 P2-1):
    「前提已經不存在」與「已經發生」一樣需要今天的證據 —— 少了它,
    模型可以一句話永久關掉一條還沒驗證的預期。"""
    bad = _validate({"watch_review": [
        {"watch_id": "w1", "status": "no_longer_relevant",
         "what_happened": "原本的政策風險已經解除", "evidence_ids": []}]})
    assert any("不再相關" in p for p in bad), bad
    ok = _validate({"watch_review": [
        {"watch_id": "w1", "status": "no_longer_relevant",
         "what_happened": "政策已撤回", "evidence_ids": ["n1"]}]})
    assert not ok, ok
    # 「還沒觸發」什麼都沒宣稱,不需要證據(反例只靠 status 分勝負)
    assert not _validate({"watch_review": [
        {"watch_id": "w1", "status": "not_triggered",
         "what_happened": "還在等", "evidence_ids": []}]})


def test_the_prompt_declares_the_lifecycle():
    """prompt 要說出生命週期,否則模型會為了保住一條預期而每天重寫它
    (那正是上一版唯一能延續的方式)。"""
    import io as _io
    from pathlib import Path
    src = _io.open(Path(__file__).resolve().parents[1] / "prompt_profiles.py",
                   encoding="utf-8").read()
    i = src.index("EVIDENCE.yesterday_watch")
    seg = src[i:i + 900]
    assert "還開著" in seg, seg
    assert "留到明天" in seg, seg
    assert "過期由本報判" in seg, seg


# ===== 外審第一輪 =====

def test_an_emptied_ledger_is_persisted(tmp_path):
    """**「什麼都沒有」與「原本有、今天清空了」是兩件事**(外審 r1):
    最後一條觀察點今天關閉、當天又沒有值得留的觀點時,上一版在寫檔前就
    return 了 —— 檔案沒被覆寫,那條關掉的明天照樣冒出來。"""
    import json
    f = tmp_path / "r.json"
    f.write_text(json.dumps({
        "date": "2026-08-09", "items": [],
        "watch": [_w("w1", "等商務部公告")], "watch_seq": 1},
        ensure_ascii=False), encoding="utf-8")
    obj = {"watch_triggers": [],
           "watch_review": [{"watch_id": "w1", "status": "triggered",
                             "what_happened": "公告了", "evidence_ids": ["n1"]}]}
    pk = {"target_session_date": "2026-08-10", "news": [],
          "news_clusters": {"clusters": []}}
    man = {}
    out = rc.save(f, obj, pk, manifest=man)
    assert out == rc.SAVED, out
    assert rc.load(f)["watch"] == [], rc.load(f)
    assert man["llm"]["watch_closed_today"] == 1
    # 真的什麼都沒有的日子仍然是 NOTHING(修正不得把它一起改掉)
    empty = tmp_path / "e.json"
    assert rc.save(empty, {"watch_triggers": []}, pk) == rc.NOTHING


def test_a_legacy_watch_is_migrated_not_broken():
    """**上線當天 state 裡的每一條都是舊形狀**(外審 r2):沒有代號的話
    好幾條在驗證與渲染裡撞成同一個,而 `carry_watch` 認不出空代號的回顧
    → 那些觀察點既關不掉也追不動。升級是確定性的。"""
    legacy = {"date": "2026-08-09", "items": [],
              "watch": [{"trigger": "A", "why": "", "horizon": "1-4w"},
                        {"trigger": "B", "why": "", "horizon": "1-5d"}]}
    got = rc.usable_watch(legacy, "2026-08-10")
    assert [w["watch_id"] for w in got] == ["w1", "w2"], got
    assert all(w["watch_id"] for w in got)
    assert got[0]["date"] == "2026-08-09"
    # 升級後關得掉(這正是舊形狀原本做不到的事)
    led, seq = rc.carry_watch(
        legacy, {"watch_triggers": [],
                 "watch_review": [{"watch_id": "w1", "status": "triggered",
                                   "what_happened": "x",
                                   "evidence_ids": ["n1"]}]}, "2026-08-10")
    assert [w["watch_id"] for w in led] == ["w2"], led
    # 新開的不得與補發的號碼相撞
    led2, _ = rc.carry_watch(
        {"date": "2026-08-09", "watch": legacy["watch"]},
        {"watch_triggers": [{"trigger": "新的", "why": "",
                             "horizon": "1-5d"}]}, "2026-08-10")
    ids = [w["watch_id"] for w in led2]
    assert len(set(ids)) == len(ids) == 3, ids


def test_an_expired_watch_never_reaches_the_prompt():
    """**過期是 Python 的判斷,不該消耗模型的一次回顧**(外審 r3):
    留到存檔才移除的話,模型會在過期後多回顧一次,而驗證還會要求它。"""
    recap = {"date": "2026-08-01",
             "watch": [_w("w1", "X", created="2026-08-01",
                          deadline="2026-08-02", horizon="intraday")]}
    assert rc.usable_watch(recap, "2026-08-03") == []
    assert [w["watch_id"] for w in rc.usable_watch(recap, "2026-08-02")] == ["w1"]


def test_the_closed_counter_counts_closures_not_rejections(tmp_path):
    """**關閉數要數「原本開著、現在不在帳本上」的**(外審 r4):
    用相減的話,被上限擋掉的新條目會被記成「關閉」。"""
    import json
    f = tmp_path / "r.json"
    open_rows = [_w(f"w{i}", f"既有{i}") for i in range(1, 9)]   # 已達上限
    f.write_text(json.dumps({"date": "2026-08-09", "items": [],
                             "watch": open_rows, "watch_seq": 8},
                            ensure_ascii=False), encoding="utf-8")
    obj = {"watch_triggers": [{"trigger": f"新的{i}", "why": "",
                               "horizon": "1-5d"} for i in range(5)],
           "watch_review": [{"watch_id": w["watch_id"],
                             "status": "not_triggered",
                             "what_happened": "還在等", "evidence_ids": []}
                            for w in open_rows]}
    man = {}
    rc.save(f, obj, {"target_session_date": "2026-08-10", "news": [],
                     "news_clusters": {"clusters": []}}, manifest=man)
    assert man["llm"]["watch_open"] == rc.WATCH_OPEN_MAX
    assert man["llm"]["watch_closed_today"] == 0, man["llm"]

