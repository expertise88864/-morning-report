"""共用 model_history loader(純 stdlib,無第三方依賴)。

正式流程(morning_report)與 backtest_data 離線腳本(月報/IC 回測)必須走同一
讀取邏輯:legacy 單檔(凍結唯讀)+ 按月分區 gzip 合併、同一交易日以分區為準。
GPT-5.6 三審 P1:先前三個回測腳本直接讀 legacy 單檔,分區上線後 legacy 停在
2026-07-15/143 筆,月報與 D1/IC 評估的樣本會凍結或倒退。
"""
import datetime as _dt
import gzip
import hashlib
import json
import re
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
    """讀既有 manifest 的 partitions 區。**壞掉不是「沒有」** ——
    raise `HistoryIntegrityError`;真的不存在才回 `{}`。

    r18 外審 P1:先前 missing / JSON 壞掉 / root 或 partitions 型別錯誤
    **全部折成同一個 `{}`**,而 `write_partition_manifest()` 把 `old == {}`
    讀成「我以前沒有記錄過這些分區」—— 於是磁碟上每一個分區都走「全新分區」
    那條路,拿**現在的內容**重算 sha256 寫成新基線。

    也就是說:manifest 壞掉的那一天,任何被竄改過的分區都會**被重新簽名**,
    完整性驗證隔天就變綠。那不是偵測到歷史被改,是替被改過的歷史背書。
    而 model_history 正是 walk-forward / IC / calibration 的信任錨點。

    `state_store` 對一般 state 早就分清楚 missing 與 corrupt(壞檔一律
    raise、不覆寫);這裡是同一條規則最後一個沒有套用的地方。
    """
    return _manifest_state(partition_dir)[1]


