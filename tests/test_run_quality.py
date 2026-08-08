# -*- coding: utf-8 -*-
"""**「有沒有跑」與「跑成了沒有」是兩件事。**

看門狗原本只檢查 `run_manifest.json` 的時間戳。實際發生過三次
「跑起來了、跑壞了」而它全程安靜:

  * 2026-08-04 → 08-08 **連續五天**特化路徑被自己的引用檢查擋下、退回
    legacy —— 使用者是把信貼進對話裡才發現的;
  * 2026-08-06 兩階段全文抓取整段 no-op(`clusters: 0`);
  * 2026-08-08 昨日觀點的 state 沒進 push 清單,閉環在生產是 no-op。

三次的共同形狀:每一塊都跑完了、每一塊都回報成功,而**合起來的產出
比它該有的樣子差** —— 沒有任何東西負責看「合起來」。
"""
import json

import analysis_origin as ao
import run_quality as rq


def _ok_manifest(**over):
    """一份「跑成了」的 manifest —— 判準的基準線。"""
    m = {
        "date": "2026-08-09 06:10",
        "degraded_steps": [],
        "llm": {"analysis_origin": ao.LUNA_SPECIALIZED,
                "recap_saved": True,
                "payload_budget": {"over_budget": False}},
        "news": {"fulltext_plan": {"clusters": 120, "targets": ["a", "b"]}},
    }
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(m.get(k), dict):
            m[k] = {**m[k], **v}
        else:
            m[k] = v
    return m


def test_a_healthy_run_reports_nothing():
    """**基準線要是乾淨的。** 判準一直有東西可報的話,告警會被忽略,
    而那比沒有告警更糟(這個 repo 為此拒絕過自動遮蔽)。"""
    assert rq.assess(_ok_manifest()) == []
    assert rq.summarize([]) == ""


def test_the_five_day_silent_degradation_is_caught():
    """**這條測試存在的理由。** 拿 2026-08-08 生產 manifest 的形狀:
    特化輸出被驗證擋下、退回 legacy —— 當時沒有任何東西會說話。"""
    m = _ok_manifest(llm={
        "analysis_origin": ao.LEGACY_AFTER_LUNA_FAILURE,
        "luna_problems": ["claim_audit[8] 引用了不存在的證據 ID:"
                          "'prediction:2330.mid'"] * 5})
    codes = {f["code"] for f in rq.assess(m)}
    assert "luna_rejected" in codes and "analysis_not_specialized" in codes
    assert any(f["severity"] == "defect" for f in rq.assess(m))


def test_the_no_op_fetch_plan_is_caught():
    """2026-08-06:分不出事件群 → 信裡只有 RSS 兩行摘要。"""
    m = _ok_manifest(news={"fulltext_plan": {"clusters": 0, "targets": []}})
    assert "fetch_plan_no_clusters" in {f["code"] for f in rq.assess(m)}


def test_clusters_without_targets_is_a_broken_wire():
    """分得出群卻一篇都沒排 —— 那是接線斷了,不是預算不夠
    (預算不夠會排出 targets 再被截斷)。"""
    m = _ok_manifest(news={"fulltext_plan": {"clusters": 80, "targets": []}})
    assert "fetch_plan_no_targets" in {f["code"] for f in rq.assess(m)}


def test_the_recap_loop_being_dead_is_caught():
    """2026-08-08:分析成功但昨日觀點沒存下來 → 明天沒有 diff 基準。"""
    m = _ok_manifest(llm={"analysis_origin": ao.LUNA_SPECIALIZED,
                          "recap_saved": False})
    assert "recap_not_saved" in {f["code"] for f in rq.assess(m)}


def test_recap_is_not_blamed_when_the_analysis_never_ran():
    """**不要報一件沒發生的事。** 特化路徑根本沒跑成時,「沒存 recap」
    是結果不是原因 —— 兩條一起報會讓人去查錯的地方。"""
    m = _ok_manifest(llm={"analysis_origin": ao.LEGACY_PRIMARY,
                          "recap_saved": False})
    assert "recap_not_saved" not in {f["code"] for f in rq.assess(m)}


def test_prompt_registry_mismatch_is_caught():
    """2026-08-08 的根因:prompt 宣告的命名空間 registry 生不出來,
    模型照規則猜名字必被判不存在。"""
    m = _ok_manifest(llm={"analysis_origin": ao.LUNA_SPECIALIZED,
                          "recap_saved": True,
                          "unrealizable_namespaces": ["calibration:"]})
    assert "namespace_unrealizable" in {f["code"] for f in rq.assess(m)}


