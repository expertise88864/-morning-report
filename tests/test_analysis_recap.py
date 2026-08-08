# -*- coding: utf-8 -*-
"""**昨日觀點閉環**(分析面縱深)。

prompt 第 150 行起早就要求「延續事件寫增量」—— 但模型沒有 diff 的對象,
那是一句無法執行的要求;也沒有任何檢查量得到「今天只是把昨天再說一次」。
閉環:分析成功 → 存 key_drivers 觀點 → 明天掛在同一件事的事件群上
(`yesterday_view`)→ 重述度過高退回加深。
"""
import analysis_depth as ad
import analysis_recap as rc
import evidence_packet as ep
import fixtures_analysis as fx


def _packet(news=None, recap=None, date="2026-08-08"):
    q = {"QQQ": {"change_pct": 1.0}}
    if recap is not None:
        q["ANALYSIS_RECAP"] = recap
    return ep.build(q, {}, {}, news if news is not None else fx.news(),
                    [], {}, as_of="x", target_session_date=date,
                    sanitize=lambda s: s)


def _saved(statement="台積電先進封裝產能瓶頸外溢,對封測廠是結構性利多"):
    """昨天存下來的形狀 —— 由 `extract` 從真的分析物件產生,
    不是測試自己發明的整齊形狀。fixture 裡 n2 的實體才是台積電
    (n1 是費半 —— 選錯群的話,別名比對測的是空集合)。"""
    obj = fx.valid_analysis()
    obj["key_drivers"] = [dict(obj["key_drivers"][0],
                               statement=statement, cluster_id="cluster:n2")]
    pk = _packet(date="2026-08-07")
    rec = rc.extract(obj, pk)
    assert rec["items"][0]["entities"] == ["台積電"], rec
    return rec


# ------------------------------------------------------------ 抽取與存取

def test_extract_keys_the_view_by_entities_not_cluster_id():
    """cluster_id 是「群裡最小的 source_item_id」,明天必然換號 ——
    觀點要以實體為身分,否則存了也接不回來。"""
    rec = _saved()
    assert rec["date"] == "2026-08-07"
    assert rec["items"] and rec["items"][0]["entities"], rec
    assert rec["items"][0]["direction"]


def test_save_and_load_round_trip(tmp_path):
    obj = fx.valid_analysis()
    obj["key_drivers"] = [dict(obj["key_drivers"][0], cluster_id="cluster:n1")]
    f = tmp_path / "analysis_recap.json"
    assert rc.save(f, obj, _packet(date="2026-08-07")) is True
    assert rc.load(f)["date"] == "2026-08-07"


def test_save_to_an_unwritable_path_degrades_without_raising(tmp_path):
    """加深不可斷晨報:存不進去回 False,不拋。"""
    assert rc.save(tmp_path, fx.valid_analysis(), _packet()) is False


def test_statement_is_stored_as_judgment_not_essay():
    rec = _saved(statement="長" * 500)
    assert len(rec["items"][0]["statement"]) == rc.STATEMENT_CHARS


# ------------------------------------------------------------ 同日重跑守衛

def test_same_day_rerun_must_not_diff_against_itself():
    """手動 dispatch 會把「今天早上」存進 state —— 拿它比就是今天比今天,
    會產生假的強化/推翻(legacy 的 `_format_narrative_delta` 踩過)。"""
    rec = _saved()
    assert rc.usable(rec, "2026-08-08"), "昨天的觀點要可用"
    assert rc.usable(rec, "2026-08-07") == [], "同日不得自比"
    assert rc.usable(rec, "2026-08-06") == [], "未來的觀點是資料錯亂"


# ------------------------------------------------------------ 掛上事件群

def test_yesterday_view_attaches_across_alias():
    """昨天寫「台積電」,今天的群寫「TSMC」—— 同一件事要接得上
    (與 continuing_days 同一套身分哲學)。"""
    pk = _packet(
        news=[{"source_item_id": "n1", "title": "TSMC 法說會下週登場 產能受矚",
               "entities": ["TSMC"], "source": "CNBC", "source_name": "CNBC"}],
        recap=_saved())
    v = pk["news_clusters"]["clusters"][0]["yesterday_view"]
    assert "2026-08-07本報" in v and "封測廠" in v, v
    assert "偏多" in v


