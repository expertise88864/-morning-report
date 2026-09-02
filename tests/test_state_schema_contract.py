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
import collections as _co
import importlib as _importlib
import subprocess as _subprocess
import datetime as _dt
import json
import sys
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


def test_the_persisted_delivery_obeys_the_canonical_contract():
    """**落地的 state 要吃同一份契約**(2026-09-01 r9 外審)。

    先前這個檔只驗 `delivery` 是不是 dict —— 而 `delivery_contract.py`
    已經定義了完整的狀態機(success 精確布林、skipped_reason 必須是
    非空字串、兩個終局宣稱互斥)。在這裡手抄一份規格,遲早會與那份
    canonical contract 漂移;**共用它**才能做到

        unit contract = watchdog contract = idempotence contract
                      = persisted state contract
    """
    import delivery_contract as dc

    def _check(name, dv):
        """真實 state 與反例走**同一段**判定 —— 否則這道 gate 壞掉時,
        真實 state 剛好合法就永遠看不出來(突變驗證抓到的:改掉
        `delivery_verdict` 的呼叫,這條測試照樣綠)。"""
        outcome, defects = dc.delivery_verdict(dv)
        assert outcome != dc.OUTCOME_INVALID, (
            f"{name} 的 delivery 不符合 canonical contract:{dv!r}")
        assert not defects, (f"{name} 的 delivery 欄位互相對不上:{defects}")
        # 落地的收據/manifest 只會是**終局**(中間狀態不該被 commit)
        assert outcome in (dc.OUTCOME_DELIVERED, dc.OUTCOME_SKIPPED,
                           dc.OUTCOME_FAILED), (name, outcome)

    for name in ("run_manifest.json", "delivery_receipt.json"):
        payload = _load(name)
        dv = payload.get("delivery")
        if dv is not None:
            _check(name, dv)

    # **這道 gate 真的會抓嗎** —— 用反例證明它不是空轉
    import pytest
    for bad in ({"success": True, "skipped_reason": "w"},
                {"success": "false"},
                {"attempted": False, "success": False,
                 "skipped_reason": ["w"]}):
        with pytest.raises(AssertionError):
            _check("反例", bad)
    with pytest.raises(AssertionError):     # 結局正確但欄位對不上
        _check("反例", {"attempted": False, "success": True})


# ------------------------------------------------- story ledger:轉載去重
#: 未合併轉載的上限:取「絕對下限」與「批量佔比」的較大者。
#:
#: 批#84:**原本寫死小數字,結果 2026-07-31 在生產把 workflow 弄紅了。**
#: 那批去重後有 3 對、上限也是 3(而且當時還多算了一對,見 `_unmerged_…`)。
#: 這是個**隨當日新聞浮動**的量 —— 沒有任何 commit 能讓它變小。
#: 這正是我在批#77 自己寫過的錯:「在遷移窗口把 CI 弄紅,是沒有任何 commit
#: 能修好的紅,而那種紅會訓練人忽略 CI」。而且信照常寄出、只有這一步紅,
#: 更糟:它讓「workflow 失敗告警」與「使用者真的沒收到信」脫鉤。
#:
#: 改成相對門檻,任務也講清楚:**這是粗網,只負責攔住整體崩壞**
#: (歸屬邏輯真的壞掉時,轉載重複會是整批的一大部分)。實測基準:
#:   批#67 之前 16/465=3.4%、11/508=2.2%、5/470=1.1%
#:   批#67 之後 3/461=0.65%(含無代號那類已由批#71 在首次上線就降到 0)
#: 取 3% 對現況有 4.6 倍餘裕,又攔得住上線前的水準。
#:
#: r4(Codex,P2)的說明仍然成立:**粗網不是精密儀器**,兩者不可兼得 ——
#: 要容忍當日新聞浮動就必然遮蔽小幅退化。抓小幅退化是**單元測試**的工作
#: (`test_template_headlines_do_not_merge_different_companies` 對兩道防線
#: 各有一條獨立的機制斷言)。
DUP_RATIO_CEILING = 0.03
DUP_ABSOLUTE_FLOOR = 10


def _dup_ceiling(cohort_size: int) -> int:
    return max(DUP_ABSOLUTE_FLOOR, int(cohort_size * DUP_RATIO_CEILING))

#: 從真實 state 取出的**確認案例**:同一則〈卓揆視察中信銀亞灣分行〉的兩家轉載
#: (工商時報 / 中時新聞網,原本在 2026-07-27 那批各自成為獨立線索)。
_KNOWN_SYNDICATED_PAIR = (
    {"key": "e:2891|l:general|known-a", "entity": "2891",
     "entity_name": "中信金", "state": "brewing", "updates": 1,
     "event_type": "general", "last_published": "2026-07-27T09:00:00+08:00",
     "headline": "肯定留財引資卓揆視察中信銀行亞灣分行- 產業 - 工商時報"},
    {"key": "e:2891|l:general|known-b", "entity": "2891",
     "entity_name": "中信金", "state": "brewing", "updates": 1,
     "event_type": "general", "last_published": "2026-07-27T10:00:00+08:00",
     "headline": "肯定留財引資 卓揆視察中信銀行亞灣分行 - 中時新聞網"},
)


