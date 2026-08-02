# -*- coding: utf-8 -*-
"""**盲評卡要真的被產生、真的是盲的、真的取得回來**(r4 #2、r5 #1/#2/#3)。

十配對達標後的判讀文字明文要求「人工 A/B 盲評」。這個要求歷經三次落空:

  1. (r4 #2)`blind_review_pair` **沒有任何生產呼叫端** —— 影子的文字算完
     指標就被丟掉,兩份文字再也湊不齊。帳本宣告「可以做判讀」的那一刻,
     判讀所需的東西已經不存在。
  2. (r5 #2)卡片產生了,但**解碼表和 A/B 內容在同一個 JSON 裡** ——
     評審點開檔案第一眼就看得到哪一邊是誰,「盲評」只剩名字。
  3. (r5 #1)卡片是盲的了,但 `sink` 只被寫進 manifest,**沒有任何分派或
     搬運行為** —— 卡片只存在於 runner 上、job 結束即消失,十天後一張都
     取不回,而 manifest 看起來像是有在交付。

三次都是同一個形狀:機制存在、看起來在運作、實際產不出東西。所以這個檔的
判準全部訂在**可觀察的結果**上(檔案在不在、身分露不露、job 結束後拿不拿
得到),不是「有沒有呼叫某個函式」。

## 通道是使用者的決定

卡片含兩份完整分析文字,而本 repo 是公開的 —— 公開 repo 的 Actions artifact
任何人拿到網址都能下載。所以預設 `local`(取不回,而那個事實會被記下來),
`artifact` 要使用者明確開啟;README 寫明外洩範圍讓那個選擇是知情的。
"""
import ast
import json
from pathlib import Path

import analysis_metrics as am
import llm_experiment as lx
import morning_report as mr

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "morning_report.py"


def _calls(src: Path, name: str) -> set:
    """哪些名字**真的被呼叫**。用 AST 而不是子字串 —— 散文裡也會出現函式名。"""
    tree = ast.parse(src.read_text(encoding="utf-8"))
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == name)
    out = set()
    for sub in ast.walk(fn):
        if isinstance(sub, ast.Call):
            f = sub.func
            out.add(f.id if isinstance(f, ast.Name) else getattr(f, "attr", ""))
    return out


def _write(monkeypatch, tmp_path, *, sink="local"):
    monkeypatch.setattr(mr, "BLIND_REVIEW_DIR", tmp_path / "blind_review")
    monkeypatch.setattr(mr, "LLM_EXPERIMENT_ID", "luna-vs-deepseek")
    monkeypatch.setattr(mr, "LLM_BLIND_REVIEW_SINK", sink)
    mr._RUN_MANIFEST.pop("llm_experiment_review", None)


# ------------------------------------------------------------ 有沒有產出

def test_the_card_has_a_production_caller():
    """影子路徑必須真的去產卡 —— 這一段只有靜態關係可查。"""
    assert "_write_blind_review_card" in _calls(_SRC, "_run_llm_shadow"), (
        "影子路徑沒有產生盲評卡 —— 文字算完指標就丟了,"
        "十配對達標時人工盲評無法執行")


def test_the_production_path_reaches_the_pairing_itself(tmp_path, monkeypatch):
    """**生產路徑吐出來的,就是 `blind_review_pair` 算出來的那份。**

    落地流程用注入的方式接組裝器(`build=_am.card_files`),AST 追不過那個
    接縫 —— 而追不過就只能改用行為判準,不能改成「看起來有呼叫就算」。
    前一版把判準釘在「`_write_blind_review_card` 直接呼叫
    `blind_review_pair`」,組裝一搬家就指向不存在的形狀:
    **搬個家就失效的守衛守不住東西。**
    """
    _write(monkeypatch, tmp_path)
    mr._write_blind_review_card("主分析內容", "影子內容", "2026-08-05")
    got = json.loads((tmp_path / "blind_review" / "2026-08-05.json")
                     .read_text(encoding="utf-8"))
    want, _ = am.split_card(
        am.blind_review_pair("主分析內容", "影子內容", seed="2026-08-05"))
    # 經過 JSON 往返再比:`criteria` 在記憶體裡是 tuple,落地後是 list。
    # 比的是**內容**,不是型別。
    assert got == json.loads(json.dumps(want, ensure_ascii=False)), \
        "落地的卡片與配對本體算出來的不一致"


