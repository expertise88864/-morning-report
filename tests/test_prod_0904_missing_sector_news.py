# -*- coding: utf-8 -*-
"""2026-09-04 實信兩件事。

1. 「其他類股資訊都不見了」:模型分析 18 則新聞(manifest `news_analyzed`),信裡只有
   7 則、「九、其他類股資訊」整段消失;9/2 同型(19 → 12)。渲染鏈唯一的丟卡出口是
   `_news_line` 對空的 `why_it_matters` 回空、`_blocks` 不排 —— 而 schema 的字串型別
   不擋空字串,昨晚的 LM-2 也漏了這一欄。修三處:驗證器擋空正文(讓修補輪去補)、
   渲染器把每一則的結果記進 manifest、`run_quality` 給專屬 finding。
2. 「資金輪動要清楚、詳細」:從四顆膠囊改成一張完整的表(每一類股一列,
   與當日類股熱度合成)。
"""
from pathlib import Path

import analysis_render as ar
import analysis_schema as sch
import fixtures_analysis as fx
import morning_report as mr
import render_utils as ru

_ROOT = Path(mr.__file__).resolve().parent


# ---------------------------------------------------------------- 1. 空正文的卡
def test_the_validator_rejects_a_news_card_with_an_empty_body():
    obj = fx.valid_analysis()
    obj["top_news_analysis"][0]["why_it_matters"] = ""
    sid = obj["top_news_analysis"][0]["source_item_id"]
    probs = sch.validate(obj, fx.ids())
    assert any("why_it_matters" in p and sid in p and "整張卡丟掉" in p for p in probs), probs
    assert not [p for p in sch.validate(fx.valid_analysis(), fx.ids()) if "why_it_matters" in p]


def test_the_renderer_records_what_it_dropped_and_the_metric_no_longer_lies_alone():
    obj = fx.valid_analysis()
    n = len(obj["top_news_analysis"])
    assert n >= 2, "fixture 至少要兩則才測得到「一則被丟、其餘照排」"
    victim = obj["top_news_analysis"][1]
    victim["why_it_matters"] = "   "
    diag = {}
    md = ar.render(obj, packet=None, diag=diag)
    assert md, "渲染不得因為一則空正文而整份失敗"
    assert diag["analyzed"] == n
    assert diag["rendered_tech"] + diag["rendered_other"] == n - 1
    assert [d["sid"] for d in diag["dropped"]] == [victim["source_item_id"]]
    assert diag["dropped"][0]["why_chars"] == 0 and diag["dropped"][0]["section"] in ("tech", "other")
    # 沒傳 diag 也不炸(其他呼叫端)
    assert ar.render(obj, packet=None)


def test_dropped_cards_become_a_defect_finding():
    import finding_domains as fd
    import run_quality as rq
    from test_run_quality import _ok_manifest
    m = _ok_manifest()
    m.setdefault("llm", {})["news_render"] = {
        "analyzed": 18, "rendered_tech": 7, "rendered_other": 0,
        "dropped": [{"sid": "n2881", "section": "other", "rendered": False, "why_chars": 0},
                    {"sid": "n2609", "section": "other", "rendered": False, "why_chars": 0}]}
    codes = {f["code"]: f for f in rq.assess(m)}
    assert codes["news_cards_dropped"]["severity"] == "defect", codes
    assert "18 則" in codes["news_cards_dropped"]["detail"] and "n2881" in codes["news_cards_dropped"]["detail"]
    assert fd.finding_domain("news_cards_dropped") == fd.DOMAIN_CONTENT
    m2 = _ok_manifest()
    m2.setdefault("llm", {})["news_render"] = {"analyzed": 18, "rendered_tech": 12,
                                              "rendered_other": 6, "dropped": []}
    assert "news_cards_dropped" not in {f["code"] for f in rq.assess(m2)}


