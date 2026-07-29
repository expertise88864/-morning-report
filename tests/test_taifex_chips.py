"""TAIFEX 大額交易人 + 選擇權 Put/Call 比 fetcher 測試(借鏡 node-twstock,OpenAPI JSON)。"""
import morning_report as mr


class _Resp:
    def __init__(self, payload):
        self._p = payload

    def json(self):
        return self._p


_PCR = [
    {"Date": "20260622", "PutVolume": "310654", "CallVolume": "243174",
     "PutCallVolumeRatio%": "127.75", "PutOI": "57608", "CallOI": "39017",
     "PutCallOIRatio%": "147.65"},
    {"Date": "20260618", "PutCallVolumeRatio%": "112.84", "PutCallOIRatio%": "114.67"},
]

_LT = [
    # 別的商品(應被過濾)
    {"Date": "20260622", "Contract": "BRF", "SettlementMonth": "999912",
     "TypeOfTraders": "0", "Top5Buy": "55", "Top5Sell": "61", "Top10Buy": "66",
     "Top10Sell": "70", "OIOfMarket": "37"},
    # TX 近月(非合計,應被過濾)
    {"Date": "20260622", "Contract": "TX", "SettlementMonth": "202607",
     "TypeOfTraders": "0", "Top5Buy": "62250", "Top5Sell": "61038",
     "Top10Buy": "71183", "Top10Sell": "77034", "OIOfMarket": "105197"},
    # TX 所有契約合計 — 全部交易人
    {"Date": "20260622", "Contract": "TX", "SettlementMonth": "999912",
     "TypeOfTraders": "0", "Top5Buy": "62250", "Top5Sell": "61285",
     "Top10Buy": "71623", "Top10Sell": "77835", "OIOfMarket": "109089"},
    # TX 所有契約合計 — 特定法人
    {"Date": "20260622", "Contract": "TX", "SettlementMonth": "999912",
     "TypeOfTraders": "1", "Top5Buy": "62250", "Top5Sell": "61285",
     "Top10Buy": "69594", "Top10Sell": "77835", "OIOfMarket": "109089"},
]


def test_pc_ratio_picks_latest_and_parses(monkeypatch):
    monkeypatch.setattr(mr.requests, "get", lambda *a, **k: _Resp(_PCR))
    out = mr.fetch_taifex_options_pc_ratio()
    assert out["date"] == "20260622"          # 取最新日(非清單順序)
    assert out["pc_oi_ratio"] == 147.65
    assert out["pc_vol_ratio"] == 127.75


def test_pc_ratio_fallback_on_failure(monkeypatch):
    def boom(*a, **k):
        raise mr.requests.exceptions.ConnectionError("down")
    monkeypatch.setattr(mr.requests, "get", boom)
    assert mr.fetch_taifex_options_pc_ratio() == {}
    monkeypatch.setattr(mr.requests, "get", lambda *a, **k: _Resp([]))
    assert mr.fetch_taifex_options_pc_ratio() == {}


def test_large_traders_filters_tx_all_contracts(monkeypatch):
    monkeypatch.setattr(mr.requests, "get", lambda *a, **k: _Resp(_LT))
    out = mr.fetch_taifex_large_traders()
    assert out["date"] == "20260622"
    assert out["top10_buy"] == 71623 and out["top10_sell"] == 77835
    assert out["top10_net"] == 71623 - 77835            # -6212(偏空)
    assert out["oi_market"] == 109089
    assert out["concentration_pct"] == round(77835 / 109089 * 100, 1)   # 71.3
    assert out["spec_top10_net"] == 69594 - 77835       # -8241(特定法人更空)


def test_large_traders_empty_when_no_tx(monkeypatch):
    only_brf = [r for r in _LT if r["Contract"] == "BRF"]
    monkeypatch.setattr(mr.requests, "get", lambda *a, **k: _Resp(only_brf))
    assert mr.fetch_taifex_large_traders() == {}


def test_large_traders_fallback_on_failure(monkeypatch):
    def boom(*a, **k):
        raise mr.requests.exceptions.ConnectionError("down")
    monkeypatch.setattr(mr.requests, "get", boom)
    assert mr.fetch_taifex_large_traders() == {}


