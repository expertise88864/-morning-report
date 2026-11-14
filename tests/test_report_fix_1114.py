"""11-14 實信:DeepSeek 以內容風險拒收整份提示詞 → 沒有模型分析;亞冠賽隊名欄位改成中文後賽程不見。"""
import datetime as dt
import pathlib

import pytest

import apbc
import content_risk as cr
import degradation_registry as registry

REFUSAL = RuntimeError('DeepSeek 所有模型皆失敗: HTTP 400: {"error":{"message":"Content Exists Risk (request_id: x)"}}')
PROMPT = ("R11. 重大地緣政治事件(川習會、台海、間諜)必須分析。\n"
          "- 寫稿規則:提到中國政策時要寫出處。\n"
          "- [N1] [來源A:鉅亨] 輝達董事會啟動調查\n"
          "- [N2] [來源A:大紀元] 法輪功學員在紐約集會\n"
          "- [N3] [來源A:路透] 白宮討論對中國晶片出口管制\n"
          "- [N4] [來源A:CNBC] Nvidia spy scandal draws in Commerce Secretary\n"
          "- 沒有編號的清單列提到習近平\n"
          "- [N5] [來源A:鉅亨] 台達電 10 月營收創高\n")


def test_soften_drops_only_news_rows_and_widens_by_level():
    one, dropped = cr.soften(PROMPT, 1)
    assert dropped == 1 and "[N2]" not in one and "[N3]" in one and "[N4]" in one
    assert "R11. 重大地緣政治事件" in one and "寫稿規則:提到中國政策" in one and "沒有編號的清單列" in one
    two, dropped = cr.soften(PROMPT, 2)
    assert dropped == 4 and all(tag not in two for tag in ("[N2]", "[N3]", "[N4]", "習近平"))
    assert "[N1]" in two and "[N5]" in two and "R11. 重大地緣政治事件" in two and "寫稿規則:提到中國政策" in two
    assert cr.soften("沒有清單列", 2) == ("沒有清單列", 0)


def test_call_retries_with_fewer_news_and_reports_the_prompt_it_used():
    sent, degraded, logs = [], [], []

    def send(prompt):
        sent.append(prompt)
        if "[N2]" in prompt:
            raise REFUSAL
        return "分析"
    text, used = cr.call(send, PROMPT, degraded.append, logs.append)
    assert text == "分析" and "[N2]" not in used and "[N3]" in used and len(sent) == 2
    assert degraded == [cr.LABEL] and "拿掉 1 則" in logs[0]
    assert cr.LABEL in registry.KNOWN_DEGRADED

    # 第一級不夠就第二級;兩級都被拒 → 原樣拋出(呼叫端走備援文字)
    sent.clear()
    degraded.clear()

    def strict(prompt):
        sent.append(prompt)
        if "spy" in prompt:
            raise REFUSAL
        return "分析二"
    assert cr.call(strict, PROMPT, degraded.append, lambda *_: None)[0] == "分析二" and len(sent) == 3
    with pytest.raises(RuntimeError, match="Content Exists Risk"):
        cr.call(lambda _p: (_ for _ in ()).throw(REFUSAL), PROMPT, degraded.append, lambda *_: None)


def test_other_errors_and_normal_runs_are_untouched():
    degraded = []
    assert cr.call(lambda p: "ok", PROMPT, degraded.append) == ("ok", PROMPT) and not degraded
    with pytest.raises(TimeoutError):
        cr.call(lambda _p: (_ for _ in ()).throw(TimeoutError("慢")), PROMPT, degraded.append)
    with pytest.raises(RuntimeError, match="Content Exists Risk"):   # 沒有東西可拿掉:不重送
        cr.call(lambda _p: (_ for _ in ()).throw(REFUSAL), "沒有新聞列", degraded.append)
    assert not degraded and cr.is_refusal('{"message":"Content Exists Risk"}') and not cr.is_refusal("HTTP 400: bad param")


def test_content_risk_does_not_trigger_the_slim_retry():
    src = pathlib.Path(cr.__file__).with_name("morning_report.py").read_text(encoding="utf-8")
    assert "if code == 400 and not slim and not _content_risk.is_refusal(body):" in src
    assert "text, prompt = _content_risk.call(_call_llm_text, prompt, _DEGRADED_STEPS.append)" in src


def _ls(date, time, road, road_abr, home, home_abr, rr="-", hr="-"):
    return (f"{{{{linescore\n|Date= {date}\n|Time= {time}\n|Road= '''{{{{bb|{road}}}}}'''\n|RoadAbr= {road_abr}\n"
            f"|R1=0|RR={rr}|RH=1|RE=0\n|Home= {{{{bb|{home}}}}}\n|HomeAbr= {home_abr}\n|H1=0|HR={hr}|HH=1|HE=0\n}}}}\n")