def _unmerged_syndicated_pairs(cohort, ledger):
    """把 `cohort` 裡的每條線索當成「剛抵達的事件」,**重播生產的
    `_match_open_story`** 去比對整份 `ledger`(排除自己)。

    r2(Codex,P2)一次、r3(Codex,P2)兩次的教訓都指向同一件事:
    **不要自己重寫判準**。前幾版分別踩到
      - 用 key 前綴分組 → 無代號線索每條自成一組,恰好排除批#71 修的情境
      - 只比 headline → 生產比的是 `_story_match_candidates`(含軌跡點標題)
      - 正規化用 `ea or eb` 剝兩側 → 生產只用事件自己的 entity 剝事件側,
        兩者在邊界上判斷不同
    每一版都是「我以為的判準」與生產判準之間的縫。直接呼叫生產函式就沒有縫。

    同時解掉「只在同一批內兩兩比」的缺口:生產遍歷整個 `by_key`,不限同日,
    所以這裡也拿最新一批對**整份帳本**比。忽略「雙方都不是最新一批」的配對
    (那是修正上線前的 legacy,永遠修不好)。

    **保真度限制(誠實說明)**:帳本是事後快照,線索的 entity/headline 可能在
    建立之後才被更新(批#71 的代號認領、後續報導改寫 headline)。所以這裡量到的
    是「以**現在**的欄位重播會不會合併」,不完全等於當初那一刻的判斷。
    因此用上限而非等式,並把它當**趨勢與退化偵測**,不當精確重現。
    """
    by_key = {str(r.get("key") or ""): r for r in ledger}
    both, mirror = [], []
    for story in cohort:
        key = str(story.get("key") or "")
        mine = by_key.pop(key, None)
        try:
            hit = sl._match_open_story(
                {"title": story.get("headline"),
                 "entity": story.get("entity"),
                 "entity_name": story.get("entity_name"),
                 "event_type": story.get("event_type"),
                 "published": story.get("last_published")},
                by_key)
        finally:
            if mine is not None:
                by_key[key] = mine
        if not hit:
            continue
        other = by_key.get(hit) or {}
        row = (key, hit, str(story.get("headline"))[:44],
               str(other.get("headline"))[:44])
        (both if (story.get("entity") and other.get("entity"))
         else mirror).append(row)

    # 批#84:**雙向重複要去掉。** 每條線索都被當成一次「剛抵達的事件」,所以
    # 同一對只要兩邊都在最新一批裡就會被算兩次(A↔B 與 B↔A),直接把數字灌成
    # 兩倍去撞上限。2026-07-31 生產實例:回報 4 對,實際只有 3 對 —— 而上限是 3,
    # 於是這個灌水正好是把 workflow 弄紅的最後一根稻草。
    def _uniq(pairs):
        seen, out = set(), []
        for item in pairs:
            key = frozenset(item[:2])
            if key not in seen:
                seen.add(key)
                out.append(item)
        return out

    return _uniq(both), _uniq(mirror)


def test_the_syndication_detector_actually_detects():
    """**這條檢查自己不能是真空通過。**

    下面那條回報的數字,只有在偵測器真的會偵測時才有意義。r1 那次正是分組把
    問題本體排除掉,於是報出漂亮的 0。

    用一組**從真實 state 取出的確認重複**釘住。刻意不掃描舊批次:掃不到時
    分不出「偵測器壞了」與「舊資料已汰換」,只能 `pytest.skip` —— 而靜默跳過
    正是這條測試存在的理由本身。
    """
    a, b = (dict(x) for x in _KNOWN_SYNDICATED_PAIR)
    both, mirror = _unmerged_syndicated_pairs([a], [a, b])
    assert len(both) == 1 and not mirror, (
        "偵測器認不出已確認的轉載重複(工商時報 / 中時新聞網 的同一則)——"
        f"雙方有代號 {len(both)} 對、含無代號 {len(mirror)} 對;"
        "在這種狀態下,主檢查回報的數字沒有意義")

    # 軌跡點也要納入比對:線索的 headline 會隨後續報導漂移(傳聞 → 公告),
    # 生產仍會拿軌跡裡的舊標題去比(`_story_match_candidates`)。
    drift_a, drift_b = (dict(x) for x in _KNOWN_SYNDICATED_PAIR)
    drift_b["headline"] = "中信金第二季法說會重點整理"
    drift_b["timeline"] = [{"d": "2026-07-27",
                            "t": _KNOWN_SYNDICATED_PAIR[1]["headline"]}]
    drifted, _ = _unmerged_syndicated_pairs([drift_a], [drift_a, drift_b])
    assert len(drifted) == 1, "headline 漂移後,軌跡裡的原標題仍必須被比對到"


