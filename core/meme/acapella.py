#!/usr/bin/env python3
"""
Acapella engine for the "<song> acapella" meme shorts.

    python core/meme/acapella.py --audio song.mp3 --out-dir work/ [--start SEC]
        [--length 20] [--heatmap heatmap.json] [--threads N] [--debug]

Picks a ~20 s hook (--start, else the yt-dlp "most replayed" heatmap, else the
loudest vocal stretch), snaps it to a downbeat, pulls the real lead vocal out
with Demucs (htdemucs, CPU, that window only) and stacks our own mouth-made
layers on it, one more per section:

    vocal  ->  + hummed "doom" bass  ->  + beatbox  ->  + ooh/aah harmony

Bass, beatbox and harmony are synthesised here (glottal-pulse harmonics shaped
by vowel formants, filtered noise bursts for the mouth drums); nothing is
sampled from other recordings. The vocal is used as separated: no pitch,
speed or timing changes.

Writes acapella.wav (stereo, about -14 LUFS, true peak under -1 dBTP), the
vocal/bass/beatbox/harmony.wav stems (dry, at mix level) and timeline.json.
The last stdout line is JSON:
    {"ok": true, "mix": ..., "stems": {...}, "timeline": ..., "bpm": ..., "duration": ...}

Install (CPU only; torch first so pip doesn't pull the CUDA build):
    pip install --break-system-packages torch --index-url https://download.pytorch.org/whl/cpu
    pip install --break-system-packages demucs librosa
The htdemucs weights (~80 MB) download from the HuggingFace hub on first use.
"""
import argparse
import json
import math
import os
import subprocess
import sys
import time
import wave

import numpy as np
from scipy import signal
from scipy.ndimage import minimum_filter1d, uniform_filter1d

SR = 44100
ASR = 22050          # analysis rate
HOP = 512
CTRL = 64            # control-rate step for formant/gain curves
NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']


def log(*a):
    print(*a, file=sys.stderr, flush=True)


# ---------------------------------------------------------------- audio io

def load_audio(path):
    """Any audio file -> float32 (2, n) at SR via ffmpeg (mono is duplicated)."""
    r = subprocess.run(['ffmpeg', '-v', 'error', '-i', path, '-vn', '-ac', '2', '-ar', str(SR), '-f', 'f32le', '-'],
                       capture_output=True, check=True)
    return np.frombuffer(r.stdout, dtype=np.float32).reshape(-1, 2).T.astype(np.float64)


def write_wav(path, x):
    x = np.atleast_2d(x)
    data = np.round(np.clip(x.T, -1, 1) * 32767).astype('<i2')
    with wave.open(path, 'wb') as w:
        w.setnchannels(x.shape[0])
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(data.tobytes())


def _to_asr(x):
    x = np.atleast_2d(x).mean(axis=0)
    return signal.resample_poly(x, 1, 2).astype(np.float32)


# ---------------------------------------------------------------- loudness

def _kweight():
    # BS.1770 K-weighting at any rate (RBJ biquads fitted to the 48 kHz spec)
    A = 10 ** (3.99984385397 / 40)
    w = 2 * np.pi * 1681.9744509555319 / SR
    c, al = np.cos(w), np.sin(w) / (2 * 0.7071752369554193)
    sa = 2 * np.sqrt(A) * al
    b1 = [A * ((A + 1) + (A - 1) * c + sa), -2 * A * ((A - 1) + (A + 1) * c), A * ((A + 1) + (A - 1) * c - sa)]
    a1 = [(A + 1) - (A - 1) * c + sa, 2 * ((A - 1) - (A + 1) * c), (A + 1) - (A - 1) * c - sa]
    w = 2 * np.pi * 38.13547087613982 / SR
    c, al = np.cos(w), np.sin(w) / (2 * 0.5003270373253953)
    b2 = [(1 + c) / 2, -(1 + c), (1 + c) / 2]
    a2 = [1 + al, -2 * c, 1 - al]
    return np.vstack([signal.tf2sos(b1, a1), signal.tf2sos(b2, a2)])


K_SOS = _kweight()


def lufs(x):
    """Integrated loudness (BS.1770-4, gated) of a (ch, n) or (n,) signal."""
    z = signal.sosfilt(K_SOS, np.atleast_2d(x), axis=-1)
    blk, step = int(0.4 * SR), int(0.1 * SR)
    if z.shape[1] < blk:
        z = np.pad(z, ((0, 0), (0, blk - z.shape[1])))
    p2 = np.cumsum(np.pad(z ** 2, ((0, 0), (1, 0))), axis=1)
    st = np.arange(0, z.shape[1] - blk + 1, step)
    ms = ((p2[:, st + blk] - p2[:, st]) / blk).sum(axis=0)
    ms = ms[-0.691 + 10 * np.log10(ms + 1e-20) > -70]
    if not len(ms):
        return -70.0
    rel = -0.691 + 10 * np.log10(ms.mean()) - 10
    ms = ms[-0.691 + 10 * np.log10(ms) > rel]
    return float(-0.691 + 10 * np.log10(ms.mean()))


def true_peak(x):
    return float(np.abs(signal.resample_poly(np.atleast_2d(x), 4, 1, axis=-1)).max())


