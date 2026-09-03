# -*- coding: utf-8 -*-
"""**第十六輪:空的分析不得真空通過,加深不得讓別處變差。**

這個檔盯的六條,其中三條是**我自己在上一批寫進去的缺陷**
(產業分歧的符號、同產業重複、加深沒有比較兩版)——
外審逐條指出、我逐條實測確認過。

共同的形狀:**守衛看起來在守,實際上守不到東西。**
  * 驗證器數 `mechanism_steps` 有幾個 dict,而空字串的步驟也算一個;
  * 橫向綜合整段留空時,`has_content` 的閘門讓所有檢查一起跳過;
  * 第二版只要合法就取代第一版,沒有人問「它真的比較好嗎」。
"""
import analysis_depth as ad
import analysis_schema as sch
import evidence_packet as ep
import fixtures_analysis as fx
import signal_tensions as st

_IDS = fx.ids()


def _packet_with_tensions() -> dict:
    """一份**真的會產生張力**的 packet(生產形狀)。"""
    return ep.build({"QQQ": {"change_pct": 1.76},
                     "TAIFEX_OI": {"foreign_oi_net": -90038}},
                    {}, {}, fx.news(), [], {},
                    as_of="2026-08-05T06:00", target_session_date="2026-08-05",
                    sanitize=str)


# ------------------------------------------------ 空的橫向/縱向不得通過

def test_an_unaddressed_tension_is_rejected():
    """**P1-2/P2-2**:今天有張力、橫向綜合沒處理 → 不合格。

    先前只有 prompt 要求,而 `conflicting_signals` 是自由文字 ——
    **沒有任何東西能證明模型真的逐條處理過**。
    """
    pk = _packet_with_tensions()
    obj = fx.valid_analysis()
    assert [p for p in sch.validate(obj, pk) if "訊號張力" in p], "沒處理卻通過"
    obj["cross_market_synthesis"]["tension_resolutions"] = [
        {"tension_id": t, "resolution": "外部定價先反映在權值開盤",
         "dominant_side": "left", "why": "開盤前只有美股已定價",
         "decision_rule": "現貨量能與期貨空單是否回補",
         "evidence_ids": ["n1"]}
        for t in sorted(st.required_tension_ids(pk.get("signal_tensions")))]
    assert not [p for p in sch.validate(obj, pk) if "訊號張力" in p]


def test_a_fabricated_tension_id_is_rejected():
    """反向:**回填不存在的 ID 比不回填更糟** —— 它讓「處理過」看起來成立。"""
    pk = _packet_with_tensions()
    obj = fx.valid_analysis()
    obj["cross_market_synthesis"]["tension_resolutions"] = [
        {"tension_id": "tension:t_bogus", "resolution": "x",
         "dominant_side": "left", "why": "y", "decision_rule": "z",
         "evidence_ids": []}]
    assert [p for p in sch.validate(obj, pk) if "不得回填不存在" in p]


def test_an_empty_news_analysis_is_rejected_when_there_is_news():
    """有新聞可分析卻交空陣列 —— 先前整份輸出照樣合法、renderer 直接省略。"""
    pk = _packet_with_tensions()
    obj = fx.valid_analysis()
    obj["top_news_analysis"] = []
    assert [p for p in sch.validate(obj, pk) if "top_news_analysis 卻是空的" in p]


def test_validate_still_accepts_a_plain_id_set():
    """**舊呼叫端不得被打壞**,只是少掉那幾條需要 packet 的判準。"""
    assert sch.validate(fx.valid_analysis(), _IDS) == []


# ------------------------------------------------ 因果鏈要非空且連續

def test_a_blank_mechanism_step_does_not_count():
    """**P1-7**:三個欄位都空的步驟先前算一步 —— 驗證器說有兩步,
    而 renderer 把空的濾掉,讀者看不到任何因果鏈。"""
    obj = fx.valid_analysis()
    obj["top_news_analysis"][0]["mechanism_steps"] = [
        {"from_what": "", "to_what": "", "channel": "",
         "step_type": "inference", "evidence_ids": []}]
    assert [p for p in sch.validate(obj, _IDS) if "空欄位" in p]


