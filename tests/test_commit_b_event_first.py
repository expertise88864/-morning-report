# -*- coding: utf-8 -*-
"""**Commit B:先把文章壓成事件,再把預算花在事件上。**

使用者的重構規格一句話:「先把 547 篇文章壓成少量可信事件,再分析事件」。
這個檔守的是那句話在**抓取層**與**可信度層**的兩個後果:

  * 「三家媒體同時報導」不等於「三個獨立來源」(同集團、通訊社轉載);
  * 全文預算逐**事件群**分配,不是逐**文章**分配。
"""
import fetch_plan as fp
import news_clusters as nc
import source_registry as sr


# ---------------------------------------------------------- 誰跟誰是同一個編輯台

def test_one_newspaper_group_is_one_source():
    """經濟日報 + 聯合報 + 聯合新聞網 = **三個字串、一個編輯台**。"""
    items = [{"source_name": n} for n in ("經濟日報", "聯合報", "聯合新聞網")]
    out = sr.independence(items)
    assert out["count"] == 1, out
    assert out["groups"] == ["udn"]


def test_a_syndicated_wire_story_is_one_source():
    """三家報紙各貼一則中央社稿 —— **一個編輯決策**,不是三個。
    署名比 `source_name` 更接近真相,所以它先判。"""
    body = "(中央社記者李四台北5日電)聯發科今日宣布…"
    items = [{"source_name": n, "summary": body}
             for n in ("經濟日報", "自由時報", "TVBS")]
    assert sr.independence(items)["count"] == 1


def test_three_genuinely_independent_outlets_still_count_as_three():
    """**保守不是一律往下壓。** 真的三個編輯台就要算三個,
    否則這條規則只是把佐證等級整體調低,而不是把它算對。"""
    items = [{"source_name": n, "summary": "本報記者採訪"}
             for n in ("經濟日報", "自由時報", "鉅亨網")]
    assert sr.independence(items)["count"] == 3


def test_an_unknown_publisher_is_reported_not_counted():
    """**沒驗,不是驗過。** 不認得的媒體不進獨立數,另外報 —— 高估獨立性
    會在信裡造出假的信心,而讀者無從察覺。"""
    out = sr.independence([{"source_name": "某地方財經網"},
                           {"source": "Google:2330"}])
    assert out["count"] == 0
    assert out["unverified"] == 1 and out["aggregator_only"] == 1


def test_the_two_counts_are_conservative_in_opposite_directions():
    """**同一個數字餵兩種用途,必然有一邊是錯的方向。**

    佐證等級(信裡的可信度宣稱)保守 = 少算;
    覆蓋率地板(必分析清單)保守 = 多算 —— 地板算少了,重要事件會從
    清單掉出去,而那正是那個清單存在的理由。
    """
    out = sr.independence([{"source_name": "經濟日報"},
                           {"source_name": "某地方財經網"},
                           {"source_name": "另一家沒聽過的"}])
    assert out["count"] == 1          # 嚴格:只認得 udn
    assert out["potential"] == 3      # 寬鬆:另外兩家**可能**是獨立的


def test_the_cluster_carries_both_numbers():
    """群要同時帶得出兩個數,下游才不會用錯那一個。"""
    udn = [{"source_item_id": f"n{i}", "title": "台積電熊本廠恢復產線運作",
            "entities": ["台積電"], "source_name": s}
           for i, s in enumerate(("經濟日報", "聯合報", "聯合新聞網"))]
    c = nc.clusters(udn)[0]
    assert c["independent_sources"] == 1
    assert c["corroboration"] == "single_source", "同報系被當成多方證實"
    # 覆蓋率地板不因此漏掉它 —— 三則都認得,potential 也是 1,
    # 於是它確實不是「三家同時報」的重大事件。
    assert c["potential_independent_sources"] == 1
    assert nc.required_analysis(udn)["required_cluster_ids"] == []


def test_unknown_outlets_still_reach_the_required_list():
    """**地板不得被保守判準壓垮。** 三家不認得的媒體同時報導,佐證等級
    仍是 single_source(沒驗過),但它**要**進必分析清單。"""
    news = [{"source_item_id": f"n{i}", "title": "央行宣布調升存款準備率",
             "entities": ["央行"], "source_name": s}
            for i, s in enumerate(("甲報", "乙報", "丙報"))]
    c = nc.clusters(news)[0]
    assert c["independent_sources"] == 0
    assert c["potential_independent_sources"] == 3
    assert nc.required_analysis(news)["required_cluster_ids"] == ["cluster:n0"]


