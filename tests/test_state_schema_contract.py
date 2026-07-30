# -*- coding: utf-8 -*-
"""**已落地 state 的 schema 契約。**

批#77(第七輪 P1-9 的可離線部分)。

第七輪建議建立 raw adapter contract tests(從 Google RSS / MOPS / TWSE /
DeepSeek 的**原始 payload** 跑到 state)。那需要真實回應樣本,而本機取不到 ——
本輪已經有兩次「猜欄位/猜回應形狀」的代價,不再猜第三次。

**能做而且有價值的是另一半**:對 repo 裡**真實落地**的 state 檔立 schema 契約。
它抓得到的正是 raw adapter 測試要抓的東西 —— 上游 schema 漂移最終一定會表現成
「state 檔的形狀變了」或「該有值的欄位變空」。差別只在時機:raw adapter 測試
在 CI 就擋下,這一層則是在資料落地後才發現。**晚一步,但比沒有好**,而且
它驗的是真實生產資料,不是我構造出來的 fixture。

刻意設計成:
  - 檔案不存在 → 跳過(全新 repo / 尚未產生該狀態不該讓 CI 紅)
  - 只驗**結構與不變式**,不驗數值(數值每天都會變)
  - 訊息要指出「哪一筆、哪個欄位」,而不是只說形狀不對
"""
import json
from pathlib import Path

import pytest

import morning_report as mr
import story_ledger as sl

# 批#78 r1:**位置由這個檔案決定,不由 CWD 決定。** 寫 `Path("state")` 的話,
# 從 repo 根目錄以外啟動 pytest 會全部走進「檔案不存在 → skip」,
# 整套契約無聲消失 —— 而它存在的理由正是抓「無聲的漂移」。
# (「不存在就跳過」的語意本身保留:全新 repo 確實還沒有 state。)
STATE = Path(__file__).resolve().parents[1] / "state"


def _load(name):
    path = STATE / name
    if not path.exists():
        pytest.skip(f"{name} 尚未產生(全新 repo 或該功能未跑過)")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:      # 壞檔本身就是要抓的東西,不能跳過
        pytest.fail(f"{name} 無法解析:{e}")


def _iso_like(value) -> bool:
    return bool(mr._parse_news_time_required(str(value or "")))


# ---------------------------------------------------------------- model_history
def test_model_history_rows_have_a_session_date_and_sane_prices():
    rows = _load("model_history.json")
    assert isinstance(rows, list) and rows
    for i, r in enumerate(rows):
        assert isinstance(r, dict), f"第 {i} 筆不是 dict"
        assert str(r.get("session_date") or "").count("-") == 2, \
            f"第 {i} 筆 session_date 異常:{r.get('session_date')!r}"
        close = r.get("taiex_close")
        if close is not None:
            assert isinstance(close, (int, float)) and close > 1000, \
                f"{r['session_date']} 的 taiex_close 不合理:{close}"
    dates = [r["session_date"] for r in rows]
    assert len(dates) == len(set(dates)), "同一 session_date 出現多次"


def test_model_history_structured_events_keep_their_contract():
    """事件欄位型別走樣時,prompt 的清洗層會把整欄剔除 —— 那是靜默失分。"""
    rows = _load("model_history.json")
    seen = 0
    for r in rows:
        for e in (r.get("structured_events") or []):
            seen += 1
            assert isinstance(e, dict)
            assert e.get("event_type"), f"{r['session_date']} 有事件缺 event_type"
            assert isinstance(e.get("direction"), int) \
                and not isinstance(e.get("direction"), bool), \
                f"{r['session_date']} 的 direction 型別錯:{e.get('direction')!r}"
            assert _iso_like(e.get("published")), \
                f"{r['session_date']} 的 published 無法解析:{e.get('published')!r}"
            corr = e.get("corroboration_count")
            if corr is not None:
                assert isinstance(corr, int) and corr >= 0
                srcs = e.get("sources") or []
                assert corr <= max(1, len(srcs)), \
                    f"交叉驗證數 {corr} 超過來源數 {len(srcs)}"
    assert seen, "歷史裡完全沒有結構化事件 —— 抽取管線可能整段失效"


