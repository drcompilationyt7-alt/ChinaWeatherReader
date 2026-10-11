"""
Fan art candidates for the silhouette / eyes / outfit pools from the Danbooru public API (owner decision 2026-10-09).
Snapshot-time only (scripts/build-pop-media.py --newformats2); every picked image still goes through the WD14 gate in
pop_extras / pop_vision, and the owner reviews the gate sheets before anything goes live.

Query rules (anonymous API: 2 tags per search, rating counts as one):
  everyone        "<character> rating:g"
  adults (18+)    also "<character> rating:s"  (swimsuit / bikini / barefoot / bare legs / beach art preferred when it passes)
  never q / e: those ratings are never queried; any post whose rating is not g (or s for adults) is dropped before download.
Post filters (from the post's own tags, before any download):
  exactly one character tag (this one); no cosplay / genderswap / crossover / alternate hair or eye colour / realistic /
  text naming the character or show; solo; no comic / monochrome / greyscale / lineart / sketch; full_body for silhouette / outfit
  apparent age, everyone: loli, shota, child, aged_down, younger, kindergarten*, randoseru, toddler (+ school uniforms for adults)
  extra, adults' swimwear / rating:s posts: also petite / flat_chest (a youthful-looking body in suggestive art is not used)
  minors / unknown: none of the strict tags (swimsuit, bare legs, barefoot, cleavage, ...)
  every ALWAYS_REJECT tag of the WD14 gate (nudity, underwear, see-through, sex acts, ...)
  skipped: banned_artist, no artist tag, paywalled source (fanbox / patreon / fantia / ...), deleted / banned posts
Credit: artist tag + post URL stored with the image (pop-quiz-meta.js prints "Art: <artist>" per round).
Polite: <= 2 requests / second, every API answer cached (extras-cache keys dbtag:<id>, dbposts:<id>:<rating>).
"""
import difflib
import json
import re
import time
import urllib.parse
import urllib.request

import pop_vision as pv
import os as _os

API = 'https://danbooru.donmai.us'
UA = 'AsianPopQuizBuilder/1.0 (https://github.com/drcompilationyt7-alt/ChinaWeatherReader)'
MIN_GAP = float(__import__('os').environ.get('POP_DB_GAP') or 0.5)   # seconds between requests (2 / s; CI shards: POP_DB_GAP=1)
_last = [0.0]

AGE_REJECT = {'loli', 'shota', 'child', 'aged_down', 'younger', 'kindergarten', 'kindergarten_uniform', 'randoseru', 'toddler',
              'oppai_loli', 'female_child', 'male_child'}
ADULT_SCHOOL = {'school_uniform', 'serafuku', 'kindergarten_uniform', 'gakuran', 'sailor_collar'}
YOUTHFUL_SUGGESTIVE = {'petite', 'flat_chest'}
NOT_COLOUR = {'comic', 'monochrome', 'greyscale', 'lineart', 'sketch', 'partially_colored', 'spot_color', '4koma', 'manga_panel_redraw'}
SWIM = {'swimsuit', 'bikini', 'one-piece_swimsuit', 'barefoot', 'bare_legs', 'beach', 'feet'}
# not the character as they are: cosplay / genderswap / crossover (the character tag is on those too), other looks,
# real-life / 3D / doll styles, and text that names the character or the show
NOT_THEM = {'cosplay', 'genderswap', 'genderswap_(mtf)', 'genderswap_(ftm)', 'crossover', 'costume_switch', 'fusion', 'aged_up',
            'age_switch', 'alternate_hair_color', 'alternate_eye_color', 'alternate_skin_color', 'realistic', 'photorealistic',
            'photo_(medium)', 'real_life', '3d', 'figure', 'character_doll', 'chibi', 'pixel_art', 'character_name', 'copyright_name',
            'english_text', 'speech_bubble', 'title', 'logo', 'meme', 'animalization', 'ponification', 'oekaki', 'parody',
            'style_parody', 'multiple_views', 'variations', 'reference_sheet', 'alternate_hairstyle', 'chibi_inset', 'sketch',
            'personification', 'objectification', 'what_if', 'alternate_universe', 'gender_bender'}
