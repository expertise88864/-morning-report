# -*- coding: utf-8 -*-
"""2026-09-01 實信:一天的可用性事實被同一天後來的補寄抹掉。

09/01 排程整組沒被 GitHub 建立,07:59 人工救援 **08:28:35 送達**(準時)。
儲值後 08:42 再手動補寄一次、09:16:23 送達 —— 收據被覆寫成 09:16 之後,
判準說今天 `delivery_sla_missed`(defect),而今天其實**準時送達過**。

「今天有沒有在期限前收到信」與「這一班幾點寄的」是兩件事。
"""
import io
import json
from pathlib import Path

import morning_report as mr
import run_quality as rq

_ROOT = Path(mr.__file__).resolve().parent


def _codes(dv, **extra):
    m = {"date": "2026-09-01 05:10", "manifest_schema": 1,
         "delivery": dict(dv, success=True),
         "llm": {"analysis_origin": "luna_specialized"}}
    m.update(extra)
    return {f["code"]: f["severity"] for f in rq.assess(m)}


def test_a_same_day_resend_does_not_erase_that_the_letter_was_on_time():
    """今天的真實資料:第一次 08:28(準時)、本班 09:16(補寄)。"""
    codes = _codes({"delivered_at": "2026-09-01T09:16:23+08:00",
                    "first_delivered_at": "2026-09-01T08:28:35+08:00"})
    assert "delivery_sla_missed" not in codes, (
        "今天 08:28 就送達了,卻被判成沒趕上", codes)
    assert codes.get("run_delivered_after_target") == "degraded", codes


def test_a_genuinely_late_day_is_still_a_defect():
    """真的沒趕上(唯一一次送達就是 09:16)—— 這條不可以被上面那條放掉。"""
    codes = _codes({"delivered_at": "2026-09-01T09:16:23+08:00",
                    "first_delivered_at": "2026-09-01T09:16:23+08:00"})
    assert codes.get("delivery_sla_missed") == "defect", codes
    assert "run_delivered_after_target" not in codes, (
        "同一件事被報了兩次", codes)


def test_an_on_time_single_run_says_nothing():
    codes = _codes({"delivered_at": "2026-09-01T08:28:35+08:00",
                    "first_delivered_at": "2026-09-01T08:28:35+08:00"})
    assert "delivery_sla_missed" not in codes and (
        "run_delivered_after_target" not in codes), codes


def test_an_old_manifest_falls_back_to_the_stricter_side():
    """缺 `first_delivered_at` 的舊檔:退回用本班時刻。

    **這不是豁免**(上一輪的教訓):單班時兩者相同,多班時會偏向
    誤報成未達成 —— 保守的那一邊。豁免會讓缺陷變安靜,這個不會。
    """
    codes = _codes({"delivered_at": "2026-09-01T09:16:23+08:00"})
    assert codes.get("delivery_sla_missed") == "defect", codes


def test_the_day_boundary_is_read_in_taipei_time():
    """兩個時刻都要正規化 —— 期限是「台北的九點」。"""
    codes = _codes({"delivered_at": "2026-09-01T01:16:23+00:00",       # 09:16 TPE
                    "first_delivered_at": "2026-09-01T00:28:35+00:00"})  # 08:28 TPE
    assert "delivery_sla_missed" not in codes, codes
    assert "run_delivered_after_target" in codes, codes
    late = _codes({"delivered_at": "2026-08-31T21:16:23-04:00",         # 09:16 TPE
                   "first_delivered_at": "2026-08-31T21:16:23-04:00"})
    assert late.get("delivery_sla_missed") == "defect", late


