"""共用 model_history loader(純 stdlib,無第三方依賴)。

正式流程(morning_report)與 backtest_data 離線腳本(月報/IC 回測)必須走同一
讀取邏輯:legacy 單檔(凍結唯讀)+ 按月分區 gzip 合併、同一交易日以分區為準。
GPT-5.6 三審 P1:先前三個回測腳本直接讀 legacy 單檔,分區上線後 legacy 停在
2026-07-15/143 筆,月報與 D1/IC 評估的樣本會凍結或倒退。
"""
import gzip
import hashlib
import json
import sys
from pathlib import Path

DEFAULT_LEGACY_FILE = Path("state/model_history.json")
DEFAULT_PARTITION_DIR = Path("state/model_history")
DEFAULT_SESSIONS = 520

MANIFEST_NAME = "manifest.json"
HISTORY_SCHEMA_VERSION = 3


class HistoryIntegrityError(RuntimeError):
    """strict 模式下的歷史資料完整性錯誤——離線消費端(月報/回測)必須讓它
    傳播中止,不得吞進報告文字後照常 commit(Codex r1 P2)。"""


def _canonical_payload(items: list) -> str:
    """分區的正規化 JSON(依日期排序、緊湊)——checksum 與寫入的共同基準;
    不比 gzip 位元組(OS header 跨平台不定)。"""
    ordered = sorted((it for it in items if isinstance(it, dict) and it.get("session_date")),
                     key=lambda i: str(i.get("session_date", "")))
    return json.dumps(ordered, ensure_ascii=False, separators=(",", ":"))


def _partition_entry(items: list) -> dict:
    """單一分區的 manifest 條目:筆數、日期範圍、payload sha256。"""
    payload = _canonical_payload(items)
    dates = sorted(str(it.get("session_date")) for it in items
                   if isinstance(it, dict) and it.get("session_date"))
    return {
        "row_count": len(dates),
        "min_date": dates[0] if dates else None,
        "max_date": dates[-1] if dates else None,
        "sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
    }


def write_partition_manifest(partition_dir: Path = DEFAULT_PARTITION_DIR) -> dict:
    """讀取所有分區、產生完整性 manifest(state/model_history/manifest.json)。
    寫入端(morning_report save 路徑)於分區寫完後呼叫一次;回寫出的 manifest。
    失敗回空(不影響晨報,只是本次無 manifest,下次補上)。"""
    manifest: dict = {"schema_version": HISTORY_SCHEMA_VERSION, "partitions": {}}
    if not partition_dir.exists():
        return manifest
    for path in sorted(partition_dir.glob("*.json.gz")):
        try:
            data = json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))
            if isinstance(data, list):
                manifest["partitions"][path.name] = _partition_entry(data)
        except Exception as e:
            print(f"[model_state] manifest 略過 {path.name}: {e}", file=sys.stderr)
    tmp = partition_dir / (MANIFEST_NAME + ".tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    tmp.replace(partition_dir / MANIFEST_NAME)
    return manifest


def verify_history_integrity(partition_dir: Path = DEFAULT_PARTITION_DIR,
                             strict: bool = False) -> dict:
    """比對分區實體與 manifest,回完整性報告 dict:
    {"ok": bool, "issues": [...], "has_manifest": bool}。
    issues 型別:checksum_mismatch / row_count_mismatch / missing_partition /
    extra_partition / month_mismatch / schema_mismatch / corrupt。
    strict=True 時有任一 issue 即 raise HistoryIntegrityError(離線 fail-closed);
    strict=False 只回報告(production 由呼叫端降級提示,晨報仍寄)。"""
    report: dict = {"ok": True, "issues": [], "has_manifest": False}

    def _flag(kind: str, detail: str) -> None:
        report["ok"] = False
        report["issues"].append({"kind": kind, "detail": detail})

    manifest = None
    mpath = partition_dir / MANIFEST_NAME
    if mpath.exists():
        report["has_manifest"] = True
        try:
            manifest = json.loads(mpath.read_text(encoding="utf-8"))
            if manifest.get("schema_version") != HISTORY_SCHEMA_VERSION:
                _flag("schema_mismatch",
                      f"manifest schema {manifest.get('schema_version')} "
                      f"!= {HISTORY_SCHEMA_VERSION}")
        except Exception as e:
            _flag("corrupt", f"manifest 解析失敗: {e}")
            manifest = None
    present = {}
    if partition_dir.exists():
        for path in sorted(partition_dir.glob("*.json.gz")):
            try:
                data = json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))
                if not isinstance(data, list):
                    _flag("corrupt", f"{path.name} 非 list")
                    continue
                present[path.name] = _partition_entry(data)
                # 分區月份與內容日期一致(YYYY-MM.json.gz 內容都應屬該月)
                month = path.name[:7]
                bad = [str(it.get("session_date")) for it in data
                       if isinstance(it, dict) and it.get("session_date")
                       and not str(it.get("session_date")).startswith(month)]
                if bad:
                    _flag("month_mismatch",
                          f"{path.name} 含非本月日期 {bad[:3]}")
            except Exception as e:
                _flag("corrupt", f"{path.name} 解壓/解析失敗: {e}")
    if manifest is not None:
        recorded = manifest.get("partitions") or {}
        for name, rec in recorded.items():
            if name not in present:
                _flag("missing_partition", f"manifest 有 {name} 但檔案不存在")
                continue
            cur = present[name]
            if cur["sha256"] != rec.get("sha256"):
                _flag("checksum_mismatch",
                      f"{name} sha256 不符(內容遭竄改/損壞但仍可解析)")
            if cur["row_count"] != rec.get("row_count"):
                _flag("row_count_mismatch",
                      f"{name} 筆數 {rec.get('row_count')}→{cur['row_count']}"
                      f"(疑遭截斷)")
        for name in present:
            if name not in recorded:
                _flag("extra_partition", f"{name} 未登錄於 manifest")
    if strict and not report["ok"]:
        raise HistoryIntegrityError(
            "分區完整性違規: " + "; ".join(
                f"{i['kind']}:{i['detail']}" for i in report["issues"][:6]))
    return report


