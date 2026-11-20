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
