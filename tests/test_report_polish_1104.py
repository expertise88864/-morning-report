"""使用者 2026-11-04 看實信後要修的內容問題:每條都釘實信裡的情況。"""
import datetime as dtm
import gzip

import report_polish as rp
import sector_rotation as sr


def _write_mail(tmp_path, date, body):
    with gzip.open(tmp_path / f"{date}.html.gz", "wt", encoding="utf-8") as fh:
        fh.write(body)


def test_reported_earnings_leave_the_calendar_previews_do_not():
    today = dtm.date(2026, 11, 4)
    amd = {"date": today, "time": "盤後(美東)", "title": "AMD 財報"}
    qcom = {"date": dtm.date(2026, 11, 5), "time": "盤後(美東)", "title": "QCOM 財報"}
    nfp = {"date": dtm.date(2026, 11, 6), "time": "21:30", "title": "[USD] 失業率"}
    news = [{"title": "AMD 財報超預期 資料中心營收近翻倍 盤後大漲 8%"}, {"title": "高通本週公布財報 市場關注手機復甦"}]
    assert rp.drop_reported_earnings([amd, qcom, nfp], news, today) == [qcom, nfp]
    far = {"date": dtm.date(2026, 11, 9), "time": "盤後(美東)", "title": "AMD 財報"}
    assert rp.drop_reported_earnings([far], news, today) == [far]      # 下一季的日期不是這一筆
    for preview in ("AMD 今晚盤後公布財報", "AMD 財報前瞻:市場預期營收年增 40%", "AMD earnings preview: revenue tops"):
        assert rp.drop_reported_earnings([amd], [{"title": preview}], today) == [amd], preview   # 外審 PR #41


def test_rotation_arrows_follow_the_sign_of_relative_strength():
    """實信:電機機械相對大盤 -0.8 卻標「▲ 強勢」。"""
    snap = []
    for ind, p5 in (("A", 2.0), ("B", 1.0), ("C", -1.0), ("D", -2.0), ("E", -3.0), ("F", -4.0)):
        snap += [{"industry": ind, "pct_5d": p5} for _ in range(3)]
    rot = sr.sector_rotation(snap)
    assert all(r[2] > 0 for r in rot["strong"]) and {r[0] for r in rot["strong"]} == {"A", "B", "C"}
    assert all(r[2] < 0 for r in rot["weak"]) and {r[0] for r in rot["weak"]} == {"F", "E"}


def test_section_order_and_terms_are_fixed_by_python():
    md = ("## 七、昨夜三大重點\na\n\n## 七之二、世界大事速覽\n荷莫茲海峽\n\n## 八、科技板塊脈動\nb\n\n"
          "## 十、台灣本地動態\nc\n\n## 七之四、敘事變化\nd\n")
    out = rp.order_sections(rp.normalize_terms(md))
    assert "荷莫茲" not in out and "荷姆茲海峽" in out
    assert out.index("七之二") < out.index("七之四") < out.index("八、科技") < out.index("十、台灣")
    assert out.count("七之四") == 1 and out.endswith("c\n\n") or out.endswith("c\n")
    plain = "## 七、昨夜三大重點\na\n"
    assert rp.order_sections(plain) == plain


def test_display_pass_renumbers_scrubs_and_adds_the_grade_note():
    html = ('<h2 style="x">七、昨夜三大重點</h2><p>新聞清單無對應事件</p><h2>七之二、世界大事速覽</h2>'
            '<h2>七之四、敘事變化(昨日觀點)</h2><h2 style="y">八、科技板塊脈動</h2><p>x</p><h2>十、台灣本地動態</h2>')
    out = rp.display_pass(html)
    for want in (">三、昨夜三大重點<", ">四、世界大事速覽<", ">五、敘事變化(昨日觀點)<", ">六、科技板塊脈動<", ">八、台灣本地動態<"):
        assert want in out
    assert "新聞清單" not in out and out.count("A 級＝") == 1
    assert out.index("六、科技") < out.index("A 級＝") < out.index("<p>x</p>")


def test_big_move_is_on_the_card_and_in_the_prompt_only_when_big():
    assert rp.session_pct([44920.14, 43307.54]) == -3.59
    assert rp.session_pct([100, 200]) is None and rp.session_pct([1]) is None
    pred = {"last_close": 43307.54, "last_session_pct": -3.59}
    assert "-3.59%" in rp.taiex_card_text(pred) and rp.taiex_card_text({}) == ""
    rule = rp.big_move_rule(pred, {"advance_ratio": 5.5})
    assert "加權 -3.59%" in rule and "上漲佔比 5.5%" in rule and "不是全面崩潰" in rule
    assert rp.big_move_rule({"last_session_pct": -0.5}, {"advance_ratio": 47}) == ""
    assert "上漲佔比 15.0%" in rp.big_move_rule({}, {"advance_ratio": 15})
    assert "加權 +2.10%" in rp.big_move_rule({"last_session_pct": 2.1}, {})


