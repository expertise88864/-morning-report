# -*- coding: utf-8 -*-
"""2026-09-03 使用者對當天實信的七項回饋。

  ① 警特報要標縣市,而且只要台中/彰化/南投/雲林
  ② 刪「早安,交易日…2330 預測:…採簡化版」那段前言
  ③ 刪「七之五、多空交鋒」
  ④ MLB 賭盤排版比照中職那一行
  ⑤ 一週天氣在 iPhone 上讀不了
  ⑥ 刪結論卡的 2330/00662/0050 三行操作建議
  ⑦ 晨報以台灣經濟為主,不要以 00662 為主詞;⑧ 前後連貫
"""
import html as htmllib
import io
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
import analysis_render as ar                                   # noqa: E402
import llm_postprocess as lp                                   # noqa: E402
import morning_report as mr                                    # noqa: E402
import writing_rules as wr                                     # noqa: E402

_SRC = (_ROOT / "morning_report.py").read_text(encoding="utf-8")


# ------------------------------------------------------------------ ②
def test_the_preamble_before_the_first_heading_never_reaches_the_letter():
    """模型自加的問候與內部試算附註。R18 早就禁止,9/3 照樣出現 ——
    **prompt 指令是請求不是保證**,要擋得住得在 Python 擋。"""
    nl = chr(10)
    t = ("早安,交易日 2026/09/03" + nl
         + "2330 預測:昨收 2385 元,校正相互抵銷;00662 合理估值資料有限,採簡化版,僅供參考。" + nl
         + "## 七、昨夜三大重點" + nl + "- 美國對半導體加徵新關稅")
    out = lp._strip_preamble_before_first_heading(t)
    assert out.startswith("## 七、"), out
    assert "早安" not in out and "簡化版" not in out
    assert "美國對半導體加徵新關稅" in out          # 正文一個字都不少
    # 沒有任何標題的輸出原樣保留 —— 整封砍掉比留著更糟
    assert lp._strip_preamble_before_first_heading("整封沒有標題") == "整封沒有標題"
    # 接線:渲染管線第一步就是它
    i = _SRC.index("analysis_for_render = _strip_preamble_before_first_heading(analysis)")
    assert i < _SRC.index("analysis_for_render = _strip_llm_watchlist_section(")
    # prompt 也明講(雙保險,但守衛是上面那個)
    assert "輸出的第一行必須就是下面這個標題" in wr.LEGACY_RULES


# ------------------------------------------------------------------ ③
def test_the_debate_section_is_removed_everywhere():
    """七之五 三個出口都要關:legacy prompt、Luna 渲染、以及它的 sanitizer。"""
    assert not hasattr(ar, "SECTION_BULLBEAR")
    assert not hasattr(lp, "_sanitize_debate_section")
    assert "_sanitize_debate_section" not in _SRC
    assert "多空交鋒" not in wr.LEGACY_RULES
    # 週報段補上七之五,編號連續(平日 七之四 → 八)
    assert "## 七之五、近期預測檢討" in _SRC and "七之六" not in _SRC


# ------------------------------------------------------------------ ⑥
def test_the_conclusion_card_keeps_only_stance_reason_and_risk():
    blk = mr._STANCE_FORMAT_BLOCK
    assert "**第 1 行" in blk and "**第 2 行" in blk and "**第 3 行" in blk
    for gone in ("第 4-6 行", "開盤關鍵價位", "00662 操作建議", "0050 操作建議",
                 "上三行的價位數字由 Python 計算"):
        assert gone not in blk, gone
    assert "主要風險" in _SRC[_SRC.index("{_stance_format_block}"):][:120]


# ------------------------------------------------------------------ ⑦⑧
def test_the_letter_is_taiwan_first_and_coherent():
    persona = _SRC[_SRC.index("你是嚴謹但敢於下判斷的"):][:200]
    assert "重押 00662" not in persona and "以台股為核心" in persona
    assert "偏多操作 00662" not in _SRC
    for old in ("傳導到 2330/00662 的機制", "硬扯 2330/00662", "避免重壓 00662"):
        assert old not in wr.LEGACY_RULES, old
    # R16c:結論卡不得引入正文沒出現的驅動因子;同一事件全信一個方向
    assert "R16c." in wr.LEGACY_RULES
    assert "結論卡不得引入正文沒出現的驅動因子" in wr.LEGACY_RULES
    assert "同一個事件,全信只能有一個方向" in wr.LEGACY_RULES
    # Luna 那條路也有同一條(規則放在圍欄外的 profile 裡)
    assert "整封信只講一個故事" in io.open(_ROOT / "prompt_profiles.py",
                                          encoding="utf-8").read()


