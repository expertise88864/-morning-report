# -*- coding: utf-8 -*-
"""**事件身分要是「事件」,不是「某國的某類新聞」**(外審 P1-9)。

題目全部取自 2026-08-07 的**真實 state**,那裡同時有兩種相反的錯:

    geopolitical:伊朗              days=6   伊朗、阿曼研議限制敵對船舶通行荷姆茲海峽
    geopolitical:伊朗、美國、阿曼   days=1   美伊荷姆茲海峽談判傳出進展
    geopolitical:美國              days=4   北京不滿對台軍售致美國防官員訪中受阻

前兩條是**同一樁事情裂成兩條**(報導點名的主體集合不同);
第三條是**兩件不同的事被算成一條**(latest_title 已經漂到別的事)。
"""
from __future__ import annotations

import datetime as dt
import json

import event_identity as eid
import morning_report as mr


def _run(tmp_path, monkeypatch, events, day="2026-08-07", state=None):
    f = tmp_path / "tl.json"
    if state is not None:
        f.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(mr, "EVENT_TIMELINE_FILE", f)
    y, m, d = (int(x) for x in day.split("-"))
    active = mr.update_event_timeline(
        events, dt.datetime(y, m, d, 7, 0, tzinfo=mr.TPE))
    return active, json.loads(f.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 動作是主鍵

def test_the_same_story_stays_one_lineage_across_different_subject_sets():
    """**(a) 同一樁事情不得因為點名的主體不同而裂成兩條。**

    這正是生產那兩條荷姆茲線的成因 —— 一則寫「伊朗、阿曼」、
    另一則寫「伊朗、美國、阿曼」,於是同一件事有兩個「第 N 天」。
    """
    one = eid.timeline_identity(
        {"event_type": "geopolitical",
         "title": "伊朗、阿曼研議限制敵對船舶通行荷姆茲海峽"},
        ["伊朗", "阿曼"], "2026-08-07")
    two = eid.timeline_identity(
        {"event_type": "geopolitical", "title": "美伊荷姆茲海峽談判傳出進展"},
        ["伊朗", "美國", "阿曼"], "2026-08-07")
    assert one["key"] == two["key"], (one["key"], two["key"])
    assert one["action"] == "hormuz_passage" and one["basis"] == "action"


def test_two_different_stories_about_the_same_country_are_two_lineages():
    """**(b) 「延燒四天」不得跨到別件事上。**

    生產的 `geopolitical:美國` 就是這樣:八月初的某件事開的頭,
    四天後 latest_title 變成「對台軍售」,而天數照算。
    """
    arms = eid.timeline_identity(
        {"event_type": "geopolitical", "title": "北京不滿對台軍售致美國防官員訪中受阻"},
        ["美國", "中國"], "2026-08-07")
    hormuz = eid.timeline_identity(
        {"event_type": "geopolitical", "title": "美伊荷姆茲海峽談判傳出進展"},
        ["美國", "伊朗"], "2026-08-07")
    assert arms["key"] != hormuz["key"]
    assert arms["action"] == "arms_sale" and hormuz["action"] == "hormuz_passage"


def test_english_and_chinese_reports_of_one_event_share_a_lineage():
    """跨語言分裂:生產同時有 `Iran-Oman`、`United States-Iran` 與
    中文「伊朗、美國、阿曼」三條 —— 那是同一件事的三個「第 N 天」。"""
    zh = eid.timeline_identity(
        {"event_type": "geopolitical", "title": "伊朗與阿曼研議荷姆茲海峽通行限制"},
        ["伊朗", "阿曼"], "2026-08-07")
    en = eid.timeline_identity(
        {"event_type": "geopolitical",
         "title": "Iran and Oman weigh Hormuz passage restrictions"},
        ["Iran", "Oman"], "2026-08-07")
    assert zh["key"] == en["key"]
    # 主體本身也要正規化成同一種寫法(降級路徑要用到)
    assert en["subjects"] == ["伊朗", "阿曼"]


def test_a_cyberattack_never_shares_a_lineage_with_real_geopolitics():
    """生產有 `geopolitical:6446`(藥華藥網攻被歸成地緣)——
    型別分類錯了是另一個問題,但**身分**不該因此把網攻與軍售混成一條。"""
    cyber = eid.timeline_identity(
        {"event_type": "geopolitical", "title": "藥華藥遭網攻,生產線一度停擺"},
        ["6446"], "2026-08-07")
    arms = eid.timeline_identity(
        {"event_type": "geopolitical", "title": "美對台軍售新案"},
        ["美國"], "2026-08-07")
    assert cyber["action"] == "cyberattack" and cyber["key"] != arms["key"]


def test_an_unrecognised_action_falls_back_to_subjects_within_a_month():
    """**認不出動作是合法答案** —— 但退回**純**主體集合不是。

    上一版的期望(`geopolitical:伊朗、美國`,不帶月份)**把缺陷釘成了
    通過條件**:那把鑰匙讓同一組國家跨月的每一件事共用一條永久 lineage,
    而外審 P1-4A 指的正是它。加上月份是最低限度的分界;
    **同月**的兩件事由辨識詞在呼叫端分(見 `same_incident`)。
    """
    out = eid.timeline_identity(
        {"event_type": "geopolitical", "title": "兩國代表昨日於第三地會面"},
        ["美國", "伊朗"], "2026-08-07")
    assert out["action"] == "" and out["basis"] == "subjects"
    assert out["key"] == "geopolitical:伊朗、美國:2026-08"
    # 跨月就是另一條線(這正是加月份要買到的東西)
    nxt = eid.timeline_identity(
        {"event_type": "geopolitical", "title": "兩國代表昨日於第三地會面"},
        ["美國", "伊朗"], "2026-09-07")
    assert nxt["key"] != out["key"]


def test_the_month_separates_this_years_case_from_next_years():
    """不帶時間會讓「每年同一批軍售案」永久共用一條線 ——
    第二年的真事件會再次被判為無進展。"""
    a = eid.timeline_identity({"event_type": "geopolitical", "title": "對台軍售"},
                              ["美國"], "2026-08-07")
    b = eid.timeline_identity({"event_type": "geopolitical", "title": "對台軍售"},
                              ["美國"], "2027-08-07")
    assert a["key"] != b["key"]


# ---------------------------------------------------------------- 生產接線

def test_the_two_hormuz_reports_collapse_into_one_lineage(tmp_path, monkeypatch):
    """**整條 timeline 接線**:兩則不同主體集合的荷姆茲報導進來,
    state 只能有一條線、天數只加一次。"""
    _a, state = _run(tmp_path, monkeypatch, [
        {"event_type": "geopolitical", "entity": "伊朗、阿曼",
         "title": "伊朗、阿曼研議限制敵對船舶通行荷姆茲海峽"},
        {"event_type": "geopolitical", "entity": "伊朗、美國、阿曼",
         "title": "美伊荷姆茲海峽談判傳出進展"}])
    keys = sorted(state)
    assert keys == ["geopolitical:hormuz_passage:2026-08"], keys
    assert state[keys[0]]["days"] == 1, "同一天兩則報導不得算成兩天"
    assert state[keys[0]]["action"] == "hormuz_passage"
    assert state[keys[0]]["identity_schema"] == eid.IDENTITY_SCHEMA_VERSION


def test_a_legacy_lineage_keeps_its_days_on_upgrade_day(tmp_path, monkeypatch):
    """**升版當天不得讓「延燒六天」憑空消失。** 舊鍵的天數要接過來,
    而且舊鍵要被移除 —— 兩條並存就等於同一件事又有兩個「第 N 天」。"""
    legacy = {"geopolitical:伊朗": {
        "first_seen": "2026-08-01", "days": 6, "last_seen": "2026-08-06",
        # 第二十五輪 P1-3:**認領要動作相符。** 舊 record 的標題原本寫
        # 「舊鍵」這種佔位字串,而真實的 state 存的是當天的實際標題 ——
        # 用佔位字串測遷移,等於測一個不存在的形狀。
        "latest_title": "伊朗與阿曼就荷姆茲航道談判",
        "entity": "伊朗", "subjects": ["伊朗"],
        "event_type": "geopolitical"}}
    _a, state = _run(tmp_path, monkeypatch, [
        {"event_type": "geopolitical", "entity": "伊朗、阿曼",
         "title": "荷姆茲海峽通行談判"}], state=legacy)
    assert "geopolitical:伊朗" not in state, "舊鍵沒被收掉,同一件事兩條線"
    new = state["geopolitical:hormuz_passage:2026-08"]
    assert new["days"] == 7, f"天數沒接過來:{new}"
    assert new["first_seen"] == "2026-08-01"


def test_a_legacy_lineage_with_a_different_action_is_not_adopted(tmp_path,
                                                                monkeypatch):
    """**主體有交集不代表是同一件事**(第二十五輪 P1-3)。

    舊鍵「geopolitical:美國」記的是制裁案(第 4 天),今天出現的是軍售案
    —— 兩者都含「美國」,先前就把四天接了過去,軍售案第一天直接顯示
    「延燒第 5 天」。那正是這次重構要消掉的錯誤,只是從穩態身分搬到了遷移。
    """
    legacy = {"geopolitical:美國": {
        "first_seen": "2026-08-01", "days": 4, "last_seen": "2026-08-06",
        "latest_title": "美國宣布對某國制裁", "entity": "美國",
        "subjects": ["美國"], "event_type": "geopolitical"}}
    _a, state = _run(tmp_path, monkeypatch, [
        {"event_type": "geopolitical", "entity": "美國、台灣",
         "title": "美國宣布對台軍售"}], state=legacy)
    new_key = [k for k in state if "arms_sale" in k]
    assert new_key, sorted(state)
    assert state[new_key[0]]["days"] == 1, "制裁案的天數被接到軍售案上"
    assert "geopolitical:美國" in state, "沒認領就不該收掉舊鍵"


def test_a_legacy_record_whose_action_is_unreadable_restarts(tmp_path,
                                                             monkeypatch):
    """舊標題認不出動作時**從第 1 天起算**,不靠主體交集接天數。

    低估連續天數只是少一句「第 N 天」;接錯會讓讀者以為一件今天才發生的
    事已經追蹤一週。兩種錯誤的代價不對稱。
    """
    legacy = {"geopolitical:伊朗": {
        "first_seen": "2026-08-01", "days": 6, "last_seen": "2026-08-06",
        "latest_title": "(舊格式沒有存標題)", "entity": "伊朗",
        "subjects": ["伊朗"], "event_type": "geopolitical"}}
    _a, state = _run(tmp_path, monkeypatch, [
        {"event_type": "geopolitical", "entity": "伊朗、阿曼",
         "title": "荷姆茲海峽通行談判"}], state=legacy)
    assert state["geopolitical:hormuz_passage:2026-08"]["days"] == 1


def test_an_unrelated_legacy_lineage_is_not_adopted(tmp_path, monkeypatch):
    """**認領要保守。** 主體沒有交集的舊線不得被接走 ——
    那會把別件事的天數安到這條上,比從第 1 天算起更糟。"""
    legacy = {"geopolitical:日本": {
        "first_seen": "2026-08-01", "days": 5, "last_seen": "2026-08-06",
        "latest_title": "日圓干預", "entity": "日本", "subjects": ["日本"],
        "event_type": "geopolitical"}}
    _a, state = _run(tmp_path, monkeypatch, [
        {"event_type": "geopolitical", "entity": "伊朗、阿曼",
         "title": "荷姆茲海峽通行談判"}], state=legacy)
    assert state["geopolitical:hormuz_passage:2026-08"]["days"] == 1
    assert state["geopolitical:日本"]["days"] == 5, "無關的舊線被動到了"


def test_the_migration_is_visible_in_the_manifest(tmp_path, monkeypatch):
    """**遷移要看得見**:沒有這些數字,「新公式上線了」與
    「新公式一則都沒改到」在 manifest 裡長得一樣。"""
    legacy = {"geopolitical:伊朗": {
        "first_seen": "2026-08-01", "days": 6, "last_seen": "2026-08-06",
        # 同上:真實 state 存的是當天的實際標題,不是佔位字串 ——
        # 而認領現在要求動作相符(第二十五輪 P1-3)。
        "latest_title": "伊朗與阿曼就荷姆茲航道談判",
        "entity": "伊朗", "subjects": ["伊朗"],
        "event_type": "geopolitical"}}
    mr._RUN_MANIFEST.pop("event_identity", None)
    _run(tmp_path, monkeypatch, [
        {"event_type": "geopolitical", "entity": "伊朗、阿曼",
         "title": "荷姆茲海峽通行談判"}], state=legacy)
    got = mr._RUN_MANIFEST["event_identity"]
    assert got["schema"] == eid.IDENTITY_SCHEMA_VERSION
    assert got["keyed_by_action"] == 1 and got["adopted_legacy"] == 1
    assert got["legacy_remaining"] == 0


# ---------------------------------------------------------------- 表本身

def test_the_action_table_is_deterministic_and_documented():
    """**身分不能靠相似度。** 表是宣告式的:每一列說得出代碼與說明,
    而且第一個命中者勝(順序即優先序)。"""
    codes = [row[0] for row in eid.ACTION_TABLE]
    assert len(codes) == len(set(codes)), "動作代碼重複"
    for row in eid.ACTION_TABLE:
        assert len(row) >= 3, f"{row[0]} 沒有關鍵詞"
        assert eid.action_label(row[0]), f"{row[0]} 沒有說明"
    assert eid.event_action("") == "" and eid.event_action(None) == ""
    assert eid.action_label("不存在的代碼") == ""


def test_canonicalisation_never_invents_a_mapping():
    """**推不出來就原樣留著。** 猜一個對照會把兩個不同的主體黏成一個,
    而那比分裂更難發現。"""
    assert eid.canonical_subject("Iran") == "伊朗"
    assert eid.canonical_subject("台積電") == "台積電"
    assert eid.canonical_subject("某個沒收錄的名字") == "某個沒收錄的名字"
    assert eid.canonical_subject("") == "" and eid.canonical_subject(None) == ""


def test_production_sweeps_the_unadopted_legacy_line(tmp_path, monkeypatch):
    """**走生產路徑驗**(2026-08-08 事故)。

    上一版的回歸測試直接呼叫 `supersede_legacy`,於是把 `update_event_timeline`
    裡的那一行拿掉時**一條測試都不紅** —— 函式對、呼叫端忘了接,而那正是
    這個 repo 記過的形狀。這裡重現那天的 state:舊鍵「伊朗」的標題認不出
    動作,今天來的是荷姆茲的報導。信裡不得出現兩個「第 N 天」。
    """
    legacy = {"geopolitical:伊朗": {
        "first_seen": "2026-08-01", "days": 7, "last_seen": "2026-08-07",
        "latest_title": "川普預告戰爭快結束,稱親自參與談判",
        "entity": "伊朗", "subjects": ["伊朗"], "event_type": "geopolitical"}}
    active, state = _run(tmp_path, monkeypatch, [
        {"event_type": "geopolitical", "entity": "伊朗、阿曼",
         "title": "川普認了飛彈庫存吃緊,荷姆茲海峽有望重啟"}],
        day="2026-08-08", state=legacy)
    assert "geopolitical:伊朗" not in state, "接不到的舊線還留著,同一件事兩條"
    assert mr._RUN_MANIFEST["event_identity"]["superseded_legacy"] == 1
    # 新線當天才第 1 天,`days >= 2` 的門檻讓它還不顯示 —— **那是對的**:
    # 讀者當天看不到這條線,總比看到兩個互相矛盾的「第 N 天」好。
    assert [r["key"] for r in active] == [], active
    assert state["geopolitical:hormuz_passage:2026-08"]["days"] == 1


def test_the_rendered_timeline_never_shows_an_internal_key(tmp_path,
                                                           monkeypatch):
    """**信裡那一行要是人看得懂的名字。** 走真正的 HTML 渲染。"""
    import html as _htmllib
    ev = [{"event_type": "geopolitical", "entity": "伊朗、阿曼",
           "title": "荷姆茲海峽有望重啟"}]
    # 跑兩天:`active` 的門檻是 `days >= 2`,一天的線本來就不顯示。
    _run(tmp_path, monkeypatch, ev, day="2026-08-08")
    active, _ = _run(tmp_path, monkeypatch, ev, day="2026-08-09")
    out = mr._render_event_timeline_html(active, _htmllib)
    assert "hormuz_passage" not in out and "2026-08" not in out, out
    assert "荷姆茲" in out


def test_a_subject_fallback_line_is_hidden_when_the_story_is_identified():
    """**同一個故事不得有兩個「第 N 天」**(2026-08-08 第二封信)。

    標題有時點得出動作、有時點不出,於是同一條荷姆茲的線同時以
    `hormuz_passage`(第 2 天)與 `伊朗`(第 7 天)存在。

    外審補審 F5:**遮蔽現在要有第二個「同一件事」的證據**(標題重疊)
    —— 只比主體的話,同主體的**另一樁真事件**會被一起藏掉。
    因此 fixture 補上生產真的有的 `latest_title`。
    """
    active = [
        {"key": "geopolitical:伊朗", "days": 7, "action": "",
         "subjects": ["伊朗"], "event_type": "geopolitical",
         "latest_title": "伊朗荷姆茲海峽通行談判傳有進展"},
        {"key": "geopolitical:hormuz_passage:2026-08", "days": 2,
         "action": "hormuz_passage", "subjects": ["伊朗", "阿曼"],
         "event_type": "geopolitical",
         "latest_title": "伊朗荷姆茲海峽通行談判進入第二週"}]
    kept = [r["key"] for r in eid.drop_shadowed(active)]
    assert kept == ["geopolitical:hormuz_passage:2026-08"], kept


def test_an_unrelated_story_on_the_same_subject_survives_but_says_which():
    """**外審補審 F5。** 標題對不上時**不遮蔽** —— 但那條線要說得出
    自己是哪件事,否則兩個「伊朗(第 N 天)」讀起來仍然矛盾。
    (2026-08-08 的抱怨是「分不出來」,不是「有兩條」。)"""
    active = [
        {"key": "geopolitical:伊朗", "days": 7, "action": "",
         "subjects": ["伊朗"], "event_type": "geopolitical",
         "latest_title": "伊朗革命衛隊在波斯灣舉行大規模軍演"},
        {"key": "geopolitical:hormuz_passage:2026-08", "days": 2,
         "action": "hormuz_passage", "subjects": ["伊朗", "阿曼"],
         "event_type": "geopolitical",
         "latest_title": "伊朗與阿曼就荷姆茲航道達成共識"}]
    kept = eid.drop_shadowed(active)
    assert len(kept) == 2, "真的另一樁事件被藏掉了"
    labels = [eid.display_label(r) for r in kept]
    assert any("軍演" in x for x in labels), labels
    assert len({*labels}) == 2, labels


def test_an_unrelated_subject_line_is_not_hidden():
    """**遮蔽要保守。** 主體沒有交集的那條與它無關,不得順手蓋掉。"""
    active = [
        {"key": "geopolitical:日本", "days": 4, "action": "",
         "subjects": ["日本"], "event_type": "geopolitical"},
        {"key": "geopolitical:hormuz_passage:2026-08", "days": 2,
         "action": "hormuz_passage", "subjects": ["伊朗"],
         "event_type": "geopolitical"}]
    assert len(eid.drop_shadowed(active)) == 2


def test_production_keeps_both_lines_distinguishable(tmp_path, monkeypatch):
    """走生產路徑 —— **函式對、呼叫端忘了接**是這個 repo 記過的形狀。

    外審補審 F5 之後量的是「兩條線分得開」而不是「其中一條被藏掉」;
    理由寫在下方斷言處。"""
    # **v7 的記錄一定帶 `incident_tokens`**(生產每次寫入都會補)——
    # 標成 v7 卻沒有 tokens 是生產產不出來的形狀,而三態判準
    #(外審 P1-3)對「沒有 tokens」的答案是 `UNKNOWN`:
    # 這條測的是遮蔽規則,fixture 不真實的話量到的是別條。
    state = {"geopolitical:伊朗:2026-08": {
        "first_seen": "2026-08-01", "days": 7, "last_seen": "2026-08-08",
        "latest_title": "川普稱與伊朗戰爭很快將結束", "action": "",
        "incident_tokens": sorted(eid.discriminative_tokens(
            "川普稱與伊朗戰爭很快將結束", ["伊朗"]))[:12],
        "entity": "伊朗", "subjects": ["伊朗"], "event_type": "geopolitical",
        "identity_schema": eid.IDENTITY_SCHEMA_VERSION},
        "geopolitical:hormuz_passage:2026-08": {
        "first_seen": "2026-08-07", "days": 1, "last_seen": "2026-08-08",
        "latest_title": "荷姆茲有望重啟", "action": "hormuz_passage",
        "incident_tokens": sorted(eid.discriminative_tokens(
            "荷姆茲有望重啟", ["伊朗", "阿曼"]))[:12],
        "entity": "伊朗", "subjects": ["伊朗", "阿曼"],
        "event_type": "geopolitical",
        "identity_schema": eid.IDENTITY_SCHEMA_VERSION}}
    # **兩條線要在同一天都活著**,否則濾掉的是「今天沒更新」那道門檻,
    # 量不到遮蔽規則(突變驗證抓到:拿掉 `drop_shadowed` 也不紅)。
    # 所以餵兩則:一則點得出荷姆茲、一則點不出動作而退回主體。
    active, _ = _run(tmp_path, monkeypatch, [
        {"event_type": "geopolitical", "entity": "伊朗、阿曼",
         "title": "荷姆茲海峽談判再進展"},
        {"event_type": "geopolitical", "entity": "伊朗",
         "title": "川普稱與伊朗的僵局很快就會有結果"}],
        day="2026-08-09", state=state)
    keys = [r["key"] for r in active]
    assert "geopolitical:hormuz_passage:2026-08" in keys, keys
    # **外審補審 F5 之後這裡的期望改了,而且是刻意的。**
    #
    # 2026-08-08 生產的這兩個標題(「川普稱與伊朗的僵局很快就會有結果」
    # vs「荷姆茲海峽談判再進展」)重疊度很低 —— 遮蔽規則現在要求
    # 「同一件事」的第二個證據,所以它**不再被遮掉**。
    #
    # 這是取捨,不是退步:只比主體的話,同主體的**另一樁真事件**
    # (例:伊朗革命衛隊軍演)會從信裡整條消失,而讀者不會知道它存在過。
    # 隱藏真事件比顯示兩條更糟。
    #
    # 原本的抱怨是「兩個矛盾的第 N 天分不出來」—— 那一半由
    # `display_label` 解掉:主體 fallback 的線現在帶自己的標題片段,
    # 兩條讀起來是**兩件事**,不是同一件事的兩個天數。
    assert "geopolitical:伊朗:2026-08" in keys, keys
    labels = [eid.display_label(r) for r in active]
    assert len(set(labels)) == len(labels), labels
    assert any("僵局" in x or "川普" in x for x in labels), labels


# ================= Commit 2:incident identity v7 =================

def test_two_unknown_actions_for_the_same_subject_do_not_share_a_line():
    """**外審 P1-4A。** 「伊朗國內爆發示威」與「伊朗與鄰國邊境衝突」
    的動作都不在表裡 —— 鍵一樣,但它們不是同一樁。辨識詞分得開。"""
    a = eid.timeline_identity({"event_type": "geopolitical",
                               "title": "伊朗國內爆發大規模示威"},
                              ["伊朗"], "2026-08-09")
    b = eid.timeline_identity({"event_type": "geopolitical",
                               "title": "伊朗與鄰國發生邊境武裝衝突"},
                              ["伊朗"], "2026-08-09")
    assert a["key"] == b["key"], "同鍵是預期的(鍵刻意維持粗粒度)"
    assert not eid.same_incident(a["incident_tokens"], b["incident_tokens"])


def test_two_incidents_on_the_same_company_in_one_month_do_not_merge():
    """**外審 P1-4B。** 同月對同一家公司的勒索攻擊與資料外洩,
    `型別:動作:對象:月` 完全一樣 —— 要靠辨識詞分。"""
    a = eid.timeline_identity({"event_type": "litigation",
                               "title": "藥華藥遭勒索軟體攻擊 產線停擺"},
                              ["藥華藥"], "2026-08-03")
    # **反例要只靠被測那條規則分勝負。** 第一版兩個標題都寫「遭勒索軟體
    # 攻擊」—— 重疊 0.67,而那本來就可能是同一樁(後續揭露資料也外洩了)。
    # 換成描述完全不同的第二樁,勝負才由辨識詞決定。
    b = eid.timeline_identity({"event_type": "litigation",
                               "title": "藥華藥傳客戶名單外洩 主管請辭"},
                              ["藥華藥"], "2026-08-20")
    assert not eid.same_incident(a["incident_tokens"], b["incident_tokens"]), (
        a["incident_tokens"], b["incident_tokens"])


def test_follow_up_coverage_of_one_incident_stays_on_the_same_line():
    """**修正不得比缺陷更糟。** 第一版把指紋寫進鍵,同一樁的後續報導
    (標題多了「再遭」兩個字)就拿到新的鍵 —— 每條線每天從第 1 天重來,
    而後續報導正是「延燒第 N 天」的常態。"""
    a = eid.timeline_identity({"event_type": "litigation",
                               "title": "藥華藥遭勒索軟體攻擊 產線停擺"},
                              ["藥華藥"], "2026-08-03")
    b = eid.timeline_identity({"event_type": "litigation",
                               "title": "藥華藥再遭勒索軟體攻擊 產線停擺"},
                              ["藥華藥"], "2026-08-04")
    assert a["key"] == b["key"]
    assert eid.same_incident(a["incident_tokens"], b["incident_tokens"])


def test_an_extra_vendor_entity_does_not_split_one_arms_package():
    """**外審 P1-4C。** 同一批軍售,某一則多抓到承包商 —— 對象簽章
    先前用整個主體集合,兩把鑰匙就不同。法域類的動作只看國家。"""
    base = {"event_type": "geopolitical", "title": "美國宣布對台軍售"}
    k1 = eid.timeline_identity(base, ["美國", "台灣"], "2026-08-09")["key"]
    k2 = eid.timeline_identity(base, ["美國", "台灣", "洛克希德馬丁"],
                               "2026-08-09")["key"]
    assert k1 == k2, (k1, k2)
    # 但**換一個受援國**仍要分開(修正不得把該分的黏起來)
    k3 = eid.timeline_identity(dict(base, title="美國宣布對日本軍售"),
                               ["美國", "日本"], "2026-08-09")["key"]
    assert k3 != k1


def test_a_jurisdiction_action_without_a_known_country_keeps_its_subjects():
    """**濾不出法域時不得硬縮成空。** 制裁的對象可能是一家公司、一個
    組織 —— 它們不在 `CANONICAL_SUBJECTS` 裡。硬縮成空字串會讓那個月
    所有這類制裁案共用一把鑰匙,而那正是加對象要修的東西。"""
    base = {"event_type": "geo", "title": "美方宣布制裁該實體"}
    a = eid.timeline_identity(base, ["某資安公司"], "2026-08-09")["key"]
    b = eid.timeline_identity(base, ["另一家公司"], "2026-08-09")["key"]
    assert a != b, (a, b)
    assert "某資安公司" in a


def test_a_company_target_is_still_part_of_the_object():
    """`cyberattack` 的對象是**公司**,不是法域 —— 不得被法域過濾清空。"""
    a = eid.timeline_identity({"event_type": "litigation", "title": "甲公司遭勒索軟體攻擊"},
                              ["甲公司"], "2026-08-09")["key"]
    b = eid.timeline_identity({"event_type": "litigation", "title": "乙公司遭勒索軟體攻擊"},
                              ["乙公司"], "2026-08-09")["key"]
    assert a != b, (a, b)


def test_an_iranian_drill_is_not_a_taiwan_strait_event():
    """**自查(外審沒提)**:`軍演` 是台海動作的關鍵詞,於是
    「伊朗革命衛隊舉行軍演」被判成 `strait_tension` —— 一個伊朗的
    演習進了台海的 lineage,而錯誤分類會一路污染 continuing days。
    台海是**地點**,不是動作。"""
    assert eid.event_action("伊朗革命衛隊舉行軍演") == ""
    assert eid.event_action("共機擾台 台海軍演升溫") == "strait_tension"


def test_ascii_keywords_need_token_boundaries():
    """**這條不是修一個已確認的缺陷。** 外審說 `export_control` 含裸的
    `ban` 因而讓 `Bank earnings` 誤判 —— 查表:沒有裸 `ban`(用的是
    片語 `chip ban`),實測回空字串,那條駁回。

    但這個 repo 已為同一個形狀修過三次(`ft` 命中 SoftBank、
    `raise` 命中 praise、`us` 命中 ASUS),而現在全靠「剛好沒有人加過
    一個會撞的單字」—— 把它變成結構上不可能發生比較便宜。
    """
    # 判準是**詞界有沒有生效**,不是「禁止短關鍵詞」:`FMS`
    # (Foreign Military Sales)是正當的縮寫,而它現在因為詞界而安全 ——
    # 禁掉它等於把修正的成果當成問題。
    assert eid.event_action("Bank earnings rise") == ""
    assert eid.event_action("US widens chip ban on China") == "export_control"
    assert eid.event_action("Taiwan FMS package approved") == "arms_sale"
    # 藏在別的字裡不得命中
    assert eid.event_action("The firm confirms guidance") == ""
    assert eid.event_action("FMSX Corp reports results") == ""
    # **複數要放行** —— 純詞界讓 `sanction` 不再命中 `sanctions`,
    # 那是硬化造成的真回歸(preflight 當場抓到:一則英文制裁報導的
    # 動作變成空字串,昨日觀點因此接不上)。只放行一個 `s`。
    assert eid.event_action("US announces new sanctions on Iran") == "sanction"
    # `ban` 是**片語** `chip ban` 的一部分(這正是駁回 P1-4D 的理由),
    # 所以「bans chip exports」不該命中 —— 斷言要照表寫,不照印象寫。
    assert eid.event_action("US bans chip exports") == ""


def test_production_really_writes_two_lineages(tmp_path, monkeypatch):
    """**走生產路徑,不是 grep 函式名**(第二輪外審 F1)。

    上一版只檢查原始碼裡出現過 `same_incident` 與 `incident_suffix` ——
    而生產把 `ident["key"]` 換掉了、落盤那行卻用著**先前存下來的**
    `key` 變數:第二樁直接**覆寫**第一樁,分線完全沒生效,還會蓋掉資料。
    只 grep 名字的守衛看不見那件事。
    """
    evs = [{"event_type": "litigation", "entity": "藥華藥",
            "title": "藥華藥遭勒索軟體攻擊 產線停擺"},
           {"event_type": "litigation", "entity": "藥華藥",
            "title": "藥華藥遭駭客入侵 客戶名單外洩"}]
    _, state = _run(tmp_path, monkeypatch, evs, day="2026-08-07")
    keys = sorted(state)
    assert len(keys) == 2, keys
    assert any("#" in k for k in keys), keys
    titles = {v["latest_title"] for v in state.values()}
    assert len(titles) == 2, titles


def test_a_split_incident_keeps_its_line_the_next_day(tmp_path, monkeypatch):
    """**分出去的那一樁隔天要接得回自己**(第二輪外審 F3)。

    後綴由當日辨識詞雜湊而來,而辨識詞會隨標題漂移 —— 只比 base、
    直接算新後綴的話,第二樁**每天都會再開一條**,天數永遠是 1。
    這是我在「指紋進鍵」上踩過的同一個坑換了個位置。
    """
    d1 = [{"event_type": "litigation", "entity": "藥華藥",
           "title": "藥華藥遭勒索軟體攻擊 產線停擺"},
          {"event_type": "litigation", "entity": "藥華藥",
           "title": "藥華藥遭駭客入侵 客戶名單外洩"}]
    _, st1 = _run(tmp_path, monkeypatch, d1, day="2026-08-07")
    d2 = [{"event_type": "litigation", "entity": "藥華藥",
           "title": "藥華藥遭勒索軟體攻擊 產線停擺進度更新"},
          {"event_type": "litigation", "entity": "藥華藥",
           # **這個續篇的 top-4 辨識詞會漂移**(加了「主管請辭負責」)——
           # 後綴由 top-4 雜湊而來,所以不找 sibling 的話會另開一條。
           # 上一版的續篇碰巧沒讓 top-4 變動,量不到那條規則。
           "title": "藥華藥遭駭客入侵 客戶名單外洩 主管請辭負責"}]
    import event_identity as _ei
    _d1 = sorted(_ei.discriminative_tokens("藥華藥遭駭客入侵 客戶名單外洩",
                                           ["藥華藥"]))
    _d2 = sorted(_ei.discriminative_tokens(d2[1]["title"], ["藥華藥"]))
    assert _ei.incident_suffix(_d1) != _ei.incident_suffix(_d2),         "反例沒有讓後綴漂移 —— 量不到 sibling 搜尋那條規則"
    assert _ei.same_incident(_d1, _d2), "但它們仍是同一樁"
    _, st2 = _run(tmp_path, monkeypatch, d2, day="2026-08-08", state=st1)
    assert sorted(st2) == sorted(st1), (sorted(st1), sorted(st2))
    assert all(v["days"] == 2 for v in st2.values()), \
        {k: v["days"] for k, v in st2.items()}


def test_the_schema_version_moves_when_the_formula_moves():
    """**公式變了就要進版**(第二輪外審 F2)。不進版的話 `adopt_legacy`
    會因為 `identity_schema >= VERSION` 跳過既有記錄,每一條 lineage
    在上線當天從第 1 天重算,而沒有人看得出原因。"""
    assert eid.IDENTITY_SCHEMA_VERSION >= 7


def test_a_schema_6_record_is_adopted_by_the_new_formula(tmp_path, monkeypatch):
    """**上線當天要接得住舊記錄。** 未知動作的鍵加了月份、對象換了
    範圍 —— 舊鍵算出來的東西與新鍵不同,而天數必須跟過來。"""
    legacy = {"geopolitical:伊朗": {
        "first_seen": "2026-08-01", "days": 6, "last_seen": "2026-08-08",
        "latest_title": "伊朗國內爆發大規模示威", "entity": "伊朗",
        "subjects": ["伊朗"], "event_type": "geopolitical",
        "identity_schema": 6}}
    _, state = _run(tmp_path, monkeypatch,
                    [{"event_type": "geopolitical", "entity": "伊朗",
                      "title": "伊朗國內示威進入第七天"}],
                    day="2026-08-09", state=legacy)
    assert "geopolitical:伊朗" not in state, "舊鍵沒被收掉"
    assert state, "新鍵也沒建立 —— 那條線整個不見了"
    assert max(v["days"] for v in state.values()) >= 7, (
        state, "舊記錄的天數沒有接過來,從第 1 天重算了")


def test_a_company_sanction_target_is_not_erased_by_the_jurisdiction_filter():
    """**制裁可以直接針對一家公司**(第二輪外審 F4)。一律縮成法域會讓
    「美國制裁甲公司」與「美國制裁乙公司」共用一把鑰匙 —— 那是我為了修
    承包商雜訊而引進的新 over-merge。"""
    base = {"event_type": "geo", "title": "美國宣布制裁該實體"}
    a = eid.timeline_identity(base, ["美國", "甲公司"], "2026-08-09")["key"]
    b = eid.timeline_identity(base, ["美國", "乙公司"], "2026-08-09")["key"]
    assert a != b, (a, b)
    # 但軍售仍要濾掉承包商(那是 P1-4C,方向相反)
    arms = {"event_type": "geo", "title": "美國宣布對台軍售"}
    k1 = eid.timeline_identity(arms, ["美國", "台灣"], "2026-08-09")["key"]
    k2 = eid.timeline_identity(arms, ["美國", "台灣", "洛克希德馬丁"],
                               "2026-08-09")["key"]
    assert k1 == k2, (k1, k2)


def test_a_sibling_survives_the_base_lineage_expiring(tmp_path, monkeypatch):
    """**base 退場之後,還在燒的那一樁要接得回自己**(第三輪外審)。

    sibling 搜尋原本掛在「base record 還在」底下 —— 而 base 停更三天
    就會被退場清掉,`state.get(base)` 回 `None`。那之後,持續延燒的
    sibling 每天都拿到 base 這把空鑰匙、重開一條新線,天數永遠是 1。
    **base 不在了正是「只剩一樁在燒」的常態**,不是罕例;而這條測試
    先前只跨了一天,兩條都還在 state 裡,量不到這件事。
    """
    # 兩則都要命中 `cyberattack`(否則 base key 本來就不同,
    # 根本沒有同鍵可分,測不到 sibling 這條規則)。
    # 型別是 `cybersecurity`:2026-08-18 起資安事件不再被歸成 geopolitical
    # (外審 P2-1),而**生產者會把上游給的 geopolitical 也正規化過來** ——
    # 這個 fixture 若繼續寫 geopolitical,測的就是一個生產不會出現的形狀。
    day1 = [{"event_type": "cybersecurity", "entity": "藥華藥",
             "title": "藥華藥遭勒索軟體攻擊 產線停擺"},
            {"event_type": "cybersecurity", "entity": "藥華藥",
             "title": "藥華藥遭駭客入侵 客戶名單外洩"}]
    _, st = _run(tmp_path, monkeypatch, day1, day="2026-08-01")
    assert len(st) == 2, st
    base = next(k for k in st if "#" not in k)
    sib = next(k for k in st if "#" in k)

    # 之後只有「名單外洩」那一樁持續有報導,base 那樁停更
    for n, extra in ((2, "後續"), (3, "最新進度"), (4, "主管請辭負責"),
                     (5, "調查結果出爐"), (6, "和解金額出爐")):
        _, st = _run(tmp_path, monkeypatch,
                     [{"event_type": "cybersecurity", "entity": "藥華藥",
                       "title": f"藥華藥遭駭客入侵 客戶名單外洩 {extra}"}],
                     day=f"2026-08-{n:02d}", state=st)
    assert base not in st, "base 那樁停更超過保留期,應該已經退場"
    assert sib in st, f"還在燒的那一樁被丟了:{sorted(st)}"
    assert st[sib]["days"] == 6, {k: v["days"] for k, v in st.items()}
    assert len(st) == 1, f"base 被重開了 —— 天數從第 1 天重算:{sorted(st)}"


def test_the_telemetry_counts_action_keys_that_carry_an_object(tmp_path,
                                                               monkeypatch):
    """**`keyed_by_action` 從 v7 起幾乎永遠是 0**(2026-08-09 P2)。

    帶對象的動作,`basis` 是 `action+object` —— 而計數只認字串完全等於
    `action`。這格數字正是用來看「動作為主鍵這件事有沒有在作用」的,
    於是「遙測說 0」與「功能真的沒接上」在 manifest 裡長得一模一樣。
    """
    evs = [{"event_type": "geopolitical", "entity": "美國、台灣",
            "title": "美國宣布對台軍售"},
           {"event_type": "geopolitical", "entity": "美國、中國",
            "title": "美國宣布制裁中國實體清單企業"},
           # **這一則的動作不帶對象**(`strait_tension` 不在 `NEEDS_OBJECT`)
           # —— 兩個計數器要因此分岔。都算進去的話,第二格恆等於第一格,
           # 而它存在的理由正是「對象簽章今天算不算得出來」。
           {"event_type": "geopolitical", "entity": "台灣",
            "title": "共機擾台 台海軍演升溫"}]
    _run(tmp_path, monkeypatch, evs, day="2026-08-09")
    tel = mr._RUN_MANIFEST["event_identity"]
    assert tel["keyed_by_action"] == 3, tel
    assert tel["keyed_by_action_object"] == 2, tel


# ===== 第二十七輪外審 P1-3 / P1-4A:三態與方向詞受詞 =====

def test_sparse_tokens_are_unknown_not_a_match():
    """**「不知道」要自己是一個答案。**

    上一版辨識詞不足時直接回「同一樁」—— 而升版當天 state 裡幾乎全是
    **沒有 `incident_tokens`** 的舊代記錄,比對時一側是空集合,
    新事件因此繼承前一樁的天數。
    """
    assert eid.incident_match([], ["入侵", "名單", "外洩"]) == eid.UNKNOWN
    assert eid.incident_match(["外洩"], ["入侵", "名單", "產線"]) == eid.UNKNOWN
    assert eid.incident_match(["a", "b", "c"], ["a", "b", "d"]) == eid.MATCH
    assert eid.incident_match(["a", "b", "c"], ["x", "y", "z"]) == eid.NO_MATCH


def test_a_legacy_record_does_not_lend_its_days_to_a_new_incident(tmp_path,
                                                                  monkeypatch):
    """**舊代記錄不得把天數借給另一樁事**(外審 P1-3 情境 A)。

    schema 6 的記錄沒有 `incident_tokens`;上一版比對時一側是空集合 →
    一律視為同一樁 → 同鍵下的新事件直接繼承它的天數。
    現在那是 `UNKNOWN`,而 `UNKNOWN` 只有「同代且今天已更新過」才承接。
    """
    legacy = {"geopolitical:cyberattack:藥華藥:2026-08": {
        "first_seen": "2026-08-01", "days": 8, "last_seen": "2026-08-08",
        "latest_title": "藥華藥遭勒索軟體攻擊 產線停擺", "entity": "藥華藥",
        "subjects": ["藥華藥"], "event_type": "geopolitical",
        "action": "cyberattack", "identity_schema": 6}}
    _, st = _run(tmp_path, monkeypatch,
                 [{"event_type": "cybersecurity", "entity": "藥華藥",
                   "title": "藥華藥遭駭客入侵 客戶名單外洩"}],
                 day="2026-08-09", state=legacy)
    # **今天被更新的那一條**要從第 1 天算起(舊那筆沒被碰,天數當然還在
    # 它自己身上,它會自然退場 —— 那不是繼承)。
    today_line = [v for v in st.values() if v.get("last_seen") == "2026-08-09"]
    assert today_line, st
    assert all(v["days"] == 1 for v in today_line),         {k: v["days"] for k, v in st.items()}
    assert all("外洩" not in str(v.get("latest_title") or "")
               for v in st.values() if v.get("days", 0) > 1), st


def test_a_same_day_follow_up_still_continues_its_line(tmp_path, monkeypatch):
    """**修正不得把該接的切斷**:同代、今天已更新過的那一筆仍要承接
    (那是同一天的後續報導,不是跨日的天數繼承)。"""
    # 同代(v7)、**今天已經更新過**、但存下來的辨識詞是空的
    # (標題太短時生產真的會寫成空清單)——「不知道」在這裡要沿用,
    # 那是同一天的後續報導,不是跨日的天數繼承。
    # 舊鍵是資安事件借用地緣型別留下的(2026-08-18 外審 P2-1);載入時會被
    # **改名**成 `cybersecurity:…`(改名而不是丟掉,延燒天數才不會重算)。
    # 這條測試要守的仍是原本那件事:同一天的後續報導接得回同一條線,
    # 而不是開出第二條。
    key = "geopolitical:cyberattack:藥華藥:2026-08"
    renamed = "cybersecurity:cyberattack:藥華藥:2026-08"
    same_day = {key: {"first_seen": "2026-08-09", "days": 1,
                      "last_seen": "2026-08-09", "latest_title": "藥華藥",
                      "entity": "藥華藥", "subjects": ["藥華藥"],
                      "event_type": "geopolitical", "action": "cyberattack",
                      "incident_tokens": [],
                      "identity_schema": eid.IDENTITY_SCHEMA_VERSION}}
    _, st = _run(tmp_path, monkeypatch,
                 [{"event_type": "cybersecurity", "entity": "藥華藥",
                   "title": "藥華藥遭勒索軟體攻擊 產線停擺"}],
                 day="2026-08-09", state=same_day)
    assert list(st) == [renamed], st


def test_the_recipient_is_the_object_even_when_the_actor_is_missing():
    """**同一批軍售不得因為 actor 有沒有被抓到而分裂**(外審 P1-4A)。

    donor 與 recipient 都是法域,「只留法域」解不了這件事 ——
    要看標題裡的方向詞(「對**台**」、"to Taiwan")。
    """
    base = {"event_type": "geopolitical", "title": "美國宣布對台軍售"}
    k1 = eid.timeline_identity(base, ["台灣"], "2026-08-09")["key"]
    k2 = eid.timeline_identity(base, ["美國", "台灣"], "2026-08-09")["key"]
    assert k1 == k2, (k1, k2)
    # 換一個受援國仍要分開
    k3 = eid.timeline_identity(dict(base, title="美國宣布對日本軍售"),
                               ["美國", "日本"], "2026-08-09")["key"]
    assert k3 != k1


def test_an_unknown_recipient_says_unknown_instead_of_guessing():
    """**點不出受詞就說不知道**(第二十八輪外審 P1-4)。

    上一版退回整個主體集合 —— 那等於拿「actor + recipient」冒充
    recipient:同一批軍售的兩則報導(一則標題寫得出「對台」、一則沒有)
    拿到兩個不同的 base key,而 sibling 比對只在同一個 base key 底下跑,
    救不回來。認不出受詞的同動作事件現在落在**同一條** provisional
    lineage 上。
    """
    assert eid.directional_object("arms_sale", "美台簽署軍售合約",
                                  ["美國", "台灣"]) == ""
    k = eid.timeline_identity({"event_type": "geopolitical",
                               "title": "美台簽署軍售合約"},
                              ["美國", "台灣"], "2026-08-09")["key"]
    assert k.endswith(f":{eid.UNKNOWN_OBJECT}:2026-08"), k
    # 同**主體**且同樣認不出受詞的落在同一個 base key 上(散開的話,
    # sibling 比對根本沒有機會跑)—— 但**不同主體不得共用一條線**,
    # 那一層由 `_hosts` 的主體相交把關(見下一條)。
    k2 = eid.timeline_identity({"event_type": "geopolitical",
                                "title": "美國批准新一批軍售案"},
                               ["美國"], "2026-08-09")["key"]
    assert k == k2, (k, k2)


def test_two_unknown_recipient_sales_by_different_actors_stay_apart(
        tmp_path, monkeypatch):
    """**`arms_sale:?` 不代表「全球所有未知受援國」**(第二十九輪外審
    P1-3)。

    美國與法國各一件軍售、受援國都認不出 → 同鍵;扣掉主體後只剩
    「FMS」一個辨識詞 → UNKNOWN;上一版只看同代同日就承接 ——
    兩國的軍售被壓成一條,第二件事件從報告消失。
    UNKNOWN 的承接現在要求**主體相交**。
    """
    evs = [{"event_type": "geopolitical", "entity": "美國",
            "title": "美國 FMS"},
           {"event_type": "geopolitical", "entity": "法國",
            "title": "法國 FMS"}]
    _, st = _run(tmp_path, monkeypatch, evs, day="2026-08-09")
    assert len(st) == 2, st
    subj = sorted(tuple(v["subjects"]) for v in st.values())
    assert subj == [("法國",), ("美國",)], subj


def test_three_actors_with_identical_tokens_get_three_lineages(
        tmp_path, monkeypatch):
    """**後綴撞到一條不是我們的線時要消歧**(外審第三輪)。

    三件同日的 unknown 受援國軍售辨識詞相同 → 後綴相同 → 第三件算出的
    鍵正是第二件的 sibling,而落盤那行會直接**覆寫掉**它 ——
    兩個 actor 的反例只需要一條 sibling,量不到這次碰撞。
    """
    evs = [{"event_type": "geopolitical", "entity": e, "title": f"{e} FMS"}
           for e in ("美國", "法國", "德國")]
    _, st = _run(tmp_path, monkeypatch, evs, day="2026-08-09")
    assert len(st) == 3, st
    subj = sorted(tuple(v["subjects"]) for v in st.values())
    assert subj == [("德國",), ("法國",), ("美國",)], subj
    # 隔天:單一辨識詞的 UNKNOWN **跨日不承接**(那是 P1-3 刻意的
    # fail-closed —— 繼承錯的天數比少算貴)。要驗的是:德國的後續
    # 不得污染美/法的線,也不得覆寫任何一條既有線。
    _, st2 = _run(tmp_path, monkeypatch,
                  [{"event_type": "geopolitical", "entity": "德國",
                    "title": "德國 FMS 進度"}],
                  day="2026-08-10", state=st)
    assert all(tuple(v["subjects"]) in (("美國",), ("法國",), ("德國",))
               for v in st2.values()), st2
    # 新的德國線**從第 1 天算起**(fail-closed 的另一半:不是繼承,
    # 也不是覆寫 —— 昨天那條還在)
    de2 = [v for v in st2.values()
           if v["subjects"] == ["德國"] and v["last_seen"] == "2026-08-10"]
    assert de2 and all(v["days"] == 1 for v in de2), st2
    assert not any(v["subjects"] != ["德國"] and v.get("days", 0) > 1
                   for v in st2.values()), "別國的線被德國的後續動到了"


def test_a_same_token_followup_does_not_overwrite_yesterdays_line(
        tmp_path, monkeypatch):
    """**消歧鍵也可能被昨天的自己佔著**(外審第四輪):同主體、同辨識詞
    的跨日 UNKNOWN 不承接(刻意),於是第二天的同名事件會算出同一把
    消歧鍵 —— 再蓋下去就是把昨天那條覆寫掉。"""
    evs = [{"event_type": "geopolitical", "entity": e, "title": f"{e} FMS"}
           for e in ("美國", "法國", "德國")]
    _, st = _run(tmp_path, monkeypatch, evs, day="2026-08-09")
    _, st2 = _run(tmp_path, monkeypatch,
                  [{"event_type": "geopolitical", "entity": "德國",
                    "title": "德國 FMS"}],       # 同標題 → 同後綴、同消歧鍵
                  day="2026-08-10", state=st)
    de = sorted((v["last_seen"], v["days"]) for v in st2.values()
                if v["subjects"] == ["德國"])
    assert de == [("2026-08-09", 1), ("2026-08-10", 1)], st2


def test_a_same_actor_followup_still_continues(tmp_path, monkeypatch):
    """**修正不得把該接的切斷**:同主體、同日的後續報導(UNKNOWN)
    仍要接得上。"""
    evs = [{"event_type": "geopolitical", "entity": "美國",
            "title": "美國 FMS"},
           {"event_type": "geopolitical", "entity": "美國",
            "title": "美國 FMS 進度"}]
    _, st = _run(tmp_path, monkeypatch, evs, day="2026-08-09")
    assert len(st) == 1, st


def test_cross_language_actor_spellings_share_one_unknown_lineage(
        tmp_path, monkeypatch):
    """**主體相交判準用的是 `CANONICAL_SUBJECTS`**(外審第二輪)——
    `France` 不在表裡的話,同一件法國軍售的中英報導在 UNKNOWN 承接的
    相交判準上是 {"法國"} vs {"France"},同日後續被錯分成 sibling。
    """
    evs = [{"event_type": "geopolitical", "entity": "法國",
            "title": "法國 FMS"},
           {"event_type": "geopolitical", "entity": "France",
            "title": "France FMS update"}]
    _, st = _run(tmp_path, monkeypatch, evs, day="2026-08-09")
    assert len(st) == 1, st
    assert next(iter(st.values()))["subjects"] == ["法國"], st


def test_timeline_and_bridge_resolve_the_object_identically():
    """**判準只能有一份**(P2-1):timeline 放 `?` 而 bridge 退回主體集合
    的話,同一則事件在分群與 timeline 拿到不同的對象身分 ——
    同一件事會重複佔據 top-event 與全文預算。"""
    title, subs = "美國批准新一批軍售案", ["美國"]
    assert eid.action_object("arms_sale", title, subs) == eid.UNKNOWN_OBJECT
    ident = eid.timeline_identity(
        {"event_type": "geopolitical", "title": title}, subs, "2026-08-09")
    assert f":{eid.action_object('arms_sale', title, subs)}:" in ident["key"]


def test_unknown_objects_do_not_bridge_across_languages():
    """**兩邊都不知道對象不等於同一個對象**:UNKNOWN 對 UNKNOWN 只說得出
    「都認不出受詞」—— 拿它當同對象會把美法各自的軍售橋在一起
    (P1-3 的跨語言側)。"""
    import cross_lang as cl
    assert not cl.bridge(
        {"title": "美國批准新一批軍售案 12 億美元", "entities": ["美國"]},
        {"title": "France approves $1.2 billion weapons sale",
         "entities": ["France"]})
    # **同 actor 也一樣**:bridge 退回主體集合的話,這一對的對象都是
    # 「美國」+ 金額相同 → 誤併;而受援國兩邊都認不出,「是不是同一批」
    # 根本答不出來 —— 誤併會讓一件真事件消失,漏併只是少一個佐證。
    assert not cl.bridge(
        {"title": "美國批准新一批軍售案 12 億美元", "entities": ["美國"]},
        {"title": "US approves new $1.2 billion weapons sale",
         "entities": ["United States"]})
    # 而受援國**認得出**的同一批仍然要橋得起來(修正不得把該接的切斷)
    assert cl.bridge(
        {"title": "美國批准對台軍售 12 億美元", "entities": ["美國", "台灣"]},
        {"title": "US approves $1.2 billion arms sale to Taiwan",
         "entities": ["United States", "Taiwan"]})


def test_a_recipient_in_the_summary_is_found():
    """**標題寫不出受援國時看 summary**(外審 P1-4 的反例)。

    "US approves new weapons package" + “intended for Taiwan” 要與
    「美國批准對台軍售」落在同一條線上。
    """
    en = eid.timeline_identity(
        {"event_type": "geopolitical", "title": "US approves new weapons package",
         "summary": "The package is intended for Taiwan."},
        ["United States", "Taiwan"], "2026-08-09")["key"]
    zh = eid.timeline_identity(
        {"event_type": "geopolitical", "title": "美國批准對台軍售"},
        ["美國", "台灣"], "2026-08-09")["key"]
    assert en == zh, (en, zh)


def test_the_suffix_uses_the_whole_token_set():
    """**後綴只雜湊前四個詞的話,前四個剛好相同的兩樁事會共用後綴** ——
    而後綴正是用來把它們分開的東西。"""
    a = ["aa", "bb", "cc", "dd", "ee"]
    b = ["aa", "bb", "cc", "dd", "zz"]
    assert eid.incident_suffix(a) != eid.incident_suffix(b)


def test_a_direction_marker_is_only_trusted_where_it_is_declared():
    """**「對台影響」是後果子句,不是受詞**(外審第二輪 F1)。

    `jurisdiction` 那一組還包含選舉、峰會、匯率干預 —— 一律套方向詞的話,
    「美國大選對台影響」與「日本大選對台影響」會拿到同一個 `台灣`,
    於是日本大選繼承美國大選的延燒天數。軍售的「對 X」在語意上就是
    受援國,那是目前唯一站得住的一個。
    """
    assert eid.directional_object("election", "美國大選對台影響",
                                  ["美國", "台灣"]) == ""
    assert eid.directional_object("summit_talks", "美中峰會對台影響",
                                  ["美國", "中國", "台灣"]) == ""
    a = eid.timeline_identity({"event_type": "geopolitical",
                               "title": "美國大選對台影響"},
                              ["美國", "台灣"], "2026-08-09")["key"]
    b = eid.timeline_identity({"event_type": "geopolitical",
                               "title": "日本大選對台影響"},
                              ["日本", "台灣"], "2026-08-09")["key"]
    assert a != b, (a, b)


def test_the_stored_tokens_are_not_shorter_than_what_we_compare_with(
        tmp_path, monkeypatch):
    """**存幾個要與比對用的一致**(外審第二輪 F2)。

    存 12 個而後綴吃 24 個的話,隔天比對的分母是被截短的那一份 ——
    重疊率被灌高,本來是 `NO_MATCH` 的兩樁事會判成 `MATCH` 而直接承接
    lineage(`incident_suffix` 根本不會被呼叫)。
    """
    long_title = "藥華藥遭勒索軟體攻擊 產線停擺 客戶名單外洩 主管請辭 調查展開"
    _, st = _run(tmp_path, monkeypatch,
                 [{"event_type": "cybersecurity", "entity": "藥華藥",
                   "title": long_title}], day="2026-08-09")
    rec = next(iter(st.values()))
    full = sorted(eid.discriminative_tokens(long_title, ["藥華藥"]))
    assert len(full) > 12, f"這個標題切不出超過 12 個辨識詞,量不到:{full}"
    assert len(rec["incident_tokens"]) == min(len(full),
                                              eid.MAX_SUFFIX_TOKENS), rec


def test_a_competing_direction_phrase_does_not_steal_the_object():
    """**取第一個方向詞會被前面的子句搶走**(外審第三輪)。

    "US responds to China with arms sale to Taiwan" 的第一個 " to " 指向
    中國,而受援國是台灣 —— 同一批軍售於是分裂成兩個 base key。
    中文的語序把方向詞放在關鍵詞**前面**(「對台軍售」),英文放在後面,
    所以判準是**離動作關鍵詞的距離**,不是先後。
    """
    for title, ents in (
            ("US responds to China with arms sale to Taiwan",
             ["United States", "China", "Taiwan"]),
            ("美國回應中國後宣布對台軍售", ["美國", "中國", "台灣"])):
        assert eid.directional_object("arms_sale", title, ents) == "台灣", title
    # 同一批軍售的兩則報導因此仍是同一個 base key
    k1 = eid.timeline_identity(
        {"event_type": "geopolitical", "title": "US approves arms sale to Taiwan"},
        ["United States", "Taiwan"], "2026-08-09")["key"]
    k2 = eid.timeline_identity(
        {"event_type": "geopolitical",
         "title": "US responds to China with arms sale to Taiwan"},
        ["United States", "China", "Taiwan"], "2026-08-09")["key"]
    assert k1 == k2, (k1, k2)


def test_an_unrelated_marker_in_the_title_does_not_block_the_summary():
    """**閘門要看「有沒有找到受詞」,不是「有沒有出現方向詞」**
    (外審第二輪 F3)。

    「美國軍售最新動**向**」的「向」會讓上一版以為標題找得到,
    summary 那條路就走不到了 —— 而受援國寫在 summary 裡。
    """
    assert eid.directional_object("arms_sale", "美國軍售最新動向",
                                  ["美國", "台灣"],
                                  summary="武器售予台灣") == "台灣"
    # 英文的無關 "for" 同理
    assert eid.directional_object(
        "arms_sale", "US weapons package under review for approval",
        ["United States", "Taiwan"],
        summary="The package is intended for Taiwan.") == "台灣"


# ===== 縱深第五批(2026-08-10):態勢的連續性 =====

_T1 = "荷莫茲傳禁美以船隻通行伊朗嗆聲攻擊「敵意目標」 | 中東戰火連綿| 全球 | 聯合新聞網 - UDN"
_T2 = "重啟荷莫茲…伊朗提嚴苛條件 要求美軍撤離、永久結束戰爭等 增添談判變數 - money.udn.com"


def test_title_furniture_is_not_event_content():
    """**來源與版名不是事件內容**:2026-08-10 實際 state 的
    incident_tokens 裡有 `com`/`money`/`udn`/「聯合」「新聞」——
    家具不同稀釋重疊、家具相同灌高重疊,兩個方向都在扭曲判斷。"""
    a = eid.discriminative_tokens(_T1, {"伊朗"})
    b = eid.discriminative_tokens(_T2, {"伊朗"})
    assert not ({"udn", "com", "money", "聯合", "新聞"} & (a | b)), (a, b)
    assert "荷莫" in a and "荷莫" in b        # 內容要活著


def test_furniture_stripping_is_conservative():
    """只剝**尾端的短標籤**:長尾巴、帶句讀的尾巴、剝完太短的都不動。"""
    strip = eid.strip_title_furniture
    assert strip(_T2) == "重啟荷莫茲…伊朗提嚴苛條件 要求美軍撤離、永久結束戰爭等 增添談判變數"
    # 尾巴超過上限 = 可能是真句子,不剝(頭要夠長 —— 反例要只靠
    # 長度上限分勝負,第一版的頭太短被別的守衛先擋住,量不到上限)
    long_tail = "台積電召開法說會 - 資本支出上修至三百億美元並宣布擴建兩座先進封裝新廠房"
    assert strip(long_tail) == long_tail
    # 帶引號的尾巴是內文,不剝
    quoted = "外資喊「買進」 - 目標價「上看 1,500」"
    assert strip(quoted) == quoted
    # 剝完剩太短 → 原樣退回
    assert strip("短 - UDN") == "短 - UDN"
    assert strip("") == ""
    # **全形｜在真實標題裡有語意用途**(外審 r1):長度不是家具的證據,
    # 註冊表認得出來才是 —— 尾段不是已知發布者,一段都不剝。
    assert strip("美國宣布新制裁｜鎖定無人機供應鏈") == "美國宣布新制裁｜鎖定無人機供應鏈"
    # 發布者錨定時,它前面的極短導覽段一併剝;較長的內容段留下
    assert strip("內容主體夠長的標題｜鎖定無人機供應鏈｜聯合新聞網") == "內容主體夠長的標題｜鎖定無人機供應鏈"
    # **含發布者的引述是內容,不是標籤**(外審 r2):`owner_of` 對中文
    # 別名是子字串比對,「路透:…」整句也會命中 —— 尾段要**是**標籤
    # (短、無冒號)才剝。
    quoted_wire = "美伊談判最新｜路透:雙方仍未就停火條件達成協議"
    assert strip(quoted_wire) == quoted_wire
    short_wire = "內容主體夠長的標題｜路透:停火近了"
    assert strip(short_wire) == short_wire
    # 反例要**只靠長度規則**分勝負(無冒號、無句讀、含發布者子字串):
    # 上一版的長引述帶冒號,長度上限被冒號守衛遮住,量不到。
    long_no_colon = "美伊談判最新｜路透獨家披露停火條件全文內容整理"
    assert strip(long_no_colon) == long_no_colon
    # **全形冒號才是生產的常態**(外審 r3):守衛只認半形 `:` 的話,
    # 「路透：停火近了」照樣被剝 —— 同一個缺陷換一個字元活著。
    fw = "內容主體夠長的標題｜路透：停火近了"
    assert strip(fw) == fw


def test_short_pipe_suffixes_still_distinguish_incidents(tmp_path,
                                                         monkeypatch):
    """**分辨兩樁的內容全在短管線尾段時,兩條線仍是兩條**(外審 r1 要求
    的回歸):尾段被誤當家具剝掉的話,兩案的辨識詞變成同一組 → 併線、
    天數接錯。"""
    t_a = "美國宣布新制裁｜鎖定無人機零組件供應鏈"
    t_b = "美國宣布新制裁｜打擊祕密油輪船隊與空殼公司"
    a = eid.discriminative_tokens(t_a, {"美國"})
    b = eid.discriminative_tokens(t_b, {"美國"})
    assert eid.incident_match(a, b) == eid.NO_MATCH, (a, b)
    _run(tmp_path, monkeypatch,
         [{"event_type": "export_controls", "entity": "美國", "title": t_a}],
         day="2026-08-05", state={})
    import json as _json
    st = _json.loads((tmp_path / "tl.json").read_text(encoding="utf-8"))
    active, new = _run(tmp_path, monkeypatch,
                       [{"event_type": "export_controls", "entity": "美國",
                         "title": t_b}],
                       day="2026-08-06", state=st)
    assert len([k for k in new if "sanction" in k]) == 2, sorted(new)


def test_a_situation_line_survives_its_own_development(tmp_path, monkeypatch):
    """**態勢不切樁**:荷莫茲 08-08 的禁航與 08-10 的談判條件,辨識詞
    天然重疊很低(同一條線的**發展**本來就會換詞)—— 上一版 NO_MATCH
    → `#04d558` 第 1 天(2026-08-10 實信)。態勢動作的鍵本身就是身分;
    **舊代 state 也要當場接回**(候選 schema 10,政策放在世代檢查之前)。"""
    state = {"geopolitical:hormuz_passage:2026-08": {
        "first_seen": "2026-08-05", "days": 2, "last_seen": "2026-08-08",
        "latest_title": _T1, "entity": "伊朗", "identity_schema": 10,
        "subjects": ["伊朗"],
        "incident_tokens": sorted(eid.discriminative_tokens(_T1, {"伊朗"}))}}
    active, new = _run(
        tmp_path, monkeypatch,
        [{"event_type": "geopolitical", "entity": "伊朗、美國", "title": _T2}],
        day="2026-08-10", state=state)
    keys = [k for k in new if "hormuz" in k]
    assert keys == ["geopolitical:hormuz_passage:2026-08"], keys
    assert new[keys[0]]["days"] == 3, new[keys[0]]
    assert new[keys[0]]["last_seen"] == "2026-08-10"


def test_incident_actions_still_split_distinct_cases(tmp_path, monkeypatch):
    """**修正不得把逐樁切分關掉**(第二十七輪的行為):同月對同一國的
    兩輪不同制裁案仍然是兩條線 —— 切分正是為「會完成的行為」設計的。
    (第一版反例用 cybersecurity,而那個型別根本不進 timeline ——
    被前置守衛先擋住的反例量不到缺陷。)"""
    t_a = "美財政部最新制裁 打擊伊朗祕密貨幣交易網絡與空殼公司"
    t_b = "美國宣布新一輪制裁 鎖定伊朗無人機零組件供應鏈與航運仲介"
    _run(tmp_path, monkeypatch,
         [{"event_type": "export_controls", "entity": "美國", "title": t_a}],
         day="2026-08-05", state={})
    import json as _json
    st = _json.loads((tmp_path / "tl.json").read_text(encoding="utf-8"))
    assert any("sanction" in k for k in st), st    # 前提:動作真的被認出
    active, new = _run(tmp_path, monkeypatch,
                       [{"event_type": "export_controls", "entity": "美國",
                         "title": t_b}],
                       day="2026-08-06", state=st)
    assert len([k for k in new if "sanction" in k]) == 2, sorted(new)


def test_situation_actions_are_declared_states_not_acts():
    """宣告的完整性:表裡的每一個都要真的在 ACTION_TABLE(拼錯的動作名
    永遠不會命中 —— 守衛靜默失效的形狀);而「會完成的行為」不得混入。"""
    from event_actions import ACTION_TABLE, SITUATION_ACTIONS
    known = {row[0] for row in ACTION_TABLE}
    assert SITUATION_ACTIONS <= known, SITUATION_ACTIONS - known
    assert not ({"arms_sale", "cyberattack", "sanction", "export_control",
                 "tariff_action"} & SITUATION_ACTIONS)
    assert "hormuz_passage" in SITUATION_ACTIONS


# ===== 第三十輪外審 Commit 1:身分的兩個 false-merge =====

def test_two_summit_rounds_same_counterpart_same_month_are_distinct(
        tmp_path, monkeypatch):
    """**會重複發生的回合不是持續態勢**(外審 P1-1):同一組對手同月的
    貿易談判與安全會談,鍵完全相同(型別:動作:對象:月)—— 上一版把
    summit_talks 當態勢、跳過逐樁切分,兩件事被寫成同一條延燒故事。"""
    a = {"event_type": "geopolitical", "entity": "美國、中國",
         "title": "美中日內瓦貿易談判結束"}
    b = {"event_type": "geopolitical", "entity": "美國、中國",
         "title": "美中兩週後舉行另一輪安全會談"}
    ia = eid.timeline_identity(a, ["美國", "中國"], "2026-08-10")
    ib = eid.timeline_identity(b, ["美國", "中國"], "2026-08-10")
    assert ia["key"] == ib["key"], "前提:鍵相同,分辨只能靠逐樁切分"
    assert not eid.is_situation_action("summit_talks")
    _run(tmp_path, monkeypatch, [a], day="2026-08-10", state={})
    import json as _json
    st = _json.loads((tmp_path / "tl.json").read_text(encoding="utf-8"))
    _, new = _run(tmp_path, monkeypatch, [b], day="2026-08-11", state=st)
    assert len([k for k in new if "summit_talks" in k]) == 2, sorted(new)


def test_two_elections_same_country_same_month_are_distinct(tmp_path,
                                                            monkeypatch):
    """同一國同月的兩場不同選舉同理 —— 上一版等於宣告「法國八月只會有
    一場選舉」。"""
    a = {"event_type": "geopolitical", "entity": "法國",
         "title": "法國國會選舉完成第一輪投票"}
    b = {"event_type": "geopolitical", "entity": "法國",
         "title": "法國地方選舉開票結果出爐 執政黨席次減少"}
    assert (eid.timeline_identity(a, ["法國"], "2026-08-10")["key"]
            == eid.timeline_identity(b, ["法國"], "2026-08-10")["key"])
    assert not eid.is_situation_action("election")
    _run(tmp_path, monkeypatch, [a], day="2026-08-10", state={})
    import json as _json
    st = _json.loads((tmp_path / "tl.json").read_text(encoding="utf-8"))
    _, new = _run(tmp_path, monkeypatch, [b], day="2026-08-11", state=st)
    assert len([k for k in new if "election" in k]) == 2, sorted(new)


def test_persistent_theatre_still_survives_rewording(tmp_path, monkeypatch):
    """**收窄不得把荷莫茲的修正一起收掉**:持續態勢照樣接得回同一條線。"""
    state = {"geopolitical:hormuz_passage:2026-08": {
        "first_seen": "2026-08-05", "days": 2, "last_seen": "2026-08-08",
        "latest_title": _T1, "entity": "伊朗", "identity_schema": 10,
        "subjects": ["伊朗"],
        "incident_tokens": sorted(eid.discriminative_tokens(_T1, {"伊朗"}))}}
    _, new = _run(tmp_path, monkeypatch,
                  [{"event_type": "geopolitical", "entity": "伊朗、美國",
                    "title": _T2}], day="2026-08-10", state=state)
    keys = [k for k in new if "hormuz" in k]
    assert keys == ["geopolitical:hormuz_passage:2026-08"], keys
    assert new[keys[0]]["days"] == 3


def test_the_theatre_table_only_holds_persistent_states():
    """宣告的完整性:表裡的每一個都要真的在 ACTION_TABLE(拼錯的動作名
    永遠不會命中);**會重複發生的回合與會完成的行為都不得混入**。"""
    from event_actions import ACTION_TABLE, SITUATION_ACTIONS
    known = {row[0] for row in ACTION_TABLE}
    assert SITUATION_ACTIONS <= known, SITUATION_ACTIONS - known
    assert not ({"election", "summit_talks", "arms_sale", "cyberattack",
                 "sanction", "export_control", "tariff_action"}
                & SITUATION_ACTIONS)
    assert SITUATION_ACTIONS == {"hormuz_passage", "strait_tension"}


# ---------------------------------------------------------------- 家具辨識

def test_a_semantic_dash_suffix_is_not_stripped():
    """**長度不是家具的證據**(外審 P1-4):兩則重大訊息的尾段都短、
    都沒句讀 —— 靠長度剝的話會被壓成同一個標題、同一組辨識詞,
    兩件事併成一條 lineage。"""
    strip = eid.strip_title_furniture
    a = "台積電重大訊息公告 - 高雄廠停工"
    b = "台積電重大訊息公告 - 董事長辭任"
    assert strip(a) == a and strip(b) == b
    assert eid.incident_match(
        eid.discriminative_tokens(a, {"台積電"}),
        eid.discriminative_tokens(b, {"台積電"})) == eid.NO_MATCH
    en_a = "Apple guidance update today - guidance cut"
    en_b = "Apple guidance update today - dividend hike"
    assert strip(en_a) == en_a and strip(en_b) == en_b
    # **發布者 + 兩個字的內容**(外審 r1):扣掉媒體名之後剩下的正是
    # 分辨兩樁的東西,而它剛好兩個字 —— 舊版的 `<= 2` 餘裕會整段剝掉。
    wire_a = "美伊談判進入關鍵階段｜路透停火"
    wire_b = "美伊談判進入關鍵階段｜路透破局"
    assert strip(wire_a) == wire_a and strip(wire_b) == wire_b
    assert not eid._is_publisher_tail("路透停火")
    assert not eid._is_publisher_tail("彭博破局")


def test_a_recognised_publisher_suffix_is_still_stripped():
    """**修正不得把家具剝除一起關掉**:認得出來的發布者照剝
    (註冊表、宣告表、網域形狀、中英並列的招牌)。"""
    strip = eid.strip_title_furniture
    assert strip("某公司第二季財報揭曉 - 經濟日報") == "某公司第二季財報揭曉"
    assert strip("某公司第二季財報揭曉 - ftnn.com.tw") == "某公司第二季財報揭曉"
    assert strip("某公司第二季財報揭曉 - TechNews 科技新報") == "某公司第二季財報揭曉"
    # **招牌尾字**那張表的用途:名字扣掉之後剩「網」「財經雲」這種招牌用語
    # 也算標籤(這條反例只靠那張表分勝負 —— 別的規則都認不出它)
    assert strip("某公司第二季財報揭曉 - ETtoday財經雲") == "某公司第二季財報揭曉"
    assert strip("某公司第二季財報揭曉 - LINE TODAY") == "某公司第二季財報揭曉"
    # 多層尾巴逐段剝(真實資料是「內容 - 站名 - 發布者」)
    assert strip("某公司第二季財報揭曉 - 民視新聞網 - LINE TODAY") == "某公司第二季財報揭曉"


def test_a_short_pipe_segment_is_not_navigation_by_default():
    """管線段同樣要正面辨識:「法說」只有兩個字,而它是內容;
    宣告過的版名(「全球」)才剝。"""
    strip = eid.strip_title_furniture
    assert strip("台積電重大訊息公告｜法說｜經濟日報") == "台積電重大訊息公告｜法說"
    assert strip("台積電重大訊息公告｜全球｜經濟日報") == "台積電重大訊息公告"


def test_the_furniture_tables_are_declarations_not_heuristics():
    """兩張表的宣稱要成立:招牌尾字裡不得混入事件內容用語。"""
    from event_identity import _OUTLET_SUFFIX_WORDS, _SECTION_LABELS
    bad = {"停工", "法說", "辭任", "罷工", "召回", "併購", "漲價"}
    assert not (bad & set(_OUTLET_SUFFIX_WORDS))
    assert not (bad & _SECTION_LABELS)
    assert _OUTLET_SUFFIX_WORDS and _SECTION_LABELS



# ------------------------------------------------- 第三十一輪外審 P1-1

def test_summary_only_recipient_matches_in_match_days():
    """受詞只在 summary 的日子,`match_days` 要與 timeline 同一個答案。

    先前它自己算 `event_action(titles)` + `object_signature(action, ents)`
    —— 不吃 summary、對象退回主體集合;timeline 早知道受援國才是對象。
    """
    import event_identity as eid
    rec = [{"subjects": ["美國", "台灣"], "action": "arms_sale",
            "object": "台灣", "days": 3,
            "latest_title": "美國宣布對台軍售", "latest_summary": ""}]
    got = eid.match_days(rec, ["美國", "台灣"], "美國軍售最新動向",
                         summary="package intended for Taiwan")
    assert got == 3, got


def test_actor_presence_does_not_change_continuing_days():
    """actor 有沒有被實體抽取抽出來,不改事件身分(受詞才是身分)。"""
    import event_identity as eid
    rec = [{"subjects": ["台灣"], "action": "arms_sale", "object": "台灣",
            "days": 5, "latest_title": "對台軍售追蹤", "latest_summary": ""}]
    with_actor = eid.match_days(rec, ["美國", "台灣"], "美國宣布對台軍售")
    without = eid.match_days(rec, ["台灣"], "對台軍售新進展")
    assert with_actor == without == 5, (with_actor, without)


def test_a_stored_object_wins_over_recomputation():
    """記錄側優先用**存下來的**對象 —— 它是當天(受詞可能只在當天的
    summary)算好的身分;重算只能看 subjects,而受援國不一定在裡面。"""
    import event_identity as eid
    rec = [{"subjects": ["美國"], "action": "arms_sale",
            "object": "台灣", "days": 4,
            "latest_title": "美國軍售案追蹤", "latest_summary": ""}]
    # 今天明確是對台軍售;記錄的 subjects 沒有台灣(當天從 summary 抽的)
    # —— 只有讀「存下來的對象」才接得上。
    got = eid.match_days(rec, ["美國", "台灣"], "美國宣布對台軍售")
    assert got == 4, got


def test_a_signature_fallback_object_is_a_candidate_set():
    """producer 存明確對象(伊朗)、消費端退回簽章(伊朗、美國)——
    等值比對永遠對不上;退回的簽章當候選集合,明確側在裡面就算對上。"""
    import event_identity as eid
    rec = [{"key": "geopolitical:sanction:伊朗:2026-08", "subjects": ["伊朗"],
            "action": "sanction", "object": "伊朗", "days": 2,
            "latest_title": "美國宣布對伊朗新一輪經濟制裁措施",
            "latest_summary": ""}]
    t = "美國對伊朗制裁 波斯灣航運受阻"
    assert eid.match_days(rec, ["美國", "伊朗"], t) == 2
    assert eid.match_lineage(rec, ["美國", "伊朗"], t) ==         "geopolitical:sanction:伊朗:2026-08"


def test_two_ambiguous_signatures_still_require_equality():
    """兩側都分不出對象時仍要求相等 —— 猜了就是擲骰子。"""
    import event_identity as eid
    assert eid._objects_agree("伊朗、美國", "俄羅斯、美國") is False
    assert eid._objects_agree("伊朗、美國", "伊朗、美國") is True
    # 明確 vs 明確:不同就是不同(對台/對日不得互認)
    assert eid._objects_agree("台灣", "日本") is False


# --------------------------------------- 第三十二輪外審 P1-1:逐樁 lineage

def _cyber_rec(title, days=7, **over):
    import event_identity as eid
    rec = {"key": "geopolitical:cyberattack:台積電:2026-08",
           "subjects": ["台積電"], "action": "cyberattack",
           "object": "台積電", "days": days, "latest_title": title,
           "latest_summary": "",
           "incident_tokens": sorted(eid.discriminative_tokens(
               title, ["台積電"]))}
    rec.update(over)
    return rec


def test_a_new_same_company_incident_does_not_inherit_old_days():
    """同公司同月的第二起資安事件是**另一樁** —— 不看 incident 辨識詞
    的話,新樁繼承舊樁 7 天、拿到延燒排序與全文優先權。"""
    import event_identity as eid
    old = _cyber_rec("台積電遭勒索軟體攻擊 產線停擺")
    # 反例要**只靠 incident 那關**分勝負:這個標題的 action 也是
    # cyberattack(action 那關擋不住它),辨識詞重疊 1/9 < 門檻。
    t_new = "台積電再遭網路攻擊 供應商系統資料外洩"
    assert eid.match_days([old], ["台積電"], t_new) == 0
    assert eid.match_lineage([old], ["台積電"], t_new) == ""


def test_the_same_incident_still_inherits():
    import event_identity as eid
    old = _cyber_rec("台積電遭勒索軟體攻擊 產線停擺")
    assert eid.match_days([old], ["台積電"],
                          "台積電勒索軟體攻擊第七天 產線停擺持續") == 7


def test_a_legacy_record_without_tokens_is_not_vetoed():
    """UNKNOWN 保守不否決 —— 升版當天 state 幾乎全是舊代記錄,
    硬否決等於把所有延燒歸零。"""
    import event_identity as eid
    old = _cyber_rec("台積電遭勒索軟體攻擊 產線停擺")
    old.pop("incident_tokens")
    assert eid.match_days([old], ["台積電"],
                          "台積電勒索軟體攻擊 產線停擺持續") == 7


def test_cross_language_inheritance_requires_an_incident_anchor():
    """跨書寫系統的零共用是語言差異,不是事件差異 —— 但也不能白拿:
    要一個逐樁錨(這裡:同幣別同量級金額)。"""
    import event_identity as eid
    old = _cyber_rec("台積電遭勒索軟體攻擊 損失20億美元")
    assert eid.match_days([old], ["台積電"],
                          "TSMC ransomware attack causes $2 billion loss") == 7
    assert eid.match_days([old], ["台積電"],
                          "TSMC ransomware attack under investigation") == 0


def test_situation_actions_are_not_split_per_incident():
    """persistent situation(荷姆茲通行)本來就不逐樁 —— 每天的報導
    辨識詞都不同,逐樁否決會讓它天天歸零。"""
    import event_identity as eid
    sit = {"key": "geopolitical:hormuz_passage:伊朗:2026-08",
           "subjects": ["伊朗"], "action": "hormuz_passage", "object": "",
           "days": 9, "latest_title": "荷姆茲海峽通行受阻",
           "latest_summary": "", "incident_tokens": ["通行", "受阻"]}
    assert eid.match_days([sit], ["伊朗"], "荷姆茲海峽危機 油輪改道") == 9


def test_fetch_plan_does_not_boost_a_new_same_company_incident():
    """端到端:fetch_plan 的延燒加權走同一個判準 —— 新樁不得拿
    舊樁的全文優先權。"""
    import fetch_plan as fp
    old = _cyber_rec("台積電遭勒索軟體攻擊 產線停擺")
    by_id = {"n1": {"source_item_id": "n1",
                    "title": "台積電再遭網路攻擊 供應商系統資料外洩",
                    "entities": ["台積電"], "summary": ""}}
    c = {"cluster_id": "cluster:n1", "member_source_ids": ["n1"]}
    assert fp._continuing(c, by_id, [old]) == 0


# ---------------------------------------- 外審 2026-08-17 P1-1:UNKNOWN 有兩種

def _cyber_record(**over):
    """現行代的一樁網攻記錄(帶足夠的辨識詞)。"""
    rec = {"subjects": ["TSMC"], "action": "cyberattack", "object": "TSMC",
           "key": "old-tsmc-cyber-A", "days": 7,
           "incident_tokens": ["ransomware", "fab", "halted"],
           "latest_title": "TSMC hit by ransomware, fab output halted",
           "latest_summary": ""}
    rec.update(over)
    return [rec]


def test_current_schema_unknown_incident_does_not_inherit_old_lineage_days():
    """**今天的證據不足,不等於是同一樁。**

    記錄側是現行代(3 個辨識詞),今天的標題 `TSMC cyberattack` 扣掉主體
    只剩 1 個辨識詞 → `incident_match` 回 UNKNOWN。先前 UNKNOWN 一律放行,
    於是全新的一樁繼承 7 天與舊 lineage,被寫成「第 8 天」,還拿到延燒
    排序與全文優先權。
    **producer 早就是這個政策**(`incident_match` docstring:跨代/跨日
    一律另開 provisional sibling、不繼承天數)—— consumer 先前與它相反。
    """
    import event_identity as eid
    recs = _cyber_record()
    assert eid.incident_match(
        eid.view_identity("TSMC cyberattack", ["TSMC"], "")["incident_tokens"],
        recs[0]["incident_tokens"]) == eid.UNKNOWN, "前提:這是 UNKNOWN"
    assert eid.match_days(recs, ["TSMC"], "TSMC cyberattack", "") == 0
    assert eid.match_lineage(recs, ["TSMC"], "TSMC cyberattack", "") == ""


def test_legacy_record_without_incident_tokens_can_still_migrate():
    """**舊代記錄沒有辨識詞是遷移相容,不是證據不足。**

    升版當天 state 幾乎全是舊代記錄 —— 把它們一起 fail-closed 會讓所有
    延燒事件在那一天集體斷線。兩種 UNKNOWN 要有兩個答案。
    """
    import event_identity as eid
    assert eid.match_days(_cyber_record(incident_tokens=[]),
                          ["TSMC"], "TSMC cyberattack", "") == 7


def test_an_overlapping_incident_still_continues():
    """反向:真的是同一樁(辨識詞重疊)不得被新規則擋掉。"""
    import event_identity as eid
    assert eid.match_days(_cyber_record(), ["TSMC"],
                          "TSMC ransomware attack halted fab output", "") == 7


def test_cross_language_unknown_requires_a_specific_anchor():
    """跨語言 + 今天證據不足 → 要有逐樁錨(金額/帶單位數量/第三實體)
    才承接。零共用在跨語言是語言差異,但「今天說不出是哪一樁」不是。"""
    import event_identity as eid
    recs = _cyber_record(latest_title="台積電遭勒索軟體攻擊 產線停工",
                         incident_tokens=["勒索", "停工", "產線"],
                         subjects=["台積電"])
    assert eid.match_days(recs, ["TSMC"], "TSMC cyberattack", "") == 0
