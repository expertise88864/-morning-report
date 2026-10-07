"""Mirror Python-authority conclusion reconciliation in the last-resort email.

The normal renderer can fail after guarding prose; the minimal path must not
deliver a surviving contradictory label. This pure helper does not score,
fetch, persist, send or import the main module. Unknown authority stays with
the existing minimal renderer's unknown-state handling.
"""
from collections.abc import Callable

from conclusion_guard import fallback, summary_word


def reconcile(text: str, stance: dict, model_stance: dict, summary: str,
              strip_sections: Callable[[str, tuple], str], manifest: dict) -> str:
    label = str(stance.get("label") or "")
    if not isinstance(stance.get("total"), int) or not label:
        return text
    model_label = str(model_stance.get("label") or "")
    word = summary_word(summary, label)
    if not ((model_label and model_label != label) or (word and word != label)
            or (not model_label and not word)):
        return text
    manifest.setdefault("llm", {})["minimal_stance_echo_reconciled"] = True
    return fallback(label) + "\n\n" + strip_sections(text, ("我的明確立場", "一句話總結"))
