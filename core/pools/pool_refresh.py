"""
Monthly refresh of the sound / slow-mo vibe pools with the quiz's SAFE-SWAP rules (core/quiz/pool_refresh.py), on top of the
pool tool of core/edit/vibe_pool.py (format: core/edit/refs/pool-format.md). vibe_pool.py builds (refresh), checks the files
(validate) and swaps (promote: new items + the live pool's FRESH items; used / retired items are dropped). This wrapper adds
the owner-approved QUALITY BASELINE and the "keep the old pool if anything fails" orchestration:

  baseline --approve   store the current live pool's quality stats (core/edit/assets/pools-baseline.json). Run it only when
                       the owner approved the sample renders. Without a baseline nothing is ever promoted.
  check                staging vs the baseline: files exist, decode-able manifests, safety gate == pass, no duplicate ids,
                       items below the baseline's 10th percentile on a metric are DROPPED from staging (files too), batch
                       medians within 15% of the baseline's, enough items (the section-6 minimums, and new + the live pool's
                       fresh items >= 80% of the live fresh count), at least MIN_NEW new items. Writes check-report.json.
  run                  refresh (vibe_pool.py) -> check -> vibe_pool.py validate -> vibe_pool.py promote. Any failing step
                       leaves the live pool untouched (exit 1). --dry-run stops before the promote.

Metrics (higher is better): scores.aesthetic, sharpness, motion, appeal, taste, and for sound hit clips vis.score (the
visible-source score). No metric is invented: a metric absent from an item is skipped. Kaggle is not used (CPU is enough
per pool-format.md section 7).

SOUND pools (v1.2): every new fresh sound clip must carry a `vis` stamp (core/pools/pool_visible.py = the visible-source gate
of core/edit/sound_visible.py run once per clip: HPSS / transient events + "the cause of the sound is on screen"). A clip with a
visible hit is a HIT clip, the others are BED clips (picture-only cuts). check also drops pictures that duplicate a live or an
earlier staged clip (5-frame 16x9 signature), and fails a batch with too few new HIT clips, a hit share or median visible-source
score below the baseline, or too many voiced hits.

CI split (.github/workflows/edit-pool-refresh.yml): `build` shards (refresh --shard K/N -> stamp -> pack, artifact per shard) and
one `swap` job (merge shards into a staging copy of the live manifests -> check -> validate -> promote -> push).
  python core/pools/pool_refresh.py baseline --approve | propose-baseline | preflight
  python core/pools/pool_refresh.py shard --staging DIR [--shard K/N] [--budget-min M] [--queries Q]   (refresh + stamp + pack)
  python core/pools/pool_refresh.py merge --staging DIR --from SHARD_DIR [SHARD_DIR ...]
  python core/pools/pool_refresh.py stamp|check --staging DIR
  python core/pools/pool_refresh.py run --staging DIR [--dry-run] [--skip-refresh] [--shard K/N]
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
VIBE_POOL = os.environ.get('VIBE_POOL_CMD') or os.path.join(ROOT, 'core', 'edit', 'vibe_pool.py')
BASELINE = os.environ.get('POOL_BASELINE') or os.path.join(ROOT, 'core', 'edit', 'assets', 'pools-baseline.json')
METRICS = ('aesthetic', 'sharpness', 'motion', 'appeal', 'taste')
MIN_FRESH = {'sound': 70, 'slowmo': 70}      # pool-format.md section 6
KEEP_SHARE = 0.8
TOL = 0.15
MIN_NEW = int(os.environ.get('POOL_MIN_NEW', '20'))
MIN_NEW_HITS = int(os.environ.get('POOL_MIN_NEW_HITS', '40'))      # new sound HIT clips per sound vibe (visible source)
MAX_VOICED = float(os.environ.get('POOL_MAX_VOICED', '0.4'))        # voiced share of the new hits (render cap 0.35)
REQUIRE_VIS = os.environ.get('POOL_REQUIRE_VIS', '1') != '0'
BASELINE_PROPOSED = os.path.join(os.path.dirname(BASELINE), 'pools-baseline.proposed.json')
sys.path.insert(0, os.path.join(ROOT, 'core', 'edit'))
sys.path.insert(0, HERE)


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def pool_dir():
    return os.path.abspath(os.environ.get('EDIT_POOL_DIR') or os.path.join(ROOT, 'output', 'edit-pools'))


def vibes(root):
    out = []
    for preset in ('sound', 'slowmo'):
        p = os.path.join(root, preset)
        if os.path.isdir(p):
            for v in sorted(os.listdir(p)):
                if os.path.isfile(os.path.join(p, v, 'clips.json')):
                    out.append((preset, v))
    return out


def load_clips(root, preset, vibe):
    with open(os.path.join(root, preset, vibe, 'clips.json'), encoding='utf-8') as f:
        return json.load(f)


def metric_values(it):
    s = it.get('scores') or {}
    m = {k: float(s[k]) for k in METRICS if isinstance(s.get(k), (int, float))}
    v = it.get('vis') or {}
    if v.get('hit') and isinstance(v.get('score'), (int, float)):
        m['vis_score'] = float(v['score'])
    return m


def batch_stats(items):
    out = {'n': len(items), 'metrics': {}}
    for k in METRICS + ('vis_score',):
        v = [metric_values(it)[k] for it in items if k in metric_values(it)]
        if len(v) >= 5:
            out['metrics'][k] = {'p10': float(np.percentile(v, 10)), 'median': float(np.median(v)), 'n': len(v)}
    st = [it for it in items if it.get('vis')]
    if st:
        hits = [it for it in st if it['vis'].get('hit')]
        out['hit_share'] = len(hits) / len(st)
        out['voiced_share'] = (sum(1 for it in hits if it['vis'].get('voiced')) / len(hits)) if hits else 0.0
        out['hits'] = len(hits)
    return out


def fresh(items):
    return [it for it in items if it.get('status', 'fresh') == 'fresh']


def approve_baseline(root=None, out=None, by='owner'):
    """Stats of the live pool's FRESH clips. The pool must be visible-source stamped first (pool_visible.py stamp)."""
    root = root or pool_dir()
    res = {'approved_at': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'approved_by': by, 'pools': {}}
    for preset, vibe in vibes(root):
        items = fresh(load_clips(root, preset, vibe).get('items', []))
        res['pools'][f'{preset}/{vibe}'] = batch_stats(items)
    os.makedirs(os.path.dirname(out or BASELINE), exist_ok=True)
    with open(out or BASELINE, 'w', encoding='utf-8') as f:
        json.dump(res, f, indent=1)
    log(f'baseline stored: {out or BASELINE} ({len(res["pools"])} pools)')
    return res


