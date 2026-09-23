"""Reader-facing quality findings at the final delivered HTML boundary."""


def assess(record: dict) -> list[tuple[str, str, str]]:
    """Pure diagnostic projection; never drops or rewrites email content."""
    record = record if isinstance(record, dict) else {}
    findings = []
    missing = record.get("missing_sections") or []
    if not isinstance(missing, list):
        missing = []
    try:
        lost = int(record.get("lost_cards") or 0)
    except (TypeError, ValueError):
        lost = 0
    if missing or lost:
        findings.append(("email_content_lost", "defect",
                         "最終寄信 HTML 遺失分析內容：" + "、".join(str(s) for s in missing)
                         + f"；少 {lost} 張新聞傳導卡"))
    if record.get("audit_error") or record.get("mobile_error"):
        findings.append(("email_finalization_failed", "defect",
                         "最終郵件排版或內容檢查失敗，已保留原信"))
    try:
        html_bytes = int(record.get("html_bytes") or 0)
    except (TypeError, ValueError):
        html_bytes = 0
    if html_bytes > 102 * 1024:
        findings.append(("email_gmail_clipping_risk", "degraded",
                         f"最終 HTML 為 {html_bytes:,} bytes，超過 Gmail 約 102 KiB 的截斷風險線；"
                         "寄送成功不代表讀者能看到完整信件，需檢查收件端"))
    return findings
