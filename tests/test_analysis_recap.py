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
    assert rc.save(f, obj, _packet(date="2026-08-07")) == rc.SAVED
    assert rc.load(f)["date"] == "2026-08-07"


def test_save_to_an_unwritable_path_degrades_without_raising(tmp_path):
    """加深不可斷晨報:存不進去回 False,不拋。"""
    # **三態**(2026-08-09 P2):寫不進去是 `FAILED`,
    # 而「今天沒有值得留給明天的觀點」是 `NOTHING` —— 兩者
    # 先前都是 `False`,下游把它一律報成 defect。
    assert rc.save(tmp_path, fx.valid_analysis(), _packet()) == rc.FAILED


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
    assert rc.save(f, obj, _packet(date="2026-08-08")) == rc.SAVED
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
    # **每一個接受出口都要經過同一個 finalizer**(外審補審 F2):
    # 加深失敗沿用第一版那條先前不存 recap,信照寄而明天沒有基準。
    # 2026-08-17:第三個出口 —— 加深**沒有額度/時間**時直接用留著的合法
    # 淺版(外審 P1-2 r1:加深先前不扣額度,繞過 deadline 與 legacy 保留額)。
    # 守的性質不變:接受路徑一律走 finalizer,不得自己交出一份文字。
    body = src.split("def _luna_analysis(")[1].split("\ndef ")[0]
    assert body.count("return _accept_luna(") == 3, body.count("return _accept_luna(")
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
               "title": "US announces $2 billion sanctions package on Iran",
               "entities": ["United States", "Iran"], "source": "X",
               "source_name": "X"}], date="2026-08-07")
    obj = fx.valid_analysis()
    obj["key_drivers"] = [dict(obj["key_drivers"][0], cluster_id="cluster:n1",
                               statement="制裁對油價的影響")]
    obj["top_news_analysis"] = []
    items = rc.usable(rc.extract(obj, pk), "2026-08-08")
    assert items[0]["entities"] == ["伊朗", "美國"], items[0]["entities"]
    # 第三十一輪 P1-2 起,跨語言還需要一個逐樁的錨(這裡:同量級金額)。
    assert rc.view_for({"美國", "伊朗"}, items,
                       titles="美國宣布對伊朗20億美元制裁方案")


def test_nothing_to_save_is_not_a_failure(tmp_path):
    """**「沒東西可存」不是「存檔失敗」**(2026-08-09 P2)。

    資料稀薄的日子,今天的分析裡可能沒有值得留給明天的觀點 ——
    那是正常的答案。兩者先前都回 `False`,而看門狗把 `False` 報成
    defect「分析成功但昨日觀點沒存下來」:一個假的缺陷,
    而讀著它的人會去查一個沒有壞的東西。
    """
    import analysis_recap as rc
    empty = fx.valid_analysis()
    empty["key_drivers"] = []
    empty["top_news_analysis"] = []
    empty["asset_net_effects"] = []
    empty["scenario_tree"] = {"base": {}, "bull": {}, "bear": {},
                              "invalidation_triggers": []}
    out = rc.save(tmp_path / "recap.json", empty, _packet())
    assert out in (rc.NOTHING, rc.SAVED), out
    if out == rc.NOTHING:
        import run_quality as rq
        # **只驗 recap 那一格**:自己另寫一組佔位符的話,
        # `manifest_incomplete` 會混進答案裡(第二十七輪外審 P1-2 之後,
        # 空的 payload_budget/metrics 本身就是缺陷)。
        assert [p for p in rq.assess({"report_kind": rq.MORNING_REPORT,
                                      "llm": {"analysis_origin":
                                              "luna_specialized",
                                              "recap_saved": out}})
                if p["code"] == "recap_not_saved"] == []


def test_a_write_failure_is_still_a_defect():
    """**修誤報不得造出漏報**:真的寫不進去仍要報 defect。"""
    import analysis_recap as rc
    import run_quality as rq
    def _codes(v):
        return [p["code"] for p in rq.assess(
            {"report_kind": rq.MORNING_REPORT,
             "llm": {"analysis_origin": "luna_specialized",
                     "recap_saved": v}})]

    assert "recap_not_saved" in _codes(rc.FAILED)
    # 舊的布林 `False` 也仍當成失敗(那是會出聲的那一邊)
    assert "recap_not_saved" in _codes(False)
    # 而 saved / nothing 不報
    assert "recap_not_saved" not in _codes(rc.SAVED)
    assert "recap_not_saved" not in _codes(rc.NOTHING)


# ===== 縱深第四批 B:首見判斷逐日 carry =====

def _thread_item(stmt, title):
    return {"statement": stmt, "direction": "bullish", "entities": ["台積電"],
            "action": "", "object": "", "title": title}


_T1 = "台積電 CoWoS 先進封裝擴產計畫啟動"
_T2 = "台積電 CoWoS 先進封裝擴產追加訂單"
_T3 = "台積電 CoWoS 先進封裝擴產訂單再上修"


