# -*- coding: utf-8 -*-
"""**第十九輪:合法不等於相關,而「看起來有做」比沒做更難察覺。**

這一輪的每一條都是同一個問題的不同面貌:

  * `market:USDTWD_prev` 的 ID 存在,**值卻是 `None`** —— 引用它的模型
    通過檢查,而檢查器根本不知道那個數字是多少。
  * 每個 market 欄位都掛著 packet 的 `as_of` —— **假精確**。QQQ 可能是
    前一個美股交易日、台指期是今天,而它們長得一模一樣。
  * 排在第 221 的央行公告不會進 packet、也不會成為必分析事件,
    而覆蓋率仍然 100% —— **分母一開始就把它排除掉了**。
  * 同一家媒體的三篇改寫稿被當成「三家同時報的重大事件」。
  * 同向訊號可以拿一則不相干的新聞當證據(矛盾那側早就不行)。
  * `asset_id="市場"` 會被 renderer 排得跟真的逐標的分析一模一樣。
  * 「事件 → 股價 → 稼動率 → 營收」指標記為順序不成立,而信裡什麼都不說。
"""
import analysis_depth as ad
import analysis_schema as sch
import analysis_stages as ast_
import evidence_packet as ep
import evidence_registry as er
import fixtures_analysis as fx
import news_clusters as nc
import schema_budget as sb


def _chain(*stages) -> dict:
    obj = fx.valid_analysis()
    steps, prev = [], "起點"
    for st in stages:
        nxt = f"{st}果"
        steps.append({"from_what": prev, "to_what": nxt, "channel": "傳導",
                      "stage": st, "step_type": "inference", "evidence_ids": []})
        prev = nxt
    obj["top_news_analysis"][0]["mechanism_steps"] = steps
    obj["top_news_analysis"][0]["materiality"] = "high"
    return obj


# ---------------------------------------------------------------- 證據圖

def test_a_block_that_is_itself_a_number_keeps_its_value():
    """**ID 存在而值不見了。** root scalar 的路徑是空字串,先前被
    `if path` 濾掉 —— `market:USDTWD_prev` 只剩一個 `value=None` 的殼。"""
    pk = ep.build({"USDTWD_prev": 29.88}, {}, {}, fx.news(), [], {},
                  as_of="2026-08-05T06:00", target_session_date="2026-08-05",
                  sanitize=str)
    assert er.registry(pk)["market:USDTWD_prev"]["value"] == 29.88


def test_the_registry_does_not_claim_per_field_observation_times():
    """**假精確比沒有 metadata 更糟。**

    每一格都掛 packet 的 `as_of` 時,模型會把不同交易日的數字當成
    同步的橫向訊號 —— 而它看起來完全有根據。
    """
    pk = ep.build({"QQQ": {"change_pct": 1.0},
                   "LAST_TRADING_SESSION": {"date": "2026-08-04"}},
                  {}, {}, fx.news(), [], {}, as_of="2026-08-05T06:00",
                  target_session_date="2026-08-05", sanitize=str)
    reg = er.registry(pk)
    assert reg["market:QQQ.change_pct"]["as_of_precision"] == "packet"
    assert reg["market:QQQ.change_pct"]["observed_session"] == "", \
        "美股側被安上了台股的交易日"
    assert reg["market:LAST_TRADING_SESSION.date"]["observed_session"] == "2026-08-04"
    # 新聞是唯一說得出自己時間的一類
    assert reg["n1"]["as_of_precision"] == "source"


# ---------------------------------------------------------------- 截斷順序

def test_an_official_event_ranked_last_still_reaches_the_packet():
    """**分母不能在截斷之後才算。** 排第 301 的央行公告先前直接消失,
    而必分析覆蓋率仍然顯示 100%。"""
    news = [{"source_item_id": f"m{i:03d}", "title": f"市場快訊{i}",
             "entities": ["台股"], "source": "同一家媒體",
             "published": "2026-08-05T05:00"} for i in range(300)]
    news.append({"source_item_id": "zzz", "title": "央行理監事會決議升息半碼",
                 "entities": ["央行"], "source": "中央銀行", "official": True,
                 "published": "2026-08-01T00:00"})
    pk = ep.build({}, {}, {}, news, [], {}, as_of="2026-08-05T06:00",
                  target_session_date="2026-08-05", sanitize=str)
    ids = {n["source_item_id"] for n in pk["news"]}
    assert "zzz" in ids, "官方公告被截斷擠掉了"
    assert "cluster:zzz" in pk["news_clusters"]["required_cluster_ids"]
    assert pk["truncation"]["required_forced_in"] >= 1
    assert len(ids) <= ep.MAX_NEWS_ITEMS, "強制保留把額度撐爆了"


