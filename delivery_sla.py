# -*- coding: utf-8 -*-
"""**當日送達期限的原語** —— 期限本身、營業日、以及時刻怎麼解讀。

第一批(2026-09-01)搬的是**自足**的那一層(期限、營業日、時刻解讀)。
第二批(2026-09-05)把**判準本體**也搬過來了:`assess_delivery()` 就是
原本 `run_quality.assess()` 裡產生 `delivery_sla_missed` /
`run_delivered_after_target` 的那一段,逐行照搬(純搬移:區塊內沒有
return/break,後面的程式碼也不引用它定義的名字)。`add()` 閉包由呼叫端
傳進來 —— 這裡只產生 finding,不決定它去哪。

兩次搬移的直接原因都是 `run_quality.py` 的 1000 行硬閘門:它不接受再調高
數字,所以逼出邊界。第二次是 9f(分析文字撞到保險絲的 finding)撞上去的。
"""
import datetime as _dt
import re as _re

from run_manifest import MANIFEST_SCHEMA as _CURRENT_MANIFEST_SCHEMA  # noqa: F401
from run_manifest import SCHEMA_V1_DELIVERY_TIMESTAMP as _V1_DELIVERED_AT
from run_manifest import SCHEMA_V2_FIRST_DELIVERY as _V2_FIRST_DELIVERY
from delivery_contract import (
    OUTCOME_DELIVERED,
    OUTCOME_INCOMPLETE,
    OUTCOME_INVALID,
    delivery_verdict,
)


#: 改期限要連測試一起改,不能默默放寬。
SLA_HOUR, SLA_MINUTE = 9, 0

#: SLA 判準的時區。**期限是「台北的九點」,不是「寫下那個字串的當地九點」**
#: (2026-09-01 外審 P2):目前 writer 寫 `+08:00`,判準直接讀 hour 剛好對;
#: 但 writer 若哪天改寫 UTC(`2026-09-01T01:05:34+00:00` 就是台北 09:05),
#: 判準會看到 hour=1 而說「沒有超時」—— 悄悄失效。判準自己守住時區語意。
SLA_TZ = _dt.timezone(_dt.timedelta(hours=8))

#: **`delivered_at` 從哪一版開始是必填**(2026-09-01 外審 P2)。
#: 「舊 manifest 沒有這個欄位不算違規」是對的(否則部署當天必定一次假
#: 警報),但沒有截止點的話那個豁免是**永久**的 —— 將來某條新寄信路徑
#: 忘了寫,判準會說「沒問題」而不是「SLA 無法稽核」。
#: 產出端在**權威產生器** `run_manifest.ManifestRecorder.build()` 寫下
#: `manifest_schema`(r1 外審:先前蓋在寄送補寫那一步,而週日路徑之後
#: 會從頭重建文件,標記就掉了)。這一版(含)以後,寄成功卻沒有
#: `delivered_at` 就是 defect。**數字只有一個定義**(見檔頭的 import)。
MANIFEST_SCHEMA_WITH_DELIVERED_AT = _V1_DELIVERED_AT
#: 從這一版起,寄送成功的 manifest **必須**寫得出「今天第一次送達」是
#: 幾點。缺了不是「舊檔」,是當日可用性無法稽核。
MANIFEST_SCHEMA_WITH_FIRST_DELIVERY = _V2_FIRST_DELIVERY

#: `manifest_schema` **自己**的豁免截止日(台北)。
#: r8 外審:前面幾條世代都靠 `manifest_schema` 判「這份檔有沒有義務寫」,
#: 但 `manifest_schema` 自己缺席時只能說「舊檔」—— 一個**永遠不會到期**
#: 的豁免,與先前修掉的 `delivered_at` / `first_delivered_at` 完全同型。
#: 它需要一個不依賴自己的歷史錨點:營業日在這一天之後還沒有世代標記,
#: 就不是舊檔,是 writer 沒寫出來。
#: 刻意設 09-02 而不是 09-01 —— 部署當天早上真正的舊 manifest 不該被追溯判錯。
MANIFEST_SCHEMA_REQUIRED_FROM = _dt.date(2026, 9, 2)


