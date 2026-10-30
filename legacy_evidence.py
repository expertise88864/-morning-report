"""備援路徑的逐句證據核對(使用者 2026-10-30:「備援如何增加證據正確性」)。

備援提示詞裡每則新聞前面掛一個編號 `[N12]`,模型引用時寫 `[媒體][N12]`。
寫完之後 Python 逐條核對:

  * 有沒有附編號、編號是不是真的存在;
  * 句子裡的金額／漲跌幅是否出現在**它引用的那則新聞**或行情資料裡;

核對完把 `[媒體][N12]` 換成可點的原文連結(編號不得漏進信)。
**前一週只記錄、不刪**(使用者選定):不通過的條目列進品質告警,
確認誤判率低之後把 `OBSERVE_ONLY` 改成 False 才會整條拿掉。
純函式加一份本班的編號表,不做網路呼叫;任何錯誤都退回原文 —— 晨報不可斷。
"""
from __future__ import annotations

import re
import unicodedata

#: 2026-10-30 使用者:「先觀察一週再刪」。改成 False 之後,不通過的條目整條不寄。
OBSERVE_ONLY = True
#: 要核對的段落(標題開頭)。十、台灣本地動態的素材沒有編號,不核。
AUDITED = ("七、", "七之二", "八、", "九、")
EXAMPLES = 5

_REGISTRY: dict[str, dict] = {}
_BY_KEY: dict[str, str] = {}
_ID = re.compile(r"\[(N\d{1,4})\]")
_CITE = re.compile(r"(?:\[([^\[\]\n]{1,24})\])?((?:\s*\[N\d{1,4}\])+)")
_NUMBER = re.compile(r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)\s*(%|％|億|萬|兆|美元|元|倍|bp|個基點)")
_ANY_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_META = re.compile(r"\[[A-C] 級[^\]]*\]|\*\*\[[^\]]*\]\*\*|\]\(https?://[^)\s]+\)")


def reset() -> None:
    _REGISTRY.clear()
    _BY_KEY.clear()


def fresh_ids(news):
    """組備援提示詞的起點:編號表重來(同一則新聞在同一份提示詞裡永遠同一號)。"""
    reset()
    return news or []


def tag(item) -> str:
    """備援提示詞裡這則新聞的編號(`[N12] `);同一則(同連結或同標題)整班同一號。"""
    if not isinstance(item, dict):
        return ""
    key = str(item.get("link") or item.get("url") or item.get("title") or "").strip()
    if not key:
        return ""
    if key not in _BY_KEY:
        _BY_KEY[key] = f"N{len(_BY_KEY) + 1}"
        _REGISTRY[_BY_KEY[key]] = item
    return f"[{_BY_KEY[key]}] "


def known(evidence_id: str) -> bool:
    return evidence_id in _REGISTRY


def _norm(text) -> str:
    return re.sub(r"[,\s]", "", unicodedata.normalize("NFKC", str(text or "")))


def _values(text) -> list[float]:
    out = []
    for raw in _ANY_NUMBER.findall(_norm(text)):
        try:
            out.append(float(raw))
        except ValueError:
            continue
    return out


def _item_text(item: dict) -> str:
    return " ".join(str(item.get(k) or "") for k in ("title", "summary", "excerpt", "fulltext"))


def _market_values(quotes, budget: int = 20000) -> list[float]:
    """行情資料裡的數字(漲跌幅寫成「跌 3.2%」時資料是 -3.2,取絕對值)。"""
    out, stack = [], [quotes]
    while stack and len(out) < budget:
        node = stack.pop()
        if isinstance(node, dict):
            stack.extend(v for k, v in node.items() if not str(k).startswith("NEWS"))
        elif isinstance(node, (list, tuple)):
            stack.extend(node[:200])
        elif isinstance(node, (int, float)) and not isinstance(node, bool):
            out.append(abs(float(node)))
    return out


def _supported(value: float, pool: list[float], raw: str = "") -> bool:
    """「13%」可對 12.6～13.4(四捨五入到整數);「13.2%」只對 13.15～13.25。大金額另容許 0.5%。"""
    decimals = len(raw.split(".", 1)[1]) if "." in raw else 0
    tol = max(0.5 * 10 ** -decimals + 1e-9, abs(value) * 0.005)
    return any(abs(v - value) <= tol for v in pool)


def _publisher(item: dict) -> str:
    name = str(item.get("source_name") or "").strip()
    if not name:
        parts = re.split(r"\s+-\s+", str(item.get("title") or ""))
        name = parts[-1].strip() if len(parts) > 1 else "原文"
    return name[:16] or "原文"