# another outfit than the character's own: useless for the outfit / silhouette question (adults' swimwear is the exception)
ALT_LOOK = {'alternate_costume', 'enmaided', 'playboy_bunny', 'casual', 'contemporary', 'adapted_costume',
            'halloween_costume', 'santa_costume', 'christmas', 'wedding_dress', 'costume', 'dressing_another'}
# quality (owner: as good as the official art): finished, clean, high resolution, high score
LOW_QUALITY = {'sketch', 'lineart', 'unfinished', 'lowres', 'jpeg_artifacts', 'traditional_media', '3d', 'photorealistic',
               'ai-generated', 'ai-assisted', 'md5_mismatch', 'bad_id', 'resized', 'upscaled', 'blurry', 'rough', 'work_in_progress'}
MIN_SCORE, GOOD_SCORE, MIN_SIDE = 20, 50, 1000
PAYWALL = re.compile(r'fanbox|patreon|fantia|gumroad|subscribestar|ci-en|boosty|skeb\.jp|fanbox\.cc', re.I)
import os as _os
THEMES = json.load(open(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'themes.json'), encoding='utf-8'))['themes']
THEME_TAGS = {t for th in THEMES.values() for t in (th.get('art') or {}).get('danbooru', []) + (th.get('art') or {}).get('confirm', [])}
FRAMING = {'silhouette': {'full_body'}, 'outfit': {'full_body'}, 'eyes': {'portrait', 'upper_body', 'close-up', 'face', 'looking_at_viewer'}}


def _get(path, params):
    if _os.environ.get('POP_OFFLINE') == '1':   # Kaggle session: only cached answers
        raise RuntimeError(f'offline: danbooru {path}')
    wait = _last[0] + MIN_GAP - time.time()
    if wait > 0:
        time.sleep(wait)
    url = f'{API}/{path}?{urllib.parse.urlencode(params)}'
    for k in range(3):
        _last[0] = time.time()
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': UA}), timeout=30) as r:
                return json.loads(r.read())
        except Exception as e:  # noqa: BLE001
            if k == 2:
                raise RuntimeError(f'danbooru {path}: {str(e)[:60]}')
            time.sleep(3 * (k + 1))


def _letters(s):
    return re.sub(r'[^a-z0-9]+', ' ', s.lower()).split()


GENERIC = {'the', 'season', 'movie', 'anime', 'series', 'part', 'final', 'shippuden', 'brotherhood', 'kai', 'new', 'and', 'with', 'of',
           'no', 'wa', 'ga', 'wo', 'to', 'na', 'ni', 'de', 'my', 'is', 'a'}


def same_show(titles, copyrights):
    """Does any of the post's copyright tags look like one of the show's titles (AniList English / romaji)?"""
    tw = {w for t in titles for w in _letters(t) if len(w) >= 4 and w not in GENERIC}
    for cp in copyrights:
        cw = [w for w in _letters(cp.replace('_', ' ')) if len(w) >= 4 and w not in GENERIC]
        for a in cw:
            for b in tw:
                if a == b or (len(a) >= 5 and len(b) >= 5 and (a[:5] == b[:5])) or difflib.SequenceMatcher(None, a, b).ratio() >= 0.85:
                    return True
    return False


def name_variants(name):
    t = [w for w in re.sub(r'[^\w\s.-]', '', name).lower().replace('.', '').split() if w]
    if not t:
        return []
    out = ['_'.join(t)]
    if len(t) == 2:
        out.append(f'{t[1]}_{t[0]}')
    elif len(t) > 2:
        out.append('_'.join(t[::-1]))
    return out


def find_tag(c, titles, cache):
    """The character's Danbooru tag ('' when none matches both the name and the show), cached as dbtag:<id>.
    An empty answer from the first (autocomplete-only) search is retried once with the wider search (dbtag2:<id> marks it)."""
    key = f"dbtag:{c['id']}"
    hit = cache.get(key)
    if hit or (hit == '' and cache.get(f"dbtag2:{c['id']}")):
        return hit
    try:
        tag = _search_tag(c, titles)
    except RuntimeError:
        return ''   # network: not cached, retried next run
    cache.put(key, tag)
    cache.put(f"dbtag2:{c['id']}", 1)
    return tag


def _qualifier(name):
    m = re.search(r'_\(([^()]*)\)$', name)
    return m.group(1) if m else None


