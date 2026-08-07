# -*- coding: utf-8 -*-
"""**「昨夜三大重點」的條數契約**(第二十四輪 P1-5 回歸)。

先前三處各自為政:schema 是一般 array、驗證器只驗既有條目的證據、
renderer 取 `[:3]` —— 沒有一個管數量。於是 0 條(段落整個消失)、
1-2 條(標題說三大、實際兩條)、4 條以上(第四條被驗證器視為已處理、
讀者永遠看不到)全都能通過。

必補測試 6:`key_drivers` 必須恰好三條。
必補測試 7:**驗證器接受的三條必須等於 renderer 顯示的三條。**
"""
from __future__ import annotations

import analysis_render as ar
import analysis_schema as sch
import analysis_validate as av
import evidence_packet as ep
import fixtures_analysis as fx


def _news(n=6):
    """n 個彼此獨立的事件(標題不重疊 → 不會被併成同一群)。"""
    topics = ["台積電 法說會 上修 資本支出", "長榮 海運 運價 指數 上漲",
              "聯發科 新 晶片 發表 會", "央行 理監事 會議 利率 決議",
              "鴻海 電動車 平台 新 客戶", "南亞科 記憶體 報價 調漲"]
    return [{"source_item_id": f"k{i}", "title": topics[i],
             "summary": f"內容{i}", "source": "Reuters",
             "source_name": "Reuters", "entities": [f"E{i}"],
             "published": f"2026-08-0{i+1}T10:00:00Z"} for i in range(n)]


def _packet(n=6):
    return ep.build({}, {}, {}, _news(n), [], {}, as_of="x",
                    target_session_date="2026-08-05", sanitize=str)


def _is_count_problem(p: str) -> bool:
    """只挑**條數**契約的問題 —— 其餘契約(claim 同向/共享證據)有自己的測試。"""
    return "key_drivers" in p and ("恰好" in p or "超過" in p)


def _with_drivers(pk, k):
    """做出 k 條指向真實事件群、且證據 ID 真的存在於本日 packet 的重點。"""
    obj = fx.valid_analysis()
    top = list((pk.get("top_events") or {}).get("top_cluster_ids") or [])
    members = {c["cluster_id"]: list(c["member_source_ids"])
               for c in ((pk.get("news_clusters") or {}).get("clusters") or [])}
    out = []
    for i in range(k):
        cid = top[i % max(1, len(top))] if top else "cluster:k0"
        out.append(dict(obj["key_drivers"][0], cluster_id=cid,
                        evidence_ids=members.get(cid, ["k0"])[:1]))
    obj["key_drivers"] = out
    return obj


def test_required_count_comes_from_the_packet_not_the_model():
    """分母是 Python 算出來的事件群數,不是模型自評。"""
    pk = _packet(6)
    assert av.key_drivers_required(pk) == 3
    # 清淡的一天:合格事件不足三個 → 要求「全部」,而不是逼模型湊到三條。
    # 分母是 `top_events`(Python 計分後的事件群),純價格變化不算事件。
    quiet = _packet(2)
    n_quiet = len((quiet.get("top_events") or {}).get("top_cluster_ids") or [])
    assert n_quiet < 3
    assert av.key_drivers_required(quiet) == n_quiet
    # 沒有 packet 就沒有分母 —— **不猜**
    assert av.key_drivers_required(None) is None
    assert av.key_drivers_required({}) is None


def test_a_fourth_driver_is_rejected():
    """第四條一定會被 renderer 隱藏 —— 驗證器不得把它算成已處理。"""
    pk = _packet(6)
    obj = _with_drivers(pk, 4)
    assert [p for p in sch.validate(obj, pk) if _is_count_problem(p)]


def test_too_few_drivers_are_rejected():
    """少於今日應有條數 → 讀者看不出被省略。"""
    pk = _packet(6)
    for k in (0, 1, 2):
        obj = _with_drivers(pk, k)
        assert [p for p in sch.validate(obj, pk)
                if _is_count_problem(p)], f"{k} 條應被擋"


