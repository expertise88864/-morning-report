"""使用者 2026-11-02 看了當天晨報後的修正(融資單位、特報門檻、週回顧改週日)。"""
import morning_report as mr

#: 證交所 MI_MARGN selectType=MS 2026-10-30 實際回應(三列:張、張、仟元)
_TWSE_MS = {"stat": "OK", "tables": [
    {"title": "115年10月30日 信用交易統計",
     "fields": ["項目", "買進", "賣出", "現金(券)償還", "前日餘額", "今日餘額"],
     "data": [["融資(交易單位)", "353,926", "377,224", "9,729", "9,588,756", "9,555,729"],
              ["融券(交易單位)", "36,026", "45,607", "1,224", "189,002", "197,359"],
              ["融資金額(仟元)", "20,744,449", "29,996,997", "1,190,414", "633,910,848", "623,467,886"]]},
    {"title": None, "fields": None, "data": []}]}


class _Resp:
    status_code = 200

    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._p


def test_margin_balance_is_the_amount_row_not_the_lot_count(monkeypatch):
    """實信:「融資餘額從 9,555,729 千元」—— 那是張數;金額是 6,234.7 億元。"""
    monkeypatch.setattr(mr, "_http_get", lambda *a, **k: _Resp(_TWSE_MS))
    m = mr.fetch_twse_margin()
    assert m["margin_balance"] == 623_467_886          # 千元
    assert m["margin_lots"] == 9_555_729
    assert m["margin_balance_100m_twd"] == 6234.7
    assert m["margin_change_100m_twd"] == -104.4


def test_margin_without_amount_row_is_unknown_not_the_lot_count(monkeypatch):
    lots_only = {"stat": "OK", "tables": [{**_TWSE_MS["tables"][0],
                                           "data": _TWSE_MS["tables"][0]["data"][:2]}]}
    monkeypatch.setattr(mr, "_http_get", lambda *a, **k: _Resp(lots_only))
    assert mr.fetch_twse_margin()["margin_balance"] is None


def test_only_severe_cwa_alerts_reach_the_email(monkeypatch):
    """實信:每天都有「陸上強風特報」。只留颱風、豪雨以上、低溫。"""
    import datetime as dtm
    import email.utils as eut
    fresh = eut.format_datetime(dtm.datetime.now(dtm.timezone.utc))
    desc = "<description><![CDATA[ 臺中市、彰化縣、雲林縣。 ]]></description>"
    titles = ["11/02 04:27 發布陸上強風特報", "11/02 05:00 發布大雨特報", "11/02 05:10 發布濃霧特報",
              "11/02 05:20 發布大豪雨特報", "11/02 05:30 發布低溫特報", "11/02 05:40 發布陸上颱風警報"]
    rss = ("<rss><channel>" + "".join(
        f"<item><title>{t}</title>{desc}<link>https://cwa/{i}</link><pubDate>{fresh}</pubDate></item>"
        for i, t in enumerate(titles)) + "</channel></rss>").encode("utf-8")
    monkeypatch.setattr(mr, "_http_get_relaxed_strict", lambda *a, **k: rss)
    got = [a["title"][12:] for a in mr.fetch_cwa_alerts(max_items=10)]
    assert got == ["發布大豪雨特報", "發布低溫特報", "發布陸上颱風警報"]


def test_week_forecast_table_lists_monday_to_friday_of_that_week():
    import datetime as dtm
    hist = [{"target_session_date": d, "pred_taiex": 100.0, "actual_open_taiex": 101.0,
             "weighted_final_2330": 10.0, "actual_open_2330": 10.0}
            for d in ("2026-10-24", "2026-10-27", "2026-10-28", "2026-10-29", "2026-10-30", "2026-10-31")]
    h = mr._render_weekly_recap_html(hist, dtm.date(2026, 11, 1))
    assert all(f"10-{d}" in h for d in (27, 28, 29, 30))
    assert "10-24" not in h and "10-31" not in h        # 上週、週六都不算


def test_sunday_week_review_carries_the_forecast_review(monkeypatch, tmp_path):
    """七之五的預測檢討改由週日本週回顧的第一小節寫,統計仍由 Python 算。"""
    import datetime as dtm
    stats = {"taiex": {"n": 5, "mae_pct": 0.27, "bias_pct": -0.16, "hit_rate_pct": 100, "n_dir": 4},
             "tw2330": None, "critical_events": ["金管會暫無限空令"], "n_days": 5}
    monkeypatch.setattr(mr, "_compute_weekly_review_stats", lambda *a, **k: stats)
    monkeypatch.setattr(mr, "load_history_state", lambda: [])
    monkeypatch.setattr(mr, "NEWS_MEMORY_DIR", tmp_path)            # 不讀 repo 的真實 state
    monkeypatch.setattr(mr, "EVENT_TIMELINE_FILE", tmp_path / "none.json")
    p = mr._build_week_review_prompt(dtm.datetime(2026, 11, 1, 6, tzinfo=mr.TPE))
    assert "### 近期預測檢討與下週假設" in p and "平均絕對誤差 0.27%" in p
    assert p.index("### 近期預測檢討與下週假設") < p.index("### 本週大事回顧")
    monkeypatch.setattr(mr, "_compute_weekly_review_stats", lambda *a, **k: {})
    assert mr._build_week_review_prompt(dtm.datetime(2026, 11, 1, 6, tzinfo=mr.TPE)) == ""


