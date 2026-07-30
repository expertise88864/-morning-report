"""批#50:資料品質閘——擋「來源沒掛,但資料是壞的」。

既有韌性擋的是「來源掛掉」(熔斷、來源分級、健康史、四條整封信失敗路徑)。
缺的是另一類:**HTTP 回 200、熔斷不觸發、來源健康滿分,但內容是壞的**。
例如某天股票池只抓到 3 檔而非往常的 100 檔——所有既有防線都不會響,
但預測/計分/Top5 全部已被污染,而且不會有人知道。這正是「靜默失效」。
"""
import data_quality as dq
import morning_report as mr


def _rows(n, **over):
    base = {"code": "2330", "close": 1000.0, "market_cap": 1e13, "day_pct": 1.0}
    return [dict(base, **over) for _ in range(n)]


def test_row_count_threshold_comes_from_history_not_magic_number():
    """門檻應由歷史中位數自動推出,而不是寫死。"""
    hist = [100] * 20
    r = dq.check_row_count("u", _rows(30), min_rows=10, history=hist)
    assert not r.passed, "30 筆遠低於歷史 100,應判失敗"
    assert "歷史中位數" in r.detail
    assert dq.check_row_count("u", _rows(80), min_rows=10, history=hist).passed


def test_row_count_uses_median_not_mean():
    """用中位數而非平均:單日異常值會把平均拉低,讓門檻自己跟著壞掉
    ——那正是這個檢查要防的情況。"""
    hist = [100, 100, 100, 100, 100, 3]     # 有一天壞掉
    # r3(突變測試):**原本餵 20 筆,而中位數門檻 50 與平均門檻 41 都 > 20,
    # 兩種基準都會判失敗 → 這條測試分不出中位數和平均**(把 median 改成 mean
    # 的突變存活)。餵 45 筆才落在兩個門檻之間,真正區分得出來。
    r = dq.check_row_count("u", _rows(45), min_rows=5, history=hist)
    assert not r.passed, "被異常值拉低的門檻放行了壞資料(用了平均而非中位數?)"
    assert "歷史中位數 100" in r.detail, f"門檻基準不是中位數:{r.detail}"


def test_row_count_falls_back_when_history_too_short():
    r = dq.check_row_count("u", _rows(5), min_rows=30, history=[100, 100])
    assert not r.passed and "硬門檻" in r.detail


def test_required_fields_uses_ratio_not_any_missing():
    """真實資料本來就有零星缺值(停牌股沒開盤價),一有缺就擋會讓晨報天天降級;
    但缺值比率飆高就是上游 schema 變了。"""
    rows = _rows(100)
    rows[0]["market_cap"] = None                      # 1% 缺值
    assert dq.check_required_fields(
        "u", rows, fields=("code", "market_cap")).passed
    for r_ in rows[:30]:
        r_["market_cap"] = None                       # 30% 缺值
    bad = dq.check_required_fields("u", rows, fields=("code", "market_cap"))
    assert not bad.passed and "market_cap" in bad.detail


def test_value_range_tolerates_single_outlier():
    vals = [1.0] * 100 + [50.0]
    assert dq.check_value_range("u", vals, lo=-11, hi=11).passed
    many = [50.0] * 20 + [1.0] * 80
    assert not dq.check_value_range("u", many, lo=-11, hi=11).passed


def test_empty_input_fails_rather_than_passing_vacuously():
    """空輸入不得靜默通過——那正是「來源回 200 但內容是空的」的樣子。"""
    assert not dq.check_required_fields("u", [], fields=("code",)).passed
    assert not dq.check_value_range("u", [], lo=0, hi=1).passed
    assert not dq.check_row_count("u", [], min_rows=1).passed


