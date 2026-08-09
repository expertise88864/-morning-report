# -*- coding: utf-8 -*-
"""**昨日觀點的閉環**(分析面縱深:延續事件要寫增量,不是重述)。

## 缺口

prompt 早就要求「延續中的事件要寫增量」—— 但特化路徑的模型只知道
`continuing_days = N`,**不知道本報昨天對這件事說了什麼**。沒有 diff 的
對象,「寫增量」就是一句無法執行的要求;驗證器也驗不了「今天是不是把
背景再講一次」。legacy prompt 有 `_format_narrative_delta`(昨日立場 +
五條標題),特化路徑什麼都沒有。

## 閉環三段

    分析成功 → `save()` 把 key_drivers 的(敘述, 方向, 實體)存進 state
      → 明天 `load()` + `view_for()` 把昨日觀點掛在對應的事件群上
      → `overlap()` 給 depth advisory 驗「重述度」,過高就用加深額度重寫

## 設計取捨

* **只存 key_drivers(首屏三條)**:它們是信裡最載重的判斷,也是
  「昨天的觀點」該指的東西。整份分析都存的話,明天的 packet 會被
  昨天的長文擠占預算。
* **以實體比對,不以 cluster_id**:cluster_id 是「群裡最小的
  source_item_id」,明天必然不同。實體 + 別名組(`entity_alias`)
  是跨日仍然穩定的身分 —— 與 `continuing_days` 用同一套比對哲學。
* **同日重跑不得自比**(`usable` 的日期守衛):手動 dispatch 會把
  「今天早上」存進 state,不濾掉就會拿今天比今天,產生假的
  「昨日觀點」—— legacy 的 `_format_narrative_delta` 已經踩過這個洞。
* **存檔失敗不斷晨報**:遞迴帳(昨日觀點)是加深,不是核心;
  但失敗要印出來,靜默的失敗明天才發現就晚了。
"""
from __future__ import annotations

import json
import sys

#: 一則觀點存這麼多字。**存的是判斷,不是全文** —— 太長會擠占明天的
#: payload 預算,而「昨天說了什麼」的重點在方向與量級,不在修辭。
STATEMENT_CHARS = 160

#: 觀點總數上限。首屏三條之外,次要事件(top_news_analysis)也存 ——
#: 但整份都存的話,明天的 packet 會被昨天的長文擠占預算;超過上限時
#: **主要的先留**(items 依重要性排序,截尾不截頭)。
MAX_ITEMS = 12

#: 重述門檻:今天敘述的 token 有六成以上昨天就說過,視為重述。
#: 訂 0.6 不是量出來的 —— 主體與事件名本來就會重複(那部分合理),
#: 六成以上代表連判斷句都在重複。外審可挑戰。
RESTATEMENT_OVERLAP = 0.6

#: 方向代碼 → 中文(進 packet 給模型看,也給 advisory 引用)。
_DIRECTION_ZH = {"bullish": "偏多", "bearish": "偏空", "neutral": "中性",
                 "mixed": "多空並陳"}


