# -*- coding: utf-8 -*-
"""**資安攻擊不是地緣攻擊**(repo-wide 外審 2026-08-18,P2-1)。

`_event_type` 的 geopolitical 規則收「attack / 攻擊」,於是任何公司的資安
新聞都變成地緣政治事件。**生產已經命中**:

    entity=AAPL、event_type=geopolitical、max_surprise=0.90
    ← 「Apple 發出間諜軟體威脅通知 用戶恐成攻擊目標」
    ← 「【美股巨頭】博通單日跌6%,駭客攻擊VMware讓市場重新定價軟體風險」

這不是標籤好不好看的問題:geopolitical 拿 0.90 的意外度(prompt 明說
`surprise_score >= 0.6` 要優先且醒目處理),催化評分的確定性 fallback 對
geopolitical 與 general 也給不同權重 —— 分類錯**真的改了優先順序與影響量級**。

而身分層早就另有 `cyberattack` 動作(`event_actions.ACTION_TABLE`)——
**兩層 taxonomy 自己互相矛盾**:一層說 cyberattack、另一層說 geopolitical。
"""
import event_actions as ea
import morning_report as mr
import news_events as ne

#: **從生產 state 抄下來的兩則**(2026-08-18 的 `story_ledger.json`)。
_PRODUCTION_MISLABELLED = (
    "Apple 發出間諜軟體威脅通知 用戶恐成攻擊目標 - 自由電子報3C科技",
    "【美股巨頭】博通單日跌6%，駭客攻擊VMware讓市場重新定價軟體風險 - CMoney投資網誌",
)


def test_the_two_production_headlines_are_no_longer_geopolitical():
    """**這兩則是真的被寫進 state 的。** 它們不得再是地緣政治事件。"""
    for title in _PRODUCTION_MISLABELLED:
        got = ne._event_type(title)
        assert got == "cybersecurity", (got, title)


def test_a_real_geopolitical_attack_is_still_geopolitical():
    """**反向:這條規則不是把 geopolitical 關掉。**

    只擋不放的判準會讓真正的軍事衝突掉進 general —— 那比錯誤分類更難察覺。
    """
    for title in ("Israel missile attack on Gaza",
                  "Iran attacks tanker in Hormuz Strait",
                  "俄烏戰爭再升級 飛彈攻擊基輔"):
        assert ne._event_type(title) == "geopolitical", title


def test_the_cyber_rule_runs_before_the_geopolitical_one():
    """**順序就是判準本身。** 兩條都收得到「攻擊」,先跑的那條贏。"""
    both = "駭客攻擊造成產線停擺"
    assert ne._event_type(both) == "cybersecurity", ne._event_type(both)


def test_the_vocabulary_has_exactly_one_copy():
    """身分層與型別層讀**同一份宣告** —— 兩份會分歧,而分歧的症狀正是
    這次的缺陷(一層 cyberattack、另一層 geopolitical)。"""
    declared = next(row[2:] for row in ea.ACTION_TABLE if row[0] == "cyberattack")
    assert tuple(ne._cyber_tokens()) == tuple(declared)
    assert ne._cyber_tokens(), "空詞彙表 = 這條規則整個消失"


def test_the_declared_vocabulary_covers_the_production_wording():
    """**生產實際的寫法要收得到。** 原本的表有「駭客入侵」而沒有
    「駭客攻擊」、有「勒索軟體」而沒有「間諜軟體」—— 那兩則就是這樣漏掉的。"""
    tokens = set(ne._cyber_tokens())
    for word in ("駭客攻擊", "間諜軟體", "spyware"):
        assert word in tokens, word


def test_ambiguous_business_words_are_not_collected():
    """**刻意不收單獨的「漏洞」與「breach」**:前者太泛(產能漏洞、
    法規漏洞),後者在商業新聞裡是違約。"""
    tokens = set(ne._cyber_tokens())
    assert "漏洞" not in tokens and "breach" not in tokens
    assert ne._event_type("台積電產能規劃出現漏洞") != "cybersecurity"
    assert ne._event_type("supplier breach of contract lawsuit") != "cybersecurity"


# ---------------------------------------------------------------- 下游影響