def test_the_clusters_in_the_packet_only_list_news_that_survived():
    """模型引用不到被截掉的 ID —— 列出來只會誘發引用不存在的東西。"""
    news = [{"source_item_id": f"m{i:03d}", "title": f"快訊{i}",
             "entities": ["台股"], "source": f"媒體{i % 5}"} for i in range(300)]
    pk = ep.build({}, {}, {}, news, [], {}, as_of="x", target_session_date="y",
                  sanitize=str)
    kept = {n["source_item_id"] for n in pk["news"]}
    for c in pk["news_clusters"]["clusters"]:
        assert set(c["member_source_ids"]) <= kept


# ---------------------------------------------------------------- 分群判準

def test_three_rewrites_from_one_outlet_are_not_three_sources():
    """**改寫稿不是獨立證據。** 先前 `size` 數的是文章數。"""
    same = [{"source_item_id": f"n{i}", "title": "台積電熊本廠恢復地震前產出水準",
             "entities": ["台積電"], "source": "經濟日報"} for i in range(1, 4)]
    c = nc.clusters(same)[0]
    assert c["size"] == 3 and c["unique_sources"] == 1
    assert nc.required_analysis(same)["required_cluster_ids"] == []
    # 換成三家不同來源就成立
    diff = [dict(x, source=f"媒體{i}") for i, x in enumerate(same)]
    assert nc.required_analysis(diff)["required_cluster_ids"] == ["cluster:n1"]


def test_an_a_grade_wire_is_not_an_official_announcement():
    """**官方公告被漏掉是這類報告最實質的失誤** —— 判準本身不能先把
    「主管機關」與「A 級媒體」糊成同一格。"""
    c = nc.clusters([{"source_item_id": "a", "title": "台積電法說會紀要",
                      "entities": ["台積電"], "source": "Reuters",
                      "source_grade": "A"}])[0]
    assert c["official"] is False and c["has_grade_a"] is True


# ---------------------------------------------------------------- 相關性

def test_the_same_news_item_cannot_be_analysed_twice():
    """事件群層級的重複早就擋了,而**同一則**重複反而漏了。"""
    obj = fx.valid_analysis()
    one = dict(obj["top_news_analysis"][0], relates_to=[])
    obj["top_news_analysis"] = [one, dict(one)]
    assert [p for p in sch.validate(obj, fx.ids()) if "寫了 2 段" in p]


def test_a_generic_asset_id_is_not_a_breakdown():
    """`asset_id="市場"` 會被 renderer 排得跟真的逐標的分析一模一樣 ——
    **讓泛論看起來更像深度分析,比不拆更糟。**"""
    obj = fx.valid_analysis()
    obj["top_news_analysis"][0]["affected_assets"][0]["asset_id"] = "市場"
    assert [p for p in sch.validate(obj, fx.ids()) if "泛稱" in p]


def test_the_same_asset_cannot_appear_twice_in_one_event():
    obj = fx.valid_analysis()
    a = obj["top_news_analysis"][0]["affected_assets"]
    a[1]["asset_id"] = a[0]["asset_id"]
    assert [p for p in sch.validate(obj, fx.ids()) if "重複了" in p]


def test_an_alignment_must_cite_evidence_about_that_alignment():
    """矛盾那側早就要求引用該張力或兩側,**同向這側只要 ID 存在就過** ——
    於是「利率與科技股同向」可以拿一則航運新聞當證據。"""
    pk = ep.build({"QQQ": {"change_pct": 1.76},
                   "TAIFEX_OI": {"foreign_oi_net": 90038}},
                  {}, {}, fx.news(), [], {}, as_of="2026-08-05T06:00",
                  target_session_date="2026-08-05", sanitize=str)
    import tension_refs as tr
    need = sorted(tr.required_alignment_ids(pk["signal_tensions"]))
    assert need, "這份行情本來就該產生同向訊號"
    obj = fx.valid_analysis()
    obj["data_gaps"] = [{"gap_id": g, "what_is_missing": "x",
                         "impact_on_conclusions": "y"}
                        for g in tr.required_gap_ids(pk["signal_tensions"])]
    base = {"interpretation": "同方向", "marginal_information": "確認",
            "double_count_risk": "同一批權值股"}
    obj["cross_market_synthesis"]["alignment_readings"] = [
        dict(base, alignment_id=a, evidence_ids=["n1"]) for a in need]
    assert [p for p in sch.validate(obj, pk) if "沒有涵蓋這筆同向訊號" in p]
    obj["cross_market_synthesis"]["alignment_readings"] = [
        dict(base, alignment_id=a, evidence_ids=[a]) for a in need]
    assert not [p for p in sch.validate(obj, pk) if "沒有涵蓋這筆同向訊號" in p]


