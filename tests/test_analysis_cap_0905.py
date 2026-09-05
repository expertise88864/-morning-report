# -*- coding: utf-8 -*-
"""**分析文字的保險絲每天靜默腰斬九、十段**(2026-09-05,使用者連問兩天)。

`_cap_analysis_text` 的舊上限 6,000 字是 2026-07 為「4–5k 字的散文分析」設的。
特化渲染器(2026-08-19 起)一張新聞卡就約 560 字純文字,正常日的完整輸出約
17,000 字 —— 於是 09-02〜09-05 四天信裡的分析區純文字全都是 ~5,500 字(那就是
天花板):十、總體經濟從來沒進過信;九段先前被截到只剩 4–6 張;9/4 世界大事改
五條後切點前移,九段整段落在切點之後。

9/4 我把「九段消失」歸咎於空正文卡被丟。那件事是真的(18 → 7),但不是主因:
9/5 渲染端 diag 說 18/18 都渲染了,九段照樣不見 —— 主因是**渲染之後**這道
保險絲,而它截了什麼、截掉多少,manifest 與品質判準沒有一個字。

這個檔用**生產量級**的物件跑**真的渲染器**(尺寸是從 9/5 實信量出來的),
釘住三件事:舊上限確實會在八段中間切斷(重現事故)、新上限讓整封信完整、
以及截斷永遠不再靜默。
"""
import ast
import copy
from pathlib import Path

import pytest

import analysis_render as ar
import degradation_registry as dr
import finding_domains as fd
import fixtures_analysis as fx
import morning_report as mr
import run_quality as rq

_ROOT = Path(mr.__file__).resolve().parent
NL = chr(10)

#: **量出來的尺寸**(2026-09-05 實信,純文字字元):
#:   八段 5 張卡 2,810 字 → 每張約 560(小標+正文+傳導+標的+等級),正文取 380;
#:   七之二 5 條 888 字 → 每條約 180;七之三 2 件 621 字;七之四 4 條 818 字。
_CARD_BODY = 380
_WORLD_WHAT, _WORLD_WHY, _WORLD_NEXT = 60, 90, 60
_SCEN_FIELD = 60
_DELTA_EVIDENCE = 120
_MACRO_FACET = 250

#: prompt 給模型的上限:八段最多 12 張、九段最多 10 張
_MAX_TECH, _MAX_OTHER = 12, 10
#: 9/5 實信的形狀:18 張(9 + 9)
_TYPICAL_TECH, _TYPICAL_OTHER = 9, 9


def _pad(prefix: str, n: int) -> str:
    """把欄位撐到 n 個字(內容與判準無關,只是尺寸)。"""
    s = prefix
    filler = "這一句是把欄位撐到生產量級的說明文字,內容與判準無關。"
    while len(s) < n:
        s += filler
    return s[:n]


def _packet(n_tech: int, n_other: int) -> dict:
    news = []
    for i in range(n_tech):
        news.append({"source_item_id": f"t{i}", "title": f"台積電 CoWoS 產能再擴一倍(第 {i} 則)",
                     "summary": "先進封裝擴產。", "source_name": "鉅亨網", "source_grade": "B",
                     "entities": ["2330"], "published_at": "2026-09-04T20:00:00+08:00"})
    for i in range(n_other):
        news.append({"source_item_id": f"o{i}", "title": f"富邦金 8 月獲利創新高(第 {i} 則)",
                     "summary": "壽險與銀行雙引擎。", "source_name": "經濟日報", "source_grade": "B",
                     "entities": ["2881"], "published_at": "2026-09-04T20:00:00+08:00"})
    return {"news": news, "tw_universe": [
        {"code": "2330", "name": "台積電", "industry": "半導體業",
         "desc": "台積電 — 全球晶圓代工龍頭"},
        {"code": "2881", "name": "富邦金", "industry": "金融保險業",
         "desc": "富邦金 — 金控龍頭"}]}


