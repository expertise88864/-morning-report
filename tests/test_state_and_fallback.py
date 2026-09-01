"""
批#37:兩條「沉默失效」不變式的回歸測試。

共同性質:出事時**不會拋例外、不會讓既有測試變紅**,只會讓晨報悄悄變差
——正是 code review 認定最需要測試釘住的類型。

A. state 檔登錄不變式
   新增一個跨日累積的 state 檔卻忘了加進 `_state_push_paths()`,在本機完全正常
   (檔案就在 state/ 下),但 CI 每天是全新 runner:沒 commit 回 repo = 次日讀不到
   = 該功能永遠停在「第一天」。2026-07-09 已真實發生過一次(state 靜默遺失)。

B. 跨供應商備援的完整性檢查
   主供應商失敗 → Gemini 備援。備援回傳若被截斷,原本會被原樣送出,
   頂部 KPI/結論卡整排變「—」。
"""
import datetime as _dt
import json as _json
import re
from pathlib import Path

import pytest

import morning_report as mr
import run_quality as rq

_SRC = Path(mr.__file__).read_text(encoding="utf-8")

#: repo 的真實 `state/`。r2(Codex,P2):**守衛測試的目標端也必須是絕對路徑。**
#: r1 只把 `conftest.py` 的守衛「保護範圍」改成由 `__file__` 定位,卻留下這裡的
#: `Path("state")` 目標端 —— 從 repo 外執行時,寫入打在別的目錄上,守衛不會命中:
#: 目錄不存在就 `FileNotFoundError`,存在就**真的寫進去**而測試因「沒有拋
#: AssertionError」失敗。範圍與目標必須用同一個錨,否則兩邊會各自漂移。
#: (r1 的 repo 外驗證只跑了另外兩個檔,沒跑守衛測試自己 —— 驗證沒有覆蓋到
#:  被修的那類不變式,漏洞才留到 r2。)
_REPO_STATE = Path(__file__).resolve().parents[1] / "state"


# ---------------------------------------------------------------- A. state 登錄
#
# 刻意做**原始碼層**檢查而非讀執行期的常數值:conftest 的 autouse fixture 會把
# 多個 state 常數 monkeypatch 到 tmp 目錄(正確做法,免得測試寫進真實 state/),
# 執行期比對只會拿到兩邊都被改寫的 tmp 路徑,驗不到「有沒有登錄」這件事。
# 登錄本來就是原始碼事實,就在原始碼層驗。

# 唯讀輸入,不是「跑完後回流」的狀態:程式只 .exists() / .read_text(),
# 由外部工具或人工維護。故不該出現在 push 清單。
#: 晨報**只讀不寫**的 state。`GOOAYE_RADAR_FILE` 的寫入端與推送責任
#: 都在 `gooaye_radar.py` 的自有 workflow(兩個 workflow 競寫同一個檔
#: 才是要避免的事)。
_READ_ONLY_STATE = {"REVENUE_CONSENSUS_FILE", "TWSE_TOP100_ARCHIVE_FILE",
                    "GOOAYE_RADAR_FILE"}

# 批#74(第七輪 P1-10):state 路徑改為由 `STATE_ROOT` 衍生,宣告形式因此有兩種:
#   舊:`FOO_FILE = Path("state/foo.json")`
#   新:`FOO_FILE = STATE_ROOT / "foo.json"`
# 掃描器只認舊形式的話會**靜默回傳幾乎空的集合** —— 而下面兩條登錄檢查
# 依賴它,空集合會讓它們變成永遠通過的空斷言。
# (`test_state_const_scan_finds_the_known_ones` 存在的理由正是這個,
#  而它這次確實抓到了:改完只找到 2 個常數。)
_PATH_CONST_RE = re.compile(
    r"^([A-Z][A-Z0-9_]*)\s*=\s*(?:Path\((?:[^()]|\([^()]*\))*\)"
    r"|STATE_ROOT\s*/\s*\"[^\"]+\")",
    re.MULTILINE | re.DOTALL,
)


def _declared_state_consts() -> set[str]:
    """原始碼中預設值指向 state/ 的模組級 Path 常數名稱。"""
    return {
        m.group(1) for m in _PATH_CONST_RE.finditer(_SRC)
        if ("state/" in m.group(0) or "STATE_ROOT" in m.group(0))
        # `STATE_ROOT` 本身是**根目錄**而不是一份 state 檔,不需要登錄 push
        # (它底下的每一個檔各自登錄)。
        and m.group(1) != "STATE_ROOT"
    }


def _push_listed_consts() -> set[str]:
    """**只認真的會被執行的登錄行。**

    突變驗證抓到:把 `str(ANALYSIS_RECAP_FILE)` 那一行整行註解掉,
    這個掃描器照樣認得它 —— 於是「移出 push 清單」這個改動不會讓
    任何測試變紅,而次日讀不到的症狀本機測不出來。
    註解掉一個登錄與刪掉它,對 CI 上的次日行為是同一件事。
    """
    body = _SRC.split("def _state_push_paths(")[1].split("\ndef ")[0]
    code = "\n".join(ln for ln in body.splitlines()
                     if not ln.lstrip().startswith("#"))
    return set(re.findall(r"str\(([A-Z][A-Z0-9_]*)\)", code))


def test_state_const_scan_finds_the_known_ones():
    """守住掃描器本身:正則若因重構失配而回傳空集合,下面兩條會變成
    永遠通過的空斷言(沉默失效的測試,比沒測試更糟)。"""
    found = _declared_state_consts()
    assert len(found) >= 10, f"state 常數掃描疑似失配,只找到 {sorted(found)}"
    for expected in ("STATE_FILE", "CONFORMAL_STATE_FILE", "FORECAST_LEDGER_FILE"):
        assert expected in found, f"掃描器漏掉已知常數 {expected}"


def test_every_state_path_is_registered_for_push():
    """每個 state 路徑常數都必須登錄在 _state_push_paths(),
    否則 CI 上次日讀不到(本機測不出來)。"""
    missing = sorted(_declared_state_consts() - _push_listed_consts()
                     - _READ_ONLY_STATE)
    assert not missing, (
        f"這些 state 路徑沒有登錄到 _state_push_paths():{missing}。\n"
        "跨日累積的狀態未 commit 回 repo,CI 每天都是新 runner → 次日讀不到,"
        "本機卻一切正常。若確定是唯讀輸入,請加進 _READ_ONLY_STATE 並註明理由。"
    )


