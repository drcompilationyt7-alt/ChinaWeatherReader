#!/usr/bin/env python3
"""
Taste for the clip finder (core/edit/library.py): is a candidate shot the KIND of shot the owner's
reference edits are cut from, and is it attractive? The owner: "our clip selection is lacking too,
I need good clips, attractive clips".

Everything here works on the SigLIP (google/siglip-base-patch16-224) frame embeddings the library
already computes for every candidate, so it costs a few matrix products per shot and no new model.

1. Exemplars (refs/exemplars.npz + .json, built by refs/ref_shots.py from every shot of the 37
   reference edits, the edits' own typography inpainted away). A candidate is compared with the shots
   of the preset being built (presets-catalog.json refs; the whole reference group of the mode when
   no preset is named or the preset has fewer than 25 shots), same kind only (anime / live action):
     ex        mean over the candidate's key frames of the mean cosine to its 8 nearest exemplars
     ex_role   the exemplar role the candidate resembles most: hero (a held, sharp, vivid shot, or the
               first shot after the drop), beat (<= 0.6 s), slowmo (held, smooth), dance, closeup
2. Attractiveness head (refs/attract_head.npz): a logistic head on the mean key-frame embedding,
   one per kind. 2026-09-27: the first head (reference shots vs random source windows, 5-fold AUC
   .985) learned the references' grade and effects look, not the shot: on editor-labelled candidate
   pools it scored AUC .45. The saved head is now trained on those labelled pools only (256 anime
   candidates from the JJK / Gojo / dance runs, 72 live from LE SSERAFIM; "usable" vs not).
   Leave-one-query-out it reaches AUC .92 (JJK) / .76 (Gojo) / .72 (LE SSERAFIM) / .52 (dance, 26
   labels), but adding it to the taste formula did not change which shots get picked, so
   library.py gives it weight 0 and only records `attract` in the manifest. `--train` (reference
   vs background) is kept for experiments; the labelled-pool trainer lives in the study scratch
   (head_loqo.py) and its labels next to the pools.
3. Plain image measures on the candidate: composition (face size and position on the centre / the
   thirds, faces cut by the frame edge), vividness (colourfulness, contrast), crispness at 640 px
   (soft upscales), all 0-1.

    python core/edit/clip_taste.py --train --bg bg.npy --bg-meta bg.json [--labels labels.json ...]
    python core/edit/clip_taste.py --selftest
"""
import argparse
import json
import math
import os

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
BANK = os.path.join(HERE, 'refs', 'exemplars')
HEAD = os.path.join(HERE, 'refs', 'attract_head.npz')

# reference groups that stand in for a mode when no preset is named
MODE_GROUPS = {
    ('anime', 'source'): ('general',),
    ('anime', 'mood'): ('general', 'character'),
    ('anime', 'character'): ('character',),
    ('anime', 'dance'): ('dance',),
    ('anime', 'sound'): ('sound',),
    ('group', 'source'): ('kpop',),
    ('group', 'mood'): ('kpop',),
    ('group', 'character'): ('kpop',),
    ('group', 'dance'): ('kpop', 'dance'),
    ('group', 'sound'): ('kpop',),
}
# seconds of source footage one shot of the preset needs (its cut length x its slow motion, plus room
# for the planner to slide the in-point onto a hit); None = never trim (long takes / single clips)
PRESET_TAKE = {
    'blur_flow': 2.6, 'hype_typography': 2.4, 'cinematic_slowmo': 4.0, 'band_parade': 2.2, 'cinematic_long': 5.0,
    'char_quick_velocity': 2.6, 'char_soft_romance': 4.0, 'char_raw_crisp': 2.4,
    'dance_burst_parade': None, 'dance_smooth_velocity': None, 'dance_raw': None, 'dance_meme': None,
    'dance_long_amv': None,
    'kpop_postcard_drop': 3.0, 'kpop_stage_push': 3.0, 'kpop_meme_reveal': 3.0, 'kpop_single_take_smear': None,
    'kpop_text_strobe': None, 'sound_hit_sync': 2.2, 'fx_typography': 3.0,
}
# presets whose references hold near-still shots (a still hero hold is wanted, not "dull")
STILL_OK = {'cinematic_slowmo', 'kpop_meme_reveal', 'char_soft_romance', 'cinematic_long', 'kpop_postcard_drop'}
ROLES = ('hero', 'beat', 'slowmo', 'dance', 'closeup')
# ex_raw (mean cosine to the 8 nearest exemplars) -> 0-1: the typical ordinary source window sits at
# the low end, a shot like the references' own at the high end (calibrated on the background windows
# and the labelled pools, see the report in the task notes)
EX_SCALE = {'anime': (0.60, 0.80), 'live': (0.55, 0.78)}
K_NN = 8


def _kind(kind):
    return 'live' if kind == 'group' else 'anime'


