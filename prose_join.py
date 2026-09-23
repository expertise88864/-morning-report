"""Join prose fields without changing their words, numbers or uncertainty."""


def sentences(parts):
    result = []
    for part in parts:
        text = str(part or "").strip().strip("、，,；; ")
        if text:
            result.append(text if text.endswith(("。", "！", "？", "!", "?", ".", "．")) else text + "。")
    return " ".join(result)