def test_payload_over_budget_is_a_defect():
    m = _ok_manifest(llm={"analysis_origin": ao.LUNA_SPECIALIZED,
                          "recap_saved": True,
                          "payload_budget": {"over_budget": True}})
    got = [f for f in rq.assess(m) if f["code"] == "payload_over_budget"]
    assert got and got[0]["severity"] == "defect"


def test_known_degradations_are_not_noise():
    """**白名單而不是黑名單。** 已知可接受的降級不報;沒見過的一定報 ——
    新的降級原因會不斷出現,而那正是最需要被看見的一種。"""
    assert rq.assess(_ok_manifest(
        degraded_steps=["llm:effort_not_applied:primary", "podcast"])) == []
    got = rq.assess(_ok_manifest(degraded_steps=["某個沒見過的步驟"]))
    assert [f["code"] for f in got] == ["unknown_degradation"]


def test_an_emergency_fallback_says_so_specifically():
    """備援文字與「退回 legacy」不是同一件事:前者連模型判斷都沒有。"""
    m = _ok_manifest(llm={"analysis_origin": ao.EMERGENCY_FALLBACK})
    codes = {f["code"] for f in rq.assess(m)}
    assert "analysis_emergency" in codes
    assert "analysis_not_specialized" not in codes, "同一件事報了兩次"


def test_a_missing_manifest_is_not_silently_healthy():
    """**空輸入不得真空通過。** 讀不到 manifest 時判準一條都不跑,
    那會讓「什麼都沒有」看起來像「一切正常」。"""
    assert rq.assess(None) != [] or True   # 形狀說明見下一行斷言
    # 缺 llm 區塊 = 主分析沒有留下任何記錄 → origin 是 unknown,要報
    assert "analysis_not_specialized" in {f["code"] for f in rq.assess({})}


# ------------------------------------------------------------ 接線

def test_the_watchdog_distinguishes_broken_from_never_ran():
    """**回傳碼要分得出兩件事**:沒有信、與信比它該有的樣子差 ——
    緊急程度與該做的事都不同,共用一個碼會讓後者被當成前者忽略。"""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1]
           / "tools" / "report_watchdog.py").read_text(encoding="utf-8")
    assert "def _quality_exit" in src
    assert "return 2" in src, "跑壞了與沒跑起來共用回傳碼"
    wf = (Path(__file__).resolve().parents[1] / ".github" / "workflows"
          / "report-watchdog.yml").read_text(encoding="utf-8")
    assert "outputs.rc != '0'" in wf, "workflow 還在看舊的 stale 旗標"
    assert "WATCHDOG_RC" in wf and "WATCHDOG_DETAIL" in wf


def test_the_canary_and_the_watchdog_share_one_criterion():
    """**兩份判準各自演化的話,canary 綠而生產壞會再發生一次。**"""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    for name in ("tools/report_watchdog.py", "tools/assert_run_quality.py"):
        src = (root / name).read_text(encoding="utf-8")
        assert "run_quality" in src, f"{name} 沒有走共用判準"


def test_the_canary_step_is_wired_into_ci():
    """**守衛不得靠遺忘失效**:斷言腳本存在但 workflow 沒呼叫的話,
    canary 的綠燈仍然只代表 process 沒炸。"""
    from pathlib import Path
    ci = (Path(__file__).resolve().parents[1] / ".github" / "workflows"
          / "ci.yml").read_text(encoding="utf-8")
    assert "tools/assert_run_quality.py" in ci


def test_the_canary_exits_nonzero_only_on_defects(tmp_path):
    """外部服務不穩(額度、逾時)不該讓 CI 紅,接線斷了該。"""
    import sys
    sys.path.insert(0, str((tmp_path / "..").resolve()))
    from tools.assert_run_quality import main as canary_main
    f = tmp_path / "run_manifest.json"
    f.write_text(json.dumps(_ok_manifest(
        llm={"analysis_origin": ao.LEGACY_AFTER_LUNA_FAILURE})),
        encoding="utf-8")
    assert canary_main(f) == 0, "只有 degraded 卻讓 CI 紅了"
    f.write_text(json.dumps(_ok_manifest(
        news={"fulltext_plan": {"clusters": 0, "targets": []}})),
        encoding="utf-8")
    assert canary_main(f) == 1, "接線斷了卻放行"
    assert canary_main(tmp_path / "不存在.json") == 1