def test_a_boilerplate_dismissal_is_rejected():
    """**「影響有限」不是理由,是換句話說。**"""
    news = [{"source_item_id": "n1", "title": "央行理監事會決議",
             "entities": ["央行"], "source": "中央銀行", "official": True}]
    pk = ep.build({}, {}, {}, news, [], {}, as_of="x", target_session_date="y",
                  sanitize=str)
    obj = fx.valid_analysis()
    obj["top_news_analysis"] = []
    obj["dismissed_events"] = [{"cluster_id": "cluster:n1", "why_not_material": "影響有限",
         "supporting_evidence_ids": ["n1"],
         "revisit_trigger": "官方後續公告改變原判斷"}]
    assert [p for p in sch.validate(obj, pk) if "只是套語" in p]
    obj["dismissed_events"][0]["why_not_material"] = (
        "本次決議與上次一致,利率路徑沒有改變,不會改變今日的折現率假設")
    assert not [p for p in sch.validate(obj, pk) if "只是套語" in p]


# ---------------------------------------------------------------- 順序一致

def test_a_backwards_chain_is_disclosed_not_silently_accepted():
    """**兩個判準先前不一致**:指標記為順序不成立,而信裡什麼都不說。"""
    bad = _chain("event", "price", "operations", "revenue")
    stub = ast_.incomplete_chains(bad)
    assert stub and "因果順序不成立" in stub[0][1], stub
    assert ast_.depth_metrics(bad, None)["chains_out_of_order"] == 1
    assert ast_.incomplete_chains(_chain("event", "operations", "revenue")) == []


def test_the_letter_says_how_many_more_chains_are_incomplete():
    """先前只印前三則,**其餘靜默消失**。"""
    import analysis_render as ar
    obj = _chain("event", "sentiment")
    stub = dict(obj["top_news_analysis"][0])
    obj["top_news_analysis"] = [dict(stub, source_item_id=f"n{i}",
                                     relates_to=[]) for i in range(1, 6)]
    text = ar.render(obj)
    # User 2026-09-07: retain every backend diagnostic, hide the notes section.
    import analysis_stages as structure
    assert len(structure.incomplete_chains(obj)) == 5
    assert "傳導未完成" not in text and "另有 2 則同樣未完成" not in text


def test_dismissed_events_are_visible_to_the_reader():
    """**看過而決定不談,讀者有權知道** ——「沒發生」與「判斷不重要」
    在信裡先前長得一模一樣。"""
    import analysis_render as ar
    obj = fx.valid_analysis()
    obj["dismissed_events"] = [{"cluster_id": "cluster:n9", "why_not_material": "與上次決議一致,利率路徑未改變",
         "supporting_evidence_ids": ["n1"],
         "revisit_trigger": "官方後續公告改變原判斷"}]
    text = ar.render(obj)
    assert "今日看過但未展開" not in text
    # 2026-08-18:**識別碼不進信**。`cluster:n9` 對讀者是亂碼(使用者原話:
    # 「為何一堆亂碼」);有 packet 就換成那則新聞的標題,沒有就只寫理由。
    # 理由才是「為什麼不談」的答案,識別碼只是噪音。
    assert "cluster:n9" not in text, text
    assert "與上次決議一致" not in text
    assert "與上次決議一致" in obj['dismissed_events'][0]['why_not_material']