def _is_show(tag, titles):
    """>= 40% (and >= 2) of the tag's 20 newest rating:g posts carry a copyright tag of the show."""
    posts = _get('posts.json', {'tags': f'{tag} rating:g', 'limit': 20})
    cps = [p.get('tag_string_copyright', '').split() for p in posts]
    ok = sum(1 for cp in cps if same_show(titles, cp))
    return bool(cps) and ok >= max(2, 0.4 * len(cps))


def _search_tag(c, titles):
    """Autocomplete on the name variants (5 best matches each); then qualified tags `<name>_(<show>)` for the variants and the
    given name alone (e.g. tsunade_(naruto), fubuki_(one-punch_man)); a qualifier must name the show and the tag's posts must
    be of the show. Raises RuntimeError on network errors (nothing cached then)."""
    tried = set()

    def first_ok(names):
        for t in names:
            if t in tried:
                continue
            tried.add(t)
            q = _qualifier(t)
            if q is not None and not same_show(titles, [q]):
                continue
            if _is_show(t, titles):
                return t
        return ''
    variants = name_variants(c['name'])
    for v in variants:
        res = _get('autocomplete.json', {'search[query]': v, 'search[type]': 'tag_query', 'limit': 10})
        cands = [r for r in res if r.get('category') == 4 and r.get('post_count', 0) >= 5
                 and re.sub(r'_\(.*\)$', '', r.get('antecedent') or r['value']) in (v, re.sub(r'_\(.*\)$', '', r['value']))]
        t = first_ok([r['value'] for r in sorted(cands, key=lambda r: -r.get('post_count', 0))[:5]])
        if t:
            return t
    words = [w for w in re.sub(r'[^\w\s-]', '', c['name']).lower().split() if w]
    given = [words[0]] if len(words) >= 2 and len(words[0]) >= 3 else []
    for v in variants + given:
        res = _get('tags.json', {'search[name_matches]': f'{v}_(*', 'search[category]': 4, 'search[order]': 'count', 'limit': 20})
        names = [t['name'] for t in res if (t.get('post_count') or 0) >= 5 and _qualifier(t['name']) is not None
                 and re.sub(r'_\([^()]*\)$', '', t['name']) == v and same_show(titles, [_qualifier(t['name'])])]
        t = first_ok(names[:3])
        if t:
            return t
    return ''


STATS = {}   # reason -> posts dropped by _clean (funnel; reset by the caller)


CANON_FN = None   # set by pop_extras: (character, cache) -> set of petite / flat_chest tags the OFFICIAL art itself shows


def _canon(c, cache):
    try:
        return set(CANON_FN(c, cache)) if CANON_FN else set()
    except Exception:  # noqa: BLE001
        return set()