def test_unrelated_cluster_gets_no_view():
    pk = _packet(
        news=[{"source_item_id": "n1", "title": "長榮7月營收年增43%",
               "entities": ["長榮"], "source": "X", "source_name": "X"}],
        recap=_saved())
    assert pk["news_clusters"]["clusters"][0]["yesterday_view"] == ""


def test_no_recap_state_degrades_to_empty_view():
    pk = _packet(recap=None)
    assert all(c["yesterday_view"] == ""
               for c in pk["news_clusters"]["clusters"])


def test_the_view_goes_through_the_sanitizer():
    """recap 的敘述是回流的模型輸出(內含外部素材的轉述)——
    與其他字串葉節點一樣要過整樹消毒。"""
    q = {"ANALYSIS_RECAP": _saved()}
    pk = ep.build(q, {}, {},
                  [{"source_item_id": "n1", "title": "台積電法說會下週登場",
                    "entities": ["TSMC"], "source": "X", "source_name": "X"}],
                  [], {}, as_of="x", target_session_date="2026-08-08",
                  sanitize=lambda s: s.replace("封測", "封測▲"))
    assert "封測▲" in pk["news_clusters"]["clusters"][0]["yesterday_view"]


def test_yesterday_view_is_not_citable_evidence():
    """**拿自己昨天的判斷當今天的證據是循環引用。** ANALYSIS_RECAP
    不進 registry —— 模型引用 `market:ANALYSIS_RECAP.*` 必須被判不存在。"""
    pk = _packet(recap=_saved())
    assert not [i for i in ep.evidence_ids(pk)
                if "ANALYSIS_RECAP" in str(i)]


# ------------------------------------------------------------ 重述檢查

def _analysis_with(statement):
    obj = fx.valid_analysis()
    obj["key_drivers"] = [dict(obj["key_drivers"][0],
                               statement=statement, cluster_id="cluster:n1")]
    return obj


def _pk_with_view():
    return _packet(
        news=[{"source_item_id": "n1", "title": "台積電法說會下週登場 供應鏈關注",
               "entities": ["台積電"], "source": "CNBC", "source_name": "CNBC"}],
        recap=_saved())


def test_restating_yesterday_is_flagged_for_deepening():
    """把昨天的判斷換兩個字再說一次 —— 要被退回加深。"""
    hits = [a for a in ad.depth_advisories(
        _analysis_with("台積電先進封裝產能瓶頸外溢,對封測廠是結構性利多"),
        _pk_with_view()) if "昨日觀點" in a]
    assert hits, "整句重述沒有被看見"


def test_a_real_delta_is_not_flagged():
    """真的寫了增量(新的量、新的方向理由)不得誤傷 —— 誤傷會訓練出
    「看到 advisory 就改寫措辭」的模型行為,而不是更深的分析。"""
    hits = [a for a in ad.depth_advisories(
        _analysis_with("日月光今日證實追加設備採購,瓶頸外溢從傳聞升級為事實"),
        _pk_with_view()) if "昨日觀點" in a]
    assert hits == [], hits


def test_no_view_means_no_restatement_check():
    """第一天出現的事件沒有 diff 基準 —— 不得拿空字串亂比。"""
    hits = [a for a in ad.depth_advisories(
        _analysis_with("台積電先進封裝產能瓶頸外溢,對封測廠是結構性利多"),
        _packet()) if "昨日觀點" in a]
    assert hits == []


# ------------------------------------------------------------ 次要事件

def _obj_with_secondary(why="半導體設備出口管制擴大,對成熟製程廠是新增的限制"):
    """key_drivers 指 n2(台積電),top_news_analysis 指 n1(費半)——
    兩個**不同**的事件群,次要那條先前完全不存。"""
    obj = fx.valid_analysis()
    obj["key_drivers"] = [dict(obj["key_drivers"][0], cluster_id="cluster:n2")]
    obj["top_news_analysis"] = [dict(obj["top_news_analysis"][0],
                                     source_item_id="n1",
                                     why_it_matters=why)]
    return obj


def test_secondary_events_are_saved_too():
    """延燒到第三天的事件常已不在首屏 —— 只存首屏的話,最需要 diff
    基準的長尾事件恰恰沒有基準。"""
    rec = rc.extract(_obj_with_secondary(), _packet(date="2026-08-07"))
    assert len(rec["items"]) == 2, rec
    ents = [it["entities"] for it in rec["items"]]
    assert ["台積電"] in ents and ["費半"] in ents, ents


