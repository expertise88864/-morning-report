# -*- coding: utf-8 -*-
"""CI canary 的斷言步驟:**exit 0 不等於跑成了**。

`python morning_report.py` 在下面這些情況都會 exit 0:

  * 特化路徑失敗、退回 legacy(信變淺)
  * 兩階段全文抓取整段 no-op(事件只剩 RSS 兩行摘要)
  * 昨日觀點沒存下來(明天的延續事件沒有 diff 基準)
  * **主流程在寫 manifest 之前就掛掉** —— 而 `state/run_manifest.json`
    是進版控的,checkout 之後就在那裡:斷言會讀到**上一班**的檔案,
    而上一班可能剛好是健康的(外審 P1-2)

canary 是一個很貴的 job(連真實服務、燒 API 額度、跑到 30 分鐘)——
它的綠燈如果只代表「process 沒有炸」,那筆成本買不到任何保證。

判準**不在這個檔裡**:它與每日看門狗共用 `run_quality.assess()`,
差別只在 `mode`:

    watchdog  每日生產。額度用罄退回 legacy 是外部因素,報 degraded。
    strict    canary。這個 job 的名字是「證明特化輸出真的產生了」,
              退回 legacy 就是不通過;而且要證明這份 manifest 是
              **這一次執行**產生的(SHA / run id 綁定,舊檔案永遠滿足不了)。

兩份判準各自演化的話,「canary 綠而生產壞」會再發生一次 ——
而那正是 2026-08-04 → 08-08 那五天的形狀。
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run_quality as rq  # noqa: E402

DEFAULT_MANIFEST = Path("state/run_manifest.json")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="canary 的執行品質斷言")
    ap.add_argument("--manifest", default=str(DEFAULT_MANIFEST),
                    help="要檢查的 manifest(預設 state/run_manifest.json)")
    ap.add_argument("--mode", default="strict", choices=("strict", "watchdog"))
    ap.add_argument("--expected-sha", default=os.environ.get("GITHUB_SHA", ""))
    # **nonce 要比對才是綁定**(第二十七輪外審 P2-5):workflow 產生一次、
    # 同時交給生產(`RUN_NONCE` env)與這裡,才證明得了「這份 manifest 是
    # 那一次 process invocation 寫的」。沒給就退回原本的「只驗非空」。
    ap.add_argument("--expected-nonce", default=os.environ.get("RUN_NONCE", ""))
    ap.add_argument("--expected-run-id",
                    default=os.environ.get("GITHUB_RUN_ID", ""))
    args = ap.parse_args(argv)

    path = Path(args.manifest)
    if not path.is_file():
        print("::error::canary 跑完了但沒有 run_manifest.json —— "
              "主流程在寫 manifest 之前就結束了")
        return 1
    try:
        m = json.loads(path.read_text(encoding="utf-8")) or {}
    except Exception as e:                      # noqa: BLE001
        print(f"::error::run_manifest.json 解析失敗:{e}")
        return 1
    findings = rq.assess(m, mode=args.mode,
                         expected_sha=args.expected_sha,
                         expected_run_id=args.expected_run_id,
                         expected_nonce=args.expected_nonce)
    _emit_outputs(findings)
    if not findings:
        print("[canary] 特化路徑走完了,判準全過")
        return 0
    for f in findings:
        level = "error" if f.get("severity") == "defect" else "warning"
        print(f"::{level}::{f.get('code')} —— {f.get('detail')}")
    print(rq.summarize(findings))
    # **退出碼服務 CI(strict canary 要擋),通知政策不看它。**
    return 1 if any(f.get("severity") == "defect" for f in findings) else 0


def _emit_outputs(findings) -> None:
    """把判準結果寫成 GitHub Actions step output(非 Actions 環境 no-op)。

    **通知政策不可以綁在退出碼上**(2026-09-01 外審 P1)。退出碼的語意是
    「CI 要不要擋」—— 只有 `defect` 會讓它非零。而 `analysis_not_specialized`
    (Luna 落回 legacy)、`llm:provider_refused:payment`(餘額用光)這些
    **要通知的事**都是 `degraded`:拿退出碼當通知判準的話,這套機制當初
    要抓的那件事自己不會發告警。看門狗那端則是「有任何 finding 就告警」
    —— 兩套監控對同一件事說不同的話,是更糟的狀態。
    """
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    sev = {str(f.get("severity") or "") for f in findings}
    rows = {
        "has_findings": "true" if findings else "false",
        "has_defect": "true" if "defect" in sev else "false",
        # 有任何 finding 就通知 —— 與看門狗同一個判準。
        "alertable": "true" if findings else "false",
        "max_severity": "defect" if "defect" in sev else (
            "degraded" if findings else "none"),
        # **「判準跑完了」與「判準說結果是壞的」是兩件事**(r3 外審第二輪)。
        # 這個 step 對任何 defect 刻意 `return 1`(退出碼服務 CI),於是
        # `steps.quality.outcome` 會是 `failure` —— 拿它當「判準自己崩了」
        # 的依據,會把**判準正常運作並明確指出 defect** 的那一班,
        # 誤報成「品質狀態不明」,而它其實知道得很清楚。
        # 崩潰要用「有沒有留下完成標記」判,不是用退出碼判。
        "assessed": "true",
    }
    try:
        with open(path, "a", encoding="utf-8") as fh:
            lines = [f"{k}={v}" for k, v in rows.items()]
            lines.append("summary<<QUALITY_EOF")
            lines.append(rq.summarize(findings) if findings else "")
            lines.append("QUALITY_EOF")
            fh.write("\n".join(lines) + "\n")
    except OSError as e:
        # **通道自己失效不得靜默**(2026-09-01 r2 外審):寫不進去 →
        # `quality_alertable` 缺席 → 告警 job 的條件不成立 → 沒有人收到,
        # 而步驟是 `continue-on-error`,連紅燈都沒有。那正好違反這批
        # 修正的核心:「判準有跑」不等於「有人收到」。
        # 印成 error annotation(Actions 摘要頁看得到)並**拋出去** ——
        # 步驟仍被 continue-on-error 吸收(晨報不會因此變紅),
        # 但 Actions 上會留下明確的失敗紀錄。
        print("::error title=quality-output-unwritable::"
              f"判準結果寫不進 GITHUB_OUTPUT({e})—— 品質告警這一班發不出去",
              file=sys.stderr)
        raise


if __name__ == "__main__":
    sys.exit(main())
