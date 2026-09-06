"""Read-only point-in-time retrieval audit. Never calls models, SMTP or writes state."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import news_memory  # noqa: E402
import news_normalize  # noqa: E402
import news_research_context  # noqa: E402
import event_score  # noqa: E402


def evaluate(archive_dir: Path, news: list, as_of: str, analysis=None) -> dict:
    """No later observation may improve an earlier day's retrieval result."""
    archive = news_memory.load(archive_dir, as_of)
    now = news_memory.timestamp(as_of)
    if now is None:
        raise ValueError("invalid as_of")
    available = [dict(n, published=pub.isoformat()) for n in news
                 if (pub := news_memory.publication_time(n)) is not None
                 and not n.get("date_missing") and pub <= now]
    normalized, _, info = news_normalize.normalize_news(available, str)
    packet = {"news": normalized, "news_clusters": info, "as_of": as_of,
              "top_events": event_score.rank(info["clusters"], normalized)}
    news_research_context.build(packet, archive)
    contexts = packet["research"]["contexts"]
    return {"as_of": as_of, "news_supplied": len(news), "news_available": len(available),
            "historical_observations_available": len(archive),
            "articles_with_context": sum(bool(c["evidence_ids"]) for c in contexts.values()),
            "selected_historical_source_ids": [r["evidence_id"] for r in packet["historical_sources"]],
            "analysis_metrics": news_research_context.metrics(analysis or {}, packet),
            "limitations": ["Structural audit only; semantic truth requires source-by-source evaluation.",
                            "Missing historical archives cannot be reconstructed from generated summaries.",
                            "Supplied current-news files must themselves be genuine as-of snapshots."]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-dir", type=Path, required=True)
    parser.add_argument("--news-file", type=Path, required=True)
    parser.add_argument("--analysis-file", type=Path)
    parser.add_argument("--as-of", required=True)
    args = parser.parse_args()
    news = json.loads(args.news_file.read_text(encoding="utf-8"))
    if not isinstance(news, list) or not all(isinstance(n, dict) for n in news):
        parser.error("news-file must contain a list of source article objects")
    analysis = json.loads(args.analysis_file.read_text(encoding="utf-8")) if args.analysis_file else None
    print(json.dumps(evaluate(args.archive_dir, news, args.as_of, analysis), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
