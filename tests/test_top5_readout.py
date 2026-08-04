# -*- coding: utf-8 -*-
"""**Top5 卡片的一句話解讀**(2026-08-04 使用者回饋)。

那張卡每檔排出約 15 個數字而**一句話都沒有**。使用者兩次反映同一件事:
「很多地方還都只是在呈現數字、堆疊數據而已,沒有詳細分析影響」。
Prompt 改再多也碰不到這裡 —— 這一塊是 Python 直接排版的。

## 這個檔盯的兩件事

1. **它要說得出衝突。** 訊號一致時讀者自己看表也看得懂;
   「外資買但大戶減」「漲卻量縮」這種矛盾不講出來,就會被當成一致。
2. **它不得變成建議。** 這個 repo 栽過「啟發式被寫得像洞見」,
   所以措辭一律是事實陳述 —— 判準是機械化的禁用詞掃描,不是我讀過覺得還好。
"""
import top5_readout as t5

#: 2026-08-04 信裡 2610 華航的真實數字。**用生產的欄位名**。
_HUAHANG = {"foreign_streak": 5, "invest_streak": -1, "tdcc_wow_pct": 1.07,
            "vol_ratio_20d": 1.80, "day_pct": 3.23, "per": 8.8,
            "dividend_yield": 3.6, "rev_yoy_pct": 29}
#: 同一天 2603 長榮 —— 量比 0.62(量縮)而收紅,是「漲卻沒量」的真實案例。
_EVERGREEN = {"foreign_streak": 5, "invest_streak": -1, "tdcc_wow_pct": 0.48,
              "vol_ratio_20d": 0.62, "day_pct": 1.49, "per": 9.1,
              "dividend_yield": 7.7, "rev_yoy_pct": 30}


# ---------------------------------------------------------------- 要說得出衝突

def test_a_rising_price_on_shrinking_volume_is_called_out():
    """**漲卻量縮**:表上是兩個獨立數字,合起來才是一件事。"""
    out = t5.readout(_EVERGREEN)
    assert "漲勢沒有量能跟上" in out, out


def test_foreign_buying_against_shrinking_large_holders_is_called_out():
    """**外資買、大戶減** —— 不講出來就會被當成籌碼一致。"""
    out = t5.readout({"foreign_streak": 5, "tdcc_wow_pct": -0.80})
    assert "站在對邊" in out, out


def test_institutions_disagreeing_with_each_other_is_called_out():
    """外資買而投信賣:法人內部沒有共識,也是衝突。"""
    out = t5.readout({"foreign_streak": 4, "invest_streak": -3})
    assert "方向相反" in out, out


def test_agreement_is_described_without_being_dressed_up():
    """反向:一致時就說一致,不加碼形容。"""
    out = t5.readout(_HUAHANG)
    assert "籌碼往同一個方向集中" in out
    assert "追價成本已經墊高" in out, "放量沒有被指出來"


# ---------------------------------------------------------------- 不得變成建議

def test_it_never_recommends_anything():
    """**只描述,不建議。**

    判準是機械化的禁用詞掃描 —— 「我讀過覺得還好」不是判準,
    而這個 repo 栽過「啟發式被寫得像洞見」。
    """
    banned = ("可以買", "建議", "值得", "偏多", "偏空", "看好", "看空",
              "應該", "進場", "布局", "加碼", "減碼", "目標價", "上看")
    cases = [_HUAHANG, _EVERGREEN,
             {"foreign_streak": 5, "tdcc_wow_pct": -0.8},
             {"foreign_streak": -4, "invest_streak": 3, "vol_ratio_20d": 2.4,
              "day_pct": -3.0, "per": 40, "dividend_yield": 0.5,
              "rev_yoy_pct": -12}]
    for c in cases:
        out = t5.readout(c)
        for w in banned:
            assert w not in out, f"出現建議語氣「{w}」:{out}"


def test_nothing_to_say_means_an_empty_string():
    """**寧可少一句,不要生一句空話。**

    欄位缺就跳過那一段;全缺就回空字串,由呼叫端整行不顯示。
    """
    assert t5.readout({}) == ""
    assert t5.readout(None) == ""
    # 只有微弱訊號(連續天數不到門檻、量比正常、估值不極端)也一樣沉默
    assert t5.readout({"foreign_streak": 1, "invest_streak": 1,
                       "vol_ratio_20d": 1.0, "per": 18,
                       "dividend_yield": 2.0}) == ""


# ---------------------------------------------------------------- 別的失效方式

def test_a_financial_stock_does_not_use_revenue_growth():
    """金融股的營收年增受合併與利差扭曲 —— 卡片別處已經排除它,
    這裡不得從後門把它放回來。"""
    fin = {"per": 8.0, "dividend_yield": 6.0, "rev_yoy_pct": 45}
    assert "營收" in t5.readout(fin)
    assert "營收" not in t5.readout(fin, is_financial=True)


def test_a_bool_is_never_a_number():
    """`True` 是 1 —— 不擋的話會被當成量比或本益比拿去比大小。"""
    assert t5.readout({"vol_ratio_20d": True, "day_pct": True}) == ""
    assert t5.readout({"per": True, "dividend_yield": True}) == ""


def test_the_thresholds_match_the_card_footnote():
    """卡片註腳寫「< 0.8 量縮、> 1.5 放量」——**兩邊要用同一組數字**,
    否則同一張卡會自己說兩套。"""
    assert (t5.VOL_QUIET, t5.VOL_HEAVY) == (0.8, 1.5)


def test_it_reads_tdcc_from_either_source():
    """大戶週變化在兩個 dict 裡都可能出現;取不到就當沒有,不得拋。"""
    a = t5.readout({"foreign_streak": 4}, {"tdcc_wow_pct": 0.9})
    assert "集中" in a, a
    assert t5.readout({"foreign_streak": 4}) == "外資連4天買。"


# ---------------------------------------------------------------- 生產接線

def test_the_card_actually_renders_the_line():
    """**生產那條路要接上。**

    直接測 `readout()` 測得很漂亮、而卡片沒有印出來 ——
    那是本 repo 反覆栽的地方,所以判準掃真正的組版原始碼。
    """
    import ast
    import inspect
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / "morning_report.py"
    text = src.read_text(encoding="utf-8")
    assert "_t5r.readout(s, sm, is_financial=is_fin)" in text, (
        "卡片沒有呼叫解讀,或 is_financial 沒有接上真正的判斷")
    assert "_htmllib.escape(_readout)" in text, "解讀沒有進 HTML,或沒有跳脫"
    # 呼叫要在 `is_fin` **之後** —— 之前的話金融股會用到被扭曲的營收年增
    i_fin = text.index("is_fin = (_ind ==")
    i_call = text.index("_t5r.readout(s, sm")
    assert i_fin < i_call, "解讀算在 is_fin 之前,金融股會誤用營收年增"
    assert ast.parse(text) is not None
    _ = inspect
