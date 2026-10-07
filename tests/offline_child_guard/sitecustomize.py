"""Offline guard for Python children launched from the pytest process only.

The parent test conftest adds this directory to PYTHONPATH.  This file is not
on the production runner's import path and makes no external requests.
"""

import os
from pathlib import Path
import smtplib
import sys
from urllib.parse import urlsplit


_FORMAL_STATE_ROOT = Path(os.environ["MORNING_TEST_FORMAL_STATE_ROOT"]).resolve()
_ALLOWED_HOSTS = {"localhost", "127.0.0.1", "::1", ""}
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND


def _blocked_smtp(*_args, **_kwargs):
    raise RuntimeError("SMTP is blocked in offline tests")


_blocked_smtp._morning_offline_guard = True
smtplib.SMTP = _blocked_smtp
smtplib.SMTP_SSL = _blocked_smtp


def _audit_network(event, args):
    if event == "socket.getaddrinfo":
        host = args[0] if args else None
    elif event == "socket.connect":
        address = args[1] if len(args) > 1 else None
        if not isinstance(address, tuple):
            return
        host = address[0] if address else None
    else:
        return
    if host is None:
        return
    name = os.fsdecode(host) if isinstance(host, bytes) else str(host)
    if name not in _ALLOWED_HOSTS:
        raise RuntimeError(f"Network is blocked in offline tests: {name}")


def _is_formal_state(path):
    if isinstance(path, int):
        return False
    try:
        candidate = Path(os.fsdecode(path)).resolve()
    except (OSError, ValueError, TypeError):
        return False
    return candidate == _FORMAL_STATE_ROOT or _FORMAL_STATE_ROOT in candidate.parents


def _audit_state_write(event, args):
    if event == "open":
        path, mode, flags = args
        writing = any(ch in str(mode) for ch in "wax+") or (
            isinstance(flags, int) and bool(flags & _WRITE_FLAGS)
        )
        targets = (path,) if writing else ()
    elif event == "os.rename":
        targets = args[:2]
    elif event in {
        "os.remove", "os.rmdir", "os.mkdir", "os.truncate",
        "os.chmod", "os.chown", "os.utime", "os.link", "os.symlink",
    }:
        targets = args[:2] if event in {"os.link", "os.symlink"} else args[:1]
    else:
        return
    for target in targets:
        if _is_formal_state(target):
            raise AssertionError(f"Formal repo state write blocked in offline tests: {event}")


sys.addaudithook(_audit_network)
sys.addaudithook(_audit_state_write)


# Native libcurl does not emit Python socket audit events.  This is installed
# at interpreter startup rather than waiting for a per-test fixture.
import curl_cffi.requests as _curl_requests  # noqa: E402

_real_curl_request = _curl_requests.Session.request


def _guard_curl_request(self, method, url, *args, **kwargs):
    host = urlsplit(str(url)).hostname or str(url)
    if host not in _ALLOWED_HOSTS:
        raise RuntimeError(f"Network is blocked in offline tests: {host}")
    return _real_curl_request(self, method, url, *args, **kwargs)


_guard_curl_request._morning_offline_guard = True
_curl_requests.Session.request = _guard_curl_request

_real_async_curl_request = _curl_requests.AsyncSession.request


async def _guard_async_curl_request(self, method, url, *args, **kwargs):
    host = urlsplit(str(url)).hostname or str(url)
    if host not in _ALLOWED_HOSTS:
        raise RuntimeError(f"Network is blocked in offline tests: {host}")
    return await _real_async_curl_request(self, method, url, *args, **kwargs)


_guard_async_curl_request._morning_offline_guard = True
_curl_requests.AsyncSession.request = _guard_async_curl_request
