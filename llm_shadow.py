# -*- coding: utf-8 -*-
"""LLM **影子比較**:同一份 prompt 讓第二個模型也跑一次,只記錄、不改輸出。

批#89(2026-07-31)。要換模型之前必須先有**可比較的證據**,而不是憑一次的
主觀印象 —— 這個 repo 已經有同樣形狀的東西(MZ 影子),照它的原則做:

  1. **只算不改**:影子輸出永遠不進信件。失敗就只是今天沒有比較資料。
  2. **附加式帳本**:比較結果逐日累積,否則永遠沒有樣本可以判斷。
  3. **可觀測**:結果進 manifest,不然它只存在於當次記憶體。

**為什麼不能只看「哪個讀起來比較好」**:立場評分、beta 0.31、Top5 熔斷都是在
現有模型的輸出分佈上校準的。換模型等於換掉那個分佈,而分佈的差異要看**多天**
才看得出來(單日的差異可能只是當天新聞本來就模稜兩可)。

本模組刻意**不碰檔案系統與網路**:比較是純函式,帳本讀寫由呼叫端傳入路徑,
這樣它可以被單獨測試,也不受 conftest 的 state 隔離影響。
"""
from __future__ import annotations

import datetime as _dt
import hashlib as _hashlib
import json as _json
from pathlib import Path
from typing import Optional

import analysis_origin as _ao

#: 帳本保留天數。比較樣本要夠長才看得出分佈差異,但也不需要無限累積。
LEDGER_KEEP_DAYS = 120

#: 一天**每個同群**最多一筆。
#:
#: 第十輪 P1-6:原本只含 `date / primary_model / shadow_model`,
#: 於是同一天測「Luna xhigh vs DeepSeek」再測「Luna medium vs DeepSeek」時,
#: 第二次會把第一次覆蓋 —— 而那正是 2026-08-01 實際發生的使用方式
#: (同一天多次改推理強度做比較)。key 必須與同群一致,否則
#: `summarize` 依同群過濾之後,被覆蓋掉的那個同群永遠是 0 筆。
#: 第十四輪 P0-1:`analysis_origin` 也進 key —— 同一天先特化失敗、
#: 重跑後成功的話,兩列是**不同的事實**,後者不該把前者蓋掉。
LEDGER_KEY_FIELDS = ("date", "primary_model", "primary_effort",
                     "shadow_model", "shadow_effort", "code_version",
                     "analysis_origin")


def _bigrams(text: str) -> set:
    t = "".join(str(text or "").split())
    return {t[i:i + 2] for i in range(len(t) - 1)} if len(t) >= 2 else set()


def text_overlap(a: str, b: str) -> float:
    """兩段文字的 bigram 重疊率(分母取較短的一邊)。

    分母取短邊是刻意的:兩個模型的輸出長度可能差很多,用長邊當分母會把
    「短的那個講了同樣的重點」誤判成不像。
    """
    ga, gb = _bigrams(a), _bigrams(b)
    if not ga or not gb:
        return 0.0
    return round(len(ga & gb) / min(len(ga), len(gb)), 3)


