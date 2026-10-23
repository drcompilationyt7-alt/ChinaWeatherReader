#!/usr/bin/env python3
"""
Edit planners: music analysis + a clip library -> a declarative plan.json for core/edit/engine.py.

    python core/edit/planners.py --type song|dance|character|kpop|sound|long \
        --audio song.mp3 [--analysis music.json] [--heatmap heatmap.json] --shots shots.json --out plan.json \
        [--flavor song|dance|character|kpop] [--template NAME] [--preset NAME|auto] [--length S] [--title T] \
        [--subtitle S] [--watermark W] [--character NAME] [--framing aura|attractive] [--caption TEXT] [--seed N] \
        [--words '[{"text":"back","t0":5.96}]'] [--vocal-onsets onsets.json]

--preset picks a named pipeline from core/edit/presets.py ("this pipeline for these clips, that pipeline for
those clips"): it chooses the planner template, and through it the style preset and the look. --preset auto
lets presets.choose_preset() match the song analysis and the clip pool against every preset's selection
signals. --template still wins when both are given.

Every number below comes from the Study specs in core/edit/refs/<group>.json (general = song edits,
dance, character, kpop, sound); the comments name the reference it was measured on.

What each planner does
  plan_song_edit       one song, many good shots (the song's own anime / group, or a mood mix).
                       Templates (general.json style.templates): flow_blur (ZkWQ, cj12, S2rz: blur
                       bridge on every felt beat), hype_typography (i4f, m3lv, zawB: 4-5 frame
                       transitions, flashes, hero cards), cinematic_slowmo (_sX2, the owner's
                       "crisp clips then smooth slow mo": hard cut + bloom bump every ~1 s, crisp
                       holds and 0.35-0.6x slow motion), beat_collection_band (GHB: one character
                       per beat in a 16:9 band)
  plan_dance_edit      dance clips cut on the beat with the moves landing on it: burst_land
                       velocity (Uf_4: slow on the kick, a 3x burst at beat phase 0.85, each pose
                       change warped into the burst) + one punch per beat; templates
                       short_cute_parade / short_smooth_velocity / short_raw
  plan_character_edit  one character only: hook-intro hold -> drop device -> 1-cut-per-beat chorus
                       block (replayed with 0-30% swapped) -> slow hero hold -> fade; families
                       quick_velocity / soft_romance (adult characters only) / raw_crisp
  plan_kpop_edit       member edits: postcard intro (mFHp6Dd2ISk, the owner's favourite: 4 cards,
                       the drop clip develops in the last one, zooms to fill, grade pop on the
                       drop beat), stage/MV (establishing push-in -> zoom-through), single-clip trend
  plan_sound_edit      core/edit/sound_edit.py (clip sounds land on the song's onsets)
  plan_long_edit       16:9, 2-4 min, the full song section by section (intro / verse / build /
                       chorus / bridge / outro) in the vocabulary of the chosen flavor

Energy -> shots: calm verses get slow-motion holds on beautiful shots, builds get faster cuts
(2 -> 1 -> 1/2 beat), the drop gets the hardest shots with a flash on its downbeat and a hero
transition every 4 bars.

Cut timing: a shot's t0 is its FIRST frame, placed `lead` ms before the beat it belongs to
(blur bridges 80 ms early so they resolve on the hit, flashes peak on the beat); `beat` keeps the
musical target. All times in plan.json are OUTPUT seconds (0 = first frame = song time music_start);
src_in / speed_curve clip times are seconds in the clip file.

plan.json follows core/edit/engine.py's schema (extra keys are hints):
  {aspect, fps, width, height, music, music_start, duration, clip_audio, style, look, frame, group, template, grade,
   title, subtitle, watermark, beats (the felt pulse), raw_beats, downbeats, energy, sections, drop, lead: "none",
   seed, particles: false, style_overrides: {end: cut, [text.title_when: none]},
   shots: [{path, t0, t1, src_in, speed, speed_curve [[seconds since t0, clip time], ...] (at the output fps),
            camera {move, amount, zoom, pan, roll, handheld, follow, accents [{at, kind}]}, transition_in (transitions.py
            names, "+bleach" on K-pop hero cuts), transition_params, layout, beat, role, source_id, source_title,
            source_channel, content_box, tags, clip_duration}],
   overlays: engine overlay types (fade, particles, ring, text, title_card, desaturate, letterbox, sticker, echo, mute,
             beat_light_pulse) and scenes that replace the picture (postcard_intro, text_on_black, music_card; the shots
             under a scene are the next shot's clip, frozen),
   audio: {fade_in, fade_out, target_lufs, true_peak_db, mute}, planner: {type, seed, window, ...}}
The planner owns structure: the style's automatic ending and particles are switched off (and its title card when the
planner shows the title itself); looks, transition envelopes, text styles and the watermark come from styles.py.

The last stdout line is JSON: {"ok": true, "plan": path, "type", "style", "template", "duration",
"shots", "clips": [source ids], "channels": [...], "transitions": {...}, "speeds": {...}}.
"""
import argparse
import inspect
import json
import math
import os
import random
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import music as MU  # noqa: E402
try:
    import styles as ST  # noqa: E402  (the engine's presets: leads per transition, looks)
except Exception:  # the planner still works without the engine package
    ST = None

# ---------------------------------------------------------------- presets

# template -> (style preset in core/edit/styles.py, spec group, default grade)
STYLE = {
    'flow_blur': ('flow_blur', 'general', 'warm_glow'),
    'hype_typography': ('hype_flash', 'general', 'show_palette'),
    'cinematic_slowmo': ('velocity_slowmo', 'general', 'warm_glow'),
    'beat_collection_band': ('beat_band', 'general', 'source'),
    'fx_heavy': ('fx_typography', 'fx', 'neutral'),
    'short_cute_parade': ('dance_sync', 'dance', 'cute'),
    'short_smooth_velocity': ('dance_smooth', 'dance', 'velocity_dark'),
    'short_raw': ('dance_sync', 'dance', 'source'),
    'quick_velocity': ('character_aura', 'character', 'dark_contrast'),
    'soft_romance': ('character_soft', 'character', 'pastel_pink'),
    'raw_crisp': ('character_raw', 'character', 'crisp_vivid'),
    'postcard_member_edit': ('postcard_kpop', 'kpop', 'kpop_crisp'),
    'stage_mv_member_edit': ('kpop_stage', 'kpop', 'kpop_crisp'),
    'single_clip_trend': ('kpop_trend', 'kpop', 'tint_hue_push'),
    'sound_hit_sync': ('sound_edit', 'sound', 'source'),
    'long_song': ('cinematic_warm', 'general', 'warm_glow'),
    'long_dance': ('dance_amv', 'dance', 'amv_bloom'),
    'long_character': ('character_aura', 'character', 'dark_contrast'),
    'long_kpop': ('postcard_kpop', 'kpop', 'kpop_crisp'),
}

# Lead of a shot's first frame before its beat, ms (negative = early), per spec group.
# ONE TABLE ONLY: this is core/edit/styles.py LEAD_MS. There used to be a second copy here that disagreed
# with it on 18 transitions (general/white_bar_wipe -147 vs -33, character/white_out 0 vs -75, sound/hard
# -140 vs 0, ...); because Builder.lead() prefers styles.lead_s() whenever styles imports, the same plan
# rendered with different cut placement depending on whether that import succeeded. The fallback below is
# now derived from the same dict, so the two can never diverge again.
if ST is not None:
    LEAD_MS = ST.LEAD_MS
else:   # styles.py unavailable (the planner still runs standalone): the family defaults only
    LEAD_MS = {'general': {'default': -80}, 'dance': {'default': -60}, 'character': {'default': -65},
               'kpop': {'default': -40}, 'sound': {'default': 0}, 'fx': {'default': -67}}

# general.json style.cut_rules.interval_beats (felt beats per shot, by loudness tercile)
GEN_INTERVALS = {'loud': {1: 0.82, 0.5: 0.09, 1.5: 0.04, 2: 0.05},
                 'mid': {1: 0.72, 0.5: 0.15, 1.5: 0.04, 2: 0.09},
                 'quiet': {1: 0.43, 0.5: 0.10, 1.5: 0.05, 2: 0.25, 3: 0.17}}
# general.json style.transitions.per_cut_p / hero_p_per_section_downbeat
GEN_CUT_P = {'blur_bridge': 0.60, 'flash_white': 0.12, 'zoom_through': 0.08, 'whip_slide': 0.05, 'dissolve_blur': 0.05,
             'exposure_bump_hard': 0.04, 'punch_settle': 0.02, 'invert_strobe': 0.02, 'rgb_split_glitch': 0.02}
GEN_HERO_P = {'flash_white': 0.28, 'radial_zoom_burst': 0.15, 'card_tilt_in': 0.1, 'photo_collage': 0.08, 'shape_wipe': 0.07,
              'white_bar_wipe': 0.1, 'split_screen': 0.08, 'dip_black_strobe': 0.07, 'invert_strobe': 0.05,
              'halftone_flash': 0.02}
# blur_flow body cuts as ZkWQloS4jtM counts them (47 cuts: blur bridge 19, white flash 12, whip 4, zoom-through 4,
# dip-black strobe 2, invert strobe 2, dissolve 2, rgb split 1) - remake 2026-09-28
ZKW_CUT_P = {'blur_bridge': 0.46, 'flash_white': 0.26, 'whip_slide': 0.09, 'zoom_through': 0.09, 'dip_black_strobe': 0.03,
             'invert_strobe': 0.03, 'rgb_split_glitch': 0.04}
# blur_flow 's2rz' variant: S2rzMsl0N7k's own 35 body cuts (blur bridge 21, white flash 9, whip 3, zoom-through 3, rgb
# split 1, punch-settle 1) - remake round 2 (refs/remake-blur_flow-S2rz.md)
S2RZ_CUT_P = {'blur_bridge': 0.58, 'flash_white': 0.25, 'whip_slide': 0.06, 'zoom_through': 0.06, 'rgb_split_glitch': 0.025,
              'punch_settle': 0.025}
# dance.json style.transitions.probabilities (hard cuts never at a dancer switch). 30 % of the hard cuts
# land enlarged and settle (s_k = 1.18^(0.55^k)) -> hard_settle, which used to be in no pool.
DANCE_TR_P = {'hard': 0.20, 'hard_settle': 0.08, 'defocus_in': 0.2, 'zoom_blur_in': 0.14, 'flash_white': 0.1, 'flash_in': 0.08,
              'cross_dissolve': 0.08, 'whip_diag': 0.04, 'pattern_flash': 0.03, 'dark_blur_in': 0.03, 'lens_zoomthrough': 0.02}
# dance.json style.camera.accent_mix (one in-shot punch per beat, attack at beat phase 0.5-0.7)
DANCE_ACCENTS = {'zoom_in_punch': 0.45, 'vslide_wrap': 0.2, 'zoom_out_punch': 0.15, 'rotate_punch': 0.07, 'blur_pulse': 0.08,
                 'flash_pulse': 0.05}
# character.json style.transitions.per_cut_p per family
CHAR_CUT_P = {
    'quick_velocity': {'flash_zoom': 0.26, 'mb_cut': 0.22, 'whip_horizontal': 0.1, 'zoom_through_color_field': 0.08,
                       'impact_zoom_1frame': 0.07, 'spin_blur': 0.06, 'whip_vertical': 0.05, 'hard': 0.05,
                       'mirror_tile_scale_in': 0.04, 'tint_flash': 0.04, 'shape_wipe': 0.03},
    'soft_romance': {'white_out': 0.22, 'defocus_dissolve': 0.25, 'impact_zoom_1frame': 0.15, 'whip_vertical': 0.12,
                     'hard': 0.12, 'dip_black': 0.08, 'glow_cut': 0.06},
    'raw_crisp': {'hard': 0.45, 'white_flash_cut': 0.45, 'dip_black': 0.05, 'mb_cut': 0.05},
}
CHAR_DROP_ENTRY = {'zoom_through_color_field': 0.4, 'flash_zoom': 0.4, 'impact_zoom_1frame': 0.2}
# block-start devices (mGz9 cards and shape wipes; the heart / iris mask only in soft edits)
CHAR_BLOCK_HERO = {'card_tilt_in': 0.3, 'mirror_tile_scale_in': 0.3, 'shape_wipe': 0.25, 'iris_wipe': 0.15}
CHAR_GRADES = {'quick_velocity': {'dark_contrast': 0.35, 'teal_mono': 0.25, 'golden_bloom': 0.2, 'faded_film': 0.2},
               'soft_romance': {'pastel_pink': 0.6, 'soft_glow': 0.4}, 'raw_crisp': {'crisp_vivid': 1.0}}
# kpop.json style.transitions
# The blur-in-punch variants (shake / rotate / zoom) are the same cut with the measured secondary motion;
# they are in the styles pool but the planner names every cut explicitly, so they need a share here too.
KPOP_HOUSE = {'blur_in_punch': 0.35, 'shake_blur': 0.06, 'blur_rotate': 0.05, 'blur_zoom': 0.04, 'vslide_wrap': 0.2,
              'hard_punch': 0.15}
# kpop.json: the reference plays peek_window immediately BEFORE cutout_prereveal (9.97 s); peek_window was
# implemented but in no pool at all, so that pairing could never be produced.
KPOP_HERO = {'cutout_prereveal': 0.34, 'slices_reveal': 0.14, 'photo_clones': 0.14, 'zoom_through': 0.13,
             'zoom_blur_flash': 0.13, 'peek_window': 0.12}
# fx.json (WGvtliE1298): the house join is an exponential whip; every join hides inside an extreme state.
FX_CUT_P = {'whip_exp_white': 0.34, 'whip_exp_black': 0.22, 'rack_focus': 0.18, 'spin_settle': 0.12, 'zoom_settle': 0.14}
FX_HERO_P = {'whip_exp_soft': 0.34, 'glass_shatter': 0.22, 'spin_settle': 0.22, 'dolly_out_white': 0.22}

MEME_CAPTIONS = ['ME THINKING I WON\'T FALL FOR ANOTHER BIAS', 'POV: YOU JUST FOUND YOUR NEW BIAS', 'ME PRETENDING I DON\'T HAVE A BIAS',
                 'WHEN THE BIAS WRECKER APPEARS', 'ME SAYING THIS IS MY LAST FANCAM']


# ---------------------------------------------------------------- small helpers

def log(*a):
    print(*a, file=sys.stderr, flush=True)


def wpick(rng, table, exclude=()):
    items = [(k, v) for k, v in table.items() if k not in exclude and v > 0]
    if not items:
        items = list(table.items())
    tot = sum(v for _, v in items)
    r = rng.uniform(0, tot)
    for k, v in items:
        r -= v
        if r <= 0:
            return k
    return items[-1][0]


def latin_text(s, n=40):
    """On-video text is drawn with Latin fonts: keep Latin letters, digits and punctuation (no emoji / CJK)."""
    if not s:
        return None
    out = ''.join(ch for ch in str(s) if (32 <= ord(ch) < 0x250) or ch in '’‘“”')
    out = ' '.join(out.split()).strip(' -|:')
    return out[:n] if out else None


def smoothstep(x):
    x = np.clip(x, 0, 1)
    return x * x * (3 - 2 * x)


def level_of(e):
    return 'loud' if e >= 0.7 else ('mid' if e >= 0.4 else 'quiet')


def probe_duration(path):
    try:
        r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', path],
                           capture_output=True, text=True, timeout=30)
        return float(r.stdout.strip())
    except Exception:
        return 0.0


