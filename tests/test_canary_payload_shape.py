# -*- coding: utf-8 -*-
"""**金絲雀送的形狀要與生產一致,而且不准恆紅**(2026-08-02 實測)。

第一次真的把 Luna 設定跑過金絲雀,兩條紅燈都不是生產的問題:

  1. `probe (openai)` HTTP 400 —— `_probe_openai_compatible` 與 DeepSeek
     共用且寫死 `max_tokens: 16`。那對 DeepSeek 對、對新的 OpenAI 模型錯
     (只收 `max_completion_tokens`)。生產從來不送那個欄位,所以生產沒事;
     但那個函式的 docstring 寫著「送出**正式排程真的會送的那份 payload**」
     —— 對 OpenAI 而言那句是假的。
  2. `probe (anthropic)` exit 1 —— Anthropic 根本沒被選用。
     「沒被選用的 provider 不算失敗」原本只套在缺金鑰那條分支。

合起來的後果比單獨看嚴重:**金絲雀從此每次都紅。** 而永遠紅的閘門會訓練人
忽略它 —— 真的壞掉那天沒有人會相信它。這個檔已經為同一個道理修過兩次
(見 `Check.body` 的註解),所以判準訂在形狀上:

  * 額度欄位由呼叫端**明講**,而且要與生產那條路徑送的一致;
  * 沒被選用的 provider 不得讓 job 變紅。
"""
import ast
import importlib.util
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_CANARY = _ROOT / "tools" / "validate_llm_config.py"


def _canary():
    spec = importlib.util.spec_from_file_location("_canary", _CANARY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fn(tree, name):
    return next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == name)


def _token_field_of(call) -> str:
    for kw in call.keywords:
        if kw.arg == "token_field" and isinstance(kw.value, ast.Constant):
            return kw.value.value
    return ""


def _probe_calls(tree) -> list:
    return [c for c in ast.walk(tree)
            if isinstance(c, ast.Call)
            and getattr(c.func, "id", "") == "_probe_openai_compatible"]


def test_the_token_field_must_be_stated_by_the_caller():
    """**額度欄位不得有預設值。**

    這一條盯的是缺陷的形狀:一個共用的預設值會讓「兩家欄位名不同」這件事
    在新增 provider 時被靜靜跳過,而症狀是那一家每次都 400。
    """
    tree = ast.parse(_CANARY.read_text(encoding="utf-8"))
    fn = _fn(tree, "_probe_openai_compatible")
    names = [a.arg for a in fn.args.kwonlyargs]
    assert "token_field" in names, "額度欄位不是由呼叫端指定的"
    default = fn.args.kw_defaults[names.index("token_field")]
    assert default is None, (
        "token_field 有預設值 —— 共用一個預設就是這個缺陷的成因")


def test_every_probe_states_a_field_and_they_are_not_all_the_same():
    """每個呼叫端都要講,而且**不會全都一樣**(全一樣就代表又共用了)。"""
    tree = ast.parse(_CANARY.read_text(encoding="utf-8"))
    calls = _probe_calls(tree)
    assert len(calls) >= 2, f"探測呼叫端只找到 {len(calls)} 個,掃描器可能壞了"
    fields = [_token_field_of(c) for c in calls]
    assert all(fields), f"有呼叫端沒有指定額度欄位:{fields}"
    assert set(fields) == {"max_tokens", "max_completion_tokens"}, (
        f"兩家的額度欄位變成一樣了({set(fields)}) —— "
        "那正是 OpenAI 那條每次都 400 的原因")


def test_the_openai_probe_sends_what_production_sends():
    """**金絲雀與生產送同一個欄位名。**

    分岔的症狀不是「金絲雀紅」而是「金絲雀驗的不是那條路」——
    兩者都會讓人對它失去信任。
    """
    src = (_ROOT / "morning_report.py").read_text(encoding="utf-8")
    prod = _fn(ast.parse(src), "_call_openai")
    prod_fields = {k.value for n in ast.walk(prod)
                   if isinstance(n, ast.Dict)
                   for k in n.keys
                   if isinstance(k, ast.Constant)
                   and str(k.value).startswith("max_")}
    assert "max_completion_tokens" in prod_fields, \
        f"生產的 _call_openai 不再送 max_completion_tokens:{prod_fields}"

    tree = ast.parse(_CANARY.read_text(encoding="utf-8"))
    openai_calls = [c for c in _probe_calls(tree)
                    if _token_field_of(c) == "max_completion_tokens"]
    assert openai_calls, "金絲雀沒有任何呼叫端用生產的那個欄位名"


