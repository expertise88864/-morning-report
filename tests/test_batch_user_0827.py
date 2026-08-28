# -*- coding: utf-8 -*-
"""2026-08-27 使用者七項回饋(逐項對應當天實信):

1. 「怎麼沒有 0050 操作建議?」—— 結論卡只有 2330/00662 兩行。
2. 「Fed Chairman Warsh Speaks 後面有中文翻譯」「Prelim Benchmark
   Payrolls Revision 也要有中文翻譯(最好附上這是什麼數據/解釋)」。
3. 信裡出現「晨報 2026/08/27」「立場:中性(系統計分淨分 +3)」
   「11維計分行:」三行格式殘骸。
4. 世界大事速覽:非經濟新聞不需要硬寫「對台股無直接關聯」。
5. MLB 旅外球員要有李灝宇跟鄭宗哲。
6. 「網球有其他更詳細的資料嗎?」—— 加逐盤比分。
7. 週日晨報要有本週完整新聞回顧(週一~週六/後續變化/下週關注)。
"""
import datetime as dt
import io
import json
import re
from pathlib import Path

import econ_terms as et
import morning_report as mr

_ROOT = Path(mr.__file__).resolve().parent


def test_the_conclusion_card_has_a_0050_line():
    """格式塊要有 0050 那一行,而且呼叫端真的填了值(佔位漏填會拋
    KeyError → 整份 prompt 組不出來,那是接線斷掉的形狀)。"""
    assert "{key_0050_line}" in mr._STANCE_FORMAT_BLOCK
    assert "0050 操作建議" in mr._STANCE_FORMAT_BLOCK
    src = io.open(_ROOT / "morning_report.py", encoding="utf-8").read()
    i = src.index("_STANCE_FORMAT_BLOCK.format(")
    assert "key_0050_line=key_0050_line" in src[i:i + 400]
    # 計算端:有預測就給價位;沒有就明說資料未提供(嚴禁編造)
    j = src.index('_t50 = quotes.get("TW0050_PRED")')
    seg = src[j:j + 700]
    assert "pred_open" in seg and "嚴禁編造" in seg


def test_header_debris_is_stripped_but_content_is_kept():
    """實信的三行殘骸要被清掉;帶理由的立場句是內容,不得誤砍。"""
    import llm_postprocess as lp
    debris = ["晨報 2026/08/27",
              "立場：中性（系統計分淨分 +3）",
              "11維計分行：",
              "11 維計分行:"]
    for ln in debris:
        assert (re.search(lp._STANCE_LABEL_ONLY_RE, ln)
                or re.search(lp._HEADER_DEBRIS_RE, ln)), ln
    keep = ["立場：中性,因為利率下行而科技股續強",
            "立場：中性（等待聯準會訊號）",     # 括號理由是內容(r1 外審)
            "晨報今天提到 2026/08/27 的行情",
            "11 維計分行顯示外資期貨部位偏空"]  # 敘述句,不是孤行標題
    for ln in keep:
        assert not re.search(lp._STANCE_LABEL_ONLY_RE, ln), ln
        assert not re.search(lp._HEADER_DEBRIS_RE, ln), ln


def test_calendar_events_are_translated_and_explained():
    assert "聯準會主席" in et.annotate("Fed Chairman Warsh Speaks")
    assert "非農就業基準修正初值" in et.annotate(
        "Prelim Benchmark Payrolls Revision")
    assert "GDP 季增年率修正值" in et.annotate("Prelim GDP q/q")
    # 解說:難懂的有、常見的不硬編
    assert "校正" in et.explain("Prelim Benchmark Payrolls Revision")
    assert et.explain("CPI m/m") == ""
    # 渲染端把解說接進 note(※ 前綴)
    from render_utils import _render_event_calendar_html
    html = _render_event_calendar_html([
        {"date": dt.date(2026, 8, 28), "time": "22:00",
         "title": "Prelim Benchmark Payrolls Revision", "note": "前值 -911K",
         "impact": "high"}])
    assert "非農就業基準修正初值" in html
    assert "※" in html and "校正" in html
    # prompt 端(七之三素材)也帶解說 —— 模型看得到才寫得出來
    txt = mr._format_event_scenarios(
        [{"date": dt.date(2026, 8, 28), "time": "22:00",
          "title": "Prelim Benchmark Payrolls Revision", "note": "前值 -911K"}],
        dt.datetime(2026, 8, 27, 6, 0, tzinfo=mr.TPE))
    assert "非農就業基準修正初值" in txt and "〔" in txt


