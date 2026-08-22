# -*- coding: utf-8 -*-
"""2026-08-22 外審:公司持久身分(P1)+ migration 留下 stale object(P2)。

P1 的前提被生產反證:`state/event_timeline.json` 裡有
`export_controls:輝達:2026-08` —— 公司中文名早就是持久身分,而
「公司鍵慣例是代號、不收斂」讓輝達/NVIDIA/NVDA 成為三條 lifecycle。
P2:migration 重算了鍵裡的對象段,卻沒同步 row 的 `object`,生產現況是
`key=…:國際刑事法院:…` 配 `object="International Criminal C"`,而消費端
優先信任存下來的 object → 中文續報接不回世系,且鍵已正規化使它
**永遠不再進修補分支**。
"""
import io
from pathlib import Path

import entity_alias as ea
import event_identity as ei
import news_events as ne
import state_migrations as sm
import subject_identity as si


# ---------------------------------------------------------------- P1

def test_company_identity_collapses_in_persistent_key():
    """同一家公司的每一種寫法必須算出同一把持久鍵。"""
    def key(name):
        return ne._event_timeline_key({"entity": name,
                                       "event_type": "export_controls",
                                       "date": "2026-08-22"})
    for group in (("輝達", "NVIDIA", "Nvidia", "NVDA"),
                  ("台積電", "TSMC", "2330", "台積"),
                  ("蘋果", "Apple", "AAPL"),
                  ("聯發科", "MediaTek", "2454")):
        keys = {key(n) for n in group}
        assert len(keys) == 1, f"{group} 裂成 {keys}"
    # 不同公司**不得**被併(誤併比漏併危險)
    assert key("台積電") != key("聯電")
    assert key("長榮") != key("長榮航"), "2603 海運與 2618 空運是兩家"


def test_subject_set_does_not_count_one_company_three_times():
    """主體集合去重也走同一權威 —— 否則 object_signature 跟著被污染。"""
    assert ei.canonical_subjects(["NVIDIA", "輝達", "NVDA"]) == ["輝達"]


def test_lifecycle_continues_across_company_spellings():
    """外審指定 regression:歷史 輝達/rumor + 今日 NVIDIA/confirmed
    → previous_lifecycle 必須是 rumor,拿 rumor→confirmed 的過渡權重
    (0.65)而不是 confirmed 的 full base weight(1.0)。"""
    history = [{"session_date": "2026-08-21", "structured_events": [{
        "entity": "輝達", "event_type": "export_controls",
        "title": "輝達傳遭擴大出口管制", "lifecycle": "rumor",
        "event_schema": ne.EVENT_SCHEMA_VERSION}]}]
    today = [{"entity": "NVIDIA", "event_type": "export_controls",
              "title": "NVIDIA export curbs confirmed by Commerce Dept",
              "lifecycle": "confirmed"}]
    out = ne.apply_event_timeline(history, today)
    assert out[0]["previous_lifecycle"] == "rumor", out[0]
    assert out[0]["lifecycle_weight"] == 0.65, out[0]
    # 同一樁事不得在 event-study 裡被算成第二個獨立事件
    hist_id = ne._event_instance_id(history[0]["structured_events"][0])
    assert ne._event_instance_id(today[0]) == hist_id


def test_generation_bridge_uses_machine_identity():
    """跨世代橋接鍵也不得原樣帶 entity(否則連退路都接不上)。"""
    a = ne._event_generation_bridge_key(
        {"entity": "NVIDIA", "event_type": "orders", "title": "Same headline"})
    b = ne._event_generation_bridge_key(
        {"entity": "輝達", "event_type": "orders", "title": "Same headline"})
    assert a == b and a, (a, b)


def test_identity_name_is_the_persistence_authority():
    """接線:三個持久化入口都走同一支(沒接上等於不存在)。"""
    assert si.identity_name("NVDA") == "輝達"
    assert not hasattr(si, "cross_language_display"), "舊的半套權威還在"
    ne_src = io.open(Path(ne.__file__), encoding="utf-8").read()
    assert "_si.identity_name(entity)" in ne_src
    ei_src = io.open(Path(ei.__file__), encoding="utf-8").read()
    assert "identity_name as canonical_subject" in ei_src