def compare_outputs(primary: dict, shadow: dict) -> dict:
    """把兩邊的輸出整理成一筆**可累積**的比較紀錄。

    `primary` / `shadow` 各需含:`model`、`text`、`stance`(dict,含 label/score)、
    `summary`、`elapsed`、`ok`。缺的欄位一律視為未知,不編值。

    刻意記錄「**是否同向**」而不是只記兩個標籤:換模型真正的風險是
    **立場翻面**(偏多 ↔ 偏空),那才會改變讀者的動作;
    「中性 vs 偏多」與「偏多 vs 中性」是同一件事,不該被算成兩種不同的分歧。
    """
    def _g(d, *path, default=None):
        cur = d
        for k in path:
            if not isinstance(cur, dict):
                return default
            cur = cur.get(k)
        return cur if cur is not None else default

    p_label = str(_g(primary, "stance", "label") or "")
    s_label = str(_g(shadow, "stance", "label") or "")
    p_score = _g(primary, "stance", "score")
    s_score = _g(shadow, "stance", "score")
    rec = {
        "primary_model": str(primary.get("model") or ""),
        "shadow_model": str(shadow.get("model") or ""),
        "primary_ok": bool(primary.get("ok")),
        "shadow_ok": bool(shadow.get("ok")),
        "primary_stance": p_label or None,
        "shadow_stance": s_label or None,
        "stance_agree": (p_label == s_label) if (p_label and s_label) else None,
        "stance_flipped": _is_flip(p_label, s_label),
        "primary_score": p_score if isinstance(p_score, int) else None,
        "shadow_score": s_score if isinstance(s_score, int) else None,
        "primary_chars": len(str(primary.get("text") or "")),
        "shadow_chars": len(str(shadow.get("text") or "")),
        "summary_overlap": text_overlap(primary.get("summary"),
                                        shadow.get("summary")),
        "body_overlap": text_overlap(primary.get("text"), shadow.get("text")),
        "primary_seconds": _round_or_none(primary.get("elapsed")),
        "shadow_seconds": _round_or_none(shadow.get("elapsed")),
    }
    if isinstance(p_score, int) and isinstance(s_score, int):
        rec["score_gap"] = s_score - p_score
    return rec


#: 立場標籤的方向。未收錄的字串視為未知,**不猜**。
_DIRECTION = {"偏多": 1, "看多": 1, "多方": 1,
              "中性": 0, "觀望": 0, "中立": 0,
              "偏空": -1, "看空": -1, "空方": -1}


def _is_flip(a: str, b: str):
    """是否**翻面**(多 ↔ 空)。任一邊未知就回 None,不當成「沒翻面」。"""
    da, db = _DIRECTION.get(a), _DIRECTION.get(b)
    if da is None or db is None:
        return None
    return da * db < 0


def _round_or_none(v):
    try:
        return round(float(v), 1)
    except (TypeError, ValueError):
        return None


def load_ledger(path: Path) -> list:
    """讀帳本。**讀不出來就拋** —— 呼叫端不得代它把檔案清掉。

    這是本 repo 反覆出現的病灶(讀檔失敗被當成沒有資料,再被原子覆寫),
    影子帳本的價值全在於累積,一次誤覆寫就等於重新開始。
    """
    if not Path(path).exists():
        return []
    data = _json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"影子帳本格式非預期:{type(data).__name__}")
    for i, r in enumerate(data):
        if not isinstance(r, dict):
            raise ValueError(f"影子帳本第 {i} 列不是物件")
    return data


def upsert(ledger: list, record: dict, today: str) -> list:
    """把今天的紀錄併進帳本(同日同模型組合覆蓋),並修剪過期資料。"""
    rec = {"date": str(today), **record}
    key = tuple(str(rec.get(k) or "") for k in LEDGER_KEY_FIELDS)
    out = [r for r in (ledger or [])
           if tuple(str(r.get(k) or "") for k in LEDGER_KEY_FIELDS) != key]
    out.append(rec)
    try:
        cutoff = (_dt.date.fromisoformat(str(today))
                  - _dt.timedelta(days=LEDGER_KEEP_DAYS)).isoformat()
        out = [r for r in out if str(r.get("date") or "") >= cutoff]
    except (ValueError, TypeError):
        pass
    return sorted(out, key=lambda r: (str(r.get("date")), str(r.get("shadow_model"))))


#: 決定「哪些樣本可以放進同一個平均」的欄位(第九輪 P1-8)。
#:
#: prompt 的**內容**每天都不同(裡面是當天的行情與新聞),所以 `prompt_sha`
#: 是逐日證據、不是同群依據。真正會讓樣本不可比的是**設定**:換了模型或
#: 推理強度,輸出分佈就換了一個。
#: 第十四輪 P0-1:**走哪條路也是設定的一部分。** 特化路徑與落回 legacy
#: 是兩套完全不同的問法(專用 prompt + strict JSON vs 既有單段 prompt),
#: 混在同一個平均裡等於拿兩件事的結果回答一個問題。
COHORT_FIELDS = ("primary_model", "primary_effort",
                 "shadow_model", "shadow_effort", "code_version",
                 "analysis_origin")