def test_the_card_is_written_while_both_texts_exist(tmp_path, monkeypatch):
    """行為驗證:兩份文字都在的時候,卡片與解碼表都要落地。"""
    _write(monkeypatch, tmp_path)
    assert mr._write_blind_review_card("主分析的完整文字", "影子的完整文字",
                                       "2026-08-05") is True
    card = json.loads((tmp_path / "blind_review" / "2026-08-05.json")
                      .read_text(encoding="utf-8"))
    assert {card["A"], card["B"]} == {"主分析的完整文字", "影子的完整文字"}
    key = json.loads((tmp_path / "blind_review" / "2026-08-05.key.json")
                     .read_text(encoding="utf-8"))
    assert am.blind_review_is_decodable(key), \
        "沒有解碼表 —— 評完的分數對不回模型,整天的盲評作廢"


# ------------------------------------------------------------ r5 #2:是不是盲的

def test_the_reviewer_payload_hides_the_identities(tmp_path, monkeypatch):
    """**評審會打開的那份不得含解碼表**(r5 Codex,#2)。

    先前兩者同在一個 JSON;而當時的測試要求「同一檔案 decodable」,
    反而把「身分和內容擺在一起」固化成通過條件。
    """
    _write(monkeypatch, tmp_path)
    mr._write_blind_review_card("主分析", "影子", "2026-08-05")
    card = json.loads((tmp_path / "blind_review" / "2026-08-05.json")
                      .read_text(encoding="utf-8"))
    assert am.blind_review_is_blind(card), \
        f"評審看的那份帶了解碼表,盲評名存實亡:{sorted(card)}"
    assert "primary" not in json.dumps(card) and "shadow" not in json.dumps(card)


def test_the_key_lives_in_a_separate_file(tmp_path, monkeypatch):
    """解碼表要另存 —— 分開存才談得上分開授權。"""
    _write(monkeypatch, tmp_path)
    mr._write_blind_review_card("主分析", "影子", "2026-08-05")
    assert (tmp_path / "blind_review" / "2026-08-05.key.json").exists()


# ------------------------------------------------------------ r5 #1:取不取得回來

def test_the_default_sink_is_recorded_as_unretrievable(tmp_path, monkeypatch):
    """**`local` 取不回來,那是事實,不是實作細節。**

    先前 sink 只被寫進 manifest,沒有任何 consumer:十天後一張卡都取不回,
    而 manifest 看起來像是有在交付。現在那個「拿不到」要被記成降級。
    """
    _write(monkeypatch, tmp_path, sink="local")
    saved = list(mr._DEGRADED_STEPS)
    try:
        mr._DEGRADED_STEPS.clear()
        mr._write_blind_review_card("主分析", "影子", "2026-08-05")
        rec = mr._RUN_MANIFEST.get("llm_experiment_review") or {}
        assert rec.get("retrievable_after_job") is False
        assert any(s.startswith("blind_review:not_retrievable")
                   for s in mr._DEGRADED_STEPS), mr._DEGRADED_STEPS
    finally:
        mr._DEGRADED_STEPS[:] = saved


def test_an_implemented_sink_is_recorded_as_retrievable(tmp_path, monkeypatch):
    """反向:真的有 consumer 的 sink 不該被記成取不回。"""
    _write(monkeypatch, tmp_path, sink="artifact")
    mr._write_blind_review_card("主分析", "影子", "2026-08-05")
    rec = mr._RUN_MANIFEST.get("llm_experiment_review") or {}
    assert rec.get("retrievable_after_job") is True


def test_every_retrievable_sink_has_a_workflow_consumer():
    """**宣稱取得回來的 sink,workflow 裡要有真的把它搬走的步驟。**

    這是 r5 #1 的核心:`sinks` 表自己說了不算 —— 表裡填 True 而 workflow
    沒有對應步驟,就又回到「機制存在但取不回」。
    """
    wf = (_ROOT / ".github" / "workflows" / "morning-report.yml").read_text(
        encoding="utf-8")
    for sink, retrievable in mr.BLIND_REVIEW_SINKS.items():
        if not retrievable:
            continue
        assert f"vars.LLM_BLIND_REVIEW_SINK == '{sink}'" in wf, \
            f"sink={sink} 宣稱取得回來,workflow 卻沒有對應的 consumer 步驟"
        assert "upload-artifact" in wf and "artifacts/blind_review" in wf