def test_the_receipt_remembers_the_first_delivery_of_the_day(tmp_path,
                                                             monkeypatch):
    """收據是當日可用性的權威來源:同日再寫只更新本班時刻,
    **第一次送達的時刻寫一次就不動**。"""
    monkeypatch.setattr(mr, "DELIVERY_RECEIPT_FILE", tmp_path / "receipt.json")
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv(mr.FRESH_RECEIPT_ENV, raising=False)

    mr._publish_delivery_receipt("2026-09-01", {
        "success": True, "delivered_at": "2026-09-01T08:28:35+08:00"})
    first = json.loads((tmp_path / "receipt.json").read_text(encoding="utf-8"))
    assert first["delivery"]["first_delivered_at"] == "2026-09-01T08:28:35+08:00"

    mr._publish_delivery_receipt("2026-09-01", {
        "success": True, "delivered_at": "2026-09-01T09:16:23+08:00"})
    again = json.loads((tmp_path / "receipt.json").read_text(encoding="utf-8"))
    assert again["delivery"]["delivered_at"] == "2026-09-01T09:16:23+08:00"
    assert again["delivery"]["first_delivered_at"] == (
        "2026-09-01T08:28:35+08:00"), ("補寄把今天準時送達的事實抹掉了",
                                       again)

    # 換一天:不可以繼承昨天的第一次
    mr._publish_delivery_receipt("2026-09-02", {
        "success": True, "delivered_at": "2026-09-02T05:30:00+08:00"})
    day2 = json.loads((tmp_path / "receipt.json").read_text(encoding="utf-8"))
    assert day2["delivery"]["first_delivered_at"] == "2026-09-02T05:30:00+08:00"


def test_a_stale_checkout_does_not_supply_todays_first_delivery(tmp_path,
                                                               monkeypatch):
    """工作區那份可能是**昨天**的 checkout —— 不可以拿它當今天的第一次。

    (原本這條叫「遠端優先」,但突變驗證顯示**它量不到那個性質**:本機是
    別天的,先讀誰結果都一樣 —— 反例被前置的日期檢查擋住了。真正保護
    讀取順序的是 `test_a_recovered_corruption_still_leaves_a_trace`:
    本機先讀到好的就 `return`,壞掉的遠端永遠不會被發現。
    測試的名字也是一種宣稱,要跟它實際驗到的性質一致。)
    """
    monkeypatch.setattr(mr, "DELIVERY_RECEIPT_FILE", tmp_path / "receipt.json")
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    (tmp_path / "receipt.json").write_text(json.dumps({
        "date": "2026-08-31", "delivery": {
            "success": True, "delivered_at": "2026-08-31T09:05:00+08:00",
            "first_delivered_at": "2026-08-31T09:05:00+08:00"}}),
        encoding="utf-8")
    remote = tmp_path / "fresh.json"
    remote.write_text(json.dumps({
        "date": "2026-09-01", "delivery": {
            "success": True, "delivered_at": "2026-09-01T08:28:35+08:00",
            "first_delivered_at": "2026-09-01T08:28:35+08:00"}}),
        encoding="utf-8")
    monkeypatch.setenv(mr.FRESH_RECEIPT_ENV, str(remote))

    mr._publish_delivery_receipt("2026-09-01", {
        "success": True, "delivered_at": "2026-09-01T09:16:23+08:00"})
    got = json.loads((tmp_path / "receipt.json").read_text(encoding="utf-8"))
    assert got["delivery"]["first_delivered_at"] == "2026-09-01T08:28:35+08:00"


def test_the_producer_puts_the_day_fact_where_the_assessor_reads_it():
    """判準讀 manifest,而當日事實在收據上 —— 產出端要把它帶過去,
    否則這整條修正在生產路徑上等於沒接。"""
    src = (_ROOT / "morning_report.py").read_text(encoding="utf-8")
    body = src[src.index("def _mark_delivery_in_manifest("):]
    body = body[:body.index("\ndef _publish_delivery_receipt(")]
    assert '_receipt_first_delivered_at(' in body, (
        "manifest 沒有帶上 first_delivered_at —— 判準永遠只看得到本班")
    assert 'delivery["first_delivered_at"]' in body


def test_the_new_label_is_registered_with_its_consumer():
    """新的降級標籤要在消費端登記,否則退化成「沒見過的降級」。"""
    assert "run_delivered_after_target" in io.open(
        _ROOT / "run_quality.py", encoding="utf-8").read()
    codes = _codes({"delivered_at": "2026-09-01T09:16:23+08:00",
                    "first_delivered_at": "2026-09-01T08:28:35+08:00"})
    assert codes["run_delivered_after_target"] == "degraded"


