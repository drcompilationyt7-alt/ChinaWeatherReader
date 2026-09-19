#!/usr/bin/env python3
"""
Layered meme edit for "<song> acapella" shorts (Zero Yen Otaku).

Every acapella layer brings in one new tile with one clip whose vibe matches
the layer, and every clip keeps looping once it is on screen:

  vocals      tile 1: a dance clip, full screen
  + bass      tile 2: a cool / aura clip        (2 tiles)
  + beatbox   tile 3: a fight / action clip     (3 tiles)
  + harmony   tile 4: a cute clip               (2x2)

- A clip plays for its section and then loops with the same length (whole
  bars), so the viewer has already seen the old tiles while the new one is fresh.
- Per-stem sync: every motion hit in a clip (punch, dance move) is matched to
  the nearest onset of that tile's own stem (dance <-> vocal, cool <-> bass
  notes, fight <-> beatbox hits, cute <-> harmony) and the clip is time-warped
  between hits (0.8-1.25x speed) so they land together. The loop is a whole
  number of bars, so the sync holds every time round.
- A quick glow marks only the tile that has just appeared; the big layer label
  pops up on top; all tiles bounce together with the beat (harder on downbeats).
- Clips are shown whole: fitted over a blurred copy of themselves, never cropped.

Then the drop (timeline "drop", from acapella.py --drop): the original song
comes in where the stack stopped and the short turns into a full-screen
AMV-style edit of the same anime / group (manifest "edit" shots):
- a white flash and a zoom punch on the drop, a shot held for the first bar,
  then cuts on the beat (every 2 beats, every beat as a stutter into each new
  phrase, holds where the song is quiet)
- shots of the same song (K-pop stages, the anime's opening) play in sync
  with it; the others get a velocity ramp (fast into each beat, slow after)
  or, when they carry a clean impact sound, play at speed with that sound
  mixed on the beat ("sound edit")
- beat pumps, a shake on phrase starts, RGB split and a flash on downbeats,
  graded, sharpened and vignetted like the 4K edits it copies.

Inputs
  --audio      acapella mix from core/meme/acapella.py
  --timeline   its timeline.json (beats, downbeats, sections, onsets per layer)
  --clips      clips.json from core/meme/clip_finder.py (vibe, peak, hook-ranked)
  --title      top caption, e.g. "yara yara acapella"
  --subtitle   smaller line under it: the anime / group every clip is from

    python core/meme/render_meme.py --audio acapella.wav --timeline timeline.json \
        --clips clips.json --title "yara yara acapella" --out meme.mp4
"""
import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile

import math

import cv2
import numpy as np
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'quiz'))
from render_quiz import text_layer, pill, put, with_shadow, clamp, ease_out_back, ease_out_cubic  # noqa: E402
import quiz_emoji  # noqa: E402
import acapella as audio_io  # noqa: E402  (same folder: wav io and the limiter)
import edit_fx as fx  # noqa: E402

W, H, FPS = 1080, 1920, 60
AFPS = 30  # motion analysis rate
LAYERS = ['vocal', 'bass', 'beatbox', 'harmony']   # the older synthesised build-up (timelines without layer names)
# clip vibe per stem: the singer dances, drums hit, bass struts, the melody is the cute one
VIBE_OF = {'vocal': 'dance', 'vocals': 'dance', 'drums': 'fight', 'kick': 'fight', 'beatbox': 'fight', 'bass': 'cool', 'guitar': 'cool',
           'harmony': 'cute', 'piano': 'cute', 'other': 'cute'}
VIBE = VIBE_OF
LAYER_LABEL = {'vocal': ('VOCALS', '🎤'), 'vocals': ('VOCALS', '🎤'), 'drums': ('DRUMS', '🥁'), 'kick': ('KICK', '🥁'), 'beatbox': ('BEATBOX', '🥁'),
               'bass': ('BASS', '🔊'), 'guitar': ('GUITAR', '🎸'), 'piano': ('PIANO', '🎹'), 'harmony': ('HARMONY', '🎶'),
               'other': ('MELODY', '🎶')}