#: 組代表 = 持久鍵的一部分。**重排組內順序會靜默改寫全部 state**,
#: 所以代表寫法是凍結契約,不是隨手可調的顯示偏好。
_FROZEN_REPRESENTATIVES = {
    "NVDA": "輝達", "TSMC": "台積電", "2330": "台積電", "AAPL": "蘋果",
    "MSFT": "微軟", "AMD": "超微", "INTC": "英特爾", "MU": "美光",
    "2317": "鴻海", "2454": "聯發科", "2303": "聯電", "3711": "日月光",
    "Fed": "聯準會", "2603": "長榮", "2609": "陽明", "2887": "台新新光金",
}


def test_alias_group_representatives_are_frozen():
    for alias, rep in _FROZEN_REPRESENTATIVES.items():
        assert ea.canonical(alias) == rep, (
            f"{alias} 的組代表變成 {ea.canonical(alias)} —— 這是持久鍵,"
            "改它要配一次 state 遷移")


def test_no_alias_belongs_to_two_groups():
    """`_INDEX.setdefault` 讓重複宣告靜默失效(改到後面那份不會有任何
    變化)—— 表裡曾經真的有兩組逐字重複。"""
    seen = {}
    for i, grp in enumerate(ea.ALIAS_GROUPS):
        for name in grp:
            assert str(name) not in seen, (
                f"{name} 同時在第 {seen[str(name)]} 與第 {i} 組")
            seen[str(name)] = i


# ---------------------------------------------------------------- P2

#: 生產現況(state/event_timeline.json):鍵已正規化、object 仍是遷移前
#: 的英文截斷值。
_ICC_ROW = {"entity": "國際刑事法院", "subjects": ["國際刑事法院"],
            "object": "International Criminal C", "days": 3,
            "latest_title": "國際刑事法院遭美國制裁",
            "latest_summary": "美國宣布制裁國際刑事法院官員"}


def test_migration_repairs_stale_object_on_already_canonical_key():
    """鍵不用改的列也要修 object —— 否則它永遠不進修補分支。"""
    tl = {"geopolitical:sanction:國際刑事法院:2026-08": dict(_ICC_ROW)}
    out, renamed, repaired = sm.migrate_cross_language_timeline_keys(tl)
    row = out["geopolitical:sanction:國際刑事法院:2026-08"]
    assert row["object"] == "國際刑事法院", row
    assert renamed == [], "鍵沒改卻報成改名"
    assert repaired == ["geopolitical:sanction:國際刑事法院:2026-08"]


def test_migration_object_repair_is_idempotent():
    tl = {"geopolitical:sanction:國際刑事法院:2026-08": dict(_ICC_ROW)}
    out, _, _ = sm.migrate_cross_language_timeline_keys(tl)
    out2, renamed2, repaired2 = sm.migrate_cross_language_timeline_keys(out)
    assert out2 == out and renamed2 == [] and repaired2 == []


def test_lineage_reconnects_after_object_repair():
    """功能面:修好之後,今天的中文續報要接得回這條世系(修之前接不上)。"""
    key = "geopolitical:sanction:國際刑事法院:2026-08"
    stale = {key: dict(_ICC_ROW)}
    fixed, _, _ = sm.migrate_cross_language_timeline_keys(stale)

    def hit(timeline):
        return ei.match_days(
            [dict(r, key=k) for k, r in timeline.items()],
            ["國際刑事法院"], ["美國再度制裁國際刑事法院"],
            summary="美國宣布制裁國際刑事法院官員")

    assert hit(fixed) > 0, "修好 object 後仍接不回世系"
    assert hit(stale) == 0, "反例不成立:修之前就已經接得上"


def test_migration_separates_rename_from_repair():
    """兩種修正要分開報,manifest 才不會宣稱改了沒改的鍵。"""
    tl = {"geopolitical:Pentagon:2026-08": {"entity": "Pentagon",
                                            "subjects": ["Pentagon"],
                                            "days": 2},
          "geopolitical:sanction:國際刑事法院:2026-08": dict(_ICC_ROW)}
    out, renamed, repaired = sm.migrate_cross_language_timeline_keys(tl)
    assert renamed == ["geopolitical:Pentagon:2026-08"]
    assert repaired == ["geopolitical:sanction:國際刑事法院:2026-08"]
    assert "geopolitical:五角大廈:2026-08" in out