#: 程式碼裡**直接**組出來的 state 路徑(沒有經過具名常數)。
_INLINE_STATE_PATH_RE = re.compile(r'STATE_ROOT\s*/\s*"([^"]+)"')

#: 常數宣告行 —— inline 寫法**只有這一種**是合法的。
_CONST_DECL_LINE = re.compile(r'^[A-Z][A-Z0-9_]*\s*=\s*STATE_ROOT\s*/')


def test_no_state_file_is_reached_by_an_inline_path():
    """**守衛只看得見遵守宣告慣例的東西**(外審補審 F1)。

    上面那條掃的是大寫常數。2026-08-08 的昨日觀點閉環寫成
    `STATE_ROOT / "analysis_recap.json"`(inline 字面量)—— 掃描器
    看不見它,於是它從不進 push 清單:檔案寫在 runner 上、次日新
    runner 讀到空的,**整個閉環在生產是 no-op**,而本機與測試全綠。

    這條補的是那個縫:inline 路徑一律不合法,必須先宣告成常數
    (常數才會被上面那條檢查登錄)。**修的是類別,不是那一個檔案。**
    """
    # 判準是**逐行**的:
    #   * 註解與 docstring 裡的示範路徑(line 265 的 `STATE_ROOT / "…"`)
    #     建不出檔案,算進來只會製造雜訊,而雜訊會訓練出「把守衛關掉」的反射;
    #   * 常數宣告行(`FOO_FILE = STATE_ROOT / "foo.json"`)是**唯一合法**的
    #     inline 寫法 —— 它正是這條規則要求的東西。
    #
    # **規則是「使用要走常數」,不是「有宣告就好」**(突變驗證抓到):
    # 宣告了 `GOOAYE_RADAR_FILE` 卻仍在函式裡 inline 組同一個路徑時,
    # 只比檔名的話那條照樣過關 —— 而 conftest 的測試隔離導的是常數,
    # inline 那條會繞過隔離直接打真實 state(批#71 r1 真的發生過)。
    stray = sorted({
        f"{m.group(1)}(line {i})"
        for i, ln in enumerate(_SRC.splitlines(), 1)
        if not ln.lstrip().startswith("#")
        and not _CONST_DECL_LINE.match(ln.strip())
        for m in _INLINE_STATE_PATH_RE.finditer(ln)})
    assert not stray, (
        f"這些地方 inline 組了 state 路徑:{stray}。\n"
        "請宣告成模組級常數(`FOO_FILE = STATE_ROOT / \"foo.json\"`)"
        "**並使用該常數** —— push 登錄檢查與 conftest 的測試隔離"
        "都只認得常數,兩者都看不見 inline 路徑。")


def test_the_inline_scanner_would_catch_a_real_stray():
    """守住掃描器本身:正則失配時上面那條會變成永遠通過的空斷言。

    三種行各給一個:**宣告**(合法)、**使用**(違規,即使同名常數
    已宣告)、**註解**(不算)。少了第二種,「宣告過就放行」那個
    寬鬆版本不會被抓到。
    """
    lines = ['A_FILE = STATE_ROOT / "a.json"',
             '    p = STATE_ROOT / "a.json"',
             '# 範例:STATE_ROOT / "doc.json"']
    stray = {m.group(1)
             for ln in lines
             if not ln.lstrip().startswith("#")
             and not _CONST_DECL_LINE.match(ln.strip())
             for m in _INLINE_STATE_PATH_RE.finditer(ln)}
    assert stray == {"a.json"}, stray


def test_read_only_whitelist_stays_read_only():
    """白名單只放唯讀輸入。一旦有程式對它們寫入,就必須改列入 push 清單
    ——否則會退化成 A 類的沉默失效。"""
    for name in _READ_ONLY_STATE:
        assert name in _declared_state_consts(), (
            f"{name} 已不是 state/ 下的常數,請一併清掉白名單條目"
        )
        for pattern in (f"{name}.write_text", f"{name}.write_bytes",
                        f"{name}.open(", f"_atomic_write_text({name}"):
            assert pattern not in _SRC, (
                f"{name} 已有寫入行為({pattern}),不再是唯讀輸入:"
                "請從 _READ_ONLY_STATE 移除並加進 _state_push_paths()。"
            )


def test_the_legacy_model_history_stays_frozen():
    """**legacy 單檔已凍結唯讀 —— 而那件事沒有守衛。**

    實測 `state/model_history.json` 已達 **13.3 MiB**,逼近當初列 A3
    (單檔 gzip)時擔心的 14MB cap。問題已由地基批#1 的**按月分區**
    解掉:新資料進 `state/model_history/YYYY-MM.json.gz`(合計 3.6 MB),
    legacy 只讀不寫,所以它不再長大。

    但「不再長大」完全靠慣例維持 —— 有人加一行寫入,檔案會繼續膨脹,
    而**沒有任何東西會發現**(它在 push 清單裡,git 只會默默收下)。
    2026-08-08 校正待辦清單時把這句話寫進文件,就該讓它擋得住。
    """
    for pattern in ("MODEL_HISTORY_FILE.write_text",
                    "MODEL_HISTORY_FILE.write_bytes",
                    "MODEL_HISTORY_FILE.open(",
                    "_atomic_write_text(MODEL_HISTORY_FILE",
                    "_atomic_write_bytes(MODEL_HISTORY_FILE"):
        assert pattern not in _SRC, (
            f"legacy model_history 又被寫入了({pattern})。\n"
            "它已達 13.3 MiB;新資料應該進 state/model_history/ 的按月分區"
            "(gzip),legacy 維持凍結唯讀。要改這個決定請先處理 repo 大小。")


def test_push_list_has_no_stale_entries():
    """反向:push 清單不該列到已不存在或已非 state 的常數(重構刪檔後留下的
    孤兒路徑會讓 git add 每天噴錯,雜訊蓋掉真正的失敗)。"""
    stale = sorted(_push_listed_consts() - _declared_state_consts())
    assert not stale, f"push 清單含非 state/ 常數:{stale}"


# ------------------------------------------------- B. 跨供應商備援的完整性檢查

_TRUNCATED = "## 今日重點\n盤面偏多,台積電領漲,外資"   # 明顯截斷


