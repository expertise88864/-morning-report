# -*- coding: utf-8 -*-
"""**發佈 state 到 repo 的原語** —— 只用標準函式庫。

從 `morning_report` 抽出(外審 2026-09-04 P2:production job 的 git 寫入
憑證存活時間太長)。拆成 compute / publish 兩個 job 之後,**有寫入權限的
那個 job 不可以安裝或執行任何第三方套件** —— 否則權限分離只是換個地方
承擔同一個風險。這個模組因此只 import stdlib,發佈 job 可以直接
`python -c "import state_publish; ..."`,不必 `pip install`。

`morning_report` 仍 re-export 這裡的名字,既有呼叫端與測試不必改寫。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time


#: 收據在 repo 裡的路徑(git 用的 POSIX 相對路徑,與磁碟路徑分開)。
RECEIPT_REPO_PATH = "state/delivery_receipt.json"


#: 收據 push 的退避秒數(第 1 次失敗等 5 秒、第 2 次等 15 秒),與
#: `tools/push_state.sh` 相同;**有界**(總共 +20 秒),不得撞 job timeout。
#: 抽成常數是為了讓測試把它設成 (0, 0) 之後真的走過重試迴圈。
RECEIPT_PUSH_BACKOFF_SEC = (5, 15)


def publish_receipt_from_remote_base(local_file, *, cwd=None,
                                     branch: str = "main") -> bool:
    """把收據**單獨**推上 `branch`,完全不碰工作區的 HEAD / index / 檔案。

    r7 外審 P1:第一版是「`git add` 收據 → `git commit` → `push_committed_state()`」。
    那等於把兩種**相反**的持久性語意綁在同一個發佈原語上:

      * 整批 state —— **不可以**立刻發佈,要等 `test_state_schema_contract`
        通過(`STATE_PUSH_DEFERRED=1` 那道閘門的全部意義:信可以先寄,
        但壞掉的 state 不准進 main);
      * 寄送收據 —— **必須**立刻發佈,而且要能在後續 state 契約失敗時存活。

    `git push` 推的是分支 HEAD,而 git 不可能只推 C 不推它的祖先 B。
    所以只要收據 commit 的祖先裡有「尚未通過契約的 state commit」,
    收據就會**順帶把它帶上 main**,那道閘門就沒了。今天的呼叫順序剛好
    讓收據先發生 —— 但那是順序的巧合,不是原語的不變量:誰把 persist
    往前挪一點,保護就消失,而且不會有任何錯誤訊息。

    改用 plumbing 從 **origin/main 的樹** 直接長出一個只差收據那一個檔的
    commit,再 `push <sha>:main`。工作區 HEAD 是什麼完全不參與 ——
    這樣「發佈收據」與「發佈整批 state」在結構上就不可能互相夾帶。

    回傳 True = 真的推了;False = 內容與遠端相同,不需要推。
    """
    import tempfile

    def _git(*args, **kw):
        env = dict(os.environ, **kw.pop("env_extra", {}))
        # **編碼要明講**:`text=True` 在 Windows 走地區編碼(實測 gbk),
        # git 回顯中文 commit 訊息時解碼失敗 —— stdout 會變成 `None`
        # 而 returncode 仍是 0(錯得很安靜)。runner 是 UTF-8 才碰巧沒事。
        return subprocess.run(["git", *args], cwd=cwd, env=env,
                              capture_output=True,
                              encoding="utf-8", errors="replace",
                              timeout=kw.pop("timeout", 60), **kw)

    def _out(*args, **kw):
        r = _git(*args, **kw)
        if r.returncode != 0:
            raise RuntimeError(f"git {args[0]} 失敗: {r.stderr.strip()[:200]}")
        return r.stdout.strip()

    author = {"GIT_AUTHOR_NAME": "morning-report-bot",
              "GIT_AUTHOR_EMAIL": "actions@github.com",
              "GIT_COMMITTER_NAME": "morning-report-bot",
              "GIT_COMMITTER_EMAIL": "actions@github.com"}
    # **有界重試,與 `tools/push_state.sh` 同一政策**(全案審查 DL-5):
    # 先前 fetch→push 各只做一次。GitHub 暫時性 5xx(push_state.sh 檔頭記錄
    # 2026-08-13 實際發生過)或 fetch 與 push 之間有人推了 main(使用者推程式碼)
    # 就 non-fast-forward → 只留一個降級標籤。第二次機會是整批 state 的延後
    # push,但那條要先過契約 —— 而「契約失敗那天收據仍在」正是收據被獨立
    # 出來的理由;兩者同日發生時 origin/main 上沒有今天的任何證據,備援班
    # 就會再寄一封(收不回來)。每一輪都**重新 fetch 取新 base** 再長 commit:
    # non-fast-forward 的正解是換基底,不是重推同一個 sha。
    for _attempt in range(len(RECEIPT_PUSH_BACKOFF_SEC) + 1):
        _out("fetch", "--quiet", "origin", branch)
        base = _out("rev-parse", "FETCH_HEAD")
        blob = _out("hash-object", "-w", "--", str(local_file))
        # 遠端已經是同一份內容就不推(重複 commit 沒有意義,也省一次寫入)
        cur = _git("rev-parse", f"{base}:{RECEIPT_REPO_PATH}")
        if cur.returncode == 0 and cur.stdout.strip() == blob:
            print("[receipt] 遠端收據已是最新,不重複發佈")
            return False
        with tempfile.TemporaryDirectory() as tmp:
            idx = {"GIT_INDEX_FILE": os.path.join(tmp, "index")}
            _out("read-tree", base, env_extra=idx)
            _out("update-index", "--add", "--cacheinfo",
                 f"100644,{blob},{RECEIPT_REPO_PATH}", env_extra=idx)
            tree = _out("write-tree", env_extra=idx)
        commit = _out("commit-tree", tree, "-p", base, "-m",
                      "寄送收據 [skip ci]", env_extra=author)
        try:
            _out("push", "origin", f"{commit}:refs/heads/{branch}")
        except RuntimeError as e:
            if _attempt >= len(RECEIPT_PUSH_BACKOFF_SEC):
                raise
            nap = RECEIPT_PUSH_BACKOFF_SEC[_attempt]
            print(f"[receipt] push 失敗(第 {_attempt + 1} 次):{e} —— "
                  f"{nap}s 後以 origin/{branch} 當下為基底再試", file=sys.stderr)
            time.sleep(nap)
            continue
        print("[receipt] 已發佈寄送收據(獨立於整批 state"
              + (f",第 {_attempt + 1} 次才成功" if _attempt else "") + ")")
        return True
    raise RuntimeError("receipt push 重試迴圈沒有結論")   # 迴圈一定 return 或 raise


# ── 交棒清單的驗證 ────────────────────────────────────────────────────────
#
# **交棒檔是不可信輸入**(Codex 2026-09-04 r2 P1)。它由跑過第三方依賴的
# compute job 產生;而讀它的是**唯一有寫入權限**的發佈 job。第一版只在 shell
# 裡做 `case "$p" in state/*)` 的前綴比對 —— `state/../morning_report.py` 直接
# 通過,而 `git rm` 會把 `..` 正規化,於是那個 job 會刪掉並推送程式碼變更。
# 那正好是這次權限分離要消除的攻擊路徑,被我自己的修正重新打開。
#
# 判準因此改成**詞法正規化**,而且結果由 Python 交給 shell(shell 不再自己
# 判斷):絕對路徑、`.` / `..` 元件、反斜線、空元件一律拒絕;正規化後必須
# 仍在 `state/` 底下;刪除項還必須落在(同樣驗過的)白名單條目之下。
#: 可發佈的根目錄 —— 這個模組只允許動 state,不動程式碼。
PUBLISH_ROOT = "state"


class UnsafePublishPath(ValueError):
    """交棒清單裡出現不該出現的路徑。**拒絕整批**,不是跳過那一行 ——
    清單被動過手腳時,剩下幾行的可信度也已經沒了。"""


def normalize_repo_path(raw: str) -> str:
    """把交棒清單的一行轉成安全的 repo 相對路徑;不安全就 raise。"""
    p = str(raw or "").strip()
    if not p:
        raise UnsafePublishPath("空路徑")
    if "\\" in p or "\0" in p:
        raise UnsafePublishPath(f"路徑含反斜線或 NUL:{p!r}")
    if p.startswith("/") or (len(p) > 1 and p[1] == ":"):
        raise UnsafePublishPath(f"絕對路徑:{p!r}")
    parts = p.split("/")
    if any(part in ("", ".", "..") for part in parts):
        # 空元件(`a//b`)與 `.` / `..` 一律拒絕:正規化交給我們,不交給 git
        raise UnsafePublishPath(f"路徑含 . / .. 或空元件:{p!r}")
    norm = "/".join(parts)
    if norm != PUBLISH_ROOT and not norm.startswith(PUBLISH_ROOT + "/"):
        raise UnsafePublishPath(f"不在 {PUBLISH_ROOT}/ 底下:{p!r}")
    return norm


def validated_allowlist(lines) -> list:
    """白名單(`_state_push_paths()` 的輸出)—— 每一條都要在 `state/` 底下。"""
    out = []
    for ln in lines:
        if str(ln).strip():
            out.append(normalize_repo_path(ln))
    if not out:
        raise UnsafePublishPath("白名單是空的 —— 那不是「沒東西要發佈」,是清單壞了")
    return out


def _under(path: str, root: str) -> bool:
    return path == root or path.startswith(root + "/")


def validated_deletions(lines, allowlist) -> list:
    """刪除清單:正規化 + 必須落在白名單條目之下。空清單是合法的。"""
    allow = validated_allowlist(allowlist)
    out = []
    for ln in lines:
        if not str(ln).strip():
            continue
        p = normalize_repo_path(ln)
        if not any(_under(p, a) for a in allow):
            raise UnsafePublishPath(f"刪除項不在白名單之下:{p!r}")
        out.append(p)
    return out


def _cli(argv) -> int:
    """`python -m state_publish paths|deletions <白名單檔> [刪除檔]`。

    把**驗過的**路徑逐行印出來給 workflow 用 —— shell 不再自己判斷路徑安全,
    它只使用這裡的輸出;任何一條不合格就非零退出,整個發佈步驟隨之失敗。
    """
    if len(argv) < 3:
        print("用法: python -m state_publish paths|deletions <paths.txt> [deleted.txt]",
              file=sys.stderr)
        return 2
    mode, paths_file = argv[1], argv[2]
    allow_lines = open(paths_file, encoding="utf-8").read().splitlines()
    try:
        if mode == "paths":
            rows = validated_allowlist(allow_lines)
        elif mode == "deletions":
            del_lines = (open(argv[3], encoding="utf-8").read().splitlines()
                         if len(argv) > 3 and os.path.exists(argv[3]) else [])
            rows = validated_deletions(del_lines, allow_lines)
        else:
            print(f"未知模式:{mode}", file=sys.stderr)
            return 2
    except UnsafePublishPath as e:
        print(f"::error title=unsafe-publish-path::交棒清單被拒:{e}", file=sys.stderr)
        return 1
    print("\n".join(rows))
    return 0


if __name__ == "__main__":                      # pragma: no cover - CLI 由 workflow 用
    sys.exit(_cli(sys.argv))