def test_large_traders_failsafe_on_missing_fields(monkeypatch):
    """缺 Top10Buy/Sell/OI 欄位 → 嚴格 parser 回 None → fail-safe 回 {}(不可用 0 算假部位)。"""
    bad = [{"Date": "20260622", "Contract": "TX", "SettlementMonth": "999912",
            "TypeOfTraders": "0", "Top10Buy": "", "OIOfMarket": "109089"}]  # 缺 Top10Sell、Buy 空
    monkeypatch.setattr(mr.requests, "get", lambda *a, **k: _Resp(bad))
    assert mr.fetch_taifex_large_traders() == {}
    zero_oi = [{"Date": "20260622", "Contract": "TX", "SettlementMonth": "999912",
                "TypeOfTraders": "0", "Top10Buy": "100", "Top10Sell": "90", "OIOfMarket": "0"}]
    monkeypatch.setattr(mr.requests, "get", lambda *a, **k: _Resp(zero_oi))
    assert mr.fetch_taifex_large_traders() == {}      # OI=0 不可當分母


def test_chip_fields_require_matching_source_date():
    """r19(Codex,P1):TAIFEX 兩個端點各自可能延遲,回傳的 date 不一定等於該交易日。
    直接寫入等於把舊訊號歸到較新的交易日,兩端點日期不同時甚至會把不同日的期貨與
    選擇權放進同一列——後續 IC/MCS/event study 會用到錯位特徵,那正好摧毀
    批#45「讓它可被量測」的目的。對不上就存 None(可辨識的缺值)。"""
    large = {"date": "20260622", "top10_net": -6212, "spec_top10_net": -8241,
             "concentration_pct": 71.3}
    pcr = {"date": "20260622", "pc_oi_ratio": 147.65}

    ok = mr._chip_fields_for_session(large, pcr, "2026-06-22")
    assert ok["taifex_top10_net"] == -6212
    assert ok["txo_pc_oi_ratio"] == 147.65
    assert ok["taifex_chip_source_date"] == "20260622"

    # 期貨端落後一天 → 期貨欄位全 None,選擇權不受影響
    stale = mr._chip_fields_for_session(
        {**large, "date": "20260620"}, pcr, "2026-06-22")
    assert stale["taifex_top10_net"] is None
    assert stale["taifex_spec_top10_net"] is None
    assert stale["taifex_top10_concentration_pct"] is None
    assert stale["txo_pc_oi_ratio"] == 147.65, "不該因期貨落後而牽連選擇權"

    # 兩端都對不上
    none_all = mr._chip_fields_for_session(
        {**large, "date": "20260620"}, {**pcr, "date": "20260621"}, "2026-06-22")
    assert all(none_all[k] is None for k in
               ("taifex_top10_net", "txo_pc_oi_ratio"))

    # 空 payload 不得爆
    assert mr._chip_fields_for_session(None, None, "2026-06-22")[
        "taifex_top10_net"] is None


def test_chip_signals_reach_both_state_and_model_history():
    """訊號要同時進 state/history.json(90 天)與 model_history(520 session)。
    只進前者的話,長期 IC/MCS 在 90 天後就沒有資料可用——那等於沒有可量測化。"""
    from pathlib import Path
    src = Path(mr.__file__).read_text(encoding="utf-8")
    assert src.count("_chip_fields_for_session(") >= 3, (
        "應有:函式定義 + state entry + model_history 各一處")
    mh_start = src.index('"universe_method": "daily_point_in_time_top100"')
    assert "_chip_fields_for_session(" in src[mh_start:mh_start + 2500],         "model_history 紀錄沒有籌碼訊號"


