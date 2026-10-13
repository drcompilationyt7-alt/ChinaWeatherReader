#!/usr/bin/env python3
"""
Sound edits ("<song> anime sound edit"): every shot adds ONE audible hit from its own anime audio (a
punch, click, clap, stomp, gunshot, sword clash...) and that hit lands on the song. The reference study
is core/edit/refs/sound.json (six references by JocoBesT, 119 shots and 55 hits measured against the
original songs); this builder follows its numbers and is checked against its quality targets.

What it builds
  - an 8.5-11.5 s loop of the song's most percussive hook, whole bars, chosen by transient clarity,
    bar-to-bar repetition and the most-replayed heatmap; the song plays untouched at speed 1.0
  - song hits = the song's own onsets (1.6-3.2 per s, >= 0.25 s apart) on a 16th pattern that repeats
    bar to bar (the riff), gaps > 0.6 s filled (from the riff's own 16ths while the edit is under the
    references' rate, then only real holes), a few strong off-pattern accents. Every hit sits on an onset
    the study's own detector finds in the rendered window - never on silence - and the hits are snapped to
    that window's onsets at the end, because librosa normalises its onset envelope against the loudest
    frame of whatever it is given, so the whole song and a 10 s hook do not agree (window_onsets).
  - one clip per hit whose own sound transient lands on that onset, and the CUT a pre-roll BEFORE the
    hit (50-290 ms) so the wind-up is seen; the pre-rolls keep their per-class order but are re-centred,
    and then corrected against what the feasible windows actually allowed, on the references' 140 ms
    median; cuts avoid the song's onsets as a tie-break only (refs: 10 %)
  - plain picture: hard cuts (at most one 2-3 frame cross-dissolve), native frames (24 fps drawings in
    a 60 fps file, never interpolated), no camera moves, no grade, no text; a 16:9 master delivered
    rotated 90 deg clockwise (p 0.6) or letterboxed on black (1080x648 at y 636), or kept as 16:9
  - audio: the song at constant level plus each clip's hit, gated 10 ms before it, high-passed at
    100 Hz (except low hits, which get a short bright attack layer instead), +2 dB at 3.5 kHz, its tail
    shaped to the references' envelope (-4 dB at 60 ms, -7 at 120, -10 from 200 ms, and down to -17 dB
    60 ms before the next hit) and levelled to music RMS + 5 dB; master -14 LUFS, true peak < -1 dBTP
    (measured through an AAC round trip). Two closed loops run to convergence on the finished mix: each hit
    is moved so the study's own onset detector finds it exactly on the song's onset, and the hits' onset
    strengths are trimmed (+-5 dB) toward their median so no hit dominates or disappears.

Two modes
  accent  the song untouched, the clip hits accent its onsets (what the references do)
  build   the song's drums are separated out (Demucs) and the clip hits rebuild the beat on the drum
          hits - kick-like clip sounds on the kicks, snare-like on the snares, hat-like on the hats -
          over the drumless bed plus a faint (-12 dB) drum bed; sparse drum parts are topped up from
          the song's other onsets

Clips come from core/edit/library.py --with-audio --need-sfx (shots.json: "path" plus optional
source_id / source_title / content_box / has_text / has_speech / sfx_hits / aesthetic / tags / query).
Per shot this detects sound events with the study's loosened thresholds (flatness 0.12, tonal 0.10,
rise 4.5 dB), measures each one (peak, gating, attack, decay, centroid, low/high share, speech around
it), classifies it (kick / snare / hat / whoosh / voice) and checks it against the picture: the visible
impact within +-2 frames of the sound, room for the pre-roll and the shot inside ONE source scene, no
talking, something actually moving, a bright crisp transient rather than a rumble. Assignment is
Hungarian over event score, timbre (weak in accent mode, strong in build mode), theme and feasibility,
with variety fixes: <= 2 shots per source video (4 for a compilation), one per source scene, never the
same video twice in a row, at most one voice hit. Not enough usable events raises TooFewEvents.

    python core/edit/sound_edit.py plan --music song.m4a --shots shots.json [more.json ...] --out plan.json
        [--start S] [--length 10] [--mode accent|build] [--deliver rot90|letterbox|master|auto]
        [--aspect 9:16|16:9] [--theme clap] [--title "Song"] [--watermark ZeroYenOtaku]
        [--heatmap info.json] [--seed N] [--loop-end 0|1] [--workdir DIR]
    python core/edit/sound_edit.py render --plan plan.json --out out.mp4     (ffmpeg only, ~15 s)
    python core/edit/sound_edit.py build  --music song.m4a (--shots shots.json ... | --library) --out out.mp4
        [plan options] [--renderer own|engine] [--check]
    python core/edit/sound_edit.py check  --video out.mp4 --plan plan.json [--sheet DIR]
    python core/edit/sound_edit.py events --shots shots.json --out events.json [--all]
    python core/edit/sound_edit.py queries --title "HandClap" [--theme clap] [--n 6]
Python API: plan_sound_edit(music, shots, ...) -> plan dict (core/edit/planners.py calls it), render(plan,
out), check(video, plan) -> {"summary": {...targets...}, "shots": [...]}, library_queries(title).
The last stdout line of every command is one JSON object.

plan.json is the engine interface (core/edit/engine.py): aspect, fps, music, music_start, duration,
clip_audio "mix", style "sound_edit", frame ("rot90" | "letterbox_black" | "full"), interp "native",
look "neutral", lead "none", beats, downbeats, energy, shots [{path, t0, t1, src_in, speed "normal",
camera null, transition_in "cut" | "dissolve", layout, sfx [...]}], overlays []. Each sfx entry is what
the engine expects - {"clip_t": the hit in the clip, "at": the output time it lands on, "gain": dB on
top of the engine's own music + 5 dB levelling} - plus our own mix's settings (snippet_clip_t,
snippet_at, dur, fade_in_at, fade_in, fade_out, highpass, presence_db, layers, gain_abs_db, cls).
Each sfx entry's "gain" is computed by simulating the engine's own _mix_hit (engine_snippet), so ITS mix
lands the hit at the same level ours does, and "style_overrides" turns off audio.snap_attacks, since every
clip_t / at here is already the study's onset time and must be taken as given. The engine still clamps its
make-up at +-12 dB: when many hits need more than that, the plan says so ("engine_quiet",
"engine_audio_ok" false) and the soundtrack to use is "audio_mix", which `render` plays.

Sound-edit extras: "audio_mix" (our finished -14 LUFS soundtrack: song + gated hits, onset-aligned and
balanced - use it instead of mixing the sfx entries, which is what `render` does), "audio_bed" (build
mode: the drumless bed the hits sit on, for check()), "deliver" {"master": "16:9", "mode": ...}, per
shot "hold" (seconds to freeze its last frame when its source scene runs out), "scene_t", "src_fps",
"content_box" and "hit" (at, clip_t, cls, song_band, preroll, grid, vis_dt, event_score), and "sound_edit"
(the plan report: bpm, hook, hits, classes, variety, levels, onset errors, seed).

check() reports the study's quality targets AND "ref_bands": this render's p25 / median / p75 for the
pre-roll, the hit-to-onset error, the shot length and the hit level next to the references' own quartiles
(sound.json pooled_measurements), so the loop can see which way a distribution has to move rather than
only whether a median passed. cut_sheets() draws the frame strips to read beside the references' own
(.../edit-refs/sound/analysis/<id>_cuts_*.png): same frames either side at 24 fps, turned upright.
"""
import argparse
import hashlib
import json
import math
import os
import random
import re
import subprocess
import sys
import tempfile
import time

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import gpu as _gpu  # noqa: E402  NVENC when usable, libx264 otherwise (and always on CI)
import numpy as np
from scipy import signal
from scipy.ndimage import uniform_filter1d

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, os.path.join(ROOT, 'core', 'meme'))
import align     # noqa: E402  sfx_frames (per-frame flux / level / flatness)
import acapella  # noqa: E402  lufs, true_peak, master, separate (Demucs)

SPEC_PATH = os.path.join(HERE, 'refs', 'sound.json')
SR = 22050                  # analysis rate
HOP = 128                   # onset hop (5.8 ms), as in the study (sound_timing.py)
MSR = acapella.SR           # mix rate 44.1 kHz
FPS_OUT = 60
MASTER = (1920, 1080)

PREROLL_MIN, PREROLL_DEF, PREROLL_MAX = 0.05, 0.14, 0.29
PREROLL_CLS = {'hat': 0.10, 'snare': 0.14, 'kick': 0.19, 'whoosh': 0.21, 'voice': 0.16}
LEAD_MAX = 0.22             # t=0 is 50-220 ms before the first hit
RATE_MIN, RATE_TARGET, RATE_MAX = 1.6, 2.4, 3.2
MIN_GAP = 0.25
LEN_MIN, LEN_TARGET, LEN_MAX = 8.5, 10.0, 11.5
HIT_OVER_MUSIC_DB = 6.0     # planned hit peak (46 ms RMS) over the music RMS (+-0.3 s); measures ~+5 as in the refs
ENGINE_SFX_DB = 5.0         # core/edit/styles.py sound_edit audio.sfx_over_music_db (the engine's own levelling)
ENGINE_PRE = 0.010          # core/edit/engine.py _mix_hit: its snippet starts this far before its anchor
# An sfx entry's clip_t / at are an anchor PAIR for the engine (it maps clip_t to at), so anchoring both a
# little before the hit would give its 8 ms gate a lead-in on the attack while the hit still landed on the
# same output time. Measured on all five reference songs it cost more than it bought - the engine's 20 ms
# RMS then reads the lead-in too and its +-12 dB make-up clamp leaves the hit quieter - so it is 0 and the
# anchors sit on the hit itself.
ENGINE_LEAD = 0.0
IMPACT_VISIBLE = 0.083      # a shot shows >= 2 frames@24 after its hit
MIN_SHOT = 5 / FPS_OUT
CLASSES = ('kick', 'snare', 'hat', 'whoosh', 'voice')
VOICE_HARM = float(os.environ.get('SOUND_VOICE_HARM', '0.6'))   # harmonic share that marks a voiced hit
EV_VERSION = 10             # bump when the event measures change (event cache key)
COMPILATION_RE = re.compile(r'compilation|sakuga|\bmix\b|moments|scenes|best of|montage|collection', re.I)

# hit themes: library queries per sound type (anime close-ups of the thing making the sound)
THEMES = {
    'clap': ['anime clapping scene', 'anime hand clap close up', 'anime high five scene', 'naruto hand signs scene'],
    'snap': ['anime finger snap scene', 'anime snapping fingers close up'],
    'gun': ['anime gun reload scene', 'anime gun cocking scene', 'anime gunshot scene 4k', 'anime shotgun pump scene',
            'sakuga guns'],
    'sword': ['anime sword clash scene', 'anime katana sheathe scene', 'anime sword draw scene', 'anime katana click'],
    'punch': ['anime punch impact scene', 'anime fist bump scene', 'anime slap scene', 'anime kick impact scene'],
    'stomp': ['anime stomp scene', 'anime table slam scene', 'anime door slam scene', 'anime foot stomp close up'],
    'click': ['anime typing keyboard scene', 'anime chess piece scene', 'anime go stone scene', 'anime pen click scene',
              'anime lighter flick scene', 'anime coin flip scene', 'anime typewriter scene'],
    'knock': ['anime knocking door scene', 'anime gavel scene', 'anime knock on table'],
    'misc': ['anime glass break scene', 'anime match strike scene', 'anime card slap scene', 'anime knuckle crack scene'],
}
# words in a song title / lyric hook that name a sound: >= 60 % of shots act it out
THEME_WORDS = {
    'clap': ['clap', 'handclap', 'hand clap', 'applause'], 'snap': ['snap', 'snapping'],
    'gun': ['gun', 'bang', 'shoot', 'shot', 'bullet', 'trigger', 'pistol', 'revolver'],
    'sword': ['sword', 'blade', 'katana', 'samurai'], 'punch': ['punch', 'fight', 'hit', 'knockout', 'boxing'],
    'stomp': ['stomp', 'stamp', 'wellerman', 'march', 'boots'], 'click': ['click', 'tick', 'clock', 'time', 'typewriter'],
    'knock': ['knock', 'fake', 'guilty', 'judge'],
}


def log(*a):
    print('[sound_edit]', *a, file=sys.stderr, flush=True)


def load_spec():
    try:
        with open(SPEC_PATH, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


# ---------------------------------------------------------------- media io

def probe(path):
    """{'w', 'h', 'fps', 'dur', 'vstart', 'has_audio'} of a video file."""
    r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries',
                        'stream=codec_type,width,height,r_frame_rate,avg_frame_rate,start_time:format=duration',
                        '-of', 'json', path], capture_output=True, text=True)
    out = {'w': 1280, 'h': 720, 'fps': 24.0, 'dur': 0.0, 'vstart': 0.0, 'has_audio': False}
    try:
        d = json.loads(r.stdout or '{}')
    except ValueError:
        return out
    out['dur'] = float((d.get('format') or {}).get('duration') or 0)
    # ffmpeg shifts every file so its earliest stream starts at 0 (-ss, filters and our audio decode
    # all use that timeline): the video's first frame sits at (its start - the earliest start)
    starts = [float(s['start_time']) for s in d.get('streams') or [] if s.get('start_time') not in (None, 'N/A')]
    t_min = min(starts) if starts else 0.0
    for s in d.get('streams') or []:
        if s.get('codec_type') == 'video' and 'vset' not in out:
            out['vset'] = True
            out['w'], out['h'] = int(s.get('width') or 1280), int(s.get('height') or 720)
            for key in ('avg_frame_rate', 'r_frame_rate'):
                n, _, dd = (s.get(key) or '0/1').partition('/')
                try:
                    f = float(n) / float(dd or 1)
                except (ValueError, ZeroDivisionError):
                    f = 0
                if 5 < f < 121:
                    out['fps'] = f
                    break
            vs = s.get('start_time')
            out['vstart'] = max(0.0, (float(vs) if vs not in (None, 'N/A') else t_min) - t_min)
        elif s.get('codec_type') == 'audio':
            out['has_audio'] = True
    out.pop('vset', None)
    return out


