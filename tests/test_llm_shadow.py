# -*- coding: utf-8 -*-
"""LLM 影子比較(批#89)。

換模型之前要有**跨日的可比較證據**,而不是一次的主觀印象 —— 立場評分、
beta 0.31、Top5 熔斷都是在現有模型的輸出分佈上校準的,而分佈差異要看多天。
"""
import json

import pytest

import llm_shadow as ls


def _out(model, stance, score, text, summary="", ok=True, elapsed=1.0):
    return {"model": model, "text": text, "summary": summary, "ok": ok,
            "elapsed": elapsed, "stance": {"label": stance, "score": score}}


def test_flip_is_distinguished_from_mere_disagreement():
    """**翻面(多↔空)與「中性 vs 偏多」是兩件事。**

    只有前者會改變讀者的動作,所以必須分開記 —— 把兩者都算成「不一致」的話,
    真正危險的訊號會被日常的措辭差異淹沒。
    """
    rec = ls.compare_outputs(_out("A", "偏多", 5, "x"), _out("B", "偏空", -3, "y"))
    assert rec["stance_agree"] is False and rec["stance_flipped"] is True
    rec2 = ls.compare_outputs(_out("A", "偏多", 5, "x"), _out("B", "中性", 1, "y"))
    assert rec2["stance_agree"] is False and rec2["stance_flipped"] is False
    # 未收錄的標籤**不猜**方向
    rec3 = ls.compare_outputs(_out("A", "偏多", 5, "x"), _out("B", "看漲吧", 2, "y"))
    assert rec3["stance_flipped"] is None


def test_verdict_says_it_does_not_know_when_samples_are_thin():
    """樣本不足時必須明說「還不知道」,不給一個看起來像結論的數字。"""
    thin = [{"date": f"2026-07-{d:02d}", "primary_ok": True, "shadow_ok": True,
             "shadow_model": "m", "stance_agree": True, "stance_flipped": False}
            for d in range(1, 4)]
    s = ls.summarize(thin, "m")
    assert s["both_ok"] == 3 and "樣本不足" in s["verdict"]
    assert "stance_agree_rate" in s      # 數字仍給,只是判讀說不夠

    enough = [{"date": f"2026-07-{d:02d}", "primary_ok": True, "shadow_ok": True,
               "shadow_model": "m", "stance_agree": True, "stance_flipped": False}
              for d in range(1, 13)]
    s2 = ls.summarize(enough, "m")
    assert "樣本不足" not in s2["verdict"] and s2["stance_agree_rate"] == 1.0


def test_a_single_flip_dominates_the_verdict():
    """**一天翻面就要被指名。** 立場一致率再高也不能蓋過它 ——
    那一天讀者會做出相反的動作,平均值看不見這件事。"""
    rows = [{"date": f"2026-07-{d:02d}", "primary_ok": True, "shadow_ok": True,
             "shadow_model": "m", "stance_agree": True, "stance_flipped": False}
            for d in range(1, 15)]
    rows[3]["stance_agree"], rows[3]["stance_flipped"] = False, True
    s = ls.summarize(rows, "m")
    assert s["stance_flips"] == 1
    assert "翻面" in s["verdict"] and "人工" in s["verdict"]


def test_ledger_upsert_is_idempotent_per_day_and_prunes():
    """同日重跑覆蓋而不是灌出重複樣本(否則一致率會被同一天灌票)。"""
    led = []
    for i in range(3):
        led = ls.upsert(led, {"primary_model": "A", "shadow_model": "B",
                              "stance_agree": True}, "2026-07-31")
    assert len(led) == 1
    led = ls.upsert(led, {"primary_model": "A", "shadow_model": "C"}, "2026-07-31")
    assert len(led) == 2, "不同影子模型算不同樣本"
    # 修剪:以**當次的 today** 為基準往回 LEDGER_KEEP_DAYS 天,更早的丟掉
    aged = led + [{"date": "2020-01-01", "primary_model": "A",
                   "shadow_model": "B"}]
    pruned = ls.upsert(aged, {"primary_model": "A", "shadow_model": "B"},
                       "2026-07-31")
    assert all(r["date"] != "2020-01-01" for r in pruned), "過期資料要修剪"
    assert any(r["date"] == "2026-07-31" for r in pruned)