def test_one_view_per_cluster_and_the_primary_wins():
    """首屏與次要講同一件事時只存首屏那句 —— 它才是本報的正式判斷,
    存兩句明天就有兩個互相競爭的「昨日觀點」。"""
    obj = fx.valid_analysis()
    obj["key_drivers"] = [dict(obj["key_drivers"][0], cluster_id="cluster:n2",
                               statement="首屏的判斷")]
    obj["top_news_analysis"] = [dict(obj["top_news_analysis"][0],
                                     source_item_id="n2",
                                     why_it_matters="次要段對同一件事的判斷")]
    rec = rc.extract(obj, _packet(date="2026-08-07"))
    tsmc = [it for it in rec["items"] if it["entities"] == ["台積電"]]
    assert len(tsmc) == 1 and tsmc[0]["statement"] == "首屏的判斷", rec


def test_an_unmatchable_view_is_not_stored():
    """接不回來的觀點是死重量:source_item_id 不在任何群、也不在
    news 裡 → 沒有實體 → 不存。"""
    obj = fx.valid_analysis()
    obj["key_drivers"] = []
    obj["top_news_analysis"] = [dict(obj["top_news_analysis"][0],
                                     source_item_id="n_不存在")]
    assert rc.extract(obj, _packet(date="2026-08-07"))["items"] == []


def test_the_item_cap_keeps_the_primaries():
    """超過上限截**尾**不截頭 —— 主要觀點(items 前段)要留下來。"""
    obj = fx.valid_analysis()
    obj["key_drivers"] = [dict(obj["key_drivers"][0], cluster_id="cluster:n2",
                               statement="主要")]
    # 發布者各自不同 —— 同發布者+相似標題會被 packet 的近似去重收掉
    # (v19 的正確行為),那樣測到的是去重不是上限。
    news = [{"source_item_id": f"s{i:02d}", "title": f"事件{i}",
             "entities": [f"公司{i}"], "source": f"媒體{i}",
             "source_name": f"媒體{i}"}
            for i in range(rc.MAX_ITEMS + 5)]
    news.append({"source_item_id": "n2", "title": "台積電新聞",
                 "entities": ["台積電"], "source": "Y", "source_name": "Y"})
    pk = _packet(news=news, date="2026-08-07")
    obj["top_news_analysis"] = [
        dict(fx.valid_analysis()["top_news_analysis"][0],
             source_item_id=f"s{i:02d}", why_it_matters=f"判斷{i}")
        for i in range(rc.MAX_ITEMS + 5)]
    rec = rc.extract(obj, pk)
    assert len(rec["items"]) == rc.MAX_ITEMS
    assert rec["items"][0]["statement"] == "主要"


def test_restating_a_secondary_event_is_flagged():
    """**這一批存在的理由**:重述最常發生在次要段 —— 首屏有三條的
    位置壓力,次要段沒有,延燒事件掉出首屏後就開始逐日重述。"""
    saved = rc.extract(_obj_with_secondary(), _packet(date="2026-08-07"))
    pk = _packet(recap=saved)   # 明天:同一批新聞(標題相同 → 事件層對得上)
    obj = fx.valid_analysis()
    obj["top_news_analysis"] = [dict(
        obj["top_news_analysis"][0], source_item_id="n1",
        why_it_matters="半導體設備出口管制擴大,對成熟製程廠是新增的限制")]
    hits = [a for a in ad.depth_advisories(obj, pk) if "昨日觀點" in a]
    assert any("top_news_analysis[0]" in a for a in hits), hits


def test_a_secondary_delta_is_not_flagged():
    saved = rc.extract(_obj_with_secondary(), _packet(date="2026-08-07"))
    pk = _packet(recap=saved)
    obj = fx.valid_analysis()
    obj["top_news_analysis"] = [dict(
        obj["top_news_analysis"][0], source_item_id="n1",
        why_it_matters="管制清單今日新增兩家台廠,影響從方向變成名單")]
    hits = [a for a in ad.depth_advisories(obj, pk)
            if "top_news_analysis[0]" in a and "昨日觀點" in a]
    assert hits == [], hits


