# -*- coding: utf-8 -*-
"""**宣告了一個命名空間,不等於它生得出 ID。**

2026-08-08 生產:特化分析連續多日作廢、退回舊路徑。manifest 記著五條
`引用了不存在的證據 ID`,看起來像模型幻覺 —— 其實三條都是程式這邊的問題:

  * `calibration:` 宣告在 prompt 裡、校準表也放進 prompt 了,而它是**一整塊
    markdown 字串**。攤平不出葉節點,於是 registry 底下**一個 `calibration:`
    開頭的 ID 都沒有**;唯一那項的鍵叫 `calibration`(沒有冒號),
    落在所有已宣告命名空間之外,模型不可能猜到。
  * `prediction:` 的說明寫「2330 **與加權**的開盤預測」,而加權指數的預測
    其實在 `market:TAIEX_PRED.*`。模型於是造出 `prediction:TAIEX.pred_open`
    與 `prediction:2330.mid`(以為要帶標的段)。

**說明錯了,模型就會照著錯的猜。** 而先前的測試量不到這件事,因為
`test_evidence_registry` 餵 calibration 的是 `{"brier": 0.21}` —— 一個
**生產從來不會產生的形狀**。那條斷言「模型校準沒有引用對象」自第十八輪
起一路是綠的,而生產那個命名空間始終是空的。
"""
import re

import evidence_namespaces as ns
import evidence_packet as ep
import fixtures_analysis as fx
import news_memory

#: 生產形狀:每一類輸入都**照 `morning_report` 真的傳的樣子**給。
#: 用簡化形狀量「命名空間有沒有實現」,量到的是 fixture 不是生產。
_QUOTES = {
    "NEWS_MEMORY": news_memory.observations([
        {"title": "台積電法說會下週登場", "entities": ["台積電"],
         "summary": "先前報導關注資本支出", "source": "經濟日報",
         "link": "https://example.com/earlier-earnings", "published": "2026-08-01T06:00+08:00"}
    ], "2026-08-01T07:00+08:00", sanitize=str)[0],
    "QQQ": {"change_pct": 1.76, "close": 500.0},
    "TAIFEX_OI": {"foreign_oi_net": -87911},
    "TAIEX_PRED": {"pred_open": 44512.0, "pred_pct": 0.65},
    "MACRO": {"10Y": {"close": 4.66, "prev_close": 4.42}},
    "SECTOR_HEAT": {"ranked": ["半導體業"],
                    "sectors": {"半導體業": {"median_pct": -1.2,
                                          "leaders": [{"code": "2330",
                                                       "name": "台積電",
                                                       "pct": 0.2}]}}},
    # 只有百分比與檔數 —— 代號與股數不得進 packet(`portfolio_summary`)。
    "PORTFOLIO_ACTUAL": {"p1": {"gain_pct": 1.4, "n_holdings": 3},
                         "p2": {"gain_pct": -0.6, "n_holdings": 2}},
}

#: 生產的校準資料形狀(`build_historical_calibration` 的回傳)。
_CALIBRATION = {
    "n_days": 7, "mean_delta_pct": -0.12, "mean_abs_delta_pct": 0.83,
    "max_abs_delta_pct": 1.9, "note": "",
    "by_date": {"08/07": {"tsm_pct": 0.44, "tw_open_pct": 0.11,
                          "delta_pct": -0.33},
                "08/06": {"tsm_pct": -1.2, "tw_open_pct": -0.4,
                          "delta_pct": 0.8}},
}


def _news():
    """生產的新聞帶 `numeric_facts`(抽取器產出)—— `fact:` 由它生成。"""
    out = []
    for i, n in enumerate(fx.news()):
        n = dict(n)
        n["numeric_facts"] = [{"value": 32.9, "unit": "%",
                               "quote": "7 月出口年增 32.9%"}]
        n.setdefault("source_name", f"來源{i}")
        out.append(n)
    return out


