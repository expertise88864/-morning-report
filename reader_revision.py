"""Conservative reader projection and explicit incomplete-watch protection."""
import re

from reader_evidence_writing import WRITING as WRITING


def watch_status(row: dict) -> str:
    from watch_assessment import status
    return status(row)


def conclusion(sections: list[str], obj: dict) -> str:
    """Move supporting paragraphs intact, never truncate risks or alter JSON."""
    from reader_editorial import OUTLOOK_HEADING
    sections = list(sections)
    support = []
    pf_summary = str((obj.get('portfolio_implications') or {}).get('summary') or '').strip()
    for i, section in enumerate(sections):
        if not section.startswith('## 我的明確立場\n'):
            continue
        head, _, body = section.partition('\n')
        body = re.sub(r'系統計分[^。\n]*。', '', body)
        lines = [line.strip() for line in body.splitlines() if line.strip()]
        contract = [line for line in lines if line.startswith(('立場：', '淨分 '))]
        prose = []
        for line in lines:
            if line in contract:
                continue
            if line.startswith(('可考慮的做法:', '風險:', '失效條件:')) or (pf_summary and line == pf_summary):
                support.append(line)
            else:
                prose.append(line)
        sections[i] = head + '\n' + '\n'.join(contract) + '\n\n' + '\n\n'.join(prose) + '\n\n'
    if support:
        heading = '## ' + OUTLOOK_HEADING + '\n'
        index = next((i for i, section in enumerate(sections) if section.startswith(heading)), None)
        extra = '\n\n'.join(support) + '\n\n'
        if index is None:
            sections.insert(next((i for i, s in enumerate(sections)
                                  if s.startswith('## 我的明確立場\n')), len(sections)), heading + extra)
        else:
            sections[index] = sections[index].rstrip() + '\n\n' + extra
    return ''.join(sections)


def watch_reviews(obj: dict) -> dict:
    reviewed = {}
    for row in obj.get('watch_review') or []:
        if isinstance(row, dict) and str(row.get('watch_id') or ''):
            projected = watch_status(row)
            reviewed[str(row['watch_id'])] = projected
            if projected != row.get('status'):
                reason = ('watch_partial_retained' if projected == 'partially_triggered'
                          else 'watch_evidence_incomplete')
                print('::warning::' + reason, flush=True)
    return reviewed
