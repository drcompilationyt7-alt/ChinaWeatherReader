#!/usr/bin/env python3
"""
sfx.py - an ORIGINAL, procedurally synthesised sound-effect library for the edit engine (numpy / scipy only;
no samples, nothing downloaded, nothing copyrighted), plus the music treatments and the clip-audio pass-through.

Every generator is deterministic by `seed`, stereo, 48 kHz by default (pass sr=44100 from the engine) and takes
    dur        seconds
    intensity  0..1   how hard / dense (drive, sub depth, number of partials ...)
    brightness 0..1   where the spectrum sits (0 dark .. 1 airy)
and returns (audio float64 (2, n) peak-normalised to 1.0, sync_s) where sync_s is the point INSIDE the sound that
must land on the picture event: the peak of a whoosh (= the cut), the attack of an impact, the END of a riser or
reverse swell, the first click of a shutter.

    GENERATORS  name -> function          (see VOCABULARY for the recipes in words)
    render_cues(cues, duration, sr, music=None)  -> (2, n) layer, levelled against the music's short-term RMS
    duck / lowpass_sweep / silence_gap / tape_stop      music treatments (in place on a (2, n) array)
    clip_passthrough(seg, sr, gain_db, hp_hz, ceiling_db)  a shot's own sound: high-pass, level, limiter
    limiter(x, sr, ceiling_db)

Mapping of transitions / accents / overlays to sounds PER PRESET lives in presets_sfx (bottom of this file):
    SFX_PROFILES, PRESET_SFX, cues_for_engine(engine, mode).
The measured facts behind the table are in core/edit/refs/audio-sfx.json / .md.

CLI:  python sfx.py --demo out.wav            every sound, 1 s apart
      python sfx.py --one whoosh --dur 0.4 --out w.wav
      python sfx.py --selftest
"""
import argparse
import json
import math
import os
import sys

import numpy as np
from scipy import signal

SR = 48000


# ------------------------------------------------------------------------------------------------ helpers

def _rng(seed):
    return np.random.default_rng(int(seed) & 0x7fffffff)


def _norm(x, peak=1.0):
    m = float(np.max(np.abs(x))) if x.size else 0.0
    return x * (peak / m) if m > 1e-9 else x


def _pan(mono, pos):
    """Equal-power pan; pos scalar or per-sample array in -1 (left) .. +1 (right)."""
    th = (np.clip(pos, -1, 1) + 1) * (math.pi / 4)
    return np.stack([mono * np.cos(th), mono * np.sin(th)])