def test_the_origin_view_survives_across_days():
    """**檔案只留最新一天,「當初的預期」在第二天就沒了。**

    線索延燒到第三天時,「昨天說什麼」不足以寫出「當初預期 →
    應驗/落空」—— 首見要逐日 carry,而且首見永遠是**最早**那天,
    不是前一天(每天重新錨定的話,第三天的「首見」會變成第二天)。
    """
    day2 = {"date": "2026-08-08", "items": [_thread_item("進度提前", _T2)]}
    rc._carry_origins(day2, {"date": "2026-08-07",
                             "items": [_thread_item("擴產將推升營收", _T1)]})
    assert day2["items"][0]["origin"]["date"] == "2026-08-07"
    day3 = {"date": "2026-08-09", "items": [_thread_item("傳追加訂單", _T3)]}
    rc._carry_origins(day3, day2)
    assert day3["items"][0]["origin"]["date"] == "2026-08-07",         day3["items"][0].get("origin")
    assert day3["items"][0]["origin"]["statement"] == "擴產將推升營收"


def test_a_same_day_rerun_does_not_reanchor_the_origin():
    """**同日重跑只沿用、不建立**:拿今天的重跑當首見,
    「首見」與「今天」是同一天 —— 那不是任何東西的起點。"""
    day3 = {"date": "2026-08-09", "items": [_thread_item("第一版", _T3)]}
    rc._carry_origins(day3, {"date": "2026-08-07",
                             "items": [_thread_item("首見版", _T1)]})
    rerun = {"date": "2026-08-09", "items": [_thread_item("重跑版", _T3)]}
    rc._carry_origins(rerun, day3)
    assert rerun["items"][0]["origin"]["date"] == "2026-08-07"
    # 同日、而且前一份**沒有** origin → 不得無中生有
    fresh = {"date": "2026-08-09", "items": [_thread_item("又一版", _T3)]}
    rc._carry_origins(fresh, {"date": "2026-08-09",
                              "items": [_thread_item("同日另一版", _T3)]})
    assert "origin" not in fresh["items"][0]


def test_an_unrelated_thread_gets_no_origin():
    """接不上(或模稜兩可)就不 carry —— 配到別條線索的首見,
    模型會對無關的判斷寫「應驗/落空」。"""
    day2 = {"date": "2026-08-08",
            "items": [_thread_item("完全另一件事", "聯發科發表新晶片平台")]}
    rc._carry_origins(day2, {"date": "2026-08-07",
                             "items": [_thread_item("擴產", _T1)]})
    assert "origin" not in day2["items"][0]


def test_the_origin_is_its_own_field_not_part_of_yesterday():
    """**渲染與重述檢查要各看各的欄位**(外審)。

    首見串進 `yesterday_view` 的話,`restatements()` 拿整串算重疊 ——
    模型**正確**回顧當初預期(應驗/落空)時,敘述必然與首見高度重疊,
    被誤判成重述、耗掉唯一一次加深呼叫。
    """
    it = dict(_thread_item("進度提前", _T2), date="2026-08-08",
              origin={"date": "2026-08-07", "statement": "擴產將推升營收",
                      "direction": "bullish"})
    yv = rc.view_for(["台積電"], [it], titles=_T2)
    ov = rc.origin_view_for(["台積電"], [it], titles=_T2)
    assert "首見" not in yv, yv                     # 昨日欄位乾淨
    assert yv.startswith("2026-08-08本報"), yv
    assert ov.startswith("2026-08-07首見"), ov
    # 第一天(origin 就是昨天)沒有首見欄位
    first = dict(it, origin={"date": "2026-08-08", "statement": "x",
                             "direction": "bullish"})
    assert rc.origin_view_for(["台積電"], [first], titles=_T2) == ""


def test_the_origin_view_reaches_the_packet_cluster():
    """**沒接上等於不存在**(repo 記過,而 TC2 突變當場證明沒有測試
    走到 `evidence_packet` 的接線):首見要真的出現在事件群的
    `origin_view` 欄位上,且與 `yesterday_view` 是兩個欄位。"""
    saved = _saved()
    saved["date"] = "2026-08-07"
    for it in saved["items"]:
        it["origin"] = {"date": "2026-08-05",
                        "statement": "首見:封測供需趨緊",
                        "direction": "bullish"}
    pk = _packet(recap=saved, date="2026-08-08")
    clu = next(c for c in pk["news_clusters"]["clusters"]
               if c.get("yesterday_view"))
    assert clu["origin_view"].startswith("2026-08-05首見"), clu["origin_view"]
    assert "首見" not in clu["yesterday_view"], clu["yesterday_view"]


