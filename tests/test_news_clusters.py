# -*- coding: utf-8 -*-
"""**同一件事被四家媒體報導,不是四件事**(第十八輪 P1-3)。

兩個一起解決的問題:

  * **重複計權** —— 分析單位先前是「一則新聞」,於是官方公告 + Reuters +
    台媒轉述會產生三個分析單位、三條因果鏈。`news_analyzed` 從 3 變 6
    看起來變深了,實際是同一條鏈改寫三次。
  * **覆蓋率的分母是模型自報的** —— `materiality` 由模型自己標,
    而驗證器只擋得住「一則都沒分析」。分析一則次要新聞就通過。
"""
import analysis_schema as sch
import evidence_packet as ep
import fixtures_analysis as fx
import news_clusters as nc

#: 2026-08-04 那類真實形狀:同一件事三家報、兩件不相干的事各一家。
_NEWS = [
    {"source_item_id": "n1", "title": "台積電熊本廠恢復至地震前水準",
     "entities": ["台積電", "2330"], "source": "公司公告", "official": True},
    {"source_item_id": "n3", "title": "台積電熊本廠已恢復地震前產出水準",
     "entities": ["台積電"], "source": "經濟日報"},
    {"source_item_id": "n7", "title": "台積電熊本廠恢復地震前的產出水準",
     "entities": ["台積電"], "source": "工商時報"},
    {"source_item_id": "n4", "title": "日月光取得營業用設備",
     "entities": ["日月光", "3711"], "source": "MOPS", "official": True},
    {"source_item_id": "n5", "title": "聯發科法說會下修全年展望",
     "entities": ["聯發科"], "source": "自由財經"},
]


# ---------------------------------------------------------------- 分群本身

def test_the_same_event_from_three_outlets_is_one_cluster():
    groups = {c["cluster_id"]: c for c in nc.clusters(_NEWS)}
    assert groups["cluster:n1"]["member_source_ids"] == ["n1", "n3", "n7"]
    assert groups["cluster:n1"]["size"] == 3
    assert groups["cluster:n1"]["official"] is True


def test_two_different_events_about_the_same_company_stay_apart():
    """**誤併比漏併危險** —— 它會讓一個真的事件被藏在另一個底下。

    實測的重疊度:同事件同語言 0.69/0.90、不同事件同主體 0.18。
    """
    pair = [{"source_item_id": "a", "title": "台積電法說會上修全年展望",
             "entities": ["台積電"]},
            {"source_item_id": "b", "title": "台積電董事會通過資本支出案",
             "entities": ["台積電"]}]
    assert len(nc.clusters(pair)) == 2


def test_a_shared_entity_alone_does_not_merge():
    """只有實體交集就併,「台積電」會把當天所有相關新聞併成一群。"""
    pair = [{"source_item_id": "a", "title": "台積電熊本廠恢復",
             "entities": ["台積電"]},
            {"source_item_id": "b", "title": "外資調升台積電目標價",
             "entities": ["台積電"]}]
    assert len(nc.clusters(pair)) == 2


def test_two_companies_doing_the_same_thing_are_not_one_event():
    """**實體交集是必要條件,不是加分項。**

    「聯發科法說會下修全年展望」與「瑞昱法說會下修全年展望」幾乎共用
    每一個詞 —— 只看標題就併的話,兩家公司的法說會會變成一件事,
    而其中一家的消息會整個消失在信裡。
    突變驗證抓到的:把實體檢查拿掉之後全套照樣綠。
    """
    pair = [{"source_item_id": "a", "title": "聯發科法說會下修全年展望",
             "entities": ["聯發科"]},
            {"source_item_id": "b", "title": "瑞昱法說會下修全年展望",
             "entities": ["瑞昱"]}]
    ta, tb = nc._tokens(pair[0]["title"]), nc._tokens(pair[1]["title"])
    assert len(ta & tb) / min(len(ta), len(tb)) >= nc.TITLE_OVERLAP,         "這組標題本來就該高度重疊,否則測不到實體檢查"
    assert len(nc.clusters(pair)) == 2, "不同公司的同類事件被併成一件"


