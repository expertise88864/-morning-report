"""事件抽取器的備援換手(2026-11-24)。

11-24 實況:DeepSeek 逾時 → 換 OpenAI → OpenAI 額度用完(HTTP 429)→ 三批只剩一批有事件,
而 Gemini 的金鑰從頭到尾沒被用到。帳號不可用(401/402/429)時再往下一家,同一班不再撞它。
其他 4xx 是請求本身有問題,換一家會用同樣的錯誤再錯一次,不換。
"""
from __future__ import annotations

import os
import sys
import time

import llm_config as _lc


class AccountBlocked(RuntimeError):
    """adapter 已把 HTTP 錯誤轉成文字時,狀態碼靠這一型帶出來。"""

    def __init__(self, message: str, status) -> None:
        super().__init__(message)
        self.status_code = status


def account_blocked(exc) -> bool:
    """「這個帳號現在不能用」(金鑰無效、欠費、額度或流量用完),不是請求寫錯。requests 與 Anthropic SDK
    的例外都帶 `.response.status_code`。"""
    status = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    return status in (401, 402, 429)


class Failover:
    def __init__(self, note=None, count=lambda: 0) -> None:
        """`note(provider, exc, t0, 先前筆數, 下一家)` 記失敗;`count()` 是目前 attempts 筆數。"""
        self.dead: set[str] = set()
        self._note, self._count = note, count

    def next(self, primary: str) -> str:
        return _lc.fallback_extractor_provider(
            primary, lambda k: bool(os.environ.get(k, "").strip()), self.dead)

    def run(self, dispatch, primary: str, alt: str, prompt: str, stat: dict) -> str:
        while True:
            t0, before = time.monotonic(), self._count()
            try:
                return dispatch(alt, prompt)
            except Exception as exc:
                if not account_blocked(exc):
                    raise
                self.dead.add(alt)
                following = self.next(primary)
                if self._note:
                    self._note(alt, exc, t0, before, following)
                if not following:
                    raise
                print(f"[llm-extractor] 備援 {alt} 帳號不可用,改用 {following}", file=sys.stderr)
                stat["fallback_to"] = alt = following