def test_recapping_the_origin_is_not_a_restatement():
    """**反例走 `restatements()` 本體**:今天的敘述回顧首見的措辭
    (那正是 prompt 要求的)不得被計成重述 —— 重述比的是
    `yesterday_view`,而首見在 `origin_view` 裡。"""
    pk = {"news_clusters": {"clusters": [{
        "cluster_id": "c1", "member_source_ids": ["n1"],
        "yesterday_view": "2026-08-08本報(偏多):進度提前",
        "origin_view": "2026-08-07首見(偏多):擴產將推升營收 帶動先進封裝"}]}}
    obj = {"key_drivers": [{
        "statement": "當初預期擴產將推升營收 帶動先進封裝,今日訂單數字應驗",
        "cluster_id": "c1"}], "top_news_analysis": []}
    hits = rc.restatements(obj, pk)
    assert hits == [], hits


def test_the_origin_carry_is_wired_into_save(tmp_path):
    """**沒有呼叫端的函式,它的 docstring 是假的**(repo 記過):
    carry 要走生產的 `save()` 路徑,直接呼叫 `_carry_origins` 的測試
    量不到「存檔時真的有接」。"""
    import json
    f = tmp_path / "recap.json"
    day1 = {"date": "2026-08-07", "items": [_thread_item("首見版", _T1)]}
    f.write_text(json.dumps(day1, ensure_ascii=False), encoding="utf-8")
    # `save()` 吃的是 analysis_obj+packet;繞過 extract 直接驗 carry 的話
    # 又量不到接線 —— 用 monkeypatch 不行(save 內部呼叫 extract),
    # 所以給一份會 extract 出同一條線索的最小輸入。
    obj = {"key_drivers": [], "top_news_analysis": [
        {"source_item_id": "n1", "why_it_matters": "進度提前",
         "direction": "bullish"}]}
    pk = {"target_session_date": "2026-08-08",
          "news": [{"source_item_id": "n1", "title": _T2,
                    "entities": ["台積電"]}],
          "news_clusters": {"clusters": [
              {"cluster_id": "c1", "member_source_ids": ["n1"]}]}}
    assert rc.save(f, obj, pk) == rc.SAVED
    saved = json.loads(f.read_text(encoding="utf-8"))
    assert saved["items"][0]["origin"]["date"] == "2026-08-07", saved


def test_the_prompt_asks_for_fulfilment_of_the_origin_view():
    """prompt 要說出「應驗/落空/仍待驗證」—— 沒說的話,首見只是多一段
    被複述的舊文。**錨在 prompt 的規則句**(`yesterday_view` 帶「首見」),
    不是版本註解 —— 第一版搜裸「帶首見」,先命中的是 v29 的說明,
    量到的是別的東西(突變驗證當場抓到)。"""
    import io as _io
    from pathlib import Path
    src = _io.open(Path(__file__).resolve().parents[1] / "prompt_profiles.py",
                   encoding="utf-8").read()
    anchor = "事件群帶 `origin_view` 時"
    assert anchor in src
    seg = src[src.index(anchor):src.index(anchor) + 400]
    assert "應驗" in seg and "落空" in seg, seg


# ===== 第三十輪外審 P1-3:身分只留一份 =====

def _item(title, ents, statement="昨天的判斷"):
    import event_identity as _eid
    it = {"statement": statement, "direction": "bearish",
          "entities": sorted(ents), "title": title, "date": "2026-08-09"}
    it.update(_eid.view_identity(title, ents))
    return it


def test_two_incidents_of_the_same_action_do_not_share_a_view():
    """**同一個動作對同一個對象,還要是同一樁**(外審 P1-3):同公司同月的
    兩起資安事件,動作與對象都相同 —— 上一版 `action_match` 一成立就接,
    標題重疊根本不看。於是今天這一起會拿到上一起的昨日觀點與首見,
    模型被要求對**另一件事**寫「應驗/落空」。"""
    yesterday = _item("台積電遭勒索軟體攻擊 部分產線短暫停擺", ["台積電"])
    assert yesterday["action"] == "cyberattack"
    today = "台積電遭網路攻擊 供應鏈系統中斷"
    assert rc.best_view(["台積電"], [yesterday], titles=today) is None
    # 同一樁的續篇照樣接得回(修正不得把延續一起關掉)
    same = "台積電勒索軟體事件 產線今日全面復工"
    assert rc.best_view(["台積電"], [yesterday], titles=same) is not None


def test_two_sanction_rounds_on_the_same_target_do_not_share_an_origin():
    """同一目標的兩輪不同制裁同理 —— 對象相同、動作相同,是兩件事。"""
    y = _item("美國制裁伊朗祕密貨幣交易網絡與空殼公司", ["伊朗", "美國"])
    assert y["action"] == "sanction"
    other = "美國制裁伊朗無人機零組件供應鏈與航運仲介"
    assert rc.best_view(["伊朗", "美國"], [y], titles=other) is None


