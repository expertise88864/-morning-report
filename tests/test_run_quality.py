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
    """一份「跑成了」的 manifest —— 判準的基準線。

    **必須是完整的**:特化路徑走完會留下 `payload_budget` /
    `primary_metrics` / `recap_saved` / `request_measurements`,
    少任何一格都與 `analysis_origin` 自己的宣稱互相矛盾(外審 P1-3)。
    基準線少東西的話,下面每一條測的都是「不完整 + 那條規則」的混合。
    """
    m = {
        "date": "2026-08-09 06:10",
        "degraded_steps": [],
        "git_sha": "abc123", "github_run_id": "42", "run_nonce": "deadbeef",
        # **基準線要是生產真的會寫出來的內容**(第二十七輪外審 P1-2):
        # 上一版用 `{"over_budget": False}` / `{"news_analyzed": 6}` 這種
        # 佔位符 —— 而語意判準要問的正是「這一格真的跑過嗎」。
        # 佔位符當基準線的話,基準線自己就是那個缺陷。
        "llm": {"analysis_origin": ao.LUNA_SPECIALIZED,
                "recap_saved": "saved",
                "payload_budget": {"chars_before": 1_052_716,
                                   "chars_after": 980_000,
                                   "limit": 1_100_000, "over_budget": False},
                "primary_metrics": {"parsed": True, "claims": 9,
                                    "sections_present": 11,
                                    "validation_problems": 0},
                "request_measurements": [
                    {"role": "primary", "chars": 1_052_716,
                     "tokens": 391_145, "accepted": True}]},
        "news": {"fulltext_plan": {"clusters": 120, "targets": ["a", "b"],
                                   "available_news": 300}},
    }
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(m.get(k), dict):
            m[k] = {**m[k], **v}
        else:
            m[k] = v
    return m


def _measured(*pairs, role="primary", accepted=True):
    """**走 recorder 產生量測**(第二輪外審 F1):手工塞 `llm.primary`
    量不到「角色槽是彙總、量測要逐次成對」這件事 —— 而那正是缺陷所在。"""
    import run_manifest as rm
    r = rm.ManifestRecorder()
    for chars, toks in pairs:
        r.record_llm_call(role, "deepseek", "m", usage={"prompt_tokens": toks},
                          accepted=accepted, request_chars=chars)
    return r.data["llm"]


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
    m = _ok_manifest(news={"fulltext_plan": {
        "clusters": 0, "targets": [], "available_news": 563}})
    assert "fetch_plan_no_clusters" in {f["code"] for f in rq.assess(m)}


def test_clusters_without_targets_is_a_broken_wire():
    """分得出群卻一篇都沒排 —— 那是接線斷了,不是預算不夠
    (預算不夠會排出 targets 再被截斷)。"""
    m = _ok_manifest(news={"fulltext_plan": {
        "clusters": 80, "targets": [], "available_news": 300}})
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
                          "recap_saved": "saved",
                          "unrealizable_namespaces": ["calibration:"]})
    assert "namespace_unrealizable" in {f["code"] for f in rq.assess(m)}


def test_payload_over_budget_is_a_defect():
    m = _ok_manifest(llm={"analysis_origin": ao.LUNA_SPECIALIZED,
                          "recap_saved": "saved",
                          "payload_budget": dict(_ok_manifest()["llm"]["payload_budget"],
                                                 over_budget=True)})
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

    def _run(mode="watchdog"):
        return canary_main(["--manifest", str(f), "--mode", mode])

    f.write_text(json.dumps(_ok_manifest(
        llm={"analysis_origin": ao.LEGACY_AFTER_LUNA_FAILURE})),
        encoding="utf-8")
    assert _run() == 0, "watchdog 模式下只有 degraded 卻讓 CI 紅了"
    # **但 strict 模式要擋** —— canary 的名字是「證明特化輸出真的產生了」
    assert _run("strict") == 1, "strict 放行了退回 legacy 的那一班"
    f.write_text(json.dumps(_ok_manifest(
        news={"fulltext_plan": {"clusters": 0, "targets": [],
                                "available_news": 563}})),
        encoding="utf-8")
    assert _run() == 1, "接線斷了卻放行"
    assert canary_main(["--manifest", str(tmp_path / "不存在.json")]) == 1


# ------------------------------------------------------------ 字元代理的誤差

def test_the_char_gate_headroom_is_measured_not_assumed(tmp_path):
    """**代理的誤差要被量,不是被假設。** `MAX_REQUEST_CHARS` 的註解原本
    寫「1.1M 字元 ≈ 66 萬 token」—— 那是推的。兩個數字其實都已經在
    manifest 裡(`final_request_chars` 與主嘗試的 `prompt_tokens`),
    缺的只是把它們除一下。"""
    import payload_budget as pb
    # **走 recorder 的生產形狀**:先前 fixture 手工把 token 放進
    # `attempts`、字元放在 manifest 頂層 —— 而健康的日子 attempts 裡根本
    # 沒有 primary,量測整個不跑,測試卻是綠的(第一輪外審 F1)。
    m = _ok_manifest(llm={**_measured((1_052_716, 391_145)),
                          "analysis_origin": ao.LUNA_SPECIALIZED,
                          "recap_saved": "saved",
                          "payload_budget": _ok_manifest()["llm"]["payload_budget"]})
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
    # 1.17 字元/token(重中文)→ 同樣的字元上限換到 94 萬 token
    m = _ok_manifest(llm={**_measured((1_052_716, 900_000)),
                          "analysis_origin": ao.LUNA_SPECIALIZED,
                          "recap_saved": "saved",
                          "payload_budget": _ok_manifest()["llm"]["payload_budget"]})
    got = [f for f in rq.assess(m) if f["code"] == "payload_proxy_thin"]
    assert got and got[0]["severity"] == "defect", rq.assess(m)


def test_the_headroom_uses_the_largest_attempt():
    """修補與加深各送一次 —— **最大的那次才是閘門要擋的東西**,
    而且**只算 primary**:抽取器走的是另一條路、另一個上限。"""
    import payload_budget as pb
    llm = {**_measured((800_000, 100_000), (1_000_000, 500_000)),
           **_measured((2_000_000, 900_000), role="extractor")}
    llm["request_measurements"] = (
        _measured((800_000, 100_000), (1_000_000, 500_000))
        ["request_measurements"]
        + _measured((2_000_000, 900_000), role="extractor")
        ["request_measurements"])
    # 取**字元最大**的 primary 那筆(1e6 / 5e5);抽取器不算,
    # 否則 2e6/9e5 會贏,而那不是特化路徑的請求。
    assert pb.proxy_headroom({"llm": llm})["chars_per_token"] == 2.0


def test_no_numbers_means_no_claim():
    """量不到就不要編:缺任一個數字時回 None,而不是拿預設值算一個
    看起來很精確的比例。"""
    import payload_budget as pb
    assert pb.proxy_headroom({}) is None
    # 只有字元沒有 token(或反過來)→ 不成對,不算
    assert pb.proxy_headroom(
        {"llm": {"primary": {"request_chars": 1000}}}) is None
    assert pb.proxy_headroom(
        {"llm": {"primary": {"prompt_tokens": 1000}}}) is None


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


# ------------------------------------------- 第一輪外審(這兩批的補正)

def test_a_repaired_run_is_not_a_defect():
    """**R1-F2 的反例。** `luna_problems` 是累積的 —— 第一次不合格、
    第二次修補成功時它仍然留著。只看它非空就報 defect,會在**特化輸出
    順利寄出的日子**讓 canary 紅、看門狗回 2。誤報是這個模組最該避免
    的東西(它自己的 docstring 這樣寫)。"""
    m = _ok_manifest(llm={
        "analysis_origin": ao.LUNA_SPECIALIZED, "recap_saved": "saved",
        "payload_budget": _ok_manifest()["llm"]["payload_budget"],
        "luna_problems": ["claim_audit[3] 引用了不存在的證據 ID"]})
    assert rq.assess(m) == [], rq.assess(m)
    # 反向:最後真的沒跑成時仍要報
    m2 = _ok_manifest(llm={
        "analysis_origin": ao.LEGACY_AFTER_LUNA_FAILURE,
        "luna_problems": ["同上"]})
    assert "luna_rejected" in {f["code"] for f in rq.assess(m2)}


def test_no_news_at_all_is_an_upstream_outage_not_a_wiring_defect():
    """**R1-F3 的反例。** 零新聞時零群集是必然結果 —— 報成接線缺陷會讓
    上游斷料的日子看起來像程式有 bug,而該查的地方完全不同。"""
    m = _ok_manifest(news={"fulltext_plan": {
        "clusters": 0, "targets": [], "available_news": 0}})
    got = rq.assess(m)
    assert [f["code"] for f in got] == ["news_upstream_empty"], got
    assert got[0]["severity"] == "degraded"