def test_an_unknown_sink_degrades_explicitly(tmp_path, monkeypatch):
    """認不得的 sink 要**明確降級**,不能當成 metadata 靜靜放過。"""
    _write(monkeypatch, tmp_path, sink="dropbox")
    saved = list(mr._DEGRADED_STEPS)
    try:
        mr._DEGRADED_STEPS.clear()
        assert mr._write_blind_review_card("主分析", "影子", "2026-08-05") is False
        rec = mr._RUN_MANIFEST.get("llm_experiment_review") or {}
        assert rec.get("ok") is False and "dropbox" in str(rec.get("error"))
        assert "blind_review:write_failed" in mr._DEGRADED_STEPS
    finally:
        mr._DEGRADED_STEPS[:] = saved


def test_the_readme_states_the_exposure():
    """`artifact` 在公開 repo 上等同公開 —— 使用者的選擇要是**知情**的。"""
    readme = (_ROOT / "README.md").read_text(encoding="utf-8")
    row = [ln for ln in readme.splitlines() if "LLM_BLIND_REVIEW_SINK" in ln]
    assert row, "README 沒有記載這個開關"
    assert "公開" in row[0] and "取不回" in row[0], \
        f"README 沒有寫明外洩範圍與 local 取不回:{row[0]}"


# ------------------------------------------------------------ r5 #3:失敗留不留痕

def test_a_write_failure_leaves_a_structured_trace(tmp_path, monkeypatch):
    """**寫失敗要留結構化痕跡**(r5 Codex,#3)。

    先前只印一行 stderr 就回去繼續寫帳本:那天的配對照樣入帳、卡片卻永遠
    缺席,manifest 與降級清單都看不出來。沒有痕跡的失敗等於沒發生過。
    """
    _write(monkeypatch, tmp_path)
    monkeypatch.setattr(mr, "_atomic_write_text",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("磁碟滿")))
    saved = list(mr._DEGRADED_STEPS)
    try:
        mr._DEGRADED_STEPS.clear()
        assert mr._write_blind_review_card("主分析", "影子", "2026-08-05") is False
        rec = mr._RUN_MANIFEST.get("llm_experiment_review") or {}
        assert rec.get("ok") is False, f"失敗沒有進 manifest:{rec}"
        assert "磁碟滿" in str(rec.get("error"))
        assert "blind_review:write_failed" in mr._DEGRADED_STEPS
    finally:
        mr._DEGRADED_STEPS[:] = saved


def test_a_pair_without_material_is_visible_in_the_verdict():
    """**判讀不得要求一件做不到的事。**

    達標的文字明說「仍需人工盲評」;若那幾天的卡片缺席或取不回,
    這句話就是空話 —— 進度要數得出來,判讀要說得出口。
    """
    def _row(day, review_ok):
        return lx.build_record(
            today=day, experiment_id="e",
            primary={"profile": "luna", "ok": True},
            shadow={"profile": "deepseek_legacy", "ok": True},
            evidence_sha_primary="a", evidence_sha_shadow="a",
            core_sha_primary="c", core_sha_shadow="c", review_ok=review_ok)

    ledger = [_row(f"2026-08-{d:02d}", d <= 3) for d in range(1, 11)]
    prog = lx.pair_progress(ledger, target=10)
    assert prog["comparable_pairs"] == 10 and prog["ready"] is True
    assert prog["pairs_with_review"] == 3
    text = lx.verdict(prog)
    assert "3/10" in text, f"判讀沒有說出盲評材料的缺口:{text}"
    assert "無法補做" in text


def test_a_full_house_reads_cleanly():
    """反向:材料齊全時不該掛一句多餘的警告。"""
    def _row(day):
        return lx.build_record(
            today=day, experiment_id="e",
            primary={"profile": "luna", "ok": True},
            shadow={"profile": "deepseek_legacy", "ok": True},
            evidence_sha_primary="a", evidence_sha_shadow="a",
            core_sha_primary="c", core_sha_shadow="c", review_ok=True)

    prog = lx.pair_progress([_row(f"2026-08-{d:02d}") for d in range(1, 11)],
                            target=10)
    assert "無法補做" not in lx.verdict(prog)


