# -*- coding: utf-8 -*-
"""2026-08-25 生產:信寄出了,但 Luna 被自己的驗證擋下 14 條而落 legacy。

manifest 逐條看下來,其中三條**不是模型的內容問題**:
  * 「不是合法 JSON」—— 回覆是「散文 + ```json 圍欄」,解析器在第 0 個
    字元就死,那一輪被判成語法輪(而語法本來就是對的),
    `repair_modes` 變成 semantic→syntax→semantic 而 semantic 額度是 2。
  * 兩條 `shared_driver_notes 宣稱的驅動是 X,而…被歸類為 Y` —— 而 X 與 Y
    是同一個東西(`label（code）` vs `code`)。
"""
import datetime as dt
import io
import json
from pathlib import Path

import analysis_contracts as ac
import deepseek_responses as dsr
import event_graph as eg
import morning_report as mr

_FENCE = chr(96) * 3


def test_prose_wrapped_json_is_not_a_syntax_failure():
    """生產原文的形狀:標題、說明、然後才是圍欄裡的 JSON。"""
    text = (chr(10).join([
        "## 修正說明", "",
        "上一輪輸出經逐項檢查,確認以下七項問題須修正;其餘部分照抄。", "",
        "## 修正後的完整輸出", "",
        _FENCE + "json",
        json.dumps({"executive_summary": "輝達財報前美股半導體先跌為敬",
                    "top_news_analysis": []}, ensure_ascii=False),
        _FENCE, "",
        "以上已修正 `n5431e`(top_news_analysis 與 macro_environment)。"]))
    obj, how = dsr.json_object_from_text(text)
    assert how == "fence" and obj["executive_summary"].startswith("輝達"), how
    # 正常情況照舊(裸 JSON 不繞路)
    assert dsr.json_object_from_text('{"a": 1}') == ({"a": 1}, "raw")
    # 連圍欄都沒有的散文夾帶也撿得回來
    assert dsr.json_object_from_text('說明:{"a": 1} 以上')[1] == "braces"
    # **截斷仍然是語法問題**:三種候選都失敗 → 分類是對的,不得硬救
    assert dsr.json_object_from_text("## 說明" + chr(10) + _FENCE + "json"
                                     + chr(10) + '{"a": [1,') == (None, "")
    # 根不是物件的不收(那是結構問題,有它自己的處置)
    assert dsr.json_object_from_text("[1, 2, 3]") == (None, "")


def test_the_primary_parser_uses_the_recovery_and_leaves_a_trace():
    """接線:helper 對了但主解析沒用它,生產照樣落 legacy。
    而且救回來**要留痕** —— 模型正在偏離「只回 JSON」的契約,
    信卻會看起來完全正常。"""
    src = io.open(Path(mr.__file__), encoding="utf-8").read()
    i = src.index('obj = json.loads(out["text"])')
    seg = src[i:i + 1800]
    assert "_dsr.json_object_from_text(out.get(\"text\"))" in seg, seg[:400]
    assert "_parse_exc = obj is None" in seg, seg[:400]
    assert '"recovered_by"' in seg, seg[:600]


def _groups():
    """用 producer 真的算一次(不是手捏 `shared_driver_groups`)。"""
    clusters = [{"cluster_id": f"cluster:{c}", "member_source_ids": [str(i)]}
                for i, c in enumerate("abcd")]
    news = [{"source_item_id": "0", "title": "AI 伺服器資本支出再上修",
             "summary": "資料中心 capex"},
            {"source_item_id": "1", "title": "GPU 需求推升資本支出",
             "summary": "AI server"},
            {"source_item_id": "2", "title": "Fed 官員鴿派發言",
             "summary": "降息預期"},
            {"source_item_id": "3", "title": "美債殖利率回落",
             "summary": "10年期公債"}]
    return eg.build(clusters, news)


