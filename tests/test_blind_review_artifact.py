# -*- coding: utf-8 -*-
"""**盲評卡要真的被產生出來**(r4 Codex,#2)。

十配對達標後的判讀文字明文要求「人工 A/B 盲評」,而 `blind_review_pair`
先前**沒有任何生產呼叫端**:影子的文字算完指標就被丟掉,兩份文字再也
湊不齊。也就是說帳本宣告「可以做判讀」的那一刻,判讀所需的東西已經不存在。

一個沒有呼叫端的函式不是功能,是宣稱。

## 通道是使用者的決定

卡片含**兩份完整的分析文字**,而 repo 是公開的 —— 公開 repo 的 Actions
artifact 任何人都下載得到。所以預設 `LLM_BLIND_REVIEW_SINK=local`:
只寫在 runner 的本地目錄,job 結束即消失。那是「還沒決定通道」時
唯一不會外洩的行為,而不是把它當成已經決定好的事。

這個檔同時盯住那條隱私線:卡片不得進 state(state 會 commit 進公開 repo)。
"""
import ast
from pathlib import Path

import analysis_metrics as am
import morning_report as mr

_SRC = Path(__file__).resolve().parents[1] / "morning_report.py"


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


def test_the_card_has_a_production_caller():
    """`blind_review_pair` 必須被生產程式碼呼叫,不能只存在於測試裡。

    判準是**整條鏈**:影子路徑 → 落地函式 → 葉模組的組裝 → 配對本體。
    第一版把最後一段釘在 `_write_blind_review_card` 直接呼叫
    `blind_review_pair`;為了守棘輪把組裝搬進 `analysis_metrics` 之後,
    那條就指向一個已經不存在的形狀 —— 搬個家就失效的守衛守不住東西。
    """
    assert "_write_blind_review_card" in _calls(_SRC, "_run_llm_shadow"), (
        "影子路徑沒有產生盲評卡 —— 文字算完指標就丟了,"
        "十配對達標時人工盲評無法執行")
    assert "build_card_payload" in _calls(_SRC, "_write_blind_review_card"), \
        "落地函式沒有去組裝卡片"
    assert "blind_review_pair" in _calls(
        Path(am.__file__), "build_card_payload"), "組裝沒有走到配對本體"


def test_the_card_is_written_while_both_texts_exist(tmp_path, monkeypatch):
    """行為驗證:走完影子成功路徑之後,卡片檔案要在,而且兩側都有文字。"""
    monkeypatch.setattr(mr, "BLIND_REVIEW_DIR", tmp_path / "blind_review")
    monkeypatch.setattr(mr, "LLM_EXPERIMENT_ID", "luna-vs-deepseek")
    mr._write_blind_review_card("主分析的完整文字", "影子的完整文字",
                                "2026-08-05")
    path = tmp_path / "blind_review" / "2026-08-05.json"
    assert path.exists(), "盲評卡沒有落地"
    import json
    card = json.loads(path.read_text(encoding="utf-8"))
    assert card["A"] and card["B"], "有一側是空的,盲評無從比起"
    assert {card["A"], card["B"]} == {"主分析的完整文字", "影子的完整文字"}
    assert am.blind_review_is_decodable(card), \
        "沒有解碼表 —— 評完的分數對不回模型,整天的盲評作廢"


def test_the_card_never_lands_in_state():
    """**隱私線**:state 會被 commit 進公開 repo,卡片不得落在那裡。

    判準訂在**原始碼的宣告**上,不是執行期的值 —— 第一版寫
    `mr.STATE_ROOT not in mr.BLIND_REVIEW_DIR.parents`,而 conftest 的
    autouse fixture 會把 `*_DIR` 這類路徑改指到 tmp,`STATE_ROOT` 卻留在
    `state`:於是比對變成「tmp 路徑不在 state 底下」—— 恆真。
    把宣告改成 `STATE_ROOT / "blind_review"` 之後測試照樣綠(突變當場抓到)。

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
    # 也不得被登錄成要 push 的 state 檔(這條看的是登錄表,不受 fixture 影響)
    assert not any("blind_review" in str(p) for p in mr._state_push_paths()), \
        "盲評卡被登錄進 state push"


def test_the_card_directory_is_gitignored():
    """就算不在 state 底下,也不能被 `git add -A` 掃進去。

    本 repo 有過先例:沒被登錄的檔案照樣被 `git add -A` 提交(批#71 r1)。
    """
    ignored = (Path(__file__).resolve().parents[1] / ".gitignore").read_text(
        encoding="utf-8")
    assert "artifacts/" in ignored, \
        "artifacts/ 沒有被 gitignore —— 盲評卡可能被提交進公開 repo"


def test_the_default_sink_does_not_publish():
    """預設通道必須是不外送的那個。通道是使用者的決定,不是預設值該替他做的。"""
    import llm_config
    assert llm_config.CONFIG_SOURCE_SPEC["LLM_BLIND_REVIEW_SINK"][1] == "local"


def test_the_manifest_records_existence_not_text(tmp_path, monkeypatch):
    """manifest 會進公開 state —— 只能記「卡片在不在」,不能記文字。"""
    monkeypatch.setattr(mr, "BLIND_REVIEW_DIR", tmp_path / "blind_review")
    monkeypatch.setattr(mr, "LLM_EXPERIMENT_ID", "luna-vs-deepseek")
    mr._RUN_MANIFEST.pop("llm_experiment_review", None)
    mr._write_blind_review_card("主分析的機密文字", "影子的機密文字",
                                "2026-08-05")
    rec = mr._RUN_MANIFEST.get("llm_experiment_review") or {}
    assert rec.get("decodable") is True
    blob = str(rec)
    assert "機密文字" not in blob, f"manifest 記了分析文字:{rec}"


def test_no_card_without_an_experiment(tmp_path, monkeypatch):
    """沒在跑實驗就不該產生卡片 —— 它只為配對判讀而存在。"""
    monkeypatch.setattr(mr, "BLIND_REVIEW_DIR", tmp_path / "blind_review")
    monkeypatch.setattr(mr, "LLM_EXPERIMENT_ID", "")
    mr._write_blind_review_card("主分析", "影子", "2026-08-05")
    assert not (tmp_path / "blind_review").exists()


def test_a_missing_side_produces_no_card(tmp_path, monkeypatch):
    """單邊的卡片不是盲評,是誤導。"""
    monkeypatch.setattr(mr, "BLIND_REVIEW_DIR", tmp_path / "blind_review")
    monkeypatch.setattr(mr, "LLM_EXPERIMENT_ID", "luna-vs-deepseek")
    mr._write_blind_review_card("只有主分析", "", "2026-08-05")
    assert not (tmp_path / "blind_review" / "2026-08-05.json").exists()


def test_card_failure_does_not_break_the_report(tmp_path, monkeypatch):
    """觀測用的東西壞掉不得弄壞晨報 —— 這是本 repo 的第一原則。"""
    monkeypatch.setattr(mr, "BLIND_REVIEW_DIR", tmp_path / "blind_review")
    monkeypatch.setattr(mr, "LLM_EXPERIMENT_ID", "luna-vs-deepseek")
    monkeypatch.setattr(mr, "_atomic_write_text",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("磁碟滿")))
    mr._write_blind_review_card("主分析", "影子", "2026-08-05")   # 不得拋
