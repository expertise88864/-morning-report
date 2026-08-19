# -*- coding: utf-8 -*-
"""**「這則在講誰」與「這則跟誰有關」是兩件事**(repo-wide 外審 2026-08-18,P1-1)。

`entity` 先前同時是三個概念:模型宣告的主體、編輯標註的相關個股、
**發起查詢的那個代號**。而 `entity` 是 story key / event timeline /
催化評分 / model history 的身分 —— 於是確定性層(不是模型幻覺)把這些
寫進了生產 state:

    e:2454|l:geopolitical|202608   聯發科  ← 「黃金終於鬆開手煞車!8月大漲9%」
    e:2890|l:earnings|2026q2       永豐金  ← 「【公告】勝悅-KY 第2季合併財報…」
    e:3231|l:earnings|2026q3       緯創    ← 「緯穎飆出6740元歷史新天價」
    geopolitical:2454:2026-08              ← 同一則黃金新聞

**這四筆是從當天的 `state/story_ledger.json` 與 `state/event_timeline.json`
抄下來的真實資料**,不是我編的 fixture —— 外審的原話是「這四條比任何
synthetic fixture 都有價值」。

判準只有一份(`news_events.mentions_entity`):主體要在新聞自己的文字裡
被指名 —— 括號裡的代號,或宣告過的別名。指不出來就沒有主體。
"""
import morning_report as mr
import news_events as ne
import state_migrations as sm

#: 這四筆的別名表(生產用的是 `_entity_alias_map`,這裡取需要的那幾檔)。
_NAMES = {"2454": ("聯發科", "MediaTek"), "2890": ("永豐金",),
          "3231": ("緯創",), "6669": ("緯穎",), "2330": ("台積電", "TSMC"),
          "AAPL": ("蘋果", "Apple")}

#: **從生產 state 抄下來的四筆**(2026-08-18)。
_PRODUCTION_MISATTRIBUTIONS = (
    ("2454", "黃金終於鬆開手煞車！8月大漲9%重拾避險光環"),
    ("2890", "【公告】勝悅-KY115年第2季合併財務報告董事會預計召開日期"),
    ("3231", "擺脫財報亂流　多方猛攻！緯穎飆出6740元歷史新天價 - 鏡週刊Mirror Media"),
    ("2454", "美債賣壓止不住！30年期殖利率飆上19年新高"),
)


# ---------------------------------------------------------------- 判準本身

def test_the_four_production_misattributions_are_rejected():
    """**這四筆都不得有公司主體。** 它們是編輯標註的相關個股被升格成主體。"""
    for code, title in _PRODUCTION_MISATTRIBUTIONS:
        assert ne.mentions_entity(title, code, _NAMES) == "", (code, title)
        assert ne.resolve_subject(title, [code], _NAMES) == ("", ""), (code, title)


def test_a_news_that_really_names_the_company_keeps_its_subject():
    """**反向:真的講那家公司的新聞不得被清掉。**

    只擋不放的判準會讓整個確定性層失去公司歸因 —— 那比錯誤歸因更難察覺
    (信裡什麼都不說,而不是說錯)。
    """
    assert ne.resolve_subject("台積電熊本廠恢復地震前產出水準", ["2330"],
                              _NAMES) == ("2330", "alias")
    assert ne.resolve_subject("緯創(3231)接獲 AI 伺服器新單", ["3231"],
                              _NAMES) == ("3231", "code")
    # 同一則新聞真的講兩家時,兩家都指得出來
    both = "緯創與緯穎同步上修 AI 伺服器出貨"
    assert ne.mentions_entity(both, "3231", _NAMES) == "alias"
    assert ne.mentions_entity(both, "6669", _NAMES) == "alias"


