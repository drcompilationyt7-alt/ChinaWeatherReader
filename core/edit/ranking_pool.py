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
Entries are pooled by ENTRY KEY (name + show), so a Gojo segment serves every list that has Gojo.

Item: {id, entry_key, entry_name, kind, anime, file, start, end (in the source), clip_in, clip_out (the window inside the
clip), source_id, source_title, source_channel, editor_handle, upload_date, age_days, views, char_conf, char_method,
char_votes, eventful, song, song_name, quality, rank_score, safety, watermark, editor_tags, rights, proof, status, added,
reviewed_by}

    python core/edit/ranking_pool.py plan                         # which entries block which topics (mining order)
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

MARGIN = 1.5                 # s kept on each side of the chosen window (re-cut room), the rest of the edit is never stored
SEG = (18.0, 24.0)           # phrase-aligned window per entry (3 revealed entries ~ 60 s)
REUSE_GAP = 8                # an edit (source) is not reused within the next 8 ranking uploads
TOPIC_GAP = 4                # a topic is not repeated within the next 4 ranking uploads (when another is eligible)
PER_ENTRY_TARGET = 2         # reviewed + staged items wanted per entry (variety under the reuse gap)
CHAR_MIN = 0.6
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

def usable(items, key, led, exclude_sources=()):
    """Reviewed items of an entry that may be used now: not within the reuse gap, editor not blocklisted, character
    check passed. Best first (rank score)."""
    recent = set((led.get('ranking_recent') or [])[-REUSE_GAP:]) | set(exclude_sources)
    bl = blocklist()
    out = [it for it in items if it.get('entry_key') == key and it.get('status') == 'reviewed'
           and it['source_id'] not in recent and (it.get('editor_handle') or '').lower().lstrip('@') not in bl
           and (it.get('char_conf') or 0) >= CHAR_MIN]
    # anime-footage edits first (owner 2026-10-25: a manga-panel edit is only a fallback), then the rank score
    return sorted(out, key=lambda it: (bool(it.get('manga')), -(it.get('rank_score') or it.get('quality') or 0)))


def pick_items(T, items, led):
    """One item per revealed rank, distinct sources AND distinct editors (never two excerpts of one editor in a video).
    -> {rank: item} or None."""
    picks, used_src, used_ed = {}, set(), set()
    for rank in RT.revealed_ranks(T):
        e = T['entries'][rank - 1]
        cands = [it for it in usable(items, entry_key(e), led, used_src)
                 if (it.get('editor_handle') or it.get('source_channel') or '').lower() not in used_ed]
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
    for it in merged.values():
        if it.get('status') in ('reviewed', 'staged'):
            have[it['entry_key']] = have.get(it['entry_key'], 0) + 1
            if not it.get('manga'):
                anime[it['entry_key']] = anime.get(it['entry_key'], 0) + 1
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
    if c and time.time() - c.get('ts', 0) < refresh_days * 86400 and c.get('q', e['q']) == e['q']:   # new queries: refresh
        return c['items']
    seen, out = set(), []
    for q in e['q']:
        for x in (RC.search(q) or []):
            if x.get('id') in seen:
                continue
            seen.add(x.get('id'))
            ok, why = RC.prefilter(x, e)
            if ok:
                out.append({k2: x.get(k2) for k2 in ('id', 'title', 'channel', 'uploader_id', 'view_count', 'duration')})
    out.sort(key=lambda x: -(x.get('view_count') or 0))
    st['candidates'][k] = {'name': e['name'], 'ts': time.time(), 'at': time.strftime('%Y-%m-%d'), 'items': out, 'q': e['q']}
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