def limit(x, ceiling, look=0.004, release=0.08):
    """Look-ahead peak limiter on 4x-oversampled peaks; never lets a peak through."""
    n = x.shape[1]
    up = np.abs(signal.resample_poly(x, 4, 1, axis=-1)).max(axis=0)
    pk = up[:n * 4].reshape(n, 4).max(axis=1)
    need = np.minimum(1.0, ceiling / np.maximum(pk, 1e-9))
    la = max(2, int(look * SR))
    g = minimum_filter1d(need, 2 * la + 1)
    # instant attack, exponential release, at a coarse control rate
    B = 32
    nb = -(-n // B)
    gb = np.pad(g, (0, nb * B - n), constant_values=1.0).reshape(nb, B).min(axis=1)
    r = math.exp(-B / (release * SR))
    out = np.empty(nb)
    cur = 1.0
    for i, v in enumerate(gb):
        cur = min(v, 1.0 - (1.0 - cur) * r)
        out[i] = cur
    # a moving average no wider than the look-ahead keeps every sample under `need`
    gs = uniform_filter1d(np.repeat(out, B)[:n], la)
    return x * gs


def master(x, target=-14.0, ceiling_db=-1.5):
    ceil = 10 ** (ceiling_db / 20)
    g = 10 ** ((target - lufs(x)) / 20)
    y = x
    for _ in range(5):
        y = limit(x * g, ceil)
        d = target - lufs(y)
        if abs(d) < 0.05:
            break
        g *= 10 ** (d / 20)
    tp = true_peak(y)
    if tp > ceil:
        y *= ceil / tp
    return y, g


# ---------------------------------------------------------------- song analysis

def _z(v):
    v = np.asarray(v, dtype=float)
    return (v - v.mean()) / (v.std() + 1e-9)


def _seg_mean(F, times, bounds):
    """Mean of feature frames F (d, T) inside each [bounds[i], bounds[i+1])."""
    idx = np.searchsorted(times, bounds)
    return np.stack([F[:, a:max(b, a + 1)].mean(axis=1) for a, b in zip(idx[:-1], idx[1:])], axis=1)


def track_beats(x):
    """Rough beat times over the whole song (the grid is refined later on the drum stem)."""
    import librosa
    oenv = librosa.onset.onset_strength(y=_to_asr(x), sr=ASR, hop_length=HOP)
    _, bf = librosa.beat.beat_track(onset_envelope=oenv, sr=ASR, hop_length=HOP, trim=False)
    return librosa.frames_to_time(bf, sr=ASR, hop_length=HOP)


def song_features(x, bt):
    """Per-beat vocal-ness, loudness and repetition over the whole song."""
    import librosa
    mid = _to_asr(x)
    out = {'beats': bt}
    if len(bt) < 32:
        return out
    side = signal.resample_poly((x[0] - x[1]) / 2, 1, 2).astype(np.float32)
    M = np.abs(librosa.stft(mid, n_fft=2048, hop_length=HOP))
    S = np.abs(librosa.stft(side, n_fft=2048, hop_length=HOP))
    fr = librosa.fft_frequencies(sr=ASR, n_fft=2048)
    band = (fr > 250) & (fr < 4000)
    harm, _ = librosa.decompose.hpss(M[band])
    # vocals sit in the centre: weight the harmonic mid-band by how centre-panned it is
    centre = M[band] ** 2 / (M[band] ** 2 + S[band] ** 2 + 1e-10)
    voc = 10 * np.log10((harm ** 2 * centre ** 2).sum(axis=0) + 1e-10)
    loud = 10 * np.log10((M ** 2).sum(axis=0) + 1e-10)
    times = librosa.frames_to_time(np.arange(M.shape[1]), sr=ASR, hop_length=HOP)
    bounds = np.concatenate([bt, [bt[-1] + np.median(np.diff(bt))]])
    out['voc'] = _seg_mean(voc[None], times, bounds)[0]
    out['loud'] = _seg_mean(loud[None], times, bounds)[0]
    chroma = _seg_mean(librosa.feature.chroma_stft(S=M ** 2, sr=ASR), times, bounds)
    chroma /= chroma.max(axis=0, keepdims=True) + 1e-9
    C = librosa.feature.stack_memory(chroma, n_steps=4, mode='edge')
    R = librosa.segment.recurrence_matrix(C, width=3, mode='affinity', sym=True)
    R = librosa.segment.path_enhance(R, 9)
    out['rep'] = np.asarray(R).sum(axis=1)  # choruses repeat, so they score high
    return out


def load_heatmap(path):
    with open(path, encoding='utf-8') as f:
        d = json.load(f)
    if isinstance(d, dict):
        d = d.get('heatmap') or ((d.get('entries') or [{}])[0] or {}).get('heatmap') or []
    return [(float(m['start_time']), float(m['end_time']), float(m['value'])) for m in d or []
            if m and m.get('value') is not None]


def heat_peak(heat, dur, L):
    """Centre of the most replayed stretch (heatmap smoothed over ~L/2), ignoring the intro spike."""
    t = np.arange(0, dur, 0.1)
    h = np.zeros(len(t))
    for a, b, v in heat:
        h[(t >= a) & (t < b)] = v
    h = uniform_filter1d(h, max(1, int(min(L / 2, 8.0) / 0.1)))
    ok = (t >= max(6.0, 0.04 * dur) + L / 2) & (t <= dur - L / 2 - 1.0)
    if not ok.any() or h[ok].max() <= 0:
        return None
    return float(t[ok][np.argmax(h[ok])])


def pick_window(song, n_beats, dur, L, centre=None):
    """
    Coarse window start on the loudest, most vocal, most repeated (chorus)
    stretch; with `centre` (heatmap peak) only within L/4 of centring on it.
    """
    bt = song['beats']
    if 'voc' not in song:
        return centre - L / 2 if centre is not None else max(0.0, (dur - L) / 2)
    zv = _z(song['voc'])
    f = zv + 0.6 * _z(song['loud']) + 0.5 * _z(song['rep'])
    lo = max(6.0, 0.04 * dur)
    cand, vs = [], []
    for i in range(len(bt) - n_beats):
        s = bt[i]
        if s < lo or s + L > dur - 1.0:
            continue
        # the first section is vocal alone, so it must actually have singing
        v = f[i:i + n_beats].mean() + 0.5 * zv[i:i + n_beats // 4].mean()
        if centre is not None:
            d = abs(s + L / 2 - centre) / (L / 4)
            if d > 1:
                continue
            v = 0.5 * zv[i:i + n_beats // 4].mean() - d
        cand.append(s)
        vs.append(v)
    if not cand:
        return centre - L / 2 if centre is not None else 0.0
    return float(cand[int(np.argmax(vs))])


def pick_bars(bar, length, dur):
    """Whole bars near `length` s, preferring counts that split into 4 equal sections."""
    best, cost = 4, 1e9
    for n in range(2, 65):
        L = n * bar
        if L > dur - 1.0 and n > 2:
            break
        if not (0.8 * length <= L <= 1.1 * length):
            continue
        c = abs(L - length) + (2.5 if n % 4 else 0) + (1.5 if n % 2 else 0)
        if c < cost:
            best, cost = n, c
    if cost == 1e9:
        best = min(int(round(length / bar)), int((dur - 1.0) // bar))
    return max(4, best)


def _onset_env(y, hop=64):
    # short analysis window, so envelope peaks sit within a few ms of the real attack
    import librosa
    env = librosa.onset.onset_strength(y=y, sr=ASR, hop_length=hop, n_fft=512, n_mels=40)
    return env, np.arange(len(env)) * hop / ASR


def fit_grid(y, p0, t0, t1):
    """
    Constant-tempo beat grid over region-relative [t0, t1]: the period (within
    +-4% of the tracker's) and phase whose beats land on the most attack energy
    in y. librosa's tracker quantises the period to whole frames (129.2 instead
    of 127 BPM drifts ~0.3 s over 20 s); pop records sit on a click, so one
    straight grid fits.
    """
    env, et = _onset_env(_to_asr(y))
    env = uniform_filter1d(env, 3) / (env.max() + 1e-9)
    span = t1 - t0
    best = (-1.0, p0, 0.0)
    for P in p0 * np.linspace(0.96, 1.04, 161):
        k = np.arange(int(span / P) + 1)
        ph = np.arange(0, P, 0.002)
        sc = np.interp(t0 + ph[:, None] + k[None] * P, et, env).mean(axis=1)
        i = int(np.argmax(sc))
        if sc[i] > best[0]:
            best = (float(sc[i]), float(P), float(ph[i]))
    _, P, ph = best
    # fine phase pass at 0.25 ms
    k = np.arange(int(span / P) + 1)
    fine = ph + np.arange(-0.002, 0.00201, 0.00025)
    sc = np.interp(t0 + fine[:, None] + k[None] * P, et, env).mean(axis=1)
    ph = float(fine[int(np.argmax(sc))]) - 0.004  # envelope peaks ~3-4 ms after an attack starts
    grid = t0 + ph + np.arange(-int(t0 / P) - 1, int(span / P) + 2) * P
    grid = grid[(grid >= 0) & (grid <= t0 + span + P)]
    # how far the real attacks sit from the grid (reported, not used)
    near = [et[max(0, j - 8):j + 9][np.argmax(env[max(0, j - 8):j + 9])] - g
            for g, j in zip(grid, np.searchsorted(et, grid)) if j < len(et)]
    return grid, P, float(np.median(np.abs(near))) if near else 0.0


def downbeat_phase(stems, rs, grid):
    """Which beat in 4 is the one: chords change and kicks land on it, snares on 2 and 4."""
    import librosa
    harm = _to_asr(stems['bass'] + stems['other'])
    chroma = librosa.feature.chroma_cqt(y=harm, sr=ASR, hop_length=HOP)
    ct = librosa.frames_to_time(np.arange(chroma.shape[1]), sr=ASR, hop_length=HOP)
    g = grid - rs
    g = g[(g >= 0) & (g < ct[-1])]
    cb = _seg_mean(chroma, ct, g)                      # per beat, len(g) - 1 columns
    nb = cb.shape[1]
    hc = np.zeros(nb)
    for i in range(2, nb - 2):
        a, b = cb[:, i - 2:i].mean(axis=1), cb[:, i:i + 2].mean(axis=1)
        hc[i] = 1 - a @ b / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9)
    ms = librosa.onset.onset_strength_multi(y=_to_asr(stems['drums']), sr=ASR, hop_length=128, n_fft=1024,
                                            channels=[0, 6, 40, 128])
    mt = np.arange(ms.shape[1]) * 128 / ASR
    bo, bt = _onset_env(_to_asr(stems['bass']), hop=128)

    def at(env, tt):
        return np.array([env[max(0, j - 2):j + 3].max() if j < len(env) else 0.0
                         for j in np.searchsorted(tt, g[:nb])])

    feat = _z(hc) + 0.5 * _z(at(ms[0], mt)) - 0.5 * _z(at(ms[2], mt)) + 0.4 * _z(at(bo, bt))
    first = int(np.searchsorted(grid - rs, 0))
    sc = [feat[p::4].mean() for p in range(4)]
    return (first + int(np.argmax(sc))) % 4


# ---------------------------------------------------------------- chords

TEMPLATES = []
for _r in range(12):
    for _q, _iv in (('', (0, 4, 7)), ('m', (0, 3, 7))):
        _t = np.zeros(12)
        _t[[(_r + i) % 12 for i in _iv]] = 1
        TEMPLATES.append((_r, _q, _t / np.linalg.norm(_t)))


def chords_per_beat(stems, vocal, rs, beats, bar_pos):
    """Major/minor triad per beat: chroma template match (bass stem votes for the root), Viterbi-smoothed."""
    import librosa
    harm = _to_asr(stems['bass'] + stems['other'])
    ca = librosa.feature.chroma_cqt(y=harm, sr=ASR, hop_length=HOP, bins_per_octave=36)
    cbass = librosa.feature.chroma_cqt(y=_to_asr(stems['bass']), sr=ASR, hop_length=HOP,
                                       fmin=librosa.note_to_hz('C1'), n_octaves=4)
    cv = librosa.feature.chroma_cqt(y=_to_asr(vocal), sr=ASR, hop_length=HOP)
    ct = librosa.frames_to_time(np.arange(ca.shape[1]), sr=ASR, hop_length=HOP)
    b = beats - rs
    A, Bs, V = (_seg_mean(c, ct, b) for c in (ca, cbass, cv))
    T = np.array([t for _, _, t in TEMPLATES])
    roots = np.array([r for r, _, _ in TEMPLATES])

    def cos(C):
        return (T @ C) / (np.linalg.norm(C, axis=0, keepdims=True) + 1e-9)

    E = cos(A) + 0.6 * Bs[roots] / (Bs.max(axis=0, keepdims=True) + 1e-9) + 0.15 * cos(V)
    E = 8.0 * E.T                                          # (beats, 24) log-score
    # switching chords is cheap on the one, dearer mid-bar, dearest on 2 and 4
    pen = {0: 1.0, 2: 2.0, 1: 4.0, 3: 4.0}
    D = E[0].copy()
    back = np.zeros(E.shape, dtype=int)
    for i in range(1, len(E)):
        j = int(np.argmax(D))
        move = D[j] - pen[bar_pos[i] % 4]
        stay = D >= move
        back[i] = np.where(stay, np.arange(24), j)
        D = np.where(stay, D, move) + E[i]
    path = [int(np.argmax(D))]
    for i in range(len(E) - 1, 0, -1):
        path.append(back[i][path[-1]])
    path = path[::-1]
    return [(TEMPLATES[k][0], TEMPLATES[k][1]) for k in path]


def chord_name(c):
    return NOTE_NAMES[c[0]] + c[1]


def lead_pitch(vocal):
    """Median MIDI pitch of the sung lead, to keep the harmony just under it."""
    import librosa
    y = _to_asr(vocal)
    f0 = librosa.yin(y, fmin=80, fmax=1000, sr=ASR, frame_length=2048, hop_length=HOP)
    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=HOP)[0][:len(f0)]
    ok = rms > 0.25 * np.percentile(rms, 95)
    if ok.sum() < 10:
        return 64.0
    return float(np.median(librosa.hz_to_midi(f0[ok])))


# ---------------------------------------------------------------- voice synthesis

# (F1..F4 Hz, bandwidths) for an adult male; scaled up for the alto harmony
VOWELS = {
    'u': [(300, 60), (870, 80), (2240, 120), (3300, 200)],
    'o': [(470, 70), (820, 80), (2600, 140), (3400, 220)],
    'a': [(730, 80), (1090, 90), (2440, 130), (3400, 250)],
    'd': [(220, 70), (1700, 120), (2600, 160), (3400, 250)],   # alveolar locus for "d" onsets
    'm': [(250, 60), (1100, 200), (2300, 250), (3300, 300)],
}


def _fm(vowel, scale=1.0):
    return np.array([(f * scale, b * scale) for f, b in VOWELS[vowel]] + [(4500, 350)])


def _tract(f, fm):
    F, B = fm[:, :, 0], fm[:, :, 1]
    f = f[:, None]
    return np.prod(F ** 2 / np.sqrt((F ** 2 - f ** 2) ** 2 + (f * B) ** 2), axis=1)


def _nasal(f):
    # closed-lips hum: nasal pole ~250 Hz, anti-resonance ~1.2 kHz, little above
    p = 250.0 ** 2 / np.sqrt((250.0 ** 2 - f ** 2) ** 2 + (f * 60) ** 2)
    z = np.sqrt((1200.0 ** 2 - f ** 2) ** 2 + (f * 200) ** 2) / 1200.0 ** 2
    return p * z / (1 + (f / 900.0) ** 4)


def _drift(n, rng, cents, rate=3.0):
    """Slow random pitch wander, like a singer's unsteady tone."""
    k = max(2, int(n / SR * rate) + 2)
    return np.interp(np.linspace(0, k - 1, n), np.arange(k), rng.standard_normal(k) * cents)


_SOS = {}


def _filt(x, lo=None, hi=None, order=2):
    key = (lo, hi, order)
    if key not in _SOS:
        if lo and hi:
            _SOS[key] = signal.butter(order, [lo, hi], 'bandpass', fs=SR, output='sos')
        elif lo:
            _SOS[key] = signal.butter(order, lo, 'highpass', fs=SR, output='sos')
        else:
            _SOS[key] = signal.butter(order, hi, 'lowpass', fs=SR, output='sos')
    return signal.sosfilt(_SOS[key], x, axis=-1)


def voice(f0, fm, amp, rng, nasal=None, tilt=1.2, breath=0.03, top=5000.0, rand_phase=True):
    """
    Glottal-pulse harmonics through a cascade of formant resonators.
    f0, amp: per sample; fm: (n_ctrl, k, 2) formants per CTRL samples;
    nasal: per-ctrl 0..1 blend toward a closed-mouth hum.
    """
    n = len(f0)
    idx = np.arange(0, n, CTRL)
    f0c = f0[idx]
    ph = 2 * np.pi * np.cumsum(f0) / SR
    H = int(top / max(float(f0.min()), 30.0))
    G = np.empty((H, len(idx)))
    for h in range(1, H + 1):
        fh = h * f0c
        g = h ** -tilt * _tract(fh, fm) * np.clip((top - fh) / (0.25 * top), 0, 1)
        if nasal is not None:
            g *= (1 - nasal) + nasal * _nasal(fh)
        G[h - 1] = g
    G /= np.sqrt((G ** 2).sum(axis=0)) + 1e-12       # same loudness whatever the vowel
    theta = rng.uniform(0, 2 * np.pi, H) if rand_phase else np.zeros(H)
    s = np.arange(n)
    out = np.zeros(n)
    for h in range(H):
        out += np.interp(s, idx, G[h]) * np.sin((h + 1) * ph + theta[h])
    if breath:
        nz = _filt(_filt(rng.standard_normal(n), 300, 3500), hi=4000)
        nz /= nz.std() + 1e-9
        # aspiration puffs out with each glottal opening, so it fuses with the tone
        out += breath * nz * (0.4 + 0.6 * (0.5 + 0.5 * np.cos(ph)))
    return out * amp


def _interp_fm(a, b, w):
    w = np.clip(w, 0, 1)[:, None, None]
    return a[None] * (1 - w) + b[None] * w


def bass_note(midi, dur, rng, syl='doom', vel=1.0):
    """Sung bass: "d" burst into "oo", closing to a hummed "m"."""
    f = _hz(midi)
    n = int((dur + 0.09) * SR)
    t = np.arange(n) / SR
    cents = (-55 * np.exp(-t / 0.03) + _drift(n, rng, 4)
             + 9 * np.clip((t - 0.25) / 0.25, 0, 1) * np.sin(2 * np.pi * 5.2 * t + rng.uniform(0, 6.3))
             - 30 * np.clip((t - dur) / 0.09, 0, 1))
    f0 = f * 2 ** (cents / 1200)
    tc = t[::CTRL]
    if syl == 'doom':
        close = np.clip((tc - 0.5 * dur) / (0.25 * dur + 0.03), 0, 1)
    else:  # "dm": straight into the hum
        close = np.clip((tc - 0.035) / 0.05, 0, 1)
    fm = _interp_fm(_fm('d', 0.95), _fm('u', 0.95), tc / 0.045)
    fm = fm * (1 - close[:, None, None]) + _fm('m', 0.95)[None] * close[:, None, None]
    amp = (np.clip((t - 0.006) / 0.016, 0, 1) ** 1.5 * np.exp(-t / (1.2 + dur))
           * np.where(t > dur, np.exp(-(t - dur) / 0.035), 1.0))
    amp *= 1 - 0.3 * np.interp(t, tc, close)
    y = voice(f0, fm, amp, rng, nasal=close, tilt=1.15, breath=0.012, rand_phase=False)
    burst = _filt(rng.standard_normal(int(0.012 * SR)), 2500, 5500) * np.exp(-np.arange(int(0.012 * SR)) / (0.003 * SR))
    y[:len(burst)] += 0.10 * burst
    return vel * y


def pad_note(midi, dur, vowel, rng, detune=0.0, scale=1.15):
    """Soft backing "ooh"/"aah": slow swell, delayed vibrato, breathy."""
    n = int((dur + 0.3) * SR)
    t = np.arange(n) / SR
    rate = 4.9 + rng.uniform(0, 0.8)
    cents = (detune - 28 * np.exp(-t / 0.07) + _drift(n, rng, 5)
             + 15 * np.clip((t - 0.3) / 0.5, 0, 1) * np.sin(2 * np.pi * rate * t + rng.uniform(0, 6.3)))
    f0 = _hz(midi) * 2 ** (cents / 1200)
    tc = t[::CTRL]
    if vowel == 'a':  # open from "o" into "aah"
        fm = _interp_fm(_fm('o', scale), _fm('a', scale), tc / 0.15)
    else:
        fm = np.repeat(_fm('u', scale)[None], len(tc), axis=0)
    amp = ((1 - np.exp(-t / 0.09)) * (1 + 0.12 * np.sin(np.pi * np.clip(t / max(dur, 0.1), 0, 1)))
           * np.where(t > dur, np.exp(-(t - dur) / 0.1), 1.0))
    return voice(f0, fm, amp, rng, tilt=1.3, breath=0.04, top=5500.0)


# ---------------------------------------------------------------- mouth drums

def _decay(n, tau):
    return np.exp(-np.arange(n) / (tau * SR))


def bb_kick(rng, vel=1.0):
    """Lip-bass "b": pitch-dropping thump, a lip pop and a puff of air."""
    n = int(0.16 * SR)
    t = np.arange(n) / SR
    f = 60 + rng.uniform(100, 130) * np.exp(-t / 0.018)
    ph = 2 * np.pi * np.cumsum(f) / SR
    body = (np.sin(ph) + 0.25 * np.sin(2 * ph)) * np.exp(-t / rng.uniform(0.038, 0.048)) * np.clip(t / 0.0015, 0, 1)
    pop = _filt(rng.standard_normal(n), hi=1800) * _decay(n, 0.0015)
    puff = _filt(rng.standard_normal(n), hi=350) * _decay(n, 0.025)
    y = body + 0.6 * pop + 0.35 * puff / (np.abs(puff).max() + 1e-9)
    y *= np.clip((n - np.arange(n)) / (0.02 * SR), 0, 1)
    return vel * np.tanh(1.2 * y) / np.tanh(1.2)


def bb_snare(rng, vel=1.0):
    """Classic "pf" snare: lip burst straight into an "f" fricative."""
    n = int(0.2 * SR)
    t = np.arange(n) / SR
    click = rng.standard_normal(n) * _decay(n, 0.0008)
    fric = _filt(rng.standard_normal(n), 1200 * rng.uniform(0.9, 1.1), 9000)
    fric = fric + 0.8 * _filt(fric, 2800, 4500)
    fric /= np.abs(fric).max() + 1e-9
    env = np.clip(t / 0.003, 0, 1) * np.exp(-t / rng.uniform(0.04, 0.055))
    body = np.sin(2 * np.pi * rng.uniform(180, 210) * t) * _decay(n, 0.018)
    return vel * (0.5 * click + 1.2 * fric * env + 0.45 * body)


def bb_k(rng, vel=1.0):
    """Short "k" rim click, used for ghost notes and fills."""
    n = int(0.09 * SR)
    t = np.arange(n) / SR
    burst = _filt(rng.standard_normal(n), 1500, 4200)
    burst /= np.abs(burst).max() + 1e-9
    tone = np.sin(2 * np.pi * rng.uniform(1450, 1750) * t) * _decay(n, 0.004)
    return vel * (burst * _decay(n, 0.011) + 0.5 * tone)


def bb_hat(rng, vel=1.0, open_=False):
    """"ts" hi-hat: tongue click then a sibilant hiss (longer "tss" when open)."""
    n = int((0.28 if open_ else 0.07) * SR)
    t = np.arange(n) / SR
    click = _filt(rng.standard_normal(n), lo=3000) * _decay(n, 0.0007)
    s = _filt(rng.standard_normal(n), 5000 * rng.uniform(0.95, 1.05), 11000)
    s = s + 0.7 * _filt(s, 6500, 8500)
    s /= np.abs(s).max() + 1e-9
    env = np.clip(t / 0.004, 0, 1) * np.exp(-t / ((0.09 if open_ else 0.022) * rng.uniform(0.9, 1.1)))
    return vel * (0.4 * click + s * env)


BB = {'kick': bb_kick, 'snare': bb_snare, 'k': bb_k, 'hat': bb_hat,
      'open_hat': lambda rng, vel: bb_hat(rng, vel, open_=True)}

# 16th-note steps per bar; a human beatboxer makes one sound at a time
GROOVES = {
    'normal': ([(0, 'kick'), (2, 'hat'), (4, 'snare'), (6, 'hat'), (8, 'kick'), (10, 'hat'), (12, 'snare'), (14, 'hat')],
               [(0, 'kick'), (2, 'hat'), (4, 'snare'), (6, 'hat'), (7, 'kick'), (10, 'kick'), (12, 'snare'), (14, 'hat'),
                (15, 'k')]),
    'slow': ([(0, 'kick'), (2, 'hat'), (3, 'hat'), (4, 'snare'), (6, 'hat'), (8, 'kick'), (10, 'hat'), (11, 'hat'),
              (12, 'snare'), (14, 'hat'), (15, 'k')],
             [(0, 'kick'), (2, 'hat'), (3, 'hat'), (4, 'snare'), (6, 'hat'), (7, 'kick'), (10, 'kick'), (12, 'snare'),
              (14, 'hat'), (15, 'hat')]),
    'fast': ([(0, 'kick'), (2, 'hat'), (4, 'hat'), (6, 'kick'), (8, 'snare'), (10, 'hat'), (12, 'hat'), (14, 'hat')],
             [(0, 'kick'), (2, 'hat'), (4, 'hat'), (6, 'kick'), (8, 'snare'), (10, 'hat'), (11, 'k'), (12, 'kick'),
              (14, 'hat')]),
}
VEL = {'kick': 1.0, 'snare': 0.9, 'k': 0.45, 'hat': 0.35, 'open_hat': 0.4}

# bass rhythm in 8th-note steps: (step, length in 8ths, syllable, octave up)
BASSLINES = {
    'normal': ([(0, 3, 'doom', 0), (3, 1, 'dm', 0), (4, 2, 'doom', 0), (6, 2, 'doom', 0)],
               [(0, 3, 'doom', 0), (3, 1, 'dm', 0), (4, 2, 'doom', 0), (6, 1, 'dm', 0), (7, 1, 'dm', 12)]),
    'slow': ([(0, 2, 'doom', 0), (2, 1, 'dm', 0), (3, 1, 'dm', 0), (4, 2, 'doom', 0), (6, 1, 'dm', 0), (7, 1, 'dm', 0)],
             [(0, 2, 'doom', 0), (2, 1, 'dm', 0), (3, 1, 'dm', 0), (4, 2, 'doom', 0), (6, 1, 'dm', 12), (7, 1, 'dm', 0)]),
    'fast': ([(0, 3, 'doom', 0), (3, 1, 'dm', 0), (4, 3, 'doom', 0), (7, 1, 'dm', 0)],
             [(0, 3, 'doom', 0), (3, 1, 'dm', 0), (4, 2, 'doom', 0), (6, 1, 'dm', 12), (7, 1, 'dm', 0)]),
}


def _hz(m):
    return 440.0 * 2 ** ((m - 69) / 12)


def _note_name(m):
    return NOTE_NAMES[int(m) % 12] + str(int(m) // 12 - 1)


def _near(pc, target, lo, hi):
    return min((m for m in range(lo, hi + 1) if m % 12 == pc), key=lambda m: abs(m - target))


def _put(buf, sig, t):
    i = int(round(t * SR))
    if i < 0:
        sig, i = sig[-i:], 0
    j = min(buf.shape[-1], i + sig.shape[-1])
    if j > i:
        buf[..., i:j] += sig[..., :j - i]


def _pan(sig, p):
    a = (p + 1) * np.pi / 4
    return np.stack([np.cos(a) * sig, np.sin(a) * sig]) * math.sqrt(2)


def _room_ir(seconds=1.3, rt60=0.95, seed=7):
    """Synthetic stereo room: decaying noise, highs dying first, 14 ms pre-delay."""
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    t = np.arange(n) / SR
    chans = []
    for _ in range(2):
        nz = rng.standard_normal(n)
        ir = (_filt(nz, hi=500) * np.exp(-6.91 * t / (rt60 * 1.1)) + _filt(nz, 500, 4000) * np.exp(-6.91 * t / rt60)
              + 0.5 * _filt(nz, lo=4000) * np.exp(-6.91 * t / (rt60 * 0.45)))
        ir *= np.clip((t - 0.014) / 0.02, 0, 1)
        chans.append(ir)
    ir = np.array(chans)
    return ir / np.sqrt((ir ** 2).sum() / 2)


# ---------------------------------------------------------------- arrangement

def arrange(beats, n_bars, sizes, chords, bpm, centre, n_out, seed=0):
    """Synthesise bass, beatbox and harmony on the beat grid (output time). Returns stems and events."""
    rng = np.random.default_rng(seed)
    kb = np.arange(len(beats))

    def at(pos):  # beat position (float) -> seconds, extrapolating past the grid
        per = beats[-1] - beats[-2]
        return float(np.interp(pos, kb, beats)) if pos <= kb[-1] else float(beats[-1] + (pos - kb[-1]) * per)

    def chord_at(pos):
        return chords[min(int(pos), len(chords) - 1)]

    style = 'slow' if bpm < 100 else 'fast' if bpm >= 150 else 'normal'
    starts = np.concatenate([[0], np.cumsum(sizes)])      # section start bars
    bass = np.zeros(n_out)
    bbox = np.zeros(n_out)
    harm = np.zeros((2, n_out))
    ev = {'bass': [], 'beatbox': [], 'harmony': []}

    # bass: from section 2 on
    for bar in range(starts[1], n_bars):
        pat = BASSLINES[style][bar % 2]
        for step, ln, syl, up in pat:
            pos = bar * 4 + step / 2
            root = chord_at(pos)[0]
            if up and step >= 6:  # octave-up pickup leads into the next bar's chord
                root = chord_at(bar * 4 + 4)[0]
            midi = 36 + root + up
            t0, t1 = at(pos), at(pos + ln / 2)
            dur = max(0.08, (t1 - t0) * (0.88 if ln > 1 else 0.8))
            vel = (1.0 if step in (0, 4) else 0.85) * rng.uniform(0.93, 1.0)
            _put(bass, bass_note(midi, dur, rng, syl, vel), t0)
            ev['bass'].append({'t': round(t0, 4), 'dur': round(dur, 4), 'midi': midi, 'note': _note_name(midi),
                               'syllable': syl})

    # beatbox: from section 3 on, with a "k k pf" pickup in the bar before
    hits = []
    if starts[2] > 0:
        b = starts[2] - 1
        hits += [(b * 4 + 3.0, 'k', 0.45), (b * 4 + 3.25, 'k', 0.5), (b * 4 + 3.5, 'snare', 0.6)]
    for bar in range(starts[2], n_bars):
        pat = GROOVES[style][bar % 2]
        last = bar == n_bars - 1
        for step, kind in pat:
            if last and step >= 14:
                kind = 'open_hat'
            vel = VEL[kind] * (1.1 if kind == 'hat' and step % 4 == 0 else 1.0)
            hits.append((bar * 4 + step / 4, kind, vel))
    end_pos = n_bars * 4
    hits.append((end_pos, 'kick', 1.0))                     # the closing "button"
    for pos, kind, vel in hits:
        t = at(pos) + rng.normal(0, 0.0015)                 # a little human timing, well under 5 ms
        _put(bbox, BB[kind](rng, vel * rng.uniform(0.9, 1.05)), t)
        ev['beatbox'].append({'t': round(at(pos), 4), 'kind': kind, 'vel': round(vel, 2)})

    # harmony: section 4, 3rd and 5th of each chord, two slightly detuned singers per part
    lo, hi = int(centre) - 5, int(centre) + 7
    prev = [centre - 2, centre + 2]
    arts, last_c, last_bar = [], None, -9
    for bar in range(starts[3], n_bars):
        for half in (0, 2):
            pos = bar * 4 + half
            c = chord_at(pos)
            if c != last_c or (half == 0 and bar - last_bar >= 2):
                arts.append((pos, c))
                last_c = c
                last_bar = bar if half == 0 else last_bar
    arts.append((end_pos, chord_at(end_pos)))
    for i, (pos, c) in enumerate(arts):
        is_end = i == len(arts) - 1
        end = at(pos + 1.0) if is_end else at(arts[i + 1][0])
        t0 = at(pos)
        dur = max(0.15, end - t0 - (0.0 if is_end else 0.04))
        third, fifth = (c[0] + (4 if c[1] == '' else 3)) % 12, (c[0] + 7) % 12
        v1, v2 = _near(third, prev[0], lo, hi), _near(fifth, prev[1], lo, hi)
        if abs(v1 - v2) < 3:
            v2 = v2 + 12 if v2 + 12 <= hi + 5 else v2 - 12
        prev = [v1, v2]
        vowel = 'a' if (i % 2 or is_end) else 'u'
        for part, (m, pans) in enumerate(((v1, (-0.5, -0.2)), (v2, (0.5, 0.2)))):
            for d, (det, off) in enumerate(((-6.0, 0.0), (6.0, 0.016))):
                s = pad_note(m, dur, vowel, rng, detune=det + rng.normal(0, 2))
                _put(harm, _pan(0.5 * s, pans[d]), t0 + off + abs(rng.normal(0, 0.004)))
        ev['harmony'].append({'t': round(t0, 4), 'dur': round(dur, 4), 'midi': [v1, v2],
                              'notes': [_note_name(v1), _note_name(v2)], 'vowel': 'aah' if vowel == 'a' else 'ooh'})

    # closing "doom" on the button
    c = chord_at(end_pos)
    t0 = at(end_pos)
    dur = max(0.2, at(end_pos + 1.0) - t0)
    _put(bass, bass_note(36 + c[0], dur, rng, 'doom', 1.0), t0)
    ev['bass'].append({'t': round(t0, 4), 'dur': round(dur, 4), 'midi': 36 + c[0], 'note': _note_name(36 + c[0]),
                       'syllable': 'doom'})
    return {'bass': bass, 'beatbox': bbox, 'harmony': harm}, ev


# ---------------------------------------------------------------- separation

def separate(seg, threads):
    import torch
    from demucs.apply import apply_model
    from demucs.pretrained import get_model
    torch.set_num_threads(threads)
    model = get_model('htdemucs')
    wav = torch.from_numpy(seg.astype(np.float32))
    ref = wav.mean(0)
    mu, sd = ref.mean(), ref.std() + 1e-8
    with torch.no_grad():
        out = apply_model(model, ((wav - mu) / sd)[None], shifts=0, split=True, overlap=0.25, progress=False)[0]
    out = (out * sd + mu).numpy().astype(np.float64)
    return dict(zip(model.sources, out))


# ---------------------------------------------------------------- main flow

def _quiet_point(env, et, a, b):
    sel = (et >= a) & (et <= b)
    if not sel.any():
        return b
    return float(et[sel][np.argmin(env[sel])])


def run(audio, out_dir, start=None, length=20.0, heatmap=None, threads=None, debug=False):
    import librosa
    t_start = time.time()
    threads = threads or os.cpu_count() or 2
    os.makedirs(out_dir, exist_ok=True)
    x = load_audio(audio)
    dur = x.shape[1] / SR
    heat = load_heatmap(heatmap) if heatmap and start is None else None
    bt = track_beats(x)
    if len(bt) < 8:
        raise RuntimeError('no beat found')
    per0 = float(np.median(np.diff(bt)))
    n_bars = pick_bars(4 * per0, length, dur)
    L = n_bars * 4 * per0
    tp = heat_peak(heat, dur, L) if heat else None
    if start is not None:
        s0, how = float(start), 'start'
    else:
        s0 = pick_window(song_features(x, bt), n_bars * 4, dur, L, centre=tp)
        how = 'heatmap' if tp is not None else 'energy (no heatmap data)' if heatmap else 'energy'
    s0 = float(np.clip(s0, 0, max(0.0, dur - L - 0.5)))
    log(f'[acapella] {dur:.0f}s song, ~{60 / per0:.1f} bpm, {n_bars} bars from ~{s0:.1f}s ({how}) '
        f'[{time.time() - t_start:.1f}s]')

    # separate only the window plus room to snap to a downbeat, slide onto the singing and ring out
    rs = max(0.0, s0 - L / 4 - 3 * per0 - 1.0)
    re_ = min(dur, s0 + L + L / 3 + 8 * per0 + 1.5)
    stems = separate(x[:, int(rs * SR):int(re_ * SR)], threads)
    log(f'[acapella] demucs done on {re_ - rs:.1f}s [{time.time() - t_start:.1f}s]')

    sel = bt[(bt >= rs) & (bt <= re_)]
    p0 = float(np.median(np.diff(sel))) if len(sel) > 4 else per0
    grid, per, resid = fit_grid(stems['drums'] + 0.3 * stems['bass'] + 0.2 * stems['other'], p0, 0.0, re_ - rs)
    grid = grid + rs
    bpm = 60.0 / per
    phase = downbeat_phase(stems, rs, grid)

    def starts(nb, room):
        # downbeats with the window, one more bar (closing hit) and optionally a pickup beat inside the region
        return [k for k in range(phase, len(grid), 4)
                if grid[k] - room >= rs and k + nb * 4 + 4 < len(grid) and grid[k + nb * 4] <= re_]

    k0s = starts(n_bars, per) or starts(n_bars, 0.0)
    while not k0s and n_bars > 4:  # song too short around here: shrink the window
        n_bars -= 1
        k0s = starts(n_bars, 0.0)
    if not k0s:
        raise RuntimeError('song too short for a 4-bar window')
    sizes = [n_bars // 4 + (1 if i >= 4 - n_bars % 4 else 0) for i in range(4)]

    voc = stems['vocals']
    vm = librosa.feature.rms(y=_to_asr(voc), frame_length=441, hop_length=220)[0]
    vt = np.arange(len(vm)) * 220 / ASR + rs
    act = np.percentile(vm, 80) + 1e-9

    def sung(k, bars):  # share of the first `bars` bars from downbeat k that has singing in it
        span = (vt >= grid[k]) & (vt < grid[k + bars * 4])
        return float((vm[span] > 0.2 * act).mean()) if span.any() else 0.0

    # nearest downbeat, but slide (-L/4..+L/3) until the vocal-alone section 1 is actually sung
    k0s.sort(key=lambda k: abs(grid[k] - s0))
    near = [k for k in k0s if -L / 4 - 0.05 <= grid[k] - s0 <= L / 3 + 0.05] or k0s[:1]
    k0 = near[0]
    if start is None:
        k0 = next((k for k in near if sung(k, 1) >= 0.3 and sung(k, sizes[0]) >= 0.5),
                  max(near, key=lambda k: sung(k, 1) + sung(k, sizes[0])))
    ws, we = float(grid[k0]), float(grid[k0 + n_bars * 4])

    # start on a quiet spot up to a beat early, so a pickup word isn't chopped
    q = _quiet_point(vm, vt, ws - per, ws - 0.02)
    pick = vm[(vt > q) & (vt < ws)]
    pre = min(ws - q, ws - rs) if len(pick) and pick.max() > 0.25 * act else 0.0
    off = ws - pre                                        # source time of output t = 0
    tail = 1.0
    n_out = int((we - off + tail) * SR)
    v_end = _quiet_point(vm, vt, we - 0.05, we + 0.5 * per)

    beats_out = grid[k0:] - off
    beats_out = beats_out[beats_out <= we - off + per * 4 + 1e-6]

    # chords over the window plus one bar (for the closing hit)
    cb = grid[k0:k0 + n_bars * 4 + 5]
    chords = chords_per_beat(stems, voc, rs, cb, np.arange(len(cb) - 1))
    lead = lead_pitch(voc[:, int((ws - rs) * SR):int((we - rs) * SR)])
    centre = float(np.clip(lead - 3, 57, 69))
    layers, ev = arrange(beats_out, n_bars, sizes, chords, bpm, centre, n_out,
                         seed=int(ws * 1000) % 100000)

    # vocal as separated (low rumble filtered), faded at the cut points
    i0 = int(round((off - rs) * SR))
    vocal = np.zeros((2, n_out))
    seg = voc[:, i0:i0 + n_out]
    vocal[:, :seg.shape[1]] = seg
    vocal = _filt(vocal, lo=70)
    t = np.arange(n_out) / SR
    ve = v_end - off
    fade = np.clip(t / 0.012, 0, 1) * np.clip((ve + 0.06 - t) / 0.08, 0, 1)
    vocal *= fade

    # levels relative to the vocal (loudness of each layer where it plays)
    lv = lufs(vocal)
    sec_t = [float(beats_out[b * 4]) if b * 4 < len(beats_out) else we - off
             for b in np.concatenate([[0], np.cumsum(sizes)])]
    sec_t[0] = 0.0
    st = {'vocal': vocal}
    for i, (name, rel) in enumerate((('bass', -6.0), ('beatbox', -5.0), ('harmony', -10.5))):
        s = layers[name]
        cur = lufs(np.atleast_2d(s)[:, int(sec_t[i + 1] * SR):])
        g = 10 ** ((lv + rel - cur) / 20) if cur > -70 else 1.0
        st[name] = s * g if s.ndim == 2 else _pan(s * g, 0.08 if name == 'beatbox' else 0.0)

    dry = sum(st.values())
    sends = {'vocal': 0.10, 'bass': 0.05, 'beatbox': 0.10, 'harmony': 0.35}
    bus = sum(sends[k] * st[k].mean(axis=0) for k in st)
    ir = _room_ir()
    wet = np.stack([signal.fftconvolve(bus, ir[c])[:n_out] for c in range(2)])
    wet = _filt(wet, lo=180)
    mix = dry + wet
    endfade = np.clip((n_out / SR - t) / 0.25, 0, 1)
    mix *= endfade
    out, g = master(mix)
    for k in st:
        st[k] = st[k] * g * endfade

    paths = {'mix': os.path.join(out_dir, 'acapella.wav')}
    write_wav(paths['mix'], out)
    stems_out = {}
    for k in ('vocal', 'bass', 'beatbox', 'harmony'):
        stems_out[k] = os.path.join(out_dir, f'{k}.wav')
        write_wav(stems_out[k], st[k])
    if debug:
        acc = sum(stems[k] for k in ('drums', 'bass', 'other'))[:, i0:i0 + n_out]
        write_wav(os.path.join(out_dir, 'accomp.wav'), acc)
        write_wav(os.path.join(out_dir, 'drums.wav'), stems['drums'][:, i0:i0 + n_out])
        write_wav(os.path.join(out_dir, 'source.wav'), x[:, int(off * SR):int(off * SR) + n_out])

    # timeline for the video editor (all times in output seconds)
    vy = _to_asr(vocal)
    von = librosa.onset.onset_detect(y=vy, sr=ASR, hop_length=128, units='time', backtrack=False)
    ve_env = librosa.feature.rms(y=vy, frame_length=1024, hop_length=128)[0]
    von = [round(float(o), 4) for o in von
           if ve_env[min(len(ve_env) - 1, int(o * ASR / 128))] > 0.1 * np.percentile(ve_env, 95)]
    bars = []
    for b in range(n_bars):
        bc = chords[b * 4:b * 4 + 4]
        name = max(set(bc), key=lambda c: (bc.count(c), -bc.index(c)))
        bars.append({'index': b, 'start': round(float(beats_out[b * 4]), 4),
                     'end': round(float(beats_out[b * 4 + 4]), 4), 'chord': chord_name(name)})
    layer_order = ['vocal', 'bass', 'beatbox', 'harmony']
    sections, b0 = [], 0
    for i, sz in enumerate(sizes):
        sections.append({'index': i + 1, 'start': round(0.0 if i == 0 else float(beats_out[b0 * 4]), 4),
                         'end': round(float(beats_out[(b0 + sz) * 4]), 4), 'bars': [b0, b0 + sz],
                         'layers': layer_order[:i + 1]})
        b0 += sz
    end_hit = round(float(beats_out[n_bars * 4]), 4)
    nb_all = n_bars * 4 + 1
    timeline = {
        'version': 1,
        'source': {'path': os.path.abspath(audio), 'window_start': round(ws, 4), 'window_end': round(we, 4),
                   'offset': round(off, 4), 'picked_by': how},
        'bpm': round(bpm, 2),
        'duration': round(n_out / SR, 4),
        'preroll': round(pre, 4),
        'beats': [round(float(b), 4) for b in beats_out[:nb_all]],
        'downbeats': [round(float(b), 4) for b in beats_out[:nb_all:4]],
        'bars': bars,
        'chords_per_beat': [chord_name(c) for c in chords[:n_bars * 4]],
        'sections': sections,
        'end_hit': end_hit,
        'layers': {
            'vocal': {'file': 'vocal.wav', 'onsets': von},
            'bass': {'file': 'bass.wav', 'notes': ev['bass']},
            'beatbox': {'file': 'beatbox.wav', 'hits': ev['beatbox']},
            'harmony': {'file': 'harmony.wav', 'notes': ev['harmony']},
        },
        'onsets': {'vocal': von, 'bass': [e['t'] for e in ev['bass']],
                   'beatbox': [e['t'] for e in ev['beatbox']], 'harmony': [e['t'] for e in ev['harmony']]},
        'analysis': {'grid_to_attacks_median_ms': round(resid * 1000, 1), 'lead_median_midi': round(lead, 1),
                     'loudness_lufs': round(lufs(out), 2), 'true_peak_dbtp': round(20 * math.log10(true_peak(out)), 2),
                     'seconds': round(time.time() - t_start, 1)},
    }
    paths['timeline'] = os.path.join(out_dir, 'timeline.json')
    with open(paths['timeline'], 'w', encoding='utf-8') as f:
        json.dump(timeline, f, indent=1)
    log(f'[acapella] done: {bpm:.1f} bpm, {n_out / SR:.1f}s, chords {" ".join(b["chord"] for b in bars)} '
        f'[{time.time() - t_start:.1f}s]')
    return {'ok': True, 'mix': os.path.abspath(paths['mix']), 'stems': {k: os.path.abspath(v) for k, v in stems_out.items()},
            'timeline': os.path.abspath(paths['timeline']), 'bpm': round(bpm, 2), 'duration': round(n_out / SR, 3),
            'window_start': round(ws, 3), 'picked_by': how}


def main():
    ap = argparse.ArgumentParser(description='Layered acapella meme audio from a song.')
    ap.add_argument('--audio', required=True)
    ap.add_argument('--out-dir', required=True)
    ap.add_argument('--start', type=float, help='window start in the song (s); snapped to a downbeat')
    ap.add_argument('--length', type=float, default=20.0, help='target length (s), rounded to whole bars')
    ap.add_argument('--heatmap', help='yt-dlp heatmap JSON (list of {start_time, end_time, value}) or -J dump')
    ap.add_argument('--threads', type=int, help='torch threads (default: all cores)')
    ap.add_argument('--debug', action='store_true', help='also write accomp/drums/source windows')
    args = ap.parse_args()
    os.environ.setdefault('HF_HUB_DISABLE_SYMLINKS_WARNING', '1')
    try:
        res = run(args.audio, args.out_dir, args.start, args.length, args.heatmap, args.threads, args.debug)
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(json.dumps({'ok': False, 'reason': f'{type(e).__name__}: {e}'}))
        sys.exit(1)
    print(json.dumps(res, ensure_ascii=False))


if __name__ == '__main__':
    main()