def test_the_surprise_score_is_no_longer_geopolitical_grade():
    """geopolitical 是 0.90 —— 公司資安事件借用它的意外度,於是直接變
    peak story。改成 0.70:仍在 prompt 的 ≥0.6 優先門檻內,但不是地緣等級。"""
    cyber = {"event_type": "cybersecurity", "title": "駭客攻擊造成產線停擺",
             "summary": ""}
    geo = {"event_type": "geopolitical", "title": "飛彈攻擊", "summary": ""}
    assert ne._event_surprise_score(cyber) == 0.70
    assert ne._event_surprise_score(geo) == 0.90


def test_a_cyber_event_is_still_tracked_across_days():
    """**只拆型別不補延燒追蹤,等於把它降級成單日新聞。**

    先前資安事件是「借用」geopolitical 才進 `_TIMELINE_EVENT_TYPES` 的;
    產線停擺、客戶通報、修復進度本來就會延燒好幾天。
    """
    assert "cybersecurity" in mr._TIMELINE_EVENT_TYPES


def test_the_extractor_may_emit_the_new_type():
    """LLM 抽取器吐出來的型別要通過驗證 —— 不然它只能寫 geopolitical,
    而那正是要修掉的東西。"""
    assert "cybersecurity" in ne._LLM_EVENT_TYPES


def test_a_cyber_event_does_not_go_to_the_world_section():
    """**世界大事那一段是「股市之外的世界」。** 公司資安事件有主體,
    要進那家公司的類股段,不是世界大事。"""
    ev = [{"entity": "AAPL", "title": "Apple 間諜軟體威脅通知",
           "event_type": "cybersecurity", "quality_score": 9}]
    got = {a["entity"]: a["section"] for a in mr.assign_event_sections(ev, [])}
    assert got["AAPL"] != mr._SECTION_WORLD, got
    # 反向:真的地緣事件仍然進世界大事
    geo = [{"entity": "", "title": "飛彈攻擊", "event_type": "geopolitical",
            "quality_score": 9}]
    assert mr.assign_event_sections(geo, [])[0]["section"] == mr._SECTION_WORLD


def test_the_catalyst_weight_is_unchanged_by_the_split():
    """**這個 commit 只修分類,不順手改計分模型。**

    拆出來之前資安事件走 geopolitical 的 -1.5;拆出來之後要維持同一級,
    否則「分類修好了」與「分數變了」會混在同一次改動裡,事後分不開。
    """
    import io
    from pathlib import Path
    src = io.open(Path(__file__).resolve().parent.parent / "morning_report.py",
                  encoding="utf-8").read()
    i = src.index('"litigation": -1.5, "geopolitical": -1.5')
    block = src[i:i + 400]
    assert '"cybersecurity": -1.5' in block, block[:300]


# ---------------------------------------------------------------- state 清理

def test_the_mistyped_production_rows_are_purged():
    """**只修 producer 不會把已經寫壞的 state 修好**(與 Commit A 同一條理由)。

    這兩筆的**歸因是對的**(標題確實指名那家公司),錯的是型別 —— 所以
    Commit A 的清理刻意沒有動它們。型別會一路影響意外度(0.90)、催化權重
    與延燒追蹤,留著就是每天用錯誤的優先序推一條線。
    """
    import state_migrations as sm
    ledger = [
        {"key": "e:aapl|l:geopolitical|202608", "entity": "AAPL",
         "event_type": "geopolitical", "headline": _PRODUCTION_MISLABELLED[0]},
        {"key": "e:avgo|l:geopolitical|svmware|2026", "entity": "AVGO",
         "event_type": "geopolitical", "headline": _PRODUCTION_MISLABELLED[1]},
        # 真的地緣事件不得被清
        {"key": "e:cluster1|l:geopolitical", "entity": "",
         "event_type": "geopolitical", "headline": "伊朗飛彈攻擊油輪"},
        # 型別本來就對的資安事件不得被清
        {"key": "e:2330|l:cybersecurity|202608", "entity": "2330",
         "event_type": "cybersecurity", "headline": "台積電遭勒索軟體攻擊"},
    ]
    keep, dropped = sm.purge_mistyped_cyber_stories(ledger)
    assert [r["key"] for r in dropped] == [
        "e:aapl|l:geopolitical|202608", "e:avgo|l:geopolitical|svmware|2026"], dropped
    assert len(keep) == 2, keep


