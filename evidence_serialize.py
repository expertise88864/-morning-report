# -*- coding: utf-8 -*-
"""**證據包的序列化與指紋**(第十七輪從 `evidence_packet` 拆出)。

自成一塊的理由是它有**自己的失效方式**,而且那個失效方式讓 Luna 特化
路徑連兩天完全跑不起來:`sort_keys=True` 在鍵混型別時會拋
`TypeError: '<' not supported between int and str`,而 `default=str`
保護的是**值**、沒有人保護鍵 —— 那個函式的 docstring 自己寫著
「寧可得到穩定字串,也不要讓整個 packet 拋例外」。

指紋是實驗公平性的全部依據(兩邊的 `evidence_sha` 相同才算可比),
所以它值得一個自己的檔、自己的測試,而不是混在證據組裝裡。
"""
from __future__ import annotations

import hashlib
import json
from typing import Optional


def _key_order(k):
    """混型別的鍵也要排得出先後。**先比型別名,再比字串形式。**

    `sorted()` 對 `{2026: …, "QQQ": …}` 會拋
    `TypeError: '<' not supported between instances of 'int' and 'str'`。
    全部是字串鍵時,`str(k) == k`,所以排序結果與 `sorted(keys)` 完全相同
    —— 這是「修了不改變既有指紋」的依據。
    """
    return (type(k).__name__, str(k))


def _sorted_tree(node):
    """把整棵樹的 dict 依 `_key_order` 重建。**只改順序,不改內容。**"""
    if isinstance(node, dict):
        return {k: _sorted_tree(node[k]) for k in sorted(node, key=_key_order)}
    if isinstance(node, (list, tuple)):
        return [_sorted_tree(v) for v in node]
    return node


def nonstring_key_paths(node, path: str = "") -> list:
    """哪些位置的 dict 鍵不是字串(給診斷用,不影響序列化)。

    2026-08-04 實機:Luna 特化路徑連兩天失敗,而第二天終於記到例外是
    `TypeError: '<' not supported between instances of 'int' and 'str'`。
    知道「是鍵的型別」還不夠 —— 要知道**是哪個上游欄位**才修得到源頭,
    否則下次換一個欄位又會重來一次。
    """
    out: list = []
    if isinstance(node, dict):
        for k, v in node.items():
            here = f"{path}.{k}" if path else str(k)
            if not isinstance(k, str):
                out.append(f"{path or '(root)'}:{k!r}({type(k).__name__})")
            out += nonstring_key_paths(v, here)
    elif isinstance(node, (list, tuple)):
        for i, v in enumerate(node):
            out += nonstring_key_paths(v, f"{path}[{i}]")
    return out


#: normalization 的四種**嚴重度**(r18 外審 P2)。先前全部混在同一個
#: 清單裡,而 `date → ISO` 與 `未知物件 → str()` 完全不是同一件事:
#: 前者無損、後者**語意已經漂掉**。混在一起再取前 8 個,等於用雜訊蓋掉訊號。
NORM_LOSSLESS = "lossless"    #: date/Decimal/numpy/Enum/tuple/set → 等價的原生值
NORM_LOSSY = "lossy"          #: 未知物件 → `str()`,型別語意沒了
NORM_DROPPED = "dropped"      #: NaN/inf → null,值沒了
NORM_COLLISION = "collision"  #: 字串化後撞在一起的鍵,有一個值被蓋掉

#: JSON 原生型別 —— 其餘都要靠 `default=` 才送得出去。
_JSON_NATIVE = (str, int, float, bool, type(None), dict, list)


def nonjson_value_paths(node, path: str = "") -> list:
    """哪些位置的**值**不是 JSON 原生型別(給診斷用,不影響序列化)。

    `nonstring_key_paths` 的孿生。2026-09-03 生產:特化路徑掛在
    `TypeError: Object of type date is not JSON serializable`,而修補輪的
    切片內容取自 packet 的 `market:` 子樹與 registry 的 `value` —— 兩者
    都是**原樣**帶過去的。知道「是值的型別」還不夠,要知道**是哪個上游
    欄位**才修得到源頭(2026-08-04 那次的教訓,換一層再犯一次)。

    **只記路徑與型別,不記值**:manifest 進公開 repo,而 packet 裡有
    `portfolio:` 這種不可外流的東西。型別足以定位欄位。
    """
    out: list = []
    if isinstance(node, dict):
        for k, v in node.items():
            here = f"{path}.{k}" if path else str(k)
            out += nonjson_value_paths(v, here)
    elif isinstance(node, (list, tuple)):
        for i, v in enumerate(node):
            out += nonjson_value_paths(v, f"{path}[{i}]")
    elif not isinstance(node, _JSON_NATIVE):
        out.append(f"{path or '(root)'}({type(node).__name__})")
    return out


