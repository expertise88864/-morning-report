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


def test_cost_is_estimated_only_where_there_is_a_price():
    """批#95(第九輪 P1-6):**估不出來就說估不出來。**

    我在 2026-08-01 用 OpenRouter 的價格估過一次 GPT-5.6,結果比官方低 2.5 倍 ——
    而這個數字會直接被拿來做「換不換模型」的決定。錯的成本數字比沒有更糟,
    所以沒收錄單價的模型一律回 None 加上原因,不拿別處的數字近似。
    """
    import llm_telemetry as lt

    usage = {"prompt_tokens": 85_814, "completion_tokens": 5_557}
    luna = lt.estimate_cost("gpt-5.6-luna", usage)
    # 85,814 × $0.20/M + 5,557 × $1.20/M
    assert luna["usd"] == pytest.approx(0.0238, abs=1e-4), luna
    terra = lt.estimate_cost("gpt-5.6-terra", usage)
    assert terra["usd"] == pytest.approx(0.2383, abs=1e-4), terra
    assert terra["usd"] > luna["usd"] * 9, "output 是 input 的 6 倍價,差距要拉開"

    unknown = lt.estimate_cost("deepseek-v4-pro", usage)
    assert unknown["usd"] is None
    assert "未收錄" in unknown["basis"] and "deepseek-v4-pro" in unknown["basis"]
    # 日期後綴不該讓單價查不到
    assert lt.estimate_cost("gpt-5.6-luna-2026-02-16", usage)["usd"] == luna["usd"]
    # 沒有 usage 時不得憑空生一個 0
    assert lt.estimate_cost("gpt-5.6-luna", None)["usd"] is None
    assert lt.estimate_cost("gpt-5.6-luna", {"prompt_tokens": 5})["usd"] is None


def test_cached_input_tokens_are_read_from_either_providers_field():
    """兩家的快取欄位名不同,**擇一不相加**(同 P2-3 的教訓)。"""
    import llm_telemetry as lt

    assert lt.cached_tokens_of({"prompt_tokens_details": {"cached_tokens": 40}}) == 40
    assert lt.cached_tokens_of({"prompt_cache_hit_tokens": 25}) == 25
    assert lt.cached_tokens_of({}) is None
    # 兩個都有時只取一個(相加會憑空翻倍)
    both = {"prompt_tokens_details": {"cached_tokens": 40},
            "prompt_cache_hit_tokens": 25}
    assert lt.cached_tokens_of(both) in (40, 25)
    # 快取要讓成本標註出**兩個方向**:命中以全價計 → 此項偏高;
    # 寫入的費率未收錄且未計入 → 此項偏低。批#100 之前只講了偏高的那一半,
    # 而 2026-08-01 的實際帳單正好高於估計值 —— 只講一邊會誤導判讀方向。
    basis = lt.estimate_cost("gpt-5.6-luna", dict(
        prompt_tokens=1000, completion_tokens=100, **both))["basis"]
    assert "偏高" in basis, basis
    with_write = lt.estimate_cost("gpt-5.6-luna", {
        "prompt_tokens": 1000, "completion_tokens": 100,
        "prompt_tokens_details": {"cached_tokens": 0,
                                  "cache_write_tokens": 900}})["basis"]
    assert "偏低" in with_write, with_write


def test_cost_and_elapsed_accumulate_across_retries():
    """**浮點欄位要分開累加。**

    `isinstance(x, int)` 對 float 是 False —— 沿用 token 那套累加會讓成本與
    耗時**靜默不累加**,而那是「看起來有數字、其實只有最後一次」的失敗。
    """
    import llm_telemetry as lt

    # 生產路徑的第一次呼叫也會經過 merge(previous=None),所以這裡照同樣的形狀組
    first = lt.merge_same_role(None, lt.build_record(
        "openai", "gpt-5.6-luna", elapsed=12.0,
        usage={"prompt_tokens": 1000, "completion_tokens": 100}))
    assert first["calls"] == 1
    merged = lt.merge_same_role(first, lt.build_record(
        "openai", "gpt-5.6-luna", elapsed=8.0,
        usage={"prompt_tokens": 1000, "completion_tokens": 100}))
    assert merged["calls"] == 2
    assert merged["prompt_tokens"] == 2000
    assert merged["elapsed_seconds"] == pytest.approx(20.0)
    assert merged["estimated_cost_usd"] == pytest.approx(
        2 * first["estimated_cost_usd"]), "成本沒有累加 —— 帳單會對不上"