def test_the_driver_name_the_model_was_given_is_not_a_rejection():
    """packet 給模型的是 `driver`(代號)**與** `label`(中文名)兩欄,
    而判準只拿代號做字串相等 —— 模型把兩欄接起來寫,就被駁回。
    命名失誤只要指得回唯一一個對象,這個 repo 的作法是正規化收下。"""
    graph = _groups()
    grp = {g["driver"]: g for g in graph["shared_driver_groups"]}
    assert set(grp) == {"ai_capex", "us_monetary"}, sorted(grp)
    assert grp["ai_capex"]["label"] == "AI 資本支出循環"

    packet = {"event_graph": graph,
              "news_clusters": {"clusters": [
                  {"cluster_id": c} for c in
                  ("cluster:a", "cluster:b", "cluster:c", "cluster:d")]}}

    def _problems(driver_text, ids):
        obj = {"cross_market_synthesis": {"shared_driver_notes": [
            {"driver": driver_text, "cluster_ids": ids,
             "why_not_double_counting": "同一條傳導鏈"}]}}
        return [p for p in ac.reference_problems(obj, packet)
                if "宣稱的驅動" in p]

    ab = ["cluster:a", "cluster:b"]
    # 生產那天實際寫的兩種形狀
    assert _problems("AI 資本支出循環（ai_capex）", ab) == []
    assert _problems("聯準會政策路徑、美債殖利率、美國就業、美國通膨"
                     "（us_monetary）", ["cluster:c", "cluster:d"]) == []
    # 代號本身、標籤本身都照樣算對
    assert _problems("ai_capex", ab) == []
    assert _problems("AI 資本支出循環", ab) == []
    # 代號帶標籤(反過來的順序)也算對
    assert _problems("ai_capex(AI 資本支出循環)", ab) == []

    # **真的寫錯還是要擋。** r1 外審:第一版用無錨點的詞法搜尋,於是
    # 「代號有出現」就算過 —— 判準比它自己的 docstring 寬,一個指錯或
    # 含糊的驅動名會把 Luna 判成合格而不要求修補。
    for bad in ("us_monetary",                    # 指到別組
                "ai_capex_extra",                 # 只是前綴相同
                "美國就業（ai_capex）",             # 標籤是錯的,代號對
                "不是 ai_capex",                   # 散文夾帶
                "fed_policy / ai_capex"):         # 兩個代號,含糊
        assert _problems(bad, ab), f"{bad!r} 被放行了"


# ---------------- 使用者:去找一條 Actions 連得上的球場來源

def _wikitext(rows):
    """`{{中華職棒賽程|場次|MM/DD|時間|客|客分|主分|主|球場|結果}}` 的真實形狀。"""
    head = "== 例行賽 ==" + chr(10) + "{{中華職棒賽程/表頭|下半球季}}" + chr(10)
    return head + chr(10).join(rows)


def test_wikipedia_is_a_venue_source_that_actions_can_reach(monkeypatch):
    """2026-08-25 生產確認 `source: "cache"` —— CPBL 官網對 GitHub Actions
    是擋的,那條 live 路徑在生產等於不存在,而快取只有人在台灣本機跑過才會
    更新。Wikipedia 是**這個系統已經證明在 Actions 連得上的**(中職戰績表
    早就走它),而且逐場帶球場。

    調查過而不採用的:TheSportsDB 的 `strVenue` 是**球隊登記主場** ——
    08/25 中信寫洲際(實際大巨蛋)、統一寫台南市立(實際亞太主),
    與「猜一個球場」是同一件事;Sofascore 403;Yahoo 的 `gamestadium`
    是 null;ESPN 無 CPBL。"""
    rows = [
        "{{中華職棒賽程|286|08/25|18:35|[[樂天桃猿|樂天]]| | "
        "|[[中信兄弟|中信]]|[[臺北大巨蛋|大巨蛋]]|l}}",
        "{{中華職棒賽程|287|08/25|18:35|[[味全龍|味全]]| | "
        "|[[統一7-ELEVEn獅|統一]]|[[亞太國際棒球訓練中心|亞太主]]|l}}",
        "{{中華職棒賽程|1|03/28|17:05|[[中信兄弟|中信]]|2|3"
        "|[[樂天桃猿|樂天]]|[[樂天桃園棒球場|桃園]]|d}}",
    ]

    class _R:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"parse": {"wikitext": _wikitext(rows)}}

    monkeypatch.setattr(mr, "_http_get", lambda *a, **k: _R())
    now = dt.datetime(2026, 8, 25, 6, 0, tzinfo=mr.TPE)
    got = mr._cpbl_venue_from_wikipedia(now, 9)
    # 未來場次(比分欄空白)照樣要有 —— 那正是「未來一週賽程」要的
    assert got[("08/25", "中信")] == "大巨蛋"
    assert got[("08/25", "統一")] == "亞太主"
    # 視窗外的不收(03/28 是三月的比賽)
    assert ("03/28", "樂天") not in got, got

    # **命名別名只有一個**:全季 351 場比對下來,兩邊唯一的差異是這個。
    # 模糊比對不做 —— 「猜一個球場」正是這功能一開始就拒絕的東西。
    got2 = mr._cpbl_venue_from_wikipedia(
        dt.datetime(2026, 3, 28, 6, 0, tzinfo=mr.TPE), 1)
    assert got2[("03/28", "樂天")] == "樂天桃園", got2

    # 頁面結構變了要說得出來,不是靜靜回空
    class _NoSection(_R):
        def json(self):
            return {"parse": {"wikitext": "== 別的章節 =="}}

    monkeypatch.setattr(mr, "_http_get", lambda *a, **k: _NoSection())
    assert mr._cpbl_venue_from_wikipedia(now, 9) == {}


