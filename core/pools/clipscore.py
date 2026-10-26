#!/usr/bin/env python3
"""zyo-clipscore, the CPU half (2026-10-26): verdict + rank score of the edit-pool candidates from the GPU FEATURES that
scripts/kaggle/clipscore_kernel.py computed on Kaggle (scripts/clipscore-kaggle.py pushes it). No model runs here: a
re-calibration never needs the GPU again.

  calibrate --feats calib-feats.json [--write]   gates at the approved extreme (+ margin) + an AUC-weighted rank score per kind;
                                                  5-fold CV precision / recall of the verdict, next to the CURRENT gates' numbers
                                                  (--baseline JSON {id: true/false} = what the current pipeline decided)
  review --kind slowmo|sound --dir DIR            reorder the by-eye review list by score (hard fails apart); no-op without scores
  score FEATS.json                                print verdicts (debug)

Thresholds: core/pools/clipscore-thresholds.json ({kind: {gates, weights, cut, ...}, 'enabled': bool}). Scores live next to the
staged tars on edit-pools-staging as clipscore-<tar stem>.json. 'enabled' false = scores are computed and shown, nothing reorders.
"""
import argparse
import glob
import json
import math
import os
import random
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
THRESH = os.path.join(HERE, 'clipscore-thresholds.json')
SCORE_PREFIX = 'clipscore-'
NSFW = {'rating_explicit': 0.5, 'nsfw_tag_max': 0.6}      # always blocked (ecchi / questionable stays allowed, owner rule)


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def score_name(tar):
    return SCORE_PREFIX + os.path.basename(tar)[:-4] + '.json' if tar.endswith('.tar') else SCORE_PREFIX + os.path.basename(tar) + '.json'


def load_thresholds():
    try:
        return json.load(open(THRESH, encoding='utf-8'))
    except (OSError, ValueError):
        return {}


# ------------------------------------------------------------------ features -> flat numeric vector
def flat(f):
    """numeric scalars of one clip's feature dict: plain numbers, [mean, max] pairs as _m / _x, sig prompts"""
    out = {}
    for k, v in f.items():
        if k in ('label', 'tar', 'kind', 'sec', 'n', 'cut_frames', 'dissolve_at', 'hit_t', 't_audio', 'w', 'h', 'trace', 'error'):
            continue
        if isinstance(v, bool):
            out[k] = float(v)
        elif isinstance(v, (int, float)) and v is not None and math.isfinite(v):
            out[k] = float(v)
        elif isinstance(v, list) and len(v) == 2 and all(isinstance(x, (int, float)) for x in v):
            out[k + '_m'], out[k + '_x'] = float(v[0]), float(v[1])
        elif k == 'sig' and isinstance(v, dict):
            for p, mv in v.items():
                if isinstance(mv, list) and len(mv) == 2:
                    out['sig_' + p + '_m'], out['sig_' + p + '_x'] = float(mv[0]), float(mv[1])
    if f.get('kind') == 'slowmo':
        out['female'] = 1.0 if f.get('subject') == 'female' else 0.0
    if f.get('cut_before') is None and f.get('kind') == 'sound':
        out['cut_before'] = 1.0                       # no cut: as far as possible
    return out


def nsfw_fail(f):
    fl = flat(f)
    return [f'{k} {fl[k + "_x"] if k + "_x" in fl else fl.get(k)}' for k, t in NSFW.items()
            if (fl.get(k + '_x') if k + '_x' in fl else fl.get(k, 0)) is not None and
            (fl.get(k + '_x') if k + '_x' in fl else fl.get(k, 0)) > t]


def auc(pos, neg):
    if not pos or not neg:
        return 0.5
    s = 0.0
    for p in pos:
        for n in neg:
            s += 1.0 if p > n else 0.5 if p == n else 0.0
    return s / (len(pos) * len(neg))