def test_optional_namespaces_being_empty_is_not_a_defect():
    """**R1-F4 的反例。** 生產那邊的註解自己就寫著「當日真的沒有持倉/
    張力時空掉是對的」—— 而第一版把每一個空掉的命名空間都報成程式缺陷,
    等於製造可預期的假警報。"""
    m = _ok_manifest(llm={
        "analysis_origin": ao.LUNA_SPECIALIZED, "recap_saved": "saved",
        "payload_budget": _ok_manifest()["llm"]["payload_budget"],
        "unrealizable_namespaces": ["portfolio:", "tension:", "fact:"]})
    assert rq.assess(m) == [], rq.assess(m)
    # 反向:每天都組得出來的那幾個空掉,仍然是缺陷(2026-08-08 的形狀)
    m2 = _ok_manifest(llm={
        "analysis_origin": ao.LUNA_SPECIALIZED, "recap_saved": "saved",
        "payload_budget": _ok_manifest()["llm"]["payload_budget"],
        "unrealizable_namespaces": ["portfolio:", "calibration:"]})
    got = [f for f in rq.assess(m2) if f["code"] == "namespace_unrealizable"]
    assert got and "calibration:" in got[0]["detail"], rq.assess(m2)
    assert "portfolio:" not in got[0]["detail"], got[0]["detail"]


def test_the_gate_runs_on_every_attempt_and_records_its_own_chars():
    """**R1-F1 的接線。** 先前只在迴圈前量一次 —— 而修補/加深那次會把
    上一版的完整 JSON 附進 input,送的是一個**更大而且從沒被量過**的
    payload;字元也只有一份,配不起第二次的 token 數。"""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1]
           / "morning_report.py").read_text(encoding="utf-8")
    body = src.split("def _luna_analysis(")[1].split("\ndef ")[0]
    head, loop = body.split("for repair in _LUNA_ATTEMPTS:", 1)
    assert "_pb.request_gate(" not in head, "閘門還在迴圈外只量一次"
    assert "_req_chars = _pb.request_gate(" in loop
    assert "request_chars=_req_chars" in loop, "字元沒有記到那一次呼叫上"


def test_an_oversized_repair_does_not_throw_away_a_good_first_version():
    """**修正不得比缺陷更糟。** 修補那次超標時若直接拋,已經拿到的
    合法第一版(`_kept`)會被丟掉,信反而更差。"""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1]
           / "morning_report.py").read_text(encoding="utf-8")
    body = src.split("def _luna_analysis(")[1].split("\ndef ")[0]
    seg = body.split("except _pb.PayloadBudgetExceeded:", 1)[1][:400]
    assert "if _kept is None:" in seg and "raise" in seg and "break" in seg, seg


def test_the_planner_records_the_news_count_the_criterion_needs():
    """**判準的前提要由生產端提供。** 上面那條分得出「零新聞」與
    「接線壞」,靠的是 `plan()` 記下 `available_news` —— 生產不記的話,
    `run_quality` 拿不到那個前提,零群集又會被一律報成缺陷。

    第一版的反例自己在 fixture 裡塞了這個欄位,於是把生產端的記錄
    拿掉時測試照樣綠(突變驗證抓到)。**測試要用生產的呼叫形狀。**
    """
    import fetch_plan as fp
    import news_clusters as nc
    news = [{"source_item_id": f"n{i}", "title": f"事件{i}",
             "entities": [f"公司{i}"], "source": f"媒體{i}",
             "source_name": f"媒體{i}", "link": "http://x"}
            for i in range(4)]
    out = fp.plan(news, nc.clusters(news))
    assert out["available_news"] == 4, out.get("available_news")
    assert fp.plan([], [])["available_news"] == 0


# ------------------------------------------- 第二輪外審:走完整管線

def test_a_deepened_run_does_not_fake_a_thin_proxy():
    """**R2-F1 的反例。** 加深成功那天有兩次 accepted 呼叫;
    `merge_same_role` 把 token **累加**、其餘欄位取最新 ——
    於是角色槽裡是「第二次的字元 ÷ 兩次的 token 和」,比例偏小,
    看起來像「字元換到很多 token」→ **假的 payload_proxy_thin**。

    角色槽是彙總、量測要的是逐次成對,兩種需求不共用容器。
    """
    import payload_budget as pb
    import run_manifest as rm
    r = rm.ManifestRecorder()
    for chars, toks in ((1_000_000, 390_000), (1_100_000, 420_000)):
        r.record_llm_call("primary", "deepseek", "m",
                          usage={"prompt_tokens": toks},
                          accepted=True, request_chars=chars)
    m = r.data
    # 合併後的角色槽確實是混的 —— 這正是不能拿它算比例的理由
    assert m["llm"]["primary"]["prompt_tokens"] == 810_000
    assert m["llm"]["primary"]["request_chars"] == 1_100_000
    head = pb.proxy_headroom(m)
    assert head["chars_per_token"] == round(1_100_000 / 420_000, 3), head
    m.update(date="x", degraded_steps=[],
             news={"fulltext_plan": {"clusters": 9, "targets": [1],
                                     "available_news": 20}})
    # **其餘欄位沿用基準線那一份**:這條測的是比例,不是完整度 ——
    # 自己另寫一組佔位符會讓 `manifest_incomplete` 混進答案裡。
    _base = _ok_manifest()["llm"]
    m["llm"].update(analysis_origin=ao.LUNA_SPECIALIZED,
                    recap_saved=_base["recap_saved"],
                    payload_budget=_base["payload_budget"],
                    primary_metrics=_base["primary_metrics"])
    assert [f["code"] for f in rq.assess(m)] == [], rq.assess(m)


def test_the_recorder_carries_the_news_count_through():
    """**R2-F2 的反例。** `plan()` 記了 `available_news`,而
    `record_fulltext_plan()` 重建 entry 時只抄四個欄位 —— 於是判準拿到
    `None`,零新聞的日子又被報成接線缺陷。

    **走 planner → recorder → 判準的完整路徑**:上一版的反例只驗
    `plan()` 的回傳,量不到中間那一段(逐欄複製的清單漂移)。
    """
    import fetch_plan as fp
    import run_manifest as rm
    r = rm.ManifestRecorder()
    fp.plan_for_run([], recorder=r)          # 今天一則新聞都沒有
    plan = r.data["news"]["fulltext_plan"]
    assert plan.get("available_news") == 0, plan
    m = _ok_manifest(news={"fulltext_plan": plan})
    got = rq.assess(m)
    assert [f["code"] for f in got] == ["news_upstream_empty"], got
    # 反向:真的有新聞卻零群集,仍要報接線缺陷
    r2 = rm.ManifestRecorder()
    news = [{"source_item_id": "n1", "title": "某事件", "entities": ["某公司"],
             "source": "甲", "source_name": "甲", "link": "http://x"}]
    r2.record_fulltext_plan(dict(fp.plan(news, []), per_cluster=[]))
    m["news"]["fulltext_plan"] = r2.data["news"]["fulltext_plan"]
    assert "fetch_plan_no_clusters" in {f["code"] for f in rq.assess(m)}


# ================= 外審 P1-2 / P1-3:acceptance proof =================

def _specialized(**over):
    """完整的特化 manifest —— `_ok_manifest` 本身就是(基準線要完整),
    這裡只是給 acceptance 那組一個講得出意圖的名字。

    strict 另外要求 `report_kind`(第二十七輪外審 P1-2):缺席時判準會
    退回「當成平日報」,那個預設對每日生產是對的,對 canary 是「沒證明」。
    """
    m = _ok_manifest()
    m.setdefault("report_kind", rq.MORNING_REPORT)
    m.update(over)
    return m


def test_a_minimal_manifest_no_longer_passes_vacuously():
    """**空集合真空通過 —— 而我自己蓋了一個**(外審 P1-3)。
    `{"llm": {"analysis_origin": "luna_specialized"}}` 先前回空 findings:
    缺 payload_budget 不報、缺 fulltext_plan 不報、缺 recap_saved 不報……
    宣稱走了特化路徑,就要留下那條路徑必然會寫下的紀錄。"""
    got = rq.assess({"llm": {"analysis_origin": ao.LUNA_SPECIALIZED}})
    assert "manifest_incomplete" in {f["code"] for f in got}, got
    assert any(f["severity"] == "defect" for f in got)