def test_the_venue_falls_through_official_then_wikipedia_then_cache(monkeypatch,
                                                                    tmp_path):
    """三層的順序與**來源標記**:只看 `matched` 的話,快取撐著的日子與
    官網正常的日子長得一模一樣,而快取何時過期沒有人知道。"""
    import inspect
    src = inspect.getsource(mr.fetch_cpbl_today_fixtures)
    i = src.index("venues = _cpbl_venue_map(")
    seg = src[i:i + 2600]
    assert "_cpbl_venue_from_wikipedia" in seg, seg
    # 官網走了快取(= 生產的實際情形)時才問 Wikipedia,而不是無條件覆蓋
    assert "if (_cached or not venues) else {}" in seg, seg
    for tag in ('"wikipedia"', '"cache"', '"none"', '"cpbl.com.tw"'):
        assert tag in seg, (tag, seg)


def test_short_and_full_home_names_both_match_the_fixture(monkeypatch):
    """官網的鍵是**全名**(統一7-ELEVEn獅)、Wikipedia 是**簡稱**(統一),
    而 Yahoo 給的 fixture 也是簡稱 —— 單向的 `x["home"] in home_key` 只對
    得上官網那一種,換到 Wikipedia 就一場都配不到(而賽程照出、只是永遠
    沒有球場,與被 geo-block 的症狀一模一樣)。"""
    now = dt.datetime(2026, 8, 25, 6, 0, tzinfo=mr.TPE)

    class _R:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"service": {"scoreboard": {
                "games": {"g1": {"status_type": "pregame",
                                 "start_time": "Tue, 25 Aug 2026 10:35:00 GMT",
                                 "away_team_id": "a", "home_team_id": "h"}},
                "teams": {"a": {"display_name": "樂天"},
                          "h": {"display_name": "中信"}}}}}

    monkeypatch.setattr(mr.requests, "get", lambda *a, **k: _R())
    for label, vmap in (("wikipedia 簡稱", {("08/25", "中信"): "大巨蛋"}),
                        ("官網全名", {("08/25", "中信兄弟"): "大巨蛋"})):
        monkeypatch.setattr(mr, "_cpbl_venue_map", lambda *a, **k: {})
        monkeypatch.setattr(mr, "_cpbl_venue_from_wikipedia",
                            lambda *a, _v=vmap, **k: _v)
        got = mr.fetch_cpbl_today_fixtures(now)
        assert got and got[0].get("venue") == "大巨蛋", (label, got)


def test_partial_wikipedia_coverage_still_uses_the_cache(monkeypatch):
    """2026-08-25 外審 P2:第一版只要 Wikipedia 有**任何一筆**就把整張表
    換掉。社群頁面只更新了一部分未來賽事時,快取明明答得出來的場次會
    變成沒有球場 —— 而症狀(信裡少一個地點)與被 geo-block 一模一樣。
    兩張表都要留著,**逐場**依序問。"""
    now = dt.datetime(2026, 8, 25, 6, 0, tzinfo=mr.TPE)
    games = {f"g{i}": {"status_type": "pregame",
                       "start_time": "Tue, 25 Aug 2026 10:35:00 GMT",
                       "away_team_id": f"a{i}", "home_team_id": f"h{i}"}
             for i in (1, 2)}
    teams = {"a1": {"display_name": "樂天"}, "h1": {"display_name": "中信"},
             "a2": {"display_name": "味全"}, "h2": {"display_name": "統一"}}

    class _R:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"service": {"scoreboard": {"games": games,
                                               "teams": teams}}}

    monkeypatch.setattr(mr.requests, "get", lambda *a, **k: _R())
    # 官網被擋 → 回整季快取;Wikipedia 只收錄了其中一場
    monkeypatch.setattr(mr, "_cpbl_venue_map",
                        lambda *a, **k: {("08/25", "中信兄弟"): "大巨蛋",
                                         ("08/25", "統一7-ELEVEn獅"): "亞太主"})
    monkeypatch.setattr(mr, "_VENUE_FROM_CACHE", {"hit": True})
    monkeypatch.setattr(mr, "_cpbl_venue_from_wikipedia",
                        lambda *a, **k: {("08/25", "中信"): "大巨蛋"})
    mr._RUN_MANIFEST.pop("sports", None)
    got = mr.fetch_cpbl_today_fixtures(now)
    by_home = {x["home"]: x.get("venue") for x in got}
    assert by_home == {"中信": "大巨蛋", "統一": "亞太主"}, by_home
    slot = mr._RUN_MANIFEST["sports"]["cpbl_venue"]
    # 混合來源要看得出來 —— 只有一個 `source` 的話,快取補的那一場是隱形的
    assert slot["by_source"] == {"cache": 1, "wikipedia": 1}, slot
    assert slot["matched"] == 2 and slot["reason"] == "", slot


