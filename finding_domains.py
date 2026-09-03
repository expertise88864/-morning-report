# -*- coding: utf-8 -*-
"""**每一條 finding 屬於哪一類** —— 宣告,不是從名字猜。

2026-09-01 r9 外審:看門狗用 `code.startswith(("analysis_", "luna_", ...))`
判「這是不是信的內容問題」,好在刻意不寄的日子只驗控制面。實測 47 個
code 裡只有 7 個符合那些前綴 —— `fetch_plan_no_clusters` /
`payload_over_budget` / `phantom_refs` / `event_extractor_partial` /
`watch_dropped_capacity` 這些**明顯是內容管線**的 finding 全都會被當成
控制面,在那些日子就是假警報。而假警報會訓練人忽略告警。

`test_every_finding_declares_its_domain` 掃 `run_quality.py` 裡所有
`add("...")` 的字面 code,要求每一個都在這裡登記(前綴家族除外)。
"""


#: finding 屬於哪一類 —— **宣告,不是從名字猜**(2026-09-01 r9 外審)。
#:
#: 先前看門狗用 `code.startswith(("analysis_", "luna_", ...))` 判「這是不是
#: 信的內容問題」。實測 47 個 code 裡只有 7 個符合那些前綴,而
#: `fetch_plan_no_clusters` / `payload_over_budget` / `phantom_refs` /
#: `event_extractor_partial` / `watch_dropped_capacity` 這些**明顯是內容
#: 管線**的 finding 全都會被當成控制面 —— 在「今天刻意不寄」的日子就是
#: 假警報,而假警報會訓練人忽略告警(這個系統修過三次同型問題)。
DOMAIN_CONTROL_PLANE = "control_plane"   #: state / schema / 綁定 / 寄送契約
DOMAIN_CONTENT = "content"               #: 信的內容與產生它的管線

#: 前綴家族(code 會接上原因,例如 `llm:provider_refused:payment`)。
_DOMAIN_PREFIXES = (
    ("delivery_record_", DOMAIN_CONTROL_PLANE),
    ("llm_provider_refused_", DOMAIN_CONTENT),
    ("state:corrupt:", DOMAIN_CONTROL_PLANE),
    ("extractor", DOMAIN_CONTENT),
)