def test_world_events_rule_forbids_forced_market_relevance():
    """非經濟新聞不硬扯市場 —— 兩條路(legacy + Luna)都要說。"""
    wr = io.open(_ROOT / "writing_rules.py", encoding="utf-8").read()
    i = wr.index("七之二、世界大事速覽")
    seg = wr[i:i + 3200]
    # 規則文字會被排版換行切斷 —— 比對前把空白壓掉
    flat = re.sub(r"\s+", "", seg)
    assert "對台股無直接關聯" in flat, "規則沒寫進七之二那一段"
    assert "若是經濟大事才需要" in flat, flat[:200]
    import analysis_schema as sch
    desc = (sch.ANALYSIS_OUTPUT_SCHEMA["properties"]["world_events"]
            ["items"]["properties"]["what_next"]["description"])
    assert "對台股無直接" in desc and "禁止" in desc


def test_mlb_roster_includes_the_two_new_players():
    roster = mr._mlb_tw_players()
    assert roster.get("Hao-Yu Lee") == "李灝宇"
    assert roster.get("Tsung-Che Cheng") == "鄭宗哲"
    assert roster.get("Kai-Wei Teng") == "鄧愷威"   # 既有的不得被擠掉


def test_tennis_results_carry_set_scores(monkeypatch):
    """ESPN 的 `linescores` 實測有逐盤與 tiebreak。勝方視角;兩側盤數
    對不齊(退賽/缺漏)就不顯示 —— 半截比分比沒有比分更誤導。"""
    comp = {
        "id": "c1", "date": "2026-08-27T01:00Z",
        "status": {"type": {"completed": True}},
        "round": {"displayName": "Semifinal"},
        "competitors": [
            {"winner": True, "athlete": {"shortName": "A. Winner"},
             "linescores": [{"value": 7.0, "tiebreak": 7},
                            {"value": 6.0}]},
            {"winner": False, "athlete": {"shortName": "B. Loser"},
             "linescores": [{"value": 6.0, "tiebreak": 3},
                            {"value": 4.0}]}]}
    ev = {"shortName": "US Open", "date": "2026-08-27T01:00Z",
          "status": {"type": {"state": "in"}},
          "groupings": [{"grouping": {"slug": "mens-singles"},
                         "competitions": [comp]}]}

    class _R:
        def raise_for_status(self):
            pass

        def json(self):
            return {"events": [ev]}

    monkeypatch.setattr(mr, "_http_get", lambda *a, **k: _R())
    out = mr.fetch_tennis_digest(dt.datetime(2026, 8, 27, 7, 0, tzinfo=mr.TPE))
    res = [r for r in out.get("results") or [] if r.get("winner") == "A. Winner"]
    assert res, out
    assert res[0]["score"] == "7-6(3) 6-4", res[0]
    # 盤數不齊 → 空字串(渲染端據此整段不顯示)
    # **兩側都有、但長度不同**(退賽的常見形狀)。反例不能用空清單 ——
    # `zip(x, [])` 湊巧也回空,「硬拼」的突變照樣綠(第一版就是這樣)。
    comp2 = dict(comp, id="c2", competitors=[
        {"winner": True, "athlete": {"shortName": "C. Ret"},
         "linescores": [{"value": 6.0}, {"value": 3.0}]},
        {"winner": False, "athlete": {"shortName": "D. Out"},
         "linescores": [{"value": 4.0}]}])
    ev["groupings"][0]["competitions"] = [comp2]
    out2 = mr.fetch_tennis_digest(dt.datetime(2026, 8, 27, 7, 0, tzinfo=mr.TPE))
    res2 = [r for r in out2.get("results") or [] if r.get("winner") == "C. Ret"]
    assert res2 and res2[0]["score"] == "", res2


