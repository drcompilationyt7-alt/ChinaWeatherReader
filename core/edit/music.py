#!/usr/bin/env python3
"""
Music analysis for the edit planners (core/edit/planners.py).

    python core/edit/music.py --audio song.mp3 [--heatmap heatmap.json] --out music.json
        [--tracker auto|beat_this|librosa] [--window 15 --kind song|dance|character|kpop|sound|long]

Analyses the WHOLE song once; the planners then pick their own window from it.

    beats / downbeats   Beat This (final0, CPU) when it is installed, else librosa; every beat is
                        snapped to its percussive attack (+-30 ms, 3 ms resolution) and the grid is
                        straightened (pop is recorded on a click), so a cut placed "2 frames before
                        the beat" really is 2 frames before the hit
    energy              0..1 per beat: mix loudness + percussive (HPSS) loudness + sub-bass, scaled
                        on the song's own 5th..95th percentile (a chorus is ~0.8-1, a verse ~0.4-0.6)
    sections            bar-level segmentation (agglomerative on chroma / MFCC / loudness), snapped
                        to 4-bar phrases and labelled intro / verse / build / chorus / drop / bridge / outro
    drops               bar starts where the mix or the drums jump up (+3 dB or more), scored
    heat                the yt-dlp "most replayed" heatmap per second (0..1), when given
    onsets              strong percussive onsets (for single-clip accents and sound edits)
    lufs / true peak    of the whole song (the engine masters to -14 LUFS, < -1 dBTP)

Helpers the planners import:
    analyze(path, heatmap=None)                -> dict (the JSON above)
    choose_window(A, length, ...)             -> {'start','end','drop','intro','score'}
    choose_long_window(A, min_len, max_len)   -> {'start','end'}
    window_view(A, start, end, lo, hi)        -> output-time beats / downbeats / energy / sections

The last stdout line is JSON: {"ok": true, "out": ..., "bpm": ..., "duration": ..., ...}.
"""
import argparse
import json
import math
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'meme'))
import acapella as AC  # noqa: E402  (audio io, BS.1770 loudness, song features, heatmap loader)

SR = AC.SR          # 44100
ASR = AC.ASR        # 22050 analysis rate
HOP = 512


def log(*a):
    print(*a, file=sys.stderr, flush=True)


# ---------------------------------------------------------------- beats

def _beat_this(mono):
    """Beat This (final0) beats + downbeats, or None when the package / checkpoint is missing."""
    try:
        import torch
        from beat_this.inference import Audio2Beats
        torch.set_num_threads(max(1, min(4, os.cpu_count() or 2)))
        a2b = Audio2Beats(checkpoint_path='final0', device='cpu', dbn=False)
        b, d = a2b(mono.astype(np.float32), SR)
        b, d = np.asarray(b, float), np.asarray(d, float)
        if len(b) < 16:
            return None
        return b, d
    except Exception as e:  # not installed / no checkpoint download: librosa is the fallback
        log(f'beat_this unavailable ({str(e)[:80]}), using librosa')
        return None


def _librosa_beats(y22):
    import librosa
    oenv = librosa.onset.onset_strength(y=y22, sr=ASR, hop_length=HOP)
    _, bf = librosa.beat.beat_track(onset_envelope=oenv, sr=ASR, hop_length=HOP, trim=False)
    return librosa.frames_to_time(bf, sr=ASR, hop_length=HOP)


def _attack_env(y22, hop=64):
    """Onset envelope with a short window: its peaks sit within a few ms of the real attacks."""
    import librosa
    env = librosa.onset.onset_strength(y=y22, sr=ASR, hop_length=hop, n_fft=512, n_mels=40)
    env = env / (np.percentile(env, 99.5) + 1e-9)
    return env, np.arange(len(env)) * hop / ASR