LAYOUT = {
    1: [(0, 0, W, H)],
    2: [(0, 0, W, H // 2), (0, H // 2, W, H // 2)],
    3: [(0, 0, W, H // 2), (0, H // 2, W // 2, H // 2), (W // 2, H // 2, W // 2, H // 2)],
    4: [(0, 0, W // 2, H // 2), (W // 2, 0, W // 2, H // 2), (0, H // 2, W // 2, H // 2), (W // 2, H // 2, W // 2, H // 2)],
}
GLOW = (255, 214, 0)
GLOW_SEC = 0.5
EDIT_LABEL = ('FULL SONG', '🔊')


def probe_duration(path):
    r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', path],
                       capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 3.0


class ClipFrames:
    """
    All frames of one clip at one tile size, pre-extracted as JPEGs so the
    editor can show any moment (time-warping for the stem sync). The WHOLE
    picture is visible: fitted over a blurred, darkened copy of itself;
    letterbox bars (content_box from clip_finder) are trimmed first.
    """

    def __init__(self, clip, w, h, workdir, fill=False, cx=0.5):
        x0, y0, x1, y1 = clip.get('content_box') or [0, 0, 1, 1]
        # burned-in subtitles / credits: cut the text band off the bottom (owner: no subtitles, ever)
        if 'text_band' not in clip:
            clip['text_band'] = fx.text_band(clip['path'])
        if clip['text_band']:
            y1 = min(y1, 1 - clip['text_band'])
        trim = f'crop=iw*{x1 - x0:.4f}:ih*{y1 - y0:.4f}:iw*{x0:.4f}:ih*{y0:.4f},' if (x1 - x0) * (y1 - y0) < 0.97 else ''
        if fill:
            # the edit: fill the screen around where the action is, graded and sharpened like a 4K edit
            # drop the bottom band first: burned-in subtitles live there and the crop would cut them mid-word
            vf = (f'[0:v]{trim}crop=iw:ih*0.86:0:ih*0.02,scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,'
                  f'crop={w}:{h}:max(0\\,min(iw-{w}\\,{cx:.3f}*iw-{w // 2})):(ih-{h})/2,'
                  f'eq=contrast=1.10:saturation=1.30:brightness=0.01,unsharp=5:5:0.8:5:5:0.0,fps={FPS}')
        else:
            vf = (f'[0:v]{trim}split[a][b];'
                  f'[a]scale={w // 4}:{h // 4}:force_original_aspect_ratio=increase,crop={w // 4}:{h // 4},'
                  f'boxblur=6:2,eq=brightness=-0.18:saturation=1.2,scale={w}:{h}[bg];'
                  f'[b]scale={w}:{h}:force_original_aspect_ratio=decrease[fg];'
                  f'[bg][fg]overlay=(W-w)/2:(H-h)/2,fps={FPS}')
        self.dir = tempfile.mkdtemp(prefix=f'tile{w}x{h}-', dir=workdir)
        subprocess.run(['ffmpeg', '-v', 'error', '-i', clip['path'], '-an', '-filter_complex', vf, '-q:v', '3',
                        os.path.join(self.dir, '%05d.jpg')], check=False)
        self.paths = sorted(glob.glob(os.path.join(self.dir, '*.jpg')))
        self.size = (w, h)
        self._cache = (None, None)

    def frame(self, clip_t):
        if not self.paths:
            return Image.new('RGB', self.size, (0, 0, 0))
        k = int(round(clip_t * FPS)) % len(self.paths)  # past the end: loop the clip
        if self._cache[0] != k:
            self._cache = (k, Image.open(self.paths[k]).convert('RGB'))
        return self._cache[1]


def motion_hits(clip):
    """Times of the clip's strongest motion hits (frame-difference peaks), unless clip_finder gave them."""
    if clip.get('hits'):
        return sorted(float(h) for h in clip['hits'])
    r = subprocess.run(['ffmpeg', '-v', 'error', '-i', clip['path'], '-an', '-vf', f'fps={AFPS},scale=64:114,format=gray',
                        '-f', 'rawvideo', '-'], capture_output=True)
    a = np.frombuffer(r.stdout, dtype=np.uint8)
    if a.size < 64 * 114 * 3:
        return []
    f = a[:a.size // (64 * 114) * 64 * 114].reshape(-1, 114 * 64).astype(np.float32)
    energy = np.abs(np.diff(f, axis=0)).mean(axis=1)
    energy = np.convolve(energy, np.ones(3) / 3, mode='same')
    thr = energy.mean() + 0.6 * energy.std()
    hits, last = [], -1e9
    for k in range(1, len(energy) - 1):
        if energy[k] >= thr and energy[k] >= energy[k - 1] and energy[k] >= energy[k + 1] and k - last >= 0.25 * AFPS:
            hits.append((k + 1) / AFPS)
            last = k
    return hits


def time_map(period, offset, hits, onsets, lo=0.8, hi=1.25):
    """
    Anchors (output time within the loop -> clip time). Each hit after the
    start is pinned to the nearest stem onset reachable at 0.8-1.25x speed;
    in between the clip plays linearly, after the last hit at normal speed.
    """
    anchors = [(0.0, offset)]
    for h in hits:
        u0, c0 = anchors[-1]
        if h <= c0 + 0.12:
            continue
        natural = u0 + (h - c0)
        if natural > period - 0.15:
            break
        cands = [u for u in onsets if u0 + 0.12 < u < period - 0.1 and lo <= (h - c0) / (u - u0) <= hi]
        if cands:
            anchors.append((min(cands, key=lambda u: abs(u - natural)), h))
    u_last, c_last = anchors[-1]
    anchors.append((period, c_last + (period - u_last)))
    return anchors


def focus_x(clip):
    """Where the action sits across the frame (0..1): the densest detail, pulled towards the centre."""
    x0, y0, x1, y1 = clip.get('content_box') or [0, 0, 1, 1]
    r = subprocess.run(['ffmpeg', '-v', 'error', '-i', clip['path'], '-an', '-vf', 'fps=2,scale=192:108,format=gray',
                        '-f', 'rawvideo', '-'], capture_output=True)
    a = np.frombuffer(r.stdout, dtype=np.uint8)
    if a.size < 192 * 108:
        return 0.5
    g = a[:a.size // (192 * 108) * 192 * 108].reshape(-1, 108, 192).astype(np.float32)
    g = g[:, int(y0 * 108):max(int(y0 * 108) + 2, int(y1 * 108)), int(x0 * 192):max(int(x0 * 192) + 2, int(x1 * 192))]
    n = g.shape[2]
    col = (np.abs(np.diff(g, axis=2))[:, :-1, :] + np.abs(np.diff(g, axis=1))[:, :, :-1]).mean(axis=(0, 1))
    x = (np.arange(len(col)) + 0.5) / len(col)
    col = col * np.exp(-0.5 * ((x - 0.5) / 0.28) ** 2)
    ar = (x1 - x0) * 16 / max(1e-3, (y1 - y0) * 9)  # clip aspect over a 16:9 frame
    cw = min(1.0, (9 / 16) / max(1e-3, ar * 9 / 16 * 16 / 9))  # share of the width a 9:16 crop keeps
    k = max(1, int(round(cw * len(col))))
    if k >= len(col):
        return 0.5
    win = np.convolve(col, np.ones(k), mode='valid')
    c = (int(np.argmax(win)) + k / 2) / n
    return float(np.clip(0.5 + 0.8 * (c - 0.5), 0.0, 1.0))


def clip_audio(path):
    """The clip's own sound as (2, n) float at the mix rate, or None."""
    r = subprocess.run(['ffmpeg', '-v', 'error', '-i', path, '-vn', '-ac', '2', '-ar', str(audio_io.SR), '-f', 'f32le', '-'],
                       capture_output=True)
    a = np.frombuffer(r.stdout, dtype=np.float32)
    return a.reshape(-1, 2).T.astype(np.float64) if a.size > audio_io.SR // 4 else None


def _vel_raw(x, lo=0.45, hi=2.2, tau=0.14):
    # speed lo + (hi - lo) * (e^(-x/tau) + e^(-(1-x)/tau)), integrated: ~3x on the hit, ~0.3x slow-mo between
    return lo * x + (hi - lo) * tau * ((1 - np.exp(-x / tau)) + (np.exp(-(1 - x) / tau) - np.exp(-1 / tau)))


def velocity(x):
    """Clip progress through one beat of a velocity edit (0..1 -> 0..1, mean speed 1)."""
    return _vel_raw(x) / _vel_raw(1.0)


def edit_shots(drop, pool):
    """
    The cut list for the drop: a held first bar, then a shot every 2 beats
    (every 4 where the song is quiet, every beat as a stutter into each new
    4-bar phrase, the second of each stutter pair a punch-in on the same
    shot). Shots of the same song play in sync; the rest start on a motion
    hit (velocity ramp) or on an impact sound (sound edit, at speed).
    """
    beats = drop['beats']
    P = 60.0 / drop['bpm']
    energy = drop.get('energy') or []
    nb = len(beats) - 1
    step = 2 if 2 * P >= 0.55 else 4
    cuts, k = [0], (4 if nb >= 8 else 2)
    while k < nb:
        cuts.append(k)
        bar = k // 4
        e = float(np.mean(energy[bar * 4:bar * 4 + 4])) if energy[bar * 4:bar * 4 + 4] else 1.0
        if bar % 4 == 3 and P >= 0.24:
            k += 1
        elif e < 0.55:
            k = (k // 4 + 1) * 4
        else:
            k += step
    cuts = sorted(set(c for c in cuts if c < nb)) + [nb]

    aligned = sorted([c for c in pool if c.get('aligned')], key=lambda c: -(c.get('align_score') or 0))
    others = [c for c in pool if not c.get('aligned')] or aligned
    if len(aligned) >= 2:
        # stage mix: switch between synced performances of the song every bar (every 2 beats into a phrase)
        cuts = [0]
        k = 4
        while k < nb:
            cuts.append(k)
            k += 2 if (k // 4) % 4 == 3 else 4
        cuts = sorted(set(c for c in cuts if c < nb)) + [nb]
    cursor = {id(c): 0 for c in pool}
    shots, prev, ai, oi = [], None, 0, 0
    for j in range(len(cuts) - 1):
        k0, k1 = cuts[j], cuts[j + 1]
        t0, t1 = float(beats[k0]), float(beats[k1])
        stutter = k1 - k0 == 1
        if stutter and shots and shots[-1].get('stutter') and shots[-1].get('pair') == 1:
            s = dict(shots[-1], t0=t0, t1=t1, zoom=1.32, pair=2, sfx=None)  # punch-in on the same shot
            if s['mode'] != 'aligned':
                s['c0'] = s['c0'] + (shots[-1]['t1'] - shots[-1]['t0'])
            shots.append(s)
            continue
        if aligned and (j == 0 or len(aligned) >= 2 or (j % 2 == 0 and not stutter)):
            clip = aligned[ai % len(aligned)]
            ai += 1
            off = t0 - drop['start'] + drop['source_start'] - float(clip['song_offset'])
            dur = float(clip.get('duration') or 0)
            if 0 <= off and off + (t1 - t0) <= dur + 0.05:
                shots.append({'t0': t0, 't1': t1, 'clip': clip, 'mode': 'aligned', 'c0': off, 'zoom': 1.0,
                              'stutter': stutter, 'pair': 1})
                prev = clip
                continue
        recent_clips = [sh['clip'] for sh in shots[-3:]]
        cands = [c for c in others if c not in recent_clips] or [c for c in others if c is not prev] or others
        clip = cands[oi % len(cands)]
        oi += 1
        dur = float(clip.get('duration') or probe_duration(clip['path']))
        need = t1 - t0
        sfx = [h for h in (clip.get('sfx_hits') or []) if h >= 0.02]
        use_sfx = clip.get('has_audio') and (clip.get('sfx_score') or 0) >= 0.45 and sfx
        hits = sorted(clip.get('_hits') or [])
        if use_sfx:
            h = sfx[cursor[id(clip)] % len(sfx)]
            c0, mode = h - 0.02, 'sfx'
        elif hits:
            h = hits[cursor[id(clip)] % len(hits)]
            c0, mode = h - 0.06, 'velocity'
        else:
            c0, mode = (cursor[id(clip)] * 1.3) % max(0.1, dur - need), 'velocity'
        cursor[id(clip)] += 1
        c0 = float(np.clip(c0, 0.0, max(0.0, dur - need - 0.03)))
        shots.append({'t0': t0, 't1': t1, 'clip': clip, 'mode': mode, 'c0': c0, 'zoom': 1.0, 'stutter': stutter,
                      'pair': 1, 'sfx': (c0, min(need, 0.9)) if mode == 'sfx' else None})
        prev = clip
    return shots


def shot_time(shot, t, P):
    """Clip time shown at output time t inside a shot (never before the shot's first frame)."""
    u = max(0.0, t - shot['t0'])
    if shot['mode'] in ('aligned', 'sfx'):
        return shot['c0'] + u
    n, x = divmod(u / P, 1.0)
    return shot['c0'] + P * (n + velocity(x))


def mix_sfx(song_path, shots, out_path):
    """The song with each sound-edit shot's own impact mixed in on its cut (music ducked a touch under it)."""
    song = audio_io.load_audio(song_path)
    SR = audio_io.SR
    n_sfx = 0
    cache = {}
    for s in shots:
        if not s.get('sfx'):
            continue
        path = s['clip']['path']
        if path not in cache:
            cache[path] = clip_audio(path)
        a = cache[path]
        if a is None:
            continue
        c0, L = s['sfx']
        seg = a[:, int(c0 * SR):int((c0 + L) * SR)].copy()
        if seg.shape[1] < SR // 20:
            continue
        m = seg.shape[1]
        tt = np.arange(m) / SR
        seg *= np.clip(tt / 0.005, 0, 1) * np.clip((m / SR - tt) / 0.15, 0, 1)
        pk = np.abs(seg).max()
        if pk < 1e-4:
            continue
        seg *= 0.5 / pk
        i0 = int(s['t0'] * SR)
        m = min(m, song.shape[1] - i0)
        if m <= 0:
            continue
        duck = 1 - 0.25 * np.clip(1 - tt[:m] / 0.3, 0, 1)
        song[:, i0:i0 + m] = song[:, i0:i0 + m] * duck + seg[:, :m]
        n_sfx += 1
    ceil = 10 ** (-1.5 / 20)
    song = audio_io.limit(song, ceil)
    tp = audio_io.true_peak(song)
    if tp > ceil:
        song *= ceil / tp
    audio_io.write_wav(out_path, song)
    return n_sfx


class Edit:
    """
    The drop: a full-screen beat-cut edit of the source's shots over the original song, at 60 fps.
    Shots are graded and sharpened once (edit_fx.prepare_shot); every frame then gets a sub-pixel
    camera (push-in, beat punches, shakes, whips) with motion blur, transitions, the palette
    gradient, grain and vignette, and the anime's name as a glowing title card when the song drops.
    """

    def __init__(self, drop, pool, workdir, layout='letterbox', look='cinematic', title=''):
        self.drop = drop
        self.P = 60.0 / drop['bpm']
        self.layout, self.look = layout, look
        for c in pool:
            c['_hits'] = motion_hits(c)
        self.shots = edit_shots(drop, pool)
        self.beats = drop['beats']
        self.downbeats = drop['downbeats']
        self.phrases = drop['downbeats'][::4]
        self.cut_times = [s['t0'] for s in self.shots]
        hits = drop.get('hits') or []
        energy = drop.get('energy') or []
        strong = []
        for i, b in enumerate(self.beats):
            on_hit = any(abs(h - b) < 0.06 for h in hits)
            if b in self.downbeats or on_hit:
                strong.append((b, (energy[i] if i < len(energy) else 1.0) * (1.0 if b in self.downbeats else 0.6)))
        self.cam = fx.Camera(self.beats, self.downbeats, self.phrases, strong, drop['start'])
        # prepare each distinct clip once
        self.frames = {}
        for s in self.shots:
            key = s['clip']['path']
            if key not in self.frames:
                cx = focus_x(s['clip']) if layout == 'fill' else 0.5
                self.frames[key] = fx.prepare_shot(s['clip'], workdir, layout=layout, look=look, cx=cx)
        # a transition on every cut (bigger ones on phrase starts), never the same one twice running;
        # stutter punch-ins stay hard cuts
        small = ['flashzoom', 'blur', 'glitch', 'flashzoom', 'whip', 'blur']
        big = ['zoomthrough', 'spin', 'zoomblur', 'whip']
        last, whip_dir = None, 1
        for j, s in enumerate(self.shots):
            s['push'] = 0.08 + 0.05 * (j % 3 == 0)
            s['push_in'] = j % 2 == 0
            s['drift'] = 0.025 * (1 if j % 2 else -1)
            s['drift_y'] = 0.02 * (1 if j % 3 else -1)
            s['tilt'] = 2.2 * (1 if (j // 2) % 2 else -1)
            s['spin_dir'] = 1 if j % 2 else -1
            if j == 0:
                s['fx'] = 'drop'
                continue
            if s.get('pair') == 2:
                s['fx'] = 'cut'
                continue
            phrase = any(abs(s['t0'] - p) < 0.03 for p in self.phrases)
            pool = [x for x in (big if phrase else small) if x != last] or (big if phrase else small)
            s['fx'] = pool[j % len(pool)]
            if s['fx'] == 'whip':
                s['whip_in'] = whip_dir
                whip_dir = -whip_dir
            last = s['fx']
        for j in range(len(self.shots) - 1):
            self.shots[j]['next_fx'] = self.shots[j + 1]['fx']
        mids = []
        for sf in self.frames.values():
            if sf.paths:
                mids.append(cv2.imread(sf.paths[len(sf.paths) // 2]))
        self.colors = fx.palette(mids)
        self.over = {}
        self.particles = {}
        self.title = fx.title_card(title or 'FULL SONG') if title is not None else None

    def _overlays(self, size):
        if size not in self.over:
            self.over[size] = fx.Overlays(self.colors, size=size)
        return self.over[size]

    def frame(self, t):
        dt = 1.0 / FPS
        # picture cuts land a frame ahead of the beat, which reads as exactly on it
        shot = next((s for s in reversed(self.shots) if s['t0'] <= t + dt + 1e-6), self.shots[0])
        sf = self.frames[shot['clip']['path']]
        src = sf.frame(shot_time(shot, t, self.P))
        size = (src.shape[1], src.shape[0])
        img = fx.motion_blurred(src, lambda tt: self.cam.params(tt, shot), t, dt, size)
        since = t - shot['t0']
        to_cut = shot['t1'] - t
        tr = shot['fx']
        if tr == 'whip' and since < fx.WHIP_T:
            j = self.shots.index(shot)
            prev = self.shots[j - 1] if j else None
            out = None
            if prev is not None:
                pf = self.frames[prev['clip']['path']]
                psrc = pf.frame(shot_time(prev, t, self.P))
                if psrc.shape[:2] == src.shape[:2]:
                    out = fx.warp(psrc, *self.cam.params(t, prev), size)
            w_img = fx.whip(img, out, since, shot.get('whip_in', 1))
            if w_img is not None:
                img = w_img
        # zoom blur rides the zoom-through / spin / zoom-blur transitions, in and out
        zb = 0.0
        if tr in ('zoomthrough', 'zoomblur', 'spin') and since < 0.22:
            zb = 0.32 * (1 - since / 0.22) ** 1.5
        if shot.get('next_fx') in ('zoomthrough', 'spin') and to_cut < 0.1:
            zb = max(zb, 0.32 * (1 - to_cut / 0.1))
        if zb:
            img = fx.zoom_blur(img, zb)
        if tr == 'blur' and since < 0.2:
            img = fx.defocus(img, 1 - since / 0.2)
        if shot.get('next_fx') == 'blur' and to_cut < 0.08:
            img = fx.defocus(img, 1 - to_cut / 0.08)
        if tr == 'glitch' and since < 0.1:
            img = fx.glitch(img, t, 1 - since / 0.1)
        img = self._overlays(size).apply(img, t)
        # particles all through the drop, bursting on phrase starts; a shockwave ring on the drop and phrases
        if size not in self.particles:
            self.particles[size] = fx.Particles(self.colors, size=size)
        sph = min([t - p for p in self.phrases + [self.drop['start']] if p <= t + 1e-6] or [9.0])
        burst = max(0.0, 1 - sph / 0.5)
        img = fx.screen(img, self.particles[size].layer(t, burst), 0.55)
        if sph < 0.4:
            img = fx.screen(img, fx.ring(size, sph / 0.4, self.colors[0][::-1] if self.colors else (255, 255, 255)), 0.9)
        sd = t - self.drop['start']
        flash = 0.0
        if 0 <= sd < 0.25:
            flash = 1 - sd / 0.25
        elif tr in ('flashzoom', 'zoomthrough') and since < 0.12:
            flash = 0.75 * (1 - since / 0.12)
        elif tr in ('spin', 'blur', 'glitch', 'whip') and since < 0.08:
            flash = 0.35 * (1 - since / 0.08)
        if tr == 'flashzoom' and since < 0.45 or 0 <= sd < 0.6:
            k = since if tr == 'flashzoom' else sd
            img = fx.screen(img, fx.light_leak(size, t, hash(shot['clip']['path']) % 97 / 97.0), 0.55 * (1 - k / 0.6))
        if flash > 0:
            # bright shots get a gentler flash (they blow out to white otherwise)
            flash *= min(1.0, max(0.3, 1.25 - float(img[::16, ::16].mean()) / 255 * 1.2))
            f = img.astype(np.float32) * (1 + 1.6 * flash)
            glow = cv2.GaussianBlur(cv2.resize(img, (size[0] // 4, size[1] // 4)), (0, 0), 6)
            f += cv2.resize(glow, size).astype(np.float32) * 0.9 * flash
            img = np.clip(f + 60 * flash, 0, 255).astype(np.uint8)
        # a short dip to black right before each new phrase
        nxt = min([p - t for p in self.phrases if p > t + 1e-6] or [9.0])
        if nxt < 0.05 and sd > 0.5:
            img = (img * 0.25).astype(np.uint8)
        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        if size == (W, H):
            frame = Image.fromarray(rgb).convert('RGBA')
        else:  # letterbox: the whole shot, over a dark blurred copy of itself filling the screen
            sw, sh = W // 10, max(2, int(W // 10 * size[1] / size[0]))
            small = cv2.resize(rgb, (sw, sh), interpolation=cv2.INTER_AREA)
            k = (H // 10) / sh
            cover = cv2.resize(small, (int(sw * k) + 2, H // 10), interpolation=cv2.INTER_LINEAR)
            x0 = (cover.shape[1] - sw) // 2
            cover = cv2.GaussianBlur(cover[:, x0:x0 + sw], (0, 0), 2.5)
            bg = (cv2.resize(cover, (W, H), interpolation=cv2.INTER_LINEAR).astype(np.float32) * 0.26)
            y = int((H - size[1]) * 0.48)
            # soft shadow above and below the shot
            ramp = np.linspace(1.0, 0.55, 40, dtype=np.float32)[:, None, None]
            bg[max(0, y - 40):y] *= ramp[::-1][-(y - max(0, y - 40)):] if y > 0 else 1
            bg[y + size[1]:y + size[1] + 40] *= ramp[:max(0, min(40, H - y - size[1]))]
            bg = bg.astype(np.uint8)
            bg[y:y + size[1]] = rgb
            frame = Image.fromarray(bg).convert('RGBA')
        # the anime's name hits with the drop, pulses on the beats, then fades
        title_end = min(1.8, self.shots[0]['t1'] - self.drop['start']) if self.shots else 1.8
        if self.title is not None and 0 <= sd < title_end:
            since_b = min([t - b for b in self.beats if b <= t + 1e-6] or [9.0])
            pulse = 1 + 0.06 * math.exp(-since_b / 0.12)
            put(frame, self.title, W / 2, H * 0.5, s=max(0.01, ease_out_back(min(1.0, sd / 0.3)) * pulse),
                a=clamp((title_end - sd) / 0.25))
        return frame

    def stats(self):
        modes = [s['mode'] for s in self.shots]
        return {'editShots': len(self.shots), 'editAligned': modes.count('aligned'), 'editSfx': modes.count('sfx'),
                'editClips': [s['clip'].get('source_id') for s in self.shots], 'layout': self.layout, 'look': self.look,
                'transitions': [s['fx'] for s in self.shots]}


def section_layers(timeline):
    """The layer each section brings in: the song's real stems (acapella.py --stems real), else the old four."""
    secs = timeline['sections'][:4]
    names = [sec['layers'][-1] if sec.get('layers') else None for sec in secs]
    return [nm or LAYERS[i % 4] for i, nm in enumerate(names)]


def choose_clips(clips, names):
    """One clip per layer, matching the layer's vibe (manifest order = hook rank); different source videos."""
    chosen, used_src, used_vibes = [], set(), set()
    for i, name in enumerate(names):
        want = VIBE_OF.get(name, 'cute')
        if want in used_vibes:
            want = next((v for v in ('dance', 'fight', 'cool', 'cute') if v not in used_vibes), want)
        used_vibes.add(want)
        pool = [c for c in clips if c.get('vibe') == want] + [c for c in clips if c.get('vibe') != want]
        pick = next((c for c in pool if (c.get('source_id') or c['path']) not in used_src and c not in chosen), None)
        if pick is None:
            pick = next((c for c in clips if c not in chosen), clips[i % len(clips)])
        chosen.append(pick)
        used_src.add(pick.get('source_id') or pick['path'])
    return chosen


def plan(timeline, clips):
    """Per tile: section start, loop period (its section's length) and the clip offset that puts the peak on a beat."""
    beats = np.array(timeline['beats'])
    onsets = timeline.get('onsets') or {}
    sections = timeline['sections'][:4]
    names = section_layers(timeline)
    chosen = choose_clips(clips, names)
    tiles = []
    for i, (sec, clip) in enumerate(zip(sections, chosen)):
        name = names[i]
        s0, s1 = float(sec['start']), float(sec['end'])
        period = s1 - s0
        dur = float(clip.get('duration') or probe_duration(clip['path']))
        peak = float(clip.get('peak') if clip.get('peak') is not None else dur / 2)
        # the hit we aim at: a drum hit for fight clips, otherwise a beat, ~1 s into the section
        grid = sorted(onsets.get(name) or []) if VIBE_OF.get(name) == 'fight' and onsets.get(name) else list(beats)
        targets = [b - s0 for b in grid if s0 + 0.6 <= b < s1 - 0.3] or [min(1.0, period / 2)]
        target = next((tg for tg in targets if peak - tg >= 0), targets[0])
        offset = max(0.0, peak - target)
        stem = [o - s0 for o in sorted(onsets.get(name) or []) if s0 <= o < s1]
        hits = [h for h in motion_hits(clip) if h > offset]
        anchors = time_map(period, offset, hits, stem)
        tiles.append({'clip': clip, 'start': s0, 'period': period, 'offset': offset, 'layer': name,
                      'anchors': anchors, 'synced': len(anchors) - 2})
    return tiles


class BuildUp:
    """
    The tile section grows with the music: while the vocal is alone the picture is nearly colourless,
    dark and still; every stem that comes in pushes saturation, contrast and glow up (eased in over a
    moment), hits with an exposure flash, a zoom punch and a shake that grow with it, and the beat
    bounce and drifting sparks build until the full song drops.
    """

    def __init__(self, starts, beats, downbeats, colors=None):
        self.starts, self.beats, self.downbeats = starts, beats, downbeats
        self.n_layers = max(1, len(starts))
        self.particles = fx.Particles(colors or [(120, 200, 255), (255, 140, 220)], n=55, seed=5)
        yy, xx = np.mgrid[0:H // 4, 0:W // 4].astype(np.float32)
        r = np.sqrt(((xx - W / 8) / (W / 8)) ** 2 + ((yy - H / 8) / (H / 8)) ** 2) / math.sqrt(2)
        self.vig = cv2.resize(np.clip((r - 0.35) / 0.65, 0, 1) ** 1.6, (W, H))[..., None]

    def level(self, t):
        """0 (vocal alone) .. 1 (everything in), eased over 0.35 s after each entry."""
        n = max(1, sum(1 for s in self.starts if s <= t))
        if self.n_layers == 1:
            return 1.0, n
        age = t - self.starts[n - 1]
        prev = (n - 2) / (self.n_layers - 1) if n > 1 else 0.0
        cur = (n - 1) / (self.n_layers - 1)
        return prev + (cur - prev) * fx.ease_io(min(1.0, age / 0.35)) if n > 1 else 0.0, n

    def apply(self, frame, t):
        x, n = self.level(t)
        # the tiles keep their own colour (owner: no saturation ramp); the build-up is in the motion,
        # the flashes and the sparks, and the drop's graded edit is the colour step up
        img = np.asarray(frame.convert('RGB')).astype(np.float32)
        # motion: entry punch + shake, beat bounce growing with the layers
        age = t - self.starts[n - 1]
        z, dx, dy, rot = 1.0, 0.0, 0.0, 0.0
        if n > 1 and age < 0.5:
            z *= 1 + fx.punch(age, 0.05 + 0.07 * x, tau=0.16)
            a = (1 - age / 0.5) ** 2
            dx += a * (6 + 18 * x) * fx.smooth_noise(t, n * 13.1, 9)
            dy += a * (4 + 14 * x) * fx.smooth_noise(t, n * 7.7 + 3, 8)
            rot += a * (0.3 + 0.9 * x) * fx.smooth_noise(t, n * 3.3 + 1, 6)
        if n >= 2:
            sdb = min([t - b for b in self.downbeats if b <= t] or [9.0])
            sb = min([t - b for b in self.beats if b <= t] or [9.0])
            z *= 1 + fx.punch(sdb, 0.02 + 0.05 * x, tau=0.12) + fx.punch(sb, 0.008 + 0.022 * x, tau=0.1)
        arr = np.clip(img, 0, 255).astype(np.uint8)
        if z > 1.0005 or abs(dx) + abs(dy) > 0.3:
            arr = fx.warp(arr, z, rot, dx, dy, (W, H))
        # sparks once the beat is in; an exposure flash on every entry, bigger as it builds
        if x > 0.3:
            arr = fx.screen(arr, self.particles.layer(t, 0.0)[..., ::-1], (x - 0.3) * 0.8)
        if n > 1 and age < 0.14:
            f = 0.3 + 0.45 * x
            k = f * (1 - age / 0.14)
            arr = np.clip(arr.astype(np.float32) * (1 + 1.3 * k) + 40 * k, 0, 255).astype(np.uint8)
        return Image.fromarray(arr).convert('RGBA'), n


def label_image(layer, override=None, first=False):
    """Big layer label ("VOCALS", "+ DRUMS", ...) that pops up on top: the stem that just came in."""
    if override:
        text, emo = override
    else:
        text, emo = LAYER_LABEL.get(layer, (str(layer).upper(), '🎵'))
        text = text if first else '+ ' + text
    p = pill(text, 58, (255, 214, 0), max_w=700)
    e = quiz_emoji.image(emo, 72)
    if e is not None:
        row = Image.new('RGBA', (p.size[0] + 82, max(p.size[1], 72)), (0, 0, 0, 0))
        row.alpha_composite(p, (0, (row.size[1] - p.size[1]) // 2))
        row.alpha_composite(e, (p.size[0] + 10, (row.size[1] - 72) // 2))
        p = row
    return with_shadow(p, blur=8, offset=(0, 5))


def title_image(title, emoji):
    emo = [quiz_emoji.image(e, 76) for e in (emoji or '').split(',') if e] if emoji != 'none' else []
    emo = [e for e in emo if e is not None]
    room = 1000 - sum(e.size[0] + 10 for e in emo)  # the whole row must fit the 1080 px frame
    t = text_layer(title.lower(), 76, stroke=9, max_w=room)
    if not emo:
        return t
    row = Image.new('RGBA', (t.size[0] + sum(e.size[0] + 10 for e in emo), max(t.size[1], 82)), (0, 0, 0, 0))
    row.alpha_composite(t, (0, (row.size[1] - t.size[1]) // 2))
    x = t.size[0] + 10
    for e in emo:
        row.alpha_composite(e, (x, (row.size[1] - e.size[1]) // 2))
        x += e.size[0] + 10
    return row


def caption_image(text, tile_w):
    """Meme caption for one clip (lowercase, white with a black outline)."""
    size = 52 if tile_w >= W else 40
    return text_layer(text.lower(), size, stroke=6, stroke_fill=(0, 0, 0), max_w=tile_w - 60, min_size=26)


def render(args):
    timeline = json.load(open(args.timeline, encoding='utf-8'))
    manifest = json.load(open(args.clips, encoding='utf-8'))
    clips = manifest['clips'] if isinstance(manifest, dict) else manifest
    if len(clips) < 4:
        raise SystemExit('need at least 4 clips')
    dur = float(timeline.get('duration') or timeline['sections'][-1]['end'])
    tiles = plan(timeline, clips)
    starts = [tl['start'] for tl in tiles]
    downbeats = timeline.get('downbeats') or timeline['beats'][::4]
    work = tempfile.mkdtemp(prefix='meme-tiles-')

    # the drop: an edit of the source's own shots on the original song (tile clips fill in if there are few)
    drop = timeline.get('drop')
    edit = None
    audio = args.audio
    if drop:
        pool = list((manifest.get('edit') if isinstance(manifest, dict) else None) or [])
        tile_ids = {id(tl['clip']) for tl in tiles}
        if len(pool) < 4:
            pool += [c for c in clips if id(c) not in tile_ids][:4 - len(pool)]
        if len(pool) < 3:
            pool += [tl['clip'] for tl in tiles][:3 - len(pool)]
        edit = Edit(drop, pool, work, layout=args.layout, look=args.look, title=args.subtitle or 'FULL SONG')
        if any(sh.get('sfx') for sh in edit.shots):
            audio = os.path.join(work, 'mix.wav')
            edit.n_sfx = mix_sfx(args.audio, edit.shots, audio)

    title = title_image(args.title, args.emoji) if args.title else None
    subtitle = text_layer(args.subtitle.lower(), 44, fill=(255, 214, 0), stroke=6, stroke_fill=(0, 0, 0), max_w=900,
                          min_size=26) if args.subtitle else None
    labels = [label_image(tl['layer'], first=(i == 0)) for i, tl in enumerate(tiles)]
    captions = {}
    wm = text_layer(args.watermark, 38, fill=(255, 255, 255, 170), stroke=3, stroke_fill=(0, 0, 0, 120)) if args.watermark else None

    cmd = ['ffmpeg', '-y', '-v', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{W}x{H}', '-r', str(FPS), '-i', '-',
           '-i', audio, '-c:v', 'libx264', '-preset', 'slow', '-crf', '16', '-pix_fmt', 'yuv420p',
           '-c:a', 'aac', '-b:a', '192k', '-shortest', '-movflags', '+faststart', args.out]
    enc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    frames = {}  # (tile, size) -> ClipFrames
    beats = timeline['beats']
    build = BuildUp(starts, beats, downbeats, colors=[c[::-1] for c in edit.colors] if edit is not None else None)
    for f in range(int(dur * FPS)):
        t = f / FPS
        if edit is not None and t >= drop['start'] - 0.5 / FPS:
            frame = edit.frame(t)
            if title is not None:
                put(frame, title, W / 2, 250)
            if subtitle is not None:
                put(frame, subtitle, W / 2, 330)
            if wm is not None:
                put(frame, wm, W / 2, 150)
            enc.stdin.write(frame.convert('RGB').tobytes())
            continue
        n = max(1, sum(1 for s in starts if s <= t))
        layout = LAYOUT[n]
        frame = Image.new('RGBA', (W, H), (0, 0, 0, 255))
        for i in range(n):
            x, y, w, h = layout[i]
            tl = tiles[i]
            u = (t - tl['start']) % tl['period']
            us, cs = zip(*tl['anchors'])
            clip_t = float(np.interp(u, us, cs))
            if (i, (w, h)) not in frames:
                frames[(i, (w, h))] = ClipFrames(tl['clip'], w, h, work)
            frame.paste(frames[(i, (w, h))].frame(clip_t), (x, y))
            cap = tl['clip'].get('caption')
            # the caption font is Latin-only: never draw CJK text as empty boxes
            if cap and any('぀' <= ch <= '鿿' or '가' <= ch <= '힯' for ch in cap):
                cap = None
            if cap and not tl['clip'].get('has_caption'):
                if (i, w) not in captions:
                    captions[(i, w)] = caption_image(cap, w)
                im = captions[(i, w)]
                put(frame, im, x + w / 2, y + h - im.size[1] / 2 - (150 if n == 1 else 70))
        d = ImageDraw.Draw(frame)
        for x, y, w, h in layout:
            d.rectangle([x, y, x + w - 1, y + h - 1], outline=(0, 0, 0, 255), width=4)
        # quick glow on the tile that has just appeared (the new layer), nowhere else
        age = t - starts[n - 1]
        if n > 1 and age < GLOW_SEC:
            x, y, w, h = layout[n - 1]
            a = 1 - age / GLOW_SEC
            if age < 0.08:
                frame.alpha_composite(Image.new('RGBA', (w, h), (255, 255, 255, int(90 * a))), (x, y))
            for k in range(3):
                d.rectangle([x + k * 5, y + k * 5, x + w - 1 - k * 5, y + h - 1 - k * 5],
                            outline=GLOW + (int(255 * a * (1 - k * 0.3)),), width=6)
        # the build-up escalates with the music: colour, glow, punches, bounce and sparks grow per layer
        frame, _ = build.apply(frame, t)
        if age < 1.8:
            put(frame, labels[n - 1], W / 2, 400, s=max(0.01, ease_out_back(age / 0.25)), a=clamp((1.8 - age) / 0.3))
        # the last half beat before the drop fades to black: the drop hits out of the dark
        if edit is not None:
            to_drop = drop['start'] - t
            half = 30.0 / drop['bpm']
            if 0 < to_drop < half:
                frame.alpha_composite(Image.new('RGBA', (W, H), (0, 0, 0, int(230 * (1 - to_drop / half)))))
        if title is not None:
            put(frame, title, W / 2, 250)
        if subtitle is not None:
            put(frame, subtitle, W / 2, 330)
        if wm is not None:
            put(frame, wm, W / 2, 150)
        enc.stdin.write(frame.convert('RGB').tobytes())
    enc.stdin.close()
    if enc.wait() != 0:
        raise SystemExit('ffmpeg failed')
    shutil.rmtree(work, ignore_errors=True)
    used = [tl['clip'] for tl in tiles]
    everything = used + ([sh['clip'] for sh in edit.shots] if edit else [])
    info = {'duration': round(dur, 2), 'cuts': len(used) + (len(edit.shots) if edit else 0),
            'clipsUsed': list(dict.fromkeys(c.get('source_id') for c in everything if c.get('source_id'))),
            'vibes': [c.get('vibe') for c in used], 'layers': [tl['layer'] for tl in tiles],
            'stemSyncedHits': [tl['synced'] for tl in tiles],
            'channelsUsed': sorted({c.get('source_channel') for c in everything if c.get('source_channel')})}
    if edit:
        info.update(edit.stats(), soundEditHits=getattr(edit, 'n_sfx', 0))
    return info


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument('--audio', required=True)
    ap.add_argument('--timeline', required=True)
    ap.add_argument('--clips', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--title', default='')
    ap.add_argument('--subtitle', default='', help='the anime / group the clips are from, under the title')
    ap.add_argument('--layout', default='letterbox', choices=['letterbox', 'fill'], help='drop edit: whole 16:9 shot, or fill the screen')
    ap.add_argument('--look', default='cinematic', choices=sorted(fx.LOOKS), help='drop edit colour grade')
    ap.add_argument('--emoji', default='😭,✌️', help='comma-separated emoji after the title, or "none"')
    ap.add_argument('--watermark', default=os.environ.get('WATERMARK', ''))
    ap.add_argument('--seed', type=int, default=None)
    args = ap.parse_args()
    info = render(args)
    print(json.dumps({'ok': True, 'path': os.path.abspath(args.out), **info}, ensure_ascii=False))


if __name__ == '__main__':
    main()