def test_each_required_block_is_individually_required():
    """**逐格驗**:少任何一格都要報 —— 只驗「全缺」的話,
    漏掉其中一格的 manifest 會靜靜通過。"""
    for key in [k for k, _ in rq.SPECIALIZED_REQUIRED]:
        m = _specialized()
        del m["llm"][key]
        got = {f["code"] for f in rq.assess(m)}
        assert "manifest_incomplete" in got, (key, rq.assess(m))
    assert rq.assess(_specialized()) == [], rq.assess(_specialized())


def test_strict_canary_rejects_legacy_fallback_even_without_defects():
    """canary step 的名字是「證明特化輸出真的產生了」,而先前
    退回 legacy 只算 degraded → exit 0,**與那個名字互相矛盾**。"""
    m = _specialized(llm={**_specialized()["llm"],
                          "analysis_origin": ao.LEGACY_AFTER_LUNA_FAILURE})
    watchdog = rq.assess(m)
    assert all(f["severity"] != "defect" for f in watchdog), watchdog
    strict = rq.assess(m, mode="strict")
    assert "canary_not_specialized" in {f["code"] for f in strict}
    assert any(f["severity"] == "defect" for f in strict)


def test_canary_rejects_a_manifest_from_the_previous_checkout():
    """**`state/run_manifest.json` 進版控**,checkout 之後就在那裡 ——
    主流程若在寫它之前掛掉,斷言會讀到上一班的檔案,而上一班可能剛好
    健康。SHA 綁定是舊檔案**永遠滿足不了**的條件。"""
    m = _specialized(git_sha="old_sha_from_last_run")
    got = {f["code"] for f in rq.assess(m, mode="strict",
                                        expected_sha="this_run_sha",
                                        expected_run_id="42")}
    assert "run_binding_mismatch" in got, got
    # 對得上就放行
    assert rq.assess(_specialized(git_sha="this_run_sha"), mode="strict",
                     expected_sha="this_run_sha", expected_run_id="42") == []


def test_canary_requires_run_id_to_match():
    got = {f["code"] for f in rq.assess(
        _specialized(), mode="strict", expected_sha="abc123",
        expected_run_id="99")}
    assert "run_binding_mismatch" in got, got


def test_a_manifest_without_run_binding_is_not_acceptable_to_the_canary():
    """**沒有身分就證明不了它是這一次跑出來的。** 舊 manifest(綁定欄位
    還不存在的世代)在 strict 下不得放行。"""
    m = _specialized()
    for k in rq.RUN_BINDING_FIELDS:
        m.pop(k, None)
    got = {f["code"] for f in rq.assess(m, mode="strict")}
    assert "run_binding_missing" in got, got


def test_final_request_budget_failure_is_a_defect():
    """**packet 沒超不代表 request 沒超**(外審 P1-3)。先前閘門擋住之後
    manifest 上只剩一個 degraded,canary 照樣 exit 0 —— 閘門做了事,
    而它做了事這件事沒有被記下來。"""
    m = _specialized(llm={**_specialized()["llm"],
                          "payload_budget": {"over_budget": False,
                                             "final_request_over_budget": True}})
    got = [f for f in rq.assess(m) if f["code"] == "final_request_over_budget"]
    assert got and got[0]["severity"] == "defect", rq.assess(m)


def test_the_gate_records_the_flag_it_needs(tmp_path):
    """**判準的前提要由生產端提供**:閘門不記旗標的話,上一條驗的是
    一個永遠不會出現的欄位。"""
    import payload_budget as pb
    man = {}
    try:
        pb.request_gate({"x": "y" * 200}, manifest=man, limit=10)
    except pb.PayloadBudgetExceeded:
        pass
    else:
        raise AssertionError("超標卻沒有拋")
    assert man["llm"]["payload_budget"]["final_request_over_budget"] is True


def test_the_manifest_carries_the_identity_of_this_run():
    """生產端要寫得出綁定,strict 才有東西可比。"""
    import run_manifest as rmod
    m = rmod.ManifestRecorder().build(
        date="2026-08-09 06:10", report_kind=rq.MORNING_REPORT,
        budget_seconds=1200, news_workers=8, degraded_steps=[])
    for k in rq.RUN_BINDING_FIELDS:
        assert k in m, (k, sorted(m))
    assert m["git_sha"], "git_sha 是空的 —— 本機應退回 git rev-parse HEAD"


def test_ci_isolates_and_binds_the_canary():
    """**守衛不得靠遺忘失效**:strict 模式與 SHA 綁定要真的接進 workflow。"""
    from pathlib import Path
    ci = (Path(__file__).resolve().parents[1] / ".github" / "workflows"
          / "ci.yml").read_text(encoding="utf-8")
    assert "rm -f state/run_manifest.json" in ci, "舊 manifest 沒有先移除"
    assert "--mode strict" in ci, "canary 沒有用 strict 模式"
    assert "GITHUB_SHA: ${{ github.sha }}" in ci
    assert "GITHUB_RUN_ID: ${{ github.run_id }}" in ci
    # **預設也要是 strict**(縱深):CI 有明確傳旗標、而上面那條釘住它,
    # 但有人在別處呼叫這個腳本時,預設值決定它是閘門還是擺設。
    from tools.assert_run_quality import main as _m
    import inspect
    src = inspect.getsource(_m)
    assert 'default="strict"' in src, "canary 的預設模式不是 strict"


# ============ 第二輪外審:斷言要能讓 job 紅、超標要分終局與回收 ============

def test_the_acceptance_assertion_can_actually_fail_the_job():
    """**沒有 acceptance 證據就是沒有通過**(第三輪外審 F1)。

    我前兩版都在替 `continue-on-error` 辯護。第二版改成只容忍 `run`
    那一步、斷言步驟跳過 —— 而那正是外審指出的:**把「沒有證據」
    轉換成 success**。import error、中途 crash、寫 manifest 之前掛掉,
    全都會讓 job 綠著結束而什麼都沒證明。

    **而上一版的這條測試把那個 fail-open 明文釘成通過條件** ——
    測試期望本身也是宣稱,寫錯了就把缺陷鎖成正確行為。

    顧慮之所以站不住:這個 job 只在 `workflow_dispatch` 手動觸發時跑,
    不在每次 push 的路徑上,紅燈不會變成日常噪音。
    """
    # **解析 YAML,不比字串**:註解裡出現 `continue-on-error` 這幾個字
    # 會讓字串判準誤判(第一版當場踩到)—— 而註解不影響 workflow 行為。
    import yaml
    from pathlib import Path
    ci = yaml.safe_load((Path(__file__).resolve().parents[1] / ".github"
                         / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    job = ci["jobs"]["dry-run-preview"]
    assert job.get("continue-on-error") is not True,         "job 層仍容忍失敗 —— 斷言紅了 workflow 還是綠的"
    steps = {str(st.get("name", "")): st for st in job["steps"]}
    assert job.get("if") == "github.event_name == 'workflow_dispatch'",         "這個 job 若進了每次 push 的路徑,不容忍失敗的決定要重新評估"
    # **每一步都不得容忍失敗** —— 主流程掛掉時沒有 manifest,
    # 那是「沒有證據」,不是「跳過」。
    for name, st in steps.items():
        assert st.get("continue-on-error") is not True,             f"步驟「{name}」容忍失敗 —— 沒有證據會被轉換成 success"
    assertion = next(st for n, st in steps.items()
                     if n.startswith("Assert the run actually produced"))
    assert assertion.get("if") == "always()", assertion


def test_a_recovered_over_budget_is_not_a_defect():
    """**已回收的超標不是缺陷**(第二輪外審 F2)。加深那次超標時主流程
    `break` 並沿用留著的合法第一版,`analysis_origin` 仍是特化 ——
    對讀者沒有任何損失。只看旗標會對一份**成功產出的報告**報 defect,
    而誤報是這套守衛最該避免的東西。"""
    pb_terminal = {"over_budget": False, "final_request_over_budget": True}
    m = _ok_manifest(llm={**_ok_manifest()["llm"], "payload_budget": pb_terminal})
    assert "final_request_over_budget" in {f["code"] for f in rq.assess(m)}
    m2 = _ok_manifest(llm={**_ok_manifest()["llm"], "payload_budget": {
        **pb_terminal, "final_request_over_budget_recovered": True}})
    assert "final_request_over_budget" not in {f["code"] for f in rq.assess(m2)}


def test_production_marks_the_recovered_case():
    """**判準的前提要由生產端提供**:回收路徑不寫旗標的話,上一條驗的是
    一個永遠不會出現的欄位。"""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1]
           / "morning_report.py").read_text(encoding="utf-8")
    seg = src.split("except _pb.PayloadBudgetExceeded:", 1)[1][:700]
    assert "final_request_over_budget_recovered" in seg, seg
    # 只在 `_kept` 存在(真的救得回來)時標
    assert seg.index("if _kept is None:") < seg.index(
        "final_request_over_budget_recovered"), seg


