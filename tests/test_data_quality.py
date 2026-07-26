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
    r = dq.check_row_count("u", _rows(20), min_rows=5, history=hist)
    assert not r.passed, "被異常值拉低的門檻放行了壞資料"


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
