# -*- coding: utf-8 -*-
"""**深度優化:新聞層的縱向與橫向**(2026-08-08)。

橫向兩條:跨語言同事件靠**數字錨點**橋接(P2-7,標題重疊實測 0.33
永遠過不了 0.5 門檻);聚合器條目的發布者從**標題尾綴**解析(P2-8,
欄位空的時候發布者藏在「標題 - 發布者」裡,先前整則記成 aggregator_only)。

縱向一條:**延燒中的事件優先抓全文** —— 延續事件在信裡要寫增量,
增量需要細節;只有 RSS 摘要時模型只能把背景再講一次。

共同原則(repo 既有):**誤併比漏併危險**,每一道橋接都有守衛測試
盯著它不得反向擴張。
"""
import json

import fetch_plan as fp
import news_clusters as nc
import source_registry as sr


def _n(sid, title, entities, source="X", source_name=None, **kw):
    d = {"source_item_id": sid, "title": title, "entities": entities,
         "source": source,
         "source_name": source if source_name is None else source_name}
    d.update(kw)
    return d


# ---------------------------------------------------------- 橫向:跨語言

def test_cross_language_same_deal_is_one_cluster_with_two_sources():
    """CNBC 的 $38B 與經濟日報的 383億美元是**同一筆錢** ——
    合併後獨立來源數是 2(nbcu + udn),佐證等級升到 multi_source。
    先前這是兩群、各自「僅單一來源」。"""
    cs = nc.clusters([
        _n("n1", "SK Hynix to spend $38 billion on two new chip plants",
           ["SK Hynix"], source="CNBC"),
        _n("n2", "SK海力士砸383億美元建兩座新廠 - 經濟日報",
           ["SK海力士"], source="Google:半導體", source_name=""),
    ])
    assert len(cs) == 1, cs
    assert cs[0]["independent_sources"] == 2
    assert cs[0]["corroboration"] == "multi_source"


def test_same_language_low_overlap_does_not_bridge():
    """**第 1 道防線。** 同一家公司同一天的兩則中文新聞常共用金額 ——
    「發債」與「發債資金用途」是兩個角度,標題重疊已判它們不同,
    數字不得推翻同語言的判定。"""
    cs = nc.clusters([
        _n("n1", "國泰人壽發行160億元公司債 十年期利率3.7%", ["國泰金"]),
        _n("n2", "國泰金響應綠色金融 全年授信目標160億元", ["國泰金"],
           source="Y", source_name="Y"),
    ])
    assert len(cs) == 2, [c["member_source_ids"] for c in cs]


def test_currency_mismatch_does_not_bridge():
    """**第 3 道防線。** 383億台幣與 $38.3B 差 30 倍,不是同一筆錢。"""
    cs = nc.clusters([
        _n("n1", "Micron to invest $38.3 billion in new fab", ["Micron"]),
        _n("n2", "美光加碼台灣383億台幣擴產封測 - 經濟日報", ["美光"],
           source="Google:半導體", source_name=""),
    ])
    assert len(cs) == 2


def test_value_mismatch_does_not_bridge():
    cs = nc.clusters([
        _n("n1", "Intel wins $8 billion CHIPS award", ["Intel"]),
        _n("n2", "英特爾豪擲380億美元蓋新廠 - 工商時報", ["英特爾"],
           source="Google:半導體", source_name=""),
    ])
    assert len(cs) == 2


def test_price_level_numbers_never_bridge():
    """**第 2 道防線。** 收盤價出現在同一家公司當天的每一則新聞裡,
    它不指認任何事件 —— 1,000 萬的金額下限把行情數字全擋在外面。"""
    import cross_lang as cl
    assert cl.money_anchors("台積電收2370元創高") == set()
    assert cl.money_anchors("TSMC closes at $240") == set()
    cs = nc.clusters([
        _n("n1", "TSMC shares hit $240 record high", ["TSMC"]),
        _n("n2", "台積電240元關卡攻防 外資按讚 - 經濟日報", ["台積電"],
           source="Google:半導體", source_name=""),
    ])
    assert len(cs) == 2


def test_no_entity_intersection_no_bridge():
    """數字相同、主體不同 —— 兩家公司同一天各買 38 億美元的設備,
    仍是兩件事。實體別名組交集是先決條件,不是加分項。"""
    cs = nc.clusters([
        _n("n1", "Samsung to spend $38 billion on fabs", ["Samsung Electronics"]),
        _n("n2", "美光豪擲380億美元擴產 - 經濟日報", ["美光"],
           source="Google:半導體", source_name=""),
    ])
    assert len(cs) == 2


