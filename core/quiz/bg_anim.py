"""Animated looping backgrounds for the pop quiz (owner 2026-10-10).

Regular background = a slowly spinning globe of LED dots (orthographic projection of the land in countries-50m.json,
one full turn per LOOP_S seconds, so frame N == frame 0).  Each themed edition adds one subtle motion layer (waves,
fog + bats, snow, lanterns, hearts, pulsing lines, smoke, lens glints).  Everything is low contrast and periodic
in LOOP_S (integer cycles only), pre-rendered ONCE per (palette, edition) into an mp4 under the cache dir and then
streamed frame by frame.  Same palette as the static background.
"""
import hashlib
import math
import os
import random
import subprocess

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

import geo

W, H, FPS = 1080, 1920, 30
LOOP_S = 24
N = LOOP_S * FPS
VERSION = 4
CACHE = os.environ.get('POP_BG_CACHE') or os.path.join(os.path.expanduser('~'), '.cache', 'pop_quiz_bg')

CX, CY, R = 540, 1060, 505   # globe centre / radius (the old dot map filled y 520..1600)
TILT = math.radians(18)
SPACING = 20                 # dot pitch at the equator, px (same as the old map)


# ─── globe ───────────────────────────────────────────────────────────────────

_PTS = None


def _land_points():
    """(lat, lon) in radians of every land LED dot, on a lat-ring lattice with ~SPACING px pitch on the sphere."""
    global _PTS
    if _PTS is not None:
        return _PTS
    mw, mh = 3600, 1800
    land = Image.new('L', (mw, mh), 0)
    d = ImageDraw.Draw(land)
    for p in geo.land_polygons():
        for idx, r in enumerate(p):
            pts = [((x + 180) / 360 * mw, (90 - y) / 180 * mh) for x, y in r.tolist()]
            if len(pts) >= 3:
                d.polygon(pts, fill=255 if idx == 0 else 0)
    arr = np.array(land)
    step = SPACING / R
    out = []
    lat = -math.pi / 2 + step / 2
    while lat < math.pi / 2:
        n = max(1, int(round(2 * math.pi * math.cos(lat) / step)))
        for k in range(n):
            lon = -math.pi + (k + 0.5) * 2 * math.pi / n
            px = int((lon + math.pi) / (2 * math.pi) * mw) % mw
            py = min(mh - 1, int((math.pi / 2 - lat) / math.pi * mh))
            if arr[py, px] > 127:
                out.append((lat, lon))
        lat += step
    _PTS = np.array(out, dtype=np.float64)
    return _PTS


def globe_mask(theta, pts=None):
    """L image (W x 1080 region 520..1600 is placed by the caller): LED dots of the land, rotated by theta."""
    pts = _land_points() if pts is None else pts
    lat, lon = pts[:, 0], pts[:, 1] + theta
    x0, y0, z0 = np.cos(lat) * np.sin(lon), np.sin(lat), np.cos(lat) * np.cos(lon)
    ca, sa = math.cos(TILT), math.sin(TILT)
    y = y0 * ca - z0 * sa
    z = y0 * sa + z0 * ca
    vis = z > 0.02
    x, y, z = x0[vis], y[vis], z[vis]
    SS = 2
    img = Image.new('L', (1080 * SS, 1080 * SS), 0)
    d = ImageDraw.Draw(img)
    cx, cy = 540 * SS, 540 * SS
    for xi, yi, zi in zip(x.tolist(), y.tolist(), z.tolist()):
        r = 3.0 * (0.45 + 0.55 * math.sqrt(zi)) * SS
        v = int(255 * min(1.0, 0.55 + 0.45 * zi))
        px, py = cx + xi * R * SS, cy - yi * R * SS
        d.ellipse([px - r, py - r, px + r, py + r], fill=v)
    return img.resize((1080, 1080), Image.BOX)


# ─── theme motion layers (all periodic in LOOP_S) ────────────────────────────

TAU = 2 * math.pi


def _glow(d, x, y, r, col, a):
    for k, f in enumerate((1.0, 0.65, 0.35)):
        rr = r * f
        d.ellipse([x - rr, y - rr, x + rr, y + rr], fill=col + (int(a * (0.35 + 0.35 * k)),))


def _heart(d, cx, cy, r, col):
    d.ellipse([cx - r, cy - r, cx, cy], fill=col)
    d.ellipse([cx, cy - r, cx + r, cy], fill=col)
    d.polygon([(cx - r, cy - r / 2), (cx + r, cy - r / 2), (cx, cy + r)], fill=col)


