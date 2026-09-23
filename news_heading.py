"""Source-title company labels; never infer an issuer from affected assets."""
import re

_NON_ISSUER_PHRASES = re.compile(r"東南亞|統一投信|統一發票|台灣大學")


def companies(item: dict, packet: dict) -> str:
    import company_profiles
    from news_events import mentions_entity
    from official_announcements import issuer

    names = {str(code): str(value[0] or code)
             for code, value in company_profiles.PROFILES.items()}
    for row in packet.get('tw_universe') or []:
        if isinstance(row, dict) and row.get('code') and row.get('name'):
            names[str(row['code'])] = str(row['name'])
    title = str(item.get('title') or '')
    title_without_homonyms = _NON_ISSUER_PHRASES.sub(' ', title)
    found = []
    official = issuer(item)
    for code, name in names.items():
        # Remove longer declared names before checking a short CJK alias:
        # 台塑化 is not 台塑; retain a separate explicit 台塑 occurrence.
        probe = title_without_homonyms
        for longer in names.values():
            if name != longer and name in longer:
                probe = re.sub(re.escape(longer), ' ', probe)
        if code == official or mentions_entity(probe, code, {code: (name,)}):
            found.append((title_without_homonyms.find(name) if name in title_without_homonyms else len(title), code, name))
    grouped: dict[str, tuple[int, list[str]]] = {}
    for position, code, name in sorted(found):
        grouped.setdefault(name, (position, []))[1].append(code)
    def label(name: str, codes: list[str]) -> str:
        if len(codes) == 1:
            return f'{name}（{codes[0]}）'
        explicit = [code for code in codes if code == official or re.search(
            rf'(?<![A-Za-z0-9]){re.escape(code)}(?![A-Za-z0-9])', title, re.I)]
        return f'{name}（{"／".join(explicit)}）' if explicit else name
    return '、'.join(label(name, codes) for name, (_, codes) in grouped.items())