# ---------------------------------------------------------------- story ledger
def test_story_ledger_rows_are_well_formed():
    rows = _load("story_ledger.json")
    assert isinstance(rows, list)
    keys = set()
    for i, s in enumerate(rows):
        assert isinstance(s, dict), f"第 {i} 筆不是 dict"
        key = str(s.get("key") or "")
        assert key, f"第 {i} 筆缺 key"
        assert key not in keys, f"重複的線索 key:{key}"
        keys.add(key)
        # 狀態集合**從程式碼取**,不手抄 —— 手抄就是漂移的來源
        # (自測抓到:我憑印象寫 "converging",實際是 "resolving")。
        assert s.get("state") in set(sl.STATE_WEIGHT), \
            f"{key} 的 state 異常:{s.get('state')!r}"
        assert isinstance(s.get("updates"), int) and s["updates"] >= 1
        for p in (s.get("timeline") or []):
            assert isinstance(p, dict) and p.get("d"), f"{key} 有壞的軌跡點"
            link = str(p.get("l") or "")
            assert not link or link.startswith(("http://", "https://")), \
                f"{key} 的軌跡點 link 不是 http(s):{link!r}"


def test_story_ledger_market_wrap_backlog_is_reported_not_enforced():
    """**刻意不當成失敗。**

    批#63/#71 的大盤總結清掃是在 `update_ledger` 執行時才套用到既有帳本的,
    所以 repo 裡的 state 會在「修正已上線、但下一次生產執行還沒跑」的窗口裡
    仍然帶著舊資料。在那個窗口把 CI 弄紅,是**沒有任何 commit 能修好的紅**
    —— 而那種紅會訓練人忽略 CI。

    清掃邏輯本身由單元測試涵蓋
    (`test_market_wrap_timeline_points_are_swept_from_existing_stories`);
    這裡只把待遷移的數量印出來,讓「還剩多少」看得見。
    """
    rows = _load("story_ledger.json")
    vocab = ("台積電", "聯電", "鴻海", "廣達", "聯發科", "美光")
    backlog = [s["key"] for s in rows
               if sl.is_market_wrap(str(s.get("headline") or ""), vocab)]
    print(f"[state-contract] 待清掃的大盤總結線索:{len(backlog)}/{len(rows)}")
    # 只保證不會**惡化到整個帳本都是**(那代表清掃邏輯反了)
    assert len(backlog) < len(rows) * 0.5, \
        f"超過半數線索是大盤總結({len(backlog)}/{len(rows)})—— 清掃可能反了"


# ---------------------------------------------------------------- forecast ledger
def test_forecast_ledger_rows_are_internally_consistent():
    rows = _load("forecast_ledger.json")
    assert isinstance(rows, list)
    for i, e in enumerate(rows):
        assert isinstance(e, dict), f"第 {i} 筆不是 dict"
        kind = e.get("type")
        if kind == "top5":
            assert e.get("status") in {"awaiting_entry", "entered", "void",
                                       "void_legacy"}, \
                f"top5 第 {i} 筆 status 異常:{e.get('status')!r}"
            for h, res in (e.get("res") or {}).items():
                assert isinstance(res, dict), f"top5 res[{h}] 不是 dict"
                if res.get("void"):
                    # 批#73 的 legacy reason 標記是**下一次執行**才套用到既有
                    # 帳本的,所以這裡不強制(理由同 backlog 那條測試:
                    # 在遷移窗口把 CI 弄紅,是沒有任何 commit 能修好的紅)。
                    # 有 reason 時必須是可判讀的字串。
                    assert isinstance(res.get("reason", ""), str)
                else:
                    assert isinstance(res.get("excess_pct"), (int, float))
        elif kind == "mz_shadow":
            for f in ("raw", "shadow"):
                assert isinstance(e.get(f), (int, float)), f"mz_shadow 缺 {f}"
            if e.get("resolved") and not e.get("void"):
                assert isinstance(e.get("actual"), (int, float))
        elif e.get("question"):
            # r1(Codex,P2):**不得用預設值。** 原本寫 `e.get("prob", 0.5)`,
            # 那讓「欄位不見了」變成合法值 —— 契約掩蓋了它該抓的漂移。
            # 而結算端在同一個地方也用 0.5 當預設,會產生**虛構的 Brier 分數**
            # 並污染預測績效統計。缺欄位必須當場失敗。
            prob = e.get("prob")
            assert isinstance(prob, (int, float)) and not isinstance(prob, bool),                 f"機率題 {e.get('question')}/{e.get('target')} 缺 prob 或型別錯:{prob!r}"
            assert prob == prob and abs(prob) != float("inf"), "prob 非有限數"
            assert 0.0 <= float(prob) <= 1.0, f"prob 超出 [0,1]:{prob}"
            assert e.get("forecast_version"), "機率題缺版本血統"