def check(staging, live=None, baseline=None, fix=True):
    live, baseline = live or pool_dir(), baseline or BASELINE
    rep = {'ok': False, 'checked_at': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'problems': [], 'dropped': [], 'pools': {}}
    if not os.path.exists(baseline):
        rep['problems'].append('no quality baseline yet: the owner has not approved a batch (pool_refresh.py baseline --approve)')
        return rep
    base = json.load(open(baseline, encoding='utf-8'))['pools']
    vs = vibes(staging)
    if not vs:
        rep['problems'].append('staging has no vibe pools')
    try:
        import pool_visible as PV
    except Exception:  # noqa: BLE001  (the picture-signature compare needs numpy only)
        PV = None
    for preset, vibe in vs:
        name = f'{preset}/{vibe}'
        man = load_clips(staging, preset, vibe)
        keep, seen = [], set()
        b = base.get(name)
        lp = os.path.join(live, preset, vibe, 'clips.json')
        live_items = fresh(json.load(open(lp, encoding='utf-8')).get('items', [])) if os.path.isfile(lp) else []
        for it in man.get('items', []):
            if it.get('status', 'fresh') != 'fresh':
                keep.append(it)
                continue
            why = None
            if it['id'] in seen:
                why = 'duplicate id'
            f = os.path.join(staging, preset, vibe, it.get('file', ''))
            if not os.path.isfile(f) or os.path.getsize(f) < 1024:
                why = why or 'missing file'
            if (it.get('safety') or {}).get('gate') != 'pass':
                why = why or 'safety gate not pass'
            if (it.get('scores') or {}).get('face_ok') is False:
                why = why or 'colour gate (face_ok false)'
            if preset == 'sound' and REQUIRE_VIS and not why and not (it.get('vis') or {}).get('v'):
                why = 'no visible-source stamp (pool_visible.py stamp)'
            if not why and PV and (it.get('vis') or {}).get('sig'):
                d = PV.dup_of(it, [x for x in keep if x.get('status', 'fresh') == 'fresh'] + live_items)
                if d:
                    why = f'same picture as {d}'
            if not why and b:
                low = [k for k, v in metric_values(it).items() if k in b['metrics'] and v < b['metrics'][k]['p10']]
                if low:
                    why = 'below baseline p10: ' + ','.join(low)
            if why:
                rep['dropped'].append({'pool': name, 'id': it['id'], 'why': why})
                if fix and os.path.isfile(f):
                    os.remove(f)
                continue
            seen.add(it['id'])
            keep.append(it)
        fr = fresh(keep)
        live_fresh = len(live_items)
        live_ids = {x['id'] for x in live_items}
        new = [it for it in fr if it['id'] not in live_ids]
        new_items = len(new)
        combined = len(fr) + len([i for i in live_ids if i not in seen])
        st = batch_stats(fr)
        rep['pools'][name] = {'staged_fresh': len(fr), 'new': new_items, 'live_fresh': live_fresh, 'combined': combined, 'stats': st}
        if new_items < MIN_NEW:
            rep['problems'].append(f'{name}: only {new_items} new items (< {MIN_NEW}): nothing worth swapping')
        if combined < MIN_FRESH[preset]:
            rep['problems'].append(f'{name}: {combined} fresh clips after the swap < minimum {MIN_FRESH[preset]}')
        if live_fresh and combined < KEEP_SHARE * live_fresh:
            rep['problems'].append(f'{name}: {combined} clips after the swap, well below the live pool ({live_fresh})')
        if b and fr:
            for k, v in st['metrics'].items():
                if k in b['metrics'] and v['median'] < (1 - TOL) * b['metrics'][k]['median']:
                    rep['problems'].append(f"{name}: median {k} {v['median']:.3g} < baseline {b['metrics'][k]['median']:.3g} - {int(TOL * 100)}%")
        elif not b:
            rep['problems'].append(f'{name}: no baseline entry for this vibe (owner approval needed)')
        if preset == 'sound' and REQUIRE_VIS and new:
            ns = batch_stats(new)
            rep['pools'][name]['new_stats'] = ns
            if ns.get('hits', 0) < MIN_NEW_HITS:
                rep['problems'].append(f'{name}: only {ns.get("hits", 0)} new visible-source HIT clips (< {MIN_NEW_HITS})')
            if b and b.get('hit_share') and ns.get('hit_share', 0) < (1 - TOL) * b['hit_share']:
                rep['problems'].append(f'{name}: hit share {ns.get("hit_share", 0):.2f} < baseline {b["hit_share"]:.2f} - {int(TOL * 100)}%')
            if ns.get('voiced_share', 0) > MAX_VOICED:
                rep['problems'].append(f'{name}: {ns["voiced_share"]:.0%} of the new hits are voiced (> {MAX_VOICED:.0%})')
        man['items'] = keep
        if fix:
            with open(os.path.join(staging, preset, vibe, 'clips.json'), 'w', encoding='utf-8') as f:
                json.dump(man, f, ensure_ascii=False, indent=1)
    rep['ok'] = not rep['problems']
    os.makedirs(staging, exist_ok=True)
    with open(os.path.join(staging, 'check-report.json'), 'w', encoding='utf-8') as f:
        json.dump(rep, f, indent=1)
    return rep