def _sla_business_day(manifest_date):
    """SLA 的**營業日**(讀不出來回 `None`)。

    取自 manifest 的 `date`(開跑時刻 —— 跨午夜的手動觸發也記開跑日,
    那正是同日冪等用的那一天)。**讀不出來要回 None,不要自己猜一天**:
    先前退回用送達時刻那一天,於是同一封晚了 15 小時的信,只因為日期
    證據壞掉就從 defect 變成乾淨通過(r6 外審)。
    """
    try:
        return _dt.date.fromisoformat(str(manifest_date or "")[:10])
    except ValueError:
        return None


#: 產出端寫的是 `datetime.now(TPE).isoformat(timespec="seconds")`,
#: 也就是「日期 + 分隔符 + 至少 HH:MM」。**只驗「解得開」不夠**
#: (r6 外審第二輪):`fromisoformat("2026-09-01")` 會成功並給出**午夜**,
#: 而午夜必然早於 09:00 —— 一個根本不含送達時刻的值於是乾淨通過。
_ISO_DATETIME_RE = _re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")


def _parse_sla_time(raw):
    """把 ISO 時刻換算到 SLA 時區;不符合產出端契約就回 `None`(**不拋**)。

    兩個 timestamp 要各自報自己的問題 —— 共用一個 `except` 連是哪一個
    壞掉都說不出來。
    """
    if not _ISO_DATETIME_RE.match(str(raw or "")):
        return None                         # 只有日期、或根本不是時刻
    try:
        return _to_sla_tz(str(raw))
    except (ValueError, TypeError):
        return None


def _to_sla_tz(raw: str) -> _dt.datetime:
    """把 ISO 時刻換算到 SLA 的時區(期限是「台北的九點」)。

    沒帶時區的字串視為台北(產出端一直是 TPE),帶了就換算過來。
    解不出來會拋 `ValueError` —— 呼叫端要報「這一天無法稽核」。
    """
    when = _dt.datetime.fromisoformat(raw)
    return (when.replace(tzinfo=SLA_TZ) if when.tzinfo is None
            else when.astimezone(SLA_TZ))