def _clean(p, cls, rating, tag=None, min_score=None, alts=(), canon=frozenset()):
    """Keep-or-drop decision on the post's own tags (no download yet). Returns the candidate dict or None.
    The reason of every drop is counted in STATS (funnel for the owner notes)."""
    ms = MIN_SCORE if min_score is None else min_score

    def rej(why):
        STATS[why] = STATS.get(why, 0) + 1
        return None
    adult = cls in pv.ADULT_CLASSES
    if p.get('rating') not in (('g', 's') if adult else ('g',)) or p.get('rating') != rating:
        return rej('rating')
    if p.get('is_deleted') or p.get('is_banned') or not p.get('file_url') or p.get('file_ext') not in ('jpg', 'jpeg', 'png', 'webp'):
        return rej('deleted / no file')
    tags = set(p.get('tag_string_general', '').split())
    meta = set(p.get('tag_string_meta', '').split())
    artist = p.get('tag_string_artist', '').split()
    if 'banned_artist' in meta or not artist or PAYWALL.search(p.get('source') or ''):
        return rej('artist / paywall')
    if (tags | meta) & LOW_QUALITY:
        return rej('low-quality tag')
    if (p.get('score') or 0) < ms:
        return rej(f'score < {ms}')
    if min(p.get('image_width', 0), p.get('image_height', 0)) < MIN_SIDE:
        return rej('resolution < 1000')
    if 'solo' not in tags:
        return rej('not solo')
    if tags & NOT_COLOUR:
        return rej('not colour')
    if tags & AGE_REJECT:
        return rej('age tag')
    if tags & NOT_THEM:
        return rej('not them (cosplay / chibi / ...)')
    chars = p.get('tag_string_character', '').split()
    if tag and not (len(chars) == 1 and chars[0] in ((tag,) + tuple(alts))):   # exactly this one character on the post
        return rej('other / several characters')
    if tags & {k for k in pv.ALWAYS_REJECT}:
        return rej('safety tag')
    swim = sorted(tags & SWIM)
    if adult:
        if tags & ADULT_SCHOOL:
            return rej('school uniform (adult)')
        if (swim or rating == 's') and tags & (YOUTHFUL_SUGGESTIVE - set(canon)):
            return rej('fan art draws them flatter / smaller than the official art')
    elif tags & pv.STRICT_REJECT:
        return rej('strict tag')
    url = p.get('large_file_url') if (p.get('image_width', 0) > 1600 or p.get('file_size', 0) > 3_000_000) else p['file_url']
    w, h = p.get('image_width', 0), p.get('image_height', 0)
    if p.get('large_file_url') and url == p['large_file_url'] and w > 850:
        w, h = 850, round(h * 850 / w)
    return {'url': url, 'w': w, 'h': h, 'res': f"{p.get('image_width')}x{p.get('image_height')}", 'post': f"{API}/posts/{p['id']}", 'id': p['id'], 'artist': ' '.join(artist), 'score': p.get('score', 0),
            'rating': rating, 'swim': bool(swim), 'swim_tags': swim, 'origin': (p.get('source') or '')[:200],
            'framing': sorted(tags & (set().union(*FRAMING.values()) | {'cowboy_shot', 'standing', 'feet_out_of_frame', 'legs_out_of_frame', 'upper_body', 'portrait'})), 'alt': bool(tags & ALT_LOOK),
            'swimwear': bool(tags & {'swimsuit', 'bikini', 'one-piece_swimsuit'}), 'ttags': sorted(tags & THEME_TAGS),
            'srctype': 'GAME' if 'game_cg' in tags else 'PROMO' if 'official_art' in tags else 'FAN ART'}


def posts(c, tag, cls, cache, extra=None, min_score=None):
    """Candidate posts (filtered as above) for the character, best first: score >= 20 first, adults' swimwear first."""
    out = []
    adult = cls in pv.ADULT_CLASSES
    canon = _canon(c, cache) if adult else set()
    for rating in (('g', 's') if adult else ('g',)):
        key = f"{'dbposts5' if not extra else 'dbofficial1_' + extra}:{c['id']}:{rating}:{'a' if adult else 's'}{'c' if canon else ''}"
        raw = cache.get(key)
        if raw is None:
            try:
                res = _get('posts.json', {'tags': f'{tag} {extra or ""} rating:{rating}'.replace('  ', ' '), 'limit': 200})
            except RuntimeError:
                continue
            raw = [x for x in (_clean(p, cls, rating, tag, canon=canon, min_score=min_score) for p in res) if x]
            cache.put(key, raw)
        out += raw
    # a stricter class than at query time (age class changed) never keeps s / swimwear posts
    if not adult:
        out = [x for x in out if x['rating'] == 'g' and not x['swim']]
    out.sort(key=lambda x: (-(adult and x['swim']), -(x['score'] >= GOOD_SCORE), -x['score']))   # >= 50 first, >= 20 as the fallback
    return out


def young_share(c, titles, cache):
    """Share of the character's Danbooru posts tagged loli / shota (counts only, no posts fetched), or None (no tag / network).
    Cached as dbyouth2:<id>. pop_extras.rule_class turns >= 1% into an explicit override entry pending owner review."""
    key = f"dbyouth2:{c['id']}"
    hit = cache.get(key)
    if hit is not None:
        return hit
    tag = find_tag(c, titles, cache)
    if not tag:
        return None
    try:
        n = _get('counts/posts.json', {'tags': tag})['counts']['posts'] or 0
        y = sum(_get('counts/posts.json', {'tags': f'{tag} {t}'})['counts']['posts'] or 0 for t in ('loli', 'shota'))
    except (RuntimeError, KeyError, TypeError):
        return None
    out = round(y / n, 4) if n else 0.0
    cache.put(key, out)
    return out