def cohort_key(rec: dict) -> tuple:
    """一列紀錄屬於哪個同群。缺欄位的舊資料自成 `legacy/unknown` 群。"""
    return tuple(str((rec or {}).get(f) or "legacy/unknown")
                 for f in COHORT_FIELDS)


def summarize(ledger: list, shadow_model: str = "", cohort: Optional[tuple] = None) -> dict:
    """把帳本彙整成「夠不夠格換」的判讀。

    **樣本不足時明說「還不知道」**,不給一個看起來像結論的數字 ——
    這一輪已經有太多次「機制存在但沒有證據」被當成有結論。
    """
    rows = [r for r in (ledger or []) if isinstance(r, dict)
            and (not shadow_model or r.get("shadow_model") == shadow_model)]
    # 第十四輪 r1(Codex):**`legacy_observability_only` 要真的不參與判讀。**
    # 我在寫入端加了那個旗標並在註解裡寫「不得參與判讀或配對計數」,
    # 而這裡完全沒有實作它 —— 於是十筆之後這份帳本照樣吐出「可依品質偏好
    # 決定」,與權威帳本並列甚至相反。**宣稱與實作不符,示範贏的那一邊是實作。**
    # 排除不等於假裝沒發生:筆數仍然報出來(見 `observability_only_rows`)。
    observability_only = [r for r in rows if r.get("legacy_observability_only")]
    rows = [r for r in rows if not r.get("legacy_observability_only")]
    # **換了設定就重新算樣本數。**
    # 這裡刻意與 forecast ledger 的做法不同:那邊是「混算 + 誠實揭露」,
    # 因為它統計的是預測誤差、跨世代仍有參考價值;而影子的問題是
    # 「要不要換成這個設定」—— 用別的設定跑出來的樣本回答不了這個問題,
    # 混進去只會讓判讀提早到達門檻而給出沒有根據的結論。
    # 舊樣本不會被刪(它們仍在帳本裡、仍會被報出來),只是不進這次的分母。
    all_cohorts = {cohort_key(r) for r in rows}
    if cohort is not None:
        rows = [r for r in rows if cohort_key(r) == cohort]
    both_ok = [r for r in rows if r.get("primary_ok") and r.get("shadow_ok")]
    agree = [r for r in both_ok if r.get("stance_agree") is not None]
    flips = [r for r in both_ok if r.get("stance_flipped") is True]
    out = {"samples": len(rows), "both_ok": len(both_ok),
           "shadow_fail": sum(1 for r in rows if not r.get("shadow_ok"))}
    if observability_only:
        # 正式實驗在跑的那些天。**看得見,但不算數** —— 不報的話,
        # 「樣本不足」會看起來像沒跑過,而實際上是跑了但不歸這份帳本判讀。
        out["observability_only_rows"] = len(observability_only)
    if agree:
        out["stance_agree_rate"] = round(
            sum(1 for r in agree if r["stance_agree"]) / len(agree), 3)
        out["stance_flips"] = len(flips)
    gaps = [r["score_gap"] for r in both_ok if isinstance(r.get("score_gap"), int)]
    if gaps:
        out["score_gap_mean"] = round(sum(gaps) / len(gaps), 2)
        out["score_gap_abs_max"] = max(abs(g) for g in gaps)
    ov = [r["body_overlap"] for r in both_ok
          if isinstance(r.get("body_overlap"), (int, float))]
    if ov:
        out["body_overlap_mean"] = round(sum(ov) / len(ov), 3)
    if len(all_cohorts) > 1:
        # 揭露必須有可觀察的出口(照 forecast ledger 的 r1 教訓):
        # 只存在記憶體裡的警告等於沒有警告。
        out["cohorts"] = len(all_cohorts)
        out["cohort_fields"] = {
            f: sorted({k[i] for k in all_cohorts})
            for i, f in enumerate(COHORT_FIELDS)
            if len({k[i] for k in all_cohorts}) > 1}
    out["verdict"] = _verdict(out)
    return out