def test_ledger_refuses_to_load_corrupt_data(tmp_path):
    """讀不出來就拋 —— 呼叫端不得代它清檔。影子帳本的價值全在累積,
    一次誤覆寫就等於重新開始(本 repo 反覆出現的病灶)。"""
    p = tmp_path / "led.json"
    p.write_text('{"not": "a list"}', encoding="utf-8")
    with pytest.raises(ValueError):
        ls.load_ledger(p)
    p.write_text('[{"date": "2026-07-31"}, "壞列"]', encoding="utf-8")
    with pytest.raises(ValueError):
        ls.load_ledger(p)
    p.write_text('[{"date": "2026-07-31"}]', encoding="utf-8")
    assert len(ls.load_ledger(p)) == 1
    assert ls.load_ledger(tmp_path / "沒有這個檔.json") == []


def test_shadow_never_breaks_the_report(monkeypatch, tmp_path):
    """**影子失敗只是今天沒有比較資料。** 晨報不可斷優先於評估。"""
    import morning_report as mr

    monkeypatch.setattr(mr, "LLM_SHADOW_PROVIDER", "openai")
    monkeypatch.setattr(mr, "LLM_SHADOW_MODEL", "gpt-5.6-terra")
    monkeypatch.setattr(mr, "LLM_SHADOW_LEDGER_FILE", tmp_path / "led.json")

    def _boom(*_a, **_k):
        raise RuntimeError("shadow down")

    monkeypatch.setattr(mr, "_call_openai", _boom)
    mr._RUN_MANIFEST.pop("llm_shadow", None)
    import datetime as _dt
    try:
        mr._run_llm_shadow("prompt", "## 我的明確立場\n立場:偏多\n淨分 +5\n",
                           _dt.datetime(2026, 7, 31, 6, 45, tzinfo=mr.TPE))
        stat = mr._RUN_MANIFEST.get("llm_shadow") or {}
        assert stat.get("today", {}).get("shadow_ok") is False
        assert "shadow_error" in stat.get("today", {}), "失敗原因要留下"
        # 仍要寫進帳本 —— 「影子今天掛了」本身就是要累積的資訊
        led = json.loads((tmp_path / "led.json").read_text(encoding="utf-8"))
        assert len(led) == 1 and led[0]["shadow_ok"] is False
    finally:
        mr._RUN_MANIFEST.pop("llm_shadow", None)


def test_shadow_is_off_by_default(monkeypatch, tmp_path):
    """沒設環境變數就完全不動作(不呼叫、不寫檔、不佔時間預算)。"""
    import morning_report as mr

    monkeypatch.setattr(mr, "LLM_SHADOW_PROVIDER", "")
    monkeypatch.setattr(mr, "LLM_SHADOW_MODEL", "")
    monkeypatch.setattr(mr, "LLM_SHADOW_LEDGER_FILE", tmp_path / "led.json")
    called = []
    monkeypatch.setattr(mr, "_call_openai",
                        lambda *a, **k: called.append(1) or "x")
    mr._RUN_MANIFEST.pop("llm_shadow", None)
    import datetime as _dt
    mr._run_llm_shadow("prompt", "text",
                       _dt.datetime(2026, 7, 31, 6, 45, tzinfo=mr.TPE))
    assert not called and not (tmp_path / "led.json").exists()
    assert "llm_shadow" not in mr._RUN_MANIFEST


def test_extractor_provider_is_independent_of_the_main_analysis(monkeypatch):
    """批#90:換主分析模型時,**抽取器不該被一起換過去**。

    抽取是機械性的結構化任務(把新聞抄成 JSON 欄位),不需要旗艦推理模型;
    而它的額度是 16000 tokens —— 用高價模型跑滿一次就比主分析還貴,
    換模型的成本會反過來由這裡主導。
    """
    import morning_report as mr

    news = [{"title": "台積電消息", "summary": "內容", "source": "測試",
             "link": "https://example.com/1",
             "published": "2026-07-31T08:00:00+08:00"}]
    used = []
    monkeypatch.setattr(mr, "LLM_PROVIDER", "openai")       # 主分析換 OpenAI
    monkeypatch.setattr(mr, "EXTRACTOR_PROVIDER", "deepseek")   # 抽取器留 DeepSeek
    assert mr._extractor_provider() == "deepseek"
    monkeypatch.setattr(mr, "DEEPSEEK_API_KEY", "k")
    monkeypatch.setenv("LLM_EVENT_EXTRACTION", "1")
    monkeypatch.setattr(mr, "_call_deepseek_extractor",
                        lambda p: used.append("deepseek") or "[]")
    monkeypatch.setattr(mr, "_call_openai",
                        lambda p, **k: used.append("openai") or "[]")
    mr._RUN_MANIFEST.pop("llm_extractor", None)
    try:
        mr.call_llm_event_extractor(news, [])
        assert used == ["deepseek"], f"抽取器走錯 provider:{used}"
    finally:
        mr._RUN_MANIFEST.pop("llm_extractor", None)