def test_section_coverage_is_judged_by_that_sections_own_material():
    """2026-08-25 外審 P2:整組涵蓋率建議先前包在 `_avail >= 30` 底下,而
    逐段的判準本來就已經是「那一段的素材夠不夠」—— 兩套並存的結果是
    **15~29 則素材的那一大段完全不進判斷**。使用者已經兩次反映科技與其他
    類股太少,而守衛在合理的生產區間裡靜靜地什麼都不做。"""
    import sys

    import analysis_depth as ad
    sys.path.insert(0, "tests")
    import fixtures_analysis as fx

    def _adv(n_tech, n_other, src_tech, src_other):
        obj = fx.valid_analysis()
        rows, pk_news = [], []
        for i in range(src_tech):
            pk_news.append({"source_item_id": f"t{i}", "entities": ["2330"],
                            "title": "台積電先進封裝再擴產"})
        for i in range(src_other):
            pk_news.append({"source_item_id": f"o{i}", "entities": ["2603"],
                            "title": "長榮美西運價連四漲"})
        for i in range(n_tech):
            rows.append({"source_item_id": f"t{i}", "why_it_matters": "x",
                         "direction": "bullish", "materiality": "medium"})
        for i in range(n_other):
            rows.append({"source_item_id": f"o{i}", "why_it_matters": "x",
                         "direction": "bullish", "materiality": "medium"})
        obj["top_news_analysis"] = rows
        obj["data_gaps"] = []
        pk = {"news": pk_news, "market": {},
              "tw_universe": [
                  {"code": "2330", "name": "台積電", "industry": "半導體業"},
                  {"code": "2603", "name": "長榮", "industry": "航運業"}]}
        return chr(10).join(ad.depth_advisories(obj, pk))

    # 外審的反例:素材 29 則(科技 15 / 其他 14),輸出只有 7 + 6
    a = _adv(7, 6, src_tech=15, src_other=14)
    assert "第八段靠它" in a, ("29 則素材落進盲區,科技不足沒有被催", a)
    assert "第九段靠它" in a, a

    # 更小的例子:素材 20 則也足以達成 8 + 7
    b = _adv(6, 6, src_tech=10, src_other=10)
    assert "第八段靠它" in b and "第九段靠它" in b, b

    # **不得逼它湊**:那一段的素材真的不夠時不催(這是既有的規則,
    # 拆掉全域門檻不可以把它一起拆掉)
    c = _adv(6, 9, src_tech=7, src_other=20)
    assert "第八段靠它" not in c, ("科技素材只有 7 則卻要求 8 則", c)
    assert "第九段靠它" not in c, c

    # 總則數那一條有自己的素材前提:素材本身就少於目標時不催
    d = _adv(5, 5, src_tech=6, src_other=6)
    assert f"{ad.NEWS_TARGET_MIN}–" not in d, ("素材只有 12 則卻要求 15 則", d)

    # **認不出主體的新聞不算任何一段的素材**:素材面問的是「今天真的有
    # 那一段的料嗎」,而 `not is_tech` 只代表「沒被認出是科技」。先前
    # `_src_other = 總數 - 科技` 把空白新聞全算成非科技 —— 10 則沒有主體
    # 的新聞就足以要求第九段寫 7 則,那正是「逼它湊」。
    obj = fx.valid_analysis()
    obj["top_news_analysis"] = [{"source_item_id": f"z{i}",
                                 "why_it_matters": "x",
                                 "direction": "bullish",
                                 "materiality": "medium"} for i in range(4)]
    obj["data_gaps"] = []
    blank = ad.depth_advisories(obj, {"news": [{"source_item_id": f"z{i}"}
                                               for i in range(10)],
                                      "market": {}, "tw_universe": []})
    assert not any("靠它" in x for x in blank), blank


