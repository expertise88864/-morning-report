"""供應商以「內容風險」拒收整份提示詞時,拿掉可能觸發的新聞再送(2026-11-14 實信)。

11-14 那班 DeepSeek 回 `HTTP 400 Content Exists Risk`:它的輸入審查擋下了整份 22 萬字的提示詞,
精簡參數重送也一樣被擋(問題在內容不在參數),結果整封信沒有任何模型分析。供應商不說是哪一句,
主分析又不跨供應商備援(使用者政策 2026-08-19),所以只能自己減:

  第 1 級:帶新聞編號 `[N12]` 的列裡,拿掉含高敏感字眼的(政治異議、宗教團體、領導人等);
  第 2 級:再拿掉所有提到中國政治、兩岸、間諜的新聞列,以及沒有編號但含高敏感字眼的清單列。

只動「- 」開頭的資料列,不動寫稿規則。兩級都被擋就照舊走備援文字。被拒的請求供應商不計費。
純函式加一個呼叫包裝;挑字眼是經驗法則,供應商的規則不公開,不保證擋得住。
"""
from __future__ import annotations

import re

MARKER = "Content Exists Risk"
LABEL = "analysis:content_risk_softened"
_TAGGED = re.compile(r"^\s*[-・]\s*.*\[N\d{1,4}\]")
_LISTED = re.compile(r"^\s*[-・]\s")
_HIGH = re.compile(
    r"習近平|习近平|共產黨|中共|法輪功|六四|天安門|新疆|維吾爾|西藏|達賴|台獨|港獨|疆獨|藏獨|反送中|文革|"
    r"顛覆|大紀元|新唐人|香港國安|劉曉波|艾未未|民運|翻牆|活摘|李克強|胡錦濤|江澤民|薄熙來|政變|獨裁|"
    r"Xi Jinping|Falun|Tiananmen|Uyghur|Xinjiang|Tibet|Dalai|Epoch Times|CCP\b", re.I)
_BROAD = re.compile(
    r"間諜|间谍|情報|國安|兩岸|台海|統一|解放軍|共軍|軍演|制裁|人權|維權|異議|抗議|示威|審查|"
    r"中國|中国|北京|香港|澳門|\bspy\b|espionage|China|Chinese|Beijing|Hong Kong|Taiwan Strait|PLA\b", re.I)


def is_refusal(error) -> bool:
    return MARKER in str(error or "")


def _items(prompt: str) -> list[list[str]]:
    """把提示詞切成「一則」:清單列連同它下面縮排的延續列(高權重新聞的 `  [全文摘錄]：…`)是同一則;
    其他每一行各自一則。外審 PR #53:只刪標題列會把全文摘錄留在提示詞裡,重送照樣被拒。"""
    items: list[list[str]] = []
    for line in str(prompt or "").split("\n"):
        continuation = bool(items) and bool(_LISTED.match(items[-1][0])) and line[:1] in (" ", "\t") \
            and line.strip() and not _LISTED.match(line)
        if continuation:
            items[-1].append(line)
        else:
            items.append([line])
    return items


def soften(prompt: str, level: int) -> tuple[str, int]:
    """回傳 (減過的提示詞, 拿掉幾則)。level 1 較保守,2 較廣;判斷看整則(含延續列)。"""
    kept, dropped = [], 0
    for item in _items(prompt):
        head, whole = item[0], "\n".join(item)
        tagged = bool(_TAGGED.match(head))
        risky = (tagged and _HIGH.search(whole)) or (
            level >= 2 and ((tagged and _BROAD.search(whole)) or (_LISTED.match(head) and _HIGH.search(whole))))
        if risky:
            dropped += 1
        else:
            kept.extend(item)
    return "\n".join(kept), dropped


def call(send, prompt: str, degrade, log=print) -> tuple[str, str]:
    """`send(prompt)`;被內容風險拒收就減一級再送,最多兩級。回傳 (模型輸出, 實際送出的提示詞)。

    不是內容風險的錯誤原樣往外拋;兩級都被拒,拋最後那次的錯誤(呼叫端走備援文字)。
    """
    try:
        return send(prompt), prompt
    except Exception as exc:   # noqa: BLE001 - 只處理內容風險,其餘原樣拋
        if not is_refusal(exc):
            raise
        last = exc
    for level in (1, 2):
        softened, dropped = soften(prompt, level)
        if not dropped:
            continue
        log(f"::warning::[llm] 供應商以內容風險拒收,拿掉 {dropped} 則可能觸發的新聞後重送(第 {level} 級)")
        try:
            text = send(softened)
        except Exception as exc:   # noqa: BLE001
            if not is_refusal(exc):
                raise
            last = exc
            continue
        degrade(LABEL)
        return text, softened
    raise last