def test_previous_mail_filters_repeated_news_but_not_new_developments(tmp_path):
    """實信:創意 EPS 上修、BDI 2,648 點、同一筆 OpenRouter 定價連兩天都寫。"""
    mail = ('<a href="https://news/old">鉅亨</a><h2>七、昨夜三大重點</h2>'
            "<p>創意:FactSet 最新調查將 2026 年 EPS 中位數由 54.87 元上修至 56.51 元,預估目標價 8,000 元,鉅亨報導,市場持續關注。</p>"
            "<p>波羅的海乾散貨指數下跌 2.43% 至 2,648 點,各船型運價普跌,散裝船東偏空,對貨櫃航商沒有直接傳導。</p>"
            "<p>AI 模型競賽:OpenRouter 目錄新上架 z-ai/glm-5.3-flashx-legal,輸入每百萬 tokens 0.37 美元,低價模型持續壓低成本。</p>"
            "<h2>台股波段觀察名單</h2><p>PER 14.4 殖利率 3.1 PBR 0.7 大戶持股 66 外資持股 29,這些每天都會再出現的數字不算寫過。</p>")
    _write_mail(tmp_path, "2026-11-03", mail)
    prev = rp.load_previous(tmp_path, dtm.date(2026, 11, 4))
    assert rp.load_previous(tmp_path, dtm.date(2026, 11, 3)) == {}      # 同日重跑不比自己
    news = [{"title": "同一則", "link": "https://news/old"},
            {"title": "FactSet:創意 EPS 由54.87元上修至56.51元", "link": "https://a"},
            {"title": "BDI 跌2.43%至2648點", "link": "https://b"},
            {"title": "創意法說:第四季營收季增 12.5%", "link": "https://c"},
            {"title": "聯發科(2454)與台積電(2330)合作", "link": "https://d"},
            {"title": "某股 PER 14.4、殖利率 3.1", "link": "https://e"}]
    assert [n["link"] for n in rp.fresh_news(news, prev)] == ["https://c", "https://d", "https://e"]
    assert rp.fresh_news(news, {}) == news
    assert not any("PER" in x for x in prev["paragraphs"])
    rows = ["11-02 上架 z-ai/glm-5.3-flashx-legal:輸入 $0.37/M", "11-03 上架 x/new-model:輸入 $1/M"]
    assert rp.fresh_model_rows(rows, prev) == rows[1:]


def test_prompt_rules_for_labels_airlines_and_grades_are_there():
    import morning_report as mr
    import prompt_profiles
    import writing_rules
    src = open(mr.__file__, encoding="utf-8").read()
    assert "標籤要和內容一致" in src and "是航空股" in src and "這則新聞說不出量級" not in src
    rules = open(writing_rules.__file__, encoding="utf-8").read()
    assert "聚合或轉貼網站" in rules and "不用段落編號指路" in rules
    assert "是航空股" in open(prompt_profiles.__file__, encoding="utf-8").read()


def test_build_prompt_skips_news_written_yesterday():
    import morning_report as mr
    from test_deepseek_legacy_golden import legacy_prompt_inputs
    args = list(legacy_prompt_inputs())
    args[3] = [{"title": "昨天寫過的舊聞甲", "link": "https://news/old", "source": "鉅亨", "summary": "", "importance": "high"},
               {"title": "今天才有的新聞乙", "link": "https://news/new", "source": "鉅亨", "summary": "", "importance": "high"}]
    args[0] = {**args[0], "NEWS_REPORTED": {"links": ["https://news/old"], "paragraphs": []}}
    prompt = mr._build_prompt(*args)
    assert "昨天寫過的舊聞甲" not in prompt and "今天才有的新聞乙" in prompt


def test_local_build_section_needs_a_real_project():
    """實信:毒駕爭議、政治人物參香都進了「建設」。"""
    import news_display_quality as q
    assert not q.relevant("建設", "賴清德南投4廟參香力挺溫世政 喊話中央挹注逾300億助地方建設")
    assert not q.relevant("建設", "彰化毒駕事件遭點名 陳素月「零容忍」:勿將不幸當政治消費工具")
    assert q.relevant("建設", "台中捷運藍線動工 預計 2032 年通車")
    assert q.relevant("建設", "斗六長照大樓爭取9億經費")      # 真的地方建設不能被擋


