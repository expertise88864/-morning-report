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

yaml = pytest.importorskip("yaml")

WF_PATH = (Path(__file__).resolve().parents[1]
           / ".github" / "workflows" / "morning-report.yml")

#: 這些必須可由 repo variable 覆寫 —— 切換模型要能隨時改、隨時退回。
LLM_VARS = ("LLM_PROVIDER", "EXTRACTOR_PROVIDER",
            "OPENAI_MODEL", "OPENAI_EXTRACTOR_MODEL",
            "OPENAI_REASONING_EFFORT", "OPENAI_EXTRACTOR_REASONING",
            "LLM_SHADOW_PROVIDER", "LLM_SHADOW_MODEL")


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
    import llm_telemetry as lt

    import morning_report as mr

    job_seconds = int(_workflow()["jobs"]["send-report"]["timeout-minutes"]) * 60
    run_budget = mr.RUN_BUDGET_SECONDS
    llm_total = lt.MAX_TOTAL_TIMEOUT

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
