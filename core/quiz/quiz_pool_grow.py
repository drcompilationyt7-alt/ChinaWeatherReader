"""
Nightly quiz pool mining for the Asian Pop Quiz (owner 2026-10-20), same staging + review pattern as core/pools/slowmo_grow.py:

    GitHub (.github/workflows/quiz-pool-mine.yml, ~03:30 UTC, off with the repo variable QUIZ_POOL_MINE=off)
        mine     candidates per category (rotating item / query lists, a per-night quota, deduped against the live pools,
                 the staged ones and everything ever rejected) + thumbnails + per-category contact sheets
        publish  one tar per shard (quiz-cand-<date>-<shard>-n<N>.tar) + the merged state -> release `edit-pools-staging`
    owner PC / Claude, each session
        pull-staged          download the new tars, number every candidate, write review/<category>.jpg sheets + review/index.json
        import-staged --reject N,N,...   everything not rejected joins the live pools (core/quiz/assets/*.json, pop2/ files);
                             the rejected keys are remembered (core/quiz/assets/quiz-pool-rejects.json) and never mined again
        push-manifests       upload the reject list + the query log to the staging release (the miner also reads the repo copy)

Nothing reaches the live quiz without that review. Categories (QUOTA = candidates per night):
    theme_art   anime theme art (beach / halloween / festival / glasses / villains / maid / winter / valentine / gym) through
                scripts/build-pop-media.py --phase theme: official / wiki / game / promo art and on-model Danbooru g/s fan art,
                WD14 + on-model + age gates (strict list / minors / unknown age: the strict gate), no figurines   [shard "art"]
    geo         city photos (Wikimedia Commons, free licence, author credited): China tier-1 + new tier-1, Korean, Japanese cities
    nightcity   night skyline photos of the same cities -> the new "guess the city by night" format
    cuisine     Chinese / Asian dishes (Commons photo, dish + country) -> the new "guess the dish" format
    kpop_song   K-pop songs for "guess the song" (LLM-proposed + fact-checked; title / artist / year only, NEVER lyrics)
    kpop_idol   K-pop idols (Wikidata members of K-pop groups with a Commons photo, adults only, licence + author kept)
    vtuber      VTubers (official portraits from the Virtual YouTuber Wiki, as the live vtuber format)
    emoji       pre-generated emoji puzzles (anime, K-pop songs, the season's edition), fact-checked; no on-demand writing
    trivia      pre-generated multiple-choice questions whose reveal picture was fetched and checked here
    wyr_food    would-you-rather FOOD pairs ("eat X or Y") with a real photo for BOTH options
    wyr_travel  would-you-rather TRAVEL pairs ("go to X or Y") with a real photo for BOTH options

Content rules (hard): adults may have swimsuits / suggestive art; always blocked: nudity, underwear, explicit, censored images;
strict list + minors / unknown age: the strict gate (theme art); IRL: free-licence photos with attribution, no private people as
the subject (Claude checks the sheets); no lyrics; no credentials anywhere in the output.

    python core/quiz/quiz_pool_grow.py selftest
    python core/quiz/quiz_pool_grow.py mine --shard light|art --work work [--state st.json] [--budget-min 40] [--dry-llm]
    python core/quiz/quiz_pool_grow.py publish --artifacts shards --out staged [--state st.json] [--dry]
    python core/quiz/quiz_pool_grow.py need
    python core/quiz/quiz_pool_grow.py pull-staged [--dir output/quiz-pool-review]
    python core/quiz/quiz_pool_grow.py import-staged [--dir ...] [--reject 3,7,12] [--delete-remote]
    python core/quiz/quiz_pool_grow.py push-manifests
"""
import argparse
import datetime
import glob
import importlib.util
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
ASSETS = os.path.join(HERE, 'assets')
sys.path.insert(0, HERE)

RELEASE = os.environ.get('QUIZ_STAGING_RELEASE', 'edit-pools-staging')
PREFIX = 'quiz-cand-'
STATE_ASSET = 'quiz-grow-state.json'
REJECTS_FILE = os.path.join(ASSETS, 'quiz-pool-rejects.json')
KEEP_DAYS = 30            # an item tried without success comes back after this many nights
CATS = ('theme_art', 'geo', 'nightcity', 'cuisine', 'kpop_song', 'kpop_idol', 'vtuber', 'emoji', 'trivia', 'wyr_food', 'wyr_travel')
# nightly: light + art; BACKFILL (workflow input mode=backfill, QUIZ_MINE_SCALE=4): the light categories split over 3 jobs and
# 3 art jobs on different themes, so the first review gets a big batch
SHARDS = {'light': [c for c in CATS if c != 'theme_art'], 'art': ['theme_art'],
          'photos': ['geo', 'nightcity', 'cuisine'], 'people': ['kpop_idol', 'vtuber'],
          'llm': ['emoji', 'trivia', 'wyr_food', 'wyr_travel', 'kpop_song'],
          'art0': ['theme_art'], 'art1': ['theme_art'], 'art2': ['theme_art']}
# owner 2026-10-21 "we have to up pool": per-night candidates, weighted to the thinnest pools (K-pop, VTuber, IRL geo / night /
# food, theme costumes); ~40+ should survive review. x QUIZ_MINE_SCALE (backfill: 4).
SCALE = max(1.0, float(os.environ.get('QUIZ_MINE_SCALE') or 1))
QUOTA = {k: int(round(v * SCALE)) for k, v in {
    'theme_art': 20, 'geo': 10, 'nightcity': 12, 'cuisine': 12, 'kpop_song': 8, 'kpop_idol': 10, 'vtuber': 8,
    'emoji': 12, 'trivia': 16, 'wyr_food': 6, 'wyr_travel': 6}.items()}
NEAR_DUP_BITS = 10        # dHash distance: at or below = the same picture
ART_THEMES = ['beach', 'halloween', 'festival', 'glasses', 'villains', 'maid', 'winter', 'valentine', 'gym']