def test_severity_split_only_errors_trigger_degradation():
    """借 dbt 的 warn/error 分級:不是所有品質問題都該擋下整封信。
    warn 累積成趨勢,error 才走既有降級路徑。"""
    results = [
        dq.check_row_count("a", [], min_rows=1, severity=dq.ERROR),
        dq.check_value_range("b", [99.0], lo=0, hi=1, severity=dq.WARN),
        dq.check_row_count("c", _rows(5), min_rows=1, severity=dq.ERROR),
    ]
    s = dq.summarize(results)
    assert s["checked"] == 3 and s["failed"] == 2
    assert len(s["errors"]) == 1 and len(s["warnings"]) == 1
    labels = dq.degraded_labels(s)
    assert labels == ["dq:a:row_count"], f"warn 級不該觸發降級:{labels}"


def test_main_uses_distinct_key_from_existing_data_quality():
    """**自測接線時抓到的碰撞**:`DATA_QUALITY` 已被既有的 build_data_quality()
    佔用(給 LLM 看哪些來源失敗的 list),而且會在稍後被覆蓋
    → 若共用同一個 key,本檢查的結果會進不了 prompt 也進不了 manifest。"""
    from pathlib import Path
    src = Path(mr.__file__).read_text(encoding="utf-8")
    assert 'quotes["SOURCE_DATA_CHECKS"]' in src
    # 既有的那條仍在,且仍是最後寫入 DATA_QUALITY 的那個
    assert 'quotes["DATA_QUALITY"] = build_data_quality(' in src
    assert src.index('quotes["SOURCE_DATA_CHECKS"]') < src.index(
        'quotes["DATA_QUALITY"] = build_data_quality('), \
        "本檢查若寫在 build_data_quality 之後,反而會蓋掉既有功能"


def test_check_result_serialises_for_manifest():
    r = dq.check_row_count("u", _rows(3), min_rows=1)
    d = r.as_dict()
    assert set(d) == {"source", "check", "severity", "passed", "detail", "observed"}
    assert d["observed"] == 3


def test_failed_checks_reach_the_email_and_the_prompt():
    """r1(Codex,P1)**確認**:品質閘原本**只是觀測**——記了降級標籤,但被污染的
    tw0050 照樣流向 MOPS 選股、候選新聞、關注度排名、Top5,這個閘要防的污染
    仍然完整抵達輸出與 state。

    不採用「換 last-known-good / 整段省略」:丟掉 tw0050 會連帶殺掉 Top5 與
    關注度排名,違反「晨報不可斷」這條更高階的不變式。改為讓錯誤進到既有的
    資料品質區——它同時渲染進信件、也進 LLM prompt,污染因此對人與模型都可見。
    """
    from tests.test_data_validation import _empty_quotes
    summary = dq.summarize([
        dq.check_row_count("tw_universe", _rows(3), min_rows=30),
        dq.check_value_range("tw_universe", [99.0] * 10, lo=-11, hi=11,
                             severity=dq.WARN),
    ])
    q = _empty_quotes(SOURCE_DATA_CHECKS=summary)
    rows = mr.build_data_quality(q, {}, {}, [], [])
    names = [r["name"] for r in rows]
    assert any("資料品質:tw_universe" in n for n in names), \
        f"品質檢查失敗沒進資料品質區:{names}"
    err = [r for r in rows if r["status"] == "error"]
    assert err and "只有 3 筆" in err[0]["detail"]
    # warn 級也要出現,但不得升級成 error
    assert any(r["status"] == "fallback" and "tw_universe" in r["name"]
               for r in rows)


def test_data_checks_survive_into_the_persisted_manifest(tmp_path, monkeypatch):
    """r1(Codex,P2)**確認**:_write_run_manifest 是**重建白名單 dict**,
    沒列到的鍵一律丟掉 → warn 級品質問題只存在於當次 stderr,無法累積成趨勢。
    (與三審 P1-4 的 stance_dual 完全同一個坑。)"""
    import datetime as dt
    import json
    f = tmp_path / "run_manifest.json"
    monkeypatch.setattr(mr, "RUN_MANIFEST_FILE", f)
    summary = dq.summarize([dq.check_value_range("u", [99.0] * 10, lo=0, hi=1,
                                                 severity=dq.WARN)])
    mr._RUN_MANIFEST["data_checks"] = summary
    mr._write_run_manifest(dt.datetime(2026, 7, 26, 6, 0))
    saved = json.loads(f.read_text(encoding="utf-8"))
    assert "data_checks" in saved, "manifest 白名單漏了 data_checks"
    assert saved["data_checks"]["warnings"], "warn 級沒被保存下來"


