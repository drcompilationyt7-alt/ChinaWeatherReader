"""Musical moments of a song (or of an edit's own audio) and how a set of cut times sits on them.

Slow-mo (owner 2026-10-18/19): the cuts must follow the song the way the reference (_sX2EUexbW8, "GOLDEN HOUR") does: not
every beat, but on musical moments (phrase starts, vocal lines, accents, the main beat of the song's real meter), so the
viewer can SEE the picture match the song. Everything here is measured on the audio that is actually heard (the regular OR
the slowed version), never on another version's timing.

    moments(path, t0=0.0, dur=None, vocals=True) -> dict(
        eighth   the tracked fast pulse (s), k / main: the meter-correct main beat = k eighths (autocorrelation: 3 for a
                 6/8 / triplet song like Golden Hour, 2 for 4/4), beats: main-beat times, bars: bar starts,
        accents: [(t, strength)] spectral-flux peaks >= 2.5 x median, vocal_onsets: [(t, prominence)], phrase_starts:
        vocal onsets after >= 0.25 s of vocal silence, vocal_active: [(a, b)])
    score_at(M, t) -> (score, kind): what musical moment a switch at t lands on (the switch frame sits ~65 ms before it)
    align(cuts, M) -> per-cut rows + stats (median offset to the nearest moment, share on a moment, share on the meter)

    python core/edit/music_moments.py AUDIO_OR_VIDEO [--t0 S] [--dur S] [--cuts 5.56,9.56,...]
Nothing is stored except Demucs' own stem cache (lyrics.vocal_stem); no lyrics text is used.
"""
import argparse
import json
import os
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

SR = 22050
HOP = 256
LEAD = 0.065          # _sX2: the switch frame sits 48-83 ms BEFORE the song's accent (refs/remake-cinematic_slowmo.md)
# a switch counts as "on" a moment when the moment follows it by -0.06 .. +0.16 s (the switch leads the sound)
ON_WIN = (-0.02, 0.14)
VOC_PROM = 0.25       # a sung syllable strong enough to read as a musical moment (_sX2's cut syllables: 0.29-1.78)
W_KIND = dict(phrase=3.0, vocal=2.0, accent=1.2, bar=1.0, beat=0.4)


def load(path, t0=0.0, dur=None, sr=SR):
    cmd = ['ffmpeg', '-v', 'error', '-ss', f'{max(0.0, t0):.4f}']
    if dur:
        cmd += ['-t', f'{dur:.4f}']
    cmd += ['-i', str(path), '-vn', '-ac', '1', '-ar', str(sr), '-f', 'f32le', '-']
    b = subprocess.run(cmd, capture_output=True).stdout
    return np.frombuffer(b, np.float32).astype(np.float64)


def _peaks(env, t, thr, min_gap=0.09):
    from scipy.signal import find_peaks
    pk, _ = find_peaks(env, height=thr, distance=max(1, int(min_gap / (t[1] - t[0]))))
    return [(float(t[i]), float(env[i])) for i in pk]


def _meter(env, dt, P):
    """main beat = k x the fast pulse P, k by the envelope autocorrelation (the meter): (k, {k: ac})"""
    e0 = env - env.mean()
    ac = np.correlate(e0, e0, 'full')[len(e0) - 1:]
    ac = ac / (ac[0] + 1e-9)
    sc = {}
    for k in (2, 3, 4):
        lag = k * P / dt
        i = int(round(lag))
        if i + 2 >= len(ac):
            continue
        sc[k] = float(ac[max(0, i - 2):i + 3].max())
    if not sc:
        return 2, sc
    k = max(sc, key=sc.get)
    if k == 4 and sc.get(2, 0) >= 0.85 * sc[4]:
        k = 2            # 4 eighths vs 2: the shorter level unless 4 clearly wins (2 and 4 are the same 4/4 meter)
    return k, sc


def moments(path, t0=0.0, dur=None, vocals=True, threads=2):
    import librosa
    y = load(path, t0, dur).astype(np.float32)
    D = len(y) / SR
    env = librosa.onset.onset_strength(y=y, sr=SR, hop_length=HOP)
    env = env / (float(np.median(env[env > 0])) + 1e-9)
    t = np.arange(len(env)) * HOP / SR
    dt = HOP / SR
    tempo, bt = librosa.beat.beat_track(onset_envelope=env, sr=SR, hop_length=HOP, units='time', tightness=200)
    tempo = float(np.atleast_1d(tempo)[0])
    P = 60.0 / tempo
    # the fast pulse at the eighth level (0.25-0.45 s): halve a slow track
    while P > 0.45:
        P /= 2.0
    while P < 0.22:
        P *= 2.0
    k, ac = _meter(env, dt, P)
    main = k * P
    # main beats: DP beat tracking at the main tempo (follows a live / slowed song's drift), phase by onset strength
    _, mb = librosa.beat.beat_track(onset_envelope=env, sr=SR, hop_length=HOP, units='time', start_bpm=60.0 / main,
                                    tightness=400)
    mb = np.asarray(mb, float)
    if len(mb) < 3:
        ph = max(range(int(main / dt)), key=lambda i: float(env[i::max(1, int(round(main / dt)))].sum()))
        mb = np.arange(ph * dt, D, main)
    # librosa trims the beats of a quiet head / tail: extend the grid with the local interval to the whole window
    if len(mb) >= 3:
        iv = float(np.median(np.diff(mb[-6:])))
        while mb[-1] + iv < D - 0.05:
            mb = np.append(mb, mb[-1] + iv)
        iv0 = float(np.median(np.diff(mb[:6])))
        while mb[0] - iv0 > 0.0:
            mb = np.insert(mb, 0, mb[0] - iv0)
    # bar starts: 2 main beats per bar (6/8 = 2 dotted quarters; 4/4 at k=2 -> 2 quarters = half bar, close enough for
    # a cut rule), phase = the parity whose beats carry more low-band onset energy
    lo = librosa.onset.onset_strength(y=y, sr=SR, hop_length=HOP, fmax=200, n_mels=32)
    lo = lo / (float(np.median(lo[lo > 0])) + 1e-9)

    def at(e, x):
        i = int(round(x / dt))
        return float(e[max(0, i - 2):i + 3].max()) if 0 <= i < len(e) else 0.0
    par = [sum(at(lo, x) + at(env, x) for x in mb[p::2]) for p in (0, 1)]
    bars = mb[int(np.argmax(par))::2]
    acc = [(a, s) for a, s in _peaks(env, t, 2.5)]
    M = dict(path=str(path), t0=t0, dur=round(D, 3), tempo=round(tempo, 2), eighth=round(P, 4), k=k, main=round(main, 4),
             meter_ac={str(a): round(b, 3) for a, b in ac.items()}, beats=[round(float(x), 3) for x in mb],
             bars=[round(float(x), 3) for x in bars], accents=[(round(a, 3), round(s, 2)) for a, s in acc],
             vocal_onsets=[], phrase_starts=[], vocal_active=[])
    if vocals:
        import lyrics as LY
        v16 = LY.vocal_stem(path, float(t0), float(t0) + D, threads=threads)
        on, prom, venv, rms, vhop = LY.vocal_onsets(v16)
        gate = 0.12 * (np.percentile(rms, 95) + 1e-9)
        act = rms > gate
        # vocal activity runs (>= 0.12 s), gaps < 0.15 s bridged
        runs, i, n = [], 0, len(act)
        while i < n:
            if act[i]:
                j = i
                while j < n and act[j]:
                    j += 1
                runs.append([i * vhop, j * vhop])
                i = j
            else:
                i += 1
        merged = []
        for a, b in runs:
            if merged and a - merged[-1][1] < 0.15:
                merged[-1][1] = b
            else:
                merged.append([a, b])
        merged = [r for r in merged if r[1] - r[0] >= 0.12]
        M['vocal_active'] = [(round(a, 3), round(b, 3)) for a, b in merged]
        vo = [(float(x), float(p)) for x, p in zip(on, prom) if p >= 0.12]
        M['vocal_onsets'] = [(round(x, 3), round(p, 2)) for x, p in vo]
        ps = []
        for x, p in vo:
            prev_end = max([b for a, b in merged if b <= x + 0.02] or [-9.0])
            inside = any(a - 0.06 <= x <= b for a, b in merged if a < x - 0.06)
            if x - prev_end >= 0.25 and not inside:
                ps.append((round(x, 3), round(p, 2)))
            elif not merged:
                ps.append((round(x, 3), round(p, 2)))
        # the first onset of each activity run is a phrase start too (the onset detector can sit a few ms inside it)
        for a, b in merged:
            first = [x for x, p in vo if a - 0.08 <= x <= a + 0.12]
            if first and all(abs(first[0] - q) > 0.05 for q, _ in ps):
                gap_before = a - max([bb for aa, bb in merged if bb <= a] or [-9.0])
                if gap_before >= 0.25:
                    ps.append((round(first[0], 3), 1.0))
        M['phrase_starts'] = sorted(ps)
    return M