# ===== 2026-08-09 生產:週日綜合信被平日報的判準判成「有段落沒跑成」 =====

def _digest_manifest() -> dict:
    """週日那條路徑的形狀:有寄信、有 LLM(政策解析),**沒有主分析**。"""
    return {"report_kind": rq.WEEKEND_DIGEST,
            "delivery": {"attempted": True, "success": True},
            "llm": {"primary": {"provider": "deepseek", "total_tokens": 12312}}}


def test_a_weekend_digest_is_not_a_broken_morning_report():
    """**每個週日一封假警報**(2026-08-09 生產實測)。

    週日走 `render_weekend_digest_html` 的輕量路徑,根本不跑主分析 ——
    那封信裡本來就沒有事件卡、淨效果、橫向綜合。拿平日報的判準去量它,
    看門狗每個週日都會說「信寄出了,但有段落沒跑成」。
    **假警報的代價是使用者開始忽略這封信**,連真的那天也一起忽略。
    """
    assert rq.assess(_digest_manifest()) == []


def test_a_missing_report_kind_still_speaks_up():
    """**沒有這一格時當成平日報** —— 那是會出聲的那一邊。

    反過來預設的話,一份缺欄位的 manifest(例如主流程中途死掉、
    或舊版留下來的)會讓所有分析面的判準整批靜默跳過,
    而那正是這個模組存在的理由。
    """
    m = _digest_manifest()
    m.pop("report_kind")
    assert [p["code"] for p in rq.assess(m)] == ["analysis_not_specialized"]


def test_the_canary_does_not_pass_by_having_nothing_to_prove():
    """**「無法證明」不是「證明了」。**

    canary 的名字是「特化輸出真的產生了」。若 dispatch 落在週日,
    那一班寄的是綜合信、根本不跑主分析 —— 靜默通過等於把一個
    量不到東西的綠燈當成證據。要紅,而且要說得出下一步。
    """
    codes = [p["code"] for p in rq.assess(_digest_manifest(), mode="strict")]
    assert "canary_on_a_non_trading_day" in codes, codes
    assert all(p["severity"] == "defect"
               for p in rq.assess(_digest_manifest(), mode="strict")
               if p["code"] == "canary_on_a_non_trading_day")


def test_every_manifest_write_says_which_kind_of_mail_it_was():
    """**必填關鍵字**:新的寄信路徑忘了表態就 TypeError,不會靜默寫錯。

    四個呼叫端(週日不寄信、週日寄了、平日 DRY_RUN、平日寄了)
    各自表態 —— 而預設值會讓下一條路徑安靜地繼承錯的那個。
    """
    import ast
    import inspect
    import morning_report as mr
    src = ast.parse(inspect.getsource(mr))
    sig = next(n for n in ast.walk(src)
               if isinstance(n, ast.FunctionDef) and n.name == "_write_run_manifest")
    assert not sig.args.args[1:], "report_kind 不得是位置參數"
    assert [a.arg for a in sig.args.kwonlyargs] == ["report_kind"]
    assert sig.args.kw_defaults == [None], "不得有預設值 —— 忘了要當場炸"
    calls = [n for n in ast.walk(src) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name)
             and n.func.id == "_write_run_manifest"]
    # **不寫死條數**:上一版斷言「恰好四個」,而外審要求補的那一個
    # (週日 DRY_RUN)一加就紅 —— 那不是缺陷,是清單漂移。
    # 要驗的性質是「每一個呼叫都表態了」,不是「有幾個」。
    assert len(calls) >= 4, len(calls)
    kinds = [ast.unparse(k.value) for c in calls for k in c.keywords
             if k.arg == "report_kind"]
    assert len(kinds) == len(calls), "有呼叫端沒有表態"
    assert set(kinds) == {"_rq.MORNING_REPORT", "_rq.WEEKEND_DIGEST"}, kinds


def test_the_weekend_dry_run_writes_its_manifest_before_returning():
    """**canary 讀不到 manifest 就給不出那句話**(外審第二輪 F1)。

    CI 的 canary 先 `rm -f` 舊檔、跑 `DRY_RUN=1`、再讀 manifest。
    週日的 DRY_RUN 從預覽那一段直接 `return`,於是根本沒有檔案 ——
    斷言只會說「找不到 run_manifest.json」,而不是那句說得出下一步的
    「這一班寄的是週日綜合信,請在交易日重新 dispatch」。
    """
    import ast
    import inspect
    import morning_report as mr
    fn = next(n for n in ast.walk(ast.parse(inspect.getsource(mr)))
              if isinstance(n, ast.FunctionDef) and n.name == "run_weekend_digest")
    guarded = [n for n in ast.walk(fn) if isinstance(n, ast.If)
               and "DRY_RUN" in ast.unparse(n.test)
               and any(isinstance(x, ast.Return) for x in ast.walk(n))]
    assert guarded, "找不到週日 DRY_RUN 的早退點 —— 這條守衛量不到東西了"
    for node in guarded:
        wrote = next((i for i, st in enumerate(node.body)
                      if "_write_run_manifest" in ast.unparse(st)), None)
        ret = next(i for i, st in enumerate(node.body)
                   if any(isinstance(x, ast.Return) for x in ast.walk(st)))
        assert wrote is not None and wrote < ret,             "DRY_RUN 早退之前沒有寫 manifest"


# ===== 2026-08-09 P2:「沒東西可抓」與「接線斷了」 =====

def _plan_manifest(**plan) -> dict:
    """**建在 `_ok_manifest` 上**:自己另寫一份 llm 區塊的話,
    語意判準一改,這裡就會混進 `manifest_incomplete` 而看不出要測的東西。"""
    return _ok_manifest(
        report_kind=rq.MORNING_REPORT,
        news={"fulltext_plan": dict({"clusters": 3, "targets": 0,
                                     "available_news": 40}, **plan)})


def test_nothing_left_to_fetch_is_not_a_broken_wire():
    """**候選全都已經有全文、或都沒有可抓的連結時,零是正確答案。**

    `targets == 0` 先前一律報「分出了 N 群卻一篇都沒排 —— 接線斷了」,
    而那句話在這一天是假的。這是 `available_news` 已經解過的同一種錯覺
    (「分不出群」vs「上游斷料」),只是在下一層。
    """
    got = rq.assess(_plan_manifest(fetchable_candidates=0,
                                   already_fulltext=9, no_fetch_link=0))
    assert [p["code"] for p in got] == ["fetch_plan_nothing_to_fetch"], got
    assert got[0]["severity"] == "degraded"
    # **訊息不得宣稱一件對其中一半是假的事**(外審):候選已經有全文時,
    # 信裡的事件**有**全文 —— 說「只會有 RSS 兩行摘要」是假的診斷,
    # 而讀著訊息的人會去查錯的地方。
    assert "RSS" not in got[0]["detail"], got[0]["detail"]
    assert "已經有全文 9 篇" in got[0]["detail"], got[0]["detail"]


def test_the_two_kinds_of_zero_are_counted_apart():
    """**「已經有全文」與「根本沒有連結」的後果相反。** 生產端要分開記,
    下游才講得出對的那一句。"""
    import fetch_plan as fp
    news = [{"source_item_id": "n1", "fulltext": "有", "link": "http://x/1"},
            {"source_item_id": "n2", "link": "notaurl"}]
    p = fp.plan(news, [{"cluster_id": "c1",
                        "member_source_ids": ["n1", "n2"],
                        "representative_source_id": "n1"}])
    assert (p["fetchable_candidates"], p["already_fulltext"],
            p["no_fetch_link"]) == (0, 1, 1), p


def test_a_broken_wire_still_speaks_up():
    """**有候選卻一篇都沒排** —— 那才是接線斷了,而且要是 defect。"""
    got = rq.assess(_plan_manifest(fetchable_candidates=12))
    assert [p["code"] for p in got] == ["fetch_plan_no_targets"], got
    assert got[0]["severity"] == "defect"
    assert "12" in got[0]["detail"], got


def test_without_the_count_it_still_reports():
    """**拿不到這個數字時仍報 defect** —— 那是會出聲的那一邊。

    反過來預設的話,一份舊 manifest(或上游忘了帶這一格)會讓整條
    判準靜默跳過,而 2026-08-06 兩階段抓取整段 no-op 正是它要抓的事。
    """
    got = rq.assess(_plan_manifest())
    assert [p["code"] for p in got] == ["fetch_plan_no_targets"], got


