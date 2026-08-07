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


def test_an_unrecognised_action_degrades_to_the_old_behaviour():
    """**認不出動作是合法答案。** 降級回主體集合 —— 行為與舊版相同,
    不是拿一個猜出來的動作把兩件事黏在一起。"""
    out = eid.timeline_identity(
        {"event_type": "geopolitical", "title": "兩國代表昨日於第三地會面"},
        ["美國", "伊朗"], "2026-08-07")
    assert out["action"] == "" and out["basis"] == "subjects"
    assert out["key"] == "geopolitical:伊朗、美國"


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
