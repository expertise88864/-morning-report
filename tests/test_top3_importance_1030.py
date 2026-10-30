"""使用者 2026-10-30:「昨夜三大重點為何是富邦人壽發債、台灣人壽小額投資、台積電買廠房,
不是 Alphabet、Intel?」「一堆發行資本、小規模投資案」「不想看藥華藥,除非有新藥」
「以前備援機制的還比現在好」。四項過濾 + 改回備援為主時不再天天報「未走特化」。"""
import datetime as dt

import analysis_origin as ao
import announcement_filter as af
import news_display_quality as q
import run_quality as rq

TODAY = dt.date(2026, 10, 30)
CAPS = {"2881": 1.0e12, "2891": 6.0e11, "2330": 3.0e13}


def test_financing_is_never_a_news_card_whatever_the_amount():
    for title in ("代子公司富邦人壽公告董事會決議發行次順位公司債新台幣400億元",
                  "代子公司富邦人壽公告辦理現金增資新台幣300億元",
                  "代子公司台灣人壽公告投資某某私募股權基金",
                  "代子公司台灣人壽公告參與某某公司現金增資認購普通股",
                  "本公司董事會決議辦理私募普通股"):
        assert af.routine({"code": "2881", "title": title, "summary": "金額新台幣400億元"}, CAPS), title


def test_investor_conference_schedules_are_not_news():
    for title in ("本公司受邀參加摩根士丹利舉辦之投資人會議", "公告本公司將召開法人說明會",
                  "本公司應邀參加法說會"):
        assert af.routine({"code": "2330", "title": title}, CAPS), title
    # 法說的實際內容仍照常(PR #33 外審:沒有「營收」字眼的簡報內容也要留)
    assert not af.routine({"code": "2330", "title": "公告本公司法說會簡報內容:第3季合併營收"}, CAPS)
    assert not af.routine({"code": "2330", "title": "公告本公司法說會簡報內容：明年先進封裝產能倍增"}, CAPS)


def test_plant_and_land_purchases_are_judged_by_amount_like_equipment():
    small = {"code": "2330", "title": "代子公司公告取得廠房", "summary": "交易總金額:新台幣50億元"}
    big = {"code": "2891", "title": "公告本公司取得土地", "summary": "交易總金額:新台幣200億元"}   # 3.3% 市值
    assert af.routine(small, CAPS) and not af.routine(big, CAPS)


def test_biotech_only_major_progress_reaches_the_analysis():
    universe = [{"code": "6446", "name": "藥華藥", "industry": "生技醫療業", "market_cap": 3.0e11},
                {"code": "2330", "industry": "半導體業", "market_cap": 3.0e13}]
    news = [{"title": "藥華藥9月營收年增四成", "entities": ["6446"]},
            {"title": "藥華藥新藥獲美國FDA核准上市", "entities": ["6446"]},
            {"title": "台積電9月營收創新高", "entities": ["2330"]},
            {"title": "Fed 維持利率不變 - CNBC"},
            {"title": "藥華藥股價走揚", "cnyes_stocks": ["6446"]},          # PR #33 外審:鉅亨欄位
            {"title": "藥華藥法人目標價上調", "company_label": "藥華藥"}]
    mops = [{"code": "6446", "title": "公告本公司董事會通過資金運用計畫"},
            {"code": "6446", "title": "公告本公司新藥第三期臨床試驗解盲成功"}]
    kept, quotes = q.current_inputs(news, {"TW_MOPS": mops}, TODAY, universe)
    assert [n["title"] for n in kept] == ["藥華藥新藥獲美國FDA核准上市", "台積電9月營收創新高",
                                          "Fed 維持利率不變 - CNBC"]
    assert [m["title"] for m in quotes["TW_MOPS"]] == ["公告本公司新藥第三期臨床試驗解盲成功"]


def _legacy_manifest(resolved):
    return {"llm": {"analysis_origin": ao.LEGACY_PRIMARY,
                    "config": {"sources": {"LLM_PRIMARY_PROMPT_PROFILE": {
                        "source": "repo_variable", "resolved": resolved}}},
                    "watch_due_at_start": {"count": 1, "unselected_count": 0, "cases": [
                        {"watch_id": "w1", "created": "2026-10-29", "deadline": "2026-10-30"}],
                        "unselected_cases": []},
                    "watch_expired_unreviewed": 2},
            "degraded_steps": ["recap:not_previous_session:2026-10-28"]}


def test_choosing_the_legacy_path_is_not_reported_as_a_degradation():
    chosen = _legacy_manifest(ao.LEGACY_PROFILE)
    assert ao.legacy_by_choice(chosen)
    codes = {f["code"] for f in rq.assess(chosen)}
    for code in ("analysis_not_specialized", "watch_due_unreviewed", "watch_expired_unreviewed",
                 "recap_not_previous_session"):
        assert code not in codes, code
    # 設定仍是特化、卻沒走特化:照舊要報
    unchosen = _legacy_manifest("luna56_xhigh_v1")
    assert not ao.legacy_by_choice(unchosen)
    codes = {f["code"] for f in rq.assess(unchosen)}
    assert {"analysis_not_specialized", "watch_due_unreviewed"} <= codes
    # 特化失敗才落到既有路徑:不是選的
    failed = _legacy_manifest(ao.LEGACY_PROFILE)
    failed["llm"]["analysis_origin"] = ao.LEGACY_AFTER_LUNA_FAILURE
    assert not ao.legacy_by_choice(failed)
    for bad in (None, [], "x", {"llm": "x"}, {"llm": {"config": []}}):
        assert not ao.legacy_by_choice(bad)
    # PR #33 外審:選了備援,帳本壞掉仍是缺陷
    broken = _legacy_manifest(ao.LEGACY_PROFILE)
    broken["llm"]["watch_due_at_start"] = {"error": "invalid_ledger"}
    assert "watch_due_scan_failed" in {f["code"] for f in rq.assess(broken)}
