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
    assert "十五到二十則為目標" in src
    assert "科技至少八則、科技之外至少七則" in src
    assert "六到十則為目標" not in src, "舊目標還在(兩個數字打架)"
    # **prompt 的數字要與守衛的常數是同一組**(2026-08-24):兩邊各養一份的
    # 話,守衛催的下限與 prompt 要求的下限可以不一樣 —— 模型照 prompt 交
    # 了卷,守衛還在催;或反過來,守衛不催而信裡就是偏少(那正是使用者
    # 08/24 又反映一次的形狀:7+6=13 剛好卡在舊目標上,沒有人會催它)。
    import analysis_depth as ad
    _cn = "零一二三四五六七八九十"

    def _zh(n):                          # 8 → 八、15 → 十五、20 → 二十
        if n < 10:
            return _cn[n]
        tens, ones = divmod(n, 10)
        return ((_cn[tens] if tens > 1 else "") + "十"
                + (_cn[ones] if ones else ""))

    assert (_zh(8), _zh(15), _zh(20)) == ("八", "十五", "二十")
    assert f"科技至少{_zh(ad.COVERAGE_FLOORS[ad.TECH_COVERAGE_GAP])}則" in src
    assert f"科技之外至少{_zh(ad.COVERAGE_FLOORS[ad.SECTOR_COVERAGE_GAP])}則"         in src
    assert f"{_zh(ad.NEWS_TARGET_MIN)}到{_zh(ad.NEWS_TARGET_MAX)}則為目標" in src


def test_jargon_gets_a_short_gloss_rule():
    """術語第一次出現要用括號解釋(PMI 是什麼)。"""
    src = io.open(Path(pp.__file__), encoding="utf-8").read()
    assert "PMI（採購經理人指數" in src      # prompt 用全形(它自己要求的寫法)
    assert "第一次出現時" in src and "不重複解釋" in src


def test_taichung_dome_rides_along_with_construction():
    """2026-08-23 使用者:**不要獨立區塊**,併進「建設」——併進來之後它與
    其他在地建設共用同一個上限,沒有新聞的日子不會有空欄位、也不會每天
    固定佔一格。信件本身不得出現「使用者要求」這類字眼。"""
    labels = [q[0] for q in mr.LOCAL_NEWS_QUERIES]
    assert "台中大巨蛋" not in labels, "獨立區塊還在"
    q = next(x for x in mr.LOCAL_NEWS_QUERIES if x[0] == "建設")
    assert "台中大巨蛋" in q[1] and "台中巨蛋" in q[1], q[1]
    # 併進去不得把原本的在地建設詞條擠掉
    for kw in ("台中捷運", "彰化市 建設", "雲林 重大建設"):
        assert kw in q[1], kw
    # **信件上看得到的只有 label**(區塊標題就是它)—— 標題是中性的
    # 「建設」,所以信裡不會出現任何「這是誰點名的」痕跡。
    assert all("巨蛋" not in str(x[0]) for x in mr.LOCAL_NEWS_QUERIES)


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
    assert f"{ad.NEWS_TARGET_MIN}–{ad.NEWS_TARGET_MAX}" in _adv(3, 3), "六則仍被當成足夠"
    # 十則但全是科技 → 第九段沒東西
    a = _adv(10, 0)
    assert "科技以外只有 0 則" in a, a
    # 科技不足
    b = _adv(2, 9)
    assert "科技條目只有 2 則" in b, b
    # 兩段都夠就不吵(下限跟著 `COVERAGE_FLOORS` 走 —— 寫死數字的話,
    # 目標一上調這條就會變成「達標也被吵」而看不出是測試過時)
    c = _adv(ad.COVERAGE_FLOORS[ad.TECH_COVERAGE_GAP],
             ad.COVERAGE_FLOORS[ad.SECTOR_COVERAGE_GAP])
    assert "第八段靠它" not in c and "第九段靠它" not in c, c
    # r2 外審:**素材真的沒有那一類**時不得要求做不到的下限(那是逼它湊)
    d = _adv(2, 9, src_tech=2)
    assert "第八段靠它" not in d, d
    # 2026-08-24 外審 P2:**出口由 Python 判,不由模型宣告。** 這個期待
    # 先前寫成「模型填了代號就不再催」—— 那與同一條規則裡 Python 自己數的
    # `src_tech=12` 直接衝突,等於模型寫一行字就能關掉建議。
    e = _adv(2, 9, src_tech=12,
             gaps=((ad.TECH_COVERAGE_GAP, "今日科技新聞多為重複報導"),))
    assert "第八段靠它" in e, e
    # 但**不得反過來斷言那句宣告是假的**(r1 外審):`src_tech` 數的是素材
    # 則數,12 則可能是同一件事的 12 家轉載 —— 「素材充足」推不出「宣告
    # 是假的」。建議要給的是誠實的揭露路徑,不是指控。
    assert "缺口要對得上事實" not in e, e
    assert "轉載" in e and "data_gaps" in e, e
    # 常數是單一定義:prompt 裡的字面值要對得回來(否則模型收到的代號與
    # 這裡認得的不是同一個,而兩邊都看起來合理)
    import prompt_profiles as pp
    pp_src = io.open(Path(pp.__file__), encoding="utf-8").read()
    assert ad.TECH_COVERAGE_GAP in pp_src, ad.TECH_COVERAGE_GAP
    assert ad.SECTOR_COVERAGE_GAP in pp_src, ad.SECTOR_COVERAGE_GAP
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


