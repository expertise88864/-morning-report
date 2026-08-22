# -*- coding: utf-8 -*-
"""**同群鍵裡的每個版本號,都要對得上一份凍結的行為**(第十二輪 P2-2)。

## 問題

`COHORT_FIELDS` 用「語意契約版本」判定兩天的樣本可不可以相加 ——
那個方向是對的(git SHA 會讓一個 README commit 把十筆樣本清零)。
但那些版本號**全部靠人工維護**:改了 developer instructions、renderer 的
段落邏輯、stance 抽取、證據截斷或修補提示,卻忘了升版,樣本照樣被算進
同一群。每筆有 `prompt_sha` 可供事後追查,但**自動排除不會發生**,
而判讀的人只會看到一個混了兩種契約的平均值。

## 這個檔的做法

不是改用檔案雜湊 —— `llm_experiment` 的註解已經說明為什麼不:那會讓
「改一個註解」變成「換一個系統」。改成**凍結可觀測行為**:

    每個版本號 → 一份固定輸入下的輸出雜湊

內容變了、版本沒變 → 紅(該升版)。
版本變了、內容沒變 → 也紅(要嘛是誤升,要嘛是忘了更新這裡)。

這與本 repo 既有的 `test_deepseek_legacy_golden` 同一個做法,而那個檔已經
證明它抓得到東西。**雜湊不是「不准改」**:改是可以的,但要是一個刻意的、
看得見的動作 —— 升版號、更新這裡的常數、在 commit 說明改了什麼。

## 涵蓋範圍不是我挑的

應該有快照的版本欄位**從 `COHORT_FIELDS` 推導**,不是手寫一份清單 ——
否則新增一個版本欄位時,漏掉它不會有任何人發現(而漏掉的症狀正是這條
finding 描述的那種:混群而不自知)。
"""
import hashlib
import json

import fixtures_analysis as fx
import json_contract as jc

import analysis_grounding as gr
import analysis_validate as av
import analysis_render as ar
import analysis_depth as _ad
import analysis_schema as sch
import evidence_packet as ep
import llm_postprocess as lp
import prompt_profiles as pp

# ---------------------------------------------------------------- 固定輸入

#: 第十六輪:**固定輸入要撐得起被量的性質。** 先前這裡沒有任何會產生
#: `signal_tensions` 的資料,於是張力的形狀怎麼改,證據指紋都不動 ——
#: 那條判準對它自己該管的東西是真空通過的(與 legacy prompt 那次同型的洞)。
_QUOTES = {"^TWII": {"close": 23000.0, "change_pct": 0.8},
           "QQQ": {"close": 500.0, "change_pct": 1.2},
           "TAIFEX_OI": {"foreign_oi_net": -40000},
           "TAIEX_PRED": {"pred_pct": 0.5},
           "BREADTH": {"advance_ratio": 52.0},
           "MACRO": {"10Y": {"close": 4.50, "prev_close": 4.65}}}
_FAIR = {"fair_value": 22500.0}
_PRED = {"model1": 23100.0}
#: `n1` 的摘要與全文**刻意寫得夠長**:證據契約管的一部分是截斷長度
#: (`MAX_SUMMARY_CHARS` / `MAX_FULLTEXT_CHARS`),而輸入短於門檻時
#: 那些常數怎麼改都不會反映在快照上 —— 那條判準就成了真空通過。
_LONG = "台積電先進製程需求維持強勁,CoWoS 產能持續吃緊,客戶追加訂單。" * 40
_NEWS = [dict(n, summary=_LONG, fulltext=_LONG * 2) if n["source_item_id"] == "n1"
         else n for n in fx.news()]

_ANALYSIS = fx.valid_analysis()

#: 這段文字要**分辨得出後處理的行為**,不只是「跑得出答案」。
#: 第一版寫「淨分 +6」(有空格)、而且全文只有一個立場 —— 於是把容錯規則
#: 收窄、把段落錨點改掉,兩個突變都不會紅:快照形同虛設。現在:
#:   * 冒號形式的「淨分:+6」→ 少了容錯就抽不到分數;
#:   * 另一段裡放一個**相反**的立場當誘餌 → 段落錨點壞掉就會抽到它。
_REPORT_TEXT = ("## 七、昨夜三大重點\n- 空方觀點:立場:偏空(淨分:-9)\n"
                "## 我的明確立場\n立場:偏多(淨分:+6)\n"
                "理由:費半走強、量能回升。\n"
                "## 一句話總結\n維持核心部位,留意法說。")


#: grounding 的行為指紋要用**正反案例**:合格的要放行、各種不合格的要擋。
#: 少了反例,「全部放行」這個突變會隱形。
#: 全部保持 **schema 合法** —— 要量的是「根據」那一關,不是形狀那一關
#: (第十三輪 P2-3:兩關混在一起就分不出誰在作用)。
#: **手寫**的最小 packet。刻意不經過 `ep.build()` —— 經過的話,
#: evidence 契約一改,這裡就跟著動,而那正是要隔開的東西。
_FIXED_PACKET = {
    "schema_version": 0, "as_of": "2026-08-02T21:00",
    "target_session_date": "2026-08-03", "trading_session": "pre_open",
    "market": {"QQQ": {"change_pct": 1.0}},
    "news": [{"source_item_id": "n1", "title": "固定標題", "summary": "固定摘要",
              "source": "固定來源", "source_grade": "A", "official": False,
              "entities": ["台積電"], "published": "2026-08-02T20:00",
              "url": "", "fulltext": "", "summary_truncated": False,
              "fulltext_truncated": False}],
    "signal_tensions": {"checks_run": [], "unavailable": [], "items": []},
}


def _profile_view(bundle: dict) -> str:
    """profile 契約自己負責的部分:指令,加上 payload 的**框架**。"""
    return bundle["developer_instructions"] + "\x00" + bundle["user_payload"]


def _fabricated() -> dict:
    obj = fx.valid_analysis()
    obj["global_market"]["evidence_ids"] = ["n_fake"]
    return obj


def _split_quantifier() -> dict:
    """第二十二輪 P1-4:方向靠 c1、證據靠 c2 —— 合一判準要看得見它。"""
    obj = fx.valid_analysis()
    obj["key_drivers"][0].update(direction="bullish", evidence_ids=["n2"],
                                 claim_ids=["c1", "c2"])
    obj["claim_audit"][1].update(direction="bearish", evidence_ids=["n2"])
    return obj


def _horizon_two_tiers_off() -> dict:
    """第二十二輪 P1-5:立場宣告當日、主張全是 1-4 週 —— 算式版的
    `got >= want` 對這一格回 True,矩陣回 False。指紋要看得見這一側。"""
    obj = fx.valid_analysis()
    obj["stance"]["time_horizon"] = "intraday"
    for c in obj["claim_audit"]:
        c["horizon"] = "1-4w"
    return obj


_GROUNDING_CASES = [fx.valid_analysis(), fx.ungrounded_analysis(),
                    _fabricated(), _split_quantifier(),
                    _horizon_two_tiers_off()]