def test_the_same_arms_sale_is_not_split_by_the_actor():
    """**假分裂的那一半**(走生產的寫入路徑 `extract`):同一批對台軍售,
    受援國才是對象 —— 上一版兩端都用 `object_signature(action, ents)`,
    於是對象是整個主體集合「台灣、美國」,而 timeline 算的是「台灣」。
    兩套判準各自漂移,昨日觀點就在某一天無聲消失。"""
    import event_identity as _eid
    pk = _packet(news=[{"source_item_id": "n1",
                        "title": "美國宣布對台軍售 F-16 零附件",
                        "entities": ["台灣", "美國"], "source": "X",
                        "source_name": "X"}], date="2026-08-07")
    obj = fx.valid_analysis()
    obj["key_drivers"] = [dict(obj["key_drivers"][0], cluster_id="cluster:n1",
                               statement="軍售對國防類股的影響")]
    obj["top_news_analysis"] = []
    items = rc.usable(rc.extract(obj, pk), "2026-08-08")
    # 寫入端存的就是 timeline 的答案(受援國),不是主體集合
    assert items[0]["object"] == "台灣" == _eid.action_object(
        "arms_sale", "美國宣布對台軍售 F-16 零附件", ["台灣", "美國"])
    # 讀取端同一個入口:今天這一群同時點名台灣與美國,對象仍是台灣
    hit = rc.best_view(["台灣", "美國"], items,
                       titles="對台軍售案 F-16 零附件出口許可獲國會通過")
    assert hit is not None and hit["statement"] == "軍售對國防類股的影響"


def test_recap_and_timeline_answer_with_the_same_identity():
    """**判準只有一份**:recap 存的動作與對象,要與 timeline 算出來的
    一模一樣 —— 兩邊各寫一次的話,「同一件事」會有兩個答案。"""
    import event_identity as _eid
    title = "美國宣布對台軍售 F-16 零附件"
    ents = ["台灣", "美國"]
    ident = _eid.timeline_identity(
        {"event_type": "geopolitical", "title": title}, ents, "2026-08-09")
    view = _eid.view_identity(title, ents)
    assert (view["action"], view["object"]) == (ident["action"],
                                                ident["object"])
    assert view["incident_tokens"] == ident["incident_tokens"]


def test_a_cross_language_continuation_needs_an_incident_anchor():
    """**跨語言:同動作+同對象只證明「可能是同一件」**(第三十一輪 P1-2)。

    同一目標的兩輪制裁、同公司的兩起資安事件 —— 中英文辨識詞零共用,
    incident 否決比不出來;先前光憑動作+對象就接,今天這一輪會拿到
    上一輪的昨日觀點,模型被要求對**另一樁**寫「應驗/落空」。
    現在要一個只屬於這一樁的錨(同量級金額/帶單位數量/第三實體)。
    """
    y = _item("US announces $2 billion sanctions package on Iran",
              ["伊朗", "美國"])
    y["summary"] = ""
    assert rc._comparable("US announces new sanctions on Iran",
                          "美國宣布對伊朗制裁") is False
    # 無錨 → 不接(可能是另一輪制裁)
    assert rc.best_view(["伊朗", "美國"], [y],
                        titles="美國宣布對伊朗新一輪經濟制裁措施") is None
    # 同量級金額錨 → 接(cross_lang 的既有判準)
    assert rc.best_view(["伊朗", "美國"], [y],
                        titles="美國宣布對伊朗20億美元制裁方案") is not None


def test_two_cross_language_incidents_same_company_stay_separate():
    """同公司的中英文兩起資安事件(reviewer 的原始反例)——
    無錨時不得互相認領觀點。"""
    y = _item("台積電遭勒索軟體攻擊,客戶資料外洩", ["台積電"])
    y["summary"] = ""
    got = rc.best_view(["台積電"], [y],
                       titles="TSMC reports separate supplier-portal "
                              "data breach")
    assert got is None, got
    # **混合書寫同樣要錨**(政策一致):辨識詞零共用時「比不出來」,
    # 而比不出來的預設從「接」改成「不接」—— 對另一樁寫「應驗/落空」
    # 比少一次 diff 更糟。
    mixed = _item("台積電 hit by ransomware; fabs halted temporarily",
                  ["台積電"])
    mixed["summary"] = ""
    assert rc._comparable(mixed["title"], "台積電遭網路攻擊 供應鏈系統中斷")         is False
    assert rc.best_view(["台積電"], [mixed],
                        titles="台積電勒索軟體事件 產線今日全面復工") is None
    # 中文標題裡的英文產品名不影響判定(比例,不是有沒有)
    assert rc._comparable("台積電 CoWoS 產能傳大幅擴充",
                          "台積電先進封裝擴產再加碼") is True



