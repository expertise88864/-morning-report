# -*- coding: utf-8 -*-
"""確定性整合測試:用固定素材跑完整條新聞管線,驗**跨模組的契約**。

批#70。單元測試各自驗自己那一塊,而本專案這個 session 反覆出現的失敗
**全部發生在模組交界**:
  - 事件聚合把兩件不同的事併成一則(批#64)
  - 軌跡點的 link 539/539 全空,因為 `link` 從未被保留到事件裡(批#57 r1)
  - 線索身分含標題 digest,續報永遠接不回去(批#67)
  - 交叉驗證數把同一個 Google News 的不同查詢當成獨立來源(批#64)

這一支刻意**不連網、不呼叫 LLM**,只跑確定性路徑:
    原始素材 → extract_structured_events → apply_event_timeline
             → story_ledger.update_ledger → assign_event_sections
並斷言那些「每個模組各自看都對、串起來才錯」的性質。

素材取自真實語料的標題形狀(公開新聞標題),不含任何持股資訊。
"""
import datetime as dt
import hashlib

import morning_report as mr
import news_events as ne
import story_ledger as sl

NOW = dt.datetime(2026, 7, 30, tzinfo=dt.timezone.utc)
TODAY = "2026-07-30"
VOCAB = {"2330": "台積電", "2317": "鴻海", "2382": "廣達", "2884": "玉山金"}
#: 生產的呼叫形狀(Commit C 主體信任層級後,抽取一定帶詞彙表;
#: `mentions_entity` 的值要是**別名序列**,字串會被逐字元迭代)。
KNOWN = {k: (v,) for k, v in VOCAB.items()}


def _item(title, source, **kw):
    # link 用 **穩定**的摘要,不用 `hash()` —— str 的 hash 有 PYTHONHASHSEED
    # 隨機化,各次執行不同,對一個宣稱「確定性」的套件是自相矛盾的。
    digest = hashlib.sha1(title.encode("utf-8")).hexdigest()[:8]
    base = {"title": title, "source": source,
            "published": "2026-07-30T01:00:00+00:00",
            "link": f"https://example.com/{digest}"}
    base.update(kw)
    return base


#: 一份小而有代表性的當日素材。刻意包含四種在交界處出過事的形狀:
#:   (a) 同一事件跨多家媒體(其中兩家同屬 Google News 家族)
#:   (b) 同一公司同型別的**兩件不同**事件
#:   (c) 同一公司連續兩期的制式公告(期別必須分集)
#:   (d) 大盤總結(不得開線索)
NEWS = [
    _item("廣達砸197億元買友達華亞廠 AI伺服器產能再擴張", "經濟日報財經",
          entity="2382", event_type="orders", direction=1),
    _item("AI伺服器ODM廠砸錢擴產 廣達斥資197億元買友達廠房", "Google-半導體",
          entity="2382", event_type="orders", direction=1),
    _item("廣達買友達華亞廠 擴充AI伺服器產能", "類股-傳產-台股",
          entity="2382", event_type="orders", direction=1),
    _item("台積電獲蘋果2奈米大單", "中央社財經",
          entity="2330", event_type="orders", direction=1),
    _item("台積電獲輝達CoWoS追加訂單", "自由財經",
          entity="2330", event_type="orders", direction=1),
    _item("公告本公司115年6月份自結合併營收", "MOPS",
          entity="2884", event_type="revenue_growth", direction=0,
          summary="玉山金控自結合併營收"),
    _item("台股收跌1195.97點 電子權值股領跌", "經濟日報財經"),
]


def _run_pipeline(news, today=TODAY, ledger=None):
    events = mr.extract_structured_events(news, [], None, NOW,
                                          known_names=KNOWN)
    events = mr.apply_event_timeline([], events)
    led = sl.update_ledger(ledger or [], events, today, VOCAB)
    sections = mr.assign_event_sections(events, None)
    return events, led, sections


def test_distinct_events_for_one_company_are_never_merged():
    """交界契約一:同一家公司同型別的**兩件不同**事件不得互吞。

    這是不可回復的錯誤方向——被吃掉的那則會靜默消失,活下來的還會宣稱
    自己被多來源交叉驗證(批#64 實測)。
    """
    events, _, _ = _run_pipeline(NEWS)
    tsmc = [e for e in events if e["entity"] == "2330"]
    assert len(tsmc) == 2, f"兩件不同訂單被互吞:{[e['title'] for e in tsmc]}"