def motion_hits(path, fps=30):
    """Frame-difference peaks of a clip (its moves), for clips the library gave no hits for."""
    try:
        r = subprocess.run(['ffmpeg', '-v', 'error', '-i', path, '-an', '-vf', f'fps={fps},scale=64:36,format=gray',
                            '-f', 'rawvideo', '-'], capture_output=True, timeout=60)
    except Exception:
        return []
    a = np.frombuffer(r.stdout, np.uint8)
    if a.size < 64 * 36 * 4:
        return []
    f = a[:a.size // (64 * 36) * 64 * 36].reshape(-1, 64 * 36).astype(np.float32)
    e = np.abs(np.diff(f, axis=0)).mean(axis=1)
    e = np.convolve(e, np.ones(3) / 3, mode='same')
    thr = e.mean() + 0.5 * e.std()
    hits, last = [], -1e9
    for k in range(1, len(e) - 1):
        if e[k] >= thr and e[k] >= e[k - 1] and e[k] >= e[k + 1] and k - last >= 0.2 * fps:
            hits.append(round((k + 1) / fps, 3))
            last = k
    return hits


# ---------------------------------------------------------------- speed curves
#
# A curve is a rate function of output time (speed = clip seconds per output second); it is integrated
# on a fine grid, and the plan gets [[u, clip_t], ...] at the output frame rate (u = 0..1 over the shot).

def rate_constant(v):
    return lambda t: np.full_like(np.asarray(t, float), v)


def rate_u_curve(t0, t1, lo=0.55, hi_in=1.6, hi_out=1.0, tau_in=0.12, tau_out=0.1):
    """general.json speed.velocity_curve_in_shot: fast entry, slow middle, speeding into the next cut."""
    d = max(1e-3, t1 - t0)

    def f(t):
        x = np.clip((np.asarray(t, float) - t0) / d, 0, 1)
        return lo + (hi_in - lo) * np.exp(-x / tau_in) + (hi_out - lo) * np.exp(-(1 - x) / tau_out)
    return f


def rate_kpop(t0, t1, lo=0.45, hi=1.2, tau=0.18):
    """kpop.json speed.velocity_per_shot: edges of a shot 2-3x faster than its middle."""
    d = max(1e-3, t1 - t0)

    def f(t):
        x = np.clip((np.asarray(t, float) - t0) / d, 0, 1)
        return lo + (hi - lo) * (np.exp(-x / tau) + np.exp(-(1 - x) / tau))
    return f


def rate_character(t0, t1, shape, base):
    """character.json speed.chorus.curve: impact (1.8x -> base by u=0.25), dip (1.6x -> base -> 1.4x), flat."""
    d = max(1e-3, t1 - t0)

    def f(t):
        u = np.clip((np.asarray(t, float) - t0) / d, 0, 1)
        if shape == 'impact':
            return base + (1.8 - base) * (1 - np.clip(u / 0.25, 0, 1)) ** 2
        if shape == 'dip':
            return base + (1.6 - base) * (1 - np.clip(u / 0.2, 0, 1)) ** 2 + (1.4 - base) * np.clip((u - 0.85) / 0.15, 0, 1) ** 2
        return np.full_like(u, base)
    return f


def burst_g(phi, lo=0.5, hi=3.0, peak=0.85, sigma=0.12):
    """dance.json speed.presets.burst_land (Uf_4): slow on the kick, a burst at beat phase 0.85."""
    return lo + (hi - lo) * np.exp(-0.5 * ((phi - peak) / sigma) ** 2)


def wave_g(phi, center=-0.1):
    """dance.json speed.presets.smooth_wave (ONlH): 1.2 + 0.5 cos(2 pi (phi - center))."""
    return 1.2 + 0.5 * np.cos(2 * np.pi * (phi - center))


_PHI = np.linspace(0, 1, 2001)
_BG = burst_g(_PHI)
_BURST_MEAN = float(np.sum((_BG[1:] + _BG[:-1]) / 2 * np.diff(_PHI)))
_F_BURST = np.concatenate([[0], np.cumsum((_BG[1:] + _BG[:-1]) / 2 * np.diff(_PHI))]) / _BURST_MEAN
F85 = float(np.interp(0.85, _PHI, _F_BURST))  # share of a beat's source advance done by the burst peak


def integrate(rate, t0, t1, fps, over=4):
    """Clip offsets at output frames of [t0, t1] (and the total) for a rate function."""
    n = max(1, int(round((t1 - t0) * fps * over)))
    ts = np.linspace(t0, t1, n + 1)
    r = np.maximum(0.0, rate(ts))
    off = np.concatenate([[0.0], np.cumsum((r[1:] + r[:-1]) / 2 * np.diff(ts))])
    return ts, off


def curve_points(ts, off, src_in, fps):
    """[[seconds since the shot's t0, clip time], ...] at the output frame rate (engine.py speed_curve)."""
    t0, t1 = ts[0], ts[-1]
    d = max(1e-6, t1 - t0)
    k = max(2, int(round(d * fps)) + 1)
    tt = np.linspace(t0, t1, k)
    oo = np.interp(tt, ts, off)
    return [[round(float(a - t0), 5), round(float(src_in + b), 4)] for a, b in zip(tt, oo)]


# ---------------------------------------------------------------- the shot pool

ROLE_TAGS = {
    'hero': {'aura': 0.5, 'closeup': 0.4, 'performance': 0.3, 'action': 0.2, 'scenery': 0.1},
    'calm': {'closeup': 0.4, 'scenery': 0.4, 'cute': 0.3, 'aura': 0.2},
    'build': {'action': 0.3, 'aura': 0.3, 'performance': 0.2, 'dance': 0.2, 'closeup': 0.1},
    'drop': {'action': 0.5, 'fight': 0.5, 'aura': 0.4, 'performance': 0.3, 'dance': 0.3},
    'dance': {'dance': 1.0, 'performance': 0.4, 'cute': 0.2},
    'face': {'closeup': 0.8, 'aura': 0.3, 'cute': 0.2},
    'wide': {'wide': 0.6, 'scenery': 0.5, 'performance': 0.3},
    'any': {},
}


class Pool:
    """The library's shots, handed out by role without repeating a clip too soon or re-using a segment."""

    def __init__(self, shots, rng, char_min=None):
        self.rng = rng
        out = []
        n = max(1, len(shots))
        for i, s in enumerate(shots):
            p = s.get('path')
            if not p or not os.path.exists(p) or s.get('has_text'):
                continue
            d = dict(s)
            d['duration'] = float(d.get('duration') or probe_duration(p) or 0)
            if d['duration'] < 0.5:
                continue
            d['_tags'] = set(d.get('tags') or [])
            fr = d.get('framing')
            if fr == 'closeup':
                d['_tags'].add('closeup')
            elif fr == 'wide':
                d['_tags'].add('wide')
            d['_rank'] = 1.0 - i / n
            d['_used'] = []
            d['_last'] = -99
            d['_uses'] = 0
            out.append(d)
        if char_min is not None:
            cm = [x for x in out if x.get('character_match') is None or float(x['character_match']) >= char_min]
            out = cm if len(cm) >= 3 else out
        # motion / aesthetic as percentile ranks (their scales are the library's business)
        for key in ('motion', 'aesthetic'):
            vals = [float(x.get(key)) for x in out if x.get(key) is not None]
            for x in out:
                v = x.get(key)
                x['_' + key] = (sum(1 for w in vals if w <= float(v)) / len(vals)) if (v is not None and vals) else 0.5
        self.shots = out

    def __len__(self):
        return len(self.shots)

    def sources(self):
        return {s.get('source_id') for s in self.shots if s.get('source_id')}

    def free_start(self, s, need, want=None, overlap_ok=False, min_start=0.0):
        """A clip start with `need` seconds free (not used yet), nearest to `want`, not before `min_start`."""
        D = s['duration']
        if need + min_start > D - 0.02:
            return None
        grid = np.arange(min_start, D - need - 0.02 + 1e-6, 0.05) if D - need - min_start > 0.07 else np.array([min_start])
        if not overlap_ok and s['_used']:
            ok = np.ones(len(grid), bool)
            for a, b in s['_used']:
                ok &= (grid + need <= a + 0.05) | (grid >= b - 0.05)
            grid = grid[ok]
            if not len(grid):
                return None
        if want is None:
            want = 0.15 * (D - need)
        return float(grid[int(np.argmin(np.abs(grid - want)))])

    def score(self, s, role, k, prev):
        tags = ROLE_TAGS.get(role, {})
        tagfit = sum(w for t, w in tags.items() if t in s['_tags'])
        tagfit = min(1.0, tagfit)
        m = s['_motion']
        if role == 'calm':
            mfit = 1 - min(1.0, abs(m - 0.4) * 2)
        elif role == 'dance':
            per = s.get('periodic')
            mfit = 0.5 * m + 0.5 * min(1.0, float(per) / 0.35) if per is not None else m
        elif role in ('drop', 'build'):
            mfit = m
        elif role in ('hero', 'face'):
            mfit = 0.6 - 0.3 * abs(m - 0.5)
        else:
            mfit = 0.5
        cm = s.get('character_match')
        cfit = float(cm) if cm is not None else 0.5
        sc = 0.33 * s['_aesthetic'] + 0.2 * s['_rank'] + 0.25 * tagfit + 0.17 * mfit
        if role in ('hero', 'face'):
            sc += 0.1 * cfit
        sc -= 0.14 * s['_uses']
        if s.get('has_speech'):
            sc -= 0.25
        if prev is not None and s.get('source_id') and s.get('source_id') == prev.get('source_id'):
            sc -= 0.2
        return sc + self.rng.uniform(0, 0.12)

    def take(self, role, need, k, prev=None, want=None, gap=4, exclude=(), min_start=0.0):
        """The best free shot for `role` with `need` clip seconds; returns (shot, start) or (None, None)."""
        for g, overlap in ((gap, False), (2, False), (1, False), (1, True)):
            cands = []
            for s in self.shots:
                if s is prev or id(s) in exclude or k - s['_last'] < g:
                    continue
                w = want(s) if callable(want) else want
                st = self.free_start(s, need, w, overlap_ok=overlap, min_start=min_start)
                if st is None:
                    continue
                cands.append((self.score(s, role, k, prev), s, st))
            if cands:
                cands.sort(key=lambda c: -c[0])
                return cands[0][1], cands[0][2]
        return None, None

    def longest(self, exclude=(), prev=None, k=None):
        """The longest clip, preferring one that is not the previous shot and was not used just before."""
        c = [s for s in self.shots if id(s) not in exclude]
        if not c:
            return None
        fresh = [s for s in c if s is not prev and (k is None or k - s['_last'] >= 2)]
        return max(fresh or c, key=lambda s: (s['duration'] - 1.5 * s['_uses']))

    def mark(self, s, a, b, k):
        s['_used'].append((a, b))
        s['_last'] = k
        s['_uses'] += 1


# ---------------------------------------------------------------- the builder

class Builder:
    """Segments (t0, t1, beat, transition, speed, role, camera, layout) -> filled plan shots."""

    def __init__(self, view, pool, rng, fps, group, template, aspect):
        self.v = view
        self.pool = pool
        self.rng = rng
        self.fps = fps
        self.group = group
        self.template = template
        self.style = STYLE.get(template, (template,))[0]
        self.aspect = aspect
        self.segs = []
        self.overlays = []
        self.beats = np.asarray(view['beats'], float)
        self.downs = np.asarray(view['downbeats'], float)
        self.energy = np.asarray(view['energy'], float)
        self.P = float(view['P'])

    # --- grid helpers
    def fr(self, t):
        return round(round(t * self.fps) / self.fps, 5)

    def e_at(self, t):
        if not len(self.beats):
            return 0.5
        j = int(np.clip(np.searchsorted(self.beats, t + 1e-6) - 1, 0, len(self.energy) - 1))
        return float(self.energy[j]) if len(self.energy) else 0.5

    def e_fwd(self, t, n=2):
        """Energy for a cut decision: the beat itself or the next `n` (a drop's first beat reads low)."""
        if not len(self.beats):
            return 0.5
        j = int(np.clip(np.searchsorted(self.beats, t + 1e-6) - 1, 0, len(self.energy) - 1))
        ahead = self.energy[j:j + 1 + n]
        return float(max(self.energy[j], ahead.mean())) if len(ahead) else 0.5

    def is_down(self, t, tol=0.03):
        return bool(len(self.downs) and np.min(np.abs(self.downs - t)) < tol)

    def bar_index(self, t):
        return int(np.searchsorted(self.downs, t + 0.03) - 1)

    def lead(self, name):
        """Seconds before its beat that a shot's first frame shows (the engine's styles.py table when present)."""
        if name in (None, 'none'):
            return 0.0
        if ST is not None:
            try:
                return float(ST.lead_s(self.style, name))
            except Exception:
                pass
        tbl = LEAD_MS.get(self.group) or LEAD_MS['general']
        return tbl.get(name.split('+')[0], tbl.get('default', -60)) / 1000.0

    def canon(self, tr, tp):
        """The engine's transition name: blur-bridge variants, '+bleach' on K-pop hero cuts."""
        tp = tp or {}
        if self.template == 'flow_blur' and tr in ('blur_bridge', 'flash_white') and getattr(self, 'flow_variant', None) != 's2rz':
            # (the s2rz variant keeps the plain bridge / flash: S2rz's cuts carry no ZkWQ push-defocus-bloom, remake round 2)
            # blur_flow = ZkWQloS4jtM's own bridge / flash (remake 2026-09-28: push + defocus + bloom into the switch, the
            # incoming tilted / zoomed settling over ~10 frames; transitions.BlurBridgeZkw / FlashWhiteZkw)
            return tr + '_zkw' if tr == 'flash_white' else 'blur_bridge_zkw'
        if tr == 'blur_bridge':
            if tp.get('flash'):
                return 'blur_bridge_flash'
            if tp.get('soft'):
                return 'blur_bridge_soft'
            if self.template == 'hype_typography':
                return 'blur_bridge_hype'  # 4-5 frame zoom-blur bridge (i4f, m3lv, zawB)
        if tp.get('exposure_bleach') and '+' not in tr:
            return tr + '+bleach'
        return tr

    def section_label(self, t):
        for s in self.v['sections']:
            if s['start'] - 1e-6 <= t < s['end']:
                return s['label']
        return 'verse'

    # --- segments
    def add(self, beat, transition='none', **kw):
        """
        A new shot whose first frame is `lead` before `beat` (the first shot of the video starts at 0).
        Returns the segment, or None when it would start within 2 frames of the previous one.
        """
        lead = kw.pop('lead', None)
        first = kw.pop('first', 'none')
        transition = self.canon(transition, kw.get('tparams'))
        if not self.segs:
            t0, tr = 0.0, first
        else:
            t0 = self.fr(beat + (self.lead(transition) if lead is None else lead))
            tr = transition
        seg = dict(beat=round(float(beat), 4), t0=t0, transition_in=tr,
                   role=kw.pop('role', 'any'), speed=kw.pop('speed', {'kind': 'normal'}), layout=kw.pop('layout', 'fill'),
                   camera=kw.pop('camera', None), tparams=kw.pop('tparams', None))
        seg.update({k: v for k, v in kw.items() if v is not None})
        if self.segs and t0 <= self.segs[-1]['t0'] + 1.5 / self.fps:
            return None  # would be an empty shot
        self.segs.append(seg)
        return seg

    def close(self, duration):
        end = self.fr(duration)
        # only trailing segments can be empty (indices recorded by the planners stay valid)
        while len(self.segs) > 1 and self.segs[-1]['t0'] >= end - 1.5 / self.fps:
            self.segs.pop()
        for i, s in enumerate(self.segs):
            s['t1'] = self.segs[i + 1]['t0'] if i + 1 < len(self.segs) else end

    # --- rates
    def rate_of(self, seg):
        sp = seg['speed']
        k = sp.get('kind', 'normal')
        t0, t1 = seg['t0'], seg['t1']
        if k == 'slowmo':
            return rate_constant(sp.get('rate', 0.5))
        if k == 'velocity_u':
            return rate_u_curve(t0, t1, sp.get('lo', 0.55), sp.get('hi_in', 1.6), sp.get('hi_out', 1.0))
        if k == 'velocity_kpop':
            return rate_kpop(t0, t1, sp.get('lo', 0.45), sp.get('hi', 1.2))
        if k == 'character':
            return rate_character(t0, t1, sp.get('shape', 'impact'), sp.get('base', 0.65))
        if k == 'freeze':
            return rate_constant(0.0)
        if k == 'ramp':
            a, b = sp.get('from', 1.0), sp.get('to', 0.5)
            d = max(1e-3, t1 - t0)
            return lambda t: a + (b - a) * smoothstep((np.asarray(t, float) - t0) / d)
        return rate_constant(sp.get('rate', 1.0))

    def label_of(self, seg):
        k = seg['speed'].get('kind', 'normal')
        return {'slowmo': 'slowmo', 'velocity_u': 'velocity', 'velocity_kpop': 'velocity', 'character': 'velocity',
                'burst_land': 'velocity', 'smooth_wave': 'velocity', 'freeze': 'freeze', 'ramp': 'ramp',
                'synced': 'synced'}.get(k, 'normal')

    # --- dance: per-beat warp so every move lands in its burst (the new pose held on the kick)
    def dance_curve(self, seg, shot, src_in, A=1.19, kind='burst_land', keep=0.0):
        """Per-beat source advance (burst_land: warped so a move lands in each burst); `keep` s stay free at the clip end."""
        t0, t1 = seg['t0'], seg['t1']
        fps = self.fps
        beats = self.beats
        P = self.P
        hits = sorted(h for h in (shot.get('hits') or []) if h > src_in)
        D = shot['duration'] - 0.03 - keep
        # beat boundaries inside the shot (the part before the first beat plays at scale 1)
        inner = [b for b in beats if t0 + 1e-6 < b < t1 - 1e-6]
        bounds = [t0] + inner + [t1]
        n = max(2, int(round((t1 - t0) * fps * 4)))
        ts = np.linspace(t0, t1, n + 1)
        idx = np.clip(np.searchsorted(beats, ts + 1e-9) - 1, 0, len(beats) - 2)
        b0 = beats[idx]
        b1 = beats[idx + 1]
        phi = np.clip((ts - b0) / np.maximum(1e-6, b1 - b0), 0, 1)
        g = burst_g(phi) / _BURST_MEAN if kind == 'burst_land' else wave_g(phi) / 1.2
        scale = np.ones(len(ts))
        c = src_in
        warped = 0
        for j in range(len(bounds) - 1):
            a, b = bounds[j], bounds[j + 1]
            m = (ts >= a - 1e-9) & (ts <= b + 1e-9)
            Pk = (b1[m][0] - b0[m][0]) if m.any() else P
            full = abs((b - a) - Pk) < 0.02 and j > 0
            nat = A * (b - a) if full else (A * float(np.sum((g[m][1:] + g[m][:-1]) / 2 * np.diff(ts[m]))) if m.sum() > 1 else A * (b - a))
            s = 1.0
            if full and kind == 'burst_land' and hits:
                target = c + F85 * nat
                near = [h for h in hits if c + F85 * 0.7 * nat <= h <= c + F85 * 1.5 * nat]
                if near:
                    h = min(near, key=lambda x: abs(x - target))
                    s = float(np.clip((h - c) / (F85 * nat), 0.7, 1.5))
                    warped += 1
            if c + s * nat > D:
                s = max(0.0, (D - c) / max(1e-6, nat))
            scale[m] = s
            c += s * nat
        r = A * g * scale
        off = np.concatenate([[0.0], np.cumsum((r[1:] + r[:-1]) / 2 * np.diff(ts))])
        off = np.minimum(off, max(0.0, D - src_in))
        return ts, off, warped

    def dance_src_in(self, seg, shot, need, A=1.19):
        """Start so the first full beat's burst lands on a move."""
        t0 = seg['t0']
        first = next((b for b in self.beats if b > t0 + 1e-6), t0)
        pre = A * (first - t0) * 0.6  # the partial beat before the kick (slow part of the burst curve)
        hits = shot.get('hits') or []
        room = shot['duration'] - need - 0.03
        best = None
        for h in hits:
            s = h - pre - F85 * A * self.P
            if 0 <= s <= room:
                ok = all(not (a - 0.05 < s + need and s < b - 0.05) for a, b in shot['_used'])
                if ok and (best is None or s < best):
                    best = s
        return best

    # --- filling
    def fill(self, prev=None):
        """Pick a clip + start + speed curve for every segment."""
        prev_shot = prev
        for i, seg in enumerate(self.segs):
            if seg.get('hidden'):
                continue
            dur = seg['t1'] - seg['t0']
            sp = seg['speed']
            if seg.get('copy_of') is not None:
                src = self.segs[seg['copy_of']]
                # a replayed shot, unless it would sit right after the same clip (that reads as no cut)
                if src.get('shot') is not None and src['shot'] is not prev_shot:
                    seg['shot'] = src['shot']
                    seg['src_in'] = src['src_in']
                    rate = self.rate_of(seg)
                    ts, off = integrate(rate, seg['t0'], seg['t1'], self.fps)
                    off = np.minimum(off, max(0.0, seg['shot']['duration'] - 0.03 - seg['src_in']))
                    seg['speed_curve'] = curve_points(ts, off, seg['src_in'], self.fps)
                    seg['speed_label'] = self.label_of(seg)
                    self.pool.mark(seg['shot'], seg['src_in'], seg['src_in'] + float(off[-1]), i)
                    prev_shot = seg['shot']
                    continue
            if seg.get('same_as') is not None:
                # the same take continues (a layout change, a slow-motion tail): no visible cut
                src = self.segs[seg['same_as']]
                shot = src['shot']
                start = float(src['speed_curve'][-1][1]) if src.get('speed_curve') else src['src_in'] + (src['t1'] - src['t0'])
                seg['shot'], seg['src_in'] = shot, start
                if sp.get('kind') in ('burst_land', 'smooth_wave'):
                    ts, off, _ = self.dance_curve(seg, shot, start, sp.get('A', 1.19), sp['kind'])
                else:
                    ts, off = integrate(self.rate_of(seg), seg['t0'], seg['t1'], self.fps)
                    room = max(0.0, shot['duration'] - 0.03 - start)
                    if off[-1] > room:  # ran out of take: slow it down to fit
                        off = off * (room / max(1e-6, off[-1]))
                seg['speed_curve'] = curve_points(ts, off, start, self.fps)
                seg['speed_label'] = self.label_of(seg)
                self.pool.mark(shot, start, start + float(off[-1]), i)
                continue
            if sp.get('kind') in ('burst_land', 'smooth_wave'):
                A = sp.get('A', 1.19)
                tail = float(seg.get('tail_need') or 0)  # a slow-motion / freeze tail of the same take follows
                need = A * dur * 1.05 + 0.05 + tail
                shot, st = self.pool.take(seg['role'], need, i, prev_shot, want=None, gap=seg.get('gap', 4),
                                          exclude=seg.get('exclude', ()))
                if shot is None:
                    # no clip is long enough: the longest fresh one, the dance slowed so the take (and its tail) fits
                    shot = self.pool.longest(prev=prev_shot, k=i)
                    if shot is None:
                        raise RuntimeError('no shots in the pool')
                    A = float(np.clip((shot['duration'] - 0.1 - tail) / max(0.1, dur * 1.05), 0.55, A))
                    st = 0.0
                if not shot.get('hits'):
                    shot['hits'] = motion_hits(shot['path'])
                better = self.dance_src_in(seg, shot, A * dur * 1.05 + tail, A)
                if better is not None:
                    st = better
                ts, off, warped = self.dance_curve(seg, shot, st, A, sp['kind'], keep=tail)
                seg['warped_beats'] = warped
            else:
                rate = self.rate_of(seg)
                ts, off = integrate(rate, seg['t0'], seg['t1'], self.fps)
                # a take that continues in later segments (same_as) needs its whole length free
                need = max(float(off[-1]) + 0.04, float(seg.get('need_total') or 0))
                align = seg.get('align', 'peak')
                ua = seg.get('align_u')
                if ua is None:
                    ua = {'velocity_u': min(0.35, max(0.0, (seg['beat'] + 0.04 - seg['t0']) / max(1e-3, dur))),
                          'velocity_kpop': 0.12, 'character': min(0.3, max(0.0, (seg['beat'] - seg['t0']) / max(1e-3, dur))),
                          'slowmo': 0.5}.get(sp.get('kind'), 0.3)
                ofs_a = float(np.interp(seg['t0'] + ua * dur, ts, off))

                def want(s, ofs_a=ofs_a, need=need, align=align):
                    if align == 'start':
                        return 0.0
                    pk = s.get('peak')
                    if pk is None:
                        return 0.15 * max(0.0, s['duration'] - need)
                    return float(pk) - ofs_a
                shot, st = self.pool.take(seg['role'], need, i, prev_shot, want=want, gap=seg.get('gap', 4),
                                          exclude=seg.get('exclude', ()), min_start=float(seg.get('min_src') or 0.0))
                if shot is None:
                    # nothing long enough: the longest fresh clip, slowed to fit
                    shot = self.pool.longest(prev=prev_shot, k=i)
                    if shot is None:
                        raise RuntimeError('no shots in the pool')
                    st = 0.0
                    room = max(0.1, shot['duration'] - 0.05)
                    off = off * min(1.0, room / max(1e-6, off[-1]))
            seg['shot'] = shot
            seg['src_in'] = round(float(st), 4)
            seg['speed_curve'] = curve_points(ts, off, st, self.fps)
            seg['speed_label'] = self.label_of(seg)
            self.pool.mark(shot, st, st + max(float(off[-1]), float(seg.get('need_total') or 0)), i)
            prev_shot = shot
        for i, seg in enumerate(self.segs):
            if not seg.get('hidden'):
                continue
            nxt = next((x for x in self.segs[i + 1:] if not x.get('hidden') and x.get('shot') is not None), None)
            if nxt is None:
                nxt = next((x for x in self.segs if x.get('shot') is not None), None)
            if nxt is None:
                raise RuntimeError('no shots in the plan')
            seg['shot'], seg['src_in'] = nxt['shot'], nxt['src_in']
            seg['speed'] = {'kind': 'freeze'}
            seg['speed_curve'] = [[0.0, round(float(nxt['src_in']), 4)], [round(seg['t1'] - seg['t0'], 5), round(float(nxt['src_in']), 4)]]
            seg['speed_label'] = 'freeze'
            seg['camera'] = dict(move='static', handheld=0.0, accents=[])

    def assign_panels(self):
        """
        Shot layouts that show more than one clip at once need their extra panels: layouts.Split needs
        shot["layout"] = "split2"/"split3" plus shot["panels"], and layouts.Hearts the same. No planner
        ever emitted either, which is why the reference split screen and the heart section could never
        render (the split_screen pool entry silently became a hard cut). Call after fill().
        """
        need_n = {'split2': 1, 'split3': 2, 'hearts': 2}
        for seg in self.segs:
            n = need_n.get(seg.get('layout'))
            if not n or seg.get('panels') or seg.get('shot') is None:
                continue
            mine = seg['shot']
            others = [s['shot'] for s in self.segs if s.get('shot') is not None and s['shot'] is not mine]
            fresh = [c for c in others if c.get('source_id') != mine.get('source_id')] or others
            seen, pick = {mine['path']}, []
            for c in fresh:
                if c['path'] in seen:
                    continue
                seen.add(c['path'])
                pick.append(c)
                if len(pick) >= n:
                    break
            if len(pick) < n:
                seg['layout'] = 'fill'      # not enough distinct clips: show one properly rather than fake it
                continue
            span = max(0.2, seg['t1'] - seg['t0'])
            seg['panels'] = [dict(path=c['path'],
                                  src_in=round(float(np.clip((c.get('peak') or c['duration'] * 0.3) - 0.4 * span,
                                                             0.0, max(0.0, c['duration'] - span - 0.05))), 4),
                                  rate=1.0) for c in pick]

    # --- output
    def shots_json(self):
        out = []
        for s in self.segs:
            sh = s['shot']
            curve = s.get('speed_curve')
            if s['speed_label'] == 'normal' and curve and abs(curve[-1][1] - curve[0][1] - (s['t1'] - s['t0'])) < 0.01:
                curve_out = None  # plain 1x
            else:
                curve_out = curve
            out.append(dict(
                path=sh['path'], t0=round(s['t0'], 5), t1=round(s['t1'], 5), src_in=s['src_in'],
                speed=s['speed_label'], speed_curve=curve_out, camera=s.get('camera'),
                transition_in=s['transition_in'], transition_params=s.get('tparams'), layout=s['layout'],
                sfx=None, beat=s['beat'], role=s['role'], source_id=sh.get('source_id'), source_title=sh.get('source_title'),
                source_channel=sh.get('source_channel'), content_box=sh.get('content_box'), tags=sorted(sh['_tags']),
                clip_duration=round(sh['duration'], 3),
                **({'panels': s['panels']} if s.get('panels') else {}),
                **({'postcard': s['postcard']} if s.get('postcard') else {}),
                **({'warped_beats': s['warped_beats']} if 'warped_beats' in s else {}),
                **({'repeat_of': s['copy_of']} if s.get('copy_of') is not None else {}),
                **({'continues': True} if s.get('same_as') is not None else {}),
                **({'bars': s['bars']} if s.get('bars') else {}),
            ))
        return out


# ---------------------------------------------------------------- cameras (dicts for the engine)

_CAMERA_BANK = {}


def camera_bank(preset):
    """The references' real per-shot camera paths (core/edit/refs/camera_bank.py -> camera_bank.json), or []."""
    if preset not in _CAMERA_BANK:
        rows = []
        try:
            p = os.path.join(HERE, 'refs', 'camera_bank.json')
            rows = json.load(open(p, encoding='utf-8')).get(preset) or []
        except Exception:  # noqa: BLE001
            rows = []
        _CAMERA_BANK[preset] = rows
    return _CAMERA_BANK[preset]


def bank_camera(rng, rows, dur, energy=0.7, prev=None):
    """
    Copy a reference shot's camera path onto a planned shot (blur_flow: the owner's "just copy the reference").
    Picks a bank row of similar length whose flow energy matches the song energy (quiet -> the calmer half of the
    bank, loud -> the busier half), never the row used by the previous shot, and returns the engine camera dict:
    static move + "keys" (engine.py base_cam) with the zoom headroom the path needs (pull-outs and vertical pans on
    a cover-fitted clip). Time is stretched at most 1.35x when no row is long enough.
    """
    if not rows:
        return None
    mags = sorted(r['mag'] for r in rows)
    med = mags[len(mags) // 2]
    if energy < 0.4:
        pool = [r for r in rows if r['mag'] <= med]
    elif energy >= 0.7:
        pool = [r for r in rows if r['mag'] >= med]
    else:
        pool = rows
    fit = [r for r in pool if r['dur'] >= dur * 0.9] or [r for r in rows if r['dur'] >= dur * 0.75] or pool or rows
    fit = [r for r in fit if r is not prev] or fit
    # nearest durations first, then a random pick among the 6 closest so the paths vary
    fit = sorted(fit, key=lambda r: abs(r['dur'] - dur))[:6]
    r = rng.choice(fit)
    k = min(1.35, max(1.0, dur / max(0.1, r['dur'])))
    keys = [[round(u * k, 4), l, dx, dy, rot] for u, l, dx, dy, rot in r['keys']]
    arr = np.array(keys)
    zoom = math.exp(max(0.0, -float(arr[:, 1].min()))) * (1 + 1.25 * float(np.abs(arr[:, 3]).max())) * 1.01
    cam = dict(move='static', amount=0.0, zoom=round(zoom, 4), pan=[0.0, 0.0], roll=0.0, handheld=None, follow=False,
               keys=keys, bank=dict(ref=r['ref'], t0=r['t0'], mag=r['mag'], stretch=round(k, 3)))
    return cam, r


def cam_general(rng, template, i, t0, t1, beat, next_tr=None, energy=0.7):
    """general.json style.camera: light handheld on 70% of shots, push / pull / drift, entry settle / roll."""
    if template in ('cinematic_slowmo',):
        return dict(move='push_in', zoom=[1.0, 1.0 + rng.uniform(0.02, 0.05)], handheld=None, entry=None, exit=None, roll=[0, 0])
    if template == 'beat_collection_band':
        return dict(move='static', zoom=[1.0, 1.0], handheld=None, entry=None, exit=None)
    d = t1 - t0
    mv = wpick(rng, {'push_in': 0.35, 'pull_out': 0.35, 'drift': 0.3})
    rate = rng.uniform(0.05, 0.2)
    zoom = [1.0, math.exp(rate * d)] if mv == 'push_in' else ([math.exp(rate * d), 1.0] if mv == 'pull_out' else [1.04, 1.04])
    pan = [rng.choice([-1, 1]) * rng.uniform(0.04, 0.1), 0.0] if mv == 'drift' else [0.0, 0.0]
    cam = dict(move=mv, zoom=[round(z, 4) for z in zoom], pan=[round(p, 4) for p in pan], ease='out',
               handheld=dict(pan_rms=0.003, freq_hz=3.0, roll_rms_deg=0.18, zoom_rms=0.01) if rng.random() < 0.7 else None,
               entry=None, exit=None, roll=[0.0, 0.0])
    if rng.random() < 0.25:
        cam['entry'] = dict(type='punch_settle', scale0=round(float(np.clip(rng.gauss(1.136, 0.08), 1.066, 1.316)), 3), tau=0.06)
    if rng.random() < 0.35:
        cam['roll'] = [round(rng.choice([-1, 1]) * rng.uniform(1.0, 3.0), 2), 0.0]
        cam['roll_settle_s'] = 0.25
    if next_tr in ('zoom_through', 'whip_slide') or rng.random() < 0.15:
        cam['exit'] = dict(type='zoom_in', amount=0.13, over_s=0.1)
    return cam


def cam_character(rng, family, i, prev_dir, transition, shake=False):
    """character.json style.camera: continuous eased zoom per shot, alternating, roll, entry settle, drop shake."""
    soft = family == 'soft_romance'
    if family == 'raw_crisp':
        return dict(move='static', zoom=[1.0, 1.0], handheld=None, entry=None, exit=None), prev_dir
    lo, hi = (0.08, 0.3)
    amt = rng.uniform(lo, hi)
    if soft:
        d = -1 if rng.random() < 0.75 else 1
    else:
        d = -prev_dir if (prev_dir and rng.random() < 0.7) else rng.choice([-1, 1])
    z = math.exp(amt)
    zoom = [1.0, z] if d > 0 else [z, 1.0]
    entry_a = {'flash_zoom': 0.22, 'mb_cut': 0.13, 'impact_zoom_1frame': 0.15, 'zoom_through_color_field': 0.38,
               'whip_horizontal': 0.34}.get(transition)
    cam = dict(move='push_in' if d > 0 else 'pull_out', zoom=[round(x, 4) for x in zoom], ease='out',
               roll=[round(rng.choice([-1, 1]) * rng.uniform(1.0, 5.0), 2), 0.0],
               handheld=None if soft else dict(pan_rms=0.003, freq_hz=3.0, roll_rms_deg=0.15, zoom_rms=0.008),
               entry=dict(type='settle', ln_scale0=entry_a, smooth_s=0.27 if transition == 'flash_zoom' else 0.1) if entry_a else None,
               exit=dict(type='rush', ln_scale=round(rng.uniform(0.1, 0.18), 3), over_s=0.1, roll_deg=round(rng.uniform(1.5, 2.6), 2))
               if transition in ('mb_cut', 'flash_zoom') else None)
    if shake:
        cam['shake'] = dict(amp=0.015, hz=10.0, decay_s=0.35)
    return cam, d


def cam_kpop(rng, prereveal=False):
    """kpop.json style.camera: entry settle (70% punch-in, 30% pull-out), still middle, pre-cut pull-back."""
    pin = rng.random() < 0.7
    A = float(np.clip(rng.gauss(0.25, 0.09), 0.12, 0.45))
    return dict(move='entry_settle', entry=dict(type='punch_in' if pin else 'pull_out', amount=round(A, 3), tau=0.085,
                                                rot0=round(rng.choice([-1, 1]) * rng.uniform(0.5, 3.0), 2), rot_tau=0.06,
                                                shift0=round(rng.choice([-1, 1]) * rng.uniform(0.01, 0.06), 3), shift_tau=0.08),
                hold=dict(zoom_pct_per_s=round(rng.uniform(-3, 3), 2), rot_deg_per_s=round(rng.uniform(-0.5, 0.5), 2)),
                exit=dict(type='pullback', frames60=7, scale_total=-0.1, roll_deg=-1.0) if rng.random() < 0.6 else None,
                handheld=None)


def dance_accents(rng, beats, t0, t1, P, cute=True):
    """One punch per beat, attack at beat phase 0.5-0.7 with the burst, decayed before the kick (Uf_4)."""
    out, last_dir = [], 1
    for b in beats:
        if not (t0 - 1e-6 <= b < t1 - 0.3 * P):
            continue
        kind = wpick(rng, DANCE_ACCENTS)
        a = dict(t=round(b + rng.uniform(0.5, 0.7) * P, 4), type=kind)
        if kind == 'zoom_in_punch':
            a.update(scale0=round(rng.uniform(1.41, 1.61), 3), frames=5, blur_first_frames=2, centre='face')
        elif kind == 'zoom_out_punch':
            a.update(scale0=round(rng.uniform(0.905, 0.934), 3), frames=3, edges='mirror')
        elif kind == 'rotate_punch':
            a.update(deg0=round(rng.choice([-1, 1]) * rng.uniform(26, 35), 1), ty_fw=0.22, frames=5)
        elif kind == 'vslide_wrap':
            a.update(offset0_h=0.45, decay=0.45, frames=4, direction=last_dir)
            last_dir = -last_dir
        out.append(a)
    return out


# ---------------------------------------------------------------- song edit

def song_template(A, pool, rng, forced=None):
    if forced:
        return forced
    bpm = float(A['bpm'])
    perc = np.asarray(A.get('beat_perc_db') or [0], float)
    loud = np.asarray(A.get('beat_loud_db') or [0], float)
    drumsy = float(np.median(perc - loud)) if len(perc) == len(loud) else -6.0
    soft = drumsy < -9.0 or bpm < 80
    big_drop = any(d['jump_db'] >= 6 for d in A.get('drops') or [])
    w = {'flow_blur': 0.45, 'hype_typography': 0.2 * (1.5 if big_drop else 1.0),
         'cinematic_slowmo': 0.25 * (2.0 if soft else 0.5), 'beat_collection_band': 0.0, 'fx_heavy': 0.0}
    felt = bpm if 80 <= bpm <= 140 else (bpm / 2 if bpm > 140 else bpm * 2)
    if len(pool.sources()) >= 6 and 110 <= felt <= 136:
        w['beat_collection_band'] = 0.1
    # fx.json: the typography style wants a SLOW vocal track with spaced lyrics (80 BPM in the reference)
    # and only 4-5 slow wide plates; a dense-percussion track would fight it.
    if soft and 70 <= bpm <= 100 and len(pool) >= 4:
        w['fx_heavy'] = 0.12
    return wpick(rng, w)


def fx_words(opts, view, drop, D, P):
    """
    The word track for the fx templates. fx.json: the LYRIC is the clock, not the drums - a word becomes
    legible a median of 2.5 frames (83 ms) AFTER the vocal-stem onset of its syllable. Word timings come
    from opts['words'] ([{text, t0}] or [text, ...]) or from opts['vocal_onsets'] paired with the title /
    song words; with neither, the words land on downbeats, which is the honest fallback.
    """
    LAG = 2.5 / 30.0
    given = opts.get('words')
    if given and isinstance(given[0], dict):
        return [dict(text=latin_text(w.get('text'), 18), t0=round(float(w['t0']) + LAG, 4)) for w in given
                if latin_text(w.get('text'), 18) and 0 <= float(w['t0']) < D - 0.3]
    texts = [w for w in (given or [])] if given else []
    if not texts:
        src = ' '.join(x for x in (latin_text(opts.get('song'), 40), latin_text(opts.get('title'), 40)) if x)
        texts = [w for w in src.split() if len(w) >= 2][:10]
    if not texts:
        return []
    ons = [float(o) for o in (opts.get('vocal_onsets') or []) if drop - 0.2 <= float(o) < D - 0.5]
    if not ons:
        ons = [float(d) for d in view['downbeats'] if drop - 0.2 <= float(d) < D - 0.5]
    if not ons:
        ons = list(np.arange(max(0.3, drop), D - 0.5, max(0.45, 1.5 * P)))
    ons = ons[:len(texts)]
    return [dict(text=texts[i], t0=round(o + LAG, 4)) for i, o in enumerate(ons)]


def plan_fx_edit(A, shots, opts):
    """
    fx_heavy (core/edit/refs/fx.json, WGvtliE1298): the most effect-dense template in the set - 31
    catalogued effect instances in 16.8 s (1.9 per second, 5.7 per bar). The text is the subject and the
    footage is a backdrop, so:
      * shots hold 1.0-3.0 s (0.42 cuts/s) and every join sits OFF the percussion beat (phase 0.64 +- 0.12)
      * every join hides inside an extreme state: an exponential whip to white / black, a rack focus, a
        spin settle or a zoom settle - there is never a visible cut between two sharp frames
      * hero words fly THROUGH the lens at their own depths, locked to the vocal with the measured
        +2.5 frame lag, and never fade
      * the pushing shots carry a sustained radial blur, not a 2-frame burst
      * no RGB split, no strobe, no dissolve, no shake, no speed ramp, no freeze and NO added SFX
    Section ordering follows the reference: card intro -> plates with flying words -> a set piece (glass
    shatter, crack web, filmstrip, polaroids) -> the white set with letters flying in -> the closing
    extruded 3D word.
    """
    rng = random.Random(opts.get('seed'))
    pool = Pool(shots, rng)
    if len(pool) < 3:
        raise RuntimeError(f'only {len(pool)} usable shots')
    template = 'fx_heavy'
    style, group, grade = STYLE[template]
    fps = int(opts.get('fps') or 60)
    aspect = opts.get('aspect') or '9:16'
    L = float(opts.get('length') or 17.0)
    win = MU.choose_window(A, L, (1.6, 3.4), min_len=L * 0.75, max_len=L * 1.25, contrast=0.25, rng=rng)
    view = MU.window_view(A, win['start'], win['end'], 0.9, 1.2)
    B = Builder(view, pool, rng, fps, group, template, aspect)
    D = view['duration']
    P = B.P
    drop = max(0.0, win['drop'] - win['start'])

    # --- the ORDER of sections of the reference (frames @30 of WGvtliE1298, 16.8 s), scaled to D:
    #   0-87 card intro | rack focus -> plate | whip white -> plate + flying words | (more plates on longer
    #   songs) | spin settle -> GLASS set (filmstrip) | soft whip -> WHITE set (letters, then polaroids) |
    #   dolly out -> last plate + closing 3D words | 10-frame fade.
    # LEGIBILITY: a built set REPLACES the picture (it is never blended over footage), a plate carries at most
    # the flying words + a light radial smear, and every effect is followed by a clean readable hold.
    k = D / 16.8
    intro = float(np.clip(2.5 * k, 1.6, 2.8))
    glass_d, white_d = float(np.clip(1.3 * k, 1.1, 1.6)), float(np.clip(2.75 * k, 2.6, 3.4))
    tail_d = float(np.clip(2.7 * k, 2.0, 3.2))
    sets_at = D - tail_d - white_d - glass_d

    def join(target):
        """The join nearest `target`, placed OFF the beat (fx.json: beat phase 0.64 +- 0.12)."""
        beat = target
        if len(B.beats):
            beat = float(B.beats[int(np.argmin(np.abs(B.beats - (target - 0.64 * P))))])
        return beat, beat + float(rng.uniform(0.52, 0.76)) * P

    def cam_push():
        return dict(move='push_in' if rng.random() < 0.7 else 'drift', amount=round(float(rng.uniform(0.09, 0.20)), 3),
                    ease='linear', handheld=None, roll=0.0, accents=[])

    B.add(0.0, 'none', role='hero', speed={'kind': 'normal'}, layout='fill', first='fade_in',
          camera=dict(move='push_in', zoom=[1.0, 1.10], ease='linear', handheld=None), section='intro')
    marks = {}
    plan_joins = [(intro, 'rack_focus', 'plate')]
    t = intro + float(np.clip(1.0 * k, 1.0, 1.6))
    body_tr = ['whip_exp_white']
    while t < sets_at - 1.0:
        plan_joins.append((t, body_tr[-1], 'plate'))
        t += float(rng.uniform(1.6, 3.0))
        # never the soft whip here: it only reads when the INCOMING picture is white (the white set below)
        body_tr.append(wpick(rng, {'whip_exp_black': 0.34, 'zoom_settle': 0.22, 'rack_focus': 0.22, 'glass_shatter': 0.22},
                             exclude=(body_tr[-1],)))
    plan_joins += [(sets_at, 'spin_settle', 'glass'), (sets_at + glass_d, 'whip_exp_soft', 'white'),
                   (D - tail_d, 'dolly_out_white', 'plate_last')]
    for target, tr, kind in plan_joins:
        beat, at = join(target)
        seg = B.add(at, tr, lead=0.0, role='calm' if kind != 'plate' or rng.random() < 0.5 else 'build',
                    speed={'kind': 'normal'}, layout='fill', section=B.section_label(min(at, D - 0.1)), camera=cam_push())
        if seg is not None:
            seg['fx_kind'] = kind
            marks[kind] = seg
    B.close(D)
    B.fill()

    # ---------------------------------------------------------------- overlays (the effect density)
    ov = []
    first_plate = B.segs[1]['t0'] if len(B.segs) > 1 else intro
    line = latin_text(opts.get('subtitle') or opts.get('song') or opts.get('title'), 30)
    ov.append(dict(type='music_card3d', t0=0.0, t1=round(first_plate, 3), dur=round(first_plate, 3),
                   title=latin_text(opts.get('song') or opts.get('title'), 26), artist=latin_text(opts.get('artist'), 26),
                   text=(line or '').lower()))
    g0 = marks.get('glass', {}).get('t0')
    w0 = marks.get('white', {}).get('t0')
    w1 = marks.get('plate_last', {}).get('t0', D)
    busy = [(0.0, first_plate)] + ([(g0, w1)] if g0 is not None else [])
    # the hero words: per-word 3D depth, vocal-locked, never fading - on the PLATES only, never over a set
    words = [w for w in fx_words(opts, view, first_plate, D, P)
             if not any(a - 0.35 <= float(w['t0']) < b + 0.15 for a, b in busy) and float(w['t0']) < D - 1.6]
    if words:
        ov.append(dict(type='word3d', t0=round(max(0.0, words[0]['t0'] - 0.2), 3), t1=round(D, 3), words=words,
                       style='hero_fx', max_alive=3, vertical=(aspect == '9:16')))
    # the sustained radial smear on the pushing plates: 0.9 x the per-frame zoom rate, ~1 %W at the frame edge,
    # centre sharp (acceptance: the smeared frame keeps >= 55 % of the Laplacian variance of the plate)
    for s in B.segs:
        c = s.get('camera') or {}
        if s.get('fx_kind') in ('plate', 'plate_last') and c.get('move') == 'push_in' and s['t1'] - s['t0'] > 0.8:
            rate = float(c.get('amount') or 0.12) / max(0.5, s['t1'] - s['t0']) / 30.0
            # round 3: ramps 0.04 -> 0.10 over the shot (edge smear 3 -> 8 %), sharp core 0.18, centred on the subject
            ov.append(dict(type='radial_blur', t0=round(s['t0'] + 0.3, 3), t1=round(s['t1'], 3),
                           amount=0.04, amount_end=0.10, steps=16, core=0.18,
                           center=[0.5, 0.45]))
    # one word standing in the ground plane - only on a WIDE plate (a close-up has no ground to stand on)
    wide = [s for s in B.segs if s.get('fx_kind') == 'plate' and str(s.get('scale') or '').startswith('wide')
            and s['t1'] - s['t0'] >= 1.2]
    if wide and words:
        s = wide[0]
        ov.append(dict(type='ground_text', t0=round(s['t0'] + 0.35, 3), t1=round(s['t1'] - 0.1, 3),
                       text=words[min(2, len(words) - 1)]['text'], style='hero_fx'))
    if g0 is not None and w0 is not None:
        ov.append(dict(type='glass_set', t0=round(g0, 3), t1=round(w0, 3), x=0.5, y=0.45))
        ov.append(dict(type='white_set', t0=round(w0, 3), t1=round(w1, 3)))
        setw = max([x for x in latin_text(opts.get('title'), 40).split() if len(x) <= 9] or [''], key=len)
        mid = w0 + (w1 - w0) * 0.52 if setw else w0
        if setw:
            ov.append(dict(type='letters_fly', t0=round(w0 + 2 / 30.0, 3), t1=round(mid, 3), text=setw.upper(), style='set_fx',
                           cap=0.25 if aspect == '16:9' else 0.11, gradient=[[45, 45, 50], [150, 120, 70]], y=0.48,
                           fade_out=True))
        ov.append(dict(type='polaroids', t0=round(mid, 3), t1=round(w1, 3), n=3, stagger=5 / 30.0))
        # a built set hides the join of the shot under it, so the measured joins are applied to the SET picture
        ov.append(dict(type='set_entry', t0=round(g0, 3), t1=round(min(w0, g0 + 0.6), 3), kind='spin'))
        ov.append(dict(type='set_exit', t0=round(w0 - 10 / 30.0, 3), t1=round(w0, 3), kind='whip_white'))
        ov.append(dict(type='set_exit', t0=round(w1 - 14 / 30.0, 3), t1=round(w1, 3), kind='dolly_white'))
    # the closing extruded 3D words, assembled and then HELD >= 20 frames, and the 10-frame linear fade
    close = latin_text(opts.get('watermark') or opts.get('title'), 14).lstrip('@').upper()
    if close and D > 5.0:
        parts = close.split()
        text = close if len(parts) < 2 else ' '.join(parts[:len(parts) // 2]) + ' / ' + ' '.join(parts[len(parts) // 2:])
        ov.append(dict(type='closing_3d', t0=round(max(w1 + 0.5, D - 1.9), 3), t1=round(D, 3), text=text,
                       cap=0.236 if aspect == '16:9' else 0.085, y=0.5 if aspect == '16:9' else 0.72))
    ov.append(dict(type='fade_out', t0=round(D - 10 / 30.0, 3), t1=round(D, 3), frames=10))
    if aspect == '16:9':
        ov.append(dict(type='letterbox', t0=0.0, t1=round(D, 3), frac=0.05))   # the constant 2:1 picture
    return finish(B, A, win, view, opts, template, style, group, grade, ov, aspect,
                  audio=dict(fade_in=0.0, fade_out=0.8), extra=dict(intro='music_card', end='fade_to_black',
                                                                    drop=round(drop, 4), words=len(words)),
                  show_title=False)


# ---------------------------------------------------------------- cinematic slow-mo (remake of _sX2EUexbW8, round 1)
# core/edit/refs/remake-cinematic_slowmo.md. What the owner's favourite does, measured frame by frame and copied:
#   * words on black for one sung line (lyric_text rule 'cinematic_slowmo': one word per beat, streak-in, shrink)
#   * the body starts on the downbeat after the song's own breath (the chorus / drop start)
#   * cuts on a ~1 s pulse (3 eighths of a 6/8 bar, 1 beat of a 66 BPM ballad), 50-95 ms before the beat; the body is
#     [4 pulses hero] [1] [1] [1] [1] ... [2 pulses + 1.2 s fade to black]
#   * every moving shot FALLS INTO SLOW MOTION: whole-frame motion energy (median flow, % of the width per frame)
#     starts at 1.0-1.7 and decays to 0.05-0.25 with tau 0.1-0.33 s; still shots sit at 0.02-0.04
#     (fits per shot: hero hi 1.27 lo .055 tau .33 | move hi 1.5 lo .08 tau .12 | still hi .03 lo 0 | move hi 1.73
#      lo .24 tau .10 | still hi .23 lo .03 tau .075 | last hi 1.12 lo .25 tau .10; x the render-feedback gains)
#   * no camera move, no handheld; hard cuts with a 1-2 frame softening; the first body shot enters from a defocus
#     with a 1.10 -> 1.0 scale settle; the 16:9 master rotated into 9:16; soft golden grade (styles 'golden_soft')
#   * music only: the song slowed to 0.95 (make_version, main), fading out in ~1.1 s (-75 dB) at the end
CINE_TARGET = {'hero': (1.35, 0.052, 0.33), 'move': (1.30, (0.07, 0.23), 0.11), 'still': (0.14, 0.026, 0.10),
               'last': (1.02, 0.12, 0.10)}
CINE_HARD = {'talking', 'lipflap', 'gore', 'fierce', 'phone', 'soft', 'stepped', 'ghost', 'roll', 'twoshot', 'gag',
             'male_lead', 'panels', 'group', 'eye_macro', 'wide'}      # 2026-10-12: never on screen in a vibe edit
CINE_ALLOW_GORE = True    # owner 2026-10-12: blood / gore allowed in the slow-mo vibe edits (the 'fierce' mood gate stays)
CINE_VMAX, CINE_VMIN = 8.0, 0.20     # round 10: floor 0.12 -> 0.20 (critic: shots freeze, 43 % near-still frames)
CINE_MONOTONE = True


def clip_flow(path, fps=30, w=270, max_s=12.0):
    """(times, whole-frame motion energy) of a clip at native speed: median DIS flow in % of the clip width per 1/30 s
    (the metric of the remake loop's motion_energy tool). Cached next to the clip."""
    cache = path + '.flow30.json'
    try:
        if os.path.exists(cache) and os.path.getmtime(cache) >= os.path.getmtime(path):
            d = json.load(open(cache, encoding='utf-8'))
            return np.asarray(d['t'], float), np.asarray(d['e'], float)
    except Exception:  # noqa: BLE001
        pass
    import cv2
    cap = cv2.VideoCapture(path)
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    step = max(1, int(round(src_fps / fps)))
    dis = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_FAST)
    prev, ts, es, i = None, [], [], 0
    while True:
        ok, f = cap.read()
        if not ok or i / src_fps > max_s:
            break
        if i % step == 0:
            g = cv2.cvtColor(cv2.resize(f, (w, max(8, int(f.shape[0] * w / f.shape[1])))), cv2.COLOR_BGR2GRAY)
            if prev is not None:
                fl = dis.calc(prev, g, None)
                es.append(float(np.median(np.linalg.norm(fl, axis=2))) / w * 100 * (30.0 / (src_fps / step)))
                ts.append(i / src_fps)
            prev = g
        i += 1
    if not es:
        ts, es = [0.0, 1.0], [0.1, 0.1]
    try:
        json.dump(dict(t=[round(x, 4) for x in ts], e=[round(x, 5) for x in es]), open(cache, 'w', encoding='utf-8'))
    except OSError:
        pass
    return np.asarray(ts, float), np.asarray(es, float)


def cine_speed_curve(ct, ce, src_in, dur, target, room, out_gain, fps, entry_vmax=None):
    """Clip-time curve that makes the rendered motion energy follow target = (hi, lo, tau): speed = E_target /
    (out_gain x E_clip(clip time)), clamped; slowed uniformly when the take is too short. -> ([[u, clip_t]], scale)."""
    from scipy.ndimage import median_filter
    ce = np.maximum(median_filter(ce, 7, mode='nearest') if len(ce) > 7 else ce, 0.03)
    hi, lo, tau = target
    scale = 1.0
    pts = []
    for _ in range(40):
        c, pts, vprev = src_in, [], None
        for u in np.arange(0.0, dur + 0.5 / fps, 1.0 / fps):
            pts.append([round(float(u), 5), round(float(c), 5)])
            et = scale * (lo + (hi - lo) * math.exp(-u / tau))
            v = float(np.clip(et / (out_gain * float(np.interp(c, ct, ce))), CINE_VMIN * scale, CINE_VMAX))
            if CINE_MONOTONE and vprev is not None and u > 0.25:
                # round 10 (critic: Kaguya runs 0.02 -> 0.18, speeding up at the end because the energy floor divides by a
                # native motion that decays; _sX2's shots fall fast -> slow and never speed up again): after the entry
                # the clip speed never rises
                v = min(v, vprev * 1.01)
            vprev = v
            c += v / fps
        if entry_vmax or CINE_ENTRY_VMAX:
            pts = _cine_smooth_entry(pts, fps, entry_vmax or CINE_ENTRY_VMAX)
        if pts[-1][1] <= src_in + room:
            return pts, scale
        scale *= 0.9
    last = pts[-1][1]
    k = room / max(1e-6, last - src_in)
    return [[u, round(src_in + (c - src_in) * k, 5)] for u, c in pts], scale


CINE_ENTRY_VMAX = 4.0


def _cine_smooth_entry(pts, fps, vmax):
    """Remake round 8 (critics: the fast-into-slow entry ran 8x, 8x then 2.2x source speed - the first frames popped,
    flow 0.35 -> 0.74 -> 0.37, and the shutter smear switched off in steps): the entry speed is capped at vmax and the
    excess over the settled speed decays as one exponential, v(u) = v_set(u) + A e^(-u/tv), keeping the clip distance
    the raw curve covered in its first 0.5 s (so the shot still lands where the energy target put it). Monotone."""
    if len(pts) < 4:
        return pts
    u = np.array([p[0] for p in pts], float)
    c = np.array([p[1] for p in pts], float)
    v = np.diff(c) * fps                                   # speed over each output frame
    if not len(v) or v[0] <= vmax * 1.001 and np.all(np.diff(v[:6]) <= 1e-6):
        return pts
    n_e = min(len(v), max(4, int(round(0.5 * fps))))
    v_set = float(np.median(v[n_e:n_e + max(3, int(0.3 * fps))])) if len(v) > n_e + 2 else float(v[-1])
    v_set = min(v_set, float(np.min(v[:n_e])))
    excess = float(np.sum(np.maximum(0.0, v[:n_e] - v_set))) / fps       # clip seconds covered above v_set
    A = max(0.0, min(vmax, float(v[0])) - v_set)
    if A <= 1e-6:
        return pts
    uu = u[:-1]
    tv = min(0.5, max(1.5 / fps, excess / A))
    nv = v.copy()
    for i in range(n_e):
        nv[i] = v_set + A * math.exp(-uu[i] / tv)
    # hand over to the raw curve after the entry without a step: blend over 4 frames
    for i in range(n_e, min(len(v), n_e + 4)):
        w = (i - n_e + 1) / 5.0
        nv[i] = (1 - w) * (v_set + A * math.exp(-uu[i] / tv)) + w * v[i]
    cc = np.concatenate([[c[0]], c[0] + np.cumsum(nv) / fps])
    return [[round(float(a), 5), round(float(b), 5)] for a, b in zip(u, cc)]


def cine_hero_keys(T, fps, sign=-1.0, pan=0.054, ln_end=0.055, ln_drift=0.035, ln_kick=0.02, tau=0.35, ease_frames=3):
    """Remake round 6 (critic: our hero entry overshoots and settles back - tx 0.7 -> 5.1 -> 2.9 %W, ln scale -0.099
    then back to -0.043 - where _sX2's falls monotonically: tx -0.6 -> -2.7 (0.25 s) -> -4.5 (0.85 s) -> -5.4 %W,
    ln scale 0 -> -0.037, decelerating, never reversing). A copied camera path for the hero shot: one decelerating
    lateral drift (70 % on an exponential with tau, 30 % linear) and a monotone slow pull-out, no spring, no push.
    -> engine camera keys [[u_s, ln_scale, dx, dy, roll], ...] (dx in canvas widths)"""
    keys = []
    T = max(0.5, float(T))
    n = int(round(T * fps)) + 1
    # round 10 (critic: the hero slams in at its maximum speed on the first frame - remake 0.80, new 0.46-0.55 %W -
    # where _sX2's starts from rest: 0.01, 0.11, 0.21, 0.34 at frame 3, then decays): the camera velocity is ramped with a
    # smoothstep over the first `ease_frames` frames (the displacement is renormalised so the path still ends where it did)
    us = np.arange(n) / float(fps)
    w_raw = 0.7 * (1 - np.exp(-us / tau)) / (1 - math.exp(-T / tau)) + 0.3 * us / T
    if ease_frames and n > ease_frames + 2:
        v = np.diff(w_raw, prepend=0.0)
        r = np.clip(np.arange(n) / float(ease_frames), 0.0, 1.0)
        v = v * (r * r * (3 - 2 * r))
        ws = np.cumsum(v)
        ws = ws / ws[-1] * w_raw[-1]
    else:
        ws = w_raw
    for i in range(n):
        u = float(us[i])
        ln = ln_end + ln_kick * math.exp(-u / tau) + ln_drift * (1 - u / T)
        keys.append([round(u, 4), round(ln, 5), round(sign * pan * (0.5 - float(ws[i])), 5), 0.0, 0.0])
    return keys


def cine_window(A, pulse, n_pulses, opts, rng):
    """(start, body start d, end) in song seconds: d = the beat after the song's own breath at a chorus / drop start,
    the intro = the sung line before it (4.2-6.4 s, starting on its first word when lyrics are available)."""
    beats, E = np.asarray(A.get('beats') or [], float), np.asarray(A.get('beat_energy') or [], float)
    V = np.asarray(A.get('beat_voc') or [0.0] * len(beats), float)
    dur = float(A['duration'])
    body = n_pulses * pulse
    cands = []
    for s in A.get('sections') or []:
        if s['label'] in ('chorus', 'drop', 'build') and 6.5 <= s['start'] <= dur - body - 0.5:
            cands.append((float(s['start']), {'chorus': 1.0, 'drop': 1.0, 'build': 0.5}[s['label']]))
    for dd in A.get('drops') or []:
        if 6.5 <= float(dd['t']) <= dur - body - 0.5:
            cands.append((float(dd['t']), 0.8))
    if not cands or body > 20.0:
        # 2026-10-12: a 30-180 s body may start on any 8th beat too (the chorus starts alone left a long edit no window
        # that avoids the song's quiet outro)
        cands += [(float(b), 0.0) for b in beats[(beats > 7) & (beats < dur - body - 0.5)][::8]]
        cands = cands or [(min(7.0, dur / 3), 0.0)]
    # 2026-10-18 (slow-mo music pool, core/edit/slowmo_music.py): the pool's analysed hook start (slowed-song seconds) is a
    # strong candidate for the hero downbeat; the breath / vocal-gap logic below still picks the exact beat
    hero_hint = opts.get('cine_hero')
    if hero_hint is not None and 6.5 <= float(hero_hint) <= dur - body - 0.5:
        cands.append((float(hero_hint), 1.0))
    LD = np.asarray(A.get('beat_loud_db') or [], float)
    ld_med = float(np.median(LD)) if len(LD) else 0.0
    best = None
    scored = []
    for t, bonus in cands:
        # the breath: the lowest-energy beat within 0.8 s before the section start; the body starts on the next beat
        near = np.where((beats >= t - 0.8) & (beats <= t + 0.4))[0]
        if not len(near) or not len(E):
            j = int(np.argmin(np.abs(beats - t))) if len(beats) else 0
            d, dip = (float(beats[j]) if len(beats) else t), 0.0
        else:
            k = int(near[int(np.argmin(E[near]))])
            d = float(beats[min(len(beats) - 1, k + 1)])
            dip = float(max(0.0, np.median(E[max(0, k - 6):k]) - E[k])) if k > 0 else 0.0
        iv = (beats >= d - 5.6) & (beats < d - 0.4)
        voc = float(np.mean(V[iv] > 0.0)) if iv.any() and len(V) == len(beats) else 0.0
        mb = (beats >= d) & (beats < d + body)
        eb = float(E[mb].mean()) if len(E) == len(beats) and mb.any() else 0.5
        sc = bonus + 1.5 * dip + 0.8 * voc + 0.5 * eb + rng.uniform(0, 0.05)
        if hero_hint is not None:
            sc += 1.0 * max(0.0, 1.0 - abs(t - float(hero_hint)) / 4.0)     # a hint: the vocal-gap search below still decides
        if body > 20.0 and len(LD) == len(beats) and mb.any():
            # never a long stretch of the song's breakdown / outro under the picture (a 14 s near-silence reached a test)
            sc -= 6.0 * float(np.mean(LD[mb] < ld_med - 12.0))
        # remake round 6: a body downbeat after the song's own breath + riser (as _sX2's) is the hook to take
        if A.get('path') and A.get('tracker') != 'synthetic' and os.path.exists(str(A['path']))                 and _cine_breath_riser(A['path'], d - 0.06):
            sc += 1.5
        scored.append((sc, d))
        if best is None or sc > best[0]:
            best = (sc, d)
    d = best[1]
    start = d - float(opts.get('cine_intro_s') or 5.5)
    words = None
    can_words = bool(opts.get('song') and A.get('tracker') != 'synthetic' and not opts.get('offline'))

    def _words_for(dd):
        try:
            import lyrics as LY
            return LY.words(opts['song'], opts.get('artist') or '', A['path'], t0=max(0.0, dd - 7.0), t1=dd, threads=2)
        except Exception as e:  # noqa: BLE001
            log(f'cinematic: no lyric timing for the intro ({e})')
            return None

    def _intro_stats(ws, dd):
        """(vocal gap before dd in s, sung words started in the 5.6 s before it)"""
        if not ws:
            return None, 0
        te = [float(w['t_end']) for w in ws if float(w['t_start']) < dd]
        gap = dd - max(te) if te else 9.0
        dens = sum(1 for w in ws if dd - 5.6 <= float(w['t_start']) < dd - 0.3)
        return gap, dens
    gap_info = {}
    if can_words:
        # round 10 (critics, BLOCKING: no gap before the hero, a hook of 5 words in 6.5 s): among the best few
        # windows take one whose intro ends in a real vocal-free gap (>= 0.5 s, so the song's gap can be built without
        # chopping a sung word) and carries a dense line (>= 7 sung words in the last 5.6 s)
        tried = []
        for sc0, d0 in sorted(set(scored), key=lambda z: -z[0])[:int(opts.get('cine_gap_tries') or os.environ.get('CINE_GAP_TRIES') or 4)]:
            try:
                import lyrics as LY
                wsr = LY.words(opts['song'], opts.get('artist') or '', A['path'], t0=max(0.0, d0 - 11.5), t1=d0 + 3.0, threads=2)
            except Exception as e:  # noqa: BLE001
                log(f'cinematic: no lyric timing around {d0:.1f} ({e})')
                continue
            if not wsr:
                continue
            ts_ = np.array([float(w['t_start']) for w in wsr])
            # every tracked beat within a few seconds of the section start is a candidate hero downbeat: the vocal must be
            # silent for >= 0.5 s before it and pick up again on / after it (a sung word never straddles the cut)
            for bi in np.where((beats >= d0 - 4.0) & (beats <= d0 + 2.5))[0]:
                b_ = float(beats[bi])
                g0, n0 = _intro_stats(wsr, b_)
                if g0 is None:
                    continue
                nxt = ts_[ts_ >= b_ - 0.3]
                pick_up = bool(len(nxt) and nxt[0] - b_ <= 1.2)
                e_b = float(E[bi]) if len(E) == len(beats) else 0.5
                tried.append((sc0 - 0.3 * abs(b_ - d0) + (1.0 if g0 >= 0.5 else 0.0)
                              + (2.5 if (g0 >= 0.5 and n0 >= 6) else 0.0) + 0.25 * min(n0, 9) + (0.3 if pick_up else 0.0)
                              + 0.5 * e_b - (0.8 if n0 < 5 else 0.0) - 0.9 * max(0.0, g0 - 1.2), b_, wsr, g0, n0))   # a long dead stretch before the hero is no 'gap', it is a hole
        if tried:
            sc1, d, words, g1, n1 = max(tried, key=lambda z: z[0])
            gap_info = dict(vocal_gap=round(float(g1), 3), words_5_6s=int(n1), tried=[(round(z[1], 2), round(z[3], 2), z[4]) for z in sorted(tried, key=lambda z: -z[0])[:8]])
            log('cinematic: window d=%.3f vocal gap %.2f s, %d words in the intro (tried %s)' % (d, g1, n1, gap_info['tried']))
    if words is None and can_words:
        words = _words_for(d)
    opts['_cine_gap'] = gap_info
    start = d - float(opts.get('cine_intro_s') or 5.5)
    if words:
        ts = [float(w['t_start']) for w in words]
        te = [float(w['t_end']) for w in words]
        starts = [ts[i] for i in range(len(ts)) if i == 0 or ts[i] - te[i - 1] >= 0.3]
        ok = [s for s in starts if 4.2 <= d - s <= 5.8] or [s for s in ts if 4.4 <= d - s <= 5.8] or             [s for s in starts if 4.2 <= d - s <= 7.0]   # round 10: intro <= 5.8 s (critic: 6.5 s): a phrase start, else any sung word
        if ok:
            start = min(ok, key=lambda s: abs(d - s - 5.5)) - 0.03
    start = max(0.0, start)
    return start, d, d + body


def cine_body_layout(A, start, d, pulse, n_pulses, rng, loose=False, max_shots=None):
    """Body of a 30-180 s cinematic slow-mo edit (2026-10-12, the vibe edits; the 15 s reference body is
    [4] [1] x4 [2]): phrases that each open on a hero hold (4 pulses for the first, 3 after) followed by single
    pulses alternating move / still (move-heavy: m s m m s), phrase starts on the song's own section starts
    (>= 7 pulses apart, else every ~12 pulses); the loudest section's phrase is the emotional LIFT (its hero hold
    gets the bigger bloom bump); a long edit (loose) uses 2-pulse shots in the quieter sections; the last shot holds
    2-3 pulses into the fade. -> (lens, kinds, lift phrase pulse indices)"""
    secs = A.get('sections') or []
    bt = np.asarray(A.get('beats') or [], float)
    E = np.asarray(A.get('beat_energy') or [], float)
    body_end = d + n_pulses * pulse
    starts = []
    for sct in secs:
        ss = float(sct['start'])
        if d + 6 * pulse <= ss <= body_end - 8 * pulse:
            starts.append(int(round((ss - d) / pulse)))
    pstarts = [0]
    for q in sorted(set(starts)):
        if q - pstarts[-1] >= 7:
            pstarts.append(q)
    last_n = 3 if n_pulses >= 40 else 2
    tail = n_pulses - last_n
    filled = []
    for i, q in enumerate(pstarts):
        nxt = pstarts[i + 1] if i + 1 < len(pstarts) else tail
        while nxt - q > 16:                           # no phrase runs longer than ~16 pulses without a hero hold
            filled.append(q)
            q += 12
        filled.append(q)
    pstarts = [q for q in filled if q < tail - 4]

    def energy(a, b):
        if not len(E) or len(E) != len(bt):
            return 0.5
        m = (bt >= d + a * pulse) & (bt < d + b * pulse)
        return float(E[m].mean()) if m.any() else 0.5
    ph = []
    for i, q in enumerate(pstarts):
        nxt = pstarts[i + 1] if i + 1 < len(pstarts) else tail
        ph.append((q, nxt, energy(q, nxt)))
    lift = max(ph[1:], key=lambda z: z[2])[0] if len(ph) > 1 else None
    med_e = float(np.median([z[2] for z in ph])) if ph else 0.5
    lens, kinds = [], []
    pat = ['move', 'still', 'move', 'move', 'still']
    k = 0
    for i, (q, nxt, e) in enumerate(ph):
        hold = 4 if i == 0 else 3
        hold = min(hold, max(1, nxt - q - 1))
        lens.append(hold)
        kinds.append('hero')
        left = nxt - q - hold
        quiet = loose and e < med_e and q != lift
        while left > 0:
            n = 2 if (quiet and left >= 2 and rng.random() < 0.6) else 1
            if not quiet and left >= 2 and loose and rng.random() < 0.15:
                n = 2
            lens.append(n)
            kinds.append(pat[k % len(pat)])
            k += 1
            left -= n
    lens.append(last_n)
    kinds.append('last')
    if max_shots:
        # fewer clean clips than shots: merge pairs of single pulses into 2-pulse shots (quiet phrases first, the lift
        # phrase last), never across a hero hold
        lift_rng = None
        if lift is not None:
            nx = [z[1] for z in ph if z[0] == lift]
            lift_rng = (lift, nx[0] if nx else lift)
        for pass_ in (0, 1):
            j = 1
            while len(lens) > int(max_shots) and j < len(lens) - 2:
                p0 = sum(lens[:j])
                in_lift = lift_rng is not None and lift_rng[0] <= p0 < lift_rng[1]
                if lens[j] == 1 and lens[j + 1] == 1 and kinds[j] != 'hero' and kinds[j + 1] != 'hero'                         and (pass_ == 1 or not in_lift):
                    lens[j:j + 2] = [2]
                    kinds[j:j + 2] = [kinds[j] if kinds[j] == 'move' else kinds[j + 1]]
                j += 1
        if loose:
            # a long landscape edit may hold 3 pulses in its quiet phrases (2-3 s verse holds) when clips are short
            j = 1
            while len(lens) > int(max_shots) and j < len(lens) - 2:
                p0 = sum(lens[:j])
                in_lift = lift_rng is not None and lift_rng[0] <= p0 < lift_rng[1]
                if not in_lift and kinds[j] != 'hero' and kinds[j + 1] != 'hero' and lens[j] + lens[j + 1] == 3:
                    lens[j:j + 2] = [3]
                    kinds[j:j + 2] = ['move']
                j += 1
    return lens, kinds, ([lift] if lift is not None else [])


def plan_cinematic_slowmo(A, shots, opts):
    rng = random.Random(opts.get('seed'))
    pool = Pool(shots, rng)
    if len(pool) < 3:
        raise RuntimeError(f'only {len(pool)} usable shots')
    template = 'cinematic_slowmo'
    style, group, grade = 'velocity_slowmo', 'general', 'golden_soft'
    fps = int(opts.get('fps') or 60)
    bt = np.asarray(A.get('beats') or [], float)
    # the pulse from the tracked beats themselves (music.json's beat_period can be a different metrical level:
    # 'blue' @0.95 tracks 0.97 s beats with beat_period 0.643), k beats closest to 1.0 s (_sX2: 3 x 0.332)
    P = float(np.median(np.diff(bt))) if len(bt) > 3 else float(A['beat_period'])
    k = min(range(1, 7), key=lambda kk: abs(kk * P - 1.0))
    pulse = k * P
    body_s = float(opts.get('cine_body_s') or 9.96)     # 2026-10-12 (vibe edits): the body length is a parameter (30-180 s videos)
    n_pulses = max(8, int(round(body_s / pulse)))
    start, d, end = cine_window(A, pulse, n_pulses, opts, rng)
    pin = opts.get('cine_pin')
    if pin:
        # 2026-10-19 round 2 (owner: "the clips are all good, only the timing with the music needs work"): an approved
        # edit's clips are kept (same clips, order, entry points) and only the cuts are re-timed: the same song window,
        # the same words-on-black intro (hero switch), the same length
        start = float(pin['music_start'])
        end = start + float(pin['duration'])
        d = start + float(pin['shots'][1]['t0']) + 0.06
    near = bt[(bt >= d - 8.0) & (bt <= d + 12.0)]
    if len(near) > 4:                                # the local beat, where the edit actually is
        P = float(np.median(np.diff(near)))
        k = min(range(1, 7), key=lambda kk: abs(kk * P - 1.0))
        pulse = k * P
        n_pulses = max(8, int(round(body_s / pulse)))
        end = d + n_pulses * pulse if not pin else start + float(pin['duration'])
    # remake round 6: the pulse and every switch frame from the song's own accents (onset strength), logged
    cine_log = {}
    env = (_cine_onsets(A['path'], start, start + max(22.0, (d - start) + body_s + 6.0))
           if A.get('path') and os.path.exists(str(A['path'])) and A.get('tracker') != 'synthetic' else None)
    if env is not None and len(bt) > 3:
        pp = _cine_pick_pulse(env, P, d - start, 0.06)
        if pp:
            pulse, n_pulses, cine_log['pulses'] = pp
            if opts.get('cine_body_s'):
                n_pulses = max(8, int(round(body_s / pulse)))
            end = d + n_pulses * pulse
    if pin:
        end = start + float(pin['duration'])
        n_pulses = max(13, n_pulses)
    long_body = n_pulses > 12
    if long_body:
        # 2026-10-12 (vibe edits 30-180 s): phrases of [hero hold] + single pulses, the hero holds and the lift on the
        # song's own section starts, looser 2-pulse shots in the quiet sections of a long edit (cine_body_layout); a
        # body that needs more shots than there are clean clips gets shorter (never an unchecked clip on screen)
        while True:
            lens, kinds, lifts = cine_body_layout(A, start, d, pulse, n_pulses, rng, loose=bool(opts.get('cine_loose')),
                                                  max_shots=opts.get('cine_max_shots'))
            if not opts.get('cine_max_shots') or len(lens) <= int(opts['cine_max_shots']) or n_pulses <= 14:
                break
            n_pulses -= max(1, len(lens) - int(opts['cine_max_shots']))
            end = d + n_pulses * pulse
        cine_log['layout'] = dict(lens=lens, lifts=lifts)
    else:
        lens = [4] + [1] * max(1, n_pulses - 6) + [2]
        kinds = ['hero'] + [('move' if j % 2 == 0 else 'still') for j in range(len(lens) - 2)] + ['last']
        lifts = []
    view = MU.window_view(A, start, end, 0.9, 1.1)
    aspect = opts.get('aspect') or '9:16'      # remake round 1 (cine): 16:9 = the reference's own landscape master
    lay = 'rotated_16x9' if aspect == '9:16' else 'fill'
    k_asp = 1.0 if aspect == '9:16' else 1080.0 / 1920.0
    B = Builder(view, pool, rng, fps, group, template, aspect)
    D = round(end - start, 4)
    drop = round(d - start, 4)
    lead = 0.06
    # the body: [4] [1] x (n - 6) [2] pulses; kinds hero, move / still alternating, last
    cuts, t = [], drop
    bo = bt - start
    for ci, n in enumerate(lens):
        if long_body and ci > 0 and len(bo):
            # a long body follows the tracked beats (a constant pulse drifts off a real song over 30+ s)
            j = int(np.argmin(np.abs(bo - t)))
            if abs(bo[j] - t) < 0.15 * pulse:
                t = float(bo[j])
        if len(bo) and ci == 0:
            # remake round 5 (critic: new-edit intervals 0.96 / 0.92 / 1.04 / 0.88 s, the reference is a metronomic
            # 1.00 s pulse): only the body cut snaps to a tracked beat, the rest ride one constant pulse from it
            j = int(np.argmin(np.abs(bo - t)))
            if abs(bo[j] - t) < 0.2 * pulse:
                t = float(bo[j])
        cuts.append(t)
        t += n * pulse
    if env is not None:
        cuts, cine_log['cuts'] = _cine_lock_cuts(env, cuts, lead, pulse, body_snap=0.04)
        cine_log['pulse'] = round(pulse, 4)
        log('cinematic: pulse %.3f s, cuts %s' % (pulse, json.dumps(cine_log['cuts'])))
        if any(r['weak'] for r in cine_log['cuts'][1:]):
            log('cinematic: WARNING a body cut has no song accent >= 2.0 within 250 ms')
    hold_done = False
    wave_done = False
    if (long_body and opts.get('cine_wave_cuts') and not opts.get('cine_probe') and A.get('path')
            and A.get('tracker') != 'synthetic'):
        # 2026-10-19 round 3 (owner, "momentum"): one clip per wave of the sung line, the switch at the next wave's start;
        # the shot count follows the waves (pinned clips: their order is kept, more / fewer clips as the waves need)
        wc_ = _cine_wave_cuts(A, start, d, end, lead, opts, rng, pin)
        if wc_:
            cuts, lens, kinds, lifts, pulse, cine_log['waves'] = wc_
            if pin:
                npb = len(pin['shots']) - 3
                for i_ in range(1, len(kinds) - 1):
                    if i_ <= npb and pin['shots'][i_ + 1].get('role') in ('move', 'still'):
                        kinds[i_] = pin['shots'][i_ + 1]['role']
            drop = round(float(cuts[0]), 4)
            d = start + drop
            hold_done = wave_done = True
            log('cinematic: wave cuts main %.3f s, %d shots' % (pulse, len(cuts)))
        elif pin:
            raise RuntimeError('cine_pin: no wave cut layout')
    if (not wave_done and long_body and (opts.get('cine_hold_cuts') or pin) and not opts.get('cine_probe') and A.get('path')
            and A.get('tracker') != 'synthetic'):
        # 2026-10-19 round 2 (owner: never cut while the singer holds a note / word): cuts where the music turns, holds
        # forbidden, a DP over the allowed switch times (music_moments.holds; the rule measured on _sX2)
        hc_ = _cine_hold_cuts(A, start, d, end, lead, opts, rng, pin)
        if hc_:
            cuts, lens, kinds, lifts, pulse, cine_log['holds'] = hc_
            drop = round(float(cuts[0]), 4)
            d = start + drop
            hold_done = True
            if abs(float(cine_log['holds'].get('D') or D) - D) > 1e-3:
                # the pinned closing shot keeps its length past the old end (the song goes on): a later fade-out
                end = start + float(cine_log['holds']['D'])
                D = round(end - start, 4)
                view = MU.window_view(A, start, end, 0.9, 1.1)
                B = Builder(view, pool, rng, fps, group, template, aspect)
                log(f'cinematic: the edit ends at {D:.2f} s (the closing shot keeps its approved length)')
            log('cinematic: hold-aware cuts main %.3f s, %d shots, %d inside a hold' % (
                pulse, len(cuts), cine_log['holds']['in_hold']))
        elif pin:
            raise RuntimeError('cine_pin: no hold-aware cut layout for the pinned clips')
    if (not hold_done and long_body and opts.get('cine_moments') and not opts.get('cine_probe') and A.get('path')
            and A.get('tracker') != 'synthetic'):
        # 2026-10-19 (owner: the cuts must follow the song like the reference - musical moments, not a fixed pulse; 3b's
        # pulse picker took 4 eighths of a 3-eighth song, so 70 % of its cuts sat a third of a beat off the meter): the cuts
        # are chosen on the main beats of the song's real meter that carry a phrase start / sung syllable / accent,
        # measured on the audio actually used (core/edit/music_moments.py)
        mc = _cine_moment_cuts(A, start, d, end, lead, opts, rng)
        if mc:
            cuts, lens, kinds, lifts, pulse, cine_log['moments'] = mc
            drop = round(float(cuts[0]), 4)
            d = start + drop
            log('cinematic: moment cuts main %.3f s, %d shots, lens %s' % (pulse, len(cuts), lens))
    # the canvas: the 16:9 master rotated into 9:16 (_sX2) -> motion of x % of a clip's width reads as x * gain % of
    # the output width
    cw, ch, ow = 1920.0, 1080.0, (1080.0 if aspect == '9:16' else 1920.0)
    flows = {}
    for s in pool.shots:
        try:
            flows[id(s)] = clip_flow(s['path'])
        except Exception as e:  # noqa: BLE001
            log(f'cinematic: flow of {os.path.basename(s["path"])} failed ({e})')
            flows[id(s)] = (np.array([0.0, 1.0]), np.array([0.1, 0.1]))

    # remake round 6 (critics, round 2 of edit-studio-6: a phone call, an eye macro, a lip-flap hero, a milky wide shot
    # and a soft head all passed the library tags): the calm-portrait QA measured on the take itself, graded
    # (portrait.qa, cached next to the clip); computed for the best 80 by the old score, read from cache for the rest
    qas = {}
    if os.environ.get('CINE_QA', '1') != '0':
        try:
            import portrait as PQ
            base = sorted(pool.shots, key=lambda x: -(float(x.get('taste') if x.get('taste') is not None
                                                            else x.get('_aesthetic', 0.5)) + 0.3 * float(x.get('crisp') or 0.5)))
            top = {id(x) for x in base[:int(opts.get('cine_qa_top') or (400 if n_pulses > 12 else 80))]}
            for x in pool.shots:
                pth = str(x.get('path') or '')
                if not os.path.exists(pth):
                    continue
                if id(x) in top or os.path.exists(pth + '.portrait.json'):
                    try:
                        qas[id(x)] = PQ.qa(pth)
                    except Exception as e:  # noqa: BLE001
                        log(f'cinematic: portrait QA of {os.path.basename(pth)} failed ({e})')
        except Exception as e:  # noqa: BLE001
            log(f'cinematic: no portrait QA ({e})')

    def qa_rejects(s, kind, slot):
        q = qas.get(id(s))
        if q is None or q.get('bad'):
            return None
        sig = q.get('sig') or {}
        fh = float(q.get('face_h') or 0.0)
        rej = []
        if float(sig.get('phone', 0.0)) >= 0.5:
            rej.append('phone')
        if (fh > 0.9 and (float(q.get('face_edge') or 0.0) >= 0.5 or float(sig.get('eye', 0.0)) >= 0.2)) or                 (fh > 0.8 and float(q.get('face_edge') or 0.0) >= 0.5) or float(sig.get('eye', 0.0)) >= 0.3:
            rej.append('eye_macro')
        if fh < (0.25 if kind in ('hero', 'still', 'last') else 0.2) or float(q.get('face_frac') or 0.0) < 0.6:
            rej.append('face_small')
        if float(sig.get('wide', 0.0)) >= 0.5:
            rej.append('wide')
        if (float(q['lap_r']) < 700.0 if q.get('lap_r') is not None else False) or float(q.get('lap') or 0.0) < 450.0:
            # (lap: graded, bicubic 960 px, no encoder sharpening - render / lap = 1.1-2.5 over 12 rendered shots, so
            # 450 keeps every rendered body shot >= ~700; lap_r alone let a soft upscale through at 1019 -> 366)
            rej.append('soft')                         # critics: >= 700 at 960 px on the rendered, graded frame
        sat, p2 = float(q.get('sat') or 0.0), float(q.get('p2') or 0.0)
        if sat < 40.0 or (p2 > 55.0 and sat < 55.0):
            rej.append('milky')
        if sat > min(150.0, max(100.0, 1.15 * CINE_SAT[slot])):
            rej.append('oversat')
        # 2026-10-18 (slow-mo pool growth, by eye on the rejects): mouth_dm alone fires on hair / head motion in calm twixtor
        # portraits (mouth openness 0.00-0.03); a talking take also opens the mouth (>= 0.05) or moves it a lot (> 10)
        md, mo = float(q.get('mouth_dm') or 0.0), float(q.get('mouth') or 0.0)
        if (md > 1.5 and fh >= 0.2 and (mo >= 0.05 or md > 10.0)) or float(sig.get('talk', 0.0)) >= 0.6:
            rej.append('talking')
        if float(sig.get('female', 0.5)) < 0.3:
            # owner 2026-09-29: cute edits open and close on a girl; round 7: every slot (a talking Gojo two-shot won a
            # 'move' slot on the -0.8 boost alone)
            rej.append('male_lead')
        if kind == 'last' and float(q.get('p50') or 0.0) < 95.0:
            rej.append('dark_last')                    # critic: close on a lit face (luma >= 100)
        gore, fierce = _cine_mood(s['path'])
        if not CINE_ALLOW_GORE and (gore >= 0.55 or (gore >= 0.3 and fierce >= 0.5) or _cine_red(s['path']) > 0.08):
            rej.append('gore')                         # round 6: a blood-streaked Gojo reached the new edit again
        if fierce >= 0.6:
            rej.append('fierce')
        if _cine_panels(s['path']):
            rej.append('panels')
        if not rej and os.environ.get('CINE_MOUTH', '1') != '0':
            # round 10 (critics, BLOCKING x2: a lip-flap Kaguya take slowed down, Yor talking through a slot): the open /
            # close cycles of the mouth box over the take (portrait.mouth_cycles); 2 or more = a talking take
            mc = _cine_mouth(s['path'])
            if mc and float(mc.get('cycles') or 0.0) >= 2.0 and float(mc.get('amp') or 0.0) >= 5.0:
                rej.append('lipflap')
        if not rej and os.environ.get('CINE_INTERP', '1') != '0':
            # round 10 (critics, BLOCKING: ghosted hair / smeared mouth, a pose that pops un-interpolated): sources whose
            # drawings step (<= 35 drawings / s with a jump >= 6x the local motion) or whose flow between drawings is
            # inconsistent (interp_qa), plus the clips reviewed by eye as carrying baked-in ghosting (refs/cine_ghost.json)
            ir = _cine_interp(s['path'])
            if ir and ((float(ir.get('spike') or 0.0) > 6.0 and float(ir.get('drawing_fps') or 99.0) < 35.0)
                       or float(ir.get('bad_frac') or 0.0) > 0.03 or float(ir.get('p90') or 0.0) > 0.08):
                rej.append('stepped')
            if _cine_ghost_listed(s['path']):
                rej.append('ghost')
            if ir and abs(float(ir.get('roll') or 0.0)) > 0.05:
                rej.append('roll')                     # round 10 (critic: -0.146 deg / frame tilt; _sX2 <= 0.047)
        if kind == 'still' and q.get('drift') is not None and float(q['drift']) < 0.006:
            rej.append('frozen')                       # round 10: a still slot needs micro-motion (hair / particle drift)
        if int(s.get('faces') or 0) >= 3:
            rej.append('group')                        # round 7: _sX2 is one character per shot (a 4-face group still passed)
        if slot in (2, 4) and float(q.get('p50') or 0.0) > 100.0:
            rej.append('bright_still')                 # round 8: _sX2's still slots are dark (luma 62 / 66), a hard window
        if not rej and os.environ.get('CINE_PEOPLE', '1') != '0':
            # round 8 (critics, BLOCKING x3: a Yor + Loid gag two-shot, an over-the-shoulder conversation, a Mitsuri +
            # Obanai two-shot in solo portrait slots): who is in the frame (portrait.people, cached per clip)
            pp = _cine_people(s['path'])
            if pp:
                if float(pp.get('twoshot') or 0.0) >= 0.5 or float(pp.get('face2_h') or 0.0) >= 0.12 or                         float(pp.get('male_face') or 0.0) >= 0.7:
                    rej.append('twoshot')
                if float(pp.get('gag') or 0.0) >= 0.45:
                    rej.append('gag')
        return rej

    def hard_rejects(s):
        # 2026-10-12 (vibe edits, 30-180 s: a long body ran out of reject-free clips and the soft fallback put talking,
        # soft, gore and phone takes on screen): rejects that no slot may take, whatever the pool size
        q = qas.get(id(s))
        if q is None:
            return ['unmeasured'] if qas else []
        if q.get('bad'):
            return ['qa_bad']
        r = set(qa_rejects(s, 'move', 1) or []) & CINE_HARD
        # the 'soft' gate (lap 450 / lap_r 700) was tuned on 4K twixtor packs; flat-shaded 1080p pool clips that look
        # crisp measure lap_r 300-700: only a really soft take (lap_r < 260, or < 400 with lap < 120) is never shown
        r.discard('soft')
        lr, lp = q.get('lap_r'), float(q.get('lap') or 0.0)
        if (lr is not None and (float(lr) < 260.0 or (float(lr) < 400.0 and lp < 120.0))) or (lr is None and lp < 150.0):
            r.add('soft')
        if float(q.get('sat') or 0.0) < 25.0:
            r.add('grey')                              # no vibrance brings a near-monochrome take back
        if float(q.get('face_h') or 0.0) < 0.2:
            r.add('face_small')                        # _sX2 is portraits only: a tiny figure in a wide frame never fits
        if float(q.get('sat') or 0.0) > 165.0:
            r.add('oversat')                           # a neon / paint frame (sat 173-178 reached a test edit)
        if q.get('drift') is not None and float(q['drift']) < 0.006:
            r.add('frozen')                            # a frozen frame: slowed it is a still image (23 repeated frames)
        try:
            e_nat = float(np.median(flows[id(s)][1]))
        except Exception:  # noqa: BLE001
            e_nat = 0.1
        if e_nat < 0.015:
            r.add('frozen')                            # native motion ~0 (0.01 %W/frame): repeated frames once slowed
        if e_nat > 0.6:
            r.add('too_fast')                          # full-body action at 0.9 %W/frame: not a slow-mo beauty shot
        ru = s.get('rules') or {}
        # owner: bright / warm / dreamy moods only (a villain / gloomy / creepy frame reached a test edit: Kaguya O.)
        if float(ru.get('villain') or 0.0) >= 0.35:
            r.add('villain')
        if max(float(ru.get('gloomy') or 0.0), float(ru.get('creepy') or 0.0)) >= 0.42:
            r.add('gloomy')
        if _cine_mood(s['path'])[1] >= 0.5:
            r.add('fierce')
        return sorted(r)

    if opts.get('cine_probe'):
        hc = [x for x in pool.shots if not hard_rejects(x)]
        return {'_probe': dict(n=len(pool.shots), clean=len(hc), pulse=round(pulse, 4), drop=round(d - start, 4),
                               paths=[x['path'] for x in hc],
                               rejects={x['path']: hard_rejects(x) for x in pool.shots},
                               soft={x['path']: (qa_rejects(x, 'move', 1) or []) for x in pool.shots})}

    used_paths = set()

    def fit(s, kind, prev):
        if s.get('path') in used_paths:
            return -99.0                   # round 10: one clip is never used twice (an 'oversat' still won slots 3 and 5 of the new edit)
        e = float(np.median(flows[id(s)][1]))
        sc = 0.45 * float(s.get('taste') if s.get('taste') is not None else s['_aesthetic']) + 0.25 * s['_aesthetic'] \
            + 0.15 * s['_rank']
        lum = s.get('luma')
        if lum is not None:
            sc += 0.15 * min(1.0, float(lum) / 110.0)             # the reference's shots are bright and soft
        # remake round 1 (cine, v3 -> v4): the two soft picks (Laplacian 127 / 198 vs the reference's 587 / 1789)
        # read as "clips put together"; the reference's shots are the crispest of the group, so crispness counts
        sc += 0.35 * float(s.get('crisp') if s.get('crisp') is not None else 0.5)
        if float(s.get('talk') or 0.0) > 0.3 and s.get('framing') != 'closeup':
            sc -= 0.2                                             # a talking medium shot is never a hero frame
        if not s.get('faces') and ('scenery' in (s.get('tags') or []) or s.get('framing') == 'wide'):
            sc -= 0.3                                             # _sX2: every shot is a character, never scenery
        if kind == 'still':
            sc += 0.3 * (1.0 - min(1.0, e / 0.12))
        else:
            sc += 0.3 * min(1.0, e / 0.12)
            if kind == 'move':
                # round 10 (critic: choose move-slot sources by native subject motion >= 0.3 %W / frame): a take whose own
                # motion is under half of that cannot fall into slow motion, it only freezes
                sc += 0.5 * min(1.0, e / 0.30) - 0.8 * max(0.0, 1.0 - e / 0.15)
            if kind == 'hero' and s.get('framing') == 'closeup':
                sc += 0.15
        if prev is not None and s.get('source_id') and s.get('source_id') == prev.get('source_id'):
            sc -= 0.35
        sc -= 0.6 * s['_uses']
        if s.get('has_speech'):
            sc -= 0.3
        # remake round 2 (critics: three of six shots from one OP, a neon paint frame, a face cut off in the last
        # shot): the reference's six shots are six different shows, each a clean character portrait
        if long_body:
            # 2026-10-12 (30-180 s edits: a show necessarily returns): never the same show / character within the last
            # 4 shots, a mild cost per earlier use so the whole pool gets spread
            rk = _show_key(s)
            if rk in show_uses_order[-4:]:
                sc -= 2.0 if rk == (show_uses_order[-1] if show_uses_order else None) else 1.0
            sc -= 0.12 * show_uses.get(rk, 0)
        else:
            sc -= 0.5 * show_uses.get(_show_key(s), 0)
        if float(s.get('colorful') or 0.0) > 95.0:
            sc -= 0.3                                             # stylised neon / paint frames, not a portrait
        if kind in ('hero', 'last', 'still') and not s.get('faces'):
            sc -= 0.4
        if kind in ('still', 'last') and ({'fight', 'action'} & set(s.get('tags') or [])):
            sc -= 0.25
        if kind == 'hero' and float(s.get('sharpness') or 1000.0) < 800.0:
            sc -= 0.25                                            # the first picture after the words must be crisp
        # remake round 5 (critics: two Blue Lock shots in a row - the per-clip character tags differ; no brightness
        # rhythm; a two-frame jolt inside a still; blood on a hero face; a face cut off in the last shot)
        if long_body:
            if any(_cine_ids(s) & ids_ for ids_ in ids_order[-3:]):
                sc -= 1.2                                 # the same character again within 3 shots
        elif _cine_ids(s) & used_ids:
            sc -= 0.6                                             # one shot per show / source video
            if qas:
                # round 6: the portrait gates (-3) outweighed it; one show per shot stays the rule. Round 9: a SECOND
                # shot of a show costs less (a clean calm Waguri portrait beats an oversaturated aura frame or a soft
                # one when the pool has no other clean female still); a third stays at -2.5
                sc -= 2.5 if show_uses.get(_show_key(s), 0) >= 2 else 1.2
        lum = float(s.get('luma') if s.get('luma') is not None else 110.0)
        if prev is not None:
            pl = float(prev.get('luma') if prev.get('luma') is not None else 110.0)
            if abs(lum - pl) < 40.0:
                sc -= 0.35                                        # _sX2 luma 111 / 204 / 50 / 95 / 45 / 130
        slot = _slotq()
        sc -= 0.25 * min(1.0, abs(lum - CINE_LUMA[slot]) / 80.0)
        if kind in ('still', 'last', 'hero') and _cine_jolt(flows[id(s)][1]):
            sc -= 0.4
        jt = _cine_jolt_t(flows[id(s)][0], flows[id(s)][1])
        if jt is not None and jt < 1.3:
            sc -= 1.0                                  # round 8: a source cut / jump inside the span the shot needs
        red = _cine_red(s['path'])
        if red > 0.06:
            sc -= min(0.8, 6.0 * (red - 0.06))                     # blood / red wash: never a calm portrait
        if kind in ('hero', 'still', 'last'):
            sc += 0.25 * min(1.0, float(s.get('big_face') or 0.0) / 0.3)
        if {'fight', 'action'} & set(s.get('tags') or []):
            sc -= 0.2
        gore, fierce = _cine_mood(s['path'])
        sc -= 1.2 * max(0.0, fierce - 0.4) + 1.5 * max(0.0, gore - 0.15)   # a soft ballad edit: no glare / blood
        if kind == 'move' and e > 0.45:
            sc -= 0.5                                  # a stage dance at 0.77 %W/frame even slowed: not a slow-mo shot
        if kind in ('hero', 'still', 'last') and float(s.get('sharpness') or 1000.0) < 700.0:
            sc -= 0.3                                  # an eye macro at Laplacian 117 closed the round-5 new edit
        if kind == 'last' and (float(s.get('big_face') or 0.0) < 0.2 or lum < 95.0):
            sc -= 0.3                                  # close on a clear, lit face (critic: face >= 0.25 H, luma >= 100)
        if kind in ('last', 'hero', 'still') and float(s.get('big_face') or 0.0) > 0.7:
            sc -= 0.3                                  # an eye macro is not a portrait (_sX2 closes on a whole face)
        slot_q = _slotq()
        rej = qa_rejects(s, kind, slot_q)
        if rej is None:
            sc -= 1.0                                  # unmeasured: only when nothing measured passes
        else:
            q = qas[id(s)]
            sig = q.get('sig') or {}
            if rej:
                sc -= 3.0 + 0.5 * len(rej)
            sc += 0.6 * (float(sig.get('calm', 0.5)) - 0.5)
            # owner 2026-09-29: cute / beautiful edits prioritise female characters (a boost, not a filter)
            sc += 0.5 * (float(sig.get('female', 0.5)) - 0.5)
            if float(sig.get('female', 0.5)) < 0.3:
                sc -= 0.8                              # a male lead only where nothing female fits (owner note)
            sc -= 0.8 * min(1.0, max(0.0, abs(float(q.get('p50') or 110.0) - CINE_LUMA[slot_q]) - 15.0) / 60.0)
            if slot_q == 1 and float(q.get('p50') or 0.0) < 165.0:
                sc -= 0.6                              # _sX2's bright 'sky' slot (luma 204): a high-key pick
            pq = qas.get(id(prev)) if prev is not None else None
            if pq and not pq.get('bad') and abs(float(q.get('p50') or 110.0) - float(pq.get('p50') or 110.0)) < 40.0:
                sc -= 0.8                              # round 8: dark next to bright at every cut (_sX2 jumps 71-125)
            sc += 0.2 * min(1.0, float(q.get('lap_r') or 2.0 * float(q.get('lap') or 0.0)) / 3000.0)
            if kind == 'last' and float(q.get('drift') or 0.0) < 0.03:
                sc -= 0.5                              # the closing shot drifts the whole time (critic)
            if kind == 'move' and float(q.get('drift') or 0.0) < 0.03:
                sc -= 1.2                              # a 'move' slot needs native motion to fall into slow motion
            if kind == 'still':
                d0 = float(q.get('drift') or 0.0)
                sc -= 0.3 * (0.0 if 0.008 <= d0 <= 0.06 else 1.0)   # a micro-drift, not a frozen frame
        return sc + rng.uniform(0, 0.04)

    pin_names = ({os.path.normcase(os.path.basename(str(x['path']))) for x in pin['shots'][1:]} if pin else set())
    show_uses = {}
    used_ids, show_uses_order = set(), []
    ids_order = []
    slot_map, nm, ns = [], 0, 0
    for kd in kinds:
        if not long_body:
            slot_map.append(min(len(CINE_LUMA) - 1, len(slot_map)))
        elif kd == 'hero':
            slot_map.append(0)
        elif kd == 'last':
            slot_map.append(len(CINE_LUMA) - 1)
        elif kd == 'move':
            slot_map.append(1 if nm % 2 == 0 else 3)
            nm += 1
        else:
            slot_map.append(2 if ns % 2 == 0 else 4)
            ns += 1

    def _slotq():
        return slot_map[min(len(slot_map) - 1, len(show_uses_order))]

    B.segs = []
    B.add(0.0, 'none', role='hero', speed={'kind': 'freeze'}, layout=lay, section='intro')
    B.segs[-1]['hidden'] = True
    prev = None
    for i, (c0, n, kind) in enumerate(zip(cuts, lens, kinds)):
        t0 = round(round((c0 - lead) * fps) / fps, 5)
        t1 = round(round(((cuts[i + 1] - lead) if i + 1 < len(cuts) else D) * fps) / fps, 5)
        # round 10 (critics: a soft take, an eye macro, a clip used twice reached the new edit because a reject only cost
        # points): candidates are the measured shots with NO reject for this slot; the soft penalty only decides when
        # fewer than 3 such shots remain
        slot_now = _slotq()
        clean = [x for x in pool.shots if qas and x.get('path') not in used_paths and qa_rejects(x, kind, slot_now) == []]
        if len(clean) >= 3:
            cand_shots = clean
        else:
            hc = [x for x in pool.shots if x.get('path') not in used_paths and not hard_rejects(x)]
            if long_body:
                cand_shots = clean + [x for x in hc if x not in clean]
                if not cand_shots:
                    cand_shots = [x for x in pool.shots if x.get('path') not in used_paths] or pool.shots
                    log('cinematic: WARNING the clean clips ran out: an unchecked clip fills this slot')
            else:
                cand_shots = pool.shots
        if len(clean) < 3:
            log(f'cinematic: only {len(clean)} reject-free shots for slot {slot_now} ({kind}): soft penalties decide')
        pin_sh = None
        if pin and wave_done:
            # the approved clips in their order; the shot count follows the waves: extra body shots get fresh reviewed
            # clips (the approved closing clip stays last), fewer shots drop approved body clips from the end
            npb = len(pin['shots']) - 3                  # approved body clips between the hero and the closing shot ([intro, hero, body.., last])
            if i == 0 or (0 < i < len(cuts) - 1 and i <= npb):
                pin_sh = pin['shots'][i + 1]
            elif i == len(cuts) - 1:
                pin_sh = pin['shots'][-1]
            if pin_sh is not None and not pin_sh.get('path'):
                pin_sh = None                            # a pinned slot left open (path null): a fresh reviewed clip
            if pin_sh is not None:
                want = os.path.normcase(os.path.basename(str(pin_sh['path'])))
                hit = [x for x in pool.shots if os.path.normcase(os.path.basename(str(x.get('path')))) == want]
                if not hit:
                    raise RuntimeError(f'cine_pin: clip {want} is not in the pool')
                s = hit[0]
            else:
                fresh = [x for x in cand_shots if os.path.normcase(os.path.basename(str(x.get('path')))) not in pin_names]
                s = max(fresh or cand_shots, key=lambda x: fit(x, kind, prev))
                log(f'cinematic: wave shot {i} has no approved clip: fresh {os.path.basename(str(s.get("path")))}')
        elif pin:
            pin_sh = pin['shots'][i + 1]
            want = os.path.normcase(os.path.basename(str(pin_sh['path'])))
            hit = [x for x in pool.shots if os.path.normcase(os.path.basename(str(x.get('path')))) == want]
            if not hit:
                raise RuntimeError(f'cine_pin: clip {want} is not in the pool')
            s = hit[0]
        if not pin:
            s = max(cand_shots, key=lambda x: fit(x, kind, prev))
        if os.environ.get('CINE_WHY'):
            for x in pool.shots:
                if any(w and w in str(x['path']).replace(chr(92), '/') for w in os.environ['CINE_WHY'].split(',')):
                    log('  why %s slot %d (%s): rej=%s fit=%.2f' % ('/'.join(str(x['path']).replace(chr(92), '/').split('/')[-2:]), slot_now, kind,
                                                           qa_rejects(x, kind, slot_now), fit(x, kind, prev)))
        if os.environ.get('CINE_DEBUG'):
            top5 = sorted(pool.shots, key=lambda x: -fit(x, kind, prev))[:6]
            for x in top5:
                log('  cand %s %.2f rej=%s ids=%s' % (os.path.basename(os.path.dirname(x['path'])) + '/' +
                                                      os.path.basename(x['path']), fit(x, kind, prev),
                                                      qa_rejects(x, kind, _slotq()),
                                                      sorted(_cine_ids(x))[:4]))
        hi, lo, tau = CINE_TARGET[kind]
        if isinstance(lo, tuple):
            lo = rng.uniform(*lo)
        # CINE_TARGET is in % of the PORTRAIT width (the rotated master); the same picture in its own 16:9 frame
        # measures x 1080/1920 (ref_land_E vs ref_energy of _sX2: 0.74 / 1.28, 0.84 / 1.40, 0.14 / 0.30)
        hi, lo = hi * k_asp, lo * k_asp
        ct, ce = flows[id(s)]
        sw, sh = float(s.get('width') or 1920), float(s.get('height') or 1080)
        gain = max(cw / sw, ch / sh) * sw / ow
        src_in = round(min(0.05, max(0.0, s['duration'] - 0.5)), 4)
        q = qas.get(id(s))
        if q and q.get('head_skip') and q.get('n'):
            # remake round 6 (critic: a cut landing on 3 interpolation-blurred frames): start where 3 native frames in
            # a row reach 0.8 x the take's median sharpness
            nfps = float(q['n']) / max(0.1, float(q['t1']) - float(q['t0']))
            src_in = round(min(max(0.0, s['duration'] - 1.0), max(src_in, int(q['head_skip']) / nfps + 0.02)), 4)
        rj = qa_rejects(s, kind, _slotq())
        log(f'cinematic: {kind} <- {os.path.basename(os.path.dirname(s["path"]))}/{os.path.basename(s["path"])} '
            f'rejects={rj} qa=' + (json.dumps({k: q.get(k) for k in ('face_h', 'lap_r', 'p50', 'sat', 'mouth_dm', 'drift')})
                                    if q else 'none'))
        pp = _CINE_PEOPLE.get(s['path'])
        if pp and pp.get('blink_s') is not None and src_in < float(pp['blink_s']) + 0.2 and s['duration'] > 1.6:
            # round 8 (critic: the cut landed on a closed-eye frame): enter after the blink
            src_in = round(min(s['duration'] - 1.2, float(pp['blink_s']) + 0.2), 4)
        if long_body and len(ct) > 8:
            # 2026-10-12 (a pool clip holds one frame for its first 1.3 s, then moves: the still slot rendered 23
            # repeated frames): enter where the take's own motion starts, if that leaves >= 0.9 s of source
            k_ = max(1, int(round(0.3 * 30)))
            rm = np.convolve(ce, np.ones(k_) / k_, mode='same')
            thr = max(0.02, 0.25 * float(np.median(ce)))
            live = np.where((ct >= src_in) & (rm > thr))[0]
            if len(live) and float(ct[live[0]]) > src_in + 0.1 and s['duration'] - float(ct[live[0]]) >= 0.9:
                src_in = round(float(ct[live[0]]), 4)
        room = max(0.2, s['duration'] - 0.05 - src_in)
        jt = _cine_jolt_t(ct, ce)
        if jt is not None and jt > src_in + 0.3:
            room = min(room, jt - 0.08 - src_in)       # round 8: stop before the source's own jump (Makima 3.00 s: 0.81)
        # 2026-10-12 (vibe edits: a 4x entry on a hair-whip take rendered a double-exposed face for 3 frames): the
        # 30-180 s edits enter at <= 2.2x source speed
        pts, sc = cine_speed_curve(ct, ce, src_in, t1 - t0, (hi, lo, tau), room, gain, fps,
                                   entry_vmax=(2.2 if long_body else None))
        if pin_sh is not None and wave_done:
            # wave layout: the approved clip from its approved entry frame, its speed curve fitted to the new shot length
            # (a shot of a different length can't reuse the old source path without leaving slow motion)
            src_in = float(pin_sh['src_in'])
            room = max(0.2, s['duration'] - 0.05 - src_in)
            if jt is not None and jt > src_in + 0.3:
                room = min(room, jt - 0.08 - src_in)
            pts, sc = cine_speed_curve(ct, ce, src_in, t1 - t0, (hi, lo, tau), room, gain, fps,
                                       entry_vmax=(2.2 if long_body else None))
        elif pin_sh is not None:
            # the approved shot's own source path (same entry and exit frame), time-scaled to the new shot length
            src_in = float(pin_sh['src_in'])
            pts = _retime_curve(pin_sh['speed_curve'], t1 - t0, fps, _pin_bounds(pin_sh['speed_curve'], hero=(i == 0),
                                                                                  last=(i == len(cuts) - 1))[1])
        tr = 'first_shot_entry' if i == 0 else 'exposure_bump_hard'
        # remake round 2 (critics: 'a pop, not a reveal' / 'soft on the beat'): the remake's measured entry (defocus
        # decaying over ~0.9 s with a 1.15 -> 1.0 ease-out push) and a one-frame incoming defocus on body cuts
        # remake round 6: the entry's zoom now lives in the hero's copied camera path (cine_hero_keys, monotone)
        tp = (dict(scale0=1.0, defocus_px=4.5, defocus_s=1.1, defocus_tau=0.5, scale_tau=0.3, scale_s=0.9) if i == 0     # round 10: the reference's blur clears at ~1.0 s (lap 214 at 0.64 s, plateau at 0.96 s); ours was clear at 0.64 s
              else dict(bloom=0.25, gain=0.04, defocus_px=0.0, defocus_s=0.04, tau=0.08))   # round 5: crisp switch frame
        if i > 0 and lifts and sum(lens[:i]) in lifts:
            tp = dict(bloom=0.55, gain=0.09, defocus_px=0.0, defocus_s=0.04, tau=0.12)   # 2026-10-12: the emotional lift (a brighter bump)
        seg = dict(beat=round(c0, 4), t0=t0, t1=t1, transition_in=tr, tparams=tp, role=kind,
                   speed={'kind': 'slowmo'}, layout=lay, shot=s, src_in=src_in, speed_curve=pts,
                   speed_label='slowmo', section=B.section_label(c0),
                   # remake round 2: the hero drifts in on a slow push (ln 0.08 over the shot) so its energy holds the
                   # reference's 0.06 / 0.03 halves when the clip runs out of native motion; the others stay locked
                   camera=(dict(move='static', zoom=1.0, amount=0.0, handheld=0.0, roll=0.0, pan=[0.0, 0.0],
                                keys=cine_hero_keys(t1 - t0 + 0.1, fps, sign=1.0, pan=0.065, tau=0.38, ln_drift=0.037))
                           if kind == 'hero' else
                           # round 6 (critic: the closing shot ends on a held frame; _sX2 drifts to the end): slow push
                           dict(move='push_in', zoom=1.0, amount=0.12, handheld=0.0, roll=0.0, pan=[0.0, 0.0])
                           if kind == 'last' else
                           dict(move='static', zoom=1.0, amount=0.0, handheld=0.0, roll=0.0, pan=[0.0, 0.0])),
                   cine=dict(kind=kind, target=[round(hi, 3), round(lo, 3), tau], scale=round(sc, 3)))
        B.segs.append(seg)
        pool.mark(s, src_in, pts[-1][1], i + 1)
        show_uses[_show_key(s)] = show_uses.get(_show_key(s), 0) + 1
        used_ids |= _cine_ids(s)
        ids_order.append(_cine_ids(s))
        used_paths.add(s.get('path'))
        show_uses_order.append(_show_key(s))
        prev = s
    h, nx = B.segs[0], B.segs[1]
    h['t1'] = nx['t0']           # the hidden intro shot under the words on black: the first body clip, frozen
    h.update(shot=nx['shot'], src_in=nx['src_in'], speed={'kind': 'freeze'}, speed_label='freeze',
             speed_curve=[[0.0, nx['src_in']], [round(h['t1'], 5), nx['src_in']]],
             camera=dict(move='static', handheld=0.0, accents=[]))
    overlays = [dict(type='text_on_black', t0=0.0, t1=round(B.segs[1]['t0'], 4), text='', words=[], leak=False, style='neon'),
                # remake round 6 (_sX2's closing shot: a warm lens light grows from the top right into the fade)
                dict(type='warm_bloom', t0=round(B.segs[-1]['t0'], 4), t1=round(D - 0.3, 3), x=0.86, y=0.14,
                     opacity=0.5, pow=1.2),
                dict(type='fade', t0=round(D - 1.22, 3), t1=D, color='black', dir='out')]
    plan = finish(B, A, dict(start=start, end=end, drop=d), view, opts, template, style, group, grade, overlays, aspect,
                  audio=dict(fade_in=0.0, fade_out=0.0), extra=dict(intro='words_on_black', end='fade_to_black', drop=drop,
                                                                   pulse=round(pulse, 4), pulses=lens),
                  show_title=False)
    for sh, seg in zip(plan['shots'], B.segs):
        sh['handover'] = False
        if seg.get('cine'):
            sh['cine'] = seg['cine']
            # round 6 (critic: a 'still' rendered at Laplacian 366 while its source measures 1019): optical-flow
            # in-betweens for the slowed takes, as the remake renders them (frame blending smeared the stills)
            sh['interp'] = 'flow'
            sh['interp_gate'] = True       # round 10: forward-backward flow consistency gate (footage._flow_mix)
            sh['shutter_soft'] = True      # round 8: a continuous per-frame shutter on the fast-into-slow entries (engine)
    plan['frame'] = 'rot90' if aspect == '9:16' else 'full'
    plan['look'] = plan['grade'] = grade
    plan['audio_design'] = 'none'
    # remake round 5 (critics: the master sounds thin next to the reference's -6.9 LUFS): keep -14 LUFS but with light
    # bus compression and a -1 dBTP ceiling, as the remake plan does
    # (the engine reads the mastering keys from the style: style_overrides.audio, deep-merged)
    plan.setdefault('style_overrides', {}).setdefault('audio', {}).update(
        lufs=_cine_master_lufs(A, start, end, float(opts.get('cine_lufs') or -8.5), float(opts.get('cine_ceiling') or -2.5)),
        ceiling_db=float(opts.get('cine_ceiling') or -2.5),
        bus_comp=dict(thresh_db=-20.0, ratio=1.2, release=0.40))   # round 8: no pumping; round 10: ceiling -2.5 (AAC overshoot -> <= -1 dBTP delivered), ratio 1.2 so the intro / body step survives the master
    # round 10 (critic: -18 / -17.4 LUFS against the reference's -11.1; the platform does not turn a quiet upload up):
    # -8 LUFS integrated on our meter (the critics read ours 7 LU under the reference; the reference master is -6.9 on this meter), true peak <= -1 dBTP
    # remake round 2: the song fade ends with the picture fade (reference: -21 dB 0.17 s into the fade, silent at ~D-0.2)
    # remake round 5 (100 ms RMS vs the reference: -5 dB at D-1.22, -26 at D-0.82, -60 at D-0.42): D-1.32 -> D-0.32,
    # -60 dB, quad 0.625 matches within 1-5 dB
    plan['song_fx'] = [dict(op='fade', t0=round(D - 1.32, 3), t1=round(D - 0.32, 3), db0=0.0, db1=-60.0, quad=0.625,
                            then_out=True)]
    body = round(B.segs[1]['t0'], 4)
    natural = bool(A.get('path')) and A.get('tracker') != 'synthetic' and _cine_breath_riser(A['path'], start + body)
    if env is not None:
        # remake round 6 (critic: the hook lands soft - _sX2's hero follows the song's own 0.47 s near-silence with a
        # noise swell, then the full band on the switch; a low-pass close under a held vocal is inaudible): when the
        # song has no such breath, song_fx builds it from the song itself (gate + reversed downbeat riser)
        # remake round 8 (critic, BLOCKING: the built gate muted the singer's pickup word for 0.35 s - a dropout, not
        # a breath; _sX2's breath is the song's own gap, transition-sound.md: no gap -> a plain hard cut on the
        # downbeat): the built breath only on request (opts cine_breath='built')
        cine_log['breath'] = 'song' if natural else 'hard_cut'
        gi = opts.get('_cine_gap') or {}
        if not natural and gi.get('vocal_gap') is not None and float(gi['vocal_gap']) >= 0.5 and opts.get('cine_breath') != 'none':
            # round 10 (critic, BLOCKING: the new edit has no gap and no level jump into the first clip; _sX2's song
            # falls to -46 dB for ~0.4 s and the clip slams in on the hit +4 dB louder): the song is cut before the hit
            # where the window has no sung word (checked on the lyric times), fade < 30 ms, back on the hit +4 dB
            hit = _cine_peak(env, cuts[0] - lead + CINE_HIT_AFTER[0], cuts[0] - lead + CINE_HIT_AFTER[1])[1]
            plan['song_fx'].insert(0, dict(op='gap_drop', cut=round(hit - 0.012, 4), gap_s=round(min(0.45, float(gi['vocal_gap']) - 0.1), 3),
                                           gate_db=-46.0, fade_s=0.025, return_s=0.008, step_db=4.5, step_tau=0.9,
                                           riser=True, riser_db=-20.0, pre_db=-15.0))   # intro ~13 dB under (the reference holds -13 dB), +4.5 dB on the hit: >= +4 dB RMS step after the master
            cine_log['breath'] = 'gap_drop'
        if not natural and opts.get('cine_breath') == 'built':
            plan['song_fx'].insert(0, dict(op='breath_riser', cut=body, gate_s=0.45, gate_db=-35.0, release=0.06,
                                           riser_s=0.45, hp=4500.0, peak_db=-17.0, peak_before=0.02, tau=0.08,
                                           onset_s=0.3))
    elif opts.get('cine_breath') == 'lowpass' and not _song_breath(A, start + body):
        # _sX2's body cut follows the song's own ~240 ms gap; a song without one gets a short dip into the cut
        # remake round 5 (critic: an -18 dB duck cut into a held vocal reads as a dropout): a low-pass close into
        # the cut instead (3-15 kHz -20 dB over 200 ms, open 40 ms after the switch) keeps the song continuous
        plan['song_fx'].insert(0, dict(op='lowpass', t_close=round(body - 0.24, 3), close_s=0.2,
                                       t_open=round(body + 0.04, 3), open_s=0.06, hi_db=-20.0, mid_db=-10.0))
    if aspect == '16:9':
        # remake round 2: one small watermark bottom centre in the landscape master (the rotated side tags are the 9:16
        # master's placement)
        so = plan.setdefault('style_overrides', {})
        so.setdefault('watermark', dict(style='watermark', pos=(0.5, 0.955), anchor='center', size=0.024, opacity=0.5,
                                        rotate_with_master=False))
    plan['handover'] = False
    if cine_log:
        plan.setdefault('planner', {})['cine'] = cine_log
    return plan


def _show_key(s):
    """The show a shot comes from (packs of one show share it): library 'show' / 'character' tags, else the source."""
    r2 = s.get('r2') if isinstance(s.get('r2'), dict) else {}
    return str(s.get('show') or r2.get('show') or s.get('character') or s.get('source_id') or s.get('path'))


CINE_LUMA = [111.0, 204.0, 50.0, 95.0, 45.0, 130.0]    # _sX2 per-slot luma p50 (hero, move, still, move, still, last)
CINE_SAT = [69.0, 47.0, 66.0, 117.0, 168.0, 82.0]       # _sX2 per-slot HSV saturation p50 (portrait.qa, look none)
_CINE_RED = {}


def _cine_ids(s):
    """Every identity a shot's show can be known by (show / r2 show / source video / character), lower-case."""
    r2 = s.get('r2') if isinstance(s.get('r2'), dict) else {}
    ids = {str(x).lower() for x in (s.get('show'), r2.get('show'), s.get('source_id')) if x}
    # a library pack folder is one show (clips_bluelock, clips/fragrant); generic folder names say nothing
    folder = os.path.basename(os.path.dirname(str(s.get('path') or ''))).lower()
    folder = folder[6:] if folder.startswith('clips_') else folder
    if len(folder) >= 3 and folder not in ('clips', 'shots', 'pool', 'tmp', 'mixed') and not folder.startswith('src'):
        ids.add(folder)
    # the show named in the source title / pack path (packs of one show come from many uploaders: Ai Hoshino and
    # Ruby clips are both Oshi no Ko)
    hay = (str(s.get('source_title') or '') + ' ' + str(s.get('path') or '')).lower().replace(chr(92), '/')
    for kw, show in _SHOW_WORDS:
        if kw in hay:
            ids.add('show:' + show)
    return ids


_SHOW_WORDS = [('oshi no ko', 'oshinoko'), ('oshi_no_ko', 'oshinoko'), ('hoshino', 'oshinoko'), ('kurokawa', 'oshinoko'),
               ('clips_oshi', 'oshinoko'), ('clips_ai', 'oshinoko'), ('jujutsu', 'jjk'), ('gojo', 'jjk'),
               ('sukuna', 'jjk'), ('blue lock', 'bluelock'), ('bluelock', 'bluelock'), ('seishiro', 'bluelock'),
               ('isagi', 'bluelock'), ('demon slayer', 'demonslayer'), ('tanjiro', 'demonslayer'),
               ('tokito', 'demonslayer'), ('shinobu', 'demonslayer'), ('frieren', 'frieren'), ('fern ', 'frieren'),
               ('spy x family', 'spyfamily'), ('spyxfamily', 'spyfamily'), ('yor forger', 'spyfamily'),
               ('clips_yor', 'spyfamily'), ('kaoru', 'fragrant'), ('waguri', 'fragrant'), ('fragrant', 'fragrant'),
               ('薫る花', 'fragrant'), ('lie in april', 'april'), ('clips_april', 'april'), ('bocchi', 'bocchi'),
               ('angel next door', 'angel'), ('mahiru', 'angel'), ('alya', 'alya'), ('your name', 'yourname')]


def _cine_jolt(e):
    """A single-frame pan step / blended frame in a clip's native flow series (> 5x the local median, > 0.3 %W)."""
    e = np.asarray(e, float)
    if len(e) < 7:
        return False
    from scipy.ndimage import median_filter
    m = median_filter(e, 7, mode='nearest')
    return bool(np.any((e > 5.0 * np.maximum(m, 0.03)) & (e > 0.3)))


_CINE_MOOD = {}
_CINE_PEOPLE = {}
_CINE_MOUTH = {}
_CINE_INTERP = {}
_CINE_GHOST = {}


def _cine_mouth(path):
    if path not in _CINE_MOUTH:
        try:
            import portrait as PQ
            _CINE_MOUTH[path] = PQ.mouth_cycles(path)
        except Exception as e:  # noqa: BLE001
            log(f'cinematic: mouth cycles of {os.path.basename(str(path))} failed ({e})')
            _CINE_MOUTH[path] = {}
    return _CINE_MOUTH[path]


def _cine_interp(path):
    if path not in _CINE_INTERP:
        try:
            import interp_qa as IQ
            _CINE_INTERP[path] = IQ.risk(path)
        except Exception as e:  # noqa: BLE001
            log(f'cinematic: interp QA of {os.path.basename(str(path))} failed ({e})')
            _CINE_INTERP[path] = {}
    return _CINE_INTERP[path]


def _cine_ghost_listed(path):
    """refs/cine_ghost.json: {"suffixes": ["clips_frieren/shot_000.mp4", ...]} - clips judged by eye (full-res frames) to carry
    twixtor ghosting (doubled strokes) that no flow check sees."""
    if not _CINE_GHOST:
        try:
            d = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'refs', 'cine_ghost.json'), encoding='utf-8'))
            _CINE_GHOST['s'] = [x.replace(chr(92), '/') for x in d.get('suffixes', [])]
        except Exception:  # noqa: BLE001
            _CINE_GHOST['s'] = []
    pp = str(path).replace(chr(92), '/')
    return any(pp.endswith(x) for x in _CINE_GHOST['s'])