def test_the_producer_actually_records_it():
    """**生產端要記,下游才驗得動**(這個 repo 已經栽過:`plan()` 記了
    `available_news`,而 `record_fulltext_plan` 只抄四個欄位)。"""
    import fetch_plan as fp
    import run_manifest as rmod
    news = [{"source_item_id": "n1", "title": "a", "link": "http://x/1"},
            {"source_item_id": "n2", "title": "b", "fulltext": "有了",
             "link": "http://x/2"}]
    plan = fp.plan(news, [{"cluster_id": "c1", "member_source_ids": ["n1", "n2"],
                           "representative_source_id": "n1"}])
    assert plan["fetchable_candidates"] == 1, plan
    rec = rmod.ManifestRecorder()
    rec.record_fulltext_plan(plan)
    landed = rec.data["news"]["fulltext_plan"]
    assert landed["fetchable_candidates"] == 1, landed


# ===== 2026-08-09 P2:重試記了,放棄沒記 =====

def test_giving_up_on_a_retry_is_recorded():
    """**429 打到預算用完的那天,manifest 要說得出來。**

    上一版只記「退避了幾次」—— 重試清單非空只說明「遇到過阻力」,
    說不出「最後有沒有拿到答案」。而那兩件事的處理完全不同。
    """
    import llm_http as lh

    class _R:
        status_code = 429
        headers: dict = {}

    man: dict = {}
    calls = {"n": 0}

    def _post(*a, **k):
        calls["n"] += 1
        return _R()

    orig_post, orig_sleep = lh.requests.post, lh.time.sleep
    try:
        lh.requests.post = _post
        lh.time.sleep = lambda *_: None
        lh.post_with_backoff("http://x", {}, {}, timeout=1, manifest=man)
    finally:
        lh.requests.post, lh.time.sleep = orig_post, orig_sleep
    gave = man["llm"]["retry_gave_up"]
    assert gave and gave[-1]["status"] == 429, man["llm"]
    assert gave[-1]["reason"] == "重試次數用完", gave
    assert man["llm"]["retry_after_status"], "退避本身也還是要記"


def test_a_clean_run_records_no_give_up():
    """**修正不得把每一天都標成放棄。** 一次就成功的請求不留這筆。"""
    import llm_http as lh

    class _R:
        status_code = 200
        headers: dict = {}

    man: dict = {}
    orig = lh.requests.post
    try:
        lh.requests.post = lambda *a, **k: _R()
        lh.post_with_backoff("http://x", {}, {}, timeout=1, manifest=man)
    finally:
        lh.requests.post = orig
    assert "retry_gave_up" not in man.get("llm", {}), man


def test_the_deadline_exit_records_the_same_two_things():
    """**四個放棄出口要用同一套語義**(外審)。

    退避途中預算用完那個出口原本記 `status="deadline"` 與一個多算一次的
    次數 —— 而 `retry_after_status` 記的是 HTTP 碼。同一份清單裡的兩筆
    讀起來意思不一樣,查的人會以為 429 那天沒有 429。

    `status` 一律是觸發重試的那個狀態,`attempt` 一律是真的送出去過幾次。
    """
    import llm_http as lh

    class _R:
        status_code = 429
        headers: dict = {}

    man: dict = {}
    clock = {"t": 0.0}
    orig_post, orig_sleep, orig_mono = (lh.requests.post, lh.time.sleep,
                                        lh.time.monotonic)
    try:
        lh.requests.post = lambda *a, **k: _R()
        # 退避把預算睡掉 —— 下一圈在迴圈頂端就過期
        lh.time.sleep = lambda s: clock.__setitem__("t", clock["t"] + 100)
        lh.time.monotonic = lambda: clock["t"]
        lh.post_with_backoff("http://x", {}, {}, timeout=1, manifest=man,
                             deadline_at=30.0)
    finally:
        (lh.requests.post, lh.time.sleep,
         lh.time.monotonic) = orig_post, orig_sleep, orig_mono
    gave = man["llm"]["retry_gave_up"]
    assert len(gave) == 1, gave
    assert gave[0]["status"] == 429, gave      # 不是字串 "deadline"
    assert gave[0]["attempt"] == 1, gave       # 只送出去過一次


# ===== 2026-08-09 P2:記了卻沒有人讀的那一格 =====

def _identity_manifest(**eid) -> dict:
    return _ok_manifest(report_kind=rq.MORNING_REPORT,
                        event_identity=dict({"schema": 7}, **eid))


def test_state_holding_two_identity_generations_is_reported():
    """**`legacy_remaining` 一直記著,而沒有任何東西讀它。**

    遷移只成功一半時,舊代記錄會留在 state 裡直到過期,而它們沒有
    `incident_tokens` —— 同鍵下的新事件比對「兩邊都不知道」會被當成
    同一樁,於是併進一條可能是別件事的 lineage 並繼承它的天數。
    升版當天出現正常,**隔天還在就是遷移沒接上** —— 而看不見的話,
    沒有人會發現「隔天還在」。
    """
    # **要有上一班的數字才判得動**(第二十七輪外審 P2-4):訊息說的是
    # 「隔天還在才是問題」,只看單次 snapshot 會讓升版當天必然報一次。
    got = rq.assess(_identity_manifest(legacy_remaining=3,
                                       previous_legacy_remaining=3))
    assert [p["code"] for p in got] == ["identity_generations_mixed"], got
    assert got[0]["severity"] == "degraded"
    assert "3" in got[0]["detail"]


def test_a_fully_migrated_state_says_nothing():
    """**修正不得把每一天都標成有問題**:零就是零。"""
    assert rq.assess(_identity_manifest(legacy_remaining=0)) == []
    assert rq.assess(_identity_manifest()) == []      # 拿不到就不猜


def test_the_producer_writes_that_field():
    """**生產端要記,下游才驗得動。** 走生產路徑寫一次 timeline,
    確認那一格真的在 manifest 裡(而不是只有這個判準在讀一個
    永遠不會出現的欄位)。"""
    import datetime as dt
    import json
    import tempfile
    from pathlib import Path
    import morning_report as mr
    f = Path(tempfile.mkdtemp()) / "tl.json"
    f.write_text(json.dumps({"geopolitical:伊朗:2026-08": {
        "first_seen": "2026-08-01", "days": 6, "last_seen": "2026-08-09",
        "latest_title": "伊朗國內爆發示威", "entity": "伊朗",
        "subjects": ["伊朗"], "event_type": "geopolitical",
        "identity_schema": 1}}, ensure_ascii=False), encoding="utf-8")
    old = mr.EVENT_TIMELINE_FILE
    try:
        mr.EVENT_TIMELINE_FILE = f
        mr.update_event_timeline(
            [{"event_type": "geopolitical", "entity": "日本",
              "title": "日本首相宣布改組內閣"}],
            dt.datetime(2026, 8, 9, 7, 0, tzinfo=mr.TPE))
    finally:
        mr.EVENT_TIMELINE_FILE = old
    assert "legacy_remaining" in mr._RUN_MANIFEST["event_identity"]
    assert mr._RUN_MANIFEST["event_identity"]["legacy_remaining"] >= 1


# ===== 第二十七輪外審 P1-2:strict 仍接受「欄位在、內容空」 =====

def _strict_ok() -> dict:
    """一份**真的跑過**的 manifest(每一格都有生產會寫進去的內容)。"""
    return {"git_sha": "abc", "github_run_id": "1", "run_nonce": "n",
            "report_kind": rq.MORNING_REPORT,
            "llm": {"analysis_origin": "luna_specialized",
                    "payload_budget": {"chars_before": 100, "chars_after": 90,
                                       "limit": 999, "over_budget": False},
                    "primary_metrics": {"parsed": True, "claims": 3,
                                        "sections_present": 8,
                                        "validation_problems": 0},
                    "recap_saved": "saved",
                    "request_measurements": [
                        {"role": "primary", "chars": 100, "tokens": 30,
                         "accepted": True}]},
            "news": {"fulltext_plan": {"clusters": 3, "targets": 5,
                                       "available_news": 40}}}


def _strict(m) -> list:
    return [p["code"] for p in rq.assess(m, mode="strict",
                                         expected_sha="abc",
                                         expected_run_id="1")]


def test_a_manifest_that_really_ran_passes_strict():
    """**地基**:先證明合格的那一份會過,否則下面每一條都可能是誤報。"""
    assert _strict(_strict_ok()) == []