#: 判斷「樣本夠不夠」的下限。10 個交易日約兩週 —— 少於這個數字,
#: 立場一致率的分母太小,一兩天的差異就會把比率甩到極端。
MIN_SAMPLES_FOR_VERDICT = 10


def _verdict(stat: dict) -> str:
    if stat.get("both_ok", 0) < MIN_SAMPLES_FOR_VERDICT:
        extra = ""
        if stat.get("cohorts", 1) > 1:
            changed = "、".join(sorted(stat.get("cohort_fields") or {}))
            extra = (f";帳本另有其他設定的樣本(已變動:{changed}),"
                     "**不計入** —— 換設定等於換一個輸出分佈")
        return (f"樣本不足({stat.get('both_ok', 0)}/{MIN_SAMPLES_FOR_VERDICT})"
                f"——尚不足以判斷,繼續累積{extra}")
    if stat.get("stance_flips", 0) > 0:
        return (f"有 {stat['stance_flips']} 天立場翻面(多↔空)"
                "——換模型會改變讀者動作,需人工逐日核對那幾天誰對")
    rate = stat.get("stance_agree_rate")
    if rate is not None and rate >= 0.8:
        return "立場高度一致——差異主要在文字,可依品質偏好決定"
    return "立場常不一致但未翻面——建議再累積,並人工抽樣比較理由品質"


def compare_texts(*, primary_model: str, primary_text: str,
                  shadow_model: str, shadow_text: str, shadow_ok: bool,
                  shadow_elapsed, extract_stance, extract_summary,
                  primary_elapsed=None) -> dict:
    """從兩邊的**原始文字**直接組出比較紀錄(批#92)。

    立場與總結的擷取器由呼叫端注入(`llm_postprocess` 的那兩個),
    這樣本模組仍然不依賴主模組,而呼叫端也不必自己組那兩個 dict ——
    組錯欄位名的話 `compare_outputs` 會安靜地拿到 None,比較結果看起來正常
    但全是空的。
    """
    return compare_outputs(
        {"model": primary_model, "text": primary_text,
         "stance": extract_stance(primary_text),
         "summary": extract_summary(primary_text), "ok": True,
         # 第十輪 P2-5:原本寫死 None,於是帳本裡 `primary_seconds` 永遠是 null,
         # 而「比較兩個模型的延遲」正是影子的用途之一。
         "elapsed": primary_elapsed},
        {"model": shadow_model, "text": shadow_text,
         "stance": extract_stance(shadow_text) if shadow_ok else {},
         "summary": extract_summary(shadow_text) if shadow_ok else "",
         "ok": shadow_ok, "elapsed": shadow_elapsed})


def prompt_fingerprint(prompt: str) -> str:
    """prompt 的指紋(第九輪 P1-8/P2-5)。

    影子帳本累積的是**跨日的比較**,而 prompt 本身會隨改版而變 ——
    沒有指紋的話,「兩個模型的立場一致率下降」分不出是模型差異還是
    我們自己改了 prompt。指紋也讓「影子送的和主分析送的是不是同一份」
    變成可稽核的事實,而不是靠讀程式碼相信。
    """
    return _hashlib.sha256((prompt or "").encode("utf-8")).hexdigest()[:12]


