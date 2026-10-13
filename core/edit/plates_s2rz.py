"""
blur_flow 's2rz' plates (remake round 3, core/edit/refs/remake-blur_flow-S2rz.md), drawn procedurally from the
reference S2rzMsl0N7k measured at 30 fps:

  word plate (reference 18.0-18.53 / 27.97-28.5): a dark navy field (luma 19-29) with ~190 fine dim dashes flying out
  and 8 soft comet streaks at the edges, the plate pulling out slightly; one 3D word (Lilita, dark slate-blue face,
  thin light edge, shallow extrusion) flies in: frames 0-2 a dark blurred burst, frames 3-9 giant letters (4x -> 1x)
  tumbling in yaw, staggered by one frame, then settled at a 0.185 H cap height (whole word <= 0.8 W).
  ember plate (reference 18.57-19.03 / 28.53-29.0): black night, a textured teal haze low in frame, two sparkler cores
  with a big orange bloom and a tapered trail, soft bokeh discs that come and go, spark spray and zig-zag bursts,
  animated at 15 fps (each frame shown twice), with a slow push.

    word_plate(out, word, case='upper', nfr=20, seed=3)
    ember_plate(out, nfr=32, seed=1)
    python core/edit/plates_s2rz.py --word WORD --out plate.mp4 | --embers --out embers.mp4
"""
import argparse
import json
import math
import os
import subprocess

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import gpu as _gpu  # noqa: E402  NVENC when usable, libx264 otherwise (and always on CI)
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
W, H, FPS = 1920, 1080, 30
SPEED = 3.2      # round 2: the reference's tunnel keeps flying under the settled word (flow 1-3 %W / frame; was 1.4 -> 0.5)
FONT = os.path.join(os.path.dirname(HERE), 'quiz', 'assets', 'fonts', 'LilitaOne-Regular.ttf')


def writer(path):
    return subprocess.Popen(_gpu.convert(['ffmpeg', '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-s', f'{W}x{H}', '-r', str(FPS),
                             '-i', '-', '-c:v', 'libx264', '-preset', 'medium', '-crf', '14', '-pix_fmt', 'yuv420p', '-threads', '6', path], 'final'),
                            stdin=subprocess.PIPE)