def extract(analysis_obj, packet) -> dict:
    """從**通過驗證的**分析物件抽出要存的觀點(純函式,不碰檔案)。

    實體從 packet 的事件群成員收集 —— key_driver 只帶 `cluster_id`,
    而 cluster_id 明天就換號;實體才是跨日的身分。

    **次要事件也存**(`top_news_analysis` 的 `why_it_matters`):延燒到
    第三天的事件常常已經不在首屏 —— 只存首屏的話,恰恰是最需要 diff
    基準的長尾事件沒有基準。**每個事件群只存一則**,首屏的優先
    (兩者講同一件事時,首屏那句才是本報的正式判斷)。
    """
    obj = analysis_obj if isinstance(analysis_obj, dict) else {}
    pk = packet if isinstance(packet, dict) else {}
    by_id = {str(n.get("source_item_id")): n for n in (pk.get("news") or [])
             if isinstance(n, dict)}
    clusters = [c for c in ((pk.get("news_clusters") or {}).get("clusters")
                            or []) if isinstance(c, dict)]
    members_of = {str(c.get("cluster_id") or ""):
                  [str(m) for m in (c.get("member_source_ids") or [])]
                  for c in clusters}
    cluster_of = {m: cid for cid, ms in members_of.items() for m in ms}

    import event_identity as _eid

    def _ents(member_ids) -> list:
        """**與 timeline 同一套正規化**(第四輪外審 F2)。存原文拼寫的話,
        明天英文報導寫 `Iran`、昨天存「伊朗」,對象簽章就對不上 ——
        而對象現在是硬判準,對不上等於整條線索失去 diff 基準。
        身分的正規化只做一半,比完全不做更難查。"""
        return sorted({_eid.canonical_subject(str(e)) for m in member_ids
                       for e in (by_id.get(m, {}).get("entities") or [])
                       if str(e).strip()})

    items, seen = [], set()

    eligible = [0]

    def _add(stmt: str, direction, cluster_id: str, fallback_sid: str = ""):
        stmt = str(stmt or "").strip()
        if stmt:
            # **候選數要記**(第二十九輪外審 P2-3):`nothing_to_save`
            # 蓋得住兩種完全不同的日子 ——「今天真的沒東西」與
            # 「有東西但 mapping 壞掉一條都抽不出來」。少了分子分母,
            # strict canary 對後者也是綠的(真空通過的另一個形狀)。
            eligible[0] += 1
        if not stmt or (cluster_id and cluster_id in seen):
            return
        ents = _ents(members_of.get(cluster_id, [])) or (
            _ents([fallback_sid]) if fallback_sid else [])
        if not ents:
            return          # 明天接不回來的觀點是死重量,不存
        seen.add(cluster_id or f"__{len(items)}")
        # **實體不足以當事件身分**(外審補審 F3):同一家公司昨天可以
        # 同時有法說會與擴廠兩件事,只比實體會把擴廠今天的敘述配到
        # 法說會的觀點上,模型被要求對無關的觀點寫「強化/轉弱/翻轉」。
        # 一併存動作與代表標題 —— 讀取端要兩層都對得上才算同一件事。
        title = " ".join(str(by_id.get(m, {}).get("title") or "")
                         for m in members_of.get(cluster_id, [])) or (
            str(by_id.get(fallback_sid, {}).get("title") or ""))
        _act = _eid.event_action(title)
        items.append({"statement": stmt[:STATEMENT_CHARS],
                      "direction": str(direction or ""),
                      "entities": ents,
                      "action": _act,
                      # **動作不等於身分**(第三輪外審 F2):`sanction`
                      # 這種帶對象的動作,「制裁伊朗」與「制裁俄羅斯」
                      # 是兩件事,而它們共用主體「美國」與同一個動作碼。
                      "object": _eid.object_signature(_act, ents),
                      "title": title[:STATEMENT_CHARS]})

    for d in (obj.get("key_drivers") or []):
        if isinstance(d, dict):
            _add(d.get("statement"), d.get("direction"),
                 str(d.get("cluster_id") or ""))
    for n in (obj.get("top_news_analysis") or []):
        if isinstance(n, dict):
            sid = str(n.get("source_item_id") or "")
            _add(n.get("why_it_matters"), n.get("direction"),
                 cluster_of.get(sid, ""), fallback_sid=sid)
    return {"date": str(pk.get("target_session_date") or ""),
            "eligible": eligible[0],
            "items": items[:MAX_ITEMS],
            "watch": _watch_of(obj)}


#: 觀察點最多留幾條、每格幾字。上限跟 items 一樣是 payload 紀律 ——
#: 回顧要逐條,存十條等於逼明天的分析寫十條回顧。
WATCH_MAX = 5
WATCH_CHARS = 120


