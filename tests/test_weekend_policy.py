"""批#46:週日綜合也跑政策深度解析。

缺口成因:週日走的是輕量路徑(render_weekend_digest_html),不呼叫 _build_prompt,
所以批#41 的公報一手法令與「重大政策深度解析」在週日全都不會執行——政策區只剩
標題級清單。而**週末正是政策消息最容易累積的時候**(立院三讀、行政院核定常在
週四五),那些消息在週日只會以標題出現一次,週一又因「已顯示」記錄不會再深入寫,
等於永久錯過。2026-07-26 的實信就是這樣:新青安 3.0 與台灣未來帳戶都只有標題。
"""
import morning_report as mr
import tw_policy_sources as tps
from tests.test_tw_policy_sources import _XML

_INTEL = {"policy": [{
    "title": "行政院拍板台灣未來帳戶 每名新生兒每年存1.2萬",
    "importance": 6.4, "timeline_key": "k1", "topic": "民生金融",
    "status": "已公告", "source_name": "中央社", "source_grade": "官方",
    "published": "2026-07-25 10:00"}]}


def test_prompt_includes_both_media_and_gazette():
    """媒體清單與公報一手法令都要進 prompt,且公報的權威性要寫明。"""
    recs = tps.parse_gazette_xml(_XML)
    prompt = mr._build_weekend_policy_prompt(_INTEL, recs)
    assert "台灣未來帳戶" in prompt, "媒體政策清單沒進 prompt"
    assert "行政院公報" in prompt, "公報素材沒進 prompt"
    assert "以公報為準" in prompt, "未寫明公報權威性高於媒體轉述"
    assert "先措施、後影響" in prompt


def test_prompt_is_fenced_for_untrusted_sources():
    """公報素材是抓取的外部文字,批#38 的圍欄鐵律同樣適用。"""
    recs = tps.parse_gazette_xml(_XML)
    prompt = mr._build_weekend_policy_prompt(_INTEL, recs)
    assert prompt.count("<UNTRUSTED_SOURCE_DATA>") >= 1
    assert prompt.count("<UNTRUSTED_SOURCE_DATA>") == prompt.count(
        "</UNTRUSTED_SOURCE_DATA>"), "圍欄未配對"


def test_gazette_only_policy_still_produces_prompt():
    """公報獨有的政策(媒體尚未報導)一樣要能觸發解析——那正是接公報的理由。"""
    recs = tps.parse_gazette_xml(_XML)
    prompt = mr._build_weekend_policy_prompt({"policy": []}, recs)
    assert prompt and "行政院公報" in prompt


def test_no_material_means_no_prompt_and_no_section():
    """兩邊都沒素材時回空 → 整段省略,不留空標題。"""
    assert mr._build_weekend_policy_prompt({"policy": []}, []) == ""
    assert mr._build_weekend_policy_prompt(None, None) == ""
    assert mr._render_weekend_policy_html("", None) == ""
    assert mr._render_weekend_policy_html("   ", None) == ""


def test_analysis_returns_empty_when_llm_unavailable(monkeypatch):
    """無金鑰或呼叫失敗時整段省略——週報不可斷。"""
    recs = tps.parse_gazette_xml(_XML)
    monkeypatch.setattr(mr, "DEEPSEEK_API_KEY", "")
    monkeypatch.setattr(mr, "GEMINI_API_KEY", "")
    monkeypatch.setattr(mr, "ANTHROPIC_API_KEY", "")
    assert mr.analyze_weekend_policy(_INTEL, recs) == ""

    monkeypatch.setattr(mr, "DEEPSEEK_API_KEY", "k")

    def _boom(_p):
        raise RuntimeError("llm down")

    monkeypatch.setattr(mr, "_call_llm_text", _boom)
    mr._DEGRADED_STEPS.clear()
    assert mr.analyze_weekend_policy(_INTEL, recs) == ""
    assert "weekend_policy_analysis" in mr._DEGRADED_STEPS, \
        "LLM 失敗要記進降級步驟,不可靜默"


def test_analysis_html_renders_and_is_placed_before_the_list():
    """解析區塊要出現在政策清單**之前**——清單是索引、解析才是內容。"""
    html = mr._render_weekend_policy_html("### 新青安3.0\n適用對象為首購族。", None)
    assert "重大政策深度解析" in html
    assert "新青安3.0" in html

    full = mr.render_weekend_digest_html(
        "2026-07-26", "", "", "", intel_html="<h2>台灣政策近月走向</h2>",
        journals_html="", calendar_html="", policy_analysis_html=html)
    assert full.index("重大政策深度解析") < full.index("台灣政策近月走向")


def test_weekend_digest_omits_section_when_analysis_empty():
    """沒有解析時信件不得出現空的解析標題。"""
    full = mr.render_weekend_digest_html(
        "2026-07-26", "", "", "", intel_html="<h2>台灣政策近月走向</h2>",
        journals_html="", calendar_html="", policy_analysis_html="")
    assert "重大政策深度解析" not in full


def test_analysis_runs_inside_shared_llm_deadline(monkeypatch):
    """r1(Codex,P1):直接呼叫 _call_llm_text 會讓 _LLM_DEADLINE 維持未設定,
    而 _llm_request_timeout() 只在該值有設時才收斂單次逾時——Gemini 備援最多可
    連打九次、每次 75 秒再加重試睡眠,整個繞過 180 秒上限。這發生在渲染與寄信
    **之前**,夠慢的週日會撞上 workflow 25 分鐘上限,結果是**整封信沒寄出**。"""
    recs = tps.parse_gazette_xml(_XML)
    monkeypatch.setattr(mr, "DEEPSEEK_API_KEY", "k")
    seen = {}

    def _capture(_p):
        seen["deadline"] = mr._LLM_DEADLINE
        return "### 測試\n內容"

    monkeypatch.setattr(mr, "_call_llm_text", _capture)
    before = mr._LLM_DEADLINE
    out = mr.analyze_weekend_policy(_INTEL, recs)
    assert out
    assert seen["deadline"] is not None, "呼叫時未設定共用 LLM 總預算"
    assert mr._LLM_DEADLINE == before, "預算未在 finally 還原"


def test_deadline_restored_even_when_llm_raises(monkeypatch):
    recs = tps.parse_gazette_xml(_XML)
    monkeypatch.setattr(mr, "DEEPSEEK_API_KEY", "k")

    def _boom(_p):
        raise RuntimeError("down")

    monkeypatch.setattr(mr, "_call_llm_text", _boom)
    before = mr._LLM_DEADLINE
    assert mr.analyze_weekend_policy(_INTEL, recs) == ""
    assert mr._LLM_DEADLINE == before, "例外路徑未還原預算"