def test_week_review_prompt_is_built_from_state_only(monkeypatch):
    """素材全部來自 state(不另外抓網路);外部標題要進圍欄、規則在
    圍欄外;整週沒素材回空字串(段落省略,不硬寫)。"""
    monkeypatch.setattr(mr, "load_history_state", lambda *a, **k: [
        {"date": "2026-08-24", "stance_label": "中性",
         "critical_news": ["Fed Faces High Stakes Test at Jackson Hole"]},
        {"date": "2026-08-25", "stance_label": "偏空",
         "critical_news": ["Trump says U.S. will hike Canada auto tariffs"]},
        {"date": "2026-08-20", "stance_label": "偏多",
         "critical_news": ["上週的,不該出現"]}])
    monkeypatch.setattr(mr, "EVENT_TIMELINE_FILE",
                        _ROOT / "state" / "nope.json")
    now = dt.datetime(2026, 8, 30, 7, 0, tzinfo=mr.TPE)   # 週日
    p = mr._build_week_review_prompt(now)
    assert "<UNTRUSTED_SOURCE_DATA>" in p
    assert "2026-08-24" in p and "Jackson Hole" in p
    assert "偏空" in p
    assert "上週的,不該出現" not in p, "週界沒切乾淨"
    # 規則在圍欄外(圍欄內的指令會被「一律忽略」那句廢掉)
    fence_end = p.index("</UNTRUSTED_SOURCE_DATA>")
    assert "下週關注方向" in p[fence_end:]
    # 整週沒素材 → 空(新部署的第一週不硬寫)
    monkeypatch.setattr(mr, "load_history_state", lambda *a, **k: [])
    assert mr._build_week_review_prompt(now) == ""


def test_week_review_material_alone_triggers_the_sunday_email(monkeypatch):
    """r1 外審 P1:沒有新 podcast、沒有賽果、沒有警報的安靜週日,
    `_weekend_digest_has_content` 回 False → 整封信不寄 —— 使用者要的
    本週回顧**永遠出不來**。素材存在本身就是寄信的理由(判斷讀 state,
    不呼叫 LLM)。"""
    now = dt.datetime(2026, 8, 30, 7, 0, tzinfo=mr.TPE)
    assert mr._weekend_digest_has_content(
        {}, [], [], now, week_review_ready=True) is True
    # 素材也沒有的話照舊不寄(新部署的第一週)
    assert mr._weekend_digest_has_content(
        {}, [], [], now, week_review_ready=False) is False
    # 接線:呼叫端在早退判斷前真的算了素材、傳了進去
    src = io.open(_ROOT / "morning_report.py", encoding="utf-8").read()
    i = src.index("_wr_ready = bool(_build_week_review_prompt(now_tpe))")
    j = src.index("if not _weekend_digest_has_content", i)
    assert i < j and "week_review_ready=_wr_ready" in src[j:j + 400]