def test_openai_reasoning_effort_is_sent_and_degrades_on_400(monkeypatch):
    """批#90c:推理強度是**成本主旋鈕**,而且必須能在被拒時退讓。

    推理 token 以 output 計價,而 GPT-5.6 的 output 是 input 的 6 倍價 ——
    推理量翻倍帳單幾乎跟著翻倍。抽取器是機械性任務(把新聞抄成 JSON),
    2026-07-31 的 0 產出事故正是推理吃光額度造成的,所以它預設 low。

    但這是**選配參數**:若某個模型或端點不接受,寧可少一個旋鈕也不要讓整份
    分析失敗 —— 所以 400 時移除該欄位重試一次。
    """
    import morning_report as mr

    sent = []

    class _R:
        def __init__(self, code):
            self.status_code = code
            self.text = "unknown parameter reasoning_effort"

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(f"HTTP {self.status_code}")

        def json(self):
            if self.status_code == 400:
                # 真實的 OpenAI 錯誤物件形狀 —— 新守衛靠 `error.param` 判斷
                # 這個 400 是不是真的在指責 reasoning_effort(第九輪 P1-3)
                return {"error": {"type": "invalid_request_error",
                                  "param": "reasoning_effort",
                                  "message": "Unsupported value"}}
            return {"choices": [{"finish_reason": "stop",
                                 "message": {"content": "ok"}}],
                    "usage": {"completion_tokens": 10}}

    def _post(url, json=None, **kw):
        sent.append(json)
        return _R(400 if len(sent) == 1 else 200)

    monkeypatch.setattr(mr, "OPENAI_API_KEY", "k")
    monkeypatch.setattr(mr.requests, "post", _post)
    out = mr._call_openai("prompt", model="gpt-5.6-luna", reasoning="low")
    assert out == "ok"
    assert sent[0].get("reasoning_effort") == "low", "第一次要帶推理強度"
    assert "reasoning_effort" not in sent[1], "400 之後要移除該欄位重試"
    assert sent[1]["model"] == "gpt-5.6-luna", "重試不得換掉模型"