def test_clustering_is_deterministic():
    """**同一份輸入的不同順序要得到同一個結果** —— 否則
    `evidence_sha` 會在無關的上游變動下抖動,而十配對建立在它上面。"""
    # **橋接形狀**:n1 與 n2 像(0.78)、n2 與 n3 像(0.56),
    # 而 n1 與 n3 不像(0.23)。貪婪合併對這種形狀**處理順序敏感** ——
    # 橋(n2)最後才處理時,n1 與 n3 已經各自成群,結果是兩群;
    # 橋先處理則是一群。少了排序,同一份輸入的不同順序會得到不同的群,
    # 而 `evidence_sha` 建立在它上面。
    bridge = [
        {"source_item_id": "n1", "title": "台積電熊本廠恢復地震前產出水準",
         "entities": ["台積電"]},
        {"source_item_id": "n3", "title": "台積電恢復正常出貨並上修展望",
         "entities": ["台積電"]},
        {"source_item_id": "n2", "title": "台積電熊本廠恢復正常",
         "entities": ["台積電"]},
    ]
    for case in (_NEWS, bridge):
        assert nc.clusters(case) == nc.clusters(list(reversed(case))),             "輸入順序改變了分群結果"
        assert nc.clusters(case) == nc.clusters(
            sorted(case, key=lambda n: n["title"])), "換一種排序就換一種答案"


def test_the_cluster_id_is_a_member_id_not_an_invented_number():
    """發明的編號明天會指到別的東西。"""
    for c in nc.clusters(_NEWS):
        assert c["cluster_id"] == f"cluster:{c['member_source_ids'][0]}"


# ---------------------------------------------------------------- 必分析清單

def test_the_denominator_comes_from_the_data_not_the_model():
    """判準是**官方來源**與**報導家數** —— 兩者都是資料說得出來的。"""
    req = nc.required_analysis(_NEWS)
    assert req["required_cluster_ids"] == ["cluster:n1", "cluster:n4"]
    assert "不採用模型自評" in req["coverage_basis"]
    # 聯發科那則只有一家報、非官方 —— 不在必分析清單裡(但仍可分析)
    assert "cluster:n5" not in req["required_cluster_ids"]


def test_a_required_event_must_be_analysed_or_explicitly_dismissed():
    """**靜默略過與判斷不重要,在信裡長得一模一樣。**"""
    pk = ep.build({}, {}, {}, _NEWS, [], {}, as_of="2026-08-05T06:00",
                  target_session_date="2026-08-05", sanitize=str)
    obj = fx.valid_analysis()
    obj["top_news_analysis"] = obj["top_news_analysis"][:1]
    obj["top_news_analysis"][0]["source_item_id"] = "n5"
    obj["top_news_analysis"][0]["relates_to"] = []
    hits = [p for p in sch.validate(obj, pk) if "既沒有分析、也沒有說" in p]
    assert len(hits) == 2, hits
    # 駁回要說得出理由
    obj["dismissed_events"] = [{"cluster_id": "cluster:n1", "why_not_material": "",
         "supporting_evidence_ids": ["n1"],
         "revisit_trigger": "官方後續公告改變原判斷"},
                               {"cluster_id": "cluster:n4", "why_not_material": "設備採購金額未達實質影響門檻",
         "supporting_evidence_ids": ["n4"],
         "revisit_trigger": "官方後續公告改變原判斷"}]
    probs = sch.validate(obj, pk)
    assert [p for p in probs if "cluster:n1] 沒有寫為什麼" in p]
    assert not [p for p in probs if "cluster:n4" in p]


def test_dismissing_something_outside_every_legal_set_is_rejected():
    """反向:**回填一個沒人要求的駁回**會讓「都處理過了」看起來成立。

    第二十四輪 P1-6:合法集合統一成 必分析 ∪ 計分前三 ∪ 總經發布 ——
    所以這裡改用一個**三個集合都不在**的事件群(此處是根本不存在的 ID,
    那正是最尖銳的版本:捏造 cluster_id 來充數)。
    """
    pk = ep.build({}, {}, {}, _NEWS, [], {}, as_of="2026-08-05T06:00",
                  target_session_date="2026-08-05", sanitize=str)
    obj = fx.valid_analysis()
    obj["dismissed_events"] = [
        {"cluster_id": "cluster:n99", "why_not_material": "不重要",
         "supporting_evidence_ids": ["n5"],
         "revisit_trigger": "官方後續公告改變原判斷"}]
    assert [p for p in sch.validate(obj, pk) if "必分析清單" in p]