# ------------------------------------------------------------ 隱私線

def test_the_card_never_lands_in_state():
    """**state 會被 commit 進公開 repo,卡片不得落在那裡。**

    判準訂在**原始碼的宣告**上,不是執行期的值 —— 第一版寫
    `mr.STATE_ROOT not in mr.BLIND_REVIEW_DIR.parents`,而 conftest 的
    autouse fixture 會把 `*_DIR` 這類路徑改指到 tmp、`STATE_ROOT` 卻留在
    `state`:比對於是變成「tmp 路徑不在 state 底下」—— 恆真。
    把宣告改成由 `STATE_ROOT` 衍生之後測試照樣綠(突變當場抓到)。

    **被測試框架改寫過的值,不能拿來當隱私守衛的判準。**
    """
    tree = ast.parse(_SRC.read_text(encoding="utf-8"))
    decl = [n for n in tree.body
            if isinstance(n, ast.Assign)
            and any(getattr(t, "id", "") == "BLIND_REVIEW_DIR" for t in n.targets)]
    assert len(decl) == 1, f"BLIND_REVIEW_DIR 的宣告有 {len(decl)} 處"
    names = {n.id for n in ast.walk(decl[0].value) if isinstance(n, ast.Name)}
    assert "STATE_ROOT" not in names, (
        "盲評卡的目錄由 STATE_ROOT 衍生 —— 兩份完整分析文字會被 "
        "commit 進公開 repo")
    assert not any("blind_review" in str(p) for p in mr._state_push_paths()), \
        "盲評卡被登錄進 state push"


def test_the_card_directory_is_gitignored():
    """就算不在 state 底下,也不能被 `git add -A` 掃進去。

    本 repo 有過先例:沒被登錄的檔案照樣被 `git add -A` 提交(批#71 r1)。
    """
    ignored = (_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "artifacts/" in ignored, \
        "artifacts/ 沒有被 gitignore —— 盲評卡可能被提交進公開 repo"


def test_the_default_sink_does_not_publish():
    """預設通道必須是不外送的那個。通道是使用者的決定,不是預設值該替他做的。"""
    import llm_config
    assert llm_config.CONFIG_SOURCE_SPEC["LLM_BLIND_REVIEW_SINK"][1] == "local"
    assert mr.BLIND_REVIEW_SINKS["local"] is False


def test_the_manifest_records_existence_not_text(tmp_path, monkeypatch):
    """manifest 會進公開 state —— 只能記存在性,不能記文字,也不能記解碼表。"""
    _write(monkeypatch, tmp_path)
    mr._write_blind_review_card("主分析的機密文字", "影子的機密文字",
                                "2026-08-05")
    rec = mr._RUN_MANIFEST.get("llm_experiment_review") or {}
    assert rec.get("ok") is True
    blob = json.dumps(rec, ensure_ascii=False)
    assert "機密文字" not in blob, f"manifest 記了分析文字:{rec}"
    assert "_key" not in blob, f"manifest 記了解碼表:{rec}"


def test_no_card_without_an_experiment(tmp_path, monkeypatch):
    """沒在跑實驗就不該產生卡片 —— 它只為配對判讀而存在。"""
    _write(monkeypatch, tmp_path)
    monkeypatch.setattr(mr, "LLM_EXPERIMENT_ID", "")
    assert mr._write_blind_review_card("主分析", "影子", "2026-08-05") is False
    assert not (tmp_path / "blind_review").exists()


def test_a_missing_side_produces_no_card(tmp_path, monkeypatch):
    """單邊的卡片不是盲評,是誤導。"""
    _write(monkeypatch, tmp_path)
    assert mr._write_blind_review_card("只有主分析", "", "2026-08-05") is False
    assert not (tmp_path / "blind_review" / "2026-08-05.json").exists()


def test_card_failure_does_not_break_the_report(tmp_path, monkeypatch):
    """觀測用的東西壞掉不得弄壞晨報 —— 這是本 repo 的第一原則。"""
    _write(monkeypatch, tmp_path)
    monkeypatch.setattr(mr, "_atomic_write_text",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("磁碟滿")))
    saved = list(mr._DEGRADED_STEPS)
    try:
        mr._write_blind_review_card("主分析", "影子", "2026-08-05")   # 不得拋
    finally:
        mr._DEGRADED_STEPS[:] = saved