def test_manifest_separates_roles_and_only_records_accepted_calls(monkeypatch):
    """批#91(第九輪 P0-2):**telemetry 必須依角色分槽,且只記通過驗收的。**

    批#90d 的第一版把 primary / extractor / shadow 寫進同一個槽位,每次呼叫
    覆蓋 provider 與 model。實際執行順序是抽取器 → 主分析 → 影子,所以開了
    影子之後 manifest 會宣稱**「這封信由影子模型撰寫」** —— 而影子輸出根本
    沒進信件。**錯誤的可觀測性比沒有更危險**:它給的是看似精確、語意卻錯的答案。

    另外原本在 `r.json()` 之後就登記,於是 `finish_reason=length` 的失敗呼叫
    也被記成「實際模型」,而真正寫出信的可能是後面的備援。
    """
    import morning_report as mr

    def _resp(payload, code=200):
        class _R:
            status_code = code

            def raise_for_status(self):
                pass

            def json(self):
                return payload
        return _R()

    ok = {"choices": [{"finish_reason": "stop", "message": {"content": "文"}}],
          "usage": {"prompt_tokens": 21000, "completion_tokens": 9000,
                    "completion_tokens_details": {"reasoning_tokens": 4200}}}
    truncated = {"choices": [{"finish_reason": "length",
                              "message": {"content": ""}}],
                 "usage": {"completion_tokens": 98000}}

    monkeypatch.setattr(mr, "OPENAI_API_KEY", "k")
    mr._RUN_MANIFEST.pop("llm", None)
    try:
        monkeypatch.setattr(mr.requests, "post", lambda *a, **k: _resp(ok))
        mr._call_openai("p", model="gpt-5.6-luna", reasoning="xhigh",
                        role="extractor")
        mr._call_openai("p", model="gpt-5.6-terra", reasoning="medium",
                        role="primary")
        mr._call_openai("p", model="gpt-5.6-luna", reasoning="low",
                        role="shadow")
        rec = mr._RUN_MANIFEST["llm"]
        assert rec["primary"]["model"] == "gpt-5.6-terra",             f"影子/抽取器蓋掉了主分析:{rec['primary']}"
        assert rec["extractor"]["model"] == "gpt-5.6-luna"
        assert rec["shadow"]["model"] == "gpt-5.6-luna"
        # 推理 token **擇一不相加**(provider 兩種欄位都給時會憑空翻倍)
        assert rec["primary"]["reasoning_tokens"] == 4200

        # 截斷的呼叫不得被當成 writer,但要留在 attempts 裡
        monkeypatch.setattr(mr.requests, "post", lambda *a, **k: _resp(truncated))
        try:
            mr._call_openai("p", model="gpt-5.6-sol", reasoning="max",
                            role="primary")
        except mr.ExtractorOutputTruncated:
            pass
        rec = mr._RUN_MANIFEST["llm"]
        assert rec["primary"]["model"] == "gpt-5.6-terra",             "失敗的呼叫覆蓋了實際 writer"
        assert any(a["role"] == "primary" and a["model"] == "gpt-5.6-sol"
                   for a in rec["attempts"]), "失敗嘗試沒有留下紀錄"
    finally:
        mr._RUN_MANIFEST.pop("llm", None)


def test_requested_and_applied_reasoning_effort_are_distinguished(monkeypatch):
    """批#91(第九輪 P1-1):400 退讓後用的是 provider 預設,不是使用者要的強度。

    原本只記 requested,於是 manifest 會顯示 `reasoning_effort: "max"`
    而實際那次呼叫**根本沒帶這個參數** —— 看起來像有生效。
    """
    import morning_report as mr

    n = []

    class _R:
        def __init__(self, code):
            self.status_code = code
            self.text = "unsupported parameter"

        def raise_for_status(self):
            pass

        def json(self):
            if self.status_code == 400:
                return {"error": {"type": "invalid_request_error",
                                  "param": "reasoning_effort",
                                  "message": "Unsupported value"}}
            return {"choices": [{"finish_reason": "stop",
                                 "message": {"content": "文"}}],
                    "usage": {}}

    monkeypatch.setattr(mr, "OPENAI_API_KEY", "k")
    monkeypatch.setattr(mr.requests, "post",
                        lambda *a, **k: (n.append(1), _R(400 if len(n) == 1 else 200))[1])
    mr._RUN_MANIFEST.pop("llm", None)
    try:
        mr._call_openai("p", model="gpt-5.6-terra", reasoning="max")
        rec = mr._RUN_MANIFEST["llm"]["primary"]
        assert rec["requested_effort"] == "max"
        assert rec["applied_effort"] is None,             f"退讓後仍宣稱套用了 {rec['applied_effort']} —— 那是假的"
    finally:
        mr._RUN_MANIFEST.pop("llm", None)


