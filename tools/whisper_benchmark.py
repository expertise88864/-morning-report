# -*- coding: utf-8 -*-
"""**whisper 模型對照報告**(OPTIMIZATION_PLAN V2 · N6)。

現行核心節目用 `medium`。`large-v3-turbo` 據稱更快也更準 —— 但那是別人
在別人的硬體上量的。這支腳本在**我們自己的 runner、我們自己的節目**上量:
耗時、字數、以及**同一段音檔兩個模型各自轉出什麼**。

## 只產報告

計劃書寫得很清楚:「**只產報告**;換模型需使用者看過同意」。所以這裡
不寫 `state/`、不改設定、不 commit —— 輸出是一份 artifact。

## 為什麼要看抽樣段落而不是只看字數

字數多不代表準。中文轉錄最常見的失效是**公司名與數字聽錯**
(「台積電」→「台기電」、「八十億」→「八億」),而那不會改變字數。
報告把同一時間區間的兩份文字並排,人一眼看得出誰把專有名詞聽對了。
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import podcast_digest as pd  # noqa: E402

#: 要對照的兩個模型。**現行的擺第一個** —— 報告要看得出「換掉的是什麼」。
MODELS = (os.getenv("BENCH_BASELINE", "medium"),
          os.getenv("BENCH_CANDIDATE", "large-v3-turbo"))

OUT_DIR = Path(__file__).resolve().parent.parent / "whisper_benchmark"


def _clip(src: Path, minutes: int) -> Path:
    """只留前 N 分鐘(控制成本)。ffmpeg 不在就原樣用整集。"""
    if minutes <= 0:
        return src
    dst = src.with_name(src.stem + f"_first{minutes}m.mp3")
    rc = os.system(f'ffmpeg -y -v quiet -i "{src}" -t {minutes * 60} '
                   f'-acodec copy "{dst}"')
    return dst if rc == 0 and dst.exists() else src


def main() -> int:
    show_key = os.getenv("BENCH_SHOW") or "gooaye"
    minutes = int(os.getenv("BENCH_MINUTES") or "15")
    cfg = next((c for c in pd.PODCASTS if c["key"] == show_key), None)
    if cfg is None:
        print(f"[bench] 找不到節目 {show_key!r};可用:"
              + ", ".join(c["key"] for c in pd.PODCASTS), file=sys.stderr)
        return 2
    feed_url = pd.resolve_feed_url(cfg["search"], cfg.get("country", "TW"))
    feed = pd.parse_feed_url(feed_url)
    entries = list(getattr(feed, "entries", []) or [])
    if not entries:
        print("[bench] feed 沒有任何集數", file=sys.stderr)
        return 3
    entry = entries[0]
    audio = ""
    for link in (getattr(entry, "links", []) or []):
        if str(getattr(link, "type", "")).startswith("audio"):
            audio = str(getattr(link, "href", ""))
            break
    if not audio:
        print("[bench] 最新一集沒有音檔連結", file=sys.stderr)
        return 4

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    src = OUT_DIR / "episode.mp3"
    if not pd.download_audio(audio, src):
        print("[bench] 音檔下載失敗", file=sys.stderr)
        return 5
    clip = _clip(src, minutes)

    rows, texts = [], {}
    for name in MODELS:
        t0 = time.time()
        try:
            text = pd.transcribe_audio(clip, lang=cfg.get("lang", "zh"),
                                       model_name=name, beam_size=1)
        except Exception as e:
            # **失敗要進報告**,不要讓它看起來像沒測過。
            rows.append((name, "失敗", "-", str(e)[:120]))
            continue
        texts[name] = text
        rows.append((name, f"{time.time() - t0:.0f} 秒", f"{len(text)} 字", ""))

    title = str(getattr(entry, "title", "") or "")[:80]
    md = [f"# whisper 模型對照 — {cfg['name']}", "",
          f"- 集數:{title}", f"- 音檔:前 {minutes} 分鐘"
          if minutes > 0 else "- 音檔:整集", "",
          "| 模型 | 耗時 | 字數 | 備註 |", "| --- | --- | --- | --- |"]
    md += [f"| `{n}` | {sec} | {chars} | {note} |" for n, sec, chars, note in rows]
    md += ["", "## 抽樣段落對照", "",
           "> **字數多不代表準。** 中文轉錄最常見的失效是公司名與數字聽錯,"
           "而那不會改變字數 —— 請直接比對下面兩段的專有名詞。", ""]
    for start in (0, 2000, 5000):
        md.append(f"### 第 {start}–{start + 400} 字")
        for name in MODELS:
            seg = (texts.get(name) or "")[start:start + 400]
            md.append(f"**`{name}`**:{seg or '(這個模型沒有轉到這裡)'}\n")
    md += ["", "---", "",
           "**這份報告不換任何設定。** 要改 `PODCAST_WHISPER_MODEL_HIGH`,",
           "請在看過上面的對照之後自己決定(計劃書 N6:換模型需使用者同意)。"]
    (OUT_DIR / "report.md").write_text("\n".join(md), encoding="utf-8")
    for name, text in texts.items():
        (OUT_DIR / f"transcript_{name}.txt").write_text(text, encoding="utf-8")
    print(f"[bench] 寫入 {OUT_DIR / 'report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
