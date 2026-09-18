#!/usr/bin/env python3
"""
Build the offline data pack for the World Quiz shorts (core/quiz/).

Sources (all free to reuse in videos):
  - Wikidata SPARQL (CC0): names, ISO codes, capital, population, area, continent
  - flagcdn.com PNGs (flags are public domain, rendered from Wikimedia SVGs)
  - world-atlas@2 countries-50m.json (Natural Earth, public domain)

Writes core/quiz/assets/{countries.json, countries-50m.json, flags/<iso2>.png}.
Run it once locally and commit the output; re-run to refresh populations.

    python scripts/build-world-data.py            # everything
    python scripts/build-world-data.py --no-flags # data only
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'core', 'quiz', 'assets')
UA = 'MrWorldWideWebsterQuizBuilder/1.0 (https://github.com/drcompilationyt7-alt/ChinaWeatherReader)'
SPARQL = 'https://query.wikidata.org/sparql'
# Denmark's sovereign-state item has no ISO code on Wikidata; its country item (Q35) does.
STATES = '{ ?c wdt:P31 wd:Q3624078 } UNION { VALUES ?c { wd:Q35 } }'

# Disputed or politically loaded entries are left out of every quiz.
EXCLUDE = {'PS', 'TW', 'XK', 'EH'}

NAME_OVERRIDES = {
    'CN': 'China', 'NL': 'Netherlands', 'FM': 'Micronesia', 'GB': 'United Kingdom', 'US': 'United States',
    'KR': 'South Korea', 'KP': 'North Korea', 'CD': 'DR Congo', 'CG': 'Congo', 'CI': 'Ivory Coast',
    'CZ': 'Czechia', 'MK': 'North Macedonia', 'RU': 'Russia', 'VA': 'Vatican City', 'LA': 'Laos',
    'SY': 'Syria', 'IR': 'Iran', 'VN': 'Vietnam', 'BO': 'Bolivia', 'VE': 'Venezuela', 'TZ': 'Tanzania',
    'MD': 'Moldova', 'BN': 'Brunei', 'SZ': 'Eswatini', 'CV': 'Cape Verde', 'TL': 'East Timor',
    'GM': 'Gambia', 'BS': 'Bahamas', 'DK': 'Denmark', 'FR': 'France', 'NZ': 'New Zealand',
    'IE': 'Ireland', 'GE': 'Georgia', 'DO': 'Dominican Republic', 'MM': 'Myanmar', 'ST': 'Sao Tome and Principe',
    'VC': 'St Vincent and the Grenadines', 'KN': 'St Kitts and Nevis', 'LC': 'Saint Lucia', 'TR': 'Turkey',
}
# Countries with more than one capital on Wikidata: the one a quiz should accept.
CAPITAL_OVERRIDES = {
    'ZA': 'Pretoria', 'BO': 'Sucre', 'LK': 'Colombo', 'PK': 'Islamabad', 'YE': "Sana'a",
    'SZ': 'Mbabane', 'NL': 'Amsterdam', 'MY': 'Kuala Lumpur', 'CI': 'Yamoussoukro', 'BJ': 'Porto-Novo',
    'KR': 'Seoul', 'TZ': 'Dodoma', 'NR': 'Yaren', 'CH': 'Bern', 'EC': 'Quito', 'AG': "St. John's",
}
# No capital question for these (contested or ambiguous answers start comment wars).
NO_CAPITAL = {'IL', 'VA'}
# UN World Population Prospects 2024, 2025 estimates, for the countries where
# Wikidata's latest statement is stale enough to flip a comparison.
POPULATION_OVERRIDES = {'IN': 1_463_865_525, 'CN': 1_416_096_094, 'US': 347_275_807, 'PK': 255_219_554,
                        'NG': 237_527_782, 'ID': 285_721_236, 'BR': 212_812_405, 'BD': 175_686_899}
CONTINENT_OVERRIDES = {'TR': 'Asia', 'RU': 'Europe', 'KZ': 'Asia', 'GE': 'Asia', 'AZ': 'Asia', 'AM': 'Asia',
                       'CY': 'Europe', 'EG': 'Africa', 'PA': 'North America', 'ID': 'Asia'}

# Difficulty tiers: how many people in the English-speaking Shorts audience
# recognise a flag / shape / capital. Everything not listed is "hard", and
# small or low-profile states become "insane".
EASY = set('US GB CA JP CN FR DE IT ES BR MX IN AU KR RU AR TR CH SE GR EG PT NL IE JM IL SA ZA NG NZ DK NO FI PL UA KP CU TH VN'.split())
MEDIUM = set('BE AT PH ID PK MA KE ET CO PE CL VE SG MY BD IR IQ AF SY LB AE QA HU CZ RO HR RS IS SK SI BG NP LK MN KZ UZ GH DZ TN LY SD SO TZ UG CM SN CI DO HT PA CR BO PY UY EC GT HN NI SV MT CY LU MC VA BT'.split())


def sparql(query):
    url = SPARQL + '?' + urllib.parse.urlencode({'query': query, 'format': 'json'})
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'application/sparql-results+json'})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.load(r)['results']['bindings']
        except Exception as e:  # rate limits happen, just back off
            print(f'  sparql retry {attempt + 1}: {e}', file=sys.stderr)
            time.sleep(5 * (attempt + 1))
    raise RuntimeError('Wikidata query failed')


def val(b, k):
    return b[k]['value'] if k in b else None


def fetch(url, dest):
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=60) as r, open(dest, 'wb') as f:
        f.write(r.read())


def main():
    os.makedirs(os.path.join(OUT, 'flags'), exist_ok=True)
    base = sparql('''
SELECT ?c ?iso2 ?iso3n ?name ?capLabel ?contLabel ?links WHERE {
  ''' + STATES + ''' ?c wdt:P297 ?iso2; wikibase:sitelinks ?links.
  FILTER NOT EXISTS { ?c wdt:P31 wd:Q3024240 }
  OPTIONAL { ?c wdt:P299 ?iso3n }
  OPTIONAL { ?c wdt:P36 ?cap }
  OPTIONAL { ?c wdt:P30 ?cont }
  ?c rdfs:label ?name FILTER(lang(?name)="en")
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}''')
    pops = sparql('''
SELECT ?iso2 ?pop ?date WHERE {
  ''' + STATES + ''' ?c wdt:P297 ?iso2; p:P1082 ?st.
  ?st ps:P1082 ?pop; wikibase:rank ?rank. FILTER(?rank != wikibase:DeprecatedRank)
  OPTIONAL { ?st pq:P585 ?date }
}''')
    areas = sparql('''
SELECT ?iso2 ?m2 ?rank WHERE {
  ''' + STATES + ''' ?c wdt:P297 ?iso2; p:P2046 ?st.
  ?st psn:P2046/wikibase:quantityAmount ?m2; wikibase:rank ?rank. FILTER(?rank != wikibase:DeprecatedRank)
}''')

    countries = {}
    for b in base:
        iso2 = val(b, 'iso2')
        if not iso2 or iso2 in EXCLUDE:
            continue
        c = countries.setdefault(iso2, {'iso2': iso2, 'name': NAME_OVERRIDES.get(iso2, val(b, 'name')),
                                        'numeric': None, 'capitals': set(), 'continents': set(),
                                        'fame': int(val(b, 'links') or 0), 'qid': val(b, 'c').rsplit('/', 1)[-1]})
        if val(b, 'iso3n'):
            c['numeric'] = val(b, 'iso3n').zfill(3)
        if val(b, 'capLabel') and not val(b, 'capLabel').startswith('Q'):
            c['capitals'].add(val(b, 'capLabel'))
        if val(b, 'contLabel'):
            c['continents'].add(val(b, 'contLabel'))

    latest = {}
    for b in pops:
        iso2, pop, date = val(b, 'iso2'), float(val(b, 'pop')), val(b, 'date') or ''
        if iso2 not in latest or date > latest[iso2][1] or (date == latest[iso2][1] and pop > latest[iso2][0]):
            latest[iso2] = (pop, date)
    area = {}
    for b in areas:
        iso2, km2 = val(b, 'iso2'), float(val(b, 'm2')) / 1e6
        preferred = val(b, 'rank').endswith('PreferredRank')
        cur = area.get(iso2)
        if cur is None or (preferred and not cur[1]) or (preferred == cur[1] and km2 > cur[0]):
            area[iso2] = (km2, preferred)

    out = []
    for iso2, c in sorted(countries.items()):
        conts = {('Oceania' if 'Oceania' in x else x) for x in c['continents']} - {'Eurasia'}
        continent = CONTINENT_OVERRIDES.get(iso2) or (sorted(conts)[0] if conts else None)
        capital = CAPITAL_OVERRIDES.get(iso2) or (sorted(c['capitals'])[0] if len(c['capitals']) == 1 else None)
        if iso2 in NO_CAPITAL:
            capital = None
        pop = POPULATION_OVERRIDES.get(iso2) or latest.get(iso2, (None, ''))[0]
        km2 = area.get(iso2, (None, False))[0]
        tier = 'easy' if iso2 in EASY else 'medium' if iso2 in MEDIUM else 'hard'
        if tier == 'hard' and (c['fame'] < 280 or (pop or 0) < 1_500_000):
            tier = 'insane'
        out.append({
            'iso2': iso2, 'numeric': c['numeric'], 'name': c['name'], 'capital': capital,
            'continent': continent, 'population': int(pop) if pop else None,
            'areaKm2': round(km2) if km2 else None, 'tier': tier, 'fame': c['fame'], 'wikidata': c['qid'],
        })

    shapes_path = os.path.join(OUT, 'countries-50m.json')
    if not os.path.exists(shapes_path):
        print('downloading world-atlas countries-50m.json')
        fetch('https://cdn.jsdelivr.net/npm/world-atlas@2/countries-50m.json', shapes_path)
    shapes = json.load(open(shapes_path, encoding='utf-8'))
    shape_ids = {g.get('id') for g in shapes['objects']['countries']['geometries']}
    for c in out:
        c['hasShape'] = bool(c['numeric'] and c['numeric'] in shape_ids)

    if '--no-flags' not in sys.argv:
        for c in out:
            dest = os.path.join(OUT, 'flags', c['iso2'].lower() + '.png')
            if os.path.exists(dest):
                continue
            try:
                fetch(f"https://flagcdn.com/w640/{c['iso2'].lower()}.png", dest)
            except Exception as e:
                print(f"  flag {c['iso2']} failed: {e}", file=sys.stderr)
            time.sleep(0.05)
    for c in out:
        c['hasFlag'] = os.path.exists(os.path.join(OUT, 'flags', c['iso2'].lower() + '.png'))

    meta = {
        'builtAt': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'sources': {
            'data': 'Wikidata (CC0) - https://www.wikidata.org',
            'flags': 'flagcdn.com (public domain flag renders)',
            'shapes': 'Natural Earth via world-atlas@2 (public domain)',
        },
        'countries': out,
    }
    with open(os.path.join(OUT, 'countries.json'), 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    tiers = {}
    for c in out:
        tiers[c['tier']] = tiers.get(c['tier'], 0) + 1
    print(f"{len(out)} countries, tiers {tiers}, shapes {sum(c['hasShape'] for c in out)}, flags {sum(c['hasFlag'] for c in out)}")
    missing = [c['iso2'] for c in out if not c['capital'] or not c['population'] or not c['areaKm2']]
    if missing:
        print('incomplete:', ' '.join(missing))


if __name__ == '__main__':
    main()