def _link(label: str, evidence_id: str) -> str:
    item = _REGISTRY.get(evidence_id) or {}
    url = str(item.get("link") or item.get("url") or "")
    label = label or _publisher(item)
    return f"[{label}]({url})" if url.startswith(("https://", "http://")) else f"[{label}]"


def _convert(block: str) -> str:
    def repl(m):
        ids = _ID.findall(m.group(2))
        good = [i for i in ids if known(i)]
        if not good:
            return f"[{m.group(1)}]" if m.group(1) else ""
        return "".join(_link(m.group(1) if n == 0 else "", i) for n, i in enumerate(good))
    return _CITE.sub(repl, block)


def _blocks(body: str) -> list[str]:
    return re.split(r"(\n\s*\n|\n(?=\s*(?:[-*] |\*\*【)))", body)


def _check(block: str, market: list[float]) -> list[str]:
    """這一條的問題(空 = 通過)。"""
    ids = _ID.findall(block)
    problems = []
    if not ids:
        problems.append("沒有附新聞編號")
    unknown = [i for i in ids if not known(i)]
    if unknown:
        problems.append("編號不存在:" + "、".join(unknown[:3]))
    cited = [v for i in ids if known(i) for v in _values(_item_text(_REGISTRY[i]))]
    everything = [v for item in _REGISTRY.values() for v in _values(_item_text(item))]
    for raw, unit in _NUMBER.findall(_META.sub(" ", block)):
        try:
            value = float(raw.replace(",", ""))
        except ValueError:
            continue
        if _supported(value, cited, raw) or _supported(value, market, raw):
            continue
        where = "其他新聞" if _supported(value, everything, raw) else "任何素材"
        problems.append(f"數字 {raw}{unit} 不在引用的新聞裡(查{where})")
    return problems


def audit(text: str, quotes=None, manifest=None) -> str:
    """核對、記錄、把編號換成連結。失敗就回傳拿掉編號的原文(編號不得漏進信)。"""
    try:
        return _audit(text, quotes, manifest)
    except Exception as exc:   # noqa: BLE001 - 核對壞掉不可讓晨報斷
        print(f"::warning::legacy_evidence_failed:{type(exc).__name__}", flush=True)
        return _ID.sub("", str(text or ""))


def _audit(text: str, quotes, manifest) -> str:
    import legacy_watch
    legacy_watch.capture(text)   # 編號換成連結之前,先把觀察點回顧的證據編號接住
    market = _market_values(quotes or {})
    report = {"checked": 0, "failed": 0, "dropped": 0, "examples": []}
    out = []
    for section in re.split(r"(?m)^(?=#{2,3} )", str(text or "")):
        head, nl, body = section.partition("\n")
        title = head.lstrip("# ").strip()
        if not title.startswith(AUDITED):
            out.append(head + nl + _convert(body) if nl else _convert(section))
            continue
        kept = []
        for block in _blocks(body):
            if len(re.sub(r"\[[^\]\n]*\]|\W", "", block)) < 8:   # 空白、分隔與短引言不算一條
                kept.append(_convert(block))
                continue
            report["checked"] += 1
            problems = _check(block, market)
            if problems:
                report["failed"] += 1
                if len(report["examples"]) < EXAMPLES:
                    excerpt = re.sub(r"\s+", " ", _ID.sub("", block)).strip()[:60]
                    report["examples"].append(f"{title[:6]}「{excerpt}」:{problems[0]}")
                if not OBSERVE_ONLY:
                    report["dropped"] += 1
                    continue
            kept.append(_convert(block))
        out.append(head + nl + "".join(kept))
    if isinstance(manifest, dict):
        manifest.setdefault("llm", {})["legacy_evidence"] = dict(report, observe_only=OBSERVE_ONLY,
                                                                  registered=len(_REGISTRY))
    return "".join(out)


def findings(manifest) -> list[tuple[str, str, str]]:
    """品質告警:不通過的條目(觀察期每天列出,供判斷誤判率)。"""
    llm = manifest.get("llm") if isinstance(manifest, dict) else None
    report = llm.get("legacy_evidence") if isinstance(llm, dict) else None
    if not isinstance(report, dict) or not report.get("failed"):
        return []
    mode = "觀察期,只記錄未刪除" if report.get("observe_only") else f"已拿掉 {report.get('dropped', 0)} 條"
    detail = (f"備援分析 {report.get('failed')}／{report.get('checked')} 條證據核對未通過({mode})")
    examples = [str(e) for e in report.get("examples") or []][:EXAMPLES]
    return [("legacy_evidence_unverified", "degraded", detail + ("：" + "；".join(examples) if examples else ""))]