def mine(budget_min=60, work=None, state_file=None, max_entries=12, max_dl=3, offline=False, entries=None, prefer=()):
    """Mining step (one job). Entries in plan() order (topic blockers first); per entry: candidate list, rights /
    info gates (old + idle editors first, vetoed / blocklisted skipped), download <= max_dl, every gate, the best
    phrase window -> staged clip (+ margin), review sheet, proof. Stops at the time budget."""
    import ranking_compilation as RC
    t0 = time.time()
    work = work or os.path.join(ROOT, 'output', 'ranking-mine')
    RC.YT_CACHE[0] = os.path.join(work, 'cache', 'yt')
    RC.OFFLINE[0] = offline
    RC.PREFER.update(prefer)
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
        todo = [p for p in todo if p['key'] in entries or p['name'] in entries] or \
            [{'key': k, 'name': v[0]['name'], 'entry': v[0], 'topics': v[1], 'score': 1, 'have': 0}
             for k, v in all_entries().items() if k in entries or v[0]['name'] in entries]
    summary = {'entries': [], 'staged': 0}
    topics = RT.load()
    for p in todo[:max_entries]:
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
        done = {sid for sid, v in st['sources'].items() if v.get('status') in ('staged', 'reviewed', 'rejected', 'gate_reject')}
        report = {'searched': 0, 'downloaded': 0, 'watermark_free': 0, 'reject': {}, 'evaluated': [], '_dl': {}}
        # an entry that alone blocks a whole topic (Yuta for jjk_strongest) gets twice the downloads
        try:
            pre = RC.find_source(e, None, None, work, 0, done, report, max_dl=max_dl * (2 if p['score'] >= 1 else 1))
        except Exception as ex:  # noqa: BLE001  (one bad entry never kills the run)
            log(f'{e["name"]}: candidate search / download failed: {type(ex).__name__}: {ex}')
            summary['entries'].append({'entry': e['name'], 'key': key, 'error': f'{type(ex).__name__}: {ex}'[:300]})
            continue
        row = {'entry': e['name'], 'key': key, 'candidates': len(cands), 'downloaded': report['downloaded'],
               'clean': len(pre), 'reject': report['reject'], 'staged': []}
        for r in report['evaluated']:
            if r.get('verdict') == 'reject':
                st['sources'][r['id']] = {'status': 'gate_reject', 'why': r.get('why'), 'entry': key, 'at': time.strftime('%Y-%m-%d')}
        for res, rrow in pre:
            if (time.time() - t0) / 60 > budget_min + 20:
                break
            try:
                res = RC.evaluate(res, e, others, judge(), SEG, e.get('pace', 'action'), all_infos=RC.cached_infos(),
                                  theme=entry_theme(p['topics'], topics))
            except Exception as ex:  # noqa: BLE001  (one bad candidate never kills the run)
                log(f'{res.get("id")}: evaluate failed: {type(ex).__name__}: {ex}')
                res.update(verdict='reject', why=f'eval_error_{type(ex).__name__}', windows=[])
            if res['verdict'] != 'ok':
                st['sources'][res['id']] = {'status': 'gate_reject', 'why': res.get('why') or 'no_window', 'entry': key,
                                            'at': time.strftime('%Y-%m-%d'),
                                            'windows': [w.get('reject') for w in (res.get('windows') or [])[:4]]}
                continue
            try:
                it = stage_item(res, e, key, sd, RC)
            except Exception as ex:  # noqa: BLE001
                log(f'{res.get("id")}: staging failed: {type(ex).__name__}: {ex}')
                continue
            if it.get('window_marks'):            # full-resolution tag / logo check of the cut window
                st['sources'][res['id']] = {'status': 'gate_reject', 'why': 'window_mark', 'marks': it['window_marks'][:3],
                                            'entry': key, 'at': time.strftime('%Y-%m-%d')}
                continue
            man['items'] = [x for x in man['items'] if x['id'] != it['id']] + [it]
            st['sources'][res['id']] = {'status': 'staged', 'entry': key, 'item': it['id'], 'at': time.strftime('%Y-%m-%d')}
            row['staged'].append(it['id'])
            summary['staged'] += 1
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


def stage_item(res, e, key, sd, RC):
    w = res['window']
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
          'char_conf': res.get('conf'), 'char_method': (res.get('match') or {}).get('method'),
          'char_votes': (res.get('match') or {}).get('votes'), 'char_others': (res.get('match') or {}).get('others'),
          'eventful': w.get('eventful'), 'song': w.get('song'), 'song_name': w.get('song_name'), 'quality': w.get('quality'),
          'theme': w.get('theme'), 'ref_score': w.get('ref_score'), 'ref_groups': w.get('ref_groups'),
          'manga': bool(w.get('manga')), 'manga_share': w.get('manga_share'),
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
    it['sheet'] = os.path.relpath(sheet(clip, os.path.join(sd, 'sheets', iid + '.jpg'), lab) or '', sd).replace('\\', '/')
    return it


