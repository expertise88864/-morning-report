"""11-27 實信的四個問題。"""
import html

import journal_selection as js
import legacy_evidence as le
import morning_report as mr
from sports_quality import news_allowed


def test_conclusion_card_does_not_leak_the_blockquote_marker():
    detail = "\n".join(x for x in ("昨日台股大幅波動:加權 +2.50%。", "> **立場：中性**\n理由：甲。") if x)
    out = mr._render_summary_bar("中性", detail, html)
    assert "&gt;" not in out and "<b>立場：中性</b>" in out and "大幅波動" in out


def test_social_posts_stream_pages_and_index_pages_are_not_sports_news():
    for t in ["CPBL 中華職棒. . #2026亞洲冬季棒球聯盟 海洋生物 #平日免費入場 - Facebook",
              "觀賞北科附工 vs 台南海事：4強賽的賽事直播 | DAZN TW - DAZN",
              "[全部]其他運動 - Now Sports"]:
        assert not news_allowed({"title": t}), t
    assert news_allowed({"title": "澳網》確定了！2屆冠軍辛納因傷缺席 - 自由體育"})
    assert news_allowed({"title": "NBA》#1 順位新秀轟 30 分 - 自由體育"})
    assert news_allowed({"title": "網球》世界 #1 擊敗 #2 奪冠 - 自由體育"})   # 外審 PR #70:排名不是 hashtag


def test_retraction_notices_and_interviews_are_not_listed():
    recs = {"1": {"title": "Retraction and replacement of: \"Real-world effectiveness of AI triage\".",
                  "pubtype": ["Journal Article", "Retraction Notice"]},
            "2": {"title": "Pioneers in dermatology and venereology: An interview with Professor Thomas Bieber.",
                  "pubtype": ["Journal Article"]},
            "3": {"title": "Real-world effectiveness of AI triage on cancer waiting times.", "pubtype": ["Journal Article"]}}
    assert [a["pmid"] for a in js.articles(["1", "2", "3"], recs, "BJD", 5)] == ["3"]
    # 外審 PR #70:標題以 Retraction 開頭的一般研究(鼓膜內陷)不可誤刪;沒有類型時靠明確的通知格式
    ear = {"4": {"title": "Retraction pockets of the tympanic membrane in children", "pubtype": ["Journal Article"]},
           "5": {"title": "Retraction: Effect of X on Y", "pubtype": []}}
    assert [a["pmid"] for a in js.articles(["4", "5"], ear, "AJO", 5)] == ["4"]


def test_a_target_price_is_not_backed_by_an_unrelated_market_number():
    le.reset()
    item = {"title": "花旗給日月光投控 908 元目標價", "source": "TechNews"}
    eid = le.tag(item).strip().strip("[]")
    quotes = {"PRED": {"weighted_final": 2132.35}}
    wrong = f"### 八、科技\n\n日月光投控（3711）：花旗給出 2132 元目標價，先進封裝外溢。[{eid}]\n"
    right = f"### 八、科技\n\n日月光投控（3711）：花旗給出 908 元目標價，先進封裝外溢。[{eid}]\n"
    assert le.MARK in le.audit(wrong, quotes, {}) and le.MARK not in le.audit(right, quotes, {})
    # 外審 PR #70:同一句裡調整前後兩個目標價都只能由新聞佐證
    moved = f"### 八、科技\n\n日月光投控：花旗將目標價由 908 元調高至 2132 元，封裝外溢。[{eid}]\n"
    assert "2132 元" + le.MARK in le.audit(moved, quotes, {})
    # 外審 PR #70 第二輪:千分位不能拆斷;同句的昨收仍由行情佐證
    comma = f"### 八、科技\n\n日月光投控：花旗目標價 2,132 元，封裝外溢。[{eid}]\n"
    assert "2,132 元" + le.MARK in le.audit(comma, quotes, {})
    vs = f"### 八、科技\n\n日月光投控：花旗目標價 908 元較昨收 2132 元低，封裝外溢。[{eid}]\n"
    assert le.MARK not in le.audit(vs, quotes, {})
    # 第三輪:「較原先」不是跟昨收比;同值的漲幅仍由行情佐證
    prior = f"### 八、科技\n\n日月光投控：花旗將目標價較原先的 908 元調高至 2132 元，封裝外溢。[{eid}]\n"
    assert "2132 元" + le.MARK in le.audit(prior, quotes, {})
    same = f"### 八、科技\n\n日月光投控：花旗目標價 908 元且股價今日上漲 908%，量能放大。[{eid}]\n"
    assert le.MARK not in le.audit(same, {"PRED": {"pct": 908.0}}, {})
    # 一般價格仍可由行情佐證
    price = f"### 八、科技\n\n台積電：模型估開盤 2132 元，昨收附近。[{eid}]\n"
    assert le.MARK not in le.audit(price, quotes, {})