# ── item tables (rotated nightly) ──
# (name, iso2, difficulty): China tier-1 + new tier-1, Korea, Japan
CITIES = [
    ('Beijing', 'CN', 1), ('Shanghai', 'CN', 1), ('Guangzhou', 'CN', 2), ('Shenzhen', 'CN', 2),
    ('Chengdu', 'CN', 2), ('Chongqing', 'CN', 2), ('Hangzhou', 'CN', 2), ('Wuhan', 'CN', 3), ("Xi'an", 'CN', 2), ('Suzhou', 'CN', 3),
    ('Zhengzhou', 'CN', 3), ('Nanjing', 'CN', 3), ('Tianjin', 'CN', 3), ('Changsha', 'CN', 3), ('Dongguan', 'CN', 3), ('Ningbo', 'CN', 3),
    ('Foshan', 'CN', 3), ('Hefei', 'CN', 3), ('Qingdao', 'CN', 3), ('Hong Kong', 'CN', 1), ('Xiamen', 'CN', 3), ('Harbin', 'CN', 3),
    ('Seoul', 'KR', 1), ('Busan', 'KR', 2), ('Incheon', 'KR', 2), ('Daegu', 'KR', 3), ('Daejeon', 'KR', 3), ('Gwangju', 'KR', 3),
    ('Ulsan', 'KR', 3), ('Suwon', 'KR', 3), ('Jeonju', 'KR', 3), ('Gyeongju', 'KR', 3), ('Jeju', 'KR', 2),
    ('Tokyo', 'JP', 1), ('Osaka', 'JP', 1), ('Kyoto', 'JP', 1), ('Yokohama', 'JP', 2), ('Nagoya', 'JP', 2), ('Sapporo', 'JP', 2),
    ('Fukuoka', 'JP', 2), ('Kobe', 'JP', 2), ('Hiroshima', 'JP', 2), ('Sendai', 'JP', 3), ('Nara', 'JP', 2), ('Kanazawa', 'JP', 3),
    ('Nagasaki', 'JP', 3), ('Naha', 'JP', 3), ('Hakodate', 'JP', 3),
]
# several photos per city / dish (owner 2026-10-21: the reuse gap is per PICTURE, so the same city may come back with another photo)
GEO_TERMS = ['skyline', 'skyline panorama', 'cityscape', 'cityscape panorama', 'skyline view', 'skyline from river', 'skyline daytime']
NIGHT_TERMS = ['skyline at night', 'night view', 'night skyline', 'at night', 'night panorama', 'city lights night']
DISH_TERMS = ['{d}', '{d} dish', '{d} food', '{d} plate', '{d} served']
# (dish, iso2 of its home country, country name, difficulty)
DISHES = [
    ('Peking duck', 'CN', 'China', 1), ('Xiaolongbao', 'CN', 'China', 2), ('Mapo tofu', 'CN', 'China', 2), ('Kung Pao chicken', 'CN', 'China', 2),
    ('Char siu', 'CN', 'China', 2), ('Har gow', 'CN', 'China', 3), ('Jiaozi', 'CN', 'China', 2), ('Zongzi', 'CN', 'China', 3),
    ('Mooncake', 'CN', 'China', 2), ('Baozi', 'CN', 'China', 2), ('Chow mein', 'CN', 'China', 1), ('Dan dan noodles', 'CN', 'China', 3),
    ('Lanzhou beef noodles', 'CN', 'China', 3), ('Biangbiang noodles', 'CN', 'China', 3), ('Roujiamo', 'CN', 'China', 3),
    ('Jianbing', 'CN', 'China', 3), ('Stinky tofu', 'CN', 'China', 2), ('Sweet and sour pork', 'CN', 'China', 1), ('Egg tart', 'CN', 'China', 2),
    ('Wonton noodles', 'CN', 'China', 2), ('Congee', 'CN', 'China', 2), ('Tangyuan', 'CN', 'China', 3), ('Hot pot', 'CN', 'China', 1),
    ('Gong bao', 'CN', 'China', 3), ('Shengjian mantou', 'CN', 'China', 3), ('Cheung fun', 'CN', 'China', 3), ('Bubble tea', 'TW', 'Taiwan', 1),
    ('Bibimbap', 'KR', 'South Korea', 1), ('Kimchi', 'KR', 'South Korea', 1), ('Tteokbokki', 'KR', 'South Korea', 2), ('Bulgogi', 'KR', 'South Korea', 2),
    ('Samgyeopsal', 'KR', 'South Korea', 2), ('Japchae', 'KR', 'South Korea', 3), ('Gimbap', 'KR', 'South Korea', 2), ('Sundubu-jjigae', 'KR', 'South Korea', 3),
    ('Naengmyeon', 'KR', 'South Korea', 3), ('Hotteok', 'KR', 'South Korea', 3), ('Korean fried chicken', 'KR', 'South Korea', 1),
    ('Sushi', 'JP', 'Japan', 1), ('Ramen', 'JP', 'Japan', 1), ('Takoyaki', 'JP', 'Japan', 2), ('Okonomiyaki', 'JP', 'Japan', 2),
    ('Tempura', 'JP', 'Japan', 1), ('Udon', 'JP', 'Japan', 2), ('Onigiri', 'JP', 'Japan', 2), ('Tonkatsu', 'JP', 'Japan', 2),
    ('Mochi', 'JP', 'Japan', 2), ('Taiyaki', 'JP', 'Japan', 3), ('Yakitori', 'JP', 'Japan', 2), ('Gyoza', 'JP', 'Japan', 2),
    ('Pho', 'VN', 'Vietnam', 1), ('Banh mi', 'VN', 'Vietnam', 1), ('Pad thai', 'TH', 'Thailand', 1), ('Tom yum', 'TH', 'Thailand', 2),
    ('Laksa', 'MY', 'Malaysia', 2), ('Nasi lemak', 'MY', 'Malaysia', 2), ('Satay', 'ID', 'Indonesia', 2), ('Rendang', 'ID', 'Indonesia', 3),
    ('Som tam', 'TH', 'Thailand', 3), ('Mango sticky rice', 'TH', 'Thailand', 2), ('Halo-halo', 'PH', 'Philippines', 3),
    ('Hainanese chicken rice', 'SG', 'Singapore', 2), ('Nasi goreng', 'ID', 'Indonesia', 2), ('Bun cha', 'VN', 'Vietnam', 3),
]
# (wiki page, agency, difficulty): official portraits from the Virtual YouTuber Wiki; checked against the live vtubers.json
VTUBER_NAMES = [
    ('Houshou Marine', 'hololive JP', 1), ('Tokoyami Towa', 'hololive JP', 2), ('Yukihana Lamy', 'hololive JP', 2),
    ('Shiranui Flare', 'hololive JP', 2), ('Shirogane Noel', 'hololive JP', 2), ('Natsuiro Matsuri', 'hololive JP', 2),
    ('Aki Rosenthal', 'hololive JP', 3), ('Yozora Mel', 'hololive JP', 3), ('Murasaki Shion', 'hololive JP', 2),
    ('Yuzuki Choco', 'hololive JP', 3), ('Tsunomaki Watame', 'hololive JP', 2), ('Kiryu Coco', 'hololive JP', 2),
    ('Sakamata Chloe', 'hololive JP', 3), ('Hiodoshi Ao', 'hololive JP', 3), ('Otonose Kanade', 'hololive JP', 3),
    ('Ichijou Ririka', 'hololive JP', 3), ('Juufuutei Raden', 'hololive JP', 3), ('Todoroki Hajime', 'hololive JP', 3),
    ('Roboco', 'hololive JP', 3), ('AZKi', 'hololive JP', 3), ('Tsukumo Sana', 'hololive EN', 2),
    ('Fuwawa Abyssgard', 'hololive EN', 2), ('Mococo Abyssgard', 'hololive EN', 2), ('Anya Melfissa', 'hololive ID', 3),
    ('Mysta Rias', 'NIJISANJI EN', 3), ('Enna Alouette', 'NIJISANJI EN', 3), ('Millie Parfait', 'NIJISANJI EN', 3),
    ('Rosemi Lovelock', 'NIJISANJI EN', 3), ('Selen Tatsuki', 'NIJISANJI EN', 3), ('Petra Gurin', 'NIJISANJI EN', 3),
    ('Fulgur Ovid', 'NIJISANJI EN', 3), ('Sonny Brisko', 'NIJISANJI EN', 3), ('Alban Knox', 'NIJISANJI EN', 3),
    ('Shizuka Rin', 'NIJISANJI', 3), ('Honma Himawari', 'NIJISANJI', 3), ('Kenmochi Toya', 'NIJISANJI', 3),
    ('Ange Katrina', 'NIJISANJI', 3), ('Inui Toko', 'NIJISANJI', 3), ('Fuwa Minato', 'NIJISANJI', 3),
    ('Amano Pikamee', 'VOMS', 2), ('Shylily', 'independent', 2), ('Filian', 'independent', 2), ('Kaguya Luna', 'independent', 3),
    ('Mirai Akari', 'independent', 3), ('Astel Leda', 'HOLOSTARS', 3), ('Kishido Temma', 'HOLOSTARS', 3), ('Yukoku Roberu', 'HOLOSTARS', 3),
    ('Aragami Oga', 'HOLOSTARS', 3),
]
# would-you-rather pairs the LLM may write about (topic in pop-bank: food / cities)
WYR_TOPIC = {'wyr_food': 'food', 'wyr_travel': 'cities'}
TRIVIA_TOPICS = {'kpop': 3, 'vtubers': 3, 'food': 3, 'cities': 3, 'anime': 2}


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def rj(p, d):
    try:
        with open(p, encoding='utf-8') as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return d


def wj(p, d):
    os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
    with open(p, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, indent=1)


def today():
    return time.strftime('%Y-%m-%d', time.gmtime())


def fold(s):
    import unicodedata
    return re.sub(r'[^a-z0-9]+', ' ', unicodedata.normalize('NFKD', str(s or '')).encode('ascii', 'ignore').decode().lower()).strip()


def _bpm():
    """scripts/build-pop-media.py (dash in the name): its Wikidata / Commons / VTuber-wiki helpers."""
    spec = importlib.util.spec_from_file_location('build_pop_media', os.path.join(ROOT, 'scripts', 'build-pop-media.py'))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ── live pools + dedupe keys ──