def refine_beats(beats, env, et, win=0.03):
    """
    Snap every beat to the strongest attack within +-win (when there is a clear one), then
    straighten: a constant grid when the song sits on a click (inlier residual < 15 ms), else a
    local +-8-beat linear smoothing that keeps real tempo changes.
    """
    beats = np.asarray(beats, float)
    if len(beats) < 8:
        return beats, 0.0, False
    snapped = beats.copy()
    for i, b in enumerate(beats):
        a, c = np.searchsorted(et, [b - win, b + win])
        if c - a < 3:
            continue
        seg = env[a:c]
        j = int(np.argmax(seg))
        loc = env[max(0, a - 300):min(len(env), c + 300)]
        if seg[j] > 0.2 and seg[j] > 2.0 * np.median(loc):
            snapped[i] = et[a + j] - 0.004  # the envelope peaks ~3-4 ms after the attack starts
    P = float(np.median(np.diff(snapped)))
    idx = np.concatenate([[0], np.cumsum(np.maximum(1, np.round(np.diff(snapped) / P)))]).astype(float)
    keep = np.ones(len(snapped), bool)
    for _ in range(4):
        k, c = np.polyfit(idx[keep], snapped[keep], 1)
        res = snapped - (k * idx + c)
        keep = np.abs(res) < 0.04
        if keep.sum() < 8:
            break
    rms = float(np.sqrt(np.mean(res[keep] ** 2))) if keep.sum() >= 8 else 1.0
    if keep.mean() > 0.85 and rms < 0.015:
        # one straight grid over the tracked span (missing / extra tracker beats fixed too)
        n = int(idx[-1]) + 1
        grid = c + k * np.arange(n)
        return grid, rms, True
    out = snapped.copy()
    for i in range(len(snapped)):
        a, b = max(0, i - 8), min(len(snapped), i + 9)
        ii, tt = idx[a:b], snapped[a:b]
        m = np.abs(tt - np.polyval(np.polyfit(ii, tt, 1), ii)) < 0.04
        if m.sum() >= 5:
            kk, cc = np.polyfit(ii[m], tt[m], 1)
            pred = kk * idx[i] + cc
            out[i] = pred + float(np.clip(snapped[i] - pred, -0.012, 0.012))
    return out, rms, False


def _nearest(arr, t):
    j = int(np.searchsorted(arr, t))
    cands = [k for k in (j - 1, j) if 0 <= k < len(arr)]
    return min(cands, key=lambda k: abs(arr[k] - t)) if cands else None


def regular_downbeats(beats, tracked_downs, meter):
    """
    Downbeats every `meter` beats that re-sync to the tracker's downbeats (anime songs insert half
    bars): walk the beats, expect the next downbeat `meter` beats after the last, take the tracker's
    one when it is within a beat of that, else the expected one.
    """
    if not len(beats):
        return np.zeros(0), []
    tdi = sorted({_nearest(beats, t) for t in tracked_downs if _nearest(beats, t) is not None})
    if not tdi:
        return beats[::meter], list(range(0, len(beats), meter))
    out = [tdi[0]]
    # back-fill before the first tracked downbeat
    k = tdi[0] - meter
    while k >= 0:
        out.insert(0, k)
        k -= meter
    tset = set(tdi)
    i = tdi[0]
    while True:
        exp = i + meter
        if exp >= len(beats):
            break
        nxt = exp
        if exp not in tset:
            # a shifted tracker downbeat only counts when the tracker keeps that new phase for
            # the next two bars (a real inserted half bar), not a one-off wobble
            for j in (exp - 1, exp + 1):
                if j in tset and j + meter in tset and j + 2 * meter in tset:
                    nxt = j
                    break
        out.append(nxt)
        i = nxt
    out = sorted(set(j for j in out if 0 <= j < len(beats)))
    return beats[out], out


def meter_of(beats, downs):
    if len(downs) < 4 or len(beats) < 8:
        return 4
    P = float(np.median(np.diff(beats)))
    n = np.round(np.diff(downs) / P)
    n = n[(n >= 2) & (n <= 8)]
    if not len(n):
        return 4
    vals, cnt = np.unique(n, return_counts=True)
    m = int(vals[np.argmax(cnt)])
    return m if m in (2, 3, 4, 6) else 4


def librosa_downbeats(beats, y22):
    """Downbeat phase (of 4) with the most sub-bass + onset energy (refine_audio.py)."""
    import librosa
    hop = 128
    S = np.abs(librosa.stft(y22, n_fft=2048, hop_length=hop))
    f = librosa.fft_frequencies(sr=ASR, n_fft=2048)
    low = np.log1p(S[f < 150].sum(axis=0))
    lflux = np.maximum(0, np.diff(low, prepend=low[:1]))
    oenv = librosa.onset.onset_strength(y=y22, sr=ASR, hop_length=hop)
    n = min(len(lflux), len(oenv))
    sc = lflux[:n] / (np.percentile(lflux, 99) + 1e-9) + 0.5 * oenv[:n] / (np.percentile(oenv, 99) + 1e-9)
    idx = np.clip((beats * ASR / hop).astype(int), 0, n - 1)
    ph = int(np.argmax([sc[idx[k::4]].mean() for k in range(4)]))
    return beats[ph::4]