def _card(template: dict, sid: str, asset: str) -> dict:
    c = copy.deepcopy(template)
    c["source_item_id"] = sid
    c["why_it_matters"] = _pad(f"這則新聞({sid})為什麼重要:", _CARD_BODY)
    c["invalidation_signal"] = "下個月營收月減逾一成"
    c["affected_assets"] = [{"asset_id": asset, "direction": "bullish",
                             "magnitude_band": "moderate", "horizon": "1-5d",
                             "first_order_effect": _pad("一階影響:", 40),
                             "second_order_effect": _pad("二階影響:", 40),
                             "evidence_ids": [sid]}]
    return c


def production_scale_analysis(n_tech: int, n_other: int) -> dict:
    """一份**生產量級**的分析物件:每段都有料、每張卡都是實信的尺寸。"""
    obj = fx.valid_analysis()
    tpl = obj["top_news_analysis"][0]
    obj["top_news_analysis"] = ([_card(tpl, f"t{i}", "2330") for i in range(n_tech)]
                                + [_card(tpl, f"o{i}", "2881") for i in range(n_other)])
    obj["world_events"] = [
        {"what": _pad(f"世界大事 {i}:", _WORLD_WHAT),
         "why_it_matters": _pad("為什麼重要:", _WORLD_WHY),
         "what_next": _pad("後續可能影響:", _WORLD_NEXT), "evidence_ids": ["n1"]}
        for i in range(5)]
    obj["upcoming_event_scenarios"] = [
        {"when": "9 月 10 日(週四)", "event": f"關鍵事件 {i}",
         "base_expectation": _pad("基準:", _SCEN_FIELD), "bull_case": _pad("偏多:", _SCEN_FIELD),
         "bear_case": _pad("偏空:", _SCEN_FIELD), "most_affected": "2330、00662",
         "invalidation": _pad("失效條件:", 40)}
        for i in range(2)]
    obj["narrative_delta"] = [
        {"prior_view": _pad(f"昨日觀點 {i}:", 80), "change": "強化",
         "evidence_today": _pad("今日新證據:", _DELTA_EVIDENCE)}
        for i in range(4)]
    obj["macro_environment"] = {
        k: {"analysis": _pad(f"{k} 切面:", _MACRO_FACET), "evidence_ids": ["n1"]}
        for k in ("us_rates_fx_vix", "fed_policy", "geopolitics")}
    for k in ("base", "bull", "bear"):
        obj["scenario_tree"][k]["narrative"] = _pad(f"{k} 情境:", 120)
        obj["scenario_tree"][k]["triggers"] = ["觸發條件一", "觸發條件二"]
    obj["data_gaps"] = [{"gap_id": f"g{i}", "what_is_missing": _pad(f"缺口 {i}:", 40),
                         "impact_on_conclusions": _pad("對結論的影響:", 40)}
                        for i in range(8)]
    obj["contradictions"] = [{"topic": f"證據衝突 {i}", "resolution": _pad("調和:", 80)}
                             for i in range(2)]
    return obj


def _render(n_tech: int, n_other: int):
    diag: dict = {}
    text = ar.render(production_scale_analysis(n_tech, n_other),
                     _packet(n_tech, n_other), diag=diag)
    assert text, "渲染器回空 —— 物件不合它的前提,這個檔量不到東西"
    return text, diag


def test_the_builder_really_produces_both_sections():
    """先確認 fixture 自己成立:九段真的有卡(否則下面全在守一個空集合)。"""
    text, diag = _render(_TYPICAL_TECH, _TYPICAL_OTHER)
    assert diag["rendered_tech"] == _TYPICAL_TECH and diag["rendered_other"] == _TYPICAL_OTHER
    assert diag["dropped"] == []
    for sec in (ar.SECTION_TECH, ar.SECTION_OTHER, ar.SECTION_MACRO, ar.SECTION_WORLD):
        assert f"## {sec}" in text, sec