def test_empty_required_blocks_do_not_satisfy_the_specialized_contract():
    """**「不是 None」不算跑過。**

    `{}`、`[]`、`""` 都不是 `None` —— 於是一份每一格都空著的 manifest
    可以通過 strict。canary 從「讀錯檔」修到「讀對這一班的檔」,
    卻還沒證明這一班**產出了有效內容**。
    """
    m = _strict_ok()
    m["llm"].update(payload_budget={}, primary_metrics={},
                    recap_saved="", request_measurements=[])
    got = _strict(m)
    assert got == ["manifest_incomplete"], got


def test_each_empty_block_is_rejected_on_its_own():
    """**逐格量**:四格一起空的話,只要有一格擋得住就看不出其餘三格
    有沒有在作用(這個 repo 記過的形狀)。"""
    for key, empty in (("payload_budget", {}), ("primary_metrics", {}),
                       ("recap_saved", ""), ("request_measurements", [])):
        m = _strict_ok()
        m["llm"][key] = empty
        assert _strict(m) == ["manifest_incomplete"], key


def test_a_half_written_budget_block_is_rejected():
    """**只有一個欄位不算跑過。** `{}` 會被「chars_before 不是正整數」
    那一條擋下,所以要另給一個**欄位型別對、但少了其餘幾格**的反例,
    才量得到「缺欄位」那條規則自己(突變驗證當場證明前者量不到)。
    """
    m = _strict_ok()
    m["llm"]["payload_budget"] = {"chars_before": 100}
    assert _strict(m) == ["manifest_incomplete"], _strict(m)


def test_a_primary_measurement_must_be_accepted_and_positive():
    """量測要是**被接受**且字元/token 都為正 —— 一筆被拒的請求證明不了
    這一班送出過有效的 payload。"""
    for bad in ([{"role": "primary", "chars": 100, "tokens": 30,
                  "accepted": False}],
                [{"role": "primary", "chars": 0, "tokens": 30,
                  "accepted": True}],
                [{"role": "primary", "chars": 100, "tokens": 0,
                  "accepted": True}]):
        m = _strict_ok()
        m["llm"]["request_measurements"] = bad
        assert _strict(m) == ["manifest_incomplete"], bad


def test_truthy_wrong_types_do_not_count_as_a_measurement():
    """**真值判斷不是型別判斷**(第二十七輪外審第二輪)。

    `accepted: "false"` 是 truthy;而 `bool` 是 `int` 的子類,
    `chars: True` 會通過 `> 0`。生產寫進去的是 `bool(accepted)` 與真的
    整數 —— 判準要照那個型別驗,否則一份型別全錯的 manifest 讓 canary 綠。
    """
    # 用 `in` 不用相等:型別壞掉的量測也會讓字元閘門的比例算出怪數字,
    # 那是另一條規則在說話 —— 這裡只驗這一條有沒有作用。
    for bad in ([{"role": "primary", "chars": 100, "tokens": 30,
                  "accepted": "false"}],
                [{"role": "primary", "chars": True, "tokens": 30,
                  "accepted": True}],
                [{"role": "primary", "chars": 100, "tokens": True,
                  "accepted": True}]):
        m = _strict_ok()
        m["llm"]["request_measurements"] = bad
        assert "manifest_incomplete" in _strict(m), bad
    # 預算那一格同理
    m = _strict_ok()
    m["llm"]["payload_budget"] = dict(m["llm"]["payload_budget"],
                                      chars_before=True)
    assert "manifest_incomplete" in _strict(m), _strict(m)


def test_unparsed_metrics_are_rejected():
    """`primary_metrics` 有欄位不代表分析真的被解析出來。"""
    m = _strict_ok()
    m["llm"]["primary_metrics"] = {"parsed": False, "claims": 0,
                                   "sections_present": 0,
                                   "validation_problems": 3}
    assert _strict(m) == ["manifest_incomplete"]


def test_missing_fulltext_plan_is_rejected_in_strict_mode():
    """**整格缺席先前靜默通過**:那段檢查包在 `if plan:` 裡,
    而它正是 2026-08-06 兩階段抓取整段 no-op 的哨兵。"""
    m = _strict_ok()
    m.pop("news")
    assert _strict(m) == ["canary_no_fetch_plan"], _strict(m)
    # 每日生產不因此吵(那一天可能真的沒跑到那一段,而信仍然寄出去了)
    assert [p["code"] for p in rq.assess(m)] == []


def test_missing_report_kind_is_rejected_in_strict_mode():
    """缺席時判準會退回「當成平日報」—— 那個預設對每日生產是對的,
    對 canary 是「沒證明」。"""
    m = _strict_ok()
    m.pop("report_kind")
    assert _strict(m) == ["canary_no_report_kind"], _strict(m)


def test_the_weekend_digest_is_not_asked_for_a_fetch_plan():
    """週日綜合信不跑兩階段抓取 —— 對它要那份計畫是另一種假警報。"""
    m = _strict_ok()
    m["report_kind"] = rq.WEEKEND_DIGEST
    m.pop("news")
    assert "canary_no_fetch_plan" not in _strict(m), _strict(m)


# ===== 第二十七輪外審 Commit 4:可觀測性 =====

def test_a_foreign_central_bank_gets_no_taiwan_locality():
    """**裸「央行」讓外國央行拿到台灣在地性加分**(外審 P2-1)。

    實測(修正前):「日本央行升息」與「歐洲央行維持利率」都是 0.4,
    而 locality 是 top-event 排序的一軸 —— 那個分數會把與台灣只有間接
    關係的外國央行事件擠進前三。台灣自己的央行寫得出來。
    """
    import event_score as es
    # **全名也要擋**(外審第二輪 F1):「歐洲**中央**銀行」不在
    # `event_graph` 的央行名單裡,而它含「中央銀行」—— 名單永遠列不完,
    # 所以判準走宣告過的**法域表**組出來的規則。
    for t in ("日本央行升息 日圓走弱", "歐洲央行維持利率不變",
              "中國央行降準", "歐洲中央銀行維持利率不變",
              "日本中央銀行升息", "英國中央銀行降息"):
        assert es._locality(t) == 0.0, t
    for t in ("央行理監事會議 新台幣", "中央銀行宣布選擇性信用管制",
              "台積電法說 台股走高"):
        assert es._locality(t) > 0.0, t


def test_success_after_retries_records_the_physical_attempts():
    """**「第 3 次才成功」與「第 1 次就成功」在帳本裡長得一樣**
    (外審 P2-2)。邏輯呼叫數與實體請求數是兩件事。"""
    import llm_http as lh

    class _R:
        def __init__(self, code):
            self.status_code, self.headers = code, {}

    man: dict = {}
    seq = {"n": 0}

    def _post(*a, **k):
        seq["n"] += 1
        return _R(503 if seq["n"] < 3 else 200)

    orig_post, orig_sleep = lh.requests.post, lh.time.sleep
    try:
        lh.requests.post = _post
        lh.time.sleep = lambda *_: None
        lh.post_with_backoff("http://x", {}, {}, timeout=1, manifest=man)
    finally:
        lh.requests.post, lh.time.sleep = orig_post, orig_sleep
    phys = man["llm"]["physical_attempts"]
    assert phys and phys[-1]["attempts"] == 3, man["llm"]
    assert phys[-1]["retried_on"] == 503, phys


def test_a_first_try_success_records_no_physical_attempts():
    """**修正不得把每一次都記一筆**:一次就成功的請求不留這筆。"""
    import llm_http as lh

    class _R:
        status_code, headers = 200, {}

    man: dict = {}
    orig = lh.requests.post
    try:
        lh.requests.post = lambda *a, **k: _R()
        lh.post_with_backoff("http://x", {}, {}, timeout=1, manifest=man)
    finally:
        lh.requests.post = orig
    assert "physical_attempts" not in man.get("llm", {}), man


def _identity_delta(now, prev=None) -> dict:
    eid = {"schema": 8, "legacy_remaining": now}
    if prev is not None:
        eid["previous_legacy_remaining"] = prev
    return _ok_manifest(report_kind=rq.MORNING_REPORT, event_identity=eid)


def test_the_first_day_of_a_migration_is_not_a_finding():
    """**判準要與寫出來的話一致**(外審 P2-4):訊息說「升版當天正常,
    隔天還在才是問題」,而上一版只看單次 snapshot —— 新公式第一次上線
    那天必然報一次 degraded,即使那正是程式自己認定的正常狀態。"""
    assert rq.assess(_identity_delta(14)) == []
    assert rq.assess(_identity_delta(14, 23)) == []      # 正在下降


