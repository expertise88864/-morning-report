# -*- coding: utf-8 -*-
"""2026-08-26 外審:三個 P2,其中一個已經污染生產狀態。

`state/event_timeline.json` 有 `geopolitical:sanction:Oil:2026-08`,
標題是「Oil Falls Further Despite Fresh U.S. Sanctions on Iran」——
Oil 是**被影響的資產**,Iran 才是制裁對象。字面提及守衛救不了:Oil 確實
出現在標題裡,它證明的是「這則新聞談到 Oil」,不是「Oil 被制裁」。
"""
import event_identity as eid
import llm_telemetry as lt
import state_migrations as sm

_PROD_TITLE = ("Oil Falls Further Despite Fresh U.S. Sanctions on Iran "
               "-- Market Talk - Moomoo")


def test_the_affected_asset_does_not_become_the_sanction_target():
    """用生產那一則的原文。"""
    assert eid.action_object("sanction", _PROD_TITLE, ["Oil"]) == "伊朗"
    # 外審點名的另外兩個形狀
    assert eid.action_object(
        "sanction", "Gold falls despite sanctions on Russia", ["Gold"]) == "俄羅斯"
    assert eid.action_object(
        "sanction", "美國對伊朗制裁未見明顯衝擊", ["伊朗"]) == "伊朗"
    # 中文的後置形式(生產也有這種標題)
    assert eid.action_object(
        "sanction", "〈能源盤後〉無懼美制裁伊朗 油市押注重返談判",
        ["Oil"]) == "伊朗"


def test_the_target_is_the_first_name_after_the_marker():
    """r1 外審:先前依**別名長度**挑,`sanctions on Iran and Saudi Arabia`
    會挑到沙烏地 —— 而受詞位置的語意是「動作詞後面**第一個**」。"""
    assert eid.action_object(
        "sanction", "sanctions on Iran and Saudi Arabia", ["Oil"]) == "伊朗"


def test_an_ascii_alias_needs_word_boundaries():
    """r1 外審:`us` 先前是裸子字串比對,會在 `cause` 裡命中變成美國。"""
    assert eid.action_object(
        "sanction", "sanctions on Nvidia because of cause",
        ["NVIDIA"]) == "NVIDIA"


def test_the_subject_itself_is_the_patient_in_a_passive_clause():
    """**連六輪外審的收斂結果。** 前五版都想從中文語法推被動:掃前面的字
    會被「油價**受**壓」誤判、用空白分句敗在沒有空白的標題、只認 `遭/被`
    又漏掉 `承受` 這種語素、而「遭美國制裁**中國**譴責」的下一子句主詞
    還會被讀成受詞 —— 每收窄一次就長出新的邊界情形。

    判準改成用**我們已經有的主體**:主體自己緊接在
    `<遭|被|受><施加方>制裁` 前面時,它就是受詞,後面那個是下一句的主詞。
    這不需要剖析中文,只需要問一件我們答得出來的事。"""
    for title in ("國際刑事法院遭美國制裁中國譴責",
                  "國際刑事法院受美國制裁中國譴責",
                  "國際刑事法院遭美國制裁"):
        assert eid.action_object("sanction", title,
                                 ["國際刑事法院"]) == "國際刑事法院", title
    # 主體**不在**被動位置時,後置形式照樣剖析得出來
    for title in ("油市承受美國制裁伊朗衝擊", "油價受壓美國制裁伊朗",
                  "〈能源盤後〉無懼美制裁伊朗 油市押注"):
        assert eid.action_object("sanction", title, ["Oil"]) == "伊朗", title
    # **「緊接在前面」要真的是結尾**(r7 外審):子字串比對讓
    # 「台積電**承**受美國制裁伊朗衝擊」的 head(`台積電承`)也算命中,
    # 於是明確的 `制裁伊朗` 被丟掉 —— 宣稱與實作差的那一層正好是這條
    # 規則的全部內容。
    assert eid.action_object("sanction", "台積電承受美國制裁伊朗衝擊",
                             ["台積電"]) == "伊朗"
    # 沒有被動標記的主動句不受影響(主體出現在動作詞前面也一樣)
    assert eid.action_object("sanction", "美國制裁伊朗",
                             ["美國", "伊朗"]) == "伊朗"


def test_the_migration_trace_survives_to_the_manifest():
    """r1 外審:痕跡先前寫在 `event_identity` 底下,而那個 dict 在事件
    迴圈之後會被**整個重新指派** —— 遷移真的跑了卻查不到。"""
    import io as _io
    import re
    from pathlib import Path

    import morning_report as mr
    src = _io.open(Path(mr.__file__), encoding="utf-8").read()
    i = src.index("migrate_sanction_objects(state)")
    seg = src[i:i + 700]
    m = re.search(r'setdefault\("([a-z_]+)", \{\}\)\[\s*"sanction_objects_renamed"',
                  seg)
    assert m and m.group(1) == "state_migrations", seg[:400]


def test_a_company_target_is_still_kept():
    """**前人刻意建立的要求**(第二輪外審 F4 / 法域過濾那條):制裁可以
    直接針對公司,而公司不在 `CANONICAL_SUBJECTS` 裡。更嚴的「主體不在
    受詞位置就 fail closed」實測會讓「美方宣布制裁該實體」全部退成
    UNKNOWN,同月所有這類案子共用一把鑰匙 —— 那正是加對象要修的
    over-merge。所以判準只在**證明得了是別人**時才推翻。"""
    assert eid.action_object("sanction", "美方宣布制裁該實體",
                             ["某資安公司"]) == "某資安公司"
    assert eid.action_object("sanction", "美國宣布制裁該實體",
                             ["美國", "甲公司"]) != eid.action_object(
        "sanction", "美國宣布制裁該實體", ["美國", "乙公司"])


