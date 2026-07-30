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
import os
from pathlib import Path

#: 批#74(第七輪 P1-10):state 根目錄由環境變數集中控制,與 morning_report
#: 共用同一個機制。這裡刻意**不 import morning_report**(那會製造循環相依,
#: 而本模組是被它匯入的下層),改為讀同一個環境變數 —— 單一事實來源仍是
#: `STATE_ROOT`,只是兩邊各自解析。
STATE_ROOT = Path(os.environ.get("STATE_ROOT") or "state")
DEFAULT_LEGACY_FILE = STATE_ROOT / "model_history.json"
DEFAULT_PARTITION_DIR = STATE_ROOT / "model_history"
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


def payload_sha256(items: list) -> str:
    """分區 canonical payload 的 sha256——供寫入端在合併前比對磁碟現有內容
    與舊 manifest(偵測跨執行間的外部竄改)。"""
    return hashlib.sha256(_canonical_payload(items).encode("utf-8")).hexdigest()


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


def _read_manifest_partitions(partition_dir: Path) -> dict:
    """讀既有 manifest 的 partitions 區(結構錯誤回空)。"""
    mpath = partition_dir / MANIFEST_NAME
    if not mpath.exists():
        return {}
    try:
        m = json.loads(mpath.read_text(encoding="utf-8"))
        parts = m.get("partitions") if isinstance(m, dict) else None
        return parts if isinstance(parts, dict) else {}
    except Exception:
        return {}


def write_partition_manifest(partition_dir: Path = DEFAULT_PARTITION_DIR,
                             rewritten: "set | None" = None) -> dict:
    """產生完整性 manifest(state/model_history/manifest.json)。

    Codex 批#25 r1 P1:**不得把已損壞的分區重新當成新基線**——只有本次
    「刻意且成功重寫」的分區(rewritten,以檔名指定)才計算新 sha256;
    未重寫的分區與既有 manifest 比對:相符→沿用舊條目;不符(=被外部
    截斷/竄改)→**保留舊條目**(讓後續 strict verify 持續抓到),不 baseline。
    rewritten=None(相容舊呼叫)則全部重算——僅供一次性初始化用。
    失敗回空(不影響晨報,只是本次無新 manifest)。"""
    manifest: dict = {"schema_version": HISTORY_SCHEMA_VERSION, "partitions": {}}
    if not partition_dir.exists():
        return manifest
    old = _read_manifest_partitions(partition_dir)
    baseline_all = rewritten is None
    rewritten = set(rewritten or [])
    damaged: list = []
    for path in sorted(partition_dir.glob("*.json.gz")):
        name = path.name
        try:
            data = json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))
            if not isinstance(data, list):
                raise ValueError("非 list")
            entry = _partition_entry(data)
        except Exception as e:
            # 損壞:保留舊條目(strict 仍會抓 checksum/筆數),不接受損壞版
            if name in old:
                manifest["partitions"][name] = old[name]
            damaged.append(f"{name}({e})")
            continue
        if baseline_all or name in rewritten:
            manifest["partitions"][name] = entry          # 刻意重寫 → 新基線
        elif name in old:
            if entry.get("sha256") == old[name].get("sha256"):
                manifest["partitions"][name] = old[name]   # 未變 → 沿用
            else:
                manifest["partitions"][name] = old[name]   # 未重寫卻變了=損壞
                damaged.append(f"{name}(未重寫卻與 manifest 不符)")
        else:
            manifest["partitions"][name] = entry           # 全新分區
    if damaged:
        print(f"[model_state] ⚠ manifest 偵測到未重寫卻異動/損壞的分區,"
              f"保留舊 checksum 供 strict 稽核: {damaged[:4]}", file=sys.stderr)
    tmp = partition_dir / (MANIFEST_NAME + ".tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    tmp.replace(partition_dir / MANIFEST_NAME)
    return manifest


def verify_history_integrity(partition_dir: Path = DEFAULT_PARTITION_DIR,
                             strict: bool = False,
                             require_manifest: bool | None = None) -> dict:
    """比對分區實體與 manifest,回完整性報告 dict:
    {"ok": bool, "issues": [...], "has_manifest": bool}。
    issues 型別:checksum_mismatch / row_count_mismatch / missing_partition /
    extra_partition / month_mismatch / schema_mismatch / corrupt /
    missing_manifest。
    strict=True 時有任一 issue 即 raise HistoryIntegrityError(離線 fail-closed);
    strict=False 只回報告(production 由呼叫端降級提示,晨報仍寄)。

    require_manifest(批#68):**manifest 不存在時,下面所有 checksum/筆數比對
    整段跳過而 `ok` 仍是 True** —— 也就是「刪掉 manifest」等於關掉全部竄改
    偵測,而嚴格模式不會發現。這正好是完整性檢查最不該有的失敗模式。
    預設跟隨 strict:離線稽核(月報/回測)要求 manifest 必須在;production
    仍寬容(首次建檔、舊 repo 尚未產生 manifest 時晨報不可斷)。
    且只在**分區檔案存在**時才要求——全新 repo 沒東西可驗,不該因此失敗。"""
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
            # 結構防呆(Codex 批#25 r1 P2:合法 JSON 但結構錯——root/partitions
            # 非 dict、entry 非 dict——直接存取 .items()/.get() 會拋
            # AttributeError 被 production 吞掉、strict 月報也不當作完整性錯誤)
            if not isinstance(manifest, dict):
                _flag("corrupt", f"manifest root 非 dict: {type(manifest).__name__}")
                manifest = None
            elif not isinstance(manifest.get("partitions"), dict):
                _flag("corrupt", "manifest partitions 非 dict")
                manifest = None
            elif any(not isinstance(v, dict)
                     for v in manifest["partitions"].values()):
                _flag("corrupt", "manifest 有非 dict 的 partition 條目")
                manifest = None
            elif manifest.get("schema_version") != HISTORY_SCHEMA_VERSION:
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
                # 逐列結構驗證(Codex r3 P2:純量/空 dict/缺 session_date 的列
                # 會被 _partition_entry 靜默過濾,manifest-less 路徑就漏檢)
                bad_rows = [i for i, it in enumerate(data)
                            if not (isinstance(it, dict)
                                    and str(it.get("session_date") or "").strip())]
                if bad_rows:
                    _flag("corrupt",
                          f"{path.name} 含 {len(bad_rows)} 個結構錯誤列"
                          f"(非 dict 或缺 session_date),index {bad_rows[:3]}")
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
    # 批#68:manifest 缺席時,上面所有 checksum/筆數比對**整段跳過**而 ok 仍是
    # True —— 「刪掉 manifest」等於關掉全部竄改偵測,而嚴格模式不會發現。
    # 只有在分區檔案存在時才要求(全新 repo 沒東西可驗,不該因此失敗)。
    if require_manifest is None:
        require_manifest = strict
    if require_manifest and present and not report["has_manifest"]:
        _flag("missing_manifest",
              f"{len(present)} 個分區存在但無 {MANIFEST_NAME}"
              "——完整性比對整段未執行,不得視為通過")
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
