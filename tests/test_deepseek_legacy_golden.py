# -*- coding: utf-8 -*-
"""**凍結 DeepSeek legacy 的現況**(Luna 特化實驗的安全網)。

## 為什麼先寫這個檔

接下來要為 GPT-5.6 Luna xhigh 做深度特化:獨立的 prompt profile、Responses API
路徑、structured output、新的 shadow 實驗架構。使用者的要求是
**「保留原本目前程式針對 DeepSeek 的設計」** —— 而「保留」如果只是意圖,
它會在幾十次編輯之後靜默消失。

所以在動任何一行特化程式碼之前,先把 DeepSeek 這條路徑的**可觀測行為**釘死:

  - `_build_prompt` 產出的完整 prompt(逐位元組雜湊 + 段落順序)
  - DeepSeek 送出的 payload 形狀(思考模式開關與強度的翻譯)
  - 主分析失敗時的 fallback 文字

這幾條紅了,就表示 Luna 的特化污染到了 DeepSeek —— 那正是使用者明說不要的。

## 雜湊為什麼配上結構斷言

只有雜湊的話,壞掉時只會說「不一樣」,不會說「哪裡不一樣」;只有結構斷言的話,
段落內文被改寫也不會紅。兩者一起才既嚴格又可診斷。

**這個雜湊不是「不准改 prompt」**。DeepSeek 的 prompt 之後當然可以改進 ——
但那要是一個**刻意的、看得見的**動作:更新這裡的常數,並在 commit 說明為什麼。
"""
import hashlib
import json
import re
from pathlib import Path

import pytest

import morning_report as mr

_ROOT = Path(__file__).resolve().parents[1]
_FIXTURE = _ROOT / "tests" / "fixtures" / "legacy_prompt_input.json"

#: **2026-08-03 刻意改動**:使用者回饋「文字擠在一起、半形全形混用,
#: 要像說故事那樣有邏輯性」。R9 從「不可用全形標點」改成「中文句子一律全形」
#: (兩個有理由的例外保留:來源用半形方括號供顯示層淡化、全形括號留給簡介),
#: 並新增 R6b 敘事寫法。**這不是 Luna 污染,是使用者要求的風格變更**,
#: 因此 `DEEPSEEK_LEGACY_VERSION` 同步升到 2。
#: **2026-08-03 晚再次刻意改動**(v4):使用者看過信之後又提三件事——
#: 七之四出現英文原標題、艱澀術語沒解釋、分析只在描述數字。
#: 新增 R6c(數字要有下文)/R6d(術語白話)/R10c(外文一律中文轉述),
#: 改七之四鐵則,R12 的 C 級補上書單廣告類。`DEEPSEEK_LEGACY_VERSION` → 4。
#: 2026-08-01 於 cd41fee 量測。改動 DeepSeek prompt 時**一起改這個值**,
#: 並在 commit message 說明改了什麼、為什麼 —— 不要為了讓測試變綠而改。
#: **2026-08-04 刻意改動**(v5):使用者再提「還是在堆疊數據、沒有分析影響」。
#: 加 R17(Python 排好的表要在立場理由裡被合起來讀)、七之二 60→90 字
#: 且要求寫得出傳導路徑而不是四個字的抽象標籤。
#: **2026-08-04 二次刻意改動**(v6):使用者澄清是**LLM 自己寫的段落**在
#: 堆數據。實測八段 10 條有 10 條以方向形容詞作結、0 條說得出量級。
#: 根因是格式模板與兩個範例**自己在示範那個毛病**(模板連「幅度」都沒有)。
#: 2026-08-05 使用者七項回饋:三大重點要「事件」不是行情、七之二與
#: 八/九段要橫縱向更深、內部試算不進信(R18)、政策取材以中彰投雲為主
#: 且已公布細節的政策不得寫「待公告」(R19/R20)。
#: **2026-08-20 刻意改動**(v8):其他類股新增「金融-金控」標籤 ——
#: prompt 的類股段對固定輸入多出一節空素材(`## 金融-金控` + 佔位行)。
#: 與 HEAD 逐行 diff 過:**只有這三行**,指示文字與其他段落逐位元組相同。
# 2026-08-21:九段龍頭優先選材進 legacy prompt(v9;r2 移到圍欄外+無條件)——刻意變更
LEGACY_PROMPT_SHA256 = (
    "e14b5cc94d4b027370807394da381aa2c1e50f7555fee9c7f018926918cafafb")