# ---------------------------------------------------------- 預算花在事件上

def _news_three_events():
    """事件甲四家報、事件乙一家報、事件丙官方公告 —— 全部 critical。"""
    return ([{"source_item_id": f"a{i}", "title": "台積電熊本廠恢復產線運作",
              "entities": ["台積電"], "source_name": s, "importance": "critical",
              "link": "http://x"}
             for i, s in enumerate(("經濟日報", "自由時報", "工商時報", "鉅亨網"))]
            + [{"source_item_id": "b0", "title": "聯發科天璣新品發表會延期",
                "entities": ["聯發科"], "source_name": "DIGITIMES",
                "importance": "critical", "link": "http://x"},
               {"source_item_id": "c0", "title": "央行理監事會決議調升存準率",
                "entities": ["央行"], "source_name": "中央銀行", "official": True,
                "importance": "critical", "link": "http://x"}])


def test_a_tight_budget_covers_three_events_not_one():
    """**這是 Commit B 的重點。** 預算三格:

        舊行為(逐則掃 critical)→ a0, a1, a2 —— 三格全在同一個事件上,
        央行公告與聯發科各拿到零篇全文,信裡它們只有兩行 RSS 摘要;
        新行為 → 三個**不同的**事件各一篇。
    """
    news = _news_three_events()
    got = fp.plan(news, nc.clusters(news), budget=3)["targets"]
    assert got == ["c0", "a0", "b0"], got
    # 舊行為的對照(如果排序退回逐則掃 critical,上面那行就會變成這個)
    old = [n["source_item_id"] for n in news
           if n["importance"] == "critical"][:3]
    assert old == ["a0", "a1", "a2"] and got != old


def test_the_official_announcement_is_fetched_first():
    """官方公告是**事實本身**,不是對事實的報導 —— 它排最前面。"""
    news = _news_three_events()
    assert fp.plan(news, nc.clusters(news), budget=1)["targets"] == ["c0"]


def test_a_second_article_only_after_every_event_has_one():
    """第二篇的用途是互相對照,但**涵蓋的事件數優先**。"""
    news = _news_three_events()
    got = fp.plan(news, nc.clusters(news), budget=4)["targets"]
    assert got[:3] == ["c0", "a0", "b0"], got
    assert got[3] == "a1", "還沒讓每個事件都有一篇就去抓第二篇了"


def test_items_without_a_link_do_not_burn_the_budget():
    news = [dict(n, link="") for n in _news_three_events()]
    assert fp.plan(news, nc.clusters(news), budget=3)["targets"] == []


def test_already_fetched_items_are_not_re_planned():
    """冪等:候選股/8-K 併入後會再跑一次,已經有全文的不該再佔預算。"""
    news = _news_three_events()
    for n in news:
        if n["source_item_id"] in ("c0", "a0"):
            n["fulltext"] = "已經抓過了"
    got = fp.plan(news, nc.clusters(news), budget=3)["targets"]
    assert "c0" not in got and "a0" not in got
    assert got[0] == "a1", got


def test_the_plan_says_what_it_could_not_cover():
    """**沒有靜默的上限。** 排得到卻沒預算的事件群要說出來 ——
    只講「抓了幾篇」會讀起來像涵蓋完整。"""
    news = _news_three_events()
    out = fp.plan(news, nc.clusters(news), budget=1)
    assert out["uncovered_clusters"], "漏掉的事件群沒有被報出來"
    assert set(out["uncovered_clusters"]) == {"cluster:a0", "cluster:b0"}
    assert fp.plan(news, nc.clusters(news), budget=9)["uncovered_clusters"] == []


def test_production_uses_the_event_plan_not_the_per_article_scan():
    """守衛不得因為呼叫端沒接上而靜默失效 —— 掃生產原始碼。"""
    import io
    from pathlib import Path
    src = io.open(Path(__file__).resolve().parents[1] / "morning_report.py",
                  encoding="utf-8").read()
    assert "plan_for_run(news, ctx.recorder)" in src, "兩階段抓取沒接進生產"
    assert "targets=_fplan.plan_for_run" in src
