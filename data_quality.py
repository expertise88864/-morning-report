# -*- coding: utf-8 -*-
"""資料品質閘:擋「來源沒掛,但資料是壞的」。

**這個模組要補的缺口**

現有韌性做的是「**來源掛掉**」——per-host 熔斷、來源分級、30 天健康史、
四條整封信失敗路徑的兜底,那一塊做得很完整。缺的是另一類:
**HTTP 回 200、熔斷不觸發、來源健康度滿分,但內容是壞的。**

具體例子:某天三大法人只抓到 3 檔而不是往常的 100 檔。所有既有防線都不會響,
但預測、計分、Top5 排名全部已經被污染,而且**你不會知道**——信照樣寄出來,
數字只是悄悄變怪。這正是這個專案反覆出現的「靜默失效」。

**設計取捨**

- **不引 pandera**:需要的檢查(最少筆數、必要欄位、值域)很簡單,
  自寫百餘行比替每日生產路徑再加一個依賴划算(trafilatura 那次已經多帶了
  11 個傳遞依賴)。概念照抄,實作自己來。
- **warn / error 兩級**(借 dbt tests 的 severity):不是所有品質問題都該擋下整封信。
  warn 記錄下來累積成趨勢,error 才走既有的降級路徑。
- **門檻盡量由歷史自動推出**,而不是在程式碼裡寫魔術數字。呼叫端可提供歷史統計,
  沒有歷史時才退回保守的硬門檻。
"""
from __future__ import annotations

WARN = "warn"
ERROR = "error"


class CheckResult:
    """單一檢查的結果。刻意做成物件而非 tuple:呼叫端要能讀懂每個欄位。"""

    __slots__ = ("source", "check", "severity", "passed", "detail", "observed")

    def __init__(self, source: str, check: str, severity: str,
                 passed: bool, detail: str = "", observed=None):
        self.source = source
        self.check = check
        self.severity = severity
        self.passed = passed
        self.detail = detail
        self.observed = observed

    def as_dict(self) -> dict:
        return {"source": self.source, "check": self.check,
                "severity": self.severity, "passed": self.passed,
                "detail": self.detail, "observed": self.observed}

    def __repr__(self) -> str:
        flag = "OK" if self.passed else self.severity.upper()
        return f"<{flag} {self.source}/{self.check}: {self.detail}>"