def _packet(calibration=None):
    # **生產的鍵**:`calc_00662_fair_value` 回 `fair_price`/`implied_change_pct`,
    # 沒有 `fair_value` —— fixture 用錯名字的話,「範例都解析得到」這條守衛
    # 就會替一個 prompt 教得出來、生產卻沒有的 ID 背書(2026-08-11 那次
    # 正是這個形狀)。
    return ep.build(_QUOTES, {"fair_price": 123.17, "premium_pct": -1.1,
                              "implied_change_pct": 1.3},
                    # **這裡要放生產真的會產生的欄位。** 先前是
                    # `{"pred_open": …, "pred_pct": …}` —— `calc_2330_predictions`
                    # 從來不回那兩個鍵,於是這一整組守衛替「prompt 教了一個
                    # 不存在的 ID」背書,而生產在 2026-08-10 的 current-head
                    # 驗收上被那個 ID 擋掉整份特化分析(下面有守衛盯著)。
                    {"mid": 2372.51, "last_2330": 2370.0,
                     "model1_1to1": 2375.0, "tsm_pct": 0.44},
                    _news(), [{"code": "2330", "pct": 0.2}],
                    _CALIBRATION if calibration is None else calibration,
                    as_of="2026-08-08T06:00",
                    target_session_date="2026-08-08", sanitize=str)


# ------------------------------------------------------------ 核心守衛

def test_every_declared_namespace_can_actually_be_cited():
    """**這條測試存在的理由**:它會在上線第一天就抓到 2026-08-08 那次。

    prompt 告訴模型有這些命名空間;資料齊全時每一個都必須至少生得出
    一個 ID。生不出來的話,模型談那件事只有兩條路 —— 不引用(被擋)、
    或拿一則新聞去頂(形式合法、語意錯誤)。
    """
    missing = ep.unrealizable_namespaces(_packet())
    assert missing == set(), f"宣告了卻生不出任何 ID:{sorted(missing)}"


def test_no_evidence_id_falls_outside_every_declared_namespace():
    """**那個 bug 的機制**:`_entries` 在「整個區塊就是一個純量」時
    做 `root.rstrip(':')`,把命名空間的冒號剝掉。生出來的 `calibration`
    既不是新聞 ID、也不屬於任何前綴 —— 它存在,但沒有人引用得到。
    """
    ids = ep.evidence_ids(_packet())
    prefixes = tuple(p for p, _, _ in ns.NAMESPACES if p.endswith(":"))
    news_ids = {str(n["source_item_id"]) for n in _news()}
    orphans = {i for i in ids
               if not str(i).startswith(prefixes) and i not in news_ids}
    assert orphans == set(), f"落在所有命名空間之外的 ID:{sorted(orphans)}"


def test_the_namespace_descriptions_point_at_ids_that_exist():
    """**說明本身要可驗證。** 描述裡舉的每個例子都必須真的解析得到 ——
    `prediction:` 先前寫「2330 與加權」,而加權根本不在這個前綴底下,
    模型就照著那句話造了 `prediction:TAIEX.pred_open`。
    """
    ids = {str(i) for i in ep.evidence_ids(_packet())}
    checked = 0
    for prefix, desc, _ in ns.NAMESPACES:
        for ex in re.findall(r"`([a-z_]+:[^`]+)`", desc):
            # `<MM/DD>` 是佔位符、`*` 是萬用字元
            pat = re.escape(ex).replace(r"\*", ".*")
            pat = re.sub(r"<[^>]*>", "[^.]+", pat.replace(r"\<", "<")
                         .replace(r"\>", ">"))
            assert any(re.fullmatch(pat, i) for i in ids), \
                f"{prefix} 的說明舉例 `{ex}` 解析不到任何 ID"
            checked += 1
    assert checked >= 3, "說明裡根本沒有舉例,這條測試等於沒跑"


def _production_body(func_name: str) -> str:
    import io as _io
    from pathlib import Path
    src = _io.open(Path(__file__).resolve().parents[1] / "morning_report.py",
                   encoding="utf-8").read()
    i = src.index(f"def {func_name}")
    return src[i:src.index(chr(10) + "def ", i + 10)]


def test_valuation_examples_exist_in_the_production_calculation():
    """**同一課學三次就夠了**(2026-08-11):`valuation:` 的說明原本只寫
    「00662 估值」—— 沒說不帶標的段、也沒舉真實欄位,模型於是寫出
    `valuation:00662.implied_change_pct`(欄位名其實是對的,多的是標的段)。
    範例釘在 `calc_00662_fair_value` 的原始碼上,fixture 造不出來。"""
    import re
    import evidence_namespaces as ns
    body = _production_body("calc_00662_fair_value")
    fields = {ex.split(":", 1)[1] for _, desc, _ in ns.NAMESPACES
              for ex in re.findall(r"`(valuation:[^`]+)`", desc)}
    assert fields, "說明裡沒有 valuation 範例 —— 這條測試等於沒跑"
    missing = sorted(f for f in fields if f'"{f}"' not in body)
    assert not missing, f"prompt 教的欄位生產不會產生:{missing}"


