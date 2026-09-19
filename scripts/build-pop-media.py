#!/usr/bin/env python3
"""
Snapshot of the media the pop quiz formats draw from:

  core/quiz/assets/anime-characters.json  top characters by AniList favourites
  core/quiz/assets/anime-list.json        most popular anime (for the opening quiz)
  core/quiz/assets/kpop-idols.json        idol photos from Wikidata / Wikimedia Commons
  core/quiz/assets/city-photos.json       city photos from Wikidata / Wikimedia Commons   (--cities)
  core/quiz/assets/vtubers.json           official VTuber portraits, Virtual YouTuber Wiki (--vtubers)

Images are fetched at render time from the URLs stored here (and cached).
Commons photos keep their author/licence so the video description can credit them.
Characters and anime carry `gender` / `age` and an `nsfw` flag (Ecchi genre, or an
AniList "Sexual Content" tag ranked 50+): flagged ones never appear in the duel,
voice or scene formats.

    python scripts/build-pop-media.py [--skip-idols] [--skip-anime]
    python scripts/build-pop-media.py --enrich    # add gender / age / nsfw to the existing anime snapshots
    python scripts/build-pop-media.py --vtubers
    python scripts/build-pop-media.py --voices    # verified voice-line clips for the voice quiz (yt-dlp + faster-whisper)
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'core', 'quiz', 'assets')
UA = 'AsianPopQuizBuilder/1.0 (https://github.com/drcompilationyt7-alt/ChinaWeatherReader)'

# How fans actually say these names (AniList uses its own romanisation / order)
NAME_FIX = {
    'Luffy Monkey': 'Monkey D. Luffy', 'Zoro Roronoa': 'Roronoa Zoro', 'Goku Son': 'Son Goku', 'Ryuuk': 'Ryuk',
    'Sanji Vinsmoke': 'Sanji', 'Ace Portgas': 'Portgas D. Ace', 'Law Trafalgar': 'Trafalgar Law', 'Nami': 'Nami',
    'Hisoka Morow': 'Hisoka', 'Shigeo Kageyama': 'Mob', 'Satoru Gojou': 'Satoru Gojo', 'Sukuna Ryoumen': 'Ryomen Sukuna',
    # after the romanisation fix (keys are what fix_name would otherwise return)
    'Leloch Lamperoge': 'Lelouch Lamperouge', 'Joutaro Kujo': 'Jotaro Kujo', 'Jousuke Higashikata': 'Josuke Higashikata',
    'Robin Nico': 'Nico Robin', 'Chopper Tony Tony': 'Tony Tony Chopper', 'Jin-U Seong': 'Sung Jinwoo',
    'Tooru Honda': 'Tohru Honda', 'Asuka Langley Souryu': 'Asuka Langley Soryu', 'Sakura Mato': 'Sakura Matou',
    'Rin Toosaka': 'Rin Tohsaka', 'Tooru Oikawa': 'Toru Oikawa', 'Kyo Souma': 'Kyo Sohma', 'Yuki Souma': 'Yuki Sohma',
    'Hancock Boa': 'Boa Hancock', 'Doflamingo Donquixote': 'Donquixote Doflamingo', 'Hak Son': 'Son Hak',
}

IDOLS = {
    'BTS': ['RM', 'Jin', 'Suga', 'J-Hope', 'Jimin', 'V', 'Jungkook'],
    'BLACKPINK': ['Jisoo', 'Jennie', 'Rosé', 'Lisa'],
    'TWICE': ['Nayeon', 'Jihyo', 'Sana', 'Tzuyu', 'Momo', 'Mina', 'Dahyun', 'Chaeyoung', 'Jeongyeon'],
    'NewJeans': ['Minji', 'Hanni', 'Danielle', 'Haerin', 'Hyein'],
    'aespa': ['Karina', 'Winter', 'Giselle', 'Ningning'],
    'IVE': ['Jang Won-young', 'An Yu-jin', 'Liz', 'Leeseo', 'Rei', 'Gaeul'],
    'LE SSERAFIM': ['Kim Chae-won', 'Sakura', 'Huh Yun-jin', 'Kazuha', 'Hong Eun-chae'],
    'Stray Kids': ['Bang Chan', 'Hyunjin', 'Felix', 'Han', 'Lee Know', 'Changbin', 'Seungmin', 'I.N'],
    'EXO': ['Baekhyun', 'Kai', 'D.O.', 'Chanyeol', 'Sehun', 'Suho', 'Xiumin', 'Chen'],
    'BIGBANG': ['G-Dragon', 'Taeyang', 'Daesung', 'T.O.P'],
    'Red Velvet': ['Irene', 'Seulgi', 'Wendy', 'Joy', 'Yeri'],
    'ITZY': ['Yeji', 'Lia', 'Ryujin', 'Chaeryeong', 'Yuna'],
    'SEVENTEEN': ['S.Coups', 'Jeonghan', 'Joshua', 'Mingyu', 'Wonwoo', 'Hoshi', 'Woozi', 'DK', 'Vernon', 'Jun', 'The8', 'Seungkwan', 'Dino'],
    'solo': ['IU', 'PSY', 'Taeyeon', 'Chungha', 'Sunmi', 'BoA', 'Hwasa', 'Jessi'],
}


def http_json(url, data=None, headers=None, tries=4):
    h = {'User-Agent': UA, 'Accept': 'application/json', **(headers or {})}
    body = json.dumps(data).encode() if data is not None else None
    if body is not None:
        h['Content-Type'] = 'application/json'
    for k in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=body, headers=h), timeout=60) as r:
                return json.load(r)
        except Exception as e:
            print(f'  retry {k + 1}: {str(e)[:80]}', file=sys.stderr)
            time.sleep(3 * (k + 1))
    raise RuntimeError(f'failed: {url}')


def fix_name(n):
    if n in NAME_FIX:
        return NAME_FIX[n]
    # AniList romanisation -> the spelling fans use (Tanjirou -> Tanjiro, Shouyou -> Shoyo)
    out = ' '.join(re.sub(r'ou$', 'o', re.sub(r'(?<=[a-z])ou(?=[^aeiou])', 'o', w)).replace('uu', 'u') for w in n.split())
    return NAME_FIX.get(out, out)


def anilist(query, variables):
    return http_json('https://graphql.anilist.co', {'query': query, 'variables': variables})['data']


def is_nsfw(media):
    """Fan-service or sexual themes: the Ecchi genre, or any AniList "Sexual Content" tag ranked 50+."""
    if 'Ecchi' in (media.get('genres') or []):
        return True
    return any(t.get('category') == 'Sexual Content' and (t.get('rank') or 0) >= 50 for t in media.get('tags') or [])


def main_media(c):
    """The character's most popular anime where they are a MAIN or SUPPORTING character (not a cameo)."""
    edges = [e for e in (c.get('media') or {}).get('edges') or [] if e.get('node')]
    for want in (('MAIN', 'SUPPORTING'), ('MAIN', 'SUPPORTING', 'BACKGROUND')):
        for types in (('ANIME',), ('ANIME', 'MANGA')):
            for e in edges:
                if e.get('characterRole') in want and e['node'].get('type') in types:
                    return e['node']
    return edges[0]['node'] if edges else {}