def test_dismissing_a_top_event_that_is_not_required_is_legal():
    """**P1-6 的正向面**:計分前三但不在必分析清單的事件,駁回它是合法的。

    先前這裡自相矛盾:覆蓋率契約說「不在必分析清單就不准駁回」,而
    top-event 契約說「前三名每一件都要具名或駁回」—— 一個不在必分析清單的
    top-3 事件,模型兩邊都做不對。
    """
    import analysis_crosscheck as ac
    pk = ep.build({}, {}, {}, _NEWS, [], {}, as_of="2026-08-05T06:00",
                  target_session_date="2026-08-05", sanitize=str)
    required = set((pk["news_clusters"] or {}).get("required_cluster_ids") or [])
    top = list((pk.get("top_events") or {}).get("top_cluster_ids") or [])
    only_top = [c for c in top if c not in required]
    assert only_top, "fixture 需要一個「在前三、不在必分析」的事件群"
    assert set(only_top) <= ac.dismissable_cluster_ids(pk), "它必須可以合法駁回"

    obj = fx.valid_analysis()
    obj["dismissed_events"] = [
        {"cluster_id": only_top[0], "why_not_material": "與主線同一驅動,合併談",
         "supporting_evidence_ids": [only_top[0].split(":", 1)[-1]],
         "revisit_trigger": "官方後續公告改變原判斷"}]
    assert not [p for p in sch.validate(obj, pk) if "必分析清單" in p], (
        "計分前三的事件被駁回不該再被判成『不該駁回』")


def test_analysing_the_same_event_twice_is_rejected():
    """**那不是更深,是同一條因果鏈改寫兩次** —— 而它會讓
    `news_analyzed` 這個數字看起來變好。"""
    pk = ep.build({}, {}, {}, _NEWS, [], {}, as_of="2026-08-05T06:00",
                  target_session_date="2026-08-05", sanitize=str)
    obj = fx.valid_analysis()
    one = dict(obj["top_news_analysis"][0], source_item_id="n1", relates_to=[])
    two = dict(one, source_item_id="n3")
    obj["top_news_analysis"] = [one, two]
    obj["dismissed_events"] = [{"cluster_id": "cluster:n4", "why_not_material": "金額未達實質門檻",
         "supporting_evidence_ids": ["n4"],
         "revisit_trigger": "官方後續公告改變原判斷"}]
    hits = [p for p in sch.validate(obj, pk) if "被分析了 2 次" in p]
    assert hits and "cluster:n1" in hits[0], hits


def test_the_packet_carries_the_list_the_model_is_judged_against():
    """**不給清單而要求覆蓋,等於要模型猜驗證器在想什麼。**"""
    pk = ep.build({}, {}, {}, _NEWS, [], {}, as_of="2026-08-05T06:00",
                  target_session_date="2026-08-05", sanitize=str)
    assert pk["news_clusters"]["required_cluster_ids"] == ["cluster:n1", "cluster:n4"]
    import prompt_profiles as pp
    dev = pp.build_luna_bundle(pk)["developer_instructions"]
    assert "required_cluster_ids" in dev
    assert "dismissed_events" in dev
    assert "一個事件群只寫" in dev, "沒有告訴模型不要為同一件事寫兩段"


# ------------------------------------------------- 生產的呼叫形狀:實體從哪來

