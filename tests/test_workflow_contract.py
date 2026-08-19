# -*- coding: utf-8 -*-
"""**workflow 的行為契約**(第九輪 P2-1、P1-10)。

第九輪 P2-1 指出既有的 workflow 測試「只做子字串比對」—— 它驗的是
「這個檔案裡出現過 `vars.`」,而不是「這個變數真的接到對應的 repo variable」。
`OPENAI_MODEL: ${{ vars.LLM_PROVIDER }}` 這種複製貼上錯誤可以完全通過,
而它的症狀正是本 repo 反覆遇到的那種:**沒有錯誤、沒有告警,只是沒生效**。

所以這裡改成:
  1. 用 YAML 解析(不是讀字串),逐鍵確認 `vars.X` 的 X **就是那個鍵本身**;
  2. 告警腳本**真的執行一次**,驗它在兩種情境下寄出的是不同性質的信。

第二點是必要的:那段 Python 內嵌在 YAML 的 heredoc 裡,不會被 pytest 收集、
不會被 ruff 檢查、不會被任何既有測試碰到 —— 它是整個 repo 唯一沒有守衛的
可執行程式碼,而它的工作是「在別的東西壞掉時通知人」。
"""
import os
import re
from pathlib import Path

import pytest

import llm_config

# **不用 `importorskip`**(2026-08-09):這個檔盯的是 workflow 契約 ——
# 排程、權限、逾時。裝不到 yaml 就跳過的話,守衛只在本機作用,
# 而它要保護的是 CI 與生產。`PyYAML` 已經是宣告的依賴(requirements.txt)。
import yaml

WF_PATH = (Path(__file__).resolve().parents[1]
           / ".github" / "workflows" / "morning-report.yml")

#: 這些必須可由 repo variable 覆寫 —— 切換模型要能隨時改、隨時退回。
#:
#: 第十一輪 P2-1:**這裡原本是一條手抄的元組**,漏了 `LLM_SHADOW_REASONING_EFFORT`
#: 與兩個逾時開關 —— 而漏掉的那個 shadow 開關,workflow 根本沒有傳給程式,
#: 使用者設了也靜默無效。手抄清單漏東西時不會紅,只會少檢查。
#: 現在從 `llm_config.CONFIG_SOURCE_SPEC` 推導,單一來源。
LLM_VARS = llm_config.CONFIG_RAW_KEYS


def _workflow() -> dict:
    if not WF_PATH.exists():
        pytest.fail(f"找不到 workflow:{WF_PATH} —— 契約測試不得因檔案不見而跳過")
    return yaml.safe_load(WF_PATH.read_text(encoding="utf-8"))


def _report_step(wf: dict) -> dict:
    for step in wf["jobs"]["send-report"]["steps"]:
        if "morning_report.py" in str(step.get("run") or ""):
            return step
    pytest.fail("send-report 裡沒有執行 morning_report.py 的步驟")


def test_each_llm_switch_reads_its_own_repo_variable():
    """**每個開關要接到「自己」那個 variable。**

    子字串比對只能確認「有 vars.」,接錯到別的鍵照樣通過 ——
    而接錯的症狀是「一切照舊」,沒有任何錯誤訊息。
    """
    env = _report_step(_workflow()).get("env") or {}
    for key in LLM_VARS:
        expr = str(env.get(key, ""))
        assert expr, f"workflow 沒有 {key}"
        assert f"vars.{key}" in expr, (
            f"{key} 沒有接到 vars.{key}(寫死或接錯到別的變數):{expr}")


def test_the_alert_job_can_tell_delivery_failure_from_post_delivery_failure():
    """批#93(第九輪 P1-10):**兩種失敗不是同一件事。**

    2026-07-31:信正常寄達,而寄信**之後**的 state schema 契約失敗讓 job 變紅
    → 告警信說「晨報未寄出,收件人可能沒有收到信」。誤報比沒有告警更糟 ——
    它訓練收件人忽略告警,而下一次是真的沒寄出。

    這條把那段內嵌的 Python **真的跑起來**,而不是比對字串:
    比對字串沒辦法發現分支寫反、或兩個情境其實走到同一句話。
    """
    wf = _workflow()
    alert = wf["jobs"]["alert-on-failure"]["steps"][0]
    script = _heredoc(str(alert["run"]))
    sent = []

    def _fake_smtp(*_a, **_k):
        class _S:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def login(self, *_a):
                pass

            def send_message(self, m):
                sent.append(m)
        return _S()

    def _run(delivered: str) -> str:
        sent.clear()
        env = {"GMAIL_USER": "u@example.com", "GMAIL_APP_PASSWORD": "pw",
               "JOB_RESULT": "failure", "RUN_URL": "https://example/run/1",
               "DELIVERED": delivered}
        import smtplib
        old_env, old_smtp = dict(os.environ), smtplib.SMTP_SSL
        os.environ.update(env)
        smtplib.SMTP_SSL = _fake_smtp
        try:
            exec(compile(script, "<alert>", "exec"), {"__name__": "__alert__"})
        finally:
            smtplib.SMTP_SSL = old_smtp
            os.environ.clear()
            os.environ.update(old_env)
        assert len(sent) == 1, "告警沒有寄出"
        return sent[0]["Subject"] + "\n" + sent[0].get_content()

    not_delivered = _run("")
    delivered = _run("true")

    assert "未寄出" in not_delivered
    assert "未寄出" not in delivered, (
        "信明明寄出了,告警仍說「未寄出」—— 這正是 2026-07-31 的誤報")
    assert "已寄出" in delivered
    assert not_delivered != delivered, "兩種情境走到同一句話,分流等於沒做"
    # 兩封都必須帶執行紀錄連結,否則收信的人無從查起
    for body in (not_delivered, delivered):
        assert "https://example/run/1" in body