# ---------------------------------------------------------- 橫向:發布者浮出

def test_publisher_surfaces_from_google_title_suffix():
    """欄位空、發布者在標題尾 —— 解析出 udn,而不是記成 aggregator_only。"""
    it = _n("n1", "台積電擴產進度超前 - 經濟日報", ["台積電"],
            source="Google:半導體", source_name="")
    assert sr.owner_of_item(it) == "udn"
    out = sr.independence([it])
    assert out["count"] == 1 and out["aggregator_only"] == 0


def test_unrecognized_suffix_stays_honest():
    """認不得的尾綴可能是副標也可能是小站 —— **不猜**,維持 aggregator_only
    (沒驗不是驗過)。"""
    it = _n("n1", "台積電擴產進度超前 - 某個小站", ["台積電"],
            source="Google:半導體", source_name="")
    # 釘在規則所在的那一層:`owner_of_item == ""` 在這裡不夠 ——
    # `title_publisher` 亂回傳時會被下游的 `owner_of` 遮住,
    # 而 `bare_title` 沒有那道下游,會開始剝不認得的尾綴。
    assert sr.title_publisher(it) == ""
    assert sr.owner_of_item(it) == ""
    assert sr.independence([it])["aggregator_only"] == 1


def test_suffix_strip_lets_short_titles_merge():
    """同一件事、兩家發布者、短標題:尾綴的字元先前把重疊壓到門檻下,
    同一件事拆成兩群 —— 兩個發布者還各自算一次獨立來源。"""
    cs = nc.clusters([
        _n("n1", "台積電法說 - 經濟日報", ["台積電"],
           source="Google:半導體", source_name=""),
        _n("n2", "台積電法說會 - 中時新聞網", ["台積電"],
           source="Google:半導體", source_name=""),
    ])
    assert len(cs) == 1, [c["member_source_ids"] for c in cs]
    assert cs[0]["independent_sources"] == 2


def test_non_aggregator_dash_tail_is_content_not_publisher():
    """一般媒體的「 - 副標」是內容。剝掉它會把「財報 - 記者觀點」與
    「財報 - 法說會前瞻」併成一件事 —— 尾綴剝除只適用聚合器條目。"""
    # 反例要只靠被測那條規則分勝負:前綴短到「不剝」時過不了門檻
    # (0.4 < 0.5),「剝了」就變 1.0 —— 勝負完全由尾綴處理決定。
    cs = nc.clusters([
        _n("n1", "統一超 - 記者觀點", ["統一超"], source="經濟日報"),
        _n("n2", "統一超 - 法說會前瞻", ["統一超"], source="工商時報"),
    ])
    assert len(cs) == 2, [c["member_source_ids"] for c in cs]
    assert sr.bare_title({"source": "經濟日報",
                          "title": "統一超 - 記者觀點"}) \
        == "統一超 - 記者觀點"


def test_field_beats_title_suffix():
    """`source_name` 有值時信欄位 —— 標題尾綴是最後手段,不是覆寫。"""
    it = _n("n1", "台積電擴產 - 經濟日報", ["台積電"],
            source="Google:半導體", source_name="工商時報")
    assert sr.owner_of_item(it) == "chinatimes"


# ---------------------------------------------------------- 縱向:延燒優先

def _fresh_vs_burning():
    """兩個事件群:同重要性、同獨立度(都是單一未知來源)。
    只有「延燒中」能分出先後 —— 反例要只靠被測那條規則分勝負。"""
    return [
        _n("a1", "某新創發表新產品", ["某新創"], source="甲站",
           importance="high", link="http://a"),
        _n("b1", "伊朗荷姆茲海峽談判進入第二週", ["伊朗"], source="乙站",
           importance="high", link="http://b"),
    ]


def _rec(key, days, subjects, title, action=""):
    """timeline 記錄的生產形狀(state 檔 `{key: {...}}` 載入後的一筆)。"""
    return {"key": key, "days": days, "subjects": subjects,
            "latest_title": title, "action": action,
            "event_type": key.split(":", 1)[0]}


_BURNING = [_rec("geopolitical:hormuz_passage:2026-08", 7, ["伊朗"],
                 "伊朗荷姆茲海峽談判進入第二週", "hormuz_passage")]