# ---------------------------------------------------------------- exdiv history
def test_exdiv_history_shape_and_coverage_metadata():
    data = _load("exdiv_history.json")
    assert isinstance(data, dict), "除權息史應是 {since, days, records}"
    assert isinstance(data.get("records"), list)
    assert isinstance(data.get("days"), list)
    for r in data["records"]:
        assert isinstance(r, dict) and r.get("code") and r.get("ex_date")
        assert str(r["ex_date"]).count("-") == 2, f"ex_date 格式異常:{r}"
    # `days` 有值卻沒有任何 record 是**危險組合**:覆蓋檢查會判定完整、
    # 紀錄卻是空的 → Top5 用原始價格照常結算(批#71 r1 的真實損毀情境)
    if data["days"]:
        assert data["records"], (
            "days 宣稱收集過但 records 是空的 —— 覆蓋檢查會誤判為完整,"
            "Top5 會用未調整價格結算")


# ---------------------------------------------------------------- run manifest
def test_run_manifest_carries_the_observability_fields():
    """批#68–#75 陸續加的診斷欄位,每一個都曾經因為漏列重建白名單而被丟掉。
    這條驗**真實落地的 manifest**確實帶著它們(至少 date 與階段耗時)。"""
    m = _load("run_manifest.json")
    assert isinstance(m, dict)
    assert str(m.get("date") or "").count("-") == 2
    assert isinstance(m.get("total_seconds"), (int, float))
    assert isinstance(m.get("degraded_steps"), list)
    for optional in ("data_checks", "llm_extractor", "capability_health",
                     "mz_shadow", "delivery"):
        if m.get(optional) is not None:
            assert isinstance(m[optional], dict), \
                f"manifest 的 {optional} 型別錯:{type(m[optional]).__name__}"


# ------------------------------------------------- story ledger:轉載去重
#: 「有代號 vs 無代號」鏡像重複的暫時上限。**這個數字應該變成 0。**
#: 修這一類的是批#71 的跨桶比對(2026-07-30 11:25 落地),而目前落地的最新一批
#: 線索是 06:45 那次執行建立的 —— **批#71 還沒在生產跑過任何一次**。
#: 07-31 之後的執行若如預期把它降到 0,就把這裡改成 0 並刪掉這段說明。
#: 不現在就寫 0,是因為那會製造「沒有任何 commit 能修好的紅」(批#77 的教訓);
#: 不寫成嚴格棘輪(低於上限就要求調降),是因為重複對數隨當日新聞浮動,
#: 嚴格棘輪會在下一天反彈時變成修不好的紅 —— 那個模式只適合單調可控的量。
ENTITYLESS_MIRROR_DUP_CEILING = 3

