# -*- coding: utf-8 -*-
"""**實驗接線的契約**(Phase 7/8)。

這個檔驗的不是「模組各自對不對」(那由各自的測試負責),而是三件只有在
接起來之後才成立或不成立的事:

  1. **回切 DeepSeek 不需要改程式碼** —— 使用者的明確要求。
     若回切要 revert commit,實驗一出問題就得在凌晨改程式,那不可接受。
  2. **金絲雀送的是生產的形狀** —— 16-token 的「回 ok」只證明金鑰存在。
  3. **新變數全部有來源遙測** —— 加了開關卻沒進 spec,manifest 就答不出
     那個值是誰決定的,而那正是第十一輪 P2-1 花了一整批修的事。
"""
import json

import pytest

yaml = pytest.importorskip("yaml")

import llm_config as lc          # noqa: E402
import morning_report as mr      # noqa: E402
import prompt_profiles as pp     # noqa: E402


EXPERIMENT_VARS = (
    "LLM_PRIMARY_PROMPT_PROFILE", "LLM_SHADOW_PROMPT_PROFILE",
    "LLM_COMPARISON_MODE", "LLM_EXPERIMENT_ID", "LLM_EXPERIMENT_TARGET_PAIRS",
    "OPENAI_API_MODE", "OPENAI_STORE", "OPENAI_TEXT_VERBOSITY",
    "OPENAI_REASONING_SUMMARY", "OPENAI_REASONING_CONTEXT",
    "OPENAI_PROMPT_CACHE_TTL_SECONDS",
)


def test_every_experiment_switch_has_source_telemetry():
    """加了開關卻沒進 spec,manifest 就答不出那個值是誰決定的。

    第十一輪 P2-1 花了一整批修這件事;新增變數時漏掉它,等於把那一批白做。
    """
    missing = [v for v in EXPERIMENT_VARS if v not in lc.CONFIG_SOURCE_SPEC]
    assert not missing, f"這些實驗開關沒有來源遙測:{missing}"
    resolved = mr._llm_config_resolved()
    absent = [v for v in EXPERIMENT_VARS if v not in resolved]
    assert not absent, f"這些開關沒有回報實際採用值:{absent}"


def test_rolling_back_to_deepseek_needs_no_code_change():
    """**使用者的明確要求。**

    回切只能是「改變數」。若它需要 revert commit,實驗一出問題就得在凌晨
    改程式碼上線 —— 那是這個專案最不該有的操作。
    """
    # 主分析 provider 是 deepseek 時,問法自動回到 legacy
    assert mr._prompt_profile_for("deepseek") == "deepseek_legacy_v1"
    assert mr._prompt_profile_for("gemini") == "deepseek_legacy_v1"
    # 明設 profile 仍可覆寫(實驗期間 openai → luna)
    assert mr._prompt_profile_for("openai") == "luna56_xhigh_v1"
    assert mr._prompt_profile_for("deepseek", "luna56_xhigh_v1") == "luna56_xhigh_v1"

    # 這四個都是 repo variable,而且預設值就是「現況」
    for var, expect in (("LLM_PROVIDER", "deepseek"),
                        ("LLM_PRIMARY_PROMPT_PROFILE", ""),
                        ("LLM_EXPERIMENT_ID", ""),
                        ("OPENAI_API_MODE", "chat_completions")):
        kind, default = lc.CONFIG_SOURCE_SPEC[var]
        assert kind == "variable", f"{var} 不是 repo variable,回切要改程式"
        assert default == expect, f"{var} 的預設不是現況:{default!r}"


def test_an_unknown_profile_override_fails_instead_of_falling_back():
    """靜默落回預設的症狀是「帳本記著一個沒發生過的設定」。"""
    with pytest.raises(KeyError):
        mr._prompt_profile_for("openai", "does_not_exist_v9")


def test_the_responses_mode_is_not_the_default():
    """新的 adapter 尚未在生產跑過,不得成為預設。

    讓未驗證的路徑成為預設,等於讓任何人一設 `LLM_PROVIDER=openai`
    就踩到它 —— 而那個人可能只是想換個模型試試。
    """
    _kind, default = lc.CONFIG_SOURCE_SPEC["OPENAI_API_MODE"]
    assert default == "chat_completions"
    assert mr.OPENAI_API_MODE in ("chat_completions", "responses")