def _cine_people(path):
    """portrait.people of a clip (round 8), memoised; {} when it cannot be measured."""
    if path not in _CINE_PEOPLE:
        try:
            import portrait as PQ
            _CINE_PEOPLE[path] = PQ.people(path)
        except Exception as e:  # noqa: BLE001
            log(f'cinematic: people QA of {os.path.basename(str(path))} failed ({e})')
            _CINE_PEOPLE[path] = {}
    return _CINE_PEOPLE[path]


def _cine_jolt_t(t, e):
    """Time of the first single-frame jump in a native flow series (> 5x the local median and > 0.3 %W), or None."""
    e = np.asarray(e, float)
    if len(e) < 7:
        return None
    from scipy.ndimage import median_filter
    m = median_filter(e, 7, mode='nearest')
    idx = np.where((e > 5.0 * np.maximum(m, 0.03)) & (e > 0.3))[0]
    return float(np.asarray(t, float)[idx[0]]) if len(idx) else None


def _cine_mood(path):
    """(gore, fierce) in 0..1 from SigLIP on 3 frames of a clip (cinematic_slowmo remake round 5: a blood-streaked
    face under a purple glow has no red pixels, and Blue Lock / assassin glares read as action in a soft edit).
    Cached next to the clip; (0, 0) when the model is unavailable."""
    if path in _CINE_MOOD:
        return _CINE_MOOD[path]
    if not os.path.exists(str(path)) or os.environ.get('CINE_MOOD') == '0':
        return (0.0, 0.0)
    cache = path + '.cinemood.json'
    try:
        if os.path.exists(cache):
            d = json.load(open(cache, encoding='utf-8'))
            _CINE_MOOD[path] = (float(d['gore']), float(d['fierce']))
            return _CINE_MOOD[path]
    except Exception:  # noqa: BLE001
        pass
    res = (0.0, 0.0)
    try:
        import cv2
        import torch
        from PIL import Image
        M = _CINE_MOOD.get('_model')
        if M is None:
            from transformers import AutoModel, AutoProcessor
            torch.set_num_threads(min(6, os.cpu_count() or 1))
            name = 'google/siglip-base-patch16-224'
            model, proc = AutoModel.from_pretrained(name).eval(), AutoProcessor.from_pretrained(name)
            G = {'gore': ['an anime character with blood on the face', 'a bloody wound', 'a person covered in blood',
                          'blood dripping'],
                 'clean': ['an anime character with a clean face', 'a gentle anime portrait', 'an anime girl smiling',
                           'a calm anime close-up'],
                 'fierce': ['an angry anime character glaring', 'a menacing villain',
                            'an anime battle power-up with glowing eyes', 'a fight scene'],
                 'soft': ['a soft emotional anime scene', 'a peaceful anime character in soft light',
                          'a beautiful anime portrait', 'a romantic anime moment']}
            texts, idx = [], {}
            for g, v in G.items():
                idx[g] = list(range(len(texts), len(texts) + len(v)))
                texts += v
            with torch.inference_mode():
                te = model.get_text_features(**proc(text=texts, padding='max_length', return_tensors='pt'))
                te = getattr(te, 'pooler_output', te)
                te = te / te.norm(dim=-1, keepdim=True)
            M = _CINE_MOOD['_model'] = (model, proc, te, idx)
        model, proc, te, idx = M
        cap = cv2.VideoCapture(path)
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        ims = []
        for q in (0.2, 0.5, 0.8):
            cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(q * (n - 1))))
            ok, f = cap.read()
            if ok:
                ims.append(Image.fromarray(cv2.cvtColor(f, cv2.COLOR_BGR2RGB)))
        if ims:
            with torch.inference_mode():
                e = model.get_image_features(**proc(images=ims, return_tensors='pt'))
                e = getattr(e, 'pooler_output', e)
                e = e / e.norm(dim=-1, keepdim=True)
                lg = ((e @ te.T) * model.logit_scale.exp() + model.logit_bias).numpy()

            def two(a, b):
                y, z = lg[:, idx[a]].max(1), lg[:, idx[b]].max(1)
                return float((1 / (1 + np.exp(-(y - z) / 3.0))).max())
            res = (round(two('gore', 'clean'), 3), round(two('fierce', 'soft'), 3))
            try:
                json.dump(dict(gore=res[0], fierce=res[1]), open(cache, 'w', encoding='utf-8'))
            except Exception:  # noqa: BLE001
                pass
    except Exception as ex:  # noqa: BLE001
        log(f'cinematic: mood score of {os.path.basename(path)} failed ({ex})')
    _CINE_MOOD[path] = res
    return res


