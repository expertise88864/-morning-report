"""11-22 週日信:醫學文獻譯名出現「Smoke（）」「conversion（）」空括號。"""
import journal_selection as js
import morning_report as mr


def test_marker_alone_in_brackets_leaves_no_empty_brackets():
    zh = "Smoke（語義待摘要確認）而非吸菸，可能是觸發 Leber 遺傳性視神經病變 conversion（語義待摘要確認）的危險因子"
    assert js.clean_zh(zh) == "Smoke而非吸菸，可能是觸發 Leber 遺傳性視神經病變 conversion的危險因子"
    assert js.clean_zh("（decoupling，語義待摘要確認）現象") == "（decoupling）現象"
    assert js.clean_zh("術語 (語義待摘要確認) 尾") == "術語  尾"
    assert js.clean_zh("一般標題（JAK）") == "一般標題（JAK）"


def test_translation_path_uses_the_cleaner(monkeypatch):
    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": '{"items": [{"i": 0, "zh": "Smoke（語義待摘要確認）風險"}]}'}}]}

    monkeypatch.setattr(mr, "DEEPSEEK_API_KEY", "k")
    monkeypatch.setattr(mr.requests, "post", lambda *a, **k: R())
    out = mr.translate_journal_titles([{"title": "Smoke risk", "pmid": "1", "journal": "AJO"}])
    assert out[0]["zh"] == "Smoke風險"
