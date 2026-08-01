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

**只用標準函式庫**(第十輪 P0-1)。原本這個 job 先 `pip install requests`
(未鎖版、無 hash),下一步才把三把 API 金鑰放進環境 —— 而主 CI 與晨報早就
只用 `requirements.lock`,金絲雀等於自己另開了一條供應鏈通道。惡意套件不必
在安裝當下讀到金鑰,它可以留下 `sitecustomize.py` / `.pth` 之類的啟動掛勾,
等下一個帶金鑰的 process 執行。改用 `urllib.request` 之後**沒有安裝步驟**,
比鎖版更徹底。

而且**只有它真的會呼叫的 provider 才需要金鑰**:其餘 provider 這裡只需要
知道「有沒有設」,所以 workflow 傳的是布林旗標而不是金鑰本身。
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import llm_telemetry as lt          # noqa: E402
from news_events import llm_event_json_schema  # noqa: E402

TIMEOUT = float(os.environ.get("CANARY_TIMEOUT_SEC", "300"))

#: 官方文件列出的推理強度。金絲雀逐一實測,因為**文件是宣稱、端點才是事實**。
_EFFORTS = ("none", "low", "medium", "high", "xhigh", "max")

# 輸出全是中文。Linux CI 的 stdout 是 UTF-8,但本機(Windows 主控台預設 GBK)
# 會在 print 的當下丟 UnicodeEncodeError —— 也就是說**所有檢查都跑完了,
# 卻死在報告那一行**,而且回傳非零讓人以為是設定有問題。診斷工具自己壞掉
# 比沒有診斷工具更糟,所以這裡不依賴環境編碼。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _has_key(env_name: str) -> bool:
    """金鑰**存不存在**。真正會被呼叫的 provider 才需要金鑰值本身。

    第十輪 P0-1:原本 workflow 把 OPENAI / DEEPSEEK / GEMINI 三把金鑰全部
    放進同一個 job。金絲雀只對 OpenAI 發真請求,其餘只是檢查「有沒有設」——
    那用布林旗標就夠了,不必讓金鑰進到這個 process 的環境。
    """
    if _env(env_name):
        return True
    return _env(env_name.replace("_API_KEY", "_KEY_PRESENT")).lower() == "true"


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


class _Resp:
    """最小的回應物件:只提供 `lt.response_blames_param` 與本檔用到的介面。"""

    def __init__(self, status_code: int, body: bytes):
        self.status_code = status_code
        self.text = body.decode("utf-8", "replace")

    def json(self):
        return json.loads(self.text)


def _http(url: str, *, method: str = "GET", payload=None, timeout: float = 30):
    """標準函式庫的 HTTP。非 2xx 不拋例外 —— 錯誤內文正是我們要看的東西。"""
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers=_openai_headers())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return _Resp(r.status, r.read())
    except urllib.error.HTTPError as e:
        return _Resp(e.code, e.read())


def _openai_headers() -> dict:
    return {"Authorization": f"Bearer {_env('OPENAI_API_KEY')}",
            "Content-Type": "application/json"}


def _base() -> str:
    return _env("OPENAI_BASE_URL", "https://api.openai.com").rstrip("/")


def check_model_exists(model: str) -> Check:
    """模型 ID 打錯時的症狀是 400,而 400 有太多其他原因 —— 先單獨確認一次。"""
    try:
        r = _http(f"{_base()}/v1/models/{model}", timeout=30)
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
        "max_completion_tokens": lt.output_cap(effort, 2000, model=model),
        "stream": False,
    }
    if effort:
        payload["reasoning_effort"] = effort
    if schema:
        payload["response_format"] = {"type": "json_schema", "json_schema": schema}
    name = label or f"{model} / reasoning={effort or '(預設)'}"
    t0 = time.monotonic()
    try:
        r = _http(f"{_base()}/v1/chat/completions", method="POST",
                  payload=payload, timeout=TIMEOUT)
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


