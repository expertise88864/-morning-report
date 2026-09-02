# -*- coding: utf-8 -*-
"""晨報看門狗:**台北 07:50** 檢查今天的信到底有沒有產出過。

(時間隨補漏跑 cron 移過兩次;寫死在註解裡的舊時間會讓事故當下
 對錯時間軸 —— 2026-08-28 外審 P2 指出這裡與 workflow 都還停在 07:30。)

**為什麼需要它(不是「多一層保險」而已)**:
`.github/workflows/morning-report-b.yml` 的註解已經寫明殘餘風險——
morning 與 podcast 共用 `state-writers` 這個 concurrency group 且不取消,
一旦某個 run 在 **pending 階段**被第三個 run 擠掉,job 根本不會啟動,
於是**連 workflow 內的告警步驟也不會執行**。那種失敗是完全無聲的:
沒有信、沒有告警、Actions 頁面上只是一個被取消的排隊項目。

所以看門狗必須:
  1. 跑在**不同的 concurrency group**——否則它會排在它要監看的那個 run 後面,
     等到輪到它時,要監看的 run 早就結束了(或它自己也被擠掉)。
  2. 只讀不寫——它不能參與 state 競爭,否則自己變成問題來源。

判定依據是 `state/run_manifest.json` 的 `date`(每次執行都會更新)。
用它而不是 history.json:週日輕量信在沒有新內容時本來就可能不寄,
但 manifest 只要跑過就會更新,不會產生假警報。

**批#N(2026-08-08):「有跑」與「跑成了」是兩件事。**
2026-08-04 → 08-08 連續五天,特化路徑每天被自己的引用檢查擋下、
退回 legacy;信照樣寄出、manifest 照樣更新 —— 這個看門狗全程安靜,
使用者是把信貼進對話裡才發現的。判準搬到 `run_quality.assess()`
(純函式,吃 manifest);這裡只負責接線與告警文字。

回傳碼:0=正常,1=沒跑起來/沒寄到,2=跑起來了但**跑壞了**
(呼叫端據此寄不同主旨的告警信)。
"""
import datetime as dt
import json
import os
import sys
from pathlib import Path

TPE = dt.timezone(dt.timedelta(hours=8))
MANIFEST = Path("state/run_manifest.json")
#: **新鮮度判準與冪等守衛同一個:台北日曆日**(2026-08-27 r2 外審)。
#:
#: 先前是「3 小時內」,而 `morning_report.already_delivered_today` 用的是
#: 「manifest 日期是今天(台北)」—— 同一個問題(今天的晨報跑過了嗎)
#: 兩個尺度。加了補漏跑之後看門狗移到 08:05,於是 04:30 手動跑成功、
#: 兩個排程都正確地跳過的那一天,看門狗會因為「3.5 小時前」發假警報。
#: 假警報會訓練人忽略告警,那比沒有看門狗更糟。
#:
#: 保留環境變數當逃生門:設了就回到舊的小時判準。
MAX_AGE_HOURS = os.environ.get("WATCHDOG_MAX_AGE_HOURS", "").strip()


def manifest_age_hours(now: dt.datetime, path: Path = MANIFEST):
    """回 (age_hours, 讀到的日期字串)。檔案不存在或無法解析回 (None, 原因)。"""
    if not path.exists():
        return None, "run_manifest.json 不存在"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        return None, f"run_manifest.json 解析失敗: {e}"
    stamp = str((raw or {}).get("date") or "").strip()
    if not stamp:
        return None, "run_manifest.json 沒有 date 欄位"
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            when = dt.datetime.strptime(stamp, fmt).replace(tzinfo=TPE)
        except ValueError:
            continue
        return (now - when).total_seconds() / 3600.0, stamp
    return None, f"run_manifest.json 的 date 無法解析: {stamp!r}"


def _too_old(now: dt.datetime, stamp: str, age_hours: float) -> bool:
    """這份 manifest 是不是**不屬於今天**(台北)。

    判準與 `morning_report.already_delivered_today` 同一個 —— 兩邊問的是
    同一件事,用兩個尺度只會在邊界互相打架。`WATCHDOG_MAX_AGE_HOURS`
    設了才回到舊的小時判準(逃生門)。
    """
    if MAX_AGE_HOURS:
        try:
            return age_hours > float(MAX_AGE_HOURS)
        except ValueError:
            pass
    return str(stamp or "")[:10] != now.strftime("%Y-%m-%d")


