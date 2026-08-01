# -*- coding: utf-8 -*-
"""**LLM 設定金絲雀**:在正式排程之前就把設定問題打出來(第九輪 P1-5)。

## 為什麼需要它
2026-08-01 一天之內兩次:設定有問題,而**發現的方式是早上六點的信壞掉**。

  1. `LLM_PROVIDER` 設在 Secrets 而不是 Variables → 靜默落回 deepseek
  2. workflow 寫死 `LLM_REQUEST_TIMEOUT_SECONDS: "75"` → GPT-5.6 跑不完
     85,814-token 的 prompt → ReadTimeout → 備援也失敗 → 降級版報告

兩次都不是「程式有 bug」,而是「設定與現實對不上,而唯一的回饋管道是生產」。
金絲雀把回饋管道往前搬:手動觸發、不寄信、不寫 state,只回答四個問題 ——
模型存在嗎、推理強度收得下嗎、結構化輸出支援嗎、**跑一次要多久**。

最後一項是今天真正缺的那個數字。逾時只告訴你「超過 75 秒」,
不告訴你 240 秒夠不夠。

## 安全
不寄信、不寫 state、不碰 repo。金鑰只用於 Authorization header,
任何輸出都經 `_safe()` 過濾;失敗時只印 provider 回的錯誤結構,不印請求內容。
"""
from __future__ import annotations

import os
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import llm_telemetry as lt          # noqa: E402
from news_events import llm_event_json_schema  # noqa: E402

TIMEOUT = float(os.environ.get("CANARY_TIMEOUT_SEC", "300"))

# 輸出全是中文。Linux CI 的 stdout 是 UTF-8,但本機(Windows 主控台預設 GBK)
# 會在 print 的當下丟 UnicodeEncodeError —— 也就是說**所有檢查都跑完了,
# 卻死在報告那一行**,而且回傳非零讓人以為是設定有問題。診斷工具自己壞掉
# 比沒有診斷工具更糟,所以這裡不依賴環境編碼。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _safe(text: str) -> str:
    """把任何看起來像金鑰的東西遮掉。**輸出會進 job log,而 log 可能被分享。**"""
    out = str(text)
    for key in ("OPENAI_API_KEY", "DEEPSEEK_API_KEY", "GEMINI_API_KEY",
                "ANTHROPIC_API_KEY"):
        val = _env(key)
        if len(val) > 8:
            out = out.replace(val, f"<{key}>")
    return out[:600]


class Check:
    """一項檢查的結果。`fatal` 代表「這樣上線一定壞」,只有它會讓 job 變紅。"""

    def __init__(self, name, ok, detail="", fatal=True):
        self.name, self.ok, self.detail, self.fatal = name, ok, detail, fatal

    def row(self) -> str:
        mark = "✅" if self.ok else ("❌" if self.fatal else "⚠")
        return f"| {mark} | {self.name} | {_safe(self.detail)} |"


def _openai_headers() -> dict:
    return {"Authorization": f"Bearer {_env('OPENAI_API_KEY')}",
            "Content-Type": "application/json"}


def _base() -> str:
    return _env("OPENAI_BASE_URL", "https://api.openai.com").rstrip("/")


def check_model_exists(model: str) -> Check:
    """模型 ID 打錯時的症狀是 400,而 400 有太多其他原因 —— 先單獨確認一次。"""
    try:
        r = requests.get(f"{_base()}/v1/models/{model}",
                         headers=_openai_headers(), timeout=30)
    except Exception as e:                      # noqa: BLE001
        return Check(f"模型 {model} 存在", False, f"{type(e).__name__}: {e}")
    if r.status_code == 200:
        return Check(f"模型 {model} 存在", True, "")
    return Check(f"模型 {model} 存在", False,
                 f"HTTP {r.status_code}: {r.text[:200]}")


