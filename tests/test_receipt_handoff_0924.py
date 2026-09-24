"""A backup run must not republish the receipt it inherited at checkout."""

import json
import subprocess
from pathlib import Path

import pytest
import yaml

import receipt_handoff as rh
from receipt_freshness import may_replace_receipt
import state_publish as sp


def _receipt(path: Path, run_id: str, delivery: dict,
             run_attempt: str = "1") -> Path:
    path.write_text(json.dumps({
        "date": "2026-09-24 07:34",
        "github_run_id": run_id,
        "github_run_attempt": run_attempt,
        "delivery": delivery,
    }), encoding="utf-8")
    return path


def test_only_current_terminal_receipt_is_eligible(tmp_path):
    path = tmp_path / "delivery_receipt.json"
    _receipt(path, "prior-run", {"success": True})
    assert not sp.receipt_belongs_to_run(path, "current-run")
    _receipt(path, "current-run", {"attempted": True})
    assert not sp.receipt_belongs_to_run(path, "current-run")
    _receipt(path, "current-run", {"success": True})
    assert sp.receipt_belongs_to_run(path, "current-run")
    _receipt(path, "current-run", {"skipped_reason": "weekend_no_new_content"})
    assert not sp.receipt_belongs_to_run(path, "current-run", "1")
    _receipt(path, "current-run", {"attempted": False, "success": False,
                                   "skipped_reason": "weekend_no_new_content"})
    assert sp.receipt_belongs_to_run(path, "current-run", "1")
    assert not sp.receipt_belongs_to_run(path, "current-run", "2")
    _receipt(path, "current-run", {"success": True,
                                   "skipped_reason": "weekend_no_new_content"})
    assert not sp.receipt_belongs_to_run(path, "current-run", "1")
    _receipt(path, "current-run", {"success": False,
                                   "skipped_reason": ["bad type"]})
    assert not sp.receipt_belongs_to_run(path, "current-run", "1")
    path.write_text("{broken", encoding="utf-8")
    assert not sp.receipt_belongs_to_run(path, "current-run")


def test_prior_producer_attempt_is_valid_for_downstream_rerun():
    assert rh.receipt_attempt_allowed("1", "1")
    assert rh.receipt_attempt_allowed("1", "2")
    assert rh.receipt_attempt_allowed("2", "3")
    for producer, current in (("2", "1"), ("0", "2"), ("01", "2"),
                              ("1x", "2"), ("", "2"), ("1", "")):
        assert not rh.receipt_attempt_allowed(producer, current)


def test_partial_publish_rerun_uses_original_receipt_attempt(tmp_path):
    path = _receipt(tmp_path / "delivery_receipt.json", "same-run",
                    {"attempted": True, "success": True}, run_attempt="1")
    assert rh.receipt_attempt_allowed("1", "2")
    assert sp.receipt_belongs_to_run(path, "same-run", "1")
    allow = ["state/delivery_receipt.json", "state/history.json"]
    assert rh.filter_batch_allowlist(
        sp.validated_allowlist(allow), run_id="same-run", run_attempt="1",
        receipt_file=path) == allow
    assert rh.filter_batch_allowlist(
        sp.validated_allowlist(allow), run_id="same-run", run_attempt="2",
        receipt_file=path) == ["state/history.json"]


def test_late_retry_cannot_replace_newer_remote_delivery(tmp_path):
    older = _receipt(tmp_path / "older.json", "100", {
        "attempted": True, "success": True,
        "delivered_at": "2026-09-24T07:35:00+08:00"})
    newer = _receipt(tmp_path / "newer.json", "101", {
        "attempted": True, "success": True,
        "delivered_at": "2026-09-24T08:05:00+08:00"})
    old_data = json.loads(older.read_text(encoding="utf-8"))
    new_data = json.loads(newer.read_text(encoding="utf-8"))
    assert not may_replace_receipt(old_data, new_data)
    assert may_replace_receipt(new_data, old_data)
    assert may_replace_receipt(new_data, new_data)
    allow = ["state/delivery_receipt.json", "state/history.json"]
    assert rh.filter_batch_allowlist(
        allow, run_id="100", run_attempt="1", receipt_file=older,
        remote_receipt_file=newer) == ["state/history.json"]
    assert rh.filter_batch_allowlist(
        allow, run_id="101", run_attempt="1", receipt_file=newer,
        remote_receipt_file=older) == allow


