#!/usr/bin/env python3
"""
song_fx.py - edits of the SONG itself at transitions, scheduled per preset exactly as the audio-visual studies measured
them (core/edit/refs/{general,fx,kpop,character,dance,sound}-av.md / .json; summary table: presets.AUDIO_AV).

What the owner hears as "sound effects on the transitions" is, in the references:
  1. the song's own kick / snare landing 0-90 ms after the picture switch (the planners' lead; `cut_hits` measures it),
  2. edits of the song around the HERO moments (this module), and
  3. very few overlays: one impact with a bright top ("shing") on 1-3 hero cuts, a soft 5 kHz whoosh on every cut in ONE
     reference (PJUNKIE i4f), sub booms trailing flash cuts (Purgify zawB), a sparkle chime on a sticker pop (upHh),
     a pitch-up glide replacing the song before a drop (91tp).  Ordinary cuts carry nothing in any reference.

Operations (times on the OUTPUT timeline, seconds; every op is a dict {"op": name, ...}):
  song edits (in place on the song track)
    hold_swell     {t0, cut, hold_db=-3.8, swell_s=0.18}            fx intro card: held down, swells back from the cut
    swell          {cut, depth_db=8, dur=0.4}                       m3lv: the song grows into the cut (linear dB)
    duck           {t0, t1, depth_db=-8, mid_db=0, attack=0.03, release=0.03}   (mid_db: extra cut 300 Hz-3 kHz)
    band           {t0, t1, lo, hi, db, ramp_in=0.02, ramp_out=0.02} generic band gain (STFT domain)
    lowpass        {t_close, close_s, t_open, open_s, hi_db=-22, mid_db=-15}    i4f muffle before a flash + shake cut
    echo           {cut, dur=0.24, delay=0.12, end_before=0.017, hi_db=-15, lowmid_db=-7.5}   echo throw, dry on the cut
    dropout        {cut, start=0.10, dur=0.20, depth_db=-14, bass_db=6}  eEPZ: >90 Hz drops, the bass rings
    bass_cut       {cut, dur=0.35, depth_db=-10}                    YH: 25-90 Hz pulled out before a chorus cut
    fade           {t0, t1, db0=0, db1=-15, then_out=True}          end ride (character) / fades
    slowdown       {t0, dur=0.18}                                   eEPZ end: tape slow over 180 ms
    flanger        {t0, t1, tau_ms=[[u, ms], ...] (u 0..1), mix=0.5}   mF intro sweep
    air_lift       {t0, t1, db=10}                                  7-15 kHz lift (mF, last 120 ms into the drop)
    bass_boost     {t0, t1=None, db=6, rise=0.1}                    fSMk: the bass-heavier chorus after the silence
    silence        {t0, t1}                                         hard-edged digital silence (fSMk, 91tp's mute)
    song_cut       {at, to}   jump: from output time `at` the song plays from file time `to` (15 ms crossfade)
    version        {reverb_mix=0.16, rt60=1.4, bass_db=4}           the "reverb + bass boost" edit-audio treatment
  overlays (added on top of the song, levelled against the song's local RMS)
    impact         {at, t_peak_db=11, bright=1.0, sub_db=0}         hero impact: boom + bright top (the "shing")
    riser          {drop, dur=0.7, swell_s=0.44}                    i4f: sub riser + noise swell ending on the drop
    whoosh5k       {cut, band_db=10, peak_after=0.12}               i4f: 5 kHz noise swell riding the cut
    sub_swell_cut  {cut, dur=0.3}  +  sub_boom {at, level_db=3}     zawB: sub into the flash cut, boom trailing it
    sub_swell      {t0, drop}                                       mF: 10-15 Hz rumble -> 30-50 Hz glide into the drop
    sparkle        {at}                                             upHh: bell chime on a sticker pop
    glide          {t0, t1, f0=None, semis=8.5, level_db=-3.2}      91tp: the song muted, one mono note gliding up
    wash           {t0, t1, level_db=-6.5}                          yUui: a wide noisy wash filling the song's stop
    clip           {path, clip_t0, clip_t1, at, level_db, ...}      a shot's own sound (core/edit/clip_audio.py)

Engine hooks (engine.build_audio):  pre(eng, seg, sr, music) before the fades (song_cut, version),
post(eng, seg, sr) -> (seg, overlay_layer) after the music treatments.  Plan keys:
  audio_design   "auto" (the preset's measured schedule + its clip-audio rule) | "none";  absent -> "auto" when
                 plan["sfx"] == "auto" (what planners.py writes with --preset), else "none" (old plans unchanged)
  song_fx        a list of ops (always applied) | "auto" | "none"
  clip_events    a list of clip ops (always applied)
  song_fx_variant  force a reference variant (e.g. "i4f", "m3lv", "zawB", "yUui", "YH", "TG", "eEPZ", "rfsj", "91tp")

CLI:
  python core/edit/song_fx.py --selftest
  python core/edit/song_fx.py --plan plan.json --out song.wav [--design auto]     the song track alone, with the report
  python core/edit/song_fx.py --make-version SONG --rate 0.91 --reverb --bass-db 4 --out slowed.m4a   (pipeline side:
                 "slowed + reverb + bass" versions must exist BEFORE beat analysis; the engine cannot change the tempo)
"""
import argparse
import json
import math
import os
import sys

import numpy as np
from scipy import signal

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

SR = 44100


# ------------------------------------------------------------------------------------------------ helpers

def _db(x):
    return 20.0 * math.log10(max(1e-12, float(x)))


def _idx(t, sr, n):
    return int(min(max(0, round(t * sr)), n))


def rms_env(x, sr, win=0.02, hop=0.005):
    """Short-term RMS (mono mean of channels) -> (times, rms)."""
    m = x.mean(axis=0) if x.ndim == 2 else x
    h, w = max(1, int(hop * sr)), max(1, int(win * sr))
    c = np.cumsum(np.pad(m.astype(np.float64) ** 2, (1, 0)))
    starts = np.arange(0, max(1, len(m) - w), h)
    e = np.sqrt(np.maximum(0.0, (c[starts + w] - c[starts]) / w))
    return (starts + w / 2) / sr, e


def local_rms(x, sr, t, half=0.3):
    n = x.shape[-1]
    a, b = _idx(t - half, sr, n), _idx(t + half, sr, n)
    if b - a < 16:
        return 1e-6
    seg = x[..., a:b]
    return float(np.sqrt(np.mean(seg ** 2)) + 1e-9)


def band_rms(x, sr, t0, t1, lo, hi):
    n = x.shape[-1]
    a, b = _idx(t0, sr, n), _idx(t1, sr, n)
    if b - a < 256:
        return 1e-9
    m = x[..., a:b].mean(axis=0) if x.ndim == 2 else x[a:b]
    sos = signal.butter(4, [max(10.0, lo), min(hi, sr * 0.45)], 'bandpass', fs=sr, output='sos')
    return float(np.sqrt(np.mean(signal.sosfilt(sos, m) ** 2)) + 1e-12)


def _gain_points(n, sr, pts):
    """Per-sample linear gain from [[t, dB], ...] (linear in dB between points, flat outside)."""
    pts = sorted(pts)
    ts = np.array([p[0] for p in pts], float)
    ds = np.array([p[1] for p in pts], float)
    tt = np.arange(n) / sr
    return 10 ** (np.interp(tt, ts, ds) / 20.0)


def _apply_gain_region(x, sr, pts):
    """Gain curve restricted to the span of its points (samples outside untouched)."""
    n = x.shape[-1]
    a, b = _idx(min(p[0] for p in pts), sr, n), _idx(max(p[0] for p in pts), sr, n)
    if b <= a:
        return
    g = _gain_points(b - a, sr, [[p[0] - a / sr, p[1]] for p in pts])
    x[:, a:b] *= g


def _ramp01(t, a, b):
    """0 before a, 1 after b, raised-cosine between."""
    if b <= a:
        return (t >= a).astype(float)
    u = np.clip((t - a) / (b - a), 0, 1)
    return 0.5 - 0.5 * np.cos(np.pi * u)


