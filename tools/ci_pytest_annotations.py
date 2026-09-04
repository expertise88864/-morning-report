# -*- coding: utf-8 -*-
"""把 pytest 的失敗轉成 GitHub Actions annotation。

**為什麼需要這支**:job log 要 repo admin 權限才讀得到,而 annotation 是
公開的。2026-09-04 我碰到 CI 連三個 commit 紅、退出碼 2,而唯一讀得到的
訊息是「Process completed with exit code 2」—— 於是只能用猜的,猜了四輪。
猜測的成本遠高於這支 40 行的腳本。

用法(在 `if: always()` 的步驟裡):

    python tools/ci_pytest_annotations.py pytest-report.xml

**這支自己永遠 exit 0**:job 的紅綠由 pytest 那一步決定,診斷工具不該有
能力改變結論 —— 它壞掉的時候應該是「少了說明」,不是「多了一次失敗」或
「把失敗蓋成成功」。
"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from collections.abc import Iterator, Sequence
from pathlib import Path

#: annotation 的訊息會被截斷,留下最有用的頭尾各一段
_HEAD = 1500
_TAIL = 700


def _esc(s: str) -> str:
    """GitHub workflow 指令的跳脫:換行與 `%` 都要編碼,否則訊息會斷在第一行。"""
    return (str(s).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A"))


def _shorten(s: str) -> str:
    s = str(s or "").strip()
    if len(s) <= _HEAD + _TAIL:
        return s
    return s[:_HEAD] + "\n…(中略)…\n" + s[-_TAIL:]


def _file_of(case: ET.Element) -> str:
    """testcase → repo 相對路徑。

    **實測過三種形狀**(2026-09-04,pytest 9;外審 r1 指出我原本猜錯了):

        一般失敗   file="tests/test_a.py"(Windows 上是反斜線) classname="tests.test_a"
        收集錯誤   file="tests/test_c.py"  classname=""        name="tests.test_c"
        沒有 file  (xunit2 家族;`pytest.ini` 已釘 legacy,這是保險絲)

    最後那種只能從 dotted 名字還原 —— `tests.test_a` → `tests/test_a.py`。
    少了 `.py` 的路徑 annotation 會指不到地方,所以副檔名要補回去。
    """
    f = (case.get("file") or "").strip().replace("\\", "/")
    if f:
        return f
    dotted = (case.get("classname") or "").strip() or (case.get("name") or "").strip()
    if not dotted:
        return ""
    # classname 對一般測試是模組;對 class 內的測試會多一層,取到模組為止
    parts = [p for p in dotted.split(".") if p]
    while parts and not parts[-1].startswith("test"):
        parts.pop()
    return "/".join(parts) + ".py" if parts else ""


def iter_annotations(xml_path: Path) -> Iterator[tuple[str, str, str, str]]:
    """讀 junit XML → 逐筆 `(檔, 行, 標題, 訊息)`。"""
    root = ET.parse(xml_path).getroot()
    for case in root.iter("testcase"):
        for bad in list(case.findall("failure")) + list(case.findall("error")):
            file = _file_of(case)
            # junit 的 line 是 0-based;annotation 是 1-based。收集錯誤沒有 line
            # (整個模組都掛了,沒有哪一行特別對) —— 指到第 1 行。
            raw = case.get("line")
            try:
                line = str(int(raw) + 1) if raw is not None else "1"
            except (TypeError, ValueError):
                line = "1"
            title = f"{case.get('classname') or ''}::{case.get('name') or ''}".strip(":")
            body = (bad.get("message") or "") + "\n" + (bad.text or "")
            yield file, line, title, _shorten(body)


def main(argv: Sequence[str]) -> int:
    if len(argv) < 2:
        print("::error title=ci-annotations::用法: ci_pytest_annotations.py <junit.xml>")
        return 0
    xml_path = Path(argv[1])
    if not xml_path.exists():
        # **沒有報告不等於沒有失敗**:pytest 在寫出報告之前就死掉(例如
        # usage error)也會走到這裡,那正是最需要被說出來的情況之一。
        print(f"::error title=ci-annotations::找不到 {xml_path} —— "
              "pytest 可能在產生報告之前就結束了(usage error / 直譯器層級的錯誤)")
        return 0
    try:
        rows = list(iter_annotations(xml_path))
    except ET.ParseError as e:
        print(f"::error title=ci-annotations::{_esc(f'報告解析不動:{e}')}")
        return 0
    if not rows:
        # 沒有失敗筆數卻走到這裡:pytest 那一步若是紅的,原因不在測試本身
        # (例如 session hook 改了退出碼)。這句話本身就是線索。
        print("::notice title=ci-annotations::junit 報告裡沒有 failure/error")
        return 0
    for file, line, title, body in rows:
        print(f"::error file={file},line={line},title={_esc(title)}::{_esc(body)}")
    print(f"::notice title=ci-annotations::共 {len(rows)} 筆失敗已轉為 annotation")
    return 0


if __name__ == "__main__":                  # pragma: no cover - CLI 由 workflow 用
    sys.exit(main(sys.argv))