# ---------------------------------------------------------------- features

def _db(p):
    return 10 * np.log10(np.maximum(p, 1e-12))


def per_interval(vals_t, t, bounds):
    """Mean of a frame series inside each [bounds[i], bounds[i+1])."""
    idx = np.searchsorted(t, bounds)
    out = np.empty(len(bounds) - 1)
    for i, (a, b) in enumerate(zip(idx[:-1], idx[1:])):
        out[i] = vals_t[a:max(b, a + 1)].mean() if a < len(vals_t) else vals_t[-1]
    return out


def to01(v, lo_pct=5, hi_pct=95):
    v = np.asarray(v, float)
    lo, hi = np.percentile(v, lo_pct), np.percentile(v, hi_pct)
    return np.clip((v - lo) / max(1e-6, hi - lo), 0, 1)


def beat_features(y22, beats, dur):
    """Per-beat loudness (dB) of the mix, the percussive part and the sub-bass."""
    import librosa
    S = np.abs(librosa.stft(y22, n_fft=1024, hop_length=HOP))
    f = librosa.fft_frequencies(sr=ASR, n_fft=1024)
    _, Pc = librosa.decompose.hpss(S, kernel_size=17)
    t = librosa.frames_to_time(np.arange(S.shape[1]), sr=ASR, hop_length=HOP, n_fft=1024)
    P2 = S ** 2
    mix = _db(P2.sum(axis=0))
    perc = _db((Pc ** 2).sum(axis=0))
    low = _db(P2[f < 150].sum(axis=0))
    P = float(np.median(np.diff(beats))) if len(beats) > 1 else 0.5
    bounds = np.concatenate([beats, [min(dur, beats[-1] + P)]])
    # loudness in the power domain per beat (a quiet gap does not drag a loud beat down)
    agg = lambda x: _db(per_interval(10 ** (x / 10), t, bounds))
    return agg(mix), agg(perc), agg(low), (S, t, f)


