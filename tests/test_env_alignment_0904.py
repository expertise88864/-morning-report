# -*- coding: utf-8 -*-
"""**本機閘門要跑在 CI 會跑的版本上**(2026-09-04)。

那天我交出三個 commit,本機 `preflight` 每一次都綠,CI 每一次都紅。
`@pytest.mark.slow` 沒登記,本機的 pytest 9.0.3 放過、CI 的 9.1.1 擋下。
查的過程又量到 pandas 本機 2.2.3 / lock 3.0.3。也就是說本機的綠燈證明的是
**另一個環境**的行為 —— 一個跑在不同版本上的閘門,綠燈是沒有保證的。

修法是 repo 自己一個 `.venv`(用 lock 釘的版本),而 preflight 用它。
全域的 site-packages 不能動:它和別的專案共用,那天我把全域升上去就
連帶違反了另外兩個專案的約束(ortools 要 protobuf<6.34、opencv 要
numpy<2.3)—— 對齊一個專案不該是弄壞另一個專案的方式。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import env_drift as ed                     # noqa: E402

_ROOT = Path(__file__).resolve().parents[1]
_PREFLIGHT = (_ROOT / "tools" / "preflight.sh").read_text(encoding="utf-8")


def _code(text: str) -> str:
    """只看可執行的行 —— 註解裡會提到它在解釋的那個東西。"""
    return "\n".join(ln for ln in text.splitlines()
                     if ln.strip() and not ln.lstrip().startswith("#"))


def test_a_version_mismatch_is_detected(tmp_path):
    lock = tmp_path / "l.lock"
    # pytest 一定裝了(不然這一輪跑不起來);故意釘一個不可能的版本
    lock.write_text("pytest==0.0.1 \\\n    --hash=sha256:x\n", encoding="utf-8")
    wrong, missing, _extra = ed.drift(lock)
    assert wrong and wrong[0][0] == "pytest", (wrong, missing)
    assert not missing


def test_a_missing_package_is_detected(tmp_path):
    lock = tmp_path / "l.lock"
    lock.write_text("nosuchpkg-zzz==1.2.3\n", encoding="utf-8")
    wrong, missing, _extra = ed.drift(lock)
    assert missing and missing[0][0] == "nosuchpkg-zzz", (wrong, missing)


def test_the_real_lock_parses_into_something(tmp_path):
    """空集合真空通過:解析壞掉的話「沒有漂移」會變成永遠成立。"""
    pins = ed.pins(_ROOT / "requirements-dev.lock")
    assert len(pins) > 20, len(pins)
    assert pins.get("pytest") and pins.get("pandas")


def test_the_expected_python_comes_from_the_workflow():
    """CI 用哪個 Python 是**讀出來的**,不是寫死的 —— 寫死的話 CI 換版本這條
    守衛會繼續拿舊值比對,而它的全部意義就是對得上。"""
    got = ed.ci_python_version(_ROOT / ".github" / "workflows" / "ci.yml")
    assert got and got[0].isdigit(), got
    ci = (_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert f'"{got}"' in ci


def test_preflight_uses_the_repo_venv_and_checks_first():
    code = _code(_PREFLIGHT)
    assert ".venv/Scripts" in code and ".venv/bin" in code, \
        "preflight 沒有用 repo 自己的 .venv —— 那它跑的是全域那一組版本"
    assert "export PATH=" in code, \
        "要用 PATH 前置:底下三條指令必須與 ci.yml 逐字相同,不能改寫成 $PY"
    i_env = code.index("tools/env_drift.py")
    for later in ("compileall", "ruff", "pytest"):
        assert i_env < code.index(later), f"環境檢查要排在 {later} 之前"


def test_the_failure_message_points_at_a_script_that_exists():
    """守衛要能修:一句「版本不一樣」而沒有下一步,下場是被關掉。"""
    src = (_ROOT / "tools" / "env_drift.py").read_text(encoding="utf-8")
    assert "tools/sync_dev_env.sh" in src
    sync = _ROOT / "tools" / "sync_dev_env.sh"
    assert sync.exists()
    body = _code(sync.read_text(encoding="utf-8"))
    assert "requirements-dev.lock" in body and "venv" in body
    # **不可以裝到全域去**:那正是弄壞別的專案的那條路。逐行判定 ——
    # 「pip install 前面幾百字內有 .venv」分不出勝負(上面建 venv 那段就有)。
    installs = [ln for ln in body.splitlines() if "pip install" in ln]
    assert installs, "sync 腳本沒有在裝任何東西"
    for ln in installs:
        assert "$PY" in ln, f"這一行會裝到共用的全域 site-packages:{ln.strip()}"
    assert "$PY" in body.split("=")[0] or "PY=" in body, "沒有指到 venv 的直譯器"


def test_the_drift_check_can_actually_print_its_message(capsys):
    """Windows 的主控台是 cp950:訊息編不進去會直接拋例外,守衛就會因為
    **與它要量的事無關的原因**而失敗(第一版當場踩到)。"""
    src = (_ROOT / "tools" / "env_drift.py").read_text(encoding="utf-8")
    assert "reconfigure(encoding=" in src
    assert 'errors="replace"' in src


def test_a_package_the_lock_does_not_have_is_reported():
    """**第三個方向**(外審 2026-09-04 r1 P1):`.venv` 裡多出來的東西。

    只檢查「lock 裡的每一筆對不對」看不到它。一個殘留的 pytest plugin 可以
    自己註冊 marker、改變收集行為 —— 本機因此綠,而 CI 那個從 lock 全新建
    起來的環境紅。那正是這整套機制要消滅的假綠燈,只是換一個入口。
    """
    want = {"pytest": "9.1.1"}
    got = {"pytest": "9.1.1", "pytest-randomly": "3.15.0"}
    wrong, missing, extra = ed.compare(want, got)
    assert not wrong and not missing
    assert [e[0] for e in extra] == ["pytest-randomly"], extra


def test_the_bootstrap_and_platform_packages_are_not_false_alarms():
    """誤報太多的守衛會被關掉:venv 引導與 Windows 專屬相依不算漂移。"""
    want = {"pytest": "9.1.1"}
    got = {"pytest": "9.1.1", "pip": "24.3.1", "colorama": "0.4.6", "tzdata": "2026.3"}
    wrong, missing, extra = ed.compare(want, got)
    assert not (wrong or missing or extra), (wrong, missing, extra)


def test_every_allowed_extra_has_a_written_reason():
    """**往白名單加東西是一個決定**:每一筆都等於把一個「CI 不會有、本機會有」
    的差異合法化。沒有理由的話,這份清單遲早變成漏報的藏身處。"""
    assert ed._ALLOWED_EXTRAS, "白名單空了 —— 那上面兩條測試在守什麼?"
    for name, why in ed._ALLOWED_EXTRAS.items():
        assert isinstance(why, str) and len(why) >= 8, (name, why)


def test_sync_rebuilds_the_environment_instead_of_topping_it_up():
    """沿用既有的 venv 只會裝上/升級,**不會移除**已經從 lock 拿掉的東西。"""
    body = _code((_ROOT / "tools" / "sync_dev_env.sh").read_text(encoding="utf-8"))
    assert "rm -rf .venv" in body, "沒有重建 —— 殘留的套件會一直留著"
    assert body.index("rm -rf .venv") < body.index("python -m venv"), body