def test_the_mistyped_cyber_purge_is_idempotent():
    """清過的不會再被清一次 —— 每天跑都安全。"""
    import state_migrations as sm
    ledger = [{"key": "e:aapl|l:geopolitical|202608", "event_type": "geopolitical",
               "headline": _PRODUCTION_MISLABELLED[0]}]
    once, _ = sm.purge_mistyped_cyber_stories(ledger)
    twice, dropped2 = sm.purge_mistyped_cyber_stories(once)
    assert twice == once and dropped2 == []


def test_the_timeline_keys_are_renamed_not_dropped():
    """**改名而不是丟掉。**

    時間軸的鍵是 `型別:動作:對象:月`,動作那一段已經明說是 cyberattack ——
    不必猜。丟掉會讓一條真的延燒好幾天的線從第 1 天重算(產線停擺、
    客戶通報、修復進度),那個代價沒有必要付。
    (線索帳本那邊的鍵**沒有**動作段,認不出來,所以那邊仍然是丟掉。)
    """
    import state_migrations as sm
    tl = {
        # 新版四段鍵:動作段就是 cyberattack
        "geopolitical:cyberattack:藥華藥:2026-08":
            {"latest_title": "藥華藥遭勒索軟體攻擊", "days": 5},
        # **生產現存的是舊版三段鍵**(沒有動作段)—— 靠標題認
        "geopolitical:AAPL:2026-08":
            {"latest_title": _PRODUCTION_MISLABELLED[0], "days": 1},
        # 真的地緣事件不得被改名
        "geopolitical:hormuz_passage:2026-08":
            {"latest_title": "Iran passes anti-infiltration law", "days": 4},
    }
    out, renamed = sm.migrate_cyber_timeline_keys(tl)
    assert sorted(renamed) == ["geopolitical:AAPL:2026-08",
                               "geopolitical:cyberattack:藥華藥:2026-08"], renamed
    assert out["cybersecurity:cyberattack:藥華藥:2026-08"]["days"] == 5,         "延燒天數在改名時掉了"
    assert out["cybersecurity:cyberattack:藥華藥:2026-08"]["event_type"] ==         "cybersecurity"
    assert "geopolitical:hormuz_passage:2026-08" in out


def test_the_timeline_rename_keeps_the_longer_running_line():
    """兩個世代撞在同一個新鍵上時**留天數多的那一筆** —— 兩筆都留會讓
    同一條線在排序裡出現兩次。"""
    import state_migrations as sm
    # **兩種順序都要測**:dict 的順序不是判準。只測一種的話,反例只要
    # 讓後到的那筆覆寫就過關了(突變驗證抓到)。
    old_row = {"latest_title": "X 遭勒索軟體攻擊", "days": 2}
    new_row = {"latest_title": "X 遭勒索軟體攻擊 後續", "days": 6}
    for tl in ({"geopolitical:cyberattack:X:2026-08": dict(old_row),
                "cybersecurity:cyberattack:X:2026-08": dict(new_row)},
               {"cybersecurity:cyberattack:X:2026-08": dict(new_row),
                "geopolitical:cyberattack:X:2026-08": dict(old_row)}):
        out, _ = sm.migrate_cyber_timeline_keys(tl)
        assert len(out) == 1, out
        assert out["cybersecurity:cyberattack:X:2026-08"]["days"] == 6, out


def test_the_timeline_rename_is_idempotent():
    """改過名的不會再被改一次。"""
    import state_migrations as sm
    tl = {"geopolitical:AAPL:2026-08":
          {"latest_title": _PRODUCTION_MISLABELLED[0], "days": 1}}
    once, _ = sm.migrate_cyber_timeline_keys(tl)
    twice, renamed2 = sm.migrate_cyber_timeline_keys(once)
    assert twice == once and renamed2 == []