# ------------------------------------------------------------ 外審補審 F3

def test_two_events_on_one_company_do_not_swap_views():
    """**外審補審 F3 的反例。** 昨天台積電有兩件事(法說會、熊本擴廠),
    今天擴廠延續 —— 先前 `view_for` 取第一個實體相交的項目,擴廠的敘述
    會拿到法說會的觀點,模型被要求對無關的判斷寫「強化/轉弱/翻轉」。"""
    items = [{"statement": "法說會下修全年展望", "direction": "bearish",
              "entities": ["台積電"], "date": "2026-08-07",
              "action": "", "title": "台積電法說會下修全年展望"},
             {"statement": "熊本二廠動工進度超前", "direction": "bullish",
              "entities": ["台積電"], "date": "2026-08-07",
              "action": "", "title": "台積電熊本二廠動工進度超前"}]
    got = rc.view_for({"台積電"}, items, titles="台積電熊本二廠動工進度再更新")
    assert "熊本" in got, got
    assert "法說會" not in got, got


def test_an_ambiguous_match_gives_no_basis():
    """**分不出來就不給基準。** 兩筆同分代表身分分辨不了,而配到別件事
    的觀點比沒有基準更糟(模型會對無關的判斷寫強化/轉弱)。"""
    items = [{"statement": "A", "direction": "bullish", "entities": ["台積電"],
              "date": "2026-08-07", "action": "", "title": "台積電消息一則"},
             {"statement": "B", "direction": "bearish", "entities": ["台積電"],
              "date": "2026-08-07", "action": "", "title": "台積電消息一則"}]
    assert rc.view_for({"台積電"}, items, titles="台積電消息一則") == ""


def test_unrelated_event_on_the_same_company_gets_no_view():
    """同公司但完全不同的事件 —— 不給基準,不亂配。"""
    items = [{"statement": "法說會下修全年展望", "direction": "bearish",
              "entities": ["台積電"], "date": "2026-08-07",
              "action": "", "title": "台積電法說會下修全年展望"}]
    assert rc.view_for({"台積電"}, items,
                       titles="台積電董事會通過股利分派案") == ""


# ------------------------------------------------------------ 外審補審 F7

def test_a_corrupt_state_is_distinguishable_and_preserved(tmp_path):
    """**「沒有檔案」與「檔案壞了」是兩件事**:壞檔要說得出來,而且
    今天的存檔不得把它靜默覆寫掉 —— 覆寫掉就查不出昨天為什麼壞。"""
    f = tmp_path / "analysis_recap.json"
    f.write_text('{"date": "2026-08-07", "items": [trunc', encoding="utf-8")
    got = rc.load(f)
    assert got.get("unreadable"), got
    assert rc.usable(got, "2026-08-08") == []      # 降級行為與「沒有」相同
    obj = fx.valid_analysis()
    obj["key_drivers"] = [dict(obj["key_drivers"][0], cluster_id="cluster:n2")]
    assert rc.save(f, obj, _packet(date="2026-08-08")) is True
    assert (tmp_path / "analysis_recap.json.corrupt").exists(), "壞檔被覆寫掉了"
    assert rc.load(f)["date"] == "2026-08-08"


def test_a_missing_state_leaves_no_corrupt_copy(tmp_path):
    """反向:正常路徑不得留下 `.corrupt` 垃圾。"""
    f = tmp_path / "analysis_recap.json"
    assert rc.load(f) == {}
    obj = fx.valid_analysis()
    obj["key_drivers"] = [dict(obj["key_drivers"][0], cluster_id="cluster:n2")]
    rc.save(f, obj, _packet(date="2026-08-08"))
    assert not (tmp_path / "analysis_recap.json.corrupt").exists()


# ------------------------------------------------------------ 生產接線

