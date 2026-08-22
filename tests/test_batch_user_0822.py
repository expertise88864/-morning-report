# -*- coding: utf-8 -*-
"""2026-08-22 使用者回饋批:TAIFEX 資料源、術語解釋、類股加量、在地大巨蛋。

TAIFEX 那條是**實測診斷**出來的:`chips` 的 fill_rate 只有 35%,根因是
`openapi.taifex.com.tw` 同時出了兩個問題 ——
(a) 資料停在 8/19 而 8/20、8/21 都是交易日(日期守衛每天把特徵留空);
(b) `OpenInterestOfLargeTradersFutures` 已改成回 **CSV**,而呼叫端還在
`r.json()`,那條路必然拋例外、fail-safe 回 `{}`。
官網每日報表兩項都有當日資料(實測 8/21 齊全)。
"""
import io
from pathlib import Path

import morning_report as mr
import prompt_profiles as pp


class _Resp:
    def __init__(self, content: bytes, status: int = 200):
        self.content = content
        self.status_code = status
        self.text = content.decode("utf-8-sig", errors="replace")

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        import json
        return json.loads(self.text)


_PCR_CSV = (
    "日期,賣權成交量,買權成交量,買賣權成交量比率%,賣權未平倉量,買權未平倉量,"
    "買賣權未平倉量比率%\n"
    "2026/08/21,358085,323629,110.65,35710,34409,103.78,\n"
    "2026/08/20,182976,163649,111.81,54070,54415,99.37,\n"
).encode("big5hkscs")

_LARGE_CSV = (
    "日期,商品(契約),商品名稱(契約名稱),到期月份(週別),交易人類別,"
    "前五大交易人買方,前五大交易人賣方,前十大交易人買方,前十大交易人賣方,"
    "全市場未沖銷部位數\n"
    "2026/08/20,TX     ,臺股期貨,999999  ,0,75181,55272,82843,75018,112450\n"
    "2026/08/21,TX     ,臺股期貨,999999  ,0,75027,54774,82218,74572,112885\n"
    "2026/08/21,TX     ,臺股期貨,999999  ,1,75027,54774,78809,74572,112885\n"
).encode("big5hkscs")


def test_pcr_prefers_the_site_report_over_the_stale_openapi(monkeypatch):
    """官網有 8/21、OpenAPI 停在 8/19 —— 要用新的那個。"""
    monkeypatch.setattr(mr.requests, "post", lambda *a, **k: _Resp(_PCR_CSV))
    out = mr.fetch_taifex_options_pc_ratio()
    assert out["date"] == "20260821", out
    assert out["pc_oi_ratio"] == 103.78 and out["pc_vol_ratio"] == 110.65


def test_large_traders_prefers_the_site_report(monkeypatch):
    """官網用 999999(所有契約合計)、類別 0=全部 1=特定法人。"""
    monkeypatch.setattr(mr.requests, "post", lambda *a, **k: _Resp(_LARGE_CSV))
    out = mr.fetch_taifex_large_traders()
    assert out["date"] == "20260821", out
    assert out["top10_net"] == 82218 - 74572
    assert out["oi_market"] == 112885
    assert out["spec_top10_net"] == 78809 - 74572


def test_openapi_fallback_parses_csv_not_only_json(monkeypatch):
    """r.json() 那條路在端點改成 CSV 的那天就靜默消失了 —— 備援要兩種都吃。"""
    def _boom(*a, **k):
        raise RuntimeError("site down")
    monkeypatch.setattr(mr.requests, "post", _boom)
    api_csv = (
        "日期,契約,商品名稱(契約名稱),到期月份(週別),交易人類別,"
        "前五大交易人買方數量,前五大交易人賣方數量,前十大交易人買方數量,"
        "前十大交易人賣方數量,全市場未沖銷部位數\n"
        "20260819,TX,臺股期貨,999912,0,73496,55651,81252,74737,109181\n"
    ).encode("utf-8-sig")
    monkeypatch.setattr(mr, "_http_get", lambda *a, **k: _Resp(api_csv))
    out = mr.fetch_taifex_large_traders()
    assert out["date"] == "20260819" and out["top10_net"] == 81252 - 74737


