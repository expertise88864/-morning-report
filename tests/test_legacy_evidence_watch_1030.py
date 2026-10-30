"""使用者 2026-10-30:改回備援為主之後,「備援如何增加證據正確性/觀察點跨日追蹤/逐句證據核對」。
選定:逐句證據核對(先觀察一週只記錄、不刪)+ 觀察點跨日追蹤。"""
import json
from pathlib import Path

import pytest

import analysis_origin as ao
import fallback_recap_runtime as frr
import legacy_actor_guard as lag
import legacy_evidence as le
import legacy_watch as lw
import reader_prose
import run_quality as rq

N1 = {"title": "台積電10月營收年增31.2% - 經濟日報", "link": "https://news.example/tsmc",
      "source_name": "經濟日報", "summary": "合併營收 3,300 億元"}
N2 = {"title": "聯發科新晶片量產 - 鉅亨", "link": "https://news.example/mtk"}
QUOTES = {"MACRO": {"QQQ": {"change_pct": -3.4}}}


@pytest.fixture(autouse=True)
def _fresh_registry():
    le.reset()
    lw._PENDING.clear()
    yield
    le.reset()
    lw._PENDING.clear()


def _register():
    assert le.tag(N1) == "[N1] " and le.tag(N2) == "[N2] " and le.tag(dict(N1)) == "[N1] "


TEXT = ("## 八、科技板塊脈動\n"
        "- 台積電營收年增 31.2%、達 3,300 億元 [經濟日報][N1]\n"
        "- 那斯達克重挫,QQQ 跌 3.4% [CNBC][N2]\n"
        "- 聯發科營收大增 50% [鉅亨][N2]\n"
        "- 市場傳出某大廠砍單 [鉅亨][N99]\n"
        "- 這是一條沒有附編號的條目\n- ---\n\n"
        "## 十一、我的明確立場\n立場：中性 [N1]\n")


def test_ids_become_source_links_and_unsupported_items_are_reported():
    _register()
    manifest = {}
    out = le.audit(TEXT, QUOTES, manifest)
    assert "[N" not in out
    assert "[經濟日報](https://news.example/tsmc)" in out and "[CNBC](https://news.example/mtk)" in out
    assert "某大廠砍單 [鉅亨]" in out   # 編號錯了:連結拿掉、媒體名留著
    report = manifest["llm"]["legacy_evidence"]
    assert report["checked"] == 5 and report["failed"] == 3 and report["observe_only"] is True
    assert any("50%" in e for e in report["examples"])
    # 觀察期:不通過的條目仍然留在信裡
    assert "聯發科營收大增 50%" in out and "沒有附編號的條目" in out and "- ---" in out   # 分隔線不算一條
    finding = le.findings(manifest)[0]
    assert finding[0] == "legacy_evidence_unverified" and "3／5" in finding[2] and "觀察期" in finding[2]


def test_after_the_observation_week_failing_items_are_dropped(monkeypatch):
    _register()
    monkeypatch.setattr(le, "OBSERVE_ONLY", False)
    manifest = {}
    out = le.audit(TEXT, QUOTES, manifest)
    assert "聯發科營收大增" not in out and "砍單" not in out and "沒有附編號" not in out
    assert "台積電營收年增 31.2%" in out and "QQQ 跌 3.4%" in out
    assert manifest["llm"]["legacy_evidence"]["dropped"] == 3


def test_rounded_numbers_match_but_a_wrong_decimal_does_not():
    le.tag({"title": "某公司毛利率 12.6%", "link": "https://news.example/a"})
    ok = le.audit("## 九、其他類股資訊\n- 某公司第三季毛利率約 13%,優於預期 [鉅亨][N1]\n", {}, m1 := {})
    bad = le.audit("## 九、其他類股資訊\n- 某公司第三季毛利率 12.9%,優於預期 [鉅亨][N1]\n", {}, m2 := {})
    assert m1["llm"]["legacy_evidence"]["failed"] == 0 and m2["llm"]["legacy_evidence"]["failed"] == 1
    assert "[鉅亨](https://news.example/a)" in ok and "[N1]" not in bad