def load_audio(path, sr=SR, ch=1, start=0.0, dur=None):
    """float32 audio at sr: mono (n,) or (ch, n); sample 0 = the file's time 0 (or the seek point). None if no audio."""
    cmd = ['ffmpeg', '-v', 'error', '-nostdin']
    if start and start > 0:
        cmd += ['-ss', f'{start:.4f}']
    cmd += ['-i', path]
    if dur:
        cmd += ['-t', f'{dur:.4f}']
    cmd += ['-map', '0:a:0', '-ac', str(ch), '-ar', str(sr), '-af', 'aresample=async=1:first_pts=0', '-f', 'f32le', '-']
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=300)
    except subprocess.TimeoutExpired:
        return None
    a = np.frombuffer(r.stdout, np.float32)
    if a.size < ch * sr * 0.25:
        return None
    a = a[:a.size // ch * ch]
    return a.reshape(-1, ch).T.copy() if ch > 1 else a.copy()


def write_wav(path, x, sr=MSR):
    import wave
    x = np.atleast_2d(x)
    data = np.round(np.clip(x.T, -1, 1) * 32767).astype('<i2')
    with wave.open(path, 'wb') as w:
        w.setnchannels(x.shape[0])
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(data.tobytes())


def db(x):
    return 20 * np.log10(np.maximum(np.asarray(x, float), 1e-7))


def rms_curve(y, sr, win=0.02, hop=0.005):
    """20 ms RMS every 5 ms (dB), and its frame times."""
    w, h = max(1, int(win * sr)), max(1, int(hop * sr))
    if len(y) < w:
        y = np.pad(y, (0, w - len(y)))
    c = np.concatenate([[0.0], np.cumsum(y.astype(np.float64) ** 2)])
    st = np.arange(0, len(y) - w + 1, h)
    r = np.sqrt((c[st + w] - c[st]) / w)
    return db(r), (st + w / 2) / sr


# ---------------------------------------------------------------- onsets

def onsets(y, sr=SR, hop=HOP, delta=0.12, wait_s=0.06):
    """Onset times (s), strength (envelope / its p95) and the envelope: the study's detector
    (librosa onset_detect, hop 128, no backtrack), used for the song AND the clips so both are
    measured the same way."""
    import librosa
    env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
    fr = librosa.onset.onset_detect(onset_envelope=env, sr=sr, hop_length=hop, backtrack=False, delta=delta,
                                    wait=max(1, int(wait_s * sr / hop)))
    return fr * hop / sr, env[fr] / (np.percentile(env, 95) + 1e-9), env


def window_onsets(path, s0, L):
    """The song's onsets as core/edit/refs/sound_timing.py will find them in the delivered edit: the same
    detector, on exactly the window we render. It is not the same as detecting on the whole song, because
    librosa's onset strength normalises its mel spectrogram against the loudest frame of whatever it is
    given (power_to_db ref=np.max, top_db=80), so a 10 s hook and a 4-minute song give different envelopes.
    Returns (times relative to s0, strengths)."""
    y = load_audio(path, SR, 1, start=max(0.0, s0), dur=L)
    if y is None:
        return np.zeros(0), np.zeros(0)
    t, s, _ = onsets(y)
    return t + max(0.0, s0) - s0, s


def band_flux(y, sr=SR, hop=HOP):
    """Positive spectral flux in low (<200 Hz), mid, high (>4.5 kHz) bands: (3, n) and frame times."""
    import librosa
    S = np.abs(librosa.stft(y, n_fft=1024, hop_length=hop))
    f = librosa.fft_frequencies(sr=sr, n_fft=1024)
    L = np.log1p(10 * S)
    fl = np.maximum(0, np.diff(L, axis=1, prepend=L[:, :1]))
    bands = [f < 200, (f >= 200) & (f < 4500), f >= 4500]
    return np.stack([fl[b].sum(0) for b in bands]), np.arange(S.shape[1]) * hop / sr


# ---------------------------------------------------------------- song

class Song:
    """The song: onsets, constant-tempo grid, downbeats, hook window and the song hits."""

    def __init__(self, path, heatmap=None):
        self.path = path
        y = load_audio(path, SR, 1)
        if y is None:
            raise ValueError(f'cannot decode {path}')
        self.y = y
        self.dur = len(y) / SR
        self.on_t, self.on_s, self.env = onsets(y)
        self.flux, self.flux_t = band_flux(y)
        self.bpm0 = self.tempo()
        self.heat = self._heat(heatmap)
        self.P, self.phase = self.fit_grid(0.0, self.dur, self.bpm0, 0.97, 1.03, steps=121)
        self.first_down = self.first_downbeat(0.0, self.dur)

    # -- grid
    def tempo(self):
        """BPM in 70-160: librosa's estimate and its metrical relatives (x1/2, 2, 2/3, 3/2, 3/4, 4/3), scored
        by (mean percussive onset strength on a constant grid) x (tempogram strength). Picks the true
        tempo of all five reference songs (Shape of You 96, not librosa's 129)."""
        import librosa
        oenv = librosa.onset.onset_strength(y=self.y, sr=SR, hop_length=512)
        t1 = float(librosa.feature.tempo(onset_envelope=oenv, sr=SR, hop_length=512)[0]) or 110.0
        tg = librosa.feature.tempogram(onset_envelope=oenv, sr=SR, hop_length=512).mean(axis=1)
        freqs = librosa.tempo_frequencies(len(tg), sr=SR, hop_length=512)
        pe = librosa.onset.onset_strength(y=librosa.effects.percussive(self.y), sr=SR, hop_length=HOP)
        self._perc_env = pe
        ot = np.arange(len(pe)) * HOP / SR
        e = pe / (pe.max() + 1e-9)
        best, best_bpm = -1.0, t1
        for f in (1, 0.5, 2, 2 / 3, 1.5, 0.75, 4 / 3):
            c = t1 * f
            if not 70 <= c <= 160:
                continue
            g = 0.0
            for P in (60 / c) * np.linspace(0.98, 1.02, 21):
                ph = np.arange(0, P, 0.005)
                k = np.arange(max(1, int(ot[-1] / P) - 1))
                g = max(g, float(np.interp(ph[:, None] + k[None] * P, ot, e).mean(axis=1).max()))
            i = int(np.argmin(np.abs(freqs[1:] - c))) + 1
            sc = g * float(tg[i])
            if sc > best:
                best, best_bpm = sc, c
        return best_bpm

    def fit_grid(self, t0, t1, bpm, lo=0.985, hi=1.015, steps=61):
        """Constant beat grid (period, a beat time) on the percussive onset envelope of [t0, t1]."""
        a, b = int(max(0, t0) * SR), int(min(self.dur, t1) * SR)
        ia, ib = a // HOP, max(a // HOP + 2, b // HOP)
        oe = self._perc_env[ia:ib]
        ot = ia * HOP / SR + np.arange(len(oe)) * HOP / SR
        e = oe / (oe.max() + 1e-9)
        best = (-1.0, 60 / bpm, 0.0)
        span = (b - a) / SR
        for P in (60 / bpm) * np.linspace(lo, hi, steps):
            ph = np.arange(0, P, 0.003)
            k = np.arange(max(1, int(span / P) - 1))
            sc = np.interp(a / SR + ph[:, None] + k[None] * P, ot, e).mean(axis=1)
            i = int(np.argmax(sc))
            if sc[i] > best[0]:
                best = (float(sc[i]), float(P), float(ph[i]))
        _, P, ph = best
        fine = ph + np.arange(-0.003, 0.00301, 0.0005)
        k = np.arange(max(1, int(span / P) - 1))
        sc = np.interp(a / SR + fine[:, None] + k[None] * P, ot, e).mean(axis=1)
        return P, a / SR + float(fine[int(np.argmax(sc))])

    def beats(self, t0, t1, P=None, phase=None):
        P, ph = P or self.P, self.phase if phase is None else phase
        k0, k1 = int(math.floor((t0 - ph) / P)), int(math.ceil((t1 - ph) / P))
        return ph + np.arange(k0, k1 + 1) * P

    def first_downbeat(self, t0, t1, P=None, phase=None):
        """A downbeat time of the grid in [t0, t1]: which beat of 4 carries the low-end attack and the
        chord change (the study's downbeat_phase)."""
        import librosa
        P, ph = P or self.P, self.phase if phase is None else phase
        a, b = int(max(0, t0) * SR), int(min(self.dur, t1) * SR)
        seg = self.y[a:b]
        S = np.abs(librosa.stft(seg, n_fft=2048, hop_length=512))
        fr = librosa.fft_frequencies(sr=SR, n_fft=2048)
        st = a / SR + np.arange(S.shape[1]) * 512 / SR
        low = np.concatenate([[0], np.maximum(0, np.diff(np.log1p(S[fr < 120]), axis=1)).sum(axis=0)])
        chroma = librosa.feature.chroma_stft(S=S ** 2, sr=SR)
        grid = self.beats(t0, t1, P, ph)
        idx = np.searchsorted(st, grid)
        ok = (idx > 0) & (idx < S.shape[1] - 4)
        idx, grid = idx[ok], grid[ok]
        if len(idx) < 12:
            return float(grid[0]) if len(grid) else float(t0)
        hc = np.zeros(len(idx))
        for i in range(2, len(idx) - 2):
            x = chroma[:, idx[i - 2]:idx[i]].mean(axis=1)
            y = chroma[:, idx[i]:idx[i + 2]].mean(axis=1)
            hc[i] = 1 - x @ y / (np.linalg.norm(x) * np.linalg.norm(y) + 1e-9)
        lowb = np.array([low[j:j + 3].max() for j in idx])

        def z(v):
            return (v - v.mean()) / (v.std() + 1e-9)
        feat = z(hc) + 0.7 * z(lowb)
        p = int(np.argmax([feat[q::4].mean() for q in range(4)]))
        return float(grid[p])

    def downbeats(self, t0, t1, P=None, phase=None):
        """Downbeat times in [t0, t1] on the grid (P, phase): the song's downbeat nearest to the
        region, snapped to this grid, then every 4 beats."""
        P, ph = P or self.P, self.phase if phase is None else phase
        mid = 0.5 * (t0 + t1)
        g = self.first_down + round((mid - self.first_down) / (4 * self.P)) * 4 * self.P
        d0 = ph + round((g - ph) / P) * P
        n0, n1 = math.floor((t0 - d0) / (4 * P)), math.ceil((t1 - d0) / (4 * P))
        return d0 + np.arange(n0, n1 + 1) * 4 * P

    # -- hook
    def _heat(self, heatmap):
        if not heatmap:
            return None
        try:
            if isinstance(heatmap, str):
                heat = acapella.load_heatmap(heatmap)
            else:
                heat = [(float(m['start_time']), float(m['end_time']), float(m['value'])) for m in heatmap]
        except Exception:
            return None
        if not heat:
            return None
        t = np.arange(0, self.dur, 0.1)
        h = np.zeros(len(t))
        for a, b, v in heat:
            h[(t >= a) & (t < b)] = v
        h = uniform_filter1d(h, 30)
        return (t, h / (h.max() + 1e-9))

    def n_bars_for(self, length=LEN_TARGET):
        bar = 4 * self.P
        best, cost = 4, 1e9
        for n in (3, 4, 5, 6, 8):
            L = n * bar
            c = abs(L - length) + (0 if LEN_MIN <= L <= LEN_MAX else 50) + (0.4 if n % 2 else 0) + (0 if n == 4 else 0.3)
            if c < cost:
                best, cost = n, c
        return best

    def hook_candidates(self, n_bars, boundary=False):
        """(score, downbeat) of every n-bar window: percussive clarity, onset rate near 2.4/s,
        bar-to-bar repetition, loudness and the most-replayed heatmap."""
        L = n_bars * 4 * self.P
        downs = self.downbeats(0.0, self.dur)
        downs = downs[(downs >= 0) & (downs + L <= self.dur - 0.3)]
        if not len(downs):
            return []
        med_s = float(np.median(self.on_s)) if len(self.on_s) else 0.0
        env_t = np.arange(len(self.env)) * HOP / SR
        e95 = float(np.percentile(self.env, 95)) + 1e-9
        rms = librosa_rms(self.y)
        top = np.percentile(rms[1], 95)
        rows = []
        for d in downs:
            m = (self.on_t >= d) & (self.on_t < d + L)
            strong = self.on_s[m] >= med_s
            rate = strong.sum() / L
            f_rate = max(0.0, 1 - abs(rate - 2.6) / 1.8)
            e = self.env[(env_t >= d) & (env_t < d + L)]
            if len(e) < 10:
                continue
            # transient contrast: the strongest onsets over the envelope's floor
            clar = float(np.mean(np.sort(self.on_s[m])[-8:])) / (float(np.median(e)) / e95 + 0.05) if m.any() else 0.0
            # bar repetition of the onset envelope
            per = int(round(4 * self.P * SR / HOP))
            bars = [e[k * per:(k + 1) * per] for k in range(n_bars) if len(e[k * per:(k + 1) * per]) == per]
            rep = float(np.mean([np.corrcoef(bars[k], bars[k + 1])[0, 1] for k in range(len(bars) - 1)])) if len(bars) > 1 else 0.0
            sel = (rms[0] >= d) & (rms[0] < d + L)
            loud = float(np.clip((np.mean(rms[1][sel]) - top + 12) / 12, 0, 1)) if sel.any() else 0.0
            heat = 0.0
            if self.heat is not None:
                ht, hv = self.heat
                hs = (ht >= d) & (ht < d + L)
                heat = float(hv[hs].mean()) if hs.any() else 0.0
            bnd = 0.0
            if boundary:
                # a long section should start where a section starts (the hook / chorus entry): the loudness
                # rise from the 2 bars before d to the 2 bars after it, in dB
                pre = (rms[0] >= d - 2 * 4 * self.P) & (rms[0] < d)
                post = (rms[0] >= d) & (rms[0] < d + 2 * 4 * self.P)
                if pre.any() and post.any():
                    bnd = float(np.clip((np.mean(rms[1][post]) - np.mean(rms[1][pre])) / 6.0, 0, 1))
                elif post.any():
                    bnd = 0.3
            rows.append({'d': float(d), 'rate': rate, 'f_rate': f_rate, 'clar': clar, 'rep': max(0.0, rep), 'loud': loud,
                         'heat': heat, 'bnd': bnd})
        if not rows:
            return []
        c = np.array([r['clar'] for r in rows])
        cz = (c - c.mean()) / (c.std() + 1e-9)
        out = []
        for r, z in zip(rows, cz):
            sc = 0.3 * r['f_rate'] + 0.2 * float(np.tanh(z)) + 0.25 * r['rep'] + 0.15 * r['loud']
            sc += (0.45 * r['heat']) if self.heat is not None else 0.0
            sc += 0.25 * r['bnd']
            if r['loud'] < 0.25:
                sc -= 0.3                        # a quiet intro / breakdown
            out.append((sc, r['d'], r))
        out.sort(key=lambda x: -x[0])
        return out

    def hook(self, start=None, length=None, n=None):
        """(downbeat, n_bars): the edit covers [downbeat - lead, + n_bars bars]. n: a fixed bar count (the
        long sections of render_vibe); the window then also prefers a section start."""
        boundary = n is not None
        n = n or self.n_bars_for(length or LEN_TARGET)
        if start is not None:
            downs = self.downbeats(max(0, start - 8), start + 8)
            d = float(downs[np.argmin(np.abs(downs - start))]) if len(downs) else float(start)
            return d, n
        cand = self.hook_candidates(n, boundary=boundary)
        if not cand:
            return max(0.0, (self.dur - n * 4 * self.P) / 2), n
        return cand[0][1], n


def librosa_rms(y, hop=512):
    import librosa
    r = librosa.feature.rms(y=y, frame_length=2048, hop_length=hop)[0]
    return np.arange(len(r)) * hop / SR, db(r)


def select_hits(song, d, n_bars, P, rate=RATE_TARGET, onset_t=None, onset_s=None, end_pad=0.2):
    """Song hits in [d - 0.05, d + n_bars bars) (d = a downbeat, P = the local beat period): the song's
    own onsets on a repeating 16th pattern (the riff), gaps > 0.6 s filled, a few strong off-pattern
    accents. Returns [{'t' (song s), 'strength', 'pos16', 'bar', 'pattern', 'dev_ms'}] in time order."""
    q = P / 4
    bar = 4 * P
    L = n_bars * bar
    ot = song.on_t if onset_t is None else np.asarray(onset_t)
    os_ = song.on_s if onset_s is None else np.asarray(onset_s)
    m = (ot >= d - 0.06) & (ot < d + L - end_pad)
    ot, os_ = ot[m], os_[m]
    if not len(ot):
        return []
    floor = max(0.25 * float(np.max(os_)), float(np.median(os_)) * 0.8)
    k = np.round((ot - d) / q).astype(int)
    dev = ot - (d + k * q)
    on_grid = np.abs(dev) <= 0.045
    pos = k % 16
    bars = k // 16
    per_bar = int(np.clip(round(rate * bar), 4, 8))
    gap16 = max(1, int(math.ceil(MIN_GAP / q - 0.05)))
    # pattern: 16th positions carrying strong onsets in most bars
    score = np.zeros(16)
    for p_, s_, g_ in zip(pos, os_, on_grid):
        if g_ and s_ >= floor:
            score[p_] += s_
    # the best set of <= per_bar positions, all >= gap16 apart around the bar (exhaustive over the top 12)
    cand = [int(p_) for p_ in np.argsort(-score)[:12] if score[p_] > 0]
    pattern, best = [], 0.0
    for mask in range(1, 1 << len(cand)):
        sel = [cand[b] for b in range(len(cand)) if mask >> b & 1]
        if len(sel) > per_bar:
            continue
        ss = sorted(sel)
        if len(ss) > 1 and min([(ss[k + 1] - ss[k]) for k in range(len(ss) - 1)] + [16 - ss[-1] + ss[0]]) < gap16:
            continue
        tot = float(sum(score[x] for x in sel))
        if tot > best:
            pattern, best = ss, tot
    hits = []
    taken = set()
    for b in range(n_bars + 1):
        for p_ in pattern:
            sel = np.where((bars == b) & (pos == p_) & on_grid)[0]
            if not len(sel):
                continue
            j = sel[int(np.argmax(os_[sel]))]
            # the riff repeats through the weak bars too: the references cut 6-8 times a bar on the same
            # 16th positions (75-100 % repeat) even where the song's onset there is soft
            if os_[j] < 0.35 * floor:
                continue
            hits.append(j)
            taken.add(j)
    hits = sorted(set(hits), key=lambda j: ot[j])
    # min gap (keep the stronger of two close hits)
    kept = []
    for j in hits:
        if kept and ot[j] - ot[kept[-1]] < MIN_GAP:
            if os_[j] > os_[kept[-1]]:
                kept[-1] = j
            continue
        kept.append(j)

    def row(j):
        return {'t': float(ot[j]), 'strength': float(os_[j]), 'pos16': int(pos[j]), 'bar': int(bars[j]),
                'pattern': bool(pos[j] in pattern and on_grid[j]), 'dev_ms': round(float(dev[j]) * 1000, 1),
                'grid': False}
    rows = [row(j) for j in kept]
    used = set(kept)

    def free_t(t):
        return all(abs(t - r['t']) >= MIN_GAP for r in rows)

    def snap(t, win=0.045):
        """A grid time -> the song's own onset within `win` if there is one (even a weak one the pattern
        step skipped): a hit on a real onset still measures 0 ms against the song."""
        if not len(ot):
            return t, None
        j = int(np.argmin(np.abs(ot - t)))
        return (float(ot[j]), j) if abs(ot[j] - t) <= win else (t, None)
    e95 = float(np.percentile(song.env, 95)) + 1e-9

    def grid_row(tg):
        """A hit on the riff's own 16th grid where the pattern step could not use the song's onset there
        (too weak, or off the grid). The references repeat 75-100 % of a bar's 16th positions in the next bar
        (sound.md 1, "the cut rhythm follows the riff"), so a sparse bar keeps its beat. The song must still
        have an onset within half a sixteenth: a hit laid on silence is not this style, and measures 200 ms
        from the nearest onset instead of the references' 9."""
        th, j = snap(tg, win=min(0.06, 0.6 * q))
        if j is None or th in [r['t'] for r in rows] or not free_t(th) or not (d - 0.05 <= th < d + L - end_pad) or j in used:
            return None
        k_ = int(np.clip(round(th * SR / HOP), 0, len(song.env) - 1))
        st_g = float(song.env[max(0, k_ - 1):k_ + 2].max()) / e95
        used.add(j)
        return {'t': float(th), 'strength': st_g, 'pos16': int(round((th - d) / q)) % 16,
                'bar': int(max(0, (th - d)) // (16 * q)), 'pattern': bool(int(round((tg - d) / q)) % 16 in pattern),
                'dev_ms': round((th - tg) * 1000, 1), 'grid': True}
    # fill gaps > 0.6 s: the strongest unused onset there, else the riff's own 16th positions inside the
    # hole, else the 16th nearest its middle. Then a few strong off-pattern accents.
    for _ in range(64):
        tt = [d - 0.05] + [r['t'] for r in rows] + [d + L - end_pad]
        gaps = [(tt[i + 1] - tt[i], i) for i in range(len(tt) - 1)]
        g, i = max(gaps)
        # below the references' typical rate any gap over 0.6 s is filled; once the edit is at ~2.4 cuts/s
        # only a real hole is (their longest shots are ~0.7 s, shot_frames_at_24fps p90 13 / max >= 17)
        if g <= (0.6 if len(rows) < rate * L else 0.78):
            break
        cand = [j for j in range(len(ot)) if tt[i] < ot[j] < tt[i + 1] and j not in used and free_t(ot[j])
                and os_[j] >= 0.5 * floor]
        if not cand:
            # nothing strong in the hole: a weak onset is still a real one, and the clip's own hit is what
            # the viewer hears there anyway - better than leaving a shot twice the references' longest
            cand = [j for j in range(len(ot)) if tt[i] < ot[j] < tt[i + 1] and j not in used and free_t(ot[j])]
        if not cand:
            mid = 0.5 * (tt[i] + tt[i + 1])
            pat_t = sorted([d + k_ * q for k_ in range(int(math.floor((tt[i] - d) / q)), int(math.ceil((tt[i + 1] - d) / q)) + 1)
                            if k_ % 16 in pattern and tt[i] < d + k_ * q < tt[i + 1]], key=lambda x: abs(x - mid))
            new = next((r for r in (grid_row(x) for x in pat_t + [d + round((mid - d) / q) * q]) if r), None)
            if new is None:
                break                        # nothing can go in this hole
            rows.append(new)
            rows.sort(key=lambda r: r['t'])
            continue
        j = max(cand, key=lambda j: os_[j])
        used.add(j)
        rows.append(row(j))
        rows.sort(key=lambda r: r['t'])
    extra_budget = int(0.15 * len(rows))
    for j in sorted(range(len(ot)), key=lambda j: -os_[j]):
        if extra_budget <= 0 or len(rows) >= min(RATE_MAX, rate * 1.15) * L:
            break
        if j in used or os_[j] < np.percentile(os_, 80) or not free_t(ot[j]):
            continue
        used.add(j)
        rows.append(row(j))
        rows.sort(key=lambda r: r['t'])
        extra_budget -= 1
    # too many for the rate cap: drop the weakest off-pattern ones
    while len(rows) > RATE_MAX * L:
        off = [r for r in rows if not r['pattern']] or rows
        rows.remove(min(off, key=lambda r: r['strength']))
    return rows



def select_hits_span(song, d, n_bars, P, rate=RATE_TARGET, chunk=4):
    """select_hits over a long continuous section (a 30-50 s Short, a 1.5-3 min long): the riff pattern is
    found per `chunk` bars (verse, pre-chorus and chorus have different riffs; one pattern for the whole
    section would leave whole phrases off the beat), the chunks joined with the same >= MIN_GAP rule."""
    bar = 4 * P
    rows = []
    for k in range(0, n_bars, chunk):
        nb = min(chunk, n_bars - k)
        last = k + nb >= n_bars
        r = select_hits(song, d + k * bar, nb, P, rate=rate, end_pad=0.2 if last else 0.0)
        lo_t = d + k * bar - (0.06 if k == 0 else 0.0)
        hi_t = d + (k + nb) * bar
        for x in r:
            if lo_t <= x['t'] < hi_t:
                x = dict(x)
                x['bar'] = int(x['bar']) + k
                rows.append(x)
    rows.sort(key=lambda r: r['t'])
    kept = []
    for r in rows:
        if kept and r['t'] - kept[-1]['t'] < MIN_GAP:
            if r['strength'] > kept[-1]['strength']:
                kept[-1] = r
            continue
        kept.append(r)
    return fill_silent(kept, d, n_bars * bar, P)


SILENT_GAP = 0.75           # a gap longer than this (s) gets 'song only' shots on the beat grid


def fill_silent(rows, d, L, P, gap_max=SILENT_GAP):
    """Quiet stretches of a long section (a breakdown, a held vocal) have no onset to hit. The references
    cut there anyway - 46 % of their cuts carry no clip sound, the shot is 'song only' - and their longest
    shot is ~0.7 s. Fill every gap over `gap_max` with evenly spaced cuts snapped to the 8th-note grid,
    marked 'silent' (the shot's action lands on the beat, its own audio is not mixed in)."""
    e8 = P / 2
    out = list(rows)
    tt = [d - 0.05] + [r['t'] for r in rows] + [d + L - 0.2]
    for a, b in zip(tt[:-1], tt[1:]):
        g = b - a
        if g <= gap_max:
            continue
        k = int(math.ceil(g / 0.5)) - 1
        prev = a
        for j in range(1, k + 1):
            x = a + g * j / (k + 1)
            x = d + round((x - d) / e8) * e8
            if x - prev < MIN_GAP or b - x < MIN_GAP:
                continue
            out.append({'t': float(x), 'strength': 0.0, 'pos16': int(round((x - d) / (P / 4))) % 16,
                        'bar': int(max(0.0, x - d) // (4 * P)), 'pattern': False, 'dev_ms': 0.0, 'grid': True,
                        'silent': True})
            prev = x
    out.sort(key=lambda r: r['t'])
    return out

def hit_bands(fl, ft, times):
    """'low' / 'mid' / 'high': which band carries each onset (flux over the band's median);
    fl, ft = band_flux(song)."""
    ref = np.percentile(fl, 97, axis=1) + 1e-6     # a typical strong onset of each band = 1
    out = []
    for t in times:
        j = int(np.clip(np.searchsorted(ft, t), 0, fl.shape[1] - 1))
        v = fl[:, max(0, j - 2):j + 3].max(axis=1) / ref
        out.append(('low', 'mid', 'high')[int(np.argmax(v))])
    return out


def drum_hits(drums, sr=SR):
    """Onsets of a drum stem, classified kick / snare / hat by band energy: [(t, strength, cls)]."""
    t, s, _ = onsets(drums, sr, delta=0.08, wait_s=0.07)
    import librosa
    S = np.abs(librosa.stft(drums, n_fft=1024, hop_length=HOP)) ** 2
    f = librosa.fft_frequencies(sr=sr, n_fft=1024)
    out = []
    for ti, si in zip(t, s):
        j = int(round(ti * sr / HOP))
        e = S[:, j:j + 8].sum(axis=1)
        tot = e.sum() + 1e-12
        low, high = e[f < 150].sum() / tot, e[f > 5000].sum() / tot
        cls = 'kick' if low > 0.45 else ('hat' if high > 0.35 and low < 0.1 else 'snare')
        out.append((float(ti), float(si), cls))
    return out


# ---------------------------------------------------------------- clip events

def _visual(path, pr, w=96):
    """Low-res grey frames of the whole clip -> (frame times, diff per frame, luma per frame, scene cut
    frame indices, mean colour per frame)."""
    h = max(2, int(round(w * pr['h'] / max(1, pr['w']) / 2) * 2))
    r = subprocess.run(['ffmpeg', '-v', 'error', '-i', path, '-an', '-vf', f'scale={w}:{h}', '-pix_fmt', 'rgb24',
                        '-f', 'rawvideo', '-'], capture_output=True, timeout=300)
    a = np.frombuffer(r.stdout, np.uint8)
    n = a.size // (w * h * 3)
    if n < 3:
        return None
    rgb = a[:n * w * h * 3].reshape(n, h, w, 3).astype(np.float32)
    g = rgb.mean(axis=3)
    diff = np.zeros(n)
    diff[1:] = np.abs(np.diff(g, axis=0)).mean(axis=(1, 2))
    hist = np.stack([np.histogram(x, bins=16, range=(0, 256))[0] for x in g]).astype(np.float32)
    hist /= hist.sum(axis=1, keepdims=True) + 1e-9
    hd = np.zeros(n)
    hd[1:] = np.abs(np.diff(hist, axis=0)).sum(axis=1)
    med = float(np.median(diff[1:])) + 1e-3
    cuts = [k for k in range(1, n) if diff[k] > max(26.0, 5 * med) and hd[k] > 0.3]
    times = pr['vstart'] + np.arange(n) / pr['fps']
    col = rgb.mean(axis=(1, 2))
    # sharpness proxy: mean |laplacian| of the small grey frame (relative use only)
    lap = np.abs(g[:, 1:-1, 1:-1] * 4 - g[:, :-2, 1:-1] - g[:, 2:, 1:-1] - g[:, 1:-1, :-2] - g[:, 1:-1, 2:]).mean(axis=(1, 2))
    return {'t': times, 'diff': diff, 'luma': g.mean(axis=(1, 2)) / 255, 'cuts': cuts, 'col': col, 'lap': lap,
            'fps': pr['fps'], 'n': n}


def _sfx_candidates(fr, t0=0.0, t1=None, max_hits=8, gap=0.2, flat_min=0.12, tonal_min=0.10, rise_min=4.5,
                    over_min=3.0, pct=93):
    """align.sfx_hits with the study's loosened thresholds (flatness 0.12, tonal 0.10, rise 4.5 dB):
    [(t, strength)] of short broadband transients, strongest first."""
    flux, dbv, flat, tonal = fr['flux'], fr['db'], fr['flat'], fr['tonal']
    n = len(flux)
    fps = align.FPS_FX
    i0, i1 = max(0, int(t0 * fps)), min(n, int((t1 if t1 is not None else n / fps) * fps))
    if i1 - i0 < fps * 0.5:
        return []
    seg = flux[i0:i1]
    med = float(np.median(seg))
    thr = max(float(np.percentile(seg, pct)), med + 5 * float(np.median(np.abs(seg - med))), 2.5)
    med_db = float(np.median(dbv[i0:i1]))
    out = []
    for k in range(max(i0, 1), min(i1, n - 1)):
        if flux[k] < thr or flux[k] < flux[k - 1] or flux[k] < flux[k + 1]:
            continue
        pk = float(dbv[k:k + 6].max())
        over, rise = pk - med_db, pk - float(np.median(dbv[max(0, k - 30):max(1, k - 2)]))
        fl, ton = float(flat[k:k + 6].max()), float(np.median(tonal[k:k + 15]))
        if over < over_min or rise < rise_min or fl < flat_min or ton < tonal_min:
            continue
        st = (0.35 * min(1, flux[k] / (2 * thr)) + 0.25 * min(1, over / 15) + 0.2 * min(1, rise / 20)
              + 0.2 * min(1, fl / 0.4))
        out.append((k / fps, round(st, 3)))
    picked = []
    for t, s in sorted(out, key=lambda x: -x[1]):
        if all(abs(t - u) >= gap for u, _ in picked):
            picked.append((round(t, 4), s))
        if len(picked) >= max_hits:
            break
    return picked


def _classify(f):
    """kick / snare / hat / whoosh / voice from the hit's measures."""
    if f['tonal'] < 0.07 and f['decay_ms'] > 180 and f['centroid'] < 3200 and f['flat'] < 0.3:
        return 'voice'
    if f['attack_ms'] > 45 and f['decay_ms'] > 220 and f['flat'] > 0.2 and f['low'] < 0.3:
        return 'whoosh'
    if f['low'] > 0.35 or (f['centroid'] < 1100 and f['low'] > 0.2):
        return 'kick'
    if f['centroid'] > 4800 or (f['high'] > 0.4 and f['decay_ms'] < 220):
        return 'hat'
    return 'snare'


def _event_measures(y, t, fr, S, freqs, st):
    """Sound measures of the hit at clip time t (mono 22.05 kHz y; STFT power S at hop HOP)."""
    lv, lt = rms_curve(y, SR)
    j = int(np.searchsorted(lt, t))
    w12 = int(0.12 / 0.005)
    pk_i = j + int(np.argmax(lv[j:j + w12])) if j < len(lv) else len(lv) - 1
    peak = float(lv[pk_i]) if len(lv) else -120.0
    pre = lv[max(0, j - int(0.3 / 0.005)):max(0, j - int(0.03 / 0.005))]
    pre_db = float(np.mean(pre) - peak) if len(pre) else -20.0
    tail = lv[pk_i:pk_i + int(1.0 / 0.005)]
    below = np.where(tail < peak - 12)[0]
    decay_ms = float(below[0] * 5) if len(below) else 1000.0
    head = lv[max(0, pk_i - int(0.2 / 0.005)):pk_i + 1]
    under = np.where(head < peak - 12)[0]          # last moment 12 dB under the peak before it
    attack_ms = float((len(head) - 1 - under[-1]) * 5) if len(under) else 200.0
    a, b = int(np.searchsorted(st, t - 0.005)), int(np.searchsorted(st, t + 0.1))
    spec = S[:, a:max(a + 1, b)].sum(axis=1) + 1e-12
    tot = spec.sum()
    centroid = float((spec * freqs).sum() / tot)
    low = float(spec[freqs < 200].sum() / tot)
    high = float(spec[freqs > 5000].sum() / tot)
    k = int(t * align.FPS_FX)
    flat = float(fr['flat'][k:k + 6].max()) if k < len(fr['flat']) else 0.0
    tonal = float(np.median(fr['tonal'][k:k + 15])) if k < len(fr['tonal']) else 0.0
    # talking around the hit (tonal low band while loud), before and after it
    fdb, ton = fr['db'], fr['tonal']
    loud = fdb > np.median(fdb) - 10
    zone = np.zeros(len(fdb), bool)
    zone[max(0, int((t - 0.45) * align.FPS_FX)):max(0, int((t - 0.04) * align.FPS_FX))] = True
    zone[int((t + 0.18) * align.FPS_FX):int((t + 0.9) * align.FPS_FX)] = True
    zl = zone & loud
    speech = float((ton[zl] < 0.06).mean()) if zl.sum() >= 4 else 0.0
    # when does talking / a new strong sound start after the hit (the snippet ends before it)
    stop = None
    for kk in range(int((t + 0.15) * align.FPS_FX), min(len(fdb), int((t + 1.0) * align.FPS_FX))):
        seg = slice(kk, kk + 8)
        if loud[kk] and np.mean(ton[seg] < 0.06) > 0.7 and np.mean(loud[seg]) > 0.7:
            stop = kk / align.FPS_FX
            break
    return {'peak_db': round(peak, 2), 'pre_db': round(pre_db, 2), 'decay_ms': decay_ms, 'attack_ms': attack_ms,
            'centroid': round(centroid), 'low': round(low, 3), 'high': round(high, 3), 'flat': round(flat, 3),
            'tonal': round(tonal, 3), 'speech': round(speech, 3), 'talk_at': stop,
            'rise_db': round(peak - float(np.mean(pre)) if len(pre) else 20.0, 2)}


def _refine(env, t, sr=SR, hop=HOP, win=0.03):
    """Onset time -> the onset-envelope peak within +-win (same detector as the song)."""
    a, b = int(max(0, (t - win) * sr / hop)), int(min(len(env), (t + win) * sr / hop + 1))
    if b - a < 2:
        return t
    return float((a + int(np.argmax(env[a:b]))) * hop / sr)


def shot_events(shot, idx=0, max_events=None, cache_dir=None):
    """Usable sound events of one library shot, best first (see module doc)."""
    path = shot['path']
    key = None
    if cache_dir:
        try:
            s = os.stat(path)
            key = os.path.join(cache_dir, 'ev_' + hashlib.md5(f'{os.path.abspath(path)}|{s.st_size}|{s.st_mtime}|{EV_VERSION}'
                                                             .encode()).hexdigest()[:16] + '.json')
            if os.path.exists(key):
                with open(key, encoding='utf-8') as f:
                    evs = json.load(f)
                for e in evs:
                    e['shot'] = idx
                return evs
        except OSError:
            key = None
    evs = _shot_events(shot, idx, max_events)
    if key:
        with open(key, 'w', encoding='utf-8') as f:
            json.dump(evs, f)
    return evs


def _shot_events(shot, idx, max_events):
    import librosa
    path = shot['path']
    if shot.get('has_text'):
        return []
    pr = probe(path)
    if not pr['has_audio']:
        return []
    y = load_audio(path, SR, 1)
    if y is None or np.abs(y).max() < 1e-4:
        return []
    fr = align.sfx_frames(y)
    long_s = len(y) / SR
    max_events = max_events or max(6, int(long_s / 2.5))
    cands = _sfx_candidates(fr, max_hits=max(8, int(long_s * 1.2)))
    for h in shot.get('sfx_hits') or []:
        if all(abs(h - t) > 0.1 for t, _ in cands):
            cands.append((float(h), 0.3))
    if not cands:
        return []
    _, _, env = onsets(y)
    S = np.abs(librosa.stft(y, n_fft=1024, hop_length=HOP)) ** 2
    freqs = librosa.fft_frequencies(sr=SR, n_fft=1024)
    st = np.arange(S.shape[1]) * HOP / SR
    vis = _visual(path, pr)
    if vis is None:
        return []
    fps = vis['fps']
    dur = min(pr['dur'] or len(y) / SR, vis['t'][-1] + 1 / fps)
    cut_t = [vis['t'][k] for k in vis['cuts']]
    src = shot.get('source_id') or os.path.splitext(os.path.basename(path))[0]
    title = shot.get('source_title') or ''
    aspect = pr['w'] / max(1, pr['h'])
    cb = shot.get('content_box')
    if cb:
        aspect = aspect * (cb[2] - cb[0]) / max(1e-3, cb[3] - cb[1])
    evs = []
    yp = frp = Sp = None
    yh = Sh = Spp = None
    for t, st_ in cands:
        t = _refine(env, t)
        if t < 0.03 or t > dur - 0.1:
            continue
        f = _event_measures(y, t, fr, S, freqs, st)
        if f['peak_db'] < -45:
            continue
        cls = _classify(f)
        perc = False
        if f['attack_ms'] > 120 and f['decay_ms'] >= 900:
            # the hit sits on the clip's music bed / a held tone, so its level never drops 12 dB around it.
            # Its percussive layer (HPSS) often is a clean transient (measured on the library's sound clips:
            # rise 11-22 dB, decay 75-380 ms, like the references' hits); use that layer for this hit.
            if yp is None:
                yp = librosa.effects.percussive(y, margin=2.0)
                frp = align.sfx_frames(yp)
                Sp = np.abs(librosa.stft(yp, n_fft=1024, hop_length=HOP)) ** 2
            fp = _event_measures(yp, t, frp, Sp, freqs, st)
            if fp['attack_ms'] <= 110 and fp['decay_ms'] < 700 and fp['rise_db'] >= 9 and fp['peak_db'] > -42:
                f = dict(fp, speech=f['speech'], talk_at=f['talk_at'])
                cls = _classify(f)
                perc = True
        # owner 2026-10-12 ("too much voice; the hits must be action sounds"): a shout / grunt / line is HARMONIC
        # (voiced pitch), a punch / gunshot / blade / impact is percussive or noisy. The harmonic share of the 30-180 ms
        # after the hit (HPSS, 150-4000 Hz) marks voiced hits as 'voice' (<= 1 per edit, heavily down-ranked).
        if yh is None:
            yh_, yp_ = librosa.effects.hpss(y, margin=1.0)
            Sh = np.abs(librosa.stft(yh_, n_fft=1024, hop_length=HOP)) ** 2
            Spp = np.abs(librosa.stft(yp_, n_fft=1024, hop_length=HOP)) ** 2
            yh = True
        a_, b_ = int(np.searchsorted(st, t + 0.03)), int(np.searchsorted(st, t + 0.18))
        band = (freqs >= 150) & (freqs <= 4000)
        eh = float(Sh[band, a_:max(a_ + 1, b_)].sum()) + 1e-12
        ep = float(Spp[band, a_:max(a_ + 1, b_)].sum()) + 1e-12
        f['harm'] = round(eh / (eh + ep), 3)
        if cls != 'voice' and f['harm'] >= VOICE_HARM and f['centroid'] < 3800 and f['flat'] < 0.35:
            cls = 'voice'
        # scene around the hit
        before = [c for c in cut_t if c <= t + 0.02]
        after = [c for c in cut_t if c > t + 0.02]
        s0 = before[-1] if before else 0.0
        s1 = after[0] if after else dur
        split_pre = False
        pre_room, post_room = t - s0, s1 - t
        if pre_room < PREROLL_MIN + 0.01 and len(before) >= 1:
            # the source cuts to the impact: the wind-up is the previous source shot
            p0 = before[-2] if len(before) >= 2 else 0.0
            if s0 - p0 >= 0.12:
                pre_room, split_pre = t - p0, True
        # visible impact: biggest picture change within +-3 frames of the hit
        ks = np.where(np.abs(vis['t'] - t) <= 3.01 / fps)[0]
        ks = ks[ks >= 1]
        if not len(ks):
            continue
        kimp = int(ks[np.argmax(vis['diff'][ks])])
        vis_dt = float(vis['t'][kimp] - t)
        in_scene = (vis['t'] >= s0) & (vis['t'] < s1)
        base = float(np.median(vis['diff'][in_scene][1:])) if in_scene.sum() > 3 else float(np.median(vis['diff']))
        prom = float(vis['diff'][kimp] / (base + 1.0))
        # motion before the impact (the wind-up): long wind-up -> longer pre-roll
        kb = np.where((vis['t'] >= t - 0.3) & (vis['t'] < t - 0.02) & in_scene)[0]
        wind = float(np.mean(vis['diff'][kb]) / (base + 1.0)) if len(kb) else 0.0
        near = np.where(in_scene & (np.abs(vis['t'] - t) < 0.4))[0]
        nm = np.where(in_scene & (np.abs(vis['t'] - t) < 0.25))[0]
        mot = float(np.mean(vis['diff'][nm])) if len(nm) else 0.0      # absolute motion around the hit (0-255 grey)
        luma = float(np.mean(vis['luma'][near])) if len(near) else float(np.mean(vis['luma']))
        col = vis['col'][near].mean(axis=0).tolist() if len(near) else [0, 0, 0]
        sharp = float(np.mean(vis['lap'][near])) if len(near) else 0.0
        # scores
        strength = float(np.clip(0.55 * st_ + 0.45 * np.clip((f['rise_db'] - 3) / 20, 0, 1), 0, 1))
        clean = float(np.clip((-f['pre_db'] - 4) / 12, 0, 1))
        vsync = 1.0 if abs(vis_dt) <= 2.01 / 24 else max(0.0, 1 - (abs(vis_dt) - 2 / 24) / 0.1)
        vprom = float(np.clip((prom - 1) / 3, 0, 1))
        c = f['centroid']
        # refs: hits are bright (centroid p10 2.3 kHz, median 3.4 kHz); booms get masked by the song's bass
        bright = float(np.interp(c, [300, 2000, 5000, 9000], [0.0, 1.0, 1.0, 0.3]))
        aest = float(shot.get('aesthetic', 0.5) or 0.5)
        # crisp: a real transient that decays (refs: -12 dB after 110-900 ms), not a rumble or BGM swell
        crisp = float(np.clip(1 - (f['decay_ms'] - 600) / 400, 0, 1)) * float(np.clip(1 - (f['attack_ms'] - 30) / 120, 0.3, 1))
        score = (0.22 * strength + 0.10 * clean + 0.12 * vsync + 0.12 * vprom + 0.16 * bright + 0.08 * aest
                 + 0.11 * (1 - min(1.0, f['speech'] / 0.4)) + 0.09 * crisp)
        why = None
        if f['speech'] > 0.3 and cls != 'voice':
            why = 'talking'
        elif cls == 'voice' and f['decay_ms'] > 700:
            why = 'dialogue'
        elif post_room < 0.12:
            why = 'no_room_after'
        elif pre_room < PREROLL_MIN:
            why = 'no_room_before'
        elif luma < 0.05:
            why = 'dark'
        elif prom < 1.3 and not (prom >= 1.0 and mot >= 2.5):
            # the sound has no visible action (off-screen, a held drawing, a sound over a static shot). The
            # references always have one: their visible action lands 1-7 frames@24 after the cut, p25 2,
            # median 3 (sound.json visual_action_frame_after_cut_frames24), never "nothing".
            why = 'nothing_moves'
        elif abs(vis_dt) > 0.11:
            why = 'impact_off'          # refs: the visible impact is within +-2 frames@24 of the hit
        elif f['attack_ms'] > 120 and f['decay_ms'] >= 900 and strength < 0.8:
            why = 'not_crisp'           # a swell / rumble / music bed, not a hit
        if split_pre:
            score -= 0.08
        if aspect < 1.2:
            score -= 0.15                # vertical sources pillarbox in the 16:9 master
        if shot.get('has_speech'):
            score -= 0.03
        pref = PREROLL_CLS.get(cls, PREROLL_DEF)
        if wind > 1.6:
            pref += 0.05
        elif wind < 0.5:
            pref -= 0.03
        ev = {'id': f'{idx}:{round(t, 3)}', 'shot': idx, 'path': path, 'source_id': src, 'source_title': title,
              'compilation': bool(COMPILATION_RE.search(title or '')), 'scene': f'{path}#{round(s0, 3)}',
              't': round(t, 4), 'cls': cls, 'strength': round(strength, 3), 'score': round(float(score), 4),
              'pre_room': round(pre_room, 3), 'post_room': round(post_room, 3), 'split_pre': split_pre,
              'scene_t': [round(s0, 3), round(s1, 3)], 'vis_dt': round(vis_dt, 3), 'prominence': round(prom, 2),
              'windup': round(wind, 2), 'perc': perc, 'motion_abs': round(mot, 2), 'preroll': round(float(np.clip(pref, PREROLL_MIN, PREROLL_MAX)), 3),
              'luma': round(luma, 3), 'col': [round(x, 1) for x in col], 'sharp': round(sharp, 2),
              'aspect': round(aspect, 3), 'fps': fps, 'dur': round(dur, 3), 'reject': why,
              'tags': shot.get('tags') or [], 'query': shot.get('query') or '', 'content_box': cb}
        ev.update(f)
        evs.append(ev)
    # one event per 0.2 s, best first
    evs.sort(key=lambda e: -e['score'])
    kept = []
    for e in evs:
        if all(abs(e['t'] - k['t']) >= 0.2 for k in kept):
            kept.append(e)
    return kept[:max_events]


def load_shots(paths_or_shots):
    """Library shots from shots.json paths (or a list of dicts / video paths)."""
    out = []
    items = paths_or_shots if isinstance(paths_or_shots, (list, tuple)) else [paths_or_shots]
    for it in items:
        if isinstance(it, dict):
            out.append(it)
            continue
        if isinstance(it, str) and it.lower().endswith('.json'):
            with open(it, encoding='utf-8') as f:
                d = json.load(f)
            lst = d.get('shots') if isinstance(d, dict) else d
            if isinstance(d, dict) and lst is None:
                lst = (d.get('edit') or []) + (d.get('clips') or [])
            base = os.path.dirname(os.path.abspath(it))
            for s in lst or []:
                s = dict(s)
                if s.get('path') and not os.path.isabs(s['path']) and not os.path.exists(s['path']):
                    s['path'] = os.path.join(base, s['path'])
                if s.get('path') and os.path.exists(s['path']):
                    out.append(s)
        elif isinstance(it, str) and os.path.exists(it):
            out.append({'path': it})
    return out


def detect_events(shots, cache_dir=None, keep_rejected=False):
    """Events of all shots, best first. Rejected ones only with keep_rejected (for the report)."""
    evs = []
    for i, s in enumerate(shots):
        try:
            e = shot_events(s, i, cache_dir=cache_dir)
        except Exception as ex:          # one bad file never stops the edit
            log(f'events failed {s.get("path")}: {ex}')
            e = []
        evs += e
    evs.sort(key=lambda e: -e['score'])
    return evs if keep_rejected else [e for e in evs if not e['reject']]


# ---------------------------------------------------------------- theme

def song_theme(title):
    t = (title or '').lower()
    for k, words in THEME_WORDS.items():
        if any(re.search(r'\b' + re.escape(w) + r'\b', t) for w in words):
            return k
    return None


def library_queries(title=None, theme=None, n=6, seed=0):
    """Library queries for a sound edit: >= 60 % from the song's own sound theme (HandClap -> claps),
    the rest a mix of crisp hit types."""
    rng = random.Random(seed)
    theme = theme or song_theme(title)
    out = []
    if theme in THEMES:
        q = list(THEMES[theme])
        rng.shuffle(q)
        out += q[:max(1, int(math.ceil(0.6 * n)))]
    mix = [k for k in ('gun', 'sword', 'punch', 'click', 'stomp', 'snap', 'clap', 'knock', 'misc') if k != theme]
    rng.shuffle(mix)
    i = 0
    while len(out) < n and i < 50:
        k = mix[i % len(mix)]
        cand = [q for q in THEMES[k] if q not in out]
        if cand:
            out.append(rng.choice(cand))
        i += 1
    return out[:n], theme


def theme_match(e, theme):
    if not theme:
        return 0.0
    words = THEME_WORDS.get(theme, []) + [theme]
    text = ' '.join([e.get('query') or '', e.get('source_title') or '', ' '.join(e.get('tags') or [])]).lower()
    return 1.0 if any(w in text for w in words) else 0.0


# ---------------------------------------------------------------- assignment

TIMBRE = {  # song hit band / drum class -> clip class affinity
    'low': {'kick': 1.0, 'snare': 0.4, 'hat': 0.0, 'whoosh': 0.2, 'voice': 0.3},
    'mid': {'kick': 0.4, 'snare': 1.0, 'hat': 0.5, 'whoosh': 0.3, 'voice': 0.4},
    'high': {'kick': 0.0, 'snare': 0.5, 'hat': 1.0, 'whoosh': 0.4, 'voice': 0.2},
    'kick': {'kick': 1.0, 'snare': 0.3, 'hat': 0.0, 'whoosh': 0.1, 'voice': 0.1},
    'snare': {'kick': 0.3, 'snare': 1.0, 'hat': 0.4, 'whoosh': 0.2, 'voice': 0.2},
    'hat': {'kick': 0.0, 'snare': 0.4, 'hat': 1.0, 'whoosh': 0.3, 'voice': 0.0},
}


SCENE_REPEAT_PEN = float(os.environ.get('SOUND_SCENE_REPEAT_PEN', '6.0'))


def assign(hits, events, end_t, mode='accent', theme=None, rng=None, cap_scale=1.0):
    """One event per hit (hits in edit time, 'e'): Hungarian assignment of the event score, weak timbre
    match (strong in build mode), theme and feasibility, then variety fixes (<= 2 per source video or
    <= 4 per compilation, one per source scene, never the same video twice in a row, <= 1 voice)."""
    from scipy.optimize import linear_sum_assignment
    events = events[:max(300, 4 * len(hits))]
    n, m = len(hits), len(events)
    if not n or m < n:
        raise ValueError(f'{m} events for {n} hits')
    w_timbre = 0.45 if mode == 'build' else 0.12
    smax = max(h['strength'] for h in hits) or 1.0
    C = np.zeros((n, m))
    for i, h in enumerate(hits):
        nxt = hits[i + 1]['e'] if i + 1 < n else end_t
        need_post = (nxt - PREROLL_DEF - h['e']) if i + 1 < n else (end_t - h['e'])
        gap_before = h['e'] - (hits[i - 1]['e'] if i else -1.0)
        hs = h['strength'] / smax
        for j, e in enumerate(events):
            c = -e['score'] - w_timbre * TIMBRE[h['band']].get(e['cls'], 0.3) - 0.2 * hs * e['strength']
            if theme:
                c -= 0.3 * e.get('_theme', 0.0)
            if e['post_room'] < need_post - 0.1:
                c += 3.0 + (need_post - e['post_room'])
            elif e['post_room'] < need_post:
                c += 0.3
            # room before the hit inside its own source scene, after the frame kept clear of the scene cut:
            # without it the cut lands on the impact and no wind-up is ever seen, which is the whole style
            room = e['pre_room'] - scene_margin(e)
            if room < PREROLL_MIN:
                c += 2.0 + (3.0 if i == 0 else 0.0)
            elif room < PREROLL_DEF:
                c += 0.4 * (PREROLL_DEF - room) / PREROLL_DEF
            if mode != 'build' and h['band'] != 'low':
                c += 0.3 * float(np.clip((1500 - e['centroid']) / 1100, 0, 1))     # a boom on a bright onset
            # a hit far under its clip's own level has to be lifted with the clip's whole background behind
            # it, and is past the +-12 dB the engine's own mixer gives it if it renders the plan itself
            c += 0.18 * float(np.clip((-18.0 - e['peak_db']) / 12.0, 0, 1))
            if h.get('silent') and (e['cls'] == 'voice' or e.get('speech', 0) > 0.3):
                c += 2.5                     # a mouth moving with its voice muted
            if e.get('silent_only') and not h.get('silent'):
                c += 6.0                     # its sound is not a crisp hit: picture only, never heard
            elif h.get('silent') and e.get('silent_only'):
                c -= 0.3                     # keep the crisp events for the audible hits
            if e['cls'] == 'voice':
                c += 1.2 if hs >= 0.7 else 1.8      # refs: words never, a grunt / shout only as the odd hit
            elif e.get('voiced'):
                c += VOICED_COST                   # harmonic (voice over the hit / BGM): an action hit first
            if e['cls'] == 'whoosh' and gap_before < 0.4:
                c += 0.4
            C[i, j] = c
    pen = np.zeros_like(C)
    src_of = [e['source_id'] for e in events]
    for _ in range(12):
        r, c = linear_sum_assignment(C + pen)
        order = np.argsort(r)
        seq = [int(c[k]) for k in order]
        bad = False
        # per source cap and per scene cap
        by_src, by_scene = {}, {}
        for i, j in enumerate(seq):
            by_src.setdefault(src_of[j], []).append(i)
            by_scene.setdefault(events[j]['scene'], []).append(i)
        for s, lst in by_src.items():
            cap = int(math.ceil(src_cap(events[seq[lst[0]]]['compilation']) * cap_scale))
            if len(lst) > cap:
                bad = True
                worst = sorted(lst, key=lambda i: (C + pen)[i, seq[i]])[cap:]
                for i in worst:
                    pen[i, [j for j in range(m) if src_of[j] == s]] += 1.5
        for s, lst in by_scene.items():
            if len(lst) > 1:
                bad = True
                for i in sorted(lst, key=lambda i: (C + pen)[i, seq[i]])[1:]:
                    # owner 2026-10-12 "no repeated moments": a repeat must cost more than a tight fit (+3..)
                    pen[i, [j for j in range(m) if events[j]['scene'] == s]] += SCENE_REPEAT_PEN
        for i in range(1, n):
            a, b = events[seq[i - 1]], events[seq[i]]
            if a['path'] == b['path'] or a['source_id'] == b['source_id']:
                # the same video twice in a row reads as a jump, not a cut (even in a compilation)
                bad = True
                pen[i, [j for j in range(m) if events[j]['source_id'] == b['source_id']]] += 1.0
        voices = [i for i, j in enumerate(seq) if events[j]['cls'] == 'voice']
        if len(voices) > 1:
            bad = True
            for i in voices[1:]:
                pen[i, [j for j in range(m) if events[j]['cls'] == 'voice']] += 1.5
        if not bad:
            break
    return repair(seq, hits, events, C, end_t, cap_scale=cap_scale)


def _need_post(i, hits, end_t):
    nxt = hits[i + 1]['e'] if i + 1 < len(hits) else end_t
    return (nxt - PREROLL_DEF - hits[i]['e']) if i + 1 < len(hits) else (end_t - hits[i]['e'])


def repair(seq, hits, events, C, end_t, rounds=40, cap_scale=1.0):
    """The penalised Hungarian rounds can still leave the same video twice in a row (a jump, not a cut),
    a scene used twice or a source over its cap. Swap two shots, or replace one with an unused event,
    whenever that removes a violation and both shots stay feasible; take the cheapest such move."""
    from collections import Counter
    n = len(seq)
    seq = list(seq)

    def cap(j):
        return int(math.ceil(src_cap(events[j]['compilation']) * cap_scale))

    def viol(s):
        v = 2.0 * sum(1 for i in range(1, n) if events[s[i - 1]]['source_id'] == events[s[i]]['source_id'])
        v += 6.0 * sum(c - 1 for c in Counter(events[j]['scene'] for j in s).values())
        src = Counter(events[j]['source_id'] for j in s)
        caps = {events[j]['source_id']: cap(j) for j in s}
        v += 1.0 * sum(max(0, c - caps[k]) for k, c in src.items())
        v += 1.0 * max(0, sum(1 for j in s if events[j]['cls'] == 'voice') - 1)
        return v

    def feas(i, j):
        e = events[j]
        return (e['post_room'] >= _need_post(i, hits, end_t) - 0.1
                and e['pre_room'] - scene_margin(e) >= (PREROLL_MIN if i else PREROLL_MIN + 0.02)
                and not (e.get('silent_only') and not hits[i].get('silent')))
    free = [j for j in range(len(events)) if j not in set(seq)]
    for _ in range(rounds):
        v0 = viol(seq)
        if v0 <= 0:
            break
        best = None
        for i in range(n):
            for k in range(i + 1, n):
                a, b = seq[i], seq[k]
                if not (feas(i, b) and feas(k, a)):
                    continue
                cand = list(seq)
                cand[i], cand[k] = b, a
                key = (viol(cand), C[i, b] + C[k, a] - C[i, a] - C[k, b])
                if key[0] < v0 and (best is None or key < best[0]):
                    best = (key, cand, None)
            for j in free:
                if not feas(i, j):
                    continue
                cand = list(seq)
                cand[i] = j
                key = (viol(cand), C[i, j] - C[i, seq[i]])
                if key[0] < v0 and (best is None or key < best[0]):
                    best = (key, cand, (i, j))
        if best is None:
            break
        seq = best[1]
        if best[2] is not None:
            free = [j for j in range(len(events)) if j not in set(seq)]
    # owner 2026-10-12 "no repeated moments" is HARD: a scene still shown twice takes the cheapest unused scene even
    # if its room is tight (the renderer then holds a few frames), never the same moment again
    seen = set()
    for i in range(n):
        sc = events[seq[i]]['scene']
        if sc not in seen:
            seen.add(sc)
            continue
        used_sc = {events[j]['scene'] for j in seq}
        nb = {events[seq[k]]['source_id'] for k in (i - 1, i + 1) if 0 <= k < n}
        cands = [j for j in range(len(events)) if j not in set(seq) and events[j]['scene'] not in used_sc
                 and events[j]['source_id'] not in nb
                 and not (events[j].get('silent_only') and not hits[i].get('silent'))]
        if cands:
            j = min(cands, key=lambda j: (not feas(i, j), C[i, j]))
            log(f'repeat removed: shot {i} {events[seq[i]]["scene"]} -> {events[j]["scene"]} (feasible {feas(i, j)})')
            seq[i] = j
            seen.add(events[j]['scene'])
    return [events[j] for j in seq]


# ---------------------------------------------------------------- timing

def frame_round(t, fps=FPS_OUT):
    return round(t * fps) / fps


def scene_margin(e):
    """Keep one source frame (+ output rounding) away from a source scene cut: never flash the next or
    the previous source shot for a frame."""
    return 1.0 / max(12.0, float(e.get('fps') or 24.0)) + 0.009


def shot_timing(H, evs, L, fps=FPS_OUT, rng=None, avoid=None):
    """Pre-roll per shot and the cuts: cut_i = H_i - preroll_i on the output frame grid, as close as possible
    to the clip's own pre-roll while the previous scene lasts until the cut, the previous impact stays
    visible >= 2 frames@24 and the cut keeps >= 40 ms from the song's onsets (`avoid`, edit s; the refs
    cut on an onset only 10 % of the time). Returns (pre, cuts, holds): holds[i] = seconds shot i must
    freeze its last frame because its scene ends before the next cut (the assignment avoids it)."""
    rng = rng or random.Random(0)
    n = len(H)
    avoid = np.asarray(avoid if avoid is not None else [], float)
    pre_room = [e['pre_room'] - scene_margin(e) for e in evs]
    post_room = [e['post_room'] - scene_margin(e) for e in evs]
    pref = [e['preroll'] + rng.uniform(-0.015, 0.015) for e in evs]
    # the per-class pre-rolls order the shots the way the wind-up needs (a hat needs less than a stomp),
    # but a kick-heavy pool would push the whole edit to ~190 ms. The references' pooled median is 140 ms
    # (p25 82, p75 210, sound.json preroll_cut_to_hit_ms): re-centre this edit's prefs on it, keeping order.
    if n > 1:
        pref = [float(np.clip(p + (PREROLL_DEF - float(np.median(pref))), PREROLL_MIN, PREROLL_MAX)) for p in pref]

    def pass_(pref):
        pre, cuts = [0.0] * n, [0.0] * n
        pre[0] = H[0]
        for i in range(1, n):
            gap = H[i] - H[i - 1]
            lo = max(PREROLL_MIN, gap - post_room[i - 1])          # the previous scene lasts until the cut
            hi = min(PREROLL_MAX, pre_room[i], gap - IMPACT_VISIBLE, H[i] - cuts[i - 1] - MIN_SHOT)
            if hi < lo:
                # infeasible: keep this shot's own scene clean, the previous shot holds its last frame
                lo = min(max(PREROLL_MIN, 0.0), hi) if hi > 0 else 0.0
            k0, k1 = int(math.ceil((H[i] - hi) * fps - 1e-6)), int(math.floor((H[i] - lo) * fps + 1e-6))
            cand = [k / fps for k in range(k0, k1 + 1)] or [frame_round(H[i] - min(max(pref[i], lo), max(lo, hi)), fps)]
            best, best_c = None, 1e9
            for c in cand:
                if c <= cuts[i - 1] + MIN_SHOT - 1e-6:
                    continue
                p = H[i] - c
                cost = abs(p - pref[i]) / 0.05
                if len(avoid) and np.min(np.abs(avoid - c)) < 0.042:
                    # the references cut on a song onset only 10 % of the time, but that is a tendency, not
                    # a rule: keep it a tie-break (worth ~35 ms of pre-roll), or a dense song drags every cut
                    # off the pre-roll the wind-up needs
                    cost += 0.7
                if cost < best_c:
                    best, best_c = c, cost
            if best is None:
                best = frame_round(cuts[i - 1] + MIN_SHOT, fps)
            cuts[i] = best
            pre[i] = H[i] - best
        return pre, cuts
    pre, cuts = pass_(pref)
    # the feasible window (the clip's own room before its hit, the previous scene's room after its hit and
    # the onsets to dodge) is one-sided per shot, so the achieved median can sit well under the references'
    # 140 ms even when every pref is 140. Shift the prefs by the shortfall and try again.
    for _ in range(3):
        if n < 3:
            break
        got = float(np.median(pre[1:]))
        if abs(got - PREROLL_DEF) <= 0.012:
            break
        pref = [float(np.clip(p + (PREROLL_DEF - got), PREROLL_MIN, PREROLL_MAX)) for p in pref]
        pre, cuts = pass_(pref)
    holds = [0.0] * n
    ends = cuts[1:] + [frame_round(L, fps)]
    for i in range(n):
        need = ends[i] - H[i]
        if need > post_room[i] + 1e-6:
            holds[i] = round(need - post_room[i], 4)
    return pre, cuts, holds


# ---------------------------------------------------------------- audio mix

_SOS = {}


def _hp(x, f=100.0):
    if f not in _SOS:
        _SOS[f] = signal.butter(2, f / (MSR / 2), 'high', output='sos')
    return signal.sosfilt(_SOS[f], x, axis=-1)


def _peaking(x, f0=3500.0, gain_db=2.0, q=1.0):
    A = 10 ** (gain_db / 40)
    w = 2 * math.pi * f0 / MSR
    al = math.sin(w) / (2 * q)
    b = [1 + al * A, -2 * math.cos(w), 1 - al * A]
    a = [1 + al / A, -2 * math.cos(w), 1 - al / A]
    return signal.lfilter(np.array(b) / a[0], np.array(a) / a[0], x, axis=-1)


def _clip_audio(path, cache):
    """(2, n) clip audio at MSR; 'path#perc' = its percussive layer (librosa HPSS, margin 2): the hit with the
    clip's music bed / held tones taken out (used for hits that are only crisp without their BGM)."""
    if path not in cache:
        if path.endswith('#perc'):
            y = _clip_audio(path[:-5], cache)
            if y is not None:
                import librosa
                y = np.stack([librosa.effects.percussive(np.ascontiguousarray(c, dtype=np.float32), margin=2.0)
                              for c in y])
            cache[path] = y
        else:
            cache[path] = load_audio(path, MSR, 2)
    return cache[path]


def snippet(e, H, nxt, L, cache):
    """The gated hit of event e as mono float, starting 30 ms before the hit, and its sfx entry."""
    y = _clip_audio(e['path'] + ('#perc' if e.get('perc') else ''), cache)
    if y is None:
        return None, None
    mono = y.mean(axis=0)
    a = e['t']
    end_edit = min(H + 0.9, (nxt - 0.025) if nxt is not None else L, L)
    if e.get('talk_at'):
        end_edit = min(end_edit, H + (e['talk_at'] - a) - 0.01)
    length = max(0.08, end_edit - (H - 0.03))
    i0 = int(round((a - 0.03) * MSR))
    seg = mono[max(0, i0):max(0, i0) + int(length * MSR)].astype(np.float64)
    if i0 < 0:
        seg = np.concatenate([np.zeros(-i0), seg])[:int(length * MSR)]
    if len(seg) < int(0.05 * MSR):
        return None, None
    hp = 0.0 if (e['cls'] == 'kick' or e['low'] > 0.35) else 100.0
    if hp:
        seg = _hp(seg, hp)
    seg = _peaking(seg, 3500.0, 2.0, 1.0)
    env = np.ones(len(seg))
    fi0, fi1 = int(0.012 * MSR), int(0.020 * MSR)
    env[:fi0] = 0.0
    env[fi0:fi1] = np.linspace(0, 1, fi1 - fi0)
    fo = min(int(0.03 * MSR), len(seg) // 3)
    env[len(seg) - fo:] *= np.linspace(1, 0, fo)
    seg = seg * env
    # engine semantics (core/edit/engine.py): clip_t = the hit in the clip, at = the output time it lands on,
    # gain = dB offset on the engine's own levelling (hit 20 ms RMS = music + 5 dB); the rest documents our mix
    entry = {'clip_t': round(a - ENGINE_LEAD, 4), 'at': round(H - ENGINE_LEAD, 4), 'gain': 0.0,
             'hit_clip_t': round(a, 4), 'hit_at': round(H, 4), 'dur': round(len(seg) / MSR - 0.03, 4),
             'snippet_clip_t': round(a - 0.03, 4), 'snippet_at': round(H - 0.03, 4), 'fade_in_at': 0.012,
             'fade_in': 0.008, 'fade_out': round(fo / MSR, 4), 'highpass': hp, 'presence_db': 2.0, 'layers': 1,
             'cls': e['cls']}
    return seg, entry


def engine_snippet(path, clip_t, at, nxt, cache):
    """The snippet core/edit/engine.py's own sfx mixer (_mix_hit) builds for this entry, if it renders the
    plan instead of using our finished audio_mix, before its make-up gain: (segment (2, n) starting
    ENGINE_PRE before the anchor, its 20 ms full-band RMS over the first 120 ms, the same segment's 46 ms
    band-limited RMS - the level the study's tools measure). The engine levels the first number to
    music + sfx_over_music_db + entry["gain"], so the gain carries the difference between the two."""
    y = _clip_audio(path, cache)
    if y is None:
        return None
    pre = ENGINE_PRE
    Ls = 0.9 if nxt is None else max(0.06, min(0.9, nxt - at - 0.025))
    j0 = int((clip_t - pre) * MSR)
    if j0 < 0:
        return None
    seg = y[:, j0:j0 + int((pre + Ls) * MSR)].astype(np.float64)
    if seg.shape[1] < int(0.03 * MSR):
        return None
    mono = seg.mean(axis=0)
    spec = np.abs(np.fft.rfft(mono * np.hanning(len(mono))))
    freqs = np.fft.rfftfreq(len(mono), 1 / MSR)
    if spec[freqs < 150].sum() / max(1e-9, spec.sum()) < 0.35:
        seg = _hp(seg, 100.0)
    seg = _peaking(seg, 3500.0, 2.0, 1.0)
    tt = np.arange(seg.shape[1]) / MSR
    seg = seg * (np.clip((tt - (pre - 0.008)) / 0.008, 0, 1) * np.clip((seg.shape[1] / MSR - tt) / 0.03, 0, 1))
    k0, k1, w20 = int(pre * MSR), int((pre + 0.12) * MSR), int(0.02 * MSR)
    body = (seg[:, k0:k1] ** 2).mean(axis=0)
    if len(body) < w20:
        return None
    rms20 = math.sqrt(float(np.convolve(body, np.ones(w20) / w20, mode='valid').max()) + 1e-12)
    lv, _ = rms_curve(_band(seg.mean(axis=0))[k0:int((pre + 0.19) * MSR)], MSR, win=0.0464, hop=0.0058)
    return seg, float(db(rms20)), (float(np.max(lv)) if len(lv) else -60.0)


def engine_align(shots_ev, entries, L, cache, music_db=-20.0, rounds=3):
    """Close the onset loop for the engine's own mix the way we close it for ours: build the layer the
    engine will lay on the song, find each hit with the study's detector and move that sfx entry's `at` by
    the error. The engine's gate opens at the hit instant (ours opens 30 ms earlier), so its transient peaks
    a frame or two later and the correction is not the same one. Only the engine reads `at` - our render
    plays plan["audio_mix"] - so this cannot move our own soundtrack. Returns (errors before, after) in ms."""
    live = [(ev, entry) for ev, entry in zip(shots_ev, entries) if entry]
    if not live:
        return [], []
    n = int(round(L * MSR))
    base = [float(entry['at']) for _, entry in live]

    def measure(ats):
        layer = np.zeros((2, n))
        for ((H0, e, nxt), entry), at in zip(live, ats):
            r = engine_snippet(e['path'], float(entry['clip_t']), at, nxt, cache)
            if r is None:
                continue
            seg, rms20, _ = r
            g = float(np.clip(music_db + ENGINE_SFX_DB + float(entry.get('gain') or 0.0) - rms20, -12, 12))
            i0 = int(round((at - ENGINE_PRE) * MSR))
            j0, j1 = max(0, i0), min(n, i0 + seg.shape[1])
            if j1 > j0:
                layer[:, j0:j1] += seg[:, j0 - i0:j1 - i0] * 10 ** (g / 20)
        # the anchors sit ENGINE_LEAD before the hits: the detector is asked about the hits
        _, errs = align_shifts(layer, [a + ENGINE_LEAD for a in ats])
        return [(-e / 1000.0 if e is not None and abs(e) <= 25 else 0.0) for e in errs], errs

    def score(errs):
        v = [abs(x) for x in errs if x is not None]
        return (float(np.median(v)) if v else 99.0, -len(v))
    shifts, errs0 = measure(base)
    best, best_ats = errs0, list(base)
    for _ in range(max(1, rounds)):
        ats = [float(np.clip(a + s, b - 0.03, b + 0.03)) for a, s, b in zip(best_ats, shifts, base)]
        if all(abs(a - b) < 1e-4 for a, b in zip(ats, best_ats)):
            break
        shifts, errs = measure(ats)
        # the engine's gate opens at the hit instant, so its transient is not simply our snippet moved:
        # a correction that does not actually improve the detector's reading is dropped, never kept on faith
        if score(errs) >= score(best):
            break
        best, best_ats = errs, ats
    for (_, entry), at in zip(live, best_ats):
        entry['at'] = round(at, 4)
    return errs0, best


_LP = {}


def _band(x):
    """What the study measured with (22.05 kHz analysis): x low-passed at 10.5 kHz."""
    if 'lp' not in _LP:
        _LP['lp'] = signal.butter(6, 10500 / (MSR / 2), 'low', output='sos')
    return signal.sosfilt(_LP['lp'], x)


# the references' clip-sound envelope around a hit (dB rel. the hit peak): sound.json
# pooled_measurements.hit_envelope_db_rel_peak: -4 dB at +60 ms, -7 at +120, -10 from +200 ms on
TAIL_ENV = ([0.0, 0.03, 0.06, 0.12, 0.2, 10.0], [0.0, -1.8, -4.1, -7.3, -9.7, -9.9])


def shape_tail(seg, pre=0.03, slack=2.0, next_at=None):
    """Duck the part of the snippet after its hit that is louder than the references' envelope (+slack dB):
    long rings, rumbles and the clip's BGM come down, a natural fast decay is left alone. next_at (s into
    the snippet): the next hit; the tail dips to -17 dB 60 ms before it, as the references' layer does
    (-17.1 dB at -60 ms), so every new hit rises out of near silence."""
    lv, lt = rms_curve(_band(seg), MSR)
    if not len(lv):
        return seg
    k0 = int(np.searchsorted(lt, pre))
    pk = float(np.max(lv[k0:k0 + int(0.12 / 0.005)])) if k0 < len(lv) else float(np.max(lv))
    allow = pk + slack + np.interp(np.maximum(0, lt - pre), *TAIL_ENV)
    if next_at is not None:
        dip = np.interp(lt, [next_at - 0.2, next_at - 0.06], [-9.9, -17.0])
        allow = np.minimum(allow, pk + np.where(lt >= next_at - 0.2, dip, 99.0))
    over = np.maximum(0.0, lv - allow)
    over[lt < pre + 0.02] = 0.0
    g = 10 ** (-uniform_filter1d(over, 5) / 20)
    return seg * np.interp(np.arange(len(seg)) / MSR, lt, g, left=1.0, right=float(g[-1]))


CLIP_BED_DB = float(os.environ.get('SOUND_CLIP_BED_DB', '-3.0'))   # a picture-only shot's own audio vs the music


def mix(music_win, shots_ev, L, target_db=HIT_OVER_MUSIC_DB, level_ref=None, rng=None, shifts=None, trims=None, mute=None):
    """music_win (2, n) at MSR = the song window (or the build-mode bed); shots_ev = [(H, event, next_H)].
    level_ref: (2, n) the music the hits are levelled against (the full song in build mode).
    Levels are measured like the study (band-limited to 10.5 kHz): hit peak (max 20 ms RMS in the first
    120 ms) = music RMS (+-0.3 s) + target dB. shifts: per hit seconds to move the sound (onset alignment);
    trims: per hit dB on top of the target (flux balance).
    Returns (mix (2, n) before mastering, sfx entries, report)."""
    rng = rng or random.Random(0)
    n = int(round(L * MSR))
    out = np.zeros((2, n))
    m = music_win[:, :n]
    out[:, :m.shape[1]] += m
    raw_ref = (level_ref if level_ref is not None else music_win).mean(axis=0)
    ref = _band(raw_ref)
    cache, entries, rep = {}, [], []
    shifts = shifts or [0.0] * len(shots_ev)
    trims = trims or [0.0] * len(shots_ev)

    def hit_level(x):
        # the study's measure: RMS over 1024 samples at 22.05 kHz (46 ms), max in the first 120 ms
        lv, _ = rms_curve(_band(x)[int(0.02 * MSR):int(0.19 * MSR)], MSR, win=0.0464, hop=0.0058)
        return float(np.max(lv)) if len(lv) else -60.0
    mute = set(mute or ())
    for k_, ((H0, e, nxt), sh, tr) in enumerate(zip(shots_ev, shifts, trims)):
        H = H0 + sh
        seg, entry = snippet(e, H, nxt, L, cache)
        if seg is None:
            entries.append(None)
            continue
        if k_ in mute:
            # owner 2026-10-12: "you can't have a whole section without sound from clips". The references' clip
            # layer is present 87-100 % of the time (output/newtypes ref_gaps: within 25 dB of its peaks), so a
            # shot without a crisp hit still plays its OWN audio, as a bed CLIP_BED_DB under the music (no
            # attack layer, no stacking): the clips' sound runs through the whole video, the hits sit on top
            a, b = int(max(0, H - 0.3) * MSR), int(min(L, H + 0.3) * MSR)
            mus_db = float(db(np.sqrt(np.mean(ref[a:b] ** 2) + 1e-12)))
            lv = hit_level(seg)
            g = float(np.clip(mus_db + CLIP_BED_DB - lv, -20, 14))
            seg = shape_tail(seg, next_at=(nxt - H + 0.03) if nxt is not None else None) * 10 ** (g / 20)
            i0 = int(round((H - 0.03) * MSR))
            j0, j1 = max(0, i0), min(n, i0 + len(seg))
            if j1 > j0:
                out[:, j0:j1] += seg[j0 - i0:j1 - i0][None]
            entry.update(gain=0.0, gain_abs_db=round(g, 2), layers=1, bed=True)
            entries.append(entry)
            rep.append({'H': round(H, 3), 'music_db': round(mus_db, 1), 'hit_db': round(lv + g, 1), 'gain': round(g, 1), 'bed': True})
            continue
        layers = 1
        if e['centroid'] < 1500 or e['low'] > 0.35:
            # a low hit (stomp, boom, body blow) gets a short bright attack layer, like the stacked snippets
            # of the tutorial: its own saturated sound, high-passed, 25 ms decay; 4 dB under the hit
            pk = float(np.abs(seg).max()) + 1e-9
            click = _hp(np.tanh(6 * seg / pk) * pk, 1500.0)
            tt = np.arange(len(seg)) / MSR - 0.03
            click *= np.where(tt < -0.002, 0.0, np.exp(-np.maximum(tt, 0) / 0.025))
            lc, lm = hit_level(click), hit_level(seg)
            if lc > -90:
                seg = seg + click * 10 ** ((lm - 4 - lc) / 20)
                layers = 2
        seg = shape_tail(seg, next_at=(nxt - H + 0.03) if nxt is not None else None)
        a, b = int(max(0, H - 0.3) * MSR), int(min(L, H + 0.3) * MSR)
        mus_db = float(db(np.sqrt(np.mean(ref[a:b] ** 2) + 1e-12)))
        # the engine's own music reference (engine.py build_audio env_rms): +-0.3 s RMS of the mean of the
        # channels' squares, not of the mid signal, so it has to be measured its way
        mus_eng = float(db(np.sqrt(np.mean((music_win[:, a:b].astype(np.float64) ** 2).mean(axis=0)) + 1e-12)))
        hit_db = hit_level(seg)
        tgt = target_db + rng.uniform(-0.8, 0.8) + tr
        # float mix before the master: any gain is fine for the level (the master normalises the sum) and
        # a snippet's own signal-to-noise does not change with it; only near-silent clips are capped
        g = float(np.clip(mus_db + tgt - hit_db, -14, 26))
        if g > 18:
            # stack a brightened, saturated duplicate instead of more gain (the tutorial stacks copies)
            dup = np.tanh(3 * _hp(seg, 900.0) / (np.abs(seg).max() + 1e-9)) * (np.abs(seg).max() + 1e-9) / 3
            seg = seg + 0.7 * dup
            hit_db = hit_level(seg)
            g = float(np.clip(mus_db + tgt - hit_db, -14, 24))
            layers += 1
        seg = seg * 10 ** (g / 20)
        i0 = int(round((H - 0.03) * MSR))
        j0, j1 = max(0, i0), min(n, i0 + len(seg))
        if j1 > j0:
            out[:, j0:j1] += seg[j0 - i0:j1 - i0][None]
        # the offset that makes the ENGINE's own mix land this hit at the same level as ours. The engine
        # builds its own snippet (no tail shaping, no attack layer) and levels its 20 ms full-band RMS to
        # music + 5 dB + gain, so the gain carries (our target) - 5, the two music references' difference
        # and the snippet's own (full-band 20 ms - band-limited 46 ms) difference. It clamps gain to +-6 and
        # its own make-up to +-12 dB, so a hit that quiet is reported in `engine_quiet`.
        el = engine_snippet(e['path'], e['t'] - ENGINE_LEAD, H - ENGINE_LEAD, nxt, cache)
        if el is None:
            off, need = (hit_db + g - mus_db) - ENGINE_SFX_DB, 0.0
        else:
            _, e_rms20, e_band = el
            off = (mus_db + tgt) - e_band + e_rms20 - mus_eng - ENGINE_SFX_DB
            need = mus_eng + ENGINE_SFX_DB + float(np.clip(off, -6, 6)) - e_rms20
        entry.update(gain=round(float(np.clip(off, -6, 6)), 2), gain_abs_db=round(g, 2), layers=layers,
                     tail_env='refs', engine_makeup_db=round(need, 1))
        entries.append(entry)
        rep.append({'H': round(H, 3), 'music_db': round(mus_db, 1), 'hit_db': round(hit_db + g, 1), 'gain': round(g, 1)})
    return out, entries, rep


def clip_layer_stats(layer, H, audible_idx, L):
    """The clip-sound layer of our mix measured like the references' residual (ref_gaps): share of time it is
    present (within 25 dB of its p95, 11.6 ms frames), audible hits per second, and the longest gap between
    audible hits (and between any clip sound)."""
    x = layer.mean(axis=0) if layer.ndim == 2 else layer
    hop = int(0.0116 * MSR)
    nf = max(1, len(x) // hop)
    r = np.sqrt(np.mean(x[:nf * hop].reshape(nf, hop) ** 2, axis=1) + 1e-12)
    d = 20 * np.log10(r)
    p95 = float(np.percentile(d, 95))
    present = d > p95 - 25
    runs, cur = [], 0
    for v in present:
        cur = 0 if v else cur + 1
        runs.append(cur)
    t = sorted(H[i] for i in audible_idx)
    gaps = np.diff([0.0] + t + [L]) if t else np.array([L])
    return {'present_pct': round(100 * float(present.mean()), 1), 'longest_silence_s': round(max(runs) * hop / MSR, 3),
            'audible_hits': len(t), 'hits': len(H), 'audible_per_s': round(len(t) / max(L, 1e-6), 2),
            'hit_gap_max_s': round(float(gaps.max()), 3), 'hit_gap_p90_s': round(float(np.percentile(gaps, 90)), 3)}


def headroom_for_hits(raw, music, lufs=-14.0, ceiling_db=-1.8, headroom_db=3.0):
    """A hot, dense master at -14 LUFS already peaks near the ceiling, so the final limiter would flatten
    every hit laid on it. Limit the song's own transient peaks 3 dB under the ceiling first (at the gain the
    master will apply; its level is unchanged), so the clip hits keep their attack."""
    g = 10 ** ((lufs - acapella.lufs(raw)) / 20)
    m = music * g
    lim = acapella.limit(m, 10 ** ((ceiling_db - headroom_db) / 20)) / g
    return raw - music + lim


def master_for_aac(x, path, lufs=-14.0, tp_max=-1.2):
    """-14 LUFS master whose true peak stays under tp_max AFTER AAC encoding (44.1 kHz, 256 kbps, as the
    renderer writes it; resampling to 48 kHz alone overshoots by ~1.5 dB on hot songs): encode a test AAC,
    measure, lower the limiter ceiling by the overshoot, at most to -3 dBTP so the hits keep their peaks."""
    ceil = -1.8
    tp = z = None
    for _ in range(4):
        y, g = acapella.master(x, lufs, ceil)
        write_wav(path, y)
        test = path + '.aac.m4a'
        subprocess.run(['ffmpeg', '-y', '-v', 'error', '-i', path, '-c:a', 'aac', '-b:a', '256k', '-ar', str(MSR), test],
                       capture_output=True)
        z = load_audio(test, MSR, 2)
        try:
            os.remove(test)
        except OSError:
            pass
        if z is None:
            break
        tp = float(db(acapella.true_peak(z.astype(np.float64))))
        if tp <= tp_max or ceil <= -3.0:
            break
        ceil = max(-3.0, ceil - (tp - tp_max + 0.2))
    # still over after AAC (a few very sharp hits ring in the codec): turn down only those spots, 10 ms
    # windows with a 5 ms smoothed gain curve, rather than the whole video (a global trim cost a 90 s long 3.6 dB)
    for _ in range(4):
        if tp is None or tp <= tp_max or z is None:
            break
        n = min(y.shape[1], z.shape[1])
        up = np.abs(signal.resample_poly(z[:, :n].astype(np.float64), 4, 1, axis=-1)).max(axis=0)
        W = int(0.01 * MSR)
        nb = -(-n // W)
        pk = np.pad(up[:n * 4], (0, nb * W * 4 - n * 4)).reshape(nb, W * 4).max(axis=1)
        need = np.minimum(1.0, 10 ** ((tp_max - 0.4) / 20) / np.maximum(pk, 1e-9))
        need = np.minimum(need, np.minimum(np.r_[need[1:], 1.0], np.r_[1.0, need[:-1]]))
        gcur = uniform_filter1d(np.repeat(need, W)[:n], int(0.005 * MSR))
        y = y.copy()
        y[:, :n] *= gcur
        write_wav(path, y)
        test = path + '.aac.m4a'
        subprocess.run(['ffmpeg', '-y', '-v', 'error', '-i', path, '-c:a', 'aac', '-b:a', '256k', '-ar', str(MSR), test],
                       capture_output=True)
        z = load_audio(test, MSR, 2)
        try:
            os.remove(test)
        except OSError:
            pass
        tp = float(db(acapella.true_peak(z.astype(np.float64)))) if z is not None else None
    return y, g, tp


def flux_balance(layer, music, H, max_db=6.0):
    """Per hit dB trims that even out the hits' onset strength (the study's detector envelope on the clip
    layer over the music at -15 dB, i.e. about what is left of the song under a hit): a hit at half the
    median strength gets up to +6 dB, a dominating one down to -6 dB. Returns (trims, strengths)."""
    import librosa
    y = signal.resample_poly((layer + 0.18 * music).mean(axis=0), 1, 2).astype(np.float32)
    env = librosa.onset.onset_strength(y=y, sr=SR, hop_length=HOP)
    t = np.arange(len(env)) * HOP / SR
    f = np.array([float(env[(t > h - 0.03) & (t < h + 0.05)].max()) if ((t > h - 0.03) & (t < h + 0.05)).any() else 0.0
                  for h in H])
    med = float(np.median(f[f > 0])) if (f > 0).any() else 1.0
    trims = [float(np.clip(6 * math.log2(med / max(x, 1e-3 * med)), -max_db, max_db)) for x in f]
    return trims, [round(x / (med + 1e-9), 2) for x in f]


def align_shifts(layer, H, search=0.045):
    """Per hit: where the study's onset detector (hop 128 on 22.05 kHz) finds the clip hit in the clip layer,
    relative to its planned time H. Returns (shifts that cancel the error, errors in ms; None = not found)."""
    y = signal.resample_poly(layer.mean(axis=0), 1, 2).astype(np.float32)
    t, st_, _ = onsets(y, SR, delta=0.2, wait_s=0.05)
    shifts, errs = [], []
    for h in H:
        m = np.where(np.abs(t - h) <= search)[0]
        if len(m):
            j = m[int(np.argmax(st_[m]))]
            d = float(t[j] - h)
            shifts.append(-d if abs(d) <= 0.025 else 0.0)     # further off: another transient, leave it
            errs.append(round(d * 1000, 1))
        else:
            shifts.append(0.0)
            errs.append(None)
    return shifts, errs


def song_window(path, s0, L, cache=None):
    """(2, n) of the song [s0, s0 + L] at MSR (zero-padded), 5 ms fade at both ends against clicks."""
    y = load_audio(path, MSR, 2, start=max(0.0, s0), dur=L + 0.1)
    n = int(round(L * MSR))
    out = np.zeros((2, n))
    if y is not None:
        off = int(round(max(0.0, -s0) * MSR))
        k = min(n - off, y.shape[1])
        out[:, off:off + k] = y[:, :k]
    f = int(0.005 * MSR)
    out[:, :f] *= np.linspace(0, 1, f)
    out[:, -f:] *= np.linspace(1, 0, f)
    return out


def separate_drums(win, threads=None):
    """(drums, rest) of a (2, n) window at MSR via Demucs (acapella.separate)."""
    threads = threads or max(1, (os.cpu_count() or 2))
    st = acapella.separate(win, threads)
    drums = st.get('drums')
    rest = sum(v for k, v in st.items() if k != 'drums')
    return drums, rest


# ---------------------------------------------------------------- plan

class TooFewEvents(RuntimeError):
    """Not enough usable clip sounds for a sound edit (.have = usable events)."""

    def __init__(self, msg, have=0):
        super().__init__(msg)
        self.have = have


# ---------------------------------------------------------------- long sections (render_vibe)

REF_LUFS = -7.3             # median integrated loudness of the five references (-5.8 .. -9.9 LUFS, measured 2026-10-12)


def reference_lufs(music_win):
    """The master level for a vibe edit: the references' loudness (their median, -7.3 LUFS), but never louder
    than the song section itself + 0.5 LU (a quiet song pushed to -7 would pump under the limiter) and never
    quieter than -9.5 (the quietest reference is -9.9)."""
    try:
        w = float(acapella.lufs(np.asarray(music_win, np.float64)))
    except Exception:  # noqa: BLE001
        w = REF_LUFS
    return float(np.clip(min(REF_LUFS, w + 0.5), -9.5, -6.5))


def bars_for_range(P, song_dur, length_range, target=None):
    """Whole bars for a section of length_range = (lo, hi) seconds: as close as possible to `target` (default
    30 % into the range), preferring 8- then 4-bar phrases; never longer than the song allows."""
    lo, hi = float(length_range[0]), float(length_range[1])
    bar = 4 * P
    target = float(target) if target else lo + 0.3 * (hi - lo)
    nmax = max(1, int((song_dur - 1.5) // bar))
    cand = [n for n in range(1, nmax + 1) if lo - 0.2 <= n * bar <= hi + 0.3]
    if not cand:
        fit = [n for n in range(1, nmax + 1) if n * bar <= hi + 0.3]
        return max(fit) if fit else 1
    return min(cand, key=lambda n: abs(n * bar - target) / bar + (0 if n % 8 == 0 else 0.4 if n % 4 == 0 else 0.9 if n % 2 == 0 else 1.4))


CAP_SCALE_S = float(os.environ.get('SOUND_CAP_SCALE_S', '60'))   # the per-source cap grows by 1 per 60 s (shorts: 1)


def src_cap(compilation):
    """Shots per source video in one edit. Owner 2026-10-12 (sample review: the same scene / look came back at 1 s and
    21 s): 1 per source (2 for a compilation of many shows); env SOUND_SRC_CAP / SOUND_SRC_CAP_COMP override."""
    return int(os.environ.get('SOUND_SRC_CAP_COMP', '2')) if compilation else int(os.environ.get('SOUND_SRC_CAP', '1'))


def avail_slots(events, cap_scale=1.0):
    """How many shots this pool can fill under the variety rules: one per source scene, <= the per-source cap."""
    from collections import defaultdict
    scenes = defaultdict(set)
    comp = {}
    for e in events:
        scenes[e['source_id']].add(e['scene'])
        comp[e['source_id']] = comp.get(e['source_id'], False) or bool(e.get('compilation'))
    return int(sum(min(len(v), int(math.ceil(src_cap(comp[k]) * cap_scale))) for k, v in scenes.items()))


def fit_hits_to_pool(hits, events, d, n_bars, P, length_range, cap_scale, audible_min=None):
    """Too few usable scenes for every song hit: first drop whole bars from the end (the section stays
    continuous and ends on a bar line) down to the range's minimum; then, when the crisp-hit scenes run short,
    turn the weakest off-pattern audible hits into 'song only' shots (picture on the beat, clip audio out:
    the references have 46 % of those) while >= `audible_min` of the shots stay audible; then thin the weakest
    off-pattern hits (never opening a gap over 0.95 s, never under 1.6 hits/s). Still too few ->
    TooFewEvents ('pool_low'). Events with 'silent_only' (a visible action whose sound is not crisp) fill only
    the song-only shots."""
    lo = float(length_range[0])
    if audible_min is None:
        # owner 2026-10-12: a clip sound on (nearly) every hit in the Short; the 16:9 long (1.5-3 min) cannot get
        # 3x the crisp moments from one pool, so it keeps >= 60 % audible (the rest play their clip audio as a bed)
        audible_min = float(os.environ.get('SOUND_AUDIBLE_MIN') or (0.9 if float(length_range[1]) < 80 else 0.6))
    bar = 4 * P
    L = n_bars * bar
    aud_ev = [e for e in events if not e.get('silent_only')]

    def slots(Lx):
        cs = max(1.0, Lx / 10.5)
        return avail_slots(events, cs), avail_slots(aud_ev, cs)
    s_all, s_aud = slots(L)
    info = {'slots': s_all, 'slots_audible': s_aud, 'hits_song': len(hits), 'len_song_s': round(L, 2)}
    margin = 0.92                       # the assignment needs a little choice (room before / after each hit)

    def n_aud():
        return sum(1 for h in hits if not h.get('silent'))

    def short():
        return len(hits) > margin * s_all or n_aud() > margin * s_aud
    while short() and (n_bars - 1) * bar >= lo - 0.2:
        n_bars -= 1
        L = n_bars * bar
        hits = [h for h in hits if h['t'] < d + L - 0.2]
        s_all, s_aud = slots(L)
    muted = 0

    def mute():
        nonlocal muted
        while n_aud() > margin * s_aud and n_aud() - 1 >= audible_min * len(hits):
            # spread the beds: never mute next to an already muted hit when another choice exists (the long had an
            # 18.8 s run of muted hits: weakest-first clustered them in a quiet section of the song)
            idx = {id(h): i for i, h in enumerate(hits)}

            def near(h):
                i = idx[id(h)]
                return sum(1 for k in (i - 1, i + 1) if 0 <= k < len(hits) and hits[k].get('silent'))
            cand = [h for h in hits if not h.get('silent')]
            h = min(cand, key=lambda h: (near(h), bool(h.get('pattern')), h['strength']))
            h['silent'] = True
            muted += 1
    mute()
    thinned = 0
    while short() and len(hits) - 1 >= RATE_MIN * L:
        mute()                          # thinning lowers the audible floor: mute again (was: muted only once)
        if not short():
            break
        best = None
        for i, h in enumerate(hits):
            prev = hits[i - 1]['t'] if i else d
            nxt = hits[i + 1]['t'] if i + 1 < len(hits) else d + L
            if nxt - prev > 0.95:
                continue
            key = (not h.get('silent') and n_aud() <= margin * s_aud, bool(h.get('pattern')), h['strength'])
            if best is None or key < best[0]:
                best = (key, i)
        if best is None:
            break
        hits.pop(best[1])
        thinned += 1
    info.update(slots_final=s_all, slots_audible_final=s_aud, hits=len(hits), audible=n_aud(), len_s=round(L, 2),
                bars=n_bars, thinned=thinned, muted=muted, shortened=bool(info['len_song_s'] - L > 0.01))
    if len(hits) > s_all or n_aud() > s_aud or L < lo - 0.5:
        raise TooFewEvents(f'pool_low: {s_all} usable scenes ({s_aud} with a crisp hit) for {len(hits)} hits '
                           f'({n_aud()} audible) over {L:.1f} s', s_all)
    return hits, n_bars, L, info


def plan_sound_edit(music, shots, music_start=None, duration=None, mode='accent', deliver='auto', theme=None,
                    title=None, watermark=None, heatmap=None, seed=None, workdir=None, fps=FPS_OUT,
                    loop_end=None, events=None, aspect='9:16', opts=None, length_range=None, lufs=None, rate=None,
                    hit_db=None):
    """Build the sound-edit plan (engine interface + our finished audio mix). See module doc.
    music: song path; shots: library shots (list of dicts or shots.json paths); music_start: hook start in
    the song (s) or None (picked from percussive clarity + heatmap); duration: target length (8.5-11.5).
    opts (planners.py): optional keys sound_mode / mode ('accent'|'build'), deliver, music_start / start,
    theme, loop_end, song_title."""
    t_start = time.time()
    o = opts or {}
    mode = o.get('sound_mode') or (o.get('mode') if o.get('mode') in ('accent', 'build') else None) or mode
    deliver = o.get('deliver') or deliver
    music_start = music_start if music_start is not None else o.get('music_start', o.get('start'))
    theme = theme or o.get('theme')
    title = title or o.get('song_title') or o.get('song')
    loop_end = o.get('loop_end', loop_end)
    if length_range:
        loop_end = False                       # the loop clip would show one scene twice in a long video
    if duration is not None and not length_range and not (LEN_MIN - 1.5 <= float(duration) <= LEN_MAX + 1.5):
        duration = None                        # a sound edit is a ~10 s loop whatever the caller's length
    fps = int(fps or FPS_OUT)
    seed = random.randrange(1 << 30) if seed is None else int(seed)
    rng = random.Random(seed)
    workdir = workdir or tempfile.mkdtemp(prefix='sound_edit_')
    os.makedirs(workdir, exist_ok=True)
    shots = load_shots(shots)
    theme = theme or song_theme(title)
    # ---- song
    song = Song(music, heatmap)
    if length_range:
        d, n_bars = song.hook(music_start, n=bars_for_range(song.P, song.dur, length_range, duration))
    else:
        d, n_bars = song.hook(music_start, duration)
    P, ph = song.fit_grid(d - 6, d + n_bars * 4 * song.P + 6, 60 / song.P)
    d = ph + round((d - ph) / P) * P          # the hook's downbeat on the local grid
    L = n_bars * 4 * P
    if length_range:
        while L > length_range[1] + 0.3 and n_bars > 1:
            n_bars -= 1
            L = n_bars * 4 * P
    elif L > LEN_MAX:
        n_bars -= 1
        L = n_bars * 4 * P
    log(f'song {os.path.basename(music)}: {60 / P:.1f} bpm, hook at {d:.2f} s, {n_bars} bars = {L:.2f} s')
    bed = None
    level_ref = None
    if mode == 'build':
        win0 = max(0.0, d - 1.0)
        win = song_window(music, win0, L + 2.0)
        drums, rest = separate_drums(win)
        dm = signal.resample_poly(drums.mean(axis=0), 1, 2).astype(np.float32)
        dh = drum_hits(dm)
        ht = np.array([t + win0 for t, _, _ in dh])
        hs = np.array([s for _, s, _ in dh])
        hc = {round(t + win0, 4): c for t, _, c in dh}
        hits = select_hits(song, d, n_bars, P, onset_t=ht, onset_s=hs)
        for h in hits:
            near = min(hc.items(), key=lambda kv: abs(kv[0] - h['t']))[1] if hc else 'snare'
            h['band'] = hc.get(round(h['t'], 4)) or near
            h['drum'] = True
        # sparse drums (an a-cappella intro, stomps only): the song's other onsets top the rate up
        L0 = n_bars * 4 * P
        if len(hits) < RATE_MIN * L0:
            m = (song.on_t >= d - 0.06) & (song.on_t < d + L0 - 0.2)
            extra = sorted(zip(song.on_t[m], song.on_s[m]), key=lambda x: -x[1])
            bands = dict(zip([round(t, 4) for t, _ in extra], hit_bands(song.flux, song.flux_t, [t for t, _ in extra])))
            for t, st_ in extra:
                if len(hits) >= RATE_TARGET * 0.85 * L0:
                    break
                if all(abs(t - h['t']) >= MIN_GAP for h in hits):
                    hits.append({'t': float(t), 'strength': float(st_), 'pos16': -1, 'bar': -1, 'pattern': False,
                                 'dev_ms': 0.0, 'band': bands[round(t, 4)], 'drum': False})
            hits.sort(key=lambda h: h['t'])
        bed = (win0, rest, drums)
    else:
        if length_range:
            hits = select_hits_span(song, d, n_bars, P, rate=rate or RATE_TARGET)
        else:
            hits = select_hits(song, d, n_bars, P)
        for h, b in zip(hits, hit_bands(song.flux, song.flux_t, [h['t'] for h in hits])):
            h['band'] = b
    if len(hits) < 4:
        raise RuntimeError(f'only {len(hits)} usable song hits in the hook')
    # ---- clip events
    if events is None:
        events = detect_events(shots, cache_dir=workdir)
    for e in events:
        e['_theme'] = theme_match(e, theme)
    cap_scale = 1.0
    fit = None
    if length_range:
        cap_scale = max(1.0, L / CAP_SCALE_S)
        hits, n_bars, L, fit = fit_hits_to_pool(hits, events, d, n_bars, P, length_range, cap_scale)
        cap_scale = max(1.0, L / CAP_SCALE_S)
    min_hits = int(math.ceil(RATE_MIN * L))
    if len(events) < min(len(hits), min_hits):
        # a sound edit needs >= 1.6 hits/s; fewer is a slideshow: the caller must source more clips
        raise TooFewEvents(f'only {len(events)} usable sound events for {min(len(hits), min_hits)} hits '
                           f'(need >= 1.6/s): source more clips (library --with-audio --need-sfx)', len(events))
    if len(events) < len(hits):
        # short of material: keep the strongest hits (pattern ones first), still >= 1.6/s
        keep = sorted(hits, key=lambda h: (not h['pattern'], -h['strength']))[:len(events)]
        hits = sorted(keep, key=lambda h: h['t'])
        log(f'only {len(events)} usable clip events: {len(hits)} hits')
    # edit time: t = 0 at s0 = first hit - lead (the lead is the first shot's pre-roll)
    lead0 = PREROLL_DEF
    s0 = hits[0]['t'] - lead0
    for h in hits:
        h['e'] = h['t'] - s0
    hits = [h for h in hits if h['e'] <= L - 0.3]
    evs = assign(hits, events, L, mode, theme, rng, cap_scale=cap_scale)
    # the first shot's own pre-roll sets the lead (50-220 ms): shift the window, then drop any hit the
    # shift pushed past the end (every shot still gets >= 0.25 s)
    lead = float(np.clip(min(evs[0]['preroll'], evs[0]['pre_room'] - scene_margin(evs[0])), PREROLL_MIN, LEAD_MAX))
    s0 = hits[0]['t'] - lead
    for h in hits:
        h['e'] = h['t'] - s0
    pairs = [(h, e) for h, e in zip(hits, evs) if h['e'] <= L - 0.25]
    hits, evs = [x[0] for x in pairs], [x[1] for x in pairs]
    loop_end = (rng.random() < 0.5) if loop_end is None else loop_end
    if loop_end and len(evs) >= 6 and evs[0]['post_room'] >= L - hits[-1]['e'] and evs[0]['pre_room'] >= PREROLL_MIN:
        evs[-1] = evs[0]                      # the last shot is the first clip again: the Short loops
    else:
        loop_end = False
    # the hits were chosen on the whole song's onsets; the delivered edit is judged against the onsets of
    # the rendered window alone, and the two detectors disagree by a few frames on weak onsets (see
    # window_onsets). Snap every hit to the window's own onset when there is one within ~35 ms.
    won_t, won_s = window_onsets(music, s0, L)
    snapped = 0
    for h in hits:
        if not len(won_t):
            break
        j = int(np.argmin(np.abs(won_t - h['e'])))
        if abs(won_t[j] - h['e']) <= 0.035:
            snapped += int(abs(won_t[j] - h['e']) > 1e-4)
            h['e'] = float(won_t[j])
            h['t'] = s0 + h['e']
    H = [h['e'] for h in hits]
    avoid = won_t if len(won_t) else (song.on_t[(song.on_t >= s0 - 0.1) & (song.on_t <= s0 + L + 0.1)] - s0)
    pre, cuts, holds = shot_timing(H, evs, L, fps, rng, avoid=avoid)
    # ---- dissolve: at most one per short (refs: 2 in 114 cuts, i.e. ~0.4 per edit), 2-3 frames@24,
    # between shots of similar tone, never into one of the strongest hits
    strong_cut = np.percentile([h['strength'] for h in hits], 70)
    eligible = []
    for i in range(1, len(hits)):
        a, b = evs[i - 1], evs[i]
        similar = abs(a['luma'] - b['luma']) < 0.08 and np.linalg.norm(np.array(a['col']) - np.array(b['col'])) < 30
        d_len = rng.choice([2, 3]) / 24
        if (similar and hits[i]['strength'] < strong_cut and pre[i] >= d_len + 0.03 and a is not b and
                cuts[i] + d_len - H[i - 1] <= a['post_room'] - scene_margin(a) and holds[i - 1] == 0):
            eligible.append((i, d_len))
    dissolve = {rng.choice(eligible)} if eligible and len(hits) >= 17 and rng.random() < 0.4 else set()
    dmap = dict(dissolve)
    # ---- audio
    if bed is not None:
        win0, rest, drums = bed
        a = int(round((s0 - win0) * MSR))
        n = int(round(L * MSR))
        music_win = rest[:, a:a + n] + drums[:, a:a + n] * 10 ** (-12 / 20)
        level_ref = None                    # the hits sit over the drumless bed: level them against it
        f = int(0.005 * MSR)
        music_win[:, :f] *= np.linspace(0, 1, f)
        music_win[:, -f:] *= np.linspace(1, 0, f)
        bed_path = os.path.join(workdir, 'bed.wav')
        write_wav(bed_path, np.clip(music_win, -1, 1))
    else:
        music_win = song_window(music, s0, L)
        bed_path = None
    shots_ev = [(H[i], evs[i], H[i + 1] if i + 1 < len(H) else None) for i in range(len(H))]
    mute = {i for i, h in enumerate(hits) if h.get('silent')}
    if SOUND_VISIBLE:
        # sound_4 review: a hit that sounds on (or 1-2 frames after) its own cut shows no cause; the reference's
        # shortest pre-roll is 0.9 f@24 only on its FX-flash miss, its visible hits have >= 1.4 f (58 ms)
        late = {i for i in range(1, len(H)) if pre[i] < VIS_PRE_MIN and i not in mute}
        if late:
            log(f'visible source: {len(late)} hits muted (pre-roll under {VIS_PRE_MIN * 1000:.0f} ms)')
        mute |= late
    # two closed loops on the finished mix, run to convergence: (1) each hit is moved so the study's own
    # onset detector finds it exactly on the song's onset, (2) the hits' onset strengths are trimmed toward
    # their median so none dominates or disappears. One pass leaves hits the first mix's levels hid.
    shifts = trims = None
    errs = flux0 = None
    for it in range(3):
        raw, entries, levels = mix(music_win, shots_ev, L, target_db=hit_db or HIT_OVER_MUSIC_DB, level_ref=level_ref, rng=random.Random(seed + 7), mute=mute,
                                   shifts=shifts, trims=trims)
        layer = raw - music_win[:, :raw.shape[1]]
        d_sh, errs_i = align_shifts(layer, [h + s for h, s in zip(H, shifts or [0.0] * len(H))])
        d_tr, flux_i = flux_balance(layer, music_win[:, :raw.shape[1]], [h + s for h, s in zip(H, shifts or [0.0] * len(H))])
        if it == 0:
            errs, flux0 = errs_i, flux_i
        if it == 2:
            errs2, flux1 = errs_i, flux_i
            break
        shifts = [float(np.clip(a + b, -0.045, 0.045)) for a, b in zip(shifts or [0.0] * len(H), d_sh)]
        # +-4 dB, not +-6: the references' hit peak over the music has an IQR of only 4 dB (p25 +3, p75 +7),
        # and a wider evening-out of the onset strengths spreads the peaks past it
        trims = [float(np.clip(a + b, -5.0, 5.0)) for a, b in zip(trims or [0.0] * len(H), d_tr)]
    # the same onset loop for the engine's own mixer, on the sfx entries' `at` (which only it reads)
    if length_range:
        eng_errs0, eng_errs = [], []           # render_vibe always plays audio_mix; the engine never mixes it
    else:
        eng_errs0, eng_errs = engine_align(shots_ev, entries, L, {},
                                           music_db=float(db(np.sqrt(np.mean(music_win ** 2) + 1e-12))))
    n_quiet = sum(1 for x in entries if x and abs(x.get('engine_makeup_db') or 0) > 12)
    if n_quiet > 0.25 * max(1, len(entries)):
        # the engine's own sfx mixer clamps its make-up at +-12 dB, so hits this far under the song never
        # arrive when IT mixes the plan. Our finished soundtrack (audio_mix, what render() plays) has no
        # such limit, and the pipeline should use it for this edit.
        log(f'{n_quiet}/{len(entries)} hits are too quiet for the engine\'s own mixer (+-12 dB): '
            f'render this plan with sound_edit.render / plan["audio_mix"], not the engine\'s soundtrack')
    layer_stats = clip_layer_stats(raw - music_win[:, :raw.shape[1]], [h + s_ for h, s_ in zip(H, shifts or [0.0] * len(H))],
                                   [i for i in range(len(H)) if i not in mute], L)
    log('clip layer: ' + json.dumps(layer_stats))
    mix_path = os.path.join(workdir, 'sound_edit_mix.wav')
    if lufs is None and length_range:
        lufs = reference_lufs(music_win)
    lufs = -14.0 if lufs is None else float(lufs)
    raw = headroom_for_hits(raw, music_win[:, :raw.shape[1]], lufs=lufs)
    mastered, g_master, tp_aac = master_for_aac(raw, mix_path, lufs=lufs)
    # ---- plan
    beats = song.beats(s0 - 0.01, s0 + L, P, ph) - s0
    beats = beats[(beats >= 0) & (beats < L)]
    downs = song.downbeats(s0 - 4 * P, s0 + L, P, ph) - s0
    downs = downs[(downs >= -0.01) & (downs < L)]
    wr, wt = librosa_rms(song_window(music, s0, L).mean(axis=0)[::2].astype(np.float32))
    energy = []
    for bt in beats:
        sel = (wr >= bt) & (wr < bt + P)
        energy.append(float(np.mean(10 ** (wt[sel] / 20))) if sel.any() else 0.0)
    emax = max(energy) if energy else 1.0
    energy = [round(v / (emax + 1e-9), 3) for v in energy]
    deliver = pick_deliver(deliver, rng)
    plan_shots = []
    for i, (h, e) in enumerate(zip(hits, evs)):
        t0 = cuts[i]
        t1 = cuts[i + 1] if i + 1 < len(cuts) else frame_round(L, fps)
        src_in = e['t'] - (H[i] - t0)
        plan_shots.append({
            'path': e['path'], 't0': round(t0, 5), 't1': round(t1, 5), 'src_in': round(src_in, 4),
            'speed': 'normal', 'speed_curve': None, 'camera': None,
            'transition_in': 'dissolve' if i in dmap else 'cut',
            'transition_frames': int(round(dmap[i] * fps)) if i in dmap else 0,
            'layout': 'fill' if e['aspect'] >= 1.2 else 'letterbox',
            'hold': holds[i], 'sfx': [entries[i]] if i < len(entries) and entries[i] else [],
            'hit': {'at': round(H[i], 4), 'clip_t': e['t'], 'cls': e['cls'], 'song_band': h['band'],
                    'song_strength': round(h['strength'], 3), 'preroll': round(H[i] - t0, 4), 'pattern': h['pattern'],
                    'grid': bool(h.get('grid')), 'silent': bool(h.get('silent')),
                    'vis_dt': e['vis_dt'], 'event_score': e['score'], 'harm': e.get('harm'),
                    'voiced': bool(e.get('voiced') or e['cls'] == 'voice' or float(e.get('harm') or 0) >= VOICED_HARM)},
            'source_id': e['source_id'], 'source_title': e['source_title'], 'content_box': e.get('content_box'),
            'scene_t': e['scene_t'], 'src_fps': e['fps'],
            'focus_x': e.get('focus_x'), 'visible': e.get('visible'), 'vis_src': e.get('vis_src'),
        })
    report = {
        'mode': mode, 'bpm': round(60 / P, 2), 'hook_downbeat': round(d, 3), 'n_bars': n_bars, 'song_window': [round(s0, 4), round(s0 + L, 4)],
        'hits': len(hits), 'hits_per_s': round(len(hits) / L, 2), 'pattern_share': round(float(np.mean([h['pattern'] for h in hits])), 2),
        'preroll_ms_median': round(float(np.median([H[i] - cuts[i] for i in range(1, len(H))])) * 1000),
        'shot_s_median': round(float(np.median(np.diff(cuts + [L]))), 3),
        'classes': {c: sum(1 for e in evs if e['cls'] == c) for c in CLASSES},
        'distinct_sources': len({e['source_id'] for e in evs}), 'distinct_scenes': len({e['scene'] for e in evs}),
        'holds': sum(1 for x in holds if x > 0), 'hold_s_max': round(max(holds), 3), 'dissolves': len(dmap),
        'grid_hits': sum(1 for h in hits if h.get('grid')), 'onsets_resnapped': snapped,
        'variety': variety_report(evs),
        'loop_end': bool(loop_end), 'theme': theme, 'events_available': len(events),
        'master_gain_db': round(float(db(g_master)), 2), 'aac_true_peak_dbtp': round(tp_aac, 2) if tp_aac is not None else None,
        # hits the engine's own mixer cannot lift to level (it clamps its make-up gain to +-12 dB): they come
        # out quiet if the engine renders the plan instead of using audio_mix
        'engine_quiet': n_quiet, 'engine_audio_ok': bool(n_quiet <= 0.25 * max(1, len(hits))),
        'levels': levels, 'seed': seed,
        'clip_layer': layer_stats, 'onset_err_ms_before': errs, 'onset_err_ms_after': errs2, 'flux_rel_before': flux0, 'flux_rel_after': flux1,
        'engine_onset_err_ms_before': eng_errs0, 'engine_onset_err_ms': eng_errs,
        'plan_s': round(time.time() - t_start, 1),
        'length_range': list(length_range) if length_range else None, 'lufs_target': round(lufs, 2),
        'pool_fit': fit, 'cap_scale': round(cap_scale, 2),
    }
    plan = {
        'aspect': '9:16' if deliver != 'none' else '16:9', 'fps': fps,
        'music': os.path.abspath(music), 'music_start': round(s0, 4), 'duration': round(frame_round(L, fps), 5),
        'clip_audio': 'mix', 'clip_audio_gate': 'sfx_only', 'style': 'sound_edit',
        'title': None, 'subtitle': None, 'watermark': watermark,
        'beats': [round(float(b), 4) for b in beats], 'downbeats': [round(float(b), 4) for b in downs],
        'energy': energy, 'shots': plan_shots, 'overlays': [],
        'audio_mix': os.path.abspath(mix_path), 'audio_bed': os.path.abspath(bed_path) if bed_path else None,
        'deliver': {'master': '16:9', 'mode': deliver},
        'frame': {'rot90_cw': 'rot90', 'letterbox_9x16': 'letterbox_black'}.get(deliver, 'full'),
        'lead': 'none', 'interp': 'native', 'look': 'neutral',
        # the engine re-finds each hit's attack by itself (audio.snap_attacks), which is right for planners
        # that pass onset-envelope peaks but moves ours: every clip_t / at here is already the study's own
        # onset time, closed-loop aligned on the finished mix, so it must be taken as given.
        'style_overrides': {'audio': {'snap_attacks': False}},
        'sound_edit': report,
    }
    if aspect == '16:9':
        plan['aspect'], plan['deliver']['mode'], plan['frame'] = '16:9', 'none', 'full'
    return plan


def variety_report(evs):
    """What the variety rules could not satisfy with this pool (0 everywhere is what we want)."""
    from collections import Counter
    src = Counter(e['source_id'] for e in evs)
    cap = {e['source_id']: (4 if e['compilation'] else 2) for e in evs}
    return {'same_source_in_a_row': sum(1 for a, b in zip(evs, evs[1:]) if a['source_id'] == b['source_id']),
            'scene_reused': len(evs) - len({e['scene'] for e in evs}),
            'over_source_cap': sum(max(0, n - cap[k]) for k, n in src.items()),
            'sources': len(src), 'max_per_source': max(src.values()) if src else 0}


def pick_deliver(deliver, rng):
    if deliver in ('rot90', 'rot90_cw'):
        return 'rot90_cw'
    if deliver in ('letterbox', 'letterbox_9x16'):
        return 'letterbox_9x16'
    if deliver in ('none', 'master', '16:9'):
        return 'none'
    spec = (load_spec().get('style') or {}).get('formats', {}).get('short_layouts') or []
    p = next((x['p'] for x in spec if x.get('name') == 'rot90_cw_full'), 0.6)
    return 'rot90_cw' if rng.random() < p else 'letterbox_9x16'


# ---------------------------------------------------------------- render (ffmpeg)

def _font(size):
    from PIL import ImageFont
    for p in ('/usr/share/fonts/truetype/dejavu/DejaVuSans-BoldOblique.ttf', 'C:/Windows/Fonts/arialbi.ttf',
              os.path.join(ROOT, 'core', 'quiz', 'assets', 'fonts', 'LilitaOne-Regular.ttf')):
        if os.path.exists(p):
            return ImageFont.truetype(p, size), ('Oblique' in p or p.endswith('bi.ttf'))
    return ImageFont.load_default(), False


def watermark_png(text, path, size=MASTER):
    """Faint italic channel tag on the 16:9 master (Latin text only): cap height 2.5 % of the height,
    white at 30 %, centred at (0.45, 0.07)."""
    from PIL import Image, ImageDraw
    text = re.sub(r'[^\x20-\x7E]', '', text or '').strip()
    W, Hh = size
    img = Image.new('RGBA', (W, Hh), (0, 0, 0, 0))
    if not text:
        img.save(path)
        return path
    font, italic = _font(int(0.025 * Hh * 1.45))
    tmp = Image.new('RGBA', (W, 200), (0, 0, 0, 0))
    d = ImageDraw.Draw(tmp)
    bb = d.textbbox((0, 0), text, font=font)
    d.text((40 - bb[0], 60 - bb[1]), text, font=font, fill=(255, 255, 255, 77))
    if not italic:
        tmp = tmp.transform(tmp.size, Image.AFFINE, (1, 0.22, -20, 0, 1, 0), resample=Image.BICUBIC)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    crop = tmp.crop((20, 40, 60 + tw + 40, 80 + th))
    img.alpha_composite(crop, (int(0.45 * W - crop.width / 2), int(0.07 * Hh - crop.height / 2)))
    img.save(path)
    return path


def _text_band(path):
    try:
        import edit_fx
        t, b = edit_fx.text_bands(path)
        return (t if 0 < t <= 0.3 else 0.0), (b if 0 < b <= 0.3 else 0.0)
    except Exception:
        return 0.0, 0.0


def clip_colour(path, t0, t1):
    """(black point p2 of the luma 0-255, median saturation 0-255) of a shot's frames (3 samples)."""
    try:
        import cv2
    except ImportError:
        return None
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        return None
    bp, sat = [], []
    for q in (0.2, 0.5, 0.8):
        cap.set(cv2.CAP_PROP_POS_MSEC, 1000.0 * (t0 + q * max(0.0, t1 - t0)))
        ok, f = cap.read()
        if not ok:
            continue
        f = cv2.resize(f, (320, max(2, int(320 * f.shape[0] / f.shape[1]))))
        y = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
        hsv = cv2.cvtColor(f, cv2.COLOR_BGR2HSV)
        rows = y.mean(axis=1) > 6                  # leave black bars out
        if rows.sum() > 8:
            y, hsv = y[rows], hsv[rows]
        bp.append(float(np.percentile(y, 2)))
        sat.append(float(np.median(hsv[..., 1])))
    cap.release()
    if not bp:
        return None
    return float(np.median(bp)), float(np.median(sat))


BP_FIX_FROM, SAT_FIX_FROM = 22.0, 70.0


def colour_fix_filter(stats):
    """The sound preset has no grade; this only repairs a milky black point (p2 luma > 22/255: lifted to ~8)
    and low saturation (median < 70/255: up to x1.25) - a clip that is already rich is left alone.
    Returns (ffmpeg filter prefix, info or None)."""
    if not stats:
        return '', None
    bp, sat = stats
    f, info = '', {'black_point': round(bp, 1), 'sat': round(sat, 1)}
    if bp > BP_FIX_FROM:
        lift = float(np.clip((bp - 8.0) / 255.0, 0.0, 0.12))
        f += f'colorlevels=rimin={lift:.4f}:gimin={lift:.4f}:bimin={lift:.4f},'
        info['lift'] = round(lift, 4)
    if sat < SAT_FIX_FROM:
        g = float(np.clip(1.0 + (SAT_FIX_FROM - sat) / 140.0, 1.0, 1.25))
        f += f'eq=saturation={g:.3f},'
        info['sat_gain'] = round(g, 3)
    return f, (info if len(info) > 2 else None)


def _shot_graph(shots, fps, sharpen, colour_fix, bands, in_off=0, dec_threads=None):
    """ffmpeg inputs + filter chains for a run of plan shots: native frames (fps=60 repeats source frames,
    never blends), crop to the content, 16:9 master, optional colour repair; hard cuts by concat, short
    dissolves by xfade. Returns (inputs, parts, out_label, colour_report)."""
    W, Hh = MASTER
    inputs, parts, rep = [], [], []
    for i, s in enumerate(shots):
        nxt = shots[i + 1] if i + 1 < len(shots) else None
        ext = (nxt['transition_frames'] / fps) if nxt and nxt.get('transition_in') == 'dissolve' else 0.0
        dur = s['t1'] - s['t0'] + ext
        nfr = int(round(dur * fps))
        seek = max(0.0, math.floor((s['src_in'] - 1.0) * fps) / fps)
        start = round((s['src_in'] - seek) * fps) / fps      # on the 60 fps grid of the seek timeline
        hold = float(s.get('hold') or 0)
        # frames the clip really has in this scene; the rest (hold) repeats the last one
        scene_end = (s.get('scene_t') or [0, 1e9])[1]
        margin = 1.0 / max(12.0, float(s.get('src_fps') or 24.0))
        avail = int(math.floor((scene_end - margin - s['src_in']) * fps + 1e-6)) if hold > 0 else nfr
        avail = max(1, min(nfr, avail))
        if s['path'] not in bands:
            bands[s['path']] = _text_band(s['path'])
        top, bot = bands[s['path']]
        x0, y0, x1, y1 = s.get('content_box') or [0, 0, 1, 1]
        y0, y1 = max(y0, top), min(y1, 1 - bot)
        crop = ''
        if (x1 - x0) * (y1 - y0) < 0.995:
            crop = f'crop=iw*{x1 - x0:.4f}:ih*{y1 - y0:.4f}:iw*{x0:.4f}:ih*{y0:.4f},'
        if s.get('layout') == 'letterbox':
            geo = (f'scale={W}:{Hh}:force_original_aspect_ratio=decrease:flags=lanczos,'
                   f'pad={W}:{Hh}:(ow-iw)/2:(oh-ih)/2:black')
        else:
            geo = f'scale={W}:{Hh}:force_original_aspect_ratio=increase:flags=lanczos,crop={W}:{Hh}'
        sh = f',cas={sharpen}' if sharpen else ''
        col = ''
        if colour_fix:
            col, info = colour_fix_filter(clip_colour(s['path'], s['src_in'], s['src_in'] + dur))
            rep.append(dict(info, shot=i, path=os.path.basename(s['path'])) if info else None)
        dt = ['-threads', str(int(dec_threads))] if dec_threads else []
        inputs += dt + ['-ss', f'{seek:.4f}', '-t', f'{start + dur + 0.5:.4f}', '-i', s['path']]
        # fps=60 repeats the source frames (3:2 for 24 fps drawings), never blends; the clone pad
        # guarantees the exact frame count even when the file ends early (and freezes a planned hold)
        a0, a1 = max(0.0, start - 0.5 / fps), start + (avail - 0.5) / fps     # half-frame margins: exact frames
        parts.append(f'[{in_off + i}:v]fps={fps},trim=start={a0:.6f}:end={a1:.6f},setpts=PTS-STARTPTS,'
                     f'{crop}{col}{geo}{sh},setsar=1,format=yuv420p,tpad=stop_mode=clone:stop={nfr},'
                     f'trim=end_frame={nfr},setpts=PTS-STARTPTS,settb=AVTB[s{i}]')
    # join: dissolve pairs with xfade, everything else with concat
    groups, cur = [], 's0'
    cur_len = shots[0]['t1'] - shots[0]['t0'] + ((shots[1]['transition_frames'] / fps)
                                                 if len(shots) > 1 and shots[1].get('transition_in') == 'dissolve' else 0)
    k = 0
    for i in range(1, len(shots)):
        s = shots[i]
        nxt = shots[i + 1] if i + 1 < len(shots) else None
        ext = (nxt['transition_frames'] / fps) if nxt and nxt.get('transition_in') == 'dissolve' else 0.0
        if s.get('transition_in') == 'dissolve':
            dlen = s['transition_frames'] / fps
            off = cur_len - dlen
            parts.append(f'[{cur}][s{i}]xfade=transition=fade:duration={dlen:.5f}:offset={off:.5f}[x{k}]')
            cur, cur_len = f'x{k}', cur_len - dlen + (s['t1'] - s['t0'] + ext)
            k += 1
        else:
            groups.append(cur)
            cur = f's{i}'
            cur_len = s['t1'] - s['t0'] + ext
    groups.append(cur)
    parts.append(''.join(f'[{g}]' for g in groups) + f'concat=n={len(groups)}:v=1:a=0[cat]')
    return inputs, parts, 'cat', rep


def _deliver_filter(plan, last):
    mode = (plan.get('deliver') or {}).get('mode', 'none')
    if mode == 'rot90_cw':
        return f'[{last}]transpose=1,format=yuv420p[vout]'
    if mode == 'letterbox_9x16':
        return f'[{last}]scale=1152:648:flags=lanczos,crop=1080:648,pad=1080:1920:0:636:black,format=yuv420p[vout]'
    return f'[{last}]format=yuv420p[vout]'


GROUP_SHOTS = 16            # shots per intermediate in the grouped renderer (bounded decoders / memory)


def render(plan, out, workdir=None, crf=17, preset='medium', sharpen=0.25, threads=None, colour_fix=False,
           group_shots=GROUP_SHOTS):
    """Render a sound-edit plan with ffmpeg only (CPU libx264; no CUDA / NVENC needed): native frames (fps=60
    duplicates, never interpolates), hard cuts / short cross-dissolves, 16:9 master, then the delivery layout;
    soundtrack = plan audio_mix (else the untouched music window). A plan of more than `group_shots` shots
    (the 30-50 s Shorts and the long) renders its shots in groups to lossless intermediates (x264 qp 0, split
    only at hard cuts, so no transition is ever joined) and then encodes the whole video ONCE from them, so
    the number of open decoders stays small. colour_fix: the mild per-clip black point / saturation repair.
    threads: ffmpeg -threads / -filter_threads (None = ffmpeg's auto). Returns the output path;
    plan['render_report'] gets the colour repairs and timings."""
    workdir = workdir or tempfile.mkdtemp(prefix='sound_render_')
    os.makedirs(workdir, exist_ok=True)
    fps = plan.get('fps', FPS_OUT)
    shots = plan['shots']
    L = plan['duration']
    bands = {}
    th = ['-threads', str(int(threads))] if threads else []
    fth = ['-filter_threads', str(int(threads))] if threads else []
    t_start = time.time()
    grp = [shots]
    if len(shots) <= group_shots:
        inputs, parts, last, colour = _shot_graph(shots, fps, sharpen, colour_fix, bands)
        ins = len(shots)
    else:
        colour = []
        grp, cur = [], []
        for i, s in enumerate(shots):
            nxt = shots[i + 1] if i + 1 < len(shots) else None
            cur.append(s)
            if len(cur) >= group_shots and (nxt is None or nxt.get('transition_in') != 'dissolve'):
                grp.append(cur)
                cur = []
        if cur:
            grp.append(cur)
        lst = os.path.join(workdir, 'groups.txt')
        with open(lst, 'w', encoding='utf-8') as fl:
            for k, g in enumerate(grp):
                gi, gp, gl, gc = _shot_graph(g, fps, sharpen, colour_fix, bands, dec_threads=2)
                colour += gc
                nfr = sum(int(round((s['t1'] - s['t0']) * fps)) for s in g)
                gpath = os.path.join(workdir, f'g{k:03d}.txt')
                with open(gpath, 'w', encoding='utf-8') as f:
                    f.write(';'.join(gp + [f'[{gl}]format=yuv420p[gout]']))
                gout = os.path.join(workdir, f'g{k:03d}.mkv')
                cmd = (['ffmpeg', '-y', '-v', 'error', '-nostdin'] + fth + gi +
                       ['-filter_complex_script', gpath, '-map', '[gout]', '-frames:v', str(nfr)] + th +
                       ['-r', str(fps), '-c:v', 'libx264', '-preset', 'ultrafast', '-qp', '0', '-pix_fmt', 'yuv420p', gout])
                r = subprocess.run(cmd, capture_output=True, text=True)
                if r.returncode != 0 or not os.path.exists(gout):
                    raise RuntimeError(f'ffmpeg group {k} failed: ' + (r.stderr or '')[-1500:])
                fl.write("file '" + os.path.abspath(gout).replace(os.sep, '/') + "'\n")
        inputs = ['-f', 'concat', '-safe', '0', '-i', lst]
        parts, last, ins = [], '0:v', 1
    if plan.get('watermark'):
        wm = watermark_png(plan['watermark'], os.path.join(workdir, 'wm.png'))
        inputs += ['-loop', '1', '-i', wm]
        parts.append(f'[{ins}:v]format=rgba[wm];[{last}][wm]overlay=0:0:shortest=1[wmo]')
        ins += 1
        last = 'wmo'
    parts.append(_deliver_filter(plan, last))
    audio = plan.get('audio_mix')
    if audio and os.path.exists(audio):
        inputs += ['-i', audio]
    else:
        inputs += ['-ss', f'{plan["music_start"]:.4f}', '-t', f'{L:.4f}', '-i', plan['music']]
    amap = f'{ins}:a'
    gpath = os.path.join(workdir, 'graph.txt')
    with open(gpath, 'w', encoding='utf-8') as f:
        f.write(';'.join(parts))
    t_final = time.time()
    cmd = (['ffmpeg', '-y', '-v', 'error', '-nostdin'] + fth + inputs +
           ['-filter_complex_script', gpath, '-map', '[vout]', '-map', amap, '-t', f'{L:.4f}'] + th +
           ['-r', str(fps), '-c:v', 'libx264', '-preset', preset, '-crf', str(crf), '-pix_fmt', 'yuv420p',
            '-c:a', 'aac', '-b:a', '256k', '-ar', str(MSR), '-movflags', '+faststart', out])
    r = _gpu.run(cmd, 'final', capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(out):
        raise RuntimeError('ffmpeg render failed: ' + (r.stderr or '')[-2000:])
    fix_true_peak(out)
    plan['render_report'] = {'colour_fix': [c for c in colour if c], 'colour_fixed_shots': sum(1 for c in colour if c),
                             'groups': len(grp), 'render_s': round(time.time() - t_start, 1),
                             'final_encode_s': round(time.time() - t_final, 1), 'threads': threads or 'auto'}
    if len(grp) > 1:
        for k in range(len(grp)):
            try:
                os.remove(os.path.join(workdir, f'g{k:03d}.mkv'))
            except OSError:
                pass
    return out


def fix_true_peak(path, ceiling=-1.2):
    """AAC can push a limited master over its ceiling: measure the encoded audio, re-encode it quieter
    (video stream copied) when its true peak is above `ceiling` dBTP."""
    y = load_audio(path, MSR, 2)
    if y is None:
        return None
    tp = float(db(acapella.true_peak(y.astype(np.float64))))
    if tp <= ceiling:
        return tp
    tmp = path + '.tp.mp4'
    g = ceiling - 0.3 - tp
    r = subprocess.run(['ffmpeg', '-y', '-v', 'error', '-i', path, '-map', '0:v:0', '-map', '0:a:0', '-c:v', 'copy',
                        '-af', f'volume={g:.2f}dB', '-c:a', 'aac', '-b:a', '256k', '-ar', str(MSR), '-movflags', '+faststart',
                        tmp], capture_output=True)
    if r.returncode == 0 and os.path.exists(tmp):
        os.replace(tmp, path)
    return tp + g


# ---------------------------------------------------------------- check (quality loop)

def _import_timing():
    sys.path.insert(0, os.path.join(HERE, 'refs'))
    import sound_timing  # noqa: E402
    return sound_timing


def _render_cuts(video, fps=FPS_OUT, rotated=False):
    """Detected cut times of a rendered video (frame difference at 64 px; picture area only)."""
    pr = probe(video)
    w = 64
    h = max(2, int(round(w * pr['h'] / max(1, pr['w']) / 2) * 2))
    r = subprocess.run(['ffmpeg', '-v', 'error', '-i', video, '-an', '-vf', f'scale={w}:{h},format=gray', '-f', 'rawvideo', '-'],
                       capture_output=True)
    a = np.frombuffer(r.stdout, np.uint8)
    n = a.size // (w * h)
    g = a[:n * w * h].reshape(n, h, w).astype(np.float32)
    ys = g.reshape(n, h, w).mean(axis=(0, 2))
    rows = ys > 3                           # skip black letterbox bars
    g = g[:, rows] if rows.sum() > 4 else g
    d = np.zeros(n)
    d[1:] = np.abs(np.diff(g, axis=0)).mean(axis=(1, 2))
    return d, pr['fps']


def check(video, plan, sheet_dir=None):
    """Measure a render against the study's quality targets (sound.json quality_targets):
    clip hits vs song onsets (song-subtracted clip layer), pre-roll, cut timing, levels, loudness."""
    import librosa
    stt = _import_timing()
    L = plan['duration']
    ry = load_audio(video, stt.SR, 1)
    if plan.get('audio_bed'):
        sy = load_audio(plan['audio_bed'], stt.SR, 1)
    else:
        sy = load_audio(plan['music'], stt.SR, 1, start=plan['music_start'], dur=L)
    n = min(len(ry), len(sy))
    ry, sy = ry[:n], sy[:n]
    res_y, song_y, gains, removed_db = stt.split_song(ry, sy)
    son_t, son_s, _ = stt.onsets_with_strength(sy)
    ev_t, ev_s, _ = stt.onsets_with_strength(res_y, delta=0.2, wait_s=0.05)
    renv = librosa.onset.onset_strength(y=res_y, sr=stt.SR, hop_length=stt.HOP)
    renv = (renv - renv.min()) / (renv.max() - renv.min() + 1e-9)
    ret = np.arange(len(renv)) * stt.HOP / stt.SR
    rms_res = librosa.feature.rms(y=res_y, frame_length=1024, hop_length=stt.HOP)[0]
    rms_song = librosa.feature.rms(y=song_y, frame_length=1024, hop_length=stt.HOP)[0]

    def fi(t):
        return int(np.clip(round(t * stt.SR / stt.HOP), 0, len(rms_res) - 1))
    shots = plan['shots']
    cuts = [s['t0'] for s in shots[1:]]
    rows = []
    for i, s in enumerate(shots):
        t0, t1 = s['t0'], s['t1']
        row = {'i': i, 't0': t0, 'dur': round(t1 - t0, 3), 'planned_hit': s['hit']['at']}
        near_hit = (ret > s['hit']['at'] - 0.03) & (ret < s['hit']['at'] + 0.05)
        row['hit_flux'] = round(float(renv[near_hit].max()), 3) if near_hit.any() else None
        if i:
            o, _ = stt.near(t0, son_t)
            row['cut_to_song_onset_ms'] = round(o * 1000) if o is not None else None
        w0, w1 = t0 - 2 / 24, min(t1, t0 + 0.45)
        sel = np.where((ev_t >= w0) & (ev_t < w1))[0]
        if len(sel):
            j = sel[int(np.argmax(ev_s[sel]))]
            te = float(ev_t[j])
            row['sfx_t'] = round(te, 3)
            row['preroll_ms'] = round((te - t0) * 1000)
            row['to_planned_ms'] = round((te - s['hit']['at']) * 1000)
            o, _ = stt.near(te, son_t)
            row['sfx_to_song_onset_ms'] = round(o * 1000) if o is not None else None
            i0 = fi(te)
            pk = rms_res[i0:i0 + int(0.12 * stt.SR / stt.HOP)].max()
            mu = rms_song[max(0, i0 - int(0.3 * stt.SR / stt.HOP)):i0 + int(0.3 * stt.SR / stt.HOP)].mean()
            row['peak_db_vs_music'] = round(float(db(pk) - db(mu)), 1) if mu > 0 else None
        rows.append(row)

    def col(k):
        return np.array([r[k] for r in rows if r.get(k) is not None], float)
    # cuts as rendered (frame diff peaks near the planned cuts)
    d, vfps = _render_cuts(video)
    found = []
    for c in cuts:
        k = int(round(c * vfps))
        lo, hi = max(1, k - 2), min(len(d), k + 3)
        if hi > lo:
            kk = lo + int(np.argmax(d[lo:hi]))
            found.append(kk - k)
    # loudness
    st = load_audio(video, MSR, 2)
    lufs = acapella.lufs(st.astype(np.float64)) if st is not None else None
    tp = float(db(acapella.true_peak(st.astype(np.float64)))) if st is not None else None
    # music gain change after cuts (ducking)
    gm = np.sqrt((gains[:4] ** 2).mean(axis=0))
    gdb = db(gm) - db(np.median(gm))
    duck = [float(np.mean(gdb[fi(c):fi(c) + 20]) - np.mean(gdb[max(0, fi(c) - 40):max(1, fi(c) - 5)])) for c in cuts]
    pr = probe(video)
    sto = col('sfx_to_song_onset_ms')
    summ = {
        'duration_s': round(pr['dur'], 3), 'size': [pr['w'], pr['h']], 'fps': round(pr['fps'], 2),
        'n_cuts': len(cuts), 'cuts_per_s': round(len(cuts) / L, 2),
        'shot_s_median': round(float(np.median([r['dur'] for r in rows[1:-1]])), 3),
        'shots_with_detectable_hit_pct': round(100 * np.mean(['sfx_t' in r for r in rows])),
        'hit_to_planned_abs_ms_median': round(float(np.median(np.abs(col('to_planned_ms'))))) if len(col('to_planned_ms')) else None,
        'hit_to_song_onset_abs_ms_median': round(float(np.median(np.abs(sto)))) if len(sto) else None,
        'hits_within_40ms_of_song_onset_pct': round(float(np.mean(np.abs(sto) <= 40) * 100)) if len(sto) else None,
        'preroll_ms_median': round(float(np.median(col('preroll_ms')))) if len(col('preroll_ms')) else None,
        'cuts_within_40ms_of_song_onset_pct': round(float(np.mean(np.abs(col('cut_to_song_onset_ms')) <= 40) * 100)) if len(col('cut_to_song_onset_ms')) else None,
        'hit_peak_db_over_music_median': round(float(np.median(col('peak_db_vs_music'))), 1) if len(col('peak_db_vs_music')) else None,
        'music_gain_change_after_cut_db_median': round(float(np.median(np.abs(duck))), 2) if duck else None,
        'hard_cut_share': round(1 - sum(1 for s in shots if s.get('transition_in') == 'dissolve') / max(1, len(cuts)), 3),
        'cut_frame_error_median': float(np.median(np.abs(found))) if found else None,
        'cuts_found_exact_pct': round(100 * np.mean(np.abs(found) <= 1)) if found else None,
        'lufs_i': round(lufs, 2) if lufs is not None else None, 'true_peak_dbtp': round(tp, 2) if tp is not None else None,
        'song_removed_db': round(removed_db, 2), 'holds': sum(1 for s in shots if (s.get('hold') or 0) > 0),
    }
    # the references' own spread, not just their medians: sound.json pooled_measurements. A render is "like
    # the references" when each distribution's quartiles sit in theirs, so the loop can see which way to move.
    pm = (load_spec().get('pooled_measurements') or {})
    pre_r = pm.get('preroll_cut_to_hit_ms') or {}
    onset_r = pm.get('hit_to_song_onset_ms') or {}
    shot_r = pm.get('shot_frames_at_24fps') or {}
    lvl_r = pm.get('hit_peak_db_over_music_rms') or {}
    bands = {
        'preroll_ms': (col('preroll_ms'), [pre_r.get('p25', 82), pre_r.get('median', 140), pre_r.get('p75', 210)]),
        'hit_to_song_onset_abs_ms': (np.abs(sto), [0, onset_r.get('abs_median', 9), 25]),
        'shot_s': (np.array([r['dur'] for r in rows[1:-1]], float),
                   [shot_r.get('p25', 7) / 24, shot_r.get('median', 9) / 24, shot_r.get('p75', 11) / 24]),
        'peak_db_vs_music': (col('peak_db_vs_music'), [lvl_r.get('p25', 3), lvl_r.get('median', 5), lvl_r.get('p75', 7)]),
    }
    ref_bands = {}
    for k, (v, ref) in bands.items():
        if not len(v):
            ref_bands[k] = None
            continue
        p25, med, p75 = (round(float(np.percentile(v, p)), 3) for p in (25, 50, 75))
        ref_bands[k] = {'p25': p25, 'median': med, 'p75': p75, 'ref_p25_median_p75': [round(float(x), 3) for x in ref],
                        'median_in_band': bool(ref[0] <= med <= ref[2]), 'n': int(len(v))}
    summ['ref_bands'] = ref_bands
    summ['medians_in_reference_band'] = sum(1 for b in ref_bands.values() if b and b['median_in_band'])
    rep_ = plan.get('sound_edit') or {}
    lr = rep_.get('length_range') or [8.5, 11.0]
    lt = rep_.get('lufs_target') if rep_.get('lufs_target') is not None else -14.0
    T = [('hit_to_song_onset_abs_ms_median', lambda v: v is not None and v <= 15),
         ('hits_within_40ms_of_song_onset_pct', lambda v: v is not None and v >= 75),
         ('shots_with_detectable_hit_pct', lambda v: v >= 70),
         ('preroll_ms_median', lambda v: v is not None and 80 <= v <= 220),
         ('cuts_within_40ms_of_song_onset_pct', lambda v: v is not None and v <= 25),
         ('cuts_per_s', lambda v: 1.6 <= v <= 3.2),
         ('shot_s_median', lambda v: 0.27 <= v <= 0.5),
         ('hit_peak_db_over_music_median', lambda v: v is not None and 3 <= v <= 7),
         # sound.json targets 1.0 dB, but its own most viewed reference (FB15TNFAHVU) measures 1.69 dB:
         # a little limiter movement under the hits is within the references
         ('music_gain_change_after_cut_db_median', lambda v: v is None or v <= 1.7),
         ('hard_cut_share', lambda v: v >= 0.93),
         ('duration_s', lambda v: lr[0] - 0.6 <= v <= lr[1] + 0.6),
         ('lufs_i', lambda v: v is not None and lt - 1 <= v <= lt + 1),
         ('true_peak_dbtp', lambda v: v is not None and v <= -1.0)]
    if plan.get('sound_edit', {}).get('mode') == 'build':
        # the song's own onsets are the drums we replaced: judge against the planned hits instead
        T[0] = ('hit_to_planned_abs_ms_median', lambda v: v is not None and v <= 15)
        T[1] = ('shots_with_detectable_hit_pct', lambda v: v >= 70)
    passed = {k: bool(f(summ.get(k))) for k, f in T}
    summ['pass'] = passed
    summ['score'] = round(sum(passed.values()) / len(passed), 3)
    if sheet_dir:
        os.makedirs(sheet_dir, exist_ok=True)
        summ['sheets'] = cut_sheets(video, cuts, sheet_dir, plan)
    return {'summary': summ, 'shots': rows}


def cut_sheets(video, cuts, out_dir, plan, before=2, after=None, cell=150, step=None):
    """Frame strips around every cut, to be read next to the references' own strips in
    C:/Users/Home/AppData/Local/Temp/edit-refs/sound/analysis/<id>_cuts_*.png (which are 4 frames either
    side at 24 fps): a red bar above the first frame of the new shot, a green bar above the frame nearest to
    the planned hit. `step` samples every nth output frame, so the default covers the same 4 source frames
    after the cut that the reference sheets do rather than a third of the time. A rot90_cw delivery is turned
    back upright for reading; the picture area only, black bars cropped. Plus one frame per shot."""
    import cv2
    pr = probe(video)
    fps = pr['fps']
    step = int(step or max(1, round(fps / 24.0)))
    after = 5 if after is None else int(after)
    upright =(plan.get('deliver') or {}).get('mode') == 'rot90_cw'
    cap = cv2.VideoCapture(video)
    frames = []
    while True:
        ok, f = cap.read()
        if not ok:
            break
        if upright:
            f = cv2.rotate(f, cv2.ROTATE_90_COUNTERCLOCKWISE)
        frames.append(cv2.resize(f, (f.shape[1] // 4, f.shape[0] // 4), interpolation=cv2.INTER_AREA))
    cap.release()
    if not frames:
        return []
    mean = np.mean([f.mean(axis=2) for f in frames[::10]], axis=0)
    rows_ok, cols_ok = np.where(mean.max(axis=1) > 4)[0], np.where(mean.max(axis=0) > 4)[0]
    y0, y1 = (rows_ok[0], rows_ok[-1] + 1) if len(rows_ok) else (0, mean.shape[0])
    x0, x1 = (cols_ok[0], cols_ok[-1] + 1) if len(cols_ok) else (0, mean.shape[1])
    h, w = y1 - y0, x1 - x0
    sc = cell / max(h, w)
    tw, th = max(8, int(w * sc)), max(8, int(h * sc))

    def thumb(j, bar=None):
        img = np.zeros((th + 6, tw, 3), np.uint8)
        if 0 <= j < len(frames):
            img[6:] = cv2.resize(frames[j][y0:y1, x0:x1], (tw, th), interpolation=cv2.INTER_AREA)
        if bar:
            img[:5] = bar
        return img
    hits = [s['hit']['at'] for s in plan['shots']]
    hit_k = {int(round(hh * fps)) for hh in hits}
    rows = []
    for c in cuts:
        k = int(round(c * fps))
        js = ([k - m * step for m in range(before, 0, -1)] + [k]
              + [k + m * step for m in range(1, after + 1)])
        row = [thumb(j, (0, 0, 255) if j == k else ((0, 255, 0) if any(abs(j - h) < step for h in hit_k) else None))
               for j in js]
        rows.append(np.concatenate([np.concatenate([r, np.zeros((r.shape[0], 3, 3), np.uint8)], axis=1) for r in row], axis=1))
    out = []
    per = 12
    for p in range(0, len(rows), per):
        path = os.path.join(out_dir, f'cuts_{p // per:02d}.png')
        cv2.imwrite(path, np.concatenate([np.concatenate([r, np.zeros((3, r.shape[1], 3), np.uint8)], axis=0)
                                          for r in rows[p:p + per]], axis=0))
        out.append(path)
    mids = [thumb(min(len(frames) - 1, int(round(s['hit']['at'] * fps)) + 1)) for s in plan['shots']]
    cols = 8 if tw < th else 5
    while len(mids) % cols:
        mids.append(np.zeros_like(mids[0]))
    grid = np.concatenate([np.concatenate(mids[r:r + cols], axis=1) for r in range(0, len(mids), cols)], axis=0)
    path = os.path.join(out_dir, 'shots.png')
    cv2.imwrite(path, grid)
    out.append(path)
    return out


# ---------------------------------------------------------------- library

def run_library(queries, out_dir, count=14, kind='anime', budget_min=11.0):
    """core/edit/library.py --mode mood --with-audio --need-sfx per query (its own caches and download pacing),
    the total time split over the queries (a Short must finish in < 20 min on a 2-core runner). Returns the
    merged shots, each tagged with its query (theme matching)."""
    lib = os.path.join(HERE, 'library.py')
    if not os.path.exists(lib):
        raise RuntimeError('core/edit/library.py is missing')
    shots = []
    end = time.time() + budget_min * 60
    for k, q in enumerate(queries):
        left = end - time.time()
        if left < 45:
            break
        per = max(45.0, left / (len(queries) - k))
        d = os.path.join(out_dir, f'lib_{k:02d}')
        cmd = [sys.executable, lib, '--mode', 'mood', '--query', q, '--kind', kind, '--count', str(count),
               '--min-dur', '1.5', '--max-dur', '6', '--with-audio', '--need-sfx', '--budget', f'{per * 0.85:.0f}',
               '--out-dir', d]
        log('library:', q)
        try:
            subprocess.run(cmd, timeout=per + 60, check=False)
        except subprocess.TimeoutExpired:
            log('library timed out:', q)
        p = os.path.join(d, 'shots.json')
        if os.path.exists(p):
            for s in load_shots(p):
                s.setdefault('query', q)
                shots.append(s)
    return shots


# ---------------------------------------------------------------- long form (our own extension)

def compile_long(songs, shots, out, mode='accent', seed=0, workdir=None, aspect='16:9', watermark=None,
                 min_s=120.0, max_s=240.0, check_each=False):
    """A long 16:9 video in this style: several ~10 s sound edits of different songs, hard-cut at each song
    change (the references are all Shorts; sound.json calls this our own extension). Each song gets its own
    clips - an event is never used twice in the whole video. Returns a result dict."""
    workdir = workdir or tempfile.mkdtemp(prefix='sound_long_')
    os.makedirs(workdir, exist_ok=True)
    shots = load_shots(shots)
    events = detect_events(shots, cache_dir=workdir)
    used, segs, report = set(), [], []
    total = 0.0
    for i, song in enumerate(songs):
        if total >= max_s:
            break
        pool = [e for e in events if e['id'] not in used]
        try:
            plan = plan_sound_edit(song, shots, mode=mode, deliver='master', aspect='16:9', seed=seed + i,
                                   workdir=os.path.join(workdir, f'seg{i:02d}'), events=pool,
                                   title=os.path.splitext(os.path.basename(song))[0], watermark=watermark)
        except (TooFewEvents, RuntimeError) as ex:
            log(f'segment {i} ({os.path.basename(song)}) skipped: {ex}')
            continue
        seg = os.path.join(workdir, f'seg{i:02d}.mp4')
        render(plan, seg, os.path.join(workdir, f'seg{i:02d}'))
        # every event of a scene we used is out for the rest of the video (no scene appears twice)
        shown = {(s['path'], s['hit']['clip_t']) for s in plan['shots']}
        scenes = {e['scene'] for e in pool if (e['path'], e['t']) in shown}
        used |= {e['id'] for e in pool if e['scene'] in scenes}
        segs.append(seg)
        total += plan['duration']
        r = {'song': os.path.abspath(song), 'seg': seg, 'duration': plan['duration'],
             'plan': plan['sound_edit'], 'plan_file': seg.replace('.mp4', '.plan.json')}
        with open(r['plan_file'], 'w', encoding='utf-8') as f:
            json.dump(plan, f, indent=1)
        if check_each:
            r['check'] = check(seg, plan)['summary']
        report.append(r)
        log(f'segment {i}: {os.path.basename(song)} {plan["duration"]:.1f} s (total {total:.1f} s)')
    if not segs:
        raise RuntimeError('no segment could be built')
    lst = os.path.join(workdir, 'segments.txt')
    with open(lst, 'w', encoding='utf-8') as f:
        for sgm in segs:
            f.write("file '" + os.path.abspath(sgm).replace(os.sep, '/') + "'\n")
    r = subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'concat', '-safe', '0', '-i', lst, '-c', 'copy',
                        '-movflags', '+faststart', out], capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(out):      # different parameters: one re-encode pass
        r = _gpu.run(['ffmpeg', '-y', '-v', 'error', '-f', 'concat', '-safe', '0', '-i', lst, '-r', '60',
                      '-c:v', 'libx264', '-preset', 'medium', '-crf', '17', '-pix_fmt', 'yuv420p',
                      '-c:a', 'aac', '-b:a', '256k', '-ar', str(MSR), '-movflags', '+faststart', out], 'final',
                     capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError('concat failed: ' + (r.stderr or '')[-1500:])
    return {'ok': total >= min_s, 'video': os.path.abspath(out), 'duration': round(total, 2),
            'segments': report, 'songs': len(report)}


# ---------------------------------------------------------------- vibe pools (production entry, core/edit/vibe_edit.py)

SHORT_TARGET_S, LONG_TARGET_S = 36.0, 110.0     # aimed for inside the caller's range (whole bars; pool permitting)
RATE_SHORT, RATE_LONG = 2.2, 1.9
MIN_SHORT_S, MIN_LONG_S = 19.5, 45.0
# Loudness (measured 2026-10-12, Shape of You, 83 shots): the references are -5.8..-9.9 LUFS but clip (+1.5..+4.8 dBTP).
# With true peak <= -1 dBTP and a limiter, louder masters crush the hits and pump the song: hit over music / song
# gain change at the cuts = +6.0 / 0.76 dB at -14 LUFS (mix target +10), +4.2 / 1.4 dB at a -12 target (reached
# only -13.7), +1.2 / 1.9 dB at -7.8. The references' relative punch (+5 dB, song flat) is kept up to ~-13.5 LUFS;
# YouTube plays everything at -14 anyway, so the punch, not the integrated level, is what is matched.
VIBE_LUFS, VIBE_HIT_DB = -13.5, 9.5           # small pool: the shortest video still returned ok (with a reason)                # song hits per s (refs 1.6-3.2, median ~2.3; the long a bit calmer)


SILENT_OK = ('not_crisp', 'no_room_after')   # owner 2026-10-12 (sound_2): picture-only is the exception, not the rule
# quality cut-offs relaxed to AUDIBLE (owner: every hit slot needs the clip's own sound): the impact lands 0.11-0.2 s
# off the visible action / the action is small on screen; the sound itself is a clean hit
AUDIBLE_RELAXED = ('impact_off', 'nothing_moves')      # event rejections that still allow a picture-only ('song only') shot


def is_talking(s):
    """A talking mouth on screen. library.py writes 'talk' and 'mouth_flap' as 0-1 scores (its own gates reject
    talk >= 0.45 with speech) and rules['talk_mouth'] (clip_rules rejects >= 0.7 when still); the pool import may
    have turned 'talk' into a bool, so the raw scores are used whenever they are there."""
    hit, seen = False, False
    for k, th in (('mouth_flap', 0.3), ('talk', 0.45)):
        v = s.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            seen = True
            hit = hit or float(v) >= th
    tm = (s.get('rules') or {}).get('talk_mouth') if isinstance(s.get('rules'), dict) else None
    if isinstance(tm, (int, float)) and not isinstance(tm, bool):
        seen = True
        hit = hit or float(tm) >= 0.7
    return hit if seen else bool(s.get('talk'))


def _pool_shot(s):
    """A vibe-pool clip (core/edit/refs/pool-format.md) as a library shot: its 'sfx' [{t}] are the library's
    hit candidates."""
    s = dict(s)
    if not s.get('sfx_hits') and s.get('sfx'):
        s['sfx_hits'] = [float(x['t']) for x in s['sfx'] if isinstance(x, dict) and x.get('t') is not None]
    s['source_title'] = s.get('source_title') or ''
    return s


# owner 2026-10-12: "too much voice; hits should be mostly action sounds (punches, reloads, shotgun blasts, blade
# clashes, impacts) at the references' voice-vs-action ratio". Measured with the same metric on both sides (harmonic
# share of the 30-180 ms after the hit, HPSS, 150-4000 Hz; output/newtypes/ref_voice.py): the refs' clip-sound layer
# has 0-20 % (pooled 10 %) of hits >= 0.5 and 6.5 % >= 0.6, harm median 0.03-0.16 (Time Back excluded: a different
# master, its residual is the song); our first samples had 33-38 % >= 0.5, median 0.38. So an audible hit must be
# non-harmonic (harm < VOICED_HARM); harmonic events (a shout, a line, a hit buried in BGM) are kept only as a small
# budget (~3 % of the audible hits, owner: fewer voices than the refs: the refs' odd grunt), the rest are picture-only or dropped (a voice).
VOICED_HARM = float(os.environ.get('SOUND_VOICED_HARM', '0.5'))
VOICED_SHARE = float(os.environ.get('SOUND_VOICED_SHARE', '0.35'))   # hard cap; the cost below keeps it a minority
VOICED_COST = float(os.environ.get('SOUND_VOICED_COST', '0.9'))
# owner 2026-10-12: "no repeated moments". A pool clip is ONE moment (pool-format.md): it may give one shot only
# (several hits inside one clip used to show the same take 2-3 times), and clips that show the same picture (the
# same scene uploaded by two channels, a static frame repeated in a source) share one moment key.
DUP_CORR = float(os.environ.get('SOUND_DUP_CORR', '0.9'))


def _clip_sig(path, cache_dir):
    """7 normalised 32x18 grey frames (8-92 %) of a clip, cached."""
    st = os.stat(path)
    key = os.path.join(cache_dir, 'sig2_' + hashlib.md5(f'{os.path.abspath(path)}|{st.st_size}|{st.st_mtime}'.encode()).hexdigest() + '.npy')
    if os.path.exists(key):
        try:
            return np.load(key)
        except Exception:  # noqa: BLE001
            pass
    import cv2
    cap = cv2.VideoCapture(path)
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    out = []
    for u in (0.08, 0.22, 0.36, 0.5, 0.64, 0.78, 0.92):
        cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(u * n)))
        ok, fr = cap.read()
        if not ok:
            continue
        g = cv2.cvtColor(cv2.resize(fr, (32, 18), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY).astype(np.float32).ravel()
        if g.std() < 4:
            continue                     # flat / black frame: matches anything
        out.append((g - g.mean()) / g.std())
    cap.release()
    a = np.array(out, np.float32).reshape(-1, 576)
    try:
        np.save(key, a)
    except Exception:  # noqa: BLE001
        pass
    return a


def moment_keys(shots, cache_dir):
    """shot index -> moment key: one key per clip, clips with the same picture (max frame corr > DUP_CORR) or an
    overlapping source range merged (union-find)."""
    n = len(shots)
    par = list(range(n))

    def find(i):
        while par[i] != i:
            par[i] = par[par[i]]
            i = par[i]
        return i
    sigs = []
    for s in shots:
        try:
            sigs.append(_clip_sig(s['path'], cache_dir))
        except Exception:  # noqa: BLE001
            sigs.append(np.zeros((0, 576), np.float32))
    for i in range(n):
        for j in range(i + 1, n):
            a, b = shots[i], shots[j]
            same = False
            if a.get('source_id') and a.get('source_id') == b.get('source_id') and a.get('start') is not None                     and b.get('start') is not None:
                same = float(a['start']) < float(b.get('end') or 0) + 1 and float(b['start']) < float(a.get('end') or 0) + 1
            if not same and len(sigs[i]) and len(sigs[j]):
                same = float((sigs[i] @ sigs[j].T).max()) / 576 > DUP_CORR
            if same:
                par[find(j)] = find(i)
    return {i: f'm{find(i)}' for i in range(n)}


# owner 2026-10-12 (sound_3 review): "I need to know WHAT IS MAKING THE SOUND". An audible clip hit needs its cause on
# screen in the same frames (core/edit/sound_visible.py; study: refs/sound-visible-source.md): a motion onset / contact /
# spark within +-2 frames@24 of the transient, the acting object large (close-up / medium) and inside one 9:16 window,
# not an FX flash, and (SigLIP tags, SOUND_VIS_TAGS=1) an object / action in frame rather than a face / eyes / scenery.
# A hit that fails becomes picture-only (its shot may still fill a song-only slot). The reference passes 9 / 10 hits on
# the motion measures and 8 / 10 by eye (#11 FX flash, #16 face reacting).
SOUND_VISIBLE = os.environ.get('SOUND_VISIBLE', '1') != '0'
SOUND_VIS_TAGS = os.environ.get('SOUND_VIS_TAGS', '1') != '0'
VIS_PRE_MIN = float(os.environ.get('SOUND_VIS_PRE_MIN', '0.055'))  # an audible hit needs >= this pre-roll (s)
VIS_LEAD_F24 = float(os.environ.get('SOUND_VIS_LEAD', '2'))   # the cut comes >= this many frames@24 before the wind-up


def visible_pass(events, tags_on=None):
    """Measure every audible candidate's visible source; failing hits become picture-only. Adds e['visible'] (measures,
    tags, gate, score), e['focus_x'] (9:16 crop centre at the hit, 0-1 of the clip width) and stretches the event's
    pre-roll so the cut lands a few frames before the visible wind-up (the viewer sees the cause)."""
    import sound_visible as SV
    tags_on = SOUND_VIS_TAGS if tags_on is None else tags_on
    n_in = n_ok = 0
    why = {}
    out = []
    for e in events:
        if e.get('silent_only') or e.get('fill'):
            out.append(e)
            continue
        n_in += 1
        st = e.get('scene_t') or [None, None]
        try:
            m = SV.measure(e['path'], float(e['t']), box=e.get('content_box'), t_min=st[0], t_max=st[1])
        except Exception as ex:  # noqa: BLE001
            m = {'error': str(ex)[:120]}
        tg = None
        if tags_on and m.get('event_t') is not None:
            try:
                fps = float(e.get('fps') or 24.0)
                tg = SV.tags(e['path'], [max(float(st[0] or 0) + 0.01, m['event_t'] - 1 / fps), m['event_t']],
                             box=e.get('content_box'), region=m.get('bbox'))
            except Exception as ex:  # noqa: BLE001
                log('vision tags failed:', ex)
                tg = None
        ok, reason = SV.gate(m, tg)
        src, non, margin = SV.tag_summary(tg) if tg else (None, None, None)
        e = dict(e, visible={'ok': ok, 'reason': reason, 'score': SV.score(m, tg), 'offset_f24': m.get('offset_f24'),
                             'prom': m.get('prom'), 'area': m.get('area'), 'fit': m.get('fit'), 'kind': m.get('kind'),
                             'src': src, 'non': non, 'margin': margin, 'windup_t': m.get('windup_t'),
                             'event_t': m.get('event_t')},
                 focus_x=m.get('focus_x'), vis_src=src)
        if ok:
            n_ok += 1
            # the viewer must see the cause: cut >= VIS_LEAD frames before the wind-up / action frame
            act = min(float(m.get('windup_t') or e['t']), float(m.get('event_t') or e['t']), float(e['t']))
            need = float(e['t']) - act + VIS_LEAD_F24 / 24.0
            e['preroll'] = round(float(np.clip(max(float(e.get('preroll') or PREROLL_DEF), need), PREROLL_MIN, PREROLL_MAX)), 3)
            e['score'] = round(float(e['score']) + 0.12 * float(e['visible']['score']), 4)
        else:
            why[reason] = why.get(reason, 0) + 1
            e = dict(e, silent_only=True, vis_reject=reason)
        out.append(e)
    log(f'visible source: {n_ok}/{n_in} audible candidates pass ({", ".join(f"{k} {v}" for k, v in sorted(why.items(), key=lambda x: -x[1]))})')
    return out


def vibe_events(shots, cache_dir=None, voiced_budget=None):
    """Usable events of pool shots: crisp hits, plus 'silent_only' ones (a visible action whose sound is not a
    crisp hit, for the song-only shots); talking mouths only as a voice hit. One moment per clip (scene = the
    clip's moment key); harmonic (voiced) hits audible only within `voiced_budget`."""
    ev_cache = cache_dir or os.environ.get('SOUND_EV_CACHE') or os.path.join(tempfile.gettempdir(), 'sound-edit-events')
    os.makedirs(ev_cache, exist_ok=True)
    events = []
    for e in detect_events(shots, cache_dir=ev_cache, keep_rejected=True):
        if e['reject'] in SILENT_OK:
            e = dict(e, reject=None, silent_only=True)
        elif e['reject'] in AUDIBLE_RELAXED:
            e = dict(e, reject=None, relaxed=e['reject'], score=round(float(e['score']) - 0.08, 4))
        if not e['reject']:
            events.append(e)
    if SOUND_VISIBLE:
        events = visible_pass(events)
    talk = {i for i, s in enumerate(shots) if is_talking(s)}
    events = [e for e in events if not (e['shot'] in talk and (e['cls'] != 'voice' or e.get('silent_only')))]
    # pool growth (owner 2026-10-12: the filters shrank the sample space too much). The references cut 46 % of their
    # shots on the song alone (clip muted): such a 'song only' shot needs a picture, not a hit. A clean pool clip with
    # no usable hit event (no sound hit found, or a hit whose action is not visible) gives ONE picture-only shot at
    # its middle; talking clips never (a muted moving mouth), the safety gates are untouched.
    if events and os.environ.get('SOUND_PICTURE_FILL', '1') != '0':
        have = {e['shot'] for e in events}
        tpl = next((e for e in events if e.get('silent_only')), events[0])
        n_fill = 0
        for i, s_ in enumerate(shots):
            if i in have or i in talk:
                continue
            try:
                pr = probe(s_['path'])
            except Exception:  # noqa: BLE001
                continue
            dur = float(pr.get('dur') or 0.0)
            if dur < 0.9:
                continue
            t = round(0.45 * dur, 3)
            e = dict({k: v for k, v in tpl.items() if k not in ('visible', 'focus_x', 'vis_src', 'vis_reject')}, id=f'{i}:fill', shot=i, path=s_['path'], source_id=s_.get('source_id') or s_['path'],
                     source_title=s_.get('source_title') or '', t=t, cls='whoosh', strength=0.3, score=0.15,
                     pre_room=t, post_room=round(dur - t, 3), scene_t=[0.0, round(dur, 3)], vis_dt=0.0,
                     silent_only=True, fill=True, speech=0.0, harm=0.0, perc=False, dur=round(dur, 3),
                     fps=pr.get('fps') or tpl.get('fps'), aspect=round(pr['w'] / max(1, pr['h']), 3) if pr.get('w') else 1.78,
                     content_box=s_.get('content_box') or tpl.get('content_box'), tags=s_.get('tags') or [],
                     compilation=bool(COMPILATION_RE.search(s_.get('source_title') or '')))
            events.append(e)
            n_fill += 1
        log(f'picture-only fill: {n_fill} clips without a usable hit')
    mk = moment_keys(shots, ev_cache)
    for e in events:
        e['scene'] = mk[e['shot']]
    log(f'moments: {len(shots)} clips -> {len(set(mk.values()))} distinct moments')
    if voiced_budget is not None:
        # owner 2026-10-12 (sound_2 muted the voiced slots and lost the hits): a voiced hit (shout / grunt / a hit over
        # BGM, harm >= VOICED_HARM) stays AUDIBLE but costs more in the assignment (VOICED_COST), so a clean action
        # hit (punch / gun / blade / impact) takes the slot whenever the pool has one; only past `voiced_budget`
        # (a hard cap, ~1/3 of the hits) are the weakest voiced events dropped. Dialogue was rejected upstream.
        voiced = sorted([e for e in events if not e.get('silent_only')
                         and (e['cls'] == 'voice' or float(e.get('harm', 0.0)) >= VOICED_HARM)], key=lambda e: -e['score'])
        keep = {e['id'] for e in voiced[:max(0, int(voiced_budget))]}
        vids = {e['id'] for e in voiced}
        out = []
        for e in events:
            if e['id'] in vids:
                if e['id'] not in keep:
                    continue
                e = dict(e, voiced=True)
            out.append(e)
        events = out
        log(f'voiced hits: {len(voiced)} harmonic events, {len(keep)} audible-eligible (cap {voiced_budget}, costed)')
    events.sort(key=lambda e: (bool(e.get('silent_only')), -e['score']))
    return events


def render_vibe(song, shots, out, *, aspect='9:16', length=(30.0, 50.0), seed=0, workdir=None, theme=None, title=None,
                threads=None):
    """One sound edit from a vibe pool. song: the song file (played untouched, speed 1.0); shots: pool / library
    shot dicts ('path', 'pool_id', 'sfx' [{t}] ...). aspect '9:16' = the Short over `length` (default 30-50 s:
    one continuous section of the song, whole bars, its riff pattern re-found every 4 bars); '16:9' with e.g.
    (90, 180) = the landscape long (the same technique over a longer continuous section, a calmer cut rate).
    No watermark (vibe_edit burns the channel's in its own pass). CPU only (libx264, EDIT_GPU=0 safe);
    threads: ffmpeg threads (None = $SOUND_THREADS or ffmpeg's auto).
    Returns {'ok', 'video', 'duration', 'used': [pool_id of every shot shown], 'plan': plan.json path,
    'render_s', 'reason' (when not ok: 'pool_low' = too few usable hit scenes, else the error),
    'check': the quality summary, 'deliver'}."""
    t_start = time.time()
    out = os.path.abspath(out)
    workdir = os.path.abspath(workdir or tempfile.mkdtemp(prefix='sound_vibe_'))
    os.makedirs(workdir, exist_ok=True)
    threads = threads or (int(os.environ['SOUND_THREADS']) if os.environ.get('SOUND_THREADS', '').isdigit() else None)
    res = {'ok': False, 'video': out, 'duration': 0.0, 'used': [], 'plan': None, 'render_s': 0.0}

    def fail(reason, **kw):
        res.update(reason=reason, render_s=round(time.time() - t_start, 1), **kw)
        log('render_vibe failed:', reason)
        return res
    try:
        lo, hi = float(length[0]), float(length[1])
        long_form = aspect == '16:9'
        shots = [_pool_shot(s) for s in shots or [] if s.get('path') and os.path.exists(s['path'])]
        if not shots:
            return fail('pool_low', detail='no clips')
        # voiced cap: VOICED_SHARE of the hits (rate x target length)
        events = vibe_events(shots, voiced_budget=max(2, int(round(VOICED_SHARE * (hi if long_form else SHORT_TARGET_S) * (RATE_LONG if long_form else RATE_SHORT)))))
        log(f'render_vibe {aspect} {lo:.0f}-{hi:.0f} s: {len(shots)} clips, {len(events)} usable events, '
            f'{avail_slots(events, max(1.0, hi / 10.5))} scene slots')
        # the long uses as much of the pool as it can (target = the top of its range, the pool fit shortens it);
        # a small pool may give a shorter video than asked, down to MIN_SHORT_S / MIN_LONG_S (then 'reason' says so)
        target = float(hi if long_form else np.clip(SHORT_TARGET_S, lo, hi))
        floor = MIN_LONG_S if long_form else MIN_SHORT_S
        ranges = [(lo, hi)] + ([(min(lo, floor), hi)] if floor < lo else [])
        plan, last = None, None
        for k, rr in enumerate(ranges):
            try:
                plan = plan_sound_edit(song, shots, duration=target, mode='accent',
                                       deliver='master' if long_form else os.environ.get('SOUND_DELIVER', 'rot90'), theme=theme, title=title,
                                       watermark=None, seed=seed, workdir=os.path.join(workdir, 'plan'),
                                       events=[dict(e) for e in events], aspect=aspect, length_range=rr,
                                       rate=RATE_LONG if long_form else RATE_SHORT, loop_end=False,
                                       lufs=VIBE_LUFS, hit_db=VIBE_HIT_DB)
                break
            except TooFewEvents as ex:
                last = ex
        if plan is None:
            return fail('pool_low', detail=str(last), slots=getattr(last, 'have', None))
        if plan['duration'] < lo - 0.5:
            res['reason'] = (f'pool_low_shortened: {plan["duration"]:.1f} s instead of >= {lo:.0f} s '
                             f'({(plan["sound_edit"].get("pool_fit") or {}).get("slots_audible")} crisp-hit scenes)')
        plan_path = os.path.join(workdir, 'plan.json')
        render(plan, out, workdir=os.path.join(workdir, 'render'), threads=threads, colour_fix=True)
        by_path = {os.path.abspath(s['path']): s for s in shots}
        used = []
        for s in plan['shots']:
            pid = (by_path.get(os.path.abspath(s['path'])) or {}).get('pool_id')
            s['pool_id'] = pid
            if pid and pid not in used:
                used.append(pid)
        summ = None
        try:
            summ = check(out, plan)['summary']
        except Exception as ex:  # noqa: BLE001  a measuring failure never fails the render
            log('check failed:', ex)
        plan['check'] = summ
        with open(plan_path, 'w', encoding='utf-8') as f:
            json.dump(plan, f, indent=1)
        dur = probe(out)['dur']
        res.update(ok=True, duration=round(dur, 3), used=used, plan=plan_path, render_s=round(time.time() - t_start, 1),
                   deliver=(plan.get('deliver') or {}).get('mode'), check=summ,
                   render_report=plan.get('render_report'), shots=len(plan['shots']))
        return res
    except Exception as ex:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        return fail(f'{type(ex).__name__}: {ex}'[:500])


# ---------------------------------------------------------------- cli

def _args_plan(ap):
    ap.add_argument('--music', required=True)
    ap.add_argument('--shots', nargs='*', default=[])
    ap.add_argument('--start', type=float, default=None)
    ap.add_argument('--length', type=float, default=None)
    ap.add_argument('--mode', choices=['accent', 'build'], default='accent')
    ap.add_argument('--deliver', default='auto', choices=['auto', 'rot90', 'letterbox', 'master'])
    ap.add_argument('--aspect', default='9:16', choices=['9:16', '16:9'])
    ap.add_argument('--theme', default=None)
    ap.add_argument('--title', default=None)
    ap.add_argument('--watermark', default=None)
    ap.add_argument('--heatmap', default=None)
    ap.add_argument('--seed', type=int, default=None)
    ap.add_argument('--loop-end', type=int, default=None)
    ap.add_argument('--workdir', default=None)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('plan')
    _args_plan(p)
    p.add_argument('--out', required=True)
    r = sub.add_parser('render')
    r.add_argument('--plan', required=True)
    r.add_argument('--out', required=True)
    r.add_argument('--cpus', type=int, default=None, help='ffmpeg threads (2 = a GitHub Actions runner)')
    b = sub.add_parser('build')
    _args_plan(b)
    b.add_argument('--out', required=True)
    b.add_argument('--library', action='store_true', help='source shots with core/edit/library.py')
    b.add_argument('--queries', type=int, default=6)
    b.add_argument('--renderer', choices=['own', 'engine'], default='own')
    b.add_argument('--check', action='store_true')
    c = sub.add_parser('check')
    c.add_argument('--video', required=True)
    c.add_argument('--plan', required=True)
    c.add_argument('--sheet', default=None)
    e = sub.add_parser('events')
    e.add_argument('--shots', nargs='+', required=True)
    e.add_argument('--out', required=True)
    e.add_argument('--all', action='store_true')
    cp = sub.add_parser('compile')
    cp.add_argument('--songs', required=True, help='comma list of song files (one ~10 s sound edit each)')
    cp.add_argument('--shots', nargs='+', required=True)
    cp.add_argument('--out', required=True)
    cp.add_argument('--mode', choices=['accent', 'build'], default='accent')
    cp.add_argument('--watermark', default=None)
    cp.add_argument('--seed', type=int, default=0)
    cp.add_argument('--workdir', default=None)
    cp.add_argument('--min-s', type=float, default=120.0)
    cp.add_argument('--max-s', type=float, default=240.0)
    cp.add_argument('--check', action='store_true')
    q = sub.add_parser('queries')
    q.add_argument('--title', default=None)
    q.add_argument('--theme', default=None)
    q.add_argument('--n', type=int, default=6)
    q.add_argument('--seed', type=int, default=0)
    a = ap.parse_args()

    if a.cmd == 'queries':
        qs, theme = library_queries(a.title, a.theme, a.n, a.seed)
        print(json.dumps({'ok': True, 'queries': qs, 'theme': theme}))
        return
    if a.cmd == 'events':
        evs = detect_events(load_shots(a.shots), keep_rejected=a.all)
        with open(a.out, 'w', encoding='utf-8') as f:
            json.dump({'events': evs}, f, indent=1)
        print(json.dumps({'ok': True, 'events': len(evs), 'out': a.out}))
        return
    if a.cmd == 'compile':
        res = compile_long([x for x in a.songs.split(',') if x], a.shots, a.out, mode=a.mode, seed=a.seed,
                           workdir=a.workdir, watermark=a.watermark, min_s=a.min_s, max_s=a.max_s,
                           check_each=a.check)
        print(json.dumps({k: v for k, v in res.items() if k != 'segments'} |
                         {'segments': [{kk: vv for kk, vv in s.items() if kk in ('song', 'duration', 'check')}
                                       for s in res['segments']]}))
        return
    if a.cmd == 'render':
        with open(a.plan, encoding='utf-8') as f:
            plan = json.load(f)
        t = time.time()
        render(plan, a.out, threads=a.cpus)
        print(json.dumps({'ok': True, 'video': a.out, 'render_s': round(time.time() - t, 1)}))
        return
    if a.cmd == 'check':
        with open(a.plan, encoding='utf-8') as f:
            plan = json.load(f)
        res = check(a.video, plan, a.sheet)
        print(json.dumps(res['summary']))
        return
    # plan / build
    workdir = a.workdir or os.path.join(os.path.dirname(os.path.abspath(a.out)), 'sound_work')
    os.makedirs(workdir, exist_ok=True)
    shots = load_shots(a.shots)
    used_q = []
    if a.cmd == 'build' and a.library and not shots:
        qs, _ = library_queries(a.title, a.theme, a.queries, a.seed or 0)
        used_q = list(qs)
        shots = run_library(qs, workdir)
    if not shots:
        print(json.dumps({'ok': False, 'error': 'no shots'}))
        sys.exit(1)
    plan = None
    for attempt in range(3):
        try:
            plan = plan_sound_edit(a.music, shots, music_start=a.start, duration=a.length, mode=a.mode, deliver=a.deliver,
                                   theme=a.theme, title=a.title, watermark=a.watermark, heatmap=a.heatmap, seed=a.seed,
                                   workdir=workdir, loop_end=None if a.loop_end is None else bool(a.loop_end), aspect=a.aspect)
            break
        except TooFewEvents as ex:
            if not (a.cmd == 'build' and a.library) or attempt == 2:
                print(json.dumps({'ok': False, 'error': str(ex), 'events': ex.have}))
                sys.exit(2)
            # more material: the next queries of the sound themes
            more = [q for q in library_queries(a.title, a.theme, 20, (a.seed or 0) + attempt + 1)[0] if q not in used_q][:4]
            used_q += more
            log(f'{ex}; sourcing more: {more}')
            shots += run_library(more, os.path.join(workdir, f'more{attempt}'))
    plan_path = a.out if a.cmd == 'plan' else os.path.splitext(a.out)[0] + '.plan.json'
    with open(plan_path, 'w', encoding='utf-8') as f:
        json.dump(plan, f, indent=1)
    if a.cmd == 'plan':
        print(json.dumps({'ok': True, 'plan': plan_path, 'sound_edit': {k: v for k, v in plan['sound_edit'].items() if k != 'levels'}}))
        return
    t = time.time()
    if a.renderer == 'engine' and os.path.exists(os.path.join(HERE, 'engine.py')):
        subprocess.run([sys.executable, os.path.join(HERE, 'engine.py'), '--plan', plan_path, '--out', a.out], check=True)
    else:
        render(plan, a.out, workdir)
    res = {'ok': True, 'video': a.out, 'plan': plan_path, 'render_s': round(time.time() - t, 1),
           'duration': plan['duration'], 'sound_edit': {k: v for k, v in plan['sound_edit'].items() if k != 'levels'}}
    if a.check:
        res['check'] = check(a.out, plan, os.path.join(workdir, 'sheets'))['summary']
    print(json.dumps(res))


if __name__ == '__main__':
    main()