# ------------------------------------------------------------------ fit / apply
def fit(rows, min_gate_rej=3, margin=0.02, top=12, recall_target=0.9):
    """rows = [(flat feats, ok bool)]. Gates: a feature cut just beyond the most extreme APPROVED value (every approved clip passes)
    that still rejects >= min_gate_rej rejects. Rank score: the top-|AUC-0.5| features, z-scored, signed and AUC-weighted.
    cut = the score keeping recall_target of the approved clips that pass the gates."""
    keys = sorted({k for r, _ in rows for k in r})
    ok = [r for r, y in rows if y]
    rj = [r for r, y in rows if not y]
    gates, stats = [], {}
    for k in keys:
        po = [r[k] for r in ok if k in r]
        ne = [r[k] for r in rj if k in r]
        if len(po) < max(5, 0.7 * len(ok)) or len(ne) < 5:
            continue
        a = auc(po, ne)
        mu = sum(po + ne) / len(po + ne)
        sd = math.sqrt(sum((x - mu) ** 2 for x in po + ne) / len(po + ne)) or 1.0
        stats[k] = {'auc': round(a, 3), 'mu': mu, 'sd': sd}
        span = (max(po + ne) - min(po + ne)) or 1.0
        hi = max(po) + margin * span                  # rejects above every approved value
        lo = min(po) - margin * span
        nh, nl = sum(1 for x in ne if x > hi), sum(1 for x in ne if x < lo)
        if nh >= min_gate_rej:
            gates.append({'f': k, 'op': '<=', 't': round(hi, 4), 'rej': nh})
        if nl >= min_gate_rej:
            gates.append({'f': k, 'op': '>=', 't': round(lo, 4), 'rej': nl})
    gates = sorted(gates, key=lambda g: -g['rej'])[:6]
    feats = sorted(stats, key=lambda k: -abs(stats[k]['auc'] - 0.5))[:top]
    weights = {k: {'w': round(2 * (stats[k]['auc'] - 0.5), 4), 'mu': round(stats[k]['mu'], 5), 'sd': round(stats[k]['sd'], 5)}
               for k in feats if abs(stats[k]['auc'] - 0.5) >= 0.08}
    m = {'gates': gates, 'weights': weights, 'cut': -1e9}
    sc = sorted(rank(r, m) for r in ok if not gate_fail(r, m))
    if sc:
        m['cut'] = round(sc[min(len(sc) - 1, int(math.floor((1 - recall_target) * len(sc))))] - 1e-4, 4)
    return m


def gate_fail(fl, m):
    why = []
    for g in m.get('gates', []):
        v = fl.get(g['f'])
        if v is None:
            continue
        if (g['op'] == '<=' and v > g['t']) or (g['op'] == '>=' and v < g['t']):
            why.append(f"{g['f']} {v:.3g} {'>' if g['op'] == '<=' else '<'} {g['t']:.3g}")
    return why


def rank(fl, m):
    s = 0.0
    for k, w in m.get('weights', {}).items():
        v = fl.get(k)
        if v is None:
            continue
        s += w['w'] * max(-3.0, min(3.0, (v - w['mu']) / (w['sd'] or 1.0)))
    return round(s + m.get('female_bonus', 0.0) * fl.get('female', 0.0), 4)


def verdict(f, m):
    """{'ok', 'score', 'why', 'hard'} for one clip's raw feature dict"""
    if 'error' in f:
        return {'ok': None, 'score': None, 'why': ['features failed: ' + str(f['error'])[:80]], 'hard': False}
    fl = flat(f)
    hard = nsfw_fail(f)
    why = hard + gate_fail(fl, m)
    s = rank(fl, m)
    if not why and s < m.get('cut', -1e9):
        why.append(f'low score {s:.2f} < {m.get("cut"):.2f}')
    return {'ok': not why, 'score': s, 'why': why, 'hard': bool(hard or gate_fail(fl, m))}


# ------------------------------------------------------------------ scores files (driver API)
def write_scores(feats, out, gpu_min=0.0):
    """one clipscore-<tar stem>.json per tar: {'tar', 'gpu_min', 'enabled', 'clips': {id: {verdict..., feats}}}"""
    th = load_thresholds()
    by = {}
    for cid, f in feats.items():
        by.setdefault(f.get('tar', 'unknown.tar'), {})[cid] = f
    files = []
    tot = max(1, len(feats))
    for tar, fs in by.items():
        clips = {}
        for cid, f in fs.items():
            m = th.get(f.get('kind', 'slowmo')) or {}
            clips[cid] = dict(verdict(f, m), kind=f.get('kind'), feats=f)
        p = os.path.join(out, score_name(tar))
        json.dump({'tar': tar, 'gpu_min': round(gpu_min * len(fs) / tot, 2), 'enabled': bool(th.get('enabled')),
                   'version': th.get('version'), 'clips': clips}, open(p, 'w', encoding='utf-8'), indent=0)
        files.append(p)
    return files


