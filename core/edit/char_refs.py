#!/usr/bin/env python3
"""Multi-image character reference bank for the ranking compilation's character check (2026-10-25).

One AniList picture per character was too weak a reference: Yuta (white uniform / Gojo-body scenes) and the romance
leads of Horimiya / Blue Box failed although the edits showed them. The bank gathers MANY pictures per character:
  1. the AniList art (quiz pool core/quiz/assets/anime-characters.json, or an AniList GraphQL lookup by name, cached);
  2. our quiz-pool images of that character (core/quiz/assets/pop2: eyes portrait / outfit / silhouette source art /
     theme art, keyed by the AniList id);
  3. the show's Fandom wiki: the character page + its /Gallery subpage (anime screenshots and official art), only files
     whose NAME carries the character's name (a gallery file named after someone else never enters).
Each picture gives at most ONE face crop: pictures with two similar-sized faces are dropped (group shots would teach the
bank the wrong face). Then, precision first:
  * the WD tagger vetoes a picture where it sees another known character and not this one;
  * a consistency filter drops crops whose median similarity to the rest of the bank is an outlier (wrong file, a
    chibi, a manga panel of someone else).
Matching (Bank.score): per candidate the mean of the top-3 crop similarities; the frame votes only with a clear margin.

    python core/edit/char_refs.py build --name "Yuta Okkotsu" --anime "JUJUTSU KAISEN" --wiki jujutsu-kaisen --sheet out.jpg
"""
import hashlib
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
ASSETS = os.path.join(ROOT, 'core', 'quiz', 'assets')
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36'
MAX_REFS = 24
WIKI_FILES = 40            # wiki files looked at per character
BAD_FILE = re.compile(r'icon|logo|signature|map|banner|chibi|sprite|emoji|avatar|thumb|cosplay|figure|merch|plush|voice|seiyuu|'
                      r'\.gif|\.svg|\.webm|sticker|stamp|nendoroid|card|volume|cover|vs\.?_|_and_|_&_|group|team|family', re.I)


def log(*a):
    print(time.strftime('%H:%M:%S'), '[char_refs]', *a, file=sys.stderr, flush=True)


BOT_UA = 'ZeroYenOtakuRanking/1.0 (https://github.com/drcompilationyt7-alt/ChinaWeatherReader)'   # wikia's CDN 403s browser UAs


def http(url, binary=False, data=None, tries=3, timeout=30):
    ua = BOT_UA if ('fandom.com' in url or 'wikia' in url) else UA
    for k in range(tries):
        try:
            req = urllib.request.Request(url, data=data, headers={'User-Agent': ua, 'Content-Type': 'application/json',
                                                                  'Accept': 'application/json'} if data else {'User-Agent': ua})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = r.read()
            return d if binary else json.loads(d)
        except Exception as e:  # noqa: BLE001
            if k == tries - 1:
                raise RuntimeError(f'{url[:80]}: {str(e)[:80]}')
            time.sleep(2 * (k + 1))


COMMON = set('''a an the and or of in on at to by for with from vs his her their its is are was be as into after before during
someone young old adult child little big new final first second third last full
anime manga prequel movie film season episode ep part arc chapter official key visual art artwork profile portrait close up closeup
face eyes smile smiling angry sad crying fight fighting battle attack attacks kills kill defeats defeated meets meeting appears
appearance reveals revealed declares tells asks says using uses use form mode true power powers ability technique domain
expansion cursed energy technique sword ring school uniform outfit casual summer winter night day image picture screenshot
character design color colour version alt alternate design sketch infobox gallery'''.split())


def other_names(filename, name):
    """Capitalised words of a wiki file name that are neither this character's name nor common words: someone else is in
    the picture ("Maki and Yuta exploring a school", "Ryu Ishigori defending against Yuta") -> the file is skipped."""
    own = set(name_tokens(name)) | {t[:-2] for t in name_tokens(name)}
    words = re.findall(r"[A-Z][a-zA-Z'\-]+", re.sub(r'^File:', '', filename).rsplit('.', 1)[0])
    out = [w for w in re.findall(r'[A-Za-z]+', filename) if w.lower() in ('everyone', 'everybody', 'crowd', 'people', 'group', 'team', 'class', 'students', 'family', 'others')]
    for w in words:
        lw = re.sub(r"'s$", '', w.lower())
        if lw in own or lw in COMMON or any(lw.startswith(o) for o in own if len(o) >= 4):
            continue
        out.append(w)
    return out


