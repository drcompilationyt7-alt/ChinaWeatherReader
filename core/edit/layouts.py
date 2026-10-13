#!/usr/bin/env python3
"""
Layouts for the edit engine.

Frame modes (the whole video: where the edit picture sits in the output frame)
  full             the picture is the frame
  rot90            the 16:9 master rotated 90 deg clockwise into 9:16 (sound edits, Golden Hour)
  band16x9         a 16:9 band (1080x608 at y .323 H) on black, caption above (GHB collections)
  band1x1          a square band (1080x1080 at y .20 H) on black, its last 12% fading to black (K-pop)
  letterbox_black  1080x648 at y 636 on black (sound edits)
  window2x3/3x4    a 1080x1620 / 1080x1440 window centred over the same picture blurred x0.35 (character)
  letterbox_blur   full-width 16:9 zoomed 1.22 (1080x740) over its own blurred copy (the meme drop look)

Shot layouts (special ones; fill / fit are in the engine): postcard (photo on paper), card3d (3D
tilted card), split2 / split3 (panels), hearts (heart-masked panels).

Scenes (overlays that replace the picture for a while): postcard_intro (the K-pop postcard intro,
measured keyframes of mFHp6Dd2ISk, retimed to the drop), text_on_black (lyric / title words on
black), music_card (album-style intro card for long videos).
"""
import math
import os
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'meme'))
sys.path.insert(0, HERE)
import edit_fx as fx  # noqa: E402
import text as T  # noqa: E402

FRAME_MODES = ('full', 'rot90', 'band16x9', 'band1x1', 'letterbox_black', 'window2x3', 'window3x4', 'letterbox_blur')


def ease(u, kind):
    u = min(1.0, max(0.0, u)) if kind != 'extrapolate' else u
    if kind == 'inout_sine':
        return (1 - math.cos(math.pi * u)) / 2
    if kind == 'in_quad':
        return u * u
    if kind == 'out_sine':
        return math.sin(u * math.pi / 2)
    if kind == 'out_quad':
        return 1 - (1 - u) ** 2
    if kind == 'out_cubic':
        return 1 - (1 - u) ** 3
    return u