def test_terminal_receipt_order_preserves_delivery_and_date():
    def item(day, run_id, outcome, when=None, attempt="1"):
        delivery = ({"attempted": True, "success": True,
                     "delivered_at": when} if outcome == "delivered" else
                    {"attempted": False, "success": False,
                     "skipped_reason": "already_delivered"})
        return {"date": day, "github_run_id": run_id,
                "github_run_attempt": attempt, "delivery": delivery}

    delivered = item("2026-09-24", "101", "delivered",
                     "2026-09-24T08:05:00+08:00")
    skipped = item("2026-09-24", "102", "skipped")
    assert not may_replace_receipt(skipped, delivered)
    assert may_replace_receipt(delivered, skipped)
    assert not may_replace_receipt(
        item("2026-09-23", "200", "delivered",
             "2026-09-23T08:05:00+08:00"), delivered)
    assert may_replace_receipt(
        item("2026-09-25", "200", "skipped"), delivered)
    assert not may_replace_receipt(
        item("2026-09-24", "101", "delivered",
             "2026-09-24T07:35:00+08:00", attempt="1"),
        item("2026-09-24", "101", "delivered",
             "2026-09-24T08:05:00+08:00", attempt="2"))


def test_corrupt_remote_can_be_repaired_but_invalid_candidate_cannot(tmp_path, capsys):
    current = _receipt(tmp_path / "current.json", "102", {
        "attempted": True, "success": True,
        "delivered_at": "2026-09-24T08:30:00+08:00"})
    remote = tmp_path / "remote.json"
    allow = ["state/delivery_receipt.json", "state/history.json"]
    for invalid in ("{broken", "null", json.dumps({"delivery": {"attempted": True}}),
                    json.dumps({"date": "bad", "delivery": {"success": True}})):
        remote.write_text(invalid, encoding="utf-8")
        assert rh.filter_batch_allowlist(
            allow, run_id="102", run_attempt="1", receipt_file=current,
            remote_receipt_file=remote) == allow
    assert "invalid-remote-receipt" in capsys.readouterr().err
    current.write_text(json.dumps({"date": "bad", "github_run_id": "102",
                                   "github_run_attempt": "1",
                                   "delivery": {"success": True}}),
                       encoding="utf-8")
    with pytest.raises(ValueError, match="日期"):
        rh.filter_batch_allowlist(
            allow, run_id="102", run_attempt="1", receipt_file=current,
            remote_receipt_file=remote)


def test_standalone_receipt_publish_rejects_late_retry(tmp_path):
    def git(*args, cwd):
        result = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                                text=True, encoding="utf-8", errors="replace")
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    remote, work = tmp_path / "remote.git", tmp_path / "work"
    git("init", "--bare", "-b", "main", str(remote), cwd=tmp_path)
    git("clone", str(remote), str(work), cwd=tmp_path)
    git("config", "user.email", "test@example.invalid", cwd=work)
    git("config", "user.name", "test", cwd=work)
    (work / "state").mkdir()
    _receipt(work / "state" / "delivery_receipt.json", "101", {
        "attempted": True, "success": True,
        "delivered_at": "2026-09-24T08:05:00+08:00"})
    git("add", "state/delivery_receipt.json", cwd=work)
    git("commit", "-m", "newer receipt", cwd=work)
    git("push", "origin", "main", cwd=work)
    old = _receipt(tmp_path / "old.json", "100", {
        "attempted": True, "success": True,
        "delivered_at": "2026-09-24T07:35:00+08:00"})
    assert sp.publish_receipt_from_remote_base(
        old, cwd=work, expected_run_id="100", expected_run_attempt="1") is False
    assert json.loads(git("show", "main:state/delivery_receipt.json",
                          cwd=remote))["github_run_id"] == "101"
    (work / "state" / "delivery_receipt.json").write_text(
        "{broken", encoding="utf-8")
    git("add", "state/delivery_receipt.json", cwd=work)
    git("commit", "-m", "corrupt receipt", cwd=work)
    git("push", "origin", "main", cwd=work)
    repaired = _receipt(tmp_path / "repaired.json", "102", {
        "attempted": True, "success": True,
        "delivered_at": "2026-09-24T08:30:00+08:00"})
    assert sp.publish_receipt_from_remote_base(
        repaired, cwd=work, expected_run_id="102", expected_run_attempt="1")
    assert json.loads(git("show", "main:state/delivery_receipt.json",
                          cwd=remote))["github_run_id"] == "102"


