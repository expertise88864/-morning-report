"""Metadata coverage, not fact checking. Counts only: never log private values/IDs."""
from datetime import datetime
import math


def _clock(value):
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (ValueError, TypeError):
        return None


def summarize(packet):
    from evidence_registry import registry
    counts = dict.fromkeys(('numeric_entries', 'nonfinite', 'missing_source',
                           'missing_unit', 'missing_observation_time',
                           'invalid_source_time', 'future_source_time',
                           'incomparable_source_time', 'unusable'), 0)
    now = _clock((packet or {}).get('as_of'))
    for meta in registry(packet).values():
        value = meta.get('value')
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        counts['numeric_entries'] += 1
        counts['nonfinite'] += int(not math.isfinite(value))
        counts['missing_source'] += int(not str(meta.get('source') or '').strip())
        counts['missing_unit'] += int(not str(meta.get('unit') or '').strip())
        counts['unusable'] += int(meta.get('usable_for_inference') is False)
        source_time = str(meta.get('as_of') or '').strip()
        precision = meta.get('as_of_precision')
        observed = str(meta.get('observed_session') or '').strip()
        counts['missing_observation_time'] += int(not observed and not (
            precision == 'source' and source_time))
        if precision != 'source' or not source_time:
            continue
        stamp = _clock(source_time)
        if stamp is None:
            counts['invalid_source_time'] += 1
        elif now is None or (stamp.tzinfo is None) != (now.tzinfo is None):
            counts['incomparable_source_time'] += 1
        else:
            counts['future_source_time'] += int(stamp > now)
    return {'version': 1, 'mode': 'diagnostic_only_not_claim_verification', **counts}