def live_keys():
    """Every key already in a live pool (or ever rejected): candidates with one of these are skipped."""
    keys = set(rj(REJECTS_FILE, {'rejected': []}).get('rejected', []))
    for c in rj(os.path.join(ASSETS, 'city-photos.json'), {}).get('cities', []):
        keys.add(cand_key('geo', c))
    for c in rj(os.path.join(ASSETS, 'night-city-photos.json'), {}).get('cities', []):
        keys.add(cand_key('nightcity', c))
    for d in rj(os.path.join(ASSETS, 'cuisine.json'), {}).get('dishes', []):
        keys.add(cand_key('cuisine', d))
    for s in rj(os.path.join(ASSETS, 'songs.json'), {}).get('songs', []):
        keys.add(f"kpop_song:{fold(s['title'])}|{fold(s['artist'])}")
    for i in rj(os.path.join(ASSETS, 'kpop-idols.json'), {}).get('idols', []):
        keys.add(f"kpop_idol:{fold(i['name'])}|{fold(i.get('group') or '')}")
        if i.get('wikidata'):
            keys.add(f"kpop_idol:{i['wikidata']}")
    for v in rj(os.path.join(ASSETS, 'vtubers.json'), {}).get('vtubers', []):
        keys.add(f"vtuber:{fold(v['name'])}")
    bank = rj(os.path.join(ASSETS, 'pop-bank.json'), {})
    for it in bank.get('emoji', []):
        keys.add(f"emoji:{fold(it['answer'])}")
    for it in bank.get('trivia', []):
        keys.add(f"trivia:{fold(it['question'])}")
    for it in bank.get('wyr', []):
        keys.add(f"wyr:{fold(it['a'])}|{fold(it['b'])}")
    ex = rj(os.path.join(ASSETS, 'character-extras.json'), {})
    for tid, fmts in (ex.get('themes') or {}).items():
        for fmt, items in fmts.items():
            for it in items:
                keys.add(f"theme_art:{tid}:{fmt}:{it.get('file_id') or it.get('id')}")
    return keys


def photo_id(it):
    """The picture of a city / dish item: its Commons file name (else the image URL)."""
    return fold(it.get('file') or urllib.parse.unquote(str(it.get('page') or it.get('image') or '').rsplit('/', 1)[-1]))[:80]


def cand_key(cat, it):
    if cat in ('geo', 'nightcity'):
        return f"{cat}:{it['iso2']}:{fold(it['name'])}:{photo_id(it)}"
    if cat == 'cuisine':
        return f"cuisine:{fold(it['name'])}:{photo_id(it)}"
    if cat == 'kpop_song':
        return f"kpop_song:{fold(it['title'])}|{fold(it['artist'])}"
    if cat == 'kpop_idol':
        return f"kpop_idol:{it.get('wikidata') or fold(it['name'])}"
    if cat == 'vtuber':
        return f"vtuber:{fold(it['name'])}"
    if cat == 'emoji':
        return f"emoji:{fold(it['answer'])}"
    if cat == 'trivia':
        return f"trivia:{fold(it['question'])}"
    if cat in ('wyr_food', 'wyr_travel'):
        return f"wyr:{fold(it['a'])}|{fold(it['b'])}"
    return f"theme_art:{it['theme']}:{it['fmt']}:{it.get('file_id') or it['id']}"


# ── nightly rotation ──

def rotation(cat, items, st, day=None, seed=''):
    """Tonight's order of a category's table items: not tried in the last KEEP_DAYS days first (seeded by the date),
    then the least recently tried. Deterministic for a given state + day (every shard computes the same list)."""
    day = day or today()
    rng = random.Random(f'{seed}{cat}{day}')
    tried = (st.get('query_log') or {})
    cutoff = (datetime.date.fromisoformat(day) - datetime.timedelta(days=KEEP_DAYS)).isoformat()
    fresh = [x for x in items if tried.get(f'{cat}|{x}', '') <= cutoff]
    rng.shuffle(fresh)
    old = sorted((x for x in items if tried.get(f'{cat}|{x}', '') > cutoff), key=lambda x: (tried[f'{cat}|{x}'], x))
    return fresh + old


def mark_tried(st, cat, item, day=None):
    st.setdefault('query_log', {})[f'{cat}|{item}'] = day or today()


def season_editions(day=None):
    """Theme editions whose date window is open now or opens within 21 days (their emoji are pre-generated ahead)."""
    themes = rj(os.path.join(HERE, 'themes.json'), {}).get('themes', {})
    d = datetime.date.fromisoformat(day or today())
    out = []
    for tid, th in themes.items():
        w = th.get('window')
        if not w:
            continue
        for k in range(0, 22):
            md = (d + datetime.timedelta(days=k)).strftime('%m-%d')
            if w[0] <= md <= w[1]:
                out.append(tid)
                break
    return out


# ── pictures + sheets ──

def thumb(img, path, w=360):
    from PIL import Image
    im = img.convert('RGB')
    im.thumbnail((w, w))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    im.save(path, quality=85)
    return path


_LAT1 = str.maketrans({'‘': "'", '’': "'", '“': '"', '”': '"', '–': '-', '—': '-', '…': '...'})   # the default bitmap font is latin-1 only