def test_a_corrupt_receipt_is_not_the_same_as_a_first_send(tmp_path,
                                                           monkeypatch):
    """r4 外審第二輪:壞檔回空字串的話,它與「今天還沒寄過」長得一樣 ——
    呼叫端會把本班當成今天第一次,把先前那個**準時送達的事實覆寫掉**。

    而且 `delivery` 是非空 list / 字串 / 數字時,`.get()` 會 AttributeError;
    這一支的呼叫點在 `_mark_delivery_in_manifest` 的 try 內 —— 例外被那個
    catch-all 吞掉並 `return`:manifest 不寫、**收據不發**,看門狗於是
    看不到今天寄過,可能補一封重複的信。
    """
    monkeypatch.setattr(mr, "DELIVERY_RECEIPT_FILE", tmp_path / "r.json")
    monkeypatch.setattr(mr, "DELIVERY_RECEIPT_QUARANTINE", tmp_path / "q.json")
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    remote = tmp_path / "fresh.json"
    monkeypatch.setenv(mr.FRESH_RECEIPT_ENV, str(remote))

    for shape in ([{"success": True}], "字串", 42, [], None):
        remote.write_text(json.dumps(
            {"date": "2026-09-01", "delivery": shape}, ensure_ascii=False),
            encoding="utf-8")
        got = mr._receipt_first_delivered_at("2026-09-01")   # 不可以拋
        assert got[0] == "", (shape, got)
        if shape is not None:
            # 第二個回傳值是 dict(None=沒壞):要說得出哪個來源、壞在哪,
            # **而且帶著原始內容** —— 只回一個 True 或一句描述,呼叫端就
            # 只能用字串比對去猜該隔離哪一份。
            assert got[1], (f"{shape!r} 沒有被認出是壞的", got)
            assert got[1]["source"] == "origin/main", got
            assert "raw" in got[1], got

    remote.write_text("{ 不是 JSON", encoding="utf-8")
    first, bad = mr._receipt_first_delivered_at("2026-09-01")
    assert first == "" and "JSON 壞掉" in bad["why"], bad
    assert "不是 JSON" in bad["raw"], "原始內容沒有帶回來"

    # **根本身不是物件**也是壞掉 —— 上面那組的根一直是 dict,量不到這條
    for root in ('"整份是字串"', "[1, 2]", "42", "null"):
        remote.write_text(root, encoding="utf-8")
        got = mr._receipt_first_delivered_at("2026-09-01")
        assert got[0] == "" and got[1], root

    # 讀不動時**留空**,而不是填本班時刻(那會把準時送過改寫成遲到)
    monkeypatch.setattr(mr, "_DEGRADED_STEPS", [])
    assert mr._day_first_delivery("2026-09-01",
                                  "2026-09-01T09:16:23+08:00") == ""
    assert "state:corrupt:delivery_receipt" in mr._DEGRADED_STEPS, (
        "壞檔沒有留痕 —— 「今天沒更新」與「今天沒事」長得一樣")

    # 沒有來源 ≠ 壞掉:本班就是今天第一次
    monkeypatch.delenv(mr.FRESH_RECEIPT_ENV)
    assert mr._receipt_first_delivered_at("2026-09-01") == ("", None)
    assert mr._day_first_delivery(
        "2026-09-01", "2026-09-01T05:30:00+08:00") == "2026-09-01T05:30:00+08:00"


def test_the_receipt_is_still_published_when_the_old_one_is_unreadable(
        tmp_path, monkeypatch):
    """壞檔時**收據照發**:不發的後果是看門狗看不到今天寄過 → 重複寄信
    (收不回來),比誤報一次 SLA 嚴重得多。repo 既有的「壞檔不覆寫」政策
    針對的是會累積歷史的 state;收據是每天重寫的單筆證據。"""
    monkeypatch.setattr(mr, "DELIVERY_RECEIPT_FILE", tmp_path / "r.json")
    monkeypatch.setattr(mr, "DELIVERY_RECEIPT_QUARANTINE", tmp_path / "q.json")
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    remote = tmp_path / "fresh.json"
    remote.write_text("{ 壞的", encoding="utf-8")
    monkeypatch.setenv(mr.FRESH_RECEIPT_ENV, str(remote))
    monkeypatch.setattr(mr, "_DEGRADED_STEPS", [])

    mr._publish_delivery_receipt("2026-09-01", {
        "success": True, "delivered_at": "2026-09-01T09:16:23+08:00"})
    got = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert got["delivery"]["delivered_at"] == "2026-09-01T09:16:23+08:00", (
        "收據沒發出去 —— 看門狗會看不到今天寄過而補一封重複的信")
    assert "first_delivered_at" not in got["delivery"], (
        "讀不動卻填了本班時刻,把當日事實編出來了")
    assert "state:corrupt:delivery_receipt" in mr._DEGRADED_STEPS