def test_bad_rows_never_become_zero_positions(monkeypatch):
    """壞值回 None 而不是 0 —— 0 會算出一個假的部位。"""
    assert mr._to_int_strict("") is None and mr._to_int_strict("-") is None
    assert mr._to_int_strict("1,234") == 1234
    bad = ("日期,商品(契約),商品名稱,到期月份(週別),交易人類別,a,b,前十大交易人買方,"
           "前十大交易人賣方,全市場未沖銷部位數\n"
           "2026/08/21,TX     ,x,999999  ,0,1,2,,,0\n").encode("big5hkscs")
    monkeypatch.setattr(mr.requests, "post", lambda *a, **k: _Resp(bad))
    monkeypatch.setattr(mr, "_http_get",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no api")))
    assert mr.fetch_taifex_large_traders() == {}


# ---------------------------------------------------------------- 內容面

def test_sector_targets_were_raised():
    """使用者 2026-08-22:科技與其他類股都偏少。兩段各自要有下限。"""
    src = io.open(Path(pp.__file__), encoding="utf-8").read()
    assert "十到十六則為目標" in src
    assert "科技至少六則、科技之外至少五則" in src
    assert "六到十則為目標" not in src, "舊目標還在(兩個數字打架)"


def test_jargon_gets_a_short_gloss_rule():
    """術語第一次出現要用括號解釋(PMI 是什麼)。"""
    src = io.open(Path(pp.__file__), encoding="utf-8").read()
    assert "PMI（採購經理人指數" in src      # prompt 用全形(它自己要求的寫法)
    assert "第一次出現時" in src and "不重複解釋" in src


def test_taichung_dome_is_a_local_topic():
    """在地快訊要有台中大巨蛋;而且信件本身不得出現「使用者要求」這類字眼。"""
    labels = [q[0] for q in mr.LOCAL_NEWS_QUERIES]
    assert "台中大巨蛋" in labels
    q = next(x for x in mr.LOCAL_NEWS_QUERIES if x[0] == "台中大巨蛋")
    assert "台中大巨蛋" in q[1] and "台中巨蛋" in q[1]
    assert len(q) == 3 and q[2] == 3, "沒有設每主題上限"


# ------------------------------------------------ 外審 r1:兩條 CONFIRMED

def test_depth_advisory_matches_the_prompt_targets():
    """r1 P2:prompt 提高到十到十六 + 兩段下限,而 `depth_advisories` 還在
    執行舊契約(總數 6、非科技 1–2)—— 守衛與 prompt 打架時,模型交出六則
    就沒有人會要求它補,信裡的兩段照樣稀薄。"""
    import analysis_depth as ad
    import sys
    sys.path.insert(0, "tests")
    import fixtures_analysis as fx

    def _adv(n_tech, n_other, src_tech=12, src_other=12, gaps=()):
        """**用生產的形狀**:分類走渲染端同一支,而它要求主體在
        packet 的新聞標題裡被指名、產業別從 `tw_universe` 查 ——
        直接把 entities 塞進分析列是繞過 producer 的假資料。
        `src_*` 是**素材面**有幾則(下限只在素材真的夠時才要求)。"""
        obj = fx.valid_analysis()
        rows, pk_news = [], []
        for i in range(max(n_tech, src_tech)):
            pk_news.append({"source_item_id": f"t{i}", "entities": ["2330"],
                            "title": "台積電先進封裝再擴產"})
        for i in range(max(n_other, src_other)):
            pk_news.append({"source_item_id": f"o{i}", "entities": ["2603"],
                            "title": "長榮美西運價連四漲"})
        for i in range(n_tech):
            rows.append({"source_item_id": f"t{i}", "why_it_matters": "x",
                         "direction": "bullish", "materiality": "medium"})
        for i in range(n_other):
            rows.append({"source_item_id": f"o{i}", "why_it_matters": "x",
                         "direction": "bullish", "materiality": "medium"})
        obj["top_news_analysis"] = rows
        # **schema 的形狀**(gap_id / what_is_missing / impact_on_conclusions);
        # 自創欄名的 fixture 會把缺陷釘成通過條件(r3 外審抓到)。
        # gaps 傳的是 (gap_id, 說明) —— 出口是**宣告式代號**,不是關鍵字
        obj["data_gaps"] = [{"gap_id": gid, "what_is_missing": txt,
                             "impact_on_conclusions": "該段條目偏少"}
                            for gid, txt in gaps]
        pk = {"news": pk_news + [{"source_item_id": f"x{i}"} for i in range(30)],
              "market": {},
              "tw_universe": [
                  {"code": "2330", "name": "台積電", "industry": "半導體業"},
                  {"code": "2603", "name": "長榮", "industry": "航運業"}]}
        return "\n".join(ad.depth_advisories(obj, pk))

    # 六則(舊契約認為夠)→ 現在要被點名
    assert "10–16" in _adv(3, 3), "六則仍被當成足夠"
    # 十則但全是科技 → 第九段沒東西
    a = _adv(10, 0)
    assert "科技以外只有 0 則" in a, a
    # 科技不足
    b = _adv(2, 9)
    assert "科技條目只有 2 則" in b, b
    # 兩段都夠就不吵
    c = _adv(6, 5)
    assert "第八段靠它" not in c and "第九段靠它" not in c, c
    # r2 外審:**素材真的沒有那一類**時不得要求做不到的下限(那是逼它湊)
    d = _adv(2, 9, src_tech=2)
    assert "第八段靠它" not in d, d
    # 模型用**指定代號**宣告該段缺料 → 有出口
    e = _adv(2, 9, src_tech=12,
             gaps=((ad.TECH_COVERAGE_GAP, "今日科技新聞多為重複報導"),))
    assert "第八段靠它" not in e, e
    # r4 外審:**無關的缺口只要提到「科技」就關掉建議** = 守衛等於不存在
    f = _adv(2, 9, src_tech=12,
             gaps=(("gap:other:chips", "缺科技類股法人買賣超資料"),))
    assert "第八段靠它" in f, f


def test_site_to_openapi_fallback_is_recorded(monkeypatch):
    """r1 P3:退到已知落後的來源之後,manifest 看起來仍然健康 ——
    那正是這批要修的 35% 的樣子。"""
    import run_quality as rq

    def _boom(*a, **k):
        raise RuntimeError("site down")
    monkeypatch.setattr(mr.requests, "post", _boom)
    monkeypatch.setattr(mr, "_http_get",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("api down")))
    before = len(mr._DEGRADED_STEPS)
    mr.fetch_taifex_options_pc_ratio()
    mr.fetch_taifex_large_traders()
    got = mr._DEGRADED_STEPS[before:]
    assert "chips:pcr_site_fallback" in got and "chips:large_site_fallback" in got
    # 兩個標籤都要註冊,否則自己變成 unknown_degradation
    for tag in ("chips:pcr_site_fallback", "chips:large_site_fallback"):
        assert tag in rq.KNOWN_DEGRADED, tag
    # 去重:同一班第二次退回不再重複記
    n = len(mr._DEGRADED_STEPS)
    mr.fetch_taifex_options_pc_ratio()
    assert mr._DEGRADED_STEPS.count("chips:pcr_site_fallback") == \
        got.count("chips:pcr_site_fallback"), "沒有去重"
    assert len(mr._DEGRADED_STEPS) <= n + 1