def key_of(name):
    return re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_')


def name_tokens(name):
    return [t for t in re.split(r'[^a-z0-9]+', name.lower()) if len(t) >= 3]


# ------------------------------------------------------------------ sources

_POOL = [None]


def pool_chars():
    if _POOL[0] is None:
        _POOL[0] = json.load(open(os.path.join(ASSETS, 'anime-characters.json'), encoding='utf-8'))['characters']
    return _POOL[0]


def anilist_char(name, cache_dir):
    """{'id','name','image','favourites','gender','anime'} from the quiz pool, else AniList (cached)."""
    for c in pool_chars():
        if c['name'].lower() == name.lower():
            return c
    p = os.path.join(cache_dir, 'anilist', key_of(name) + '.json')
    if os.path.exists(p):
        return json.load(open(p, encoding='utf-8')) or None
    q = ('query($s:String){Character(search:$s){id name{full} image{large} favourites gender age '
         'media(sort:POPULARITY_DESC,perPage:1){nodes{title{english romaji}}}}}')
    try:
        d = http('https://graphql.anilist.co', data=json.dumps({'query': q, 'variables': {'s': name}}).encode())['data']['Character']
        m = ((d.get('media') or {}).get('nodes') or [{}])[0].get('title') or {}
        out = {'id': d['id'], 'name': d['name']['full'], 'image': d['image']['large'], 'favourites': d.get('favourites'),
               'gender': d.get('gender'), 'age': d.get('age'), 'anime': m.get('english') or m.get('romaji'), 'source': 'anilist_api'}
    except Exception as e:  # noqa: BLE001
        log(f'AniList lookup failed for {name}: {e}')
        out = None
    os.makedirs(os.path.dirname(p), exist_ok=True)
    json.dump(out, open(p, 'w', encoding='utf-8'), ensure_ascii=False)
    time.sleep(1.0)
    return out


def quiz_images(cid):
    """Our quiz-pool pictures of this AniList id (portrait / outfit / theme art). Silhouettes are black shapes: skipped."""
    out = []
    pop2 = os.path.join(ASSETS, 'pop2')
    for rel in (f'eyes/{cid}_p.jpg', f'outfit/{cid}.jpg', f'outfit/{cid}_full.jpg'):
        p = os.path.join(pop2, rel)
        if os.path.exists(p):
            out.append((p, 'quiz:' + rel.split('/')[0]))
    th = os.path.join(pop2, 'theme')
    if os.path.isdir(th):
        for theme in os.listdir(th):
            p = os.path.join(th, theme, 'character', f'{cid}.jpg')
            if os.path.exists(p):
                out.append((p, f'quiz:theme/{theme}'))
    try:
        ex = json.load(open(os.path.join(ASSETS, 'character-extras.json'), encoding='utf-8'))
        for kind in ('silhouette', 'outfit', 'eyes'):
            for it in ex.get(kind, []):
                if it.get('id') == cid:
                    for k in ('image', 'full', 'portrait'):
                        u = it.get(k)
                        if isinstance(u, str) and u.startswith('http'):
                            out.append((u, f'quiz:{kind}_{k}'))
    except Exception:  # noqa: BLE001
        pass
    return out


