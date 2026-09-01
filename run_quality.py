# -*- coding: utf-8 -*-
"""**今天的信「跑成了」嗎** —— 不是「有沒有跑」。

## 為什麼需要這個檔

`tools/report_watchdog.py` 檢查 `run_manifest.json` 的 `date` 夠不夠新,
回答的是「排程有沒有被擠掉」。那道守衛是對的,而且救過事 ——
但它對**跑起來了、卻跑壞了**的日子完全無感。

實際發生過的三次,看門狗全程安靜:

  * **2026-08-04 → 08-08(連續五天)**:特化路徑每天都被自己的引用檢查
    擋下、退回 legacy。信照樣寄出、manifest 照樣更新 —— 使用者是把信
    貼進對話裡才發現的。
  * **2026-08-06**:兩階段全文抓取整段 no-op(`clusters: 0`、`targets: 0`),
    因為 `source_item_id` 還沒補。信裡的事件只有 RSS 兩行摘要。
  * **2026-08-08**:昨日觀點閉環的 state 沒進 push 清單,GitHub Actions
    每天新 runner —— 寫了、從不 commit、次日讀不到。整條閉環是 no-op,
    而本機測試全綠。

三次的共同形狀:**每一塊都跑完了、每一塊都回報成功,而合起來的產出
比它該有的樣子差** —— 而沒有任何一個東西負責看「合起來」。

## 判準怎麼訂

只收**「這件事發生了就是缺陷」**的訊號,不收「今天資料比較少」這類
正常波動。誤報會訓練出忽略告警的反射,而那比沒有告警更糟 ——
這個 repo 已經為此拒絕過一次自動遮蔽(V2-N3)。

每一條都要說得出:**看到什麼、為什麼那是缺陷、讀者少了什麼**。
"""
from __future__ import annotations

import re as _re
import datetime as _dt

import analysis_origin as _ao
import analysis_recap as _arc
import llm_telemetry as _lt
from run_manifest import MANIFEST_SCHEMA as _CURRENT_MANIFEST_SCHEMA
from run_manifest import SCHEMA_V1_DELIVERY_TIMESTAMP as _V1_DELIVERED_AT
from run_manifest import SCHEMA_V2_FIRST_DELIVERY as _V2_FIRST_DELIVERY

#: 已知且可接受的降級步驟。**不在這裡的一律報出來** —— 白名單而不是
#: 黑名單,是因為新的降級原因會不斷出現,而「沒見過的降級」正是最
#: 需要被看見的那一種。
#: **一定生得出 ID 的命名空間。** 它們的來源資料每天都會組出來
#: (行情、校準、資料涵蓋度 —— 即使降級也會留下 note/error 欄位),
#: 所以空掉只可能是 prompt 宣告與 registry 對不上。
#: 其餘的(`portfolio:` / `tension:` / `fact:` / `universe:` …)
#: **當日真的沒有那類資料時空掉是正常的** —— 生產那邊的註解本來就
#: 這樣寫,而第一版的判準忽略了它,等於製造可預期的假警報。
ALWAYS_REALIZABLE = frozenset({"market:", "calibration:", "quality:"})

#: **送達期限**(2026-08-31 使用者定案):信可以晚到,但台股 09:00 開盤
#: 前必須到。判準寫成常數,`tests/test_batch_prod_0831.py` 釘住它 ——
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

KNOWN_DEGRADED = frozenset({
    # 推理強度沒被 provider 套用:影響深度,不影響管線是否走完。
    "llm:effort_not_applied:primary",
    "llm:effort_not_applied:extractor",
    # TAIFEX 來源日期對不上該交易日(2026-08-11 首次在生產觸發:
    # 端點回前一天的資料)。行為是對的 —— 寧可留空也不要錯位
    # (批#83),缺的那一格與原因都在 manifest["chips"]。
    "chips:source_date_mismatch",
    # T86 法人資料當日缺席(2026-08-21 批新增的標籤,**當批漏了註冊**,
    # 缺席日會被誤報成「沒見過的降級」):熱度表只缺法人欄,其餘照常。
    "sector:institutional_missing",
    # 供應商擋下請求(餘額/金鑰)→ 跳過 legacy 直接走緊急備援
    # (2026-08-26)。**這裡註冊只是為了不落成「沒見過的降級」** ——
    # 它本身已經有專屬 finding(`llm_provider_refused_*`,說得出是哪一種、
    # 該做什麼),那條才是通報的主體。
    "llm:provider_refused:payment",
    "llm:provider_refused:auth",
    # 週回顧的延燒事件素材有壞列被跳過(2026-08-27):段落照出,只少骨架
    "weekend_week_review_rows",
    # 備援班的新鮮寄送紀錄讀取失敗(r2 外審 P1):守衛退回只看工作區
    "backup_idempotence_probe",
    # 代號→名稱對照當日取不到:公司鍵遷移照跑,只跳過錯歸因清理。
    "state:alias_map_unavailable",
    # 中職未來賽程有場次、但一場都對不到球場(CPBL 官網對 Actions 的海外
    # IP 可能 geo-block)。賽程照出、只少場地;明細在 manifest.sports。
    "sports:cpbl_venue_missing",
    # TAIFEX 官網當日報表拿不到,退回已知落後的 OpenAPI(日期守衛仍會
    # 把不匹配的值擋在計分外;這是「今天的籌碼可能是舊的」的訊號)。
    "chips:pcr_site_fallback", "chips:large_site_fallback",
    # 時間預算不夠而跳過的加值步驟(核心報告仍完整)。
    "重大事件全文擷取", "podcast", "story_ledger", "story_ledger_save",
    "medical_journals", "sports", "policy",
    # ── 2026-08-30:**這一整批本來全都會變成「沒見過的降級步驟」。**
    # 08/29 的品質告警信實際印出「unknown_degradation —— 沒見過的降級
    # 步驟:gazette」,查下去發現不是漏一個,是漏 17 個 —— 只註冊了
    # `weekend_gazette` 而平日路徑發的是 `gazette`。新增標籤要同批註冊
    # 在消費端,否則退化成「未知」;現在有 `test_every_emitted_degradation
    # _label_is_registered` 機械化守住,不再靠記得。
    "gazette", "weekend_gazette", "weekend_policy_analysis",
    "weekend_week_review", "article_extractor", "horizontal_queries",
    "sector_map_unavailable", "story_ledger_corrupt",
    "analysis_recap_unreadable", "policy_keywords_load",
    "policy_keywords_save", "delivery_receipt_publish",
    # 除權息/公司行動(前綴族,逐個列出來才看得出漏了誰)
    "corpact:fetch_failed", "corpact:delisted_fetch_failed",
    "corpact:history_unreadable", "corpact:persist_failed",
    "corpact:update_failed",
})


