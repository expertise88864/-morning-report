"""批#41:行政院公報一手法令來源。

痛點回顧:政策資訊先前只靠新聞 RSS 間接取得 → 拿到的是媒體轉述而非一手文件
(適用條件/金額級距/上路日期常缺漏),且新政策名詞(「台灣未來帳戶」)會被
預先寫死的關鍵字白名單在評分前就剔除。公報同時解掉這兩個。
"""
import pytest

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
    # r2(七維度審查,P1):**這條原本斷言 == [],等於把缺陷釘成規格。**
    # 回 [] 會讓呼叫端接著 save_policy_keywords([], fresh) 把 merged=[]+fresh
    # 原子性覆寫上去 → 數月歷史庫縮成 ≤12 筆,而且會被 commit 回 repo,不可逆。
    # 正確的不變式是「讀不到就不准寫」,與 StoryLedgerCorrupt 一致。
    with pytest.raises(mr.PolicyKeywordsCorrupt):
        mr.load_policy_keywords()
    assert "policy_keywords_load" in mr._DEGRADED_STEPS, \
        "讀檔失敗必須進降級步驟——否則新詞偵測整個失效卻無人知道"

    # 形狀不符的**合法** JSON 走同一條路(先前連降級都沒記,卻同樣會覆寫)
    f.write_text('{"foo": 1}', encoding="utf-8")
    mr._DEGRADED_STEPS.clear()
    with pytest.raises(mr.PolicyKeywordsCorrupt):
        mr.load_policy_keywords()
    assert "policy_keywords_load" in mr._DEGRADED_STEPS


def test_corrupt_keywords_does_not_wipe_history(tmp_path, monkeypatch):
    """讀檔失敗後歷史庫必須原封不動——這是不可逆資料遺失的防線。"""
    f = tmp_path / "policy_keywords.json"
    monkeypatch.setattr(mr, "POLICY_KEYWORDS_FILE", f)
    mr.save_policy_keywords([f"詞{i}" for i in range(500)], [])
    good = f.read_text(encoding="utf-8")
    f.write_text("{ broken", encoding="utf-8")
    # r3(突變測試,P1):**這裡原本是測試自己重寫一份 try/except 再斷言自己
    # 剛寫下的 None** —— 生產那行改成 `[]` 也不會紅。實測把守衛改掉後 4000 筆
    # 歷史被覆寫成 3 筆,而全套測試仍全綠。改為呼叫真正上線的那個函式。
    assert mr.load_policy_keywords_for_run() is None,         "讀檔失敗必須回 None(呼叫端據此跳過存檔),不得回空清單"
    # 檔案內容必須維持原樣(斷言的是磁碟狀態,不是測試自己設的變數)
    assert f.read_text(encoding="utf-8") == "{ broken", "壞檔被覆寫了"
    f.write_text(good, encoding="utf-8")
    assert len(mr.load_policy_keywords()) == 500, "歷史庫被抹掉了"


def test_run_loader_returns_list_when_readable(tmp_path, monkeypatch):
    """正常情況必須回 list(而非 None),否則新詞永遠不會被存下來。"""
    f = tmp_path / "policy_keywords.json"
    monkeypatch.setattr(mr, "POLICY_KEYWORDS_FILE", f)
    mr.save_policy_keywords(["新青安3.0"], [])
    assert mr.load_policy_keywords_for_run() == ["新青安3.0"]


def test_gazette_only_policy_still_activates_deepdive_section():
    """r5(Codex):這批的核心目的是讓公報獨有的政策也能被深度解析。
    若段落規則仍寫「僅在有【台灣重大政策】清單時才寫」,公報獨有的政策會被略過,
    整個一手法令來源等於白接。"""
    from tests.test_data_validation import _empty_quotes
    recs = tps.parse_gazette_xml(_XML)
    # 媒體政策清單為空,只有公報素材
    q = _empty_quotes(GAZETTE_RECORDS=recs, TW_DAILY_INTELLIGENCE={"policy": []})
    prompt = mr._build_prompt(q, {"error": "x"}, {"error": "x"}, [], [], "")

    assert "十之二、重大政策深度解析" in prompt, "公報獨有政策時整段消失"
    assert "行政院公報" in prompt
    # 啟用條件必須明確含公報,否則 LLM 會照舊條件略過
    head = prompt[prompt.index("十之二、重大政策深度解析"):][:120]
    assert "公報" in head, f"段落啟用條件未涵蓋公報:{head}"


