# -*- coding: utf-8 -*-
"""**SLA 判定只住一處**(2026-09-05,工單自 2026-09-01 r7 外審)。

`run_quality.py` 的 1000 行硬閘門第二次擋下「再加一點」(這次是分析保險絲的
finding)。工單說得很清楚:不准再調高數字,先把 SLA 判定搬到 `delivery_sla.py`。
搬了。這個檔擋住它漂回來:判準本體在 `delivery_sla.assess_delivery`,
`run_quality.assess` 只委派 —— 兩處都有判定就會分歧,而分歧的症狀是
「同一份 state 兩種結論」(2026-09-01 r8 外審實際踩過)。
"""
import ast
import datetime as dt
from pathlib import Path

import delivery_sla as ds
import run_quality as rq

_ROOT = Path(rq.__file__).resolve().parent


def _findings_for(manifest: dict, *, digest: bool = False) -> list:
    out = []
    ds.assess_delivery(manifest, lambda c, s, d: out.append((c, s, d)), digest=digest)
    return out


def test_the_extracted_judgment_still_catches_a_late_letter():
    """行為不變:遲到的信在新家一樣被判 `delivery_sla_missed`(defect)。"""
    m = {"date": "2026-09-05 06:58", "manifest_schema": ds._CURRENT_MANIFEST_SCHEMA,
         "delivery": {"success": True, "attempted": True,
                      "delivered_at": "2026-09-05T09:26:31+08:00",
                      "first_delivered_at": "2026-09-05T09:26:31+08:00"}}
    codes = {c for c, _s, _d in _findings_for(m)}
    assert "delivery_sla_missed" in codes, codes


def test_the_extracted_judgment_passes_an_on_time_letter():
    m = {"date": "2026-09-05 05:07", "manifest_schema": ds._CURRENT_MANIFEST_SCHEMA,
         "delivery": {"success": True, "attempted": True,
                      "delivered_at": "2026-09-05T07:26:31+08:00",
                      "first_delivered_at": "2026-09-05T07:26:31+08:00"}}
    assert _findings_for(m) == []


def test_run_quality_delegates_instead_of_judging():
    """判定只住一處:`assess()` 呼叫 `assess_delivery`,而且自己不再產生 SLA finding。"""
    tree = ast.parse((_ROOT / "run_quality.py").read_text(encoding="utf-8"))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "assess")
    calls = [c for c in ast.walk(fn) if isinstance(c, ast.Call)]
    assert any(getattr(c.func, "id", "") == "assess_delivery" for c in calls), \
        "run_quality.assess 沒有委派給 delivery_sla.assess_delivery"
    # SLA finding 的字面值不得再出現在 run_quality(否則就是第二份判定)
    src = (_ROOT / "run_quality.py").read_text(encoding="utf-8")
    for code in ("delivery_sla_missed", "run_delivered_after_target",
                 "first_delivered_at_out_of_range", "delivered_at_invalid"):
        assert f'"{code}"' not in src, f"{code} 又回到 run_quality 了 —— 判定要只住一處"
    sla_src = (_ROOT / "delivery_sla.py").read_text(encoding="utf-8")
    assert '"delivery_sla_missed"' in sla_src


def test_the_whole_verdict_is_reachable_through_assess():
    """委派要真的接上:透過 `run_quality.assess` 也拿得到同一個 finding。"""
    m = {"date": "2026-09-05 06:58", "manifest_schema": ds._CURRENT_MANIFEST_SCHEMA,
         "llm": {"analysis_origin": "luna_specialized"},
         "delivery": {"success": True, "attempted": True,
                      "delivered_at": "2026-09-05T09:26:31+08:00",
                      "first_delivered_at": "2026-09-05T09:26:31+08:00"}}
    codes = {f["code"] for f in rq.assess(m)}
    assert "delivery_sla_missed" in codes, sorted(codes)


def test_the_deadline_is_still_taipei_nine_oclock():
    """搬家不得順手改期限。"""
    assert (ds.SLA_HOUR, ds.SLA_MINUTE) == (9, 0)
    assert ds.SLA_TZ.utcoffset(None) == dt.timedelta(hours=8)
