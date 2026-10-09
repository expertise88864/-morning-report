"""Visible RSS summary text; source URLs remain in dedicated evidence fields."""
import re
from html.parser import HTMLParser

_BLOCKS = {'br', 'p', 'div', 'li', 'blockquote', 'table', 'tr',
           'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'ul', 'ol', 'article', 'section',
           'address', 'aside', 'dd', 'dl', 'dt', 'fieldset', 'figcaption', 'figure',
           'footer', 'form', 'header', 'hr', 'main', 'nav', 'pre', 'details', 'summary'}

class _Visible(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.hidden += 1
        if not self.hidden and tag in _BLOCKS:
            self.parts.append('\n')
        if not self.hidden and tag in ('td', 'th'):
            self.parts.append(' ')
        if not self.hidden and tag == 'img':
            self.parts.append(' ' + str(dict(attrs).get('alt') or '') + ' ')

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.hidden = max(0, self.hidden - 1)
        if not self.hidden and tag in _BLOCKS:
            self.parts.append('\n')
        elif not self.hidden and tag in ('a', 'td', 'th'):
            self.parts.append(' ')

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def visible_summary(value: str) -> str:
    """Remove markup before truncation/sanitization, never fetch linked content."""
    text = str(value or '')
    # Old ingesters may have cut inside href. Keep visible prefix; never invent lost anchor text.
    text = re.sub(r'<a\b[^>]*\bhref\s*=\s*["\x27][^>]*$', '', text, flags=re.I)
    if not re.search(r'</?[A-Za-z][\w:-]*(?:\s[^<>]*|\s*/)?\s*>', text):
        return text
    parser = _Visible()
    parser.feed(text)
    parser.close()
    visible = re.sub(r'[^\S\n]+', ' ', ''.join(parser.parts))
    visible = re.sub(r' *\n *', '\n', visible)
    return re.sub(r'\n{3,}', '\n\n', visible).strip()


def entry_summary(entry: dict, limit: int = 800) -> str:
    return visible_summary(entry.get('summary') or '')[:limit]


def history_projection(row: dict) -> dict:
    """Prompt-only copy; evidence ID points to the immutable raw observation."""
    text = visible_summary(row.get('excerpt') or '')
    return dict(row, excerpt=text, content_level=row['content_level'] if text else 'title_only')