# ------------------------------------------------------------------ CI building blocks (shards, merge, stamp, preflight)

def stamp(staging):
    """Visible-source stamp of every new fresh sound clip of `staging` (the live-status rows are not fresh: untouched)."""
    import pool_visible as PV
    return PV.stamp(staging)


def init_staging(staging, live=None):
    """Staging = the live manifests with every row marked non-fresh ('live' / 'retired'), no media: de-duplication sees the
    whole live pool, only newly added rows are 'fresh' (the layout `vibe_pool.py refresh --staging` starts from)."""
    live = live or pool_dir()
    for preset, vibe in vibes(live):
        d = os.path.join(staging, preset, vibe)
        os.makedirs(d, exist_ok=True)
        for kind in ('clips', 'sounds'):
            f = os.path.join(live, preset, vibe, f'{kind}.json')
            if os.path.isfile(f) and not os.path.isfile(os.path.join(d, f'{kind}.json')):
                m = json.load(open(f, encoding='utf-8'))
                if kind == 'clips':
                    m['items'] = [dict(it, status='live' if it.get('status') == 'fresh' else 'retired') for it in m.get('items', [])]
                json.dump(m, open(os.path.join(d, f'{kind}.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)


def pack(staging):
    """Shrink a shard for the artifact: only the new fresh rows + their files (no live rows, no work caches, no sheets)."""
    n = 0
    for preset, vibe in vibes(staging):
        m = load_clips(staging, preset, vibe)
        m['items'] = fresh(m.get('items', []))
        n += len(m['items'])
        json.dump(m, open(os.path.join(staging, preset, vibe, 'clips.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        shutil.rmtree(os.path.join(staging, preset, vibe, 'sheets'), ignore_errors=True)
    for d in ('_lib', '_hit'):
        shutil.rmtree(os.path.join(staging, d), ignore_errors=True)
    return n


def merge(staging, shards, live=None):
    """Staging = init_staging(live) + the fresh rows of every shard dir, de-duplicated against: the live pool, the used ledger's
    moments, the blocklist, the other vibes of the preset and earlier shards (same id / overlapping moment of one source)."""
    import vibe_pool as VP
    init_staging(staging, live)
    led, bl = VP.load_ledger(), VP.load_blocklist()
    added, skipped = 0, {}
    for sh in shards:
        for preset, vibe in vibes(sh):
            sp = os.path.join(staging, preset, vibe, 'clips.json')
            dst = load_clips(staging, preset, vibe) if os.path.isfile(sp) else {'version': 1, 'preset': preset, 'vibe': vibe, 'items': []}
            others = [it for pv, vv in vibes(staging) if pv == preset and vv != vibe for it in load_clips(staging, pv, vv).get('items', [])]
            have = {it['id'] for it in dst['items']}
            os.makedirs(os.path.join(staging, preset, vibe, 'clips'), exist_ok=True)
            for it in load_clips(sh, preset, vibe).get('items', []):
                if it.get('status', 'fresh') != 'fresh':
                    continue
                src = os.path.join(sh, preset, vibe, it['file'])
                s0, e0 = float(it.get('start') or 0), float(it.get('end') or 0)
                why = ('missing' if not os.path.isfile(src) else 'dup_id' if it['id'] in have
                       else 'ledger' if VP.key(preset, vibe, 'clip', it['id']) in led['items']
                       else 'moment_used' if VP.moment_taken(it['source_id'], s0, e0, led, dst['items'] + others)
                       else 'blocked' if VP.blocked(it['source_id'], s0, e0, bl) else None)
                if why:
                    skipped[why] = skipped.get(why, 0) + 1
                    continue
                for g in [it['file'], it['file'] + '.portrait.json', it['file'] + '.flow.json']:
                    if os.path.isfile(os.path.join(sh, preset, vibe, g)):
                        shutil.copy2(os.path.join(sh, preset, vibe, g), os.path.join(staging, preset, vibe, g))
                dst['items'].append(it)
                have.add(it['id'])
                added += 1
            json.dump(dst, open(sp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    return {'added': added, 'skipped': skipped}


def preflight(n_try=3):
    """Can this runner download from YouTube (cookies from YT_COOKIES)? A bot-block is reported, never raised."""
    import re
    import time
    cmd = [sys.executable, '-m', 'yt_dlp', '--no-warnings', '--no-playlist', '-s', '-f', 'worst',
           '--match-filter', 'duration > 60', 'ytsearch1:anime gun reload scene']
    ck = os.environ.get('YT_COOKIES')
    has_ck = bool(ck and os.path.isfile(ck) and os.path.getsize(ck) > 100)
    if has_ck:
        cmd[3:3] = ['--cookies', ck]
    pat = re.compile(r"not a bot|sign in to confirm|http error 429|too many requests|rate.?limit|use --cookies|requires (?:login|authentication)", re.I)
    last = ''
    for k in range(n_try):
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
        last = (r.stderr or '')[-400:]
        if r.returncode == 0:
            return {'ok': True, 'blocked': False, 'cookies': has_ck}
        if not pat.search(r.stderr or ''):
            return {'ok': False, 'blocked': False, 'reason': 'yt-dlp failed: ' + last[-200:], 'cookies': has_ck}
        time.sleep(30 * (k + 1))
    return {'ok': False, 'blocked': True, 'reason': 'YouTube bot-block / rate limit: ' + last[-200:], 'cookies': has_ck}


def build_shard(staging, shard=None, budget=270, queries=None):
    """One CI build shard. 40 % of the time: library refresh of every vibe (queries K::N; yields the slow-mo pools and the BED
    clips of the sound pools). The rest, sound only: HIT-FIRST mining (core/pools/hit_scan.py): fetch fight sources through yt-dlp
    (queries K::N, shared pacing / bot-block back-off), scan their audio transients for a visible cause, cut those moments,
    run them through library.py's gates, import, stamp. Then the visible-source stamp of everything new and pack."""
    import time
    k, n = (int(x) for x in shard.split('/')) if shard else (0, 1)
    t0 = time.time()
    if os.path.exists(staging):
        shutil.rmtree(staging)
    extra = (['--shard', shard] if shard else []) + (['--queries', str(queries)] if queries else [])
    vp('refresh', '--preset', 'all', '--budget-min', str(max(5, int(budget * 0.4))), '--staging', staging, *extra, check_rc=False)
    rep = {}
    try:
        import hit_scan as HS
        left = budget - (time.time() - t0) / 60.0
        work = os.path.join(staging, '_hit')
        qs = HS.queries_for_shard(k, n)[:queries] if queries else HS.queries_for_shard(k, n)
        HS.fetch(work, qs, per_query=3, max_sources=int(os.environ.get('HIT_MAX_SOURCES', '40')), budget_min=left * 0.3)
        left = budget - (time.time() - t0) / 60.0
        globs = [os.path.join(work, 'src', '*__all.mkv'), os.path.join(HS.edit_cache(), 'src', '*__all.mkv')]
        rep['hit_first'] = HS.grow(work, globs, budget_min=max(3, left * 0.35), root=staging, library_budget_min=max(5, left * 0.5))
    except Exception as e:  # noqa: BLE001  (hit-first mining is additive: a failure keeps what the library refresh found)
        log('hit-first mining failed:', repr(e)[:300])
        rep['hit_first_error'] = repr(e)[:300]
    rep['stamp'] = stamp(staging)
    rep['packed'] = pack(staging)
    return rep


def propose_baseline():
    """The CURRENT live pool's stats as a proposal for the owner (check never reads it; renaming it to pools-baseline.json = approve)."""
    return approve_baseline(out=BASELINE_PROPOSED, by='proposal (not approved)')


def vp(*a, check_rc=True):
    cmd = [sys.executable, VIBE_POOL, *a] if VIBE_POOL.endswith('.py') else [VIBE_POOL, *a]
    log('+', ' '.join(cmd))
    r = subprocess.run(cmd)
    if check_rc and r.returncode != 0:
        raise SystemExit(f'{os.path.basename(VIBE_POOL)} {a[0]} failed ({r.returncode}): the live pool stays')
    return r.returncode


def run(staging, dry=False, skip_refresh=False, budget=300, shard=None, queries=None):
    if not skip_refresh:
        if os.path.exists(staging):
            shutil.rmtree(staging)
        extra = (['--shard', shard] if shard else []) + (['--queries', str(queries)] if queries else [])
        vp('refresh', '--preset', 'all', '--budget-min', str(budget), '--staging', staging, *extra)
    if not os.environ.get('POOL_SKIP_STAMP'):
        stamp(staging)
    rep = check(staging)
    log(json.dumps({'ok': rep['ok'], 'problems': rep['problems'][:20], 'dropped': len(rep['dropped'])}, indent=1))
    if not rep['ok']:
        log('CHECK FAILED: the live pool stays as it is')
        return 1
    vp('validate', '--dir', staging)
    if dry:
        log('dry run: validated, not promoted')
        return 0
    vp('promote', '--staging', staging)
    log('promoted')
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['baseline', 'propose-baseline', 'check', 'run', 'stamp', 'shard', 'merge', 'preflight'])
    ap.add_argument('--approve', action='store_true')
    ap.add_argument('--staging', default=os.path.join(ROOT, 'output', 'edit-pools-next'))
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--skip-refresh', action='store_true')
    ap.add_argument('--budget-min', type=int, default=300)
    ap.add_argument('--shard')
    ap.add_argument('--queries', type=int)
    ap.add_argument('--from', dest='shards', nargs='*', default=[])
    a = ap.parse_args()
    if a.cmd == 'baseline':
        if not a.approve:
            sys.exit('baseline needs --approve (only after the owner approved the sample renders)')
        approve_baseline()
    elif a.cmd == 'propose-baseline':
        propose_baseline()
    elif a.cmd == 'check':
        rep = check(a.staging)
        log(json.dumps(rep, indent=1)[:3000])
        sys.exit(0 if rep['ok'] else 1)
    elif a.cmd == 'stamp':
        print(json.dumps(stamp(a.staging)))
    elif a.cmd == 'shard':
        print(json.dumps(build_shard(a.staging, a.shard, a.budget_min, a.queries)))
    elif a.cmd == 'merge':
        print(json.dumps(merge(a.staging, a.shards)))
    elif a.cmd == 'preflight':
        r = preflight()
        print(json.dumps(r))
        if os.environ.get('GITHUB_OUTPUT'):
            with open(os.environ['GITHUB_OUTPUT'], 'a') as f:
                f.write(f"blocked={'true' if r.get('blocked') else 'false'}\nytok={'true' if r.get('ok') else 'false'}\n")
    else:
        sys.exit(run(a.staging, a.dry_run, a.skip_refresh, a.budget_min, a.shard, a.queries))


if __name__ == '__main__':
    main()
