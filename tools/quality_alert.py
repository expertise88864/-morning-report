# -*- coding: utf-8 -*-
"""品質告警信:**產出者自己**在收尾判定之後發出的那一封。

**為什麼不是由看門狗發**(2026-08-31 外審 P1,completion race):
看門狗可能在主班**還在跑**的時候啟動 —— 它讀到的 manifest 是舊的,
看不到「這班最後 Luna 會失敗」。08/31 那班正是 20 → 12 → 2 條駁回,
直到三十多分鐘的執行接近尾聲才確定落回 legacy。而晨報自己的
`alert-on-failure` 只看 job 成敗,「信寄出去了但落回 legacy」在它眼中
是成功 —— 那種缺陷可能整天沒有人知道。

**為什麼要獨立 job**:品質有瑕疵**不等於**晨報沒寄出。讓品質判準把
`send-report` 變紅,失敗告警就會說「晨報未寄出,收件人可能沒收到信」
—— 誤報比沒有告警更糟(批#93 修過同型的一次)。所以
`send-report` 保持綠、把結果寫成 job output,由這裡消費。

收件人走**品質信箱**(維運訊號與晨報本體分流,使用者 2026-08-13 指定);
沒設就退回主收件人 —— 告警不可斷。
"""
import datetime as dt
import os
import smtplib
import ssl
import sys
from email.message import EmailMessage

TPE = dt.timezone(dt.timedelta(hours=8))


def build_message(detail: str, run_url: str, now: dt.datetime) -> EmailMessage:
    """告警信本體(純函式,好測)。"""
    msg = EmailMessage()
    msg["Subject"] = (f"[晨報品質] {now:%Y-%m-%d %H:%M} "
                      "信寄出了,但有段落沒跑成")
    body = [
        "晨報跑起來也寄出去了,但**有段落沒跑成** —— 收件人今天",
        "拿到的比它該有的樣子少。判準見 `run_quality.assess()`。",
        "",
        (detail or "").strip(),
        "",
        "這封信由**產出者自己**在收尾時判定後發出(不是看門狗事後補的)",
        "—— 看門狗可能在本班還在跑的時候啟動,那時看不到最後的結果。",
        "",
        "severity 的差別:",
        "  defect   —— 程式或接線壞了(例:特化輸出被自己的驗證擋下、",
        "               信超過 09:00 才寄出、昨日觀點沒存下來)",
        "  degraded —— 讀者今天少拿到東西,但可能是外部因素",
        "",
        f"執行紀錄:{run_url}",
    ]
    msg.set_content("\n".join(body))
    return msg


def main() -> int:
    user = os.environ.get("GMAIL_USER", "")
    pwd = os.environ.get("GMAIL_APP_PASSWORD", "")
    to = (os.environ.get("QUALITY_RECIPIENT", "").strip()
          or os.environ.get("RECIPIENT", "").strip() or user)
    if not (user and pwd and to):
        print("[quality] 缺少寄信憑證,無法告警", file=sys.stderr)
        return 1
    msg = build_message(os.environ.get("QUALITY_DETAIL", ""),
                        os.environ.get("RUN_URL", ""),
                        dt.datetime.now(TPE))
    msg["From"], msg["To"] = user, to
    with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
        smtp.starttls(context=ssl.create_default_context())
        smtp.login(user, pwd)
        smtp.send_message(msg)
    print(f"[quality] 已寄出品質告警給 {to}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
