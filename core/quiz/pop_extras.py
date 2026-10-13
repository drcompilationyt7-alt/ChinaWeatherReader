"""
Builds core/quiz/assets/character-extras.json (+ small image files in core/quiz/assets/pop2/) for the new
pop quiz formats: silhouette, eyes, outfit. Run through `python scripts/build-pop-media.py --newformats`.
Heavy models run here, once (pop_vision.py); the daily render only reads the stored files.

Sources: AniList portraits (tight face crops: eyes) and Fandom wiki infobox art through the MediaWiki API
(full-body, often transparent: silhouettes and outfits). Fandom text is CC-BY-SA; the artwork belongs to the
rights holders and is used here only to identify the character (recorded per item as `source`).

Age class (adult / minor / unknown) per character from AniList age, the wiki infobox "Age" and clear adult-role
evidence in the AniList description; stored with the evidence and written back to anime-characters.json.
"""
import difflib
import io
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

import numpy as np
from PIL import Image

import pop_fanart as pf
import pop_onmodel as om
import pop_vision as pv

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, 'assets')
# a monthly refresh builds into staging (pop2_next/ + character-extras.next.json) and core/quiz/pool_refresh.py promotes it
POP2 = os.environ.get('POP2_DIR') or os.path.join(ASSETS, 'pop2')
EXTRAS = os.environ.get('POP_EXTRAS_FILE') or os.path.join(ASSETS, 'character-extras.json')
UA = 'AsianPopQuizBuilder/1.0 (https://github.com/drcompilationyt7-alt/ChinaWeatherReader)'
CACHE_FILE = os.path.join(pv.MODELS, 'extras-cache.json')
EYE_W, EYE_H = 760, 450       # v1 eye band (both eyes), superseded by the single-eye crop below
EYE1_W, EYE1_H = 900, 620     # v2 eyes format: ONE eye, very tight (the eye ~60% of the width), like the eye-quiz references
EYE1_FILL = 0.56              # eye width / card width
EYE1_FILL_H = 0.55            # eye height / card height: tall (round) eyes size the crop by height, never cut at the top / bottom
EYE1_MIN_PX = 30              # source eye width (px) below which even double SR looks soft (23 px eyes came out blurry)
SIL_H, FULL_H, PORT_H = 520, 800, 420


def log(*a):
    print(*a, file=sys.stderr, flush=True)


# ------------------------------------------------------------------ http
# CI split (scripts/pop-pool-kaggle.py): the GitHub runner prefetches every picture into POP_IMG_CACHE (network only), the Kaggle
# GPU session runs the models with POP_OFFLINE=1 (no network at all: a missing picture is a retryable error, never a cached drop)
IMG_CACHE = os.environ.get('POP_IMG_CACHE')
OFFLINE = os.environ.get('POP_OFFLINE') == '1'


def http(url, tries=3, binary=False, timeout=45):
    if OFFLINE:
        raise RuntimeError(f'offline: {url[:60]}')
    for k in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': UA}), timeout=timeout) as r:
                d = r.read()
            return d if binary else json.loads(d)
        except Exception as e:  # noqa: BLE001
            if k == tries - 1:
                raise RuntimeError(f'{url[:80]}: {str(e)[:60]}')
            time.sleep(2 * (k + 1))


def _img_path(url):
    import hashlib
    return os.path.join(IMG_CACHE, hashlib.sha1(url.encode('utf-8')).hexdigest()[:24] + '.img')


def load_img(url):
    if IMG_CACHE:
        p = _img_path(url)
        if os.path.exists(p) and os.path.getsize(p) > 200:
            return Image.open(p)
        data = http(url, binary=True)   # raises when offline
        os.makedirs(IMG_CACHE, exist_ok=True)
        with open(p + '.tmp', 'wb') as f:
            f.write(data)
        os.replace(p + '.tmp', p)
        return Image.open(p)
    return Image.open(io.BytesIO(http(url, binary=True)))


class Cache:
    def __init__(self):
        try:
            self.d = json.load(open(CACHE_FILE, encoding='utf-8'))
        except (OSError, ValueError):
            self.d = {}
        self.n = 0

    def get(self, k):
        return self.d.get(k)

    def put(self, k, v):
        self.d[k] = v
        self.n += 1
        if self.n % 10 == 0:
            self.save()

    def save(self):
        os.makedirs(pv.MODELS, exist_ok=True)
        json.dump(self.d, open(CACHE_FILE + '.tmp', 'w', encoding='utf-8'))
        os.replace(CACHE_FILE + '.tmp', CACHE_FILE)


# ------------------------------------------------------------------ names / wikis
def norm(s):
    return re.sub(r'[^a-z0-9 ]+', ' ', str(s or '').lower().replace('&', ' and ')).split()


def wiki_slugs(title, romaji=''):
    out = []
    for t in (title, romaji, str(title).split(':')[0], str(romaji).split(':')[0], re.sub(r'\(.*?\)', '', str(title))):
        w = norm(t)
        if w and w[0] == 'the':
            w = w[1:]
        for s in (''.join(w), '-'.join(w), ''.join(w[:2]), ''.join(w[:3])):
            if len(s) >= 4 and s not in out:
                out.append(s)
    return out


WIKI_OVERRIDE = {'hunter x hunter': 'hunterxhunter', 'naruto': 'naruto', 'bleach': 'bleach', 'dragon ball': 'dragonball',
                 'my hero academia': 'bokunoheroacademia', 'demon slayer': 'kimetsu-no-yaiba', 'fairy tail': 'fairytail',
                 'sword art online': 'swordartonline', 'black clover': 'blackclover', 'chainsaw man': 'chainsawman',
                 'spy x family': 'spy-x-family', 'fullmetal alchemist': 'fma', 'one punch man': 'onepunchman',
                 'tokyo revengers': 'tokyorevengers', 'haikyu': 'haikyuu', 'jojo': 'jojo', 'evangelion': 'evangelion',
                 'neon genesis evangelion': 'evangelion', 'cowboy bebop': 'cowboybebop', 'steins gate': 'steins-gate',
                 'fate': 'typemoon', 'the seven deadly sins': 'nanatsu-no-taizai', 'seven deadly sins': 'nanatsu-no-taizai',
                 'konosuba': 'konosuba', 'overlord': 'overlordmaruyama', 'frieren': 'frieren', 'oshi no ko': 'oshinoko',
                 'bocchi the rock': 'bocchi-the-rock', 'solo leveling': 'solo-leveling', 'dandadan': 'dandadan',
                 'blue lock': 'blue-lock', 'vinland saga': 'vinlandsaga', 'violet evergarden': 'violet-evergarden',
                 'kaguya sama': 'kaguyasama-wa-kokurasetai', 'toradora': 'tora-dora', 'your name': 'kiminonawa'}


def wiki_for(show, romaji, cache):
    """Fandom subdomain of a show's wiki (verified by its site name), or None."""
    k = 'wiki:' + show
    c = cache.get(k)
    if c is not None:
        return c or None
    found = ''
    low = ' '.join(norm(show))
    for pre, slug in WIKI_OVERRIDE.items():
        if low.startswith(pre):
            found = slug
            break
    for slug in ([] if found else wiki_slugs(show, romaji)[:8]):
        try:
            d = http(f'https://{slug}.fandom.com/api.php?action=query&meta=siteinfo&siprop=general&format=json', tries=1, timeout=15)
        except Exception:  # noqa: BLE001
            continue
        name = ''.join(norm(re.sub(r'wiki', '', d['query']['general'].get('sitename', ''), flags=re.I)))
        ref = [''.join(norm(x)) for x in (show, romaji, str(show).split(':')[0], str(romaji).split(':')[0])]
        if any(r and name and (difflib.SequenceMatcher(None, name, r).ratio() >= 0.62 or name in r or r.startswith(name)) for r in ref):
            found = slug
            break
        time.sleep(0.15)
    cache.put(k, found)
    return found or None


def wiki_character(wiki, name, aniname_tokens):
    """{'title','url','w','h','age','page'} for a character page on a wiki, via the MediaWiki API."""
    q = urllib.parse.urlencode({'action': 'query', 'list': 'search', 'srsearch': name, 'srlimit': 4, 'srnamespace': 0, 'format': 'json'})
    hits = http(f'https://{wiki}.fandom.com/api.php?{q}', tries=2, timeout=25)['query']['search']
    want = set(aniname_tokens)
    best = None
    for h in hits:
        tt = norm(h['title'])
        if not tt:
            continue
        if '/' in h['title']:
            continue
        ts = set(tt)
        if len(want) == 1 and want <= ts and len(ts) <= 3:
            best = h['title']
            break
        if ts == want or (len(want) >= 2 and want <= ts) or (len(ts) >= 2 and ts <= want and len(ts) >= len(want) - 1) or (len(want) == 1 and tt[0] == tt[0] and want == ts):
            best = h['title']
            break
    if not best:
        return None
    q = urllib.parse.urlencode({'action': 'query', 'titles': best, 'redirects': 1, 'prop': 'pageimages|revisions', 'piprop': 'original|name',
                                'rvprop': 'content', 'rvslots': 'main', 'rvsection': 0, 'format': 'json'})
    p = next(iter(http(f'https://{wiki}.fandom.com/api.php?{q}', tries=2, timeout=25)['query']['pages'].values()))
    text = (p.get('revisions') or [{}])[0].get('slots', {}).get('main', {}).get('*', '')
    m = re.search(r'\|\s*(?:age|age1|age_start|age_part_?1|ages?)\s*=[ \t]*([^\n]*)', text, re.I)
    age = re.sub(r'<[^>]+>|\[\[|\]\]|\{\{[^}]*\}\}', ' ', m.group(1)).strip() if m else None
    o = p.get('original') or {}
    return {'title': p.get('title', best), 'url': o.get('source'), 'w': o.get('width'), 'h': o.get('height'), 'age': age,
            'page': f'https://{wiki}.fandom.com/wiki/' + urllib.parse.quote(p.get('title', best).replace(' ', '_')), 'wiki': wiki}


# ------------------------------------------------------------------ age
ADULT_ROLE = re.compile(r'\b(teacher|professor|principal|instructor|sensei of|mother of|father of|wife of|husband of|married|widow|office (?:worker|lady)|'
                        r'salary ?man|soldier|officer|lieutenant|captain of the|commander|colonel|admiral|marshal|sergeant|doctor|physician|surgeon|nurse|'
                        r'detective|police|inspector|bartender|in (?:her|his) (?:twenties|thirties|forties|fifties|late 20s|early 30s|30s)|adult woman|'
                        r'grown woman|mother|housewife)\b', re.I)
MINOR_WORD = re.compile(r'\b(high school|middle school|elementary|junior high|first[- ]year|second[- ]year|third[- ]year|child|teen(?:age)?(?:r|d)?|kid|'
                        r'schoolgirl|school ?boy|freshman|sophomore|years? old girl|loli|young girl|little girl|student council)\b', re.I)
TIMESKIP = re.compile(r'time ?skip|years later|adult version|as an adult|older version|after the (?:war|skip)|grown up|future version', re.I)


def nums(s):
    return [int(x) for x in re.findall(r'\d+', str(s or '')) if not re.search(r'\d+\s*(day|week|month)', str(s), re.I)]