def test_the_venue_cache_is_scoped_to_its_season(monkeypatch, tmp_path):
    """2026-08-25 外審 P2:快取的鍵是 `MM/DD|主隊`,**沒有年份**。
    2027-08-25 官網照樣被擋、Wikipedia 又剛好失敗時,`08/25|中信兄弟` 會
    命中 2026 年那一筆 —— 把去年的球場印成今年的。而這個功能一開始就寫著
    「猜錯的地點比沒有地點糟」,那正好違反它自己的判準。"""
    cache = tmp_path / "v.json"
    monkeypatch.setattr(mr, "CPBL_VENUE_FILE", cache)
    mr._save_cpbl_venue_cache({"08/25|中信兄弟": "大巨蛋"}, 2026)
    body = json.loads(cache.read_text(encoding="utf-8"))
    assert body["season"] == 2026 and body["games"], body
    assert body.get("fetched_at"), body      # 多舊了要查得出來

    assert mr._load_cpbl_venue_cache(2026) == {("08/25", "中信兄弟"): "大巨蛋"}
    assert mr._load_cpbl_venue_cache(2027) == {}, "去年的球場被當成今年的"

    # 舊格式(沒有 season 欄)也不採用:年份不可考,後果與「是別年的」一樣
    cache.write_text(json.dumps({"08/25|中信兄弟": "大巨蛋"},
                                ensure_ascii=False), encoding="utf-8")
    assert mr._load_cpbl_venue_cache(2026) == {}, "舊格式沒有年份卻被採用"

    # 接線:官網失敗時要用**那一天的**季別去讀,不是 wall clock
    import inspect
    src = inspect.getsource(mr._cpbl_venue_map)
    assert "_load_cpbl_venue_cache(_now.year)" in src, src[-600:]
    assert "_load_cpbl_venue_cache((as_of or dt.datetime.now(TPE)).year)" in src


def test_a_corrupt_cache_must_not_take_the_whole_schedule_with_it(monkeypatch,
                                                                  tmp_path):
    """2026-08-25 外審:`load_json_state(expected=dict)` **只驗最外層**。
    `{"season": 2026, "games": ["x"]}` 時 `(x or {}).items()` 會拋 ——
    而那是從 `_cpbl_venue_map` 的 except 裡呼叫的,例外往外逃、呼叫端的
    try 把 `cpbl_fixtures` 整段丟掉:讀者少的不是一個地點,是一整週的賽程。
    (`[] or {}` 是 `{}` 所以空 list 剛好躲過 —— 非空的才炸。)"""
    cache = tmp_path / "v.json"
    monkeypatch.setattr(mr, "CPBL_VENUE_FILE", cache)
    cache.write_text(json.dumps({"season": 2026, "games": ["壞掉"]},
                                ensure_ascii=False), encoding="utf-8")
    mr._DEGRADED_STEPS.clear()
    assert mr._load_cpbl_venue_cache(2026) == {}
    assert any("cpbl_venues" in d for d in mr._DEGRADED_STEPS), \
        mr._DEGRADED_STEPS

    # **賽程不受影響**:場地那條路上拋出任何東西都不得帶走 fixtures
    now = dt.datetime(2026, 8, 25, 6, 0, tzinfo=mr.TPE)

    class _R:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"service": {"scoreboard": {
                "games": {"g1": {"status_type": "pregame",
                                 "start_time": "Tue, 25 Aug 2026 10:35:00 GMT",
                                 "away_team_id": "a", "home_team_id": "h"}},
                "teams": {"a": {"display_name": "樂天"},
                          "h": {"display_name": "中信"}}}}}

    monkeypatch.setattr(mr.requests, "get", lambda *a, **k: _R())
    monkeypatch.setattr(mr, "_cpbl_venue_map",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("場地那條路炸了")))
    mr._RUN_MANIFEST.pop("sports", None)
    got = mr.fetch_cpbl_today_fixtures(now)
    assert got and got[0]["home"] == "中信", got
    assert "venue" not in got[0], got
    slot = mr._RUN_MANIFEST["sports"]["cpbl_venue"]
    assert slot["reason"].startswith("error:"), slot
    assert slot["fixtures"] == 1 and slot["matched"] == 0, slot