def effort_matrix(model: str) -> list:
    """逐一實測每個推理強度**在這個 model + 這個 endpoint 上**是否被接受(批#104)。

    2026-08-01:官方文件列出 luna 支援 none…max,而生產實際送出 `max` 時
    API 回 400 且訊息指名 `reasoning_effort` —— 文件與端點行為不一致
    (很可能是 chat/completions 與 Responses API 的差異)。

    **文件是宣稱,這裡是量測。** 每個值送一次 20-token 的小請求,
    比對「文件說支援」與「這個端點真的收」,不一致就明白列出來。
    """
    out = []
    for effort in _EFFORTS:
        payload = {"model": model, "max_completion_tokens": 64,
                   "messages": [{"role": "user", "content": "ok"}],
                   "reasoning_effort": effort, "stream": False}
        try:
            r = _http(f"{_base()}/v1/chat/completions", method="POST",
                      payload=payload, timeout=90)
        except Exception as e:                  # noqa: BLE001
            out.append(Check(f"reasoning={effort}", False,
                             f"{type(e).__name__}", fatal=False))
            continue
        if r.status_code == 200:
            usage = (r.json() or {}).get("usage") or {}
            out.append(Check(f"reasoning={effort}", True,
                             f"接受、reasoning={lt.reasoning_tokens_of(usage)}",
                             fatal=False))
        else:
            blames = lt.response_blames_param(r, "reasoning_effort")
            out.append(Check(
                f"reasoning={effort}", False,
                f"HTTP {r.status_code}"
                + ("(指名 reasoning_effort)" if blames else "")
                + f": {r.text[:160]}", fatal=False))
    return out


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
        has_key=_has_key,
        efforts={"primary": effort if provider == "openai" else "",
                 "extractor": ext_effort if extractor == "openai" else ""})
    if issues:
        # r1(Codex #8,P2):**致命的設定問題必須讓 job 變紅。**
        # 原本一律 `fatal=False`,於是「選了 deepseek 卻沒有 DEEPSEEK_API_KEY」
        # 這種必定失敗的設定,金絲雀仍然 exit 0、workflow 綠燈 ——
        # 它就當不成設定閘門,而那是它存在的唯一理由。
        # 判準是「這樣上線一定不會動」,不是「風險比較高」:
        # 「超過實測過的上限」屬於後者,維持警告。
        checks = [Check("設定本身合法",
                        not any(lt.is_fatal(i) for i in issues),
                        ";".join(issues),
                        fatal=any(lt.is_fatal(i) for i in issues))]

    if "openai" in (provider, extractor):
        if not _env("OPENAI_API_KEY"):
            checks.append(Check("OPENAI_API_KEY 可用", False, "沒有設"))
        else:
            checks.append(check_model_exists(model))
            if provider == "openai":
                chk, took, usage = probe(model, effort, label=f"主分析 {model}")
                checks.append(chk)
                checks.append(_budget_verdict(took, provider, effort))
            if provider == "openai" and _env("CANARY_EFFORT_MATRIX", "1") == "1":
                checks.extend(effort_matrix(model))
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




# ── 各 provider 的真實探測(第十輪 P1-3)────────────────────────────────
# 原本只有 OpenAI 會發真請求,其餘只檢查「有沒有 key」—— 而 workflow 叫做
# `Validate LLM Config`,很容易讓人以為每個 provider 都被實測過。
#
# 這些探測**每個只用自己那把金鑰**,由 workflow 的 matrix 分成獨立 job
# (第十輪 P0-1:不把四把金鑰放進同一個 process)。

def _probe_openai_compatible(base: str, key: str, model: str,
                             label: str) -> Check:
    """OpenAI 相容的 chat/completions(OpenAI 與 DeepSeek 共用)。"""
    payload = {"model": model, "max_tokens": 16, "stream": False,
               "messages": [{"role": "user", "content": "ok"}]}
    return _probe_json(f"{base.rstrip('/')}/v1/chat/completions", payload,
                       {"Authorization": f"Bearer {key}",
                        "Content-Type": "application/json"}, label)


def _probe_gemini(key: str, model: str) -> Check:
    url = ("https://generativelanguage.googleapis.com/v1beta/models/"
           f"{model}:generateContent?key={key}")
    payload = {"contents": [{"parts": [{"text": "ok"}]}],
               "generationConfig": {"maxOutputTokens": 16}}
    return _probe_json(url, payload, {"Content-Type": "application/json"},
                       f"gemini {model}")