def test_burning_story_outranks_fresh_story_at_equal_footing():
    news = _fresh_vs_burning()
    out = fp.plan(news, nc.clusters(news), timeline=_BURNING)
    assert out["targets"][0] == "b1", out["targets"]
    assert out["continuing_boosted"] == ["cluster:b1"]
    # 沒給 timeline 時退回原排序(ID 決勝 → a1 先)
    base = fp.plan(news, nc.clusters(news))
    assert base["targets"][0] == "a1", base["targets"]


def test_burning_does_not_outrank_independence():
    """**延燒排在獨立度之後**:三家證實的新事件比單來源的延燒尾巴重要。"""
    news = _fresh_vs_burning() + [
        _n("a2", "某新創發表新產品 市場關注", ["某新創"], source="經濟日報",
           importance="high", link="http://a2"),
        _n("a3", "某新創發表新產品 供應鏈受惠", ["某新創"], source="中時新聞網",
           importance="high", link="http://a3"),
    ]
    out = fp.plan(news, nc.clusters(news), timeline=_BURNING)
    first_cluster = out["targets"][0]
    assert first_cluster in ("a1", "a2", "a3"), out["targets"]


def test_day_one_is_not_burning():
    """第 1 天不是延燒 —— 昨天才第一次出現的事件沒有「增量」可寫,
    門檻與信裡「延燒中事件(第 N 天)」的顯示門檻一致(≥2)。"""
    news = _fresh_vs_burning()
    out = fp.plan(news, nc.clusters(news), timeline=[
        _rec("geopolitical:hormuz_passage:2026-08", 1, ["伊朗"],
             "伊朗荷姆茲海峽談判進入第二週", "hormuz_passage")])
    assert out["continuing_boosted"] == []


def test_plan_for_run_reads_the_real_state_shape(tmp_path):
    """**生產的呼叫形狀**:state 檔是 `{key: {days, subjects}}`,
    loader 要吃這個形狀 —— 不是測試自己發明的整齊形狀。"""
    f = tmp_path / "event_timeline.json"
    f.write_text(json.dumps({
        "geopolitical:hormuz_passage:2026-08": {
            "days": 7, "subjects": ["伊朗", "阿曼"],
            "action": "hormuz_passage",
            "latest_title": "伊朗荷姆茲海峽談判進入第二週",
            "identity_schema": 6},
        "corp:x": "壞資料,要跳過",
    }, ensure_ascii=False), encoding="utf-8")
    recs = fp.timeline_records(f)
    assert len(recs) == 1 and recs[0]["days"] == 7
    assert recs[0]["subjects"] == ["伊朗", "阿曼"], recs
    news = _fresh_vs_burning()
    targets = fp.plan_for_run(news, budget=26, timeline_file=f)
    assert targets[0] == "b1"


def test_two_live_events_survive_the_state_loader(tmp_path):
    """**外審補審 F4 的生產形狀反例。** 上一條驗的是 `match_days` 本身;
    這一條走 `timeline_records` → `plan`,證明**載入時**沒有把 action
    丟掉(丟掉的話制裁案會拿到荷姆茲的 7 天與全文優先權)。"""
    f = tmp_path / "event_timeline.json"
    f.write_text(json.dumps({
        "geopolitical:hormuz_passage:2026-08": {
            "days": 7, "subjects": ["伊朗"], "action": "hormuz_passage",
            "latest_title": "伊朗荷姆茲海峽談判進入第二週"},
        "geopolitical:sanction:2026-08": {
            "days": 1, "subjects": ["伊朗"], "action": "sanction",
            "latest_title": "美國宣布對伊朗新一輪制裁"},
    }, ensure_ascii=False), encoding="utf-8")
    news = [_n("s1", "美國宣布對伊朗新一輪制裁措施", ["伊朗"], source="甲站",
               importance="high", link="http://s")]
    out = fp.plan(news, nc.clusters(news), timeline=fp.timeline_records(f))
    # 制裁案今天才第 1 天 —— 不得因為同主體就繼承荷姆茲的 7 天
    assert out["continuing_boosted"] == [], out["continuing_boosted"]


def test_missing_timeline_file_degrades_to_no_boost(tmp_path):
    """讀不到 → 不加權,不是不抓。降級方向是退回今天以前的排序。"""
    news = _fresh_vs_burning()
    targets = fp.plan_for_run(news, budget=26,
                              timeline_file=tmp_path / "沒有這個檔.json")
    assert sorted(targets) == ["a1", "b1"]