#: `delivery_state()` 的四種結果。
EVIDENCE_LEGACY_MISSING = "legacy_missing"    #: 真舊檔:當時還沒有這個欄位
EVIDENCE_CURRENT_MISSING = "current_missing"  #: 現行世代卻沒有 —— writer 壞了
EVIDENCE_INVALID = "invalid"                  #: 有,但型別不對
EVIDENCE_VALID = "valid"


def _rq_delivery_outcome(dv) -> str:
    """`delivery` 的**終局狀態** —— 判準本體在 `run_quality`,這裡只是轉接。

    r8 外審:先前每個 consumer 各自把 `success` 與 `skipped_reason` 排成
    自己的順序,於是同一份 state 在看門狗主流程與 `fresh_conclusion()`
    說**不同的話**。收斂成一個狀態機之後,順序不再是誰寫的問題。
    """
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import run_quality as _rq
    return _rq.delivery_outcome(dv)


def delivery_state(path: Path = MANIFEST):
    """manifest 裡的寄送結果 → `(狀態, delivery dict)`。

    2026-09-01 r7 外審:先前**三種狀態壓成一個 `{}`** ——
    真舊檔沒有這個欄位、現行世代的 writer 沒寫出來、欄位型別壞掉,
    全部被解讀成「舊格式,正常」。而看門狗存在的理由正是
    「**有跑過 ≠ 有成功寄到**」:證據不見了或壞掉,恰恰是最該吵的時候,
    卻被當成最安靜的那一種。

    `manifest_schema` 已經正式到 v2,現行 writer 的 manifest 再缺
    `delivery`,已經不能叫 legacy。
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8")) or {}
    except Exception:                       # noqa: BLE001 - 讀不到就當沒證據
        return EVIDENCE_LEGACY_MISSING, {}
    if not isinstance(raw, dict):
        return EVIDENCE_INVALID, {}
    # **先看寄送證據本身**(r7 外審第三輪):`manifest_schema` 壞掉**不會**
    # 讓一個明確的 `success: true` 失效。先前我把 schema 檢查排在前面,
    # 於是「版本壞掉但確實寄出了」被判成「沒寄到」(rc=1)——
    # 而 rc=1 會觸發自動補寄:**把漏報換成了重複寄信**,那是收不回來的
    # 那一邊。版本壞掉是品質缺陷,由 `run_quality` 報(rc=2),
    # 不是由看門狗宣稱沒寄到。
    if "delivery" in raw:
        d = raw["delivery"]
        if not isinstance(d, dict):
            return EVIDENCE_INVALID, {}
        return EVIDENCE_VALID, d
    # 到這裡才需要問「這份檔有沒有義務寫出 delivery」。
    # **key 在不在要問 key**(`run_quality` 已經確立過同一條:欄位缺席才是
    # 舊檔,存在但無效是**壞掉**)。先前用 `.get()` 的回傳值判,於是
    # `manifest_schema: null / "2" / false` 全都被當成「真舊檔」——
    # 版本資訊壞掉的檔反而拿到最寬鬆的待遇。
    if "manifest_schema" not in raw:
        # **豁免要有截止日**(r8 外審):只看 key 在不在,等於一個永遠不會
        # 到期的 legacy 豁免 —— 而上面已經確認這份 manifest 是**今天**的。
        # 截止日由 `run_quality` 擁有(判準只有一份)。
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        import run_quality as _rq
        day = _rq._sla_business_day(raw.get("date"))
        if day is not None and day >= _rq.MANIFEST_SCHEMA_REQUIRED_FROM:
            return EVIDENCE_INVALID, {}     # 這麼新的檔不可能是舊格式
        return EVIDENCE_LEGACY_MISSING, {}  # 真舊檔:當時還沒有世代標記
    schema = raw["manifest_schema"]
    if (isinstance(schema, bool) or not isinstance(schema, int)
            or schema < 1):
        return EVIDENCE_INVALID, {}         # 版本壞掉**又**沒有寄送證據
    return EVIDENCE_CURRENT_MISSING, {}


def quality_findings(path: Path = MANIFEST) -> list:
    """今天這一班的品質判準(判準本體在 `run_quality`)。"""
    try:
        raw = json.loads(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return []
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import run_quality as _rq
    return _rq.assess(raw)


def main() -> int:
    now = dt.datetime.now(TPE)
    age, info = manifest_age_hours(now)
    if age is None:
        print(f"[watchdog] 異常:{info}", file=sys.stderr)
        return 1
    if _too_old(now, info, age):
        print(f"[watchdog] 異常:最後一次執行是 {info}"
              f"({age:.1f} 小時前)——今天的晨報可能整個沒有跑起來",
              file=sys.stderr)
        return 1
    # 批#73(第七輪 P2-2):**「有跑過」不等於「有寄到」。**
    # 只看時間戳的話,這些情境會被誤判成正常:
    #   - 05:30 手動跑過、06:00 正式排程在 pending 被擠掉 → 07:30 時 age < 3h
    #   - manifest 更新了,但在寄信那一步失敗
    # 而看門狗存在的理由正是後者。
    evidence, delivery = delivery_state()
    if evidence == EVIDENCE_LEGACY_MISSING:
        # 真舊格式 manifest 沒有這個欄位。**不當成異常**——那會在部署當天
        # 產生一次確定的假警報,而假警報會訓練人忽略告警。
        print(f"[watchdog] 正常(舊格式 manifest,無寄送欄位):{info}"
              f"({age:.1f} 小時前)")
        return _quality_exit(info)
    if evidence == EVIDENCE_CURRENT_MISSING:
        print(f"[watchdog] 異常:{info} 的 manifest 有世代標記卻**沒有寄送"
              "欄位** —— 這不是舊檔,是 writer 沒寫出來,"
              "今天有沒有寄到查不出來", file=sys.stderr)
        return 1
    if evidence == EVIDENCE_INVALID:
        print(f"[watchdog] 異常:{info} 的寄送紀錄型別壞掉 —— "
              "今天有沒有寄到查不出來", file=sys.stderr)
        return 1
    # **一個狀態機,不是幾個 if**(r8 外審):`success` 與 `skipped_reason`
    # 的排列順序先前各處不同,同一份 state 在這裡與 `fresh_conclusion()`
    # 說不同的話。矛盾的組合(同時宣稱寄出與刻意不寄)現在會被拒絕。
    _outcome = _rq_delivery_outcome(delivery)
    if _outcome == "invalid":
        print(f"[watchdog] 異常:{info} 的寄送紀錄自相矛盾或型別壞掉"
              f"(success={delivery.get('success')!r}、"
              f"skipped_reason={delivery.get('skipped_reason')!r}) —— "
              "今天有沒有寄到查不出來", file=sys.stderr)
        return 1
    if _outcome == "intentionally_skipped":
        # 刻意不寄(週日無新內容)。批#69 r2 才剛修掉同型的假警報。
        # **但控制面的缺陷仍然要驗**(r8 外審):先前這裡直接 `return 0`,
        # 於是 schema 壞掉之類的問題在「刻意不寄」的日子完全無聲 ——
        # 而 workflow 的品質自評只在 `run_outcome == delivered` 時跑,
        # 那條路也補不到。信的內容不必驗(今天本來就沒有信)。
        print(f"[watchdog] 正常:{info} 刻意未寄信"
              f"({delivery.get('skipped_reason')})")
        return _control_plane_exit(info)
    if _outcome != "delivered":
        print(f"[watchdog] 異常:{info} 有執行但**沒有成功寄出**"
              f"(狀態={_outcome}、attempted={delivery.get('attempted')}、"
              f"run_kind={delivery.get('run_kind')})", file=sys.stderr)
        return 1
    print(f"[watchdog] 正常:{info} 已寄出({age:.1f} 小時前、"
          f"run_kind={delivery.get('run_kind')})")
    return _quality_exit(info)


#: 自動補寄的開關(repo variable 可關)。預設開 —— 使用者 2026-08-28 定案。
AUTO_RESCUE = (os.environ.get("WATCHDOG_AUTO_RESCUE", "1").strip()
               not in ("0", "false", "no", ""))


def fresh_conclusion(now: dt.datetime) -> str:
    """origin/main **當下**說今天有結論了嗎(沒有回空字串)。

    看門狗讀的是 checkout 裡的 manifest,而排程觸發的 checkout 檢出的是
    **排程事件建立當時**的 commit —— 主班在那之後才寄成功的話,看門狗
    看不到,於是誤判「今天沒寄」而去補寄(r9 外審 P1)。
    這裡讀 workflow 在 job 內 fetch 出來的那兩份(檔案路徑由環境變數傳),
    Python 不自己跑 git:測試就不會碰網路。
    """
    for env in ("WATCHDOG_FRESH_RECEIPT", "WATCHDOG_FRESH_MANIFEST"):
        path = (os.environ.get(env) or "").strip()
        if not path:
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError, UnicodeDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        if str(data.get("date") or "")[:10] != now.strftime("%Y-%m-%d"):
            continue
        d = data.get("delivery")
        if not isinstance(d, dict):
            continue
        # 同一個狀態機 —— 這裡與看門狗主流程不可以對同一份 state 說不同的話。
        _o = _rq_delivery_outcome(d)
        if _o == "delivered":
            return f"origin/main 說今天已寄出({d.get('run_kind') or '?'})"
        if _o == "intentionally_skipped":
            return f"origin/main 說今天刻意不寄({d['skipped_reason']})"
    return ""


def morning_runs_active_today(now: dt.datetime, get_json) -> int:
    """今天(台北)還在排隊/執行中的晨報 run 有幾個。讀不到回 **-1**。

    **check-time ≠ execution-time**(r9 外審):主班還在跑的時候 manifest
    當然是舊的,看門狗若只看 manifest 就會判「沒寄」而去補 —— 而補寄班
    排在主班後面,等它執行時主班早就寄成功了。有人在跑就不要插隊。
    """
    try:
        data = get_json(
            "https://api.github.com/repos/expertise88864/-morning-report"
            "/actions/workflows/morning-report-b.yml/runs?per_page=20")
        today = now.strftime("%Y-%m-%d")
        n = 0
        for r in (data or {}).get("workflow_runs") or []:
            if str(r.get("status") or "") not in ("queued", "in_progress",
                                                  "waiting", "requested",
                                                  "pending"):
                continue
            stamp = str(r.get("created_at") or "")
            if not stamp:
                continue
            when = dt.datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=dt.timezone.utc).astimezone(TPE)
            if when.strftime("%Y-%m-%d") == today:
                n += 1
        return n
    except Exception as e:                  # noqa: BLE001
        print(f"[watchdog] 查不出今天在跑的晨報({type(e).__name__})",
              file=sys.stderr)
        return -1


def rescue_decision(rc: int, dispatch_runs_today: int, *,
                    active_runs: int, fresh_verdict: str,
                    enabled: bool = True) -> tuple:
    """要不要自動補寄?回 `(要不要, 理由)`。**純函式,判準在這裡。**

    2026-08-28:GitHub 連四個排程觸發點沒有建立 run(看門狗自己也一起
    沒跑),使用者早上發現沒信、由人工補寄。這一支把那個動作自動化。
    **它會主動寄信給收件人,所以判準一律取保守的那一邊:**

      * 只在 `rc == 1`(今天沒跑起來 / 沒寄成功)才補。`rc == 2` 是
        「寄到了但品質差」—— 信已經在收件匣裡,再寄一封是打擾不是修復。
        `rc == 0` 更不用說。
      * **一天最多補一次**:判準是「今天已經有 workflow_dispatch 的
        run 了嗎」(可觀察的事實,不必另外存狀態)。已經補過卻仍然沒信,
        那就不是排程漏跑,再補一次也不會好 —— 留給人看。
        使用者自己手動補過的那一天同樣不再自動補,理由相同。
      * 開關關掉就不補(逃生門)。

    **補寄不取代告警**:兩件事都要做,而且告警要說有沒有補成功。
    無聲的自動修復會讓「排程壞了」這件事永遠沒有人知道。
    """
    if not enabled:
        return False, "自動補寄已關閉(WATCHDOG_AUTO_RESCUE)"
    # **新鮮證據優先於 checkout**(r9 外審 P1):checkout 是排程事件建立
    # 當時的快照,主班在那之後寄成功的話,rc 會是 1 而信其實已經到了。
    if fresh_verdict:
        return False, fresh_verdict
    # **有人在跑就不要插隊**:主班還在跑時 manifest 當然是舊的,而補寄班
    # 排在它後面 —— 等補寄班執行時主班早就寄成功了。查不出來也不補。
    if active_runs != 0:
        return False, ("查不出今天有沒有晨報在跑 —— 不知道的時候不寄"
                       if active_runs < 0 else
                       f"今天還有 {active_runs} 個晨報 run 在排隊/執行中")
    if rc != 1:
        return False, ("今天的信已經寄出(品質另計)" if rc == 2 else "一切正常")
    # **只有「確定是 0」才補**。第一版寫 `> 0`,於是查不出來的 `-1` 一路
    # 放行 —— 而上面那段 docstring 說的是「查不出來就不補」。宣稱與實作
    # 差的那一層,正好是這個能力最危險的地方(不知道的時候多寄一封)。
    if dispatch_runs_today != 0:
        if dispatch_runs_today < 0:
            return False, "查不出今天補寄過幾次 —— 不知道的時候不寄"
        return False, (f"今天已經有 {dispatch_runs_today} 次手動/補寄觸發 —— "
                       "再補一次也不會好,留給人判斷")
    return True, "今天沒有寄成功、也還沒有補寄過"


def dispatch_runs_today(now: dt.datetime, get_json) -> int:
    """今天(台北)已經有幾次 `workflow_dispatch` 的晨報 run。

    `get_json(url)` 由呼叫端注入 —— 測試不碰網路。讀不到就回 **-1**
    (呼叫端據此**不補**:查不出來就是不知道,而「不知道」時多寄一封
    的代價比少寄一封高)。
    """
    try:
        data = get_json(
            "https://api.github.com/repos/expertise88864/-morning-report"
            "/actions/workflows/morning-report-b.yml/runs"
            "?event=workflow_dispatch&per_page=20")
        today = now.strftime("%Y-%m-%d")
        n = 0
        for r in (data or {}).get("workflow_runs") or []:
            stamp = str(r.get("created_at") or "")
            if not stamp:
                continue
            when = dt.datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=dt.timezone.utc).astimezone(TPE)
            if when.strftime("%Y-%m-%d") == today:
                n += 1
        return n
    except Exception as e:                  # noqa: BLE001
        print(f"[watchdog] 查不出今天的補寄次數({type(e).__name__})",
              file=sys.stderr)
        return -1


def _quality_exit(info: str) -> int:
    """跑起來也寄到了 —— 再問一次「跑成了嗎」。

    **回 2 而不是 1**:呼叫端要能分辨「今天沒有信」與「今天的信比它
    該有的樣子差」—— 兩者的緊急程度與該做的事都不同。
    """
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import run_quality as _rq
    findings = quality_findings()
    if not findings:
        return 0
    print(f"[watchdog] 品質異常({info}):\n" + _rq.summarize(findings),
          file=sys.stderr)
    return 2


#: (r9 外審後改用 finding 自己宣告的 `domain`,不再從名字猜 ——
#:  實測 47 個 code 裡只有 7 個符合舊的前綴表,而 `fetch_plan_*` /
#:  `payload_*` / `phantom_refs` 這些內容類全都會被當成控制面。)


def _control_plane_exit(info: str) -> int:
    """**刻意不寄的日子也要驗控制面**(r8 外審)。

    先前這條路直接 `return 0`:於是 `manifest_schema` 壞掉之類的問題,
    在「今天不寄信」的日子完全無聲 —— 而 workflow 的品質自評只在
    `run_outcome == 'delivered'` 時跑,那條路也補不到。

    但**不能**直接跑完整判準:那裡面有一大類「信的內容夠不夠好」的
    判準,而今天本來就沒有信 —— 硬跑會製造假警報,
    而假警報會訓練人忽略告警(這個系統修過三次同型問題)。
    所以只留控制面的那些。
    """
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import run_quality as _rq
    findings = [f for f in quality_findings()
                if f.get("domain") != _rq.DOMAIN_CONTENT]
    if not findings:
        return 0
    print(f"[watchdog] 控制面異常({info},今天刻意不寄信):" + chr(10)
          + _rq.summarize(findings), file=sys.stderr)
    return 2


def _rescue_cli() -> int:
    """`--rescue`:workflow 用的入口。**永遠回 0** —— 補寄失敗不得讓
    看門狗這個 job 變紅到蓋掉真正的告警;結果寫進 `GITHUB_OUTPUT`
    讓告警信帶上去。"""
    done, why = rescue(dt.datetime.now(TPE), 1)
    print(f"[watchdog] 自動補寄:{'已觸發' if done else '未觸發'} —— {why}")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            label = "已自動觸發補寄" if done else "未補寄"
            fh.write("note=%s:%s\n" % (label, why))
    return 0




def rescue(now: dt.datetime, rc: int, *, get_json=None, post=None) -> tuple:
    """判斷 + 執行補寄。回 `(有沒有補, 一句話說明)`;**永不拋**。

    看門狗的本業是告警,補寄失敗不得把告警一起弄死。
    """
    if get_json is None or post is None:
        import urllib.request as _u

        def _req(url, data=None):
            req = _u.Request(
                url, data=data,
                headers={"Accept": "application/vnd.github+json",
                         "Authorization":
                             f"Bearer {os.environ.get('GITHUB_TOKEN', '')}",
                         "Content-Type": "application/json",
                         "User-Agent": "morning-report-watchdog"},
                method="POST" if data is not None else "GET")
            with _u.urlopen(req, timeout=30) as r:
                return json.loads(r.read() or b"{}") if data is None else True
        get_json = get_json or (lambda url: _req(url))
        post = post or (lambda url, body: _req(url, json.dumps(body).encode()))
    # **先問一次不需要網路的部分**。用 `0`(唯一可能回 True 的計數)去問:
    # 它都說不補的話,任何計數都不會補 —— 判準單調,所以這個提前退出
    # 不可能與下面那次的結論相反(判準仍然只有 `rescue_decision` 一份)。
    fresh = fresh_conclusion(now)
    pre = rescue_decision(rc, 0, active_runs=0, fresh_verdict=fresh,
                          enabled=AUTO_RESCUE)
    if not pre[0]:
        return False, pre[1]
    try:
        go, why = rescue_decision(
            rc, dispatch_runs_today(now, get_json),
            active_runs=morning_runs_active_today(now, get_json),
            fresh_verdict=fresh, enabled=AUTO_RESCUE)
        if not go:
            return False, why
        # **標記成補寄**:晨報那端據此套用同日冪等(執行當下再判一次)。
        # 沒有這個標記的 `workflow_dispatch` 是使用者的人工救援,不受擋。
        post("https://api.github.com/repos/expertise88864/-morning-report"
             "/actions/workflows/morning-report-b.yml/dispatches",
             {"ref": "main", "inputs": {"rescue": "true"}})
        return True, "已自動觸發補寄(workflow_dispatch)"
    except Exception as e:                  # noqa: BLE001
        return False, f"自動補寄失敗({type(e).__name__})—— 需要人工補寄"


# **`__main__` 一定要在最後**(r1 外審 P1):`rescue()` 原本被 append 到
# 檔案尾端、在這個區塊之後 —— `python tools/report_watchdog.py --rescue`
# 執行到這裡時它還沒定義,`NameError` 當場炸掉。而 8 條測試全綠,
# 因為它們是 `import` 模組(定義全跑完)再直接呼叫函式,
# **從來沒有走過生產真正用的那個入口**。
if __name__ == "__main__":
    sys.exit(_rescue_cli() if "--rescue" in sys.argv[1:] else main())
