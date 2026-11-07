#!/usr/bin/env python3
"""Ranking compilation: candidates -> mining -> staging -> review -> import -> weekly assembly (2026-10-25).

The daily video never downloads. Mining (one light GitHub job a week in pool-mine-nightly's rotation, or the PC idle
miner) searches candidate edits per ENTRY, runs every gate, cuts only the chosen phrase window (+ MARGIN s each side) and
STAGES it with its proof. The owner reviews; import moves the item into the live pool; the daily job (weekly slot,
pipeline/newtype-pipeline.js runRanking) assembles a topic only from reviewed items.

Layout ($EDIT_POOL_DIR; the live pool travels in the release "edit-pools" as pool-ranking-pool.tar via
scripts/edit-pools-store.js, preset "ranking"):
  ranking/pool/clips.json          live items (status reviewed); clips/<id>.mp4 = the window + margin
  _staged/ranking/clips.json       staged items (status staged / rejected), clips/, sheets/<id>.jpg, proofs/<source>.json
  _staged/ranking/state.json       candidate URL lists per entry (metadata only) + every checked source's verdict
                                   (CI: the asset ranking-state.json on the release edit-pools-staging)
Entries are pooled by ENTRY KEY (name + show), so a Gojo segment serves every list that has Gojo. Library mining (default,
owner 2026-10-31): per SHOW, whole edits, every good section tagged: serves = the characters that OWN it + the show's genre
entry, themes = every theme it is on par with (action / beauty / romance); a list picks by key + its theme (usable).

Item: {id, entry_key, entry_name, kind, anime, file, start, end (in the source), clip_in, clip_out (the window inside the
clip), source_id, source_title, source_channel, editor_handle, upload_date, age_days, views, char_conf, char_method,
char_votes, eventful, song, song_name, quality, rank_score, safety, watermark, editor_tags, rights, proof, status, added,
reviewed_by}

    python core/edit/ranking_pool.py plan                         # which entries block which topics (mining order)
    python core/edit/ranking_pool.py plan-shows                   # library mining order (shows whose sections unblock most)
    python core/edit/ranking_pool.py mine --budget-min 60         # search + gates + stage (YouTube, spare cookies, paced)
    python core/edit/ranking_pool.py publish --dest DIR --label ci   # staged batch -> ranking-cand-<date>-<label>-n<N>.tar
    python core/edit/ranking_pool.py pull-staged                  # PC: the release's cand tars -> _staged/ranking
    python core/edit/ranking_pool.py review                       # staged items + their sheets
    python core/edit/ranking_pool.py import --ids a,b --reject c [--by owner]
    python core/edit/ranking_pool.py push-state                   # PC: decisions -> ranking-state.json on the release
    python core/edit/ranking_pool.py eligible [--json]            # topics the daily job may build now
    python core/edit/ranking_pool.py assemble --topic T --out f.mp4 [--mark-used]   (--topic auto = the weekly rotation)
"""
import argparse
import glob
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tarfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import ranking_topics as RT  # noqa: E402

PUB_MAX_MB = 1800             # one staging tar stays under the release's 2 GiB per-file limit
MARGIN = 1.5                 # s kept on each side of the chosen window (re-cut room), the rest of the edit is never stored
SEG = (18.0, 24.0)           # phrase-aligned window per entry (3 revealed entries ~ 60 s)
SECTIONS = 4                 # good non-overlapping sections staged per edit (owner 2026-10-28)
LIB_SECTIONS = 6             # library mining: every good section of a whole edit (owner 2026-10-31)
REUSE_GAP = 8                # an edit (source) is not reused within the next 8 ranking uploads
TOPIC_GAP = 4                # a topic is not repeated within the next 4 ranking uploads (when another is eligible)
PER_ENTRY_TARGET = 2         # reviewed + staged items wanted per entry (variety under the reuse gap)
CHAR_MIN = 0.6
CAST_MIN_STRONG = float(os.environ.get('RANKING_CAST_MIN_STRONG', '0.5'))   # strongest lists: the entry's screen share
COUPLE_MEMBER_MIN = float(os.environ.get('RANKING_COUPLE_MEMBER_MIN', '0.1'))   # couples: each member's screen share
COUPLE_SUM_MIN = float(os.environ.get('RANKING_COUPLE_SUM_MIN', '0.45'))        # ... and both together
GAP_MAX_STRONG = float(os.environ.get('RANKING_GAP_MAX_STRONG', '4.0'))    # ... nor a stretch of people the tagger cannot name
RUN_MAX_STRONG = float(os.environ.get('RANKING_RUN_MAX_STRONG', '2.5'))    # ... and no other character's run longer than this (s)
STATUS_RANK = {'gate_reject': 0, 'candidate': 0, 'staged': 1, 'rejected': 2, 'reviewed': 3}


def log(*a):
    print(time.strftime('%H:%M:%S'), *a, file=sys.stderr, flush=True)


def rj(p, d):
    try:
        return json.load(open(p, encoding='utf-8'))
    except Exception:  # noqa: BLE001
        return d


def wj(p, d):
    os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
    tmp = p + '.tmp'
    json.dump(d, open(tmp, 'w', encoding='utf-8'), indent=1, ensure_ascii=False, default=str)
    os.replace(tmp, p)


def pool_root():
    return os.path.abspath(os.environ.get('EDIT_POOL_DIR') or os.path.join(ROOT, 'output', 'edit-pools'))


def live_dir():
    return os.path.join(pool_root(), 'ranking', 'pool')


def staged_dir():
    return os.path.join(pool_root(), '_staged', 'ranking')


def entry_key(e):
    import char_refs
    show = e.get('anime') or ''
    return char_refs.key_of(f'{e["name"]}__{RT_show(show)}') if e.get('kind') != 'anime' else char_refs.key_of(f'anime__{RT_show(show)}')


def RT_show(anime):
    a = re.sub(r':.*$', '', (anime or '').lower())
    a = re.sub(r'\s*(season \d+|\d+(st|nd|rd|th) season|part \d+|the movie.*|\d+)$', '', a).strip()
    return re.sub(r'\s*(-.*-|\(.*\))\s*$', '', a).strip()


def manifest(d):
    return rj(os.path.join(d, 'clips.json'), {'version': 2, 'items': []})