def test_caller_records_both_outcomes():
    """接線:呼叫端要收三元組並分別留痕。"""
    import morning_report as mr
    src = io.open(Path(mr.__file__), encoding="utf-8").read()
    i = src.index("_sm.migrate_cross_language_timeline_keys(")
    seg = src[max(0, i - 200):i + 900]
    assert "_tl_lang, _tl_obj" in seg, "呼叫端沒收 repaired"
    assert "timeline_object_repaired" in seg, "object 修正沒有 manifest 痕跡"


def test_existing_code_form_story_keys_are_not_orphaned():
    """**本次改動自己創造的風險**:生產 `state/story_ledger.json` 有 9,846
    列、鍵是 `e:2330|l:earnings|2026q3`,而今天算出來的是 `e:台積電|…`。
    若歸屬只看鍵,所有公司線會在同一天被孤立(比原缺陷更糟)。

    我原本判斷 `_match_open_story`(主體相似度)會接住 —— **自測反例證明
    那是錯的**:標題差得夠遠時分數不過門檻,當場裂成兩條。所以遷移是
    必要配套,而這條測試把兩邊都釘住。
    """
    import story_ledger as sl
    vocab = {"2330": "台積電"}
    item = {"entity": "2330", "entity_name": "台積電",
            "title": "〈台積電法說〉AI 營收三年拚逾 10 億美元",
            "link": "https://a/1", "source_name": "鉅亨台股",
            "event_type": "earnings", "published": "2026-07-30T08:00:00+00:00"}
    led = sl.update_ledger([], [dict(item)], "2026-07-30", vocab)
    assert len(led) == 1
    # 遷移前的生產列:**只有主體段不同**(反例要只靠被測那一段分勝負)
    legacy_key = led[0]["key"].replace("e:台積電|", "e:2330|", 1)
    assert legacy_key != led[0]["key"]
    legacy = dict(led[0], key=legacy_key)
    migrated, renamed = sm.migrate_company_story_keys([legacy])
    assert renamed == [legacy_key] and migrated[0]["key"] == led[0]["key"]
    after = sl.update_ledger(migrated, [dict(
        item, link="https://a/2",
        title="台積電法說會確認先進封裝擴產")], "2026-07-31", vocab)
    assert len(after) == 1, f"被孤立、另開一條:{[r['key'] for r in after]}"
    # 反例要成立:**不跑遷移**的同一份輸入必須真的裂成兩條
    assert len(sl.update_ledger([dict(legacy)], [dict(
        item, link="https://a/3",
        title="台積電法說會確認先進封裝擴產")], "2026-07-31", vocab)) == 2


def test_story_key_migration_never_merges_two_lines():
    """撞鍵時原地不動(合併軌跡點是另一種語意,原地不動不丟資料);可重入。"""
    a = {"key": "e:2330|l:earnings|2026q3", "entity": "2330", "timeline": [1]}
    b = {"key": "e:台積電|l:earnings|2026q3", "entity": "2330",
         "timeline": [1, 2]}
    rows, renamed = sm.migrate_company_story_keys([dict(a), dict(b)])
    assert renamed == [] and len(rows) == 2, rows
    assert {r["key"] for r in rows} == {a["key"], b["key"]}
    rows2, ren2 = sm.migrate_company_story_keys([dict(a)])
    assert ren2 == [a["key"]] and rows2[0]["key"] == "e:台積電|l:earnings|2026q3"
    rows3, ren3 = sm.migrate_company_story_keys(rows2)
    assert ren3 == [] and rows3[0]["key"] == "e:台積電|l:earnings|2026q3"


# ------------------------------------------------ 外審 r1(deep):四條 CONFIRMED

def test_event_schema_version_was_bumped_with_the_formula():
    """r1 P1:`_event_instance_id` 由 timeline key 雜湊而來,主體公式改了
    就必須跳版 —— 否則舊 ID 與新 ID 都自稱當代,event-study 把同一樁事
    算成兩個獨立可信事件(3→4 跳版時記過同一種傷害)。"""
    assert ne.EVENT_SCHEMA_VERSION == 5