def _manifest_state(partition_dir: Path) -> tuple:
    """`(manifest 在不在, partitions 區)`。壞掉一律 raise。

    r18 第二輪(Codex deep P1):上一版只補了「解析/型別壞掉」那個洞 ——
    而 `{"partitions": {}}` 或**漏記某些分區**的 manifest 仍然回 `{}`,
    與「真的沒有 manifest」完全一樣。於是磁碟上那些沒被記到的分區照樣走
    「全新分區」那條路,拿現在的內容重算 sha256 —— 重新簽名的路徑還在。
    而 `verify_history_integrity()` 對同一種情形報的是 `extra_partition`
    (**已經是完整性違規**),兩邊對同一份 state 說不同的話。
    """
    mpath = partition_dir / MANIFEST_NAME
    if not mpath.exists():
        return False, {}                    # 真的沒有 —— 可以初始化
    try:
        raw = mpath.read_text(encoding="utf-8")
    except OSError as e:
        raise HistoryIntegrityError(f"{MANIFEST_NAME} 讀取失敗: {e}") from e
    try:
        m = json.loads(raw)
    except ValueError as e:
        raise HistoryIntegrityError(f"{MANIFEST_NAME} 解析失敗: {e}") from e
    if not isinstance(m, dict):
        raise HistoryIntegrityError(
            f"{MANIFEST_NAME} root 非 dict: {type(m).__name__}")
    parts = m.get("partitions")
    if not isinstance(parts, dict):
        raise HistoryIntegrityError(
            f"{MANIFEST_NAME} partitions 非 dict: {type(parts).__name__}")
    return True, parts


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
    # **舊 manifest 讀不動就不要寫新的**(r18 外審 P1):`old` 是判斷
    # 「這個分區是不是被外部改過」的唯一依據,拿不到它就分不出
    # 「全新分區」與「被竄改的舊分區」—— 而那兩者的處置正好相反。
    # 這裡讓例外往上傳:呼叫端(morning_report)會記 state 壞檔並跳過,
    # 壞掉的 manifest 原封留著給 strict 稽核。
    had_manifest, old = _manifest_state(partition_dir)
    # CR-01:glob 看不到的舊月份也必須保留,否則日常存檔會洗掉缺檔證據。
    manifest["partitions"].update(old)
    baseline_all = rewritten is None
    rewritten = set(rewritten or [])
    present_names = {path.name for path in partition_dir.glob("*.json.gz")}
    damaged: list = [f"{name}(已登錄分區缺檔)" for name in old if name not in present_names]
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
        elif not had_manifest:
            manifest["partitions"][name] = entry           # 首次建檔
        else:
            # **manifest 在,卻沒記到這個檔**:那是 `verify_history_integrity`
            # 眼中的 `extra_partition` —— 已經是完整性違規。本次沒有刻意重寫
            # 它,就不可以拿現在的內容替它簽名(那正是「替被改過的歷史背書」
            # 的另一條路)。留白 → verify 繼續 flag,人工修復。
            damaged.append(f"{name}(manifest 未登錄且本次未重寫)")
    if damaged:
        print(f"[model_state] ⚠ manifest 偵測到未重寫卻異動/損壞的分區,"
              f"保留舊 checksum 供 strict 稽核: {damaged[:4]}", file=sys.stderr)
    tmp = partition_dir / (MANIFEST_NAME + ".tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    tmp.replace(partition_dir / MANIFEST_NAME)
    return manifest


#: 分區每一列的**語意**必要欄位 —— 缺任何一個,那一列對消費端就沒有意義。
#: (`taiex_close` 允許 `None`:真實 state 242 列裡有 65 列是 None ——
#:  收盤價當天抓不到是既有事實,不是壞資料;但**欄位本身必須在**。)
PARTITION_REQUIRED_FIELDS = ("session_date", "taiex_close", "stocks",
                             "model_version")

#: 大盤收盤價的合理下界。與凍結 legacy `model_history.json` 的契約同一個值
#: —— 先前 live 分區的語意契約**比凍結的舊檔還鬆**,那是反過來的。
_TAIEX_CLOSE_FLOOR = 1000

#: 分區檔名的月份(`2026-09.json.gz` → `2026-09`)。
_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")


def partition_semantic_issues(name: str, rows) -> list:
    """一個分區的**語意**違規清單(空 list = 沒話說)。

    `verify_history_integrity()` 答的是「這份 state **自洽**嗎」——
    checksum、row_count、月份、manifest 對得上。它不答「它還**有意義**嗎」:
    `[]`、重複的 `session_date`、只有 `session_date` 的空殼、
    `taiex_close: "壞掉"` —— 全部自洽,全部沒有意義。

    r15 外審:這條規則先前只住在 `tests/test_state_schema_contract.py`,
    於是 **publish gate(pytest)比正式 strict consumer 嚴** ——
    而 `load_model_history(strict=True)` 的宣稱是「月報/回測必須 fail-closed,
    不得靜默少樣本或讓統計漂移」。規則搬到這裡,兩邊呼叫同一支。

    `name` 只用來取月份(`2026-09.json.gz` → `2026-09`);不像月份的檔名
    就不驗月份(這支函式不該對檔名慣例有意見)。
    """
    issues = []
    if not isinstance(rows, list):
        return [f"{name} 的 root 不是 list(是 {type(rows).__name__})"]
    if not rows:
        # 空分區:manifest 會誠實記下 row_count 0 而完全自洽 ——
        # 但那個月的資料就是不見了,消費端不會知道。
        return [f"{name} 是空分區(0 列)—— 自洽,但那個月的樣本消失了"]
    month = str(name).split(".", 1)[0]
    check_month = bool(_MONTH_RE.match(month))
    seen = set()
    for i, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            issues.append(f"{name} 第 {i} 列不是 dict(是 {type(row).__name__})")
            continue
        missing = [k for k in PARTITION_REQUIRED_FIELDS if k not in row]
        if missing:
            issues.append(f"{name} 第 {i} 列缺欄位:{missing}")
        day = str(row.get("session_date") or "")
        # **必須剛好是 `YYYY-MM-DD`**:`datetime.fromisoformat()` 連
        # `"2026-09-01T08:00:00"` 都收,而 loader 是
        # `merged[row["session_date"]] = row` —— 拿**原字串**當鍵。
        # 同一個交易日多一個時間部分就是另一個 session,重複檢查也看不出來。
        try:
            parsed = _dt.date.fromisoformat(day)
        except ValueError:
            parsed = None
        if parsed is None or parsed.isoformat() != day:
            issues.append(
                f"{name} 第 {i} 列的 session_date 不是 YYYY-MM-DD:{day!r}"
                " —— loader 用原字串當 merge key")
        elif check_month and day[:7] != month:
            issues.append(f"{name} 第 {i} 列的月份是 {day[:7]},不屬於 {month}")
        if day and day in seen:
            issues.append(
                f"{name} 有重複的 session_date {day} —— loader 是"
                " `merged[session_date] = row`,第二列直接蓋掉第一列:"
                "manifest 說幾列,消費端拿到的卻更少")
        seen.add(day)
        issues.extend(_row_value_issues(name, i, row))
    return issues


def _row_value_issues(name: str, i: int, row: dict) -> list:
    """一列的**值**契約 —— 「欄位在」不等於「值有意義」。

    r15 外審:先前只驗 `k not in row`,於是
    `{"taiex_close": "壞掉", "stocks": [], "model_version": null}`
    四個欄位都在、日期合法、月份合法、沒有重複 —— 完全通過。
    """
    out = []
    close = row.get("taiex_close")
    if close is not None:
        if isinstance(close, bool) or not isinstance(close, (int, float)):
            out.append(f"{name} 第 {i} 列的 taiex_close 不是數字:{close!r}")
        elif not (close > _TAIEX_CLOSE_FLOOR):
            out.append(f"{name} 第 {i} 列的 taiex_close 不合理:{close!r}")
    if "stocks" in row and not isinstance(row.get("stocks"), dict):
        # `stocks` 是 `{code: row}` 的映射;變成 list 的話,下游
        # `.get(code)` 會靜默拿不到任何一檔,而不是壞掉。
        out.append(f"{name} 第 {i} 列的 stocks 不是 dict"
                   f"(是 {type(row.get('stocks')).__name__})")
    if "model_version" in row:
        mv = row.get("model_version")
        if not isinstance(mv, str) or not mv.strip():
            out.append(f"{name} 第 {i} 列的 model_version 不是非空字串:{mv!r}")
    return out


def validate_partition_semantics(path) -> None:
    """讀一個分區檔並驗語意;有違規就 raise `HistoryIntegrityError`。

    給「手上只有路徑」的呼叫端(state 契約 gate)用;
    `verify_history_integrity()` 走 in-memory 那一支,不重讀檔案。
    """
    path = Path(path)
    try:
        rows = json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))
    except Exception as e:                  # noqa: BLE001
        raise HistoryIntegrityError(f"{path.name} 解壓/解析失敗: {e}") from e
    issues = partition_semantic_issues(path.name, rows)
    if issues:
        raise HistoryIntegrityError("; ".join(issues[:6]))