def _near(lst, t, win=ON_WIN):
    """the strongest (time, strength) in lst with time - t inside win"""
    best = None
    for x in lst:
        a, s = (x if isinstance(x, (list, tuple)) else (x, 1.0))
        if win[0] <= a - t <= win[1] and (best is None or s > best[1]):
            best = (a, s)
    return best


def score_at(M, t):
    """(score, kind, moment time) of a switch frame at t: the best musical moment just after it (ON_WIN)."""
    best = (0.0, 'none', None)
    for kind, lst in (('phrase', M.get('phrase_starts') or []), ('vocal', [v for v in M.get('vocal_onsets') or [] if v[1] >= VOC_PROM]),
                      ('accent', [a for a in M.get('accents') or [] if a[1] >= 3.0]), ('bar', M.get('bars') or []),
                      ('beat', M.get('beats') or [])):
        h = _near(lst, t)
        if h is not None:
            sc = W_KIND[kind] * (min(1.5, 0.5 + float(h[1])) if kind == 'vocal' else 1.0)
            if sc > best[0]:
                best = (sc, kind, h[0])
    return best


def meter_phase(M, t):
    """distance (fraction of the main beat, 0..0.5) of t + LEAD from the nearest tracked main beat"""
    b = np.asarray(M.get('beats') or [], float)
    if not len(b):
        return None
    j = int(np.argmin(np.abs(b - (t + LEAD))))
    return float(abs(b[j] - (t + LEAD)) / max(1e-6, float(M['main'])))


def align(cuts, M):
    rows = []
    for c in cuts:
        sc, kind, mt = score_at(M, c)
        ph = meter_phase(M, c)
        # the offset to the nearest moment of ANY kind within +-0.35 s (for the offset stats)
        allm = [x[0] if isinstance(x, (list, tuple)) else x for x in
                (M.get('phrase_starts') or []) + [v for v in M.get('vocal_onsets') or [] if v[1] >= VOC_PROM] + [a for a in M.get('accents') or [] if a[1] >= 3.0]
                + (M.get('beats') or [])]
        off = min(allm, key=lambda m: abs(m - c)) - c if allm else None
        rows.append(dict(cut=round(c, 3), kind=kind, score=round(sc, 2), moment=mt, offset=None if off is None else round(off, 3),
                         phase=None if ph is None else round(ph, 3)))
    n = max(1, len(rows))
    musical = [r for r in rows if r['kind'] in ('phrase', 'vocal', 'accent', 'bar')]
    on_meter = [r for r in rows if r['phase'] is not None and r['phase'] <= 0.15]
    offs = [r['offset'] for r in rows if r['offset'] is not None and abs(r['offset']) <= 0.35]
    st = dict(n=len(rows), on_moment=round(len(musical) / n, 3), on_any=round(sum(r['kind'] != 'none' for r in rows) / n, 3),
              on_meter=round(len(on_meter) / n, 3),
              phrase_or_vocal=round(sum(r['kind'] in ('phrase', 'vocal') for r in rows) / n, 3),
              offset_med=round(float(np.median(offs)), 3) if offs else None,
              offset_iqr=round(float(np.percentile(offs, 75) - np.percentile(offs, 25)), 3) if len(offs) > 1 else None,
              mean_score=round(float(np.mean([r['score'] for r in rows])) if rows else 0.0, 2))
    return rows, st


# ---------------------------------------------------------------------------------------------------------------------
# HOLDS (owner 2026-10-19, round 2: "don't change clips when they are holding a key or a word"): a cut inside a sustained
# sung note / held word / held instrumental chord or swell looks wrong; cuts belong where the music turns (a new sung word
# after a hold, a phrase boundary, a new attack, a breath). Measured on the Demucs vocal stem (pitch + energy stability,
# librosa pyin) and on the accompaniment (mix - vocals: harmonic onsets, chroma changes, sustain / swell runs).
HOP_H = 160                  # 10 ms at 16 kHz
HOLD_MIN = 0.30              # a sung note this long is a hold (its body is no place for a cut)
HOLD_IN, HOLD_OUT = 0.10, 0.15   # the hold body = (onset + HOLD_IN, offset - HOLD_OUT): the switch frame leads the next
                                 # sung onset by up to ~0.15 s (_sX2: 0.05-0.11 s), so that much before the offset is clear
INST_HOLD_MIN = 0.80         # accompaniment without a new attack (>= 2 x median flux) this long = a held chord / swell;
                             # only where nobody sings (while the voice is active, the sung notes decide)


def _runs(mask, min_len=1):
    out, i, n = [], 0, len(mask)
    while i < n:
        if mask[i]:
            j = i
            while j < n and mask[j]:
                j += 1
            if j - i >= min_len:
                out.append((i, j))
            i = j
        else:
            i += 1
    return out