def test_extract_reads_the_recipient_from_the_summary():
    """**寫入端也要吃 summary**(第三十一輪 P1-1A):受詞只在 summary 的
    軍售新聞,recap 存下的對象要與 timeline 同一個答案(台灣),
    否則明天標題寫明「對台」時,兩天的對象對不上、觀點接不回來。"""
    pk = _packet(
        news=[{"source_item_id": "n1",
               "title": "美國軍售最新動向",
               "summary": "五角大廈證實新一批軍售 package for Taiwan,對台灣交付時程未定",
               "entities": ["美國", "台灣"], "source": "X",
               "source_name": "X"}], date="2026-08-07")
    obj = fx.valid_analysis()
    obj["key_drivers"] = [dict(obj["key_drivers"][0], cluster_id="cluster:n1",
                               statement="軍售對供應鏈的影響")]
    obj["top_news_analysis"] = []
    items = rc.usable(rc.extract(obj, pk), "2026-08-08")
    assert items and items[0]["object"] == "台灣", items
    # 錨要用的 summary 也存下來(跨語言那一關讀它)
    assert "Taiwan" in str(items[0].get("summary") or ""), items[0]


def test_the_cross_language_anchor_receives_the_entities(monkeypatch):
    """外審 r1(P2):第三種錨(非對象第三實體)由 `entities` 算 ——
    只傳 title/summary 的話那條錨永遠是空的。**在邊界釘**:
    best_view 傳給錨的兩個 dict 都要帶 entities。"""
    import cross_lang as cl
    seen = {}

    def _spy(a, b, *, obj=""):
        seen["a"], seen["b"] = a, b
        return False

    monkeypatch.setattr(cl, "_shared_specific_anchor", _spy)
    y = _item("US announces new arms sale to Taiwan", ["美國", "台灣"])
    y["summary"] = ""
    rc.best_view(["美國", "台灣"], [y], titles="美國宣布對台軍售")
    assert seen, "跨語言路徑沒有走到錨"
    assert seen["a"].get("entities") and seen["b"].get("entities"), seen


def test_a_third_entity_anchor_is_reachable_with_entities():
    """錨本身:對象之外、兩側都點名且有別名組的公司(台積電)接得上 ——
    entities 沒進 dict 時這條永遠 False(那正是修掉的洞)。"""
    import cross_lang as cl
    ents = ["美國", "台灣", "台積電"]
    assert cl._shared_specific_anchor(
        {"title": "美國宣布對台軍售", "summary": "", "entities": ents},
        {"title": "US announces new arms sale to Taiwan", "summary": "",
         "entities": ents}, obj="台灣") is True
    assert cl._shared_specific_anchor(
        {"title": "美國宣布對台軍售", "summary": ""},
        {"title": "US announces new arms sale to Taiwan", "summary": ""},
        obj="台灣") is False


# ------------------------------------------------- Batch A:lineage 端到端

def test_a_shared_lineage_connects_across_languages_without_an_anchor():
    """**世系是單一契約**:timeline 的橋接早就做完跨語言的辨識 ——
    同世系直接接,不必再找金額/數量錨。"""
    y = _item("US announces new sanctions package on Iran", ["伊朗", "美國"])
    y["summary"] = ""
    y["lineage_id"] = "sanction:伊朗"
    got = rc.best_view(["伊朗", "美國"], [y],
                       titles="美國宣布對伊朗新一輪經濟制裁措施",
                       lineage_id="sanction:伊朗")
    assert got is not None, "同世系被丟掉了"


def test_different_lineages_never_share_a_view():
    """兩邊都有世系而不同 = 兩件事,標題再像也一樣。"""
    y = _item("美國宣布對伊朗新一輪經濟制裁措施", ["伊朗", "美國"])
    y["lineage_id"] = "sanction:伊朗#a1"
    got = rc.best_view(["伊朗", "美國"], [y],
                       titles="美國宣布對伊朗新一輪經濟制裁措施",
                       lineage_id="sanction:伊朗#b2")
    assert got is None, got


def test_an_item_without_lineage_still_uses_the_fuzzy_rules():
    """世系只約束**兩邊都有**的配對 —— 第一天的事件(還不在 active
    清單)沒有世系,照走既有判準,不得被連坐排除。"""
    y = _item("美國宣布對伊朗新一輪經濟制裁措施", ["伊朗", "美國"])
    y["lineage_id"] = ""
    got = rc.best_view(["伊朗", "美國"], [y],
                       titles="美國對伊朗經濟制裁再加碼",
                       lineage_id="sanction:伊朗")
    assert got is not None, "沒有世系的舊觀點被連坐排除了"