def test_a_bare_number_is_not_a_stock_code():
    """「大盤大漲 2454 點」的 2454 是點數 —— **裸數字不算指名**。

    第一版用「左右不是英數字」當邊界,這一句照樣過關(自測抓到)。
    台股新聞寫代號的慣例是括號,所以判準要求括號相鄰。
    """
    assert ne.mentions_entity("大盤大漲 2454 點改寫紀錄", "2454", _NAMES) == ""
    assert ne.mentions_entity("成交 6669 張", "6669", _NAMES) == ""
    for form in ("聯發科(2454)法說", "【2454】法說會", "聯發科（2454）法說"):
        assert ne.mentions_entity(form, "2454", {}) == "code", form


def test_a_one_character_alias_cannot_match_everything():
    """一個字的別名會命中任何句子 —— 宣告表裡本來就不該有,這裡再擋一次。"""
    # **反例要真的含那個一字別名**,否則測不到長度那一關(突變驗證抓到:
    # 第一版用「今天大盤上漲」,而那句本來就沒有「金」)。
    assert ne.mentions_entity("黃金大漲", "9999", {"9999": ("金",)}) == ""
    assert ne.mentions_entity("黃金大漲", "9999", {"9999": ("黃金",)}) == "alias"


# ---------------------------------------------------------------- 生產者

def _events(news, known_names=None):
    return mr.extract_structured_events(news, [], known_names=known_names or _NAMES)


def _news(title, **over):
    n = {"title": title, "summary": "", "source": "鉅亨台股",
         "published": "2026-08-18T06:00:00+08:00"}
    n.update(over)
    return n


def test_an_editorial_tag_does_not_become_the_event_subject():
    """**編輯標註只是「相關」。** 鉅亨的 `stock` 欄位是人工標的關聯個股;
    先前每一個被標註的代號都會被升格成一個事件的 `entity`。"""
    evs = _events([_news("黃金終於鬆開手煞車！8月大漲9%重拾避險光環",
                         company_label="2454", cnyes_stocks=["2454"])])
    assert evs, "事件本身不該消失 —— 消失的是錯誤的主體"
    assert all(e["entity"] == "" for e in evs), [e["entity"] for e in evs]
    # 關聯個股仍然留著,只是換了欄位
    assert evs[0]["related_tickers"] == ["2454"], evs[0]
    assert evs[0]["query_origin"] == "2454", evs[0]


def test_a_multi_subject_news_still_produces_both_events():
    """**真的講兩家的新聞仍要有兩個事件** —— 這條規則不是把多主體歸因關掉。"""
    evs = _events([_news("台積電與日月光同步示警:記憶體漲價侵蝕毛利",
                         company_label="2330", cnyes_stocks=["2330", "3711"]),
                   ],
                  known_names=dict(_NAMES, **{"3711": ("日月光",)}))
    subjects = {e["entity"] for e in evs}
    assert "2330" in subjects, subjects
    assert "3711" in subjects, "標題指名的第二個主體被關掉了"


def test_a_tagged_code_that_the_text_never_names_gets_no_event():
    """標註了、但文字沒提到 —— 不得另開一個事件。"""
    one = _news("大盤量能萎縮 觀望氣氛濃", company_label="2330",
                cnyes_stocks=["2330", "2454"])
    evs = _events([one])
    assert all(e["entity"] == "" for e in evs), [e["entity"] for e in evs]
    # **筆數也要量**:標註展開若照舊執行,會多出一個(同樣沒有主體的)重複
    # 事件 —— 只看 entity 的話兩者長得一樣(突變驗證抓到)。
    assert len(evs) == 1, [e["title"] for e in evs]


def test_the_subject_basis_is_recorded():
    """**憑什麼說它是主體**要留得下來 —— 出錯時「用代號認的」與
    「用別名認的」要分得開。"""
    evs = _events([_news("台積電熊本廠恢復生產", company_label="2330")])
    assert evs[0]["subject_basis"] == "alias", evs[0]
    evs = _events([_news("緯創(3231)接獲新單", company_label="3231")])
    assert evs[0]["subject_basis"] == "code", evs[0]


# ---------------------------------------------------------------- state 清理

