"""Gate contact sheets for owner review: every stored item (base pools + theme pools), paginated, with the age class + evidence,
the gate class (MODEST / STRICT* override / STRICT(pic)), the appearance override + reason, the rating mix, the source
(WIKI / WIKI GALLERY / ANILIST / FAN ART with artist, score, resolution, on-model margin / hair / eye) and SWIMWEAR /
BARE FEET / BARE LEGS / SENSITIVE / THEME labels.
  python scripts/pop-gate-sheets.py [theme ids...]     (POP2_DIR / POP_EXTRAS_FILE / POP_SHEETS_OUT override the paths)"""
import glob
import json
import os
import re

import numpy as np
from PIL import Image, ImageDraw, ImageFont

A = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'core', 'quiz', 'assets')
P2 = os.environ.get('POP2_DIR') or os.path.join(A, 'pop2')
OUT = os.environ.get('POP_SHEETS_OUT') or r'C:/Users/Home/Desktop/review-shorts/quiz-new'
os.makedirs(OUT, exist_ok=True)
d = json.load(open(os.environ.get('POP_EXTRAS_FILE') or os.path.join(A, 'character-extras.json'), encoding='utf-8'))
OV = json.load(open(A + '/appearance-overrides.json', encoding='utf-8'))['characters']
def _font(names, size):
    for n in names:
        try:
            return ImageFont.truetype(n, size)
        except OSError:
            pass
    return ImageFont.load_default()


REG = ['C:/Windows/Fonts/arial.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf']
BOLD = ['C:/Windows/Fonts/arialbd.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf']
F = _font(REG, 14)
FB = _font(BOLD, 15)
for f in glob.glob(OUT + '/*-sheet-*.jpg'):
    os.remove(f)


def labels(it):
    g = it.get('gate') or {}
    sw = set(g.get('swimwear') or [])
    out = []
    if sw & {'swimsuit', 'bikini', 'swimwear', 'one-piece_swimsuit'} or (it.get('source') or {}).get('name', '') and False:
        out.append('SWIMWEAR')
    if sw & {'barefoot', 'feet'}:
        out.append('BARE FEET')
    if 'bare_legs' in sw:
        out.append('BARE LEGS')
    if 'sensitive' in str(g.get('why', '')):
        out.append('SENSITIVE')
    return out


def tile(it, kind, tw, th):
    if kind == 'character':   # themed reveal art (shown whole; the card crops its top part)
        im = Image.open(f"{P2}/{it['dir']}/{it['id']}.jpg").convert('RGB') if it.get('dir') else Image.new('RGB', (300, 400), (60, 60, 70))
    elif kind == 'outfit':
        im = Image.open(f"{P2}/{it.get('dir') or 'outfit'}/{it['id']}.jpg").convert('RGB')
        c = it['crop']
        ImageDraw.Draw(im).rectangle([c[0] * im.size[0], c[1] * im.size[1], c[2] * im.size[0] - 1, c[3] * im.size[1] - 1], outline=(255, 60, 60), width=3)
    elif kind == 'eyes':
        e = Image.open(f"{P2}/{it.get('dir') or 'eyes'}/{it['id']}.jpg").convert('RGB')
        p = Image.open(f"{P2}/{it.get('dir') or 'eyes'}/{it['id']}_p.jpg").convert('RGB')
        p = p.resize((max(1, round(p.size[0] * e.size[1] / p.size[1])), e.size[1]))
        im = Image.new('RGB', (e.size[0] + p.size[0] + 10, e.size[1]), (24, 24, 30))
        im.paste(e, (0, 0))
        im.paste(p, (e.size[0] + 10, 0))
    else:
        col = Image.open(f"{P2}/{it.get('dir') or 'sil'}/{it['id']}.jpg").convert('RGB')
        m = Image.open(f"{P2}/{it.get('dir') or 'sil'}/{it['id']}.png").convert('L')
        bk = Image.new('RGB', col.size, (255, 200, 90))
        bk.paste(Image.new('RGB', col.size, (20, 8, 40)), (0, 0), m)
        im = Image.fromarray(np.hstack([np.asarray(bk), np.asarray(col)]))
    im.thumbnail((tw, th))
    return im