def test_production_call_site_passes_the_timeline_file():
    """**守衛不得靠遺忘失效**:加權只在呼叫端有傳 `timeline_file` 時
    存在 —— 生產忘了傳的話,上面每一條照樣綠而生產整段 no-op
    (2026-08-06 兩階段抓取 no-op 的形狀)。"""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "morning_report.py").read_text(encoding="utf-8")
    i = src.index("plan_for_run(")
    assert "timeline_file=EVENT_TIMELINE_FILE" in src[i:i + 200], \
        src[i:i + 200]


def test_manifest_can_tell_no_burning_from_broken_wiring():
    """`continuing_boosted=[]` + `timeline_entities=0` = 沒接上;
    `timeline_entities>0` = 接上了但今天沒有延燒事件。兩者要分得開。"""
    news = _fresh_vs_burning()
    off = fp.plan(news, nc.clusters(news))
    on = fp.plan(news, nc.clusters(news), timeline=[
        _rec("corp:x", 5, ["某公司"], "某公司財報")])
    assert off["timeline_events"] == 0
    assert on["timeline_events"] == 1 and on["continuing_boosted"] == []


# ---------------------------------------------------------- 共用判準不漂移

def test_packet_and_fetch_share_the_same_continuing_test():
    """packet 的 `continuing_days` 與 fetch 的延燒判定必須是**同一套**
    判準(`event_identity.match_days`)—— 兩套各自演化的話,「抓了全文
    的事件」與「標成第 N 天的事件」會是兩個集合。"""
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    for name in ("evidence_packet.py", "fetch_plan.py"):
        src = (root / name).read_text(encoding="utf-8")
        assert "match_days(" in src, f"{name} 不再用共用判準"
    import event_identity as ei
    # 三層比對都在:精確、別名組、標題(ASCII token 邊界)
    tsmc = [_rec("corp:台積電", 3, ["台積電"], "台積電熊本廠復線")]
    assert ei.match_days(tsmc, {"台積電"}, "台積電熊本廠復線") == 3
    assert ei.match_days(tsmc, {"TSMC"}, "TSMC 熊本廠復線") == 3
    # 標題層(實體抽取會漏,標題不會)。用**不帶對象**的動作來量,
    # 否則量到的是 NEEDS_OBJECT 的保守規則而不是 token 邊界。
    fx = [_rec("macro:fx", 4, ["Fed"], "Fed signals policy shift")]
    assert ei.match_days(fx, set(), "Fed signals policy shift again") == 4
    # ASCII token 邊界:ASUS 不得命中 US
    us = [_rec("geopolitical:US", 4, ["US"], "US budget talks stall")]
    assert ei.match_days(us, set(), "ASUS launches new laptop") == 0


def test_an_object_bearing_action_without_a_known_object_stays_at_zero():
    """**算不出對象就不接**(第二輪外審 F1 的保守側)。實體抽取空掉時,
    `arms_sale` 這種帶對象的動作無法確認是不是同一樁 —— 低估天數只是
    少一句「第 N 天」,接錯會讓今天才發生的事顯示成追蹤一週。"""
    import event_identity as ei
    recs = [_rec("geopolitical:arms_sale:台灣、美國:2026-08", 7,
                 ["美國", "台灣"], "美國宣布對台軍售", "arms_sale")]
    assert ei.match_days(recs, set(), "美國宣布對台軍售") == 0


def test_two_live_events_on_one_subject_do_not_share_days():
    """**外審補審 F4 的反例。** 同一個主體兩個活躍事件:荷姆茲第 7 天、
    制裁第 2 天。先前 timeline 被折成 `{主體: max(天數)}`,於是制裁案
    第一天就被標成延燒第 7 天、拿到全文優先權 —— 而 action/object
    身分引進來的**全部理由**就是主體會把不同事件併在一起。"""
    import event_identity as ei
    recs = [_rec("geopolitical:hormuz_passage:2026-08", 7, ["伊朗"],
                 "伊朗荷姆茲海峽通行談判", "hormuz_passage"),
            _rec("geopolitical:sanction:2026-08", 2, ["伊朗"],
                 "美國宣布對伊朗新一輪制裁", "sanction")]
    assert ei.match_days(recs, {"伊朗"}, "美國宣布對伊朗新一輪制裁") == 2
    assert ei.match_days(recs, {"伊朗"}, "伊朗與阿曼就荷姆茲通行達共識") == 7
    # 今天認不出動作 → 不接動作已知的記錄(低估天數是保守側)
    assert ei.match_days(recs, {"伊朗"}, "伊朗市場今日概況") == 0