#: 從真實 state 取出的**確認案例**:同一則〈卓揆視察中信銀亞灣分行〉的兩家轉載
#: (工商時報 / 中時新聞網,原本在 2026-07-27 那批各自成為獨立線索)。
#: r2(Codex,P2):非真空確認**不能靠掃描舊批次**——「舊批次也找不到重複」有
#: 兩種解釋(偵測器壞了 / 舊資料已汰換),而分不出來時只能 `pytest.skip`,
#: 那正是這一輪最該避免的靜默失效。釘住樣本就沒有這個歧義:它永遠會執行。
_KNOWN_SYNDICATED_PAIR = (
    {"key": "e:2891|l:general|known-a", "entity": "2891",
     "entity_name": "中信金", "state": "brewing", "updates": 1,
     "headline": "肯定留財引資卓揆視察中信銀行亞灣分行- 產業 - 工商時報"},
    {"key": "e:2891|l:general|known-b", "entity": "2891",
     "entity_name": "中信金", "state": "brewing", "updates": 1,
     "headline": "肯定留財引資 卓揆視察中信銀行亞灣分行 - 中時新聞網"},
)


def _unmerged_syndicated_pairs(cohort):
    """回傳 (雙方都有代號的重複, 至少一方無代號的重複)。

    判準完全比照 `_match_open_story` 的候選與門檻規則,不自己另寫一套:
      - 兩邊都有代號且不同 → 不同公司,跳過
      - 期別型且同型不同期 → 不同集,本來就該分開(6 月營收 vs 7 月營收)
      - 門檻:雙方都有代號用 `STORY_MATCH_THRESHOLD`,否則用較嚴的
        `STORY_MATCH_THRESHOLD_NO_ENTITY`
      - 比對文字取 `_story_match_candidates`(headline **加軌跡點標題**),
        並用**兩邊都知道的**代號/名稱做剝除(`cand_ent or ent`)

    r1(Codex,P2):第一版用 `key.split("|")[0]` 分組,無代號線索的 key 前綴是
    `e:cluster<digest>`(每條唯一)→ 恰好把批#71 修的鏡像情境整個排除掉。
    r2(Codex,P2):第二版只比 `headline`,而生產比的是候選清單 ——
    線索的 headline 會隨後續報導漂移(傳聞標題 → 公告標題),兩條重複線索
    各自漂移之後 headline 可能不像了,但軌跡裡仍留著一模一樣的轉載標題。
    """
    memo = {}

    def _subjects(idx, story, ent, name):
        hit = memo.get((idx, ent, name))
        if hit is None:
            hit = [s for s in (sl._story_subject(c, ent, name)
                               for c in sl._story_match_candidates(story)) if s]
            memo[(idx, ent, name)] = hit
        return hit

    prepared = [(i, r, str(r.get("entity") or ""),
                 str(r.get("entity_name") or ""),
                 sl._episodic_period_of_story(r))
                for i, r in enumerate(cohort)]
    both, mirror = [], []
    for pos, (ia, a, ea, na, pa) in enumerate(prepared):
        for ib, b, eb, nb, pb in prepared[pos + 1:]:
            if ea and eb and ea != eb:
                continue
            if pa and pb and pa[0] == pb[0] and pa[1] != pb[1]:
                continue
            threshold = (sl.STORY_MATCH_THRESHOLD if (ea and eb)
                         else sl.STORY_MATCH_THRESHOLD_NO_ENTITY)
            ent, name = (ea or eb), (na or nb)
            if any(sl._same_story_subject(sa, sb, threshold)
                   for sa in _subjects(ia, a, ent, name)
                   for sb in _subjects(ib, b, ent, name)):
                (both if (ea and eb) else mirror).append(
                    (str(a.get("key")), str(b.get("key")),
                     str(a.get("headline"))[:44], str(b.get("headline"))[:44]))
    return both, mirror


