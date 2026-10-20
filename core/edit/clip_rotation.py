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
    # slow-mo: ~18-20 shots (wave cuts), the planner needs >= ~12 clean clips; a few spare for its hard gates
    'slowmo': dict(target=20, want=28, min_need=22, window=5, cap=0.25, per_clip=3, days=30),
    # sound: ~39 pinned hit clips per video, the beat remake needs ~45 pinned candidates (SOUND_RELAX_MIN_PINS)
    'sound': dict(target=39, want=50, min_need=45, window=5, cap=0.25, per_clip=3, days=30),
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


def usage(hist, now=None, days=30, window=5):
    """clip id -> {last (datetime), n_days (videos in the last `days`), recent (in the last `window` videos)}"""
    now = now or _dt.datetime.now(_dt.timezone.utc)
    cut = now - _dt.timedelta(days=days)
    out = {}
    for i, h in enumerate(hist):
        t = _ts(h.get('at'))
        rec = len(hist) - i <= window
        for c in set(h.get('clips') or []):
            u = out.setdefault(c, {'last': None, 'n_days': 0, 'recent': False})
            if t and (u['last'] is None or t > u['last']):
                u['last'] = t
            if t and t >= cut:
                u['n_days'] += 1
            u['recent'] = u['recent'] or rec
    return out


def choose(items, hist, cfg, rng=None, level=0, key=lambda it: it['id'], counts=lambda it: True, now=None):
    rng = rng or random.Random()
    U = usage(hist, now, int(cfg['days']), int(cfg['window']))
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
    cfg = dict(CONFIG['slowmo'])
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
    print('clip_rotation selftest ok')


if __name__ == '__main__':
    selftest()