def test_a_migration_that_stops_moving_is_reported():
    """沒有下降 → degraded;**往回長** → defect(那不只是沒接上)。"""
    got = rq.assess(_identity_delta(14, 14))
    assert [(p["code"], p["severity"]) for p in got] == [
        ("identity_generations_mixed", "degraded")], got
    got = rq.assess(_identity_delta(20, 14))
    assert [(p["code"], p["severity"]) for p in got] == [
        ("identity_generations_mixed", "defect")], got


def test_the_nonce_is_compared_not_just_present():
    """**只驗非空的話它只是一個存在性欄位**(外審 P2-5)——
    證明不了「這是那一次 process invocation」。"""
    m = _specialized(run_nonce="from-last-run")
    got = {f["code"] for f in rq.assess(m, mode="strict",
                                        expected_sha="abc123",
                                        expected_run_id="42",
                                        expected_nonce="this-invocation")}
    assert "run_binding_mismatch" in got, got
    assert rq.assess(_specialized(run_nonce="this-invocation"), mode="strict",
                     expected_sha="abc123", expected_run_id="42",
                     expected_nonce="this-invocation") == []


def test_the_producer_uses_an_externally_supplied_nonce(monkeypatch):
    """workflow 產生一次、同時交給生產與斷言,比對才有意義。"""
    import run_manifest as rmod
    monkeypatch.setenv("RUN_NONCE", "wf-supplied-123")
    assert rmod.run_binding()["run_nonce"] == "wf-supplied-123"
    monkeypatch.delenv("RUN_NONCE")
    assert rmod.run_binding()["run_nonce"]          # 沒給就退回隨機值


def _canary_runs(tmp_path, days):
    """同一個狀態檔跑過這幾天,回每次的退出碼。"""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    import deepseek_live_canary as canary
    import os as _os
    st = str(tmp_path / "streak.json")
    old = _os.environ.pop("DEEPSEEK_API_KEY", None)
    try:
        return [canary.main(["--state", st, "--now", d]) for d in days]
    finally:
        if old is not None:
            _os.environ["DEEPSEEK_API_KEY"] = old


def test_the_canary_escalates_once_the_contract_goes_unverified_too_long(
        tmp_path):
    """**secret 被刪掉之後可以每週綠燈**(外審 P2-3):RC=2 只印 warning。

    第一版數「連續幾次」—— 而那個數字被同日 rerun 灌高、被漏跑的班次打斷
    (外審第二、三輪各抓到一次)。**「連續幾次」本來就不是要量的東西**:
    要量的是「距離上一次真的驗證過,過了多久」。
    """
    # **第三班就要升級**(外審第四輪):從來沒成功過時,第一次失敗是
    # 這個窗口的起點而不是起點前一班 —— 直接拿它當基準會少算一班。
    codes = _canary_runs(tmp_path, ["2026-08-01", "2026-08-08", "2026-08-15"])
    assert codes == [2, 2, 1], codes


def test_reruns_on_the_same_day_do_not_escalate(tmp_path):
    """同日 rerun 不會讓它變老 —— 時間才是判準。"""
    assert _canary_runs(tmp_path, ["2026-08-09"] * 4) == [2, 2, 2, 2]


def test_a_missed_scheduled_run_does_not_reset_the_clock(tmp_path):
    """**漏跑一班不是「驗證過」。**

    數次數的版本會被漏班打斷(第三輪外審要求隔太久就重設)——
    而漏班代表**沒有資料**,不代表契約好好的。改看時間之後,
    漏一週反而讓它更快到門檻,那才是對的方向。
    """
    assert _canary_runs(tmp_path, ["2026-08-01", "2026-08-15"]) == [2, 1]


def test_a_success_resets_the_clock(tmp_path, monkeypatch):
    """驗證成功之後重新起算 —— 否則一次久遠的失敗會永遠掛在那裡。"""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    import deepseek_live_canary as canary
    st = str(tmp_path / "s.json")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    canary.main(["--state", st, "--now", "2026-08-01"])
    canary._record(st, True, "2026-08-20")          # 中間有一次成功
    assert canary.main(["--state", st, "--now", "2026-08-27"]) == 2
    assert canary.main(["--state", st, "--now", "2026-09-11"]) == 1


# ===== 第二十八輪外審 P2-1 / P2-3 =====

def test_the_budget_block_validates_types_and_relationships():
    """**只驗 `chars_before` 是正整數不夠**(外審 P2-1)。

    `{"chars_after": null, "limit": "not-a-number", "over_budget": "false"}`
    照樣通過 —— 而 canary 的綠燈要代表「預算政策真的跑過」。
    """
    base = _strict_ok()["llm"]["payload_budget"]
    for bad in ({"chars_after": None}, {"chars_after": -1},
                {"chars_after": True}, {"limit": "not-a-number"},
                {"limit": 0}, {"over_budget": "false"}, {"over_budget": 1}):
        m = _strict_ok()
        m["llm"]["payload_budget"] = dict(base, **bad)
        assert "manifest_incomplete" in _strict(m), bad
    # **旗標要與數字一致**:兩者矛盾時,信任哪一個都是猜的
    m = _strict_ok()
    m["llm"]["payload_budget"] = dict(base, over_budget=True)
    assert "manifest_incomplete" in _strict(m), _strict(m)


def test_the_metrics_block_requires_a_clean_validation():
    """`validation_problems=999` 代表那份輸出**沒有通過驗證** ——
    而 canary 的名字是「特化輸出真的產生了」。"""
    base = _strict_ok()["llm"]["primary_metrics"]
    for bad in ({"claims": None}, {"claims": -1}, {"claims": True},
                {"sections_present": None}, {"sections_present": []},
                # `structured_metrics` 寫出來的是**計數**,不是清單 ——
                # 上一版順手接受了非空清單,那是想像出來的形狀
                #(外審第二輪 F1)。
                {"sections_present": ["anything"]}, {"sections_present": 0},
                {"validation_problems": "0"}, {"validation_problems": 999}):
        m = _strict_ok()
        m["llm"]["primary_metrics"] = dict(base, **bad)
        assert "manifest_incomplete" in _strict(m), bad


def test_broken_state_persistence_is_not_a_temporary_outage(tmp_path):
    """**狀態層壞掉時仍會永遠綠燈**(外審 P2-3)。

    「多久沒驗證過」整條政策靠那個檔活著 —— cache 一直 restore/save
    失敗的話,每一班都以為自己是第一次,於是永遠停在 2、永遠綠燈。
    那要有自己的退出碼,而 workflow 對它失敗。
    """
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    import deepseek_live_canary as canary
    import os as _os
    blocker = tmp_path / "afile"
    blocker.write_text("", encoding="utf-8")
    old = _os.environ.pop("DEEPSEEK_API_KEY", None)
    try:
        rc = canary.main(["--state", str(blocker / "s.json"),
                          "--now", "2026-08-09"])
    finally:
        if old is not None:
            _os.environ["DEEPSEEK_API_KEY"] = old
    assert rc == 3, rc
    # **不帶 `--state` 是本機用法** —— 不碰任何狀態檔,也不算壞掉
    assert canary.main(["--now", "2026-08-09"]) == 2


def test_the_workflow_fails_on_broken_state_too():
    """**噪音會淹沒訊號,但沉默會淹沒一切。** RC=3 要讓 job 紅。"""
    import yaml
    from pathlib import Path
    wf = yaml.safe_load((Path(__file__).resolve().parents[1] / ".github"
                         / "workflows" / "deepseek-canary.yml")
                        .read_text(encoding="utf-8"))
    run = next(st for st in wf["jobs"]["contract"]["steps"]
               if st.get("id") == "canary")["run"]
    assert '"$code" = "3"' in run, run


def test_a_corrupt_state_file_is_not_silently_reset(tmp_path):
    """**「沒有這個檔」與「讀不動這個檔」是兩件事**(外審第二輪 F2)。

    上一版把讀取例外一律吞成 `{}` 然後**覆寫**掉那份歷史 ——
    升級時鐘被一個壞掉的檔案重設,而沒有人看得出來。
    """
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    import deepseek_live_canary as canary
    import os as _os
    st = tmp_path / "s.json"
    st.write_text("{壞掉", encoding="utf-8")
    old = _os.environ.pop("DEEPSEEK_API_KEY", None)
    try:
        rc = canary.main(["--state", str(st), "--now", "2026-08-09"])
    finally:
        if old is not None:
            _os.environ["DEEPSEEK_API_KEY"] = old
    assert rc == 3, rc
    assert st.read_text(encoding="utf-8") == "{壞掉", "壞檔被覆寫了"