def test_both_render_call_sites_pass_the_diag():
    src = (_ROOT / "morning_report.py").read_text(encoding="utf-8")
    calls = [i for i in range(len(src)) if src.startswith("_ar.render(obj, packet", i)]
    assert len(calls) == 2, calls
    for i in calls:
        assert 'diag=_RUN_MANIFEST["llm"].setdefault("news_render", {})' in src[i:i + 400], src[i:i + 300]


# ---------------------------------------------------------------- 2. 資金輪動表
def _snap():
    rows = []
    for ind, p5s in (("半導體業", [8.0, 6.5, 5.0, 3.0]), ("金融保險業", [9.1, 8.4, 7.0]),
                     ("航運業", [2.0, -1.0, 4.0]), ("電機機械", [-7.0, -6.7, -5.0]),
                     ("光電業", [4.0, 3.9, -0.5])):
        rows += [{"industry": ind, "pct_5d": p} for p in p5s]
    rows.append({"industry": "未分類", "pct_5d": 50.0})
    return rows


def _heat():
    return {"sectors": {
        "半導體業": {"n": 60, "up": 10, "down": 45, "median_pct": -2.5, "value_yi": 3311,
                    "value_share_pct": 35.5, "inst_net_yi": -287.9,
                    "leaders": [{"code": "2408", "name": "南亞科", "pct": -7.8, "value_yi": 300}]},
        "金融保險業": {"n": 30, "up": 20, "down": 5, "median_pct": 1.1, "value_yi": 579,
                      "value_share_pct": 6.2, "inst_net_yi": 41.0,
                      "leaders": [{"code": "2885", "name": "元大金<b>", "pct": 2.7, "value_yi": 80}]},
    }, "ranked": ["半導體業", "金融保險業"], "total_value_yi": 9401}


def test_the_rotation_carries_a_full_table_not_just_chips():
    rot = mr._sector_rotation(_snap())
    table = rot["table"]
    assert [r["industry"] for r in table] == ["金融保險業", "半導體業", "光電業", "航運業", "電機機械"]
    assert table[0]["up_5d"] == 3 and table[0]["members"] == 3
    assert table[-1]["up_5d"] == 0 and table[-1]["median_5d"] < 0
    assert "未分類" not in {r["industry"] for r in table}
    assert rot["strong"] and rot["weak"]                  # 既有欄位不動


def test_the_rotation_table_renders_every_sector_with_today_context():
    rot = mr._sector_rotation(_snap())
    html = ru._render_sector_rotation_table(rot, _heat())
    body = html[html.index("<table"):]
    order = [ind for ind in ("金融保險業", "半導體業", "光電業", "航運業", "電機機械")]
    pos = [body.index(ind) for ind in order]
    assert pos == sorted(pos), "要依相對大盤由強到弱排"
    assert "▲" in html and "▼" in html                     # 強勢 / 轉弱標記
    assert "35.5%" in html and "法人 -288 億" in html       # 全市場口徑的今日欄
    assert "領漲 2408 南亞科 -7.8%" in html
    assert "元大金&lt;b&gt;" in html and "元大金<b>" not in html   # 外部名稱 escape
    assert body.count("<tr>") == 6                          # 表頭 + 5 類股
    assert "4/4" in html and "2/3" in html                  # 半導體 4 檔全漲;航運 3 檔漲 2 檔
    # 熱度表沒有的類股:今日欄留「—」,不硬湊
    row = body[body.index("航運業"):]
    row = row[:row.index("</tr>")]
    assert "—" in row
    assert "大盤中位" in html and "全市場口徑" in html


def test_the_rotation_table_is_wired_into_the_top5_card():
    src = (_ROOT / "morning_report.py").read_text(encoding="utf-8")
    assert '_render_sector_rotation_table(\n                _rot, quotes.get("SECTOR_HEAT") or {})' in src
    assert "_rot_chip" not in src, "舊的膠囊渲染還留著"
    assert ru._render_sector_rotation_table({}, {}) == "" and ru._render_sector_rotation_table({"table": []}, None) == ""