def _watch_of(obj) -> list:
    """今天信裡的觀察點(`watch_triggers`),要留給明天回顧的形狀。

    **預期→結果的閉環**(縱深第四批 D):觀察點先前寫進信裡就被遺忘
    —— schema 有寫入端(watch_triggers),沒有任何東西隔天回頭問
    「觸發了沒」。這裡是閉環的存檔面;隔天由 `usable_watch` 派代號、
    packet 的 `yesterday_watch` 進 prompt、validator 驗逐條回顧。
    """
    out = []
    for w in (obj.get("watch_triggers") or []):
        if not isinstance(w, dict):
            continue
        trig = str(w.get("trigger") or "").strip()
        if not trig:
            continue
        out.append({"trigger": trig[:WATCH_CHARS],
                    "why": str(w.get("why") or "").strip()[:WATCH_CHARS],
                    "horizon": str(w.get("horizon") or "")[:16]})
        if len(out) >= WATCH_MAX:
            break
    return out


#: `save()` 的三種結果。**「沒東西可存」不是「存檔失敗」**
#: (2026-08-09 P2):上一版兩者都回 `False`,而下游把 `False` 報成
#: defect「分析成功但昨日觀點沒存下來」—— 那句話在資料稀薄的日子是假的,
#: 而假的缺陷會讓人去查一個沒有壞的東西。
SAVED = "saved"
NOTHING = "nothing_to_save"
FAILED = "failed"


def _carry_origins(rec: dict, prior: dict) -> None:
    """**首見判斷逐日 carry**(縱深第四批)。

    檔案只留最新一天,於是「當初的預期」在第二天就沒了 —— 而線索延燒到
    第三天時,「昨天說什麼」不足以寫出「當初預期 → 應驗/落空」。
    每次存檔前,把今天的每一條觀點接回昨天那一條(**同一份身分判準**:
    `best_view` —— 主體相交 + 事件層一致,模稜兩可不接),接得上就把
    首見帶過來:昨天那條已有 `origin` → 沿用(首見永遠是**最早**那天,
    不是前一天);沒有 → 昨天那條自己就是首見。

    **同日重跑只沿用、不建立**:拿今天的重跑當首見,「首見」與「今天」
    是同一天,那不是任何東西的起點。日期倒退(時鐘錯亂)整批不接。
    """
    pdate = str((prior or {}).get("date") or "")
    ndate = str(rec.get("date") or "")
    if not pdate or not ndate or pdate > ndate:
        return
    prior_items = [it for it in (prior.get("items") or [])
                   if isinstance(it, dict)]
    if not prior_items:
        return
    for it in rec["items"]:
        hit = best_view(it.get("entities"), prior_items,
                        titles=str(it.get("title") or ""))
        if not hit:
            continue
        origin = hit.get("origin") if isinstance(hit.get("origin"), dict)             else None
        if origin:
            it["origin"] = {"date": str(origin.get("date") or "")[:10],
                            "statement": str(origin.get("statement")
                                             or "")[:STATEMENT_CHARS],
                            "direction": str(origin.get("direction") or "")}
        elif pdate < ndate:
            it["origin"] = {"date": pdate[:10],
                            "statement": str(hit.get("statement")
                                             or "")[:STATEMENT_CHARS],
                            "direction": str(hit.get("direction") or "")}