def test_prediction_examples_exist_in_the_production_calculation():
    """**fixture 不能替 prompt 背書**(2026-08-10 current-head 生產驗收)。

    上一版的 fixture 自己造了 `pred_open`,於是上面那條守衛綠燈,
    而生產的 `calc_2330_predictions` 根本不回那個鍵 —— 模型照著 prompt
    的範例寫,引用被判不存在、整份特化分析作廢退回 legacy。
    這條把範例釘在**生產的計算函式**上,fixture 造不出來。
    """
    import io as _io
    from pathlib import Path
    src = _io.open(Path(__file__).resolve().parents[1] / "morning_report.py",
                   encoding="utf-8").read()
    i = src.index("def calc_2330_predictions")
    body = src[i:src.index(chr(10) + "def ", i + 10)]
    fields = {ex.split(":", 1)[1] for _, desc, _ in ns.NAMESPACES
              for ex in re.findall(r"`(prediction:[^`]+)`", desc)}
    assert fields, "說明裡沒有 prediction 範例 —— 這條測試等於沒跑"
    missing = sorted(f for f in fields if f'"{f}"' not in body)
    assert not missing, f"prompt 教的欄位生產不會產生:{missing}"


# ------------------------------------------------------------ 生產那次的三條

def test_a_markdown_table_realizes_nothing():
    """**這是生產真正發生的事,而且是兩個機制。**

    正常日:整張表數百字,超過 `_MAX_STRING_LEAF` —— 不是葉子,
    `calibration:` 底下**什麼都不生**。prompt 照樣宣告它合法。
    """
    table = ("近 7 個交易日 TSM 漲跌 vs 2330 開盤對照：\n"
             "  08/07：TSM 收盤 +0.44% → 2330 開盤 +0.11%（偏離 -0.33%）")
    assert len(table) > 60, "反例要落在「長字串不是葉子」那個機制上"
    ids = ep.evidence_ids(_packet(calibration=table))
    assert not [i for i in ids if str(i).startswith("calibration:")]
    assert "calibration:" in ep.unrealizable_namespaces(_packet(calibration=table))


def test_a_short_degraded_note_does_not_mint_an_orphan_id():
    """降級日的第二個機制:「（歷史資料不足，無法生成校準表）」**短到
    算純量葉子**,而根純量的路徑是空字串 —— 先前 `_entries` 對它做
    `root.rstrip(':')`,生出一個叫 `calibration`(沒有冒號)的 ID,
    落在所有已宣告命名空間之外。**孤兒只在短字串形狀下出現**,
    上一條的長表量不到這一條的機制(反例要只靠被測的規則分勝負)。
    """
    note = "（歷史資料不足，無法生成校準表）"
    assert len(note) <= 60, "反例要落在「根純量」那個機制上"
    ids = ep.evidence_ids(_packet(calibration=note))
    assert "calibration" not in ids, "命名空間的冒號又被剝掉了"
    assert not [i for i in ids if str(i).startswith("calibration:")]


def test_the_number_the_model_asked_for_now_exists():
    """模型當時引的是 `calibration:tsm2330_7d_absdev` —— 名字不同,
    但它要的東西(近 N 日絕對誤差)現在真的有一個 ID。"""
    ids = ep.evidence_ids(_packet())
    assert "calibration:mean_abs_delta_pct" in ids
    assert "calibration:by_date.08/07.delta_pct" in ids


def test_the_weighted_index_prediction_is_citable_somewhere():
    """`prediction:TAIEX.pred_open` 不存在是對的 —— 但加權預測是信件
    首屏四個數字之一,它**必須**引用得到,否則模型談它只能不引用。"""
    ids = ep.evidence_ids(_packet())
    assert "market:TAIEX_PRED.pred_open" in ids
    assert "market:TAIEX_PRED.pred_pct" in ids


# ------------------------------------------------------------ 舊路徑沒被弄壞