# ------------------------------------------------------------------ staging transport (CI / PC miner <-> owner PC)

def publish(dest, label='ci'):
    """The staged items not yet published -> one tar (clips, sheets, proofs, a clips.json of just those items) + the
    state, in `dest`. Marks them published (published_in) in the local staged manifest."""
    sd = staged_dir()
    man = manifest(sd)
    new = [it for it in man['items'] if it.get('status') == 'staged' and not it.get('published_in')]
    os.makedirs(dest, exist_ok=True)
    out = {'tar': None, 'items': len(new)}
    if new:
        name = f'ranking-cand-{time.strftime("%Y%m%d-%H%M", time.gmtime())}-{label}-n{len(new)}.tar'
        tp = os.path.join(dest, name)
        with tarfile.open(tp, 'w') as t:
            for it in new:
                for rel in (it['file'], it.get('sheet'), (it.get('proof') or {}).get('snapshot_file')):
                    if rel and os.path.exists(os.path.join(sd, rel)):
                        t.add(os.path.join(sd, rel), arcname=rel)
            tmp = os.path.join(dest, 'clips.batch.json')
            wj(tmp, {'version': 2, 'items': new, 'batch': name})
            t.add(tmp, arcname='clips.json')
            os.remove(tmp)
        for it in new:
            it['published_in'] = name
        save_manifest(sd, man)
        out['tar'] = tp
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
        shutil.rmtree(tmp, ignore_errors=True)
    save_manifest(sd, man)
    return n


def import_items(ids, reject=(), by='owner'):
    """Reviewed staged items -> the live pool (status reviewed); rejected ones stay in staging as rejected."""
    sd, ld = staged_dir(), live_dir()
    sm, lm = manifest(sd), manifest(ld)
    st = rj(state_path(), {'candidates': {}, 'sources': {}})
    n = 0
    for it in sm['items']:
        if it['id'] in reject:
            it['status'] = 'rejected'
            it['reviewed_by'] = by
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
    ap.add_argument('cmd', choices=['plan', 'mine', 'publish', 'pull-staged', 'review', 'import', 'push-state', 'eligible',
                                    'assemble', 'status', 'mark', 'remove', 'rescreen'])
    ap.add_argument('--result')
    ap.add_argument('--budget-min', type=float, default=60)
    ap.add_argument('--work')
    ap.add_argument('--state')
    ap.add_argument('--max-entries', type=int, default=12)
    ap.add_argument('--max-dl', type=int, default=3)
    ap.add_argument('--entries', default='', help='mine: only these entry keys / names (comma-separated)')
    ap.add_argument('--offline', action='store_true')
    ap.add_argument('--prefer', default='', help='mine: source ids searched first + rank bonus (owner-approved edits)')
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
    ap.add_argument('--apply', action='store_true', help='rescreen: drop failing items from the live pool')
    a = ap.parse_args()
    if a.cmd == 'plan':
        for p in plan():
            print(f'{p["score"]:5.2f}  {p["name"]:<22} have {p["have"]}  blocks {",".join(p["topics"])}')
    elif a.cmd == 'mine':
        print(json.dumps(mine(a.budget_min, a.work, a.state, a.max_entries, a.max_dl, a.offline,
                              set(filter(None, a.entries.split(','))) or None, set(filter(None, a.prefer.split(',')))),
                         ensure_ascii=False, indent=1, default=str))
    elif a.cmd == 'publish':
        print(json.dumps(publish(a.dest, a.label)))
    elif a.cmd == 'pull-staged':
        print(pull_staged(), 'new staged items')
    elif a.cmd == 'review':
        for it in manifest(staged_dir())['items']:
            if it['status'] == 'staged':
                print(f'{it["id"]}  {it["entry_name"]:<20} {it.get("editor_handle")}  char {it.get("char_conf")} '
                      f'ev {it.get("eventful")} song {it.get("song")}  {it.get("age_days")} d  '
                      f'{os.path.join(staged_dir(), it.get("sheet") or "")}')
    elif a.cmd == 'import':
        print(import_items(set(filter(None, a.ids.split(','))), set(filter(None, a.reject.split(','))), a.by), 'imported')
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


if __name__ == '__main__':
    main()