def save_manifest(d, m):
    m['built'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    wj(os.path.join(d, 'clips.json'), m)


def ledger():
    p = os.path.join(os.environ.get('MEMORY_DIR') or os.path.join(ROOT, 'memory'), 'edit-pool-used.json')
    return p, rj(p, {'version': 1, 'items': {}, 'moments': {}})


def blocklist():
    p = os.path.join(HERE, 'refs', 'ranking-editor-blocklist.json')
    return {k.lower().lstrip('@') for k in (rj(p, {}).get('editors') or {})}


def all_entries(topics=None):
    """entry_key -> (an entry dict, [topics it appears in])"""
    topics = topics or RT.load()
    out = {}
    for tk, T in topics.items():
        for e in T['entries']:
            k = entry_key(e)
            if k not in out:
                out[k] = (e, [])
            out[k][1].append(tk)
    return out


# ------------------------------------------------------------------ eligibility / planning

def lib_show(anime):
    """One key per show for the library ('ATTACK ON TITAN Season 3' = 'Attack on Titan', 'Re:ZERO -Starting Life...-' =
    're:zero'; a subtitle after ':' goes unless the part before it is a short title like 'Re')."""
    a, prev = (anime or '').lower().strip(), None
    a = re.sub(r'\s+-\S.*$', '', a)                                   # "konosuba -god's blessing ...!"
    while a != prev:                                                  # "attack on titan season 3 part 2"
        prev = a
        a = re.sub(r'\s*(\(.*\)|season \d+|\d+(st|nd|rd|th) season|final season.*|part \d+|the movie.*)\s*$', '', a)
    if ':' in a and len(a.split(':')[0].strip()) >= 4:
        a = a.split(':')[0]
    return re.sub(r'\s+\d+$', '', a).strip()


CHARFIX_DAY = '2026-11-08'


def charfix_retry(v):
    """A library edit the character gate rejected before the tag fix (2a46cac, 2026-11-07: Yor / Zero Two / Rem / Sukuna ...
    had 1-picture banks): evaluated once more when a search finds it again; the new verdict carries a later date."""
    return (v.get('status') == 'gate_reject' and v.get('lib') and str(v.get('at') or '') < CHARFIX_DAY
            and 'character' in (v.get('windows') or []))


def shows(topics=None):
    """The library's mining unit (owner 2026-10-31: download whole edits of a show, then classify every good section):
    show key -> {'show': its genre entry (from a genre topic, else built from the cast), 'cast': its character entries}."""
    topics = topics or RT.load()
    out = {}
    for k, (e, tks) in all_entries(topics).items():
        g = out.setdefault(lib_show(e.get('anime')), {'show': None, 'cast': [], 'topics': set(), 'couples': []})
        g['topics'].update(tks)
        if e.get('kind') == 'anime':
            g['show'] = g['show'] or e
        elif e.get('kind') == 'couple':
            g['couples'].append(e)          # owner 2026-11-07: 'A x B' entries; both members are measured as cast below
        else:
            g['cast'].append(e)
    for sk, g in out.items():
        # a couple member no list names yet joins the show's cast (synthetic): the miner then measures its screen share
        for cp in g['couples']:
            for m in cp.get('members') or []:
                if not any(c['name'] == m['name'] for c in g['cast']):
                    me = dict(m, kind='character', anime=cp.get('anime'), wiki=cp.get('wiki'), pace=cp.get('pace', 'calm'),
                              rank=0, synthetic_member=True)
                    me.setdefault('search', m['name'].lower())
                    me.setdefault('q', [f"{me['search']} edit 4k", f"{me['search']} edit"])
                    me['alias'] = RT._alias(me)
                    g['cast'].append(me)
    for sk, g in out.items():
        if g['show'] is None:              # no genre list names this show yet: its sections still serve its characters
            c0 = g['cast'][0]
            g['show'] = {'name': sk.upper(), 'kind': 'anime', 'anime': c0.get('anime'), 'search': sk, 'wiki': c0.get('wiki'),
                         'q': [f'{sk} edit 4k'], 'alias': [re.escape(sk)], 'pace': 'action',
                         'chars': list(dict.fromkeys(c for e in g['cast'] for c in e.get('chars', []))), 'synthetic': True}
    return out


def item_themes(it):
    return it.get('themes') or ([it['theme']] if it.get('theme') else None)


def serve_keys(it, smap=None):
    """The entry keys an item can fill: library items carry 'serves' (the characters that OWN the section + the show's
    genre entry); an older per-entry item serves its entry and, being that show's footage, the show's genre entry.
    Couples (owner 2026-11-07): a section serves 'A x B' when BOTH members hold >= COUPLE_MEMBER_MIN of its frames and
    together >= COUPLE_SUM_MIN (measured 'cast' shares; an edit of both of them, not one with a cameo)."""
    g = (smap or {}).get(lib_show(it.get('anime')))
    if it.get('serves'):
        keys = list(it['serves'])
    else:
        keys = [it['entry_key']] if (it.get('char_conf') or 0) >= CHAR_MIN or it.get('title_trusted') else []
        if keys and g and not g['show'].get('synthetic') and entry_key(g['show']) not in keys:
            keys.append(entry_key(g['show']))
    cast = it.get('cast') or {}
    for cp in (g or {}).get('couples') or []:
        sh = [float(cast.get(m['name']) or 0.0) for m in cp.get('members') or []]
        if len(sh) == 2 and min(sh) >= COUPLE_MEMBER_MIN and sum(sh) >= COUPLE_SUM_MIN and entry_key(cp) not in keys:
            keys.append(entry_key(cp))
    return keys


def topic_theme(T):
    try:
        import ranking_profile as RP
        return RP.theme_of(T.get('key'), T)
    except Exception:  # noqa: BLE001  (no analysis deps: no theme filter)
        return None


def usable(items, key, led, exclude_sources=(), theme=None, smap=None):
    """Reviewed items that may fill an entry now: they serve its key, fit the topic's theme (a library section records
    every theme it is on par with), are not within the reuse gap, editor not blocklisted. Best first (rank score)."""
    recent = set((led.get('ranking_recent') or [])[-REUSE_GAP:]) | set(exclude_sources)
    bl = blocklist()
    out = [it for it in items if it.get('status') == 'reviewed' and key in serve_keys(it, smap)
           and (theme is None or item_themes(it) is None or theme in item_themes(it))
           and it['source_id'] not in recent and (it.get('editor_handle') or '').lower().lstrip('@') not in bl]
    # anime-footage edits first (owner 2026-10-25: a manga-panel edit is only a fallback), then the rank score
    return sorted(out, key=lambda it: (bool(it.get('manga')), -(it.get('rank_score') or it.get('quality') or 0)))


def pick_items(T, items, led):
    """One item per revealed rank, distinct sources AND distinct editors (never two excerpts of one editor in a video).
    -> {rank: item} or None."""
    picks, used_src, used_ed = {}, set(), set()
    th, smap = topic_theme(T), shows()
    for rank in RT.revealed_ranks(T):
        e = T['entries'][rank - 1]
        cands = [it for it in usable(items, entry_key(e), led, used_src, th, smap)
                 if (it.get('editor_handle') or it.get('source_channel') or '').lower() not in used_ed]
        if th == 'action' and e.get('kind') != 'anime':
            # owner 2026-11-07 (first 'strongest' sample): a strongest-character entry must be THAT character's edit - he
            # holds the majority of the frames (the Madara section, 0.55, "is a good example despite other characters");
            # a mixed couple / ensemble section (Sung Jinwoo 0.40 + Cha Hae-in) belongs to a couple / romance list instead.
            # Older per-entry items have no cast measure (mined for that character): kept.
            # owner 2026-11-07 (2nd note): short cutaways to another character are fine, a sustained stretch of another one
            # (a complementary sub-edit: Cha Hae-in in the Jinwoo section, other JJK cast in the Gojo one) is not:
            # the longest continuous run owned by others <= RUN_MAX_STRONG s
            cands = [it for it in cands if ((it.get('cast') or {}).get(e['name']) is None
                     or float((it.get('cast') or {}).get(e['name']) or 0.0) >= CAST_MIN_STRONG)
                     and float((it.get('cast_runs') or {}).get(e['name']) or 0.0) <= RUN_MAX_STRONG
                     # owner 2026-11-07 (2nd sample: the Gojo section "started becoming a Yuta edit"): the tagger does not know
                     # Yuta, so his frames were no one's; a tagger-known target's stretch without him <= GAP_MAX_STRONG s
                     and float((it.get('cast_gaps') or {}).get(e['name']) or 0.0) <= GAP_MAX_STRONG]
        if not cands:
            return None
        it = cands[0]
        picks[rank] = it
        used_src.add(it['source_id'])
        used_ed.add((it.get('editor_handle') or it.get('source_channel') or '').lower())
    return picks


def eligible_topics(topics=None, items=None, led=None):
    topics = topics or RT.load()
    items = items if items is not None else manifest(live_dir())['items']
    led = led if led is not None else ledger()[1]
    return [k for k, T in topics.items() if pick_items(T, items, led)]


def choose_topic(topics=None):
    """The weekly rotation: an eligible topic not used in the last TOPIC_GAP ranking uploads, least recently used first."""
    topics = topics or RT.load()
    _, led = ledger()
    el = eligible_topics(topics, led=led)
    if not el:
        return None
    hist = led.get('ranking_topics_recent') or []
    fresh = [k for k in el if k not in hist[-TOPIC_GAP:]] or el
    last_used = {k: (len(hist) - 1 - hist[::-1].index(k)) if k in hist else -1 for k in fresh}
    return sorted(fresh, key=lambda k: (last_used[k], list(topics).index(k)))[0]


def entry_theme(topic_keys, topics=None):
    """The reference-profile theme of an entry: the strictest of its topics' themes (action > beauty > romance)."""
    import ranking_profile as RP
    topics = topics or RT.load()
    th = [RP.theme_of(k, topics.get(k)) for k in topic_keys] or ['action']
    return min(th, key=lambda t: ['action', 'beauty', 'romance'].index(t))


def plan(topics=None):
    """Mining order: entries that block topics. Each missing revealed entry of a topic scores 1 / (missing entries of
    that topic): an entry that alone blocks a topic (the Yuta case) comes first. Entries short of PER_ENTRY_TARGET
    reviewed + staged items keep a small score (variety)."""
    topics = topics or RT.load()
    live = manifest(live_dir())['items']
    st = manifest(staged_dir())['items']
    have, anime = {}, {}
    merged = {it['id']: it for it in st}
    merged.update({it['id']: it for it in live})          # the live record (rescreened: manga flag) wins
    smap = shows(topics)
    for it in merged.values():
        if it.get('status') in ('reviewed', 'staged'):
            for k in serve_keys(it, smap) or [it['entry_key']]:     # a library section counts for every entry it serves
                have[k] = have.get(k, 0) + 1
                if not it.get('manga'):
                    anime[k] = anime.get(k, 0) + 1
    score, info = {}, {}
    for tk, T in topics.items():
        ranks = RT.revealed_ranks(T)
        keys = [entry_key(T['entries'][r - 1]) for r in ranks]
        missing = [k for k in keys if not have.get(k)]
        for r, k in zip(ranks, keys):
            info.setdefault(k, {'entry': T['entries'][r - 1], 'topics': []})['topics'].append(tk)
            if k in missing:
                score[k] = score.get(k, 0) + 1.0 / len(missing)
            elif not anime.get(k):
                # only a manga-panel edit so far (owner: a fallback): look for an anime-footage replacement
                score[k] = score.get(k, 0) + 0.3
            elif have.get(k, 0) < PER_ENTRY_TARGET:
                score[k] = score.get(k, 0) + 0.05
    order = sorted(score, key=lambda k: -score[k])
    return [{'key': k, 'name': info[k]['entry']['name'], 'score': round(score[k], 2), 'have': have.get(k, 0),
             'topics': info[k]['topics'], 'entry': info[k]['entry']} for k in order]


def plan_shows(topics=None):
    """Library mining order (owner 2026-10-31): a show scores the plan scores of its entries (its characters + its genre
    entry), so the show whose sections unblock the most topics is mined first; every other show follows (variety)."""
    topics = topics or RT.load()
    smap = shows(topics)
    sc = {k: 0.0 for k in smap}
    for p in plan(topics):
        k = lib_show(p['entry'].get('anime'))
        if k in sc:
            sc[k] += p['score']
    return [{'show': k, 'name': smap[k]['show']['name'], 'score': round(sc[k], 2), 'cast': [c['name'] for c in smap[k]['cast']],
             'topics': sorted(smap[k]['topics'])} for k in sorted(sc, key=lambda k: -sc[k])]


# ------------------------------------------------------------------ mining

def state_path(p=None):
    return p or os.path.join(staged_dir(), 'state.json')


def merge_state(a, b):
    """Union of two states; per source the 'strongest' status wins (reviewed > rejected > staged > gate_reject)."""
    out = {'candidates': dict(a.get('candidates') or {}), 'sources': dict(a.get('sources') or {})}
    for k, v in (b.get('candidates') or {}).items():
        if k not in out['candidates'] or (v.get('at') or '') > (out['candidates'][k].get('at') or ''):
            out['candidates'][k] = v
    for sid, v in (b.get('sources') or {}).items():
        o = out['sources'].get(sid)
        if not o or STATUS_RANK.get(v.get('status'), 0) >= STATUS_RANK.get(o.get('status'), 0):
            out['sources'][sid] = v
    return out


def candidates(e, RC, st, refresh_days=14):
    """Cheap candidate URL list of an entry (search metadata only, cached in the state for refresh_days)."""
    k = entry_key(e)
    c = st['candidates'].get(k)
    qs = RC.queries(e)
    if c and time.time() - c.get('ts', 0) < refresh_days * 86400 and c.get('q', qs) == qs:   # new queries: refresh
        return c['items']
    seen, out = set(), []
    for q in qs:
        for x in (RC.search(q) or []):
            if x.get('id') in seen:
                continue
            seen.add(x.get('id'))
            ok, why = RC.prefilter(x, e)
            if ok:
                out.append({k2: x.get(k2) for k2 in ('id', 'title', 'channel', 'uploader_id', 'view_count', 'duration')})
    out.sort(key=lambda x: -(x.get('view_count') or 0))
    st['candidates'][k] = {'name': e['name'], 'ts': time.time(), 'at': time.strftime('%Y-%m-%d'), 'items': out, 'q': qs}
    return out


def sheet(clip, out_png, label):
    """Review sheet: 20 random frames of the clip's window (seeded per item) + a label line."""
    import cv2
    import numpy as np
    cap = cv2.VideoCapture(clip)
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    rng = random.Random(label)
    idx = sorted(rng.sample(range(max(1, n)), min(20, max(1, n))))
    tiles = []
    for i in idx:
        cap.set(cv2.CAP_PROP_POS_FRAMES, i)
        ok, f = cap.read()
        if ok:
            tiles.append(cv2.resize(f, (384, 216)))
    cap.release()
    while len(tiles) % 5:
        tiles.append(np.zeros((216, 384, 3), np.uint8))
    if not tiles:
        return None
    grid = np.vstack([np.hstack(tiles[r:r + 5]) for r in range(0, len(tiles), 5)])
    bar = np.zeros((44, grid.shape[1], 3), np.uint8)
    cv2.putText(bar, label[:150], (8, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
    os.makedirs(os.path.dirname(out_png), exist_ok=True)
    cv2.imwrite(out_png, np.vstack([bar, grid]), [cv2.IMWRITE_JPEG_QUALITY, 80])
    return out_png


def mine(budget_min=60, work=None, state_file=None, max_entries=12, max_dl=3, offline=False, entries=None, prefer=(),
         first=(), retry=()):
    """Mining step (one job). Entries in plan() order (topic blockers first); per entry: candidate list, rights /
    info gates (old + idle editors first, vetoed / blocklisted skipped), download <= max_dl, every gate, the best
    phrase window -> staged clip (+ margin), review sheet, proof. Stops at the time budget. `retry`: source ids decided
    before (a bad window, not a bad edit) that are evaluated again (searched first, like --prefer)."""
    import ranking_compilation as RC
    t0 = time.time()
    work = work or os.path.join(ROOT, 'output', 'ranking-mine')
    RC.YT_CACHE[0] = os.path.join(work, 'cache', 'yt')
    RC.OFFLINE[0] = offline
    _KEEP[0] = offline
    RC.PREFER.update(prefer)
    RC.PREFER.update(retry)
    sd = staged_dir()
    RC.PROOF_DIR[0] = os.path.join(sd, 'proofs')
    RC.idle()
    sp = state_path(state_file)
    st = merge_state({'candidates': {}, 'sources': {}}, rj(sp, {}))
    man = manifest(sd)
    live = manifest(live_dir())['items']
    for it in live + man['items']:
        st['sources'].setdefault(it['source_id'], {'status': it.get('status', 'staged'), 'entry': it.get('entry_key')})
    jh = {}

    def judge():
        if 'j' not in jh:
            RC.wait_ram('the SigLIP / analysis models')
            jh['j'] = RC.Judge(os.path.join(work, 'cache'))
        return jh['j']
    todo = plan()
    if entries:
        # named entries always run (also one the plan no longer needs, e.g. a --retry of an already-stocked character)
        planned = [p for p in todo if p['key'] in entries or p['name'] in entries]
        keys = {p['key'] for p in planned}
        todo = planned + [{'key': k, 'name': v[0]['name'], 'entry': v[0], 'topics': v[1], 'score': 1, 'have': 0}
                          for k, v in all_entries().items() if (k in entries or v[0]['name'] in entries) and k not in keys]
    summary = {'entries': [], 'staged': 0}
    topics = RT.load()
    if first:                                  # --first: these entries lead (in that order), then the plan order
        fl = [x.lower() for x in first]
        pos = {p['key']: next((i for i, x in enumerate(fl) if x in (p['key'].lower(), p['name'].lower())), len(fl)) for p in todo}
        todo = sorted(todo, key=lambda p: pos[p['key']])
    tried = 0
    for p in todo:
        if tried >= max_entries:
            break
        if (time.time() - t0) / 60 > budget_min:
            log('time budget used up')
            break
        e, key = p['entry'], p['key']
        # the other entries of its first topic are the character-check distractors
        T = topics[p['topics'][0]]
        others = [o for o in T['entries'] if entry_key(o) != key]
        log(f'== {e["name"]} ({key}): blocks {p["topics"]} (score {p["score"]}, have {p["have"]})')
        try:
            cands = candidates(e, RC, st)
        except Exception as ex:  # noqa: BLE001
            log(f'search failed: {ex}')
            continue
        done = {sid for sid, v in st['sources'].items()
                if v.get('status') in ('staged', 'reviewed', 'rejected', 'gate_reject') and sid not in retry}
        report = {'searched': 0, 'downloaded': 0, 'watermark_free': 0, 'reject': {}, 'evaluated': [], '_dl': {}}
        # an entry that alone blocks a whole topic (Yuta for jjk_strongest) gets twice the downloads
        try:
            pre = RC.find_source(e, None, None, work, 0, done, report, max_dl=max_dl * (2 if p['score'] >= 1 else 1))
        except Exception as ex:  # noqa: BLE001  (one bad entry never kills the run)
            log(f'{e["name"]}: candidate search / download failed: {type(ex).__name__}: {ex}')
            summary['entries'].append({'entry': e['name'], 'key': key, 'error': f'{type(ex).__name__}: {ex}'[:300]})
            continue
        if report['downloaded'] or report['_dl']:
            tried += 1                         # an entry whose candidates are all checked costs no slot (search only)
        for r in report['evaluated']:          # text-gate rejects: their full downloads go right away
            if r.get('verdict') == 'reject':
                _rm(os.path.join(work, 'src', f"{r.get('id')}.mp4"))
        summary['downloaded'] = summary.get('downloaded', 0) + report['downloaded']
        row = {'entry': e['name'], 'key': key, 'candidates': len(cands), 'downloaded': report['downloaded'],
               'clean': len(pre), 'reject': report['reject'], 'staged': []}
        for r in report['evaluated']:
            if r.get('verdict') == 'reject':
                st['sources'][r['id']] = {'status': 'gate_reject', 'why': r.get('why'), 'entry': key, 'at': time.strftime('%Y-%m-%d')}
        for res, rrow in pre:
            if (time.time() - t0) / 60 > budget_min + 20:
                break
            try:
                res = RC.evaluate(res, e, others, judge(), SEG, e.get('pace', 'action'), max_pass=SECTIONS,
                                  all_infos=RC.cached_infos(), theme=entry_theme(p['topics'], topics), distinct=True)
            except Exception as ex:  # noqa: BLE001  (one bad candidate never kills the run)
                log(f'{res.get("id")}: evaluate failed: {type(ex).__name__}: {ex}')
                res.update(verdict='reject', why=f'eval_error_{type(ex).__name__}', windows=[])
            if res['verdict'] != 'ok':
                _rm(res.get('path'))
                st['sources'][res['id']] = {'status': 'gate_reject', 'why': res.get('why') or 'no_window', 'entry': key,
                                            'at': time.strftime('%Y-%m-%d'),
                                            'windows': [w.get('reject') for w in (res.get('windows') or [])[:4]]}
                continue
            # owner 2026-10-28: the whole edit is downloaded, then EVERY good section of it is staged (each its own
            # item, reviewed on its own, usable in different videos)
            staged, marks = [], []
            for w in sorted(res.get('passed') or [res['window']], key=lambda w: w['s']):
                try:
                    it = stage_item(res, e, key, sd, RC, w)
                except Exception as ex:  # noqa: BLE001
                    log(f'{res.get("id")}: staging {w["s"]:.1f}s failed: {type(ex).__name__}: {ex}')
                    continue
                if it.get('window_marks'):        # full-resolution tag / logo check of the cut window
                    marks += it['window_marks'][:3]
                    continue
                man['items'] = [x for x in man['items'] if x['id'] != it['id']] + [it]
                staged.append(it['id'])
            _rm(res.get('path'))                   # only the cut sections are kept, the full download goes now
            if not staged:
                st['sources'][res['id']] = {'status': 'gate_reject', 'why': 'window_mark' if marks else 'staging',
                                            'marks': marks[:3], 'entry': key, 'at': time.strftime('%Y-%m-%d')}
                continue
            st['sources'][res['id']] = {'status': 'staged', 'entry': key, 'item': staged[0], 'items': staged,
                                        'at': time.strftime('%Y-%m-%d')}
            row['staged'] += staged
            summary['staged'] += len(staged)
            save_manifest(sd, man)
            wj(sp, st)
        try:                                   # the source edits are never kept: only the staged window
            for res, _ in pre:
                if res.get('path') and os.path.exists(res['path']) and not os.environ.get('RANKING_KEEP_SRC'):
                    os.remove(res['path'])
        except OSError:
            pass
        summary['entries'].append(row)
        wj(sp, st)
        log(f'{e["name"]}: {row}')
    save_manifest(sd, man)
    wj(sp, st)
    summary['minutes'] = round((time.time() - t0) / 60, 1)
    return summary


# 2026-10-31 owner: "download a lot, then classify what character it is; it does not have to be the specific character, it
# can be mixed; if it is not fully Gojo but still good, add it to JJK and use it for best action; themes are not disjoint".
# The library mines per SHOW: broad searches (the show + its characters), whole edits downloaded, every good section staged
# with its tags (the characters that own it, the show, every theme it is on par with); ranking videos pick by tag (usable).
LIB_RETRY_WHY = ('no_window', 'character', 'eval_error', 'handle', 'editor_tag')   # per-entry rejects (the old whole-edit
# handle / tag rules included) a show-level look can still use; once the library judged a source ('lib') it stays decided


STYLE_REFS = os.path.join(HERE, 'refs', 'ranking-style-refs.json')
_STYLE = []


def style_uploads(RC):
    """Owner 2026-11-01 ('these are the type of edits I want'): the uploads of the editors of the owner's reference edits
    (refs/ranking-style-refs.json channels; flat channel lists, cached 7 days, one paced request per channel and week) are
    candidates for every show; the prefilter keeps the ones whose title names the show / its characters."""
    if _STYLE:
        return _STYLE[0]
    out = []
    for cid in (rj(STYLE_REFS, {}).get('channels') or {}):
        try:
            lst = RC._cached(f'chan_{cid}', lambda: RC.pace() or json.loads(RC.ytdlp(['--flat-playlist', '-J', '--playlist-end', '150',
                                                                          f'https://www.youtube.com/channel/{cid}/videos']) or '{}'))
        except Exception as ex:  # noqa: BLE001  (one channel never stops a run)
            log(f'style channel {cid}: {ex}')
            continue
        out += [{k: e.get(k) for k in ('id', 'title', 'channel', 'uploader_id', 'view_count', 'duration')}
                for e in ((lst or {}).get('entries') or []) if isinstance(e, dict) and e.get('id')]
    _STYLE.append(out)
    log(f'style refs: {len(out)} uploads of {len(rj(STYLE_REFS, {}).get("channels") or {})} reference editors')
    return out


def show_cast_alias(sk):
    """Full names (either order) of every quiz-pool character of the show: a 'Todo Aoi edit' is a Jujutsu Kaisen edit too."""
    try:
        chars = rj(os.path.join(ROOT, 'core', 'quiz', 'assets', 'anime-characters.json'), {}).get('characters') or []
    except Exception:  # noqa: BLE001
        return []
    out = []
    for c in chars:
        ws = re.findall(r"[a-z]+", (c.get('name') or '').lower())
        if lib_show(c.get('anime')) == sk and len(ws) >= 2 and min(len(w) for w in ws[:2]) >= 3:
            # AniList romanizes long vowels ('Aoi Toudou', 'Satoru Gojou'); titles mostly do not ('Todo Aoi')
            ws = [w.replace('ou', 'o').replace('uu', 'u') for w in ws[:2]]
            out += [r'\b' + r'\s*'.join(ws) + r'\b', r'\b' + r'\s*'.join(ws[::-1]) + r'\b']
    return list(dict.fromkeys(out))


def mine_library(budget_min=60, work=None, state_file=None, max_shows=6, max_dl=4, offline=False, first=(), only=None):
    import ranking_compilation as RC
    t0 = time.time()
    work = work or os.path.join(ROOT, 'output', 'ranking-mine')
    RC.YT_CACHE[0] = os.path.join(work, 'cache', 'yt')
    RC.OFFLINE[0] = offline
    _KEEP[0] = offline
    sd = staged_dir()
    RC.PROOF_DIR[0] = os.path.join(sd, 'proofs')
    RC.idle()
    sp = state_path(state_file)
    st = merge_state({'candidates': {}, 'sources': {}}, rj(sp, {}))
    man = manifest(sd)
    for it in manifest(live_dir())['items'] + man['items']:
        st['sources'].setdefault(it['source_id'], {'status': it.get('status', 'staged'), 'entry': it.get('entry_key')})
    jh = {}

    def judge():
        if 'j' not in jh:
            RC.wait_ram('the SigLIP / analysis models')
            jh['j'] = RC.Judge(os.path.join(work, 'cache'))
        return jh['j']
    topics = RT.load()
    smap = shows(topics)
    order = [r['show'] for r in plan_shows(topics)]
    if only:
        ol = {x.lower() for x in only}
        order = [k for k in order if k in ol or smap[k]['show']['name'].lower() in ol
                 or any(c['name'].lower() in ol for c in smap[k]['cast'])]
    if first:                                  # --first: shows of these entries / shows lead (in that order)
        fl = [x.lower() for x in first]

        def pos(k):
            names = [k, smap[k]['show']['name'].lower()] + [c['name'].lower() for c in smap[k]['cast']]
            return min([i for i, x in enumerate(fl) if x in names] or [len(fl)])
        order = sorted(order, key=pos)
    summary = {'mode': 'library', 'entries': [], 'staged': 0, 'downloaded': 0}
    tried = 0
    for sk in order:
        if tried >= max_shows:
            break
        if (time.time() - t0) / 60 > budget_min:
            log('time budget used up')
            break
        G = smap[sk]
        show, cast = G['show'], G['cast']
        skey = entry_key(show)
        # one broad search entry: the show's searches + "<character> edit" per cast member; a title naming the show OR
        # any of its characters passes; vs / mixed edits are welcome (solo False)
        q = RC.no_amv(list(dict.fromkeys(list(show['q']) + [f'{show.get("search") or sk} edit']
                               + [f'{(c.get("search") or c["name"]).lower()} edit' for c in cast]
                               + [q_ for cp in G.get('couples') or [] for q_ in cp.get('q') or []])))
        se = dict(show, q=q, alias=list(dict.fromkeys(list(show.get('alias') or []) + [a for c in cast for a in c['alias']]
                                                      + [a for cp in G.get('couples') or [] for a in cp.get('alias') or []]
                                                      + show_cast_alias(sk))),
                  solo=False, search=show.get('search') or sk, extra=style_uploads(RC))
        others = [smap[o]['show'] for o in order if o != sk][:4]           # distractor shows for the show-level check
        done = {sid for sid, v in st['sources'].items()
                if v.get('status') in ('staged', 'reviewed', 'rejected')
                or (v.get('status') == 'gate_reject' and (v.get('lib') or not str(v.get('why') or '').startswith(LIB_RETRY_WHY))
                    and not charfix_retry(v))}
        report = {'searched': 0, 'downloaded': 0, 'watermark_free': 0, 'reject': {}, 'evaluated': [], '_dl': {}}
        log(f'== library {show["name"]} ({sk}): cast {[c["name"] for c in cast]}, {len(q)} searches')
        try:
            pre = RC.find_source(se, None, None, work, 0, done, report, max_dl=max_dl)
        except Exception as ex:  # noqa: BLE001  (one bad show never kills the run)
            log(f'{sk}: search / download failed: {type(ex).__name__}: {ex}')
            summary['entries'].append({'entry': show['name'], 'key': skey, 'error': f'{type(ex).__name__}: {ex}'[:300]})
            continue
        if report['downloaded'] or report['_dl']:
            tried += 1
        summary['downloaded'] += report['downloaded']
        row = {'entry': show['name'], 'key': skey, 'show': sk, 'downloaded': report['downloaded'], 'clean': len(pre),
               'reject': report['reject'], 'staged': [], 'serves': {}}
        for r in report['evaluated']:
            if r.get('verdict') == 'reject':
                _rm(os.path.join(work, 'src', f"{r.get('id')}.mp4"))
                st['sources'][r['id']] = {'status': 'gate_reject', 'why': r.get('why'), 'entry': skey, 'lib': True,
                                          'at': time.strftime('%Y-%m-%d')}
        for res, _ in pre:
            if (time.time() - t0) / 60 > budget_min + 20:
                break
            try:
                # 'lib' pace: calm edits are fine (owner 2026-11-01), only near-static footage out; the theme gate decides action vs
                # beauty / romance per section
                # a crossover title ('Demon Slayer x Jujutsu Kaisen') names other shows: their whole popular cast are
                # distractors, so their frames never pass as this show's (2026-11-01: a Tengen / Daki section passed as JJK)
                xo = [dict(smap[o]['show'], distract_n=12) for o in smap if o != sk
                      and RC.title_names(res.get('title'), [dict(smap[o]['show'], alias=list(smap[o]['show'].get('alias') or [])
                                                                 + [re.escape(w) for w in RC.anime_words(smap[o]['show'])])])]
                res = RC.evaluate(res, show, xo + [o for o in others if o['name'] not in {x['name'] for x in xo}], judge(),
                                  SEG, 'lib', max_pass=LIB_SECTIONS,
                                  all_infos=RC.cached_infos(), theme='any', distinct=True, cast=cast)
            except Exception as ex:  # noqa: BLE001
                log(f'{res.get("id")}: evaluate failed: {type(ex).__name__}: {ex}')
                res.update(verdict='reject', why=f'eval_error_{type(ex).__name__}', windows=[])
            if res['verdict'] != 'ok':
                _rm(res.get('path'))
                st['sources'][res['id']] = {'status': 'gate_reject', 'why': res.get('why') or 'no_window', 'entry': skey,
                                            'lib': True, 'at': time.strftime('%Y-%m-%d'),
                                            'windows': [w.get('reject') for w in (res.get('windows') or [])[:6]]}
                continue
            staged, marks = [], []
            for w in sorted(res.get('passed') or [res['window']], key=lambda w: w['s']):
                # owner 2026-11-01: a title that names a character is trusted (the others in the edit back that person up);
                # otherwise the characters that own the section (attribute)
                mains = RC.title_names(res.get('title'), cast) or [c for n in (w.get('mains') or []) for c in cast if c['name'] == n]
                e = mains[0] if mains else show
                extra = {'show': sk, 'lib': True, 'themes': w.get('themes') or [], 'cast': w.get('cast') or {}, 'cast_runs': w.get('cast_runs') or {},
                         'cast_gaps': w.get('cast_gaps') or {},
                         'serves': [entry_key(c) for c in mains] + [skey]}
                try:
                    it = stage_item(res, e, entry_key(e), sd, RC, w, extra)
                except Exception as ex:  # noqa: BLE001
                    log(f'{res.get("id")}: staging {w["s"]:.1f}s failed: {type(ex).__name__}: {ex}')
                    continue
                if it.get('window_marks'):
                    marks += it['window_marks'][:3]
                    continue
                man['items'] = [x for x in man['items'] if x['id'] != it['id']] + [it]
                staged.append(it['id'])
                for k in extra['serves']:
                    row['serves'][k] = row['serves'].get(k, 0) + 1
            _rm(res.get('path'))
            if not staged:
                st['sources'][res['id']] = {'status': 'gate_reject', 'why': 'window_mark' if marks else 'staging', 'lib': True,
                                            'marks': marks[:3], 'entry': skey, 'at': time.strftime('%Y-%m-%d')}
                continue
            st['sources'][res['id']] = {'status': 'staged', 'entry': skey, 'item': staged[0], 'items': staged, 'lib': True,
                                        'at': time.strftime('%Y-%m-%d')}
            row['staged'] += staged
            summary['staged'] += len(staged)
            if os.environ.get('RANKING_STAGE_SOURCE', '1') != '0':
                try:
                    srec = stage_source(res, sd, [x for x in man['items'] if x['id'] in staged])
                    if srec:
                        man['sources'] = [x for x in man.get('sources') or [] if x['source_id'] != srec['source_id']] + [srec]
                except Exception as ex:  # noqa: BLE001  (the sections stay staged without the whole edit)
                    log(f'{res.get("id")}: staging the whole edit failed: {type(ex).__name__}: {ex}')
            save_manifest(sd, man)
            wj(sp, st)
        for res, _ in pre:
            _rm(res.get('path'))
        summary['entries'].append(row)
        wj(sp, st)
        log(f'{sk}: {row}')
    save_manifest(sd, man)
    wj(sp, st)
    summary['minutes'] = round((time.time() - t0) / 60, 1)
    return summary


_KEEP = [False]        # an --offline run re-uses the cached downloads


def _rm(p):
    """delete a full source download (never kept: owner 2026-10-26 'no large pool on local')"""
    if p and os.path.exists(p) and not os.environ.get('RANKING_KEEP_SRC') and not _KEEP[0]:
        try:
            os.remove(p)
        except OSError:
            pass


def stage_item(res, e, key, sd, RC, w=None, extra=None):
    w = w or res['window']
    dur = res.get('dur') or w['e'] + MARGIN
    s0, e0 = max(0.0, w['s'] - MARGIN), min(float(dur), w['e'] + MARGIN)
    iid = f'r_{res["id"]}_{int(w["s"] * 1000)}'
    clip = os.path.join(sd, 'clips', iid + '.mp4')
    RC.cut_clip(res['path'], s0, e0, clip)
    marks = RC.window_marks(clip, res, w['s'] - s0, w['e'] - s0)
    if marks:
        os.remove(clip)
        log(f'{res["id"]}: watermark in the window {marks[:2]}')
        return {'window_marks': marks}
    rt = res.get('rights') or {}
    info = res.get('_info') or {}
    proof = rj(os.path.join(RC.PROOF_DIR[0] or '', f'{res["id"]}.json'), {}) if RC.PROOF_DIR[0] else {}
    it = {'id': iid, 'entry_key': key, 'entry_name': e['name'], 'kind': e.get('kind', 'character'), 'anime': e.get('anime'),
          'chars': e.get('chars'), 'file': f'clips/{iid}.mp4', 'start': round(s0, 3), 'end': round(e0, 3),
          'clip_in': round(w['s'] - s0, 3), 'clip_out': round(w['e'] - s0, 3), 'win_len': round(w['e'] - w['s'], 2),
          'source_id': res['id'], 'source_title': res.get('title'), 'source_channel': res.get('channel'),
          'editor_handle': res.get('handle'), 'upload_date': info.get('upload_date'), 'age_days': rt.get('age_days'),
          'channel_idle_days': rt.get('channel_idle_days'), 'views': res.get('views'), 'source_dur': res.get('dur'),
          'char_conf': w.get('conf', res.get('conf')), 'char_method': (w.get('match') or res.get('match') or {}).get('method'),
          'char_votes': (w.get('match') or res.get('match') or {}).get('votes'),
          'char_others': (w.get('match') or res.get('match') or {}).get('others'),
          'eventful': w.get('eventful'), 'song': w.get('song'), 'song_name': w.get('song_name'), 'quality': w.get('quality'),
          'theme': w.get('theme'), 'ref_score': w.get('ref_score'), 'ref_groups': w.get('ref_groups'),
          'manga': bool(w.get('manga')), 'manga_share': w.get('manga_share'), 'title_trusted': bool(w.get('title_trusted')),
          # owner 2026-10-25 late: the free-to-use bonus is dropped; old (>= 1 y) + idle editors, the reference-profile
          # closeness, and anime footage over manga panels
          'rank_score': round((w.get('quality') or 0) + 1.0 * bool((rt.get('age_days') or 0) >= 365)
                              + 0.5 * bool((rt.get('channel_idle_days') or 0) >= 182)
                              + 0.5 * (w.get('ref_score') or 0) - 3.0 * bool(w.get('manga')), 2),
          'safety': 'pass', 'watermark': 'none', 'editor_tags': [t for t in (res.get('editor_tags') or [])][:12],
          'rights': rt, 'proof': {'reason': proof.get('reason'), 'type': proof.get('type'),
                                  'evidence': proof.get('evidence'), 'snapshot_file': f'proofs/{res["id"]}.json'},
          'status': 'staged', 'added': time.strftime('%Y-%m-%d')}
    lab = (f'{e["name"]} | {it["editor_handle"]} | {res["id"]} {w["s"]:.1f}-{w["e"]:.1f}s | char {it["char_conf"]} '
           f'({it["char_method"]}) | ev {it["eventful"]} song {it["song"]} | {it["age_days"]} d old')
    if extra:                                  # library: what the section serves + its themes, on the sheet too
        it.update(extra)
        lab += f' | serves {",".join(x.split("__")[0] for x in extra.get("serves") or [])} | {"/".join(extra.get("themes") or [])}'
    it['sheet'] = os.path.relpath(sheet(clip, os.path.join(sd, 'sheets', iid + '.jpg'), lab) or '', sd).replace('\\', '/')
    return it


# ------------------------------------------------------------------ whole-edit review (owner 2026-11-07)
# "GitHub downloads, then you view the whole video and do the clipping / sectioning / tagging": every edit that gives a
# section is staged WHOLE (sources/<id>.mp4) with timeline sheets (a frame every TL_STEP s, its time, the proposed sections
# outlined). Review: recut (move a section's start / end, snapped to a scene cut; set serves / themes) and add-section
# (another stretch of the same edit). The full edits leave the PC once every section of theirs is decided.
TL_STEP = 0.5
TL_COLS, TL_ROWS = 10, 12
SRC_MAX_MB = 150             # a bigger download is re-encoded (CRF 19, <= 1080p) before it is staged


def _rel(p, base):
    return os.path.relpath(p, base).replace(os.sep, '/')


def stage_source(res, sd, items):
    """copy the whole edit into staging + its timeline sheets -> the source record (or None)"""
    src = res.get('path')
    if not src or not os.path.exists(src):
        return None
    sid = res['id']
    rel = f'sources/{sid}.mp4'
    dst = os.path.join(sd, rel)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.getsize(src) > SRC_MAX_MB * 1048576:
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', src, '-vf', "scale='min(1920,iw)':-2", '-c:v', 'libx264', '-crf', '19',
                        '-preset', 'medium', '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart', dst], check=True)
    else:
        shutil.copy2(src, dst)
    props = [(float(it['start']) + float(it['clip_in']), float(it['start']) + float(it['clip_out']), it['id']) for it in items]
    lab = f"{sid} | {res.get('handle')} | {(res.get('title') or '')[:70]}"
    sheets = timeline_sheets(dst, os.path.join(sd, 'timelines', sid), props, lab)
    return {'source_id': sid, 'file': rel, 'timelines': [_rel(x, sd) for x in sheets],
            'dur': res.get('dur'), 'title': res.get('title'), 'handle': res.get('handle'), 'items': [it['id'] for it in items],
            'show': (items[0].get('show') if items else None), 'status': 'staged'}


def timeline_sheets(video, out_prefix, props=(), label=''):
    """frames every TL_STEP s of the whole edit, TL_COLS x TL_ROWS per sheet; each tile shows its time; a tile inside a
    proposed section gets a coloured frame + the section's number (S1 = the first proposal)"""
    import cv2
    import numpy as np
    cap = cv2.VideoCapture(video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    dur = (cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0) / fps
    cols = [(0, 200, 255), (80, 255, 80), (255, 120, 60), (255, 80, 255), (60, 255, 255), (200, 200, 200)]
    tiles, t = [], 0.0
    while t < dur - 0.05:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, f = cap.read()
        if ok:
            f = cv2.resize(f, (240, 135))
            for k, (a, b, _) in enumerate(props):
                if a <= t < b:
                    cv2.rectangle(f, (0, 0), (239, 134), cols[k % len(cols)], 4)
                    cv2.putText(f, f'S{k + 1}', (196, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, cols[k % len(cols)], 2)
            cv2.putText(f, f'{t:.1f}', (6, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 4)
            cv2.putText(f, f'{t:.1f}', (6, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
            tiles.append(f)
        t += TL_STEP
    cap.release()
    out, per = [], TL_COLS * TL_ROWS
    os.makedirs(os.path.dirname(out_prefix), exist_ok=True)
    for n, i in enumerate(range(0, len(tiles), per)):
        part = tiles[i:i + per]
        while len(part) % TL_COLS:
            part.append(np.zeros((135, 240, 3), np.uint8))
        grid = np.vstack([np.hstack(part[r:r + TL_COLS]) for r in range(0, len(part), TL_COLS)])
        bar = np.zeros((40, grid.shape[1], 3), np.uint8)
        txt = f'{label[:120]} | sheet {n + 1} | ' + ' '.join(f'S{k + 1} {a:.1f}-{b:.1f}' for k, (a, b, _) in enumerate(props))
        cv2.putText(bar, txt[:200], (8, 27), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
        p_ = f'{out_prefix}_{n:02d}.jpg'
        cv2.imwrite(p_, np.vstack([bar, grid]), [cv2.IMWRITE_JPEG_QUALITY, 78])
        out.append(p_)
    return out


_SCD_RE = re.compile(r'lavfi[.]scd[.]time: ([0-9.]+)')


def _snap(video, t, win=0.4):
    """the scene cut nearest t within +-win s (ffmpeg scdet), else t"""
    a = max(0.0, t - win)
    r = subprocess.run(['ffmpeg', '-hide_banner', '-ss', f'{a:.3f}', '-t', f'{2 * win:.3f}', '-i', video, '-vf', 'scdet=threshold=8',
                        '-f', 'null', '-'], capture_output=True, text=True)
    cuts = [a + float(x) for x in _SCD_RE.findall(r.stderr)]
    return round(min(cuts, key=lambda c: abs(c - t)), 3) if cuts else round(t, 3)


def _src_record(man, sid):
    return next((x for x in man.get('sources') or [] if x['source_id'] == sid), None)


def _apply_window(it, sd, src, s, e, label_extra=''):
    import ranking_compilation as RC
    dur = float(it.get('source_dur') or 0) or None
    s0, e0 = max(0.0, s - MARGIN), (min(dur, e + MARGIN) if dur else e + MARGIN)
    clip = os.path.join(sd, it['file'])
    RC.cut_clip(os.path.join(sd, src['file']), s0, e0, clip)
    it.update(start=round(s0, 3), end=round(e0, 3), clip_in=round(s - s0, 3), clip_out=round(e - s0, 3), win_len=round(e - s, 2),
              recut=time.strftime('%Y-%m-%d'))
    lab = (f"{it['entry_name']} | {it.get('editor_handle')} | {it['source_id']} {s:.1f}-{e:.1f}s | RECUT | serves "
           f"{','.join(x.split('__')[0] for x in it.get('serves') or [])} | {'/'.join(it.get('themes') or [])}{label_extra}")
    it['sheet'] = _rel(sheet(clip, os.path.join(sd, 'sheets', it['id'] + '.jpg'), lab) or '', sd)


def _set_tags(it, serves=None, themes=None, entry=None):
    if entry:
        e, _ = all_entries().get(entry) or (None, None)
        if not e:
            raise SystemExit(f'unknown entry key {entry}')
        it.update(entry_key=entry, entry_name=e['name'], kind=e.get('kind', 'character'), chars=e.get('chars'))
    if serves is not None:
        it['serves'] = list(serves)
        it['serves_by'] = 'review'
    if themes is not None:
        it['themes'] = list(themes)
        it['theme'] = themes[0] if themes else it.get('theme')


def recut(iid, s=None, e=None, serves=None, themes=None, snap=True, entry=None):
    """Review: move a staged section to [s, e] of its source (source seconds, snapped to scene cuts), optionally set what it
    serves (entry keys) / its themes / the entry it is filed under."""
    sd = staged_dir()
    man = manifest(sd)
    it = next(x for x in man['items'] if x['id'] == iid)
    src = _src_record(man, it['source_id'])
    if s is not None and e is not None:
        if not src or not os.path.exists(os.path.join(sd, src['file'])):
            raise SystemExit(f'{iid}: the whole edit is not staged (older batch): only serves / themes can change')
        vid = os.path.join(sd, src['file'])
        if snap:
            s, e = _snap(vid, s), _snap(vid, e)
        _apply_window(it, sd, src, float(s), float(e))
    _set_tags(it, serves, themes, entry)
    save_manifest(sd, man)
    return {k: it.get(k) for k in ('id', 'start', 'clip_in', 'clip_out', 'win_len', 'serves', 'themes', 'entry_key', 'sheet')}


def add_section(sid, s, e, like, serves=None, themes=None, snap=True, entry=None):
    """Review: a new section [s, e] of a staged whole edit; metadata cloned from `like` (a section of the same edit)."""
    sd = staged_dir()
    man = manifest(sd)
    src = _src_record(man, sid)
    if not src:
        raise SystemExit(f'{sid}: no staged whole edit')
    base = next(x for x in man['items'] if x['id'] == like)
    vid = os.path.join(sd, src['file'])
    if snap:
        s, e = _snap(vid, s), _snap(vid, e)
    iid = f'r_{sid}_{int(float(s) * 1000)}'
    it = {k: v for k, v in base.items() if k not in ('published_in', 'sheet', 'recut')}
    it.update(id=iid, file=f'clips/{iid}.mp4', status='staged', added=time.strftime('%Y-%m-%d'), added_by='review')
    _apply_window(it, sd, src, float(s), float(e), ' | NEW')
    _set_tags(it, serves, themes, entry)
    man['items'] = [x for x in man['items'] if x['id'] != iid] + [it]
    src['items'] = list(dict.fromkeys((src.get('items') or []) + [iid]))
    save_manifest(sd, man)
    return {k: it.get(k) for k in ('id', 'start', 'clip_in', 'clip_out', 'win_len', 'serves', 'themes', 'entry_key', 'sheet')}


def clean_sources():
    """delete the staged whole edits whose sections are all decided (reviewed / rejected): no large files kept on the PC"""
    sd = staged_dir()
    man = manifest(sd)
    st = {x['id']: x.get('status') for x in man['items']}
    n = 0
    for src in man.get('sources') or []:
        if src.get('status') == 'done':
            continue
        if all(st.get(i) in ('reviewed', 'rejected') for i in src.get('items') or []):
            for rel in [src['file']] + list(src.get('timelines') or []):
                p_ = os.path.join(sd, rel)
                if os.path.exists(p_):
                    os.remove(p_)
            src['status'] = 'done'
            n += 1
    save_manifest(sd, man)
    return n


# ------------------------------------------------------------------ staging transport (CI / PC miner <-> owner PC)

def publish(dest, label='ci', merge_remote=False, release='edit-pools-staging'):
    """The staged items not yet published -> one tar (clips, sheets, proofs, a clips.json of just those items) + the
    state, in `dest`. Marks them published (published_in) in the local staged manifest. merge_remote: the release's
    CURRENT state is downloaded and merged in first (CI and the PC miner both write it: neither clobbers the other)."""
    sd = staged_dir()
    if merge_remote:
        tmp = os.path.join(dest, '.remote')
        os.makedirs(tmp, exist_ok=True)
        r = gh('release', 'download', release, '--pattern', 'ranking-state.json', '--dir', tmp, '--clobber', check=False)
        if r.returncode == 0:
            wj(state_path(), merge_state(rj(os.path.join(tmp, 'ranking-state.json'), {}), rj(state_path(), {})))
        shutil.rmtree(tmp, ignore_errors=True)
    man = manifest(sd)
    new = [it for it in man['items'] if it.get('status') == 'staged' and not it.get('published_in')]
    os.makedirs(dest, exist_ok=True)
    out = {'tar': None, 'tars': [], 'items': len(new)}
    if new:
        # owner 2026-11-07 whole-edit review: each edit's whole file + timelines travel with its sections; batches are split
        # by source so one tar stays under the release's per-file limit (PUB_MAX_MB)
        srcs = {x['source_id']: x for x in man.get('sources') or []}
        groups = {}
        for it in new:
            groups.setdefault(it['source_id'], []).append(it)

        def files_of(sid, its):
            fs = [rel for it in its for rel in (it['file'], it.get('sheet'), (it.get('proof') or {}).get('snapshot_file'))]
            if sid in srcs:
                fs += [srcs[sid]['file']] + list(srcs[sid].get('timelines') or [])
            return [rel for rel in dict.fromkeys(fs) if rel and os.path.exists(os.path.join(sd, rel))]
        batches, cur, size = [], [], 0
        for sid, its in groups.items():
            sz = sum(os.path.getsize(os.path.join(sd, r)) for r in files_of(sid, its))
            if cur and size + sz > PUB_MAX_MB * 1048576:
                batches.append(cur)
                cur, size = [], 0
            cur.append(sid)
            size += sz
        if cur:
            batches.append(cur)
        stamp = time.strftime("%Y%m%d-%H%M", time.gmtime())
        for bi, sids in enumerate(batches):
            its = [it for sid in sids for it in groups[sid]]
            name = f'ranking-cand-{stamp}-{label}{"" if len(batches) == 1 else chr(97 + bi)}-n{len(its)}.tar'
            tp = os.path.join(dest, name)
            with tarfile.open(tp, 'w') as t:
                for sid in sids:
                    for rel in files_of(sid, groups[sid]):
                        t.add(os.path.join(sd, rel), arcname=rel)
                tmp = os.path.join(dest, 'clips.batch.json')
                wj(tmp, {'version': 3, 'items': its, 'sources': [srcs[sid] for sid in sids if sid in srcs], 'batch': name})
                t.add(tmp, arcname='clips.json')
                os.remove(tmp)
            for it in its:
                it['published_in'] = name
            out['tars'].append(tp)
        save_manifest(sd, man)
        out['tar'] = out['tars'][0]
    if os.path.exists(state_path()):
        shutil.copy2(state_path(), os.path.join(dest, 'ranking-state.json'))
    return out


def gh(*a, check=True):
    env = dict(os.environ)
    return subprocess.run(['gh', *a], capture_output=True, text=True, check=check, env=env, cwd=ROOT)


def pull_staged(release='edit-pools-staging'):
    """PC: download the ranking-cand tars not pulled yet, merge their items into the local staging manifest, and merge the
    release's state into the local one."""
    sd = staged_dir()
    os.makedirs(sd, exist_ok=True)
    man = manifest(sd)
    have = {it.get('published_in') for it in man['items']}
    lst = json.loads(gh('release', 'view', release, '--json', 'assets').stdout)['assets']
    n = 0
    for a in lst:
        nm = a['name']
        if nm == 'ranking-state.json':
            tmp = os.path.join(sd, '.dl')
            os.makedirs(tmp, exist_ok=True)
            gh('release', 'download', release, '--pattern', nm, '--dir', tmp, '--clobber')
            wj(state_path(), merge_state(rj(state_path(), {}), rj(os.path.join(tmp, nm), {})))
            shutil.rmtree(tmp, ignore_errors=True)
        if not nm.startswith('ranking-cand-') or nm in have:
            continue
        tmp = os.path.join(sd, '.dl')
        os.makedirs(tmp, exist_ok=True)
        gh('release', 'download', release, '--pattern', nm, '--dir', tmp, '--clobber')
        with tarfile.open(os.path.join(tmp, nm)) as t:
            mem = [m for m in t.getmembers() if m.isfile() and '..' not in m.name and not m.name.startswith('/')]
            batch = json.load(t.extractfile('clips.json'))
            t.extractall(sd, members=[m for m in mem if m.name != 'clips.json'])
        ids = {x['id'] for x in man['items']}
        for it in batch['items']:
            it['published_in'] = nm
            if it['id'] not in ids:
                man['items'].append(it)
                n += 1
        have_src = {x['source_id'] for x in man.get('sources') or []}
        man['sources'] = (man.get('sources') or []) + [x for x in batch.get('sources') or [] if x['source_id'] not in have_src]
        shutil.rmtree(tmp, ignore_errors=True)
    save_manifest(sd, man)
    return n


def import_items(ids, reject=(), by='owner'):
    """Reviewed staged items -> the live pool (status reviewed); rejected ones stay in staging as rejected."""
    sd, ld = staged_dir(), live_dir()
    sm, lm = manifest(sd), manifest(ld)
    st = rj(state_path(), {'candidates': {}, 'sources': {}})
    keep_src = {it['source_id'] for it in sm['items'] if it['id'] in ids}
    n = 0
    for it in sm['items']:
        if it['id'] in reject:
            it['status'] = 'rejected'
            it['reviewed_by'] = by
            # one rejected section never blocks the edit's other (approved) sections
            if not (keep_src & {it['source_id']}) and (st['sources'].get(it['source_id']) or {}).get('status') != 'reviewed':
                st['sources'][it['source_id']] = {'status': 'rejected', 'entry': it['entry_key'], 'by': by, 'at': time.strftime('%Y-%m-%d')}
        elif it['id'] in ids and it['status'] == 'staged':
            os.makedirs(os.path.join(ld, 'clips'), exist_ok=True)
            shutil.copy2(os.path.join(sd, it['file']), os.path.join(ld, it['file']))
            it['status'] = 'reviewed'
            it['reviewed_by'] = by
            it['reviewed_at'] = time.strftime('%Y-%m-%d')
            live = {k: v for k, v in it.items() if k not in ('sheet', 'published_in')}
            lm['items'] = [x for x in lm['items'] if x['id'] != it['id']] + [live]
            st['sources'][it['source_id']] = {'status': 'reviewed', 'entry': it['entry_key'], 'by': by, 'at': time.strftime('%Y-%m-%d')}
            n += 1
    save_manifest(sd, sm)
    save_manifest(ld, lm)
    wj(state_path(), st)
    return n


def remove_items(ids, by='owner', why='rejected'):
    """Live items -> out of the pool (manifest + clip), status rejected in staging and in the state (never mined again)."""
    sd, ld = staged_dir(), live_dir()
    sm, lm = manifest(sd), manifest(ld)
    st = rj(state_path(), {'candidates': {}, 'sources': {}})
    gone = []
    for it in lm['items']:
        if it['id'] in ids:
            gone.append(it)
            try:
                os.remove(os.path.join(ld, it['file']))
            except OSError:
                pass
    lm['items'] = [x for x in lm['items'] if x['id'] not in ids]
    staged_ids = {x['id'] for x in sm['items']}
    for it in gone:
        if it['id'] not in staged_ids:
            sm['items'].append({k: v for k, v in it.items()})
    for it in sm['items']:
        if it['id'] in ids:
            it.update(status='rejected', reviewed_by=by, rejected_why=why, reviewed_at=time.strftime('%Y-%m-%d'))
            st['sources'][it['source_id']] = {'status': 'rejected', 'entry': it['entry_key'], 'by': by, 'why': why,
                                              'at': time.strftime('%Y-%m-%d')}
    save_manifest(sd, sm)
    save_manifest(ld, lm)
    wj(state_path(), st)
    return [x['id'] for x in gone]


def recast(ids=None):
    """Backfill (owner 2026-11-07): every live item's per-character screen share ('cast') and the longest stretch other
    characters own ('cast_runs'), measured on its CURRENT window (clip_in..clip_out, 2 fps) with the show's ranking cast -
    what pick_items' strongest rule reads. Rewrites the live manifest; push the pool afterwards."""
    import cv2
    import ranking_compilation as RC
    topics = RT.load()
    smap = shows(topics)
    ld = live_dir()
    lm = manifest(ld)
    work = os.path.join(ROOT, 'output', 'ranking-mine')
    judge = RC.Judge(os.path.join(work, 'cache'))
    rows = []
    for it in lm['items']:
        if it.get('status') != 'reviewed' or (ids and it['id'] not in ids):
            continue
        g = smap.get(lib_show(it.get('anime')))
        if not g or not g['cast']:
            continue
        clip = os.path.join(ld, it['file'])
        cap, frames, ts = cv2.VideoCapture(clip), [], []
        t = float(it['clip_in'])
        while t < float(it['clip_out']):
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, fr = cap.read()
            if ok:
                frames.append(cv2.resize(fr, (448, int(fr.shape[0] * 448 / fr.shape[1]))))   # the miner's decode() size
                ts.append(round(t, 3))
            t += 0.5
        cap.release()
        if not frames:
            continue
        runs, gaps = {}, {}
        shares, mains = RC.attribute(judge, g['cast'], frames, ts, runs=runs, gaps=gaps)
        it.update(cast=shares, cast_runs=runs, cast_gaps=gaps, recast=time.strftime('%Y-%m-%d'))
        rows.append({'id': it['id'], 'entry': it['entry_name'], 'share': shares.get(it['entry_name']),
                     'others_run': runs.get(it['entry_name']), 'gap': gaps.get(it['entry_name']), 'mains': mains})
        log(f"recast {it['id']} {it['entry_name']}: share {shares.get(it['entry_name'])} others run {runs.get(it['entry_name'])} s"
            f" gap {gaps.get(it['entry_name'])} s")
    save_manifest(ld, lm)
    return rows


def rescreen(apply=False):
    """Re-run the reference-profile gate + the manga detector on every live item's window (owner 2026-10-25 late:
    on par with ALL our analysed references, anime footage over manga). Writes ref_score / manga into the live
    manifest; with apply=True a failing item leaves the pool (status rejected, by claude-refgate)."""
    import cv2
    import library as L
    import ranking_profile as RP
    topics = RT.load()
    ents = all_entries(topics)
    sig = L.Siglip(L.Cache(os.path.join(ROOT, 'output', 'ranking-mine', 'cache'), 2000))
    ld = live_dir()
    lm = manifest(ld)
    rows, fail = [], []
    for it in lm['items']:
        th = entry_theme(ents.get(it['entry_key'], (None, ['jjk_strongest']))[1], topics)
        clip = os.path.join(ld, it['file'])
        f = RP.measure(clip, it['clip_in'], it['clip_out'])
        cap, frames = cv2.VideoCapture(clip), []
        t = it['clip_in']
        while t < it['clip_out']:                                  # 2 fps frames for the SigLIP manga check
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, fr = cap.read()
            if ok:
                frames.append(cv2.resize(fr, (448, int(fr.shape[0] * 448 / fr.shape[1]))))
            t += 0.5
        cap.release()
        f['manga_siglip'] = RP.manga_siglip(sig, frames)
        g = RP.gate(f, th)
        if it.get('lib'):                      # a library section keeps every theme it is still on par with
            gs = {t_: RP.gate(f, t_) for t_ in ('action', 'beauty', 'romance')}
            ok_th = [t_ for t_ in gs if gs[t_]['ok']]
            th = max(ok_th or list(gs), key=lambda k: gs[k]['ref_score'] or 0)
            g = dict(gs[th], ok=bool(ok_th))
            it['themes'] = ok_th
        it.update(theme=th, ref_score=g['ref_score'], ref_groups=g['ref_groups'], manga=g['manga'],
                  manga_share=g.get('manga_share'), rescreened=time.strftime('%Y-%m-%d'))
        rows.append({'id': it['id'], 'entry': it['entry_name'], 'theme': th, 'ref_score': g['ref_score'],
                     'groups': g['ref_groups'], 'manga_share': g.get('manga_share'), 'manga': g['manga'], 'ok': g['ok'],
                     'why': g['why']})
        if not g['ok']:
            fail.append(it['id'])
    save_manifest(ld, lm)
    if apply and fail:
        remove_items(set(fail), by='claude-refgate', why='below_reference_profile')
    return rows


def push_state(release='edit-pools-staging'):
    """PC: merge the release's state with the local decisions and upload it (the miner's dedupe); delete cand tars whose
    items are all decided."""
    sd = staged_dir()
    tmp = os.path.join(sd, '.dl')
    os.makedirs(tmp, exist_ok=True)
    gh('release', 'download', release, '--pattern', 'ranking-state.json', '--dir', tmp, '--clobber', check=False)
    st = merge_state(rj(os.path.join(tmp, 'ranking-state.json'), {}), rj(state_path(), {}))
    wj(state_path(), st)
    shutil.copy2(state_path(), os.path.join(tmp, 'ranking-state.json'))
    gh('release', 'upload', release, os.path.join(tmp, 'ranking-state.json'), '--clobber')
    man = manifest(sd)
    by_tar = {}
    for it in man['items']:
        if it.get('published_in'):
            by_tar.setdefault(it['published_in'], []).append(it['status'])
    gone = [t for t, ss in by_tar.items() if all(s in ('reviewed', 'rejected') for s in ss)]
    for t in gone:
        gh('release', 'delete-asset', release, t, '-y', check=False)
    shutil.rmtree(tmp, ignore_errors=True)
    return {'sources': len(st['sources']), 'deleted_tars': gone}


# ------------------------------------------------------------------ assembly (the daily job, weekly slot)

def title_for(T):
    return f'{T.get("keyword") or " ".join(T["title"]).lower()} 🔥 #{T.get("hashtag") or "anime"}'


def describe(T, entries):
    import ranking_compilation as RC
    head = ' '.join(w if (w.isupper() and len(w) <= 3) else w.capitalize() for w in ' '.join(T['title']).split())
    basis = {'fans': 'ranked by fans (AniList favourites)', 'score': 'ranked by AniList score',
             'power': 'our power ranking'}.get(T.get('order'), 'our ranking')
    lines = [f'{head} ({basis})', '']
    for e in sorted(entries, key=lambda x: -x['rank']):
        h = str(e.get('handle') or '')
        who = h if h.startswith('@') else (e.get('channel') or h or 'unknown editor')
        lines.append(f'#{e["rank"]} {e["name"].title()}: edit by {who}')
    lines += ['', 'All edits belong to their original editors; clips and songs to their rights holders.',
              'Editors: if you want your edit removed, comment and it is taken down the same day.',
              '', ' '.join(dict.fromkeys([f'#{T.get("hashtag") or "anime"}', '#anime', '#edit']))]
    text = '\n'.join(lines)
    if RC.LINK_RE.search(text):
        raise ValueError('description contains a link')
    return text


def assemble(topic, out, work=None, mark_used=False, seed=None):
    """Assemble the ranking video from reviewed items only (no network). -> result dict (also written next to `out` as
    <out>.result.json) with the checks the pre-upload guard reads."""
    import ranking_compilation as RC
    topics = RT.load()
    if topic in (None, '', 'auto'):
        topic = choose_topic(topics)
        if not topic:
            return {'ok': False, 'reason': 'pool_low: no topic has reviewed segments for every revealed entry'}
    T = topics[topic]
    lp, led = ledger()
    ld = live_dir()
    items = manifest(ld)['items']
    picks = pick_items(T, items, led)
    if not picks:
        return {'ok': False, 'reason': f'pool_low: topic {topic} is missing a revealed entry'}
    work = work or os.path.join(os.path.dirname(os.path.abspath(out)), f'work-ranking-{int(time.time())}')
    os.makedirs(work, exist_ok=True)
    RC.idle()
    n_slots = RT.slots(T)
    names = [e['name'] for e in T['entries'][:n_slots]]
    parts, shown, entries, checks = [], set(), [], []
    for rank in RT.revealed_ranks(T):
        it = picks[rank]
        shown.add(rank - 1)
        clip = os.path.join(ld, it['file'])
        if not os.path.exists(clip):
            return {'ok': False, 'reason': f'missing clip {it["file"]}'}
        win = {'s': it['clip_in'], 'e': it['clip_out']}
        png = os.path.join(work, f'ov_{rank}.png')
        RC.overlay_png(png, T['title'], names, shown, '')
        part = os.path.join(work, f'part_{rank}.mp4')
        RC.wait_ram('rendering', need=min(RC.MIN_FREE_GB, 1.0))
        RC.render_entry(clip, win, png, part)
        parts.append(part)
        e = T['entries'][rank - 1]
        entries.append({'rank': rank, 'name': e['name'], 'source': it['source_id'], 'handle': it.get('editor_handle'),
                        'channel': it.get('source_channel'), 'item': it['id'], 'len': round(win['e'] - win['s'], 2)})
        checks.append({'rank': rank, 'entry': e['name'], 'entry_key': entry_key(e), 'item_entry_key': it['entry_key'],
                       'status': it['status'], 'char_conf': it.get('char_conf'), 'char_method': it.get('char_method'),
                       'reviewed_by': it.get('reviewed_by')})
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    RC.concat(parts, out)
    desc = describe(T, entries)
    res = {'ok': True, 'topic': topic, 'short': out, 'title': title_for(T), 'description': desc, 'entries': entries,
           'checks': checks, 'revealed': RT.revealed_ranks(T), 'reveal': T.get('reveal'), 'keyword': T.get('keyword'),
           'hashtag': T.get('hashtag'), 'duration_expected': round(sum(x['len'] for x in entries), 2)}
    if mark_used:
        mark(res)
    wj(os.path.splitext(out)[0] + '.result.json', res)
    return res


def mark(res):
    lp, led = ledger()
    for e in res['entries']:
        led.setdefault('items', {})[f'ranking/clip/{e["item"]}'] = {'used_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                                                                    'video': os.path.basename(res['short'])}
        led.setdefault('ranking_recent', []).append(e['source'])
    led.setdefault('ranking_topics_recent', []).append(res['topic'])
    led['ranking_recent'] = led['ranking_recent'][-50:]
    led['ranking_topics_recent'] = led['ranking_topics_recent'][-50:]
    wj(lp, led)


def library_report(md=False):
    topics = RT.load()
    items = manifest(live_dir())['items']
    staged = manifest(staged_dir())['items']
    _, led = ledger()
    rows = []
    for k, T in topics.items():
        rr = RT.revealed_ranks(T)
        have = []
        for r in rr:
            ek = entry_key(T['entries'][r - 1])
            have.append(f'{T["entries"][r - 1]["name"]}:{sum(1 for i in items if i["entry_key"] == ek and i["status"] == "reviewed")}'
                        f'/{sum(1 for i in staged if i["entry_key"] == ek and i["status"] == "staged")}')
        rows.append(f'{k}: ' + ', '.join(have) + ('  ELIGIBLE' if pick_items(T, items, led) else ''))
    return '\n'.join(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['plan', 'plan-shows', 'mine', 'publish', 'pull-staged', 'review', 'import', 'push-state', 'eligible',
                                    'assemble', 'status', 'mark', 'remove', 'rescreen', 'recast', 'recut', 'add-section',
                                    'timeline', 'clean-sources'])
    ap.add_argument('--result')
    ap.add_argument('--budget-min', type=float, default=60)
    ap.add_argument('--work')
    ap.add_argument('--state')
    ap.add_argument('--max-entries', type=int, default=12)
    ap.add_argument('--max-dl', type=int, default=3)
    ap.add_argument('--entries', default='', help='mine: only these entry keys / names (comma-separated)')
    ap.add_argument('--offline', action='store_true')
    ap.add_argument('--prefer', default='', help='mine: source ids searched first + rank bonus (owner-approved edits)')
    ap.add_argument('--first', default='', help='mine: these entry keys / names lead, in this order (then the plan)')
    ap.add_argument('--avoid', default='', help='mine: SRC:a-b[;SRC:a-b] source seconds no window may touch (review flags)')
    ap.add_argument('--retry', default='', help='mine: source ids evaluated again although decided (a bad window, not a bad edit)')
    ap.add_argument('--per-entry', action='store_true', help='mine: the old per-entry miner (default: the library, per show;'
                    ' --entries / --retry also use the per-entry miner)')
    ap.add_argument('--shows', default='', help='mine (library): only these shows / their characters (comma-separated)')
    ap.add_argument('--merge-remote', action='store_true', help='publish: merge the release state in first')
    ap.add_argument('--dest')
    ap.add_argument('--label', default='ci')
    ap.add_argument('--ids', default='')
    ap.add_argument('--reject', default='')
    ap.add_argument('--by', default='owner')
    ap.add_argument('--topic', default='auto')
    ap.add_argument('--out')
    ap.add_argument('--mark-used', action='store_true')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--why', default='rejected')
    # whole-edit review (owner 2026-11-07): recut / add-section / timeline
    ap.add_argument('--id', default='', help='recut: the staged item; add-section: the source id')
    ap.add_argument('--s', type=float, default=None, help='recut / add-section: section start (source seconds)')
    ap.add_argument('--e', type=float, default=None, help='recut / add-section: section end (source seconds)')
    ap.add_argument('--like', default='', help='add-section: copy the metadata of this staged item (same edit)')
    ap.add_argument('--serves', default=None, help='recut / add-section: entry keys this section serves (comma-separated)')
    ap.add_argument('--themes', default=None, help='recut / add-section: action,beauty,romance (comma-separated)')
    ap.add_argument('--entry', default=None, help='recut / add-section: file the section under this entry key')
    ap.add_argument('--no-snap', action='store_true', help='recut / add-section: keep the exact times (no scene-cut snap)')
    ap.add_argument('--apply', action='store_true', help='rescreen: drop failing items from the live pool')
    a = ap.parse_args()
    if a.cmd == 'plan-shows':
        for p in plan_shows():
            print(f'{p["score"]:5.2f}  {p["name"]:<28} cast {",".join(p["cast"])}  topics {",".join(p["topics"])}')
    elif a.cmd == 'plan':
        for p in plan():
            print(f'{p["score"]:5.2f}  {p["name"]:<22} have {p["have"]}  blocks {",".join(p["topics"])}')
    elif a.cmd == 'mine':
        import ranking_compilation as RC
        for part in filter(None, a.avoid.split(';')):
            sid, rng = part.rsplit(':', 1)
            lo_, hi_ = map(float, rng.split('-'))
            RC.AVOID.setdefault(sid, []).append((lo_, hi_))
        if not (a.per_entry or a.entries.strip() or a.retry.strip()):
            print(json.dumps(mine_library(a.budget_min, a.work, a.state, a.max_entries, max(a.max_dl, 4), a.offline,
                                          [x.strip() for x in a.first.split(',') if x.strip()],
                                          [x.strip() for x in a.shows.split(',') if x.strip()] or None),
                             ensure_ascii=False, indent=1, default=str))
            return
        print(json.dumps(mine(a.budget_min, a.work, a.state, a.max_entries, a.max_dl, a.offline,
                              set(filter(None, a.entries.split(','))) or None, set(filter(None, a.prefer.split(','))),
                              [x.strip() for x in a.first.split(',') if x.strip()], set(filter(None, a.retry.split(','))) or ()),
                         ensure_ascii=False, indent=1, default=str))
    elif a.cmd == 'publish':
        print(json.dumps(publish(a.dest, a.label, a.merge_remote)))
    elif a.cmd == 'pull-staged':
        print(pull_staged(), 'new staged items')
    elif a.cmd == 'review':
        for it in manifest(staged_dir())['items']:
            if it['status'] == 'staged':
                print(f'{it["id"]}  {it["entry_name"]:<20} {it.get("editor_handle")}  char {it.get("char_conf")} '
                      f'ev {it.get("eventful")} song {it.get("song")}  {it.get("age_days")} d  '
                      + (f'serves {",".join(it.get("serves") or [])} themes {"/".join(it.get("themes") or [])}  '
                         if it.get('lib') else '')
                      + f'{os.path.join(staged_dir(), it.get("sheet") or "")}')
    elif a.cmd == 'import':
        print(import_items(set(filter(None, a.ids.split(','))), set(filter(None, a.reject.split(','))), a.by), 'imported')
        print(clean_sources(), 'whole edits deleted (all their sections decided)')
    elif a.cmd in ('recut', 'add-section'):
        lst = lambda x: None if x is None else [y.strip() for y in x.split(',') if y.strip()]
        if a.cmd == 'recut':
            r = recut(a.id, a.s, a.e, lst(a.serves), lst(a.themes), not a.no_snap, a.entry)
        else:
            r = add_section(a.id, a.s, a.e, a.like, lst(a.serves), lst(a.themes), not a.no_snap, a.entry)
        print(json.dumps(r, ensure_ascii=False, indent=1))
    elif a.cmd == 'timeline':
        man = manifest(staged_dir())
        for src in man.get('sources') or []:
            if src.get('status') != 'done':
                print(src['source_id'], src.get('handle'), (src.get('title') or '')[:60], 'items', src.get('items'),
                      [os.path.join(staged_dir(), x) for x in src.get('timelines') or []])
    elif a.cmd == 'clean-sources':
        print(clean_sources(), 'whole edits deleted')
    elif a.cmd == 'push-state':
        print(push_state())
    elif a.cmd == 'eligible':
        el = eligible_topics()
        print(json.dumps({'eligible': el, 'next': choose_topic()}) if a.json else '\n'.join(el))
    elif a.cmd == 'assemble':
        r = assemble(a.topic, a.out, a.work, a.mark_used)
        print(json.dumps(r, ensure_ascii=False, indent=1))
        sys.exit(0 if r.get('ok') else 2)
    elif a.cmd == 'mark':
        mark(rj(a.result, {}))
        print('marked')
    elif a.cmd == 'status':
        print(library_report())
    elif a.cmd == 'remove':
        print(remove_items(set(filter(None, a.ids.split(','))), a.by, a.why), 'removed')
    elif a.cmd == 'rescreen':
        print(json.dumps(rescreen(a.apply), ensure_ascii=False, indent=1))
    elif a.cmd == 'recast':
        print(json.dumps(recast(set(filter(None, (a.ids or '').split(','))) or None), ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