def probe(model: str, effort: str, *, schema=None, prompt="",
          label="") -> tuple:
    """送一次真的請求,回 (Check, 耗時秒, usage)。"""
    payload = {
        "model": model,
        "messages": [{"role": "user",
                      "content": prompt or "Reply with the single word: ok"}],
        "max_completion_tokens": lt.output_cap(effort, 2000),
        "stream": False,
    }
    if effort:
        payload["reasoning_effort"] = effort
    if schema:
        payload["response_format"] = {"type": "json_schema", "json_schema": schema}
    name = label or f"{model} / reasoning={effort or '(預設)'}"
    t0 = time.monotonic()
    try:
        r = requests.post(f"{_base()}/v1/chat/completions", json=payload,
                          headers=_openai_headers(), timeout=TIMEOUT)
    except Exception as e:                      # noqa: BLE001
        took = time.monotonic() - t0
        return (Check(name, False,
                      f"{type(e).__name__}(在 {took:.0f}s 後)"), took, {})
    took = time.monotonic() - t0
    if r.status_code != 200:
        blames = lt.response_blames_param(r, "reasoning_effort")
        hint = "(錯誤指名 reasoning_effort)" if blames else ""
        return (Check(name, False, f"HTTP {r.status_code}{hint}: {r.text[:200]}"),
                took, {})
    body = r.json() or {}
    usage = body.get("usage") or {}
    finish = ((body.get("choices") or [{}])[0]).get("finish_reason") or ""
    if finish == "length":
        return (Check(name, False,
                      f"finish_reason=length —— 推理吃光額度({took:.0f}s)"),
                took, usage)
    return (Check(name, True, f"{took:.0f}s、"
                  f"reasoning={lt.reasoning_tokens_of(usage)}、"
                  f"completion={usage.get('completion_tokens')}"), took, usage)


def main() -> int:
    provider = _env("LLM_PROVIDER", "deepseek")
    extractor = _env("EXTRACTOR_PROVIDER") or provider
    model = _env("OPENAI_MODEL", "gpt-5.6-terra")
    ext_model = _env("OPENAI_EXTRACTOR_MODEL") or model
    effort = _env("OPENAI_REASONING_EFFORT", "medium")
    ext_effort = _env("OPENAI_EXTRACTOR_REASONING", "low")

    checks = [Check("設定本身合法", True, "")]
    issues = lt.validate_llm_config(
        provider=provider, extractor_provider=extractor,
        shadow_provider=_env("LLM_SHADOW_PROVIDER"),
        has_key=lambda e: bool(_env(e)),
        efforts={"primary": effort if provider == "openai" else "",
                 "extractor": ext_effort if extractor == "openai" else ""})
    if issues:
        checks = [Check("設定本身合法", False, ";".join(issues), fatal=False)]

    if "openai" in (provider, extractor):
        if not _env("OPENAI_API_KEY"):
            checks.append(Check("OPENAI_API_KEY 可用", False, "沒有設"))
        else:
            checks.append(check_model_exists(model))
            if provider == "openai":
                chk, took, usage = probe(model, effort, label=f"主分析 {model}")
                checks.append(chk)
                checks.append(_budget_verdict(took, provider, effort))
            if extractor == "openai":
                if ext_model != model:
                    checks.append(check_model_exists(ext_model))
                chk, _t, _u = probe(ext_model, ext_effort,
                                    schema=llm_event_json_schema(),
                                    label=f"抽取器結構化輸出 {ext_model}")
                checks.append(chk)
    else:
        checks.append(Check("OpenAI 探測", True, "本次設定沒有用到 OpenAI,略過",
                            fatal=False))

    _report(checks, provider, extractor, model, effort)
    return 1 if any(c.fatal and not c.ok for c in checks) else 0


def _budget_verdict(took: float, provider: str, effort: str) -> Check:
    """**這才是今天缺的那個數字。**

    逾時只告訴你「超過 75 秒」,不告訴你 240 秒夠不夠。這裡拿實測耗時去比
    程式算出來的單次請求上限 —— 但要說清楚:探測用的是短 prompt,
    生產的 prompt 是 85,000 token 級,所以這個數字是**下界**。
    """
    total, req = lt.timeout_base(provider)
    limit = min(lt.timeout_for(effort, req), lt.timeout_for(effort, total) * 0.7)
    ok = took < limit * 0.5
    return Check("時間預算夠嗎", ok,
                 f"短 prompt 實測 {took:.0f}s、單次上限 {limit:.0f}s"
                 "(生產 prompt 約 85k token,實際會更久 —— 此為下界)",
                 fatal=False)


def _report(checks, provider, extractor, model, effort) -> None:
    lines = ["## LLM 設定金絲雀", "",
             f"provider=`{provider}`・抽取器=`{extractor}`・"
             f"模型=`{model}`・推理強度=`{effort}`", "",
             "| | 檢查 | 細節 |", "|---|---|---|"]
    lines += [c.row() for c in checks]
    text = "\n".join(lines)
    print(text)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(text + "\n")
        except OSError as e:
            print(f"[canary] step summary 寫入失敗: {e}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