# ------------------------------------------------------------------ background: navy star tunnel
# round 3 (critic: ours was a bright cyan starburst, mean luma ~50-90 and Laplacian 4700-21000; the reference plate is
# a dark navy field, luma 19-29, Laplacian 205-320, ~180 fine dim dashes and 6-10 soft comet streaks at the edges)
class Tunnel:
    def __init__(self, seed=3, n=190, nbig=8):
        rng = np.random.default_rng(seed)
        self.ang = rng.uniform(0, 2 * np.pi, n)
        self.r0 = rng.uniform(0.05, 1.0, n) ** 0.7
        self.spd = rng.uniform(0.5, 1.3, n)
        self.b = rng.uniform(0.25, 0.65, n)
        self.bang = np.linspace(0, 2 * np.pi, nbig, endpoint=False) + rng.uniform(-0.3, 0.3, nbig)
        self.br0 = rng.uniform(0.45, 0.8, nbig)
        self.bspd = rng.uniform(0.25, 0.45, nbig)
        self.bw = rng.uniform(0.6, 1.0, nbig)
        yy, xx = np.mgrid[0:H, 0:W]
        v = np.clip(1 - np.hypot((xx - W / 2) / W, (yy - H / 2) / H) * 1.5, 0, 1)
        self.base = np.stack([0.085 + 0.09 * v, 0.045 + 0.045 * v, 0.025 + 0.02 * v], 2).astype(np.float32)   # BGR navy

    def frame(self, t, zoom=1.0):
        img = np.zeros((H, W, 3), np.float32)
        cx, cy, R = W / 2, H / 2, math.hypot(W, H) / 2
        for a, r0, s, b in zip(self.ang, self.r0, self.spd, self.b):
            z = (r0 * math.exp(SPEED * s * t)) % 1.0
            z2 = (r0 * math.exp(SPEED * s * (t - 0.6 / FPS))) % 1.0
            if z2 > z:
                continue
            r1 = z * R * zoom
            r2 = max(r1 - (6 + 26 * z), z2 * R * zoom)          # short dashes, not long lines (reference 18.4 s)
            p1 = (int(cx + r1 * math.cos(a)), int(cy + r1 * 0.7 * math.sin(a)))
            p2 = (int(cx + r2 * math.cos(a)), int(cy + r2 * 0.7 * math.sin(a)))
            k = b * (0.35 + 0.65 * z)
            cv2.line(img, p2, p1, (0.95 * k, 0.9 * k, 0.85 * k), 1, cv2.LINE_AA)
        big = np.zeros((H // 4, W // 4, 3), np.float32)
        for a, r0, s, bw in zip(self.bang, self.br0, self.bspd, self.bw):
            z = r0 + 1.6 * s * t
            r1 = z * R / 4 * zoom
            p = (cx / 4 + r1 * math.cos(a), cy / 4 + r1 * 0.72 * math.sin(a))
            L = 34 + 26 * z
            for q, th in ((1.0, 5), (0.7, 9), (0.4, 13)):          # a comet: thin bright head, wide soft tail
                e = (int(p[0] - L * q * math.cos(a)), int(p[1] - L * q * 0.72 * math.sin(a)))
                cv2.line(big, e, (int(p[0]), int(p[1])), (0.5 * bw, 0.42 * bw, 0.36 * bw), th, cv2.LINE_AA)
        big = cv2.GaussianBlur(big, (0, 0), 5.5)
        img += cv2.resize(big, (W, H)) * 1.15
        img = cv2.GaussianBlur(img, (0, 0), 0.8)
        return np.clip(self.base + img, 0, 1)


# ------------------------------------------------------------------ the 3D word
def letter_sprite(ch, size):
    """BGRA letter: a dark slate-blue face with a thin light edge and a shallow dark extrusion (reference 18.4 s:
    face ~RGB (50, 64, 96), edge ~(190, 215, 255) 2-3 px at 1080)."""
    f = ImageFont.truetype(FONT, size)
    bb = f.getbbox(ch)
    ext = max(3, int(size * 0.035))
    pad = int(size * 0.14) + ext
    w, h = bb[2] - bb[0] + 2 * pad, bb[3] - bb[1] + 2 * pad
    im = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    ox, oy = pad - bb[0], pad - bb[1]
    for k in range(ext, 0, -1):
        c = int(12 + 8 * (1 - k / ext))
        d.text((ox + k, oy + k), ch, font=f, fill=(c, c + 5, c + 18, 255))
    sw = max(2, int(size * 0.016))
    d.text((ox, oy), ch, font=f, fill=(50, 64, 96, 255), stroke_width=sw, stroke_fill=(190, 215, 255, 255))
    d.text((ox, oy), ch, font=f, fill=(50, 64, 96, 255))
    a = np.array(im).astype(np.float32) / 255.0
    return a[..., [2, 1, 0, 3]]                   # BGRA float


def warp_letter(spr, cx, cy, scale, yaw, roll=0.0, blur=0.0):
    """Letter homography: scale, yaw (a card turning about its vertical axis: one side nearer), roll. Returns
    (BGRA float canvas-size layer, alpha)."""
    h, w = spr.shape[:2]
    f = 1.6 * w
    ya = math.radians(yaw)
    pts = []
    for (x, y) in [(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)]:
        X, Z = x * math.cos(ya), x * math.sin(ya)
        k = f / (f + Z)
        pts.append((X * k, y * k))
    ca, sa = math.cos(math.radians(roll)), math.sin(math.radians(roll))
    dst = np.float32([(cx + scale * (px * ca - py * sa), cy + scale * (px * sa + py * ca)) for px, py in pts])
    src = np.float32([(0, 0), (w, 0), (w, h), (0, h)])
    M = cv2.getPerspectiveTransform(src, dst)
    lay = cv2.warpPerspective(spr, M, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
    if blur > 0.5:
        lay = cv2.GaussianBlur(lay, (0, 0), blur)
    return lay


def word_frame(bg, letters, adv, f, nfr, case_h):
    """Composite the word at frame f (float, for shutter samples)."""
    img = bg.copy()
    n = len(letters)
    base_scale_end = math.exp(-0.12 * max(0.0, f - 10) / max(1.0, nfr - 10))
    total = sum(adv)
    glow = np.zeros((H, W), np.float32)
    lays = []
    for i, (spr, a) in enumerate(zip(letters, adv)):
        fi = f - 3 - 0.9 * i                            # staggered one frame per letter
        if fi < -0.5:
            continue
        e = math.exp(-max(0.0, fi) / 2.0)
        s = (1.0 + 1.3 * e) * base_scale_end              # round 3: the fly-in starts ~2.3x (4.2x greyed the frame)
        yaw = (40.0 if i % 2 == 0 else -34.0) * e + (6.0 - 4.0 * min(1.0, max(0.0, f - 10) / 12.0)) * (1 - e)
        roll = (12.0 if i % 2 else -9.0) * e
        xoff = (sum(adv[:i]) + a / 2 - total / 2)
        cx = W / 2 + xoff * s * (1 + 0.25 * e) - 0.012 * W * max(0.0, f - 10) / 10.0
        cy = H * 0.48 + (0.10 * H * e if i % 2 else -0.06 * H * e) + 0.006 * H * max(0.0, f - 10) / 10.0
        lay = warp_letter(spr, cx, cy, s, yaw, roll, blur=6.0 * e)
        al = lay[..., 3:4] * min(1.0, 0.35 + fi / 1.5)
        img = img * (1 - al) + lay[..., :3] * al
        glow += lay[..., 3] * al[..., 0]
    g = cv2.GaussianBlur(glow, (0, 0), 14) * 0.07
    img = img + g[..., None] * np.array([1.0, 0.8, 0.6])
    return np.clip(img, 0, 1)


def word_plate(out, word, case='upper', nfr=20, seed=3, contract=True, wfrac=0.62):
    word = word.upper() if case == 'upper' else word.lower()
    size = int(0.185 * H / 0.70)                          # cap height 0.185 H at the settle (reference 18.4 s; Lilita caps 0.70)
    f = ImageFont.truetype(FONT, size)
    adv = [f.getlength(ch) + 0.10 * size for ch in word]  # the reference's wide tracking
    if sum(adv) > wfrac * W:                                # the whole word fits (critic: 'P' / 'E' left the frame at 18.30)
        size = int(size * wfrac * W / sum(adv))
        f = ImageFont.truetype(FONT, size)
        adv = [f.getlength(ch) + 0.10 * size for ch in word]
    letters = [letter_sprite(ch, size) for ch in word]
    tun = Tunnel(seed=seed)
    p = writer(out)
    for k in range(nfr):
        t = k / FPS
        z = math.exp(-0.07 * min(1.0, k / 20.0))           # the plate pulls out slightly
        acc = None
        subs = (-0.33, 0.0, 0.33) if 2 <= k <= 9 else (0.0,)
        for q in subs:
            bg = tun.frame((nfr / FPS - (t + q / FPS)) if contract else (t + q / FPS), zoom=z)
            if k < 3:
                # frames 0-2: the dark blurred burst out of the flash
                bg = cv2.GaussianBlur(bg, (0, 0), 12 - 3 * k) * (0.55 + 0.15 * k)
                im = bg
            else:
                im = word_frame(bg, letters, adv, k + q, nfr, 0.2)
            acc = im if acc is None else acc + im
        im = acc / len(subs)
        p.stdin.write((im * 255).astype(np.uint8).tobytes())
    p.stdin.close()
    p.wait()


# ------------------------------------------------------------------ ember plate
def _noise(rng, h, w, octaves=((90, 1.0), (40, 0.5), (18, 0.25))):
    acc = np.zeros((h, w), np.float32)
    for div, amp in octaves:
        n = rng.uniform(0, 1, (max(2, h // div), max(2, w // div))).astype(np.float32)
        acc += cv2.resize(cv2.GaussianBlur(n, (0, 0), 1.2), (w, h), interpolation=cv2.INTER_CUBIC) * amp
    acc -= acc.min()
    return acc / max(1e-6, acc.max())


def _walk(rng, x, y, n, step, ang):
    """A bent spark: a random walk that keeps its heading with sharp kinks (the reference's zig-zag sparks)."""
    pts = [(x, y)]
    for _ in range(n):
        if rng.random() < 0.35:
            ang += rng.choice([-1, 1]) * rng.uniform(1.6, 2.6)      # a kink
        else:
            ang += rng.uniform(-0.25, 0.25)
        x += step * math.cos(ang)
        y += step * math.sin(ang)
        pts.append((x, y))
    return np.array(pts, np.int32)


def star_plate(out, nfr=80, seed=3, speed=1.8, n=520):
    """The S2rz intro star field (0-2.4 s 'Ah!' / 'One More Thing!', and the dark star card before the entry): a dark navy
    field of thin light-blue streaks that CONTRACT toward the centre (reference ln-scale rate -1.2 .. -3 / s, steady);
    speed = ln / s.  Measured with the same similarity fit as the reference: -1.35 / s at speed 1.8."""
    rng = np.random.default_rng(seed)
    ang = rng.uniform(0, 2 * np.pi, n)
    lr0 = rng.uniform(0, 1, n) * 3.2
    bri = rng.uniform(0.45, 1.0, n)
    wid = rng.choice([1, 1, 2, 2, 3], n)
    cx, cy, R = W / 2, H / 2, math.hypot(W, H) / 2
    yy, xx = np.mgrid[0:H, 0:W]
    v = np.clip(1 - np.hypot((xx - cx) / W, (yy - cy) / H) * 1.7, 0, 1)
    base = np.stack([0.10 + 0.10 * v, 0.055 + 0.06 * v, 0.03 + 0.03 * v], 2).astype(np.float32)
    p = writer(out)
    lmin = math.log(0.035)
    for k in range(nfr):
        acc = None
        for q in (-0.3, 0.0, 0.3):
            t = (k + q) / FPS
            img = np.zeros((H, W, 3), np.float32)
            lw = lmin + np.mod(lr0 - speed * t, 3.2)
            for a, l, b, w in zip(ang, lw, bri, wid):
                r1 = math.exp(l) * R
                r2 = r1 * (1.0 + 0.10 + 0.05 * abs(speed))
                p1 = (int(cx + r1 * math.cos(a)), int(cy + r1 * math.sin(a)))
                p2 = (int(cx + r2 * math.cos(a)), int(cy + r2 * math.sin(a)))
                kk = b * min(1.0, 0.25 + 1.2 * math.exp(l))
                cv2.line(img, p1, p2, (1.0 * kk, 0.86 * kk, 0.62 * kk), int(w), cv2.LINE_AA)
            glow = cv2.GaussianBlur(img, (0, 0), 9) * 0.5
            im = np.clip(base + img + glow, 0, 1)
            acc = im if acc is None else acc + im
        p.stdin.write((acc / 3 * 255).astype(np.uint8).tobytes())
    p.stdin.close()
    p.wait()


def ember_plate(out, nfr=30, seed=1):
    """Round 3 (critic: vertical red lines and round discs on brown read cheap): black night, a textured teal haze in the
    lower half, two sparkler cores (white-hot centre, big orange bloom, a short tapered trail fading orange -> dark
    green), 1-3 big soft orange / amber bokeh discs that come and go, a spray of tiny sparks around each core and
    bright zig-zag spark bursts (branching random walks), animated at 15 fps like the reference (every frame shown
    twice), with a slow push."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    nz = _noise(rng, H, W)
    haze = np.clip(1 - np.abs(yy / H - 0.74) / 0.46, 0, 1) ** 1.2 * (0.25 + 0.75 * nz)       # textured over the lower 2/3
    base = np.stack([haze * 0.36, haze * 0.27, haze * 0.10], 2).astype(np.float32)          # BGR teal
    cores = [(0.30 * W, 0.62 * H), (0.66 * W, 0.70 * H)]
    p = writer(out)
    last = None
    bok = []
    for k in range(nfr):
        if k % 2 == 1 and last is not None:
            p.stdin.write(last)
            continue
        t = k / FPS
        img = base.copy()
        # bokeh: 1-3 big soft discs, each lives 2-6 plate frames
        bok = [b for b in bok if b[4] > k]
        while len(bok) < 1 + (k // 6) % 3:
            bok.append((rng.uniform(0.1, 0.9) * W, rng.uniform(0.2, 0.75) * H, rng.uniform(90, 210),
                        rng.choice([(0.10, 0.30, 0.85), (0.08, 0.45, 0.80), (0.35, 0.30, 0.55)]), k + int(rng.integers(4, 12))))
        lay = np.zeros((H // 4, W // 4, 3), np.float32)
        for (x, y, r, c, _) in bok:
            cv2.circle(lay, (int(x / 4), int(y / 4)), int(r / 4), c, -1, cv2.LINE_AA)
        lay = cv2.GaussianBlur(lay, (0, 0), 9.0) * 0.55
        img += cv2.resize(lay, (W, H))
        spark = np.zeros((H, W, 3), np.float32)
        glow = np.zeros((H // 4, W // 4, 3), np.float32)
        for j, (x, y) in enumerate(cores):
            x += 10 * math.sin(2.3 * t + j * 2)
            y += 0.02 * H * t + 6 * math.cos(3.1 * t + j)
            L = 0.23 * H * (0.9 + 0.2 * math.sin(4 * t + j))
            for q in range(12):                                   # tapered trail, orange -> dark green
                a0, a1 = q / 12, (q + 1) / 12
                col = (0.10 + 0.15 * a0, 0.35 - 0.1 * a0, 0.95 - 0.8 * a0)
                cv2.line(spark, (int(x), int(y - L * a0)), (int(x), int(y - L * a1)), tuple(c * (1 - 0.8 * a0) for c in col),
                         max(2, int(9 * (1 - a0))), cv2.LINE_AA)
            cv2.circle(glow, (int(x / 4), int(y / 4)), 14, (0.12, 0.42, 1.0), -1, cv2.LINE_AA)
            cv2.circle(spark, (int(x), int(y)), 11, (0.85, 0.97, 1.0), -1, cv2.LINE_AA)
            for _ in range(40):                                   # the tiny spark spray
                a = rng.uniform(0, 6.28)
                r = rng.uniform(15, 120)
                cv2.circle(spark, (int(x + r * math.cos(a)), int(y + r * math.sin(a) * 0.7)), 1, (0.4, 0.75, 1.0), -1, cv2.LINE_AA)
            nb = int(rng.integers(0, 3)) if k % 4 else int(rng.integers(2, 5))
            for _ in range(nb):                                   # bright zig-zag bursts
                pts = _walk(rng, x, y, int(rng.integers(4, 9)), rng.uniform(18, 45), rng.uniform(0, 6.28))
                cv2.polylines(spark, [pts], False, (0.35, 0.78, 1.0), 3, cv2.LINE_AA)
        glow = cv2.GaussianBlur(glow, (0, 0), 12.0) * 3.2
        img += cv2.resize(glow, (W, H))
        img = np.clip(img + cv2.GaussianBlur(spark, (0, 0), 1.2) + cv2.GaussianBlur(spark, (0, 0), 10) * 1.6, 0, 1)
        zz = 1.0 + 0.10 * t
        M = cv2.getRotationMatrix2D((W / 2, H * 0.55), 0, zz)
        img = cv2.warpAffine(img, M, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT101)
        last = (img * 255).astype(np.uint8).tobytes()
        p.stdin.write(last)
    p.stdin.close()
    p.wait()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', required=True)
    ap.add_argument('--word')
    ap.add_argument('--case', default='upper')
    ap.add_argument('--frames', type=int, default=20)
    ap.add_argument('--embers', action='store_true')
    ap.add_argument('--seed', type=int, default=1)
    a = ap.parse_args()
    if a.embers:
        ember_plate(a.out, a.frames, a.seed)
    else:
        word_plate(a.out, a.word, a.case, a.frames, seed=a.seed + 2)
    print('ok', a.out)


if __name__ == '__main__':
    main()