def test_the_migration_drops_exactly_the_polluted_rows():
    """**只修 producer 不會把已經寫壞的 state 修好。**

    那幾筆會跨日回流:昨日敘事、線索追蹤、延燒天數、催化評分都讀它們。
    """
    ledger = [{"key": f"e:{c}|l:general|x{i}", "entity": c, "headline": t}
              for i, (c, t) in enumerate(_PRODUCTION_MISATTRIBUTIONS)]
    ledger.append({"key": "e:2330|l:orders|1", "entity": "2330",
                   "headline": "台積電熊本廠恢復生產"})
    ledger.append({"key": "e:cluster9|l:general", "entity": "",
                   "headline": "黃金大漲"})
    keep, dropped = sm.purge_misattributed_stories(ledger, _NAMES)
    assert len(dropped) == len(_PRODUCTION_MISATTRIBUTIONS), dropped
    kept_keys = {r["key"] for r in keep}
    assert "e:2330|l:orders|1" in kept_keys, "真的講台積電的線索被清掉了"
    assert "e:cluster9|l:general" in kept_keys, "沒有公司主體的市場級線索被清掉了"


def test_the_migration_keeps_entities_the_vocabulary_does_not_cover():
    """**詞彙表沒收錄 ≠ 歸因錯了。** 查不到別名的實體一律留著 ——
    清錯比留錯糟得多(歷史不可逆)。"""
    row = {"key": "e:9999|l:general", "entity": "9999", "headline": "某公司財報"}
    keep, dropped = sm.purge_misattributed_stories([row], _NAMES)
    assert not dropped and keep == [row]


def test_the_migration_is_idempotent():
    """清過的不會再被清一次 —— 每天跑都安全,不需要「已經跑過」的旗標
    (那種旗標本身會壞掉)。"""
    ledger = [{"key": f"e:{c}|l:general|x{i}", "entity": c, "headline": t}
              for i, (c, t) in enumerate(_PRODUCTION_MISATTRIBUTIONS)]
    once, _ = sm.purge_misattributed_stories(ledger, _NAMES)
    twice, dropped2 = sm.purge_misattributed_stories(once, _NAMES)
    assert twice == once and dropped2 == []


def test_the_timeline_migration_drops_the_gold_line():
    """`geopolitical:2454:2026-08` 的 latest_title 是「黃金 8 月大漲 9%」——
    那條線會繼續累計延燒天數。"""
    tl = {"geopolitical:2454:2026-08":
          {"latest_title": "黃金終於鬆開手煞車！8月大漲9%重拾避險光環", "days": 1},
          "geopolitical:伊朗:2026-08": {"latest_title": "Iran passes law", "days": 4},
          "orders:2330:2026-08": {"latest_title": "台積電獲追加訂單", "days": 2}}
    keep, dropped = sm.purge_misattributed_timeline(tl, _NAMES)
    assert dropped == ["geopolitical:2454:2026-08"], dropped
    assert "orders:2330:2026-08" in keep and "geopolitical:伊朗:2026-08" in keep


def test_the_producer_and_the_migration_share_one_rule():
    """**兩份判準會分歧**,而分歧的症狀是「清掉的明天又長回來」。"""
    import ast
    import io
    from pathlib import Path
    src = io.open(Path(__file__).resolve().parent.parent / "state_migrations.py",
                  encoding="utf-8").read()
    names = {n.attr for n in ast.walk(ast.parse(src))
             if isinstance(n, ast.Attribute)}
    assert "mentions_entity" in names, "清理自己寫了一份判準"


def test_the_migration_is_wired_into_the_run():
    """**沒有呼叫端的清理等於沒有清理。** 帳本與時間軸兩條載入路徑都要接上。"""
    import io
    from pathlib import Path
    src = io.open(Path(__file__).resolve().parent.parent / "morning_report.py",
                  encoding="utf-8").read()
    assert "purge_story_misattribution(load_story_ledger())" in src
    assert "_sm.purge_misattributed_timeline(state, _kn_tl)" in src