def save(path, analysis_obj, packet, manifest=None) -> str:
    """把今天的觀點寫進 state(**只留最新一天** —— 昨日觀點只需要
    上一次的)。回 `SAVED` / `NOTHING` / `FAILED`,不拋 ——
    晨報不可因加深而斷。"""
    try:
        rec = extract(analysis_obj, packet)
        if isinstance(manifest, dict):
            slot = manifest.setdefault("llm", {})
            slot["recap_eligible"] = int(rec.get("eligible") or 0)
            slot["recap_extracted"] = len(rec["items"])
        if not rec["items"] and not rec.get("watch"):
            # 今天的分析裡沒有值得留給明天的觀點**也沒有觀察點** ——
            # 那是正常的答案,不是寫檔壞了。觀點空、觀察點不空的日子
            # 仍要存:回顧的閉環不能因為當天觀點稀薄就斷一天。
            return NOTHING
        _carry_origins(rec, load(path))
        import pathlib
        p = pathlib.Path(str(path))
        p.parent.mkdir(parents=True, exist_ok=True)
        # 壞檔先留副本再覆寫(外審補審 F7)—— 覆寫掉就查不出昨天
        # 為什麼壞了,而那正是隔天要診斷的東西。
        # **只碰普通檔案**:第一版寫 `p.exists()`,而路徑是目錄時
        # 「讀不動」也成立 → 直接把那個目錄改名。修 F7 的動作本身
        # 變成破壞性操作(測試當場把 pytest 的 tmp 目錄搬走)。
        # 這個 repo 記過:一個修正可能比原本的缺陷更糟。
        if p.is_file():
            try:
                json.loads(p.read_text(encoding="utf-8"))
            except Exception:               # noqa: BLE001
                p.replace(p.with_suffix(".json.corrupt"))
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(rec, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        tmp.replace(p)
        return SAVED
    except Exception as e:                  # noqa: BLE001 - 加深不可斷晨報
        print(f"[recap] 昨日觀點存檔失敗(不影響晨報):{e}", file=sys.stderr)
        return FAILED


def load(path) -> dict:
    """讀 state。**「沒有檔案」與「檔案壞了」是兩件事**(外審補審 F7)。

    先前兩者都靜靜回 `{}`,然後今天的 `save()` 把壞檔原子覆寫掉 ——
    連「昨天壞過」都查不到。現在壞檔回 `{"unreadable": ...}`:
    昨日觀點一樣不可用(降級相同),但 `save()` 會先留一份 `.corrupt`
    副本,而呼叫端看得出兩者的差別。
    """
    import pathlib
    p = pathlib.Path(str(path))
    if not p.is_file():
        return {}          # 不存在、或根本不是檔案 —— 都當成「沒有昨日觀點」
    try:
        return json.loads(p.read_text(encoding="utf-8")) or {}
    except Exception as e:                  # noqa: BLE001
        print(f"[recap] 昨日觀點 state 讀不動({e});今日照常產生新的,"
              f"壞檔會另存 .corrupt", file=sys.stderr)
        # **不把原始例外字串放進去**(第二輪外審 F5):這個 dict 會進
        # `quotes["ANALYSIS_RECAP"]` → packet → prompt。例外訊息含路徑
        # 與內文片段,既是雜訊也是一條不必要的注入面。
        # 旗標給呼叫端記 degraded 用,`items` 空 = 降級行為與缺檔相同。
        return {"unreadable": True, "items": []}


def usable_watch(recap, target_session_date: str) -> list:
    """昨天的觀察點,配上 Python 派的代號(`w1`…)。

    與 `usable` 同一條日期閘門:**同日重跑不得自比** —— 拿今天剛寫的
    觀察點當「昨天的預期」回顧,每一條都會「已觸發」(它就是照今天
    的新聞寫的)。代號在這裡派而不是讓模型自報 —— 回顧要能逐條對帳,
    帳本的鍵就得是我們發的。
    """
    r = recap if isinstance(recap, dict) else {}
    date = str(r.get("date") or "")
    if not date or not target_session_date or date >= str(target_session_date):
        return []
    ws = [w for w in (r.get("watch") or [])
          if isinstance(w, dict) and str(w.get("trigger") or "").strip()]
    return [{"watch_id": f"w{i + 1}", "date": date,
             "trigger": str(w.get("trigger") or ""),
             "why": str(w.get("why") or ""),
             "horizon": str(w.get("horizon") or "")}
            for i, w in enumerate(ws[:WATCH_MAX])]


def usable(recap, target_session_date: str) -> list:
    """**同日重跑不得自比。** 只有日期**早於**今天交易日的觀點可用 ——
    等於今天的是同日重跑寫進去的,拿它比就是「今天比今天」,
    會產生假的強化/推翻。晚於今天的是時鐘或資料錯亂,同樣不可用。"""
    r = recap if isinstance(recap, dict) else {}
    date = str(r.get("date") or "")
    if not date or not target_session_date or date >= str(target_session_date):
        return []
    return [dict(it, date=date) for it in (r.get("items") or [])
            if isinstance(it, dict) and str(it.get("statement") or "").strip()]


#: 昨日觀點與今天的事件群要對到這個標題重疊度才算同一件事。
#: 比分群的 0.5 寬(跨日報導用詞差異較大),但**不是零** ——
#: 零就退回「同公司即同事件」,那正是外審補審 F3 的缺陷。
VIEW_TITLE_OVERLAP = 0.3


def best_view(entities, items, titles: str = ""):
    """對得上的那一筆觀點(對不上、或**分不出來**時回 `None`)。

    兩層判準(外審補審 F3):
      1. 主體相交(精確或別名組)—— 與 `continuing_days` 同一套哲學,
         「台積電」的觀點要接得上明天寫「TSMC」的群;
      2. **事件層一致** —— 動作相同,或標題重疊過門檻。

    **模稜兩可時回 `None`。** 兩筆同分代表身分分不出來,而「沒有基準」
    只是少一次 diff,「配到別件事的觀點」會讓模型對無關的判斷寫
    強化/轉弱/翻轉 —— 兩種錯誤的代價不對稱。

    抽成獨立函式(縱深第四批):`save()` 的**首見判斷 carry** 要用
    同一份身分判準 —— 各寫一份的話,「昨天接得上、首見卻接不上」
    這種分歧沒有人查得出來。
    """
    import entity_alias as _ea
    import event_identity as _eid
    # 兩側同一套正規化(第四輪外審 F2)—— 存的是 canonical,
    # 今天的實體是原文拼寫;不正規化的話跨語言的續篇會失去基準。
    ents = {_eid.canonical_subject(str(e)) for e in (entities or ())
            if str(e).strip()}
    keys = _ea.expand(ents)
    today_action = _eid.event_action(titles)
    scored = []
    for it in (items or []):
        theirs = {str(e) for e in (it.get("entities") or [])}
        if not (ents & theirs or (keys & _ea.expand(theirs))):
            continue
        their_action = str(it.get("action") or "")
        # **對象不同就是兩件事,標題再像也一樣**(第四輪外審 F1)。
        # 「美國宣布對伊朗新一輪經濟制裁措施」與同一句話換成俄羅斯 ——
        # 去掉主體之後剩下的詞幾乎完全相同,標題那條路會翻案。
        # 已知且不同 → 直接排除;算不出來才退回標題辨識。
        their_obj = str(it.get("object") or "")
        my_obj = _eid.object_signature(their_action or today_action, ents)
        if my_obj and their_obj and my_obj != their_obj:
            continue
        # **只用辨識詞比**(第二輪外審 F2):主體相交已經在上一行判過,
        # 標題重疊若又被主體名與「宣布」這類套語灌滿,等於把同一份
        # 證據算兩次 —— 「台積電宣布法說會」與「台積電宣布擴建新廠」
        # 的共同詞正好全部是這一類(重疊 0.50,越過 0.3 門檻)。
        subj = ents | theirs
        a = _eid.discriminative_tokens(titles, subj)
        b = _eid.discriminative_tokens(it.get("title"), subj)
        overlap = (len(a & b) / min(len(a), len(b))
                   if len(a) >= _eid.MIN_DISCRIMINATIVE
                   and len(b) >= _eid.MIN_DISCRIMINATIVE else 0.0)
        action_match = bool(today_action) and today_action == their_action
        if action_match and today_action in _eid.NEEDS_OBJECT:
            # 帶對象的動作:動作相同還不夠,對象也要相同(第三輪外審 F2)。
            # 算不出對象一律不當作動作命中 —— 退回標題辨識詞那一關。
            action_match = bool(my_obj) and my_obj == their_obj
        if not action_match and overlap < VIEW_TITLE_OVERLAP:
            continue
        scored.append(((1 if action_match else 0, round(overlap, 3)), it))
    if not scored:
        return None
    scored.sort(key=lambda x: x[0], reverse=True)
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        return None          # 分不出來就不給基準
    return scored[0][1]


def _fmt_view(date, direction, stmt, label, sanitize=None) -> str:
    zh = _DIRECTION_ZH.get(str(direction or ""), "")
    body = str(stmt or "")
    if callable(sanitize):
        body = str(sanitize(body))
    return (f"{date}{label}" + (f"({zh})" if zh else "")
            + f":{body}")[:STATEMENT_CHARS + 40]


def view_for(entities, items, sanitize=None, titles: str = "") -> str:
    """`best_view` 的字串出口(給 packet 的 `yesterday_view`)。

    **只有昨天那一句** —— 首見走 `origin_view_for`(另一個欄位)。
    第一版把首見串進同一個字串,而 `restatements()` 拿整串
    `yesterday_view` 算重疊:模型**正確**回顧當初預期(應驗/落空)時,
    敘述必然與首見高度重疊 → 被誤判成重述、耗掉唯一一次加深呼叫
    (外審抓到)。渲染與重述檢查要各看各的欄位。
    """
    it = best_view(entities, items, titles=titles)
    if not it:
        return ""
    return _fmt_view(it.get("date", ""), it.get("direction"),
                     it.get("statement"), "本報", sanitize)


def origin_view_for(entities, items, sanitize=None, titles: str = "") -> str:
    """這個事件群的**首見判斷**(沒有、或首見就是昨天時回空字串)。

    縱深第四批:線索延燒到第三天時,「昨天說什麼」不足以寫出
    「當初預期 → 應驗/落空」—— 那需要**最初**那一天的判斷,而檔案只留
    最新一天,首見靠 `save()` 逐日 carry(`origin` 欄位)。
    """
    it = best_view(entities, items, titles=titles)
    if not it:
        return ""
    origin = it.get("origin") if isinstance(it.get("origin"), dict) else None
    if not origin or str(origin.get("date") or "") == str(it.get("date")
                                                         or ""):
        return ""
    return _fmt_view(origin.get("date", ""), origin.get("direction"),
                     origin.get("statement"), "首見", sanitize)


def restatements(analysis_obj, packet) -> list:
    """**與昨日觀點高度重複的敘述**(主要 + 次要事件),給 depth
    advisory 用。本體放這裡而不是 `analysis_depth`:重述的定義
    (存什麼、比什麼、門檻)整組屬於同一個閉環,拆兩處會各自漂移。

    次要事件比的是 `why_it_matters` —— 延燒事件掉出首屏之後,
    重述最常發生在這裡(首屏有三條的位置壓力,次要段沒有)。
    """
    obj = analysis_obj if isinstance(analysis_obj, dict) else {}
    pk = packet if isinstance(packet, dict) else {}
    clusters = [c for c in ((pk.get("news_clusters") or {}).get("clusters")
                            or []) if isinstance(c, dict)]
    yv = {str(c.get("cluster_id") or ""): str(c.get("yesterday_view") or "")
          for c in clusters}
    cluster_of = {str(m): str(c.get("cluster_id") or "") for c in clusters
                  for m in (c.get("member_source_ids") or [])}
    out = []

    def _check(where: str, stmt, cid: str):
        v = yv.get(cid, "")
        if v and overlap(stmt, v) >= RESTATEMENT_OVERLAP:
            out.append(f"{where} 的敘述與昨日觀點高度重複 —— 延續事件要"
                       "寫增量:強化/轉弱/翻轉之一,附**今天的**新證據,"
                       "不要把昨天的判斷再說一次")

    for d in (obj.get("key_drivers") or []):
        if isinstance(d, dict):
            _check(f"key_drivers 對 {d.get('cluster_id')}",
                   d.get("statement"), str(d.get("cluster_id") or ""))
    for i, n in enumerate(obj.get("top_news_analysis") or []):
        if isinstance(n, dict):
            _check(f"top_news_analysis[{i}]", n.get("why_it_matters"),
                   cluster_of.get(str(n.get("source_item_id") or ""), ""))
    return out


def overlap(statement, yesterday_view) -> float:
    """今天的敘述與昨日觀點的重述度(0~1)。

    給 depth advisory 用的**結構性**判準:借分群的 token 化
    (中文二元組 + 英文詞),算今天敘述被昨日觀點覆蓋的比例 ——
    量的是「有多少字昨天就說過」,不是語意。門檻由呼叫端訂。
    """
    from news_clusters import _tokens
    today = _tokens(str(statement or ""))
    yest = _tokens(str(yesterday_view or ""))
    if not today or not yest:
        return 0.0
    return len(today & yest) / len(today)