def rescore_file(p):
    d = json.load(open(p, encoding='utf-8'))
    th = load_thresholds()
    for cid, c in d.get('clips', {}).items():
        f = c.get('feats') or {}
        c.update(verdict(f, th.get(f.get('kind', c.get('kind', 'slowmo'))) or {}))
    d['enabled'], d['version'] = bool(th.get('enabled')), th.get('version')
    json.dump(d, open(p, 'w', encoding='utf-8'), indent=0)
    return p


def summary(d):
    cl = d.get('clips', {}).values()
    return {'clips': len(cl), 'pass': sum(1 for c in cl if c.get('ok')), 'hard_fail': sum(1 for c in cl if c.get('hard')),
            'error': sum(1 for c in cl if c.get('ok') is None)}


# ------------------------------------------------------------------ calibration
def evaluate(pred, y):
    tp = sum(1 for p, t in zip(pred, y) if p and t)
    fp = sum(1 for p, t in zip(pred, y) if p and not t)
    fn = sum(1 for p, t in zip(pred, y) if not p and t)
    return {'precision': round(tp / max(1, tp + fp), 3), 'recall': round(tp / max(1, tp + fn), 3), 'tp': tp, 'fp': fp, 'fn': fn,
            'n': len(y)}


def calibrate(feats, baseline=None, folds=5, seed=7, recall_target=0.9):
    res = {}
    for kind in ('slowmo', 'sound'):
        items = [(cid, f) for cid, f in feats.items() if f.get('kind') == kind and f.get('label') in ('ok', 'rej') and 'error' not in f]
        if len(items) < 20:
            continue
        rows = [(flat(f), f['label'] == 'ok') for _, f in items]
        ids = [cid for cid, _ in items]
        rnd = random.Random(seed)
        order = list(range(len(rows)))
        rnd.shuffle(order)
        # stratified folds
        pos = [i for i in order if rows[i][1]]
        neg = [i for i in order if not rows[i][1]]
        fold = {}
        for j, i in enumerate(pos):
            fold[i] = j % folds
        for j, i in enumerate(neg):
            fold[i] = j % folds
        pred = [None] * len(rows)
        for k in range(folds):
            m = fit([rows[i] for i in range(len(rows)) if fold[i] != k], recall_target=recall_target)
            for i in range(len(rows)):
                if fold[i] == k:
                    fl = rows[i][0]
                    pred[i] = not gate_fail(fl, m) and rank(fl, m) >= m['cut'] and not nsfw_fail(items[i][1])
        y = [r[1] for r in rows]
        cv = evaluate(pred, y)
        base = {'precision': round(sum(y) / len(y), 3), 'recall': 1.0, 'note': 'all labelled clips were staged = passed the gates'}
        if baseline:
            bp = [bool(baseline.get(cid, True)) for cid in ids]
            base = evaluate(bp, y)
            # current gates + clipscore on top
            res.setdefault(kind, {})['gates_plus_clipscore_cv'] = evaluate([a and b for a, b in zip(bp, pred)], y)
        full = fit(rows, recall_target=recall_target)
        tr = evaluate([not gate_fail(r, full) and rank(r, full) >= full['cut'] for r, _ in rows], y)
        res.setdefault(kind, {}).update({'n_ok': sum(y), 'n_rej': len(y) - sum(y), 'cv': cv, 'current_gates': base, 'train': tr,
                                         'model': full,
                                         'beats_gates': cv['precision'] > base['precision'] + 0.05 and cv['recall'] >= base['recall'] - 0.1})
    return res


