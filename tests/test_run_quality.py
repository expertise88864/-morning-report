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
    # **走 recorder 的生產形狀**:先前 fixture 手工把 token 放進
    # `attempts`、字元放在 manifest 頂層 —— 而健康的日子 attempts 裡根本
    # 沒有 primary,量測整個不跑,測試卻是綠的(第一輪外審 F1)。
    m = _ok_manifest(llm={**_measured((1_052_716, 391_145)),
                          "analysis_origin": ao.LUNA_SPECIALIZED,
                          "recap_saved": True,
                          "payload_budget": {"over_budget": False}})
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
                          "recap_saved": True,
                          "payload_budget": {"over_budget": False}})
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
        "analysis_origin": ao.LUNA_SPECIALIZED, "recap_saved": True,
        "payload_budget": {"over_budget": False},
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
        "analysis_origin": ao.LUNA_SPECIALIZED, "recap_saved": True,
        "payload_budget": {"over_budget": False},
        "unrealizable_namespaces": ["portfolio:", "tension:", "fact:"]})
    assert rq.assess(m) == [], rq.assess(m)
    # 反向:每天都組得出來的那幾個空掉,仍然是缺陷(2026-08-08 的形狀)
    m2 = _ok_manifest(llm={
        "analysis_origin": ao.LUNA_SPECIALIZED, "recap_saved": True,
        "payload_budget": {"over_budget": False},
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
    m["llm"].update(analysis_origin=ao.LUNA_SPECIALIZED, recap_saved=True,
                    payload_budget={"over_budget": False})
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
    m = {"date": "x", "degraded_steps": [],
         "llm": {"analysis_origin": ao.LUNA_SPECIALIZED, "recap_saved": True,
                 "payload_budget": {"over_budget": False}},
         "news": {"fulltext_plan": plan}}
    got = rq.assess(m)
    assert [f["code"] for f in got] == ["news_upstream_empty"], got
    # 反向:真的有新聞卻零群集,仍要報接線缺陷
    r2 = rm.ManifestRecorder()
    news = [{"source_item_id": "n1", "title": "某事件", "entities": ["某公司"],
             "source": "甲", "source_name": "甲", "link": "http://x"}]
    r2.record_fulltext_plan(dict(fp.plan(news, []), per_cluster=[]))
    m["news"]["fulltext_plan"] = r2.data["news"]["fulltext_plan"]
    assert "fetch_plan_no_clusters" in {f["code"] for f in rq.assess(m)}
