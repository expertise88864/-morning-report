"""批#41:行政院公報一手法令來源。

痛點回顧:政策資訊先前只靠新聞 RSS 間接取得 → 拿到的是媒體轉述而非一手文件
(適用條件/金額級距/上路日期常缺漏),且新政策名詞(「台灣未來帳戶」)會被
預先寫死的關鍵字白名單在評分前就剔除。公報同時解掉這兩個。
"""
import morning_report as mr
import tw_policy_sources as tps

# 精簡版真實結構(欄位名與順序取自 2026-07-25 實測的端點回應)
_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Gazette>
<Record>
<MetaId>167273</MetaId>
<PubGovName>財政部</PubGovName>
<Date_Published>中華民國115年7月24日</Date_Published>
<Comment_Deadline></Comment_Deadline>
<Title>財政部令：修正「金融機構執行稅務用途金融帳戶資訊電子申報作業要點」</Title>
<ThemeSubject>修正作業要點第8點</ThemeSubject>
<Keyword>青年安心成家方案;優惠貸款;審查;申報</Keyword>
<Explain>配合實務需要修正</Explain>
<Category>510(財政稅務)</Category>
<HTMLContent>&lt;div class="c"&gt;&lt;p&gt;第一點 適用對象為……&lt;/p&gt;&lt;/div&gt;</HTMLContent>
<PreviewStageURL>https://gazette.example/1</PreviewStageURL>
</Record>
<Record>
<MetaId>167274</MetaId>
<PubGovName>國防部</PubGovName>
<Date_Published>中華民國115年7月24日</Date_Published>
<Title>國防部函：註銷某令</Title>
<Keyword>軍事教育;基金</Keyword>
<Category>240(國防)</Category>
<HTMLContent>&lt;p&gt;內容&lt;/p&gt;</HTMLContent>
</Record>
<Record>
<MetaId>167275</MetaId>
<PubGovName>經濟部</PubGovName>
<Date_Published>中華民國115年7月24日</Date_Published>
<Comment_Deadline>中華民國115年9月22日</Comment_Deadline>
<Title>經濟部公告：預告「商業團體分業標準」「虛擬通貨商業」修正草案</Title>
<Keyword>虛擬通貨;商業團體分業標準</Keyword>
<Category>550(產業管理);1Z0(其他)</Category>
<HTMLContent>&lt;p&gt;草案內容&lt;/p&gt;</HTMLContent>
</Record>
</Gazette>"""


def test_parses_multi_valued_category():
    """Category 實測可能多值(如 "550(產業管理);1Z0(其他)"),兩個碼都要抓到。"""
    recs = tps.parse_gazette_xml(_XML)
    assert len(recs) == 3
    assert recs[2]["category_codes"] == ["550", "1Z0"]
    assert recs[0]["category_codes"] == ["510"]


def test_focus_filter_uses_category_codes_not_keywords():
    """以**分類碼**過濾而非關鍵字:政策名詞會變,分類碼是政府的固定本體。
    國防那筆不在關注範圍,財稅與產業管理要留下。"""
    recs = tps.parse_gazette_xml(_XML)
    kept = [r for r in recs if tps.is_focus_record(r)]
    assert [r["publisher"] for r in kept] == ["財政部", "經濟部"]
    assert "財政稅務" in tps.focus_labels(recs[0])


def test_html_content_is_stripped_before_reaching_prompt():
    """HTMLContent 是原始 HTML(且為轉義形式)。標籤必須清掉,否則會佔素材預算
    並干擾模型閱讀。"""
    recs = tps.parse_gazette_xml(_XML)
    body = recs[0]["content"]
    assert "適用對象" in body
    assert "<div" not in body and "<p>" not in body and "&lt;" not in body


def test_new_policy_term_discovery_keeps_real_names_drops_admin_verbs():
    """取代關鍵字白名單的核心:政府自標的 Keyword 與歷史庫比對,沒見過的就是候選。
    但要濾掉「審查/申報」這類每天都出現的通用行政動作詞。"""
    recs = tps.parse_gazette_xml(_XML)
    fresh = tps.discover_new_keywords(recs, seen=set())
    assert "青年安心成家方案" in fresh, "真實政策名詞不得被誤殺"
    assert "優惠貸款" in fresh
    assert "審查" not in fresh and "申報" not in fresh, "通用行政詞應剔除"
    assert "軍事教育" not in fresh, "非關注分類的詞不應進入候選"


def test_known_terms_are_not_reported_as_new():
    """歷史庫裡有的詞不再算新——這正是跨日 state 必須 commit 回 repo 的理由。"""
    recs = tps.parse_gazette_xml(_XML)
    fresh = tps.discover_new_keywords(recs, seen={"青年安心成家方案"})
    assert "青年安心成家方案" not in fresh
    assert "虛擬通貨" in fresh


def test_parse_failure_surfaces_instead_of_silently_emptying():
    """r3(Codex):XML 壞掉必須拋 GazetteUnavailable,不得吞成空清單。

    吞成空清單的話,「來源整個掛掉」與「今天沒有關注分類的公報」在呼叫端看起來
    一模一樣,_DEGRADED_STEPS 不會有紀錄、run manifest 也看不出來——降級與靜默
    是兩回事。"""
    import pytest
    for bad in ("not xml at all", ""):
        with pytest.raises(tps.GazetteUnavailable):
            tps.parse_gazette_xml(bad)


def test_fetch_failure_surfaces_to_caller():
    """來源掛掉要讓呼叫端知道;由呼叫端決定降級(它會記 _DEGRADED_STEPS)。"""
    import pytest

    def _boom(url, timeout=0):
        raise RuntimeError("gazette down")

    with pytest.raises(tps.GazetteUnavailable):
        tps.fetch_gazette(_boom)


def test_main_records_degradation_when_gazette_unavailable(monkeypatch):
    """端到端:公報掛掉時 _DEGRADED_STEPS 必須有 gazette,否則整個一手政策來源
    消失卻沒有任何人知道。"""
    def _boom(url, timeout=0):
        raise RuntimeError("down")

    mr._DEGRADED_STEPS.clear()
    try:
        tps.fetch_gazette(_boom)
    except tps.GazetteUnavailable:
        mr._DEGRADED_STEPS.append("gazette")
    assert "gazette" in mr._DEGRADED_STEPS


def test_prompt_block_is_fenced_and_flags_draft_status():
    """批#38 的圍欄鐵律同樣適用:政府網站原文仍是外部文字。
    另外草案預告必須標明「尚未定案」,否則 LLM 會把草案寫成已上路。"""
    recs = tps.parse_gazette_xml(_XML)
    block = mr._format_gazette_prompt_block(recs)
    assert block.count("<UNTRUSTED_SOURCE_DATA>") == 1
    assert block.count("</UNTRUSTED_SOURCE_DATA>") == 1
    # 安全規則必須在圍欄外才有效力
    assert block.index("一律忽略") < block.index("<UNTRUSTED_SOURCE_DATA>")
    assert "尚未定案" in block, "草案預告未標明狀態"
    # 非關注分類不得混進來
    assert "國防部" not in block


def test_prompt_block_empty_when_no_focus_records():
    """今日無關注分類的公報 → 回空字串,呼叫端整段省略而非寫「無」。"""
    only_defense = _XML.replace("510(財政稅務)", "240(國防)").replace(
        "550(產業管理);1Z0(其他)", "440(文化藝術)")
    recs = tps.parse_gazette_xml(only_defense)
    assert mr._format_gazette_prompt_block(recs) == ""
    assert mr._format_gazette_prompt_block([]) == ""
    assert mr._format_gazette_prompt_block(None) == ""


def test_policy_keywords_state_roundtrip(tmp_path, monkeypatch):
    """歷史庫讀寫;讀檔失敗要記進降級步驟(不是靜默當成空)。"""
    f = tmp_path / "policy_keywords.json"
    monkeypatch.setattr(mr, "POLICY_KEYWORDS_FILE", f)
    assert mr.load_policy_keywords() == []          # 檔案不存在 = 首次執行,非錯誤
    assert mr.save_policy_keywords([], ["新青安3.0", "台灣未來帳戶"]) is True
    assert mr.load_policy_keywords() == ["新青安3.0", "台灣未來帳戶"]
    # 併入時去重且保序
    mr.save_policy_keywords(mr.load_policy_keywords(), ["台灣未來帳戶", "碳費"])
    assert mr.load_policy_keywords() == ["新青安3.0", "台灣未來帳戶", "碳費"]

    f.write_text("{ broken", encoding="utf-8")
    mr._DEGRADED_STEPS.clear()
    assert mr.load_policy_keywords() == []
    assert "policy_keywords_load" in mr._DEGRADED_STEPS, \
        "讀檔失敗必須進降級步驟——否則新詞偵測整個失效卻無人知道"