def test_the_catalyst_scorer_gets_events_built_with_the_vocabulary():
    """**催化評分不得自己重算一份沒有詞彙表的事件。**

    `_stock_news_catalysts` 在沒收到 `events` 時會自己呼叫抽取器,而那一條
    路徑拿不到別名表 → 每個候選都會是 `unverified`(照舊採用)。生產必須
    把**已經用詞彙表算過的**事件傳進去,否則歸因驗證在計分那一側等於沒開。
    """
    import ast
    import io
    from pathlib import Path
    src = io.open(Path(__file__).resolve().parent.parent / "morning_report.py",
                  encoding="utf-8").read()
    calls = [n for n in ast.walk(ast.parse(src))
             if isinstance(n, ast.Call)
             and getattr(n.func, "id", "") == "_stock_news_catalysts"]
    assert calls, "掃不到呼叫端 —— 掃描器壞了,本測試無效"
    for c in calls:
        assert any(k.arg == "events" for k in c.keywords), \
            "生產沒有把算好的事件傳進催化評分"


def test_an_unknown_subject_needs_a_literal_mention():
    """**主體信任層級**(repo-wide 外審 2026-08-19 P2,取代舊的
    「詞彙表沒收錄照舊採用 unverified」契約):詞彙表外的候選要在文字裡
    **逐字出現**才採用(依據 `literal`);逐字都指不出來的名字不進
    story key / timeline / 催化評分 —— 持久化一個證明不了的 entity key,
    比「這一則沒有主體」更難察覺也更難清。
    """
    # 逐字出現 → 採用(Pentagon 這類合法語意主體不受詞彙表限制)
    evs = _events([_news("Pentagon confirms new arms package",
                         company_label="Pentagon")])
    assert evs[0]["entity"] == "Pentagon", evs[0]
    assert evs[0]["subject_basis"] == "literal", evs[0]
    # 逐字指不出來(裸代號不在標題裡)→ 誠實降級:沒有主體
    evs = _events([_news("某新股掛牌首日大漲", company_label="9999")])
    assert evs[0]["entity"] == "", evs[0]


# ------------------------------------------------- 外審 2026-08-18 的三個 P1

def test_a_subject_named_only_in_the_summary_still_counts():
    """**摘要也是新聞自己的文字**(外審 2026-08-18 第三輪)。

    標題寫「第二季獲利優於預期」、摘要寫「台積電公布財報」是常見寫法。
    中間有一版把判準收窄成只看標題(為了讓清理端能重現同一個依據),
    外審指出那是**用縮窄定義解決 round-trip**,合法主體會整批消失。
    """
    evs = _events([_news("第二季獲利優於預期", company_label="2330",
                         summary="台積電公布財報,毛利率優於預期")])
    assert evs[0]["entity"] == "2330", evs[0]
    assert evs[0]["subject_basis"] == "alias", evs[0]


def test_a_row_that_carries_its_basis_is_never_purged():
    """round-trip 由**依據隨列走**解決:生產者驗證用的是標題+摘要,而帳本
    只存標題 —— 拿標題去判有依據的列,會把昨天建立的合法 state 刪掉。

    清理的工作是**修正之前寫下的舊列**(那些列沒有 `subject_basis`)。
    """
    evs = _events([_news("第二季獲利優於預期", company_label="2330",
                         summary="台積電公布財報")])
    row = {"key": "e:2330|l:earnings|x", "entity": evs[0]["entity"],
           "headline": evs[0]["title"], "subject_basis": evs[0]["subject_basis"]}
    keep, dropped = sm.purge_misattributed_stories([row], _NAMES)
    assert not dropped and keep == [row], (keep, dropped)
    # 反向:同一列**沒有依據**(修正之前寫的)就要被清掉
    legacy = {k: v for k, v in row.items() if k != "subject_basis"}
    keep2, dropped2 = sm.purge_misattributed_stories([legacy], _NAMES)
    assert dropped2 == [legacy] and keep2 == [], (keep2, dropped2)