def test_scenario_triggers_reach_the_letter_but_probabilities_do_not():
    """觸發條件不是數字 —— 而信裡的**數字**必須是 Python 算的
    (既有不變式,情境機率沒有任何 Python 來源)。"""
    import analysis_render as ar
    obj = fx.valid_analysis()
    obj["scenario_tree"]["bear"]["triggers"] = ["十年期殖利率突破 4.6%"]
    obj["scenario_tree"]["bear"]["probability"] = 0.2
    text = ar.render(obj)
    assert "觀察條件：十年期殖利率突破 4.6%" in text
    assert "20%" not in text and "0.2" not in text, "情境機率進了信件"


# ---------------------------------------------------------------- 加深保護

def _shallow():
    o = fx.valid_analysis()
    o["top_news_analysis"][0]["mechanism_steps"] = \
        o["top_news_analysis"][0]["mechanism_steps"][:1]
    return o


def test_deepen_cannot_drop_the_alignment_readings():
    """第二版「更深」而同時刪掉橫向 —— **加深反而讓信變淺**。"""
    deep = fx.valid_analysis()
    shallow = _shallow()
    shallow["cross_market_synthesis"]["alignment_readings"] = [
        {"alignment_id": "tension:t_x", "interpretation": "i",
         "marginal_information": "m", "double_count_risk": "d",
         "evidence_ids": []}]
    ok, why = ad.deepen_is_an_improvement(shallow, deep, evidence_ids=fx.ids())
    assert not ok and "同向訊號" in why, why


def test_deepen_cannot_swap_out_a_section_claim_mapping():
    """**第二版完全合法,而立場改成靠另一條主張。**

    反例要只違反一條規則才測得到那一條 —— 把 `claim_ids` 清空的話,
    「stance 沒有回指任何 claim」會先擋下來,身分檢查根本沒被問到。
    """
    deep = fx.valid_analysis()
    deep["claim_audit"].append(dict(deep["claim_audit"][1], claim_id="c9",
                                    statement="另一條同尺度的主張"))
    # c1 的回指不見了,而立場的時間尺度仍然有主張撐著(c2/c9 都是 1-5d)
    deep["stance"]["claim_ids"] = ["c2", "c9"]
    deep["priced_in"]["claim_ids"] = ["c1", "c9"]
    assert sch.validate(deep, fx.ids()) == [], "第二版本身要是合法的"
    ok, why = ad.deepen_is_an_improvement(_shallow(), deep,
                                          evidence_ids=fx.ids())
    assert not ok and "回指" in why, why


def test_deepen_cannot_swap_out_an_analysed_asset():
    """**第二版仍然合法** —— 它照樣拆了標的,只是把指數換成別的東西。

    「至少一個標的」那條規則擋不住這種替換,而讀者失去的是
    昨天已經有的那一份拆解。反例要只違反一條規則。
    """
    deep = fx.valid_analysis()
    # 第二十九輪 F1 之後,ID-set 路徑也驗標的相關性 —— `3711` 不在 n1
    # (費半新聞)的證據裡,不再是「合法的第二版」。換成另一個指數
    # (相關性豁免),反例仍然只違反「不得換掉拆過的標的」那一條。
    deep["top_news_analysis"][0]["affected_assets"][1]["asset_id"] = "SOX"
    assert sch.validate(deep, fx.ids()) == [], "第二版本身要是合法的"
    ok, why = ad.deepen_is_an_improvement(_shallow(), deep,
                                          evidence_ids=fx.ids())
    assert not ok and "拆過的標的" in why, why


def test_deepen_cannot_drop_a_mechanism_evidence_reference():
    deep = fx.valid_analysis()
    deep["top_news_analysis"][0]["mechanism_steps"][0]["evidence_ids"] = []
    deep["top_news_analysis"][0]["mechanism_steps"][0]["step_type"] = "inference"
    ok, why = ad.deepen_is_an_improvement(_shallow(), deep,
                                          evidence_ids=fx.ids())
    assert not ok and "因果步驟的證據" in why, why


# ---------------------------------------------------------------- strict 預算

def test_the_schema_still_fits_the_provider_budget():
    """**超標時測試全綠、真實 API 整份拒收** —— 深度已經貼齊 10/10。"""
    assert sb.strict_budget_problems() == [], sb.strict_budget()


def test_the_budget_gate_actually_fires():
    """守衛自己要擋得住 —— 空集合真空通過是這個 repo 反覆栽的形狀。"""
    deep = {"a": {}}
    for _ in range(12):
        deep = {"a": deep}
    assert [p for p in sb.strict_budget_problems(deep) if "depth" in p]