def _cine_panels(path):
    """True for a split-screen / multi-panel frame (round 6: a four-panel band collage passed as a 'still'): >= 2
    inner columns where a vertical edge runs through >= 80 % of the height, on 2 of 3 sampled frames."""
    if path in _CINE_PANELS:
        return _CINE_PANELS[path]
    _CINE_PANELS[path] = False
    try:
        import cv2
        cap = cv2.VideoCapture(path)
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        votes = 0
        for q in (0.2, 0.5, 0.8):
            cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(q * (n - 1))))
            ok, f = cap.read()
            if not ok:
                continue
            g = cv2.cvtColor(cv2.resize(f, (480, 270)), cv2.COLOR_BGR2GRAY).astype(np.float32)
            gx = np.abs(np.diff(g, axis=1))
            cov = (gx > 12).mean(axis=0)
            cov = np.maximum(cov[:-1], cov[1:])[12:-12]
            peaks = np.where(cov > 0.75)[0]
            groups = len(np.unique(peaks // 6)) if len(peaks) else 0
            votes += groups >= 2
        _CINE_PANELS[path] = votes >= 2
    except Exception:  # noqa: BLE001
        pass
    return _CINE_PANELS[path]


_CINE_PANELS = {}


def _cine_red(path):
    """Share of blood-red pixels (hue < 8 or > 172, sat > 140, 40 < value < 200) on 3 frames of a clip."""
    if path in _CINE_RED:
        return _CINE_RED[path]
    v = 0.0
    try:
        import cv2
        cap = cv2.VideoCapture(path)
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        vals = []
        for k in (0.2, 0.5, 0.8):
            cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(k * (n - 1))))
            ok, f = cap.read()
            if not ok:
                continue
            h = cv2.cvtColor(cv2.resize(f, (160, 90)), cv2.COLOR_BGR2HSV)
            r = ((h[..., 0] < 8) | (h[..., 0] > 172)) & (h[..., 1] > 140) & (h[..., 2] > 40) & (h[..., 2] < 200)
            vals.append(float(r.mean()))
        v = max(vals) if vals else 0.0
    except Exception:  # noqa: BLE001
        v = 0.0
    _CINE_RED[path] = v
    return v