def test_the_delivered_flag_comes_from_the_report_itself():
    """**「有沒有寄到」不可從步驟成敗反推。**

    步驟成功不代表信寄出去了(可能在寄信前就被跳過),步驟失敗也不代表沒寄出
    (2026-07-31 就是寄完才失敗)。這個旗標必須由晨報在寄信成功那一刻自己寫。
    """
    wf = _workflow()
    job = wf["jobs"]["send-report"]
    assert (job.get("outputs") or {}).get("delivered"), \
        "send-report 沒有把 delivered 接出去,告警 job 讀不到"
    step_id = _report_step(wf).get("id")
    assert step_id, "跑 morning_report.py 的步驟沒有 id,output 接不出來"
    assert f"steps.{step_id}.outputs.delivered" in job["outputs"]["delivered"], \
        f"delivered 沒有接到 steps.{step_id} 的 output"

    alert_env = wf["jobs"]["alert-on-failure"]["steps"][0].get("env") or {}
    assert "needs.send-report.outputs.delivered" in str(alert_env.get("DELIVERED", "")), \
        "告警 job 沒有讀 send-report 的 delivered output"

    import morning_report as mr
    assert hasattr(mr, "_gha_output"), "晨報沒有寫 step output 的能力"


def _heredoc(run: str) -> str:
    """把 `python - <<'PY' … PY` 裡的腳本抽出來。"""
    lines = run.splitlines()
    try:
        start = next(i for i, ln in enumerate(lines) if "<<'PY'" in ln) + 1
    except StopIteration:
        pytest.fail("告警步驟不是預期的 heredoc 形式,契約需要同步更新")
    end = next((i for i in range(len(lines) - 1, start - 1, -1)
                if lines[i].strip() == "PY"), len(lines))
    body = lines[start:end]
    pad = min((len(ln) - len(ln.lstrip()) for ln in body if ln.strip()), default=0)
    return "\n".join(ln[pad:] for ln in body)


def test_readme_documents_every_llm_variable_the_workflow_reads():
    """**README 說得出來的,必須是 workflow 真的在讀的**(第九輪 P2-4)。

    2026-08-01 使用者照 README 設定卻沒生效 —— 當時 README 完全沒有提到
    OpenAI 那組變數,也沒說「Variables 與 Secrets 是兩個不同的分頁」。
    設定文件漂移的代價不是看起來不專業,而是**使用者做了正確的事卻沒有效果**,
    然後從結果完全看不出原因。

    這條只驗一個方向(workflow 讀的 → README 要寫到):README 多寫一些
    背景說明是好事,少寫一個開關則會讓人設在錯的地方。
    """
    env = _report_step(_workflow()).get("env") or {}
    readme = (Path(__file__).resolve().parents[1] / "README.md")
    if not readme.exists():
        pytest.fail("找不到 README.md —— 設定文件契約不得因檔案不見而跳過")
    text = readme.read_text(encoding="utf-8")
    missing = [k for k in LLM_VARS if k in env and f"`{k}`" not in text]
    assert not missing, (
        f"README 沒有寫到這些 LLM 開關:{'、'.join(missing)} —— "
        "使用者會不知道要設,或設在 Secrets 而不是 Variables")
    assert "Variables 與 Secrets 是兩個不同的分頁" in text, (
        "README 沒有點出 Variables/Secrets 的差別 —— 那正是 2026-08-01 "
        "設定沒生效的原因,而症狀是「一切照舊」")