def test_privileged_publisher_rejects_stale_receipt_before_git(tmp_path):
    path = _receipt(tmp_path / "delivery_receipt.json", "old-run",
                    {"success": True})
    with pytest.raises(ValueError, match="不是本班"):
        sp.publish_receipt_from_remote_base(path, cwd=tmp_path,
                                            expected_run_id="new-run")
    _receipt(path, "new-run", {"success": True}, run_attempt="1")
    with pytest.raises(ValueError, match="不是本班"):
        sp.publish_receipt_from_remote_base(path, cwd=tmp_path,
                                            expected_run_id="new-run",
                                            expected_run_attempt="2")


def test_batch_state_push_cannot_republish_checkout_receipt(tmp_path):
    path = _receipt(tmp_path / "delivery_receipt.json", "old-run",
                    {"success": True})
    allow = ["state/delivery_receipt.json", "state/history.json"]
    current = rh.filter_batch_allowlist(
        sp.validated_allowlist(allow), run_id="new-run", run_attempt="1",
        receipt_file=path)
    assert current == ["state/history.json"]
    with pytest.raises(sp.UnsafePublishPath):
        sp.validated_deletions(["state/delivery_receipt.json"], current)
    _receipt(path, "new-run", {"success": True})
    assert rh.filter_batch_allowlist(
        sp.validated_allowlist(allow), run_id="new-run", run_attempt="1",
        receipt_file=path) == allow
    with pytest.raises(ValueError):
        rh.filter_batch_allowlist(
            sp.validated_allowlist(["state", "state/history.json"]),
            run_id="new-run",
            run_attempt="1", receipt_file=path)


