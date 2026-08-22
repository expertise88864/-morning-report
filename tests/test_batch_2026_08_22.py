# -*- coding: utf-8 -*-
"""2026-08-22 生產事故:修補請求爆掉硬閘門 → 整條特化路徑落 legacy。

當天 watchdog 四項:analysis_not_specialized / luna_rejected /
final_request_over_budget(defect)/ unknown_degradation。
修法:① 修補輪 slim payload(裝不下才切;量測與閘門同一把尺);
② `llm:luna_path_failed:*` 家族列為已知降級,失敗原因改由
analysis_not_specialized 帶出;③ sector:institutional_missing 補註冊
(2026-08-21 批新增的標籤,漏了註冊 → T86 缺席日必誤報 unknown)。
"""
import io
import json
from pathlib import Path

import morning_report as mr
import payload_budget as pb
import run_quality as rq


def test_measure_request_is_the_gate_ruler():
    """送出前的量測與硬閘門必須同一把尺,否則決策在閘門前後各說各話。"""
    body = {"model": "m", "input": "中文" * 10}
    assert pb.measure_request(body) == len(
        json.dumps(body, ensure_ascii=False, default=str))
    assert pb.request_gate(dict(body)) == pb.measure_request(body)


def test_repair_payload_keeps_full_packet_when_it_fits(monkeypatch):
    """裝得下的日子行為不變:完整資料包 + 修正指示,不切 slim。"""
    monkeypatch.setattr(mr._ep, "evidence_ids", lambda p: ["market:QQQ"])
    out, rec = mr._repair_request_payload({"model": "m"}, "PACKET", "\nTAIL", {})
    assert rec is None
    assert out["input"] == "PACKET\nTAIL"


def test_repair_payload_slims_when_over_gate(monkeypatch):
    """08/22 生產:99.1 萬資料包 + 11 萬修正尾 = 110.3 萬 > 110 萬閘門。
    slim 丟資料包、留底本與合法 ID 全集,而且 slim 後要過得了真閘門。"""
    monkeypatch.setattr(mr._ep, "evidence_ids",
                        lambda p: ["market:QQQ", "news:167811"])
    fat = "x" * pb.MAX_REQUEST_CHARS          # 光資料包就頂到閘門
    out, rec = mr._repair_request_payload({"model": "m"}, fat, "\nTAIL", {})
    assert rec is not None, "超標卻沒切 slim"
    assert rec["full_chars"] > pb.MAX_REQUEST_CHARS
    assert rec["slim_chars"] <= pb.MAX_REQUEST_CHARS, "slim 後仍超標"
    assert "TAIL" in out["input"], "底本(修正指示)被丟掉了"
    assert "market:QQQ" in out["input"] and "news:167811" in out["input"]
    assert "xxxx" not in out["input"], "slim 還帶著資料包"
    assert pb.request_gate(dict(out)) == rec["slim_chars"]


def test_repair_payload_slim_survives_index_failure(monkeypatch):
    """ID 索引生不出來時 slim 照走(修補不可因索引壞掉而回到必死路徑)。"""
    def _boom(p):
        raise RuntimeError("no ids")
    monkeypatch.setattr(mr._ep, "evidence_ids", _boom)
    fat = "x" * pb.MAX_REQUEST_CHARS
    out, rec = mr._repair_request_payload({"model": "m"}, fat, "\nTAIL", {})
    assert rec is not None and "TAIL" in out["input"]
    assert "索引生成失敗" in out["input"]


def test_luna_loop_wires_the_helper():
    """接線:修補輪真的走 helper 並留 manifest 痕跡 —— 沒接上等於不存在。"""
    src = io.open(Path(mr.__file__), encoding="utf-8").read()
    i = src.index("_hints = _repair_evidence_hints(_base_problems, packet)")
    seg = src[i:i + 1600]
    assert "_repair_request_payload(" in seg, "修補輪沒接 slim helper"
    assert "repair_payload_slim" in seg, "slim 沒有 manifest 痕跡"
    assert 'input=(\n            bundle["user_payload"]' not in seg, "舊形狀殘留"


def _manifest(steps, origin="legacy_fallback_after_luna_failure", err=None):
    m = {"degraded_steps": list(steps),
         "llm": {"analysis_origin": origin}}
    if err:
        m["llm"]["luna_path_error"] = {"error": err}
    return m


def test_luna_path_failed_family_is_a_known_degradation():
    """後綴是例外類名(開放集),frozenset 列舉不完 —— 家族整個列為已知;
    失敗原因改騎在 analysis_not_specialized 上,不因豁免而消失。"""
    f = rq.assess(_manifest(["llm:luna_path_failed:PayloadBudgetExceeded"],
                            err="PayloadBudgetExceeded: 1103248 > 1100000"))
    codes = {x["code"] for x in f}
    assert "unknown_degradation" not in codes, f
    d = [x for x in f if x["code"] == "analysis_not_specialized"]
    assert d and "PayloadBudgetExceeded" in d[0]["detail"], f
    # 豁免只給這個家族:別的新標籤照樣要被抓(守衛不得順手 no-op)
    f2 = rq.assess(_manifest(["llm:brand_new_thing"]))
    assert "unknown_degradation" in {x["code"] for x in f2}


def test_sector_institutional_missing_is_registered():
    assert "sector:institutional_missing" in rq.KNOWN_DEGRADED


def test_emergency_fallback_also_carries_the_luna_reason():
    """外審 r1 P2:Luna 失敗後 legacy 再失敗 → origin=emergency_fallback,
    走的是 analysis_emergency 分支 —— 家族豁免關掉 unknown 管道後,
    原因必須也騎在這一筆上,否則最嚴重的那種日子反而沒有原因。"""
    f = rq.assess(_manifest(["llm:luna_path_failed:PayloadBudgetExceeded"],
                            origin="emergency_fallback",
                            err="PayloadBudgetExceeded: 1103248 > 1100000"))
    codes = {x["code"] for x in f}
    assert "unknown_degradation" not in codes, f
    d = [x for x in f if x["code"] == "analysis_emergency"]
    assert d and "PayloadBudgetExceeded" in d[0]["detail"], f