def _chain_probes(pk) -> list:
    """鏈的連續性:**上一步的終點有沒有指名過這一步的起點**(CI #495)。

    這條規則兩天內改了三次判準(逐字相等 → 一方包含另一方 → 重疊比例
    → 有沒有指名),而 grounding 指紋**一次都沒動** —— 它量不到自己
    該管的東西。正反各一格,而且用**會稀釋比例的長句**:那正是生產
    連三次誤擋的形狀,短句量不到。
    """
    prev = ("市場同時收到巴基斯坦「協議可能接近」的訊號，選擇相信談判："
            "油價收 82.5 美元、新興市場資產反彈")
    out = []
    #    油價與通膨預期=接走上一步列舉的其中一個結果(要放行);
    #    生技新藥…=完全不相干(要擋)。兩格只靠這一條規則分勝負。
    #    v35 再加三格:token 太少的斷鏈(要擋)、泛用詞交接(要擋)、
    #    短節點照抄(要放行)—— fail-open 拆掉之後,指紋要看得見這三條。
    for nxt in ("油價與通膨預期", "生技新藥三期試驗解盲解盲"):
        o = fx.valid_analysis()
        steps = o["top_news_analysis"][0]["mechanism_steps"]
        steps[0]["to_what"] = prev
        steps[1]["from_what"] = nxt
        out.append(sch.validate(o, pk))
    for prev2, nxt2 in (("需求", "毛利"),
                        ("市場需求轉弱", "市場資金回流"),
                        ("需求", "需求持續轉弱")):
        o = fx.valid_analysis()
        steps = o["top_news_analysis"][0]["mechanism_steps"]
        steps[0]["to_what"] = prev2
        steps[1]["from_what"] = nxt2
        out.append(sch.validate(o, pk))
    return out


def _asset_probes() -> list:
    """第二十二輪 P1-6 的標的判準 —— 概念詞黑名單、ASCII token 邊界、
    中文名要在證據裡。標準 packet 的新聞蓋不到這些邊角,指紋看不見
    這三條規則的存廢 —— 各給一個剛好只靠那條規則分勝負的標的。"""
    pk = ep.build({}, {}, {},
                  [{"source_item_id": "n1",
                    # 第二十六輪 P1-6:標題**帶著 Q2**。少了它,
                    # 期間詞那條規則會因為「Q2 不在證據裡」而被相關性
                    # 檢查順手擋掉 —— 指紋量到的是別條規則,不是這條。
                    "title": "Taiwan GPU demand accelerates as AMD ramps in Q2",
                    "entities": ["台積電", "AMD"], "source": "X"},
                   # 第二十六輪 P1-6:撞名代號的兩種寫法在同一則裡 ——
                   # `MTD` 有宣告過的別名(要放行),`TTM` 只是版面上
                   # 長得一樣的期間(要擋)。這條規則來回改了三次,
                   # 指紋要看得見它往任一邊漂。
                   {"source_item_id": "n2",
                    "title": "Mettler-Toledo (MTD) lifts guidance; "
                             "Apple (TTM) valuation at a record",
                    "entities": ["Mettler-Toledo", "Apple"], "source": "d"}],
                  [], {}, as_of="x", target_session_date="y",
                  sanitize=lambda s: s)
    out = []
    #    Ai/GPU=概念詞;MD=只有 token 邊界擋得住(藏在 AMD 裡);
    #    華碩=不在證據裡的中文名;台積電=真的在證據裡,要放行。
    #    Q2/FY25=會計期間(第二十六輪 P1-6):**它就在標題裡**,
    #    只有「永遠不是標的」那條規則擋得住;AMD 在同一則標題裡,
    #    確認新規則沒有把真代號一起掃掉。
    #    MTD=與期間縮寫碰撞的**真代號**(Mettler-Toledo):它該走
    #    「與這件事無關」那條訊息,不是「永遠不是標的」——
    #    黑名單一旦又長回裸縮寫,這一格的指紋就會動。
    for aid in ("Ai", "GPU", "MD", "華碩", "台積電", "Q2", "FY25", "AMD",
                "MTD"):
        o = fx.valid_analysis()
        o["top_news_analysis"][0]["affected_assets"][0]["asset_id"] = aid
        out.append(sch.validate(o, pk))
    # n2 那則同時有兩種寫法:`MTD` 是宣告過的公司(放行)、
    # `TTM` 在版面上長得一模一樣卻是期間(要擋)。
    for aid in ("MTD", "TTM"):
        o = fx.valid_analysis()
        o["top_news_analysis"][0]["source_item_id"] = "n2"
        o["top_news_analysis"][0]["affected_assets"][0]["asset_id"] = aid
        out.append(sch.validate(o, pk))
    return out


def _anchor_scope_probe() -> list:
    """第二十二輪 P2-1:帶主體的錨點要在這一段的範圍裡。
    講 2330 的鏈錨在 `universe:2317`(鴻海)上 —— 數字是真的,
    跟這條鏈沒有關係。**規則被拿掉時這個探針要動。**"""
    import analysis_stages as _ast
    reg = {"universe:2317.change_pct": {"value": 1.5},
           "universe:2330.change_pct": {"value": 1.5},
           "market:QQQ.change_pct": {"value": 1.5}}
    return [_ast.is_numeric_anchor(e, "n1", reg, subjects=s)
            for e in ("universe:2317.change_pct", "universe:2330.change_pct",
                      "market:QQQ.change_pct")
            for s in (None, {"2330"}, {"台積電"})]


def _top_event_probe() -> list:
    """Commit C:三大重點的事件契約。標準案例的 packet 裡沒有被排除的
    價格變化群,四條規則指紋一條都量不到 —— 給一個含價格文的 packet,
    再逐個違反方式各跑一次。"""
    news = [{"source_item_id": "p0", "title": "台積電ADR收跌0.4%",
             "entities": ["台積電"], "source_name": "鉅亨網"},
            {"source_item_id": "n1", "title": "央行宣布調升存款準備率1碼",
             "summary": "新台幣升值0.3%,台股加權承壓", "entities": ["央行"],
             "source_name": "中央銀行", "official": True},
            {"source_item_id": "n2", "title": "b", "entities": ["c"],
             "source": "d"}]
    pk = ep.build({}, {}, {}, news, [], {}, as_of="x",
                  target_session_date="y", sanitize=lambda s: s)
    out = [sorted(pk["top_events"]["top_cluster_ids"]),
           sorted(pk["top_events"]["excluded_price_moves"])]
    for cids in (("cluster:p0",), ("cluster:不存在",), ("cluster:n1", "", ""),
                 ("cluster:n2", "cluster:n2")):
        o = fx.valid_analysis()
        base = o["key_drivers"][0]
        o["key_drivers"] = [dict(base, cluster_id=c) for c in cids]
        out.append(sch.validate(o, pk))
    return out


def _event_graph_probe() -> list:
    """Commit D:淨效果、共同驅動、總經聯合情境。標準案例沒有方向衝突、
    沒有共同驅動群、沒有總經發布 —— 三條規則指紋一條都量不到。"""
    news = [{"source_item_id": "m1",
             "title": "美國7月非農就業新增18.5萬人 低於預期",
             "summary": "失業率升至4.3%", "entities": ["美國"],
             "source_name": "Reuters"},
            {"source_item_id": "m2", "title": "Fed 官員鴿派發言 暗示9月降息",
             "summary": "", "entities": ["聯準會"], "source_name": "CNBC"},
            {"source_item_id": "m3", "title": "台積電熊本廠恢復產線運作",
             "summary": "", "entities": ["台積電"], "source_name": "經濟日報"}]
    pk = ep.build({}, {}, {}, news, [], {}, as_of="x",
                  target_session_date="y", sanitize=lambda s: s)
    g = pk["event_graph"]
    out = [sorted(x["driver"] for x in g["shared_driver_groups"]),
           g["macro_release_cluster_id"],
           sorted((k, v["driver"]) for k, v in g["drivers"].items())]
    # 方向衝突但沒有淨效果 / 有淨效果但沒有衝突 / 共同驅動沒說明
    o = fx.valid_analysis()
    o["top_news_analysis"][0]["affected_assets"][0].update(
        asset_id="2330", direction="bullish")
    o["top_news_analysis"].append(dict(
        o["top_news_analysis"][0], source_item_id="n2",
        affected_assets=[dict(o["top_news_analysis"][0]["affected_assets"][0],
                              direction="bearish")]))
    out.append(sch.validate(o, pk))
    o2 = fx.valid_analysis()
    o2["asset_net_effects"] = [{"asset_id": "2330", "net_direction": "bullish",
                                "net_magnitude_band": "small",
                                "offsetting_cluster_ids": [], "why": "x",
                                "claim_ids": []}]
    out.append(sch.validate(o2, pk))
    o3 = fx.valid_analysis()
    base = o3["key_drivers"][0]
    o3["key_drivers"] = [dict(base, cluster_id=c)
                         for c in ("cluster:m1", "cluster:m2")]
    out.append(sch.validate(o3, pk))
    # 第二十六輪 P1-5:**兩側標籤齊全、證據卻同側。** 上面三個案例
    # 一個都碰不到這條規則 —— 它只在「標籤兩側、證據單側」時作用,
    # 而那正是它要擋的東西。放行版與被擋版都量,指紋才分得出存廢。
    out += [_side_grounding_case(["n1"], ["n1"]),
            _side_grounding_case(["n1"], ["n2"])]
    return out


