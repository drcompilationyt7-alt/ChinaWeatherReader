"""
Colour emoji images for the quiz renderer: Google's Noto Emoji (Apache-2.0)
512 px PNGs, cached in core/quiz/assets/emoji/. Pillow cannot draw colour
emoji from a font reliably on every OS, so the pictures are pasted instead.
"""
import os
import urllib.request

from PIL import Image

ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'assets')
CACHE = os.path.join(ASSETS, 'emoji')
URL = 'https://fonts.gstatic.com/s/e/notoemoji/latest/{}/512.png'
UA = 'Mozilla/5.0 (quiz renderer)'

_mem = {}


def _variants(e):
    # Noto pads code points below U+10000 to 4 hex digits (keycaps: 0031_20e3)
    cps = [f'{ord(c):04x}' for c in e]
    no_fe0f = [c for c in cps if c != 'fe0f']
    out = []
    for v in (no_fe0f, cps):
        name = '_'.join(v)
        if name and name not in out:
            out.append(name)
    return out


def path_for(e):
    """Local PNG path for an emoji, downloading it on first use (None if unavailable)."""
    os.makedirs(CACHE, exist_ok=True)
    for name in _variants(e):
        p = os.path.join(CACHE, name + '.png')
        if os.path.exists(p) and os.path.getsize(p) > 200:
            return p
    for name in _variants(e):
        p = os.path.join(CACHE, name + '.png')
        try:
            req = urllib.request.Request(URL.format(name), headers={'User-Agent': UA})
            with urllib.request.urlopen(req, timeout=20) as r:
                data = r.read()
            if len(data) > 200 and data[:4] == b'\x89PNG':
                with open(p, 'wb') as f:
                    f.write(data)
                return p
        except Exception:
            continue
    return None


def image(e, size):
    """RGBA image of the emoji at size x size, or None."""
    key = (e, size)
    if key in _mem:
        return _mem[key]
    p = path_for(e)
    img = None
    if p:
        try:
            img = Image.open(p).convert('RGBA').resize((size, size), Image.LANCZOS)
        except Exception:
            img = None
    _mem[key] = img
    return img