def test_the_old_fuse_cut_the_letter_inside_section_eight():
    """**重現 09-04 / 09-05 的信**:18 張卡的正常日,6,000 字的上限在八段中間切斷 ——
    八段只剩幾張、九段與十段整段消失,而渲染端的 diag 仍說 18/18 都渲染了。"""
    text, _ = _render(_TYPICAL_TECH, _TYPICAL_OTHER)
    assert len(text) > 6000, f"生產量級的輸出只有 {len(text)} 字?尺寸常數量錯了"
    old = mr._cap_analysis_text(text, max_chars=6000)
    assert f"## {ar.SECTION_TECH}" in old
    assert f"## {ar.SECTION_OTHER}" not in old, "舊上限下九段應該整段消失(這正是事故)"
    assert f"## {ar.SECTION_MACRO}" not in old
    assert old.count("傳導:") < _TYPICAL_TECH, "舊上限下八段應該只剩幾張卡(9/5 實信:5 張)"


def test_the_default_fuse_keeps_the_whole_letter():
    """正常日與 prompt 上限的滿載日,預設保險絲都要讓整封信完整。"""
    for n_t, n_o in ((_TYPICAL_TECH, _TYPICAL_OTHER), (_MAX_TECH, _MAX_OTHER)):
        text, diag = _render(n_t, n_o)
        kept = mr._cap_analysis_text(text)
        assert kept == text, f"{n_t}+{n_o} 張卡被截了:{len(text)} → {len(kept)}"
        for sec in (ar.SECTION_OTHER, ar.SECTION_MACRO, "情境與觸發條件", "資料缺口"):
            assert sec in kept, sec
        assert kept.count("傳導:") == n_t + n_o


def test_the_fuse_sits_above_the_legitimate_maximum():
    """**門檻要量,不要推**:上限必須是「滿載日的完整輸出」的倍數,而且仍然是
    一道保險絲(不是無限大)。誰把它調回 6,000,這條就紅。"""
    text, _ = _render(_MAX_TECH, _MAX_OTHER)
    assert mr.ANALYSIS_TEXT_FUSE >= 1.5 * len(text), \
        f"保險絲 {mr.ANALYSIS_TEXT_FUSE} 對滿載輸出 {len(text)} 字沒有餘裕"
    assert mr.ANALYSIS_TEXT_FUSE <= 100_000, "保險絲要仍然是保險絲"


def test_a_cut_is_never_silent():
    """截了就要說得出:多少字、上限多少、剩多少、**哪幾段整段沒了**。"""
    text, _ = _render(_TYPICAL_TECH, _TYPICAL_OTHER)
    diag: dict = {}
    out = mr._cap_analysis_text(text, max_chars=6000, diag=diag)
    assert diag["chars"] == len(text) and diag["limit"] == 6000 and diag["kept"] == len(out)
    assert ar.SECTION_OTHER in diag["lost_sections"]
    assert ar.SECTION_MACRO in diag["lost_sections"]
    assert ar.SECTION_TECH not in diag["lost_sections"], "八段還在,不該報成消失"
    # 沒截就什麼都不寫 —— 「有 diag 就是有截」要成立,消費端才不必再猜
    quiet: dict = {}
    assert mr._cap_analysis_text("短文", diag=quiet) == "短文" and quiet == {}


