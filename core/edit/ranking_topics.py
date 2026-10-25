#!/usr/bin/env python3
"""Topic library of the ranking compilation: load / refresh / sanity check (core/edit/refs/ranking-topics.json).

    python core/edit/ranking_topics.py check            # sanity check (exit 1 on an error); the owner runs it after edits
    python core/edit/ranking_topics.py refresh          # AniList numbers (favourites / score / main characters) -> the file
    python core/edit/ranking_topics.py show [--md]      # the library with its orders

Order rules (see the file's _rules):
  power  community power-scaling consensus; every 'above' pair [stronger, weaker, why] must hold; pins hold;
         a pinned #1 needs reveal 'top' (owner 2026-10-25: Rimuru is #1 in any power list AND shown)
  fans   AniList favourites high -> low (a later entry may not have more favourites than an earlier one)
  score  AniList average score high -> low (refresh re-sorts unless order_locked; the check flags a > 2 point inversion)
Errors fail the check; warnings are printed (e.g. a character missing from the AniList pool, no wiki for references).
"""
import json
import os
import re
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
PATH = os.path.join(HERE, 'refs', 'ranking-topics.json')
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36'
SLOTS = 5


def load_raw(path=PATH):
    return json.load(open(path, encoding='utf-8'))


def _alias(e):
    if e.get('alias'):
        return e['alias']
    toks = [t for t in re.split(r'[^a-z0-9.]+', (e.get('search') or e['name']).lower()) if len(t) >= 3]
    return [re.escape(' '.join(toks))] + [r'\b' + re.escape(t) + r'\b' for t in toks if t not in ('the', 'anime', 'edit')][:2]


def normalise(raw):
    """-> {topic: topic dict} with entry defaults filled (q, alias, wiki, pace, kind)."""
    out = {}
    for key, T in raw['topics'].items():
        T = dict(T)
        T['key'] = key
        T.setdefault('reveal', 'countdown')
        T.setdefault('pace', 'action')
        ents = []
        for i, e in enumerate(T['entries']):
            e = dict(e)
            e['rank'] = i + 1
            e.setdefault('search', e['name'].lower())
            e.setdefault('q', [f'{e["search"]} edit 4k', f'{e["search"]} amv edit'])
            e['alias'] = _alias(e)
            e.setdefault('wiki', T.get('wiki'))
            e.setdefault('pace', T['pace'])
            e.setdefault('kind', T.get('kind', 'character'))
            e.setdefault('chars', [])
            ents.append(e)
        T['entries'] = ents
        out[key] = T
    return out


def load(path=PATH):
    return normalise(load_raw(path))


def revealed_ranks(T, n_show=3):
    """Ranks shown on screen in play order. countdown: #5, #4[, #3]; top: #3, #2, #1 (a 3-slot list)."""
    if T.get('reveal') == 'top':
        return [3, 2, 1]
    return list(range(SLOTS, SLOTS - n_show, -1))


def slots(T):
    return 3 if T.get('reveal') == 'top' else SLOTS


# ------------------------------------------------------------------ AniList

def gql(query, variables):
    body = json.dumps({'query': query, 'variables': variables}).encode()
    for k in range(4):
        try:
            req = urllib.request.Request('https://graphql.anilist.co', data=body, headers={
                'Content-Type': 'application/json', 'Accept': 'application/json', 'User-Agent': UA})
            return json.load(urllib.request.urlopen(req, timeout=25))['data']
        except Exception as e:  # noqa: BLE001
            if k == 3:
                raise
            time.sleep(65 if '429' in str(e) else 4 * (k + 1))     # AniList: ~30 requests / min


_WIKI = {}


def _site(slug):
    if slug not in _WIKI:
        try:
            req = urllib.request.Request(f'https://{slug}.fandom.com/api.php?action=query&meta=siteinfo&siprop=general&format=json',
                                         headers={'User-Agent': UA})
            _WIKI[slug] = json.load(urllib.request.urlopen(req, timeout=20))['query']['general'].get('sitename', '')
        except Exception:  # noqa: BLE001
            _WIKI[slug] = None
    return _WIKI[slug]