def test_apbc_reads_team_from_the_template_or_chinese_names():
    """比賽打完後維基把 RoadAbr 從「TPE」改成「中華台北」:11-13、11-14 的信只剩「待定 vs 待定」。"""
    page = ("== 預賽 ==\n" + _ls("2026年11月12日", "19:00", "JPN", "日本", "TPE", "中華台北", "5", "0")
            + _ls("2026年11月13日", "12:00", "TPE", "中華台北", "AUS", "澳洲", "6", "4")
            + "=== 季軍賽 ===\n" + _ls("2026年11月15日", "11:00", "AUS", "澳洲", "TPE", "中華台北")
            + "=== 冠軍賽 ===\n" + _ls("2026年11月15日", "19:00", "KOR", "韓國", "JPN", "日本"))
    games = apbc.parse(page)
    assert [(g["away"], g["home"]) for g in games] == [("JPN", "TPE"), ("TPE", "AUS"), ("AUS", "TPE"), ("KOR", "JPN")]
    out = apbc.select(games, dt.datetime(2026, 11, 15, 6, 0, tzinfo=apbc.TPE))
    assert [(r["stage"], r["away"], r["home"], r["score"]) for r in out["rows"]] == [
        ("預賽", "日本", "中華隊", (5, 0)), ("預賽", "中華隊", "澳洲", (6, 4)), ("季軍賽", "澳洲", "中華隊", None)]
    assert out["record"] == [1, 1, 0]
    # 沒有模板時認縮寫欄位(代號或中文);認不出就是待定
    plain = "{{linescore\n|Date= 2026年11月12日\n|Time= 19:00\n|Road= 日本\n|RoadAbr= 日本\n|Home= ？\n|HomeAbr= 中華\n}}\n"
    assert [(g["away"], g["home"]) for g in apbc.parse(plain)] == [("JPN", "TPE")]
    unknown = "{{linescore\n|Date= 2026年11月15日\n|Time= 19:00\n|Road= {{bb|}}\n|RoadAbr= \n|Home= 待定\n|HomeAbr= 勝者\n}}\n"
    assert [(g["away"], g["home"]) for g in apbc.parse(unknown)] == [("", "")]


def test_review_pr53_full_text_excerpt_goes_with_its_news_item():
    """高權重新聞下一行是 `  [全文摘錄]：…`:只刪標題列會把觸發內容留在提示詞裡。"""
    prompt = ("- [N1] [來源A:路透] 白宮討論出口管制（摘要）\n"
              "  [全文摘錄]：報導提到習近平與川普將會面。\n"
              "- [N2] [來源A:鉅亨] 台達電營收創高（摘要）\n"
              "  [全文摘錄]：單月 746 億元。\n"
              "</UNTRUSTED_SOURCE_DATA>\n"
              "  縮排但不屬於任何新聞的說明,提到習近平\n"
              "一般段落\n")
    out, dropped = cr.soften(prompt, 1)
    assert dropped == 1 and "[N1]" not in out and "報導提到習近平與川普" not in out   # 標題沒敏感字,全文有:整則拿掉
    assert "[N2]" in out and "單月 746 億元" in out
    assert "縮排但不屬於任何新聞的說明" in out and "一般段落" in out and out.endswith("\n")
    # 標題敏感、全文不敏感:延續列也一起拿掉,不留孤兒
    orphan = "- [N3] [來源A:大紀元] 法輪功集會\n  [全文摘錄]：現場約三千人。\n- [N4] 其他新聞\n"
    out, dropped = cr.soften(orphan, 1)
    assert dropped == 1 and "現場約三千人" not in out and "[N4]" in out


def test_review_pr53_round2_multi_paragraph_excerpts_stay_with_their_item():
    """全文保留分段:第二段起沒有縮排、中間有空行,也都是同一則。"""
    prompt = ("【重大事件】\n"
              "- [N1] [來源A:路透] 兩國領袖會面（摘要）\n"
              "  [全文摘錄]：第一段。\n"
              "第二段談習近平。\n"
              "\n"
              "第三段是後續。\n"
              "- [N2] [來源A:鉅亨] 台達電營收創高（摘要）\n"
              "  [全文摘錄]：單月 746 億元。\n"
              "第二段講毛利率。\n"
              "※ 各事件附 surprise_score\n"
              "寫稿規則照舊,提到習近平也不動\n")
    out, dropped = cr.soften(prompt, 1)
    assert dropped == 1 and "[N1]" not in out and "第二段談習近平" not in out and "第三段是後續" not in out
    assert "[N2]" in out and "第二段講毛利率" in out and "【重大事件】" in out
    assert "※ 各事件附 surprise_score" in out and "寫稿規則照舊,提到習近平也不動" in out
    clean = prompt.replace("習近平", "某人")
    assert cr.soften(clean, 1) == (clean, 0)   # 沒有東西要拿:原樣
