"""
Age research for characters whose age class is 'unknown' (network only, cached, ~2 requests/s).

Reddit's search JSON needs OAuth (403 without it) and MyAnimeList forums have no search API, so the source is the
character's Fandom wiki ARTICLE BODY: an age statement only counts when the same sentence carries a <ref> citation
to an official source (databook, guidebook, fanbook, volume / chapter, episode, light novel, official site, author
interview). Fan guesses never count.

Results are PROPOSALS, never applied automatically: they go to assets/age-proposals.json for the owner to confirm.
A proposal is 'adult' only when every age found on the page is >= 18 and nothing conflicts (an age under 18, a
school year, a teen role, or a minor word in the AniList description). Minors are never touched.
Run: python scripts/build-pop-media.py --age-research [--minutes 40]
"""
import json
import os
import re
import time
import urllib.parse

import pop_extras as px

OFFICIAL = re.compile(r'databook|data book|guidebook|guide book|fanbook|fan book|character book|official|volume|vol\.|chapter|ch\.|episode|ep\.|'
                      r'light novel|interview|author|mangaka|profile|settei|booklet|q ?& ?a', re.I)
AGE = re.compile(r'\b(\d{1,3})[- ]years?[- ]old\b|\bage[sd]?\s*(?:of|:|is|was)?\s*(\d{1,3})\b|\baged\s+(\d{1,3})\b', re.I)
SCHOOL = re.compile(r'\b(high school|middle school|junior high|elementary|first[- ]year student|second[- ]year student|third[- ]year student|'
                    r'teenager|teenage|minor|child)\b', re.I)
OUT = os.path.join(px.ASSETS, 'age-proposals.json')


def _wikitext(wiki, title):
    q = urllib.parse.urlencode({'action': 'query', 'titles': title, 'redirects': 1, 'prop': 'revisions', 'rvprop': 'content',
                                'rvslots': 'main', 'format': 'json'})
    p = next(iter(px.http(f'https://{wiki}.fandom.com/api.php?{q}', tries=2, timeout=30)['query']['pages'].values()))
    return (p.get('revisions') or [{}])[0].get('slots', {}).get('main', {}).get('*', '')


def _sentences(text):
    text = re.sub(r'\{\{(?!.*?ref)[^{}]*\}\}', ' ', text)
    return re.split(r'(?<=[.!?])\s+|\n+', text)


def assess(c, desc, text):
    """{'proposal': 'adult'|'keep', 'age', 'citation', 'conflict'}"""
    ages, cited = [], []
    for s in _sentences(text):
        found = [int(n) for m in AGE.finditer(re.sub(r'<ref[^>]*>.*?</ref>', ' ', s, flags=re.S)) for n in m.groups() if n]
        found = [n for n in found if 3 <= n <= 2000]
        if not found:
            continue
        ages += found
        refs = re.findall(r'<ref[^>]*>(.*?)</ref>', s, flags=re.S)
        ref = next((r for r in refs if OFFICIAL.search(r)), None)
        if ref:
            cited.append((min(found), re.sub(r'\s+', ' ', re.sub(r'<[^>]+>|\[\[|\]\]|\{\{|\}\}', ' ', ref)).strip()[:160],
                          re.sub(r'<ref.*?</ref>|<[^>]+>|\[\[|\]\]|\{\{|\}\}', ' ', s, flags=re.S).strip()[:200]))
    conflict = []
    if any(a < 18 for a in ages):
        conflict.append(f'age {min(ages)} on the page')
    m = SCHOOL.search(re.sub(r'<ref.*?</ref>', ' ', text, flags=re.S))
    if m:
        conflict.append(f'"{m.group(0)}" on the page')
    m = px.MINOR_WORD.search(str(desc or ''))
    if m:
        conflict.append(f'"{m.group(0)}" in AniList description')
    if px.TIMESKIP.search(text):
        conflict.append('timeskip / older version mentioned')
    if cited and not conflict and all(a >= 18 for a, _, _ in cited):
        a, ref, sent = cited[0]
        return {'proposal': 'adult', 'age': a, 'citation': ref, 'sentence': sent}
    return {'proposal': 'keep', 'cited': [(a, r) for a, r, _ in cited][:2], 'conflict': conflict}


class _OwnCache(px.Cache):
    """Separate file: the pool build writes extras-cache.json at the same time (read-only here)."""
    FILE = os.path.join(px.pv.MODELS, 'age-research-cache.json')

    def __init__(self):
        try:
            self.d = json.load(open(self.FILE, encoding='utf-8'))
        except (OSError, ValueError):
            self.d = {}
        self.n = 0

    def save(self):
        json.dump(self.d, open(self.FILE + '.tmp', 'w', encoding='utf-8'))
        os.replace(self.FILE + '.tmp', self.FILE)


def run(minutes=40):
    shared = px.Cache()          # read only: wiki pages + AniList descriptions from the light phase
    cache = _OwnCache()
    chars = json.load(open(os.path.join(px.ASSETS, 'anime-characters.json'), encoding='utf-8'))['characters']
    todo = [c for c in chars if c.get('age_class') == 'unknown']
    t_end = time.time() + minutes * 60
    props = json.load(open(OUT, encoding='utf-8')) if os.path.exists(OUT) else {}
    for n, c in enumerate(todo):
        if time.time() > t_end:
            px.log('time budget used, rerun to continue')
            break
        key = f"agewiki:{c['id']}"
        r = cache.get(key)
        if r is None:
            w = shared.get(f"wikichar:{c['id']}") or {}
            if not w.get('title'):
                r = {'proposal': 'keep', 'conflict': ['no wiki page']}
            else:
                try:
                    r = assess(c, shared.get(f"desc:{c['id']}"), _wikitext(w['wiki'], w['title']))
                    r['url'] = w.get('page')
                except Exception as e:  # noqa: BLE001
                    r = {'proposal': 'keep', 'conflict': [f'error {str(e)[:40]}']}
                time.sleep(0.5)
            cache.put(key, r)
        if r.get('proposal') == 'adult':
            props[str(c['id'])] = {'name': c['name'], 'anime': c['anime'], 'current': c['age_class'], **r}
        if n % 50 == 0:
            px.log(f'age research {n}/{len(todo)}: {sum(1 for v in props.values())} proposals')
    cache.save()
    json.dump(props, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    px.log(f'{len(props)} adult proposals (NOT applied) -> {OUT}')
    return props