def test_a_timeout_leaves_a_record_instead_of_vanishing(monkeypatch):
    """批#95:**逾時是最需要診斷、卻唯一沒有紀錄的失敗。**

    例外原本從 `requests.post` 直接往外拋,manifest 完全看不到這次呼叫 ——
    不知道用了哪個模型、哪個推理強度、花了幾秒才放棄,而那三件事正是判斷
    「timeout 該不該調」的全部依據。2026-08-01 兩班都是這樣掉到 Gemini 的。
    """
    import requests

    import morning_report as mr

    monkeypatch.setattr(mr, "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(mr, "OPENAI_MODEL", "gpt-5.6-luna")
    monkeypatch.setattr(mr, "OPENAI_REASONING_EFFORT", "xhigh")

    def _boom(*_a, **_k):
        raise requests.exceptions.ReadTimeout("Read timed out.")

    monkeypatch.setattr(mr.requests, "post", _boom)
    saved = mr._RUN_MANIFEST.get("llm")
    mr._RUN_MANIFEST.pop("llm", None)
    try:
        with pytest.raises(requests.exceptions.ReadTimeout):
            mr._call_openai("hi")
        attempts = mr._RUN_MANIFEST.get("llm", {}).get("attempts") or []
        assert attempts, "逾時完全沒有留下紀錄"
        rec = attempts[-1]
        assert rec["provider"] == "openai" and rec["model"] == "gpt-5.6-luna"
        assert rec["requested_effort"] == "xhigh", "看不出是哪個強度逾時的"
        assert "ReadTimeout" in rec["error"]
        assert "elapsed_seconds" in rec, "沒有耗時就無從判斷 timeout 該不該調"
        # 失敗的呼叫**不得**被記成 writer
        assert "primary" not in mr._RUN_MANIFEST.get("llm", {})
    finally:
        if saved is None:
            mr._RUN_MANIFEST.pop("llm", None)
        else:
            mr._RUN_MANIFEST["llm"] = saved


def test_gemini_fallback_is_recorded_as_the_writer(monkeypatch):
    """批#95:**主供應商掛掉時,實際寫出這封信的是 Gemini。**

    Gemini 原本完全不記錄,所以 manifest 在最該說清楚「誰寫的」的時候是空的
    (2026-08-01 兩班都掉到 Gemini)。
    """
    import morning_report as mr

    monkeypatch.setattr(mr, "GEMINI_API_KEY", "token")
    monkeypatch.setattr(mr, "GEMINI_FALLBACK_MODELS", ["gemini-2.5-flash"])
    monkeypatch.setattr(mr, "_call_gemini_once", lambda _m, _p: "分析內文")
    saved = mr._RUN_MANIFEST.get("llm")
    mr._RUN_MANIFEST.pop("llm", None)
    try:
        assert mr._call_gemini("prompt") == "分析內文"
        slot = mr._RUN_MANIFEST.get("llm", {})
        assert slot.get("primary", {}).get("provider") == "gemini", slot
        assert slot["primary"]["model"] == "gemini-2.5-flash"
        # 抽取器用 Gemini 時不得佔用 writer 槽
        mr._RUN_MANIFEST.pop("llm", None)
        mr._call_gemini("prompt", role="extractor")
        slot = mr._RUN_MANIFEST.get("llm", {})
        assert "primary" not in slot and "extractor" in slot, slot
    finally:
        if saved is None:
            mr._RUN_MANIFEST.pop("llm", None)
        else:
            mr._RUN_MANIFEST["llm"] = saved


def test_structured_output_schema_is_derived_from_the_validator():
    """批#96(第九輪 P1-4):**schema 與驗證器不得是兩份手抄的名單。**

    手抄的必然結局是漂移:schema 允許的 event_type 多一個,`_validate_llm_events`
    就默默丟掉(manifest 看起來像「沒有事件」);少一個,模型就被迫亂填。
    這條把兩邊直接對起來 —— 加一個新的 event_type 而忘了同步,它會紅。
    """
    import news_events as ne

    schema = ne.llm_event_json_schema()
    item = schema["schema"]["properties"]["events"]["items"]

    assert schema["strict"] is True
    assert schema["schema"]["type"] == "object", \
        "OpenAI strict 模式的根節點必須是 object,不能直接回陣列"
    # strict 要求:additionalProperties=false,且每個欄位都要在 required 裡
    assert item["additionalProperties"] is False
    assert set(item["required"]) == set(item["properties"]), \
        "strict 模式下未列進 required 的欄位會被 API 拒絕"

    assert set(item["properties"]["event_type"]["enum"]) == set(ne._LLM_EVENT_TYPES)
    assert set(item["properties"]["direction"]["enum"]) == {-1, 0, 1}
    assert set(item["properties"]) <= set(ne._LLM_EVENT_FIELDS), \
        "schema 開了 _validate_llm_events 會剝掉的欄位 —— 兩邊都白做工"
    lifecycles = {v for v in item["properties"]["lifecycle"]["enum"] if v}
    assert lifecycles == set(ne._LLM_LIFECYCLES)
    # 數量上限刻意不寫進 schema(strict 模式對 maxItems 的支援我無法在此驗證),
    # 由 Python 側把關 —— 寫進去卻沒生效就是「以為擋住了」的假守衛
    assert "maxItems" not in item and "maxItems" not in \
        schema["schema"]["properties"]["events"]


def test_extractor_accepts_both_the_array_and_the_structured_object():
    """Structured Outputs 回的是 `{"events": [...]}`,不是裸陣列。

    舊的括號掃描「剛好」也挖得出來 —— 但那是巧合,而巧合會在某天有人在
    events 之前多放一個含 `[` 的欄位時安靜地壞掉。
    """
    from llm_postprocess import _parse_llm_event_json

    ev = {"entity": "2330", "event_type": "orders", "direction": 1, "title": "x"}
    assert _parse_llm_event_json(json.dumps([ev])) == [ev]
    assert _parse_llm_event_json(json.dumps({"events": [ev]})) == [ev]
    # 巧合會壞掉的那個形狀:events 之前先出現另一個含 `[` 的欄位
    tricky = json.dumps({"notes": ["a]b"], "events": [ev]})
    assert _parse_llm_event_json(tricky) == [ev], "括號掃描挖錯了陣列"
    assert _parse_llm_event_json('{"events": "not a list"}') == []
    assert _parse_llm_event_json("garbage") == []


def test_extractor_switches_provider_only_on_network_failure():
    """批#96:**對方機房的狀況不該直接等於今天沒有事件抽取。**

    2026-08-01 連續兩班的抽取器都在 `api.deepseek.com` 逾時(35 則進去 0 則
    出來),而它被釘在單一 provider。但只有網路層失敗才換人:HTTP 4xx 是我們
    的請求有問題,換一家會用同樣的錯誤參數再錯一次。
    """
    import llm_telemetry as lt

    def _has(env):
        return env in {"DEEPSEEK_API_KEY", "OPENAI_API_KEY"}

    assert lt.fallback_extractor_provider("deepseek", _has) == "openai"
    assert lt.fallback_extractor_provider("openai", _has) == "deepseek"
    # 沒有第二把金鑰就不換(換了只會拿到「缺金鑰」的錯誤蓋掉真正的原因)
    assert lt.fallback_extractor_provider(
        "deepseek", lambda e: e == "DEEPSEEK_API_KEY") == ""
    assert lt.fallback_extractor_provider("deepseek", lambda _e: False) == ""


def test_extractor_falls_back_to_another_provider_on_timeout(monkeypatch):
    """降級要真的接上去 —— 上面那條只驗策略,這條驗接線。"""
    import requests

    import morning_report as mr

    monkeypatch.setattr(mr, "EXTRACTOR_PROVIDER", "deepseek")
    monkeypatch.setattr(mr, "LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-d")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-o")
    monkeypatch.setattr(mr, "DEEPSEEK_API_KEY", "sk-d")
    monkeypatch.setattr(mr, "GEMINI_API_KEY", "")

    def _timeout(_p):
        raise requests.exceptions.ReadTimeout("Read timed out.")

    seen = {}

    def _openai(p, **kw):
        seen.update(kw)
        return json.dumps({"events": [
            {"entity": "2330", "event_type": "orders", "direction": 1,
             "title": "台積電接單"}]})

    monkeypatch.setattr(mr, "_call_deepseek_extractor", _timeout)
    monkeypatch.setattr(mr, "_call_openai", _openai)
    news = [{"source": "MOPS", "company_label": "2330", "title": "2330 orders",
             "importance": "critical", "published": "Tue, 02 Jul 2026 00:00:00 GMT"}]
    mr.call_llm_event_extractor(news, [])

    stat = mr._RUN_MANIFEST.get("llm_extractor", {})
    assert stat.get("fallback_from") == "deepseek", stat
    assert stat.get("fallback_to") == "openai", stat
    assert stat.get("valid") == 1, "降級之後應該真的抽到事件"
    # 降級過去時仍要帶 schema 與抽取器自己的額度基準
    assert seen.get("role") == "extractor"
    assert seen.get("response_format", {}).get("strict") is True
    assert seen.get("base_tokens") == mr.EXTRACTOR_ANSWER_TOKENS


def test_http_errors_do_not_trigger_a_provider_switch(monkeypatch):
    """**只有網路層失敗才換人。**

    批#96 的第一版測試沒有涵蓋這一半 —— 我把 `except (Timeout,
    ConnectionError)` 突變成 `except Exception`,測試全綠。那代表守衛只驗了
    「會換」,沒驗「不該換的時候不換」,而後者才是這個條件存在的理由:
    HTTP 4xx 是我們的請求有問題,換一家會用同樣的錯誤參數再錯一次
    —— 兩倍成本、同一個錯誤,而且第二個錯誤會蓋掉第一個。
    """
    import requests

    import morning_report as mr

    monkeypatch.setattr(mr, "EXTRACTOR_PROVIDER", "deepseek")
    monkeypatch.setattr(mr, "DEEPSEEK_API_KEY", "sk-d")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-d")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-o")
    monkeypatch.setattr(mr, "GEMINI_API_KEY", "")

    def _http_400(_p):
        resp = requests.Response()
        resp.status_code = 400
        raise requests.exceptions.HTTPError("400 Bad Request", response=resp)

    called = {"openai": 0}

    def _openai(_p, **_kw):
        called["openai"] += 1
        return "[]"

    monkeypatch.setattr(mr, "_call_deepseek_extractor", _http_400)
    monkeypatch.setattr(mr, "_call_openai", _openai)
    mr._RUN_MANIFEST.pop("llm_extractor", None)
    news = [{"source": "MOPS", "company_label": "2330", "title": "2330 orders",
             "importance": "critical", "published": "Tue, 02 Jul 2026 00:00:00 GMT"}]
    mr.call_llm_event_extractor(news, [])

    assert called["openai"] == 0, "HTTP 400 不該換 provider —— 換了只是再錯一次"
    stat = mr._RUN_MANIFEST.get("llm_extractor", {})
    assert "fallback_to" not in stat, stat