# ------------------------------------------------------------------ ①
def test_cwa_alerts_show_counties_and_only_the_four_that_matter(monkeypatch):
    import datetime as dtm
    import email.utils as eut
    fresh = eut.format_datetime(dtm.datetime.now(dtm.timezone.utc))
    rss = f"""<rss><channel>
      <item><title>09/03 07:05 發布大雨特報</title>
        <description><![CDATA[ 基隆北海岸、屏東、宜蘭、臺東地區有局部大雨。 ]]></description>
        <link>https://cwa/a</link><pubDate>{fresh}</pubDate></item>
      <item><title>09/03 10:47 發布陸上強風特報</title>
        <description><![CDATA[ 今(3)日雲林縣、南投縣局部地區有平均風6級以上。 ]]></description>
        <link>https://cwa/b</link><pubDate>{fresh}</pubDate></item>
      <item><title>09/03 06:13 發布海上颱風警報</title>
        <description><![CDATA[ 臺灣海峽、巴士海峽。 ]]></description>
        <link>https://cwa/c</link><pubDate>{fresh}</pubDate></item>
    </channel></rss>""".encode("utf-8")
    monkeypatch.setattr(mr, "_http_get_relaxed_strict", lambda *a, **k: rss)
    got = mr.fetch_cwa_alerts()
    titles = [a["title"] for a in got]
    assert "09/03 07:05 發布大雨特報" not in titles, "別的縣市的特報還在"
    assert got[0]["counties"] == ["南投", "雲林"]            # 固定順序、去重
    assert got[1]["typhoon"] is True and got[1]["counties"] == []   # 颱風例外
    h = mr._render_weather_html([], [], got)
    assert "陸上強風特報（南投、雲林）" in h, h
    assert "🌀 氣象署:09/03 06:13 發布海上颱風警報</a>" in h   # 沒縣市就不加括號


# ------------------------------------------------------------------ ⑤
def test_the_week_forecast_is_a_table_one_day_per_row():
    def _loc(name, base):
        return {"name": name, "t_min": 25, "t_max": 30, "rain_prob": 90, "label": "雷雨",
                "week": [{"date": f"2026-09-{d:02d}", "wd": "一二三四五六日"[d % 7],
                          "t_min": base, "t_max": base + 6, "rain_prob": 10 * d}
                         for d in range(3, 11)]}
    h = mr._render_weather_html([_loc("彰化市", 24), _loc("台中北區", 23)])
    table = h[h.index("<table"):h.index("</table>")]
    assert table.count("<tr>") == 8                       # 表頭 + 七天
    assert "09/03" not in table and "09/04" in table       # 今天不在,明天起
    assert "24~30°" in table and "23~29°" in table         # 兩地各自一欄
    assert "彰化市" in table and "台中北區" in table
    # 一地缺資料時整張表仍然出得來(不會因為一格而整卡消失)
    h1 = mr._render_weather_html([_loc("彰化市", 24)])
    assert h1.count("<tr>") == 8


# ------------------------------------------------------------------ ④
def test_mlb_series_odds_render_as_one_line():
    sports = {"mlb_fixtures": [
        {"text": "BOS @ BAL", "when": "09/04 07:15",
         "odds": "賭盤:紅襪 53%・金鶯 47%(Polymarket)"},
        {"text": "BOS @ BAL", "when": "09/05 07:15",
         "odds": "賭盤:紅襪 52%・金鶯 48%(Polymarket)"}]}
    h = mr._render_sports_html(sports, htmllib)
    odds = h[h.index("賭盤:"):h.index("(Polymarket)")]
    assert "<div" not in odds and "<br" not in odds, odds   # 真的只有一行
    assert "09/04:紅襪 53% ・ 金鶯 47%" in odds and ";09/05:紅襪 52% ・ 金鶯 48%" in odds


# ------------------------------------------------ Codex r1:四條都是這批新碼的洞
def test_the_preamble_guard_only_trusts_h2_sections():
    """模型常先吐 `# 台股晨報` / `### 今日摘要` 再接正文 —— 任意層級都算標題
    的話,守衛會停在那個前言標題上,問候與附註連同它一起進信。"""
    nl = chr(10)
    t = ("# 台股晨報" + nl + "早安,交易日 2026/09/03" + nl + "### 今日摘要" + nl
         + "2330 預測:採簡化版,僅供參考。" + nl + "## 七、昨夜三大重點" + nl + "- x")
    out = lp._strip_preamble_before_first_heading(t)
    assert out.startswith("## 七、"), out
    assert "台股晨報" not in out and "今日摘要" not in out and "簡化版" not in out
    # 完全沒有 H2 的輸出原樣保留(整封砍掉比留著更糟)
    keep = "### 只有三級標題" + nl + "內文"
    assert lp._strip_preamble_before_first_heading(keep) == keep