@pytest.fixture
def _args():
    """call_llm_analysis(quotes, fair, predictions, news) 的最小可用參數。"""
    news = [{"title": "台積電法說會優於預期", "summary": "毛利率上修",
             "source": "測試", "link": "https://example.com/a"}]
    return {}, {}, {}, news


@pytest.fixture
def _force_fallback(monkeypatch):
    """讓主供應商炸掉並確保備援分支的兩個前置條件成立
    (LLM_PROVIDER != gemini 且有 GEMINI_API_KEY)。"""
    monkeypatch.setattr(mr, "LLM_PROVIDER", "deepseek")
    monkeypatch.setattr(mr, "GEMINI_API_KEY", "test-key")

    def _boom(*_a, **_k):
        raise RuntimeError("primary provider down")

    monkeypatch.setattr(mr, "_call_llm_text", _boom)


def test_primary_failure_uses_deterministic_text_not_gemini(monkeypatch, _args, _force_fallback):
    """主分析**不做跨供應商備援**(使用者政策 2026-08-19:Gemini 只保留
    抽取器的網路故障備援)。主供應商失敗 → 即使 GEMINI_API_KEY 在、
    Gemini 也不得接手寫主分析;退回確定性備援文字(晨報不可斷)。

    舊契約(test_truncated_gemini_fallback_does_not_ship /
    test_complete_gemini_fallback_is_used)固化的是政策變更前的行為,
    已由本條取代。"""
    called = []
    monkeypatch.setattr(mr, "_call_gemini",
                        lambda _p, **_k: called.append(1) or "不該被叫到")

    out = mr.call_llm_analysis(*_args)

    assert not called, "主分析失敗時 Gemini 被叫來寫整份分析(政策外)"
    assert out, "應退回確定性備援文字而非空字串(晨報不可斷)"
    # 備援文字自己也不得推薦政策外供應商(外審 r2 P3:舊文案建議
    # 「切 anthropic / 等 Gemini 恢復」,拆掉備援後它成了主要故障輸出)
    assert "anthropic" not in out.lower(), out[-300:]
    assert "Gemini" not in out, out[-300:]


def test_all_providers_down_still_returns_text(monkeypatch, _args, _force_fallback):
    """主供應商炸掉:仍必須回傳可渲染文字(晨報不可斷)。"""
    assert mr.call_llm_analysis(*_args)


def test_weekend_path_writes_the_manifest_before_pushing_it():
    """r1(Codex,P1):週日路徑原本先 push、後寫 manifest,而那次 push 的**明確
    路徑清單也不含 run_manifest** —— 於是週日寫出來的 manifest 永遠不會被
    commit,repo 裡的檔案停在週六。批#69 的看門狗正是讀這個檔判定「今天有沒有
    跑」,週日必然誤報一封失敗告警。

    這條用 AST 盯**呼叫順序與參數**:兩者都對才有效,而且都是「錯了不會壞、
    只會靜默失準」的那種——單元測試若只驗看門狗自己的判定邏輯照樣全綠
    (第一版測試正是如此)。
    """
    import ast
    import pathlib
    tree = ast.parse(pathlib.Path(mr.__file__).read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "run_weekend_digest")
    manifest_at, push_at, push_call = None, None, None
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id == "_write_run_manifest" and manifest_at is None:
            manifest_at = node.lineno
        elif node.func.id == "_git_commit_and_push_state" and push_at is None:
            push_at, push_call = node.lineno, node
    assert manifest_at and push_at, "週日路徑少了寫 manifest 或 push"
    assert manifest_at < push_at, "manifest 寫在 push 之後 → 永遠不會被 commit"
    src = ast.unparse(push_call)
    assert "RUN_MANIFEST_FILE" in src, "push 清單不含 manifest → 寫了也是白寫"


def test_no_module_state_path_points_into_the_repo_during_tests():
    """批#71:**這條測試存在的理由是「守衛宣稱通用、實作只有一個特例」**。

    `_never_write_repo_state` 的 docstring 寫著「統一把寫入型 state 路徑導到
    暫存目錄,新增的寫入點自動受保護」,但程式碼只導了 `RUN_MANIFEST_FILE`。
    實害:批#66 新增的 `EXDIV_HISTORY_FILE` 不在任何清單裡,測試把真實的
    `state/exdiv_history.json` **115 筆除權息事件清成空陣列**,而 `days` 仍宣稱
    當天收集成功 —— 覆蓋檢查判定完整、紀錄卻是空的,Top5 會用原始價格照常結算。

    改成通用之後,這條測試負責讓它**保持**通用:任何新增的 state 路徑若沒被
    導走,這裡立刻失敗,而不是等某天靜靜地覆寫真實資料。
    """
    from pathlib import Path
    import model_history_store as mhs

    repo_state = Path(__file__).resolve().parents[1] / "state"
    leaked = []
    for mod in (mr, mhs):
        for attr in dir(mod):
            if not (attr.endswith("_FILE") or attr.endswith("_DIR")):
                continue
            value = getattr(mod, attr, None)
            if not isinstance(value, Path):
                continue
            try:
                if value.resolve().is_relative_to(repo_state):
                    leaked.append(f"{mod.__name__}.{attr} → {value}")
            except (OSError, ValueError):
                continue
    assert not leaked, (
        "測試期間這些 state 路徑仍指向 repo,會覆寫真實資料:\n  "
        + "\n  ".join(leaked))


def test_os_level_guard_blocks_dynamically_built_state_paths():
    """批#74(第七輪 P1-10):**與命名/宣告位置無關的守衛。**

    「掃描指向 repo state 的 Path 常數」比逐一 monkeypatch 好,但仍漏兩類:
      (a) 函式內動態組出的路徑(模組層掃不到)
      (b) 直接 `open("state/…", "w")`
    批#71 r1 那次真實資料損毀(exdiv_history.json 115 筆被清空並提交)
    就是被這兩類漏掉的。這條驗守衛對兩者都會當場拋。
    """
    import pytest as _pytest
    from pathlib import Path as _P

    target = _REPO_STATE / "should_never_be_written.json"
    with _pytest.raises(AssertionError, match="真實 state"):
        target.write_text("x", encoding="utf-8")
    with _pytest.raises(AssertionError, match="真實 state"):
        target.write_bytes(b"x")
    with _pytest.raises(AssertionError, match="真實 state"):
        open(str(_REPO_STATE / "should_never_be_written.json"), "w").close()
    # 唯讀不受影響(測試要能讀真實 state 當語料)
    assert _REPO_STATE.exists()
    # tmp 路徑完全不受影響
    import tempfile
    tmp = _P(tempfile.mkdtemp()) / "ok.json"
    tmp.write_text("{}", encoding="utf-8")
    assert tmp.read_text(encoding="utf-8") == "{}"