def test_extractor_provider_matrix_is_explicit_and_fails_fast(monkeypatch):
    """批#91(第九輪 P0-1):**每個 provider 都要明確分派,未知就當場失敗。**

    原本 deepseek/openai 之外一律落到 `_call_llm_text`,而那個函式讀的是全域
    `LLM_PROVIDER` —— 於是 `LLM_PROVIDER=openai` + `EXTRACTOR_PROVIDER=gemini`
    會**兩邊都走 OpenAI**,症狀是「看起來有分開設定、實際沒有」。
    """
    import morning_report as mr

    news = [{"title": "台積電消息", "summary": "內容", "source": "測試",
             "link": "https://example.com/1",
             "published": "2026-07-31T08:00:00+08:00"}]
    for main, ext, expect in (("openai", "gemini", "gemini"),
                              ("openai", "anthropic", "anthropic"),
                              ("deepseek", "openai", "openai"),
                              ("gemini", "deepseek", "deepseek")):
        used = []
        monkeypatch.setattr(mr, "LLM_PROVIDER", main)
        monkeypatch.setattr(mr, "EXTRACTOR_PROVIDER", ext)
        monkeypatch.setattr(mr, "DEEPSEEK_API_KEY", "k")
        monkeypatch.setenv("LLM_EVENT_EXTRACTION", "1")
        for name, fn in (("deepseek", "_call_deepseek_extractor"),
                         ("openai", "_call_openai"), ("gemini", "_call_gemini"),
                         ("anthropic", "_call_anthropic")):
            monkeypatch.setattr(mr, fn,
                                lambda *a, _n=name, **k: used.append(_n) or "[]")
        mr._RUN_MANIFEST.pop("llm_extractor", None)
        mr.call_llm_event_extractor(news, [])
        assert used == [expect], f"{main}→{ext} 走到 {used},應為 {expect}"

    # 無效值**不得靜默落到別人身上**
    monkeypatch.setattr(mr, "EXTRACTOR_PROVIDER", "typo-provider")
    mr._RUN_MANIFEST.pop("llm_extractor", None)
    mr.call_llm_event_extractor(news, [])
    stat = mr._RUN_MANIFEST.get("llm_extractor") or {}
    assert "typo-provider" in str(stat.get("error") or ""),         f"無效 provider 沒有當場失敗:{stat}"
    mr._RUN_MANIFEST.pop("llm_extractor", None)


def test_400_backoff_only_when_the_error_blames_that_parameter():
    """批#92(第九輪 P1-3):**不是所有 400 都該退讓。**

    400 也可能來自 model ID 錯、額度過大、schema 不合、專案沒權限 ——
    那些情況移除推理強度沒有用,只是白花一次呼叫,而且**真正的錯誤訊息會被
    第二次的失敗蓋掉**,只剩 stderr 前 160 字。解析不出來時保守不退讓,
    讓原始錯誤原樣浮上來(訊息完整、可診斷)。
    """
    import llm_telemetry as lt

    yes = [{"type": "invalid_request_error", "param": "reasoning_effort",
            "message": "Unsupported value"},
           {"type": "invalid_request_error",
            "message": "Unknown parameter: reasoning_effort"}]
    for err in yes:
        assert lt.error_blames_param(err, "reasoning_effort")

    no = [{"type": "invalid_request_error", "param": "model",
           "message": "The model `gpt-5.6-typo` does not exist"},
          {"type": "invalid_request_error", "param": "max_completion_tokens",
           "message": "too large"},
          {"type": "insufficient_quota", "message": "quota exceeded"},
          {}, None, "not a dict"]
    for err in no:
        assert not lt.error_blames_param(err, "reasoning_effort"), err

    class _Bad:
        def json(self):
            raise ValueError("not json")

    assert not lt.response_blames_param(_Bad(), "reasoning_effort")


def test_effort_caps_are_per_provider_and_grounded_in_measurement():
    """批#93:**推理強度的標籤在不同 provider 之間不可比。**

    批#92 用一張跨 provider 的表(primary 一律 medium),上線第一班就誤報 ——
    2026-08-01 生產 manifest 顯示 `deepseek-v4-pro / requested=high /
    reasoning_tokens=517 / calls=1`,一次就完成,而 `high` 正是本 repo 的
    **程式碼預設值**。那條守衛等於每天把 `llm:config_issue` 塞進降級清單,
    而降級清單一旦有常駐雜訊,真的問題就會被淹掉。

    所以這條同時是**那次誤報的回歸測試**:上限必須各自依實測訂。
    """
    import llm_telemetry as lt

    def _has(_env):
        return True

    def _issues(**kw):
        base = dict(provider="deepseek", extractor_provider="deepseek",
                    shadow_provider="", has_key=_has)
        base.update(kw)
        return lt.validate_llm_config(**base)

    # 生產實測過的組合不得告警(這正是批#92 誤報的那一班)
    assert _issues(efforts={"primary": "high"}) == []
    # 超出實測範圍的才告警,而且要說得出是哪個 provider
    over = _issues(efforts={"primary": "max"})
    assert any("deepseek" in m and "max" in m for m in over), over
    # OpenAI 的抽取器仍限 low —— 2026-07-31 的 1560 則 0 產出就是它推理過頭
    ext = _issues(provider="openai", extractor_provider="openai",
                  efforts={"extractor": "high"})
    assert any("extractor" in m for m in ext), ext
    # 但 OpenAI 主分析可以到 xhigh(前提是 timeout 一起放大,見下)
    assert _issues(provider="openai", extractor_provider="openai",
                   efforts={"primary": "xhigh", "extractor": "low"}) == []
    # 手動/離線執行不受此限
    assert _issues(efforts={"primary": "max"}, scheduled=False) == []