def test_empty_site_report_also_records_the_fallback(monkeypatch):
    """r2 外審 P3:HTTP 200 但報表空/欄位漂移時 helper 回 `{}` 而不是拋 ——
    只在 except 記標籤的話,**最現實的那種失敗**反而沒有痕跡。"""
    empty = "日期,賣權成交量,買權成交量\n".encode("big5hkscs")
    monkeypatch.setattr(mr.requests, "post", lambda *a, **k: _Resp(empty))
    monkeypatch.setattr(mr, "_http_get",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("api")))
    before = len(mr._DEGRADED_STEPS)
    mr.fetch_taifex_options_pc_ratio()
    assert "chips:pcr_site_fallback" in mr._DEGRADED_STEPS[before:]
    before = len(mr._DEGRADED_STEPS)
    mr.fetch_taifex_large_traders()
    assert "chips:large_site_fallback" in mr._DEGRADED_STEPS[before:]


def test_the_gap_exit_is_declared_in_the_prompt():
    """出口只在程式裡認得、prompt 沒說 = 模型永遠不會用(和欄名寫錯同一種
    無效出口)。代號兩邊必須一致。"""
    import analysis_depth as ad
    src = io.open(Path(pp.__file__), encoding="utf-8").read()
    assert ad.TECH_COVERAGE_GAP in src, "科技缺口代號沒寫進 prompt"
    assert ad.SECTOR_COVERAGE_GAP in src, "其他類股缺口代號沒寫進 prompt"