def _song_breath(A, t_song):
    """True when the song already dips before t_song (the beat before it >= 6 dB under the 4 beats before that)."""
    bt, ld = np.asarray(A.get('beats') or [], float), np.asarray(A.get('beat_loud_db') or [], float)
    if len(bt) < 6 or len(ld) != len(bt):
        return True
    j = int(np.searchsorted(bt, t_song - 0.05)) - 1
    if j < 5:
        return True
    return bool(ld[j] <= np.mean(ld[j - 5:j - 1]) - 6.0)


def _cine_onsets(path, t0, t1):
    """(times rel. t0, onset strength / its median) of the song over [t0, t1] (librosa spectral flux, hop 256 @
    22.05 kHz) - the unit of the round-6 cut log (_sX2's switch frames sit 48-83 ms before a 2.6-14.7 peak)."""
    try:
        import librosa
        sr = 22050
        y = _cine_audio(path, t0, max(1.0, t1 - t0), sr).astype(np.float32)
        env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=256)
        env = env / (float(np.median(env[env > 0])) + 1e-9)
        return np.arange(len(env)) * 256 / sr + (max(0.0, t0) - t0), env
    except Exception as e:  # noqa: BLE001
        log(f'cinematic: no onset envelope ({e})')
        return None


def _cine_peak(env, a, b):
    """(strength, time) of the strongest onset in [a, b] (seconds on env's axis)."""
    t, e = env
    m = (t >= a) & (t <= b)
    if not m.any():
        return 0.0, (a + b) / 2
    i = int(np.argmax(np.where(m, e, -1.0)))
    return float(e[i]), float(t[i])


CINE_HIT_AFTER = (0.02, 0.13)     # _sX2: the song's accent lands 48-83 ms after every switch frame


def _cine_pick_pulse(env, P, drop, lead):
    """Remake round 6 (critic: a 0.92 s pulse = three 8ths of a 4/4 song put every second cut on the off-beat): the
    pulse is the multiple k x P of the tracked beat (0.55-1.45 s) whose cut grid from the body downbeat lands the
    most song accent 20-130 ms after the switch frames, nearest 1.0 s on a tie. -> (pulse, n_pulses, log)"""
    cands = []
    t_, e_ = env
    e0 = e_ - e_.mean()
    ac = np.correlate(e0, e0, 'full')[len(e0) - 1:]
    ac = ac / (ac[0] + 1e-9)
    dt = float(t_[1] - t_[0]) if len(t_) > 1 else 0.0116
    for k in range(1, 9):
        pulse = k * P
        if not 0.55 <= pulse <= 1.45:
            continue
        # the song's own periodicity at this pulse (the meter: 'blue' and _sX2's song both peak at 3 eighths,
        # 0.44 / 0.24, against 0.16 / 0.08 at 4 eighths)
        acp = float(ac[min(len(ac) - 1, int(round(pulse / dt)))])
        npul = 10       # round 6: _sX2's body is always [4] [1] x4 [2] = six shots (six shows), whatever the pulse
        lens = [4] + [1] * max(1, npul - 6) + [2]
        cs, t = [], drop
        for n in lens[:-1]:
            cs.append(t)
            t += n * pulse
        cs.append(t)
        st = [_cine_peak(env, c - lead + CINE_HIT_AFTER[0], c - lead + CINE_HIT_AFTER[1])[0] for c in cs[1:]]
        sc = float(np.mean(st)) + 0.5 * float(np.min(st)) - 2.0 * abs(pulse - 1.0) + 8.0 * acp
        cands.append((sc, pulse, npul, [round(x, 2) for x in st], round(acp, 3)))
    if not cands:
        return None
    best = max(cands, key=lambda c: c[0])
    return best[1], best[2], [dict(pulse=round(c[1], 4), score=round(c[0], 2), hits=c[3], autocorr=c[4]) for c in cands]


def _cine_moment_cuts(A, start, d, end, lead, opts, rng):
    """Slow-mo cuts on musical moments (2026-10-19), the rule measured on _sX2 (music_moments.align: 6 / 6 cuts on the
    main beat of the song's meter, 6 / 6 on a moment - 5 sung syllables + 1 bar start -, the moment ~66 ms after the switch
    frame, the hero hold 4 main beats): a DP over the main beats after the hero picks the shots (1-3 beats each, the hero
    3-4) maximising the moment strength at every switch (phrase start 3, sung syllable <= 3, accent 1.2, bar 1, plain beat
    -0.8), mildly against runs of 1-beat shots (not every beat). -> (cuts, lens, kinds, lifts, main, log) or None"""
    try:
        import music_moments as MM
        M = MM.moments(A['path'], float(start), float(end - start) + 0.8, vocals=True, threads=2)
    except Exception as e:  # noqa: BLE001
        log(f'cinematic: no musical moments ({type(e).__name__}: {e}): the pulse grid stays')
        return None
    off = float(start)
    main = float(M['main'])
    beats = [float(b) + off for b in M['beats']]
    if len(beats) < 6 or not 0.5 <= main <= 1.6:
        log(f'cinematic: moment grid unusable (main {main}, {len(beats)} beats): the pulse grid stays')
        return None
    mom = ([float(p[0]) + off for p in M['phrase_starts']] + [float(v[0]) + off for v in M['vocal_onsets'] if v[1] >= MM.VOC_PROM]
           + [float(a[0]) + off for a in M['accents'] if a[1] >= 3.0])

    def sc_of(c):
        sc, kind, _ = MM.score_at(M, c - off - lead)
        return (sc if kind in ('phrase', 'vocal', 'accent', 'bar') else sc - 1.2), kind

    def best_at(b, win=0.1):
        cs = [b] + [m for m in mom if abs(m - b) <= win]
        return max(((c,) + sc_of(c) for c in cs), key=lambda z: z[1])
    # the hero (first clip) lands on the vocal phrase start measured in THIS audio (_sX2: the hero switch 0.06 s before the
    # line's first syllable). The lyric-timing window search can sit mid-phrase (3b: 0.6 s after the line had started under
    # the black intro): every main beat / phrase start within -0.9..+0.6 s of d competes, the intro stays >= 4.2 s
    hc = [best_at(b) for b in beats if d - 0.9 <= b <= d + 0.6]
    hc += [(p_,) + sc_of(p_) for p_ in [float(x[0]) + off for x in M['phrase_starts']] if d - 0.9 <= p_ <= d + 0.6]
    hc = [h for h in hc if h[0] - off >= 4.2] or [(d,) + sc_of(d)]
    hero = max(hc, key=lambda h: h[1] + (1.5 if h[2] == 'phrase' else 0.0) - 0.5 * abs(h[0] - d))
    cand = [hero] + [best_at(b) for b in beats if b > hero[0] + 0.6 * main and b <= end - 1.5]
    if len(cand) < 6:
        return None
    body = end - hero[0]
    max_shots = int(opts.get('cine_max_shots') or 99)
    n_lo = max(6, int(math.ceil(body / 2.9)))
    n_hi = min(max_shots, int(body / 1.7))
    if n_hi < n_lo:
        n_lo = min(n_lo, n_hi)
    # beat index of every candidate (the main-beat grid may drift: index by position in the list)
    NEG = -1e9
    best = {}                       # (i, n, lastgap) -> (value, prev key)
    best[(0, 1, 0)] = (0.0, None)
    for i in range(len(cand)):
        for key in [k for k in list(best) if k[0] == i]:
            v0, _ = best[key]
            _, n, lg = key
            gaps = (3, 4) if i == 0 else (1, 2, 3)
            for g in gaps:
                j = i + g
                if j >= len(cand):
                    continue
                # owner: not every beat - a 1-beat shot only on a strong moment, never three in a row
                pen = (-0.7 - (1.2 if lg == 1 else 0.0)) if g == 1 else (-0.1 if g == 3 else 0.0)
                v = v0 + cand[j][1] + pen
                nk = (j, n + 1, g)
                if nk not in best or best[nk][0] < v:
                    best[nk] = (v, key)
    fin = []
    for key, (v, _) in best.items():
        j, n, _ = key
        tail = end - cand[j][0]
        if 1.5 <= tail <= 3.6 and n_lo <= n + 0 <= n_hi:        # n counts the shots incl. the last one (it starts at j)
            fin.append((v + 0.0, key))
    if not fin:
        log(f'cinematic: no moment-cut layout fits {n_lo}-{n_hi} shots: the pulse grid stays')
        return None
    v, key = max(fin)
    path = []
    while key is not None:
        path.append(key)
        key = best[key][1]
    path.reverse()
    idx = [k[0] for k in path]
    cuts = [round(cand[i][0], 4) for i in idx]
    lens = [idx[a + 1] - idx[a] for a in range(len(idx) - 1)] + [max(1, int(round((end - cuts[-1]) / main)))]
    pat = ['move', 'still', 'move', 'move', 'still']
    kinds, k = [], 0
    for a, i in enumerate(idx):
        if a == 0:
            kinds.append('hero')
        elif a == len(idx) - 1:
            kinds.append('last')
        elif cand[i][2] == 'phrase' and lens[a] >= 2:
            kinds.append('hero')
        else:
            kinds.append(pat[k % len(pat)])
            k += 1
    # the emotional lift: the strongest accent among the second-half cuts gets the brighter bump
    acc = {round(float(a[0]) + off, 3): float(a[1]) for a in M['accents']}
    half = [a for a in range(1, len(cuts) - 1) if cuts[a] >= hero[0] + body / 2]
    lifts = []
    if half:
        def acc_near(c):
            return max([s_ for t_, s_ in acc.items() if -0.03 <= t_ - c <= 0.14] or [0.0])
        la = max(half, key=lambda a: acc_near(cuts[a]))
        lifts = [sum(lens[:la])]
    rows, st = MM.align([c - off - lead for c in cuts], M)
    info = dict(main=round(main, 4), k=M['k'], meter_ac=M['meter_ac'], value=round(v, 2), n_range=[n_lo, n_hi], stats=st,
                cuts=[dict(t=c, kind=cand[i][2], score=round(cand[i][1], 2)) for c, i in zip(cuts, idx)])
    # the plan's cut times are edit seconds (song seconds - start), as the pulse grid's
    return [round(c - off, 4) for c in cuts], lens, kinds, lifts, main, info


def _pin_bounds(curve, hero=False, last=False):
    """(r_min, r_speed, r_max) of an approved shot's new length / old length: below r_speed its source path is not sped
    up any further (past 1.3 x real-time source speed, or 0.95 for a shot already faster: it would leave slow motion) but
    TRIMMED at the end (down to r_min = 0.5, 0.6 for the closing shot); a near-still portrait (source < 0.6 x real time) takes 0.5-1.8, the others
    up to HC_RATIO[1]; the hero never gets shorter (_sX2: 4 main beats)"""
    rt = (float(curve[-1][1]) - float(curve[0][1])) / max(0.1, float(curve[-1][0]))
    if rt < 0.6:
        lo, hi = 0.5, 1.8
    else:
        lo, hi = max(HC_RATIO[0], min(0.95, rt / 1.3)), HC_RATIO[1]
    if hero:
        return 1.0, max(1.0, lo), hi
    return (0.6 if last else 0.5), lo, hi          # the closing shot fades out over its last 1.2 s: keep >= 0.6


def _retime_curve(curve, dur, fps, r_speed=None):
    """a shot's speed curve [[t, src], ...] stretched in time to `dur` (same source in / out), on the frame grid; with
    r_speed (> dur / old length) it is stretched by r_speed only and its end trimmed (same entry, same speed)"""
    ct = np.asarray([float(p[0]) for p in curve])
    cs = np.asarray([float(p[1]) for p in curve])
    od = max(1e-6, float(ct[-1]))
    k = dur / od
    if r_speed is not None and r_speed > k:
        k = float(r_speed)
    n = max(1, int(round(dur * fps)))
    tt = np.arange(n + 1) / float(fps)
    tt[-1] = dur
    return [[round(float(t), 5), round(float(np.interp(t / k, ct, cs)), 5)] for t in tt]


# the measured cut rule of the slow-mo reference (_sX2EUexbW8, 2026-10-19 round 2; refs/new-types-progress.md "Slow-mo
# cut timing round 2"): every switch frame 0.03-0.14 s (median 0.10) BEFORE a new sung note, 0.05-0.10 s before an
# accompaniment attack, on the main beat, never inside a held note; one held note per body shot
HC_PRE = 0.09            # switch -> the sung onset / attack it announces
HC_WIN = (0.0, 0.17)     # a turn counts for a switch when it follows it by this much
HC_RATIO = (0.68, 1.5)   # a pinned shot's new length / its approved length (its source path is time-scaled)
HC_EXTEND = 1.2          # a pinned edit may end up to this much later (its closing shot keeps its length)


def _cine_hold_cuts(A, start, d, end, lead, opts, rng, pin=None):
    """Hold-aware slow-mo cuts (owner 2026-10-19 round 2: never cut while the singer holds a note / word; cut where the
    music turns). Measured on the audio actually heard (music_moments.holds: sung notes from the Demucs vocal stem with a
    pitch / energy tracker, instrumental sustains / swells in the vocal gaps). Candidate switch times: HC_PRE before every
    sung note onset and every accompaniment attack, and every main beat, minus the ones inside a hold body; a DP over the
    candidates scores each switch (the turn it lands on: phrase start 3, a note after a hold 2.4, a syllable 1.5, + the
    attack, + the main beat, - a mid-word switch) and each shot length (pinned clips: close to the approved length, the
    source path time-scaled within HC_RATIO; else the reference's lengths: hero 3-4.5 main beats, body 0.8-2.7).
    -> (cuts (edit s, switch + lead), lens (main beats), kinds, lifts, main, log) or None"""
    try:
        import music_moments as MM
        # (+ 2 s: a pinned closing shot may run up to HC_EXTEND past the old end)
        span = float(end - start) + (0.8 + HC_EXTEND if pin else 0.8)
        M = MM.moments(A['path'], float(start), span, vocals=True, threads=2)
        H = MM.holds(A['path'], float(start), span, threads=2, M=M, strict=True)
    except Exception as e:  # noqa: BLE001
        log(f'cinematic: no hold analysis ({type(e).__name__}: {e})')
        return None
    main = float(M['main'])
    D = float(end - start)
    hero = round(float(d - start) - lead, 4)            # the hero switch (edit s): kept (the words-on-black intro ends here)
    notes = H['notes']
    att = [(float(a), float(s_)) for a, s_ in H['inst']['attacks'] if s_ >= 2.5]

    def turn_score(sw):
        sc, why = 0.0, []
        nn = [k for k, x in enumerate(notes) if HC_WIN[0] <= x['a'] - sw <= HC_WIN[1]]
        if nn:
            k = nn[0]
            x = notes[k]
            pv = notes[k - 1] if k > 0 else None
            gap = x['a'] - pv['b'] if pv else 9.0
            if gap >= 0.25:
                sc += 3.0
                why.append('phrase')
            elif pv is not None and pv['hold']:
                sc += 2.4
                why.append('after_hold')
            else:
                sc += 1.5
                why.append('syllable')
            if pv is not None and x['midi'] is not None and pv['midi'] is not None and abs(x['midi'] - pv['midi']) >= 1.0:
                sc += 0.4
        aa = [s_ for a, s_ in att if HC_WIN[0] <= a - sw <= HC_WIN[1]]
        if aa:
            sc += min(1.2, 0.2 * max(aa))
            why.append('attack')
        ph = MM.meter_phase(M, sw)
        if ph is not None:
            sc += 1.0 if ph <= 0.08 else (0.5 if ph <= 0.15 else -0.3)
        if [x for x in notes if x['a'] + 0.1 < sw and x['b'] - 0.16 > sw]:
            sc -= 1.5                                       # mid-word (a short note): no turn there
            why.append('mid_word')
        return sc, '+'.join(why) or 'beat'
    raw = ([x['a'] - HC_PRE for x in notes] + [float(a) - HC_PRE for a, s_ in H['inst']['attacks'] if s_ >= 2.0]
           + [float(b) - MM.LEAD for b in M['beats']])
    D0 = D
    raw = sorted(t for t in raw if hero + 0.5 < t < D - 0.8)
    cands, last = [], -9.0
    for t in raw:
        if any(MM.in_hold(H, t + e) is not None for e in (-0.06, 0.0, 0.06)):
            continue                    # (+-60 ms margin: a hold edge moves a little between two analyses of the same song)
        sc, why = turn_score(t)
        if cands and t - last < 0.04:
            if sc > cands[-1][1]:
                cands[-1] = (t, sc, why)
                last = t
            continue
        cands.append((t, sc, why))
        last = t
    # a switch that lands on nothing (no sung onset, no attack, off the beat) reads as random: costs extra
    cands = [(t, sc - (1.5 if sc < 1.0 else 0.0), why) for t, sc, why in cands]
    cands = [(hero, 0.0, 'hero')] + cands
    if os.environ.get('HC_DEBUG'):
        log('hold cuts: forbid %s' % json.dumps(H['forbid']))
        log('hold cuts: candidates %s' % json.dumps([(round(c[0], 3), round(c[1], 2), c[2]) for c in cands]))
    if pin:
        old = [float(x['t0']) for x in pin['shots'][1:]] + [float(pin['duration'])]
        targ = [old[k + 1] - old[k] for k in range(len(old) - 1)]
        roles = [x.get('role') for x in pin['shots'][1:]]
        bnd = [_pin_bounds(x['speed_curve'], hero=(k == 0), last=(k == len(pin['shots']) - 2))
               for k, x in enumerate(pin['shots'][1:])]
        # a speed change shows on a moving take, hardly on a near-still portrait
        w_sp = [4.0 if (float(x['speed_curve'][-1][1]) - float(x['speed_curve'][0][1])) / max(0.1, float(x['speed_curve'][-1][0])) < 0.6
                else 15.0 for x in pin['shots'][1:]]
        r_lo, r_sp, r_hi = [b[0] for b in bnd], [b[1] for b in bnd], [b[2] for b in bnd]
    else:
        n_lo = max(6, int(math.ceil((D - hero) / (2.4 * main))))
        n_hi = min(int(opts.get('cine_max_shots') or 99), int((D - hero) / (1.2 * main)))
        targ, roles = None, None
    NEG = -1e18

    def fit(k, dur):
        if targ is not None:
            r = dur / targ[k]
            if not r_lo[k] <= r <= r_hi[k] or dur < 0.75:
                return NEG                     # (_sX2's shortest shot is one main beat, 1.0 s)
            re = max(r, r_sp[k])
            # the speed change of the approved source path, + the share of it trimmed off the end
            return -w_sp[k] * math.log(re) ** 2 - 8.0 * (1.0 - r / re)
        b = dur / main
        if k == 0:
            return NEG if not 2.9 <= b <= 4.6 else -0.3 * abs(b - 4.0)
        if not 0.8 <= b <= 2.7:
            return NEG
        return -0.25 * abs(b - 1.6) - (0.5 if b < 1.15 else 0.0)
    C = len(cands)
    tt = np.asarray([c[0] for c in cands])
    if targ is not None:
        N = len(targ)
        V = np.full((N, C), NEG)
        P = np.full((N, C), -1, int)
        V[0, 0] = 0.0
        for k in range(1, N):
            lo_d, hi_d = targ[k - 1] * r_lo[k - 1], targ[k - 1] * r_hi[k - 1]
            for j in range(1, C):
                best, bi = NEG, -1
                for i in np.where((tt[j] - tt >= lo_d) & (tt[j] - tt <= hi_d) & (V[k - 1] > NEG / 2))[0]:
                    v = V[k - 1, i] + fit(k - 1, tt[j] - tt[i]) + cands[j][1]
                    if v > best:
                        best, bi = v, int(i)
                V[k, j], P[k, j] = best, bi
        fin = [(V[N - 1, j] + fit(N - 1, D - tt[j]), j, D) for j in range(C) if V[N - 1, j] > NEG / 2]
        for j in range(C):
            # or the closing shot keeps (>= 0.9 of) its approved length and the edit ends later (<= HC_EXTEND): a mild cost
            if V[N - 1, j] > NEG / 2 and D < tt[j] + 0.9 * targ[N - 1] <= D + HC_EXTEND:
                De = round(max(D, tt[j] + targ[N - 1]) if tt[j] + targ[N - 1] <= D + HC_EXTEND else D + HC_EXTEND, 3)
                fin.append((V[N - 1, j] + fit(N - 1, De - tt[j]) - 1.0 - 0.8 * (De - D), j, De))
        fin = [f for f in fin if f[0] > NEG / 2]
        if not fin:
            kk = max([k for k in range(N) if (V[k] > NEG / 2).any()] or [0])
            log('cinematic: hold-aware cuts: no layout keeps every pinned shot within its length range (reached shot %d of %d, '
                'last cut <= %.2f s, %d candidates)' % (kk, N, max(tt[V[kk] > NEG / 2]), C))
            return None
        v, j, D = max(fin)
        idx = [j]
        for k in range(N - 1, 0, -1):
            j = int(P[k, j])
            idx.append(j)
        idx.reverse()
    else:
        best = {(0, 1): (0.0, None)}
        for j in range(1, C):
            for i in range(j):
                f = fit(0 if i == 0 else 1, tt[j] - tt[i])
                if f <= NEG / 2:
                    continue
                for n in range(1, n_hi):
                    if (i, n) not in best:
                        continue
                    v = best[(i, n)][0] + f + cands[j][1]
                    if (j, n + 1) not in best or best[(j, n + 1)][0] < v:
                        best[(j, n + 1)] = (v, (i, n))
        fin = [(v + fit(1, D - tt[j]), (j, n)) for (j, n), (v, _) in best.items()
               if n_lo <= n <= n_hi and 1.5 <= D - tt[j] <= 3.6]
        fin = [f for f in fin if f[0] > NEG / 2]
        if not fin:
            log('cinematic: hold-aware cuts: no free layout fits')
            return None
        v, key = max(fin)
        idx = []
        while key is not None:
            idx.append(key[0])
            key = best[key][1]
        idx.reverse()
    sw = [cands[i][0] for i in idx]
    rows = MM.cut_report(sw, H, end=D)
    nin = sum(r['in_hold'] is not None for r in rows)
    cuts = [round(t + lead, 4) for t in sw]
    lens = [max(1, int(round((b - a) / main))) for a, b in zip(sw, sw[1:] + [D])]
    if roles:
        kinds = list(roles)
    else:
        pat = ['move', 'still', 'move', 'move', 'still']
        kinds = ['hero'] + [pat[k % len(pat)] for k in range(len(sw) - 2)] + ['last']
    half = [a for a in range(1, len(sw) - 1) if sw[a] >= hero + (D - hero) / 2]
    lifts = []
    if half:
        la = max(half, key=lambda a: max([s_ for t_, s_ in att if 0.0 <= t_ - sw[a] <= 0.17] or [0.0]))
        lifts = [sum(lens[:la])]
    offs = [r['to_next_onset'] if r['to_next_onset'] is not None and r['to_next_onset'] <= 0.35 else
            (r['acc_attack'][0] if r['acc_attack'] else None) for r in rows[1:]]
    info = dict(main=round(main, 4), value=round(float(v), 2), in_hold=nin, n=len(sw), pinned=bool(pin), D=round(float(D), 4),
                D_old=round(D0, 4),
                hold_null=MM.hold_null(H, hero, D),
                cuts=[dict(sw=round(t, 3), score=round(cands[i][1], 2), why=cands[i][2]) for t, i in zip(sw, idx)],
                ratio=([round((b - a) / targ[k], 3) for k, (a, b) in enumerate(zip(sw, sw[1:] + [D]))] if targ else None),
                offsets=offs)
    return cuts, lens, kinds, lifts, main, info


# owner 2026-10-19 round 3 ("momentum"): ONE clip carries ONE wave of the sung line, the switch sits at the start of the
# next wave (the reference: 0.03-0.16 s before the attack, on the main beat); the number of shots follows the waves
WV_PRE = 0.10            # switch -> the wave's attack (music_moments.waves times the attack itself; _sX2 median 0.10)
WV_SNAP = (0.03, 0.16)   # a main beat whose switch (beat - LEAD) leads the attack by this much takes the switch
WV_SHOT = (0.70, 5.2)    # 2026-10-21: floor 0.75 -> 0.70 = the judge's JW_MIN_SHOT; shot length limits (s): _sX2's shortest body shot is 1.0 s, its held "shine" 4.0 s (slowed 4.7)
WV_MUST = 2.0            # a wave this strong must get a switch (a phrase / a new word after a dip / a release)
WV_CUT_MIN = 1.3         # a wave weaker than this earns no switch (the tail's faint lines: 3g cut 4 of them)
WV_SPLIT = 5.2           # a longer stretch with no wave start (an instrumental tail) splits at a strong attack / bar


CINE_MAX_LIMIT_DB = 3.0


def _cine_master_lufs(A, start, end, lufs, ceiling, max_lim=CINE_MAX_LIMIT_DB):
    """2026-10-21 (CI dry run "blue" @v0.9: a dynamic master, -12.5 LUFS / -1.0 dBTP in the window): the -6.8 LUFS target
    needed ~7 dB of peak limiting, the engine's look-ahead limiter flattened the mix (quiet bars +17 dB vs the song, the
    intro duck gone) and the judge read 12 gain jumps (9_no_sfx 0). The target is capped so the limiter takes <= max_lim dB
    off the song's peaks: lufs <= song LUFS + (ceiling - song true peak) + max_lim. Loud masters (Golden Hour: 2.9 dB) keep it."""
    try:
        _md = os.path.join(HERE, '..', 'meme')
        if _md not in sys.path:
            sys.path.append(_md)
        import acapella as AC
        r = subprocess.run(['ffmpeg', '-v', 'error', '-ss', f'{max(0.0, float(start)):.4f}', '-t', f'{float(end - start):.4f}',
                            '-i', str(A['path']), '-ac', '2', '-ar', str(AC.SR), '-f', 'f32le', '-'], capture_output=True).stdout
        x = np.frombuffer(r, np.float32).reshape(-1, 2).T.astype(np.float64)
        if x.shape[1] < AC.SR:
            return lufs
        L, tp = float(AC.lufs(x)), 20 * math.log10(max(1e-9, float(AC.true_peak(x))))
        cap = L + (ceiling - tp) + max_lim
        if cap < lufs:
            log(f'cinematic: master {lufs} LUFS -> {cap:.1f} (song window {L:.1f} LUFS / {tp:.1f} dBTP: <= {max_lim} dB of limiting)')
            return round(cap, 2)
    except Exception as e:  # noqa: BLE001
        log(f'cinematic: master cap failed ({type(e).__name__}: {e})')
    return lufs


def _cine_wave_cuts(A, start, d, end, lead, opts, rng, pin=None):
    """Momentum cuts: music_moments.waves() on the song window actually heard (the Demucs vocal stem: loudness valleys,
    pyin pitch continuity, vocal onsets; one sustained note across small dips = one wave), one switch at the start of
    every wave (the hero stays: the words-on-black intro ends there), a DP keeps the shots within WV_SHOT and never
    drops a strong wave start unless two are closer than the shortest shot. -> (cuts, lens, kinds, lifts, main, log)"""
    try:
        import music_moments as MM
        span = float(end - start) + 0.8
        M = MM.moments(A['path'], float(start), span, vocals=True, threads=2)
        # the version's speed (x the original): a faster version than the 0.85 calibration is analysed at that tempo
        # (owner 2026-10-20 on 3h, the regular version: wave starts one syllable early, 0.4-0.6 s off after 15 s)
        Wv = MM.waves(A['path'], float(start), span, threads=2, speed=opts.get('cine_song_speed'))
    except Exception as e:  # noqa: BLE001
        log(f'cinematic: no wave analysis ({type(e).__name__}: {e})')
        return None
    main = float(M['main'])
    D = float(end - start)
    hero = round(float(d - start) - lead, 4)
    beats = np.asarray(M.get('beats') or [], float)
    acc = [(float(a), float(s_)) for a, s_ in M.get('accents') or []]

    # groove songs (a steady drum / bass pulse, music_moments.groove): the switch sits ON the beat or tight on the sung attack
    # (2026-10-22, owner on "blue" + the blue reference edits; Golden Hour has no such pulse and keeps the approved rule)
    gm = os.environ.get('SLOWMO_GROOVE', 'auto')
    G = None
    if gm != '0':
        try:
            G = MM.groove(A['path'], float(start), span)
            if gm == '1':
                G['groove'] = True
        except Exception as e:  # noqa: BLE001
            log(f'cinematic: groove analysis failed ({type(e).__name__}: {e})')
    grv = bool(G and G.get('groove'))

    def switch_for(a):
        """the switch frame for a wave attack at a: on a main beat when one leads it by WV_SNAP, else WV_PRE before.
        groove v2 (2026-10-23, the real "blue" edits): GROOVE_PRE before the sung attack, no beat snap (the refs cut 0..0.06 s
        before the attack; the 0.10 s lead / beat - LEAD snap of the ballad rule put our r6 0.08-0.11 s early on 10 / 23 cuts)"""
        if grv:
            return a - MM.GROOVE_PRE, False
        if len(beats):
            sw_b = beats - MM.LEAD
            ok = [x for x in sw_b if WV_SNAP[0] <= a - x <= WV_SNAP[1]]
            if ok:
                return float(min(ok, key=lambda x: abs(a - x - WV_PRE))), True
        return a - WV_PRE, False
    # 2026-10-20 (owner on 3i): a held sung note is ONE wave including its decay tail (the harmony rings on it), and the
    # final section (the band drops out: 3i's violin outro) holds ONE clip to the end
    tails, outro = [], None
    if os.environ.get('SLOWMO_HOLD_TAILS', '1') != '0':
        try:
            tails = MM.hold_tails(A['path'], float(start), D, Wv, from_t=hero + 0.6)
            outro = MM.outro_start(A['path'], float(start), D)
        except Exception as e:  # noqa: BLE001
            log(f'cinematic: hold tails / outro failed ({type(e).__name__}: {e})')
    tails = [z for k_, z in enumerate(tails) if not any(y['a'] < z['a'] < y['end'] for y in tails[:k_])]

    def in_tail(t):
        return any(z['a'] + 0.05 < t < z['end'] for z in tails)
    out_a = None
    if outro is not None:
        near_o = [x['a'] for x in Wv['waves'] if outro - 0.2 <= x['a'] <= outro + 0.5]
        out_a = float(min(near_o)) if near_o else outro + 0.1
    cands = []
    for x in Wv['waves']:
        if x['a'] - WV_PRE <= hero + 0.6 or x['a'] - WV_PRE >= D - 0.9:
            continue
        if in_tail(x['a']) or (out_a is not None and x['a'] >= out_a - 0.05):
            continue
        sw, on_b = switch_for(float(x['a']))
        st0 = float(x['strength'])
        if grv and not str(x['why']).startswith('phrase') and any(k in str(x['why']) for k in ('legato', 'step')):
            # groove v2: a word that glides on from the previous one (legato / stepwise) continues the phrase's momentum -
            # the real blue edits cut 1 in 5 of these vs about 1 in 2 of the other word starts: optional, never a must-switch
            st0 = min(st0, WV_MUST - 0.1) * 0.6
        cands.append(dict(sw=round(sw, 4), a=x['a'], b=x['b'], strength=st0, why=x['why'], beat=on_b))
    if out_a is not None:
        sw, on_b = switch_for(out_a)
        cands.append(dict(sw=round(sw, 4), a=round(out_a, 3), b=None, strength=3.0, why='outro', beat=on_b))
    # long stretches without a wave start (an instrumental tail): the strongest accompaniment accent / bar inside (never
    # inside a held note's tail, never in the outro)
    pts = sorted([hero] + [c['sw'] for c in cands] + [D])
    for p0, p1 in zip(pts[:-1], pts[1:]):
        if p1 - p0 > WV_SPLIT:
            inn = [(a - MM.LEAD, s_) for a, s_ in acc if p0 + 1.5 <= a - MM.LEAD <= p1 - 1.5 and s_ >= 3.0
                   and not in_tail(a) and (out_a is None or a < out_a - 0.3)]
            if inn:
                t_, s_ = max(inn, key=lambda z: z[1])
                cands.append(dict(sw=round(t_, 4), a=round(t_ + MM.LEAD, 3), b=None, strength=1.0, why='inst_attack', beat=True))
    cands.sort(key=lambda c: c['sw'])
    C = [dict(sw=hero, strength=0.0, why='hero', beat=True, a=hero + WV_PRE)] + cands
    n = len(C)
    NEG = -1e18
    V = np.full(n, NEG)
    P = np.full(n, -1, int)
    V[0] = 0.0

    def gain(c):
        # a weak wave (a faint / fading line, strength < WV_CUT_MIN) is not worth a switch of its own: the clip carries on
        return c['strength'] - WV_CUT_MIN + (0.4 if c['beat'] else 0.0)

    def skip_cost(i, j):
        # the wave starts left inside a shot: a strong one costs heavily, a middling one a little, a faint one nothing
        return sum((8.0 if C[k]['strength'] >= WV_MUST else max(0.0, C[k]['strength'] - WV_CUT_MIN)) for k in range(i + 1, j))

    tsc = float(Wv.get('tscale') or 1.0)     # the shortest body shot scales with the tempo like the waves do

    def tail_max(i):
        # a shot that starts on a held note may carry its whole decay tail
        z = [y['end'] for y in tails if abs(y['a'] - C[i]['a']) < 0.06]
        return (max(z) - C[i]['sw'] + 0.4) if z else 0.0

    def len_cost(L, first=False, i=None, j=None):
        # 2026-10-21 (CI dry run "blue" @v0.9): the hero's 2.4 s floor skipped the line start "I'll imagine" 1.7 s after the
        # drop; a strong wave (a new line / word) may end the hero shot from 1.4 s. The body floor = the independent
        # judge's JW_MIN_SHOT (0.70 s at the reference tempo): two strong starts 0.70-0.75 s apart ("leave" 0.76 s after
        # "I'll", the beat snap pulled it to 0.70) were unswitchable here and 'missed' there
        lo = (1.4 * tsc if (j is not None and C[j]['strength'] >= WV_MUST) else 2.4) if first else WV_SHOT[0] * tsc
        hi = max(WV_SHOT[1] + (1.0 if first else 0.0), tail_max(i) if i is not None else 0.0)
        if L < lo or L > hi:
            return None
        return 0.6 if L < 0.95 * tsc else 0.0
    # 2026-10-21 (first unpinned CI dry run, "blue": 27 shots for 22 clean clips -> one clip shown 5 times): never more shots
    # than clean clips (cine_max_shots, each clip once). The shot count is a dimension of the DP (exact: the best layout with
    # <= max_sh shots). A per-switch cost (the first fix) fell off a cliff: once a switch cost more than a skipped strong
    # wave (8) the layout collapsed to 9 shots for 26 clips.
    max_sh = int(opts.get('cine_max_shots') or 999)
    K = max(1, min(max_sh, n))
    VK = np.full((K + 1, n), NEG)                     # VK[k, j]: best value with the k-th shot starting at candidate j
    PK = np.full((K + 1, n), -1, int)
    VK[1, 0] = 0.0
    LC = {}
    for j in range(1, n):
        for i in range(j):
            lc = len_cost(C[j]['sw'] - C[i]['sw'], first=(i == 0), i=i, j=j)
            if lc is not None:
                LC[(i, j)] = gain(C[j]) - lc - skip_cost(i, j)
    for k in range(2, K + 1):
        for (i, j), w in LC.items():
            if VK[k - 1, i] <= NEG / 2:
                continue
            v = VK[k - 1, i] + w
            if v > VK[k, j]:
                VK[k, j], PK[k, j] = v, i
    fin = []
    for k in range(1, K + 1):
        for j in range(n):
            if VK[k, j] <= NEG / 2:
                continue
            L = D - C[j]['sw']
            if out_a is not None and C[j]['why'] != 'outro':
                continue                              # the outro holds the last clip to the end
            if 1.2 <= L <= max(WV_SHOT[1] + 1.5, tail_max(j), (D - C[j]['sw'] + 0.01) if C[j]['why'] == 'outro' else 0.0):
                fin.append((VK[k, j] - skip_cost(j, n), k, j))
    if not fin:
        log('cinematic: wave cuts: no layout fits')
        return None
    v, k, j = max(fin)
    idx = []
    while j >= 0 and k >= 1:
        idx.append(j)
        j, k = (int(PK[k, j]), k - 1) if k > 1 else (-1, 0)
    idx.reverse()
    if max_sh < 999 and len(idx) == max_sh:
        log(f'cinematic: wave cuts limited to {max_sh} clean clips: {len(idx)} shots')
    res_ = (v, idx)
    v, idx = res_
    if len(idx) > max_sh:
        log(f'cinematic: wave cuts need {len(idx)} shots for {max_sh} clean clips: no layout')
        return None
    sw = [C[i]['sw'] for i in idx]
    st_ = [C[i]['strength'] for i in idx]
    skipped = [dict(a=C[k]['a'], strength=C[k]['strength'], why=C[k]['why']) for k in range(1, n) if k not in idx]
    repair = None
    if not pin and os.environ.get('SLOWMO_JUDGE_REPAIR', '1') != '0':
        sw, st_, repair = _cine_judge_repair(A, start, D, sw, st_, opts.get('cine_song_speed'), WV_SHOT[0] * tsc, max_sh)
    cuts = [round(t + lead, 4) for t in sw]
    lens = [max(1, int(round((b - a) / main))) for a, b in zip(sw, sw[1:] + [D])]
    pat = ['move', 'still', 'move', 'move', 'still']
    kinds = ['hero'] + [pat[k % len(pat)] for k in range(len(sw) - 2)] + ['last']
    half = [k for k in range(1, len(sw) - 1) if sw[k] >= hero + (D - hero) / 2]
    lifts = []
    if half:
        la = max(half, key=lambda k: st_[k])
        lifts = [sum(lens[:la])]
    info = dict(main=round(main, 4), value=round(float(v), 2), n=len(sw), D=round(D, 4), pinned=bool(pin), tscale=tsc, tails=tails, outro=outro,
                groove=G,
                waves=[dict(a=x['a'], b=x['b'], s=x['strength'], why=x['why']) for x in Wv['waves']],
                cuts=[dict(sw=round(C[i]['sw'], 3), attack=C[i]['a'], why=C[i]['why'], s=C[i]['strength'], beat=C[i]['beat'])
                      for i in idx], skipped=skipped, judge_repair=repair)
    log('cinematic: wave cuts: %d shots, skipped wave starts %s' % (len(sw), json.dumps(skipped)))
    return cuts, lens, kinds, lifts, main, info