def _fade(x, sr, a=0.002, b=0.004):
    n = x.shape[-1]
    ka, kb = min(n // 2, max(1, int(a * sr))), min(n // 2, max(1, int(b * sr)))
    x = x.copy()
    x[..., :ka] *= np.linspace(0, 1, ka)
    x[..., -kb:] *= np.linspace(1, 0, kb)
    return x


def _shaped_noise(n, sr, centre_hz, width_oct, rng, tilt_db_oct=0.0):
    """White noise through a TIME-VARYING log-gaussian band (centre_hz: array of n samples or scalar).
    Done in the STFT domain so the sweep is smooth and cheap."""
    nper, hop = 1024, 256
    x = rng.standard_normal(n + nper)
    f, _, Z = signal.stft(x, sr, nperseg=nper, noverlap=nper - hop)
    T = Z.shape[1]
    c = np.broadcast_to(np.asarray(centre_hz, dtype=float), (n,)) if np.ndim(centre_hz) else np.full(n, float(centre_hz))
    w = np.broadcast_to(np.asarray(width_oct, dtype=float), (n,)) if np.ndim(width_oct) else np.full(n, float(width_oct))
    idx = np.clip((np.arange(T) * hop).astype(int), 0, n - 1)
    lf = np.log2(np.maximum(f, 1.0))[:, None]
    g = np.exp(-0.5 * ((lf - np.log2(c[idx])[None]) / w[idx][None]) ** 2)
    if tilt_db_oct:
        g = g * 10 ** (tilt_db_oct * (lf - np.log2(1000.0)) / 20)
    _, y = signal.istft(Z * g, sr, nperseg=nper, noverlap=nper - hop)
    return y[:n]


def _reverb(x, sr, rt60=0.6, mix=0.25, seed=0, bright=0.5):
    """Small synthetic room: exponentially decaying stereo noise IR, low-passed."""
    rng = _rng(seed + 977)
    n = int(rt60 * sr)
    t = np.arange(n) / sr
    ir = rng.standard_normal((2, n)) * np.exp(-6.91 * t / rt60)
    sos = signal.butter(2, 2500 + 9000 * bright, 'lowpass', fs=sr, output='sos')
    ir = signal.sosfilt(sos, ir, axis=1)
    ir /= np.sqrt((ir ** 2).sum(axis=1, keepdims=True)) + 1e-12
    wet = np.stack([signal.fftconvolve(x[i], ir[i])[:x.shape[1] + n] for i in range(2)])
    dry = np.pad(x, ((0, 0), (0, wet.shape[1] - x.shape[1])))
    return dry * (1 - mix) + wet * mix


def limiter(x, sr, ceiling_db=-1.0, lookahead=0.003, release=0.08):
    """Look-ahead peak limiter (linked stereo)."""
    c = 10 ** (ceiling_db / 20)
    pk = np.max(np.abs(x), axis=0)
    la = max(1, int(lookahead * sr))
    from scipy.ndimage import maximum_filter1d
    pk = maximum_filter1d(pk, 2 * la + 1)
    g = np.minimum(1.0, c / np.maximum(pk, 1e-9))
    # smooth release: one-pole on the gain, attack instant (the look-ahead window covers it)
    a = math.exp(-1.0 / (release * sr))
    out = np.empty_like(g)
    cur = 1.0
    # vectorised enough: run only where limiting happens
    if np.all(g >= 1.0):
        return x
    for i in range(len(g)):
        cur = g[i] if g[i] < cur else a * cur + (1 - a) * g[i]
        out[i] = cur
    return x * out


# ------------------------------------------------------------------------------------------------ generators

def whoosh(dur=0.4, intensity=0.6, brightness=0.5, seed=0, sr=SR, direction='lr', peak_pos=0.62):
    """Filtered-noise swell: the band centre sweeps UP into the peak and down after it (a pass-by), the level
    rises exponentially to the peak and falls ~2x faster, the image pans across with a doppler-like skew."""
    rng = _rng(seed)
    n = int(dur * sr)
    t = np.arange(n) / n
    k = np.where(t < peak_pos, t / peak_pos, 1 - (t - peak_pos) / (1 - peak_pos))       # 0..1..0
    lo, hi = 250 * 2 ** (2.0 * brightness), 1800 * 2 ** (2.2 * brightness)
    centre = lo * (hi / lo) ** (k ** 1.4)
    x = _shaped_noise(n, sr, centre, 0.9 + 0.5 * intensity, rng, tilt_db_oct=-1.5)
    # a second, airy layer for brightness
    x += (0.25 + 0.5 * brightness) * _shaped_noise(n, sr, np.minimum(centre * 2.4, 0.35 * sr), 0.7, rng)
    env = np.where(t < peak_pos, np.exp(-5.5 * (1 - t / peak_pos)), np.exp(-7.0 * (t - peak_pos) / (1 - peak_pos)))
    env *= np.clip(t / 0.04, 0, 1) * np.clip((1 - t) / 0.06, 0, 1)
    flutter = 1 + 0.12 * intensity * np.sin(2 * math.pi * (9 + 6 * rng.random()) * t * dur + rng.random() * 6)
    mono = x * env * flutter
    sgn = {'lr': 1.0, 'rl': -1.0}.get(direction, 0.0)
    pos = sgn * 0.6 * np.tanh(3.0 * (t - peak_pos))
    y = _pan(mono, pos)
    return _norm(_fade(y, sr)), peak_pos * dur


def swish(dur=0.18, intensity=0.5, brightness=0.7, seed=0, sr=SR, direction='lr'):
    """A short, bright whoosh for fast whips / slides (peak in the middle)."""
    return whoosh(dur, intensity, min(1.0, brightness + 0.1), seed, sr, direction, peak_pos=0.5)


def impact(dur=0.7, intensity=0.7, brightness=0.4, seed=0, sr=SR):
    """Sub sine drop (95 -> 38 Hz, exponential) + a 'body' thump + a band-passed noise crack; soft-clipped."""
    rng = _rng(seed)
    n = int(dur * sr)
    t = np.arange(n) / sr
    f0, f1 = 80 + 30 * intensity, 36.0
    f = f1 + (f0 - f1) * np.exp(-t / 0.055)
    sub = np.sin(2 * math.pi * np.cumsum(f) / sr) * np.exp(-t / (0.16 + 0.22 * intensity))
    body = np.sin(2 * math.pi * np.cumsum(170 * np.exp(-t / 0.03) + 60) / sr) * np.exp(-t / 0.045)
    crack = _shaped_noise(n, sr, 1500 * 2 ** (2.2 * brightness), 1.3, rng) * np.exp(-t / (0.012 + 0.03 * brightness))
    tail = _shaped_noise(n, sr, 500, 1.4, rng) * np.exp(-t / 0.18) * 0.12
    mono = np.tanh((1.0 * sub + 0.6 * body + (0.35 + 0.5 * brightness) * _norm(crack) + tail) * (1.2 + 1.5 * intensity))
    mono *= np.clip(t / 0.0015, 0, 1)
    y = np.stack([mono, mono])
    side = _shaped_noise(n, sr, 3000, 1.0, _rng(seed + 1)) * np.exp(-t / 0.05) * 0.08
    y[0] += side
    y[1] -= side
    return _norm(_fade(y, sr, 0.0005, 0.03)), 0.0015


def sub_boom(dur=1.4, intensity=0.8, brightness=0.2, seed=0, sr=SR):
    """The cinematic drop hit: a long 60 -> 30 Hz sine with a slow tail and a soft low crack."""
    n = int(dur * sr)
    t = np.arange(n) / sr
    f = 30 + (34 + 20 * intensity) * np.exp(-t / 0.12)
    sub = np.sin(2 * math.pi * np.cumsum(f) / sr) * np.exp(-t / (0.35 + 0.35 * intensity))
    crack = _shaped_noise(n, sr, 300 + 1500 * brightness, 1.5, _rng(seed)) * np.exp(-t / 0.04)
    mono = np.tanh((sub + 0.3 * _norm(crack)) * 1.6) * np.clip(t / 0.002, 0, 1)
    return _norm(_fade(np.stack([mono, mono]), sr, 0.0005, 0.08)), 0.002


def riser(dur=2.0, intensity=0.6, brightness=0.6, seed=0, sr=SR):
    """Noise whose band climbs ~3 octaves + a quiet sine sweep, exponential level, tremolo that speeds up;
    ends hard at dur (sync = the end)."""
    rng = _rng(seed)
    n = int(dur * sr)
    t = np.arange(n) / n
    centre = (400 * 2 ** (1.2 * brightness)) * 2 ** (3.0 * t ** 1.3)
    x = _shaped_noise(n, sr, np.minimum(centre, 0.42 * sr), 0.8, rng)
    tone_f = 220 * 2 ** (2.0 * t ** 1.5)
    tone = np.sin(2 * math.pi * np.cumsum(tone_f) / sr) * 0.18 * intensity
    trem_f = 4 + 20 * t ** 2
    trem = 1 - 0.35 * intensity * (0.5 + 0.5 * np.sin(2 * math.pi * np.cumsum(trem_f) * dur / n))
    env = np.exp(-4.6 * (1 - t)) * np.clip(t / 0.05, 0, 1)
    mono = (x / (np.max(np.abs(x)) + 1e-9) + tone) * env * trem
    width = 0.2 + 0.6 * t
    other = _shaped_noise(n, sr, np.minimum(centre * 1.1, 0.42 * sr), 0.8, _rng(seed + 5))
    other = other / (np.max(np.abs(other)) + 1e-9) * env * trem
    y = np.stack([mono + width * 0.5 * other, mono - width * 0.5 * other])
    y[..., -int(0.004 * sr):] *= np.linspace(1, 0, int(0.004 * sr))
    return _norm(y), dur


def reverse_swell(dur=1.0, intensity=0.5, brightness=0.5, seed=0, sr=SR):
    """A reverberated tonal hit played backwards: it sucks INTO the event (sync = the end)."""
    rng = _rng(seed)
    n = int(dur * sr)
    t = np.arange(n) / sr
    base = 110 * 2 ** (rng.integers(0, 5) / 12)
    parts = sum(np.sin(2 * math.pi * base * m * t + rng.random() * 6) * np.exp(-t / (0.5 * dur / m ** 0.5)) / m
                for m in (1, 2, 3, 4.02, 5.98))
    noise = _shaped_noise(n, sr, 1200 * 2 ** (2 * brightness), 1.2, rng) * np.exp(-t / (0.3 * dur))
    hit = np.stack([parts + 0.5 * _norm(noise)] * 2)
    wet = _reverb(hit, sr, rt60=dur * 0.9, mix=0.7, seed=seed, bright=brightness)[:, :n]
    y = wet[:, ::-1].copy()
    y[..., -int(0.006 * sr):] *= np.linspace(1, 0, int(0.006 * sr))
    return _norm(_fade(y, sr, 0.05, 0.001)), dur


def shutter(dur=0.16, intensity=0.6, brightness=0.6, seed=0, sr=SR):
    """Camera shutter: mirror slap + curtain, two resonant clicks ~55 ms apart, then a tiny mechanical tail."""
    rng = _rng(seed)
    n = int(dur * sr)
    y = np.zeros(n)

    def click(at, f, q, amp, dec):
        m = int(0.06 * sr)
        tt = np.arange(m) / sr
        c = rng.standard_normal(m) * np.exp(-tt / dec)
        sos = signal.butter(2, [f / q, min(f * q, 0.45 * sr)], 'bandpass', fs=sr, output='sos')
        c = signal.sosfilt(sos, c) + 0.5 * np.sin(2 * math.pi * f * tt) * np.exp(-tt / (dec * 0.7))
        i = int(at * sr)
        k = min(m, n - i)
        if k > 0:
            y[i:i + k] += amp * c[:k] / (np.max(np.abs(c)) + 1e-9)

    click(0.002, 1800 + 2500 * brightness, 1.8, 1.0, 0.006)
    click(0.055 + 0.01 * rng.random(), 1200 + 2000 * brightness, 2.0, 0.8, 0.008)
    click(0.085, 600, 1.6, 0.25 * intensity, 0.02)
    return _norm(_fade(np.stack([y, y * 0.92]), sr, 0.0003, 0.01)), 0.002


def click(dur=0.03, intensity=0.5, brightness=0.6, seed=0, sr=SR):
    """UI tick / type-on: one 4 ms resonant click."""
    n = int(dur * sr)
    t = np.arange(n) / sr
    f = 1500 + 4000 * brightness
    mono = (np.sin(2 * math.pi * f * t) + 0.5 * _rng(seed).standard_normal(n)) * np.exp(-t / 0.004)
    return _norm(_fade(np.stack([mono, mono]), sr, 0.0002, 0.003)), 0.0


def paper_slide(dur=0.35, intensity=0.5, brightness=0.6, seed=0, sr=SR, direction='lr'):
    """Photo / card sliding over a surface: high-passed friction noise, granular amplitude, small thud at the stop."""
    rng = _rng(seed)
    n = int(dur * sr)
    t = np.arange(n) / n
    x = _shaped_noise(n, sr, 3500 * 2 ** (1.0 * brightness), 0.9, rng, tilt_db_oct=1.0)
    grains = np.abs(signal.sosfilt(signal.butter(2, 90, 'lowpass', fs=sr, output='sos'), rng.standard_normal(n)))
    grains = 0.35 + 0.65 * grains / (grains.max() + 1e-9)
    env = np.sin(math.pi * np.clip(t / 0.85, 0, 1)) ** 0.7 * (t < 0.85)
    mono = x * grains * env
    stop = int(0.85 * n)
    m = n - stop
    tt = np.arange(m) / sr
    mono[stop:] += 0.5 * intensity * np.max(np.abs(mono)) * np.sin(2 * math.pi * 140 * tt) * np.exp(-tt / 0.02)
    sgn = {'lr': 1.0, 'rl': -1.0}.get(direction, 0.0)
    return _norm(_fade(_pan(mono, sgn * (t * 1.2 - 0.6)), sr)), 0.85 * dur


def glass_shatter(dur=1.1, intensity=0.7, brightness=0.7, seed=0, sr=SR):
    """Crack transient, then 30-60 inharmonic modal partials (1.2-11 kHz, 40-400 ms decays) with scattered onsets
    and a rain of noise grains (falling shards), spread across the stereo field."""
    rng = _rng(seed)
    n = int(dur * sr)
    t = np.arange(n) / sr
    y = np.zeros((2, n))
    crack = _shaped_noise(n, sr, 4500, 1.6, rng) * np.exp(-t / 0.012)
    y += np.stack([crack, crack]) * 1.4 / (np.max(np.abs(crack)) + 1e-9)
    for _ in range(int(30 + 30 * intensity)):
        f = 1200 * 2 ** (rng.random() * (2.2 + 1.0 * brightness))
        on = int(min(n - 1, abs(rng.normal(0, 0.06)) * sr))
        dec = 0.04 + 0.36 * rng.random() ** 2
        m = n - on
        tt = np.arange(m) / sr
        p = np.sin(2 * math.pi * f * tt + rng.random() * 6) * np.exp(-tt / dec) * rng.uniform(0.15, 0.6)
        y[:, on:] += _pan(p, rng.uniform(-0.9, 0.9))
    for _ in range(int(25 + 50 * intensity)):
        on = int(rng.uniform(0.03, 0.85) ** 1.5 * n)
        m = min(n - on, int(rng.uniform(0.003, 0.015) * sr))
        if m < 8:
            continue
        tt = np.arange(m) / sr
        f = rng.uniform(3000, 12000)
        g = np.sin(2 * math.pi * f * tt) * np.exp(-tt / (m / sr / 3)) * rng.uniform(0.1, 0.4) * (1 - on / n)
        y[:, on:on + m] += _pan(g, rng.uniform(-1, 1))
    return _norm(_fade(y, sr, 0.0003, 0.05)), 0.001


def glitch(dur=0.25, intensity=0.6, brightness=0.6, seed=0, sr=SR):
    """Digital stutter: a 1/32-style gate over bit-crushed square + noise bursts, random repeats."""
    rng = _rng(seed)
    n = int(dur * sr)
    step = max(64, int(sr * (0.018 + 0.02 * (1 - intensity))))
    y = np.zeros(n)
    i = 0
    cell = None
    while i < n:
        m = min(step, n - i)
        if cell is None or rng.random() > 0.55:
            tt = np.arange(step) / sr
            f = rng.choice([110, 220, 440, 880, 1760]) * (1 + 2 * brightness * rng.random())
            sq = np.sign(np.sin(2 * math.pi * f * tt))
            nz = rng.standard_normal(step)
            cell = (0.6 * sq + 0.6 * nz * (rng.random() < 0.5)) * (rng.random() < 0.8)
            cell = np.round(cell * 6) / 6
        y[i:i + m] = cell[:m] * np.linspace(1, 0.6, m)
        i += step
    sos = signal.butter(2, [200, min(0.45 * sr, 4000 + 9000 * brightness)], 'bandpass', fs=sr, output='sos')
    y = signal.sosfilt(sos, y)
    w = _rng(seed + 3).uniform(-0.7, 0.7, size=n // step + 2).repeat(step)[:n]
    return _norm(_fade(_pan(y, w), sr, 0.0005, 0.003)), 0.0


def tape_stop_fx(dur=0.5, intensity=0.6, brightness=0.4, seed=0, sr=SR):
    """Stand-alone 'power down': a saw-ish tone + motor noise whose pitch falls to zero (sync = start)."""
    n = int(dur * sr)
    t = np.arange(n) / n
    f = (180 + 240 * brightness) * (1 - t) ** 1.8 + 8
    ph = 2 * math.pi * np.cumsum(f) / sr
    mono = (np.sin(ph) + 0.4 * np.sin(2 * ph) + 0.2 * np.sin(3 * ph)) * (1 - t) ** 0.6
    mono += 0.2 * _shaped_noise(n, sr, 300 + 2000 * (1 - t), 1.0, _rng(seed)) * (1 - t)
    return _norm(_fade(np.stack([mono, mono]), sr, 0.002, 0.02)), 0.0


def heartbeat(dur=0.45, intensity=0.6, brightness=0.2, seed=0, sr=SR):
    """Lub-dub: two 50-60 Hz thumps 0.16 s apart."""
    n = int(dur * sr)
    y = np.zeros(n)
    for at, amp in ((0.0, 1.0), (0.16, 0.7)):
        i = int(at * sr)
        m = n - i
        tt = np.arange(m) / sr
        y[i:] += amp * np.sin(2 * math.pi * (48 + 25 * np.exp(-tt / 0.03)) * tt) * np.exp(-tt / 0.06) * np.clip(tt / 0.004, 0, 1)
    return _norm(_fade(np.stack([y, y]), sr, 0.001, 0.02)), 0.004


def shimmer(dur=0.9, intensity=0.4, brightness=0.8, seed=0, sr=SR):
    """Sparkle for glow / light-leak / heart effects: a cloud of short high sine pings with a soft rise."""
    rng = _rng(seed)
    n = int(dur * sr)
    y = np.zeros((2, n))
    scale = np.array([0, 2, 4, 7, 9, 12, 14, 16, 19])
    for _ in range(int(14 + 30 * intensity)):
        on = int(rng.uniform(0, 0.8) * n)
        m = min(n - on, int(0.25 * sr))
        tt = np.arange(m) / sr
        f = (1760 + 1760 * brightness) * 2 ** (rng.choice(scale) / 12)
        p = np.sin(2 * math.pi * f * tt) * np.exp(-tt / 0.07) * rng.uniform(0.2, 0.7)
        y[:, on:on + m] += _pan(p, rng.uniform(-0.9, 0.9))
    env = np.sin(math.pi * np.arange(n) / n) ** 0.5
    return _norm(_fade(_reverb(y * env, sr, 0.5, 0.35, seed)[:, :n], sr, 0.01, 0.05)), 0.15 * dur


# --- the few overlays the audio-visual studies DID measure (core/edit/refs/*-av.md; placed by core/edit/song_fx.py)

def hero_impact(dur=0.9, intensity=0.8, brightness=1.0, seed=0, sr=SR):
    """The hero-cut impact of cj12 / m3lv / i4f: a sub boom (sub +40..+50 dB) with a bright noise top (3-7 kHz
    +12..+26, 7-15 kHz +8..+30 dB) that dies in 100-200 ms: the top is the 'shing' the owner hears."""
    rng = _rng(seed)
    n = int(dur * sr)
    t = np.arange(n) / sr
    f = 36 + (58 + 20 * intensity) * np.exp(-t / 0.07)
    sub = np.sin(2 * math.pi * np.cumsum(f) / sr) * np.exp(-t / (0.22 + 0.15 * intensity))
    body = np.sin(2 * math.pi * np.cumsum(190 * np.exp(-t / 0.025) + 70) / sr) * np.exp(-t / 0.05)
    top = _shaped_noise(n, sr, 6500, 1.1, rng) * np.exp(-t / (0.045 + 0.03 * brightness))
    air = _shaped_noise(n, sr, 11000, 0.6, _rng(seed + 3)) * np.exp(-t / 0.05)
    mono = np.tanh(1.0 * sub + 0.5 * body) + (0.55 + 0.6 * brightness) * _norm(top) + 0.35 * brightness * _norm(air)
    mono *= np.clip(t / 0.0015, 0, 1)
    y = np.stack([mono, mono])
    side = _shaped_noise(n, sr, 8000, 0.9, _rng(seed + 1)) * np.exp(-t / 0.06) * 0.18 * brightness
    y[0] += side
    y[1] -= side
    return _norm(_fade(y, sr, 0.0005, 0.05)), 0.0015


def cut_whoosh(dur=0.6, intensity=0.5, brightness=0.7, seed=0, sr=SR, peak_s=0.37, decay_s=0.09):
    """i4f's soft whoosh riding every cut: 3-12 kHz noise centred ~5 kHz (centroid 4.5-5.7 kHz), exponential attack
    (10-90 % over 130-450 ms), peak at `peak_s`, -10 dB `decay_s` after it. sync = the peak."""
    rng = _rng(seed)
    n = int(dur * sr)
    t = np.arange(n) / sr
    centre = 5000 * 2 ** (0.25 * np.tanh(4 * (t - peak_s)))            # a slight upward glide through the peak
    x = _shaped_noise(n, sr, centre, 0.75 + 0.2 * intensity, rng)
    tau_up = max(0.03, peak_s / 3.2)
    env = np.where(t < peak_s, np.exp(-(peak_s - t) / tau_up), np.exp(-(t - peak_s) * 1.151 / max(0.01, decay_s)))
    y = _pan(x * env, 0.25 * np.tanh(3 * (t - peak_s)))
    return _norm(_fade(y, sr, 0.004, 0.01)), peak_s


def sparkle_chime(dur=0.55, intensity=0.5, brightness=0.8, seed=0, sr=SR):
    """upHh's heart-sticker chime: bell partials 3.36 / 6.93 / 10.56 kHz (flatness 0.03), -13 dB over 0.35 s, mono."""
    rng = _rng(seed)
    n = int(dur * sr)
    t = np.arange(n) / sr
    y = np.zeros(n)
    for f, a, tau in ((3360, 1.0, 0.23), (6930, 0.55, 0.16), (10560, 0.35, 0.11), (4720, 0.18, 0.08)):
        y += a * np.sin(2 * math.pi * f * (1 + 0.0015 * rng.standard_normal()) * t + rng.uniform(0, 6)) * np.exp(-t / tau)
    y *= np.clip(t / 0.002, 0, 1)
    return _norm(_fade(np.stack([y, y]), sr, 0.001, 0.03)), 0.0


def wash(dur=0.3, intensity=0.5, brightness=0.5, seed=0, sr=SR):
    """yUui's pre-drop fill: sustained, very wide (independent L / R noise, side ~ mid), centroid ~1.8 kHz, with a few
    200-300 Hz thumps; flat level, 8 ms edges (it runs up to the drop switch)."""
    rng = _rng(seed)
    n = max(64, int(dur * sr))
    t = np.arange(n) / sr
    y = np.stack([_shaped_noise(n, sr, 1800, 1.4, _rng(seed + k)) for k in (0, 1)])
    y /= np.sqrt(np.mean(y ** 2)) + 1e-9
    for on in rng.uniform(0, max(0.01, dur - 0.05), size=max(1, int(dur / 0.08))):
        i = int(on * sr)
        m = n - i
        tt = np.arange(m) / sr
        th = 0.8 * np.sin(2 * math.pi * rng.uniform(200, 300) * tt) * np.exp(-tt / 0.025)
        y[:, i:] += th
    return _norm(_fade(y, sr, 0.008, 0.008)), 0.0


# --- transition sounds measured with stem separation (core/edit/refs/transition-sound.md / .json, 2026-09-29):
# reference stems minus the waveform-locked song stems at every cut / zoom / flash of the Blur Flow references.

def _db_env(t, tpk, rise, t6, t12, floor_db=-60.0):
    """Level in dB (0 at the peak): exponential (linear-dB) rise reaching -12 dB `rise` s before the peak tpk (the
    study's onset = the -12 dB point), then -6 dB `t6` s and -12 dB `t12` s after the peak, continuing at the second
    slope."""
    up = np.where(t < tpk, -12.0 * np.clip((tpk - t) / max(rise, 1e-3), 0, 10), 0.0) if rise > 0 else \
        np.where(t < tpk, floor_db, 0.0)
    dt = np.maximum(0.0, t - tpk)
    s2 = 6.0 / max(1e-3, t12 - t6)
    dn = np.where(dt <= t6, -6.0 * dt / max(t6, 1e-3), -6.0 - s2 * (dt - t6))
    return np.maximum(floor_db, np.where(t < tpk, up, dn))


def noise_crash(dur=0.8, intensity=0.6, brightness=0.6, seed=0, sr=SR, rise=0.0, attack=0.006, t6=0.14, t12=0.45,
                cen0=7600.0, cen1=4450.0, width_oct=1.2, glide_s=0.1):
    """The Blur Flow 'shing' / whoosh (transition-sound.md): noise through a log-gaussian band whose centre glides
    cen0 -> cen1 over glide_s after the peak (S2rz's crash after a blur cut: 7.3-7.9 -> 4.4-4.5 kHz), level rising
    over `rise` s (0 = a hit with a `attack` s onset; 0.10-0.13 = S2rz's swell into a flash cut / the drop), -6 dB at
    t6 and -12 dB at t12 after the peak.  Slightly wide (independent L / R noise at -8 dB side).  sync = the peak."""
    rng = _rng(seed)
    tpk = max(2.5 * rise, attack)
    n = int(max(dur, tpk + t12 * 2.2 + 0.05) * sr)
    t = np.arange(n) / sr
    u = np.clip((t - tpk) / max(glide_s, 1e-3), 0, 1)
    pre = np.clip((t - (tpk - max(2.5 * rise, attack))) / max(2.5 * rise, attack, 1e-3), 0, 1)
    cen = np.where(t < tpk, cen0 * (cen1 / cen0) ** (-0.25 * (1 - pre)), cen0 * (cen1 / cen0) ** u)
    mid = _shaped_noise(n, sr, cen, width_oct, rng)
    side = _shaped_noise(n, sr, cen, width_oct, _rng(seed + 11))
    env_db = _db_env(t, tpk, rise, t6, t12)
    if rise <= 0:
        env_db = np.where(t < tpk, 20 * np.log10(np.clip(t / max(attack, 1e-4), 1e-3, 1)), env_db)
    env = 10 ** (env_db / 20)
    y = np.stack([mid + 0.4 * side, mid - 0.4 * side]) * env[None]
    return _norm(_fade(y, sr, 0.001, 0.01)), tpk


def sub_hit(dur=0.35, intensity=0.6, brightness=0.2, seed=0, sr=SR, f0=90.0, f1=45.0, tau_f=0.04, rise=0.0,
            t6=0.03, t12=0.06, body=0.5, click=0.3):
    """The Blur Flow sub hit (S2rz 24.95 / 34.91 s: 90 -> 45 Hz, -12 dB within 60-80 ms; the drop boom 64 -> 43 Hz)
    or, with rise > 0, the sub glide UP under a swell (S2rz 26.07 / 36.03: 30 -> 90 Hz over the last ~0.2 s).
    Mono sine with a little 2nd harmonic for small speakers.  sync = the peak."""
    tpk = max(2.5 * rise, 0.002)
    n = int(max(dur, tpk + t12 * 2.5 + 0.05) * sr)
    t = np.arange(n) / sr
    if rise > 0:
        f = np.where(t < tpk, f0 + (f1 - f0) * np.clip(t / tpk, 0, 1) ** 1.5, f1)
    else:
        f = f1 + (f0 - f1) * np.exp(-t / max(tau_f, 1e-3))
    ph = 2 * math.pi * np.cumsum(f) / sr
    tone = np.sin(ph) + 0.18 * (0.5 + brightness) * np.sin(2 * ph)
    env = 10 ** (_db_env(t, tpk, rise, t6, t12) / 20)
    if rise <= 0:
        env = env * np.clip(t / 0.002, 0, 1)
    y = np.tanh((0.8 + intensity) * tone) * env
    if rise <= 0 and (body > 0 or click > 0):
        # the references' hits are kick-like (Demucs files them under drums; S2rz entry: lowmid +6.4 dB at 274 Hz,
        # a noisy top): a 180 -> 70 Hz body (25 ms) and a 1-6 kHz noise click (6 ms)
        tt = np.maximum(0.0, t - tpk)
        bd = np.sin(2 * math.pi * np.cumsum(70 + 110 * np.exp(-tt / 0.012)) / sr) * np.exp(-tt / 0.025) * (t >= tpk)
        ck = signal.sosfilt(signal.butter(2, [1000, 6000], 'bandpass', fs=sr, output='sos'),
                            _rng(seed + 5).standard_normal(n)) * np.exp(-tt / 0.006) * (t >= tpk)
        y = y + body * bd + click * 2.5 * ck
    return _norm(_fade(np.stack([y, y]), sr, 0.0005, 0.01)), tpk


# --- TEXT sounds (core/edit/refs/text-sound.md, 2026-10-01). MEASURED: the references have NO sound of their own on typed
# letters, word entries, pops, glitches or card lands (event-triggered averages of the song-subtracted residual at 149
# typed letters / 38 S2rz body word entries / 9 WGvt hero words / 22 yUui words: flat vs random times, see text-sound.json);
# the only added sounds that sit on a title are the S2rz card-into-body boom + wash (title_impact below = song_fx 'entry')
# and the hyperspace air bed.  So the generators below are the OWNER-REQUESTED DESIGN tier (opt-in via plan "text_sound":
# "design", text_sound.py): small, band-limited, level-matched to the song, never a new drum kit.  Their spectra follow
# the vocabulary of the editors' own UI / foley sounds in the NPSOM sfx pack (camera shutter 91 % > 1.5 kHz, cloth
# 1 kHz, swoosh 0.43 s) scaled down; they are synthesised here (nothing sampled).

def type_click(dur=0.06, intensity=0.5, brightness=0.55, seed=0, sr=SR):
    """One typed key: a 2 ms noise tick (2-7 kHz) + a damped 1.1-1.9 kHz 'thock' (9 ms); the pitch varies +-9 % with
    the seed so a run of letters never machine-guns.  sync = the tick."""
    rng = _rng(seed)
    n = int(dur * sr)
    t = np.arange(n) / sr
    f = (1100 + 800 * brightness) * (1 + 0.09 * rng.uniform(-1, 1))
    nz = signal.sosfilt(signal.butter(2, [2000, min(7000 + 2500 * brightness, 0.45 * sr)], 'bandpass', fs=sr,
                                      output='sos'), rng.standard_normal(n)) * np.exp(-t / 0.0018)
    th = np.sin(2 * math.pi * f * t) * np.exp(-t / 0.009)
    y = (0.9 + 0.5 * intensity) * nz / (np.max(np.abs(nz)) + 1e-9) + 0.55 * th
    return _norm(_fade(_pan(y, rng.uniform(-0.25, 0.25)), sr, 0.0002, 0.006)), 0.0


def word_pop(dur=0.16, intensity=0.5, brightness=0.5, seed=0, sr=SR):
    """A word landing: a 5 ms 1.5-5 kHz noise tick over a pitched 'bloop' (sine 520 -> 190 Hz, tau 30 ms, +2nd harmonic
    at -12 dB), 90 ms tail.  sync = the attack (the frame the word reaches full size)."""
    rng = _rng(seed)
    n = int(dur * sr)
    t = np.arange(n) / sr
    f0 = 480 + 120 * brightness + 40 * rng.uniform(-1, 1)
    f = 190 + (f0 - 190) * np.exp(-t / 0.03)
    ph = 2 * math.pi * np.cumsum(f) / sr
    body = (np.sin(ph) + 0.25 * np.sin(2 * ph)) * np.exp(-t / 0.045) * np.clip(t / 0.0015, 0, 1)
    tick = signal.sosfilt(signal.butter(2, [1500, min(5000 + 2000 * brightness, 0.45 * sr)], 'bandpass', fs=sr,
                                        output='sos'), rng.standard_normal(n)) * np.exp(-t / 0.004)
    y = body + (0.35 + 0.5 * intensity) * tick / (np.max(np.abs(tick)) + 1e-9)
    return _norm(_fade(np.stack([y, y * 0.96]), sr, 0.0003, 0.012)), 0.0


def word_swish(dur=0.24, intensity=0.5, brightness=0.55, seed=0, sr=SR, direction='lr', peak_pos=0.72):
    """The swipe of a word flying / spreading in: shaped noise whose centre climbs 900 Hz -> 3.5-6 kHz, exponential
    swell to the peak at peak_pos * dur (= the frame the word lands), 25 ms fall.  sync = the peak."""
    rng = _rng(seed)
    n = int(dur * sr)
    t = np.arange(n) / sr
    tp = peak_pos * dur
    u = np.clip(t / tp, 0, 1)
    cen = 900 * ((3500 + 2500 * brightness) / 900) ** (u ** 1.3)
    mid = _shaped_noise(n, sr, cen, 1.1, rng)
    side = _shaped_noise(n, sr, cen, 1.1, _rng(seed + 7))
    env = np.where(t < tp, np.exp(-5.0 * (1 - t / tp)), np.exp(-(t - tp) / 0.012))
    pan = np.tanh((t - tp) / (0.25 * dur)) * (1 if direction == 'lr' else -1) * 0.5
    y = _pan(mid * env, pan) + 0.25 * np.stack([side, -side]) * env
    return _norm(_fade(y, sr, 0.002, 0.01)), tp


def glitch_tick(dur=0.1, intensity=0.6, brightness=0.6, seed=0, sr=SR):
    """A digital stutter on a text twitch: three 8-22 ms bit-crushed (5-level) square / noise cells at 1-6 kHz, the
    first loudest, random pans.  sync = the first cell."""
    rng = _rng(seed)
    n = int(dur * sr)
    y = np.zeros(n)
    i = 0
    for k in range(3):
        m = int(rng.uniform(0.008, 0.022) * sr)
        tt = np.arange(m) / sr
        f = rng.choice([700, 1400, 2800, 5600]) * (0.8 + 0.4 * brightness)
        cell = 0.7 * np.sign(np.sin(2 * math.pi * f * tt)) + 0.5 * rng.standard_normal(m)
        cell = np.round(cell * 5) / 5 * np.linspace(1, 0.5, m) * (1.0 if k == 0 else 0.55)
        y[i:i + m] += cell[:m]
        i += m + int(rng.uniform(0.004, 0.014) * sr)
        if i >= n - 40:
            break
    y = signal.sosfilt(signal.butter(2, [900, min(7000.0, 0.45 * sr)], 'bandpass', fs=sr, output='sos'), y)
    w = float(rng.uniform(-0.6, 0.6))
    return _norm(_fade(_pan(y, w), sr, 0.0003, 0.004)), 0.0


def title_impact(dur=1.0, intensity=0.7, brightness=0.3, seed=0, sr=SR):
    """The S2rz card-into-body landing (transition-sound.md 'entry', MEASURED): a kick-like sub (64 -> 43 Hz, tau 30 ms,
    lows +10.4 dB, peak +90..+160 ms after the switch) and a short hiss (noise 2.4 -> 1.4 kHz, peak 90 ms after the sub).
    sync = the sub peak.  (song_fx schedules the same pair via TRANSITION_SOUND['s2rz']['entry']; use this for a title
    card that has no such op.)"""
    a, sa = sub_hit(f0=64.0, f1=43.0, tau_f=0.03, t6=0.02, t12=0.04, seed=seed, sr=sr, click=0.05)
    b, sb = noise_crash(rise=0.13, t6=0.03, t12=0.07, cen0=2400.0, cen1=1400.0, width_oct=0.8, seed=seed + 3, sr=sr)
    off = int(round((sa + 0.09 - sb) * sr))        # the hiss peaks 90 ms after the sub peak
    lead = max(0, -off)
    n = max(a.shape[1] + lead, b.shape[1] + max(0, off) + lead, int(dur * sr))
    y = np.zeros((2, n))
    y[:, lead:lead + a.shape[1]] += a
    y[:, lead + max(0, off):lead + max(0, off) + b.shape[1]] += 0.22 * b
    return _norm(y), sa + lead / sr


GENERATORS = {
    'whoosh': whoosh, 'swish': swish, 'impact': impact, 'sub_boom': sub_boom, 'riser': riser,
    'reverse_swell': reverse_swell, 'shutter': shutter, 'click': click, 'paper_slide': paper_slide,
    'glass_shatter': glass_shatter, 'glitch': glitch, 'tape_stop': tape_stop_fx, 'heartbeat': heartbeat,
    'shimmer': shimmer, 'hero_impact': hero_impact, 'cut_whoosh': cut_whoosh, 'sparkle_chime': sparkle_chime,
    'wash': wash, 'noise_crash': noise_crash, 'sub_hit': sub_hit,
    'type_click': type_click, 'word_pop': word_pop, 'word_swish': word_swish, 'glitch_tick': glitch_tick,
    'title_impact': title_impact,
}

VOCABULARY = {
    'whoosh': 'white noise through a log-gaussian band whose centre sweeps 250-1000 Hz -> 1.8-8 kHz and back '
              '(k^1.4), width 0.9-1.4 oct, -1.5 dB/oct tilt, + airy layer x2.4; level exp(-5.5(1-t/tp)) up, exp(-7) '
              'down, peak at 62 % (= the cut); equal-power pan tanh across the peak; 0.25-0.6 s',
    'swish': 'the same, 0.12-0.2 s, brighter, peak at 50 %: whips, slides, bar wipes',
    'impact': 'sine 110->36 Hz (tau 55 ms) decaying 0.16-0.38 s + 230->60 Hz body (45 ms) + noise crack 1.5-7 kHz '
              '(12-40 ms) through tanh drive; attack 1.5 ms (= the first frame of the new shot / flash)',
    'sub_boom': 'sine 64->30 Hz (tau 120 ms), decay 0.35-0.7 s, soft low crack: the drop',
    'riser': 'noise band climbing 3 octaves (t^1.3) + sine 220 Hz x2^(2t^1.5) at -15 dB, level exp(-4.6(1-t)), '
             'tremolo 4->24 Hz, stereo width 0.2->0.8; cut dead at the end (= the drop)',
    'reverse_swell': 'five-partial tonal hit + noise, 0.9*dur reverb, reversed: ends on the event',
    'shutter': 'two band-passed noise + sine clicks (1.8-4.3 kHz then 1.2-3.2 kHz, 6-8 ms decays) 55-65 ms apart + '
               '600 Hz mechanical tail: photo / polaroid / freeze-frame effects',
    'click': '4 ms resonant tick 1.5-5.5 kHz: type-on text, UI taps',
    'paper_slide': 'friction noise 3.5-7 kHz, +1 dB/oct, amplitude grains (90 Hz low-passed noise), sine-shaped '
                   'level, 140 Hz stop thud at 85 %: photo / card / postcard slides',
    'glass_shatter': 'crack (4.5 kHz, 12 ms) + 30-60 inharmonic sines 1.2-11 kHz (40-400 ms decays, onsets '
                     'scattered over 60 ms) + 25-75 falling noise grains, random pans',
    'glitch': '18-38 ms cells of 6-level bit-crushed square / noise, 45 % cell repeats, band 0.2-13 kHz, per-cell pan',
    'tape_stop': 'as a music treatment: resample the music with speed (1-t)^1.5 over 0.3-0.8 s; as an effect: a '
                 'falling three-harmonic tone + motor noise',
    'heartbeat': 'two 73->48 Hz thumps 160 ms apart (60 ms decays)',
    'shimmer': '14-44 sine pings on a major-pentatonic cloud 1.8-10 kHz, 70 ms decays, reverb 0.5 s: glow, leaks, hearts',
    'hero_impact': 'sine 94..114 -> 36 Hz (tau 70 ms) + 260 -> 70 Hz body + 3-15 kHz noise top dying in 100-200 ms: '
                   'the hero-cut impact whose top reads as the "shing" (cj12, m3lv, i4f; general-av.md)',
    'cut_whoosh': '5 kHz-centred noise, exponential attack to the peak 50-190 ms after the cut, -10 dB 20-200 ms '
                  'later (i4f only, general-av.md 2.4)',
    'sparkle_chime': 'bell partials 3.36 / 6.93 / 10.56 kHz, -13 dB over 0.35 s, mono (upHh sticker pop, kpop-av.md)',
    'wash': 'wide decorrelated noise, centroid 1.8 kHz, 200-300 Hz thumps: fills the song\'s stop before a drop '
            '(yUui, character-av.md)',
    'noise_crash': 'noise in a log-gaussian band gliding cen0 -> cen1 (7.6 -> 4.5 kHz) after the peak, rise 0 (hit) or '
                   '0.10-0.13 s (swell), -6 dB at t6, -12 dB at t12: the Blur Flow shing / whoosh (transition-sound.md)',
    'sub_hit': 'sine 90 -> 45 Hz (tau 40 ms), -12 dB in 60-80 ms; with rise > 0 a 30 -> 90 Hz glide up under a swell '
               '(S2rz, transition-sound.md)',    'type_click': 'TEXT (design tier, text-sound.md): 2 ms 2-7 kHz tick + damped 1.1-1.9 kHz thock, pitch +-9 % per seed',
    'word_pop': 'TEXT (design): 5 ms 1.5-5 kHz tick + sine 520 -> 190 Hz bloop (tau 30 ms), 90 ms tail',
    'word_swish': 'TEXT (design): noise band climbing 0.9 -> 3.5-6 kHz, swell to the landing frame, 25 ms fall, 0.24 s',
    'glitch_tick': 'TEXT (design): three 8-22 ms 5-level bit-crushed cells 0.7-7 kHz on a text twitch',
    'title_impact': 'TEXT (MEASURED, S2rz card -> body): sub 64 -> 43 Hz + hiss 2.4 -> 1.4 kHz 90 ms later (transition-sound.md entry)',
}


# ------------------------------------------------------------------------------------------------ music treatments

def duck(x, sr, at, depth_db=-4.0, attack=0.03, hold=0.08, release=0.25):
    """Dip the music around `at` (in place): attack before, hold, release after."""
    n = x.shape[1]
    t = np.arange(n) / sr - at
    g = np.zeros(n)
    g = np.where((t >= -attack) & (t < 0), (t + attack) / attack, g)
    g = np.where((t >= 0) & (t < hold), 1.0, g)
    g = np.where((t >= hold) & (t < hold + release), 1 - (t - hold) / release, g)
    x *= 10 ** (depth_db * g / 20)
    return x


def silence_gap(x, sr, t0, t1, fade=0.005):
    """Digital silence between t0 and t1 (the freeze before a drop; kpop fSMk1eTrSX0: 1.57 s at -96 dB)."""
    n = x.shape[1]
    a, b, r = int(t0 * sr), int(t1 * sr), max(1, int(fade * sr))
    g = np.ones(n)
    g[max(0, a):max(0, b)] = 0
    if a - r > 0:
        g[a - r:a] = np.linspace(1, 0, r)
    if 0 < b and b + r < n:
        g[b:b + r] = np.linspace(0, 1, r)
    x *= g
    return x


def lowpass_sweep(x, sr, t0, t1, f0=400.0, f1=18000.0, curve=2.0):
    """Time-varying low-pass between t0 and t1 (cutoff f0 -> f1, exponential in frequency): the 'underwater
    intro opening up' treatment. STFT-domain, 4th-order-like slope."""
    a, b = max(0, int(t0 * sr)), min(x.shape[1], int(t1 * sr))
    if b - a < 2048:
        return x
    nper, hop = 1024, 256
    for ch in range(x.shape[0]):
        f, _, Z = signal.stft(x[ch, a:b], sr, nperseg=nper, noverlap=nper - hop)
        k = np.linspace(0, 1, Z.shape[1]) ** curve
        fc = f0 * (f1 / f0) ** k
        g = 1.0 / np.sqrt(1 + (f[:, None] / fc[None]) ** 8)
        _, y = signal.istft(Z * g, sr, nperseg=nper, noverlap=nper - hop)
        m = min(len(y), b - a)
        x[ch, a:a + m] = y[:m]
    return x


def tape_stop(x, sr, t0, dur=0.5, power=1.5):
    """Slow the music to a halt from t0 over `dur`, silence until t0+dur (in place)."""
    a, n = int(t0 * sr), int(dur * sr)
    if a < 0 or a + n > x.shape[1]:
        return x
    u = np.arange(n) / n
    pos = np.cumsum((1 - u) ** power)
    pos = pos[pos < n - 1]
    i = pos.astype(int)
    fr = pos - i
    seg = x[:, a:a + n].copy()
    out = np.zeros_like(seg)
    out[:, :len(i)] = (seg[:, i] * (1 - fr) + seg[:, i + 1] * fr) * (1 - u[:len(i)]) ** 0.5
    x[:, a:a + n] = out
    return x


def clip_passthrough(seg, sr, gain_db=0.0, hp_hz=120.0, ceiling_db=-3.0, fade=0.015):
    """A shot's own sound under the music: high-pass (keeps rumble out of the song's low end), gain, fades and a
    limiter so that one loud line can never poke through the master."""
    y = np.asarray(seg, dtype=float).copy()
    if y.shape[-1] < 32:
        return y
    if hp_hz:
        y = signal.sosfilt(signal.butter(2, hp_hz, 'highpass', fs=sr, output='sos'), y, axis=-1)
    y *= 10 ** (gain_db / 20)
    y = _fade(y, sr, fade, fade)
    return limiter(y, sr, ceiling_db)


# ------------------------------------------------------------------------------------------------ cue rendering

def _st_rms(x, sr, win=0.02):
    w = max(8, int(win * sr))
    p = np.convolve((x ** 2).mean(axis=0), np.ones(w) / w, mode='same')
    return float(np.sqrt(p.max() + 1e-12))


def music_rms_env(music, sr, win=0.3, hop=0.01):
    """+-win RMS of the music every `hop` seconds (the reference level every cue is set against)."""
    n = music.shape[1]
    p2 = np.cumsum(np.pad((music ** 2).mean(axis=0), (1, 0)))
    idx = np.arange(0, n, max(1, int(hop * sr)))
    lo, hi = np.clip(idx - int(win * sr), 0, n), np.clip(idx + int(win * sr), 0, n)
    return np.sqrt(np.maximum(1e-12, (p2[hi] - p2[lo]) / np.maximum(1, hi - lo)))


def _song_ceiling(music, sr, at, span=1.5, rel_db=-40.0):
    """Highest frequency where the song around `at` is within rel_db of its 3-8 kHz level (same rule as
    song_fx.song_ceiling; local copy so sfx.py stays import-free of song_fx)."""
    n = music.shape[-1]
    a, b = max(0, int((at - span) * sr)), min(n, int((at + span) * sr))
    if b - a < 8192:
        return 16000.0
    f, P = signal.welch(music[:, a:b].mean(axis=0), sr, nperseg=8192)
    ref = float(np.median(P[(f > 3000) & (f < 8000)])) + 1e-20
    ok = f[10 * np.log10(P / ref + 1e-20) > rel_db]
    return float(np.clip(ok.max() if len(ok) else 16000.0, 6000.0, 20000.0))


def render_cues(cues, duration, sr=SR, music=None, floor_db=-30.0):
    """
    cues: [{'sfx': name, 'at': s, 'gain_db': dB relative to the music's +-0.3 s RMS at `at` (peak 20 ms RMS of the
            sound), 'dur', 'intensity', 'brightness', 'seed', 'direction'}]
    Returns (layer (2, n), report [{sfx, at, start, dur, level_db, music_db}]).
    Without music the level reference is floor_db + 12.
    """
    n = int(round(duration * sr))
    layer = np.zeros((2, n))
    env = music_rms_env(music, sr) if music is not None and np.any(music) else None
    gmed = 20 * math.log10(float(np.median(env)) + 1e-9) if env is not None else floor_db + 12
    rep = []
    for k, c in enumerate(cues):
        fn = GENERATORS.get(c.get('sfx'))
        if fn is None:
            continue
        kw = dict(intensity=float(c.get('intensity', 0.6)), brightness=float(c.get('brightness', 0.5)),
                  seed=int(c.get('seed', k * 7919 + 13)), sr=sr)
        if c.get('dur'):
            kw['dur'] = float(c['dur'])
        if c.get('direction') and c['sfx'] in ('whoosh', 'swish', 'paper_slide'):
            kw['direction'] = c['direction']
        y, sync = fn(**kw)
        at = float(c['at'])
        # optional band limits (text sounds): 'ceiling': 'song' = never brighter than the song around `at` (its own
        # 15.8-16.5 kHz codec limit, transition-sound.md), 'lp_hz' / 'hp_hz' fixed corners
        lp = c.get('lp_hz')
        if c.get('ceiling') == 'song' and music is not None and np.any(music):
            lp = min(float(lp or 1e9), _song_ceiling(music, sr, at))
        if lp and float(lp) < 0.45 * sr:
            y = signal.sosfiltfilt(signal.butter(6, float(lp), 'lowpass', fs=sr, output='sos'), y, axis=-1)
        if c.get('hp_hz'):
            y = signal.sosfilt(signal.butter(2, float(c['hp_hz']), 'highpass', fs=sr, output='sos'), y, axis=-1)
        if env is not None:
            mdb = 20 * math.log10(float(env[min(len(env) - 1, max(0, int(at / 0.01)))]) + 1e-9)
            mdb = max(mdb, gmed - 12.0)          # a silence gap must not make the sound vanish
        else:
            mdb = gmed
        target = max(float(c.get('floor_db', floor_db)), mdb + float(c.get('gain_db', -6.0)))
        y = y * (10 ** (target / 20) / _st_rms(y, sr))
        i0 = int(round((at - sync) * sr))
        if i0 < 0:
            y = y[:, -i0:]
            i0 = 0
        m = min(y.shape[1], n - i0)
        if m <= 0:
            continue
        layer[:, i0:i0 + m] += y[:, :m]
        rep.append(dict(sfx=c['sfx'], at=round(at, 4), start=round(i0 / sr, 4), dur=round(y.shape[1] / sr, 3),
                        level_db=round(target, 1), music_db=round(mdb, 1), why=c.get('why')))
    return layer, rep


# ------------------------------------------------------------------------------------------------ preset mapping
#
# MEASURED (core/edit/refs/audio-sfx.json): none of the 37 references carries added whoosh / impact / riser
# sounds on its transitions; the only added layers are (1) the clips' own hits in the sound group, (2) dialogue
# before the song in a few general / character references (forbidden by the owner) and (3) music treatments:
# a digital-silence freeze before the drop (kpop fSMk1eTrSX0 1.57 s, PsdUhnuh-AE 1.3 s) and end fades.
# So the 'measured' profile of every preset is "none" (+ the treatments above, which the planners already emit
# as `mute` overlays).  The owner, after watching fx_heavy_gojo, asked for transition sound design anyway
# ("what is missing is ... the sound effects of the transitions"), so each preset ALSO has a 'design' profile,
# and SFX_AUTO decides what "sfx": "auto" means per preset.  Hard-cut / raw / sound-edit presets stay silent.

# family of a transition / accent / overlay -> sound.  gain_db is relative to the music's +-0.3 s RMS.
_T = dict
SFX_PROFILES = {
    'none': {},
    # subtle: supports the motion, never competes with the song (whooshes 8-10 dB under the music)
    'soft': {
        'whip': _T(sfx='swish', gain_db=-10, dur=0.22, brightness=0.6),
        'zoom': _T(sfx='whoosh', gain_db=-10, dur=0.36, brightness=0.45),
        'spin': _T(sfx='whoosh', gain_db=-10, dur=0.42, brightness=0.5),
        'blur': _T(sfx='whoosh', gain_db=-12, dur=0.30, brightness=0.35),
        'flash': _T(sfx='impact', gain_db=-9, dur=0.45, brightness=0.3, intensity=0.4),
        'drop': _T(sfx='sub_boom', gain_db=-5, dur=1.2),
        'riser': _T(sfx='reverse_swell', gain_db=-10, dur=1.2),
        'photo': _T(sfx='shutter', gain_db=-6),
        'card': _T(sfx='paper_slide', gain_db=-9, dur=0.32),
        'glass': _T(sfx='glass_shatter', gain_db=-5, dur=1.0),
        'glow': _T(sfx='shimmer', gain_db=-14, dur=0.8),
    },
    # full: the AE / CapCut "velocity edit" sound design (whooshes 5-6 dB under, impacts at music level)
    'full': {
        'whip': _T(sfx='swish', gain_db=-6, dur=0.22, brightness=0.55),
        'zoom': _T(sfx='whoosh', gain_db=-6, dur=0.40, brightness=0.5),
        'spin': _T(sfx='whoosh', gain_db=-6, dur=0.48, brightness=0.55),
        'blur': _T(sfx='whoosh', gain_db=-9, dur=0.30, brightness=0.4),
        'flash': _T(sfx='impact', gain_db=-4, dur=0.6, brightness=0.4, intensity=0.6),
        'punch': _T(sfx='impact', gain_db=-8, dur=0.3, brightness=0.3, intensity=0.4),
        'shake': _T(sfx='impact', gain_db=-5, dur=0.6, brightness=0.3, intensity=0.7),
        'drop': _T(sfx='sub_boom', gain_db=-2, dur=1.4),
        'riser': _T(sfx='riser', gain_db=-8, dur=2.0),
        'photo': _T(sfx='shutter', gain_db=-4),
        'card': _T(sfx='paper_slide', gain_db=-7, dur=0.32),
        'glass': _T(sfx='glass_shatter', gain_db=-3, dur=1.1),
        'glitch': _T(sfx='glitch', gain_db=-8, dur=0.2),
        'wipe': _T(sfx='swish', gain_db=-9, dur=0.25, brightness=0.5),
        'glow': _T(sfx='shimmer', gain_db=-12, dur=0.8),
        'strobe': _T(sfx='glitch', gain_db=-10, dur=0.15),
    },
}

# transition (registry name, aliases resolved) -> family
TRANSITION_FAMILY = {
    'whip': ['whip_diag', 'whip_exp_black', 'whip_exp_soft', 'whip_exp_white', 'whip_horizontal', 'whip_slide',
             'whip_vertical', 'vslide_wrap', 'cutout_prereveal', 'darken_slide_preview'],
    'zoom': ['zoom_through', 'zoom_through_color_field', 'lens_zoomthrough', 'radial_zoom_burst', 'zoom_blur_in',
             'zoom_blur_flash', 'blur_zoom', 'zoom_settle', 'dolly_out_white', 'flash_zoom', 'punch_settle',
             'blur_in_punch', 'impact_zoom_1frame', 'mirror_tile_scale_in'],
    'spin': ['spin_blur', 'spin_settle', 'blur_rotate'],
    'blur': ['blur_bridge', 'blur_bridge_flash', 'blur_bridge_hype', 'blur_bridge_soft', 'mb_cut', 'shake_blur',
             'rack_focus', 'defocus_dissolve', 'defocus_in', 'dissolve_blur', 'dark_blur_in'],
    'flash': ['flash_white', 'flash_in', 'white_flash_cut', 'white_out', 'tint_flash', 'halftone_flash',
              'pattern_flash', 'glow_cut', 'exposure_bump_hard', 'hard_punch'],
    'photo': ['photo_clones', 'photo_collage', 'photo_collage_legacy', 'photo_to_phone', 'photo_to_scene',
              'scene_to_photo_collage'],
    'card': ['card_tilt_in', 'peek_window', 'slices_reveal'],
    'glass': ['glass_shatter'],
    'glitch': ['rgb_split_glitch'],
    'strobe': ['invert_strobe', 'dip_black_strobe'],
    'wipe': ['bar_open_reveal', 'blob_wipe', 'chevron_wipe', 'iris_heart', 'iris_wipe', 'shape_wipe', 'wave_wipe',
             'white_bar_wipe'],
}
_FAMILY_OF = {name: fam for fam, names in TRANSITION_FAMILY.items() for name in names}
# silent on purpose: hard, hard_settle, cross_dissolve, dip_black, fade_in, vignette_fade_in, breath, first_shot_entry
ACCENT_FAMILY = {'shake': 'shake', 'flash': 'flash', 'tint_flash': 'flash', 'strobe_smear': 'strobe'}
MODIFIER_FAMILY = {'flash': 'flash'}
OVERLAY_FAMILY = {'glass_cracks': 'glass', 'polaroids': 'photo', 'zoom_blur_hit': 'zoom'}

# what "sfx": "auto" resolves to, per preset.  The audio-visual re-study (core/edit/refs/{general,fx,kpop,character,
# dance,sound}-av.md, 2026-09-26) measured NO designed sound on ORDINARY cuts in any of the 37 references (cut-triggered
# averages with injected-sound calibration: a whoosh / shing at -18..-30 dB under the music would have shown). What the
# owner hears is the song's own hit under the cut plus edits of the song at hero moments; those, and the few overlays
# that do exist (hero impact, i4f's 5 kHz whoosh, zawB's sub boom, upHh's sparkle, 91tp's glide), are scheduled per
# preset by core/edit/song_fx.py (plan "audio_design": "auto", the default with "sfx": "auto"; table presets.AUDIO_AV).
# So "auto" resolves to NO transition-SFX profile everywhere; "soft" / "full" stay available for hand-made plans.
# (was, 2026-09-21: auto='soft' / 'full' on blur_flow, hype, band, cinematic_long, char_quick, kpop postcard / stage, fx)
PRESET_SFX = {
    'blur_flow':              dict(measured='none', auto='none', clip_audio='none'),      # hero impacts: song_fx (cj12)
    'hype_typography':        dict(measured='none', auto='none', clip_audio='none'),      # i4f / m3lv / zawB: song_fx
    'cinematic_slowmo':       dict(measured='none', auto='none', clip_audio='none'),      # hard cut + bloom bump, song only
    'band_parade':            dict(measured='none', auto='none', clip_audio='none'),
    'cinematic_long':         dict(measured='none', auto='none', clip_audio='none'),
    'char_quick_velocity':    dict(measured='none', auto='none', clip_audio='intro_line'),  # + song edits (song_fx)
    'char_soft_romance':      dict(measured='none', auto='none', clip_audio='intro_line'),
    'char_raw_crisp':         dict(measured='none', auto='none', clip_audio='none'),
    'dance_burst_parade':     dict(measured='none', auto='none', clip_audio='none'),
    'dance_smooth_velocity':  dict(measured='none', auto='none', clip_audio='none'),      # 91tp glide: song_fx
    'dance_raw':              dict(measured='none', auto='none', clip_audio='none'),
    'dance_meme':             dict(measured='none', auto='none', clip_audio='none'),
    'dance_long_amv':         dict(measured='none', auto='none', clip_audio='none'),
    'kpop_postcard_drop':     dict(measured='none', auto='none', clip_audio='pop_line'),  # flanger / sub swell: song_fx
    'kpop_stage_push':        dict(measured='none', auto='none', clip_audio='none'),      # sparkle on stickers: song_fx
    'kpop_meme_reveal':       dict(measured='none', auto='none', clip_audio='none'),      # + silence gap (mute overlay)
    'kpop_single_take_smear': dict(measured='none', auto='none', clip_audio='none'),      # + 1.51 s freeze silence
    'kpop_text_strobe':       dict(measured='none', auto='none', clip_audio='none'),
    'sound_hit_sync':         dict(measured='clip_hits', auto='none', clip_audio='hits'),  # the clips ARE the sound design
    'fx_typography':          dict(measured='none', auto='none', clip_audio='none'),      # hold + swell: song_fx (fx-av.md)
}
# style name -> preset id fallbacks (plans that carry no "preset" key)
STYLE_DEFAULT = {'fx_typography': 'fx_typography', 'sound': 'sound_hit_sync'}


def resolve_mode(plan, style_name=None):
    """plan['sfx']: 'none' | 'auto' | 'measured' | 'soft' | 'full'  ->  profile name.
    A plan WITHOUT the key keeps the old behaviour (none): nothing changes for existing plans."""
    mode = plan.get('sfx')
    if mode in (None, False, '', 'none', 'off'):
        return 'none'
    if mode in ('soft', 'full'):
        return mode
    pid = plan.get('preset') or (plan.get('planner') or {}).get('preset') or STYLE_DEFAULT.get(style_name or '')
    row = PRESET_SFX.get(pid)
    if mode == 'measured':
        return 'none'
    if mode in ('auto', True, 'on'):
        return row['auto'] if row else 'none'
    return 'none'


def family_of_transition(name, aliases=None):
    base = (name or 'hard').split('+')[0]
    base = (aliases or {}).get(base, base)
    fams = []
    if base in _FAMILY_OF:
        fams.append(_FAMILY_OF[base])
    for mod in (name or '').split('+')[1:]:
        if mod in MODIFIER_FAMILY and MODIFIER_FAMILY[mod] not in fams and 'flash' not in base:
            fams.append(MODIFIER_FAMILY[mod])
    if 'flash' in base and base in _FAMILY_OF and _FAMILY_OF[base] != 'flash':
        fams.append('flash')
    return fams


def cues_for_engine(eng, profile_name, aliases=None):
    """Build the cue list for an engine instance (after its _prep): one sound per designed transition, accents,
    overlays and the drop, thinned so that sounds never pile up (>= 0.18 s apart, impacts win over whooshes)."""
    prof = SFX_PROFILES.get(profile_name) or {}
    if not prof:
        return []
    cues = []

    def add(fam, at, why, **over):
        spec = prof.get(fam)
        if not spec or at < 0.0 or at > eng.duration - 0.03:
            return
        c = dict(spec)
        c.update(over)
        c.update(at=float(at), family=fam, why=why)
        cues.append(c)

    for i, s in enumerate(eng.shots):
        if i == 0:
            continue
        own = s.get('sfx_design', s.get('sound_design'))
        if own in ('none', False):
            continue
        name = s.get('transition_in') or 'hard'
        tr = eng.trans[i] if i < len(eng.trans) else None
        pre = float(getattr(tr, 'pre', 0.0) or 0.0)
        post = float(getattr(tr, 'post', 0.0) or 0.0)
        if isinstance(own, str) and own in GENERATORS:
            cues.append(dict(sfx=own, at=float(s['t0']), gain_db=-6, family='shot', why=f'shot {i} sfx_design'))
            continue
        for fam in family_of_transition(name, aliases):
            over = {}
            if fam in ('whip', 'zoom', 'spin', 'blur', 'wipe'):
                # the sound spans the picture transition: peak on the cut, body = pre + post (clamped)
                span = pre + post
                if span > 0.05:
                    over['dur'] = float(np.clip(span * 1.6, 0.16, 0.6))
                over['direction'] = 'lr' if (s.get('seed', i) % 2 == 0) else 'rl'
            over['seed'] = int(s.get('seed', i)) + 101
            add(fam, s['t0'], f'{name}', **over)
        for a in s.get('_acc') or []:
            fam = ACCENT_FAMILY.get(getattr(a, 'kind', ''))
            if fam:
                add(fam, a.at, f'accent {a.kind}', seed=int(a.at * 1000))
    for ov in eng.plan.get('overlays') or []:
        fam = OVERLAY_FAMILY.get(ov.get('type'))
        if fam and ov.get('sfx_design') != 'none':
            add(fam, float(ov.get('t0', 0.0)), f"overlay {ov.get('type')}", seed=int(float(ov.get('t0', 0)) * 1000))
    drop = getattr(eng, 'drop_t', None)
    if drop is not None and 0.5 < float(drop) < eng.duration - 0.2 and eng.plan.get('drop') is not None:
        add('drop', float(drop), 'drop')
        r = prof.get('riser')
        if r and float(drop) > float(r.get('dur', 1.5)) * 0.6:
            add('riser', float(drop), 'into the drop', dur=min(float(r.get('dur', 1.5)), float(drop) - 0.05))
    # thin: strongest family wins inside 0.18 s
    rank = {'drop': 9, 'glass': 8, 'photo': 7, 'shake': 6, 'flash': 5, 'card': 4, 'whip': 3, 'zoom': 3, 'spin': 3,
            'glitch': 3, 'wipe': 2, 'blur': 1, 'punch': 1, 'strobe': 1, 'glow': 0, 'riser': 10, 'shot': 9}
    cues.sort(key=lambda c: c['at'])
    # density caps: on a cut-per-beat edit a sound on EVERY cut becomes a second drum kit. Impacts at most one per
    # 1.5 s, motion sounds (whoosh / swish) at most one per 0.45 s; the special ones (photo, card, glass, drop) always.
    gaps = {'flash': 1.5, 'punch': 1.5, 'shake': 1.5, 'whip': 0.45, 'zoom': 0.45, 'spin': 0.45, 'blur': 0.45, 'wipe': 0.45,
            'strobe': 0.8, 'glitch': 0.8, 'glow': 1.5}
    gaps.update(getattr(eng, 'plan', {}).get('sfx_min_gap') or {})
    last_cls = {}
    kept = []
    for c in cues:
        cls = 'impact' if c['family'] in ('flash', 'punch', 'shake') else             'motion' if c['family'] in ('whip', 'zoom', 'spin', 'blur', 'wipe') else c['family']
        g = gaps.get(c['family'])
        if g is not None and cls in last_cls and c['at'] - last_cls[cls] < g:
            continue
        if g is not None:
            last_cls[cls] = c['at']
        kept.append(c)
    cues = kept
    out = []
    for c in cues:
        if c['family'] == 'riser':
            out.append(c)
            continue
        prev = next((o for o in reversed(out) if o['family'] != 'riser'), None)
        # a whoosh INTO a cut and the impact ON it are one gesture: both stay
        pair = prev and {prev['family'], c['family']} & {'flash', 'drop', 'shake'} and \
            {prev['family'], c['family']} & {'whip', 'zoom', 'spin', 'blur', 'wipe'}
        if prev and c['at'] - prev['at'] < 0.18 and not pair:
            if rank.get(c['family'], 0) > rank.get(prev['family'], 0):
                out[out.index(prev)] = c
            continue
        out.append(c)
    return out


# ------------------------------------------------------------------------------------------------ cli / selftest

def selftest():
    ok = True
    for name, fn in GENERATORS.items():
        a, s1 = fn(seed=3)
        b, _ = fn(seed=3)
        c, _ = fn(seed=4, sr=44100)
        good = (a.shape[0] == 2 and a.shape[1] > 100 and np.isfinite(a).all() and abs(np.max(np.abs(a)) - 1) < 1e-6
                and np.array_equal(a, b) and c.shape[0] == 2 and 0 <= s1 <= a.shape[1] / SR + 1e-9)
        print(f'{name:14s} {"ok" if good else "FAIL"}  {a.shape[1] / SR:.2f} s  sync {s1:.3f}')
        ok &= bool(good)
    mus = np.random.default_rng(0).standard_normal((2, SR * 3)) * 0.1
    layer, rep = render_cues([dict(sfx='whoosh', at=1.0, gain_db=-6), dict(sfx='impact', at=1.0, gain_db=-3)], 3.0, SR, mus)
    pk = int(np.argmax(np.abs(layer).max(axis=0))) / SR
    good = abs(pk - 1.0) < 0.03 and len(rep) == 2
    print('render_cues    ', 'ok' if good else 'FAIL', f'peak at {pk:.3f}')
    ok &= good
    x = mus.copy()
    duck(x, SR, 1.0, -6)
    silence_gap(x, SR, 2.0, 2.5)
    lowpass_sweep(x, SR, 0.0, 0.9)
    tape_stop(x, SR, 2.5, 0.4)
    y = clip_passthrough(mus[:, :SR] * 20, SR, 0.0)
    good = np.isfinite(x).all() and np.abs(x[:, int(2.1 * SR):int(2.4 * SR)]).max() == 0 and np.max(np.abs(y)) <= 10 ** (-3 / 20) + 1e-3
    print('treatments     ', 'ok' if good else 'FAIL')
    ok &= bool(good)
    assert resolve_mode({}) == 'none' and resolve_mode({'sfx': 'auto', 'preset': 'fx_typography'}) == 'none'
    assert resolve_mode({'sfx': 'full', 'preset': 'fx_typography'}) == 'full'
    assert resolve_mode({'sfx': 'auto', 'preset': 'sound_hit_sync'}) == 'none'
    assert family_of_transition('whip_exp_white+flash') == ['whip', 'flash']
    print('SELFTEST', 'PASS' if ok else 'FAIL')
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--demo')
    ap.add_argument('--one')
    ap.add_argument('--dur', type=float)
    ap.add_argument('--out')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        sys.exit(0 if selftest() else 1)
    import soundfile as sf
    if a.one:
        kw = {'dur': a.dur} if a.dur else {}
        y, _ = GENERATORS[a.one](**kw)
        sf.write(a.out or a.one + '.wav', (y * 0.5).T.astype(np.float32), SR)
        return
    if a.demo:
        parts = []
        for name, fn in GENERATORS.items():
            y, _ = fn()
            parts += [y * 0.5, np.zeros((2, SR // 2))]
        sf.write(a.demo, np.concatenate(parts, axis=1).T.astype(np.float32), SR)
        print(json.dumps({'ok': True, 'path': os.path.abspath(a.demo), 'sounds': list(GENERATORS)}))


if __name__ == '__main__':
    main()