def test_migration_object_uses_the_producer_authority():
    """r1 P1:遷移用 `object_signature` 而 producer 用 `action_object`,
    對 directional action 不等價 —— 軍售記錄的對象是「台灣」,簽章是
    「台灣、美國」。用簽章重算會把鍵改成 producer 明天算不出來的樣子。"""
    assert ei.object_signature("arms_sale", ["美國", "台灣"]) == "台灣、美國"
    assert ei.action_object("arms_sale", "美國對台軍售 66 架 F-16",
                            ["美國", "台灣"]) == "台灣"
    key = "geopolitical:arms_sale:台灣:2026-08"
    tl = {key: {"entity": "美國", "subjects": ["美國", "台灣"],
                "object": "台灣", "days": 5,
                "latest_title": "美國對台軍售 66 架 F-16",
                "latest_summary": ""}}
    out, renamed, repaired = sm.migrate_cross_language_timeline_keys(tl)
    assert list(out) == [key], f"軍售鍵被改寫:{list(out)}"
    assert renamed == [] and repaired == []
    assert out[key]["object"] == "台灣", "對象被簽章蓋掉"


def test_migration_keeps_the_stored_object_when_it_cannot_recompute():
    """算不出對象(沒有標題)就沿用既有非空值,不得清空。"""
    key = "geopolitical:sanction:國際刑事法院:2026-08"
    tl = {key: {"entity": "國際刑事法院", "subjects": ["國際刑事法院"],
                "object": "國際刑事法院", "days": 2}}
    out, renamed, repaired = sm.migrate_cross_language_timeline_keys(tl)
    assert list(out) == [key] and out[key]["object"] == "國際刑事法院"
    assert renamed == [] and repaired == []


def test_alias_lookup_is_case_insensitive_for_normalized_story_keys():
    """r1 P1:story 鍵是 `_norm` 過的**小寫**(生產有 116 筆 `e:nvda`、
    91 筆 `e:aapl`)—— 逐字索引查不到,那些線永遠遷不掉。"""
    for low, rep in (("nvda", "輝達"), ("aapl", "蘋果"), ("msft", "微軟"),
                     ("tsmc", "台積電"), ("amd", "超微")):
        assert si.identity_name(low) == rep, low
    rows, renamed = sm.migrate_company_story_keys(
        [{"key": "e:nvda|l:general|2026-08", "entity": "NVDA"}])
    assert renamed == ["e:nvda|l:general|2026-08"]
    assert rows[0]["key"] == "e:輝達|l:general|2026-08"
    assert ea.canonical("NVDA") == "輝達"


def test_company_key_migration_runs_even_without_the_alias_map():
    """r1 P2:公司鍵遷移不依賴 alias map,先前卻排在 `if not kn: return`
    之後 —— 對照表取不到的那天整批公司舊鍵靜默孤立,而且毫無痕跡。"""
    import morning_report as mr
    import run_quality as rq
    src = io.open(Path(mr.__file__), encoding="utf-8").read()
    # 比對**程式碼行**:註解裡也有那串字,4 空白縮排排除註解行。
    i = src.index("    keep, _story_renamed = _sm.migrate_company_story_keys(")
    j = src.index("    if not kn:")
    assert j > i, "遷移仍排在 alias map 閘門之後"
    assert "state:alias_map_unavailable" in src[i:i + 1200]
    assert "state:alias_map_unavailable" in rq.KNOWN_DEGRADED


def test_unrecognizable_object_never_overwrites_a_definite_one():
    """r2 外審 P1:`action_object` 辨識不出受詞時回 `UNKNOWN_OBJECT`(`"?"`),
    而它是 truthy —— 最新標題變模糊的日子會把 `arms_sale:台灣` 改寫成
    `arms_sale:?` 並覆寫 object,明天那則明確的「對台軍售」反而接不回來。
    重算失敗要沿用既有身分。"""
    assert ei.action_object("arms_sale", "美國軍售案追蹤",
                            ["美國", "台灣"], summary="") == ei.UNKNOWN_OBJECT
    key = "geopolitical:arms_sale:台灣:2026-08"
    tl = {key: {"entity": "美國", "subjects": ["美國", "台灣"],
                "object": "台灣", "days": 9,
                "latest_title": "美國軍售案追蹤", "latest_summary": ""}}
    out, renamed, repaired = sm.migrate_cross_language_timeline_keys(tl)
    assert list(out) == [key], f"好的身分被佔位符蓋掉:{list(out)}"
    assert out[key]["object"] == "台灣"
    assert renamed == [] and repaired == []