def test_the_cut_reaches_the_manifest_and_the_quality_verdict():
    """留痕要走到底:降級標籤、manifest、run_quality 的 defect —— 三處缺一,
    下一次還是要等使用者問「為何消失」才有人知道。"""
    text, _ = _render(_TYPICAL_TECH, _TYPICAL_OTHER)
    diag: dict = {}
    mr._cap_analysis_text(text, max_chars=6000, diag=diag)
    saved_llm = mr._RUN_MANIFEST.get("llm")
    had_label = "render:analysis_capped" in mr._DEGRADED_STEPS
    try:
        mr._note_analysis_capped(diag)
        assert "render:analysis_capped" in mr._DEGRADED_STEPS
        rec = mr._RUN_MANIFEST["llm"]["analysis_cap"]
        assert rec["kept"] < rec["chars"] and ar.SECTION_OTHER in rec["lost_sections"]
        # 同一班截兩次不該登記成兩條(watchdog 信裡會重複)
        mr._note_analysis_capped(diag)
        assert mr._DEGRADED_STEPS.count("render:analysis_capped") == 1
    finally:
        if not had_label:
            while "render:analysis_capped" in mr._DEGRADED_STEPS:
                mr._DEGRADED_STEPS.remove("render:analysis_capped")
        if saved_llm is None:
            mr._RUN_MANIFEST.pop("llm", None)
        else:
            mr._RUN_MANIFEST["llm"] = saved_llm

    # 品質判準:用 run_quality 自己的健康 manifest 加上這筆紀錄
    import test_run_quality as _trq
    m = _trq._ok_manifest()
    m.setdefault("llm", {})["analysis_cap"] = dict(rec)
    m["degraded_steps"] = list(m.get("degraded_steps") or []) + ["render:analysis_capped"]
    findings = {f.get("code") if isinstance(f, dict) else getattr(f, "code", None): f
                for f in rq.assess(m)}
    hit = findings.get("analysis_capped")
    assert hit is not None, f"沒有 analysis_capped:{sorted(k for k in findings if k)}"
    sev = hit.get("severity") if isinstance(hit, dict) else getattr(hit, "severity", None)
    assert sev == "defect", sev
    detail = str(hit.get("detail") if isinstance(hit, dict) else getattr(hit, "detail", ""))
    assert ar.SECTION_OTHER in detail, detail
    # 標籤與 finding 都要登記(否則會被報成「沒見過的降級步驟」)
    assert "render:analysis_capped" in dr.KNOWN_DEGRADED
    assert fd.finding_domain("analysis_capped") == fd.DOMAIN_CONTENT
    assert "unknown_degradation" not in findings


def test_render_html_actually_wires_the_diag():
    """沒接上去的話,上面全是在守一個不會執行的東西。用 AST 找真正的呼叫。"""
    tree = ast.parse((_ROOT / "morning_report.py").read_text(encoding="utf-8"))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "render_html")
    calls = [c for c in ast.walk(fn) if isinstance(c, ast.Call)]
    cap = [c for c in calls if getattr(c.func, "id", "") == "_cap_analysis_text"]
    assert cap, "render_html 沒有呼叫 _cap_analysis_text"
    assert all(any(k.arg == "diag" for k in c.keywords) for c in cap), \
        "呼叫沒帶 diag —— 截了就沒人知道"
    assert any(getattr(c.func, "id", "") == "_note_analysis_capped" for c in calls), \
        "截了但沒登記"


def test_the_measured_sizes_still_match_a_real_letter():
    """尺寸常數是從 9/5 實信量的;有存檔就對一次,免得常數慢慢變成裝飾。
    存檔不在(全新 repo)不算通過 —— 那就明說量不到。"""
    arc = _ROOT / "state" / "emails" / "2026-09-05.html.gz"
    if not arc.exists():
        pytest.skip("2026-09-05 的信件存檔不在這個 checkout 裡(量不到,不是通過)")
    import gzip
    import re
    html = gzip.decompress(arc.read_bytes()).decode("utf-8", "replace")
    i = html.find(ar.SECTION_TECH)
    j = html.find("台股波段觀察名單", i)
    plain = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html[i:j]))
    cards = plain.count("傳導:")
    assert cards >= 3
    per_card = len(plain) / cards
    assert 400 <= per_card <= 800, f"實信每張卡 {per_card:.0f} 字,與常數量級不符"