def test_untrusted_universe_is_emptied_at_the_shared_boundary():
    """r2(Codex,P1)**接受**:「讓污染可見」不等於「阻止它傳播」。
    決定性理由是**自我毒化迴圈**:髒股票池寫進 model_history 的 stocks 快照,
    而本閘的自動門檻正是拿 model_history 的歷史中位數推出來的 → 髒日拉低中位數、
    削弱閘本身;且 state 會 commit 回 repo。state 污染不可逆。

    r4(Codex,P1)**再確認我只擋了四條路徑中的一條**:render_html 會從
    TW_UNIVERSE_SNAPSHOT **重新**呼叫 _rank_attention_candidates(信件 Top5
    卡片照常出現)、_build_prompt 照常產生 Top15/Top5、
    pending_state_entry["breakout_candidates"] 仍把排名寫進跨日 state。
    **而我上一版的測試是字串比對**,沒有渲染信件、沒有建 prompt、沒有檢查
    history state,所以那三條全部漏掉——正是「測試驗的是我蓋的東西」再犯一次。

    正解是在**共用邊界**把 tw0050 清空:所有下游一次到位,不必去數還有幾個
    消費點(數漏就是上一輪的失敗原因)。這條測試因此驗**行為**而非原始碼文字。
    """
    from tests.test_data_validation import _empty_quotes
    # 要能排名就得有 ranking_score(_rank_attention_candidates 的門檻);
    # 自測時第一版用了沒有分數的列,對照組因此恆為空 —— 那會讓這條測試恆真。
    dirty = [dict(r, ranking_score=5.0 - i, code=f"233{i}")
             for i, r in enumerate(_rows(3))]

    # 排名類函式吃到空清單時必須回空,而不是回「3 檔裡的前 5 名」
    assert mr._rank_attention_candidates([]) == []
    assert mr._breakout_candidates_for_state([]) in ([], {}, None)
    assert mr._snapshot_for_model([]) in ([], {}, None)

    # 渲染層:空 universe 不得產生 Top5 卡片
    q = _empty_quotes(TW_UNIVERSE_SNAPSHOT=[])
    assert mr._rank_attention_candidates(
        q.get("TW_UNIVERSE_SNAPSHOT") or []) == []

    # 而未清空時,同樣的髒資料**確實**會產生排名(證明這條測試分得出差別,
    # 不是恆真)
    assert mr._rank_attention_candidates(dirty),         "髒資料本來就會產生排名 —— 所以必須在邊界清空"


def test_gate_empties_universe_in_source_not_just_flags_it():
    """閘必須真的把 tw0050 清掉,而不是只設旗標讓每個消費點自己記得檢查。"""
    from pathlib import Path as _P
    src = _P(mr.__file__).read_text(encoding="utf-8")
    i = src.index('quotes["UNIVERSE_UNTRUSTED"]')
    window = src[i:i + 1200]
    assert "tw0050 = []" in window,         "旗標設了但沒在共用邊界清空 —— 下游每多一個消費點就多一個漏洞"


def test_gate_flag_only_trips_on_universe_errors_not_warnings():
    """warn 級不得擋 state——分級的意義就在這裡,否則單一離群值就會停掉學習。"""
    warn_only = dq.summarize([
        dq.check_value_range("tw_universe", [99.0] * 10, lo=-11, hi=11,
                             severity=dq.WARN)])
    assert not any(e.get("source") == "tw_universe"
                   for e in warn_only.get("errors", []))
    err = dq.summarize([dq.check_row_count("tw_universe", _rows(3), min_rows=30)])
    assert any(e.get("source") == "tw_universe" for e in err.get("errors", []))


