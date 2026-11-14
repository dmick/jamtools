"""HTML rendering for the /nextshow page (markup and CSS ported from the old nextshow.py)."""
import html
import re

import set_utils

CSS = """
body { font-family: sans-serif; margin: 1.5em; }
h1 { margin-bottom: .2em; }
h2 { margin-top: 0; font-weight: normal; color: #555; }
table { border-collapse: collapse; }
th, td { border: 1px solid #ccc; padding: 4px 10px; text-align: left; }
th { font-weight: bold; background: #fff; border-bottom: 2px solid #888; }
tbody tr:nth-child(odd)  td { background: #ffffff; }
tbody tr:nth-child(even) td { background: #f0f0f0; }
tbody td.empty { background: #ffd6d6 !important; }
"""

# (heading, row key) for each column; the row keys are what set_utils.get_rows() produces
_NAMES = ['#'] + [f if isinstance(f, str) else f[1] for f in set_utils.PAGE_FIELDS]
_KEYS = set_utils.cleanfields(['SONGNUM'] + [f if isinstance(f, str) else f[1] for f in set_utils.PAGE_FIELDS])
COLUMNS = list(zip(_NAMES, _KEYS))


def cell(text: str) -> str:
    """Escaped cell text with bare URLs turned into links."""
    t = html.escape(text.strip())
    return re.sub(r'(https?://[^\s<]+)', r'<a href="\1">\1</a>', t)


def render(banner: str, date: str, rows: list[dict[str, str]]) -> str:
    title = banner or 'Setlist'
    out = ['<!DOCTYPE html>', '<html><head><meta charset="utf-8">',
           '<meta name="viewport" content="width=device-width, initial-scale=1">',
           f'<title>{html.escape(title)}</title>', f'<style>{CSS}</style></head><body>',
           f'<h1>{html.escape(title)}</h1>']
    if date:
        out.append(f'<h2>{html.escape(date)}</h2>')
    out.append('<table>\n<thead><tr>' + ''.join(f'<th>{html.escape(n)}</th>' for n, _ in COLUMNS)
               + '</tr></thead>\n<tbody>')
    for row in rows:
        out.append('<tr>' + ''.join(
            f'<td>{cell(v)}</td>' if (v := row.get(key, '').strip()) else '<td class="empty"></td>'
            for _, key in COLUMNS) + '</tr>')
    out.append('</tbody></table></body></html>')
    return '\n'.join(out)
