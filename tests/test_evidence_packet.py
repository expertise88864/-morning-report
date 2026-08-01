# -*- coding: utf-8 -*-
"""**EvidencePacket 的契約**(Luna 特化實驗的公平性基礎)。

十天實驗的兩邊使用**不同的 prompt**(那正是「深度特化」的意思),所以公平性
不可能建立在「同一份 prompt 字串」。它只能建立在「兩邊看到同一份證據,而且
證明得出來」—— 也就是 `evidence_sha`。

這個檔盯住三件會讓那個保證失效的事:
  1. sha 不確定(同樣的輸入算出不同的值 → 每天都判成不可比)
  2. sha 對**無關的改動**敏感(主流程加一個渲染用的鍵就讓十天樣本分裂)
  3. 持股明細混進 packet(它會進 prompt,它的 sha 會進公開 repo 的 state)
"""
import json


import evidence_packet as ep

_NEWS = [
    {"title": "A 公司財報優於預期", "summary": "x" * 900,
     "published": "2026-08-01T10:00:00", "source": "鉅亨", "source_grade": "B",
     "entities": ["2330", "TSMC"]},
    {"title": "央行理監事會決議", "summary": "維持利率",
     "published": "2026-08-01T09:00:00", "source": "CBC", "official": True},
    {"title": "B 新聞", "summary": "z", "published": "2026-08-01T11:00:00",
     "source": "Yahoo", "source_grade": "A"},
]
_QUOTES = {"QQQ": {"close": 500.0}, "MACRO": {"VIX": {"close": 15.0}}}


def _build(quotes=None, news=None):
    return ep.build(quotes if quotes is not None else _QUOTES,
                    {"fair_value": 100.0}, {"model1": 1000.0},
                    news if news is not None else _NEWS, [], {},
                    as_of="2026-08-01T06:00:00+08:00",
                    target_session_date="2026-08-01", sanitize=str)


def test_the_same_evidence_always_hashes_to_the_same_value():
    """不確定的 sha 會讓**每一天**都判成不可比,實驗永遠湊不滿十筆。"""
    shas = {ep.evidence_sha(_build()) for _ in range(8)}
    assert len(shas) == 1, f"同樣的輸入算出 {len(shas)} 個不同的 sha"


def test_the_hash_ignores_quote_keys_that_are_not_evidence():
    """主流程往 `quotes` 塞一個渲染用的鍵,**不得**讓十天樣本分裂。

    `quotes` 是主流程的萬用袋子。若 packet 直接 `dict(quotes)`,任何無關的
    新鍵都會改變 sha —— 而 cohort 以 sha 為身分,樣本會莫名其妙歸零。
    """
    base = ep.evidence_sha(_build())
    noisy = ep.evidence_sha(_build(quotes=dict(_QUOTES, PODCAST_SHOWN_EPISODES=[1, 2],
                                               TW_INTEL_POLICY_SHOWN=True)))
    assert base == noisy, "無關的 quotes 鍵改變了 evidence_sha"


def test_the_hash_changes_when_the_evidence_actually_changes():
    """反向:證據真的變了就必須換 sha,否則兩天會被當成同一份證據。"""
    base = ep.evidence_sha(_build())
    more = ep.evidence_sha(_build(news=_NEWS + [
        {"title": "新的一則", "summary": "q", "published": "2026-08-01T12:00:00",
         "source": "Reuters", "source_grade": "A"}]))
    assert base != more, "多了一則新聞,sha 卻沒變"
    moved = ep.evidence_sha(_build(quotes=dict(_QUOTES, QQQ={"close": 501.0})))
    assert base != moved, "行情變了,sha 卻沒變"


def test_news_is_ordered_by_materiality_not_by_fetch_order():
    """官方來源優先,其次依等級與時間 —— **不是**抓取順序。

    依抓取順序的話,「今天剛好排在後面所以被截掉」會讓兩天的證據品質
    不可比,而那個差異看起來會像模型能力差異。
    """
    packet = _build()
    grades = [n["source_grade"] for n in packet["news"]]
    assert grades[0] == "OFFICIAL", f"官方來源沒有排第一:{grades}"
    assert grades == sorted(grades, key=lambda g: ep._GRADE_RANK[g]), \
        f"新聞順序不是依等級:{grades}"

    # 同樣的三則、不同的輸入順序 → 同一個 sha
    import random
    shuffled = list(_NEWS)
    random.Random(7).shuffle(shuffled)
    assert ep.evidence_sha(_build(news=shuffled)) == ep.evidence_sha(_build()), \
        "輸入順序改變就讓 sha 變了 —— 排序不是完全確定性的"