def test_weekend_digest_section_order_matches_the_weekday_letter():
    """2026-08-23 使用者:週日信的順序要與平日晨報一致 ——
    天氣 → 未來 7 天風險事件 → 重大政策深度解析 → 在地快訊 → Podcast →
    體育 → 醫學文獻。先前體育/Podcast 在前、風險事件墊底,同一組區塊卻
    兩種順序,讀者每週要重新找一次東西在哪。"""
    html = mr.render_weekend_digest_html(
        "2026-08-23 (Sun)",
        weather_html="<div>W_WEATHER</div>",
        sports_html="<div>W_SPORTS</div>",
        podcast_html="<div>W_PODCAST</div>",
        journals_html="<div>W_JOURNALS</div>",
        calendar_html="<div>W_CALENDAR</div>",
        local_news_html="<div>W_LOCAL</div>",
        policy_analysis_html="<div>W_POLICY</div>")
    order = ["W_WEATHER", "W_CALENDAR", "W_POLICY", "W_LOCAL",
             "W_PODCAST", "W_SPORTS", "W_JOURNALS"]
    pos = [html.index(m) for m in order]
    assert pos == sorted(pos), [
        (m, html.index(m)) for m in order]
    # 缺席的區塊不留空殼(既有行為)
    lean = mr.render_weekend_digest_html(
        "2026-08-23 (Sun)", weather_html="<div>W_WEATHER</div>",
        sports_html="", podcast_html="", journals_html="",
        calendar_html="", local_news_html="", policy_analysis_html="")
    assert "W_WEATHER" in lean and "W_SPORTS" not in lean


# ---------------- 2026-08-24 使用者:中職未來賽程要有球場地點

