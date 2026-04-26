import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.ingestion.scraper import fetch_rendered_html
from bs4 import BeautifulSoup

html = fetch_rendered_html('https://groww.in/mutual-funds/sbi-gold-fund-direct-growth')
soup = BeautifulSoup(html, 'html.parser')
for tag in soup.select('nav,header,footer,script,style,noscript'):
    tag.decompose()

# Find all divs/sections with substantial prose text
print('=== LONG TEXT BLOCKS (>150 chars) ===')
seen = set()
for el in soup.find_all(['p', 'div', 'section', 'article']):
    txt = el.get_text(' ', strip=True)
    if len(txt) > 150 and len(txt) < 1000 and txt not in seen:
        # skip if it contains too many pipe separators (nav menus)
        if txt.count('|') < 10:
            seen.add(txt)
            cls = str(el.get('class', ''))[:80]
            print(f'TAG:{el.name} CLASS:{cls}')
            print(f'TEXT: {txt[:200]}')
            print('---')