def test_unknown_models_get_a_conservative_output_cap():
    """批#99(第九輪 P2-2):**登錄簿要說得出數字從哪來。**

    原本一個 `DEFAULT_MAX_OUTPUT = 128_000` 套所有模型 —— 那是把一個模型的
    規格當成整族的契約,而公開資料沒有保證那件事。

    不對稱在於後果:額度給得比真實上限**低**,最壞是輸出被截斷
    (有 `finish_reason=length` 可偵測、有減量重試);給得比真實上限**高**,
    是當場 400、整份分析作廢。所以未收錄的模型取保守值。
    """
    import llm_telemetry as lt

    known, src = lt.max_output_for("gpt-5.6-luna")
    assert known == 128_000 and "MODEL_LIMITS" in src
    # 日期後綴不該讓登錄簿查不到
    assert lt.max_output_for("gpt-5.6-luna-2026-02-16")[0] == known
    unknown, why = lt.max_output_for("gpt-7-imaginary")
    assert unknown == lt.UNKNOWN_MODEL_MAX_OUTPUT < known
    assert "未收錄" in why, "沒有出處這件事必須說出來"
    # 額度會被夾在該模型的上限內
    assert lt.output_cap("xhigh", 7000, model="gpt-5.6-luna") == 98_000
    assert lt.output_cap("xhigh", 7000, model="gpt-7-imaginary") == \
        lt.UNKNOWN_MODEL_MAX_OUTPUT
    # 每一列都要有出處可查(空的登錄簿會讓上面兩條都虛過)
    assert lt.MODEL_LIMITS, "登錄簿是空的"
    for name, spec in lt.MODEL_LIMITS.items():
        assert spec["max_output"] > 0 and spec["context"] > 0, name


