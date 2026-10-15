"""Offline tests for the whole-PR review gate (_delivery_review.py). No network."""
import io
import subprocess
import unittest
from unittest.mock import patch

import _delivery as d
import _delivery_review as r

SHA = "a" * 40


class PRReviewGateTests(unittest.TestCase):
    def _review_api(self, *statuses):
        class Fake:
            def pages(self, path):
                assert path == f"/commits/{SHA}/statuses"
                return list(statuses)
        return Fake()

    def _status(self, state="success", description=None, login="owner", context=None, ident=1):
        return {"id": ident, "state": state, "context": context or r.REVIEW_CONTEXT,
                "description": description or f"APPROVE base={'b'*40}",
                "creator": {"login": login}, "target_url": "https://github.com/owner/repo/pull/1"}

    def test_delivery_shares_the_blocked_type_and_gate(self):
        self.assertIs(d.Blocked, r.Blocked)
        self.assertIs(d.pr_review, r.pr_review)

    def test_pr_review_approve_covering_main_passes(self):
        cfg = {"repository": "owner/repo"}
        with patch.object(r.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run:
            result = r.pr_review(self._review_api(self._status()), cfg, SHA, "c"*40)
        self.assertEqual(result["pr_review"], "APPROVE")
        self.assertEqual(run.call_args.args[0][-2:], ["b"*40, "c"*40])

    def test_pr_review_blocks_missing_failed_foreign_or_malformed(self):
        cfg = {"repository": "owner/repo"}
        cases = ([], [self._status("failure", f"REQUEST_CHANGES base={'b'*40}")],
                 [self._status(login="someone-else")], [self._status(context="other")],
                 [self._status("pending", f"APPROVE base={'b'*40}")],
                 [self._status(description="APPROVE")],
                 [self._status(ident=1), self._status("failure", f"REQUEST_CHANGES base={'b'*40}", ident=2)])
        for statuses in cases:
            with self.subTest(statuses=statuses), \
                 patch.object(r.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)), \
                 self.assertRaises(r.Blocked):
                r.pr_review(self._review_api(*statuses), cfg, SHA, "c"*40)

    def test_pr_review_rejects_short_shas(self):
        with self.assertRaises(r.Blocked):
            r.pr_review(self._review_api(self._status()), {"repository": "owner/repo"}, SHA, "c")

    def test_pr_review_range_must_cover_promoted_commits(self):
        with patch.object(r.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)), \
             self.assertRaises(r.Blocked):
            r.pr_review(self._review_api(self._status()), {"repository": "owner/repo"}, SHA, "c"*40)

    def test_pr_review_quota_pending_publishes_but_is_reported(self):
        status = self._status("pending", f"quota-pending base={'b'*40}")
        with patch.object(r.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)), \
             patch.object(r.sys, "stderr", io.StringIO()) as err:
            result = r.pr_review(self._review_api(status), {"repository": "owner/repo"}, SHA, "c"*40)
        self.assertEqual(result["pr_review"], "quota-pending")
        self.assertIn("not approved", err.getvalue())

    def test_candidate_cannot_drop_base_pr_review(self):
        self.assertTrue(r.requires_pr_review({"pr_review": False}, {"pr_review": True}))
        self.assertTrue(r.requires_pr_review({"pr_review": True}, {}))
        self.assertFalse(r.requires_pr_review({}, {}))
        with self.assertRaises(d.Blocked):
            d.validate_policy({**d.policy(), "pr_review": "yes"})

    def test_main_promotion_checks_pr_review_required_by_production_policy(self):
        """The previous main's policy decides even when the candidate drops pr_review."""
        prev = "b" * 40
        outputs = {("remote", "get-url", "--push", "origin"): "https://github.com/owner/repo.git",
                   ("rev-parse", "FETCH_HEAD"): prev,
                   ("show", f"{prev}:_delivery_policy.json"): '{"pr_review": true}'}
        with patch.object(d, "git", side_effect=lambda *a: outputs[a]), \
             patch.object(d, "clean"), patch.object(d.subprocess, "run"), \
             patch.object(d, "API"), patch.object(d, "verify", return_value=[]), \
             patch.object(d, "pr_review", side_effect=d.Blocked("no review")) as review, \
             self.assertRaises(d.Blocked):
            d.pre_push([f"HEAD {SHA} refs/heads/main {prev}"], "origin",
                       {"repository": "owner/repo", "pr_review": False})
        review.assert_called_once()

    def test_candidate_push_runs_both_offline_delivery_test_modules(self):
        source = (d.ROOT / "_delivery.py").read_text(encoding="utf-8")
        self.assertIn('"unittest", "_test_delivery", "_test_delivery_review"', source)
        workflow = (d.ROOT / ".github" / "workflows" / "delivery.yml").read_text(encoding="utf-8")
        self.assertIn("python -m unittest _test_delivery _test_delivery_review", workflow)
        self.assertFalse(d.needs_morning_preview(["_delivery_review.py", "_test_delivery_review.py"]))


if __name__ == "__main__":
    unittest.main()
