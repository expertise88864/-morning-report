# -*- coding: utf-8 -*-
"""**故事縱深不是沒有,是沒接上**(縱深第四批,2026-08-09)。

`story_ledger` 的狀態機(醞釀/發展/高潮/收斂)、逐步軌跡、起因日期
全部存在 —— 但只餵 legacy prompt(`_format_story_prompt_block`)。
特化路徑(結構化分析)看不到它:同一條延燒中的線索,legacy 的信寫得出
「上週 X → 前天 Y → 今天 Z」,特化的信只有「第 N 天」+ 昨天一句。

這一批把它接進 evidence packet(`packet["story_arcs"]`),
所以這裡的判準是**接線**:兩條路徑要看到同一組線索、
原始帳本不得留在 packet(payload 與 evidence_sha)、
消毒要蓋到、prompt 要說得出這是脈絡不是證據。
"""
from __future__ import annotations

import evidence_packet as ep
import story_ledger as sl


def _ledger() -> list:
    return [
        {"entity": "台積電", "state": "developing", "first_seen": "2026-08-05",
         "updates": 3, "last_update": "2026-08-09",
         "headline": "CoWoS 產能傳大幅擴充",
         "timeline": [
             {"d": "2026-08-05", "t": "傳台積電評估擴充先進封裝",
              "f": ["1000000000"]},
             {"d": "2026-08-07", "t": "設備商接獲追加訂單", "f": []},
             {"d": "2026-08-09", "t": "CoWoS 產能傳大幅擴充",
              "f": ["2000000000"]}],
         "prev_delta": "追加訂單金額上修"},
        {"entity": "沉寂的舊案", "state": "dormant",
         "headline": "x", "last_update": "2026-07-01", "timeline": []},
        {"entity": "昨天有動的", "state": "peak", "first_seen": "2026-08-01",
         "updates": 5, "last_update": "2026-08-08", "headline": "y",
         "timeline": [{"d": "2026-08-08", "t": "z", "f": []}]},
    ]


def _packet(**over):
    quotes = {"STORY_LEDGER": _ledger()}
    quotes.update(over.pop("quotes", {}))
    return ep.build(quotes, {}, {}, [], [], {}, as_of="2026-08-09 06:00",
                    target_session_date="2026-08-09",
                    sanitize=over.pop("sanitize", lambda s, *a: s))


# ---------------------------------------------------------------- 接線

def test_the_specialized_path_finally_sees_the_story():
    """**接上就要真的在 packet 裡**,而且軌跡是縱深的本體:
    起因(第一步)→ 轉折 → 最新,以及起因日期與追蹤次數。"""
    pk = _packet()
    arcs = pk["story_arcs"]
    assert arcs and arcs[0]["entity"] == "台積電"
    steps = arcs[0]["trajectory"]
    assert [e["date"] for e in steps] == ["2026-08-05", "2026-08-07",
                                          "2026-08-09"]
    assert arcs[0]["first_seen"] == "2026-08-05"
    assert arcs[0]["updates"] == 3
    assert arcs[0]["state_zh"], arcs[0]
    # 數字事實換回中文單位(帳本存的是正規化純數字,直接印是一串零)
    assert arcs[0]["trajectory"][0]["facts"] == ["10億"]


def test_selection_matches_the_legacy_path():
    """**兩條路徑要看到同一組線索**(`active_stories` 是同一份判準)。

    各選各的話,「哪條線索在燒」會依 provider 而變 ——
    那不是模型的差異,是我們餵的差異。
    """
    arcs = sl.story_arcs(_ledger(), today="2026-08-09")
    legacy = sl.active_stories(_ledger(), today="2026-08-09")
    assert [a["entity"] for a in arcs] == [s["entity"] for s in legacy]
    # 沉寂線索兩邊都不在
    assert "沉寂的舊案" not in {a["entity"] for a in arcs}


def test_a_stale_story_is_flagged_not_hidden():
    """昨天有動、今天沒動的線索**標成不新鮮**而不是藏掉 ——
    沒標的話模型會照樣重述(每日重複正是要消滅的東西)。"""
    arcs = sl.story_arcs(_ledger(), today="2026-08-09")
    by = {a["entity"]: a for a in arcs}
    assert by["台積電"]["fresh_today"] is True
    assert by["昨天有動的"]["fresh_today"] is False


def test_the_raw_ledger_does_not_stay_in_the_packet():
    """**原始帳本不留在 packet**:數百條線索會吃掉 payload 預算,
    也讓 evidence_sha 對與今天無關的舊線索變動敏感。"""
    pk = _packet()
    assert "STORY_LEDGER" not in pk["market"]