class Taste:
    """Exemplar similarity + attractiveness for one run (one kind, one mode / preset)."""

    def __init__(self, kind, mode, preset='', need_sfx=False, bank=BANK, head=HEAD):
        with open(bank + '.json', encoding='utf-8') as f:
            meta = json.load(f)
        E = np.load(bank + '.npz')['emb'].astype(np.float32)
        E /= np.linalg.norm(E, axis=1, keepdims=True) + 1e-9
        rows = meta['shots']
        k = _kind(kind)
        m = 'sound' if need_sfx and mode != 'dance' else mode
        groups = MODE_GROUPS.get((kind, m)) or MODE_GROUPS.get((kind, 'source'))
        # usable exemplars only: not a black / white / graphics frame from a transition or a card
        same = [i for i, r in enumerate(rows) if r['kind'] == k and 25 <= r['luma'] <= 235
                and r['sharp'] >= 60 and r['text_frac'] < 0.08]
        self.preset = preset if preset in meta.get('presets', {}) else ''
        pre = [i for i in same if self.preset and self.preset in rows[i]['presets']]
        grp = [i for i in same if rows[i]['group'] in groups]
        idx = pre if len(pre) >= 25 else sorted(set(pre) | set(grp))
        # live action has only ~64 usable reference shots in all (the K-pop references are mostly
        # graded, anime-voted frames), so a live run always compares with every live shot
        if len(idx) < 12 or (k == 'live' and len(idx) < 40):
            idx = same
        self.idx = idx
        self.X = E[idx]
        self.all = E[same]
        self.rows = [rows[i] for i in idx]
        self.role_idx = {r: [j for j, row in enumerate(self.rows) if r in row['roles']] for r in ROLES}
        self.kind = k
        self.why = (f'{len(idx)} {k} reference shots ('
                    + (f'preset {self.preset}' + (' + ' + '/'.join(groups) if len(pre) < 25 else '')
                       if self.preset else '/'.join(groups)) + ')')
        self.head = None
        if head and os.path.exists(head):
            h = np.load(head)
            if f'w_{k}' in h.files:
                self.head = (h[f'w_{k}'].astype(np.float32), float(h[f'b_{k}']),
                             float(h[f'lo_{k}']), float(h[f'hi_{k}']))
        self.take = PRESET_TAKE.get(self.preset)
        self.still_ok = self.preset in STILL_OK

    def features(self, F):
        """F: (frames, D) unit SigLIP embeddings of one candidate -> dict of 0-1 scores."""
        F = np.asarray(F, np.float32)
        S = F @ self.X.T
        kk = min(K_NN, S.shape[1])
        top = -np.sort(-S, axis=1)[:, :kk]
        ex = float(top.mean())
        lo, hi = EX_SCALE[self.kind]
        out = {'ex_raw': round(ex, 4), 'ex': round(float(np.clip((ex - lo) / (hi - lo), 0, 1)), 3)}
        best, bv = None, -1.0
        for r, ix in self.role_idx.items():
            if len(ix) < 3:
                continue
            v = float((-np.sort(-S[:, ix], axis=1)[:, :3]).mean())
            out[f'ex_{r}'] = round(v, 4)
            if v > bv:
                best, bv = r, v
        out['ex_role'] = best
        m = F.mean(0)
        m = m / (np.linalg.norm(m) + 1e-9)
        if self.head is not None:
            w, b, lo, hi = self.head
            z = float(m @ w + b)
            out['attract_raw'] = round(z, 3)
            out['attract'] = round(float(np.clip((z - lo) / max(1e-6, hi - lo), 0, 1)), 3)
        return out


# ---------------------------------------------------------------- plain image measures

def composition(face_boxes, n_frames):
    """0-1 from anime / real face boxes per key frame ({frame: [(x, y, w, h, conf)]}): a face that is
    big enough to read and sits on the centre or a third line scores high; a face cut by the frame
    edge, or a crowd of small faces, scores low. No face: 0.45 (scenery, effects, hands)."""
    vals = []
    for bs in (face_boxes or {}).values():
        if not bs:
            continue
        x, y, w, h = [float(v) for v in bs[0][:4]]
        a = w * h
        size = float(np.clip((a - 0.004) / 0.05, 0, 1))
        cx, cy = x + w / 2, y + h / 2
        d = min(abs(cx - t) for t in (1 / 3, 0.5, 2 / 3))
        pos = float(np.clip(1 - d / 0.2, 0, 1)) * float(np.clip(1 - max(0.0, cy - 0.62) / 0.25, 0, 1))
        cut = (x < 0.01 or x + w > 0.99) and a < 0.1
        crowd = len(bs) >= 4 and a < 0.02
        v = 0.35 + 0.65 * (0.6 * size + 0.4 * pos)
        if cut:
            v *= 0.75
        if crowd:
            v *= 0.8
        vals.append(v)
    if not vals:
        return 0.45
    return float(np.clip(np.mean(vals) * (0.7 + 0.3 * len(vals) / max(1, n_frames)), 0, 1))


