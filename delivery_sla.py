# -*- coding: utf-8 -*-
"""**當日送達期限的原語** —— 期限本身、營業日、以及時刻怎麼解讀。

從 `run_quality` 搬出來的是**自足**的那一層(只依賴 `datetime` / `re`
與 `run_manifest` 的世代常數)。判準本體(`assess()` 裡那段產生
`delivery_sla_missed` / `run_delivered_after_target` 的邏輯)**還在
`run_quality`** —— 它與 `add()` 閉包綁在一起,要拆得再動一次結構,
而那應該獨立成一批(這一批已經有四條行為修正)。

搬移的直接原因是 `run_quality.py` 的 1000 行硬閘門:它不接受第七次
調高數字,所以逼出邊界。這是它第二次擋下「再加一點」。
"""
import datetime as _dt
import re as _re

from run_manifest import MANIFEST_SCHEMA as _CURRENT_MANIFEST_SCHEMA  # noqa: F401
from run_manifest import SCHEMA_V1_DELIVERY_TIMESTAMP as _V1_DELIVERED_AT
from run_manifest import SCHEMA_V2_FIRST_DELIVERY as _V2_FIRST_DELIVERY


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