def _side_grounding_case(ev_bull, ev_bear) -> list:
    """2330 有方向衝突、淨效果兩側各一條主張,只有**證據**不同。"""
    o = fx.valid_analysis()
    a = o["top_news_analysis"][0]
    a["affected_assets"][0].update(asset_id="2330", direction="bullish")
    o["top_news_analysis"] = [a, dict(
        a, source_item_id="n2",
        affected_assets=[dict(a["affected_assets"][0], direction="bearish")])]
    c0 = o["claim_audit"][0]
    o["claim_audit"] += [
        dict(c0, claim_id="cb", direction="bullish", asset_scope=["2330"],
             evidence_ids=list(ev_bull)),
        dict(c0, claim_id="cs", direction="bearish", asset_scope=["2330"],
             evidence_ids=list(ev_bear))]
    o["asset_net_effects"] = [{
        "asset_id": "2330", "net_direction": "bullish",
        "net_magnitude_band": "moderate",
        "offsetting_cluster_ids": ["cluster:m1", "cluster:m2"],
        "why": "產能恢復的量級大於降息預期的折現效果",
        "claim_ids": ["cb", "cs"]}]
    return sch.validate(o, ep.build({}, {}, {}, [], [], {}, as_of="x",
                                    target_session_date="y",
                                    sanitize=lambda s: s))


def _edge_packet() -> dict:
    """第二十二輪 P1-9/P2-3 的證據行為 —— 延續事件 token 邊界
    (US 不得命中 ASUS)、公司別名接上(台積電↔TSMC 併群且接上
    第 4 天)、國家/首都不再是別名(伊朗接不上德黑蘭)。"""
    return ep.build(
        {"EVENT_TIMELINE": [{"entity": "US", "days": 4},
                            {"entity": "台積電", "days": 4},
                            {"entity": "伊朗", "days": 4}],
         # v22:**昨日觀點掛上事件群**。台積電那兩群(e2/e3)要帶
         # yesterday_view;同日重跑守衛與別名比對被拿掉時,這一格會動。
         "ANALYSIS_RECAP": {"date": "2026-08-07", "items": [
             {"statement": "熊本廠復線對先進製程排程是正面",
              "direction": "bullish", "entities": ["台積電"]}]}},
        {}, {},
        [{"source_item_id": "e1", "title": "ASUS 財報優於預期",
          "entities": ["華碩"], "source": "甲"},
         {"source_item_id": "e2", "title": "TSMC 熊本廠恢復產線運作",
          "entities": ["TSMC"], "source": "乙"},
         {"source_item_id": "e3", "title": "台積電熊本廠恢復產線",
          "entities": ["台積電"], "source": "丙"},
         {"source_item_id": "e4", "title": "德黑蘭發生地震",
          "entities": ["德黑蘭"], "source": "丁"},
         # Commit B:**同集團與通訊社轉載的獨立性**。三則講同一件事,
         # 兩則是聯合報系(一個編輯台)、一則帶中央社署名 —— 字串去重
         # 會數到 3,獨立群組只有 2。規則被拿掉時這幾格要動。
         {"source_item_id": "e5", "title": "聯發科天璣新品發表會延期",
          "entities": ["聯發科"], "source": "經濟日報",
          "source_name": "經濟日報"},
         {"source_item_id": "e6", "title": "聯發科天璣新品發表會延期",
          "entities": ["聯發科"], "source": "聯合報", "source_name": "聯合報"},
         {"source_item_id": "e7", "title": "聯發科天璣新品發表會延期",
          "entities": ["聯發科"], "source": "自由時報",
          "source_name": "自由時報",
          "summary": "(中央社記者李四台北5日電)聯發科今日宣布…"},
         # v21:**跨語言數字錨點橋接 + 聚合器發布者浮出**。CNBC 的 $38B
         # 與經濟日報(藏在 Google 尾綴裡)的 383億美元要併成一群、
         # 獨立來源數 2 —— 橋接或尾綴解析被拿掉時,這幾格要動。
         {"source_item_id": "e8",
          "title": "SK Hynix to spend $38 billion on two new chip plants",
          "entities": ["SK Hynix"], "source": "CNBC", "source_name": "CNBC"},
         {"source_item_id": "e9",
          "title": "SK海力士砸383億美元建兩座新廠 - 經濟日報",
          "entities": ["SK海力士"], "source": "Google:半導體",
          "source_name": ""}],
        # v33:**universe 條目節點可引用**(`universe:2317`)—— 指紋要
        # 蓋得到這條規則,fixture 就要有一列 universe(先前給空清單,
        # 節點 ID 的存廢在指紋裡完全看不見)。
        [{"code": "2317", "name": "鴻海", "close": 187.5,
          "change_pct": -3.0}],
        {}, as_of="x", target_session_date="y", sanitize=lambda s: s)