def test_low_tier_wta_finals_and_minor_tournaments_stay_out():
    import html as htmllib
    import render_utils as ru
    final = {"round": "Final", "date": "11/02", "winner": "A", "loser": "B", "score": ""}
    sports = {"tennis": {
        "results": [{**final, "event": "Queretaro Open", "tour": "WTA", "tier": ""},
                    {**final, "event": "Erste Bank Open", "tour": "ATP", "tier": ""},
                    {**final, "event": "Wuhan Open", "tour": "WTA", "tier": ""}],
        "tournaments": [{"name": "Rolex Paris Masters", "status": "進行中", "tier": "1000 級"},
                        {"name": "Reto Win Tennis Cali Open", "status": "進行中", "tier": ""}]},
        "nba_fixtures": [{"when": "11/05 08:00", "text": "A @ B"}]}
    out = ru._render_sports_html(sports, htmllib)
    assert "Queretaro" not in out and "Cali" not in out and "Paris" in out
    assert "Erste Bank" in out and "Wuhan" in out


def test_previous_mail_stops_at_every_ranking_title_and_needs_an_end(tmp_path):
    """外審 PR #41:高分時標題是「台股客觀關注排名」;排名缺席時也不可讀到體育。"""
    para = "<p>這一段是分析內容,裡面有 12.5 與 33.75 兩個數字,要留下來比對隔天的新聞是否重複。</p>"
    tail = "<p>美聯 坦帕灣光芒 98-64 勝率 0.605,洋基 0.578,這是體育戰績,不應該被當成分析段落拿來比對。</p>"
    for marker in ("台股客觀關注排名 Top 3", "Podcast 重點", "體育快訊"):
        _write_mail(tmp_path, "2026-11-03", f"<h2>七、昨夜三大重點</h2>{para}<h2>{marker}</h2>{tail}")
        got = rp.load_previous(tmp_path, dtm.date(2026, 11, 4))["paragraphs"]
        assert any("12.5" in p for p in got) and not any("0.605" in p for p in got), marker
    _write_mail(tmp_path, "2026-11-03", f"<h2>七、昨夜三大重點</h2>{para}{tail}")     # 沒有終點:不比
    assert rp.load_previous(tmp_path, dtm.date(2026, 11, 4))["paragraphs"] == []


def test_sent_mail_is_renumbered_but_archive_and_recap_keep_internal_names(monkeypatch):
    """外審 PR #41:寄後擷取只認「八、科技板塊脈動」,改標題要限於寄出去的那份。"""
    import inspect
    import morning_report as mr
    src = inspect.getsource(mr.deliver_report)
    assert "_fallback_recap.delivered(html," in src and "archive_report_html(html," in src
    assert "html = _polish.display_pass(html)" in inspect.getsource(mr.send_email)   # 函式內的區域變數,不影響呼叫端
    assert rp.display_pass(None) in ("", None)


def test_notable_list_keeps_major_wta_without_touching_tier_labels():
    """外審 PR #41:武漢、年終賽級別是空白,不可被當低級別;但同城市 ATP 的級別標籤不能動。"""
    import morning_report as mr
    for name in ("Wuhan Open", "WTA Finals", "Nitto ATP Finals", "Ningbo Open"):
        assert rp.notable_tennis(name), name
    assert not rp.notable_tennis("Queretaro Open") and not rp.notable_tennis("Reto Win Tennis Cali Open")
    assert mr._tennis_tier("Stuttgart Open") == (2, "") and mr._tennis_tier("Wuhan Open") == (2, "")


def test_results_that_beat_expectations_are_not_previews():
    """外審 PR #41:「優於市場預期」是結果,不是預告。"""
    today = dtm.date(2026, 11, 4)
    amd = {"date": today, "time": "盤後(美東)", "title": "AMD 財報"}
    for done in ("AMD 財報營收年增40%,優於市場預期", "AMD 財報超出分析師預期", "超微財報不如市場預期 年增僅 5%",
                 "AMD 財報營收年增40%,超越市場預期", "AMD 財報優於市場預估", "AMD earnings beat estimates",
                 "AMD 第三季財報營收年增 45%"):
        assert rp.drop_reported_earnings([amd], [{"title": done}], today) == [], done
    for pending in ("AMD 財報 市場預期營收年增 40%", "AMD 財報 分析師預估年增 40%", "AMD 今晚財報 能否優於市場預期",
                    "分析師預估 AMD 財報將優於市場預期", "AMD 財報有望優於市場預期", "analysts expect AMD earnings to beat"):
        assert rp.drop_reported_earnings([amd], [{"title": pending}], today) == [amd], pending