def _safe_int(v):
    """拿得到就回 int,拿不到回 0(判準不因型別而爆掉)。"""
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _dig(obj, *path, default=None):
    cur = obj
    for key in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
    return default if cur is None else cur


#: 特化路徑**走完就一定會留下**的紀錄。少任何一格,那份 manifest
#: 就與它自己宣稱的 `analysis_origin` 互相矛盾 —— 而先前
#: `{"llm": {"analysis_origin": "luna_specialized"}}` 這種最小 manifest
#: 會回**空 findings**:空集合真空通過,這個 repo 記過的形狀,
#: 而我自己蓋了一個(外審 P1-3)。
#:
#: 每一格都說得出誰寫的:
#:   payload_budget       ← `payload_budget.apply()`
#:   primary_metrics      ← `_accept_luna()`
#:   recap_saved          ← `_accept_luna()`
#:   request_measurements ← `record_llm_call()`(逐次成對)
#:
#: **「不是 None」不算跑過**(第二十七輪外審 P1-2):`{}`、`[]`、`""`
#: 都不是 `None`,於是一份每一格都空著的 manifest 可以通過 strict ——
#: canary 從「讀錯檔」修到「讀對這一班的檔」,卻還沒證明這一班**產出了
#: 有效內容**。所以每一格都配一個**語意**判準(見 `_BLOCK_CHECKS`),
#: 而不是問它在不在。
SPECIALIZED_REQUIRED = (
    ("payload_budget", "預算政策沒有跑過(`payload_budget.apply`)"),
    ("primary_metrics", "沒有結構化指標(`_accept_luna` 沒走到)"),
    ("recap_saved", "沒有昨日觀點的存檔結果(同上)"),
    ("request_measurements", "沒有逐次的字元/token 量測(沒送出過請求)"),
)

def _pos_int(v) -> bool:
    """**是正整數,而且不是布林**(第二十七輪外審第二輪)。

    `bool` 是 `int` 的子類 —— `True > 0` 成立,於是 `chars: True` 會被
    當成「量到了一個正數」。生產寫進去的是真的整數(見
    `run_manifest.record_llm_call`),判準要照那個型別驗。
    """
    return type(v) is int and v > 0


def _budget_problem(v):
    """預算政策**跑過**的樣子(欄位見 `payload_budget.apply` 的 `report`)。

    第二十八輪外審 P2-1:上一版只真的驗了 `chars_before` 是正整數 ——
    `{"chars_after": null, "limit": "not-a-number", "over_budget": "false"}`
    照樣通過。**值的型別與欄位之間的關係也是契約**。
    """
    if not isinstance(v, dict):
        return "不是物件"
    missing = [k for k in ("chars_before", "chars_after", "limit",
                           "over_budget") if k not in v]
    if missing:
        return f"缺欄位 {missing}"
    if not _pos_int(v.get("chars_before")):
        return f"chars_before 不是正整數:{v.get('chars_before')!r}"
    if not (type(v.get("chars_after")) is int and v["chars_after"] >= 0):
        return f"chars_after 不是非負整數:{v.get('chars_after')!r}"
    if not _pos_int(v.get("limit")):
        return f"limit 不是正整數:{v.get('limit')!r}"
    if type(v.get("over_budget")) is not bool:
        return f"over_budget 不是布林:{v.get('over_budget')!r}"
    # **旗標要與數字一致**:兩者矛盾時,信任哪一個都是猜的。
    if v["over_budget"] is not (v["chars_after"] > v["limit"]):
        return (f"over_budget={v['over_budget']} 與 "
                f"chars_after={v['chars_after']} / limit={v['limit']} 不一致")
    return ""


def _metrics_problem(v):
    """結構化指標**算過**的樣子(欄位見 `analysis_metrics.structured_metrics`)。

    第二十八輪外審 P2-1:上一版只要求那三格「在」——
    `{"claims": null, "sections_present": null, "validation_problems": 999}`
    照樣通過,而最後那個數字非零時,那份輸出根本沒有通過驗證。
    """
    if not isinstance(v, dict) or not v:
        return "是空的"
    if v.get("parsed") is not True:
        return f"parsed 不是 True:{v.get('parsed')!r}"
    missing = [k for k in ("claims", "sections_present", "validation_problems")
               if k not in v]
    if missing:
        return f"缺欄位 {missing}"
    if not (type(v.get("claims")) is int and v["claims"] >= 0):
        return f"claims 不是非負整數:{v.get('claims')!r}"
    # `structured_metrics` 寫出來的是**計數**(見 `analysis_metrics`)——
    # 上一版順手接受了非空清單,那是我自己想像出來的形狀(外審第二輪 F1)。
    sect = v.get("sections_present")
    if not (type(sect) is int and sect > 0):
        return f"sections_present 不是正整數:{sect!r}"
    if type(v.get("validation_problems")) is not int:
        return f"validation_problems 不是整數:{v.get('validation_problems')!r}"
    # **驗證問題非零 = 那份輸出沒有通過驗證**,而 canary 的名字是
    # 「特化輸出真的產生了」—— 帶著未解問題的輸出不算產生。
    if v["validation_problems"] != 0:
        return f"validation_problems={v['validation_problems']}(不是 0)"
    return ""