def age_from_field(s):
    """AniList / wiki age string -> ('adult'|'minor'|None, note)."""
    s = re.sub(r"\([^)]*\)|\{\{.*?\}\}|\[\[.*?\]\]|'{2,}|<[^>]+>", ' ', str(s or '')).strip()
    if re.search(r'teen|child|young|infant|baby', s, re.I):
        return 'minor', s
    if not s or re.search(r'unknown|n/?a|\?\?', s, re.I):
        return None, ''
    n = nums(s)
    if not n:
        return None, ''
    if min(n) < 18:
        return 'minor', s
    if re.search(r'-\s*$', s) and min(n) <= 18:   # '18-' style open ranges stay unclear
        return None, ''
    return 'adult', s


def age_class(c, desc, wiki_age):
    """{'class','evidence','source'}: adult only when the evidence clearly says 18+ and nothing conflicts."""
    text = re.sub(r'~!.*?!~', ' ', str(desc or ''), flags=re.S)
    text = re.sub(r'<[^>]+>|[_*]', ' ', text)
    ev = []
    a_cls, a_note = age_from_field(c.get('age'))
    w_cls, w_note = age_from_field(wiki_age)
    d_ages = [int(x) for x in re.findall(r'\bage[d]?\s*[:\-]?\s*(\d{1,3})|\b(\d{1,3})[- ]years?[- ]old', text, re.I) for x in x if x] if text else []
    d_cls = None if not d_ages else ('minor' if min(d_ages) < 18 else 'adult')
    minor_word = MINOR_WORD.search(text)
    role = ADULT_ROLE.search(text)
    skip = bool(TIMESKIP.search(text))
    if 'minor' in (a_cls, w_cls, d_cls):
        which = 'AniList age ' + a_note if a_cls == 'minor' else 'wiki age ' + w_note if w_cls == 'minor' else f'description age {min(d_ages)}'
        return {'class': 'minor', 'evidence': which, 'source': 'anilist' if a_cls == 'minor' else 'wiki' if w_cls == 'minor' else 'description'}
    if a_cls == 'adult' or w_cls == 'adult' or d_cls == 'adult':
        if a_cls == 'adult':
            ev, src = f'AniList age {a_note}', 'anilist'
        elif w_cls == 'adult':
            ev, src = f'wiki age {w_note}', 'wiki'
        else:
            ev, src = f'description age {max(d_ages)}', 'description'
        if minor_word and not (a_cls == 'adult' and re.fullmatch(r'\s*[\d\s,/]*\d', str(c.get('age') or ''))):
            return {'class': 'unknown', 'evidence': f'{ev} but "{minor_word.group(0)}" in description', 'source': src}
        return {'class': 'adult', 'evidence': ev, 'source': src}
    if role and not minor_word and not skip:
        return {'class': 'adult', 'evidence': f'role: "{role.group(0)}" (description)', 'source': 'description-role'}
    why = 'no age' if not (c.get('age') or wiki_age) else 'age unclear'
    if minor_word:
        why += f', "{minor_word.group(0)}"'
    if skip:
        why += ', timeskip'
    return {'class': 'unknown', 'evidence': why, 'source': 'none'}


def anilist_descriptions(ids, cache):
    q = 'query($ids:[Int]){ Page(page:1, perPage:50){ characters(id_in:$ids){ id description(asHtml:false) } } }'
    need = [i for i in ids if cache.get(f'desc:{i}') is None]
    for k in range(0, len(need), 50):
        body = json.dumps({'query': q, 'variables': {'ids': need[k:k + 50]}}).encode()
        for t in range(4):
            try:
                req = urllib.request.Request('https://graphql.anilist.co', data=body, headers={'User-Agent': UA, 'Content-Type': 'application/json',
                                                                                              'Accept': 'application/json'})
                got = json.load(urllib.request.urlopen(req, timeout=60))['data']['Page']['characters']
                break
            except Exception:  # noqa: BLE001
                time.sleep(5 * (t + 1))
                got = []
        for c in got:
            cache.put(f"desc:{c['id']}", (c.get('description') or '')[:3000])
        time.sleep(1.0)
    return {i: cache.get(f'desc:{i}') or '' for i in ids}


# ------------------------------------------------------------------ image helpers
def flatten(im, bg=(255, 255, 255)):
    im = im.convert('RGBA')
    base = Image.new('RGBA', im.size, bg + (255,))
    base.alpha_composite(im)
    return base.convert('RGB')


def has_alpha(im):
    if im.mode not in ('RGBA', 'LA', 'P'):
        return False
    a = np.asarray(im.convert('RGBA'))[:, :, 3]
    return a.min() < 40 and (a < 128).mean() > 0.08


def figure_mask(im):
    """Mask from the alpha channel when the art is a cut-out, else the ISNet anime model."""
    if has_alpha(im):
        return np.asarray(im.convert('RGBA'))[:, :, 3].astype(np.float32) / 255.0, 'alpha'
    return pv.mask(im), 'isnet'


def fit_h(im, h):
    if im.size[1] <= h:
        return im
    return im.resize((max(1, round(im.size[0] * h / im.size[1])), h), Image.LANCZOS)


def save_jpg(im, path, q=78):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    im.convert('RGB').save(path, 'JPEG', quality=q, optimize=True, progressive=True)
    return os.path.getsize(path)



MONO_PIC, MONO_CROP = 6.0, 5.0   # colourfulness below this = black-and-white art (manga panels, line sketches): 1.7-5.3 measured,
                                 # the palest real colour crops 7.6+


def colourful(im):
    """Hasler-Suesstrunk colourfulness of a picture (0 = greyscale; anime colour art ~15-90)."""
    a = np.asarray(im.convert('RGB').resize((160, 110)), np.float32)
    rg = a[..., 0] - a[..., 1]
    yb = 0.5 * (a[..., 0] + a[..., 1]) - a[..., 2]
    return float(np.sqrt(rg.std() ** 2 + yb.std() ** 2) + 0.3 * np.sqrt(rg.mean() ** 2 + yb.mean() ** 2))


# ------------------------------------------------------------------ per format
def do_silhouette(c, img, cls):
    """(item or None, reason). img: full-character art. Mask + shape checks + gate on the colour art."""
    if img.size[1] < 480 or img.size[1] / img.size[0] > 4.5:
        return None, 'small'
    if colourful(flatten(img)) < MONO_PIC:
        return None, 'black-and-white art'
    m, how = figure_mask(img)
    st = pv.shape_stats(m)
    if not st:
        return None, 'empty mask'
    x0, y0, x1, y1 = st['bbox']
    bw, bh = x1 - x0, y1 - y0
    if bh < 0.5 * img.size[1] or bw < 0.15 * img.size[0]:
        return None, 'tiny figure'
    fill = st['coverage'] * img.size[0] * img.size[1] / max(1, bw * bh)
    if st['blob'] < 0.9 or not 0.22 <= fill <= 0.74 or st['solidity'] > 0.74 or bh / bw < 1.15:
        return None, f"not distinctive (fill {fill:.2f}, solidity {st['solidity']:.2f}, blob {st['blob']:.2f})"
    mg = int(0.05 * max(bw, bh))
    box = (max(0, x0 - mg), max(0, y0 - mg), min(img.size[0], x1 + mg), min(img.size[1], y1 + mg))
    col = flatten(img).crop(box)
    mk = Image.fromarray((np.clip(m, 0, 1) * 255).astype(np.uint8)).crop(box)
    s = SIL_H / col.size[1]
    size = (max(1, round(col.size[0] * s)), SIL_H)
    col, mk = col.resize(size, Image.LANCZOS), mk.resize(size, Image.LANCZOS)
    mk = mk.point(lambda v: 0 if v < 24 else 255 if v > 232 else v)
    # the colour art shown at the reveal is gated (cut out onto white, as stored)
    t = pv.tag(col)
    tg = t['tags']
    if tg.get('solo', 0) < 0.5 or max(tg.get(k, 0) for k in ('multiple_girls', 'multiple_boys', '2girls', '2boys', '1other', 'multiple_views')) >= 0.3:
        return None, 'not a single figure'
    cls = pv.effective_class(t, cls)
    ok, why = pv.gate(t, cls, framing=['full_body', 'upper_body', 'portrait', 'cowboy_shot_x'] if cls != 'adult' else None)
    if not ok:
        return None, 'gate: ' + why
    return {'col': col, 'mask': mk, 'size': size, 'stats': {'fill': round(fill, 2), 'solidity': round(st['solidity'], 2), 'mask': how},
            'gate': {'rating': {k: round(v, 2) for k, v in t['rating'].items()}, 'why': why, 'class': cls,
                     'swimwear': sorted(k for k in ('swimsuit', 'bikini', 'swimwear', 'one-piece_swimsuit', 'barefoot', 'bare_legs') if t['tags'].get(k, 0) >= 0.28)}}, 'ok'


def do_eyes(c, img, cls):
    """Eye band (super-resolved) + the portrait for the zoom-out reveal."""
    img = flatten(img)
    W, H = img.size
    pair = pv.eye_pair(pv.eyes(img))
    if not pair:
        return None, 'eyes not clear'
    a, b = pair
    aw = (a[2] + b[2]) / 2 * W
    if aw < 11:
        return None, f'eyes too small ({aw:.0f}px)'
    cx, cy = (a[0] + b[0]) / 2 * W, (a[1] + b[1]) / 2 * H
    span = abs(b[0] - a[0]) * W + aw
    bw = span * 2.3
    bh = bw * EYE_H / EYE_W
    if bw > W * 1.02 or bh > H * 1.02:
        bw, bh = min(bw, W), min(bh, H)
        if bw < span * 1.5:
            return None, 'band does not fit'
        bh = bw * EYE_H / EYE_W
    x0 = min(max(0, cx - bw / 2), W - bw)
    y0 = min(max(0, cy - bh * 0.46), H - bh)
    box = (int(x0), int(y0), int(x0 + bw), int(y0 + bh))
    crop = img.crop(box)
    if crop.size[0] < 60:
        return None, 'band too small'
    band = pv.upscale(crop).resize((EYE_W, EYE_H), Image.LANCZOS)
    t = pv.tag(band)
    tp = pv.tag(img)
    ok, why = pv.gate(t, cls)
    ok2, why2 = pv.gate(tp, cls, framing=['portrait', 'upper_body', 'close-up', 'face', 'full_body', 'cowboy_shot'] if cls != 'adult' else None)
    if not (ok and ok2):
        return None, 'gate: ' + (why if not ok else why2)
    port = fit_h(img, PORT_H)
    return {'band': band, 'portrait': port, 'box': [round(v / s, 4) for v, s in zip(box, (W, H, W, H))], 'eye_px': round(aw),
            'gate': {'rating': {k: round(v, 2) for k, v in tp.items() if False} or {k: round(v, 2) for k, v in tp['rating'].items()}, 'why': why2}}, 'ok'