class Frame:
    """Output frame geometry for a frame mode: canvas size, where it sits, and the final composition."""

    def __init__(self, mode, out_size):
        W, H = out_size
        self.out = out_size
        self.mode = mode if mode in FRAME_MODES else 'full'
        if W > H and self.mode not in ('full',):
            self.mode = 'full'                     # 16:9 output: the picture is the frame
        self.pos = (0, 0)
        self.fade_band = False
        if self.mode == 'rot90':
            self.canvas = (H, W)
        elif self.mode == 'band16x9':
            self.canvas = (W, int(round(W * 9 / 16 / 2) * 2))
            self.pos = (0, int(round(0.323 * H)))
        elif self.mode == 'band1x1':
            self.canvas = (W, W)
            self.pos = (0, int(round(0.20 * H)))
            self.fade_band = True
        elif self.mode == 'letterbox_black':
            self.canvas = (W, int(round(W * 0.6)))
            self.pos = (0, int(round((H - W * 0.6) / 2)))
        elif self.mode in ('window2x3', 'window3x4'):
            ch = int(round(W * (1.5 if self.mode == 'window2x3' else 4 / 3) / 2) * 2)
            self.canvas = (W, ch)
            self.pos = (0, (H - ch) // 2)
        elif self.mode == 'letterbox_blur':
            ch = int(round(W * 9 / 16 * 1.22 / 2) * 2)
            self.canvas = (W, ch)
            self.pos = (0, int((H - ch) * 0.48))
        else:
            self.canvas = (W, H)
        self._fade = None

    def compose(self, pic):
        """Canvas picture -> output frame (BGR)."""
        W, H = self.out
        if self.mode == 'full':
            return pic
        if self.mode == 'rot90':
            return cv2.rotate(pic, cv2.ROTATE_90_CLOCKWISE)
        if self.mode in ('window2x3', 'window3x4', 'letterbox_blur'):
            small = cv2.resize(pic, (max(8, W // 12), max(8, int(W // 12 * pic.shape[0] / pic.shape[1]))), interpolation=cv2.INTER_AREA)
            k = (H // 12) / small.shape[0]
            cover = cv2.resize(small, (max(small.shape[1], int(small.shape[1] * k) + 2), H // 12), interpolation=cv2.INTER_LINEAR)
            x0 = (cover.shape[1] - small.shape[1]) // 2
            cover = cv2.GaussianBlur(cover[:, x0:x0 + small.shape[1]], (0, 0), 3.3)
            frame = cv2.convertScaleAbs(cv2.resize(cover, (W, H), interpolation=cv2.INTER_LINEAR),
                                        alpha=0.35 if self.mode != 'letterbox_blur' else 0.26)
        else:
            frame = np.zeros((H, W, 3), np.uint8)
        x, y = self.pos
        ch, cw = pic.shape[:2]
        if self.fade_band:
            if self._fade is None or self._fade.shape[0] != ch:
                g = np.ones(ch, np.float32)
                n = int(ch * 0.12)
                g[ch - n:] = np.linspace(1, 0, n)
                self._fade = cv2.merge([(g * 255).astype(np.uint8)[:, None].repeat(cw, 1)] * 3)
            pic = cv2.multiply(pic, self._fade, scale=1 / 255.0)
        frame[y:y + ch, x:x + cw] = pic
        return frame


# ---------------------------------------------------------------- special shot layouts

class Postcard:
    """
    The shot printed as a photo on paper: off-white paper, a soft pink paint stroke, the photo (55% W,
    4% white border, shadow) rotated -12..-16 deg, sliding up from below out of a white flash, then a
    slow 3% push. variant 'pastel' (general intro): -8 deg, pastel paper, flower stickers.
    """

    def __init__(self, eng, shot, canvas):
        self.eng, self.shot, self.canvas = eng, shot, canvas
        rng = np.random.default_rng(shot['seed'])
        p = shot.get('postcard') or {}
        self.variant = p.get('variant', 'character')
        # The whole card (photo + a 2.5 %W paper border, rotated) stays INSIDE the frame: <= 0.82 W and <= 0.72 H,
        # +-4..6 deg. At -12..-16 deg and 0.58 W x 0.70 W the card was cut off by a 16:9 canvas on two sides,
        # so the "photo held inside a paper frame" idea was lost. `angle` / `width` still override.
        self.ang = float(p.get('angle', -8.0 if self.variant == 'pastel' else float(rng.uniform(4, 6)) * (1 if rng.random() < 0.5 else -1)))
        self.bw = bool(p.get('bw', False))
        self.paper_col = (228, 232, 246) if self.variant == 'pastel' else (234, 239, 244)
        w, h = canvas
        ar = 1.2 if h > w else 0.62
        self.width = float(p.get('width', min(0.70, 0.60 * h / (ar * w))))
        self.photo_size = (int(w * self.width), int(w * self.width * ar))
        self.drift = (1 if rng.random() < 0.5 else -1) * float(p.get('drift', 0.05))      # W / s
        self.stroke = fx.brush_stroke(canvas, (181, 163, 232), 0.4, seed=shot['seed'] % 97, box=(0.05, 0.3, 0.95, 0.65))
        self.entry = p.get('entry', 'slide_up' if self.variant != 'pastel' else 'none')

    def render(self, t, cam):
        w, h = self.canvas
        bg = fx.paper(self.canvas, self.paper_col, 0.03)
        bg = fx.blend_mask(bg, self.stroke[0], self.stroke[1])
        since = t - self.shot['t0']
        push = 1 + 0.03 * max(0.0, since) / max(0.5, self.shot['t1'] - self.shot['t0'])
        pic = self.eng.place(self.shot, t, self.photo_size, cam=(math.log(push), 0, 0, 0))
        if self.bw:
            pic = fx.saturation_u8(pic, 0.0)
        card = fx.bordered(pic, 0.025 * w / max(1.0, pic.shape[1] + 0.05 * w))
        dy = 0.0
        if self.entry == 'slide_up' and since < 5 / 30:
            dy = 0.6 * h * (1 - ease(since / (5 / 30), 'out_cubic'))
        dur = max(0.5, self.shot['t1'] - self.shot['t0'])
        u = min(1.0, since / dur)
        dx = self.drift * w * (since - dur / 2)                     # 5 %W/s, centred on the block
        ang = self.ang + 3.0 * (2 * u - 1) * (1 if self.drift > 0 else -1)
        lay, m = fx.affine_layer(card, w * 0.5 + dx, h * 0.47 + dy, card.shape[1], ang, self.canvas)
        if self.entry == 'slide_up' and dy > 0.004 * h:
            # round 4: the card RISES in over 5 frames with a vertical smear 6 %H -> 0 (mGz9 8.53-8.67)
            k = int(max(3, 0.06 * h * dy / (0.6 * h))) | 1
            lay = cv2.blur(lay, (1, k))
            m = cv2.blur(m, (1, k))
        if self.variant != 'pastel':
            bg = self._decor(bg, since)
        bg = fx.shadow_under(bg, m, 0.02 * w, (0.01 * w, 0.015 * w), 0.35)
        out = fx.blend_mask(bg, lay, m)
        if self.variant != 'pastel':
            out = self._stickers(out, w * 0.5 + dx, h * 0.47 + dy, card.shape[1], card.shape[0], ang)
        if self.variant == 'pastel':
            for i, (x, y) in enumerate(((0.18, 0.2), (0.82, 0.78), (0.8, 0.16))):
                out = _flower(out, x * w, y * h, 0.07 * w, i)
        if self.entry == 'slide_up' and since < 1 / 30:
            out = fx.fill_mix(out, (255, 255, 255), 0.83 * (1 - since / (1 / 30)))     # capped white, <= 1 frame @30
        return out

    def _decor(self, bg, since):
        """A pink brush BLOB behind the card (alpha 0.8, 35 %W) and a light bloom top-right (25 %W, alpha 0.35)."""
        w, h = self.canvas
        if not hasattr(self, '_blob'):
            rng = np.random.default_rng(self.shot['seed'] + 3)
            m = np.zeros((h // 4, w // 4), np.float32)
            cx, cy = 0.30 * w / 4, 0.66 * h / 4
            for _ in range(26):
                a, r = float(rng.uniform(0, 6.283)), float(rng.uniform(0, 0.11)) * w / 4
                cv2.circle(m, (int(cx + math.cos(a) * r * 1.5), int(cy + math.sin(a) * r * 0.7)),
                           int(float(rng.uniform(0.03, 0.07)) * w / 4), 1.0, -1, cv2.LINE_AA)
            m = np.clip((cv2.GaussianBlur(m, (0, 0), 2.0) - 0.3) * 4.0, 0, 1)
            self._blob = cv2.resize(m, (w, h), interpolation=cv2.INTER_LINEAR) * 0.8
            yy, xx = np.mgrid[0:h // 8 + 1, 0:w // 8 + 1].astype(np.float32)
            g = np.exp(-(((xx * 8 - 0.86 * w) / (0.125 * w)) ** 2 + ((yy * 8 - 0.14 * h) / (0.125 * w)) ** 2))
            self._bloom = cv2.resize(g, (w, h))[..., None] * np.array([0.9, 0.95, 1.0], np.float32) * 0.35
        bg = fx.blend_mask(bg, np.full_like(bg, (196, 170, 240)), self._blob)
        return fx.screen_layer(bg, self._bloom, 1.0)

    def _stickers(self, out, cx, cy, cw, ch, ang):
        """2-3 small heart stickers at the card's lower-right corner."""
        w, h = self.canvas
        a = math.radians(-ang)
        for k, (ox, oy, sz, col) in enumerate(((0.46, 0.44, 0.060, (150, 120, 240)), (0.37, 0.52, 0.036, (190, 170, 250)),
                                               (0.52, 0.33, 0.028, (245, 235, 255)))):
            px = cx + (ox * cw) * math.cos(a) + (oy * ch) * math.sin(a)
            py = cy - (ox * cw) * math.sin(a) + (oy * ch) * math.cos(a)
            m = fx.poly_mask(self.canvas, fx.heart_poly(60), px, py, sz * w * 0.5, rot=ang + 12 * (k - 1), blur=1)
            out = fx.shadow_under(out, m, 0.004 * w, (0.002 * w, 0.003 * w), 0.3)
            out = fx.blend_mask(out, np.full_like(out, col), m)
        return out


def _flower(img, cx, cy, r, seed):
    """A small five-petal flower sticker (pastel)."""
    cols = [(203, 192, 255), (180, 229, 255), (230, 216, 173)]
    c = cols[seed % 3]
    lay = img.copy()
    for k in range(5):
        a = k * 2 * math.pi / 5 + seed
        cv2.ellipse(lay, (int(cx + math.cos(a) * r * 0.5), int(cy + math.sin(a) * r * 0.5)), (int(r * 0.45), int(r * 0.3)),
                    math.degrees(a), 0, 360, c, -1, cv2.LINE_AA)
    cv2.circle(lay, (int(cx), int(cy)), int(r * 0.25), (120, 220, 255), -1, cv2.LINE_AA)
    return cv2.addWeighted(img, 0.15, lay, 0.85, 0)


class Card3D:
    """
    The shot as a plane rotated about Y (15-22 deg) and Z (-6..-10 deg), scaling 0.95 -> 0.78 over 0.3 s,
    a light edge, over the same shot blurred (3% W) x0.35 with sparkles.
    """

    def __init__(self, eng, shot, canvas):
        self.eng, self.shot, self.canvas = eng, shot, canvas
        rng = np.random.default_rng(shot['seed'])
        # ry 22-28 + rx 6-9 deg at focal 1.0 gives a near / far edge-height ratio of 1.25-1.4: at 15-22 deg and the
        # default focal 1.8 the two edges measured EQUAL and the card read as a rolled rectangle, not a 3D card.
        self.ry = float(rng.uniform(22, 28)) * (1 if rng.random() < 0.5 else -1)
        self.rx = float(rng.uniform(6, 9))
        self.rz = -float(rng.uniform(4, 7))
        self.sparks = fx.ParticleField('sparkles', n=40, seed=shot['seed'] % 1000, size=canvas)

    def render(self, t, cam):
        w, h = self.canvas
        pic = self.eng.place(self.shot, t, self.canvas, cam=cam)
        # the ground is a FLAT set (the shot enlarged x1.6, blurred 8 %W, x0.28) so the card separates from it
        small = cv2.resize(pic, (max(8, w // 8), max(8, h // 8)), interpolation=cv2.INTER_AREA)
        sh_, sw_ = small.shape[:2]
        small = small[int(sh_ * 0.19):int(sh_ * 0.81), int(sw_ * 0.19):int(sw_ * 0.81)]
        legacy = bool((self.shot.get('card3d') or {}).get('legacy'))
        small = cv2.GaussianBlur(small, (0, 0), max(1.0, (0.08 if legacy else 0.03) * w / 8 / 1.6))
        bg = fx.gain_u8(cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR), 0.28 if legacy else 0.55)
        if legacy:
            bg = fx.screen_layer(bg, self.sparks.layer(t), 0.7)
        since = max(0.0, t - self.shot['t0'])
        k = ease(since / 0.2, 'out_cubic')
        s = (0.80 - 0.04 * k) if legacy else (0.90 - 0.05 * k)   # YH 20.6 s: the card fills ~85 % of the frame
        if not legacy and since < 4 / 30.0:                      # defocus 0.6, 0.3, 0.1, 0 %W - crisp by +3 frames
            f = since * 30.0
            seq = (0.006, 0.003, 0.001, 0.0, 0.0)
            i = min(3, int(f))
            sg = (seq[i] * (1 - (f - i)) + seq[i + 1] * (f - i)) * w
            if sg > 0.3:
                pic = fx.gauss_px(pic, sg)
        b = max(2, int(0.015 * w))
        edge = cv2.copyMakeBorder(pic, b, b, b, b, cv2.BORDER_CONSTANT, value=(246, 246, 246))
        quad = fx.card_quad(self.canvas, w / 2 + 0.008 * w * since, h / 2, w * s, h * s, rx=self.rx, ry=self.ry, rz=self.rz, focal=1.0)
        lay, m = fx.warp_to_quad(edge, quad, self.canvas)
        bg = fx.shadow_under(bg, m, 0.02 * w, (0.012 * w, 0.02 * w), 0.35)
        return fx.blend_mask(bg, lay, m)


class Split:
    """Two or three shots side by side (16:9) or stacked (9:16), 6 px dark gaps, each with its own slow push."""

    def __init__(self, eng, shot, canvas, n=2):
        self.eng, self.shot, self.canvas, self.n = eng, shot, canvas, n
        w, h = canvas
        gap = max(4, int(6 * w / 1920 if w > h else 6))
        self.rects = []
        if w >= h:
            pw = (w - gap * (n - 1)) // n
            self.rects = [(i * (pw + gap), 0, pw, h) for i in range(n)]
        else:
            ph = (h - gap * (n - 1)) // n
            self.rects = [(0, i * (ph + gap), w, ph) for i in range(n)]

    def render(self, t, cam):
        w, h = self.canvas
        out = np.full((h, w, 3), 12, np.uint8)
        panels = [None] + list(self.shot.get('panels') or [])
        since = max(0.0, t - self.shot['t0'])
        for i, (x, y, pw, ph) in enumerate(self.rects):
            src = panels[i] if i < len(panels) else None
            # each panel has its OWN slow push, ~1.5 %/s, in opposite directions. The receding panel starts
            # pre-zoomed so it never goes under its half (a negative ln s exposed the mirrored plate border).
            dur = max(0.3, self.shot['t1'] - self.shot['t0'])
            push = 0.015 * since if i % 2 == 0 else 0.015 * (dur - since) + 0.01
            f = since * 30.0
            settle = math.log(1.15) * math.exp(-f / 2.0) if not self.shot.get('split_legacy') else 0.0
            c = (max(0.0, cam[0]) + push + settle, cam[1], cam[2], cam[3])
            pic = self.eng.place(self.shot, t, (pw, ph), cam=c, panel=src)
            if f < 4.0 and not self.shot.get('split_legacy'):
                # i4fS 24.30-24.43: each panel arrives SMEARED from its own side, 12 %W -> 0 over 4 frames
                ln = 0.12 * w * (1.0 - f / 4.0) ** 1.5
                if ln >= 2:
                    pic = fx.line_blur(pic, ln, 0.0 if w >= h else 90.0)
                    off = int(ln * 0.5) * (1 if i % 2 == 0 else -1)
                    pic = np.roll(pic, off, 1 if w >= h else 0)
            out[y:y + ph, x:x + pw] = pic
        return out


class Hearts:
    """Soft romance: 2-3 heart-shaped windows with different shots drifting on black with gold dust."""

    def __init__(self, eng, shot, canvas):
        self.eng, self.shot, self.canvas = eng, shot, canvas
        rng = np.random.default_rng(shot['seed'])
        n = 1 + min(2, len(shot.get('panels') or []))
        w, h = canvas
        # spread ACROSS the frame (they were clustered and overlapping at bottom centre on a 16:9 canvas)
        spots = ([(0.5, 0.30), (0.30, 0.62), (0.72, 0.70)] if h > w else [(0.5, 0.52), (0.19, 0.50), (0.81, 0.47)])[:n]
        self.land = w >= h
        self.hearts = [(x * w, y * h, float(rng.uniform(-8, 8)), float(rng.uniform(0.05, 0.1)) * (1 if rng.random() < .5 else -1))
                       for x, y in spots]
        self.dust = fx.ParticleField('gold_dust', n=70, seed=shot['seed'] % 777, size=canvas)
        self.shape = fx.heart_poly(120)

    def render(self, t, cam):
        w, h = self.canvas
        out = np.zeros((h, w, 3), np.uint8)
        out = fx.screen_layer(out, self.dust.layer(t), 0.9)
        panels = [None] + list(self.shot.get('panels') or [])
        since = max(0.0, t - self.shot['t0'])
        hw0 = (0.27 * w if self.land else 0.38 * w) if len(self.hearts) > 1 else (0.5 * w if self.land else 0.8 * w)
        if not self.shot.get('hearts_legacy'):
            return self._render_v2(t, cam, panels, since)
        dur = max(0.4, self.shot['t1'] - self.shot['t0'])
        ue = min(1.0, max(0.0, (since - (dur - 0.3)) / 0.3))       # the block ends by one heart scaling up to fill
        for i, (cx, cy, rot, drift) in reversed(list(enumerate(self.hearts))):
            hw = hw0 * (1.0 + 2.5 * ue ** 3) if i == 0 else hw0
            x = min(0.84 * w, max(0.16 * w, cx + drift * w * (since - dur / 2) * 0.6))
            if i == 0 and ue > 0:
                x, cy = x + (w / 2 - x) * ue, cy + (h / 2 - cy) * ue
            r = rot + 3 * math.sin(since * 1.3 + i)
            size = (int(hw * 1.1), int(hw * 1.1))
            pic = self.eng.place(self.shot, t, size, cam=cam, panel=panels[i] if i < len(panels) else None)
            big = np.zeros_like(out)
            x0, y0 = int(x - size[0] / 2), int(cy - size[1] / 2)
            xa, ya, xb, yb = max(0, x0), max(0, y0), min(w, x0 + size[0]), min(h, y0 + size[1])
            if xb <= xa or yb <= ya:
                continue
            big[ya:yb, xa:xb] = pic[ya - y0:yb - y0, xa - x0:xb - x0]
            m = fx.poly_mask(self.canvas, self.shape, x, cy, hw * 0.52, rot=r, blur=2)
            out = fx.blend_mask(out, big, m)
            out = fx.screen_layer(out, fx.heart_outline(self.canvas, x, cy, hw * 1.04, thick=max(2, int(0.005 * w)), color=(238, 228, 255), glow=0.01 * w), 0.9)   # rfsj 26.7 s: a THIN pale stroke + 1 %W glow
        return out


def _hearts_v2(self, t, cam, panels, since):
    """
    rfsj 26.50-27.00 s, read frame by frame: the shot plays FULL FRAME while a soft vignette iris closes on it
    (7 frames, radius 0.9 -> 0.3 W), the iris becomes a heart over the last 2, and the heart then holds as a MASK
    on the same full-frame picture (the face never moves or rescales) on black, with a soft pale-pink glow rim,
    two short ribbon arcs crossing under its tip and very faint dust. One heart; shot["hearts_multi"] adds the
    two small blurred side hearts from the panels.
    """
    w, h = self.canvas
    f = since * 30.0
    pic = self.eng.place(self.shot, t, self.canvas, cam=cam)
    try:
        fo = self.shot['_cam'].get('focus') or self.eng.foot(self.shot, None).prep.focus
        fxp, fyp = float(fo[0]), float(fo[1])
    except Exception:
        fxp, fyp = 0.5, 0.42
    hero_w = 0.46 * w if self.land else 0.80 * w
    cx = min(w - hero_w * 0.55, max(hero_w * 0.55, fxp * w))
    cy = min(h - hero_w * 0.55, max(hero_w * 0.50, fyp * h + 0.04 * h))
    dur = max(0.4, self.shot['t1'] - self.shot['t0'])
    ue = min(1.0, max(0.0, (since - (dur - 0.3)) / 0.3))
    hw = hero_w * (1.0 + 2.5 * ue ** 3)
    heart = fx.poly_mask(self.canvas, self.shape, cx, cy, hw * 0.52, rot=0.0, blur=4)
    k = min(1.0, f / 7.0)
    if k < 1.0:
        r = (0.66 - 0.38 * k) * max(w, h) * (1.0 if w >= h else 0.75)      # visible from the first frame, as in the reference
        yy, xx = np.mgrid[0:h // 4 + 1, 0:w // 4 + 1].astype(np.float32)
        d = np.sqrt((xx * 4 - cx) ** 2 + (yy * 4 - cy) ** 2)
        iris = cv2.resize(np.clip((r - d) / max(8.0, 0.22 * w * (1 - 0.6 * k)), 0, 1), (w, h))
        morph = min(1.0, max(0.0, (f - 5.0) / 2.0))
        m = np.minimum(iris, 1.0) * (1 - morph) + heart * morph
    else:
        m = heart
    out = (fx.gain_u8(pic, 0.92).astype(np.float32) * m[..., None]).astype(np.uint8)
    deco = min(1.0, max(0.0, (f - 5.0) / 3.0))
    if deco > 0:
        out = fx.screen_layer(out, self.dust.layer(t), 0.3 * deco)
        lay = np.zeros((h // 2, w // 2, 3), np.float32)
        pts = self.shape * (hw * 1.04 / 4) + np.array([cx / 2, cy / 2], np.float32)
        cv2.polylines(lay, [np.round(pts * 16).astype(np.int32)], True, (0.86, 0.84, 0.96), max(1, int(0.002 * w)), cv2.LINE_AA, shift=4)
        tipx, tipy = cx / 2, (cy + hw * 0.50) / 2
        for sgn in (1, -1):                                   # two short ribbon arcs crossing under the tip
            us = np.linspace(0, 1, 24)
            ax = tipx + sgn * (us - 0.35) * hw * 0.42 / 2
            ay = tipy + (0.10 - 0.55 * (us - 0.35) ** 2 * 1.2) * hw * 0.22 / 2 + (us - 0.35) * hw * 0.16 / 2
            cv2.polylines(lay, [np.round(np.stack([ax, ay], 1) * 16).astype(np.int32)], False, (0.7, 0.66, 0.8), max(1, int(0.0012 * w)), cv2.LINE_AA, shift=4)
        lay = lay * 0.9 + cv2.GaussianBlur(lay, (0, 0), max(1.0, 0.004 * w)) * 1.6 + cv2.GaussianBlur(lay, (0, 0), max(1.0, 0.012 * w)) * 1.2
        out = fx.screen_layer(out, cv2.resize(lay, (w, h)), 0.85 * deco)
    if self.shot.get('hearts_multi'):
        for i, (px, py) in enumerate((((0.16 if self.land else 0.22) * w, 0.70 * h), ((0.84 if self.land else 0.78) * w, 0.28 * h))):
            if i + 1 >= len(panels) or deco <= 0:
                continue
            sw_ = int(0.2 * w * (1.0 if self.land else 1.5)) // 2 * 2
            sp = fx.gain_u8(fx.gauss_px(self.eng.place(self.shot, t, (sw_, sw_), cam=cam, panel=panels[i + 1]), 0.003 * w), 0.9)
            big = np.zeros_like(out)
            x0, y0 = int(px - sw_ / 2), int(py - sw_ / 2)
            xa, ya, xb, yb = max(0, x0), max(0, y0), min(w, x0 + sw_), min(h, y0 + sw_)
            if xb <= xa or yb <= ya:
                continue
            big[ya:yb, xa:xb] = sp[ya - y0:yb - y0, xa - x0:xb - x0]
            out = fx.blend_mask(out, big, fx.poly_mask(self.canvas, self.shape, px, py, sw_ * 0.47, blur=3) * 0.7 * deco)
    return out


Hearts._render_v2 = _hearts_v2


def subject_matte(img):
    """
    Rough subject mask for cutout reveals (no segmentation model on the runner): GrabCut seeded with
    the middle of the frame at quarter size, feathered. Returns float mask (h, w) or None.
    """
    # round 4: a real segmentation model first (core/edit/matte.py: IS-Net anime / general through onnxruntime);
    # the GrabCut below is only the fallback when onnxruntime or the model file is missing.
    try:
        import matte as MT
        mm = MT.segment(img)
        if mm is not None:
            return mm
    except Exception:  # noqa: BLE001
        pass
    h, w = img.shape[:2]
    small = cv2.resize(img, (w // 4, h // 4), interpolation=cv2.INTER_AREA)
    sh, sw = small.shape[:2]
    mask = np.zeros((sh, sw), np.uint8)
    rect = (int(sw * 0.14), int(sh * 0.06), int(sw * 0.72), int(sh * 0.94))
    try:
        bgd, fgd = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
        cv2.grabCut(small, mask, rect, bgd, fgd, 4, cv2.GC_INIT_WITH_RECT)
    except cv2.error:
        return None
    m = np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 1.0, 0.0).astype(np.float32)
    n, lab, stats, _ = cv2.connectedComponentsWithStats((m > 0.5).astype(np.uint8))
    if n > 1:
        k = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        m = (lab == k).astype(np.float32)
    if m.mean() < 0.05 or m.mean() > 0.85:
        return None
    m = cv2.GaussianBlur(m, (0, 0), 1.0)
    return cv2.resize(m, (w, h), interpolation=cv2.INTER_LINEAR)


# ---------------------------------------------------------------- scenes

CARD_KEYS = {
    # mFHp6Dd2ISk postcard intro (times from the video start; the drop downbeat at 5.98 s). [cx, cy, ang]
    'card1': dict(visible=(0.0, 1.45), rest=(0.526, 0.547, 0.4),
                  pose=[(0.333, 1.1, (0.526, 0.547, 0.4), (0.337, 0.036, 12.3), 'in_quad', True)],
                  width=[(0.3, 0.97, 0.739, 0.617, 'out_sine')]),
    'card2': dict(visible=(0.3, 2.95),
                  pose=[(0.317, 1.767, (1.221, 1.371, -24.2), (0.509, 0.504, 0.4), 'inout_sine', False),
                        (1.783, 2.65, (0.509, 0.504, 0.4), (-0.094, 0.603, 13.4), 'in_quad', True)],
                  width=0.614),
    'card3': dict(visible=(1.9, 5.35),
                  pose=[(1.917, 3.267, (1.358, -0.02, -22.1), (0.513, 0.502, -0.1), 'inout_sine', False),
                        (3.3, 4.95, (0.513, 0.502, -0.1), (0.184, -0.081, -10.1), 'inout_sine', False)],
                  width=[(3.3, 4.95, 0.615, 0.444, 'inout_sine')]),
    'card4': dict(visible=(3.8, 5.98),
                  pose=[(3.833, 4.817, (0.696, 1.017, 11.5), (0.481, 0.532, 7.8), 'inout_sine', False),
                        (5.0, 5.417, (0.481, 0.532, 7.8), (0.465, 0.547, 4.0), 'inout_sine', False),
                        (5.417, 5.933, (0.465, 0.547, 4.0), (0.483, 0.503, -1.1), 'inout_sine', False)],
                  width=[(3.83, 4.82, 0.51, 0.44, 'inout_sine'), (5.0, 5.93, 0.44, 1.09, 'inout_sine')]),
}
REF_DROP = 5.98


def _eval_pose(spec, t):
    """(cx, cy, ang) of a card at reference time t: piecewise eased segments, the last one may extrapolate."""
    segs = spec['pose']
    if t <= segs[0][0]:
        return segs[0][2]
    for i, (a, b, p0, p1, kind, extra) in enumerate(segs):
        nxt = segs[i + 1][0] if i + 1 < len(segs) else None
        if nxt is not None and t >= nxt:
            continue
        u = (t - a) / max(1e-6, b - a)
        if u <= 1:
            k = ease(u, kind)
        elif extra:
            k = u * u if kind == 'in_quad' else u     # keeps accelerating until the card is off-frame
        else:
            k = 1.0
        return tuple(p0[j] + (p1[j] - p0[j]) * k for j in range(3))
    return segs[-1][3]


def _eval_width(spec, t):
    wd = spec['width']
    if not isinstance(wd, list):
        return wd
    if t <= wd[0][0]:
        return wd[0][2]
    for i, (a, b, w0, w1, kind) in enumerate(wd):
        if t <= b:
            return w0 + (w1 - w0) * ease((t - a) / max(1e-6, b - a), kind)
        if i + 1 < len(wd) and t < wd[i + 1][0]:
            return w1
    return wd[-1][3]


def flat_look(img):
    """The K-pop pre-drop flat look: f' = .085 + .93 f^.8 and saturation x.53 (then the grade pops back on the drop)."""
    lut = np.clip((0.085 + 0.93 * (np.arange(256) / 255.0) ** 0.8) * 255, 0, 255).astype(np.uint8)
    return fx.saturation_u8(cv2.LUT(img, lut), 0.53)


class PostcardIntro:
    """
    The K-pop postcard intro (owner favourite, mFHp6Dd2ISk): photo cards glide in and out over black with
    purple / blue particles; the last card is the drop clip itself, developing from grey paper, zooming to
    fill the frame exactly on the drop, where the flat look pops into the full grade. Card poses are the
    measured keyframes, retimed so the zoom-to-fill ends on the drop.
    """

    def __init__(self, eng, ov, canvas):
        self.eng, self.ov, self.canvas = eng, ov, canvas
        self.t0 = float(ov.get('t0', 0.0))
        self.drop = float(ov.get('t1'))
        L = self.drop - self.t0
        k = 1.0
        if L > REF_DROP:
            k = min(1.25, L / REF_DROP)
        self.k = k
        self.shift = self.drop - REF_DROP * k               # ref time r -> video time shift + r * k
        self.cards = []
        for name in ('card1', 'card2', 'card3', 'card4'):
            spec = CARD_KEYS[name]
            vis0 = self.shift + spec['visible'][0] * k
            if name == 'card1' and vis0 < self.t0 - 0.25:
                continue                                     # a shorter hook drops card 1 first,
            if name == 'card2' and vis0 < self.t0 - 0.9:
                continue                                     # then card 2
            self.cards.append((name, spec))
        self.stills = eng.card_stills(ov, 3)
        self.particles = fx.ParticleField('bokeh', colors=[(160, 60, 110), (200, 90, 40)], n=60, seed=21, size=canvas)
        self.caption = ov.get('caption') or ov.get('text')
        self.cap_img = None
        if self.caption:
            self.cap_img = T.render(self.caption, 'caption_bar', int(0.026 * canvas[1] / 0.72), max_w=int(canvas[0] * 0.92)).img
        self.textures = {}
        self.prev = {}

    def _texture(self, i, img):
        if i not in self.textures:
            h, w = img.shape[:2]
            s = min(h, w)
            sq = img[(h - s) // 2:(h - s) // 2 + s, (w - s) // 2:(w - s) // 2 + s]
            sq = cv2.resize(sq, (720, 720), interpolation=cv2.INTER_AREA)
            self.textures[i] = fx.bordered(sq, 0.018, shade=(1.0, 0.65))
        return self.textures[i]

    def render(self, t):
        w, h = self.canvas
        out = np.zeros((h, w, 3), np.uint8)
        out = fx.screen_layer(out, cv2.GaussianBlur(self.particles.layer(t), (0, 0), 1.5), 0.6)
        r = (t - self.shift) / self.k                        # time on the reference clock
        for i, (name, spec) in enumerate(self.cards):
            v0, v1 = spec['visible']
            if not (v0 <= r <= v1):
                continue
            cx, cy, ang = _eval_pose(spec, r)
            wd = _eval_width(spec, r)
            if name == 'card4':
                clip = flat_look(self.eng.drop_picture(t, (w, w)))
                a = 1.0 if r < 4.80 else max(0.0, 1 - (r - 4.80) / 0.90) ** 1.15
                paper = np.full_like(clip, 215)
                g = np.linspace(1.03, 0.93, clip.shape[0], dtype=np.float32)[:, None, None]
                paper = np.clip(paper * g, 0, 255).astype(np.uint8)
                tex = fx.bordered(fx.mix_u8(clip, paper, a), 0.018, shade=(1.0, 0.8))
            else:
                src = self.stills[i % len(self.stills)] if self.stills else np.full((720, 720, 3), 200, np.uint8)
                tex = self._texture(i, src)
            px, py = cx * w, cy * h
            lay, m = fx.affine_layer(tex, px, py, wd * w, ang, (w, h))
            prev = self.prev.get(name)
            if prev is not None:
                dx, dy = px - prev[0], py - prev[1]
                L = 0.55 * math.hypot(dx, dy) * (60.0 / self.eng.fps)
                if L >= 3:
                    angle = math.degrees(math.atan2(dy, dx))
                    lay = fx.line_blur(lay, L, angle)
                    m = cv2.resize(fx.line_blur(cv2.resize(m, (w // 2, h // 2)), L / 2, angle), (w, h))
            self.prev[name] = (px, py)
            out = fx.blend_mask(out, lay, m)
        if self.cap_img is not None:
            bar_h = int(0.069 * h)
            if r < 0.10 or r > 5.53:
                y = None
            elif r < 0.60:
                y = -bar_h + bar_h * ease((r - 0.10) / 0.5, 'out_quad')
            elif r > 4.98:
                y = -bar_h * ease((r - 4.98) / 0.55, 'in_quad')
            else:
                y = 0
            if y is not None:
                y = int(y)
                out[max(0, y):max(0, y + bar_h)] = (254, 254, 254)
                T.blit(out, self.cap_img, w / 2, y + bar_h / 2)
        since = t - self.t0
        if since < 0.7:
            out = fx.gain_u8(out, (since / 0.7) ** 2)
        return out


class TextOnBlack:
    """
    Words on black: 'neon' (Golden Hour: one word at a time, vertical stretch 3 -> 1 with motion blur over
    2 frames, hold, 4-frame fade) or 'lower' (K-pop: lowercase words fading in at their onsets, the
    phrase building up, fading out) or 'wide_thin'.
    """

    def __init__(self, eng, ov, canvas):
        self.eng, self.ov, self.canvas = eng, ov, canvas
        self.style = ov.get('style', 'neon')
        self.t0, self.t1 = float(ov['t0']), float(ov['t1'])
        words = ov.get('words')
        if not words:
            toks = T.latin_only(ov.get('text') or '').split()
            span = max(0.5, self.t1 - self.t0 - 0.35)
            words = [{'text': tk, 't0': self.t0 + span * i / max(1, len(toks)), 't1': self.t0 + span * (i + 1) / max(1, len(toks))}
                     for i, tk in enumerate(toks)]
        self.words = words
        w, h = canvas
        size = ov.get('size') or (0.075 if self.style == 'neon' else 0.03) * h * (0.5625 if w > h else 1.0) / 0.5625 * (w / max(w, h) if w > h else 1)
        self.size = int(ov.get('size_px') or size)
        self.sprites = [T.render(wd['text'], self.style, self.size, max_w=int(w * 0.9)) for wd in words]
        self.glow = fx.light_leak_layer(canvas, 0.0, seed=0.2, color=(255, 140, 190)) if ov.get('leak', self.style == 'neon') else None

    def render(self, t):
        w, h = self.canvas
        out = np.zeros((h, w, 3), np.uint8)
        if self.glow is not None:
            out = fx.screen_layer(out, self.glow, 0.25)
        f = 1 / 30.0
        if self.style == 'neon':
            for wd, spr in zip(self.words, self.sprites):
                a, b = float(wd['t0']), float(wd.get('t1', wd['t0'] + 0.5))
                if not (a <= t < b + 4 * f):
                    continue
                since = t - a
                sy = 1.0 + 2.0 * max(0.0, 1 - since / (2 * f))
                alpha = 1.0 if t < b else max(0.0, 1 - (t - b) / (4 * f))
                T.blit(out, spr.img, w / 2, h / 2, alpha=alpha, sy=sy, blur_y=int(40 * (sy - 1)))
            return out
        # phrase: words appear one by one (fade 0.35 s) and stay; the phrase fades out at the end
        line_w = sum(s.size[0] - 2 * s.pad for s in self.sprites) + 0.3 * self.size * (len(self.sprites) - 1)
        x = w / 2 - line_w / 2
        end_a = max(0.0, min(1.0, (self.t1 - t) / 0.3))
        for wd, spr in zip(self.words, self.sprites):
            a = float(wd['t0'])
            sw = spr.size[0] - 2 * spr.pad
            if t >= a:
                T.blit(out, spr.img, x + sw / 2, h / 2, alpha=min(1.0, (t - a) / 0.35) * end_a)
            x += sw + 0.3 * self.size
        return out


class MusicCard:
    """
    Long-video intro card: an album-style square still (32% H, white border) left of centre, the song
    title and artist in wide-tracked thin caps, a lyric line typed letter by letter, EDIT BY <channel>,
    a thin waveform, over a dark blurred copy of the still with a light leak; the whole card pushes 3%/s.
    """

    def __init__(self, eng, ov, canvas):
        self.eng, self.ov, self.canvas = eng, ov, canvas
        self.t0, self.t1 = float(ov['t0']), float(ov['t1'])
        w, h = canvas
        stills = eng.card_stills(ov, 1)
        st = stills[0] if stills else np.full((720, 720, 3), 90, np.uint8)
        s = min(st.shape[:2])
        sq = st[(st.shape[0] - s) // 2:(st.shape[0] - s) // 2 + s, (st.shape[1] - s) // 2:(st.shape[1] - s) // 2 + s]
        side = int(0.32 * h if w > h else 0.42 * w)
        self.card = fx.bordered(cv2.resize(sq, (side, side), interpolation=cv2.INTER_AREA), 0.02)
        self.bg = fx.gain_u8(cv2.resize(cv2.GaussianBlur(cv2.resize(st, (64, 36 if w > h else 114)), (0, 0), 3), (w, h)), 0.4)
        big = int(0.055 * h if w > h else 0.04 * h)
        self.title = T.render(ov.get('title') or '', 'wide_thin', big, max_w=int(w * 0.42 if w > h else w * 0.86))
        self.artist = T.render(ov.get('artist') or '', 'tiny', int(big * 0.55), max_w=int(w * 0.4))
        self.line = T.render(ov.get('line') or '', 'lyric', int(big * 0.5), max_w=int(w * 0.42 if w > h else w * 0.86))
        self.edit = T.render(('edit by ' + ov['channel']) if ov.get('channel') else '', 'wide_thin', int(big * 0.32))
        self.wave = eng.music_envelope()

    def render(self, t):
        w, h = self.canvas
        out = self.bg.copy()
        out = fx.screen_layer(out, fx.light_leak_layer(self.canvas, t, seed=0.4), 0.35)
        since = t - self.t0
        land = w > h
        cx, cy = (0.33 * w, 0.5 * h) if land else (0.5 * w, 0.36 * h)
        tx = (0.47 * w) if land else 0.5 * w
        lay, m = fx.affine_layer(self.card, cx, cy, self.card.shape[1], 0.0, (w, h))
        out = fx.shadow_under(out, m, 0.02 * w, (0.0, 0.01 * w), 0.5)
        out = fx.blend_mask(out, lay, m)
        a = min(1.0, since / 0.4)
        if land:
            ty = 0.40 * h
            T.blit(out, self.title.img, tx + self.title.size[0] / 2, ty, alpha=a)
            T.blit(out, self.artist.img, tx + self.artist.size[0] / 2, ty + 0.08 * h, alpha=a)
            n = int(max(0.0, since - 0.3) * 24)
            T.blit(out, self.line.crop_letters(n), tx + self.line.size[0] / 2, ty + 0.16 * h, alpha=a)
            T.blit(out, self.edit.img, tx + self.edit.size[0] / 2, ty + 0.24 * h, alpha=a * 0.8)
            y_w, x0, x1 = int(0.8 * h), int(0.25 * w), int(0.75 * w)
        else:
            ty = cy + self.card.shape[0] / 2 + 0.06 * h
            T.blit(out, self.title.img, tx, ty, alpha=a)
            T.blit(out, self.artist.img, tx, ty + 0.05 * h, alpha=a)
            n = int(max(0.0, since - 0.3) * 24)
            T.blit(out, self.line.crop_letters(n), tx, ty + 0.1 * h, alpha=a)
            T.blit(out, self.edit.img, tx, ty + 0.15 * h, alpha=a * 0.8)
            y_w, x0, x1 = int(0.86 * h), int(0.12 * w), int(0.88 * w)
        if self.wave is not None:
            env, rate = self.wave
            n = x1 - x0
            ts = self.eng.music_start + t - 0.8 + 1.6 * np.arange(n) / n
            idx = np.clip((ts * rate).astype(int), 0, len(env) - 1)
            amp = env[idx] * 0.03 * h
            pts = np.stack([np.arange(x0, x1), y_w - amp], 1).astype(np.int32)
            cv2.polylines(out, [pts], False, (235, 235, 235), 2, cv2.LINE_AA)
        push = 1 + 0.03 * since
        if push > 1.001:
            out = fx.warp(out, push, 0, 0, 0, (w, h))
        return out


SCENES = {'postcard_intro': PostcardIntro, 'text_on_black': TextOnBlack, 'music_card': MusicCard}
SHOT_LAYOUTS = {'postcard': Postcard, 'card3d': Card3D, 'split2': lambda e, s, c: Split(e, s, c, 2),
                'split3': lambda e, s, c: Split(e, s, c, 3), 'hearts': Hearts}

# photo_scene_intro (the ZkWQloS4jtM postcard intro) must be in SCENES before engine.py
# snapshots SCENE_TYPES, so it is added here rather than from the module itself.
try:  # noqa: E402
    import photo_phone as _photo_phone  # noqa: E402
    _photo_phone.register_scenes()
except Exception as _e:  # pragma: no cover
    print('photo_phone scenes not loaded: %s' % _e, file=sys.stderr)
