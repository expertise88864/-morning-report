# -*- coding: utf-8 -*-
"""2026-09-03 生產事故:特化路徑整條掛在

    TypeError: Object of type date is not JSON serializable

信照樣寄到(legacy 補上),但事件卡、淨效果、橫向綜合那三段today 都不在信裡。

**根因不是型別本身,是兩把尺**:修補輪切片的**成本估算**用
`json.dumps(..., default=str)`(`evidence_packet.evidence_snippets` 的
`cost = …`),而真正送出去那一次沒有 —— 於是一筆帶 `date` 的證據
**通過預算檢查、在序列化時炸掉**。這個 repo 已經栽過一次同型的
(slim 反而比 full 大:估的與量的不是同一份東西)。

而 `date` 進得了切片,是因為 `evidence_snippets` 對 `market:` 子樹與
registry 的 `value` 刻意**不轉型**(數值要保持數值)。所以序列化端必須
自己承擔 —— 政策早就寫在 `evidence_serialize.canonical_json` 的
docstring 裡:「證據裡混進 datetime / Decimal 時,寧可得到一個穩定的
字串,也不要讓整個 packet 拋例外」。9/3 之前只有那一處沒有照做。
"""
import ast
import datetime as dt
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
import evidence_packet as ep                                   # noqa: E402
import evidence_serialize as es                                # noqa: E402
import morning_report as mr                                    # noqa: E402
import payload_budget as pb                                    # noqa: E402

_NFP = dt.date(2026, 9, 4)          # 9/4 非農 —— 事故當天的行事曆
_TAIL = chr(10) + "PROBLEMS: macro_release 沒進 scenario_tree" + chr(10)


def _fat_slim_call(snippets, monkeypatch):
    """把 `_repair_request_payload` 逼進 slim 那條路,回 (payload, 紀錄)。"""
    monkeypatch.setattr(mr._ep, "evidence_ids", lambda p: set(snippets))
    monkeypatch.setattr(mr._ep, "evidence_snippets",
                        lambda p, ids, **k: dict(snippets))
    return mr._repair_request_payload(
        {"model": "m"}, "x" * pb.MAX_REQUEST_CHARS, _TAIL, {"news": []})


def test_a_date_in_the_slice_does_not_kill_the_specialized_path(monkeypatch):
    """切片裡有 `date` 時,修補輪要送得出去(而且日期讀得懂)。"""
    out, rec = _fat_slim_call(
        {"market:MACRO": {"value": {"nfp_release": _NFP, "consensus": "55K"}}},
        monkeypatch)
    assert rec["mode"] == "evidence_slice"
    assert "2026-09-04" in out["input"], "日期沒有變成看得懂的字串"
    assert "55K" in out["input"]


def test_the_budget_check_and_the_send_use_the_same_ruler(monkeypatch):
    """**估得過、送不出去**就是這次事故的形狀。

    同一份 body:成本估算用 `default=str` 所以算得出長度、被收進切片,
    而真正序列化那一次不吃 `default` —— 預算說「裝得下」,送出去卻炸。
    """
    body = {"value": {"nfp_release": _NFP}}
    # 估算那一把尺:算得出來(這是 `evidence_snippets` 用的那一支)
    assert len(json.dumps(body, ensure_ascii=False, default=str)) > 0
    # 送出去那一把尺:少了 default 會炸 —— 兩把尺不一致正是缺陷本身
    try:
        json.dumps(body, ensure_ascii=False)
        raise AssertionError("反例失效:這個 body 本來就該序列化不了")
    except TypeError:
        pass
    out, _ = _fat_slim_call({"market:MACRO": body}, monkeypatch)
    assert "2026-09-04" in out["input"]


def test_the_slice_really_can_carry_a_raw_date_from_a_packet(monkeypatch):
    """反例不是假設的:`evidence_snippets` 對 `market:` 子樹**刻意不轉型**。

    (數值要保持數值 —— 這是它的規約,不是疏忽。)所以 packet 裡任何一個
    `date` 物件都會原樣進到切片,序列化端必須自己承擔。
    """
    packet = {"news": [],
              "market": {"MACRO": {"nfp_release": _NFP, "10Y": 4.8}}}
    monkeypatch.setattr(ep, "evidence_meta",
                        lambda p: {"market:MACRO": {"source": "calendar"}})
    got = ep.evidence_snippets(packet, ["market:MACRO"], budget_chars=10_000)
    assert got, "反例沒切出東西 —— 那它證明不了任何事"
    assert any(isinstance(v, dt.date)
               for v in (got["market:MACRO"].get("value") or {}).values()), got


def test_the_boundary_normalizes_instead_of_relying_on_default_str():
    """**`default=str` 是最後一道保險,不是正常路徑**(r17 架構外審 P1)。

    依賴它的話 `Decimal("4.25")` 會悄悄變成字串 `"4.25"` —— 不會炸、
    測試全綠,而型別契約已經改了。所以進 prompt 之前就轉,而且每一種
    型別有明確規則(日期走 ISO、Decimal 保持是數字)。
    """
    import decimal
    tree, hits = es.normalize_json({
        "market": {"nfp": _NFP, "yield": decimal.Decimal("4.25"),
                   "tags": {"b", "a"}, "nan": float("nan"), "ok": 1.5}})
    assert tree["market"]["nfp"] == "2026-09-04"
    assert tree["market"]["yield"] == 4.25 and isinstance(
        tree["market"]["yield"], float), "Decimal 被轉成字串了"
    assert tree["market"]["tags"] == ["a", "b"]
    assert tree["market"]["nan"] is None, "NaN 不是合法 JSON"
    assert tree["market"]["ok"] == 1.5
    # 轉過的都要留痕 —— 那是真正該修的上游欄位
    # r18:hits 帶**嚴重度**(無損轉型與「語意沒了」不是同一件事),
    # 所以這裡連分級一起釘 —— 比只比對路徑更嚴。
    assert sorted(hits) == sorted([
        (es.NORM_LOSSLESS, "market.nfp(date)"),
        (es.NORM_LOSSLESS, "market.yield(Decimal)"),
        (es.NORM_LOSSLESS, "market.tags(set)"),
        (es.NORM_DROPPED, "market.nan(non_finite_float)")]), hits
    # 轉完就是 JSON 原生,而且**冪等**
    assert es.nonjson_value_paths(tree) == []
    assert es.normalize_json(tree) == (tree, [])
    json.dumps(tree, ensure_ascii=False)      # 不靠 default 也送得出去