def _cine_judge_repair(A, start, D, sw, st_, speed, lo, max_sh, passes=4):
    """2026-10-21 (CI dry run "blue" @v0.9, judge NOT PASS): the planner's waves (music_moments) and the pre-upload judge's
    independent wave analysis (judge_slowmo.wave_events) disagree on borderline syllables: "with me?" = one held note for the
    judge (a voiced 'm', same pitch, < 11 dB dip) but two waves here (switch 37.07 inside the judge's held tail); "still" ->
    "the" 0.61 s apart (the switch on 16.48 left "the" 17.19 'missed'). The plan is checked with the judge's own analysis
    of the song window it plays and repaired where the judge would fail it: a cut the judge reads mid-hold / mid-phrase /
    inside a held tail is dropped (its shot joins the previous one, <= 7 s); a missed strong start moves the previous
    switch later inside its own start's hit window (so the new start is that shot's own) or gets a switch of its own when
    the spacing (>= lo) and the clip count allow. -> (sw, strengths, log) ; any failure = the plan unchanged."""
    rep = dict(dropped=[], moved=[], added=[])
    try:
        import tempfile
        import judge_slowmo as JS
        tmp = os.path.join(tempfile.mkdtemp(prefix='cine_jr_'), 'window.wav')
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-ss', f'{float(start):.4f}', '-t', f'{float(D):.4f}', '-i', str(A['path']),
                        '-ac', '2', '-ar', '44100', tmp], check=True)
        E = JS.wave_events(tmp, float(D), words=False, speed=speed)
        tsc = float(E.get('tscale') or 1.0)
        mins = JS.JW_MIN_SHOT * tsc
        sw, st_ = list(sw), list(st_)
        fps = 30.0

        def fr(t):
            return math.ceil(t * fps - 1e-6) / fps
        for _ in range(passes):
            WJ = JS.wave_judge(tmp, sw, float(D), E=E, speed=speed)
            bad = set(WJ['bad']) | {round(c, 3) for c, _w in ((WJ.get('tail_outro') or {}).get('bad') or [])}
            changed = False
            for c in sorted(bad, reverse=True):
                k = min(range(len(sw)), key=lambda k_: abs(sw[k_] - c))
                if k == 0 or abs(sw[k] - c) > 0.02:
                    continue
                nxt = sw[k + 1] if k + 1 < len(sw) else float(D)
                # first choice: the switch moves back to the start of the wave / held note it fell into (CI dry run 4: 37.07
                # inside "with me?" held from 36.06, and no switch on "with" -> dropping it left "me?" 'missed' in the render)
                st_a = [a for a in E['attacks'] if a[2] and a[0] < sw[k] and sw[k] - a[0] < 2.5]
                if st_a:
                    a = max(st_a, key=lambda a: a[0])
                    tb = fr(max(a[4] - JS.JW_HIT[1], a[0] - 0.10))
                    if tb <= a[0] - JS.JW_HIT[0] and tb - sw[k - 1] >= lo and nxt - tb >= lo and                             not any(abs(x - tb) < lo for j_, x in enumerate(sw) if j_ != k):
                        rep['moved'].append([round(sw[k], 3), round(tb, 3)])
                        sw[k] = tb
                        changed = True
                        continue
                if nxt - sw[k - 1] <= 7.0:
                    rep['dropped'].append(round(sw[k], 3))
                    del sw[k], st_[k]
                    changed = True
            if changed:
                continue
            for m in WJ['missed']:
                t = float(m['t'])
                a_ = next((a for a in E['attacks'] if abs(a[0] - t) < 1e-6), None)
                if a_ is None:
                    continue
                kp = max([k for k in range(len(sw)) if sw[k] <= t] or [0])
                # (1) the previous switch later, still inside the hit window of the start it is on
                on = [a for a in E['attacks'] if a[2] and a[0] < t and a[4] - JS.JW_HIT[1] <= sw[kp] <= a[0] - JS.JW_HIT[0]]
                tgt = fr(t - mins + 0.01)
                if kp > 0 and on and tgt <= on[0][0] - JS.JW_HIT[0] and tgt - sw[kp - 1] >= lo and                         (kp + 1 >= len(sw) or sw[kp + 1] - tgt >= lo):
                    rep['moved'].append([round(sw[kp], 3), round(tgt, 3)])
                    sw[kp] = tgt
                    changed = True
                    break
                # (2) a switch of its own: 0.10 s before the attack, inside its hit window, the spacing kept
                c_new = fr(max(a_[4] - JS.JW_HIT[1], min(t - 0.10, t - JS.JW_HIT[0])))
                nxt = min([x for x in sw if x > c_new] or [float(D)])
                if c_new - sw[kp] >= lo and nxt - c_new >= lo:
                    if len(sw) >= max_sh:
                        # at the clip cap (CI dry run 2: 25 clean clips, "leave" 23.36 missed): give up the switch whose
                        # removal the judge does not notice (a planner wave the judge hears as no strong start)
                        n0 = len(WJ['missed']) + len(WJ['bad'])
                        best = None
                        for k in range(1, len(sw)):
                            nxt_k = sw[k + 1] if k + 1 < len(sw) else float(D)
                            if nxt_k - sw[k - 1] > WV_SHOT[1] + 1.0:
                                continue
                            tr = sorted(sw[:k] + sw[k + 1:] + [c_new])
                            if any(b_ - a__ < lo - 1e-6 for a__, b_ in zip(tr, tr[1:])):
                                continue
                            W2 = JS.wave_judge(tmp, tr, float(D), E=E, speed=speed)
                            n2 = len(W2['missed']) + len(W2['bad']) + len((W2.get('tail_outro') or {}).get('bad') or [])
                            if n2 < n0 and (best is None or n2 < best[0]):
                                best = (n2, k)
                                if n2 == 0:
                                    break
                        if best is None:
                            continue
                        rep.setdefault('swapped', []).append([round(sw[best[1]], 3), round(c_new, 3)])
                        del sw[best[1]], st_[best[1]]
                        kp = max([k for k in range(len(sw)) if sw[k] <= t] or [0])
                    else:
                        rep['added'].append(round(c_new, 3))
                    sw.insert(kp + 1, c_new)
                    st_.insert(kp + 1, 2.5)
                    changed = True
                    break
            if not changed:
                break
        # groove songs (judge 14, 2026-10-23): a LOOSE switch (neither on a main beat nor tight on its sung attack) moves onto
        # the beat its attack lags or tight before the attack, when the judge's 11 / 13 counts do not get worse
        WJ = JS.wave_judge(tmp, sw, float(D), E=E, speed=speed)
        GC = WJ.get('groove') or {}
        if GC.get('groove') and os.environ.get('SLOWMO_GROOVE_REPAIR', '1') != '0':
            bt = np.asarray((E.get('groove') or {}).get('beats') or [], float)
            per = float((E.get('groove') or {}).get('period') or 0.6)

            def _n(W_):
                return len(W_['bad']) + len(W_['missed']) + len((W_.get('tail_outro') or {}).get('bad') or [])
            n0 = _n(WJ)
            for c in list(GC.get('loose') or []):
                k = min(range(len(sw)), key=lambda k_: abs(sw[k_] - c))
                if k == 0 or abs(sw[k] - c) > 0.02:
                    continue
                cands = []
                for a in [a_[0] for a_ in E['attacks'] if -0.2 <= a_[0] - c <= 0.6]:
                    cands.append(fr(a - 0.03))                 # tight on the sung attack
                    pb = bt[(bt <= a) & (bt >= a - 0.25)]
                    if len(pb):
                        cands.append(fr(pb[-1] - 0.02))        # on the beat just before it
                for x in sorted(set(cands), key=lambda x_: abs(x_ - c)):
                    if abs(x - c) > 0.12:
                        break                                   # a fine-tune only (CI 10-23: an 0.19 s move pushed a line-start switch off)
                    prv = sw[k - 1]
                    nxt = sw[k + 1] if k + 1 < len(sw) else float(D)
                    if x - prv < lo or nxt - x < lo:
                        continue
                    tr = sw[:k] + [x] + sw[k + 1:]
                    W2 = JS.wave_judge(tmp, tr, float(D), E=E, speed=speed)
                    if _n(W2) <= n0 and not any(abs(y - x) < 0.01 for y in ((W2.get('groove') or {}).get('loose') or [])):
                        rep.setdefault('groove', []).append([round(sw[k], 3), round(x, 3)])
                        sw[k], n0 = x, _n(W2)
                        break
            # a switch on a pick-up word right before a phrase that has its own switch (CI 10-23: 26.73 on a short word
            # 0.7 s before the phrase switch at 27.47; the rendered audio's judge read it as mid-hold): the pick-up joins the
            # shot before it (the refs' phrase-level momentum), when the judge's counts stay the same
            WJ = JS.wave_judge(tmp, sw, float(D), E=E, speed=speed)
            others = [r_['cut'] for r_ in ((WJ.get('groove') or {}).get('rows') or [])
                      if r_['kind'] in ('other', 'beat') and r_.get('attack_after') is None]   # no sung attack within 0.5 s
            for c in others:
                k = min(range(len(sw)), key=lambda k_: abs(sw[k_] - c))
                if k == 0 or abs(sw[k] - c) > 0.02 or k + 1 >= len(sw) or sw[k + 1] - c > 0.9:
                    continue
                row = next((r_ for r_ in WJ['rows'] if abs(r_['cut'] - c) < 0.02), {})
                if str(row.get('what', '')).startswith(('vocal gap', 'release')):
                    continue
                tr = sw[:k] + sw[k + 1:]
                if tr[k] - tr[k - 1] > WV_SHOT[1] + 1.0:
                    continue
                W2 = JS.wave_judge(tmp, tr, float(D), E=E, speed=speed)
                if _n(W2) <= _n(WJ):
                    rep.setdefault('groove_pickup', []).append(round(sw[k], 3))
                    del sw[k], st_[k]
                    WJ = W2
        WJ = JS.wave_judge(tmp, sw, float(D), E=E, speed=speed)
        rep['after'] = dict(bad=WJ['bad'], missed=[m['t'] for m in WJ['missed']],
                            tail_bad=[c for c, _w in ((WJ.get('tail_outro') or {}).get('bad') or [])], sync_off=WJ['sync']['off'])
        if rep['dropped'] or rep['moved'] or rep['added'] or rep.get('swapped') or rep.get('groove') or rep.get('groove_pickup'):
            log('cinematic: judge repair %s' % json.dumps(rep, default=float))
        return sw, st_, rep
    except Exception as e:  # noqa: BLE001
        log(f'cinematic: judge repair failed ({type(e).__name__}: {e})')
        return sw, st_, dict(error=str(e)[:200])


def _cine_lock_cuts(env, cuts, lead, pulse, min_hit=2.0, body_snap=None):
    """Each cut's switch frame ~70 ms before the song's accent: the accent right after the grid switch when it is
    the strongest near the pulse point, else a stronger one within 250 ms (critic: the closing cut landed 213 ms
    after the phrase pickup). -> (cuts, log rows)"""
    out, rows = [], []
    for i, c in enumerate(cuts):
        s_loc, t_loc = _cine_peak(env, c - lead + CINE_HIT_AFTER[0], c - lead + CINE_HIT_AFTER[1])
        win = 0.25 if (i == 0 or body_snap is None) else float(body_snap)
        # remake round 8 (critic: intervals 1.04 / 0.88 / 0.96 / 0.88 s where _sX2 cuts every 1.00 s): the body cuts
        # keep one constant pulse and move at most body_snap toward an accent; only the hero cut snaps 250 ms
        s_w, t_w = _cine_peak(env, c - win, c + win)
        acc, src = (t_loc, 'grid') if s_loc >= 0.8 * s_w else (t_w, 'snap')
        s_acc = s_loc if src == 'grid' else s_w
        nc = acc - 0.07 + lead
        if i > 0 and body_snap is not None:
            nc = min(c + float(body_snap), max(c - float(body_snap), nc))
        if out and nc - out[-1] < 0.6 * pulse:
            nc, src, s_acc = c, 'kept', s_loc
        out.append(round(nc, 4))
        rows.append(dict(i=i, grid=round(c, 3), cut=round(nc - lead, 3), hit=round(s_acc, 2), how=src,
                         weak=bool(s_acc < min_hit)))
    return out, rows


def _cine_audio(path, t0, dur, sr=22050):
    """Mono float32 samples of [t0, t0 + dur] (ffmpeg decode; fast on m4a)."""
    b = subprocess.run(['ffmpeg', '-v', 'error', '-ss', f'{max(0.0, t0):.4f}', '-t', f'{dur:.4f}', '-i', str(path),
                        '-ac', '1', '-ar', str(sr), '-f', 'f32le', '-'], capture_output=True).stdout
    return np.frombuffer(b, np.float32).astype(np.float64)


def _cine_breath_riser(path, t_song, lookback=0.8):
    """True when the song itself breathes into t_song the way _sX2's does (5.0-5.56 s: 0.4 s at -33..-38 dB re the
    0.5 s after the switch, the 3 kHz+ share rising 0.0 -> 0.95 over its last 0.3 s, then the full band): a run of
    >= 0.3 s under -24 dB within `lookback`, the high share rising >= 0.4 from the run's start to the last 0.15 s."""
    sr = 22050
    try:
        y = _cine_audio(path, t_song - lookback, lookback + 0.5, sr)
    except Exception:  # noqa: BLE001
        return False
    n0 = int(lookback * sr)
    if len(y) < n0 + int(0.3 * sr):
        return False
    hop = int(0.01 * sr)
    ref = float(np.sqrt(np.mean(y[n0:] ** 2)) + 1e-9)
    fr = y[:n0 - n0 % hop].reshape(-1, hop)
    db = 20 * np.log10(np.sqrt(np.mean(fr ** 2, axis=1)) / ref + 1e-9)
    run, best, b_end = 0, 0, 0
    for k, q in enumerate(db < -24.0):
        run = run + 1 if q else 0
        if run > best:
            best, b_end = run, k
    if best * hop / sr < 0.3:
        return False
    from scipy import signal as SG
    hi = SG.sosfilt(SG.butter(4, 3000, 'highpass', fs=sr, output='sos'), y)

    def share(a, b):
        return float(np.sum(hi[a:b] ** 2) / (np.sum(y[a:b] ** 2) + 1e-12))
    q0 = (b_end - best + 1) * hop
    s_a, s_b = share(q0, q0 + int(0.15 * sr)), share(n0 - int(0.15 * sr), n0)
    return bool(s_b - s_a >= 0.4)


def plan_song_edit(A, shots, opts):
    if (opts.get('template') or '') == 'fx_heavy':
        return plan_fx_edit(A, shots, opts)
    if (opts.get('template') or '') == 'cinematic_slowmo' and not opts.get('cine_legacy'):
        return plan_cinematic_slowmo(A, shots, opts)     # remake round 1 (refs/remake-cinematic_slowmo.md)
    if (opts.get('template') or '') == 'flow_blur' and opts.get('flow_variant') == 's2rz' and not opts.get('s2rz_legacy'):
        # remake round 3 (refs/remake-blur_flow-S2rz.md): the remake's S2rzMsl0N7k shot table (flow_s2rz.py template)
        # mapped onto the song's beat grid; the round-2 rules below stay as the fallback
        try:
            import flow_s2rz as FS2  # noqa: E402
            if os.path.exists(FS2.TEMPLATE):
                return FS2.plan_flow_s2rz(A, shots, opts)
        except Exception as e:  # noqa: BLE001
            log(f's2rz template planner failed, round-2 rules: {e}')
    if (opts.get('template') or '') == 'hype_typography' and not opts.get('hype_legacy'):
        # remake round 1 (refs/remake-hype_typography.md): the i4fSruIkkxk structure mapped onto the song's beat grid
        try:
            import hype_i4f as HI4F  # noqa: E402
            return HI4F.plan_hype_i4f(A, shots, opts)
        except Exception as e:  # noqa: BLE001  (the legacy hype planner still makes an edit)
            log(f'hype_i4f planner failed, legacy hype_typography: {e}')
    rng = random.Random(opts.get('seed'))
    pool = Pool(shots, rng)
    if len(pool) < 3:
        raise RuntimeError(f'only {len(pool)} usable shots')
    template = song_template(A, pool, rng, opts.get('template'))
    style, group, grade = STYLE[template]
    fps = int(opts.get('fps') or 60)
    cine = template == 'cinematic_slowmo'
    lo, hi = (0.9, 1.1) if cine else (0.45, 0.75)
    L = float(opts.get('length') or (13.5 if cine else 14.0))
    # hook intro (general.json structure.short.hook_intro_s.kinds; straight-in weighted up for retention)
    intro_p = {'straight_in': 0.45, 'held_hero': 0.25, 'title_on_black': 0.12,
               'title_card_light': 0.1, 'postcard_card': 0.08}
    if template == 'flow_blur':
        # blur_flow only (ZkWQloS4jtM's own intro): the photo card on paper that blows out into
        # the scene.  Never offered to the other templates - presets are type specific.
        # (remake round 1: the owner's favourite opening, so it leads the intro mix)
        intro_p['photo_scene_card'] = 0.35
    intro_kind = opts.get('intro') or wpick(rng, intro_p)
    # remake round 1 of S2rzMsl0N7k (refs/remake-blur_flow-S2rz.md): the blur_flow "s2rz" variant opens on words over a
    # dark plate (the reference types its lines over a star field until the drop), never on the ZkWQ photo card
    flow_variant = opts.get('flow_variant') if template == 'flow_blur' else None
    if flow_variant == 's2rz' and not opts.get('intro'):
        # round 2: the reference opens on footage within 2.5 s (a star plate, then the character); a 4.8 s black card
        # with a small title was the viewer critic's first blocking defect -> the held hero shot, the title popped over
        # it for ~1.2 s (overlays below)
        intro_kind = 'held_hero'
    if cine and intro_kind == 'straight_in' and rng.random() < 0.5:
        intro_kind = 'held_hero'
    intro_rng = {'straight_in': (0.0, 0.0), 'held_hero': (1.5, 4.0), 'title_on_black': (2.0, 4.0),
                 'title_card_light': (1.5, 4.0), 'postcard_card': (2.0, 4.5),
                 'photo_scene_card': (1.5, 4.0)}[intro_kind]
    win = MU.choose_window(A, L, intro_rng, min_len=L * 0.75, max_len=L * 1.25, contrast=0.3 if intro_rng[1] else 0.0, rng=rng)
    if win['intro'] < 0.5:
        intro_kind = 'straight_in'
    view = MU.window_view(A, win['start'], win['end'], lo, hi)
    aspect = opts.get('aspect') or '9:16'      # remake round 2: 16:9 renders compared with the references at their own size
    B = Builder(view, pool, rng, fps, group, template, aspect)
    B.flow_variant = flow_variant
    D = view['duration']
    drop = win['drop'] - win['start']
    P = B.P

    layout = 'fill'
    if template == 'beat_collection_band':
        layout = 'band_16x9'
    elif cine and rng.random() < 0.3:
        layout = 'rotated_16x9'  # _sX2: the 16:9 master turned 90 degrees into the 9:16 frame
    elif rng.random() < 0.12 and template != 'cinematic_slowmo':
        layout = 'letterbox'  # general.json layouts.letterbox_existing (0.2); full-bleed reads better in the feed
    # --- intro
    if intro_kind != 'straight_in':
        B.add(0.0, 'none', role='hero', speed={'kind': 'slowmo', 'rate': 0.5 if cine else 0.6}, layout=layout,
              first='first_shot_entry' if cine else 'none',
              camera=dict(move='push_in', zoom=[1.0, round(math.exp(0.06 * max(1.0, drop)), 4)], ease='inout', handheld=None),
              section='intro')
        if intro_kind in ('title_on_black', 'postcard_card'):
            B.segs[-1]['hidden'] = True  # a scene replaces the picture until the drop
    # --- body
    house = {'flow_blur': 'blur_bridge', 'hype_typography': 'blur_bridge', 'cinematic_slowmo': 'exposure_bump_hard',
             'beat_collection_band': 'darken_slide_preview'}[template]
    flash_var = {'flow_blur': 0.4, 'hype_typography': 0.6}.get(template, 0.0)
    last_tr = None
    t = drop
    first_body = True
    phrase_every = 4 * view['bar'] if view['bar'] < 2.4 else 2 * view['bar']
    heroes = 0
    last_double = -1e9
    while t < D - max(0.25, 0.4 * P):
        e = B.e_fwd(t)
        down = B.is_down(t)
        phrase = down and (abs(((t - drop) / phrase_every) - round((t - drop) / phrase_every)) < 0.05)
        tp = None
        seg_layout = layout
        if first_body:
            tr = 'flash_white' if template != 'cinematic_slowmo' and template != 'beat_collection_band' else house
            if intro_kind == 'straight_in' and not B.segs:
                tr = 'none'
        elif phrase and template in ('flow_blur', 'hype_typography') and heroes < 3 and rng.random() < 0.8:
            pool_h = dict(GEN_HERO_P)
            if template == 'flow_blur':
                for k in ('split_screen', 'white_bar_wipe', 'card_tilt_in'):
                    pool_h[k] *= 0.5
                # ZkWQloS4jtM's hero moment: the scene pulls back into the photo collage and the
                # collage turns out to be a post on a phone (blur_flow only; remake round 1: weighted up).
                pool_h['photo_to_phone'] = 0.25
                if any(x.get('transition_in') == 'photo_to_phone' for x in B.segs):
                    pool_h.pop('photo_to_phone', None)     # ZkWQ shows its collage -> phone hero once per edit (round 4)
                if flow_variant == 's2rz':
                    # S2rz's hero moments are white flashes into its star / word plates and zoom-throughs; it has no
                    # collage, phone, wipe or strobe set pieces (remake round 2)
                    pool_h = {'flash_white': 0.7, 'zoom_through': 0.3}
            tr = wpick(rng, pool_h, exclude=(last_tr,))
            heroes += 1
            if tr == 'photo_to_phone':
                tp = {'build_beats': 2.0, 'settle_beats': 1.0, 'hold_beats': 2.6}
            if tr == 'split_screen':
                # general.json: the split screen is a shot LAYOUT held 1-2 beats behind a bar-wipe reveal,
                # not a cut. Emitting the layout is what makes it render at all (the transition name alone
                # used to alias to a plain hard cut).
                seg_layout, tr = 'split2' if rng.random() < 0.7 else 'split3', 'white_bar_wipe'
        elif template == 'cinematic_slowmo':
            tr = 'exposure_bump_hard' if rng.random() < 0.85 else 'blur_bridge'
            if tr == 'blur_bridge':
                tp = {'flash': False, 'soft': True}
        elif template == 'beat_collection_band':
            tr = house
        else:
            tr = wpick(rng, (S2RZ_CUT_P if flow_variant == 's2rz' else ZKW_CUT_P) if template == 'flow_blur' else GEN_CUT_P,
                       exclude=(last_tr,) if last_tr != 'blur_bridge' else ())
            if tr == 'whip_slide' and template == 'flow_blur':
                tp = {'mode': 'pan_in', 'post': 0.25, 'dist': 0.4}     # ZkWQ's whip: one smear frame, then a decelerating pan-in
            if tr == 'blur_bridge':
                tp = {'flash': rng.random() < (flash_var if e >= 0.55 else 0.1)}
        lead = None
        # speed by energy: calm -> slow-mo hold, build -> velocity, drop -> velocity with fast entry
        if cine:
            spd = {'kind': 'slowmo', 'rate': round(rng.uniform(0.35, 0.6), 3)} if rng.random() < 0.55 else {'kind': 'normal'}
            role = 'calm'
        elif template == 'beat_collection_band':
            spd, role = {'kind': 'normal'}, 'hero'
        elif e < 0.4:
            spd, role = {'kind': 'slowmo', 'rate': round(rng.uniform(0.4, 0.6), 3)}, 'calm'
        elif e < 0.7:
            spd = {'kind': 'velocity_u'} if rng.random() < 0.6 else {'kind': 'normal', 'rate': rng.choice([0.8, 0.9, 1.0])}
            role = 'build'
        else:
            spd = rng.choice([{'kind': 'velocity_u'}, {'kind': 'velocity_u'}, {'kind': 'normal'}])
            role = 'drop'
        if first_body and not cine and template != 'beat_collection_band':
            # the drop: the strongest hero shot, entering fast (never a slow-motion hold)
            spd, role = rng.choice([{'kind': 'velocity_u'}, {'kind': 'normal'}]), 'hero'
        seg = B.add(t, tr, lead=lead, role=role, speed=spd, layout=seg_layout, tparams=tp, section=B.section_label(t))
        if seg is not None:
            last_tr = tr
        # next cut: the interval by loudness (general.json cut_rules.interval_beats)
        if cine:
            # one felt pulse of ~1 s (_sX2); when the pulse is short, some shots hold two
            step = 1.0 if P >= 0.85 else wpick(rng, {1: 0.6, 2: 0.4})
        elif template == 'beat_collection_band':
            step = 1.0
        else:
            dist = dict(GEN_INTERVALS[level_of(e)])
            if first_body:
                dist = {1: 0.3, 2: 0.7}  # hold the drop shot a little
            step = wpick(rng, dist)
            if step == 0.5 and t - last_double < 2 * P - 0.05:
                step = 1.0
            if step == 0.5:
                last_double = t
                # a quick double inside one beat: two half-beat shots
                B.add(t + 0.5 * P, 'blur_bridge', role='drop', speed={'kind': 'velocity_u'}, layout=layout, tparams={'flash': False},
                      section=B.section_label(t))
                step = 1.0
        first_body = False
        nt = t + step * P
        # snap to the pulse grid
        if len(B.beats):
            j = int(np.argmin(np.abs(B.beats - nt)))
            nt = float(B.beats[j]) if abs(B.beats[j] - nt) < 0.3 * P else nt
        t = nt
    B.close(D)
    for i, s in enumerate(B.segs):
        nxt = B.segs[i + 1]['transition_in'] if i + 1 < len(B.segs) else None
        if s.get('camera') is None:
            s['camera'] = cam_general(rng, template, i, s['t0'], s['t1'], s['beat'], nxt, B.e_at(s['beat']))
    bank_rows = camera_bank('blur_flow') if template == 'flow_blur' else []
    if bank_rows and flow_variant == 's2rz':
        # the S2rz variant plays only S2rz's own camera paths: fast push-ins / pull-outs (ln-scale range p50 0.24 per
        # shot vs 0.08 in ZkWQ, flow p50 1.30 vs 0.89 %W / frame) - the owner's "zoom in and zoom out" reference
        bank_rows = [r for r in bank_rows if r.get('ref') == 'S2rzMsl0N7k'] or bank_rows
    if bank_rows:
        # remake round 1 (core/edit/refs/remake-blur_flow-ZkWQ.md): the references' shots never sit still - the camera
        # pans / rolls / zooms through the whole shot (ZkWQ flow median 0.88 %W / frame; ours was 0.32-0.41 with the
        # synthetic push / pull / drift moves). Every body shot now plays a real reference camera path of its length
        # and energy (refs/camera_bank.json); the intro card shot keeps its slow push.
        prev_row = None
        for s in B.segs:
            if s.get('hidden') or s.get('section') == 'intro':
                continue
            got = bank_camera(rng, bank_rows, s['t1'] - s['t0'], B.e_at(s['beat']), prev_row)
            if got:
                s['camera'], prev_row = got
    B.fill()
    B.assign_panels()
    # overlays in the engine's types (engine.py Overlay / layouts.SCENES); the style draws the title (plan.title)
    # where its references put it, unless the planner shows it itself
    title = latin_text(opts.get('title'))
    overlays = []
    end_kind = wpick(rng, {'fade_to_black': 0.45, 'cut_on_last_hit': 0.55}) if not cine else 'fade_to_black'
    show_title = bool(title)
    if intro_kind == 'title_on_black' and title:
        # _sX2: words on black until the drop (neon stretch) / thin wide caps
        overlays.append(dict(type='text_on_black', t0=0.0, t1=round(drop, 3), text=title, style='neon' if cine else 'wide_thin'))
        if flow_variant == 's2rz':
            # S2rz intro: chunky rounded caps with a blue glow, 8.5-12 % of the frame height, centred (the 'wide_thin'
            # phrase at 3 % H was unreadable in the first planner render, remake round 1)
            overlays[-1].update(style='s2rz_title', size=int(0.11 * (1080 if aspect == '16:9' else 1920)))
        show_title = False
    if flow_variant == 's2rz' and title and intro_kind == 'held_hero' and drop > 0.6:
        # S2rz 0.07-2.3 s: chunky rounded caps with a blue halo popped over the opening plate, ~8.5 % H, centred
        overlays.append(dict(type='text', text=title, style='s2rz_title', size=0.085 / 0.72, x=0.5, y=0.5, anim='pop',
                             t0=0.1, t1=round(min(1.4, drop - 0.2), 3)))
        show_title = False
    if intro_kind == 'title_card_light' and title:
        # JQH: a title card with a beat-pulsing light over the held intro shot
        overlays.append(dict(type='title_card', t0=0.1, t1=round(max(0.6, drop - 0.05), 3), text=title, style='neon', size=0.07,
                             anim='stretch', y=0.5))
        overlays.append(dict(type='beat_light_pulse', t0=0.0, t1=round(drop, 3)))
        show_title = False
    if intro_kind == 'postcard_card':
        # i4f / m3lv: a music card (album still, song title, EDIT BY the channel) until the drop
        overlays.append(dict(type='music_card', t0=0.0, t1=round(drop, 3), title=latin_text(opts.get('song')) or title or '',
                             artist=latin_text(opts.get('artist')) or latin_text(opts.get('subtitle')) or '', line=title or '',
                             channel=latin_text(opts.get('watermark')) or ''))
        show_title = False
    if intro_kind == 'photo_scene_card':
        # ZkWQloS4jtM f0-f41: the live shot printed as a photo card on lavender paper (crimson
        # strokes, sakura), blowing out into the full-frame scene 2.5 beats in.  blur_flow only.
        overlays.append(dict(type='photo_scene_intro', t0=0.0,
                             t1=round(min(drop, 1.9 * P) if P else drop, 3),
                             # 2.69 beats of ZkWQ's 161 BPM grid = 1.363 felt beats: the card lands 1.0 s in at 81 BPM
                             beats=1.363, stickers=4, seed=int(opts.get('seed') or 0) % 97))
    if template == 'hype_typography' and title:
        # the style slams the title on the drop; the next hero downbeats get a whole phrase each (i4f, zawB: the song
        # title, the name) - never a fragment of the title
        phrases = [x.upper() for x in (latin_text(opts.get('song'), 16), latin_text(opts.get('subtitle'), 16)) if x and len(x) >= 3]
        phrases = [x for x in phrases if x.lower() not in title.lower() or len(phrases) == 1] or             [w.upper() for w in sorted(title.split(), key=len, reverse=True) if len(w) >= 4][:2]
        for j, d in enumerate([d for d in B.downs if drop + 1.0 < d < D - 1.0][::2][:len(phrases)]):
            overlays.append(dict(type='text', t0=round(float(d) - 0.03, 3), t1=round(float(d) + min(1.2, 2 * P), 3),
                                 text=phrases[j], style='bold_italic', size=0.085, anim='slam', y=0.5))
    elif template == 'flow_blur' and show_title:
        show_title = rng.random() < 0.4  # the flow references carry no text
    if template in ('flow_blur', 'cinematic_slowmo') and flow_variant != 's2rz' and rng.random() < (0.5 if template == 'flow_blur' else 0.3):
        # (S2rz carries no particle layer: its embers are a plate of their own)
        kind = rng.choice(['sparkles', 'hearts', 'embers', 'petals'] if template == 'flow_blur' else ['petals', 'gold_dust'])
        overlays.append(particles_overlay(style, kind, drop, D))
    fade_out = 1.2 if end_kind == 'fade_to_black' else 0.0
    if end_kind == 'fade_to_black':
        overlays.append(dict(type='fade', t0=round(D - 1.2, 3), t1=round(D, 3), color='black', dir='out'))
    if drop > 0.3 and template not in ('cinematic_slowmo', 'beat_collection_band'):
        overlays.append(dict(type='ring', t0=round(drop, 3), t1=round(drop + 0.4, 3)))
    song_fx = []
    if template == 'flow_blur':
        # ZkWQ: the creator card ("FLIMZY") twice in the body - 20-24 % of a 16:9 frame height, navy, no glow, hard
        # on, 0.9-1.4 s - 3 beats after the drop and again 12 s later (remake round 1)
        name = latin_text(opts.get('watermark') or opts.get('title') or '', 14)
        if flow_variant == 's2rz':
            name = ''            # S2rz shows no creator card: its body text is the lyric track (lyric_text.py 'blur_flow')
        if name and drop > 0.3:
            for j, tc in enumerate((drop + 3 * P, drop + 3 * P + 12.0)):
                if tc + 1.0 < D - 0.5:
                    # 16:9: the reference's own 20.3 / 24.2 % of H; 9:16: the same card width (x 1080 / 1920)
                    size = (0.203 if j == 0 else 0.242) if aspect == '16:9' else (0.114 if j == 0 else 0.13)
                    # remake 2026-09-28: the card is white caps extruded in soft pink over a defocused plate (the navy
                    # read was the sweater behind it)
                    overlays.append(dict(type='text', text=name.upper(), style='zkw_card', size=size,
                                         x=0.49 if j == 0 else 0.47, y=0.47 if j == 0 else 0.54, anim='none', backdrop_blur=0.012,
                                         t0=round(tc, 3), t1=round(tc + (0.9 if j == 0 else 1.37), 3)))
        if aspect == '16:9' and drop > 0.3:
            # ZkWQ (measured per frame, remake round 4): 7.5 % letterbox bars slide in over 0.2 s once the photo card lands
            # and stay on the held intro until the drop; in the body they come back on alternating runs of 1-2 beats
            # (9 runs, ~35 % of the body) with 2-6 beat gaps between them - the engine ramps the bars 6 frames each end
            land = min(drop, 1.363 * P) if (P and intro_kind == 'photo_scene_card') else 0.0
            if drop - land > 0.6:
                overlays.append(dict(type='letterbox', t0=round(land + 0.1, 3), t1=round(drop, 3), frac=0.075))
            tb = drop + rng.uniform(3.0, 5.0) * P
            while tb + P < D - 1.0:
                run = rng.choice([1, 1, 2]) * P
                overlays.append(dict(type='letterbox', t0=round(tb, 3), t1=round(min(tb + run, D - 0.5), 3), frac=0.075))
                tb += run + rng.uniform(2.0, 6.0) * P
        if intro_kind == 'photo_scene_card' and len(B.segs) > 1:
            # ZkWQ 0-0.94 s: the song under the photo card is low-passed (3-15 kHz -29..-51 dB), opening over 860 ms
            # and fully open 80 ms before the card lands; the song swells +7.4 dB over the intro into the first cut
            land = min(drop, 1.363 * P) if P else 1.0        # the card lands 1.363 felt beats in (photo_phone.py)
            first_cut = float(B.segs[1]['t0'])
            song_fx = [dict(op='lowpass', t_close=-0.5, close_s=0.01, t_open=round(land - 0.08, 3), open_s=round(0.86 * land, 3),
                            hi_db=-40.0, mid_db=-4.0),
                       dict(op='fade', t0=0.0, t1=round(first_cut, 3), db0=-7.4, db1=0.0)]
        if flow_variant == 's2rz' and drop > 0.6:
            # S2rz's song edits (remake round 2, refs/remake-blur_flow-S2rz.md): the intro is held -4 dB and low-passed
            # (hi -12 / mid -6 dB) opening 0.35 s before the drop, a riser + bright impact into the drop, +3 dB air
            # over the body, the sub cut -8 dB for ~0.3 s before each white-flash cut, the song fading with the end
            song_fx = [dict(op='duck', t0=0.0, t1=round(drop - 0.3, 3), depth_db=-4.0, attack=0.02, release=0.2),
                       dict(op='lowpass', t_close=-0.5, close_s=0.01, t_open=round(drop - 0.35, 3), open_s=0.35, hi_db=-12.0, mid_db=-6.0),
                       dict(op='riser', drop=round(drop, 3), dur=0.6, swell_s=0.35),
                       dict(op='impact', at=round(drop - 0.14, 3), t_peak_db=12.0, bright=0.7, sub_db=6.0, anchor=False),
                       dict(op='air_lift', t0=round(drop, 3), t1=round(D - 0.5, 3), db=3.0)]
            for s in B.segs[1:]:
                if str(s.get('transition_in') or '').startswith('flash_white') and s['t0'] > drop + 0.5:
                    song_fx.append(dict(op='bass_cut', cut=round(float(s['t0']), 3), dur=0.3, depth_db=-8.0))
            if end_kind == 'fade_to_black':
                song_fx.append(dict(op='fade', t0=round(D - 2.5, 3), t1=round(D - 0.2, 3), db0=0.0, db1=-30.0, then_out=True))
    plan = finish(B, A, win, view, opts, template, style, group, grade, overlays, aspect,
                  audio=dict(fade_in=0.0, fade_out=fade_out), extra=dict(intro=intro_kind, end=end_kind, drop=round(drop, 4)),
                  show_title=show_title)
    if template == 'flow_blur' and flow_variant == 's2rz':
        plan['look'] = plan['grade'] = 's2rz_cool_adapt'  # S2rz: crisp saturated cool anime colour, per-clip normalised (styles.LOOKS)
        plan['planner']['flow_variant'] = 's2rz'
    elif template == 'flow_blur':
        plan['look'] = plan['grade'] = 'zkw_soft_warm'   # ZkWQ's measured palette, normalised per clip (styles.adapt_look)
        av = opts.get('avatar') or os.path.join(os.path.dirname(os.path.abspath(__file__)), 'assets', 'brand', 'avatar_zero_yen_otaku.png')
        if D >= 20.0 and os.path.exists(av) and opts.get('outro', True):
            # ZkWQ 33.47-37.5 s: the edit ends on the creator's end card (dark vignette, the round avatar settling out of a
            # defocused ring, then a pulsing down-chevron) - the last 2.6 s, the music fading under it
            plan['overlays'].append(dict(type='zkw_outro', t0=round(D - 2.6, 3), t1=round(D, 3), image=av.replace(chr(92), '/'),
                                         avatar_end=1.2, beat=round(P, 4) if P else 0.73))
    if bank_rows:
        plan['drive'] = False          # the copied reference paths ARE the in-shot motion (handover.DRIVE would double it)
        plan['handover'] = False       # ... and they start with the reference's own post-cut settle
    if song_fx:
        plan['song_fx'] = song_fx
    return plan