def test_extract_stores_the_cluster_lineage():
    """寫入端:packet cluster 的 lineage_id 要存進 recap item ——
    明天讀取端才有東西可比。"""
    pk = _packet(
        news=[{"source_item_id": "n1", "title": "美國宣布對台軍售",
               "entities": ["美國", "台灣"], "source": "X",
               "source_name": "X"}], date="2026-08-07")
    for c in pk["news_clusters"]["clusters"]:
        c["lineage_id"] = "arms_sale:台灣"
    obj = fx.valid_analysis()
    obj["key_drivers"] = [dict(obj["key_drivers"][0], cluster_id="cluster:n1",
                               statement="軍售的影響")]
    obj["top_news_analysis"] = []
    items = rc.usable(rc.extract(obj, pk), "2026-08-08")
    assert items and items[0].get("lineage_id") == "arms_sale:台灣", items


def test_one_lineage_writes_only_one_item():
    """**同世系只存一筆**(外審 r1):同一條世系對應兩個 cluster
    (跨語言分群)時,兩筆都寫會讓明天的世系直配退回模糊判準。"""
    pk = _packet(
        news=[{"source_item_id": "n1", "title": "美國宣布對台軍售",
               "entities": ["美國", "台灣"], "source": "X", "source_name": "X"},
              {"source_item_id": "n2",
               "title": "US announces arms sale to Taiwan",
               "entities": ["美國", "台灣"], "source": "Y", "source_name": "Y"}],
        date="2026-08-07")
    for c in pk["news_clusters"]["clusters"]:
        c["lineage_id"] = "arms_sale:台灣"
    obj = fx.valid_analysis()
    obj["key_drivers"] = [
        dict(obj["key_drivers"][0], cluster_id="cluster:n1",
             statement="軍售的影響(中文群)"),
        dict(obj["key_drivers"][0], cluster_id="cluster:n2",
             statement="arms sale view(英文群)")]
    obj["top_news_analysis"] = []
    items = rc.usable(rc.extract(obj, pk), "2026-08-08")
    lin = [it for it in items if it.get("lineage_id") == "arms_sale:台灣"]
    assert len(lin) == 1, lin
    assert "中文群" in lin[0]["statement"], "沒有依重要性序留第一筆"


def test_multiple_same_lineage_items_pick_the_first_deterministically():
    """讀取端防線:舊 state 殘留兩筆同世系時取第一筆,不退回模糊判準
    (退回的話跨語言那筆沒有錨、整個消失)。"""
    a = _item("US announces arms sale to Taiwan", ["美國", "台灣"],
              statement="第一筆")
    b = _item("美國對台軍售追蹤", ["美國", "台灣"], statement="第二筆")
    for it in (a, b):
        it["summary"] = ""
        it["lineage_id"] = "arms_sale:台灣"
    got = rc.best_view(["美國", "台灣"], [a, b],
                       titles="美國對台軍售最新進展",
                       lineage_id="arms_sale:台灣")
    assert got is not None and got["statement"] == "第一筆", got


# ------------------------- 外審 2026-08-17 P2-1:admission 才是渲染的真相

def _full_ledger(today="2026-08-18"):
    """帳本已滿(`WATCH_OPEN_MAX` 條都開著)。"""
    return {"date": today, "items": [], "watch_seq": rc.WATCH_OPEN_MAX,
            "watch": [{"watch_id": f"w{i}", "trigger": f"既有觀察點 {i}",
                       "why": "x", "horizon": "1-5d", "status": rc.WATCH_OPEN,
                       "created": "2026-08-10", "last_reviewed": "",
                       "deadline": "2026-12-31"}
                      for i in range(1, rc.WATCH_OPEN_MAX + 1)]}


def test_a_capacity_dropped_watch_is_not_rendered_as_persistent(tmp_path):
    """**信上不得承諾帳本不會兌現的事。**

    帳本滿了,今天模型又提一條 —— 先前信裡照樣印成「觀察觸發點」,
    而明天帳本裡根本沒有它(讀者以為系統在盯)。
    """
    import json as _j
    import analysis_render as ar
    import fixtures_analysis as fx
    path = tmp_path / "recap.json"
    path.write_text(_j.dumps(_full_ledger(), ensure_ascii=False),
                    encoding="utf-8")
    obj = fx.valid_analysis()
    obj["watch_triggers"] = [{"trigger": "美元指數突破 105", "why": "資金面",
                              "horizon": "1-5d"}]
    tracked = rc.tracked_triggers(str(path), obj, "2026-08-18")
    assert "美元指數突破 105" not in tracked, "帳本滿了卻收下了新條目"
    assert len(tracked) == rc.WATCH_OPEN_MAX, "帳本原本那幾條要還在"
    out = ar.render(obj, None, admitted_watch=tracked)
    assert "美元指數突破 105" in out, "內容仍要印出來(有參考價值)"
    assert "一次性觀察,未納入持續追蹤" in out, "沒有標出它不會被持續追蹤"