def test_all_state_paths_derive_from_state_root():
    """state 路徑必須由 `STATE_ROOT` 衍生 —— 硬寫 `Path("state/…")` 的話,
    換根(測試用 tmp root)就換不掉,而那正是「新增 state 檔忘記隔離」的來源。"""
    import re as _re
    hard = _re.findall(r'Path\("state/[^"]+"\)', _SRC)
    assert not hard, f"仍有硬寫的 state 路徑:{hard}"


def test_no_test_file_reads_repo_files_through_a_cwd_relative_path():
    """r2:**同一個病灶在兩輪裡出現兩次,所以擋成類別而不是逐點修。**

    測試檔用 `Path("state/…")` / `Path(".github/…")` 這種相對字面值時,只有
    「從 repo 根目錄啟動 pytest」才會對。從別處啟動時的失敗形狀都很難看:

      - `pytest.skip` → 整組檢查消失,報告裡只剩一排 `s`
      - `glob()` 掃到空集合 → 迴圈不執行 → **無聲通過**(連 `s` 都沒有)
      - 寫入守衛的目標打在別的目錄 → 守衛不命中,真實 state 反而全裸

    正確的錨只有一個:`Path(__file__).resolve().parents[1]`。檔案位置不會因為
    誰在哪裡啟動 pytest 而改變,CWD 會。

    (`mr.__file__` / `tmp_path` / 絕對路徑都不在此列 —— 它們本來就與 CWD 無關。)
    """
    import ast as _ast

    root = Path(__file__).resolve().parents[1]
    top = {e.name for e in root.iterdir()}
    offenders = []
    for src in sorted(Path(__file__).resolve().parent.glob("*.py")):
        tree = _ast.parse(src.read_text(encoding="utf-8"))
        for node in _ast.walk(tree):
            if not isinstance(node, _ast.Call) or not node.args:
                continue
            fn = node.func
            name = (fn.attr if isinstance(fn, _ast.Attribute)
                    else getattr(fn, "id", ""))
            if name not in {"Path", "_P", "_Path", "open"}:
                continue
            first = node.args[0]
            if not (isinstance(first, _ast.Constant)
                    and isinstance(first.value, str)):
                continue
            head = first.value.replace("\\", "/").split("/")[0]
            if head in top:
                offenders.append(
                    f"{src.name}:{node.lineno} → {first.value!r}")
    assert not offenders, (
        "測試檔用了相對 CWD 的 repo 路徑,從 repo 根目錄以外執行會靜默失效:\n  "
        + "\n  ".join(offenders)
        + "\n  請改用 `Path(__file__).resolve().parents[1] / ...`。")


def test_os_level_guard_blocks_move_destinations_not_just_sources():
    """r1(Codex,P1):`Path.replace`/`rename` 是以類別方法被呼叫的,wrapper 收到
    `(self, target)` —— `args[0]` 是**來源**。第一版守衛用預設的 `args[0]`,
    於是 `tmp.replace(Path("state/x.json"))` 完全不會被擋,而那正是本批要防的
    那一類不可回復損毀:`model_history_store.write_partition_manifest` 就是這樣
    寫 manifest 的(`tmp.replace(partition_dir / MANIFEST_NAME)`)。
    """
    import tempfile
    import pytest as _pytest
    from pathlib import Path as _P

    src = _P(tempfile.mkdtemp()) / "tmp.json"
    src.write_text("{}", encoding="utf-8")
    with _pytest.raises(AssertionError, match="搬動 repo"):
        src.replace(_REPO_STATE / "should_never_be_replaced.json")
    with _pytest.raises(AssertionError, match="搬動 repo"):
        src.rename(_REPO_STATE / "should_never_be_renamed.json")
    # 來源在 repo state 也要擋(把真實 state 搬走一樣是損毀)
    with _pytest.raises(AssertionError, match="搬動 repo"):
        (_REPO_STATE / "exdiv_history.json").replace(src)
    # tmp → tmp 完全不受影響
    dst = src.with_name("moved.json")
    src.replace(dst)
    assert dst.read_text(encoding="utf-8") == "{}"


def test_producers_and_consumer_share_one_state_root(tmp_path, monkeypatch):
    """r1(Codex,P2):**生產者與消費者必須共用同一個 state 根。**

    晨報的 state 路徑改由 `STATE_ROOT` 衍生後,`podcast_digest.py` 與
    `gooaye_radar.py` 仍硬寫 `state/…` —— 設定 `STATE_ROOT` 之後生產者寫舊路徑、
    晨報讀新路徑,兩邊靜默分家(podcast 內容變空/過期、radar 的 GUID 去重失效
    讓已寄過的集數再出現)。而新的原始碼掃描只讀 `morning_report.py`,看不到它們。

    這條用**獨立匯入**驗:設好環境變數後重新匯入三個模組,比對解析結果。
    """
    import importlib
    import sys as _sys

    root = tmp_path / "iso_state"
    monkeypatch.setenv("STATE_ROOT", str(root))
    saved = {n: _sys.modules.pop(n, None)
             for n in ("morning_report", "podcast_digest", "gooaye_radar",
                       "model_history_store")}
    try:
        mr2 = importlib.import_module("morning_report")
        pod = importlib.import_module("podcast_digest")
        rad = importlib.import_module("gooaye_radar")
        mhs = importlib.import_module("model_history_store")
        assert mr2.STATE_ROOT == root
        assert pod.STATE_FILE == root / "podcast_digest.json"
        assert rad.RADAR_STATE_FILE == root / "gooaye_radar.json"
        assert mhs.DEFAULT_PARTITION_DIR == root / "model_history"
        # 生產者寫的、晨報讀的,必須是**同一個檔**
        assert pod.STATE_FILE == mr2.PODCAST_DIGEST_FILE
    finally:
        for name, mod in saved.items():
            if mod is not None:
                _sys.modules[name] = mod
            else:
                _sys.modules.pop(name, None)


