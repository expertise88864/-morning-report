"""2026-11-10 使用者:消息正確性五項 —— 查無來源的數字標「未核實」、官方營收財報核對、
同一指標一個數字、資料月份過舊的新聞、Podcast 未經查核提示。"""
import datetime as dt
import pathlib

import pytest

import legacy_evidence as le
import legacy_watch as lw
import news_staleness as ns
import official_numbers as on
import report_polish as rp

N1 = {"title": "藥華藥前三季每股純益 18.43 元 - 經濟日報", "link": "https://news.example/pec", "source_name": "經濟日報"}


class _Resp:
    def __init__(self, rows):
        self.rows = rows

    def raise_for_status(self):
        pass

    def json(self):
        return self.rows


REV = [{"資料年月": "11510", "公司代號": "2327", "公司名稱": "國巨*", "營業收入-當月營收": "17597000",
        "營業收入-上月比較增減(%)": "3.1", "營業收入-去年同月增減(%)": "55.0",
        "累計營業收入-當月累計營收": "149751000", "累計營業收入-前期比較增減(%)": "38.3"}]
INC = [{"年度": "115", "季別": "3", "公司代號": "6446", "基本每股盈餘(元)": "18.43", "營業收入": "19736673.00",
        "營業利益": "8371448.00", "稅後淨利": "7602895.00"}]
OTC = [{"Year": "115", "季別": "3", "SecuritiesCompanyCode": "1259", "基本每股盈餘": "2.67", "稅後淨利": "87082.00"}]


def _get(url, **_kw):
    if url.endswith("t187ap05_L"):
        return _Resp(REV)
    if url.endswith("t187ap14_L"):
        return _Resp(INC)
    if url.endswith("t187ap14_O"):
        return _Resp(OTC)
    raise OSError("櫃買營收連不上")


@pytest.fixture(autouse=True)
def _clean():
    le.reset()
    on.reset()
    lw._PENDING.clear()
    yield
    le.reset()
    on.reset()
    lw._PENDING.clear()


def test_official_numbers_load_survives_one_dead_source():
    logs = []
    assert on.load(_get, logs.append) == 3
    assert any("t187ap05_O" in line and "抓取失敗" in line for line in logs)
    assert "76.03 億元" in on.facts("藥華藥（6446，新藥廠）") and "18.43 元" in on.facts("（6446）")
    assert "175.97 億元" in on.facts("國巨（2327）") and "55.00%" in on.facts("國巨（2327）")
    assert "2.67 元" in on.facts("安心（1259）") and on.facts("沒有代號") == "" and on.facts("9999 不在表上") == ""


def test_unverifiable_number_is_marked_and_official_number_is_evidence():
    on.load(_get, lambda *_: None)
    assert le.tag(N1) == "[N1] "
    text = ("## 九、其他類股資訊\n"
            "**【生技｜台灣】藥華藥（6446，新藥廠）**：前三季稅後淨利 76 億元、每股純益 18.43 元 [經濟日報][N1]\n\n"
            "**【生技｜台灣】藥華藥（6446，新藥廠）**：手上現金 987 億元、每股純益 18.43 元 [經濟日報][N1]\n")
    manifest = {}
    out = le.audit(text, {}, manifest)
    report = manifest["llm"]["legacy_evidence"]
    assert "76 億元、" in out and "76 億元（未核實）" not in out          # 官方稅後淨利 76.03 億:算有來源
    assert "987 億元（未核實）" in out and report["marked"] == 1 and report["failed"] == 1
    assert "18.43 元（未核實）" not in out and "6446（未核實）" not in out
    detail = le.findings(manifest)[0][2]
    assert "1 個查無來源的數字已標「未核實」" in detail and "未刪除" in detail


def test_same_month_revenue_that_disagrees_gets_the_official_figure():
    on.load(_get, lambda *_: None)
    ok = "**國巨（2327，被動元件）**：10 月營收 175.97 億元，月增 3.1%、年增 55% [自由財經]"
    assert on.correction(ok) == ""
    wrong = "**國巨（2327，被動元件）**：10 月營收 185.2 億元、年增 61% [自由財經]"
    assert on.correction(wrong) == "（官方公告：10 月營收 175.97 億元、年增 55.0%，與上述數字不同）"
    assert on.correction("**國巨（2327）**：11 月營收 190 億元、年增 60%") == ""     # 官方還沒更新到這個月:不比
    assert on.correction("國巨（2327）與藥華藥（6446）：10 月營收 185.2 億元") == ""  # 兩家公司分不清是誰的
    le.tag(N1)
    manifest = {}
    out = le.audit("## 八、科技板塊脈動\n" + wrong + "[N1]\n", {}, manifest)
    assert "與上述數字不同）" in out and manifest["llm"]["legacy_evidence"]["corrected"] == 1


