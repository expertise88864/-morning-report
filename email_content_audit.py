"""Check the actual HTML boundary, not just model/Markdown render counters."""
from html import unescape
import re
import sys

import email_mobile

SECTIONS = ("八、科技板塊脈動", "九、其他類股資訊", "十、總體經濟與政策環境")


def _estimated_email_kb(html: str) -> float:
    """估算 Gmail 是否會剪信用的大小(KB)。
    Gmail ~102KB 截斷量的是「解碼後的 HTML 內容」本身(Email on Acid 6000+ 封實測、
    Litmus、Mailchimp 一致),而非 base64 編碼後大小——base64 信反而更晚才剪(~110KB)。
    故直接量解碼後 UTF-8 大小即可,不可再 ×1.37(那會在離真正危險還有 ~30KB 餘裕時就誤判超標,
    把使用者要看的內容過早砍掉)。"""
    return len(html.encode("utf-8")) / 1024.0


def _sections(text: str, html: bool) -> dict[str, str]:
    if html:
        text = re.sub(r"<(style|script)\b[^>]*>.*?</\1>", "", text,
                      flags=re.I | re.S)
        text = re.sub(r"<h[1-6]\b[^>]*>(.*?)</h[1-6]>",
                      lambda m: "\n## " + re.sub(r"<[^>]+>", "", m[1]) + "\n",
                      text, flags=re.I | re.S)
        text = unescape(re.sub(r"<[^>]+>", "", text))
    chunks = re.split(r"(?m)^#{1,6}\s+([^\n]+)\n?", text)
    return {chunks[i].strip(): chunks[i + 1] for i in range(1, len(chunks) - 1, 2)}


def audit(analysis: str, html: str) -> dict:
    """Only counts and fixed section labels leave this function; no news text.

    Chain markers are deterministic renderer output. Legacy prose need not
    contain them; absence in the input is never presented as a measured pass
    for individual cards. Heading checks still apply to either path.
    """
    before, after = _sections(analysis, False), _sections(html, True)
    expected = [s for s in SECTIONS if s in before]
    counts = {}
    for section in expected[:]:
        if section == SECTIONS[2]:
            continue
        counts[section] = {
            "expected": len(re.findall(r"傳導[:：]", before[section])),
            "html": len(re.findall(r"傳導[:：]", after.get(section, "")))}
    return {"expected_sections": expected,
            "missing_sections": [s for s in expected if s not in after],
            "chain_counts": counts,
            "lost_cards": sum(max(0, c["expected"] - c["html"]) for c in counts.values())}


def finalize(analysis: str, html: str, manifest: dict) -> str:
    """Caller-owned degradation: an optional transformation must not lose mail."""
    record = {}
    try:
        html = email_mobile.enhance(html)
        record["mobile"] = "enhanced" if 'id="morning-mobile"' in html else "inline_fallback"
    except Exception as exc:  # noqa: BLE001 - retain original HTML and visible warning
        record["mobile_error"] = type(exc).__name__
        print("::warning::Mobile email enhancement failed; original HTML retained", file=sys.stderr)
    try:
        record.update(audit(analysis, html))
        if record["missing_sections"] or record["lost_cards"]:
            print("::warning::Final email HTML lost analysis sections/cards", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001 - diagnostics never prevent delivery
        record["audit_error"] = type(exc).__name__
        print("::warning::Final email content audit unavailable", file=sys.stderr)
    record["html_bytes"] = len(html.encode("utf-8"))
    manifest.setdefault("llm", {})["email_html"] = record
    return html