def test_no_workflow_overrides_state_root():
    """workflow 的 `git add state/…` 是硬寫的,因此它們**假設** `STATE_ROOT`
    維持預設值 `state`。這個假設目前成立(生產不設該變數),但它是沉默的:
    哪天有人在 workflow 裡設了 `STATE_ROOT`,生產者會寫到新根、而 `git add`
    仍指向舊路徑 → state 從此不再被 commit 回 repo,而 CI 每天都是新 runner,
    次日就讀不到,本機卻一切正常(這個 repo 的經典失敗形狀)。

    要嘛 workflow 一起改成用變數,要嘛不准設 —— 選後者並在這裡釘住,
    因為 shell 端的間接層更容易出錯而收益有限。
    """
    from pathlib import Path
    offenders = []
    # 批#78 r1:glob 相對於 CWD —— 從別處啟動 pytest 時會掃到**空集合**,
    # 迴圈整段不執行、測試無聲通過。空集合的真空通過比 skip 更危險
    # (skip 至少會在報告裡看得到),所以下面同時釘住「有掃到東西」。
    wf_dir = Path(__file__).resolve().parents[1] / ".github" / "workflows"
    workflows = sorted(wf_dir.glob("*.yml"))
    assert workflows, f"{wf_dir} 沒有任何 workflow —— 這條檢查會真空通過"
    for wf in workflows:
        text = wf.read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "STATE_ROOT" in stripped:
                offenders.append(f"{wf.name}: {stripped[:70]}")
    assert not offenders, (
        "workflow 設定了 STATE_ROOT,但 `git add state/…` 仍硬寫舊路徑:\n  "
        + "\n  ".join(offenders))


def test_state_schema_contract_has_a_daily_trigger():
    """r1(Codex,P1):**契約需要每日觸發點。**

    `tests/test_state_schema_contract.py` 原本只由 pytest 跑,而 pytest 只在
    push/PR 觸發;每日的 state commit 帶 `[skip ci]`,所以今天寫壞的 state 要等到
    下一次有人 push 程式碼才可能被發現 —— 期間損毀資料會直接餵給後續晨報。

    這條釘住:每日 workflow 必須在跑完晨報之後執行那組契約。
    (放在寄信之後,所以契約失敗不影響「晨報不可斷」;失敗會讓 job 變紅,
     既有的 alert-on-failure job 據此發告警信。)
    """
    from pathlib import Path
    text = (Path(__file__).resolve().parents[1]
            / ".github" / "workflows" / "morning-report-b.yml"
            ).read_text(encoding="utf-8")
    assert "tests/test_state_schema_contract.py" in text, \
        "每日 workflow 沒有執行 state schema 契約 —— 它只會在有人 push 時被跑到"
    # 必須在跑完晨報**之後**(信已寄出),否則契約失敗會擋掉當天的信
    assert text.index("python morning_report.py") < \
        text.index("tests/test_state_schema_contract.py"), \
        "契約排在晨報之前 —— 失敗會擋掉當天的信,違反「晨報不可斷」"


def test_every_manifest_key_written_is_also_persisted():
    """**寫進 `_RUN_MANIFEST` 的診斷鍵,都必須真的落地。**

    批#81 r1(Codex,P2)。`_write_run_manifest` 是**重建白名單 dict**,
    沒列到的鍵一律被靜默丟掉 —— 記憶體裡有值、檔案裡沒有,而診斷欄位存在的
    唯一理由就是累積成趨勢。這個坑至今發生**八次**,每一次都在 writer 裡
    留下一行「同一個坑的第 N 次」的註解,然後下一次還是有人忘記。

    逐點補一行等於預約第九次。這條測試用 AST 掃出所有
    `_RUN_MANIFEST["x"] = ...` 與 `_RUN_MANIFEST.setdefault("x", ...)` 的鍵,
    比對 `_MANIFEST_DIAGNOSTIC_KEYS`(落地)與 `_MANIFEST_TRANSIENT_KEYS`
    (刻意不落地),漏列時**指名是哪一個鍵**。

    第十輪 P1-12:`state_writes` 搬進 `run_manifest.ManifestRecorder` 之後,
    只掃主模組的版本立刻漏掉它 —— **只掃單一檔的守衛,程式碼一搬家就失明**。
    掃描範圍改成「所有會碰 manifest 的檔」,而且要認得 recorder 內部的
    `self.data[...]`(它與 `_RUN_MANIFEST` 是同一個 dict)。
    """
    import ast
    import morning_report as mr

    # 2026-08-03:`llm_experiment` 的寫入搬進 `experiment_record.py` 之後,
    # 這條又失明了一次 —— 而上面那段註解正是在講同一件事。
    # **列舉模組本身就是那個會漏的東西**,所以改成掃根目錄所有模組:
    # 新模組不必有人記得加進來。
    root = Path(mr.__file__).resolve().parent
    files = sorted(f for f in root.glob("*.py") if not f.name.startswith("_"))
    assert len(files) > 10, f"掃到的模組太少({len(files)}),掃描器可能壞了"
    trees = [ast.parse(f.read_text(encoding="utf-8")) for f in files]
    def _is_manifest(node) -> bool:
        """`_RUN_MANIFEST` 或 recorder 內部的 `self.data` —— 兩者是同一個 dict。

        只認前者的話,搬進 `ManifestRecorder` 的鍵就會從守衛的視野消失
        (第十輪 P1-12 搬走 `state_writes` 時立刻發生)。
        """
        if isinstance(node, ast.Name):
            # `manifest` 是**注入時的約定名稱**(見 `experiment_record`):
            # 葉模組不碰模組全域,manifest 由呼叫端交進來。
            return node.id in ("_RUN_MANIFEST", "manifest")
        return (isinstance(node, ast.Attribute) and node.attr == "data"
                and isinstance(node.value, ast.Name) and node.value.id == "self")

    written = set()
    for node in [n for t in trees for n in ast.walk(t)]:
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AugAssign):
            targets = [node.target]
        for t in targets:
            if (isinstance(t, ast.Subscript) and _is_manifest(t.value)
                    and isinstance(t.slice, ast.Constant)
                    and isinstance(t.slice.value, str)):
                written.add(t.slice.value)
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "setdefault"
                and _is_manifest(node.func.value)
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            written.add(node.args[0].value)

    assert written, "AST 掃不到任何 _RUN_MANIFEST 寫入 —— 掃描器壞了,本測試無效"
    known = set(mr._MANIFEST_DIAGNOSTIC_KEYS) | set(mr._MANIFEST_TRANSIENT_KEYS)
    missing = sorted(written - known)
    assert not missing, (
        f"這些鍵有寫進 _RUN_MANIFEST 卻不會落地:{missing}\n"
        "  請加進 `_MANIFEST_DIAGNOSTIC_KEYS`(要落地)或 "
        "`_MANIFEST_TRANSIENT_KEYS`(刻意不落地)。\n"
        "  writer 是重建白名單 dict,漏列的鍵會被**靜默**丟掉。")

    # 反向:宣告了卻沒有人寫的鍵 = 死宣告,同樣要被看見
    stale = sorted(set(mr._MANIFEST_DIAGNOSTIC_KEYS) - written)
    assert not stale, (
        f"這些鍵列在 _MANIFEST_DIAGNOSTIC_KEYS 卻沒有任何地方寫入:{stale} —— "
        "功能可能已移除,宣告要一起清掉")