def do_eye1(c, img, cls):
    """
    One eye, tight: the bigger eye of a clear pair (so it is really a face), cropped so the eye fills ~56% of the width (and <= 55% of the height) of a
    1.45:1 card with brow / lashes / a bit of hair around it; anime SR (twice for small sources) for a sharp crop.
    Gate on the whole picture (strict for minors / unknown ages); the portrait is stored for the reveal.
    """
    img = flatten(img)
    W, H = img.size
    if colourful(img) < MONO_PIC:
        return None, 'black-and-white art'
    pair = pv.eye_pair(pv.eyes(img))
    if not pair:
        return None, 'eyes not clear'
    e = max(pair, key=lambda d: d[2] * d[3])
    ew, eh = e[2] * W, e[3] * H
    if ew < EYE1_MIN_PX:
        return None, f'eye too small ({ew:.0f}px)'
    cw = max(ew / EYE1_FILL, eh / EYE1_FILL_H * EYE1_W / EYE1_H)
    ch = cw * EYE1_H / EYE1_W
    cx, cy = e[0] * W, e[1] * H + 0.04 * ch   # eye a touch above centre: brow and lashes in, cheek mostly out
    if cw > W or ch > H:
        return None, 'crop does not fit'
    x0 = min(max(0.0, cx - cw / 2), W - cw)
    y0 = min(max(0.0, cy - ch / 2), H - ch)
    if abs((x0 + cw / 2) - e[0] * W) > 0.18 * cw or abs((y0 + ch / 2) - e[1] * H) > 0.22 * ch:
        return None, 'eye too close to the picture edge'
    box = (int(round(x0)), int(round(y0)), int(round(x0 + cw)), int(round(y0 + ch)))
    crop = img.crop(box)
    up = pv.upscale(crop)
    if up.size[0] < EYE1_W * 0.8:
        up = pv.upscale(up)
    eye = up.resize((EYE1_W, EYE1_H), Image.LANCZOS)
    if colourful(eye) < MONO_CROP:
        return None, 'black-and-white eye crop'
    tp = pv.tag(img)
    cls = pv.effective_class(tp, cls)
    ok, why = pv.gate(tp, cls, framing=['portrait', 'upper_body', 'close-up', 'face', 'full_body', 'cowboy_shot'] if cls != 'adult' else None)
    if not ok:
        return None, 'gate: ' + why
    ok, why_e = pv.gate(pv.tag(eye), cls)
    if not ok:
        return None, 'gate (eye crop): ' + why_e
    port = pv.upscale(img) if H < 600 else img
    port = fit_h(port, 760)
    return {'eye': eye, 'portrait': port, 'eye_box': [round(v / s, 4) for v, s in zip(box, (W, H, W, H))], 'eye_px': round(ew),
            'gate': {'rating': {k: round(v, 2) for k, v in tp['rating'].items()}, 'why': why, 'class': cls}}, 'ok'


def do_outfit(c, img, cls):
    """Full-body art of an adult woman: waist-to-feet crop box, gated on the crop and the whole picture."""
    if cls not in pv.ADULT_CLASSES:
        return None, 'not confirmed adult (or appearance override strict)'
    if img.size[1] < 450 or not 1.25 <= img.size[1] / img.size[0] <= 4.2:
        return None, 'not a tall full-body picture'
    flat = flatten(img)
    if colourful(flat) < MONO_PIC:
        return None, 'black-and-white art'
    full_t = pv.tag(flat)
    cls = pv.effective_class(full_t, cls)   # young-looking picture: strict rules (no swimwear / bare legs / sensitive)
    tg = full_t['tags']
    if tg.get('full_body', 0) < 0.35 or tg.get('solo', 0) < 0.5 or tg.get('lower_body', 0) > 0.3:
        return None, f"no solo full-body (full_body {tg.get('full_body', 0):.2f}, solo {tg.get('solo', 0):.2f})"
    ok, why = pv.gate(full_t, cls)
    if not ok:
        return None, 'gate (whole): ' + why
    m, how = figure_mask(img)
    st = pv.shape_stats(m)
    if not st:
        return None, 'empty mask'
    x0, y0, x1, y1 = st['bbox']
    bw, bh = x1 - x0, y1 - y0
    if bh / max(1, bw) < 1.45 or st['blob'] < 0.85 or bh < 0.75 * img.size[1]:
        return None, f'pose not a standing full figure (h/w {bh / max(1, bw):.2f})'
    if how == 'isnet' and y1 >= img.size[1] - 1:
        return None, 'feet cut off by the frame'
    W, H = img.size
    top = y0 + 0.47 * bh                      # waist
    bot = min(H, y1 + 0.02 * bh)              # feet
    ch = bot - top
    cw = max(bw * 1.1, ch * 0.9)
    # centre on the legs: the mask's centre of mass below the waist
    ys, xs = np.nonzero(m[int(top):int(y1) + 1, :] > 0.5)
    cxm = float(xs.mean()) if len(xs) else (x0 + x1) / 2
    cx0 = min(max(0, cxm - cw / 2), max(0, W - cw))
    crop = (cx0, top, min(W, cx0 + cw), bot)
    full = fit_h(flat, FULL_H)
    s = full.size[1] / H
    cbox = [round(v * s) for v in crop]
    crop_img = full.crop(tuple(cbox))
    tc = pv.tag(crop_img)
    # our own waist-to-feet crop is what makes it "lower_body" / "head out of frame": not held against it (rating + skin tags still are)
    own = ('lower_body', 'head_out_of_frame', 'torso_crop', 'cowboy_shot', 'out_of_frame')
    ok, why = pv.gate({'rating': tc['rating'], 'tags': {k: v for k, v in tc['tags'].items() if k not in own}}, cls)
    if not ok:
        return None, 'gate (crop): ' + why
    if tc['tags'].get('1girl', 0) < 0.4 and tc['tags'].get('solo', 0) < 0.4:
        return None, 'crop not a clear lone figure'
    swim = sorted(k for k in ('swimsuit', 'bikini', 'swimwear', 'one-piece_swimsuit', 'barefoot', 'bare_legs', 'feet') if max(tc['tags'].get(k, 0), full_t['tags'].get(k, 0)) >= 0.28)
    return {'full': full, 'crop': [round(cbox[0] / full.size[0], 4), round(cbox[1] / full.size[1], 4), round(cbox[2] / full.size[0], 4),
                                   round(cbox[3] / full.size[1], 4)],
            'gate': {'rating': {k: round(v, 2) for k, v in full_t['rating'].items()}, 'why': why, 'class': cls,
                     'crop_rating': {k: round(v, 2) for k, v in tc['rating'].items()}, 'swimwear': swim}}, 'ok'