def _recap_problem(v):
    """昨日觀點的存檔結果要是**明確的狀態**,不是空字串。"""
    import analysis_recap as _arc
    if v is True:                      # 舊的布林(升版前的 manifest)
        return ""
    if v in (_arc.SAVED, _arc.NOTHING):
        return ""
    return f"不是明確的存檔狀態:{v!r}"


def _measurements_problem(v):
    """**至少一筆被接受的逐次量測**,而且字元與 token 都是正數。

    空清單先前讓 token headroom 那條檢查整段不執行 —— 而空集合真空通過
    正是這個模組的 docstring 在講的那件事。
    """
    if not isinstance(v, list) or not v:
        return "沒有任何逐次量測"
    # `accepted` 要**真的是 `True`**:生產寫的是 `bool(accepted)`,
    # 而 `"false"` 這種字串是 truthy —— 用真值判斷會把它當成被接受。
    ok = [x for x in v if isinstance(x, dict) and x.get("accepted") is True
          and _pos_int(x.get("chars")) and _pos_int(x.get("tokens"))]
    if not ok:
        return "沒有一筆是**被接受**且字元/token 都為正的量測"
    # **主分析自己要有一筆**(第二十九輪外審 P2-4):extractor 的量測
    # 湊不了數 —— canary 的名字是「特化**主分析**真的產生了」,
    # 而 primary 一筆都沒有時,上面那條靠 extractor 也能綠。
    if not [x for x in ok if str(x.get("role") or "") == "primary"]:
        return "被接受的量測裡沒有 `primary` —— extractor 的量測湊不了數"
    return ""


#: `manifest["llm"][key]` → 語意判準(回空字串代表合格)。
_BLOCK_CHECKS = {
    "payload_budget": _budget_problem,
    "primary_metrics": _metrics_problem,
    "recap_saved": _recap_problem,
    "request_measurements": _measurements_problem,
}


def _plan_problem(v):
    """兩階段抓取的計畫**跑過**的樣子。整格缺席時,先前那段檢查
    (`if plan:`)是靜默跳過的 —— 而它正是 2026-08-06 整段 no-op 的哨兵。"""
    if not isinstance(v, dict) or not v:
        return "沒有兩階段抓取的計畫(`fetch_plan.plan` 沒跑過)"
    if not isinstance(v.get("available_news"), int):
        return "計畫裡沒有 `available_news` —— 分不出上游斷料與分群壞掉"
    return ""


#: strict 模式(CI canary)額外要求的執行身分欄位。
RUN_BINDING_FIELDS = ("git_sha", "github_run_id", "run_nonce")

#: 這一班寄的是哪一種信。**週日綜合信沒有主分析那一段** ——
#: 它走 `render_weekend_digest_html` 的輕量路徑,不跑行情、不跑事件卡。
#: 拿平日報的判準去量它,每個週日都會發一封「有段落沒跑成」
#: (2026-08-09 生產實際發生)。
MORNING_REPORT = "morning_report"
WEEKEND_DIGEST = "weekend_digest"


def is_weekend_digest(manifest) -> bool:
    """這一班寄的是週日綜合信嗎。

    **沒有這一格時當成平日報**:那是會出聲的那一邊。反過來預設的話,
    一份缺欄位的舊 manifest 會讓所有分析面的判準整批靜默跳過。
    """
    m = manifest if isinstance(manifest, dict) else {}
    return str(m.get("report_kind") or "") == WEEKEND_DIGEST