def test_manifest_diagnostic_keys_survive_serialisation(tmp_path, monkeypatch):
    """**驗序列化後的檔案,不是記憶體裡的 dict。**

    r1 的 finding 之所以成立,正是因為既有測試都只看 `_RUN_MANIFEST`
    ——那是 writer 的**輸入**,不是它的輸出。
    """
    import datetime as _dt
    import json as _json
    import morning_report as mr

    target = tmp_path / "run_manifest.json"
    monkeypatch.setattr(mr, "RUN_MANIFEST_FILE", target)
    probe = {"rows": 115, "min_ex_date": "2026-07-28",
             "max_ex_date": "2026-10-06", "days_back": 2, "days_forward": 68}
    saved = {k: mr._RUN_MANIFEST.get(k) for k in mr._MANIFEST_DIAGNOSTIC_KEYS}
    try:
        for key in mr._MANIFEST_DIAGNOSTIC_KEYS:
            mr._RUN_MANIFEST[key] = probe if key == "exdiv_preview" else {"probe": key}
        mr._write_run_manifest(_dt.datetime.now(mr.TPE),
                               report_kind=rq.MORNING_REPORT)
        assert target.exists(), "manifest 沒有被寫出來"
        landed = _json.loads(target.read_text(encoding="utf-8"))
        for key in mr._MANIFEST_DIAGNOSTIC_KEYS:
            assert key in landed, f"{key} 沒有落地 —— 又被重建白名單丟掉了"
        assert landed["exdiv_preview"] == probe
    finally:
        for k, v in saved.items():
            if v is None:
                mr._RUN_MANIFEST.pop(k, None)
            else:
                mr._RUN_MANIFEST[k] = v


# 批#90f 的 `test_llm_switches_are_overridable_without_editing_the_workflow`
# 已由 `tests/test_workflow_contract.py` 取代並強化(第九輪 P2-1)。
# 舊版只驗「這一行裡出現過 `vars.`」,所以 `OPENAI_MODEL: ${{ vars.LLM_PROVIDER }}`
# 這種複製貼上錯誤照樣通過 —— 而它的症狀正是「沒有錯誤、沒有告警,只是沒生效」。
# 保留一個較弱的重複測試只會給假的覆蓋感,所以刪掉而不是並存。


def test_no_module_defines_the_same_top_level_name_twice():
    """**重複的頂層定義是合法 Python,所以沒有任何既有檢查會抓到。**

    批#92:我用字串切片改 `morning_report.py` 時索引順序弄反,結果檔案裡有
    **三份** `_call_openai` 與 `_run_llm_shadow` —— 而 `ruff check` 全綠、
    1,324 條測試全過(重複定義只是後者覆蓋前者,而最後一份剛好是對的)。
    那代表這一類損壞可以一路通過所有閘門進到生產。

    ruff 的 F811 只在**同一個 scope 內未被使用就重定義**時觸發,函式被呼叫過
    就不算 —— 所以它擋不住這種情形。這條用 AST 自己數。
    """
    import ast
    import collections

    root = Path(__file__).resolve().parents[1]
    problems = []
    for path in sorted(root.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        # 第十輪 P2-4:**模組層賦值也算。** 我第一版只查 def/class,
        # 而 `news_events.py` 裡 `_SUBJECT_LATIN_STOP` 定義了兩次一直沒被抓到 ——
        # 後者靜默勝出,讓第一個消費端失去 LIMITED / CORPORATION。
        # 守衛只查一半 = 那一半以外的重複可以永遠通過。
        names = collections.Counter()
        for n in tree.body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                              ast.ClassDef)):
                names[n.name] += 1
            elif isinstance(n, ast.Assign):
                for t in n.targets:
                    if isinstance(t, ast.Name):
                        names[t.id] += 1
            elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
                names[n.target.id] += 1
        for name, count in names.items():
            if count > 1:
                problems.append(f"{path.name}: {name} 定義了 {count} 次")
    assert not problems, (
        "有頂層名稱被重複定義(後者靜默覆蓋前者):\n  " + "\n  ".join(problems))


def test_a_failed_state_write_is_recorded_not_swallowed(tmp_path, monkeypatch):
    """批#108(第八輪 state staging):**要知道這次交易完不完整。**

    跨檔的交易邊界其實已經存在 —— state 只透過 `git commit` 對下一班可見,
    而 commit 發生在所有寫入之後。缺的不是 staging,是**可見性**:
    某個檔寫失敗時,呼叫端普遍是 `except: print("(不影響晨報)")`,
    而 push 只 add「存在的路徑」→ 那個檔以**舊版內容**被一起 commit 出去,
    下一班讀到過期資料卻以為是當日的。症狀是安靜的:某一塊資料停在昨天。
    """
    import morning_report as mr

    saved = dict(mr._STATE_WRITES)
    mr._STATE_WRITES.clear()
    try:
        good = tmp_path / "ok.json"
        mr._atomic_write_text(good, "{}")
        assert mr._STATE_WRITES["ok.json"]["ok"] is True
        assert mr._STATE_WRITES["ok.json"]["bytes"] == 2

        # 寫不進去(目錄不存在)→ 必須記帳,而且例外照樣往外拋
        bad = tmp_path / "missing" / "bad.json"
        with pytest.raises(OSError):
            mr._atomic_write_text(bad, "{}")
        rec = mr._STATE_WRITES["bad.json"]
        assert rec["ok"] is False and rec["error"], rec
    finally:
        mr._STATE_WRITES.clear()
        mr._STATE_WRITES.update(saved)


