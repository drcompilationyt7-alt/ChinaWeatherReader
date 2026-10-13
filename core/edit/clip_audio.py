#!/usr/bin/env python3
"""
clip_audio.py - the anime / K-pop footage's OWN sound, let through only where the references do it, and only when it
complements the song (owner rule 5). Measured rules: core/edit/refs/character-av.md 4, kpop-av.md "Clip-audio rules",
sound-av.md 1 (hits), general-av.md 2.8 / fx-av.md 4 / dance-av.md (none). Per preset: presets.AUDIO_AV[pid]["clip_audio"].

    intro_line  (char_quick_velocity, char_soft_romance; 7 of 8 character references)
                one short anime line (1.3-3 s measured, 0.5-3.2 s accepted, <= 8 words) in the intro, the song held
                down through the WHOLE intro up to the drop (TG -10 dB 0-5 s, mGz9 -5..-8 dB, re-measured with
                refs/audio_av_proof.py; -10 used; a variant that already fades the intro in, YH, keeps its fade), the
                line +4 dB over the ducked music (study +5..+9 on stems; with the same meter as ours TG's line span
                reads +1.3 dB over the ducked intro after it), 300 Hz-3.5 kHz, near mono (side/mid -13..-37 dB), ending
                before the drop.  A dialogue SCENE
                (eEPZ's 12.8 s intro) stays forbidden.
    pop_line    (kpop_postcard_drop; mF 5.98 s)  ONE line <= 1 s from the drop clip starting ON the beat of the grade
                pop, in a vocal-free gap of the song, +4..+8 dB over the quiet music (+6), no duck.
    fill        (char_quick_velocity yUui variant)  a non-speech effect sound from the drop clip filling the song's own
                pre-drop stop up to the drop switch, wide, -6.5 dB re the music median (else song_fx's procedural wash).
    hits        (sound_hit_sync)  the clip's own hit on the song's drum: the engine's per-shot "sfx" path (_mix_hit)
                already does it with sound-av.md's numbers; mix_event(role="hit") is the same recipe for plan
                "clip_events".

Pipeline for every candidate:
  1. shot_audio(): the cut file's own audio, else the library source (edit-cache/src/<id>__all.mkv at the shot's source
     time from the library's shots.json); sources whose audio is a music bed (scene packs, twixtor packs:
     library "music_audio") are refused - their music would fight the song.
  2. separate(): demucs htdemucs -> voice vs everything else (only on the few seconds that are candidates).
  3. find_events(): hits (attack <= 15 ms, -12 dB within 30-300 ms), swings / whooshes (noise-like rise-and-fall
     80-400 ms), cheers (sustained noisy >= 0.8 s), lines (an isolated voiced run 0.5-3.2 s, confirmed by
     faster-whisper, <= 8 words); every passage whose speech share over 5 s is > 0.5 is a dialogue scene: rejected.
  4. score(): on-beat alignment, vocal-gap placement (the song's demucs vocal stem), spectral masking against the song
     at the placement (1/3-octave bands after levelling), key / pitch compatibility (event chroma vs the song's
     chroma; noise-like sounds pass), loudness headroom (gain needed, the event's own SNR).
  5. mix_event(): the reference recipe for the role (EQ, level vs the music, ducking, length, fades).

CLI:  python core/edit/clip_audio.py --selftest
      python core/edit/clip_audio.py --scan CLIP.mp4 [--t0 s --t1 s]      events found in a clip (JSON)
"""
import argparse
import glob
import json
import math
import os
import sys
import tempfile

import numpy as np
from scipy import signal

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

SR = 44100
CACHE = os.environ.get('EDIT_CACHE') or os.path.join(tempfile.gettempdir(), 'edit-cache')


def _db(x):
    return 20.0 * math.log10(max(1e-12, float(x)))


# ------------------------------------------------------------------------------------------------ audio of a shot

_LIB = {}


def library_meta(path):
    """The library's shots.json record of a cut clip (source id, source start, has_speech, music_audio ...)."""
    d = os.path.dirname(os.path.abspath(path))
    if d not in _LIB:
        recs = {}
        for f in (os.path.join(d, 'shots.json'), os.path.join(os.path.dirname(d), 'shots.json')):
            if os.path.exists(f):
                try:
                    j = json.load(open(f, encoding='utf-8'))
                    for s in (j.get('shots') if isinstance(j, dict) else j) or []:
                        recs[os.path.normcase(os.path.abspath(s.get('path', '')))] = s
                except (OSError, ValueError):
                    pass
        _LIB[d] = recs
    return _LIB[d].get(os.path.normcase(os.path.abspath(path)))


def _decode(path, a, b, sr=SR):
    import subprocess
    cmd = ['ffmpeg', '-v', 'error', '-ss', f'{max(0.0, a):.3f}', '-t', f'{max(0.05, b - max(0.0, a)):.3f}', '-i', path,
           '-vn', '-ac', '2', '-ar', str(sr), '-f', 'f32le', '-']
    r = subprocess.run(cmd, capture_output=True, timeout=300)
    if r.returncode != 0:
        return None
    y = np.frombuffer(r.stdout, dtype=np.float32)
    if y.size < 2 * int(0.05 * sr):
        return None
    return y.reshape(-1, 2).T.astype(np.float64)


def source_path(meta):
    sid = (meta or {}).get('source_id')
    if not sid:
        return None
    for pat in (f'src/{sid}__all.*', f'sound_src/{sid}.*', f'src/{sid}.*'):
        for f in glob.glob(os.path.join(CACHE, pat)):
            if not f.endswith('.json'):
                return f
    return None


def shot_audio(path, a, b, sr=SR):
    """Clip seconds [a, b] of a shot's sound -> (audio (2, n) or None, origin / reason)."""
    meta = library_meta(path) or {}
    if meta.get('music_audio'):
        return None, 'the source audio is a music bed (scene / twixtor pack)'
    y = _decode(path, a, b, sr) if os.path.exists(path) else None
    if y is not None and _db(np.sqrt(np.mean(y ** 2))) > -55:
        return y, 'clip'
    src = source_path(meta)
    if src and meta.get('start') is not None:
        y = _decode(src, float(meta['start']) + a, float(meta['start']) + b, sr)
        if y is not None and _db(np.sqrt(np.mean(y ** 2))) > -55:
            return y, 'source'
    return None, 'no usable sound'