def _sparkle(d, x, y, r, col, a):
    w = max(1.0, r * 0.18)
    c = col + (int(a),)
    d.polygon([(x, y - r), (x + w, y - w), (x + r, y), (x + w, y + w), (x, y + r), (x - w, y + w), (x - r, y), (x - w, y - w)], fill=c)


class Motion:
    def __init__(self, kind, accent, seed=7):
        self.kind = kind
        self.acc = tuple(int(c) for c in accent)
        rng = random.Random(seed)
        self.rng = rng
        k = kind
        if k == 'snow':
            self.snow = [(rng.randrange(0, W), rng.randrange(0, H), rng.choice((2, 3, 3, 4)), rng.choice((1, 1, 2)),
                          rng.choice((1, 2, 3)), rng.random(), rng.randrange(70, 120)) for _ in range(170)]
        elif k in ('hearts', 'maid'):
            self.hearts = [(rng.randrange(30, W - 30), rng.randrange(0, H), rng.randrange(10, 26), rng.choice((1, 1, 2)),
                            rng.choice((1, 2, 3)), rng.random(), rng.choice(((255, 120, 160), (255, 200, 220), (230, 70, 120))),
                            rng.randrange(60, 115)) for _ in range(26)]
            self.stars = [(rng.randrange(40, W - 40), rng.choice((rng.randrange(20, 220), rng.randrange(1580, 1900), rng.randrange(0, H))),
                           rng.randrange(8, 18), rng.choice((1, 2, 3)), rng.random()) for _ in range(14)]
        elif k == 'moon_bats':
            self.bats = [(rng.choice((rng.randrange(40, 200), rng.randrange(1600, 1860))), rng.random(), rng.uniform(0.6, 1.2),
                          rng.choice((-1, 1)), rng.choice((1, 2)), rng.random()) for _ in range(5)]
        elif k == 'fireworks':
            self.lant = [(rng.choice((rng.randrange(25, 170), rng.randrange(910, 1055))), rng.random(), rng.randrange(14, 26),
                          rng.choice((1, 2)), rng.random()) for _ in range(9)]
            self.bursts = [(rng.randrange(80, W - 80), rng.choice((rng.randrange(60, 200), rng.randrange(1600, 1860))),
                            rng.choice(((255, 200, 80), (255, 110, 160), (120, 220, 255), (190, 255, 120))), rng.random()) for _ in range(6)]
        elif k == 'glints':
            self.glints = [(rng.randrange(60, W - 60), rng.choice((rng.randrange(40, 230), rng.randrange(1570, 1880))),
                            rng.randrange(14, 30), rng.choice((1, 2)), rng.random()) for _ in range(9)]
        if k in ('moon_bats', 'smoke', 'sun_waves'):
            ph = lambda: rng.uniform(0, TAU)  # noqa: E731
            self.fog = [(rng.choice((1, 2, 3)), rng.choice((1, 2, 3)), rng.choice((1, 1, 2)), ph(), rng.choice((-1, 1))) for _ in range(5)]

    # fog / smoke: smooth periodic scalar field on a coarse grid, upscaled (alpha only)
    def _fog_alpha(self, u, strength, rows=(0, H)):
        gw, gh = 54, 96
        xs = np.arange(gw)[None, :] / gw
        ys = np.arange(gh)[:, None] / gh
        f = np.zeros((gh, gw), dtype=np.float32)
        for kx, ky, n, ph, sg in self.fog:
            f += np.sin(TAU * (kx * xs + ky * ys * 0.5 + sg * n * u) + ph)
        f = np.clip(f / 3.0 * 0.5 + 0.5, 0, 1) ** 1.6
        a = Image.fromarray((f * 255).astype(np.uint8), 'L').resize((W, H), Image.BICUBIC)
        arr = np.asarray(a, dtype=np.float32) / 255.0 * strength
        # fade out toward the middle so the card area stays clean
        yy = np.arange(H, dtype=np.float32)
        edge = np.clip(np.maximum((300 - yy) / 300, (yy - 1500) / 420), 0.25, 1.0)
        return arr * edge[:, None]

    def layer(self, u, base_rgb=None):
        """RGBA overlay for loop phase u in [0, 1)."""
        k = self.kind
        lay = Image.new('RGBA', (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(lay)
        if k == 'sun_waves':
            for i, (y, col) in enumerate(((1620, (255, 255, 255, 70)), (1700, (120, 220, 255, 120)), (1790, (40, 170, 230, 170)), (1860, (20, 120, 200, 210)))):
                dirn = 1 if i % 2 == 0 else -1
                ph = i * 1.3 + dirn * TAU * u * (1 + (i % 2))
                pts = [(x, y + 26 * math.sin(x / 95 + ph)) for x in range(0, W + 20, 20)] + [(W, H), (0, H)]
                d.polygon(pts, fill=col)
            # light shimmer on the top wave
            for j in range(16):
                x = (j * 71 + 23) % W
                tw = math.sin(math.pi * ((u * (1 + j % 3) + j * 0.137) % 1.0)) ** 2
                yy = 1620 + 26 * math.sin(x / 95 + 0 + TAU * u) + 8
                if tw > 0.05:
                    d.ellipse([x - 9, yy - 2, x + 9, yy + 2], fill=(255, 255, 255, int(130 * tw)))
            # sun glow breathes (2 cycles)
            g = 0.5 + 0.5 * math.sin(TAU * 2 * u)
            _glow(d, 930, 100, 160, (255, 226, 120), 30 + 30 * g)
        elif k == 'fireworks':
            for x, ph, r, n, p2 in self.lant:   # floating lanterns, rise one frame height per loop (n=1) with fade at the ends
                p = (ph + u * n) % 1.0
                y = H + 80 - p * (H + 160)
                a = math.sin(math.pi * p) ** 0.7
                xx = x + 14 * math.sin(TAU * (u * 2 + p2))
                _glow(d, xx, y, r * 1.8, (255, 170, 60), 70 * a)
                d.ellipse([xx - r * 0.55, y - r * 0.75, xx + r * 0.55, y + r * 0.75], fill=(255, 190, 90, int(120 * a)))
            for cx, cy, col, off in self.bursts:   # far soft fireworks, 2 bursts per loop each, expanding and fading
                p = (off + u * 2) % 1.0
                if p > 0.75:
                    continue
                q = p / 0.75
                rr = 20 + 80 * (1 - (1 - q) ** 2)
                a = (1 - q) ** 1.4
                for j in range(14):
                    ang = j * TAU / 14 + off
                    x, y = cx + rr * math.cos(ang), cy + rr * math.sin(ang)
                    d.ellipse([x - 3, y - 3, x + 3, y + 3], fill=col + (int(110 * a),))
        elif k == 'moon_bats':
            fa = self._fog_alpha(u, 0.20)
            fog = Image.fromarray((fa * 255).astype(np.uint8), 'L')
            lay.paste(Image.new('RGBA', (W, H), (190, 170, 215, 255)), (0, 0), fog)
            for y0, ph, s_, dirn, n, p2 in self.bats:
                p = (ph + u) % 1.0
                cx = (-120 + p * (W + 240)) if dirn > 0 else (W + 120 - p * (W + 240))
                cy = y0 + 16 * math.sin(TAU * (u * 2 + p2))
                fl = math.sin(TAU * (u * 36))
                wy = 14 * fl * s_
                pts = [(cx - 40 * s_, cy - wy), (cx - 18 * s_, cy - 12 * s_ - wy * 0.6), (cx, cy + 4 * s_), (cx + 18 * s_, cy - 12 * s_ - wy * 0.6),
                       (cx + 40 * s_, cy - wy), (cx + 10 * s_, cy + 10 * s_), (cx - 10 * s_, cy + 10 * s_)]
                d.polygon(pts, fill=(10, 4, 16, 190))
        elif k == 'snow':
            for x, y, r, n, m, ph, a in self.snow:
                yy = (y + u * n * H) % H
                xx = (x + 16 * math.sin(TAU * (u * m + ph))) % W
                d.ellipse([xx - r, yy - r, xx + r, yy + r], fill=(255, 255, 255, a))
        elif k in ('hearts', 'maid'):
            for x, y, r, n, m, ph, col, a in self.hearts:
                yy = (y - u * n * H) % H
                xx = x + 18 * math.sin(TAU * (u * m + ph))
                _heart(d, xx, yy, r, col + (a,))
            for x, y, r, m, ph in self.stars:
                tw = math.sin(math.pi * ((u * m + ph) % 1.0)) ** 2
                _sparkle(d, x, y, r, (255, 255, 255), 150 * tw)
        elif k == 'stripes':
            pulse = 0.5 + 0.5 * math.sin(TAU * 4 * u)   # 6 s breathing
            sh = 110 * u
            a = int(20 + 28 * pulse)
            for i in range(-5, 14):
                x = i * 110 + sh
                d.polygon([(x, H), (x + 50, H), (x + 330, 1560), (x + 280, 1560)], fill=self.acc + (a,))
        elif k == 'smoke':
            fa = self._fog_alpha(u, 0.45)
            fog = Image.fromarray((fa * 255).astype(np.uint8), 'L')
            lay.paste(Image.new('RGBA', (W, H), (0, 0, 0, 255)), (0, 0), fog)
            fa2 = self._fog_alpha((u * 1) % 1.0, 0.10)
            lay.paste(Image.new('RGBA', (W, H), self.acc + (255,)), (0, 0), Image.fromarray((fa2[::-1] * 255).astype(np.uint8), 'L'))
        elif k == 'glints':
            for x, y, r, m, ph in self.glints:
                tw = math.sin(math.pi * ((u * m + ph) % 1.0)) ** 6
                if tw > 0.03:
                    _sparkle(d, x, y, r, (210, 240, 255), 190 * tw)
        return lay


# ─── loop render / cache / reader ────────────────────────────────────────────

def _key(base, accent, kind, dots):
    h = hashlib.sha1()
    h.update(np.asarray(base.convert('RGB').resize((54, 96))).tobytes())
    h.update(repr((accent, kind, dots, VERSION, LOOP_S, FPS)).encode())
    return h.hexdigest()[:16]


def loop_path(base, accent, kind, dots=True):
    return os.path.join(CACHE, f'bg-{kind or "plain"}-{_key(base, accent, kind, dots)}.mp4')


def render_loop(base, accent, kind=None, dots=True, dot_strength=0.14, out=None):
    """base: RGBA/RGB static background (gradient + static deco, no dot map).  Writes the loop mp4 once."""
    out = out or loop_path(base, accent, kind, dots)
    if os.path.exists(out):
        return out
    os.makedirs(os.path.dirname(out), exist_ok=True)
    tmp = out + '.part.mp4'
    base_rgb = np.asarray(base.convert('RGB'), dtype=np.uint8)
    acc = np.array(accent, dtype=np.float32)
    mot = Motion(kind, accent) if kind and kind not in ('none',) else None
    pts = _land_points()
    cmd = ['ffmpeg', '-y', '-v', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{W}x{H}', '-r', str(FPS), '-i', '-',
           '-c:v', 'libx264', '-preset', 'medium', '-crf', '12', '-pix_fmt', 'yuv420p', '-g', '30', '-an', tmp]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for f in range(N):
        u = f / N
        frame = base_rgb.copy()
        if dots:
            m = np.asarray(globe_mask(TAU * u, pts), dtype=np.float32) / 255.0 * dot_strength
            reg = frame[520:1600].astype(np.float32)
            reg = reg * (1 - m[..., None]) + acc[None, None, :] * m[..., None]
            frame[520:1600] = np.clip(reg + 0.5, 0, 255).astype(np.uint8)
        if mot is not None:
            im = Image.fromarray(frame, 'RGB').convert('RGBA')
            im.alpha_composite(mot.layer(u))
            frame = np.asarray(im.convert('RGB'))
        proc.stdin.write(frame.tobytes())
    proc.stdin.close()
    if proc.wait() != 0:
        raise RuntimeError('ffmpeg failed while rendering the background loop')
    os.replace(tmp, out)
    return out


class LoopReader:
    """Frames of the cached loop: frame_at(t) -> PIL RGBA (sequential reads stream, a jump restarts ffmpeg)."""

    def __init__(self, path):
        self.path = path
        self.proc = None
        self.next = 0
        self.last = None

    def _start(self, idx):
        self._stop()
        cmd = ['ffmpeg', '-v', 'error', '-ss', f'{idx / FPS:.4f}', '-i', self.path, '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-']
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=W * H * 3 * 2)
        self.next = idx

    def _stop(self):
        if self.proc is not None:
            try:
                self.proc.stdout.close()
                self.proc.kill()
            except Exception:  # noqa: BLE001
                pass
            self.proc = None

    def frame_at(self, t):
        idx = int(t * FPS) % N
        if self.proc is None or idx != self.next:
            if self.last is not None and idx == self.next - 1:
                return self.last.copy()
            self._start(idx)
        raw = self.proc.stdout.read(W * H * 3)
        if len(raw) < W * H * 3:   # end of the file: wrap
            self._start(0)
            raw = self.proc.stdout.read(W * H * 3)
            idx = 0
        self.next = idx + 1
        if self.next >= N:
            self._stop()
            self.next = N   # forces a restart at 0 on the next call
        self.last = Image.frombuffer('RGB', (W, H), raw, 'raw', 'RGB', 0, 1).convert('RGBA')
        return self.last.copy()

    def close(self):
        self._stop()