def test_production_wires_both_ends_of_the_loop():
    """**守衛不得靠遺忘失效**:存與讀都只在生產有接線時存在 ——
    上面每一條在生產斷線時照樣綠,而閉環整段 no-op。"""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "morning_report.py").read_text(encoding="utf-8")
    # 寫入端:成功點存檔,而且結果進 manifest(靜默失敗明天才發現就晚了)
    assert "_arc.save(\n        ANALYSIS_RECAP_FILE" in src, \
        "存檔沒有走具名常數(push 登錄檢查看不見 inline 路徑)"
    # 讀取端:與 EVENT_TIMELINE 同一段接進 quotes
    assert "_arc.load(ANALYSIS_RECAP_FILE)" in src
    assert 'quotes["ANALYSIS_RECAP"] = _recap_state' in src
    # **兩個接受出口都要經過同一個 finalizer**(外審補審 F2):
    # 加深失敗沿用第一版那條先前不存 recap,信照寄而明天沒有基準。
    body = src.split("def _luna_analysis(")[1].split("\ndef ")[0]
    assert body.count("return _accept_luna(") == 2, body.count("return _accept_luna(")
    # **不得有繞過 finalizer 的接受出口。** 只數 finalizer 的呼叫次數
    # 擋不住「在它前面多一個 `return text`」—— 突變驗證當場抓到。
    strays = [ln.strip() for ln in body.splitlines()
              if ln.strip() == "return text"]
    assert not strays, ("有接受出口沒走 _accept_luna(不會存 recap):"
                        f"{strays}")


# ------------------------------------------------- 第二輪外審(補審 pass 2)

def test_a_generic_headline_pair_on_one_company_gets_no_view():
    """**R2-F2 的反例。** 昨天「台積電宣布法說會日期」、今天「台積電宣布
    擴建新廠」—— 共同詞全是主體名與「宣布」,重疊 0.50 越過 0.3 門檻,
    而它們是兩件事。主體相交已經在上一層算過了。"""
    items = [{"statement": "法說會前瞻", "direction": "bearish",
              "entities": ["台積電"], "date": "2026-08-07",
              "action": "", "title": "台積電宣布法說會日期"}]
    assert rc.view_for({"台積電"}, items, titles="台積電宣布擴建新廠") == ""
    # 真的同一件事仍要接得上
    assert rc.view_for({"台積電"}, items,
                       titles="台積電法說會日期確定 下週登場")


def test_the_unreadable_flag_carries_no_exception_text(tmp_path):
    """**R2-F5。** 這個 dict 會進 packet → prompt;原始例外訊息含路徑與
    內文片段,既是雜訊也是一條不必要的注入面。旗標是布林。"""
    f = tmp_path / "analysis_recap.json"
    f.write_text('{"date": "2026-08-07", "items": [trunc', encoding="utf-8")
    got = rc.load(f)
    assert got["unreadable"] is True
    assert not any(isinstance(v, str) and len(v) > 20 for v in got.values()), got


def test_production_records_the_unreadable_state_as_degraded():
    """**只印 stderr 的降級等於沒有降級紀錄**:生產監控分不出
    「昨天沒跑成」與「state 壞了」。"""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "morning_report.py").read_text(encoding="utf-8")
    i = src.index("_recap_state = _arc.load(ANALYSIS_RECAP_FILE)")
    seg = src[i:i + 600]
    assert '_DEGRADED_STEPS.append("analysis_recap_unreadable")' in seg, seg
    assert "::warning::" in seg, seg
    # 壞掉時不得把旗標塞進 packet
    assert "_recap_state = {}" in seg, seg


def test_same_action_different_object_gets_no_view():
    """**R3-F2 的反例。** 「美國制裁伊朗」與「美國制裁俄羅斯」共用主體
    「美國」與同一個動作碼 `sanction` —— 動作相同會繞過標題辨識,
    把伊朗的觀點掛到俄羅斯的事件上。**動作不等於身分。**"""
    import event_identity as ei
    items = [{"statement": "制裁伊朗對油價的影響", "direction": "bearish",
              "entities": ["美國", "伊朗"], "date": "2026-08-07",
              "action": "sanction",
              "object": ei.object_signature("sanction", ["美國", "伊朗"]),
              "title": "美國宣布制裁伊朗"}]
    assert rc.view_for({"美國", "俄羅斯"}, items,
                       titles="美國宣布制裁俄羅斯") == ""
    # 同一樁制裁的續篇仍要接得上
    assert rc.view_for({"美國", "伊朗"}, items, titles="美國制裁伊朗再加碼")


def test_extract_stores_the_object_signature():
    """對象簽章要**存下來** —— 讀取端比不了昨天沒存的東西。"""
    rec = _saved()
    assert "object" in rec["items"][0], rec["items"][0]