def _probe_anthropic(key: str, model: str) -> Check:
    payload = {"model": model, "max_tokens": 16,
               "messages": [{"role": "user", "content": "ok"}]}
    return _probe_json("https://api.anthropic.com/v1/messages", payload,
                       {"x-api-key": key, "anthropic-version": "2023-06-01",
                        "Content-Type": "application/json"},
                       f"anthropic {model}")


def _probe_json(url: str, payload: dict, headers: dict, label: str) -> Check:
    """送一次最小請求。**只回狀態與錯誤內文,不回內容。**"""
    import urllib.request as _u
    data = json.dumps(payload).encode("utf-8")
    req = _u.Request(url, data=data, method="POST", headers=headers)
    t0 = time.monotonic()
    try:
        with _u.urlopen(req, timeout=60) as r:
            body = r.read()
            status = r.status
    except urllib.error.HTTPError as e:
        status, body = e.code, e.read()
    except Exception as e:                      # noqa: BLE001
        return Check(label, False, f"{type(e).__name__}"
                     f"(在 {time.monotonic() - t0:.0f}s 後)")
    took = time.monotonic() - t0
    if status == 200:
        return Check(label, True, f"{took:.0f}s、回應可解析")
    return Check(label, False,
                 f"HTTP {status}: {body.decode('utf-8', 'replace')[:200]}")


def probe_one_provider(provider: str) -> int:
    """matrix 模式:只探測**這一個** provider,而且只用它自己的金鑰。"""
    key = _env(_PROVIDER_KEY.get(provider, ""))
    # **只有被選用的 provider 缺金鑰才算失敗。** matrix 對四個 provider 都跑,
    # 而使用者通常只用其中一兩個 —— 讓沒用到的那些變紅,金絲雀就會恆紅,
    # 而恆紅的閘門等於沒有閘門(與降級清單的常駐雜訊是同一個病)。
    selected = {_env("LLM_PROVIDER", "deepseek"),
                _env("EXTRACTOR_PROVIDER") or _env("LLM_PROVIDER", "deepseek"),
                _env("LLM_SHADOW_PROVIDER")}
    if not key:
        used = provider in selected
        _report([Check(f"{provider} 金鑰", False,
                       (f"這個 job 沒有拿到 {_PROVIDER_KEY.get(provider)}"
                        if used else "本次設定沒有用到這個 provider,略過"),
                       fatal=used)], provider, "-", "-", "-")
        return 1 if used else 0
    if provider == "openai":
        model = _env("OPENAI_MODEL", "gpt-5.6-terra")
        checks = [check_model_exists(model),
                  _probe_openai_compatible(_base(), key, model, f"openai {model}")]
        checks.extend(effort_matrix(model))
    elif provider == "deepseek":
        model = _env("DEEPSEEK_MODEL", "deepseek-v4-pro")
        checks = [_probe_openai_compatible(
            _env("DEEPSEEK_BASE_URL", "https://api.deepseek.com"), key, model,
            f"deepseek {model}")]
    elif provider == "gemini":
        checks = [_probe_gemini(key, _env("GEMINI_MODEL", "gemini-2.5-flash"))]
    elif provider == "anthropic":
        checks = [_probe_anthropic(key, _env("CLAUDE_MODEL", "claude-sonnet-4-6"))]
    else:
        checks = [Check(f"provider {provider}", False, "不是合法值")]
    _report(checks, provider, "-", "-", "-")
    return 1 if any(c.fatal and not c.ok for c in checks) else 0


_PROVIDER_KEY = {"openai": "OPENAI_API_KEY", "deepseek": "DEEPSEEK_API_KEY",
                 "gemini": "GEMINI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}


if __name__ == "__main__":
    # matrix 模式: 有值就只探測那一個 provider
    # (第十輪 P1-3 要真實探測,P0-1 要金鑰隔離 —— matrix 同時滿足兩者)。
    _only = _env("CANARY_PROVIDER")
    raise SystemExit(probe_one_provider(_only) if _only else main())
