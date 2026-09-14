#!/usr/bin/env python3
"""
Online views predictor (River, free, CPU).

An adaptive random forest regressor that learns log(views/day) of our shorts
from country / region / category / source channel / title features / easter
eggs / weekday / hour, one video at a time. After every performance sync the
new scored videos are streamed into the model (`--update`), and at pick and
title time the model predicts for candidates and title options (`--predict`).
The model lives in memory/river-views-model.pkl; if it cannot be loaded (new
river version, corrupt file) it is rebuilt from the full history on the next
update, so nothing ever breaks.

stdin JSON (--update):  {"videos": [{"id": "abc", "features": {...}, "target": 1.23}, ...]}
stdout JSON:            {"available": true, "learned": 5, "seen": 130, "mae": 0.42}
stdin JSON (--predict): {"items": [{"id": "c1", "features": {...}}, ...]}
stdout JSON:            {"available": true, "seen": 130, "predictions": [{"id": "c1", "predicted": 1.1}]}

Feature dict (strings are treated as nominal by the trees, numbers as numeric):
  country, region, category, channel, weekday (0-6), hour (0-23), eggs (0-2),
  duration (s), title_len, title_emoji, title_question, title_number,
  title_caps, title_place
"""
import argparse
import json
import os
import pickle
import sys

MEM = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'memory')
MODEL_PATH = os.path.join(MEM, 'river-views-model.pkl')
SEEN_PATH = os.path.join(MEM, 'river-seen.json')
MIN_SEEN_FOR_PREDICT = 10

NUMERIC = ('weekday', 'hour', 'eggs', 'duration', 'title_len', 'title_emoji', 'title_question', 'title_number', 'title_caps', 'title_place')
NOMINAL = ('country', 'region', 'category', 'channel')


def clean(features):
    """Numeric dict: nominal values become one-hot keys (country=japan: 1.0)."""
    f = {}
    for k in NOMINAL:
        v = features.get(k)
        v = str(v).strip().lower() if v not in (None, '') else 'unknown'
        f[f'{k}={v}'] = 1.0
    for k in NUMERIC:
        try:
            f[k] = float(features.get(k) if features.get(k) is not None else 0)
        except Exception:
            f[k] = 0.0
    return f


def new_model():
    from river import forest, metrics
    # small data: let leaves split early (grace_period) and keep the split test lenient (delta)
    model = forest.ARFRegressor(n_models=10, seed=42, grace_period=20, delta=0.05, leaf_prediction='adaptive')
    return {'model': model, 'metric': metrics.MAE(), 'version': 2}


def load_model():
    try:
        with open(MODEL_PATH, 'rb') as fh:
            state = pickle.load(fh)
        if isinstance(state, dict) and 'model' in state:
            return state, True
    except Exception:
        pass
    return new_model(), False


def load_seen():
    try:
        with open(SEEN_PATH, encoding='utf-8') as fh:
            return set(json.load(fh).get('ids', []))
    except Exception:
        return set()


def save(state, seen):
    os.makedirs(MEM, exist_ok=True)
    with open(MODEL_PATH, 'wb') as fh:
        pickle.dump(state, fh)
    with open(SEEN_PATH, 'w', encoding='utf-8') as fh:
        json.dump({'ids': sorted(seen), 'count': len(seen)}, fh)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--update', action='store_true')
    ap.add_argument('--predict', action='store_true')
    args = ap.parse_args()
    try:
        import river  # noqa: F401
    except Exception as e:
        print(json.dumps({'available': False, 'reason': f'river missing: {e.__class__.__name__}', 'predictions': []}))
        return
    raw = sys.stdin.read()
    data = json.loads(raw) if raw.strip() else {}

    state, loaded = load_model()
    seen = load_seen() if loaded else set()

    if args.update:
        videos = [v for v in data.get('videos', []) if v.get('id') and isinstance(v.get('target'), (int, float))]
        # oldest first so the stream is chronological
        videos.sort(key=lambda v: str(v.get('publishedAt') or ''))
        learned = 0
        for v in videos:
            if v['id'] in seen:
                continue
            x, y = clean(v.get('features') or {}), float(v['target'])
            try:
                pred = state['model'].predict_one(x)
                state['metric'].update(y, pred)
                state['model'].learn_one(x, y)
                seen.add(v['id'])
                learned += 1
            except Exception as e:
                sys.stderr.write(f'learn failed for {v["id"]}: {e}\n')
        save(state, seen)
        mae = state['metric'].get() if len(seen) else None
        print(json.dumps({'available': True, 'learned': learned, 'seen': len(seen), 'rebuilt': not loaded,
                          'mae': round(mae, 3) if mae is not None else None}))
        return

    items = data.get('items', [])
    if len(seen) < MIN_SEEN_FOR_PREDICT:
        print(json.dumps({'available': True, 'seen': len(seen), 'reason': 'not enough training data',
                          'predictions': [{'id': it.get('id'), 'predicted': None} for it in items]}))
        return
    preds = []
    for it in items:
        try:
            p = float(state['model'].predict_one(clean(it.get('features') or {})))
        except Exception:
            p = None
        preds.append({'id': it.get('id'), 'predicted': round(p, 3) if p is not None else None})
    print(json.dumps({'available': True, 'seen': len(seen), 'predictions': preds}))


if __name__ == '__main__':
    main()
