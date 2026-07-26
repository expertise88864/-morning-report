"""全形標點的統一防線。

r9(Codex,P1)的根因不是「某一個集合寫錯」,而是**用字面量寫標點集合**這件事
本身有風險:字元可能在編輯管線中被轉成 ASCII,而**看起來完全正常**。
實際發生過:_CLAUSE_SEPARATORS 我以為寫了全形+半形成對,存進檔案的卻是四組
重複的半形,全形 ，；！？ 一個都不在——而中文標題用的正是全形。

更糟的是**我寫的測試也用 ASCII 標點**,所以測試通過而缺陷還在。

這個檔案用 chr(0x....) 建測試字串(碼位不會被任何編碼轉換弄壞),
對所有標點正規化路徑做同一套檢查。新增正規化器時請加進來。
"""
import morning_report as mr
import news_events as ne
import story_ledger as sl

#: 中文財經標題實際會出現的全形標點(碼位形式,避免測資本身被轉換)
FULLWIDTH = {
    "，逗號": 0xFF0C, "；分號": 0xFF1B, "！驚嘆": 0xFF01, "？問號": 0xFF1F,
    "：冒號": 0xFF1A, "（左括": 0xFF08, "）右括": 0xFF09, "　空格": 0x3000,
    "。句號": 0x3002, "、頓號": 0x3001, "－破折": 0xFF0D,
}


def test_clause_separators_contain_both_widths():
    """子句分隔符必須全形半形都有——中文用全形,混排時半形也會出現。"""
    for cp in (0xFF0C, 0xFF1B, 0xFF01, 0xFF1F, 0xFF1A, 0x3002, 0x3001):
        assert chr(cp) in ne._CLAUSE_SEPARATORS, f"缺 U+{cp:04X}"
    for ch in (",", ";", "!", "?", ":", "\n"):
        assert ch in ne._CLAUSE_SEPARATORS, f"缺半形 {ch!r}"


def test_pending_marker_does_not_cross_any_fullwidth_separator():
    """「尚待」不得跨過任何全形分隔符,把後一子句已核准的事判成待決。"""
    for name, cp in [("，", 0xFF0C), ("；", 0xFF1B), ("！", 0xFF01),
                     ("。", 0x3002)]:
        title = f"尚待主管機關進一步審議{chr(cp)}另案已核准"
        assert ne._event_lifecycle({"title": title, "source_grade": "A"}) \
            == "confirmed", f"「尚待」跨過了 {name}"


def test_all_normalisers_strip_fullwidth_punctuation():
    """三個標點正規化器對同一套全形字元的行為必須一致。"""
    for name, cp in FULLWIDTH.items():
        probe = "A" + chr(cp) + "B"
        assert chr(cp) not in mr._norm_podcast_point(probe), \
            f"_norm_podcast_point 未剝除 {name}"
        assert chr(cp) not in sl._norm(probe), f"story_ledger._norm 未剝除 {name}"


def test_mops_title_join_survives_every_fullwidth_variant():
    """權威覆寫的標題比對:LLM 抄錄時插入任一全形標點都不得讓它失效。

    (_norm_title_key 是巢狀函式,故以端到端行為驗證。)
    """
    mops = [{"code": "2330", "title": "台積電公告訂定除息基準日", "summary": "x",
             "published": "2026-07-25T01:00:00+00:00",
             "clause": "第14款", "event_type": "general"}]
    for name, cp in FULLWIDTH.items():
        variant = "台積電" + chr(cp) + "公告訂定除息基準日"
        llm = [{"entity": "台積電", "title": variant, "event_type": "earnings",
                "surprise_score": 0.7, "published": "2026-07-25T01:00:00+00:00"}]
        events = mr.extract_structured_events(news=[], mops=mops, llm_events=llm)
        assert len(events) == 1, f"{name} 讓權威覆寫失效"
        assert events[0]["event_type"] == "general"