def test_the_ledger_row_carries_the_basis():
    """**沒有寫進帳本的欄位等於不存在** —— 上面那條的前提要成立。"""
    import story_ledger as sl
    ev = {"entity": "2330", "event_type": "earnings", "title": "第二季獲利優於預期",
          "subject_basis": "alias", "published": "2026-08-18T06:00:00+08:00",
          "surprise_score": 0.6, "source": "鉅亨台股", "source_grade": "A"}
    rows = sl.update_ledger([], [ev], "2026-08-18")
    assert rows, "帳本沒有收下這個事件 —— 本測試的前提不成立"
    assert rows[0].get("subject_basis") == "alias", rows[0]


def test_the_timeline_subject_comes_from_the_row_not_the_key():
    """**鍵的格式已經換過**(外審 P1-2):新版是 `型別:動作:對象:月`,
    第二段是動作不是代號 —— 照舊解析鍵會把 `arms_sale` 當主體,而詞彙表
    查不到它,污染列就因為 fail-open 永遠留著。"""
    tl = {"geopolitical:arms_sale:台灣:2026-08":
          {"latest_title": "黃金終於鬆開手煞車！8月大漲9%", "entity": "2454"},
          "geopolitical:hormuz_passage:2026-08":
          {"latest_title": "Iran passes law", "subjects": ["伊朗"]},
          "orders:2330:2026-08":
          {"latest_title": "台積電獲追加訂單", "entity": "2330"}}
    keep, dropped = sm.purge_misattributed_timeline(tl, _NAMES)
    assert dropped == ["geopolitical:arms_sale:台灣:2026-08"], dropped
    assert "orders:2330:2026-08" in keep
    assert "geopolitical:hormuz_passage:2026-08" in keep


def test_a_latin_alias_needs_a_word_boundary():
    """**別名是一個整體,不是幾個可以各自比對的字**(外審 P1-3)。

    第一版對每個別名再 `.split()` 再做無邊界子字串比對:
    `Hon Hai` → `Hon` → 「iPhone demand…」命中鴻海;`Arm` 命中
    `pharmaceutical`;`Applied Materials` → `materials` 命中任何講材料的新聞。
    那正好把這次要關掉的路徑重新打開。
    """
    kn = {"2317": ("鴻海", "Foxconn", "Hon Hai"), "ARM": ("安謀", "Arm"),
          "AMAT": ("應用材料", "Applied Materials")}
    for title, code in (("iPhone demand stays strong", "2317"),
                        ("pharmaceutical stocks rally", "ARM"),
                        ("raw materials cost up", "AMAT")):
        assert ne.mentions_entity(title, code, kn) == "", (title, code)
    # 反向:完整別名落在詞邊界上仍要命中
    for title, code in (("Hon Hai lifts AI server outlook", "2317"),
                        ("Arm raises royalty guidance", "ARM"),
                        ("Applied Materials Q4 beat", "AMAT"),
                        ("鴻海上修 AI 伺服器展望", "2317")):
        assert ne.mentions_entity(title, code, kn) == "alias", (title, code)


def test_an_english_alias_next_to_chinese_still_matches():
    """**邊界只看 ASCII 英數字**(外審 2026-08-18 第三輪)。

    中文字的 `isalnum()` 也是 True,於是「Arm架構需求升溫」會被當成別名
    落在單字內而拒絕 —— 要擋的是 `pharmaceutical`,不是中文緊鄰。
    """
    kn = {"ARM": ("安謀", "Arm"), "AAPL": ("蘋果", "Apple"),
          "NVDA": ("輝達", "NVIDIA")}
    for title, code in (("Arm架構需求升溫", "ARM"), ("Apple發表新晶片", "AAPL"),
                        ("NVIDIA執行長訪台", "NVDA"), ("Arm、Apple同步走強", "ARM")):
        assert ne.mentions_entity(title, code, kn) == "alias", (title, code)
    assert ne.mentions_entity("pharmaceutical stocks rally", "ARM", kn) == ""