def load_model_history(legacy_file: Path = DEFAULT_LEGACY_FILE,
                       partition_dir: Path = DEFAULT_PARTITION_DIR,
                       sessions: int = DEFAULT_SESSIONS,
                       strict: bool = False) -> list[dict]:
    """讀取 point-in-time 股票池歷史:legacy + 分區合併(分區優先),
    回傳依日期排序的最近 sessions 筆。

    strict=False(晨報 production):單一分區壞檔只略過該檔(晨報不可斷)。
    strict=True(離線回測/月報):任一檔損壞直接 raise fail-closed——
    靜默少一個月的樣本會讓 IC/回測指標無聲漂移,寧可中止(四審 P1-4)。"""
    merged: dict[str, dict] = {}
    if legacy_file.exists():
        try:
            data = json.loads(legacy_file.read_text(encoding="utf-8"))
            # 語法合法但結構錯(如整檔是 {})不是「空歷史」:strict 必炸——
            # 否則回測靜默少掉整段樣本(Codex r1 P1)
            if strict and not isinstance(data, list):
                raise HistoryIntegrityError(
                    f"legacy model_history 結構錯誤: {type(data).__name__} 非 list")
            for item in data if isinstance(data, list) else []:
                if isinstance(item, dict) and item.get("session_date"):
                    merged[item["session_date"]] = item
        except HistoryIntegrityError:
            raise
        except Exception as e:
            if strict:
                raise HistoryIntegrityError(f"legacy model_history 損壞: {e}") from e
            print(f"[model_state] legacy 載入失敗: {e}", file=sys.stderr)
    if partition_dir.exists():
        for path in sorted(partition_dir.glob("*.json.gz")):
            try:
                data = json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))
                if strict and not isinstance(data, list):
                    raise HistoryIntegrityError(
                        f"分區 {path.name} 結構錯誤: {type(data).__name__} 非 list")
                for item in data if isinstance(data, list) else []:
                    if isinstance(item, dict) and item.get("session_date"):
                        merged[item["session_date"]] = item   # 分區優先(較新)
            except HistoryIntegrityError:
                raise
            except Exception as e:
                if strict:
                    raise HistoryIntegrityError(f"分區 {path.name} 損壞: {e}") from e
                print(f"[model_state] 分區 {path.name} 載入失敗(略過): {e}",
                      file=sys.stderr)
    # strict(離線回測/月報):除「可解析」外,再驗 manifest 完整性
    # (checksum/筆數/月份一致——語法合法但被截斷或竄改的分區,前面讀得過但
    # 統計會失真,fail-closed)。manifest 缺席(轉換期首跑)不擋,只驗結構。
    if strict:
        verify_history_integrity(partition_dir, strict=True)
    history = sorted(merged.values(), key=lambda item: item.get("session_date", ""))
    return history[-sessions:]