def run_comparison(*, primary_model: str, primary_text: str, prompt: str,
                   shadow_model: str, call_shadow, today: str,
                   ledger_path, read_ledger, write_ledger,
                   extract_stance, extract_summary, elapsed_timer,
                   primary_effort: str = "", shadow_effort: str = "",
                   code_version: str = "", applied_effort_probe=None,
                   primary_elapsed=None, analysis_origin: str = "",
                   experiment_running: bool = False, log=print) -> dict:
    """跑一次影子比較並落地,回傳要放進 manifest 的狀態(批#92)。

    第九輪 P1-11 要的**依賴注入**:呼叫、讀寫、計時、擷取器全部由外面傳進來,
    所以本模組仍然不碰網路與檔案系統(可單獨測),而主模組只留薄接線。

    設計不變(照 MZ 影子):
      - **失敗只是今天沒有比較資料** —— 例外吞在呼叫端,這裡只記 ok/err
      - **帳本讀不出來就不寫** —— 覆蓋等於把樣本清空,而累積是它的全部價值
    """
    # 第九輪 P2-5:**影子把同一份 prompt 交給第二家廠商 —— 那是一個新的
    # 資料揭露決定。** 但影子必須送**同一份**才比較得出東西,所以正確的做法
    # 不是遮蔽(遮了就不是同一份),而是把「同一份」變成可稽核的不變式:
    # prompt 由這裡傳給 `call_shadow`,呼叫端無從偷偷換掉;兩邊的指紋一起
    # 進帳本。這樣主 prompt 既有的隱私防線(R15b、讀者身分、持股不落地)
    # 全部自動涵蓋影子,不必再維護第二套會漂移的規則。
    #
    # 詞彙掃描式的「敏感詞遮蔽」在這裡是錯的設計:持股代號在行情區塊本來就
    # 會出現,掃描不是永遠誤擋、就是要開一堆例外把自己掏空。
    t0 = elapsed_timer()
    shadow_text, ok, err = "", False, ""
    try:
        shadow_text = call_shadow(prompt)
        ok = bool(shadow_text)
    except Exception as e:                       # noqa: BLE001 - 影子不得影響正班
        err = f"{type(e).__name__}: {e}"
        log(f"[llm-shadow] 影子呼叫失敗(不影響晨報): {err}")
    rec = compare_texts(
        primary_model=primary_model, primary_text=primary_text,
        shadow_model=shadow_model, shadow_text=shadow_text, shadow_ok=ok,
        shadow_elapsed=elapsed_timer() - t0, primary_elapsed=primary_elapsed,
        extract_stance=extract_stance, extract_summary=extract_summary)
    rec["prompt_sha"] = prompt_fingerprint(prompt)
    # 同群欄位。**沒有它們,換了推理強度之後新舊樣本會被混進同一個平均**,
    # 而那個平均正是用來決定換不換模型的。
    rec["primary_effort"] = primary_effort or "unknown"
    # 呼叫之後才問「實際生效的是哪個強度」:API 可能拒絕並靜默退回預設,
    # 那時把 requested 寫進 cohort 等於用一個謊當分群依據。
    _applied = applied_effort_probe() if (ok and applied_effort_probe) else ""
    rec["shadow_effort"] = _applied or shadow_effort or "unknown"
    rec["code_version"] = (code_version or "unknown")[:12]
    # 第十四輪 P0-1:**`primary_ok` 量的是「有沒有東西可以寄」,不是
    # 「哪條路走成了」。** 2026-08-03 這兩件事分開了:特化路徑失敗、Luna 這個
    # 模型跑了 DeepSeek 的舊 prompt,而這份帳本記著 `primary_ok: true` +
    # `primary_model: gpt-5.6-luna` + `primary_effort: xhigh`。十天後翻帳本
    # 的人會讀成「Luna xhigh 成功」—— 那正是這個實驗要回答的問題。
    rec["analysis_origin"] = _ao.normalize(analysis_origin)
    if experiment_running:
        # 有正式實驗帳本在跑的時候,這一份只是觀測用 —— 不得參與判讀或配對計數。
        rec["legacy_observability_only"] = True
    if err:
        rec["shadow_error"] = err[:160]
    try:
        ledger = read_ledger(ledger_path)
    except Exception as e:                       # noqa: BLE001
        log(f"[llm-shadow] 帳本不可讀,本次不寫入: {e}")
        return {"skipped": "ledger_unreadable"}
    ledger = upsert(ledger, rec, today)
    write_ledger(ledger_path, ledger)
    return {"today": rec,
            "cumulative": summarize(ledger, shadow_model,
                                    cohort=cohort_key(rec))}