def test_an_unused_provider_cannot_turn_the_canary_red(monkeypatch, capsys):
    """**沒被選用的 provider 失敗不得讓 job 變紅。**

    Anthropic 在這組設定裡根本不會被呼叫;讓它把整個金絲雀染紅,
    等於每天送一個假警報,而假警報會把真警報一起淹掉。
    """
    mod = _canary()
    for k, v in {"LLM_PROVIDER": "openai", "EXTRACTOR_PROVIDER": "openai",
                 "LLM_SHADOW_PROVIDER": "deepseek",
                 "CANARY_PROVIDER": "anthropic",
                 "ANTHROPIC_API_KEY": "sk-present"}.items():
        monkeypatch.setenv(k, v)
    # 探測直接判失敗 —— 我們要驗的是「失敗之後 job 紅不紅」
    monkeypatch.setattr(mod, "_probe_anthropic",
                        lambda *a, **k: mod.Check("anthropic x", False,
                                                  "HTTP 404"))
    assert mod.probe_one_provider(__import__("os").environ["CANARY_PROVIDER"]) == 0, (
        "沒被選用的 provider 失敗仍然讓金絲雀變紅 —— "
        "恆紅的閘門會訓練人忽略它")
    assert "anthropic" in capsys.readouterr().out.lower(), \
        "失敗被藏起來了 —— 不致命不等於不報告"


def test_a_selected_provider_still_turns_it_red(monkeypatch):
    """反向:**別為了消紅燈而把真的該紅的也放過。**"""
    mod = _canary()
    for k, v in {"LLM_PROVIDER": "openai", "EXTRACTOR_PROVIDER": "",
                 "LLM_SHADOW_PROVIDER": "", "CANARY_PROVIDER": "openai",
                 "OPENAI_API_KEY": "sk-present", "OPENAI_MODEL": "gpt-5.6-luna",
                 "OPENAI_API_MODE": "chat_completions"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(mod, "check_model_exists",
                        lambda m: mod.Check("模型", True, ""))
    monkeypatch.setattr(mod, "effort_matrix", lambda m: [])
    monkeypatch.setattr(mod, "_probe_openai_compatible",
                        lambda *a, **k: mod.Check("openai x", False, "HTTP 400"))
    assert mod.probe_one_provider(__import__("os").environ["CANARY_PROVIDER"]) == 1, \
        "正在使用的 provider 失敗卻沒有讓金絲雀變紅"


def test_mixed_case_provider_still_turns_it_red(monkeypatch):
    """**大小寫不得讓正在用的 provider 變成「沒被選用」**(r1 Codex)。

    生產把 `LLM_PROVIDER` 正規化成 `.strip().lower()`,金絲雀原本只 strip。
    於是 `LLM_PROVIDER=OpenAI` 時:生產跑 openai,金絲雀卻判 openai 沒被
    選用 → 把它的**真實**探測失敗全部降成非致命 → job 收綠燈。

    **正在使用的 provider 壞掉而金絲雀說沒事**,是最糟的一種假綠燈,
    而且是「未選用不致命」那個修正自己造出來的路徑 ——
    修掉假警報的動作,順手造了一個假平安。
    """
    mod = _canary()
    for k, v in {"LLM_PROVIDER": "  OpenAI  ", "EXTRACTOR_PROVIDER": "",
                 "LLM_SHADOW_PROVIDER": "", "CANARY_PROVIDER": "openai",
                 "OPENAI_API_KEY": "sk-present", "OPENAI_MODEL": "gpt-5.6-luna",
                 "OPENAI_API_MODE": "chat_completions"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(mod, "check_model_exists",
                        lambda m: mod.Check("模型", True, ""))
    monkeypatch.setattr(mod, "effort_matrix", lambda m: [])
    monkeypatch.setattr(mod, "_probe_openai_compatible",
                        lambda *a, **k: mod.Check("openai x", False, "HTTP 400"))
    assert mod.probe_one_provider("openai") == 1, (
        "LLM_PROVIDER=OpenAI 讓金絲雀以為 openai 沒被選用 —— "
        "生產正在用的 provider 壞掉卻收綠燈")


def test_provider_names_are_read_through_one_normalizer():
    """**六個讀取點不得各自為政。**

    漏掉任何一個,症狀都是同一種假綠燈,而漏掉不會有錯誤訊息 ——
    所以判準訂在「有沒有人繞過正規化器」,不是「這一次改對了沒有」。
    """
    tree = ast.parse(_CANARY.read_text(encoding="utf-8"))
    bad = []
    for call in ast.walk(tree):
        if not (isinstance(call, ast.Call)
                and getattr(call.func, "id", "") == "_env"):
            continue
        if call.args and isinstance(call.args[0], ast.Constant)                 and str(call.args[0].value).endswith("PROVIDER"):
            bad.append(call.args[0].value)
    assert not bad, (
        f"這些 provider 名稱繞過 _provider_env 直接用 _env 讀:{bad} —— "
        "少了 .lower() 就會與生產對「誰正在被使用」的認知分岔")

    mod = _canary()
    assert mod._provider_env.__doc__, "正規化器要說明它為什麼存在"