def test_the_newest_cohort_has_no_unmerged_syndicated_duplicates():
    """**最新一批線索裡,不得有「該合併卻分開」的轉載重複。**

    批#80。用真實落地的 state 逐批重播生產判準:

    ```
    first_seen   新建   雙方有代號   含無代號     生產時的程式碼
    2026-07-27    465       16          17        批#67 之前
    2026-07-28    508       11          19        批#67 之前
    2026-07-29    470        5           8        批#67 之前
    2026-07-30    451        1           9        批#67 有、批#71 無
    ```

    漏網樣本全是相似度 1.00、只差媒體名的轉載:
    「肯定留財引資 卓揆視察中信銀行亞灣分行」工商時報 / 中時新聞網 / 翻爆、
    「〈聯電法說〉AI營收三年拚逾10億美元」yahoo / cnyes、
    「廣達拚擴產 砸197億買友達桃園廠房」台視 / UDN。

    只把**最新一批**當事件側:舊資料是修正上線前留下的、永遠修不好,
    驗它就是製造「沒有任何 commit 能修好的紅」(批#77 已經踩過一次)。
    但比對的另一側是**整份帳本** —— 生產本來就會拿新事件比所有既有線索,
    只在同一批內兩兩比會漏掉跨日退化(r3 Codex)。
    """
    rows = _load("story_ledger.json")
    if not rows:
        pytest.skip("線索帳本是空的")
    newest = max(str(r.get("first_seen") or "")[:10] for r in rows)
    if not newest:
        pytest.skip("線索帳本沒有 first_seen 欄位(舊格式)")

    cohort = [r for r in rows if str(r.get("first_seen") or "")[:10] == newest]
    both, mirror = _unmerged_syndicated_pairs(cohort, rows)
    print(f"[state-contract] {newest} 新建 {len(cohort)} 條線索(對照全帳本 "
          f"{len(rows)} 條);未合併轉載:雙方有代號 {len(both)} 對、"
          f"含無代號 {len(mirror)} 對")

    def _fmt(pairs):
        return "\n  ".join(f"{a} ↔ {b}\n    {ha}\n    {hb}"
                           for a, b, ha, hb in pairs[:5])

    cap = _dup_ceiling(len(cohort))
    assert len(both) <= cap, (
        f"{newest} 這批 {len(cohort)} 條線索裡有 {len(both)} 對**雙方都有代號**"
        f"的該合併卻分開,超過上限 {cap} —— "
        f"批#67 的主旨相似度歸屬可能**整體**失效:\n  {_fmt(both)}")
    assert len(mirror) <= cap, (
        f"{newest} 這批有 {len(mirror)} 對含無代號的鏡像重複,超過上限 {cap} —— "
        f"批#71 的跨桶比對可能**整體**失效:\n  {_fmt(mirror)}")


# ---------------------------------------------- 閉世界:每個 state 檔都要有人管
#: **這道 gate 的理念是 fail-closed,但它的涵蓋清單本身是 open-world**
#: (2026-09-02 r11 外審)。實測 `state/` 下有 90 個檔,而點名到的只有 5 個。
#:
#: workflow 的順序是「寫 state → local commit → **這個檔** → 通過才 push」,
#: 設計目的就是「壞掉的 state 不可以先進 main 再事後發現」。但如果某個
#: writer 新增了一個檔、或既有檔的 schema 漂掉而這裡根本沒碰它,
#: pytest 全綠 → 照樣 push → 明天的新 runner 讀到壞檔。
#:
#: `analysis_recap` 不是小角落:2026-08-08 的真實事故就是它寫了但沒進 push,
#: 而 GitHub Actions 每天是新 runner —— 次日永遠讀不到,整條閉環 no-op,
#: 本機測試仍全綠。
#:
#: 所以這裡改成閉世界:**每一個 state 檔都必須對上一條規則**,
#: 對不上就紅。新增 writer 時,CI 會直接告訴你檔名。
#: 一個 state 檔的契約:**根型別 + 可執行的驗證器**。
#:
#: r12 外審:先前這裡放的是一串「應該有某個測試在驗它」的**字串**,
#: 而其中四個名字根本不存在於這個檔(改名/重寫之後沒人發現)。
#: 那個 registry 於是只是一段文字宣稱 —— declared ≠ mechanically exercised,
#: 與上一輪 finding-domain 的動態工廠是同一類問題。
#:
#: 而 `"shape"` 那一類也只驗 `isinstance(data, (dict, list))` ——
#: `analysis_recap` 從 dict 變成 list 也會通過,而它正是昨日觀點閉環的核心:
#: 那種錯不會讓晨報 crash,只會讓明天的連續性**無聲消失**。
#: `required`:**這個檔在成熟的 production 上必須存在**。
#: r13 外審:closed-world 只防「多出陌生檔」,沒防「必要檔消失」——
#: `if not path.exists(): continue` 加上「跑到的契約數 >= 10」這個總量
#: 下限,等於**刪掉一個(甚至好幾個)檔仍然全綠**。
#: 而 `analysis_recap.load()` 對不存在的檔回 `{}` —— 那會被當成
#: 「今天沒有昨日觀點」而不是損壞:state gate 綠 → push → 明天新 runner
#: 讀到空的 → **連續性無聲消失**。正是這套系統一直在防的形狀。
#:
#: 但**不可以粗暴地全部 required**:全新 repo 與功能還沒跑過時本來就沒有。
#: 用 `required_from`(台北日期)當歷史錨點 —— 與 `MANIFEST_SCHEMA_REQUIRED_FROM`
#: 同一個手法:那一天之後這個檔還不見,就不是「還沒跑過」。
Contract = _co.namedtuple("Contract", "root validate required_from")
Contract.__new__.__defaults__ = (None,)


def _required_today(contract, today) -> bool:
    """這個檔今天必須存在嗎。`required_from` 是 None = 永遠選擇性。"""
    if not contract.required_from:
        return False
    return today >= contract.required_from


def _rows(data, key=None):
    return data[key] if key else data


def _v_analysis_recap(d):
    assert isinstance(d.get("items"), list), "items 不是 list"
    assert isinstance(d.get("date"), str) and _iso_like(d["date"]), d.get("date")
    for it in d["items"]:
        assert isinstance(it, dict), type(it).__name__