def vividness(colorful, contrast):
    return float(0.6 * np.clip(colorful / 70, 0, 1) + 0.4 * np.clip(contrast / 60, 0, 1))


def crispness(sharp_loc, sharp640):
    """0-1: line work crisp at 320 px (sharp_loc, block p85 Laplacian variance) and still detailed
    at 640 px (sharp640; a soft 720p upscale drops here first)."""
    a = np.clip(math.log(max(sharp_loc, 1) / 150) / math.log(20), 0, 1)
    b = np.clip(math.log(max(sharp640, 1) / 60) / math.log(20), 0, 1)
    return float(0.5 * a + 0.5 * b)


def best_window(ev, length_frames):
    """(offset, length) of the best sub-window of `ev` (per analysis frame interest: motion + hits)
    of the given length: the most action, lightly pulled toward the middle so an action is not cut
    at its edge."""
    ev = np.asarray(ev, np.float32)
    n = len(ev)
    L = int(length_frames)
    if L >= n or n == 0:
        return 0, n
    cs = np.concatenate([[0.0], np.cumsum(ev)])
    sums = cs[L:] - cs[:-L]
    centre = np.arange(len(sums)) + L / 2
    w = 1 - 0.15 * np.abs(centre - n / 2) / max(1.0, n / 2)
    return int(np.argmax(sums * w)), L


# ---------------------------------------------------------------- training

def train(bg, bg_meta, labels=(), out=HEAD, bank=BANK, C=0.5):
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import cross_val_score
    with open(bank + '.json', encoding='utf-8') as f:
        rows = json.load(f)['shots']
    E = np.load(bank + '.npz')['emb'].astype(np.float32)
    B = np.load(bg).astype(np.float32)
    with open(bg_meta, encoding='utf-8') as f:
        bm = json.load(f)
    lab = []
    for p in labels:
        with open(p, encoding='utf-8') as f:
            lab += json.load(f)
    res = {}
    for k in ('anime', 'live'):
        pos = [E[i] for i, r in enumerate(rows) if r['kind'] == k]
        neg = [B[i] for i, r in enumerate(bm) if r['kind'] == k]
        pos += [np.asarray(x['emb'], np.float32) for x in lab if x['kind'] == k and x['y'] == 1]
        neg += [np.asarray(x['emb'], np.float32) for x in lab if x['kind'] == k and x['y'] == 0]
        if len(pos) < 20 or len(neg) < 20:
            continue
        X = np.stack(pos + neg)
        X /= np.linalg.norm(X, axis=1, keepdims=True) + 1e-9
        y = np.array([1] * len(pos) + [0] * len(neg))
        clf = LogisticRegression(C=C, max_iter=2000, class_weight='balanced')
        auc = cross_val_score(clf, X, y, cv=5, scoring='roc_auc').mean()
        clf.fit(X, y)
        z = X @ clf.coef_[0] + clf.intercept_[0]
        lo, hi = float(np.percentile(z[y == 0], 50)), float(np.percentile(z[y == 1], 75))
        res[f'w_{k}'], res[f'b_{k}'] = clf.coef_[0].astype(np.float32), np.float32(clf.intercept_[0])
        res[f'lo_{k}'], res[f'hi_{k}'] = np.float32(lo), np.float32(hi)
        print(f'{k}: {len(pos)} positives, {len(neg)} negatives, 5-fold AUC {auc:.3f}, scale {lo:.2f}..{hi:.2f}')
    np.savez(out, **res)
    return res


def selftest():
    rng = np.random.default_rng(1)
    t = Taste('anime', 'source')
    F = rng.normal(size=(3, t.X.shape[1])).astype(np.float32)
    F /= np.linalg.norm(F, axis=1, keepdims=True)
    f_rand = t.features(F)
    f_ref = t.features(t.X[:3])
    assert f_ref['ex_raw'] > f_rand['ex_raw'] + 0.3, (f_ref, f_rand)
    assert composition({}, 3) == 0.45
    assert composition({'1': [(0.4, 0.2, 0.2, 0.3, 0.9)]}, 1) > 0.8
    assert best_window([0, 0, 0, 1, 1, 1, 0, 0], 3) == (3, 3)
    for kind, mode in (('group', 'source'), ('anime', 'dance'), ('anime', 'character')):
        tt = Taste(kind, mode)
        assert len(tt.idx) >= 12, (kind, mode)
    tp = Taste('anime', 'character', preset='char_quick_velocity')
    print('selftest ok:', t.why, '|', tp.why, '| head', t.head is not None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--train', action='store_true')
    ap.add_argument('--bg')
    ap.add_argument('--bg-meta')
    ap.add_argument('--labels', nargs='*', default=[])
    ap.add_argument('--C', type=float, default=0.5)
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.train:
        train(a.bg, a.bg_meta, a.labels, C=a.C)
    if a.selftest:
        selftest()


if __name__ == '__main__':
    main()
