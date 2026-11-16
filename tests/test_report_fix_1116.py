"""11-15、11-16 實信:週日回顧的編號清單擠成一段與寫稿說明外露;類股法人買超被標成未核實。"""
import html

import legacy_evidence as le
import morning_report as mr
import week_review as wr


def test_week_review_numbered_items_become_a_list_and_meta_notes_go():
    text = ("### 本週大事回顧\n1. 美國通膨：CPI 月增 0.4% 高於預期。\n2. 輝達風暴延伸到出口管制。\n3. 以色列選舉。\n\n"
            "---\n### 下週關注方向\n已知日期事件：素材未提供下週已排定日期事件，故省略。 未定期觀察條件：\n\n- Fed 升息路徑\n")
    out = wr.readable(text)
    assert "- 美國通膨：CPI 月增 0.4% 高於預期。\n- 輝達風暴延伸到出口管制。\n- 以色列選舉。" in out
    assert "素材未提供" not in out and "故省略" not in out and "未定期觀察條件" not in out and "---" not in out
    assert "- Fed 升息路徑" in out and "0.4%" in out
    page = mr._render_week_review_html(text, html)
    assert page.count("<li") == 4 and "1. 美國通膨" not in page and "本週回顧與下週展望" in page
    # 有已知日期事件時兩個小標都留,各自一行
    dated = "### 下週關注方向\n已知日期事件：11/18 輝達財報。 未定期觀察條件：\n\n- Fed 升息路徑\n"
    kept = wr.readable(dated)
    assert "已知日期事件：11/18 輝達財報。\n\n未定期觀察條件：\n\n- Fed 升息路徑" in kept
    assert wr.readable("") == "" and wr.readable("一般段落 2026. 年份不是編號") == "一般段落 2026. 年份不是編號"
    assert mr._render_week_review_html("", html) == ""


def test_yi_fields_are_always_market_evidence_even_past_the_budget():
    """類股法人買超 71.1 億排在兩萬個行情數字之後:原本到上限就停,「71.1 億元」被標成未核實。"""
    quotes = {"SECTOR_HEAT": {"sectors": {"金融保險業": {"inst_net_yi": 71.1, "value_yi": 280.0}}},
              "ZZ_BIG": [{"v": float(i)} for i in range(200)] * 1}
    quotes["ZZ_MANY"] = {f"k{i}": {f"j{j}": i * 1000 + j + 0.5 for j in range(100)} for i in range(300)}
    market = le._market_values(quotes, budget=50)
    assert any(isinstance(v, le._Yi) and abs(v - 71.1) < 1e-9 for v in market)
    assert sum(1 for v in market if not isinstance(v, le._Yi)) <= 50
    le.reset()
    le.tag({"title": "中信金前 10 月 EPS 3.45 元 - 鉅亨", "link": "https://news.example/ctbc"})
    manifest = {}
    out = le.audit("## 九、其他類股資訊\n**【金融｜台灣】中信金（2891）**：EPS 3.45 元,類股法人買超 71.1 億元 [鉅亨][N1]\n",
                   quotes, manifest)
    assert "71.1 億元（未核實）" not in out and manifest["llm"]["legacy_evidence"]["marked"] == 0
    le.reset()


def test_review_pr54_dated_events_survive_even_when_they_mention_absence():
    dated = "### 下週關注方向\n已知日期事件：11/18 輝達財報，觀察是否仍無中國市場營收。 未定期觀察條件：\n\n- Fed 升息路徑\n"
    out = wr.readable(dated)
    assert "已知日期事件：11/18 輝達財報，觀察是否仍無中國市場營收。" in out and "未定期觀察條件：" in out
    for meta in ("已知日期事件：素材未提供下週已排定日期事件，故省略。", "已知日期事件：無。", "已知日期事件：本週沒有。"):
        assert "已知日期事件" not in wr.readable(f"### 下週關注方向\n{meta} 未定期觀察條件：\n\n- 甲\n")
