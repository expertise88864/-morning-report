#!/usr/bin/env python3
"""GPT-6.1-Sol (Codex CLI) diff review gate with explicitly marked quota deferral.

2026-10-15 使用者定案:改為**每個 PR review 一次**(`tools/pr_review.py`),不再逐 commit /
逐 push 審查;pre-commit、commit-msg hook 已移除,`_delivery.py` 也不再呼叫本閘門。仍在使用的
入口:`which`(`tools/codex_review.sh` 選 CLI)、`verify-session`(證明 PR review 實際模型)、
`pending`(列出切換前帶 pending trailer 的歷史 commit,以便補審;結果記在 PR,不再寫 audit commit)。
下方其餘內容是 2026-10-14 逐 commit 閘門的歷史說明。

使用者 2026-10-14 定案:**之後任何 diff review 一律改由 gpt-6.1-sol / high 執行**;
額度不足時標記 pending、照常 commit+push,額度恢復後補審,審查意見逐條判斷是否採用。
既有 Claude Opus 5.5 閘門(`tools/claude_diff_review.py`)保留,只用來消化切換前
已經排進 Claude 佇列的歷史 pending 補審。

這支只替換三件事,其餘(git 證據、fingerprint、pending 佇列、hook 契約)與 Claude
閘門共用**同一份程式碼**,不另抄一份 —— 兩份會分歧,而分歧的症狀是兩道閘門對同一個
diff 給出不同的「審過了沒」:

1. 審查後端:Codex CLI,`--sandbox read-only`、`--ignore-user-config`、關閉 web search
   與 apps;prompt 走 stdin,diff 只以「機器產生的證據快照」檔案路徑交給審查者讀。
2. 模型證據:Codex 的 JSON 事件流不含模型名。證據取自該次執行自己的 session rollout
   (`$CODEX_HOME/sessions/**/rollout-*-<thread_id>.jsonl`)裡的 `turn_context`:
   每一個回合都必須是 `gpt-6.1-sol` / `high` / read-only sandbox。**證明不了就 fail
   closed**,不得把「review 沒跑成」當成通過,也不得 fallback 到其他模型。
3. trailer 名稱:`GPT-6-1-Sol-Review` / `-Effort` / `-Reviewed-Commit`。

CLI 版本:gpt-6.1-sol 需要較新的 Codex CLI。2026-10-14 實測 npm 全域的 0.155.1 回
「model is not supported when using Codex with a ChatGPT account」,而 Codex 桌面版內附的
0.162.0-alpha.17.2 可用。所以這裡會在 `GPT_REVIEW_CODEX_BIN`、桌面版內附目錄、PATH
之中挑**版本最高**的那一支;挑到的 CLI 不認得這個模型時,錯誤會讓閘門擋下而不是降級。

只用標準函式庫:hooks 可能在專案 venv 建立之前就執行。
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import glob
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools import claude_diff_review as _base  # noqa: E402
from tools import claude_review_queue as queue  # noqa: E402

# ---- 與 Claude 閘門共用的 git 證據與佇列協定(同一份實作) -------------------
ReviewError = _base.ReviewError
QuotaUnavailable = _base.QuotaUnavailable
PushRange = _base.PushRange
_git_bytes = _base._git_bytes
_try_git_bytes = _base._try_git_bytes
_empty_tree = _base._empty_tree
_outgoing_commits = _base._outgoing_commits
_atomic_text = _base._atomic_text
worktree_fingerprint = _base.worktree_fingerprint
staged_fingerprint = _base.staged_fingerprint
push_ranges = _base.push_ranges
push_fingerprint = _base.push_fingerprint

# ---- 這道閘門自己的身分 -----------------------------------------------------
STATE_DIR = ROOT / ".gpt-review"
MODEL = "gpt-6.1-sol"
# 使用者 2026-10-14 定案兩次:先 high,同日改 xhigh(不降級、不 fallback)
EFFORT = "xhigh"
#: 佇列認得的 effort trailer:切換 xhigh 前已排入的 high 仍是待審(見 queue.accepted_efforts)
ACCEPTED_TRAILER_EFFORTS = ("xhigh", "high")
PENDING_PATH = STATE_DIR / "pending_review.json"
# trailer key 裡**不能有 `.`**:`GPT-6.1-Sol-Review: pending` git interpret-trailers 不認(實測
# 解析出 0 行),佇列會把內容 commit 判成「沒排進去」而擋下。所以版本號用連字號寫。
TRAILER_STATUS = "GPT-6-1-Sol-Review"
TRAILER_EFFORT = "GPT-6-1-Sol-Review-Effort"
TRAILER_REVIEWED = "GPT-6-1-Sol-Reviewed-Commit"
TRAILER_RESOLVED_BY = "GPT-6-1-Sol-Resolved-By"
#: 切換前的 Claude 佇列:它的空 audit 在 push 時仍被接受(見 queue.accepted_outgoing)
LEGACY_GATE = _base
LOG_PREFIX = "[gpt-review]"

_VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?")
_QUOTA_MARKERS = (
    "usage limit", "rate limit", "rate_limit", "too many requests", "quota",
    "hit your limit", "try again at", "status\":429", "status_code\":429",
    "status 429", "insufficient_quota",
)


# ---------------------------------------------------------------------------
# Codex CLI 的選擇
# ---------------------------------------------------------------------------
def _version_key(text: str) -> tuple:
    """`codex-cli 0.162.0-alpha.17.2` → 可比較的鍵;正式版排在同號 pre-release 之後。"""
    m = _VERSION.search(text or "")
    if not m:
        return (-1,)
    major, minor, patch, pre = m.groups()
    pre_key = (1, ()) if not pre else (0, tuple(
        (0, int(p), "") if p.isdigit() else (1, 0, p) for p in pre.split(".")))
    return (int(major), int(minor), int(patch), pre_key)


def _cli_version(path: str) -> str:
    try:
        proc = subprocess.run([path, "--version"], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=60, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return (proc.stdout or proc.stderr or "").strip().splitlines()[0] if proc.returncode == 0 else ""


def codex_candidates() -> list[str]:
    """所有找得到的 Codex CLI:明示的環境變數優先,其次桌面版內附目錄,最後 PATH。"""
    found: list[str] = []
    explicit = os.environ.get("GPT_REVIEW_CODEX_BIN", "").strip()
    if explicit:
        found.append(explicit)
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        found.extend(sorted(glob.glob(os.path.join(local, "OpenAI", "Codex", "bin", "*", "codex.exe"))))
    for name in (("codex.cmd", "codex.exe", "codex") if os.name == "nt" else ("codex",)):
        hit = shutil.which(name)
        if hit:
            found.append(hit)
    seen: set[str] = set()
    out = []
    for path in found:
        key = os.path.normcase(os.path.abspath(path))
        if key not in seen and os.path.exists(path):
            seen.add(key)
            out.append(path)
    return out


def find_codex() -> tuple[str, str]:
    """→ `(路徑, 版本字串)`。明示的 `GPT_REVIEW_CODEX_BIN` 直接採用;否則取版本最高者。"""
    explicit = os.environ.get("GPT_REVIEW_CODEX_BIN", "").strip()
    if explicit:
        version = _cli_version(explicit)
        if not version:
            raise ReviewError(f"GPT_REVIEW_CODEX_BIN 無法執行:{explicit}")
        return explicit, version
    ranked = []
    for path in codex_candidates():
        version = _cli_version(path)
        if version:
            ranked.append((_version_key(version), path, version))
    if not ranked:
        raise ReviewError("Codex CLI not found; install it (or the Codex desktop app) before committing or pushing")
    ranked.sort(key=lambda row: row[0])
    _, path, version = ranked[-1]
    return path, version


# ---------------------------------------------------------------------------
# 指令、prompt、輸出解析
# ---------------------------------------------------------------------------
def build_command(codex: str, last_message: Path) -> list[str]:
    return [
        codex, "exec",
        "--json",
        "--ignore-user-config",
        "--strict-config",
        "--model", MODEL,
        "-c", f"model_reasoning_effort={EFFORT}",
        "-c", "web_search=disabled",
        "-c", "features.apps=false",
        "--sandbox", "read-only",
        # --ignore-user-config 也丟掉使用者的 [windows] sandbox;沒有它 read-only 沙箱在
        # Windows 會拒絕每一個指令(連 git diff 都跑不了),審查者什麼都讀不到。
        *(("-c", "windows.sandbox=unelevated") if os.name == "nt" else ()),
        "--cd", str(ROOT),
        "-o", str(last_message),
        "-",                       # prompt 走 stdin:diff 內容永遠不進 argv
    ]


def review_env() -> dict[str, str]:
    """審查子行程的環境:**只對這個 repo、只在這個子行程**加 safe.directory。

    Windows 唯讀沙箱以受限權杖執行審查者;checkout 由 BUILTIN\\Administrators 擁有時
    (主 checkout 就是),沙箱內每個 git 指令都回 dubious ownership,審查者讀不到 diff
    (另一個 session 在 user-main 實測到第一輪空轉;本 repo 的 worktree 擁有者是 User,
    所以先前測不出來)。GIT_CONFIG_PARAMETERS 是命令列層級設定,git 承認其中的
    safe.directory;使用者的全域 gitconfig 不動。路徑裡的單引號依 git 規則跳脫成 '\\''。
    """
    env = dict(os.environ)
    top = ROOT.resolve().as_posix()   # 與 git --show-toplevel 同格式(C:/…);不另開 git 子行程
    entry = "'safe.directory'='" + top.replace("'", "'\\''") + "'"
    existing = env.get("GIT_CONFIG_PARAMETERS", "").strip()
    env["GIT_CONFIG_PARAMETERS"] = f"{existing} {entry}" if existing else entry
    return env


def build_prompt(scope: str, ranges: Sequence[PushRange], snapshot_path: Path) -> str:
    """與 Claude 閘門同一份審查契約,只換掉模型宣告與工具說明。"""
    text = _base.build_prompt(scope, ranges, snapshot_path)
    text = text.replace(
        f"MODEL REQUIREMENT: this run must use Claude Opus 5.5 (`{_base.MODEL}`).",
        f"MODEL REQUIREMENT: this run must use GPT-6.1-Sol (`{MODEL}`) with reasoning effort `{EFFORT}`.")
    text = text.replace(
        "Then use Read to inspect",
        "Then read (with read-only shell commands such as `git show`, `git diff`, `cat`, `rg`) the file")
    text = text.replace(
        "For worktree reviews, also Read every untracked path",
        "For worktree reviews, also read every untracked path")
    if MODEL not in text:
        raise ReviewError("cannot build the review prompt: model requirement line not found")
    return text


def _events(raw: str) -> list[dict]:
    out = []
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            out.append(item)
    return out


def _thread_id(events: list[dict]) -> str:
    for item in events:
        if item.get("type") == "thread.started" and isinstance(item.get("thread_id"), str):
            return item["thread_id"]
    return ""


def _error_text(events: list[dict]) -> str:
    parts = []
    for item in events:
        if item.get("type") in ("error", "turn.failed"):
            err = item.get("error") if isinstance(item.get("error"), dict) else {}
            parts.append(str(item.get("message") or err.get("message") or ""))
    return "\n".join(p for p in parts if p)


def _terminal_failure(events: list[dict], stderr: str) -> str:
    """讓這次執行終止的那個失敗:turn.failed;沒有就 stderr 末行;再沒有,只有當事件流
    **結束在**一個 error 上才用它。查不出來就回空字串(→ 擋下,不延後)。

    額度 pending 只能看這一個,而且要有正面證據 —— 前面已經重試成功的 429(後面還有
    事件)不可讓之後別的終止原因(認證失敗、CLI 當掉只寫 stderr)被誤判成額度不足而延後。
    """
    failed = [t for t in (_error_text([item]) for item in events
                          if item.get("type") == "turn.failed") if t]
    if failed:
        return failed[-1]
    lines = [line for line in (stderr or "").splitlines() if line.strip()]
    if lines:
        return lines[-1]
    if events and events[-1].get("type") == "error":
        return _error_text([events[-1]])
    return ""


def _turn_completed(events: list[dict]) -> bool:
    """回合真的跑完了:有 turn.completed、沒有 turn.failed。

    Codex 在重連/重試時會先發出非致命的 `error` 事件(例如 429 之後重試成功),那不是
    終止失敗;只看「有沒有 error 事件」會把一次已完成的裁決改判成失敗甚至 pending。
    """
    types = {item.get("type") for item in events}
    return "turn.completed" in types and "turn.failed" not in types


def is_quota_error(text: str) -> bool:
    lowered = (text or "").casefold().replace(" ", "")
    return any(marker.replace(" ", "") in lowered for marker in _QUOTA_MARKERS)


def retry_hint(text: str) -> str | None:
    match = re.search(r"(?:try again at|resets?(?:\s+at)?)\s+([0-9]{1,2}(?::[0-9]{2})?\s*(?:am|pm)?"
                      r"(?:\s*\([A-Za-z0-9_+./:-]{1,48}\))?)", text or "", flags=re.IGNORECASE)
    return " ".join(match.group(1).split()) if match else None


def _codex_home() -> Path:
    env = os.environ.get("CODEX_HOME", "").strip()
    return Path(env) if env else Path.home() / ".codex"


def rollout_evidence(thread_id: str, sessions_root: Path | None = None) -> dict:
    """該次執行自己的 rollout:每一回合都必須是 MODEL / EFFORT / read-only。

    回 `{"rollout", "cli_version", "turns"}`;任何一項證明不了就 raise ReviewError。
    """
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", thread_id or ""):
        raise ReviewError("cannot prove the reviewer model: Codex did not report a thread id")
    root = sessions_root or (_codex_home() / "sessions")
    hits = sorted(root.rglob(f"rollout-*-{thread_id}.jsonl")) if root.exists() else []
    if len(hits) != 1:
        raise ReviewError(f"cannot prove that {MODEL} ran: rollout for thread {thread_id} not found ({len(hits)})")
    turns = 0
    cli_version = ""
    for line in hits[0].read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        payload = item.get("payload") if isinstance(item, dict) else None
        if not isinstance(payload, dict):
            continue
        if item.get("type") == "session_meta":
            cli_version = str(payload.get("cli_version") or "")
        elif item.get("type") == "turn_context":
            turns += 1
            sandbox = payload.get("sandbox_policy")
            sandbox_type = sandbox.get("type") if isinstance(sandbox, dict) else sandbox
            if payload.get("model") != MODEL or payload.get("effort") != EFFORT or sandbox_type != "read-only":
                raise ReviewError(
                    f"cannot prove that {MODEL}/{EFFORT}/read-only ran (turn reported "
                    f"model={payload.get('model')!r} effort={payload.get('effort')!r} sandbox={sandbox_type!r})")
    if not turns:
        raise ReviewError(f"cannot prove that {MODEL} ran: rollout has no turn context")
    return {"rollout": str(hits[0]), "cli_version": cli_version, "turns": turns}


def session_proof(thread_id: str, since: float, sessions_root: Path | None = None,
                  repo: Path | None = None) -> dict:
    """`tools/codex_review.sh`(PR review 的唯讀審查器)用:證明**這一次呼叫**的 session。

    使用者 2026-10-15 改為每個 PR review 一次(`tools/pr_review.py`),逐 commit 閘門不再
    執行,但「證明不了實際模型就 fail closed」不變:除了 `rollout_evidence` 的每回合
    MODEL / EFFORT / read-only,最後一個回合還必須屬於本 repo,且時間不早於本次呼叫開始。
    """
    sid = (thread_id or "").lower()
    if not re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", sid):
        raise ReviewError("cannot prove the reviewer model: invalid session id")
    evidence = rollout_evidence(sid, sessions_root)
    latest: dict = {}
    for line in Path(evidence["rollout"]).read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict) and item.get("type") == "turn_context":
            latest = item
    try:
        stamp = datetime.fromisoformat(str(latest.get("timestamp", "")).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReviewError("cannot prove this invocation ran: turn has no valid timestamp") from exc
    if stamp.tzinfo is None or stamp.timestamp() < since - 1:
        raise ReviewError("cannot prove this invocation ran: the latest turn predates it")
    payload = latest.get("payload") if isinstance(latest.get("payload"), dict) else {}
    cwd = str(payload.get("cwd") or "")
    if not cwd or Path(cwd).resolve() != (repo or ROOT).resolve():
        raise ReviewError("cannot prove the review ran in this repository")
    return {**evidence, "session": sid, "turn_timestamp": latest.get("timestamp")}


def _commands_ran(events: list[dict]) -> int:
    """審查者實際成功執行的唯讀指令數;0 代表它什麼都沒讀到。"""
    return sum(1 for item in events
               if isinstance(item.get("item"), dict)
               and item["item"].get("type") == "command_execution"
               and item["item"].get("exit_code") == 0)


def parse_verdict(message: str) -> str:
    lines = [line.strip() for line in (message or "").splitlines() if line.strip()]
    last = lines[-1] if lines else ""
    for verdict in ("APPROVE", "REQUEST_CHANGES"):
        if last in (verdict, f"**{verdict}**", f"`{verdict}`"):
            return verdict
    raise ReviewError("review did not end with exact APPROVE or REQUEST_CHANGES")


# ---------------------------------------------------------------------------
# pending 紀錄(這道閘門自己的檔案)
# ---------------------------------------------------------------------------
def record_pending_review(scope: str, fingerprint: str, error: BaseException) -> None:
    text = str(error)
    record = {
        "status": "PENDING",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "scope": scope,
        "fingerprint": fingerprint,
        "model": MODEL,
        "effort": EFFORT,
        "failure_kind": "quota" if isinstance(error, QuotaUnavailable) else "review_failed",
        "retry_after": retry_hint(text),
    }
    _atomic_text(PENDING_PATH, json.dumps(record, ensure_ascii=False, indent=2) + "\n")


def clear_pending_review(scope: str, fingerprint: str) -> None:
    try:
        record = json.loads(PENDING_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return
    except (OSError, ValueError) as exc:
        raise ReviewError("cannot read pending review record; preserving it") from exc
    if not isinstance(record, dict):
        raise ReviewError("invalid pending review record; preserving it")
    if record.get("scope") == scope and record.get("fingerprint") == fingerprint:
        PENDING_PATH.unlink()


# ---------------------------------------------------------------------------
# 執行一次審查
# ---------------------------------------------------------------------------
def run_review(scope: str, fingerprint: str, ranges: Sequence[PushRange]) -> int:
    codex, version = find_codex()
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    fd, snapshot_name = tempfile.mkstemp(prefix="review-", suffix=".patch", dir=STATE_DIR)
    os.close(fd)
    snapshot_path = Path(snapshot_name)
    fd, last_name = tempfile.mkstemp(prefix="last-", suffix=".txt", dir=STATE_DIR)
    os.close(fd)
    last_path = Path(last_name)
    try:
        snapshot = _base._snapshot_text(scope, ranges)
        snapshot_sha256 = hashlib.sha256(snapshot.encode("utf-8")).hexdigest()
        _atomic_text(snapshot_path, snapshot)
        proc = subprocess.run(
            build_command(codex, last_path), cwd=ROOT, env=review_env(),
            input=build_prompt(scope, ranges, snapshot_path),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", check=False,
        )
        message = last_path.read_text(encoding="utf-8", errors="replace") if last_path.exists() else ""
    finally:
        snapshot_path.unlink(missing_ok=True)
        last_path.unlink(missing_ok=True)
    events = _events(proc.stdout)
    errors = _error_text(events)
    if proc.returncode or not _turn_completed(events) or not message.strip():
        # 只有終止失敗才可能延後成 pending,而且只看終止的那個失敗;中途恢復的 error 只是診斷
        terminal = _terminal_failure(events, proc.stderr or "")
        failure = {"type": "review_failure", "exit_code": proc.returncode, "cli": version,
                   "terminal": terminal[:500], "recovered_errors": errors.splitlines(),
                   "quota": is_quota_error(terminal), "retry_after": retry_hint(terminal)}
        _atomic_text(STATE_DIR / "last_raw.json", json.dumps(failure, ensure_ascii=False) + "\n")
        kind = QuotaUnavailable if is_quota_error(terminal) else ReviewError
        raise kind(f"Codex review failed ({version}): {(terminal or 'no final message')[:300]}")
    evidence = rollout_evidence(_thread_id(events))
    if not _commands_ran(events):
        # 讀不到 repo 的「REQUEST_CHANGES」不是審查結論,也不能把它當成 finding 去修
        raise ReviewError("Codex reviewer ran no read-only command successfully; "
                          f"nothing was reviewed: {message.strip().splitlines()[0][:200] if message.strip() else ''}")
    verdict = parse_verdict(message)
    _atomic_text(STATE_DIR / "last_message.txt", message.rstrip() + "\n")
    _atomic_text(STATE_DIR / "last_review.json", json.dumps({
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "scope": scope, "fingerprint": fingerprint, "snapshot_sha256": snapshot_sha256,
        "model": MODEL, "effort": EFFORT, "verdict": verdict,
        "cli_version": evidence["cli_version"] or version, "rollout": evidence["rollout"],
        "recovered_errors": errors.splitlines() if errors else [],
        "ranges": [f"{r.local_ref}->{r.remote_ref} ({r.base}..{r.tip})" for r in ranges],
    }, ensure_ascii=False, indent=2) + "\n")
    print(message.rstrip())
    return 0 if verdict == "APPROVE" else 2


# ---------------------------------------------------------------------------
# commit-msg:新 commit 排進 GPT 佇列;切換前的 Claude 補審 audit 仍照 Claude 規則驗證
# ---------------------------------------------------------------------------
def commit_message(path: str) -> int:
    message = Path(path).read_text(encoding="utf-8")
    legacy = queue.trailers(_base, message)
    if legacy.get(queue.REVIEWED.lower()):
        # 切換前已排進 Claude 佇列的歷史 commit,補審仍用 Opus 5.5。它的 audit 必須是
        # 空的,而且照 Claude 佇列的規則驗證(passed 要真的 Claude 收據、resolved 要
        # 已結案的後代修正)—— 同一套檢查,不放寬。空 audit 不是新 diff,不排進 GPT 佇列。
        return queue.commit_message(_base, path)
    return queue.commit_message(sys.modules[__name__], path)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("scope", choices=("worktree", "staged", "push", "commit", "commit-msg", "pending", "which",
                                          "verify-session"))
    parser.add_argument("--remote-name", default="origin")
    parser.add_argument("--commit")
    parser.add_argument("--message-file")
    parser.add_argument("--session", help="verify-session: the wrapper's session id")
    parser.add_argument("--since", type=float, help="verify-session: epoch seconds the invocation started")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.scope == "verify-session":
        try:
            if args.since is None:
                raise ReviewError("--since is required")
            proof = session_proof(args.session or "", args.since)
        except (ReviewError, OSError) as exc:
            print(f"{LOG_PREFIX} BLOCKED: {exc}", file=sys.stderr)
            return 4
        print(f"{LOG_PREFIX} verified {MODEL}/{EFFORT}/read-only session {proof['session']} "
              f"({proof['turns']} turn(s), cli {proof['cli_version'] or 'unknown'})")
        return 0
    me = sys.modules[__name__]
    ranges: list[PushRange] = []
    before: str | None = None
    try:
        if args.scope == "which":
            path, version = find_codex()
            print(f"{path}\n{version}")
            return 0
        if args.scope == "pending":
            print("\n".join(queue.pending_commits(me)))
            return 0
        if args.scope == "commit-msg":
            if not args.message_file:
                raise ReviewError("--message-file is required")
            return commit_message(args.message_file)
        if args.scope == "worktree":
            before, fingerprint_fn = worktree_fingerprint(), worktree_fingerprint
        elif args.scope == "staged":
            before, fingerprint_fn = staged_fingerprint(), staged_fingerprint
        else:
            ranges = ([queue.commit_range(me, args.commit or "")]
                      if args.scope == "commit" else push_ranges(sys.stdin, args.remote_name))
            if args.scope == "push":
                queue.validate_push(me, ranges)
            before = push_fingerprint(ranges)

            def fingerprint_fn() -> str | None:
                return push_fingerprint(ranges)

        if before is None:
            print(f"{LOG_PREFIX} no {args.scope} diff to review")
            return 0
        print(f"{LOG_PREFIX} scope={args.scope} model={MODEL} effort={EFFORT}")
        result = run_review(args.scope, before, ranges)
        after = fingerprint_fn()
        if after != before:
            before = after
            raise ReviewError("the reviewed diff changed during review; run the review again")
        if result in (0, 2):
            clear_pending_review(args.scope, before)
        if result == 0 and args.scope in ("commit", "push"):
            queue.save_approvals(me, ranges, before)
        return result
    except QuotaUnavailable as exc:
        if before is not None:
            record_pending_review(args.scope, before, exc)
        if args.scope == "push":
            if not queue.quota_push_allowed(me, ranges):
                print(f"{LOG_PREFIX} BLOCKED: every outgoing commit needs a pending trailer", file=sys.stderr)
                return 4
            print(f"{LOG_PREFIX} PENDING: marked commits may push; exact-commit review remains queued")
            return 0
        print(f"{LOG_PREFIX} PENDING: {exc}", file=sys.stderr)
        return 3
    except ReviewError as exc:
        _atomic_text(STATE_DIR / "last_blocked.json", json.dumps({
            "status": "BLOCKED", "scope": args.scope, "fingerprint": before,
            "detail": str(exc)[:300]}, ensure_ascii=False) + "\n")
        print(f"{LOG_PREFIX} BLOCKED: {exc}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