def test_a_broken_chain_is_rejected():
    """鏈斷了要抓得到 —— 三個不相干的片段各自成立,讀起來卻像一條因果。"""
    obj = fx.valid_analysis()
    obj["top_news_analysis"][0]["mechanism_steps"][1]["from_what"] = "無關的東西"
    assert [p for p in sch.validate(obj, _IDS) if "鏈斷了" in p]


def test_the_reference_fixture_has_a_continuous_chain():
    """**參考答案自己要示範它要求的性質。**

    這條守衛第一次跑就抓到 fixture 原本是斷的
    (`到:台股電子開盤定價` 接 `從:電子權值走強`)—— 修 fixture,不是修守衛。
    """
    assert sch.validate(fx.valid_analysis(), _IDS) == []
    assert ad.depth_advisories(fx.valid_analysis()) == []


# ------------------------------------------------ 加深要選優,不是照單全收

def test_the_deepen_result_must_actually_be_better():
    """**P1-8**:第二版只要合法就採用 → 可能修好深度、改壞別處。

    加深那次是**整份重生**,而「一個修正可能比原本的缺陷更糟」
    正是這個 repo 反覆栽的形狀 —— 這一次是我自己寫進去的。
    """
    shallow = fx.valid_analysis()
    shallow["top_news_analysis"][0]["mechanism_steps"] = \
        shallow["top_news_analysis"][0]["mechanism_steps"][:1]
    assert ad.depth_advisories(shallow), "第一版要是淺的"
    ok, why = ad.deepen_is_an_improvement(
        shallow, fx.valid_analysis(), evidence_ids=_IDS)
    assert ok, why


def test_a_drifted_stance_is_not_an_improvement():
    """加深不該改變判斷 —— 改了就不是加深,是換一份報告。"""
    shallow = fx.valid_analysis()
    shallow["top_news_analysis"][0]["mechanism_steps"] = \
        shallow["top_news_analysis"][0]["mechanism_steps"][:1]
    drifted = fx.valid_analysis()
    drifted["stance"]["label"] = "偏空"
    ok, why = ad.deepen_is_an_improvement(shallow, drifted, evidence_ids=_IDS)
    assert not ok and "立場漂移" in why, why


def test_losing_a_news_item_is_not_an_improvement():
    """**深度變好也不行** —— 少分析一則新聞是實質退步。"""
    shallow = fx.valid_analysis()
    shallow["top_news_analysis"][0]["mechanism_steps"] = \
        shallow["top_news_analysis"][0]["mechanism_steps"][:1]
    # 刻意刪掉**沒有被 `relates_to` 指向**的那一則(n1 指向 n2,
    # 所以刪 n2 會先撞上「關係指向不存在」那條)—— 這條要測的是
    # **筆數退步本身**,一個反例只違反一條規則才測得到那一條。
    fewer = fx.valid_analysis()
    fewer["top_news_analysis"] = fewer["top_news_analysis"][1:]
    ok, why = ad.deepen_is_an_improvement(shallow, fewer, evidence_ids=_IDS)
    # **身分檢查比數量檢查先攔到**(第十七輪 P1-8):數量只知道「少了一則」,
    # 身分說得出**少的是哪一則** —— 而「換掉一則」數量根本不會變。
    assert not ok and "弄丟了分析過的新聞" in why, why
    assert "n1" in why, why


def test_deleting_data_gaps_is_not_an_improvement():
    """**資料缺口被刪掉**是最難察覺的退步:報告看起來反而更完整。"""
    base = fx.valid_analysis()
    base["data_gaps"] = [{"what_is_missing": "資本支出金額",
                          "impact_on_conclusions": "量級判斷不出來"}]
    shallow = fx.valid_analysis()
    shallow["data_gaps"] = list(base["data_gaps"])
    shallow["top_news_analysis"][0]["mechanism_steps"] = \
        shallow["top_news_analysis"][0]["mechanism_steps"][:1]
    ok, why = ad.deepen_is_an_improvement(
        shallow, fx.valid_analysis(), evidence_ids=_IDS)
    assert not ok and "弄丟了資料缺口" in why, why
    assert "資本支出金額" in why, "要說得出弄丟的是哪一個缺口"


def test_an_illegal_second_version_is_rejected():
    """第二版不合法時當然留第一版 —— 而且理由要說得出是哪一條。"""
    shallow = fx.valid_analysis()
    shallow["top_news_analysis"][0]["mechanism_steps"] = \
        shallow["top_news_analysis"][0]["mechanism_steps"][:1]
    bad = fx.valid_analysis()
    bad["global_market"]["evidence_ids"] = ["n_fake"]
    ok, why = ad.deepen_is_an_improvement(shallow, bad, evidence_ids=_IDS)
    assert not ok and "不合法" in why, why


