# -*- coding: utf-8 -*-
"""**Commit C:「昨夜三大重點」要是三個事件,不是三個價格變化。**

使用者看完 2026-08-05 那封信的原話:

> 我要的是真正國際上昨夜三大發生得重大事件 而不是數據文字堆疊

那天信件第一段寫的是 QQQ 漲 1.2%、台積電 ADR 跌 0.4%。價格變化是**別的
事件造成的結果** —— 它沒有主詞、沒有動作,而讀者想知道的是造成它的那件事。
"""
import analysis_schema as sch
import event_score as es
import evidence_packet as ep
import fixtures_analysis as fx
import news_clusters as nc


# ---------------------------------------------------------------- 什麼不是事件

def test_the_exact_headlines_the_user_complained_about_are_not_events():
    """使用者信裡看到的那幾行,逐字測。"""
    for t in ("美股四大指數收紅 那斯達克漲1.2%", "台積電ADR收跌0.4%",
              "台股開高走低 終場跌45點收23000點", "費城半導體指數收漲2.1%"):
        assert es.is_price_move(t), t


def test_an_event_that_happens_to_mention_a_price_is_still_an_event():
    """**判準是「有價格詞**而且**沒有事件詞」。** 少了後半,
    「輝達股價大漲 因公布財報優於預期」會被誤判成價格文 —— 而它是財報。"""
    for t in ("輝達股價大漲 因公布財報優於預期",
              "央行宣布調升存款準備率1碼 新台幣升值0.3%",
              "台積電法說會上修全年營收展望"):
        assert not es.is_price_move(t), t


def test_a_price_move_cluster_is_excluded_and_said_so():
    """**排除要說出來。** 靜默的排除讀起來像「今天沒有這種東西」。"""
    news = [{"source_item_id": "p0", "title": "台積電ADR收跌0.4%",
             "entities": ["台積電"], "source_name": "鉅亨網"},
            {"source_item_id": "r0", "title": "央行宣布調升存款準備率1碼",
             "summary": "新台幣升值0.3%", "entities": ["央行"],
             "source_name": "中央銀行", "official": True}]
    out = es.rank(nc.clusters(news), news)
    assert out["top_cluster_ids"] == ["cluster:r0"]
    assert out["excluded_price_moves"] == ["cluster:p0"]


# ---------------------------------------------------------------- 多軸計分

def test_the_axes_are_scored_separately_not_collapsed():
    """**單一「重要性」把好幾個問題壓成一個數字。** 五軸各自算得出來,
    而且權重是宣告的(改一個看得出來總分為什麼變)。"""
    news = [{"source_item_id": "c0", "title": "央行宣布調升存款準備率1碼",
             "summary": "新台幣升值0.3%,台股加權指數承壓", "entities": ["央行"],
             "source_name": "中央銀行", "official": True}]
    r = es.rank(nc.clusters(news), news)["ranked"][0]
    assert set(r["axes"]) == set(es.WEIGHTS)
    assert r["axes"]["corroboration"] == 1.0     # 官方公告封頂
    assert r["axes"]["locality"] == 1.0          # 台股/央行/新台幣
    assert r["axes"]["magnitude"] == 1.0         # 有帶單位的數字
    assert abs(sum(es.WEIGHTS.values()) - 1.0) < 1e-9


def test_an_official_local_event_outranks_a_narrow_foreign_one():
    news = [{"source_item_id": "c0", "title": "央行宣布調升存款準備率1碼",
             "summary": "新台幣升值0.3%,台股加權承壓", "entities": ["央行"],
             "source_name": "中央銀行", "official": True},
            {"source_item_id": "b0", "title": "某中小型IC設計廠公布營收月增3%",
             "summary": "法人看好下半年", "entities": ["某公司"],
             "source_name": "某小報"}]
    assert es.rank(nc.clusters(news), news)["top_cluster_ids"][0] == "cluster:c0"


def test_a_continuing_event_loses_novelty_but_not_everything():
    """第 5 天的追蹤稿新意較低,但**仍然可能是今天最重要的事**
    (戰事第 5 天的停火談判)—— 所以有地板,不是歸零。"""
    c = {"cluster_id": "cluster:x", "official": False,
         "independent_sources": 3, "continuing_days": 5}
    assert 0.3 < es.score_one(c, [])["axes"]["novelty"] < 0.5
    assert es.score_one(dict(c, continuing_days=0), [])["axes"]["novelty"] == 1.0