def enrich_anime():
    """Add gender / age / nsfw to the existing snapshots without re-ranking them (difficulty stays)."""
    p = os.path.join(OUT, 'anime-characters.json')
    data = json.load(open(p, encoding='utf-8'))
    q = '''query($ids:[Int]){ Page(page:1, perPage:50){ characters(id_in:$ids){ id gender age
        media(sort: POPULARITY_DESC, perPage: 8){ edges{ characterRole node{ isAdult type title{ english romaji }
        genres tags{ name rank category } } } } } } }'''
    got = {}
    ids = [c['id'] for c in data['characters']]
    for k in range(0, len(ids), 50):
        for c in anilist(q, {'ids': ids[k:k + 50]})['Page']['characters']:
            got[c['id']] = c
        time.sleep(1.0)
    for c in data['characters']:
        g = got.get(c['id'])
        if g:
            media = main_media(g)
            title = media.get('title') and (media['title'].get('english') or media['title'].get('romaji'))
            if title and title != c['anime']:
                print(f"  {c['name']}: {c['anime']} -> {title}")
                c['anime'] = title
            c.update({'gender': g.get('gender'), 'age': g.get('age'), 'nsfw': is_nsfw(media)})
    json.dump(data, open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(f"{len(got)} characters enriched, {sum(bool(c.get('nsfw')) for c in data['characters'])} flagged nsfw")

    p = os.path.join(OUT, 'anime-list.json')
    data = json.load(open(p, encoding='utf-8'))
    q = '''query($ids:[Int]){ Page(page:1, perPage:50){ media(id_in:$ids){ id genres tags{ name rank category } } } }'''
    got = {}
    ids = [a['id'] for a in data['anime']]
    for k in range(0, len(ids), 50):
        for m in anilist(q, {'ids': ids[k:k + 50]})['Page']['media']:
            got[m['id']] = m
        time.sleep(1.0)
    for a in data['anime']:
        if a['id'] in got:
            a['nsfw'] = is_nsfw(got[a['id']])
    json.dump(data, open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(f"{len(got)} anime enriched, {sum(bool(a.get('nsfw')) for a in data['anime'])} flagged nsfw")


def build_anime():
    chars, seen = [], set()
    q = '''query($p:Int){ Page(page:$p, perPage:50){ characters(sort: FAVOURITES_DESC){ id favourites gender age
        name{ full } image{ large }
        media(sort: POPULARITY_DESC, perPage: 8){ edges{ characterRole node{ isAdult type title{ english romaji }
        genres tags{ name rank category } } } } } } }'''
    for page in range(1, 9):
        for c in anilist(q, {'p': page})['Page']['characters']:
            media = main_media(c)
            if not media or media.get('isAdult') or not c['image'].get('large') or 'default' in c['image']['large']:
                continue
            name = fix_name(c['name']['full'] or '')
            if not name or name.lower() in seen:
                continue
            seen.add(name.lower())
            title = media['title'].get('english') or media['title'].get('romaji')
            chars.append({'id': c['id'], 'name': name, 'anime': title, 'image': c['image']['large'], 'favourites': c['favourites'],
                          'gender': c.get('gender'), 'age': c.get('age'), 'nsfw': is_nsfw(media)})
        time.sleep(1.0)
    for i, c in enumerate(chars):
        c['difficulty'] = 1 if i < 60 else 2 if i < 180 else 3
    json.dump({'source': 'AniList (anilist.co)', 'characters': chars},
              open(os.path.join(OUT, 'anime-characters.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(f'{len(chars)} anime characters')

    anime = []
    q = '''query($p:Int){ Page(page:$p, perPage:50){ media(type: ANIME, sort: POPULARITY_DESC, isAdult: false){ id popularity
        title{ english romaji } coverImage{ extraLarge } seasonYear format genres tags{ name rank category } } } }'''
    for page in range(1, 7):
        for m in anilist(q, {'p': page})['Page']['media']:
            if m.get('format') not in ('TV', 'TV_SHORT', 'ONA'):
                continue
            anime.append({'id': m['id'], 'title': m['title'].get('english') or m['title']['romaji'], 'romaji': m['title']['romaji'],
                          'cover': m['coverImage']['extraLarge'], 'year': m.get('seasonYear'), 'popularity': m['popularity'],
                          'nsfw': is_nsfw(m)})
        time.sleep(1.0)
    for i, a in enumerate(anime):
        a['difficulty'] = 1 if i < 50 else 2 if i < 150 else 3
    json.dump({'source': 'AniList (anilist.co)', 'anime': anime},
              open(os.path.join(OUT, 'anime-list.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(f'{len(anime)} anime')


def wd_search(name, group):
    """Wikidata item for an idol: search the name, prefer entries that mention the group or K-pop."""
    params = {'action': 'wbsearchentities', 'search': name if group == 'solo' else f'{name}', 'language': 'en',
              'type': 'item', 'limit': 12, 'format': 'json'}
    res = http_json('https://www.wikidata.org/w/api.php?' + urllib.parse.urlencode(params)).get('search', [])
    def score(r):
        d = (r.get('description') or '').lower()
        s = 0
        if group != 'solo' and group.lower() in d:
            s += 5
        if 'south korean' in d or 'k-pop' in d or 'korean' in d:
            s += 3
        if 'singer' in d or 'rapper' in d or 'idol' in d or 'member' in d or 'dancer' in d:
            s += 2
        if group in ('TWICE', 'LE SSERAFIM') and ('japanese' in d or 'taiwanese' in d):
            s += 3
        if group == 'aespa' and ('japanese' in d or 'chinese' in d):
            s += 2
        return s
    best = max(res, key=score, default=None)
    return best['id'] if best and score(best) >= 5 else None


def commons_info(filename):
    params = {'action': 'query', 'titles': 'File:' + filename, 'prop': 'imageinfo', 'iiprop': 'url|extmetadata',
              'iiurlwidth': 900, 'format': 'json'}
    pages = http_json('https://commons.wikimedia.org/w/api.php?' + urllib.parse.urlencode(params))['query']['pages']
    info = next(iter(pages.values())).get('imageinfo', [{}])[0]
    meta = info.get('extmetadata', {})
    artist = re.sub(r'\s+', ' ', re.sub('<[^>]+>', '', meta.get('Artist', {}).get('value', ''))).strip()
    return {'url': info.get('thumburl') or info.get('url'), 'page': info.get('descriptionurl'),
            'license': meta.get('LicenseShortName', {}).get('value', ''), 'author': artist[:80]}


def build_idols():
    idols = []
    for group, members in IDOLS.items():
        for name in members:
            try:
                qid = wd_search(name, group)
                if not qid:
                    print(f'  no match: {name} ({group})', file=sys.stderr)
                    continue
                ent = http_json(f'https://www.wikidata.org/wiki/Special:EntityData/{qid}.json')['entities'][qid]
                p18 = ent.get('claims', {}).get('P18')
                if not p18:
                    print(f'  no photo: {name} ({group})', file=sys.stderr)
                    continue
                fn = p18[0]['mainsnak']['datavalue']['value']
                info = commons_info(fn)
                if not info['url']:
                    continue
                idols.append({'name': name, 'group': None if group == 'solo' else group, 'wikidata': qid, 'image': info['url'],
                              'credit': f"{info['author'] or 'Wikimedia Commons'}, {info['license']}", 'page': info['page']})
                time.sleep(0.3)
            except Exception as e:
                print(f'  {name}: {e}', file=sys.stderr)
    fame = {'BTS': 1, 'BLACKPINK': 1, 'solo': 2, 'TWICE': 2, 'NewJeans': 2, 'aespa': 2, 'Stray Kids': 2, 'BIGBANG': 2,
            'IVE': 3, 'LE SSERAFIM': 3, 'EXO': 2, 'Red Velvet': 3, 'ITZY': 3, 'SEVENTEEN': 3}
    for i in idols:
        i['difficulty'] = fame.get(i['group'] or 'solo', 3)
    json.dump({'source': 'Wikidata / Wikimedia Commons (see credit per photo)', 'idols': idols},
              open(os.path.join(OUT, 'kpop-idols.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(f'{len(idols)} idols with photos')


CITY_QIDS = {  # Wikidata items of the cities in pop-bank.json (avoids ambiguous name searches)
    'Beijing': 'Q956', 'Shanghai': 'Q8686', 'Hong Kong': 'Q8646', 'Guangzhou': 'Q16572', 'Chengdu': 'Q30002',
    "Xi'an": 'Q5826', 'Harbin': 'Q42956', 'Lhasa': 'Q5869', 'Urumqi': 'Q93264', 'Chongqing': 'Q11725',
    'Hangzhou': 'Q4970', 'Kunming': 'Q167219', 'Sanya': 'Q192906', 'Wuhan': 'Q11746', 'Tokyo': 'Q1490',
    'Osaka': 'Q35765', 'Kyoto': 'Q34600', 'Sapporo': 'Q37951', 'Fukuoka': 'Q26600', 'Hiroshima': 'Q34664',
    'Nagoya': 'Q11751', 'Sendai': 'Q47262', 'Nagasaki': 'Q38234', 'Seoul': 'Q8684', 'Busan': 'Q16520',
    'Incheon': 'Q20934', 'Daegu': 'Q20927', 'Gwangju': 'Q41283', 'Jeju': 'Q41520', 'Daejeon': 'Q20921',
    'Gyeongju': 'Q41327',
}


# Commons files that show a city better than its Wikidata image (a landmark instead of a hazy skyline)
CITY_PHOTO_FILE = {
    'Lhasa': '布达拉宫.jpg',
    'Harbin': 'Harbin Ice & Snow Festival 2026 - Saint Sophia Cathedral.jpg',
    'Jeju': 'Seongsan Ilchulbong from the air.jpg',
    'Gyeongju': 'Bulguksa temple entrance gate stairs flower bed and blue sky in Gyeongju South Korea.jpg',
}
# the photo quiz only: skylines that are hard to tell apart without a landmark
CITY_PHOTO_DIFFICULTY = {'Incheon': 3, 'Daegu': 3}


def build_city_photos():
    bank = json.load(open(os.path.join(OUT, 'pop-bank.json'), encoding='utf-8'))
    out = []
    for c in bank['cities']:
        qid = CITY_QIDS.get(c['name'])
        if not qid:
            continue
        c = {**c, 'difficulty': CITY_PHOTO_DIFFICULTY.get(c['name'], c['difficulty'])}
        if c['name'] in CITY_PHOTO_FILE:
            info = commons_info(CITY_PHOTO_FILE[c['name']])
            if info['url']:
                out.append({**c, 'image': info['url'], 'credit': f"{info['author'] or 'Wikimedia Commons'}, {info['license']}",
                            'page': info['page'], 'wikidata': qid})
                continue
        try:
            ent = http_json(f'https://www.wikidata.org/wiki/Special:EntityData/{qid}.json')['entities'][qid]
            label = ent.get('labels', {}).get('en', {}).get('value', '')
            if label.split(',')[0].lower().replace('-', ' ') not in c['name'].lower().replace("'", '').replace('-', ' ')                     and c['name'].lower().split()[0] not in label.lower():
                print(f"  {c['name']}: {qid} is '{label}', skipped", file=sys.stderr)
                continue
            p18 = ent.get('claims', {}).get('P18')
            if not p18:
                continue
            info = commons_info(p18[0]['mainsnak']['datavalue']['value'])
            if info['url']:
                out.append({**c, 'image': info['url'], 'credit': f"{info['author'] or 'Wikimedia Commons'}, {info['license']}",
                            'page': info['page'], 'wikidata': qid})
            time.sleep(0.3)
        except Exception as e:
            print(f"  {c['name']}: {e}", file=sys.stderr)
    json.dump({'source': 'Wikidata / Wikimedia Commons (see credit per photo)', 'cities': out},
              open(os.path.join(OUT, 'city-photos.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(f'{len(out)} city photos')


VT_API = 'https://virtualyoutuber.fandom.com/api.php'
# (wiki page, agency / branch shown on the reveal, difficulty, display name). Only VTubers whose
# persona and official art are clean (portraits with a revealing neckline are left out); the
# pick is always the wiki's official portrait art.
VTUBERS = [
    ('Gawr Gura', 'hololive EN', 1), ('Mori Calliope', 'hololive EN', 1), ('Kizuna AI', 'the first VTuber', 1, 'Kizuna AI'),
    ('Neuro-sama', 'AI VTuber by Vedal', 1), ('Usada Pekora', 'hololive JP', 1), ('Shirakami Fubuki', 'hololive JP', 1),
    ('Hoshimachi Suisei', 'hololive JP', 1), ('Inugami Korone', 'hololive JP', 1), ('Takanashi Kiara', 'hololive EN', 1),
    ("Ninomae Ina'nis", 'hololive EN', 1), ('Watson Amelia', 'hololive EN', 1), ('Ironmouse', 'formerly VShojo', 1),
    ('Sakura Miko', 'hololive JP', 1), ('Minato Aqua', 'hololive JP', 1),
    ('Ouro Kronii', 'hololive EN', 2), ('Nanashi Mumei', 'hololive EN', 2), ('Hakos Baelz', 'hololive EN', 2),
    ('IRyS', 'hololive EN', 2), ('Ceres Fauna', 'hololive EN', 2), ('Koseki Bijou', 'hololive EN', 2),
    ('Nerissa Ravencroft', 'hololive EN', 2), ('Nekomata Okayu', 'hololive JP', 2), ('Amane Kanata', 'hololive JP', 2),
    ('Oozora Subaru', 'hololive JP', 2), ('Nakiri Ayame', 'hololive JP', 2),
    ('Shishiro Botan', 'hololive JP', 2), ('Omaru Polka', 'hololive JP', 2),
    ('Tokino Sora', 'hololive JP', 2), ('Ookami Mio', 'hololive JP', 2), ('La+ Darknesss', 'hololive JP', 2),
    ('Hakui Koyori', 'hololive JP', 2), ('Pavolia Reine', 'hololive ID', 2), ('Kureiji Ollie', 'hololive ID', 2),
    ('Moona Hoshinova', 'hololive ID', 2), ('Kobo Kanaeru', 'hololive ID', 2), ('Tsukino Mito', 'NIJISANJI', 2),
    ('Kuzuha', 'NIJISANJI', 2), ('Vox Akuma', 'NIJISANJI EN', 2), ('Pomu Rainpuff', 'NIJISANJI EN', 2),
    ('Nyanners', 'formerly VShojo', 2, 'Nyanners'), ('Pipkin Pippa', 'Phase Connect', 2), ('Dokibird', 'independent', 2),
    ('Airani Iofifteen', 'hololive ID', 3), ('Vestia Zeta', 'hololive ID', 3), ('Kaela Kovalskia', 'hololive ID', 3),
    ('Shiori Novella', 'hololive EN', 3), ('Elizabeth Rose Bloodflame', 'hololive EN', 3), ('Gigi Murin', 'hololive EN', 3),
    ('Cecilia Immergreen', 'hololive EN', 3), ('Raora Panthera', 'hololive EN', 3), ('Momosuzu Nene', 'hololive JP', 3),
    ('Takane Lui', 'hololive JP', 3), ('Kazama Iroha', 'hololive JP', 3),
    ('Akai Haato', 'hololive JP', 3), ('Himemori Luna', 'hololive JP', 3), ('Kanade Izuru', 'HOLOSTARS', 3),
    ('Kanae', 'NIJISANJI', 3), ('Lize Helesta', 'NIJISANJI', 3), ('Hoshikawa Sara', 'NIJISANJI', 3),
    ('Sasaki Saku', 'NIJISANJI', 3), ('Elira Pendora', 'NIJISANJI EN', 3), ('Finana Ryugu', 'NIJISANJI EN', 3),
    ('Luca Kaneshiro', 'NIJISANJI EN', 3), ('Shu Yamino', 'NIJISANJI EN', 3), ('Ike Eveland', 'NIJISANJI EN', 3),
    ('Uki Violeta', 'NIJISANJI EN', 3), ('Kson', 'formerly VShojo', 3),
]


def vt_query(params):
    return http_json(VT_API + '?' + urllib.parse.urlencode({**params, 'format': 'json'}))


def build_vtubers():
    """Official portrait of each VTuber from the Virtual YouTuber Wiki (the infobox image, or a '* Portrait' file)."""
    out = []
    for k in range(0, len(VTUBERS), 20):
        chunk = VTUBERS[k:k + 20]
        d = vt_query({'action': 'query', 'titles': '|'.join(v[0] for v in chunk), 'redirects': 1,
                      'prop': 'pageimages|images|revisions', 'piprop': 'name', 'imlimit': 500,
                      'rvprop': 'content', 'rvslots': 'main', 'rvsection': 0})['query']
        alias = {r['from']: r['to'] for r in d.get('normalized', []) + d.get('redirects', [])}
        pages = {p.get('title'): p for p in d['pages'].values()}
        for v in chunk:
            title = alias.get(alias.get(v[0], v[0]), alias.get(v[0], v[0]))
            p = pages.get(title)
            if not p or 'missing' in p:
                print(f'  missing page: {v[0]}', file=sys.stderr)
                continue
            text = (p.get('revisions') or [{}])[0].get('slots', {}).get('main', {}).get('*', '')
            m = re.search(r'\|\s*image1\s*=[ \t]*([^\n|<]*)', text)
            infobox = m.group(1).strip() if m and m.group(1).strip() else None
            portraits = [i['title'][5:] for i in p.get('images', []) if 'portrait' in i['title'].lower() and 'alt' not in i['title'].lower()]
            pick = infobox if infobox and 'portrait' in infobox.lower() else (portraits[-1] if portraits else infobox or p.get('pageimage'))
            if not pick:
                print(f'  no portrait: {v[0]}', file=sys.stderr)
                continue
            info = vt_query({'action': 'query', 'titles': 'File:' + pick, 'prop': 'imageinfo', 'iiprop': 'url|size', 'iiurlwidth': 720})['query']
            ii = (next(iter(info['pages'].values())).get('imageinfo') or [{}])[0]
            url = ii.get('thumburl') or ii.get('url')
            if not url:
                print(f'  no image url: {v[0]} ({pick})', file=sys.stderr)
                continue
            r = re.search(r'\|\s*retirement_date\s*=[ \t]*([^\n|]*)', text)
            retired = bool(r and r.group(1).strip() and 'streaming' not in r.group(1))
            out.append({'name': v[3] if len(v) > 3 else title, 'agency': v[1], 'graduated': retired, 'difficulty': v[2],
                        'image': url, 'file': pick, 'page': 'https://virtualyoutuber.fandom.com/wiki/' + urllib.parse.quote(title.replace(' ', '_'))})
            time.sleep(0.3)
    json.dump({'source': 'Virtual YouTuber Wiki (virtualyoutuber.fandom.com), official portrait art; characters (c) their agencies',
               'vtubers': out}, open(os.path.join(OUT, 'vtubers.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(f'{len(out)} VTubers with portraits')


def build_voices(limit=220):
    """
    core/quiz/assets/voice-lines.json: for each of the most popular characters (no nsfw flag, no
    fan-service show), a YouTube voice-line upload that names the character and the show, and the
    start of ~3 s in it that faster-whisper confirmed as Japanese speech with a few words. The voice
    quiz draws only from these (and re-checks the clip at render time). Needs yt-dlp + faster-whisper.
    """
    sys.path.insert(0, os.path.join(ROOT, 'core', 'quiz'))
    import media
    from render_pop import SCENE_SKIP, same_series
    from concurrent.futures import ThreadPoolExecutor
    chars = json.load(open(os.path.join(OUT, 'anime-characters.json'), encoding='utf-8'))['characters'][:limit]
    pool = [{**c, 'difficulty': 1 if k < 25 else 2 if k < 70 else 3} for k, c in enumerate(chars)
            if not c.get('nsfw') and not any(same_series(c['anime'], s) for s in SCENE_SKIP)]

    def one(c):
        try:
            seg, info = media.fetch_voice(c['name'], c['anime'])
        except Exception as e:
            seg, info = None, str(e)
        return c, seg, info

    out = []
    with ThreadPoolExecutor(3) as ex:
        for c, seg, info in ex.map(one, pool):
            if seg is None:
                print(f"  - {c['name']} ({c['anime']}): {str(info)[:90]}", file=sys.stderr)
                continue
            asr = info.get('asr') or {}
            out.append({'id': c['id'], 'name': c['name'], 'anime': c['anime'], 'image': c['image'], 'favourites': c['favourites'],
                        'difficulty': c['difficulty'], 'video': info['video'], 'videoTitle': info.get('videoTitle'),
                        'start': round(info['pin'], 3), 'check':{k: asr.get(k) for k in ('lang', 'langProb', 'noSpeech', 'logprob')}})
            print(f"  + {c['name']}: {info.get('videoTitle')}", file=sys.stderr)
    json.dump({'source': 'YouTube voice-line uploads that name the character and the show; clips checked by faster-whisper '
                         '(Japanese speech, a few words). Character art: AniList.', 'voices': out},
              open(os.path.join(OUT, 'voice-lines.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(f'{len(out)}/{len(pool)} characters with a verified voice clip')


if __name__ == '__main__':
    if '--voices' in sys.argv:
        build_voices()
        sys.exit(0)
    if '--cities' in sys.argv:
        build_city_photos()
        sys.exit(0)
    if '--enrich' in sys.argv:
        enrich_anime()
        sys.exit(0)
    if '--vtubers' in sys.argv:
        build_vtubers()
        sys.exit(0)
    if '--skip-anime' not in sys.argv:
        build_anime()
    if '--skip-idols' not in sys.argv:
        build_idols()