def test_the_corrupt_bytes_are_kept_before_the_overwrite(tmp_path, monkeypatch):
    """r4 外審第三輪:壞掉的檔案裡**可能還留著救得回來的東西** ——
    截斷的 JSON 往往看得到 `"first_delivered_at": "…"` 那一段,人工修得回,
    而 `note_state_corrupt()` 的 120 字元明細裝不下它。

    正本仍然覆寫(不發收據會讓看門狗補一封重複的信),所以順序是
    **先存副本再覆寫** —— 中斷後救得回的那一邊。這是**權衡**,
    不是「壞檔可以覆寫」的通則。
    """
    monkeypatch.setattr(mr, "DELIVERY_RECEIPT_FILE", tmp_path / "r.json")
    monkeypatch.setattr(mr, "DELIVERY_RECEIPT_QUARANTINE",
                        tmp_path / "r.corrupt.json")
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv(mr.FRESH_RECEIPT_ENV, raising=False)
    monkeypatch.setattr(mr, "_DEGRADED_STEPS", [])
    mr._RECORDER._state_corrupt.clear()
    # 截斷在 `first_delivered_at` 之後 —— 當日事實還在檔案裡
    (tmp_path / "r.json").write_text(
        '{"date":"2026-09-01","delivery":{"success":true,'
        '"first_delivered_at":"2026-09-01T08:28:35+08:00",', encoding="utf-8")

    mr._publish_delivery_receipt("2026-09-01", {
        "success": True, "delivered_at": "2026-09-01T09:16:23+08:00"})

    kept = json.loads((tmp_path / "r.corrupt.json").read_text(encoding="utf-8"))
    assert "2026-09-01T08:28:35+08:00" in kept["raw"], (
        "壞掉的原始內容沒留下來 —— 那個還救得回的時間戳沒了", kept)
    assert kept["why"] and kept["quarantined_at"]
    # 正本照樣被本班覆寫(看門狗要讀得到今天的證據)
    now = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert now["delivery"]["delivered_at"] == "2026-09-01T09:16:23+08:00"


def test_the_quarantine_file_is_published_or_it_vanishes(tmp_path,
                                                        monkeypatch):
    """隔離檔要跟著 state 一起發佈 —— 留在 runner 上等於沒存。
    而且**沒有壞檔的日子清單完全不變**:無條件加進去的話,99.9% 的日子
    都在為一個不存在的檔案改變發佈行為。"""
    monkeypatch.setattr(mr, "DELIVERY_RECEIPT_QUARANTINE",
                        tmp_path / "q.json")
    assert mr._with_quarantine(["a"]) == ["a"], "沒有壞檔卻改了發佈清單"
    (tmp_path / "q.json").write_text("{}", encoding="utf-8")
    assert mr._with_quarantine(["a"]) == ["a", str(tmp_path / "q.json")]

    src = (_ROOT / "morning_report.py").read_text(encoding="utf-8")
    assert src.count("_with_quarantine(") >= 4, (
        "有發佈 state 的地方沒有經過這一支 —— 隔離檔會跟 runner 一起消失")



def test_the_assessor_falls_back_conservatively_when_the_day_fact_is_absent():
    """欄位留空時判準退回用本班時刻 —— 會誤報成未達成,那是保守的那一邊。
    豁免會讓缺陷變安靜,這個不會。"""
    codes = _codes({"delivered_at": "2026-09-01T09:16:23+08:00"})
    assert codes.get("delivery_sla_missed") == "defect", codes