def test_the_polluted_production_row_is_migrated():
    """改判準要配遷移 —— 舊鍵留在 state 裡就是一條永遠接不上的孤立線。"""
    tl = {"geopolitical:sanction:Oil:2026-08": {
              "latest_title": _PROD_TITLE, "object": "Oil", "entity": "Oil",
              "subjects": ["Oil"], "action": "sanction", "days": 1,
              "event_type": "geopolitical"},
          "geopolitical:sanction:伊朗:2026-08#89ec92": {
              "latest_title": "US threatened new sanctions against Iran",
              "object": "伊朗", "subjects": ["伊朗"], "action": "sanction",
              "days": 3}}
    out, renamed = sm.migrate_sanction_objects(tl)
    assert renamed == ["geopolitical:sanction:Oil:2026-08"], renamed
    assert not [k for k in out if ":sanction:Oil:" in k], sorted(out)
    moved = out["geopolitical:sanction:伊朗:2026-08"]
    # **列也要改**:只改鍵的話,同日重跑會把舊對象讀回活躍時間軸
    assert moved["object"] == "伊朗" and moved["entity"] == "伊朗", moved
    # 可重入
    again, ren2 = sm.migrate_sanction_objects(out)
    assert ren2 == [] and set(again) == set(out), ren2
    # 算不出對象的不動(不因為剖析不出來就打掉對得上的事件)
    keep = {"geopolitical:sanction:某公司:2026-08": {
        "latest_title": "美方宣布制裁該實體", "object": "某公司",
        "subjects": ["某公司"], "action": "sanction"}}
    kept, ren3 = sm.migrate_sanction_objects(keep)
    assert ren3 == [] and set(kept) == set(keep)


def test_the_identity_schema_version_moved_with_the_formula():
    """識別公式改了就要跳版 —— 否則舊記錄會被當成「已經是新版」而跳過。"""
    assert eid.IDENTITY_SCHEMA_VERSION >= 13


def test_403_is_not_assumed_to_be_a_global_credential_failure():
    """外審 P2:`403 → auth` 讓下游做四件事(不重試、不換模型、不計費、
    叫使用者去換金鑰),而 DeepSeek 官方錯誤表**沒有列 403**
    (400/401/402/422/429/500/503)。403 也可能是某個 endpoint 或模型的
    權限問題 —— 那時金鑰完全有效,而系統會停掉所有還能用的模型並告訴
    使用者金鑰壞了。401/402 有官方契約支撐,403 沒有。"""
    assert lt.refusal_reason(_err(401)) == "auth"
    assert lt.refusal_reason(_err(402)) == "payment"
    assert lt.refusal_reason(_err(403)) != "auth", (
        "403 被無條件當成帳號級金鑰失效,而那不是 provider 契約推得出來的")
    assert lt.refusal_reason(_err(500)) == ""


class _R:
    def __init__(self, code):
        self.status_code = code


def _err(code):
    e = Exception(f"HTTP {code}")
    e.response = _R(code)
    return e


def test_an_account_refusal_does_not_trigger_a_second_same_account_call(
        monkeypatch):
    """外審 P2:402 修正只做到 `_call_deepseek()` 的 model loop。特化路徑
    失敗後仍會落 legacy,而 legacy 用**同一個帳號**再打一次 DeepSeek ——
    Responses 沒錢不代表 Chat 會突然有錢,那次請求的成功機率是零,卻照樣
    吃掉 run budget。

    **行為測試,從 orchestration 進去**,不是直接測 leaf helper(上一批
    就是因為只測 leaf 而讓守衛留在走不到的分支裡)。"""
    import morning_report as mr
    import requests

    posts = []

    class _R402:
        status_code = 402
        text = '{"error":{"message":"Insufficient Balance"}}'

        def raise_for_status(self):
            err = requests.exceptions.HTTPError("402 Payment Required")
            err.response = self
            raise err

    def _post(url, *a, **k):
        posts.append(str(url))
        return _R402()

    monkeypatch.setattr(requests, "post", _post)
    monkeypatch.setattr(mr, "_llm_sleep", lambda *a, **k: None)
    monkeypatch.setattr(mr, "DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(mr, "LLM_PROVIDER", "deepseek")
    mr._DEGRADED_STEPS.clear()
    out = mr._call_llm_analysis_impl(
        {"QQQ": {"close": 1.0}}, {}, {},
        [{"source": "CNBC", "title": "Oil drops more than 3%"}], [], {})
    # 緊急備援,而且提示是「儲值」不是「稍後重跑」
    assert "原始新聞清單" in out and "重跑不會好" in out, out[:300]
    assert "Oil drops more than 3%" in out
    # **只打一次**:不重試、不換模型、不落 legacy 再打一次
    assert len(posts) == 1, posts
    assert any("provider_refused" in d for d in mr._DEGRADED_STEPS), \
        mr._DEGRADED_STEPS


def test_a_normal_failure_still_falls_back_to_legacy():
    """反向:一般的語意/語法/內容失敗仍要落 legacy —— 那是 legacy 存在
    的理由,不可以跟著關掉。"""
    import inspect

    import morning_report as mr
    src = inspect.getsource(mr._call_llm_analysis_impl)
    i = src.index("_refused = _lt.refusal_reason(e)")
    seg = src[i:i + 500]
    assert "if _refused:" in seg and "return _fallback_analysis_text" in seg
    # 判斷是**條件式**的,不是無條件跳過 legacy
    assert seg.count("return _fallback_analysis_text") == 1, seg