def test_a_corrupt_timeline_leaves_a_trace_not_silence(monkeypatch, tmp_path):
    """r1 外審 P3:timeline 壞檔先前被 `except: pass` 靜默吞掉 ——
    週回顧少了延燒事件骨架而沒有人知道為什麼。照既有規約登記壞檔;
    素材少一塊仍可寫(不擋段落)。"""
    bad = tmp_path / "event_timeline.json"
    bad.write_text("{壞掉", encoding="utf-8")
    monkeypatch.setattr(mr, "EVENT_TIMELINE_FILE", bad)
    monkeypatch.setattr(mr, "load_history_state", lambda *a, **k: [
        {"date": "2026-08-24", "stance_label": "中性",
         "critical_news": ["某則新聞"]}])
    mr._DEGRADED_STEPS.clear()
    p = mr._build_week_review_prompt(
        dt.datetime(2026, 8, 30, 7, 0, tzinfo=mr.TPE))
    assert p and "某則新聞" in p, "壞 timeline 不得把整段素材拖垮"
    assert any("event_timeline" in d for d in mr._DEGRADED_STEPS),         mr._DEGRADED_STEPS
    # **合法 JSON、壞的巢狀列**(r2 外審):`days: "unknown"` 先前讓整個
    # 延燒段在 `int()` 拋錯後被 `pass` 吞掉。壞一列跳一列、留痕,
    # 好的那列照樣進素材。
    ok = tmp_path / "tl2.json"
    ok.write_text(json.dumps({
        "z": [],                            # 非 dict 的列也是壞列(r3 外審)
        "a": {"days": "unknown", "last_seen": "2026-08-26"},
        "b": {"days": 4, "last_seen": "2026-08-26",
              "latest_title": "好的那列", "subjects": ["伊朗"],
              "action": "sanction"}}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(mr, "EVENT_TIMELINE_FILE", ok)
    mr._DEGRADED_STEPS.clear()
    p2 = mr._build_week_review_prompt(
        dt.datetime(2026, 8, 30, 7, 0, tzinfo=mr.TPE))
    assert "好的那列" in p2, "壞列把好列一起拖垮了"
    assert "weekend_week_review_rows" in mr._DEGRADED_STEPS


def test_week_review_is_wired_into_the_sunday_email():
    """接線:抓取有 try(失敗整段省略)、渲染端收參數、body 順序在政策
    解析之後。空字串不得長出空標題。"""
    src = io.open(_ROOT / "morning_report.py", encoding="utf-8").read()
    i = src.index("def run_weekend_digest")
    seg = src[i:src.index("def ", i + 10)]
    assert "analyze_week_in_review(now_tpe)" in seg
    assert "week_review_html=week_review_html" in seg
    j = src.index("def render_weekend_digest_html")
    seg2 = src[j:j + 2600]
    assert "week_review_html: str = \"\"" in seg2
    assert (seg2.index("policy_analysis_html,")
            < seg2.index("week_review_html,")
            < seg2.index("local_news_html,")), "段落順序不對"
    assert mr._render_week_review_html("", None) == ""
    html = mr._render_week_review_html("### 本週大事回顧\n- 測試", None)
    assert "本週回顧與下週展望" in html and "測試" in html


# ─────────── 2026-08-28 實信:兩項回饋只修了一半 ───────────
def test_the_calendar_explanation_survives_the_currency_prefix():
    """**測試用了生產不會產生的形狀。** 08/27 那批我用
    `title='Prelim Benchmark Payrolls Revision'` 驗過解說會出現,而生產的
    日曆標題帶幣別前綴 —— `[USD] Prelim Benchmark Payrolls Revision`。
    `annotate` 是掃描所以翻譯照出,`explain` 是精確查表所以**解說整批不見**:
    08/28 實信的那一列只有「前值 -911K」,沒有 ※。
    """
    import econ_terms as et
    for title in ("[USD] Prelim Benchmark Payrolls Revision",
                  "Prelim Benchmark Payrolls Revision",
                  "[USD] Fed Chairman Warsh Speaks"):
        assert et.explain(title), title
    assert et.explain("[USD] CPI m/m") == ""        # 常見的不硬編
    assert et.explain("[USD] 完全不認得的東西") == ""
    # 走生產的渲染路徑(帶前綴)
    from render_utils import _render_event_calendar_html
    html = _render_event_calendar_html([
        {"date": dt.date(2026, 8, 28), "time": "22:00",
         "title": "[USD] Prelim Benchmark Payrolls Revision",
         "note": "前值 -911K", "impact": "high"}])
    assert "非農就業基準修正初值" in html
    assert "※" in html and "校正" in html, html[-200:]


def test_the_date_first_header_debris_is_stripped_too():
    """08/28 實信:`## 2026-08-28 晨報` 被 `_md_to_html` 渲染成 24px 大標題
    進了信裡。08/27 那批我只寫了「晨報+日期」的排法 —— 同一種殘骸換個
    順序就漏掉。日期片段抽出來,兩種排法共用。"""
    import llm_postprocess as lp
    for ln in ("2026-08-28 晨報", "## 2026-08-28 晨報", "晨報 2026/08/27",
               "2026/08/28 晨報", "晨報 2026-08-28"):
        assert re.search(lp._HEADER_DEBRIS_RE, ln), ln
    # 敘述句仍是內容(日期出現在句子裡不算殘骸)
    for ln in ("晨報今天提到 2026/08/27 的行情",
               "2026-08-28 晨報的重點是輝達財報"):
        assert not re.search(lp._HEADER_DEBRIS_RE, ln), ln
