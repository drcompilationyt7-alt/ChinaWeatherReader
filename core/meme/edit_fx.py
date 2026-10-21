#!/usr/bin/env python3
"""
Effects for the drop edit (render_meme.py Edit): what makes a beat-cut
montage feel like an After Effects "edit" instead of a slideshow.

- prepare_shot(): one ffmpeg pass per shot: trims burned-in text bands,
  lays the shot out (full-width "letterbox", native sharpness; or "fill"
  around the action), sharpens (CAS), grades (S-curve, split toning,
  vibrance) and adds bloom to the highlights. Frames are kept at the
  source rate; ShotFrames blends neighbours for smooth speed ramps.
- Camera: sub-pixel affine moves at 60 fps: a slow push-in per shot,
  beat punches with overshoot, a decaying shake on phrase starts, whips;
  rendered with motion blur (several sub-frame warps averaged) whenever the
  camera moves fast.
- transitions: whip (directional blur slide), zoom blur (radial), flash
  with a light leak, dip to black.
- Overlays: an animated two-colour gradient from the anime's own palette
  (soft light), film grain, vignette, a glowing title card.

Everything is numpy + OpenCV on the CPU (GitHub Actions has no GPU).
"""
import glob
import math
import os
import subprocess
import tempfile

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1080, 1920
LOOKS = {
    # S-curve + split toning (cool shadows, warm highlights) + vibrance; bloom opacity; sharpening
    'cinematic': dict(curve="0/0 0.10/0.03 0.35/0.26 0.7/0.74 0.92/0.97 1/1", sat=1.42, gamma=0.93,
                      balance='rs=-0.04:gs=-0.01:bs=0.09:rh=0.07:gh=0.02:bh=-0.06', bloom=0.45, bloom_at=0.74, cas=0.6),
    'dreamy': dict(curve="0/0.03 0.25/0.22 0.6/0.66 0.9/0.96 1/1", sat=1.32, gamma=0.98,
                   balance='rs=0.04:bs=0.06:rm=0.04:bm=-0.02:rh=0.07:bh=-0.03', bloom=0.6, bloom_at=0.66, cas=0.45),
    'hype': dict(curve="0/0 0.15/0.04 0.5/0.5 0.85/0.95 1/1", sat=1.6, gamma=0.88,
                 balance='rs=-0.05:bs=0.09:rh=0.07:bh=-0.07', bloom=0.3, bloom_at=0.82, cas=0.75),
}
FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'quiz', 'assets', 'fonts')


# ---------------------------------------------------------------- shot preparation

def probe(path):
    r = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries',
                        'stream=width,height,r_frame_rate', '-of', 'csv=p=0', path], capture_output=True, text=True)
    try:
        w, h, rate = r.stdout.strip().split(',')[:3]
        n, d = rate.split('/')
        return int(w), int(h), float(n) / float(d or 1)
    except Exception:
        return 1280, 720, 30.0