def test_the_rendered_table_still_carries_every_number():
    """結構化之後,legacy prompt 那段文字仍要一字不差地說得出同樣的數字 ——
    這次改動不得順手改變舊路徑看到的內容。"""
    import morning_report as mr
    txt = mr.render_calibration_table(_CALIBRATION)
    for frag in ("近 7 個交易日", "08/07", "+0.44%", "+0.11%", "-0.33%",
                 "平均偏離 = -0.12%", "平均絕對偏離 = 0.83%"):
        assert frag in txt, (frag, txt)


def test_production_hands_the_structured_form_to_the_packet():
    """**上面每一條都可以在生產改回傳字串之後照樣綠** —— 它們驗的是
    `ep.build` 收到 dict 時的行為,而 2026-08-08 的缺陷正是「生產傳的是
    另一種形狀」。這個 repo 記過這個形狀:守衛不得靠遺忘失效。

    因此這裡盯生產的兩個呼叫點:證據包收**結構化**那一份,
    legacy prompt 收**渲染後**那一份。
    """
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "morning_report.py").read_text(encoding="utf-8")
    # 證據包:直接收 calibration 這個變數(= 結構化的回傳)
    i = src.index("_packet = _ep.build(")
    assert "calibration,\n" in src[i:i + 260], src[i:i + 260]
    assert "render_calibration_table" not in src[i:i + 260], \
        "證據包收到的是渲染後的表 —— 那攤平不出任何葉節點"
    # legacy prompt:收渲染後的字串
    j = src.index("prompt = _build_prompt(")
    assert "render_calibration_table(calibration)" in src[j:j + 240], \
        src[j:j + 240]


def test_a_degraded_day_still_says_why():
    """取不到資料時仍回 dict 並說明原因 —— **命名空間不會整個消失**,
    否則降級日的引用檢查又會開始亂擋。"""
    import morning_report as mr
    empty = mr.build_historical_calibration(None, days=7)
    assert isinstance(empty, dict) and empty["n_days"] == 0
    assert "不足" in empty["note"]
    assert "不足" in mr.render_calibration_table(empty)
    ids = ep.evidence_ids(_packet(calibration=empty))
    assert "calibration:n_days" in ids and "calibration:note" in ids


# ===== 2026-08-10 current-head 生產驗收:引用被判不存在的兩個根因 =====

def _rich_packet():
    """夠豐富的代表性 packet:每個命名空間都有資料可攤。"""
    import evidence_packet as ep
    import news_normalize as nn
    news = nn.normalize_news([{
        "source_item_id": "n1",
        "title": "台積電 7 月營收 3200 億元 年增 24%",
        "summary": "台積電公布 7 月營收 3200 億元,年增 24%",
        "entities": ["台積電"], "source": "經濟日報",
        "published": "2026-08-10T01:00:00Z", "link": "http://x"}])[0]
    return ep.build(
        {"QQQ": {"change_pct": 1.2}, "TAIEX_PRED": {"open": 44474}},
        # **生產的鍵**(`calc_00662_fair_value` 回的是 `fair_price`,
        # 不是 `fair_value` —— 我修 `pred_open` 那個 bug 時差點再犯一次)
        {"fair_price": 123.4, "premium_pct": -1.1,
         "implied_change_pct": 1.3},
        {"mid": 2372.5, "last_2330": 2370.0, "model1_1to1": 2375.0},
        news, [], {"mean_abs_delta_pct": 1.2},
        as_of="2026-08-10 06:00", target_session_date="2026-08-10",
        sanitize=lambda s, *a: s)


def test_every_example_id_in_the_prompt_actually_exists():
    """**範例 ID 自己要存在**(2026-08-10 current-head 生產驗收):
    說明裡寫著 `prediction:pred_open`,而那個欄位從來沒有被產生過
    (真正的鍵是 `mid`/`last_2330`/`model1_1to1`)。模型照著範例寫,
    引用全被判不存在、整份特化分析作廢退回 legacy。
    通配符(`*`)與佔位符(`<…>`)不算範例。"""
    import re
    import evidence_namespaces as ns
    import evidence_packet as ep
    real = ep.evidence_ids(_rich_packet())
    examples = {m for _, desc, _ in ns.NAMESPACES
                for m in re.findall(r"`([^`]+)`", desc)
                if ":" in m and "*" not in m and "<" not in m}
    assert examples, "說明裡一個範例都沒有 —— 這條守衛會真空通過"
    missing = sorted(e for e in examples if e not in real)
    assert not missing, f"prompt 教了不存在的 ID:{missing}"