def wiki_images(wiki, name, cache_dir, limit=WIKI_FILES, alias=None):
    """Files on the character's wiki page + /Gallery whose file name carries the character's name (AniList spelling or the
    entry's own spelling: "Touji" / "Toji")."""
    p = os.path.join(cache_dir, 'wiki', f'{wiki}__{key_of(name)}.json')
    if os.path.exists(p):
        return json.load(open(p, encoding='utf-8'))
    out = []
    try:
        api = f'https://{wiki}.fandom.com/api.php?'
        title = None
        # AniList romanises long vowels ("Kyouko", "Touji", "Yuuta"); wikis mostly do not ("Kyoko", "Toji")
        names = list(dict.fromkeys([name] + ([alias] if alias else []) + [re.sub(r'(?<=[aeiou])u|(?<=o)o', '', name, flags=re.I)]))
        toks = {t for nm in names for t in name_tokens(nm)}
        for nm in names:
            hits = http(api + urllib.parse.urlencode({'action': 'query', 'list': 'search', 'srsearch': nm, 'srlimit': 5,
                                                       'srnamespace': 0, 'format': 'json'}))['query']['search']
            for h in hits:
                ht = set(name_tokens(h['title']))
                if '/' not in h['title'] and ht and (ht & toks) and len(ht - toks) <= 1:
                    title = h['title']
                    break
            if title:
                break
        if title:
            pages = http(api + urllib.parse.urlencode({'action': 'query', 'titles': f'{title}|{title}/Gallery|{title}/Image Gallery',
                                                        'prop': 'images', 'imlimit': 200, 'format': 'json'}))['query']['pages']
            files = []
            for pg in pages.values():
                for im in pg.get('images') or []:
                    f = im['title']
                    ft = set(name_tokens(f))
                    if (re.search(r'\.(png|jpe?g|webp)$', f, re.I) and not BAD_FILE.search(f) and (ft & toks)
                            and not other_names(f, ' '.join(sorted(toks))) and f not in files):
                        files.append(f)
            # anime screenshots first ("anime", "episode", "ep"), then the rest
            files.sort(key=lambda f: (not re.search(r'anime|episode|\bep\b|_ep|season', f, re.I), f))
            files = files[:limit]
            for k in range(0, len(files), 20):
                q = urllib.parse.urlencode({'action': 'query', 'titles': '|'.join(files[k:k + 20]), 'prop': 'imageinfo',
                                            'iiprop': 'url|size', 'iiurlwidth': 640, 'format': 'json'})
                for pg in http(api + q)['query']['pages'].values():
                    ii = (pg.get('imageinfo') or [{}])[0]
                    if ii.get('url') and (ii.get('width') or 0) >= 200 and (ii.get('height') or 0) >= 200:
                        out.append({'url': ii.get('thumburl') or ii['url'], 'file': pg['title'], 'page': title})
                time.sleep(0.3)
    except Exception as e:  # noqa: BLE001
        log(f'wiki {wiki} / {name}: {e}')
    os.makedirs(os.path.dirname(p), exist_ok=True)
    json.dump(out, open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=0)
    return out


def load_image(url, cache_dir):
    if os.path.exists(url):
        return cv2.imread(url, cv2.IMREAD_COLOR)
    p = os.path.join(cache_dir, 'img', hashlib.sha1(url.encode()).hexdigest()[:24] + '.img')
    if not os.path.exists(p):
        try:
            data = http(url, binary=True)
        except Exception as e:  # noqa: BLE001
            log(f'image {url[:70]}: {e}')
            return None
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, 'wb').write(data)
    im = cv2.imdecode(np.fromfile(p, np.uint8), cv2.IMREAD_COLOR)
    if im is None:            # webp / png with alpha via PIL
        try:
            from PIL import Image
            im = cv2.cvtColor(np.array(Image.open(p).convert('RGB')), cv2.COLOR_RGB2BGR)
        except Exception:  # noqa: BLE001
            return None
    return im


# ------------------------------------------------------------------ bank