def test_the_canary_sends_the_production_shape_not_a_toy_request():
    """金絲雀要送 strict schema + 生產的 developer 前綴 + responses 端點。

    「回 ok」的 16-token 請求只證明金鑰與模型存在 —— 這條路徑的三個新風險
    (端點收不收 xhigh、strict schema 會不會被拒、強度會不會靜默退讓)
    全都只在真實形狀下才出現。
    """
    import openai_responses as orx
    import tools.validate_llm_config as canary

    schema = canary._CANARY_SCHEMA
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    assert "央行" in canary._CANARY_EVIDENCE
    assert "source_item_id" in canary._CANARY_EVIDENCE, \
        "金絲雀的證據沒有 ID —— 那就驗不到「模型會不會回指證據」"

    payload = orx.build_payload(
        model="gpt-5.6-luna", instructions=pp.LUNA_DEVELOPER_INSTRUCTIONS,
        user_input=canary._CANARY_EVIDENCE, effort="xhigh",
        response_format={"type": "json_schema", "name": "canary",
                         "schema": schema, "strict": True},
        max_output_tokens=2000)
    assert payload["reasoning"]["effort"] == "xhigh"
    assert payload["text"]["format"]["strict"] is True
    assert payload["instructions"] == pp.LUNA_DEVELOPER_INSTRUCTIONS, \
        "金絲雀用的不是生產的 developer 前綴 —— 那就驗不到前綴會不會被拒"
    assert len(json.dumps(payload, ensure_ascii=False)) > 1500, \
        "金絲雀的請求太小,不具代表性"


def test_the_canary_only_pays_for_the_responses_probe_when_it_is_needed():
    """responses 探測會花掉一次高推理的錢 —— 模式維持現況時不該送。"""
    import inspect

    import tools.validate_llm_config as canary

    src = inspect.getsource(canary.probe_one_provider)
    assert 'OPENAI_API_MODE' in src and 'responses' in src, \
        "金絲雀沒有依 OPENAI_API_MODE 決定要不要送 responses 探測"
    assert "probe_responses_strict" in src


def test_the_experiment_variables_reach_the_workflow():
    """程式讀得到、workflow 沒傳 = 設了也靜默無效(P2-1 抓過一次同型)。"""
    from pathlib import Path

    wf = yaml.safe_load((Path(__file__).resolve().parents[1] / ".github"
                         / "workflows" / "morning-report.yml").read_text(
        encoding="utf-8"))
    step = next(s for s in wf["jobs"]["send-report"]["steps"]
                if "morning_report.py" in str(s.get("run") or ""))
    env = step.get("env") or {}
    for var in EXPERIMENT_VARS:
        assert var in env, f"workflow 沒有傳 {var}"
        assert f"vars.{var}" in str(env[var]), f"{var} 沒有接到自己的 variable"
        assert var in str(env.get("LLM_CONFIG_RAW") or ""), \
            f"{var} 的原始值沒有進 LLM_CONFIG_RAW,manifest 答不出來源"


def test_a_bad_integer_variable_does_not_kill_the_run():
    """設定打錯字不得讓晨報整份失敗 —— 但也不得靜默。"""
    import os

    saved = dict(os.environ)
    try:
        os.environ["LLM_EXPERIMENT_TARGET_PAIRS"] = "十"
        assert mr._int_env("LLM_EXPERIMENT_TARGET_PAIRS", 10) == 10
        os.environ["LLM_EXPERIMENT_TARGET_PAIRS"] = "7"
        assert mr._int_env("LLM_EXPERIMENT_TARGET_PAIRS", 10) == 7
        os.environ.pop("LLM_EXPERIMENT_TARGET_PAIRS")
        assert mr._int_env("LLM_EXPERIMENT_TARGET_PAIRS", 10) == 10
    finally:
        os.environ.clear()
        os.environ.update(saved)