# ------------------------------------------------------------------ review ordering (PC session start)
def _scores_for(tar_names, dl_dir):
    """download clipscore-<stem>.json for the given tars (silently nothing when absent)"""
    os.makedirs(dl_dir, exist_ok=True)
    out = {}
    tag = os.environ.get('EDIT_POOLS_STAGING_TAG', 'edit-pools-staging')
    for t in tar_names:
        n = score_name(t)
        p = os.path.join(dl_dir, n)
        if not os.path.isfile(p):
            subprocess.run(['gh', 'release', 'download', tag, '--pattern', n, '--dir', dl_dir, '--clobber'], capture_output=True, timeout=300)
        if os.path.isfile(p):
            try:
                out.update(json.load(open(p, encoding='utf-8')).get('clips', {}))
            except ValueError:
                pass
    return out


def _rid(r):
    return os.path.basename(r['path'])[:-4] if r.get('path') else r.get('id')


def order_rows(rows, scores):
    """good first (by score), unscored middle, hard / soft fails last; 'n' is renumbered so the sheets follow the order"""
    def k(r):
        c = scores.get(_rid(r)) or scores.get(r.get('id'))
        if not c or c.get('score') is None:
            return (1, 0.0)
        return (0 if c.get('ok') else 2 + (1 if c.get('hard') else 0), -c['score'])
    out = sorted(rows, key=k)
    for i, r in enumerate(out):
        c = scores.get(_rid(r)) or scores.get(r.get('id')) or {}
        r['n'] = i
        if c:
            r['clipscore'] = {'ok': c.get('ok'), 'score': c.get('score'), 'why': c.get('why', [])[:3]}
    return out


def review_hook(kind, rows=None, tar_names=None, dl_dir=None, out_txt=None):
    """called once by slowmo_grow.cmd_pull (rows -> reordered rows) and sound_refresh.pull_staged (writes a ranked list).
    No-op (returns rows unchanged) when clip scoring is off or no scores exist; never raises."""
    try:
        th = load_thresholds()
        if not th.get('enabled') and os.environ.get('CLIP_SCORE_REVIEW') != '1':
            return rows
        scores = _scores_for(tar_names or [], dl_dir or os.path.join(ROOT, 'output', 'clipscore', 'review'))
        if not scores:
            return rows
        if rows is not None:
            rows = order_rows(rows, scores)
        if out_txt:
            ranked = sorted(scores.items(), key=lambda kv: (0 if kv[1].get('ok') else 1, -(kv[1].get('score') or -99)))
            with open(out_txt, 'w', encoding='utf-8') as fh:
                fh.write(f'clipscore ({kind}): pass first, by score; FAIL lines are hints, the eye decides\n')
                for cid, c in ranked:
                    fh.write(f"{'PASS' if c.get('ok') else 'FAIL'} {c.get('score')} {cid} {'; '.join(c.get('why', [])[:3])}\n")
        log(f'clipscore: review order from {len(scores)} scores')
        return rows
    except Exception as e:  # noqa: BLE001  (a review aid must never block the import)
        log(f'clipscore review skipped: {e!r}'[:200])
        return rows


# ------------------------------------------------------------------ cli
def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest='cmd', required=True)
    c = sp.add_parser('calibrate')
    c.add_argument('--feats', required=True)
    c.add_argument('--baseline')
    c.add_argument('--write', action='store_true')
    c.add_argument('--enable', action='store_true')
    c.add_argument('--recall', type=float, default=0.9)
    s = sp.add_parser('score')
    s.add_argument('feats')
    a = ap.parse_args()
    if a.cmd == 'calibrate':
        feats = json.load(open(a.feats, encoding='utf-8'))
        base = json.load(open(a.baseline, encoding='utf-8')) if a.baseline else None
        r = calibrate(feats, base, recall_target=a.recall)
        print(json.dumps({k: {kk: vv for kk, vv in v.items() if kk != 'model'} for k, v in r.items()}, indent=1))
        if a.write:
            th = load_thresholds()
            for k, v in r.items():
                th[k] = dict(v['model'], cv=v['cv'], current_gates=v['current_gates'], beats_gates=v['beats_gates'])
            th['enabled'] = bool(a.enable)
            th['version'] = th.get('version', 0) + 1
            json.dump(th, open(THRESH, 'w', encoding='utf-8'), indent=1)
            log('written', THRESH)
    elif a.cmd == 'score':
        th = load_thresholds()
        for cid, f in json.load(open(a.feats, encoding='utf-8')).items():
            print(cid, json.dumps(verdict(f, th.get(f.get('kind', 'slowmo')) or {})))


if __name__ == '__main__':
    main()