def vocal_notes(v16, sr=16000, hop=HOP_H, onsets=None, dip_thr=0.55, valley_thr=0.5, step_st=0.8, f0=None):
    """Sung notes from the vocal stem: activity (RMS gate, breaks < 60 ms bridged) split at the vocal onsets that start
    a new syllable (energy dip or onset + pitch step; pyin pitch). -> [dict(a, b, dur, midi, stab (share of frames within +-0.5 st of the
    note median), hold)]"""
    import librosa
    from scipy.ndimage import median_filter
    if f0 is None:
        f0, vf, vp = librosa.pyin(v16.astype(np.float32), fmin=75.0, fmax=1100.0, sr=sr, frame_length=1024, hop_length=hop,
                                  fill_na=np.nan)
    rms = librosa.feature.rms(y=v16, frame_length=640, hop_length=hop, center=True)[0][:len(f0)]
    if len(rms) < len(f0):
        rms = np.pad(rms, (0, len(f0) - len(rms)))
    gate = 0.10 * (np.percentile(rms, 95) + 1e-9)
    act = (rms > gate)
    # bridge breaks < 60 ms
    for a, b in _runs(~act):
        if b - a < int(0.06 * sr / hop) and a > 0 and b < len(act):
            act[a:b] = True
    midi = np.where(np.isfinite(f0), librosa.hz_to_midi(np.where(np.isfinite(f0), f0, 1.0)), np.nan)
    ms = midi.copy()
    ok = np.isfinite(ms)
    if ok.any():
        ms[ok] = median_filter(ms[ok], size=5)
    # note boundaries = vocal onsets that start a NEW syllable: an energy dip (consonant / breath, >= 5 dB under the
    # surrounding vowel level) or a clear onset with a pitch step. A same-pitch flux peak without a dip is vibrato /
    # a vowel colour change inside a held note (the reference's 0.56 s held note was split by one); a legato pitch step
    # without an onset is a melisma on the same word (still a held word)
    fr = sr / hop
    bounds = {}
    for t, p in (onsets or []):
        i = int(round(t * fr))
        if p < 0.12 or i < 15 or i > len(rms) - 15:
            continue
        lv = max(float(np.median(rms[i - 15:i - 5])), float(np.median(rms[i + 3:i + 13])))
        dip = float(rms[i - 6:i + 3].min()) / (lv + 1e-9)
        pa, pb = ms[i - 9:i - 1], ms[i + 2:i + 10]
        dp = (abs(np.nanmedian(pb) - np.nanmedian(pa)) if np.isfinite(pa).sum() >= 3 and np.isfinite(pb).sum() >= 3
              else 9.0)
        if dip <= dip_thr or (p >= 0.3 and dp >= step_st):
            bounds[i] = round(dip, 2)
    # energy valleys without a detected onset (a soft consonant between two vowels): >= 6 dB under both neighbouring
    # vowel peaks within 0.3 s
    from scipy.signal import find_peaks as _fp
    from scipy.ndimage import uniform_filter1d
    rs = uniform_filter1d(rms.astype(float), 3)
    vi, _ = _fp(-rs, distance=int(0.08 * fr))
    w3 = int(0.3 * fr)
    for i in vi:
        if i < 3 or i > len(rs) - 3:
            continue
        l, r = float(rs[max(0, i - w3):i].max()), float(rs[i + 1:i + 1 + w3].max())
        if rs[i] <= valley_thr * min(l, r) and min(l, r) > gate and all(abs(i - j) > int(0.06 * fr) for j in bounds):
            bounds[int(i)] = round(float(rs[i] / (min(l, r) + 1e-9)), 2)
    notes = []
    for a, b in _runs(act, int(0.08 * sr / hop)):
        cut = sorted(x for x in bounds if a + 3 < x < b - 3)
        edges = [a] + cut + [b]
        for x, y in zip(edges[:-1], edges[1:]):
            if y - x < 3:
                continue
            seg = ms[x:y]
            fin = np.isfinite(seg)
            md = float(np.nanmedian(seg)) if fin.sum() >= 3 else None
            stab = float((np.abs(seg[fin] - md) <= 0.5).mean()) if md is not None else 0.0
            dur = (y - x) * hop / sr
            voiced = float(fin.mean())
            notes.append(dict(a=round(x * hop / sr, 3), b=round(y * hop / sr, 3), dur=round(dur, 3),
                              midi=None if md is None else round(md, 2), stab=round(stab, 2), voiced=round(voiced, 2),
                              hold=bool(dur >= HOLD_MIN and (voiced >= 0.5 or dur >= 0.5))))
    return notes, dict(f0=f0, rms=rms, hop=hop / sr, gate=float(gate))