def test_the_manifest_and_commit_message_name_the_stale_files(tmp_path, monkeypatch):
    """失敗必須同時出現在 manifest、降級清單與 commit 訊息。

    **只記成功的等於沒記** —— 失敗才是要看的那一半。
    """
    import morning_report as mr

    saved_w, saved_d = dict(mr._STATE_WRITES), list(mr._DEGRADED_STEPS)
    saved_m = mr._RUN_MANIFEST.get("state_writes")
    mr._STATE_WRITES.clear()
    mr._DEGRADED_STEPS.clear()
    monkeypatch.setattr(mr, "RUN_MANIFEST_FILE", tmp_path / "run_manifest.json")
    try:
        mr._STATE_WRITES["history.json"] = {"ok": True, "bytes": 10}
        mr._STATE_WRITES["forecast_ledger.json"] = {
            "ok": False, "bytes": 20, "error": "OSError: disk full"}
        mr._write_run_manifest(_dt.datetime.now(mr.TPE),
                               report_kind=rq.MORNING_REPORT)

        landed = _json.loads((tmp_path / "run_manifest.json").read_text(encoding="utf-8"))
        sw = landed["state_writes"]
        assert sw["attempted"] == 2
        assert sw["failed"] == ["forecast_ledger.json"]
        assert "disk full" in sw["detail"]["forecast_ledger.json"]["error"]
        assert "history.json" not in sw["detail"], "成功的不必佔版面"
        assert any("state:write_failed" in d for d in mr._DEGRADED_STEPS), \
            mr._DEGRADED_STEPS
    finally:
        mr._STATE_WRITES.clear()
        mr._STATE_WRITES.update(saved_w)
        mr._DEGRADED_STEPS[:] = saved_d
        if saved_m is None:
            mr._RUN_MANIFEST.pop("state_writes", None)
        else:
            mr._RUN_MANIFEST["state_writes"] = saved_m


def test_failures_after_the_manifest_snapshot_reach_the_landed_degraded_list(
        tmp_path, monkeypatch):
    """r2(Codex,P2):**`base` 是從磁碟讀回來的,帶著快照當時的降級清單。**

    `_write_run_manifest` 跑在交付之前;archive / podcast / history 都在那之後
    才寫。它們失敗時,只 append 到記憶體裡的 `_DEGRADED_STEPS` 而不同步 `base`,
    落地的降級清單裡就**沒有那一筆** —— 而那份清單正是資料品質區與看門狗在讀的。

    既有測試在 `_write_run_manifest()` **之前**注入失敗,所以完全不會走到
    補寫這條路徑。這一條專門測快照之後才發生的失敗。
    """
    import morning_report as mr

    saved_w, saved_d = dict(mr._STATE_WRITES), list(mr._DEGRADED_STEPS)
    mr._STATE_WRITES.clear()
    mr._DEGRADED_STEPS.clear()
    manifest = tmp_path / "run_manifest.json"
    monkeypatch.setattr(mr, "RUN_MANIFEST_FILE", manifest)
    try:
        # 快照:此時一切正常
        mr._write_run_manifest(_dt.datetime.now(mr.TPE),
                               report_kind=rq.MORNING_REPORT)
        first = _json.loads(manifest.read_text(encoding="utf-8"))
        assert first["state_writes"]["failed"] == []
        assert not any("state:write_failed" in d
                       for d in first.get("degraded_steps") or [])

        # 交付之後才失敗
        mr._STATE_WRITES["history.json"] = {
            "ok": False, "bytes": 5, "error": "OSError: disk full"}
        mr._refresh_state_writes_in_manifest()

        landed = _json.loads(manifest.read_text(encoding="utf-8"))
        assert landed["state_writes"]["failed"] == ["history.json"]
        assert any("state:write_failed" in d
                   for d in landed.get("degraded_steps") or []), \
            "交付後的失敗沒有進到**落地的**降級清單"
    finally:
        mr._STATE_WRITES.clear()
        mr._STATE_WRITES.update(saved_w)
        mr._DEGRADED_STEPS[:] = saved_d


def test_late_failures_are_added_to_the_marker_not_hidden_by_early_ones(
        tmp_path, monkeypatch):
    """r3(Codex,P2):**去重不能讓標記變成陳舊的。**

    快照前 `forecast_ledger.json` 失敗 → 標記名它;交付後 `history.json` 也
    失敗時,原本的「已經有就不再加」會讓落地的標記**只名第一個**,
    消費端看不出新增壞掉的是哪個檔。

    我前一版的測試從零失敗開始、只在快照後加一個 —— 所以完全看不到這個
    混合路徑。守衛要從當前的 failed 集合**重算**,不是「加或不加」。
    """
    import morning_report as mr

    saved_w, saved_d = dict(mr._STATE_WRITES), list(mr._DEGRADED_STEPS)
    mr._STATE_WRITES.clear()
    mr._DEGRADED_STEPS.clear()
    manifest = tmp_path / "run_manifest.json"
    monkeypatch.setattr(mr, "RUN_MANIFEST_FILE", manifest)
    try:
        mr._STATE_WRITES["forecast_ledger.json"] = {
            "ok": False, "bytes": 1, "error": "OSError: early"}
        mr._write_run_manifest(_dt.datetime.now(mr.TPE),
                               report_kind=rq.MORNING_REPORT)
        early = _json.loads(manifest.read_text(encoding="utf-8"))
        assert early["state_writes"]["failed"] == ["forecast_ledger.json"]

        mr._STATE_WRITES["history.json"] = {
            "ok": False, "bytes": 2, "error": "OSError: late"}
        mr._refresh_state_writes_in_manifest()

        landed = _json.loads(manifest.read_text(encoding="utf-8"))
        assert landed["state_writes"]["failed"] == [
            "forecast_ledger.json", "history.json"]
        marks = [d for d in landed.get("degraded_steps") or []
                 if d.startswith("state:write_failed")]
        assert len(marks) == 1, f"標記重複了:{marks}"
        assert "history.json" in marks[0], (
            f"交付後新壞掉的檔沒有進標記(標記只有:{marks[0]})")
        assert "forecast_ledger.json" in marks[0], "早期的失敗被蓋掉了"
    finally:
        mr._STATE_WRITES.clear()
        mr._STATE_WRITES.update(saved_w)
        mr._DEGRADED_STEPS[:] = saved_d