def _v_event_timeline(d):
    """鍵是事件身分(`類型:主體:月份`),值是那條線的狀態。

    **空 mapping 是合法的**(r12 外審第二輪):`update_event_timeline()`
    會把超過 3 天沒更新的事件全部退場,連續幾天沒有可追蹤事件時
    寫出來就是 `{}`。把它判成損壞的話 → state 契約紅 → **發佈被跳過**
    → 那一班**其他所有 state 也全部不落地**。修正比原問題嚴重。

    **全部走完,不取樣**:消費端會遍歷所有 entries 並呼叫 `v.get()`,
    第 21 筆之後壞掉一樣會讓那個區塊整體降級。
    """
    for k, v in d.items():
        assert ":" in k, f"事件鍵不像身分:{k!r}"
        assert isinstance(v, dict), (k, type(v).__name__)


def _v_source_health(rows):
    """30 天來源健康史 —— **全部驗完,不取樣**(r12 外審第二輪)。

    writer 是 append 之後排序保留 30 筆,所以**最新的資料排在後面**:
    只驗前 20 筆的話,連續失敗判斷真正要用的那十筆從來沒被檢查過。
    """
    assert rows, "來源健康史是空的 —— 連續失敗算不出來"
    for r in rows:
        assert isinstance(r, dict) and _iso_like(str(r.get("date") or "")), r
        assert isinstance(r.get("checks"), dict), r.get("checks")


def _v_model_history_manifest(_d):
    """**接上正式 consumer 的驗證器,不要手抄一份較弱的**(r13 外審)。

    先前這裡自己寫「partitions 是 list 或 dict 且非空、schema_version 是 int」
    —— 而 `model_history_store` 的正式契約是:partitions **必須是 dict**、
    `schema_version` 必須等於 `HISTORY_SCHEMA_VERSION`(目前 3)。
    於是 `{"schema_version": 999, "partitions": ["foo"]}` 在這道 gate 通過,
    在真正的 consumer 卻是 corrupt —— **publish gate 與消費端說不同的話**。

    `verify_history_integrity()` 一次涵蓋:JSON 解析、root 型別、每列形狀、
    `session_date`、月份與檔名一致、SHA256、row count、manifest 少列/多列、
    schema 世代。那才是 single source of truth。
    """
    import model_history_store as _mh
    report = _mh.verify_history_integrity(
        partition_dir=STATE / "model_history", require_manifest=True)
    assert report.get("ok"), report.get("issues")


def _v_nonempty_mapping(d):
    assert d, "是空的"


def _v_nonempty_rows(rows):
    assert rows, "是空的"


#: 每個 state 檔都要對上一條 —— 而且 validator **會被真的呼叫**
#: (`test_every_contract_is_actually_executed`)。
STATE_CONTRACTS = {
    "run_manifest.json": Contract(dict, _v_nonempty_mapping, "2026-08-01"),
    "delivery_receipt.json": Contract(dict, _v_nonempty_mapping, "2026-08-29"),
    "model_history.json": Contract(list, _v_nonempty_rows, "2026-08-01"),
    "story_ledger.json": Contract(list, _v_nonempty_rows, "2026-08-01"),
    "forecast_ledger.json": Contract(list, _v_nonempty_rows),
    "exdiv_history.json": Contract(dict, _v_nonempty_mapping),
    # r12:這四個是下一班 continuity / observability 價值最高的
    "analysis_recap.json": Contract(dict, _v_analysis_recap, "2026-08-08"),
    "event_timeline.json": Contract(dict, _v_event_timeline),
    "source_health_history.json": Contract(list, _v_source_health, "2026-08-01"),
    "model_history/manifest.json": Contract(dict, _v_model_history_manifest, "2026-08-01"),
    # 其餘先鎖**根型別**(比 `isinstance(dict, list)` 嚴格),語意分批補
    "conformal_intervals.json": Contract(dict, _v_nonempty_mapping),
    "corporate_actions.json": Contract(dict, _v_nonempty_mapping),
    "podcast_digest.json": Contract(dict, _v_nonempty_mapping),
    "policy_keywords.json": Contract(list, _v_nonempty_rows),
    "poly_history.json": Contract(dict, _v_nonempty_mapping),
    "sector_rank_history.json": Contract(dict, _v_nonempty_mapping),
    "history.json": Contract(list, _v_nonempty_rows),
    "cpbl_venues.json": Contract(dict, _v_nonempty_mapping),
    "gooaye_radar.json": Contract(dict, _v_nonempty_mapping),
    "deepseek_canary.json": Contract(dict, _v_nonempty_mapping),
    "history_index.jsonl": Contract("jsonl", _v_nonempty_rows),
}

#: 檔名帶日期/月份的家族 —— 逐檔列不完,但**規則要列得出來**。
#: 值是驗證器,同樣會被真的呼叫。
def _v_gzip_intact(path):
    """讀到 EOF 才算數:gzip 的長度與 CRC 記在**尾端**,只讀開頭的話
    後半被截斷的檔照樣通過,而消費端是整份讀的(r11 外審)。"""
    import gzip
    total = 0
    with gzip.open(path, "rb") as fh:
        while True:
            chunk = fh.read(1 << 16)
            if not chunk:
                break
            total += len(chunk)
    assert total, f"{path.name} 解出來是空的"


