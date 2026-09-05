"""CR-04: notify on state publication failure without resending the report (stdlib)."""
import os
import smtplib
import ssl
import sys
from email.message import EmailMessage


def build_message(delivered: bool, result: str, run_url: str) -> EmailMessage:
    msg = EmailMessage()
    delivery = "晨報已寄出" if delivered else "晨報寄送狀態未確認"
    msg["Subject"] = f"⚠ {delivery}，state 發佈未完成({result})"
    msg.set_content(
        f"{delivery}。\n"
        "收據或 state 發佈步驟失敗／取消，遠端資料可能僅部分落地，影響後續晨報。\n"
        "請先檢查收據、artifact 與 push 紀錄，再修復 state 發佈。\n"
        "不要因這封告警直接重寄晨報；寄送狀態不明時須先查證，避免重複寄信。\n"
        f"發佈結果：{result}\n執行紀錄：{run_url}\n")
    return msg


def main() -> int:
    user = os.environ.get("GMAIL_USER", "").strip()
    password = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
    if not (user and password):
        print("::error::缺少寄信憑證，state 發佈失敗告警未寄出", file=sys.stderr)
        return 1
    msg = build_message(os.environ.get("DELIVERED", "").strip() == "true",
                        os.environ.get("PUBLISH_RESULT", "unknown"),
                        os.environ.get("RUN_URL", ""))
    msg["From"], msg["To"] = user, user
    with smtplib.SMTP_SSL("smtp.gmail.com", 465,
                          context=ssl.create_default_context(), timeout=60) as smtp:
        smtp.login(user, password)
        smtp.send_message(msg)
    print("[state-alert] 已寄出發佈失敗告警")
    return 0


if __name__ == "__main__":
    sys.exit(main())
