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
    obj["dismissed_events"] = [{"cluster_id": "cluster:n1", "why_not_material": ""},
                               {"cluster_id": "cluster:n4",
                                "why_not_material": "設備採購金額未達實質影響門檻"}]
    probs = sch.validate(obj, pk)
    assert [p for p in probs if "cluster:n1] 沒有寫為什麼" in p]
    assert not [p for p in probs if "cluster:n4" in p]


def test_dismissing_something_that_was_never_required_is_rejected():
    """反向:**回填一個沒被要求的駁回**會讓「都處理過了」看起來成立。"""
    pk = ep.build({}, {}, {}, _NEWS, [], {}, as_of="2026-08-05T06:00",
                  target_session_date="2026-08-05", sanitize=str)
    obj = fx.valid_analysis()
    obj["dismissed_events"] = [{"cluster_id": "cluster:n5",
                                "why_not_material": "不重要"}]
    assert [p for p in sch.validate(obj, pk) if "不在本報的必分析清單" in p]


def test_analysing_the_same_event_twice_is_rejected():
    """**那不是更深,是同一條因果鏈改寫兩次** —— 而它會讓
    `news_analyzed` 這個數字看起來變好。"""
    pk = ep.build({}, {}, {}, _NEWS, [], {}, as_of="2026-08-05T06:00",
                  target_session_date="2026-08-05", sanitize=str)
    obj = fx.valid_analysis()
    one = dict(obj["top_news_analysis"][0], source_item_id="n1", relates_to=[])
    two = dict(one, source_item_id="n3")
    obj["top_news_analysis"] = [one, two]
    obj["dismissed_events"] = [{"cluster_id": "cluster:n4",
                                "why_not_material": "金額未達實質門檻"}]
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