def test_an_unrelated_same_subject_event_is_not_shadowed():
    """**外審補審 F5 的反例。** 荷姆茲通行(認得出動作)與革命衛隊軍演
    (動作不在表裡)是兩件事 —— 只比主體的話,軍演會從信裡整條消失。
    **隱藏真事件比顯示兩條更糟**:兩條讀者看得出混亂,消失的看不出。"""
    import event_identity as ei
    active = [
        {"event_type": "geopolitical", "action": "hormuz_passage",
         "subjects": ["伊朗"], "key": "geopolitical:hormuz_passage:2026-08",
         "days": 2, "latest_title": "伊朗與阿曼就荷姆茲航道達成共識"},
        {"event_type": "geopolitical", "action": "", "subjects": ["伊朗"],
         "key": "geopolitical:伊朗", "days": 5,
         "latest_title": "伊朗革命衛隊在波斯灣舉行大規模軍演"},
    ]
    assert len(ei.drop_shadowed(active)) == 2, "真事件被遮蔽了"
    # 反向:真的是同一個故事(標題重疊)時仍要遮蔽 —— 修正不得把
    # 2026-08-08 那個「同一件事兩個第 N 天」的缺陷放回來
    same = [active[0], dict(active[1],
                            latest_title="伊朗與阿曼荷姆茲航道共識後續")]
    assert len(ei.drop_shadowed(same)) == 1, same


def test_same_amount_different_event_type_does_not_bridge():
    """**外審補審 F6 的反例。** 「Micron 投資 $10B」與「美光營收 100 億
    美元」是兩件事 —— 先前只比金額,兩者被併成一群,`independent_sources`
    變 2、佐證升到 multi_source,**虛增的可信度會寫進信裡**。"""
    cs = nc.clusters([
        _n("n1", "Micron to invest $10 billion in new fab", ["Micron"],
           source="CNBC"),
        _n("n2", "美光第三季營收達100億美元 - 經濟日報", ["美光"],
           source="Google:半導體", source_name=""),
    ])
    assert len(cs) == 2, [c["member_source_ids"] for c in cs]
    assert all(c["corroboration"] == "single_source" for c in cs)


def test_same_category_still_bridges():
    """**修正不得把該合併的拆開**:同類別(capex)的跨語言同一筆錢仍要併。"""
    cs = nc.clusters([
        _n("n1", "SK Hynix to spend $38 billion on two new chip plants",
           ["SK Hynix"], source="CNBC"),
        _n("n2", "SK海力士砸383億美元建兩座新廠 - 經濟日報", ["SK海力士"],
           source="Google:半導體", source_name=""),
    ])
    assert len(cs) == 1, [c["member_source_ids"] for c in cs]
    assert cs[0]["independent_sources"] == 2


# ------------------------------------------------- 第二輪外審(補審 pass 2)

def test_same_action_different_object_does_not_inherit_days():
    """**R2-F1 的反例。** 「美國對台軍售」追蹤 7 天,今天首次出現
    「美國對日軍售」—— 兩者都是 `arms_sale`、都含「美國」。只比動作
    的話新事件會繼承 7 天,而 `NEEDS_OBJECT` 存在的理由正是這個。"""
    import event_identity as ei
    recs = [_rec("geopolitical:arms_sale:台灣、美國:2026-08", 7,
                 ["美國", "台灣"], "美國宣布對台軍售", "arms_sale")]
    assert ei.match_days(recs, {"美國", "日本"}, "美國宣布對日本軍售") == 0
    assert ei.match_days(recs, {"美國", "台灣"}, "美國再宣布對台軍售") == 7


def test_generic_words_and_the_subject_do_not_count_as_event_evidence():
    """**R2-F2/F3 的反例。** 「伊朗宣布軍演」與「伊朗宣布荷姆茲協議」
    的共同詞是「伊朗/朗宣/宣布」—— 重疊 0.38 越過 0.35 門檻,而那些詞
    **一個都不指認事件**:主體相交在上一層已經算過,再算一次是把同一份
    證據用兩次。"""
    import event_identity as ei
    assert not ei.title_related("伊朗宣布大規模軍演",
                                "伊朗宣布荷姆茲新協議", ["伊朗"])
    assert not ei.title_related("台積電宣布法說會日期",
                                "台積電宣布擴建新廠", ["台積電"])
    # 修正不得把真的同故事拆開
    assert ei.title_related("伊朗與阿曼荷姆茲航道達共識",
                            "伊朗阿曼荷姆茲航道共識後續", ["伊朗", "阿曼"])