def test_raising_the_token_cap_without_raising_the_timeout_is_self_contradictory():
    """批#93(第九輪 P1-2):**額度與 wall-clock 是同一個預算。**

    批#92 只對過高的強度告警,但告警擋不住實際的失敗:xhigh 的額度是 98,000
    token,75 秒內生成不完 → 逾時 → 掉備援,於是「想量 xhigh 的成本」這件事
    永遠量不到,只會得到一次 timeout。額度放大而時間不放大是自相矛盾的。
    """
    import llm_telemetry as lt

    assert lt.timeout_for("medium", 75) == 75, "medium 是基準,不該被放大"
    assert lt.timeout_for("xhigh", 75) > 75
    assert lt.timeout_for("max", 75) > lt.timeout_for("xhigh", 75)
    assert lt.timeout_for("garbage", 75) == 75, "未知強度不猜"
    # 每一個會放大額度的強度,都必須同時放大時間 —— 否則就是上面那個矛盾
    for effort, mult in lt.CAP_MULTIPLIER.items():
        if mult > lt.CAP_MULTIPLIER["medium"]:
            assert lt.timeout_for(effort, 75) > 75, (
                f"{effort} 放大了額度({mult}x)卻沒放大時間 —— 必然逾時")


def test_config_source_distinguishes_unset_from_set_to_the_default():
    """批#93:**「沒設」與「設成跟預設一樣」必須分得出來。**

    2026-08-01 使用者設了 `LLM_PROVIDER=openai` 卻仍跑 DeepSeek,而 manifest
    完全看不出原因 —— workflow 在 YAML 裡就用 `${{ vars.X || 'deepseek' }}`
    補了預設,程式看到的永遠是最終值。
    """
    import llm_telemetry as lt

    assert lt.config_source_issues("LLM_PROVIDER=openai;OPENAI_MODEL=gpt") == []
    msgs = lt.config_source_issues("LLM_PROVIDER=;OPENAI_MODEL=gpt")
    assert any("LLM_PROVIDER" in m for m in msgs), msgs
    assert any("Secrets" in m for m in msgs), "要指出最可能的原因"
    assert "OPENAI_MODEL" not in "".join(msgs), "有設的不該被列進去"
    assert lt.config_source_issues("") == [], "本機執行沒有這個變數,不該吵"


def test_config_validation_catches_typos_and_missing_keys():
    """批#92(第九輪 P1-5):**打錯字的症狀是「一切照舊」。**

    模型現在可由 GitHub Variables 隨時改,拼錯時沒有錯誤、沒有告警,
    只是沒切過去。另外「有任一把金鑰就啟動」的舊閘門是在只有 DeepSeek 的
    年代寫的 —— 換成 OpenAI-only 之後它會誤判成可用(第九輪 P1-7),
    所以要**只驗被選中的那個 provider 的金鑰**。
    """
    import llm_telemetry as lt

    only_openai = {"OPENAI_API_KEY"}

    def _has(env):
        return env in only_openai

    # 拼錯的 provider 要被指名
    msgs = lt.validate_llm_config(provider="openai", extractor_provider="openai",
                                  shadow_provider="", has_key=_has,
                                  efforts={})
    assert msgs == [], msgs
    msgs = lt.validate_llm_config(provider="opanai", extractor_provider="openai",
                                  shadow_provider="", has_key=_has, efforts={})
    assert any("opanai" in m for m in msgs), msgs
    # 只有 OpenAI 金鑰卻把抽取器指向 DeepSeek → 要抓到
    msgs = lt.validate_llm_config(provider="openai", extractor_provider="deepseek",
                                  shadow_provider="", has_key=_has, efforts={})
    assert any("DEEPSEEK_API_KEY" in m for m in msgs), msgs
    # 影子與主分析同一個 provider = 只是加倍付費
    msgs = lt.validate_llm_config(provider="openai", extractor_provider="openai",
                                  shadow_provider="openai", has_key=_has, efforts={})
    assert any("加倍付費" in m for m in msgs), msgs