def _reset_cap_marks():
    """把這一輪測試留下的登記清乾淨(manifest 與降級清單都是模組層級的)。"""
    (mr._RUN_MANIFEST.get("llm") or {}).pop("analysis_cap", None)
    while "render:analysis_capped" in mr._DEGRADED_STEPS:
        mr._DEGRADED_STEPS.remove("render:analysis_capped")


def test_the_minimal_fallback_letter_is_capped_too():
    """**備援路徑不得繞過保險絲**(Codex 2026-09-05 r1 P2)。

    主渲染炸掉時 `_phase_render` 把**原文**交給極簡信;沒有這道的話,超長文
    照寄、而且 manifest 若已被主路徑登記成「截了」,就在對一份沒寄出的文字說話。
    """
    text, _ = _render(_TYPICAL_TECH, _TYPICAL_OTHER)
    # 疊到超過保險絲:每份都以九段結尾,最後一份的九段一定在切點之後
    huge = (text + NL + NL) * (mr.ANALYSIS_TEXT_FUSE // len(text) + 2)
    assert len(huge) > mr.ANALYSIS_TEXT_FUSE
    saved_llm = mr._RUN_MANIFEST.get('llm')
    try:
        _reset_cap_marks()
        html = mr._render_minimal_html({"STANCE_PY": {"total": 6, "label": "偏多"}},
                                       {}, {}, huge, "2026-09-05", "每日報")
        rec = mr._RUN_MANIFEST['llm']['analysis_cap']
        assert rec['chars'] == len(huge) and rec['kept'] <= mr.ANALYSIS_TEXT_FUSE
        assert "render:analysis_capped" in mr._DEGRADED_STEPS
        # 信裡的字數要對得上登記的 kept,不是原文
        assert len(html) < len(huge)
    finally:
        _reset_cap_marks()
        if saved_llm is not None:
            mr._RUN_MANIFEST['llm'] = saved_llm


def test_the_minimal_fallback_retracts_a_claim_about_text_it_did_not_send():
    """主路徑登記「截了」之後才炸、極簡信寄的是全文 → 那筆宣稱要撤掉。"""
    saved_llm = mr._RUN_MANIFEST.get('llm')
    try:
        _reset_cap_marks()
        mr._RUN_MANIFEST.setdefault("llm", {})["analysis_cap"] = {
            "chars": 99_999, "limit": 6000, "kept": 5900, "lost_sections": ["九、其他類股資訊"]}
        mr._DEGRADED_STEPS.append("render:analysis_capped")
        mr._render_minimal_html({}, {}, {}, "## 八、科技板塊脈動" + NL + NL + "短文", "2026-09-05", "每日報")
        assert "analysis_cap" not in (mr._RUN_MANIFEST.get("llm") or {})
        assert "render:analysis_capped" not in mr._DEGRADED_STEPS
    finally:
        _reset_cap_marks()
        if saved_llm is not None:
            mr._RUN_MANIFEST['llm'] = saved_llm


def test_the_fallback_renderer_is_wired_to_the_fuse():
    """AST:極簡渲染器本體呼叫保險絲(帶 diag)並登記 —— 任何呼叫端都因此被涵蓋。"""
    tree = ast.parse((_ROOT / "morning_report.py").read_text(encoding="utf-8"))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_render_minimal_html")
    calls = [c for c in ast.walk(fn) if isinstance(c, ast.Call)]
    cap = [c for c in calls if getattr(c.func, "id", "") == "_cap_analysis_text"]
    assert cap and all(any(k.arg == "diag" for k in c.keywords) for c in cap)
    assert any(getattr(c.func, "id", "") == "_note_analysis_capped" for c in calls)
    # 而 _phase_render 的備援分支真的把原文交給它(接線存在才有東西可守)
    ph = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_phase_render")
    assert any(getattr(c.func, "id", "") == "_render_minimal_html" for c in ast.walk(ph) if isinstance(c, ast.Call))