def wiki_ok(slug, show):
    """The slug's Fandom wiki exists and its site name looks like the show."""
    import difflib
    name = _site(slug) if slug else None
    if not name:
        return False
    a = re.sub(r'[^a-z0-9]', '', re.sub(r'wiki', '', name.lower()))
    b = re.sub(r'[^a-z0-9]', '', re.split(r'[:(]', show.lower())[0])
    return bool(a) and (a in b or b in a or difflib.SequenceMatcher(None, a, b).ratio() >= 0.6)


def discover_wiki(show):
    w = [t for t in re.split(r'[^a-z0-9]+', re.split(r'[:(]', show.lower())[0]) if t]
    if w and w[0] == 'the':
        w = w[1:]
    for slug in dict.fromkeys([''.join(w), '-'.join(w), ''.join(w[:2]), ''.join(w[:3]), '-'.join(w[:3])]):
        if len(slug) >= 4 and wiki_ok(slug, show):
            return slug
    return None


def pool_chars():
    return json.load(open(os.path.join(ROOT, 'core', 'quiz', 'assets', 'anime-characters.json'), encoding='utf-8'))['characters']


def refresh(path=PATH):
    raw = load_raw(path)
    pool = {c['name'].lower(): c for c in pool_chars()}
    QC = 'query($s:String){Character(search:$s){id name{full} favourites}}'
    QM = ('query($s:String){Media(search:$s,type:ANIME){id title{english romaji} averageScore popularity '
          'characters(sort:FAVOURITES_DESC,perPage:6,role:MAIN){nodes{name{full} favourites}}}}')
    for key, T in raw['topics'].items():
        if T.get('wiki') and not _site(T['wiki']):          # a hand-set slug is trusted when that wiki exists
            print(f'{key}: wiki {T["wiki"]} not found / not this show', flush=True)
            T['wiki'] = discover_wiki(T['entries'][0]['anime'])
        for e in T['entries']:
            if e.get('wiki') and not _site(e['wiki']):
                print(f'{key} {e["name"]}: wiki {e["wiki"]} not found / not this show', flush=True)
                e['wiki'] = discover_wiki(e['anime'])
            if not e.get('wiki') and not T.get('wiki'):
                e['wiki'] = discover_wiki(e['anime'])
            if e.get('wiki') is None:
                e.pop('wiki', None)
            b = e.setdefault('anilist', {})
            if b.get('id') and (T.get('kind') != 'anime' or e.get('chars')):
                continue                                   # already filled (delete 'anilist' to refetch)
            if T.get('kind') == 'anime':
                d = gql(QM, {'s': e['anime']})['Media']
                b.update(id=d['id'], title=d['title']['english'] or d['title']['romaji'], score=d['averageScore'],
                         popularity=d['popularity'])
                if not e.get('chars'):
                    e['chars'] = [n['name']['full'] for n in d['characters']['nodes']][:6]
            else:
                c = pool.get(e['chars'][0].lower())
                if c:
                    b.update(id=c['id'], favourites=c['favourites'], in_pool=True)
                else:
                    d = gql(QC, {'s': e['chars'][0]})['Character']
                    b.update(id=d['id'], favourites=d['favourites'], name=d['name']['full'], in_pool=False)
            time.sleep(2.2)
            json.dump(raw, open(path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        if T.get('order') == 'score' and not T.get('order_locked'):
            T['entries'].sort(key=lambda e: -((e.get('anilist') or {}).get('score') or 0))
        if T.get('order') == 'fans' and not T.get('order_locked'):
            T['entries'].sort(key=lambda e: -((e.get('anilist') or {}).get('favourites') or 0))
        print(key, [(e['name'], e['anilist'].get('score') or e['anilist'].get('favourites')) for e in T['entries']], flush=True)
    raw['refreshed'] = time.strftime('%Y-%m-%d')
    json.dump(raw, open(path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)


# ------------------------------------------------------------------ sanity check

def check(topics=None):
    """-> (errors, warnings)"""
    topics = topics or load()
    errs, warns = [], []
    pool = {c['name'].lower() for c in pool_chars()}
    for key, T in topics.items():
        es = T['entries']
        names = [e['name'] for e in es]
        rank = {e['name']: e['rank'] for e in es}
        need = max(revealed_ranks(T)) + (0 if T.get('reveal') == 'top' else 0)
        if len(es) < need:
            errs.append(f'{key}: {len(es)} entries, the reveal needs {need}')
        if len(set(names)) != len(names):
            errs.append(f'{key}: duplicate entries')
        if T.get('order') not in ('power', 'fans', 'score'):
            errs.append(f'{key}: order must be power / fans / score')
        for e in es:
            if e.get('pin') and e['rank'] != e['pin']:
                errs.append(f'{key}: {e["name"]} is pinned to #{e["pin"]} but sits at #{e["rank"]}')
            if T.get('order') == 'power' and e.get('pin') == 1 and T.get('reveal') != 'top':
                errs.append(f'{key}: {e["name"]} is pinned #1 in a power list, so the list must reveal its top (reveal: top)')
            if T.get('kind') != 'anime':
                for c in e.get('chars') or []:
                    if c.lower() not in pool and not (e.get('anilist') or {}).get('id'):
                        warns.append(f'{key}: {e["name"]} ({c}) not in the AniList pool yet (refresh looks it up)')
                if not e.get('chars'):
                    errs.append(f'{key}: {e["name"]} has no AniList character name')
            elif not e.get('chars'):
                warns.append(f'{key}: {e["name"]}: main characters not filled yet (run refresh)')
            if not e.get('wiki'):
                warns.append(f'{key}: {e["name"]}: no Fandom wiki -> the character check has only AniList / quiz pictures')
        # every power list with Rimuru: he is #1 (owner 2026-10-25)
        if T.get('order') == 'power':
            for e in es:
                if any('rimuru' in c.lower() for c in e.get('chars') or []) and e['rank'] != 1:
                    errs.append(f'{key}: Rimuru must be #1 in a power list (owner rule), he is #{e["rank"]}')
            for a, b, why in T.get('above', []):
                if a not in rank or b not in rank:
                    errs.append(f'{key}: above-pair names unknown: {a} / {b}')
                elif rank[a] > rank[b]:
                    errs.append(f'{key}: {a} (#{rank[a]}) must rank above {b} (#{rank[b]}): {why}')
            if not T.get('above'):
                warns.append(f'{key}: power list without any "above" pair: the order is unchecked')
        if T.get('order') == 'fans':
            fav = [(e['name'], (e.get('anilist') or {}).get('favourites')) for e in es]
            for (n1, f1), (n2, f2) in zip(fav, fav[1:]):
                if f1 is not None and f2 is not None and f2 > f1:
                    (warns if T.get('order_locked') else errs).append(f'{key}: {n2} ({f2} favourites) is below {n1} ({f1}): "ranked by fans" must follow the favourites')
        if T.get('order') == 'score':
            sc = [(e['name'], (e.get('anilist') or {}).get('score')) for e in es]
            for (n1, s1), (n2, s2) in zip(sc, sc[1:]):
                if s1 is not None and s2 is not None and s2 > s1 + 2:
                    (warns if T.get('order_locked') else errs).append(f'{key}: {n2} (score {s2}) is below {n1} ({s1})')
    return errs, warns


def show(md=False):
    topics = load()
    lines = []
    for key, T in topics.items():
        rv = revealed_ranks(T)
        ent = ' > '.join(f'#{e["rank"]} {e["name"]}' + ('*' if e['rank'] in rv else '') for e in T['entries'][:slots(T) + 1])
        lines.append(f'{key} [{T["order"]}, {T["reveal"]}]: {ent}')
    print('\n'.join(lines) + '\n(* = shown on screen)')


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'check'
    if cmd == 'refresh':
        refresh()
    elif cmd == 'show':
        show('--md' in sys.argv)
    else:
        e, w = check()
        for x in w:
            print('WARN ', x)
        for x in e:
            print('ERROR', x)
        print(f'{len(load())} topics, {len(e)} errors, {len(w)} warnings')
        sys.exit(1 if e else 0)