def _via(value, path: str, tag):
    """轉一手之後再正規化,**內層的 hits 不可以丟掉**(Codex deep P2)。

    先前寫 `normalize_json(v, path)[0]` —— 只取樹、扔掉診斷。於是
    `np.complex128(...).item()` 回一個 Python `complex`、再掉到 `str()`
    的那種情形,對外只報成「無損」:`summarize_normalization()` 數不到
    lossy、`run_quality` 也就不會報 —— 而模型收到的已經是字串。
    內層有比無損更嚴重的結果時,外面那層轉換本身也不算無損。
    """
    tree, nested = normalize_json(value, path)
    if any(k != NORM_LOSSLESS for k, _ in nested):
        return tree, nested
    return tree, [tag] + nested


def normalize_json(node, path: str = ""):
    """把整棵樹轉成 **JSON 原生型別**,回 `(新的樹, 被轉過的路徑清單)`。

    r17 外審 P1(2026-09-03 生產事故的根治):`default=str` 是**最後一道
    保險**,正常路徑不該依賴它 —— 它會把 `Decimal("4.25")` 變成字串
    `"4.25"`,型別悄悄改了而測試全綠。所以在**進 prompt 之前**就轉,
    而且每一種型別有明確規則:

      date / datetime → ISO 字串(而不是 `str()` 的區域格式)
      Decimal         → float(**保持是數字**)
      set / tuple     → list
      Enum            → 它的 value
      其餘非原生      → `str()`,但**留下路徑**(那是要修的上游欄位)

    **回新的樹,不就地改**:packet 的子樹與 `quotes` 共用物件,就地改會
    連帶改掉後面渲染要用的真值。
    """
    import datetime as _d
    import decimal as _dec
    import enum as _e
    import numbers as _num
    if isinstance(node, dict):
        out, hits, won = {}, [], {}
        for k, v in node.items():
            here = f"{path}.{k}" if path else str(k)
            nv, nh = normalize_json(v, here)
            hits += nh
            nk = k if isinstance(k, str) else str(k)
            if nk in out:
                # **混型別鍵是被支援且被診斷的情境**(`_key_order` /
                # `nonstring_key_paths`),不是這裡可以靜靜丟掉一個的地方
                # (Codex r1 P2):`{"1": ..., 1: ...}` 字串化後撞在一起,
                # 誰活下來會變成**看插入順序**。判準沿用 canonical_json:
                # 依 `_key_order` 排序後由後者勝出(那也是 JSON 解析器對
                # 重複鍵的結果)。碰撞本身要留痕 —— 上游該修。
                hits.append((NORM_COLLISION, f"{here}(key_collision)"))
                if _key_order(k) < _key_order(won[nk]):
                    continue
            out[nk], won[nk] = nv, k
        return out, hits
    if isinstance(node, (list, tuple, set)):
        # **`str` 不是全序**(r18 外審 P2):`{1, "1"}` 兩個元素的 `str()`
        # 完全相同,而 Python 的穩定排序會保留 **set 的迭代順序** ——
        # 那個順序跨 process 不保證一致(字串雜湊有隨機種子)。同一份證據
        # 因此可能產生兩種表示,而它會進 prompt、進指紋。判準沿用鍵那邊
        # 已經有的全序 `_key_order`(型別名 + 字串形式)。
        seq = sorted(node, key=_key_order) if isinstance(node, set) else node
        out, hits = [], []
        for i, v in enumerate(seq):
            nv, nh = normalize_json(v, f"{path}[{i}]")
            out.append(nv)
            hits += nh
        # tuple / set 本身也是一種轉換 —— 上游欄位仍然要修
        if not isinstance(node, list):
            hits = [(NORM_LOSSLESS,
                     f"{path or '(root)'}({type(node).__name__})")] + hits
        return out, hits
    if isinstance(node, bool) or node is None or isinstance(node, (str, int)):
        return node, []
    if isinstance(node, float):
        # NaN / inf 不是合法 JSON(`json.dumps` 預設會產出 `NaN` 字面值,
        # 那是**別的解析器讀不懂**的東西)。轉成 null 並留痕。
        if node != node or node in (float("inf"), float("-inf")):
            return None, [(NORM_DROPPED, f"{path or '(root)'}(non_finite_float)")]
        return node, []
    _p = f"{path or '(root)'}({type(node).__name__})"
    tag = (NORM_LOSSLESS, _p)
    # **NumPy 的純量不是 Python 的純量**(r18 外審 P2):
    #   `isinstance(np.int64(5), int)` → False
    #   `isinstance(np.float32(1.2), float)` → False
    #   `isinstance(np.bool_(True), bool)` → False
    # 於是它們先前全部掉到最後那行 `str(node)`:`5` 變 `"5"`、
    # `True` 變 `"True"` —— **不會炸、測試全綠、型別已經改了**,
    # 正是這支函式存在的理由(9/3 的 `date` 是同一族的另一種)。
    # 而 numpy / pandas 是這個 repo 的硬依賴,不是理論情境。
    # 順序要緊:`bool` 是 `Integral` 的子型別,得先問。
    if isinstance(node, _num.Integral):
        return int(node), [tag]
    if isinstance(node, _num.Real):
        f = float(node)
        if f != f or f in (float("inf"), float("-inf")):
            return None, [(NORM_DROPPED, f"{path or '(root)'}(non_finite_real)")]
        return f, [tag]
    if isinstance(node, (_d.datetime, _d.date, _d.time)):
        return node.isoformat(), [tag]
    if isinstance(node, _dec.Decimal):
        # **兩道都要**(Codex r1/r2 P2,而且是量出來的不是推理出來的):
        #   `Decimal("sNaN")`  → `float()` 直接拋 ValueError(先前的 except
        #                        回 `str(node)`,正好重現「Decimal 悄悄變字串」)
        #   `Decimal("1e400")` → `is_finite()` 是 **True**,`float()` 卻給 inf
        # 少任何一道都會漏掉一種。契約是「NaN/inf → null」與
        # 「Decimal 保持是數字」,兩者都不能靠字串化蒙混過去。
        nf = (NORM_DROPPED, f"{path or '(root)'}(non_finite_decimal)")
        if not node.is_finite():
            return None, [nf]
        try:
            f = float(node)
        except (ValueError, OverflowError):   # 有限的 Decimal 走不到這裡
            return None, [(NORM_LOSSY,
                           f"{path or '(root)'}(decimal_unconvertible)")]
        if f != f or f in (float("inf"), float("-inf")):
            return None, [nf]
        return f, [tag]
    if isinstance(node, _e.Enum):
        return _via(node.value, path, tag)
    # **`np.bool_` 連 `numbers.Integral` 都不是**(實測):上面兩條攔不到它,
    # 它會掉到最後那行變成 `"True"`。NumPy / Pandas 的純量都提供 `.item()`
    # 回 Python 原生值 —— 用它比在這個 leaf 模組 import numpy 好
    # (這裡刻意只依賴 stdlib),而且 `np.datetime64` / `pd.Timestamp`
    # 之類未來冒出來的型別也一起接住。
    _item = getattr(node, "item", None)
    if callable(_item):
        try:
            v = _item()
        except Exception:                   # noqa: BLE001 - 接不住就退回 str
            v = node
        if type(v) is not type(node):       # 防自我遞迴
            return _via(v, path, tag)
    return str(node), [(NORM_LOSSY, _p)]