def test_universe_derived_values_are_cleared_too():
    """r6(Codex,P1):**清空原始清單還不夠**——由 universe 算出的衍生值在品質
    檢查**之前**就已寫進 quotes。FOREIGN_TOP10_TOTAL 清空 tw0050 不會動到它,
    污染值仍會進晨報、進 Python 立場計分(13941),並寫入跨日 state(20135)。

    而我上一版的測試用 3 筆資料,`_foreign_top10_total()` 對 3 筆本來就回 None
    ——**對照組是假的**,完全驗不到這條(要 10-29 筆才露餡)。
    """
    from pathlib import Path as _P
    # 先證明對照組是真的:足夠多的列時衍生值不是 None
    rows = [dict(r, code=f"2{330 + i}", foreign_net=1e8, market_cap=1e12)
            for i, r in enumerate(_rows(20))]
    assert mr._foreign_top10_total(rows) is not None,         "對照組無效 —— 這批資料本來就算不出衍生值,測不到清除行為"

    src = _P(mr.__file__).read_text(encoding="utf-8")
    i = src.index('quotes["UNIVERSE_UNTRUSTED"]')
    window = src[i:i + 1600]
    assert "FOREIGN_TOP10_TOTAL" in window,         "衍生值未在 error 分支清除 —— 污染仍會進立場計分與 state"


def test_all_universe_derived_state_is_gated_at_persistence():
    """r8(Codex,P1):TDCC 快照同樣衍生自 universe,而我上一輪是**逐個列舉
    衍生值**去清 —— 這輪就漏了它。後果:部分 universe 的快照被存成「完整比較
    基準」,calc_tdcc_wow_delta 之後拿它比對時,不在該快照裡的代號**整週失去
    籌碼週變化**,靜默劣化關注度排名。

    逐個列舉行不通(已是第二次漏),改在**單一持久化邊界**擋。
    """
    from pathlib import Path as _P
    src = _P(mr.__file__).read_text(encoding="utf-8")
    i = src.index('"tdcc_snapshot":')
    window = src[i:i + 300]
    assert "UNIVERSE_UNTRUSTED" in window,         "TDCC 快照未受品質閘保護 —— 部分 universe 會被存成完整比較基準"
    # model_history 的 stocks 快照與 breakout 同樣走 tw0050,已由邊界清空覆蓋
    j = src.index('quotes["UNIVERSE_UNTRUSTED"]')
    assert "tw0050 = []" in src[j:j + 1600]


def test_capability_health_separates_inactive_from_fatal_and_warn():
    """第七輪 P1-8:2026-07-30 的 manifest 同時是
    `degraded_steps: []` 與「taifex_top10_net 10%、txo_pc_oi_ratio 3%」——
    品質閘**成功抓到問題**,而頂層健康語意仍顯示沒有降級。

    `degraded_labels()` 只收 error 級是刻意的(warn 不該擋信),但「不擋信」
    不等於「可以不呈現」。error/warn 兩級表達不出「長期空轉」這第三種狀態。
    """
    import data_quality as dq
    summary = dq.summarize([
        dq.check_fill_rate("model_history",
                           [{"taifex_top10_net": None}] * 20,
                           field="taifex_top10_net", min_ratio=0.5),
        dq.check_row_count("tw_universe", [1] * 100, min_rows=30),
        dq.check_value_range("tw_universe", [999.0], lo=-11.0, hi=11.0),
    ])
    health = dq.capability_health(summary, extra_inactive=("llm_event_extractor",))
    assert "taifex_top10_net" in health["inactive_capabilities"]
    assert "llm_event_extractor" in health["inactive_capabilities"]
    # 值域是 warn 但不是「能力失效」,不得混進 inactive
    assert any("value_range" in w for w in health["warnings"])
    assert not any("value_range" in c for c in health["inactive_capabilities"])
    assert health["fatal"] == []


