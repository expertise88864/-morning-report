# -*- coding: utf-8 -*-
"""**第二十二輪 defer 掉的三項:兩個判準寫成宣告式,一個清單改成機械檢查。**

三項的共同形狀是「**規則寫在算式或人腦裡**」:
`got >= want` 只擋得住不等號的一側;錨點的範圍限制只寫在 `fact:` 上;
加深要保護的欄位是我手動列的,而渲染器隨時可以多讀一個欄位。
"""
import ast
import io
from pathlib import Path

import analysis_schema as sch
import analysis_stages as ast_mod
import claim_map as cm
import fixtures_analysis as fx

_ROOT = Path(__file__).resolve().parents[1]


def _read(name):
    return io.open(_ROOT / name, encoding="utf-8").read()


# ---------------------------------------------------------------- P1-5 矩陣

def test_a_claim_two_tiers_longer_does_not_cover_the_section():
    """**算式擋不住的那一側**:立場宣告當日,而撐它的全是 1-4 週的
    結構性主張 —— 「這個月看多」推不出「今天會漲」。
    `got >= want` 對這一格回 True(更長 = 更安全),矩陣回 False。"""
    assert cm.horizon_covers("intraday", "1-4w") is False
    assert cm.horizon_covers("1-4w", "intraday") is False   # 原本就擋
    # 相鄰一階仍然相容 —— 這條規則不是「只准完全相同」
    assert cm.horizon_covers("intraday", "1-5d") is True
    assert cm.horizon_covers("1-5d", "1-4w") is True
    assert cm.horizon_covers("1-5d", "1-5d") is True
    # 不認得的尺度不判(降級不誤擋)
    assert cm.horizon_covers("1-5d", "某個新尺度") is True
    assert cm.horizon_covers("某個新段落", "intraday") is True


def test_every_matrix_cell_is_declared():
    """**矩陣的價值在於每一格都被決定過** —— 缺格會退回「不判斷」,
    那正是算式的行為,等於白改。"""
    for sec in cm.HORIZON_ORDER:
        row = cm.HORIZON_MATRIX[sec]
        assert set(row) == set(cm.HORIZON_ORDER), (sec, sorted(row))
        assert row[sec] is True, f"{sec} 撐不起自己"


def test_the_message_states_what_would_be_accepted():
    """**訊息要與矩陣同一件事**(第二十一輪 P1-6 的教訓)。
    上一版寫死「全都比它更短」,而矩陣也會擋「更長兩階」。"""
    assert cm.horizons_compatible_with("intraday") == ["intraday", "1-5d"]
    assert cm.horizons_compatible_with("1-4w") == ["1-4w"]
    # 立場宣告**當日**,而撐它的主張全是 1-4 週的結構性判斷 ——
    # 這一格 `got >= want` 回 True(更長 = 更安全),矩陣回 False。
    obj = fx.valid_analysis()
    obj["stance"]["time_horizon"] = "intraday"
    for c in obj["claim_audit"]:
        c["horizon"] = "1-4w"
    hits = [p for p in sch.validate(obj, fx.ids())
            if "撐得起" in p and p.startswith("stance")]
    assert hits, "差兩階沒有被擋"
    assert "更短" not in hits[0], hits[0]
    assert "相容的是" in hits[0], hits[0]


# ---------------------------------------------------------------- P2-1 範圍

def _reg(value=1.5):
    return {"universe:2317.change_pct": {"value": value},
            "universe:2330.change_pct": {"value": value},
            "market:QQQ.change_pct": {"value": value}}


def test_a_subject_bearing_anchor_must_be_in_scope():
    """**數字是真的,跟這條鏈沒有關係。** 講台積電的鏈錨在鴻海的
    當日漲跌上 —— 上一版通過「有引用真數字」這一關。"""
    r = _reg()
    assert ast_mod.is_numeric_anchor(
        "universe:2317.change_pct", "n1", r, subjects={"2330"}) is False
    assert ast_mod.is_numeric_anchor(
        "universe:2330.change_pct", "n1", r, subjects={"2330"}) is True
    # 別名:實體寫「台積電」而錨點寫代號
    assert ast_mod.is_numeric_anchor(
        "universe:2330.change_pct", "n1", r, subjects={"台積電"}) is True


