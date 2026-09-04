# -*- coding: utf-8 -*-
"""**執行期依賴與開發期依賴分層**(外審 2026-09-04 P3)。

`requirements.txt` 原本把 pytest / PyYAML 和 yfinance / pandas 混在一起,
`requirements.lock` 由它編譯,而每天早上那個持有 Gmail 憑證與 LLM 金鑰的
行程裝的就是這份 lock —— 晨報一行都不會 import 的測試工具,卻要它承擔
整條相依鏈。分層之後:

    requirements.txt      → requirements.lock       晨報真的會 import 的
    requirements-dev.txt  → requirements-dev.lock   CI 與本機開發才需要的

來源檔引的是 `requirements.txt`(這樣它在 Windows 本機也 `pip install` 得
起來 —— lock 的 hash 只涵蓋 Linux wheel),而**版本對齊由編譯保證**:
lock-refresh 用 `-c requirements.lock` 編 dev lock,共用套件因此被生產 lock
釘死,不是要靠人記得對齊的約定 —— CI 綠燈證明的就是 production 那些版本。
這個檔把上面每一句話變成機械檢查。
"""
import re
from pathlib import Path

import yaml

import morning_report as mr

_ROOT = Path(mr.__file__).resolve().parent
_WF_DIR = _ROOT / ".github" / "workflows"

# 只有開發/CI 才需要的東西(以及只因它們才被拉進來的傳遞依賴)
_DEV_ONLY = ("pytest", "pyyaml", "ruff", "pluggy", "iniconfig")


def _pins(lock: Path) -> dict:
    """lock 檔裡的 `名字==版本`。"""
    out = {}
    for line in lock.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Za-z0-9._-]+)==([^ \\]+)", line)
        if m:
            out[m.group(1).lower().replace("_", "-")] = m.group(2)
    return out


def _executable(script: str) -> str:
    """只留下會被 shell 執行的行 —— 註解在說明「不要做什麼」時會帶到關鍵字。"""
    return "\n".join(ln for ln in str(script or "").splitlines()
                     if not ln.lstrip().startswith("#"))


def _wf(name: str) -> dict:
    return yaml.safe_load((_WF_DIR / name).read_text(encoding="utf-8"))


def test_the_production_lock_carries_nothing_the_app_does_not_import():
    prod = _pins(_ROOT / "requirements.lock")
    assert prod, "生產 lock 解析不出任何 pin —— 這條測試會變成空集合真空通過"
    for pkg in _DEV_ONLY:
        assert pkg not in prod, f"{pkg} 不該進 production 環境:{sorted(prod)[:5]}…"
    # 而且它還是有東西(不要因為某次編譯壞掉而變成一份空 lock 卻仍然「通過」)
    for must in ("yfinance", "pandas", "requests", "trafilatura"):
        assert must in prod, must


def test_the_source_file_is_the_one_that_changed_not_only_the_lock():
    """lock 是編譯產物;真正要維護的是 requirements.txt。"""
    src = (_ROOT / "requirements.txt").read_text(encoding="utf-8")
    requested = [ln.split("#")[0].strip().lower() for ln in src.splitlines()
                 if ln.strip() and not ln.strip().startswith("#")]
    for pkg in _DEV_ONLY:
        assert not any(r.startswith(pkg) for r in requested), (pkg, requested)


def test_the_dev_lock_pins_exactly_what_production_pins():
    """**這是整個分層的重點**:CI 必須跑在 production 真正安裝的那些版本上。

    如果兩份 lock 各自解析,共用套件遲早會分岔(實測過 certifi 兩個版本),
    那時候測試綠燈證明的是另一個環境。`-c requirements.lock` 讓這件事由編譯
    保證;這條測試驗的是保證真的成立 —— 驗的是兩份 lock 的**實際 pin**,
    不是那句話。
    """
    # 來源檔引 requirements.txt(這樣本機才裝得起來 —— lock 的 hash 只涵蓋
    # Linux wheel),版本對齊改由編譯時的 `-c requirements.lock` 保證。
    dev_src = (_ROOT / "requirements-dev.txt").read_text(encoding="utf-8")
    assert re.search(r"^-r\s+requirements\.txt\s*$", dev_src, flags=re.M), dev_src
    refresher = (_WF_DIR / "lock-refresh.yml").read_text(encoding="utf-8")
    compile_block = refresher[refresher.index("Recompile locks"):
                              refresher.index("Detect changes")]
    dev_cmd = compile_block[compile_block.index("requirements-dev.txt"):]
    dev_cmd = dev_cmd[:dev_cmd.index("-o requirements-dev.lock")]
    assert "-c requirements.lock" in dev_cmd, \
        f"dev lock 沒有以生產 lock 為約束編譯,版本遲早分岔:{dev_cmd!r}"

    prod = _pins(_ROOT / "requirements.lock")
    dev = _pins(_ROOT / "requirements-dev.lock")
    assert dev, "dev lock 解析不出任何 pin"
    drift = {k: (v, dev.get(k)) for k, v in prod.items() if dev.get(k) != v}
    assert not drift, f"CI 會跑在和 production 不同的版本上:{drift}"
    # 反過來:dev lock 多出來的就該是測試工具那一組
    extra = set(dev) - set(prod)
    assert extra, "dev lock 沒有多出任何東西 —— 那它不是 dev lock"
    assert set(_DEV_ONLY) <= extra, sorted(extra)