def test_no_ledger_degrades_to_an_empty_list():
    """**晨報不可斷**:沒有帳本(讀檔失敗那天)是空清單,不是例外。"""
    pk = ep.build({}, {}, {}, [], [], {}, as_of="x",
                  target_session_date="y", sanitize=lambda s, *a: s)
    assert pk["story_arcs"] == []


def test_the_arcs_pass_through_the_sanitizer():
    """**跨日回流的外部標題是存放式注入的高風險路徑**(批#36 的教訓)——
    legacy 那條有圍欄,packet 這條靠 `sanitize_tree` 整樹掃,
    所以弧裡的每一個字串都必須是它掃得到的葉節點。"""
    pk = _packet(sanitize=lambda s, *a: f"S:{s}")
    # `sanitize_tree` 連 dict 的**鍵**都掃(那是刻意的)—— 所以標記過的
    # packet 裡,這一格的鍵也帶著標記。
    arc = pk["S:story_arcs"][0]
    assert arc["S:headline"].startswith("S:")
    assert arc["S:trajectory"][0]["S:title"].startswith("S:")
    assert arc["S:prior_delta"].startswith("S:")


def test_the_prompt_declares_arcs_as_context_not_evidence():
    """prompt 要說出這條規則,否則模型只是在退回合法的工作;
    而「不是證據」那一句與 `yesterday_view` 是同一條規矩 ——
    少了它,過往軌跡會被拿來替今天的判斷背書。"""
    import io
    from pathlib import Path
    src = io.open(Path(__file__).resolve().parents[1] / "prompt_profiles.py",
                  encoding="utf-8").read()
    # 找的是 prompt 的規則段(`EVIDENCE.story_arcs`),不是版本註解 ——
    # 第一版用裸 `story_arcs` 搜,先命中的是版本說明,量到的是別的東西。
    assert "EVIDENCE.story_arcs" in src
    i = src.index("EVIDENCE.story_arcs")
    seg = src[i:i + 600]
    assert "不是證據" in seg or "脈絡不是證據" in seg, seg
    assert "不要自行改判" in seg, seg


# ===== 外審第二輪 =====

def test_weekend_freshness_uses_the_report_date_not_the_target_session():
    """**週六產報時 `target_session_date` 指到週一**(外審 F1)——
    拿目標日比新鮮度的話,今天才更新的線索全部被標成不新鮮、
    被舊線索擠出上限,而 legacy 用的是台北當日。
    兩條路徑的「今天」必須是同一天。
    """
    sat_ledger = [{"entity": "台積電", "state": "developing",
                   "first_seen": "2026-08-08", "updates": 1,
                   "last_update": "2026-08-08", "headline": "h",
                   "timeline": [{"d": "2026-08-08", "t": "t", "f": []}]}]
    pk = ep.build({"STORY_LEDGER": sat_ledger}, {}, {}, [], [], {},
                  as_of="2026-08-08 06:15",          # 週六產報
                  target_session_date="2026-08-10",  # 目標是週一
                  sanitize=lambda s, *a: s)
    assert pk["story_arcs"][0]["fresh_today"] is True, pk["story_arcs"]


def test_truncation_keeps_the_origin_step():
    """**截斷不得丟掉起因**(外審 F2):prompt 把軌跡第一步當成起因,
    只取尾端的話,六步線索的「第一步」其實是中途轉折 ——
    模型會把轉折誤寫成故事的開端。"""
    tl = [{"d": f"2026-08-0{i}", "t": f"第{i}步", "f": []} for i in range(1, 7)]
    arcs = sl.story_arcs([{"entity": "X", "state": "peak",
                           "first_seen": "2026-08-01", "updates": 6,
                           "last_update": "2026-08-06", "headline": "h",
                           "timeline": tl}], today="2026-08-06")
    steps = arcs[0]["trajectory"]
    assert len(steps) == 4
    assert steps[0]["date"] == "2026-08-01", "起因被裁掉了"
    assert steps[0]["steps_omitted_after"] == 2, steps[0]
    assert [e["date"] for e in steps[1:]] == ["2026-08-04", "2026-08-05",
                                              "2026-08-06"]
    # 四步以內不標省略(沒有省略就不要暗示有)
    short = sl.story_arcs([{"entity": "Y", "state": "peak",
                            "first_seen": "2026-08-01", "updates": 3,
                            "last_update": "2026-08-06", "headline": "h",
                            "timeline": tl[:3]}], today="2026-08-06")
    assert "steps_omitted_after" not in short[0]["trajectory"][0]