def test_a_title_with_no_discriminative_words_never_matches():
    """辨識詞太少 → **不敢說是同一件事**(任何重疊都不構成證據)。"""
    import event_identity as ei
    assert ei.discriminative_tokens("台積電宣布", ["台積電"]) == set()
    # **反例要只靠門檻那條規則分勝負**:兩邊都空的話,`if not ta or not tb`
    # 那個寬鬆版本也會回 False,量不到門檻(突變驗證抓到)。
    # 這一組是「剛好一個辨識詞、而且重疊 1.0」—— 只有 MIN_DISCRIMINATIVE
    # 分得出勝負。單一辨識詞不足以認定同一件事:擴產可以是兩則不同的事。
    one = ei.discriminative_tokens("台積電宣布擴產", ["台積電"])
    assert len(one) == 1, one
    assert not ei.title_related("台積電宣布擴產", "台積電表示擴產不變",
                                ["台積電"])


def test_ambiguous_and_substring_category_hits_do_not_bridge():
    """**R2-F4 的反例。** 「投資人」含「投資」但講的是股東;
    英文 `raise` 不得命中 `praise`;同時命中兩類 → 分不出來就不橋接。"""
    import cross_lang as cl
    assert cl.event_category("美光營收100億美元,投資人關注") == "revenue"
    assert cl.event_category("firm draws praise for results") == ""
    assert cl.event_category("營收創高 將投資擴產") == ""
    cs = nc.clusters([
        _n("n1", "Micron to invest $10 billion in new fab", ["Micron"],
           source="CNBC"),
        _n("n2", "美光營收100億美元 投資人關注 - 經濟日報", ["美光"],
           source="Google:半導體", source_name=""),
    ])
    assert len(cs) == 2, [c["member_source_ids"] for c in cs]


def test_a_cross_language_continuation_is_not_lost_to_spelling():
    """**R3-F1 的反例。** timeline 存的是正規化後的主體(「美國」),
    今天英文報導給的是 `United States` —— 兩邊都要正規化,否則一條
    延燒 7 天的事件回 0 天、掉出全文優先權。"""
    import event_identity as ei
    recs = [_rec("geopolitical:arms_sale:台灣、美國:2026-08", 7,
                 ["美國", "台灣"], "美國宣布對台軍售", "arms_sale")]
    assert ei.match_days(recs, {"United States", "Taiwan"},
                         "US approves arms sale to Taiwan") == 7


def test_a_legacy_record_with_unnormalised_subjects_still_matches():
    """**R3-F1 的另一半反例(記錄那一側)。** 上一條驗的是今天的實體要
    正規化;舊記錄(或 `entity` fallback)存的可能是原文拼寫,
    **記錄那一側也要正規化**,否則同一件事一樣接不上。
    反例刻意讓今天全是中文、記錄全是英文 —— 只有記錄側的正規化
    分得出勝負。"""
    import event_identity as ei
    recs = [_rec("geopolitical:arms_sale:Taiwan、United States:2026-07", 6,
                 ["United States", "Taiwan"],
                 "US announces arms sale to Taiwan", "arms_sale")]
    assert ei.match_days(recs, {"美國", "台灣"}, "美國宣布對台軍售") == 6


def test_english_subject_spellings_are_stripped_too():
    """**R5-F1 的反例。** 主體傳進來的是 canonical(「美國」「伊朗」),
    而英文標題寫的是 `US` / `Iran` —— 正規化是**多對一**,反查要展開,
    而且要不分大小寫(表存小寫、標題大寫)。不挖掉的話,兩件不相干的
    英文事件會靠共用的國名越過門檻。"""
    import event_identity as ei
    tok = ei.discriminative_tokens("US Iran nuclear dossier dispute deepens",
                                   ["美國", "伊朗"])
    assert "us" not in tok and "iran" not in tok, sorted(tok)
    assert not ei.title_related("US Iran nuclear dossier dispute deepens",
                                "US Iran shipping incident escalates",
                                ["美國", "伊朗"])
    # 真的同一個故事仍要相關
    assert ei.title_related("US Iran nuclear talks resume in Vienna",
                            "US Iran nuclear talks enter second round",
                            ["美國", "伊朗"])


def test_stripping_a_short_english_name_keeps_word_boundaries():
    """`us` 不得把 `focus` / `versus` 挖出洞 —— 挖穿了會製造假的辨識詞。"""
    import event_identity as ei
    tok = ei.discriminative_tokens("Market focus versus consensus view",
                                   ["美國"])
    assert "focus" in tok and "versus" in tok, sorted(tok)