def bar_segments(S, t, f, downs, dur, k=None):
    """Agglomerative segmentation of the bars on chroma + MFCC + loudness."""
    import librosa
    if len(downs) < 8:
        return [0]
    bounds = np.concatenate([downs, [min(dur, downs[-1] + np.median(np.diff(downs)))]])
    chroma = librosa.feature.chroma_stft(S=S ** 2, sr=ASR)
    mf = librosa.feature.mfcc(S=librosa.power_to_db(librosa.feature.melspectrogram(S=S ** 2, sr=ASR, n_mels=64)), n_mfcc=13)
    loud = _db((S ** 2).sum(axis=0))[None]
    feats = []
    for F in (chroma, mf, loud):
        idx = np.searchsorted(t, bounds)
        m = np.stack([F[:, a:max(b, a + 1)].mean(axis=1) for a, b in zip(idx[:-1], idx[1:])], axis=1)
        m = (m - m.mean(axis=1, keepdims=True)) / (m.std(axis=1, keepdims=True) + 1e-6)
        feats.append(m)
    X = np.vstack([feats[0], feats[1] * 0.8, feats[2] * 2.0])
    nb = X.shape[1]
    k = k or int(np.clip(round(dur / 18), 4, 12))
    k = max(2, min(k, nb // 2))
    try:
        b = list(librosa.segment.agglomerative(X, k))
    except Exception:
        b = list(range(0, nb, 8))
    return sorted(set([0] + [int(x) for x in b if 0 < x < nb]))


def snap_phrases(bnds, nb):
    """Move section starts onto the 4-bar phrase grid when they are within a bar of it."""
    if len(bnds) < 2:
        return bnds
    best = max(range(4), key=lambda ph: sum(1 for b in bnds if (b - ph) % 4 == 0))
    out = []
    for b in bnds:
        r = (b - best) % 4
        if r == 1:
            b -= 1
        elif r == 3:
            b += 1
        out.append(max(0, min(nb - 1, b)))
    out = sorted(set(out))
    # merge sections shorter than 2 bars
    merged = [out[0]]
    for b in out[1:]:
        if b - merged[-1] >= 2:
            merged.append(b)
    return merged


def label_sections(bar_t, bar_e, bar_rep, bar_jump, bnds, dur):
    nb = len(bar_t)
    secs = []
    for i, b in enumerate(bnds):
        e = bnds[i + 1] if i + 1 < len(bnds) else nb
        secs.append(dict(b0=b, b1=e, start=float(bar_t[b]), end=float(bar_t[e]) if e < nb else float(dur),
                         energy=float(np.mean(bar_e[b:e])), rep=float(np.mean(bar_rep[b:e])),
                         jump=float(bar_jump[b]) if b < len(bar_jump) else 0.0))
    E = np.array([s['energy'] for s in secs])
    hi = max(0.6, float(np.percentile(E, 67))) if len(E) >= 3 else 0.6
    for i, s in enumerate(secs):
        s['label'] = 'chorus' if s['energy'] >= hi or (s['energy'] >= 0.55 and s['rep'] > 0.5) else 'verse'
        if (s['label'] == 'chorus' and s['jump'] >= 4.0) or (s['jump'] >= 6.0 and s['energy'] >= 0.5):
            s['label'] = 'drop'
    for i, s in enumerate(secs):
        nxt = secs[i + 1] if i + 1 < len(secs) else None
        if s['label'] == 'verse' and nxt and nxt['label'] in ('chorus', 'drop'):
            b0, b1 = s['b0'], s['b1']
            rising = b1 - b0 >= 2 and np.mean(bar_e[max(b0, b1 - 2):b1]) >= np.mean(bar_e[b0:b0 + 2]) + 0.05
            if s['energy'] >= 0.3 and (rising or s['energy'] >= 0.45):
                s['label'] = 'build'
    if secs and secs[0]['start'] <= 3.0 and secs[0]['label'] in ('verse', 'build') and len(secs) > 2 \
            and secs[0]['energy'] < max(E) - 0.15:
        secs[0]['label'] = 'intro'
    if len(secs) > 2 and secs[-1]['label'] == 'verse' and secs[-1]['energy'] < secs[-2]['energy']:
        secs[-1]['label'] = 'outro'
    # a quieter verse after two choruses and before another one is the bridge
    seen = 0
    for i, s in enumerate(secs):
        if s['label'] in ('chorus', 'drop'):
            seen += 1
        elif s['label'] == 'verse' and seen >= 2 and any(x['label'] in ('chorus', 'drop') for x in secs[i + 1:]):
            s['label'] = 'bridge'
    for s in secs:
        for k in ('b0', 'b1'):
            s.pop(k, None)
        s['energy'] = round(s['energy'], 3)
        s['rep'] = round(s['rep'], 3)
        s['jump'] = round(s['jump'], 2)
        s['start'] = round(s['start'], 4)
        s['end'] = round(s['end'], 4)
    return secs


def heat_series(heatmap, dur):
    if not heatmap:
        return None
    try:
        heat = AC.load_heatmap(heatmap)
    except Exception:
        return None
    if not heat:
        return None
    n = int(math.ceil(dur))
    h = np.zeros(n)
    for a, b, v in heat:
        h[int(max(0, a)):int(min(n, math.ceil(b)))] = np.maximum(h[int(max(0, a)):int(min(n, math.ceil(b)))], v)
    skip = int(max(4, 0.04 * dur))  # the intro spike (people start the video) says nothing
    body = h[skip:] if len(h) > skip else h
    lo, hi = np.percentile(body, 10), body.max()
    return np.clip((h - lo) / max(1e-6, hi - lo), 0, 1)


# ---------------------------------------------------------------- analysis

def analyze(path, heatmap=None, tracker='auto'):
    t_start = time.time()
    x = AC.load_audio(path)
    dur = x.shape[1] / SR
    mono = x.mean(axis=0)
    y22 = AC._to_asr(x)
    used = 'librosa'
    bt = None
    if tracker in ('auto', 'beat_this') and os.environ.get('EDIT_BEAT_TRACKER', '') != 'librosa':
        bt = _beat_this(mono)
        if bt is not None:
            used = 'beat_this'
    env, et = _attack_env(y22)
    if bt is None:
        raw = _librosa_beats(y22)
        beats, rms, straight = refine_beats(raw, env, et)
        tracked_downs = librosa_downbeats(beats, y22)
    else:
        beats, rms, straight = refine_beats(bt[0], env, et)
        tracked_downs = bt[1]
    beats = beats[(beats >= 0) & (beats < dur)]
    meter = meter_of(beats, tracked_downs)
    downs, down_idx = regular_downbeats(beats, tracked_downs, meter)
    P = float(np.median(np.diff(beats)))
    loud, perc, low, (S, st, sf) = beat_features(y22, beats, dur)
    e_raw = 0.5 * (loud - np.median(loud)) + 0.3 * (perc - np.median(perc)) + 0.2 * (low - np.median(low))
    energy = to01(np.convolve(e_raw, [0.25, 0.5, 0.25], mode='same'))
    try:
        sf_ = AC.song_features(x, beats)
        rep = AC._z(sf_['rep']) if 'rep' in sf_ else np.zeros(len(beats))
        voc = AC._z(sf_['voc']) if 'voc' in sf_ else np.zeros(len(beats))
    except Exception as e:
        log(f'song features failed: {e}')
        rep = voc = np.zeros(len(beats))
    # bars
    nb = len(downs)
    bar_of_beat = np.clip(np.searchsorted(downs, beats + 1e-6) - 1, 0, max(0, nb - 1))
    bar_e = np.array([energy[bar_of_beat == b].mean() if (bar_of_beat == b).any() else 0.0 for b in range(nb)])
    bar_rep = np.array([rep[bar_of_beat == b].mean() if (bar_of_beat == b).any() else 0.0 for b in range(nb)])
    bar_loud = np.array([_db((10 ** (loud[bar_of_beat == b] / 10)).mean()) if (bar_of_beat == b).any() else -80 for b in range(nb)])
    bar_perc = np.array([_db((10 ** (perc[bar_of_beat == b] / 10)).mean()) if (bar_of_beat == b).any() else -80 for b in range(nb)])
    jump = np.zeros(nb)
    for b in range(2, nb):
        jm = bar_loud[b:b + 2].mean() - bar_loud[b - 2:b].mean()
        jp = bar_perc[b:b + 2].mean() - bar_perc[b - 2:b].mean()
        jump[b] = max(jm, 0.8 * jp)
    # sections
    bnds = snap_phrases(bar_segments(S, st, sf, downs, dur), nb) if nb >= 8 else [0]
    sections = label_sections(downs, bar_e, bar_rep, jump, bnds, dur) if nb else []
    # drops: local maxima of the jump, >= 3 dB, 4+ bars apart
    drops = []
    order = np.argsort(-jump)
    for b in order:
        if jump[b] < 3.0 or len(drops) >= 10:
            break
        if all(abs(b - d['bar']) >= 4 for d in drops) and bar_e[b:b + 2].mean() >= 0.45:
            drops.append(dict(bar=int(b), t=round(float(downs[b]), 4), jump_db=round(float(jump[b]), 2),
                              after=round(float(bar_e[b:b + 4].mean()), 3), before=round(float(bar_e[max(0, b - 4):b].mean()), 3)))
    drops.sort(key=lambda d: d['t'])
    # strong percussive onsets
    import librosa
    oe = librosa.onset.onset_strength(y=y22, sr=ASR, hop_length=128, aggregate=np.median)
    of = librosa.onset.onset_detect(onset_envelope=oe, sr=ASR, hop_length=128, backtrack=True, units='frames')
    strength = oe[np.clip(of, 0, len(oe) - 1)]
    thr = np.percentile(strength, 55) if len(strength) else 0
    onsets = (of[strength >= thr] * 128 / ASR).round(4).tolist()
    heat = heat_series(heatmap, dur)
    try:
        L = AC.lufs(x)
        tp = 20 * math.log10(max(1e-9, AC.true_peak(x)))
    except Exception:
        L, tp = None, None
    out = dict(
        ok=True, path=os.path.abspath(path), duration=round(dur, 4), tracker=used,
        bpm=round(60.0 / P, 3), beat_period=round(P, 5), meter=int(meter),
        straight_grid=bool(straight), grid_rms_ms=round(rms * 1000, 1),
        beats=beats.round(4).tolist(), downbeats=np.asarray(downs).round(4).tolist(), downbeat_index=[int(i) for i in down_idx],
        beat_energy=energy.round(3).tolist(), beat_loud_db=loud.round(2).tolist(), beat_perc_db=perc.round(2).tolist(),
        beat_low_db=low.round(2).tolist(), beat_rep=np.asarray(rep).round(3).tolist(), beat_voc=np.asarray(voc).round(3).tolist(),
        bar_energy=bar_e.round(3).tolist(), bar_jump_db=jump.round(2).tolist(),
        sections=sections, drops=drops, onsets=onsets,
        heat=heat.round(3).tolist() if heat is not None else None,
        lufs=round(L, 2) if L is not None else None, true_peak_db=round(tp, 2) if tp is not None else None,
        analysis_s=round(time.time() - t_start, 1),
    )
    return out


def load(path_or_dict):
    if isinstance(path_or_dict, dict):
        return path_or_dict
    with open(path_or_dict, encoding='utf-8') as f:
        return json.load(f)


# ---------------------------------------------------------------- windows

def _arr(A, k):
    return np.asarray(A.get(k) or [], float)


def energy_at(A, t0, t1):
    b, e = _arr(A, 'beats'), _arr(A, 'beat_energy')
    m = (b >= t0 - 1e-6) & (b < t1)
    return float(e[m].mean()) if m.any() else 0.0


def heat_at(A, t0, t1):
    h = A.get('heat')
    if not h:
        return None
    h = np.asarray(h, float)
    a, b = int(max(0, t0)), int(min(len(h), math.ceil(t1)))
    return float(h[a:b].mean()) if b > a else None


def section_at(A, t):
    for s in A.get('sections') or []:
        if s['start'] - 1e-6 <= t < s['end']:
            return s
    return None


def snap_down(A, t, side='nearest'):
    d = _arr(A, 'downbeats')
    if not len(d):
        return float(t)
    if side == 'before':
        j = int(np.searchsorted(d, t + 1e-6)) - 1
        return float(d[max(0, j)])
    if side == 'after':
        j = int(np.searchsorted(d, t - 1e-6))
        return float(d[min(len(d) - 1, j)])
    return float(d[_nearest(d, t)])


def choose_window(A, length, intro=(0.0, 0.0), min_len=None, max_len=None, contrast=0.0, phrase_end=True,
                  exclude=None, rng=None):
    """
    The best [start, end] for a short: a drop / chorus start `d` (the drop list, chorus section
    starts and the heatmap peak) with `intro` = (min, max) seconds of lead-in before it, whole bars,
    ending on a downbeat (a phrase end after d when possible).
      contrast  > 0 rewards a quieter lead-in than body (K-pop / character hook intros)
    Returns {'start','end','drop','intro','score'} in song seconds.
    """
    dur = float(A['duration'])
    downs = _arr(A, 'downbeats')
    min_len = min_len or length * 0.8
    max_len = max_len or length * 1.2
    cands = {}
    for d in A.get('drops') or []:
        cands[round(d['t'], 3)] = max(cands.get(round(d['t'], 3), 0.0), min(1.5, d['jump_db'] / 6.0))
    for s in A.get('sections') or []:
        if s['label'] in ('chorus', 'drop'):
            t = round(snap_down(A, s['start']), 3)
            cands[t] = max(cands.get(t, 0.0), 0.3 + min(1.0, max(0.0, s.get('jump', 0)) / 6.0))
    h = A.get('heat')
    if h:
        hh = np.convolve(np.asarray(h, float), np.ones(8) / 8, mode='same')
        pk = int(np.argmax(hh[int(0.05 * dur):])) + int(0.05 * dur)
        t = round(snap_down(A, max(0.0, pk - 4)), 3)
        cands.setdefault(t, 0.2)
    if not cands:
        for t in downs[::8]:
            cands[round(float(t), 3)] = 0.0
    best = None
    bar = float(np.median(np.diff(downs))) if len(downs) > 2 else 2.0
    for d, jscore in cands.items():
        if exclude and any(a - 1 <= d <= b + 1 for a, b in exclude):
            continue
        for want in sorted(set([intro[0], (intro[0] + intro[1]) / 2, intro[1]])):
            start = snap_down(A, d - want, 'nearest') if want > 0 else d
            if start > d + 1e-6:
                start = snap_down(A, d - want, 'before')
            if start < 0.5 and d - want > 0.5:
                continue
            lead = d - start
            if lead < intro[0] - bar * 0.55 or lead > intro[1] + bar * 0.55:
                continue
            # end: a downbeat within [min_len, max_len]; prefer phrase ends (4 bars after d)
            ends = downs[(downs >= start + min_len - 1e-6) & (downs <= start + max_len + 1e-6)]
            if not len(ends):
                continue
            def end_cost(e):
                c = abs((e - start) - length) / length
                if phrase_end:
                    nb_after = round((e - d) / bar)
                    c += 0.0 if nb_after % 4 == 0 else (0.08 if nb_after % 2 == 0 else 0.2)
                return c
            end = float(min(ends, key=end_cost))
            if end > dur - 0.5:
                continue
            body = energy_at(A, d, end)
            before = energy_at(A, start, d) if lead > 0.2 else body
            ht = heat_at(A, d, end)
            score = 1.0 * body + 0.8 * jscore + (1.6 * ht if ht is not None else 0.0) \
                + contrast * max(-0.3, body - before) - 0.6 * end_cost(end)
            if d < 6.0:
                score -= 0.5
            if rng is not None:
                score += rng.uniform(0, 0.08)
            if best is None or score > best['score']:
                best = dict(start=round(start, 4), end=round(end, 4), drop=round(d, 4), intro=round(lead, 4),
                            score=round(score, 3))
    if best is None:  # nothing fits: the loudest stretch
        b = _arr(A, 'beats')
        e = _arr(A, 'beat_energy')
        P = float(A['beat_period'])
        n = max(4, int(length / P))
        i = int(np.argmax(np.convolve(e, np.ones(n), mode='valid'))) if len(e) > n else 0
        s = snap_down(A, b[i] if len(b) else 0.0)
        best = dict(start=round(s, 4), end=round(min(dur - 0.5, s + length), 4), drop=round(s, 4), intro=0.0, score=0.0)
    return best


def choose_long_window(A, min_len=120.0, max_len=240.0):
    """The whole song when it fits (leading silence skipped), else a section-aligned cut of it."""
    dur = float(A['duration'])
    beats = _arr(A, 'beats')
    first = float(beats[0]) if len(beats) else 0.0
    start = max(0.0, snap_down(A, first, 'before')) if first > 1.0 else 0.0
    # the song's audible end: last beat with any energy
    e = _arr(A, 'beat_loud_db')
    end = dur
    if len(e):
        alive = np.where(e > np.max(e) - 40)[0]
        if len(alive):
            end = min(dur, float(beats[alive[-1]]) + 2 * float(A['beat_period']))
    if end - start <= max_len + 8:
        return dict(start=round(start, 4), end=round(end, 4))
    secs = A.get('sections') or []
    best = None
    for s0 in [x['start'] for x in secs if x['start'] < end - min_len] or [start]:
        for s1 in [x['end'] for x in secs]:
            L = s1 - s0
            if not (min_len <= L <= max_len):
                continue
            n_ch = sum(1 for x in secs if x['label'] in ('chorus', 'drop') and x['start'] >= s0 and x['end'] <= s1)
            sc = n_ch + L / max_len - (0.5 if s0 > 30 else 0)
            if best is None or sc > best[0]:
                best = (sc, s0, s1)
    if best is None:
        return dict(start=round(start, 4), end=round(min(end, start + max_len), 4))
    return dict(start=round(best[1], 4), end=round(best[2], 4))


def pulse_factor(P, meter, lo, hi):
    """Beats per felt pulse: the divisor of the bar whose period lands inside [lo, hi] (else nearest)."""
    if meter == 3:
        opts = [1, 3]
    elif meter == 6:
        opts = [1, 2, 3, 6]
    else:
        opts = [1, 2, 4]
    inside = [k for k in opts if lo <= k * P <= hi]
    if inside:
        return min(inside, key=lambda k: abs(k * P - (lo + hi) / 2))
    return min(opts, key=lambda k: min(abs(k * P - lo), abs(k * P - hi)))


def window_view(A, start, end, lo=0.45, hi=0.75):
    """
    Everything a planner needs in OUTPUT time (0 = the video's first frame = song time `start`):
      beats      the felt pulse (every k-th tracked beat, aligned to the downbeats), a few past the end
      raw_beats  the tracked beats; downbeats; bar length; energy per pulse beat (0..1)
      sections   clipped to the window; drops inside; strong onsets
    """
    beats = _arr(A, 'beats')
    downs = _arr(A, 'downbeats')
    E = _arr(A, 'beat_energy')
    P = float(A['beat_period'])
    meter = int(A.get('meter') or 4)
    k = pulse_factor(P, meter, lo, hi)
    dset = set(int(i) for i in (A.get('downbeat_index') or []))
    pulse_idx, since = [], None
    for i in range(len(beats)):
        if i in dset:
            since = 0
        elif since is not None:
            since += 1
        else:
            since = i  # before the first downbeat: count from the song start
        if since % k == 0:
            pulse_idx.append(i)
    pulse_idx = np.asarray(pulse_idx, int)
    pb = beats[pulse_idx] if len(pulse_idx) else beats
    # energy of a pulse beat = mean over the tracked beats it spans
    pe = []
    for j, i in enumerate(pulse_idx):
        i1 = pulse_idx[j + 1] if j + 1 < len(pulse_idx) else i + k
        pe.append(float(E[i:max(i + 1, i1)].mean()) if i < len(E) else 0.0)
    pe = np.asarray(pe)
    Pp = k * P
    m = (pb >= start - Pp - 1e-6) & (pb <= end + 2 * Pp)
    md = (downs >= start - 1e-6) & (downs <= end + 4 * P * meter)
    mr = (beats >= start - P - 1e-6) & (beats <= end + 2 * P)
    secs = []
    for s in A.get('sections') or []:
        if s['end'] <= start or s['start'] >= end:
            continue
        secs.append(dict(s, start=round(max(0.0, s['start'] - start), 4), end=round(min(end, s['end']) - start, 4)))
    drops = [dict(d, t=round(d['t'] - start, 4)) for d in A.get('drops') or [] if start - 0.01 <= d['t'] < end]
    ons = [round(t - start, 4) for t in A.get('onsets') or [] if start <= t < end]
    bar = float(np.median(np.diff(downs))) if len(downs) > 2 else P * meter
    return dict(
        start=float(start), end=float(end), duration=round(end - start, 4),
        P=round(Pp, 5), k=int(k), raw_P=round(P, 5), meter=meter, bar=round(bar, 4), bpm=round(60 / Pp, 2),
        beats=(pb[m] - start).round(4).tolist(), energy=pe[m].round(3).tolist(),
        raw_beats=(beats[mr] - start).round(4).tolist(), raw_energy=E[mr].round(3).tolist(),
        downbeats=(downs[md] - start).round(4).tolist(),
        sections=secs, drops=drops, onsets=ons,
    )


# ---------------------------------------------------------------- cli

def main():
    ap = argparse.ArgumentParser(description='Music analysis for the edit planners.')
    ap.add_argument('--audio', required=True)
    ap.add_argument('--heatmap')
    ap.add_argument('--out', required=True)
    ap.add_argument('--tracker', default='auto', choices=['auto', 'beat_this', 'librosa'])
    ap.add_argument('--window', type=float, help='also print the best window of this length (s)')
    ap.add_argument('--intro', type=float, nargs=2, default=(0.0, 0.0))
    args = ap.parse_args()
    try:
        A = analyze(args.audio, args.heatmap, args.tracker)
        os.makedirs(os.path.dirname(os.path.abspath(args.out)) or '.', exist_ok=True)
        with open(args.out, 'w', encoding='utf-8') as f:
            json.dump(A, f)
        res = dict(ok=True, out=os.path.abspath(args.out), bpm=A['bpm'], meter=A['meter'], duration=A['duration'],
                   tracker=A['tracker'], straight=A['straight_grid'], grid_rms_ms=A['grid_rms_ms'],
                   sections=[f"{s['label']}@{s['start']:.1f}" for s in A['sections']],
                   drops=[d['t'] for d in A['drops']], lufs=A['lufs'], seconds=A['analysis_s'])
        if args.window:
            res['window'] = choose_window(A, args.window, tuple(args.intro))
        print(json.dumps(res))
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(json.dumps({'ok': False, 'reason': str(e)[:300]}))
        sys.exit(1)


if __name__ == '__main__':
    main()