def check_row_count(source: str, rows, *, min_rows: int,
                    history: list | None = None,
                    severity: str = ERROR) -> CheckResult:
    """筆數檢查。有歷史時以「歷史中位數的一半」為門檻,否則用 min_rows。

    用中位數而非平均:單日的異常值(例如某天真的只抓到 3 筆)會把平均拉低,
    讓門檻自己跟著壞掉——那正是這個檢查要防的情況。
    """
    n = len(rows or [])
    threshold = min_rows
    basis = f"硬門檻 {min_rows}"
    valid_hist = [int(h) for h in (history or []) if isinstance(h, (int, float)) and h > 0]
    if len(valid_hist) >= 5:
        median = sorted(valid_hist)[len(valid_hist) // 2]
        auto = max(1, median // 2)
        if auto > threshold:
            threshold, basis = auto, f"歷史中位數 {median} 的一半"
    ok = n >= threshold
    return CheckResult(
        source, "row_count", severity, ok, observed=n,
        detail=(f"{n} 筆" if ok else
                f"只有 {n} 筆,低於門檻 {threshold}({basis})——"
                "來源可能回了 200 但內容不完整"))


def check_required_fields(source: str, rows, *, fields,
                          max_missing_ratio: float = 0.1,
                          severity: str = ERROR) -> CheckResult:
    """必要欄位的缺值比率。

    刻意用**比率**而非「任一列缺就失敗」:真實資料本來就會有零星缺值
    (停牌股沒有開盤價),一有缺就擋會讓晨報天天降級。
    但缺值比率飆高就是上游 schema 變了。
    """
    rows = list(rows or [])
    if not rows:
        return CheckResult(source, "required_fields", severity, False,
                           "沒有任何資料列", 0)
    worst_field, worst_ratio = None, 0.0
    for f in fields:
        missing = sum(1 for r in rows
                      if not isinstance(r, dict) or r.get(f) in (None, ""))
        ratio = missing / len(rows)
        if ratio > worst_ratio:
            worst_field, worst_ratio = f, ratio
    ok = worst_ratio <= max_missing_ratio
    return CheckResult(
        source, "required_fields", severity, ok, observed=round(worst_ratio, 3),
        detail=("必要欄位齊全" if ok else
                f"欄位 `{worst_field}` 有 {worst_ratio:.0%} 的列缺值"
                f"(上限 {max_missing_ratio:.0%})——上游 schema 可能變了"))


def check_value_range(source: str, values, *, lo=None, hi=None,
                      max_outlier_ratio: float = 0.05,
                      severity: str = WARN) -> CheckResult:
    """值域檢查。超出範圍的比率過高才失敗(單一離群值不算問題)。"""
    nums = [float(v) for v in (values or [])
            if isinstance(v, (int, float))]
    if not nums:
        return CheckResult(source, "value_range", severity, False,
                           "沒有可檢查的數值", 0)
    bad = sum(1 for v in nums
              if (lo is not None and v < lo) or (hi is not None and v > hi))
    ratio = bad / len(nums)
    ok = ratio <= max_outlier_ratio
    return CheckResult(
        source, "value_range", severity, ok, observed=round(ratio, 3),
        detail=("值域正常" if ok else
                f"{ratio:.0%} 的值落在 [{lo}, {hi}] 之外(上限 "
                f"{max_outlier_ratio:.0%})"))


def summarize(results) -> dict:
    """彙整成可寫進 run manifest 的結構,並分出 warn / error 兩級。

    回傳 dict(errors=[...], warnings=[...], all=[...])。
    呼叫端據此決定:error 走既有降級路徑,warn 只記錄(累積成趨勢)。
    """
    rs = [r for r in (results or []) if isinstance(r, CheckResult)]
    failed = [r for r in rs if not r.passed]
    return {
        "all": [r.as_dict() for r in rs],
        "errors": [r.as_dict() for r in failed if r.severity == ERROR],
        "warnings": [r.as_dict() for r in failed if r.severity == WARN],
        "checked": len(rs),
        "failed": len(failed),
    }


def degraded_labels(summary: dict) -> list[str]:
    """error 級失敗要記進 _DEGRADED_STEPS 的標籤。

    **只有 error 級**——warn 級進 run manifest 累積趨勢,不觸發降級。
    分級的意義就在這裡:不是所有品質問題都該擋下整封信。
    """
    return [f"dq:{e['source']}:{e['check']}" for e in (summary or {}).get("errors", [])]


def check_fill_rate(source: str, rows, *, field: str, min_ratio: float,
                    severity: str = WARN) -> CheckResult:
    """某個欄位在最近 N 筆紀錄裡的**填充率**。

    批#69。前面幾批連續量測到同一種失敗:功能寫好、測試全綠、外審通過,
    但在生產環境**從來沒有產出過任何東西**,而且完全無聲——
      - LLM 事件抽取器:1160 則歷史事件裡沒有一則是 C 級
      - 台指期籌碼:`taifex_top10_net` 在 143 筆歷史中 0/143
        (`_chip_fields_for_session` 的 fail-closed 是對的,但沒人發現它一直是關的)

    這一類問題共同的形狀是「**應該被填的欄位長期是 None**」,而既有的
    row_count / required_fields / value_range 都抓不到:紀錄有、筆數夠、
    欄位在 schema 裡,只是永遠沒有值。

    刻意用**比率而非「今天有沒有」**:單日缺值本來就正常(來源延遲、假日),
    要抓的是「長期都沒有」。
    """
    rows = [r for r in (rows or []) if isinstance(r, dict)]
    if not rows:
        return CheckResult(source, f"fill_rate:{field}", severity, False,
                           "沒有任何紀錄可檢查", 0)
    filled = sum(1 for r in rows if r.get(field) not in (None, "", [], {}))
    ratio = filled / len(rows)
    ok = ratio >= min_ratio
    return CheckResult(
        source, f"fill_rate:{field}", severity, ok, observed=round(ratio, 3),
        detail=(f"`{field}` 填充率 {ratio:.0%}({filled}/{len(rows)})" if ok else
                f"`{field}` 最近 {len(rows)} 筆只填了 {filled} 筆"
                f"({ratio:.0%},低於 {min_ratio:.0%})"
                "——功能可能在生產環境從未真正產出"))


def capability_health(summary: dict, extra_inactive=()) -> dict:
    """把品質檢查結果整理成**能力健康狀態**,分成三層。

    批#73(第七輪 P1-8)。2026-07-30 的 manifest 同時出現這兩件事:
    ```
    degraded_steps: []
    taifex_top10_net 填充率 10%、txo_pc_oi_ratio 填充率 3%
    ```
    也就是說品質閘**成功抓到問題**,而頂層健康語意仍顯示「沒有降級」——
    讀者(和我)看到 `degraded_steps: []` 會以為一切正常,實際上有兩個功能
    幾乎沒有產出。`degraded_labels()` 只收 error 級是刻意的(warn 不該擋信),
    但「不擋信」不等於「可以不呈現」。

    三層的語意:
      - `fatal`:error 級失敗。會進 `_DEGRADED_STEPS`,信裡明說。
      - `inactive`:填充率低於門檻的**能力**。它不是「今天壞了」,而是
        「這個功能實質上沒在運作」——最該被獨立列出來的一類,因為既有的
        error/warn 兩級都表達不出「長期空轉」。
      - `warnings`:其餘 warn 級。
    `extra_inactive` 供呼叫端補上非品質檢查來源的失效能力(例如 LLM 抽取器
    的 outcome 是 error)。
    """
    errors = list((summary or {}).get("errors") or [])
    warnings = list((summary or {}).get("warnings") or [])
    inactive, rest = [], []
    for w in warnings:
        check = str(w.get("check") or "")
        if check.startswith("fill_rate:"):
            inactive.append(check.split(":", 1)[1])
        else:
            rest.append(f"{w.get('source')}:{check}")
    for name in extra_inactive or ():
        if name and name not in inactive:
            inactive.append(str(name))
    return {
        "fatal": [f"{e.get('source')}:{e.get('check')}" for e in errors],
        "inactive_capabilities": sorted(set(inactive)),
        "warnings": sorted(set(rest)),
    }