def test_a_recovered_corruption_still_leaves_a_trace(tmp_path, monkeypatch):
    """r4 外審第二輪:遠端那份壞掉、本機那份剛好補得上時,先前在迴圈裡
    就 return 了 —— 壞掉的事實**完全不出聲**。「有備援救回來」不是
    「沒有壞」;「今天沒更新」與「今天沒事」在紀錄裡不可以長得一樣。"""
    monkeypatch.setattr(mr, "DELIVERY_RECEIPT_FILE", tmp_path / "r.json")
    monkeypatch.setattr(mr, "DELIVERY_RECEIPT_QUARANTINE", tmp_path / "q.json")
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    remote = tmp_path / "fresh.json"
    remote.write_text("{ 壞掉的遠端", encoding="utf-8")
    monkeypatch.setenv(mr.FRESH_RECEIPT_ENV, str(remote))
    (tmp_path / "r.json").write_text(json.dumps({
        "date": "2026-09-01", "delivery": {
            "success": True, "delivered_at": "2026-09-01T08:28:35+08:00",
            "first_delivered_at": "2026-09-01T08:28:35+08:00"}}),
        encoding="utf-8")
    monkeypatch.setattr(mr, "_DEGRADED_STEPS", [])
    mr._RECORDER._state_corrupt.clear()

    got = mr._day_first_delivery("2026-09-01", "2026-09-01T09:16:23+08:00")
    assert got == "2026-09-01T08:28:35+08:00", ("本機那份救得回來", got)
    assert "state:corrupt:delivery_receipt" in mr._DEGRADED_STEPS, (
        "救回來了就不出聲 —— 壞掉的來源沒有人會知道")

    # 壞掉的內容要留得下來:runner 上另存隔離檔沒有意義(機器等一下就沒了),
    # 明細由 recorder 擁有、會跟著 manifest 進 git,那才是查得到的地方。
    detail = mr._RECORDER.state_corrupt()
    assert detail and "origin/main" in detail[0]["why"], (
        "留痕說不出是哪一個來源壞的", detail)


def test_the_authoritative_source_is_quarantined_too(tmp_path, monkeypatch):
    """r4 外審第四輪:先前用 `"工作區" in 描述字串` 決定隔離哪一份 ——
    又一次拿便利的判斷式代表語意狀態。**權威來源(origin/main)壞掉時
    反而不會被保存**,而它才是同日前一班真正寫下的那份。

    現在來源自己把 `raw` 帶回來,壞哪一個就存哪一個。
    """
    monkeypatch.setattr(mr, "DELIVERY_RECEIPT_FILE", tmp_path / "r.json")
    monkeypatch.setattr(mr, "DELIVERY_RECEIPT_QUARANTINE", tmp_path / "q.json")
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(mr, "_DEGRADED_STEPS", [])
    mr._RECORDER._state_corrupt.clear()
    remote = tmp_path / "fresh.json"
    monkeypatch.setenv(mr.FRESH_RECEIPT_ENV, str(remote))
    # 遠端壞掉,而且**本機那份根本不存在** —— 先前這個情境完全不隔離
    remote.write_text('{"date":"2026-09-01","delivery":{"success":true,'
                      '"first_delivered_at":"2026-09-01T08:28:35+08:00",',
                      encoding="utf-8")

    assert mr._day_first_delivery("2026-09-01",
                                  "2026-09-01T09:16:23+08:00") == ""
    kept = json.loads((tmp_path / "q.json").read_text(encoding="utf-8"))
    assert kept["source"] == "origin/main", (
        "權威來源壞掉卻沒被隔離", kept)
    assert "2026-09-01T08:28:35+08:00" in kept["raw"], (
        "那個還救得回的時間戳沒了", kept)


def _sla_codes(first, at=None, date="2026-09-01 05:10", schema=2):
    m = {"date": date, "manifest_schema": schema,
         "llm": {"analysis_origin": "luna_specialized"},
         "delivery": {"success": True, "delivered_at": at or first}}
    if first:
        m["delivery"]["first_delivered_at"] = first
    return {f["code"] for f in rq.assess(m)}