# ---------------------------------------------------------------- dance edit

def plan_dance_edit(A, shots, opts):
    rng = random.Random(opts.get('seed'))
    pool = Pool(shots, rng)
    if len(pool) < 2:
        raise RuntimeError(f'only {len(pool)} usable dance shots')
    template = opts.get('template') or wpick(rng, {'short_cute_parade': 0.55, 'short_smooth_velocity': 0.3, 'short_raw': 0.15})
    if template == 'short_cute_parade' and len(pool) < 3:
        template = 'short_smooth_velocity'
    style, group, grade = STYLE[template]
    fps = int(opts.get('fps') or 60)
    # dance refs: 85-125 BPM, bars of ~2 s; 8 bars for the parade (Uf_4 15 s)
    P0 = float(A['beat_period'])
    k = MU.pulse_factor(P0, int(A.get('meter') or 4), 0.46, 0.72)
    bar = 4 * k * P0
    # Uf_4: 3.5 bars of dancers + 3 bars of hero + 1 slow bar = 15 s; slow-felt songs get 2 dancers
    n_par = 3 if bar <= 2.3 else 2
    hero_bars = max(2, int(round(6.5 / bar)))
    n_bars = {'short_cute_parade': n_par + hero_bars + 1, 'short_smooth_velocity': max(5, int(round(16.0 / bar))),
              'short_raw': 4}[template]
    L = float(opts.get('length') or bar * n_bars)
    win = MU.choose_window(A, L, (0.0, 0.0), min_len=L * 0.85, max_len=L * 1.15, rng=rng)
    view = MU.window_view(A, win['start'], win['end'], 0.46, 0.72)
    B = Builder(view, pool, rng, fps, group, template, '9:16')
    D = view['duration']
    P = B.P
    downs = [d for d in B.downs if -1e-6 <= d < D - 0.3]
    if not downs or downs[0] > 0.05:
        downs = [0.0] + downs
    # bars of ~2 s (group tracked bars when they are short)
    fb = downs if (len(downs) < 2 or downs[1] - downs[0] >= 1.6) else downs[::2]
    cute = rng.random() < 0.7
    overlays = []
    grade = 'cute' if cute and template != 'short_smooth_velocity' else grade
    if template == 'short_cute_parade':
        # bars 1-3: one dancer per bar, switched ~0.3 beat (-90 ms) before the downbeat, a transition at every switch
        n = len(fb)
        n_par = max(1, min(n_par, n - 2, len(pool) - 1))
        switch_tr = {k: v for k, v in DANCE_TR_P.items() if k not in ('hard', 'lens_zoomthrough', 'flash_white')}
        if not cute:
            switch_tr.pop('pattern_flash', None)
        last = None
        for j in range(n_par):
            tr = 'none' if j == 0 else wpick(rng, switch_tr, exclude=(last,))
            B.add(fb[j], tr, lead=-0.09, role='dance', speed={'kind': 'burst_land', 'A': 1.19}, layout='fill', section='parade')
            last = tr
        # bars 4-7: the hero dancer in one take (two when no clip is long enough), a punch on every beat
        hero_start = fb[n_par] if n_par < n else D * 0.4
        last_bar = fb[-1] if len(fb) >= n_par + 2 else None
        hero_end = last_bar if last_bar is not None and last_bar > hero_start + 2 * P else D
        longest = max((s['duration'] for s in pool.shots), default=0.0)
        tr = wpick(rng, {'flash_white': 0.3, 'zoom_blur_in': 0.3, 'defocus_in': 0.25, 'lens_zoomthrough': 0.15})
        tail = 0.5 * max(0.0, D - hero_end) + 0.05
        B.add(hero_start, tr, lead=B.lead(tr), role='dance', speed={'kind': 'burst_land', 'A': 1.1}, layout='fill', section='hero', gap=8,
              tail_need=tail)
        hero_idx = len(B.segs) - 1
        if longest < 1.1 * (hero_end - hero_start) + tail:
            mid = [d for d in fb if hero_start + 1.5 < d < hero_end - 1.5]
            if mid:
                B.segs[hero_idx].pop('tail_need', None)
                B.add(mid[len(mid) // 2], wpick(rng, {'zoom_blur_in': 0.5, 'defocus_in': 0.5}), role='dance',
                      speed={'kind': 'burst_land', 'A': 1.1}, layout='fill', section='hero', gap=8, tail_need=tail)
                hero_idx = len(B.segs) - 1
        if hero_end < D - 0.5:
            # the last bar: constant 0.5x slow motion of the same take (no cut, no accents)
            B.add(hero_end, 'none', lead=0.0, role='dance', speed={'kind': 'slowmo', 'rate': 0.5}, layout='fill', same_as=hero_idx,
                  section='ending')
    elif template == 'short_smooth_velocity':
        hero_end = fb[min(len(fb) - 1, max(2, int(len(fb) * 0.6)))]
        B.add(0.0, 'none', role='dance', speed={'kind': 'smooth_wave', 'A': 1.2}, layout='fill', section='hero')
        t = hero_end
        while t < D - 2.2:
            tr = wpick(rng, {'defocus_in': 0.5, 'zoom_blur_in': 0.5})
            B.add(t, tr, role='dance', speed={'kind': 'smooth_wave', 'A': 1.2}, layout='fill', section='cuts')
            t += P * rng.choice([1, 2, 2])
            j = int(np.argmin(np.abs(B.beats - t)))
            t = float(B.beats[j])
        # a 1.5-2 s freeze on the last take to end
        ft = max(B.segs[-1]['t0'] + 2 * P, D - rng.uniform(1.5, 2.0))
        j = int(np.argmin(np.abs(B.beats - ft)))
        if B.beats[j] > B.segs[-1]['t0'] + P:
            ft = float(B.beats[j])
        B.add(ft, 'none', lead=0.0, role='dance', speed={'kind': 'freeze'}, layout='fill', same_as=len(B.segs) - 1, section='freeze')
    else:  # short_raw: 4 shots x 1 bar, hard cuts ~60 ms before the downbeat, source speed
        for j, d in enumerate(fb[:4]):
            B.add(d, 'hard', role='dance', speed={'kind': 'normal'}, layout='fill', section='raw')
    B.close(D)
    # camera: a static crop centred on the dancer, one punch per beat in the parade and hero take
    for i, s in enumerate(B.segs):
        acc = []
        if template != 'short_raw' and s.get('section') in ('parade', 'hero', 'cuts'):
            acc = dance_accents(rng, B.beats, s['t0'], s['t1'], P, cute)
        s['camera'] = dict(move='static', zoom=[1.0, 1.0], focus='dancer', follow=template == 'short_smooth_velocity',
                           follow_max_frac=0.12, accents=acc, handheld=None,
                           entry=dict(type='settle', scale0=round(rng.uniform(1.13, 1.26), 3), frames30=3, decay=0.55)
                           if s['transition_in'] in ('hard', 'zoom_blur_in') and rng.random() < 0.5 else None)
    B.fill()
    if template == 'short_cute_parade' and cute and rng.random() < 0.25:
        # the bokeh confetti of the Uf_4 ED footage
        overlays.append(dict(type='particles', kind='confetti', t0=0.0, t1=round(D, 3), n=40, opacity=0.45))
    fade = 0.0
    if template == 'short_raw' or rng.random() < 0.3:
        fade = 0.8
        overlays.append(dict(type='fade', t0=round(D - fade, 3), t1=round(D, 3), color='black', dir='out'))
    # the dance references carry no title text
    return finish(B, A, win, view, opts, template, style, group, grade, overlays, '9:16',
                  audio=dict(fade_in=0.0, fade_out=fade), extra=dict(cute=cute), show_title=False)


# ---------------------------------------------------------------- character edit

def character_family(A, rng, framing, forced=None):
    if forced:
        fam = forced
    else:
        bpm = float(A['bpm'])
        felt = bpm if 85 <= bpm <= 140 else (bpm / 2 if bpm > 140 else bpm * 2)
        perc = np.asarray(A.get('beat_perc_db') or [0], float)
        loud = np.asarray(A.get('beat_loud_db') or [0], float)
        drumsy = float(np.median(perc - loud)) if len(perc) == len(loud) else -6.0
        w = {'quick_velocity': 0.65, 'soft_romance': 0.25, 'raw_crisp': 0.1}
        if drumsy < -9.0 or felt < 88:
            w['soft_romance'] *= 2.5
            w['quick_velocity'] *= 0.5
        fam = wpick(rng, w)
    # attractiveness framing (soft romance: hearts, pastel, lingering) is for adult characters only
    if fam == 'soft_romance' and framing != 'attractive':
        fam = 'quick_velocity'
    return fam


def plan_character_edit(A, shots, opts):
    rng = random.Random(opts.get('seed'))
    pool = Pool(shots, rng, char_min=opts.get('char_min', 0.0))
    if len(pool) < 3:
        raise RuntimeError(f'only {len(pool)} usable shots of the character')
    framing = opts.get('framing') or 'aura'
    family = character_family(A, rng, framing, opts.get('template'))
    template = family
    style, group, grade = STYLE[template]
    grade = wpick(rng, CHAR_GRADES[family])
    fps = int(opts.get('fps') or 60)
    L = float(opts.get('length') or rng.uniform(20, 26))
    bar0 = float(A['beat_period']) * MU.pulse_factor(float(A['beat_period']), int(A.get('meter') or 4), 0.43, 0.71) * 4
    intro_s = float(np.clip(bar0 * rng.choice([1, 2]), 2.5, 8.0))
    win = MU.choose_window(A, L, (max(2.0, intro_s - bar0 * 0.5), intro_s + bar0 * 0.5), min_len=L * 0.8, max_len=L * 1.2,
                           contrast=0.6, rng=rng)
    view = MU.window_view(A, win['start'], win['end'], 0.43, 0.71)
    B = Builder(view, pool, rng, fps, group, template, '9:16')
    D = view['duration']
    P = B.P
    drop = win['drop'] - win['start']
    downs = [d for d in B.downs if -1e-6 <= d < D]
    soft = family == 'soft_romance'
    layout = wpick(rng, {'window_2x3': 0.55, 'window_3x4': 0.3, 'fill': 0.15})
    # the house transition takes 45-85% of the chorus cuts: only the main ones qualify (yUui flash_zoom, KSjp mb_cut ...)
    house = wpick(rng, {k: v for k, v in CHAR_CUT_P[family].items() if v >= 0.1})
    opening = wpick(rng, {'bar_open_reveal': 0.3, 'fade_from_black': 0.4, 'hard': 0.3})
    # --- hook intro: ONE hero shot (face close-up / look-back), slow push-in, native speed
    if drop > 0.6:
        n_intro = 1 if drop < 2 * view['bar'] + 0.1 or rng.random() < 0.6 else 2
        B.add(0.0, 'none', role='face', speed={'kind': 'normal'}, layout=layout,
              first={'bar_open_reveal': 'bar_open_reveal', 'fade_from_black': 'fade_in'}.get(opening, 'none'),
              camera=dict(move='push_in', zoom=[1.0, round(math.exp(rng.uniform(0.04, 0.08) * drop / max(1, n_intro)), 4)],
                          ease='linear', handheld=None), section='intro')
        if n_intro == 2:
            mid = min(downs, key=lambda d: abs(d - drop / 2)) if downs else drop / 2
            if 1.0 < mid < drop - 1.0:
                B.add(mid, 'hard' if not soft else 'defocus_dissolve', role='face', speed={'kind': 'normal'}, layout=layout,
                      camera=dict(move='push_in', zoom=[1.0, 1.05], ease='linear', handheld=None), section='intro')
    # --- the drop + chorus block(s)
    entry = wpick(rng, CHAR_DROP_ENTRY) if not soft else wpick(rng, {'white_out': 0.6, 'impact_zoom_1frame': 0.4})
    devices = 0
    t = drop
    block_beats = rng.choice([8, 16]) if P < 0.62 else 8
    replay = rng.random() < 0.6          # character.json cut_rules.phrase_block p_repeat
    block_a = []                         # (segment index, time since the drop) of block A
    replayed = False
    breath_at = None
    if rng.random() < 0.5:
        # one 3-5 beat hero hold at 0.5x before the last phrase
        cand = [d for d in downs if drop + (2 if replay else 1) * block_beats * P - 0.05 < d < D - 6 * P]
        if cand:
            breath_at = cand[-1]
    first = True
    prev_dir = 1
    loud_cut = float(np.percentile(B.energy, 60)) if len(B.energy) else 0.7
    heroes_ok = CHAR_BLOCK_HERO if soft else {k: v for k, v in CHAR_BLOCK_HERO.items() if k != 'iris_wipe'}
    last_tr = None
    last_pair = -1e9
    while t < D - max(0.8, 0.6 * P):
        e = B.e_fwd(t)
        down = B.is_down(t)
        rel_beat = int(round((t - drop) / P))
        if replay and not replayed and rel_beat >= block_beats and block_a:
            # block A again: the same cuts, transitions, speeds and (70-100% of) the same shots
            swap = rng.uniform(0.0, 0.3)
            for idx, rel in block_a:
                tt = t + rel
                if tt >= D - max(0.8, 0.6 * P) or (breath_at is not None and tt >= breath_at - 0.05):
                    break
                src = B.segs[idx]
                B.add(tt, src['transition_in'], lead=src['t0'] - src['beat'] if idx else None, role=src['role'],
                      speed=dict(src['speed']), layout=layout, section='chorus_repeat',
                      copy_of=None if rng.random() < swap else idx, shake=src.get('shake'))
            replayed = True
            t = t + block_beats * P
            j = int(np.argmin(np.abs(B.beats - t)))
            t = float(B.beats[j])
            continue
        seg_layout, seg_postcard = layout, None
        if first:
            tr, role = entry, 'hero'
        elif down and rel_beat % block_beats == 0 and devices < 2 and rng.random() < 0.5:
            tr = wpick(rng, heroes_ok)
            devices += 1
            role = 'face'
            # character.json: two of these devices are shot LAYOUTS held 0.3-1 s, not cuts - the mGz9 title
            # postcard and the YH tilted 3D card. layouts.Postcard / layouts.Card3D existed but no planner
            # ever emitted shot["layout"], so the card silently rendered as a growing card and the postcard
            # never appeared at all. The heart windows (rfsj) are the soft-romance version.
            if tr == 'card_tilt_in' and rng.random() < 0.5:
                # round 4: the layout has its own 4-frame defocus entry; a full flash_zoom in front of it left
                # 0.2 s of mush (critic: unreadable until frame 12). mb_cut = 2 blurred frames, then the card.
                seg_layout, tr = 'card3d', 'mb_cut' if not soft else 'glow_cut'
            elif tr == 'shape_wipe' and soft and rng.random() < 0.5:
                seg_layout, tr = 'hearts', 'hard'            # rfsj: the layout's own closing iris becomes the heart (no grey veil)
            elif tr == 'mirror_tile_scale_in' and rng.random() < 0.3:
                seg_layout = 'postcard'
                seg_postcard = dict(variant='pastel' if soft else 'character', bw=(not soft and rng.random() < 0.3),
                                    entry='slide_up')
        else:
            tr = house if rng.random() < 0.7 else wpick(rng, CHAR_CUT_P[family], exclude=(house, last_tr))
            role = 'face' if rng.random() < 0.45 else 'hero'
        last_tr = tr
        base = rng.uniform(0.5, 0.8)
        shape = 'flat' if family == 'raw_crisp' else wpick(rng, {'impact': 0.55, 'dip': 0.3, 'flat': 0.15})
        spd = {'kind': 'normal'} if family == 'raw_crisp' else {'kind': 'character', 'shape': shape, 'base': round(base, 3)}
        breath = breath_at is not None and abs(t - breath_at) < 0.05
        if breath:
            spd = {'kind': 'slowmo', 'rate': round(rng.uniform(0.4, 0.5), 3)}
            role = 'face'
            tr = 'impact_zoom_1frame' if not soft else 'white_out'
        if breath:
            seg_layout, seg_postcard = layout, None
        seg = B.add(t, tr, role=role, speed=spd, layout=seg_layout, postcard=seg_postcard,
                    section='drop' if first else ('breath' if breath else 'chorus'),
                    shake=first or (down and rel_beat % block_beats == 0))
        if seg is not None and 0 <= rel_beat < block_beats and not breath:
            block_a.append((len(B.segs) - 1, round(t - drop, 4)))
        # interval (character.json cut_rules.by_energy)
        if breath:
            step = rng.choice([3, 4, 5])
        elif first:
            step = 2  # no cut in the 0.25 s after the drop (and hold the drop shot)
        elif soft:
            step = wpick(rng, {2: 0.7, 1: 0.15, 4: 0.15})
        elif e >= 0.7 or e >= loud_cut:
            step = wpick(rng, {1: 0.6, 0.5: 0.2, 2: 0.12, 3: 0.08})
        elif e >= 0.4:
            step = wpick(rng, {2: 0.7, 1: 0.2, 4: 0.1})
        else:
            step = 4
        if step == 0.5:
            if e >= loud_cut and not soft and t - last_pair >= 4 * P - 0.05 and t - drop >= 2 * P - 0.05:
                last_pair = t
                # 1/2-beat cuts come as a pair inside one beat, only when it is loud (KSjp, YH: 8-12 of 22 intervals)
                s2 = B.add(t + 0.5 * P, 'mb_cut', role='hero', speed={'kind': 'character', 'shape': 'impact', 'base': round(base, 3)},
                           layout=layout, section='chorus')
                if s2 is not None and 0 <= rel_beat < block_beats:
                    block_a.append((len(B.segs) - 1, round(t + 0.5 * P - drop, 4)))
            step = 1
        first = False
        nt = t + step * P
        if breath_at is not None and t < breath_at < nt - 0.1:
            nt = breath_at
        if replay and not replayed and t - drop < block_beats * P - 0.1 < nt - drop:
            nt = drop + block_beats * P  # block A ends exactly on its last beat
        j = int(np.argmin(np.abs(B.beats - nt)))
        t = float(B.beats[j]) if abs(B.beats[j] - nt) < 0.3 * P else nt
    B.close(D)
    for i, s in enumerate(B.segs):
        if s.get('camera') is None:
            s['camera'], prev_dir = cam_character(rng, family, i, prev_dir, s['transition_in'], shake=bool(s.get('shake')))
    B.fill()
    B.assign_panels()
    overlays = []
    if family != 'raw_crisp' and rng.random() < 0.5:
        # sparkles / bokeh / gold dust in 5 of 8 references; hearts only in soft (adult) edits
        kinds = ['sparkles', 'bokeh', 'gold_dust'] + (['hearts'] if soft else [])
        overlays.append(particles_overlay(style, rng.choice(kinds), drop, D))
    end = wpick(rng, {'fade_black': 0.5, 'white_to_black': 0.22 if soft else 0.0, 'cut_to_black': 0.13,
                      'bw_selective': 0.15})
    fo = round(rng.uniform(0.4, 0.8), 3) if end != 'cut_to_black' else 0.05
    if end == 'white_to_black':
        overlays.append(dict(type='fade', t0=round(D - fo - 0.4, 3), t1=round(D - fo, 3), color='white', dir='out'))
    if end == 'bw_selective':
        # character.json (m3lv): the last bars keep only the dominant hue and drain the rest. The engine has
        # had this overlay all along but nothing ever emitted it, so the look could never appear.
        overlays.append(dict(type='bw_selective', t0=round(max(drop, D - 2.0), 3), t1=round(D, 3)))
    # yUui: a held shot cycles colour on every half beat; PJUNKIE: a light streak writes the closing word
    if not soft and rng.random() < 0.18:
        hold = max((s for s in B.segs if s['t1'] - s['t0'] > 1.2 * P and s['t0'] > drop + P),
                   key=lambda s: s['t1'] - s['t0'], default=None)
        if hold is not None:
            overlays.append(dict(type='colour_cycle', t0=round(hold['t0'], 3), t1=round(hold['t1'], 3)))
    if soft and rng.random() < 0.3:
        overlays.append(dict(type='light_streak', t0=round(max(0.0, D - fo - 1.2), 3), t1=round(D - fo, 3),
                             text=latin_text(opts.get('character'), 14) or None))
    overlays.append(dict(type='fade', t0=round(D - fo, 3), t1=round(D, 3), color='black', dir='out'))
    # the hook word / name lands with the drop (the style's title: YH 3D words, yUui thin caps); raw 4K edits have no text
    show_title = family != 'raw_crisp' and rng.random() < 0.7
    return finish(B, A, win, view, opts, template, style, group, grade, overlays, '9:16',
                  audio=dict(fade_in=0.15 if drop > 0.5 else 0.0, fade_out=max(0.3, fo)),
                  extra=dict(family=family, framing=framing, house=house, drop=round(drop, 4), entry=entry,
                             replayed=any(s.get('copy_of') is not None for s in B.segs)), show_title=show_title)


# ---------------------------------------------------------------- K-pop edit

def plan_kpop_edit(A, shots, opts):
    rng = random.Random(opts.get('seed'))
    pool = Pool(shots, rng)
    if len(pool) < 3:
        raise RuntimeError(f'only {len(pool)} usable K-pop shots')
    template = opts.get('template') or wpick(rng, {'postcard_member_edit': 0.5, 'stage_mv_member_edit': 0.32, 'single_clip_trend': 0.18})
    if template == 'postcard_member_edit' and len(pool) < 4:
        template = 'stage_mv_member_edit'
    longest = max((x['duration'] for x in pool.shots), default=0.0)
    if template == 'single_clip_trend' and longest < 8.5 and not opts.get('template'):
        # one take for the whole edit (fSMk, pB7P): only with a long fancam / stage take
        template = 'postcard_member_edit' if len(pool) >= 4 else 'stage_mv_member_edit'
    style, group, grade = STYLE[template]
    fps = int(opts.get('fps') or 60)
    L = float(opts.get('length') or {'postcard_member_edit': 17.0, 'stage_mv_member_edit': 14.0, 'single_clip_trend': 11.0}[template])
    intro = {'postcard_member_edit': (5.0, 7.0), 'stage_mv_member_edit': (3.4, 5.5), 'single_clip_trend': (4.0, 6.8)}[template]
    win = MU.choose_window(A, L, intro, min_len=max(10.5, L * 0.8), max_len=min(20.0, L * 1.2), contrast=1.0, rng=rng)
    view = MU.window_view(A, win['start'], win['end'], 0.5, 0.75)
    B = Builder(view, pool, rng, fps, group, template, '9:16')
    D = view['duration']
    P = B.P
    drop = win['drop'] - win['start']
    layout = 'square_band' if rng.random() < 0.4 else 'fill'
    house = 'strobe_smear' if template == 'single_clip_trend' else wpick(rng, KPOP_HOUSE)
    if template == 'stage_mv_member_edit' and house == 'vslide_wrap':
        house = 'hard_punch'
    overlays = []
    audio = dict(fade_in=round(rng.uniform(0.5, 1.0), 2), fade_out=0.0, mute=[])
    title = latin_text(opts.get('title'))
    first_hold = P * rng.choice([2, 3])  # kpop.json structure.drop.first_shot_hold_beats
    if template == 'postcard_member_edit':
        # the drop clip plays inside card 4 (it develops from photo paper from ~0.9 s before the zoom), zooms to fill
        # and the grade pops on the beat; before card 4 rises the clip is not on screen, so it waits (freeze)
        # the engine's postcard_intro scene draws the cards over black and plays the shot that starts on the drop
        # inside card 4 from ~2 s before it: that shot needs clip time before its src_in (min_src)
        B.add(0.0, 'none', role='face', speed={'kind': 'freeze'}, layout='fill', section='intro', hidden=True)
        B.add(drop, 'none', lead=0.0, role='face', speed={'kind': 'velocity_kpop'}, layout=layout, section='drop',
              camera=cam_kpop(rng), min_src=2.4, align='start')
    elif template == 'stage_mv_member_edit':
        # establishing push-in on the widest shot, zoom-through into the member close-up on the drop
        B.add(0.0, 'none', role='wide', speed={'kind': 'normal'}, layout=layout, section='intro',
              camera=dict(move='establishing_push', ln_scale='0.098*t + 0.094*t^2', start_after_s=0.6, handheld=None,
                          ends_in='zoom_through'))
        B.add(drop, 'zoom_through', role='face', speed={'kind': 'velocity_kpop'}, layout=layout, section='drop', camera=cam_kpop(rng))
    else:
        # single-clip trend: text on black (or a single take + freeze with 1.5 s of silence), then one take with accents
        if rng.random() < 0.5 and title:
            # pB7P: lowercase words on black until the fancam drops in
            B.add(0.0, 'none', role='face', speed={'kind': 'freeze'}, layout='fill', section='intro', hidden=True)
            overlays.append(dict(type='text_on_black', t0=0.0, t1=round(drop, 3), text=title.lower(), style='lower'))
            B.add(drop, 'hard', lead=0.0, role='face', speed={'kind': 'velocity_kpop', 'lo': 0.6, 'hi': 1.0}, layout=layout,
                  section='take', camera=dict(move='static', handheld=None), align='start')
        else:
            # fSMk: one stage take, a freeze frame with 1.5 s of total silence, then the drop
            fz = max(0.5, drop - 1.5)
            B.add(0.0, 'none', role='face', speed={'kind': 'normal'}, layout=layout, section='intro', camera=dict(move='static', handheld=None),
                  align='start', need_total=fz + 0.8 * (D - drop) + 0.3)
            B.add(fz, 'none', lead=0.0, role='face', speed={'kind': 'freeze'}, layout=layout, same_as=0, section='freeze',
                  camera=dict(move='static', handheld=None))
            audio['mute'].append([round(fz, 3), round(drop - 0.01, 3)])
            overlays.append(dict(type='mute', t0=round(fz, 3), t1=round(drop - 0.01, 3)))
            B.add(drop, 'none', lead=0.0, role='face', speed={'kind': 'velocity_kpop', 'lo': 0.6, 'hi': 1.0}, layout=layout, same_as=1,
                  section='take', camera=dict(move='static', handheld=None))
        # the take drops in on a vertical smear, then strobe / smear accents on 8th notes that carry a strong
        # onset (3-4 per bar, >= 0.3 s apart; pB7P, fSMk), attached to the take's camera like the dance punches
        acc, last = [dict(t=round(drop, 4), type='vsmear')], drop
        ons = np.asarray(view['onsets'], float)
        per_bar = {}
        for g in np.arange(drop + P / 2, D - 0.3, P / 2):
            bi = B.bar_index(g)
            if len(ons) and np.min(np.abs(ons - g)) < 0.04 and g - last >= 0.3 and per_bar.get(bi, 0) < 4:
                acc.append(dict(t=round(float(g), 4), type='strobe_smear' if rng.random() < 0.6 else 'vsmear'))
                per_bar[bi] = per_bar.get(bi, 0) + 1
                last = g
        B.segs[-1]['camera'] = dict(move='static', handheld=None, accents=acc)
        overlays.append(dict(type='echo', t0=round(D - min(D - drop, 4 * P * 2), 3), t1=round(D, 3), scale=1.35, shift_h=-0.15,
                             opacity=0.35))
        grade = rng.choice(['tint_hue_push', 'tint_pastel_cool'])
    if template != 'single_clip_trend':
        # beat section: 1-2 beat shots, one house transition, 1 hero per 4 bars previewed on the beat before a downbeat
        t = drop + first_hold
        heroes = 0
        last_hero_bar = None
        while t < D - max(1.0, 0.8 * P):
            e = B.e_fwd(t)
            down = B.is_down(t)
            bi = B.bar_index(t)
            # at most one hero per 4 bars, always on a downbeat (kpop.json transitions.hero.max_per_4_bars)
            hero = down and (last_hero_bar is None or bi - last_hero_bar >= 4) and heroes < 2 and rng.random() < 0.55
            if hero:
                tr = wpick(rng, KPOP_HERO)
                heroes += 1
                last_hero_bar = bi
                tp = {'exposure_bleach': rng.random() < 0.6}
                if tr in ('cutout_prereveal', 'slices_reveal', 'photo_clones'):
                    tp['preview_s'] = round(min(1.4, max(0.3, P)), 3)
            else:
                tr = house if rng.random() < 0.8 else wpick(rng, {'blur_in_punch': 0.5, 'hard_punch': 0.3, 'vslide_wrap': 0.2}, exclude=(house,))
                tp = None
            B.add(t, tr, role='face' if rng.random() < 0.6 else 'hero', speed={'kind': 'velocity_kpop'}, layout=layout,
                  tparams=tp, section='beat', camera=cam_kpop(rng))
            if e >= 0.7:
                step = wpick(rng, {1: 0.6, 2: 0.35, 3: 0.05})
            elif e >= 0.35:
                step = rng.choice([2, 3])
            else:
                step = 4
            nt = t + step * P
            j = int(np.argmin(np.abs(B.beats - nt)))
            t = float(B.beats[j]) if abs(B.beats[j] - nt) < 0.3 * P else nt
        # outro: dip-to-black chain on the last beats (stage) or a defocus fade
        if template == 'stage_mv_member_edit' or rng.random() < 0.4:
            last_beats = [b for b in B.beats if D - 3.2 * P < b < D - 0.3 * P][-3:]
            for b in last_beats:
                if b > B.segs[-1]['t0'] + 0.5 * P:
                    B.add(b, 'dip_black', role='face', speed={'kind': 'velocity_kpop'}, layout=layout, section='outro',
                          camera=dict(move='push_in', zoom=[1.0, 1.2], handheld=None, accents=[]))
        fo = round(rng.uniform(0.3, 1.1), 2)
        overlays.append(dict(type='fade', t0=round(D - fo, 3), t1=round(D, 3), color='black', dir='out', defocus=True))
        audio['fade_out'] = fo
    B.close(D)
    B.fill()
    # postcard intro: 3 stills (frames of other shots) + the live drop clip, retimed so the zoom ends 50 ms before the drop
    if template == 'postcard_member_edit':
        # 3 photo cards: crisp moments of other shots (the scene retimes the measured keyframes to end on the drop)
        drop_path = next((s['shot']['path'] for s in B.segs if s.get('section') == 'drop'), None)
        others = [s['shot'] for s in B.segs if s.get('section') == 'beat'] + list(pool.shots)
        stills, seen = [], {drop_path}
        for s in others:
            if s['path'] in seen:
                continue
            seen.add(s['path'])
            pk = s.get('peak')
            stills.append(dict(path=s['path'], clip_t=round(float(pk if pk is not None else s['duration'] * 0.4), 3)))
            if len(stills) == 3:
                break
        caption = latin_text(opts.get('caption'), 48) if opts.get('caption') else (rng.choice(MEME_CAPTIONS) if rng.random() < 0.35 else None)
        overlays.insert(0, dict(type='postcard_intro', t0=0.0, t1=round(drop, 4), cards=stills,
                                caption=caption.upper() if caption else None))
    if template == 'stage_mv_member_edit' and rng.random() < 0.5:
        hs = next((s for s in B.segs if s.get('section') == 'drop'), None)
        if hs and rng.random() < 0.5:
            # upHh: hearts / sparkles / a lightning bolt pop for 0.3-0.4 s on an expression moment of the hero close-up
            a = round(hs['t0'] + 0.3 * (hs['t1'] - hs['t0']), 3)
            overlays.append(dict(type='sticker', t0=a, t1=round(a + 0.35, 3), kind=rng.choice(['hearts', 'sparkles', 'heart_frame', 'lightning']),
                                 x=round(rng.uniform(0.3, 0.7), 3), y=round(rng.uniform(0.3, 0.45), 3)))
    # postcard / trend edits carry no title (the caption bar / words on black are the text); stage edits scroll the name
    return finish(B, A, win, view, opts, template, style, group, grade, overlays, '9:16', audio=audio,
                  extra=dict(house=house, layout=layout, drop=round(drop, 4)), show_title=template == 'stage_mv_member_edit')


# ---------------------------------------------------------------- long edit (16:9)

def plan_long_edit(A, shots, opts):
    """
    16:9, 2-4 min: the full song section by section (general.json style.structure.long, dance.json
    templates.long_amv, character.json structure.long_template, kpop format.long).
    """
    rng = random.Random(opts.get('seed'))
    flavor = opts.get('flavor') or 'song'
    pool = Pool(shots, rng, char_min=opts.get('char_min') if flavor == 'character' else None)
    if len(pool) < 8:
        raise RuntimeError(f'only {len(pool)} usable shots for a long edit')
    template = f'long_{flavor}'
    style, group, grade = STYLE[template]
    fps = int(opts.get('fps') or 30)
    w = MU.choose_long_window(A, float(opts.get('min_len') or 120), float(opts.get('max_len') or 240))
    lo, hi = (0.46, 0.72) if flavor == 'dance' else (0.43, 0.75)
    view = MU.window_view(A, w['start'], w['end'], lo, hi)
    B = Builder(view, pool, rng, fps, group, template, '16:9')
    D = view['duration']
    P = B.P
    secs0 = view['sections'] or [dict(start=0.0, end=D, label='chorus', energy=0.8)]
    # only the last 2 bars before a chorus accelerate (2 -> 1 -> 1/2 beat); the rest of a long build is verse
    secs = []
    bar = float(view['bar'])
    for s0 in secs0:
        if s0['label'] == 'build' and s0['end'] - s0['start'] > 3 * bar:
            cut = s0['end'] - 2 * bar
            secs.append(dict(s0, end=round(cut, 4), label='verse'))
            secs.append(dict(s0, start=round(cut, 4), label='build'))
        else:
            secs.append(dict(s0))
    family = 'quick_velocity'
    house_char = wpick(rng, CHAR_CUT_P[family])
    kpop_house = wpick(rng, KPOP_HOUSE)
    overlays = []
    title = latin_text(opts.get('title'), 40)
    n_ch = 0
    prev_dir = 1
    hook_pattern = None  # dance: the first hook's cut pattern, replayed with new clips
    letterbox_secs = set()
    for si, s in enumerate(secs):
        lab = s['label']
        a, b = max(0.0, s['start']), min(D, s['end'])
        if b - a < 0.5:
            continue
        chorus = lab in ('chorus', 'drop')
        # a chorus straight after a chorus / drop continues it: no second drop flash
        cont = chorus and si > 0 and secs[si - 1]['label'] in ('chorus', 'drop') and abs(secs[si - 1]['end'] - s['start']) < 0.1
        if chorus and not cont:
            n_ch += 1
            # each chorus picks its own house transition (3 minutes of one transition is monotone)
            house_char = wpick(rng, {k: v for k, v in CHAR_CUT_P[family].items() if v >= 0.1})
            kpop_house = wpick(rng, KPOP_HOUSE)
        final = chorus and not any(x['label'] in ('chorus', 'drop') for x in secs[si + 1:])
        if lab == 'verse' and flavor == 'song' and rng.random() < 0.3:
            letterbox_secs.add(si)
        t = a
        first_in_sec = True
        is_intro = lab == 'intro' or (si == 0 and not chorus)
        # the dance hook B replays hook A's cut pattern (1L7: the 14.5 s hook block copied)
        if flavor == 'dance' and chorus and hook_pattern and n_ch >= 2:
            for rel, tr in hook_pattern:
                tt = a + rel
                if tt >= b - 0.3:
                    break
                B.add(tt, tr, role='dance', speed={'kind': 'burst_land', 'A': 1.15}, layout='fill', section=lab, sec=si)
            continue
        pat = []
        while t < b - max(0.3, 0.4 * P):
            e = B.e_at(t)
            down = B.is_down(t)
            bar_i = B.bar_index(t)
            role = 'calm'
            spd = {'kind': 'normal'}
            tp = None
            seg_layout = 'fill'
            # ---- transition + interval by section
            if is_intro:
                tr = 'none' if not B.segs else ('first_shot_entry' if group == 'general' else 'defocus_in' if group == 'dance' else 'white_out'
                                                if group == 'character' else 'blur_in_punch')
                if B.segs and group == 'general':
                    tr = 'dissolve_blur'
                role, spd = 'hero', {'kind': 'slowmo', 'rate': 0.6}
                step = rng.choice([4, 8]) if flavor != 'dance' else 2
            elif lab in ('verse', 'outro'):
                if group == 'general':
                    tr = 'blur_bridge'
                    tp = {'soft': True}
                elif group == 'dance':
                    tr = wpick(rng, {'defocus_in': 0.4, 'cross_dissolve': 0.3, 'zoom_blur_in': 0.3})
                elif group == 'character':
                    tr = wpick(rng, {'defocus_dissolve': 0.4, 'impact_zoom_1frame': 0.3, 'whip_vertical': 0.3})
                else:
                    tr = kpop_house
                role = 'calm' if e < 0.5 else 'build'
                spd = {'kind': 'slowmo', 'rate': round(rng.uniform(0.45, 0.6), 3)} if e < 0.45 or rng.random() < 0.4 else \
                    ({'kind': 'burst_land', 'A': 1.1} if flavor == 'dance' else {'kind': 'velocity_u'})
                step = wpick(rng, {1: 0.45, 2: 0.45, 4: 0.1}) if flavor != 'dance' else rng.choice([2, 4])
            elif lab == 'build':
                left = b - t
                step = 2 if left > 1.5 * bar else (1 if left > 0.75 * bar else 0.5)
                last_cut = left <= step * P + 0.05
                # blur bridges while it builds, a zoom-through into the drop
                tr = {'general': 'zoom_through' if last_cut else 'blur_bridge', 'dance': 'zoom_blur_in' if last_cut else 'defocus_in',
                      'character': 'flash_zoom' if last_cut else 'mb_cut', 'kpop': 'zoom_through' if last_cut else 'blur_in_punch'}[group]
                if tr == 'blur_bridge':
                    tp = {'flash': False}
                role, spd = 'build', ({'kind': 'burst_land', 'A': 1.19} if flavor == 'dance' else {'kind': 'velocity_u'})
            elif lab == 'bridge':
                tr = {'general': 'dissolve_blur', 'dance': 'defocus_in', 'character': 'white_out', 'kpop': 'dip_black'}[group]
                role, spd = 'calm', {'kind': 'slowmo', 'rate': round(rng.uniform(0.4, 0.6), 3)}
                step = rng.choice([2, 4])
            else:  # chorus / drop
                role = 'drop'
                if first_in_sec and not cont:
                    tr = {'general': 'flash_white', 'dance': 'flash_white', 'character': wpick(rng, CHAR_DROP_ENTRY),
                          'kpop': 'zoom_blur_flash'}[group]
                    role = 'hero'
                elif down and (bar_i % 4 == 0 or first_in_sec):
                    tr = {'general': wpick(rng, GEN_HERO_P), 'dance': wpick(rng, {'flash_white': 0.4, 'zoom_blur_in': 0.4, 'pattern_flash': 0.2}),
                          'character': wpick(rng, {k: v for k, v in CHAR_BLOCK_HERO.items() if k != 'iris_wipe'}),
                          'kpop': wpick(rng, KPOP_HERO)}[group]
                    if tr == 'split_screen':     # a shot layout, not a cut (see plan_song_edit)
                        seg_layout, tr = 'split2', 'white_bar_wipe'
                else:
                    if group == 'general':
                        tr = wpick(rng, GEN_CUT_P)
                        if tr == 'blur_bridge':
                            tp = {'flash': rng.random() < (0.64 if final else 0.4)}
                    elif group == 'dance':
                        tr = wpick(rng, DANCE_TR_P, exclude=('lens_zoomthrough',))
                    elif group == 'character':
                        tr = house_char if rng.random() < 0.7 else wpick(rng, CHAR_CUT_P[family], exclude=(house_char,))
                    else:
                        tr = kpop_house
                if flavor == 'dance':
                    spd = {'kind': 'burst_land', 'A': 1.19}
                    role = 'dance'
                elif flavor == 'kpop':
                    spd = {'kind': 'velocity_kpop'}
                elif flavor == 'character':
                    spd = {'kind': 'character', 'shape': wpick(rng, {'impact': 0.55, 'dip': 0.3, 'flat': 0.15}),
                           'base': round(rng.uniform(0.5, 0.8), 3)}
                else:
                    spd = {'kind': 'velocity_u'} if rng.random() < 0.7 else {'kind': 'normal'}
                # 1 cut per beat; 1/2-beat doubles on 10-20% of loud beats
                step = 1
                if flavor == 'dance':
                    step = wpick(rng, {0.5: 0.1, 1: 0.45, 1.5: 0.2, 2: 0.25})
                elif e >= 0.75 and rng.random() < (0.2 if final else 0.12):
                    step = 0.5
            if group == 'dance' and tr in ('lens_zoomthrough',):
                tr = 'zoom_blur_in'
            role = 'dance' if flavor == 'dance' else role
            seg = B.add(t, tr, role=role, speed=spd, layout=seg_layout, tparams=tp, section=lab, sec=si,
                        bars=0.09 if si in letterbox_secs else None, shake=first_in_sec and chorus and not cont)
            if seg is not None and flavor == 'dance' and chorus and n_ch == 1:
                pat.append((round(t - a, 4), tr))
            first_in_sec = False
            nt = t + step * P
            j = int(np.argmin(np.abs(B.beats - nt))) if len(B.beats) else 0
            t = float(B.beats[j]) if len(B.beats) and abs(B.beats[j] - nt) < 0.3 * P else nt
        if flavor == 'dance' and chorus and n_ch == 1:
            hook_pattern = pat
    B.close(D)
    for i, s in enumerate(B.segs):
        nxt = B.segs[i + 1]['transition_in'] if i + 1 < len(B.segs) else None
        if group == 'general':
            s['camera'] = cam_general(rng, 'flow_blur' if s.get('section') not in ('bridge', 'intro') else 'cinematic_slowmo',
                                      i, s['t0'], s['t1'], s['beat'], nxt)
        elif group == 'character':
            s['camera'], prev_dir = cam_character(rng, family, i, prev_dir, s['transition_in'], shake=bool(s.get('shake')))
        elif group == 'kpop':
            s['camera'] = cam_kpop(rng)
        else:
            acc = dance_accents(rng, B.beats, s['t0'], s['t1'], P) if s.get('section') in ('chorus', 'drop') else []
            s['camera'] = dict(move='static', zoom=[1.0, 1.0], accents=acc, handheld=None)
    # the intro: a music card (i4f / m3lv: album still, title, artist, EDIT BY) over the first shots, a vignette
    # fade-in from black (1L7, dance) or a short fade in
    first_ch = next((s for s in secs if s['label'] in ('chorus', 'drop')), None)
    intro_end = secs[0]['end'] if secs and secs[0]['label'] == 'intro' else min(D, 4.0)
    intro_kind = 'vignette' if group == 'dance' else ('music_card' if group == 'general' and title and rng.random() < 0.5 else 'fade')
    card_end = None
    if intro_kind == 'music_card':
        k = next((i for i, x in enumerate(B.segs) if x['t0'] >= min(3.0, intro_end)), None)
        if k and B.segs[k]['t0'] <= 7.5:
            card_end = B.segs[k]['t0']
            for x in B.segs[:k]:
                x['hidden'] = True
        else:
            intro_kind = 'fade'
    if intro_kind == 'vignette':
        B.segs[0]['transition_in'] = 'vignette_fade_in'
    elif intro_kind == 'fade':
        B.segs[0]['transition_in'] = 'fade_in'
    B.fill()
    B.assign_panels()
    if card_end is not None:
        overlays.append(dict(type='music_card', t0=0.0, t1=round(card_end, 3), title=latin_text(opts.get('song')) or title,
                             artist=latin_text(opts.get('artist')) or latin_text(opts.get('subtitle')) or '', line=title or '',
                             channel=latin_text(opts.get('watermark')) or ''))
    # letterbox bars on whole verse sections (4 of 6 AE refs), the bridge desaturated, a long fade out
    spans = []
    for x in B.segs:
        if x.get('bars'):
            if spans and abs(spans[-1][1] - x['t0']) < 1e-3:
                spans[-1][1] = x['t1']
            else:
                spans.append([x['t0'], x['t1']])
    for a, b in spans:
        overlays.append(dict(type='letterbox', t0=round(a, 3), t1=round(b, 3), frac=0.09))
    for x in secs:
        if x['label'] == 'bridge':
            overlays.append(dict(type='desaturate', t0=round(x['start'], 3), t1=round(x['end'], 3), amount=0.3))
    fo = round(rng.uniform(1.5, 3.0), 2)
    overlays.append(dict(type='fade', t0=round(D - fo, 3), t1=round(D, 3), color='black', dir='out'))
    return finish(B, A, dict(start=w['start'], end=w['end'], drop=first_ch['start'] + w['start'] if first_ch else w['start']),
                  view, opts, template, style, group, grade, overlays, '16:9', audio=dict(fade_in=0.3, fade_out=fo),
                  extra=dict(flavor=flavor, intro=intro_kind, sections=[f"{x['label']}@{x['start']:.1f}" for x in secs]), sections=secs,
                  show_title=card_end is None and group != 'kpop')


# ---------------------------------------------------------------- sound edit (core/edit/sound_edit.py)

def plan_sound_edit(A, shots, opts):
    """Delegates to core/edit/sound_edit.plan_sound_edit (the sound agent's builder), passing what it asks for."""
    try:
        import sound_edit as SE
    except Exception as e:
        raise RuntimeError(f'core/edit/sound_edit.py is not available: {e}')
    fn = getattr(SE, 'plan_sound_edit', None)
    if fn is None:
        raise RuntimeError('core/edit/sound_edit.py has no plan_sound_edit()')
    avail = dict(music=A['path'], music_path=A['path'], audio=A['path'], song=A['path'], analysis=A, music_analysis=A, A=A,
                 shots=shots, pool=shots, library=shots, opts=opts, options=opts, aspect='9:16', fps=int(opts.get('fps') or 60),
                 seed=opts.get('seed'), title=opts.get('song') or latin_text(opts.get('title')), subtitle=latin_text(opts.get('subtitle')),
                 watermark=opts.get('watermark'), length=opts.get('length'), duration=opts.get('length'),
                 heatmap=opts.get('heatmap'), work_dir=opts.get('work_dir'), workdir=opts.get('work_dir'))
    sig = inspect.signature(fn)
    kwargs, args = {}, []
    params = list(sig.parameters.values())
    var_kw = any(p.kind == p.VAR_KEYWORD for p in params)
    for p in params:
        if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            continue
        if p.name in avail and avail[p.name] is not None:
            kwargs[p.name] = avail[p.name]
        elif p.default is p.empty:
            raise RuntimeError(f'sound_edit.plan_sound_edit needs "{p.name}"')
    if var_kw:
        for k in ('fps', 'seed', 'title', 'watermark'):
            kwargs.setdefault(k, avail[k])
    plan = fn(*args, **kwargs)
    if isinstance(plan, str) and os.path.exists(plan):
        with open(plan, encoding='utf-8') as f:
            plan = json.load(f)
    if not isinstance(plan, dict) or not plan.get('shots'):
        raise RuntimeError('sound_edit.plan_sound_edit returned no plan')
    plan.setdefault('style', 'sound_edit')
    plan.setdefault('group', 'sound')
    plan.setdefault('template', 'sound_hit_sync')
    plan.setdefault('clip_audio', 'mix')
    plan.setdefault('aspect', '9:16')
    plan.setdefault('fps', int(opts.get('fps') or 60))
    plan.setdefault('watermark', opts.get('watermark'))
    plan.setdefault('title', latin_text(opts.get('title')))
    plan.setdefault('overlays', [])
    return plan


# ---------------------------------------------------------------- output

# the planners' layout choice -> the engine's video frame (styles.py `frame`) + the shot layout
FRAME_OF = {'fill': 'full', 'letterbox': 'letterbox_blur', 'rotated_16x9': 'rot90', 'band_16x9': 'band16x9',
            'window_2x3': 'window2x3', 'window_3x4': 'window3x4', 'square_band': 'band1x1'}


def particles_overlay(style, kind, t0, t1):
    """A particles overlay with the style's count / opacity (engine kinds: sparkles hearts bokeh embers confetti gold_dust petals stars)."""
    n, op = 50, 0.5
    if ST is not None:
        try:
            pc = (ST.resolve(style).get('overlays') or {}).get('particles') or {}
            n, op = int(pc.get('n', n)), float(pc.get('opacity', op))
        except Exception:
            pass
    return dict(type='particles', t0=round(float(t0), 3), t1=round(float(t1), 3), kind=kind, n=n, opacity=op)


def engine_camera(c):
    """The planners' camera dict in engine.py's keys: move / amount (ln scale over the shot) / zoom (base) / pan /
    roll (deg over the shot) / handheld (x the style's) / follow / accents [{at, kind}]. Entry settles and exit rushes
    ride the transitions (transitions.py), so they are not camera keys."""
    if not c:
        return None
    out = {}
    mv = c.get('move')
    out['move'] = {'entry_settle': 'hold', 'establishing_push': 'establishing'}.get(mv, mv) if mv else None
    if out['move'] is None:
        out.pop('move')
    z = c.get('zoom')
    if isinstance(z, (list, tuple)) and len(z) == 2:
        z0, z1 = max(1e-3, float(z[0])), max(1e-3, float(z[1]))
        out['amount'] = round(abs(math.log(z1 / z0)), 4)
        out['zoom'] = round(min(z0, z1), 4)
        if out.get('move') not in ('push_in', 'pull_out') and out['amount'] > 1e-3:
            out['move'] = 'push_in' if z1 > z0 else 'pull_out'
    elif isinstance(z, (int, float)):
        out['zoom'] = float(z)
    if 'amount' in c and isinstance(c['amount'], (int, float)):
        out['amount'] = float(c['amount'])
    if c.get('pan'):
        out['pan'] = [round(float(v), 4) for v in c['pan']]
    r = c.get('roll')
    if isinstance(r, (list, tuple)):
        r = (float(r[0]) - float(r[1])) if len(r) == 2 else float(r[0])
    if r:
        out['roll'] = round(float(r), 3)
    hh = c.get('handheld')
    out['handheld'] = 0.0 if not hh else (1.0 if isinstance(hh, dict) else float(hh))
    if c.get('follow'):
        out['follow'] = True
    acc = c.get('accents')
    if acc is not None:
        out['accents'] = [dict({k: v for k, v in a.items() if k not in ('t', 'type')}, at=float(a.get('at', a.get('t'))),
                               kind=a.get('kind', a.get('type'))) for a in acc]
    if c.get('keys'):
        out['keys'] = c['keys']          # a copied reference camera path (bank_camera / remakes): engine.py base_cam
        out['amount'] = 0.0
        if c.get('bank'):
            out['bank'] = c['bank']
    return out


def finish(B, A, win, view, opts, template, style, group, grade, overlays, aspect, audio=None, extra=None, sections=None,
           show_title=True):
    """The plan in engine.py's schema. The planner owns structure, so the style's automatic ending, particles and
    (unless show_title) title card are switched off; everything else the style supplies (looks, transition envelopes,
    text styles, watermark placement)."""
    D = round(view['duration'], 4)
    shots = B.shots_json()
    for sh in shots:
        sh['camera'] = engine_camera(sh.get('camera'))
    frames = [FRAME_OF[s['layout']] for s in shots if s['layout'] in FRAME_OF]
    frame = max(set(frames), key=frames.count) if frames else 'full'
    if aspect == '16:9':
        frame = 'full'
    for s in shots:
        if s['layout'] in FRAME_OF and s['layout'] != 'letterbox':
            s['layout'] = 'fill'  # the frame (window / band / rotation) is video-wide
    for o in overlays:
        for k in ('t0', 't1'):
            if k in o:
                o[k] = float(np.clip(o[k], 0.0, D))
    w, h = (1080, 1920) if aspect == '9:16' else (1920, 1080)
    beats = [b for b in view['beats'] if -1e-6 <= b <= D + 1e-6]
    energy = [e for b, e in zip(view['beats'], view['energy']) if -1e-6 <= b <= D + 1e-6]
    au = dict(fade_in=0.0, fade_out=0.0, target_lufs=-14.0, true_peak_db=-1.0, mute=[])
    au.update(audio or {})
    overrides = {'end': {'kind': 'cut', 'dur': 0.0}}
    if not show_title:
        overrides['text'] = {'title_when': 'none'}
    plan = dict(
        aspect=aspect, fps=B.fps, width=w, height=h, music=A['path'], music_start=round(float(win['start']), 4), duration=D,
        clip_audio='none', style=style, group=group, template=template, grade=grade, look=grade, frame=frame,
        title=latin_text(opts.get('title')), subtitle=latin_text(opts.get('subtitle')), watermark=opts.get('watermark'),
        bpm=view['bpm'], beat_period=view['P'], beats=[round(b, 4) for b in beats], downbeats=[round(d, 4) for d in view['downbeats'] if d <= D],
        raw_beats=[round(b, 4) for b in view['raw_beats'] if -1e-6 <= b <= D], energy=energy,
        sections=sections if sections is not None else view['sections'],
        drop=round(float(win.get('drop', win['start'])) - float(win['start']), 4),
        shots=shots, overlays=overlays, audio=au, lead='none', seed=opts.get('seed'), particles=False, style_overrides=overrides,
        planner=dict(type=opts.get('type'), seed=opts.get('seed'), window=win, show_title=show_title, **(extra or {})),
    )
    return plan


def check_plan(plan):
    """Problems that would make a bad render: gaps, off-grid cuts, reading past a clip, a clip twice in a row."""
    errs, warns = [], []
    shots = plan.get('shots') or []
    D = float(plan.get('duration') or 0)
    fps = float(plan.get('fps') or 60)
    if not shots:
        return ['no shots'], warns
    if abs(shots[0]['t0']) > 1e-6:
        errs.append(f'first shot starts at {shots[0]["t0"]}')
    if abs(shots[-1]['t1'] - D) > 1.0 / fps:
        errs.append(f'last shot ends at {shots[-1]["t1"]}, duration {D}')
    for i, s in enumerate(shots):
        if s['t1'] - s['t0'] < 0.99 / fps:
            errs.append(f'shot {i} is shorter than a frame')
        if i and abs(s['t0'] - shots[i - 1]['t1']) > 1e-4:
            errs.append(f'gap/overlap before shot {i}')
        if not s.get('path') or not os.path.exists(s['path']):
            errs.append(f'shot {i}: missing file')
        cd = float(s.get('clip_duration') or 0)
        cur = s.get('speed_curve')
        if cur:
            ct = [c[1] for c in cur]
            if any(b < a - 1e-4 for a, b in zip(ct, ct[1:])):
                errs.append(f'shot {i}: clip time runs backwards')
            if min(ct) < -1e-3 or (cd and max(ct) > cd + 0.02):
                errs.append(f'shot {i}: reads clip time {min(ct):.2f}..{max(ct):.2f} of a {cd:.2f} s clip')
        elif cd and s['src_in'] + (s['t1'] - s['t0']) > cd + 0.02:
            errs.append(f'shot {i}: 1x playback runs past the clip end')
        if i and s['path'] == shots[i - 1]['path'] and not s.get('continues') and s.get('transition_in') not in ('none',):
            src_prev = shots[i - 1].get('speed_curve')
            prev_end = src_prev[-1][1] if src_prev else shots[i - 1]['src_in'] + shots[i - 1]['t1'] - shots[i - 1]['t0']
            if abs(s['src_in'] - prev_end) < 0.3:
                warns.append(f'shot {i}: same clip and moment as the previous shot')
        if i and not s.get('continues') and s.get('beat') is not None:
            off = s['t0'] - s['beat']
            if off < -0.2 or off > 0.06:
                warns.append(f'shot {i}: cut {off * 1000:+.0f} ms from its beat')
    for o in plan.get('overlays') or []:
        if 't0' in o and 't1' in o and not (0 <= o['t0'] <= o['t1'] <= D + 1e-6):
            warns.append(f'overlay {o.get("type")} outside the video')
    return errs, warns


def summarize(plan):
    tr, sp = {}, {}
    for s in plan['shots']:
        tr[s['transition_in']] = tr.get(s['transition_in'], 0) + 1
        sp[s['speed']] = sp.get(s['speed'], 0) + 1
    ids = []
    chans = []
    for s in plan['shots']:
        if s.get('source_id') and s['source_id'] not in ids:
            ids.append(s['source_id'])
        if s.get('source_channel') and s['source_channel'] not in chans:
            chans.append(s['source_channel'])
    return dict(style=plan.get('style'), template=plan.get('template'), group=plan.get('group'), duration=plan.get('duration'),
                shots=len(plan['shots']), clips=ids, channels=chans, transitions=tr, speeds=sp, music_start=plan.get('music_start'),
                grade=plan.get('grade'), aspect=plan.get('aspect'))


PLANNERS = {'song': plan_song_edit, 'dance': plan_dance_edit, 'character': plan_character_edit, 'kpop': plan_kpop_edit,
            'sound': plan_sound_edit, 'long': plan_long_edit}


def build_plan(kind, A, shots, opts):
    opts = dict(opts)
    opts['type'] = kind
    return PLANNERS[kind](A, shots, opts)


def synthetic_analysis(dur=100.0, bpm=120.0):
    """A music.py-shaped analysis of an imaginary 4/4 pop song (intro, verse, build, chorus, verse, bridge, chorus)."""
    P = 60.0 / bpm
    beats = np.arange(0.5, dur - 0.5, P)
    didx = list(range(0, len(beats), 4))
    plan = [('intro', 0, 8, 0.3), ('verse', 8, 24, 0.5), ('build', 24, 32, 0.65), ('drop', 32, 48, 0.92), ('verse', 48, 64, 0.5),
            ('bridge', 64, 72, 0.2), ('chorus', 72, dur, 0.95)]
    e = np.zeros(len(beats))
    secs = []
    for lab, a, b, en in plan:
        m = (beats >= a) & (beats < b)
        e[m] = en
        secs.append(dict(start=float(a), end=float(min(b, dur)), label=lab, energy=en, rep=0.0, jump=8.0 if lab == 'drop' else 0.0))
    loud = -30 + 20 * e
    return dict(ok=True, path='', duration=dur, tracker='synthetic', bpm=bpm, beat_period=P, meter=4, straight_grid=True, grid_rms_ms=0.0,
                beats=beats.round(4).tolist(), downbeats=beats[didx].round(4).tolist(), downbeat_index=didx,
                beat_energy=e.round(3).tolist(), beat_loud_db=loud.round(2).tolist(), beat_perc_db=(loud - 3).round(2).tolist(),
                beat_low_db=(loud - 6).round(2).tolist(), beat_rep=[0.0] * len(beats), beat_voc=[0.0] * len(beats),
                bar_energy=e[didx].round(3).tolist(), bar_jump_db=[0.0] * len(didx), sections=secs,
                drops=[dict(bar=16, t=float(beats[64]), jump_db=8.0, after=0.9, before=0.6), dict(bar=36, t=float(beats[144]), jump_db=9.0,
                                                                                               after=0.95, before=0.2)],
                onsets=np.sort(np.concatenate([beats, beats + P / 2])).round(4).tolist(), heat=None, lufs=-9.0, true_peak_db=0.0)


def selftest(workdir=None):
    """Every template of every planner on a synthetic song + generated clips: the plans must pass check_plan and use
    only names the engine knows. Needs ffmpeg; no network. Returns (ok, report)."""
    import tempfile
    work = workdir or tempfile.mkdtemp(prefix='planners_selftest_')
    os.makedirs(work, exist_ok=True)
    song = os.path.join(work, 'song.wav')
    if not os.path.exists(song):
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'sine=frequency=220:duration=100', song], check=True)
    shots = []
    tags = [['dance', 'performance'], ['closeup', 'aura'], ['action', 'fight'], ['cute', 'closeup'], ['scenery'], ['dance'],
            ['closeup'], ['aura', 'performance'], ['action'], ['dance', 'cute']]
    for k in range(10):
        pth = os.path.join(work, f'clip_{k}.mp4')
        dur = [5, 7, 4, 6, 9, 12, 5, 8, 6, 11][k]
        if not os.path.exists(pth):
            subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f'testsrc2=size=320x180:rate=30:duration={dur}',
                            '-pix_fmt', 'yuv420p', pth], check=True)
        shots.append(dict(path=pth, duration=float(dur), width=320, height=180, fps=30, tags=tags[k], motion=0.1 * k,
                          aesthetic=1 - 0.05 * k, has_speech=False, has_text=False, peak=dur / 3, hits=[round(0.45 * i, 2) for i in range(1, int(dur / 0.45))],
                          character_match=0.8, source_id=f'src{k % 5}', source_title=f'test {k}', source_channel=f'chan{k % 3}',
                          framing=['closeup', 'medium', 'wide'][k % 3], periodic=0.3))
    A = synthetic_analysis()
    A['path'] = song
    try:
        import transitions as TRN
        known = TRN.known
    except Exception:
        known = None
    cases = [('song', t) for t in ('flow_blur', 'hype_typography', 'cinematic_slowmo', 'beat_collection_band', 'fx_heavy')]
    cases += [('dance', t) for t in ('short_cute_parade', 'short_smooth_velocity', 'short_raw')]
    cases += [('character', t) for t in ('quick_velocity', 'soft_romance', 'raw_crisp')]
    cases += [('kpop', t) for t in ('postcard_member_edit', 'stage_mv_member_edit', 'single_clip_trend')]
    cases += [('long', f) for f in ('song', 'dance', 'character', 'kpop')]
    report, ok = [], True
    for kind, name in cases:
        for seed in (1, 2):
            opts = dict(seed=seed, title='test edit', subtitle='Test Show', song='test song', watermark='@zeroyenotaku',
                        character='Test', framing='attractive', type=kind)
            if kind == 'long':
                opts['flavor'] = name
            else:
                opts['template'] = name
            try:
                plan = build_plan(kind, A, shots, opts)
                errs, warns = check_plan(plan)
                bad = [s['transition_in'] for s in plan['shots'] if known and s['transition_in'] != 'none' and not known(s['transition_in'])]
                if bad:
                    errs.append(f'unknown transitions {sorted(set(bad))}')
                if kind != 'long' and plan['template'] != name and not (kind == 'character' and name == 'soft_romance'):
                    warns.append(f'template {plan["template"]} instead of {name}')
                ok &= not errs
                report.append(dict(case=f'{kind}:{name}:{seed}', ok=not errs, errors=errs[:3], warnings=len(warns),
                                   shots=len(plan['shots']), duration=plan['duration'], template=plan['template']))
            except Exception as e:
                ok = False
                report.append(dict(case=f'{kind}:{name}:{seed}', ok=False, errors=[f'{type(e).__name__}: {e}'[:200]]))
    return ok, report