def sheet(rows, out, cols=4, cw=360, ch=330):
    """rows: [(thumb path or None, label lines)] -> one jpg; text-only rows get a dark card with their lines."""
    from PIL import Image, ImageDraw
    if not rows:
        return None
    n = len(rows)
    img = Image.new('RGB', (cols * cw, ((n + cols - 1) // cols) * ch), (18, 18, 22))
    d = ImageDraw.Draw(img)
    for k, (p, lines) in enumerate(rows):
        x, y = (k % cols) * cw, (k // cols) * ch
        if p and os.path.exists(p):
            t = Image.open(p).convert('RGB')
            t.thumbnail((cw - 10, ch - 80))
            img.paste(t, (x + (cw - t.size[0]) // 2, y + 5))
            ty = y + ch - 72
        else:
            ty = y + 10
        for j, line in enumerate(lines[:6 if not p else 4]):
            d.text((x + 6, ty + j * 16), str(line)[:58].translate(_LAT1).encode('latin-1', 'replace').decode('latin-1'), fill=(255, 255, 255) if j == 0 else (210, 210, 120))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    img.save(out, quality=82)
    return out


def dhash(img, size=8):
    import numpy as np
    from PIL import Image
    g = np.asarray(img.convert('L').resize((size + 1, size), Image.LANCZOS), dtype=np.int16)
    return int(''.join('1' if b else '0' for b in (g[:, 1:] > g[:, :-1]).flatten()), 2)


def near_dup(h, hashes):
    return any(bin(h ^ x).count('1') <= NEAR_DUP_BITS for x in hashes)


def prefilter(img, file=''):
    """Cheap runner-side checks before anything is staged: big enough, not blank / flat, not text-heavy (a lot of thin
    high-contrast strokes: posters, signs, screenshots, watermark banners), file name not a logo / map / poster."""
    import numpy as np
    from render_pop import picture_ok
    if img is None or min(img.size) < 480 or not picture_ok(img):
        return 'blank / low-res'
    if re.search(r'\b(logo|map|poster|sign|signage|banner|screenshot|watermark|text|menu|ticket|diagram|infographic|collage|montage)\b', fold(file)):
        return 'file name: text / logo / map'
    g = np.asarray(img.convert('L').resize((256, 256)), dtype=np.float32)
    hsv = np.asarray(img.convert('RGB').convert('HSV').resize((128, 128)), dtype=np.float32)
    if hsv[:, :, 1].mean() < 30:
        return 'black-and-white / sepia'
    if g.std() < 32:
        return 'washed out / foggy'
    edges = (np.abs(np.diff(g, axis=1)) > 90).mean() + (np.abs(np.diff(g, axis=0)) > 90).mean()
    if edges > 0.16:
        return f'text-heavy ({edges:.2f})'
    return None


def safe_photo_name(file):
    """Commons file names that suggest people as the subject, protests, ads etc. (media.PHOTO_BAD covers the rest)."""
    return not re.search(r'\b(portrait|selfie|man|woman|girl|boy|people|crowd|family|couple|child|kid|model|cosplay|ad|advert)\b',
                         fold(file))


# ── categories ──

def live_hashes(cat):
    """dHashes of the live pool pictures of a photo category (downloads are cached by media.fetch_image)."""
    import media
    files = {'geo': ('city-photos.json', 'cities'), 'nightcity': ('night-city-photos.json', 'cities'), 'cuisine': ('cuisine.json', 'dishes'),
             'kpop_idol': ('kpop-idols.json', 'idols')}.get(cat)
    out = []
    for it in rj(os.path.join(ASSETS, files[0]), {}).get(files[1], []) if files else []:
        try:
            out.append(dhash(media.fetch_image(it['image'])))
        except Exception:  # noqa: BLE001
            pass
    return out


def mine_commons(cat, st, quota, seen, out_dir, deadline, hashes):
    """geo / nightcity / cuisine: Commons photos for (city or dish) x search term queries; several pictures per city / dish
    over the nights, never a near-duplicate of a live, staged or tonight's picture."""
    import media
    if cat == 'cuisine':
        table = {f'{d[0]}|{t}': (d, t.replace('{d}', d[0])) for d in DISHES for t in DISH_TERMS}
    else:
        table = {f'{c[0]}|{c[1]}|{t}': (c, f'{c[0]} {t}') for c in CITIES for t in (NIGHT_TERMS if cat == 'nightcity' else GEO_TERMS)}
    found, per_item = [], {}
    why = {}
    for qk in rotation(cat, list(table), st):
        if len(found) >= quota or time.time() > deadline:
            break
        row, q = table[qk]
        if per_item.get(row[0], 0) >= 2:      # at most 2 new pictures of one city / dish a night (variety)
            continue
        mark_tried(st, cat, qk)
        if cat == 'cuisine':
            it = {'name': row[0], 'iso2': row[1], 'country': row[2], 'difficulty': row[3]}
        else:
            it = {'name': row[0], 'iso2': row[1], 'difficulty': row[2]}
        img, info = media.commons_photo(q, min_w=900)
        if img is None:
            continue
        file = info.get('file', '')
        name_words = [w for w in fold(row[0]).split() if len(w) >= 3]
        if not all(w in fold(file) for w in name_words) or (not name_words and fold(row[0]).replace(' ', '') not in fold(file).replace(' ', '')):   # 'Naha skyline' found a Dallas skyline: the name must be in the file
            why['wrong place / dish'] = why.get('wrong place / dish', 0) + 1
            continue
        if not safe_photo_name(file):
            why['people / unsafe name'] = why.get('people / unsafe name', 0) + 1
            continue
        if cat == 'geo' and (not re.search(r'skyline|cityscape|panorama|view', file, re.I) or re.search(r'night|dusk|evening', file, re.I)):
            why['not a skyline / view'] = why.get('not a skyline / view', 0) + 1
            continue
        if cat == 'nightcity' and not re.search(r'night|nacht|evening|dusk|lights|夜', file, re.I):
            why['not a night view'] = why.get('not a night view', 0) + 1
            continue
        bad = prefilter(img, file)
        if bad:
            why[bad.split(' (')[0]] = why.get(bad.split(' (')[0], 0) + 1
            continue
        h = dhash(img)
        if near_dup(h, hashes):
            why['near-duplicate'] = why.get('near-duplicate', 0) + 1
            continue
        url = _commons_url(info)
        if not url:
            continue
        it.update({'image': url, 'credit': info['credit'], 'page': info['page'], 'file': file, 'query': q, 'dhash': format(h, '016x'),
                   'mined': today(), **({'night': True} if cat == 'nightcity' else {})})
        k = cand_key(cat, it)
        if k in seen:
            continue
        it['_thumb'] = thumb(img, os.path.join(out_dir, 'thumbs', cat, f'{len(found)}.jpg'))
        found.append(it)
        seen.add(k)
        hashes.append(h)
        per_item[row[0]] = per_item.get(row[0], 0) + 1
    log(f'{cat}: {len(found)} staged; prefilter drops {why}')
    return found


def _commons_url(info):
    """The 1280 px thumb URL of a Commons file (what fetch_image downloads at render time)."""
    import media
    page = info.get('page') or ''
    file = info.get('file') or (urllib.parse.unquote(page.rsplit('File:', 1)[-1]) if 'File:' in page else '')
    if not file:
        return None
    try:
        d = media._commons_json({'action': 'query', 'titles': 'File:' + file, 'prop': 'imageinfo', 'iiprop': 'url', 'iiurlwidth': 1280})
        ii = (next(iter(d['query']['pages'].values())).get('imageinfo') or [{}])[0]
        return ii.get('thumburl') or ii.get('url')
    except Exception:  # noqa: BLE001
        return None


SPARQL_IDOLS = """
SELECT ?p ?pLabel ?gLabel ?img ?dob ?links WHERE {
  ?g wdt:P136 wd:Q213665 .
  ?p wdt:P463 ?g ; wdt:P18 ?img ; wdt:P569 ?dob ; wikibase:sitelinks ?links .
  FILTER(?dob < "%s"^^xsd:dateTime)
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
} LIMIT 3000
"""


def mine_idols(st, quota, seen, out_dir, deadline, hashes):
    """K-pop idols: Wikidata members of K-pop groups with a Commons photo; adults only (born 18+ years ago)."""
    import media
    bpm = _bpm()
    cut = (datetime.date.today() - datetime.timedelta(days=int(18.2 * 365.25))).isoformat() + 'T00:00:00Z'
    url = 'https://query.wikidata.org/sparql?' + urllib.parse.urlencode({'query': SPARQL_IDOLS % cut, 'format': 'json'})
    try:
        rows = bpm.http_json(url, headers={'Accept': 'application/sparql-results+json'})['results']['bindings']
    except Exception as e:  # noqa: BLE001
        log(f'idols: wikidata failed: {str(e)[:100]}')
        return []
    people = {}
    for r in rows:
        qid = r['p']['value'].rsplit('/', 1)[-1]
        name = r['pLabel']['value']
        if re.fullmatch(r'Q\d+', name):
            continue
        p = people.setdefault(qid, {'qid': qid, 'name': name, 'groups': set(), 'img': r['img']['value'], 'links': int(r['links']['value'])})
        p['groups'].add(r['gLabel']['value'])
    # the best-known first (sitelinks), at least 10 language editions: an obscure idol makes an unanswerable round
    order = sorted((p for p in people.values() if p['links'] >= 10), key=lambda p: -p['links'])
    cutoff = (datetime.date.fromisoformat(today()) - datetime.timedelta(days=KEEP_DAYS)).isoformat()
    tried = st.get('query_log') or {}
    names = [p['qid'] for p in order if tried.get(f"kpop_idol|{p['qid']}", '') <= cutoff]
    found = []
    for qid in names[:quota * 6]:
        if len(found) >= quota or time.time() > deadline:
            break
        p = people[qid]
        it = {'name': p['name'], 'group': sorted(p['groups'])[0], 'wikidata': qid}
        if cand_key('kpop_idol', it) in seen or f"kpop_idol:{fold(it['name'])}|{fold(it['group'])}" in seen:
            continue
        mark_tried(st, 'kpop_idol', qid)
        fn = urllib.parse.unquote(p['img'].rsplit('/', 1)[-1])
        try:
            info = bpm.commons_info(fn)
        except Exception:  # noqa: BLE001
            continue
        if not info.get('url') or not media.FREE_LICENCE.match(info.get('license') or ''):
            continue
        try:
            img = media.fetch_image(info['url'])
        except Exception:  # noqa: BLE001
            continue
        if img is None or min(img.size) < 300 or near_dup(dhash(img), hashes):
            continue
        hashes.append(dhash(img))
        it.update({'image': info['url'], 'credit': f"{info['author'] or 'Wikimedia Commons'}, {info['license']}", 'page': info['page'],
                   'difficulty': 1 if p['links'] >= 60 else 2 if p['links'] >= 30 else 3, 'sitelinks': p['links'], 'mined': today()})
        it['_thumb'] = thumb(img, os.path.join(out_dir, 'thumbs', 'kpop_idol', qid + '.jpg'))
        found.append(it)
        seen.add(cand_key('kpop_idol', it))
    return found


def mine_vtubers(st, quota, seen, out_dir, deadline):
    """VTubers: the official portrait from the Virtual YouTuber Wiki (the same rule as the live vtuber pool)."""
    import media
    bpm = _bpm()
    table = {v[0]: v for v in VTUBER_NAMES}
    found = []
    for name in rotation('vtuber', list(table), st):
        if len(found) >= quota or time.time() > deadline:
            break
        if f'vtuber:{fold(name)}' in seen:
            continue
        mark_tried(st, 'vtuber', name)
        time.sleep(1.5)   # the wiki answers 403 to quick bursts
        try:
            d = bpm.vt_query({'action': 'query', 'titles': name, 'redirects': 1, 'prop': 'pageimages|images|revisions', 'piprop': 'name',
                              'imlimit': 500, 'rvprop': 'content', 'rvslots': 'main', 'rvsection': 0})['query']
            p = next(iter(d['pages'].values()))
            if 'missing' in p:
                continue
            text = (p.get('revisions') or [{}])[0].get('slots', {}).get('main', {}).get('*', '')
            m = re.search(r'\|\s*image1\s*=[ \t]*([^\n|<]*)', text)
            infobox = m.group(1).strip() if m and m.group(1).strip() else None
            portraits = [i['title'][5:] for i in p.get('images', []) if 'portrait' in i['title'].lower() and 'alt' not in i['title'].lower()]
            pick = infobox if infobox and 'portrait' in infobox.lower() else (portraits[-1] if portraits else infobox or p.get('pageimage'))
            if not pick:
                continue
            info = bpm.vt_query({'action': 'query', 'titles': 'File:' + pick, 'prop': 'imageinfo', 'iiprop': 'url|size', 'iiurlwidth': 720})['query']
            ii = (next(iter(info['pages'].values())).get('imageinfo') or [{}])[0]
            url = ii.get('thumburl') or ii.get('url')
            if not url:
                continue
            img = media.fetch_image(url)
        except Exception as e:  # noqa: BLE001
            log(f'vtuber {name}: {str(e)[:80]}')
            if '403' in str(e):
                log('vtuber: the Virtual YouTuber Wiki blocks this machine (GitHub runners get 403): stopping; mine this category on '
                    'the PC: python core/quiz/quiz_pool_grow.py mine --shard people --cats vtuber')
                break
            continue
        r = re.search(r'\|\s*retirement_date\s*=[ \t]*([^\n|]*)', text)
        it = {'name': name, 'agency': table[name][1], 'graduated': bool(r and r.group(1).strip() and 'streaming' not in r.group(1)),
              'difficulty': table[name][2], 'image': url, 'file': pick,
              'page': 'https://virtualyoutuber.fandom.com/wiki/' + urllib.parse.quote(p['title'].replace(' ', '_')), 'mined': today()}
        it['_thumb'] = thumb(img, os.path.join(out_dir, 'thumbs', 'vtuber', re.sub(r'[^A-Za-z0-9]+', '_', name) + '.jpg'))
        found.append(it)
        seen.add(f'vtuber:{fold(name)}')
    return found


def llm_spec(cats, st, seen):
    """The LLM jobs for tonight (pipeline/quiz-pool-llm.js)."""
    jobs, songs = [], None
    if 'emoji' in cats:
        jobs += [{'fmt': 'emoji', 'topic': 'anime', 'n': 6}, {'fmt': 'emoji', 'topic': 'kpop song', 'n': 6}]
        for ed in season_editions()[:2]:
            jobs.append({'fmt': 'emoji', 'topic': 'anime', 'n': 6, 'edition': ed})
    if 'trivia' in cats:
        jobs += [{'fmt': 'trivia', 'topic': t, 'n': n + 2} for t, n in TRIVIA_TOPICS.items()]
    for c in ('wyr_food', 'wyr_travel'):
        if c in cats:
            jobs.append({'fmt': 'wyr', 'topic': WYR_TOPIC[c], 'n': QUOTA[c] + 3})
    if 'kpop_song' in cats:
        songs = {'n': QUOTA['kpop_song'] + 4}
    avoid = {'emoji': [], 'trivia': [], 'wyr': [], 'song': []}
    for k in seen:
        cat, _, rest = k.partition(':')
        if cat in ('emoji', 'trivia', 'wyr'):
            avoid[cat].append(rest)
        elif cat == 'kpop_song':
            avoid['song'].append(rest)
    return {'jobs': jobs, 'songs': songs, 'avoid': {k: v[-150:] for k, v in avoid.items()}}


def mine_llm(cats, st, seen, out_dir, deadline, dry=False):
    """emoji / trivia / wyr_food / wyr_travel / kpop_song: LLM candidates (fact-checked in node), then pictures checked here."""
    from render_pop import photo_for, picture_ok
    spec = llm_spec(cats, st, seen)
    work = os.path.join(out_dir, 'llm')
    os.makedirs(work, exist_ok=True)
    sp, op = os.path.join(work, 'spec.json'), os.path.join(work, 'out.json')
    wj(sp, spec)
    if dry:
        return {c: [] for c in cats}, spec
    r = subprocess.run(['node', os.path.join(ROOT, 'pipeline', 'quiz-pool-llm.js'), sp, op], capture_output=True, text=True,
                       timeout=max(60, int(deadline - time.time())), cwd=ROOT, env=dict(os.environ, QUIZ_LLM='on'))
    log('llm stdout:', (r.stdout or '')[-3000:])
    log('llm stderr:', (r.stderr or '')[-1500:])
    got = rj(op, {}).get('items', {})
    out = {c: [] for c in cats}

    def pic(q, name):
        try:
            img, info = photo_for(q)
        except Exception as e:  # noqa: BLE001
            return None, str(e)
        if img is None or not picture_ok(img):
            return None, info
        return img, info

    if 'emoji' in cats:
        for it in got.get('emoji', [])[:QUOTA['emoji']]:
            if cand_key('emoji', it) not in seen:
                out['emoji'].append(it)
                seen.add(cand_key('emoji', it))
    if 'kpop_song' in cats:
        for it in got.get('song', [])[:QUOTA['kpop_song']]:
            if cand_key('kpop_song', it) not in seen:
                out['kpop_song'].append(it)
                seen.add(cand_key('kpop_song', it))
    if 'trivia' in cats:
        for it in got.get('trivia', []):
            if len(out['trivia']) >= QUOTA['trivia'] or time.time() > deadline:
                break
            if cand_key('trivia', it) in seen:
                continue
            img, info = pic(it.get('image'), it['question'])
            if img is None:
                log(f"trivia dropped (no reveal picture): {it['question']}: {str(info)[:80]}")
                continue
            it['imageCredit'] = info.get('credit')
            it['imagePage'] = info.get('page')
            it['_thumb'] = thumb(img, os.path.join(out_dir, 'thumbs', 'trivia', f"{len(out['trivia'])}.jpg"))
            out['trivia'].append(it)
            seen.add(cand_key('trivia', it))
    for c in ('wyr_food', 'wyr_travel'):
        if c not in cats:
            continue
        for it in got.get('wyr', []):
            if it.get('topic') != WYR_TOPIC[c] or len(out[c]) >= QUOTA[c] or time.time() > deadline:
                continue
            if cand_key(c, it) in seen:
                continue
            ia, inf_a = pic(it.get('imageQueryA'), it['a'])
            ib, inf_b = pic(it.get('imageQueryB'), it['b']) if ia is not None else (None, 'skipped')
            if ia is None or ib is None:
                log(f"{c} dropped (needs a photo for both options): {it['a']} / {it['b']}")
                continue
            from PIL import Image
            both = Image.new('RGB', (720, 360), (0, 0, 0))
            a2, b2 = ia.convert('RGB'), ib.convert('RGB')
            a2.thumbnail((358, 358))
            b2.thumbnail((358, 358))
            both.paste(a2, (0, 0))
            both.paste(b2, (362, 0))
            it['photos'] = {'a': {'credit': inf_a.get('credit'), 'page': inf_a.get('page')}, 'b': {'credit': inf_b.get('credit'), 'page': inf_b.get('page')}}
            it['_thumb'] = thumb(both, os.path.join(out_dir, 'thumbs', c, f'{len(out[c])}.jpg'), w=720)
            out[c].append(it)
            seen.add(cand_key(c, it))
    return out, spec


def mine_theme_art(st, quota, seen, out_dir, budget_min, slot=0):
    """Anime theme art through the snapshot builder (every gate of the live theme pools), on a staging copy of the pool.
    Popular characters collect up to 4 different pictures per theme (POP_THEME_VARIANTS; dHash-distinct)."""
    tid = rotation('theme_art', ART_THEMES, st)[slot % len(ART_THEMES)]
    mark_tried(st, 'theme_art', tid)
    stage = os.path.join(out_dir, 'art_stage')
    pop2 = os.path.join(stage, 'pop2')
    shutil.copytree(os.path.join(ASSETS, 'pop2'), pop2, dirs_exist_ok=True)
    extras = os.path.join(stage, 'character-extras.json')
    shutil.copy2(os.path.join(ASSETS, 'character-extras.json'), extras)
    env = dict(os.environ, POP2_DIR=pop2, POP_EXTRAS_FILE=extras, POP_MIN_FREE_GB='0', PYTHONIOENCODING='utf-8',
               POP_THEME_VARIANTS=os.environ.get('POP_THEME_VARIANTS', '4'), POP_THEME_VARIANT_TOP=os.environ.get('POP_THEME_VARIANT_TOP', '150'))
    r = subprocess.run([sys.executable, '-u', os.path.join(ROOT, 'scripts', 'build-pop-media.py'), '--newformats2', '--phase', 'theme',
                        '--theme', tid, '--minutes', str(budget_min)], env=env, cwd=ROOT, capture_output=True, text=True,
                       timeout=int(budget_min * 60 + 1800))
    log(f'theme {tid}: exit {r.returncode}', (r.stderr or '')[-1500:])
    new = rj(extras, {}).get('themes', {}).get(tid, {})
    found = []
    for fmt, items in new.items():
        for it in items:
            c = {'theme': tid, 'fmt': fmt, 'id': it.get('id'), 'file_id': it.get('file_id') or it.get('id'), 'item': it}
            k = cand_key('theme_art', c)
            if k in seen:
                continue
            g = it.get('gate') or {}
            if it.get('nsfw') or (g.get('rating') or {}).get('explicit', 0) > 0.15:
                continue
            d = it.get('dir') or f'theme/{tid}/{fmt}'
            fid = it.get('file_id') or it.get('id')
            files = [f for f in (os.path.join(pop2, d, f'{fid}.jpg'), os.path.join(pop2, d, f'{fid}.png'), os.path.join(pop2, d, f'{fid}_p.jpg'))
                     if os.path.isfile(f)]
            if not files and fmt != 'character':
                continue
            c['files'] = [os.path.relpath(f, pop2).replace(os.sep, '/') for f in files]
            dst = os.path.join(out_dir, 'pop2')
            for f in files:
                rel = os.path.relpath(f, pop2)
                os.makedirs(os.path.dirname(os.path.join(dst, rel)), exist_ok=True)
                shutil.copy2(f, os.path.join(dst, rel))
            jpg = next((f for f in files if f.endswith('.jpg')), None)
            try:   # selector themes (glasses, villains) reuse the character's AniList picture: thumbnail that for the review
                from PIL import Image
                import media
                pic = Image.open(jpg) if jpg else (media.fetch_image(it['image']) if it.get('image') else None)
                if pic is not None:
                    c['_thumb'] = thumb(pic, os.path.join(out_dir, 'thumbs', 'theme_art', f'{tid}_{fmt}_{fid}.jpg'))
            except Exception:  # noqa: BLE001
                pass
            found.append(c)
            seen.add(k)
            if len(found) >= quota:
                break
        if len(found) >= quota:
            break
    shutil.rmtree(stage, ignore_errors=True)
    return found


def label_lines(cat, it):
    if cat in ('geo', 'nightcity'):
        return [f"{it['name']} ({it['iso2']})", it.get('file', ''), it.get('credit', '')]
    if cat == 'cuisine':
        return [f"{it['name']} ({it['country']})", it.get('file', ''), it.get('credit', '')]
    if cat == 'kpop_idol':
        return [f"{it['name']} / {it['group']}", f"{it['wikidata']} links {it.get('sitelinks')}", it.get('credit', '')]
    if cat == 'vtuber':
        return [f"{it['name']} ({it['agency']})", it.get('file', '')]
    if cat == 'trivia':
        return [it['question'], f"-> {it['options'][it['answer']]}  | {' / '.join(it['options'])}", it.get('image', ''), it.get('imageCredit', '')]
    if cat in ('wyr_food', 'wyr_travel'):
        return [f"{it['a']}  OR  {it['b']}", f"pick {it.get('pick')}: {it.get('reason')}", f"{it.get('imageQueryA')} | {it.get('imageQueryB')}"]
    if cat == 'emoji':
        return [f"{it['answer']} [{it.get('topic')}{'/' + it['edition'] if it.get('edition') else ''}]", ' '.join(it.get('emojis') or []),
                it.get('fact', ''), f"song: {it.get('song') or '-'} / {it.get('artist') or '-'}"]
    if cat == 'kpop_song':
        return [f"{it['title']} - {it['artist']} ({it.get('year')})", f"difficulty {it.get('difficulty')}", it.get('query', '')]
    g = (it.get('item') or {}).get('gate') or {}
    src = (it.get('item') or {}).get('source') or {}
    return [f"{it['theme']}/{it['fmt']}: {(it.get('item') or {}).get('name')}", f"{(it.get('item') or {}).get('anime', '')[:40]}",
            f"age {(it.get('item') or {}).get('age_class')} gate {g.get('why')}", f"{src.get('srctype', 'wiki')} {src.get('artist', '')}"]


def cmd_mine(a):
    st = rj(a.state, {}) if a.state else {}
    cats = SHARDS[a.shard]
    if a.cats:
        cats = [c for c in cats if c in a.cats.split(',')]
    out_dir = os.path.abspath(os.path.join(a.work, 'out'))
    os.makedirs(out_dir, exist_ok=True)
    seen = live_keys() | set(st.get('staged') or [])
    deadline = time.time() + a.budget_min * 60
    res = {}
    t0 = time.time()
    staged_h = [int(x, 16) for x in (st.get('dhash') or [])]
    for c in [c for c in cats if c in ('geo', 'nightcity', 'cuisine')]:
        try:
            res[c] = mine_commons(c, st, QUOTA[c], seen, out_dir, deadline, live_hashes(c) + staged_h)
        except Exception as e:  # noqa: BLE001
            log(f'{c} failed: {e}')
            res[c] = []
    if 'kpop_idol' in cats:
        try:
            res['kpop_idol'] = mine_idols(st, QUOTA['kpop_idol'], seen, out_dir, deadline, live_hashes('kpop_idol') + staged_h)
        except Exception as e:  # noqa: BLE001
            log(f'kpop_idol failed: {e}')
    if 'vtuber' in cats:
        try:
            res['vtuber'] = mine_vtubers(st, QUOTA['vtuber'], seen, out_dir, deadline)
        except Exception as e:  # noqa: BLE001
            log(f'vtuber failed: {e}')
    llm_cats = [c for c in cats if c in ('emoji', 'trivia', 'wyr_food', 'wyr_travel', 'kpop_song')]
    if llm_cats:
        try:
            got, _ = mine_llm(llm_cats, st, seen, out_dir, deadline + 600, dry=a.dry_llm)
            res.update(got)
        except Exception as e:  # noqa: BLE001
            log(f'llm categories failed: {e}')
    if 'theme_art' in cats:
        try:
            slot = int(a.shard[3:]) if a.shard.startswith('art') and a.shard[3:].isdigit() else 0
            res['theme_art'] = mine_theme_art(st, QUOTA['theme_art'], seen, out_dir, a.budget_min, slot)
        except Exception as e:  # noqa: BLE001
            log(f'theme_art failed: {e}')
    # sheets per category
    for c, items in res.items():
        rows = [(it.get('_thumb'), [f'{k}. ' + label_lines(c, it)[0]] + label_lines(c, it)[1:]) for k, it in enumerate(items)]
        if rows:
            sheet(rows, os.path.join(out_dir, 'sheets', f'{c}.jpg'), cols=3 if c.startswith('wyr') else 4)
    for c, items in res.items():
        for it in items:
            if it.get('_thumb'):
                it['_thumb'] = os.path.relpath(it['_thumb'], out_dir).replace(os.sep, '/')
    st['staged'] = sorted(set(st.get('staged') or []) | {cand_key(c, it) for c, items in res.items() for it in items})
    st['dhash'] = sorted(set(st.get('dhash') or []) | {it['dhash'] for items in res.values() for it in items if it.get('dhash')})
    wj(os.path.join(out_dir, 'candidates.json'), {'shard': a.shard, 'date': today(), 'items': res})
    wj(os.path.join(out_dir, 'state.json'), st)
    print(json.dumps({'ok': True, 'shard': a.shard, 'counts': {c: len(v) for c, v in res.items()}, 'sec': round(time.time() - t0)}))


# ── staging release ──

def _gh(args, check=True):
    return subprocess.run(['gh'] + args, capture_output=True, text=True, check=check)


def staged_assets():
    r = _gh(['release', 'view', RELEASE, '--json', 'assets', '--jq', '.assets[].name'], check=False)
    return [x for x in r.stdout.split() if x.startswith(PREFIX) and x.endswith('.tar')] if r.returncode == 0 else []


def merge_states(states):
    out = {'query_log': {}, 'staged': set(), 'rejected': set(), 'dhash': set()}
    for s in states:
        out['dhash'] |= set(s.get('dhash') or [])
        for k, v in (s.get('query_log') or {}).items():
            out['query_log'][k] = max(v, out['query_log'].get(k, ''))
        out['staged'] |= set(s.get('staged') or [])
        out['rejected'] |= set(s.get('rejected') or [])
    return {'query_log': out['query_log'], 'staged': sorted(out['staged']), 'rejected': sorted(out['rejected']), 'dhash': sorted(out['dhash'])}


def cmd_publish(a):
    arts = sorted(glob.glob(os.path.join(a.artifacts, '*', 'candidates.json')))
    states = [rj(a.state, {})] if a.state and os.path.exists(a.state) else []
    os.makedirs(a.out, exist_ok=True)
    day = time.strftime('%Y%m%d', time.gmtime())
    tars, total = [], {}
    for cj in arts:
        base = os.path.dirname(cj)
        states.append(rj(os.path.join(base, 'state.json'), {}))
        c = rj(cj, {})
        n = sum(len(v) for v in (c.get('items') or {}).values())
        for k, v in (c.get('items') or {}).items():
            total[k] = total.get(k, 0) + len(v)
        if not n:
            continue
        name = f"{PREFIX}{day}-{c.get('shard', 'x')}-n{n}.tar"
        with tarfile.open(os.path.join(a.out, name), 'w') as t:
            for f in glob.glob(os.path.join(base, '**', '*'), recursive=True):
                rel = os.path.relpath(f, base).replace(os.sep, '/')
                if os.path.isfile(f) and not rel.startswith('llm/') and rel != 'state.json':
                    t.add(f, arcname=rel)
        tars.append(os.path.join(a.out, name))
    wj(os.path.join(a.out, STATE_ASSET), merge_states(states))
    if not a.dry:
        if _gh(['release', 'view', RELEASE], check=False).returncode != 0:
            _gh(['release', 'create', RELEASE, '--title', 'edit pools staging', '--notes', 'Staged pool candidates (reviewed before import).', '--prerelease'])
        _gh(['release', 'upload', RELEASE] + tars + [os.path.join(a.out, STATE_ASSET), '--clobber'])
    print(json.dumps({'ok': True, 'tars': [os.path.basename(x) for x in tars], 'staged': total, 'dry': bool(a.dry)}))


def cmd_need(a):
    """go = some category still has items to mine (always true for the LLM categories); prints go / stop."""
    print('go')


def cmd_pull(a):
    d = os.path.abspath(a.dir)
    os.makedirs(d, exist_ok=True)
    pulled = rj(os.path.join(d, 'pulled.json'), {'assets': []})
    todo = [x for x in staged_assets() if x not in pulled['assets']]
    for name in todo:
        _gh(['release', 'download', RELEASE, '--pattern', name, '--dir', d, '--clobber'])
        sub = os.path.join(d, name[:-4])
        os.makedirs(sub, exist_ok=True)
        with tarfile.open(os.path.join(d, name)) as t:
            for m in t.getmembers():
                if m.name.startswith('/') or '..' in m.name or not (m.isfile() or m.isdir()):
                    continue                      # never outside the folder, no links
                t.extract(m, sub)
        os.remove(os.path.join(d, name))
        pulled['assets'].append(name)
        log(f'pulled {name}')
    wj(os.path.join(d, 'pulled.json'), pulled)
    rows = []
    for cj in sorted(glob.glob(os.path.join(d, PREFIX + '*', 'candidates.json'))):
        base = os.path.dirname(cj)
        if os.path.exists(os.path.join(base, 'IMPORTED')):
            continue
        handled = set(rj(os.path.join(base, 'handled.json'), []))
        for cat, items in (rj(cj, {}).get('items') or {}).items():
            for k, it in enumerate(items):
                if cand_key(cat, it) in handled:
                    continue
                rows.append({'n': len(rows), 'cat': cat, 'key': cand_key(cat, it), 'dir': base, 'idx': k,
                             'thumb': os.path.join(base, it['_thumb']) if it.get('_thumb') else None, 'label': label_lines(cat, it)})
    rv = os.path.join(d, 'review')
    shutil.rmtree(rv, ignore_errors=True)
    os.makedirs(rv)
    wj(os.path.join(rv, 'index.json'), rows)
    for cat in CATS:
        rs = [r for r in rows if r['cat'] == cat]
        for i in range(0, len(rs), 24):
            sheet([(r['thumb'], [f"{r['n']}. {r['label'][0]}"] + r['label'][1:]) for r in rs[i:i + 24]],
                  os.path.join(rv, f'{cat}_{i // 24:02d}.jpg'), cols=3 if cat.startswith('wyr') else 4)
    with open(os.path.join(rv, 'index.txt'), 'w', encoding='utf-8') as f:
        for r in rows:
            f.write(f"{r['n']}\t{r['cat']}\t" + ' | '.join(str(x) for x in r['label']) + '\n')
    print(json.dumps({'ok': True, 'pulled': todo, 'to_review': len(rows), 'by_cat': {c: sum(r['cat'] == c for r in rows) for c in CATS if any(r['cat'] == c for r in rows)},
                      'review': rv}))


# ── import into the live pools ──

def _append_bank(section, items):
    """pop-bank.json keeps one item per line: append to a section in that style."""
    p = os.path.join(ASSETS, 'pop-bank.json')
    lines = open(p, encoding='utf-8').read().split('\n')
    start = next(i for i, l in enumerate(lines) if l.strip() == f'"{section}": [')
    end = start + 1
    while not re.match(r'^\s*\]', lines[end]):
        end += 1
    fmt = lambda it: '    ' + json.dumps(it, ensure_ascii=False).replace('", "', '", "')
    new = [fmt(it) for it in items]
    if not new:
        return
    if end - 1 > start:
        lines[end - 1] = lines[end - 1].rstrip().rstrip(',') + ','
    lines[end:end] = [l + (',' if k < len(new) - 1 else '') for k, l in enumerate(new)]
    txt = '\n'.join(lines)
    json.loads(txt)
    open(p, 'w', encoding='utf-8').write(txt)


def _append_json(name, field, items, source):
    p = os.path.join(ASSETS, name)
    d = rj(p, {'source': source, field: []})
    d.setdefault(field, []).extend(items)
    wj(p, d)


def clean_item(it):
    return {k: v for k, v in it.items() if not k.startswith('_')}


def cmd_import(a):
    d = os.path.abspath(a.dir)
    rows = rj(os.path.join(d, 'review', 'index.json'), [])
    rej = {x.strip() for x in (a.reject or '').split(',') if x.strip()}
    only = {x.strip() for x in (a.only or '').split(',') if x.strip()}
    if only:   # review one category at a time: the other rows stay pending for a later import
        rows = [r for r in rows if r['cat'] in only]
    rej_rows = [r for r in rows if str(r['n']) in rej]
    stamp = today()
    keep = {c: [] for c in CATS}
    for r in rows:
        if str(r['n']) in rej:
            continue
        it = (rj(os.path.join(r['dir'], 'candidates.json'), {}).get('items') or {}).get(r['cat'], [])[r['idx']]
        keep[r['cat']].append((r, dict(clean_item(it), reviewed=stamp)))
    have = live_keys()
    added = {}

    def fresh(cat, pairs):
        out = []
        for r, it in pairs:
            if r['key'] in have:
                continue
            have.add(r['key'])
            out.append((r, it))
        return out

    for cat, pairs in keep.items():
        pairs = fresh(cat, pairs)
        if not pairs:
            continue
        items = [it for _, it in pairs]
        if cat == 'geo':
            _append_json('city-photos.json', 'cities', [{k: v for k, v in it.items() if k not in ('query',)} for it in items],
                         'Wikimedia Commons photos (free licences, authors credited)')
        elif cat == 'nightcity':
            _append_json('night-city-photos.json', 'cities', items, 'Wikimedia Commons night skyline photos (free licences, authors credited); '
                         'nightly mining, reviewed by eye')
        elif cat == 'cuisine':
            _append_json('cuisine.json', 'dishes', items, 'Wikimedia Commons dish photos (free licences, authors credited); nightly mining, reviewed by eye')
        elif cat == 'kpop_idol':
            _append_json('kpop-idols.json', 'idols', items, 'Wikidata + Wikimedia Commons')
        elif cat == 'vtuber':
            _append_json('vtubers.json', 'vtubers', items, 'Virtual YouTuber Wiki official portraits')
        elif cat == 'kpop_song':
            _append_json('songs.json', 'songs', items, 'songs')
        elif cat == 'emoji':
            _append_bank('emoji', items)
        elif cat == 'trivia':
            _append_bank('trivia', items)
        elif cat in ('wyr_food', 'wyr_travel'):
            _append_bank('wyr', items)
        elif cat == 'theme_art':
            p = os.path.join(ASSETS, 'character-extras.json')
            ex = rj(p, {})
            for (r, it) in pairs:
                for rel in it.get('files') or []:
                    src = os.path.join(r['dir'], 'pop2', rel)
                    if os.path.exists(src):
                        os.makedirs(os.path.dirname(os.path.join(ASSETS, 'pop2', rel)), exist_ok=True)
                        shutil.copy2(src, os.path.join(ASSETS, 'pop2', rel))
                ex.setdefault('themes', {}).setdefault(it['theme'], {}).setdefault(it['fmt'], []).append(dict(it['item'], reviewed=stamp))
            wj(p, ex)
        added[cat] = len(items)
    rj_file = rj(REJECTS_FILE, {'about': 'Quiz pool candidates rejected in review (core/quiz/quiz_pool_grow.py import-staged); never mined again.',
                                'rejected': []})
    rj_file['rejected'] = sorted(set(rj_file['rejected']) | {r['key'] for r in rej_rows})
    wj(REJECTS_FILE, rj_file)
    for base in sorted({r['dir'] for r in rows}):
        handled = set(rj(os.path.join(base, 'handled.json'), [])) | {r['key'] for r in rows if r['dir'] == base}
        wj(os.path.join(base, 'handled.json'), sorted(handled))
        total = sum(len(v) for v in (rj(os.path.join(base, 'candidates.json'), {}).get('items') or {}).values())
        if len(handled) < total:
            continue                   # rows of other categories still wait for review
        open(os.path.join(base, 'IMPORTED'), 'w').close()
        if a.delete_remote:
            _gh(['release', 'delete-asset', RELEASE, os.path.basename(base) + '.tar', '-y'], check=False)
    print(json.dumps({'ok': True, 'imported': added, 'rejected': len(rej_rows)}))


def cmd_push_manifests(a):
    """Upload the reject list + query log (merged with the release's state) so the miner never re-stages a rejected item.
    (The live pools themselves are in the repo: the miner dedupes against the checkout.)"""
    dd = tempfile.mkdtemp(prefix='quiz-man-')
    remote = os.path.join(dd, STATE_ASSET)
    if _gh(['release', 'download', RELEASE, '--pattern', STATE_ASSET, '--dir', dd], check=False).returncode != 0:
        wj(remote, {})
    st = merge_states([rj(remote, {}), {'rejected': rj(REJECTS_FILE, {}).get('rejected', [])}])
    st['staged'] = [k for k in st['staged'] if k not in set(live_keys())]   # imported ones are live now
    wj(remote, st)
    assets = [remote]
    # the theme-art builder's network cache from this PC (wiki pages / pictures, Danbooru answers, descriptions, age classes; public
    # data, no credentials): the GitHub art shards start from it instead of an empty cache (an empty one finds 0 themed pictures)
    import gzip
    cache = os.path.join(os.environ.get('POP_MODELS') or os.path.join(os.path.expanduser('~'), '.cache', 'pop_quiz_models'), 'extras-cache.json')
    if os.path.exists(cache):
        gz = os.path.join(dd, 'pop-extras-cache.json.gz')
        with open(cache, 'rb') as f, gzip.open(gz, 'wb') as g:
            shutil.copyfileobj(f, g)
        assets.append(gz)
    _gh(['release', 'upload', RELEASE] + assets + ['--clobber'])
    print(json.dumps({'ok': True, 'rejected': len(st['rejected']), 'staged_pending': len(st['staged']), 'uploaded': [os.path.basename(x) for x in assets]}))


# ── selftest ──

def selftest(nights=60):
    st = {}
    t0 = datetime.date(2026, 10, 22)
    for n in range(nights):
        day = (t0 + datetime.timedelta(days=n)).isoformat()
        for cat, table in (('geo', [f'{c[0]}|{c[1]}|{t}' for c in CITIES for t in GEO_TERMS]),
                           ('nightcity', [f'{c[0]}|{c[1]}|{t}' for c in CITIES for t in NIGHT_TERMS]),
                           ('cuisine', [f'{x[0]}|{t}' for x in DISHES for t in DISH_TERMS]), ('vtuber', [v[0] for v in VTUBER_NAMES]),
                           ('theme_art', ART_THEMES)):
            order = rotation(cat, table, st, day)
            assert len(order) == len(table) and set(order) == set(table), f'{cat}: rotation lost items'
            assert rotation(cat, table, st, day) == order, f'{cat}: not deterministic'
            for x in order[:QUOTA[cat] if cat != 'theme_art' else 1]:
                st.setdefault('query_log', {})[f'{cat}|{x}'] = day
    # every table item gets its turn within the 60 nights; themes rotate (each one at least 6 times)
    for cat, table in (('geo', CITIES), ('cuisine', DISHES), ('vtuber', VTUBER_NAMES)):
        assert all(any(k.startswith(f'{cat}|{x[0]}') for k in st['query_log']) for x in table), f'{cat}: an item never came up'
    tried = [k for k in st['query_log'] if k.startswith('theme_art|')]
    assert set(t.split('|')[1] for t in tried) == set(ART_THEMES)
    # nightly shards are disjoint and complete; the backfill split covers the light categories exactly once
    assert not (set(SHARDS['light']) & set(SHARDS['art'])) and set(SHARDS['light']) | set(SHARDS['art']) == set(CATS)
    # keys: stable, category-scoped
    assert cand_key('geo', {'iso2': 'CN', 'name': "Xi'an", 'file': 'A.jpg'}) == 'geo:CN:xi an:a jpg'
    assert all(len(set(v)) == len(v) for v in SHARDS.values())
    assert set(SHARDS['photos'] + SHARDS['people'] + SHARDS['llm']) == set(SHARDS['light'])
    assert cand_key('trivia', {'question': 'What?'}) == 'trivia:what'
    assert fold('Rosé') == 'rose'
    # editions ahead of their window
    assert isinstance(season_editions('2026-10-20'), list)
    # live keys load (the repo pools)
    lk = live_keys()
    assert any(k.startswith('geo:') for k in lk) and any(k.startswith('trivia:') for k in lk)
    assert len({c[0] for c in CITIES}) == len(CITIES) and len({d[0] for d in DISHES}) == len(DISHES)
    print(json.dumps({'ok': True, 'nights': nights, 'cities': len(CITIES), 'dishes': len(DISHES), 'vtubers': len(VTUBER_NAMES),
                      'live_keys': len(lk), 'editions_now': season_editions()}))


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest='cmd', required=True)
    m = sp.add_parser('mine')
    m.add_argument('--shard', choices=list(SHARDS), default='light')
    m.add_argument('--work', default='work')
    m.add_argument('--state', default=None)
    m.add_argument('--budget-min', type=float, default=40)
    m.add_argument('--cats', default='')
    m.add_argument('--dry-llm', action='store_true')
    sp.add_parser('selftest')
    sp.add_parser('need')
    p = sp.add_parser('pull-staged')
    p.add_argument('--dir', default=os.path.join(ROOT, 'output', 'quiz-pool-review'))
    i = sp.add_parser('import-staged')
    i.add_argument('--dir', default=os.path.join(ROOT, 'output', 'quiz-pool-review'))
    i.add_argument('--reject', default='')
    i.add_argument('--delete-remote', action='store_true')
    i.add_argument('--only', default='', help='import only these categories (comma list); the rest stays pending')
    sp.add_parser('push-manifests')
    pb = sp.add_parser('publish')
    pb.add_argument('--artifacts', required=True)
    pb.add_argument('--out', required=True)
    pb.add_argument('--state', default=None)
    pb.add_argument('--dry', action='store_true')
    a = ap.parse_args()
    {'mine': cmd_mine, 'selftest': lambda a: selftest(), 'need': cmd_need, 'pull-staged': cmd_pull, 'import-staged': cmd_import,
     'push-manifests': cmd_push_manifests, 'publish': cmd_publish}[a.cmd](a)


if __name__ == '__main__':
    main()
