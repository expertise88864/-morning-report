# -*- coding: utf-8 -*-
"""**第二十輪:確定性的誤併、矛盾的判準、接錯線的指標。**

這一輪最有教育意義的三條:

  * **確定性不等於正確** —— 上一輪把分群修成順序無關,而它是
    「確定性地誤併」:A~B、B~C、A≁C 的橋接形狀每次都併成一群。
  * **兩個判準可以同時成立而互相矛盾** —— `event → operations → price
    → revenue` 同時算「完整」與「順序錯誤」,信裡什麼都不說。
  * **指標接錯線與指標不存在,對判讀的人是同一件事** ——
    `event_fingerprint_coverage` 需要 rendered text,而生產呼叫端沒傳,
    於是無論信寫得多完整,十配對帳本都會顯示事件覆蓋 0%。

外加一條**宣稱與實作不符**:`news_normalize` 的註解寫「沒有循環」,
而循環是真的(先 import 它就炸)。宣稱要回頭驗。
"""
import analysis_depth as ad
import analysis_metrics as am
import analysis_schema as sch
import analysis_stages as ast_
import evidence_packet as ep
import evidence_registry as er
import fixtures_analysis as fx
import news_clusters as nc

_IDS = fx.ids()


# ---------------------------------------------------------------- 分群

def test_a_bridge_does_not_merge_two_different_events():
    """**P1-2**:single-link 被橋接串起來 —— 兩件不同的事壓成一群,
    其中一件還會因「同一群只能分析一次」被驗證器強迫省略。"""
    bridge = [
        {"source_item_id": "n1", "title": "台積電熊本廠恢復地震前產出水準",
         "entities": ["台積電"]},
        {"source_item_id": "n2", "title": "台積電熊本廠恢復正常",
         "entities": ["台積電"]},
        {"source_item_id": "n3", "title": "台積電恢復正常出貨並上修展望",
         "entities": ["台積電"]},
    ]
    got = [c["member_source_ids"] for c in nc.clusters(bridge)]
    assert ["n1", "n2"] in got and ["n3"] in got, got
    # 順序無關仍然成立(上一輪修的性質不得被這一輪打掉)
    assert nc.clusters(bridge) == nc.clusters(list(reversed(bridge)))


def test_direct_import_of_news_normalize_does_not_explode():
    """**註解宣稱「沒有循環」,而循環是真的。** 修完要能從任一側 import。"""
    import importlib
    import sys
    for mod in ("news_normalize", "evidence_packet"):
        sys.modules.pop(mod, None)
    import news_normalize
    importlib.reload(news_normalize)
    assert callable(news_normalize.normalize_news)


# ---------------------------------------------------------------- 順序

def test_complete_and_out_of_order_cannot_both_be_true():
    """**P1-5**:`event → operations → price → revenue` 先前同時算
    「完整」與「順序錯誤」—— 兩個矛盾的標籤,信裡什麼都不說。"""
    obj = fx.valid_analysis()
    steps, prev = [], "起點"
    for st in ("event", "operations", "price", "revenue"):
        nxt = f"{st}果"
        steps.append({"from_what": prev, "to_what": nxt, "channel": "c",
                      "stage": st, "step_type": "inference", "evidence_ids": []})
        prev = nxt
    obj["top_news_analysis"][0]["mechanism_steps"] = steps
    n = obj["top_news_analysis"][0]
    assert ast_._stage_order_broken(n)
    assert not ast_._ordered_chain(n), "倒退的鏈不得算完整"
    assert ast_.incomplete_chains(obj), "而且要進揭露"


# ---------------------------------------------------------------- 接線

