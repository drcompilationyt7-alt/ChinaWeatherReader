#!/usr/bin/env python3
"""
Contextual bandit for the clip pick (MABWiser, free, CPU).

Arms are content categories (kpop, dance, anime, food, ...). Context is the
country region, weekday and hour bucket. Reward is the posted short's
performance percentile (0..1). Every run refits from the full history
(a few hundred rows, instant) and samples an expected reward per candidate
with Linear Thompson Sampling, so exploration is principled: arms with
little data get optimistic, noisy draws; proven arms get confident ones.

stdin JSON:
  {"history":   [{"arm": "kpop", "context": {"country": "Japan", "weekday": 3, "hour": 12}, "reward": 0.7}, ...],
   "candidates":[{"id": "c1", "arm": "dance", "context": {...}}, ...]}
stdout JSON:
  {"available": true, "policy": "LinTS", "n": 120,
   "scores": [{"id": "c1", "arm": "dance", "score": 0.61}], "bestArm": {"c1": "kpop"}}
"""
import json
import sys

REGIONS = {
    'east': ['china', 'japan', 'south korea', 'korea', 'taiwan', 'hong kong', 'macau', 'mongolia'],
    'sea': ['thailand', 'vietnam', 'indonesia', 'philippines', 'malaysia', 'singapore', 'cambodia', 'laos', 'myanmar', 'brunei'],
    'south': ['india', 'pakistan', 'bangladesh', 'sri lanka', 'nepal', 'bhutan', 'maldives'],
    'central': ['kazakhstan', 'uzbekistan', 'kyrgyzstan', 'tajikistan', 'turkmenistan'],
}
HOUR_BUCKETS = [(0, 6), (6, 12), (12, 18), (18, 24)]


def region_of(country):
    c = str(country or '').strip().lower()
    for name, lst in REGIONS.items():
        if any(c == x or x in c for x in lst):
            return name
    if c in ('world', 'asia', 'global', ''):
        return 'asia'
    return 'other'


def vectorize(ctx):
    ctx = ctx or {}
    reg = region_of(ctx.get('country'))
    v = [1.0 if reg == r else 0.0 for r in ('east', 'sea', 'south', 'central', 'asia', 'other')]
    wd = int(ctx.get('weekday', 0) or 0) % 7
    v += [1.0 if i == wd else 0.0 for i in range(7)]
    hr = int(ctx.get('hour', 12) or 12) % 24
    v += [1.0 if lo <= hr < hi else 0.0 for lo, hi in HOUR_BUCKETS]
    v.append(1.0)  # bias
    return v


def main():
    raw = sys.stdin.read()
    data = json.loads(raw) if raw.strip() else {}
    history = [h for h in data.get('history', []) if h.get('arm')]
    candidates = [c for c in data.get('candidates', []) if c.get('arm')]
    try:
        from mabwiser.mab import MAB, LearningPolicy
    except Exception as e:
        print(json.dumps({'available': False, 'reason': f'mabwiser missing: {e.__class__.__name__}', 'scores': []}))
        return
    if not candidates:
        print(json.dumps({'available': True, 'n': len(history), 'scores': []}))
        return

    arms = sorted({h['arm'] for h in history} | {c['arm'] for c in candidates})
    n = len(history)
    if n < 8:
        print(json.dumps({'available': True, 'policy': 'none', 'n': n, 'reason': 'not enough history',
                          'scores': [{'id': c.get('id'), 'arm': c['arm'], 'score': 0.5} for c in candidates], 'bestArm': {}}))
        return

    decisions = [h['arm'] for h in history]
    rewards = [max(0.0, min(1.0, float(h.get('reward', 0.5)))) for h in history]
    contexts = [vectorize(h.get('context')) for h in history]
    cand_ctx = [vectorize(c.get('context')) for c in candidates]

    import random
    seed = random.randrange(1, 2 ** 31)   # fresh posterior sample every run
    policy = 'LinTS'
    try:
        mab = MAB(arms=arms, learning_policy=LearningPolicy.LinTS(alpha=0.5, l2_lambda=1.0), seed=seed)
        mab.fit(decisions=decisions, rewards=rewards, contexts=contexts)
        exp = mab.predict_expectations(cand_ctx)
    except Exception as e:
        # fall back to plain Thompson sampling on binary rewards (above / below median)
        sys.stderr.write(f'LinTS failed ({e}), using ThompsonSampling\n')
        policy = 'ThompsonSampling'
        med = sorted(rewards)[len(rewards) // 2]
        mab = MAB(arms=arms, learning_policy=LearningPolicy.ThompsonSampling(), seed=seed)
        mab.fit(decisions=decisions, rewards=[1 if r >= med else 0 for r in rewards])
        one = mab.predict_expectations()
        exp = [one for _ in candidates]
    if isinstance(exp, dict):
        exp = [exp]

    # arms with little data keep exploring, but their wild prior draws are
    # shrunk towards neutral so one unseen category cannot dominate the pick
    plays = {a: decisions.count(a) for a in arms}
    scores, best = [], {}
    for c, e in zip(candidates, exp):
        s = float(e.get(c['arm'], 0.5))
        s = max(0.0, min(1.0, s))
        k = min(1.0, plays.get(c['arm'], 0) / 5.0)
        s = 0.5 + (s - 0.5) * (0.4 + 0.6 * k)
        scores.append({'id': c.get('id'), 'arm': c['arm'], 'score': round(s, 3), 'plays': plays.get(c['arm'], 0)})
        best[str(c.get('id'))] = max(e.items(), key=lambda kv: kv[1])[0] if e else None
    print(json.dumps({'available': True, 'policy': policy, 'n': n, 'arms': arms, 'scores': scores, 'bestArm': best}))


if __name__ == '__main__':
    main()