def test_the_shadow_cannot_send_a_different_prompt_than_the_primary():
    """批#99(第九輪 P2-5):**影子把同一份 prompt 交給第二家廠商。**

    那是一個新的資料揭露決定 —— 但影子必須送同一份才比較得出東西,
    所以正確的做法不是遮蔽(遮了就不是同一份),而是把「同一份」變成
    **結構上的不變式**:prompt 由 `run_comparison` 交給 `call_shadow`,
    呼叫端無從偷偷換掉。這樣主 prompt 既有的隱私防線(R15b、讀者身分、
    持股不落地)全部自動涵蓋影子,不必維護第二套會漂移的規則。

    詞彙掃描式的「敏感詞遮蔽」在這裡是錯的設計:持股代號在行情區塊本來
    就會出現,掃描不是永遠誤擋、就是要開一堆例外把自己掏空。
    """
    import inspect

    import llm_shadow as ls

    import morning_report as mr

    sig = inspect.signature(ls.run_comparison)
    assert "prompt" in sig.parameters, "run_comparison 沒有收下 prompt"

    seen = {}

    def _call(p):
        seen["prompt"] = p
        return "影子:立場中性"

    # 夠長才驗得出「被截斷後送出」—— 短字串會讓 `prompt[:10]` 這種突變虛過,
    # 而我第一版正是用 4 個字的 prompt,截斷突變照樣全綠。
    long_prompt = "P-原文-" + ("行情與新聞內文 " * 40)
    out = ls.run_comparison(
        primary_model="a", primary_text="主分析:立場中性", prompt=long_prompt,
        shadow_model="b", call_shadow=_call, today="2026-08-01",
        ledger_path=None, read_ledger=lambda _p: [],
        write_ledger=lambda _p, _l: None,
        extract_stance=lambda _t: {"label": "中性", "score": 0},
        extract_summary=lambda t: t, elapsed_timer=lambda: 0.0,
        log=lambda _m: None)
    assert seen["prompt"] == long_prompt, "影子收到的不是主分析那一份"
    assert out["today"]["prompt_sha"] == ls.prompt_fingerprint(long_prompt)
    assert out["today"]["prompt_sha"] != ls.prompt_fingerprint("別的 prompt")

    # 接線端。**用 AST 看 body,不是看簽名。**
    # 我第一版只斷言原始碼裡有 `def _call(p)` —— 然後把 body 從 `p` 改回外層的
    # `prompt`,測試照樣全綠。簽名對了不代表用的是那個參數,而「在 body 裡用
    # 外層變數」正是這個結構保證唯一會失效的方式。
    import ast
    import textwrap

    src = inspect.getsource(mr._run_llm_shadow)
    tree = ast.parse(textwrap.dedent(src))
    call_fn = next((n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef) and n.name == "_call"), None)
    assert call_fn is not None, "找不到影子的呼叫函式 —— 接線改了,契約要同步"
    params = {a.arg for a in call_fn.args.args}
    assert params, "影子的呼叫函式沒有收 prompt 參數"
    used = {n.id for n in ast.walk(call_fn)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    assert "prompt" not in used, (
        "影子的呼叫函式在 body 裡用了外層的 `prompt` —— "
        "那就繞過了「送出去的必定是 run_comparison 交進來的那一份」")
    assert "prompt=prompt" in src, "沒有把主分析的 prompt 交給 run_comparison"


def test_cost_summary_says_when_it_is_incomplete():
    """批#100:**逾時的呼叫照樣計費,但沒有 usage 可讀。**

    2026-08-01 實際帳單約 $0.1,而 manifest 只記到 $0.056 —— 差額主要是
    08:57 那班逾時 75 秒的呼叫:server 端已經生成的 token 不會因為 client
    放棄就不算,可是它進不了加總。

    只報一個看起來精確的總額,會讓人以為帳單對不上是別的原因。
    **成本估算若只在成功時準確,它在最該看的時候(一直逾時的那幾天)最不準。**
    """
    import llm_telemetry as lt

    clean = lt.run_cost_summary({
        "primary": {"model": "gpt-5.6-luna", "estimated_cost_usd": 0.05,
                    "calls": 1},
        "extractor": {"model": "gpt-5.6-luna", "estimated_cost_usd": 0.004,
                      "calls": 1}})
    assert clean["total_usd"] == pytest.approx(0.054)
    assert clean["measured_calls"] == 2
    assert "incomplete" not in clean, clean

    with_timeout = lt.run_cost_summary({
        "primary": {"model": "gpt-5.6-luna", "estimated_cost_usd": 0.05,
                    "calls": 1},
        "attempts": [{"role": "primary", "provider": "openai",
                      "error": "ReadTimeout", "billable_unmeasured": True}]})
    assert with_timeout["unmeasured_billable_calls"] == 1
    assert "incomplete" in with_timeout
    assert "計費" in with_timeout["incomplete"]

    # 未收錄單價的模型也要說(DeepSeek 目前就是)
    mixed = lt.run_cost_summary({"primary": {"model": "deepseek-v4-pro",
                                             "prompt_tokens": 100, "calls": 1}})
    assert "deepseek-v4-pro" in mixed.get("incomplete", "")


def test_cache_write_tokens_are_recorded_and_flagged_in_the_basis():
    """批#100:`cache_write_tokens` 原本完全沒被記。

    2026-08-01 實測回應是 `{cached_tokens: 0, cache_write_tokens: 93191}` ——
    我只記了前者(當天是 0),於是「快取」在 manifest 裡看起來完全沒發生。
    寫入通常另有費率,而那個費率我沒有出處 → 估計值因此**偏低**,要講出來。
    """
    import llm_telemetry as lt

    usage = {"prompt_tokens": 93_194, "completion_tokens": 27_933,
             "prompt_tokens_details": {"cached_tokens": 0,
                                       "cache_write_tokens": 93_191},
             "completion_tokens_details": {"reasoning_tokens": 23_095}}
    assert lt.cache_write_tokens_of(usage) == 93_191
    rec = lt.build_record("openai", "gpt-5.6-luna", usage=usage)
    assert rec["cache_write_tokens"] == 93_191
    assert rec["reasoning_tokens"] == 23_095
    # 這一天的實測成本:input 93,194×$0.20/M + output 27,933×$1.20/M
    assert rec["estimated_cost_usd"] == pytest.approx(0.0522, abs=1e-4)
    assert "偏低" in rec["cost_basis"], "沒說出估計值可能低於帳單"
    # 重試要累加,否則與帳單對不上
    merged = lt.merge_same_role(lt.merge_same_role(None, rec), rec)
    assert merged["cache_write_tokens"] == 2 * 93_191
