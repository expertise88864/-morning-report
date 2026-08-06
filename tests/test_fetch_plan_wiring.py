# -*- coding: utf-8 -*-
"""**兩階段全文抓取的接線順序**(第二十四輪 P1-1 回歸)。

2026-08-06 生產 manifest:`available news = 563`、`fulltext_plan.clusters = 0`、
`targets = 0` —— 563 則新聞,planner 卻認為一個事件群都不存在。

根因不是資料,是順序:`source_item_id` 原本只在 EvidencePacket 的
`normalize_news()` 產生,而 `plan_for_run()` / `fetch_news_fulltext()` 在它**之前**執行。
三個環節(分群、排計畫、抓取)都以該 ID 索引,於是整段兩階段抓取是 no-op。

**上一輪的測試沒抓到,因為 fixture 都預先手工填好了 `source_item_id`。**
所以這份測試的鐵則是:**fixture 一律不得帶 `source_item_id`**(生產形狀),
由被測程式自己補齊。
"""
from __future__ import annotations

import evidence_packet as ep
import fetch_plan as fp
import news_clusters as nc


def _raw_news():
    """生產形狀的原始新聞:**沒有 source_item_id**(RSS 抓回來就是這樣)。

    兩個事件、各兩家媒體(標題重疊足以同群)+ 一則獨立事件。
    """
    return [
        {"title": "台積電 法說會 上修 全年 資本支出 至 520 億美元",
         "summary": "台積電上修資本支出", "source": "Reuters",
         "source_name": "Reuters", "link": "https://reuters.com/a",
         "published": "2026-08-05T10:00:00Z", "importance": "critical",
         "entities": ["台積電"]},
        {"title": "台積電 法說會 上修 全年 資本支出 至 520 億",
         "summary": "資本支出上修", "source": "Bloomberg",
         "source_name": "Bloomberg", "link": "https://bloomberg.com/b",
         "published": "2026-08-05T10:30:00Z", "importance": "high",
         "entities": ["台積電"]},
        {"title": "Fed 決議 維持 利率 不變 鮑爾 強調 通膨 仍高",
         "summary": "Fed 按兵不動", "source": "CNBC",
         "source_name": "CNBC", "link": "https://cnbc.com/c",
         "published": "2026-08-05T18:00:00Z", "importance": "critical",
         "entities": ["Fed"]},
        {"title": "Fed 決議 維持 利率 不變 鮑爾 強調 通膨 仍",
         "summary": "利率不變", "source": "WSJ",
         "source_name": "WSJ", "link": "https://wsj.com/d",
         "published": "2026-08-05T18:20:00Z", "importance": "high",
         "entities": ["Fed"]},
        {"title": "長榮 海運 運價 指數 SCFI 連續 三週 上漲",
         "summary": "運價上漲", "source": "經濟日報",
         "source_name": "經濟日報", "link": "https://money.udn.com/e",
         "published": "2026-08-05T02:00:00Z", "importance": "medium",
         "entities": ["長榮"]},
    ]


# ── 必補測試 1:生產形狀(無 ID)仍須產生非空 clusters / targets ──

def test_raw_news_without_ids_still_yields_clusters_and_targets():
    news = _raw_news()
    assert all("source_item_id" not in n for n in news), "fixture 不得預填 ID"

    # 修正前:此處 clusters/targets 皆為空(生產 2026-08-06 的實況)
    news = ep.assign_source_item_ids(news)
    cl = nc.clusters(news)
    plan = fp.plan(news, cl, budget=26)

    assert len(cl) > 0, "生產形狀的新聞必須能分群"
    assert len(plan["targets"]) > 0, "必須排得出抓取目標"
    assert all(str(t) for t in plan["targets"])


def test_plan_for_run_end_to_end_on_raw_news():
    """`plan_for_run` 是生產入口:吃原始新聞(補完 ID 後)就要回非空 targets。"""
    news = ep.assign_source_item_ids(_raw_news())
    targets = fp.plan_for_run(news)
    assert len(targets) > 0
    ids = {n["source_item_id"] for n in news}
    assert set(targets) <= ids, "targets 必須都是實際存在的新聞 ID"