#: 一列 model-history 至少要有的欄位 —— **不是「有 session_date 就算」**。
#: r14 外審:`verify_history_integrity()` 是**一致性** verifier(checksum、
#: row_count、月份、manifest 對得上),它保證 state **自洽**,不保證
#: state **有意義**。三個反例實測全部放行:
#:   `[]`(空分區)、重複的 `session_date`、只有 `session_date` 的空殼。
#: 而正式 loader 是 `merged[row["session_date"]] = row` —— 重複的第二列
#: 直接蓋掉第一列:**manifest 說 2 列,消費端只拿到 1 個交易日**。
_PARTITION_REQUIRED_FIELDS = ("session_date", "taiex_close", "stocks",
                              "model_version")


def _v_model_partition(path):
    """分區的**一致性**由 `verify_history_integrity()` 驗;**語意**在這裡。

    兩層分開:前者答「這份 state 自洽嗎」,後者答「它還有意義嗎」。
    """
    import gzip
    import json as _json
    _v_gzip_intact(path)
    month = path.name.split(".", 1)[0]      # `2026-09.json.gz` → `2026-09`
    with gzip.open(path, "rb") as fh:
        rows = _json.loads(fh.read())
    assert isinstance(rows, list) and rows, f"{path.name} 是空分區"
    seen = set()
    for i, row in enumerate(rows, 1):
        assert isinstance(row, dict), (path.name, i, type(row).__name__)
        missing = [k for k in _PARTITION_REQUIRED_FIELDS if k not in row]
        assert not missing, f"{path.name} 第 {i} 列缺欄位:{missing}"
        day = str(row.get("session_date") or "")
        # **必須剛好是 `YYYY-MM-DD`**(r14 外審第二輪):`_iso_like()` 走
        # `datetime.fromisoformat()`,`"2026-09-01T08:00:00"` 也會過 ——
        # 而 loader 是 `merged[row["session_date"]] = row`,拿**原字串**
        # 當鍵:`"2026-09-01"` 與 `"2026-09-01T08:00:00"` 是兩個不同的鍵,
        # 同一個交易日於是變成兩個 session,而重複檢查也看不出來。
        try:
            parsed = _dt.date.fromisoformat(day)
        except ValueError:
            parsed = None
        assert parsed is not None and parsed.isoformat() == day, (
            f"{path.name} 第 {i} 列的 session_date 不是 YYYY-MM-DD:{day!r}"
            " —— loader 用原字串當 merge key,多一個時間部分就是另一個 session")
        assert day[:7] == month, f"{path.name} 第 {i} 列的月份是 {day[:7]}"
        assert day not in seen, (
            f"{path.name} 有重複的 session_date {day} —— "
            "loader 是 `merged[session_date] = row`,第二列會直接蓋掉第一列:"
            "manifest 說幾列,消費端拿到的卻更少")
        seen.add(day)


STATE_PATTERNS = (
    ("emails/*.html.gz", _v_gzip_intact),          # 寄出信件存檔(去識別)
    ("model_history/*.json.gz", _v_model_partition),
)

#: 不是「跨日累積的 state」的東西(目前沒有;留這一格是為了讓豁免**顯式**)。
STATE_EXEMPTIONS: dict = {}


def _state_files():
    return sorted(
        str(p.relative_to(STATE)).replace("\\", "/")
        for p in STATE.rglob("*") if p.is_file())


def test_every_state_file_is_covered_by_a_rule():
    """**閉世界**:`state/` 下的每一個檔都要對上一條規則。

    對不上就紅,而且訊息直接給檔名 —— 新增 writer 卻忘了寫契約時,
    這道 gate 會在 push 之前擋下來(它跑在 local commit 之後、push 之前)。
    """
    import fnmatch
    files = _state_files()
    assert len(files) >= 10, ("掃描器疑似失配", files[:5])
    unknown = []
    for rel in files:
        if rel in STATE_CONTRACTS or rel in STATE_EXEMPTIONS:
            continue
        if any(fnmatch.fnmatch(rel, pat) for pat, _ in STATE_PATTERNS):
            continue
        unknown.append(rel)
    assert not unknown, (
        f"這些 state 檔沒有任何契約或豁免:{unknown}\n"
        "新增跨日累積的 state 時要在 STATE_CONTRACTS 加一行(並補斷言),"
        "檔名帶日期的家族加進 STATE_PATTERNS,"
        "真的不需要契約請加進 STATE_EXEMPTIONS 並註明理由。")


