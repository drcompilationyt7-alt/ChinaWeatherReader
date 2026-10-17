#!/usr/bin/env python3
"""
Edit engine: renders a declarative plan (plan.json) into a finished video (Zero Yen Otaku edits).

    python core/edit/engine.py --plan plan.json --out out.mp4 [--preview] [--start s --end s]
                               [--dump DIR --times 1.0,2.5] [--cpus 2] [--preset slow --crf 16]

Plan (times in seconds on the OUTPUT timeline; t = 0 is the first frame):
  aspect        "9:16" (1080x1920) | "16:9" (1920x1080)
  fps           60 (shorts) / 30 or 60 (long)
  music         audio file or null;  music_start: where in the file t = 0 is;  duration: output length
  clip_audio    "none" | "mix" (each normal-speed shot's own sound under the music) | "only" (no music)
  sfx           designed transition sounds (core/edit/sfx.py): absent | "none" (default, the old behaviour) | "auto" (the
                preset's row in sfx.PRESET_SFX; needs plan "preset") | "soft" | "full";  sfx_gain_db: trim for all of them;
                sfx_cues: [{"sfx": name, "at": s, "gain_db": dB vs the music}] hand-placed;  per shot "sfx_design":
                "none" | a generator name.  music_fx: [{"type": "duck"|"lowpass_sweep"|"silence_gap"|"tape_stop", ...}].
                clip_audio_gain_db / per shot clip_gain_db: level of a shot's own sound in "mix" mode (dB vs the music's
                median RMS, default style audio.clip_gain_db = -8);  clip_audio_hp: high-pass in Hz for that sound (0 = off)
  audio_design  "auto" | "none" (absent: "auto" when sfx == "auto"): the preset's MEASURED song edits + the few
                overlays + its clip-audio rule (core/edit/song_fx.py, clip_audio.py, table presets.AUDIO_AV);
                song_fx: [{"op": ...}] explicit song edits (song_cut, echo, lowpass, duck, impact, ...);
                clip_events: [{"role": line|pop_line|fill|hit, "path"|"shot", "clip_t0", "clip_t1", "at"}];
                song_fx_variant: force the reference recipe (i4f, m3lv, zawB, cj12, yUui, YH, TG, eEPZ, rfsj, 91tp)
  style         preset in core/edit/styles.py (velocity_slowmo, flow_blur, hype_flash, cinematic_warm, glitch,
                beat_band, dance_sync, dance_smooth, dance_amv, character_aura, character_soft, character_raw,
                postcard_kpop, kpop_stage, kpop_trend, sound_edit); style_overrides: dict merged into it
  frame         optional frame mode (full | rot90 | band16x9 | band1x1 | letterbox_black | window2x3 |
                window3x4 | letterbox_blur); default from the style
  look          optional colour look (core/edit/styles.py LOOKS); default from the style
  title / subtitle / watermark   Latin text (emoji are stripped; use overlays "sticker" with emoji images)
  beats, downbeats   beat times;  energy: 0..1 per beat;  drop: time of the drop (optional)
  lead          "none" (default: shot t0 is the exact switch time) | "auto" (t0 values are beat times and
                the engine moves each cut by its transition's measured lead, styles.lead_s)
  seed          int (auto choices are deterministic)
  shots         list, in order:
      path        clip file (video or still image)
      t0, t1      output times of the shot (t1 defaults to the next shot's t0)
      src_in      clip time shown at t0
      speed       "normal" | "velocity" | "slowmo" | "ramp" | "synced" | "freeze"   (+ "rate": mean speed)
                  velocity kinds come from the style (u_shape, burst_land, smooth_wave, kpop_u, meme) or
                  "velocity": {"kind": ..., params}; ramp: "ramp": "impact" | "dip" | "flat"
      speed_curve [[u, clip_t], ...]: u = seconds since t0, clip_t = absolute clip seconds (overrides speed)
      camera      {"move": push_in|pull_out|drift|static|hold|establishing, "amount": ln-scale over the shot,
                   "pan": [dx, dy] (fractions of the width), "roll": deg, "zoom": base scale, "focus": [x, y]
                   (fractions of the clip), "handheld": multiplier, "bounce": amount, "follow": bool,
                   "accents": "auto" | "none" | [{"at": t, "kind": zoom_in_punch|zoom_out_punch|rotate_punch|
                   vslide_wrap|blur_pulse|flash_pulse|strobe_smear|vsmear|glow_pulse|bounce|shake|flash|echo}]}
                  3D (core/edit/camera3d.py): "move3d": {"kind": orbit|parallax|dolly_zoom|tilt|push_depth|swing,
                  "amount", "dir"}, "depth": false | strength (2.5D layers from Depth Anything V2 small)
      handover    false = no camera carry through this cut (plan "handover": false / "handover_overrides": {...};
                  per-preset profiles in core/edit/handover.py: the move runs through the cut + measured 3D turns)
      transition_in  name in core/edit/transitions.py (+ modifiers "+bleach" "+flash" "+rgb" "+leak"),
                  "auto" / missing = picked from the style (house on most cuts, hero on phrase starts)
      layout      "fill" | "fit" | "letterbox" | "fit_black" | "postcard" | "card3d" | "split2" | "split3" | "hearts"
      panels      for split / hearts: [{"path", "src_in", "rate"}]
      postcard    {"variant": "character" | "pastel", "angle", "width", "bw", "entry"}
      look        per-shot look override;  interp: flow | native | blend
      sfx         [{"clip_t": hit time in the clip, "at": output time it must land on, "gain": dB offset}]
                  (sound edits: the clip's own hit, gated and mixed sample-accurately over the music)
      audio       true / false: this shot's own sound in "mix" mode
  overlays      list of {"type", "t0", "t1", ...}:
      title_card / text   {"text", "style" (text.STYLES), "size" (fraction of H), "x", "y" (fractions),
                           "anim": pop | fade | stretch | type | slam | letters_3d | slide | none, "space": canvas | frame,
                           "behind": true = drawn behind the subject (rough per-frame matte)}
      lyrics      {"words": [{"text", "t0", "t1"}], "style": "lyric"}  (word per beat)
      lyric_text  {"preset", "style", "items": [{"text", "t0" first ink, "t_out", "t1", "x", "y", "box", "anim_in", ...}]}
                  the sung-word track of a preset (core/edit/lyric_text.py builds and draws it)
      caption_bar {"text"}      center_label {"lines": [...], "red": bool}     kinetic_name {"text"}
      particles   {"kind": sparkles|hearts|bokeh|embers|confetti|gold_dust|petals|stars, "n", "opacity"}
      light_leak, lens_flare {"x", "y"}, light_rays {"x", "y"}, light_streak, ring, glow_pulse, echo
      letterbox   {"frac"}      fade {"color": black | white, "dir": in | out}      flat (pre-drop flat look)
      bw_selective, desaturate {"amount"}, colour_cycle, beat_light_pulse
      fx group (core/edit/refs/fx.json):
      word3d      {"words": [{"text", "t0", "x", "y"}], "style": "hero_fx", "cap0": 0.108, "growth": 1.11,
                   "exit_cap": 0.55, "max_alive": 3, "vertical": bool}  per-word 3D fly-through, back to front,
                   motion blurred, never fading (the core of the typography style)
      ground_text {"text", "tilt": 74, "grow", "travel", "plate_blur"}  the word lying flat on the plate's ground
      radial_blur {"amount": 0.027, "center": [x, y]}   sustained in-shot radial smear on a pushing shot
      glass_cracks {"x", "y", "progress", "sparks"}     filmstrip {"angle": 28, "speed", "thumbs"}
      ink_blot    {"side", "coverage", "text"}          polaroids {"n": 3, "stagger", "size"}
      bokeh       {"n": 57}    zoom_blur_hit {"at", "pre", "amount"}  (whole-frame impact before a title lands)
      anim "letters_fly" on text / title_card: letters arrive from off-screen right, smeared for 2 frames
      built set pieces (core/edit/fxsets.py, each traced to frames of WGvtliE1298):
      music_card3d {"title", "artist", "text", "cps": 13.2, "dur", "cards"}   3D now-playing card over black + bokeh
      glass_set   {"cards", "thumbs", "speed", "ry", "angle", "opacity"}   near-black set, perspective filmstrip, crack web
      white_set   {"n"}  the luma-217 white studio;   letters_fly {"text" ("A / B" = two lines), "cap": 0.25,
                   "gradient": [bgr0, bgr1], "from_x": 1.35, "stagger"}     closing_3d {"text": "I WAS / IN IT"}
      fade_out    {"frames": 10}  gain 1 - i/9, linear in luma      watermark_fx {"text"}  bottom-left, cap 2.4 %H, 0.55
      word3d also takes "cap_hold", "cap1", "hold_f", "entry_f", "exit_f", "vp": [x, y] and per word "hold", "park"
      text anim "type" takes "char_fade" (s): fx.json types at 13.2 chars/s with a 3-frame per-character fade
      sticker     {"kind": hearts | sparkles | lightning | heart_frame | emoji, "emoji", "x", "y", "size"}
      mute        (audio silence, e.g. the freeze-silence before a drop)
      scenes (replace the picture): postcard_intro {"cards": [{"path", "clip_t"}], "caption"},
                  text_on_black {"words" or "text", "style": neon | lower | wide_thin}, music_card {"title",
                  "artist", "line", "channel", "cards"}

Output: an H.264 / AAC mp4 (BT.709), audio at -14 LUFS with true peak < -1 dBTP; the JSON summary
is printed ({"ok": true, "path", "render_s", "fps_render", "transitions", ...}).
"""
import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from queue import Queue

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'meme'))
sys.path.insert(0, HERE)
import edit_fx as fx  # noqa: E402
import acapella as audio_io  # noqa: E402
import styles as S  # noqa: E402
import transitions as TR  # noqa: E402
import layouts as L  # noqa: E402
import text as T  # noqa: E402
import footage as FT  # noqa: E402
import fxsets as FXS  # noqa: E402
import lyric_text as LT  # noqa: E402
import camera3d as C3  # noqa: E402
import handover as HO  # noqa: E402
import gpu as _gpu  # noqa: E402

ASPECTS = {'9:16': (1080, 1920), '16:9': (1920, 1080), '1:1': (1080, 1080), '4:5': (1080, 1350)}
PRE_MARGIN, POST_MARGIN = 1.6, 0.8        # clip time is mapped this far around each shot (transitions, previews)
SCENE_TYPES = set(L.SCENES)