def test_the_deadline_is_an_instant_not_a_clock_face():
    """r5 外審:先前比 `(hour, minute) >= (9, 0)` —— 那問的是「寄出時間的
    鐘面是不是 09:00 以後」,而 SLA 問的是「**這個營業日**的 09:00 之前有
    沒有送達」。實測:date=09-01 而信 `09-02T00:20` 才送達(晚了 15 小時
    20 分),鐘面 00:20 < 09:00 → 判成準時。**跨過午夜的遲到會變成 false
    negative,而那正是最嚴重的遲到。**"""
    assert "delivery_sla_missed" not in _sla_codes("2026-09-01T08:59:59+08:00")
    assert "delivery_sla_missed" in _sla_codes("2026-09-01T09:00:00+08:00")
    # 跨日:這一天的期限早就過了
    assert "delivery_sla_missed" in _sla_codes("2026-09-02T00:20:00+08:00"), (
        "晚了 15 小時卻因為鐘面是 00:20 而判成準時")
    # 同一個時刻用 UTC 寫:換算成台北是 09-02 00:20,一樣要抓到
    assert "delivery_sla_missed" in _sla_codes("2026-09-01T16:20:00+00:00")
    # 今天的真實情況:第一次準時、本班是同日補寄
    today = _sla_codes("2026-09-01T08:28:35+08:00",
                       at="2026-09-01T09:16:23+08:00")
    assert "delivery_sla_missed" not in today
    assert "run_delivered_after_target" in today

    # **本班那條判準也要用同一個期限**:第一次準時、而這一班拖到隔天
    # 凌晨才寄出。上面那組兩個時刻同日,鐘面比較和期限比較結果一樣 ——
    # 量不到這條規則(突變驗證抓到的白測)。
    crossed = _sla_codes("2026-09-01T08:28:35+08:00",
                         at="2026-09-02T00:20:00+08:00")
    assert "delivery_sla_missed" not in crossed, "當日第一次是準時的"
    assert "run_delivered_after_target" in crossed, (
        "本班跨日才寄出,鐘面 00:20 卻被當成準時", crossed)


def test_an_unreadable_business_date_is_a_defect_not_a_clean_pass():
    """r6 外審:**不知道營業日 = SLA 無法稽核,不是「那就當作準時」。**

    先前壞掉的 `date` 會退回用送達時刻自己那一天,於是同一封晚了 15 小時
    20 分的信,只因為日期證據壞掉就從 defect 變成**乾淨通過** ——
    ★而我上一輪寫的測試還把那個 fail-open 釘成了正確答案★
    (它斷言 `delivery_sla_missed not in ...`,而程式註解同時宣稱
    「不會憑空放行」—— 兩者矛盾,跨午夜情境證明會放行)。

    現在:先報 defect,再用同一個 fallback 做**輔助**判斷 ——
    抓得到的遲到照樣抓,但這一天不可能是 clean pass。
    """
    crossed = _sla_codes("2026-09-02T00:20:00+08:00", date="壞掉的日期")
    assert "manifest_business_date_invalid" in crossed, crossed
    # 最重要的一條:這一天不可以看起來乾淨
    assert any(f["severity"] == "defect" for f in rq.assess(
        {"date": "壞掉的日期", "manifest_schema": 2,
         "llm": {"analysis_origin": "luna_specialized"},
         "delivery": {"success": True,
                      "delivered_at": "2026-09-02T00:20:00+08:00",
                      "first_delivered_at": "2026-09-02T00:20:00+08:00"}})), (
        "晚了 15 小時的信,因為日期壞掉而完全乾淨")
    # 日期正常時不可以誤報
    assert "manifest_business_date_invalid" not in _sla_codes(
        "2026-09-01T08:28:35+08:00")


