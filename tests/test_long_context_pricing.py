# -*- coding: utf-8 -*-
"""**超長輸入有另一段費率**(第十三輪 P1-1)。

## 外審說對了一半

它宣稱 Luna/Terra 單價各低估 5 倍與 20%。**2026-08-02 逐頁查證後不成立** ——
官方頁面兩處各自確認 luna `$0.2/$0.02/$1.2`、terra `$2.00/$0.20/$12.00`,
與本表一致。這一半沒有採納,理由寫在 `llm_pricing` 的 module docstring。

但同一份文件證實另一半:

    "Prompts with >272K input tokens are priced at 2x input and 1.5x output
     for the full request."

而 `estimate_cost()` 完全沒有這一層。晨報的 EvidencePacket 最多收 220 則、
每則全文 1,500 字,重大新聞日是有機會越過 272K 的 —— 那時整筆帳會被低估,
而**低估的方向正好偏向「Luna 很便宜」**,也就是這個實驗要下的那個結論。

## 判準

  1. 門檻以下不得套用(否則平常日全被高估);
  2. 越過門檻時**整筆**換價,不是只有超出的那一段;
  3. 倍率與官方 long-context 牌價一致(用倍率就不必再抄一張表,
     但那組倍率本身要被釘住);
  4. 沒有這一層的 provider(DeepSeek)不得被套用;
  5. 生效費率要記進結果 —— 只有總額的話,對不上帳單時分不出原因。
"""
import llm_pricing as lp

_LUNA = "gpt-5.6-luna"


def _usage(prompt, completion=10_000, **extra):
    return dict({"prompt_tokens": prompt, "completion_tokens": completion},
                **extra)


def test_below_the_threshold_nothing_changes():
    """平常日不得被套上長 context 費率。"""
    r = lp.estimate_cost(_LUNA, _usage(100_000))
    assert r["pricing_tier"] == "standard"
    assert r["effective_input_rate"] == lp.MODEL_PRICING[_LUNA]["input"]
    assert r["effective_output_rate"] == lp.MODEL_PRICING[_LUNA]["output"]


def test_exactly_at_the_threshold_is_still_standard():
    """官方寫的是 `>272K`,不是 `>=` —— 邊界要照抄,不要自己詮釋。"""
    t = lp.LONG_CONTEXT_TIERS["gpt-"]["threshold"]
    assert lp.estimate_cost(_LUNA, _usage(t))["pricing_tier"] == "standard"
    assert lp.estimate_cost(_LUNA, _usage(t + 1))["pricing_tier"] == "long_context"


def test_the_whole_request_switches_rate_not_just_the_excess():
    """**整筆換價。**

    只把超出的那一段加價,是很自然的誤解 —— 但官方寫的是
    "for the full request",而兩種算法在剛越線時差距最大。
    """
    t = lp.LONG_CONTEXT_TIERS["gpt-"]["threshold"]
    p = lp.MODEL_PRICING[_LUNA]
    pt, ct = t + 1000, 10_000
    got = lp.estimate_cost(_LUNA, _usage(pt, ct))["usd"]
    whole = (pt * p["input"] * 2 + ct * p["output"] * 1.5) / 1_000_000
    excess_only = ((t * p["input"] + 1000 * p["input"] * 2
                    + ct * p["output"]) / 1_000_000)
    assert abs(got - whole) < 1e-9, f"不是整筆換價:{got} vs {whole}"
    assert abs(got - excess_only) > 1e-6, "算成只有超出部分加價了"


def test_the_multipliers_match_the_published_long_context_prices():
    """**倍率要對得上官方的 long-context 牌價。**

    用倍率而不是再抄一張表,是為了避免兩張表要同步;代價是那組倍率必須
    被釘住 —— 否則「省下一張表」變成「少了一個可核對的東西」。
    2026-08-02 官方頁:luna 0.2→0.4 / 0.02→0.04 / 1.2→1.8;
    terra 2→4 / 0.2→0.4 / 12→18;sol 5→10 / 0.5→1 / 30→45。
    """
    published = {
        "gpt-5.6-luna": {"input": 0.4, "cached_input": 0.04, "output": 1.8},
        "gpt-5.6-terra": {"input": 4.0, "cached_input": 0.4, "output": 18.0},
        "gpt-5.6-sol": {"input": 10.0, "cached_input": 1.0, "output": 45.0},
    }
    tier = lp.LONG_CONTEXT_TIERS["gpt-"]
    for model, want in published.items():
        base = lp.MODEL_PRICING[model]
        for field, w in want.items():
            got = base[field] * tier[field]
            assert abs(got - w) < 1e-9, (
                f"{model}.{field}:倍率算出 {got},官方 long-context 牌價是 {w}")


def test_a_provider_without_the_tier_is_untouched():
    """DeepSeek 沒有這一層 —— 不收錄就不該被套用。"""
    assert lp.long_context_tier("deepseek-v4-pro", 10_000_000) is None
    r = lp.estimate_cost("deepseek-v4-pro", _usage(1_000_000))
    assert r["pricing_tier"] == "standard"


def test_the_effective_rates_are_recorded():
    """**生效費率要記下來。**

    只記一個總額,事後對不上帳單時分不出是「用錯費率」還是「漏了呼叫」。
    """
    r = lp.estimate_cost(_LUNA, _usage(300_000))
    for k in ("pricing_tier", "pricing_schema", "effective_input_rate",
              "effective_cached_rate", "effective_output_rate",
              "pricing_source"):
        assert k in r, f"成本結果沒有帶 {k}"
    assert r["pricing_schema"] == lp.PRICING_SCHEMA
    assert "long context" in r["basis"] or "長 context" in r["basis"]


def test_cache_fields_still_use_the_tiered_rate():
    """快取那兩段也要跟著換價 —— 官方 long-context 牌價的 cached 也是 ×2。"""
    u = _usage(300_000, prompt_tokens_details={"cached_tokens": 100_000})
    r = lp.estimate_cost(_LUNA, u)
    assert r["effective_cached_rate"] == lp.MODEL_PRICING[_LUNA]["cached_input"] * 2


def test_the_published_prices_are_what_we_verified():
    """**單價本身要被釘住。**

    外審宣稱 Luna 低估 5 倍;查證後不成立,而「查證過」這件事需要有東西
    守著 —— 否則下一次有人照著那個宣稱把表改掉,不會有任何阻力。
    """
    assert lp.MODEL_PRICING["gpt-5.6-luna"] == {
        "input": 0.20, "cached_input": 0.02, "output": 1.20}
    assert lp.MODEL_PRICING["gpt-5.6-terra"] == {
        "input": 2.00, "cached_input": 0.20, "output": 12.00}
    assert lp.PRICING_SCHEMA == 4, "加了費率層就要升 schema,舊資料不可相加"
