"""User 2026-10-19: no simplified characters in the email, without corrupting 正體.

opencc alone rewrites 台→臺 and 發布會→釋出會, so only simplified-only characters
(and their immediate simplified neighbours) may change.
"""
import pytest

import morning_report as mr
import traditional_guard as tg


@pytest.mark.parametrize("src, want", [
    ("外資資金撤离", "外資資金撤離"),                       # 10/19 實信
    ("后续发布会", "後續發佈會"),
    ("这里的面条很干净", "這裡的麵條很乾淨"),
    ("头发与价格", "頭髮與價格"),
    ("台湾发展", "台灣發展"),                              # 台 never becomes 臺
    ("鄭欽文首度来港参赛", "鄭欽文首度來港參賽"),
    # PR #22 外審:Big5 次常用區裡的簡體字(厂、与、网)也要換。
    ("科技大厂", "科技大廠"),
    ("工厂与网络", "工廠與網絡"),
    ("听说台湾发展", "聽說台灣發展"),
    # 標籤屬性與網址不轉,只轉可見文字。
    ('<a href="https://udn.com/新闻/1">地方新闻</a>', '<a href="https://udn.com/新闻/1">地方新聞</a>'),
    ("见 https://x.com/新闻 报导", "見 https://x.com/新闻 報導"),
])
def test_simplified_text_becomes_taiwan_traditional(src, want):
    assert tg.simplified_free(src) == want


@pytest.mark.parametrize("text", [
    "台積電在台灣發布財報，皇后、公里、准許、一隻、乾淨、週報",
    "純繁體不變：臺北與台中",
    "涂醒哲手頭拮据，瀟洒依舊",
    "<a href=\"https://news.google.com/rss/articles/CBMi?oc=5\">標題</a>",
    "",
])
def test_traditional_and_markup_are_untouched(text):
    assert tg.simplified_free(text) == text


def test_non_strings_and_missing_opencc_never_block_sending(monkeypatch):
    assert tg.simplified_free(None) is None
    monkeypatch.setattr(tg, "_CC", None)
    import builtins
    real_import = builtins.__import__

    def no_opencc(name, *a, **kw):
        if name == "opencc":
            raise ImportError("offline fixture")
        return real_import(name, *a, **kw)
    monkeypatch.setattr(builtins, "__import__", no_opencc)
    assert tg.simplified_free("撤离") == "撤离"


def test_delivery_sends_and_archives_the_same_converted_html(monkeypatch, tmp_path):
    sent, archived = [], []
    monkeypatch.setattr(mr, "send_email", lambda html, subject: sent.append((html, subject)))
    monkeypatch.setattr(mr, "archive_report_html", lambda html, day: archived.append(html))
    monkeypatch.setattr(mr, "persist_delivered_report_state", lambda *a, **kw: None)
    monkeypatch.setattr(mr, "_mark_delivery_in_manifest", lambda **kw: None)
    import fallback_recap_runtime
    monkeypatch.setattr(fallback_recap_runtime, "delivered", lambda *a, **kw: None)
    mr.deliver_report("<p>台積電資金撤离</p>", "📈 晨报", {"date": "2026-10-19"}, [])
    assert sent == [("<p>台積電資金撤離</p>", "📈 晨報")]
    assert archived == ["<p>台積電資金撤離</p>"]
