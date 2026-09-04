# -*- coding: utf-8 -*-
"""**型別檢查從邊界開始,而且是一份只會變長的清單**(外審 2026-09-04 P3)。

外審說 typing 覆蓋不足,建議從邊界導入 mypy。26,000 行的主模組一次全開會
得到一份沒有人會看的錯誤清單 —— 而一個沒人看的檢查器等於沒有檢查器。
所以 `mypy.ini` 列的是已經**逐行補完註記、`--strict` 零錯誤**的模組:

    state_publish.py                 發佈原語(唯一有寫入權限的 job 在跑)
    state_store.py                   state 讀取(corrupt ≠ missing 那條邊界)
    degradation_registry.py          降級標籤的登記
    tools/env_drift.py               本機與 CI 的版本比對
    tools/ci_pytest_annotations.py   CI 失敗的轉譯

共同點是「邊界」:跨行程、跨檔案、跨 CI 的介面 —— 型別搞錯的代價最高,
而單元測試最不容易涵蓋。**加一個模組進來的方式是把它補到零錯誤,不是把
規則放寬。** 這個檔擋住清單變短。

另外記一件實測到的事:`mypy.ini` 只能放 ASCII。mypy 用**地區編碼**讀設定檔,
在 cp950/gbk 的主控台上,這個 repo 到處都是的中文註解會讓它在檢查任何一行
之前就 UnicodeDecodeError —— 本機壞、CI(UTF-8)好,又是一個同型的落差。
"""
import configparser
import subprocess
import sys
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[1]
_CFG = _ROOT / "mypy.ini"

#: 2026-09-04 首批補完的邊界模組。**記的是身分,不是數量**(外審 r1 P2):
#: 只斷言「至少五個」的話,把 state_publish.py 換成別的檔照樣通過 ——
#: 承諾過的那條邊界靜靜失去涵蓋,而後面那條「真的跑 mypy」只驗改過的清單,
#: 也會一起通過。這是「方便的述詞不等於那個狀態」的又一次。
_BASELINE = frozenset({
    "state_publish.py",
    "state_store.py",
    "degradation_registry.py",
    "tools/env_drift.py",
    "tools/ci_pytest_annotations.py",
})


def _files() -> list:
    cp = configparser.ConfigParser()
    cp.read(_CFG, encoding="utf-8")
    raw = cp.get("mypy", "files", fallback="")
    return [x.strip().rstrip(",") for x in raw.replace("\n", " ").split() if x.strip(", ")]


def test_the_boundary_list_only_grows():
    files = _files()
    assert len(files) == len(set(files)), f"mypy.ini 有重複的項目:{files}"
    lost = sorted(_BASELINE - set(files))
    assert not lost, (
        f"這些模組被移出型別檢查:{lost}\n"
        "把模組拿掉不是修法 —— 修法是把它補到零錯誤。")
    for rel in files:
        assert (_ROOT / rel).exists(), f"mypy.ini 列了一個不存在的檔:{rel}"


def test_strictness_is_not_quietly_relaxed():
    cp = configparser.ConfigParser()
    cp.read(_CFG, encoding="utf-8")
    assert cp.getboolean("mypy", "strict", fallback=False), \
        "strict 被關掉了 —— 那這份清單通過與否就不代表什麼了"
    # CI 用哪個 Python 是讀出來的:兩邊對同一段程式碼要得到同一個結論
    ci = yaml.safe_load((_ROOT / ".github" / "workflows" / "ci.yml")
                        .read_text(encoding="utf-8"))
    step = next(s for s in ci["jobs"]["test"]["steps"]
                if "setup-python" in str(s.get("uses") or ""))
    assert cp.get("mypy", "python_version") == str((step.get("with") or {})["python-version"])


def test_the_config_stays_readable_under_a_non_utf8_locale():
    """mypy 用地區編碼讀設定檔:中文註解會讓它在檢查任何一行之前就死掉。"""
    raw = _CFG.read_bytes()
    bad = [i for i, b in enumerate(raw) if b > 0x7F]
    assert not bad, f"mypy.ini 有非 ASCII 位元組(位置 {bad[:5]})—— cp950 上會 UnicodeDecodeError"


def test_both_gates_actually_run_it():
    """沒有接上去的話,這整個檔在守一個不會執行的東西。"""
    cmd = "python -m mypy --config-file mypy.ini"
    ci = yaml.safe_load((_ROOT / ".github" / "workflows" / "ci.yml")
                        .read_text(encoding="utf-8"))
    runs = [str(s.get("run") or "").strip() for s in ci["jobs"]["test"]["steps"]]
    assert cmd in runs, f"CI 沒有跑型別檢查:{runs}"
    pf = (_ROOT / "tools" / "preflight.sh").read_text(encoding="utf-8")
    code = "\n".join(ln for ln in pf.splitlines()
                     if ln.strip() and not ln.lstrip().startswith("#"))
    assert cmd in code, "本機閘門沒有跑型別檢查 —— 那會變成只有 push 之後才知道"


def test_the_listed_modules_really_pass_today():
    """**真的跑一次。** 「設定檔長這樣」不等於「它會通過」;而這條測試若因為
    別的原因跑不動(例如 mypy 沒裝),要說出來而不是靜靜跳過。"""
    r = subprocess.run([sys.executable, "-m", "mypy", "--config-file", str(_CFG)],
                       cwd=str(_ROOT), capture_output=True, encoding="utf-8",
                       errors="replace", timeout=600)
    assert "No module named mypy" not in (r.stderr or ""), \
        "mypy 沒裝 —— 它在 requirements-dev.lock 裡,跑 bash tools/sync_dev_env.sh"
    assert r.returncode == 0, (r.stdout or "") + (r.stderr or "")