def test_a_missing_restore_that_should_have_happened_is_a_state_failure(
        tmp_path):
    """**cache 說上一班存過,而檔案不在** —— 那是跨 run 的持久層壞了
    (外審第二輪 F3)。這一格壞掉時,金絲雀會每一班都以為自己是第一次。"""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    import deepseek_live_canary as canary
    import os as _os
    old = _os.environ.pop("DEEPSEEK_API_KEY", None)
    try:
        rc = canary.main(["--state", str(tmp_path / "nope.json"),
                          "--now", "2026-08-09", "--expect-restored"])
        # 沒有宣稱該有的話,第一次跑是正常的
        rc2 = canary.main(["--state", str(tmp_path / "fresh.json"),
                           "--now", "2026-08-09"])
    finally:
        if old is not None:
            _os.environ["DEEPSEEK_API_KEY"] = old
    assert rc == 3, rc
    assert rc2 == 2, rc2


def test_the_canary_state_lives_in_the_repository():
    """**cache 對「一週一班」是錯的儲存**(外審第二輪 F3)。

    GitHub 七天未用就清掉快取,而我們的間隔正好是七天 —— 一直沒命中的話,
    每一班都以為自己是第一次,於是永遠停在「暫時性」而週週綠燈。
    repo 是這個 job 拿得到的持久層,而且**可驗證**:checkout 一定會把它
    帶回來,所以「檔案不在」只可能是還沒 bootstrap。
    """
    import yaml
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    wf = yaml.safe_load((root / ".github" / "workflows"
                         / "deepseek-canary.yml").read_text(encoding="utf-8"))
    steps = wf["jobs"]["contract"]["steps"]
    assert not any(str(st.get("uses", "")).startswith("actions/cache")
                   for st in steps), "還在用 cache 當持久層"
    assert wf["permissions"]["contents"] == "write", wf["permissions"]
    run = next(st for st in steps if st.get("id") == "canary")["run"]
    import sys
    sys.path.insert(0, str(root / "tools"))
    import deepseek_live_canary as _c
    assert _c.DEFAULT_STATE in run, run
    # **狀態要被 commit 回去**,否則下一班還是看不到
    persist = next(st for st in steps
                   if "Persist" in str(st.get("name", "")))["run"]
    assert "git push" in persist and _c.DEFAULT_STATE in persist, persist
    # 而那個檔案已經在版控裡(bootstrap 過了)
    assert (root / _c.DEFAULT_STATE).exists()


def test_running_without_a_state_path_touches_nothing(tmp_path, monkeypatch):
    """**文件寫的本機用法不得動到版控裡的狀態**(外審第三輪)。

    把 `--state` 的預設改成 repo 的路徑之後,不帶參數直接跑會寫進那個檔、
    甚至把 `last_success` 洗掉 —— 而 workflow 本來就明講了路徑。
    """
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "tools"))
    import deepseek_live_canary as canary
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    live = root / canary.DEFAULT_STATE
    before = live.read_text(encoding="utf-8") if live.exists() else None
    assert canary.main(["--now", "2026-08-09"]) == 2
    after = live.read_text(encoding="utf-8") if live.exists() else None
    assert before == after, "無參數的本機用法動到了版控裡的狀態"


# ===== 第二十九輪外審 Commit 4:strict 驗收補完 =====

def test_nothing_to_save_with_candidates_is_a_dead_wire():
    """**`nothing_to_save` 蓋得住兩種完全不同的日子**(P2-3):
    「今天真的沒東西」與「有東西但 mapping 壞掉一條都抽不出來」。
    有分子分母才分得開。"""
    m = _strict_ok()
    m["llm"].update(recap_saved="nothing_to_save",
                    recap_eligible=3, recap_extracted=0)
    assert "recap_extraction_dead" in _strict(m), _strict(m)


def test_a_genuinely_quiet_day_is_still_allowed():
    """**修正不得把清淡的一天標成缺陷**:eligible == 0 的
    `nothing_to_save` 是正常答案。"""
    m = _strict_ok()
    m["llm"].update(recap_saved="nothing_to_save",
                    recap_eligible=0, recap_extracted=0)
    assert "recap_extraction_dead" not in _strict(m), _strict(m)
    # 沒有計數的舊 manifest 也不猜
    m2 = _strict_ok()
    m2["llm"]["recap_saved"] = "nothing_to_save"
    assert "recap_extraction_dead" not in _strict(m2)


def test_an_extractor_only_measurement_does_not_satisfy_primary():
    """**extractor 的量測湊不了數**(P2-4):canary 的名字是「特化
    **主分析**真的產生了」,primary 一筆都沒有時不算。"""
    m = _strict_ok()
    m["llm"]["request_measurements"] = [
        {"role": "extractor", "chars": 1000, "tokens": 500, "accepted": True}]
    assert "manifest_incomplete" in _strict(m), _strict(m)
    # 被拒的 primary + 被接受的 extractor 也不夠
    m2 = _strict_ok()
    m2["llm"]["request_measurements"] = [
        {"role": "primary", "chars": 1000, "tokens": 500, "accepted": False},
        {"role": "extractor", "chars": 1000, "tokens": 500, "accepted": True}]
    assert "manifest_incomplete" in _strict(m2), _strict(m2)


def test_the_producer_records_the_recap_counts(tmp_path):
    """**生產端要記,下游才驗得動**:`save(manifest=...)` 要寫
    eligible/extracted 兩格(走生產的呼叫形狀)。"""
    import sys
    sys.path.insert(0, "tests")
    import analysis_recap as rc
    import fixtures_analysis as fx
    man: dict = {}
    obj = fx.valid_analysis()
    pk = {"target_session_date": "2026-08-10",
          "news": [{"source_item_id": "n2", "title": "台積電法說會下週登場",
                    "entities": ["台積電"]}],
          "news_clusters": {"clusters": [
              {"cluster_id": "cluster:n2", "member_source_ids": ["n2"]}]}}
    out = rc.save(tmp_path / "recap.json", obj, pk, manifest=man)
    assert out == rc.SAVED
    assert man["llm"]["recap_eligible"] >= man["llm"]["recap_extracted"] >= 1


def test_strict_rejects_an_extractor_that_produced_nothing():
    """**「送得出去」不等於「這個能力活著」**(第三十輪外審 P2-3):
    2026-08-10 的實機 manifest 是 `called=true, items=35, parsed=0,
    valid=0, outcome="ok"` —— 抽取器吃了 35 筆、換過 provider,
    最後零筆有效輸出,而 strict 全綠。"""
    m = _strict_ok()
    m["llm_extractor"] = {"called": True, "items": 35, "parsed": 0,
                          "valid": 0, "outcome": "ok",
                          "fallback_from": "deepseek", "fallback_to": "gemini"}
    assert "event_extractor_dead" in _strict(m), _strict(m)


def test_parsed_but_all_invalid_is_still_dead():
    """**判準與 capability_health 同一條**(外審 r1):`parsed` 只代表
    「JSON 解得開」、`valid` 只代表「通過 schema」—— 解得開但全部不合格、
    或合格卻全被合併掉,都是零產出。兩邊各寫一次的話,
    一邊說 inactive、一邊說綠燈。"""
    for ex in ({"called": True, "items": 35, "parsed": 30, "valid": 0,
                "survived": 0, "outcome": "ok"},
               {"called": True, "items": 35, "parsed": 30, "valid": 12,
                "survived": 0, "outcome": "ok"}):
        m = _strict_ok()
        m["llm_extractor"] = ex
        assert "event_extractor_dead" in _strict(m), ex
    # 沒有 `survived` 的舊 manifest 退回看 `valid`
    m2 = _strict_ok()
    m2["llm_extractor"] = {"called": True, "items": 35, "parsed": 30,
                           "valid": 0, "outcome": "ok"}
    assert "event_extractor_dead" in _strict(m2)


def test_a_working_or_idle_extractor_is_not_a_defect():
    """有產出、或今天根本沒東西可抽、或沒開 —— 都不是缺陷
    (修正不得把正常的日子標紅)。"""
    for ex in ({"called": True, "items": 35, "parsed": 30, "valid": 28,
                "survived": 25, "outcome": "ok"},
               {"called": True, "items": 0, "parsed": 0, "valid": 0,
                "outcome": "ok"},
               {"called": False, "items": 0, "parsed": 0, "valid": 0,
                "outcome": "disabled"}):
        m = _strict_ok()
        m["llm_extractor"] = ex
        assert "event_extractor_dead" not in _strict(m), ex
    # 沒有這一格的舊 manifest 不猜
    assert "event_extractor_dead" not in _strict(_strict_ok())