def test_the_stored_object_is_what_rescues_a_reworded_continuation():
    """**R3-F2 的隔離反例。** 昨天「美國宣布制裁伊朗」的辨識詞只有
    「制裁」一個(低於門檻)—— 標題那條路走不通,**只有存下來的
    對象簽章**能證明今天這篇講的是同一樁。不存對象的話,
    同一件事的續篇會失去 diff 基準。

    **走 `extract` 產生 items,不是手寫。** 第一版自己造 items 並把
    `object` 填好 —— 於是把 `extract` 改成不存對象時,測試照樣綠
    (突變驗證抓到)。這個 repo 記過:測試要用生產的呼叫形狀。
    """
    import event_identity as ei
    assert len(ei.discriminative_tokens("美國宣布制裁伊朗",
                                        {"美國", "伊朗"})) < ei.MIN_DISCRIMINATIVE
    yday = _packet(
        news=[{"source_item_id": "n1", "title": "美國宣布制裁伊朗",
               "entities": ["美國", "伊朗"], "source": "X",
               "source_name": "X"}],
        date="2026-08-07")
    obj = fx.valid_analysis()
    obj["key_drivers"] = [dict(obj["key_drivers"][0], cluster_id="cluster:n1",
                               statement="制裁伊朗對油價的影響")]
    obj["top_news_analysis"] = []
    items = rc.usable(rc.extract(obj, yday), "2026-08-08")
    assert items and items[0]["object"], items
    assert rc.view_for({"美國", "伊朗"}, items,
                       titles="美國對伊朗啟動新一輪制裁")


def _sanction_item():
    """走 `extract` 產生(生產呼叫形狀),不是手寫。"""
    pk = _packet(
        news=[{"source_item_id": "n1",
               "title": "美國宣布對伊朗新一輪經濟制裁措施",
               "entities": ["美國", "伊朗"], "source": "X",
               "source_name": "X"}], date="2026-08-07")
    obj = fx.valid_analysis()
    obj["key_drivers"] = [dict(obj["key_drivers"][0], cluster_id="cluster:n1",
                               statement="制裁伊朗對油價的影響")]
    obj["top_news_analysis"] = []
    return rc.usable(rc.extract(obj, pk), "2026-08-08")


def test_a_known_object_mismatch_is_a_hard_reject():
    """**R4-F1 的反例。** 同一句話把伊朗換成俄羅斯 —— 去掉主體之後
    剩下的辨識詞幾乎完全相同,標題那條路會翻案。**對象已知且不同
    就是兩件事**,不給標題翻案的機會。"""
    items = _sanction_item()
    assert items[0]["object"], items
    assert rc.view_for({"美國", "俄羅斯"}, items,
                       titles="美國宣布對俄羅斯新一輪經濟制裁措施") == ""


def test_a_cross_language_continuation_survives_the_object_rule():
    """**R4-F2 的反例(讀取側)。** 對象是硬判準之後,不正規化就會讓
    跨語言的續篇失去基準 —— 身分的正規化只做一半,比完全不做更難查。"""
    items = _sanction_item()
    assert rc.view_for({"美國", "Iran"}, items,
                       titles="美國對 Iran 新一輪經濟制裁措施")


def test_the_saved_side_is_normalised_too():
    """**R4-F2 的反例(寫入側)。** 昨天是英文報導,實體存的是
    `United States / Iran`;今天中文報導給「美國/伊朗」。
    **兩側都要正規化** —— 只修讀取端的話,對象簽章仍然對不上。
    (上一條的昨日實體本來就是標準名,勝負分不出來 —— 突變驗證抓到。)"""
    pk = _packet(
        news=[{"source_item_id": "n1",
               "title": "US announces new sanctions package on Iran",
               "entities": ["United States", "Iran"], "source": "X",
               "source_name": "X"}], date="2026-08-07")
    obj = fx.valid_analysis()
    obj["key_drivers"] = [dict(obj["key_drivers"][0], cluster_id="cluster:n1",
                               statement="制裁對油價的影響")]
    obj["top_news_analysis"] = []
    items = rc.usable(rc.extract(obj, pk), "2026-08-08")
    assert items[0]["entities"] == ["伊朗", "美國"], items[0]["entities"]
    assert rc.view_for({"美國", "伊朗"}, items,
                       titles="美國宣布對伊朗新一輪經濟制裁措施")