#: 段落順序也是契約的一部分。LLM 對「重要的東西放前面」很敏感,
#: 而順序被改動時 prompt 雜湊會變,但雜湊說不出是順序變了還是內文變了。
LEGACY_SECTION_PREFIX = [
    "【資料品質（最優先閱讀）】",
    "【昨日美股收盤】",
    "【總經指標（昨日收盤值、變動%、252 日歷史百分位）】",
]


def legacy_prompt_inputs() -> tuple:
    """凍結用的確定性輸入。**Luna 側之後要用同一組** —— 兩邊看到的證據必須一樣。"""
    if not _FIXTURE.exists():
        pytest.fail(f"找不到 {_FIXTURE} —— 凍結測試不得因 fixture 不見而跳過")
    d = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    return (d["quotes"], d["fair"], d["predictions"],
            d["news"], d["tw0050"], d["calibration"])


def test_the_legacy_deepseek_prompt_is_byte_frozen():
    """DeepSeek 的 prompt 不得被 Luna 特化順手改掉。

    這是使用者的明確要求(「仍保留原本目前程式針對 deepseek 的設計」)。
    prompt 是那個設計裡**最容易被順手動到**的部分 —— 它沒有型別、沒有介面,
    改一行不會有任何東西變紅。
    """
    prompt = mr._build_prompt(*legacy_prompt_inputs())
    got = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    assert got == LEGACY_PROMPT_SHA256, (
        "DeepSeek legacy prompt 變了。若是**刻意**要改,請更新 "
        f"LEGACY_PROMPT_SHA256 為 {got} 並在 commit 說明改了什麼、為什麼;"
        "若不是刻意的,那就是 Luna 的特化污染到了 legacy 路徑。")


def test_the_legacy_prompt_keeps_its_section_order():
    """段落順序是契約:資料品質必須在最前面。

    「資料品質最優先」不是排版偏好 —— 它決定模型會不會在來源失敗時
    照樣編數字。雜湊會抓到順序改變,但說不出改的是順序還是內文。
    """
    prompt = mr._build_prompt(*legacy_prompt_inputs())
    # 段落標題後面可能接補充說明(「【昨日美股收盤】（…美元計價）」),
    # 所以只取行首那一對方括號,不要求整行就是標題。
    found = [re.match(r"【[^】]+】", ln.strip()).group(0)
             for ln in prompt.split("\n")
             if ln.strip().startswith("【") and "】" in ln]
    assert found[:len(LEGACY_SECTION_PREFIX)] == LEGACY_SECTION_PREFIX, (
        f"legacy prompt 的開頭段落順序變了:{found[:5]}")
    assert len(found) >= 30, (
        f"legacy prompt 只剩 {len(found)} 個段落 —— 內容大幅減少,"
        "請確認不是 Luna 特化把共用的組裝函式改掉了")


@pytest.mark.parametrize("raw,thinking,effort,canonical", [
    ("max", "enabled", "max", "max"),
    # 2026-08-13 官方表改版:xhigh 只到 high、low 有真的 low 檔
    ("xhigh", "enabled", "high", "high"),
    ("high", "enabled", "high", "high"),
    ("medium", "enabled", "high", "high"),
    ("low", "enabled", "low", "low"),
    ("off", "disabled", None, "none"),
    ("", "enabled", "high", "high"),
])
def test_the_deepseek_thinking_translation_is_frozen(raw, thinking, effort, canonical):
    """思考模式的翻譯規則不得漂移。

    **開關與強度是兩個欄位,而且思考預設是開的**(第十一輪 P1-2)。
    設 `off` 必須明確送 `disabled`;不送等於沿用預設(開著),那不是關閉。
    """
    import llm_telemetry as lt

    got = lt.deepseek_thinking(raw)
    assert got["thinking"]["type"] == thinking, f"{raw!r} 的開關變了"
    assert got["reasoning_effort"] == effort, f"{raw!r} 的強度映射變了"
    assert got["canonical"] == canonical


