"""Clip rotation for the pool-based edit types (sound, slow-mo): variety with rare reuse.

Owner 2026-10-20 (all edit types): "growing pool, we can reuse the clips but just variety so reuse is not every time, only here
and there". The pools only grow; a clip may come back, but most shots of a video are fresh or long unused.

  choose(items, hist, cfg, rng, level)  -> (candidates for the planner, info)
      1. per-clip cap: a clip shown in >= PER_CLIP videos in the last DAYS days is skipped (the cap rises by one, step by step,
         only while fewer than MIN_NEED clips would be left: a small pool still renders, and info says so)
      2. LEAST-RECENTLY-USED order: never used first (random among them), then the longest unused
      3. per-video reuse cap: clips shown in the last WINDOW videos ("recent") get ~CAP of the candidate slots; the share rises
         automatically only when the pool cannot sustain it (fresh per video <= pool / (WINDOW + 1)) or MIN_NEED needs more
      4. the planner gets at most WANT candidates (so it cannot keep choosing the same favourites from a big pool)
      level (pool_low retries): WANT x (1 + 0.5 level), the reuse slots grow the same way; level >= 2 = every allowed clip
  report(used, hist, window)            -> {shots, reused, reuse_share, from_last_video, ...} (logged per video)
  history(ledger, preset)               -> the preset's videos (all vibes, oldest first): [{at, clips}]

The planners keep their own rules on top (sound: no clip twice in one video; slow-mo: max 2 per source, no clip twice).
CLIP_ROTATION=0 turns this off (the old last-N-videos cooldown, core/edit/vibe_pool.py).
"""
import datetime as _dt
import math
import os
import random

# per preset: TARGET shots per video, WANT candidates handed to the planner, MIN_NEED the planner's floor,
# WINDOW recent videos, CAP reuse share, PER_CLIP uses per DAYS
CONFIG = {
    # slow-mo: 18-27 shots (the wave cuts follow the song), each clip once (the wave DP caps the shots at the clean clips, 2026-10-21)
    # 2026-10-22 owner (blue dry run: "the clips are the exact same"): slow-mo shows NO clip of the last 3 slow-mo videos, the
    # queued prebuilt ones (3j / 3g) included (cap 0 = hard exclusion); fewer than min_fresh clips left -> no slow-mo that day
    'slowmo': dict(target=22, want=30, min_need=24, window=3, cap=0.0, per_clip=3, days=30, hard=1, min_fresh=22,
                   recent_days=0),
    # sound: ~39 pinned hit clips per video, the beat remake needs ~45 pinned candidates (SOUND_RELAX_MIN_PINS)
    # 2026-10-22 owner "no variety and good match need both": HARD reuse cap. The planner gets every allowed clip, each stamped
    # rot_recent / rot_age (choose(..., hard=True)); core/edit/sound_edit.py keeps the recent clips <= CAP of the shots and picks the
    # best matches inside that cap; a pool that cannot give both fails as 'pool_too_small' (-> the daily run posts stem).
    # min_fresh: usable fresh clips a passing short needs at least (~37 usable hits x 0.7); below it the render is not even tried.
    # recent_days: a video older than this no longer counts as recent (a stem-only fortnight would otherwise freeze the window
    # on the same 5 videos until the pool grows); 0 = no age limit
    'sound': dict(target=39, want=50, min_need=45, window=5, cap=0.30, per_clip=3, days=30, hard=1, min_fresh=20,
                  recent_days=14),
}


def enabled():
    return os.environ.get('CLIP_ROTATION', '1') != '0'


def config(preset):
    c = dict(CONFIG.get(preset) or CONFIG['slowmo'])
    for k in c:
        v = os.environ.get(f'CLIP_ROT_{preset.upper()}_{k.upper()}')
        if v not in (None, ''):
            c[k] = type(c[k])(float(v)) if isinstance(c[k], int) else float(v)
    return c