# ------------------------------------------------------------------ main
def build(limit=450, only=None, adult_scan=900):
    sys.path.insert(0, os.path.dirname(HERE))
    from render_pop import SCENE_SKIP, same_series, franchise
    DUEL_SKIP = {'bakemonogatari'}
    chars = json.load(open(os.path.join(ASSETS, 'anime-characters.json'), encoding='utf-8'))['characters']
    anime = {a['title']: a for a in json.load(open(os.path.join(ASSETS, 'anime-list.json'), encoding='utf-8'))['anime']}
    cache = Cache()
    ok_show = lambda c: not c.get('nsfw') and not any(same_series(c['anime'], s) for s in SCENE_SKIP) and franchise(c['anime']) not in DUEL_SKIP
    pool = [(k, c) for k, c in enumerate(chars) if ok_show(c)]
    # ages first: everyone in the scan window (silhouette / eyes need an age class; outfit needs adult women)
    scan = pool[:adult_scan]
    descs = anilist_descriptions([c['id'] for _, c in scan], cache)
    log(f'descriptions for {len(scan)}')
    wiki_info = {}
    for n, (k, c) in enumerate(scan):
        key = f"wikichar:{c['id']}"
        w = cache.get(key)
        if w is None:
            w = {}
            try:
                slug = wiki_for(c['anime'], (anime.get(c['anime']) or {}).get('romaji', ''), cache)
                if slug:
                    w = wiki_character(slug, c['name'], norm(c['name'])) or {}
            except Exception as e:  # noqa: BLE001
                log(f"  wiki {c['name']}: {e}")
                w = {'error': str(e)[:60]}
            cache.put(key, w)
            time.sleep(0.25)
        wiki_info[c['id']] = w
        if n % 50 == 0:
            log(f'wiki {n}/{len(scan)}')
    cache.save()
    for _, c in scan:
        ac = age_class(c, descs.get(c['id']), wiki_info[c['id']].get('age'))
        c['age_class'], c['age_evidence'], c['age_source'] = ac['class'], ac['evidence'], ac['source']
    json.dump({'source': 'AniList (anilist.co)', 'characters': chars}, open(os.path.join(ASSETS, 'anime-characters.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    from collections import Counter
    log('age classes', Counter(c['age_class'] for _, c in scan))

    out = {'silhouette': [], 'eyes': [], 'outfit': []}
    dropped = {'silhouette': Counter(), 'eyes': Counter(), 'outfit': Counter()}
    todo = scan[:limit] if not only else [(k, c) for k, c in scan if c['id'] in only]
    # beyond `limit`: adult women with a full-size wiki picture still get the outfit pass (that format needs many candidates)
    extra = [(k, c) for k, c in scan[limit:] if not only and c.get('gender') == 'Female' and c.get('age_class') == 'adult'
             and (wiki_info[c['id']].get('h') or 0) >= 450]
    n_main = len(todo)
    todo = todo + extra
    log(f'{n_main} full passes + {len(extra)} outfit-only passes')
    pop_items = {}
    for n, (rank, c) in enumerate(todo):
        w = wiki_info[c['id']]
        cls = c['age_class']
        diff = 1 if rank < 60 else 2 if rank < 180 else 3
        base = {'id': c['id'], 'name': c['name'], 'anime': c['anime'], 'favourites': c['favourites'], 'difficulty': diff,
                'gender': c.get('gender'), 'age': c.get('age'), 'age_class': cls, 'age_evidence': c['age_evidence'], 'image': c['image']}
        outfit_only = n >= n_main
        ckey = f"fmt:{c['id']}:v1" + ('o' if outfit_only else '')
        res = cache.get(ckey) or (cache.get(f"fmt:{c['id']}:v1") if outfit_only else None)
        if res is None:
            res = {}
            fandom_img = None
            if w.get('url') and (w.get('h') or 0) >= 450:
                try:
                    fandom_img = load_img(w['url'])
                except Exception as e:  # noqa: BLE001
                    log(f"  fandom image {c['name']}: {e}")
            try:
                ani = load_img(c['image'])
            except Exception as e:  # noqa: BLE001
                log(f"  anilist image {c['name']}: {e}")
                ani = None
            src_fan = {'kind': 'fandom', 'wiki': w.get('wiki'), 'page': w.get('page'), 'file': w.get('url'),
                       'licence': 'Fandom text CC-BY-SA 3.0; artwork (c) its rights holders, shown only to identify the character'}
            src_ani = {'kind': 'anilist', 'page': f"https://anilist.co/character/{c['id']}",
                       'licence': 'AniList character art; (c) its rights holders, shown only to identify the character'}
            # silhouette: full-character art (Fandom) first
            for name, fn, cands in (() if outfit_only else (('silhouette', do_silhouette, [(fandom_img, src_fan)]),
                                    ('eyes', do_eyes, [(ani, src_ani), (fandom_img, src_fan)]))) + (
                    ('outfit', do_outfit, [(fandom_img, src_fan), (ani, src_ani)] if c.get('gender') == 'Female' else []),):
                reason = 'no image'
                for img, src in cands:
                    if img is None:
                        continue
                    try:
                        r, reason = fn(c, img, cls)
                    except Exception as e:  # noqa: BLE001
                        r, reason = None, f'error {str(e)[:50]}'
                    if r:
                        r['source'] = src
                        res[name] = r
                        break
                if name not in res:
                    res[name] = {'dropped': reason}
            # stash images on disk now (cache holds only the JSON-able parts)
            for name, r in res.items():
                if 'dropped' in r:
                    continue
                if name == 'silhouette':
                    save_jpg(r['col'], os.path.join(POP2, 'sil', f"{c['id']}.jpg"), 76)
                    r['mask'].save(os.path.join(POP2, 'sil', f"{c['id']}.png"), optimize=True)
                    r['col'] = r['mask'] = None
                elif name == 'eyes':
                    save_jpg(r['band'], os.path.join(POP2, 'eyes', f"{c['id']}.jpg"), 78)
                    save_jpg(r['portrait'], os.path.join(POP2, 'eyes', f"{c['id']}_p.jpg"), 76)
                    r['band'] = r['portrait'] = None
                else:
                    save_jpg(r['full'], os.path.join(POP2, 'outfit', f"{c['id']}.jpg"), 78)
                    r['full'] = None
            cache.put(ckey, json.loads(json.dumps(res, default=lambda o: None)))
        for name in out:
            r = res.get(name) or {}
            if 'dropped' in r:
                dropped[name][re.sub(r'[\d.]+', '#', r['dropped'])[:40]] += 1
            elif r:
                out[name].append({**base, **{k: v for k, v in r.items() if k not in ('col', 'mask', 'band', 'portrait', 'full')}})
        if n % 10 == 0:
            log(f"{n}/{len(todo)} {c['name']}: sil {len(out['silhouette'])} eyes {len(out['eyes'])} outfit {len(out['outfit'])}")
    cache.save()
    json.dump({'source': 'AniList + Fandom wiki infobox art (see per-item source); gate: WD14 tagger v3, ISNet anime-seg, anime eye detector',
               'gate_rules': 'see core/quiz/pop_vision.py: questionable/explicit, nudity, underwear, see-through always rejected; adults (confirmed 18+) may be '
                             'swimwear / suggestive; minors and unknown ages strict (rating general, no swimwear / body-focus tags)',
               'silhouette': out['silhouette'], 'eyes': out['eyes'], 'outfit': out['outfit']},
              open(os.path.join(ASSETS, 'character-extras.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    log({k: len(v) for k, v in out.items()})
    for k, v in dropped.items():
        log(k, 'dropped:', dict(v.most_common(8)))
    return out


# ------------------------------------------------------------------ v2: every usable character, several wiki images each
SWIM_NAME = re.compile(r'swim|bikini|beach|summer|resort|pool|hot[ _]?spring|onsen|yukata|sunbath|bathing|seaside', re.I)
BAD_NAME = re.compile(r'icon|logo|signature|map|screenshot|banner|infobox|chibi|sprite|emoji|avatar|thumb|cosplay|figure|merch|plush|voice|seiyuu|'
                      r'\.gif|\.svg|\.webm|\.ogg|\.ogv|\.mp4|sticker|stamp|nendoroid|card|chapter|volume|cover', re.I)


def wiki_images(wiki, title, limit=4):
    """Candidate art on a character's wiki page and its /Gallery subpage: [{url,w,h,file,score,swim}] best first."""
    q = urllib.parse.urlencode({'action': 'query', 'titles': f'{title}|{title}/Gallery', 'prop': 'images', 'imlimit': 80, 'format': 'json'})
    pages = http(f'https://{wiki}.fandom.com/api.php?{q}', tries=2, timeout=25)['query']['pages']
    files = []
    for p in pages.values():
        for im in p.get('images') or []:
            f = im['title']
            if re.search(r'\.(png|jpe?g|webp)$', f, re.I) and not BAD_NAME.search(f) and f not in files:
                files.append(f)
    out = []
    for k in range(0, min(len(files), 60), 20):
        q = urllib.parse.urlencode({'action': 'query', 'titles': '|'.join(files[k:k + 20]), 'prop': 'imageinfo', 'iiprop': 'url|size', 'format': 'json'})
        for p in http(f'https://{wiki}.fandom.com/api.php?{q}', tries=2, timeout=25)['query']['pages'].values():
            ii = (p.get('imageinfo') or [{}])[0]
            w, h = ii.get('width') or 0, ii.get('height') or 0
            if not ii.get('url') or h < 450 or w < 250 or not 1.15 <= h / max(1, w) <= 4.6:
                continue
            sw = bool(SWIM_NAME.search(p['title']))
            out.append({'url': ii['url'], 'w': w, 'h': h, 'file': p['title'], 'swim': sw,
                        'score': (6 if sw else 0) + (1 if re.search(r'anime|official|full|key|visual', p['title'], re.I) else 0) + min(2, h / 1500)})
    out.sort(key=lambda c: -c['score'])
    return out[:limit]


def free_ram_gb():
    import subprocess
    try:
        r = subprocess.run(['powershell', '-NoProfile', '-Command', '(Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory'],
                           capture_output=True, text=True, timeout=30)
        return int(r.stdout.strip()) / 1048576
    except Exception:  # noqa: BLE001
        return 99.0


def heavy_running():
    """
    True when the build should pause. POP_MIN_FREE_GB set (e.g. 2): pause only while free RAM is below it (edit renders are
    small now). Otherwise: pause while an edit render (compose_blocks / aep_replay / ffmpeg encode) is running.
    """
    if os.environ.get('POP_MIN_FREE_GB'):
        return free_ram_gb() < float(os.environ['POP_MIN_FREE_GB'])
    import subprocess
    cmd = ("Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $PID -and $_.CommandLine -match 'compose_blocks|aep_replay|edit-pipeline|ffmpeg.*libx264' } "
           "| Measure-Object | Select-Object -ExpandProperty Count")
    try:
        r = subprocess.run(['powershell', '-NoProfile', '-Command', cmd], capture_output=True, text=True, timeout=30)
        return int((r.stdout or '0').strip() or 0) > 0
    except Exception:  # noqa: BLE001
        return False


def shard_filter(pool):
    """CI sharding (POP_SHARD='i/N', set by build-pop-media.py --shard i/N): this shard's slice of the characters (id % N == i)."""
    sh = os.environ.get('POP_SHARD')
    if not sh:
        return pool
    i, n = (int(x) for x in sh.split('/'))
    log(f'shard {i}/{n}')
    return [c for c in pool if c['id'] % n == i]


def build_v2(phase='light', minutes=40, batch_wait=60):
    """
    phase 'light': network only (descriptions, wiki pages, candidate images, age classes) for ALL usable characters.
    phase 'models': silhouette + outfit candidates through the models, in priority order (adult women first), waiting
    whenever an edit render is running; stops after `minutes`. Rerun to continue (everything is cached).
    """
    sys.path.insert(0, os.path.dirname(HERE))
    from render_pop import SCENE_SKIP, same_series, franchise
    chars = json.load(open(os.path.join(ASSETS, 'anime-characters.json'), encoding='utf-8'))['characters']
    anime = {a['title']: a for a in json.load(open(os.path.join(ASSETS, 'anime-list.json'), encoding='utf-8'))['anime']}
    cache = Cache()
    ok_show = lambda c: not c.get('nsfw') and not any(same_series(c['anime'], s) for s in SCENE_SKIP) and franchise(c['anime']) != 'bakemonogatari'
    pool = [c for c in chars if ok_show(c)]
    t_end = time.time() + minutes * 60
    if phase == 'light':
        descs = anilist_descriptions([c['id'] for c in pool], cache)
        log(f'descriptions: {len(pool)}')
        for n, c in enumerate(pool):
            if time.time() > t_end:
                log('light phase: time budget used, rerun to continue')
                break
            key = f"wikichar:{c['id']}"
            w = cache.get(key)
            if w is None:
                w = {}
                try:
                    slug = wiki_for(c['anime'], (anime.get(c['anime']) or {}).get('romaji', ''), cache)
                    if slug:
                        w = wiki_character(slug, c['name'], norm(c['name'])) or {}
                except Exception as e:  # noqa: BLE001
                    w = {'error': str(e)[:60]}
                cache.put(key, w)
                time.sleep(0.2)
            ikey = f"wikiimgs:{c['id']}"
            if w.get('title') and cache.get(ikey) is None:
                try:
                    imgs = wiki_images(w['wiki'], w['title'])
                except Exception:  # noqa: BLE001
                    imgs = []
                cache.put(ikey, imgs)
                time.sleep(0.2)
            if n % 100 == 0:
                log(f'light {n}/{len(pool)}')
        for c in pool:
            w = cache.get(f"wikichar:{c['id']}") or {}
            ac = age_class(c, descs.get(c['id']), w.get('age'))
            c['age_class'], c['age_evidence'], c['age_source'] = ac['class'], ac['evidence'], ac['source']
        json.dump({'source': 'AniList (anilist.co)', 'characters': chars}, open(os.path.join(ASSETS, 'anime-characters.json'), 'w', encoding='utf-8'),
                  ensure_ascii=False, indent=1)
        cache.save()
        from collections import Counter
        log('age classes', Counter(c['age_class'] for c in pool))
        log('with wiki images', sum(1 for c in pool if cache.get(f"wikiimgs:{c['id']}")))
        return
    pool = shard_filter(pool)
    for c in pool:
        c.setdefault('age_class', 'unknown')
    if phase == 'eyes':
        return build_eyes_v2(pool, cache, t_end, batch_wait)
    # ---- models phase
    order = sorted(pool, key=lambda c: (0 if (c['age_class'] == 'adult' and c.get('gender') == 'Female') else 1 if c['age_class'] == 'adult' else 2, -c['favourites']))
    done = 0
    for c in order:
        if cache.get(f"so:{c['id']}:v2") is not None:
            continue
        imgs = cache.get(f"wikiimgs:{c['id']}") or []
        w = cache.get(f"wikichar:{c['id']}") or {}
        if w.get('url') and (w.get('h') or 0) >= 450 and not any(i['url'] == w['url'] for i in imgs):
            imgs = imgs + [{'url': w['url'], 'w': w.get('w'), 'h': w.get('h'), 'file': 'infobox', 'swim': False, 'score': 0.5}]
        while time.time() < t_end and heavy_running():
            log(f'pausing: free RAM {free_ram_gb():.1f} GB / edit render')
            time.sleep(batch_wait)
        if time.time() > t_end:
            break
        cls = rule_class(c, cache, anime)
        res = {}
        todo = ['silhouette'] + (['outfit'] if cls in pv.ADULT_CLASSES and c.get('gender') == 'Female' else [])
        wiki_c = [(im['url'], {'kind': 'fandom', 'wiki': w.get('wiki'), 'page': w.get('page'), 'file': im['url'], 'name': im['file'],
                               'licence': 'Fandom text CC-BY-SA 3.0; artwork (c) its rights holders, shown only to identify the character'},
                   bool(im['swim'])) for im in imgs[:4]]
        for name in todo:
            fn = do_silhouette if name == 'silhouette' else do_outfit
            try:
                fan_c = pf.candidates(c, name, show_titles(c, anime), cache, cls=cls)
            except Exception as e:  # noqa: BLE001
                log(f"  fan art {c['name']}: {str(e)[:60]}")
                fan_c = []
            cands = wiki_c + fan_c
            if cls in pv.ADULT_CLASSES:   # adults: swimsuit / beach / bare-feet art first (wiki or fan art); the first that passes wins
                cands = [x for x in cands if x[2]] + [x for x in cands if not x[2]]
            best, reason = None, 'no art' if not cands else 'no passing image'
            for url, src, _ in cands:
                try:
                    pic = load_img(url)
                    if src.get('kind') == 'danbooru':   # fan art: only when it is on-model (recognisable)
                        ok_m, sim, why_m = om.check(pic, official_ref(c, w, cache))
                        src = {**src, 'onmodel': sim}
                        r, why = fn(c, pic, cls) if ok_m else (None, why_m)
                    else:
                        r, why = fn(c, pic, cls)
                except Exception as e:  # noqa: BLE001
                    r, why = None, f'error {str(e)[:40]}'
                if not r:
                    reason = why
                    continue
                r['source'] = src
                best = r
                break
            if best:
                if name == 'silhouette':
                    save_jpg(best['col'], os.path.join(POP2, 'sil', f"{c['id']}.jpg"), 76)
                    best['mask'].save(os.path.join(POP2, 'sil', f"{c['id']}.png"), optimize=True)
                    best['col'] = best['mask'] = None
                else:
                    save_jpg(best['full'], os.path.join(POP2, 'outfit', f"{c['id']}.jpg"), 78)
                    best['full'] = None
                res[name] = best
            else:
                res[name] = {'dropped': reason}
        if all('dropped' in r and r['dropped'].startswith('error') for r in res.values()):
            continue   # only errors (network / runtime): retry on the next run instead of caching a drop
        cache.put(f"so:{c['id']}:v2", json.loads(json.dumps(res, default=lambda o: None)))
        done += 1
        if done % 10 == 0:
            log(f"models {done}: {c['name']}  ({int((t_end - time.time()) / 60)} min left)")
    cache.save()
    finish_v2(cache)


_ANIME = {}


def anime_list():
    if not _ANIME:
        _ANIME.update({a['title']: a for a in json.load(open(os.path.join(ASSETS, 'anime-list.json'), encoding='utf-8'))['anime']})
    return _ANIME


OVERRIDES = os.path.join(ASSETS, 'appearance-overrides.json')
RULE_CLASS = {'adult': 'adult', 'adult-modest': 'adult-modest', 'strict': 'adult-strict'}


def overrides():
    try:
        return json.load(open(OVERRIDES, encoding='utf-8'))
    except (OSError, ValueError):
        return {'characters': {}}


def rule_class(c, cache, anime):
    """The gate class for a character: age_class, refined for adults ONLY by the explicit hand-made list in
    assets/appearance-overrides.json (owner 2026-10-10: a confirmed 18+ gets the full adult rules; the one exception is a
    character canonically drawn with a child's body, listed by hand and reviewed by the owner). No automatic flags, no
    body heuristics. Per-picture rejects (WD14 loli / shota / child / aged_down) and the Danbooru age tags stay.
    age_class itself is never changed."""
    if c.get('age_class') != 'adult':
        return c.get('age_class') or 'unknown'
    o = overrides()['characters'].get(str(c['id']))
    if o:
        return RULE_CLASS.get(o.get('rule'), 'adult-strict')
    return 'adult'


_CHARS = []


def official_ref(c, w, cache=None):
    """pop_onmodel reference: the anime look (AniList portrait, wiki infobox, 2 wiki gallery pictures) + castmates'
    AniList portraits (up to 5 most popular characters of the same show)."""
    from render_pop import same_series

    def get(u):
        try:
            return load_img(u) if u else None
        except Exception:  # noqa: BLE001
            return None
    if c['id'] in om._refs:
        return om._refs[c['id']]
    if not _CHARS:
        _CHARS.extend(sorted(json.load(open(os.path.join(ASSETS, 'anime-characters.json'), encoding='utf-8'))['characters'],
                             key=lambda o: -o['favourites']))
    gallery = [x['url'] for x in ((cache.get(f"wikiimgs:{c['id']}") if cache else None) or []) if x['url'] != w.get('url')][:2]
    mates = [o for o in _CHARS if o['id'] != c['id'] and same_series(o['anime'], c['anime'])][:5]
    return om.refs(c, [get(c.get('image')), get(w.get('url') if (w.get('h') or 0) >= 300 else None)] + [get(u) for u in gallery],
                   [get(o['image']) for o in mates])


def canon_young(c, cache):
    """petite / flat_chest tags that are CANONICAL for the character: WD14 on the official art (AniList portrait + wiki infobox).
    A fan-art swimwear post carrying such a tag is allowed only if the official art shows it too (owner 2026-10-10: no
    body-shaming; reject only when the fan art draws them flatter / smaller than canon). Cached as canon:<id>."""
    key = f"canon:{c['id']}"
    hit = cache.get(key)
    if hit is not None:
        return hit
    w = cache.get(f"wikichar:{c['id']}") or {}
    got, n = set(), 0
    for u in (c.get('image'), w.get('url') if (w.get('h') or 0) >= 300 else None):
        if not u:
            continue
        try:
            tg = pv.tag(flatten(load_img(u)))['tags']
        except Exception:  # noqa: BLE001
            continue
        n += 1
        small = tg.get('small_breasts', 0) >= 0.3
        for k in pf.YOUTHFUL_SUGGESTIVE:
            if tg.get(k, 0) >= 0.3 or (k == 'flat_chest' and small):
                got.add(k)
    if not n:
        return set()   # no official picture could be read: not cached, nothing is canonical (post stays rejected)
    out = sorted(got)
    cache.put(key, out)
    return out


pf.CANON_FN = canon_young


def show_titles(c, anime):
    a = anime.get(c['anime']) or {}
    return [t for t in (c['anime'], a.get('romaji')) if t]


EYES_FAN_ART = False   # owner 2026-10-09: the eyes format uses AniList / wiki art only, never fan art


def build_eyes_v2(pool, cache, t_end, batch_wait=60):
    """Single-eye crops for every usable character: AniList portrait, then the wiki infobox art (bigger eye wins). Official art only."""
    done = 0
    for c in sorted(pool, key=lambda c: -c['favourites']):
        key = f"eye1:{c['id']}"
        prev = cache.get(key)
        fan_key = f"eyefan:{c['id']}"
        if prev is not None:   # eyes = official art only (owner 2026-10-09): no fan-art retry for dropped characters
            continue
        while time.time() < t_end and heavy_running():
            log(f'pausing: free RAM {free_ram_gb():.1f} GB / edit render')
            time.sleep(batch_wait)
        if time.time() > t_end:
            break
        w = cache.get(f"wikichar:{c['id']}") or {}
        cands = []
        if prev is None:   # official art first: the AniList portrait, then the wiki infobox art
            cands = [(c['image'], {'kind': 'anilist', 'page': f"https://anilist.co/character/{c['id']}",
                                   'licence': 'AniList character art; (c) its rights holders, shown only to identify the character'})]
            if w.get('url') and (w.get('h') or 0) >= 450:
                cands.append((w['url'], {'kind': 'fandom', 'wiki': w.get('wiki'), 'page': w.get('page'), 'file': w['url'],
                                         'licence': 'Fandom text CC-BY-SA 3.0; artwork (c) its rights holders, shown only to identify the character'}))
        best, reason = None, (prev or {}).get('dropped', 'no image')
        fan_tried = False
        cls = rule_class(c, cache, anime_list())
        for url, src in cands:
            try:
                r, why = do_eye1(c, load_img(url), cls)
            except Exception as e:  # noqa: BLE001
                r, why = None, f'error {str(e)[:40]}'
            if r and (best is None or r['eye_px'] > best['eye_px'] * 1.3):
                r['source'] = src
                best = r
            elif not r and best is None:
                reason = why
        if best is None and EYES_FAN_ART and not reason.startswith('error'):   # (off: eyes use official art only)
            fan_tried = True
            try:
                fan_c = pf.candidates(c, 'eyes', show_titles(c, anime_list()), cache, limit=3, cls=cls)
            except Exception as e:  # noqa: BLE001
                fan_c, reason = [], f'error fan art {str(e)[:40]}'
            for url, src, _ in fan_c:
                try:
                    pic = load_img(url)
                    ok_m, sim, why_m = om.check(pic, official_ref(c, w, cache), eyes=True)   # on-model + same eye colour as the official art
                    src = {**src, 'onmodel': sim}
                    r, why = do_eye1(c, pic, cls) if ok_m else (None, why_m)
                except Exception as e:  # noqa: BLE001
                    r, why = None, f'error {str(e)[:40]}'
                if r:
                    r['source'] = src
                    best = r
                    break
        if fan_tried and not reason.startswith('error'):
            cache.put(fan_key, 1)
        if best:
            save_jpg(best['eye'], os.path.join(POP2, 'eyes', f"{c['id']}.jpg"), 84)
            save_jpg(best['portrait'], os.path.join(POP2, 'eyes', f"{c['id']}_p.jpg"), 80)
            best['eye'] = best['portrait'] = None
            cache.put(key, json.loads(json.dumps(best, default=lambda o: None)))
        elif not reason.startswith('error'):   # network / runtime errors are retried on the next run, not cached as drops
            cache.put(key, {'dropped': reason})
        done += 1
        if done % 25 == 0:
            n_ok = sum(1 for k, v in cache.d.items() if k.startswith('eye1:') and 'dropped' not in v)
            log(f"eyes {done}: {c['name']}  ok so far {n_ok}  ({int((t_end - time.time()) / 60)} min left)")
    cache.save()
    finish_v2(cache)


def merge_ai_base(out, chars, rank):
    """AI-generated silhouette / outfit items (pop_gen scope 'base') join the normal pools only where the character has no real item."""
    import shutil
    try:
        ai = json.load(open(os.path.join(pv.MODELS, 'ai-cache.json'), encoding='utf-8'))
    except (OSError, ValueError):
        return
    have = {f: {it['id'] for it in out[f]} for f in ('silhouette', 'outfit')}
    for k, v in ai.items():
        if not k.startswith('ai:base:') or not isinstance(v, dict):
            continue
        cid = int(k.split(':')[2])
        c = chars.get(cid)
        if not c:
            continue
        for f in ('silhouette', 'outfit'):
            r = v.get(f)
            if not isinstance(r, dict) or 'dropped' in r or cid in have[f]:
                continue
            if f == 'outfit' and (c.get('age_class') != 'adult' or c.get('gender') != 'Female'):
                continue
            src = os.path.join(pv.MODELS, 'gen', 'stage', 'base', f, f'{cid}.jpg')
            if not os.path.exists(src):
                continue
            d = 'sil' if f == 'silhouette' else 'outfit'
            os.makedirs(os.path.join(POP2, d), exist_ok=True)
            shutil.copyfile(src, os.path.join(POP2, d, f'{cid}.jpg'))
            if f == 'silhouette':
                shutil.copyfile(src[:-4] + '.png', os.path.join(POP2, 'sil', f'{cid}.png'))
            out[f].append({'id': cid, 'name': c['name'], 'anime': c['anime'], 'favourites': c['favourites'],
                           'difficulty': 1 if rank[cid] < 60 else 2 if rank[cid] < 180 else 3, 'gender': c.get('gender'), 'age': c.get('age'),
                           'age_class': c['age_class'], 'age_evidence': c.get('age_evidence'), 'image': c['image'], **r})


def finish_v2(cache=None):
    """Merge the v2 silhouette / outfit results with the stored eyes into character-extras.json; drop orphan files."""
    cache = cache or Cache()
    path = EXTRAS
    prev = json.load(open(path, encoding='utf-8')) if os.path.exists(path) else {}
    chars = {c['id']: c for c in json.load(open(os.path.join(ASSETS, 'anime-characters.json'), encoding='utf-8'))['characters']}
    rank = {c['id']: k for k, c in enumerate(sorted(chars.values(), key=lambda c: -c['favourites']))}
    eye1 = {int(k.split(':')[1]): v for k, v in cache.d.items() if k.startswith('eye1:')}
    out = {'silhouette': [], 'outfit': [], 'eyes': [] if eye1 else prev.get('eyes', [])}
    for cid, r in eye1.items():
        c = chars.get(cid)
        if not c or not r or 'dropped' in r or not os.path.exists(os.path.join(POP2, 'eyes', f'{cid}.jpg')):
            continue
        out['eyes'].append({'id': cid, 'name': c['name'], 'anime': c['anime'], 'favourites': c['favourites'],
                            'difficulty': 1 if rank[cid] < 60 else 2 if rank[cid] < 180 else 3, 'gender': c.get('gender'), 'age': c.get('age'),
                            'age_class': c.get('age_class'), 'age_evidence': c.get('age_evidence'), 'image': c['image'], 'kind': 'eye1', **r})
    for k, v in cache.d.items():
        if not k.startswith('so:'):
            continue
        cid = int(k.split(':')[1])
        c = chars.get(cid)
        if not c:
            continue
        for name in ('silhouette', 'outfit'):
            r = (v or {}).get(name)
            if not r or 'dropped' in r:
                continue
            if name == 'outfit' and (c.get('age_class') != 'adult' or c.get('gender') != 'Female'):
                continue
            if name == 'silhouette':
                try:
                    m = Image.open(os.path.join(POP2, 'sil', f'{cid}.png'))
                except OSError:
                    continue
                if m.size[1] / m.size[0] < 1.15 or r['stats']['solidity'] > 0.74:
                    continue
            out[name].append({'id': cid, 'name': c['name'], 'anime': c['anime'], 'favourites': c['favourites'],
                              'difficulty': 1 if rank[cid] < 60 else 2 if rank[cid] < 180 else 3, 'gender': c.get('gender'), 'age': c.get('age'),
                              'age_class': c['age_class'], 'age_evidence': c.get('age_evidence'), 'image': c['image'], **r})
    merge_ai_base(out, chars, rank)
    for k in out:
        out[k].sort(key=lambda it: -it['favourites'])
    used = set()
    for it in out['silhouette']:
        used |= {f"sil/{it['id']}.jpg", f"sil/{it['id']}.png"}
    for it in out['eyes']:
        used |= {f"eyes/{it['id']}.jpg", f"eyes/{it['id']}_p.jpg"}
    for it in out['outfit']:
        used |= {f"outfit/{it['id']}.jpg"}
    for d in ('sil', 'eyes', 'outfit'):
        for f in os.listdir(os.path.join(POP2, d)):
            if f'{d}/{f}' not in used:
                os.remove(os.path.join(POP2, d, f))
    prev.update(out)
    json.dump(prev, open(path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    log({k: len(v) for k, v in out.items()})


# ------------------------------------------------------------------ themed editions (core/quiz/themes.json)
THEMES_FILE = os.path.join(HERE, 'themes.json')
ART_FORMATS = ('character', 'silhouette', 'outfit', 'eyes')


def themes():
    return json.load(open(THEMES_FILE, encoding='utf-8'))['themes']


def theme_class(c, th, cache, anime):
    """Gate class of a character for a theme, or None when the theme's age rule leaves them out.
    'adults' = age_class adult and NO appearance override (owner list, or one the build would add); 'any' = the usual rules."""
    if th.get('age_rule') == 'adults':
        if c.get('age_class') != 'adult' or str(c['id']) in overrides()['characters']:
            return None
        return 'adult' if rule_class(c, cache, anime) == 'adult' else None
    return rule_class(c, cache, anime)


def theme_confirm(t, th):
    """The picture is on theme: one of the theme's WD14 tags (swimsuit, yukata, witch_hat, ...) at >= 0.35."""
    hit = [k for k in th['art']['confirm'] if t['tags'].get(k, 0) >= 0.35]
    return bool(hit), hit


def do_character_art(c, img, cls):
    """Themed reveal art for the character format (blur reveal): gate on the whole picture, stored at 900 px high."""
    flat = flatten(img)
    if colourful(flat) < MONO_PIC:
        return None, 'black-and-white art'
    t = pv.tag(flat)
    tg = t['tags']
    # one figure only: a group picture (e.g. a wiki swimsuit poster of three girls) is not "this character", and the others
    # in it were never age-checked
    if tg.get('solo', 0) < 0.5 or max(tg.get(k, 0) for k in ('multiple_girls', 'multiple_boys', '2girls', '2boys', '1other', 'multiple_views')) >= 0.3:
        return None, 'not a single figure'
    cls = pv.effective_class(t, cls)
    ok, why = pv.gate(t, cls, framing=['portrait', 'upper_body', 'full_body', 'cowboy_shot'] if cls not in pv.ADULT_CLASSES else None)
    if not ok:
        return None, 'gate: ' + why
    return {'full': fit_h(flat, 900), 'gate': {'rating': {k: round(v, 2) for k, v in t['rating'].items()}, 'why': why, 'class': cls,
            'swimwear': sorted(k for k in ('swimsuit', 'bikini', 'one-piece_swimsuit', 'barefoot', 'bare_legs') if t['tags'].get(k, 0) >= 0.28)}}, 'ok'


def theme_order(pool, top_n=500, women_share=0.75, seed=20261011):
    """Processing order for theme builds (owner plan 2026-10-11): ~75 % women / 25 % men, most popular (AniList favourites) first for the
    top `top_n`, then alternating the next most popular with a RANDOM pick from the wider list, so the pools are not only famous faces.
    Characters of other / unknown gender follow the men's stream."""
    import random
    rng = random.Random(seed)
    w = sorted([c for c in pool if c.get('gender') == 'Female'], key=lambda c: -c['favourites'])
    m = sorted([c for c in pool if c.get('gender') != 'Female'], key=lambda c: -c['favourites'])
    out, wi, mi, k = [], [], [], 0
    wl, ml = list(w), list(m)

    def take(lst, popular):
        if not lst:
            return None
        if popular or len(out) < top_n:
            return lst.pop(0)
        return lst.pop(rng.randrange(len(lst)))
    toggle = 0
    while wl or ml:
        want_w = (len(out) % 4 != 3) if (wl and ml) else bool(wl)   # 3 women : 1 man
        lst = wl if want_w else ml
        c = take(lst, popular=(toggle % 2 == 0))
        if c is None:
            continue
        out.append(c)
        if len(out) >= top_n:
            toggle += 1
    return out


def build_theme(tid, minutes=40, batch_wait=60):
    """Theme pool for one themes.json entry: art themes get themed art per character (wiki pictures named for the theme,
    then themed fan art; same quality / on-model / WD14 gates + the theme check); selector themes (villains, glasses)
    pick characters and reuse their normal pool items. Resumable (cache keys theme:<tid>:<id>, themesel:<tid>:<id>)."""
    sys.path.insert(0, os.path.dirname(HERE))
    from render_pop import SCENE_SKIP, same_series, franchise
    th = themes()[tid]
    chars = json.load(open(os.path.join(ASSETS, 'anime-characters.json'), encoding='utf-8'))['characters']
    anime = anime_list()
    cache = Cache()
    pool = [c for c in chars if not c.get('nsfw') and not any(same_series(c['anime'], s) for s in SCENE_SKIP) and franchise(c['anime']) != 'bakemonogatari']
    pool = shard_filter(pool)
    t_end = time.time() + minutes * 60
    sel = th.get('select')
    if sel:
        if sel.get('desc'):
            rx = re.compile(sel['desc'], re.I)
            nx = re.compile(sel['not_desc'], re.I) if sel.get('not_desc') else None
            names = {n.lower() for n in sel.get('names', [])}       # hand-checked famous ones (owner: a "villains" edition must not show heroes)
            excl = {n.lower() for n in sel.get('exclude', [])}
            for c in pool:
                d = cache.get(f"desc:{c['id']}") or ''
                hit = c['name'].lower() in names or bool(rx.search(d) and not (nx and nx.search(d)))
                cache.put(f'themesel:{tid}:{c["id"]}', bool(hit and c['name'].lower() not in excl))
        else:
            for c in sorted(pool, key=lambda c: -c['favourites']):
                k = f'themesel:{tid}:{c["id"]}'
                if cache.get(k) is not None:
                    continue
                if time.time() > t_end:
                    break
                while time.time() < t_end and heavy_running():
                    log(f'pausing: free RAM {free_ram_gb():.1f} GB / edit render')
                    time.sleep(batch_wait)
                try:
                    t = pv.tag(load_img(c['image']))
                    cache.put(k, any(t['tags'].get(x, 0) >= 0.5 for x in sel['wd14']))
                except Exception as e:  # noqa: BLE001
                    log(f"  select {c['name']}: {str(e)[:50]}")
        cache.save()
        return finish_theme(tid, cache)
    fmts = [f for f in th['formats'] if f in ART_FORMATS and f != 'eyes']   # eye crops do not show a theme
    wiki_rx = re.compile(th['art'].get('wiki') or r'^$', re.I)
    # adult women first (outfit rounds + most themed art), then other adults, then everyone else; popular first
    order = theme_order(pool)
    done = 0
    fun = cache.get(f'funnel:{tid}') or {'chars': 0, 'with_tag': 0, 'with_candidates': {}, 'passed': {}, 'images': {}, 'posts': {}}
    for c in order:
        key = f'theme:{tid}:{c["id"]}'
        if cache.get(key) is not None:
            continue
        cls = theme_class(c, th, cache, anime)
        if cls is None:
            cache.put(key, {'skip': 'age rule'})
            continue
        if time.time() > t_end:
            break
        while time.time() < t_end and heavy_running():
            log(f'pausing: free RAM {free_ram_gb():.1f} GB / edit render')
            time.sleep(batch_wait)
        w = cache.get(f"wikichar:{c['id']}") or {}
        wiki_c = [(im['url'], {'kind': 'fandom', 'wiki': w.get('wiki'), 'page': w.get('page'), 'file': im['url'], 'name': im['file'],
                               'licence': 'Fandom text CC-BY-SA 3.0; artwork (c) its rights holders, shown only to identify the character'}, True)
                  for im in (cache.get(f"wikiimgs:{c['id']}") or []) if wiki_rx.search(im['file'])]
        res = {}
        fun['chars'] += 1
        fun['with_tag'] += 1 if cache.get(f"dbtag:{c['id']}") else 0
        for fmt in fmts:
            if fmt == 'outfit' and not (cls in pv.ADULT_CLASSES and c.get('gender') == 'Female'):
                continue
            pf.STATS.clear()
            try:
                fan_c = pf.theme_candidates(c, fmt, show_titles(c, anime), cache, th, cls)
            except Exception as e:  # noqa: BLE001
                log(f"  theme fan art {c['name']}: {str(e)[:60]}")
                fan_c = []
            fn = {'silhouette': do_silhouette, 'outfit': do_outfit, 'character': do_character_art}[fmt]
            best, reason = None, 'no themed art' if not (wiki_c or fan_c) else 'no passing image'
            if fan_c:
                fun['with_candidates'][fmt] = fun['with_candidates'].get(fmt, 0) + 1
            for k2, n2 in pf.STATS.items():
                fun['posts'][k2] = fun['posts'].get(k2, 0) + n2
            fi = fun['images'].setdefault(fmt, {})
            for url, src, _ in wiki_c + fan_c:
                hit = []
                try:
                    pic = load_img(url)
                    ok_t, hit = theme_confirm(pv.tag(flatten(pic)), th)
                    if not ok_t:
                        r, why = None, 'not on theme'
                    elif src.get('kind') == 'danbooru':
                        ok_m, sim, why_m = om.check(pic, official_ref(c, w, cache))
                        src = {**src, 'onmodel': sim}
                        r, why = fn(c, pic, cls) if ok_m else (None, why_m)
                    else:
                        r, why = fn(c, pic, cls)
                except Exception as e:  # noqa: BLE001
                    r, why = None, f'error {str(e)[:40]}'
                if not r:
                    reason = why
                    kk = re.sub(r'[0-9.]+', '#', str(why))[:40]
                    fi[kk] = fi.get(kk, 0) + 1
                    continue
                r['source'], r['theme_hit'] = src, hit
                best = r
                fun['passed'][fmt] = fun['passed'].get(fmt, 0) + 1
                break
            if best:
                d = os.path.join(POP2, 'theme', tid, fmt)
                os.makedirs(d, exist_ok=True)
                if fmt == 'silhouette':
                    save_jpg(best['col'], os.path.join(d, f"{c['id']}.jpg"), 76)
                    best['mask'].save(os.path.join(d, f"{c['id']}.png"), optimize=True)
                    best['col'] = best['mask'] = None
                else:
                    save_jpg(best['full'], os.path.join(d, f"{c['id']}.jpg"), 80)
                    best['full'] = None
                res[fmt] = best
            else:
                res[fmt] = {'dropped': reason}
        if res and all('dropped' in r and r['dropped'].startswith('error') for r in res.values()):
            continue
        cache.put(key, json.loads(json.dumps({'class': cls, **res}, default=lambda o: None)))
        cache.put(f'funnel:{tid}', fun)
        done += 1
        if done % 10 == 0:
            n_ok = sum(1 for k, v in cache.d.items() if k.startswith(f'theme:{tid}:') and any(isinstance(x, dict) and 'dropped' not in x for x in v.values()))
            log(f"theme {tid} {done}: {c['name']}  characters with art {n_ok}  ({int((t_end - time.time()) / 60)} min left)")
    cache.save()
    return finish_theme(tid, cache)



def build_topup(minutes=40, scopes=('base', 'beach', 'halloween'), batch_wait=60):
    """Second pass over characters whose silhouette / outfit / themed art was DROPPED: try only official promotional art + game art
    (Danbooru official_art / game_cg; source label PROMO / GAME) with the same quality, on-model, WD14 gate and age rules.
    Owner 2026-10-10 (more image sources, no figurines). Resumable (cache keys topup1:<scope>:<format>:<id>); a pass replaces the
    dropped entry. Run --phase finish (base) / finish_theme afterwards."""
    from render_pop import SCENE_SKIP, same_series, franchise
    chars = json.load(open(os.path.join(ASSETS, 'anime-characters.json'), encoding='utf-8'))['characters']
    anime = anime_list()
    cache = Cache()
    pool = [c for c in chars if not c.get('nsfw') and not any(same_series(c['anime'], s) for s in SCENE_SKIP) and franchise(c['anime']) != 'bakemonogatari']
    t_end = time.time() + minutes * 60
    order = sorted(pool, key=lambda c: (0 if (c.get('age_class') == 'adult' and c.get('gender') == 'Female') else 1 if c.get('age_class') == 'adult' else 2, -c['favourites']))
    done = got = 0
    for c in order:
        w = cache.get(f"wikichar:{c['id']}") or {}
        for scope in scopes:
            base = scope == 'base'
            key = f"so:{c['id']}:v2" if base else f"theme:{scope}:{c['id']}"
            v = cache.get(key)
            if not isinstance(v, dict) or 'skip' in v:
                continue
            th = None if base else themes()[scope]
            cls = rule_class(c, cache, anime) if base else v.get('class')
            fmts = ['silhouette', 'outfit'] if base else [f for f in th['formats'] if f in ART_FORMATS and f != 'eyes']
            for fmt in fmts:
                r0 = v.get(fmt)
                tk = f"topup1:{scope}:{fmt}:{c['id']}"
                if cache.get(tk) or (isinstance(r0, dict) and 'dropped' not in r0):
                    continue
                if r0 is None and fmt == 'outfit':
                    continue   # outfit was never eligible (not an adult woman)
                if fmt == 'outfit' and not (cls in pv.ADULT_CLASSES and c.get('gender') == 'Female'):
                    continue
                if time.time() > t_end:
                    cache.save()
                    log(f'topup: time up ({done} tried, {got} new)')
                    return
                while time.time() < t_end and heavy_running():
                    log(f'pausing: free RAM {free_ram_gb():.1f} GB / edit render')
                    time.sleep(batch_wait)
                try:
                    if base:
                        cands = pf.candidates(c, fmt, show_titles(c, anime), cache, cls=cls, official=True, only_official=True)
                    else:
                        cands = [x for x in pf.theme_candidates(c, fmt, show_titles(c, anime), cache, th, cls) if x[1].get('srctype', 'FAN ART') != 'FAN ART']
                except Exception as e:  # noqa: BLE001
                    log(f"  topup {c['name']}: {str(e)[:60]}")
                    continue
                fn = {'silhouette': do_silhouette, 'outfit': do_outfit, 'character': do_character_art}[fmt]
                best = None
                for url, src, _ in cands:
                    try:
                        pic = load_img(url)
                        if not base:
                            ok_t, hit = theme_confirm(pv.tag(flatten(pic)), th)
                            if not ok_t:
                                continue
                        ok_m, sim, why_m = om.check(pic, official_ref(c, w, cache))
                        if not ok_m:
                            continue
                        r, why = fn(c, pic, cls)
                    except Exception as e:  # noqa: BLE001
                        continue
                    if r:
                        r['source'] = {**src, 'onmodel': sim}
                        if not base:
                            r['theme_hit'] = hit
                        best = r
                        break
                done += 1
                cache.put(tk, True)
                if not best:
                    continue
                d = os.path.join(POP2, 'sil' if fmt == 'silhouette' else 'outfit') if base else os.path.join(POP2, 'theme', scope, fmt)
                os.makedirs(d, exist_ok=True)
                if fmt == 'silhouette':
                    save_jpg(best['col'], os.path.join(d, f"{c['id']}.jpg"), 76)
                    best['mask'].save(os.path.join(d, f"{c['id']}.png"), optimize=True)
                    best['col'] = best['mask'] = None
                else:
                    save_jpg(best['full'], os.path.join(d, f"{c['id']}.jpg"), 78 if base else 80)
                    best['full'] = None
                v[fmt] = json.loads(json.dumps(best, default=lambda o: None))
                cache.put(key, v)
                got += 1
                log(f"topup NEW {scope}/{fmt}: {c['name']} [{best['source'].get('srctype')}]")
        if done and done % 25 == 0:
            cache.save()
    cache.save()
    log(f'topup done ({done} tried, {got} new)')


def merge_ai_theme(tid, out, chars, base):
    """AI-generated items (pop_gen: ai-cache.json, staged pictures) join a theme pool ONLY where the character has no real item
    for that format (real art wins). The staged picture is copied into assets/pop2/theme/<tid>/<format>/."""
    import shutil
    try:
        ai = json.load(open(os.path.join(pv.MODELS, 'ai-cache.json'), encoding='utf-8'))
    except (OSError, ValueError):
        return
    have = {f: {it['id'] for it in out[f]} for f in out}
    for k, v in ai.items():
        if not k.startswith(f'ai:{tid}:') or not isinstance(v, dict):
            continue
        cid = int(k.split(':')[2])
        c = chars.get(cid)
        if not c:
            continue
        for f, r in v.items():
            if f not in out or not isinstance(r, dict) or 'dropped' in r or cid in have[f]:
                continue
            src = os.path.join(pv.MODELS, 'gen', 'stage', tid, f, f'{cid}.jpg')
            if not os.path.exists(src):
                continue
            d = f'theme/{tid}/{f}'
            os.makedirs(os.path.join(POP2, d), exist_ok=True)
            shutil.copyfile(src, os.path.join(POP2, d, f'{cid}.jpg'))
            if f == 'silhouette':
                shutil.copyfile(src[:-4] + '.png', os.path.join(POP2, d, f'{cid}.png'))
            out[f].append({**base(c), **r, 'dir': d, 'gate_class': v.get('class')})


def finish_theme(tid, cache=None):
    """Merge a theme pool into character-extras.json -> themes.<tid>.<format> (items carry 'dir' + 'edition')."""
    cache = cache or Cache()
    th = themes()[tid]
    path = EXTRAS
    ex = json.load(open(path, encoding='utf-8'))
    chars = {c['id']: c for c in json.load(open(os.path.join(ASSETS, 'anime-characters.json'), encoding='utf-8'))['characters']}
    rank = {c['id']: k for k, c in enumerate(sorted(chars.values(), key=lambda c: -c['favourites']))}

    def base(c):
        return {'id': c['id'], 'name': c['name'], 'anime': c['anime'], 'favourites': c['favourites'],
                'difficulty': 1 if rank[c['id']] < 60 else 2 if rank[c['id']] < 180 else 3, 'gender': c.get('gender'), 'age': c.get('age'),
                'age_class': c.get('age_class'), 'age_evidence': c.get('age_evidence'), 'image': c['image'], 'edition': tid}
    out = {f: [] for f in th['formats'] if f in ART_FORMATS}
    if th.get('select'):
        ids = {int(k.split(':')[2]) for k, v in cache.d.items() if k.startswith(f'themesel:{tid}:') and v}
        for f in out:
            if f == 'character':
                out[f] = [base(chars[i]) for i in ids if i in chars]
            else:
                out[f] = [{**it, 'edition': tid} for it in ex.get(f, []) if it['id'] in ids]
    else:
        for k, v in cache.d.items():
            if not k.startswith(f'theme:{tid}:') or 'skip' in v:
                continue
            cid = int(k.split(':')[2])
            c = chars.get(cid)
            if not c:
                continue
            for f, r in v.items():
                if f not in out or not isinstance(r, dict) or 'dropped' in r:
                    continue
                d = f'theme/{tid}/{f}'
                if not os.path.exists(os.path.join(POP2, d, f'{cid}.jpg')):
                    continue
                out[f].append({**base(c), **r, 'dir': d, 'gate_class': v.get('class')})
    if not th.get('select'):
        merge_ai_theme(tid, out, chars, base)
    for f in out:
        out[f].sort(key=lambda it: -it['favourites'])
    ex.setdefault('themes', {})[tid] = out
    json.dump(ex, open(path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    log(f'theme {tid}:', {f: len(v) for f, v in out.items()})
    return out


# ------------------------------------------------------------------ CI phases: prefetch (network only), regate, compact
def todo_characters(cache, pool):
    """Characters that still need model work (eyes / models / art themes), new and popular first."""
    th = {k: v for k, v in themes().items() if not v.get('select')}
    need = []
    for c in sorted(pool, key=lambda c: -c['favourites']):
        jobs = []
        if cache.get(f"eye1:{c['id']}") is None:
            jobs.append('eyes')
        if cache.get(f"so:{c['id']}:v2") is None:
            jobs.append('models')
        jobs += [f'theme:{t}' for t in th if cache.get(f'theme:{t}:{c["id"]}') is None]
        if jobs:
            need.append((c, jobs))
    return need


def prefetch(minutes=60, limit=None):
    """Network half of the model phases: Danbooru answers (cached), wiki / AniList / fan-art pictures (POP_IMG_CACHE), the
    on-model references, the appearance auto-flags. No model runs. Then a GPU session can do the rest offline."""
    sys.path.insert(0, os.path.dirname(HERE))
    from render_pop import SCENE_SKIP, same_series, franchise
    assert IMG_CACHE, 'set POP_IMG_CACHE'
    chars = json.load(open(os.path.join(ASSETS, 'anime-characters.json'), encoding='utf-8'))['characters']
    anime = anime_list()
    cache = Cache()
    pool = [c for c in chars if not c.get('nsfw') and not any(same_series(c['anime'], s) for s in SCENE_SKIP) and franchise(c['anime']) != 'bakemonogatari']
    by_fav = sorted(chars, key=lambda o: -o['favourites'])
    t_end = time.time() + minutes * 60
    n_img = 0
    todo = todo_characters(cache, pool)[:limit] if limit else todo_characters(cache, pool)
    log(f'prefetch: {len(todo)} characters need model work')
    for n, (c, jobs) in enumerate(todo):
        if time.time() > t_end:
            log('prefetch: time budget used, rerun to continue')
            break
        w = cache.get(f"wikichar:{c['id']}") or {}
        urls = [c['image']] + ([w['url']] if w.get('url') else [])
        try:
            cls = rule_class(c, cache, anime)
            if 'models' in jobs:
                urls += [im['url'] for im in (cache.get(f"wikiimgs:{c['id']}") or [])[:4]]
                for fmt in ['silhouette'] + (['outfit'] if cls in pv.ADULT_CLASSES and c.get('gender') == 'Female' else []):
                    urls += [u for u, _, _ in pf.candidates(c, fmt, show_titles(c, anime), cache, cls=cls)]
            for j in jobs:
                if j.startswith('theme:'):
                    tid = j.split(':')[1]
                    t = themes()[tid]
                    tc = theme_class(c, t, cache, anime)
                    if tc is None:
                        continue
                    rx = re.compile(t['art'].get('wiki') or r'^$', re.I)
                    urls += [im['url'] for im in (cache.get(f"wikiimgs:{c['id']}") or []) if rx.search(im['file'])]
                    for fmt in [f for f in t['formats'] if f in ('character', 'silhouette', 'outfit')]:
                        urls += [u for u, _, _ in pf.theme_candidates(c, fmt, show_titles(c, anime), cache, t, tc)]
            if any(j != 'eyes' for j in jobs):   # on-model references: gallery + castmates' portraits
                from render_pop import same_series as _ss
                urls += [x['url'] for x in (cache.get(f"wikiimgs:{c['id']}") or []) if x['url'] != w.get('url')][:2]
                urls += [o['image'] for o in by_fav if o['id'] != c['id'] and _ss(o['anime'], c['anime'])][:5]
        except Exception as e:  # noqa: BLE001
            log(f"  prefetch {c['name']}: {str(e)[:60]}")
        for u in dict.fromkeys(urls):
            if u and not os.path.exists(_img_path(u)):
                try:
                    load_img(u)
                    n_img += 1
                except Exception:  # noqa: BLE001
                    pass
        if n % 50 == 0:
            cache.save()
            log(f'prefetch {n}/{len(todo)}: {n_img} pictures')
    cache.save()
    log(f'prefetch done: {n_img} pictures downloaded')


def regate_all():
    """Final safety pass over every stored item with the CURRENT gate, colour rule, age class and appearance overrides:
    failures are marked dropped (not rebuilt), then the pools are merged again. Run before publishing the pools."""
    cache = Cache()
    path = EXTRAS
    ex = json.load(open(path, encoding='utf-8'))
    chars = {c['id']: c for c in json.load(open(os.path.join(ASSETS, 'anime-characters.json'), encoding='utf-8'))['characters']}
    anime = anime_list()
    from collections import Counter
    why_n = Counter()

    def check(pics, cls, outfit=False):
        eff = cls
        for j, (im, framing, mono) in enumerate(pics):
            if colourful(im) < mono:
                return 'black-and-white art'
            t = pv.tag(im)
            if j == 0:
                eff = pv.effective_class(t, cls)
            if outfit and eff not in pv.ADULT_CLASSES:
                t = {'rating': t['rating'], 'tags': {k: v for k, v in t['tags'].items() if k not in ('lower_body', 'head_out_of_frame', 'cowboy_shot')}}
            ok, why = pv.gate(t, eff, framing=framing if eff not in pv.ADULT_CLASSES else None)
            if not ok:
                return why
        return None

    for fmt in ('eyes', 'silhouette', 'outfit'):
        for it in ex.get(fmt, []):
            cid = it['id']
            cls = rule_class(chars[cid], cache, anime)
            d = os.path.join(POP2, {'eyes': 'eyes', 'silhouette': 'sil', 'outfit': 'outfit'}[fmt])
            if fmt == 'eyes':
                pics = [(Image.open(os.path.join(d, f'{cid}_p.jpg')), ['portrait', 'upper_body', 'close-up', 'face', 'full_body', 'cowboy_shot'], MONO_PIC),
                        (Image.open(os.path.join(d, f'{cid}.jpg')), None, MONO_CROP)]
            else:
                pics = [(Image.open(os.path.join(d, f'{cid}.jpg')), ['full_body', 'upper_body', 'portrait', 'cowboy_shot_x'] if fmt == 'silhouette' else None, MONO_PIC)]
            bad = 'outfit: not a confirmed adult (or override strict)' if fmt == 'outfit' and cls not in pv.ADULT_CLASSES else check(pics, cls, fmt == 'outfit')
            if bad:
                why_n[f'{fmt}: {bad[:40]}'] += 1
                log(f'regate {fmt} {cid} {it["name"]}: {bad}')
                if fmt == 'eyes':
                    cache.d[f'eye1:{cid}'] = {'dropped': 'regate: ' + bad}
                else:
                    v = dict(cache.d.get(f'so:{cid}:v2') or {})
                    v[fmt] = {'dropped': 'regate: ' + bad}
                    cache.d[f'so:{cid}:v2'] = v
    # themed art pools: the theme's age rule (beach = adults without an override), one figure, the gate (colour + WD14)
    all_th = themes()
    for tid, pools in (ex.get('themes') or {}).items():
        th = all_th.get(tid) or {}
        if th.get('select'):
            continue   # selector themes reuse the base items checked above
        for fmt, items in pools.items():
            for it in items:
                if not it.get('dir'):
                    continue
                cid = it['id']
                cls = theme_class(chars[cid], th, cache, anime)
                p = os.path.join(POP2, it['dir'], f'{cid}.jpg')
                if cls is None:
                    bad = f'theme age rule ({th.get("age_rule")})'
                elif fmt == 'outfit' and cls not in pv.ADULT_CLASSES:
                    bad = 'outfit: not a confirmed adult (or override strict)'
                elif not os.path.exists(p):
                    bad = 'file missing'
                else:
                    im = Image.open(p).convert('RGB')
                    bad = None
                    if fmt in ('character', 'silhouette'):
                        tg = pv.tag(im)['tags']
                        if tg.get('solo', 0) < 0.5 or max(tg.get(k, 0) for k in ('multiple_girls', 'multiple_boys', '2girls', '2boys', '1other', 'multiple_views')) >= 0.3:
                            bad = 'not a single figure'
                    if not bad:
                        fr = {'character': ['portrait', 'upper_body', 'full_body', 'cowboy_shot'],
                              'silhouette': ['full_body', 'upper_body', 'portrait', 'cowboy_shot_x']}.get(fmt)
                        bad = check([(im, fr, MONO_PIC)], cls, fmt == 'outfit')
                if bad:
                    why_n[f'theme {tid} {fmt}: {bad[:40]}'] += 1
                    log(f'regate theme {tid} {fmt} {cid} {it["name"]}: {bad}')
                    v = dict(cache.d.get(f'theme:{tid}:{cid}') or {})
                    v[fmt] = {'dropped': 'regate: ' + bad}
                    cache.d[f'theme:{tid}:{cid}'] = v
    cache.save()
    log('regate:', dict(why_n))
    finish_v2(cache)
    for tid in (ex.get('themes') or {}):
        finish_theme(tid, cache)


def compact(max_kb=110, portrait_kb=80):
    """Keep the repo small: recompress stored pictures above a size cap (quality steps down, then 85% size)."""
    n = saved = 0
    for root, _, files in os.walk(POP2):
        for f in files:
            if not f.endswith('.jpg'):
                continue
            p = os.path.join(root, f)
            cap = (portrait_kb if f.endswith('_p.jpg') else max_kb) * 1024
            size = os.path.getsize(p)
            if size <= cap:
                continue
            im = Image.open(p).convert('RGB')
            for qy in (78, 72, 66, 60):
                buf = io.BytesIO()
                im.save(buf, 'JPEG', quality=qy, optimize=True, progressive=True)
                if buf.tell() <= cap:
                    break
                if qy == 60:
                    im = im.resize((int(im.size[0] * 0.85), int(im.size[1] * 0.85)), Image.LANCZOS)
                    buf = io.BytesIO()
                    im.save(buf, 'JPEG', quality=72, optimize=True, progressive=True)
            if buf.tell() < size:
                open(p, 'wb').write(buf.getvalue())
                n += 1
                saved += size - buf.tell()
    log(f'compact: {n} pictures recompressed, {saved / 1e6:.1f} MB saved')
