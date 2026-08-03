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
#: 2026-08-01 於 cd41fee 量測。改動 DeepSeek prompt 時**一起改這個值**,
#: 並在 commit message 說明改了什麼、為什麼 —— 不要為了讓測試變綠而改。
LEGACY_PROMPT_SHA256 = "6e6d8428f494f6d96f9b3b743d1e2899158be2ecd6645997d57bebd1e5c9cefc"

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
    ("xhigh", "enabled", "max", "max"),
    ("high", "enabled", "high", "high"),
    ("medium", "enabled", "high", "high"),
    ("low", "enabled", "high", "high"),
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