def sheet(items, kind, name, cols, tw, th, rows=4):
    per = cols * rows
    for part in range(0, max(1, len(items)), per):
        chunk = items[part:part + per]
        nr = (len(chunk) + cols - 1) // cols
        S = Image.new('RGB', (cols * (tw + 12) + 12, nr * (th + 132) + 12), (24, 24, 30))
        dr = ImageDraw.Draw(S)

        def tx(pos, text, font, fill):   # cut to the tile width (long labels ran into the next tile)
            text = str(text)
            while text and dr.textlength(text, font=font) > tw:
                text = text[:-1]
            dr.text(pos, text, font=font, fill=fill)
        for k, it in enumerate(chunk):
            x, y = 12 + (k % cols) * (tw + 12), 12 + (k // cols) * (th + 132)
            im = tile(it, kind, tw, th)
            S.paste(im, (x + (tw - im.size[0]) // 2, y + (th - im.size[1]) // 2))
            r = (it.get('gate') or {}).get('rating', {})
            cls = it.get('age_class', '?')
            col = {'adult': (120, 220, 140), 'minor': (255, 120, 120), 'unknown': (255, 200, 90)}.get(cls, (200, 200, 200))
            lab = labels(it)
            tx((x, y + th + 4), f"{it['name']}"[:38], font=FB, fill=(255, 255, 255))
            tx((x, y + th + 22), f"{it['anime']}"[:42], font=F, fill=(180, 180, 200))
            gcl = (it.get('gate') or {}).get('class') or cls
            tagc = {'adult': '', 'adult-modest': ' MODEST', 'adult-strict': ' STRICT*', 'adult-young-picture': ' STRICT(pic)'}.get(gcl, '')
            if gcl not in ('adult', 'minor', 'unknown'):
                col = (255, 200, 90)
            tx((x, y + th + 40), f"AGE {cls.upper()}{tagc} g{r.get('general', 0):.2f} s{r.get('sensitive', 0):.2f} q{r.get('questionable', 0):.2f}",
                    font=F, fill=col)
            o = OV.get(str(it['id']))
            if o:   # appearance override: rule + who decided + reason
                tx((x, y + th + 57), f"OVERRIDE {o['rule']} ({'owner' if o['by'].startswith('owner') else 'AUTO, pending'}): {o['reason']}"[:46], font=F, fill=(255, 200, 90))
            else:
                tx((x, y + th + 57), str(it.get('age_evidence', ''))[:46], font=F, fill=(160, 160, 175))
            src = it.get('source') or {}
            k = src.get('kind')
            if k == 'ai':
                om_ = src.get('onmodel') or {}
                stext = f"AI: {src.get('model', '?')} seed {src.get('seed')}"
                stext2 = f"margin {om_.get('margin', 0):+.2f} hair {om_.get('hair', 0):.2f}" + (f" eye {om_['eye']:.2f}" if om_.get('eye') is not None else '')
                scol = (255, 110, 200)
            elif k == 'danbooru':
                om_ = src.get('onmodel') or {}
                stext = f"{src.get('srctype') or 'FAN ART'}: {src.get('artist', '?')}"
                stext2 = (f"score {src.get('score')}  {src.get('res')}  margin {om_.get('margin', 0):+.2f} hair {om_.get('hair', 0):.2f}"
                          + (f" eye {om_['eye']:.2f}" if om_.get('eye') is not None else ''))
                scol = (255, 190, 90)
            elif k == 'anilist':
                stext, scol, stext2 = 'ANILIST (official portrait)', (140, 190, 255), ''
            else:
                nm = src.get('name') or 'infobox'
                stext = ('WIKI (infobox)' if nm == 'infobox' or kind == 'eyes' else 'WIKI GALLERY: ' + nm.replace('File:', '')) + f" {src.get('wiki') or ''}"
                scol = (140, 190, 255)
            tx((x, y + th + 74), stext[:46], font=FB if k in ('danbooru', 'ai') else F, fill=scol)
            if k in ('danbooru', 'ai'):
                tx((x, y + th + 92), stext2[:60], font=_font(REG, 12), fill=scol)
            if it.get('theme_hit'):
                lab = lab + ['THEME: ' + it['theme_hit'][0]]
            if lab:
                tx((x, y + th + 110), '  '.join(lab)[:52], font=FB, fill=(255, 140, 220))
                dr.rectangle([x - 4, y - 4, x + tw + 3, y + th + 129], outline=(255, 140, 220), width=3)
        p = f"{OUT}/{name}-{part // per + 1}.jpg"
        S.save(p, quality=86)
    print(name, len(items), 'items')


sheet(d['outfit'], 'outfit', 'outfit-gate-sheet', 5, 290, 360)
sheet(d['silhouette'], 'sil', 'silhouette-gate-sheet', 5, 300, 300)
sheet(d['eyes'], 'eyes', 'eyes-gate-sheet', 4, 400, 210, rows=6)

# themed edition pools (character-extras.json -> themes.<id>.<format>)
import sys as _sys
_only = [a for a in _sys.argv[1:]]
for tid, pools in (d.get('themes') or {}).items():
    if _only and tid not in _only:
        continue
    for fmt, items in pools.items():
        if not items:
            continue
        kind = {'silhouette': 'sil'}.get(fmt, fmt)
        dims = {'character': (5, 260, 340), 'outfit': (5, 290, 360), 'sil': (5, 300, 300), 'eyes': (4, 400, 210)}[kind]
        sheet(items, kind, f'theme-{tid}-{fmt}-gate-sheet', *dims)


# ---- the automatic STRICT appearance flags (build-added, pending owner review), one numbered tile per character
def _picture(url):
    """Official art for the review tile (AniList portrait / wiki infobox); POP_IMG_CACHE reused when set; None on failure."""
    import hashlib
    import io
    import urllib.request
    if not url:
        return None
    cdir = os.environ.get('POP_IMG_CACHE')
    p = os.path.join(cdir, hashlib.sha1(url.encode('utf-8')).hexdigest()[:24] + '.img') if cdir else None
    try:
        if p and os.path.exists(p) and os.path.getsize(p) > 200:
            return Image.open(p).convert('RGB')
        req = urllib.request.Request(url, headers={'User-Agent': 'AsianPopQuizBuilder/1.0 (gate sheets)'})
        with urllib.request.urlopen(req, timeout=40) as r:
            data = r.read()
        if p:
            os.makedirs(cdir, exist_ok=True)
            open(p, 'wb').write(data)
        return Image.open(io.BytesIO(data)).convert('RGB')
    except Exception:  # noqa: BLE001
        return None


def strict_flags_sheet(cols=4, rows=3, ph=330):
    chars = {c['id']: c for c in json.load(open(os.path.join(A, 'anime-characters.json'), encoding='utf-8'))['characters']}
    try:
        cache = json.load(open(os.path.expanduser('~/.cache/pop_quiz_models/extras-cache.json'), encoding='utf-8'))
    except (OSError, ValueError):
        cache = {}
    auto = [(int(k), o) for k, o in OV.items() if (str(o.get('rule')) == 'strict')]
    if not auto:
        return
    in_pool = {}
    for fmt in ('eyes', 'silhouette', 'outfit'):
        for it in d.get(fmt) or []:
            in_pool.setdefault(it['id'], []).append(fmt)
    TW = 2 * 230 + 10
    TH = ph + 150
    per = cols * rows
    pages = (len(auto) + per - 1) // per
    FH = _font(BOLD, 22)
    lines = []
    for n, (cid, o) in enumerate(auto, 1):
        c = chars.get(cid) or {}
        lines.append(f"#{n:02d}  {o.get('name')}  ({c.get('anime', '?')})  age_class {c.get('age_class', '?')}: {o.get('reason')}")
    for pg in range(pages):
        chunk = list(enumerate(auto, 1))[pg * per:(pg + 1) * per]
        nr = (len(chunk) + cols - 1) // cols
        S = Image.new('RGB', (cols * (TW + 16) + 16, 92 + nr * (TH + 16)), (24, 24, 30))
        dr = ImageDraw.Draw(S)
        dr.text((16, 12), f'CHILD-BODIED CHARACTERS (strict list, owner approved) ({len(auto)} characters, page {pg + 1}/{pages})',
                font=FH, fill=(255, 200, 90))
        dr.text((16, 42), 'Flagged by the build: >= 1% of the character\'s Danbooru posts are tagged loli / shota. STRICT = general art only '
                          '(no swimwear / bare skin / suggestive, no outfit rounds, not in Beach Day); silhouette / eyes / character still fine.',
                font=F, fill=(200, 200, 215))
        dr.text((16, 62), 'Left: AniList portrait. Right: wiki infobox art. Reply with the numbers to REMOVE from the list (full adult rules); '
                          'nothing is added automatically. age_class itself is never changed.', font=F, fill=(200, 200, 215))
        for j, (n, (cid, o)) in enumerate(chunk):
            c = chars.get(cid) or {}
            x, y = 16 + (j % cols) * (TW + 16), 92 + (j // cols) * (TH + 16)
            dr.rectangle([x - 2, y - 2, x + TW + 1, y + TH + 1], outline=(70, 70, 90), width=2)
            w = cache.get(f'wikichar:{cid}') or {}
            for k, url in enumerate((c.get('image'), w.get('url'))):
                im = _picture(url)
                bx = x + k * 240
                if im is None:
                    dr.rectangle([bx, y, bx + 229, y + ph - 1], fill=(45, 45, 55))
                    dr.text((bx + 60, y + ph // 2 - 8), 'no picture', font=F, fill=(150, 150, 160))
                    continue
                im.thumbnail((230, ph))
                S.paste(im, (bx + (230 - im.size[0]) // 2, y + (ph - im.size[1]) // 2))
            ty = y + ph + 6
            dr.text((x + 4, ty), f"#{n:02d}  {o.get('name')}"[:40], font=FB, fill=(255, 255, 255))
            dr.text((x + 4, ty + 22), str(c.get('anime', '?'))[:52], font=F, fill=(180, 180, 200))
            dr.text((x + 4, ty + 42), f"AGE CLASS {str(c.get('age_class', '?')).upper()}: {c.get('age_evidence', '')}"[:60], font=F, fill=(120, 220, 140))
            dr.text((x + 4, ty + 62), f"STRICT: {o.get('reason', '')}"[:62], font=F, fill=(255, 200, 90))
            pools = ', '.join(in_pool.get(cid, [])) or 'none yet'
            dr.text((x + 4, ty + 82), f"in pools now: {pools}"[:60], font=F, fill=(160, 160, 175))
            dr.text((x + 4, ty + 104), 'decision:  [ ] keep strict   [ ] full adult rules', font=FB, fill=(140, 190, 255))
        S.save(f'{OUT}/strict-flags-sheet-{pg + 1}.jpg', quality=88)
    print('strict flags', len(auto), 'characters,', pages, 'pages')
    print(*lines, sep='\n')


strict_flags_sheet()