def test_the_legacy_fallback_still_produces_a_sendable_report():
    """主分析失敗時的降級文字必須還在(晨報不可斷)。

    Luna 特化引入新的失敗模式(strict schema 不合、Responses API 不可用),
    而那些新路徑最後都要落回這裡。它若被改掉,新的失敗模式會變成「沒有信」。
    """
    _q, _f, _p, news, _t, _c = legacy_prompt_inputs()
    text = mr._fallback_analysis_text(news, RuntimeError("讀取逾時"))
    assert isinstance(text, str) and text.strip(), "降級分析回了空字串"
    assert len(text) > 80, f"降級分析只有 {len(text)} 字元,不像一份可寄出的內容"


# ------------------------------- 第十四輪:使用者回饋的三條可讀性規則(legacy 側)

def test_the_legacy_prompt_demands_the_same_three_readability_rules():
    """**兩份 prompt 要一起改。**

    使用者明確選了「兩邊都改」——只改 Luna 的話,實驗比的就變成
    「新規則 vs 舊規則」而不是兩個模型;而在 Luna 特化路徑還沒跑成的日子,
    寄出去的信走的正是 legacy 這一份,使用者反映的問題會原封不動再來一次。

    判準是**語意**而不是雜湊:上面的 SHA 說得出「有東西變了」,
    說不出「它還要求著這三件事」。規則被後人順手刪掉時,SHA 只會叫人更新它。
    """
    prompt = mr._build_prompt(*legacy_prompt_inputs())
    missing = [name for name, needle in (
        ("R6c 數字要有下文", "只報數字不算分析"),
        ("R6c 反例/正例對照", "才是分析"),
        ("R6d 術語白話", "用一句白話解釋"),
        ("R10c 外文中文轉述", "不得整句照貼原文標題"),
        ("R10c 不得改變原意", "不能改意思"),
        ("七之四 要翻成中文", "翻成中文轉述"),
        ("C 級 排除書單廣告", "根本不是新聞的條目"),
        # v5(2026-08-04):Python 排好的表要有人解讀。突變驗證顯示只靠上面
        # 那個 SHA 的話,規則被刪掉時只會叫人「更新 SHA」——**雜湊說不出
        # 少了什麼**,所以每一條要求都要有自己的語意判準。
        ("R17 表要被合起來讀", "Python 排好的表要有人解讀"),
        ("R17 要接回今天的立場", "一致還是矛盾"),
        ("R17 矛盾不可略過", "矛盾時要明講,不可略過"),
        ("七之二 要寫得出路徑", "「所以會怎樣」要寫得出**路徑**"),
        ("七之二 禁抽象標籤", "不接受四五個字的抽象標籤"),
        # v6(2026-08-04 二次):**方向形容詞不是分析**。實測八段 10 條有
        # 10 條以方向詞作結、0 條說得出量級 —— 而根因是格式模板與兩個
        # 範例自己在示範那個毛病。改完的東西同樣要有語意判準,不能只靠 SHA。
        ("八段 要量級不要方向詞", "**量級＋時間＋信心**"),
        ("八段 禁方向詞收尾", "同樣禁止**用方向形容詞當結論收尾"),
        ("八段 判斷不出要明講", "這則新聞說不出量級"),
        ("八段 至少兩條跨條連結", "橫向連結(至少 2 條)"),
        ("八段 句式不得雷同", "句式不得雷同"),
        ("八段 格式含量級", "[量級與時間,或明說量級判斷不出來]"),
    ) if needle not in prompt]
    assert not missing, f"legacy prompt 少了這些要求:{missing}"


def test_the_legacy_prompt_keeps_the_number_density_rule_too():
    """反向:新加的「深度」規則不得把舊的「密度」規則擠掉。

    兩者管的是不同毛病 —— 一句塞三個數字(擠)與只報數字不解釋(空)。
    使用者兩種都反映過,任一條掉了都會退回去。
    """
    prompt = mr._build_prompt(*legacy_prompt_inputs())
    assert "一句話裡最多一個數字" in prompt
    assert "中文句子的標點一律用全形" in prompt