def _production_shape():
    """**抓取層真的會產出的欄位。**

    2026-08-17 查證:生產的新聞 dict **沒有 `entities`** —— 抓取層寫的是
    `company_label` / `cnyes_stocks` / `cnyes_keywords`。這份 fixture 刻意
    照抄那個形狀,而不是照抄本檔上面那份(那份直接給 `entities`,
    正是為什麼整條管線在測試裡是活的、在生產是死的)。
    """
    return [
        {"source": "鉅亨", "source_name": "鉅亨網",
         "title": "台積電法說會釋出樂觀展望 上調資本支出",
         "summary": "台積電表示 AI 需求強勁", "published": "2026-08-17T01:00",
         "company_label": "2330", "cnyes_stocks": ["2330", "2317"],
         "cnyes_keywords": ["台積電", "AI"]},
        {"source": "Google:2330", "source_name": "經濟日報",
         "title": "台積電法說會樂觀展望 上調今年資本支出",
         "summary": "法人看好", "published": "2026-08-17T02:00",
         "company_label": "2330"},
        {"source": "Google:2317", "source_name": "工商時報",
         "title": "鴻海 AI 伺服器出貨動能強",
         "summary": "營收創高", "published": "2026-08-17T03:00",
         "company_label": "2317"},
    ]


def test_editorial_tags_become_entities():
    """**生產的新聞沒有 `entities`,只有編輯人工標註。**

    先前 `news_normalize` 只照抄 `entities` —— 於是實體在生產永遠是空的,
    而 `_same_event` 要求實體有交集才併群:2026-08-17 生產 402 則新聞
    分成 402 群(等於完全沒分群)。
    """
    import news_normalize as nn
    items = nn.normalize_news(_production_shape())[0]
    by_title = {it["title"][:3]: it for it in items}
    assert by_title["台積電"]["entities"] == ["2317", "2330", "AI", "台積電"]
    assert by_title["鴻海 "]["entities"] == ["2317"], "只有 Google 標籤的也要有實體"


def test_the_same_story_from_two_publishers_merges_in_production_shape():
    """同一件事兩家報導 → 一群。**用生產的形狀驗**(上面那份 fixture
    直接給 `entities`,所以它證明不了生產會不會分群)。"""
    import news_normalize as nn
    items = nn.normalize_news(_production_shape())[0]
    cs = nc.clusters(items)
    assert len(cs) == 2, [(c["cluster_id"], c["member_source_ids"]) for c in cs]
    big = max(cs, key=lambda c: len(c["member_source_ids"]))
    assert len(big["member_source_ids"]) == 2


def test_a_generic_keyword_alone_does_not_merge_two_stories():
    """**編輯的主題詞也會是泛用詞**(「AI」)—— 只共用泛用詞、標題講不同
    的事,不得併群。併錯比不併更難查。"""
    import news_normalize as nn
    items = nn.normalize_news([
        {"source": "鉅亨", "source_name": "鉅亨網", "title": "台積電法說會上調資本支出",
         "summary": "", "published": "2026-08-17T01:00", "cnyes_keywords": ["AI"]},
        {"source": "鉅亨", "source_name": "工商時報", "title": "藥華藥新藥獲美國藥證",
         "summary": "", "published": "2026-08-17T02:00", "cnyes_keywords": ["AI"]},
    ])[0]
    assert len(nc.clusters(items)) == 2


def test_yesterdays_view_can_be_saved_from_production_shaped_news():
    """**縱向敘事的燃料。** `analysis_recap` 沒有實體就不存(接不回來的
    觀點是死重量)—— 2026-08-17 生產 `eligible 7 / items 0`,於是明天
    沒有「這件事昨天說過什麼」可比。"""
    import analysis_recap as arc
    import news_normalize as nn          # noqa: F401 - 走同一條正規化
    raw = _production_shape()[:1]
    pk = ep.build({"QQQ": {"close": 500.0, "change_pct": 1.0}}, {}, {},
                  raw, [], {}, as_of="x", target_session_date="y", sanitize=str)
    obj = fx.valid_analysis()
    obj["top_news_analysis"][0]["source_item_id"] = pk["news"][0]["source_item_id"]
    out = arc.extract(obj, pk)
    assert out["items"], f"eligible={out.get('eligible')} 卻一筆都沒存"
    # 2026-08-22 外審 P1:recap 的實體也走同一權威(組代表寫法);
    # 比對端本來就是別名感知(`entity_alias.expand`),存哪一種寫法都接得上。
    assert "台積電" in out["items"][0]["entities"]