def verify_history_integrity(partition_dir: Path = DEFAULT_PARTITION_DIR,
                             strict: bool = False,
                             require_manifest: bool | None = None) -> dict:
    """比對分區實體與 manifest,回完整性報告 dict:
    {"ok": bool, "issues": [...], "has_manifest": bool}。
    issues 型別:checksum_mismatch / row_count_mismatch / missing_partition /
    extra_partition / month_mismatch / schema_mismatch / corrupt /
    missing_manifest / semantic_violation(自洽但沒有意義:空分區、重複
    session_date、空殼列、值型別壞掉)。
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
                # **一致性不等於有意義**(r15 外審):上面驗完
                # 「這份 state 自洽嗎」,這裡問「它還有意義嗎」。
                # 同一支函式也給 state 契約 gate 用 —— publish gate 與
                # strict consumer 不可以對同一份分區說不同的話。
                # (`month_mismatch` 那條刻意留著:它是既有的 issue kind,
                #  兩條規則同時 flag 同一個檔不會有害。)
                for _detail in partition_semantic_issues(path.name, data)[:6]:
                    _flag("semantic_violation", _detail)
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
            if not isinstance(data, list):
                # strict 必炸;非 strict(晨報)略過但**要說**(全案審查 ST-3):
                # `for item in []` 不會進 except,於是壞掉的 legacy 檔與空檔在
                # log 裡長得一樣,而 walk-forward 樣本數靜默少一截。
                if strict:
                    raise HistoryIntegrityError(
                        f"legacy model_history 結構錯誤: {type(data).__name__} 非 list")
                print(f"[model_state] legacy root 型別是 {type(data).__name__} 非 list,"
                      f"整檔略過(不是空歷史)", file=sys.stderr)
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
                if not isinstance(data, list):
                    # 同 legacy(全案審查 ST-3):非 strict 也要留一行,
                    # 否則「這個月沒樣本」與「這個月的檔壞了」分不開。
                    if strict:
                        raise HistoryIntegrityError(
                            f"分區 {path.name} 結構錯誤: {type(data).__name__} 非 list")
                    print(f"[model_state] 分區 {path.name} root 型別是 "
                          f"{type(data).__name__} 非 list,略過", file=sys.stderr)
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
