"""Exercise the actual stateless scope guard, without report import or API calls."""
import ast
import copy
from pathlib import Path

import analysis_validate as av
import podcast_revision
import pytest


def _claim(statement="公司維持展望", *, support=("n1",), contrary=(), assets=("2330",)):
    return {"claim_audit": [{"statement": statement, "asset_scope": list(assets),
                            "evidence_ids": list(support), "counterevidence_ids": list(contrary),
                            "claim_id": "c1", "confidence": 0.7}]}


def _scope_guard():
    path = Path(__file__).resolve().parents[1] / "morning_report.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    caller = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                  and node.name == "_luna_analysis")
    checks = [node for node in ast.walk(caller) if isinstance(node, ast.If)
              and isinstance(node.test, ast.BoolOp)
              and "_sent_visible" in ast.unparse(node.test)]
    assert len(checks) == 2
    code = compile(ast.fix_missing_locations(ast.Module(body=checks, type_ignores=[])), str(path), "exec")
    scope = {"_av": av, "_podcast_revision": podcast_revision, "_full_ctx_cited": set(),
             "_full_ctx_opinions": set(), "_full_ctx_claims": set()}
    if "_claim_revision" in ast.unparse(ast.Module(body=checks, type_ignores=[])):
        import repair_claim_revision
        scope["_claim_revision"] = repair_claim_revision

    def check(obj, visible):
        scope.update(obj=obj, _sent_visible=visible, problems=[])
        exec(code, scope)
        return scope["problems"]

    return check


@pytest.mark.parametrize("change", ["statement", "asset_scope", "counterevidence_ids"])
def test_reusing_an_old_id_cannot_authorize_a_new_unseen_claim(change):
    original = _claim()
    revised = copy.deepcopy(original)
    row = revised["claim_audit"][0]
    if change == "statement":
        row[change] = "公司已撤回展望"
    elif change == "asset_scope":
        row[change] = ["2317"]
    else:
        row["evidence_ids"], row[change] = [], ["n1"]
    check = _scope_guard()
    assert check(original, None) == []
    assert av.cited_evidence_ids(revised) == av.cited_evidence_ids(original)
    assert check(revised, set()), "同一 ID 不代表本輪看得到新主張的證據"


def test_unchanged_claims_allow_structural_metadata_repairs_and_reordering():
    original = _claim(support=("n1", "n2"), assets=("2330", "TAIEX"))
    revised = copy.deepcopy(original)
    row = revised["claim_audit"][0]
    row.update(claim_id="renumbered", confidence=0.4, horizon="unknown")
    row["evidence_ids"].reverse()
    row["asset_scope"].reverse()
    check = _scope_guard()
    assert check(original, None) == []
    assert check(revised, set()) == []


def test_visible_evidence_allows_a_changed_claim_without_grandfathering():
    check = _scope_guard()
    check(_claim(), None)
    assert check(_claim("公司撤回展望"), {"n1"}) == []


def test_removing_unseen_references_allows_relabelled_inference():
    check = _scope_guard()
    check(_claim(), None)
    inferred = _claim("本報不能確認展望", support=())
    inferred["claim_audit"][0]["claim_type"] = "inference"
    assert check(inferred, set()) == []


def test_two_rejected_slice_revisions_do_not_replace_the_full_context_anchor():
    check = _scope_guard()
    check(_claim(), None)
    invented = _claim("公司撤回展望")
    assert check(invented, set())
    assert check(copy.deepcopy(invented), set())
    assert check(_claim(), set()) == []


def test_a_later_full_context_reanchors_only_when_evidence_is_resent():
    check = _scope_guard()
    check(_claim(), None)
    revised = _claim("公司撤回展望")
    assert check(revised, None) == []
    assert check(revised, set()) == []


def test_wrong_shape_remains_owned_by_the_existing_schema_validator():
    check = _scope_guard()
    for obj in ({}, {"claim_audit": None}, {"claim_audit": [None]}, {"claim_audit": "bad"}):
        assert check(obj, None) == []
        assert check(obj, set()) == []