def test_an_updated_legacy_story_picks_up_the_basis():
    """**換了 headline 就要同步依據**(外審 2026-08-18 第四輪)。

    舊列沒有 `subject_basis`;被一則只在摘要指名公司的合法續報更新之後,
    標題仍是泛稱 —— 不同步的話,清理明天照樣把它當舊污染列刪掉。
    """
    import story_ledger as sl
    base = {"entity": "2330", "event_type": "earnings",
            "title": "台積電第二季獲利優於預期",
            "subject_basis": "alias", "published": "2026-08-17T06:00:00+08:00",
            "surprise_score": 0.6, "source": "鉅亨台股", "source_grade": "B"}
    rows = sl.update_ledger([], [base], "2026-08-17")
    assert rows, "前提不成立:帳本沒有收下這個事件"
    legacy = [{k: v for k, v in r.items() if k != "subject_basis"} for r in rows]
    # 出口 B:同一條線索的隔日續報(**同一個 key**,所以走的是更新而不是新建)。
    # 第一版用「第三季」當續報 —— 那會開一條新線索,根本沒走到更新路徑,
    # 於是那條測試量到的是新建那一格(突變驗證抓到)。
    follow = dict(base, title="台積電第二季毛利率同步走高至 59%",
                  published="2026-08-18T06:00:00+08:00")
    after = sl.update_ledger(legacy, [follow], "2026-08-18")
    assert [r["key"] for r in after] == [rows[0]["key"]], after
    assert any(r.get("subject_basis") == "alias" for r in after), after
    # 出口 A:**同一輪裡被更權威的一則覆寫**。兩個出口都要同步 ——
    # 只補一個的話,反例改另一個時測試照樣綠。
    legacy2 = [{k: v for k, v in r.items() if k != "subject_basis"} for r in after]
    same_run = [dict(base, title="台積電第二季展望上修(初稿)", source_grade="C",
                     published="2026-08-19T06:00:00+08:00", subject_basis=""),
                dict(base, title="台積電第二季展望上修(公告)", source_grade="A",
                     official=True, published="2026-08-19T07:00:00+08:00")]
    after2 = sl.update_ledger(legacy2, same_run, "2026-08-19")
    assert any(r.get("subject_basis") == "alias" for r in after2), after2


def test_a_timeline_row_that_carries_its_basis_is_never_purged():
    """時間軸與帳本同一條規則:帶著依據的列不判。

    生產者用標題+摘要驗證,而清理只看得到標題 —— 沒有依據的話,
    只在摘要指名公司的合法時間軸明天會被清掉。
    """
    tl = {"earnings:2330:2026-08": {"latest_title": "第二季獲利優於預期",
                                    "entity": "2330", "subject_basis": "alias"}}
    keep, dropped = sm.purge_misattributed_timeline(tl, _NAMES)
    assert not dropped and keep == tl, (keep, dropped)
    # 反向:同一列沒有依據就要被清
    legacy = {"earnings:2330:2026-08":
              {k: v for k, v in tl["earnings:2330:2026-08"].items()
               if k != "subject_basis"}}
    keep2, dropped2 = sm.purge_misattributed_timeline(legacy, _NAMES)
    assert dropped2 == ["earnings:2330:2026-08"], (keep2, dropped2)


def test_the_timeline_writer_records_the_basis():
    """**沒有寫進 state 的欄位等於不存在** —— 上面那條的前提要成立。"""
    import ast
    import io
    from pathlib import Path
    src = io.open(Path(__file__).resolve().parent.parent / "morning_report.py",
                  encoding="utf-8").read()
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "update_event_timeline")
    assert "subject_basis" in ast.dump(fn), "時間軸沒有把依據寫進去"