def test_an_admitted_watch_is_rendered_as_persistent(tmp_path):
    """反向:帳本收下的那條**不得**被標成一次性 —— 標錯方向同樣是說謊。"""
    import json as _j
    import analysis_render as ar
    import fixtures_analysis as fx
    path = tmp_path / "recap.json"
    path.write_text(_j.dumps({"date": "2026-08-18", "items": [], "watch": []},
                             ensure_ascii=False), encoding="utf-8")
    obj = fx.valid_analysis()
    obj["watch_triggers"] = [{"trigger": "美元指數突破 105", "why": "資金面",
                              "horizon": "1-5d"}]
    admitted = rc.tracked_triggers(str(path), obj, "2026-08-18")
    assert "美元指數突破 105" in admitted
    out = ar.render(obj, None, admitted_watch=admitted)
    assert "一次性觀察" not in out


def test_admission_and_the_ledger_agree(tmp_path):
    """**兩邊必須是同一個答案。** 渲染端問 `admitted_triggers`、存檔走
    `carry_watch` —— 判準寫兩份就會漂移,而漂移的症狀是信與帳本互相矛盾。"""
    import json as _j
    import fixtures_analysis as fx
    path = tmp_path / "recap.json"
    path.write_text(_j.dumps(_full_ledger(), ensure_ascii=False),
                    encoding="utf-8")
    obj = fx.valid_analysis()
    obj["watch_triggers"] = [
        {"trigger": "新的一條", "why": "x", "horizon": "1-5d"},
        {"trigger": "既有觀察點 1", "why": "x", "horizon": "1-5d"}]
    admitted = rc.tracked_triggers(str(path), obj, "2026-08-18")
    ledger, _seq, dropped = rc.carry_watch(rc.load(str(path)), obj, "2026-08-18")
    _in_ledger = {str(w.get("trigger") or "") for w in ledger}
    # **兩邊是同一個集合**,不是子集 —— 子集判準放得過「渲染端少報一半」
    assert admitted == _in_ledger, (admitted, _in_ledger)
    # 被容量擋掉的那條:兩邊都說沒收
    assert "新的一條" not in admitted and "新的一條" not in _in_ledger
    assert dropped == 1, dropped


def test_a_trigger_longer_than_the_ledger_limit_is_still_recognised(tmp_path):
    """**比對要用帳本的正規形式**(外審 2026-08-17 r1)。

    帳本存的是截到 `WATCH_CHARS` 的字串;渲染端先前拿**未截斷的全文**
    比對集合 —— 超過上限的 trigger 因此被錯標成「一次性觀察」,
    而它其實已經被收下了。
    """
    import json as _j
    import analysis_render as ar
    import fixtures_analysis as fx
    long_trigger = "美元指數" + "很長的條件說明" * 30      # 遠超 WATCH_CHARS
    assert len(long_trigger) > rc.WATCH_CHARS
    path = tmp_path / "recap.json"
    path.write_text(_j.dumps({"date": "2026-08-18", "items": [], "watch": []},
                             ensure_ascii=False), encoding="utf-8")
    obj = fx.valid_analysis()
    obj["watch_triggers"] = [{"trigger": long_trigger, "why": "資金面",
                              "horizon": "1-5d"}]
    tracked = rc.tracked_triggers(str(path), obj, "2026-08-18")
    assert tracked == {rc.canonical_trigger(long_trigger)}, tracked
    out = ar.render(obj, None, admitted_watch=tracked)
    assert "一次性觀察" not in out, "被收下的超長 trigger 被錯標成一次性"


def test_two_triggers_sharing_the_first_120_chars_are_one_entry(tmp_path):
    """截斷之後相同 → 帳本視為同一條(不重複開)。**渲染端要跟著同一個
    判準**,否則第二條會被標成一次性,而它其實與第一條是同一個承諾。"""
    import json as _j
    import analysis_render as ar
    import fixtures_analysis as fx
    head = "美元" * 70                                  # 前 120 字相同
    path = tmp_path / "recap.json"
    path.write_text(_j.dumps({"date": "2026-08-18", "items": [], "watch": []},
                             ensure_ascii=False), encoding="utf-8")
    obj = fx.valid_analysis()
    obj["watch_triggers"] = [{"trigger": head + "甲", "why": "x", "horizon": "1-5d"},
                             {"trigger": head + "乙", "why": "y", "horizon": "1-5d"}]
    ledger, _seq, _dropped = rc.carry_watch(rc.load(str(path)), obj, "2026-08-18")
    assert len(ledger) == 1, "截斷後相同卻開了兩條"
    tracked = rc.tracked_triggers(str(path), obj, "2026-08-18")
    out = ar.render(obj, None, admitted_watch=tracked)
    assert "一次性觀察" not in out, out[-300:]


