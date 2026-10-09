"""Offline regressions for pending publication-gate findings."""
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import pytest

import _delivery as d

SHA = "a" * 40


@pytest.mark.parametrize("url", [
    "https://github.com/owner/repo", "https://github.com/owner/repo.git",
    "https://GITHUB.COM/Owner/Repo.git/", "https://github.com/owner/repo/",
])
def test_https_repository_spellings_reach_candidate_checks(url):
    with patch.object(d, "git", return_value=url), patch.object(d, "clean") as clean, \
         patch.object(d.subprocess, "run") as run:
        d.pre_push([f"HEAD {SHA} refs/heads/codex/test {d.ZERO}"],
                   "origin", {"repository": "owner/repo"})
        assert clean.call_count == 2
        assert run.call_count == 2


@pytest.mark.parametrize("url", [
    "http://github.com/owner/repo", "https://github.com.evil.test/owner/repo",
    "https://github.com@evil.test/owner/repo", "https://user@github.com/owner/repo",
    "https://github.com/other/repo", "https://github.com/owner/other",
    "https://github.com/owner/repo?target=other", "https://github.com/owner/repo#other",
    "https://github.com:444/owner/repo", "https://github.com/owner/repo//", "https://[broken",
])
def test_repository_normalization_rejects_other_destinations(url):
    with patch.object(d, "git", return_value=url), patch.object(d, "clean") as clean, \
         patch.object(d.subprocess, "run") as run:
        with pytest.raises(d.Blocked):
            d.pre_push([f"HEAD {SHA} refs/heads/codex/test {d.ZERO}"],
                       "origin", {"repository": "owner/repo"})
        clean.assert_not_called()
        run.assert_not_called()


@pytest.mark.parametrize("failure", [
    URLError("temporary outage"), TimeoutError("timeout"),
    *[HTTPError("https://api.github.com/", code, "unavailable", {}, None) for code in (403, 429, 503)],
])
def test_verify_wait_recovers_from_temporary_api_failures(failure):
    with patch.object(d.sys, "argv", ["_delivery.py", "verify", SHA, "--wait", "60"]), \
         patch.object(d, "API"), patch.object(d, "verify", side_effect=[failure, []]) as verify, \
         patch.object(d.time, "monotonic", return_value=0), patch.object(d.time, "sleep") as sleep:
        assert d.main() == 0
        assert verify.call_count == 2
        sleep.assert_called_once_with(30)


@pytest.mark.parametrize("wait", ["0", "60"])
def test_verify_wait_still_fails_closed_at_deadline(wait):
    with patch.object(d.sys, "argv", ["_delivery.py", "verify", SHA, "--wait", wait]), \
         patch.object(d, "API"), patch.object(d, "verify", side_effect=URLError("outage")) as verify, \
         patch.object(d.time, "monotonic", side_effect=[0, 60]), patch.object(d.time, "sleep") as sleep:
        assert d.main() == 1
        verify.assert_called_once()
        sleep.assert_not_called()