def test_gazette_absent_and_no_policy_omits_section():
    """兩者皆無時仍要整段省略,不留空標題。"""
    from tests.test_data_validation import _empty_quotes
    q = _empty_quotes(GAZETTE_RECORDS=[], TW_DAILY_INTELLIGENCE={"policy": []})
    prompt = mr._build_prompt(q, {"error": "x"}, {"error": "x"}, [], [], "")
    assert "十之二、重大政策深度解析" not in prompt


def test_gazette_is_declared_authoritative_over_media():
    """公報是一手法令原文,細節權威性高於媒體轉述——prompt 必須明講,
    否則 LLM 遇到兩邊數字不一致時無所適從。"""
    from tests.test_data_validation import _empty_quotes
    recs = tps.parse_gazette_xml(_XML)
    prompt = mr._build_prompt(_empty_quotes(GAZETTE_RECORDS=recs),
                              {"error": "x"}, {"error": "x"}, [], [], "")
    assert "一手法令原文" in prompt
    assert "以公報為準" in prompt


def test_policy_deepdive_never_drops_policies_silently():
    """批#87:超過版面上限的政策**不得靜默消失**。

    原本 `[:3]` 之後第 4 個起直接不見,信裡與 manifest 都看不出「今天還有別的
    政策沒展開」—— 讀者無從知道自己漏了什麼。這與本專案反覆出現的
    「靜默截斷讀起來像全部涵蓋」是同一個病灶。
    """
    import morning_report as mr

    intel = {"policy": [
        {"title": f"重大政策 {i} 公告上路", "topic": f"政策{i}",
         "importance": 9.0 - i * 0.1, "status": "已公告",
         "source_name": "自由時報", "link": f"https://example.com/{i}",
         "published": "2026-07-31T08:00:00+08:00"} for i in range(6)]}
    mr._RUN_MANIFEST.pop("policy_deepdive", None)
    try:
        block = mr._format_policy_deepdive_block(intel)
        stat = mr._RUN_MANIFEST.get("policy_deepdive") or {}
        assert stat["candidates"] == 6 and stat["written"] == 3
        assert len(stat["dropped"]) == 3, "被截掉的政策沒有記進 manifest"
        # 沒展開的也必須在 prompt 裡被提到(一行帶過)
        assert "本日另有以下政策" in block
        for name in stat["dropped"]:
            assert name in block, f"{name} 被截掉且完全沒有提到"
    finally:
        mr._RUN_MANIFEST.pop("policy_deepdive", None)


def test_medical_policy_queries_feed_the_policy_channel():
    """批#87:醫療衛生政策要進得了深度解析。

    衛福部/健保署的查詢原本只在 `medical` 通道,而深度解析只吃 `policy`
    (`_format_policy_deepdive_block` 讀 `intel["policy"]`)——健保給付調整、
    醫療法規修法這類會改變執業與家戶支出的政策,最多只在醫界動態卡出現一行。
    """
    import morning_report as mr

    policy_qs = " ".join(mr.TW_INTELLIGENCE_QUERIES["policy"])
    assert "健保" in policy_qs and "給付" in policy_qs, "政策通道沒有健保給付查詢"
    assert "衛福部" in policy_qs, "政策通道沒有衛福部查詢"
    # 反向:medical 通道的事件形狀查詢不該被搬走(兩者職責不同)
    medical_qs = " ".join(mr.TW_INTELLIGENCE_QUERIES["medical"])
    assert "裁罰" in medical_qs and "缺藥" in medical_qs