def test_inactive_capabilities_reach_the_email_quality_block():
    """接線檢查:只寫進 manifest 的話讀信的人與 LLM 都看不到 ——
    而這一區塊的存在理由正是「不讓抓取失敗被誤讀成市場沒有訊號」。"""
    import morning_report as mr
    mr._RUN_MANIFEST["capability_health"] = {
        "fatal": [], "warnings": [],
        "inactive_capabilities": ["taifex_top10_net", "llm_event_extractor"]}
    try:
        dq = mr.build_data_quality({}, {}, {}, [], [])
    finally:
        mr._RUN_MANIFEST.pop("capability_health", None)
    hit = [d for d in dq if d.get("name") == "能力狀態"]
    assert hit, "失效能力沒有進到信件的資料品質區塊"
    assert "taifex_top10_net" in hit[0]["detail"]


def test_fill_rate_window_starts_at_the_fields_first_appearance():
    """批#79:**分母不能包含欄位還不存在的日子。**

    2026-07-30 的生產 manifest 報 `taifex_top10_net` 填充率 10% 並附上
    「功能可能在生產環境從未真正產出」。去合併視圖(218 筆)實測後真相相反:

    ```
    taifex_top10_net  全期 3/218 | 首見 2026-07-24 起 3/4 | 近30筆 3/30
    txo_pc_oi_ratio   全期 2/218 | 首見 2026-07-24 起 2/4 | 近30筆 2/30
    ```

    四個籌碼欄位首見都是功能落地那天,之後 4 個交易日命中 3/4 與 2/4 ——
    它產出得很正常,10% 裡有 26 筆早於功能存在。
    """
    import data_quality as dq

    # 26 天沒有這個欄位,之後 4 天有 3 天有值(重現生產現場)
    rows = ([{"session_date": f"2026-06-{d:02d}"} for d in range(1, 27)]
            + [{"session_date": "2026-07-24", "taifex_top10_net": -6212},
               {"session_date": "2026-07-27", "taifex_top10_net": -5100},
               {"session_date": "2026-07-28", "taifex_top10_net": -4880},
               {"session_date": "2026-07-29"}])
    res = dq.check_fill_rate("model_history", rows,
                             field="taifex_top10_net", min_ratio=0.5)
    assert res.passed, "剛上線且正常產出的欄位不得被報成失效"
    assert res.observed == 0.75, f"分母應從首見起算(3/4),實得 {res.observed}"
    assert "觀察中" in res.detail and "尚不足以判定" in res.detail

    # 舊行為的對照:固定尾端視窗會算成 3/30 = 10% 並判失敗。
    # 這是這批要修掉的誤報,釘住它確保不會回頭。
    naive = sum(1 for r in rows if r.get("taifex_top10_net") is not None) / len(rows)
    assert naive == 0.1 and naive < 0.5, "對照組數字對不上,測試前提已漂移"


def test_fill_rate_still_fails_a_field_that_produced_then_died():
    """**策展規則涵蓋不到的那一類。**

    呼叫端原本的迴避方式是人工維護「只列已上線一段時間的欄位」,那條規則
    (即使有人執行)也抓不到「上線很久、產出過、後來死掉」——而那正是最需要
    被抓到的狀態,因為它沒有任何其他徵兆。
    """
    import data_quality as dq

    rows = ([{"session_date": f"2026-05-{d:02d}", "chip": 1} for d in range(1, 6)]
            + [{"session_date": f"2026-06-{d:02d}"} for d in range(1, 26)])
    res = dq.check_fill_rate("model_history", rows, field="chip", min_ratio=0.5)
    assert not res.passed
    assert res.observed == 0.167, f"應為 5/30,實得 {res.observed}"
    assert "衰退" in res.detail
    assert "從未真正產出" not in res.detail, "產出過的欄位不得被說成從未產出"


