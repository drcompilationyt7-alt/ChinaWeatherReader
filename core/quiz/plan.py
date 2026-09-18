"""
Pick the questions for one World Quiz short.

Formats
  flag     guess the country from its flag            (5 rounds)
  shape    guess the country from its silhouette      (5 rounds)
  capital  name the capital of the shown country      (5 rounds)
  bigger   which of two countries is bigger (area)    (4 rounds)
  crowd    which of two countries has more people     (4 rounds)

Every round ramps in difficulty (easy -> impossible); the last round of a flag
quiz is often a look-alike flag, the most commented-on moment of the genre.
`avoid` holds ISO codes used in recent quizzes so answers do not repeat.
"""
import json
import math
import os
import random

ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'assets')

FORMATS = ('flag', 'shape', 'capital', 'bigger', 'crowd')
THEMES = ('world', 'asia', 'europe', 'africa', 'americas')

LEVELS = {3: ['easy', 'medium', 'insane'], 4: ['easy', 'medium', 'hard', 'insane'],
          5: ['easy', 'easy', 'medium', 'hard', 'insane']}
LEVEL_LABEL = {'easy': 'EASY', 'medium': 'MEDIUM', 'hard': 'HARD', 'insane': 'IMPOSSIBLE'}

# Flags people mix up; the second one is shown, the first is the usual wrong guess.
LOOKALIKES = [('RO', 'TD'), ('MC', 'ID'), ('ID', 'MC'), ('IE', 'CI'), ('IT', 'IE'), ('AU', 'NZ'),
              ('NO', 'IS'), ('SK', 'SI'), ('RU', 'SI'), ('CO', 'EC'), ('SN', 'ML'), ('NL', 'LU'),
              ('HT', 'LI'), ('PL', 'MC'), ('VE', 'EC'), ('GN', 'ML'), ('JO', 'PS'), ('DZ', 'PK')]
# Capitals that trip people up (the usual wrong answer), used for the later rounds.
CAPITAL_TRAPS = {'AU': 'Sydney', 'CA': 'Toronto', 'TR': 'Istanbul', 'BR': 'Rio', 'CH': 'Zurich',
                 'NZ': 'Auckland', 'NG': 'Lagos', 'MA': 'Casablanca', 'VN': 'Ho Chi Minh City', 'IN': 'Mumbai',
                 'US': 'New York', 'PK': 'Karachi', 'MM': 'Yangon', 'KZ': 'Almaty', 'TZ': 'Dar es Salaam',
                 'CI': 'Abidjan', 'BJ': 'Cotonou', 'BZ': 'Belize City', 'ZA': 'Cape Town', 'CN': 'Shanghai',
                 'MX': 'Cancun', 'EC': 'Guayaquil', 'SA': 'Jeddah', 'AE': 'Dubai', 'CM': 'Douala'}
TRICKY_CAPITALS = set(CAPITAL_TRAPS)

MIN_SHAPE_KM2 = 25000     # smaller states are unrecognisable as silhouettes


def load_countries():
    with open(os.path.join(ASSETS, 'countries.json'), encoding='utf-8') as f:
        data = json.load(f)['countries']
    return [c for c in data if c.get('hasFlag')]


def _theme_ok(c, theme):
    if theme == 'world':
        return True
    cont = (c.get('continent') or '').lower()
    if theme == 'americas':
        return cont in ('north america', 'south america')
    return cont == theme


def _pick(pool, level, rng, used, avoid, theme, fallback_order=('easy', 'medium', 'hard', 'insane')):
    order = [level] + [l for l in fallback_order if l != level]
    for lv in order:
        cands = [c for c in pool if c['tier'] == lv and c['iso2'] not in used and _theme_ok(c, theme)]
        fresh = [c for c in cands if c['iso2'] not in avoid]
        if fresh or cands:
            return rng.choice(fresh or cands)
    return None


def plan_single(fmt, rng, avoid, theme, n=5):
    countries = load_countries()
    by = {c['iso2']: c for c in countries}
    if fmt == 'flag':
        pool = countries
    elif fmt == 'shape':
        pool = [c for c in countries if c.get('hasShape') and (c.get('areaKm2') or 0) >= MIN_SHAPE_KM2]
    else:  # capital
        pool = [c for c in countries if c.get('capital')]
    rounds, used = [], set()
    for i, level in enumerate(LEVELS.get(n, LEVELS[5])):
        c = None
        if fmt == 'flag' and level == 'insane' and rng.random() < 0.6:
            pairs = [(a, b) for a, b in LOOKALIKES if b in by and a in by and b not in used and a not in used
                     and _theme_ok(by[b], theme) and b not in avoid]
            if pairs:
                a, b = rng.choice(pairs)
                c = dict(by[b])
                c['decoy'] = by[a]['name']
        if fmt == 'capital' and level in ('medium', 'hard') and c is None:
            tricky = [by[x] for x in TRICKY_CAPITALS if x in by and by[x].get('capital') and x not in used
                      and x not in avoid and _theme_ok(by[x], theme)]
            if tricky:
                c = rng.choice(tricky)
        if c is None:
            c = _pick(pool, level, rng, used, avoid, theme)
        if c is None:
            break
        used.add(c['iso2'])
        r = {'iso2': c['iso2'], 'numeric': c.get('numeric'), 'name': c['name'], 'level': level,
             'levelLabel': LEVEL_LABEL[level], 'capital': c.get('capital'), 'population': c.get('population'),
             'areaKm2': c.get('areaKm2'), 'continent': c.get('continent')}
        if c.get('decoy'):
            r['decoy'] = c['decoy']
        if fmt == 'capital' and c['iso2'] in CAPITAL_TRAPS:
            r['decoy'] = CAPITAL_TRAPS[c['iso2']]
        r['answer'] = c['capital'] if fmt == 'capital' else c['name']
        rounds.append(r)
    return rounds