def assess_delivery(m: dict, add, *, digest: bool) -> None:
    """**09:00 SLA 的判準本體**(從 `run_quality.assess` 搬來,2026-09-05)。

    2026-08-31 使用者定案:信可以晚到,但台股開盤前必須到。先前 state 只有
    `date`(**開跑時刻**),而 08/31 那班 `date=08:30`、`total_seconds=2088`
    —— 信其實 09:05 才寄出,監控資料看起來卻像「08:30 成功」。**宣告了 SLA
    卻沒有欄位能稽核它**,那條 SLA 就只是一句話。判準吃 `delivery.delivered_at`。

    舊 manifest 沒有這個欄位 —— **不當成違規**(那會在部署當天產生一次確定的
    假警報,而假警報會訓練人忽略告警)。**型別壞掉不可以靜靜變成 `{}`**
    (2026-09-01 r7 外審)。**「缺 delivery」刻意不在這裡報**:canary 是
    `DRY_RUN=1` 不寄信;「這一班該有結論了嗎」需要時序資訊,那半留在看門狗。

    `add(code, severity, detail)` 是 `assess()` 的閉包;`digest` = 週日綜合信
    (期限訊息不寫「台股開盤」)。
    """
    if "delivery" in m and not isinstance(m.get("delivery"), dict):
        add("delivery_structure_invalid", "defect",
            f"delivery 不是物件:{type(m.get('delivery')).__name__} —— "
            "今天有沒有寄到查不出來,而它決定要不要補寄")
    _dv = m.get("delivery") if isinstance(m.get("delivery"), dict) else {}
    _at = str(_dv.get("delivered_at") or "").strip()
    # **「不知道它是哪一版」不等於「它一定是最舊版」**(r2 外審)。
    # `_safe_int` 讓壞值變 0 → 判成 legacy → 反而拿到豁免:版本資訊壞掉
    # 的檔比正常檔更寬鬆。三態分開:沒有(真 legacy)/ 讀得出來 / 壞掉。
    # **「欄位不存在」與「欄位存在但值是 null」是兩件事**(2026-09-01 r3 外審):
    # 用 `.get()` 的回傳值判斷缺席,會把 `{"manifest_schema": null}` 這種
    # **版本資訊已經損壞**的檔判成「舊版,當時還沒有這個欄位」——
    # 又一次讓壞掉的檔拿到比合法新檔更寬鬆的待遇。key 在不在要問 key。
    _has_schema = "manifest_schema" in m
    if not _has_schema:
        _bday = _sla_business_day(m.get("date"))
        if _bday is not None and _bday >= MANIFEST_SCHEMA_REQUIRED_FROM:
            add("manifest_schema_missing", "defect",
                f"營業日 {_bday} 的 manifest 沒有 manifest_schema —— "
                f"{MANIFEST_SCHEMA_REQUIRED_FROM} 之後產生的檔不可能是舊檔,"
                "而所有欄位的必填判定都靠它")
    _raw_schema = m.get("manifest_schema")
    _schema, _schema_bad = 0, False
    if _has_schema:
        # **只接受正整數**(2026-09-01 r2 外審):`int()` 對 `0.9` 給 0、
        # 對 `False` 給 0,負數則原樣通過 —— 這些壞值都會小於功能世代 1
        # 而拿到 legacy 豁免,SLA 又一次稽核不到。「欄位缺席」才是真舊檔;
        # 存在但不合法的值一律是 invalid,而且不得豁免。
        if isinstance(_raw_schema, bool) or not isinstance(_raw_schema, int):
            _schema_bad = True
        elif _raw_schema < 1:
            _schema_bad = True
        else:
            _schema = _raw_schema
    if _schema_bad:
        add("manifest_schema_invalid", "defect",
            f"manifest_schema 讀不出來:{_raw_schema!r} —— 版本契約失效,"
            "不能用它決定哪些欄位是必填的")
    elif _schema > _CURRENT_MANIFEST_SCHEMA:
        add("manifest_schema_unsupported", "degraded",
            f"manifest_schema={_schema} 比本程式認得的 "
            f"{_CURRENT_MANIFEST_SCHEMA} 新 —— 判準可能漏驗新欄位")
    # **判準自己也是 consumer**(2026-09-01 r8 外審第二輪):我把看門狗與
    # `morning_report` 都換成了五態狀態機,卻漏了這裡 —— 於是矛盾的紀錄
    # (同時宣稱寄出與刻意不寄)在看門狗被拒絕,在判準卻仍被當成「成功」
    # 而拿去判 SLA:**同一份 state 兩種結論**,正是收斂狀態機要消滅的事。
    # 而我的測試只驗了原始碼字串(誰呼叫了什麼),沒驗 `assess()` 的行為。
    _dv_outcome, _dv_defects = (delivery_verdict(_dv) if _dv
                                else (OUTCOME_INCOMPLETE, ()))
    # **紀錄本身的瑕疵是品質問題,不是控制流問題**(r9 外審):
    # `attempted` 與結局不一致時,結局仍然照 `success` 算 —— 明確的
    # `success: true` 是很強的「已寄出」證據,因為旁邊的欄位壞掉就
    # 改判成「沒寄到」會觸發自動補寄,那是真的重複寄信。
    for _d in _dv_defects:
        add("delivery_record_" + _d, "degraded",
            f"寄送紀錄的欄位互相對不上({_d})—— 結局仍照 success 判定"
            "(明確的寄送證據不因旁邊的 metadata 壞掉而失效),但這份紀錄"
            "說不清楚它自己是怎麼寫出來的")
    if _dv_outcome == OUTCOME_INVALID:
        add("delivery_state_invalid", "defect",
            f"寄送紀錄自相矛盾或型別壞掉(success={_dv.get('success')!r}、"
            f"skipped_reason={_dv.get('skipped_reason')!r})—— "
            "它決定要不要告警、要不要補寄、要不要判 SLA,"
            "而這一份說不出今天到底寄了沒")
    if _dv_outcome == OUTCOME_DELIVERED and _at:
        # **「今天有沒有準時收到信」與「這一班幾點寄的」是兩件事**
        # (2026-09-01 r4 外審,當天真實踩到):09/01 08:28 已經送達一次,
        # 儲值後手動補寄、09:16 再送一次 —— 收據被覆寫之後,判準說
        # 今天 SLA 未達成,而今天其實**準時送達過**。一天的可用性事實
        # 不可以被同一天後來的補寄抹掉。
        # 缺 `first_delivered_at` 的舊檔退回用本班時刻:單班時兩者相同,
        # 多班時會偏向**誤報成未達成** —— 這不是豁免,是保守的那一邊。
        _raw_first = str(_dv.get("first_delivered_at") or "")
        _first_at = _raw_first or _at
        # **缺席的豁免要有截止點**(r5 外審,與上一輪 `delivered_at` 同型):
        # 退回用本班時刻是**保守側**的行為(可能誤報成未達成),但只有
        # v2 之前的檔才有資格說「當時還沒有這個欄位」。v2 以後缺了,
        # 就是 writer 沒寫出來 —— 當日可用性從此無法用事實稽核。
        if not _raw_first and (_schema_bad
                               or _schema >= MANIFEST_SCHEMA_WITH_FIRST_DELIVERY):
            add("first_delivered_at_missing", "defect",
                f"寄送成功卻沒有寫下「今天第一次送達」的時刻"
                f"(manifest_schema={_schema}) —— 當日送達期限只能拿本班"
                "時刻代替,同日補寄會被誤判成整天遲到")
        # **必填欄位「內容壞掉」不可以比「整個缺席」更寬鬆**(r6 外審):
        # 先前兩個 timestamp 共用一個 `except ValueError` → 一律
        # `delivered_at_unparsable`(degraded),而缺欄位是 defect ——
        # contract inversion,而且連是哪一個壞掉都說不出來。各自解析、
        # 各自報,現行世代要求的那個壞掉就是 defect。
        _when = _parse_sla_time(_at)
        if _when is None:
            add("delivered_at_invalid", "defect",
                f"寄出時刻讀不出來:{_at!r} —— 這是 v"
                f"{MANIFEST_SCHEMA_WITH_DELIVERED_AT} 起的必填欄位,"
                "SLA 這一天無法稽核")
        _first_when = _parse_sla_time(_raw_first) if _raw_first else None
        if _raw_first and _first_when is None:
            _sev = ("defect" if (_schema_bad
                                 or _schema >= MANIFEST_SCHEMA_WITH_FIRST_DELIVERY)
                    else "degraded")
            add("first_delivered_at_invalid", _sev,
                f"「今天第一次送達」讀不出來:{_raw_first!r} —— "
                "當日送達期限只能拿本班時刻代替")
        # **不知道營業日 = SLA 無法稽核,不是「那就當作準時」**(r6 外審)。
        # 先前壞掉的 `date` 會退回用**送達時刻自己那一天**,於是同一封晚了
        # 15 小時 20 分的信,只因為日期證據壞掉就從 defect 變成乾淨通過
        # —— 而我上一輪的測試還把那個 fail-open 釘成了正確答案。
        # 現在先報 defect,再用同一個 fallback 做**輔助**判斷:
        # 抓得到的遲到照樣抓,但這一天不可能是 clean pass。
        _day = _sla_business_day(m.get("date"))
        if _day is None:
            add("manifest_business_date_invalid", "defect",
                f"營業日讀不出來:{m.get('date')!r} —— "
                f"組不出 {SLA_HOUR:02d}:{SLA_MINUTE:02d} 的送達期限,"
                "以下的 SLA 判定只是最佳努力")
        if _when is not None or _first_when is not None:
            _why = ("" if digest else "(台股開盤)")
            _deadline = _dt.datetime.combine(
                _day or (_when or _first_when).date(),
                _dt.time(SLA_HOUR, SLA_MINUTE), tzinfo=SLA_TZ)
            # **時序不可能的一對不是可信的 SLA 證據**(r6 外審):
            # 「第一次」晚於「最新這次」在語意上不成立,而它會憑空造出
            # 一個 SLA defect(而 `delivered_at` 自己就證明那時送達過),
            # 或者兩個都早於期限而**完全沒有 finding**。
            if (_first_when is not None and _when is not None
                    and _first_when > _when):
                add("delivery_timestamp_order_invalid", "defect",
                    f"「第一次送達」{_first_when:%m-%d %H:%M} 晚於「本班送達」"
                    f"{_when:%m-%d %H:%M} —— 時序不可能,兩個時刻都不可信,"
                    "本班不判 SLA")
            else:
                # 缺 `first` 的舊檔退回用本班時刻:單班時兩者相同,多班時
                # 偏向**誤報成未達成** —— 保守的那一邊,不是豁免。
                _first_when = _first_when if _first_when is not None else _when
                _when = _when if _when is not None else _first_when
                # **「今天第一次」必須真的在今天**(r5 外審第二輪):`first`
                # 是 stale/corrupt state 留下的**前一天**時刻時,它必然小於
                # 今天的期限 → 判成「今天曾準時送達」→ 真正的遲到被吞掉。
                # 這個洞是改成絕對時間比較之後才出現的:先前只比鐘面時,
                # 昨天 09:30 的鐘面仍 ≥ 09:00,會被抓到。
                # 只擋**早於**營業日:晚於是合法的(跨午夜才寄出的真遲到)。
                if _first_when.date() < _deadline.date():
                    add("first_delivered_at_out_of_range", "defect",
                        f"「今天第一次送達」寫的是 {_first_when:%Y-%m-%d %H:%M},"
                        f"早於本班的營業日 {_deadline:%Y-%m-%d} —— "
                        "state 沒跟上或壞掉了;本班時刻先當成當日事實")
                    _first_when = _when     # 保守:退回用本班自己的時刻
                if _first_when >= _deadline:
                    add("delivery_sla_missed", "defect",
                        f"今天第一次送達是 {_first_when:%H:%M},超過 "
                        f"{SLA_HOUR:02d}:{SLA_MINUTE:02d} 的送達期限{_why} —— "
                        "排程延遲或本班跑太久,兩者的處置不同,看 total_seconds")
                elif _when >= _deadline:
                    # 今天準時送過了,這一班只是同日的補寄/重跑 ——
                    # 是事實要記,但**不是**「今天沒收到信」。
                    add("run_delivered_after_target", "degraded",
                        f"本班 {_when:%H:%M} 才寄出(超過 {SLA_HOUR:02d}:"
                        f"{SLA_MINUTE:02d}),但今天第一次送達是 "
                        f"{_first_when:%H:%M} —— 當日送達期限已經達成,"
                        "這一班是同日的補寄或重跑")
    elif _dv_outcome == OUTCOME_DELIVERED and (
            _schema_bad or _schema >= MANIFEST_SCHEMA_WITH_DELIVERED_AT):
        # 這一版以後就是必填 —— 缺了不是「沒問題」,是「SLA 無法稽核」。
        add("delivered_at_missing", "defect",
            f"寄送成功卻沒有寫下實際寄出時刻(manifest_schema={_schema})"
            " —— SLA 無法稽核,而它是使用者定案的送達期限")