def test_fill_rate_separates_never_produced_from_not_yet_judgeable():
    """三種狀態必須可分辨:從未產出 / 觀察中 / 產出後衰退。

    收斂成同一個數字正是 2026-07-30 那次誤判的成因 —— 檢查存在的理由就是
    區分「剛上線、正常」與「上線很久、已死」,而固定視窗讓兩者長得一模一樣。
    """
    import data_quality as dq

    never = dq.check_fill_rate(
        "model_history", [{"session_date": f"2026-07-{d:02d}"} for d in range(1, 21)],
        field="ghost", min_ratio=0.5)
    assert not never.passed and never.observed == 0.0
    assert "從未真正產出" in never.detail

    # 邊界:樣本剛好達到門檻就要開始判定,不得永遠停在「觀察中」
    n = dq.FILL_RATE_MIN_SAMPLES
    rows = ([{"session_date": f"2026-06-{d:02d}"} for d in range(1, 6)]
            + [{"session_date": f"2026-07-{d:02d}", "chip": None} for d in range(1, n + 1)])
    rows[5]["chip"] = 1                      # 首見在第 6 筆,之後剛好 n 筆
    res = dq.check_fill_rate("model_history", rows, field="chip", min_ratio=0.5)
    assert not res.passed, f"樣本達 {n} 筆就該開始判定,不得停在觀察中"
    assert "觀察中" not in res.detail


def test_fill_rate_is_not_reset_by_a_stray_hit_late_in_the_window():
    """r1(Codex,P2):**偶發產出不得重置樣本門檻。**

    第一版把「視窗內最早的非空值」當成上線日。一個上線很久、最近 30 筆只在
    第 22 筆冒出一次值的成熟功能,視窗只剩 9 筆 → 未達 `min_samples` →
    回報「觀察中」且 `passed=True` → 實際 1/30 的死亡功能既不會進 warning,
    也不會進 `inactive_capabilities`。**那正是這個檢查存在的理由。**

    我原本的「產出後死亡」測試把命中放在視窗開頭,分母仍是完整 30 筆,
    剛好避開這條路徑 —— 測試涵蓋了結論,沒涵蓋機制。
    """
    import data_quality as dq

    history = ([{"session_date": f"2026-04-{d:02d}", "chip": 1} for d in range(1, 21)]
               + [{"session_date": f"2026-06-{d:02d}"} for d in range(1, 23)]
               + [{"session_date": "2026-06-23", "chip": 7}]
               + [{"session_date": f"2026-07-{d:02d}"} for d in range(1, 8)])
    res = dq.check_fill_rate("model_history", history,
                             field="chip", min_ratio=0.5, window=30)
    assert not res.passed, "成熟欄位不得因視窗後段偶發一次產出而變成「觀察中」"
    assert "觀察中" not in res.detail
    assert res.observed == round(1 / 30, 3), \
        f"分母應保留完整視窗 30 筆,實得 {res.observed}"
    assert "衰退" in res.detail

    # 同一份資料若沒有前史(欄位真的剛上線),才允許「觀察中」
    fresh = history[20:]
    res2 = dq.check_fill_rate("model_history", fresh,
                              field="chip", min_ratio=0.5, window=30)
    assert res2.passed and "觀察中" in res2.detail


def test_fill_rate_flags_a_mature_field_that_went_completely_silent():
    """成熟欄位在整個視窗內**一次都沒有值** —— 這是最嚴重的情形,
    不得因為「視窗內找不到首見」而走進「從未產出」的分支(措辭會是錯的),
    也絕不能通過。"""
    import data_quality as dq

    history = ([{"session_date": f"2026-04-{d:02d}", "chip": 1} for d in range(1, 21)]
               + [{"session_date": f"2026-06-{d:02d}"} for d in range(1, 31)])
    res = dq.check_fill_rate("model_history", history,
                             field="chip", min_ratio=0.5, window=30)
    assert not res.passed and res.observed == 0.0
    assert "從未真正產出" not in res.detail, "產出過的欄位不得被說成從未產出"
    assert "衰退" in res.detail