def _mercator_inflation(c, lat):
    return 1.0 / max(0.2, math.cos(math.radians(lat))) ** 2


def plan_pairs(fmt, rng, avoid, theme, n=4):
    countries = [c for c in load_countries() if c['tier'] in ('easy', 'medium')]
    if fmt == 'bigger':
        countries = [c for c in countries if c.get('hasShape') and (c.get('areaKm2') or 0) >= 20000]
        key = 'areaKm2'
    else:
        countries = [c for c in countries if c.get('population')]
        key = 'population'
    lats = {}
    if fmt == 'bigger':
        import geo
        for c in countries:
            lats[c['iso2']] = geo.centroid_lat(c['numeric'])
    pairs = []
    for i, a in enumerate(countries):
        for b in countries[i + 1:]:
            va, vb = a.get(key) or 0, b.get(key) or 0
            if not va or not vb:
                continue
            ratio = max(va, vb) / min(va, vb)
            if not (1.3 <= ratio <= 4.5):
                continue
            if not (_theme_ok(a, theme) or _theme_ok(b, theme)):
                continue
            big, small = (a, b) if va > vb else (b, a)
            if fmt == 'bigger':
                # surprising when the flat map makes the smaller one look bigger
                pa = big['areaKm2'] * _mercator_inflation(big, lats[big['iso2']])
                pb = small['areaKm2'] * _mercator_inflation(small, lats[small['iso2']])
                surprise = 1.0 if pb > pa else 0.0
            else:
                # surprising when the bigger country has fewer people
                surprise = 1.0 if (small.get('areaKm2') or 0) > (big.get('areaKm2') or 0) else 0.0
            easy = (a['tier'] == 'easy') + (b['tier'] == 'easy')
            fam = (0.12, 0.45, 1.0)[easy]
            w = (0.35 + surprise) * fam * (0.5 if (a['iso2'] in avoid or b['iso2'] in avoid) else 1.0)
            pairs.append((w, a, b))
    rounds, used = [], set()
    for _ in range(n):
        cands = [(w, a, b) for w, a, b in pairs if a['iso2'] not in used and b['iso2'] not in used]
        if not cands:
            break
        tot = sum(w for w, _, _ in cands)
        x = rng.random() * tot
        for w, a, b in cands:
            x -= w
            if x <= 0:
                break
        if rng.random() < 0.5:
            a, b = b, a
        used.update([a['iso2'], b['iso2']])
        win = a if (a.get(key) or 0) > (b.get(key) or 0) else b
        lose = b if win is a else a
        rounds.append({
            'left': {k: a.get(k) for k in ('iso2', 'numeric', 'name', 'population', 'areaKm2')},
            'right': {k: b.get(k) for k in ('iso2', 'numeric', 'name', 'population', 'areaKm2')},
            'winner': 'left' if win is a else 'right',
            'answer': win['name'],
            'ratio': round((win.get(key) or 1) / (lose.get(key) or 1), 1),
            'level': 'medium', 'levelLabel': f'ROUND {len(rounds) + 1}',
        })
    return rounds


def make_plan(fmt, seed=None, avoid=(), theme='world', rounds=None):
    if fmt not in FORMATS:
        raise ValueError(f'unknown format {fmt}')
    rng = random.Random(seed)
    avoid = set(avoid or ())
    if theme not in THEMES:
        theme = 'world'
    if fmt in ('bigger', 'crowd'):
        out = plan_pairs(fmt, rng, avoid, theme, n=min(4, rounds or 4))
    else:
        out = plan_single(fmt, rng, avoid, theme, n=rounds if rounds in LEVELS else 5)
    return {'format': fmt, 'theme': theme, 'seed': seed, 'rounds': out}


if __name__ == '__main__':
    import sys
    fmt = sys.argv[1] if len(sys.argv) > 1 else 'flag'
    print(json.dumps(make_plan(fmt, seed=int(sys.argv[2]) if len(sys.argv) > 2 else 1,
                               theme=sys.argv[3] if len(sys.argv) > 3 else 'world'), indent=1))