def test_chip_signals_survive_history_compaction():
    """壓縮白名單必須涵蓋,否則舊 session 在壓縮階段被裁掉,時序又出現空洞。"""
    from pathlib import Path
    src = Path(mr.__file__).read_text(encoding="utf-8")
    # 批#66:原本用「從 `keep_record = {` 起算 700 個字元」切原始碼比對,
    # 被一段無關的新註解推出視窗就會誤報。改用 AST 取**真正的集合字面值**
    # ——同一個不變式,但不會因為排版或註解而壞掉。
    import ast
    tree = ast.parse(src)
    keep = None
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "keep_record"
                        for t in node.targets)
                and isinstance(node.value, ast.Set)):
            keep = {e.value for e in node.value.elts
                    if isinstance(e, ast.Constant)}
            break
    assert keep, "找不到 keep_record 集合"
    for f in ("taifex_top10_net", "taifex_spec_top10_net",
              "taifex_top10_concentration_pct", "txo_pc_oi_ratio",
              # r21(Codex):來源日期欄位存在的理由就是日後對帳,壓縮時裁掉
              # 等於把它們的用途取消
              "taifex_chip_source_date", "txo_pcr_source_date"):
        assert f in keep, f"{f} 不在壓縮白名單"


def test_chip_signals_stay_out_of_stance_scoring():
    """**刻意不納入 11 維計分**。記憶裡的定案是「別貿然改計分/預測係數」,
    而 MCS 那批的結論也是「沒有把關前,新維度只是新的過擬合來源」。
    這條測試是防止日後有人(包括我)在沒有證據的情況下悄悄把它接進計分。"""
    import inspect
    src = inspect.getsource(mr._compute_stance_score)
    for field in ("taifex_top10_net", "spec_top10_net", "pc_oi_ratio",
                  "txo_pc_oi_ratio"):
        assert field not in src, (
            f"{field} 進了立場計分。若這是刻意的,必須先有 MCS/IC 證據並更新本測試"
            "與 model_version——立場分是信件頂部 KPI 的權威來源,不可無聲變動。"
        )


def test_cpbl_odds_normalise_before_rounding():
    """r2(七維度審查,P2):中職賠率原本先各自 round(p*100) 成整數,才在下游
    正規化;NBA 那條是對**原始 float** 正規化。批#47 宣稱「統一成 NBA 的做法」,
    實際只抽出了函式、沒統一取整時機。實測 raw=[0.554,0.456] 顯示成 (54,46),
    正確為 (55,45),差 1pp。"""
    import morning_report as mr
    assert mr._normalized_two_way([55.4, 45.6]) == (55, 45)
    assert sum(mr._normalized_two_way([55.4, 45.6])) == 100
    # 先取整再正規化會得到的錯誤結果
    assert mr._normalized_two_way([55.0, 46.0]) != (55, 45)


def test_all_odds_paths_always_sum_to_100():
    """2026-07-27 實信:三條賭盤路徑都是「先正規化、再**各自**四捨五入」,
    浮點誤差加上 banker's rounding 讓合計變成 99% 或 101%,看起來像算錯。
    實信同時出現「遊騎兵 42%・光芒 57%」(99%)與「釀酒人 55%・巨人 46%」(101%)。

    批#52 只把中職那條從「未正規化」改成「正規化」,**取整方式沒改**
    ——同一個病換個地方。這條測試窮舉兩方與三方,釘死「合計必然 100」。
    """
    import morning_report as mr
    # 兩方:窮舉 0.01% 粒度(修正前有 16 組破功,含實信那兩組)
    bad = [i / 10000 for i in range(1, 10000)
           if sum(mr._pct_split([i / 10000, 1 - i / 10000])) != 100]
    assert not bad, f"兩方合計不為 100 的比例:{bad[:5]}"
    # 實信的那一組
    assert sum(mr._pct_split([0.425, 0.575])) == 100

    # 三方(足球含和局)
    bad3 = [(i, j) for i in range(1, 99) for j in range(1, 100 - i)
            if sum(mr._pct_split([i / 100, j / 100, (100 - i - j) / 100])) != 100]
    assert not bad3, f"三方合計不為 100:{bad3[:5]}"

    # 中職那條也要走同一套
    assert sum(mr._normalized_two_way([55.4, 45.6])) == 100
    assert sum(mr._normalized_two_way([42.5, 57.5])) == 100


def test_pct_split_degrades_safely_on_bad_input():
    """壞輸入回空清單,呼叫端據此跳過該場——寧可不顯示賭盤,不要造出假機率。"""
    import morning_report as mr
    assert mr._pct_split([]) == []
    assert mr._pct_split([0, 0]) == []
    assert mr._pct_split(["x", "y"]) == []
    assert mr._pct_split(None) == []
