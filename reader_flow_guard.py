"""Correct known unsupported same-money inferences in report prose."""

import re

_MONEY_CONCENTRATION = re.compile(
    r"今天的錢(?:明顯)?往(?P<sector>[\u4e00-\u9fffA-Za-z*·－-]{2,20})集中"
)
_CROSS_FLOW = "這個流向與外資現貨賣超、期貨偏空的方向一致"
_FLOW_REVERSAL = "要讓這個流向反轉"


def neutralize_flow(prose: str, manifest: dict) -> str:
    """Keep observed figures without claiming that different flows are identical."""
    if not isinstance(prose, str) or not prose:
        return prose
    revised, n1 = _MONEY_CONCENTRATION.subn(
        lambda m: f"上一交易日{m.group('sector')}成交較集中", prose)
    n2 = revised.count(_CROSS_FLOW)
    revised = revised.replace(
        _CROSS_FLOW,
        "類股法人淨額與外資現貨、期貨部位口徑不同，不能確認為同一資金流向")
    n3 = revised.count(_FLOW_REVERSAL)
    revised = revised.replace(_FLOW_REVERSAL, "要判斷類股交易分布是否改變")
    if n1 + n2 + n3:
        manifest.setdefault("llm", {})["sector_flow_claims_neutralized"] = n1 + n2 + n3
    return revised