def test_the_cyber_purge_shares_the_declared_vocabulary():
    """判準不在清理裡自己寫一份 —— 兩份會分歧,而分歧的症狀是
    「清掉的明天又長回來」。"""
    import ast
    import io
    from pathlib import Path
    src = io.open(Path(__file__).resolve().parent.parent / "state_migrations.py",
                  encoding="utf-8").read()
    names = {n.attr for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Attribute)}
    assert "_cyber_tokens" in names, "清理自己寫了一份資安詞彙表"


def test_the_cyber_purge_is_wired_into_the_run():
    """**沒有呼叫端的清理等於沒有清理。**"""
    import io
    from pathlib import Path
    src = io.open(Path(__file__).resolve().parent.parent / "morning_report.py",
                  encoding="utf-8").read()
    assert "_sm.purge_mistyped_cyber_stories(keep)" in src
    assert "_sm.migrate_cyber_timeline_keys(state)" in src


def test_an_upstream_geopolitical_label_is_normalised_too():
    """**上游給的型別也要過同一條規則。**

    `event_type` 可能來自 LLM 抽取器,而它照樣會把資安事件寫成 geopolitical。
    只修確定性推導的話,錯誤分類每天會從另一條路進來 —— 而 state 清理會
    與它每天打架:清掉、隔天又寫回來,延燒天數永遠是 1。
    """
    assert ne.normalize_event_type(
        "geopolitical", "藥華藥遭勒索軟體攻擊 產線停擺") == "cybersecurity"
    # 反向:真的地緣事件不動,別的型別也不動
    assert ne.normalize_event_type("geopolitical", "伊朗飛彈攻擊油輪") == "geopolitical"
    assert ne.normalize_event_type("earnings", "駭客攻擊") == "earnings"


def test_the_producer_normalises_an_upstream_label():
    """端到端:抽取器寫 geopolitical,事件仍要是 cybersecurity。"""
    evs = mr.extract_structured_events(
        [{"title": "藥華藥遭駭客網路攻擊 公司發聲明", "summary": "",
          "source": "鉅亨台股", "published": "2026-08-18T06:00:00+08:00",
          "company_label": "6446", "event_type": "geopolitical"}],
        [], known_names={"6446": ("藥華藥",)})
    assert evs[0]["event_type"] == "cybersecurity", evs[0]


