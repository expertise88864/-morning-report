# -*- coding: utf-8 -*-
"""2026-08-29 使用者六項回饋(對當天實信 —— Luna 特化第一天)。

1. 第五段「加權昨收」獨立格是重複(藍卡「較昨收」已有同數字)。
2. 七之二排版跑掉:「…航運中。:戰爭…」的「。:」黏接、後續影響斷行破碎。
3. 科技新聞跑到「其他類股」:產業級新聞(長鑫/SK海力士/CCL/NVL72)
   無可指名主體 → 全掉到九段,八段只剩兩條。
4. 刪三檔英文 podcast(WSB / Odd Lots / Sharp Tech)。
5. (詢問)世界大事的代表性 —— 回答,無程式變更。
6. 醫界追蹤:台大等床 12 天死亡事件的後續(民眾/政府/政策)。
"""
import io
import time
from pathlib import Path

import analysis_render as ar
import industry_class as ic
import morning_report as mr
import podcast_digest as pdg

_ROOT = Path(mr.__file__).resolve().parent


def test_the_taiex_card_shows_the_close_only_once():
    """藍色大字卡的「較昨收 46331.45」與下方獨立格「加權昨收 46331.45」
    是同一個數字印兩次 —— 使用者:「不用寫」。"""
    src = io.open(_ROOT / "morning_report.py", encoding="utf-8").read()
    i = src.index("五、加權指數開盤預測</h2>")   # 錨 h2 本體,不是註解
    seg = src[max(0, i - 4000):i + 2000]
    assert "較昨收" in seg
    assert ">加權昨收<" not in seg, "那一格又回來了"


def test_world_events_render_as_one_flowing_paragraph():
    """實信:「…仍遠低於戰前,卡達斡旋恢復航運中。:戰爭進入…」——
    「。:」黏接;「後續可能影響」另起縮排行,在郵件裡斷成碎片。
    正確形狀是 legacy 的一氣呵成:標題句。解讀句。後續可能影響:…"""
    obj = {"stance": {"label": "中性"}, "executive_summary": "x",
           "world_events": [
               {"what": "美伊戰爭滿六個月,卡達斡旋恢復航運中。",
                "why_it_matters": "這決定油價與通膨預期的下一階",
                "what_next": "若週末達成協議,油價續跌"}]}
    md = ar.render(obj, {}) or ""
    assert "。:" not in md and "。:" not in md, md[:400]
    line = [ln for ln in md.splitlines() if "美伊戰爭" in ln][0]
    # 三段都在同一行(段落),而且「後續可能影響」不是另起縮排行
    assert "下一階" in line and "後續可能影響:" in line and "油價續跌" in line


def test_industry_level_tech_news_lands_in_the_tech_section():
    """實信:長鑫/SK 海力士/CCL/NVL72/Marvell 全部掉進「九、其他類股」,
    八段只剩台積電與欣興 —— 主體判準只認可指名的公司,產業級新聞沒有
    主體就一律 other。無主體時退回標題判準(宣告式關鍵字表)。"""
    packet = {"news": [
        {"source_item_id": "n1", "title": "SK海力士:記憶體缺到2030"},
        {"source_item_id": "n2", "title": "SCFI 運價指數連5漲 美東線站上萬美元"}]}
    obj = {"stance": {"label": "中性"}, "executive_summary": "x",
           "top_news_analysis": [
               {"source_item_id": "n1", "why_it_matters": "記憶體缺貨延續",
                "affected_assets": []},
               {"source_item_id": "n2", "why_it_matters": "運價利多已定價",
                "affected_assets": []}]}
    md = ar.render(obj, packet) or ""
    i_tech = md.index("八、科技板塊脈動")
    i_other = md.index("九、其他類股資訊")
    assert i_tech < md.index("記憶體缺到2030") < i_other, "科技新聞仍在九段"
    assert md.index("SCFI") > i_other, "航運跑進科技段"


def test_ascii_tech_keywords_need_word_boundaries():
    """裸子字串的 `AI` 會在 `SAID`/`AIRLINE` 裡命中 —— 與別名比對
    同一個教訓。"""
    assert ic.is_tech_headline("AI大單難解近渴!Marvell暴跌超10%")
    assert ic.is_tech_headline("大陸 DRAM 一哥 長鑫上半年賺逾3,000億")
    assert not ic.is_tech_headline("He SAID the airline was fine")
    assert not ic.is_tech_headline("長榮砸重金換船 從甲醇到SAF")
    assert not ic.is_tech_headline("美國 FDA 核准胰臟癌標靶藥")
    # 「代工」不可裸列(r1 外審):傳產代工不是科技
    assert not ic.is_tech_headline("成衣代工廠訂單回溫 東南亞產能滿載")
    assert not ic.is_tech_headline("製鞋代工業調整產能因應關稅")
    assert ic.is_tech_headline("晶圓代工報價喊漲 成熟製程滿載")


def test_the_three_english_podcasts_are_gone():
    """使用者 2026-08-29 拍板刪 WSB / Odd Lots / Sharp Tech。
    白名單同時是 state 殘留的過濾器 —— 兩邊都要清,只清 feeds 的話,
    舊 digest state 裡那幾集還會再進信。"""
    names = {p["name"] for p in pdg.PODCASTS}
    gone = {"Wall Street Breakfast", "Odd Lots", "Sharp Tech (Ben Thompson)"}
    assert not (names & gone), names & gone
    assert not (set(mr._PODCAST_DISPLAY_RANK) & gone)
    # 台灣節目與使用者沒點名的英文節目不受影響
    assert {"股癌", "財報狗", "All-In Podcast"} <= (
        names | set(mr._PODCAST_DISPLAY_RANK))
    # 還在抓的節目都要能被顯示(不在白名單=抓了也進不了信=餓死)
    missing = names - set(mr._PODCAST_DISPLAY_RANK)
    assert not missing, f"這些節目抓了卻進不了信:{missing}"


def test_the_medical_watch_skips_the_region_filter(monkeypatch):
    """醫界追蹤是**全國**事件(台大等床死亡的後續):標題沒有中彰投
    地名,照走地區過濾等於這個主題不存在。national 欄免除它;
    其他主題的地區過濾不受影響。"""
    row = [r for r in mr.LOCAL_NEWS_QUERIES if r[0] == "醫界追蹤"][0]
    assert len(row) > 3 and row[3] is True
    assert "台大醫院" in row[1] and "衛福部" in row[1]

    now = time.gmtime(time.time() - 3600)

    class _Feed:
        entries = [
            {"title": "台大醫院急診等床12天病逝 衛福部回應了",
             "link": "https://news.example/a", "published_parsed": now},
            {"title": "台中 建設 新進度",
             "link": "https://news.example/b", "published_parsed": now}]

        def get(self, k, d=None):
            return getattr(self, k, d)
    monkeypatch.setattr(mr, "_feedparser_parse_url_with_timeout",
                        lambda url: _Feed())
    out = mr.fetch_local_news()
    assert "台大醫院急診等床12天病逝 衛福部回應了" in [
        i["title"] for i in out.get("醫界追蹤") or []], out.get("醫界追蹤")
    # 一般主題仍走地區過濾:同一個假 feed 裡無地名的台大標題不得進「建設」
    for label, items in out.items():
        if label == "醫界追蹤":
            continue
        assert all("台大醫院" not in i["title"] for i in items), (label, items)