def text_bands(path, samples=8):
    """
    Burned-in text at the top and the bottom of the frame (subtitles, credits, Shorts captions like
    "POV: ..."): (top, bottom) fractions of the height to cut away, 0 where there is none. Text is
    found as sharp, bright, outlined strokes that hold still between neighbouring samples while the
    picture moves (lyric lines change every few seconds).
    """
    r = subprocess.run(['ffmpeg', '-v', 'error', '-i', path, '-an', '-vf', f'fps=4,scale=320:-2,format=gray', '-frames:v', str(samples * 2),
                        '-f', 'rawvideo', '-'], capture_output=True)
    w, h, _ = probe(path)
    hh = int(round(320 * h / w / 2) * 2)
    a = np.frombuffer(r.stdout, np.uint8)
    if a.size < 320 * hh * 3:
        return 0.0, 0.0
    f = a[:a.size // (320 * hh) * 320 * hh].reshape(-1, hh, 320).astype(np.float32)
    edges = np.abs(np.diff(f, axis=2))[:, :-1, :] + np.abs(np.diff(f, axis=1))[:, :, :-1]
    strong = (edges > 60) & (f[:, :-1, :-1] > 180)          # bright glyph strokes (subtitles, logos)
    still = strong[1:] & strong[:-1] & (np.abs(f[1:, :-1, :-1] - f[:-1, :-1, :-1]) < 12)
    persist = still.mean(axis=0)
    on = persist > 0.3
    # letters: a row of text is many short bright strokes; window frames, lamps and sun rays are a few
    # long edges. Count stroke segments and their mean length per row.
    rise = np.diff(on.astype(np.int8), axis=1) > 0
    segs = rise.sum(axis=1)
    cover = on.sum(axis=1)
    letters = (segs >= 6) & (cover / np.maximum(segs, 1) < 7) & (cover > 0.02 * on.shape[1])
    # and a line of text is several consecutive such rows
    dense = np.convolve(letters.astype(float), np.ones(3), mode='same') >= 2
    txt = np.where(dense)[0]
    bottom = txt[txt > hh * 0.55]
    top = txt[txt < hh * 0.40]
    b = float(min(0.45, (hh - bottom.min()) / hh + 0.02)) if len(bottom) else 0.0
    t = float(min(0.45, top.max() / hh + 0.03)) if len(top) else 0.0
    return t, b


def text_band(path, samples=8):
    """Bottom burned-in text band only (kept for callers that trim the bottom)."""
    return text_bands(path, samples)[1]


def exposure(path, target=0.42):
    """
    Per-shot exposure: (gamma for ffmpeg eq, bloom scale). Bright shots (a pink stage, a white
    sky) come down toward the target mean so the grade and bloom don't turn them milky;
    dark ones are lifted a little. Returns (1.0, 1.0) if the shot can't be read.
    """
    r = subprocess.run(['ffmpeg', '-v', 'error', '-i', path, '-an', '-vf', 'fps=2,scale=160:-2,format=gray',
                        '-f', 'rawvideo', '-'], capture_output=True)
    a = np.frombuffer(r.stdout, np.uint8)
    if a.size < 1000:
        return 1.0, 1.0
    m = float(a.mean()) / 255
    m = min(0.95, max(0.05, m))
    g = math.log(target) / math.log(m)           # out = in ** g maps the mean onto the target
    g = min(1.45, max(0.8, g))
    bloom = min(1.0, max(0.35, (0.62 - m) / 0.22))  # less glow on already-bright shots
    return 1.0 / g, bloom                          # eq's gamma brightens above 1


def prepare_shot(clip, workdir, layout='letterbox', look='cinematic', cx=0.5):
    """Grade, sharpen and lay out one shot; returns ShotFrames."""
    w, h, fps = probe(clip['path'])
    lk = LOOKS.get(look, LOOKS['cinematic'])
    ex_gamma, bloom_k = exposure(clip['path'])
    x0, y0, x1, y1 = clip.get('content_box') or [0, 0, 1, 1]
    top, band = text_bands(clip['path'])
    y1 = min(y1, 1 - band) if 0 < band <= 0.3 else y1   # bigger "bands" are false alarms: never crop half
    y0 = max(y0, top) if 0 < top <= 0.3 else y0
    crop = f'crop=iw*{x1 - x0:.4f}:ih*{y1 - y0:.4f}:iw*{x0:.4f}:ih*{y0:.4f},' if (x1 - x0) * (y1 - y0) < 0.995 else ''
    aspect = (w * (x1 - x0)) / max(1, h * (y1 - y0))
    if layout == 'fill' or aspect < 1.0:  # vertical sources always fill
        geo = (f'scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,'
               f'crop={W}:{H}:max(0\\,min(iw-{W}\\,{cx:.3f}*iw-{W // 2})):(ih-{H})/2')
        size = (W, H)
    else:
        # full width, near-native sharpness: a 16:9 shot is zoomed 1.22 (1080 x 740 visible) to lose the edges
        sh = int(round(W / aspect * 1.22 / 2) * 2)
        sw = int(round(W * 1.22 / 2) * 2)
        geo = f'scale={sw}:{sh}:flags=lanczos,crop={W}:{sh}'
        size = (W, sh)
    vf = (f'[0:v]{crop}{geo},eq=gamma={ex_gamma:.3f},cas={lk["cas"]},curves=all=\'{lk["curve"]}\','
          f'eq=saturation={lk["sat"]}:gamma={lk["gamma"]},colorbalance={lk["balance"]},split[a][b];'
          f'[b]curves=all=\'0/0 {lk["bloom_at"]}/0 1/1\',gblur=sigma=24[g];'
          f'[a][g]blend=all_mode=screen:all_opacity={lk["bloom"] * bloom_k:.3f}')
    d = tempfile.mkdtemp(prefix='shot-', dir=workdir)
    subprocess.run(['ffmpeg', '-v', 'error', '-i', clip['path'], '-an', '-filter_complex', vf, '-q:v', '2',
                    os.path.join(d, '%05d.jpg')], check=False)
    return ShotFrames(sorted(glob.glob(os.path.join(d, '*.jpg'))), fps, size, band)


class ShotFrames:
    """
    Frames of a prepared shot at any time t. Anime holds each drawing for 2-3
    frames, so repeats are dropped first (keeping their real times); in-between
    moments are synthesised with DIS optical flow (both directions, half
    resolution), which is smooth slow motion without the ghosting of a plain
    cross-fade. Across a scene cut inside the clip there is no flow: the nearer
    frame is shown.
    """

    def __init__(self, paths, fps, size, text_band=0.0):
        self.fps, self.size, self.text_band = fps, size, text_band
        self.all_paths = paths
        self._cache, self._flows = {}, {}
        self._dis = None
        # unique drawings: drop frames (almost) identical to the previous kept one
        self.paths, self.times, self.cut_after = [], [], set()
        prev = None
        for k, p in enumerate(paths):
            g = cv2.imread(p, cv2.IMREAD_REDUCED_GRAYSCALE_4)
            if g is None:
                continue
            if prev is not None:
                d = float(np.abs(g.astype(np.int16) - prev.astype(np.int16)).mean())
                if d < 1.2:
                    continue
                if d > 38:  # a cut inside the clip: never morph across it
                    self.cut_after.add(len(self.paths) - 1)
            self.paths.append(p)
            self.times.append(k / fps)
            prev = g
        self.times = np.array(self.times) if self.times else np.zeros(1)

    @property
    def duration(self):
        return len(self.all_paths) / self.fps if self.all_paths else 0.0

    def _load(self, k):
        k = max(0, min(len(self.paths) - 1, k))
        if k not in self._cache:
            if len(self._cache) > 10:
                self._cache.pop(next(iter(self._cache)))
            self._cache[k] = cv2.imread(self.paths[k], cv2.IMREAD_COLOR)
        return self._cache[k]

    def _flow(self, k):
        """Flows k->k+1 and k+1->k at full size (computed at half resolution, cached)."""
        if k not in self._flows:
            if self._dis is None:
                self._dis = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM)
            a, b = self._load(k), self._load(k + 1)
            h, w = a.shape[:2]
            ga = cv2.cvtColor(cv2.resize(a, (w // 2, h // 2), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
            gb = cv2.cvtColor(cv2.resize(b, (w // 2, h // 2), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
            fab = cv2.resize(self._dis.calc(ga, gb, None), (w, h)) * 2
            fba = cv2.resize(self._dis.calc(gb, ga, None), (w, h)) * 2
            if len(self._flows) > 6:
                self._flows.pop(next(iter(self._flows)))
            self._flows[k] = (fab, fba)
        return self._flows[k]

    def frame(self, t):
        if not self.paths:
            return np.zeros((self.size[1], self.size[0], 3), np.uint8)
        t = max(0.0, t)
        k = int(np.searchsorted(self.times, t, side='right')) - 1
        k = max(0, min(len(self.paths) - 1, k))
        if k + 1 >= len(self.paths):
            return self._load(k)
        t0, t1 = self.times[k], self.times[k + 1]
        a = float((t - t0) / max(1e-6, t1 - t0))
        if a < 0.04:
            return self._load(k)
        if a > 0.96:
            return self._load(k + 1)
        if k in self.cut_after:
            return self._load(k if a < 0.5 else k + 1)
        A, B = self._load(k), self._load(k + 1)
        fab, fba = self._flow(k)
        h, w = A.shape[:2]
        gx, gy = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
        f32 = np.float32
        wa = cv2.remap(A, (gx - f32(a) * fab[..., 0]).astype(f32), (gy - f32(a) * fab[..., 1]).astype(f32),
                       cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        wb = cv2.remap(B, (gx - f32(1 - a) * fba[..., 0]).astype(f32), (gy - f32(1 - a) * fba[..., 1]).astype(f32),
                       cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        blend = cv2.addWeighted(wa, 1 - a, wb, a, 0)
        # where the warps disagree (fast limbs, occlusions) the flow failed: take the nearer warp outright
        diff = cv2.absdiff(wa, wb).max(axis=2)
        mask = cv2.GaussianBlur((diff > 38).astype(np.float32), (0, 0), 3)[..., None]
        if float(mask.mean()) < 0.002:
            return blend
        near = wa if a < 0.5 else wb
        return (blend * (1 - mask) + near * mask).astype(np.uint8)


# ---------------------------------------------------------------- camera

def ease_io(x):
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


def punch(dt, amp, tau=0.16, overshoot=0.25):
    """Scale bump after a hit: fast rise, exponential settle with a small overshoot."""
    if dt < 0:
        return 0.0
    rise = min(1.0, dt / 0.035)
    return amp * rise * math.exp(-dt / tau) * (1 - overshoot * math.sin(min(math.pi, dt / tau * 2.2)))


def smooth_noise(t, seed, freq):
    """Cheap band-limited noise in [-1, 1] (sum of detuned sines)."""
    return (math.sin(t * freq * 6.283 + seed) * 0.6 + math.sin(t * freq * 1.618 * 6.283 + seed * 1.7) * 0.3
            + math.sin(t * freq * 2.71 * 6.283 + seed * 0.3) * 0.1)


class Camera:
    """Per-frame affine: scale, rotation (deg), offset (px); summed from layered animations."""

    def __init__(self, beats, downbeats, phrases, strong_beats, drop_start):
        self.beats, self.downbeats, self.phrases = beats, downbeats, phrases
        self.strong = strong_beats
        self.drop_start = drop_start
        self.shots = []

    def params(self, t, shot):
        """
        Every shot keeps moving: a push-in or pull-out (alternating), a slow tilt and pan drift,
        a small bounce on every beat and a big one on the strong beats; shakes on phrase starts.
        """
        span = max(0.3, shot['t1'] - shot['t0'])
        u = ease_io((t - shot['t0']) / span)
        push = shot.get('push', 0.1)
        s = shot.get('zoom', 1.0) * ((1.0 + push * u) if shot.get('push_in', True) else (1.0 + push * (1 - u)))
        dx = shot.get('drift', 0.0) * (u - 0.5) * W
        dy = shot.get('drift_y', 0.0) * (u - 0.5) * H * 0.3
        rot = shot.get('tilt', 0.0) * (u - 0.5)
        # every beat: a small bounce; strong beats (hits, downbeats) a big one
        for b in self.beats:
            if 0 <= t - b < 0.4:
                s *= 1 + punch(t - b, 0.018, tau=0.12)
        for b, g in self.strong:
            if 0 <= t - b < 0.6:
                s *= 1 + punch(t - b, 0.035 + 0.06 * g)
        # the drop
        sd = t - self.drop_start
        if 0 <= sd < 0.9:
            s *= 1 + punch(sd, 0.32, tau=0.3)   # the drop: the biggest hit of the short
        # shake on phrase starts and the drop (decaying, ~9 Hz, with a little roll)
        for p in self.phrases + [self.drop_start]:
            dt = t - p
            if 0 <= dt < 0.45:
                a = (1 - dt / 0.45) ** 2
                dx += a * 24 * smooth_noise(t, p * 13.1, 9)
                dy += a * 18 * smooth_noise(t, p * 7.7 + 3, 8)
                rot += a * 1.1 * smooth_noise(t, p * 3.3 + 1, 6)
        # transitions that move the camera: spin-in, zoom-through (in), flash-zoom (in)
        since = t - shot['t0']
        tr = shot.get('fx')
        if tr == 'spin' and since < 0.22:
            k = 1 - ease_io(since / 0.22)
            rot += shot.get('spin_dir', 1) * 28 * k
            s *= 1 + 0.35 * k
        elif tr in ('zoomthrough', 'flashzoom') and since < 0.18:
            k = 1 - ease_io(since / 0.18)
            s *= 1 + (0.6 if tr == 'zoomthrough' else 0.22) * k
        # zoom-through (out): the outgoing shot rushes forward into the cut
        nxt = shot.get('next_fx')
        to_cut = shot['t1'] - t
        if nxt == 'zoomthrough' and to_cut < 0.1:
            s *= 1 + 0.6 * ease_io(1 - to_cut / 0.1)
        elif nxt == 'spin' and to_cut < 0.08:
            rot -= shot.get('spin_dir', 1) * 14 * ease_io(1 - to_cut / 0.08)
        return s, rot, dx, dy


def warp(img, s, rot, dx, dy, out_size):
    """Scale/rotate about the output centre plus an offset; sub-pixel, no integer jitter."""
    ih, iw = img.shape[:2]
    ow, oh = out_size
    m = cv2.getRotationMatrix2D((iw / 2, ih / 2), rot, s)
    m[0, 2] += (ow - iw) / 2 + dx
    m[1, 2] += (oh - ih) / 2 + dy
    return cv2.warpAffine(img, m, (ow, oh), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT101)


def motion_blurred(img, cam_fn, t, dt, out_size, samples=5, threshold=6.0):
    """Average several sub-frame warps when the camera moves fast (screen-space px per frame)."""
    p0, p1 = cam_fn(t - dt / 2), cam_fn(t + dt / 2)
    move = abs(p1[2] - p0[2]) + abs(p1[3] - p0[3]) + abs(p1[0] - p0[0]) * W * 0.5 + abs(p1[1] - p0[1]) * 12
    if move < threshold:
        return warp(img, *cam_fn(t), out_size)
    n = int(min(16, max(3, move / 4)))
    acc = np.zeros((out_size[1], out_size[0], 3), np.float32)
    for i in range(n):
        acc += warp(img, *cam_fn(t - dt / 2 + dt * i / (n - 1)), out_size)
    return (acc / n).astype(np.uint8)


# ---------------------------------------------------------------- transitions

def directional_blur(img, length, horizontal=True):
    length = int(max(1, length))
    if length < 3:
        return img
    k = np.zeros((length, length), np.float32)
    if horizontal:
        k[length // 2, :] = 1.0 / length
    else:
        k[:, length // 2] = 1.0 / length
    return cv2.filter2D(img, -1, k)


def zoom_blur(img, strength, steps=6):
    """Radial blur: average of progressively scaled copies about the centre."""
    if strength < 0.01:
        return img
    h, w = img.shape[:2]
    acc = img.astype(np.float32)
    for i in range(1, steps):
        s = 1 + strength * i / steps
        m = cv2.getRotationMatrix2D((w / 2, h / 2), 0, s)
        acc += cv2.warpAffine(img, m, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT101)
    return (acc / steps).astype(np.uint8)


WHIP_T = 0.16


def whip(incoming, outgoing, since, direction):
    """
    Whip pan between two shots: the outgoing one slides out as the incoming one
    slides in right behind it (edge to edge), smeared along the move in
    proportion to its speed. None once the whip is over.
    """
    if since >= WHIP_T or outgoing is None:
        return None
    h, w = incoming.shape[:2]
    k = ease_io(since / WHIP_T)
    k_next = ease_io(min(1.0, (since + 1 / 60) / WHIP_T))
    off = int(round(direction * w * (1 - k)))          # incoming shot's left edge
    speed = abs(k_next - k) * w                          # px per frame
    canvas = np.zeros_like(incoming)
    if off >= 0:
        canvas[:, off:] = incoming[:, :w - off]
        canvas[:, :off] = outgoing[:, w - off:] if off else canvas[:, :0]
    else:
        canvas[:, :w + off] = incoming[:, -off:]
        canvas[:, w + off:] = outgoing[:, :-off]
    return directional_blur(canvas, min(140, speed * 1.2))


# ---------------------------------------------------------------- overlays

def palette(frames, k=2):
    """Two vivid colours that define the shots (for the gradient overlay), BGR."""
    px = np.concatenate([cv2.resize(f, (48, 48)).reshape(-1, 3) for f in frames if f is not None]).astype(np.float32)
    hsv = cv2.cvtColor(px.reshape(1, -1, 3).astype(np.uint8), cv2.COLOR_BGR2HSV).reshape(-1, 3).astype(np.float32)
    vivid = px[(hsv[:, 1] > 90) & (hsv[:, 2] > 90)]
    if len(vivid) < 50:
        return [(255, 80, 200), (255, 180, 60)]  # pink / cyan-ish default (BGR)
    _, labels, centers = cv2.kmeans(vivid, k, None, (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 1.0), 3,
                                    cv2.KMEANS_PP_CENTERS)
    order = np.argsort(-np.bincount(labels.ravel(), minlength=k))
    return [tuple(int(c) for c in centers[i]) for i in order]


class Overlays:
    """Precomputed full-frame layers: vignette, grain, the palette gradient (a few angles)."""

    def __init__(self, colors, size=(W, H), grain=5.0, seed=7):
        w, h = size
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        r = np.sqrt(((xx - w / 2) / (w / 2)) ** 2 + ((yy - h / 2) / (h / 2)) ** 2) / math.sqrt(2)
        self.vig = (1 - 0.32 * np.clip((r - 0.35) / 0.65, 0, 1) ** 1.7)[..., None].astype(np.float32)
        rng = np.random.default_rng(seed)
        self.grain = [rng.normal(0, grain, (h // 2, w // 2, 1)).astype(np.float32) for _ in range(4)]
        self.grads = []
        c0, c1 = np.array(colors[0], np.float32), np.array(colors[-1], np.float32)
        for ang in np.linspace(0, math.pi, 12, endpoint=False):
            u = ((xx - w / 2) * math.cos(ang) + (yy - h / 2) * math.sin(ang)) / (0.5 * math.hypot(w, h)) * 0.5 + 0.5
            u = np.clip(u, 0, 1)[..., None]
            self.grads.append((c0 * (1 - u) + c1 * u).astype(np.float32))

    def apply(self, img, t, strength=0.34):
        f = img.astype(np.float32) / 255.0
        x = t * 0.8
        i, fr = int(x) % len(self.grads), x - int(x)
        g = (self.grads[i] * (1 - fr) + self.grads[(i + 1) % len(self.grads)] * fr) / 255.0
        # soft light
        soft = np.where(g < 0.5, 2 * f * g + f * f * (1 - 2 * g), 2 * f * (1 - g) + np.sqrt(np.clip(f, 0, 1)) * (2 * g - 1))
        f = f * (1 - strength) + soft * strength
        f *= self.vig
        n = self.grain[int(t * 24) % len(self.grain)]
        f += cv2.resize(n, (f.shape[1], f.shape[0]))[..., None] / 255.0
        return np.clip(f * 255, 0, 255).astype(np.uint8)


def light_leak(size, t, seed, color=(80, 170, 255)):
    """A warm moving blob for flashes (BGR, float 0..1), screen-blended by the caller."""
    w, h = size
    small = np.zeros((h // 8, w // 8, 3), np.float32)
    cx = (0.2 + 0.6 * ((seed * 0.37 + t * 0.8) % 1.0)) * small.shape[1]
    cy = (0.3 + 0.4 * ((seed * 0.61) % 1.0)) * small.shape[0]
    cv2.circle(small, (int(cx), int(cy)), int(small.shape[1] * 0.45), [c / 255 for c in color], -1)
    small = cv2.GaussianBlur(small, (0, 0), small.shape[1] * 0.18)
    return cv2.resize(small, (w, h))


class Particles:
    """Drifting glowing embers / sparkles in the palette colours (quarter resolution, screen-blended)."""

    def __init__(self, colors, n=70, seed=11, size=(W, H)):
        rng = np.random.default_rng(seed)
        self.size = size
        self.pos = rng.uniform(0, 1, (n, 2)).astype(np.float32)
        self.vel = np.stack([rng.uniform(-0.02, 0.02, n), rng.uniform(-0.09, -0.03, n)], 1).astype(np.float32)
        self.rad = rng.uniform(0.8, 2.6, n).astype(np.float32)
        self.phase = rng.uniform(0, 6.28, n).astype(np.float32)
        self.col = [colors[i % len(colors)] for i in range(n)]

    def layer(self, t, burst=0.0):
        w, h = self.size[0] // 4, self.size[1] // 4
        out = np.zeros((h, w, 3), np.float32)
        p = (self.pos + self.vel * t) % 1.0
        tw = 0.55 + 0.45 * np.sin(t * 5.0 + self.phase)
        for i in range(len(p)):
            c = [float(min(255, v * 0.6 + 110) / 255 * tw[i] * (1 + burst)) for v in self.col[i]]
            cv2.circle(out, (int(p[i, 0] * w), int(p[i, 1] * h)), int(self.rad[i] * (1 + burst)), c, -1, cv2.LINE_AA)
        glow = cv2.GaussianBlur(out, (0, 0), 2.2)
        return cv2.resize(out + glow * 1.5, self.size)


def ring(size, progress, color=(255, 255, 255)):
    """An expanding shockwave ring (float BGR 0..1 layer)."""
    w, h = size[0] // 4, size[1] // 4
    out = np.zeros((h, w, 3), np.float32)
    r = int((0.05 + 0.9 * ease_io(progress)) * max(w, h) * 0.7)
    th = max(1, int(6 * (1 - progress)))
    cv2.circle(out, (w // 2, h // 2), r, [float(c) / 255 * (1 - progress) for c in color], th, cv2.LINE_AA)
    out = out + cv2.GaussianBlur(out, (0, 0), 4) * 2
    return cv2.resize(out, size)


def glitch(img, t, amount):
    """RGB split plus a few horizontal slices shoved sideways."""
    h, w = img.shape[:2]
    out = img.copy()
    px = int(14 * amount)
    if px:
        out[:, px:, 2] = img[:, :-px, 2]
        out[:, :-px, 0] = img[:, px:, 0]
    rng = np.random.default_rng(int(t * 60))
    for _ in range(int(6 * amount)):
        y0 = int(rng.uniform(0, h - 20))
        hh = int(rng.uniform(8, h * 0.08))
        sh = int(rng.uniform(-60, 60) * amount)
        out[y0:y0 + hh] = np.roll(out[y0:y0 + hh], sh, axis=1)
    return out


def defocus(img, amount):
    """Lens-style blur for blur transitions (fast: blur a small copy)."""
    if amount < 0.02:
        return img
    h, w = img.shape[:2]
    small = cv2.resize(img, (w // 4, h // 4), interpolation=cv2.INTER_AREA)
    small = cv2.GaussianBlur(small, (0, 0), 1 + 7 * amount)
    return cv2.addWeighted(img, 1 - min(1.0, amount * 1.3), cv2.resize(small, (w, h)), min(1.0, amount * 1.3), 0)


def screen(img, layer, amount):
    f = img.astype(np.float32) / 255.0
    out = 1 - (1 - f) * (1 - np.clip(layer * amount, 0, 1))
    return np.clip(out * 255, 0, 255).astype(np.uint8)


def title_card(text, size=150):
    """Big glowing title (RGBA PIL image) for the drop: the anime / group name."""
    path = os.path.join(FONT_DIR, 'LilitaOne-Regular.ttf')
    font = ImageFont.truetype(path, size) if os.path.exists(path) else ImageFont.load_default()
    text = text.upper()
    d0 = ImageDraw.Draw(Image.new('RGBA', (10, 10)))
    bb = d0.textbbox((0, 0), text, font=font, stroke_width=4)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    scale = min(1.0, 980 / max(1, tw))
    if scale < 1.0:
        font = ImageFont.truetype(path, int(size * scale)) if os.path.exists(path) else font
        bb = d0.textbbox((0, 0), text, font=font, stroke_width=4)
        tw, th = bb[2] - bb[0], bb[3] - bb[1]
    pad = 80
    img = Image.new('RGBA', (tw + 2 * pad, th + 2 * pad), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.text((pad - bb[0], pad - bb[1]), text, font=font, fill=(255, 255, 255, 255), stroke_width=4, stroke_fill=(20, 10, 40, 255))
    glow = img.filter(ImageFilter.GaussianBlur(22))
    out = Image.new('RGBA', img.size, (0, 0, 0, 0))
    for _ in range(2):
        out.alpha_composite(glow)
    out.alpha_composite(img)
    return out


# =====================================================================================================
# Engine extensions (core/edit/engine.py). Everything above is unchanged, so render_meme.py keeps working.
#
# The recipes measured in core/edit/refs/*.json as image primitives. They are fast at 1080x1920 on
# 2 cores: full-resolution work stays in uint8 OpenCV calls (a float numpy pass over a 1080x1920
# frame costs 25-35 ms, the cv2 uint8 equivalent 2 ms); blurs, glows and masks are computed at
# 1/2-1/4 resolution and scaled up.
# =====================================================================================================

def gain_u8(img, g, add=0.0):
    """img * g + add, saturated (exposure bumps, flashes)."""
    if abs(g - 1.0) < 1e-3 and abs(add) < 0.5:
        return img
    return cv2.convertScaleAbs(img, alpha=float(g), beta=float(add))


def screen_u8(img, layer):
    """Screen blend of two uint8 images: 1 - (1 - a)(1 - b) = a + b (1 - a)."""
    return cv2.add(img, cv2.multiply(layer, cv2.bitwise_not(img), scale=1 / 255.0))


def layer_u8(layer_f, amount, size=None):
    """A float 0..1 light layer (any resolution) scaled by amount -> uint8 at `size` (w, h)."""
    small = np.clip(layer_f * (255.0 * amount), 0, 255).astype(np.uint8)
    if size is not None and (small.shape[1], small.shape[0]) != tuple(size):
        small = cv2.resize(small, tuple(size), interpolation=cv2.INTER_LINEAR)
    return small


def screen_layer(img, layer_f, amount):
    """Screen-blend a float light layer (e.g. quarter resolution) over img."""
    if amount <= 0.003:
        return img
    lay = layer_u8(layer_f, amount, (img.shape[1], img.shape[0]))
    if lay.ndim == 2:
        lay = cv2.cvtColor(lay, cv2.COLOR_GRAY2BGR)
    return screen_u8(img, lay)


def add_layer(img, layer_f, amount):
    """Additive light (sparse glints: particles, sparkles) - cheaper than screen and brighter where it lands."""
    if amount <= 0.003:
        return img
    return cv2.add(img, layer_u8(layer_f, amount, (img.shape[1], img.shape[0])))


def mix_u8(a, b, alpha):
    """(1 - alpha) * a + alpha * b."""
    if alpha <= 0.002:
        return a
    if alpha >= 0.998:
        return b
    return cv2.addWeighted(a, 1.0 - alpha, b, alpha, 0)


def fill_mix(img, color, alpha):
    """Mix a flat colour (BGR) over img: white flashes, dips, tint flashes."""
    if alpha <= 0.002:
        return img
    c = np.empty_like(img)
    c[:] = color
    return mix_u8(img, c, min(1.0, alpha))


def cap_white(img, limit=250, pct=99.0):
    """
    Scale the picture down so its `pct`-th luma percentile sits at `limit`.

    fx.json `not_observed`: the reference contains ZERO pure-white and ZERO pure-black frames - every
    white ramp peaks at alpha 0.72-0.83 and every dip bottoms at luma ~23. Any composite that can reach
    255 goes through here so a flash never blows out into a flat card.
    """
    if img is None or img.size == 0:
        return img
    small = img[::4, ::4]
    g = small if small.ndim == 2 else small.max(axis=2)
    p = float(np.percentile(g, pct))
    if p <= limit + 0.5:
        return img
    return cv2.convertScaleAbs(img, alpha=float(limit) / p)


def cap_black(img, floor=6, pct=1.0):
    """The dark twin of cap_white: lift the picture so its `pct`-th percentile sits at `floor`."""
    if img is None or img.size == 0:
        return img
    small = img[::4, ::4]
    g = small if small.ndim == 2 else small.min(axis=2)
    p = float(np.percentile(g, pct))
    if p >= floor - 0.5:
        return img
    return cv2.convertScaleAbs(img, alpha=1.0, beta=float(floor) - p)


def saturation_u8(img, k):
    """Saturation x k (k < 1 greys, k > 1 boosts) by mixing with the luma."""
    if abs(k - 1.0) < 0.01:
        return img
    g = cv2.cvtColor(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
    return cv2.addWeighted(img, k, g, 1.0 - k, 0)


def gauss_px(img, sigma):
    """Gaussian blur with sigma in output px: exact below 3 px, at 1/2 or 1/4 resolution above."""
    if sigma < 0.35:
        return img
    if sigma <= 3.0:
        return cv2.GaussianBlur(img, (0, 0), sigma)
    h, w = img.shape[:2]
    f = 2 if sigma < 14 else 4
    small = cv2.resize(img, (max(2, w // f), max(2, h // f)), interpolation=cv2.INTER_AREA)
    small = cv2.GaussianBlur(small, (0, 0), sigma / f)
    return cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)


def line_blur(img, length, angle=0.0):
    """
    Directional (motion) blur of `length` px along `angle` degrees (0 = horizontal, 90 = vertical).
    Axis-aligned blurs are O(1) box filters at full size; other angles use a line kernel at half size.
    """
    L = int(round(abs(length)))
    if L < 3:
        return img
    a = angle % 180.0
    if a < 1.0 or a > 179.0:
        return cv2.blur(img, (L, 1))
    if abs(a - 90.0) < 1.0:
        return cv2.blur(img, (1, L))
    h, w = img.shape[:2]
    f = 2 if L < 160 else 4
    small = cv2.resize(img, (w // f, h // f), interpolation=cv2.INTER_AREA)
    n = max(3, L // f) | 1
    k = np.zeros((n, n), np.float32)
    c = n // 2
    dx, dy = math.cos(math.radians(a)), math.sin(math.radians(a))
    cv2.line(k, (int(round(c - dx * c)), int(round(c - dy * c))), (int(round(c + dx * c)), int(round(c + dy * c))), 1.0, 1)
    k /= max(1e-6, k.sum())
    small = cv2.filter2D(small, -1, k, borderType=cv2.BORDER_REFLECT101)
    return cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)


def _avg_warps(img, mats, size):
    acc = None
    for m in mats:
        wimg = cv2.warpAffine(img, m, size, flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT101)
        if acc is None:
            acc = wimg.astype(np.uint16)
        else:
            acc += wimg
    return (acc // len(mats)).astype(np.uint8)


def _center_frac(center, w, h):
    """
    Normalise a blur centre to a fraction of the picture.

    The zoom/rot blur helpers were documented as taking a FRACTION (fx, fy), but two call sites
    (engine's radial_blur overlay and transitions.Accent.fx) hand over PIXELS, which put the centre
    of rotation ~w*w/2 off-frame - the 'radial' blur then degenerated into a colossal translation
    smear that erased the plate in a handful of frames. Accept both: anything above 1.5 is pixels.
    """
    if center is None:
        return 0.5, 0.5
    cx, cy = float(center[0]), float(center[1])
    if cx > 1.5 or cy > 1.5:
        cx, cy = cx / max(1.0, float(w)), cy / max(1.0, float(h))
    return float(np.clip(cx, -0.5, 1.5)), float(np.clip(cy, -0.5, 1.5))


def zoom_blur_c(img, strength, steps=6, center=None, down=2):
    """Radial zoom blur (average of copies scaled 1 .. 1 + strength about `center` (fx, fy)), at 1/down size."""
    if strength < 0.01:
        return img
    h, w = img.shape[:2]
    sw, sh = w // down, h // down
    small = cv2.resize(img, (sw, sh), interpolation=cv2.INTER_AREA)
    cx, cy = _center_frac(center, w, h)
    # at least one sample per ~1.2 px of travel at the frame corner, or the zoom reads as facets
    steps = int(min(24, max(int(steps), math.ceil(strength * 0.5 * math.hypot(sw, sh) / 1.2) + 1)))
    mats = [cv2.getRotationMatrix2D((cx * sw, cy * sh), 0, 1 + strength * i / max(1, steps - 1)) for i in range(steps)]
    out = _avg_warps(small, mats, (sw, sh))
    return cv2.resize(out, (w, h), interpolation=cv2.INTER_LINEAR)


def rot_blur(img, angle, steps=6, center=None, down=2):
    """Rotational blur: copies rotated over +-angle/2 degrees about the centre, averaged (spin transitions)."""
    if abs(angle) < 0.3:
        return img
    h, w = img.shape[:2]
    sw, sh = w // down, h // down
    small = cv2.resize(img, (sw, sh), interpolation=cv2.INTER_AREA)
    cx, cy = _center_frac(center, w, h)
    # no single arc step may exceed 0.4 deg, otherwise the smear shows discrete concentric ghosts
    steps = int(min(64, max(int(steps), math.ceil(abs(float(angle)) / 0.4) + 1)))
    mats = [cv2.getRotationMatrix2D((cx * sw, cy * sh), angle * (i / max(1, steps - 1) - 0.5), 1.0) for i in range(steps)]
    out = _avg_warps(small, mats, (sw, sh))
    return cv2.resize(out, (w, h), interpolation=cv2.INTER_LINEAR)


def wrap_shift(img, dy=0, dx=0):
    """Shift with wrap-around (the frame tiles into itself; vertical slide cuts)."""
    out = img
    if int(dy):
        out = np.roll(out, int(dy), axis=0)
    if int(dx):
        out = np.roll(out, int(dx), axis=1)
    return out


_LENS = {}


def lens_distort(img, k1, ca=0.0):
    """Barrel distortion r' = r (1 + k1 r^2) plus chromatic aberration (R scaled 1 + ca, B 1 - ca)."""
    if abs(k1) < 1e-3 and abs(ca) < 1e-3:
        return img
    h, w = img.shape[:2]
    key = (w, h)
    if key not in _LENS:
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        nx, ny = (xx - w / 2) / (w / 2), (yy - h / 2) / (w / 2)
        _LENS[key] = (nx, ny, nx * nx + ny * ny)
    nx, ny, r2 = _LENS[key]
    out = np.empty_like(img)
    for c, sc in ((0, 1 - ca), (1, 1.0), (2, 1 + ca)):
        f = (1 + k1 * r2) / sc
        mx = (nx * f * (w / 2) + w / 2).astype(np.float32)
        my = (ny * f * (w / 2) + h / 2).astype(np.float32)
        out[..., c] = cv2.remap(img[..., c], mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT101)
    return out


def invert_u8(img):
    return cv2.bitwise_not(img)


def rgb_split(img, px):
    """Chromatic fringe: R shifted right, B left by px."""
    px = int(round(px))
    if px <= 0:
        return img
    out = img.copy()
    out[:, px:, 2] = img[:, :-px, 2]
    out[:, :-px, 0] = img[:, px:, 0]
    return out


_HALFTONE = {}


def halftone(img, cell=18, tint=(255, 255, 255)):
    """Coarse halftone dots (dot size from the luma) over white: the 1-2 frame halftone flash."""
    h, w = img.shape[:2]
    key = (w, h, cell)
    if key not in _HALFTONE:
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        cx = (xx % cell) - cell / 2
        cy = (yy % cell) - cell / 2
        _HALFTONE[key] = (np.sqrt(cx * cx + cy * cy) / (cell * 0.7071)).astype(np.float32)
    d = _HALFTONE[key]
    lum = cv2.cvtColor(cv2.resize(img, (w // cell + 1, h // cell + 1), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
    lum = cv2.resize(lum, (w, h), interpolation=cv2.INTER_NEAREST).astype(np.float32) / 255
    dots = (d < (1 - lum) * 0.9).astype(np.uint8)
    out = np.empty_like(img)
    out[:] = tint
    out[dots > 0] = (img[dots > 0] * 0.35).astype(np.uint8)
    return out


def color_field(img, lift=25):
    """The picture reduced to a smooth gradient of its own colours, lighter in the middle (colour-field cut)."""
    h, w = img.shape[:2]
    small = cv2.resize(img, (max(2, w // 16), max(2, h // 16)), interpolation=cv2.INTER_AREA)
    small = cv2.GaussianBlur(small, (0, 0), max(1.0, small.shape[1] * 0.06 * 4))
    yy, xx = np.mgrid[0:small.shape[0], 0:small.shape[1]].astype(np.float32)
    r = np.sqrt(((xx - small.shape[1] / 2) / (small.shape[1] / 2)) ** 2 + ((yy - small.shape[0] / 2) / (small.shape[0] / 2)) ** 2)
    rad = np.clip(1 - r / 1.4, 0, 1)[..., None]
    small = np.clip(small.astype(np.float32) * (0.85 + 0.3 * rad) + lift * rad, 0, 255).astype(np.uint8)
    return cv2.resize(small, (w, h), interpolation=cv2.INTER_CUBIC)


def argyle(size, bg=(235, 205, 230), fg=(215, 190, 240), cell=0.33, alpha=0.6):
    """Diamond (argyle) pattern card for the cute pattern-flash cut (BGR colours)."""
    w, h = size
    img = np.empty((h, w, 3), np.uint8)
    img[:] = bg
    s = int(w * cell)
    lay = img.copy()
    for yc in range(-s, h + s, s):
        for xc in range(-s, w + s, s):
            off = (s // 2) if (yc // s) % 2 else 0
            p = np.array([[xc + off, yc - s // 2], [xc + off + s // 2, yc], [xc + off, yc + s // 2], [xc + off - s // 2, yc]], np.int32)
            cv2.fillConvexPoly(lay, p, fg, cv2.LINE_AA)
    return cv2.addWeighted(img, 1 - alpha, lay, alpha, 0)


def heart_poly(n=90):
    """Unit heart outline (x, y in about -1..1, y down), centred."""
    t = np.linspace(0, 2 * np.pi, n, endpoint=False)
    x = 16 * np.sin(t) ** 3
    y = -(13 * np.cos(t) - 5 * np.cos(2 * t) - 2 * np.cos(3 * t) - np.cos(4 * t))
    pts = np.stack([x, y], 1) / 17.0
    pts[:, 1] += 0.1
    return pts.astype(np.float32)


def star_poly(points=5, inner=0.45):
    a = np.linspace(-np.pi / 2, 1.5 * np.pi, points * 2, endpoint=False)
    r = np.where(np.arange(points * 2) % 2 == 0, 1.0, inner)
    return np.stack([np.cos(a) * r, np.sin(a) * r], 1).astype(np.float32)


def poly_mask(size, pts_unit, cx, cy, scale, rot=0.0, blur=0.0, down=4):
    """Filled polygon mask (float32 h x w, 0..1): unit points scaled by `scale` px, rotated, centred at (cx, cy) px."""
    w, h = size
    sw, sh = max(2, w // down), max(2, h // down)
    c, s = math.cos(math.radians(rot)), math.sin(math.radians(rot))
    p = pts_unit @ np.array([[c, s], [-s, c]], np.float32) * (scale / down)
    p += np.array([cx / down, cy / down], np.float32)
    m = np.zeros((sh, sw), np.uint8)
    cv2.fillPoly(m, [np.round(p * 16).astype(np.int32)], 255, cv2.LINE_AA, shift=4)
    if blur > 0:
        m = cv2.GaussianBlur(m, (0, 0), max(0.5, blur / down))
    return cv2.resize(m, (w, h), interpolation=cv2.INTER_LINEAR).astype(np.float32) / 255.0


def blend_mask(a, b, mask):
    """a where mask = 0, b where mask = 1 (mask float h x w). Float math only inside the mask's bounding box."""
    if mask is None:
        return a
    ys, xs = np.nonzero(mask > 0.002)
    if not len(ys):
        return a
    if mask.min() > 0.998:
        return b
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    out = a.copy()
    m = mask[y0:y1, x0:x1, None]
    out[y0:y1, x0:x1] = (a[y0:y1, x0:x1] * (1 - m) + b[y0:y1, x0:x1] * m).astype(np.uint8)
    return out


def chevron_masks(size, lead_x, n=3, band=0.14, apex_deg=110.0):
    """n nested '>' bands sweeping right -> left: mask i = pixels right of band edge i (x - x0 > k |y - H/2|)."""
    w, h = size
    sw, sh = w // 4, h // 4
    yy, xx = np.mgrid[0:sh, 0:sw].astype(np.float32)
    k = 1.0 / math.tan(math.radians(apex_deg / 2))
    out = []
    for i in range(n):
        x0 = (lead_x + i * band) * sw
        m = ((xx - x0) > k * np.abs(yy - sh / 2) * (sw / sh) * 0.5).astype(np.float32)
        out.append(cv2.resize(cv2.GaussianBlur(m, (0, 0), 0.8), (w, h)))
    return out


def iris_mask(size, radius_px, feather=6.0):
    w, h = size
    sw, sh = w // 4, h // 4
    m = np.zeros((sh, sw), np.uint8)
    cv2.circle(m, (sw // 2, sh // 2), max(0, int(radius_px / 4)), 255, -1, cv2.LINE_AA)
    m = cv2.GaussianBlur(m, (0, 0), max(0.5, feather / 4))
    return cv2.resize(m, (w, h)).astype(np.float32) / 255.0


def wave_mask(size, y_front, amp=0.04, wavelength=0.6, phase=0.0):
    """Pixels below a sine-edged front at y_front (fraction of H): the wave-band wipe (sweeps bottom -> top)."""
    w, h = size
    sw, sh = w // 4, h // 4
    xs = np.arange(sw, dtype=np.float32) / sw
    edge = (y_front + amp * np.sin(2 * np.pi * xs / wavelength + phase)) * sh
    yy = np.arange(sh, dtype=np.float32)[:, None]
    m = np.clip((yy - edge[None, :]) / 1.5 + 0.5, 0, 1)
    return cv2.resize(m.astype(np.float32), (w, h))


def blob_mask(size, progress, seed=0, corner=(1.0, 1.0)):
    """Noise-edged blob growing from a corner (progress 0..1 covers the frame)."""
    w, h = size
    sw, sh = max(8, w // 8), max(8, h // 8)
    rng = np.random.default_rng(seed)
    noise = cv2.resize(rng.random((6, 4)).astype(np.float32), (sw, sh), interpolation=cv2.INTER_CUBIC)
    noise = cv2.GaussianBlur(noise, (0, 0), 3)
    yy, xx = np.mgrid[0:sh, 0:sw].astype(np.float32)
    d = np.sqrt(((xx / sw - corner[0]) * (w / h)) ** 2 + (yy / sh - corner[1]) ** 2) / math.hypot(w / h, 1.0)
    m = ((progress * 1.25 - d + 0.18 * (noise - 0.5)) > 0).astype(np.float32)
    m = cv2.GaussianBlur(m, (0, 0), 1.0)
    return cv2.resize(m, (w, h))


def card_quad(size, cx, cy, w, h, rx=0.0, ry=0.0, rz=0.0, focal=1.8):
    """Corners of a w x h card centred at (cx, cy) px, rotated in 3D (deg about x, y, z) and projected."""
    W_, H_ = size
    f = focal * max(W_, H_)
    pts = np.array([[-w / 2, -h / 2, 0], [w / 2, -h / 2, 0], [w / 2, h / 2, 0], [-w / 2, h / 2, 0]], np.float32)
    ax, ay, az = [math.radians(v) for v in (rx, ry, rz)]
    Rx = np.array([[1, 0, 0], [0, math.cos(ax), -math.sin(ax)], [0, math.sin(ax), math.cos(ax)]], np.float32)
    Ry = np.array([[math.cos(ay), 0, math.sin(ay)], [0, 1, 0], [-math.sin(ay), 0, math.cos(ay)]], np.float32)
    Rz = np.array([[math.cos(az), -math.sin(az), 0], [math.sin(az), math.cos(az), 0], [0, 0, 1]], np.float32)
    p = pts @ (Rz @ Ry @ Rx).T
    z = f + p[:, 2]
    return np.stack([cx + p[:, 0] * f / z, cy + p[:, 1] * f / z], 1).astype(np.float32)


def warp_to_quad(img, quad, out_size, border=cv2.BORDER_CONSTANT):
    """The whole image mapped onto quad (4 corners TL, TR, BR, BL) in an out_size canvas; returns (layer, mask float)."""
    h, w = img.shape[:2]
    src = np.array([[0, 0], [w, 0], [w, h], [0, h]], np.float32)
    M = cv2.getPerspectiveTransform(src, quad.astype(np.float32))
    layer = cv2.warpPerspective(img, M, out_size, flags=cv2.INTER_LINEAR, borderMode=border)
    m = np.zeros((out_size[1], out_size[0]), np.uint8)
    cv2.fillConvexPoly(m, np.round(quad * 16).astype(np.int32), 255, cv2.LINE_AA, shift=4)
    return layer, m.astype(np.float32) / 255.0


def affine_layer(img, cx, cy, width_px, angle_deg, out_size, alpha=None):
    """img scaled to width_px, rotated angle_deg clockwise, centred at (cx, cy) px; returns (layer, mask float)."""
    h, w = img.shape[:2]
    s = width_px / max(1, w)
    M = cv2.getRotationMatrix2D((w / 2, h / 2), -angle_deg, s)
    M[0, 2] += cx - w / 2
    M[1, 2] += cy - h / 2
    layer = cv2.warpAffine(img, M, out_size, flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
    a = alpha if alpha is not None else np.full((h, w), 255, np.uint8)
    mask = cv2.warpAffine(a, M, out_size, flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
    return layer, mask.astype(np.float32) / 255.0


def bordered(img, border_frac=0.018, color=(255, 255, 255), shade=None):
    """Photo card: img inside a white border (fraction of the card width); optional top->bottom shade (1.0, 0.65)."""
    h, w = img.shape[:2]
    b = max(1, int(round(w * border_frac / (1 - 2 * border_frac))))
    out = cv2.copyMakeBorder(img, b, b, b, b, cv2.BORDER_CONSTANT, value=color)
    if shade:
        g = np.linspace(shade[0], shade[1], out.shape[0], dtype=np.float32)[:, None, None]
        out = np.clip(out * g, 0, 255).astype(np.uint8)
    return out


def shadow_under(dst, mask, sigma, offset, opacity):
    """Darken dst under a blurred, offset copy of mask (drop shadow)."""
    if opacity <= 0:
        return dst
    h, w = mask.shape
    small = cv2.resize(mask, (w // 4, h // 4))
    M = np.float32([[1, 0, offset[0] / 4], [0, 1, offset[1] / 4]])
    small = cv2.warpAffine(small, M, (w // 4, h // 4))
    small = cv2.GaussianBlur(small, (0, 0), max(0.5, sigma / 4))
    sh = cv2.resize(small, (w, h))
    g = (1 - opacity * np.clip(sh, 0, 1))
    return cv2.multiply(dst, cv2.merge([(g * 255).astype(np.uint8)] * 3), scale=1 / 255.0)


_PAPER = {}


def paper(size, color=(234, 239, 244), grain=0.03, seed=3):
    """Paper background (BGR) with faint grain and a soft light falloff."""
    key = (tuple(size), tuple(color), grain, seed)
    if key not in _PAPER:
        w, h = size
        rng = np.random.default_rng(seed)
        n = rng.normal(0, grain * 255, (h // 2, w // 2)).astype(np.float32)
        n = cv2.resize(cv2.GaussianBlur(n, (0, 0), 0.7), (w, h))
        yy, xx = np.mgrid[0:h // 8, 0:w // 8].astype(np.float32)
        r = np.sqrt(((xx - w / 16) / (w / 16)) ** 2 + ((yy - h / 16) / (h / 16)) ** 2)
        fall = cv2.resize(1 - 0.08 * np.clip(r - 0.4, 0, 1), (w, h))
        img = (np.array(color, np.float32)[None, None, :] * fall[..., None] + n[..., None])
        _PAPER[key] = np.clip(img, 0, 255).astype(np.uint8)
    return _PAPER[key]


def brush_stroke(size, color=(181, 163, 232), opacity=0.4, seed=5, box=(0.1, 0.25, 0.9, 0.7)):
    """A soft paint stroke (BGR colour) for postcard backgrounds; returns (layer, mask float)."""
    w, h = size
    rng = np.random.default_rng(seed)
    m = np.zeros((h // 4, w // 4), np.uint8)
    x0, y0, x1, y1 = box
    pts = []
    for i in range(7):
        u = i / 6
        pts.append([(x0 + (x1 - x0) * u) * w / 4, (y0 + (y1 - y0) * (0.5 + 0.35 * math.sin(u * 3.1 + seed))) * h / 4])
    pts = np.array(pts, np.int32)
    cv2.polylines(m, [pts], False, 255, max(1, int(w / 4 * 0.16)), cv2.LINE_AA)
    m = cv2.GaussianBlur(m, (0, 0), max(0.5, w / 4 * 0.02))
    tex = rng.random((max(2, h // 16), max(2, w // 16))).astype(np.float32)
    tex = cv2.resize(tex, (w // 4, h // 4))
    m = (m.astype(np.float32) / 255 * (0.75 + 0.25 * tex)) * opacity
    mask = cv2.resize(m, (w, h))
    layer = np.empty((h, w, 3), np.uint8)
    layer[:] = color
    return layer, mask


def vignette_map(size, strength, power=1.7, inner=0.3):
    """uint8 multiply map (h, w, 3): 1 in the middle, 1 - strength in the corners (cv2.multiply(img, map, scale=1/255))."""
    w, h = size
    yy, xx = np.mgrid[0:h // 4, 0:w // 4].astype(np.float32)
    r = np.sqrt(((xx - w / 8) / (w / 8)) ** 2 + ((yy - h / 8) / (h / 8)) ** 2) / math.sqrt(2)
    v = 1 - strength * np.clip((r - inner) / (1 - inner), 0, 1) ** power
    v = cv2.resize((v * 255).astype(np.uint8), (w, h), interpolation=cv2.INTER_LINEAR)
    return cv2.merge([v, v, v])


def apply_map(img, m):
    return cv2.multiply(img, m, scale=1 / 255.0)


def letterbox_bars(img, frac, color=(0, 0, 0)):
    """Black bars of `frac` of the height top and bottom (on a copy)."""
    if frac <= 0.001:
        return img
    h = img.shape[0]
    b = int(round(h * frac))
    if b <= 0:
        return img
    out = img.copy()
    out[:b] = color
    out[h - b:] = color
    return out


def hue_rotate(img, deg):
    if abs(deg) < 0.5:
        return img
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    hsv[..., 0] = ((hsv[..., 0].astype(np.int16) + int(round(deg / 2))) % 180).astype(np.uint8)
    return cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)


def bw_selective(img, hue_deg=20.0, width=20.0, sat_rest=0.15, contrast=1.1):
    """Black and white except the hues within +-width of hue_deg (the selective-colour outro)."""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    hh = hsv[..., 0].astype(np.float32) * 2
    d = np.abs((hh - hue_deg + 180) % 360 - 180)
    keep = np.clip(1 - (d - width) / 10.0, 0, 1)
    s = hsv[..., 1].astype(np.float32) * (sat_rest + (1 - sat_rest) * keep)
    hsv[..., 1] = np.clip(s, 0, 255).astype(np.uint8)
    out = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
    return cv2.convertScaleAbs(out, alpha=contrast, beta=-128 * (contrast - 1))


def dominant_hue(img):
    """The most common vivid hue (deg) of an image."""
    hsv = cv2.cvtColor(cv2.resize(img, (96, 96)), cv2.COLOR_BGR2HSV).reshape(-1, 3)
    v = hsv[(hsv[:, 1] > 70) & (hsv[:, 2] > 70)]
    if len(v) < 30:
        return 20.0
    hist = np.bincount(v[:, 0], minlength=180).astype(np.float32)
    hist = np.convolve(np.concatenate([hist[-5:], hist, hist[:5]]), np.ones(9), 'same')[5:-5]
    return float(np.argmax(hist) * 2)


def bloom_boost(img, k, threshold=0.62, sigma_frac=0.02):
    """Extra glow on the highlights (screen), computed at quarter size: exposure bumps and glow pulses."""
    if k <= 0.01:
        return img
    h, w = img.shape[:2]
    small = cv2.resize(img, (w // 4, h // 4), interpolation=cv2.INTER_AREA)
    lut = np.clip((np.arange(256) / 255.0 - threshold) / (1 - threshold), 0, 1) * 255
    hi = cv2.LUT(small, lut.astype(np.uint8))
    hi = cv2.GaussianBlur(hi, (0, 0), max(1.0, w * sigma_frac / 4))
    hi = cv2.convertScaleAbs(hi, alpha=min(3.0, k))
    return screen_u8(img, cv2.resize(hi, (w, h), interpolation=cv2.INTER_LINEAR))


def orton(img, amount=0.45, sigma_frac=0.015):
    """Orton glow: screen a blurred copy over itself (soft romance look)."""
    if amount <= 0.01:
        return img
    b = gauss_px(img, img.shape[1] * sigma_frac)
    return screen_u8(img, cv2.convertScaleAbs(b, alpha=amount))


# ---------------------------------------------------------------- light layers (float 0..1, low resolution)

class ParticleField:
    """
    Deterministic particles for any time t (random access): sparkles (4-point glints), hearts, bokeh,
    embers, confetti, gold_dust, petals, dust, stars. layer(t) returns a float BGR layer at 1/down size.
    """

    KINDS = ('sparkles', 'hearts', 'bokeh', 'embers', 'confetti', 'gold_dust', 'petals', 'dust', 'stars')

    def __init__(self, kind='sparkles', colors=None, n=60, seed=11, size=(W, H), down=4):
        self.kind = kind if kind in self.KINDS else 'sparkles'
        self.size, self.down = tuple(size), down
        rng = np.random.default_rng(seed)
        self.n = int(n)
        self.pos = rng.uniform(0, 1, (self.n, 2)).astype(np.float32)
        up = {'embers': (-0.09, -0.03), 'bokeh': (-0.05, -0.01), 'sparkles': (-0.03, 0.0), 'hearts': (-0.07, -0.03),
              'confetti': (0.06, 0.14), 'petals': (0.04, 0.09), 'gold_dust': (-0.03, -0.005), 'dust': (-0.01, 0.01),
              'stars': (-0.01, 0.01)}[self.kind]
        self.vel = np.stack([rng.uniform(-0.02, 0.02, self.n), rng.uniform(up[0], up[1], self.n)], 1).astype(np.float32)
        base = {'sparkles': (1.5, 4.0), 'hearts': (3.0, 7.0), 'bokeh': (3.0, 10.0), 'embers': (0.8, 2.6), 'confetti': (2.0, 4.0),
                'gold_dust': (0.6, 1.6), 'petals': (2.5, 5.0), 'dust': (0.5, 1.2), 'stars': (1.0, 2.5)}[self.kind]
        self.rad = rng.uniform(base[0], base[1], self.n).astype(np.float32) * (min(self.size) / 1080.0)
        self.phase = rng.uniform(0, 6.28, self.n).astype(np.float32)
        self.spin = rng.uniform(-3, 3, self.n).astype(np.float32)
        cols = colors or {'hearts': [(180, 105, 255), (203, 192, 255)], 'gold_dust': [(138, 213, 245)],
                          'embers': [(60, 140, 255), (80, 200, 255)], 'bokeh': [(255, 200, 150), (230, 160, 255)]}.get(
            self.kind, [(255, 255, 255), (255, 230, 200)])
        self.col = [cols[i % len(cols)] for i in range(self.n)]
        self.heart = heart_poly(24)

    def layer(self, t, burst=0.0):
        w, h = self.size[0] // self.down, self.size[1] // self.down
        out = np.zeros((h, w, 3), np.float32)
        p = (self.pos + self.vel * t + np.stack([0.01 * np.sin(t * 1.3 + self.phase), np.zeros(self.n)], 1)) % 1.0
        k = self.kind
        if k in ('sparkles', 'gold_dust', 'stars'):
            # glints: each one flares up briefly (3-6 Hz) and is dark in between
            tw = np.clip(np.sin(t * (2 * np.pi * 0.7) * (1 + self.spin * 0.25) + self.phase), 0, 1) ** 3
        else:
            tw = 0.55 + 0.45 * np.sin(t * 2.0 + self.phase)
        sc = 4.0 / self.down
        for i in range(self.n):
            x, y = float(p[i, 0] * w), float(p[i, 1] * h)
            r = float(self.rad[i] * (1 + 0.5 * burst)) * sc
            c = [float(min(1.0, v / 255.0 * float(tw[i]) * (1 + burst))) for v in self.col[i]]
            if tw[i] < 0.02 and k in ('sparkles', 'stars', 'gold_dust'):
                continue
            if k in ('sparkles', 'stars'):
                L = r * 2.0 * (0.5 + 0.5 * float(tw[i]))
                cv2.line(out, (int(x - L), int(y)), (int(x + L), int(y)), c, 1, cv2.LINE_AA)
                cv2.line(out, (int(x), int(y - L)), (int(x), int(y + L)), c, 1, cv2.LINE_AA)
                cv2.circle(out, (int(x), int(y)), max(1, int(r * 0.5)), c, -1, cv2.LINE_AA)
            elif k == 'gold_dust':
                cv2.circle(out, (int(x), int(y)), max(1, int(r * 0.7)), c, -1, cv2.LINE_AA)
            elif k == 'hearts':
                pts = self.heart * r * 1.4
                a = math.radians(12 * math.sin(t * 1.7 + self.phase[i]))
                R = np.array([[math.cos(a), math.sin(a)], [-math.sin(a), math.cos(a)]], np.float32)
                cv2.fillPoly(out, [np.round((pts @ R + [x, y]) * 16).astype(np.int32)], c, cv2.LINE_AA, shift=4)
            elif k in ('confetti', 'petals'):
                a = t * self.spin[i] + self.phase[i]
                ax = (r * 1.2, r * (0.45 + 0.4 * abs(math.sin(a))))
                cv2.ellipse(out, (int(x), int(y)), (max(1, int(ax[0])), max(1, int(ax[1]))), math.degrees(a), 0, 360, c, -1,
                            cv2.LINE_AA)
            else:
                cv2.circle(out, (int(x), int(y)), max(1, int(r)), c, -1, cv2.LINE_AA)
        if k == 'bokeh':
            return cv2.GaussianBlur(out, (0, 0), 1.6) * 0.8
        glow = cv2.GaussianBlur(out, (0, 0), 2.2)
        return out + glow * 1.5


def lens_flare(size, x, y, strength=1.0, color=(255, 220, 170), down=4):
    """Anamorphic lens flare at (x, y) (fractions): core glow, horizontal streak and ghosts through the centre."""
    w, h = size[0] // down, size[1] // down
    out = np.zeros((h, w, 3), np.float32)
    px, py = x * w, y * h
    c = np.array(color, np.float32) / 255
    cv2.circle(out, (int(px), int(py)), max(2, int(w * 0.05)), c.tolist(), -1, cv2.LINE_AA)
    cv2.ellipse(out, (int(px), int(py)), (int(w * 0.6), max(1, int(h * 0.004))), 0, 0, 360, (c * 0.8).tolist(), -1, cv2.LINE_AA)
    for k, (f, r) in enumerate(((-0.4, 0.03), (0.3, 0.05), (0.7, 0.02), (1.2, 0.08))):
        gx, gy = w / 2 + (w / 2 - px) * f, h / 2 + (h / 2 - py) * f
        col = (c * np.array([0.6, 0.9, 1.0] if k % 2 else [1.0, 0.7, 0.9], np.float32) * 0.35).tolist()
        cv2.circle(out, (int(gx), int(gy)), max(1, int(w * r)), col, -1, cv2.LINE_AA)
    out = cv2.GaussianBlur(out, (0, 0), 2.0) + cv2.GaussianBlur(out, (0, 0), 8.0) * 0.8
    return out * strength


def light_rays(img, x, y, strength=0.6, threshold=0.55, down=4, steps=12):
    """God rays: the highlights radially smeared away from the light source (x, y fractions); float layer."""
    h, w = img.shape[:2]
    sw, sh = w // down, h // down
    small = cv2.resize(img, (sw, sh), interpolation=cv2.INTER_AREA).astype(np.float32) / 255
    hi = np.clip((small - threshold) / (1 - threshold), 0, 1)
    acc = hi.copy()
    for i in range(1, steps):
        s = 1 + 0.6 * i / steps
        M = cv2.getRotationMatrix2D((x * sw, y * sh), 0, s)
        acc += cv2.warpAffine(hi, M, (sw, sh), flags=cv2.INTER_LINEAR) * (1 - i / steps)
    return acc / steps * 2.5 * strength


def light_leak_layer(size, t, seed=0.3, color=(80, 170, 255), down=8):
    """A warm moving light leak (float layer at 1/8 size): screen it over the frame."""
    w, h = size[0] // down, size[1] // down
    out = np.zeros((h, w, 3), np.float32)
    cx = (0.2 + 0.6 * ((seed * 0.37 + t * 0.8) % 1.0)) * w
    cy = (0.3 + 0.4 * ((seed * 0.61) % 1.0)) * h
    cv2.circle(out, (int(cx), int(cy)), int(w * 0.45), [v / 255 for v in color], -1)
    return cv2.GaussianBlur(out, (0, 0), w * 0.18)


def lightning(size, p0, angle=0.0, seed=0, length=0.35, color=(255, 200, 120), down=2):
    """A jagged lightning bolt (random-walk polyline) from p0 (fractions): white core + coloured glow; float layer."""
    w, h = size[0] // down, size[1] // down
    rng = np.random.default_rng(seed)
    out = np.zeros((h, w, 3), np.float32)
    x, y = p0[0] * w, p0[1] * h
    pts = [(x, y)]
    step = length * max(w, h) / 10
    a = math.radians(angle)
    for _ in range(10):
        a2 = a + rng.uniform(-0.7, 0.7)
        x, y = x + math.cos(a2) * step, y + math.sin(a2) * step
        pts.append((x, y))
    pts = np.array(pts, np.int32)
    glow = np.zeros_like(out)
    cv2.polylines(glow, [pts], False, [v / 255 for v in color], 6, cv2.LINE_AA)
    cv2.polylines(out, [pts], False, (1, 1, 1), 2, cv2.LINE_AA)
    return out + cv2.GaussianBlur(glow, (0, 0), 6) * 1.5


def heart_outline(size, cx, cy, width, thick=3, color=(255, 255, 255), glow=8.0):
    """White heart outline with a glow (float layer at half size)."""
    w, h = size[0] // 2, size[1] // 2
    out = np.zeros((h, w, 3), np.float32)
    pts = heart_poly(80) * (width / 4) + np.array([cx / 2, cy / 2], np.float32)
    cv2.polylines(out, [np.round(pts * 16).astype(np.int32)], True, [v / 255 for v in color], max(1, thick // 2), cv2.LINE_AA, shift=4)
    return out + cv2.GaussianBlur(out, (0, 0), glow / 2) * 1.2


def dust_specks(size, seed=0, n=80, down=2):
    """Faint star / dust specks on black (dark blur-in cut); float layer at 1/down size."""
    w, h = size[0] // down, size[1] // down
    rng = np.random.default_rng(seed)
    out = np.zeros((h, w, 3), np.float32)
    for _ in range(n):
        cv2.circle(out, (int(rng.uniform(0, w)), int(rng.uniform(0, h))), 1, (0.5, 0.5, 0.5), -1, cv2.LINE_AA)
    return cv2.GaussianBlur(out, (0, 0), 0.8)


# ---------------------------------------------------------------------------------------------- fx group
# Primitives measured on core/edit/refs/fx.json (WGvtliE1298, "End Of Beginning" AE typography showcase).
# Everything below is additive: nothing above this line is touched, so core/meme/render_meme.py is unaffected.

def exp_whip_series(n, growth, peak_frac):
    """
    The house whip of the fx group: per-frame displacement growing GEOMETRICALLY to `peak_frac` of the
    width on the last frame. Measured f117-131 (14 frames, x1.30, peak 16.8 %W) and f192-204
    (13 frames, x1.40, peak 15.4 %W). Returns the cumulative offset in width fractions, one per frame,
    ending at 0 for the last entry (the cut lands on the peak).
    """
    n = max(2, int(n))
    step = np.array([peak_frac * growth ** (k - (n - 1)) for k in range(n)], dtype=np.float64)
    return np.cumsum(step) - float(np.sum(step))


def whip_alpha(k, n, peak=0.72):
    """Overlay alpha of a whip ramp: shape ~ (k / (n-1))^2 up to `peak` (measured 0.71-0.83, never 1.0)."""
    u = min(1.0, max(0.0, k / max(1, n - 1)))
    return peak * u * u


def aniso_blur(img, length, angle=0.0, aniso=1.0):
    """
    Directional blur whose cross-axis also smears by (1 - aniso) of the length: the fx whips measure
    anisotropy 0.34 -> 1.00 (0.34 = nearly isotropic smear, 1.00 = a pure line).
    """
    if length < 1.0:
        return img
    out = line_blur(img, length, angle)
    cross = length * max(0.0, 1.0 - float(aniso))
    if cross >= 1.0:
        out = line_blur(out, cross, angle + 90.0)
    return out


def crack_star(size, cx, cy, radius, arms=20, seed=0, thick=2, glow=6.0):
    """The single impact star of frame 182: ~20 white cracks radiating from one point (float layer, half size)."""
    w, h = max(2, size[0] // 2), max(2, size[1] // 2)
    rng = np.random.default_rng(seed)
    out = np.zeros((h, w, 3), np.float32)
    px, py = cx / 2.0, cy / 2.0
    for k in range(int(arms)):
        a = k * 2 * math.pi / max(1, arms) + float(rng.uniform(-0.12, 0.12))
        r = (radius / 2.0) * float(rng.uniform(0.45, 1.0))
        pts = [(px, py)]
        steps = 3
        for j in range(1, steps + 1):
            rr = r * j / steps
            aa = a + float(rng.uniform(-0.09, 0.09))
            pts.append((px + math.cos(aa) * rr, py + math.sin(aa) * rr))
        cv2.polylines(out, [np.round(np.array(pts, np.float32) * 16).astype(np.int32)], False, (1, 1, 1),
                      max(1, int(thick)), cv2.LINE_AA, shift=4)
    return out + cv2.GaussianBlur(out, (0, 0), glow / 2) * 1.1


def spider_cracks(size, cx, cy, progress, seed=0, rings=4, arms=14, sparks=5):
    """
    The cracked-glass memory plane (f292-330): radiating cracks joined by concentric chords, with 4-6
    specular sparks. `progress` 0..1 grows the web. Float layer at half size.
    """
    w, h = max(2, size[0] // 2), max(2, size[1] // 2)
    rng = np.random.default_rng(seed)
    out = np.zeros((h, w, 3), np.float32)
    px, py = cx / 2.0, cy / 2.0
    R = 0.85 * max(w, h) * min(1.0, max(0.0, progress))
    ang = [k * 2 * math.pi / arms + float(rng.uniform(-0.2, 0.2)) for k in range(arms)]
    rad = [[R * (j + 1) / rings * float(rng.uniform(0.82, 1.14)) for j in range(rings)] for _ in range(arms)]
    for k in range(arms):
        pts = [(px, py)] + [(px + math.cos(ang[k]) * rad[k][j], py + math.sin(ang[k]) * rad[k][j]) for j in range(rings)]
        cv2.polylines(out, [np.round(np.array(pts, np.float32) * 16).astype(np.int32)], False, (1, 1, 1), 1, cv2.LINE_AA, shift=4)
    for j in range(rings):
        ring = [(px + math.cos(ang[k]) * rad[k][j], py + math.sin(ang[k]) * rad[k][j]) for k in range(arms)]
        cv2.polylines(out, [np.round(np.array(ring, np.float32) * 16).astype(np.int32)], True, (0.8, 0.8, 0.8), 1,
                      cv2.LINE_AA, shift=4)
    out = out * 0.55 + cv2.GaussianBlur(out, (0, 0), 2.0) * 0.9
    for _ in range(int(sparks)):
        k = int(rng.integers(0, arms))
        j = int(rng.integers(0, rings))
        sx, sy = px + math.cos(ang[k]) * rad[k][j], py + math.sin(ang[k]) * rad[k][j]
        cv2.circle(out, (int(sx), int(sy)), max(1, int(0.006 * w)), (1.6, 1.6, 1.6), -1, cv2.LINE_AA)
    return out


_SHATTER_CACHE = {}


def _voronoi_cells(w, h, cells, seed, cx, cy):
    """Voronoi facets of `cells` random seeds (denser near the impact point) as float polygons."""
    rng = np.random.default_rng(seed)
    n_near = cells // 2
    ang = rng.uniform(0, 2 * math.pi, n_near)
    rad = rng.uniform(0.03, 0.45, n_near) ** 1.4 * w
    pts = [(cx * w + math.cos(a) * r, cy * h + math.sin(a) * r * 0.8) for a, r in zip(ang, rad)]
    pts += [(float(rng.uniform(0, w)), float(rng.uniform(0, h))) for _ in range(cells - n_near)]
    sub = cv2.Subdiv2D((-w, -h, 3 * w, 3 * h))
    for x, y in pts:
        if 0 <= x < w and 0 <= y < h:
            sub.insert((float(x), float(y)))
    facets, centers = sub.getVoronoiFacetList([])
    out = []
    for f, c in zip(facets, centers):
        f = np.clip(np.asarray(f, np.float32), [-0.2 * w, -0.2 * h], [1.2 * w, 1.2 * h])
        out.append((f, (float(c[0]), float(c[1]))))
    return out


def shatter_shards(img, progress, cx=0.5, cy=0.5, seed=0, cells=55, spread=0.04, max_rot=8.0,
                   gap_color=(90, 180, 255), gap_gain=0.42, edge=0.55):
    """
    Voronoi glass break (f182-192, 11 frames). The plane really BREAKS: every shard is translated outward
    from the impact point by progress x `spread` x W (x a distance factor) and rotated by rng(-max_rot,
    max_rot) x progress deg about its own centroid, over a warm gap layer, with a lit glass edge and a
    per-shard facet shade. Measured on the reference: mean gradient x2.9, blob count x11, luma -43 %,
    R-B -11 -> +10. `progress` 0..1.
    """
    p = min(1.0, max(0.0, float(progress)))
    if p <= 0.001:
        return img
    h, w = img.shape[:2]
    key = (int(seed), int(cells), w, h, round(float(cx), 3), round(float(cy), 3))
    got = _SHATTER_CACHE.get(key)
    if got is None:
        rng = np.random.default_rng(seed + 17)
        cl = _voronoi_cells(w, h, int(cells), seed, cx, cy)
        meta = []
        for poly, c in cl:
            dx, dy = c[0] - cx * w, c[1] - cy * h
            d = math.hypot(dx, dy)
            nd = d / max(1.0, math.hypot(w, h) * 0.5)
            ux, uy = (dx / d, dy / d) if d > 1 else (0.0, 0.0)
            meta.append((poly, c, ux, uy, 0.6 + 1.6 * nd, float(rng.uniform(-1, 1)), float(rng.uniform(0, 1)), d))
        meta.sort(key=lambda m: -m[7])                      # far shards first, the near ones overlap them
        yy, xx = np.mgrid[0:h // 8 + 1, 0:w // 8 + 1].astype(np.float32)
        glow = np.exp(-(((xx * 8 - cx * w) / (0.35 * w)) ** 2 + ((yy * 8 - cy * h) / (0.35 * w)) ** 2))
        got = (meta, cv2.resize(glow, (w, h), interpolation=cv2.INTER_LINEAR))
        if len(_SHATTER_CACHE) > 8:
            _SHATTER_CACHE.clear()
        _SHATTER_CACHE[key] = got
    meta, glow = got
    gc = np.array(gap_color, np.float32)
    out = (gc[None, None, :] * (gap_gain * (0.35 + 1.1 * glow[..., None]))).clip(0, 255).astype(np.uint8)
    src = gain_u8(img, 1 - 0.30 * p)
    for poly, c, ux, uy, amp, rr, shade, _d in meta:
        tx, ty = ux * amp * spread * w * p, uy * amp * spread * w * p
        ang = rr * max_rot * p
        sc = 1.0 - 0.10 * p * shade                         # shards tip away from the lens
        M = cv2.getRotationMatrix2D(c, ang, sc)
        M[:, 2] += (tx, ty)
        q = (np.hstack([poly, np.ones((len(poly), 1), np.float32)]) @ M.T).astype(np.float32)
        x0, y0 = int(max(0, math.floor(q[:, 0].min()) - 2)), int(max(0, math.floor(q[:, 1].min()) - 2))
        x1, y1 = int(min(w, math.ceil(q[:, 0].max()) + 2)), int(min(h, math.ceil(q[:, 1].max()) + 2))
        if x1 - x0 < 2 or y1 - y0 < 2:
            continue
        Mr = M.copy()
        Mr[:, 2] -= (x0, y0)
        piece = cv2.warpAffine(src, Mr, (x1 - x0, y1 - y0), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT101)
        m = np.zeros((y1 - y0, x1 - x0), np.uint8)
        qq = np.round((q - (x0, y0)) * 16).astype(np.int32)
        cv2.fillPoly(m, [qq], 255, cv2.LINE_AA, shift=4)
        g = 1.0 - 0.55 * p * shade                          # facet shading: each shard catches less light
        piece = cv2.convertScaleAbs(piece, alpha=g, beta=0)
        cv2.polylines(piece, [qq], True, (255, 255, 255), max(1, int(round(w / 960.0))), cv2.LINE_AA, shift=4)
        if edge < 1.0:
            pass
        roi = out[y0:y1, x0:x1]
        mf = (m.astype(np.float32) / 255.0)[..., None]
        roi[:] = (roi * (1 - mf) + piece * mf).astype(np.uint8)
    warm = np.array([-10.5, 0.0, 10.5], np.float32) * (2 * p - 1)       # R-B swings -11 -> +10
    return np.clip(out.astype(np.float32) + warm, 0, 255).astype(np.uint8)


def filmstrip_layer(size, thumbs, angle_deg=28.0, phase=0.0, frac=0.20):
    """
    A 35 mm strip running diagonally across the frame carrying thumbnails (f292-330).
    Returns (BGR layer, float mask) at full size. `thumbs` is a list of BGR images.
    """
    w, h = size
    L = int(math.hypot(w, h) * 1.25)
    cell = max(24, int(frac * w))
    strip_h = int(cell * 1.42)
    strip = np.full((strip_h, L, 3), 18, np.uint8)
    hole_h = max(2, int(strip_h * 0.10))
    pad = max(2, int(strip_h * 0.03))
    x = int(-phase * (cell + pad)) % (cell + pad) - (cell + pad)
    i = 0
    thh = int(strip_h * 0.62)
    while x < L:
        if thumbs:
            th = thumbs[i % len(thumbs)]
            if th is not None and getattr(th, 'size', 0):
                sub = cv2.resize(th, (cell, thh), interpolation=cv2.INTER_AREA)
                x0, y0 = max(0, x), int(strip_h * 0.19)
                x1 = min(L, x + cell)
                if x1 > x0:
                    strip[y0:y0 + thh, x0:x1] = sub[:, x0 - x:x1 - x]
        i += 1
        x += cell + pad
    for row in (int(strip_h * 0.045), int(strip_h * 0.855)):
        xx = 0
        while xx < L:
            cv2.rectangle(strip, (xx, row), (xx + int(cell * 0.16), row + hole_h), (235, 235, 235), -1)
            xx += max(4, int(cell * 0.33))
    return affine_layer(strip, w * 0.5, h * 0.5, L, -float(angle_deg), size)


def bokeh_layer(size, n=57, seed=0, t=0.0, colors=((255, 210, 160), (200, 220, 255), (255, 255, 255)), down=2):
    """Out-of-focus bokeh discs drifting slowly (the intro card's field: 57 blobs/frame). Float layer at 1/down."""
    w, h = max(2, size[0] // down), max(2, size[1] // down)
    rng = np.random.default_rng(seed)
    out = np.zeros((h, w, 3), np.float32)
    for _ in range(int(n)):
        bx = (float(rng.uniform(0, 1)) + 0.012 * t * float(rng.uniform(-1, 1))) % 1.0 * w
        by = (float(rng.uniform(0, 1)) + 0.008 * t) % 1.0 * h
        r = float(rng.uniform(0.012, 0.055)) * w
        c = colors[int(rng.integers(0, len(colors)))]
        a = float(rng.uniform(0.10, 0.34))
        cv2.circle(out, (int(bx), int(by)), int(r), tuple(v / 255.0 * a for v in c), -1, cv2.LINE_AA)
        cv2.circle(out, (int(bx), int(by)), int(r), tuple(v / 255.0 * a * 0.6 for v in c), max(1, int(r * 0.12)), cv2.LINE_AA)
    return cv2.GaussianBlur(out, (0, 0), max(1.0, 0.004 * w))


def ink_blot_mask(size, progress, seed=0, side='top', coverage=0.35):
    """
    The organic ink blot that enters from an edge and grows fingers to ~35 % coverage (f107-121).
    Returns a float mask (h, w) in 0..1.
    """
    w, h = size
    p = min(1.0, max(0.0, float(progress)))
    sw, sh = max(16, w // 8), max(16, h // 8)
    rng = np.random.default_rng(seed)
    m = np.zeros((sh, sw), np.float32)
    reach = coverage * 2.4 * p
    base = {'top': (0.5, 0.0), 'bottom': (0.5, 1.0), 'left': (0.0, 0.5), 'right': (1.0, 0.5)}.get(side, (0.5, 0.0))
    sgn = 1 if side in ('top', 'left') else -1
    for k in range(13):
        a = math.pi * (k / 12.0)
        rr = reach * float(rng.uniform(0.45, 1.25))
        cx = base[0] * sw + math.cos(a) * rr * sw * 0.7
        cy = base[1] * sh + abs(math.sin(a)) * rr * sh * sgn
        cv2.circle(m, (int(cx), int(cy)), int(max(2, rr * 0.55 * min(sw, sh))), 1.0, -1, cv2.LINE_AA)
    m = cv2.GaussianBlur(m, (0, 0), 1.6)
    m = np.clip((m - 0.35) * 3.4, 0, 1)
    return cv2.resize(m, (w, h), interpolation=cv2.INTER_LINEAR)


def motion_blur_bgra(bgra, length, angle=0.0):
    """Directional blur of a BGRA sprite (colour and alpha together): text that passes the lens is smeared."""
    if bgra is None or length < 1.5:
        return bgra
    n = int(min(81, max(3, round(length))))
    if n % 2 == 0:
        n += 1
    k = np.zeros((n, n), np.float32)
    cv2.line(k, (0, n // 2), (n - 1, n // 2), 1.0, 1)
    M = cv2.getRotationMatrix2D((n / 2 - 0.5, n / 2 - 0.5), -float(angle), 1.0)
    k = cv2.warpAffine(k, M, (n, n))
    s = float(k.sum())
    if s <= 1e-6:
        return bgra
    pad = n // 2 + 1
    big = cv2.copyMakeBorder(bgra, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=(0, 0, 0, 0))
    return cv2.filter2D(big, -1, k / s, borderType=cv2.BORDER_CONSTANT)


def polaroid_card(img, border_frac=0.07, bottom_extra=0.16):
    """A photo with a white polaroid border (wider at the bottom)."""
    h, w = img.shape[:2]
    b = max(2, int(border_frac * w))
    bb = int(b + bottom_extra * w)
    return cv2.copyMakeBorder(img, b, bb, b, b, cv2.BORDER_CONSTANT, value=(252, 252, 250))


def light_streaks_h(size, n=7, seed=0, strength=1.0, down=2):
    """Horizontal light streaks over the incoming shot of a whip (measured on f131-143)."""
    w, h = max(2, size[0] // down), max(2, size[1] // down)
    rng = np.random.default_rng(seed)
    out = np.zeros((h, w, 3), np.float32)
    for _ in range(int(n)):
        y = int(rng.uniform(0, h))
        t = max(1, int(rng.uniform(1, 4)))
        a = float(rng.uniform(0.2, 0.7)) * strength
        cv2.line(out, (0, y), (w, y), (a, a, a), t, cv2.LINE_AA)
    return cv2.GaussianBlur(out, (0, 0), max(1.0, 0.003 * w))


# ---------------------------------------------------------------- round 4: glass break in depth (fx f176-192)

_NEBULA_CACHE = {}


def nebula_set(size, seed=0, cx=0.5, cy=0.5, luma=(22, 58), tint=(0.62, 0.80, 1.0)):
    """
    The dark warm set seen THROUGH a glass break (fx f184-192): low-frequency brown smoke, luma 25-40 with a
    brighter core behind the impact point. Saturation stays < 0.4. BGR uint8, cached per (size, seed).
    """
    w, h = size
    key = (w, h, int(seed), round(cx, 2), round(cy, 2))
    got = _NEBULA_CACHE.get(key)
    if got is not None:
        return got
    rng = np.random.default_rng(seed + 401)
    acc = np.zeros((h, w), np.float32)
    for div, amp in ((4, 1.0), (9, 0.55), (22, 0.3)):
        n = rng.random((div + 2, max(2, int(div * w / max(1, h))) + 2)).astype(np.float32)
        acc += cv2.resize(n, (w, h), interpolation=cv2.INTER_CUBIC) * amp
    acc = (acc - acc.min()) / max(1e-6, float(acc.max() - acc.min()))
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    core = np.exp(-(((xx - cx * w) / (0.38 * w)) ** 2 + ((yy - cy * h) / (0.38 * w)) ** 2))
    v = luma[0] + (luma[1] - luma[0]) * np.clip(0.55 * acc + 0.6 * core * (0.5 + 0.5 * acc), 0, 1)
    out = np.clip(v[..., None] * np.array(tint, np.float32)[None, None, :] * 1.18, 0, 255).astype(np.uint8)
    if len(_NEBULA_CACHE) > 6:
        _NEBULA_CACHE.clear()
    _NEBULA_CACHE[key] = out
    return out


def crack_wedges(img, under, cx, cy, length_px, width_px, arms=18, seed=0, rim=1):
    """
    The radial crack STAR of f183-185: `arms` thin tapering wedges from the impact point, each a dark gap
    showing `under` (the set behind the glass) with a 1 px white rim. length_px / width_px grow per frame
    (0.2 -> 0.6 W and 2 -> 6 px in the reference). Nothing else is drawn - no web before the break.
    """
    h, w = img.shape[:2]
    rng = np.random.default_rng(seed + 77)
    m = np.zeros((h, w), np.uint8)
    rimm = np.zeros((h, w), np.uint8)
    for k in range(int(arms)):
        a = k * 2 * math.pi / arms + float(rng.uniform(-0.16, 0.16))
        ln = length_px * float(rng.uniform(0.45, 1.0))
        wd = width_px * float(rng.uniform(0.6, 1.5))
        kink = a + float(rng.uniform(-0.10, 0.10))
        mx, my = cx + math.cos(a) * ln * 0.45, cy + math.sin(a) * ln * 0.45
        ex, ey = mx + math.cos(kink) * ln * 0.55, my + math.sin(kink) * ln * 0.55
        nx, ny = -math.sin(a), math.cos(a)
        poly = np.array([(cx + nx * wd * 0.35, cy + ny * wd * 0.35), (mx + nx * wd * 0.5, my + ny * wd * 0.5), (ex, ey),
                         (mx - nx * wd * 0.5, my - ny * wd * 0.5), (cx - nx * wd * 0.35, cy - ny * wd * 0.35)], np.float32)
        q = np.round(poly * 16).astype(np.int32)
        cv2.fillPoly(m, [q], 255, cv2.LINE_AA, shift=4)
        cv2.polylines(rimm, [q], True, 255, max(1, int(rim)), cv2.LINE_AA, shift=4)
    mf = (m.astype(np.float32) / 255.0)[..., None]
    rf = (rimm.astype(np.float32) / 255.0)[..., None] * (1 - mf) * 0.85
    out = img.astype(np.float32) * (1 - mf) + under.astype(np.float32) * mf
    out = out * (1 - rf) + 255.0 * rf
    return np.clip(out, 0, 255).astype(np.uint8)


_SHARD3D_CACHE = {}


def shatter_shards3d(img, under, frame_f, n_frames=7, cx=0.5, cy=0.5, seed=0, cells=44, travel=0.12, growth=1.4,
                     z_near=1.35, rot=(8.0, 25.0), dim=0.78):
    """
    Large irregular shards flying OUTWARD and toward the lens (fx f186-192). `frame_f` = float frames @30 since
    the break. Per shard: outward translation 0 -> `travel` W accelerating x`growth` per frame, z-scale
    1 -> `z_near` for the near half, in-plane roll +-rot deg plus a tilt about a random axis (an anisotropic
    squeeze), its own motion blur of 0.5 x the step, one lit edge. The gaps show `under` (the dark set), never
    a flat colour, and the full cell net is never drawn. Cells are SMALL at the impact and LARGE outside.
    """
    h, w = img.shape[:2]
    f = max(0.0, min(float(n_frames), float(frame_f)))
    if f <= 0.0:
        return img
    key = (int(seed), int(cells), w, h, round(float(cx), 3), round(float(cy), 3))
    meta = _SHARD3D_CACHE.get(key)
    if meta is None:
        rng = np.random.default_rng(seed + 5)
        pts = []
        for _ in range(int(cells)):
            a = float(rng.uniform(0, 2 * math.pi))
            r = float(rng.uniform(0.0, 1.0)) ** 1.35 * 0.75 * w + 0.045 * w     # denser centre, big cells outside
            pts.append((cx * w + math.cos(a) * r, cy * h + math.sin(a) * r * 0.85))
        sub = cv2.Subdiv2D((-w, -h, 3 * w, 3 * h))
        for x, y in pts:
            if -0.5 * w < x < 1.5 * w and -0.5 * h < y < 1.5 * h:
                sub.insert((float(x), float(y)))
        facets, centers = sub.getVoronoiFacetList([])
        meta = []
        for fc, c in zip(facets, centers):
            poly = np.clip(np.asarray(fc, np.float32), [-0.15 * w, -0.15 * h], [1.15 * w, 1.15 * h])
            dx, dy = float(c[0]) - cx * w, float(c[1]) - cy * h
            d = math.hypot(dx, dy)
            ux, uy = (dx / d, dy / d) if d > 1 else (1.0, 0.0)
            # jitter the flight direction so the pattern is not a perfect radial fan
            ja = float(rng.uniform(-0.35, 0.35))
            ux, uy = ux * math.cos(ja) - uy * math.sin(ja), ux * math.sin(ja) + uy * math.cos(ja)
            near = float(rng.uniform(0, 1))
            meta.append(dict(poly=poly, c=(float(c[0]), float(c[1])), ux=ux, uy=uy, amp=float(rng.uniform(0.55, 1.45)),
                             near=near, roll=float(rng.uniform(rot[0], rot[1])) * (1 if rng.random() < 0.5 else -1),
                             tilt=float(rng.uniform(15, 55)), axis=float(rng.uniform(0, math.pi)),
                             shade=float(rng.uniform(0.0, 1.0)), d=d))
        meta.sort(key=lambda m: m['near'])                   # far shards first, near ones cover them
        if len(_SHARD3D_CACHE) > 6:
            _SHARD3D_CACHE.clear()
        _SHARD3D_CACHE[key] = meta
    # cumulative geometric travel
    tot = sum(growth ** j for j in range(int(n_frames)))

    def cum(ff):
        i = int(ff)
        s = sum(growth ** j for j in range(i)) + (ff - i) * growth ** i
        return s / tot

    p = cum(f)
    step = (p - cum(max(0.0, f - 1.0))) * travel * w
    out = under.copy()
    src = gain_u8(img, 1.0 - (1.0 - dim) * min(1.0, f / n_frames))
    lw = max(1, int(round(w / 960.0)))
    for m in meta:
        tx, ty = m['ux'] * m['amp'] * travel * w * p, m['uy'] * m['amp'] * travel * w * p
        sc = 1.0 + ((z_near - 1.0) * m['near'] - 0.06 * (1 - m['near'])) * p
        ang = m['roll'] * p
        tl = math.cos(math.radians(m['tilt'] * p))
        ca, sa = math.cos(m['axis']), math.sin(m['axis'])
        A = np.array([[ca, -sa], [sa, ca]]) @ np.diag([1.0, tl]) @ np.array([[ca, sa], [-sa, ca]])
        r = math.radians(ang)
        Rm = np.array([[math.cos(r), -math.sin(r)], [math.sin(r), math.cos(r)]])
        L = (Rm @ A) * sc
        c = np.array(m['c'])
        M = np.hstack([L, (c + (tx, ty) - L @ c)[:, None]]).astype(np.float32)
        q = (np.hstack([m['poly'], np.ones((len(m['poly']), 1), np.float32)]) @ M.T).astype(np.float32)
        bl = 0.5 * step * m['amp']
        pad = int(bl) + 3
        x0, y0 = int(max(0, math.floor(q[:, 0].min()) - pad)), int(max(0, math.floor(q[:, 1].min()) - pad))
        x1, y1 = int(min(w, math.ceil(q[:, 0].max()) + pad)), int(min(h, math.ceil(q[:, 1].max()) + pad))
        if x1 - x0 < 3 or y1 - y0 < 3:
            continue
        Mr = M.copy()
        Mr[:, 2] -= (x0, y0)
        piece = cv2.warpAffine(src, Mr, (x1 - x0, y1 - y0), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT101)
        mk = np.zeros((y1 - y0, x1 - x0), np.uint8)
        qq = np.round((q - (x0, y0)) * 16).astype(np.int32)
        cv2.fillPoly(mk, [qq], 255, cv2.LINE_AA, shift=4)
        g = 1.0 - 0.45 * p * m['shade'] + 0.25 * p * m['near'] * (1 - m['shade'])
        piece = cv2.convertScaleAbs(piece, alpha=g, beta=0)
        # one lit edge (the side that faces the impact light), not a full outline
        e = np.zeros_like(mk)
        cv2.polylines(e, [qq], True, 255, lw, cv2.LINE_AA, shift=4)
        sh = np.float32([[1, 0, -m['ux'] * 2 * lw], [0, 1, -m['uy'] * 2 * lw]])
        lit = cv2.subtract(e, cv2.warpAffine(e, sh, (e.shape[1], e.shape[0])))
        piece = cv2.add(piece, cv2.merge([lit, lit, lit]) // 2)
        if bl >= 1.5:
            n = int(min(61, max(3, round(bl)))) | 1
            k = np.zeros((n, n), np.float32)
            cv2.line(k, (int(round(n / 2 - 0.5 - m['ux'] * n / 2)), int(round(n / 2 - 0.5 - m['uy'] * n / 2))),
                     (int(round(n / 2 - 0.5 + m['ux'] * n / 2)), int(round(n / 2 - 0.5 + m['uy'] * n / 2))), 1.0, 1)
            k /= max(1e-6, float(k.sum()))
            mf = mk.astype(np.float32) / 255.0
            pm = cv2.filter2D(piece.astype(np.float32) * mf[..., None], -1, k, borderType=cv2.BORDER_CONSTANT)
            mf = cv2.filter2D(mf, -1, k, borderType=cv2.BORDER_CONSTANT)
            roi = out[y0:y1, x0:x1].astype(np.float32)
            out[y0:y1, x0:x1] = np.clip(roi * (1 - mf[..., None]) + pm, 0, 255).astype(np.uint8)
        else:
            mf = (mk.astype(np.float32) / 255.0)[..., None]
            roi = out[y0:y1, x0:x1]
            roi[:] = (roi * (1 - mf) + piece * mf).astype(np.uint8)
    return out