def _sha(obj) -> str:
    blob = obj if isinstance(obj, str) else json.dumps(
        obj, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _packet() -> dict:
    return ep.build(_QUOTES, _FAIR, _PRED, _NEWS, [], {},
                    as_of="2026-08-02T21:00",
                    target_session_date="2026-08-03",
                    sanitize=lambda s: s)


def _render_case(pk: dict) -> dict:
    """渲染探針的固定輸入 —— **要示範 renderer 真的會做的事**。

    第十八輪:`fx.valid_analysis()` 沒有任何 `tension_resolutions`,
    高重要性事件的鏈也是完整的 —— 於是「逐筆張力抬頭」與「傳導未完成
    的揭露」兩段程式碼在快照裡**一行都跑不到**,改了 renderer 而指紋不動。
    探針量不到的東西,版本升降就只是在猜。
    """
    import signal_tensions as _st
    o = fx.valid_analysis()
    o["cross_market_synthesis"]["tension_resolutions"] = [
        {"tension_id": t, "resolution": "外部定價先反映在權值開盤",
         "dominant_side": "left", "why": "開盤前只有美股已定價",
         "decision_rule": "現貨量能與期貨空單是否回補",
         "evidence_ids": [t]}
        for t in sorted(_st.required_tension_ids(pk.get("signal_tensions")))]
    # 一條**停在情緒**的高重要性鏈 —— 揭露那一段才跑得到。
    o["top_news_analysis"][0]["mechanism_steps"] = [
        {"from_what": "費半收漲", "to_what": "市場關注提高", "channel": "情緒",
         "stage": "event", "step_type": "inference", "evidence_ids": []},
        {"from_what": "市場關注提高", "to_what": "投資情緒改善",
         "channel": "情緒", "stage": "sentiment", "step_type": "inference",
         "evidence_ids": []}]
    # **鏈的連續性判準要在指紋裡看得見**(外審 2026-08-17):第二則的
    # 第二步刻意用 schema 明說的「照抄再補充」形狀(上一步的終點完整
    # 出現在這一步的起點裡)。逐字相等的探針量不到這條規則 ——
    # renderer 改回逐字相等會把驗證過的連續鏈畫成斷鏈,而指紋不動。
    if len(o.get("top_news_analysis") or []) > 1:
        o["top_news_analysis"][1]["mechanism_steps"] = [
            {"from_what": "AI 需求", "to_what": "先進封裝產能擴充",
             "channel": "產能", "stage": "operations", "step_type": "fact",
             "evidence_ids": []},
            {"from_what": "先進封裝產能擴充（CoWoS 量產）",
             "to_what": "台積電營收上修", "channel": "營收",
             "stage": "financial", "step_type": "inference",
             "evidence_ids": []}]
    return o


# ---------------------------------------------------------------- 行為快照

#: 屬於**別的**契約版本的欄位 —— 混進來會讓一個契約的變動誤觸另一個的快照。
_OTHER_CONTRACTS = ("evidence_sha", "core_evidence_sha", "evidence_coverage",
                    "evidence_schema_version", "output_schema_version",
                    "truncation_summary", "estimated_input_tokens",
                    "user_payload", "prompt_sha", "response_schema")


def _contract_view(bundle: dict) -> dict:
    """這個 profile 契約自己負責的部分。"""
    return {k: v for k, v in bundle.items() if k not in _OTHER_CONTRACTS}


def _versionless(obj):
    """把**版本號本身**從行為指紋裡拿掉(r1 Codex)。

    packet 帶 `schema_version`、bundle 帶 `profile_version`,而 Luna 的
    user payload 內嵌整個 packet —— 於是「只升版、行為沒變」會讓雜湊跟著變,
    看起來像是行為也改了,而那正好讓「誤升版」那條判準抓不到東西。
    (`ANALYSIS_OUTPUT_SCHEMA` 實測不帶版本欄位,不受影響。)

    **指紋要量行為,不能把版本號量進去。**
    """
    if isinstance(obj, dict):
        return {k: _versionless(v) for k, v in obj.items()
                if not str(k).endswith("_version")}
    if isinstance(obj, list):
        return [_versionless(v) for v in obj]
    return obj


def _legacy_prompt() -> str:
    """生產真的會送給 DeepSeek 的那份 prompt(固定輸入)。

    與 `test_deepseek_legacy_golden` 釘的是同一個東西 —— 那個檔負責
    「改了要看得見」,這裡負責「改了就必須升版」。兩者角度不同,都要有。
    """
    import morning_report as mr
    return mr._build_prompt({"QQQ": {"close": 500.0}}, {"fair_value": 100.0},
                            {"model1": 1000.0}, _NEWS, [], "")


def _behaviour() -> dict:
    """每個契約版本**現在**的行為指紋。"""
    pk = _packet()
    bare = _versionless(pk)
    # **走生產的組裝器,只是餵去掉版本號的 packet。**
    # r1(Codex,pass 2):為了剝掉版本號,我一度改成直接呼叫
    # `luna_user_payload(bare)` —— 於是快照不再經過 `build_luna_bundle`,
    # 而生產送出去的正是它回傳的 `user_payload`。那個組裝器日後多包一層、
    # 多附一句 profile 專屬指令,快照都看不到。
    # 2026-08-05:profile 那一格改餵 `_FIXED_PACKET`(見下),
    # **evidence 那一格仍然走這條** —— 它本來就該隨 packet 變。
    return {
        # 第十七輪:evidence v4(遞迴 registry + 廣度方向/強度分離)、
    # schema v4(tension_resolutions + stage)、renderer v4(逐筆調和進信)、
    # grounding v5(深度提示再擴充)、Luna profile v10。
    # v15:加 `_edge_packet()` —— 標準 packet 蓋不到延續事件邊界與
    # 別名分群,那三個行為的存廢先前指紋看不見。
    "evidence_schema_version": _sha([bare, _versionless(_edge_packet())]),
        "output_schema_version": _sha(sch.ANALYSIS_OUTPUT_SCHEMA),
        # **profile 的指紋不該被證據契約牽動。** 餵 `luna`(由真實
        # `_packet()` 建的)時,evidence 加一個欄位就讓 prompt 契約亮紅 ——
        # 2026-08-05 又發生一次(`coverage` 加了 `raw_available`)。
        # 那種誤報會訓練出「看到紅就升版」的反射,真正的 prompt 變動反而
        # 混在裡面。改餵**手寫的固定 packet**:payload 的框架仍然量得到。
        "primary_profile_version": _sha(
            _profile_view(pp.build_luna_bundle(_FIXED_PACKET))),
        # legacy 契約管兩件事:prompt 的**內容**,以及它被怎麼包裝。
        # 2026-08-03:先前餵一段固定字串當 prompt,於是**真正的 prompt 改了、
        # 指紋卻不動** —— 那天升 `DEEPSEEK_LEGACY_VERSION` 時,「版本變了行為
        # 沒變」當場亮紅,而那個紅是對的:指紋涵蓋不到它自己該管的東西。
        # 現在餵**生產真的會送的那份**(`_build_prompt` 對固定輸入的輸出)。
        # prompt 內容**另外算一份**:`_contract_view` 排除了 `user_payload`
        # (那一格在 Luna 側屬於 evidence 契約),而 legacy 的 prompt 正好
        # 就住在那裡 —— 只餵真 prompt 而不把它算進去,指紋照樣不動。
        # 這是同一個洞的第二層:**改對了輸入,卻沒改到被雜湊的東西。**
        "fallback_profile_version": _sha([
            _versionless(_contract_view(
                pp.build_deepseek_legacy_bundle(pk, _legacy_prompt()))),
            _sha(_legacy_prompt())]),
        # 第二十四輪 P1-10:**探針先前量不到加深選優。** `_identity()` 決定
        # 哪一版會被發表,而它整個不在任何快照裡 —— 改了選優規則沒有任何
        # tripwire 會響。這正是本檔要防的形狀,只是漏了這一格。
        "postprocess_version": _sha([lp._extract_stance(_REPORT_TEXT),
                                     lp._extract_summary(_REPORT_TEXT),
                                     sorted(_ad._identity(_ANALYSIS).keys()),
                                     sorted(map(str, _ad._identity(
                                         _ANALYSIS).get("三大重點", ())))]),
        # **探針要用生產的呼叫形狀**(第十八輪)。先前餵 `render(obj)` ——
        # 而生產是 `render(obj, packet)`。於是逐筆張力的抬頭、傳導未完成的
        # 揭露,這些**只有在有 packet 時才存在的行為**,快照根本量不到:
        # 改了 renderer 而指紋不動,「版本升了行為沒變」就會誤報。
        # 這個 repo 已經栽過同一形狀兩次(legacy prompt 那兩層)。
        "renderer_version": _sha([ar.render(_ANALYSIS),
                                  ar.render(_render_case(pk), pk)]),
        # **接受契約要用正反案例量**(第十三輪 P1-3)。只餵合格輸入的話,
        # 把規則放寬到全部放行,雜湊照樣不變 —— 那種快照量不到「擋不擋」。
        # v3:接受政策含「深度加深」的觸發條件 —— depth_advisories 的行為
        # 也是契約的一部分(它決定要不要多跑一次、輸出分佈因此不同)。
        # 同上:**主閘門在生產吃的是 packet**,而這裡餵 ID 集合 ——
        # 於是「有張力卻沒處理」「重複調和」「證據沒涵蓋兩側」這些
        # packet-aware 的接受規則,快照一條都量不到。**兩種形狀都量**:
        # 舊呼叫端仍然合法,而新規則要看得見。
        # v18:案例加 split-quantifier、探針加標的判準 —— 合一判準與
        # 概念詞/token 邊界/中文名三條規則,先前的案例一條都量不到。
        "grounding_version": _sha([sch.validate(o, fx.ids())
                                   for o in _GROUNDING_CASES]
                                  + [sch.validate(o, pk)
                                     for o in _GROUNDING_CASES]
                                  # **生產傳的是 `(obj, packet)`**
                                  # (morning_report:14016)。少了 packet,
                                  # 範圍化錨點整條規則躲在
                                  # `registry is not None` 後面,指紋看不到
                                  # 它的存廢 —— 這個檔已經栽過同一形狀三次。
                                  + [av.depth_advisories(o, pk)
                                     for o in _GROUNDING_CASES]
                                  + _asset_probes()
                                  + _chain_probes(pk)
                                  + [_anchor_scope_probe()]
                                  + _top_event_probe()
                                  + _event_graph_probe()),
    }


#: `(版本欄位) → (版本號, 行為雜湊)`。**2026-08-02 於 f5645cd 量測。**
#:
#: 改了任何一個契約的行為時:升版號 **並且** 更新這裡的雜湊,在 commit
#: 說明改了什麼、為什麼。**不要為了讓測試變綠而改** —— 那等於把
#: 「這一群樣本不可比」這件事偷偷抹掉。
#:
#: 2026-08-03 更新四個雜湊,而版本號**維持 1**:原因是**固定輸入被修正**
#: (先前的 `_ANALYSIS` 不合乎 strict schema,見第十三輪 P2-3),
#: 不是契約行為改變。這是這張表少數該「改雜湊而不升版」的情形,
#: 所以理由寫在這裡,不是寫在 commit 就算。
#: 2026-08-03 第四次更新:兩個 profile 版本 2→3。r1 外審指出「維持 v2」
#: 這個決定**取決於「剛好沒有 v2 執行過」** —— 查證確實沒有(v2 未推、
#: 帳本不存在),但規則不該建立在那種查證上。prompt 位元組變了就換版本;
#: `prompt_sha` 只是溯源、不在同群鍵裡,靠它事後分辨等於讓混群先發生。
#: 雜湊不變是正確的:內容在上一步就已經改完,這一步只補版本號。
#:
#: 2026-08-03 第三次更新:r1 外審抓到「用全形」那條規則**自己用半形舉例**,
#: 而且整份指令通篇半形 —— 模型模仿它看到的東西,示範會蓋過規則。
#: 兩份 prompt 的中文散文都做了保守的全形轉換(格式模板不動)。
#: 版本仍是 2:這是同一次風格變更的修正,不是另一次變更。
#:
#: 2026-08-03 第二次更新:兩個 profile 的**風格**依使用者回饋改成敘事寫法 +
#: 全形標點,那會改變輸出,所以兩個版本號都升到 2(不是只改雜湊)。
#: 同時修好一個漏洞:shadow 指紋原本餵一段固定字串當 prompt、而且
#: `_contract_view` 又把 `user_payload` 排除掉 —— **真正的 legacy prompt
#: 改了,指紋卻兩層都攔不到**。現在 prompt 內容另外算一份算進去。
_FROZEN = {
    # v2(第十五輪 P2-1):packet 加 signal_tensions —— 橫向矛盾由 Python
    # 先算好(附數字與門檻出處),模型從「找矛盾」變成「解釋矛盾」。
    # v3(第十六輪):張力改純觀測(left/right/relationship/tension_id/
    # usable_for_inference);registry 改 typed。固定輸入同時補上會產生
    # 張力的行情 —— 先前它撐不起這個性質,指紋對自己該管的東西真空通過。
    # v5(第十八輪 P1-4):利率×科技改用**象限名**(先前的 `same_direction`
    #     內建了「折現率下行有利成長股」這條假說);衍生值改掛 `derived:`
    #     並帶來源 —— `market:MACRO.10Y.change_bps` 那個 packet 裡不存在的
    #     路徑不再是合法引用。
    # v6(第十八輪 P1-1/P1-2/P1-8):registry 擴到整個 packet 並帶 metadata
    #    (值/單位/時間/來源/能不能推論);`US_HOLIDAY` 進 packet ——
    #    先前張力看得到美股休市而 registry 看不到,同一天兩個真相;
    #    新增 `required_disclosures`(今天哪幾項沒有答案)。
    # v7(第十八輪 P1-3):`news_clusters` —— 同一件事的多家報導併成一群,
    #    並由官方來源與報導家數選出必分析清單(不採用模型自評的重要性)。
    # v8(第十九輪):root scalar 的值不再掉在空 path;`as_of_precision`
    #    與 `observed_session` 取代假精確;新聞先分群、必分析事件強制
    #    保留、再截斷(先前排第 221 的央行公告直接消失而覆蓋率 100%)。
    # v9(第二十輪 P1-2/P1-7):分群改與**代表**比對(single-link 會被
    #    橋接串起來,兩件事壓成一群);observed_session 改逐區塊政策
    #    (先前「非美即台」,公報與匯率被掛上台股交易日)。
    # v10(深度加強第二批):每則新聞抽帶單位的數字成 `fact:` 命名空間
    #     (值/單位/上下文進 registry —— 抄錯十倍終於抓得到);
    #     同源改版重發去重;事件群帶 corroboration 等級。
    # v11(2026-08-05 實機 + 第二十輪 P2-3):`coverage` 的分母改成
    #     **去重後**的可用數(一家重發十次時,去重成功先前顯示成
    #     「涵蓋不足」);原始數另外報。
    # v12(第二十輪 P2-1):事件群代表改選「官方 > 資訊量高 > 最小 ID」
    #     —— 最小 ID 會確定性 over-split(短而模糊的標題當代表)。
    # v13(深度優化第三批):事件群標 `continuing_days`(EVENT_TIMELINE
    #     的第 N 天接到分析單位上 —— 延續事件要寫增量不是重述)。
    # v14(第二十一輪):裁切標記不再留在 market(P1-3,先前會變成
    #     量化錨點);trim 改依實際大小;被裁區塊變成必揭露缺口;
    #     cluster 帶 representative 並在截斷時保留(P1-7);
    #     延續事件用實體別名接上(P2-8);fact 跨 title/summary 去重(P2-2)。
    # v15(第二十二輪 P1-9/P2-3):延續事件標題比對改 token 邊界
    #     (US 不再命中 ASUS);別名表整批拿掉國家/首都、只留公司與 Fed;
    #     分群交集吃別名(台積電/TSMC 併群)。探針同批加 `_edge_packet()`。
    # v16(重構規格 Commit B):事件群帶獨立性(已驗證 / 可能 / 未驗證)。
    #     「三家報導」與「三個獨立來源」是兩個數字:同集團轉載與通訊社
    #     稿件只算一個編輯決策。`_edge_packet()` 同批加聯合報系兩則 +
    #     一則中央社署名的轉載 —— 兩個突變(不看署名、每個字串各自一組)
    #     都實測讓指紋移動。
    # v17(Commit C):packet 帶 `top_events` —— 多軸計分的三大重點
    #     候選、被排除的純價格變化群、權重宣告。
    # v18(Commit D):packet 帶 `event_graph` —— 共用底層驅動的事件群
    #     (就業→降息預期→殖利率是同一件事的三個表現)、總經發布。
    # v19(Commit E):正規化保留 `source_name`;近似去重改用發布者
    #     當鍵 —— 先前用聚合器別名,同一個 Google 查詢帶回的三家
    #     媒體被判成「同一家改版重發」而砍掉兩則。
    # v20(第二十三輪):macro_release_cluster_ids、來源別名 token 邊界、
    #     未知來源以發布者字串去重。
    # v21(深度優化):跨語言數字錨點橋接 + 聚合器發布者浮出 + 分群剝
    # 發布者尾綴。edge packet 補了 e8/e9(CNBC $38B ↔ 經濟日報 383億美元)
    # —— 先前的探針蓋不到這兩個行為,指紋不動;**探針量不到的東西,
    # 版本升降就只是在猜**(這一格的既有教訓,同一句話再驗一次)。
    # v22(分析面縱深):事件群帶 `yesterday_view`(昨日觀點閉環)。
    # v23(外審補審):timeline 記錄整筆帶著走、yesterday_view 加事件層
    # 比對、跨語言橋接要事件類別一致。
    # v24(縱深第四批):`story_arcs` 接進 packet(線索帳本先前只餵 legacy)
    "evidence_schema_version":  (33, "5dacf45c472fcebd"),
    # v2(schema v2):top_news_analysis 加因果鏈/量級/關係;新增
    # cross_market_synthesis。prompt 叫模型深入而 schema 沒地方放,
    # 是使用者三次「堆疊數據」回饋在結構層的根因(第十五輪 P1-1)。
    # v3(第十六輪 P2-2):`addressed_tension_ids` + `priced_in.evidence_ids`。
    # v5(第十八輪 P1-8):`data_gaps[].gap_id` —— 缺口要對得上是哪一項。
    # v6(第十八輪 P1-3):`dismissed_events` —— 駁回必分析事件要留理由。
    # v7(第十八輪):`affected_assets`(同一件事對不同標的可以相反)、
    #    `claim_id` + 各段 `claim_ids`(閉合 claim 圖)、`alignment_readings`。
    # v8(第十九輪 P1-8):`claim_audit.asset_scope` 與頂層
    #    `executive_summary_claim_ids` —— 最可能被單獨閱讀的那一段
    #    先前完全脫離稽核。回指放頂層是**攤平**(深度已貼齊上限)。
    # v9(第二十輪 P1-6/P2-2):scenario 與 watch_triggers 接進 claim 圖
    #    (最前瞻的判斷不能是唯一不用根據的段落);dismissed_events 加
    #    revisit_trigger 與 supporting_evidence_ids。
    # v10(第二十輪 P1-5/P2-7):`key_drivers[].claim_ids`(Email 第一段
    #     先前完全在 claim 圖之外)、`corroboration_assessment` 與
    #     `source_caveat`(單一來源的揭露改成機械契約)。
    # v11(Commit C):`key_drivers[].cluster_id` —— 三大重點要指名它
    #     講的是哪一個事件群(價格變化沒有主詞也沒有動作)。
    # v12(Commit D):`asset_net_effects`(方向相反的標的要給淨方向 ——
    #     使用者要的是「合起來是利多還是利空」)、`shared_driver_notes`。
    # v20(2026-08-19):`taiwan_policy` —— legacy 的「台灣本地動態」在
    #     特化 schema 沒有對應欄位,那一段整個消失(使用者連兩天反映)。
    # v21(2026-08-19 第四批):world_events / 48h 情境 / 敘事變化 /
    #     多空交鋒 / 總經環境 / 在地動態 / primary_target。
    "output_schema_version":  (22, "ab61f586889ba2e0"),  # v22 敘事/總經證據契約(2026-08-19 P1-B)
    # v4(2026-08-03 晚):可讀性三修——全中文轉述、術語白話化、數字要有下文。
    # v5(2026-08-04):Python 排好的表要被合起來解讀(R17)、七之二要寫得出傳導路徑。
    # v6(2026-08-04 二次):方向形容詞不是分析——量級/時間取代方向詞、
    # 至少兩條跨條連結、句式不得雷同;兩個範例整個重寫(它們自己在示範那個毛病)。
    # v7(schema v2):新欄位的填法指引(unknown 是誠實不是失敗、
    # 編造的關聯比沒有關聯更糟、五個市場各寫一句不是綜合)。
    # v8(第十五輪 P2-1):要求逐條正面處理 signal_tensions 的每個 tension。
    # v9(第十六輪):張力純觀測、typed 引用 ID、回填 addressed_tension_ids。
    # v11(第十八輪):證據引用改口徑 —— 先前開頭寫「帶上支持它的
    #      `source_item_id`」而後段才說行情用 `market:*`,前後矛盾。
    # v12(第十八輪):九個命名空間的用法、逐項揭露 `required_disclosures`、
    #      不同步的欄位不得單獨支撐高重要性判斷。
    # v13(第十八輪 P1-3):一個事件群只寫一個分析單位;
    #      必分析清單要嘛分析、要嘛說明為什麼不談。
    # v14(第十八輪):三條新規則(逐標的、同向解讀、claim 回指)。
    # 第十九輪:**探針輸入被修正,契約本身沒變** —— 先前餵
    #    `build_luna_bundle(_packet())`,而 payload 內嵌整個 packet,
    #    於是 evidence 加一個欄位就會讓 prompt 契約亮紅。改餵手寫的
    #    固定 packet。依本表既有先例:**改雜湊而不升版**。
    # v15(第十九輪):asset_scope、總結回指、時間尺度要連對。
    # v16(第二十輪+深度加強):量化錨點、橫向接行情、駁回的回頭條件。
    # v17(深度加強第二批):新聞數字用 fact: 引用;單一來源要明講。
    # 2026-08-05:**探針輸入被修正,prompt 本身沒變**
    #    (dev 指令與 payload 框架逐位元組相同,已實測)。
    #    依本表既有先例:改雜湊而不升版。
    # v18(第二十輪 P2-6):命名空間與量化錨點的說明改由
    #      `evidence_namespaces` 單一宣告生成(先前三邊各說各話);
    #      key_drivers/情境/觀察點也要回指;佐證等級照抄不自評。
    # v19:`calibration:` 與 `quality:` 不再列為量化錨點 ——
    #      它們是關於**本報自己**的數字(校準、涵蓋度),
    #      不是市場量級。用它們錨住因果鏈是把儀表板當證據。
    # v20(2026-08-05 使用者七項回饋):三大重點要「事件」不是行情、
    #      七之二與八/九段的四個必答問題、內部試算不進信、
    #      政策取材以中彰投雲為主。
    # v21(深度優化第三批):continuing_days > 1 的事件寫增量。
    # v22(Commit C):三大重點的規則(候選由 top_events 給、至少一半
    #     要指到真事件、行情數字用來說明量級)。
    # v23(Commit D):淨效果、共同驅動、總經聯合情境三段規則。
    # v24(第二十三輪):每條重點都要是事件、前三全處理、多總經發布。
    # v25(2026-08-08 生產):命名空間說明改成指得到真 ID 的樣子 ——
    # `prediction:` 不帶標的段、加權在 `market:TAIEX_PRED.*`、
    # `calibration:` 舉出實際欄位。模型先前照錯的說明猜名字,五條引用
    # 被判不存在,整份特化分析作廢退回舊路徑。
    # v26(分析面縱深):延續事件的敘述要相對 `yesterday_view` 定位
    # (強化/轉弱/翻轉),且不得引用它替今天背書。
    # v28(縱深第四批):多日軌跡的線索寫成發展;狀態不得改判、脈絡不是證據
    # v38(2026-08-19):條數目標六到十則、非科技至少一到兩則、
    #     `taiwan_policy` 欄位說明。
    "primary_profile_version":  (43, "0bda83c2cd716692"),  # v43 2026-08-22 使用者:類股加量(科技≥6/其他≥5)+術語首次出現要括號解釋  # v42 金融龍頭+5(2026-08-22)
    # v7:同一批(legacy 與 Luna 共用 `writing_rules`)。
    # v8(2026-08-20):其他類股新增「金融-金控」標籤,固定輸入下 prompt
    # 多一節空素材;指示文字沒動(diff 只有三行,見 legacy golden 的說明)。
    "fallback_profile_version":   (11, "b12689a6fccb34dd"),  # v11 金融龍頭+5(2026-08-22)
    # v2(第二十四輪 P1-10):加深選優的身分補上四段可見欄位;
    # 探針同時補上 `_identity`(先前完全量不到選優規則)。
    # v8(2026-08-19):taiwan_policy 的引用檢查。
    "postprocess_version":      (10, "ff37668d050219a1"),  # v22 形狀
    # v2(2026-08-04,第十五輪 P1-2/P1-3):段落語意映射修正 + 補上先前
    # 整段丟掉的 priced_in / falsification_trigger / counterevidence /
    # actions_to_consider。**渲染層丟資料時模型再深入也沒用。**
    # v3(schema v2):因果鏈/量級/驗證與失效/關係 + 橫向綜合段。
    # 第十六輪:renderer **契約沒變**,是固定輸入補了 priced_in 內容與
    # addressed_tension_ids(fixture 要示範新欄位長什麼樣)。依本表既有先例
    # (2026-08-03 那次同理):輸入被修正時**改雜湊而不升版**,理由寫在這裡。
    # v5(第十八輪):逐筆張力印出**它在調和什麼**(topic 與兩側數值,
    #    由 renderer 從 packet 回查);高重要性事件的傳導沒走完時揭露。
    #    **探針同批修好** —— 先前餵 `render(obj)` 而生產是 `render(obj, packet)`,
    #    新行為在快照裡一行都跑不到。
    # v6(第十八輪):逐標的影響與同向解讀排進信。
    # v7(第十九輪):情境觸發條件(機率仍不進信 —— 信裡的數字必須是
    #    Python 算的)、駁回事件、未完成鏈的剩餘則數。
    # v8(第二十輪 P2-2):駁回超過 4 件顯示「另有 N 件」。
    # v9(第二十輪 P2-7):單一來源/未證實的佐證等級與保留事項固定呈現。
    # v10(Commit C):`key_drivers` 多了 `cluster_id`。
    # v11(Commit E):事件卡 + 各標的合計影響 + 共用驅動說明進信。
    # v12(第二十三輪):三大重點依 Python 計分排序;aggregator-only
    #     寫「原始發布者未解析」。
    # v14(2026-08-17 使用者定案:敘事為主):特化路徑第一次在生產成功
    #     那天,使用者回「敘述方式變成這樣,原本的還比較好」。機械欄位
    #     (claim_type/信心%/量級/確認訊號/與另一則的關係/來源說明文字)
    #     收起來;留下判斷、失效條件、傳導鏈一行、逐標的影響一行,以及
    #     句尾的誠實性括號(來源、反面證據、追蹤天數)。**欄位仍在
    #     schema 裡被要求與驗證**,只是不再排進讀者視線。
    # v15(2026-08-18 使用者定案):第八段回到「哪間公司昨天發生什麼事」
    #     的小標題寫法,依產業拆科技/其他兩個子段;逐標的的方向、
    #     幅度、時間窗整組拿掉(合計後在「各標的合計影響」出現一次),
    #     一階/二階影響與〔推測性傳導〕揭露留著。
    #     外審同批:小標題與敘述之間補空行(逐行的 md→html 會把相鄰
    #     非空行併成同一個 <p>),候選順序改成一個候選問完兩張表。
    # v16(2026-08-18 第二批):小標題只寫公司、新聞寫在下一段;五段併成
    #     「九、今日市場關注與預測」且排在第八段之後;保留事項的內部
    #     識別碼換成新聞標題。
    # v17(2026-08-18 第三次校正):公司/新聞/分析同一段、側寫、發布者、
    #     `[A 級・信心:中]`。
    # v18(2026-08-19 第三批):主體要被標題指名、逐則散文、七段收掉
    #     失效條件、市場段整段刪除、新增台灣政策段。
    # v19(2026-08-19 第四批):legacy 骨架全回。
    "renderer_version":         (19, "64255eb3e87ff07b"),
    # v2(schema v2):cross_market_synthesis 進 RENDERED 與 EVIDENCE_BEARING。
    # v3(第十五輪):接受政策加「合法但淺 → 用剩餘額度加深一次」;
    # 指紋納入 depth_advisories 的行為。
    # v4(第十六輪 P2-4):`priced_in` 也要帶證據(高推論性判斷更需要根據)。
    # v6(第十八輪):接受規則加三條 —— 重複的張力調和、調和的證據沒有
    #    涵蓋兩側、以及**主閘門改吃 packet**(先前生產傳 ID 集合,
    #    packet-aware 的規則一條都沒跑過)。探針同批改成兩種形狀都量。
    # v7(第十八輪 P1-8/P1-2):逐 gap 揭露(先前只要 data_gaps 非空就過,
    #    於是一筆無關的缺口能替所有跑不成的檢查過關);高重要性判斷
    #    不得只靠標為不同步的證據。
    # v8(第十八輪 P1-3):必分析事件的覆蓋率;同一事件群不得分析兩次。
    # v9(第十八輪):高重要性事件要拆出標的;同向訊號逐筆解讀;
    #    各段要回指 claim,而高重要性的孤兒主張不算根據。
    # v10(第十九輪):同一則新聞不得寫兩段;標的不得是泛稱或重複;
    #     同向訊號的證據要綁在那一筆上;駁回理由不得是套語。
    # v11(第十九輪 P1-8):回指要**連對**不只是連上 —— 立場的時間
    #     尺度要有同尺度的主張撐著;asset_scope 不得是泛稱或留空。
    # v12(第二十輪):情境/觀察點要回指;駁回要引用被駁回那群自己的
    #     新聞並給回頭條件;段落內重複回指要擋;完整鏈=全程不倒退;
    #     深度加強:量化錨點與橫向接行情(advisory,不擋信)。
    # v13(深度加強第二批):量化錨點 advisory 接受 `fact:`。
    # v14(第二十輪 P1-3):量化錨點改用 `is_numeric_anchor` ——
    #     要是**這則新聞自己的、真的是數字的、今天可用的**證據。
    # v15(第二十輪 P1-5/P2-5/P2-7):段落→主張的對照表改由 `claim_map`
    #     生成(四個消費者共用一份);時間尺度要有主張撐得住;
    #     佐證等級不得往上寫、單一來源要有 caveat。
    # v16(深度優化第三批):大寫字母的標的要是**該則新聞的實體** ——
    #     字串格式分不出「代號」與「概念」,證據分得出(AMD 在 AMD
    #     新聞的 entities 裡;GPU 不會是任何新聞的實體)。
    # v17(第二十一輪):key_driver 要與引用的 claim 方向同向且證據
    #     有交集(P1-5);horizon 訊息與程式同向(P1-6);佐證 caveat 的
    #     布林錯誤(P2-4);標的判準改「證據裡的人」不看大小寫(P1-9)。
    # v18(第二十二輪):key_driver 判準合一 —— **同一條 claim** 要同時
    #     同向且共享證據(P1-4 split-quantifier);標的判準加概念詞
    #     黑名單、ASCII token 邊界、中文名也要在證據裡(P1-6)。
    #     案例加 `_split_quantifier()`、探針加 `_asset_probes()`。
    # v19(第二十二輪 defer 三項):horizon 改宣告式矩陣(相鄰一階以內
    #     才相容,「這個月看多」推不出「今天會漲」);帶主體的錨點要在
    #     該段範圍裡;加深選優改用與觸發同一套判準(`depth_advisories`
    #     在選優裡先前收不到 packet)。探針同批改成**生產的呼叫形狀**
    #     `depth_advisories(o, pk)` —— 範圍化錨點整條規則躲在
    #     `registry is not None` 後面,盲測的探針量不到它。
    # v20(Commit C):接受政策多了三大重點的事件契約 —— 指到被排除的
    #     價格變化群、指到不存在的群、真事件不到一半、計分最高的
    #     被靜默略過。`_top_event_probe()` 讓指紋看得見這四條。
    # v21(Commit D):方向衝突要給淨效果、共用驅動要說明為什麼不算
    #     重複計權、有總經發布時三個情境分支要條件在它上面。
    #     `_event_graph_probe()` 讓指紋看得見這三條。
    # v22(第二十三輪):每條重點都要是事件、前三全處理、第二總經發布
    #     不得忽略、淨效果衝突用別名正規化、request_gate 量
    #     response_schema(32K schema 先前漏算)。
    # 第二十四輪 P1-5/P1-6/P1-7:三大重點條數成為契約、可駁回集合統一、
    # 每個未駁回的總經發布都要條件在三個分支上。
    # v24(P1-8/P1-9):結構化引用的指涉完整性(淨效果要有 claim 根據且
    # 主張要關於那個標的;cluster 引用要指得到真的東西)。
    # v25(外審 P1-6/P1-7/P1-8):證據欄位不再被自動修剪(裝飾層除外)、
    # 淨效果的標的比對改吃陣列 `asset_scope`(泛稱/空範圍不算指名)並要求
    # 方向同向、`offsetting_cluster_ids` 至少兩群且與衝突偵測一致、
    # 共用驅動要對得上 Python 端的分組、key-driver 反證也要驗。
    # v26(第二十六輪 P1-5):淨效果「兩側各一條主張」要錨回**證據側** ——
    # 那一側先前由主張自己的 `direction` 標籤決定,而標籤是輸出自己填的:
    # 同一批新聞寫兩條、其中一條標成相反方向就形式合格。
    # 探針補 `_side_grounding_case()`(放行版與被擋版都量)。
    # v27(P1-6):會計期間不是標的;「永遠不是標的」與「與這件事無關」
    # 拆成兩個問題(訊息才說得出真正的理由)。`_asset_probes()` 的標題
    # 帶上 Q2,新規則才是靠自己分勝負的那一條。
    "grounding_version":      (37, "aedfab6c966f63f4"),  # v37 加深建議改看真素材(2026-08-22 外審 P3;MACRO 只有 error 紀錄不算素材)  # v36 RENDERED 補六段(2026-08-19 P1-B)
}


def _declared_versions() -> dict:
    """同群鍵裡**目前**的版本號。"""
    return {
        "evidence_schema_version": ep.EVIDENCE_SCHEMA_VERSION,
        "output_schema_version": sch.ANALYSIS_SCHEMA_VERSION,
        "primary_profile_version": pp.LUNA_XHIGH_VERSION,
        "fallback_profile_version": pp.DEEPSEEK_LEGACY_VERSION,
        "postprocess_version": lp.POSTPROCESS_VERSION,
        "renderer_version": lp.RENDERER_VERSION,
        "grounding_version": gr.GROUNDING_VERSION,
    }


# ---------------------------------------------------------------- 判準

def test_every_version_field_in_the_cohort_key_has_a_snapshot():
    """**涵蓋範圍從 `COHORT_FIELDS` 推導,不是手寫清單。**

    新增一個版本欄位卻沒有快照,漏掉不會有任何人發現 —— 而漏掉的症狀
    正是這條 finding 描述的那種:混群而不自知。
    """
    fields = set(lp.CONTRACT_VERSION_FIELDS)
    assert fields, "COHORT_FIELDS 裡找不到任何版本欄位 —— 掃描器壞了"
    assert set(_FROZEN) == fields, (
        f"沒有快照的版本欄位:{fields - set(_FROZEN)};"
        f"多出來的:{set(_FROZEN) - fields}")
    assert set(_declared_versions()) == fields


def test_each_contract_matches_its_frozen_version_and_behaviour():
    """**`(版本號, 行為雜湊)` 這一對要完全相等。**

    r1(Codex):原本拆成兩條判準 ——「行為變了**且**版本沒變」與
    「版本變了**且**行為沒變」—— **兩個都變時,兩條都不報**。
    而版本一旦升過、`_FROZEN` 沒跟著更新,第一條的 `declared == ver` 就
    永遠是 False:**那個契約從此不再被檢查,而且沒有任何訊號。**

    修一個守衛的缺口不該靠再加一條判準去補;**判準本身不能有縫**。
    合成一對之後三種情況都會紅,而診斷訊息分得出是哪一種。
    """
    now, declared = _behaviour(), _declared_versions()
    problems = []
    for k, (ver, want) in _FROZEN.items():
        got, dv = now[k], declared[k]
        if (dv, got) == (ver, want):
            continue
        if dv == ver:
            problems.append(f"{k}: 行為變了({want[:8]}→{got[:8]})但版本仍是 "
                            f"{ver} —— 樣本會混群,請升版並更新 _FROZEN")
        elif got == want:
            problems.append(f"{k}: 版本 {ver}→{dv} 但行為沒變 —— 誤升會把可比"
                            "的樣本切成兩半;若是刻意的請更新 _FROZEN")
        else:
            problems.append(f"{k}: 版本 {ver}→{dv} 且行為 {want[:8]}→{got[:8]}"
                            " —— 兩個都變,請更新 _FROZEN(這一格原本是漏洞)")
    assert not problems, "\n  ".join(["契約快照對不上:"] + problems)


def test_the_snapshot_inputs_are_not_empty():
    """**空輸入會讓每個雜湊都變成「空的雜湊」** —— 那時這個檔恆綠。

    固定輸入本身要有內容,否則四條判準全部真空通過。
    """
    pk = _packet()
    assert (pk.get("news") or []), "固定 packet 沒有新聞"
    assert ar.render(_ANALYSIS).strip(), "固定分析渲染不出東西"
    assert lp._extract_stance(_REPORT_TEXT).get("label"), "固定文字抽不出立場"
    assert pp.build_luna_bundle(pk)["user_payload"].strip()


def test_the_snapshot_fixture_is_schema_valid():
    """**固定輸入自己要合法**(第十三輪 P2-3)。

    快照量的是「契約對某個輸入怎麼反應」。輸入若是真實 API 不會產出的
    形狀,量到的就是一個與生產無關的行為 —— 而它照樣會穩定、照樣會通過。
    """
    assert jc.violations(_ANALYSIS, sch.ANALYSIS_OUTPUT_SCHEMA) == []
    for i, case in enumerate(_GROUNDING_CASES):
        assert jc.violations(case, sch.ANALYSIS_OUTPUT_SCHEMA) == [], (
            f"grounding 案例 {i} 形狀就不合法 —— 那一關會先擋掉它,"
            "量不到「根據」那一關")