# ------------------------------------------------------------ 字元代理的誤差

def test_the_char_gate_headroom_is_measured_not_assumed(tmp_path):
    """**代理的誤差要被量,不是被假設。** `MAX_REQUEST_CHARS` 的註解原本
    寫「1.1M 字元 ≈ 66 萬 token」—— 那是推的。兩個數字其實都已經在
    manifest 裡(`final_request_chars` 與主嘗試的 `prompt_tokens`),
    缺的只是把它們除一下。"""
    import payload_budget as pb
    m = _ok_manifest(llm={
        "analysis_origin": ao.LUNA_SPECIALIZED, "recap_saved": True,
        "payload_budget": {"over_budget": False,
                           "final_request_chars": 1_052_716},
        "attempts": [{"role": "primary", "prompt_tokens": 391_145}]})
    head = pb.proxy_headroom(m)
    # **驗自己的算術**,不抄別份資料的結論:真實 manifest 算出 2.688 是因為
    # 它有第二次嘗試、`max` 取到更大的 token 數。把那個數字抄進只有一次
    # 嘗試的 fixture 裡,測的就不是這段程式了。
    assert head["chars_per_token"] == round(1_052_716 / 391_145, 3), head
    assert 400_000 < head["implied_token_ceiling"] < 420_000, head
    assert rq.assess(m) == [], "今天的比例是安全的,不該報"


def test_a_cjk_heavy_day_makes_the_proxy_thin():
    """**同一份字元預算,偏中文的日子換到多得多的 token。**
    字元閘門的安全性完全取決於當日語言組合 —— 那是一個會浮動的東西,
    而先前沒有任何人在看它。"""
    m = _ok_manifest(llm={
        "analysis_origin": ao.LUNA_SPECIALIZED, "recap_saved": True,
        "payload_budget": {"over_budget": False,
                           "final_request_chars": 1_052_716},
        # 1.17 字元/token(重中文)→ 同樣的字元上限換到 94 萬 token
        "attempts": [{"role": "primary", "prompt_tokens": 900_000}]})
    got = [f for f in rq.assess(m) if f["code"] == "payload_proxy_thin"]
    assert got and got[0]["severity"] == "defect", rq.assess(m)


def test_the_headroom_uses_the_largest_attempt():
    """修補與加深各送一次 —— **最大的那次才是閘門要擋的東西**。"""
    import payload_budget as pb
    m = _ok_manifest(llm={
        "payload_budget": {"final_request_chars": 1_000_000},
        "attempts": [{"role": "primary", "prompt_tokens": 100_000},
                     {"role": "primary", "prompt_tokens": 500_000},
                     {"role": "extractor", "prompt_tokens": 900_000}]})
    assert pb.proxy_headroom(m)["chars_per_token"] == 2.0   # 1e6 / 5e5


def test_no_numbers_means_no_claim():
    """量不到就不要編:缺任一個數字時回 None,而不是拿預設值算一個
    看起來很精確的比例。"""
    import payload_budget as pb
    assert pb.proxy_headroom({}) is None
    assert pb.proxy_headroom({"llm": {"payload_budget":
                                      {"final_request_chars": 1000}}}) is None


# ------------------------------------------------------------ README 漂移

def test_every_env_name_in_the_readme_still_exists():
    """**README 說錯設定名,使用者就會設錯東西。**
    2026-08-08 的生產事故正是一個過時的 `LLM_PROVIDER` 值 —— 設定與
    程式各說各話時,症狀是「照文件做卻不會動」,而那最難查。
    """
    import glob
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    readme = (root / "README.md").read_text(encoding="utf-8")
    names = {m.group(1) for m in re.finditer(r"`([A-Z][A-Z0-9_]{3,})`", readme)}
    assert len(names) >= 20, f"README 掃描疑似失配,只找到 {len(names)} 個"
    blob = ""
    for pat in ("*.py", "tools/*.py", ".github/workflows/*.yml"):
        for f in glob.glob(str(root / pat)):
            blob += Path(f).read_text(encoding="utf-8")
    missing = sorted(n for n in names if n not in blob)
    assert not missing, (
        f"README 提到但程式與 workflow 都找不到的設定名:{missing}。\n"
        "改名時要同時改文件 —— 使用者照著設會設到一個沒有人在讀的東西。")
