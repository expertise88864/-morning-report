# -*- coding: utf-8 -*-
"""**涵蓋面的宣告要真的被查**(2026-08-18 使用者定案)。

原話:「有重大新聞的公司 有新聞才報 例如納入台灣0050前10大/
NASDAQ前15大等公司」。

兩件事要分開:
  * **涵蓋**是「每天都會去問這家公司有沒有新聞」;
  * **報導**是「抓到了而且通過重要性門檻」。
使用者要的是前者變寬,後者的門檻不動 —— 沒新聞的公司照樣不出現。

一張寫在程式裡的清單,如果沒有對應的查詢,那個「已涵蓋」的宣稱就是假的
(這個 repo 踩過同一形狀:宣告存在、呼叫端不存在)。
"""
import industry_class as ic
import instrument_registry as ir
import morning_report as mr


def _labels() -> set:
    return {lbl for _q, lbl in mr.GOOGLE_NEWS_COMPANIES}


def test_every_declared_cover_target_has_a_daily_query():
    """**宣告的每一檔都要有查詢。** 沒有的話那張表只是註解。"""
    missing = [c for c in (mr.TW0050_TOP10_LABELS + mr.NASDAQ_TOP15_LABELS)
               if c not in _labels()]
    assert not missing, f"宣告在涵蓋清單裡卻沒有任何查詢:{missing}"


def test_the_cover_lists_are_not_empty():
    """空集合不算通過 —— 清單被清空時上面那條會真空通過。"""
    assert len(mr.TW0050_TOP10_LABELS) == 10
    assert len(mr.NASDAQ_TOP15_LABELS) == 15


def test_every_covered_foreign_name_is_a_declared_instrument():
    """查得到新聞卻**不是宣告過的標的**,那則新聞的主體會被丟掉 ——
    小標題查不到主體就沒有標題(見 `analysis_render_depth.news_subject`)。"""
    bad = [c for c in mr.NASDAQ_TOP15_LABELS
           if ir.resolve_status(c)[2] == "invalid"]
    assert not bad, f"涵蓋了但沒有在 instrument_registry 宣告:{bad}"


def test_a_retailer_and_a_carrier_are_not_filed_as_tech():
    """**「被宣告」不再等於「是科技股」。**

    registry 原本的收錄範圍是「半導體鏈與大型科技股」,所以宣告本身
    可以當科技的依據。補到 NASDAQ-100 權重前段班之後那個等式不成立了:
    Costco 是零售、T-Mobile 是電信業者。例外要**宣告**,不是猜。
    """
    assert not ic.is_tech_foreign("COST")
    assert not ic.is_tech_foreign("TMUS")
    # 網通設備與軟體仍算科技 —— 這條測試不是在把整批新名字踢出科技。
    assert ic.is_tech_foreign("CSCO")
    assert ic.is_tech_foreign("ADBE")
    assert ic.is_tech_foreign("NVDA")


def test_the_exception_list_only_holds_declared_names():
    """例外表裡的名字必須真的被宣告過 —— 拼錯的名字會**永遠不生效**,
    而症狀是 Costco 靜靜地出現在科技類股底下。"""
    for name in ic.NON_TECH_FOREIGN:
        assert ir.resolve_status(name)[2] != "invalid", name


def test_the_legacy_section_assignment_uses_the_same_exception_list():
    """**歸屬表是直接寫進 prompt 的指令**(「Python 指定,不得更動」)——
    分錯段模型會照辦。

    外審 2026-08-18:`assign_event_sections` 的 `us_tech` 原本收
    `GOOGLE_NEWS_COMPANIES` 裡所有非數字標籤,前提是那張表只有科技股。
    同一批把涵蓋面補到 NASDAQ-100 前段班之後那個前提不成立,而
    `NON_TECH_FOREIGN` 只被渲染層那條路徑用到 —— **同一個判斷在兩處
    分歧**,Costco 於是被指到「八、科技板塊脈動」。
    """
    ev = [{"entity": e, "title": f"{e} 的重大新聞", "event_type": "earnings",
           "quality_score": 9 - i} for i, e in enumerate(
              ("COST", "TMUS", "NVDA", "CSCO", "ADBE"))]
    got = {a["entity"]: a["section"] for a in mr.assign_event_sections(ev, [])}
    assert got["COST"] == mr._SECTION_OTHER, got
    assert got["TMUS"] == mr._SECTION_OTHER, got
    # 反向:網通設備與軟體仍歸科技段 —— 這條不是在把新名字整批踢出去。
    assert got["NVDA"] == mr._SECTION_TECH, got
    assert got["CSCO"] == mr._SECTION_TECH, got
    assert got["ADBE"] == mr._SECTION_TECH, got


def test_the_two_paths_agree_on_every_covered_foreign_name():
    """**判準只有一份。** 渲染層(`is_tech_foreign`)與既有路徑的段落歸屬
    對同一個名字必須給同一個答案 —— 兩份會分歧,而分歧的症狀是同一家公司
    在兩處被分到不同段,沒有任何錯誤訊息。"""
    names = [lbl for _q, lbl in mr.GOOGLE_NEWS_COMPANIES
             if lbl and not str(lbl).isdigit()]
    assert names, "空集合不算通過"
    ev = [{"entity": n, "title": f"{n} 新聞", "event_type": "earnings",
           "quality_score": 1} for n in names]
    got = {a["entity"]: a["section"]
           for a in mr.assign_event_sections(
               ev, [], limit=len(ev))}
    for n in names:
        by_section = got[n] == mr._SECTION_TECH
        assert by_section == ic.is_tech_foreign(n), (
            f"{n}:段落歸屬說 {got[n]},而 is_tech_foreign 說 "
            f"{ic.is_tech_foreign(n)}")