OFFICIAL_TAGS = ('official_art', 'game_cg')   # Danbooru tags for promotional art / game art (2-tag limit: character + this)
OFFICIAL_MIN_SCORE = 5


def _licence(x):
    if x.get('srctype', 'FAN ART') == 'FAN ART':
        return f"Fan art by {x['artist']} (credited, post {x['post']}); character (c) its rights holders"
    return f"Official {'game' if x['srctype'] == 'GAME' else 'promotional'} art ({x['artist']}, post {x['post']}); (c) its rights holders, shown only to identify the character"


def candidates(c, fmt, titles, cache, limit=4, cls=None, official=True, only_official=False):
    """[(url, source dict, swim flag)] of fan art for one format, best first; [] when the character has no tag / no post."""
    cls = cls or c.get('age_class')
    if cls not in ('adult', 'adult-modest', 'adult-strict', 'minor', 'unknown'):
        return []
    tag = find_tag(c, titles, cache)
    if not tag:
        return []
    want = FRAMING[fmt]
    out = []
    # official promotional art (Danbooru official_art) and game art (game_cg: gacha / event versions) come first, same filters;
    # a lower score bar (promo art is rarely voted), the on-model check and the WD14 gate run later as for every source. No figurines.
    offi = []
    if official or only_official:
        for ex in OFFICIAL_TAGS:
            offi += posts(c, tag, cls, cache, extra=ex, min_score=OFFICIAL_MIN_SCORE)
        offi.sort(key=lambda x: -x['score'])
    for x in offi + ([] if only_official else posts(c, tag, cls, cache)):
        if not set(x['framing']) & want or x['h'] < (600 if fmt != 'eyes' else 400):
            continue
        if fmt in ('silhouette', 'outfit') and not 1.15 <= x['h'] / max(1, x['w']) <= 4.6:
            continue
        # recognisable only: another costume than the character's own is skipped (every format); adults' real swimwear excepted
        if x.get('alt', True) and not (cls in pv.ADULT_CLASSES and x.get('swimwear')):
            continue
        src = {'kind': 'danbooru', 'post': x['post'], 'file': x['url'], 'artist': x['artist'], 'origin': x['origin'], 'rating': x['rating'],
               'score': x['score'], 'res': x.get('res'), 'srctype': x.get('srctype', 'FAN ART'),
               'tag': tag, 'licence': _licence(x)}
        if x in offi and x.get('srctype') == 'FAN ART':
            continue
        out.append((x['url'], src, x['swim']))
        if len(out) >= limit:
            break
    return out


COSTUME_ALT = re.compile(r'swim|summer|beach|bikini|resort|pool|sea|ocean|vacation', re.I)


def costume_tags(c, tag, cache):
    """Official themed costume character tags of this character (e.g. `mash_kyrielight_(swimsuit_of_perpetual_summer)`):
    `<tag base>_(*)` tags whose qualifier names a swimsuit / summer / beach look. Cached as dbalt:<id>."""
    key = f"dbalt:{c['id']}"
    hit = cache.get(key)
    if hit is not None:
        return hit
    base = re.sub(r'_\([^()]*\)$', '', tag)
    try:
        res = _get('tags.json', {'search[name_matches]': f'{base}_(*)', 'search[category]': 4, 'search[order]': 'count', 'limit': 40})
    except RuntimeError:
        return []
    out = [t['name'] for t in res if t['name'] != tag and (t.get('post_count') or 0) >= 3
           and re.sub(r'_\([^()]*\)$', '', t['name']) == base and COSTUME_ALT.search(_qualifier(t['name']) or '')][:4]
    cache.put(key, out)
    return out


