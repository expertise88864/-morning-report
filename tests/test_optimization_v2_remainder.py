# -*- coding: utf-8 -*-
"""**OPTIMIZATION_PLAN V2 的剩餘項目**(N3 / N4 / D6 / N6)。

四項的共同主題是「**看得見**」:查詢還有沒有用、寫法有沒有在漂、
20 日模型該不該立項、換模型划不划算 —— 都是先前只能憑印象回答的問題。
"""
import io
from pathlib import Path

import gnews_registry as gr
import health_trends as ht

_ROOT = Path(__file__).resolve().parents[1]


def _read(name):
    return io.open(_ROOT / name, encoding="utf-8").read()


# ------------------------------------------------------------ V2-N4 註冊表

def test_the_registry_is_the_single_source_of_the_fixed_queries():
    """**同一份宣告**:`RSS_FEEDS` 的鍵、健康歷史的鍵、月報候刪清單的鍵。
    散在字面量裡的話,加一條查詢就要記得改三個地方。"""
    import morning_report as mr
    labels = {label for label, _, _ in gr.QUERIES}
    assert labels, "註冊表是空的"
    assert labels <= set(mr.RSS_FEEDS), sorted(labels - set(mr.RSS_FEEDS))
    assert set(gr.today_hits()) == labels
    # 主模組不得再有固定的 Google 查詢字面量(動態的個股/類股不算)
    src = _read("morning_report.py")
    for _label, query, _p in gr.QUERIES:
        assert f'_gnews_rss("{query}")' not in src, query


def test_hits_are_only_recorded_for_registry_urls():
    """個股與類股的動態查詢不得誤記到主題查詢頭上 —— 那會讓一條壞掉的
    主題查詢看起來還有命中。"""
    gr.reset()
    urls = gr.feed_entries(lambda q: f"https://x/?q={q}")
    gr.record(urls["Google-半導體"], 7)
    gr.record("https://x/?q=某個股 when:2d", 99)
    h = gr.today_hits()
    assert h["Google-半導體"] == 7
    assert sum(v for k, v in h.items() if k != "Google-半導體") == 0


def test_zero_hit_needs_a_full_window_of_evidence():
    """**樣本不足不是「沒有候刪」。** 上線第三天就列出十三條候刪,
    只會讓人學會忽略這份清單。"""
    day = {"queries": {lab: 0 for lab, _, _ in gr.QUERIES}}
    short = [dict(day, date=f"2026-07-{d:02d}") for d in range(1, 21)]
    assert gr.zero_hit_candidates(short, days=30) == []
    full = [dict(day, date=f"2026-07-{d:02d}") for d in range(1, 31)]
    assert len(gr.zero_hit_candidates(full, days=30)) == len(gr.QUERIES)


def test_one_hit_anywhere_in_the_window_clears_it():
    day = {"queries": {lab: 0 for lab, _, _ in gr.QUERIES}}
    hist = [dict(day, date=f"2026-07-{d:02d}",
                 queries=dict(day["queries"],
                              **({"Google-半導體": 5} if d == 14 else {})))
            for d in range(1, 31)]
    assert "Google-半導體" not in {c[0] for c in gr.zero_hit_candidates(hist)}


def test_a_day_that_was_never_fetched_breaks_the_streak():
    """**缺席與零命中是兩件事。** 熔斷或時間不夠那天沒抓,不該算成
    「這條查詢沒用」—— 那會把我們自己的抓取失敗算到查詢頭上。"""
    day = {"queries": {lab: 0 for lab, _, _ in gr.QUERIES}}
    hist = [dict(day, date=f"2026-07-{d:02d}") for d in range(1, 31)]
    hist[-3] = {"date": hist[-3]["date"], "queries": {}}      # 那天整批沒抓
    assert gr.zero_hit_candidates(hist, days=30) == []


def test_the_candidate_row_says_what_the_query_was_for():
    """只給標籤的話,判斷「該不該刪」還是要回去翻程式碼。"""
    day = {"queries": {lab: 0 for lab, _, _ in gr.QUERIES}}
    hist = [dict(day, date=f"2026-07-{d:02d}") for d in range(1, 31)]
    label, purpose, streak = gr.zero_hit_candidates(hist, days=30)[0]
    assert purpose and purpose != label
    assert streak == 30


# ------------------------------------------------------------ V2-N3 趨勢

def test_the_drama_trend_ignores_days_that_never_measured_it():
    """舊資料沒有這個欄位。把缺席算成 0 會讓走勢看起來「以前都很好」,
    而那正好是判讀時最容易被誤導的方向。"""
    hist = ([{"date": "2026-06-01"}, {"date": "2026-06-02"}]
            + [{"date": f"2026-07-{d:02d}", "drama": d % 4} for d in range(1, 11)])
    out = ht.drama_trend(hist)
    assert out["days"] == 10
    assert out["total"] == sum(d % 4 for d in range(1, 11))
    assert out["latest"] == 10 % 4