def assess(manifest, *, mode: str = "watchdog",
           expected_sha: str = "", expected_run_id: str = "",
           expected_nonce: str = "") -> list:
    """回 `[{code, severity, detail}]`(空 = 今天的信跑成了)。

    `severity`:`defect` = 程式或接線壞了;`degraded` = 讀者今天拿到的
    比應有的少,但可能是外部因素(額度、服務不穩)。兩者都值得說,
    分開是因為**要修的東西不一樣**。

    `mode`(外審 P1-3):
      * `watchdog` —— 每日生產。額度用罄那幾天退回 legacy 是外部因素,
        報 `degraded` 讓使用者知道,但不該天天當成程式缺陷。
      * `strict` —— CI canary。那個 job 的名字是「**證明特化輸出真的
        產生了**」,所以退回 legacy 就是不通過;而且要證明這份 manifest
        是**這一次執行**產生的(舊檔案永遠滿足不了 SHA/run id 綁定)。
    """
    m = manifest if isinstance(manifest, dict) else {}
    strict = str(mode) == "strict"
    out: list = []

    def add(code, severity, detail):
        out.append({"code": code, "severity": severity, "detail": detail})

    # ---- 1. 主分析走了哪條路
    #
    # **週日綜合信沒有這一段**(2026-08-09 生產):那條路徑不跑主分析,
    # 判準套上去等於每個週日發一封假警報 —— 而假警報的代價是使用者
    # 開始忽略這封信,連真的那天也一起忽略。
    digest = is_weekend_digest(m)
    origin = _ao.normalize(_dig(m, "llm", "analysis_origin"))
    # 失敗原因跟著**會發出的那一筆**走(2026-08-22 生產 + 外審 r1 P2):
    # 例外名先前只從 unknown_degradation 漏出來,該家族在第 11 條列為
    # 已知之後,原因必須騎在真的會發出的 finding 上 —— 而 emergency 也是
    # Luna 失敗的可能終點(特化失敗 → legacy 再失敗),只掛在
    # not_specialized 分支的話,最嚴重的那種日子反而沒有原因。
    _lerr = _dig(m, "llm", "luna_path_error", "error")
    _lsuffix = f"(特化失敗原因:{str(_lerr)[:160]})" if _lerr else ""
    if digest:
        pass
    elif origin == _ao.EMERGENCY_FALLBACK:
        add("analysis_emergency", "degraded",
            "信裡沒有任何模型判斷,寄出的是 Python 組的備援文字" + _lsuffix)
    elif origin != _ao.LUNA_SPECIALIZED:
        add("analysis_not_specialized", "degraded",
            f"主分析走的是「{_ao.describe(origin)}」—— "
            "特化路徑的事件卡、淨效果、橫向綜合今天都不在信裡" + _lsuffix)

    # ---- 2. 特化路徑被自己的驗證擋下(2026-08-04→08 連續五天)
    #
    # **修補成功的日子不算缺陷**(第一輪外審 F2):`luna_problems` 是
    # 累積的,第一次不合格、第二次修補成功時它仍然留著 —— 只看它非空
    # 就報 defect,會在**特化輸出順利寄出的日子**讓 canary 紅、看門狗
    # 回 2。誤報是這個模組最該避免的東西(見模組 docstring)。
    problems = _dig(m, "llm", "luna_problems", default=[]) or []
    if problems and not digest and origin != _ao.LUNA_SPECIALIZED:
        add("luna_rejected", "defect",
            f"特化輸出被驗證擋下 {len(problems)} 條:"
            + "；".join(str(p) for p in problems[:3]))

    # ---- 3. prompt 宣告了 registry 生不出來的命名空間
    #
    # **空掉不必然是缺陷**(第一輪外審 F4)—— 生產那邊的註解自己就寫著
    # 「當日真的沒有持倉/張力時空掉是對的」,而我在這裡把每一個空掉的
    # 命名空間都報成程式缺陷,等於製造可預期的假警報。
    # 只報 `ALWAYS_REALIZABLE`:它們的來源資料**每天都會組出來**
    # (行情、校準、涵蓋度即使降級也會留下 note/error 欄位),
    # 空掉只可能是 prompt 宣告與 registry 對不上 —— 那正是 2026-08-08
    # 讓特化分析連續五天作廢的那個缺陷。
    unreal = [x for x in (_dig(m, "llm", "unrealizable_namespaces",
                               default=[]) or [])
              if str(x) in ALWAYS_REALIZABLE]
    if unreal:
        add("namespace_unrealizable", "defect",
            "這些命名空間宣告在 prompt 裡卻生不出任何 ID(模型照規則猜"
            f"名字必被判不存在):{'、'.join(str(x) for x in unreal)}")

    phantom = _dig(m, "llm", "phantom_evidence_refs", default=[]) or []
    if phantom:
        add("phantom_refs", "defect",
            f"張力宣稱了 packet 沒有的路徑:{'、'.join(str(x) for x in phantom[:3])}")

    # ---- 4. payload 超出硬閘門
    if _dig(m, "llm", "payload_budget", "over_budget") is True:
        add("payload_over_budget", "defect",
            "請求超過硬閘門 —— 裁背景與第二層壓縮都沒能把它壓下來")

    # ---- 5. 兩階段全文抓取的接線(2026-08-06 整段 no-op)
    plan = _dig(m, "news", "fulltext_plan", default={}) or {}
    if plan:
        clusters = int(plan.get("clusters") or 0)
        targets = len(plan.get("targets") or []) if isinstance(
            plan.get("targets"), list) else int(plan.get("targets") or 0)
        # **「分不出群」與「今天沒有新聞」是兩件事**(第一輪外審 F3)。
        # 零新聞時零群集是必然結果,不是接線壞了 —— 報成 defect 會讓
        # 上游斷料的日子看起來像程式有 bug,而該查的地方完全不同。
        avail = plan.get("available_news")
        if clusters == 0 and isinstance(avail, int) and avail == 0:
            add("news_upstream_empty", "degraded",
                "今天一則新聞都沒抓到 —— 上游斷料,不是分群壞了。"
                "信裡的事件段落會是空的")
        elif clusters == 0:
            add("fetch_plan_no_clusters", "defect",
                f"有 {avail if isinstance(avail, int) else '未知數量'} 則新聞卻"
                "分不出任何事件群 —— 信裡的事件只會有 RSS 兩行摘要"
                "(2026-08-06 的形狀:source_item_id 還沒補)")
        elif targets == 0:
            # **「沒東西可抓」不是接線斷了**(2026-08-09 P2):候選全都
            # 已經有全文、或全都沒有 http 連結時,零是正確答案。
            # 拿不到這個數字時仍報 defect —— 那是會出聲的那一邊。
            cand = plan.get("fetchable_candidates")
            if isinstance(cand, int) and cand == 0:
                # **兩種零的後果相反**(外審):候選已經有全文 → 信裡的
                # 事件**有**全文;候選沒有連結 → 只剩 RSS 兩行摘要。
                # 講一句對其中一半是假的話,等於用一個假的診斷換掉一個
                # 假的缺陷 —— 讀著訊息的人會去查錯的地方。
                got = plan.get("already_fulltext")
                none = plan.get("no_fetch_link")
                add("fetch_plan_nothing_to_fetch", "degraded",
                    f"分出了 {clusters} 個事件群,沒有需要再排的全文目標"
                    + (f"(已經有全文 {got} 篇、沒有可抓連結 {none} 篇)"
                       if isinstance(got, int) and isinstance(none, int)
                       else "(候選已經有全文、或沒有可抓的連結)"))
            else:
                add("fetch_plan_no_targets", "defect",
                    f"分出了 {clusters} 個事件群卻一篇全文都沒排 —— 接線斷了"
                    + (f"(可抓的候選有 {cand} 篇)" if isinstance(cand, int)
                       else ""))

    # ---- 6. 昨日觀點閉環(2026-08-08:state 沒進 push 清單)
    # **「沒東西可存」不是「存檔失敗」**(2026-08-09 P2):上一版兩者
    # 都是 `False`,而這裡一律報 defect —— 那句話在資料稀薄的日子是假的。
    # 舊的布林 `False` 仍當成失敗(那是會出聲的那一邊)。
    _recap = _dig(m, "llm", "recap_saved")
    if origin == _ao.LUNA_SPECIALIZED and (
            _recap is False or _recap == _arc.FAILED):
        add("recap_not_saved", "defect",
            "分析成功但昨日觀點沒存下來 —— 明天的延續事件沒有 diff 基準")

    # ---- 6b. **state 裡混著兩代身分**(2026-08-09 P2)
    #
    # `legacy_remaining` 一直記著,而**沒有任何東西讀它** —— 遷移只成功
    # 一半(舊鍵接不上新公式)時,那些記錄會留在 state 裡直到過期,
    # 而它們沒有 `incident_tokens`:同鍵下的新事件比對「兩邊都不知道」
    # 會被當成同一樁,於是併進一條可能是別件事的 lineage 並繼承它的天數。
    # 升版當天出現是正常的(隔天就該歸零);**連續出現才是問題**,
    # 而看不見的話沒有人會發現「連續」。
    # **判準要與寫出來的話一致**(第二十七輪外審 P2-4):訊息說「升版當天
    # 正常,隔天還在才是問題」,而上一版只看單次 snapshot —— 於是新公式
    # 第一次上線那天必然報一次 degraded,即使那正是程式自己認定的正常狀態。
    # 生產在寫 manifest 前把上一班的數字帶過來(`previous_legacy_remaining`)。
    _left = _safe_int(_dig(m, "event_identity", "legacy_remaining"))
    _prev = _dig(m, "event_identity", "previous_legacy_remaining")
    if _left and isinstance(_prev, int):
        if _left > _prev:
            add("identity_generations_mixed", "defect",
                f"舊版身分公式的記錄從 {_prev} 條**增加到** {_left} 條 —— "
                "遷移不只是沒接上,是還在往回長")
        elif _left >= _prev:
            add("identity_generations_mixed", "degraded",
                f"事件 timeline 裡還有 {_left} 條是舊版身分公式寫的,"
                f"而上一班是 {_prev} 條 —— **沒有下降**。它們沒有 "
                "incident_tokens,同鍵下的新事件會另開一條而不是接上去")

    # ---- 7. 字元閘門還擋得住嗎(代理的誤差要被量,不是被假設)
    import payload_budget as _pb
    head = _pb.proxy_headroom(m)
    if head:
        implied = head["implied_token_ceiling"]
        floor = _pb.OBSERVED_REJECTED_TOKENS * _pb.PROXY_ALERT_FRACTION
        if implied >= floor:
            add("payload_proxy_thin", "defect",
                f"字元上限今天只換到 {implied:,} token 的餘裕 —— 逼近實測會被"
                f"拒收的 {head['observed_rejected_tokens']:,}"
                f"(今日 {head['chars_per_token']} 字元/token)。"
                "閘門擋的是字元、provider 算的是 token,而這個比例隨當日"
                "語言組合浮動:偏中文的日子同樣的字元預算會換到多得多的 token")

    # ---- 8. **宣稱走了特化路徑,就要留下那條路徑的紀錄**(外審 P1-3)
    if origin == _ao.LUNA_SPECIALIZED:
        missing = []
        for k, why in SPECIALIZED_REQUIRED:
            v = _dig(m, "llm", k)
            if v is None:
                missing.append(f"`{k}`({why})")
                continue
            # **「有這一格」不等於「跑過」**(第二十七輪外審 P1-2)。
            bad = _BLOCK_CHECKS[k](v)
            if bad:
                missing.append(f"`{k}`({bad})")
        if missing:
            add("manifest_incomplete", "defect",
                "manifest 說走的是特化路徑,卻缺了那條路徑必然會寫下的"
                "紀錄:" + "、".join(missing)
                + " —— 要嘛是主流程中途死掉,要嘛這份 manifest 不是"
                  "這一次執行產生的")

    # ---- 9. 最終 request 被閘門擋下(packet 沒超不代表 request 沒超)
    # **終局的超標才是缺陷**(第二輪外審 F2)。加深那次超標時主流程會
    # `break` 並沿用留著的合法第一版 —— 那條路徑走完仍是特化輸出,
    # 對讀者沒有任何損失。只看旗標會對一份成功的報告報 defect。
    if (_dig(m, "llm", "payload_budget", "final_request_over_budget") is True
            and _dig(m, "llm", "payload_budget",
                     "final_request_over_budget_recovered") is not True):
        add("final_request_over_budget", "defect",
            "最終 request 超過硬閘門而被擋下,且沒有回收 —— packet 層的 "
            "over_budget 是 False,只看那一格會完全看不到這件事")

    # ---- 9b. 模式無關的品質降級(外審 r1,P1:這兩條原本在 strict 區,
    # 每日 watchdog 走 mode="watchdog" 永遠看不到 —— 品質信不會提它們)。
    # **撿回一筆不等於健康**(P2-1):解析器自己說了有列確定丟失
    # (ok_array_salvaged + skipped>0),35→1 與 35→30 不再都叫「活著」。
    _exq = m.get("llm_extractor")
    if isinstance(_exq, dict) and _exq.get("called") is True:
        _pdq = _exq.get("parse") or {}
        _aliveq = (_safe_int(_exq.get("survived"))
                   if "survived" in _exq else _safe_int(_exq.get("valid")))
        if (str(_pdq.get("kind") or "") == "ok_array_salvaged"
                and _safe_int(_pdq.get("skipped")) > 0 and _aliveq > 0):
            add("event_extractor_partial", "degraded",
                f"抽取器逐列撿回:{_safe_int(_exq.get('items'))} 筆進、"
                f"{_safe_int(_pdq.get('salvaged'))} 筆撿回、"
                f"{_safe_int(_pdq.get('skipped'))} 筆確定丟失 ——"
                " 信寄得出去,但事件面比它該有的樣子少")
    # **看得見丟掉還不夠**(P2-2):信裡渲染了「後續觀察點」而帳本
    # 沒接住 —— telemetry 有了,還要有人讀它。
    _wd = _safe_int(_dig(m, "llm", "watch_dropped_capacity"))
    if _wd > 0:
        add("watch_dropped_capacity", "degraded",
            f"觀察點帳本已滿,今天有 {_wd} 條新觀察點沒被記住 ——"
            " 信裡寫了「接下來觀察」,明天它會無聲消失")

    # ---- 10. strict(CI canary):綠燈必須代表「特化輸出真的產生了」
    if strict:
        if digest:
            # **「無法證明」不是「證明了」。** canary 的名字是「特化輸出
            # 真的產生了」,而週日那條路徑根本不跑主分析 —— 讓它靜默通過
            # 等於把一個量不到東西的綠燈當成證據。
            add("canary_on_a_non_trading_day", "defect",
                "這一班寄的是週日綜合信 —— 那條路徑不跑主分析,"
                "canary 證明不了任何事。請在交易日重新 dispatch")
        elif origin != _ao.LUNA_SPECIALIZED:
            add("canary_not_specialized", "defect",
                f"canary 的判準是「特化輸出真的產生了」,而這一班走的是"
                f"「{_ao.describe(origin)}」—— 退回 legacy 對每日生產是"
                "可接受的降級,對 canary 是不通過")
        # **整格缺席不得靜默通過**(第二十七輪外審 P1-2)。
        # `fulltext_plan` 那段檢查包在 `if plan:` 裡,而它正是 2026-08-06
        # 整段 no-op 的哨兵 —— 計畫從來沒跑過的那一班,先前一句話都不說。
        # `report_kind` 同理:缺席時判準會退回「當成平日報」,
        # 那個預設對每日生產是對的,對 canary 是「沒證明」。
        if not digest:
            bad_plan = _plan_problem(_dig(m, "news", "fulltext_plan"))
            if bad_plan:
                add("canary_no_fetch_plan", "defect",
                    f"canary 沒有兩階段抓取的證據:{bad_plan}")
        # **`nothing_to_save` 蓋得住兩種完全不同的日子**(外審 P2-3):
        # 「今天真的沒東西」與「有東西但 mapping 壞掉一條都抽不出來」。
        # 有分子分母才分得開 —— eligible > 0 而一條都沒抽出來,
        # 那是閉環接線失敗,不是清淡的一天。
        import analysis_recap as _arc2
        if (_dig(m, "llm", "recap_saved") == _arc2.NOTHING
                and _safe_int(_dig(m, "llm", "recap_eligible")) > 0
                and _safe_int(_dig(m, "llm", "recap_extracted")) == 0):
            add("recap_extraction_dead", "defect",
                f"分析裡有 {_safe_int(_dig(m, 'llm', 'recap_eligible'))} 條"
                "候選觀點,而 recap 一條都沒抽出來 —— `nothing_to_save` "
                "在這種日子是接線失敗的偽裝,不是清淡")
        # **「送得出去」不等於「這個能力活著」**(第三十輪外審 P2-3):
        # 2026-08-10 的實機 manifest:`called=true, items=35, parsed=0,
        # valid=0, outcome="ok"` —— 抽取器吃了 35 筆、DeepSeek 失敗後
        # 換 Gemini,最後零筆有效輸出,而 `outcome` 還叫 ok、strict 全綠。
        # capability_health 那邊早就把它列進 `inactive_capabilities`,
        # 只是驗收沒有讀。
        _ex = m.get("llm_extractor")
        # **缺席不得真空通過**(第三十一輪外審 P1-5):先前只在
        # `called is True` 時檢查 —— 接線 regression 讓抽取器整個沒被
        # 呼叫時,manifest 沒有這個區塊,strict 反而全綠。三種形狀分開:
        #   * 區塊不存在        → 接線壞了(停用路徑也會留 called=false);
        #   * called=false      → 刻意停用(disabled)不報;
        #                          沒金鑰等其他原因 → 能力沒跑,報出來;
        #   * called=true       → 走既有的零產出判準。
        if not isinstance(_ex, dict):
            add("event_extractor_missing", "defect",
                "manifest 沒有 llm_extractor 區塊 —— 抽取器整個沒被接上"
                "(刻意停用也會留 called=false 的紀錄,缺席只能是接線壞)")
        elif _ex.get("called") is not True:
            if not _ex.get("disabled"):
                add("event_extractor_not_called", "defect",
                    f"抽取器沒有被呼叫(outcome={_ex.get('outcome')!r})——"
                    " 能力設了卻沒跑,與「今天沒事件」在信裡長得一樣")
        if isinstance(_ex, dict) and _ex.get("called") is True:
            _items = _safe_int(_ex.get("items"))
            # **判準與 capability_health 同一條**(外審 r1):那邊用的是
            # `survived`(真的活到下游的事件數),而 `parsed` 只代表
            # 「JSON 解得開」、`valid` 只代表「通過 schema」——
            # 解得開但全部不合格、或合格卻全被合併掉,都是零產出。
            # 兩邊各寫一次的話,一邊說 inactive、一邊說綠燈(現況)。
            _alive = (_safe_int(_ex.get("survived"))
                      if "survived" in _ex else _safe_int(_ex.get("valid")))
            if _items > 0 and _alive <= 0:
                # **原因要跟著訊息走**(2026-08-11):只說「活到下游 0 筆」
                # 的話,收信的人還是得自己去猜是哪一段 —— 而四種原因的
                # 處置完全不同(見 `llm_postprocess._parse_llm_event_json`)。
                _pd = _ex.get("parse") or {}
                _pk = str(_pd.get("kind") or "")
                if _pd.get("error"):
                    _pk = f"{_pk}({str(_pd['error'])[:60]})"
                add("event_extractor_dead", "defect",
                    f"事件抽取器吃了 {_items} 筆、活到下游 0 筆"
                    f"(parsed={_safe_int(_ex.get('parsed'))}、"
                    f"outcome={_ex.get('outcome')!r}"
                    + (f"、解析={_pk}" if _pk else "")
                    + ")—— 「送得出去」不等於「這個能力活著」")
        if not str(m.get("report_kind") or ""):
            add("canary_no_report_kind", "defect",
                "manifest 沒說這一班寄的是哪一種信 —— 判準會退回"
                "「當成平日報」,那個預設對每日生產是對的,對 canary 是"
                "「沒證明」")
        missing_bind = [k for k in RUN_BINDING_FIELDS if not _dig(m, k)]
        if missing_bind:
            add("run_binding_missing", "defect",
                f"manifest 沒有執行身分:{'、'.join(missing_bind)} —— "
                "沒有它就證明不了這份檔案是這一次跑出來的")
        for got, want, what in ((_dig(m, "git_sha"), expected_sha, "git_sha"),
                                (_dig(m, "github_run_id"), expected_run_id,
                                 "github_run_id"),
                                # **nonce 要比對才是綁定**(外審 P2-5):
                                # 只驗非空的話它只是一個存在性欄位,
                                # 證明不了「這是那一次 process invocation」。
                                (_dig(m, "run_nonce"), expected_nonce,
                                 "run_nonce")):
            if want and str(got or "") != str(want):
                add("run_binding_mismatch", "defect",
                    f"{what} 對不上:manifest 是 {got or '(空)'}、"
                    f"這一次是 {want} —— **讀到的是別次執行的 manifest**"
                    "(`state/run_manifest.json` 進版控,checkout 之後就在那裡)")

    # ---- 10c. 供應商把請求擋在門外(餘額/金鑰)
    #
    # 這與「服務不穩」**處置完全不同**:重試幾次都一樣,要有人去儲值或
    # 換金鑰,而且在那之前**每一班都會這樣**。2026-08-15 生產:DeepSeek
    # 402 Insufficient Balance,主分析三個模型與抽取器全被擋,信只剩備援
    # 模型寫的版本 —— 而品質信當時只說得出「沒見過的降級」。
    _refusals = {}
    for _a in (_dig(m, "llm", "attempts", default=[]) or []):
        _why = _lt.refusal_reason(str((_a or {}).get("error") or ""))
        if _why:
            _refusals.setdefault(_why, set()).add(
                str((_a or {}).get("provider") or "?"))
    _ex_err = str(_dig(m, "llm_extractor", "error", default="") or "")
    _ex_why = _lt.refusal_reason(_ex_err)
    if _ex_why:
        _refusals.setdefault(_ex_why, set()).add("extractor")
    for _why, _who in sorted(_refusals.items()):
        add("llm_provider_refused_" + _why, "degraded",
            ("供應商擋下請求(%s):%s —— %s。"
             "**重試不會好,而且在處理之前每一班都會這樣。**"
             % (_why,
                "、".join(sorted(_who)),
                "帳戶餘額不足,需要儲值" if _why == "payment"
                else "金鑰無效或沒有權限,需要換金鑰/開通")))

    # ---- 10b. 持久 state 壞掉(2026-08-22 外審 P3)
    #
    # 資料是安全的(寫入端 fail-closed、原檔沒被覆寫),但**需要人處理**:
    # 壞檔不會自己好,而且在修好之前那份 state 每天都不會更新。
    # 說出「哪一份壞了」比「發現未知降級」有用得多 —— 後者還會把讀者
    # 引去「字彙缺漏」的方向。
    _corrupt = sorted({str(s).split(":", 2)[-1]
                       for s in (m.get("degraded_steps") or [])
                       if str(s).startswith("state:corrupt:")})
    if _corrupt:
        _why = {str(r.get("file")): str(r.get("why") or "")
                for r in (_dig(m, "state_writes", "corrupt", default=[]) or [])
                if isinstance(r, dict)}
        add("persistent_state_corrupt", "defect",
            "持久 state 讀不動(寫入端已 fail-closed、原檔未被覆寫,但在修好"
            "之前它每天都不會更新):"
            + "、".join(f"{f}({_why[f][:60]})" if _why.get(f) else f
                       for f in _corrupt))

    # ---- 10a. **09:00 SLA**(2026-08-31 使用者定案:信可以晚到,但台股
    # 開盤前必須到)。先前 state 只有 `date`(**開跑時刻**),而 08/31 那班
    # `date=08:30`、`total_seconds=2088` —— 信其實 09:05 才寄出,監控資料
    # 看起來卻像「08:30 成功」。**宣告了 SLA 卻沒有欄位能稽核它**,那條
    # SLA 就只是一句話。判準吃 `delivery.delivered_at`(寄出的那一刻)。
    #
    # 舊 manifest 沒有這個欄位 —— **不當成違規**(那會在部署當天產生一次
    # 確定的假警報,而假警報會訓練人忽略告警)。
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
    if _dv.get("success") and _at:
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
    elif _dv.get("success") and (
            _schema_bad or _schema >= MANIFEST_SCHEMA_WITH_DELIVERED_AT):
        # 這一版以後就是必填 —— 缺了不是「沒問題」,是「SLA 無法稽核」。
        add("delivered_at_missing", "defect",
            f"寄送成功卻沒有寫下實際寄出時刻(manifest_schema={_schema})"
            " —— SLA 無法稽核,而它是使用者定案的送達期限")

    # ---- 10b. **登記過 ≠ 可以靜音**(2026-08-30 外審)
    # 上一批為了修「沒見過的降級步驟:gazette」,把 17 個標籤一次補進
    # `KNOWN_DEGRADED` —— 而那個集合的語意是「已知**且可接受**」。
    # 結果 `story_ledger_corrupt`(120 天敘事連續性讀不動)、
    # `delivery_receipt_publish`(防重複寄信的收據沒發佈成功)、
    # `analysis_recap_unreadable`(明天的昨日觀點會缺)這三個**安全機制
    # 失效**的訊號,從「至少會報 unknown」變成完全不出聲 —— 修正比缺陷
    # 更糟的形狀。登記讓掃描守衛過得去,能不能靜音是另一個問題。
    _ALARMING = {
        "story_ledger_corrupt": (
            "defect", "敘事帳本讀不出來 —— 今天的信沒有 120 天的事件脈絡,"
            "而且舊檔刻意不覆寫(fail-closed);持久檔要修"),
        "delivery_receipt_publish": (
            "defect", "寄送收據沒能發佈 —— 那是防止備援班重複寄信的證據,"
            "它失效時「已經寄過」在 origin/main 上沒有紀錄"),
        "analysis_recap_unreadable": (
            "defect", "昨日觀點讀不出來 —— 明天的「敘事變化」會整段缺席"),
    }
    for _label, (_sev, _why) in _ALARMING.items():
        if _label in (m.get("degraded_steps") or []):
            add(_label, _sev, _why)

    # ---- 11. 沒見過的降級
    # `llm:luna_path_failed:<例外類名>` 是**已分類的家族**:後綴是開放集,
    # frozenset 列舉不完(2026-08-22 生產:PayloadBudgetExceeded 被當成
    # 「沒見過的降級」,把讀者引去「字彙缺漏」的方向 —— 真正的故事由
    # analysis_not_specialized(帶失敗原因)與最終閘門判準講)。
    # 前綴豁免**只給這一個**確定開放的家族;其餘標籤維持逐一列舉,
    # 不得變成順手塞新標籤的後門。
    # `state:corrupt:<檔名>` 與 `llm:luna_path_failed:<例外類名>` 都是**後綴
    # 開放**的家族,frozenset 列舉不完;各自有專屬 finding 把話說清楚
    # (見上面兩條),所以不再從這裡以「沒見過」的名義重報一次。
    # `recap:not_previous_session:<日期>` 同樣是後綴開放的家族(日期會變),
    # 專屬 finding 在下面 —— 它說得出停在哪一天,catch-all 只會說「沒見過」。
    _stale_recap = [str(s) for s in (m.get("degraded_steps") or [])
                    if str(s).startswith("recap:not_previous_session:")]
    if _stale_recap:
        add("recap_not_previous_session", "degraded",
            "昨日觀點不是上一個交易日,整段未進 EVIDENCE:"
            + "、".join(x.split(":", 2)[-1] for x in _stale_recap)
            + "(通常代表前一班主分析落回 legacy,recap 沒有更新)")
    unknown = [s for s in (m.get("degraded_steps") or [])
               if str(s) not in KNOWN_DEGRADED
               and not str(s).startswith(("llm:luna_path_failed:",
                                          "state:corrupt:",
                                          "recap:not_previous_session:"))]
    if unknown:
        add("unknown_degradation", "degraded",
            "沒見過的降級步驟:" + "、".join(str(s) for s in unknown))

    return out


def summarize(findings) -> str:
    """給告警信用的一段文字(空 = 回空字串)。"""
    rows = [f for f in (findings or []) if isinstance(f, dict)]
    if not rows:
        return ""
    defects = [f for f in rows if f.get("severity") == "defect"]
    head = (f"今天的晨報跑起來了,但有 {len(rows)} 項不對"
            + (f"(其中 {len(defects)} 項是程式或接線的缺陷)" if defects else "")
            + ":")
    return head + "\n" + "\n".join(
        f"  [{f.get('severity')}] {f.get('code')} —— {f.get('detail')}"
        for f in rows)