def test_every_contract_is_actually_executed():
    """**registry 要可執行,不能只是文字宣稱**(2026-09-02 r12 外審)。

    先前這裡放的是「應該有某個測試在驗它」的**字串**,而其中四個名字
    根本不存在於這個檔 —— 改名/重寫之後沒有人發現。
    現在每一條都是 `Contract(root, validate)`,而這條測試**真的呼叫**
    每一個 validator,並記錄跑過幾個:少一個就紅。

    ★換成 namedtuple 的當下,三條舊測試(`kind != "shape"` / `"jsonl"` /
    `"gzip"`)瞬間全部空轉而照樣綠 —— 那正是這條斷言存在的理由。★
    """
    today = _dt.datetime.now(
        _dt.timezone(_dt.timedelta(hours=8))).strftime("%Y-%m-%d")
    executed, missing = [], []
    for rel, contract in sorted(STATE_CONTRACTS.items()):
        path = STATE / rel
        if not path.exists():
            # **「不存在」也要有契約**(r13 外審):先前一律 `continue`,
            # 加上「跑到的契約數 >= 10」這個總量下限,等於刪掉一個(甚至
            # 好幾個)必要檔仍然全綠。而 `analysis_recap.load()` 對不存在
            # 的檔回 `{}` —— 那會被讀成「今天沒有昨日觀點」而不是損壞。
            if _required_today(contract, today):
                missing.append(f"{rel}(自 {contract.required_from} 起必須存在)")
            continue                        # 該功能還沒跑過
        if contract.root == "jsonl":
            rows = []
            for i, ln in enumerate(
                    path.read_text(encoding="utf-8").splitlines(), 1):
                if not ln.strip():
                    continue
                try:
                    row = json.loads(ln)
                except Exception as e:      # noqa: BLE001
                    pytest.fail(f"{rel} 第 {i} 行解不開:{e}")
                assert isinstance(row, dict), (rel, i, type(row).__name__)
                rows.append(row)
            contract.validate(rows)
        else:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception as e:          # noqa: BLE001
                pytest.fail(f"{rel} 讀不動:{e}")
            # **精確型別**:`isinstance(data, (dict, list))` 讓
            # `analysis_recap` 從 dict 變 list 也會通過,而它是昨日觀點
            # 閉環的核心 —— 那種錯不會 crash,只會讓明天的連續性無聲消失。
            assert type(data) is contract.root, (
                f"{rel} 的根型別是 {type(data).__name__},契約要求 "
                f"{contract.root.__name__}")
            contract.validate(data)
        executed.append(rel)
    assert not missing, (
        f"成熟 production 的必要 state 不見了:{missing} —— "
        "消費端多半會把它讀成「今天沒有」而不是「壞掉了」,"
        "那正是這套系統一直在防的無聲失效。"
        "確定要移除某個 state,請把它的 required_from 拿掉並註明理由。")
    assert len(executed) >= 10, ("跑到的契約太少,registry 可能沒被執行",
                                 executed)


def test_the_blob_families_are_intact():
    """gzip 家族:**逐個家族**算 —— 寫成「總共至少驗到一個」的話,
    某一條 pattern 打錯了也還有另一條撐著(突變驗證抓到的白測)。"""
    import fnmatch
    files = _state_files()
    for pat, validate in STATE_PATTERNS:
        matched = [r for r in files if fnmatch.fnmatch(r, pat)]
        assert matched, f"pattern {pat!r} 一個檔都沒對上 —— 它可能打錯了"
        for rel in matched:
            validate(STATE / rel)



def test_an_empty_timeline_is_legal_not_broken():
    """r12 外審第二輪:`update_event_timeline()` 會把超過 3 天沒更新的事件
    **全部**退場,連續幾天沒有可追蹤事件時寫出來就是 `{}`。

    把它判成損壞的話 → state 契約紅 → **發佈被跳過** → 那一班**其他所有
    state 也全部不落地**。★修正比原問題嚴重★:原本只是「少驗一個檔」,
    變成「整天的持久化都掉了」。
    """
    _v_event_timeline({})                   # 不可以拋
    # 但有 entry 時仍然要驗形狀
    with pytest.raises(AssertionError):
        _v_event_timeline({"沒有冒號": {}})
    with pytest.raises(AssertionError):
        _v_event_timeline({"a:b:2026-09": "不是 dict"})


def test_the_bounded_state_is_validated_all_the_way_through():
    """r12 外審第二輪:兩個 validator 都只走前 20 筆,而
    `source_health_history` 有 **30 筆**、writer 是 append 後排序保留 ——
    **最新的資料排在後面**,連續失敗判斷真正要用的那十筆從來沒被驗過。
    timeline 的消費端也會遍歷所有 entries 並呼叫 `v.get()`。
    """
    rows = [{"date": "2026-08-01", "checks": {}} for _ in range(29)]
    rows.append({"date": "壞掉的日期", "checks": {}})
    with pytest.raises(AssertionError):
        _v_source_health(rows)              # 第 30 筆
    many = {f"k{i}:x:2026-09": {} for i in range(25)}
    many["沒有冒號的鍵"] = {}
    with pytest.raises(AssertionError):
        _v_event_timeline(many)             # 第 26 筆
    recap = {"date": "2026-09-02", "eligible": 1,
             "items": [{} for _ in range(25)] + ["不是 dict"]}
    with pytest.raises(AssertionError):
        _v_analysis_recap(recap)            # 第 26 筆
    # 真實資料的筆數確實超過 20 —— 否則上面三條只是理論
    real = json.loads(
        (STATE / "source_health_history.json").read_text(encoding="utf-8"))
    assert len(real) > 20, ("來源健康史只有 %d 筆,取樣與否量不出差別"
                            % len(real))