def test_world_severity_ranks_the_1102_headlines():
    """實信:七之二放了地方槍擊與震度 3 級地震,同一天素材裡的伊朗、OPEC+、幣安遭駭卻沒上。"""
    import world_severity as ws
    assert ws.rank("伊朗軍方宣稱　若與美國爆發新戰事恐比先前更漫長") == 2
    assert ws.rank("路透：OPEC+會議料將12月目標產油量維持不變") == 2
    assert ws.rank("Binance resumes withdrawals following $6.3 billion hack") == 2
    assert ws.rank("美國阿拉巴馬州美式足球賽場外爆槍響　1死8傷") == 0
    assert ws.rank("東部海域地震規模5.6 最大震度宜蘭花蓮3級") == 0
    assert ws.rank("某國槍擊案 32人死亡") == 1                      # 大規模死傷不算地方小事
    assert ws.rank("某國槍擊案造成1死50傷") == 1                    # 傷者數十人也算(外審 PR #38)
    assert ws.rank("mall shooting leaves 2 dead, 31 injured") == 1
    assert ws.rank("德國生活成本抗議演變成暴力事件 商店遭洗劫") == 0


def test_legacy_world_block_lists_severe_events_first():
    news = [{"title": "東部海域地震規模5.6 最大震度宜蘭花蓮3級", "source": "中央社國際"},
            {"title": "美國阿拉巴馬州美式足球賽場外爆槍響　1死8傷", "source": "中央社國際"},
            {"title": "伊朗軍方宣稱　若與美國爆發新戰事恐比先前更漫長", "source": "中央社國際"}]
    import world_severity as ws
    first = sorted(news, key=lambda x: -ws.rank(x.get("title")))[0]["title"]
    assert first.startswith("伊朗軍方")
    import inspect
    assert "world_severity.rank" in inspect.getsource(mr._build_prompt) or \
        "world_severity.rank" in open(mr.__file__, encoding="utf-8").read()


def test_editorial_order_puts_minor_incidents_behind_real_events():
    import editorial_priority as ep
    pk = {"news": [{"source_item_id": "q", "title": "東部海域地震規模5.6 最大震度宜蘭花蓮3級"},
                   {"source_item_id": "w", "title": "印度反對派號召5萬人集會"},
                   {"source_item_id": "c", "title": "經濟部研議擴大對陸半導體出口管制"}]}
    order = [c["source_item_id"] for c in ep.order([{"source_item_id": s} for s in "qwc"], pk)]
    assert order[0] == "c" and order[-1] == "q"


def test_prompts_carry_the_1102_rules():
    import prompt_profiles
    import writing_rules
    legacy = open(writing_rules.__file__, encoding="utf-8").read()
    assert "每條 2～3 句、約 60～100 字" in legacy and "60 字上限是硬性的" not in legacy
    assert "依嚴重性與影響範圍取捨" in legacy
    src = open(mr.__file__, encoding="utf-8").read()
    assert "台灣當天的大事優先" in src and "七之五、近期預測檢討" not in src
    luna = open(prompt_profiles.__file__, encoding="utf-8").read()
    assert "每一條 `statement` 寫 **2～3 句**" in luna and "台灣當天的大事要優先" in luna


def test_severe_world_event_outranks_ordinary_news_in_specialized_order():
    """外審 PR #38:只區分「小事」會讓重大事件與一般科學新聞同級,第六條重大事件被擠掉。"""
    import editorial_priority as ep
    pk = {"news": [{"source_item_id": "s", "title": "新型望遠鏡拍到遙遠星系影像"},
                   {"source_item_id": "i", "title": "伊朗軍方宣稱　若與美國爆發新戰事恐比先前更漫長"}]}
    order = [c["source_item_id"] for c in ep.order([{"source_item_id": "s"}, {"source_item_id": "i"}], pk)]
    assert order == ["i", "s"]


def test_world_depth_guard_does_not_count_minor_incidents_as_material():
    """外審 PR #38:2 則重大 + 3 則地方小事,模型只寫 2 條時不應被催著用小事補滿五條。"""
    import analysis_depth
    titles = ["伊朗軍方警告新戰事", "OPEC+維持產量", "阿拉巴馬州球賽外槍響 1死8傷",
              "東部海域地震規模5.6 最大震度3級", "德國生活成本抗議演變成暴力"]
    pk = {"news": [{"source_item_id": f"w{i}", "title": t, "source": "中央社國際"}
                   for i, t in enumerate(titles)]}
    w = {"what": "x", "why_it_matters": "y", "what_next": "z"}
    adv = analysis_depth.depth_advisories({"world_events": [w, w], "top_news_analysis": []}, pk)
    assert not any("world_events" in a for a in adv)
    pk["news"] += [{"source_item_id": f"x{i}", "title": f"美中峰會第{i}輪", "source": "中央社國際"}
                   for i in range(3)]
    adv = analysis_depth.depth_advisories({"world_events": [w, w], "top_news_analysis": []}, pk)
    assert any("world_events" in a for a in adv)          # 重大素材真的夠五則時照催