def theme_candidates(c, fmt, titles, cache, theme, cls, limit=None, per_tag=None):
    """Fan art for a themed edition: `<character> <theme tag> rating:g score:>=N` (+ `rating:s` for adult rules) for the
    theme's first `query_tags` Danbooru tags (default 2; rating: and score: are not counted in the anonymous 2-tag limit;
    q / e are never queried), 200 posts per query (the 60 best scored are kept), every filter above except the costume rule
    (the costume IS the theme). Adults get up to `fan_limit_adult` candidates (default 6). [(url, source, swim)] best first.
    Optional art keys (Beach Day, quality / framing only, never safety / age): `min_score` (default 20), `costume_tags` (also query
    the character's official swimsuit / summer costume tags), `loose_framing` (character: any framing; silhouette: full body or a
    standing figure; outfit: full body or only the feet cut off)."""
    tag = find_tag(c, titles, cache)
    if not tag:
        return []
    art = theme['art']
    want = set(art['danbooru'])
    adult = cls in pv.ADULT_CLASSES
    per_tag = per_tag or art.get('query_tags', 2)
    limit = limit or (art.get('fan_limit_adult', 6) if adult else 6)
    ms = art.get('min_score', MIN_SCORE)
    canon = _canon(c, cache) if adult else set()
    kp = ('dbtheme3' if ms == MIN_SCORE else f'dbtheme4_{ms}') + ('c' if canon else '')
    out, seen = [], set()
    queries = list(art['danbooru'][:per_tag])
    # full-body themed art is rare among the newest themed posts: also scan the character's full_body posts and keep the
    # ones that carry a theme tag (themes.json art.framing_query; silhouette / outfit only)
    if art.get('framing_query') and fmt in ('silhouette', 'outfit'):
        queries.append('full_body')
    queries += list(OFFICIAL_TAGS)   # official promotional / game art that also carries a theme tag (kept by the theme-tag check below)
    names = [(tag, ())]
    alts = costume_tags(c, tag, cache) if art.get('costume_tags') else []
    for at in alts:
        names.append((at, tuple(alts) + (tag,)))
    for nm, al in names:
        for ttag in (queries if nm == tag else queries[:2]):
            for rating in (('g', 's') if adult else ('g',)):
                key = f"{kp}:{c['id']}:{nm if nm != tag else ''}{ttag}:{rating}:{'a' if adult else 's'}"
                hit = cache.get(key)
                if hit is None:
                    try:
                        res = _get('posts.json', {'tags': f'{nm} {ttag} rating:{rating} score:>={ms}', 'limit': 200})
                    except RuntimeError:
                        continue
                    hit = sorted(({**x, 'costume': nm != tag} for x in (_clean(p, cls, rating, nm, min_score=ms, alts=al, canon=canon) for p in res)
                                  if x and (ttag in want or set(x.get('ttags') or []) & want or nm != tag)), key=lambda x: -x['score'])[:60]
                    cache.put(key, hit)
                for x in hit:
                    if x['id'] not in seen:
                        seen.add(x['id'])
                        out.append(x)
    loose = art.get('loose_framing')
    keep = []
    for x in sorted(out, key=lambda x: (-(x['score'] >= GOOD_SCORE), -x['score'])):
        fr, ar = set(x['framing']), x['h'] / max(1, x['w'])
        if loose:
            if fmt == 'silhouette':
                okf = 'full_body' in fr or ('standing' in fr and not fr & {'upper_body', 'portrait'} and ar >= 1.3)
            elif fmt == 'outfit':
                okf = 'full_body' in fr or (fr & {'feet_out_of_frame', 'legs_out_of_frame'} and not fr & {'upper_body', 'portrait'} and ar >= 1.6)
            else:
                okf = True
        else:
            # character rounds show the top of the picture in the card: full-body / cowboy-shot themed art works too
            okf = bool(fr & (FRAMING['silhouette'] if fmt in ('silhouette', 'outfit') else FRAMING['eyes'] | {'full_body', 'cowboy_shot'}))
        if not (set(x.get('ttags') or []) & want or x.get('costume')):
            continue
        if not okf:
            STATS['framing'] = STATS.get('framing', 0) + 1
            continue
        if not adult and (x['rating'] != 'g' or x['swim']):
            continue
        if fmt in ('silhouette', 'outfit') and not 1.15 <= ar <= 4.6:
            continue
        src = {'kind': 'danbooru', 'post': x['post'], 'file': x['url'], 'artist': x['artist'], 'origin': x['origin'], 'rating': x['rating'],
               'score': x['score'], 'res': x.get('res'), 'tag': tag, 'theme_tags': x.get('ttags'), 'srctype': x.get('srctype', 'FAN ART'),
               'licence': _licence(x)}
        keep.append((x['url'], src, x['swim']))
        if len(keep) >= limit:
            break
    return keep
