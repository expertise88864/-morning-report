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

退出碼:0 = 形狀符合;1 = 契約變了;2 = 跑不起來(沒有金鑰、網路不通)。
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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="DeepSeek 線上契約金絲雀")
    ap.add_argument("--model", default=_DEFAULT_MODEL)
    ap.add_argument("--timeout", type=float, default=120.0)
    args = ap.parse_args(argv)

    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        print("[canary] 沒有 DEEPSEEK_API_KEY —— **這不是契約變了**,"
              "是這支程式跑不起來", file=sys.stderr)
        return 2
    try:
        import requests
        r = requests.post(_URL, json=_payload(args.model), timeout=args.timeout,
                          headers={"Authorization": f"Bearer {key}",
                                   "Content-Type": "application/json"})
    except Exception as e:                              # noqa: BLE001
        print(f"[canary] 送不出去({type(e).__name__}:{e})—— 跑不起來,"
              "不是契約變了", file=sys.stderr)
        return 2
    if r.status_code != 200:
        # 429/5xx 是服務狀況,不是契約 —— 分開回報,否則暫時性的忙碌
        # 會被讀成「DeepSeek 改了 API」而讓人去改 adapter。
        body = (r.text or "")[:300]
        print(f"[canary] HTTP {r.status_code} —— 服務狀況,不是契約:{body}",
              file=sys.stderr)
        # **認證失敗也是「跑不起來」**(外審):金鑰過期/被撤銷/權限不足
        # 時,請求根本沒有執行過 —— 宣稱「契約變了」會讓人去改 adapter,
        # 而該做的是換金鑰。
        return 2 if r.status_code in (401, 403, 408, 425, 429,
                                      500, 502, 503, 504) else 1
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
    print(f"[canary] 形狀符合;取到答案 {len(got.get('text') or '')} 字元、"
          f"applied_effort={ds.applied_effort(resp)!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