def test_numeric_facts_carry_the_id_we_issued():
    """**ID 由我們發,不讓模型組**:prompt 只說「`fact:<新聞ID>.<序號>`」,
    而 packet 給的是沒有編號的清單 —— 模型得自己猜從 0 還是從 1 起算,
    實測寫出 `fact:nfe44152db8e.1`(那則的合法 ID 是 `.0`)。"""
    import evidence_packet as ep
    pk = _rich_packet()
    facts = pk["news"][0].get("numeric_facts") or []
    assert facts, "前提:這則新聞抽得出數字事實"
    ids = [f.get("evidence_id") for f in facts]
    assert all(ids), f"有事實沒有帶 ID:{facts}"
    assert ids[0] == "fact:n1.0"
    real = ep.evidence_ids(pk)
    assert set(ids) <= real, sorted(set(ids) - real)


def test_the_registry_reads_the_issued_id_not_its_position():
    """**派號的地方只有一個**:registry 讀 packet 帶的那一個,
    兩邊各算一次的話,去重或排序一動就對不上。"""
    import evidence_registry as er
    pk = {"news": [{"source_item_id": "n9", "title": "t", "published": "",
                    "numeric_facts": [
                        {"value": 1, "unit": "%", "quote": "q",
                         "evidence_id": "fact:n9.7"}]}]}
    reg = er.registry(pk)
    assert "fact:n9.7" in reg and "fact:n9.0" not in reg
    # 舊 packet(沒有帶 ID)仍然退回位置推導
    pk2 = {"news": [{"source_item_id": "n9", "title": "t", "published": "",
                     "numeric_facts": [{"value": 1, "unit": "%",
                                        "quote": "q"}]}]}
    assert "fact:n9.0" in er.registry(pk2)


# ===== 2026-08-11:今天沒有的命名空間,不要邀請模型引用 =====

def test_the_packet_says_which_namespaces_are_empty_today():
    """**prompt 講了模型拿不到的東西**(`derived:tsmc_capex_twd_9503`,
    2026-08-11 生產)——那是 `pred_open`/`fact:<序號>`/`valuation:` 之後的
    第四次。靜態清單說「本報有哪幾種證據」,packet 說「今天哪幾種是空的」。"""
    pk = _rich_packet()
    empty = pk["unavailable_namespaces"]
    assert "derived:" in empty, empty        # 這一天確實沒有 derived:
    # 生得出 ID 的不得列進去(列了等於叫模型別引用它真的有的東西)
    ids = ep.evidence_ids(pk)
    for p in empty:
        assert not any(str(i).startswith(p) for i in ids), p


def test_the_rule_lives_in_the_stable_prefix_and_the_data_in_the_packet():
    """**前綴要逐位元組相同才打得中 prompt caching**(cached input 便宜
    十倍)—— 當日資訊放前綴會把它整個關掉。規矩走前綴、資料走 packet,
    與 `required_disclosures` 同一個模式。"""
    import prompt_profiles as pp
    a = pp.build_luna_bundle(_rich_packet())["developer_instructions"]
    b = pp.build_luna_bundle(ep.build(
        {}, {}, {}, [], [], {}, as_of="z", target_session_date="w",
        sanitize=lambda s, *x: s))["developer_instructions"]
    assert a == b == pp.LUNA_DEVELOPER_INSTRUCTIONS
    assert "unavailable_namespaces" in a, "規則沒進前綴 = 模型不知道那一格"


def test_a_day_without_numeric_facts_says_so():
    """**`fact:` 不豁免**(外審 r1):我上一版把它當「骨幹」放行,
    而那正好是這個功能要關掉的洞 —— 沒有任何數字事實的日子,模型照樣
    會寫 `fact:n3.0`。靜態清單仍然講它是什麼(規則),
    這一格說的是今天有沒有(資料)。"""
    pk = ep.build({}, {}, {}, [], [], {}, as_of="z", target_session_date="w",
                  sanitize=lambda s, *x: s)
    assert "fact:" in pk["unavailable_namespaces"]
    assert "fact:" in ns.prompt_lines(), "規則本身不得被拿掉"
    # 有數字事實的日子不列(不要為了列而列)
    rich = _rich_packet()
    assert "fact:" not in rich["unavailable_namespaces"]
