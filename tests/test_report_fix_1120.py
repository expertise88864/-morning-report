"""11-20 實信:「高階模型單價差到 2.67 倍」是 8 美元 ÷ 3 美元,算對了卻被標成未核實。"""
import legacy_evidence as le

SRC = {"title": "OpenRouter 上架 Gemini 4 Neon 輸入 3 美元、輸出 15 美元;GPT-6.2 Astra Pro 輸入 8 美元、輸出 40 美元",
       "link": "https://news.example/openrouter"}


def _run(sentence):
    le.reset()
    le.tag(SRC)
    manifest = {}
    out = le.audit(f"## 八、科技板塊脈動\n**AI 模型競賽**：{sentence} [OpenRouter][N1]\n", {}, manifest)
    le.reset()
    return out, manifest["llm"]["legacy_evidence"]


def test_a_ratio_of_two_numbers_in_the_same_item_counts_as_evidence():
    out, report = _run("輸入 3 美元對 8 美元,高階模型單價差到 2.67 倍。")
    assert "（未核實）" not in out and report["failed"] == 0 and report["marked"] == 0


def test_a_wrong_ratio_is_still_marked():
    out, report = _run("輸入 3 美元對 8 美元,高階模型單價差到 3.4 倍。")
    assert "3.4 倍（未核實）" in out and report["marked"] == 1


def test_numbers_of_different_kinds_do_not_make_a_ratio():
    """8 美元 ÷ 3% 不是倍數;數字也必須在同一條裡,不從其他新聞湊。"""
    out, report = _run("價格 8 美元,市占 3%,差到 2.67 倍。")
    assert "2.67 倍（未核實）" in out and report["marked"] == 2   # 3% 本身也查無來源
    assert not le._ratio(("2.67", 2.67, "x", "", "倍"), [("8", 8.0, "usd", "", "美元")])
    assert not le._ratio(("0.5", 0.5, "x", "", "倍"), le._quantities("3 美元 6 美元"))


def _audit(src_title, sentence):
    le.reset()
    le.tag({"title": src_title, "link": "https://news.example/x"})
    manifest = {}
    out = le.audit(f"## 八、科技板塊脈動\n**甲**：{sentence} [鉅亨][N1]\n", {}, manifest)
    le.reset()
    return out


def test_review_pr62_operands_must_themselves_be_backed():
    out, _ = _run("輸入 30 美元對 80 美元,高階模型單價差到 2.67 倍。")
    assert "2.67 倍（未核實）" in out


def test_review_pr62_negative_operands_do_not_divide():
    out = _audit("甲每股盈餘 -3 美元,乙 8 美元", "甲每股盈餘 -3 美元,乙 8 美元,乙是甲的 2.67 倍。")
    assert "2.67 倍（未核實）" in out


def test_review_pr62_counts_need_the_same_unit():
    out = _audit("用戶 8 萬人、出貨 3 萬台", "用戶 8 萬人、出貨 3 萬台,出貨量達去年 2.67 倍。")
    assert "2.67 倍（未核實）" in out
    ok = _audit("今年出貨 8 萬台、去年 3 萬台", "今年出貨 8 萬台、去年 3 萬台,出貨量達去年 2.67 倍。")
    assert "（未核實）" not in ok


def test_review_pr62_ratio_rounds_half_up_at_the_stated_precision():
    assert "2 倍（未核實）" in _audit("售價 5 美元、成本 2 美元", "售價 5 美元、成本 2 美元,差 2 倍。")
    assert "（未核實）" not in _audit("售價 5 美元、成本 2 美元", "售價 5 美元、成本 2 美元,差 3 倍。")


def test_review_pr62_round2_sign_belongs_to_each_amount():
    src = "甲每股虧損 −US$3,乙每股盈餘 US$8"
    assert "2.67 倍（未核實）" in _audit(src, src + ",乙是甲的 2.67 倍。")
    both = "跌幅 -3%,輸入 3 美元、輸出 8 美元"
    assert "（未核實）" not in _audit(both, both + ",差到 2.67 倍。")


def test_review_pr62_round2_english_counts_need_a_unit():
    src = "8 million users, 3 million vehicles"
    assert "2.67 倍（未核實）" in _audit(src, src + ",出貨量是去年的 2.67 倍。")


def test_review_pr62_round2_ratio_is_computed_in_decimal():
    src = "售價 107 美元、成本 40 美元"
    assert "（未核實）" not in _audit(src, src + ",差 2.68 倍。")
    assert "2.67 倍（未核實）" in _audit(src, src + ",差 2.67 倍。")


def test_review_pr62_round3_a_negative_does_not_knock_out_an_equal_positive():
    src = "每股盈餘 -3 美元;甲模型輸入價 3 美元、乙模型 8 美元"
    assert "（未核實）" not in _audit(src, src + ",單價差到 2.67 倍。")
