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
import re
from pathlib import Path

import pytest

import morning_report as mr

_SRC = Path(mr.__file__).read_text(encoding="utf-8")


# ---------------------------------------------------------------- A. state 登錄
#
# 刻意做**原始碼層**檢查而非讀執行期的常數值:conftest 的 autouse fixture 會把
# 多個 state 常數 monkeypatch 到 tmp 目錄(正確做法,免得測試寫進真實 state/),
# 執行期比對只會拿到兩邊都被改寫的 tmp 路徑,驗不到「有沒有登錄」這件事。
# 登錄本來就是原始碼事實,就在原始碼層驗。

# 唯讀輸入,不是「跑完後回流」的狀態:程式只 .exists() / .read_text(),
# 由外部工具或人工維護。故不該出現在 push 清單。
_READ_ONLY_STATE = {"REVENUE_CONSENSUS_FILE", "TWSE_TOP100_ARCHIVE_FILE"}

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
    body = _SRC.split("def _state_push_paths(")[1].split("\ndef ")[0]
    return set(re.findall(r"str\(([A-Z][A-Z0-9_]*)\)", body))


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


def test_truncated_gemini_fallback_does_not_ship(monkeypatch, _args, _force_fallback):
    """主供應商失敗 → Gemini 備援**也**截斷時,必須退回確定性備援文字,
    不能把半截分析當成正常結果送進渲染層。

    這條是生產實際會走的路徑;函式內另外兩處(主呼叫、concise 重試)早有
    _analysis_complete_enough,唯獨這條沒有。"""
    assert not mr._analysis_complete_enough(_TRUNCATED), "測試素材本身應被判為不完整"
    monkeypatch.setattr(mr, "_call_gemini", lambda _p: _TRUNCATED)

    out = mr.call_llm_analysis(*_args)

    assert out != _TRUNCATED, "截斷的備援輸出被原樣送出(頂部 KPI 會整排變「—」)"
    assert out, "應退回確定性備援文字而非空字串"


def test_complete_gemini_fallback_is_used(monkeypatch, _args, _force_fallback):
    """反向:備援輸出**完整**時要正常採用,別讓上一條測試被「一律退回備援文字」
    這種偷懶實作矇混過關。"""
    good = ("## 今日重點\n盤面偏多,台積電領漲。\n"
            "## 我的明確立場\n偏多(淨分 +3)\n"
            "## 一句話總結\n偏多,留意月線支撐。\n")
    assert mr._analysis_complete_enough(good), (
        "測試素材本身要先通過完整性檢查,否則本條對照組會變成永遠不執行的空測試"
    )
    monkeypatch.setattr(mr, "_call_gemini", lambda _p: good)

    assert mr.call_llm_analysis(*_args) == good


def test_all_providers_down_still_returns_text(monkeypatch, _args, _force_fallback):
    """主供應商與 Gemini 都炸:仍必須回傳可渲染文字(晨報不可斷)。"""
    def _boom(_p):
        raise RuntimeError("gemini down")

    monkeypatch.setattr(mr, "_call_gemini", _boom)
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

    repo_state = Path("state").resolve()
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

    target = _P("state") / "should_never_be_written.json"
    with _pytest.raises(AssertionError, match="真實 state"):
        target.write_text("x", encoding="utf-8")
    with _pytest.raises(AssertionError, match="真實 state"):
        target.write_bytes(b"x")
    with _pytest.raises(AssertionError, match="真實 state"):
        open("state/should_never_be_written.json", "w").close()
    # 唯讀不受影響(測試要能讀真實 state 當語料)
    assert _P("state").exists()
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