def _ts(s):
    try:
        return _dt.datetime.fromisoformat(str(s).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None


def history(ledger, preset):
    """every video of the preset (all vibes: a sound short also shows its extra vibe's clips), oldest first"""
    rows = []
    for k, v in ((ledger or {}).get('recent') or {}).items():
        if k.split('/')[0] == preset:
            rows += [h for h in v or [] if h.get('clips')]
    return sorted(rows, key=lambda h: str(h.get('at') or ''))


def usage(hist, now=None, days=30, window=5, recent_days=0):
    """clip id -> {last (datetime), n_days (videos in the last `days`), recent (in the last `window` videos, and posted within
    `recent_days` when > 0; a queued video dated in the future is recent)}"""
    now = now or _dt.datetime.now(_dt.timezone.utc)
    cut = now - _dt.timedelta(days=days)
    rcut = now - _dt.timedelta(days=recent_days) if recent_days else None
    out = {}
    for i, h in enumerate(hist):
        t = _ts(h.get('at'))
        rec = len(hist) - i <= window and (rcut is None or t is None or t >= rcut)
        for c in set(h.get('clips') or []):
            u = out.setdefault(c, {'last': None, 'n_days': 0, 'recent': False})
            if t and (u['last'] is None or t > u['last']):
                u['last'] = t
            if t and t >= cut:
                u['n_days'] += 1
            u['recent'] = u['recent'] or rec
    return out


def videos_since(hist):
    """clip id -> number of the preset's videos since its last use (0 = shown in the latest video)"""
    out = {}
    for i, h in enumerate(hist):
        for c in set(h.get('clips') or []):
            out[c] = len(hist) - 1 - i
    return out


def choose(items, hist, cfg, rng=None, level=0, key=lambda it: it['id'], counts=lambda it: True, now=None):
    rng = rng or random.Random()
    U = usage(hist, now, int(cfg['days']), int(cfg['window']), int(cfg.get('recent_days') or 0))
    if cfg.get('hard'):
        return choose_hard(items, hist, cfg, U, rng, key, counts)
    pas = [it for it in items if not counts(it)]          # not rotated (e.g. sound clips without a pinned hit: never shown)
    pool = [it for it in items if counts(it)]
    info = {'pool': len(pool), 'level': level}
    if not pool:
        return pas, dict(info, given=0)
    min_need = min(int(cfg['min_need']), len(pool))
    # 1. per-clip cap (relaxed one step at a time only while too few clips are left)
    cap_n = int(cfg['per_clip'])
    while True:
        allowed = [it for it in pool if U.get(key(it), {}).get('n_days', 0) < cap_n]
        if len(allowed) >= min_need or cap_n > 99:
            break
        cap_n += 1
    info['per_clip_cap'] = cap_n
    info['per_clip_relaxed'] = cap_n > int(cfg['per_clip'])
    # 2. LRU order (never used first, random among equals; then the oldest last use)
    epoch = _dt.datetime(1970, 1, 1, tzinfo=_dt.timezone.utc)
    tie = {id(it): rng.random() for it in allowed}
    lru = sorted(allowed, key=lambda it: ((U.get(key(it)) or {}).get('last') or epoch, tie[id(it)]))
    fresh = [it for it in lru if not (U.get(key(it)) or {}).get('recent')]
    recent = [it for it in lru if (U.get(key(it)) or {}).get('recent')]
    # 3. + 4. how many candidates, how many of them recent. Fresh clips first (LRU order); recent clips (shown in the last
    # WINDOW videos, oldest first) only fill what the fresh ones cannot, at most ceil(CAP x TARGET) of them. When the pool is
    # too small for that, the reuse share rises (logged) and the planner gets only TARGET + 2 candidates, so it cannot keep
    # choosing the same favourites (simulated: 'reserving' recent slots or a plain LRU list both repeat more; see
    # new-types-progress.md "clip rotation").
    grow = 1.0 + 0.5 * level
    S = int(cfg['target'])
    want = len(lru) if level >= 2 else max(min_need, int(math.ceil(int(cfg['want']) * grow)))
    k = min(len(recent), int(math.ceil(float(cfg['cap']) * S * grow)), max(0, want - len(fresh)))
    take_f = fresh[:want]
    tight = int(math.ceil(max(min_need, S + 2) * grow))
    if level >= 2:
        k = len(recent)                               # a second pool_low retry: every allowed clip
    elif len(take_f) + k < tight:
        k = min(len(recent), max(0, tight - len(take_f)))
        info['reuse_raised'] = True
    take_r_share = k
    out = take_f + recent[:k]
    info.update(fresh=len(fresh), recent=len(recent), recent_given=take_r_share, fresh_given=len(take_f), given=len(out), want=want,
                expected_reuse_share=round(min(1.0, len(take_f) and take_r_share / max(1, len(out)) or 1.0), 2))
    return out + pas, info


def choose_hard(items, hist, cfg, U, rng, key, counts):
    """HARD reuse cap (sound, owner 2026-10-22): every allowed clip goes to the planner, LRU order (never used first), each stamped
    'rot_recent' (shown in the last WINDOW videos) and 'rot_age' (videos since its last use, None = never). The planner enforces
    recent <= CAP of the shots and chooses the best matches inside it; info['pool_too_small'] when the fresh clips cannot carry a
    short at all (fresh < min_fresh)."""
    pas = [it for it in items if not counts(it)]
    pool = [it for it in items if counts(it)]
    cap_n = int(cfg['per_clip'])
    allowed = [it for it in pool if U.get(key(it), {}).get('n_days', 0) < cap_n]
    since = videos_since(hist)
    epoch = _dt.datetime(1970, 1, 1, tzinfo=_dt.timezone.utc)
    tie = {id(it): rng.random() for it in allowed}
    lru = sorted(allowed, key=lambda it: ((U.get(key(it)) or {}).get('last') or epoch, tie[id(it)]))
    for it in lru:
        it['rot_recent'] = bool((U.get(key(it)) or {}).get('recent'))
        it['rot_age'] = since.get(key(it))
    for it in pas:
        it['rot_recent'], it['rot_age'] = False, None      # never shown (sound: no pinned hit): not part of the reuse share
    fresh = sum(1 for it in lru if not it['rot_recent'])
    excluded = 0
    override = float(cfg['cap']) <= 0 and os.environ.get('CLIP_ROTATION_FRESH_OVERRIDE') == '1'
    if float(cfg['cap']) <= 0 and not override:            # slow-mo: the recent videos' clips are not offered at all
        excluded = len(lru) - fresh
        lru = [it for it in lru if not it['rot_recent']]
    elif override:
        # TIMING TEST ONLY (vibe_edit --fresh-override): the recent clips come back after the fresh ones; the result is
        # labelled fresh_override and the pre-upload guard never lets it post
        lru = [it for it in lru if not it['rot_recent']] + [it for it in lru if it['rot_recent']]
    info = {'pool': len(pool), 'mode': 'hard_cap', 'cap': float(cfg['cap']), 'per_clip_cap': cap_n, 'fresh': fresh,
            'recent': len(lru) - fresh + excluded, 'excluded_recent': excluded, 'given': len(lru),
            'skipped_per_clip': len(pool) - len(allowed)}
    if override:
        info['fresh_override'] = True
    elif fresh < int(cfg.get('min_fresh') or 0):
        info['pool_too_small'] = f'{fresh} fresh clips (< {int(cfg["min_fresh"])}) for <= {float(cfg["cap"]):.0%} reuse'
    return lru + pas, info


def report(used, hist, window=5):
    """the reuse share of one rendered video vs the preset's last `window` videos (and vs the previous one)"""
    used = list(dict.fromkeys(used or []))
    last = [set(h.get('clips') or []) for h in hist[-window:]]
    prev = last[-1] if last else set()
    rec = set().union(*last) if last else set()
    r = sum(1 for c in used if c in rec)
    return {'shots': len(used), 'reused': r, 'reuse_share': round(r / max(1, len(used)), 3),
            'from_last_video': round(sum(1 for c in used if c in prev) / max(1, len(used)), 3), 'window': window}


def selftest():
    rng = random.Random(1)
    items = [{'id': f'c{i}'} for i in range(120)]     # 25 % reuse over 5 videos x 20 shots needs >= ~100 clips
    hist = []
    now = _dt.datetime(2026, 10, 20, tzinfo=_dt.timezone.utc)
    cfg = dict(target=20, want=28, min_need=22, window=5, cap=0.25, per_clip=3, days=30)   # fixed: independent of CONFIG
    for v in range(10):
        t = now + _dt.timedelta(days=3 * v)
        c, info = choose(items, hist, cfg, rng, now=t)
        used = [x['id'] for x in rng.sample(c, 20)]   # the planner picks by its own quality score: random here
        rep = report(used, hist)
        assert rep['reuse_share'] <= 0.26, (v, rep, info)
        hist.append({'at': t.isoformat(), 'clips': used})
    U = usage(hist, now + _dt.timedelta(days=30), 30, 5)
    assert max(u['n_days'] for u in U.values()) <= 4
    small = items[:20]
    c, info = choose(small, hist, cfg, rng, now=now + _dt.timedelta(days=30))
    assert len(c) >= 20 and info.get('reuse_raised'), info
    # hard mode: everything allowed is handed over, stamped; too few fresh -> pool_too_small
    hc = dict(cfg, hard=1, cap=0.3, min_fresh=15)
    items2 = [{'id': f'c{i}'} for i in range(40)]
    h2 = [{'at': now.isoformat(), 'clips': [f'c{i}' for i in range(30)]}]
    c, info = choose(items2, h2, hc, rng, now=now)
    assert len(c) == 40 and info['fresh'] == 10 and info.get('pool_too_small'), info
    assert c[0]['rot_recent'] is False and c[0]['rot_age'] is None and c[-1]['rot_recent'] and c[-1]['rot_age'] == 0, c[:2]
    # slow-mo: cap 0 = the recent clips are not offered; a 20-day-old video is not recent with recent_days 14
    c, info = choose(items2, h2, dict(hc, cap=0.0, min_fresh=5), rng, now=now)
    assert len(c) == 10 and info['excluded_recent'] == 30 and not info.get('pool_too_small'), info
    c, info = choose(items2, h2, dict(hc, recent_days=14), rng, now=now + _dt.timedelta(days=20))
    assert info['fresh'] == 40, info
    print('clip_rotation selftest ok')


if __name__ == '__main__':
    selftest()
