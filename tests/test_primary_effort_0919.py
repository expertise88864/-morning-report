"""User-approved production default; no model API calls."""
import ast
from pathlib import Path


def test_python_and_workflow_defaults_agree_on_high():
    root = Path(__file__).resolve().parents[1]
    tree = ast.parse((root / 'morning_report.py').read_text(encoding='utf-8'))
    defaults = [node.args[1].value for node in ast.walk(tree)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == 'get' and len(node.args) > 1
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == 'DEEPSEEK_REASONING_EFFORT'
                and isinstance(node.args[1], ast.Constant)]
    assert defaults == ['high']
    for name in ('morning-report-b.yml', 'ci.yml', 'validate-llm-config.yml'):
        text = (root / '.github/workflows' / name).read_text(encoding='utf-8')
        assert "DEEPSEEK_REASONING_EFFORT: ${{ vars.DEEPSEEK_REASONING_EFFORT || 'high' }}" in text


def test_high_output_budget_is_not_raised_to_max():
    from llm_telemetry import output_cap
    assert output_cap('high', 7000, model='deepseek-v4-flash') < output_cap(
        'max', 7000, model='deepseek-v4-flash')
