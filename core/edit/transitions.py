#!/usr/bin/env python3
"""
Transitions and accents for the edit engine: every cut recipe in core/edit/refs/*.json as envelopes in
time around the cut.

Time convention: t0 is the SWITCH (the first frame that belongs to the new shot; the planner or
styles.cut_time() already put it the measured lead before the beat). A transition acts on
  - the outgoing shot for u = t0 - t in (0, pre]    (camera: cam_out(u), image: fx_out(img, u))
  - the incoming shot for v = t - t0 in [0, post)   (camera: cam_in(v),  image: fx_in(img, v))
  - or composites both (composite(v) for v in [-pre, post)) when it needs two pictures at once.
Camera contributions are (ln scale, roll deg, dx, dy) with dx / dy in fractions of the canvas width;
the engine adds them to the shot camera so they ride the same single warp (and its motion blur).
`hold` transitions keep their incoming camera for the whole shot (K-pop entry settles).

Names (families in brackets; aliases resolve to these):
 general   hard, hard_settle, blur_bridge, blur_bridge_soft, blur_bridge_flash, blur_bridge_hype, flash_white,
           exposure_bump_hard, zoom_through, punch_settle, radial_zoom_burst, whip_slide, dissolve_blur,
           invert_strobe, dip_black_strobe, rgb_split_glitch, halftone_flash, darken_slide_preview,
           white_bar_wipe, shape_wipe (heart / star), card_tilt_in, photo_collage, first_shot_entry
 dance     defocus_in, zoom_blur_in, flash_in, cross_dissolve, whip_diag, pattern_flash, dark_blur_in,
           lens_zoomthrough (flash_white has the dance variant: glow wipe + 3 white frames on the kick)
 character flash_zoom, mb_cut, zoom_through_color_field, whip_horizontal, whip_vertical, impact_zoom_1frame,
           spin_blur, mirror_tile_scale_in, tint_flash, white_out, defocus_dissolve, dip_black,
           white_flash_cut, glow_cut, chevron_wipe, iris_wipe, wave_wipe, blob_wipe
 kpop      blur_in_punch, shake_blur, blur_rotate, blur_zoom, zoom_blur_flash, hard_punch, vslide_wrap,
           cutout_prereveal, peek_window, slices_reveal, photo_clones (zoom_through has the kpop variant)
 intros    fade_in, bar_open_reveal, vignette_fade_in, breath
 modifiers "<name>+bleach" (exposure bleach), "+flash" (white flash on the cut), "+rgb" (RGB fringe), "+leak"
"""
import math
import os
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'meme'))
import edit_fx as fx  # noqa: E402

F30, F60 = 1 / 30.0, 1 / 60.0
ZERO = (0.0, 0.0, 0.0, 0.0)


def ss(u):
    u = min(1.0, max(0.0, u))
    return u * u * (3 - 2 * u)


def e_out(u):
    u = min(1.0, max(0.0, u))
    return 1 - (1 - u) ** 3


def e_in(u):
    u = min(1.0, max(0.0, u))
    return u * u


def ex(t, tau):
    return math.exp(-max(0.0, t) / tau)


def bright_att(img):
    """Exposure bumps shrink on bright pictures (a pink stage or a white sky would blow out to white)."""
    m = float(cv2.resize(img, (32, 32), interpolation=cv2.INTER_AREA).mean()) / 255
    return min(1.0, max(0.35, 1.45 - 1.1 * m))


def add_cam(a, b):
    if len(a) > 4 or len(b) > 4:                  # 3D camera terms (core/edit/camera3d.py): keep them
        n = max(len(a), len(b))
        a = tuple(a) + (0.0,) * (n - len(a))
        b = tuple(b) + (0.0,) * (n - len(b))
        return tuple(x + y for x, y in zip(a, b))
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2], a[3] + b[3])


class Ctx:
    """What a transition may ask the engine for (filled per cut by the engine)."""

    def __init__(self, size, fps, rng, family='general', colors=None, beat=0.5, energy=1.0, get_img=None, stills=None,
                 matte=None, focus=(0.5, 0.42), flash_color=(255, 255, 255), leak=True):
        self.size, self.fps, self.rng, self.family = size, fps, rng, family
        self.colors = colors or [(255, 80, 200), (255, 180, 60)]
        self.beat, self.energy = beat, energy
        self.get_img = get_img            # get_img(role, t) -> canvas BGR of the 'out' / 'in' shot at time t
        self.stills = stills              # stills(n) -> list of BGR stills of other shots (collage / clones)
        self.matte = matte                # matte() -> (BGR image, float mask) of the incoming subject (cutouts)
        self.focus = focus                # subject position in the canvas (fractions)
        self.flash_color = flash_color
        self.leak = leak

    def px(self, v):
        """px measured at 1080 wide -> px at this canvas width."""
        return v * self.size[0] / 1080.0

    def pw(self, frac):
        """fraction of the canvas width -> px."""
        return frac * self.size[0]


class T:
    """Base transition: a hard cut."""
    name = 'hard'
    pre = 0.0
    post = 0.0
    composite_pre = False
    composite_post = False
    hold = False

    def __init__(self, ctx, **kw):
        self.ctx = ctx
        self.kw = kw
        self.setup()

    def setup(self):
        pass

    def cam_out(self, u):
        return ZERO

    def cam_in(self, v):
        return ZERO

    def fx_out(self, img, u):
        return img

    def fx_in(self, img, v):
        return img

    def composite(self, v, t):
        return None

    # helpers
    def rnd(self, a, b):
        return float(self.ctx.rng.uniform(a, b))

    def sign(self):
        return 1.0 if self.ctx.rng.random() < 0.5 else -1.0

    def white(self, img, a, color=None):
        # transition_args {"white_cap": 0.83}: the veil never reaches a pure white frame (fx.json not_observed;
        # the shot layouts ask for it). Default: the recipe's own alpha, unchanged.
        cap = self.kw.get('white_cap')
        return fx.fill_mix(img, color or self.ctx.flash_color, a if cap is None else min(float(cap), a))


class Hard(T):
    name = 'hard'


class HardSettle(T):
    """
    Dance hard cut with an incoming zoom settle s_k = 1.18 ** (0.55 ** k) (k = frames @30).

    The settle is QUANTISED to 30 fps steps. dance.json's hard cut carries no defocus at all, but a
    continuous settle moves ~80 px between the two 60 fps halves of the join frame, which trips
    engine.place's own camera motion blur and the join measured Laplacian var 99.6 against 418.7 one
    frame later - i.e. it stopped reading as HARD. Held still inside each 30 fps frame the camera does
    not move, the engine takes the single-resample path, and s_0 = 1.180 lands sharp.
    """
    name = 'hard_settle'
    post = 0.3

    snap = True         # engine: the join frame's shutter samples just before t0 also get cam_in(0)
    no_mblur = True     # round 3: no shutter blur at all over the settle (first frame >= 90 % of settled sharpness)

    def cam_in(self, v):
        n = int(round(max(0.0, v) * self.ctx.fps))              # whole output frame: constant across its shutter
        k = int(n * 30.0 / self.ctx.fps + 1e-6)
        return (math.log(1.18) * 0.55 ** k, 0, 0, 0)


class BlurBridge(T):
    """
    The AE house transition: the outgoing smears over its last 2 frames @30, the incoming appears
    blurred and over-exposed and resolves: sigma(t) = s0 e^(-t/0.09) px @1080, gain 1 + A e^(-t/0.1),
    directional blur 100 e^(-t/0.05) px @1920; entry punch-settle (p .25), zoom-in (p .2), roll (p .35).
    """
    name = 'blur_bridge'
    pre = 4 * F30               # round 5: k = .25, .5, .75, 1 -> 3 VISIBLE frames of anticipation (3 * F30 only showed 2)
    post = 0.34
    # round 5 LEGIBILITY RECALIBRATION (core/edit/refs/join_sharp.py, 17 references, 150+ cuts): the equivalent
    # incoming defocus after a join is 0.16-0.21 %W median and 0.31-0.62 %W at p75 on the first two frames, in every
    # group - never the 18 px @1080 (1.67 %W) the first spec pass wrote down, which buried the new shot for 6-8
    # frames (side by side with ZkWQ 7.40 s: reference sharpness 0.26 of settled on frame 1, ours 0.00 until frame 8).
    # The outgoing reaches ratio 0.03-0.3 on its last frame only. sigma0 is now the p75 value.
    # AV round (motion3d on 48 Blur Flow bridges, the same tool on our renders): blur EXCESS at the switch p50 0.14 %W
    # (IQR to ~0.3), exposure 1.17 at +1..+3 frames decaying over ~8 frames -> sigma0 2.4 px @1080, gain 0.65, tau 0.12
    sigma0, gain_a, dir0, flash_p, zoomblur = 2.4, 0.65, 60.0, 0.0, 0.0
    gain_tau = 0.12
    clear_s = 0.30              # the defocus is truncated to exactly 0 here (spec: gone by 230-330 ms)

    def setup(self):
        r = self.ctx.rng.random()
        self.entry = 'settle' if r < 0.25 else ('zoom' if r < 0.45 else None)
        self.A = self.rnd(0.07, 0.25)
        self.zoom_amt = self.rnd(0.05, 0.18)
        self.roll = self.sign() * self.rnd(1.0, 3.0) if self.ctx.rng.random() < 0.35 else 0.0
        self.dir = self.sign()
        self.flash = self.ctx.rng.random() < self.flash_p or self.kw.get('flash', False)
        self.flash_len = (1 if self.ctx.rng.random() < 0.6 else 2) * F30 * (2 if self.kw.get('drop') else 1)
        if self.kw.get('frames'):            # remakes / planners: the reference's measured flash length (frames @30)
            self.flash_len = int(self.kw['frames']) * F30

    def cam_in(self, v):
        lns = 0.0
        if self.entry == 'settle':
            lns = math.log(1 + self.A * ex(v, 0.06))
        elif self.entry == 'zoom':
            lns = self.zoom_amt * e_out(v / 0.17) - self.zoom_amt
        rot = self.roll * (1 - ss(v / 0.25))
        return (lns, rot, 0, 0)

    def fx_out(self, img, u):
        k = 1 - u / self.pre                      # 0 .. 1 toward the cut
        # AV round (motion3d, 48 Blur Flow bridges): the outgoing blur excess is 0.04 %W at -67 ms and 0.09-0.14 %W on
        # the LAST frame; the old 6 px @1080 + 80 px smear measured 0.39 %W there (x3). Now ~0.15 %W on the last frame.
        sig = self.ctx.px(0.8 + 2.2 * k * k)
        img = fx.gauss_px(img, sig * min(1.0, k * 2))
        img = fx.line_blur(img, self.ctx.pw((15 + 25 * k) / 1920.0) * k * k, 0)
        if self.zoomblur:
            img = fx.zoom_blur_c(img, self.zoomblur * (0.5 + k))
        elif k > 0.5:
            img = fx.zoom_blur_c(img, 0.08 * k)
        return img

    def fx_in(self, img, v):
        if self.flash and v < self.flash_len:
            base = fx.gauss_px(img, self.ctx.px(self.sigma0))
            return self.white(base, 0.85 + 0.15 * v / self.flash_len)
        w = v - (self.flash_len if self.flash else 0.0)
        sig = self.ctx.px(self.sigma0) * ex(w, 0.09) * min(1.0, max(0.0, (self.clear_s - w) / 0.10))
        img = fx.gauss_px(img, sig)
        d = self.ctx.pw(self.dir0 / 1920.0) * ex(w, 0.05)
        img = fx.line_blur(img, d, 0)
        if self.zoomblur:
            img = fx.zoom_blur_c(img, self.zoomblur * 1.2 * max(0.0, 1 - w / 0.12))
        a = (0.6 if self.flash else self.gain_a) * ex(w, getattr(self, 'gain_tau', 0.1))
        if self.kw.get('gain') is not None:
            # optional transition_params gain / gain_tau / gain_end (S2rz remake round 3: the measured incoming bump
            # 1 + 0.5 e^(-t/0.1), back to exactly 1.0 by gain_end - it used to stack on bright plates into a milky wash)
            a = float(self.kw['gain']) * ex(w, float(self.kw.get('gain_tau', 0.1)))
            ge = self.kw.get('gain_end')
            if ge is not None:
                a *= max(0.0, min(1.0, (float(ge) - w) / max(1e-3, 0.3 * float(ge))))
        img = fx.gain_u8(img, 1 + a * bright_att(img))
        if self.flash and self.ctx.leak and w < 0.45:
            img = fx.screen_layer(img, fx.light_leak_layer(self.ctx.size, v, seed=self.ctx.rng.random()), 0.55 * (1 - w / 0.45))
        return img


class BlurBridgeSoft(BlurBridge):
    name = 'blur_bridge_soft'
    sigma0, gain_a, dir0 = 2.5, 0.25, 0.0
    gain_tau = 0.1

    def setup(self):
        super().setup()
        self.entry, self.roll = None, 0.0


class BlurBridgeFlash(BlurBridge):
    name = 'blur_bridge_flash'
    flash_p = 1.0
    post = 0.45


class BlurBridgeHype(BlurBridge):
    """Hype variant: a 4-5 frame zoom-blur bridge with a stronger blur; a white flash on 60% of cuts."""
    name = 'blur_bridge_hype'
    pre = 4 * F30
    post = 0.4
    sigma0, gain_a, zoomblur, flash_p = 8.0, 0.7, 0.22, 0.6
    gain_tau = 0.1
    clear_s = 0.26