def test_cpbl_venue_map_speaks_the_official_protocol(monkeypatch, tmp_path):
    """官網是 ASP.NET:token 在頁面 JS(不是 hidden input),POST 放 header
    並帶 X-Requested-With(實測缺一不可)。生產海外 IP 被擋 → 回 {}。"""
    import json as _json

    class _Sess:
        def get(self, url, **k):
            class _R:
                text = ("$.ajax({ url: '/schedule/getgamedatas', headers: {"
                        " RequestVerificationToken: 'TOK123' },")
                def raise_for_status(self): pass
            return _R()

        def post(self, url, data=None, headers=None, **k):
            assert headers.get("RequestVerificationToken") == "TOK123"
            assert headers.get("X-Requested-With") == "XMLHttpRequest"

            class _R:
                status_code = 200
                def raise_for_status(self): pass
                def json(self):
                    import datetime as dtm
                    d = (dtm.datetime.now(mr.TPE) + dtm.timedelta(days=1))
                    return {"Success": True, "GameDatas": _json.dumps([
                        {"GameDateTimeS": d.strftime("%Y-%m-%dT18:35:00"),
                         "HomeTeamName": "統一7-ELEVEn獅",
                         "VisitingTeamName": "味全龍", "FieldAbbe": "大巨蛋"}])}
            return _R()

    # 快取要隔離:這條測的是**協定**,不是「本機剛好有一份整季快取」
    monkeypatch.setattr(mr, "CPBL_VENUE_FILE", tmp_path / "v.json")
    monkeypatch.setattr(mr.requests, "Session", _Sess)
    vm = mr._cpbl_venue_map()
    assert len(vm) == 1
    (d, home), field = next(iter(vm.items()))
    assert home == "統一7-ELEVEn獅" and field == "大巨蛋"
    # 成功那次會把整季寫進快取 —— 所以下面測「無快取」要換一條乾淨路徑
    assert (tmp_path / "v.json").exists(), "成功抓取沒有落快取"

    class _Boom:
        def get(self, *a, **k): raise RuntimeError("geo-blocked")
    monkeypatch.setattr(mr.requests, "Session", _Boom)
    # 有快取 → 照樣答得出球場(這正是生產被 geo-block 那天要的行為)
    assert mr._cpbl_venue_map() and mr._VENUE_FROM_CACHE.get("hit") is True
    # 無快取 → {}(賽程照出、只少場地)
    monkeypatch.setattr(mr, "CPBL_VENUE_FILE", tmp_path / "empty.json")
    assert mr._cpbl_venue_map() == {}
    assert mr._VENUE_FROM_CACHE.get("hit") is False
    # **geo-block 最現實的形狀**(r1 外審):HTTP 200 回一頁沒有 token 的
    # blocked HTML —— 也要留痕地退化,不得無聲消失
    class _Blocked:
        def get(self, *a, **k):
            class _R:
                text = "<html>Access denied</html>"
                def raise_for_status(self): pass
            return _R()
    monkeypatch.setattr(mr.requests, "Session", _Blocked)
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        assert mr._cpbl_venue_map() == {}
    assert "CPBL 官網場地抓取失敗" in buf.getvalue(), "blocked HTML 沒有留痕"


def test_fixtures_carry_venue_and_survive_without_it(monkeypatch):
    """走**真的** `fetch_cpbl_today_fixtures`(mock Yahoo 回應,不打網路)——
    直接驗補綴規則會繞過 fetch 尾端的接線,那正是「測試要用生產的呼叫
    形狀」要防的。對照缺席時賽程照出、只少 venue(少一欄優於錯欄)。"""
    import datetime as dtm
    from email.utils import format_datetime
    now = dtm.datetime.now(mr.TPE)
    ko = (now + dtm.timedelta(days=1)).replace(hour=18, minute=35, second=0)
    tomorrow = ko.strftime("%m/%d")

    class _R:
        status_code = 200
        def raise_for_status(self): pass
        def json(self):
            return {"service": {"scoreboard": {
                "games": {"cpbl.g.1": {
                    "status_type": "status.type.pregame",
                    "start_time": format_datetime(ko),
                    "away_team_id": "a", "home_team_id": "h"}},
                "teams": {"a": {"display_name": "味全"},
                          "h": {"display_name": "統一"}}}}}
    monkeypatch.setattr(mr, "_http_get", lambda *a, **k: _R())
    got_asof = {}

    def _vm(as_of=None, days=9):
        got_asof["as_of"] = as_of
        return {(tomorrow, "統一7-ELEVEn獅"): "大巨蛋"}
    monkeypatch.setattr(mr, "_cpbl_venue_map", _vm)
    fx = mr.fetch_cpbl_today_fixtures(now)
    # 兩個抓取要用**同一個時鐘**(r1 外審 P3:回放時各看各的會對不上)
    assert got_asof["as_of"] is now
    ours = [x for x in fx if x["home"] == "統一"]
    assert ours and ours[0].get("venue") == "大巨蛋", fx
    # 對照缺席 → 賽程照出、只少 venue
    monkeypatch.setattr(mr, "_cpbl_venue_map", lambda as_of=None, days=9: {})
    fx2 = mr.fetch_cpbl_today_fixtures(now)
    assert any(x["home"] == "統一" for x in fx2)
    assert all("venue" not in x for x in fx2)