def test_no_depth_gain_is_not_an_improvement():
    """深度提示沒有減少 → 那一次呼叫沒有達到目的,不值得換掉第一版。"""
    same = fx.valid_analysis()
    same["top_news_analysis"][0]["mechanism_steps"] = \
        same["top_news_analysis"][0]["mechanism_steps"][:1]
    ok, why = ad.deepen_is_an_improvement(same, dict(same), evidence_ids=_IDS)
    assert not ok and "沒有減少" in why, why


def test_the_deepen_prompt_carries_the_previous_output():
    """不附上一版,模型只能整份重生 —— 那正是上面那些退步的來源。"""
    txt = ad.deepen_input("PAYLOAD", ["a"], previous=fx.valid_analysis())
    # 全案審查 2026-09-03 LM-3:上一版是回流的不可信資料 —— 與修補輪同一支
    # `previous_output_block`(裸 `PREVIOUS_OUTPUT` 標題 + 標準不信任圍欄),
    # 不再是自己捏的 `<PREVIOUS_OUTPUT>` 尖括號標籤。
    assert "PREVIOUS_OUTPUT" in txt and "<PREVIOUS_OUTPUT>" not in txt
    assert txt.count("<UNTRUSTED_SOURCE_DATA>") == 1 == txt.count("</UNTRUSTED_SOURCE_DATA>")
    assert "保留上一版所有已經成立的內容" in txt
    assert "不得硬湊" in txt
    assert txt.startswith("PAYLOAD"), "證據不見了"


def test_the_production_loop_uses_dominance_selection():
    """**生產迴圈要真的呼叫它** —— 只驗合法性的版本會靜靜採用較差的第二版。"""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "morning_report.py"
           ).read_text(encoding="utf-8")
    body = src[src.index("def _luna_analysis"):]
    body = body[:body.index("#: 盲評卡的落地目錄")]
    assert "_av.deepen_is_an_improvement(" in body, "沒有比較兩版"
    assert "previous=obj" in body, "加深時沒有附上前一版"
    assert "deepen_verdict" in body, "判定結果沒有進 manifest"


def test_an_empty_resolution_does_not_count_as_handled():
    """**P1-3 的另一半:填了 ID 但欄位是空的,等於只點名沒有處理。**

    突變驗證抓到的:把「resolution/why/decision_rule 不得為空」拿掉之後
    全套照樣綠 —— 沒有人守這一條。
    """
    pk = _packet_with_tensions()
    obj = fx.valid_analysis()
    obj["cross_market_synthesis"]["tension_resolutions"] = [
        {"tension_id": t, "resolution": "", "dominant_side": "left",
         "why": "", "decision_rule": "", "evidence_ids": []}
        for t in sorted(st.required_tension_ids(pk.get("signal_tensions")))]
    hits = [p for p in sch.validate(obj, pk) if "等於只點名沒有處理" in p]
    assert hits, "空的 resolution 被當成處理過了"
    assert any("resolution" in h for h in hits), "要指出是哪些欄位空的"


def test_a_high_materiality_chain_must_reach_the_financial_layer():
    """**P1-7**:「事件 → 市場關注提高 → 投資情緒改善」是兩步連續的合法鏈,
    卻沒有碰到訂單、稼動率、營收、估值或股價的任何一層。"""
    obj = fx.valid_analysis()
    obj["top_news_analysis"][0]["mechanism_steps"] = [
        {"from_what": "費半收漲", "to_what": "市場關注提高",
         "channel": "情緒", "stage": "event", "step_type": "inference",
         "evidence_ids": []},
        {"from_what": "市場關注提高", "to_what": "投資情緒改善",
         "channel": "情緒", "stage": "sentiment", "step_type": "inference",
         "evidence_ids": []}]
    adv = ad.depth_advisories(obj)
    assert any("營運或產業供需" in a for a in adv), adv
    assert any("停在情緒不算分析" in a for a in adv), adv
    # **仍然合法** —— 淺不擋信,只觸發加深
    assert sch.validate(obj, _IDS) == []