def test_a_target_price_is_not_backed_by_official_eps(monkeypatch):
    """外審 PR #70:官方 EPS 12.00 元不能替「目標價 12 元」背書。"""
    import official_numbers
    le.reset()
    eid = le.tag({"title": "外資給甲公司 90 元目標價", "source": "鉅亨"}).strip().strip("[]")
    monkeypatch.setattr(official_numbers, "facts", lambda block: "EPS 12.00 元")
    out = le.audit(f"### 八、科技\n\n甲公司：外資給出目標價 12 元，營運回溫。[{eid}]\n", {}, {})
    assert "12 元" + le.MARK in out


def test_target_price_is_located_by_position():
    """外審 PR #70 第四輪:比較對象寫成「前一交易日收盤價」、同值同幣別的昨收,都不能被當成目標價。"""
    le.reset()
    eid = le.tag({"title": "花旗給日月光投控 908 元目標價", "source": "TechNews"}).strip().strip("[]")
    quotes = {"PRED": {"last": 800.0, "weighted_final": 2132.35}}
    prev = f"### 八、科技\n\n日月光：花旗目標價 908 元較前一交易日收盤價 800 元高。[{eid}]\n"
    assert le.MARK not in le.audit(prev, quotes, {})
    twin = f"### 八、科技\n\n日月光：昨收 2132 元，花旗給出 2132 元目標價。[{eid}]\n"
    out = le.audit(twin, quotes, {})
    assert out.count(le.MARK) == 1 and "2132 元" + le.MARK + "目標價" in out


def test_target_price_round_five_cases():
    """外審 PR #70 第五輪:冒號寫法、長句標在新目標價、新聞證實的目標價可驗算倍數。"""
    le.reset()
    eid = le.tag({"title": "花旗給日月光投控 908 元目標價,昨收 100 元、目標價 200 元", "source": "TechNews"}).strip().strip("[]")
    quotes = {"PRED": {"last": 2132.0, "prev": 100.0}}
    colon = f"### 八、科技\n\n日月光：花旗目標價：2,132 元，封裝外溢。[{eid}]\n"
    assert "2,132 元" + le.MARK in le.audit(colon, quotes, {})
    long = f"### 八、科技\n\n日月光：昨收 2132 元，花旗將目標價較原先的 908 元調高至 2132 元。[{eid}]\n"
    out = le.audit(long, quotes, {})
    assert out.count(le.MARK) == 1 and out.rstrip().split("[")[0].endswith("2132 元" + le.MARK + "。")
    ratio = f"### 八、科技\n\n日月光：花旗目標價 200 元，是昨收 100 元的 2 倍。[{eid}]\n"
    assert le.MARK not in le.audit(ratio, quotes, {})


def test_both_wrong_target_prices_are_marked_where_they_are():
    """外審 PR #70 第六輪:新舊目標價都錯時,先插入的標記不能讓第二個標到昨收。"""
    le.reset()
    eid = le.tag({"title": "花旗給日月光投控 700 元目標價", "source": "TechNews"}).strip().strip("[]")
    text = f"### 八、科技\n\n日月光：昨收 2132 元，花旗將目標價由 900 元調高至 2132 元。[{eid}]\n"
    out = le.audit(text, {"PRED": {"last": 2132.0}}, {})
    assert out.count(le.MARK) == 2 and "昨收 2132 元，" in out
    assert "900 元" + le.MARK in out and "至 2132 元" + le.MARK in out