def test_fixture_renderer_shows_the_venue():
    """渲染:`日期 時間@場地　客 vs 主`(2026-08-24 使用者指定的版面 ——
    場地緊跟時間,不是排在隊伍後面);沒有 venue 的列不得出現空殼 @。"""
    import html as _h
    import render_utils as ru
    rows = ru._render_sports_html({"cpbl_fixtures": [
        {"date": "08/25", "start": "18:35", "away": "味全", "home": "統一",
         "venue": "亞太主"},
        {"date": "08/27", "start": "18:35", "away": "台鋼", "home": "富邦"}]},
        _h)
    import re as _re
    text = _re.sub("<[^>]+>", "", _re.sub("<div", chr(10) + "<div", rows))
    lines = [ln.strip() for ln in text.splitlines() if "vs" in ln]
    assert lines[0].startswith("08/25 18:35@亞太主"), lines
    assert lines[0].endswith("味全 vs 統一"), lines
    # 沒有場地的列不得留下空殼 @
    assert "@" not in lines[1] and lines[1] == "08/27 18:35　台鋼 vs 富邦", lines


def test_a_coverage_gap_must_be_droppable_once_the_section_is_filled():
    """2026-08-24 r2 外審:這條路徑上有一個結構性衝突 —— `deepen_input` 要
    模型「保留同樣的資料缺口」,`_identity()` 又把 `what_is_missing` 納入
    不可遺失身分。於是「補足條目之後撤掉那句『這一段不足』」做不到:模型
    照建議撤掉,選優判準就說「第二版弄丟了資料缺口」而沿用第一版。
    而**留著**也不行 —— 那句話會被自己的內容否證,並照樣印在信的
    「資料缺口」段(`analysis_render`)。所以撤得掉必須成立,留著必須被擋。"""
    import analysis_depth as ad
    import sys
    sys.path.insert(0, "tests")
    import fixtures_analysis as fx

    def _obj(n_tech, gap=True):
        o = fx.valid_analysis()
        rows = [{"source_item_id": f"t{i}", "why_it_matters": "x",
                 "direction": "bullish", "materiality": "medium"}
                for i in range(n_tech)]
        rows += [{"source_item_id": f"o{i}", "why_it_matters": "x",
                  "direction": "bullish", "materiality": "medium"}
                 for i in range(9)]
        o["top_news_analysis"] = rows
        o["data_gaps"] = ([{"gap_id": ad.TECH_COVERAGE_GAP,
                            "what_is_missing": "科技新聞多為同一件事的轉載",
                            "impact_on_conclusions": "第八段條目偏少"}]
                          if gap else [])
        return o

    # fixture 的 `claim_audit` 引用 `n1`、`cross_market_synthesis` 引用
    # `market:QQQ.change_pct` —— packet 少了它們,第二版會因為「引用了不存在
    # 的證據 ID」被否決,量到的就不是這條測試要問的事。
    pk_news = ([{"source_item_id": "n1", "title": "基準新聞"},
                {"source_item_id": "n2", "title": "基準新聞二"}]
               + [{"source_item_id": f"t{i}", "entities": ["2330"],
                   "title": "台積電先進封裝再擴產"} for i in range(12)]
               + [{"source_item_id": f"o{i}", "entities": ["2603"],
                   "title": "長榮美西運價連四漲"} for i in range(12)]
               + [{"source_item_id": f"x{i}"} for i in range(30)])
    pk = {"news": pk_news, "market": {"QQQ": {"change_pct": 1.8}},
          "tw_universe": [
              {"code": "2330", "name": "台積電", "industry": "半導體業"},
              {"code": "2603", "name": "長榮", "industry": "航運業"}]}

    before = _obj(2)                     # 2 則 + 缺口 → 一致,不算矛盾
    assert ad.contradicted_coverage_gaps(before, pk) == [], before["data_gaps"]
    assert "第八段靠它" in chr(10).join(ad.depth_advisories(before, pk))

    _floor = ad.COVERAGE_FLOORS[ad.TECH_COVERAGE_GAP]
    kept = _obj(_floor)                  # 補到下限卻留著那句話 → 矛盾
    assert ad.contradicted_coverage_gaps(kept, pk) == [ad.TECH_COVERAGE_GAP]
    assert "已經不成立" in chr(10).join(ad.depth_advisories(kept, pk))
    ok, why = ad.deepen_is_an_improvement(before, kept, evidence_ids=pk)
    assert not ok and "否證" in why, (ok, why)

    dropped = _obj(_floor, gap=False)    # 補了條目、撤掉那句話 → 才是改善
    assert "第八段靠它" not in chr(10).join(ad.depth_advisories(dropped, pk))
    ok, why = ad.deepen_is_an_improvement(before, dropped, evidence_ids=pk)
    assert ok, f"撤掉被否證的缺口反而被身分保存擋住了:{why}"

    # **提早撤掉也要擋**(2026-08-24 r3 外審):上一版把這兩個代號無條件
    # 從身分保存與面向計數裡豁免,於是「那一段還是只有 2 則卻把揭露刪了」
    # 沒有任何守衛看得到 —— 修一個洞開一個洞。規則是**雙向**的:
    # 該在不在、不該在還在,兩邊都算 fault,而且訊息要分得開(處置不同)。
    early = _obj(2, gap=False)
    assert ad.coverage_gap_faults(before, early, pk), "提早撤掉沒有被抓"
    ok, why = ad.deepen_is_an_improvement(before, early, evidence_ids=pk)
    assert not ok and "還成立" in why, (ok, why)
    # 兩條訊息要分得開:留著矛盾 vs 提早撤掉
    kept_why = ad.coverage_gap_faults(before, kept, pk)[0]
    early_why = ad.coverage_gap_faults(before, early, pk)[0]
    assert "否證" in kept_why and "否證" not in early_why, (kept_why, early_why)

    # 分類壞掉時**不猜**:回空而不是擋死。分不出科技/非科技的那一天,
    # 把每一次加深都判成 fault 會讓整條加深路徑靜默失效(而症狀只是
    # 「信變淺了」,沒有人看得出原因)。
    import analysis_render_depth as ard
    _real = ard.is_tech
    try:
        ard.is_tech = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x"))
        # 留著 → 證不出矛盾就別擋(不亂擋加深)
        assert ad.coverage_gap_faults(before, kept, pk) == [], "分類壞了卻擋死"
        assert ad.contradicted_coverage_gaps(kept, pk) == []
        # 撤掉 → **證不出已達標就不可以撤**(2026-08-24 r4 外審):上一版
        # 數不出來就一律放行,於是這條刪除路徑不需要任何證明就過關,而
        # `_identity` / `_dominance` 都已經無條件豁免這兩個代號。
        faults = ad.coverage_gap_faults(before, early, pk)
        assert faults and "要有證明" in faults[0], faults
        ok2, why2 = ad.deepen_is_an_improvement(before, early, evidence_ids=pk)
        assert not ok2 and "要有證明" in why2, (ok2, why2)
    finally:
        ard.is_tech = _real

    # **其餘缺口照舊保**:身分保存的豁免只給涵蓋率那兩個代號
    # 反例只靠身分保存分勝負:a2 的涵蓋率建議已清掉(6 則),所以數量關
    # 讓它通過、矛盾判準也放行 —— 唯一還會擋它的就是「弄丟了 chips 缺口」。
    b2, a2 = _obj(2), _obj(_floor, gap=False)
    b2["data_gaps"].append({"gap_id": "gap:other:chips",
                            "what_is_missing": "缺法人買賣超",
                            "impact_on_conclusions": "籌碼判斷保守"})
    ok, why = ad.deepen_is_an_improvement(b2, a2, evidence_ids=pk)
    assert not ok and "資料缺口" in why, (ok, why)

    # **加深輪的 prompt 也要說得對**:它明講「保留同樣的資料缺口」,而這批
    # 要求的正是「補足之後把那句話刪掉」—— 規格沒改的話,模型收到的是
    # 兩條互相矛盾的指令,而它只看得到 prompt。
    spec = ad.deepen_input("payload", ["補科技"], previous={"x": 1})
    assert ad.TECH_COVERAGE_GAP in spec and "刪掉" in spec, spec[-400:]


