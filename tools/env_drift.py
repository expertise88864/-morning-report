# -*- coding: utf-8 -*-
"""**本機跑的版本要和 CI 一樣**(2026-09-04 的教訓)。

那天我交出三個 commit,本機 `preflight` 每一次都是綠的,而 CI 每一次都是
紅的。原因是 `@pytest.mark.slow` 沒有登記:本機的 pytest 9.0.3 放過它,
CI 的 9.1.1 擋下 —— 收集階段錯誤、退出碼 2。查的過程又發現本機 pandas
2.2.3 而 lock 是 3.0.3。

也就是說:**「本機測試綠」證明的是另一個環境的行為。** 一個本機閘門若跑在
和 CI 不同的版本上,它的綠燈是沒有保證的。

這支比對執行中的直譯器裝的版本與 `requirements-dev.lock` 釘的版本。
Python 的次版本另外報告 —— 那個修不了(這台機器只有 3.13),寫死成失敗會
讓閘門永遠紅,所以它是一句說出來的已知落差,不是靜靜不提。
"""
from __future__ import annotations

import re
import sys
from collections.abc import Mapping, Sequence
from typing import Any
from importlib import metadata
from pathlib import Path

# Windows 的主控台是 cp950:訊息裡的中文與符號編不進去會**直接拋例外**,
# 於是這道守衛會因為「印不出來」而失敗 —— 一個與它要量的事完全無關的原因。
for _stream in (sys.stdout, sys.stderr):
    _rc: Any = getattr(_stream, "reconfigure", None)
    if _rc is not None:
        try:
            _rc(encoding="utf-8", errors="replace")
        except ValueError:             # 被接管的串流(pytest capture 等)
            pass

_ROOT = Path(__file__).resolve().parents[1]
_PIN = re.compile(r"^([A-Za-z0-9._-]+)==([^\s\\]+)")

#: **lock 之外允許存在的東西,每一個都要有理由**(外審 2026-09-04 r1 P1)。
#:
#: 這份清單是量出來的(在對齊後的 `.venv` 裡跑 `importlib.metadata`),不是
#: 猜的。它同時管兩個方向:lock 有而本機沒裝不算漂移,本機有而 lock 沒有
#: 也不算。**往這裡加東西是一個決定**:每加一筆就等於把一個「CI 不會有、
#: 本機會有」的差異合法化,而那正是假綠燈的來源。
_ALLOWED_EXTRAS = {
    "pip": "venv 自己的引導套件(CI 用 runner 內建的 pip)",
    "setuptools": "venv 引導;3.12 起預設不裝,留著相容舊環境",
    "wheel": "venv 引導;某些舊版 pip 會需要它來建 wheel",
    # lock 是用 --python-platform linux 編的,所以不會有 Windows 專屬相依
    "colorama": "pytest 在 Windows 的相依(Linux 的 lock 裡不會有)",
    "tzdata": "pandas 在 Windows 的時區資料(Linux 由系統提供)",
}


def _norm(name: str) -> str:
    return name.lower().replace("_", "-")


def installed_all() -> dict[str, str]:
    """本機裝了什麼 → `{正規化名字: 版本}`。"""
    out = {}
    for dist in metadata.distributions():
        name = _norm((dist.metadata["Name"] or "").strip())
        if name:
            out[name] = dist.version
    return out


_Row = tuple[str, str | None, str | None]


def compare(want: Mapping[str, str], got: Mapping[str, str],
            allowed: Mapping[str, str] | None = None) -> tuple[list[_Row], list[_Row], list[_Row]]:
    """純函式:`(不一致, 沒裝, 多出來的)`。

    **「多出來的」是第三個方向,而它正是最容易被漏掉的那個**(外審 r1 P1)。
    一個殘留的 pytest plugin 可以自己註冊 marker、改變收集行為 —— 本機因此
    綠、CI 那個從 lock 全新建起來的環境紅。這就是這一批要消滅的那一類假綠燈,
    只看「lock 裡的每一筆對不對」永遠看不到它。
    """
    allowed = _ALLOWED_EXTRAS if allowed is None else allowed
    wrong: list[_Row] = []
    missing: list[_Row] = []
    extra: list[_Row] = []
    for name, expect in sorted(want.items()):
        actual = got.get(name)
        if actual is None:
            if name not in allowed:
                missing.append((name, expect, None))
        elif actual != expect:
            wrong.append((name, expect, actual))
    for name, actual in sorted(got.items()):
        if name not in want and name not in allowed:
            extra.append((name, None, actual))
    return wrong, missing, extra


def pins(lock: Path) -> dict[str, str]:
    out = {}
    for line in lock.read_text(encoding="utf-8").splitlines():
        m = _PIN.match(line)
        if m:
            out[_norm(m.group(1))] = m.group(2)
    return out


def drift(lock: Path) -> tuple[list[_Row], list[_Row], list[_Row]]:
    """→ `(不一致, 沒裝, 多出來的)`,都是 `[(套件, 期望, 實際)]`。"""
    return compare(pins(lock), installed_all())


def ci_python_version(workflow: Path) -> str | None:
    """CI 用哪個 Python —— 從 workflow 讀,不要寫死。"""
    m = re.search(r'python-version:\s*"?([0-9.]+)"?', workflow.read_text(encoding="utf-8"))
    return m.group(1) if m else None


def main(argv: Sequence[str] = ()) -> int:
    lock = _ROOT / "requirements-dev.lock"
    if not lock.exists():
        print(f"[env] 找不到 {lock.name} —— 無法確認本機與 CI 是否一致", file=sys.stderr)
        return 1
    wrong, missing, extra = drift(lock)

    want_py = ci_python_version(_ROOT / ".github" / "workflows" / "ci.yml")
    got_py = ".".join(str(x) for x in sys.version_info[:2])
    if want_py and not got_py.startswith(want_py):
        # **說出來,但不擋**:這台機器只有 3.13,寫死成失敗等於閘門永遠紅。
        # 這是一個已知落差,不是一件沒人提的事。
        print(f"[env] ⚠ 本機 Python {got_py},CI 是 {want_py} —— "
              "版本相關的行為差異仍然量不到(要裝 3.11 才補得起來)")

    if not wrong and not missing and not extra:
        print(f"[env] ✅ 本機套件版本與 {lock.name} 一致(Python {got_py})")
        return 0

    print("=" * 68, file=sys.stderr)
    print("[env] 本機裝的版本與 CI 會裝的不一樣 —— 這裡的綠燈證明的是另一個環境:",
          file=sys.stderr)
    for name, want, got in wrong:
        print(f"    {name}: lock {want} / 本機 {got}", file=sys.stderr)
    for name, want, _ in missing:
        print(f"    {name}: lock {want} / 本機沒裝", file=sys.stderr)
    for name, _want, got in extra:
        print(f"    {name} {got}: lock 裡沒有這個東西 —— CI 的環境不會有它",
              file=sys.stderr)
    print("修法:bash tools/sync_dev_env.sh", file=sys.stderr)
    print("=" * 68, file=sys.stderr)
    return 1


if __name__ == "__main__":                  # pragma: no cover - CLI 由 preflight 用
    sys.exit(main(sys.argv))