class Bank:
    """Reference crops + embeddings per character. `judge` gives .sig (SigLIP), .faces (anime face detector) and
    .tagger() (WD tagger or None)."""

    def __init__(self, judge, cache_dir):
        self.j, self.dir = judge, os.path.join(cache_dir, 'char_refs')
        os.makedirs(self.dir, exist_ok=True)
        self.mem = {}

    def one_face(self, img):
        """The single dominant face crop of a picture, or None (no face / a group shot)."""
        import library as L
        if img is None or self.j.faces is None:
            return None
        h, w = img.shape[:2]
        if max(h, w) > 1280:
            s = 1280 / max(h, w)
            img = cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
        boxes = [b for b in self.j.faces(img) if b[2] * b[3] >= 0.002]
        if not boxes:
            return None
        boxes.sort(key=lambda b: -b[2] * b[3])
        if len(boxes) > 1 and boxes[1][2] * boxes[1][3] > 0.4 * boxes[0][2] * boxes[0][3]:
            return None
        return L.crop_face(img, boxes[0][:4])

    def build(self, name, anime='', wiki=None, sheet=None, alias=None):
        """-> {'name', 'emb' (n x d), 'src': [..], 'n_raw'}; cached as npz + json."""
        k = key_of(name)
        if k in self.mem:
            return self.mem[k]
        npz, meta = os.path.join(self.dir, k + '.npz'), os.path.join(self.dir, k + '.json')
        if os.path.exists(npz) and os.path.exists(meta):
            m = json.load(open(meta, encoding='utf-8'))
            if m.get('wiki') == wiki:
                m['emb'] = np.load(npz)['emb']
                self.mem[k] = m
                return m
        c = anilist_char(name, os.path.dirname(self.dir)) or {}
        cands = []
        if c.get('image'):
            cands.append((c['image'], 'anilist'))
        if c.get('id'):
            cands += quiz_images(c['id'])
        if wiki:
            cands += [(w['url'], 'wiki:' + w['file']) for w in wiki_images(wiki, name, os.path.dirname(self.dir), alias=alias)]
        seen, crops, srcs = set(), [], []
        tg = self.j.tagger()
        own_tag = self.j.tag_for(name) if tg else None
        n_raw = 0
        for url, src in cands:
            if url in seen:
                continue
            seen.add(url)
            im = load_image(url, os.path.dirname(self.dir))
            if im is None:
                continue
            n_raw += 1
            cr = self.one_face(im)
            if cr is None or min(cr.shape[:2]) < 40:
                continue
            if tg is not None and src != 'anilist':
                ch = tg.chars(tg(im), 0.6)
                if ch and (own_tag not in ch):
                    continue                      # the tagger sees someone else (and not this character)
            crops.append(cr)
            srcs.append(src)
        emb = self.j.sig.images(crops) if crops else np.zeros((0, 768), np.float32)
        keep = list(range(len(crops)))
        if len(crops) >= 4:
            S = emb @ emb.T
            np.fill_diagonal(S, np.nan)
            med = np.nanmedian(S, 1)
            ai = [i for i, s in enumerate(srcs) if s == 'anilist']
            base = float(np.nanmedian(med))
            # outliers: far below the bank's typical self-similarity, or unlike the AniList anchor
            keep = [i for i in range(len(crops)) if med[i] >= base - 0.08
                    and (not ai or i in ai or S[i, ai[0]] >= float(np.nanmedian(S[ai[0]])) - 0.12)]
        keep = keep[:MAX_REFS] if 'anilist' not in srcs else ([i for i in keep if srcs[i] == 'anilist']
                                                              + [i for i in keep if srcs[i] != 'anilist'])[:MAX_REFS]
        m = {'name': name, 'anime': anime or c.get('anime'), 'wiki': wiki, 'id': c.get('id'), 'gender': c.get('gender'),
             'favourites': c.get('favourites'), 'n_raw': n_raw, 'n_faces': len(crops), 'n': len(keep),
             'src': [srcs[i] for i in keep], 'built': time.strftime('%Y-%m-%d')}
        np.savez_compressed(npz, emb=emb[keep] if len(keep) else emb[:0])
        json.dump(m, open(meta, 'w', encoding='utf-8'), ensure_ascii=False, indent=0)
        sheet = sheet or os.path.join(self.dir, k + '.jpg')          # always: the bank is reviewable by eye
        if sheet and keep:
            tiles = [cv2.resize(crops[i], (128, 128)) for i in keep]
            while len(tiles) % 8:
                tiles.append(np.zeros((128, 128, 3), np.uint8))
            cv2.imwrite(sheet, np.vstack([np.hstack(tiles[r:r + 8]) for r in range(0, len(tiles), 8)]))
        m['emb'] = emb[keep] if len(keep) else emb[:0]
        self.mem[k] = m
        log(f'{name}: {n_raw} pictures -> {len(crops)} single-face crops -> {len(keep)} refs')
        return m

    @staticmethod
    def score(crop_emb, ref_emb, top=3):
        """Mean of the top-`top` similarities of each crop to the bank -> (n_crops,)."""
        if not len(ref_emb):
            return np.full(len(crop_emb), -1.0)
        s = crop_emb @ ref_emb.T
        t = min(top, s.shape[1])
        return np.sort(s, 1)[:, -t:].mean(1)


if __name__ == '__main__':
    import argparse
    sys.path.insert(0, HERE)
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['build'])
    ap.add_argument('--name', required=True)
    ap.add_argument('--anime', default='')
    ap.add_argument('--wiki')
    ap.add_argument('--sheet')
    ap.add_argument('--work', default=os.path.join(ROOT, 'output', 'ranking-proto'))
    a = ap.parse_args()
    import ranking_compilation as RC
    j = RC.Judge(os.path.join(a.work, 'cache'))
    m = Bank(j, os.path.join(a.work, 'cache')).build(a.name, a.anime, a.wiki, a.sheet)
    print(json.dumps({k: v for k, v in m.items() if k != 'emb'}, ensure_ascii=False))
