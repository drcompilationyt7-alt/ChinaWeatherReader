"""
Wikimedia Commons photos for the Mainland Unpacked Shorts.

Only licences that allow commercial reuse with credit are accepted (CC0, public domain, CC BY, CC BY-SA).
Each download returns the credit line that the video description must carry.

  python core/china/commons.py search "Huajiang Grand Canyon Bridge" [n]   # candidates + licences + a contact sheet
"""
import hashlib
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

API = 'https://commons.wikimedia.org/w/api.php'
UA = 'MainlandUnpackedBot/1.0 (https://github.com/drcompilationyt7-alt/ChinaWeatherReader)'
CACHE = os.environ.get('CHINA_MEDIA_CACHE') or os.path.join(os.path.expanduser('~'), '.cache', 'china-media')
OK_LICENCE = re.compile(r'^(cc0|public domain|pd\b|pdm|cc by(-sa)? ?\d|cc-by(-sa)?-\d|attribution)', re.I)


def _get(params):
    q = urllib.parse.urlencode({**params, 'format': 'json', 'formatversion': 2})
    req = urllib.request.Request(f'{API}?{q}', headers={'User-Agent': UA})
    for i in range(4):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except Exception:
            time.sleep(2 * (i + 1))
    raise RuntimeError(f'Commons API failed: {params}')


def _strip(html):
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', html or '')).strip()


def _info(page):
    ii = (page.get('imageinfo') or [{}])[0]
    meta = ii.get('extmetadata') or {}
    lic = _strip(meta.get('LicenseShortName', {}).get('value', ''))
    return {
        'title': page['title'],
        'width': ii.get('width', 0), 'height': ii.get('height', 0),
        'mime': ii.get('mime', ''),
        'thumb': ii.get('thumburl') or ii.get('url'),
        'page': ii.get('descriptionurl', ''),
        'licence': lic,
        'licence_url': meta.get('LicenseUrl', {}).get('value', ''),
        'artist': _strip(meta.get('Artist', {}).get('value', '')) or 'Unknown author',
        'ok': bool(OK_LICENCE.search(lic)) and 'nc' not in lic.lower() and 'nd' not in lic.lower().split(),
    }


def search(query, n=12, width=1600):
    d = _get({'action': 'query', 'generator': 'search', 'gsrsearch': f'{query} filetype:bitmap', 'gsrnamespace': 6,
              'gsrlimit': n, 'prop': 'imageinfo', 'iiprop': 'url|size|mime|extmetadata', 'iiurlwidth': width})
    pages = sorted((d.get('query') or {}).get('pages', []), key=lambda p: p.get('index', 0))
    return [_info(p) for p in pages]


def fetch(title, width=1600):
    """Download a Commons file (by 'File:...' title) to the cache. Returns (path, info with credit)."""
    d = _get({'action': 'query', 'titles': title, 'prop': 'imageinfo', 'iiprop': 'url|size|mime|extmetadata', 'iiurlwidth': width})
    info = _info(d['query']['pages'][0])
    if not info['ok']:
        raise ValueError(f'{title}: licence "{info["licence"]}" is not allowed')
    os.makedirs(CACHE, exist_ok=True)
    ext = os.path.splitext(info['thumb'].split('?')[0])[1] or '.jpg'
    path = os.path.join(CACHE, hashlib.sha1(f'{title}|{width}'.encode()).hexdigest()[:16] + ext)
    if not os.path.exists(path):
        req = urllib.request.Request(info['thumb'], headers={'User-Agent': UA})
        with urllib.request.urlopen(req, timeout=60) as r, open(path, 'wb') as f:
            f.write(r.read())
    info['credit'] = f'"{title[5:]}" by {info["artist"]}, {info["licence"]}, via Wikimedia Commons ({info["page"]})'
    return path, info


if __name__ == '__main__' and len(sys.argv) > 2 and sys.argv[1] == 'search':
    from PIL import Image, ImageDraw
    q, n = sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 12
    res = [r for r in search(q, n, width=400) if r['ok'] and r['width'] >= 1000]
    sheet = Image.new('RGB', (4 * 400, ((len(res) + 3) // 4) * 300 or 300), (30, 30, 30))
    for i, r in enumerate(res):
        print(f'[{i}] {r["title"]}  {r["width"]}x{r["height"]}  {r["licence"]}  by {r["artist"][:40]}')
        try:
            req = urllib.request.Request(r['thumb'], headers={'User-Agent': UA})
            from io import BytesIO
            with urllib.request.urlopen(req, timeout=30) as resp:
                im = Image.open(BytesIO(resp.read())).convert('RGB')
            im.thumbnail((396, 270))
            sheet.paste(im, ((i % 4) * 400 + 2, (i // 4) * 300 + 2))
            ImageDraw.Draw(sheet).text(((i % 4) * 400 + 6, (i // 4) * 300 + 276), f'[{i}] {r["title"][5:50]}', fill=(255, 255, 255))
        except Exception as e:
            print('   thumb failed', e)
    out = os.path.join(os.environ.get('SHEET_DIR', '.'), 'sheet-' + re.sub(r'\W+', '_', q)[:40] + '.jpg')
    sheet.save(out, quality=85)
    print('sheet:', out)