def test_stale_data_month_titles_are_not_news():
    nov = dt.date(2026, 11, 10)
    assert ns.stale("日本 8 月電動車銷量創新高、特斯拉年增 9 成", nov)
    assert ns.stale("美國7月CPI年增3.2%", nov)
    for title in ("國巨 10 月營收 175.97 億元創新高", "中國 9 月出口年增 8%", "11 月 17 日公布 Q3 財報",
                  "前 8 月營收年增 36%", "明年 1 月出貨放量", "8 月底前完成併購", "陸港務費暫停至 1 月 10 日"):
        assert not ns.stale(title, nov), title
    news = [{"title": "日本 8 月電動車銷量創新高"}, {"title": "台達電 10 月營收創高"}]
    assert [n["title"] for n in ns.fresh(news, "2026-11-10")] == ["台達電 10 月營收創高"]
    assert ns.fresh(news, None) == news and ns.fresh(news, "壞日期") == news
    assert len(rp.fresh_news(news, None, "2026-11-10")) == 1 and len(rp.fresh_news(news, None)) == 2
    assert ns.stale("去年 12 月營收", dt.date(2027, 3, 5)) and not ns.stale("2 月營收", dt.date(2027, 3, 5))


def test_prompt_rule_podcast_note_and_reader_legend():
    root = pathlib.Path(rp.__file__).parent
    assert "同一個行情指標(加權、費半、油價、匯率、成交金額)全信只用行情表的值" in (root / "writing_rules.py").read_text(encoding="utf-8")
    assert "以下是主持人說法的摘要，未經查核" in (root / "render_utils.py").read_text(encoding="utf-8")
    assert "不可寫成「80 幾瓦」" in (root / "podcast_digest.py").read_text(encoding="utf-8")
    html = rp.display_pass('<h2 style="x">八、科技板塊脈動</h2><p>內容</p>')
    assert "標「未核實」的數字＝在引用的新聞、行情與官方營收財報裡都對不到" in html


def test_review_pr50_cumulative_future_months_and_same_number_two_currencies():
    on.load(_get, lambda *_: None)
    # 累計營收不拿來比單月;累計的年增率不套到單月
    assert on.correction("國巨（2327）：前 10 月營收 1,497.51 億元、年增 38.3%") == ""
    assert on.correction("國巨（2327）：累計 10 月營收 1,497.51 億元") == ""
    assert on.correction("國巨（2327）：10 月營收 175.97 億元；累計營收 1,497.51 億元、年增 38.3%") == ""
    assert on.correction("國巨（2327）：10 月營收 175.97 億元，累計前 10 月 1,497.51 億元、年增 38.3%") == ""
    assert on.correction("國巨（2327）：10 月營收 175.97 億元、年增 61%") != ""
    # 明確的未來月份是展望;寫了年份就照年月算
    nov = dt.date(2026, 11, 10)
    assert not ns.stale("台積電明年 3 月出貨放量", nov) and not ns.stale("2027 年 3 月出貨放量", nov)
    assert ns.stale("2026 年 3 月出口年增 8%", nov) and ns.stale("2025 年 12 月營收回顧", nov)
    assert not ns.stale("2026 年 10 月營收創高", nov)
    # 同一個數字兩種幣別:只標對不到來源的那一筆
    le.tag({"title": "某公司擬發債新台幣100億元 - 經濟日報", "link": "https://news.example/bond"})
    manifest = {}
    out = le.audit("## 八、科技板塊脈動\n- 某公司擬發債新台幣100億元、另籌人民幣100億元 [經濟日報][N1]\n", {}, manifest)
    assert "新台幣100億元、另籌人民幣100億元（未核實）" in out and manifest["llm"]["legacy_evidence"]["marked"] == 1


def test_review_pr50_round2_cumulative_without_space_and_semicolon_before_growth():
    on.load(_get, lambda *_: None)
    assert on.correction("國巨（2327）：累計10月營收1,497.51億元") == ""
    assert on.correction("國巨（2327）：前10月營收1,497.51億元") == ""
    assert "年增 55.0%" in on.correction("國巨（2327）：10月營收175.97億元；年增61%")
    assert on.correction("國巨（2327）：10月營收175.97億元；年增55%") == ""