def test_a_missing_required_state_is_not_silence(tmp_path, monkeypatch):
    """r13 外審:closed-world 只防「多出陌生檔」,沒防「必要檔消失」——
    `if not path.exists(): continue` 加上「跑到的契約數 >= 10」這個**總量**
    下限,等於刪掉一個(甚至好幾個)必要檔仍然全綠。

    而 `analysis_recap.load()` 對不存在的檔回 `{}` —— 那會被讀成
    「今天沒有昨日觀點」而不是損壞:gate 綠 → push → 明天新 runner
    讀到空的 → **連續性無聲消失**。
    """
    # **registry-driven,不要手抄名單**(r14 外審):上一版列了 5 個
    # 而實際標成必要的有 7 個 —— 「宣稱七個、實測五個」正是這一整批
    # 在修的那種問題(declared ≠ mechanically exercised)。
    required = {rel: c for rel, c in STATE_CONTRACTS.items() if c.required_from}
    assert len(required) >= 7, ("必要 state 少於預期", sorted(required))
    for rel, c in required.items():
        assert _required_today(c, "2026-09-03"), rel
        # 而歷史錨點之前不算(全新 repo / 功能還沒跑過時本來就沒有)
        assert not _required_today(c, "2026-07-01"), rel
    # 沒標 required_from 的仍然是選擇性
    opt = STATE_CONTRACTS["gooaye_radar.json"]
    assert not opt.required_from
    assert not _required_today(opt, "2026-12-31")

    # **在 tmp 造一份 state 樹來驗,絕不搬動真實的**(r13 外審第二輪):
    # 我原本用 `shutil.move` 把 `analysis_recap.json` 搬走再 `finally` 搬回
    # —— pytest 被強制中斷時那個檔就永遠不見了,而它正是這條測試剛標成
    # 「必要」的那一個。而且 conftest 的守衛當時**沒擋住**(它 patch 了
    # `os.replace` 但沒 patch `os.rename`,而 `shutil.move` 走後者)——
    # 守衛的漏洞讓人以為有保護,那比沒有守衛更糟。已一併補上。
    import shutil
    # **整棵複製再刪一個** —— 逐項挑會漏掉 `STATE_PATTERNS` 那些分區檔,
    # 而 `verify_history_integrity()` 會因此報 missing_partition:
    # 那是測試自己造出來的假故障,不是被測的性質。
    # **逐個必要檔都要證明缺席會紅**(而不是只證明其中一個)——
    # 而且要**數得出來**:少驗幾個的話下面那條斷言會紅
    # (突變驗證抓到:改成只驗 `analysis_recap` 時測試照樣過)。
    proved = []
    for rel in sorted(required):
        fake = tmp_path / rel.replace("/", "_")
        shutil.copytree(STATE, fake)
        (fake / rel).unlink()
        monkeypatch.setattr(sys.modules[__name__], "STATE", fake)
        try:
            with pytest.raises(AssertionError, match="必要 state"):
                test_every_contract_is_actually_executed()
        finally:
            monkeypatch.undo()
        proved.append(rel)
    assert sorted(proved) == sorted(required), (
        "有必要 state 沒有被證明「缺席會紅」", sorted(set(required) - set(proved)))
    assert (STATE / "analysis_recap.json").exists(), "真實 state 被動到了"


