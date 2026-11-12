"""11-12 實信:交易日曆解析、七之三漏出、觀察點回顧進信、來源連結夾在句子中間。"""
import legacy_evidence as le
import legacy_watch as lw
import llm_postprocess as lp
import morning_report as mr
import reader_prose
import report_polish as rp
import source_placement as sp


def test_twse_compact_dates_parse_so_the_calendar_does_not_depend_on_yahoo(monkeypatch):
    """FMTQIK 給「1151111」:原本解析失敗,日曆只剩 Yahoo —— Yahoo 少一天就把上一交易日算錯。"""
    assert mr._parse_twse_date("1151111") == "2026-11-11" and mr._parse_twse_date("20261111") == "2026-11-11"
    assert mr._parse_twse_date("115/11/11") == "2026-11-11" and mr._parse_twse_date("2026-11-11") == "2026-11-11"
    assert mr._parse_twse_date("壞") is None and mr._parse_twse_date("1151340") is None and mr._parse_twse_date("") is None

    class _R:
        def raise_for_status(self):
            pass

        def json(self):
            return [{"Date": "1151110"}, {"Date": "1151111"}]

    class _Yahoo:
        def history(self, **_):
            raise OSError("Yahoo 少一天")
    monkeypatch.setattr(mr, "_http_get", lambda *a, **k: _R())
    monkeypatch.setattr(mr.yf, "Ticker", lambda *_: _Yahoo())
    assert mr.fetch_tw_trading_sessions() == ["2026-11-10", "2026-11-11"]


def test_any_7_3_section_is_hidden_whatever_the_wording():
    text = "## 七之四、敘事變化\n內容甲\n\n## 七之三、未來 48 小時關鍵事件\n- 事件\n\n## 八、科技板塊脈動\n內容乙\n"
    out = lp.strip_reader_hidden(text)
    assert "七之三" not in out and "- 事件" not in out and "內容甲" in out and "內容乙" in out
    assert "七之三" not in lp.strip_reader_hidden("**七之三、事件情境回顧**\n舊內容\n\n## 八、科技板塊脈動\n乙")


def test_watch_review_lines_in_the_wrong_section_are_captured_but_not_shown():
    le.reset()
    lw._PENDING.clear()
    text = ("## 十、台灣本地動態\n- [w68] 未觸發：10 年期殖利率 4.998% 未達 5.2% [行情表]\n"
            "- [w122] 證據不足：今日無新公告\n\n全體上市公司 10 月營收年增 52.40%。\n")
    parsed = lw.parse(text)
    assert [r["watch_id"] for r in parsed["watch_review"]] == ["w68", "w122"]
    shown = reader_prose.public_sections(text)
    assert "w68" not in shown and "w122" not in shown and "全體上市公司 10 月營收年增 52.40%" in shown
    proper = "## 昨日觀察點回顧\n- [w1] 未觸發：沒事\n\n## 十、台灣本地動態\n內容\n"
    assert [r["watch_id"] for r in lw.parse(proper)["watch_review"]] == ["w1"]


def test_source_links_move_to_the_end_of_each_paragraph():
    a, b, c = "[中央社財經](https://a.example/1)", "[鉅亨台股](https://b.example/2)", "[科技新報](https://c.example/3)"
    para = f"**台積電（2330）**：ADR 大漲 4.25% {a}，但今日開低 {b}。訂單未見削減 {c}。回檔風險大。**[A 級・信心:中]**"
    assert sp.last(para) == ("**台積電（2330）**：ADR 大漲 4.25%，但今日開低。訂單未見削減。回檔風險大。"
                             f"**[A 級・信心:中]**　{a} {b} {c}")
    # 同一網址只留一次;清單每項各自成段;標題、表格、沒有連結的段落不動
    text = (f"## 八、科技板塊脈動\n- 甲事 {a}，又提一次 {a}。\n- 乙事 {b}。\n\n沒有連結的段落。\n"
            f"| 表格 | {c} |\n\n多行段落第一行 {a}\n第二行 {b}。\n")
    out = sp.last(text).split("\n")
    assert out[0] == "## 八、科技板塊脈動" and out[1] == f"- 甲事，又提一次。　{a}" and out[2] == f"- 乙事。　{b}"
    assert out[4] == "沒有連結的段落。" and out[5] == f"| 表格 | {c} |"
    assert out[7] == "多行段落第一行" and out[8] == f"第二行。　{a} {b}"
    assert sp.last("沒有連結") == "沒有連結" and sp.last(None) is None
    assert rp.normalize_terms(para).endswith(f"　{a} {b} {c}")


def test_review_pr52_integrated_sections_and_bold_chapter_headings():
    # 回顧列寫進「觀察觸發點」也不可併進展望
    text = "## 觀察觸發點\n- [w68] 未觸發：殖利率未達 5.2%\n- 外資台指期淨空縮回 9 萬口以內(觀察期:5 天內)\n\n## 我的明確立場\n中性\n"
    shown = reader_prose.public_sections(text)
    assert "w68" not in shown and "外資台指期淨空縮回 9 萬口以內" in shown
    # 整行粗體的編號章節是段落邊界:連結不跨過去,標題原樣保留
    a, b = "[A](https://a.example)", "[B](https://b.example)"
    out = sp.last(f"新聞甲 {a}\n**八、科技板塊脈動**\n新聞乙 {b}").split("\n")
    assert out == [f"新聞甲　{a}", "**八、科技板塊脈動**", f"新聞乙　{b}"]
    assert sp.last(f"新聞甲 {a}\n**七之二、世界大事速覽**") == f"新聞甲　{a}\n**七之二、世界大事速覽**"
    # 段首粗體公司名不是章節:照常是內文
    assert sp.last(f"**台積電（2330）**：大漲 {a}。") == f"**台積電（2330）**：大漲。　{a}"