def log(*a):
    print('[engine]', *a, file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------------------------- plan

def load_plan(path_or_dict):
    plan = path_or_dict if isinstance(path_or_dict, dict) else json.load(open(path_or_dict, encoding='utf-8'))
    plan = dict(plan)
    shots = [dict(s) for s in plan.get('shots') or []]
    if not shots:
        raise SystemExit('plan has no shots')
    for s in shots:
        if not s.get('path') or not os.path.exists(s['path']):
            raise SystemExit(f"shot clip missing: {s.get('path')}")
        s['t0'] = float(s.get('t0', 0.0))
    shots.sort(key=lambda s: s['t0'])
    for i, s in enumerate(shots):
        if s.get('t1') is None:
            s['t1'] = shots[i + 1]['t0'] if i + 1 < len(shots) else float(plan.get('duration') or s['t0'] + 2.0)
        s['t1'] = float(s['t1'])
        s['src_in'] = float(s.get('src_in') or 0.0)
    plan['shots'] = shots
    if not plan.get('duration'):
        plan['duration'] = max(s['t1'] for s in shots)
    plan['duration'] = float(plan['duration'])
    plan.setdefault('aspect', '9:16')
    plan.setdefault('fps', 60)
    plan.setdefault('clip_audio', 'none')
    plan.setdefault('beats', [])
    plan.setdefault('downbeats', [])
    plan.setdefault('energy', [])
    plan.setdefault('overlays', [])
    return plan


# ---------------------------------------------------------------------------------------------- time maps

NO_SHAKE = lambda: os.environ.get('EDIT_NO_SHAKE', '0') == '1'      # owner 2026-10-13: sound / slow-mo vibe edits: no shake, wiggle, handheld, bounce


class TimeMap:
    """Output time -> clip time for one shot (dense table, linear interpolation, clamped to the clip)."""

    def __init__(self, ts, cs, vs, dur, fps):
        self.ts, self.cs, self.vs = ts, cs, vs
        self.dur, self.fps = dur, fps

    def __call__(self, t):
        if t <= self.ts[0]:
            c = self.cs[0] + (t - self.ts[0]) * self.vs[0]
        elif t >= self.ts[-1]:
            c = self.cs[-1] + (t - self.ts[-1]) * self.vs[-1]
        else:
            c = float(np.interp(t, self.ts, self.cs))
        return min(max(0.0, c), max(0.0, self.dur - 0.5 / self.fps))

    def speed(self, t):
        return float(np.interp(t, self.ts, self.vs))

    def span(self, a, b):
        """Clip-time range needed for output times [a, b]."""
        xs = np.linspace(a, b, 24)
        cs = [self(x) for x in xs]
        return min(cs), max(cs)


def _vel_u(x, lo, hi_in, hi_out, ti, to):
    return lo + (hi_in - lo) * np.exp(-x / ti) + (hi_out - lo) * np.exp(-(1 - x) / to)


def speed_profile(eng, shot, ts):
    """Speed (clip seconds per output second) at output times ts for a shot."""
    st = eng.style['speed']
    mode = shot.get('speed') or st.get('default', 'normal')
    t0, t1 = shot['t0'], shot['t1']
    span = max(1e-3, t1 - t0)
    x = np.clip((ts - t0) / span, 0, 1)
    vel = dict(st.get('velocity') or {})
    if isinstance(shot.get('velocity'), dict):
        vel.update(shot['velocity'])
    if mode == 'freeze':
        return np.zeros_like(ts)
    if mode in ('normal', 'synced'):
        return np.full_like(ts, float(shot.get('rate') or 1.0))
    if mode == 'slowmo':
        return np.full_like(ts, float(shot.get('rate') or st.get('slowmo_rate', 0.5)))
    if mode == 'ramp':
        base = float(shot.get('rate') or st.get('rate', 0.65))
        kind = shot.get('ramp') or st.get('ramp', 'impact')
        if kind == 'impact':
            # 1.8x right after the cut, easing out to the base speed by a quarter of the shot
            return base + (1.8 - base) * (1 - np.clip(x / 0.25, 0, 1)) ** 3
        if kind == 'dip':
            v = np.full_like(ts, base)
            a = x < 0.2
            v[a] = 1.6 + (base - 1.6) * (x[a] / 0.2)
            b = x > 0.85
            v[b] = base + (1.4 - base) * ((x[b] - 0.85) / 0.15)
            return v
        if kind == 'hero_hold':
            return np.full_like(ts, float(shot.get('rate') or 0.45))
        return np.full_like(ts, base)
    # velocity
    kind = vel.get('kind', 'u_shape')
    rate = float(shot.get('rate') or st.get('rate', 1.0))
    if kind in ('u_shape', 'kpop_u'):
        if kind == 'u_shape':
            nxt = eng.next_transition_name(shot)
            hi_out = vel.get('hi_out', 1.0)
            if nxt and nxt.split('+')[0] in ('zoom_through', 'whip_slide', 'radial_zoom_burst', 'whip_horizontal', 'flash_zoom'):
                hi_out = max(hi_out, 1.6)
            f = lambda xx: _vel_u(xx, vel.get('lo', 0.55), vel.get('hi_in', 1.6), hi_out, vel.get('tau_in', 0.12), vel.get('tau_out', 0.1))
        else:
            lo, hi, tau = vel.get('lo', 0.45), vel.get('hi', 1.2), vel.get('tau', 0.18)
            f = lambda xx: lo + (hi - lo) * (np.exp(-xx / tau) + np.exp(-(1 - xx) / tau))
        grid = np.linspace(0, 1, 401)
        m = float(f(grid).mean())
        return f(x) * (rate / max(1e-6, m))
    # per-beat curves
    phi = eng.beat_phase(ts)
    adv = float(vel.get('advance', 1.1)) * float(shot.get('rate') or 1.0)
    grid = np.linspace(0, 1, 401, endpoint=False)
    if kind == 'burst_land':
        lo, hi, pk, sg = vel.get('lo', 0.5), vel.get('hi', 3.0), vel.get('peak', 0.85), vel.get('sigma', 0.12)
        f = lambda p: lo + (hi - lo) * np.exp(-0.5 * ((p - pk) / sg) ** 2)
    elif kind == 'smooth_wave':
        mean, amp, c = vel.get('mean', 1.2), vel.get('amp', 0.5), vel.get('center', -0.1)
        f = lambda p: mean + amp * np.cos(2 * np.pi * (p - c))
    else:   # meme: fast into the beat, slow after
        lo, hi, tau = 0.45, 2.2, 0.14
        f = lambda p: lo + (hi - lo) * (np.exp(-p / tau) + np.exp(-(1 - p) / tau))
    m = float(f(grid).mean())
    return f(phi) * (adv / max(1e-6, m))


# ---------------------------------------------------------------------------------------------- the engine

class Engine:
    def __init__(self, plan, workdir, preview=False, fps=None, seed=None):
        self.plan = plan
        self.work = workdir
        self.warnings = []
        self.style = S.resolve(plan.get('style', 'flow_blur'), plan.get('style_overrides'))
        self.family = self.style.get('family', 'general')
        self.aspect = plan.get('aspect', '9:16')
        W, H = ASPECTS.get(self.aspect, (1080, 1920))
        self.scale = 0.5 if preview else 1.0
        self.out_size = (int(W * self.scale) // 2 * 2, int(H * self.scale) // 2 * 2)
        self.fps = float(fps or plan.get('fps') or 60)
        self.duration = float(plan['duration'])
        self.frame_mode = plan.get('frame') or self.style['frame'].get(self.aspect, 'full')
        self.fr = L.Frame(self.frame_mode, self.out_size)
        self.canvas = self.fr.canvas
        self.look_name = plan.get('look') or self.style['look']
        self.look = S.LOOKS.get(self.look_name, S.LOOKS['source'])
        self.interp = plan.get('interp') or self.style.get('interp', 'flow')
        self.seed = int(plan.get('seed') if plan.get('seed') is not None else (seed or 7))
        self.rng = np.random.default_rng(self.seed)
        self.beats = sorted(float(b) for b in plan.get('beats') or [])
        self.downbeats = sorted(float(b) for b in plan.get('downbeats') or [])
        self.energy = [float(e) for e in plan.get('energy') or []]
        self.music = plan.get('music')
        self.music_start = float(plan.get('music_start') or 0.0)
        self._music = None
        self.shots = plan['shots']
        for i, s in enumerate(self.shots):
            s['i'] = i
            s['seed'] = self.seed * 1000 + i * 7919
            s['layout'] = s.get('layout') or self.style.get('layout', 'fill')
        auto = self._postcard_shots_to_scene()
        if auto and not any(o.get('type') == 'postcard_intro' for o in plan.get('overlays') or []):
            plan.setdefault('overlays', []).append(auto)
        self.P = self._beat_len()
        self.phrases = plan.get('phrases') or (self.downbeats[::4] if self.downbeats else [])
        self.drop_t = self._drop_time()
        self._prep()
        self._apply_leads()
        self._init_footage()
        self._init_cameras()
        self._init_transitions()
        self._init_timemaps()
        self._init_accents()
        self._init_overlays()
        self._memo = {}
        self._memo_t = None
        self._maps = {}
        self.stats = {'frames': 0}

    # ------------------------------------------------------------------ timing helpers
    def _beat_len(self):
        if len(self.beats) >= 2:
            return float(np.median(np.diff(self.beats)))
        return 0.5

    def beat_phase(self, ts):
        ts = np.asarray(ts, dtype=np.float64)
        if len(self.beats) < 2:
            return (ts / self.P) % 1.0
        b = np.array(self.beats)
        k = np.searchsorted(b, ts, side='right') - 1
        out = np.empty_like(ts)
        lo = k < 0
        hi = k >= len(b) - 1
        mid = ~(lo | hi)
        out[mid] = (ts[mid] - b[k[mid]]) / np.maximum(1e-6, b[k[mid] + 1] - b[k[mid]])
        out[lo] = ((ts[lo] - b[0]) / self.P) % 1.0
        out[hi] = ((ts[hi] - b[-1]) / self.P) % 1.0
        return out

    def energy_at(self, t):
        if not self.energy or not self.beats:
            return 1.0
        k = int(np.searchsorted(self.beats, t, side='right')) - 1
        return float(self.energy[max(0, min(len(self.energy) - 1, k))])

    def _drop_time(self):
        if self.plan.get('drop') is not None:
            return float(self.plan['drop'])
        for ov in self.plan.get('overlays') or []:
            if ov.get('type') in ('postcard_intro', 'text_on_black', 'music_card'):
                return float(ov.get('t1', 0))
        return float(self.shots[1]['t0']) if len(self.shots) > 1 and self.shots[0]['t1'] - self.shots[0]['t0'] > 1.5 else 0.0

    def next_transition_name(self, shot):
        i = shot['i']
        if i + 1 < len(self.shots):
            return self.shots[i + 1].get('transition_in') or ''
        return ''

    # ------------------------------------------------------------------ preparation
    def _prep(self):
        t = time.time()
        follow = bool(self.style['camera'].get('follow'))
        paths = set()
        for s in self.shots:
            paths.add(s['path'])
            for p in s.get('panels') or []:
                if p.get('path'):
                    paths.add(p['path'])
        self.preps = {}
        for p in sorted(paths):
            want = follow or any(s.get('camera', {}) and (s.get('camera') or {}).get('follow') for s in self.shots if s['path'] == p)
            self.preps[p] = FT.analyze(p, want_track=want)
        mids = [pr.palette for pr in self.preps.values() if pr.palette is not None]
        self.colors = fx.palette(mids[:12]) if mids else [(255, 80, 200), (255, 180, 60)]
        self.timing = {'analyze_s': round(time.time() - t, 2)}

    def _apply_leads(self):
        if self.plan.get('lead') != 'auto':
            return
        for i, s in enumerate(self.shots):
            if i == 0:
                continue
            tr = s.get('transition_in') or self.style['transitions'].get('house', 'hard')
            s['t0'] = S.cut_time(s['t0'], tr, self.style, self.fps)
        for i in range(len(self.shots) - 1):
            self.shots[i]['t1'] = self.shots[i + 1]['t0']

    def _crop(self, prep, shot):
        x0, y0, x1, y1 = shot.get('content_box') or [0, 0, 1, 1]
        top, bottom = prep.bands
        if 0 < bottom <= 0.3:
            y1 = min(y1, 1 - bottom)
        if 0 < top <= 0.3:
            y0 = max(y0, top)
        X0, Y0 = int(round(x0 * prep.w / 2) * 2), int(round(y0 * prep.h / 2) * 2)
        X1, Y1 = int(round(x1 * prep.w / 2) * 2), int(round(y1 * prep.h / 2) * 2)
        return (X0, Y0, max(16, X1 - X0), max(16, Y1 - Y0))

    def _target_sizes(self, shot):
        """Picture sizes this shot's clip is drawn at (to size the decode)."""
        cw, ch = self.canvas
        lay = shot['layout']
        if lay == 'postcard':
            return [(int(cw * 0.62), int(cw * 0.75))]
        if lay in ('split2', 'split3'):
            n = 2 if lay == 'split2' else 3
            return [(cw // n, ch) if cw >= ch else (cw, ch // n)]
        if lay == 'hearts':
            return [(int(cw * 0.77), int(cw * 0.77))]
        return [(cw, ch)]

    def _init_footage(self):
        self.footage = {}
        needs = {}
        for s in self.shots:
            srcs = [(s['path'], s.get('look') or self.look_name, s)]
            for p in s.get('panels') or []:
                if p.get('path'):
                    srcs.append((p['path'], s.get('look') or self.look_name, s))
            for path, look, sh in srcs:
                prep = self.preps[path]
                crop = self._crop(prep, sh if path == sh['path'] else {})
                sizes = self._target_sizes(sh)
                need = 0.0
                for tw, th in sizes:
                    if sh['layout'] in ('fit', 'letterbox', 'fit_black'):
                        need = max(need, tw / crop[2] * (1.22 if sh['layout'] == 'letterbox' else 1.0))
                    else:
                        need = max(need, max(tw / crop[2], th / crop[3]))
                key = (path, look)
                a = needs.get(key)
                needs[key] = (crop, max(need, a[1] if a else 0.0))
        for (path, look), (crop, need) in needs.items():
            prep = self.preps[path]
            k = min(1.0, need * 1.2)
            ww = max(16, int(round(crop[2] * k / 2) * 2))
            wh = max(16, int(round(crop[3] * k / 2) * 2))
            lk = S.adapt_look(S.LOOKS.get(look, self.look), prep)
            gamma, bloom_k = prep.exposure(lk.get('exposure') or 0.42) if lk.get('exposure') else (1.0, 1.0)
            vf = S.grade_graph(lk, ww, gamma, bloom_k, (prep.p02, prep.p98) if lk.get('levels') else None)
            self.footage[(path, look)] = FT.Footage(path, vf, crop, (ww, wh), prep)

    def foot(self, shot, panel=None):
        look = shot.get('look') or self.look_name
        path = panel['path'] if panel else shot['path']
        return self.footage[(path, look)]

    def _init_timemaps(self):
        end = self.style.get('end') or {}
        HO.pick_sources(self)                   # source moments with subject motion (handover.pick_sources)
        for s in self.shots:
            prep = self.preps[s['path']]
            a, b = s['t0'] - PRE_MARGIN, s['t1'] + POST_MARGIN
            ts = np.arange(a, b + 1e-9, 1 / 480.0)
            if s.get('speed_curve'):
                cv = np.array(s['speed_curve'], dtype=np.float64)
                us, cs_ = cv[:, 0] + s['t0'], cv[:, 1]
                cs = np.interp(ts, us, cs_)
                v0 = (cs_[1] - cs_[0]) / max(1e-6, us[1] - us[0]) if len(cv) > 1 else 1.0
                v1 = (cs_[-1] - cs_[-2]) / max(1e-6, us[-1] - us[-2]) if len(cv) > 1 else 1.0
                cs = np.where(ts < us[0], cs_[0] + (ts - us[0]) * v0, cs)
                cs = np.where(ts > us[-1], cs_[-1] + (ts - us[-1]) * v1, cs)
                vs = np.gradient(cs, ts)
            else:
                vs = speed_profile(self, s, ts)
                if s is self.shots[-1] and end.get('kind') in ('freeze', 'slowmo'):
                    if end['kind'] == 'freeze':
                        vs[ts > self.duration - float(end.get('dur') or 1.8)] = 0.0
                    else:
                        vs[ts > self.duration - 4 * self.P] *= 0.5
                cs = np.concatenate([[0.0], np.cumsum((vs[1:] + vs[:-1]) / 2 * np.diff(ts))])
                i0 = int(np.searchsorted(ts, s['t0']))
                cs = cs - cs[min(i0, len(cs) - 1)] + s['src_in']
            dur = prep.duration if not prep.path.lower().endswith(FT.IMAGE_EXT) else 1e6
            avail = dur - 1.0 / max(1.0, prep.fps)
            i1 = int(np.searchsorted(ts, s['t1']))
            c_end = cs[min(i1, len(cs) - 1)]
            if c_end > avail and s.get('speed') not in ('freeze',):
                need = c_end - s['src_in']
                if s['src_in'] + need > avail:
                    shift = min(s['src_in'], c_end - avail)
                    cs = cs - shift
                    s['src_in'] -= shift
                    c_end -= shift
                if c_end > avail:
                    k = max(0.15, (avail - s['src_in']) / max(1e-6, c_end - s['src_in']))
                    cs = s['src_in'] + (cs - s['src_in']) * k
                    vs = vs * k
                    self.warnings.append(f"shot {s['i']}: clip too short, slowed x{k:.2f}")
            s['_tm'] = TimeMap(ts, cs, vs, dur, prep.fps)
            for p in s.get('panels') or []:
                pp = self.preps[p['path']]
                rate = float(p.get('rate') or 1.0)
                cs2 = float(p.get('src_in') or 0.0) + (ts - s['t0']) * rate
                p['_tm'] = TimeMap(ts, cs2, np.full_like(ts, rate), pp.duration, pp.fps)

    def _init_cameras(self):
        cam_st = self.style['camera']
        moves = cam_st.get('moves') or {'static': 1.0}
        names, ws = list(moves), np.array(list(moves.values()), dtype=float)
        prev_dir = 1
        for s in self.shots:
            rng = np.random.default_rng(s['seed'])
            c = dict(s.get('camera') or {})
            move = c.get('move')
            if not move:
                move = names[int(rng.choice(len(names), p=ws / ws.sum()))]
                if move in ('push_in', 'pull_out') and cam_st.get('alternate') and rng.random() < cam_st['alternate']:
                    move = 'pull_out' if prev_dir > 0 else 'push_in'
            prev_dir = 1 if move == 'push_in' else -1
            lo, hi = cam_st.get('move_ln', (0.04, 0.1))
            c['move'] = move
            c.setdefault('amount', float(rng.uniform(lo, hi)))
            if move == 'drift' and 'pan' not in c:
                dl, dh = cam_st.get('drift_frac', (0.03, 0.07))
                c['pan'] = [float(rng.uniform(dl, dh)) * (1 if rng.random() < 0.5 else -1), 0.0]
            c.setdefault('pan', [0.0, 0.0])
            if 'roll' not in c:
                c['roll'] = float(rng.uniform(1.0, 3.0)) * (1 if rng.random() < 0.5 else -1) if rng.random() < cam_st.get('entry_roll', 0.0) else 0.0
            c.setdefault('zoom', 1.0)
            c.setdefault('handheld', 1.0)
            if c['handheld'] is None:           # a hand-written "handheld": null crashed float(); planners.engine_camera maps it to 0
                c['handheld'] = 0.0
            c.setdefault('bounce', cam_st.get('bounce', 0.0))
            c.setdefault('follow', bool(cam_st.get('follow')))
            c['_hh_seed'] = float(rng.uniform(0, 100))
            if move == 'hold':
                c['amount'] = float(rng.uniform(-0.03, 0.03)) * max(0.3, s['t1'] - s['t0'])
            c['pre_pull'] = None        # resolved in _init_transitions, once the cuts are named
            s['_cam'] = c

    def base_cam(self, s, t):
        c = s['_cam']
        cam_st = self.style['camera']
        span = max(0.2, s['t1'] - s['t0'])
        u = min(1.0, max(0.0, (t - s['t0']) / span))
        e = S.ease(u, cam_st.get('ease', 'io'))
        lns = math.log(max(0.05, float(c.get('zoom', 1.0))))
        mv, amt = c['move'], float(c['amount'])
        kdx = kdy = krot = kyaw = kpitch = 0.0
        keys = c.get('keys')
        if keys:
            # a copied per-frame camera path (remakes / planners): [[u_s, ln_scale, dx, dy, roll_deg], ...] with u =
            # seconds since t0, dx / dy in canvas widths, linear between keys, held past the last key
            kt = c.get('_keys_arr')
            if kt is None:
                ncol = len(keys[0]) if keys and len(keys[0]) in (5, 7) else 5
                kt = np.asarray(keys, dtype=float).reshape(-1, ncol)
                c['_keys_arr'] = kt
            uu = max(0.0, t - s['t0'])
            lns += float(np.interp(uu, kt[:, 0], kt[:, 1]))
            kdx, kdy, krot = (float(np.interp(uu, kt[:, 0], kt[:, k])) for k in (2, 3, 4))
            if kt.shape[1] >= 7:
                # optional 3D columns [.., yaw_deg, pitch_deg] (remakes: the reference's plane swings)
                kyaw, kpitch = (float(np.interp(uu, kt[:, 0], kt[:, k])) for k in (5, 6))
        if mv == 'push_in':
            lns += amt * e
        elif mv == 'pull_out':
            lns += amt * (1 - e)
        elif mv == 'hold':
            lns += amt * u + abs(amt)
        elif mv == 'establishing':
            tt = max(0.0, t - s['t0'])
            lns += 0.098 * tt + 0.094 * tt * tt
        dx = float(c['pan'][0]) * (e - 0.5) + kdx
        dy = float(c['pan'][1]) * (e - 0.5) + kdy
        rot = float(c['roll']) * (e - 0.5) + krot
        hh = float(cam_st.get('handheld', 0.0)) * float(c.get('handheld', 1.0))
        if NO_SHAKE():
            hh = 0.0
        if hh > 0:
            sd = c['_hh_seed']
            f = cam_st.get('handheld_hz', 3.0)
            dx += hh * 1.4 * fx.smooth_noise(t, sd, f)
            dy += hh * 1.0 * fx.smooth_noise(t, sd + 17.3, f * 0.9)
            rot += cam_st.get('handheld_roll', 0.18) * fx.smooth_noise(t, sd + 5.1, 2.5) * (hh / 0.003)
            lns += cam_st.get('handheld_zoom', 0.01) * fx.smooth_noise(t, sd + 9.7, 1.5) * (hh / 0.003)
        pp = c.get('pre_pull')
        if pp:
            amt, dur = pp
            left = s['t1'] - t
            # round 6: shot_cam clamps a net ln s < 0 to 0 (no plate edge may show), so on a 'hold' camera at zoom 1.0
            # the pull-back was clamped away on EVERY kpop shot (measured 0.0 %/frame before the cut against the
            # reference's -0.4..-0.8 %/frame). The shot now carries the pull amount as headroom for its whole
            # length and gives it back over the last `dur` seconds.
            lns += amt
            if 0 <= left < dur:
                lns -= amt * (1 - left / dur) ** 2
        b = 0.0 if NO_SHAKE() else float(c.get('bounce') or 0.0)
        if b > 0 and self.beats:
            k = int(np.searchsorted(self.beats, t, side='right')) - 1
            if k >= 0:
                lns += math.log(1 + fx.punch(t - self.beats[k], b, tau=0.12))
        if kyaw or kpitch:
            b9 = list(C3.ext((lns, rot, dx, dy)))
            b9[C3.YAW] += kyaw
            b9[C3.PITCH] += kpitch
            return C3.shot_move(c, tuple(b9), u, e, t)
        return C3.shot_move(c, (lns, rot, dx, dy), u, e, t)

    # ------------------------------------------------------------------ transitions
    def _pick(self, table, rng):
        names = [k for k, v in table.items() if v > 0 and TR.known(k)]
        if not names:
            return 'hard'
        w = np.array([table[k] for k in names], dtype=float)
        return names[int(rng.choice(len(names), p=w / w.sum()))]

    def _init_transitions(self):
        trs = self.style['transitions']
        self.trans = []
        last, last_hero = None, -99.0
        for i, s in enumerate(self.shots):
            rng = np.random.default_rng(s['seed'] + 1)
            name = s.get('transition_in')
            if i == 0:
                if not name or name == 'auto':
                    name = trs.get('first', 'hard')
            elif not name or name == 'auto':
                phrase = any(abs(s['t0'] - p) < 0.2 for p in self.phrases) or abs(s['t0'] - self.drop_t) < 0.2
                hero = trs.get('hero') or {}
                bars4 = 16 * self.P
                if phrase and hero and s['t0'] - last_hero >= bars4 * 0.9:
                    name = self._pick(hero, rng)
                    last_hero = s['t0']
                    if trs.get('bleach_on_hero') and rng.random() < trs['bleach_on_hero']:
                        name += '+bleach'
                else:
                    house_share = trs.get('house_share')
                    if house_share and rng.random() < house_share:
                        name = trs.get('house', 'hard')
                    else:
                        name = self._pick(trs.get('p') or {'hard': 1.0}, rng)
                    if name == last and name not in (trs.get('house'),) and rng.random() < 0.6:
                        name = self._pick(trs.get('p') or {'hard': 1.0}, rng)
            s['transition_in'] = name
            last = name
            self.trans.append(None)
        # contexts need the transitions' neighbours, so build them after naming
        for i, s in enumerate(self.shots):
            self.trans[i] = self._make_transition(i)
        # kpop.json (mFHp6Dd2ISk): the outgoing shot PULLS BACK over its last 6-8 frames before the cut
        # (scale 0.84-0.92). It belongs to the CAMERA, not to one transition, so every cut gets it - except
        # the ones whose own cam_out already pulls back, which would otherwise double it. Resolved here
        # because _init_cameras runs before the "auto" cuts have names.
        pp = self.style['camera'].get('pre_cut_pull')
        if pp:
            own = ('spin_blur', 'spin_settle', 'zoom_settle',
                   'dolly_out_white', 'whip_exp_white', 'whip_exp_black', 'whip_exp_soft', 'whip_horizontal',
                   'photo_collage', 'glass_shatter', 'card_tilt_in')
            for i, s in enumerate(self.shots[:-1]):
                nm = (self.shots[i + 1].get('transition_in') or '').split('+')[0]
                if TR.ALIASES.get(nm, nm) not in own:
                    s['_cam']['pre_pull'] = (float(pp[0]), float(pp[1]))

    def _make_transition(self, i):
        s = self.shots[i]
        rng = np.random.default_rng(s['seed'] + 2)
        eng = self

        def get_img(role, t, j=i):
            k = j - 1 if role == 'out' else j
            if k < 0:
                return np.zeros((eng.canvas[1], eng.canvas[0], 3), np.uint8)
            return eng.render_shot(k, t)

        def stills(n, j=i):
            return eng.shot_stills(j, n)

        def matte(j=i):
            return eng.shot_matte(j)

        cam = s['_cam']
        focus = cam.get('focus') or [0.5, 0.42]
        ctx = TR.Ctx(self.canvas, self.fps, rng, family=self.family, colors=self.colors, beat=self.P,
                     energy=self.energy_at(s['t0']), get_img=get_img, stills=stills, matte=matte, focus=(0.5, focus[1]),
                     flash_color=tuple(self.style['overlays'].get('flash_color') or (255, 255, 255)),
                     leak=bool(self.style['overlays'].get('light_leak_on_flash', True)))
        drop = abs(s['t0'] - self.drop_t) < 0.2
        kw = {'drop': drop}
        if s.get('layout') in ('split2', 'split3', 'card3d', 'postcard', 'hearts'):
            kw['white_cap'] = 0.83          # a built layout never enters through a pure white frame
        kw.update(s.get('transition_args') or s.get('transition_params') or {})
        tr = TR.make(s['transition_in'], ctx, **kw)
        # never let a transition window run past the neighbouring cuts
        prev_len = s['t0'] - self.shots[i - 1]['t0'] if i > 0 else 9.0
        tr.pre = min(tr.pre, max(0.0, prev_len - 1.0 / self.fps))
        return tr

    # ------------------------------------------------------------------ accents
    def _init_accents(self):
        acc = self.style.get('accents') or {}
        plan_acc = self.plan.get('accents') or []
        for s in self.shots:
            rng = np.random.default_rng(s['seed'] + 3)
            ctx = TR.Ctx(self.canvas, self.fps, rng, family=self.family, colors=self.colors)
            lst = []
            spec = (s.get('camera') or {}).get('accents', s.get('accents'))
            items = []
            if isinstance(spec, list):
                items = spec
            elif spec != 'none' and acc.get('auto') and self.beats:
                items = self._auto_accents(s, acc, rng)
            items += [a for a in plan_acc if s['t0'] <= float(a.get('at', -1)) < s['t1']]
            # dance.json: punches are centred on the subject, and the safety rule pins that to the face /
            # upper body (y 0.22-0.52) so a punch can never zoom into legs.
            pc = (s.get('camera') or {}).get('punch_center') or acc.get('center')
            if pc is None:
                f = (s.get('_cam') or {}).get('focus') or [0.5, 0.42]
                pc = [0.5, float(np.clip(float(f[1]), 0.22, 0.52))]
            if NO_SHAKE():
                items = [it for it in items if it.get('kind') not in ('shake', 'bounce', 'shake_blur')]
            for it in items:
                sg = 1.0 if rng.random() < 0.5 else -1.0
                c = it.get('center', pc)
                lst.append(TR.Accent(it.get('kind', 'zoom_in_punch'), float(it['at']), ctx, sign=sg,
                                     amount=float(it.get('amount', 1.0)), center=c))
            # phrase shakes / drop punch
            ps = float(self.style['camera'].get('phrase_shake') or 0.0)
            if ps > 0 and not NO_SHAKE():
                for p in list(self.phrases) + [self.drop_t]:
                    if s['t0'] - 0.05 <= p < s['t1']:
                        lst.append(TR.Accent('shake', p, ctx, amount=ps / 0.015))
            s['_acc'] = lst

    def _auto_accents(self, s, acc, rng):
        mix = acc.get('mix') or {'zoom_in_punch': 1.0}
        names = list(mix)
        w = np.array(list(mix.values()), dtype=float)
        out = []
        every = int(acc.get('every', 1))
        phase = float(acc.get('phase', 0.6))
        for k, b in enumerate(self.beats):
            if k % every:
                continue
            at = b - (1 - phase) * self.P if phase < 1 else b
            if not (s['t0'] + 0.18 <= at < s['t1'] - 0.06):
                continue
            if self.energy_at(b) < acc.get('min_energy', 0.0):
                continue
            if s.get('speed') == 'slowmo' and self.family == 'dance':
                continue
            out.append({'at': at, 'kind': names[int(rng.choice(len(names), p=w / w.sum()))]})
        return out

    # ------------------------------------------------------------------ overlays
    def _postcard_shots_to_scene(self):
        """
        K-pop styles: leading shots with layout "postcard" are the postcard intro's cards (their clip at
        src_in is the photo), the first other shot is the drop clip that develops inside the last card.
        """
        if self.family != 'kpop':
            return None
        lead = []
        for s in self.shots:
            if s['layout'] != 'postcard':
                break
            lead.append(s)
        if not lead or len(lead) == len(self.shots):
            return None
        drop = self.shots[len(lead)]
        return {'type': 'postcard_intro', 't0': lead[0]['t0'], 't1': drop['t0'],
                'cards': [{'path': s['path'], 'clip_t': s['src_in']} for s in lead][:3],
                'caption': (lead[0].get('postcard') or {}).get('caption')}

    def _init_overlays(self):
        self.scenes, self.ovs = [], []
        for ov in self.plan.get('overlays') or []:
            typ = ov.get('type')
            if typ in SCENE_TYPES:
                self.scenes.append(L.SCENES[typ](self, ov, self.canvas))
            elif typ != 'mute':
                self.ovs.append(Overlay(self, ov))
        st = self.style
        pc = (st.get('overlays') or {}).get('particles')
        if pc and self.plan.get('particles', True) is not False:
            self.ovs.insert(0, Overlay(self, {'type': 'particles', 't0': 0.0, 't1': self.duration, 'kind': pc.get('kind', 'sparkles'),
                                              'n': pc.get('n', 50), 'opacity': pc.get('opacity', 0.5), 'auto': True}))
        tx = st.get('text') or {}
        title, sub = self.plan.get('title'), self.plan.get('subtitle')
        when = tx.get('title_when', 'drop')
        if title and tx.get('title') and when != 'none':
            size = tx.get('title_size', 0.07)
            dur = tx.get('title_dur', 1.6)
            if when == 'intro':
                t0 = 0.15
                anim = 'stretch' if tx['title'] == 'neon' else 'fade'
                # an intro scene (music card, words on black, postcards) already carries text: title after it
                sc = [o for o in self.plan.get('overlays') or [] if o.get('type') in SCENE_TYPES and float(o.get('t0', 0)) <= 0.2]
                if sc:
                    t0 = float(sc[0].get('t1', 0)) + 0.1
                    anim = 'fade'
            elif when == 'band_caption':
                self.ovs.append(Overlay(self, {'type': 'text', 'text': title, 'style': tx['title'], 'size': 0.028, 't0': 0.0,
                                                't1': self.duration, 'x': 0.5, 'y': 0.26, 'anim': 'none', 'space': 'frame'}))
                t0 = None
            elif when == 'kinetic':
                self.ovs.append(Overlay(self, {'type': 'kinetic_name', 'text': title, 't0': max(0.0, self.drop_t - 2.2),
                                                't1': self.drop_t + 0.2}))
                t0 = None
            else:
                t0 = self.drop_t
                anim = tx.get('title_anim') or ('letters_3d' if tx['title'] == 'hook_3d' and self.family == 'dance' else
                                                'slam' if tx['title'] == 'hook_3d' else 'pop')
            if t0 is not None:
                self.ovs.append(Overlay(self, {'type': 'title_card', 'text': title, 'style': tx['title'], 'size': size, 't0': t0,
                                                't1': t0 + dur, 'anim': anim, 'y': 0.5}))
                if sub and tx.get('subtitle'):
                    self.ovs.append(Overlay(self, {'type': 'text', 'text': sub, 'style': tx['subtitle'], 'size': tx.get('subtitle_size', 0.03),
                                                    't0': t0 + 0.15, 't1': t0 + dur, 'anim': 'fade', 'y': 0.5 + size * 0.95}))
        wm = self.plan.get('watermark')
        if wm:
            self.ovs.append(Overlay(self, {'type': 'watermark', 'text': wm, 't0': 0.0, 't1': self.duration}))
        end = st.get('end') or {}
        kind, dur = end.get('kind'), float(end.get('dur') or 0.0)
        if kind in ('fade_black', 'white_black', 'bw_fade', 'dip_black_chain') and dur > 0:
            a = self.duration - dur
            if kind == 'bw_fade':
                self.ovs.append(Overlay(self, {'type': 'bw_selective', 't0': a, 't1': self.duration}))
                a = self.duration - min(1.0, dur)
            if kind == 'white_black':
                self.ovs.append(Overlay(self, {'type': 'fade', 't0': a, 't1': a + dur * 0.5, 'color': 'white', 'dir': 'out', 'space': 'frame'}))
                a = a + dur * 0.5
            self.ovs.append(Overlay(self, {'type': 'fade', 't0': a, 't1': self.duration + 1.0 / self.fps, 'color': 'black', 'dir': 'out',
                                            'space': 'frame', 'defocus': kind == 'dip_black_chain'}))

    # ------------------------------------------------------------------ pictures
    def _clip_frame(self, shot, t, panel=None):
        """Work-resolution picture of a shot's clip at output time t (shutter blur when it plays fast)."""
        src = panel or shot
        foot = self.foot(shot, panel)
        foot.gate = bool(shot.get('interp_gate'))
        tm = src['_tm']
        interp = shot.get('interp') or self.interp
        ct = tm(t)
        sp = abs(tm.speed(t))
        sh = self.style['speed']
        soft = sh.get('shutter_soft') or shot.get('shutter_soft')
        if soft and sh.get('shutter', True) and interp != 'native':
            # cinematic_slowmo remake round 8 (critic: a fast-into-slow entry's sharpness stepped 0.24 | 0.50 | 1.07 of
            # its plateau - the smear switched off in one frame at shutter_above and its sample count stepped 5/4/3):
            # a continuous 180-degree shutter, evaluated per OUTPUT frame: clip span = speed x 0.5 / fps, faded to 0
            # with a smoothstep between lo and hi, sample count scaled to the span (no plateaus, no pop)
            lo, hi = (soft if isinstance(soft, (list, tuple)) else (0.25, 0.9))
            x = min(1.0, max(0.0, (sp - lo) / max(1e-6, hi - lo)))
            k = x * x * (3 - 2 * x)
            if k > 0.02:
                half = 0.25 / self.fps * k
                c0, c1 = tm(t - half), tm(t + half)
                n = int(min(9, max(3, math.ceil(abs(c1 - c0) * foot.fps * 3) + 1)))
                acc = None
                for q in range(n):
                    f = foot.frame(c0 + (c1 - c0) * q / (n - 1), interp)
                    acc = f.astype(np.uint16) if acc is None else acc + f
                return (acc // n).astype(np.uint8)
            return foot.frame(ct, interp)
        if sh.get('shutter', True) and sp > sh.get('shutter_above', 1.5) and interp != 'native':
            half = 0.25 / self.fps
            c0, c1 = tm(t - half), tm(t + half)
            n = int(min(5, max(3, (c1 - c0) * foot.fps * 2)))
            acc = None
            for q in range(n):
                f = foot.frame(c0 + (c1 - c0) * q / (n - 1), interp)
                acc = f.astype(np.uint16) if acc is None else acc + f
            return (acc // n).astype(np.uint8)
        return foot.frame(ct, interp)

    def _matrix(self, shot, foot, size, cam, t, panel=None, layout='fill'):
        """2x3 affine: work frame -> target picture (cover around the subject, then the camera)."""
        ww, wh = foot.size
        cw, ch = size
        lns, rot, dx, dy = cam[:4]
        c = shot['_cam']
        if layout in ('fit', 'letterbox', 'fit_black'):
            b = cw / ww * (1.22 if layout == 'letterbox' else 1.0)
            px, py = ww / 2, wh / 2
        else:
            b = max(cw / ww, ch / wh)
            focus = (panel or {}).get('focus') or c.get('focus')
            fx_, fy_ = (focus if focus else (foot.prep.focus[0], 0.45))
            if c.get('follow') and foot.prep.track and not panel:
                ct = (panel or shot)['_tm'](t)
                tr = foot.prep.track
                xs = [p[1] for p in tr]
                ts = [p[0] for p in tr]
                fx_ = float(np.interp(ct, ts, xs))
                fx_ = float(np.clip(fx_, foot.prep.focus[0] - 0.12, foot.prep.focus[0] + 0.12))
            if c.get('focus_scaled'):
                # optional (S2rz planner round 5): the focus is clamped at the ZOOMED scale, so a push centres on the
                # face (the default clamp at the cover scale pins a vertically-full plate to its middle: a nose crop)
                bs = b * math.exp(lns)
                px = min(max(fx_ * ww, cw / (2 * bs)), ww - cw / (2 * bs)) if bs * ww > cw else ww / 2
                py = min(max(fy_ * wh, ch / (2 * bs)), wh - ch / (2 * bs)) if bs * wh > ch else wh / 2
            else:
                px = min(max(fx_ * ww, cw / (2 * b)), ww - cw / (2 * b)) if b * ww > cw else ww / 2
                py = min(max(fy_ * wh, ch / (2 * b)), wh - ch / (2 * b)) if b * wh > ch else wh / 2
        s = b * math.exp(lns)
        a = math.radians(rot)
        ca, sa = math.cos(a) * s, math.sin(a) * s
        M = np.array([[ca, sa, 0.0], [-sa, ca, 0.0]], np.float64)
        M[0, 2] = cw / 2 + dx * cw - (ca * px + sa * py)
        M[1, 2] = ch / 2 + dy * cw - (-sa * px + ca * py)
        return M

    def place(self, shot, t, size, cam=(0.0, 0.0, 0.0, 0.0), panel=None, camf=None, layout=None):
        """The shot's clip drawn into a picture of `size` with a camera (one resample; motion blur when it moves fast)."""
        layout = layout or ('fill' if shot['layout'] not in ('fit', 'letterbox', 'fit_black') else shot['layout'])
        foot = self.foot(shot, panel)
        img = self._clip_frame(shot, t, panel)
        c = camf(t) if camf else cam
        if C3.is3d(c) or (camf is not None and (C3.is3d(camf(t - 0.49 / self.fps)) or C3.is3d(camf(t + 0.49 / self.fps)))):
            return self._fit_bg(C3.place(self, shot, t, size, c, camf, layout, img, foot, panel), shot, img, size, layout)
        M = self._matrix(shot, foot, size, c, t, panel, layout)
        border = cv2.BORDER_REFLECT101
        thr = float(self.style['camera'].get('motion_blur', 6.0))
        if camf is not None and thr < 1e6:
            # style camera 'shutter' (fraction of the frame interval, default 1.0 = the old 360 deg): a remake can
            # match its reference's shorter shutter (S2rz: crisp frames under fast zooms)
            dt = float(self.style['camera'].get('shutter', 1.0)) / self.fps
            # 0.49, not 0.5: neighbouring frames must not share a shutter sample, or a camera that STEPS
            # between frames (hard_settle) is smeared across the step
            M0 = self._matrix(shot, foot, size, camf(t - dt * 0.49), t, panel, layout)
            M1 = self._matrix(shot, foot, size, camf(t + dt * 0.49), t, panel, layout)
            pts = np.array([[0, 0, 1], [foot.size[0], 0, 1], [0, foot.size[1], 1], [foot.size[0], foot.size[1], 1],
                            [foot.size[0] / 2, foot.size[1] / 2, 1]], np.float64).T
            move = float(np.abs(M1 @ pts - M0 @ pts).max()) * (1080.0 / max(size))
            if move > thr:
                n = int(min(12, max(3, move / 4)))
                acc = None
                for q in range(n):
                    u = q / (n - 1)
                    Mq = M0 * (1 - u) + M1 * u
                    wq = cv2.warpAffine(img, Mq, size, flags=cv2.INTER_LINEAR, borderMode=border)
                    acc = wq.astype(np.uint16) if acc is None else acc + wq
                out = (acc // n).astype(np.uint8)
                return self._fit_bg(out, shot, img, size, layout)
        s_eff = math.sqrt(abs(np.linalg.det(M[:, :2])))
        flags = cv2.INTER_CUBIC if s_eff > 0.8 else cv2.INTER_LINEAR
        out = cv2.warpAffine(img, M, size, flags=flags, borderMode=border)
        return self._fit_bg(out, shot, img, size, layout)

    def _fit_bg(self, out, shot, img, size, layout):
        if layout not in ('fit', 'letterbox', 'fit_black'):
            return out
        # fit layouts: the picture over a dark blurred copy of itself (or black) instead of mirrored edges
        foot = self.foot(shot)
        cw, ch = size
        b = cw / foot.size[0] * (1.22 if layout == 'letterbox' else 1.0)
        ph = int(foot.size[1] * b)
        if ph >= ch:
            return out
        y0 = (ch - ph) // 2
        if layout == 'fit_black':
            bg = np.zeros_like(out)
        else:
            small = cv2.resize(img, (max(8, cw // 12), max(8, ch // 12)), interpolation=cv2.INTER_AREA)
            bg = cv2.convertScaleAbs(cv2.resize(cv2.GaussianBlur(small, (0, 0), 2.5), size), alpha=0.28)
        bg[y0:y0 + ph] = out[y0:y0 + ph]
        return bg

    def shot_cam(self, j, t, _carry=True):
        s = self.shots[j]
        c = self.base_cam(s, t)
        tr = self.trans[j]
        v = t - s['t0']
        if -0.26 / self.fps < v < 0:      # shot_at() takes a shot a quarter frame early: its join frame gets fx_in too
            v = 0.0
        mirror = False
        under = False
        if tr is not None and -0.6 / self.fps < v < 0 and getattr(tr, 'snap', False):
            c = TR.add_cam(c, tr.cam_in(0.0))                   # the open shutter of the join frame
        if tr is not None and v >= 0 and (v < tr.post or tr.hold):
            c = TR.add_cam(c, tr.cam_in(v))
            mirror = mirror or tr.name.startswith('mirror_tile')
        if j + 1 < len(self.shots):
            nt = self.trans[j + 1]
            u = self.shots[j + 1]['t0'] - t
            if nt is not None and 0 < u <= nt.pre:
                c = TR.add_cam(c, nt.cam_out(u))
                under = bool(getattr(nt, 'under_ok', False))    # a real pull-back: fx_out paints the set behind it
            pz = (self.shots[j + 1].get('transition_params') or {}).get('pre_zoom')
            if pz and 0 < u * self.fps <= 4.0:
                # round 6 (S2rz remake): the reference's zoom runs UP into the cut (ln/s of the last 3 outgoing steps, e.g.
                # -1.6 -3.6 -9.8); the offsets are cumulative ln scale on the last outgoing frames (m = frames before the cut)
                r3 = [float(x) / self.fps for x in pz][:3]
                o3, o2, o1 = r3[0], r3[0] + r3[1], r3[0] + r3[1] + r3[2]
                c = TR.add_cam(c, (float(np.interp(u * self.fps, [0.0, 1.0, 2.0, 3.0, 4.0], [o1, o1, o2, o3, 0.0])), 0.0, 0.0, 0.0))
                under = under or o1 < 0
        c = HO.drive(self, j, t, c)                          # every shot keeps moving (handover.drive)
        if _carry:
            c = HO.carry(self, j, t, c)                      # the move runs through the cut (handover.py)
        for a in s['_acc']:
            if a.active(t):
                c = TR.add_cam(c, a.cam(t))
                mirror = mirror or a.kind == 'zoom_out_punch'
        if not mirror and not under and c[0] < 0 and s['_cam'].get('zoom', 1.0) >= 1.0:
            c = (0.0,) + tuple(c[1:])
        return c

    def render_shot(self, j, t):
        key = (j, round(t * self.fps * 8))
        if key in self._memo:
            return self._memo[key]
        s = self.shots[j]
        lay = s['layout']
        camf = lambda tt: self.shot_cam(j, tt)  # noqa: E731
        if lay in L.SHOT_LAYOUTS:
            if '_layout_obj' not in s:
                s['_layout_obj'] = L.SHOT_LAYOUTS[lay](self, s, self.canvas)
            img = s['_layout_obj'].render(t, camf(t))
        else:
            # transitions with no_mblur = True (rack_focus, zoom_settle, hard_settle): their settle must not be
            # shutter-smeared - the join frame is sharp (dance) or a pure defocus (fx), never a radial streak.
            nomb = False
            tr0 = self.trans[j]
            if tr0 is not None and getattr(tr0, 'no_mblur', False) and 0 <= t - s['t0'] < tr0.post:
                nomb = True
            if j + 1 < len(self.shots):
                nt0 = self.trans[j + 1]
                if nt0 is not None and getattr(nt0, 'no_mblur', False) and 0 < self.shots[j + 1]['t0'] - t <= nt0.pre:
                    nomb = True
            if nomb:
                img = self.place(s, t, self.canvas, cam=camf(t), layout=lay if lay in ('fit', 'letterbox', 'fit_black') else 'fill')
            else:
                img = self.place(s, t, self.canvas, camf=camf, layout=lay if lay in ('fit', 'letterbox', 'fit_black') else 'fill')
        if s.get('set'):
            # shot["set"] = [overlay dicts] (or one dict): a BUILT set (white_set, glass_set, polaroids, letters_fly,
            # music_card3d ...) painted over the plate BEFORE the joins, so a transition into / out of the set works
            # on the set's own picture (whip_exp_soft resolves INTO the white set, dolly_out_white recedes on it).
            if '_set_ovs' not in s:
                lst = s['set'] if isinstance(s['set'], list) else [s['set']]
                s['_set_ovs'] = [Overlay(self, dict(o, t0=float(o.get('t0', 0.0)) + s['t0'],
                                                    t1=min(s['t1'] + 1.0, float(o['t1']) + s['t0']) if 't1' in o else s['t1'] + 1.0,
                                                    space='canvas')) for o in lst]
            img = img.copy()
            for so in s['_set_ovs']:
                if so.t0 <= t < so.t1:
                    img = so.apply(img, t)
        tr = self.trans[j]
        v = t - s['t0']
        if -0.26 / self.fps < v < 0:      # shot_at() takes a shot a quarter frame early: its join frame gets fx_in too
            v = 0.0
        if tr is not None and 0 <= v < tr.post and not tr.composite_post:
            img = tr.fx_in(img, v)
        if j + 1 < len(self.shots):
            nt = self.trans[j + 1]
            u = self.shots[j + 1]['t0'] - t
            if nt is not None and 0 < u <= nt.pre and not nt.composite_pre:
                if getattr(nt, 'under_ok', False):
                    nt.under_cam = self.shot_cam(j, t)          # total camera, so fx_out knows where the picture is
                img = nt.fx_out(img, u)
        for a in s['_acc']:
            if a.active(t):
                img = a.fx(img, t)
        self._memo[key] = img
        return img

    def shot_stills(self, j, n):
        """Stills of other shots (the same source first) for collages / clone cards."""
        me = self.shots[j]
        others = [s for s in self.shots if s['path'] != me['path']]
        same = [s for s in others if s.get('source_id') and s.get('source_id') == me.get('source_id')]
        pool = same + [s for s in others if s not in same] or [me]
        out = []
        size = (int(self.canvas[0] * 0.6), int(self.canvas[0] * 0.6 * self.canvas[1] / self.canvas[0]))
        for s in pool[:n]:
            key = ('still', s['i'])
            if key not in self._maps:
                self._maps[key] = self.place(s, (s['t0'] + s['t1']) / 2, size)
            out.append(self._maps[key])
        return out

    def shot_matte(self, j):
        key = ('matte', j)
        if key not in self._maps:
            s = self.shots[j]
            img = self.place(s, s['t0'] + 0.02, self.canvas)
            m = L.subject_matte(img)
            self._maps[key] = (img, m) if m is not None else None
        return self._maps[key]

    def card_stills(self, ov, n):
        """Stills for scene cards: overlay 'cards' [{path, clip_t}] or frames of the plan's shots."""
        out = []
        for c in (ov.get('cards') or [])[:n]:
            path = c.get('path')
            if not path or not os.path.exists(path):
                continue
            key = (path, self.look_name)
            if key not in self.footage:
                prep = FT.analyze(path)
                self.preps[path] = prep
                crop = self._crop(prep, {})
                k = min(1.0, 900.0 / min(crop[2], crop[3]))
                ww, wh = int(crop[2] * k) // 2 * 2, int(crop[3] * k) // 2 * 2
                gamma, bloom_k = prep.exposure(self.look.get('exposure')) if self.look.get('exposure') else (1.0, 1.0)
                vf = S.grade_graph(S.adapt_look(self.look, prep), ww, gamma, bloom_k, levels=(prep.p02, prep.p98) if self.look.get('levels') else None)
                self.footage[key] = FT.Footage(path, vf, crop, (ww, wh), prep)
            f = self.footage[key]
            out.append(f.native(float(c.get('clip_t') or 0.0)).copy())
        if len(out) < n:
            seen = set()
            for s in self.shots:
                if len(out) >= n:
                    break
                if s['path'] in seen or abs(s['t0'] - float(ov.get('t1', 0))) < 0.05:
                    continue
                seen.add(s['path'])
                out.append(self.place(s, (s['t0'] + s['t1']) / 2, (900, 900)))
        return out

    def drop_picture(self, t, size):
        """The shot that starts at the drop, drawn at `size` at time t (it plays inside the last postcard)."""
        j = next((i for i, s in enumerate(self.shots) if s['t0'] >= self.drop_t - 0.05), 0)
        return self.place(self.shots[j], t, size)

    def music_envelope(self):
        a = self._load_music()
        if a is None:
            return None
        mono = np.abs(a).mean(axis=0)
        hop = audio_io.SR // 100
        n = len(mono) // hop
        env = mono[:n * hop].reshape(n, hop).max(axis=1)
        env = env / max(1e-6, np.percentile(env, 98))
        return np.clip(env, 0, 1.2), 100.0

    def _load_music(self):
        if self._music is None and self.music and os.path.exists(self.music):
            self._music = audio_io.load_audio(self.music)
        return self._music

    # ------------------------------------------------------------------ frames
    def shot_at(self, t):
        eps = 0.25 / self.fps
        j = 0
        for i, s in enumerate(self.shots):
            if s['t0'] <= t + eps:
                j = i
            else:
                break
        return j

    def frame(self, t):
        if self._memo_t != t:
            self._memo = {}
            self._memo_t = t
        pic = None
        for sc in self.scenes:
            if sc.ov['t0'] <= t < sc.ov['t1']:
                pic = sc.render(t)
                break
        if pic is None:
            j = self.shot_at(t)
            s = self.shots[j]
            tr = self.trans[j]
            v = t - s['t0']
            if -0.26 / self.fps < v < 0:
                v = 0.0
            if tr is not None and tr.composite_post and 0 <= v < tr.post and j > 0:
                pic = tr.composite(v, t)
            if pic is None and j + 1 < len(self.shots):
                nt = self.trans[j + 1]
                u = self.shots[j + 1]['t0'] - t
                if nt is not None and nt.composite_pre and 0 < u <= nt.pre:
                    pic = nt.composite(-u, t)
            if pic is None:
                pic = self.render_shot(j, t)
        pic = self._canvas_fx(pic, t)
        out = self.fr.compose(pic)
        for ov in self.ovs:
            if ov.space == 'frame' and ov.active(t):
                out = ov.apply(out, t)
        return out

    def _canvas_fx(self, img, t):
        ovs = self.style.get('overlays') or {}
        grad = float(self.plan.get('gradient', ovs.get('gradient', 0.0)) or 0.0)
        if grad > 0:
            img = self._gradient(img, t, grad)
        vig = self.plan.get('vignette', ovs.get('vignette', self.look.get('vignette', 0.0)))
        if vig and vig > 0.01:
            key = ('vig', img.shape[1], img.shape[0], round(vig, 3))
            if key not in self._maps:
                self._maps[key] = fx.vignette_map((img.shape[1], img.shape[0]), vig)
            img = fx.apply_map(img, self._maps[key])
        if img.flags.writeable is False or any(img is v for v in self._memo.values()):
            img = img.copy()
        for ov in self.ovs:
            if ov.space == 'canvas' and ov.active(t):
                img = ov.apply(img, t)
        return img

    def _gradient(self, img, t, strength):
        """Palette gradient in soft light (cheap form: f + (2g - 1) f (1 - f) k), drifting with time."""
        h, w = img.shape[:2]
        key = ('lutf', 0)
        if key not in self._maps:
            f = np.arange(256) / 255.0
            self._maps[key] = np.clip(f * (1 - f) * 4 * 255, 0, 255).astype(np.uint8)
        ang = round(t * 0.35 / math.radians(2)) * math.radians(2)        # maps cached per 2 degrees of drift
        gk = ('grad', w, h, round(ang, 4), strength)
        if gk not in self._maps:
            for k in [k for k in self._maps if k[0] == 'grad']:
                self._maps.pop(k)
            sw, sh = max(4, w // 16), max(4, h // 16)
            yy, xx = np.mgrid[0:sh, 0:sw].astype(np.float32)
            u = ((xx - sw / 2) * math.cos(ang) + (yy - sh / 2) * math.sin(ang)) / (0.5 * math.hypot(sw, sh)) * 0.5 + 0.5
            c0, c1 = np.array(self.colors[0], np.float32) / 255, np.array(self.colors[-1], np.float32) / 255
            g = c0 * (1 - u[..., None]) + c1 * u[..., None]
            self._maps[gk] = (cv2.resize(np.clip((2 * g - 1) * 255 * strength, 0, 255).astype(np.uint8), (w, h)),
                              cv2.resize(np.clip((1 - 2 * g) * 255 * strength, 0, 255).astype(np.uint8), (w, h)))
        pos, neg = self._maps[gk]
        ff = cv2.LUT(img, self._maps[key])
        img = cv2.add(img, cv2.multiply(ff, pos, scale=0.25 / 255))
        return cv2.subtract(img, cv2.multiply(ff, neg, scale=0.25 / 255))

    # ------------------------------------------------------------------ memory
    def prefetch(self, j):
        """Decode a shot's whole source range (plus transition margins) in one go."""
        if j < 0 or j >= len(self.shots):
            return
        s = self.shots[j]
        if s.get('_prefetched'):
            return
        s['_prefetched'] = True
        if s['t1'] < getattr(self, '_now', 0.0):
            return
        f = self.foot(s)
        a, b = s['_tm'].span(s['t0'] - 0.4, s['t1'] + 0.3)
        f.ensure(int(a * f.fps) - 1, int(b * f.fps) + 6)
        for p in s.get('panels') or []:
            fp = self.foot(s, p)
            a, b = p['_tm'].span(s['t0'], s['t1'])
            fp.ensure(int(a * fp.fps) - 1, int(b * fp.fps) + 6)

    def release(self, t):
        """Drop decoded frames no remaining shot needs."""
        keep = {}
        for s in self.shots:
            if s['t1'] + POST_MARGIN < t:
                continue
            f = self.foot(s)
            a, b = s['_tm'].span(max(t, s['t0'] - 3.0), s['t1'] + POST_MARGIN)
            keep.setdefault(id(f), (f, []))[1].append((int(a * f.fps) - 2, int(b * f.fps) + 10))
            for p in s.get('panels') or []:
                fp = self.foot(s, p)
                a, b = p['_tm'].span(max(t, s['t0'] - PRE_MARGIN), s['t1'] + POST_MARGIN)
                keep.setdefault(id(fp), (fp, []))[1].append((int(a * fp.fps) - 2, int(b * fp.fps) + 10))
        for f in self.footage.values():
            if id(f) in keep:
                f.release(keep[id(f)][1])
            elif not f.is_image:
                f.release([])

    # ------------------------------------------------------------------ audio
    def build_audio(self, path):
        SR = audio_io.SR
        n = int(round(self.duration * SR))
        mix = np.zeros((2, n))
        au = self.style.get('audio') or {}
        mode = self.plan.get('clip_audio', 'none')
        music = self._load_music() if mode != 'only' else None
        env_rms = None
        if music is not None:
            i0 = int(round(self.music_start * SR))
            seg = music[:, max(0, i0):max(0, i0) + n]
            if i0 < 0:
                seg = np.concatenate([np.zeros((2, -i0)), seg], axis=1)[:, :n]
            if seg.shape[1] < n:
                seg = np.pad(seg, ((0, 0), (0, n - seg.shape[1])))
            seg = seg.copy()
            try:  # song edits that replace the song's content (core/edit/song_fx.py: song_cut, version) - before fades
                import song_fx as SONGFX
                seg = SONGFX.pre(self, seg, SR, music)
            except Exception as e:
                self.warnings.append(f'song_fx pre: {e}')
            fi = float(self.plan.get('fade_in', au.get('fade_in', 0.0)) or 0.0)
            fo = float(self.plan.get('fade_out', au.get('fade_out', 0.0)) or 0.0)
            end = self.style.get('end') or {}
            if end.get('kind') in ('fade_black', 'white_black', 'bw_fade', 'dip_black_chain'):
                fo = max(fo, min(1.5, float(end.get('dur') or 0.0)))
            if fi > 0:
                k = int(fi * SR)
                seg[:, :k] *= np.linspace(0, 1, k)
            if fo > 0:
                k = min(n, int(fo * SR))
                seg[:, n - k:] *= np.linspace(1, 0, k)
            for ov in self.plan.get('overlays') or []:
                if ov.get('type') == 'mute':
                    a, b = int(float(ov['t0']) * SR), int(float(ov['t1']) * SR)
                    r = int(0.005 * SR)
                    g = np.ones(n)
                    g[max(0, a):max(0, b)] = 0.0
                    if a - r > 0:
                        g[a - r:a] = np.linspace(1, 0, r)
                    if b + r < n:
                        g[b:b + r] = np.linspace(0, 1, r)
                    seg *= g
            # music treatments (core/edit/sfx.py): plan["music_fx"] = [{"type": "duck"|"lowpass_sweep"|"silence_gap"|
            # "tape_stop", ...}]; absent = the song untouched (what every reference measured, audio-sfx.md)
            for mf in self.plan.get('music_fx') or []:
                try:
                    import sfx as SFX
                    ty = mf.get('type')
                    if ty == 'duck':
                        SFX.duck(seg, SR, float(mf['at']), float(mf.get('depth_db', -4.0)), float(mf.get('attack', 0.03)),
                                 float(mf.get('hold', 0.08)), float(mf.get('release', 0.25)))
                    elif ty == 'lowpass_sweep':
                        SFX.lowpass_sweep(seg, SR, float(mf['t0']), float(mf['t1']), float(mf.get('f0', 400.0)),
                                          float(mf.get('f1', 18000.0)), float(mf.get('curve', 2.0)))
                    elif ty == 'silence_gap':
                        SFX.silence_gap(seg, SR, float(mf['t0']), float(mf['t1']))
                    elif ty == 'tape_stop':
                        SFX.tape_stop(seg, SR, float(mf['t0']), float(mf.get('dur', 0.5)))
                except Exception as e:
                    self.warnings.append(f'music_fx {mf}: {e}')
            # the measured song edits + overlays + clip audio per preset (core/edit/song_fx.py, clip_audio.py; plan
            # "audio_design" / "song_fx" / "clip_events"; absent and sfx != "auto" = nothing, the old behaviour)
            try:
                import song_fx as SONGFX
                seg, self._song_fx_layer = SONGFX.post(self, seg, SR)
            except Exception as e:
                self._song_fx_layer = None
                self.warnings.append(f'song_fx: {e}')
            mix += seg
            win = int(0.3 * SR)
            p2 = np.cumsum(np.pad((seg ** 2).mean(axis=0), (1, 0)))
            idx = np.arange(n)
            lo, hi = np.clip(idx - win, 0, n), np.clip(idx + win, 0, n)
            env_rms = np.sqrt(np.maximum(1e-12, (p2[hi] - p2[lo]) / np.maximum(1, hi - lo)))[::441]
        n_sfx = 0
        self._song_seg = mix.copy() if music is not None else None
        for s in self.shots:
            want = s.get('audio')
            if mode == 'only' or (mode == 'mix' and want is not False and s.get('speed', 'normal') in ('normal', 'synced')
                                  and not s.get('sfx')):
                a = FT.clip_audio(s['path'], SR)
                if a is not None:
                    cg = s.get('clip_gain_db', self.plan.get('clip_audio_gain_db', au.get('clip_gain_db', -8.0)))
                    self._mix_shot_audio(mix, a, s, SR, env_rms, 0.0 if mode == 'only' else float(cg))
            for k, h in enumerate(s.get('sfx') or []):
                a = FT.clip_audio(s['path'], SR)
                if a is None:
                    continue
                nxt = self._next_hit(s, h)
                if self._mix_hit(mix, a, float(h['clip_t']), float(h['at']), nxt, float(h.get('gain') or 0.0), SR, env_rms, au):
                    n_sfx += 1
        sfx_info = self._mix_sfx_design(mix, SR)
        if getattr(self, '_song_fx_layer', None) is not None:
            mix += self._song_fx_layer
        if music is None and not np.any(mix):
            mix += 1e-6
        if au.get('bus_comp'):
            mix = _bus_comp(mix, SR, **(au['bus_comp'] if isinstance(au['bus_comp'], dict) else {}))
        y, _ = audio_io.master(mix, target=float(au.get('lufs', -14.0)), ceiling_db=float(au.get('ceiling_db', -2.0)))
        audio_io.write_wav(path, y)
        return {'lufs': round(audio_io.lufs(y), 2), 'true_peak_dbtp': round(20 * math.log10(max(1e-9, audio_io.true_peak(y))), 2),
                'sfx_hits': n_sfx, 'sfx_design': sfx_info, 'song_fx': getattr(self, 'song_fx_report', None)}

    def _mix_sfx_design(self, mix, SR):
        """Designed transition sounds (core/edit/sfx.py). plan["sfx"]: absent / "none" = nothing (old behaviour);
        "auto" = the preset's row in sfx.PRESET_SFX; "soft" / "full" = that profile; plan["sfx_cues"] = extra
        hand-placed cues [{"sfx", "at", "gain_db", ...}]. Levels ride on the music's +-0.3 s RMS."""
        extra = self.plan.get('sfx_cues') or []
        # 2026-10-01 text sounds (core/edit/text_sound.py, refs/text-sound.md): plan["text_sound"] absent / "none" = nothing
        tcues = []
        if self.plan.get('text_sound') not in (None, False, '', 'none', 'off'):
            try:
                import text_sound as TS
                tcues = TS.text_cues(self.plan, duration=self.duration)
            except Exception as e:  # noqa: BLE001
                self.warnings.append(f'text_sound: {e}')
        if self.plan.get('sfx') in (None, False, '', 'none', 'off') and not extra and not tcues:
            return {'mode': 'none', 'cues': 0}
        try:
            import sfx as SFX
            prof = SFX.resolve_mode(self.plan, self.style.get('name'))
            cues = SFX.cues_for_engine(self, prof, TR.ALIASES) + [dict(c) for c in extra]
            g = float(self.plan.get('sfx_gain_db', 0.0) or 0.0)
            for c in cues:
                c['gain_db'] = float(c.get('gain_db', -6.0)) + g
            cues += tcues                                    # text cues carry their own trim (text_sound_gain_db)
            if not cues:
                return {'mode': prof, 'cues': 0}
            layer, rep = SFX.render_cues(cues, self.duration, SR, self._song_seg)
            mix += layer
            self.sfx_report = rep
            return {'mode': prof, 'cues': len(rep), 'list': rep}
        except Exception as e:  # sound design must never break a render
            self.warnings.append(f'sfx design: {e}')
            return {'mode': 'error', 'cues': 0}

    def _next_hit(self, s, h):
        at = float(h['at'])
        later = [float(x['at']) for sh in self.shots for x in (sh.get('sfx') or []) if float(x['at']) > at + 1e-3]
        return min(later) if later else None

    def _mix_shot_audio(self, mix, a, s, SR, env_rms, gain_db):
        t0, t1 = s['t0'], min(self.duration, s['t1'])
        c0 = s['_tm'](t0)
        i0, n = int(t0 * SR), int((t1 - t0) * SR)
        j0 = int(c0 * SR)
        seg = a[:, j0:j0 + n].copy()
        if seg.shape[1] < 16:
            return
        r = min(seg.shape[1] // 3, int(0.015 * SR))
        seg[:, :r] *= np.linspace(0, 1, r)
        seg[:, -r:] *= np.linspace(1, 0, r)
        hp = float(self.plan.get('clip_audio_hp', 0.0) or 0.0)
        if hp > 0:  # pass-through treatment: keep clip rumble out of the song's low end (off by default)
            from scipy import signal as _sg
            seg = _sg.sosfilt(_sg.butter(2, hp, 'highpass', fs=SR, output='sos'), seg, axis=1)
        rms = float(np.sqrt((seg ** 2).mean()) + 1e-9)
        ref = float(np.median(env_rms)) if env_rms is not None else 0.1
        seg *= (ref / rms) * 10 ** (gain_db / 20)
        m = min(seg.shape[1], mix.shape[1] - i0)
        if m > 0:
            mix[:, i0:i0 + m] += seg[:, :m]

    @staticmethod
    def attack(x, t, SR, win=0.04):
        """Time of the steepest energy rise (the attack) of signal x (2, n) within t +- win (1 ms resolution)."""
        a, b = int(max(0.0, t - win) * SR), int((t + win) * SR)
        seg = x[:, a:b].mean(axis=0) if x.ndim == 2 else x[a:b]
        hop, w = SR // 1000, SR // 500
        if len(seg) < 4 * w:
            return t
        e = np.sqrt(np.convolve(seg ** 2, np.ones(w) / w, mode='same')[::hop] + 1e-12)
        d = np.diff(20 * np.log10(e + 1e-6))
        k = int(np.argmax(d))
        return (a + k * hop) / SR

    def _mix_hit(self, mix, a, clip_t, at, nxt, gain_off, SR, env_rms, au):
        """The clip's own hit, gated (from 10 ms before), EQ'd bright, levelled to music + 5 dB, landing exactly on `at`."""
        from scipy import signal
        pre = 0.010
        if au.get('snap_attacks', True):
            # align the attacks themselves (planners often pass onset-envelope peaks, 10-40 ms late)
            clip_t = self.attack(a, clip_t, SR)
            if self._song_seg is not None:
                at = self.attack(self._song_seg, at, SR, win=0.03)
        L = 0.9 if nxt is None else max(0.06, min(0.9, nxt - at - 0.025))
        j0 = int((clip_t - pre) * SR)
        if j0 < 0:
            return False
        seg = a[:, j0:j0 + int((pre + L) * SR)].copy()
        if seg.shape[1] < int(0.03 * SR):
            return False
        mono = seg.mean(axis=0)
        spec = np.abs(np.fft.rfft(mono * np.hanning(len(mono))))
        freqs = np.fft.rfftfreq(len(mono), 1 / SR)
        low_share = spec[freqs < 150].sum() / max(1e-9, spec.sum())
        if low_share < 0.35 and au.get('sfx_hp', 100.0):
            sos = signal.butter(2, float(au.get('sfx_hp', 100.0)), 'highpass', fs=SR, output='sos')
            seg = signal.sosfilt(sos, seg, axis=1)
        # presence +2 dB at 3.5 kHz (RBJ peaking, Q 1)
        A_ = 10 ** (2 / 40)
        w0 = 2 * math.pi * 3500 / SR
        al = math.sin(w0) / 2
        b = [1 + al * A_, -2 * math.cos(w0), 1 - al * A_]
        a_ = [1 + al / A_, -2 * math.cos(w0), 1 - al / A_]
        seg = signal.lfilter(np.array(b) / a_[0], np.array(a_) / a_[0], seg, axis=1)
        m = seg.shape[1]
        tt = np.arange(m) / SR
        env = np.clip((tt - (pre - 0.008)) / 0.008, 0, 1) * np.clip((m / SR - tt) / 0.03, 0, 1)
        seg *= env
        k0, k1 = int(pre * SR), int((pre + 0.12) * SR)
        w20 = int(0.02 * SR)
        body = (seg[:, k0:k1] ** 2).mean(axis=0)
        if len(body) < w20:
            return False
        rms20 = np.sqrt(np.convolve(body, np.ones(w20) / w20, mode='valid').max() + 1e-12)
        if env_rms is not None:
            music_db = 20 * math.log10(float(env_rms[min(len(env_rms) - 1, int(at * SR) // 441)]) + 1e-9)
        else:
            music_db = -20.0
        target = music_db + float(au.get('sfx_over_music_db', 5.0)) + max(-6.0, min(6.0, gain_off))
        g_db = max(-12.0, min(12.0, target - 20 * math.log10(rms20)))
        seg *= 10 ** (g_db / 20)
        i0 = int(round((at - pre) * SR))
        if i0 < 0:
            seg = seg[:, -i0:]
            i0 = 0
        m = min(seg.shape[1], mix.shape[1] - i0)
        if m <= 0:
            return False
        mix[:, i0:i0 + m] += seg[:, :m]
        return True

    # ------------------------------------------------------------------ render
    def render(self, out_path, preset='auto', crf=16, start=None, end=None, dump=None, times=None, threads=None):
        # NVENC when the GPU is usable (never on GitHub Actions / CI, EDIT_GPU=0 forces CPU); a failed NVENC encode
        # is rerun once from the start with libx264, so a render never dies because of the GPU
        try:
            return self._render(out_path, preset, crf, start, end, dump, times, threads, False)
        except SystemExit as e:
            if 'ffmpeg encode failed' not in str(e) or not _gpu.usable():
                raise
            log('NVENC encode failed -> rerendering with libx264')
            return self._render(out_path, preset, crf, start, end, dump, times, threads, True)

    def _render(self, out_path, preset, crf, start, end, dump, times, threads, force_cpu):
        t_start = time.time()
        self.stats['frames'] = 0
        os.makedirs(os.path.dirname(os.path.abspath(out_path)) or '.', exist_ok=True)
        fps = self.fps
        if dump:
            os.makedirs(dump, exist_ok=True)
            ts = times or []
            for t in ts:
                img = self.frame(float(t))
                cv2.imwrite(os.path.join(dump, f'f_{float(t):08.3f}.png'), img)
            return {'ok': True, 'dumped': len(ts), 'dir': dump, 'render_s': round(time.time() - t_start, 2)}
        a0 = float(start or 0.0)
        a1 = min(self.duration, float(end)) if end is not None else self.duration
        wav = os.path.join(self.work, 'audio.wav')
        t_a = time.time()
        ainfo = self.build_audio(wav)
        self.timing['audio_s'] = round(time.time() - t_a, 2)
        W, H = self.out_size
        if preset in (None, 'auto'):
            # x264 on 2 cores at 1080x1920 60 fps: slow 3.3 fps, medium 7.2 fps (measured); keep shorts < 20 min
            px_s = W * H * fps * (a1 - a0)
            preset = 'slow' if px_s <= 1080 * 1920 * 60 * 20 else 'medium'
        self.preset = preset
        vf = S.encoder_vf(self.look, self.plan.get('grain'))
        if self.scale < 1:
            vf = [v for v in vf if not v.startswith('noise')]
        vf += ['scale=out_color_matrix=bt709:out_range=tv', 'format=yuv420p']
        cmd = ['ffmpeg', '-y', '-v', 'error', '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-s', f'{W}x{H}', '-r', f'{fps:g}', '-i', '-']
        if a0 > 0:
            cmd += ['-ss', f'{a0:.4f}']
        use_gpu = _gpu.usable() and not force_cpu
        purpose = 'test' if (preset in ('ultrafast', 'superfast', 'veryfast', 'faster', 'fast') or crf >= 19) else 'final'
        if use_gpu:
            self.preset = f'nvenc-{purpose}'
            enc_args = _gpu.venc(purpose)
            cmd[0] = _gpu.ffmpeg_bin()
        else:
            enc_args = ['-c:v', 'libx264', '-preset', preset, '-crf', str(crf)]
        cmd += ['-i', wav, '-vf', ','.join(vf)] + enc_args + [
                '-colorspace', 'bt709', '-color_primaries', 'bt709', '-color_trc', 'bt709', '-color_range', 'tv']
        if threads:                      # same cap with NVENC (it bounds the grain / sharpen filter threads, which stay on the CPU)
            cmd += ['-threads', str(threads)]
        cmd += ['-c:a', 'aac', '-b:a', '192k', '-shortest', '-movflags',
                ('+frag_keyframe+empty_moov' if os.environ.get('EDIT_FRAG_MP4') else '+faststart'), out_path]  # EDIT_FRAG_MP4=1: a stopped render still plays
        enc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        n0, n1 = int(round(a0 * fps)), int(round(a1 * fps))
        # decode upcoming shots in the background while this one renders
        q = Queue()

        def worker():
            while True:
                k = q.get()
                if k is None:
                    return
                try:
                    self.prefetch(k)
                except Exception as e:  # a failed prefetch is retried on demand
                    self.warnings.append(f'prefetch {k}: {e}')

        th = threading.Thread(target=worker, daemon=True)
        th.start()
        t_v = time.time()
        next_release = 0
        cur_shot = -1
        for f in range(n0, n1):
            t = f / fps
            j = self.shot_at(t)
            if j != cur_shot:
                cur_shot = j
                if f >= next_release:
                    self.release(t)
                    next_release = f + int(fps)
                self.prefetch(j)
                # EDIT_PREFETCH: shots decoded ahead in the background (default 2). Each prefetched slow-mo take holds
                # ~0.5-1 GB of work-resolution frames; 0 keeps a 9:16 slow-mo render under ~2.5 GB (owner's 3 GB rule)
                for _k in range(1, 1 + int(os.environ.get('EDIT_PREFETCH', '2'))):
                    q.put(j + _k)
            self._now = t
            _sw = self.plan.get('step_windows')
            if _sw:
                # round 6: 'on twos / threes' stutter windows [{t0, t1, n}]: every n-th frame is drawn, the others repeat it (the reference's ember plates)
                for _w in _sw:
                    if float(_w['t0']) - 1e-6 <= t < float(_w['t1']) - 1e-6:
                        _k = int(round((t - float(_w['t0'])) * fps + 1e-6)) % max(1, int(_w.get('n', 2)))
                        if _k:
                            t = t - _k / fps
                        break
            img = self.frame(t)
            if img.shape[1] != W or img.shape[0] != H:
                img = cv2.resize(img, (W, H), interpolation=cv2.INTER_AREA)
            try:
                enc.stdin.write(np.ascontiguousarray(img).tobytes())
            except (BrokenPipeError, OSError):      # the encoder died (Windows raises EINVAL for a dead pipe)
                break
            self.stats['frames'] += 1
            if (f - n0) % int(fps * 5) == 0 and f > n0:
                el = time.time() - t_v
                log(f't={t:6.2f}s  {(f - n0) / el:5.1f} fps')
        q.put(None)
        try:
            enc.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        rc = enc.wait()
        if rc != 0:
            raise SystemExit('ffmpeg encode failed')
        vt = time.time() - t_v
        self.timing['video_s'] = round(vt, 2)
        info = {
            'ok': True, 'path': os.path.abspath(out_path), 'duration': round(a1 - a0, 3), 'fps': fps, 'size': [W, H],
            'frames': self.stats['frames'], 'render_s': round(time.time() - t_start, 2),
            'fps_render': round(self.stats['frames'] / max(1e-6, vt), 2), 'style': self.style['name'], 'look': self.look_name,
            'frame_mode': self.fr.mode, 'shots': len(self.shots), 'preset': self.preset,
            'transitions': [s['transition_in'] for s in self.shots],
            'accents': sum(len(s['_acc']) for s in self.shots),
            'decoded_frames': sum(f.decoded for f in self.footage.values()),
            'audio': ainfo, 'timing': self.timing, 'warnings': self.warnings,
            'clips': list(dict.fromkeys(s.get('source_id') or os.path.basename(s['path']) for s in self.shots)),
        }
        return info


# ---------------------------------------------------------------------------------------------- overlays

def ease_out_back(x, s=1.6):
    x = min(1.0, max(0.0, x)) - 1
    return 1 + (s + 1) * x ** 3 + s * x ** 2


class Overlay:
    """A timed overlay drawn on the canvas (or the output frame with "space": "frame")."""

    def __init__(self, eng, ov):
        self.eng, self.ov = eng, dict(ov)
        self.type = ov.get('type', 'text')
        self.t0 = float(ov.get('t0', 0.0))
        self.t1 = float(ov.get('t1', eng.duration))
        default_space = 'frame' if self.type in ('fade',) else 'canvas'
        if self.type == 'watermark':
            default_space = 'canvas' if (eng.style.get('watermark') or {}).get('rotate_with_master', True) else 'frame'
        self.space = ov.get('space', default_space)
        self.size = eng.out_size if self.space == 'frame' else eng.canvas
        self._sprite = None
        self._sprites = None
        self._pf = None
        self._cache = {}
        self._build()

    def active(self, t):
        return self.t0 <= t < self.t1

    def _px(self, frac):
        w, h = self.size
        return max(10, int(frac * h)) if frac < 1 else int(frac)

    def _build(self):
        o, eng = self.ov, self.eng
        w, h = self.size
        typ = self.type
        if typ in ('title_card', 'text'):
            size = self._px(float(o.get('size', 0.07)))
            style = o.get('style', 'title')
            if o.get('anim') in ('letters_3d', 'slam', 'letters_fly'):
                txt = T.latin_only(o.get('text', ''))
                txt = txt.upper() if T.STYLES.get(style, {}).get('case') == 'upper' else txt
                self._sprites = [T.render(ch, style, size) for ch in txt]
                full = T.render(txt, style, size, max_w=int(w * 0.9))
                k = min(1.0, (w * 0.9) / max(1, sum(s.size[0] - 2 * s.pad for s in self._sprites)))
                if k < 1:
                    self._sprites = [T.render(ch, style, int(size * k)) for ch in txt]
                self._sprite = full
            else:
                self._sprite = T.render(o.get('text', ''), style, size, max_w=int(w * 0.9), fill=tuple(o['color']) if o.get('color') else None)
                if o.get('hi_color'):             # optional: 'type' draws the letters being typed in this colour (S2rz caption)
                    self._sprite_hi = T.render(o.get('text', ''), style, size, max_w=int(w * 0.9), fill=tuple(o['hi_color']))
        elif typ == 'lyrics':
            # round 4: cap 17 % of the canvas height (yUui / PJUNKIE), never wider than 88 %W. "size" is still the
            # font size as a fraction of H for plans that set it; the default is now cap 0.17 / CAP_RATIO.
            self._sprites = [(wd, T.render(wd['text'], o.get('style', 'lyric'), self._px(float(o.get('size', 0.17 / self.CAP_RATIO))),
                                           max_w=int(0.88 * w)))
                             for wd in o.get('words') or []]
        elif typ == 'caption_bar':
            self._sprite = T.render(o.get('text', ''), 'caption_bar', int(0.026 * h / 0.72), max_w=int(w * 0.92))
        elif typ == 'center_label':
            lines = o.get('lines') or [o.get('text', '')]
            self._sprites = [T.render(ln, 'label_red' if o.get('red') else 'label', int(0.024 * h / 0.7), max_w=int(w * 0.9)) for ln in lines]
        elif typ == 'kinetic_name':
            self._sprite = T.render(o.get('text', ''), 'kinetic', int(0.083 * h / 0.72))
        elif typ == 'particles':
            kind = o.get('kind', 'sparkles')
            self._pf = fx.ParticleField(kind, colors=o.get('colors') or ([tuple(c) for c in eng.colors] if kind in ('sparkles', 'embers', 'confetti') else None),
                                        n=int(o.get('n', 50)), seed=int(o.get('seed', 11)), size=self.size)
        elif typ == 'watermark':
            wst = eng.style.get('watermark') or {}
            self._sprite = T.render(o.get('text', ''), wst.get('style', 'watermark'), int(wst.get('size', 0.026) * h / 0.72),
                                    max_w=int(w * 0.6))
            self.wst = wst
        elif typ == 'sticker':
            kind = o.get('kind', 'hearts')
            if kind == 'emoji' and o.get('emoji'):
                self._sprite_img = T.emoji_sprite(o['emoji'], int(float(o.get('size', 0.12)) * w))
        elif typ == 'light_streak' and o.get('text'):
            self._sprite = T.render(o['text'], 'tiny', int(0.025 * h / 0.7))
        elif typ == 'word3d':
            # fx.json word_3d_flythrough: every word is a point in space, the camera reaches it, it grows
            # x1.11 per frame @30 from cap0 to the exit size and leaves by PASSING THE LENS, motion blurred.
            # Up to 3 words are alive at once at different depths and are drawn back to front.
            st = o.get('style', 'hero_fx')
            # cap0 is the CAP HEIGHT of the glyphs as a fraction of the picture height, which is what the
            # reference measures (10.8 % at birth, 25.3 % eight frames later). A Poppins em is ~1/CAP_RATIO
            # of its cap height, so the sprite is rendered at cap0 / CAP_RATIO to land on the measurement.
            cap0 = float(o.get('cap0', 0.108))
            base = self._px(cap0 / self.CAP_RATIO * self.REND)   # rendered big, blitted down: stays crisp
            # round 4 (critic, side by side with f153-158): the halo read as a heavy white box at x2 the reference -
            # sigma 0.31 %W @0.45 + 2.0 %W @0.08 - and the face is a LIGHT rounded sans (weight 300), not medium.
            gl = [(0.0031 * w * self.REND, 0.45, None), (0.020 * w * self.REND, 0.08, None)] if st == 'hero_fx' else None
            fnt = o.get('font') or ('poppins_light' if st == 'hero_fx' else None)
            self._sprites = []
            for i, wd in enumerate(o.get('words') or []):
                self._sprites.append((dict(wd), T.render(wd.get('text', ''), st, base, glow=gl, font=fnt)))
        elif typ == 'ground_text':
            self._sprite = T.render(o.get('text', ''), o.get('style', 'hero_fx'), self._px(float(o.get('size', 0.16))),
                                    glow=[(0.0031 * w, 0.45, None)] if o.get('style', 'hero_fx') == 'hero_fx' else None)

    def apply(self, img, t):
        o, eng = self.ov, self.eng
        typ = self.type
        w, h = img.shape[1], img.shape[0]
        since, left = t - self.t0, self.t1 - t
        fade = min(1.0, left / 0.25) if o.get('fade_out', True) else 1.0
        if typ in LT.HANDLERS:                  # lyric text per preset (core/edit/lyric_text.py)
            return LT.HANDLERS[typ](self, img, t)
        if typ in FXS.HANDLERS:                 # the built set pieces of the fx group live in fxsets.py
            return FXS.HANDLERS[typ](self, img, t)
        if typ in ('title_card', 'text'):
            x, y = float(o.get('x', 0.5)) * w, float(o.get('y', 0.5)) * h
            if o.get('drift'):                  # optional [vx, vy] in frame fractions per second: the card keeps sliding
                x += float(o['drift'][0]) * w * since
                y += float(o['drift'][1]) * h * since
            anim = o.get('anim', 'pop' if typ == 'title_card' else 'fade')
            _wrot = 0.0
            if o.get('wave'):                   # 2026-10-01 text fx: optional keyed wiggle + roll of the whole card
                try:
                    import text_fx as _TFX
                    _dx, _dy, _wrot = _TFX.card_wave(o, since, (w, h))
                    x += _dx
                    y += _dy
                except Exception:  # noqa: BLE001
                    _wrot = 0.0
            base = img
            img = img.copy()
            if o.get('backdrop_blur'):
                # ZkWQ creator card: the plate under the card is thrown out of focus (sigma as a fraction of W, 3-frame ramp)
                img = fx.gauss_px(img, float(o['backdrop_blur']) * w * min(1.0, since / 0.1) * fade)
            if o.get('behind') and not o.get('behind_fixed'):
                # round 6: on a close-up the matte covers the whole word and NOTHING was drawn (the compare strip showed
                # 15 frames without any text). Decided once per overlay: the row nearest the asked y where the subject
                # hides 15-60 % of the word box (the references hide about a third); when every row is > 75 % hidden
                # the word is drawn in FRONT, in the emptiest row, so it is always legible.
                if '_by' not in self._cache:
                    self._cache['_by'] = (y, True)
                    try:
                        import matte as MT
                        m0 = MT.segment(base)
                    except Exception:  # noqa: BLE001
                        m0 = None
                    sp0 = getattr(self, '_sprite', None)
                    if m0 is not None and sp0 is not None:
                        sh, sw = sp0.img.shape[:2]
                        x0, x1 = int(max(0, x - sw / 2)), int(min(w, x + sw / 2))
                        cands = []
                        for fy in (float(o.get('y', 0.5)), 0.30, 0.22, 0.15, 0.40, 0.62, 0.72, 0.82):
                            y0, y1 = int(max(0, fy * h - sh / 2)), int(min(h, fy * h + sh / 2))
                            if y1 - y0 < 4 or x1 - x0 < 4:
                                continue
                            box = m0[y0:y1, x0:x1]
                            q = max(1, box.shape[1] // 4)
                            ends = max(float(box[:, :q].mean()), float(box[:, -q:].mean()))
                            cands.append((fy, float(box.mean()), ends))
                        # legible = the subject hides the MIDDLE of the word (10-50 %) and both ends stay readable
                        good = [c for c in cands if 0.10 <= c[1] <= 0.50 and c[2] <= 0.35]
                        if good:
                            self._cache['_by'] = (good[0][0] * h, True)
                        elif cands:
                            fy, oc = min(cands, key=lambda c: c[1])[:2]
                            self._cache['_by'] = (fy * h, oc < 0.10)      # nothing legible behind: in FRONT, emptiest row
                y, self._behind_on = self._cache['_by']
            if anim == 'pop':
                pulse = 1.0
                if eng.beats:
                    k = int(np.searchsorted(eng.beats, t, side='right')) - 1
                    if k >= 0:
                        pulse = 1 + 0.06 * math.exp(-(t - eng.beats[k]) / 0.12)
                T.blit(img, self._sprite.img, x, y, alpha=fade, scale=max(0.01, ease_out_back(since / 0.3)) * pulse, rot=_wrot)
            elif anim == 'stretch':
                sy = 1.0 + 2.0 * max(0.0, 1 - since / (2 / 30))
                T.blit(img, self._sprite.img, x, y, alpha=min(fade, 1.0), sy=sy, blur_y=int(40 * (sy - 1)))
            elif anim == 'type':
                pos = since * float(o.get('cps', 24))
                n = int(pos)
                cf = float(o.get('char_fade', 0.0))       # seconds; fx.json: 3 frames @30 per character
                if getattr(self, '_sprite_hi', None) is not None:
                    # S2rz caption: the last `hi_n` typed letters (the syllable being spoken) in the highlight colour
                    T.blit(img, self._sprite_hi.crop_letters(n, 1.0), x, y, alpha=fade)
                    T.blit(img, self._sprite.crop_letters(max(0, n - int(o.get('hi_n', 3))), 1.0), x, y, alpha=fade)
                else:
                    T.blit(img, self._sprite.crop_letters(n, min(1.0, (pos - n) / max(1e-6, cf * float(o.get('cps', 24)))) if cf > 0 else 1.0), x, y, alpha=fade)
            elif anim in ('slam', 'letters_3d', 'letters_fly') and self._sprites:
                total = sum(s.size[0] - 2 * s.pad for s in self._sprites)
                cx = x - total / 2
                stag = float(o.get('stagger', 3 / 30.0))
                for i, sp in enumerate(self._sprites):
                    lw = sp.size[0] - 2 * sp.pad
                    age = since - i * (1 / 30 if anim == 'slam' else stag if anim == 'letters_fly' else 3 / 60)
                    if age < 0:
                        cx += lw
                        continue
                    if anim == 'slam':
                        k = age / (5 / 30)
                        sc = 1.12 * min(1.0, k / 0.6) if k < 0.6 else 1.12 - 0.12 * min(1.0, (k - 0.6) / 0.4)
                        T.blit(img, sp.img, cx + lw / 2, y, alpha=fade, scale=max(0.01, sc))
                    elif anim == 'letters_fly':
                        # fx.json letters_fly_in_motion_blurred: each letter starts at 1.35 W, eases to its
                        # slot over 5 frames @30 and is smeared 60-140 px @1920 along its travel for 2 frames
                        k = min(1.0, age / (5 / 30.0))
                        e = 1 - (1 - k) ** 3
                        x0 = 1.35 * w - (cx + lw / 2)
                        off = x0 * (1 - e)
                        k2 = min(1.0, (age + 1.0 / eng.fps) / (5 / 30.0))
                        off2 = x0 * (1 - (1 - (1 - k2) ** 3))
                        T.blit(img, sp.img, cx + lw / 2 + off, y, alpha=fade, blur_len=0.5 * abs(off - off2), blur_ang=0.0)
                    else:
                        rng = np.random.default_rng(i * 31 + 7)
                        k = min(1.0, age / 0.4)
                        e = 1 - (1 - k) ** 3
                        sx0, sy0 = float(rng.uniform(-0.5, 0.5)) * w, float(rng.uniform(-0.4, 0.4)) * h
                        rot0 = float(rng.uniform(60, 120)) * (1 if rng.random() < 0.5 else -1)
                        T.blit(img, sp.img, cx + lw / 2 + sx0 * (1 - e), y + sy0 * (1 - e), alpha=fade * min(1.0, k * 3),
                               scale=2 - e, rot=rot0 * (1 - e))
                    cx += lw
            elif anim == 'shrink_blur':
                # S2rz hook title: arrives big (scale0, 1.35) and shrinks to 1.0 over ~20 frames (ease-out), keeps a slow
                # creep, and leaves with a 4-frame blur-out (x1.15, smear 6 %W, fading)
                k = min(1.0, since / float(o.get('shrink_s', 0.67)))
                s0 = float(o.get('scale0', 1.35))
                sc = s0 + (1 - s0) * (1 - (1 - k) ** 3) + 0.03 * since
                v = max(0.0, 1 - left / float(o.get('out_s', 0.13)))
                sc *= 1 + 0.15 * v
                T.blit(img, self._sprite.img, x, y, alpha=min(1.0, since / 0.05) * (1 - v) ** 1.2, scale=sc,
                       blur_len=0.06 * w * v + 0.02 * w * max(0.0, 1 - since / 0.1), blur_ang=0.0, blur_max=301)
            elif anim == 'none':
                T.blit(img, self._sprite.img, x, y, alpha=float(o.get('opacity', 1.0)))
            elif anim == 'slide':
                k = min(1.0, since / 0.15)
                off = 0.04 * w * (1 - (1 - (1 - k) ** 3))
                if off > 1:
                    for g, op in ((2, 0.15), (1, 0.3)):
                        T.blit(img, self._sprite.img, x + off + g * 0.015 * w, y, alpha=op * fade)
                T.blit(img, self._sprite.img, x + off, y, alpha=min(1.0, since / 0.07) * fade)
            else:   # fade
                T.blit(img, self._sprite.img, x, y, alpha=min(1.0, since / 0.3) * fade * float(o.get('opacity', 1.0)), rot=_wrot)
            if o.get('behind') and getattr(self, '_behind_on', True):
                # text behind the subject: the subject goes back on top. round 4: a temporally smoothed model
                # matte (matte.Temporal, EMA 0.6, 2 px feather) - the per-frame GrabCut flickered letters in and out.
                m = None
                try:
                    import matte as MT
                    if '_tm' not in self._cache:
                        self._cache['_tm'] = MT.Temporal()
                    m = self._cache['_tm'].update(base, t)
                except Exception:  # noqa: BLE001
                    m = None
                if m is None:
                    m = L.subject_matte(base)
                if m is not None:
                    img = fx.blend_mask(img, base, m)
            return img
        if typ == 'lyrics':
            img = img.copy()
            for i, (wd, sp) in enumerate(self._sprites):
                a, b = float(wd['t0']), float(wd.get('t1', wd['t0'] + 0.5))
                if not (a <= t < b):
                    continue
                rng = np.random.default_rng(i * 13 + 5)
                if o.get('legacy'):
                    x = (0.5 + float(rng.uniform(-0.15, 0.15))) * w
                    y = float(rng.uniform(0.45, 0.7)) * h
                else:
                    # the lower-third negative space, or the upper third when the subject sits low
                    try:
                        fo = FXS._subject(self, a)
                    except Exception:  # noqa: BLE001
                        fo = None
                    low = fo is not None and fo[1] > 0.58
                    x = 0.5 * w + float(rng.uniform(-0.03, 0.03)) * w
                    y = (float(rng.uniform(0.20, 0.27)) if low else float(rng.uniform(0.70, 0.78))) * h
                age = t - a
                for k in range(len(sp.letters)):
                    # letter stagger 1 frame @30; each letter types on with a sideways motion-blur TRAIL of
                    # 6 %W that decays over 5 frames (ref 'EVERY': streaks across 40 % of the width)
                    la = age - k * ((5 / 30) / max(1, len(sp.letters)) if o.get('legacy') else 1 / 30.0)
                    if la < 0:
                        break
                    glyph, gx = sp.letter(k)
                    u5 = max(0.0, 1 - la / (5 / 30))
                    slide = (0.04 if o.get('legacy') else 0.06) * w * u5 ** 1.5
                    px = x - sp.size[0] / 2 + gx + glyph.shape[1] / 2 + slide
                    if o.get('legacy'):
                        for g, op in ((2, 0.15), (1, 0.3)):
                            if slide > 1:
                                T.blit(img, glyph, px + g * 0.015 * w, y, alpha=op)
                        T.blit(img, glyph, px, y, alpha=min(1.0, la / (2 / 30)))
                    else:
                        T.blit(img, glyph, px + slide * 0.5, y, alpha=min(1.0, la / (2 / 30)),
                               blur_len=slide if slide > 2 else 0.0, blur_ang=0.0, blur_max=401)
            return img
        if typ == 'caption_bar':
            bar_h = int(0.069 * h)
            a_in, a_out = min(1.0, since / 0.5), min(1.0, left / 0.55)
            y = int(-bar_h + bar_h * (1 - (1 - a_in) ** 2)) if a_in < 1 else int(-bar_h * (1 - a_out) ** 2) if a_out < 1 else 0
            img = img.copy()
            img[max(0, y):max(0, y + bar_h)] = (254, 254, 254)
            T.blit(img, self._sprite.img, w / 2, y + bar_h / 2)
            return img
        if typ == 'center_label':
            img = img.copy()
            y = float(o.get('y', 0.5)) * h - len(self._sprites) * self._sprites[0].size[1] * 0.35
            for sp in self._sprites:
                T.blit(img, sp.img, w / 2, y, alpha=fade)
                y += sp.size[1] * 0.7
            return img
        if typ == 'kinetic_name':
            img = img.copy()
            x0 = -0.25 * w + self._sprite.size[0] / 2
            T.blit(img, self._sprite.img, x0 + 0.17 * w * since, float(o.get('y', 0.86)) * h, alpha=fade)
            return img
        if typ == 'particles':
            burst = 0.0
            if o.get('auto'):
                ph = [p for p in list(eng.phrases) + [eng.drop_t] if p <= t]
                if ph:
                    burst = max(0.0, 1 - (t - max(ph)) / 0.5)
            return fx.add_layer(img, self._pf.layer(t, burst), float(o.get('opacity', 0.5)) * fade)
        if typ == 'ring':
            return fx.screen(img, fx.ring((w, h), min(1.0, since / max(0.1, self.t1 - self.t0))), 0.9)
        if typ == 'light_leak':
            return fx.screen_layer(img, fx.light_leak_layer((w, h), t, seed=float(o.get('seed', 0.3))), float(o.get('opacity', 0.5)) * fade)
        if typ == 'warm_bloom':
            # cinematic_slowmo remake round 6 (_sX2's closing shot: a warm lens light grows from one corner into the
            # fade): a soft warm radial light at (x, y) drifting slowly inward, its opacity growing k^pow over t0..t1
            k = min(1.0, max(0.0, since / max(0.1, self.t1 - self.t0))) ** float(o.get('pow', 1.6))
            dw, dh = max(2, w // 8), max(2, h // 8)
            x0, y0 = float(o.get('x', 0.85)), float(o.get('y', 0.2))
            cx = (x0 + (0.5 - x0) * 0.12 * k) * dw
            cy = (y0 + (0.5 - y0) * 0.12 * k) * dh
            yy, xx = np.mgrid[0:dh, 0:dw].astype(np.float32)
            r = float(o.get('radius', 0.55)) * dw
            g = np.exp(-(((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * r * r)))[..., None]
            col = np.array(o.get('color_bgr', (70, 160, 255)), np.float32)[None, None, :] / 255.0
            return fx.screen_layer(img, g * col, float(o.get('opacity', 0.55)) * k)
        if typ == 'lens_flare':
            x = float(o.get('x', 0.25)) + 0.05 * since
            return fx.screen_layer(img, fx.lens_flare((w, h), x, float(o.get('y', 0.2))), float(o.get('opacity', 0.9)) * fade * min(1.0, since / 0.2))
        if typ == 'light_rays':
            return fx.screen_layer(img, fx.light_rays(img, float(o.get('x', 0.5)), float(o.get('y', 0.1))), float(o.get('opacity', 0.8)) * fade)
        if typ == 'light_streak':
            k = min(1.0, since / 0.4)
            lay = np.zeros((h // 2, w // 2, 3), np.float32)
            p0, p1, p2 = np.array([0.1, 0.7]), np.array([0.5, 0.2]), np.array([0.9, 0.6])
            us = np.linspace(0, k, max(2, int(60 * k)))
            pts = ((1 - us)[:, None] ** 2 * p0 + 2 * (1 - us)[:, None] * us[:, None] * p1 + us[:, None] ** 2 * p2) * [w // 2, h // 2]
            cv2.polylines(lay, [pts.astype(np.int32)], False, (0.54, 0.84, 0.96), 2, cv2.LINE_AA)
            lay = lay + cv2.GaussianBlur(lay, (0, 0), 9) * 1.5
            img = fx.screen_layer(img, lay, fade)
            if self._sprite is not None and k > 0.5:
                img = img.copy()
                T.blit(img, self._sprite.img, w / 2, 0.36 * h, alpha=min(1.0, (k - 0.5) * 2) * fade)
            return img
        if typ == 'word3d':
            return self._word3d(img, t)
        if typ == 'ground_text':
            return self._ground_text(img, t)
        if typ == 'radial_blur':
            # fx.json radial_zoom_push_blur: a SUSTAINED radial smear across a whole pushing shot (not a
            # 2-frame burst), strength = 0.9 x the zoom rate per frame, i.e. ~0.03 at the reference's
            # 1.6-3.0 %/frame push, which smears the frame edge by about 1 % of the width per frame.
            # NOTE the reference's radtan (0.72-0.79 against 1.17 static) is NOT transferable: radtan
            # depends on how radial the scene itself is. A clean anime plate here measures 0.96 with no
            # blur at all, so what is matched is the smear LENGTH, and radtan is judged as a ratio to the
            # unblurred baseline of the same shot.
            # `center` is a FRACTION of the picture (edit_fx normalises pixels too, but do not rely on it).
            # The amount is capped at 0.030 and rendered at full resolution (down=1): the acceptance test is
            # that the smeared frame keeps >= 55 % of the unsmeared plate's Laplacian variance, and a
            # half-resolution pass alone costs ~75 % of it before any smear is applied.
            c = o.get('center') or [0.5, 0.5]
            return fx.zoom_blur_c(img, min(0.030, float(o.get('amount', 0.027))), steps=int(o.get('steps', 12)),
                                  center=(float(c[0]), float(c[1])), down=int(o.get('down', 1)))
        if typ == 'glass_cracks':
            p = float(o.get('progress')) if o.get('progress') is not None else min(1.0, since / max(0.1, 0.4 * (self.t1 - self.t0)))
            key = ('cracks', round(p, 2))
            if key not in self._cache:
                self._cache.clear()
                self._cache[key] = fx.spider_cracks((w, h), float(o.get('x', 0.5)) * w, float(o.get('y', 0.45)) * h, p,
                                                    seed=int(o.get('seed', 7)), sparks=int(o.get('sparks', 5)))
            return fx.screen_layer(img, self._cache[key], float(o.get('opacity', 0.35)) * fade)
        if typ == 'filmstrip':
            if self._sprites is None:
                self._sprites = [cv2.resize(s, (240, 150), interpolation=cv2.INTER_AREA)
                                 for s in eng.card_stills(o, int(o.get('thumbs', 7)))]
            lay, m = fx.filmstrip_layer((w, h), self._sprites, angle_deg=float(o.get('angle', 28.0)),
                                        phase=since * float(o.get('speed', 1.2)), frac=float(o.get('frac', 0.20)))
            return fx.blend_mask(img, lay, m * float(o.get('opacity', 1.0)) * fade)
        if typ == 'ink_blot':
            p = min(1.0, since / max(0.1, float(o.get('grow', 0.5))))
            m = fx.ink_blot_mask((w, h), p, seed=int(o.get('seed', 3)), side=o.get('side', 'top'),
                                 coverage=float(o.get('coverage', 0.35)))
            out = fx.blend_mask(img, np.zeros_like(img), m * float(o.get('darken', 0.62)))
            if self._sprite is None and o.get('text'):
                self._sprite = T.render(o['text'], o.get('style', 'wide_thin'), self._px(float(o.get('size', 0.055))))
            if self._sprite is not None and p > 0.15:
                out = out.copy()
                T.blit(out, self._sprite.img, float(o.get('x', 0.5)) * w, float(o.get('y', 0.3)) * h,
                       alpha=min(1.0, (p - 0.15) / 0.3) * fade)
            return out
        if typ == 'bokeh':
            return fx.add_layer(img, fx.bokeh_layer((w, h), n=int(o.get('n', 57)), seed=int(o.get('seed', 9)), t=t),
                                float(o.get('opacity', 0.75)) * fade)
        if typ == 'polaroids':
            return self._polaroids(img, t, since, fade)
        if typ == 'zoom_blur_hit':
            # the whole-frame zoom blur 2 frames before a title assembly (the impact the 1L7 title lands without)
            at = float(o.get('at', self.t1))
            dt = at - t
            if dt < 0 or dt > float(o.get('pre', 2 / 30.0)):
                return img
            return fx.zoom_blur_c(img, float(o.get('amount', 0.18)) * (dt / max(1e-3, float(o.get('pre', 2 / 30.0)))))
        if typ == 'letterbox':
            frac = float(o.get('frac', (eng.style.get('overlays') or {}).get('letterbox') or 0.09))
            k = min(1.0, since / (6 / 30)) * min(1.0, left / (6 / 30))
            return fx.letterbox_bars(img, frac * k)
        if typ == 'fade':
            k = min(1.0, max(0.0, since - float(o.get('hold', 0.0))) / max(1e-3, self.t1 - self.t0 - float(o.get('hold', 0.0))))
            a = k if o.get('dir', 'out') == 'out' else 1 - k
            if o.get('defocus'):
                img = fx.gauss_px(img, 0.01 * w * a)
            return fx.fill_mix(img, (255, 255, 255) if o.get('color') == 'white' else (0, 0, 0), a)
        if typ == 'bw_selective':
            key = 'hue'
            if key not in self._cache:
                self._cache[key] = fx.dominant_hue(img)
            return fx.bw_selective(img, self._cache[key])
        if typ == 'desaturate':
            return fx.saturation_u8(img, 1 - float(o.get('amount', 0.3)))
        if typ == 'flat':
            return L.flat_look(img)
        if typ == 'colour_cycle':
            k = int(since / (eng.P / 2)) % 3
            col = [(60, 60, 255), (60, 255, 60), (255, 80, 60)][k]
            return fx.fill_mix(fx.hue_rotate(img, 120 * k), col, 0.25)
        if typ == 'beat_light_pulse':
            k = int(np.searchsorted(eng.beats, t, side='right')) - 1 if eng.beats else -1
            g = 0.6 * math.exp(-(t - eng.beats[k]) / 0.2) if k >= 0 else 0.0
            key = 'rad'
            if key not in self._cache:
                yy, xx = np.mgrid[0:h // 8, 0:w // 8].astype(np.float32)
                r = np.sqrt(((xx - w / 16) / (w / 16)) ** 2 + ((yy - h / 16) / (h / 16)) ** 2)
                self._cache[key] = np.clip(1 - r, 0, 1)[..., None].repeat(3, 2)
            return fx.screen_layer(img, self._cache[key], 0.35 + g)
        if typ == 'glow_pulse':
            dt = since
            kk = 0.8 * math.sin(math.pi * min(1.0, dt / 0.15)) if dt < 0.075 else 0.3 + 0.5 * max(0.0, 1 - (dt - 0.075) / 0.2)
            return fx.bloom_boost(img, kk)
        if typ == 'echo':
            ghost = fx.warp(img, 1.35, 0, 0, -0.15 * h, (w, h))
            return fx.mix_u8(img, ghost, 0.35 * fade)
        if typ == 'sticker':
            return self._sticker(img, t, since, fade)
        if typ == 'watermark':
            wst = self.wst
            img = img.copy()
            x, y = wst.get('pos', (0.93, 0.955))
            anchor = wst.get('anchor', 'right')
            sp = self._sprite
            if anchor == 'vertical_left':
                T.blit(img, sp.img, x * w, y * h, alpha=wst.get('opacity', 0.5), rot=90)
            elif anchor == 'right':
                T.blit(img, sp.img, x * w - sp.size[0] / 2, y * h, alpha=wst.get('opacity', 0.5))
            else:
                T.blit(img, sp.img, x * w, y * h, alpha=wst.get('opacity', 0.5))
            return img
        return img

    REND = 3.0          # words are rendered 3x their entry cap so the fly-through upscales at most ~1.7x
    CAP_RATIO = 0.667   # measured cap height / font size for the hero face (calibrated on the render)

    def _word3d(self, img, t):
        """
        The core of the fx style: each word is a point in space at its own depth. The camera moves at a
        constant speed, so scale and screen offset both grow x1.11 per frame @30 (fx.json: cap height
        10.8 -> 25.3 % of the picture over 8 frames, x2.34); the word exits by PASSING THE LENS, motion
        blurred, and never fades. Up to 3 words are alive at once and are drawn back to front.
        """
        o, w, h = self.ov, img.shape[1], img.shape[0]
        G = float(o.get('growth', 1.11))
        cap0 = float(o.get('cap0', 0.108))
        exit_cap = float(o.get('exit_cap', 0.55))
        vertical = bool(o.get('vertical', h > w))
        step = 30.0 / max(1.0, self.eng.fps)          # frames @30 per output frame
        live = []
        for i, (wd, sp) in enumerate(self._sprites or []):
            k = (t - float(wd['t0'])) * 30.0
            if k < 0:
                continue
            g = G ** k
            if cap0 * g > exit_cap * 1.4:
                continue
            live.append((g, i, wd, sp, k))
        if not live:
            return img
        live.sort(key=lambda r: r[0])                 # far (small) first: the near word occludes it
        img = img.copy()
        for g, i, wd, sp, k in live[-int(o.get('max_alive', 3)):]:
            rng = np.random.default_rng(int(wd.get('seed', i * 37 + 11)))
            sx = float(wd['x']) if 'x' in wd else 0.5 + float(rng.uniform(-0.10, 0.10) if vertical else rng.uniform(-0.18, 0.18))
            sy = float(wd['y']) if 'y' in wd else 0.5 + float(rng.uniform(-0.22, 0.22) if vertical else rng.uniform(-0.12, 0.12))
            x, y = 0.5 * w + (sx - 0.5) * w * g, 0.5 * h + (sy - 0.5) * h * g
            g2 = G ** (k + step)
            dx, dy = (sx - 0.5) * w * (g2 - g), (sy - 0.5) * h * (g2 - g)
            cap_px = cap0 / self.CAP_RATIO * g * h
            blur = 0.5 * (math.hypot(dx, dy) + 0.5 * cap_px * (g2 / g - 1))
            ang = math.degrees(math.atan2(dy, dx)) if (abs(dx) + abs(dy)) > 0.5 else 0.0
            T.blit(img, sp.img, x, y, alpha=1.0, scale=g / self.REND, blur_len=blur, blur_ang=ang)
        return img

    def _ground_text(self, img, t):
        """
        fx.json ground_plane_3d_text: the word lies FLAT on the ground plane of the plate and the camera
        dollies over it until it exits under the bottom edge. It carries exactly the plate's radial blur
        (radtan 0.79) - that is what makes it sit in the scene instead of floating on it - plus a contact
        shadow where it meets the ground.
        """
        o, w, h = self.ov, img.shape[1], img.shape[0]
        sp = self._sprite
        if sp is None:
            return img
        u = min(1.0, max(0.0, (t - self.t0) / max(0.2, self.t1 - self.t0)))
        grow = float(o.get('grow', 5.2))                    # cap 6 % -> 31 % of the picture
        s = 1.0 + (grow - 1.0) * u * u
        cx = float(o.get('x', 0.5)) * w
        cy = (float(o.get('y', 0.62)) + float(o.get('travel', 0.55)) * u * u) * h
        pw_, ph_ = sp.size[0] * s * float(o.get('width', 0.85)) * w / max(1, sp.size[0]), sp.size[1] * s * w / max(1, sp.size[0]) * float(o.get('width', 0.85))
        quad = fx.card_quad((w, h), cx, cy, pw_, ph_, rx=float(o.get('tilt', 74.0)), rz=float(o.get('roll', 0.0)))
        M = cv2.getPerspectiveTransform(np.array([[0, 0], [sp.size[0], 0], [sp.size[0], sp.size[1]], [0, sp.size[1]]], np.float32),
                                        quad.astype(np.float32))
        lay = cv2.warpPerspective(sp.img, M, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
        m = lay[..., 3].astype(np.float32) / 255.0 * float(o.get('opacity', 0.92))
        if m.max() < 0.01:
            return img
        bl = float(o.get('plate_blur', 0.027))
        if bl > 0.001:                                      # the same radial smear the plate carries
            lay = fx.zoom_blur_c(lay[..., :3], bl, steps=6)
            m = fx.zoom_blur_c(cv2.cvtColor((m * 255).astype(np.uint8), cv2.COLOR_GRAY2BGR), bl, steps=6)[..., 0].astype(np.float32) / 255.0
        else:
            lay = lay[..., :3]
        out = fx.blend_mask(img, np.zeros_like(img), cv2.GaussianBlur(m, (0, 0), max(1.0, 0.006 * w)) * 0.35)
        return fx.blend_mask(out, lay, m)

    def _polaroids(self, img, t, since, fade):
        """fx.json polaroid_cards_3d: 3 bordered photos tilted 10-30 deg about their own Y, arriving 4-6 frames apart."""
        o, w, h = self.ov, img.shape[1], img.shape[0]
        n = int(o.get('n', 3))
        if self._sprites is None:
            self._sprites = [fx.polaroid_card(s) for s in self.eng.card_stills(o, n)]
        out = img
        for i, card in enumerate(self._sprites[:n]):
            age = since - i * float(o.get('stagger', 5 / 30.0))
            if age < 0:
                continue
            e = min(1.0, age / 0.25)
            e = 1 - (1 - e) ** 3
            rng = np.random.default_rng(i * 13 + int(o.get('seed', 5)))
            ry = float(rng.uniform(10, 30)) * (1 if i % 2 else -1)
            cw = float(o.get('size', 0.34)) * h * (1 - 0.06 * max(0.0, since - float(o.get('recede_at', 9e9))))
            cx = (0.30 + 0.20 * i) * w + (1.5 - 1.5 * e) * w
            cy = (0.42 + 0.06 * (i % 2)) * h
            quad = fx.card_quad((w, h), cx, cy, cw, cw * card.shape[0] / max(1, card.shape[1]), ry=ry, rz=float(rng.uniform(-8, 8)))
            lay, m = fx.warp_to_quad(card, quad, (w, h))
            if e < 1.0:
                lay = fx.line_blur(lay, 0.10 * w * (1 - e), 0.0)
            out = fx.shadow_under(out, m, 0.02 * w, (0.006 * w, 0.012 * w), 0.4)
            out = fx.blend_mask(out, lay, m * fade)
        return out

    def _sticker(self, img, t, since, fade):
        o = self.ov
        w, h = img.shape[1], img.shape[0]
        x, y = float(o.get('x', 0.5)) * w, float(o.get('y', 0.4)) * h
        size = float(o.get('size', 0.12)) * w
        pop = min(1.0, 0.6 + 0.4 * since / (3 / 60))
        kind = o.get('kind', 'hearts')
        if kind == 'emoji' and getattr(self, '_sprite_img', None) is not None:
            img = img.copy()
            T.blit(img, self._sprite_img, x, y, alpha=fade, scale=pop)
            return img
        if kind == 'lightning':
            if int(since * 30) % 2:
                return img
            return fx.screen_layer(img, fx.lightning((w, h), (x / w, y / h), angle=float(o.get('angle', 20)), seed=int(since * 15)), fade)
        if kind == 'sparkles':
            if self._pf is None:
                self._pf = fx.ParticleField('sparkles', n=14, seed=5, size=(w, h))
            return fx.screen_layer(img, self._pf.layer(t, 0.5), fade)
        if kind == 'heart_frame':
            return fx.screen_layer(img, fx.heart_outline((w, h), x, y, size * pop, thick=4), fade)
        # outline hearts around the point
        lay = np.zeros((h // 2, w // 2, 3), np.float32)
        for k in range(3):
            a = k * 2.1 + 0.4
            cx, cy = x + math.cos(a) * size * 0.8, y + math.sin(a) * size * 0.6
            pts = fx.heart_poly(60) * (size * 0.3 * pop / 2) + np.array([cx / 2, cy / 2], np.float32)
            cv2.polylines(lay, [np.round(pts * 16).astype(np.int32)], True, (1, 1, 1), 2, cv2.LINE_AA, shift=4)
        lay = lay + cv2.GaussianBlur(lay, (0, 0), 4) * 1.2
        return fx.screen_layer(img, lay, fade)


# ---------------------------------------------------------------------------------------------- cli

def render_plan(plan, out, preview=False, work=None, **kw):
    """Python entry: plan (dict or path) -> render info dict."""
    plan = load_plan(plan)
    own = work is None
    work = work or tempfile.mkdtemp(prefix='edit-engine-')
    try:
        eng = Engine(plan, work, preview=preview, fps=kw.pop('fps', None))
        return eng.render(out, **kw)
    finally:
        if own:
            shutil.rmtree(work, ignore_errors=True)


def limit_cpus(n):
    try:
        import psutil
        p = psutil.Process()
        cpus = list(range(min(n, os.cpu_count() or n)))
        p.cpu_affinity(cpus)
    except Exception:
        try:
            os.sched_setaffinity(0, set(range(n)))
        except Exception:
            pass
    cv2.setNumThreads(n)


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    ap = argparse.ArgumentParser(description='Render an edit plan (plan.json) to video.')
    ap.add_argument('--plan', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--preview', action='store_true', help='half resolution, fast encode')
    ap.add_argument('--fps', type=float, default=None, help='override the plan fps')
    ap.add_argument('--start', type=float, default=None)
    ap.add_argument('--end', type=float, default=None)
    ap.add_argument('--preset', default=None, help='x264 preset (default slow; veryfast with --preview)')
    ap.add_argument('--crf', type=int, default=None)
    ap.add_argument('--cpus', type=int, default=None, help='limit to N cores (benchmark the 2-core runner)')
    ap.add_argument('--dump', default=None, help='write PNG frames at --times into this folder instead of a video')
    ap.add_argument('--times', default='', help='comma-separated times for --dump, or a:b:step')
    ap.add_argument('--work', default=None)
    args = ap.parse_args()
    if args.cpus:
        limit_cpus(args.cpus)
    times = None
    if args.times:
        if ':' in args.times:
            a, b, st = [float(v) for v in args.times.split(':')]
            times = list(np.arange(a, b, st))
        else:
            times = [float(v) for v in args.times.split(',') if v.strip()]
    info = render_plan(args.plan, args.out, preview=args.preview, work=args.work, fps=args.fps,
                       preset=args.preset or ('veryfast' if args.preview else 'auto'),
                       crf=args.crf or (22 if args.preview else 16), start=args.start, end=args.end, dump=args.dump, times=times,
                       threads=args.cpus)
    print(json.dumps(info, ensure_ascii=False))



def _bus_comp(x, sr, thresh_db=-18.0, ratio=2.0, attack=0.010, release=0.150, knee_db=6.0):
    """Light stereo-linked bus compression (plan audio.bus_comp, remake round 4 of cinematic_slowmo: the references
    are dense masters, LRA ~2 LU): RMS-ish envelope, soft knee, gain applied to both channels; level is restored by
    the loudness master that follows."""
    import scipy.signal as _sg
    x = np.asarray(x, dtype=np.float64)
    m = np.sqrt(np.mean(x ** 2, axis=0)) if x.ndim == 2 else np.abs(x)
    env = _sg.lfilter([1 - math.exp(-1.0 / (sr * 0.005))], [1, -math.exp(-1.0 / (sr * 0.005))], m)
    ga, gr = math.exp(-1.0 / (sr * attack)), math.exp(-1.0 / (sr * release))
    lv = 20 * np.log10(np.maximum(env, 1e-9))
    over = lv - thresh_db
    red = np.where(over <= -knee_db / 2, 0.0, np.where(over >= knee_db / 2, over * (1 - 1 / ratio),
                   (1 - 1 / ratio) * (over + knee_db / 2) ** 2 / (2 * knee_db)))
    # attack / release smoothing of the gain reduction (one-pole, faster when reduction grows)
    out = np.empty_like(red)
    g = 0.0
    for i in range(0, len(red), 64):
        r = float(red[i:i + 64].max())
        k = ga ** 64 if r > g else gr ** 64
        g = k * g + (1 - k) * r
        out[i:i + 64] = g
    return x * (10 ** (-out / 20.0))


if __name__ == '__main__':
    main()