_FINDING_DOMAINS = {
    # ---- 控制面:這一班有沒有寄到、紀錄本身可不可信
    "delivered_at_invalid": DOMAIN_CONTROL_PLANE,
    "delivered_at_missing": DOMAIN_CONTROL_PLANE,
    "delivered_at_unparsable": DOMAIN_CONTROL_PLANE,
    "delivery_sla_missed": DOMAIN_CONTROL_PLANE,
    # r18:evidence packet 的邊界正規化 —— 送進模型的東西被改過,
    # 那是**接線**的問題(不是「信的內容不夠好」),所以刻意不是 content:
    # 刻意不寄信的日子也要驗(`_control_plane_exit` 會留下它們)。
    "evidence_value_stringified": DOMAIN_CONTROL_PLANE,
    "evidence_key_collision": DOMAIN_CONTROL_PLANE,
    "evidence_value_dropped": DOMAIN_CONTROL_PLANE,
    "delivery_state_invalid": DOMAIN_CONTROL_PLANE,
    "delivery_structure_invalid": DOMAIN_CONTROL_PLANE,
    "delivery_timestamp_order_invalid": DOMAIN_CONTROL_PLANE,
    "first_delivered_at_invalid": DOMAIN_CONTROL_PLANE,
    "first_delivered_at_missing": DOMAIN_CONTROL_PLANE,
    "first_delivered_at_out_of_range": DOMAIN_CONTROL_PLANE,
    "run_delivered_after_target": DOMAIN_CONTROL_PLANE,
    "manifest_business_date_invalid": DOMAIN_CONTROL_PLANE,
    "manifest_schema_invalid": DOMAIN_CONTROL_PLANE,
    "manifest_schema_missing": DOMAIN_CONTROL_PLANE,
    "manifest_schema_unsupported": DOMAIN_CONTROL_PLANE,
    "run_binding_mismatch": DOMAIN_CONTROL_PLANE,
    "run_binding_missing": DOMAIN_CONTROL_PLANE,
    "persistent_state_corrupt": DOMAIN_CONTROL_PLANE,
    # 全案審查 2026-09-03 TC-2:`state:write_failed:<檔名…>` 這個家族先前既沒
    # 登記也沒有專屬 finding —— 每次出現都被報成「沒見過的降級步驟」。
    "state_write_failed": DOMAIN_CONTROL_PLANE,
    # LLM 設定(fatal / 非致命)是控制面的事:與信的內容無關,刻意不寄的日子也該知道。
    "llm:config_invalid": DOMAIN_CONTROL_PLANE,
    "llm:config_issue": DOMAIN_CONTROL_PLANE,
    "identity_generations_mixed": DOMAIN_CONTROL_PLANE,
    "unknown_degradation": DOMAIN_CONTROL_PLANE,
    # ---- 內容:今天的信夠不夠好(刻意不寄的日子沒有信可談)
    # 全案審查 2026-09-03 TC-2:`dq:<source>:<check>`(data_quality 的 error 級
    # 檢查)同樣是沒登記、沒 finding 的動態家族;原始資料在 manifest 的
    # `data_checks.errors`。餵信的資料來源壞了是內容面的事(週日沒有這批抓取)。
    "data_quality_error": DOMAIN_CONTENT,
    # 渲染失敗(同批):主體失敗寄的是極簡版(defect);單張卡被略過(degraded)。
    "render_body_failed": DOMAIN_CONTENT,
    "render_card_failed": DOMAIN_CONTENT,
    "analysis_emergency": DOMAIN_CONTENT,
    "analysis_not_specialized": DOMAIN_CONTENT,
    "luna_rejected": DOMAIN_CONTENT,
    "news_upstream_empty": DOMAIN_CONTENT,
    "recap_extraction_dead": DOMAIN_CONTENT,
    "recap_not_previous_session": DOMAIN_CONTENT,
    "recap_not_saved": DOMAIN_CONTENT,
    "canary_no_fetch_plan": DOMAIN_CONTENT,
    "canary_no_report_kind": DOMAIN_CONTENT,
    "canary_not_specialized": DOMAIN_CONTENT,
    "canary_on_a_non_trading_day": DOMAIN_CONTENT,
    "event_extractor_dead": DOMAIN_CONTENT,
    "event_extractor_missing": DOMAIN_CONTENT,
    "event_extractor_not_called": DOMAIN_CONTENT,
    "event_extractor_partial": DOMAIN_CONTENT,
    "fetch_plan_no_clusters": DOMAIN_CONTENT,
    "fetch_plan_no_targets": DOMAIN_CONTENT,
    "fetch_plan_nothing_to_fetch": DOMAIN_CONTENT,
    "final_request_over_budget": DOMAIN_CONTENT,
    "manifest_incomplete": DOMAIN_CONTENT,
    "namespace_unrealizable": DOMAIN_CONTENT,
    "payload_over_budget": DOMAIN_CONTENT,
    "payload_proxy_thin": DOMAIN_CONTENT,
    "phantom_refs": DOMAIN_CONTENT,
    "watch_dropped_capacity": DOMAIN_CONTENT,
    # ---- `_ALARMING` 家族(**動態產生**,不是字面 `add("...")`)
    # r10 外審:守衛只掃字面 code,這三個因此從來沒被檢查過,
    # 全部落到「沒登記 → 預設控制面」。前兩個當控制面是對的,
    # 但 `analysis_recap_unreadable` 是**內容連續性** —— 明天的昨日觀點
    # 會缺,那是信的內容問題,在刻意不寄的日子不該拿它報警。
    "story_ledger_corrupt": DOMAIN_CONTROL_PLANE,
    "delivery_receipt_publish": DOMAIN_CONTROL_PLANE,
    "analysis_recap_unreadable": DOMAIN_CONTENT,
}


def finding_domain(code: str) -> str:
    """這條 finding 屬於哪一類。**沒登記就是控制面** —— 失效方向要選
    「多吵一次」而不是「該吵的時候不吵」:新增 finding 忘了登記時,
    最壞是刻意不寄的日子多一則訊息;反過來則是控制面的問題永遠靜默。
    `test_every_finding_declares_its_domain` 會擋下沒登記的 code。
    """
    code = str(code or "")
    known = _FINDING_DOMAINS.get(code)
    if known:
        return known
    for prefix, domain in _DOMAIN_PREFIXES:
        if code.startswith(prefix):
            return domain
    return DOMAIN_CONTROL_PLANE