def test_the_recorder_owns_the_manifest_dict_itself():
    """第十輪 P1-12:**recorder 的 `data` 必須就是主模組的 `_RUN_MANIFEST`。**

    不是複本、也不重新綁定 —— 131 處測試引用全部是就地變更
    (`.pop` / `[k] = v` / `.setdefault`)。一旦分家,兩邊都「有資料」
    但不是同一份,而那種失敗是靜默的。
    """
    import morning_report as mr

    assert mr._RUN_MANIFEST is mr._RECORDER.data
    probe = {"__di_probe__": 1}
    mr._RUN_MANIFEST.update(probe)
    try:
        assert mr._RECORDER.data.get("__di_probe__") == 1
    finally:
        mr._RUN_MANIFEST.pop("__di_probe__", None)


def test_the_recorder_builds_a_manifest_without_touching_the_world():
    """組裝是**純函式**:不寫檔、不讀全域、不碰網路 —— 所以可以單獨測。

    它也是那個「漏列白名單」坑的唯一守門處:診斷鍵一律由 `DIAGNOSTIC_KEYS`
    統一帶出,不逐項明列(逐項明列正是發生過八次的那個坑)。
    """
    import run_manifest as rm

    rec = rm.ManifestRecorder()
    rec.mark_phase("抓資料", 100.0)
    rec.mark_phase("LLM", 130.0)
    rec.mark_phase("寄信", 175.5)
    rec.data["llm_extractor"] = {"called": True, "valid": 3}
    rec.data["data_checks"] = {"warn": 1}

    built = rec.build(date="2026-08-01 06:00", report_kind=rq.MORNING_REPORT, budget_seconds=2100.0,
                      news_workers=8, degraded_steps=["a", "a", "b"],
                      feeds={"example.com": {"ok": 3, "fail": 1}})

    assert built["total_seconds"] == 75.5
    assert [p["label"] for p in built["phases"]] == ["抓資料", "LLM"]
    assert built["phases"][0]["seconds"] == 30.0
    assert built["degraded_steps"] == ["a", "b"], "重複的降級項沒有去重"
    assert built["feeds"] == {"example.com": {"ok": 3, "fail": 1}}
    # 每一個診斷鍵都必須出現(即使值是 None)—— 這正是白名單的意義
    for key in rm.DIAGNOSTIC_KEYS:
        assert key in built, f"{key} 沒有被帶出去"
    assert built["llm_extractor"] == {"called": True, "valid": 3}
    # 暫時鍵不落地
    for key in rm.TRANSIENT_KEYS:
        assert key not in built, f"{key} 是中間結構,不該落地"


def test_the_recorder_reports_only_failed_state_writes():
    """**只記成功的等於沒記** —— `detail` 只留失敗項,而 `attempted` 記全部。"""
    import run_manifest as rm

    rec = rm.ManifestRecorder()
    failed = rec.record_state_writes({
        "history.json": {"ok": True, "bytes": 10},
        "ledger.json": {"ok": False, "bytes": 2, "error": "OSError"}})
    assert failed == ["ledger.json"]
    sw = rec.data["state_writes"]
    assert sw["attempted"] == 2 and sw["failed"] == ["ledger.json"]
    assert "history.json" not in sw["detail"], "成功的不必佔版面"
    assert rec.record_state_writes({}) == []


def test_the_recorder_shares_the_degraded_list_too():
    """第十輪 P1-12:**降級清單也必須是同一個 list 物件。**

    manifest 與降級清單是同一件事的兩面(「發生了什麼」與「哪裡不對」)。
    recorder 若持有複本,`record_llm_call` 記下的
    `llm:effort_not_applied` 就只會出現在 recorder 裡,而落地的 manifest
    與資料品質區讀的是主模組那一份 —— 兩邊都「有資料」但不是同一份,
    而那種分家是靜默的。
    """
    import morning_report as mr

    assert mr._DEGRADED_STEPS is mr._RECORDER.degraded
    saved = list(mr._DEGRADED_STEPS)
    mr._RUN_MANIFEST.pop("llm", None)
    try:
        mr._record_llm_call("primary", "openai", "gpt-5.6-luna",
                            requested_effort="max", applied_effort="",
                            usage={"prompt_tokens": 1, "completion_tokens": 1},
                            accepted=True)
        assert any("effort_not_applied" in d for d in mr._DEGRADED_STEPS), \
            "recorder 記的降級沒有出現在主模組那一份"
    finally:
        mr._DEGRADED_STEPS[:] = saved
        mr._RUN_MANIFEST.pop("llm", None)


def test_moved_recorder_logic_still_behaves_identically():
    """搬家不得改變行為 —— 三個委派入口的對外行為要與搬家前一致。"""
    import run_manifest as rm

    rec = rm.ManifestRecorder(degraded=[])
    # 角色分槽:不同角色不得互相覆寫
    rec.record_llm_call("extractor", "openai", "gpt-5.6-luna", accepted=True,
                        usage={"prompt_tokens": 10, "completion_tokens": 2})
    rec.record_llm_call("primary", "deepseek", "deepseek-v4-pro", accepted=True,
                        usage={"prompt_tokens": 20, "completion_tokens": 4})
    assert rec.data["llm"]["extractor"]["provider"] == "openai"
    assert rec.data["llm"]["primary"]["provider"] == "deepseek"
    # 未通過的進 attempts,不佔角色槽
    rec.record_llm_call("primary", "deepseek", "deepseek-v4-pro",
                        accepted=False, error="ReadTimeout")
    assert rec.data["llm"]["primary"]["provider"] == "deepseek"
    assert rec.data["llm"]["attempts"][-1]["error"].startswith("ReadTimeout")

    # writer:報告層驗收未過 → 不得宣稱 provider 寫了這封信
    rec.record_report_writer(False)
    assert rec.data["llm"]["writer"]["source"] == "deterministic_fallback"
    rec.record_report_writer(True)
    assert rec.data["llm"]["writer"] == {"source": "primary",
                                         "provider": "deepseek",
                                         "model": "deepseek-v4-pro"}

    # 身分遷移:合併是進度、分裂是缺陷
    out = rec.record_identity_migration(
        {"recomputed": 2, "canonicalized": 2,
         "changed_pairs": {"2奈米,蘋果": "2nm,aapl", "2nm,apple": "2nm,aapl"}},
        coverage={"3": 2}, schema_version=4)
    assert out["collisions"] == 1 and out["splits"] == 0
    assert not any("event_identity:split" in d for d in rec.degraded)