def summarize_normalization(hits) -> dict:
    """把 hits 收成**可以進 manifest、也判得動嚴重度**的一份摘要。

    r18 外審 P2:先前直接把整串 hits 截前 8 個寫進 manifest ——
    於是 1~8 全是無損的 `date → ISO`、第 9 個才是 `未知物件 → str()` 時,
    **真正嚴重的那一個根本不會被記下來**。分類計數不會被截斷。
    """
    counts = {NORM_LOSSLESS: 0, NORM_LOSSY: 0,
              NORM_DROPPED: 0, NORM_COLLISION: 0}
    samples: dict = {}
    for kind, where in hits or ():
        counts[kind] = counts.get(kind, 0) + 1
        # 每一類各留幾筆:嚴重的那幾類不會被無損那類擠掉
        samples.setdefault(kind, [])
        if len(samples[kind]) < 4:
            samples[kind].append(where)
    out = {k: v for k, v in counts.items() if v}
    if samples:
        out["samples"] = samples
    return out


def canonical_json(packet: dict) -> str:
    """穩定序列化。**排序鍵、無空白、不逃逸非 ASCII、無法序列化的轉字串。**

    `default=str` 是刻意的:證據裡混進 datetime / Decimal 時,寧可得到一個
    穩定的字串,也不要讓整個 packet 拋例外 —— 那會讓當天完全沒有 sha,
    而沒有 sha 的那天就是不可比的一天。

    2026-08-04 實機:**上面那句話是這個函式沒有做到的事。** `default=str`
    保護的是**值**,而 `sort_keys=True` 在**鍵**混型別時照樣拋 ——
    Luna 特化路徑連兩天在這裡掛掉(`build()` 只對 news 算 core_sha 所以沒事,
    `build_luna_bundle` 對整個 packet 算 evidence_sha 才炸),實驗 0/10。
    改成先用型別感知的順序重建整棵樹,再以 `sort_keys=False` 輸出:
    **全字串鍵時輸出逐位元組相同**,混型別時不再拋。
    """
    return json.dumps(_sorted_tree(packet), sort_keys=False, ensure_ascii=False,
                      separators=(",", ":"), default=str)