def inst_events(acc, sr=16000, hop=HOP_H):
    """Accompaniment (mix - vocals): attacks (onset-strength peaks >= 2 x median), chord changes (CQT chroma cosine
    distance of the 0.35 s before / after >= 0.25), and held stretches: >= INST_HOLD_MIN without an attack, kind
    'swell' when the RMS rises >= 3 dB over it, else 'sustain'. -> dict(attacks, chords, holds)"""
    import librosa
    from scipy.signal import find_peaks
    env = librosa.onset.onset_strength(y=acc, sr=sr, hop_length=hop, n_mels=64, fmax=7000)
    env = env / (float(np.median(env[env > 0])) + 1e-9)
    t = np.arange(len(env)) * hop / sr
    pk, pr = find_peaks(env, height=2.0, distance=int(0.08 * sr / hop), prominence=1.0)
    attacks = [(round(float(t[i]), 3), round(float(env[i]), 2)) for i in pk]
    chroma = librosa.feature.chroma_cqt(y=acc, sr=sr, hop_length=hop * 2)
    ct = np.arange(chroma.shape[1]) * hop * 2 / sr
    w = max(2, int(0.35 * sr / (hop * 2)))
    dist = np.zeros(chroma.shape[1])
    for i in range(w, chroma.shape[1] - w):
        u, v = chroma[:, i - w:i].mean(1), chroma[:, i:i + w].mean(1)
        dist[i] = 1.0 - float(u @ v / (np.linalg.norm(u) * np.linalg.norm(v) + 1e-9))
    cp, _ = find_peaks(dist, height=0.25, distance=int(0.5 * sr / (hop * 2)))
    chords = [(round(float(ct[i]), 3), round(float(dist[i]), 2)) for i in cp]
    rms = librosa.feature.rms(y=acc, frame_length=640, hop_length=hop)[0]
    holds = []
    at = [a for a, s in attacks if s >= 2.0] + [0.0, len(acc) / sr]
    at = sorted(at)
    for a, b in zip(at[:-1], at[1:]):
        if b - a >= INST_HOLD_MIN:
            ia, ib = int(a * sr / hop), int(b * sr / hop)
            seg = rms[ia:ib]
            if len(seg) < 4 or float(seg.mean()) < 0.05 * float(np.percentile(rms, 95)):
                continue
            q = max(2, len(seg) // 4)
            rise = 20 * np.log10((seg[-q:].mean() + 1e-9) / (seg[:q].mean() + 1e-9))
            holds.append(dict(a=round(a, 3), b=round(b, 3), kind='swell' if rise >= 3.0 else 'sustain', rise_db=round(float(rise), 1)))
    return dict(attacks=attacks, chords=chords, holds=holds)


def holds(path, t0=0.0, dur=None, threads=2, M=None, strict=False):
    """Everything the hold-aware cut matcher needs, measured on the audio actually heard (edit or song seconds from t0):
    dict(notes, vocal_holds, inst, forbid [(a, b, why)], turns [(t, strength, kind)], M (moments)).
    strict (the planner): the holds of a second, more merging segmentation (a syllable boundary needs a >= 7 dB dip or a
    >= 1 st step) are forbidden too - on a long same-pitch line the syllable boundaries are borderline and flip between
    two analyses of the same song (the planner's song window vs the judge's rendered edit: 3e's 16.77 s cut)"""
    import lyrics as LY
    from scipy.signal import resample_poly
    if M is None:
        M = moments(path, t0, dur, vocals=True, threads=threads)
    D = float(M['dur'])
    v16 = LY.vocal_stem(path, float(t0), float(t0) + D, threads=threads)
    mix = load(path, t0, D, sr=16000).astype(np.float32)
    n = min(len(mix), len(v16))
    acc = (mix[:n] - v16[:n]).astype(np.float32)
    on, prom, _, _, _ = LY.vocal_onsets(v16)
    notes, aux = vocal_notes(v16, onsets=list(zip(on, prom)))
    inst = inst_events(acc)
    forbid = [(round(x['a'] + HOLD_IN, 3), round(x['b'] - HOLD_OUT, 3), 'vocal_hold') for x in notes if x['hold']
              and x['b'] - HOLD_OUT > x['a'] + HOLD_IN]
    if strict:
        n2, _ = vocal_notes(v16, onsets=list(zip(on, prom)), dip_thr=0.45, valley_thr=0.42, step_st=1.0, f0=aux['f0'])
        forbid += [(round(x['a'] + HOLD_IN, 3), round(x['b'] - HOLD_OUT, 3), 'vocal_hold') for x in n2 if x['hold']
                   and x['b'] - HOLD_OUT > x['a'] + HOLD_IN]
    # instrumental holds count only in the vocal gaps (>= 0.3 s without a sung note)
    gaps, prev = [], 0.0
    for x in notes:
        if x['a'] - prev >= 0.3:
            gaps.append((prev, x['a']))
        prev = max(prev, x['b'])
    if D - prev >= 0.3:
        gaps.append((prev, D))
    for h in inst['holds']:
        for ga, gb in gaps:
            a_, b_ = max(h['a'] + 0.15, ga + 0.05), min(h['b'] - HOLD_OUT, gb - HOLD_OUT)
            if b_ - a_ >= 0.25:
                forbid.append((round(a_, 3), round(b_, 3), 'inst_' + h['kind']))
    # turns: where the music moves on (the cut's moment lands here): a sung note onset after a hold / a breath (phrase),
    # any sung note onset, an accompaniment attack, a chord change, a bar start
    turns = []
    prev_b, prev_hold = -9.0, False
    for x in notes:
        gap = x['a'] - prev_b
        kind = 'phrase' if gap >= 0.25 else ('after_hold' if prev_hold else 'syllable')
        turns.append((x['a'], 3.0 if kind == 'phrase' else (2.4 if kind == 'after_hold' else 1.6), kind))
        prev_b, prev_hold = x['b'], x['hold']
    for a, s in inst['attacks']:
        if s >= 3.0:
            turns.append((a, min(1.6, 0.4 * s), 'attack'))
    for a, s in inst['chords']:
        turns.append((a, 1.4, 'chord'))
    for b in M.get('bars') or []:
        turns.append((float(b), 1.0, 'bar'))
    turns.sort()
    return dict(notes=notes, vocal_holds=[x for x in notes if x['hold']], inst=inst, forbid=sorted(forbid), turns=turns, M=M,
                dur=D, trace=dict(hop=aux['hop'], f0=aux['f0'], vrms=aux['rms'], gate=aux['gate'], mix=mix, acc_attacks=inst['attacks']))


def in_hold(H, c):
    """the forbidden interval (a, b, why) that a switch at c falls in, else None"""
    for a, b, why in H['forbid']:
        if a < c < b:
            return (a, b, why)
    return None


def cut_report(cuts, H, lead=LEAD, end=None):
    """per cut: inside a hold (why)?, the sung note it sits in and how long before that note ends, the time to the next
    sung onset (the 'moment' offset), the pitch step there, the strongest accompaniment attack 0..0.2 s after it, the turn
    it lands on, its phase on the main beat / bar; per shot (cut -> next cut / end): its length, sung notes, holds."""
    M = H['M']
    notes = H['notes']
    att = H['inst']['attacks']
    end = float(end if end is not None else H['dur'])
    rows = []
    for k, c in enumerate(cuts):
        ih = in_hold(H, c)
        cur = [x for x in notes if x['a'] - 0.02 <= c < x['b']]
        nxt = [x for x in notes if x['a'] >= c - 0.02 and not (cur and x is cur[0])]
        hended = [x['b'] for x in notes if x['hold'] and x['b'] <= c + 0.2]
        tn = [tt for tt in H['turns'] if -0.06 <= tt[0] - c <= 0.16]
        best = max(tn, key=lambda z: z[1]) if tn else None
        aa = [x for x in att if 0.0 <= x[0] - c <= 0.2]
        ab = max(aa, key=lambda z: z[1]) if aa else None
        ph = meter_phase(M, c)
        bars = np.asarray(M.get('bars') or [], float)
        bar_ph = None
        if len(bars) > 1:
            j = int(np.argmin(np.abs(bars - (c + lead))))
            bar_ph = round(float((c + lead - bars[j]) / (2 * float(M['main']))), 3)
        c2 = cuts[k + 1] if k + 1 < len(cuts) else end
        sh = [x for x in notes if c <= x['a'] < c2]
        pstep = None
        if nxt and nxt[0]['midi'] is not None:
            prev = cur[0] if cur else ([x for x in notes if x['b'] <= c + 0.02] or [None])[-1]
            if prev is not None and prev['midi'] is not None:
                pstep = round(nxt[0]['midi'] - prev['midi'], 2)
        rows.append(dict(cut=round(c, 3), in_hold=None if ih is None else ih[2], in_note=bool(cur),
                         to_note_end=round(cur[0]['b'] - c, 3) if cur else None,
                         to_next_onset=round(nxt[0]['a'] - c, 3) if nxt else None, pitch_step=pstep,
                         since_hold_end=round(c - max(hended), 3) if hended else None,
                         acc_attack=None if ab is None else [round(ab[0] - c, 3), ab[1]],
                         turn=None if best is None else best[2], turn_off=None if best is None else round(best[0] - c, 3),
                         beat_phase=None if ph is None else round(ph, 3), bar_phase=bar_ph,
                         shot_len=round(c2 - c, 3), shot_notes=len(sh), shot_holds=sum(x['hold'] for x in sh),
                         shot_hold_s=round(sum(x['dur'] for x in sh if x['hold']), 2)))
    return rows


def hold_null(H, t0, t1, grid=None):
    """the share of [t0, t1] (or of the grid points in it) that is forbidden: what a random cut would hit"""
    if grid is not None:
        g = [x for x in grid if t0 <= x <= t1]
        return round(sum(in_hold(H, x) is not None for x in g) / max(1, len(g)), 3)
    ts = np.arange(t0, t1, 0.01)
    return round(float(np.mean([in_hold(H, x) is not None for x in ts])), 3)


def ks_distance(a, b):
    """two-sample Kolmogorov-Smirnov distance (0 = same distribution, 1 = disjoint)"""
    a, b = np.sort(np.asarray(a, float)), np.sort(np.asarray(b, float))
    if not len(a) or not len(b):
        return 1.0
    x = np.concatenate([a, b])
    fa = np.searchsorted(a, x, side='right') / len(a)
    fb = np.searchsorted(b, x, side='right') / len(b)
    return float(np.max(np.abs(fa - fb)))


def plot(H, cuts, out, title='', ref_cuts=None):
    """PNG: mix waveform, vocal energy + pitch, sung notes (holds shaded red, instrumental holds orange), the main beats /
    bars, and the cuts (green = clear, red = inside a hold)."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    tr = H['trace']
    D = H['dur']
    mix = np.asarray(tr['mix'])
    fig, ax = plt.subplots(3, 1, figsize=(max(14, D * 0.55), 7.5), sharex=True,
                           gridspec_kw=dict(height_ratios=[1.0, 1.3, 0.5]))
    tm = np.arange(len(mix)) / 16000.0
    step = max(1, len(mix) // 20000)
    ax[0].plot(tm[::step], mix[::step], lw=0.4, color='#555')
    ax[0].set_ylabel('mix')
    th = np.arange(len(tr['f0'])) * tr['hop']
    f0 = np.asarray(tr['f0'], float)
    import librosa
    md = np.where(np.isfinite(f0), librosa.hz_to_midi(np.where(np.isfinite(f0), f0, 1.0)), np.nan)
    ax[1].plot(th, md, '.', ms=1.5, color='#1f5fbf')
    ax[1].set_ylabel('vocal pitch (midi)')
    a2 = ax[1].twinx()
    a2.plot(th[:len(tr['vrms'])], tr['vrms'], lw=0.6, color='#999')
    a2.set_yticks([])
    for n in H['notes']:
        ax[1].plot([n['a'], n['b']], [n['midi'] or np.nanmean(md), n['midi'] or np.nanmean(md)],
                   color='#c00' if n['hold'] else '#222', lw=3 if n['hold'] else 1.2)
    for a, b, why in H['forbid']:
        for x in ax:
            x.axvspan(a, b, color='#f4a0a0' if why == 'vocal_hold' else '#f8d49a', alpha=0.45, lw=0)
    for b in H['M'].get('beats') or []:
        ax[2].axvline(b, color='#bbb', lw=0.8)
    for b in H['M'].get('bars') or []:
        ax[2].axvline(b, color='#666', lw=1.2)
    for t, s, k in H['turns']:
        if k in ('phrase', 'after_hold', 'syllable'):
            ax[2].plot([t], [0.3 + 0.2 * (k != 'syllable')], 'v', color='#1f5fbf' if k == 'syllable' else '#7a2fbf', ms=4)
        elif k == 'attack':
            ax[2].plot([t], [0.15], '^', color='#555', ms=3)
        elif k == 'chord':
            ax[2].plot([t], [0.8], 's', color='#2a8', ms=4)
    ax[2].set_yticks([])
    ax[2].set_ylim(0, 1)
    ax[2].set_ylabel('beats/turns')
    nh = 0
    for c in cuts:
        bad = in_hold(H, c) is not None
        nh += bad
        for x in ax:
            x.axvline(c, color='#d00' if bad else '#090', lw=2.0 if bad else 1.6)
        ax[0].text(c, ax[0].get_ylim()[1] * 0.9, f'{c:.2f}', fontsize=7, color='#d00' if bad else '#090', rotation=90, va='top')
    for c in ref_cuts or []:
        ax[2].axvline(c, color='#e0a000', lw=1.5, ls='--')
    ax[0].set_title(f'{title}   cuts {len(cuts)}, inside a hold {nh}   (red band = sung hold, orange = instrumental hold)')
    ax[2].set_xlabel('s')
    ax[2].set_xlim(0, D)
    fig.tight_layout()
    fig.savefig(out, dpi=90)
    plt.close(fig)
    return nh


# ---------------------------------------------------------------------------------------------------------------------
# WAVES (owner 2026-10-19, round 3 "momentum"): the song is a series of momentum waves - a sung line / word builds, carries
# or holds, then flattens; ONE clip carries ONE wave, the switch sits at the START of the next wave (its first attack,
# usually on the main beat). 3e failed because holds() split one held note into syllables (a cut at 16.47 s inside the
# held "hour") and its turns were too weak to demand a cut (no cut on the new word at 21.1 s). Measured on the vocal stem:
# loudness valleys + pitch continuity (pyin) + vocal onset strength; fragments of one sustained note (same pitch across a
# shallow dip) are one wave; a long hold that drops to a quieter tail ("hour" -> "(oh)") releases into a new wave.
WV_HOP = 0.01
WV_ACT_DB = -30.0        # vocal active: smoothed level >= this (dB re the stem's p95 frame)
WV_GAP = 0.10            # a silence this long separates two waves (phrase start)
WV_DEEP = 7.0            # a valley this deep (dB under both neighbouring peaks within 0.6 s) is a new syllable / word
                         # (3e: the dips inside the held "hour" reach 6.4 dB; the word starts 7.4-16 dB)
WV_DIP = 4.5             # a shallower valley + a pitch change >= WV_STEP = a new sung note
WV_STEP = 1.5            # semitones (median pitch 0.08-0.40 s before / after the valley)
WV_STEP_DIP = 2.0        # a legato pitch step needs at least this dip AND a vocal onset >= 2.5 x median to start a wave
WV_RELEASE = 7.0         # a hold >= WV_RELEASE_HOLD s that drops this much (0.35 s means) and goes on quieter: a new wave
WV_RELEASE_HOLD = 1.2
WV_LEGATO = 1.0          # semitones between two stable sung pitches (legato word change)
WV_SAME = 0.7            # same note across a dip (vibrato / a breath in a held vowel) unless it is >= 10 dB or has an onset
WV_CLUSTER = 0.25        # valleys this close are one consonant / breath: the first one that qualifies is the start
WV_MIN = 0.60            # shorter waves merge into the next one (a pick-up syllable belongs to the wave it leads into)


def _wv_features(v16, sr=16000):
    import librosa
    from scipy.ndimage import uniform_filter1d
    hop = int(WV_HOP * sr)
    f0, vf, vp = librosa.pyin(v16.astype(np.float32), fmin=75.0, fmax=1100.0, sr=sr, frame_length=1024, hop_length=hop,
                              fill_na=np.nan)
    rms = librosa.feature.rms(y=v16, frame_length=640, hop_length=hop)[0][:len(f0)]
    if len(rms) < len(f0):
        rms = np.pad(rms, (0, len(f0) - len(rms)))
    db = 20 * np.log10(rms / (np.percentile(rms, 95) + 1e-9) + 1e-6)
    db = uniform_filter1d(db, 5)
    on = librosa.onset.onset_strength(y=v16, sr=sr, hop_length=hop, n_mels=64, fmax=8000)[:len(f0)]
    if len(on) < len(f0):
        on = np.pad(on, (0, len(f0) - len(on)))
    on = on / (float(np.median(on[on > 0])) + 1e-9)
    midi = np.where(np.isfinite(f0), librosa.hz_to_midi(np.where(np.isfinite(f0), f0, 1.0)), np.nan)
    return dict(f0=f0, midi=midi, db=db, on=on, n=len(f0))


def _wv_pitch(midi, a, b):
    seg = midi[max(0, a):max(0, b)]
    seg = seg[np.isfinite(seg)]
    return float(np.median(seg)) if len(seg) >= 3 else None


WV_REF_SPEED = 0.85      # every WV_* duration (and the planner's shot limits) was calibrated on the SLOWED 0.85 song
                         # (_sX2, 3e, 3g approved). 2026-10-20 (owner on 3h, the REGULAR version: "after 15 s the timing is
                         # 1-2 s off"): at speed 1.0 the same syllables are 15 % shorter (0.44-0.57 s), fell under WV_MIN
                         # 0.60 and the chained "join the next" merge moved every wave start ONE SYLLABLE early (16.85 for
                         # "You" 17.21, 17.74 for "slow" 18.18, 19.69 for "time" 20.24; "hour" 27.80 joined "den" as a
                         # tail). Faster versions are analysed at the reference tempo (tempo_scale).


def tempo_scale(speed):
    """output seconds per reference second for a song version played at `speed` (x the original): 0.85 / speed when the
    version is faster than the calibration tempo, else 1.0 (slower versions keep the approved constants)"""
    try:
        r = WV_REF_SPEED / float(speed)
    except (TypeError, ValueError, ZeroDivisionError):
        return 1.0
    return round(r, 4) if r < 0.97 else 1.0


def _ref_tempo_stem(path, t0, dur, r, threads=2):
    """the Demucs vocal stem of [t0, t0 + dur] of `path` played 1 / r times slower (the MIX resampled first, pitch follows,
    exactly like song_fx.make_version: the approved slowed analysis separated the slowed mix, and Demucs on the regular mix
    gives a few dB different syllable levels, e.g. "hour" 27.8 s -16.6 vs -10.7 dB)"""
    import hashlib
    import tempfile
    import soundfile as sf
    import lyrics as LY
    from fractions import Fraction
    from scipy.signal import resample_poly
    st = os.stat(path)
    key = hashlib.sha1(f'{os.path.abspath(path)}|{st.st_size}|{int(st.st_mtime)}|{t0:.3f}|{dur:.3f}|{r:.4f}'.encode()).hexdigest()[:16]
    wp = os.path.join(tempfile.gettempdir(), f'wv-reftempo-{key}.wav')
    if not os.path.exists(wp):
        y = LY.load_audio(path, t0, t0 + dur, 44100, 2).astype(np.float64)
        fr_ = Fraction(1.0 / r).limit_denominator(400)
        y = resample_poly(y, fr_.numerator, fr_.denominator, axis=-1)
        y *= 0.89 / max(1e-9, float(np.max(np.abs(y))))
        sf.write(wp + '.tmp.wav', y.T.astype(np.float32), 44100)
        os.replace(wp + '.tmp.wav', wp)
    return LY.vocal_stem(wp, 0.0, dur / r, threads=threads)


def waves(path, t0=0.0, dur=None, threads=2, F=None, v16=None, speed=None):
    """Momentum waves of the sung line in [t0, t0 + dur] of `path` (seconds from t0).
    -> dict(waves=[dict(a (start), p (loudest point), s (sustain end: last frame within 6 dB of the peak), b (end),
    strength (0-3), why)], starts, dur, F (features)). why: phrase (after silence), valley (deep dip), note (dip + pitch
    change), step (legato pitch step with an onset), release (a long hold drops into a quieter tail).
    speed = the version's playback speed (x the original song): a version faster than WV_REF_SPEED is analysed on the
    vocal stem of its mix resampled to the reference tempo and the times are mapped back (tempo_scale), so a regular-speed song gets
    the same waves as its approved slowed version."""
    if dur is None and v16 is None:
        dur = len(load(path, t0, None, sr=8000)) / 8000.0
    r = tempo_scale(speed) if speed is not None and F is None and v16 is None else 1.0
    if v16 is None and r >= 1.0:
        import lyrics as LY
        v16 = LY.vocal_stem(path, float(t0), float(t0) + float(dur), threads=threads)
    if r < 1.0:
        Wr = waves(path, t0, dur, threads=threads, v16=_ref_tempo_stem(path, float(t0), float(dur), r, threads))
        for x in Wr['waves']:
            for k in ('a', 'p', 's', 'b', 'len'):
                if x.get(k) is not None:
                    x[k] = round(float(x[k]) * r, 3)
        Wr['starts'] = [(x['a'], x['strength'], x['why']) for x in Wr['waves']]
        Wr['dur'] = round(Wr['dur'] * r, 3)
        Wr['F'] = dict(Wr['F'], hop=WV_HOP * r)
        Wr['tscale'] = r
        return Wr
    if F is None:
        F = _wv_features(v16)
    db, midi, on, n = F['db'], F['midi'], F['on'], F['n']
    fr = 1.0 / WV_HOP
    D = n * WV_HOP
    act = db >= WV_ACT_DB
    for a, b in _runs(~act):                       # bridge breaks shorter than a breath
        if b - a < int(WV_GAP * fr) and a > 0 and b < n:
            act[a:b] = True
    runs = [(a, b) for a, b in _runs(act) if b - a >= int(0.12 * fr)]
    from scipy.signal import find_peaks
    bounds = []                                    # (frame, strength, why)
    for ra, rb in runs:
        gap = ra - max([b for a, b in runs if b <= ra] or [-999])
        bounds.append((ra, 3.0 if gap * WV_HOP >= 0.25 else 2.5, 'phrase'))
        seg = db[ra:rb]
        vi, _ = find_peaks(-seg, distance=int(0.08 * fr))
        w6 = int(0.6 * fr)
        for i in vi:
            g = ra + int(i)
            if g - ra < int(0.15 * fr) or rb - g < int(0.15 * fr):
                continue
            depth = min(float(db[max(ra, g - w6):g].max()), float(db[g:min(rb, g + w6)].max())) - float(db[g])
            pl = _wv_pitch(midi, g - 40, g - 8)
            pr = _wv_pitch(midi, g + 8, g + 40)
            step = abs(pr - pl) if pl is not None and pr is not None else None
            onp = float(on[g - 3:g + 8].max())
            # the same held note across the dip: same pitch both sides AND the pitch stays there through the dip (a fall
            # and re-attack on the same pitch is a new syllable: 3h's 17.64 s, -3 st inside the dip)
            mid_ = midi[max(0, g - 12):g + 7]
            mid_ = mid_[np.isfinite(mid_)]
            steady = pl is None or not len(mid_) or float(np.max(np.abs(mid_ - pl))) <= 1.5
            # ... and the voice stays VOICED through the dip: a held vowel's tremolo / breath keeps its pitch, a consonant
            # between two words does not (3i "down" -> "time" 20.2 s: unvoiced 0.25 s, pyin's octave slip made both sides
            # read the same pitch)
            steady = steady and float(np.isfinite(midi[max(0, g - 12):g + 7]).mean()) >= 0.6
            same_note = step is not None and step <= WV_SAME and onp < 2.0 and depth < 10.0 and steady
            if depth >= WV_DEEP and not same_note:
                bounds.append((g, 2.5 if depth >= 12 else 2.0, 'valley'))
            elif depth >= WV_DIP and step is not None and step >= WV_STEP:
                bounds.append((g, 2.0, 'note'))
            elif depth >= WV_STEP_DIP and step is not None and step >= WV_STEP and onp >= 2.5:
                bounds.append((g, 1.5, 'step'))
    # legato word changes: two stable sung pitches (>= 0.25 s within +-0.4 st) a step >= WV_LEGATO apart with a vocal
    # onset >= 2 x median between them (_sX2's "den" -> "hour": -1.1 st, no dip)
    segs = []
    i = 0
    while i < n:
        if not np.isfinite(midi[i]):
            i += 1
            continue
        j = i
        while j + 1 < n and np.isfinite(midi[j + 1]) and abs(midi[j + 1] - np.median(midi[i:j + 1])) <= 0.4:
            j += 1
        if j - i + 1 >= int(0.25 * fr):
            segs.append((i, j + 1, float(np.median(midi[i:j + 1]))))
        i = j + 1
    for (a1, b1, m1), (a2, b2, m2) in zip(segs[:-1], segs[1:]):
        if a2 - b1 <= int(0.2 * fr) and abs(m2 - m1) >= WV_LEGATO and act[b1:a2 + 1].all():
            lo_, hi_ = max(0, b1 - 5), min(n, a2 + 6)
            k = lo_ + int(np.argmax(on[lo_:hi_]))
            if float(on[k]) >= 2.0:
                bounds.append((k, 1.5, 'legato'))
    bounds.sort()
    # a weak pitch change (step / legato) within 0.4 s after a real attack is that attack's scoop, not a new word
    strong_ = [g for g, s_, w in bounds if s_ >= 2.0]
    bounds = [z for z in bounds if z[1] >= 2.0 or not any(0 < z[0] - g <= int(0.4 * fr) for g in strong_)]
    # one consonant / breath gives several candidates: chain them (gaps < WV_CLUSTER) and start the wave on the strongest
    # vocal onset of the chain (the attack itself)
    bb, cl = [], []
    for c in bounds + [(10 ** 9, 0, '')]:
        if cl and c[0] - cl[-1][0] >= int(WV_CLUSTER * fr):
            # (a phrase run starts on its breath / consonant: its attack follows within 0.45 s)
            ext = 45 if any(w == 'phrase' for _, _, w in cl) else 9
            # a legato scoop right after a dip belongs to the dip's attack (3e "time": dip 23.55, scoop 23.9)
            cz = [z for z in cl if z[2] != 'legato'] or cl
            lo_, hi_ = max(0, cz[0][0] - 5), min(n, cz[-1][0] + ext)
            if ext == 9:
                # a long fade has several shallow minima: the attack follows the DEEPEST one (3g's "gol" at 30.2 s,
                # not an early wobble of the fading "your")
                deep = min((z[0] for z in cz), key=lambda g_: float(db[g_]))
                lo_, hi_ = max(0, deep - 3), min(n, deep + 20)
            k = lo_ + int(np.argmax(on[lo_:hi_]))
            if ext == 9:
                # a near-tie (within 10 %) between the consonant at the valley's bottom and the vowel climbing out of it,
                # on the SAME note (a re-articulated syllable: no pitch change marks the consonant): the later one is the
                # attack heard (3i "den" 26.69 vs 26.74 s, on 2.1 vs 2.0); a new note keeps its consonant (3g "slow")
                tie = [j for j in range(k + 1, hi_) if on[j] >= 0.9 * on[k] and db[j] > db[k] + 3.0]
                pb_ = _wv_pitch(midi, deep - 25, deep - 5)
                pa_ = _wv_pitch(midi, tie[0], tie[0] + 12) if tie else None
                if tie and pb_ is not None and pa_ is not None and abs(pa_ - pb_) <= 1.0:
                    k = tie[0]
            top = max(cl, key=lambda z: z[1])
            bb.append((k, top[1], top[2]))
            cl = []
        cl.append(c)
    # releases: a long sustained wave whose level drops >= WV_RELEASE and goes on quieter (>= 0.4 s still sung)
    ends = [g for g, _, _ in bb[1:]] + [n]
    rel = []
    q = int(0.35 * fr)
    for (g, s_, w), e in zip(bb, ends):
        rb = min(e, max([b for a, b in runs if a <= g] or [n]))
        best = None
        for i in range(g + int(WV_RELEASE_HOLD * fr), rb - int(0.4 * fr)):
            drop = float(db[i - q:i].mean() - db[i + 5:i + 5 + q].mean())
            if drop >= WV_RELEASE and (best is None or drop > best[1]):
                best = (i, drop)
        if best is not None:
            i = best[0]
            j0 = max(g, i - 25)
            d1 = np.diff(db[j0:i + 25])                # the frame where the level actually falls
            i = j0 + int(np.argmin(d1)) + 1 if len(d1) else i
            rel.append((i, 2.0, 'release'))
    bb = sorted(bb + rel)
    W = []
    for k, (g, s_, w) in enumerate(bb):
        e = bb[k + 1][0] if k + 1 < len(bb) else n
        seg_act = np.where(act[g:e])[0]
        b = g + (int(seg_act[-1]) + 1 if len(seg_act) else 1)
        p = g + int(np.argmax(db[g:b])) if b > g else g
        pk = float(db[p])
        sus = np.where(db[g:b] >= pk - 6.0)[0]
        pm = _wv_pitch(midi, g, b)
        W.append(dict(a=round(g * WV_HOP, 3), p=round(p * WV_HOP, 3),
                      s=round((g + int(sus[-1])) * WV_HOP, 3) if len(sus) else round(p * WV_HOP, 3),
                      b=round(b * WV_HOP, 3), strength=s_, why=w, midi=None if pm is None else round(pm, 1),
                      peak_db=round(pk, 1),
                      dip=round(float(db[max(0, g - 40):g + 1].max() - db[max(0, g - 15):g + 10].min()), 1) if g > 0 else 99.0))
    # merge: a wave shorter than WV_MIN joins the next (its start stays: the pick-up leads that wave), unless a phrase
    # gap follows it
    out = []
    for x in W:
        if x['why'] == 'release' and (x['b'] - x['a']) < WV_MIN and out:
            # a short quiet tail after a hold is the end of that hold, not the pick-up of the next wave: it joins the hold
            # (3g: the fading "your" at 29.7 s took the start of "gol" at 30.2 s)
            out[-1] = dict(out[-1], b=x['b'])
            out[-1]['_short'] = (out[-1]['b'] - out[-1]['a']) < WV_MIN
            continue
        if out and out[-1]['_short']:
            pv = out.pop()
            if (len(out) and pv['why'] not in ('phrase',) and x['dip'] >= pv['dip'] + 3.0 and pv['a'] - out[-1]['b'] < 0.15):
                # the short piece is the TAIL of the wave before it (a shallow dip into it, a deep one after it: 3g's
                # falling "slo-ow" 21.95 s), not the pick-up of the next one
                out[-1] = dict(out[-1], b=pv['b'])
                x['_short'] = (x['b'] - x['a']) < WV_MIN
                out.append(x)
                continue
            if (len(out) and pv['why'] not in ('phrase',) and pv['a'] - out[-1]['b'] < 0.15 and None not in
                    (pv['midi'], out[-1]['midi'], x['midi']) and abs(pv['midi'] - out[-1]['midi']) <= 1.0
                    and abs(x['midi'] - pv['midi']) >= 1.5):
                # the short piece sings the note of the wave BEFORE it and the next wave moves to another note: it is that
                # word's re-articulated tail ("dow-own" 19.7 s before "time" 20.2 s in 3i), not a pick-up of the next word
                out[-1] = dict(out[-1], b=pv['b'])
                x['_short'] = (x['b'] - x['a']) < WV_MIN
                out.append(x)
                continue
            if x['a'] - pv['b'] < 0.15 and x['why'] != 'phrase':
                x = dict(x, a=pv['a'], strength=max(pv['strength'], x['strength']), why=pv['why'] + '>' + x['why'])
            else:
                out.append(pv)
        x['_short'] = (x['b'] - x['a']) < WV_MIN
        out.append(x)
    out = [x for x in out if x['b'] - x['a'] >= 0.05]
    for x in out:
        x.pop('_short', None)
        x['len'] = round(x['b'] - x['a'], 3)
        if x['peak_db'] < -15.0:                   # a faint line (a fading tail, a far backing voice): a weak wave
            x['strength'] = round(x['strength'] * 0.5, 2)
    return dict(waves=out, starts=[(x['a'], x['strength'], x['why']) for x in out], dur=round(D, 3), F=F)


HT_SIM = 0.80            # hold tail: the harmony (mix chroma) stays this close (cosine) to the held note's
HT_BREAK = 0.40          # ... and breaks only when it leaves for this long (s)
HT_LOUD = -15.0          # a wave this loud (dB re the stem's p95) is sung; under it = a fading / faint tail
HT_LINE_END = 1.0        # a line has ENDED when no loud wave starts this long after the held wave's sustain end
OUTRO_DROP = 12.0        # outro: the low band (40-160 Hz) falls this far and stays down to the end (the band drops out)


def _chroma(path, t0, dur, hop_s=0.1):
    import librosa
    y = load(path, t0, dur, sr=22050).astype(np.float32)
    yh = librosa.effects.harmonic(y)
    C = librosa.feature.chroma_cqt(y=yh, sr=22050, hop_length=int(hop_s * 22050))
    return C / (np.linalg.norm(C, axis=0, keepdims=True) + 1e-9)


def hold_tails(path, t0, dur, Wv, from_t=0.0, C=None):
    """Owner 2026-10-20 on 3i ("30 s it transitions but it shouldn't, it is on the hoouuurrr hold"): a sung note held until it
    fades out is ONE wave including its decay tail. A loud wave whose line ENDS (no loud wave starts within HT_LINE_END s
    of its sustain end) holds while the harmony of the mix stays on it (chroma cosine >= HT_SIM to the held part; it breaks
    after HT_BREAK s away) and until a new SUNG line (a loud wave sustained >= 0.7 s, or followed by another loud wave
    within 1.2 s). 3i: "hour" 27.77 s, the E chord rings to 34.4 s; the faint echoes and the short loud "oh" at 30.9 s are
    inside it. -> [dict(a, s, end, absorbed [wave starts])] (seconds from t0)"""
    ws = [x for x in Wv['waves'] if x['a'] >= from_t]
    if C is None:
        C = _chroma(path, t0, dur)
    hop = 0.1
    nC = C.shape[1]
    out = []
    loud = [x for x in ws if x['peak_db'] >= HT_LOUD]
    for k, w in enumerate(ws):
        if w['peak_db'] < HT_LOUD or w['strength'] < 2.0:
            continue
        s_ = float(w.get('s') or w['b'])
        if any(s_ < x['a'] <= s_ + HT_LINE_END for x in loud):
            continue                                   # the line goes on: no tail
        i0, i1 = int((w['a'] + 0.1) / hop), max(int((w['a'] + 0.1) / hop) + 2, int(s_ / hop))
        if i1 >= nC:
            continue
        ref = C[:, i0:i1].mean(axis=1)
        ref /= np.linalg.norm(ref) + 1e-9
        sim = ref @ C
        from scipy.ndimage import uniform_filter1d
        sim = uniform_filter1d(sim, 5)
        end, j, run = None, int(s_ / hop), 0
        while j < nC:
            run = run + 1 if sim[j] < HT_SIM else 0
            if run * hop >= HT_BREAK:
                end = (j - run + 1) * hop
                break
            j += 1
        end = float(dur) if end is None else end
        # a new sung line inside ends the tail at its start
        nxt = [x for x in ws[k + 1:] if s_ < x['a'] < end]
        for m, x in enumerate(nxt):
            if x['peak_db'] < HT_LOUD:
                continue
            sus = float(x.get('s') or x['b']) - x['a']
            follow = any(y['peak_db'] >= HT_LOUD and 0 < y['a'] - x['a'] <= 1.2 for y in nxt[m + 1:] + [z for z in ws if z['a'] >= end])
            if sus >= 0.7 or follow:
                end = x['a']
                break
        if end - s_ >= 0.6:
            out.append(dict(a=w['a'], s=round(s_, 3), end=round(end, 3),
                            absorbed=[x['a'] for x in ws[k + 1:] if w['a'] < x['a'] < end]))
    return out


def outro_start(path, t0, dur, search=12.0):
    """Owner 2026-10-20 ("the violin part: just end with that one clip till the end"): the final section = the band drops
    out (the low band 40-160 Hz falls >= OUTRO_DROP dB against the 1.5 s before and stays down to the end) in the last
    `search` s, >= 2 s before the end. -> seconds from t0 or None"""
    import librosa
    y = load(path, t0, dur, sr=8000).astype(np.float32)
    S = np.abs(librosa.stft(y, n_fft=1024, hop_length=400))
    f = librosa.fft_frequencies(sr=8000, n_fft=1024)
    lo = 20 * np.log10(np.sqrt((S[(f >= 40) & (f <= 160)] ** 2).mean(axis=0)) + 1e-6)
    hop = 400 / 8000.0
    n = len(lo)
    best = None
    for i in range(int(max(1.5, float(dur) - search) / hop), int((float(dur) - 2.0) / hop)):
        before = float(np.mean(lo[max(0, i - int(1.5 / hop)):i]))
        after = lo[i:max(i + 1, n - int(0.5 / hop))]
        if before - float(np.percentile(after, 90)) >= OUTRO_DROP:
            best = i * hop
            break
    return None if best is None else round(best, 3)


def wave_of(Wv, t):
    """the wave sounding at t (its start <= t < the next start, and t before its end + 50 ms), else None"""
    ws = Wv['waves']
    for k, x in enumerate(ws):
        nx = ws[k + 1]['a'] if k + 1 < len(ws) else 9e9
        if x['a'] <= t < min(nx, x['b'] + 0.05):
            return x
    return None


def plot_waves(Wv, cuts, out, title='', mix=None, extra=None):
    """timeline PNG: mix waveform, vocal level, vocal pitch, waves shaded (alternating), wave starts, cuts, 1 s labels;
    extra = [(t, label, colour)] marks (a judge's flags)"""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    F = Wv['F']
    D = Wv['dur']
    th = np.arange(F['n']) * F.get('hop', WV_HOP)
    fig, ax = plt.subplots(3, 1, figsize=(max(16, D * 0.75), 8), sharex=True,
                           gridspec_kw=dict(height_ratios=[0.8, 1, 1.2]))
    if mix is not None:
        tm = np.arange(len(mix)) / 16000.0
        st = max(1, len(mix) // 30000)
        ax[0].plot(tm[::st], mix[::st], lw=0.3, color='#555')
    ax[0].set_ylabel('mix')
    ax[1].plot(th, F['db'], color='#c33', lw=0.8)
    ax[1].set_ylim(-45, 3)
    ax[1].set_ylabel('vocal dB')
    ax[2].plot(th, F['midi'], '.', ms=1.6, color='#1f5fbf')
    ax[2].set_ylabel('vocal pitch')
    top = float(np.nanmax(F['midi'])) if np.isfinite(F['midi']).any() else 70.0
    cols = ['#9ecae1', '#fdd0a2']
    for k, x in enumerate(Wv['waves']):
        for a_ in ax[1:]:
            a_.axvspan(x['a'], x['b'], color=cols[k % 2], alpha=0.45, lw=0)
        ax[2].text(x['a'], top, f"{x['why'].split('>')[-1][:4]} {x['strength']:.1f}", fontsize=7, rotation=90, va='top')
        ax[1].axvline(x['a'], color='#225', lw=0.8, ls=':')
    y0 = ax[0].get_ylim()[1]
    for c in cuts:
        for a_ in ax:
            a_.axvline(c, color='#090', lw=1.6)
        ax[0].text(c, y0 * 0.95, f'{c:.2f}', fontsize=7, color='#060', rotation=90, va='top')
    for t_, lab, col in (extra or []):
        for a_ in ax:
            a_.axvline(t_, color=col, lw=2.2, ls='--')
        ax[1].text(t_, -43, lab, fontsize=8, color=col, rotation=90, va='bottom')
    ax[2].set_xticks(np.arange(0, D + 0.01, 1.0))
    ax[2].set_xticks(np.arange(0, D + 0.01, 0.25), minor=True)
    for a_ in ax:
        a_.grid(axis='x', which='major', alpha=0.35)
    ax[2].set_xlim(0, D)
    ax[0].set_title(title)
    fig.tight_layout()
    fig.savefig(out, dpi=72)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('audio')
    ap.add_argument('--t0', type=float, default=0.0)
    ap.add_argument('--dur', type=float)
    ap.add_argument('--cuts')
    ap.add_argument('--no-vocals', action='store_true')
    ap.add_argument('--holds', action='store_true', help='+ sung notes / holds / forbidden intervals / per-cut hold report')
    ap.add_argument('--png', help='with --holds: the cut / hold plot')
    a = ap.parse_args()
    M = moments(a.audio, a.t0, a.dur, vocals=not a.no_vocals)
    out = dict(M)
    if a.cuts:
        rows, st = align([float(x) for x in a.cuts.split(',')], M)
        out['cut_rows'], out['cut_stats'] = rows, st
    if a.holds:
        H = holds(a.audio, a.t0, a.dur, M=M)
        out.update(notes=H['notes'], inst=H['inst'], forbid=H['forbid'])
        if a.cuts:
            cc = [float(x) for x in a.cuts.split(',')]
            out['hold_rows'] = cut_report(cc, H)
            out['hold_null'] = dict(time=hold_null(H, cc[0], H['dur']),
                                    beats_lead=hold_null(H, cc[0], H['dur'], [b - LEAD for b in M['beats']]))
        if a.png:
            plot(H, [float(x) for x in a.cuts.split(',')] if a.cuts else [], a.png, os.path.basename(a.audio))
    print(json.dumps(out, indent=1))


if __name__ == '__main__':
    main()

