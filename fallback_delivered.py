"""Recover attributed opinions from the actual delivered news sections, not facts."""
import datetime as dt
import re

from bs4 import BeautifulSoup

import fallback_recap as fr


def extract(html, news, date, origin):
    """Renderer-added source links count only when they match today's news URLs."""
    if origin not in fr.ALLOWED_ORIGINS:
        return {}
    dt.date.fromisoformat(date)
    urls = {str(n.get('link') or n.get('url') or '')
            for n in news if isinstance(n, dict)}
    urls = {url for url in urls if url.startswith(('https://', 'http://'))}
    soup = BeautifulSoup(html, 'html.parser')
    for node in soup(['script', 'style', 'head']):
        node.decompose()
    active, items = False, []
    for node in soup.find_all(['h1', 'h2', 'h3', 'p', 'li']):
        if node.name.startswith('h'):
            active = node.get_text().strip() in {'八、科技板塊脈動', '九、其他類股資訊'}
            continue
        if not active or node.find_parent(['p', 'li']):
            continue
        statement = node.get_text().strip()
        links = list(dict.fromkeys(a.get('href') for a in node.find_all('a')))
        if (not statement or len(statement) > fr.MAX_CHARS or not links
                or len(links) > 12 or any(link not in urls for link in links)
                or re.search(r'持倉|持有股數|我的部位|你的部位|組合曝險|PORTFOLIO', statement, re.I)):
            continue
        item = {'statement': statement, 'source_urls': links}
        if item not in items:
            items.append(item)
    return {'date': date, 'analysis_origin': origin,
            'status': 'unvalidated_model_opinion', 'items': items[:fr.MAX_ITEMS]}