def test_a_malformed_required_timestamp_is_not_softer_than_a_missing_one():
    """r6 外審:兩個 timestamp 先前共用一個 `except ValueError` → 一律
    `delivered_at_unparsable`(**degraded**),而缺欄位是 defect ——
    contract inversion:必填欄位「內容壞掉」比「整個缺席」還寬鬆,
    而且連是哪一個壞掉都說不出來。"""
    def _sev(dv, schema=2):
        m = {"date": "2026-09-01 05:10", "manifest_schema": schema,
             "llm": {"analysis_origin": "luna_specialized"},
             "delivery": dict(dv, success=True)}
        return {f["code"]: f["severity"] for f in rq.assess(m)}

    ok = "2026-09-01T08:28:35+08:00"
    # v2:兩個必填欄位壞掉都是 defect,而且分得出是哪一個
    assert _sev({"delivered_at": ok, "first_delivered_at": "垃圾"}).get(
        "first_delivered_at_invalid") == "defect"
    assert _sev({"delivered_at": "垃圾", "first_delivered_at": ok}).get(
        "delivered_at_invalid") == "defect"
    # v1 的檔案 first 還不是必填 —— 壞掉是 degraded,不是 defect
    assert _sev({"delivered_at": ok, "first_delivered_at": "垃圾"},
                schema=1).get("first_delivered_at_invalid") == "degraded"
    # 缺席仍然照舊
    assert _sev({"delivered_at": ok}).get(
        "first_delivered_at_missing") == "defect"
    assert _sev({"delivered_at": ok, "first_delivered_at": ok}) .get(
        "first_delivered_at_invalid") is None


def test_an_impossible_chronology_is_not_trusted_evidence():
    """r6 外審:「第一次」晚於「最新這次」在語意上不成立。它會憑空造出
    一個 SLA defect(而 `delivered_at` 自己就證明那時送達過),或者兩個
    都早於期限而**完全沒有 finding**。時序壞掉的一對不是可信的證據。"""
    def _codes(first, at):
        m = {"date": "2026-09-01 05:10", "manifest_schema": 2,
             "llm": {"analysis_origin": "luna_specialized"},
             "delivery": {"success": True, "delivered_at": at,
                          "first_delivered_at": first}}
        return {f["code"] for f in rq.assess(m)}

    bad = _codes("2026-09-01T09:16:00+08:00", "2026-09-01T08:28:00+08:00")
    assert "delivery_timestamp_order_invalid" in bad, bad
    assert "delivery_sla_missed" not in bad, (
        "拿時序不可能的一對判出了 SLA 違規", bad)
    # 兩個都早於期限、時序仍然不可能 —— 先前會完全沒有 finding
    quiet = _codes("2026-09-01T08:50:00+08:00", "2026-09-01T08:20:00+08:00")
    assert "delivery_timestamp_order_invalid" in quiet, quiet
    # 正常時序不可以誤報
    assert "delivery_timestamp_order_invalid" not in _codes(
        "2026-09-01T08:28:35+08:00", "2026-09-01T09:16:23+08:00")


def test_the_first_delivery_field_has_its_own_epoch():
    """r5 外審:導入 `first_delivered_at` 時我沒有跟著開世代 —— 於是
    「缺這個欄位」永遠只能解讀成「舊檔」,分不出「新 writer 壞了」。
    這正是上一輪剛為 `delivered_at` 修過的同型缺陷(沒有截止點的豁免)。
    """
    import run_manifest as rm
    assert rm.MANIFEST_SCHEMA == rm.SCHEMA_V2_FIRST_DELIVERY
    # **上一輪那條修正在真正的 bump 下經住了**:歷史世代沒有跟著移動
    assert rq.MANIFEST_SCHEMA_WITH_DELIVERED_AT == rm.SCHEMA_V1_DELIVERY_TIMESTAMP
    assert rq.MANIFEST_SCHEMA_WITH_FIRST_DELIVERY == rm.SCHEMA_V2_FIRST_DELIVERY

    late = "2026-10-01T09:20:00+08:00"
    v1 = _sla_codes("", at=late, date="2026-10-01 05:10", schema=1)
    assert "first_delivered_at_missing" not in v1, ("v1 是真的舊檔", v1)
    v2 = _sla_codes("", at=late, date="2026-10-01 05:10", schema=2)
    assert "first_delivered_at_missing" in v2, ("v2 缺欄位就是 writer 壞了", v2)
    ok = _sla_codes("2026-10-01T05:30:00+08:00", at=late,
                    date="2026-10-01 05:10", schema=2)
    assert "first_delivered_at_missing" not in ok
    assert "delivery_sla_missed" not in ok, "第一次 05:30 是準時的"