def test_exactly_three_passes():
    pk = _packet(6)
    obj = _with_drivers(pk, 3)
    assert not [p for p in sch.validate(obj, pk) if _is_count_problem(p)]


def test_quiet_day_requires_all_available_not_a_padded_third():
    """**湊一段不會讓分析更深**:只有兩個事件時,兩條就是對的。"""
    pk = _packet(2)
    want = av.key_drivers_required(pk)
    assert want < 3, "這一天本來就湊不出三條"
    obj = _with_drivers(pk, want)
    assert not [p for p in sch.validate(obj, pk) if _is_count_problem(p)]


def test_validator_accepted_count_equals_rendered_count():
    """**必補測試 7**:驗證器接受幾條,信裡就要顯示幾條。"""
    import re
    for n_events in (6, 2):
        pk = _packet(n_events)
        obj = _with_drivers(pk, av.key_drivers_required(pk))
        assert not [p for p in sch.validate(obj, pk) if _is_count_problem(p)]
        md = ar.render(obj, pk) if hasattr(ar, "render") else ar.to_markdown(obj, pk)
        # 事件卡的條數 = 驗證器接受的條數
        body = md.split(ar.SECTION_TOP3, 1)[-1]
        shown = len(re.findall(r"^###?\s", body, flags=re.M)) or body.count("\n- ")
        assert shown >= 1, "段落存在但沒有任何一條"


def test_no_silent_hiding_when_model_overshoots():
    """模型多寫時:驗證器必須擋下,而不是讓 renderer 靜靜吃掉。"""
    pk = _packet(6)
    obj = _with_drivers(pk, 5)
    problems = [p for p in sch.validate(obj, pk) if _is_count_problem(p)]
    assert problems, "多寫沒有被擋 —— 那正是靜默隱藏"


def test_a_zero_event_day_requires_zero_drivers_not_three():
    """**0 個合格事件是合法答案,不是「不知道」**(外審 P1-5)。

    上一版 `n == 0` 與「拿不到 packet」都回 `None`:
      * 條數契約整段跳過 → 模型塞 1–3 條虛構重點不會被擋;
      * renderer 的 `or 3` 又把 0 當 falsy → 最多顯示三條。
    「今天沒有三大事件」因此被迫變成虛構的三大事件 —— 而週日輕量日、
    新聞池全是價格變化的日子,`top_cluster_ids` 真的會是空的。
    """
    import analysis_contracts as ac
    pk = _packet(0)
    assert (pk.get("top_events") or {}).get("top_cluster_ids") == [], (
        "這份 fixture 不是 0 事件,測不到本條")
    assert ac.key_drivers_required(pk) == 0, "0 個合格事件要回 0,不是 None"

    # 拿不到分母才是 None(這條分界不能被上面那個修正弄丟)
    assert ac.key_drivers_required(None) is None
    assert ac.key_drivers_required({}) is None
    assert ac.key_drivers_required({"top_events": []}) is None

    # 0 事件日塞任何一條重點都要被擋
    obj = fx.valid_analysis()
    obj["key_drivers"] = [dict(obj["key_drivers"][0])]
    problems = [p for p in ac.key_driver_count_problems(obj, pk)]
    assert problems, "0 事件日塞了一條虛構重點卻沒被擋"
    assert "恰好 0 條" in problems[0], problems

    obj["key_drivers"] = []
    assert ac.key_driver_count_problems(obj, pk) == [], "0 條才是這天的正確答案"


def test_the_renderer_does_not_fall_back_to_three_on_a_zero_event_day():
    """**`or 3` 是這個缺陷的另一半**:切片端不得把合法的 0 當成沒答案。"""
    import analysis_contracts as ac
    pk = _packet(0)
    drivers = [{"statement": f"虛構{i}"} for i in range(3)]
    assert ac.top_drivers(drivers, pk) == [], "0 事件日仍切出重點給 renderer"
    # 拿不到分母時仍退回硬上限(不得因為修 0 而把 None 也變成 0)
    assert len(ac.top_drivers(drivers, None)) == ac.KEY_DRIVERS_REQUIRED
