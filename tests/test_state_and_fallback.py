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
import os
import re
from pathlib import Path

import pytest

import morning_report as mr

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
            / ".github" / "workflows" / "morning-report.yml"
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
    """
    import ast
    import morning_report as mr

    tree = ast.parse(Path(mr.__file__).read_text(encoding="utf-8"))
    written = set()
    for node in ast.walk(tree):
        # _RUN_MANIFEST["x"] = ...
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AugAssign):
            targets = [node.target]
        for t in targets:
            if (isinstance(t, ast.Subscript)
                    and isinstance(t.value, ast.Name)
                    and t.value.id == "_RUN_MANIFEST"
                    and isinstance(t.slice, ast.Constant)
                    and isinstance(t.slice.value, str)):
                written.add(t.slice.value)
        # _RUN_MANIFEST.setdefault("x", ...)
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "setdefault"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "_RUN_MANIFEST"
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
        mr._write_run_manifest(_dt.datetime.now(mr.TPE))
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
        names = collections.Counter(
            n.name for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)))
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
        mr._write_run_manifest(_dt.datetime.now(mr.TPE))

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


def test_the_shadow_timeout_follows_its_own_model_and_is_capped():
    """批#108:影子的 timeout 原本寫死 120 秒。

    luna 在 xhigh 實測要 **196 秒** —— 影子會每天逾時,帳本永遠收不到樣本,
    而「影子沒有資料」看起來就只是「還在累積」。

    但它是**選配**的評估工具,不該有能力吃掉十分鐘的執行預算:
    超過上限就是今天沒有樣本(既有的設計降級)。
    """
    import importlib

    import morning_report as mr

    def _reload(**env):
        for k, v in env.items():
            os.environ[k] = v
        try:
            return importlib.reload(mr).LLM_SHADOW_TIMEOUT
        finally:
            for k in env:
                os.environ.pop(k, None)
            importlib.reload(mr)

    xhigh = _reload(LLM_SHADOW_PROVIDER="openai",
                    LLM_SHADOW_REASONING_EFFORT="xhigh")
    assert xhigh > 196, f"影子跑不完 luna xhigh(實測 196s),只有 {xhigh}s"
    assert xhigh <= mr.LLM_SHADOW_MAX_TIMEOUT, "影子可以吃掉整個預算"
    assert _reload(LLM_SHADOW_TIMEOUT_SEC="45") == 45, "明設的逃生門壞了"