def test_venue_survives_a_geo_blocked_run(monkeypatch, tmp_path):
    """2026-08-24 使用者:信裡看不到球場。本機抓得到、生產抓不到 ——
    Actions 是海外 IP,而 CPBL 官網 2026-07 就已經被證實會擋(比分因此改走
    Yahoo)。球場**會輪動**(中信在大巨蛋也在洲際、樂天在大巨蛋也在桃園),
    所以靜態主場表會寫錯 —— 錯的地點比沒有地點糟。改成快取官方原始資料:
    任何一次抓得到就把整季寫下來,之後被擋的日子照樣答得出正確球場。"""
    cache = tmp_path / "v.json"
    monkeypatch.setattr(mr, "CPBL_VENUE_FILE", cache)
    mr._save_cpbl_venue_cache({"08/25|統一7-ELEVEn獅": "亞太主",
                               "08/25|中信兄弟": "大巨蛋"})

    class _Boom:
        def get(self, *a, **k):
            raise RuntimeError("geo-blocked")

    monkeypatch.setattr(mr.requests, "Session", _Boom)
    vm = mr._cpbl_venue_map()
    assert vm[("08/25", "統一7-ELEVEn獅")] == "亞太主", vm
    assert mr._VENUE_FROM_CACHE.get("hit") is True
    # **空的不覆寫**:抓到 0 場與「今天沒比賽」分不開,覆寫等於把整季擦掉
    mr._save_cpbl_venue_cache({})
    assert mr._load_cpbl_venue_cache(), "空結果把快取擦掉了"
    # 快取要跟著 state 一起 push,否則它只活在那一次 runner 上
    assert str(mr.CPBL_VENUE_FILE) in mr._state_push_paths() or \
        "cpbl_venues.json" in " ".join(mr._state_push_paths())