def test_a_failed_save_keeps_the_claim_for_triggers_already_on_disk(tmp_path):
    """**存檔失敗不代表什麼都沒在追**(外審 2026-08-17 r2)。

    寫入是「暫存檔 → atomic replace」,失敗時舊帳本通常完整保留 ——
    今天再次提出的舊 trigger 明天仍然會被追。第一版在存檔失敗時傳空集合,
    於是那些條目被標成「一次性觀察」= 少報,而少報也是一種不準。
    """
    import json as _j
    path = tmp_path / "recap.json"
    path.write_text(_j.dumps(
        {"date": "2026-08-17", "items": [],
         "watch": [{"watch_id": "w1", "trigger": "美元指數突破 105",
                    "why": "資金面", "horizon": "1-5d",
                    "status": rc.WATCH_OPEN, "created": "2026-08-10",
                    "last_reviewed": "", "deadline": "2026-12-31"}]},
        ensure_ascii=False), encoding="utf-8")
    on_disk = rc.ledger_triggers(str(path))
    assert on_disk == {"美元指數突破 105"}, on_disk


def test_an_unreadable_ledger_says_it_does_not_know(tmp_path):
    """`None` 與空集合是兩件事:問不到磁碟狀態時渲染端**不標**,
    不假裝知道;確定什麼都沒在追才是空集合。

    2026-08-17 r3:第一版這裡寫成 `in (None, set())` —— **那種斷言
    兩個答案都收**,等於沒有釘住任何東西(外審點名)。逐種情況寫明。
    """
    # 沒有檔案 = 沒有帳本 = 確定什麼都沒在追
    assert rc.ledger_triggers(str(tmp_path / "missing.json")) == set()
    # 壞 JSON:`load()` 回 `{"unreadable": ...}`,裡面沒有 watch → 空集合
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json", encoding="utf-8")
    assert rc.ledger_triggers(str(bad)) == set()


def test_a_malformed_ledger_field_does_not_raise(tmp_path):
    """**合法 JSON 但欄位壞掉**(外審 2026-08-17 r3)。

    `_watch_ledger()` 自己會 `int(prior["watch_seq"])` —— 先前 try 只包住
    `load()`,於是 `"watch_seq": "invalid"` 的例外會往外拋、穿過
    `_accept_luna`,把**已經成功的整份分析**丟掉落回 legacy。
    這個 helper 的契約是「問不到就說不知道」,不是「問不到就毀掉晨報」。
    """
    import json as _j
    path = tmp_path / "recap.json"
    path.write_text(_j.dumps(
        {"date": "2026-08-17", "items": [], "watch_seq": "invalid",
         "watch": [{"watch_id": "w1", "trigger": "美元指數突破 105",
                    "status": rc.WATCH_OPEN, "horizon": "1-5d"}]},
        ensure_ascii=False), encoding="utf-8")
    assert rc.ledger_triggers(str(path)) is None, "壞欄位應該回 None,不是拋"


def test_a_closed_entry_is_not_reported_as_tracked(tmp_path):
    """已關閉的條目不算在追 —— 帳本只留在燒的那些。"""
    import json as _j
    path = tmp_path / "recap.json"
    path.write_text(_j.dumps(
        {"date": "2026-08-17", "items": [],
         "watch": [{"watch_id": "w1", "trigger": "已關閉的那條",
                    "status": "triggered", "horizon": "1-5d"}]},
        ensure_ascii=False), encoding="utf-8")
    assert rc.ledger_triggers(str(path)) == set()


def test_an_overlong_entry_left_by_an_older_version_still_matches(tmp_path):
    """**磁碟側也要正規化。**

    帳本是版控裡的 JSON,可能被手改、也可能由更舊的程式寫入未截斷的
    trigger。渲染端拿的是截斷後的正規形式 —— 磁碟側不跟著正規化,
    那條就對不上,被錯標成「一次性觀察」。
    (突變驗證顯示:只用今天寫出來的帳本測不到這條規則,因為那些值
    **已經**是截斷的 —— 所以這裡刻意造一個未截斷的舊值。)
    """
    import json as _j
    import analysis_render as ar
    import fixtures_analysis as fx
    long_trigger = "美元指數" + "很長的條件說明" * 30
    assert len(long_trigger) > rc.WATCH_CHARS
    path = tmp_path / "recap.json"
    path.write_text(_j.dumps(
        {"date": "2026-08-17", "items": [],
         "watch": [{"watch_id": "w1", "trigger": long_trigger,   # 未截斷
                    "why": "x", "horizon": "1-5d",
                    "status": rc.WATCH_OPEN, "created": "2026-08-10",
                    "last_reviewed": "", "deadline": "2026-12-31"}]},
        ensure_ascii=False), encoding="utf-8")
    on_disk = rc.ledger_triggers(str(path))
    assert on_disk == {rc.canonical_trigger(long_trigger)}, on_disk
    obj = fx.valid_analysis()
    obj["watch_triggers"] = [{"trigger": long_trigger, "why": "x",
                              "horizon": "1-5d"}]
    out = ar.render(obj, None, admitted_watch=on_disk)
    assert "一次性觀察" not in out, "舊帳本裡的未截斷值對不上了"
