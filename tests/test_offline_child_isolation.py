"""Synthetic child-process checks; never open a socket or production state."""

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def _child(code: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", code, *args], cwd=ROOT,
        env=dict(os.environ), capture_output=True, text=True, timeout=15,
    )


def test_python_child_blocks_synthetic_network_audit_event():
    # sys.audit emits only a synthetic event; it never connects or resolves DNS.
    result = _child(
        "import sys\n"
        "try: sys.audit('socket.connect', object(), ('api.deepseek.com', 443))\n"
        "except RuntimeError: print('blocked')\n"
        "else: print('allowed')\n"
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "blocked"


def test_python_child_blocks_synthetic_formal_state_write_event():
    # No file is opened. This only checks the child's audit hook.
    target = ROOT / "state" / "offline-child-probe-do-not-create.json"
    result = _child(
        "import os,sys\n"
        "try: sys.audit('open', sys.argv[1], 'w', os.O_CREAT | os.O_WRONLY)\n"
        "except AssertionError: print('blocked')\n"
        "else: print('allowed')\n",
        str(target),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "blocked"
    assert not target.exists()


def test_python_child_installs_smtp_and_native_curl_guards():
    # Introspection only: no SMTP or HTTP method is called.
    result = _child(
        "import smtplib, curl_cffi.requests as c\n"
        "print(int(getattr(smtplib.SMTP, '_morning_offline_guard', False)))\n"
        "print(int(getattr(smtplib.SMTP_SSL, '_morning_offline_guard', False)))\n"
        "print(int(getattr(c.Session.request, '_morning_offline_guard', False)))\n"
        "print(int(getattr(c.AsyncSession.request, '_morning_offline_guard', False)))\n"
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["1", "1", "1", "1"]


def test_parent_installs_native_async_curl_guard():
    import curl_cffi.requests as curl_requests

    assert getattr(curl_requests.AsyncSession.request, "_morning_offline_guard", False)