def _fake_history(manifest, tmp=[]):
    """在暫存目錄造一份 model_history 樹(不碰真實 state)。"""
    import json as _json
    import tempfile
    d = Path(tempfile.mkdtemp()) / "model_history"
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(
        _json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    tmp.append(d)
    return d


def test_the_history_gate_speaks_the_consumer_contract():
    """r13 外審:`model_history` 的 gate 先前手抄了一份**較弱**的規格
    ——「partitions 是 list 或 dict 且非空、schema_version 是 int」——
    而正式 consumer 要求 partitions **必須是 dict**、`schema_version`
    必須等於 `HISTORY_SCHEMA_VERSION`。

    於是 `{"schema_version": 999, "partitions": ["foo"]}` 在 publish gate
    通過、在真正的消費端卻是 corrupt。**已經有 single source of truth,
    就不要在 gate 再抄一份。**
    """
    import model_history_store as mh
    src = Path(__file__).read_text(encoding="utf-8")
    assert "verify_history_integrity" in src, (
        "gate 又自己手抄了一份 model_history 的規格")
    # gate 用的就是消費端那一支
    report = mh.verify_history_integrity(
        partition_dir=STATE / "model_history", require_manifest=True)
    assert report["ok"], report["issues"]
    _v_model_history_manifest(None)          # 不可以拋

    # **而它真的比手抄的嚴格**:先前那份會放行的東西,消費端的驗證器不會。
    # (在 tmp 目錄驗 —— conftest 禁止測試寫真實 state,那道守衛是對的。)
    bad = mh.verify_history_integrity(
        partition_dir=_fake_history(
            {"schema_version": 999, "partitions": ["foo"]}),
        require_manifest=True)
    assert not bad["ok"], "手抄版會放行的 manifest,消費端也放行了"
    assert any("partitions" in i or "schema" in i
               for i in map(str, bad["issues"])), bad["issues"]


def test_the_state_guard_covers_shutil_move(tmp_path):
    """r13 外審第二輪:conftest 的守衛 patch 了 `Path.rename` /
    `Path.replace` / `os.replace`,**卻沒有 `os.rename`** —— 而
    `shutil.move` 走的正是後者。

    於是我上一版那條「把真實 `analysis_recap.json` 搬走再 finally 搬回」
    的測試**完全沒有被擋下**;pytest 一旦被強制中斷,那個檔就永遠不見了
    (而它正是同一批剛標成「必要」的那一個)。

    ★守衛自己的漏洞讓人以為有保護,那比沒有守衛更糟。★
    """
    import os as _os
    import shutil
    # **用一個不存在的 state 路徑** —— 守衛看的是路徑,不是檔案在不在。
    # ★這條測試的第一版拿真實的 `analysis_recap.json` 當實驗品:
    # 突變(把守衛改回只有 `replace`)的那一刻,它就真的被搬走了,
    # 而 tmp_path 隨後被清掉 —— **我在驗證這個修正時親手弄丟了它**,
    # 靠 `git checkout` 才救回來。守衛的測試自己不可以需要那個守衛。★
    ghost = STATE / "__guard_probe_does_not_exist__.json"
    assert not ghost.exists()
    for call, label in (
            (lambda: shutil.move(str(ghost), str(tmp_path / "x.json")),
             "shutil.move"),
            (lambda: _os.rename(str(ghost), str(tmp_path / "y.json")),
             "os.rename"),
            (lambda: _os.replace(str(ghost), str(tmp_path / "z.json")),
             "os.replace")):
        with pytest.raises(AssertionError, match="真實 state"):
            call()
    # 真實的必要 state 從頭到尾沒有被碰過
    assert (STATE / "analysis_recap.json").exists()


def _fake_partition(rows, month="2026-09"):
    """在暫存目錄造一個分區檔(不碰真實 state)。"""
    import gzip
    import json as _json
    import tempfile
    p = Path(tempfile.mkdtemp()) / f"{month}.json.gz"
    with gzip.open(p, "wb") as fh:
        fh.write(_json.dumps(rows, ensure_ascii=False).encode())
    return p


def test_the_partition_semantics_are_checked():
    """r14 外審:`verify_history_integrity()` 是**一致性** verifier ——
    它保證 state **自洽**(checksum、row_count、月份、manifest 對得上),
    不保證 state **有意義**。三個反例實測全部放行。

    最漂亮的是重複的 `session_date`:正式 loader 是
    `merged[row["session_date"]] = row`,第二列直接蓋掉第一列 ——
    **manifest 說 2 列,消費端只拿到 1 個交易日**,而 checksum 與
    row_count 都完全相符。那不是「不一致」,是「一致地錯」。
    """
    good = [{"session_date": "2026-09-01", "taiex_close": 24000,
             "stocks": {}, "model_version": "v1"}]
    _v_model_partition(_fake_partition(good))       # 正常的不可以誤擋

    for name, rows in (
            ("空分區", []),
            ("重複 session_date", good + [dict(good[0], taiex_close=99999)]),
            ("只有 session_date 的空殼", [{"session_date": "2026-09-01"}]),
            ("月份對不上", [dict(good[0], session_date="2026-08-01")]),
            ("列不是 dict", ["不是 dict"]),
            ("日期不是 ISO", [dict(good[0], session_date="壞掉")]),
            # r14 外審第二輪:`_iso_like()` 走 `datetime.fromisoformat()`,
            # **timestamp 也會過** —— 而 loader 拿原字串當 merge key,
            # `"2026-09-01"` 與 `"2026-09-01T08:00:00"` 是兩個不同的鍵:
            # 同一個交易日變成兩個 session,而重複檢查也看不出來。
            ("timestamp 當 session_date",
             [dict(good[0], session_date="2026-09-01T08:00:00")]),
            ("同日的 date 與 timestamp 並存",
             good + [dict(good[0], session_date="2026-09-01T08:00:00")]),
            ("非零填充的日期", [dict(good[0], session_date="2026-9-1")])):
        with pytest.raises(AssertionError):
            _v_model_partition(_fake_partition(rows))

    # 真實分區照樣通過(否則上面那些反例只是理論)
    import fnmatch
    real = [r for r in _state_files()
            if fnmatch.fnmatch(r, "model_history/*.json.gz")]
    assert real, "沒有真實分區可驗"
    for rel in real:
        _v_model_partition(STATE / rel)


def test_the_state_invariant_actually_fails_the_run(monkeypatch):
    """r14 外審第二輪:那道 git 不變式先前**只 print,退出碼仍是 0**
    —— CI 照樣綠,而它的整個目的就是在 push 之前擋住。
    ★而我驗證它時只看「有沒有印出警告」,沒看退出碼 ——
    那是「印出來 ≠ 擋下來」的觀測版本,同一種錯的兩面。★

    ★第二個教訓(r14 第三輪,P1):這條測試的上一版**在真實 repo 裡跑
    探針**,讓子 pytest 刪掉真的 `gooaye_radar.json`,再
    `git checkout -- state/` 收尾 —— 那會**無條件丟棄使用者所有未提交的
    state 修改**;而我在同一批才剛寫下「刻意不自動還原,那會把使用者的
    修改一起丟掉」。現在改用假的 session 直接驗 hook:不碰 repo 一個位元組。★
    """
    import types
    conftest = _importlib.import_module("conftest")
    assert hasattr(conftest, "pytest_sessionfinish")

    def _run_hook(returncode, stdout):
        def _fake_run(*a, **kw):
            return types.SimpleNamespace(returncode=returncode,
                                         stdout=stdout, stderr="")
        monkeypatch.setattr(_subprocess, "run", _fake_run)
        session = types.SimpleNamespace(exitstatus=0)
        try:
            conftest.pytest_sessionfinish(session, 0)
        finally:
            monkeypatch.undo()
        return session.exitstatus

    assert _run_hook(0, "") == 0                        # 乾淨 → 不動
    assert _run_hook(0, " M state/analysis_recap.json") != 0, (
        "測試偷改了 state,而整輪 pytest 的退出碼仍是 0 —— "
        "印出來不等於擋下來")
    assert _run_hook(0, " D state/gooaye_radar.json") != 0
    # git 查不動 = 不知道,而不知道不可以被讀成「沒事」
    assert _run_hook(128, "") != 0, "git status 失敗被當成 clean"

    # 這條測試自己不製造它要防的災難
    assert (STATE / "gooaye_radar.json").exists()
