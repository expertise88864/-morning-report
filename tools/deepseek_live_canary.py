# -*- coding: utf-8 -*-
"""**線上契約金絲雀**:DeepSeek 現在還是不是我們解析的那個形狀。

離線的契約測試(`tests/test_deepseek_contract.py`)釘的是 2026-08-08 的
實機 fixture —— 它守得住「我們的 adapter 有沒有被改壞」,守不住
「provider 有沒有換契約」。而後者的第一個徵兆會是**某天早上的信壞掉**,
在使用者手上,而不是在 CI 裡。

判準走 `deepseek_responses.contract_problems()`,**與離線測試同一份** ——
兩邊各寫一次的話,金絲雀綠燈與測試綠燈會是兩件事。

請求刻意做到最小:最短的 instructions/input、`effort=minimal`、
`max_output_tokens` 壓到個位數量級。這支程式只問「形狀對不對」,
不問模型答得好不好。

用法:
    python tools/deepseek_live_canary.py            # 需要 DEEPSEEK_API_KEY
    python tools/deepseek_live_canary.py --model deepseek-v4-flash

退出碼:
    0 = 形狀符合
    1 = 契約變了(去改 adapter)
    2 = 這一次跑不起來(沒有金鑰、網路、服務忙碌)
    3 = **狀態層壞掉** —— 沒有持久化狀態就執行不了「多久沒驗證過」那條
        政策,而那正是這支程式唯一的升級機制(第二十八輪外審 P2-3:
        cache 一直 restore/save 失敗時,每一班都以為自己是第一次,
        於是永遠停在 2、永遠綠燈)。
**「跑不起來」與「契約變了」要分得開** —— 前者不該讓人去改 adapter。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import deepseek_responses as ds  # noqa: E402

_URL = "https://api.deepseek.com/v1/responses"
_DEFAULT_MODEL = "deepseek-v4-flash"


def _payload(model: str) -> dict:
    """**用生產的那個組裝器**,不是手寫一份 JSON。

    手寫的話,`build_payload` 少送一個必填欄位時金絲雀照樣綠 ——
    而那正是它該抓到的事。
    """
    return ds.build_payload(
        model=model,
        instructions="回答時只輸出一個 JSON 物件。",
        user_input='用 {"ok": true} 回答。',
        effort="minimal",
        verbosity="low",
        max_output_tokens=64,
    )


#: 狀態檔的預設位置。**放在 repo 裡而不是 CI cache**(第二十八輪外審
#: 第二輪 F3):cache 對「一週一班」是錯的儲存 —— GitHub 七天未用就清掉,
#: 而我們的間隔正好是七天。一直沒命中的話,每一班都以為自己是第一次,
#: 於是永遠停在「暫時性」而週週綠燈。repo 是這個 job 拿得到的持久層,
#: 而且可驗證:checkout 一定會把它帶回來。
DEFAULT_STATE = "state/deepseek_canary.json"

#: **契約多久沒被驗證過就當成缺陷**(天)。
#:
#: 第二十七輪外審 P2-3:RC=2 只印 warning 然後綠燈 —— secret 被刪掉之後
#: 可以每週綠燈、永遠沒有真正監測 provider contract。
#: 第一版數「連續幾次」,而那個數字被同日 rerun 灌高、被漏跑的班次打斷
#: (外審第二、三輪各抓到一次)。**「連續幾次」本來就不是要量的東西** ——
#: 要量的是「距離上一次真的驗證過,過了多久」。那個問題有一個明確的答案,
#: 而且 rerun 與漏班都不會動到它。
CADENCE_DAYS = 7               # 這支程式一週跑一次
STALE_AFTER_DAYS = CADENCE_DAYS * 3     # 三班沒驗證過就升級


def _days_between(a: str, b: str):
    """`b - a` 的天數(任一邊解析不了回 `None`)。"""
    import datetime as _dt
    try:
        return (_dt.date.fromisoformat(str(b))
                - _dt.date.fromisoformat(str(a))).days
    except (TypeError, ValueError):
        return None


def _record(path, ok: bool, stamp: str) -> dict:
    """更新並回傳狀態(`last_success` / `first_unavailable`)。

    純檔案操作,失敗不影響判定 —— 這支程式的主業是回報契約。
    """
    import json as _json
    cur, readable = {}, True
    if path and os.path.exists(path):
        # **「沒有這個檔」與「讀不動這個檔」是兩件事**(外審第二輪 F2):
        # 上一版把讀取例外一律吞成 `{}`,然後**覆寫**掉那份歷史 ——
        # 升級時鐘於是被一個壞掉的檔案重設,而沒有人看得出來。
        try:
            cur = _json.loads(open(path, encoding="utf-8").read())
            readable = isinstance(cur, dict)
        except Exception:                               # noqa: BLE001
            cur, readable = {}, False
    if ok:
        out = {"last_success": stamp, "first_unavailable": ""}
    else:
        out = {"last_success": cur.get("last_success", ""),
               "first_unavailable": (cur.get("first_unavailable")
                                     or stamp or "")}
    out["_persisted"] = readable
    if not readable:
        print(f"[canary] 既有狀態讀不動({path})—— **不覆寫**,"
              "升級時鐘由人處理", file=sys.stderr)
        return out
    if path:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                _json.dump({k: v for k, v in out.items()
                            if not k.startswith("_")}, f, ensure_ascii=False)
        except Exception as e:                          # noqa: BLE001
            # **寫不進去不是「不影響判定」**(第二十八輪外審 P2-3):
            # 「多久沒驗證過」整條政策靠這個檔活著,寫不進去等於每一班
            # 都從零開始 —— 那正是這支程式要關掉的「永遠綠燈」。
            print(f"[canary] 狀態寫不進去({e})—— 升級政策失效",
                  file=sys.stderr)
            out["_persisted"] = False
    return out


def _unavailable(state, stamp, why: str) -> int:
    """跑不起來:記一次,**距離上次驗證過太久**就升級成失敗。"""
    st = _record(state, False, stamp)
    if state and not st.get("_persisted"):
        # **狀態層壞掉不是「暫時性」**(第二十八輪外審 P2-3):
        # 「多久沒驗證過」整條政策靠這個檔活著 —— 它一直壞的話,
        # 每一班都以為自己是第一次,於是永遠停在 2、永遠綠燈。
        print("[canary] 沒有持久化狀態,「多久沒驗證過」判斷不了",
              file=sys.stderr)
        return 3
    # **從「上次驗證過」起算**。從來沒成功過的話,`first_unavailable`
    # 是這個窗口的**起點**而不是起點前一班 —— 直接拿它當基準會少算一班
    # (三次每週失敗只走到第 14 天,要到第四次才升級;外審第四輪)。
    # 往前推一個排程間隔,語意就與「上次驗證過」對齊。
    since, age = st.get("last_success") or "", None
    if since and stamp:
        age = _days_between(since, stamp)
    elif st.get("first_unavailable") and stamp:
        gap = _days_between(st["first_unavailable"], stamp)
        age = None if gap is None else gap + CADENCE_DAYS
    if age is not None and age >= STALE_AFTER_DAYS:
        print(f"[canary] 契約已經 {age} 天沒有被驗證過({why})—— "
              "這已經不是暫時性的:金鑰可能被刪或撤銷,而 provider 換契約"
              "我們不會知道", file=sys.stderr)
        return 1
    print(f"[canary] 跑不起來({why});距上次驗證 "
          f"{'未知' if age is None else age} 天,到 {STALE_AFTER_DAYS} 天才升級",
          file=sys.stderr)
    return 2


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="DeepSeek 線上契約金絲雀")
    ap.add_argument("--model", default=_DEFAULT_MODEL)
    ap.add_argument("--timeout", type=float, default=120.0)
    # **預設不動狀態**(外審第三輪):把預設改成 repo 的路徑之後,
    # 文件寫的本機用法(不帶參數直接跑)會寫到版控裡的檔案、甚至把
    # `last_success` 洗掉 —— 而 workflow 本來就明講了路徑。
    # `DEFAULT_STATE` 留著當**唯一的一份宣告**(workflow 與守衛都指它)。
    ap.add_argument("--state", default="",
                    help="連續次數的狀態檔(workflow 用 cache 帶著走)")
    ap.add_argument("--now", default="",
                    help="今天的日期(由呼叫端給,程式本身不讀時鐘)")
    ap.add_argument("--expect-restored", action="store_true",
                    help="呼叫端說上一班的狀態應該存在;而它不在 → "
                         "狀態層壞掉。狀態改放 repo(見 workflow)之後,"
                         "checkout 一定會帶回來 —— 所以「檔案不在」只可能是"
                         "還沒 bootstrap,這個旗標留給別的呼叫端。")
    args = ap.parse_args(argv)

    if args.expect_restored and args.state and not os.path.exists(args.state):
        # **cache 說上一班存過,而檔案不在** —— 那是跨 run 的持久層壞了,
        # 而「多久沒驗證過」整條政策靠它活著。這一格壞掉時,金絲雀會
        # 每一班都以為自己是第一次(外審第二輪 F3)。
        print(f"[canary] 上一班的狀態應該在 {args.state},而它不在 —— "
              "跨 run 的持久層壞了,升級政策失效", file=sys.stderr)
        return 3
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        return _unavailable(args.state, args.now, "沒有 DEEPSEEK_API_KEY")
    try:
        import requests
        r = requests.post(_URL, json=_payload(args.model), timeout=args.timeout,
                          headers={"Authorization": f"Bearer {key}",
                                   "Content-Type": "application/json"})
    except Exception as e:                              # noqa: BLE001
        return _unavailable(args.state, args.now,
                            f"送不出去:{type(e).__name__}")
    if r.status_code != 200:
        # 429/5xx 是服務狀況,不是契約 —— 分開回報,否則暫時性的忙碌
        # 會被讀成「DeepSeek 改了 API」而讓人去改 adapter。
        body = (r.text or "")[:300]
        print(f"[canary] HTTP {r.status_code} —— 服務狀況,不是契約:{body}",
              file=sys.stderr)
        # **認證失敗也是「跑不起來」**(外審):金鑰過期/被撤銷/權限不足
        # 時,請求根本沒有執行過 —— 宣稱「契約變了」會讓人去改 adapter,
        # 而該做的是換金鑰。
        if r.status_code in (401, 403, 408, 425, 429, 500, 502, 503, 504):
            return _unavailable(args.state, args.now, f"HTTP {r.status_code}")
        return 1
    try:
        resp = r.json()
    except Exception:                                   # noqa: BLE001
        print("[canary] 回應不是 JSON —— 契約變了", file=sys.stderr)
        return 1

    problems = ds.contract_problems(resp)
    if problems:
        print("[canary] **契約變了**,要改的是 adapter,不是把測試改成通過:",
              file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        # 形狀摘要(不印內容 —— 那是模型輸出,與判準無關)
        print("[canary] 實際形狀:"
              + json.dumps({"status": resp.get("status"),
                            "output_types": [i.get("type") for i in
                                             (resp.get("output") or [])
                                             if isinstance(i, dict)],
                            "usage_keys": sorted(resp.get("usage") or {})},
                           ensure_ascii=False), file=sys.stderr)
        return 1
    got = ds.extract_output(resp)
    if args.state and not _record(args.state, True, args.now).get("_persisted"):
        print("[canary] 契約沒問題,但狀態存不下來 —— 下一班會以為"
              "從來沒驗證過", file=sys.stderr)
        return 3
    print(f"[canary] 形狀符合;取到答案 {len(got.get('text') or '')} 字元、"
          f"applied_effort={ds.applied_effort(resp)!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