def test_the_fingerprint_metric_actually_receives_the_letter():
    """**P1-3**:生產呼叫端不傳 rendered text,事件覆蓋永遠 0 ——
    無論信寫得多完整,十配對帳本都顯示 Luna 失敗。"""
    pk = ep.build({}, {}, {}, [
        {"source_item_id": "n1", "title": "央行理監事會決議升息半碼",
         "entities": ["央行"], "source": "中央銀行", "official": True}],
        [], {}, as_of="x", target_session_date="y", sanitize=str)
    text = "央行理監事會決議升息半碼,以下是它對折現率的影響。"
    out = am.structured_metrics(fx.valid_analysis(), pk, rendered_text=text)
    assert out["quality"]["event_fingerprint_coverage"]["covered"] == 1
    # **生產那一行要真的把信傳進來** —— 判準掃原始碼。
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "morning_report.py"
           ).read_text(encoding="utf-8")
    assert "structured_metrics(\n                    obj, packet, rendered_text=text)" in src, \
        "生產呼叫端沒有把 rendered text 傳給指標"


# ---------------------------------------------------------------- 情境/觀察點

def test_a_scenario_with_a_narrative_must_cite_claims():
    """**P1-6**:最前瞻的判斷不能是唯一不用根據的段落 ——
    「台積電明日可能跌停」配一句「外資情緒轉弱」先前可以整段進信。"""
    obj = fx.valid_analysis()
    obj["scenario_tree"]["bear"]["claim_ids"] = []
    assert [p for p in sch.validate(obj, _IDS) if "scenario_tree.bear" in p]
    obj["scenario_tree"]["bear"]["claim_ids"] = ["c99"]
    assert [p for p in sch.validate(obj, _IDS) if "指向不存在的主張" in p]


def test_a_watch_trigger_must_say_why_it_deserves_attention():
    obj = fx.valid_analysis()
    obj["watch_triggers"] = [{"trigger": "外資期貨空單增至十萬口",
                              "why": "籌碼面轉空", "horizon": "1-5d",
                              "claim_ids": []}]
    assert [p for p in sch.validate(obj, _IDS) if "watch_triggers[0]" in p]
    obj["watch_triggers"][0]["claim_ids"] = ["c1"]
    assert not [p for p in sch.validate(obj, _IDS) if "watch_triggers[0]" in p]


# ---------------------------------------------------------------- 駁回

def test_a_dismissal_must_cite_the_cluster_it_dismisses():
    """**P2-2**:套語偵測靠字面會被修飾詞繞過 —— 機械化的判準是
    「引用你駁回的那則新聞本身」與「說得出什麼情況要回頭看」。"""
    news = [{"source_item_id": "n1", "title": "央行理監事會決議",
             "entities": ["央行"], "source": "中央銀行", "official": True}]
    pk = ep.build({}, {}, {}, news, [], {}, as_of="x", target_session_date="y",
                  sanitize=str)
    obj = fx.valid_analysis()
    obj["top_news_analysis"] = []
    base = {"cluster_id": "cluster:n1",
            "why_not_material": "與上次決議一致,利率路徑沒有改變",
            "supporting_evidence_ids": ["n1"],
            "revisit_trigger": "會後記者會出現前瞻指引轉向"}
    obj["dismissed_events"] = [dict(base, revisit_trigger="")]
    assert [p for p in sch.validate(obj, pk) if "revisit_trigger" in p]
    # 引用別的新聞不算「看過」
    obj["dismissed_events"] = [dict(base, supporting_evidence_ids=["n2"])]
    assert [p for p in sch.validate(obj, pk) if "自己的新聞" in p]
    obj["dismissed_events"] = [base]
    assert not [p for p in sch.validate(obj, pk)
                if "revisit" in p or "自己的新聞" in p]


# ---------------------------------------------------------------- session 政策

def test_only_tw_market_blocks_get_the_tw_session():
    """**P1-7**:先前「非美即台」—— 上週公報被標成最新台股交易日,
    正是這套 metadata 要消滅的假精確。"""
    pk = ep.build({"GAZETTE_RECORDS": {"count": 3}, "USDTWD": 32.5,
                   "TAIFEX_OI": {"foreign_oi_net": -5},
                   "LAST_TRADING_SESSION": {"date": "2026-08-04"}},
                  {}, {}, fx.news(), [], {}, as_of="x", target_session_date="y",
                  sanitize=str)
    reg = er.registry(pk)
    assert reg["market:TAIFEX_OI.foreign_oi_net"]["observed_session"] == "2026-08-04"
    assert reg["market:GAZETTE_RECORDS.count"]["observed_session"] == ""
    assert reg["market:USDTWD"]["observed_session"] == ""


