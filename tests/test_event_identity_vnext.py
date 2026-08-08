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
    state = {"geopolitical:伊朗:2026-08": {
        "first_seen": "2026-08-01", "days": 7, "last_seen": "2026-08-08",
        "latest_title": "川普稱與伊朗戰爭很快將結束", "action": "",
        "entity": "伊朗", "subjects": ["伊朗"], "event_type": "geopolitical",
        "identity_schema": eid.IDENTITY_SCHEMA_VERSION},
        "geopolitical:hormuz_passage:2026-08": {
        "first_seen": "2026-08-07", "days": 1, "last_seen": "2026-08-08",
        "latest_title": "荷姆茲有望重啟", "action": "hormuz_passage",
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
    day1 = [{"event_type": "geopolitical", "entity": "藥華藥",
             "title": "藥華藥遭勒索軟體攻擊 產線停擺"},
            {"event_type": "geopolitical", "entity": "藥華藥",
             "title": "藥華藥遭駭客入侵 客戶名單外洩"}]
    _, st = _run(tmp_path, monkeypatch, day1, day="2026-08-01")
    assert len(st) == 2, st
    base = next(k for k in st if "#" not in k)
    sib = next(k for k in st if "#" in k)

    # 之後只有「名單外洩」那一樁持續有報導,base 那樁停更
    for n, extra in ((2, "後續"), (3, "最新進度"), (4, "主管請辭負責"),
                     (5, "調查結果出爐"), (6, "和解金額出爐")):
        _, st = _run(tmp_path, monkeypatch,
                     [{"event_type": "geopolitical", "entity": "藥華藥",
                       "title": f"藥華藥遭駭客入侵 客戶名單外洩 {extra}"}],
                     day=f"2026-08-{n:02d}", state=st)
    assert base not in st, "base 那樁停更超過保留期,應該已經退場"
    assert sib in st, f"還在燒的那一樁被丟了:{sorted(st)}"
    assert st[sib]["days"] == 6, {k: v["days"] for k, v in st.items()}
    assert len(st) == 1, f"base 被重開了 —— 天數從第 1 天重算:{sorted(st)}"
