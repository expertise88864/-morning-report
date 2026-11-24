"""事件抽取器的備援換手(2026-11-24)。

11-24 實況:DeepSeek 逾時 → 換 OpenAI → OpenAI 額度用完(HTTP 429)→ 三批只剩一批有事件,
而 Gemini 的金鑰從頭到尾沒被用到。帳號不可用(401/402/429)時再往下一家,同一班不再撞它。
其他 4xx 是請求本身有問題,換一家會用同樣的錯誤再錯一次,不換。
"""
from __future__ import annotations

import os
import sys

import llm_config as _lc


def account_blocked(exc) -> bool:
    """HTTP 錯誤是「這個帳號現在不能用」(金鑰無效、欠費、額度或流量用完),不是請求寫錯。"""
    return getattr(getattr(exc, "response", None), "status_code", None) in (401, 402, 429)


class Failover:
    def __init__(self) -> None:
        self.dead: set[str] = set()

    def next(self, primary: str) -> str:
        return _lc.fallback_extractor_provider(
            primary, lambda k: bool(os.environ.get(k, "").strip()), self.dead)

    def run(self, dispatch, primary: str, alt: str, prompt: str, stat: dict) -> str:
        while True:
            try:
                return dispatch(alt, prompt)
            except Exception as exc:
                if not account_blocked(exc):
                    raise
                self.dead.add(alt)
                following = self.next(primary)
                if not following:
                    raise
                print(f"[llm-extractor] 備援 {alt} 帳號不可用,改用 {following}", file=sys.stderr)
                stat["fallback_to"] = alt = following