def test_readme_test_count_is_a_floor_not_a_boast():
    """README 宣稱的測試數**不得灌水**(第九輪 P2-4)。

    宣稱數要是真實數的**下界**:寫少了只是保守,寫多了就是不實。
    另外差距不得過大,否則這個數字會慢慢變成沒人維護的裝飾。
    """
    import ast

    root = Path(__file__).resolve().parents[1]
    text = (root / "README.md").read_text(encoding="utf-8")
    claims = re.findall(r"([\d,]+)\+\s*(?:單元)?測試", text)
    assert claims, "README 不再宣稱測試數 —— 若刻意移除,連同這條測試一起刪"
    claimed = max(int(c.replace(",", "")) for c in claims)

    # 用 AST 數 test 函式:這是**下界**(parametrize 會展開成更多筆),
    # 所以「宣稱 ≤ AST 數」是比實際更嚴格的要求,不會誤判成通過。
    actual = 0
    for path in sorted((root / "tests").glob("test_*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                    and node.name.startswith("test_"):
                actual += 1
    assert claimed <= actual, (
        f"README 宣稱 {claimed}+ 測試,但實際只數到 {actual} 個 test 函式")
    assert actual - claimed <= 400, (
        f"README 宣稱 {claimed}+,實際 {actual} —— 差距太大,請更新宣稱數")


def test_the_workflow_does_not_pin_values_that_disable_in_code_logic():
    """批#97:**workflow 寫死一個值,等於把程式裡的機制變成死碼。**

    批#93 讓 LLM 時間預算依 provider 與推理強度放大,但 workflow 當時寫著
    `LLM_REQUEST_TIMEOUT_SECONDS: "75"`。程式讓明設的環境變數優先(那是對的
    —— 要能臨時壓低),於是整套放大從來沒有發生過,而症狀是「沒有錯誤、
    沒有告警,只是沒生效」。2026-08-01 的後果:GPT-5.6 在 75 秒內跑不完
    85,814-token 的 prompt → ReadTimeout → Gemini 也失敗 → 使用者收到降級版。

    這條要求:這類「程式會自己算」的旋鈕在 workflow 裡只能接 `vars.*`,
    不能是字面值。要臨時壓低就去設 repo variable —— 那是一個看得見的動作。
    """
    env = _report_step(_workflow()).get("env") or {}
    computed = ("LLM_TOTAL_TIMEOUT_SECONDS", "LLM_REQUEST_TIMEOUT_SECONDS")
    pinned = [k for k in computed
              if k in env and "vars." not in str(env[k])]
    assert not pinned, (
        f"{'、'.join(pinned)} 在 workflow 裡被寫死 —— "
        "程式裡依 provider/推理強度計算的邏輯會變成死碼")


def test_the_computed_timeout_actually_applies_when_the_variable_is_unset(
        monkeypatch):
    """**光是移除寫死還不夠 —— 要驗算出來的值真的被用上。**

    GitHub Actions 對未設定的 `vars.X` 會傳**空字串**而不是不傳,
    所以程式端必須把空字串當成「沒設」。少了這一步,改完 workflow 依然沒效果。
    """
    import importlib

    import morning_report as mr

    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-t")
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", "medium")
    monkeypatch.setenv("LLM_REQUEST_TIMEOUT_SECONDS", "")   # 未設的真實形狀
    monkeypatch.setenv("LLM_TOTAL_TIMEOUT_SECONDS", "")
    reloaded = importlib.reload(mr)
    try:
        assert reloaded.LLM_REQUEST_TIMEOUT_SECONDS > 75, (
            "空字串沒有被當成「沒設」,算出來的 timeout 沒有生效")
        assert reloaded.LLM_TOTAL_TIMEOUT_SECONDS >= 360
        # 明設仍要優先(逃生門不能壞)
        monkeypatch.setenv("LLM_REQUEST_TIMEOUT_SECONDS", "30")
        assert importlib.reload(mr).LLM_REQUEST_TIMEOUT_SECONDS == 30
    finally:
        for k in ("LLM_PROVIDER", "OPENAI_API_KEY", "OPENAI_REASONING_EFFORT",
                  "LLM_REQUEST_TIMEOUT_SECONDS", "LLM_TOTAL_TIMEOUT_SECONDS"):
            monkeypatch.delenv(k, raising=False)
        importlib.reload(mr)


CANARY_PATH = (Path(__file__).resolve().parents[1]
               / ".github" / "workflows" / "validate-llm-config.yml")


def _canary() -> dict:
    if not CANARY_PATH.exists():
        pytest.fail(f"找不到金絲雀 workflow:{CANARY_PATH}")
    return yaml.safe_load(CANARY_PATH.read_text(encoding="utf-8"))


def test_the_canary_cannot_send_mail_or_write_state():
    """批#98(第九輪 P1-5):**金絲雀是診斷工具,不得有副作用。**

    它要能在任何時候被按下去 —— 包括晨報正在跑的時候。所以它不碰 Gmail
    憑證、不寫 repo、也不與 state 寫入者共用 concurrency group。
    只讀權限是結構性的保證,比「我記得沒有寫」可靠。
    """
    wf = _canary()
    job = wf["jobs"]["canary"]
    text = CANARY_PATH.read_text(encoding="utf-8")

    assert wf.get("permissions") == {"contents": "read"}, \
        "金絲雀必須是唯讀 —— 它沒有任何理由寫回 repo"
    for forbidden in ("GMAIL_USER", "GMAIL_APP_PASSWORD", "RECIPIENT"):
        assert forbidden not in text, f"金絲雀不該碰 {forbidden} —— 它不寄信"
    assert "morning_report.py" not in text, \
        "金絲雀跑起晨報本體就會寄信/寫 state,那不是診斷"
    # 觸發方式:只有手動。它會花 API 額度,不該每次 push 都跑
    triggers = wf[True] if True in wf else wf["on"]
    assert list(triggers) == ["workflow_dispatch"], triggers
    # 不得與 state 寫入者搶同一個 group(否則會互相取消)
    main_group = (_workflow().get("concurrency") or {}).get("group")
    assert (wf.get("concurrency") or {}).get("group") != main_group
    assert job["steps"], "金絲雀沒有步驟"


def test_the_canary_reads_the_same_variables_as_the_morning_report():
    """**金絲雀查的必須是正式排程真的會用的那份設定。**

    自己另設一組預設值,等於在驗一個不存在的情境 —— 金絲雀綠燈、隔天照壞,
    而那比沒有金絲雀更糟(它會讓人停止懷疑設定)。
    """
    canary_env = _canary()["jobs"]["canary"]["steps"][-1]["env"]
    report_env = _report_step(_workflow()).get("env") or {}
    for key in LLM_VARS:
        if key not in canary_env:
            continue
        assert canary_env[key] == report_env.get(key), (
            f"{key} 在金絲雀與晨報之間不一致:\n"
            f"  金絲雀 {canary_env[key]}\n  晨報   {report_env.get(key)}")
    # 至少要涵蓋決定「用哪個模型」的那幾個,否則驗不到重點
    for key in ("LLM_PROVIDER", "OPENAI_MODEL", "OPENAI_REASONING_EFFORT"):
        assert key in canary_env, f"金絲雀沒有讀 {key}"


def test_the_canary_redacts_keys_from_anything_it_prints(monkeypatch):
    """輸出會進 job log,而 log 可能被分享。"""
    import importlib
    import sys as _sys

    _sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-verysecretvalue123")
    canary = importlib.import_module("validate_llm_config")
    importlib.reload(canary)
    leaked = canary._safe("Bearer sk-verysecretvalue123 rejected")
    assert "sk-verysecretvalue123" not in leaked
    assert "<OPENAI_API_KEY>" in leaked
    # 太短的值不遮(否則會把常見字串都打成 <KEY>,反而看不懂錯誤)
    monkeypatch.setenv("OPENAI_API_KEY", "abc")
    importlib.reload(canary)
    assert canary._safe("abc def") == "abc def"


def test_the_three_budgets_are_ordered_and_stay_ordered():
    """批#101:**job timeout > 執行預算 > LLM 預算** —— 這三個數字必須成階梯。

    它們分散在三個檔案(workflow / morning_report / llm_telemetry),而且沒有
    任何東西綁著。調高其中一個而忘了另一個的後果各自不同,但都很嚴重:

      - 執行預算 ≥ job timeout → **job 在寄信途中被 GitHub 殺掉**,
        而 `RUN_BUDGET_SECONDS` 存在的唯一理由就是防這件事;
      - LLM 預算 ≥ 執行預算 → `_core_tail_seconds()` 的保留量超過總預算,
        所有昂貴步驟(新聞全文、8-K 補抓、事件抽取)**全部被跳過**,
        信照樣寄出但內容被掏空 —— 而那是最難察覺的一種壞掉。

    2026-08-01 三個數字一起放寬(25→40 分、1140→2100s、600→900s),
    這條把「一起」變成強制的。
    """

    import morning_report as mr

    job_seconds = int(_workflow()["jobs"]["send-report"]["timeout-minutes"]) * 60
    run_budget = mr.RUN_BUDGET_SECONDS
    llm_total = llm_config.MAX_TOTAL_TIMEOUT

    # 寄信 + state push + 存檔的尾段。job 被殺 = 使用者收不到信。
    tail = 240
    assert run_budget + tail <= job_seconds, (
        f"執行預算 {run_budget:.0f}s + 收尾 {tail}s 超過 job timeout "
        f"{job_seconds}s —— job 會在寄信途中被殺")

    # LLM 預算加上核心保留之後,要留得下昂貴步驟,否則它們永遠被跳過
    core_tail = llm_total + 40
    cheapest_expensive_step = 140      # `_run_budget_ok` 呼叫端的最小估時
    assert core_tail + cheapest_expensive_step < run_budget, (
        f"LLM 保留 {core_tail:.0f}s + 最便宜的昂貴步驟 {cheapest_expensive_step}s "
        f"≥ 執行預算 {run_budget:.0f}s —— 新聞全文擷取等步驟會永遠被跳過")

    # 而且要留下有意義的工作空間,不是剛好塞得下
    assert run_budget - core_tail >= 600, (
        f"扣掉 LLM 保留後只剩 {run_budget - core_tail:.0f}s 給資料收集 —— "
        "2026-08-01 實測光是行情+新聞+籌碼就要約 600s")


def test_preflight_runs_exactly_what_ci_gates_on():
    """本機閘門必須與 CI **逐項對應**(使用者定案 2026-08-01:過 CI 才能 push)。

    2026-08-01 我用 `ruff check . | tail -1` 判定 lint,看到最後一行
    `No fixes available (1 hidden fix…)` 就當成通過,結果 CI 紅 ——
    **用 tail 看檢查結果本身就是一個會靜默通過的檢查器。**

    但「照 CI 跑」這個約定會漂移:CI 新增一項檢查而本機腳本沒跟上,
    preflight 照樣全綠、push 之後才發現。這條把對應關係釘住。

    只比對**擋門的那個 job**(`test`);`dry-run-preview` 是手動觸發加
    `continue-on-error`,不是閘門,本機也不該去打外部 API。
    """
    root = Path(__file__).resolve().parents[1]
    ci_path = root / ".github" / "workflows" / "ci.yml"
    script = root / "tools" / "preflight.sh"
    for p in (ci_path, script):
        if not p.exists():
            pytest.fail(f"找不到 {p.name} —— 閘門契約不得因檔案不見而跳過")

    ci = yaml.safe_load(ci_path.read_text(encoding="utf-8"))
    gate = ci["jobs"]["test"]
    assert not gate.get("continue-on-error"), "擋門的 job 不能 continue-on-error"

    text = script.read_text(encoding="utf-8")
    # **只看可執行的行。** 被註解掉的指令字面上還在 —— 拿整份文字比對的話,
    # 「把 compileall 註解掉」這種改動會完全通過(我第一版就是這樣,
    # 三個突變只抓到兩個)。註解裡也常常會提到指令名稱。
    code = "\n".join(ln for ln in text.splitlines()
                     if ln.strip() and not ln.lstrip().startswith("#"))
    commands = [str(s["run"]).strip() for s in gate["steps"] if s.get("run")]
    checks = [c for c in commands if c.startswith("python -m")]
    assert checks, "CI 的 test job 沒有任何 python -m 檢查 —— 契約需要更新"
    missing = [c for c in checks if c not in code]
    assert not missing, (
        "CI 會跑但 tools/preflight.sh 沒有跑:\n  " + "\n  ".join(missing) +
        "\n本機閘門與 CI 脫節 = preflight 全綠而 push 後才發現")

    # 任一步失敗必須立刻停,否則後面的綠燈會蓋掉前面的紅燈
    assert "set -euo pipefail" in text, "preflight 沒有 fail-fast"
    # 而且不得用管線去讀檢查結果(那正是這次的病灶)。同樣只看可執行的行:
    # 第一版連自己「不要用 | tail」那句註解都判成違規,而「解釋為什麼不做
    # 某件事」的註解本身就會提到那件事。
    for bad in ("| tail", "|tail", "| head", "|head"):
        assert bad not in code, f"preflight 用了 {bad} 判讀結果 —— 會靜默通過"


def test_no_workflow_declares_the_same_env_key_twice():
    """r2(Codex,P1):**同一個 mapping 裡的重複 key 會靜默勝出。**

    2026-08-01 我把 `DEEPSEEK_REASONING_EFFORT` 改成 repo variable 可覆寫,
    卻沒發現同一個 `env:` 區塊底下 14 行外還有一個寫死的 `high` ——
    後者勝出,於是那個 variable **完全沒有效果**,而症狀是「一切照舊」。

    **`yaml.safe_load` 看不到這件事**:它把重複 key 靜默收斂成最後一個,
    所以既有的所有契約測試都通過了。這條必須讀**原始文字**。

    (同一個錯我在批#90f 差點犯過一次 —— 當時是 `LLM_PROVIDER`,
     靠人眼發現。人眼不是守衛。)
    """
    import collections
    import re

    root = Path(__file__).resolve().parents[1] / ".github" / "workflows"
    files = sorted(root.glob("*.yml"))
    assert files, "找不到任何 workflow —— 掃描路徑錯了"
    problems = []
    for path in files:
        # 以縮排分群:同一縮排層級、連續的 `KEY: value` 視為同一個 mapping。
        groups = collections.defaultdict(list)
        block = 0
        prev_indent = None
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            indent = len(line) - len(line.lstrip())
            m = re.match(r"([A-Za-z_][A-Za-z0-9_-]*):", line.strip())
            if prev_indent is not None and indent != prev_indent:
                block += 1
            prev_indent = indent
            if m and not line.lstrip().startswith("-"):
                groups[(indent, block)].append(m.group(1))
        for keys in groups.values():
            for key, n in collections.Counter(keys).items():
                if n > 1:
                    problems.append(f"{path.name}: {key} 出現 {n} 次")
    assert not problems, (
        "workflow 有重複的 key(後者靜默勝出,前者形同不存在):\n  "
        + "\n  ".join(problems))


def test_the_state_contract_gates_the_push_not_the_other_way_round():
    """第十輪 P1-9:**push 才是發佈邊界,契約要卡在 commit 與 push 之間。**

    原本晨報自己 commit + push,而 schema 契約是 workflow 的**下一步** ——
    壞掉的 state 早就在 main 上,契約只能事後告訴你。而 `_STATE_WRITES`
    只看得到 I/O 失敗,看不到 schema 損壞、跨檔版本不一致、語意空資料。

    信在契約之前就已寄出,所以這不違反「晨報不可斷」。
    """
    wf = _workflow()
    steps = wf["jobs"]["send-report"]["steps"]
    names = [str(s.get("name") or "") for s in steps]
    report_i = next(i for i, s in enumerate(steps)
                    if "morning_report.py" in str(s.get("run") or ""))
    contract_i = next(i for i, s in enumerate(steps)
                      if "test_state_schema_contract" in str(s.get("run") or ""))
    push_i = next((i for i, s in enumerate(steps)
                   if "push_committed_state" in str(s.get("run") or "")), None)
    assert push_i is not None, f"沒有獨立的發佈步驟:{names}"
    assert report_i < contract_i < push_i, (
        f"順序必須是 晨報 → 契約 → 發佈,實際是 {report_i}/{contract_i}/{push_i}")

    # 晨報必須被告知延後 push,否則它會自己推出去、契約再擋也來不及
    assert (steps[report_i].get("env") or {}).get("STATE_PUSH_DEFERRED") == "1"
    # 發佈步驟**不得**有 `if: always()` —— 契約失敗時它必須被跳過
    assert "always" not in str(steps[push_i].get("if") or ""), \
        "發佈步驟用了 always(),契約失敗照樣 push —— 閘門形同虛設"

    import morning_report as mr
    assert hasattr(mr, "push_committed_state")


def test_each_canary_probe_job_holds_only_its_own_key():
    """第十輪 P0-1 + P1-3:**真實探測與金鑰隔離要同時成立。**

    P1-3 要求每個 provider 都發真請求;P0-1 要求不要把四把金鑰放進同一個
    process。matrix 同時滿足兩者 —— 但前提是每個 job 真的只拿自己那一把,
    所以這裡逐一比對表達式。
    """
    wf = _workflow_at("validate-llm-config.yml")
    probe = wf["jobs"]["probe"]
    assert probe["strategy"]["matrix"]["provider"] == [
        "openai", "deepseek", "gemini", "anthropic"]
    env = probe["steps"][-1]["env"]
    for provider, key in (("openai", "OPENAI_API_KEY"),
                          ("deepseek", "DEEPSEEK_API_KEY"),
                          ("gemini", "GEMINI_API_KEY"),
                          ("anthropic", "ANTHROPIC_API_KEY")):
        expr = str(env.get(key, ""))
        assert f"matrix.provider == '{provider}'" in expr, (
            f"{key} 沒有以 matrix.provider 條件化 —— 每個 job 會拿到所有金鑰:{expr}")

    # 設定驗證那個 job 仍然不得持有非 OpenAI 的金鑰
    cfg_env = wf["jobs"]["canary"]["steps"][-1]["env"]
    for forbidden in ("DEEPSEEK_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY"):
        assert forbidden not in cfg_env, f"設定 job 又拿到了 {forbidden}"
    for flag in ("DEEPSEEK_KEY_PRESENT", "GEMINI_KEY_PRESENT",
                 "ANTHROPIC_KEY_PRESENT"):
        assert flag in cfg_env, f"缺 {flag} —— 缺金鑰會被誤判成沒設定"
    # 而且不得有任何安裝步驟(未鎖版套件 + 金鑰 = 供應鏈風險)
    for job in wf["jobs"].values():
        for step in job["steps"]:
            assert "pip install" not in str(step.get("run") or ""), \
                "金絲雀又開了安裝步驟 —— 它只用標準函式庫"


def _workflow_at(name: str) -> dict:
    path = Path(__file__).resolve().parents[1] / ".github" / "workflows" / name
    if not path.exists():
        pytest.fail(f"找不到 {name}")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


# ── 第十一輪 P2-1:設定來源契約 ────────────────────────────────────────
#: 報告步驟裡**不是 LLM 行為開關**的 env 鍵,逐一明列。
#:
#: r1(Codex,#1):這裡原本是 `^(LLM_|OPENAI_|DEEPSEEK_|EXTRACTOR_)` 的前綴
#: regex —— 也就是**守衛自己決定要掃哪些檔**。`GEMINI_MODEL` 與 `CLAUDE_MODEL`
#: 因此被無聲排除,而那兩家是 `VALID_PROVIDERS` 的成員、gemini 更是所有
#: 設定錯誤的落點。一個宣稱「雙向相等」的守衛,卻對半數 provider 視而不見。
#:
#: 改成**白名單**:報告步驟的每一個 env 鍵,不是 LLM 開關(進
#: `CONFIG_SOURCE_SPEC`)就是列在這裡。新增第五家 provider 時,漏掉它會紅。
#: (這與 `test_module_size_freeze.UNCAPPED_MODULES` 是同一個形狀:
#:  守衛不得自己縮小掃描範圍,只能明列例外。)
_NOT_AN_LLM_SWITCH = {
    # 金鑰:是 Secrets,而且**絕不可以**進 manifest。
    "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY", "FRED_API_KEY",
    "GEMINI_API_KEY", "OPENAI_API_KEY",
    # 這一行的內容就是「開關的原始值」本身,不是開關。
    "LLM_CONFIG_RAW",
    # 寄信與收件人。
    "GMAIL_USER", "GMAIL_APP_PASSWORD", "RECIPIENT", "CONTACT_EMAIL",
    # 持股(Secrets;明細不得進 HTML/prompt/state/log)與顯示名稱。
    "PORTFOLIO_1", "PORTFOLIO_2", "PORTFOLIO_1_NAME", "PORTFOLIO_2_NAME",
    # 執行環境,與 LLM 無關。
    "STATE_PUSH_DEFERRED", "TZ",
}


def _llm_env_keys(env: dict) -> set:
    """報告步驟裡所有會改變 LLM 行為的 env 鍵。"""
    return set(env) - _NOT_AN_LLM_SWITCH


def test_the_config_source_table_matches_the_workflow_both_ways():
    """`CONFIG_SOURCE_SPEC` 與 workflow 的開關集合必須**完全相等**。

    只驗單向會留下兩個各自無聲的洞:表少一個 = manifest 答不出那個開關的
    來源(症狀是少一格,沒人會發現);workflow 少一個 = 程式讀得到、
    使用者設了卻沒傳進來(症狀是設定靜默無效 —— `LLM_SHADOW_REASONING_EFFORT`
    當初就是這樣,是逐鍵列表時才發現的)。
    """
    env = _report_step(_workflow()).get("env") or {}
    spec = set(llm_config.CONFIG_SOURCE_SPEC)
    # 2026-08-08 單一模型架構:影子/實驗/OpenAI 開關整批拆除,12 個是
    # 真實的全集 —— 門檻跟著降,但仍防「表被清空後真空通過」。
    assert len(spec) >= 10, (
        "CONFIG_SOURCE_SPEC 只剩 %d 個鍵 —— 這條測試會因此變成空集合真空通過"
        % len(spec))
    in_workflow = _llm_env_keys(env)
    assert spec == in_workflow, (
        f"表裡有 workflow 沒有的:{sorted(spec - in_workflow)};"
        f"workflow 有表裡沒有的:{sorted(in_workflow - spec)}")


def test_every_declared_default_is_the_default_the_workflow_actually_uses():
    """宣告的預設值必須就是 workflow 那一格寫的值(逐格比對)。

    `config_sources` 會把 `workflow_default` 寫進 manifest;那個數字若是抄來的,
    它記錄的就是**我以為的預設**而不是生效的預設 —— 那比不記錄更糟,
    因為它看起來已經回答了問題。
    """
    env = _report_step(_workflow()).get("env") or {}
    for key, (kind, declared) in llm_config.CONFIG_SOURCE_SPEC.items():
        expr = str(env.get(key, ""))
        if kind == "fixed":
            assert "${{" not in expr, f"{key} 宣告成 fixed,workflow 卻用了表達式"
            assert expr == declared, f"{key} 寫死值不符:{expr!r} vs {declared!r}"
            continue
        m = re.search(r"vars\.%s\s*\|\|\s*'([^']*)'" % re.escape(key), expr)
        if m is None:
            # 沒有 `|| 預設` 就等於預設是空字串(逾時那兩個開關就是如此)。
            assert re.search(r"vars\.%s\s*\}\}" % re.escape(key), expr), (
                f"{key} 沒有接到 vars.{key}:{expr}")
            assert declared == "", f"{key} 宣告預設 {declared!r},workflow 卻沒給預設"
        else:
            assert m.group(1) == declared, (
                f"{key} 預設不符:workflow={m.group(1)!r} 宣告={declared!r}")


def test_config_raw_carries_every_overridable_switch():
    """`LLM_CONFIG_RAW` 要帶到每一個可覆寫開關的**原始**值。

    帶不到的鍵,`config_sources` 只能回 `unknown` —— 那正是這一批要消滅的
    狀態(批#118 把預設改成 `max` 之後,manifest 答不出那是誰決定的)。
    每個鍵還必須接到**自己**那個 `vars.*`:接錯不會有錯誤,只會記錯來源。
    """
    env = _report_step(_workflow()).get("env") or {}
    raw = str(env.get("LLM_CONFIG_RAW") or "")
    assert raw, "workflow 沒有 LLM_CONFIG_RAW"
    for key in llm_config.CONFIG_RAW_KEYS:
        assert re.search(r"\b%s=\$\{\{\s*vars\.%s\s*\}\}"
                         % (re.escape(key), re.escape(key)), raw), (
            f"LLM_CONFIG_RAW 沒有帶 {key} 的原始值(或接錯到別的 vars.*)")
    # 反向:`fixed` 的鍵不該混進來 —— 它們沒有 repo variable 可讀,
    # 混進來只會讓每一班都多一筆假的「走預設」。
    parsed = set(llm_config.parse_config_raw(
        raw.replace("${{", "").replace("}}", "")))
    assert parsed == set(llm_config.CONFIG_RAW_KEYS), (
        f"LLM_CONFIG_RAW 的鍵集合不符:{sorted(parsed)}")


def test_the_program_reports_a_resolved_value_for_every_switch(monkeypatch):
    """`_llm_config_resolved()` 要**每一個**開關都有值,而且來自模組常數。

    漏掉一個的症狀是 manifest 少一格 `resolved`,不會有任何錯誤。
    """
    import morning_report as mr
    resolved = mr._llm_config_resolved()
    missing = sorted(set(llm_config.CONFIG_SOURCE_SPEC) - set(resolved))
    assert not missing, f"這些開關沒有回報實際採用值:{missing}"
    extra = sorted(set(resolved) - set(llm_config.CONFIG_SOURCE_SPEC))
    assert not extra, f"回報了不在表裡的鍵:{extra}"
    # 逾時是**算出來的**,不是環境變數 —— 重讀 os.environ 會拿到空字串。
    assert float(resolved["LLM_TOTAL_TIMEOUT_SECONDS"]) > 0
    assert float(resolved["LLM_REQUEST_TIMEOUT_SECONDS"]) > 0


def test_readme_documents_the_same_default_as_the_workflow():
    """README 表格的「預設」欄要跟 workflow 一致(第十一輪 P2-1)。

    在此之前它漂過:`DEEPSEEK_REASONING_EFFORT` 寫著預設 `high`,而批#118
    已經把 workflow 預設改成 `max`。使用者照著讀就會以為自己在跑 high。
    """
    readme = Path(__file__).resolve().parents[1] / "README.md"
    if not readme.exists():
        pytest.fail("找不到 README.md —— 設定文件契約不得因檔案不見而跳過")
    text = readme.read_text(encoding="utf-8")
    for key in llm_config.CONFIG_RAW_KEYS:
        row = re.search(r"^\|\s*`%s`\s*\|([^|]*)\|" % re.escape(key),
                        text, re.M)
        assert row, f"README 變數表沒有 `{key}` 這一列"
        cell = row.group(1).strip()
        _, declared = llm_config.CONFIG_SOURCE_SPEC[key]
        if declared:
            assert f"`{declared}`" == cell, (
                f"README 的 `{key}` 預設寫 {cell!r},workflow 是 `{declared}`")
        else:
            assert cell.startswith("空"), (
                f"README 的 `{key}` 預設是空字串,卻寫 {cell!r}")


def test_a_whitespace_only_variable_counts_as_set(monkeypatch):
    """`LLM_PROVIDER=" "` 是**設了一個壞掉的值**,不是沒設(r1 Codex #2)。

    GitHub Actions 的 `${{ vars.X || '預設' }}` 把 whitespace-only 當成
    truthy,所以走的是 repo variable 那條路。若來源判定先 strip,manifest
    會說「走 workflow 預設」,而實際上 `_call_llm_text` 對不上任何分支、
    落到 Gemini —— 遙測與實跑各說各話,正是這一批要消滅的狀態。
    """
    src = llm_config.config_sources(
        "LLM_PROVIDER= ;DEEPSEEK_REASONING_EFFORT=", {"LLM_PROVIDER": " "})
    assert src["LLM_PROVIDER"]["source"] == "repo_variable", (
        "whitespace-only 被當成沒設 —— manifest 會把使用者的錯誤記成走預設")
    assert src["DEEPSEEK_REASONING_EFFORT"]["source"] == "workflow_default", \
        "真正的空字串仍必須是 workflow_default"

    issues = [str(i) for i in llm_config.validate_llm_config(
        provider=" ", extractor_provider="", shadow_provider="",
        has_key=lambda _e: True)]
    assert any("空白" in m for m in issues), (
        f"provider 設成空白沒有被報成設定錯誤:{issues} —— "
        "它會靜默落到 Gemini")
    empty = [str(i) for i in llm_config.validate_llm_config(
        provider="", extractor_provider="", shadow_provider="",
        has_key=lambda _e: True)]
    assert any("空的" in m for m in empty), \
        "primary provider 是空的也必須報 —— 空一樣落到 Gemini"


def test_the_runtime_provider_is_stripped_the_same_way_validation_strips_it():
    """驗證與 dispatch 必須看到**同一個** provider 值(r1 Codex #2)。

    `validate_llm_config` 內部 strip、`_call_llm_text` 用 `==` 比對:
    只要 runtime 常數不 strip,`" deepseek "` 就會驗證說沒問題、實際跑 Gemini。
    """
    src = (Path(__file__).resolve().parents[1] / "morning_report.py").read_text(
        encoding="utf-8")
    m = re.search(r"^LLM_PROVIDER = os\.environ\.get\([^)]*\)(.*)$", src, re.M)
    assert m, "找不到 LLM_PROVIDER 的定義 —— 這條契約需要同步更新"
    assert ".strip()" in m.group(1), (
        "LLM_PROVIDER 沒有 strip —— repo variable 多一個空格就會靜默跑 Gemini")


def test_every_provider_gets_its_own_model_recorded():
    """四家 provider 都要記到**自己**的模型(r1 Codex #1)。

    原本是 `OPENAI_MODEL if openai else DEEPSEEK_MODEL` —— 選 gemini 或
    anthropic 那一班,manifest 會記成 DeepSeek 的模型。gemini 更是所有
    設定錯誤的落點,最需要被記對的就是它。

    現在 dispatcher 與遙測共用 `_PROVIDERS`,所以「呼叫哪家」與「記哪家」
    結構上不可能分岔。這條驗那張表**涵蓋每一家合法 provider**,
    以及未知 provider 的落點與 dispatcher 一致。
    """
    import morning_report as mr

    assert set(mr._PROVIDERS) == set(llm_config.VALID_PROVIDERS), (
        "provider 表與 VALID_PROVIDERS 不一致 —— 新增一家 provider 卻沒有"
        "給它呼叫函式/模型,信會由別家寫、manifest 會記別家的")
    # P1-1(2026-08-19):未知 provider 不再落到 Gemini —— 那個 fallthrough
    # 讓一個 typo 繞過「Gemini 只留抽取器備援」的政策。遙測記
    # `invalid:<原值>`,不記一個沒有人選過的模型。
    for prov, expect in (("openai", mr.OPENAI_MODEL),
                         ("deepseek", mr.DEEPSEEK_MODEL),
                         ("anthropic", mr.CLAUDE_MODEL),
                         ("gemini", mr.GEMINI_MODEL),
                         ("typo-provider", "invalid:typo-provider")):
        old = mr.LLM_PROVIDER
        try:
            mr.LLM_PROVIDER = prov
            got = mr._primary_model()
        finally:
            mr.LLM_PROVIDER = old
        assert got == expect, f"provider={prov} 記成 {got},應該是 {expect}"


def test_the_ci_canary_runs_the_same_settings_as_the_scheduled_job():
    """**canary 的價值全部來自「跑的是同一條路」**(外審 P2-1)。

    先前 CI 的 dry-run 用 v4-pro / high / `LLM_EVENT_EXTRACTION=0`,
    而排程班是 flash / max / 抽取器開啟、走特化結構化路徑 ——
    「canary 綠」在那個設定下不代表生產跑得起來,而那正是這個 repo
    反覆遇到的「測試全綠、生產零產出」的形狀。

    這裡逐項比對兩個 workflow 的 env,任何一項漂移都要紅。
    """
    import yaml
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / ".github" / "workflows"
    prod = yaml.safe_load((root / "morning-report.yml").read_text(encoding="utf-8"))
    ci = yaml.safe_load((root / "ci.yml").read_text(encoding="utf-8"))

    def _run_env(wf, job, needle):
        for step in wf["jobs"][job]["steps"]:
            env = step.get("env") or {}
            if needle in env:
                return env
        raise AssertionError(f"{job} 找不到帶 {needle} 的步驟")

    prod_env = _run_env(prod, "send-report", "DEEPSEEK_MODEL")
    ci_env = _run_env(ci, "dry-run-preview", "DEEPSEEK_MODEL")
    # 2026-08-19 第三輪 P2-4:**不再手抄五個 key** —— 凡是會影響 LLM
    # 路由/模型/額度/timeout 的生產 env,canary 都要逐字鏡射。清單由
    # 生產 workflow 的 env 推導(前綴/名單),漂移(生產加了、canary 沒跟)
    # 就紅 —— 手抄清單正是 EXTRACTOR_PROVIDER 漏掉的原因。
    # r4 F2:**provider 專屬模型鍵不得因前綴而漏**(CLAUDE_MODEL 沒有
    # LLM_/DEEPSEEK_ 前綴,手抄前綴清單會靜默漏掉它 —— 生產切 anthropic
    # 那天 canary 跑的是 Python 預設模型)。凡以 _MODEL 結尾、或在
    # LLM 路由前綴群的非機密鍵,一律鏡射。
    _routing_keys = sorted(
        k for k in prod_env
        if (k.startswith(("LLM_", "DEEPSEEK_", "EXTRACTOR_"))
            or k.endswith("_MODEL"))
        and not k.endswith(("_API_KEY", "_BASE_URL"))
        and k != "LLM_CONFIG_RAW")
    assert "EXTRACTOR_PROVIDER" in _routing_keys, "推導清單掉了抽取器路由"
    assert "CLAUDE_MODEL" in _routing_keys and "GEMINI_MODEL" in _routing_keys
    for key in _routing_keys:
        assert key in ci_env, f"canary 少了 {key} —— 它會拿到與生產不同的預設"
        assert str(ci_env[key]) == str(prod_env[key]), (
            f"{key} 在 canary 與排程班不一致:"
            f"canary={ci_env[key]!r} vs 排程={prod_env[key]!r}")
    assert ci_env["DRY_RUN"] == "1", "canary 必須是 DRY_RUN(不寄信)"

    # **時間預算要裝得下**:max 推理下總預算 1200s,12 分鐘的 job timeout
    # 會在 canary 證明任何事之前先把它砍掉。
    import llm_config as lc
    job_seconds = int(ci["jobs"]["dry-run-preview"]["timeout-minutes"]) * 60
    assert job_seconds > lc.MAX_TOTAL_TIMEOUT, (
        f"canary 的 job timeout {job_seconds}s 裝不下 LLM 總預算 "
        f"{lc.MAX_TOTAL_TIMEOUT}s")


# ------------------------------------------------- 2026-08-13 Podcast #235

def test_every_state_push_goes_through_the_shared_retry_policy():
    """**state push 的重試政策只有一份。**

    2026-08-13 #235:podcast digest 的 commit 做好了,push 收到 GitHub 500
    (`remote rejected ... Internal Server Error`)—— 而那個呼叫點完全沒有
    重試,那一班的 digest 整批掉了。當時四個呼叫點有四種寫法(兩個沒有
    重試、一個沒有退避)。「改了一份、另外三份還留著舊的」是本 repo
    反覆記過的失效形狀 —— 這條釘住它們都走同一份政策。
    """
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    assert (root / "tools" / "push_state.sh").exists()
    for name in ("podcast-digest", "deepseek-canary", "gooaye-radar",
                 "monthly-ic-report"):
        text = (root / ".github" / "workflows" / f"{name}.yml").read_text(
            encoding="utf-8")
        if "git commit" not in text:
            continue
        assert "tools/push_state.sh" in text, f"{name} 沒走共用的 push 政策"
        # 裸 `git push` 不得再出現(那就是繞過政策)
        bare = [ln for ln in text.splitlines()
                if ln.strip() == "git push"]
        assert not bare, f"{name} 還有裸 git push:{bare}"


def test_the_main_state_publisher_uses_the_same_policy():
    """晨報自己的發佈步驟也走同一份 —— 它原本自己寫「推一次、rebase、
    再推一次」,兩次之間沒有等待,對暫時性 5xx 等於連撞兩次。"""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1]
           / "morning_report.py").read_text(encoding="utf-8")
    body = src.split("def push_committed_state(")[1].split(chr(10) + "def ")[0]
    assert "push_state.sh" in body, "發佈步驟沒走共用政策"
    assert '"git", "push"' not in body, "還留著自己那份 push"