# ---------------------------------------------------------------- 泛稱樣式

def test_a_modifier_does_not_smuggle_a_generic_asset_past_the_list():
    """**P1-8**:exact-match 黑名單一個修飾詞就繞過。"""
    for aid, want in (("台灣市場", True), ("半導體產業", True),
                      ("相關電子族群", True), ("主要供應鏈公司", True),
                      ("整體科技類股", True),
                      ("2330", False), ("00662", False), ("TAIEX", False),
                      ("6510A", False), ("費半", False)):
        obj = fx.valid_analysis()
        obj["top_news_analysis"][0]["affected_assets"][0]["asset_id"] = aid
        hit = bool([p for p in sch.validate(obj, _IDS) if "泛稱" in p])
        assert hit == want, f"{aid} → {'該擋' if want else '該放行'}"


# ---------------------------------------------------------------- 深度加強

def test_a_chain_without_a_quantified_anchor_triggers_deepen():
    """**縱向**:每一步都合法而整條鏈沒有引用任何行情數字 ——
    讀者無從判斷這個傳導是 0.3% 還是 3% 的事。不擋信,觸發加深。"""
    obj = fx.valid_analysis()
    for st in obj["top_news_analysis"][0]["mechanism_steps"]:
        st["evidence_ids"] = ["n1"]
        st["step_type"] = "inference"
    assert [a for a in ad.depth_advisories(obj) if "錨" in a]
    assert sch.validate(obj, _IDS) == [], "淺不擋信 —— 這是 advisory"


def test_a_synthesis_citing_only_news_triggers_deepen():
    """**橫向**:綜合段的證據全是新聞時,它是轉述不是綜合。"""
    obj = fx.valid_analysis()
    obj["cross_market_synthesis"]["evidence_ids"] = ["n1"]
    assert [a for a in ad.depth_advisories(obj) if "轉述" in a]
    # fixture 本身要示範正確做法(錨在 market: 上)
    assert not [a for a in ad.depth_advisories(fx.valid_analysis())
                if "轉述" in a or "錨" in a]


def test_saturation_cannot_exceed_one():
    """**P2-1**:同一段重複填五次先前得到 2.0 —— 大於 100% 的飽和率
    自己就是 false green;而段落內重複也要被驗證器擋。"""
    import quality_metrics as qm
    obj = fx.valid_analysis()
    obj["executive_summary_claim_ids"] = ["c1"] * 5
    assert qm.claim_graph_saturation(obj)["saturation_rate"] <= 1.0
    assert [p for p in sch.validate(obj, _IDS)
            if "executive_summary_claim_ids 有重複" in p]
    obj2 = fx.valid_analysis()
    obj2["stance"]["claim_ids"] = ["c1", "c1", "c2"]
    assert [p for p in sch.validate(obj2, _IDS) if "claim_ids 有重複" in p]


def test_official_and_grade_a_are_separate_metrics():
    """**P2-4**:Reuters 不是主管機關 —— dashboard 的「官方覆蓋」
    先前被 A 級媒體灌高。"""
    pk_news = [
        {"source_item_id": "n1", "title": "央行決議", "entities": ["央行"],
         "source": "中央銀行", "official": True, "source_grade": "OFFICIAL"},
        {"source_item_id": "n2", "title": "Reuters 報導", "entities": ["台積電"],
         "source": "Reuters", "source_grade": "A"},
    ]
    m = am.evidence_coverage("完全沒談到任何一則", {"news": pk_news})
    assert m["official_items"] == 1 and m["grade_a_items"] == 1