def test_a_production_shaped_row_keeps_its_days_the_next_day(tmp_path, monkeypatch):
    """**端到端:生產現存的三段鍵,隔天要接得回同一條線。**

    只驗「改名後的 dict」不夠(外審 2026-08-18 第二輪):生產那幾筆的
    `identity_schema` 已經是最新版,所以 `adopt_legacy()` **不會**接手;
    只把型別那一段改掉會得到 `cybersecurity:AAPL:2026-08`,而隔天的事件
    算出來的是 `cybersecurity:cyberattack:AAPL:2026-08` —— 對不上,
    天數從 1 重算,改名就白做了。
    """
    import datetime as dt
    import json
    import event_identity as eid
    f = tmp_path / "tl.json"
    # **生產形狀**:三段鍵、schema 為最新版、已經燒了 3 天
    # **這一列是從生產 state 逐字抄下來的**(`geopolitical:AAPL:2026-08`),
    # 只把天數改成 3 —— 用我自己捏的空 `incident_tokens` 會測到一個生產
    # 不存在的形狀(自測抓到:那樣會開出 sibling,而真實資料不會)。
    f.write_text(json.dumps({
        "geopolitical:AAPL:2026-08": {
            "first_seen": "2026-08-15", "days": 3, "last_seen": "2026-08-17",
            "latest_title": _PRODUCTION_MISLABELLED[0], "latest_summary": "",
            "object": "",
            "incident_tokens": ["3c", "出間", "威脅", "子報", "恐成", "成攻",
                                "戶恐", "擊目", "攻擊", "用戶", "由電", "發出",
                                "目標", "科技", "脅通", "自由", "諜軟", "軟體",
                                "通知", "間諜", "電子", "體威"],
            "entity": "AAPL", "subjects": ["AAPL"],
            "event_type": "geopolitical", "action": "",
            "identity_schema": eid.IDENTITY_SCHEMA_VERSION}},
        ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(mr, "EVENT_TIMELINE_FILE", f)
    mr.update_event_timeline(
        [{"event_type": "cybersecurity", "entity": "AAPL",
          "title": "Apple 發出間諜軟體威脅通知 用戶恐成攻擊目標:已釋出修補程式"}],
        dt.datetime(2026, 8, 18, 7, 0, tzinfo=mr.TPE))
    st = json.loads(f.read_text(encoding="utf-8"))
    assert len(st) == 1, st
    key, row = next(iter(st.items()))
    assert key.startswith("cybersecurity:cyberattack:"), key
    assert row["days"] >= 4, f"延燒天數從頭算了:{row['days']}"


def test_the_extractor_prompt_lists_the_new_type():
    """**prompt 與 schema 不得分歧。**

    schema 收得下 `cybersecurity` 而 prompt 的允許清單沒說,模型就**選不到**
    它;退而選 `general` 時 `normalize_event_type` 又刻意不動(它只改
    geopolitical),於是錯誤分類原封不動地留著。
    """
    import io
    from pathlib import Path
    src = io.open(Path(__file__).resolve().parent.parent / "morning_report.py",
                  encoding="utf-8").read()
    i = src.index("Allowed event_type: ")
    block = src[i:i + 200]
    # 清單是**產生**的,不是手寫的 —— 手寫一份就會再分歧一次
    assert "_LLM_EVENT_TYPES" in block, block
    assert "geopolitical, general" not in block, block


def test_the_renamed_row_is_internally_consistent():
    """**鍵說 cyberattack、欄位卻是空的**是內部不一致 —— 身分層有幾處
    直接讀 `row["action"]`(例如挑出「有具名動作」的那些列),不補的話
    改名過的列在那些判斷裡看起來像沒有動作。"""
    import state_migrations as sm
    tl = {"geopolitical:AAPL:2026-08":
          {"latest_title": _PRODUCTION_MISLABELLED[0], "days": 3, "action": ""}}
    out, _ = sm.migrate_cyber_timeline_keys(tl)
    row = out["cybersecurity:cyberattack:AAPL:2026-08"]
    assert row["action"] == "cyberattack", row
    assert row["event_type"] == "cybersecurity", row


def test_a_multi_subject_row_uses_the_current_object_signature():
    """**對象要用身分層的那個函式算,不要抄舊鍵裡的字**(外審第三輪)。

    舊的三段鍵把主體截到 20 字,而現行的 `object_signature` 截到 24 字 ——
    多主體的列兩邊會差一段,改名之後照樣對不上、天數從 1 重算。
    單一主體(AAPL)兩種算法一樣長,所以那個 fixture 藏得住這個差別。
    """
    import event_identity as eid
    import state_migrations as sm
    subs = ["台積電", "聯發科", "鴻海", "日月光", "緯創", "廣達", "聯電", "華碩"]
    old_key = "geopolitical:" + "、".join(subs)[:20] + ":2026-08"
    row = {"first_seen": "2026-08-15", "days": 3, "last_seen": "2026-08-17",
           "latest_title": "多家台廠遭勒索軟體攻擊 產線受影響",
           "latest_summary": "", "object": "", "incident_tokens": [],
           "entity": subs[0], "subjects": subs,
           "event_type": "geopolitical", "action": "",
           "identity_schema": eid.IDENTITY_SCHEMA_VERSION}
    out, renamed = sm.migrate_cyber_timeline_keys({old_key: dict(row)})
    assert renamed == [old_key], renamed
    new_key = next(iter(out))
    # **與身分層算出來的對象逐字相同** —— 抄舊鍵的字會少一截
    assert new_key == (
        "cybersecurity:cyberattack:"
        + eid.object_signature("cyberattack", subs) + ":2026-08"), new_key

    # **這裡刻意不做端到端。** 隔天那一則的主體是從**標題**抽出來的,
    # 一則標題不會同時點名八家公司 —— 硬編一個那樣的 fixture 是在測一個
    # 生產不會出現的形狀。這條要守的是「改名算出來的對象**與身分層同一個
    # 函式**」;跨日接得回去由上面 AAPL 那條(逐字抄自生產)負責。