def test_the_syndication_detector_actually_detects():
    """**這條檢查自己不能是真空通過。**

    下面那條在最新一批上回報 0 —— 但「回報 0」也可能是因為判準壞了、分組錯了、
    或資料讀不到。r1 那次正是如此:分組把問題本體排除掉,於是報出漂亮的 0。

    所以用一組**從真實 state 取出的確認重複**釘住偵測器。刻意不掃描舊批次:
    掃不到時分不出「偵測器壞了」與「舊資料已汰換」,只能 skip,而 skip 就是
    靜默失效——那是這條測試存在的理由本身。
    """
    both, mirror = _unmerged_syndicated_pairs(list(_KNOWN_SYNDICATED_PAIR))
    assert len(both) == 1 and not mirror, (
        "偵測器認不出已確認的轉載重複(工商時報 / 中時新聞網 的同一則)——"
        f"雙方有代號 {len(both)} 對、含無代號 {len(mirror)} 對;"
        "在這種狀態下,主檢查回報的 0 沒有意義")

    # 軌跡點也要納入比對:兩條線索的 headline 各自漂移之後,
    # 生產仍會拿軌跡裡的舊標題去比(`_story_match_candidates`)。
    a, b = ({**_KNOWN_SYNDICATED_PAIR[0], "headline": "中信金今日法說會重點整理"},
            dict(_KNOWN_SYNDICATED_PAIR[1]))
    a["timeline"] = [{"d": "2026-07-27",
                      "t": _KNOWN_SYNDICATED_PAIR[0]["headline"]}]
    drifted, _ = _unmerged_syndicated_pairs([a, b])
    assert len(drifted) == 1, "headline 漂移後,軌跡裡的原標題仍必須被比對到"


def test_the_newest_cohort_has_no_unmerged_syndicated_duplicates():
    """**同一批建立的線索裡,不得有「該合併卻分開」的轉載重複。**

    批#80。用真實落地的 state 逐批量測(判準見 `_unmerged_syndicated_pairs`):

    ```
    first_seen   新建   雙方有代號   含無代號     生產時的程式碼
    2026-07-27    465       16          17        批#67 之前
    2026-07-28    508       11          19        批#67 之前
    2026-07-29    470        5           8        批#67 之前
    2026-07-30    451        0           3        批#67 有、批#71 無
    ```

    漏網樣本全是相似度 1.00、只差媒體名的轉載:
    「肯定留財引資 卓揆視察中信銀行亞灣分行」工商時報 / 中時新聞網 / 翻爆、
    「股息來了!國泰金今發513億」非凡新聞台 / Yahoo股市。

    **兩類分開驗,因為修它們的是不同批、上線時間也不同:**
      - 雙方都有代號 → 批#67 的主旨相似度歸屬(01:39 落地,已跑過一次)
        實測 5 → 0,所以硬性要求 0。
      - 至少一方無代號 → 批#71 的跨桶比對(11:25 落地,**還沒跑過**)。
        剩下的 3 對全是它的目標,包括它 docstring 裡點名的那一則:
        `e:clusterfaa9b7b77c|l:revenue_growth`(yahoo)與
        `e:2303|l:revenue_growth|202607`(cnyes)是同一篇〈聯電法說〉。

    只驗**最新一批**:舊資料是修正上線前留下的、永遠修不好,驗它就是製造
    「沒有任何 commit 能修好的紅」(批#77 已經踩過一次)。
    """
    rows = _load("story_ledger.json")
    if not rows:
        pytest.skip("線索帳本是空的")
    newest = max(str(r.get("first_seen") or "")[:10] for r in rows)
    if not newest:
        pytest.skip("線索帳本沒有 first_seen 欄位(舊格式)")

    cohort = [r for r in rows if str(r.get("first_seen") or "")[:10] == newest]
    both, mirror = _unmerged_syndicated_pairs(cohort)
    print(f"[state-contract] {newest} 新建 {len(cohort)} 條線索;"
          f"未合併轉載:雙方有代號 {len(both)} 對、含無代號 {len(mirror)} 對")

    def _fmt(pairs):
        return "\n  ".join(f"{a} ↔ {b}\n    {ha}\n    {hb}"
                           for a, b, ha, hb in pairs[:5])

    assert not both, (
        f"{newest} 這批有 {len(both)} 對**雙方都有代號**的線索該合併卻分開 —— "
        f"批#67 的主旨相似度歸屬可能退化:\n  {_fmt(both)}")
    assert len(mirror) <= ENTITYLESS_MIRROR_DUP_CEILING, (
        f"{newest} 這批有 {len(mirror)} 對含無代號的鏡像重複,"
        f"超過暫時上限 {ENTITYLESS_MIRROR_DUP_CEILING} —— "
        f"批#71 的跨桶比對可能退化:\n  {_fmt(mirror)}")
