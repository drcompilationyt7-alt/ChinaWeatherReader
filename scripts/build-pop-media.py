#!/usr/bin/env python3
"""
Snapshot of the media the pop quiz formats draw from:

  core/quiz/assets/anime-characters.json  top characters by AniList favourites
  core/quiz/assets/anime-list.json        most popular anime (for the opening quiz)
  core/quiz/assets/kpop-idols.json        idol photos from Wikidata / Wikimedia Commons

Images are fetched at render time from the URLs stored here (and cached).
Commons photos keep their author/licence so the video description can credit them.

    python scripts/build-pop-media.py [--skip-idols] [--skip-anime]
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
    n = NAME_FIX.get(n, n)
    # AniList romanisation -> the spelling fans use (Tanjirou -> Tanjiro, Shouyou -> Shoyo)
    return ' '.join(re.sub(r'ou$', 'o', re.sub(r'(?<=[a-z])ou(?=[^aeiou])', 'o', w)).replace('uu', 'u') for w in n.split())


def anilist(query, variables):
    return http_json('https://graphql.anilist.co', {'query': query, 'variables': variables})['data']


def build_anime():
    chars, seen = [], set()
    q = '''query($p:Int){ Page(page:$p, perPage:50){ characters(sort: FAVOURITES_DESC){ id favourites
        name{ full } image{ large }
        media(sort: POPULARITY_DESC, perPage: 1){ nodes{ isAdult type title{ english romaji } } } } } }'''
    for page in range(1, 9):
        for c in anilist(q, {'p': page})['Page']['characters']:
            media = (c['media']['nodes'] or [{}])[0]
            if not media or media.get('isAdult') or not c['image'].get('large') or 'default' in c['image']['large']:
                continue
            name = fix_name(c['name']['full'] or '')
            if not name or name.lower() in seen:
                continue
            seen.add(name.lower())
            title = media['title'].get('english') or media['title'].get('romaji')
            chars.append({'id': c['id'], 'name': name, 'anime': title, 'image': c['image']['large'], 'favourites': c['favourites']})
        time.sleep(1.0)
    for i, c in enumerate(chars):
        c['difficulty'] = 1 if i < 60 else 2 if i < 180 else 3
    json.dump({'source': 'AniList (anilist.co)', 'characters': chars},
              open(os.path.join(OUT, 'anime-characters.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(f'{len(chars)} anime characters')

    anime = []
    q = '''query($p:Int){ Page(page:$p, perPage:50){ media(type: ANIME, sort: POPULARITY_DESC, isAdult: false){ id popularity
        title{ english romaji } coverImage{ extraLarge } seasonYear format } } }'''
    for page in range(1, 7):
        for m in anilist(q, {'p': page})['Page']['media']:
            if m.get('format') not in ('TV', 'TV_SHORT', 'ONA'):
                continue
            anime.append({'id': m['id'], 'title': m['title'].get('english') or m['title']['romaji'], 'romaji': m['title']['romaji'],
                          'cover': m['coverImage']['extraLarge'], 'year': m.get('seasonYear'), 'popularity': m['popularity']})
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
    artist = re.sub('<[^>]+>', '', meta.get('Artist', {}).get('value', '')).strip()
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


if __name__ == '__main__':
    if '--skip-anime' not in sys.argv:
        build_anime()
    if '--skip-idols' not in sys.argv:
        build_idols()