def test_the_retry_policy_backs_off_and_fails_loudly():
    """政策本身:多次嘗試、之間有退避、最後失敗要紅(不得吞掉保持綠燈)。"""
    from pathlib import Path
    sh = (Path(__file__).resolve().parents[1] / "tools"
          / "push_state.sh").read_text(encoding="utf-8")
    assert "sleep" in sh, "沒有退避 —— 對暫時性 5xx 等於連撞 N 次"
    assert "git pull --rebase --autostash" in sh, "沒有處理競寫"
    assert "exit 1" in sh and "::error::" in sh, "推不上去沒有變紅"


def _run_push_state(tmp_path, fail_times, attempts=3):
    """用假的 `git` 跑真的腳本 —— 只驗文字的合約測試證明不了它會重試。"""
    import os
    import shutil
    import subprocess
    from pathlib import Path
    bash = shutil.which("bash")
    if not bash:
        import pytest as _pt
        _pt.skip("這台機器沒有 bash")
    counter = tmp_path / "count"
    counter.write_text("0", encoding="utf-8")
    stub = tmp_path / "git"
    stub.write_text(
        "#!/usr/bin/env bash" + chr(10)
        + 'if [[ "$1" != "push" ]]; then exit 0; fi' + chr(10)
        + f'n=$(cat {counter.as_posix()})' + chr(10)
        + "n=$((n + 1))" + chr(10)
        + f'echo "$n" > {counter.as_posix()}' + chr(10)
        + f'if (( n <= {fail_times} )); then' + chr(10)
        + '  echo "remote: Internal Server Error" >&2; exit 1' + chr(10)
        + "fi" + chr(10) + "exit 0" + chr(10), encoding="utf-8")
    stub.chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = str(tmp_path) + os.pathsep + env.get("PATH", "")
    env["PUSH_STATE_ATTEMPTS"] = str(attempts)
    env["PUSH_STATE_SLEEPS"] = "0 0 0"          # 測試不等真的退避
    script = Path(__file__).resolve().parents[1] / "tools" / "push_state.sh"
    # **明確指定編碼**:Windows 預設 cp950 會在讀 UTF-8 的 stderr 時炸掉
    # reader thread,proc.stderr 變成 None(本機實測)。
    proc = subprocess.run([bash, str(script)], env=env, capture_output=True,
                          text=True, encoding='utf-8', errors='replace',
                          timeout=60)
    return proc, int(counter.read_text(encoding="utf-8"))


def test_a_transient_500_is_retried_and_succeeds(tmp_path):
    """2026-08-13 #235 的形狀:第一次 500、第二次成功 —— 那一班的
    state 不該就這樣掉了。"""
    proc, pushes = _run_push_state(tmp_path, fail_times=1)
    assert proc.returncode == 0, proc.stderr
    assert pushes == 2, f"沒有重試(push 次數={pushes})"


def test_persistent_failure_ends_red(tmp_path):
    """一直推不上去要紅 —— 吞掉保持綠燈的話,state 沒發佈而沒有人知道。"""
    proc, pushes = _run_push_state(tmp_path, fail_times=99, attempts=3)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert pushes == 3, f"嘗試次數不是 3(={pushes})"
    assert "::error::" in proc.stderr