# ------------------------------------------------------------------------------------------------ separation

def separate(y, sr=SR):
    """demucs htdemucs on a short (2, n) snippet -> (voice (2, n), rest (2, n)); falls back to (y, y)."""
    try:
        import torch
        import lyrics as LY
        from demucs.apply import apply_model
        from demucs.pretrained import get_model
        if LY._DEMUCS is None:
            LY._DEMUCS = get_model('htdemucs')
            LY._DEMUCS.eval()
        model = LY._DEMUCS
        x = y if sr == 44100 else signal.resample_poly(y, 44100, sr, axis=-1)
        wav = torch.from_numpy(x.astype(np.float32))
        ref = wav.mean(0)
        mu, sd = ref.mean(), ref.std() + 1e-8
        import gpu as _gpu
        dev = _gpu.torch_device()   # 'cuda' when usable, else 'cpu' (as before)
        with torch.no_grad():
            out = apply_model(model, ((wav - mu) / sd)[None], shifts=0, split=True, overlap=0.25, progress=False,
                              device=dev)[0]
        out = (out.cpu() * sd + mu).numpy().astype(np.float64)
        v = out[model.sources.index('vocals')]
        rest = out.sum(axis=0) - v
        if sr != 44100:
            v, rest = (signal.resample_poly(z, sr, 44100, axis=-1) for z in (v, rest))
        return v[:, :y.shape[1]], rest[:, :y.shape[1]]
    except Exception:
        return y, y


# ------------------------------------------------------------------------------------------------ events

HOP = 0.01


