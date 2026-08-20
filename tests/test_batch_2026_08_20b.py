# -*- coding: utf-8 -*-
"""2026-08-20 生產回饋批(第二批,使用者逐條指示)。

(1) ⚠ emoji 移除;(2) 第五段預測漲跌 % 併入大字、拿掉獨立格;
(3) 廣度+類股熱度搬到第五段正下方;(4) 來源引用(鉅亨/CNBC…)可點 ——
保守比對,錯連比不連糟;(5) 台指期 vs 現貨列刪除;(6) 在地快訊缺日期
fail-closed(三個月前的彰基舊聞就是這樣進來的);(7) 金控雙雄集團素材。
"""
import io
import datetime as dt
from pathlib import Path

import morning_report as mr
from render_utils import (
    _dim_source_citations,
    _link_source_citations,
    build_news_link_index,
)

_SRC = io.open(Path(__file__).resolve().parents[1] / "morning_report.py",
               encoding="utf-8").read()


# ── (1) ⚠ 移除 ───────────────────────────────────────────────────────────
def test_low_volume_markers_have_no_emoji():
    rows = [{"name": "甲", "prob": 58, "low_vol": True},
            {"name": "乙", "prob": 42, "wide": True}]
    line = mr._poly_prob_line(rows)
    assert "⚠" not in line, line
    assert "(部分量低)" in line and "(部分價差寬)" in line


# ── (2) 第五段版面 ───────────────────────────────────────────────────────
def test_pred_pct_sits_beside_the_big_number():
    """預測漲跌 % 接在「今天開盤大約落在 44,719」後方,不再獨立佔一格
    (2026-08-20 使用者:少一格、行寬收斂,信件不被左右拉長)。"""
    i = _SRC.index(">五、加權指數開盤預測</h2>")
    seg = _SRC[i:i + 4000]
    big = seg[seg.index("今天開盤大約落在"):]
    assert "{pct_sign}{final_pct:.2f}%" in big[:big.index("</div>", big.index("font-size:30px"))], \
        "預測 % 沒有跟在大字後面"
    assert ">預測漲跌</div>" not in seg, "獨立的「預測漲跌」格應已移除"
    assert "display:flex" not in seg and "table-layout:fixed" in seg


# ── (3) 廣度緊接第五段 ───────────────────────────────────────────────────
def test_breadth_block_sits_right_below_section_five():
    i = _SRC.index("{taiex_html}")
    j = _SRC.index("{breadth_html}")
    k = _SRC.index("{combined_pred_html}")
    assert i < j < k, "廣度+類股熱度要緊接在第五段之後、0050 之前"
    assert _SRC.count("{breadth_html}") == 1


# ── (4) 來源引用超連結 ───────────────────────────────────────────────────
_NEWS = [
    {"title": "Fed會議紀要:3官員主張立即降息 內部分歧加深 - 鉅亨網",
     "link": "https://news.cnyes.com/news/id/123"},
    {"title": "創意電子董事會通過400億元聯貸案 - 鉅亨網",
     "link": "https://news.cnyes.com/news/id/456"},
    {"title": "台積電先進封裝產能明年翻倍 - 工商時報",
     "link": "https://ctee.com.tw/news/789"},
]


def _linked(html):
    return _link_source_citations(_dim_source_citations(html),
                                  build_news_link_index(_NEWS))


def test_a_confident_citation_becomes_a_link():
    h = _linked("Fed 7月會議紀要偏鷹,3名官員主張立即升息（鉅亨）")
    assert 'href="https://news.cnyes.com/news/id/123"' in h
    assert "text-decoration:underline" in h


def test_an_unrelated_line_stays_unlinked():
    """★錯連比不連糟★:比不出內容重合就維持淡化文字。"""
    h = _linked("完全無關的一句話,講天氣與午餐（鉅亨）")
    assert "href=" not in h


def test_a_media_mismatch_stays_unlinked():
    """內容像但媒體對不上 → 不連(引用寫 CNBC 就不能連到鉅亨的文章)。"""
    h = _linked("Fed 會議紀要偏鷹,官員主張立即升息,分歧加深（CNBC）")
    assert "href=" not in h


def test_no_index_is_a_noop():
    h = _dim_source_citations("台積電先進封裝產能翻倍（工商時報）")
    assert _link_source_citations(h, []) == h


def test_the_confidence_tag_is_never_linked():
    """信心標 [A級・信心:中] 不是來源,不得被連結。"""
    h = _linked("mRNA癌症疫苗三期數據 [A級・信心:中]")
    assert "href=" not in h


# ── (5) 台指期 vs 現貨列已刪 ─────────────────────────────────────────────
def test_the_basis_line_is_gone():
    assert "_basis_line_html(quotes.get" not in _SRC, \
        "台指期 vs 大盤現貨列應已不再渲染(2026-08-20 使用者要求刪除)"


# ── (6) 在地快訊:缺日期 fail-closed ─────────────────────────────────────
def test_local_news_drops_dateless_entries(monkeypatch):
    """2026-08-20 使用者回報:在地快訊出現三個月前的彰基舊聞。
    `if pub and … < cutoff` 是 fail-open —— 缺日期的條目完全不檢查就放行,
    而 Google 重新收錄的舊文正是日期最不可靠的一群。缺日期=不收。"""
    now_parsed = dt.datetime.now(dt.timezone.utc).timetuple()

    class Feed:
        def __init__(self, url):
            # ★fake 要用真 feedparser 的形狀★:有日期的條目帶 published_parsed
            #  (struct_time);缺日期的就是兩個欄位都沒有。
            self.entries = [
                {"title": "彰基總院長遭AI換臉賣護膝"},              # 缺日期 → 不收
                {"title": "彰化鐵路高架化再獲中央補助",
                 "published_parsed": now_parsed},
            ]
    monkeypatch.setattr(mr, "_feedparser_parse_url_with_timeout",
                        lambda url, *a, **k: Feed(url))
    out = mr.fetch_local_news()
    flat = [x["title"] for items in out.values() for x in items]
    assert all("AI換臉" not in t for t in flat), \
        f"★缺日期的舊聞放行了★ {flat}"
    assert any("鐵路高架" in t for t in flat), "有日期的新聞要照收"


def test_old_entries_are_still_cut_by_date(monkeypatch):
    old_parsed = (dt.datetime.now(dt.timezone.utc)
                  - dt.timedelta(days=90)).timetuple()

    class Feed:
        def __init__(self, url):
            self.entries = [{"title": "彰基三個月前的舊聞",
                             "published_parsed": old_parsed}]
    monkeypatch.setattr(mr, "_feedparser_parse_url_with_timeout",
                        lambda url, *a, **k: Feed(url))
    assert mr.fetch_local_news() == {}


# ── (7) 金控雙雄素材 ─────────────────────────────────────────────────────
def test_the_holding_groups_feed_exists():
    q = mr.OTHER_SECTOR_QUERIES.get("金融-金控") or ""
    for kw in ("國泰金", "國泰人壽", "中信金", "中國信託", "台灣人壽"):
        assert kw in q, f"金控查詢缺 {kw}"
    assert "類股-金融-金控" in mr.RSS_FEEDS, "查詢沒有併入 RSS_FEEDS"
    # 信件/prompt 端以一般類股素材呈現:查詢與標籤不得帶「使用者」字眼
    assert "使用者" not in q and "使用者" not in "金融-金控"