def test_known_gap_cross_outlet_paraphrase_still_yields_separate_events():
    """**特徵化測試:記錄一個已知缺口,不是宣稱它是對的。**

    三則講同一件事(廣達買友達華亞廠)的跨媒體報導,在**事件層**沒有收斂
    (各自成為一則事件),只有在**線索層**被收成一條敘事。

    為什麼不在這裡順手調門檻——量化過,這一組**無法用單一相似度門檻切開**:
      - 該合併的廣達跨媒體改寫:max 分母 0.46~0.52
      - **不該**合併的 MOPS 包含關係(「增資發行新股」vs「⋯之資金用途變更」):0.68
      假陽性的分數比真陽性還高。
    改用 min 分母 + 包含關係偵測後,「富邦金 總經理選任 vs 董事長選任」(0.857)
    又會被誤併——那是語意差異不是結構差異,門檻切不出來。

    批#64 目前的設定刻意偏保守:**寧可留兩則重複,不願消滅一則真事件**
    (前者可回復、後者不可)。缺口的實際代價是 prompt 裡有近似重複的素材,
    而線索層已經把它們收成一條、prompt 也另有不重複規則。

    也記一下批#64 校準的**選擇偏差**:當時拿「已經合併成功的 93 則」當語料,
    而那些之所以在樣本裡正是因為標題完全相同 —— 真正的改寫型重複從來沒進過
    那份樣本,所以「跨來源重複幾乎都是完全相同的轉載」這個結論被高估了。

    這條測試會在有人真的修好時失敗,那時請**刻意**更新它,而不是默默放寬。
    """
    events, led, _ = _run_pipeline(NEWS)
    quanta_events = [e for e in events if e["entity"] == "2382"]
    quanta_stories = [s for s in led if s.get("entity") == "2382"]
    assert len(quanta_events) == 3, "事件層行為改變了 —— 請確認是刻意的"
    assert len(quanta_stories) == 1, "線索層必須把它們收成同一條敘事"


def test_corroboration_counts_publishers_not_feed_labels():
    """交界契約二:交叉驗證數必須是**獨立發布者**。

    素材裡兩則同一事件的報導分別來自 `Google-半導體` 與 `類股-傳產-台股`,
    兩者都是 news.google.com —— 舊碼會把它們當成兩個獨立來源。
    """
    assert mr._source_family("Google-半導體") == mr._source_family("類股-傳產-台股")
    assert mr._source_family("經濟日報財經") != mr._source_family("Google-半導體")
    events, _, _ = _run_pipeline(NEWS)
    for e in events:
        fams = {mr._source_family(x) for x in (e.get("sources") or []) if x}
        assert e["corroboration_count"] <= max(1, len(fams)),             f"交叉驗證數超過獨立發布者數:{e['title']}"


def test_every_timeline_point_has_a_usable_link():
    """交界契約三:軌跡點的 `link` 必須真的傳得到底。

    批#57 r1 的實害:`link` 從未被保留到事件裡 → **生產帳本 539/539 個軌跡點的
    `l` 都是空的**,「可點回原文」從第一天就沒生效;而當時的測試直接把 link
    餵給 `update_ledger`,繞過了正規化步驟——驗的是測試蓋的東西,不是生產送進來的。
    """
    _, led, _ = _run_pipeline(NEWS)
    points = [p for s in led for p in (s.get("timeline") or [])]
    assert points, "完全沒有軌跡點"
    assert all(str(p.get("l", "")).startswith(("http://", "https://"))
               for p in points), f"軌跡點的 link 空了:{[p.get('l') for p in points]}"


def test_market_wrap_does_not_open_a_story():
    """交界契約四:大盤總結沒有單一主體,掛到任一個股上會產生無意義的軌跡。"""
    _, led, _ = _run_pipeline(NEWS)
    assert not [s for s in led if "台股收跌" in str(s.get("headline"))]


def test_follow_up_next_day_extends_the_same_story():
    """交界契約五:**縱向連貫**。續報要接回既有線索,而不是每天開一條新的。

    診斷依據:真實 state 裡 1502 條線索有 1485 條只有 1 次更新、
    沒有任何一條累積到 3 個軌跡點(批#67)。
    """
    _, led, _ = _run_pipeline(NEWS)
    before = len(led)
    led2 = sl.update_ledger(led, mr.apply_event_timeline([], mr.extract_structured_events(
        [_item("廣達買友達華亞廠案完成交割 AI伺服器產能到位", "中央社財經",
               entity="2382", event_type="orders", direction=1,
               published="2026-07-31T01:00:00+00:00")], [], None,
        NOW + dt.timedelta(days=1), known_names=KNOWN)), "2026-07-31", VOCAB)
    assert len(led2) == before, "續報又開了一條新線索"
    quanta = next(s for s in led2 if s.get("entity") == "2382")
    assert len(quanta["timeline"]) >= 2, "軌跡沒有累積"


