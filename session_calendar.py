"""交易日 / 預測目標日 / 突破追蹤 日期工具(A5-B4 由 morning_report 抽出)。
純日期/序列運算,只依 stdlib datetime;無網路/狀態,不依賴 morning_report 其它符號。
morning_report 以 re-export 保相容,既有測試零修改。
"""
import datetime as dt
from typing import Optional


def _session_distance(start_date: str, end_date: str, sessions: list[str]) -> Optional[int]:
    """用真實 TWSE 交易日計算距離；任一日期不在日曆中則回 None。"""
    ordered = sorted(set(sessions))
    try:
        return ordered.index(end_date) - ordered.index(start_date)
    except ValueError:
        return None


def _next_tw_weekday(day: dt.date) -> dt.date:
    """回傳 day 當日或下一個台股平日。休市日會在實際開盤對齊時再往後解析。"""
    while day.weekday() >= 5:
        day += dt.timedelta(days=1)
    return day


def _infer_target_session_date(date_str: str) -> str:
    """舊 state 沒有 target_session_date 時，依報告日期推導預測對應的台股開盤日。"""
    try:
        day = dt.datetime.strptime(date_str, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return str(date_str or "")
    return _next_tw_weekday(day).strftime("%Y-%m-%d")


def _target_session_date(entry: dict) -> str:
    """取得 state entry 的預測目標交易日，並兼容舊版 state。"""
    return (entry.get("target_session_date")
            or _infer_target_session_date(entry.get("date", "")))


def _normalize_history_entries(history: list[dict]) -> list[dict]:
    """
    將舊版 state 補上 target_session_date，並以目標交易日去重。

    週六晨報與週一晨報都預測週一開盤；保留較晚產生的週一版，避免同一個實際
    開盤被重複餵進 bias / MAE。台股國定假日造成的重複則在實際開盤解析時再去重。
    """
    by_target: dict[str, dict] = {}
    for raw in history or []:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        target = _target_session_date(item)
        if not target:
            continue
        item["target_session_date"] = target
        prev = by_target.get(target)
        item_sort = (item.get("generated_at") or item.get("date", ""), item.get("date", ""))
        prev_sort = ((prev or {}).get("generated_at") or (prev or {}).get("date", ""),
                     (prev or {}).get("date", ""))
        if prev is None or item_sort >= prev_sort:
            by_target[target] = item
    return sorted(by_target.values(), key=lambda h: (_target_session_date(h), h.get("date", "")))


def _actual_open_date_for(target_date: str,
                          opens_map: dict[str, float],
                          before_date: Optional[str] = None) -> Optional[str]:
    """找目標日當天或之後第一個已成熟的實際開盤日。"""
    for open_date in sorted(opens_map):
        if open_date >= target_date and (before_date is None or open_date < before_date):
            return open_date
    return None


def _resolved_prediction_history(history: list[dict],
                                 reference_opens: dict[str, float],
                                 before_date: Optional[str] = None) -> list[tuple[str, dict]]:
    """將 state 對齊到真實交易日，國定假日造成的重複只保留最後一筆預測。"""
    by_actual_date: dict[str, dict] = {}
    for entry in _normalize_history_entries(history):
        open_date = _actual_open_date_for(_target_session_date(entry), reference_opens, before_date)
        if open_date:
            by_actual_date[open_date] = entry
    return sorted(by_actual_date.items())


def _weekday_session_distance(start_date: str, end_date: str) -> int:
    """計算兩日期間的台股平日數；正式校準前的候選追蹤用近似值。"""
    start = dt.datetime.strptime(start_date, "%Y-%m-%d").date()
    end = dt.datetime.strptime(end_date, "%Y-%m-%d").date()
    count = 0
    day = start
    while day < end:
        day += dt.timedelta(days=1)
        if day.weekday() < 5:
            count += 1
    return count


def evaluate_breakout_forecasts(history: list[dict],
                                current_snapshot: list[dict],
                                target_session_date: str,
                                sessions: Optional[list[str]] = None) -> dict[int, dict]:
    """以目前快照回看 3 日 / 5 日候選，計算實際報酬、預測 MAE 與方向命中率。"""
    current_close = {
        item.get("code"): item.get("close")
        for item in current_snapshot or []
        if item.get("code") and item.get("close")
    }
    raw = {
        3: {"returns": [], "forecast_errors": [], "direction_hits": []},
        5: {"returns": [], "forecast_errors": [], "direction_hits": []},
    }
    for entry in _normalize_history_entries(history):
        candidates = entry.get("breakout_candidates") or []
        if not candidates:
            continue
        try:
            horizon = (
                _session_distance(_target_session_date(entry), target_session_date, sessions)
                if sessions else None
            )
            if horizon is None:
                horizon = _weekday_session_distance(
                    _target_session_date(entry), target_session_date)
        except ValueError:
            continue
        if horizon not in raw:
            continue
        for candidate in candidates:
            old_close = candidate.get("close")
            new_close = current_close.get(candidate.get("code"))
            if not old_close or not new_close:
                continue
            actual_return = (new_close / old_close - 1) * 100
            raw[horizon]["returns"].append(actual_return)
            forecast = (candidate.get("price_forecast") or {}).get(f"{horizon}d") or {}
            expected_price = forecast.get("expected_price")
            if expected_price:
                expected_return = (expected_price / old_close - 1) * 100
                raw[horizon]["forecast_errors"].append(actual_return - expected_return)
                raw[horizon]["direction_hits"].append(
                    (actual_return >= 0) == (expected_return >= 0))

    out: dict[int, dict] = {}
    for horizon, values in raw.items():
        returns = values["returns"]
        errors = values["forecast_errors"]
        hits = values["direction_hits"]
        out[horizon] = {
            "samples": len(returns),
            "avg_return_pct": round(sum(returns) / len(returns), 3) if returns else None,
            "win_rate_pct": round(sum(v > 0 for v in returns) / len(returns) * 100, 1) if returns else None,
            "forecast_samples": len(errors),
            "forecast_bias_pct": round(sum(errors) / len(errors), 3) if errors else None,
            "forecast_mae_pct": round(sum(abs(v) for v in errors) / len(errors), 3) if errors else None,
            "direction_hit_pct": round(sum(hits) / len(hits) * 100, 1) if hits else None,
        }
    return out


def build_breakout_tracking(history: list[dict],
                            current_snapshot: list[dict],
                            target_session_date: str,
                            sessions: Optional[list[str]] = None) -> str:
    """
    初步追蹤短線候選在晨報快照間的 3 日 / 5 日報酬。

    這不是完整 walk-forward 校準：國定假日先以平日近似，待樣本累積後再用
    官方交易日曆與歷史收盤做正式權重調整。
    """
    evaluation = evaluate_breakout_forecasts(
        history, current_snapshot, target_session_date, sessions=sessions)
    lines = []
    for horizon in (3, 5):
        stats = evaluation[horizon]
        if stats["samples"]:
            line = (
                f"{horizon} 日候選：n={stats['samples']}，平均 {stats['avg_return_pct']:+.2f}% ，"
                f"上漲率 {stats['win_rate_pct']:.0f}%")
            if stats["forecast_samples"]:
                line += (
                    f"，預測 MAE {stats['forecast_mae_pct']:.2f}% ，"
                    f"方向命中 {stats['direction_hit_pct']:.0f}%")
            lines.append(line)
    return "\n".join(lines) if lines else "（候選追蹤樣本累積中）"