def _failed(line: str) -> int:
    manifest = {}
    le.audit("## 九、其他類股資訊\n" + line + "\n", {"MACRO": {"TWII": {"close": 23000}}}, manifest)
    return manifest["llm"]["legacy_evidence"]["failed"]


def test_review_findings_pr34_ids_units_and_adjacent_numbers():
    # 觀測站公告共用同一個網址:不同公告要拿到不同編號,核對時看的是自己那則
    mops = "https://mops.twse.com.tw/mops/#/web/t05st01"
    assert le.tag({"code": "2881", "title": "富邦人壽發行次順位債 400 億元", "link": mops}) == "[N1] "
    assert le.tag({"code": "2882", "title": "國泰人壽取得不動產 50 億元", "link": mops}) == "[N2] "
    assert _failed("- 國泰人壽公告取得不動產 50 億元 [MOPS][N2]") == 0
    assert _failed("- 國泰人壽公告取得不動產 400 億元 [MOPS][N2]") == 1   # 那是 N1 的數字
    # 數字緊貼中文也要抓到;單位(億／兆、元／美元、%)要對得上
    le.tag({"title": "聯發科營收年增 20%,達 3,300 億元", "link": "https://news.example/m"})
    assert _failed("- 聯發科營收大增50%,表現亮眼 [鉅亨][N3]") == 1
    assert _failed("- 聯發科營收達3,300兆元,表現亮眼 [鉅亨][N3]") == 1
    assert _failed("- 聯發科營收達 3,300 美元,表現亮眼 [鉅亨][N3]") == 1
    assert _failed("- 聯發科營收年增20%、達3,300億元 [鉅亨][N3]") == 0
    # 行情資料只對沒有量級的價格與漲跌幅
    assert _failed("- 加權指數收在 23,000 點附近,收 23,000 元 [鉅亨][N3]") == 0
    assert _failed("- 加權指數成交 23,000 億元,創新高 [鉅亨][N3]") == 1


def test_english_sources_back_their_chinese_rendering():
    """PR #34 外審第二輪:英文標題的「$200」「US$38 billion」「3 percent」轉述成中文也要對得上。"""
    le.tag({"title": "Streaming service raises plan to $200 a month - CNBC", "link": "https://news.example/a"})
    le.tag({"title": "SK Hynix to spend US$38 billion on new fabs, shares up 3 percent",
            "link": "https://news.example/b"})
    assert _failed("- 串流平台把方案月費調到200美元 [CNBC][N1]") == 0
    assert _failed("- SK海力士砸380億美元建廠,股價漲3% [CNBC][N2]") == 0
    assert _failed("- SK海力士砸380億元建廠,市場關注 [CNBC][N2]") == 1   # 美元不能替新台幣背書
    assert _failed("- 串流平台把方案月費調到 300 美元 [CNBC][N1]") == 1


def test_counts_never_back_money_claims():
    """PR #34 外審第三輪:「200 million users」「2億用戶」是數量,不是金額。"""
    le.tag({"title": "App reaches 200 million users; revenue hits $500 million", "link": "https://news.example/a"})
    le.tag({"title": "平台用戶突破2億用戶", "link": "https://news.example/b"})
    assert _failed("- 平台營收衝上2億美元,成長強勁 [CNBC][N1]") == 1
    assert _failed("- 平台營收衝上5億美元、用戶達2億人 [CNBC][N1]") == 0
    assert _failed("- 平台營收已達2億元,市場驚艷 [經濟日報][N2]") == 1
    assert _failed("- 平台用戶已經達到2億用戶 [經濟日報][N2]") == 0


def test_only_the_legacy_path_is_audited_and_quality_alert_carries_it():
    _register()
    manifest = {}
    legacy = lag.correct_reader_claims(TEXT, [], origin=ao.LEGACY_PRIMARY, manifest=manifest, quotes=QUOTES)
    assert "[N1]" not in legacy and "legacy_evidence" in manifest["llm"]
    luna = lag.correct_reader_claims("## 八、科技\n- x [N1]\n", [], origin=ao.LUNA_SPECIALIZED, manifest={})
    assert "[N1]" in luna   # 特化路徑沒有編號制度,不動
    manifest["llm"]["analysis_origin"] = ao.LEGACY_PRIMARY
    assert "legacy_evidence_unverified" in {f["code"] for f in rq.assess(manifest)}