def test_consecutive_periods_stay_separate_episodes():
    """交界契約六:期別型事件跨期必須分集——這與契約五(要接得起來)方向相反,
    兩者由同一段程式碼決定,所以必須放在一起驗。"""
    _, led, _ = _run_pipeline(NEWS)
    led2 = sl.update_ledger(led, mr.apply_event_timeline([], mr.extract_structured_events(
        [_item("公告本公司115年7月份自結合併營收", "MOPS", entity="2884",
               event_type="revenue_growth", direction=0,
               summary="玉山金控自結合併營收",
               published="2026-08-06T01:00:00+00:00")], [], None,
        NOW + dt.timedelta(days=7), known_names=KNOWN)), "2026-08-06", VOCAB)
    keys = {s["key"] for s in led2 if s.get("entity") == "2884"}
    assert len(keys) == 2, f"六月與七月營收黏成同一集:{keys}"


def test_section_assignment_gives_each_event_exactly_one_home():
    """交界契約七:同一件事只在一個段落深寫。

    2026-07-29 實信的實害:「費半 -4.49%」出現在**四個段落**。
    """
    events, _, sections = _run_pipeline(NEWS)
    titles = [s["title"] for s in sections]
    assert len(titles) == len(set(titles)), f"同一事件被指派到多個段落:{titles}"
    assert all(s.get("section") for s in sections), "有事件沒有主段落"


def test_pipeline_is_stable_across_reruns():
    """交界契約八:同樣的輸入跑兩次結果必須相同。

    帳本會被持久化,workflow 手動重跑(或補跑)時會拿同一批事件再跑一次;
    不穩定就代表 state 會隨重跑漂移。
    """
    a_events, a_led, a_sec = _run_pipeline(NEWS)
    b_events, b_led, b_sec = _run_pipeline(NEWS)
    assert [e["title"] for e in a_events] == [e["title"] for e in b_events]
    assert [s["key"] for s in a_led] == [s["key"] for s in b_led]
    assert a_sec == b_sec
    # 對同一份帳本重跑同一批事件,不得重複推進狀態
    again = sl.update_ledger(a_led, a_events, TODAY, VOCAB)
    assert [s["updates"] for s in again] == [s["updates"] for s in a_led]


def test_event_fields_stay_within_their_declared_types():
    """交界契約九:送進 LLM prompt 的事件欄位型別必須乾淨。

    prompt 的清洗層(`_sanitize_event_for_prompt`)只放行數值型別的數值欄位,
    上游若把字串塞進 surprise_score,該欄會被整個剔除——那是靜默失分。
    """
    events, _, _ = _run_pipeline(NEWS)
    for e in events:
        for field in ("direction", "confidence", "surprise_score",
                      "freshness_weight", "quality_score", "corroboration_count"):
            v = e.get(field)
            assert v is None or (isinstance(v, (int, float))
                                 and not isinstance(v, bool)), \
                f"{field} 型別不對:{v!r}"
        assert isinstance(e.get("published"), str)
        dt.datetime.fromisoformat(e["published"].replace("Z", "+00:00"))


def test_llm_events_that_pass_schema_actually_reach_the_output():
    """交界契約十:通過 schema 的 LLM 事件必須真的活到輸出。

    批#68 量到「LLM 抽取器在生產從未產出任何事件」之後,這條守住的是
    **管線這一側**:就算模型有回,聚合階段也可能把它整批吃掉,而那同樣
    等於沒產出。這裡給一個確定性素材裡沒有的主體,它應該以自己的身分存活。
    """
    llm = [{"entity": "3231", "event_type": "orders", "direction": 1,
            "title": "緯創獲AI機櫃大單 產能滿載", "summary": "x",
            "confidence": 0.6, "lifecycle": "confirmed",
            "published": "2026-07-30T01:00:00+00:00"}]
    valid, dropped = ne._validate_llm_events(llm)
    assert dropped == 0 and valid, "測試素材自己就過不了 schema,對照組無效"
    out = mr.extract_structured_events(NEWS, [], valid, NOW, known_names=KNOWN)
    survived = [e for e in out if e.get("source") == "LLM extractor"]
    assert len(survived) == 1, "通過 schema 的 LLM 事件沒有活到輸出"