def test_a_definite_object_still_repairs_an_unknown_placeholder():
    """反向:存的是 `"?"`、今天算得出明確對象 → 該修(修復不得因上面
    那條防護而整個失效)。"""
    old_key = "geopolitical:arms_sale:?:2026-08"
    tl = {old_key: {"entity": "美國", "subjects": ["美國", "台灣"],
                    "object": ei.UNKNOWN_OBJECT, "days": 4,
                    "latest_title": "美國對台軍售 66 架 F-16",
                    "latest_summary": ""}}
    out, renamed, repaired = sm.migrate_cross_language_timeline_keys(tl)
    assert list(out) == ["geopolitical:arms_sale:台灣:2026-08"], list(out)
    assert renamed == [old_key] and repaired == []


# ------------------------------- repo-wide 外審 2026-08-22 P1-2:producer ingress

def test_resolve_subject_accepts_company_aliases_across_languages():
    """**鍵那一層收斂得再好也沒用,如果資料沒有以那個身分進來。**

    中文標題〈輝達否認…〉配上抽取器給的候選 `NVIDIA`,先前三條路全滅:
    `known_names` 查的是代號 NVDA、語意驗證因 `aliases_of` 不含公司而
    不成立、literal 找不到英文字 —— entity 掉成空。隔天英文續報 literal
    成立變成 NVIDIA → 昨天是 entityless、今天是輝達,依然接不起來。
    """
    kn = {"NVDA": ("輝達", "NVIDIA"), "2330": ("台積電",)}
    assert ne.resolve_subject(
        "輝達否認年底前推出專供中國 AI 晶片", ["NVIDIA"], kn) == ("輝達", "alias")
    assert ne.resolve_subject(
        "台積電法說會確認先進封裝擴產", ["TSMC"], kn) == ("台積電", "alias")
    assert ne.resolve_subject(
        "NVIDIA denies China chip report", ["輝達"], kn)[1] == "alias"
    # **候選在 known_names 裡時仍回代號**,這是刻意的:下游
    # (`event_subject_key`、`purge_misattributed_*`)都用代號查對照表,
    # 回中文名會打斷那些查詢。跨語言收斂由鍵那一層負責(見下一條)。
    assert ne.resolve_subject("輝達否認擴大管制", ["NVDA"], kn)[0] == "NVDA"


def test_resolve_subject_still_rejects_unrelated_and_bare_numbers():
    """防護不得反過來製造誤併(誤併比漏併危險)。裸數字尤其危險:
    公司別名組含代號(緯創=3231),而「成交量 3231 張」的 3231 是張數
    —— literal 路徑本來就擋,別名路徑漏了同一條規則(自測回歸)。"""
    kn = {"2330": ("台積電",)}
    assert ne.resolve_subject("聯電法說會", ["TSMC"], kn) == ("", "")
    assert ne.resolve_subject("成交量 3231 張創天量", ["3231"], kn) == ("", "")
    assert ne.resolve_subject("Q2 earnings beat", ["Q2"], kn) == ("", "")


def test_producer_and_key_layer_agree_end_to_end():
    """從 producer 入口一路到持久鍵:兩天不同語言的同一則事件必須落同一把鍵
    (先前的測試直接把乾淨 entity 塞進 structured event,繞過會弄丟資料的
    producer —— 這條補上那一段)。"""
    kn = {"NVDA": ("輝達", "NVIDIA")}
    day1, b1 = ne.resolve_subject("輝達否認擴大出口管制傳聞", ["NVIDIA"], kn)
    day2, b2 = ne.resolve_subject(
        "NVIDIA confirms expanded export curbs", ["NVDA"], kn)
    assert b1 == b2 == "alias"
    k1 = ne._event_timeline_key({"entity": day1, "event_type": "export_controls",
                                 "date": "2026-08-22"})
    k2 = ne._event_timeline_key({"entity": day2, "event_type": "export_controls",
                                 "date": "2026-08-23"})
    assert k1 == k2, (k1, k2)