def features(y, sr=SR):
    """10 ms frames: rms dB, onset flux, spectral centroid, flatness (2-12 kHz), voicing strength, 300-3.5 kHz share."""
    m = y.mean(axis=0) if y.ndim == 2 else y
    h, w = int(HOP * sr), int(0.04 * sr)
    n = max(1, (len(m) - w) // h)
    raw = np.lib.stride_tricks.sliding_window_view(m, w)[::h][:n]
    fr = raw * np.hanning(w)
    S = np.abs(np.fft.rfft(fr, axis=1)) + 1e-10
    f = np.fft.rfftfreq(w, 1 / sr)
    rms = 20 * np.log10(np.sqrt(np.mean(raw ** 2, axis=1)) + 1e-9)
    cen = (S * f).sum(axis=1) / S.sum(axis=1)
    hb = (f > 2000) & (f < 12000)
    flat = np.exp(np.mean(np.log(S[:, hb]), axis=1)) / np.mean(S[:, hb], axis=1)
    vb = (f > 300) & (f < 3500)
    vshare = S[:, vb].sum(axis=1) / S.sum(axis=1)
    flux = np.maximum(0, np.diff(rms, prepend=rms[0]))
    # voicing: normalised autocorrelation peak over 2.5-12.5 ms lags (80-400 Hz) on a 4 kHz low-passed signal
    sos = signal.butter(4, 1200, 'lowpass', fs=sr, output='sos')
    lp = signal.sosfilt(sos, m)
    fr2 = np.lib.stride_tricks.sliding_window_view(lp, w)[::h][:n]
    fr2 = fr2 - fr2.mean(axis=1, keepdims=True)
    ac = np.fft.irfft(np.abs(np.fft.rfft(fr2, 2 * w, axis=1)) ** 2, axis=1)[:, :w]
    l0, l1 = int(sr / 400), int(sr / 80)
    voic = ac[:, l0:l1].max(axis=1) / (ac[:, 0] + 1e-12)
    return dict(t=(np.arange(n) * h + w / 2) / sr, rms=rms, cen=cen, flat=flat, voice=voic, vshare=vshare, flux=flux)


def _runs(mask, t, min_len=0.0, merge=0.0):
    out, s = [], None
    for i, v in enumerate(mask):
        if v and s is None:
            s = t[i]
        if (not v or i == len(mask) - 1) and s is not None:
            out.append([float(s), float(t[i])])
            s = None
    merged = []
    for r in out:
        if merged and r[0] - merged[-1][1] <= merge:
            merged[-1][1] = r[1]
        else:
            merged.append(r)
    return [r for r in merged if r[1] - r[0] >= min_len]


def speech_mask(F):
    floor = np.percentile(F['rms'], 10)
    loud = F['rms'] > max(floor + 12, np.max(F['rms']) - 40)
    sp = loud & (F['voice'] > 0.45) & (F['vshare'] > 0.35) & (F['cen'] > 250) & (F['cen'] < 3500)
    return signal.medfilt(sp.astype(float), 5) > 0.5


def find_events(y, sr=SR, voice=None, rest=None):
    """Events in a (2, n) clip-audio snippet (times in seconds of the snippet). voice / rest: demucs stems if known."""
    F = features(y, sr)
    t = F['t']
    # speech is segmented on the mix (a demucs voice stem tends to glue breaths onto lines); the stem is what we PLAY
    sp = speech_mask(F)
    speech_runs = _runs(sp, t, 0.12, 0.25)
    dur = len(t) * HOP
    # dialogue scenes: > 50 % speech over any 5 s window (or over the whole snippet when shorter)
    win = int(min(5.0, max(1.0, dur)) / HOP)
    share = np.convolve(sp.astype(float), np.ones(win) / win, mode='same')
    dialogue = _runs(share > 0.5, t, 0.5, 0.5)
    ev = []
    floor = float(np.percentile(F['rms'], 10))
    R = features(rest, sr) if rest is not None else F

    def in_dialogue(a, b):
        return any(a < d1 and b > d0 for d0, d1 in dialogue)

    def speechy(a, b):
        return any(a < s1 and b > s0 for s0, s1 in speech_runs)
    # hits: a fast attack (>= 12 dB over 15 ms), loud, not voiced; -12 dB decay within 30-300 ms
    rr = R['rms']
    for i in range(2, len(rr) - 2):
        rise = rr[i] - rr[max(0, i - 2)]
        if rise >= 12 and rr[i] >= rr[i - 1] and rr[i] >= rr[i + 1] - 1 and rr[i] > floor + 18:
            j = i
            while j < len(rr) - 1 and rr[j] > rr[i] - 12 and (j - i) * HOP < 0.4:
                j += 1
            d12 = (j - i) * HOP
            a = float(t[i] - 0.015)
            if any(e['kind'] == 'hit' and abs(e['onset'] - a) < 0.06 for e in ev):
                continue
            if 0.02 <= d12 <= 0.3 and not speechy(a, a + 0.06) and not in_dialogue(a, a + 0.2):
                ev.append(dict(kind='hit', t0=round(a, 3), t1=round(t[i] + max(0.12, 2 * d12), 3), onset=round(a, 3),
                               peak_db=round(float(rr[i]), 1), decay12_ms=round(1000 * d12), centroid=round(float(R['cen'][i])),
                               flat=round(float(R['flat'][i]), 3), snr_db=round(float(rr[i] - floor), 1)))
    # swings / whooshes: noise-like, rising >= 8 dB over 80-400 ms to a peak, then falling
    sm = np.convolve(rr, np.ones(5) / 5, mode='same')
    pk, _ = signal.find_peaks(sm, prominence=8, distance=15)
    for i in pk:
        k = i
        while k > 0 and sm[k - 1] < sm[k] and (i - k) * HOP < 0.45:
            k -= 1
        rise_s = (i - k) * HOP
        if 0.08 <= rise_s <= 0.4 and sm[i] - sm[k] >= 8 and R['flat'][i] > 0.22 and not speechy(t[k], t[i] + 0.1) \
                and not in_dialogue(t[k], t[i]):
            j = i
            while j < len(sm) - 1 and sm[j] > sm[i] - 10 and (j - i) * HOP < 0.6:
                j += 1
            ev.append(dict(kind='whoosh', t0=round(float(t[k]), 3), t1=round(float(t[j]), 3), onset=round(float(t[i]), 3),
                           peak_db=round(float(sm[i]), 1), centroid=round(float(R['cen'][i])), flat=round(float(R['flat'][i]), 3),
                           snr_db=round(float(sm[i] - floor), 1)))
    # cheers / crowd: sustained noisy >= 0.8 s, busy
    noisy = (R['flat'] > 0.3) & (R['rms'] > floor + 15)
    for a, b in _runs(noisy, t, 0.8, 0.1):
        if not in_dialogue(a, b) and not speechy(a, b):
            seg = (t >= a) & (t <= b)
            ev.append(dict(kind='cheer', t0=round(a, 3), t1=round(b, 3), onset=round(a, 3),
                           peak_db=round(float(R['rms'][seg].max()), 1), centroid=round(float(np.median(R['cen'][seg]))),
                           flat=round(float(np.median(R['flat'][seg])), 3), snr_db=round(float(R['rms'][seg].max() - floor), 1)))
    # lines: an isolated voiced run 0.5-3.2 s (>= 0.25 s of non-speech on both sides), outside dialogue scenes
    for k, (a, b) in enumerate(speech_runs):
        if not 0.5 <= b - a <= 3.2:
            continue
        before = a - (speech_runs[k - 1][1] if k else -9.0)
        after = (speech_runs[k + 1][0] if k + 1 < len(speech_runs) else 99.0) - b
        # a line may come out of a conversation (the references' intro lines do); what is played is this one line
        if before < 0.25 or after < 0.25:
            continue
        seg = (t >= a) & (t <= b)
        ev.append(dict(kind='line', t0=round(a, 3), t1=round(b, 3), onset=round(a, 3), peak_db=round(float(F['rms'][seg].max()), 1),
                       centroid=round(float(np.median(F['cen'][seg]))), flat=round(float(np.median(F['flat'][seg])), 3),
                       snr_db=round(float(F['rms'][seg].max() - floor), 1)))
    return dict(events=sorted(ev, key=lambda e: e['t0']), speech=speech_runs, dialogue=dialogue,
                speech_share=round(float(sp.mean()) if len(sp) else 0.0, 3), dur=round(dur, 3))


_FW = None


def transcribe(y, sr=SR):
    """faster-whisper (base) on a line -> dict(words, text, no_speech, logprob); None when unavailable."""
    global _FW
    try:
        from faster_whisper import WhisperModel
        if _FW is None:
            _FW = WhisperModel(os.environ.get('LYRICS_WHISPER', 'base'), device='cpu', compute_type='int8', cpu_threads=2)
        m = (y.mean(axis=0) if y.ndim == 2 else y).astype(np.float32)
        v16 = signal.resample_poly(m, 16000, sr).astype(np.float32)
        segs, info = _FW.transcribe(v16, word_timestamps=True, vad_filter=False, beam_size=3,
                                    condition_on_previous_text=False)
        segs = list(segs)
        words = [w.word.strip() for s in segs for w in (s.words or [])]
        return dict(words=words, text=' '.join(words)[:120], lang=info.language,
                    lang_p=round(float(getattr(info, 'language_probability', 1.0) or 0.0), 3),
                    no_speech=round(max([s.no_speech_prob for s in segs] or [1.0]), 3),
                    logprob=round(float(np.mean([s.avg_logprob for s in segs])) if segs else -9.0, 3))
    except Exception as e:
        return dict(words=[], text='', error=str(e)[:120], no_speech=1.0, logprob=-9.0)


# ------------------------------------------------------------------------------------------------ scoring

def _third_oct(y, sr, lo=60, hi=16000):
    m = y.mean(axis=0) if y.ndim == 2 else y
    if len(m) < 1024:
        return None, None
    f, P = signal.welch(m, sr, nperseg=2048)
    edges = lo * 2 ** (np.arange(0, math.log2(hi / lo) * 3 + 1) / 3)
    bands = np.array([P[(f >= a) & (f < b)].sum() for a, b in zip(edges[:-1], edges[1:])])
    return np.sqrt(edges[:-1] * edges[1:]), 10 * np.log10(bands + 1e-20)


def chroma(y, sr):
    m = y.mean(axis=0) if y.ndim == 2 else y
    if len(m) < 4096:
        return np.ones(12) / 12
    f, P = signal.welch(m, sr, nperseg=8192)
    ok = (f > 80) & (f < 2000)
    pc = np.round(12 * np.log2(f[ok] / 440.0)).astype(int) % 12
    c = np.bincount(pc, weights=P[ok], minlength=12)
    return c / (c.sum() + 1e-20)


def score(ev_audio, song, sr, at, ctx, role):
    """How well an event fits the song at output time `at` -> dict of components and 'total' (0..1)."""
    dur = ev_audio.shape[1] / sr
    beats = np.asarray(ctx.get('beats') or [], float)
    anchor = at + float(ctx.get('anchor', 0.0))
    d_beat = float(np.min(np.abs(beats - anchor))) if len(beats) else 0.0
    s_beat = math.exp(-(d_beat / (0.03 if role == 'hit' else 0.08)) ** 2)
    # vocal gap: share of the event's span where the song's vocal stem is active
    vo = ctx.get('vocal_env')
    s_gap, v_share = 1.0, None
    if vo is not None:
        vt, ve, thr = vo
        sel = (vt >= at) & (vt <= at + dur)
        if np.any(sel):
            v_share = float(np.mean(ve[sel] > thr))
            s_gap = 1.0 - v_share
    # masking: the event levelled to its target against the song in 1/3-octave bands over the same span
    n = song.shape[1]
    a, b = int(max(0, at) * sr), int(min(n / sr, at + dur) * sr)
    target_gain = float(ctx.get('gain', 1.0))
    fc, ev_b = _third_oct(ev_audio * target_gain, sr)
    _, so_b = _third_oct(song[:, a:b], sr) if b - a > 2048 else (None, None)
    s_mask, audible = 1.0, None
    if ev_b is not None and so_b is not None:
        pe = 10 ** (ev_b / 10)
        audible = float(pe[ev_b >= so_b - 3].sum() / (pe.sum() + 1e-20))
        s_mask = min(1.0, audible / 0.6)
    # key: tonal events must sit in the song's pitch classes (noise-like sounds pass)
    F = features(ev_audio, sr)
    tonal = float(np.median(F['flat'])) < 0.12 and role not in ('line', 'pop_line')
    s_key, kc = 1.0, None
    if tonal and b - a > 4096:
        ce, cs = chroma(ev_audio, sr), chroma(song[:, a:b], sr)
        kc = float((ce * cs).sum() / (np.max(cs) * 1.0 + 1e-12))
        s_key = min(1.0, kc / 0.5)
    # loudness: the gain needed to reach the target and the event's own SNR
    g_db = _db(target_gain)
    snr = float(ctx.get('snr_db', 20.0))
    s_loud = (1.0 if -24 <= g_db <= 18 else 0.4) * min(1.0, max(0.0, (snr - 6) / 12))
    w = {'hit': (0.45, 0.0, 0.3, 0.1, 0.15), 'line': (0.15, 0.25, 0.25, 0.0, 0.35),
         'pop_line': (0.25, 0.35, 0.15, 0.0, 0.25), 'fill': (0.0, 0.2, 0.4, 0.15, 0.25)}.get(role, (0.2, 0.2, 0.2, 0.2, 0.2))
    total = w[0] * s_beat + w[1] * s_gap + w[2] * s_mask + w[3] * s_key + w[4] * s_loud
    return dict(total=round(total, 3), beat_ms=round(1000 * d_beat, 1), vocal_share=None if v_share is None else round(v_share, 3),
                audible_share=None if audible is None else round(audible, 3), key=None if kc is None else round(kc, 3),
                gain_db=round(g_db, 1), snr_db=round(snr, 1))


# ------------------------------------------------------------------------------------------------ mixing (the recipes)

def _bp(y, sr, lo, hi, order=2):
    sos = signal.butter(order, [lo, min(hi, 0.45 * sr)], 'bandpass', fs=sr, output='sos')
    return signal.sosfilt(sos, y, axis=-1)


def _fade(y, sr, a, b):
    y = y.copy()
    ka, kb = min(y.shape[1] // 2, int(a * sr)), min(y.shape[1] // 2, int(b * sr))
    if ka:
        y[:, :ka] *= np.linspace(0, 1, ka)
    if kb:
        y[:, -kb:] *= np.linspace(1, 0, kb)
    return y


def _music_ref(song, sr, t0, t1):
    n = song.shape[1]
    a, b = int(max(0, t0) * sr), int(min(n / sr, t1) * sr)
    if b - a < 256:
        return 1e-4
    return float(np.sqrt(np.mean(song[:, a:b] ** 2)) + 1e-9)


def _reverb_ir(sr, tail, seed=0, lp_hz=6000.0):
    """A short exponential-decay noise IR (-60 dB at `tail` s), decorrelated per channel, high-passed 200 Hz /
    low-passed lp_hz: a room tail for a voice layer (The Beach: 100-200 ms reverb)."""
    rng = np.random.default_rng(int(seed) & 0x7fffffff)
    n = max(64, int(tail * sr))
    t = np.arange(n) / sr
    env = np.exp(-6.9 * t / max(tail, 1e-3))
    ir = rng.standard_normal((2, n)) * env
    sos = signal.butter(2, [200.0, min(lp_hz, 0.45 * sr)], 'bandpass', fs=sr, output='sos')
    ir = signal.sosfilt(sos, ir, axis=-1)
    ir[:, :int(0.004 * sr)] *= np.linspace(0, 1, int(0.004 * sr))        # no click at the start
    return ir / (np.sqrt(np.sum(ir ** 2, axis=1, keepdims=True)) + 1e-12)


def _prepare_voice(ev, y, song, sr):
    """Role 'voice': one footage sound as its OWN layer (owner 2026-09-29: layers like After Effects / Premiere).
    The event keeps its natural length (never time-compressed) and carries its own:
      fade_in / fade_out (s; 0.05 / 0.12 default: a cut is never a hard chop, never mid-word - the planner picks the
        clip_t0 / clip_t1 on word boundaries), hp_hz (150), lp_hz (9000), level_db (speech-active RMS re the song's
        RMS over the line; default -2), reverb: {tail (s), wet_db (dB re the dry voice), seed} - the tail runs past
        clip_t1 (J / L cut: a line may keep ringing over the next shot), echo: {delay (s), fb_db, n} (optional),
        duck_db / duck_pad (the song under the line, applied by plan_for_engine on the song layer, not here).
    ev['layer'] reports the layer's span and its last-20-ms level re its peak (the end-of-layer gate)."""
    y = signal.sosfilt(signal.butter(2, float(ev.get('hp_hz', 150.0)), 'highpass', fs=sr, output='sos'), y, axis=-1)
    lp = float(ev.get('lp_hz', 12000.0) or 0.0)
    if lp:
        y = signal.sosfilt(signal.butter(2, min(lp, 0.45 * sr), 'lowpass', fs=sr, output='sos'), y, axis=-1)
    if abs(float(np.corrcoef(y[0], y[1])[0, 1])) > 0.9:                       # widen a mono source (Haas 9 ms)
        d = int(0.009 * sr)
        y = np.stack([y[0], np.concatenate([np.zeros(d), y[1, :-d]])])
    fi, fo = float(ev.get('fade_in', 0.05)), float(ev.get('fade_out', 0.12))
    y = _fade(y, sr, fi, fo)
    dry = y
    tail_s = 0.0
    rv = ev.get('reverb')
    if rv:
        tail_s = float(rv.get('tail', 0.2))
        ir = _reverb_ir(sr, tail_s, int(rv.get('seed', 0)))
        wet = np.stack([signal.fftconvolve(dry[c], ir[c]) for c in range(2)])
        wet *= 10 ** (float(rv.get('wet_db', -12.0)) / 20) * float(np.sqrt(np.mean(dry ** 2)) / (np.sqrt(np.mean(wet ** 2)) + 1e-12)) * 6.0
        out = np.zeros((2, max(dry.shape[1], wet.shape[1])))
        out[:, :dry.shape[1]] += dry
        out += wet
        y = out
    ec = ev.get('echo')
    if ec:
        dl, fb, nrep = int(float(ec.get('delay', 0.18)) * sr), 10 ** (float(ec.get('fb_db', -9.0)) / 20), int(ec.get('n', 2))
        out = np.zeros((2, y.shape[1] + dl * nrep))
        out[:, :y.shape[1]] += y
        for k in range(1, nrep + 1):
            r = signal.sosfilt(signal.butter(1, 3500.0 * 0.6 ** k, 'lowpass', fs=sr, output='sos'), y, axis=-1)
            out[:, dl * k:dl * k + y.shape[1]] += r * fb ** k
        y = out
    tf = float(ev.get('tail_fade', 0.0))
    if rv or ec:
        y = _fade(y, sr, 0.0, max(0.05, min(0.25, tail_s * 0.4)))
    # level: speech-active RMS of the dry line, against the song's RMS over the line
    mono = _bp(dry.mean(axis=0, keepdims=True), sr, 200, 3500)
    h = int(0.02 * sr)
    k = mono.shape[1] // h
    e = np.sqrt((mono[0, :k * h].reshape(k, h) ** 2).mean(axis=1) + 1e-14) if k > 1 else np.array([float(np.sqrt(np.mean(mono ** 2)) + 1e-9)])
    act = e > e.max() * 10 ** (-20 / 20)
    body = float(np.sqrt(np.mean(e[act] ** 2))) if np.any(act) else float(e.max() + 1e-9)
    at, L = float(ev['at']), dry.shape[1] / sr
    if ev.get('abs_ref') is not None:
        ref = float(ev['abs_ref'])
    else:
        ref = float(ev.get('music_ref') or _music_ref(song, sr, 0.0, song.shape[1] / sr))
    y = y * (ref * 10 ** (float(ev.get('level_db', -2.0)) / 20) / (body + 1e-12))
    n = y.shape[1]
    w20 = int(0.02 * sr)
    pk = float(np.sqrt(np.mean(y[:, :] ** 2, axis=0).max()) + 1e-12)
    end_rms = float(np.sqrt(np.mean(y[:, -w20:] ** 2)) + 1e-12)
    ev['layer'] = dict(t0=round(at, 3), t1=round(at + n / sr, 3), dry_len=round(L, 3), tail=round(tail_s, 3),
                       end_db_re_peak=round(20 * math.log10(end_rms / pk), 1))
    return y


def sync_gate(plan, tol=None):
    """Planner gate: a voice / line event whose source is ON SCREEN must play at the shot's own source offset.
    For every event with a path and every normal-speed shot of the same file overlapping the event's span:
    video offset (shot t0 - src_in) must equal audio offset (at - clip_t0) within one frame.  -> list of violations."""
    fps = float(plan.get('fps') or 30)
    tol = (1.0 / fps) + 1e-3 if tol is None else tol
    bad = []
    for i, e in enumerate(plan.get('clip_events') or []):
        if e.get('role') not in ('voice', 'line', 'fill') or not e.get('path') or e.get('offscreen'):
            continue
        a0 = float(e['at'])
        a1 = a0 + float(e['clip_t1']) - float(e['clip_t0'])
        for j, s in enumerate(plan.get('shots') or []):
            if os.path.normcase(os.path.abspath(s.get('path', ''))) != os.path.normcase(os.path.abspath(e['path'])):
                continue
            if s.get('speed', 'normal') != 'normal' or abs(float(s.get('rate', 1.0) or 1.0) - 1.0) > 1e-6:
                continue
            ov0, ov1 = max(a0, float(s['t0'])), min(a1, float(s['t1']))
            if ov1 - ov0 < 0.15:
                continue
            vid = float(s['t0']) - float(s.get('src_in') or 0.0)
            aud = a0 - float(e['clip_t0'])
            if abs(vid - aud) > tol:
                bad.append(dict(event=i, shot=j, video_offset=round(vid, 3), audio_offset=round(aud, 3), diff_ms=round(1000 * (aud - vid))))
    return bad


def prepare(ev, song, sr):
    """Apply the role's recipe to ev['audio'] (in place): EQ, width, level; returns the level actually set."""
    y = ev['audio']
    role = ev['role']
    at = float(ev['at'])
    dur = y.shape[1] / sr
    if role in ('line', 'pop_line'):
        mono = _bp(y.mean(axis=0, keepdims=True), sr, 300 if role == 'line' else 250, 3500 if role == 'line' else 4000)
        side = _bp((y[0] - y[1])[None] * 0.5, sr, 300, 3500) * 10 ** (-14 / 20)       # side/mid about -14 dB (TG)
        y = np.concatenate([mono + side, mono - side])
        F = features(y, sr)
        act = F['rms'] > np.max(F['rms']) - 20
        body = float(np.sqrt(np.mean(10 ** (F['rms'][act] / 10)))) if np.any(act) else float(np.sqrt(np.mean(y ** 2)))
        if role == 'line':
            duck = 10 ** (float(ev.get('duck_db', LINE_DUCK_DB)) / 20)
            ref = _music_ref(song, sr, at, at + dur) * duck
            over = float(ev.get('level_db', LINE_OVER_DB))
        else:
            ref = _music_ref(song, sr, at - 0.3, at + dur + 0.3)
            over = float(ev.get('level_db', 6.0))
        y = y * (ref * 10 ** (over / 20) / (body + 1e-12))
        y = _fade(y, sr, 0.012, 0.06)
    elif role == 'voice':
        y = _prepare_voice(ev, y, song, sr)
    elif role == 'fill':
        y = signal.sosfilt(signal.butter(2, 150, 'highpass', fs=sr, output='sos'), y, axis=-1)
        if abs(float(np.corrcoef(y[0], y[1])[0, 1])) > 0.9:                   # widen a mono effect (Haas 11 ms)
            d = int(0.011 * sr)
            y = np.stack([y[0], np.concatenate([np.zeros(d), y[1, :-d]])])
        med = float(ev.get('music_median', _music_ref(song, sr, 0, song.shape[1] / sr)))
        y = y * (med * 10 ** (float(ev.get('level_db', -6.5)) / 20) / (float(np.sqrt(np.mean(y ** 2))) + 1e-12))
        y = _fade(y, sr, 0.008, 0.005)
    elif role == 'hit':
        # sound-av.md: gate ~30 ms before the transient (8 ms fade-in), high-pass 60-100 Hz, peak (20 ms RMS)
        # +4.7 dB over the music RMS (+-0.3 s), natural tail, faded 30 ms before the next hit
        y = signal.sosfilt(signal.butter(2, 80, 'highpass', fs=sr, output='sos'), y, axis=-1)
        w20 = int(0.02 * sr)
        pk = float(np.sqrt(np.convolve((y ** 2).mean(axis=0), np.ones(w20) / w20, mode='valid').max()) + 1e-12)
        ref = _music_ref(song, sr, at - 0.3, at + 0.3)
        y = y * (ref * 10 ** (float(ev.get('level_db', 4.7)) / 20) / pk)
        y = _fade(y, sr, 0.008, 0.03)
    ev['audio'] = y
    return y


def mix_event(layer, song, sr, ev):
    """Add a prepared event to the overlay layer at ev['at'] (song_fx.post calls this)."""
    y = ev.get('audio')
    if y is None:
        return
    i0 = int(round(float(ev['at']) * sr))
    if i0 < 0:
        y = y[:, -i0:]
        i0 = 0
    m = min(y.shape[1], layer.shape[1] - i0)
    if m > 0:
        layer[:, i0:i0 + m] += y[:, :m]


# ------------------------------------------------------------------------------------------------ engine planning

def _vocal_env(eng, t0, t1):
    """The song's vocal-stem activity over output [t0, t1] -> (times, env dB, threshold) or None."""
    try:
        import lyrics as LY
        a = float(eng.music_start) + max(0.0, t0)
        b = float(eng.music_start) + t1
        v16 = LY.vocal_stem(eng.music, a, b, threads=2)
        hop = 160
        n = len(v16) // hop
        e = 20 * np.log10(np.sqrt(np.mean(v16[:n * hop].reshape(n, hop) ** 2, axis=1)) + 1e-9)
        tt = max(0.0, t0) + (np.arange(n) + 0.5) * hop / 16000.0
        return tt, e, float(np.max(e) - 25.0)
    except Exception:
        return None


def _candidates(shot, want, sr, span_pad=2.0):
    """Line / effect events from the shot's clip around what is shown (clip seconds)."""
    c0 = float(shot.get('src_in') or 0.0)
    dur_out = float(shot['t1']) - float(shot['t0'])
    a, b = max(0.0, c0 - span_pad), c0 + max(1.0, dur_out * 1.5) + span_pad
    y, origin = shot_audio(shot['path'], a, b, sr)
    if y is None:
        return [], origin
    voice, rest = separate(y, sr)
    found = find_events(y, sr, voice=voice, rest=rest)
    out = []
    for e in found['events']:
        if e['kind'] not in want:
            continue
        src = voice if e['kind'] == 'line' else rest
        s0, s1 = e['t0'] - (0.03 if e['kind'] == 'line' else 0.0), e['t1'] + (0.12 if e['kind'] == 'line' else 0.05)
        i0, i1 = int(max(0, s0) * sr), int(min(y.shape[1] / sr, s1) * sr)
        if i1 - i0 < int(0.05 * sr):
            continue
        out.append(dict(e, clip_t0=round(a + max(0, s0), 3), clip_t1=round(a + s1, 3), audio=src[:, i0:i1].copy(),
                        origin=origin, dialogue=found['dialogue'], speech_share=found['speech_share'],
                        path=shot['path']))
    return out, origin


def plan_for_engine(eng, song, sr, auto=True):
    """-> (clip events ready to mix, song ops (ducks) for song_fx, report)."""
    import song_fx as SF
    plan = eng.plan
    pid = SF.preset_of(plan, eng.style.get('name'))
    rule = (SF.audio_rules(pid) or {}).get('clip_audio', 'none') if auto else 'none'
    events, song_ops, rep = [], [], dict(rule=rule, candidates=0, chosen=[], rejected=[])
    # explicit events: {"role", "path" | "shot", "clip_t0", "clip_t1", "at", "level_db"}
    for e in plan.get('clip_events') or []:
        path = e.get('path') or (eng.shots[int(e['shot'])]['path'] if e.get('shot') is not None else None)
        if not path:
            continue
        y, origin = shot_audio(path, float(e['clip_t0']), float(e['clip_t1']), sr)
        if y is None:
            rep['rejected'].append(dict(path=os.path.basename(path), why=origin))
            continue
        ev = dict(e, audio=y, role=e.get('role', 'hit'), origin=origin)
        prepare(ev, song, sr)
        events.append(ev)
        if ev['role'] == 'line':
            song_ops.append(_line_duck(ev, sr, SF._drop(eng), eng))
        elif ev['role'] == 'voice' and ev.get('duck_db') is not None:
            lay = ev.get('layer') or {}
            pad = float(ev.get('duck_pad', 0.1))
            song_ops.append(dict(op='duck', t0=max(0.0, float(ev['at']) - pad), t1=float(lay.get('t0', ev['at'])) + float(lay.get('dry_len', 0.5)),
                                 depth_db=float(ev['duck_db']), attack=float(ev.get('duck_attack', 0.15)),
                                 release=float(ev.get('duck_release', 0.3)), why='voice layer duck'))
        if ev.get('layer'):
            rep.setdefault('layers', []).append(dict(ev['layer'], path=os.path.basename(path), clip_t0=e['clip_t0'], clip_t1=e['clip_t1']))
    try:
        bad = sync_gate(plan)
        if bad:
            rep['sync_violations'] = bad
            eng.warnings.append(f'clip_audio sync gate: {bad}')
    except Exception as ex:
        eng.warnings.append(f'clip_audio sync gate: {ex}')
    if rule in ('none', 'hits') or not auto:
        return events, song_ops, rep
    drop = SF._drop(eng)
    beats = np.asarray(eng.beats, float)
    if rule == 'intro_line':
        if drop is None or drop < 2.0:
            rep['why'] = 'no intro before the drop'
            return events, song_ops, rep
        shots = [s for s in eng.shots if float(s['t0']) < drop - 1.0][:4]
        cands = []
        for s in shots:
            cs, why = _candidates(s, ('line',), sr)
            if not cs:
                rep['rejected'].append(dict(shot=s.get('i'), why=f'no isolated short line in its {why} audio'
                                            if why in ('clip', 'source') else (why or 'no isolated short line')))
            for c in cs:
                c['shot'] = s
            cands += cs
        rep['candidates'] = len(cands)
        best = None
        venv = _vocal_env(eng, 0.0, drop) if cands else None
        for c in cands:
            L = c['audio'].shape[1] / sr
            if L > drop - 0.35:
                continue
            tr = transcribe(c['audio'], sr)
            nw = len(tr['words'])
            if nw == 0 or nw > 8 or tr['no_speech'] > 0.6 or tr['logprob'] < -1.0 or tr.get('lang_p', 1.0) < 0.5:
                rep['rejected'].append(dict(clip=os.path.basename(c['path']), t=c['t0'], why=f"asr {tr.get('text')!r} "
                                            f"words {nw} no_speech {tr['no_speech']} lp {tr['logprob']}"))
                continue
            s = c['shot']
            # a normal-speed shot plays its own line where it is (scene sync); only a slowed / ramped shot may carry
            # a line from next to what it shows, placed on the first beat
            nat = float(s['t0']) + (c['clip_t0'] - float(s.get('src_in') or 0.0))
            if s.get('speed', 'normal') == 'normal':
                if not (float(s['t0']) <= nat and nat + L <= float(s['t1']) + 0.1 and nat + L < drop - 0.25):
                    rep['rejected'].append(dict(clip=os.path.basename(c['path']), t=c['clip_t0'], why='outside what the shot shows'))
                    continue
                at = nat
            else:
                bb = [b for b in beats if max(0.15, float(s['t0'])) <= b <= drop - 0.25 - L]
                at = float(bb[0]) if bb else max(0.15, drop - 0.3 - L)
            ref = _music_ref(song, sr, at, at + L) * 10 ** (LINE_DUCK_DB / 20)
            body = float(np.sqrt(np.mean(_bp(c['audio'].mean(axis=0, keepdims=True), sr, 300, 3500) ** 2)) + 1e-12)
            sc = score(c['audio'], song * 10 ** (LINE_DUCK_DB / 20), sr, at, dict(beats=list(beats), vocal_env=venv,
                                                                               gain=ref * 10 ** (LINE_OVER_DB / 20) / body,
                                                                               snr_db=c['snr_db']), 'line')
            sc['asr'] = tr.get('text')
            if best is None or sc['total'] > best[0]['total']:
                best = (sc, c, at)
        if best and best[0]['total'] >= 0.45:
            sc, c, at = best
            ev = dict(role='line', at=at, audio=c['audio'], level_db=LINE_OVER_DB, duck_db=LINE_DUCK_DB, path=c['path'],
                      clip_t0=c['clip_t0'], clip_t1=c['clip_t1'], origin=c['origin'], score=sc)
            prepare(ev, song, sr)
            events.append(ev)
            song_ops.append(_line_duck(ev, sr, drop, eng))
            rep['chosen'].append({k: v for k, v in ev.items() if k != 'audio'})
        elif best:
            rep['rejected'].append(dict(why=f"best line scored {best[0]['total']} < 0.45", score=best[0]))
    elif rule == 'pop_line':
        pop = getattr(eng, '_grade_pop', None)
        dshot = next((s for s in eng.shots if drop is not None and abs(float(s['t0']) - drop) < 0.3), None)
        if pop is None or dshot is None:
            rep['why'] = 'no grade pop / drop clip'
            return events, song_ops, rep
        cs, why = _candidates(dshot, ('line',), sr, span_pad=3.0)
        rep['candidates'] = len(cs)
        venv = _vocal_env(eng, pop - 0.2, pop + 1.4) if cs else None
        best = None
        for c in cs:
            L = c['audio'].shape[1] / sr
            if L > 1.1:
                continue
            tr = transcribe(c['audio'], sr)
            if not 1 <= len(tr['words']) <= 4 or tr['no_speech'] > 0.6 or tr.get('lang_p', 1.0) < 0.5:
                rep['rejected'].append(dict(t=c['t0'], why=f"asr {tr.get('text')!r}"))
                continue
            ref = _music_ref(song, sr, pop - 0.3, pop + L + 0.3)
            body = float(np.sqrt(np.mean(c['audio'] ** 2)) + 1e-12)
            sc = score(c['audio'], song, sr, pop, dict(beats=list(beats), vocal_env=venv, gain=ref * 2.0 / body,
                                                       snr_db=c['snr_db']), 'pop_line')
            sc['asr'] = tr.get('text')
            if (sc['vocal_share'] or 0) <= 0.25 and (best is None or sc['total'] > best[0]['total']):
                best = (sc, c)
        if best and best[0]['total'] >= 0.45:
            sc, c = best
            ev = dict(role='pop_line', at=pop, audio=c['audio'], level_db=6.0, path=c['path'], clip_t0=c['clip_t0'],
                      clip_t1=c['clip_t1'], origin=c['origin'], score=sc)
            prepare(ev, song, sr)
            events.append(ev)
            rep['chosen'].append({k: v for k, v in ev.items() if k != 'audio'})
        else:
            rep['rejected'].append(dict(why=why if not cs else 'no line <= 1 s in a vocal-free gap'))
    # the pre-drop fill (song_fx scheduled a wash with clip_first): try a non-speech effect from the drop clip first
    wash = next((o for o in (getattr(eng, '_song_fx_sched', None) or []) if o.get('op') == 'wash'), None)
    if wash is not None:
        dshot = next((s for s in eng.shots if abs(float(s['t0']) - float(wash['t1'])) < 0.3), None)
        if dshot is not None:
            cs, _ = _candidates(dshot, ('whoosh', 'cheer', 'hit'), sr, span_pad=3.0)
            need = float(wash['t1']) - float(wash['t0'])
            cs = [c for c in cs if c['audio'].shape[1] / sr >= need * 0.8]
            if cs:
                c = max(cs, key=lambda c: c['flat'])
                ev = dict(role='fill', at=float(wash['t0']), audio=c['audio'][:, :int(need * sr)], level_db=-6.5,
                          path=c['path'], clip_t0=c['clip_t0'], origin=c['origin'])
                prepare(ev, song, sr)
                events.append(ev)
                rep['chosen'].append({k: v for k, v in ev.items() if k != 'audio'})
    return events, song_ops, rep


LINE_DUCK_DB = -10.0     # the song under the intro (TG -10 dB over 0-5 s, mGz9 -5..-8, study -10..-22)
LINE_OVER_DB = 4.0       # the line over the ducked music (TG: the line span +1.3 dB over the ducked intro after it)


def _line_duck(ev, sr, drop, eng=None):
    """The song under an intro line.  The references hold the song down through the whole intro and let the drop in
    at full level (TG, mGz9); a plan whose song_fx already fades the intro in (YH) only gets the duck under the line.
    Without a drop: the duck covers the line only (in 60 ms before it, out 180 ms after)."""
    at, L = float(ev['at']), ev['audio'].shape[1] / sr
    d = float(ev.get('duck_db', LINE_DUCK_DB))
    faded = any(o.get('op') == 'fade' and float(o.get('t0', 1.0)) < 0.05 and float(o.get('db0', 0.0)) <= -6.0
                for o in (getattr(eng, '_song_fx_sched', None) or []))
    if drop is not None and not faded:
        t1 = max(at + L, drop - 0.3)
        return dict(op='duck', t0=0.0, t1=t1, depth_db=d, attack=0.0, release=max(0.04, drop - 0.02 - t1))
    t1 = at + L + 0.12
    if drop is not None:
        t1 = min(t1, drop - 0.06)
    return dict(op='duck', t0=max(0.0, at - 0.08), t1=t1, depth_db=d, attack=0.06,
                release=0.18 if drop is None else max(0.04, min(0.3, drop - 0.02 - t1)))


# ------------------------------------------------------------------------------------------------ selftest / cli

def selftest():
    ok = True
    rng = np.random.default_rng(1)
    n = SR * 6
    t = np.arange(n) / SR
    y = 0.003 * rng.standard_normal((2, n))
    # a hit at 1.0 s
    i = SR
    y[:, i:i + 4000] += 0.8 * rng.standard_normal(4000) * np.exp(-np.arange(4000) / 500)
    # a whoosh peaking at 2.6 s
    a, b = int(2.3 * SR), int(2.8 * SR)
    env = np.concatenate([np.exp(np.linspace(-4, 0, int(0.3 * SR))), np.exp(np.linspace(0, -5, b - a - int(0.3 * SR)))])
    sos = signal.butter(2, [2000, 9000], 'bandpass', fs=SR, output='sos')
    y[:, a:b] += 0.5 * signal.sosfilt(sos, rng.standard_normal(b - a)) * env
    # a voiced 'line' 4.0-5.2 s (150 Hz harmonics with syllable modulation)
    a, b = int(4.0 * SR), int(5.2 * SR)
    tt = t[a:b]
    f0 = 150 + 20 * np.sin(2 * np.pi * 1.5 * tt)
    ph = 2 * np.pi * np.cumsum(f0) / SR
    v = sum(np.sin(k * ph) / k for k in range(1, 12)) * (0.6 + 0.4 * np.sin(2 * np.pi * 4 * tt) ** 2)
    y[:, a:b] += 0.2 * v
    found = find_events(y)
    kinds = [e['kind'] for e in found['events']]
    good = 'hit' in kinds and 'line' in kinds and 'whoosh' in kinds
    print('find_events   ', 'ok' if good else 'FAIL', [(e['kind'], e['t0']) for e in found['events']])
    ok &= good
    song = 0.1 * rng.standard_normal((2, n))
    ev = dict(role='line', at=1.0, audio=y[:, int(4.0 * SR):int(5.3 * SR)].copy(), level_db=7.0, duck_db=-14.0)
    prepare(ev, song, SR)
    body = _db(np.sqrt(np.mean(ev['audio'][:, :int(1.1 * SR)] ** 2)))
    good = abs(body - (_db(0.1) - 14 + 7)) < 3.0
    print('line level    ', 'ok' if good else 'FAIL', round(body - (_db(0.1) - 14), 1), 'dB over the ducked music')
    ok &= good
    sc = score(ev['audio'], song, SR, 1.0, dict(beats=[1.0, 1.5], gain=1.0, snr_db=30), 'line')
    good = 0 <= sc['total'] <= 1
    print('score         ', 'ok' if good else 'FAIL', sc)
    ok &= good
    print('SELFTEST', 'PASS' if ok else 'FAIL')
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--selftest', action='store_true')
    ap.add_argument('--scan')
    ap.add_argument('--t0', type=float, default=0.0)
    ap.add_argument('--t1', type=float, default=None)
    ap.add_argument('--separate', action='store_true')
    ap.add_argument('--asr', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        sys.exit(0 if selftest() else 1)
    if a.scan:
        t1 = a.t1 if a.t1 is not None else a.t0 + 20.0
        y, origin = shot_audio(a.scan, a.t0, t1)
        if y is None:
            print(json.dumps(dict(ok=False, why=origin)))
            return
        voice, rest = separate(y) if a.separate else (None, None)
        res = find_events(y, voice=voice, rest=rest)
        if a.asr:
            for e in res['events']:
                if e['kind'] == 'line':
                    src = voice if voice is not None else y
                    e['asr'] = transcribe(src[:, int(e['t0'] * SR):int((e['t1'] + 0.1) * SR)])
        print(json.dumps(dict(ok=True, origin=origin, **res), ensure_ascii=False, indent=1))
        return
    ap.print_help()


if __name__ == '__main__':
    main()
