"""Distinguish final rejection from accumulated repair diagnostics."""


def describe(llm: dict) -> str:
    history = llm.get("luna_problems") or []
    attempts = [a for a in llm.get("attempts") or []
                if isinstance(a, dict) and a.get("role") == "primary" and a.get("reject_reason")]
    last = attempts[-1] if attempts else {}
    reason = last.get("reject_reason")
    count = last.get("problems_total")
    if reason:
        label = (f"最後一輪 {count} 項驗證問題"
                 if isinstance(count, int) and not isinstance(count, bool) and count > 0
                 else "最後一輪未通過")
        return (f"{label}：{str(reason)[:800]}"
                f"（特化共 {len(attempts)} 次嘗試；歷次錯誤摘錄 {len(history)} 條，"
                "不是最後剩餘問題數）")
    return (f"特化輸出未通過，歷次錯誤摘錄 {len(history)} 條"
            "（缺少最後一輪明細）：" + "；".join(str(p) for p in history[:3]))