def test_the_drama_check_stays_record_only():
    """**不得升級為自動遮蔽**(計劃書 V2-N3:誤殺風險>收益)。
    判準靠關鍵詞,而同一個詞在不同語境可以是準確的。"""
    src = _read("morning_report.py")
    i = src.index("_audit_dramatic_macro_claims(analysis_for_render")
    seg = src[i:i + 700]
    assert "_DRAMA_COUNT" in seg
    for banned in ("analysis_for_render = ", "re.sub", "replace("):
        assert banned not in seg, f"交叉驗證開始改寫敘述了:{banned}"


def test_absent_measurements_are_not_written_as_zero():
    """`update_source_health_history` 只在**真的量到**時才寫那一格。"""
    src = _read("morning_report.py")
    assert 'if isinstance(query_hits, dict) and query_hits:' in src
    assert 'if isinstance(drama_count, int):' in src


def test_production_actually_passes_the_measurements_down():
    """**寫入端有守衛,不代表呼叫端有傳。**

    突變驗證抓到:把 `query_hits=` / `drama_count=` 從生產呼叫拿掉,
    上面那些測試**一條都不紅** —— 它們驗的是寫入函式的行為,而那個函式
    永遠收得到 `None`。這是這個 repo 記過的形狀:守衛不得靠遺忘失效。
    """
    src = _read("morning_report.py")
    i = src.index("update_source_health_history(")
    i = src.index("update_source_health_history(", i + 10)   # 跳過 def
    call = src[i:i + 320]
    assert "query_hits=_gnews_reg.today_hits()" in call, call
    assert "drama_count=_DRAMA_COUNT" in call, call


def test_a_short_streak_is_not_a_candidate():
    """**門檻要真的生效。** 連續 29 天零命中不算候刪 —— 差一天就列,
    等於每個月都有一批假的候刪清單。"""
    day = {"queries": {lab: 0 for lab, _, _ in gr.QUERIES}}
    hist = [dict(day, date=f"2026-07-{d:02d}") for d in range(1, 31)]
    hist[0] = {"date": hist[0]["date"],
               "queries": dict(day["queries"], **{"Google-半導體": 3})}
    got = {c[0] for c in gr.zero_hit_candidates(hist, days=30)}
    assert "Google-半導體" not in got
    assert len(got) == len(gr.QUERIES) - 1


def test_the_monthly_block_says_so_when_there_is_no_data():
    """**讀不到就說讀不到。** 印一個空表格會讓人以為一切正常。"""
    out = ht.monthly_block(_ROOT / "state" / "__不存在的檔案__.json")
    assert "讀不到" in out and "訊號" in out


def test_the_monthly_block_lists_candidates_without_deleting():
    day = {"queries": {lab: 0 for lab, _, _ in gr.QUERIES}, "drama": 2}
    hist = [dict(day, date=f"2026-07-{d:02d}") for d in range(1, 31)]
    import json
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                     encoding="utf-8") as f:
        json.dump(hist, f)
        path = f.name
    out = ht.monthly_block(path)
    assert "候刪,不自動刪" in out
    assert "Google-半導體" in out
    assert "敘述-數字交叉驗證" in out and "不遮蔽" in out


# ------------------------------------------------------------ V2-D6 / N6

def test_the_20d_design_doc_states_the_sample_problem():
    """**這份文件最重要的是那張表。** 20 日 horizon 的重疊樣本會讓
    `n_days` 看起來很大,而有效自由度接近月數 —— 用重疊樣本算 t 統計量
    會嚴重高估顯著性。文件沒講這件事就等於沒寫。"""
    doc = _read("docs/20d_horizon_design.md")
    assert "重疊" in doc and "獨立" in doc
    assert "Newey-West" in doc
    assert "現在不立項" in doc, "設計文件要給得出一個建議,不是只列選項"


def test_the_whisper_benchmark_never_runs_itself():
    """**換模型是使用者的決定。** 這個 job 只能手動觸發,而且唯讀。"""
    wf = _read(".github/workflows/whisper-benchmark.yml")
    assert "workflow_dispatch" in wf
    assert "schedule" not in wf, "benchmark 不該有排程"
    assert "contents: read" in wf and "contents: write" not in wf
    assert "requirements-podcast.lock" in wf, "未鎖版相依會在安裝時就執行"


def test_the_benchmark_only_writes_a_report():
    """**判準要看「做了什麼」,不是「提到什麼」。**

    第一版寫成「原始碼不得含 `PODCAST_WHISPER_MODEL_HIGH`」—— 而報告
    結尾那句「要改 `PODCAST_WHISPER_MODEL_HIGH` 請自己決定」剛好含它。
    子字串判準把**說明**當成**行為**。改成掃寫入動作。
    """
    import ast
    src = _read("tools/whisper_benchmark.py")
    tree = ast.parse(src)
    writes = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
            writes.add(n.func.attr)
        # `os.environ[...] = ...` 這種改設定的寫法
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if (isinstance(t, ast.Subscript)
                        and "environ" in ast.dump(t.value)):
                    raise AssertionError("benchmark 在改環境變數")
    assert "save_state" not in writes, "benchmark 寫了 state"
    # 只准寫進報告目錄
    assert "write_text" in writes
    for bad in ("commit", "push", "unlink", "rmtree"):
        assert bad not in writes, bad
    # 失敗的模型也要進報告 —— 否則它看起來像沒測過
    assert '"失敗"' in src