def test_the_producer_stamps_the_new_generation():
    """世代由權威產生器蓋 —— 沒接上的話,新欄位的必填要求永遠不會生效。"""
    import run_manifest as rm
    rec = rm.ManifestRecorder()
    built = rec.build(date="2026-09-02 05:07", report_kind="daily",
                      budget_seconds=2700.0, news_workers=4,
                      degraded_steps=[])
    assert built["manifest_schema"] == rm.SCHEMA_V2_FIRST_DELIVERY


def test_a_stale_first_delivery_cannot_certify_today(tmp_path):
    """r5 外審第二輪:`first_delivered_at` 是 stale/corrupt state 留下的
    **前一天**時刻時,它必然小於今天的期限 → 判成「今天曾準時送達」→
    真正的遲到被吞掉。

    **這個洞是改成絕對時間比較之後才出現的**:先前只比鐘面時,昨天
    09:30 的鐘面仍 ≥ 09:00,會被抓到。修正的正確性不能只看它自己 ——
    要看它與既有行為的組合。
    """
    def _c(first, at, date):
        m = {"date": date, "manifest_schema": 2,
             "llm": {"analysis_origin": "luna_specialized"},
             "delivery": {"success": True, "delivered_at": at,
                          "first_delivered_at": first}}
        return {f["code"] for f in rq.assess(m)}

    stale = _c("2026-09-01T09:30:00+08:00", "2026-09-02T09:30:00+08:00",
               "2026-09-02 05:07")
    assert "first_delivered_at_out_of_range" in stale, stale
    assert "delivery_sla_missed" in stale, (
        "今天根本沒有準時送達過,卻被昨天的時刻背書", stale)

    # 退回用本班時刻之後,本班準時就仍然是準時
    ok = _c("2026-09-01T09:30:00+08:00", "2026-09-02T05:30:00+08:00",
            "2026-09-02 05:07")
    assert "first_delivered_at_out_of_range" in ok
    assert "delivery_sla_missed" not in ok, ok

    # **晚於營業日是合法的**:跨午夜才寄出的真遲到,不是契約缺陷
    late = _c("2026-09-02T00:20:00+08:00", "2026-09-02T00:20:00+08:00",
              "2026-09-01 05:10")
    assert "first_delivered_at_out_of_range" not in late, late
    assert "delivery_sla_missed" in late, late


def test_a_date_without_a_time_is_not_a_delivery_timestamp():
    """r6 外審第二輪:**只驗「解得開」不夠**。
    `datetime.fromisoformat("2026-09-01")` 會成功並給出**午夜**,
    而午夜必然早於 09:00 —— 一個根本不含送達時刻的值於是乾淨通過:
    沒有 timestamp defect,也沒有 SLA finding。

    先前的壞值測試用的是「垃圾」這種 `fromisoformat` 會拒絕的字串,
    量不到「語法可解析但語意不完整」這一類。
    """
    def _codes(v, schema=2):
        m = {"date": "2026-09-01 05:10", "manifest_schema": schema,
             "llm": {"analysis_origin": "luna_specialized"},
             "delivery": {"success": True, "delivered_at": v,
                          "first_delivered_at": v}}
        return {f["code"]: f["severity"] for f in rq.assess(m)}

    for date_only in ("2026-09-01", "20260901", "2026-09-01T", "2026-09-01 "):
        got = _codes(date_only)
        assert got.get("delivered_at_invalid") == "defect", (date_only, got)
        assert got.get("first_delivered_at_invalid") == "defect", (date_only, got)

    # 產出端真正寫出來的形狀不可以被誤擋
    import datetime as _d
    real = _d.datetime(2026, 9, 1, 8, 28, 35,
                       tzinfo=_d.timezone(_d.timedelta(hours=8))).isoformat(
                           timespec="seconds")
    assert "delivered_at_invalid" not in _codes(real), real
    assert "2026-09-01T08:28:35+08:00" == real, real
    # 分鐘精度(人工 backfill 常見)也要收
    assert "delivered_at_invalid" not in _codes("2026-09-01 08:28")