def test_an_http_ok_but_empty_season_still_uses_the_cache(monkeypatch,
                                                          tmp_path):
    """r1 外審:軟性 geo-block(HTTP 200 + 空 GameDatas)先前直接回 {} ——
    快取被保護著不被覆寫,卻沒有人去讀它。「不覆寫」與「用得上」是兩件事,
    只做前者等於白留了一份快取:那天場地照樣全部消失,而 manifest 還會
    標成 `source: none`。"""
    import json as _json
    cache = tmp_path / "v.json"
    monkeypatch.setattr(mr, "CPBL_VENUE_FILE", cache)
    mr._save_cpbl_venue_cache({"08/25|統一7-ELEVEn獅": "亞太主"})

    class _Sess:
        def get(self, url, **k):
            class _R:
                text = "RequestVerificationToken: 'TOK'"

                def raise_for_status(self):
                    pass
            return _R()

        def post(self, url, **k):
            class _R:
                def raise_for_status(self):
                    pass

                def json(self):
                    return {"Success": True, "GameDatas": _json.dumps([])}
            return _R()

    monkeypatch.setattr(mr.requests, "Session", _Sess)
    vm = mr._cpbl_venue_map()
    assert vm[("08/25", "統一7-ELEVEn獅")] == "亞太主", vm
    assert mr._VENUE_FROM_CACHE.get("hit") is True
    # 而且**不得覆寫**快取(空的回應把整季擦掉,下一班就真的沒了)
    assert mr._load_cpbl_venue_cache(), "空回應把快取擦掉了"