def test_a_non_finite_decimal_follows_the_same_rule_as_a_float():
    """`Decimal("NaN")` 轉成 float 之後**仍然**是 NaN(Codex r1 P2)。

    上面那條「NaN/inf → null」的契約先前只套在原生 float 那條路 ——
    Decimal 這條會回一個非有限的 float,不是合法 JSON,而且要再呼叫
    一次才會變 None:**冪等也不成立**。
    """
    import decimal
    # **兩種失效路徑都要**(量出來的):`sNaN` 的 `float()` 會**拋例外**
    # (先前的 except 回 `str(node)` —— 正好重現「Decimal 悄悄變字串」);
    # 而 `1e400` 的 `is_finite()` 是 **True**、`float()` 卻給 `inf`,
    # 所以只靠 `is_finite()` 也漏。
    for raw in ("NaN", "Infinity", "-Infinity", "sNaN", "1e400", "-1e400"):
        tree, hits = es.normalize_json({"x": decimal.Decimal(raw)})
        assert tree == {"x": None}, (raw, tree)
        assert hits == [(es.NORM_DROPPED, "x(non_finite_decimal)")], (raw, hits)
        assert es.normalize_json(tree) == (tree, []), "不冪等"
    # 有限的照樣保持是數字
    tree, _ = es.normalize_json({"x": decimal.Decimal("4.25")})
    assert tree["x"] == 4.25 and isinstance(tree["x"], float)


def test_a_key_collision_is_deterministic_and_never_silent():
    """字串化之後撞在一起的鍵,不可以看**插入順序**決定誰活(Codex r1 P2)。

    `{"1": …, 1: …}` 是這個 repo **被支援且被診斷**的情境
    (`_key_order` / `nonstring_key_paths` / `test_canonical_json_keys`)——
    在 canonical serialization 之前就丟掉一個,等於送進 prompt 的證據與
    指紋只因為 dict 的插入順序不同而改變。
    """
    a = es.normalize_json({"1": "string", 1: "integer"})
    b = es.normalize_json({1: "integer", "1": "string"})
    assert a == b, ("插入順序改變了結果", a, b)
    assert a[1] == [(es.NORM_COLLISION, "1(key_collision)")], "碰撞被靜靜吃掉了"
    # 判準要與 canonical_json 一致(依 `_key_order` 排序後由後者勝出,
    # 那也是 JSON 解析器對重複鍵的結果)
    assert a[0] == json.loads(es.canonical_json({"1": "string", 1: "integer"}))
    # 沒有碰撞時不可以誤報
    assert es.normalize_json({"a": 1, "b": 2})[1] == []


def test_the_packet_is_normalized_before_it_reaches_the_prompt():
    """接上去了才算數:正規化要真的發生在 packet 建好、送出去之前。"""
    tree = ast.parse((_ROOT / "morning_report.py").read_text(encoding="utf-8"))

    def _calls(node):
        return {ast.unparse(n.func) for n in ast.walk(node)
                if isinstance(n, ast.Call)}

    hosts = [f for f in ast.walk(tree)
             if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
             and "_luna_analysis" in _calls(f)]
    assert hosts, "錨點沒了"
    assert any("_es.normalize_json" in _calls(f) for f in hosts), (
        "packet 沒有在送進 prompt 之前正規化")


def test_the_offending_upstream_field_is_recorded(monkeypatch):
    """`default=str` 是止血 —— **源頭**仍然是某個上游欄位放了 `date` 物件。

    2026-08-04 的同型事故(鍵的型別)留下的規約就是「不記下來的話,下次
    換一個欄位又要從零查一次」。那次記了鍵,這次要記值。
    """
    paths = es.nonjson_value_paths(
        {"market": {"MACRO": {"nfp_release": _NFP}}, "news": [{"t": "x"}]})
    assert paths == ["market.MACRO.nfp_release(date)"], paths
    # 乾淨的 packet 不可以誤報(否則 manifest 天天多一欄雜訊)
    assert es.nonjson_value_paths(
        {"a": [1, 2.5, True, None, "s", {"b": []}]}) == []
    # **值不可以進 manifest**:公開 repo,packet 裡有 `portfolio:`。
    assert "2026-09-04" not in "".join(paths)


def test_the_diagnostic_is_wired_next_to_its_twin():
    """接上去了才算數:那支函式要真的被呼叫,而且與孿生診斷在同一個函式裡。"""
    tree = ast.parse((_ROOT / "morning_report.py").read_text(encoding="utf-8"))

    def _calls(node):
        return {ast.unparse(n.func) for n in ast.walk(node)
                if isinstance(n, ast.Call)}

    hosts = [f for f in ast.walk(tree)
             if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
             and "_ep.nonstring_key_paths" in _calls(f)]
    assert hosts, "孿生診斷不見了 —— 這條測試的錨點沒了"
    assert any("_es.normalize_json" in _calls(f) for f in hosts), (
        "值的型別診斷沒有接在 packet 建好的那個地方")