def test_ranking_is_deterministic_on_ties():
    """同分用 cluster_id 決勝 —— 靠輸入順序的話,同一天兩次執行會排出
    不同的三大重點。"""
    news = [{"source_item_id": s, "title": f"{s} 公司宣布擴產",
             "entities": [s], "source_name": "某報"} for s in ("z9", "a1", "m5")]
    first = es.rank(nc.clusters(news), news)["top_cluster_ids"]
    second = es.rank(nc.clusters(list(reversed(news))), news)["top_cluster_ids"]
    assert first == second == ["cluster:a1", "cluster:m5", "cluster:z9"]


# ---------------------------------------------------------------- 契約

def _packet():
    news = [{"source_item_id": "p0", "title": "台積電ADR收跌0.4%",
             "entities": ["台積電"], "source_name": "鉅亨網"},
            {"source_item_id": "n1", "title": "央行宣布調升存款準備率1碼",
             "summary": "新台幣升值0.3%,台股加權承壓", "entities": ["央行"],
             "source_name": "中央銀行", "official": True},
            {"source_item_id": "n2", "title": "b", "entities": ["c"],
             "source": "d"}]
    return ep.build({}, {}, {}, news, [], {}, as_of="x",
                    target_session_date="y", sanitize=lambda s: s)


def _drivers(obj, *cluster_ids):
    base = obj["key_drivers"][0]
    obj["key_drivers"] = [dict(base, cluster_id=c) for c in cluster_ids]
    return obj


def test_a_key_driver_may_not_point_at_a_price_move():
    """**這是使用者那條回饋的機械化。**"""
    hits = [p for p in sch.validate(
        _drivers(fx.valid_analysis(), "cluster:p0"), _packet())
        if "純價格變化" in p]
    assert hits, "三大重點指到價格文卻通過了"


def test_at_least_half_the_drivers_must_be_real_events():
    """留一格給非新聞的驅動因子(外資期貨部位)是合理的 ——
    三格**全部**不指向事件,就是「數據文字堆疊」。"""
    pk = _packet()
    bad = _drivers(fx.valid_analysis(), "cluster:n1", "", "")
    assert [p for p in sch.validate(bad, pk) if "指向真正的事件" in p]
    ok = _drivers(fx.valid_analysis(), "cluster:n1", "cluster:n2", "")
    assert not [p for p in sch.validate(ok, pk) if "指向真正的事件" in p]


def test_a_fabricated_cluster_id_is_caught():
    """編造的引用比沒有引用更危險 —— 它讓錯誤看起來有根據。"""
    assert [p for p in sch.validate(
        _drivers(fx.valid_analysis(), "cluster:不存在"), _packet())
        if "不在今天的事件群裡" in p]


def test_the_top_scored_event_cannot_be_silently_skipped():
    """其餘候選可以不談(版面有限),**第一名不談要說為什麼** ——
    靜默略過與判斷不重要,在信裡長得一模一樣。"""
    pk = _packet()
    top = pk["top_events"]["top_cluster_ids"][0]
    obj = _drivers(fx.valid_analysis(), "cluster:n2", "cluster:n2")
    assert [p for p in sch.validate(obj, pk) if "計分最高的事件" in p]
    obj["dismissed_events"] = [{"cluster_id": top,
                                "reason": "央行今日僅例行性調整,幅度低於市場預期區間",
                                "revisit_trigger": "若下次理監事會再升",
                                "supporting_evidence_ids": ["n1"]}]
    assert not [p for p in sch.validate(obj, pk) if "計分最高的事件" in p]


def test_the_contract_does_not_fire_without_a_packet():
    """舊呼叫端(只有 ID 集合)不判 —— **沒驗,不是驗過**。"""
    assert not [p for p in sch.validate(fx.valid_analysis(), fx.ids())
                if "三大重點" in p or "純價格變化" in p]


def test_the_prompt_tells_the_model_where_the_candidates_are():
    """規則要與程式同一件事 —— 模型沒被告知候選在哪,就只會反覆修補。"""
    import prompt_profiles as pp
    text = pp.LUNA_DEVELOPER_INSTRUCTIONS
    assert "top_events" in text and "cluster_id" in text
    assert "excluded_price_moves" in text
