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


def test_burning_story_outranks_fresh_story_at_equal_footing():
    news = _fresh_vs_burning()
    out = fp.plan(news, nc.clusters(news), timeline={"伊朗": 7})
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
    out = fp.plan(news, nc.clusters(news), timeline={"伊朗": 7})
    first_cluster = out["targets"][0]
    assert first_cluster in ("a1", "a2", "a3"), out["targets"]


def test_day_one_is_not_burning():
    """第 1 天不是延燒 —— 昨天才第一次出現的事件沒有「增量」可寫,
    門檻與信裡「延燒中事件(第 N 天)」的顯示門檻一致(≥2)。"""
    news = _fresh_vs_burning()
    out = fp.plan(news, nc.clusters(news), timeline={"伊朗": 1})
    assert out["continuing_boosted"] == []


def test_plan_for_run_reads_the_real_state_shape(tmp_path):
    """**生產的呼叫形狀**:state 檔是 `{key: {days, subjects}}`,
    loader 要吃這個形狀 —— 不是測試自己發明的整齊形狀。"""
    f = tmp_path / "event_timeline.json"
    f.write_text(json.dumps({
        "geopolitical:hormuz_passage:2026-08": {
            "days": 7, "subjects": ["伊朗", "阿曼"], "identity_schema": 6},
        "corp:x": "壞資料,要跳過",
    }, ensure_ascii=False), encoding="utf-8")
    assert fp.timeline_map(f) == {"伊朗": 7, "阿曼": 7}
    news = _fresh_vs_burning()
    targets = fp.plan_for_run(news, budget=26, timeline_file=f)
    assert targets[0] == "b1"


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
    on = fp.plan(news, nc.clusters(news), timeline={"某公司": 5})
    assert off["timeline_entities"] == 0
    assert on["timeline_entities"] == 1 and on["continuing_boosted"] == []


# ---------------------------------------------------------- 共用判準不漂移

def test_packet_and_fetch_share_the_same_continuing_test():
    """packet 的 `continuing_days` 與 fetch 的延燒判定必須是**同一套**
    判準(`entity_alias.days_for`)—— 兩套各自演化的話,「抓了全文的
    事件」與「標成第 N 天的事件」會是兩個集合。"""
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    for name in ("evidence_packet.py", "fetch_plan.py"):
        src = (root / name).read_text(encoding="utf-8")
        assert "days_for(" in src, f"{name} 不再用共用判準"
    import entity_alias as ea
    # 三層比對都在:精確、別名組、標題(ASCII token 邊界)
    assert ea.days_for({"台積電"}, "", {"台積電": 3}) == 3
    assert ea.days_for({"TSMC"}, "", {"台積電": 3}) == 3
    assert ea.days_for(set(), "US sanctions on ASUS suppliers",
                       {"US": 4}) == 4
    assert ea.days_for(set(), "ASUS launches new laptop", {"US": 4}) == 0