def _recap(tmp_path, watch):
    path = tmp_path / "analysis_recap.json"
    path.write_text(json.dumps({"date": "2026-10-29", "items": [], "watch": watch, "watch_seq": 4},
                               ensure_ascii=False), encoding="utf-8")
    return path


def _w(wid, trigger, created="2026-10-28", deadline="2026-11-05"):
    return {"watch_id": wid, "trigger": trigger, "trigger_full": trigger, "status": "open",
            "created": created, "deadline": deadline, "last_reviewed": "", "horizon": "1-5d"}


def test_prompt_lists_due_watches_inside_the_untrusted_fence(tmp_path):
    import analysis_recap as recap
    path = _recap(tmp_path, [_w("w3", "費半跌破 5000 點"), _w("w4", "今天才開的", created="2026-10-31")])
    block = lw.prompt_block({"ANALYSIS_RECAP": recap.load(path), "TARGET_SESSION": "2026-10-31"}, str)
    assert "## 觀察觸發點" in block and "## 昨日觀察點回顧" in block
    fenced = block.split("<UNTRUSTED_SOURCE_DATA>")[1]
    assert "[w3] 費半跌破 5000 點" in fenced and "w4" not in block   # 同日建立的不回顧
    assert "昨日觀察點回顧" not in lw.prompt_block({}, str)   # 沒有待回顧就只要求寫新觀察點


REPLY = ("## 昨日觀察點回顧\n"
         "- [w3] 已觸發:費半昨夜收 4,950 點 [CNBC][N2]\n"
         "- [w4] 已觸發:好像有發生\n"
         "## 觀察觸發點\n"
         "- 美債 10 年期殖利率站上 4.5%(觀察期:5 天內)\n"
         "- 台積電法說後外資連續三日賣超逾 1 萬張(觀察期:4 週內)\n")


def test_reviews_need_a_real_news_id_to_close_a_watch():
    _register()
    parsed = lw.parse(REPLY)
    assert parsed["watch_review"][0] == {"watch_id": "w3", "status": "triggered", "evidence_ids": ["N2"],
                                         "what_happened": "已觸發:費半昨夜收 4,950 點 [CNBC]"}
    assert parsed["watch_review"][1]["status"] == "insufficient_evidence"
    assert [t["horizon"] for t in parsed["watch_triggers"]] == ["1-5d", "1-4w"]


def test_delivered_letter_updates_the_watch_ledger(tmp_path):
    _register()
    path = _recap(tmp_path, [_w("w3", "費半跌破 5000 點"), _w("w4", "台幣升破 30 元")])
    le.audit(REPLY, {}, {})   # 寄信前:核對時接住回顧與新觀察點
    manifest = {}
    frr.delivered("<html></html>", {"origin": ao.LEGACY_PRIMARY, "date": "2026-10-31", "text": "", "news": []},
                  str(path), manifest, lambda p, text: Path(p).write_text(text, encoding="utf-8"))
    saved = json.loads(path.read_text(encoding="utf-8"))
    ids = {w["watch_id"]: w for w in saved["watch"]}
    assert "w3" not in ids                                   # 有新聞佐證的「已觸發」→ 關閉
    assert ids["w4"]["last_reviewed"] == "2026-10-31"         # 證據不足 → 留著
    new = [w for w in saved["watch"] if w["watch_id"] not in ("w4",)]
    assert [w["watch_id"] for w in new] == ["w5", "w6"] and new[0]["deadline"] == "2026-11-05"
    assert manifest["llm"]["legacy_watch_saved"] == "saved"
    assert manifest["llm"]["legacy_watch"]["reviewed"] == 2


def test_review_stays_out_of_the_letter_and_triggers_join_the_outlook():
    shown = reader_prose.public_sections(REPLY + "## 我的明確立場\n立場：中性\n")
    assert "昨日觀察點回顧" not in shown and "費半昨夜收" not in shown
    assert "美債 10 年期殖利率站上 4.5%" in shown