def test_market_wide_anchors_are_deliberately_unscoped():
    """`market:`/`derived:` 是指數、匯率、殖利率 —— 範圍本來就是整個
    市場,對任何標的的鏈都成立。**刻意不受這條規則約束。**"""
    assert ast_mod.is_numeric_anchor(
        "market:QQQ.change_pct", "n1", _reg(), subjects={"2330"}) is True


def test_both_production_call_sites_pass_the_scope():
    """**守衛不得因為呼叫端忘了傳而靜默失效。** `subjects=None` 是
    合法的降級(說得出自己沒驗),所以要另外釘住:生產的兩個呼叫端
    都有傳。"""
    for name in ("analysis_depth.py", "quality_metrics.py"):
        src = _read(name)
        assert "is_numeric_anchor(" in src, name
        assert "subjects=" in src, f"{name} 沒有把範圍傳下去"


# ------------------------------------------------- P1-8 渲染欄位的機械涵蓋

def _get_literals(src: str) -> set:
    """`x.get("欄位")` 裡的欄位名。"""
    out = set()
    for node in ast.walk(ast.parse(src)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get" and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            out.add(node.args[0].value)
    return out


def _depth_literals() -> set:
    """加深「身分」真正提到的欄位 —— 只看那幾個函式與 `_NEWS_KEPT`,
    不是整個檔的字串(整個檔會把註解與訊息裡的欄位名也算進來,
    那種涵蓋是假的)。"""
    tree = ast.parse(_read("analysis_depth.py"))
    want = {"_identity", "_news_identity", "_claim_fingerprint"}
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in want:
            out |= {x.value for x in ast.walk(node)
                    if isinstance(x, ast.Constant) and isinstance(x.value, str)}
        if (isinstance(node, ast.Assign) and node.targets
                and getattr(node.targets[0], "id", "") == "_NEWS_KEPT"):
            out |= {x.value for x in ast.walk(node.value)
                    if isinstance(x, ast.Constant) and isinstance(x.value, str)}
    return out


def test_every_rendered_field_is_protected_from_deepening():
    """**第二十一輪 P1-8 只做了一半**:我手動把 `source_caveat` 與
    `why_it_matters` 補進保護清單 —— 而下一個被渲染的新欄位一樣會漏。
    這裡兩邊都用 AST 掃:**渲染器讀得到的新聞/標的欄位,加深不得
    讓它由有變無**。清單漂移這次是機械檢查,不是我記得。"""
    rendered = set()
    for name in ("analysis_render.py", "analysis_render_depth.py"):
        rendered |= _get_literals(_read(name))
    news = sch.ANALYSIS_OUTPUT_SCHEMA["properties"][
        "top_news_analysis"]["items"]["properties"]
    asset = news["affected_assets"]["items"]["properties"]
    protected = _depth_literals()
    # `affected_assets` 是容器本身,逐標的身分另外守
    gap_news = (rendered & set(news)) - {"affected_assets"} - protected
    gap_asset = (rendered & set(asset)) - protected
    assert not gap_news, f"渲染進信卻不在加深身分裡的新聞欄位:{sorted(gap_news)}"
    assert not gap_asset, f"同上,標的欄位:{sorted(gap_asset)}"


def test_the_deepen_verdict_sees_the_same_advisories_that_triggered_it():
    """**選優的判準要與觸發加深的判準是同一套。**

    觸發用 `depth_advisories(obj, packet)`,而選優裡是
    `depth_advisories(before)` —— 少了 packet 就少了錨點、橫向這些規則。
    第二版剛好把 packet-aware 的那幾條修好時,盲測數量沒變少,
    **真正的改善被判成沒有改善**,沿用第一版。
    """
    src = _read("analysis_depth.py")
    body = src[src.index("def deepen_is_an_improvement"):]
    body = body[:body.index("\ndef ", 1)] if "\ndef " in body[1:] else body
    assert "depth_advisories(before, evidence_ids)" in body, \
        "選優仍在盲測 —— 沒有把 packet 傳給 depth_advisories"
    assert "depth_advisories(after, evidence_ids)" in body


#: **刻意不排進信裡的 `top_news_analysis` 欄位,與理由。**
#: schema 仍然要求模型填(填了才驗得動因果與引用),但讀者的視線是
#: 另一件事 —— 一則新聞底下排五六行標籤,使用者的原話是
#: 「讀起來像表單不像文章」。省略是決策,不是遺漏,所以要留下理由。
DELIBERATELY_UNRENDERED = {
    "confirmation_signal": "2026-08-17 定案:只留失效條件那一半",
    "why_this_magnitude": "2026-08-17 定案:量級的理由仍被驗證,不排進視線",
    "persistence": "2026-08-17 定案:同上",
    "relates_to": "橫向綜合那一段已經在講關係",
    "source_caveat": "佐證由 packet 說(行尾標籤),模型的原文說明不排",
    # 2026-08-18 外審第三輪的延伸:句尾那個「(單一來源)」是模型抄的,
    # 而 packet 分群時就算好同一件事(schema 自己寫著「以 EVIDENCE 為準」)。
    # 兩處寫同一件事、其中一處是模型抄的 —— 留 packet 那份。
    "corroboration_assessment": "2026-08-18 定案:改由 packet 的分群結果在行尾標籤呈現(`[A 級・2 家獨立報導]`)",
    # `magnitude_band` 中途離開過帳本又回來:有一版拿它當「信心」的輸入,
    # 而那一版被外審駁回(模型欄位不得決定讀者看到的信心)。現在信心只看
    # packet 的分群證據,量級又回到「刻意不排進信裡」。
    "magnitude_band": "2026-08-18 定案:同上",
    "direction": "2026-08-18 定案:逐則方向詞正是「整篇都是偏多什麼的」;方向在「各標的合計影響」那一段合計後出現一次",
    "horizon": "2026-08-18 定案:同上",
}


def test_the_coverage_check_cannot_pass_on_an_empty_set():
    """**空集合不算通過。** 掃不到欄位(renderer 改寫法、schema 換路徑)
    時上面那條會真空通過 —— 這裡釘住兩邊都要有實質內容。"""
    rendered = set()
    for name in ("analysis_render.py", "analysis_render_depth.py"):
        rendered |= _get_literals(_read(name))
    news = sch.ANALYSIS_OUTPUT_SCHEMA["properties"][
        "top_news_analysis"]["items"]["properties"]
    # **「至少 N 個」擋不住「又少渲染一個」**(2026-08-18):第八段改回
    # 敘事寫法時,逐標的的方向/幅度/時間窗被拿掉,這條只是從 10 掉到 7,
    # 而它要防的正是這種事。改成**逐欄位的省略帳本**:沒渲染又不在帳本裡
    # 的欄位當場紅,逼人寫下理由;已在帳本裡卻又被渲染回去也紅
    # (帳本過期跟少渲染一樣危險 —— 它會讓下一個人以為那欄沒排進信裡)。
    unrendered = set(news) - rendered
    assert unrendered == set(DELIBERATELY_UNRENDERED), (
        "沒排進信裡又沒寫理由:" + str(sorted(unrendered - set(DELIBERATELY_UNRENDERED)))
        + ";帳本說沒排、實際排了:"
        + str(sorted(set(DELIBERATELY_UNRENDERED) - unrendered)))
    assert len(_depth_literals()) >= 20
    # 已知會渲染的三個欄位要在掃描結果裡 —— 掃描本身壞掉時這條先紅。
    # 2026-08-17:哨兵從 `source_caveat` 換成 `invalidation_signal` ——
    # 使用者定案改敘事文體後,來源說明文字不再排進信裡(佐證**等級**
    # 仍以句尾「(單一來源)」呈現),而失效條件是留下來的那一半。
    # 哨兵要挑**確定會被渲染**的欄位,否則這條守衛自己會變成假紅。
    assert {"why_it_matters", "invalidation_signal", "materiality"} <= rendered