# ── 必補測試 2:plan_for_run → fetch_news_fulltext → normalize_news ID 一致 ──

def test_ids_stay_identical_through_the_whole_pipeline():
    """同一則新聞在 planner / 抓取 / packet 三處必須是**同一個 ID**。

    `normalize_news()` 會再呼叫 `_sid()`;若它不冪等,claim 回指的證據就會
    指到別則新聞(而且是安靜地指錯)。
    """
    import news_normalize as nn

    news = ep.assign_source_item_ids(_raw_news())
    before = [n["source_item_id"] for n in news]

    targets = fp.plan_for_run(news)                 # planner 看到的 ID
    kept, _trunc, _info = nn.normalize_news(news)   # packet 階段重算的 ID
    after = {k["source_item_id"] for k in kept}

    assert before == [n["source_item_id"] for n in news], "planner 階段不得改號"
    assert set(targets) <= after or set(targets) <= set(before), "targets 必須仍可回指"
    # 每則被保留的新聞,ID 必須與指派時完全相同(冪等)
    assert after <= set(before), f"normalize_news 改了號:{after - set(before)}"


def test_assign_source_item_ids_is_idempotent():
    news = _raw_news()
    first = [n["source_item_id"] for n in ep.assign_source_item_ids(news)]
    second = [n["source_item_id"] for n in ep.assign_source_item_ids(news)]
    third = [n["source_item_id"] for n in ep.assign_source_item_ids(list(news))]
    assert first == second == third, "重複指派不得改號"
    assert len(set(first)) == len(first), "同一批新聞的 ID 不得碰撞"


def test_assign_preserves_upstream_ids():
    """上游已給 ID 時必須原樣保留(不得重新編號)。"""
    news = [{"title": "x", "source": "s", "published": "p",
             "source_item_id": "upstream-1"}]
    out = ep.assign_source_item_ids(news)
    assert out[0]["source_item_id"] == "upstream-1"


def test_assign_tolerates_junk_items():
    """fail-safe:非 dict / 空 dict / None 不得炸掉整條相位。"""
    news = [None, "junk", {}, {"title": "ok", "source": "s"}]
    out = ep.assign_source_item_ids(news)
    assert out[3]["source_item_id"]
    assert out[2]["source_item_id"]        # 空 dict 也要有可辨識的 ID
    assert ep.assign_source_item_ids(None) is None
    assert ep.assign_source_item_ids([]) == []


# ── 必補測試 3:有新聞且有 URL 時,manifest 不得記 clusters=0 ──

class _Recorder:
    def __init__(self):
        self.plan = None

    def record_fulltext_plan(self, out):
        self.plan = out


def test_manifest_never_records_zero_clusters_when_news_exist():
    """`available_news > 0` 且有可抓 URL 時,manifest 記到 clusters=0 就是接線壞了。"""
    news = ep.assign_source_item_ids(_raw_news())
    rec = _Recorder()
    fp.plan_for_run(news, rec)

    assert rec.plan is not None, "必須記錄 fulltext_plan"
    assert len(rec.plan["per_cluster"]) > 0, "manifest 不得記 clusters=0"
    assert len(rec.plan["targets"]) > 0, "manifest 不得記 targets=0"


def test_plan_for_run_guarantees_ids_for_the_caller():
    """**入口自己保證前置條件**,呼叫端不必記得先補 ID。

    修法刻意放在 `plan_for_run` 而不是要求呼叫端先呼叫 —— 「忘記先補」正是
    2026-08-06 那個缺陷的成因。就地寫入,所以呼叫端拿同一個 list 去
    `fetch_news_fulltext()` 也看得到 ID(生產就是這樣接的)。
    """
    news = _raw_news()
    assert all("source_item_id" not in n for n in news)

    targets = fp.plan_for_run(news)          # 呼叫端沒有先補 ID

    assert all(n.get("source_item_id") for n in news), "入口必須就地補齊 ID"
    assert len(targets) > 0
    # 生產的下一步:同一個 list 交給 fetch —— 它也以 source_item_id 索引
    by_id = {n["source_item_id"]: n for n in news}
    assert all(str(t) in by_id for t in targets), "targets 必須能被 fetch 階段解析"