def load_shots(path):
    with open(path, encoding='utf-8') as f:
        d = json.load(f)
    return d.get('shots') if isinstance(d, dict) else d


def main():
    if '--selftest' in sys.argv:
        ok, report = selftest()
        for r in report:
            if not r['ok']:
                log(f"FAIL {r['case']}: {r['errors']}")
        print(json.dumps(dict(ok=ok, cases=len(report), failed=[r['case'] for r in report if not r['ok']])))
        sys.exit(0 if ok else 1)
    ap = argparse.ArgumentParser(description='Plan an edit (plan.json for core/edit/engine.py).')
    ap.add_argument('--type', required=True, choices=list(PLANNERS))
    ap.add_argument('--audio', required=True)
    ap.add_argument('--analysis', help='music.json from core/edit/music.py (computed when missing)')
    ap.add_argument('--heatmap')
    ap.add_argument('--shots', required=True, help='shots.json from core/edit/library.py')
    ap.add_argument('--out', required=True)
    ap.add_argument('--flavor', choices=['song', 'dance', 'character', 'kpop'], help='long edits: whose vocabulary')
    ap.add_argument('--template')
    ap.add_argument('--sfx', default=None, help='sound design: auto (the preset decides, default with --preset) | none | '
                                                'soft | full (core/edit/sfx.py)')
    ap.add_argument('--preset', help='a named pipeline in core/edit/presets.py, or "auto" to let it choose one '
                                     'from the song analysis and the clip pool (--template wins if both are given)')
    ap.add_argument('--words', help='JSON list of lyric words [{"text","t0"}] or [text, ...] (fx templates)')
    ap.add_argument('--vocal-onsets', help='JSON list of vocal-stem onset times, seconds in the song')
    ap.add_argument('--lyric-text', default='auto', choices=['auto', 'none', 'force'],
                    help='sung-word text track per preset (needs --song; core/edit/lyric_text.py)')
    ap.add_argument('--song-audio', help='the original song, when --audio is an edit of it (lyric timing)')
    ap.add_argument('--length', type=float)
    ap.add_argument('--fps', type=int)
    ap.add_argument('--aspect', choices=['9:16', '16:9'], help='song edits: output aspect (default 9:16; 16:9 = the '
                    'references\' own size for the remake comparisons)')
    ap.add_argument('--title')
    ap.add_argument('--subtitle')
    ap.add_argument('--song')
    ap.add_argument('--artist')
    ap.add_argument('--watermark')
    ap.add_argument('--character')
    ap.add_argument('--group')
    ap.add_argument('--framing', choices=['aura', 'attractive'], default='aura')
    ap.add_argument('--caption')
    ap.add_argument('--char-min', type=float)
    ap.add_argument('--seed', type=int)
    ap.add_argument('--flow-variant', choices=['zkw', 's2rz'], default=None,
                    help='blur_flow only: which reference flavour (default: the ZkWQ rules; s2rz = words-on-dark intro, '
                         'S2rz camera paths, lyric words, same-tempo song)')
    args = ap.parse_args()
    try:
        if args.preset == 'blur_flow' and '@v' not in os.path.basename(args.audio) and args.flow_variant != 's2rz':
            # Blur Flow plays the SLOWED + reverb + bass version of the song (ZkWQ 0.909 / cj12 0.925, general-av.md 4.1):
            # made before the analysis so the beats are the version's (remake round 1); the plan's music is the version
            try:
                import song_fx as SONGFX  # noqa: E402
                base = os.path.splitext(os.path.basename(args.audio))[0]
                vp = os.path.join(os.path.dirname(os.path.abspath(args.out)), base + '@v0.909.m4a')
                if not os.path.exists(vp):
                    SONGFX.make_version(args.audio, vp, rate=0.909, reverb=True, bass_db=4.0)
                args.audio = vp
                args.analysis = vp + '.music.json'
                log(f'blur_flow: slowed + reverb + bass version -> {vp}')
            except Exception as e:  # noqa: BLE001
                log(f'blur_flow version skipped: {e}')
        if args.preset == 'cinematic_slowmo' and '@v' not in os.path.basename(args.audio):
            # Cinematic Slow-Mo plays the song SLOWED to 0.95 (pitch follows, no reverb / boost: _sX2 locks at 0.950,
            # general-av.md 2.1); made before the analysis so the beats and the lyric times are the version's
            try:
                import song_fx as SONGFX  # noqa: E402
                base = os.path.splitext(os.path.basename(args.audio))[0]
                vp = os.path.join(os.path.dirname(os.path.abspath(args.out)), base + '@v0.95.m4a')
                if not os.path.exists(vp):
                    SONGFX.make_version(args.audio, vp, rate=0.95)
                args.audio = vp
                args.analysis = vp + '.music.json'
                log(f'cinematic_slowmo: slowed 0.95 version -> {vp}')
            except Exception as e:  # noqa: BLE001
                log(f'cinematic_slowmo version skipped: {e}')
        if args.analysis and os.path.exists(args.analysis):
            A = MU.load(args.analysis)
        else:
            A = MU.analyze(args.audio, args.heatmap)
            if args.analysis:
                with open(args.analysis, 'w', encoding='utf-8') as f:
                    json.dump(A, f)
        A['path'] = os.path.abspath(args.audio)
        shots = load_shots(args.shots)
        seed = args.seed if args.seed is not None else random.randrange(1 << 30)
        opts = dict(seed=seed, template=args.template, length=args.length, fps=args.fps, aspect=args.aspect, title=args.title, subtitle=args.subtitle,
                    song=args.song, artist=args.artist, watermark=args.watermark, character=args.character, group=args.group, framing=args.framing,
                    caption=args.caption, flavor=args.flavor, heatmap=args.heatmap, flow_variant=args.flow_variant,
                    work_dir=os.path.dirname(os.path.abspath(args.out)))
        if args.char_min is not None:
            opts['char_min'] = args.char_min
        for key, raw in (('words', args.words), ('vocal_onsets', args.vocal_onsets)):
            if raw:
                opts[key] = json.loads(raw) if raw.lstrip()[:1] in '[{' else json.load(open(raw, encoding='utf-8'))
        preset_id, preset_why = None, None
        if args.preset:
            try:
                import presets as PS  # noqa: E402  (optional: the planner runs without it)
                preset_id, pre = PS.resolve(args.preset, args.type, A, shots, random.Random(seed))
                if pre is None:
                    raise RuntimeError(f'unknown preset {args.preset!r} (see core/edit/presets.py --list)')
                _, preset_why = (preset_id, f"{preset_id} -> template {pre['template']}") if args.template else \
                    PS.choose_preset(args.type, A, shots, random.Random(seed)) if args.preset == 'auto' else (preset_id, preset_id)
                if not args.template:
                    opts['template'] = pre['template']
                opts['preset'] = preset_id
                log(f'preset: {preset_why}')
            except ImportError:
                log('preset requested but core/edit/presets.py is not importable; falling back to the template')
        plan = build_plan(args.type, A, shots, {k: v for k, v in opts.items() if v is not None})
        if preset_id:
            plan.setdefault('planner', {})['preset'] = preset_id
            plan['planner']['preset_why'] = preset_why
            # sound design per preset (core/edit/sfx.py PRESET_SFX; "none" for presets whose references are song-only)
            plan.setdefault('sfx', getattr(args, 'sfx', None) or 'auto')
        if args.song and args.lyric_text != 'none':
            # lyric text that follows the sung words, only for presets whose references show it (core/edit/lyric_text.py)
            try:
                import lyric_text as LT  # noqa: E402
                LT.attach(plan, dict(opts, preset=preset_id, force_lyrics=args.lyric_text == 'force'), song=args.song,
                          artist=args.artist, song_audio=args.song_audio)
            except Exception as e:  # noqa: BLE001  (the edit renders without lyric text rather than failing)
                log(f'lyric text skipped: {e}')
        errs, warns = check_plan(plan)
        for w in warns:
            log(f'plan warning: {w}')
        if errs:
            raise RuntimeError('bad plan: ' + '; '.join(errs[:6]))
        os.makedirs(os.path.dirname(os.path.abspath(args.out)) or '.', exist_ok=True)
        with open(args.out, 'w', encoding='utf-8') as f:
            json.dump(plan, f, indent=1)
        print(json.dumps(dict(ok=True, plan=os.path.abspath(args.out), type=args.type, seed=seed, preset=preset_id,
                              warnings=len(warns), **summarize(plan))))
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(json.dumps({'ok': False, 'reason': str(e)[:400]}))
        sys.exit(1)


if __name__ == '__main__':
    main()