def test_truncation_is_recorded_never_silent():
    """截斷必須留下數量與等級。

    靜默截斷會讓「證據根本沒進去」看起來像「模型沒注意到」——
    那會把資料管線的問題誤判成模型能力差異,而這正是十天實驗要量的東西。
    """
    many = [dict(_NEWS[2], title=f"n{i}", published=f"2026-08-01T0{i % 10}:00:00")
            for i in range(ep.MAX_NEWS_ITEMS + 25)]
    packet = _build(news=many)
    t = packet["truncation"]
    assert t["news_kept"] == ep.MAX_NEWS_ITEMS
    assert t["news_dropped"] == 25, f"被丟掉的數量沒記對:{t}"
    assert sum(t["news_dropped_by_grade"].values()) == 25, "沒記下被丟掉的等級分布"
    assert t["summaries_truncated"] >= 0

    long_summary = _build(news=[_NEWS[0]])
    assert long_summary["truncation"]["summaries_truncated"] == 1, \
        "摘要被截斷卻沒有記錄"


def test_every_news_item_has_a_stable_id_that_claims_can_point_at():
    """證據 ID 必須穩定,而且不得是陣列索引。

    索引會隨當日抓取數量漂移 —— 昨天的 claim 會指到今天的另一則新聞。
    """
    packet = _build()
    ids = [n["source_item_id"] for n in packet["news"]]
    assert len(set(ids)) == len(ids), "證據 ID 有重複"
    assert all(ids), "有新聞沒有證據 ID"
    assert ep.evidence_ids(packet) == set(ids)

    # 同一則新聞,不論位置,ID 都一樣
    reordered = _build(news=list(reversed(_NEWS)))
    assert {n["source_item_id"] for n in reordered["news"]} == set(ids), \
        "證據 ID 依賴輸入位置"


def test_the_packet_never_carries_holdings_or_amounts():
    """**持股明細與絕對金額不得進 packet。**

    packet 會進 prompt,它的 sha 會進 commit 到公開 repo 的 state。
    信件裡顯示金額是使用者看自己的信;這裡的標準要更嚴 ——
    只有百分比與檔數,沒有代號、沒有股數、沒有金額、沒有倉位名稱。
    """
    quotes = dict(_QUOTES, PORTFOLIO_ACTUAL={
        "p1": {"gain_pct": 1.23, "gain_amount": 987654, "prev_value": 8000000,
               "last_value": 8098765, "n_holdings": 4, "n_priced": 4},
        "p2": {"gain_pct": -0.4, "gain_amount": -12345, "prev_value": 3000000,
               "last_value": 2987655, "n_holdings": 2, "n_priced": 2},
        "p1_name": "核心", "p2_name": "衛星",
    })
    packet = _build(quotes=quotes)
    blob = ep.canonical_json(packet)

    assert packet["portfolio"]["available"] is True
    assert packet["portfolio"]["slots"]["p1"]["change_pct"] == 1.23, \
        "百分比沒有被帶出來 —— 這個欄位形同虛設"

    for leaked in ("987654", "8000000", "8098765", "12345", "3000000",
                   "2987655", "核心", "衛星", "gain_amount",
                   "prev_value", "last_value"):
        assert leaked not in blob, f"packet 洩漏了 {leaked!r}"


def test_the_packet_only_takes_declared_quote_keys():
    """`quotes` 裡沒有明列的鍵不得進 packet。

    這條同時是隱私防線:持股相關的中間物若被順手放進 `quotes`,
    白名單會擋住它,而 `dict(quotes)` 不會。
    """
    packet = _build(quotes=dict(_QUOTES, PORTFOLIO_1="2330:5000",
                                SOME_FUTURE_SECRET="不該出現"))
    blob = ep.canonical_json(packet)
    assert "2330:5000" not in blob
    assert "不該出現" not in blob
    assert set(packet["market"]) <= set(ep.EVIDENCE_QUOTE_KEYS)


def test_the_schema_version_is_part_of_the_packet():
    """schema 版本要在 packet 裡 —— cohort 以它為身分的一部分。

    悄悄改欄位而不進版,等於把不同定義的樣本混進同一個平均。
    """
    assert _build()["schema_version"] == ep.EVIDENCE_SCHEMA_VERSION
    assert isinstance(ep.EVIDENCE_SCHEMA_VERSION, int)


def test_serialization_never_explodes_on_odd_values():
    """無法序列化的值不得讓整份 packet 拋例外。

    沒有 sha 的那一天就是不可比的一天 —— 為了一個 datetime 丟掉整天的樣本
    不划算,轉成穩定字串即可。
    """
    import datetime as dt

    packet = _build(quotes=dict(_QUOTES, LAST_TRADING_SESSION=dt.date(2026, 8, 1)))
    blob = ep.canonical_json(packet)
    assert "2026-08-01" in blob
    json.loads(blob)          # 仍必須是合法 JSON
    assert ep.evidence_sha(packet)