def test_a_habitually_echoed_debate_section_is_stripped_at_render():
    """prompt 不再要求 ≠ 模型不再寫。舊章節照習慣吐回來時,渲染端要確定性移除
    —— 而且要**真的走 `render_html()`**,不是只驗 prompt 與函式名。"""
    from tests.test_markdown import _full_quotes
    analysis = ("## 七、昨夜三大重點" + chr(10) + "- 美國對半導體加徵新關稅" + chr(10)
                + "## 七之五、多空交鋒" + chr(10)
                + "- **多方最強**：美股終結連三黑" + chr(10)
                + "- **空方最強**：外資台指期淨空單再增" + chr(10)
                + "## 八、科技板塊脈動" + chr(10) + "台積電 CoWoS 擴產。" + chr(10)
                + "## 十一、我的明確立場" + chr(10) + "> **立場：中性**" + chr(10)
                + "## 十二、一句話總結" + chr(10) + "中性觀望")
    html = mr.render_html(_full_quotes(), {"error": "x"}, {"error": "x"}, analysis,
                          "2026-09-03", "每日報")
    assert "多空交鋒" not in html and "多方最強" not in html and "空方最強" not in html
    assert "美國對半導體加徵新關稅" in html and "CoWoS 擴產" in html   # 鄰段完好


def test_the_week_table_aligns_by_date_not_by_index():
    """`fetch_weather()` 對缺欄位的那一天是**跳過** —— 只有一地缺某天時,
    索引對齊會把兩個不同日期排在同一列、還套上其中一地的日期標籤。"""
    def _wk(base, skip=None):
        return [{"date": f"2026-09-{d:02d}", "wd": "一二三四五六日"[d % 7],
                 "t_min": base, "t_max": base + 6, "rain_prob": 10 * (d - 3)}
                for d in range(3, 11) if d != skip]
    a = {"name": "彰化市", "t_min": 25, "t_max": 30, "rain_prob": 90, "label": "雷雨",
         "week": _wk(24)}
    b = dict(a, name="台中北區", week=_wk(23, skip=6))   # 台中缺 09/06
    h = mr._render_weather_html([a, b])
    table = h[h.index("<table"):h.index("</table>")]
    rows = table.split("<tr>")[2:]                       # 去掉表頭
    assert len(rows) == 7
    r06 = next(r for r in rows if "09/06" in r)
    assert "24~30°" in r06 and "—" in r06, r06              # 彰化有、台中留白
    r07 = next(r for r in rows if "09/07" in r)
    assert r07.count("~") == 2 and "23~29°" in r07, r07      # 之後沒有前移
    assert "23~29°" not in r06


def test_the_luna_coherence_rule_names_real_schema_fields():
    """規則約束不存在的欄位等於沒約束(Codex r1:我寫了 `key_risks`,
    schema 裡是 `portfolio_implications.risks`)。用真實 schema 驗欄位名。"""
    import analysis_schema as sch
    text = io.open(_ROOT / "prompt_profiles.py", encoding="utf-8").read()
    # 錨在**規則那一條**上:同一句也出現在 LUNA_XHIGH_VERSION 的版本註解裡,
    # 取第一個出現處會切到註解而不是規則(自測第一版就抓到這個)。
    seg = text[text.index("- **整封信只講一個故事**:"):][:1200]
    assert "key_risks" not in seg
    pi = sch.ANALYSIS_OUTPUT_SCHEMA["properties"]["portfolio_implications"]["properties"]
    for field in ("risks", "summary", "actions_to_consider"):
        assert field in pi, field
        assert field in seg, field
    assert "executive_summary" in sch.ANALYSIS_OUTPUT_SCHEMA["properties"]
    assert "executive_summary" in seg
    # Codex r2:`scenario_tree.invalidation_triggers` 也會被渲染進結論卡
    # (analysis_render 的失效條件那幾行)—— 規則與測試都要涵蓋它
    render_src = io.open(_ROOT / "analysis_render.py", encoding="utf-8").read()
    assert "invalidation_triggers" in render_src
    assert "scenario_tree.invalidation_triggers" in seg