class FlashWhite(T):
    """
    General: 1-2 (3-4 on drops) white frames starting at the switch, then the incoming at exposure
    1 + 0.6 e^(-t/0.1) with defocus 15 -> 0 over 200 ms and a light leak.
    Dance (1L7): a glow wipes in from a corner 3 frames before, 3 pure white frames, then x1.8 -> x1.0.
    """
    name = 'flash_white'

    def setup(self):
        self.dance = self.ctx.family == 'dance'
        if self.dance:
            self.pre, self.L = 3 * F30, 3 * F30
        else:
            self.pre = 0.0
            self.L = (1 if self.ctx.rng.random() < 0.6 else 2) * F30 * (2 if self.kw.get('drop') else 1)
            if self.kw.get('frames'):        # remakes / planners: the reference's measured flash length (frames @30)
                self.L = int(self.kw['frames']) * F30
        self.post = self.L + 0.3
        self.corner = (self.ctx.rng.random() < 0.5, self.ctx.rng.random() < 0.5)

    def fx_out(self, img, u):
        if not self.dance:
            return img
        k = 1 - u / self.pre
        w, h = self.ctx.size
        lay = np.zeros((h // 8, w // 8, 3), np.float32)
        cx, cy = (w // 8) * (1.0 if self.corner[0] else 0.0), (h // 8) * (1.0 if self.corner[1] else 0.0)
        cv2.circle(lay, (int(cx), int(cy)), int(max(w, h) / 8 * (0.4 + 0.8 * k)), (1, 1, 1), -1)
        lay = cv2.GaussianBlur(lay, (0, 0), w / 8 * 0.2)
        return fx.screen_layer(img, lay, 0.6 * k)

    def fx_in(self, img, v):
        if v < self.L:
            return self.white(img, 1.0 if self.dance else 0.9 + 0.1 * v / self.L)
        w = v - self.L
        if self.dance:
            img = fx.gauss_px(img, self.ctx.pw(0.02) * 0.8 * max(0.0, 1 - w / 0.1))
            return fx.gain_u8(img, 1 + 0.8 * max(0.0, 1 - w / 0.1) * bright_att(img))
        img = fx.gauss_px(img, self.ctx.px(3) * max(0.0, 1 - w / 0.13))     # round 5: 15 -> 3 px, 4 frames (m3lv 13.8 s: 0.18, 0.12, 0.09 %W)
        a = 0.6 * ex(w, 0.1)
        if self.kw.get('gain') is not None:     # optional gain / gain_tau / gain_end (see BlurBridge.fx_in)
            a = float(self.kw['gain']) * ex(w, float(self.kw.get('gain_tau', 0.1)))
            ge = self.kw.get('gain_end')
            if ge is not None:
                a *= max(0.0, min(1.0, (float(ge) - w) / max(1e-3, 0.3 * float(ge))))
        img = fx.gain_u8(img, 1 + a * bright_att(img))
        if self.kw.get('no_leak'):
            return img
        if self.ctx.leak and w < 0.5:
            img = fx.screen_layer(img, fx.light_leak_layer(self.ctx.size, v, seed=0.37), 0.30 * (1 - w / 0.5))   # round 5: .55 flattened the shot
        return img


class ExposureBumpHard(T):
    """Golden Hour: a hard cut whose first frames are brighter and bloomier: bloom x (1 + 1.5 e^(-t/0.08)), gain 1 + 0.25 e^(-t/0.08)."""
    name = 'exposure_bump_hard'
    post = 0.35

    def fx_in(self, img, v):
        # transition_params (all optional, defaults = the original look): bloom, gain, defocus_px, tau, defocus_s
        # (cinematic_slowmo remake of _sX2: the measured bump is tiny - exposure 1.00-1.04, blur excess 0.05 %W on
        # the first 1-2 frames - so that preset passes bloom 0.25 / gain 0.04 / defocus_px 1.5)
        tau = float(self.kw.get('tau', 0.08))
        k = ex(v, tau)
        if k < 0.02:
            return img
        # bright shots get a gentler bump (they blow out to white otherwise)
        m = float(cv2.resize(img, (32, 32), interpolation=cv2.INTER_AREA).mean()) / 255
        att = min(1.0, max(0.25, 1.3 - 1.4 * m))
        img = fx.gauss_px(img, self.ctx.px(float(self.kw.get('defocus_px', 4))) * max(0.0, 1 - v / float(self.kw.get('defocus_s', 0.1))))
        img = fx.bloom_boost(img, float(self.kw.get('bloom', 0.9)) * k * att, threshold=0.6)
        return fx.gain_u8(img, 1 + float(self.kw.get('gain', 0.25)) * k * att)


class ZoomThrough(T):
    """
    General: the outgoing rushes in (x1.13, ease-in, last 100 ms) with zoom blur 0 -> 0.15; the incoming
    continues from x1.10 to 1.0 over 170 ms with zoom blur 0.12 -> 0.
    K-pop: +20% extra scale and zoom blur 0 -> 0.35 over the last 12 frames @60; incoming 1.3 -> 1.0 over
    13 frames, roll 3 -> 0, blur 0.28% W held 8 frames.
    """
    name = 'zoom_through'

    def setup(self):
        self.kp = self.ctx.family == 'kpop'
        self.pre = 12 * F60 if self.kp else 8 * F60      # round 5: 4 frames @30 so that 3 of them visibly rush
        self.post = 16 * F60 if self.kp else 0.18

    def cam_out(self, u):
        # round 4: ln s = 0.098 t + 0.094 t^2 (t in frames @60) -> x1.6 over the 12 K-pop frames and x1.22 over the
        # 6 general ones: the rush is visible from the first frame instead of living in the last one.
        t = (self.pre - u) / F60
        return ((0.098 * t + 0.094 * t * t) * (math.log(1.6) / 14.7 if self.kp else math.log(1.22) / 6.8), 0, 0, 0)

    def fx_out(self, img, u):
        k = 1 - u / self.pre
        return fx.zoom_blur_c(img, (0.35 if self.kp else 0.25) * k ** 1.3)

    def cam_in(self, v):
        if self.kp:
            return (math.log(1.3) * (1 - e_out(v / (13 * F60))), 3.0 * (1 - e_out(v / (13 * F60))), 0, 0)
        return (math.log(1.10) * (1 - e_out(v / 0.17)), 0, 0, 0)

    def fx_in(self, img, v):
        if self.kp:
            sig = self.ctx.pw(0.0028) * (1.0 if v < 8 * F60 else max(0.0, 1 - (v - 8 * F60) / (8 * F60)))
            return fx.gauss_px(img, sig)
        return fx.zoom_blur_c(img, 0.12 * max(0.0, 1 - v / 0.1))


class PunchSettle(T):
    """Incoming starts enlarged and settles: s = 1 + A e^(-t/0.06), A ~ 0.14."""
    name = 'punch_settle'
    post = 0.25

    def setup(self):
        self.A = self.kw.get('A') or self.rnd(0.07, 0.3)

    def cam_in(self, v):
        return (math.log(1 + self.A * ex(v, 0.06)), 0, 0, 0)


class RadialZoomBurst(T):
    """Hero: zoom blur 0.2 -> 0.4 on the outgoing's last 2 frames @30, 0.4 -> 0 on the incoming's first 3, a 0.6 flash on the cut."""
    name = 'radial_zoom_burst'
    pre = 2 * F30
    post = 0.25

    def cam_out(self, u):
        return (0.12 * e_in(1 - u / self.pre), 0, 0, 0)

    def fx_out(self, img, u):
        return fx.zoom_blur_c(img, 0.2 + 0.2 * (1 - u / self.pre), steps=8)

    def cam_in(self, v):
        return (0.15 * (1 - e_out(v / 0.2)), 0, 0, 0)

    def fx_in(self, img, v):
        img = fx.zoom_blur_c(img, 0.4 * max(0.0, 1 - v / (3 * F30)), steps=8)
        if v < F30:
            img = self.white(img, 0.6)
        return fx.gain_u8(img, 1 + 0.35 * ex(v, 0.08) * bright_att(img))


class WhipSlide(T):
    """Both shots slide sideways together (edge to edge), smeared along the move (8-10% W per frame @30)."""
    name = 'whip_slide'
    post = 0.12
    composite_post = True

    def setup(self):
        self.dir = self.kw.get('dir') or self.sign()
        # optional (ZkWQ remake): a longer, front-loaded slide - the reference's whip peaks ~10 %W/frame on the switch
        # and decays over ~6 frames (general-av.json curves) instead of an edge-to-edge smoothstep in 0.12 s
        if self.kw.get('post'):
            self.post = float(self.kw['post'])
        self.ease = self.kw.get('ease', 'ss')

    def composite(self, v, t):
        inc = self.ctx.get_img('in', t)
        out = self.ctx.get_img('out', t)
        h, w = inc.shape[:2]
        if self.kw.get('mode') == 'pan_in':
            # ZkWQ whip (frames 16.40-16.80 s): one mixed, smeared frame on the switch, then the incoming picture pans in
            # from `dist` of the width and decelerates (e_out) over `post`; the edge it uncovers is mirrored under the smear
            dist = float(self.kw.get('dist', 0.35))
            k = e_out(min(1.0, v / self.post))
            k2 = e_out(min(1.0, (v + 1.0 / self.ctx.fps) / self.post))
            dx = self.dir * dist * w * (1 - k)
            M = np.float32([[1, 0, dx], [0, 1, 0]])
            img = cv2.warpAffine(inc, M, (w, h), borderMode=cv2.BORDER_REFLECT)
            if v < 1.0 / self.ctx.fps - 1e-6:
                img = fx.mix_u8(out, img, 0.5)
            speed = abs(k2 - k) * dist * w
            return fx.line_blur(img, min(self.ctx.pw(250 / 1920), speed * 1.3 + self.ctx.pw(6 / 1920)), 0)
        f = e_out if self.ease == 'out' else ss
        k = f(min(1.0, v / self.post))
        k2 = f(min(1.0, (v + 1.0 / self.ctx.fps) / self.post))
        off = int(round(self.dir * w * (1 - k)))
        canvas = np.empty_like(inc)
        if off >= 0:
            canvas[:, off:] = inc[:, :w - off]
            canvas[:, :off] = out[:, w - off:] if off else canvas[:, :0]
        else:
            canvas[:, :w + off] = inc[:, -off:]
            canvas[:, w + off:] = out[:, :-off]
        speed = abs(k2 - k) * w
        return fx.line_blur(canvas, min(self.ctx.pw(250 / 1920), speed * 1.2), 0)


class CrossDissolve(T):
    """Plain cross-dissolve starting at the switch (sound edits: 2-3 frames @24, dance: 4-12 frames @30); both keep playing."""
    name = 'cross_dissolve'
    composite_post = True

    def setup(self):
        fam = self.ctx.family
        n = self.kw.get('frames')
        if fam == 'sound':
            self.post = (n or 2) / 24.0
        else:
            self.post = (n or int(self.rnd(4, 9))) * F30

    def composite(self, v, t):
        a = (v + 1.0 / self.ctx.fps) / (self.post + 1.0 / self.ctx.fps)
        if self.ctx.family != 'sound':
            a = ss(a)
        return fx.mix_u8(self.ctx.get_img('out', t), self.ctx.get_img('in', t), min(1.0, a))


class DissolveBlur(CrossDissolve):
    """2-4 frame dissolve with both pictures defocused 4-8 px (AE)."""
    name = 'dissolve_blur'

    def setup(self):
        self.post = int(self.rnd(2, 5)) * F30

    def composite(self, v, t):
        a = ss((v + 1.0 / self.ctx.fps) / (self.post + 1.0 / self.ctx.fps))
        sig = self.ctx.px(6) * math.sin(math.pi * min(1.0, a))
        A = fx.gauss_px(self.ctx.get_img('out', t), sig)
        B = fx.gauss_px(self.ctx.get_img('in', t), sig)
        return fx.mix_u8(A, B, a)


class InvertStrobe(T):
    """1 inverted frame, 1 white, 2 black (on the beat), then the new shot (frames @30)."""
    name = 'invert_strobe'
    post = 4 * F30

    def fx_in(self, img, v):
        f = int(v / F30)
        if f == 0:
            return fx.invert_u8(img)
        if f == 1:
            return self.white(img, 1.0)
        if f <= 3:
            return np.zeros_like(img)
        return img


class DipBlackStrobe(T):
    """1-2 black frames then 1 white frame then the new shot (@30)."""
    name = 'dip_black_strobe'
    post = 3 * F30

    def fx_in(self, img, v):
        f = int(v / F30)
        if f <= 1:
            return np.zeros_like(img)
        if f == 2:
            return self.white(img, 1.0)
        return img


class RGBSplitGlitch(T):
    """1-3 frames of RGB split (7-25 px @1920) with a few slices shoved sideways."""
    name = 'rgb_split_glitch'
    post = 3 * F30

    def fx_in(self, img, v):
        a = 1.5 - 1.0 * v / self.post
        img = fx.rgb_split(img, self.ctx.pw(16 / 1920) * a)
        return fx.glitch(img, v + self.ctx.rng.random(), min(1.0, a * 0.7))


class HalftoneFlash(T):
    """1-2 frames of a coarse halftone dot pattern over a bright frame."""
    name = 'halftone_flash'
    post = 2 * F30

    def fx_in(self, img, v):
        return fx.halftone(fx.gain_u8(img, 1.3), cell=max(8, int(self.ctx.pw(0.017))))


class DarkenSlidePreview(T):
    """The next shot slides in blurred over the still-sharp current one for 3 frames @30, only its darker (or brighter) parts showing."""
    name = 'darken_slide_preview'
    pre = 3 * F30
    composite_pre = True

    def composite(self, v, t):
        out = self.ctx.get_img('out', t)
        inc = self.ctx.get_img('in', t)
        k = min(2, int((v + self.pre) / F30))
        off = int([0.45, 0.25, 0.1][k] * out.shape[1])
        b = fx.gauss_px(inc, self.ctx.px(25))
        b = np.roll(b, off, axis=1)
        m = cv2.min(out, b) if b.mean() < out.mean() else cv2.max(out, b)
        return fx.mix_u8(out, m, 0.9)


class WhiteBarWipe(T):
    """Two vertical white bars (x .35 / .62, 5% W) grow to cover the frame over 4 frames, 1 white frame, then the new shot bumped x1.5."""
    name = 'white_bar_wipe'
    pre = 4 * F30
    post = 0.3

    def fx_out(self, img, u):
        k = e_in(1 - u / self.pre)
        w = img.shape[1]
        # round 4: the bars BLOOM (soft glow edge, sigma 0.8 %W) instead of being razor-edged rectangles
        m = np.zeros((1, w), np.float32)
        for xc in (0.35, 0.62):
            half = (0.025 + 0.4 * k) * w
            x0, x1 = max(0, int(xc * w - half)), min(w, int(xc * w + half))
            m[:, x0:x1] = 1.0
        m = np.maximum(m, 0.0)
        m = np.clip(cv2.GaussianBlur(m, (0, 0), max(1.0, 0.008 * w)) * 1.15 + 0.35 * cv2.GaussianBlur(m, (0, 0), max(1.0, 0.03 * w)), 0, 1)
        cap = self.kw.get('white_cap')
        a = m[0][None, :, None] * (1.0 if cap is None else float(cap))
        return np.clip(img.astype(np.float32) * (1 - a) + 255.0 * a, 0, 255).astype(np.uint8)

    def fx_in(self, img, v):
        f = v * 30.0 + 1e-3
        if f < 1.0:
            return self.white(img, 1.0)
        g = fx.gain_u8(img, 1 + 0.5 * ex(v - F30, 0.06) * bright_att(img))      # 1.5, 1.29, 1.16, 1.09
        if self.kw.get('white_cap') is not None and f < 3.0:
            return fx.fill_mix(g, (255, 255, 255), (0.45, 0.15)[int(f) - 1])     # into a layout: 0.83, 0.45, 0.15, 0
        return g


class ShapeWipe(T):
    """
    General: a big soft-shaded heart (or star) flies through the camera (scale 0.6 -> 2.6 over 4 frames
    @30) covering the cut. Character family: one of chevron / iris / wave / blob wipes.
    """
    name = 'shape_wipe'
    pre = 2 * F30
    post = 2 * F30
    composite_pre = composite_post = True

    def setup(self):
        self.shape = self.kw.get('shape') or ('heart' if self.ctx.rng.random() < 0.7 else 'star')
        self.start = (self.rnd(0.1, 0.9), 1.25)
        c0 = self.ctx.colors[0] if self.ctx.colors else (180, 105, 255)
        self.col_top = tuple(int(min(255, c * 0.5 + 140)) for c in c0)
        self.col_bot = tuple(int(c * 0.9) for c in c0)
        self.shade = None

    def _shaded(self, base, m, cx, cy, r, tint):
        """round 4: a soft-shaded 3D cream shape (centre #F2DDB8 -> edge #C9A878, specular top-left), palette pulled
        toward the outgoing shot's warm hue - not a flat slate silhouette."""
        h, w = m.shape
        yy, xx = np.mgrid[0:h // 4 + 1, 0:w // 4 + 1].astype(np.float32)
        d = np.sqrt((xx * 4 - cx) ** 2 + (yy * 4 - cy) ** 2) / max(1.0, r)
        g = cv2.resize(np.clip(d, 0, 1) ** 1.6, (w, h))[..., None]
        ctr = np.array((184, 221, 242), np.float32) * 0.8 + tint * 0.2          # BGR of #F2DDB8
        edg = np.array((120, 168, 201), np.float32) * 0.8 + tint * 0.2          # BGR of #C9A878
        lay = ctr * (1 - g) + edg * g
        sp = np.exp(-(((xx * 4 - (cx - 0.35 * r)) / (0.28 * r)) ** 2 + ((yy * 4 - (cy - 0.38 * r)) / (0.20 * r)) ** 2))
        lay = lay + cv2.resize(sp, (w, h))[..., None] * 60.0
        # a soft inner shadow along the rim gives the shape its thickness
        rim = np.clip(m - cv2.GaussianBlur(m, (0, 0), max(2.0, 0.03 * r)), 0, 1)[..., None]
        return np.clip(lay * (1 - 0.5 * rim), 0, 255).astype(np.uint8)

    def composite(self, v, t):
        base = self.ctx.get_img('out' if v < 0 else 'in', t)
        u = (v + self.pre) / (self.pre + self.post)
        s = 0.6 + 2.0 * e_in(u)
        w, h = self.ctx.size
        if not self.kw.get('flat'):
            if v < 0 and self.shade is None:
                self.shade = cv2.resize(base, (4, 4), interpolation=cv2.INTER_AREA).reshape(-1, 3).mean(0).astype(np.float32)
            f = u * 4.0                                         # scale 0.6, 1.0, 1.7, 2.6 over 4 frames @30
            seq = (0.6, 1.0, 1.7, 2.6, 3.6)
            i = min(3, int(f))
            s = seq[i] * (1 - (f - i)) + seq[i + 1] * (f - i)
            cx = (self.start[0] + (0.5 - self.start[0]) * ss(u * 1.6)) * w
            cy = (self.start[1] + (0.5 - self.start[1]) * ss(u * 1.6)) * h
            pts = fx.heart_poly() if self.shape == 'heart' else fx.star_poly()
            r = s * max(w, h) * 0.5
            m = fx.poly_mask((w, h), pts, cx, cy, r, rot=10 * (u - 0.5), blur=6)
            tint = self.shade if self.shade is not None else np.array((150, 180, 210), np.float32)
            comp = fx.blend_mask(base, self._shaded(base, m, cx, cy, r, tint), m)
            return fx.zoom_blur_c(comp, 0.2 * min(1.0, u * 1.5), center=(cx / w, cy / h))
        cx = (self.start[0] + (0.5 - self.start[0]) * ss(u * 1.6)) * w
        cy = (self.start[1] + (0.5 - self.start[1]) * ss(u * 1.6)) * h
        pts = fx.heart_poly() if self.shape == 'heart' else fx.star_poly()
        m = fx.poly_mask((w, h), pts, cx, cy, s * max(w, h) * 0.5, rot=10 * (u - 0.5), blur=6 + 20 * u)
        lay = np.empty_like(base)
        g = np.linspace(0, 1, h, dtype=np.float32)[:, None, None]
        lay[:] = (np.array(self.col_top, np.float32) * (1 - g) + np.array(self.col_bot, np.float32) * g).astype(np.uint8)
        return fx.blend_mask(base, lay, m)


class MaskWipe(T):
    """Character wipes: chevron (3 nested > bands), iris ring, wave bands, noise blob; colours from the palette."""
    name = 'chevron_wipe'
    pre = 2 * F30
    post = 3 * F30
    composite_pre = composite_post = True
    kind = 'chevron'

    def setup(self):
        pink, yellow = (224, 214, 247), (107, 196, 242)
        self.cols = [pink, yellow, (255, 255, 255)]
        self.seed = int(self.ctx.rng.integers(0, 1000))

    def composite(self, v, t):
        w, h = self.ctx.size
        u = (v + self.pre) / (self.pre + self.post)
        out = self.ctx.get_img('out', t)
        inc = self.ctx.get_img('in', t)
        if self.kind == 'chevron':
            lead = 1.05 - 1.6 * ss(u)
            masks = fx.chevron_masks((w, h), lead, n=3, band=0.14)
            img = out
            for m, c in zip(masks, self.cols):
                lay = np.empty_like(out)
                lay[:] = c
                img = fx.blend_mask(img, lay, m)
            reveal = fx.chevron_masks((w, h), lead + 0.42, n=1, band=0.14)[0]
            return fx.blend_mask(img, inc, reveal)
        if self.kind == 'iris':
            r = e_in(u) * 0.75 * math.hypot(w, h)
            base = fx.screen_layer(fx.zoom_blur_c(out, 0.3 * u), np.ones((8, 8, 3), np.float32), 0.5 * u)
            img = fx.blend_mask(base, inc, fx.iris_mask((w, h), r))
            ring = np.zeros((h // 4, w // 4, 3), np.float32)
            cv2.circle(ring, (w // 8, h // 8), int(r / 4), (0.88, 0.84, 0.97), max(1, int(h * 0.06 / 4)), cv2.LINE_AA)
            ring = ring + cv2.GaussianBlur(ring, (0, 0), w * 0.015 / 4) * 0.8
            return fx.screen_layer(img, ring, 1.0)
        if self.kind == 'wave':
            img = out
            front = 1.02 - 1.37 * ss(u)          # round 5: the bands are already in frame on the last outgoing frame
            for i, c in enumerate(self.cols):
                m = fx.wave_mask((w, h), front + i * 0.08, phase=i * 1.3)
                lay = np.empty_like(out)
                lay[:] = c
                img = fx.blend_mask(img, lay, m)
            return fx.blend_mask(img, inc, fx.wave_mask((w, h), front + 0.3, phase=4.0))
        # blob
        m = fx.blob_mask((w, h), ss(u), seed=self.seed)
        edge = fx.blob_mask((w, h), ss(u) + 0.08, seed=self.seed)
        lay = np.empty_like(out)
        lay[:] = self.cols[0]
        return fx.blend_mask(fx.blend_mask(out, lay, edge), inc, m)


class ChevronWipe(MaskWipe):
    name, kind = 'chevron_wipe', 'chevron'


class IrisWipe(MaskWipe):
    name, kind = 'iris_wipe', 'iris'
    pre, post = 3 * F30, 3 * F30


class WaveWipe(MaskWipe):
    name, kind = 'wave_wipe', 'wave'
    pre, post = 2 * F30, 3 * F30


class BlobWipe(MaskWipe):
    name, kind = 'blob_wipe', 'blob'
    pre, post = 6 * F30, 6 * F30


class CardTiltIn(T):
    """Hero: the new shot appears as a floating 3D card on the dark blurred outgoing and grows to full frame (10 frames @30)."""
    name = 'card_tilt_in'
    post = 10 * F30
    composite_post = True

    def setup(self):
        self.sgn = self.sign()

    def composite(self, v, t):
        w, h = self.ctx.size
        k = ss(v / self.post)
        out = self.ctx.get_img('out', t)
        inc = self.ctx.get_img('in', t)
        bg = fx.gain_u8(fx.gauss_px(out, self.ctx.pw(0.03)), 0.30)            # dark: sigma 3 %W, gain 0.30
        wf = 0.45 + 0.55 * k
        quad = fx.card_quad((w, h), w / 2, h / 2, w * wf, h * wf, rx=10 * (1 - k), ry=30 * (1 - k) * self.sgn, rz=-4 * (1 - k))
        layer, m = fx.warp_to_quad(inc, quad, (w, h))
        if k < 0.98:
            bg = fx.shadow_under(bg, m, self.ctx.pw(0.02), (0, self.ctx.px(20)), 0.5)
        return fx.blend_mask(bg, layer, m)


class PhotoCollage(T):
    """
    Hero: the outgoing shrinks into a white-bordered photo among 5-6 other photos on light paper, the
    camera pulling out (1.0 -> 0.45 over 12 frames @30), then a whip slide into the new shot.
    """
    name = 'photo_collage'
    pre = 12 * F30
    post = 0.12
    composite_pre = composite_post = True

    def setup(self):
        self.photos = None
        self.dir = self.sign()
        self._last = None

    def _layout(self):
        w, h = self.ctx.size
        stills = (self.ctx.stills(6) if self.ctx.stills else []) or []
        rng = self.ctx.rng
        # world positions (the live shot spans the whole frame at scale 1): the other photos lie around it
        # and come into view as the camera pulls out
        spots = [(-1.02, -0.62), (1.0, -0.58), (-1.08, 0.12), (1.06, 0.16), (-0.9, 0.86), (0.94, 0.82)]
        self.photos = []
        for (x, y), st in zip(spots, stills):
            pw = 0.85 * w
            ph = pw * st.shape[0] / st.shape[1]
            card = fx.bordered(cv2.resize(st, (int(pw), int(ph)), interpolation=cv2.INTER_AREA), 0.025)
            self.photos.append((card, w / 2 + x * w, h / 2 + y * h * 0.62, float(rng.uniform(-8, 8))))

    def _collage(self, t, k):
        w, h = self.ctx.size
        if self.photos is None:
            self._layout()
        s = 1.0 - 0.55 * e_out(k)                        # the whole collage pulls back
        canvas = fx.paper((w, h), (237, 237, 237), 0.02)
        for card, x, y, ang in self.photos:
            cx, cy = w / 2 + (x - w / 2) * s, h / 2 + (y - h / 2) * s
            lay, m = fx.affine_layer(card, cx, cy, card.shape[1] * s, ang, (w, h))
            canvas = fx.shadow_under(canvas, m, self.ctx.px(20), (self.ctx.px(6), self.ctx.px(8)), 0.4)
            canvas = fx.blend_mask(canvas, lay, m)
        live = self.ctx.get_img('out', t)
        card = fx.bordered(live, 0.025 * k)
        lay, m = fx.affine_layer(card, w / 2, h / 2, w * s * (1 + 0.05 * k), -3 * k, (w, h))
        canvas = fx.shadow_under(canvas, m, self.ctx.px(20), (self.ctx.px(6), self.ctx.px(8)), 0.4 * k)
        return fx.blend_mask(canvas, lay, m)

    def composite(self, v, t):
        if v < 0:
            k = 1 + v / self.pre
            self._last = self._collage(t, k)
            return self._last
        out = self._last if self._last is not None else self._collage(t, 1.0)
        inc = self.ctx.get_img('in', t)
        w = inc.shape[1]
        k = ss(v / self.post)
        off = int(round(self.dir * w * (1 - k)))
        canvas = np.empty_like(inc)
        if off >= 0:
            canvas[:, off:] = inc[:, :w - off]
            canvas[:, :off] = out[:, w - off:] if off else canvas[:, :0]
        else:
            canvas[:, :w + off] = inc[:, -off:]
            canvas[:, w + off:] = out[:, :-off]
        return fx.line_blur(canvas, self.ctx.pw(0.08), 0)


class FirstShotEntry(T):
    """The first shot after an intro: defocus 20 -> 0 px and scale 1.10 -> 1.0 over 5 frames @30."""
    name = 'first_shot_entry'
    post = 5 * F30

    def setup(self):
        # transition_params (optional): scale0 (1.10), defocus_px (20), defocus_s (the defocus clears over this; default
        # the 5-frame scale settle) and defocus_tau (exponential clear instead of linear: _sX2 measures the blur
        # excess 0.43 -> 0.30 -> 0.20 -> 0.11 -> 0 %W over ~0.75 s while the scale settles in 5 frames)
        self.scale0 = float(self.kw.get('scale0', 1.10))
        self.dpx = float(self.kw.get('defocus_px', 20))
        self.ds = float(self.kw.get('defocus_s', 5 * F30))
        self.dtau = self.kw.get('defocus_tau')
        # optional scale_tau: the push settles exponentially over scale_s (_sX2 remake round 3: flow 0.75 -> 0.34 ->
        # 0.20 -> 0.12 %W/frame over 0.6 s, a visible push, not a 5-frame snap)
        self.stau = self.kw.get('scale_tau')
        self.ss = float(self.kw.get('scale_s', 5 * F30))
        self.post = max(5 * F30, self.ds, self.ss if self.stau else 0.0)

    def cam_in(self, v):
        if self.stau:
            a, b = math.exp(-v / float(self.stau)), math.exp(-self.ss / float(self.stau))
            return (math.log(self.scale0) * max(0.0, a - b) / (1 - b), 0, 0, 0)
        return (math.log(self.scale0) * (1 - e_out(min(1.0, v / (5 * F30)))), 0, 0, 0)

    def fx_in(self, img, v):
        if self.dtau:
            k = max(0.0, math.exp(-v / float(self.dtau)) - math.exp(-self.ds / float(self.dtau))) / (1 - math.exp(-self.ds / float(self.dtau)))
        else:
            k = max(0.0, 1 - v / self.ds)
        return fx.gauss_px(img, self.ctx.px(self.dpx) * k)


class FadeIn(T):
    """Fade in from black (0.3-0.5 s) with a slight blur."""
    name = 'fade_in'

    def setup(self):
        self.post = float(self.kw.get('dur') or 0.4)

    def fx_in(self, img, v):
        k = v / self.post
        img = fx.gauss_px(img, self.ctx.px(6) * (1 - k))
        return fx.gain_u8(img, k)


class BarOpenReveal(T):
    """Letterbox bars open from a 9% slit to the full frame (curved lens-shaped edge while opening)."""
    name = 'bar_open_reveal'

    def setup(self):
        self.post = float(self.kw.get('dur') or 0.6)
        self.curved = self.kw.get('curved', True)

    def fx_in(self, img, v):
        k = e_out(v / self.post)
        h, w = img.shape[:2]
        half = (0.045 + 0.455 * k) * h
        if not self.curved:
            out = img.copy()
            out[:max(0, int(h / 2 - half))] = 0
            out[int(h / 2 + half):] = 0
            return out
        xs = np.linspace(-1, 1, w // 4, dtype=np.float32)
        bulge = 0.06 * h * (1 - xs ** 2) * (1 - k)
        m = np.zeros((h // 4, w // 4), np.float32)
        yy = np.arange(h // 4, dtype=np.float32)[:, None] * 4
        m[(np.abs(yy - h / 2) < (half + bulge)[None, :])] = 1.0
        m = cv2.resize(cv2.GaussianBlur(m, (0, 0), 0.8), (w, h))
        return fx.blend_mask(np.zeros_like(img), img, m)


class VignetteFadeIn(T):
    """Dance AMV intro: exposure 0 -> 1 over 2.4 s under a heavy vignette that relaxes."""
    name = 'vignette_fade_in'

    def setup(self):
        self.post = float(self.kw.get('dur') or 2.4)
        self.maps = {}

    def fx_in(self, img, v):
        k = v / self.post
        strength = round(0.9 - 0.6 * k, 2)
        key = (strength, img.shape[1], img.shape[0])
        if key not in self.maps:
            self.maps[key] = fx.vignette_map((img.shape[1], img.shape[0]), strength, power=1.3, inner=0.1)
        return fx.gain_u8(fx.apply_map(img, self.maps[key]), ss(k))


class Breath(T):
    """Dance smooth intro: fade in 1.0-1.6 s from black with defocus 1 -> 0."""
    name = 'breath'

    def setup(self):
        self.post = float(self.kw.get('dur') or 1.3)

    def fx_in(self, img, v):
        k = ss(v / self.post)
        return fx.gain_u8(fx.gauss_px(img, self.ctx.pw(0.02) * (1 - k)), k)


# ---------------------------------------------------------------- dance cuts

class DefocusIn(T):
    """Outgoing last 4 frames @60: x1.0 -> 1.35 and defocus 0 -> 0.6; incoming defocus [1, .9, .6, .3, .1] (1 = 2% W), x1.2 decaying."""
    name = 'defocus_in'
    pre = 4 * F60
    post = 6 * F60
    seq = [1.0, 0.9, 0.6, 0.3, 0.1, 0.0]

    def fx_out(self, img, u):
        k = 1 - u / self.pre
        return fx.gain_u8(fx.gauss_px(img, self.ctx.pw(0.02) * 0.6 * k), 1 + 0.35 * k)

    def fx_in(self, img, v):
        i = min(len(self.seq) - 1, int(v / F60))
        a = self.seq[i]
        return fx.gain_u8(fx.gauss_px(img, self.ctx.pw(0.02) * a), 1 + 0.2 * a)


class ZoomBlurIn(T):
    """Outgoing: zoom blur + x1.12 over 2 frames @60; incoming s_k = 1.22^(0.75^k) @60 with zoom blur 0.25 x 0.5^k."""
    name = 'zoom_blur_in'
    pre = 2 * F60
    post = 12 * F60

    def cam_out(self, u):
        return (math.log(1.12) * (1 - u / self.pre), 0, 0, 0)

    def fx_out(self, img, u):
        return fx.zoom_blur_c(img, 0.15 * (1 + (1 - u / self.pre)))

    def cam_in(self, v):
        return (math.log(1.22) * 0.75 ** (v / F60), 0, 0, 0)

    def fx_in(self, img, v):
        k = v / F60
        img = fx.zoom_blur_c(img, 0.25 * 0.5 ** k)
        if k < 1:
            img = self.white(img, 0.1)
        return img


class FlashIn(T):
    """Exposure-in cut: img * (1 + 0.6 x 0.5^k) + 30 x 0.5^k over 3 frames @30."""
    name = 'flash_in'
    post = 4 * F30

    def fx_in(self, img, v):
        k = v / F30
        a = bright_att(img)
        return fx.gain_u8(img, 1 + 0.6 * 0.5 ** k * a, 30 * 0.5 ** k * a)


class WhipDiag(T):
    """Dance whip: tiled copies smeared along a 45 deg diagonal (4 frames @60), then a 1-frame crossfade into the sharp shot."""
    name = 'whip_diag'
    pre = F60
    post = 3 * F60
    composite_pre = composite_post = True

    def setup(self):
        self.sg = self.sign()

    def composite(self, v, t):
        w, h = self.ctx.size
        k = int(round((v + self.pre) / F60))
        ang = 45 if self.sg > 0 else 135
        dx, dy = math.cos(math.radians(ang)), math.sin(math.radians(ang))
        out = self.ctx.get_img('out', t)
        if k <= 0:
            return fx.line_blur(fx.wrap_shift(out, int(0.3 * w * dy), int(0.3 * w * dx)), 0.25 * w, ang)
        inc = self.ctx.get_img('in', t)
        if k <= 2:
            o = fx.wrap_shift(out, int(0.4 * w * dy), int(0.4 * w * dx))
            i = fx.wrap_shift(inc, -int(0.2 * w * dy), -int(0.2 * w * dx))
            return fx.line_blur(fx.mix_u8(o, i, 0.5), 0.18 * w, ang)
        return fx.mix_u8(fx.line_blur(inc, 0.05 * w, ang), inc, 0.5)


class PatternFlash(T):
    """Cute pattern card (lavender then pink argyle, 2 frames @60), then the incoming defocused with a x1.07 settle."""
    name = 'pattern_flash'
    post = 6 * F60
    seq = [0.8, 0.6, 0.35, 0.15]

    def fx_in(self, img, v):
        k = int(v / F60)
        size = (img.shape[1], img.shape[0])
        if k == 0:
            return fx.argyle(size, (235, 205, 230), (215, 190, 240))
        if k == 1:
            return fx.argyle(size, (215, 190, 240), (235, 215, 250))
        a = self.seq[min(3, k - 2)] if k - 2 < 4 else 0.0
        return fx.gauss_px(img, self.ctx.pw(0.006) * a)             # round 5: Uf f306 measures 0.15 %W then sharp

    def cam_in(self, v):
        return (math.log(1.07) * ex(max(0.0, v - 2 * F60), 0.06), 0, 0, 0)


class DarkBlurIn(T):
    """Section change: a near-black frame with faint specks, then the incoming at exposure .32 -> .98 while defocused 1 -> .3."""
    name = 'dark_blur_in'
    post = 6 * F60
    seq = [0.32, 0.57, 0.75, 0.89, 0.98]

    def fx_in(self, img, v):
        k = int(v / F60)
        if k == 0:
            base = np.full_like(img, 22)
            return fx.screen_layer(base, fx.dust_specks(self.ctx.size, seed=3), 1.0)
        g = self.seq[min(4, k - 1)]
        return fx.gain_u8(fx.gauss_px(img, self.ctx.pw(0.009) * (1.0 - 0.7 * (k - 1) / 4)), g)   # round 5: 91tp f328 measures 0.15-0.24 %W


class LensZoomThrough(T):
    """Into a new section: zoom blur .5 + x2 exposure, then barrel distortion .25 -> 0 with chromatic aberration, a 9 deg roll, x1.35 -> 1."""
    name = 'lens_zoomthrough'
    post = 8 * F30

    def cam_in(self, v):
        k = v / self.post
        rot = 9.0 if F30 <= v < 2 * F30 else 0.0
        return (math.log(1.35) * (1 - e_out(k)), rot, 0, 0)

    def fx_in(self, img, v):
        f = int(v / F30)
        if f == 0:
            return fx.gain_u8(fx.zoom_blur_c(img, 0.5), 2.0)
        if f <= 3:
            a = 1 - (f - 1) / 3
            return fx.lens_distort(img, 0.25 * a, 0.02 * a)
        return img


# ---------------------------------------------------------------- character cuts

class FlashZoom(T):
    """
    The quick-velocity house cut. Outgoing (last 130 ms): camera rush-in, zoom blur, exposure, roll.
    Incoming: gain 1 + 1.8 e^(-t/.03) + .17 e^(-t/.2), a white layer .8 e^(-t/.035) through a soft
    vignette, ln s = A (1 - smoothstep(t/.27)), roll r0 -> 0, blur .9% W decaying, zoom blur, 3 px RGB fringe.
    """
    name = 'flash_zoom'
    pre = 0.13
    post = 0.3

    def setup(self):
        self.A = self.rnd(0.18, 0.35)
        self.r0 = self.sign() * 3.0
        self.ro = self.sign() * 1.9
        self.vig = None

    def cam_out(self, u):
        k = 1 - u / self.pre
        # AV round: |zoom| at the last outgoing frames p50 1.3-2 %/frame (44 refs) - the 0.08 rush measured 6 %/frame
        return (0.03 * e_in(k) ** 2, self.ro * k, 0, 0)

    def fx_out(self, img, u):
        k = 1 - u / self.pre
        if not hasattr(self, 'tint'):
            m = cv2.resize(img, (8, 8), interpolation=cv2.INTER_AREA).reshape(-1, 3).mean(0)
            self.tint = np.clip(255.0 - 0.35 * (float(m.max()) - m), 200, 255)       # white leaning to the outgoing hue
        img = fx.zoom_blur_c(img, 0.3 * k ** 1.6)
        # AV round: the exposure peaks on the LAST outgoing frames (x1.33 at -2..-1 @30), not after the switch
        return fx.gain_u8(img, 1 + 0.33 * k * k)

    def cam_in(self, v):
        return (self.A * (1 - ss(v / 0.27)), self.r0 * (1 - ss(v / 0.2)), 0, 0)

    def fx_in(self, img, v):
        if v < 0.2:
            img = fx.gauss_px(img, self.ctx.pw(0.0025) * (1 - v / 0.2) ** 1.5)   # AV round: incoming excess 0.05 %W at +1 (was 0.24)
        if v < 0.12:
            img = fx.zoom_blur_c(img, 0.12 * (1 - v / 0.12))
        if v < 0.05:
            img = fx.rgb_split(img, self.ctx.px(3))
        img = fx.gain_u8(img, 1 + (1.0 * ex(v, 0.03) + 0.12 * ex(v, 0.2)) * (bright_att(img) if v > 0.04 else 1.0))
        wv = 0.8 * ex(v, 0.035)
        if wv > 0.02:
            if self.vig is None or self.vig.shape[:2] != img.shape[:2]:
                self.vig = fx.vignette_map((img.shape[1], img.shape[0]), 0.4, power=1.2, inner=0.0)
            lay = np.empty_like(img)
            lay[:] = (getattr(self, 'tint', np.array([255.0, 255.0, 255.0])) * wv).astype(np.uint8)
            img = fx.screen_u8(img, fx.apply_map(lay, self.vig))
        return img


class MBCut(T):
    """Motion-blur cut: outgoing ln s +0.18 over 66 ms with roll, zoom blur; incoming from ln s .13 settling (tau 22 ms), 4 blurred frames @60."""
    name = 'mb_cut'
    pre = 4 * F60
    post = 0.12

    def setup(self):
        self.ro = self.sign() * 2.6
        self.ri = -self.ro * 0.8

    def cam_out(self, u):
        k = 1 - u / self.pre
        return (0.18 * k ** 1.5, self.ro * k, 0, 0)

    def fx_out(self, img, u):
        return fx.zoom_blur_c(img, 0.12) if u < 2 * F60 else img

    def cam_in(self, v):
        return (0.13 * ex(v, 0.022) + 0.04 * ex(v, 0.1), self.ri * ex(v, 0.03), 0, 0)

    def fx_in(self, img, v):
        f = int(v / F60)
        if f <= 1:                                              # round 5: KSjp 13.5 s measures 0.11, 0.09 %W (was .35 / .15)
            return fx.gauss_px(img, self.ctx.pw(0.0015))
        if f <= 3:
            return fx.gauss_px(img, self.ctx.pw(0.0007))
        return img


class ZoomThroughColorField(T):
    """
    The drop / block-start cut: the outgoing pushes in (ln s .04 -> .16), rolls and zoom-blurs, the picture
    becomes a smooth field of its own colours for 3 frames @60, then the incoming lands at x1.46, darker,
    and settles.
    """
    name = 'zoom_through_color_field'
    pre = 0.2
    post = 3 * F60 + 0.3

    def setup(self):
        self.ro = self.sign() * 4.5
        self.field = 3 * F60

    def cam_out(self, u):
        k = 1 - u / self.pre
        return (0.04 + 0.12 * e_in(k), self.ro * e_in(k), 0, 0)

    def fx_out(self, img, u):
        k = 1 - u / self.pre
        if u < 0.066:
            img = fx.zoom_blur_c(img, 0.6 * (1 - u / 0.066))
        return fx.gain_u8(img, 1.05 + 0.1 * k)

    def cam_in(self, v):
        w = v - self.field
        if w < 0:
            return ZERO
        return (0.38 * (1 - ss(w / 0.16)) - 0.06 * ss((w - 0.1) / 0.2), 0, 0, 0)

    def fx_in(self, img, v):
        if v < self.field:
            return fx.color_field(img)
        w = v - self.field
        img = fx.gauss_px(img, self.ctx.pw(0.004) * max(0.0, 1 - w / 0.12))
        return fx.gain_u8(img, 0.72 + 0.28 * ss(w / 0.1))


class WhipHorizontal(T):
    """Smear cut: outgoing smeared 3 / 8 / 25% W over its last 3 frames @30 and pushed; incoming smeared 20 -> 0% W, offset from the smear side."""
    name = 'whip_horizontal'
    pre = 3 * F30
    post = 0.2

    def setup(self):
        self.d = self.sign()

    def cam_out(self, u):
        k = 1 - u / self.pre
        return (0.09 + 0.05 * k, 0, -0.08 * k * self.d, 0)

    def fx_out(self, img, u):
        f = int(u / F30)
        L = [0.25, 0.08, 0.03][min(2, f)]
        return fx.line_blur(img, self.ctx.pw(L), 0)

    def cam_in(self, v):
        k = e_out(v / 0.15)
        # round 5: the roll decays to 0 (it used to stop at 1.3 deg and POP to 0 when the transition ended)
        return (0.34 * (1 - k), 3.7 * (1 - e_out(v / 0.2)) if v < 0.2 else 0.0, 0.12 * self.d * (1 - k), 0)

    def fx_in(self, img, v):
        f = int(v / F30)
        L = [0.20, 0.10, 0.04, 0.0, 0.0][min(4, f)]
        img = fx.line_blur(img, self.ctx.pw(L), 0)
        return fx.gain_u8(img, 1.12) if v < 0.066 else img


class WhipVertical(T):
    """Soft-romance smear: outgoing vertical smear 15% then 45% H (2 frames @24, gain 1.23), incoming 1 frame of 15% H then sharp."""
    name = 'whip_vertical'
    pre = 2 / 24.0
    post = 0.14

    def fx_out(self, img, u):
        h = img.shape[0]
        if u > 1 / 24.0:
            return fx.line_blur(img, 0.15 * h, 90)
        return fx.gain_u8(fx.line_blur(img, 0.45 * h, 90), 1.23)

    def cam_in(self, v):
        return (0.04 * (1 - ss(v / 0.13)), 0, 0, 0)

    def fx_in(self, img, v):
        if v < 1 / 24.0:
            return fx.line_blur(img, 0.15 * img.shape[0], 90)
        return fx.line_blur(img, 0.15 * img.shape[0] * max(0.0, 1 - (v - 1 / 24.0) / 0.09), 90)


class ImpactZoom1Frame(T):
    """1-frame impact zoom: ln s = .15 e^(-t/.055), roll 3 -> 0 (tau 25 ms), blur .25% W (tau 30 ms), gain 1.09 (tau 40 ms)."""
    name = 'impact_zoom_1frame'
    post = 0.2

    def setup(self):
        self.r = self.sign() * 3.0

    def cam_in(self, v):
        return (0.15 * ex(v, 0.055), self.r * ex(v, 0.025), 0, 0)

    def fx_in(self, img, v):
        img = fx.gauss_px(img, self.ctx.pw(0.0025) * ex(v, 0.03))
        return fx.gain_u8(img, 1 + 0.09 * ex(v, 0.04))


class SpinBlur(T):
    """Spin cut: outgoing roll accelerates (to 2 deg / frame @30) while pulling back; incoming starts rolled 12-20 deg the other way and unwinds (300 ms), rotationally blurred."""
    name = 'spin_blur'
    pre = 4 * F30
    post = 0.3

    def setup(self):
        self.d = self.sign()
        self.r0 = -self.d * self.rnd(12, 20)

    def _rot_out(self, u):
        f = (self.pre - u) * 30.0                          # cumulative 0, 0.7, 2, 4 deg = 0.25 f^2
        return self.d * 0.25 * f * f

    def cam_out(self, u):
        return (-0.15 * e_in(1 - u / self.pre), self._rot_out(u), 0, 0)

    def fx_out(self, img, u):
        f = (self.pre - u) * 30.0
        return fx.rot_blur(img, self.d * 0.5 * max(0.0, f))   # smear = the per-frame step (0.5 f deg)

    def _rot_in(self, v):
        return self.r0 * (1 - e_out(v / self.post))

    def cam_in(self, v):
        return (0.14 * (1 - e_out(v / self.post)), self._rot_in(v), 0, 0)

    def fx_in(self, img, v):
        if v > 6 * F30:
            return img
        rate = self._rot_in(v) - self._rot_in(v + F30)      # arc per frame @30
        return fx.rot_blur(img, 0.5 * rate)


class MirrorTileScaleIn(T):
    """The incoming starts at x0.4 tiled with mirror copies and a white flash, scales to 1.0 over 4 frames @30 with zoom blur."""
    name = 'mirror_tile_scale_in'
    post = 5 * F30

    SCALES = (0.40, 0.62, 0.82, 0.95, 1.0, 1.0)

    def cam_in(self, v):
        f = min(4.999, max(0.0, v * 30.0))
        i = int(f)
        s = self.SCALES[i] * (1 - (f - i)) + self.SCALES[i + 1] * (f - i)
        return (math.log(s), 0, 0, 0)

    def fx_in(self, img, v):
        if v < 2 * F30:
            img = fx.zoom_blur_c(img, 0.15 * (1 - v / (2 * F30)))
        if v < 2 * F30:
            img = self.white(img, 0.4 * (1 - v / (2 * F30)))
        return img


class TintFlash(T):
    """On the cut frame the picture is the palette colour over a blurred copy (x0.75), then 0.45 / 0.2 / 0 on the next frames @30."""
    name = 'tint_flash'
    post = 3 * F30

    def setup(self):
        c = np.uint8([[list(self.ctx.colors[0])]])
        hsv = cv2.cvtColor(c, cv2.COLOR_BGR2HSV)
        hsv[0, 0, 1], hsv[0, 0, 2] = int(0.55 * 255), int(0.95 * 255)
        self.col = tuple(int(x) for x in cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)[0, 0])

    def fx_in(self, img, v):
        f = int(v / F30)
        a = [0.75, 0.45, 0.2][min(2, f)]
        if f == 0:
            img = fx.gauss_px(img, self.ctx.pw(0.015))
        return fx.fill_mix(img, self.col, a)


class WhiteOut(T):
    """Soft cut: the outgoing blurs and brightens over 230 ms, a white hold of 150 ms, the incoming emerges (white 1 -> 0 over 300 ms) pulling out."""
    name = 'white_out'
    pre = 0.23
    post = 0.15 + 0.4
    hold_s = 0.15
    color = (250, 253, 255)

    def fx_out(self, img, u):
        k = e_in(1 - u / self.pre)
        img = fx.gauss_px(img, self.ctx.pw(0.0007 + 0.0033 * k))
        return fx.gain_u8(img, 1 + 0.23 * k)

    def cam_in(self, v):
        return (0.25 * (1 - e_out(v / 0.4)), 0, 0, 0)

    def fx_in(self, img, v):
        if self.kw.get('white_cap') is not None:
            # capped variant: peak alpha for at most 2 frames, gone 3 frames later, so the layout under it reads
            k = v * 30.0
            a = 1.0 if k < 2 else max(0.0, 1 - (k - 2) / 3.0)
            return self.white(fx.gauss_px(img, self.ctx.pw(0.006) * a), a, self.color)
        if v < self.hold_s:
            return self.white(img, 1.0, self.color)
        w = v - self.hold_s
        img = fx.gauss_px(img, self.ctx.pw(0.006) * max(0.0, 1 - w / 0.17))        # round 5: rfsj 6.5 s: 0.42, 0.24, 0.15 %W after the hold
        return self.white(img, 1 - e_out(w / 0.22), self.color)


class DefocusDissolve(T):
    """Soft dissolve: both pictures defocus toward the middle (peak .8% W) while crossfading (7 frames @30), +8% exposure at the midpoint."""
    name = 'defocus_dissolve'
    pre = 3.5 * F30
    post = 3.5 * F30
    composite_pre = composite_post = True

    def composite(self, v, t):
        a = (v + self.pre) / (self.pre + self.post)
        A = fx.gauss_px(self.ctx.get_img('out', t), self.ctx.pw(0.008) * min(1.0, 2 * a))
        B = fx.gauss_px(self.ctx.get_img('in', t), self.ctx.pw(0.008) * min(1.0, 2 * (1 - a)))
        return fx.gain_u8(fx.mix_u8(A, B, ss(a)), 1 + 0.08 * math.sin(math.pi * a))


class DipBlack(T):
    """Fade to black (7 frames @60), 1-2 black frames just before the switch, fade back in with a small punch-in and defocus."""
    name = 'dip_black'
    out_g = [0.92, 0.82, 0.69, 0.53, 0.34, 0.17, 0.03]
    in_g = [0.03, 0.14, 0.3, 0.48, 0.65, 0.79, 0.9, 0.99]
    pre = (7 + 2) * F60
    post = 12 * F60

    def fx_out(self, img, u):
        f = int((self.pre - u) / F60)
        if f >= len(self.out_g):
            return np.zeros_like(img)
        return fx.gain_u8(img, self.out_g[f])

    def cam_in(self, v):
        # round 5: rises to 0.08 then decays (the first branch used to FALL to 0 and the second jumped back to 0.08)
        return (0.08 * e_out(v / (4 * F60)) if v < 4 * F60 else 0.08 * ex(v - 4 * F60, 0.1), 0, 0, 0)

    def fx_in(self, img, v):
        f = int(v / F60)
        img = fx.gauss_px(img, self.ctx.pw(0.0025) * max(0.0, 1 - f / 8))          # round 5: mFHp 17.35 s measures 0.12, 0.11, 0.10 %W
        return fx.gain_u8(img, self.in_g[f] if f < len(self.in_g) else 1.0)


class WhiteFlashCut(T):
    """
    Raw crisp. AV round (motion3d + character-av: 10 JDgu cuts, native 60 fps): the exposure at these cuts is x1.01
    median - the "flash" is the bright drawing the editor cuts to on the kick, not white frames. Now ONE 60 fps frame
    of a 0.35 veil and gain 1.15 -> 1.0 (tau 35 ms): a crisp hard cut with a lift, never a white-out.
    """
    name = 'white_flash_cut'
    post = F60 + 0.15

    def fx_in(self, img, v):
        if v < F60:
            return self.white(img, 0.35)
        return fx.gain_u8(img, 1 + 0.15 * ex(v - F60, 0.035) * bright_att(img))


class GlowCut(T):
    """Hard cut with a bloom pulse 0 -> .8 -> .3 over 150 ms (glow pulse)."""
    name = 'glow_cut'
    post = 0.3

    def fx_in(self, img, v):
        # round 5: decays to 0 by 0.2 s (it used to sit at 0.3 and pop off when the transition ended)
        k = 0.8 * math.sin(math.pi * min(1.0, v / 0.15)) if v < 0.075 else 0.8 * max(0.0, 1 - (v - 0.075) / 0.125) ** 1.5
        return fx.bloom_boost(img, k, threshold=0.6)


# ---------------------------------------------------------------- K-pop cuts

class BlurInPunch(T):
    """
    The K-pop house cut. Outgoing 8 frames @60: blur .11 -> .40% W, pulling back 3% a frame and rolling
    -1 deg. Incoming 15 frames: blur .5% W (1 - k/15)^.9, gain 1 + .1 e^(-t/.1), and the entry settle
    s = 1 + A (1 - e^(-t/.085)) (70% punch-in, stays) or 1 + A e^(-t/.085) (30% pull-out).
    """
    name = 'blur_in_punch'
    pre = 8 * F60
    post = 15 * F60
    hold = True
    sig_out = [0.11, 0.12, 0.15, 0.18, 0.27, 0.38, 0.38, 0.40]
    variant = None

    def setup(self):
        lo, hi = self.kw.get('A_range') or (0.15, 0.35)
        self.A = self.rnd(lo, hi)
        self.punch_in = self.ctx.rng.random() < self.kw.get('in_p', 0.7)
        self.tau = 0.085
        self.ang = self.rnd(0, 180)
        self.rot0 = self.sign() * self.rnd(8, 11)

    def cam_out(self, u):
        # AV round: the pull-back (-4.7 %/frame into the switch, kpop-av) comes from the camera's pre_cut_pull, which
        # carries its own cover headroom; a negative ln s here was clamped to 0 by engine.shot_cam (measured 0 %/frame)
        k = 1 - u / self.pre
        return (0.0, -1.0 * k, 0, 0)

    def fx_out(self, img, u):
        f = min(7, int((self.pre - u) / F60))
        return fx.gauss_px(img, self.ctx.pw(self.sig_out[f] / 100))

    def cam_in(self, v):
        if self.punch_in:
            lns = math.log(1 + self.A * (1 - ex(v, self.tau)))
        else:
            lns = math.log(1 + self.A * ex(v, self.tau))
        rot = self.rot0 * ex(v, 0.03) if self.variant == 'rotate' else 0.0
        dx = 0.03 * ex(v / F60, 4) * math.sin(2 * math.pi * 11 * v) if (self.variant == 'shake' and os.environ.get('EDIT_NO_SHAKE', '0') != '1') else 0.0
        return (lns, rot, dx, 0.0)

    def fx_in(self, img, v):
        k = v / F60
        if k < 15:
            if self.variant == 'zoom':
                img = fx.zoom_blur_c(img, 0.30 * max(0.0, 1 - k / 7.0) ** 1.5)
            else:
                # AV round (mFHp, 10 cuts @60): the incoming blur HOLDS ~0.3 %W for the first 6 frames @60 (peak +66..+83 ms,
                # while the song's attack is heard) and is gone by +10 frames; the old linear fade peaked on the switch
                kk = float(getattr(self, 'in_frames', 15))
                hold = min(6.0, kk * 0.4)
                a = 1.0 if k < hold else max(0.0, 1 - (k - hold) / max(1.0, kk - hold - 5)) ** 1.3
                img = fx.gauss_px(img, self.ctx.pw(0.0036) * a)
            if self.variant == 'shake' and k < 10:
                img = fx.line_blur(img, self.ctx.pw(0.08) * (1 - k / 10) ** 2, self.ang)
        return fx.gain_u8(img, 1 + 0.10 * ex(v, 0.1))


class ShakeBlur(BlurInPunch):
    name, variant = 'shake_blur', 'shake'


class BlurRotate(BlurInPunch):
    name, variant = 'blur_rotate', 'rotate'


class BlurZoom(BlurInPunch):
    name, variant = 'blur_zoom', 'zoom'


class ZoomBlurFlash(T):
    """First loud downbeat: outgoing x1 -> 1.12 (ease-in) with zoom blur 0 -> .25 over 9 frames @60; incoming punch-in A .45 with zoom blur .3 -> 0 and gain 1.14 -> 1."""
    name = 'zoom_blur_flash'
    pre = 9 * F60
    post = 12 * F60
    hold = True

    def cam_out(self, u):
        return (math.log(1.12) * e_in(1 - u / self.pre), 0, 0, 0)

    def fx_out(self, img, u):
        return fx.zoom_blur_c(img, 0.25 * (1 - u / self.pre))

    def cam_in(self, v):
        return (math.log(1 + 0.45 * (1 - ex(v, 0.10))), 0, 0, 0)

    def fx_in(self, img, v):
        k = v / F60
        if k < 12:
            img = fx.zoom_blur_c(img, 0.40 * max(0.0, 1 - k / 6.0) ** 1.5)      # 0.4 -> 0 over 3 frames @30, then nothing
            img = fx.gain_u8(img, 1.14 - 0.14 * k / 12)
        return img


class HardPunch(T):
    """Hard cut with the entry settle only: ln s up to A ~ U(.08, .3) in 4 frames, then an exponential settle (stays)."""
    name = 'hard_punch'
    post = 0.3
    hold = True

    def setup(self):
        self.A = self.rnd(0.08, 0.3)

    def cam_in(self, v):
        return (math.log(1 + self.A * (1 - ex(v, 0.085))) if v > 0 else 0.0, 0, 0, 0)


class VSlideWrap(T):
    """Vertical slide with wrap-around: dy = .20 H (1 - ease_out(k/8)) over 8 frames @60, vertical blur, 4% punch-in."""
    name = 'vslide_wrap'
    post = 8 * F60 + 0.2

    def setup(self):
        self.d = self.sign()

    def _dy(self, v, h):
        return 0.20 * h * (1 - e_out(v / (8 * F60)))

    def cam_in(self, v):
        return (math.log(1.04) * (min(1.0, v / (2 * F30)) if v < 2 * F30 else ex(v - 2 * F30, 0.1)), 0, 0, 0)

    def fx_in(self, img, v):
        if v >= 8 * F60:
            return img
        h = img.shape[0]
        dy = self._dy(v, h)
        d2 = self._dy(v + F60, h)
        img = fx.wrap_shift(img, int(self.d * dy))
        return fx.line_blur(img, 1.2 * abs(dy - d2), 90)


class _PreReveal(BlurInPunch):
    """Base for K-pop hero reveals: a preview of the next shot over the current one, then a blur-in punch cut."""
    composite_pre = True
    hold = True

    def setup(self):
        super().setup()
        self.pre = float(self.kw.get('pre') or self.default_pre())

    def default_pre(self):
        return 0.37

    def base(self, t, u):
        """The current shot defocusing (to .8% W) and darkening (x.75) under the preview."""
        k = min(1.0, (self.pre - u) / max(0.05, min(0.4, self.pre)))
        img = self.ctx.get_img('out', t)
        return fx.gain_u8(fx.gauss_px(img, self.ctx.pw(0.008) * k), 1 - 0.25 * k)


class CutoutPrereveal(_PreReveal):
    """The next shot's subject as a white-outlined sticker rises from below over the defocused current shot, then the cut."""
    name = 'cutout_prereveal'

    def composite(self, v, t):
        u = -v
        img = self.base(t, u)
        m = self.ctx.matte() if self.ctx.matte else None
        if m is None:
            return img
        src, mask = m
        w, h = self.ctx.size
        rise = e_out(min(1.0, (self.pre - u) / (15 * F60)))
        dy = int((1 - rise) * 0.6 * h)
        ring = None
        if not self.kw.get('smooth_ring'):
            try:                                     # round 4: the hand-cut JAGGED sticker ring (kpop mFHp 10.23-10.60)
                import matte as MT
                ring = MT.sticker_outline(mask, self.ctx.pw(0.008), seed=int(self.ctx.rng.integers(0, 999)))
                ring = cv2.GaussianBlur(ring, (0, 0), 0.8)
            except Exception:  # noqa: BLE001
                ring = None
        if ring is None:
            ring = cv2.dilate((mask > 0.5).astype(np.uint8), np.ones((3, 3), np.uint8), iterations=max(1, int(self.ctx.pw(0.008))))
            ring = cv2.GaussianBlur(ring.astype(np.float32), (0, 0), max(0.5, self.ctx.pw(0.004)))
        white = np.full_like(src, 255)
        glow = cv2.GaussianBlur(ring, (0, 0), self.ctx.pw(0.012)) * 0.8
        M = np.float32([[1, 0, 0], [0, 1, dy]])
        ring_s = cv2.warpAffine(np.clip(ring, 0, 1), M, (w, h))
        glow_s = cv2.warpAffine(np.clip(glow, 0, 1), M, (w, h))
        mask_s = cv2.warpAffine(mask, M, (w, h))
        src_s = cv2.warpAffine(src, M, (w, h))
        img = fx.screen_layer(img, cv2.merge([glow_s, glow_s, glow_s]), 0.8)
        img = fx.blend_mask(img, white, ring_s)
        return fx.blend_mask(img, src_s, mask_s)


class PeekWindow(_PreReveal):
    """A tilted window over the lower face shows the next shot at the same coordinates (white-tinted for 3 frames), then the cut."""
    name = 'peek_window'

    in_frames = 4               # round 3: the incoming is crisp 2 frames @30 after the join (ref continues crisp)

    def default_pre(self):
        return 7 * F30          # the window POPS at join - 7 frames @30 (0.23 s)

    def setup(self):
        super().setup()
        self.ang = self.sign() * self.rnd(5, 8)      # never 0 deg

    def composite(self, v, t):
        u = -v
        w, h = self.ctx.size
        img = self.ctx.get_img('out', t)
        inc = self.ctx.get_img('in', t)
        fx_, fy_ = self.ctx.focus
        rect = np.array([[-0.5, -0.5], [0.5, -0.5], [0.5, 0.5], [-0.5, 0.5]], np.float32) * [0.38 * w, 0.31 * h]
        c, s = math.cos(math.radians(self.ang)), math.sin(math.radians(self.ang))
        # kpop.json puts the window on the LOWER face (face centre + 0.25 face height), not on the eyes;
        # ctx.focus is the face centre and a face is ~0.30 H, so +0.075 H is that quarter-face offset.
        pts = rect @ np.array([[c, s], [-s, c]], np.float32) + [fx_ * w, min(0.80, fy_ + 0.075 + 0.045) * h]
        m = np.zeros((h, w), np.uint8)
        cv2.fillConvexPoly(m, np.round(pts * 16).astype(np.int32), 255, cv2.LINE_AA, shift=4)
        mask = m.astype(np.float32) / 255
        # the preview must be READABLE: a near-black crop makes the peek show nothing at all
        # contrast-normalise the preview to p2 / p98 = 0.02 / 0.98 of the window's own content
        ys, xs = np.nonzero(m[::8, ::8])
        if len(ys) > 50:
            g = cv2.cvtColor(inc[::8, ::8], cv2.COLOR_BGR2GRAY)[ys, xs].astype(np.float32)
            lo, hi = np.percentile(g, 2), np.percentile(g, 98)
            if hi - lo > 12:
                a = min(3.0, (0.96 * 255.0) / float(hi - lo))
                inc = cv2.convertScaleAbs(inc, alpha=a, beta=float(0.02 * 255.0 - lo * a))
        f = (self.pre - u) * 30.0                # frames since the pop: tint 0.9, 0.5, 0.15, 0
        seq = (0.9, 0.5, 0.15, 0.0, 0.0)
        i = int(min(3, max(0, f)))
        tint = seq[i] * (1 - (f - i)) + seq[i + 1] * (f - i) if f < 4 else 0.0
        if tint > 0.003:
            inc = fx.fill_mix(inc, (255, 255, 255), tint)
        return fx.blend_mask(img, inc, mask)


class SlicesReveal(_PreReveal):
    """Three horizontal slices over eyes / mouth / chin pop in on 8th notes showing the next shot, each 1 white frame first."""
    name = 'slices_reveal'

    def default_pre(self):
        return 1.5 * self.ctx.beat

    def composite(self, v, t):
        u = -v
        w, h = self.ctx.size
        img = self.ctx.get_img('out', t)
        if u < 6 * F60:
            img = fx.gauss_px(img, self.ctx.pw(0.005) * (1 - u / (6 * F60)))
        inc = self.ctx.get_img('in', t)
        fx_ = self.ctx.focus[0]
        for i, yc in enumerate((0.36, 0.5, 0.64)):
            appear = self.pre - i * 0.5 * self.ctx.beat
            if u > appear:
                continue
            y0, y1 = int((yc - 0.065) * h), int((yc + 0.065) * h)
            x0, x1 = int(max(0, (fx_ - 0.225) * w)), int(min(w, (fx_ + 0.225) * w))
            img = img.copy() if img is self.ctx.get_img('out', t) else img
            if appear - u < F60:
                img[y0:y1, x0:x1] = fx.fill_mix(inc[y0:y1, x0:x1], (255, 255, 255), 0.9)
            else:
                img[y0:y1, x0:x1] = inc[y0:y1, x0:x1]
        return img


class PhotoClones(_PreReveal):
    """Three stills of the same subject pop in as tilted cards from the corners on 8th notes; the frame blurs for the cut."""
    name = 'photo_clones'

    def default_pre(self):
        return 1.5 * self.ctx.beat

    def setup(self):
        super().setup()
        self.cards = None

    def composite(self, v, t):
        u = -v
        w, h = self.ctx.size
        img = self.ctx.get_img('out', t)
        if self.cards is None:
            stills = (self.ctx.stills(3) if self.ctx.stills else []) or []
            spots = [(0.95, 0.12, 1), (0.05, 0.15, -1), (0.08, 0.9, 1)]
            self.cards = []
            for (x, y, sg), st in zip(spots, stills):
                cw = self.rnd(0.35, 0.45) * w
                chh = cw * 5 / 4
                sh, sw = st.shape[:2]
                if sw / sh > 4 / 5:
                    nw = int(sh * 4 / 5)
                    st = st[:, (sw - nw) // 2:(sw - nw) // 2 + nw]
                card = cv2.resize(st, (int(cw), int(chh)), interpolation=cv2.INTER_AREA)
                card = cv2.copyMakeBorder(card, 1, 1, 1, 1, cv2.BORDER_CONSTANT, value=(235, 235, 235))
                self.cards.append((card, x * w, y * h, sg * self.rnd(12, 30)))
        for i, (card, x, y, ang) in enumerate(self.cards):
            appear = self.pre - i * 0.5 * self.ctx.beat
            age = appear - u
            if age < 0 or u < 4 * F60:
                continue
            a = min(1.0, age / (3 * F60))
            s = 1.10 - 0.10 * e_out(age / (6 * F60))
            lay, m = fx.affine_layer(card, x, y, card.shape[1] * s, ang, (w, h))
            img = fx.shadow_under(img, m, self.ctx.pw(0.01), (self.ctx.pw(0.005), self.ctx.pw(0.005)), 0.35 * a)
            img = fx.blend_mask(img, lay, m * a)
        if u < 4 * F60:
            img = fx.gauss_px(img, self.ctx.pw(0.005) * (1 - u / (4 * F60)))
        return img


# ---------------------------------------------------------------- fx cuts (typography / AE showcase)
# Every number below is from core/edit/refs/fx.json (WGvtliE1298). The house join of this group is an
# EXPONENTIAL whip: the per-frame displacement grows geometrically for 8-14 frames and the cut lands on
# the extreme frame, so there is never a visible join between two sharp frames - and never a pure white
# or pure black frame either (the ramps peak at alpha 0.72-0.83).

_STREAK_CACHE = {}


def short_streaks(size, seed, d=1.0, n=16, drift=0.0, bloom=True, down=2):
    """
    12-20 SHORT horizontal light streaks (4-10 %W long, 1-2 px) clustered toward the exit side, plus a soft
    edge bloom of radius 12 %W on that side (WGvtliE1298 f131-134). Replaces the full-width bands of
    fx.light_streaks_h, which read as scanline artefacts. Float layer at 1/down size for fx.screen_layer.
    """
    w, h = max(2, size[0] // down), max(2, size[1] // down)
    key = (w, h, int(seed), int(n), bool(bloom))
    base = _STREAK_CACHE.get(key)
    if base is None:
        rng = np.random.default_rng(int(seed) + 17)
        lines = []
        for _ in range(int(n)):
            ln = float(rng.uniform(0.04, 0.10)) * w
            xc = (1.0 - abs(float(rng.normal(0.0, 0.28)))) * w          # clustered at the exit edge
            y = float(rng.uniform(0.18, 0.82)) * h
            lines.append((xc, y, ln, float(rng.uniform(0.35, 1.0)), int(rng.integers(1, 3))))
        bl = np.zeros((h, w), np.float32)
        if bloom:
            yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
            r = np.sqrt((xx - w * 1.02) ** 2 + (yy - h * 0.5) ** 2) / (0.12 * w)
            bl = np.clip(np.exp(-0.5 * r * r) * 1.6, 0, 1).astype(np.float32)
        base = (lines, bl)
        if len(_STREAK_CACHE) > 8:
            _STREAK_CACHE.clear()
        _STREAK_CACHE[key] = base
    lines, bl = base
    out = np.zeros((h, w), np.float32)
    for xc, y, ln, a, t in lines:
        x = xc - drift * w
        cv2.line(out, (int(x - ln / 2), int(y)), (int(x + ln / 2), int(y)), a, t, cv2.LINE_AA)
    out = cv2.GaussianBlur(out, (0, 0), max(0.8, 0.0012 * w)) + bl
    if d <= 0:
        out = out[:, ::-1]
    out = np.ascontiguousarray(np.clip(out, 0, 1))
    return np.repeat(out[..., None], 3, 2)


class _ExpWhip(T):
    """Base exponential whip: displacement x`growth` per frame @30 up to `peak` of the width on the last frame."""
    name = 'whip_exp_white'
    nf = 12                 # GROWTH frames @30 (the measured ramp is 12 growing + 2 unwinding = 14 total)
    growth = 1.30
    peak = 0.168            # width fraction travelled on the peak frame
    # The reference does NOT stop at the peak: it unwinds over the last 2 frames (d = 322, 209, 9 px @1920)
    # so the incoming lands nearly sharp. Without the tail our smear grows monotonically INTO the join and
    # the cut reads as a blur-to-white instead of a whip. Fractions of `peak`, measured f117-131.
    unwind = (0.649, 0.028)
    a_peak = 0.72           # overlay alpha at the PEAK frame (never 1.0)
    hold_alpha = None       # alpha held for `hold_frames` after the cut (soft whip: the incoming set is white)
    color = (255, 255, 255)
    aniso0, aniso1 = 0.69, 1.00
    hold_frames = 0         # frames the overlay holds after the cut (the black whip hides the join in 4)
    resolve_f = 8           # frames the incoming takes to come back
    resolve_gain = None     # (g0, frames): the black whip recovers by EXPOSURE, not by an alpha fade
    a_tau = 0.50            # alpha decay after the cut, in frames @30 (alpha <= 0.10 by k = 1)
    streaks = True          # horizontal light streaks over the incoming
    sat_ramp = (8.0, 88.0)  # incoming saturation ramp over 12 frames
    in_blur_f = 2.0         # incoming residual smear decay, frames @30 (0 = none, the black hold wants none)

    # how many of the `unwind` frames still belong to the OUTGOING shot. Read off the strips: the white
    # whip's 209 px frame (f130) is the brightest frame and still the old shot, its 9 px frame (f131) is
    # already the new one; the black whip's whole unwind (131, 74, 22, 40, 10) sits on the incoming,
    # inside the dark hold.
    out_unwind = 1
    # smear length as a multiple of the per-frame displacement. 0.5 is a 180 deg shutter, but on the
    # reference the features are dragged ~4x the flow displacement (f127: d = 108 px, streaks ~25 %W):
    # optical flow under-reads a smear. 2.0 reproduces the strip; 0.5 left the face readable to the join.
    blur_k = 2.0
    in_alpha0 = 0.10        # veil left on the first incoming frame ("cut ON the brightest frame, no hold")

    def setup(self):
        self.d = self.sign()
        grow = [self.peak * self.growth ** (k - (self.nf - 1)) for k in range(self.nf)]
        if self.step_px:                                 # the reference's own displacement series, px @1920
            grow = [float(v) / 1920.0 for v in self.step_px]
        tail = [self.peak * float(f) for f in (self.unwind or ())]
        no = max(0, min(len(tail), int(self.out_unwind)))
        self.step = np.array(grow + tail[:no], float)
        self.step_in = np.array(tail[no:], float)
        self.n_all = len(self.step)
        self.k_peak = self.nf - 1
        self.cum = np.cumsum(self.step)
        self.seed = int(self.ctx.rng.integers(0, 99999))
        self.pre = self.n_all * F30
        self.post = (self.hold_frames + self.resolve_f) * F30
        # The travel drags the mirrored BORDER_REFLECT101 edge into shot (a duplicated face on the join
        # frame) unless the plate is zoomed by at least 1 + 2 x the maximum shift.
        self.shift_max = float(self.cum[-1]) * self.travel
        if not self.wedge:
            self.zoom_ln = max(self.zoom_ln, 2.0 * self.shift_max * 1.06)
        if self.len_px:                                  # the reference's own smear-length series, px @1920
            self.blur_len = [float(v) / 1920.0 for v in self.len_px]
        else:
            self.blur_len = [self.blur_k * float(v) for v in self.step]

    # Round 3 (critic): the whip is a CAMERA whip. The picture really travels (0.85 x the measured
    # displacement = ~0.69 W against the reference's 0.71 W) and the colour enters as a WEDGE from the
    # leading edge, which also hides the mirrored plate border, instead of a uniform full-frame veil.
    travel = 0.85
    zoom_ln = 0.26
    wedge = True
    wedge_cov = (0.04, 0.08, 0.16, 0.26, 0.35, 0.70, 1.00)   # coverage over the LAST outgoing frames
    wedge_alpha = 0.94
    wedge_feather = 0.03    # of the width; grows with the smear
    veil_max = 0.25         # uniform veil ceiling (the wedge does the rest)
    grad_alpha = None       # black whip: a travelling darkness gradient with this per-frame alpha
    len_px = None
    step_px = None
    in_sigma = (0.005, 0.003, 0.0015)   # incoming defocus per frame @30, of the width (then 0: no pop)
    in_unwind_px = None     # black whip: horizontal unwind on the first recovery frames, px @1920
    gain_growth = 1.105
    bloom = True

    def _xn(self):
        w = self.ctx.size[0]
        xn = np.linspace(0.0, 1.0, w, dtype=np.float32)
        return xn if self.d > 0 else 1.0 - xn            # 1.0 = the leading edge (where the colour enters)

    def _oop(self, k):
        """Width fraction where the travelling plate has left the canvas (the mirrored border would show)."""
        margin = 0.5 * (math.exp(self._zoom(k)) - 1.0)
        return max(0.0, float(self.cum[k]) * self.travel - margin)

    # round 5: 0.40 kept the subject centred (the zoom cancelled the travel) and its 4 %/frame ramp was shutter-
    # smeared into a radial defocus on frame 1. The wedge / darkness gradient already covers the part of the canvas
    # the plate has left (coverage = cum x travel = 0.26, 0.34, 0.45, 0.59, 0.69 W on the last 5 frames - the
    # reference's own 25 / 35 / 45 / 75 / 100 %), so only a token zoom remains.
    zoom_max = 0.08

    def _zoom(self, k):
        """ln zoom that keeps the mirrored border out for as long as x`zoom_max` allows; the wedge covers the rest."""
        if not self.wedge:
            return self.zoom_ln * float(self.cum[k]) / max(1e-6, float(self.cum[-1]))
        return min(self.zoom_max, math.log(1.0 + 2.1 * float(self.cum[k]) * self.travel))

    def _wedge_out(self, img, k, L):
        w, h = self.ctx.size
        xn = self._xn()
        oop = self._oop(k)
        if self.grad_alpha:
            a = float(self.grad_alpha[min(k, len(self.grad_alpha) - 1)])
            slope = 0.8 * (1.0 - k / max(1.0, self.n_all - 1.0))
            m = a * (1.0 - (1.0 - xn) * slope)
            if oop > 0:                                  # fully dark where the plate has left the canvas
                # round 5: a WIDE smooth shoulder (30 %W). The 8 %W ramp read as a hard-edged dark slab on a bright plate.
                f = 0.30 + 0.5 * L / w
                e = np.clip((xn - (1.0 - oop - 0.05 - 0.5 * f)) / f, 0.0, 1.0)
                e = e * e * (3 - 2 * e)
                m = m + (0.97 - m) * e
        else:
            j = k - (self.n_all - len(self.wedge_cov))
            cov = self.wedge_cov[j] if j >= 0 else 0.0
            if oop > 0:
                cov = max(cov, min(1.0, oop + 0.05))
            if cov <= 0:
                return img
            f = self.wedge_feather + 0.5 * L / w
            e = 1.0 - cov - (f if cov >= 1.0 else 0.0)
            m = np.clip((xn - e) / max(1e-3, f) + 0.5, 0.0, 1.0)
            m = m * m * (3 - 2 * m) * self.wedge_alpha
        c = np.empty_like(img)
        c[:] = self.color
        return fx.blend_mask(img, c, np.repeat(m[None, :].astype(np.float32), h, 0))

    def _bloom_ok(self, img):
        # the edge bloom belongs on a dark or mid plate (the reference lands on a dark set); on a bright
        # picture it would only wash the frame
        if not hasattr(self, '_bl'):
            self._bl = float(img[::8, ::8].mean()) < 150.0
        return self._bl

    def _k(self, u):
        return int(min(self.n_all - 1, max(0, math.floor((self.pre - u) * 30.0 + 1e-6))))

    def _alpha_out(self, k):
        """Monotone ramp that reaches a_peak on the LAST outgoing frame: the cut lands on the brightest frame."""
        a = self.a_peak * ((k + 1) / float(self.n_all)) ** 2.6
        return min(self.veil_max, a) if self.wedge else a

    def cam_out(self, u):
        k = self._k(u)
        return (self._zoom(k), 0.0, -self.d * float(self.cum[k]) * self.travel, 0.0)

    def fx_out(self, img, u):
        k = self._k(u)
        an = self.aniso0 + (self.aniso1 - self.aniso0) * min(1.0, k / max(1, self.k_peak))
        L = float(self.blur_len[min(k, len(self.blur_len) - 1)]) * self.ctx.size[0]
        img = fx.aniso_blur(img, L, 0.0, an)
        img = fx.fill_mix(img, self.color, self._alpha_out(k))
        if self.wedge:
            img = self._wedge_out(img, k, L)
        return fx.cap_white(img)

    def fx_in(self, img, v):
        k = v * 30.0                                   # FLOAT frames @30: at 60 fps the veil must not step
        if k < self.hold_frames:
            a = self.a_peak if self.hold_alpha is None else self.hold_alpha
        else:
            a0 = self.in_alpha0 if self.hold_frames == 0 else (self.a_peak if self.hold_alpha is None else self.hold_alpha)
            a = a0 * math.exp(-(k - self.hold_frames) / max(0.05, self.a_tau))
        if self.resolve_gain:
            # The black whip holds the INCOMING plate at very low exposure (Laplacian var 19-35 - the
            # ground texture stays visible) and then brightens it, luma 24 -> 53 over 8 frames.
            g0, gf = self.resolve_gain
            if not hasattr(self, '_g0'):                        # hold the plate at luma ~24 whatever its own level
                m = max(12.0, float(img[::8, ::8].mean()))
                self._g0 = min(0.45, max(g0, 24.0 / m))
            j = max(0.0, k - self.hold_frames)
            if self.in_unwind_px and 0.0 <= k < len(self.in_unwind_px):
                q = k                                   # round 5: the unwind sits INSIDE the dark hold (f200-203), not after it
                seq = list(self.in_unwind_px) + [0.0]
                Lq = seq[int(q)] * (1 - (q - int(q))) + seq[int(q) + 1] * (q - int(q))
                img = fx.aniso_blur(img, Lq * self.ctx.size[0] / 1920.0, 0.0, 1.0)
            g = self._g0 * self.gain_growth ** j
            if self.gain_growth > 1.2 and j > self.resolve_f - 1.0:
                g = max(g, 1.0)
            img = fx.gain_u8(img, min(1.0, g))
            a = 0.0
        ki = int(k)
        if self.wedge and not self.resolve_gain:
            # incoming sharpness ramps over 4 frames (sigma 0.5, 0.3, 0.15, 0 %W, interpolated per output
            # frame) instead of 2 soft frames and a pop
            sg = list(self.in_sigma) + [0.0, 0.0]
            if ki < len(self.in_sigma) and k >= self.hold_frames * 0:
                f = k - ki
                img = fx.gauss_px(img, self.ctx.pw(sg[ki] * (1 - f) + sg[ki + 1] * f))
        elif ki < len(self.step_in) and not self.resolve_gain:   # the measured unwind carried by the incoming
            img = fx.aniso_blur(img, self.blur_k * float(self.step_in[ki]) * self.ctx.size[0], 0.0, 1.0)
        if self.sat_ramp:
            s0, s1 = self.sat_ramp
            img = fx.saturation_u8(img, s0 / s1 + (1 - s0 / s1) * min(1.0, k / 12.0))
        if self.streaks and self.wedge:
            if k < 5 and self._bloom_ok(img):
                g = max(0.0, 1 - k / 4.5)
                img = fx.screen_layer(img, short_streaks(self.ctx.size, self.seed, self.d, drift=k * 0.012), 0.8 * g)
        elif self.streaks and k < 6:
            g = 1 - k / 6.0
            img = fx.screen_layer(img, fx.light_streaks_h(self.ctx.size, 7, seed=self.seed, strength=g), 0.5 * g)
        return fx.cap_white(fx.fill_mix(img, self.color, a))


class WhipExpWhite(_ExpWhip):
    """f117-131: 12 growth frames x1.30 to 16.8 %W + a 2-frame unwind, alpha 0 -> 0.72, cut on the brightest frame."""
    name = 'whip_exp_white'


class WhipExpBlack(_ExpWhip):
    """f192-204: 8 growth frames x1.48 to 15.4 %W then a 5-frame unwind inside a 4-frame dark hold (luma 24 -> 53)."""
    name = 'whip_exp_black'
    nf, growth, peak = 8, 1.48, 0.154
    unwind = (0.443, 0.250, 0.075, 0.135, 0.034)
    a_peak, color = 0.71, (22, 22, 22)       # never a pure black frame: the reference bottoms at luma ~23
    # round 3: a purely horizontal smear (sigma_y <= 0.15 sigma_x) with the reference's own length series,
    # darkness as a gradient travelling in from the leading edge, 4 dark frames, then full exposure in 6
    # frames (x1.45 / frame) under a 3-frame horizontal unwind.
    aniso0, aniso1 = 0.88, 1.00
    len_px = (19, 43, 66, 88, 116, 143, 208, 295)
    step_px = (19, 43, 66, 88, 116, 143, 208, 295)
    grad_alpha = (0.34, 0.68, 0.82, 0.89, 0.91, 0.92, 0.94, 0.96)
    veil_max = 0.0
    in_unwind_px = (131, 74, 22)
    gain_growth = 1.45
    hold_frames = 4
    out_unwind = 0
    # the recovery is an EXPOSURE ramp, not an alpha fade: gain 0.13 -> 1.0 over 20 frames @30 is
    # +11 %/frame at the start and reaches 0.294 at frame 8, i.e. the measured luma 24 -> 53.
    resolve_f = 6
    resolve_gain = (0.13, 6.0)
    in_blur_f = 0.0
    streaks = False
    sat_ramp = None


class WhipExpSoft(_ExpWhip):
    """
    f322-333: the gentler third whip. Its defining property is the SHOT IT CUTS INTO - a set whose own
    background is white (luma 217, sat 4), so the white never resolves away: the reference measures
    245.7, 243.3, 242.7, 241.7 after the join. Cut into a dark plate it reads as a flash to black, the
    exact opposite, which is why planners gate it on the incoming shot (see presets.whip_soft_ok).
    """
    name = 'whip_exp_soft'
    nf, growth, peak = 10, 1.48, 0.150
    unwind = (0.60, 0.05)
    a_peak = 0.83
    hold_frames, hold_alpha = 4, 0.90
    a_tau = 1.6
    resolve_f = 6
    sat_ramp = (30.0, 88.0)

    def fx_in(self, img, v):
        # Render-time gate (the planners gate it too): the held veil only reads when the INCOMING picture is
        # itself a white set (mean luma >= 180). Into anything darker it would read as a long white-out, so
        # the whip then resolves like whip_exp_white: no hold, veil gone one frame after the cut.
        if not hasattr(self, '_soft_ok'):
            self._soft_ok = float(img[::8, ::8].mean()) >= 180.0
            if not self._soft_ok:
                self.hold_frames, self.hold_alpha, self.a_tau = 0, None, 0.5
        return super().fx_in(img, v)


def under_set(img, cam, size, bg='dark'):
    """
    A pull-back below scale 1.0 (cam ln s < 0) leaves the mirrored plate border around the shrunken picture.
    Paint a SET behind it instead, as the reference does (its receding shots are photos in a set):
    'white' = the luma-245 white set, 'dark' = a heavily blurred copy of the picture at 35 % exposure.
    """
    if cam is None or cam[0] >= -1e-4:
        return img
    w, h = size
    s = math.exp(cam[0])
    cx, cy = w * (0.5 + cam[2]), h * (0.5 + cam[3])
    x0, y0 = int(round(cx - s * w / 2)), int(round(cy - s * h / 2))
    x1, y1 = int(round(cx + s * w / 2)), int(round(cy + s * h / 2))
    x0c, y0c, x1c, y1c = max(0, x0), max(0, y0), min(w, x1), min(h, y1)
    if x1c - x0c < 8 or y1c - y0c < 8 or abs(cam[1]) > 0.5:
        return img
    if bg == 'self':
        # round 3: NO visible plate edge. Behind the receding picture sits the same picture at x1.3,
        # heavily defocused, and the join between the two is feathered over 2 %W.
        crop = img[y0c:y1c, x0c:x1c]
        small = cv2.resize(crop, (max(8, w // 8), max(8, h // 8)), interpolation=cv2.INTER_AREA)
        small = cv2.GaussianBlur(small, (0, 0), max(1.0, w / 8 * 0.03))
        big = cv2.resize(small, (int(w * 1.3), int(h * 1.3)), interpolation=cv2.INTER_LINEAR)
        bx, by = (big.shape[1] - w) // 2, (big.shape[0] - h) // 2
        back = fx.gain_u8(big[by:by + h, bx:bx + w], 0.85)
        m = np.zeros((h, w), np.float32)
        f = max(2, int(w * (0.02 + 0.10 * (1.0 - s))))
        if x1c - x0c > 4 * f and y1c - y0c > 4 * f:
            m[y0c + f:y1c - f, x0c + f:x1c - f] = 1.0
        m = cv2.blur(cv2.blur(m, (2 * f + 1, 2 * f + 1)), (f | 1, f | 1))
        return fx.blend_mask(back, img, m)
    if bg == 'edge':
        # round 4: the outgoing picture IS a white set already - continue its own edge colour outward, feathered,
        # with no border and no shadow, so no frame edge is ever seen (the reference's dolly out of the white studio)
        crop = img[y0c:y1c, x0c:x1c]
        e = max(2, int(0.02 * min(crop.shape[:2])))
        ring = np.concatenate([crop[:e].reshape(-1, 3), crop[-e:].reshape(-1, 3), crop[:, :e].reshape(-1, 3), crop[:, -e:].reshape(-1, 3)])
        back = np.full_like(img, np.median(ring, axis=0).astype(np.uint8))
        m = np.zeros((h, w), np.float32)
        f = max(2, int(w * 0.03))
        if x1c - x0c > 4 * f and y1c - y0c > 4 * f:
            m[y0c + f:y1c - f, x0c + f:x1c - f] = 1.0
        m = cv2.blur(m, (2 * f + 1, 2 * f + 1))
        return fx.blend_mask(back, img, m)
    if bg == 'card':
        # a polaroid-style card on the white set: the edge is legitimate (border 1.5 %W, shadow sigma 2 %W)
        back = np.full_like(img, 247)
        b = max(2, int(round(w * 0.015 * s)))
        m = np.zeros((h, w), np.float32)
        m[max(0, y0 - b):min(h, y1 + b), max(0, x0 - b):min(w, x1 + b)] = 1.0
        sh = cv2.GaussianBlur(m, (0, 0), max(1.0, w * 0.02))
        sh = np.roll(sh, (int(w * 0.006), int(w * 0.004)), (0, 1))
        back = (back.astype(np.float32) * (1 - 0.30 * sh[..., None])).astype(np.uint8)
        back[m > 0.5] = 252
        pm = np.zeros((h, w), np.float32)
        pm[y0c:y1c, x0c:x1c] = 1.0
        return fx.blend_mask(back, img, cv2.GaussianBlur(pm, (0, 0), 0.8))
    if bg == 'white':
        back = np.full_like(img, 245)
    else:
        small = cv2.resize(img[y0c:y1c, x0c:x1c], (max(8, w // 8), max(8, h // 8)), interpolation=cv2.INTER_AREA)
        small = cv2.GaussianBlur(small, (0, 0), max(1.0, w / 8 * 0.03))
        back = fx.gain_u8(cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR), 0.35)
    m = np.zeros((h, w), np.float32)
    m[y0c:y1c, x0c:x1c] = 1.0
    sh = cv2.GaussianBlur(m, (0, 0), max(1.0, w * 0.012))
    back = (back.astype(np.float32) * (1 - 0.35 * sh[..., None])).astype(np.uint8)
    m = cv2.GaussianBlur(m, (0, 0), 1.2)
    return fx.blend_mask(back, img, m)


class RackFocusIn(T):
    """
    f88-104 (17 frames): the incoming resolves out of a full defocus, sigma >= 1.7 %W saturated for 2 frames,
    then a 6-frame plateau and an exponential decay with tau 5 frames @30 to 0.14 %W. Exposure stays FLAT
    (luma 143 -> 153): unlike dance defocus_in there is NO gain pump.
    """
    name = 'rack_focus'
    post = 12 * F30
    # The reference does not hold the outgoing still: the photo it racks off shrinks to ~35 % of its size
    # over 8 frames while its luma falls 75.5 -> 31.4. `cam_out` cannot simply return a negative ln s -
    # engine.shot_cam clamps any negative zoom to 0 when the shot's base zoom is >= 1.0, because a shot
    # smaller than the canvas would expose the plate edge. So the pull-back is expressed as a PRE-ZOOM
    # that ramps DOWN to 1.00 at the cut, which is the same motion cue and never goes under the frame.
    # round 3 (critic): the object is NOT shrunk to a small plate. It keeps (almost) its size, recedes
    # 3 %/frame for 6 frames (never below x0.85), defocuses 0 -> 0.45 %W and dims to 0.45, with the
    # blurred picture itself behind it so that no plate edge exists.
    pre = 6 * F30
    out_rate = 0.027        # ln s per frame @30: 6 frames -> x0.85

    under_ok = True         # engine: do not clamp the pull-back, fx_out paints the set behind the picture
    no_mblur = True         # engine: no shutter smear over the settle (the defocus is the effect)

    def cam_out(self, u):
        return (-self.out_rate * (self.pre - u) * 30.0, 0, 0, 0)

    def fx_out(self, img, u):
        k = 1 - u / self.pre
        img = under_set(img, getattr(self, 'under_cam', None), self.ctx.size, 'self')
        return fx.gain_u8(fx.gauss_px(img, self.ctx.pw(0.0045) * k), 1 - 0.55 * k)

    in_sigma = (0.32, 0.34, 0.38, 0.40, 0.40, 0.38, 0.31, 0.23, 0.19, 0.16)   # %W per frame @30, then 0
    in_scale0, in_tau_f = 2.0, 3.0

    def cam_in(self, v):
        return (math.log(self.in_scale0) * math.exp(-v * 30.0 / self.in_tau_f), 0, 0, 0)

    # fx.json sigma_pct_w_by_frame = [sat, .32, .34, .38, .40, .40, .38, .31, .23, .19, .16, .15, .15, .15, .14, .14]:
    # ONE saturated frame, a 6-frame plateau at 0.32-0.40 %W, then an exponential decay to 0.14 %W. Fitting the
    # tail gives tau 1.9 frames @30, not the 5 frames the prose recipe suggests - the array is what we follow.
    # plateau trimmed from the measured 0.0040 to 0.0034 because the estimator also sees the source plate's
    # own softness: at 0.0040 our render measured 0.41-0.48 %W against the reference's 0.32-0.40.
    sat, plateau, floor, tau_f = 0.017, 0.0034, 0.0012, 1.9

    def fx_in(self, img, v):
        f = v * 30.0
        if self.in_sigma:
            seq = [x * 0.01 * 0.85 for x in self.in_sigma] + [0.0, 0.0]   # x0.85: the estimator also sees the plate's own softness
            i = int(f)
            if i >= len(self.in_sigma):
                return img
            return fx.gauss_px(img, self.ctx.pw(seq[i] * (1 - (f - i)) + seq[i + 1] * (f - i)))
        if f < 1.0:
            sig = self.sat
        else:
            ramp = min(1.0, 0.8 + 0.2 * (f - 1) / 3.0)          # 0.32 -> 0.40 %W over the first 4 frames
            sig = self.floor + (self.plateau * ramp - self.floor) * math.exp(-max(0.0, f - 6.0) / self.tau_f)
        return fx.gauss_px(img, self.ctx.pw(sig))


class SpinSettle(T):
    """
    f292-306 (15 frames): the incoming lands rolled 57 deg and at x1.68 and unwinds together, both with
    tau 4.7 frames @30 (0.156 s), rotationally blurred at 1.5x the per-frame roll rate. This is the full
    version of spin_blur, which only rolls 12-20 deg and has no coupled scale settle.
    """
    name = 'spin_settle'
    # 18 frames, not the reference window's 15: at tau 4.67 frames 15 frames still leave 2.3 deg of
    # cumulative roll on the table, so the shot never lands inside the transition. 18 leaves 1.2 deg,
    # inside the reference's own tail (1.4, 1.3, 1.1 deg/frame).
    post = 18 * F30
    tau = 0.156
    roll_total = 57.0
    scale0 = 1.68
    # round 3 (critic): the OUTGOING already rolls (0, 1.5, 4, 8, 14, 22 deg cumulative over its last 6
    # frames, rotational blur 0.5 x the step); the incoming blur is 0.5 x the per-frame arc and nothing
    # else (no shutter smear on top, no gaussian), gone by frame 8; the zoom follows the measured series.
    pre = 6 * F30
    pre_roll = (0.0, 1.5, 4.0, 8.0, 14.0, 22.0)
    zoom_pct = (13.9, 12.5, 7.5, 6.0, 4.7, 4.6, 3.7, 3.2, 2.5, 1.9, 1.5, 1.2, 0.7)
    no_mblur = True

    def setup(self):
        self.d = self.sign()
        ln = [math.log(1 + z / 100.0) for z in self.zoom_pct]
        self.z_left = [sum(ln[i:]) for i in range(len(ln))] + [0.0, 0.0]      # ln scale still to unwind at frame i

    def _roll(self, v):
        return -self.d * self.roll_total * math.exp(-max(0.0, v) / self.tau)

    def _pre(self, u):
        f = min(len(self.pre_roll) - 1.0, max(0.0, (self.pre - u) * 30.0))
        i = int(f)
        j = min(len(self.pre_roll) - 1, i + 1)
        return self.pre_roll[i] * (1 - (f - i)) + self.pre_roll[j] * (f - i)

    def cam_out(self, u):
        r = self._pre(u)
        # zoom just enough to keep most of the mirrored corners out while it rolls
        return (math.log(1.0 + 0.012 * r), self.d * r, 0, 0)

    def fx_out(self, img, u):
        step = self._pre(u) - self._pre(u + F30)
        return fx.rot_blur(img, 0.5 * step * self.d)

    def cam_in(self, v):
        f = max(0.0, v * 30.0)
        i = int(f)
        if i >= len(self.z_left) - 1:
            z = 0.0
        else:
            z = self.z_left[i] * (1 - (f - i)) + self.z_left[i + 1] * (f - i)
        return (z, self._roll(v), 0, 0)

    def fx_in(self, img, v):
        arc30 = self._roll(v) - self._roll(v + F30)              # the per-frame (@30) arc
        k = min(1.0, max(0.0, (8.0 - v * 30.0) / 3.0))            # sharp by frame 8 after the join
        return fx.rot_blur(img, 0.5 * arc30 * k)


class ZoomSettle(T):
    """
    The biggest grade jump in the reference (f414-424) hidden in a settle: the outgoing pushes AWAY
    (-3.1 -> -14.1 %/frame, growth x1.24) while its luma falls to 36 %, and the incoming lands at x1.73
    with a 10 px defocus @1920 and settles with tau 5.5 frames @30.
    """
    name = 'zoom_settle'
    pre = 8 * F30
    post = 12 * F30
    tau_in = 0.183
    # The recede is a GEOMETRIC ramp: the measured per-frame rates -3.1, -4.2, -5.1, -5.8, -6.7, -6.9,
    # -9.4, -14.1 grow x1.24 and sum to ln s = -0.644 over the 8 frames. Expressed as a pre-zoom of
    # +0.644 ramping down to 1.00 at the cut, because engine.shot_cam clamps a negative ln s to 0 (a
    # shot smaller than the canvas would expose the plate edge) - which is exactly why the measured
    # render froze at 0.0 %/frame for the last 4 frames before the cut.
    out_growth, out_ln = 1.24, 0.644

    def setup(self):
        n = max(1, int(round(self.pre * 30.0)))
        step = np.array([self.out_growth ** j for j in range(n)], float)
        step *= self.out_ln / max(1e-9, float(step.sum()))
        self.out_cum = np.concatenate([[0.0], np.cumsum(step)])

    def _ok(self, u):
        k = int(min(len(self.out_cum) - 1, max(0, round((self.pre - u) * 30.0))))
        return k, 1 - u / self.pre

    under_ok = True

    def cam_out(self, u):
        k, _ = self._ok(u)
        return (-float(self.out_cum[k]), 0, 0, 0)

    def fx_out(self, img, u):
        _, k = self._ok(u)
        img = under_set(img, getattr(self, 'under_cam', None), self.ctx.size, 'self')
        return fx.gain_u8(fx.gauss_px(img, self.ctx.pw(0.005) * k), 1 - 0.64 * k ** 1.6)

    no_mblur = True             # the join is a heavy DEFOCUS at x1.73, not a radial streak burst

    def cam_in(self, v):
        return (math.log(1.73) * math.exp(-v / self.tau_in), 0, 0, 0)

    def fx_in(self, img, v):
        f = v * 30.0                                           # 10 px @1920, tau 4 frames, exactly 0 at frame 9
        return fx.gauss_px(img, self.ctx.pw(0.0052) * math.exp(-f / 4.0) * min(1.0, max(0.0, (9.0 - f) / 3.0)))


class GlassShatter(T):
    """
    f182-192 (11 frames): a single ~20-arm white crack star at the impact point, then the plane breaks into
    Voronoi shards. Measured mean gradient x2.9, small-blob count x11, luma -43 %, warm R-B -11 -> +10,
    with a constant +2..+4 %/frame push throughout.
    """
    name = 'glass_shatter'
    # round 4 timeline (the critic's frame reading of f180-192): 3 flare frames with NO cracks, 3 frames of
    # the crack star alone, then 7 frames of shards flying out in depth over the dark warm set.
    flare_f = 3                 # pre-break flare: gain 1.00 -> 1.48
    star_f = 3
    break_f = 7
    pre = 13 * F30
    post = 3 * F30

    def setup(self):
        self.seed = int(self.ctx.rng.integers(0, 9999))
        self.cx, self.cy = float(self.ctx.focus[0]), float(self.ctx.focus[1])

    def _f(self, u):
        return (self.pre - u) * 30.0 - self.flare_f          # float frames since the break started (< 0 = flare)

    def cam_out(self, u):
        return (0.03 * max(0.0, (self.pre - u) * 30.0), 0, 0, 0)      # +3 %/frame push throughout

    def fx_out(self, img, u):
        f = self._f(u)
        w, h = self.ctx.size
        if f < 0:
            g = 1.0 + 0.48 * (1 + f / self.flare_f)
            return fx.cap_white(fx.gain_u8(fx.gauss_px(img, self.ctx.pw(0.004) * (1 + f / self.flare_f)), g))
        if not self.kw.get('legacy'):
            under = fx.nebula_set(self.ctx.size, self.seed, self.cx, self.cy)
            img = fx.gain_u8(fx.gauss_px(img, self.ctx.pw(0.004) * max(0.0, 1 - f / 4.0)), 1.0 + 0.48 * math.exp(-f / 1.5))
            fs = f - self.star_f
            if fs > 0:
                img = fx.shatter_shards3d(img, under, fs, self.break_f, self.cx, self.cy, self.seed)
                fade = max(0.0, 1.0 - fs / 3.0)          # the star is swallowed by the opening gaps
            else:
                fade = 1.0
            if fade > 0:
                u3 = min(1.0, (f + 1.0) / self.star_f)
                star = fx.crack_wedges(img, under, self.cx * w, self.cy * h, w * (0.2 + 0.4 * u3),
                                       max(2.0, (4.0 + 10.0 * u3) * w / 1920.0), 18, self.seed)
                img = star if fade >= 1.0 else cv2.addWeighted(star, fade, img, 1 - fade, 0)
            return img
        k = min(1.0, (f + 1.0) / self.break_f)
        # the flare decays into the break and the picture stays slightly defocused behind the sharp shard edges
        img = fx.gain_u8(fx.gauss_px(img, self.ctx.pw(0.004) * (1 - 0.6 * k)), 1.0 + 0.48 * math.exp(-f / 2.0))
        img = fx.shatter_shards(img, k ** 1.3, self.cx, self.cy, self.seed)
        if f < 4:
            r = w * 0.6 * min(1.0, (f + 1) / 3.0)
            star = fx.crack_star(self.ctx.size, self.cx * w, self.cy * h, r, 20, self.seed, thick=1, glow=3.0)
            img = fx.screen_layer(img, star, 1.0 if f < 3 else 0.5)
        return img

    def fx_in(self, img, v):
        return fx.gauss_px(img, self.ctx.pw(0.004) * max(0.0, 1 - v / self.post))


class DollyOutWhite(T):
    """f400-413: the exit is a SCALE ramp, not a pan - the subject shrinks away and the white set is all that is left."""
    name = 'dolly_out_white'
    pre = 14 * F30
    post = 3 * F30              # round 3: veil 0.5, 0.25, 0.1 over 3 frames (a 1-frame veil flickered)
    # Same clamp as zoom_settle, and here it killed the effect outright (exactly 0.0 %/frame for the
    # last 6 frames, so it read as a plain fade to white). Geometric shrink x1.10 over 14 frames,
    # total ln s 0.46, last frame -5.6 % - the measured peak.
    # round 3: -2.9 -> -5.6 %/frame geometric to the LAST frame (growth 1.052, total ln s 0.577), the plate
    # is a bordered CARD on the white set (so its edge is legitimate), it defocuses 0 -> 0.5 %W while it
    # recedes, and the white veil only arrives over the last 5 frames.
    out_growth, out_ln = 1.052, 0.577

    def setup(self):
        n = max(1, int(round(self.pre * 30.0)))
        step = np.array([self.out_growth ** j for j in range(n)], float)
        step *= self.out_ln / max(1e-9, float(step.sum()))
        self.out_cum = np.concatenate([[0.0], np.cumsum(step)])

    def cam_out(self, u):
        f = min(len(self.out_cum) - 1.0, max(0.0, (self.pre - u) * 30.0))      # continuous: no stall, no step
        i = int(f)
        j = min(len(self.out_cum) - 1, i + 1)
        return (-float(self.out_cum[i] * (1 - (f - i)) + self.out_cum[j] * (f - i)), 0, 0, 0)

    under_ok = True

    def fx_out(self, img, u):
        k = 1 - u / self.pre
        if not hasattr(self, '_mode'):
            ring = np.concatenate([img[:12:4, ::8].reshape(-1, 3), img[-12::4, ::8].reshape(-1, 3)])
            self._mode = 'edge' if float(ring.mean()) >= 225.0 else 'card'      # already on a white set: no card edge
        img = under_set(img, getattr(self, 'under_cam', None), self.ctx.size, self._mode)
        a = 0.80 * max(0.0, (k - 0.64) / 0.36) ** 1.5
        return fx.cap_white(fx.fill_mix(fx.gauss_px(img, self.ctx.pw(0.005) * k), (255, 255, 255), a))

    def fx_in(self, img, v):
        f = v * 30.0
        seq = (0.5, 0.25, 0.1, 0.0, 0.0)
        i = min(3, int(f))
        return fx.fill_mix(img, (255, 255, 255), seq[i] * (1 - (f - i)) + seq[i + 1] * (f - i))


class IrisHeart(T):
    """
    rfsj 26.4-26.7 s: the way INTO the heart windows. A vignette iris closes on the outgoing shot over 7 frames
    (radius 0.9 -> 0.35 W, on black) and morphs into the heart mask on the last 2, so the join lands on the
    hero heart of layouts.Hearts with no veil and no flash. Incoming: untouched.
    """
    name = 'iris_heart'
    pre = 7 * F30
    post = 0.0

    def fx_out(self, img, u):
        w, h = self.ctx.size
        f = (self.pre - u) * 30.0
        k = min(1.0, max(0.0, f / 7.0))
        hero = 0.34 * w if w >= h else 0.66 * w
        r0 = 0.52 * math.hypot(w, h) / w                  # round 5: start just outside the corners, so the iris is seen closing from frame 1
        r = (r0 - (r0 - hero * 0.52 / w) * k ** 1.15) * w
        circ = fx.iris_mask((w, h), r, feather=max(6.0, 0.05 * w * (1 - k) + 3))
        morph = min(1.0, max(0.0, (f - 5.0) / 2.0))
        if morph > 0:
            heart = fx.poly_mask((w, h), fx.heart_poly(120), w / 2, h / 2, hero * 0.52, blur=3)
            circ = circ * (1 - morph) + heart * morph
        return (img.astype(np.float32) * circ[..., None]).astype(np.uint8)


# ---------------------------------------------------------------- 3D camera transitions (core/edit/camera3d.py)

def _c3():
    import camera3d as C3
    return C3


def _geo(k, g):
    """Geometric ramp 0..1 over k in [0, 1] with per-frame growth g (the references' whips: x1.28-1.50 / frame)."""
    n = 6.0
    return (g ** (k * n) - 1) / (g ** n - 1)


class _Card3D(T):
    """
    Base of the plane transitions: both pictures are cards in space seen by the 40 deg camera. The outgoing turns
    away with a geometric acceleration (x g per frame, the references' whip law), the switch happens edge-on, the
    incoming turns in from the other side and settles exponentially (tau). Perspective motion blur: each card is
    re-projected at 5 shutter samples while it turns fast. Behind the cards: the outgoing picture, dark and defocused.
    """
    pre = 5 * F30
    post = 8 * F30
    composite_pre = True
    composite_post = True
    axis = 'yaw'
    turn = 90.0
    g = 1.40
    tau = 0.055
    pz_k = 0.0          # axis depth behind the card, in half canvas widths (cube: 1)
    recede = 0.18       # the card backs away by this much ln distance at the edge-on moment

    def setup(self):
        self.sg = float(self.kw['dir']) if self.kw.get('dir') is not None else self.sign()
        self.bg = None

    def _angle_out(self, u):
        k = 1 - u / self.pre                      # 0 at the start of the ramp, 1 at the switch
        return self.turn * _geo(max(0.0, k), self.g)

    def _settle(self, v):
        """exp(-v/tau) re-scaled to reach exactly 0 at the end of the transition (no pop when it hands back)."""
        e0 = math.exp(-self.post / self.tau)
        return max(0.0, (math.exp(-max(0.0, v) / self.tau) - e0) / (1 - e0))

    def _angle_in(self, v):
        return -self.turn * self._settle(v)

    def _pz(self):
        C3 = _c3()
        cw = self.ctx.size[0]
        return self.pz_k * 0.5 * cw / C3.focal(cw)

    def _params(self, ang, t_rel):
        a = abs(ang) / self.turn
        d = -self.recede * math.sin(math.pi * min(1.0, a))
        prm = dict(dolly=d)
        prm['yaw' if self.axis == 'yaw' else 'pitch'] = self.sg * ang
        return prm

    def _backdrop(self, t):
        img = self.ctx.get_img('out', t)
        return fx.gain_u8(fx.gauss_px(img, self.ctx.pw(0.03)), 0.28)

    def _draw(self, role, ang_fn, x, t):
        C3 = _c3()
        w, h = self.ctx.size
        img = self.ctx.get_img(role, t)
        piv = (w / 2.0, h / 2.0)
        dt = 1.0 / self.ctx.fps
        a0, a1 = ang_fn(x - 0.49 * dt), ang_fn(x + 0.49 * dt)
        n = int(min(9, max(5, abs(a1 - a0) / 1.5))) if abs(a1 - a0) > 1.5 else 1   # perspective blur samples
        samples = [self._params(ang_fn(x + (q / (n - 1) - 0.5) * 0.98 * dt), t) for q in range(n)] if n > 1 else None
        prm = self._params(ang_fn(x), t)
        return C3.card(img, (w, h), piv, pz=self._pz(), samples=samples, **prm)

    def composite(self, v, t):
        C3 = _c3()
        bg = self._backdrop(t)
        if v < 0:
            pic, m, _ = self._draw('out', lambda x: self._angle_out(-x), v, t)
            return C3.over(bg, pic, m)
        pic, m, _ = self._draw('in', lambda x: self._angle_in(x), v, t)
        a = min(1.0, v / (3 * F30))
        bg = fx.mix_u8(bg, self.ctx.get_img('in', t), a * 0.6) if a > 0 else bg
        return C3.over(bg, pic, m)


class SwingTurn(_Card3D):
    """Card turning about the vertical axis (Y swing): out 0 -> 90 deg accelerating x1.4 / frame, in -90 -> 0 (tau 55 ms)."""
    name = 'swing_turn'
    axis = 'yaw'


class FlipTurn(_Card3D):
    """Card flipping about the horizontal axis (X swing / tilt), the same laws as swing_turn."""
    name = 'flip_turn'
    axis = 'pitch'


class CubeTurn(_Card3D):
    """
    The two shots on two faces of a cube turning about its vertical axis: both faces are visible during the turn,
    the camera backs off at 45 deg so the cube fits, the incoming face settles front-on.
    """
    name = 'cube_turn'
    axis = 'yaw'
    pz_k = 1.0
    recede = 0.25
    pre = 6 * F30
    post = 6 * F30

    def _cube(self, ang, t, samples_fn=None):
        C3 = _c3()
        w, h = self.ctx.size
        piv = (w / 2.0, h / 2.0)
        pz = self._pz()
        d = -self.recede * math.sin(math.radians(2 * min(90.0, abs(ang))))
        out_img, in_img = self.ctx.get_img('out', t), self.ctx.get_img('in', t)
        bg = self._backdrop(t)
        faces = []
        for img, a in ((out_img, self.sg * ang), (in_img, self.sg * (ang - 90.0))):
            pic, m, H = C3.card(img, (w, h), piv, yaw=a, dolly=d, pz=pz)
            faces.append((abs(a), pic, m))
        for _, pic, m in sorted(faces, key=lambda f: -f[0]):       # the more turned face is further back
            bg = C3.over(bg, pic, m)
        return bg

    def composite(self, v, t):
        if v < 0:
            k = 1 - (-v) / self.pre
            ang = 45.0 * _geo(max(0.0, k), self.g)
        else:
            ang = 90.0 - 45.0 * self._settle(v)
        return self._cube(ang, t)


class OrbitThrough(T):
    """
    The camera orbits the subject through the cut: the outgoing picture (as depth layers - parallax) swings with a
    geometric yaw ramp and a small dolly-in, the incoming continues the SAME turn from the other side and settles
    (tau 70 ms); a 2-frame 0.3 %W defocus hides the switch. Uses the engine's 3D camera (engine.place -> camera3d).
    """
    name = 'orbit_through'
    pre = 5 * F30
    post = 0.35

    def setup(self):
        self.sg = self.sign()
        self.deg = self.rnd(10.0, 16.0)
        self.tau = float(self.kw.get('tau', 0.07))

    def cam_out(self, u):
        k = 1 - u / self.pre
        a = self.deg * _geo(max(0.0, k), 1.4)
        return (0.0, 0.0, 0.0, 0.0, self.sg * a, 0.0, 0.06 * a / self.deg, 0.0, 0.0)

    def cam_in(self, v):
        a = self.deg * math.exp(-v / self.tau)
        return (0.0, 0.0, 0.0, 0.0, -self.sg * a, 0.0, 0.06 * a / self.deg, 0.0, 0.0)

    def fx_out(self, img, u):
        return fx.gauss_px(img, self.ctx.pw(0.003)) if u < 2 * F60 else img

    def fx_in(self, img, v):
        return fx.gauss_px(img, self.ctx.pw(0.003) * (1 - v / (3 * F60))) if v < 3 * F60 else img


class ZoomThroughDepth(T):
    """
    Zoom-through with perspective: the outgoing camera DOLLIES into the picture (depth layers: the near layer rushes
    past faster than the background) with an accelerating law and a rising radial smear; the incoming keeps pushing
    forward from its own wide framing and settles (tau 90 ms). Exposure lifts +12 % at the switch.
    """
    name = 'zoom_through_depth'
    pre = 6 * F30
    post = 0.4

    def setup(self):
        self.A = self.rnd(0.35, 0.5)
        self.B = self.rnd(0.12, 0.2)
        self.tau = float(self.kw.get('tau', 0.09))

    def cam_out(self, u):
        k = 1 - u / self.pre
        return (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, self.A * _geo(max(0.0, k), 1.35), 0.0, 0.0)

    def cam_in(self, v):
        return (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, self.B * (1 - math.exp(-v / self.tau)), 0.0, 0.0)

    def fx_out(self, img, u):
        k = 1 - u / self.pre
        img = fx.zoom_blur_c(img, 0.22 * k ** 2)
        return fx.gain_u8(img, 1 + 0.12 * k ** 2)

    def fx_in(self, img, v):
        if v < 0.1:
            img = fx.zoom_blur_c(img, 0.16 * (1 - v / 0.1))
        return fx.gain_u8(img, 1 + 0.12 * math.exp(-v / 0.08))


# ---------------------------------------------------------------- modifiers

class Modified(T):
    """A transition with modifiers: +bleach (exposure bleach), +flash (white flash on the cut), +rgb, +leak."""

    def __init__(self, inner, mods, ctx):
        self.inner, self.mods = inner, mods
        self.ctx = ctx
        self.name = inner.name + ''.join('+' + m for m in mods)
        self.pre, self.post = inner.pre, inner.post
        if 'bleach' in mods:
            self.pre = max(self.pre, 11 * F60)
            self.post = max(self.post, 14 * F60)
            self.G, self.D = float(ctx.rng.uniform(0.2, 0.35)), float(ctx.rng.uniform(0.3, 0.5))
            self.Gi, self.S0 = float(ctx.rng.uniform(0.13, 0.39)), float(ctx.rng.uniform(0.3, 0.73))
        if 'flash' in mods or 'leak' in mods:
            self.post = max(self.post, 0.4)
        self.composite_pre, self.composite_post = inner.composite_pre, inner.composite_post
        self.hold = inner.hold

    def cam_out(self, u):
        return self.inner.cam_out(u) if u <= self.inner.pre else ZERO

    def cam_in(self, v):
        return self.inner.cam_in(v) if (v < self.inner.post or self.inner.hold) else ZERO

    def fx_out(self, img, u):
        if u <= self.inner.pre:
            img = self.inner.fx_out(img, u)
        if 'bleach' in self.mods and u <= 11 * F60:
            k = (11 * F60 - u) / (11 * F60)
            img = fx.gain_u8(fx.saturation_u8(img, 1 - self.D * k * k), 1 + self.G * k * k)
        return img

    def fx_in(self, img, v):
        if v < self.inner.post:
            img = self.inner.fx_in(img, v)
        if 'bleach' in self.mods and v < 14 * F60:
            j = v / (14 * F60)
            img = fx.gain_u8(fx.saturation_u8(img, self.S0 + (1 - self.S0) * j ** 0.8), 1 + self.Gi * (1 - j) ** 2)
        if 'flash' in self.mods:
            if v < F30:
                img = fx.fill_mix(img, self.ctx.flash_color, 0.9)
            elif v < 0.3:
                img = fx.gain_u8(img, 1 + 0.5 * ex(v - F30, 0.08) * bright_att(img))
        if 'rgb' in self.mods and v < 0.1:
            img = fx.rgb_split(img, self.ctx.px(6) * (1 - v / 0.1))
        if 'leak' in self.mods and v < 0.45:
            img = fx.screen_layer(img, fx.light_leak_layer(self.ctx.size, v, seed=0.5), 0.55 * (1 - v / 0.45))
        return img

    def composite(self, v, t):
        img = self.inner.composite(v, t)
        if img is None:
            return None
        if v < 0:
            return self.fx_out_mod(img, -v)
        return self.fx_in_mod(img, v)

    def fx_out_mod(self, img, u):
        if 'bleach' in self.mods and u <= 11 * F60:
            k = (11 * F60 - u) / (11 * F60)
            img = fx.gain_u8(fx.saturation_u8(img, 1 - self.D * k * k), 1 + self.G * k * k)
        return img

    def fx_in_mod(self, img, v):
        if 'bleach' in self.mods and v < 14 * F60:
            j = v / (14 * F60)
            img = fx.gain_u8(fx.saturation_u8(img, self.S0 + (1 - self.S0) * j ** 0.8), 1 + self.Gi * (1 - j) ** 2)
        return img


REGISTRY = {c.name: c for c in [
    Hard, HardSettle, BlurBridge, BlurBridgeSoft, BlurBridgeFlash, BlurBridgeHype, FlashWhite, ExposureBumpHard, ZoomThrough,
    PunchSettle, RadialZoomBurst, WhipSlide, CrossDissolve, DissolveBlur, InvertStrobe, DipBlackStrobe, RGBSplitGlitch,
    HalftoneFlash, DarkenSlidePreview, WhiteBarWipe, ShapeWipe, ChevronWipe, IrisWipe, WaveWipe, BlobWipe, CardTiltIn,
    PhotoCollage, FirstShotEntry, FadeIn, BarOpenReveal, VignetteFadeIn, Breath, DefocusIn, ZoomBlurIn, FlashIn, WhipDiag,
    PatternFlash, DarkBlurIn, LensZoomThrough, FlashZoom, MBCut, ZoomThroughColorField, WhipHorizontal, WhipVertical,
    ImpactZoom1Frame, SpinBlur, MirrorTileScaleIn, TintFlash, WhiteOut, DefocusDissolve, DipBlack, WhiteFlashCut, GlowCut,
    BlurInPunch, ShakeBlur, BlurRotate, BlurZoom, ZoomBlurFlash, HardPunch, VSlideWrap, CutoutPrereveal, PeekWindow,
    SlicesReveal, PhotoClones,
    WhipExpWhite, WhipExpBlack, WhipExpSoft, RackFocusIn, SpinSettle, ZoomSettle, GlassShatter, DollyOutWhite,
    IrisHeart, SwingTurn, FlipTurn, CubeTurn, OrbitThrough, ZoomThroughDepth]}
ALIASES = {'cut': 'hard', 'none': 'hard', 'flash': 'flash_white', 'flashzoom': 'flash_zoom', 'blur': 'blur_bridge',
           'zoomthrough': 'zoom_through', 'zoomblur': 'radial_zoom_burst', 'spin': 'spin_blur', 'whip': 'whip_slide',
           'glitch': 'rgb_split_glitch', 'dissolve': 'cross_dissolve', 'fade': 'fade_in', 'drop': 'blur_bridge_flash',
           'dip': 'dip_black', 'strobe': 'invert_strobe', 'white_flash': 'white_flash_cut', 'heart_wipe': 'shape_wipe',
           'card_3d_tilt': 'card_tilt_in', 'impact_zoom': 'impact_zoom_1frame', 'mirror_tile': 'mirror_tile_scale_in',
           'color_field': 'zoom_through_color_field', 'exposure_bump': 'exposure_bump_hard',
           # split_screen and card_3d_tilt are SHOT LAYOUTS (layouts.Split / layouts.Card3D), not cuts: the planner
           # emits shot["layout"]="split2"/"card3d" and a real cut. These aliases only catch hand-written plans, and
           # they now resolve to the closest visible cut instead of silently becoming a plain hard cut.
           'split_screen': 'white_bar_wipe',
           # fx group (core/edit/refs/fx.json)
           'whip_exp': 'whip_exp_white', 'whip_white': 'whip_exp_white', 'whip_black': 'whip_exp_black',
           'rack': 'rack_focus', 'spin_full': 'spin_settle', 'shatter': 'glass_shatter', 'dolly_white': 'dolly_out_white',
           'vslide': 'vslide_wrap', 'cutout': 'cutout_prereveal', 'slices': 'slices_reveal', 'clones': 'photo_clones',
           'peek': 'peek_window', 'zoom_blur': 'zoom_blur_in', 'lens': 'lens_zoomthrough', 'defocus': 'defocus_in',
           'hard_cut': 'hard', 'white': 'white_out',
           # 3D camera (camera3d.py)
           'swing': 'swing_turn', 'swing_y': 'swing_turn', 'flip': 'flip_turn', 'swing_x': 'flip_turn',
           'cube': 'cube_turn', 'orbit': 'orbit_through', 'zoom_depth': 'zoom_through_depth'}


def make(name, ctx, **kw):
    """Instantiate a transition by name ('<base>[+modifier...]'); unknown names fall back to a hard cut."""
    parts = (name or 'hard').split('+')
    base = ALIASES.get(parts[0], parts[0])
    if base == 'shape_wipe' and ctx.family == 'character' and not kw.get('shape'):
        base = ['chevron_wipe', 'iris_wipe', 'wave_wipe', 'blob_wipe'][int(ctx.rng.integers(0, 4))]
    cls = REGISTRY.get(base, Hard)
    t = cls(ctx, **kw)
    mods = [m for m in parts[1:] if m in ('bleach', 'flash', 'rgb', 'leak')]
    return Modified(t, mods, ctx) if mods else t


def known(name):
    base = (name or '').split('+')[0]
    return ALIASES.get(base, base) in REGISTRY


# ---------------------------------------------------------------- accents (inside a shot, at an attack time)

ACCENTS = ('zoom_in_punch', 'zoom_out_punch', 'rotate_punch', 'vslide_wrap', 'blur_pulse', 'flash_pulse', 'strobe_smear',
           'vsmear', 'glow_pulse', 'bounce', 'shake', 'flash', 'tint_flash', 'echo')


class Accent:
    """
    A per-beat accent at time `at` (the attack): camera (ln s, roll, dx, dy) and an image effect for
    dt = t - at in [-pre, dur). Dance punches (Uf_4: x1.44 gone in 83 ms), K-pop strobe / vertical smears.
    """

    def __init__(self, kind, at, ctx, sign=1.0, amount=1.0, center=None):
        self.kind, self.at, self.ctx, self.sg, self.amount = kind, float(at), ctx, sign, amount
        # dance.json: the punch is centred on the SUBJECT, not the frame ("centred on the subject, not the
        # frame"), and the safety rule says it must centre on the face / upper body because the reference
        # itself punches into legs otherwise. `center` is (x, y) in canvas fractions; None = the old
        # frame-centre behaviour. A zoom by s about p instead of the centre c0 is the same warp plus a
        # translation of (p - c0)(1 - s), which is what cam() adds below.
        self.center = (float(center[0]), float(center[1])) if center else None
        self.pre = 10 * F60 if kind == 'strobe_smear' else 0.0
        self.dur = {'zoom_in_punch': 0.15, 'zoom_out_punch': 0.1, 'rotate_punch': 0.15, 'vslide_wrap': 0.1, 'blur_pulse': 0.08,
                    'flash_pulse': 0.08, 'strobe_smear': 18 * F60 + 0.3, 'vsmear': 14 * F60, 'glow_pulse': 0.3, 'bounce': 0.4,
                    'shake': 0.35, 'flash': 0.12, 'tint_flash': 0.12, 'echo': 0.6}.get(kind, 0.2)
        self.s0 = float(ctx.rng.uniform(1.41, 1.61)) if kind == 'zoom_in_punch' else 1.44

    def active(self, t):
        return -self.pre <= t - self.at < self.dur

    def _about(self, lns):
        """Extra (dx, dy) in canvas-width fractions that moves a centre-warp onto self.center."""
        if not self.center or abs(lns) < 1e-6:
            return (0.0, 0.0)
        w, h = self.ctx.size
        k = 1 - math.exp(lns)
        return ((self.center[0] - 0.5) * k, (self.center[1] - 0.5) * (h / float(w)) * k)

    def cam(self, t):
        dt = t - self.at
        k = dt / F60
        a = self.amount
        if dt < 0:
            return ZERO
        if self.kind == 'zoom_in_punch':
            lns = math.log(self.s0) * 0.5 ** k * a
            ox, oy = self._about(lns)
            return (lns, 0, ox, oy)
        if self.kind == 'zoom_out_punch':
            lns = math.log(1 - 0.08 * 0.4 ** k) * a
            ox, oy = self._about(lns)
            return (lns, 0, ox, oy)
        if self.kind == 'rotate_punch':
            lns = math.log(1 + 0.5 * 0.5 ** k) * a
            ox, oy = self._about(lns)
            return (lns, self.sg * 30 * 0.5 ** k * a, ox, 0.22 * 0.5 ** k * a + oy)
        if self.kind == 'bounce':
            return (math.log(1 + fx.punch(dt, 0.03 * a, tau=0.12)), 0, 0, 0)
        if self.kind == 'shake':
            d = (1 - dt / self.dur) ** 2 * 0.015 * a
            return (0, 1.2 * d / 0.015 * fx.smooth_noise(t, self.at * 3.3 + 1, 6) * 0.5,
                    d * fx.smooth_noise(t, self.at * 13.1, 10), d * fx.smooth_noise(t, self.at * 7.7 + 3, 9))
        if self.kind == 'strobe_smear':
            if dt < 0:
                return ZERO
            A = 0.05 * a
            return (math.log(1.03) if k < 1 else 0.0, self.sg * 1.0 * math.exp(-k / 3), 0,
                    A * (-1) ** int(k) * math.exp(-k / 3))
        if self.kind == 'vsmear':
            return (0, 0, 0, 0.17 * math.exp(-dt / (3 * F30)) * self.sg * a)
        return ZERO

    def fx(self, img, t):
        dt = t - self.at
        k = dt / F60
        c = self.ctx
        cen = (self.center[0] * img.shape[1], self.center[1] * img.shape[0]) if self.center else None
        if self.kind == 'zoom_in_punch' and 0 <= k < 2:
            return fx.gain_u8(fx.zoom_blur_c(img, 0.12 * (1 - k / 2), center=cen), 1 + 0.1 * 0.5 ** k)
        if self.kind == 'zoom_out_punch' and 0 <= k < 1:
            return fx.zoom_blur_c(img, 0.05, center=cen)
        if self.kind == 'rotate_punch' and 0 <= k < 4:
            ang = 30 * (0.5 ** k - 0.5 ** (k + 1))
            return fx.rot_blur(img, ang * self.sg)
        if self.kind == 'vslide_wrap' and 0 <= k < 4:
            h = img.shape[0]
            dy = 0.45 * h * 0.45 ** k
            d2 = 0.45 * h * 0.45 ** (k + 1)
            return fx.line_blur(fx.wrap_shift(img, int(self.sg * dy)), min(200, abs(dy - d2)), 90)
        if self.kind == 'blur_pulse' and 0 <= k < 4:
            a = [1.0, 0.6, 0.3, 0.1][int(k)]
            img = fx.gauss_px(img, c.pw(0.02) * a)
            return fx.gain_u8(img, 1.08) if k < 1 else img
        if self.kind == 'flash_pulse' and 0 <= k < 3:
            return fx.gain_u8(img, 1 + 0.35 * 0.45 ** k, 20 * 0.45 ** k)
        if self.kind == 'strobe_smear':
            h = img.shape[0]
            if dt < 0:
                kk = 1 + dt / self.pre
                return fx.gauss_px(fx.gain_u8(img, 1 - 0.45 * kk), c.pw(0.004) * kk)
            if k < 1:
                return fx.gain_u8(fx.line_blur(img, 0.25 * h, 90), 1.5)
            post = 18 * F60
            if dt < post:
                sig = 0.0022 - 0.0017 * dt / post
                img = fx.line_blur(img, c.pw(sig) * 6, 90)
            g = 1.0 if dt < 0.15 else max(0.7, 1 - 0.3 * (dt - 0.15) / 0.15)
            return fx.gain_u8(img, g)
        if self.kind == 'vsmear':
            h = img.shape[0]
            if k < 1:
                L = 0.6 * h
            elif k < 11:
                L = (0.45 - 0.1 * (k - 1) / 10) * h
            else:
                L = 0.35 * h * max(0.0, 1 - (k - 11) / 2)
            return fx.line_blur(img, L, 90)
        if self.kind == 'glow_pulse':
            kk = 0.8 * math.sin(math.pi * min(1.0, dt / 0.15)) if dt < 0.075 else 0.3 + 0.5 * max(0.0, 1 - (dt - 0.075) / 0.2)
            return fx.bloom_boost(img, kk * self.amount)
        if self.kind == 'flash' and dt >= 0:
            return fx.fill_mix(img, (255, 255, 255), 0.7 * ex(dt, 0.035))
        if self.kind == 'tint_flash' and dt >= 0:
            return fx.fill_mix(img, c.colors[0], 0.5 * ex(dt, 0.04))
        if self.kind == 'echo' and dt >= 0:
            ghost = fx.warp(img, 1.35, 0, 0, -0.15 * img.shape[0], (img.shape[1], img.shape[0]))
            return fx.mix_u8(img, ghost, 0.35 * (1 - dt / self.dur))
        return img


class FlashSmear(T):
    """
    hype_typography i4f (PJUNKIE) cut, measured frame by frame on the remake (core/edit/refs/remake-hype_typography.md):
    the picture NEVER stops under the flash. The outgoing accelerates into the cut (zoom blur + exposure over its last
    2 frames, a camera punch growing x1.3-1.5 per frame), the switch frames are blown out (white veil 0.45-0.6) over a
    zoom-blurred MOVING picture, then blur and exposure decay over ~6 frames. No pure white / black hold (dead frames).
    transition_params: frames (switch frames @30, default 1-2), invert (one inverted frame first), veil (peak white).
    """
    name = 'flash_smear'
    pre = 2 * F30

    def setup(self):
        self.L = int(self.kw.get('frames') or (1 if self.ctx.rng.random() < 0.6 else 2)) * F30
        self.L = min(self.L, 3 * F30)
        self.post = self.L + 0.22
        self.veil = float(self.kw.get('veil', 0.55))
        self.inv = bool(self.kw.get('invert', False))
        self.sgn = self.sign()
        self.punch = self.rnd(0.05, 0.09)

    def cam_out(self, u):
        k = max(0.0, 1 - u / self.pre)
        return (self.punch * k * k, self.sgn * 1.5 * k * k, 0.0, 0.0)

    def cam_in(self, v):
        return (self.punch * 0.8 * ex(v, 0.07), -self.sgn * 1.0 * ex(v, 0.06), 0.0, 0.0)

    def fx_out(self, img, u):
        k = max(0.0, 1 - u / self.pre)
        img = fx.zoom_blur_c(img, 0.16 * k)
        img = fx.gain_u8(img, 1 + 0.5 * k * bright_att(img))
        return self.white(img, 0.25 * k * k)

    def fx_in(self, img, v):
        if v < self.L:
            if self.inv and v < F30:
                img = fx.mix_u8(img, fx.invert_u8(img), 0.75)
            img = fx.zoom_blur_c(img, 0.2)
            return self.white(img, self.veil * (1 - 0.3 * v / self.L))
        w = v - self.L
        img = fx.zoom_blur_c(img, 0.14 * ex(w, 0.06))
        return fx.gain_u8(img, 1 + 0.55 * ex(w, 0.08) * bright_att(img))


REGISTRY[FlashSmear.name] = FlashSmear


# the ZkWQloS4jtM photo / collage / phone family registers itself into REGISTRY / ALIASES
try:  # noqa: E402
    import photo_phone as _photo_phone  # noqa: F401,E402
except Exception as _e:  # pragma: no cover - never break the engine over an optional module
    import sys as _sys
    print('photo_phone not loaded: %s' % _e, file=_sys.stderr)


class BlurBridgeZkw(BlurBridge):
    """
    blur_flow remake of ZkWQloS4jtM (the owner's favourite): the reference's own bridge, read off its frames at 1.8 /
    13.5 s and general-av.json (flow around 17 bridges: -3..+6 frames 1.6 2.5 4.0 | 7.9 | 5.0 4.1 3.2 2.7 1.7 %W/frame).
    The outgoing pushes in and defocuses over its last 3 frames and blooms; 2 frames of warm, glowing defocus on the
    switch; the incoming arrives tilted 10-18 deg (60 % of cuts), zoomed in (rotation cover + 12 %), defocused and
    over-exposed (x1.42) and settles level / sharp / to exposure over ~10 frames (e_out 0.33 s; exposure tau 0.15 s).
    """
    name = 'blur_bridge_zkw'
    pre = 4 * F30
    post = 0.50

    def setup(self):
        super().setup()
        self.dir = self.sign()
        self.theta0 = self.rnd(10.0, 18.0) if self.ctx.rng.random() < float(self.kw.get('roll_p', 0.75)) else 0.0
        self.z0 = float(self.kw.get('zoom', 0.3))
        self.settle = float(self.kw.get('settle', 0.42))
        self.sig_max = float(self.kw.get('sigma', 0.018))          # of the width
        self.glow = float(self.kw.get('glow', 0.45))

    def _cover(self, deg):
        a = math.radians(abs(deg))
        w, h = self.ctx.size
        r = max(w, h) / float(min(w, h))
        return math.cos(a) + r * math.sin(a)

    def cam_out(self, u):
        k = max(0.0, 1 - u / self.pre)
        rot = self.dir * 6.0 * k ** 1.5 if self.theta0 else 0.0
        return (float(getattr(self, 'push', 0.22)) * k ** 1.5 + math.log(self._cover(rot)), rot, 0, 0)

    def cam_in(self, v):
        e = e_out(min(1.0, v / self.settle))
        rot = -self.dir * self.theta0 * (1 - e)
        lns = math.log(self._cover(rot)) + self.z0 * (1 - e_out(min(1.0, v / self.settle)))
        return (lns, rot, 0, 0)

    def _bloom(self, img, a):
        if a <= 0.01:
            return img
        return fx.screen_layer(img, fx.gauss_px(img, self.ctx.pw(0.03)).astype(np.float32) / 255.0 * 0.9, a)

    def fx_out(self, img, u):
        k = max(0.0, 1 - u / self.pre)
        img = fx.gauss_px(img, self.ctx.pw(self.sig_max) * k ** 1.5)
        img = fx.gain_u8(img, 1 + 0.25 * k * k * bright_att(img))
        return self._bloom(img, self.glow * k * k)

    def fx_in(self, img, v):
        hold = 2 * F30
        w = max(0.0, v - hold)
        sig = self.ctx.pw(self.sig_max) * (1.0 if v < hold else math.exp(-w / 0.07)) * min(1.0, max(0.0, (0.34 - v) / 0.08))
        img = fx.gauss_px(img, sig)
        img = fx.gain_u8(img, 1 + 0.42 * ex(v, 0.15) * bright_att(img))
        return self._bloom(img, self.glow * (1.0 if v < hold else math.exp(-w / 0.1)))


REGISTRY[BlurBridgeZkw.name] = BlurBridgeZkw


class FlashWhiteZkw(FlashWhite):
    """ZkWQ remake: the reference's white flash cuts carry the same camera as its bridges (flow -3..+3 frames
    1.4 2.4 4.8 | 8.4 | 5.1 7.3 3.1): outgoing push + defocus + bloom into the flash, incoming tilted / zoomed settling."""
    name = 'flash_white_zkw'
    _cover = BlurBridgeZkw._cover
    cam_out = BlurBridgeZkw.cam_out
    cam_in = BlurBridgeZkw.cam_in
    _bloom = BlurBridgeZkw._bloom
    fx_out = BlurBridgeZkw.fx_out

    def setup(self):
        super().setup()
        self.pre = 4 * F30
        self.post = max(self.post, 0.5)
        self.dir = self.sign()
        self.theta0 = self.rnd(8.0, 15.0) if self.ctx.rng.random() < float(self.kw.get('roll_p', 0.6)) else 0.0
        self.z0 = float(self.kw.get('zoom', 0.18))
        self.settle = float(self.kw.get('settle', 0.45))
        self.sig_max = float(self.kw.get('sigma', 0.012))
        self.glow = float(self.kw.get('glow', 0.35))


REGISTRY[FlashWhiteZkw.name] = FlashWhiteZkw


class FlashDecay(T):
    """
    S2rzMsl0N7k's white flash (blur_flow S2rz remake, critics round 3), measured on the reference at 24.80 / 27.97 s:
    the outgoing defocuses + whip-smears over its last `pre_frames` (2; 3 on the drop), ONE white frame on the switch
    (luma >= 240), then the incoming arrives washed and defocused and resolves: white veil a0 e^(-t/0.045) (frame luma
    ~230 -> 150 -> 110 -> 90 on a luma-90 shot), gain 1 + g0 e^(-t/0.1), defocus sigma 18 px @1920 e^(-t/0.09), a
    directional smear decaying with the camera; everything is exactly back to the plain shot by `clear_s` (0.3 s).
    The luma across the join only rises into the white frame and only falls after it (selftest: flash_monotonic).
    transition_params: frames (white frames, 1), pre_frames, veil (0.8), gain (0.3), sigma (px @1920, 18),
    smear_out (fraction of W on the last outgoing frame, 0.05), smear_in (0.04), dir (+-1), clear_s.
    """
    name = 'flash_decay'

    def setup(self):
        self.L = max(1, int(self.kw.get('frames') or 1)) * F30
        self.pre = max(1, int(self.kw.get('pre_frames', 2))) * F30
        self.clear_s = float(self.kw.get('clear_s', 0.30))
        self.post = self.L + self.clear_s
        self.a0 = float(self.kw.get('veil', 0.8))
        self.g0 = float(self.kw.get('gain', 0.3))
        self.sig = float(self.kw.get('sigma', 18.0))
        self.sm_out = float(self.kw.get('smear_out', 0.05))
        self.sm_in = float(self.kw.get('smear_in', 0.04))
        self.dir = float(self.kw.get('dir') or self.sign())
        self.veil_tau = float(self.kw.get('veil_tau', 0.045))      # reference 24.80: veil e^(-t/0.095) from 0.86 (luma 230 192 152 126 111 100)
        self.white0 = float(self.kw.get('white', 0.97))

    def _clear(self, w):
        return min(1.0, max(0.0, (self.clear_s - w) / 0.08))

    def fx_out(self, img, u):
        k = max(0.0, min(1.0, 1 - (u - F30 * 0.5) / self.pre))       # ~1 on the last outgoing frame
        img = fx.gauss_px(img, self.ctx.pw(self.sig * 0.5 / 1920.0) * k * k)
        img = fx.line_blur(img, self.ctx.pw(min(0.12, self.sm_out)) * k * k, 0)
        return fx.gain_u8(img, 1 + 0.25 * k * k * bright_att(img))

    def fx_in(self, img, v):
        if v < self.L - 0.004:             # (a cut time rounded to 4 decimals sits up to 0.4 ms off the frame grid)
            img = fx.gauss_px(img, self.ctx.pw(self.sig / 1920.0))
            return self.white(img, self.white0)
        w = v - self.L + F30                  # the first frame after the white is one frame into the decay
        c = self._clear(w)
        img = fx.gauss_px(img, min(self.ctx.pw(20.0 / 1920.0), self.ctx.pw(self.sig / 1920.0) * ex(w, 0.09)) * c)
        img = fx.line_blur(img, self.ctx.pw(min(0.12, self.sm_in)) * ex(w, float(self.kw.get('smear_tau', 0.05))) * c, 0)   # smear_tau: round 5 (the incoming slide's blur lasts as long as the slide)
        img = fx.gain_u8(img, 1 + self.g0 * ex(w, 0.1) * c * bright_att(img))
        a = self.a0 * ex(w - F30, self.veil_tau) * c
        return self.white(img, a) if a > 0.004 else img


REGISTRY[FlashDecay.name] = FlashDecay


class WhipBlur(T):
    """
    S2rzMsl0N7k's whip (blur_flow S2rz remake, critics round 3, reference 13.30 / 20.47 / 21.73 s): a 2-frame
    directional blur on the outgoing and a 3-frame decaying one on the incoming, drawn as ONE line-integral box blur
    (never stacked shifted copies: no shutter smear on these frames), <= 10 % W, over a readable picture; the camera
    runs through the cut in one direction (outgoing slides `slide` W with an ease-in, the incoming continues and eases
    out, tau 0.12 s) with an optional plane swing (yaw deg, same sense on both sides: the reference's orbit).
    transition_params: dir (+-1), slide (0.04), blur (0.10 W), yaw (0 deg), pre_frames (2).
    """
    name = 'whip_blur'
    no_mblur = True
    post = 0.2

    def setup(self):
        self.dir = float(self.kw.get('dir') or self.sign())
        self.pre = max(1, int(self.kw.get('pre_frames', 2))) * F30
        self.slide = float(self.kw.get('slide', 0.04))
        self.blur = min(0.10, float(self.kw.get('blur', 0.10)))
        self.yaw = float(self.kw.get('yaw', 0.0))

    def _k(self, u):
        return max(0.0, min(1.0, 1 - (u - F30 * 0.5) / self.pre))

    def cam_out(self, u):
        k = self._k(u)
        c = (0.0, 0.0, self.dir * self.slide * k * k, 0.0)
        if self.yaw:
            c = c + (self.dir * self.yaw * k * k, 0.0, 0.0, 0.0, 0.0)
        return c

    def cam_in(self, v):
        e = ex(v, 0.12)
        c = (0.0, 0.0, -self.dir * self.slide * e, 0.0)
        if self.yaw:
            c = c + (-self.dir * self.yaw * e, 0.0, 0.0, 0.0, 0.0)
        return c

    def fx_out(self, img, u):
        k = self._k(u)
        img = fx.gauss_px(img, self.ctx.pw(0.003) * k)
        return fx.line_blur(img, self.ctx.pw(self.blur) * k ** 1.5, 0)

    def fx_in(self, img, v):
        e = ex(v, 0.045)
        if e < 0.05:
            return img
        img = fx.gauss_px(img, self.ctx.pw(0.003) * e)
        return fx.line_blur(img, self.ctx.pw(self.blur) * e, 0)


REGISTRY[WhipBlur.name] = WhipBlur


class StarBarOpen(T):
    """
    S2rzMsl0N7k 2.33 s (blur_flow remake round 5): a pale horizontal bar opens in the middle of the star field and the next shot
    arrives inside it; the bar grows (e_out, 0.42 s) to the full frame while the veil on the incoming picture (0.8 -> 0 e^(-t/0.07))
    clears.  The outgoing plate stays visible above and below the bar.  transition_params: dur (0.42), veil (0.8), slit (0.045 of H).
    """
    name = 'bar_open_stars'
    post = 0.42
    composite_post = True

    def setup(self):
        self.post = float(self.kw.get('dur', 0.42))
        self.veil = float(self.kw.get('veil', 0.8))
        self.slit = float(self.kw.get('slit', 0.045))

    def composite(self, v, t):
        inc = self.ctx.get_img('in', t)
        out = self.ctx.get_img('out', t)
        h, w = inc.shape[:2]
        k = e_out(min(1.0, v / self.post))
        half = (self.slit + (0.5 - self.slit) * k) * h
        yy = np.arange(h, dtype=np.float32)[:, None]
        edge = max(1.0, 0.004 * h)
        m = np.clip((half - np.abs(yy - h / 2)) / edge, 0.0, 1.0)
        m = np.repeat(m, w, axis=1)
        a = self.veil * ex(v, 0.07)
        band = fx.fill_mix(inc, (214, 222, 220), a) if a > 0.004 else inc
        return fx.blend_mask(out, band, m)


REGISTRY[StarBarOpen.name] = StarBarOpen