def evidence_sha(packet: dict) -> str:
    """**這個 packet 物件**的指紋。

    ⚠ 它證明的是「兩邊拿到同一個 packet」,**不是**「兩邊看到同樣的東西」——
    legacy profile 走的是 `_build_prompt`,那份 prompt 有自己的 bucket 配額、
    自己的全文取捨、也消費了幾個不在 `EVIDENCE_QUOTE_KEYS` 裡的欄位。
    可比性請用 `core_evidence_sha`,理由見它的 docstring。
    """
    return hashlib.sha256(canonical_json(packet).encode("utf-8")).hexdigest()[:16]


def core_evidence_sha(news: Optional[list], target_session_date: str = "") -> str:
    """**兩邊都確實看得到的核心證據**的指紋(r2 外審 #2 的折衷)。

    ## 為什麼不能用整個 packet 的 sha 當可比性判準

    兩份 prompt 是**各自獨立組出來的**:Luna 從 packet 渲染,DeepSeek 走既有的
    `_build_prompt`。兩者的深度與欄位取捨本來就不同(那正是「各自最佳化」),
    所以「同一個 packet 物件」證明不了「同樣的證據」。拿它當可比性判準,
    是一個聽起來很硬、實際上是空的保證。

    要讓那個保證為真只有兩條路,而兩條都牴觸既有約束:
      (a) 讓 DeepSeek 也從 packet 渲染 → 改變它的 prompt,違反「保留原設計」,
          逐位元組凍結會紅;
      (b) 對兩份 prompt 各自算真實內容指紋、不同就判不可比 → 誠實,
          但幾乎每天都不可比,十配對湊不滿。

    ## 折衷:指紋只涵蓋「來源池」

    這個 sha 算的是**上游那份 `news` 的 source_item_id 集合 + 目標交易日**,
    也就是兩條路徑共同的**輸入**,在任何截斷與渲染之前。它證明得了:

        兩邊今天是從同一批新聞、同一個交易日出發的。

    它**證明不了**兩邊看到同樣的深度 —— 那個差異由 `coverage` 逐側記錄,
    在最終報告裡當作**已揭露的 profile 差異**,而不是假裝不存在。
    這是這份實驗誠實能給的最強保證,不是最漂亮的那個。
    """
    from evidence_packet import _sid   # 延遲:避免循環 import
    ids = sorted({_sid(n, i) for i, n in enumerate(news or [])
                  if isinstance(n, dict)})
    raw = str(target_session_date or "") + "|" + ",".join(ids)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