def stft_region(x, sr, t0, t1, gain_fn, pad=0.12, nper=2048):
    """Multiply the STFT of x[t0 - pad, t1 + pad] by gain_fn(freqs (F,), abs_times (T,)) -> (F, T) and resynthesise
    in place (the gain must be 1 at the region edges; hann 75 % overlap reconstructs exactly where it is)."""
    n = x.shape[-1]
    a, b = _idx(t0 - pad, sr, n), _idx(t1 + pad, sr, n)
    if b - a < nper:
        b = min(n, a + nper)
        if b - a < nper:
            return
    hop = nper // 4
    f, tt, Z = signal.stft(x[:, a:b], sr, window='hann', nperseg=nper, noverlap=nper - hop, axis=-1)
    G = np.asarray(gain_fn(f, a / sr + tt), float)
    _, y = signal.istft(Z * G[None], sr, window='hann', nperseg=nper, noverlap=nper - hop)
    y = y[:, :b - a]
    if y.shape[1] < b - a:
        y = np.pad(y, ((0, 0), (0, b - a - y.shape[1])))
    # 4 ms crossfade at the edges (numerical safety)
    r = min((b - a) // 4, int(0.004 * sr))
    w = np.ones(b - a)
    w[:r] = np.linspace(0, 1, r)
    w[-r:] = np.linspace(1, 0, r)
    x[:, a:b] = x[:, a:b] * (1 - w) + y * w


def _band_curve(f, lo, hi, soft=0.25):
    """1 inside [lo, hi] Hz, raised-cosine skirts `soft` octaves wide, 0 outside."""
    lf = np.log2(np.maximum(f, 1.0))
    a, b = math.log2(max(1.0, lo)), math.log2(max(1.0, hi))
    up = np.clip((lf - (a - soft)) / soft, 0, 1)
    dn = np.clip(((b + soft) - lf) / soft, 0, 1)
    return (0.5 - 0.5 * np.cos(np.pi * up)) * (0.5 - 0.5 * np.cos(np.pi * dn))


def _place(layer, snd, sr, t_start):
    n = layer.shape[1]
    i0 = int(round(t_start * sr))
    if i0 < 0:
        snd = snd[:, -i0:]
        i0 = 0
    m = min(snd.shape[1], n - i0)
    if m > 0:
        layer[:, i0:i0 + m] += snd[:, :m]


def t_peak_gain(song, snd, sr, t_start, target_db, span=0.3, lo=None, hi=None, frame=0.02):
    """Gain for `snd` placed at t_start so that the peak over 20 ms frames of T = 10 log10((song + snd) / song)
    (optionally inside a band) is target_db: the quantity the AV studies measured on the references
    (general_av_audio T, audio_av_proof.measure_op). Closed form: g^2 = (10^(target/10) - 1) / max(E_snd / E_song)."""
    n = song.shape[-1]
    i0 = int(round(t_start * sr))
    m = min(snd.shape[1], int(span * sr), n - max(0, i0))
    if m <= int(frame * sr) or i0 < 0:
        return 1.0
    a = song[:, i0:i0 + m].mean(axis=0)
    b = snd[:, :m].mean(axis=0)
    if lo or hi:
        sos = signal.butter(4, [max(20.0, lo or 20.0), min(hi or 0.45 * sr, 0.45 * sr)], 'bandpass', fs=sr, output='sos')
        a, b = signal.sosfilt(sos, a), signal.sosfilt(sos, b)
    h = int(frame * sr)
    k = m // h
    Ea = (a[:k * h] ** 2).reshape(k, h).mean(axis=1) + 1e-12
    Eb = (b[:k * h] ** 2).reshape(k, h).mean(axis=1) + 1e-15
    floor = max(float(np.median(Ea)) * 1e-2, 1e-9)            # a silent song frame must not blow the ratio up
    r = float(np.max(Eb / np.maximum(Ea, floor)))
    return math.sqrt(max(0.0, 10 ** (target_db / 10) - 1) / max(r, 1e-12))


def _norm_rms(snd, sr, a=0.0, b=None):
    """RMS of snd over [a, b] seconds of itself."""
    ia, ib = int(a * sr), snd.shape[1] if b is None else int(b * sr)
    return float(np.sqrt(np.mean(snd[:, ia:max(ia + 16, ib)] ** 2)) + 1e-12)


# ------------------------------------------------------------------------------------------------ song edits

def op_hold_swell(x, sr, o):
    t0, cut = float(o.get('t0', 0.0)), float(o['cut']) - float(o.get('lead', 0.0))
    h, s = float(o.get('hold_db', -3.8)), float(o.get('swell_s', 0.18))
    _apply_gain_region(x, sr, [[t0, h], [cut, h], [cut + s, 0.0]])


def op_swell(x, sr, o):
    cut, d, dur = float(o['cut']), float(o.get('depth_db', 8.0)), float(o.get('dur', 0.4))
    _apply_gain_region(x, sr, [[cut - dur - 0.01, 0.0], [cut - dur, -d], [cut, 0.0]])


def op_duck(x, sr, o):
    t0, t1 = float(o['t0']), float(o['t1'])
    d, at, rl = float(o.get('depth_db', -8.0)), float(o.get('attack', 0.03)), float(o.get('release', 0.03))
    at, rl = max(at, 1e-3), max(rl, 1e-3)              # np.interp needs strictly increasing points
    _apply_gain_region(x, sr, [[t0, 0.0 if t0 > 0 else d], [t0 + at, d], [t1, d], [t1 + rl, 0.0]])
    md = float(o.get('mid_db', 0.0) or 0.0)
    if md:
        op_band(x, sr, dict(t0=t0, t1=t1, lo=300, hi=3000, db=md, ramp_in=at, ramp_out=rl))


def op_band(x, sr, o):
    t0, t1, lo, hi, db = float(o['t0']), float(o['t1']), float(o['lo']), float(o['hi']), float(o['db'])
    ri, ro = float(o.get('ramp_in', 0.02)), float(o.get('ramp_out', 0.02))

    def g(f, tt):
        k = _ramp01(tt, t0, t0 + ri) * (1 - _ramp01(tt, t1, t1 + ro))
        B = _band_curve(f, lo, hi, float(o.get('soft', 0.2)))
        return 10 ** ((db * B[:, None] * k[None]) / 20)
    stft_region(x, sr, t0, t1 + ro, g)


def op_lowpass(x, sr, o):
    """Muffle: close over close_s from t_close, hold, open over open_s ending at t_open (+ what the caller wants).
    3-15 kHz goes hi_db, 1-3 kHz mid_db, below 300 Hz untouched (i4f: -26 / -16 dB)."""
    tc, cs = float(o['t_close']), float(o.get('close_s', 0.06))
    to, osd = float(o['t_open']), float(o.get('open_s', 0.25))
    hi_db, mid_db = float(o.get('hi_db', -22.0)), float(o.get('mid_db', -15.0))

    def g(f, tt):
        k = _ramp01(tt, tc, tc + cs) * (1 - _ramp01(tt, to - osd, to))
        prof = np.interp(np.log2(np.maximum(f, 1.0)), np.log2([300, 1000, 3000, 20000]), [0.0, mid_db, hi_db, hi_db])
        return 10 ** ((prof[:, None] * k[None]) / 20)
    stft_region(x, sr, tc, to, g)


def _shelf_eq(y, sr, hi_db, lowmid_db):
    """High shelf above 8 kHz and a 300 Hz-1 kHz cut (the echo's darkened return)."""
    if hi_db:
        sos = signal.butter(2, 7000, 'lowpass', fs=sr, output='sos')
        lp = signal.sosfilt(sos, y, axis=-1)
        y = lp + (y - lp) * 10 ** (hi_db / 20)
    if lowmid_db:
        sos = signal.butter(2, [300, 1000], 'bandpass', fs=sr, output='sos')
        bp = signal.sosfilt(sos, y, axis=-1)
        y = y + bp * (10 ** (lowmid_db / 20) - 1)
    return y


def _zp(y, sr, kind, f, order=4):
    """Zero-phase Butterworth (so band parts subtract exactly)."""
    sos = signal.butter(order, f, kind, fs=sr, output='sos')
    return signal.sosfiltfilt(sos, y, axis=-1)


def _rms(z):
    return float(np.sqrt(np.mean(z ** 2)) + 1e-12)


def op_echo(x, sr, o):
    """Echo throw into a cut (yUui 8.01-8.25 s, eEPZ 19.68-19.89 s): the song alternates with itself `delay` earlier,
    highs (> 2.5 kHz) hi_db and low-mids (300 Hz-1 kHz) lowmid_db UNDER THE DRY SONG'S OWN LEVEL in those bands (so a
    throw over a quiet moment can never add brightness), for `dur`, ending `end_before` before the switch; the dry
    song snaps back. `dry` = the dry share in the throw (yUui dry corr 0.3 -> 0.3, eEPZ 0.6 -> 0.8)."""
    cut = float(o['cut'])
    e1 = cut - float(o.get('end_before', 0.017))
    e0 = e1 - float(o.get('dur', 0.24))
    d = int(float(o.get('delay', 0.12)) * sr)
    n = x.shape[-1]
    a, b = _idx(e0, sr, n), _idx(e1, sr, n)
    if b - a < 64 or a - 3 * d < 0:
        return
    src = x[:, a - 3 * d:b].copy()
    fb = 0.45
    wet = np.zeros_like(src)
    for k in range(1, 4):
        wet[:, k * d:] += (fb ** (k - 1)) * src[:, :src.shape[1] - k * d]
    wet = wet[:, 3 * d:]
    dry = src[:, 3 * d:]
    y = float(o.get('dry', 0.3)) * dry + wet
    # level-match on the lows (< 300 Hz): the throw keeps the song's weight
    y *= min(2.0, _rms(_zp(dry, sr, 'lowpass', 300)) / _rms(_zp(y, sr, 'lowpass', 300)))
    hi_db, lm_db = float(o.get('hi_db', -15.0)), float(o.get('lowmid_db', -7.5))
    # frame by frame (23 ms): the throw's > 2.5 kHz and 300 Hz-1 kHz energy held hi_db / lowmid_db under the DRY song's
    # own energy in that band and frame (an echo repeating a hat into a quiet gap must not brighten the song)
    nper = 1024
    f, _, Zy = signal.stft(y, sr, nperseg=nper, noverlap=nper * 3 // 4, axis=-1)
    _, _, Zd = signal.stft(dry, sr, nperseg=nper, noverlap=nper * 3 // 4, axis=-1)
    G = np.ones(Zy.shape[1:], float)                       # (F, T)
    for band, db in ((f > 2500, hi_db), ((f > 300) & (f < 1000), lm_db)):
        ey = (np.abs(Zy[:, band]) ** 2).sum(axis=(0, 1)) + 1e-12
        ed = (np.abs(Zd[:, band]) ** 2).sum(axis=(0, 1))
        ed = signal.convolve(ed, np.ones(3) / 3, mode='same')
        gb = 10 ** (db / 20) * np.minimum(1.0, np.sqrt(ed / ey))
        G[band] = gb[None, :]
    _, y = signal.istft(Zy * G[None], sr, nperseg=nper, noverlap=nper * 3 // 4)
    y = y[:, :dry.shape[1]]
    if y.shape[1] < dry.shape[1]:
        y = np.pad(y, ((0, 0), (0, dry.shape[1] - y.shape[1])))
    r_in, r_out = int(0.012 * sr), int(0.006 * sr)
    w = np.ones(b - a)
    w[:r_in] = np.linspace(0, 1, r_in)
    w[-r_out:] = np.linspace(1, 0, r_out)
    x[:, a:b] = dry * (1 - w) + y * w


def op_dropout(x, sr, o):
    cut = float(o['cut'])
    t0 = cut + float(o.get('start', 0.10))
    t1 = t0 + float(o.get('dur', 0.20))
    d, bb = float(o.get('depth_db', -14.0)), float(o.get('bass_db', 6.0))

    def g(f, tt):
        k = _ramp01(tt, t0, t0 + 0.012) * (1 - _ramp01(tt, t1, t1 + 0.03))
        hi = 1 - _band_curve(f, 1.0, 90.0, 0.3)
        low = _band_curve(f, 25.0, 90.0, 0.3)
        prof = d * hi + bb * low
        return 10 ** ((prof[:, None] * k[None]) / 20)
    stft_region(x, sr, t0, t1 + 0.03, g)


def op_bass_cut(x, sr, o):
    cut = float(o['cut'])
    dur = float(o.get('dur', 0.35))
    op_band(x, sr, dict(t0=cut - dur, t1=cut - 0.01, lo=25, hi=90, db=float(o.get('depth_db', -10.0)), ramp_in=0.03,
                        ramp_out=0.01, soft=0.3))
    sl = float(o.get('slam_db', 0.0) or 0.0)
    if sl:   # YH: the bass comes back heavier than before on the switch (the chorus 'slam'), settling over slam_s
        op_band(x, sr, dict(t0=cut, t1=cut + float(o.get('slam_s', 0.35)), lo=25, hi=90, db=sl, ramp_in=0.01,
                            ramp_out=0.15, soft=0.3))


def op_fade(x, sr, o):
    t0, t1 = float(o['t0']), float(o['t1'])
    db0, db1 = float(o.get('db0', 0.0)), float(o.get('db1', -15.0))
    pts = [[t0, db0], [t1, db1]]
    if o.get('quad') is not None:
        # optional accelerating fade (remake round 4 of _sX2: -6 / -9 / -12 dB per 0.2 s): the dB drop follows
        # (1 - q) u + q u^2 of the way from db0 to db1
        q = float(o['quad'])
        pts = [[t0 + (t1 - t0) * u, db0 + (db1 - db0) * ((1 - q) * u + q * u * u)] for u in np.linspace(0, 1, 21)]
    n = x.shape[-1]
    _apply_gain_region(x, sr, pts)
    if o.get('then_out', False):
        a = _idx(t1, sr, n)
        g1 = 10 ** (db1 / 20)
        x[:, a:] *= g1 * np.linspace(1, 0, n - a) ** 2 if n > a else 1.0


def op_slowdown(x, sr, o):
    import sfx as SFX
    SFX.tape_stop(x, sr, float(o['t0']), float(o.get('dur', 0.18)), float(o.get('power', 1.5)))


def op_flanger(x, sr, o):
    """Feed-forward comb (notches at odd multiples of 1/(2 tau)) with a time-varying delay: the mF intro sweep."""
    t0, t1 = float(o['t0']), float(o['t1'])
    n = x.shape[-1]
    a, b = _idx(t0, sr, n), _idx(t1, sr, n)
    if b - a < 256:
        return
    curve = o.get('tau_ms') or MF_FLANGER_TAU
    us = np.array([c[0] for c in curve], float)
    ms = np.array([c[1] for c in curve], float)
    u = np.linspace(0, 1, b - a)
    tau = np.interp(u, us, ms) * 1e-3 * sr                      # samples
    mix = float(o.get('mix', 0.5))
    idx = np.arange(a, b) - tau
    for c in range(x.shape[0]):
        delayed = np.interp(idx, np.arange(n), x[c])
        y = (1 - mix) * x[c, a:b] + mix * delayed
        r = min((b - a) // 4, int(0.01 * sr))
        w = np.ones(b - a)
        w[-r:] = np.linspace(1, 0, r)                           # dry again at the end (the grade pop)
        x[c, a:b] = x[c, a:b] * (1 - w) + y * w


# mF: tau falls ~linearly 1.71 -> 0.06 ms over the first 3.2 s of 5.98, rests near 0.05, climbs back to 0.89 ms
MF_FLANGER_TAU = [[0.0, 1.9], [0.084, 1.71], [0.167, 1.32], [0.251, 1.02], [0.334, 0.61], [0.418, 0.34], [0.502, 0.12],
                  [0.535, 0.06], [0.72, 0.05], [0.786, 0.23], [0.836, 0.41], [0.92, 0.74], [0.97, 0.89], [1.0, 0.9]]


def op_air_lift(x, sr, o):
    t0, t1, db = float(o['t0']), float(o['t1']), float(o.get('db', 10.0))

    def g(f, tt):
        k = _ramp01(tt, t0, t1) * (1 - _ramp01(tt, t1, t1 + 0.01))
        return 10 ** ((db * _band_curve(f, 7000, 15000, 0.3)[:, None] * k[None]) / 20)
    stft_region(x, sr, t0, t1 + 0.01, g)


def op_bass_boost(x, sr, o):
    t0 = float(o['t0'])
    n = x.shape[-1]
    t1 = float(o.get('t1') or n / sr)
    db, rise = float(o.get('db', 6.0)), float(o.get('rise', 0.1))
    a, b = _idx(t0, sr, n), _idx(t1, sr, n)
    if b - a < 64:
        return
    sos = signal.butter(2, 110, 'lowpass', fs=sr, output='sos')
    lp = signal.sosfilt(sos, x[:, a:b], axis=-1)
    k = np.clip(np.arange(b - a) / max(1, rise * sr), 0, 1)
    x[:, a:b] += lp * (10 ** (db / 20) - 1) * k


def op_silence(x, sr, o):
    import sfx as SFX
    SFX.silence_gap(x, sr, float(o['t0']), float(o['t1']), float(o.get('fade', 0.004)))


def op_version(x, sr, o):
    """The 'reverb + bass boost' edit-audio treatment (ZkWQ / cj12 play such versions): a short stereo room and a
    +bass_db low shelf below 90 Hz, applied to the whole song. The tempo change of a 'slowed' version cannot be done
    here (beats are already placed): use make_version() before the analysis."""
    import sfx as SFX
    mix, rt = float(o.get('reverb_mix', 0.16)), float(o.get('rt60', 1.4))
    y = SFX._reverb(x, sr, rt60=rt, mix=mix, seed=11, bright=0.35)[:, :x.shape[1]]
    bd = float(o.get('bass_db', 4.0))
    if bd:
        sos = signal.butter(2, 90, 'lowpass', fs=sr, output='sos')
        y = y + signal.sosfilt(sos, y, axis=-1) * (10 ** (bd / 20) - 1)
    x[:] = y * (math.sqrt(np.mean(x ** 2) + 1e-12) / math.sqrt(np.mean(y ** 2) + 1e-12))


def op_song_cut(x, sr, o, music, music_start):
    """From output time `at` the song plays from file time `to` (a whole number of bars away, so the grid holds);
    15 ms equal-power crossfade centred on `at` (the references' lock resumes within 120 ms of the switch)."""
    if music is None:
        return
    n = x.shape[-1]
    at, to = float(o['at']), float(o['to'])
    k = _idx(at, sr, n)
    j = int(round(to * sr))
    r = int(0.0075 * sr)
    if k - r < 0 or j - r < 0:
        return
    new = music[:, j - r:j - r + (n - (k - r))]
    if new.shape[1] < n - (k - r):
        new = np.pad(new, ((0, 0), (0, n - (k - r) - new.shape[1])))
    th = np.linspace(0, math.pi / 2, 2 * r)
    tail = x[:, k - r:k + r] * np.cos(th) + new[:, :2 * r] * np.sin(th)
    x[:, k - r:k + r] = tail
    x[:, k + r:] = new[:, 2 * r:2 * r + (n - k - r)]


def op_breath_riser(x, sr, o):
    """_sX2 5.0-5.56 s (cinematic_slowmo hero reveal), built from the song itself when the song has no breath there:
    the reference's song goes near-silent for ~0.47 s (-33..-41 dB rel) while a noise swell's high-band share rises
    23 -> 93 %, then the full band hits on the switch.  Here: the `gate_s` before `cut` is gated to gate_db (closing
    over `release`, reopening in 8 ms onto the cut), and a REVERSED slice of the song's own downbeat (the riser_s after
    the cut), high-passed at `hp` Hz, swells into the gap peaking `peak_before` s before the cut at peak_db relative to
    the song's RMS over the 0.4 s after the cut (rising from onset_s before the peak with time constant tau).
    {cut, gate_s=0.45, gate_db=-35, release=0.06, riser_s=0.45, hp=3000, peak_db=-14, peak_before=0.04, onset_s=0.3,
    tau=0.08}"""
    n = x.shape[-1]
    cut = float(o['cut'])
    gs, gdb, rel = float(o.get('gate_s', 0.45)), float(o.get('gate_db', -35.0)), float(o.get('release', 0.06))
    rs, hp = float(o.get('riser_s', 0.45)), float(o.get('hp', 3000.0))
    pk_db, pb = float(o.get('peak_db', -14.0)), float(o.get('peak_before', 0.04))
    c = _idx(cut, sr, n)
    a = _idx(cut - gs, sr, n)
    if c - a < int(0.1 * sr) or c + int(rs * sr) > n:
        return
    ref = float(np.sqrt(np.mean(x[:, c:min(n, c + int(0.4 * sr))] ** 2)) + 1e-9)
    sl = x[:, c:c + int(rs * sr)][:, ::-1].copy()
    sl = signal.sosfilt(signal.butter(4, hp, 'highpass', fs=sr, output='sos'), sl, axis=-1)
    m = sl.shape[1]
    t = np.arange(m) / sr
    t_pk = rs - pb                                       # the riser ends at the cut; its peak pb before it
    env = np.where(t <= t_pk, np.exp(-(t_pk - t) / max(0.02, float(o.get('tau', 0.08)))),
                   np.exp(-(t - t_pk) / 0.012))
    env *= _ramp01(t, t_pk - float(o.get('onset_s', 0.30)), t_pk - float(o.get('onset_s', 0.30)) + 0.05)
    sl *= env
    w = int(0.02 * sr)
    k0 = max(0, int(t_pk * sr) - w)
    pk = float(np.sqrt(np.mean(sl[:, k0:k0 + 2 * w] ** 2)) + 1e-12)
    sl *= ref * 10 ** (pk_db / 20.0) / pk
    # the gate: the song's own content in the gap drops to gate_db, reopening in 8 ms onto the switch
    tt = np.arange(c - a) / sr
    gdl = 10 ** (gdb / 20.0)
    g = 1.0 - (1.0 - gdl) * _ramp01(tt, 0.0, rel) * (1.0 - _ramp01(tt, (c - a) / sr - 0.004, (c - a) / sr))
    x[:, a:c] *= g
    s0 = c - m
    if s0 < 0:
        sl = sl[:, -s0:]
        s0 = 0
    x[:, s0:c] += sl[:, :c - s0]


def op_gap_drop(x, sr, o):
    """cinematic_slowmo 'gap then drop' (critic, round 10: _sX2's song falls to -46 dB for ~0.4 s, then the first slow-motion clip
    slams in on the hit +4 dB louder than before the gap).  The song is cut `gap_s` before `cut` (the hit's onset) with a
    fade under 30 ms to `gate_db`, a bright reversed-slice wash fills the gap (optional), and the song returns on the hit
    (`return_s` ramp ending at the onset) `step_db` louder, settling over `step_tau`.  Only used where the song has no
    sung word in the gap (the planner checks).  {cut, gap_s=0.45, gate_db=-46, fade_s=0.025, return_s=0.008, step_db=4,
    step_tau=0.9, riser=True, riser_db=-20, pre_db=0}"""
    n = x.shape[-1]
    cut, gap = float(o['cut']), float(o.get('gap_s', 0.45))
    fade_s, ret = float(o.get('fade_s', 0.025)), float(o.get('return_s', 0.008))
    gdb = float(o.get('gate_db', -46.0))
    c, a = _idx(cut, sr, n), _idx(cut - gap, sr, n)
    if c - a < int(0.1 * sr):
        return
    pre_db = float(o.get('pre_db', 0.0))
    if pre_db:
        # _sX2 holds the song down through the words on black (-13 dB) and the first clip lands ~7 dB over it: the intro
        # plays pre_db under the body level
        x[:, :a] *= 10 ** (pre_db / 20.0)
    if o.get('riser', True):
        op_breath_riser(x, sr, dict(cut=cut, gate_s=gap, gate_db=gdb, release=fade_s, riser_s=min(0.45, gap),
                                    hp=float(o.get('hp', 4500.0)), peak_db=float(o.get('riser_db', -20.0)),
                                    peak_before=0.02, tau=0.08, onset_s=min(0.3, gap - 0.05)))
    else:
        tt = np.arange(c - a) / sr
        g = 1.0 - (1.0 - 10 ** (gdb / 20.0)) * _ramp01(tt, 0.0, fade_s) * (1.0 - _ramp01(tt, (c - a) / sr - ret, (c - a) / sr))
        x[:, a:c] *= g
    sd, tau = float(o.get('step_db', 4.0)), float(o.get('step_tau', 0.9))
    if sd:
        m = min(n, c + int(5 * tau * sr)) - c
        if m > 0:
            u = np.arange(m) / sr
            x[:, c:c + m] *= 1.0 + (10 ** (sd / 20.0) - 1.0) * np.exp(-u / tau)


def op_filter_sweep(x, sr, o):
    """A MOVING cutoff (the owner's "filters on the audio"; transition-sound.md): kind 'lp' | 'hp', pts [[t, fc_hz],
    ...] with the cutoff log-interpolated between points, `order` poles (2 = 12 dB / oct, 4 = 24 dB / oct); before the
    first and after the last point the filter fades out over `edge` s (a lp at >= 18 kHz / a hp at <= 20 Hz is open)."""
    pts = sorted([(float(a), float(b)) for a, b in o['pts']])
    kind = o.get('kind', 'lp')
    n2 = 2 * int(o.get('order', 4))
    edge = float(o.get('edge', 0.03))
    ts = np.array([p[0] for p in pts])
    lf = np.log(np.array([max(10.0, p[1]) for p in pts]))

    def g(f, tt):
        fc = np.exp(np.interp(tt, ts, lf))[None]
        ff = np.maximum(f, 1.0)[:, None]
        r = (ff / fc) if kind == 'lp' else (fc / ff)
        G = 1.0 / np.sqrt(1.0 + r ** n2)
        w = _ramp01(tt, ts[0] - edge, ts[0]) * (1 - _ramp01(tt, ts[-1], ts[-1] + edge))
        return 1.0 - w[None] * (1.0 - G)
    stft_region(x, sr, ts[0] - edge, ts[-1] + edge, g)


SONG_OPS = {'hold_swell': op_hold_swell, 'swell': op_swell, 'duck': op_duck, 'band': op_band, 'lowpass': op_lowpass,
            'echo': op_echo, 'dropout': op_dropout, 'bass_cut': op_bass_cut, 'fade': op_fade, 'slowdown': op_slowdown,
            'flanger': op_flanger, 'air_lift': op_air_lift, 'bass_boost': op_bass_boost, 'silence': op_silence,
            'version': op_version, 'breath_riser': op_breath_riser, 'filter_sweep': op_filter_sweep, 'gap_drop': op_gap_drop}
PRE_OPS = {'song_cut', 'version'}


# ------------------------------------------------------------------------------------------------ overlays

def _gen(name, **kw):
    import sfx as SFX
    return SFX.GENERATORS[name](**kw)


def ov_impact(layer, song, sr, o):
    """Hero impact (cj12 26.98 / 40.58 / 41.82 s, m3lv drop, i4f drop): broadband +10 dB over the song for
    100-200 ms, sub-heavy, 3-15 kHz +12..+30 dB: the bright top is the 'shing'. Level: the impact's first 150 ms RMS
    = the song's local RMS + level_db (edit/song +10 dB -> +9.5 dB)."""
    at = float(o['at'])
    if o.get('anchor', True):
        # the references' impacts coincide with the song's own hit after the switch (peak -13..+47 ms from the switch,
        # the hit ~57 ms after it): land on the song's attack when there is one within 150 ms of the cut
        # (only a hit within anchor_max of the switch: a later one would drag the impact off the picture, so the
        # impact then keeps its place and precedes the song's hit, as cj12's impacts precede its kick by 5-65 ms)
        on = drum_onsets(song, sr, float(o.get('cut', at)) - 0.015, float(o.get('cut', at)) + float(o.get('anchor_max', 0.06)))
        if len(on):
            at = float(on[0]) - 0.005
            o['at_song_hit'] = round(at, 4)
    snd, sync = _gen('hero_impact', dur=float(o.get('dur', 0.8)), intensity=float(o.get('intensity', 0.6)),
                     brightness=float(o.get('bright', 1.0)), seed=int(o.get('seed', 0)), sr=sr)
    if o.get('sub_db'):                                   # m3lv's impacts carry far less sub (+17..+22 vs +42..+47 dB)
        sos = signal.butter(2, 100, 'lowpass', fs=sr, output='sos')
        snd = snd + signal.sosfilt(sos, snd, axis=-1) * (10 ** (float(o['sub_db']) / 20) - 1)
    tt_ = float(o.get('tail_tau', 0.0) or 0.0)
    if tt_ > 0:  # a shorter tail than the generator's (i4f / m3lv: T back near 0 dB ~80-120 ms after the peak)
        tn = np.arange(snd.shape[1]) / sr
        snd = snd * np.exp(-np.maximum(0.0, tn - sync - 0.04) / tt_)[None]
    dr = float(o.get('drive', 0.0) or 0.0)
    if dr > 0:   # soft saturation: more body under the -14 LUFS / -2 dBTP limiter (the references are clipped masters)
        snd = np.tanh(dr * snd / (np.max(np.abs(snd)) + 1e-12)) / math.tanh(dr)
    # levelled against the music the impact lands IN (a pre-drop break is quiet, the drop is not); calibrated with
    # audio_av_proof.measure_op on the references: peak T_all +10.3..+12.3 dB, > +3 dB for 140-260 ms
    tgt = float(o.get('t_peak_db', 11.0))
    g = t_peak_gain(song, snd, sr, at - sync, tgt, span=0.3)
    _place(layer, snd * g, sr, at - sync)
    return dict(t_peak_target_db=tgt, at_placed=round(at, 4))


def ov_riser(layer, song, sr, o):
    """i4f 17.70 s, re-measured band by band (audio_av_proof, edit vs aligned song, 20 ms): the 'riser' is a SUB
    riser (25-90 Hz +13 -> +53 dB over the last ~0.5 s) with the 90-300 Hz band joining in the last 150 ms (+2 ->
    +16 dB); mids and highs stay flat until the impact.  So: a 32 -> 69 Hz sine sweep with an exponential level plus a
    low (< 250 Hz) noise swell over the last 0.2 s, levelled so the broadband T over the last 100 ms is `bb_db`."""
    drop = float(o['drop'])
    dur = float(o.get('dur', 0.7))
    n = int(dur * sr)
    t = np.arange(n) / sr
    f = 32 * 2 ** (1.1 * (t / dur) ** 1.5)                # 32 -> 69 Hz
    sub = np.tanh(1.6 * np.sin(2 * np.pi * np.cumsum(f) / sr)) * np.exp(-5.8 * (1 - t / dur))
    rng = np.random.default_rng(int(o.get('seed', 0)) + 17)
    nz = signal.sosfilt(signal.butter(4, [90, 250], 'bandpass', fs=sr, output='sos'), rng.standard_normal(n))
    nz *= np.exp(-np.maximum(0.0, (dur - 0.2) - t) / 0.03) * np.exp(-3.0 * (1 - t / dur))
    nz /= (np.sqrt(np.mean(nz[-int(0.1 * sr):] ** 2)) + 1e-9)
    sub /= (np.sqrt(np.mean(sub[-int(0.1 * sr):] ** 2)) + 1e-9)
    mono = sub + float(o.get('low_mix', 0.45)) * nz
    mono[-int(0.004 * sr):] *= np.linspace(1, 0, int(0.004 * sr))
    y = np.stack([mono, mono])
    tail = int(0.1 * sr)
    g = t_peak_gain(song, y[:, -tail:], sr, drop - 0.1, float(o.get('bb_db', 8.0)), span=0.1)
    _place(layer, y * g, sr, drop - dur)
    return {}


def ov_whoosh5k(layer, song, sr, o):
    """i4f: 3-12 kHz noise centred ~5 kHz, attack 130-450 ms, peak 50-190 ms after the switch, -10 dB within
    20-200 ms; its 3-12 kHz level band_db over the song's own 3-12 kHz at the cut (8-20 measured; 10 = quiet)."""
    cut = float(o['cut'])
    pk = float(o.get('peak_after', 0.12))
    att, dec = float(o.get('attack', 0.25)), float(o.get('decay', 0.09))
    dur = att + pk + dec * 3
    snd, sync = _gen('cut_whoosh', dur=dur, intensity=0.5, brightness=0.7, seed=int(o.get('seed', 0)), sr=sr,
                     peak_s=att + pk, decay_s=dec)
    # i4f residual bursts: +8..+20 dB over the context in 3-12 kHz (frame peak); 12 = the quiet side of it
    g = t_peak_gain(song, snd, sr, cut + pk - sync, float(o.get('band_db', 12.0)), span=snd.shape[1] / sr, lo=3000, hi=12000)
    _place(layer, snd * g, sr, cut + pk - sync)
    return {}


def ov_sub_swell_cut(layer, song, sr, o):
    """zawB: a sub swell over 240-340 ms ending ON the flash cut (then the boom trails)."""
    cut, dur = float(o['cut']), float(o.get('dur', 0.3))
    n = int(dur * sr)
    t = np.arange(n) / sr
    s = np.sin(2 * np.pi * np.cumsum(46 + 12 * t / dur) / sr) * np.exp(-4.5 * (1 - t / dur))
    s[-int(0.003 * sr):] *= np.linspace(1, 0, int(0.003 * sr))
    y = np.stack([s, s])
    g = t_peak_gain(song, y, sr, cut - dur, float(o.get('sub_db', 30.0)), span=dur, lo=25, hi=90)
    if o.get('bb_db') is not None:
        g = min(g, t_peak_gain(song, y, sr, cut - dur, float(o['bb_db']), span=dur))
    _place(layer, y * g, sr, cut - dur)
    return {}


def ov_sub_boom(layer, song, sr, o):
    """zawB: sub-only boom (25-90 Hz +25..+49 dB, other bands +4..+6 dB) for 100-200 ms trailing a flash cut."""
    at = float(o['at'])
    snd, sync = _gen('sub_boom', dur=0.45, intensity=0.6, brightness=0.05, seed=int(o.get('seed', 0)), sr=sr)
    sos = signal.butter(4, 120, 'lowpass', fs=sr, output='sos')
    snd = signal.sosfilt(sos, snd, axis=-1)
    g = t_peak_gain(song, snd, sr, at - sync, float(o.get('sub_db', 30.0)), span=0.2, lo=25, hi=90)
    if o.get('bb_db') is not None:
        g = min(g, t_peak_gain(song, snd, sr, at - sync, float(o['bb_db']), span=0.2))
    _place(layer, snd * g, sr, at - sync)
    return {}


def ov_sub_swell(layer, song, sr, o):
    """mF 6.10 -> 7.98 s: a 10-15 Hz rumble rising (-26 -> -21 dBFS) and a 30-50 Hz glide over the last 0.2 s
    (-15 dBFS) into the drop, whose kick sits at -9.3 dBFS; levels are kept relative to the drop."""
    t0, drop = float(o['t0']), float(o['drop'])
    dur = max(0.3, drop - t0)
    n = int(dur * sr)
    t = np.arange(n) / sr
    f = np.where(t < dur - 0.2, 12.0 + 3.0 * t / dur, 30 + 20 * np.clip((t - (dur - 0.2)) / 0.2, 0, 1))
    tone = np.sin(2 * np.pi * np.cumsum(f) / sr)
    lvl_db = np.where(t < dur - 0.2, -9.0 + 3.0 * (t / max(1e-3, dur - 0.2)), -5.3)
    ref = float(np.max(np.abs(song[:, _idx(drop, sr, song.shape[1]):_idx(drop + 0.15, sr, song.shape[1])])) + 1e-9)
    y = tone * ref * 10 ** (lvl_db / 20)
    y[:int(0.2 * sr)] *= np.linspace(0, 1, int(0.2 * sr))
    y[-int(0.003 * sr):] *= np.linspace(1, 0, int(0.003 * sr))
    _place(layer, np.stack([y, y]), sr, t0)
    return {}


def ov_sparkle(layer, song, sr, o):
    """upHh 5.175 s: onset 25 ms before the accent, partials 3.36 / 6.93 / 10.56 kHz, -13 dB over 0.35 s, mono,
    RMS over its window 4-7 dB under the mix."""
    at = float(o['at'])
    snd, sync = _gen('sparkle_chime', seed=int(o.get('seed', 0)), sr=sr)
    # the reference chime SWELLS in: onset 25 ms before the accent, peak ~25-50 ms after it (a 60 ms rise)
    ka = int(float(o.get('attack', 0.06)) * sr)
    snd[:, :ka] *= (0.5 - 0.5 * np.cos(np.linspace(0, np.pi, ka)))[None]
    g = t_peak_gain(song, snd, sr, at - 0.025 - sync, float(o.get('band_db', 13.0)), span=0.55, lo=2500, hi=16000)
    _place(layer, snd * g, sr, at - 0.025 - sync)
    return {}


def ov_glide(layer, song, sr, o):
    """91tp: the song muted, one mono low note gliding up exponentially (25 st/s, 94 -> 154 Hz) ending on the drop,
    3.2 dB under the drop's level (+12.5 dB over the ducked build)."""
    t0, t1 = float(o['t0']), float(o['t1'])
    dur = t1 - t0
    f0 = float(o.get('f0') or 94.0)
    semis = float(o.get('semis', 8.5))
    n = int(dur * sr)
    t = np.arange(n) / sr
    f = f0 * 2 ** ((semis / 12.0) * (t / dur))
    ph = 2 * np.pi * np.cumsum(f) / sr
    tone = np.sin(ph) + 0.28 * np.sin(2 * ph) + 0.12 * np.sin(3 * ph)
    tone = np.tanh(1.3 * tone)
    tone *= np.clip(t / 0.012, 0, 1) * np.clip((dur - t) / 0.006, 0, 1)
    ref = local_rms(song, sr, t1 + 0.5, 0.5)
    tone *= ref * 10 ** (float(o.get('level_db', -3.2)) / 20) / (np.sqrt(np.mean(tone ** 2)) + 1e-9)
    _place(layer, np.stack([tone, tone]), sr, t0)
    return dict(f0=round(f0, 1), f1=round(f0 * 2 ** (semis / 12), 1))


def ov_wash(layer, song, sr, o):
    """yUui 5.43-5.72 s: a wide (side ~ mid), noise-like wash, centroid ~1.8 kHz with 200-300 Hz thumps, -6.5 dB re
    the music median, sustained, running exactly to the drop switch."""
    t0, t1 = float(o['t0']), float(o['t1'])
    snd, _ = _gen('wash', dur=t1 - t0, seed=int(o.get('seed', 0)), sr=sr)
    # width as measured on yUui with audio_av_proof: side/mid -7.3 dB (wide but centred)
    mid, side = (snd[0] + snd[1]) / 2, (snd[0] - snd[1]) / 2
    side *= math.sqrt(np.mean(mid ** 2) / (np.mean(side ** 2) + 1e-12)) * 10 ** (float(o.get('side_db', -7.3)) / 20)
    snd = np.stack([mid + side, mid - side])
    _, env = rms_env(song, sr, 0.3, 0.05)
    ref = float(np.median(env[env > 1e-5])) if np.any(env > 1e-5) else 0.05
    g = ref * 10 ** (float(o.get('level_db', -6.5)) / 20) / _norm_rms(snd, sr)
    _place(layer, snd * g, sr, t0)
    return {}


def band_level_gain(song, snd, sr, t_peak_song, sync, target_db, lo, hi, ctx=0.3, frame=0.03):
    """Gain for `snd` (whose peak sits `sync` s into it, placed so the peak lands on t_peak_song) so that its loudest
    30 ms frame inside [lo, hi] Hz is target_db over the SONG's power in the same band averaged over +-ctx s: the
    quantity transition_sound_events.residual_blobs measured on the references (added level re the music, same band)."""
    n = song.shape[-1]
    sos = signal.butter(4, [max(20.0, lo), min(hi, 0.45 * sr)], 'bandpass', fs=sr, output='sos')
    a, b = _idx(t_peak_song - ctx, sr, n), _idx(t_peak_song + ctx, sr, n)
    if b - a < 256:
        return 0.0
    ps = float(np.mean(signal.sosfilt(sos, song[:, a:b].mean(axis=0)) ** 2)) + 1e-12
    y = signal.sosfilt(sos, snd.mean(axis=0))
    h = max(16, int(frame * sr))
    k = len(y) // h
    if k < 1:
        return 0.0
    pk = float(np.max((y[:k * h] ** 2).reshape(k, h).mean(axis=1))) + 1e-15
    return math.sqrt(ps * 10 ** (float(target_db) / 10) / pk)


_CTX = {}


def _level_ref(song):
    """the song as it sounds after the song ops (post() stashes the treated track; same shape) else the dry song"""
    tr = _CTX.get('treated')
    return tr if tr is not None and getattr(tr, 'shape', None) == song.shape else song


def song_ceiling(song, sr, at, span=1.5, rel_db=-40.0):
    """Highest frequency where the song's spectrum around `at` is within rel_db of its 3-8 kHz level (its bandwidth;
    15.8-16.5 kHz for the codec'd songs of the references)."""
    n = song.shape[-1]
    a, b = _idx(at - span, sr, n), _idx(at + span, sr, n)
    if b - a < 8192:
        return 16000.0
    f, P = signal.welch(song[:, a:b].mean(axis=0), sr, nperseg=8192)
    ref = float(np.median(P[(f > 3000) & (f < 8000)])) + 1e-20
    ok = f[10 * np.log10(P / ref + 1e-20) > rel_db]
    return float(np.clip(ok.max() if len(ok) else 16000.0, 6000.0, 20000.0))


def ov_noise_crash(layer, song, sr, o):
    """The Blur Flow 'shing' / whoosh (transition-sound.md): sfx.noise_crash with its PEAK on `at`, levelled so
    its 1.5-16 kHz peak is hi_db over the song's own 1.5-16 kHz level (S2rz -2..+4 dB, cj12 +3.6 dB)."""
    at = float(o['at'])
    kw = {k: float(o[k]) for k in ('rise', 'attack', 't6', 't12', 'cen0', 'cen1', 'width_oct', 'glide_s') if k in o}
    snd, sync = _gen('noise_crash', seed=int(o.get('seed', 0)), sr=sr, **kw)
    # never brighter than the song: low-passed at the song's own ceiling around `at` (the references are
    # codec-limited at 15.8-16.5 kHz; a crash reaching 20 kHz reads as a pasted sample)
    fc = min(float(o.get('ceiling_hz') or song_ceiling(song, sr, at)), 0.45 * sr)
    snd = signal.sosfiltfilt(signal.butter(8, fc, 'lowpass', fs=sr, output='sos'), snd, axis=-1)
    g = band_level_gain(_level_ref(song), snd, sr, at, sync, float(o.get('hi_db', 0.0)), 1500.0, 16000.0)
    _place(layer, snd * g, sr, at - sync)
    return dict(gain=round(g, 5))


def ov_sub_hit(layer, song, sr, o):
    """The Blur Flow sub hit / sub glide (transition-sound.md): sfx.sub_hit with its PEAK on `at`, levelled so its
    25-150 Hz peak is lo_db over the song's own 25-150 Hz level (S2rz +9 dB after flash cuts, +10 dB at the entry,
    +2..+4 dB under swells)."""
    at = float(o['at'])
    kw = {k: float(o[k]) for k in ('f0', 'f1', 'tau_f', 'rise', 't6', 't12', 'body', 'click') if k in o}
    snd, sync = _gen('sub_hit', seed=int(o.get('seed', 0)), sr=sr, **kw)
    g = band_level_gain(_level_ref(song), snd, sr, at, sync, float(o.get('lo_db', 6.0)), 25.0, 150.0)
    _place(layer, snd * g, sr, at - sync)
    return dict(gain=round(g, 5))


def ov_noise_bed(layer, song, sr, o):
    """A band-limited noise bed (the reference's hyperspace air under its intro card): fades in over `rise` s, holds,
    out over `fall` s; level_db is re the song's median RMS; lo / hi Hz (default 3.5-14 kHz); the spectrum tilts
    (-3 dB / oct) so it reads as air, not hiss.  No transient, no click (raised-cosine edges)."""
    t0, t1 = float(o['t0']), float(o['t1'])
    rise, fall = float(o.get('rise', 0.4)), float(o.get('fall', 0.25))
    n = max(64, int((t1 - t0) * sr))
    rng = np.random.default_rng(int(o.get('seed', 5)))
    x = rng.standard_normal((2, n))
    lo, hi = float(o.get('lo', 3500.0)), min(float(o.get('hi', 14000.0)), 0.45 * sr)
    x = signal.sosfilt(signal.butter(4, [lo, hi], 'bandpass', fs=sr, output='sos'), x, axis=-1)
    tt = np.arange(n) / sr
    env = np.clip(tt / max(rise, 1e-3), 0, 1) * np.clip(((t1 - t0) - tt) / max(fall, 1e-3), 0, 1)
    env = 0.5 - 0.5 * np.cos(np.pi * env)
    x = x * env
    _, e = rms_env(song, sr, 0.3, 0.05)
    ref = float(np.median(e[e > 1e-5])) if np.any(e > 1e-5) else 0.05
    x *= ref * 10 ** (float(o.get('level_db', -24.0)) / 20) / (float(np.sqrt(np.mean(x ** 2))) + 1e-12)
    _place(layer, x, sr, t0)
    return {}


OVERLAYS = {'noise_bed': ov_noise_bed, 'impact': ov_impact, 'riser': ov_riser, 'whoosh5k': ov_whoosh5k, 'sub_swell_cut': ov_sub_swell_cut,
            'sub_boom': ov_sub_boom, 'sub_swell': ov_sub_swell, 'sparkle': ov_sparkle, 'glide': ov_glide, 'wash': ov_wash,
            'noise_crash': ov_noise_crash, 'sub_hit': ov_sub_hit}


# ------------------------------------------------------------------------------------------------ song analysis

def drum_onsets(song, sr, t0=0.0, t1=None):
    """Kick / snare attacks of the song track (sub+low band flux and a mid 'crack' flux), seconds."""
    m = song.mean(axis=0)
    a = _idx(t0, sr, len(m))
    b = _idx(t1 if t1 is not None else len(m) / sr, sr, len(m))
    m = m[a:b]
    out = []
    for lo, hi, thr in ((35, 150, 2.0), (150, 2500, 2.2)):
        sos = signal.butter(2, [lo, hi], 'bandpass', fs=sr, output='sos')
        y = signal.sosfilt(sos, m)
        hop = sr // 200
        e = np.sqrt(np.convolve(y ** 2, np.ones(hop * 2) / (hop * 2), mode='same')[::hop] + 1e-12)
        d = np.maximum(0, np.diff(20 * np.log10(e + 1e-7), prepend=0))
        k = min(41, len(d) if len(d) % 2 else len(d) - 1)
        if k < 5:
            continue
        med = signal.medfilt(d, k) + 1e-6
        pk, _ = signal.find_peaks(d, height=np.maximum(3.0, thr * med + 1.5), distance=16)
        out += list(pk / 200.0 + t0)
    out = sorted(out)
    merged = []
    for t in out:
        if not merged or t - merged[-1] > 0.03:
            merged.append(t)
    return np.array(merged)


def cut_hits(song, sr, cuts, win=(-0.10, 0.15)):
    """For each picture switch: the song's drum attack nearest in [cut - 0.10, cut + 0.15] (ms after the switch)."""
    on = drum_onsets(song, sr)
    rows = []
    for c in cuts:
        near = on[(on >= c + win[0]) & (on <= c + win[1])]
        rows.append(None if not len(near) else round(1000 * float(near[np.argmin(np.abs(near - c - 0.03))] - c), 1))
    return rows


def song_stops(song, sr, t0, t1, min_len=0.12, rel_db=-25.0):
    """Stretches in [t0, t1] where the song itself stops (20 ms RMS under its median - 25 dB for >= min_len)."""
    tt, e = rms_env(song, sr, 0.02, 0.005)
    ok = e > 1e-6
    if not np.any(ok):
        return []
    med = float(np.median(e[ok]))
    low = (20 * np.log10(e + 1e-9) < _db(med) + rel_db) & (tt >= t0) & (tt <= t1)
    out, s = [], None
    for i, v in enumerate(low):
        if v and s is None:
            s = tt[i]
        if (not v or i == len(low) - 1) and s is not None:
            if tt[i] - s >= min_len:
                out.append((float(s), float(tt[i])))
            s = None
    return out


def root_hz(song, sr, t0, t1, lo=70.0, hi=140.0):
    """The strongest bass pitch in [t0, t1], folded into [lo, hi) (the song's root under the drop)."""
    n = song.shape[-1]
    a, b = _idx(t0, sr, n), _idx(t1, sr, n)
    if b - a < sr // 8:
        return None
    m = song[:, a:b].mean(axis=0) * np.hanning(b - a)
    sp = np.abs(np.fft.rfft(m, 1 << 18))
    f = np.fft.rfftfreq(1 << 18, 1 / sr)
    band = (f > 35) & (f < 300)
    fpk = float(f[band][np.argmax(sp[band])])
    while fpk < lo:
        fpk *= 2
    while fpk >= hi:
        fpk /= 2
    return fpk


# ------------------------------------------------------------------------------------------------ scheduling

def preset_of(plan, style_name=None):
    pid = plan.get('preset') or (plan.get('planner') or {}).get('preset')
    if pid:
        return pid
    try:
        import sfx as SFX
        return SFX.STYLE_DEFAULT.get(style_name or '')
    except Exception:
        return None


def design_mode(plan):
    m = plan.get('audio_design')
    if m is None:
        m = 'auto' if plan.get('sfx') in ('auto', True, 'on') else 'none'
    return 'auto' if m in ('auto', True, 'on', 'measured') else 'none'


def audio_rules(pid):
    try:
        import presets as PS
        return (getattr(PS, 'AUDIO_AV', {}) or {}).get(pid) or {}
    except Exception:
        return {}


def _cuts(eng):
    """[(i, t0, transition, role, flash_like, shake_like)] for every shot after the first."""
    out = []
    for i, s in enumerate(eng.shots):
        if i == 0:
            continue
        tr = str(s.get('transition_in') or 'hard')
        accs = [getattr(a, 'kind', a.get('kind') if isinstance(a, dict) else '') for a in (s.get('_acc') or [])]
        flash = ('flash' in tr) or ('bleach' in tr) or tr.startswith('white')
        shake = any(k in ('shake',) for k in accs) or 'shake' in tr
        out.append(dict(i=i, t=float(s['t0']), tr=tr, role=s.get('role') or 'any', flash=flash, shake=shake,
                        beat=s.get('beat')))
    return out


def _is_down(eng, t, tol=0.08):
    d = np.asarray(eng.downbeats, float)
    return bool(len(d) and np.min(np.abs(d - t)) < tol)


def _drop(eng):
    d = eng.plan.get('drop')
    try:
        d = float(d)
    except (TypeError, ValueError):
        d = getattr(eng, 'drop_t', None)
    if d is None or not (0.3 < float(d) < eng.duration - 0.3):
        return None
    return float(d)


def _drop_cut(eng, cuts, drop):
    if drop is None:
        return None
    near = [c for c in cuts if abs(c['t'] - drop) < 0.25]
    return min(near, key=lambda c: abs(c['t'] - drop)) if near else None


def _heroes(eng, cuts, drop, k=3, min_gap=3.0):
    """Hero cuts: the drop cut, then flash / hero-role cuts on downbeats, spaced >= min_gap."""
    pool = []
    dc = _drop_cut(eng, cuts, drop)
    if dc:
        pool.append((0, dc))
    for c in cuts:
        if dc and c is dc:
            continue
        score = (2 if c['role'] in ('hero', 'drop') else 0) + (2 if c['flash'] else 0) + (1 if c['shake'] else 0) + \
                (1 if _is_down(eng, c.get('beat') or c['t']) else 0)
        if score >= 2:
            pool.append((10 - score, c))
    pool.sort(key=lambda p: (p[0], p[1]['t']))
    out = []
    for _, c in pool:
        if all(abs(c['t'] - o['t']) >= min_gap for o in out):
            out.append(c)
        if len(out) >= k:
            break
    return sorted(out, key=lambda c: c['t'])


def _card_end(eng):
    ends = [float(o.get('t1', 0)) for o in eng.plan.get('overlays') or []
            if o.get('type') in ('music_card3d', 'music_card', 'title_card', 'postcard_intro', 'text_on_black')
            and float(o.get('t0', 0)) < 0.5]
    return max(ends) if ends else None


# ------------------------------------------------------------------------------------------------ transition sounds
# Measured with stem separation (core/edit/refs/transition_sound_study.py / transition_sound_events.py; findings and
# numbers: core/edit/refs/transition-sound.md / .json).  Levels are the added sound's loudest 30 ms frame over the
# song's own level IN THE SAME BAND (hi = 1.5-16 kHz, lo = 25-150 Hz); times are re the picture switch (s).
TRANSITION_SOUND = {
    # S2rzMsl0N7k (blur_flow 's2rz'): its white-flash cuts cycle through four treatments (18.0 none, 18.567 breakdown,
    # 24.8 sub hit, 26.067 swell; the same again +9.97 s), the drop gets a swell, a breakdown ends on a blur cut with
    # a bright crash
    's2rz': dict(
        # rise = the -12 dB onset before the peak, t6 / t12 after it; cen* = generator centres calibrated so the measured
        # 1.5-16 kHz centroid matches (width 0.8 oct: centroid ~ 1.2 x centre + 500 Hz).  hi_db / lo_db are synthesis
        # levels CALIBRATED (2 iterations, transition-sound.md 'proof') so that the identical stem measure returns the
        # reference's numbers on our render (the measure under-reads a hit that Demucs files next to the song's own bass)
        drop_swell=[dict(op='noise_crash', dt=0.17, rise=0.15, t6=0.05, t12=0.11, cen0=2150.0, cen1=2800.0,
                         width_oct=0.8, hi_db=7.4),
                    dict(op='sub_hit', dt=0.12, rise=0.22, f0=30.0, f1=90.0, t6=0.06, t12=0.12, lo_db=6.7)],
        flash_cycle=('none', 'breakdown', 'sub_hit', 'swell'),
        swell=[dict(op='noise_crash', dt=0.07, rise=0.18, t6=0.06, t12=0.115, cen0=2300.0, cen1=2750.0, width_oct=0.8,
                    hi_db=1.6),
               dict(op='sub_hit', dt=0.01, rise=0.21, f0=30.0, f1=90.0, t6=0.07, t12=0.15, lo_db=6.2)],
        sub_hit=[dict(op='sub_hit', dt=0.145, f0=90.0, f1=45.0, tau_f=0.04, t6=0.025, t12=0.045, lo_db=14.8)],
        # whip-slide cuts (21.733 / 31.70, the template repeat): a quiet swish, -12 dB onset 160-190 ms before its peak
        # at +80..+110 ms, gone 30-40 ms after it, centroid 3-4.5 kHz, -7.4 / -0.9 dB re the highs (level offset from
        # the swell's calibration, not iterated itself)
        whip=[dict(op='noise_crash', dt=0.095, rise=0.175, t6=0.018, t12=0.035, cen0=2600.0, cen1=2200.0, width_oct=0.8,
                   hi_db=-0.5)],       # the two reference whips read -7.4 / -0.9 dB: this lands midway (~ -4 dB)
        # round 6 (per-50 ms band diff against the aligned reference): the reference's breakdown is a BASS cut only (sub -16 dB for
        # ~0.45 s, +0.38 .. +0.83 s after the flash); the whole-mix duck (-12) and the -40 dB bass cut made the song 'break'
        breakdown=dict(bass_from=0.38, bass_db=-14.0, bass_ramp_out=0.1, duck_from=0.43, duck_to=0.93, duck_db=0.0,
                       min_len=0.8, max_len=1.6, bass_len=0.45),
        crash=[dict(op='noise_crash', dt=0.055, rise=0.18, t6=0.14, t12=0.465, cen0=6150.0, cen1=3550.0,
                    width_oct=0.8, hi_db=3.1),
               dict(op='sub_hit', dt=0.08, rise=0.14, f0=50.0, f1=43.0, t6=0.08, t12=0.185, lo_db=-0.8)],
        entry=dict(ops=[dict(op='noise_crash', dt=0.207, rise=0.13, t6=0.03, t12=0.07, cen0=2400.0, cen1=1400.0,
                             width_oct=0.8, hi_db=-1.8),
                        dict(op='sub_hit', dt=0.117, f0=64.0, f1=43.0, tau_f=0.03, t6=0.02, t12=0.04, lo_db=21.3,
                             click=0.05)]),
        # measured reference numbers (stem residual, dB re the music in the same band; ms re the switch), for reports
        measured=dict(entry=dict(low=[10.4, 120], high=[1.3, 210, 3162]), drop_swell=dict(low=[3.9, 120], high=[3.8, 170, 2891]),
                      crash=dict(low=[3.3, 80], high=[-0.6, 55, 7600]), sub_hit=dict(low=[8.9, 145]),
                      swell=dict(low=[2.3, 10], high=[0.6, 70, 3060]), whip=dict(high=[-4.2, 95, 3600])),
    ),
    # cj12R_cW2ac (blur_flow 'cj12'): 7 of 12 white-flash cuts carry a 70 ms noise tick (centroid 6.3 -> 5.1 kHz,
    # +3.6 dB) over a slow sub swell (+2..+3 dB, -0.2 .. +0.55 s); the 3 hero impacts stay (ov_impact)
    # the tick's fine structure follows the song at that instant (corr 0.45-0.50; 0 = noise): the song's own 3-16 kHz
    # pumped +8 dB (residual +3.6 dB re the song's highs = amplitude x2.5) for 70 ms, 30-100 ms after the switch
    'cj12': dict(
        flash_share=0.58,
        flash_band=dict(dt0=0.03, dt1=0.10, lo=3000.0, hi=16000.0, db=10.0, ramp_in=0.01, ramp_out=0.03),
        flash=[dict(op='sub_hit', dt=0.25, rise=0.26, f0=45.0, f1=60.0, t6=0.2, t12=0.5, lo_db=5.0)],
    ),
    # i4fSruIkkxk (hype_typography 'i4f'): the song has no drums in the verse (-62 dB) and the reference carries a
    # 40-55 Hz 808 boom after each blur-bridge cut that opens a held shot (5.60 / 8.03 / 11.03 / 13.67 s): peak
    # +90..+280 ms, +8..+12 dB over the song's own sub (+2.6..+4.4 dB re the music's 20-150 Hz), a long tail (-12 dB
    # after ~0.8 s).  The per-cut 5 kHz whoosh of general-av.md is NOT confirmed by the stems (added high-band sound at 4
    # of 42 cuts = the drop and the two muffles; 17 % vs a 5 % null) -> no whoosh5k.  Riser + impact on the drop and the
    # two low-pass muffles stay as they were (general-av.md 2.4; the stems confirm both).
    'i4f': dict(
        verse_boom=[dict(op='sub_hit', dt=0.2, f0=56.0, f1=42.0, tau_f=0.09, t6=0.12, t12=0.75, lo_db=11.0, body=0.0,
                         click=0.0)],
        verse_boom_min_hold=2.0,
        measured=dict(verse_boom=dict(low=[3.4, 190], hz='56 -> 42', over_song_sub_db=[9.9, 8.3, 10.1, 12.1])),
    ),
    # mFHp6Dd2ISk (kpop_postcard_drop 'mF'): an added noise wash on the grade pop that ends the postcard intro (6.0 s;
    # fine-structure corr with the song -0.29): onset +80, peak +140, -6 dB +190, -12 dB +270 ms, centroid 4.4 -> 3.4 kHz,
    # -1.4 dB re the music's highs.  Nothing at its cuts / zooms / whips (every-cut test -0.6 dB, origin corr 0.45 / 0.39
    # vs 0.35 at other hits); the intro flanger is a song filter (corr ~0.6), already the 'flanger' op.
    'mF': dict(
        pop_crash=[dict(op='noise_crash', dt=0.14, attack=0.06, t6=0.05, t12=0.17, cen0=3400.0, cen1=2600.0,
                        width_oct=0.8, hi_db=1.4)],
        measured=dict(pop_crash=dict(high=[-1.4, 140, 4429], on_ms=80, end12_ms=270)),
    ),
    # yUuiSey5kmE (char_quick_velocity 'yUui'): one added broadband impact on the zoom-blur cut that ends the intro,
    # 0.7 s before the drop (5.0 s; origin corr -0.03 / -0.06; the intro plays clip audio, so it may be the clip's own
    # hit): lows +10.2 dB @ +150, mids +7.0 @ +130, highs +8.2 @ +110 ms (onset on the cut, -12 dB by +200..+220 ms),
    # centroid 4.5 -> 5.6 kHz.  Its cuts / zooms / whips otherwise carry nothing (hat leakage of a partial lock only).
    'yUui': dict(
        intro_impact=[dict(op='noise_crash', dt=0.08, rise=0.1, t6=0.06, t12=0.09, cen0=4200.0, cen1=4800.0,
                           width_oct=0.9, hi_db=5.0),
                      dict(op='sub_hit', dt=0.125, f0=110.0, f1=55.0, tau_f=0.04, t6=0.04, t12=0.07, lo_db=18.0,
                           body=0.2, click=0.0)],
        window=(0.4, 1.2),
        measured=dict(intro_impact=dict(low=[10.2, 150], mid=[7.0, 130], high=[8.2, 110, 4520])),
    ),
    # ZkWQloS4jtM (blur_flow 'ZkWQ'): added energy at its cuts / zooms / flashes occurs at the drum-onset null rate
    # (0.40 vs 0.38): nothing added; what sounds like a whoosh is the slowed + reverb version's own tails
    'ZkWQ': dict(),
}


def _tsnd(ops_spec, t, seed):
    out = []
    for k, sp in enumerate(ops_spec):
        o = {kk: v for kk, v in sp.items() if kk != 'dt'}
        o['at'] = round(t + float(sp.get('dt', 0.0)), 4)
        o['seed'] = int(seed) * 7 + k
        out.append(o)
    return out


def _is_flash_cut(c):
    return str(c.get('tr') or '').startswith(('flash_white', 'white_flash', 'flash_in'))


def transition_sound_ops(variant, cuts, duration, drop=None, entry=None, seed=0):
    """The measured transition sounds of one reference flavour as song_fx ops (explicit-plan form, also used by the
    auto schedule).  cuts: [{'t': switch_s, 'tr': transition name}, ...]; drop / entry: seconds or None (entry = the
    first body shot after a held intro).  A flavour whose reference adds nothing returns []."""
    R = TRANSITION_SOUND.get(variant) or {}
    if not R:
        return []
    rng = np.random.default_rng((int(seed) * 31 + 7) & 0x7fffffff)
    cuts = sorted([dict(c) for c in cuts if 0.3 < float(c['t']) < duration - 0.3], key=lambda c: float(c['t']))
    ops = []
    if variant == 's2rz':
        if entry is not None and 1.0 < entry < duration - 1.0:
            # (no extra low-pass: the reference's residual shows none before its entry; its intro filter is the
            # template's own band / lowpass ops)
            ops += _tsnd(R['entry']['ops'], entry, 1)
        if drop is not None and 0.5 < drop < duration - 0.5:
            ops += _tsnd(R['drop_swell'], drop, 2)
        for k, c in enumerate([c for c in cuts if str(c.get('tr') or '').startswith('whip')
                               and (drop is None or c['t'] > drop + 0.3)]):
            ops += _tsnd(R['whip'], float(c['t']), 400 + k)
        flashes = [c for c in cuts if _is_flash_cut(c) and (drop is None or c['t'] > drop + 0.3)]
        cyc = R['flash_cycle']
        for k, c in enumerate(flashes):
            kind = cyc[k % len(cyc)]
            t = float(c['t'])
            if kind == 'swell':
                ops += _tsnd(R['swell'], t, 100 + k)
            elif kind == 'sub_hit':
                ops += _tsnd(R['sub_hit'], t, 100 + k)
            elif kind == 'breakdown':
                b = R['breakdown']
                nxt = [d for d in cuts if b['min_len'] <= d['t'] - t <= b['max_len']]
                if not nxt:
                    continue
                ret = float(nxt[0]['t'])
                ops.append(dict(op='band', t0=round(t + b['bass_from'], 3),
                                t1=round(min(ret, t + b['bass_from'] + b['bass_len']) if b.get('bass_len') else ret + b['bass_ramp_out'], 3),
                                lo=20.0, hi=150.0, db=b['bass_db'], ramp_in=0.05, ramp_out=b['bass_ramp_out'],
                                why='breakdown: bass out'))
                d0, d1 = t + b['duck_from'], min(t + b['duck_to'], ret - 0.1)
                if d1 - d0 > 0.15 and b.get('duck_db'):
                    ops.append(dict(op='duck', t0=round(d0, 3), t1=round(d1, 3), depth_db=b['duck_db'], attack=0.05,
                                    release=0.05, why='breakdown: duck'))
                ops += _tsnd(R['crash'], ret, 200 + k)
    elif variant == 'i4f':
        # verse booms: blur-bridge cuts before the drop that open a shot held >= verse_boom_min_hold s
        for k, c in enumerate(cuts):
            nxt = [d['t'] for d in cuts if d['t'] > c['t']]
            hold = (nxt[0] if nxt else duration) - c['t']
            if (drop is None or c['t'] < drop - 1.0) and hold >= R['verse_boom_min_hold'] and \
                    str(c.get('tr') or '').startswith('blur'):
                ops += _tsnd(R['verse_boom'], float(c['t']), 500 + k)
    elif variant == 'cj12':
        for k, c in enumerate([c for c in cuts if _is_flash_cut(c)]):
            if rng.random() < R['flash_share']:
                fb, t = R['flash_band'], float(c['t'])
                ops.append(dict(op='band', t0=round(t + fb['dt0'], 3), t1=round(t + fb['dt1'], 3), lo=fb['lo'],
                                hi=fb['hi'], db=fb['db'], ramp_in=fb['ramp_in'], ramp_out=fb['ramp_out'],
                                why='cj12 flash tick: the song\'s highs pumped'))
                ops += _tsnd(R['flash'], t, 300 + k)
    for o in ops:
        o.setdefault('why', 'transition sound (%s)' % variant)
    return ops


def schedule(eng, song, sr, variant=None):
    """The measured song-fx schedule for the engine's preset -> (variant, ops, notes)."""
    plan = eng.plan
    pid = preset_of(plan, eng.style.get('name'))
    rules = audio_rules(pid)
    rng = np.random.default_rng((int(eng.seed) * 7 + 13) & 0x7fffffff)
    variants = rules.get('variants') or []
    variant = variant or plan.get('song_fx_variant')
    if not variant and pid == 'blur_flow' and (plan.get('planner') or {}).get('flow_variant') == 's2rz':
        variant = 's2rz'
    if not variant and variants:
        names = [v[0] for v in variants]
        w = np.array([float(v[1]) for v in variants], float)
        variant = names[int(rng.choice(len(names), p=w / w.sum()))]
    ops, notes = [], []
    D = eng.duration
    cuts = _cuts(eng)
    drop = _drop(eng)
    dc = _drop_cut(eng, cuts, drop)
    P = float(getattr(eng, 'P', 0.5) or 0.5)
    after = [c for c in cuts if drop is None or c['t'] > drop + 0.5]

    def spaced(cands, n, gap):
        out = []
        for c in cands:
            if all(abs(c['t'] - o['t']) >= gap for o in out):
                out.append(c)
            if len(out) >= n:
                break
        return out

    if pid == 'fx_typography':
        ce = _card_end(eng)
        first = next((c for c in cuts if ce is not None and c['t'] >= ce - 0.1), None)
        if first:
            # measured on WGvt with audio_av_proof: hold -3.5..-3.8 dB, swell 10-90 % in 180 ms starting ~20 ms
            # before the cut frame (a linear-dB ramp of 225 ms has its 10-90 % in 180 ms)
            ops.append(dict(op='hold_swell', t0=0.0, cut=first['t'], lead=0.043, hold_db=-3.8, swell_s=0.225))
            notes.append('WGvt: intro card held -3.8 dB, +3.8 dB swell over 180 ms from the first cut')
    elif pid == 'blur_flow' and variant == 'cj12':
        for h in _heroes(eng, cuts, drop, k=3, min_gap=4.0):
            off = float(rng.choice([-0.013, 0.013, 0.047]))
            ops.append(dict(op='impact', at=h['t'] + off, cut=h['t'], bright=1.0, intensity=0.9, sub_db=4.0, seed=h['i']))
        heroes_t = [o['cut'] for o in ops if o.get('op') == 'impact']
        ops += transition_sound_ops('cj12', [c for c in cuts if all(abs(c['t'] - h) > 0.3 for h in heroes_t)], D,
                                    drop=drop, seed=int(eng.seed))
        if (eng.style.get('end') or {}).get('kind') in ('fade_black', 'white_black', 'bw_fade', 'dip_black_chain') or \
                any(o.get('type') == 'fade' and o.get('dir') == 'out' for o in plan.get('overlays') or []):
            # cj12 outro: highs tilted down ~4 dB against the lows (the -11.5 dB of the study includes its dips / fade,
            # which the engine's own fade already makes)
            ops.append(dict(op='lowpass', t_close=D - 1.7, close_s=0.22, t_open=D + 1.0, open_s=0.01, hi_db=-4.5,
                            mid_db=-2.5))
        notes.append('cj12: impacts (boom + bright top) on up to 3 hero cuts; nothing on ordinary cuts')
    elif pid == 'blur_flow' and variant == 's2rz':
        if not any(o.get('op') in ('noise_crash', 'sub_hit') for o in _explicit(plan, 'song_fx')):
            ops += transition_sound_ops('s2rz', cuts, D, drop=drop, seed=int(eng.seed))
        notes.append('S2rz: white-flash cuts cycle none / breakdown (bass out, duck, bright crash on the return cut) / '
                     'sub hit / swell; a swell into the drop (transition-sound.md)')
    elif pid == 'blur_flow':
        notes.append('ZkWQ: nothing added, nothing edited on ordinary cuts (re-sequencing needs an explicit song_cut; '
                     'transition-sound.md: added energy at its cuts = the drum-onset null)')
    elif pid == 'hype_typography':
        flashes = [c for c in after if c['flash']]
        if variant == 'i4f':
            # 2026-09-29 (transition-sound.md): no per-cut whoosh (not in the stems); sub booms after the held verse cuts
            ops += transition_sound_ops('i4f', cuts, D, drop=drop, seed=int(eng.seed))
            if dc and dc['t'] > 1.0:
                ops.append(dict(op='riser', drop=dc['t'], dur=0.7, bb_db=10.0, low_mix=0.4, seed=3))
                # i4f's drop impact is short: T +12..+14 dB from the switch to +100 ms, back to +2 dB by +160 ms
                # (levels set so that AFTER the -14 LUFS / -2 dBTP master it reads about +11 dB broadband with the
                # bright top +10 / +17 dB: the reference's +14 / +20 / +20 come from a -6.5 LUFS clipped master)
                ops.append(dict(op='impact', at=dc['t'] + 0.08, cut=dc['t'], anchor=False, t_peak_db=18.0, bright=2.0,
                                drive=4.0, dur=0.3, intensity=0.3, tail_tau=0.05, seed=4))
            # i4f: the muffle closes right after the previous cut, holds 0.3-1.3 s and is fully open 60-90 ms after
            # the flash cut; so it goes before flash cuts that follow a held shot (>= 0.75 s since the last cut)
            prev_t = {c['i']: (cuts[k - 1]['t'] if k else 0.0) for k, c in enumerate(cuts)}
            room = sorted([c for c in (flashes or after) if c['t'] - prev_t[c['i']] >= 0.75],
                          key=lambda c: -(c['t'] - prev_t[c['i']]))
            for h in sorted(spaced(room, 2, 4.0), key=lambda c: c['t']):
                tc = max(prev_t[h['i']] + 0.06, h['t'] - 1.3)
                osd = float(np.clip(0.3 * (h['t'] - tc), 0.08, 0.34))
                ops.append(dict(op='lowpass', t_close=tc, close_s=0.06, t_open=h['t'] + 0.085 + 0.5 * osd, open_s=osd,
                                hi_db=-20.0, mid_db=-19.0, cut=h['t']))
            notes.append('i4f: 808 sub booms after the held verse cuts, riser + impact on the drop, low-pass muffle '
                         'opening 60-90 ms after up to 2 flash cuts (no per-cut whoosh: transition-sound.md)')
        elif variant == 'zawB':
            for c in spaced(flashes or after, 5, 1.2):
                # levels: broadband +5..+11.5 dB (swell) / +8..+10 dB (boom) over the song, measured on zawB with
                # audio_av_proof (the +36..+50 dB sub figures only say the song has little sub there)
                ops.append(dict(op='sub_swell_cut', cut=c['t'], dur=0.3, sub_db=42.0, bb_db=7.5))
                on = drum_onsets(song, sr, c['t'] + 0.1, c['t'] + 0.46)
                ops.append(dict(op='sub_boom', at=float(on[0]) if len(on) else c['t'] + 0.2, sub_db=44.0, bb_db=9.0,
                                seed=c['i']))
            notes.append('zawB: sub swell into the flash cuts, a sub boom trailing them 100-450 ms')
        else:  # m3lv
            for h in _heroes(eng, cuts, drop, k=3, min_gap=2.5):
                ops.append(dict(op='impact', at=h['t'] + float(rng.uniform(0.06, 0.107)), cut=h['t'], anchor=False,
                                bright=0.3, sub_db=-20.0, dur=0.45, drive=3.0, t_peak_db=12.0, tail_tau=0.07,
                                seed=h['i']))
            last = -9.0
            for c in cuts:
                if c['t'] - last >= 0.6 and c['t'] > 0.5 and rng.random() < 0.6:
                    ops.append(dict(op='swell', cut=c['t'], depth_db=5.0, dur=0.4))
                    last = c['t']
            notes.append('m3lv: impacts on 3 hero cuts (+60..+107 ms), the song swelling +5 dB over 0.4 s into 60 % '
                         'of the cuts')
    elif pid == 'char_quick_velocity':
        chorus = [c for c in after if _is_down(eng, c.get('beat') or c['t'])] or after
        if variant == 'yUui':
            if drop is not None:
                # the impact on the cut that ends the intro, 0.4-1.2 s before the drop (transition-sound.md)
                ya, yb = TRANSITION_SOUND['yUui']['window']
                pre = [c for c in cuts if ya <= drop - c['t'] <= yb]
                if pre:
                    ops += _tsnd(TRANSITION_SOUND['yUui']['intro_impact'], pre[-1]['t'], 700)
                for s0, s1 in song_stops(song, sr, drop - 0.8, drop + 0.05):
                    sw = dc['t'] if dc else drop
                    if s0 < sw:
                        ops.append(dict(op='wash', t0=s0, t1=sw, level_db=-5.8, seed=5, clip_first=True))
                        notes.append(f'yUui: the song\'s own stop {s0:.2f}-{s1:.2f} filled up to the drop switch')
                        break
            picks = spaced([c for c in chorus if c['t'] > (drop or 0) + 1.5], 2, 2.5)
            if picks:
                ops.append(dict(op='echo', cut=picks[0]['t'], dur=0.24, delay=0.12, end_before=0.017, hi_db=-13.0,
                                lowmid_db=-3.0, dry=0.3))
            if len(picks) > 1:
                ops.append(dict(op='duck', t0=picks[1]['t'] - 0.24, t1=picks[1]['t'] - 0.03, depth_db=-5.0,
                                attack=0.02, release=0.02))
            ops.append(dict(op='fade', t0=max(0.0, D - 1.6), t1=D, db0=0.0, db1=-11.0))
            notes.append('yUui: echo throw ending 17 ms before a chorus cut, a -5 dB duck ending 30 ms before '
                         'another, fade to -11 dB at the end')
        elif variant == 'YH':
            # measured on YH with audio_av_proof (edit vs aligned song, 20 ms frames): 25-90 Hz dips ~0.42 s ending
            # 30-50 ms before the switch while the whole mix sags ~5 dB, then the chorus hit slams back on the cut
            for c in spaced(chorus, 4, 1.4):
                ops.append(dict(op='bass_cut', cut=c['t'] - 0.03, dur=0.4, depth_db=-8.0, slam_db=15.0, slam_s=0.4))
                ops.append(dict(op='duck', t0=c['t'] - 0.43, t1=c['t'] - 0.03, depth_db=-3.0, attack=0.06, release=0.02))
            if drop is not None and drop > 3.0:
                ops.append(dict(op='fade', t0=0.0, t1=drop, db0=-22.0, db1=-4.0))
                ops.append(dict(op='fade', t0=drop, t1=drop + 0.02, db0=-4.0, db1=0.0))
            ops.append(dict(op='fade', t0=max(0.0, D - 2.0), t1=D, db0=0.0, db1=-10.0, then_out=False))
            notes.append('YH: bass (25-90 Hz) -8 dB and the mix -3 dB for 400 ms before up to 4 chorus cuts, the bass '
                         'slamming back (+15 dB for 0.4 s) on the switch; long fade-in to the drop')
        else:  # TG
            pk = spaced(chorus, 1, 1.0)
            if pk:
                ops.append(dict(op='dropout', cut=pk[0]['t'], start=0.137, dur=0.2, depth_db=-12.0, bass_db=10.0))
            ops.append(dict(op='fade', t0=max(0.0, D - 1.6), t1=D, db0=0.0, db1=-18.0))
            notes.append('TG: one mids/highs drop-out with the bass ringing 137 ms after a chorus cut; -18 dB end ride')
    elif pid == 'char_soft_romance':
        if variant == 'eEPZ':
            picks = spaced(after or cuts, 4, 2.0)
            for c in picks[:4]:
                ops.append(dict(op='dropout', cut=c['t'], start=0.10, dur=float(rng.uniform(0.15, 0.25)),
                                depth_db=float(rng.uniform(-19, -10)), bass_db=float(rng.uniform(5, 12))))
            rest = [c for c in (after or cuts) if all(abs(c['t'] - p['t']) > 1.0 for p in picks)]
            if rest:
                ops.append(dict(op='echo', cut=rest[len(rest) // 2]['t'], dur=0.21, delay=0.09, end_before=0.172,
                                hi_db=-12.0, lowmid_db=-6.0, dry=0.8))
            ops.append(dict(op='slowdown', t0=max(0.0, D - 0.18), dur=0.18))
            notes.append('eEPZ: >90 Hz drop-outs 100 ms after 4 cuts while the bass rings, one echo throw, 180 ms '
                         'slowdown at the end')
        else:  # rfsj
            ops.append(dict(op='fade', t0=max(0.0, D - 2.0), t1=D, db0=0.0, db1=-15.0, then_out=False))
            notes.append('rfsj: the drumless song alone at the white-outs; -15 dB end ride')
    elif pid == 'dance_smooth_velocity' and variant == '91tp' and dc is not None and dc['t'] > 2.5:
        d0 = drop if drop is not None else dc['t']          # the glide ends on the song's drop downbeat (91tp 6.05 s)
        ops.append(dict(op='duck', t0=d0 - 2.05, t1=d0 - 0.42, depth_db=-4.0, attack=0.25, release=0.01))
        ops.append(dict(op='band', t0=d0 - 2.05, t1=d0 - 0.42, lo=500, hi=20000, db=-6.0, ramp_in=0.25, ramp_out=0.01))
        ops.append(dict(op='silence', t0=d0 - 0.42, t1=d0 - 0.01, fade=0.004))
        f0 = root_hz(song, sr, d0, d0 + 1.0, 80.0, 160.0)
        ops.append(dict(op='glide', t0=d0 - 0.42, t1=d0 - 0.03, f0=f0, semis=8.5, level_db=-3.2))
        notes.append('91tp: build ducked -5.7 dB and darkened, the song muted 0.4 s and replaced by one note gliding '
                     'up ~8.5 st (25 st/s) into the drop')
    elif pid == 'kpop_postcard_drop' and drop is not None and drop > 2.5:
        downs = [d for d in eng.downbeats if d <= drop - 1.5]
        pop = max(downs) if downs else drop - 2.0
        ops.append(dict(op='flanger', t0=0.0, t1=pop, mix=0.5))
        ops += _tsnd(TRANSITION_SOUND['mF']['pop_crash'], pop, 600)       # the wash on the grade pop (transition-sound.md)
        ops.append(dict(op='sub_swell', t0=pop + 0.12, drop=drop))
        ops.append(dict(op='air_lift', t0=drop - 0.12, t1=drop, db=26.0))
        for c in cuts:
            if c['tr'].startswith('cutout'):
                ops.append(dict(op='duck', t0=c['t'] - 0.30, t1=c['t'] - 0.01, depth_db=-6.0, mid_db=-7.5,
                                attack=0.06, release=0.04))
        ops.append(dict(op='lowpass', t_close=D - 0.6, close_s=0.45, t_open=D + 1.0, open_s=0.01, hi_db=-18.0,
                        mid_db=-6.0))
        ops.append(dict(op='fade', t0=D - 0.12, t1=D, db0=0.0, db1=-40.0))
        eng._grade_pop = pop
        notes.append('mF: flanger over the intro to the grade pop, sub swell + 120 ms air lift into the drop, duck '
                     'before the cutout reveal, highs rolled off and a 0.12 s fade at the end')
    elif pid == 'kpop_stage_push':
        for o in plan.get('overlays') or []:
            if o.get('type') == 'sticker' and o.get('kind') in ('hearts', 'sparkles', 'heart_frame'):
                ops.append(dict(op='sparkle', at=float(o['t0']), band_db=13.0))
        ops.append(dict(op='fade', t0=D - 0.4, t1=D, db0=0.0, db1=-40.0))
        notes.append('upHh: the song untouched except one sparkle chime on the sticker pop; 0.4 s end fade')
    elif pid == 'kpop_single_take_smear':
        mutes = [o for o in plan.get('overlays') or [] if o.get('type') == 'mute']
        if mutes:
            m = max(mutes, key=lambda o: float(o['t1']))
            ops.append(dict(op='bass_boost', t0=float(m['t1']), db=6.0, rise=0.1))
            notes.append('fSMk: after the 1.5 s silence the chorus returns bass-heavier (+6 dB below 110 Hz, 0.1 s rise)')
    else:
        notes.append(f'{pid}: the song alone (nothing added and nothing edited in its references)')
    return variant, ops, notes


# ------------------------------------------------------------------------------------------------ engine hooks

def _explicit(plan, key):
    v = plan.get(key)
    return [dict(o) for o in v] if isinstance(v, list) else []


def pre(eng, seg, sr, music):
    """Before the engine's fades / mutes: song_cut and version (they replace the song's content)."""
    ops = [o for o in _explicit(eng.plan, 'song_fx') if o.get('op') in PRE_OPS]
    for o in sorted(ops, key=lambda o: float(o.get('at', 0.0))):
        try:
            if o['op'] == 'song_cut':
                op_song_cut(seg, sr, o, music, float(getattr(eng, 'music_start', 0.0)))
            else:
                op_version(seg, sr, o)
        except Exception as e:
            eng.warnings.append(f'song_fx {o.get("op")}: {e}')
    eng._song_fx_pre = ops
    return seg


def post(eng, seg, sr):
    """After the music treatments: schedule (auto) + explicit ops + clip audio -> (song, overlay layer or None)."""
    plan = eng.plan
    mode = design_mode(plan)
    sf = plan.get('song_fx')
    ops, notes, variant = [], [], None
    dry = seg.copy()
    if mode == 'auto' and sf not in ('none', False):
        variant, ops, notes = schedule(eng, dry, sr)
    eng._song_fx_sched = list(ops)
    ops += [o for o in _explicit(plan, 'song_fx') if o.get('op') not in PRE_OPS]
    # clip audio: the preset's rule (auto) + explicit plan["clip_events"]
    clip_ops, clip_rep = [], None
    try:
        import clip_audio as CA
        clip_ops, song_ops, clip_rep = CA.plan_for_engine(eng, dry, sr, auto=(mode == 'auto'))
        ops += song_ops
        # a clip that fills the song's stop replaces the procedural wash
        if any(c.get('role') == 'fill' for c in clip_ops):
            ops = [o for o in ops if not (o.get('op') == 'wash' and o.get('clip_first'))]
    except Exception as e:
        eng.warnings.append(f'clip_audio: {e}')
    layer = np.zeros_like(seg)
    done = []
    _CTX['treated'] = seg            # mutated in place by the song ops, which run before every overlay
    for o in sorted(ops, key=lambda o: (0 if o.get('op') in SONG_OPS else 1)):
        name = o.get('op')
        try:
            if name in SONG_OPS:
                SONG_OPS[name](seg, sr, o)
                done.append(dict(o))
            elif name in OVERLAYS:
                extra = OVERLAYS[name](layer, dry, sr, o) or {}
                done.append(dict(o, **extra))
        except Exception as e:
            eng.warnings.append(f'song_fx {name}: {e}')
    _CTX.pop('treated', None)
    try:
        import clip_audio as CA
        for c in clip_ops:
            CA.mix_event(layer, dry, sr, c)
            done.append(dict({k: v for k, v in c.items() if k not in ('audio',)}, op='clip_' + str(c.get('role'))))
    except Exception as e:
        eng.warnings.append(f'clip_audio mix: {e}')
    cuts = [c['t'] for c in _cuts(eng)]
    hits = cut_hits(dry, sr, cuts) if cuts else []
    got = [h for h in hits if h is not None]
    eng.song_fx_report = dict(
        mode=mode, preset=preset_of(plan, eng.style.get('name')), variant=variant, notes=notes,
        ops=[{k: (round(v, 4) if isinstance(v, float) else v) for k, v in o.items() if k != 'tau_ms'} for o in done],
        pre=getattr(eng, '_song_fx_pre', []), clip_audio=clip_rep,
        cut_to_song_hit_ms=dict(p50=round(float(np.median(got)), 1) if got else None,
                                share_within_150ms=round(len(got) / max(1, len(hits)), 3), n=len(hits)))
    return seg, (layer if np.any(layer) else None)


# ------------------------------------------------------------------------------------------------ version maker

def make_version(src, out, rate=1.0, reverb=False, bass_db=0.0, sr=SR):
    """'slowed (+ reverb) (+ bass boost)' edit audio by resampling (pitch follows, like every reference: ZkWQ 0.909,
    cj12 0.925, _sX2 0.95, upHh 0.891, vnCJ 0.8975, pB7P 1.10). Write BEFORE music.py analyses the song."""
    import subprocess
    import soundfile as sf
    r = subprocess.run(['ffmpeg', '-v', 'error', '-i', src, '-vn', '-ac', '2', '-ar', str(sr), '-f', 'f32le', '-'],
                       capture_output=True, check=True)
    x = np.frombuffer(r.stdout, dtype=np.float32).reshape(-1, 2).T.astype(np.float64)
    if abs(rate - 1.0) > 1e-4:
        from fractions import Fraction
        fr = Fraction(1.0 / rate).limit_denominator(400)
        x = signal.resample_poly(x, fr.numerator, fr.denominator, axis=-1)
    if reverb or bass_db:
        op_version(x, sr, dict(reverb_mix=0.16 if reverb else 0.0, rt60=1.4, bass_db=bass_db))
    x *= 0.89 / max(1e-9, float(np.max(np.abs(x))))
    tmp = out + '.wav'
    sf.write(tmp, x.T.astype(np.float32), sr)
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', tmp, '-c:a', 'aac', '-b:a', '256k', out], check=True)
    os.remove(tmp)
    return out


# ------------------------------------------------------------------------------------------------ selftest / cli

def selftest():
    ok = True
    rng = np.random.default_rng(0)
    n = SR * 4
    t = np.arange(n) / SR
    base = np.stack([0.1 * np.sin(2 * np.pi * 55 * t) + 0.05 * rng.standard_normal(n) for _ in range(2)])
    for k in range(8):                                   # kicks every 0.5 s
        i = int(k * 0.5 * SR)
        base[:, i:i + 2000] += 0.6 * np.sin(2 * np.pi * 60 * np.arange(2000) / SR) * np.exp(-np.arange(2000) / 400)
    x = base.copy()
    stft_region(x, SR, 1.0, 2.0, lambda f, tt: np.ones((len(f), len(tt))))
    good = float(np.max(np.abs(x - base))) < 1e-6
    print('stft identity ', 'ok' if good else 'FAIL', f'{float(np.max(np.abs(x - base))):.2e}')
    ok &= good
    for name, o in [('hold_swell', dict(t0=0, cut=1.0)), ('swell', dict(cut=2.0)), ('duck', dict(t0=1, t1=1.3, mid_db=-6)),
                    ('lowpass', dict(t_close=1.0, t_open=2.0)), ('echo', dict(cut=2.0)), ('dropout', dict(cut=1.5)),
                    ('bass_cut', dict(cut=2.5)), ('fade', dict(t0=3, t1=4, then_out=True)), ('flanger', dict(t0=0, t1=2)),
                    ('air_lift', dict(t0=1.8, t1=2.0)), ('bass_boost', dict(t0=2.0)), ('silence', dict(t0=1.0, t1=1.4)),
                    ('slowdown', dict(t0=3.5, dur=0.18)),
                    ('filter_sweep', dict(kind='lp', pts=[[1.0, 2800.0], [1.9, 2800.0], [1.95, 18000.0]])),
                    ('filter_sweep', dict(kind='hp', pts=[[2.0, 20.0], [2.6, 150.0], [3.2, 150.0]]))]:
        y = base.copy()
        SONG_OPS[name](y, SR, o)
        good = np.isfinite(y).all() and y.shape == base.shape and float(np.max(np.abs(y))) < 5
        print(f'{name:12s}  ', 'ok' if good else 'FAIL')
        ok &= bool(good)
    y = base.copy()
    op_silence(y, SR, dict(t0=1.0, t1=1.4))
    good = float(np.max(np.abs(y[:, int(1.05 * SR):int(1.35 * SR)]))) == 0.0
    ok &= good
    y = base.copy()
    op_song_cut(y, SR, dict(at=2.0, to=0.5), base, 0.0)
    k0, j0, m = int(2.1 * SR), int(0.6 * SR), int(0.1 * SR)
    good = np.allclose(y[:, k0:k0 + m], base[:, j0:j0 + m])
    print('song_cut      ', 'ok' if good else 'FAIL')
    ok &= bool(good)
    for name, o in [('impact', dict(at=1.0)), ('riser', dict(drop=2.0)), ('whoosh5k', dict(cut=1.5)),
                    ('sub_swell_cut', dict(cut=1.5)), ('sub_boom', dict(at=1.6)), ('sub_swell', dict(t0=0.5, drop=2.5)),
                    ('sparkle', dict(at=1.0)), ('glide', dict(t0=2.0, t1=2.4, f0=94)), ('wash', dict(t0=1.0, t1=1.3)),
                    ('noise_crash', dict(at=1.5, rise=0.12, hi_db=0.0)), ('sub_hit', dict(at=2.1, lo_db=9.0))]:
        lay = np.zeros_like(base)
        OVERLAYS[name](lay, base, SR, o)
        good = np.isfinite(lay).all() and float(np.max(np.abs(lay))) > 1e-4
        print(f'{name:12s}  ', 'ok' if good else 'FAIL')
        ok &= bool(good)
    lay = np.zeros_like(base)
    ov_impact(lay, base, SR, dict(at=1.0, t_peak_db=11.0))
    i0, h = int(0.99 * SR), int(0.02 * SR)
    Es = [np.mean(base[:, i0 + k * h:i0 + (k + 1) * h].mean(axis=0) ** 2) for k in range(15)]
    Ee = [np.mean((base + lay)[:, i0 + k * h:i0 + (k + 1) * h].mean(axis=0) ** 2) for k in range(15)]
    got = max(10 * math.log10(b / a) for a, b in zip(Es, Ee))
    good = abs(got - 11.0) < 2.0
    print('impact level  ', 'ok' if good else 'FAIL', f'{got:.1f} dB re music')
    ok &= good
    # transition sounds: the crash's 1.5-16 kHz peak lands at its hi_db re the song's own band level; the rule builder
    lay = np.zeros_like(base)
    ov_noise_crash(lay, base, SR, dict(at=1.5, rise=0.12, hi_db=2.0))
    sos = signal.butter(4, [1500, 16000], 'bandpass', fs=SR, output='sos')
    ps = np.mean(signal.sosfilt(sos, base[:, int(1.2 * SR):int(1.8 * SR)].mean(axis=0)) ** 2)
    yb = signal.sosfilt(sos, lay.mean(axis=0))
    h = int(0.03 * SR)
    pk = max(np.mean(yb[k:k + h] ** 2) for k in range(0, len(yb) - h, h))
    got = 10 * math.log10(pk / ps)
    tpk = int(np.argmax(np.abs(yb))) / SR
    good = abs(got - 2.0) < 1.0 and abs(tpk - 1.5) < 0.05
    print('crash level   ', 'ok' if good else 'FAIL', f'{got:.1f} dB re song highs, peak at {tpk:.3f}')
    ok &= good
    cuts = [dict(t=0.5 * k, tr=('flash_white' if k % 2 else 'blur_bridge')) for k in range(1, 30)]
    o1 = transition_sound_ops('s2rz', cuts, 15.0, drop=2.0, entry=1.5)
    o2 = transition_sound_ops('ZkWQ', cuts, 15.0, drop=2.0)
    kinds = sorted({o['op'] for o in o1})
    good = (not o2) and {'noise_crash', 'sub_hit', 'band'} <= set(kinds) and 'duck' not in kinds and \
        all(0.0 <= float(o.get('at', o.get('t0', 0.0))) < 15.0 for o in o1)
    print('transition ops', 'ok' if good else 'FAIL', len(o1), kinds)
    ok &= good
    on = drum_onsets(base, SR)
    good = len(on) >= 6 and np.min(np.abs(on[:, None] - np.arange(8)[None] * 0.5), axis=0).max() < 0.03
    print('drum_onsets   ', 'ok' if good else 'FAIL', np.round(on, 3)[:8])
    ok &= bool(good)
    print('SELFTEST', 'PASS' if ok else 'FAIL')
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--selftest', action='store_true')
    ap.add_argument('--plan')
    ap.add_argument('--out')
    ap.add_argument('--design')
    ap.add_argument('--make-version')
    ap.add_argument('--rate', type=float, default=1.0)
    ap.add_argument('--reverb', action='store_true')
    ap.add_argument('--bass-db', type=float, default=0.0)
    a = ap.parse_args()
    if a.selftest:
        sys.exit(0 if selftest() else 1)
    if a.make_version:
        print(json.dumps(dict(ok=True, path=make_version(a.make_version, a.out, a.rate, a.reverb, a.bass_db))))
        return
    if a.plan:
        import tempfile
        import engine as E
        plan = E.load_plan(a.plan)
        if a.design:
            plan['audio_design'] = a.design
        eng = E.Engine(plan, tempfile.mkdtemp(prefix='songfx_'), preview=True)
        info = eng.build_audio(a.out or 'song_fx.wav')
        print(json.dumps(dict(ok=True, audio=info, warnings=eng.warnings), indent=1, default=str))
        return
    ap.print_help()


if __name__ == '__main__':
    main()