def _jobs_with_steps():
    for path in sorted(_WF_DIR.glob("*.yml")):
        wf = yaml.safe_load(path.read_text(encoding="utf-8"))
        for job_name, job in (wf.get("jobs") or {}).items():
            yield path.name, job_name, (job.get("steps") or [])


def test_every_job_that_runs_pytest_installs_something_that_provides_it():
    """pytest 已經不在生產 lock 裡了 —— 忘了改的那個 job 會在**執行時**才炸。

    最貴的是 morning-report-b 的 schema 契約:它決定 state 發不發佈,而它跑在
    信寄出之後,所以壞掉的話不會有人從「信沒收到」發現。
    """
    seen = 0
    for wf_name, job_name, steps in _jobs_with_steps():
        installed = ""
        for step in steps:
            run = _executable(step.get("run") or "")
            installed += "\n" + run
            if not re.search(r"(^|[^\w-])(python[3]? -m )?pytest\b", run):
                continue
            seen += 1
            ok = ("requirements-dev.lock" in installed
                  or re.search(r"pip install [^\n]*\bpytest\b", installed))
            assert ok, f"{wf_name}:{job_name} 跑 pytest 卻沒裝它"
    assert seen >= 3, f"只找到 {seen} 個跑 pytest 的 job —— 掃描條件可能失準了"


def test_the_app_itself_runs_on_the_production_lock():
    """晨報那一步之前只能裝執行期依賴;測試工具是信寄出之後才進來的。"""
    steps = _wf("morning-report-b.yml")["jobs"]["send-report"]["steps"]
    app_i = next(i for i, s in enumerate(steps)
                 if "morning_report.py" in _executable(s.get("run") or ""))
    before = "\n".join(_executable(s.get("run") or "") for s in steps[:app_i])
    assert "requirements.lock" in before, "晨報跑之前要先裝生產 lock"
    assert "requirements-dev.lock" not in before, \
        "測試工具在晨報執行之前就裝進去了 —— 那就沒有分層可言"
    after = "\n".join(_executable(s.get("run") or "") for s in steps[app_i + 1:])
    assert "requirements-dev.lock" in after, "契約要跑 pytest,總得有人裝"


def test_every_lock_file_is_registered_with_the_refresher():
    """新增一份 lock 卻忘了登記,它就會**永遠停在建立當天的版本**而沒人發現。"""
    locks = sorted(p.name for p in _ROOT.glob("requirements*.lock"))
    assert len(locks) >= 5, locks
    src = (_WF_DIR / "lock-refresh.yml").read_text(encoding="utf-8")
    compile_block = src[src.index("Recompile locks"):src.index("Detect changes")]
    add_line = src[src.index("git add requirements"):][:400]
    for name in locks:
        if name == "requirements-uvtool.lock":
            # 建置工具本身:由 resolve job 以 --require-hashes 安裝
            assert "--require-hashes -r requirements-uvtool.lock" in src
        assert f"-o {name}" in compile_block, f"{name} 沒被重新編譯"
        assert name in add_line, f"{name} 不會被 commit 進 PR"


def test_the_documented_local_setup_can_actually_run_the_tests():
    """**守衛原本只盯 GitHub Actions**(Codex 2026-09-04 r1 P2)。

    README 第七節是新環境唯一的入口。分層之後它仍寫著
    `pip install -r requirements.txt` 的話,照做的人會發現 pytest 不存在 ——
    而這條路徑沒有任何 CI 會替他先撞到。
    """
    readme = (_ROOT / "README.md").read_text(encoding="utf-8")
    i = readme.index("## 七、本地測試")
    block = readme[i:readme.index("```", readme.index("```", i) + 3)]
    assert "pytest" in block, "第七節不再是「本地測試」了 —— 這條守衛失去了對象"
    installs = re.findall(r"pip install ([^\n]+)", block)
    assert installs, f"第七節沒有安裝指令:{block}"
    dev_only = {"pytest", "pyyaml", "ruff"}
    for line in installs:
        line_l = line.lower()
        if "requirements-dev.txt" in line_l or "requirements-dev.lock" in line_l:
            dev_only.clear()
            break
        dev_only -= {p for p in list(dev_only) if p in line_l}
    assert not dev_only, f"照 README 裝完仍然缺:{sorted(dev_only)}"


def test_the_commit_script_cannot_stage_half_the_change():
    """**硬編碼的 stage 清單漏檔不會報錯**(Codex 2026-09-04 r1 P2)。

    `commit_push.bat` 原本逐一列出檔名,那份清單早就漏了幾十個模組與多數
    workflow;這批再加上 requirements-dev.* 的話,它會安安靜靜 commit 半套,
    而 CI 隨即引用一份 repo 裡不存在的 lock。列舉法換成排除法。
    """
    bat = (_ROOT / "commit_push.bat").read_text(encoding="utf-8", errors="replace")
    adds = [ln.strip() for ln in bat.splitlines()
            if ln.strip().lower().startswith("git add")]
    assert adds, "提交腳本不再 stage 任何東西?"
    assert all("-A" in a for a in adds), f"還在列舉檔名:{adds}"
    # state/ 仍要排除:本機 DRY_RUN 產生的版本推上去會蓋掉線上的記憶
    assert any("exclude)state" in a for a in adds), adds