def test_batch_cli_filters_stale_receipt_from_both_operations(
        tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    receipt = tmp_path / "state" / "delivery_receipt.json"
    receipt.parent.mkdir()
    _receipt(receipt, "old-run", {"success": True})
    paths = tmp_path / "paths.txt"
    paths.write_text("state/delivery_receipt.json\nstate/history.json\n",
                     encoding="utf-8")
    deleted = tmp_path / "deleted.txt"
    deleted.write_text("state/delivery_receipt.json\n", encoding="utf-8")
    monkeypatch.setenv("RECEIPT_RUN_ID", "new-run")
    monkeypatch.setenv("RECEIPT_RUN_ATTEMPT", "1")
    assert sp._cli(["state_publish", "paths", str(paths)]) == 0
    logged = capsys.readouterr()
    assert logged.out.strip() == "state/history.json"
    assert "::warning title=stale-delivery-receipt::" in logged.err
    assert sp._cli(["state_publish", "deletions", str(paths),
                    str(deleted)]) == 1
    assert "交棒清單被拒" in capsys.readouterr().err


def test_batch_cli_drops_valid_receipt_older_than_remote(
        tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    receipt = tmp_path / "state" / "delivery_receipt.json"
    receipt.parent.mkdir()
    _receipt(receipt, "100", {"attempted": True, "success": True,
                              "delivered_at": "2026-09-24T07:35:00+08:00"})
    remote = _receipt(tmp_path / "remote.json", "101", {
        "attempted": True, "success": True,
        "delivered_at": "2026-09-24T08:05:00+08:00"})
    paths = tmp_path / "paths.txt"
    paths.write_text("state/delivery_receipt.json\nstate/history.json\n",
                     encoding="utf-8")
    monkeypatch.setenv("RECEIPT_RUN_ID", "100")
    monkeypatch.setenv("RECEIPT_RUN_ATTEMPT", "1")
    monkeypatch.setenv("REMOTE_RECEIPT_FILE", str(remote))
    assert sp._cli(["state_publish", "paths", str(paths)]) == 0
    logged = capsys.readouterr()
    assert logged.out.strip() == "state/history.json"
    assert "::warning title=stale-delivery-receipt::" in logged.err


def test_producer_records_run_attempt(tmp_path, monkeypatch):
    import morning_report as mr

    path = tmp_path / "delivery_receipt.json"
    monkeypatch.setattr(mr, "DELIVERY_RECEIPT_FILE", path)
    monkeypatch.setattr(mr, "_day_first_delivery", lambda _day, at: at)
    monkeypatch.setenv("GITHUB_RUN_ID", "current-run")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    monkeypatch.setenv("GITHUB_ACTIONS", "false")
    mr._publish_delivery_receipt("2026-09-24 07:34", {
        "attempted": True, "success": True,
        "delivered_at": "2026-09-24T08:00:00+08:00",
    })
    assert sp.receipt_belongs_to_run(path, "current-run", "2")


def test_workflow_guards_handoff_and_privileged_publish():
    wf_path = Path(__file__).resolve().parents[1] / ".github/workflows/morning-report-b.yml"
    wf = yaml.safe_load(wf_path.read_text(encoding="utf-8"))
    send = wf["jobs"]["send-report"]["steps"]
    check = next(step for step in send if step.get("id") == "fresh_receipt")
    upload = next(step for step in send
                  if str((step.get("with") or {}).get("name") or "").startswith(
                      "delivery-receipt-"))
    assert "receipt_belongs_to_run" in check["run"]
    assert "GITHUB_RUN_ID" in check["run"]
    assert "GITHUB_RUN_ATTEMPT" in check["run"]
    assert "receipt_attempt=$GITHUB_RUN_ATTEMPT" in check["run"]
    assert wf["jobs"]["send-report"]["outputs"]["receipt_attempt"] == (
        "${{ steps.fresh_receipt.outputs.receipt_attempt }}")
    assert "steps.fresh_receipt.outputs.current == 'true'" in upload["if"]
    assert upload["with"]["name"] == (
        "delivery-receipt-${{ github.run_id }}-${{ github.run_attempt }}")
    publisher_steps = wf["jobs"]["publish-state"]["steps"]
    source = next(step for step in publisher_steps
                  if step.get("id") == "receipt_source")
    assert "receipt_attempt_allowed" in source["run"]
    assert source["env"]["CURRENT_RUN_ATTEMPT"] == "${{ github.run_attempt }}"
    missing_source = next(step for step in publisher_steps
                          if step.get("name") == "已寄信但缺少收據來源輪次")
    assert "needs.send-report.outputs.delivered == 'true'" in missing_source["if"]
    download = next(step for step in publisher_steps
                    if step.get("id") == "receipt")
    assert download["with"]["name"] == (
        "delivery-receipt-${{ github.run_id }}-${{ needs.send-report.outputs.receipt_attempt }}")
    missing_artifact = next(step for step in publisher_steps
                            if step.get("name") == "預期寄送收據缺席")
    assert "steps.receipt.outcome != 'success'" in missing_artifact["if"]
    assert "hashFiles('_receipt/delivery_receipt.json') == ''" in missing_artifact["if"]
    publish = next(step for step in publisher_steps
                   if step.get("name") == "發佈寄送收據")
    assert publish["env"]["RECEIPT_RUN_ID"] == "${{ github.run_id }}"
    assert publish["env"]["RECEIPT_RUN_ATTEMPT"] == (
        "${{ needs.send-report.outputs.receipt_attempt }}")
    assert "expected_run_id=os.environ['RECEIPT_RUN_ID']" in publish["run"]
    assert "expected_run_attempt=os.environ['RECEIPT_RUN_ATTEMPT']" in publish["run"]
    batch = next(step for step in publisher_steps
                 if step.get("name") == "發佈 state(契約通過後才 push)")
    state_download = next(step for step in publisher_steps if step.get("id") == "statedl")
    assert "!cancelled()" in state_download["if"]
    assert "!cancelled()" in batch["if"]
    assert batch["env"]["RECEIPT_RUN_ID"] == "${{ github.run_id }}"
    assert batch["env"]["RECEIPT_RUN_ATTEMPT"] == (
        "${{ needs.send-report.outputs.receipt_attempt || github.run_attempt }}")
    assert "git fetch --quiet origin main" in batch["run"]
    assert "for fetch_attempt in 1 2 3; do" in batch["run"]
    assert "if git fetch --quiet origin main; then break; fi" in batch["run"]
    assert "receipt-freshness-unavailable" in batch["run"]
    assert 'export REMOTE_RECEIPT_FILE="$T/_remote_receipt.json"' in batch["run"]
    assert "python3 -m state_publish paths" in batch["run"]
