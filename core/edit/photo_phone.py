#!/usr/bin/env python3
"""
The ZkWQloS4jtM photo / collage / phone family, copied frame for frame.

Reference: https://youtu.be/ZkWQloS4jtM (Copines / Marin), 3840x2160 @30, 161.5 BPM
(beat period P = 0.3715 s, felt 81 BPM).  Every number below is measured, per frame, in
`core/edit/refs/zkw_photo_phone.md` / `.json`; the tracks live under
`C:/Users/Home/AppData/Local/Temp/edit-refs/general/zkw_photo_phone/`.

  photo_to_scene           ref f0-f41 (0.000-1.367 s).  A printed photo card on lavender
                           halftone paper - crimson brush strokes, sakura stickers - holding
                           the LIVE incoming shot.  The card turns level (12.90 deg -> 0,
                           power ease-out p = 1.36) while it zooms exponentially
                           (0.549 W -> 0.733 W over 24 frames, +1.2 %/frame accelerating),
                           then a 6-frame exposure blow-out + defocus ramp hands over to the
                           full-frame scene exactly on the 2.5-beat mark, which resolves out of
                           sigma 0.0059 W of defocus and +107/255 of exposure over 11 frames.

  scene_to_photo_collage   ref f229-f252 (7.633-8.400 s).  The full-frame shot shrinks into a
                           dark-bordered card on a light wall (0.697 W -> 0.374 W over 20
                           frames, ease-out) while six more cards slide in from outside,
                           staggered, motion-blurred; faint grey hearts on the wall, a black
                           letterbox of 0.032 H top and bottom.  Registered under its own name
                           AND aliased over the old `photo_collage` so existing plans inherit
                           the measured version (the old class stays as `photo_collage_legacy`).

  photo_to_phone           ref f252-f262 (8.400-8.533 s + hold).  The collage turns out to be
                           the top post of a phone feed: the camera keeps pulling back, the app
                           chrome, the phone body, a hand and the incoming shot appear.  The
                           pull-back is a pure exponential settle, tau = 0.0847 s = 0.228 P;
                           the beat falls 3 frames after the move starts.  The phone holds,
                           tilted -5.6 deg easing to -4.9, the feed scrolling slowly.
                           The reference does NOT come back out of the phone (a heart wipe
                           takes over at f291), so there is no phone_to_scene.

Everything drawn here is original: the paper, the strokes, the sakura, the phone chassis, the
hand and a generic feed UI (no brand marks, no copied glyphs).

Registration (no rewrite of a shared file - dict writes only):
    transitions.REGISTRY['photo_to_scene' | 'scene_to_photo_collage' | 'photo_to_phone']
    transitions.ALIASES['photo_collage'] -> 'scene_to_photo_collage'  (upgrade in place)
    transitions.ALIASES['photo_collage_legacy'] -> the old class, still reachable
    layouts.SCENES['photo_scene_intro']      (the same intro as a scene, for t = 0 use)
    styles.LEAD_MS[*]['photo_to_scene' | 'scene_to_photo_collage' | 'photo_to_phone']

Plan usage:
    {"transition_in": "photo_to_scene",
     "transition_params": {"beats": 2.5, "paper": "lavender", "stickers": 4}}
    {"transition_in": "photo_to_phone",
     "transition_params": {"build_beats": 2.0, "settle_beats": 1.0, "hand": true}}
    {"transition_in": "scene_to_photo_collage", "transition_params": {"build_beats": 2.0}}
    overlays: {"type": "photo_scene_intro", "t0": 0.0, "t1": 1.4,
               "cards": [{"path": "...", "clip_t": 2.0}]}
"""
import math
import os
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
_MEME = os.path.join(HERE, '..', 'meme')
if _MEME not in sys.path:
    sys.path.insert(0, _MEME)

import edit_fx as fx  # noqa: E402
import transitions as TR  # noqa: E402

# --------------------------------------------------------------------------------------
# measured constants (fractions of the canvas width W unless said otherwise)
# --------------------------------------------------------------------------------------
REF = dict(
    fps=30.0, width=3840, height=2160, bpm=161.499, beat=0.37152,
    # -- photo_to_scene ------------------------------------------------------------------
    A=dict(
        n_card=24, n_blow=6, n_resolve=11, n_land=30,     # frames
        w0=0.54944, w24=0.73336,                          # card outer width / W
        growth_exp=1.1015,                                # log(w/w0) = b * n**growth_exp
        theta0=12.90, theta_n=22.6, theta_p=1.362,        # deg, level at n, power ease-out
        cx0=0.49262, cx24=0.49956, cy0=0.50528, cy24=0.49959,
        border=0.00852,                                   # 32.7 px @3840, constant in screen space
        radius=0.00573,                                   # 22 px @3840
        border_bgr=(22, 6, 16),
        shadow_opacity=0.0,                               # measured: none (paper flat to +-2 %)
        paper_bgr=(242, 226, 239), paper_edge_bgr=(207, 196, 205),
        stroke_bgr=(143, 100, 222),
        fade_in_frames=12,                                # black -> paper, vignette opening
        # the open-up is a RADIAL gain, not a fade to black.  ratio[n] is the measured frame-mean
        # luma divided by its plateau (172.0 for the card phase, 111.6 once the scene is full
        # frame), so it transfers to any footage; vig[n] is corner/centre of that gain.
        ratio=[0.4349, 0.5227, 0.6099, 0.7070, 0.7948, 0.8634, 0.9221, 0.9698, 1.0029, 1.0262,
               1.0401, 1.0529, 1.0587, 1.0605, 1.0605, 1.0581, 1.0483, 1.0349, 1.0221, 1.0070,
               1.0029, 0.9988, 0.9953, 0.9738, 0.9773, 1.0355, 1.1471, 1.2122, 1.2529, 1.2663],
        ratio_full=[(30, 1.707), (31, 1.658), (32, 1.566), (33, 1.630), (34, 1.519), (35, 1.416),
                    (36, 1.317), (37, 1.191), (38, 1.151), (39, 1.105), (40, 1.055), (41, 1.000)],
        vig=[0.016, 0.029, 0.190, 0.328, 0.435, 0.517, 0.616, 0.693, 0.768, 0.845, 0.921, 1.000,
             1.011, 1.004, 1.000],
        sticker_in=8,                                     # frames over which the sakura scale in
        lum_base=111.6, lum_peak=217.8, lum_peak_n=29,    # frame mean luma
        sigma_peak=0.00591,                               # 22.7 px @3840 at the land frame
        sigma=[(24, 0.0), (25, 0.00048), (26, 0.00129), (27, 0.00190), (28, 0.00278),
               (29, 0.00377), (30, 0.00591), (31, 0.00276), (32, 0.00422), (33, 0.00326),
               (34, 0.00375), (35, 0.00360), (36, 0.00304), (37, 0.00275), (38, 0.00203),
               (39, 0.00150), (40, 0.00073), (41, 0.0)],
        lum=[(24, 168.1), (25, 178.1), (26, 197.3), (27, 208.5), (28, 215.5), (29, 217.8),
             (30, 190.5), (31, 185.0), (32, 174.8), (33, 181.9), (34, 169.5), (35, 158.0),
             (36, 147.0), (37, 132.9), (38, 128.5), (39, 123.3), (40, 117.7), (41, 111.9)],
    ),
    # -- scene_to_photo_collage ----------------------------------------------------------
    B=dict(
        n_build=26,                                       # f226 (full frame) -> f252
        n0=226,
        w=[(226, 1.00000), (229, 0.83971), (230, 0.78530), (231, 0.73835), (232, 0.69727),
           (236, 0.57507), (240, 0.49693), (244, 0.44232), (248, 0.40311), (252, 0.37415)],
        cx=[(226, 0.50000), (252, 0.50253)], cy=[(226, 0.50000), (252, 0.50987)],
        border=0.00599, radius=0.0039,                    # 23 px / 15 px @3840
        wall_bgr=(232, 232, 232), letterbox=0.032,        # fraction of H, top and bottom
        shadow_opacity=0.30, shadow_sigma=0.0065, shadow_offset=(0.0016, 0.0031),
        # settled layout at f251, (x0, y0, x1, y1) in fractions of (W, H); "from" = entry side
        slots=[(0.000, 0.078, 0.225, 0.597, 'left', 10),
               (0.300, 0.142, 0.528, 0.373, 'top', 3),
               (0.692, 0.116, 0.922, 0.364, 'top', 12),
               (0.085, 0.653, 0.433, 1.000, 'bottom', 15),
               (0.613, 0.582, 0.950, 0.924, 'right', 17),
               (0.439, 0.702, 0.606, 0.920, 'bottom', 20)],
        main=(0.315, 0.315, 0.692, 0.703),
        # a 9:16 canvas cannot carry six 16:9 slots and stay legible (the owner's blocking rule:
        # the viewer must always see the subject), so a portrait canvas gets four bigger ones,
        # laid out on the same staggered-entry principle
        slots_portrait=[(0.000, 0.055, 0.560, 0.300, 'left', 3),
                        (0.480, 0.240, 1.000, 0.470, 'right', 10),
                        (0.030, 0.690, 0.590, 0.930, 'bottom', 15),
                        (0.530, 0.760, 1.000, 0.985, 'bottom', 20)],
        main_portrait=(0.120, 0.360, 0.880, 0.700),
        hearts=14,
    ),
    # -- photo_to_phone ------------------------------------------------------------------
    C=dict(
        tau_frames=2.54,                                  # exponential settle
        beat_after_start=3,                               # the beat lands 3 frames in
        screen_w=[(255, 0.50286), (256, 0.45833), (257, 0.38359), (258, 0.32604),
                  (259, 0.28646), (260, 0.25964), (261, 0.24323), (262, 0.23411),
                  (265, 0.22943), (270, 0.22552), (275, 0.22188), (280, 0.21927),
                  (285, 0.21693), (290, 0.21516)],
        screen_ar=0.487,                                  # w / h of the screen
        theta0=-5.60, theta1=-4.90,                       # deg, CCW
        cx=[(262, 0.28302), (290, 0.30680)], cy=0.4998,
        # after the settle the reference keeps drifting: the screen narrows 0.2341 -> 0.2186 W
        # and the phone slides right 0.283 -> 0.307 W over 28 frames, the tilt easing -5.6 -> -4.9
        drift_scale=-0.00236, drift_x=0.000857, drift_theta=0.025,   # per frame @30
        body_pad=0.055,                                   # phone body border, fraction of screen w
        body_bgr=(92, 62, 58), body_edge_bgr=(150, 120, 112),
        scroll_px_per_s=0.055,                            # feed scroll, fraction of screen h per s
    ),
)

_F = 1.0 / REF['fps']


# --------------------------------------------------------------------------------------
# easing
# --------------------------------------------------------------------------------------
def ease_out_pow(u, p):
    u = min(1.0, max(0.0, u))
    return 1.0 - (1.0 - u) ** p


def ease_in_pow(u, p):
    u = min(1.0, max(0.0, u))
    return u ** p


def settle(u, tau):
    """Exponential settle: 1 at u = 0, -> 0 as u grows (tau in the same unit as u)."""
    return math.exp(-max(0.0, u) / max(tau, 1e-6))


def track(pairs, n):
    """Linear interpolation of a measured (frame, value) track."""
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    return float(np.interp(n, xs, ys))


def shaped_gain(img, ratio, vig, power=1.6):
    """Multiply by a radial map whose corner / centre ratio is `vig` and whose MEAN gain is
    exactly `ratio`: the measured vignette shape and the measured frame-mean in one step."""
    h, w = img.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    rx = (xx - w * .5) / (w * .5)
    ry = (yy - h * .5) / (h * .5)
    r = np.clip(np.sqrt(rx * rx + ry * ry) / math.sqrt(2.0), 0, 1) ** power
    shape = 1.0 + (vig - 1.0) * r
    f = img.astype(np.float32)
    shape = shape[..., None]
    m = float(f.mean())
    if m < 1.0:
        return img
    tgt = m * ratio
    g = 1.0
    for _ in range(6):
        cur = float(np.clip(f * (shape * g), 0, 255).mean())
        if abs(cur - tgt) < 0.3:
            break
        g *= 1.0 + 0.95 * (tgt - cur) / max(cur, 1.0)
        g = min(max(g, 0.01), 30.0)
    return np.clip(f * (shape * g), 0, 255).astype(np.uint8)


def expose_to(img, target_mean, bloom=0.55):
    """Lift the exposure until the frame's mean luma hits `target_mean`.

    The reference's blow-out is a real exposure push - the highlights clip to white while the
    darks stay dark - so a linear mix towards white (which only greys the frame) is wrong.  A
    gain is solved for with two Newton steps against the clipped mean, and a bloom is screened
    on top for the glow around the blown highlights."""
    f = img.astype(np.float32)
    cur = float(f.mean())
    target_mean = min(float(target_mean), 248.0)     # never ask for more than is reachable
    if target_mean <= cur + 0.5:
        return img
    g = target_mean / max(cur, 1.0)
    for _ in range(4):
        m = float(np.clip(f * g, 0, 255).mean())
        if abs(m - target_mean) < 0.4:
            break
        g *= 1.0 + 0.9 * (target_mean - m) / max(m, 1.0)
        g = min(g, 6.0)
    out = np.clip(f * g, 0, 255).astype(np.uint8)
    if bloom > 0.01:
        try:
            out = fx.bloom_boost(out, bloom * min(1.0, (g - 1.0)), threshold=0.55)
        except Exception:
            pass
    return out


# --------------------------------------------------------------------------------------
# drawing primitives (all original art)
# --------------------------------------------------------------------------------------
def round_rect_mask(size, x0, y0, x1, y1, radius, feather=1.0):
    """Float 0..1 mask of an axis-aligned rounded rectangle."""
    w, h = size
    m = np.zeros((h, w), np.float32)
    x0, y0, x1, y1 = float(x0), float(y0), float(x1), float(y1)
    r = max(0.0, min(radius, (x1 - x0) / 2 - 1, (y1 - y0) / 2 - 1))
    ss = 4
    box = (max(0, int(x0) - 4), max(0, int(y0) - 4),
           min(w, int(x1) + 5), min(h, int(y1) + 5))
    bw, bh = box[2] - box[0], box[3] - box[1]
    if bw <= 0 or bh <= 0:
        return m
    big = np.zeros((bh * ss, bw * ss), np.uint8)
    ax0, ay0 = (x0 - box[0]) * ss, (y0 - box[1]) * ss
    ax1, ay1 = (x1 - box[0]) * ss, (y1 - box[1]) * ss
    rr = r * ss
    cv2.rectangle(big, (int(ax0 + rr), int(ay0)), (int(ax1 - rr), int(ay1)), 255, -1)
    cv2.rectangle(big, (int(ax0), int(ay0 + rr)), (int(ax1), int(ay1 - rr)), 255, -1)
    for (cx, cy) in ((ax0 + rr, ay0 + rr), (ax1 - rr, ay0 + rr),
                     (ax0 + rr, ay1 - rr), (ax1 - rr, ay1 - rr)):
        cv2.circle(big, (int(round(cx)), int(round(cy))), int(round(rr)), 255, -1)
    small = cv2.resize(big, (bw, bh), interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0
    if feather > 0.3:
        small = cv2.GaussianBlur(small, (0, 0), feather)
    m[box[1]:box[3], box[0]:box[2]] = small
    return m


def card_rgba(img, w, h, border_px, radius_px, border_bgr):
    """A photo card: `img` inside a solid border, rounded corners.  Returns (bgr, alpha)."""
    w, h = max(8, int(round(w))), max(8, int(round(h)))
    b = int(round(max(0.0, border_px)))
    iw, ih = max(2, w - 2 * b), max(2, h - 2 * b)
    card = np.empty((h, w, 3), np.uint8)
    card[:] = np.array(border_bgr, np.uint8)
    card[b:b + ih, b:b + iw] = cv2.resize(img, (iw, ih), interpolation=cv2.INTER_AREA)
    alpha = round_rect_mask((w, h), 0, 0, w, h, radius_px, feather=0.8)
    return card, alpha


def affine_rgba(card, alpha, cx, cy, angle_deg, out_size, scale=1.0):
    """Place a (bgr, alpha) card rotated CCW by angle_deg, centred at (cx, cy)."""
    W, H = out_size
    h, w = card.shape[:2]
    M = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), angle_deg, scale)
    M[0, 2] += cx - w / 2.0
    M[1, 2] += cy - h / 2.0
    layer = cv2.warpAffine(card, M, (W, H), flags=cv2.INTER_LINEAR,
                           borderMode=cv2.BORDER_CONSTANT)
    mask = cv2.warpAffine(alpha, M, (W, H), flags=cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_CONSTANT)
    return layer, mask


_PAPER_CACHE = {}


def halftone_paper(size, base_bgr, edge_bgr, seed=3):
    """The reference's paper: a light lavender field, a fine woven halftone dot texture and a
    soft radial falloff towards the corners (measured: centre 242/226/239, edge 207/196/205)."""
    key = (size, tuple(base_bgr), tuple(edge_bgr), seed)
    if key in _PAPER_CACHE:
        return _PAPER_CACHE[key].copy()
    w, h = size
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    rx = (xx - w * 0.5) / (w * 0.5)
    ry = (yy - h * 0.62) / (h * 0.5)
    r = np.clip(np.sqrt(rx * rx + ry * ry) / 1.35, 0, 1) ** 1.35
    base = np.array(base_bgr, np.float32)
    edge = np.array(edge_bgr, np.float32)
    img = base[None, None, :] * (1 - r[..., None]) + edge[None, None, :] * r[..., None]
    # halftone weave: two crossed sine grids at ~2.2 px @1080 plus a faint diagonal
    p = max(2.0, w / 480.0)
    dots = (np.sin(xx * (2 * math.pi / p)) * np.sin(yy * (2 * math.pi / p)) * 0.5 + 0.5)
    diag = np.sin((xx + yy) * (2 * math.pi / (p * 3.1))) * 0.5 + 0.5
    tex = (dots * 0.72 + diag * 0.28 - 0.5) * 7.0
    img += tex[..., None]
    rng = np.random.default_rng(seed)
    img += rng.normal(0, 1.3, (h, w, 1)).astype(np.float32)
    out = np.clip(img, 0, 255).astype(np.uint8)
    _PAPER_CACHE[key] = out
    return out.copy()


def brush_blob(size, cx, cy, rx, ry, rot_deg, color, seed=0, softness=0.0011):
    """One of the crimson brush strokes behind the card: a fat rounded lozenge with a torn end."""
    w, h = size
    m = np.zeros((h, w), np.float32)
    pts = []
    rng = np.random.default_rng(seed)
    n = 72
    for i in range(n):
        a = 2 * math.pi * i / n
        rr = 1.0 + 0.06 * math.sin(3 * a + seed) + 0.04 * rng.normal()
        # squash one end so it reads like a loaded brush leaving the paper
        taper = 1.0 - 0.34 * max(0.0, math.cos(a))
        pts.append((rx * rr * taper * math.cos(a), ry * rr * math.sin(a)))
    ca, sa = math.cos(math.radians(-rot_deg)), math.sin(math.radians(-rot_deg))
    poly = np.array([[cx + x * ca - y * sa, cy + x * sa + y * ca] for x, y in pts], np.int32)
    cv2.fillPoly(m, [poly], 1.0, cv2.LINE_AA)
    s = max(0.8, softness * w)
    if s > 0.9:
        m = cv2.GaussianBlur(m, (0, 0), s)
    layer = np.empty((h, w, 3), np.uint8)
    layer[:] = np.array(color, np.uint8)
    return layer, m


def sakura(size, cx, cy, r, seed=0, rot_deg=0.0, blur=0.0, petals=5):
    """A five-petal cherry blossom in the reference's style: a saturated pink rim washing out to
    white towards the centre, fine radial veins, a notched petal tip and orange stamens.
    Returns (bgr layer, float mask)."""
    w, h = size
    m = np.zeros((h, w), np.float32)
    rng = np.random.default_rng(seed)
    pink = np.array((205, 165, 250), np.float32)      # BGR, the petal rim (soft, not hot)
    pale = np.array((252, 246, 255), np.float32)      # near the heart
    veins = []
    for i in range(petals):
        a = math.radians(rot_deg + 360.0 * i / petals + rng.uniform(-5, 5))
        px, py = cx + 0.56 * r * math.cos(a), cy + 0.56 * r * math.sin(a)
        ax = (int(round(r * 0.66)), int(round(r * 0.47)))
        cv2.ellipse(m, (int(round(px)), int(round(py))), ax,
                    math.degrees(a), 0, 360, 1.0, -1, cv2.LINE_AA)
        # the heart-shaped notch at the petal tip
        nx, ny = cx + 1.24 * r * math.cos(a), cy + 1.24 * r * math.sin(a)
        cv2.circle(m, (int(round(nx)), int(round(ny))), int(round(r * 0.26)), 0.0, -1, cv2.LINE_AA)
        for k in range(-4, 5):
            b = a + k * 0.062
            veins.append(((cx + r * 0.20 * math.cos(b), cy + r * 0.20 * math.sin(b)),
                          (cx + r * 1.05 * math.cos(b), cy + r * 1.05 * math.sin(b))))
    # radial wash: white heart -> pink rim (the reference is pale for most of the petal)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    d = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / max(1.0, r)
    t = np.clip((d - 0.34) / 0.95, 0, 1)[..., None] ** 1.25
    col = pale[None, None, :] * (1 - t) + pink[None, None, :] * t
    col = np.ascontiguousarray(col)
    for p0, p1 in veins:
        cv2.line(col, (int(p0[0]), int(p0[1])), (int(p1[0]), int(p1[1])),
                 (214, 180, 248), max(1, int(r * 0.010)), cv2.LINE_AA)
    cv2.circle(col, (int(round(cx)), int(round(cy))), int(round(r * 0.17)),
               (246, 248, 255), -1, cv2.LINE_AA)
    for i in range(11):
        a = 2 * math.pi * i / 11 + rng.uniform(0, 0.35)
        rr = r * rng.uniform(0.22, 0.38)
        cv2.circle(col, (int(round(cx + rr * math.cos(a))), int(round(cy + rr * math.sin(a)))),
                   max(1, int(r * 0.042)), (96, 172, 253), -1, cv2.LINE_AA)
    if blur > 0.4:
        col = cv2.GaussianBlur(col, (0, 0), blur)
        m = cv2.GaussianBlur(m, (0, 0), blur)
    return np.clip(col, 0, 255).astype(np.uint8), np.clip(m, 0, 1)


def heart_glyph(size, cx, cy, r, alpha=0.055, color=(150, 150, 150)):
    """One of the faint grey hearts scattered over the collage wall.  Returns a float mask."""
    w, h = size
    m = np.zeros((h, w), np.float32)
    pts = fx.heart_poly(60) if hasattr(fx, 'heart_poly') else None
    if pts is None:
        cv2.circle(m, (int(cx), int(cy)), int(r), 1.0, -1, cv2.LINE_AA)
    else:
        p = np.array(pts, np.float32)
        p = p / max(1e-6, np.abs(p).max())
        poly = np.stack([cx + p[:, 0] * r, cy + p[:, 1] * r], 1).astype(np.int32)
        cv2.fillPoly(m, [poly], 1.0, cv2.LINE_AA)
    return m * alpha, color


# --------------------------------------------------------------------------------------
# the phone: chassis, generic feed UI, hand
# --------------------------------------------------------------------------------------
def _icon_heart(img, cx, cy, r, color, thick=-1):
    p = np.array(fx.heart_poly(60), np.float32)
    p = p / max(1e-6, np.abs(p).max())
    poly = np.stack([cx + p[:, 0] * r, cy + p[:, 1] * r], 1).astype(np.int32)
    if thick < 0:
        cv2.fillPoly(img, [poly], color, cv2.LINE_AA)
    else:
        cv2.polylines(img, [poly], True, color, thick, cv2.LINE_AA)


def _icon_bubble(img, cx, cy, r, color, t):
    cv2.circle(img, (int(cx), int(cy)), int(r), color, t, cv2.LINE_AA)


def _icon_send(img, cx, cy, r, color, t):
    pts = np.array([[cx - r, cy - r * 0.55], [cx + r, cy], [cx - r, cy + r * 0.55],
                    [cx - r * 0.35, cy]], np.int32)
    cv2.polylines(img, [pts], True, color, t, cv2.LINE_AA)


def _icon_mark(img, cx, cy, r, color, t):
    pts = np.array([[cx - r * 0.62, cy - r], [cx + r * 0.62, cy - r],
                    [cx + r * 0.62, cy + r], [cx, cy + r * 0.42], [cx - r * 0.62, cy + r]], np.int32)
    cv2.polylines(img, [pts], True, color, t, cv2.LINE_AA)


def _icon_home(img, cx, cy, r, color, t):
    pts = np.array([[cx - r, cy], [cx, cy - r], [cx + r, cy]], np.int32)
    cv2.polylines(img, [pts], False, color, t, cv2.LINE_AA)
    cv2.rectangle(img, (int(cx - r * 0.62), int(cy)), (int(cx + r * 0.62), int(cy + r * 0.8)),
                  color, t, cv2.LINE_AA)


def _icon_search(img, cx, cy, r, color, t):
    cv2.circle(img, (int(cx - r * 0.15), int(cy - r * 0.15)), int(r * 0.7), color, t, cv2.LINE_AA)
    cv2.line(img, (int(cx + r * 0.32), int(cy + r * 0.32)), (int(cx + r * 0.9), int(cy + r * 0.9)),
             color, t, cv2.LINE_AA)


def _icon_plus(img, cx, cy, r, color, t):
    cv2.rectangle(img, (int(cx - r), int(cy - r)), (int(cx + r), int(cy + r)), color, t, cv2.LINE_AA)
    cv2.line(img, (int(cx - r * 0.5), int(cy)), (int(cx + r * 0.5), int(cy)), color, t, cv2.LINE_AA)
    cv2.line(img, (int(cx), int(cy - r * 0.5)), (int(cx), int(cy + r * 0.5)), color, t, cv2.LINE_AA)


def _icon_play(img, cx, cy, r, color, t):
    cv2.rectangle(img, (int(cx - r), int(cy - r * 0.8)), (int(cx + r), int(cy + r * 0.8)),
                  color, t, cv2.LINE_AA)
    pts = np.array([[cx - r * 0.25, cy - r * 0.4], [cx + r * 0.4, cy], [cx - r * 0.25, cy + r * 0.4]],
                   np.int32)
    cv2.polylines(img, [pts], True, color, t, cv2.LINE_AA)


def _icon_person(img, cx, cy, r, color, t):
    cv2.circle(img, (int(cx), int(cy - r * 0.35)), int(r * 0.42), color, t, cv2.LINE_AA)
    cv2.ellipse(img, (int(cx), int(cy + r * 0.85)), (int(r * 0.78), int(r * 0.62)),
                0, 180, 360, color, t, cv2.LINE_AA)


def feed_screen(sw, sh, posts, avatars, scroll=0.0, accent=(96, 82, 235)):
    """A generic social feed, drawn from scratch: status strip, a row of story rings, posts with
    an author strip / picture / action row, and a bottom nav bar.  No brand marks."""
    sw, sh = max(64, int(sw)), max(64, int(sh))
    img = np.full((sh, sw, 3), 252, np.uint8)
    u = sw / 1000.0                                     # one design unit
    grey, dark = (168, 168, 168), (44, 44, 44)
    t1 = max(1, int(2.4 * u))

    # ---- top chrome ------------------------------------------------------------------
    cv2.rectangle(img, (0, 0), (sw, int(44 * u)), (252, 252, 252), -1)
    cv2.rectangle(img, (int(40 * u), int(16 * u)), (int(120 * u), int(26 * u)), (70, 70, 70), -1)
    cv2.circle(img, (int(sw - 120 * u), int(21 * u)), int(9 * u), (90, 90, 90), t1)
    cv2.rectangle(img, (int(sw - 90 * u), int(14 * u)), (int(sw - 45 * u), int(28 * u)),
                  (90, 90, 90), t1)
    # wordmark: a plain rounded lozenge, deliberately not a logo
    cv2.ellipse(img, (int(150 * u), int(70 * u)), (int(95 * u), int(22 * u)),
                0, 0, 360, (58, 58, 58), -1, cv2.LINE_AA)
    _icon_heart(img, sw - 130 * u, 70 * u, 20 * u, (70, 70, 70), t1)
    _icon_send(img, sw - 62 * u, 70 * u, 20 * u, (70, 70, 70), t1)
    cv2.line(img, (0, int(100 * u)), (sw, int(100 * u)), (232, 232, 232), max(1, int(1.6 * u)))

    y = int(100 * u) - int(scroll * sh)

    def ring(cx, cy, r, av):
        cv2.circle(img, (int(cx), int(cy)), int(r), (120, 90, 235), max(2, int(3.4 * u)), cv2.LINE_AA)
        cv2.circle(img, (int(cx), int(cy)), int(r * 0.82), (252, 252, 252), -1, cv2.LINE_AA)
        if av is None:
            return
        rr = int(r * 0.78)
        patch = cv2.resize(av, (2 * rr, 2 * rr), interpolation=cv2.INTER_AREA)
        m = np.zeros((2 * rr, 2 * rr), np.float32)
        cv2.circle(m, (rr, rr), rr, 1.0, -1, cv2.LINE_AA)
        x0, y0 = int(cx) - rr, int(cy) - rr
        if 0 <= x0 and 0 <= y0 and x0 + 2 * rr <= sw and y0 + 2 * rr <= sh:
            sub = img[y0:y0 + 2 * rr, x0:x0 + 2 * rr].astype(np.float32)
            img[y0:y0 + 2 * rr, x0:x0 + 2 * rr] = np.clip(
                sub * (1 - m[..., None]) + patch.astype(np.float32) * m[..., None], 0, 255).astype(np.uint8)

    # ---- stories ---------------------------------------------------------------------
    sr = 52 * u
    for i in range(5):
        cx = 80 * u + i * 148 * u
        if cx + sr > sw:
            break
        if -3 * sr < y + 80 * u < sh + 3 * sr:
            ring(cx, y + 78 * u, sr, avatars[i % len(avatars)] if avatars else None)
    y += int(168 * u)

    # ---- posts -----------------------------------------------------------------------
    for pi, post in enumerate(posts):
        if post is None:
            continue
        ph = int(sw * post.shape[0] / max(1, post.shape[1]))
        # author strip
        if -120 * u < y < sh:
            cv2.circle(img, (int(46 * u), int(y + 34 * u)), int(24 * u), (226, 226, 226), -1, cv2.LINE_AA)
            av = avatars[pi % len(avatars)] if avatars else None
            if av is not None:
                rr = int(22 * u)
                patch = cv2.resize(av, (2 * rr, 2 * rr), interpolation=cv2.INTER_AREA)
                m = np.zeros((2 * rr, 2 * rr), np.float32)
                cv2.circle(m, (rr, rr), rr, 1.0, -1, cv2.LINE_AA)
                x0, y0 = int(46 * u) - rr, int(y + 34 * u) - rr
                if 0 <= x0 and 0 <= y0 and x0 + 2 * rr <= sw and y0 + 2 * rr <= sh:
                    sub = img[y0:y0 + 2 * rr, x0:x0 + 2 * rr].astype(np.float32)
                    img[y0:y0 + 2 * rr, x0:x0 + 2 * rr] = np.clip(
                        sub * (1 - m[..., None]) + patch.astype(np.float32) * m[..., None],
                        0, 255).astype(np.uint8)
            cv2.rectangle(img, (int(84 * u), int(y + 26 * u)), (int(230 * u), int(y + 40 * u)),
                          (92, 92, 92), -1)
            for k in range(3):
                cv2.circle(img, (int(sw - 40 * u), int(y + 22 * u + k * 12 * u)),
                           max(1, int(3 * u)), (110, 110, 110), -1, cv2.LINE_AA)
        y += int(66 * u)
        # picture
        y0, y1 = y, y + ph
        if y1 > 0 and y0 < sh:
            a0, a1 = max(0, y0), min(sh, y1)
            src = cv2.resize(post, (sw, ph), interpolation=cv2.INTER_AREA)
            img[a0:a1] = src[a0 - y0:a1 - y0]
        y = y1
        # action row
        if -80 * u < y < sh:
            _icon_heart(img, 52 * u, y + 40 * u, 24 * u, (58, 58, 232), -1)
            _icon_bubble(img, 124 * u, y + 40 * u, 22 * u, dark, t1)
            _icon_send(img, 196 * u, y + 40 * u, 22 * u, dark, t1)
            _icon_mark(img, sw - 54 * u, y + 40 * u, 22 * u, dark, t1)
            cv2.rectangle(img, (int(40 * u), int(y + 78 * u)), (int(300 * u), int(y + 90 * u)),
                          (198, 198, 198), -1)
        y += int(110 * u)

    # ---- bottom nav ------------------------------------------------------------------
    navy = sh - int(74 * u)
    cv2.rectangle(img, (0, navy), (sw, sh), (252, 252, 252), -1)
    cv2.line(img, (0, navy), (sw, navy), (230, 230, 230), max(1, int(1.6 * u)))
    for i, fn in enumerate((_icon_home, _icon_search, _icon_plus, _icon_play, _icon_person)):
        fn(img, (0.1 + 0.2 * i) * sw, navy + 36 * u, 20 * u, (48, 48, 48), t1)
    cv2.rectangle(img, (int(sw * 0.33), sh - int(12 * u)), (int(sw * 0.67), sh - int(7 * u)),
                  (60, 60, 60), -1)
    return img


def first_post_centre(sw, sh, post):
    """Where the first post's picture sits inside the screen, as fractions of (sw, sh) - the
    pull-back has to keep THAT centred on the frame while the phone is still huge."""
    u = sw / 1000.0
    ph = sw * post.shape[0] / max(1, post.shape[1])
    top = 100 * u + 168 * u + 66 * u
    return 0.5, float((top + ph * 0.5) / max(1.0, sh))


def phone_layer(canvas, screen, cx, cy, sw, sh, theta, keystone=0.0, pad_frac=0.055,
                body_bgr=(92, 62, 58), edge_bgr=(150, 120, 112), radius_frac=0.085):
    """Compose the phone: chassis (rounded, with a bright edge highlight) + screen, rotated by
    theta (deg CCW) with an optional vertical keystone.  Returns (layer, mask)."""
    W, H = canvas
    pad = max(2.0, pad_frac * sw)
    bw, bh = int(round(sw + 2 * pad)), int(round(sh + 2 * pad))
    body = np.empty((bh, bw, 3), np.uint8)
    body[:] = np.array(body_bgr, np.uint8)
    r = radius_frac * bw
    # edge highlight: a slightly lighter rounded ring just inside the silhouette
    outer = round_rect_mask((bw, bh), 0, 0, bw, bh, r, 0.8)
    inner = round_rect_mask((bw, bh), pad * 0.34, pad * 0.34, bw - pad * 0.34, bh - pad * 0.34,
                            r * 0.92, 0.8)
    ring = np.clip(outer - inner, 0, 1)
    body = np.clip(body.astype(np.float32) * (1 - ring[..., None])
                   + np.array(edge_bgr, np.float32)[None, None, :] * ring[..., None],
                   0, 255).astype(np.uint8)
    sx0, sy0 = int(round(pad)), int(round(pad))
    body[sy0:sy0 + int(sh), sx0:sx0 + int(sw)] = cv2.resize(
        screen, (int(sw), int(sh)), interpolation=cv2.INTER_AREA)
    smask = round_rect_mask((bw, bh), sx0, sy0, sx0 + sw, sy0 + sh, r * 0.55, 0.8)
    body = np.clip(body.astype(np.float32) * np.maximum(smask, 1.0)[..., None], 0, 255).astype(np.uint8)
    alpha = outer
    if abs(keystone) < 1e-4:
        return affine_rgba(body, alpha, cx, cy, theta, canvas)
    quad = fx.card_quad((W, H), cx, cy, bw, bh, rx=0.0, ry=keystone * 30.0, rz=-theta)
    layer, mask = fx.warp_to_quad(body, quad, (W, H))
    am, _ = fx.warp_to_quad(cv2.cvtColor((alpha * 255).astype(np.uint8), cv2.COLOR_GRAY2BGR),
                            quad, (W, H))
    return layer, np.clip(mask * (am[:, :, 0].astype(np.float32) / 255.0), 0, 1)


def hand_layer(canvas, cx, cy, sw, sh, theta, skin=(168, 196, 240), line=(96, 118, 170),
               part='front'):
    """A stylised hand holding the phone.  'back' = the palm and the four fingers that pass
    behind the phone's left edge, 'front' = only the thumb that laps over the lower left, the
    way the reference's hand reads.  Original art - our footage has no phone in it."""
    W, H = canvas
    layer = np.zeros((H, W, 3), np.uint8)
    mask = np.zeros((H, W), np.float32)
    ca, sa = math.cos(math.radians(-theta)), math.sin(math.radians(-theta))

    def put(lx, ly, ax, ay, ang):
        px = cx + lx * ca - ly * sa
        py = cy + lx * sa + ly * ca
        cv2.ellipse(mask, (int(px), int(py)), (int(max(2, ax)), int(max(2, ay))),
                    ang - theta, 0, 360, 1.0, -1, cv2.LINE_AA)

    if part == 'back':
        put(-sw * 0.46, sh * 0.20, sw * 0.34, sh * 0.19, 0)      # palm
        for i, fy in enumerate((0.02, 0.13, 0.24, 0.34)):        # fingers reaching round
            put(-sw * 0.50, sh * fy, sw * 0.26, sh * 0.035, 5 - 3 * i)
    else:
        put(-sw * 0.30, sh * 0.30, sw * 0.13, sh * 0.048, -26)   # thumb, over the bezel
        put(-sw * 0.42, sh * 0.36, sw * 0.12, sh * 0.055, -14)   # its base
    mask = cv2.GaussianBlur(mask, (0, 0), max(1.0, 0.0012 * W))
    layer[:] = np.array(skin, np.uint8)
    edge = np.clip(cv2.GaussianBlur(mask, (0, 0), max(1.5, 0.003 * W)) - mask, 0, 1)
    layer = np.clip(layer.astype(np.float32) * (1 - edge[..., None] * 1.7)
                    + np.array(line, np.float32)[None, None, :] * edge[..., None] * 1.7,
                    0, 255).astype(np.uint8)
    return layer, np.clip(mask, 0, 1)


# --------------------------------------------------------------------------------------
# shared builders
# --------------------------------------------------------------------------------------
def _postcard_background(ctx, k, seed=0):
    """The intro paper at progress k (0 at the first frame, 1 when the card is level):
    the fade-up from black, the crimson strokes and the sakura stickers scaling in."""
    W, H = ctx.size
    A = REF['A']
    bg = halftone_paper((W, H), A['paper_bgr'], A['paper_edge_bgr'], seed=seed)
    # two crimson brush strokes, the top one wide, the lower one clipped by the frame
    lay, m = brush_blob((W, H), 0.47 * W, 0.055 * H, 0.30 * W, 0.075 * H, -6.0,
                        A['stroke_bgr'], seed=seed + 1)
    bg = fx.blend_mask(bg, lay, m)
    lay, m = brush_blob((W, H), 0.17 * W, 0.93 * H, 0.30 * W, 0.085 * H, 5.0,
                        A['stroke_bgr'], seed=seed + 2)
    bg = fx.blend_mask(bg, lay, m)
    # the third lobe, behind the card's right shoulder (ref f12-f24)
    lay, m = brush_blob((W, H), 0.67 * W, 0.21 * H, 0.075 * W, 0.155 * H, -12.0,
                        A['stroke_bgr'], seed=seed + 3)
    bg = fx.blend_mask(bg, lay, m)
    return bg


def _sticker_set(ctx, n, seed=0):
    """Sakura positions measured off the reference: two big blossoms hugging the left edge and
    two smaller ones on the right, all softly out of focus."""
    W, H = ctx.size
    spots = [(0.030, 0.150, 0.138, 14, 0.0045),
             (0.050, 0.330, 0.100, -22, 0.0075),
             (0.985, 0.615, 0.092, 8, 0.0050),
             (0.965, 0.320, 0.064, 32, 0.0095),
             (0.105, 0.835, 0.062, -8, 0.0100),
             (0.930, 0.870, 0.050, 18, 0.0120)]
    out = []
    for i, (fx_, fy, fr, rot, bl) in enumerate(spots[:max(0, int(n))]):
        out.append((fx_, fy, fr, rot, bl, seed + i))
    return out


def _radial_gain(size, g_centre, g_corner, power=1.6):
    """The intro's open-up: a radial multiply from g_centre at the middle to g_corner at the
    frame corners (measured, not a fade to black)."""
    w, h = size
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    rx = (xx - w * 0.5) / (w * 0.5)
    ry = (yy - h * 0.5) / (h * 0.5)
    r = np.clip(np.sqrt(rx * rx + ry * ry) / math.sqrt(2.0), 0, 1) ** power
    return (g_centre + (g_corner - g_centre) * r).astype(np.float32)


# --------------------------------------------------------------------------------------
# 1. photo_to_scene
# --------------------------------------------------------------------------------------
class PhotoToScene(TR.T):
    """
    ZkW f0-f41.  The incoming shot lives inside a bordered photo card on lavender halftone
    paper (crimson brush strokes, sakura stickers).  The card turns level while it zooms
    exponentially, then blows out to white with a defocus ramp and lands full frame on the
    beat, resolving out of the blur over one more beat.
    """
    name = 'photo_to_scene'
    post = 3.5 * 0.37152
    composite_post = True

    def setup(self):
        A = REF['A']
        P = max(0.12, float(self.ctx.beat or 0.37))
        self.beats = float(self.kw.get('beats', A['n_land'] * _F / REF['beat']))  # 2.69
        self.land = float(self.kw.get('land', self.beats * P))
        k = self.land / (A['n_land'] * _F)                       # reference -> our tempo
        self.t_card = A['n_card'] * _F * k
        self.t_blow = self.land
        self.t_end = (A['n_card'] + A['n_blow'] + A['n_resolve']) * _F * k
        self.post = self.t_end
        self.k = k
        self.seed = int(self.kw.get('seed', 0))
        self.n_stick = int(self.kw.get('stickers', 4))
        self.fade = bool(self.kw.get('fade_in', True))
        self.shadow = float(self.kw.get('shadow', A['shadow_opacity']))
        self._bg = None
        self._stick = None
        self._stick_cache = {}
        self._base_mean = 111.6

    # -- measured curves -----------------------------------------------------------------
    def _card_n(self, v):
        """Reference frame number for our time v (seconds since the switch)."""
        return v / (_F * self.k)

    def _width(self, n):
        A = REF['A']
        b = math.log(A['w24'] / A['w0']) / (A['n_card'] ** A['growth_exp'])
        return A['w0'] * math.exp(b * max(0.0, n) ** A['growth_exp'])

    def _theta(self, n):
        A = REF['A']
        return A['theta0'] * (1.0 - ease_out_pow(n / A['theta_n'], A['theta_p']))

    def _sigma(self, n):
        return track(REF['A']['sigma'], min(max(n, 24.0), 41.0)) if n >= 24 else 0.0

    def _lift(self, n):
        A = REF['A']
        if n < 24:
            return 0.0
        L = track(A['lum'], min(max(n, 24.0), 41.0))
        return max(0.0, (L - A['lum_base']) / (255.0 - A['lum_base']))

    def _ratio(self, n):
        """Measured frame-mean luma / its plateau, for the card phase (n < 30)."""
        A = REF['A']
        r = A['ratio']
        return float(np.interp(min(max(n, 0.0), len(r) - 1.0), range(len(r)), r))

    def _vig(self, n):
        v = REF['A']['vig']
        return float(np.interp(min(max(n, 0.0), len(v) - 1.0), range(len(v)), v))

    def _ratio_full(self, n):
        """Same, for the full-frame resolve (n >= 30), baseline = the clean shot."""
        p = REF['A']['ratio_full']
        return float(np.interp(min(max(n, p[0][0]), p[-1][0]),
                               [q[0] for q in p], [q[1] for q in p]))

    def composite(self, v, t):
        W, H = self.ctx.size
        A = REF['A']
        inc = self.ctx.get_img('in', t)
        n = self._card_n(v)

        # the hand-over is decided in FRAMES, so it lands on exactly the measured frame whatever
        # the output fps / rounding does to v
        if n >= A['n_land'] - 0.5:                              # full-frame resolve
            out = inc
            s = self._sigma(n) * W
            if s > 0.4:
                out = fx.gauss_px(out, s)
            rr = self._ratio_full(n)
            if rr > 1.003:
                out = expose_to(out, float(out.mean()) * rr)
            return out

        # ---- card phase ----------------------------------------------------------------
        if self._bg is None:
            self._bg = _postcard_background(self.ctx, 1.0, self.seed)
            self._stick = _sticker_set(self.ctx, self.n_stick, self.seed)
            self._stick_cache = {}
        bg = self._bg.copy()

        wfrac = self._width(n)
        # the last frames accelerate so the card is exactly frame-size at the land frame
        if n > A['n_card']:
            u = (n - A['n_card']) / float(A['n_blow'])
            w_end = self._width(A['n_card'])
            wfrac = w_end + (1.0 - w_end) * ease_in_pow(min(1.0, u), 2.1)
        cw = wfrac * W
        ch = cw * H / float(W)
        cx = np.interp(n, [0, A['n_card']], [A['cx0'], A['cx24']]) * W
        cy = np.interp(n, [0, A['n_card']], [A['cy0'], A['cy24']]) * H
        theta = self._theta(n)

        card, alpha = card_rgba(inc, cw, ch, A['border'] * W, A['radius'] * W, A['border_bgr'])
        layer, mask = affine_rgba(card, alpha, cx, cy, theta, (W, H))
        if self.shadow > 0.01:
            bg = fx.shadow_under(bg, mask, 0.018 * W, (0.004 * W, 0.006 * W), self.shadow)
        out = fx.blend_mask(bg, layer, mask)

        # the sakura sit IN FRONT of the card (they overlap its edges in the reference) and
        # scale in over the first 8 frames
        sc = ease_out_pow(min(1.0, (n + 1.0) / A['sticker_in']), 2.0)
        for (sx, sy, sr, rot, bl, sd) in self._stick:
            key = (sd, round(sc, 2))
            if key not in self._stick_cache:
                if len(self._stick_cache) > 64:
                    self._stick_cache.clear()
                self._stick_cache[key] = sakura((W, H), sx * W, sy * H, max(4.0, sr * W * sc),
                                                seed=sd, rot_deg=rot, blur=bl * W * sc)
            lay, m = self._stick_cache[key]
            out = fx.blend_mask(out, lay, m)

        if self.fade:
            rr, vg = self._ratio(n), self._vig(n)
            if abs(rr - 1.0) > 0.004 or abs(vg - 1.0) > 0.01:
                out = shaped_gain(out, rr, vg)
        # the reference's defocus is a CAMERA defocus: it softens the paper, the strokes and the
        # stickers as well as the card, so it goes on the composed frame
        s = self._sigma(n) * W
        if s > 0.4:
            out = fx.gauss_px(out, s)
        return out


# --------------------------------------------------------------------------------------
# 2. scene_to_photo_collage  (upgrades photo_collage)
# --------------------------------------------------------------------------------------
class CollageBuild:
    """The measured collage: the live shot shrinking into its slot while six cards slide in."""

    def __init__(self, ctx, seed=0, build_beats=None, letterbox=None):
        self.ctx = ctx
        B = REF['B']
        P = max(0.12, float(ctx.beat or 0.37))
        ref_build = B['n_build'] * _F                       # 0.667 s
        self.build = float(build_beats * P) if build_beats else ref_build * (P / REF['beat'])
        self.k = self.build / ref_build
        self.seed = int(seed)
        self.letterbox = B['letterbox'] if letterbox is None else float(letterbox)
        self._wall = None
        self._cards = None

    def _wall_bg(self):
        W, H = self.ctx.size
        B = REF['B']
        wall = halftone_paper((W, H), B['wall_bgr'],
                              tuple(int(c * 0.86) for c in B['wall_bgr']), seed=self.seed + 5)
        rng = np.random.default_rng(self.seed + 17)
        for i in range(B['hearts']):
            x = rng.uniform(0.02, 0.98) * W
            y = rng.uniform(0.05, 0.95) * H
            r = rng.uniform(0.008, 0.02) * W
            m, col = heart_glyph((W, H), x, y, r, alpha=rng.uniform(0.03, 0.08))
            lay = np.empty((H, W, 3), np.uint8)
            lay[:] = np.array(col, np.uint8)
            wall = fx.blend_mask(wall, lay, m)
        return wall

    def _stills(self, n):
        try:
            st = (self.ctx.stills(n) if self.ctx.stills else []) or []
        except Exception:
            st = []
        return st

    def slots(self):
        W, H = self.ctx.size
        B = REF['B']
        return B['slots_portrait'] if H > W else B['slots']

    def render(self, t, u, live):
        """u = 0 at the start of the build, 1 when the collage is settled."""
        W, H = self.ctx.size
        B = REF['B']
        if self._wall is None:
            self._wall = self._wall_bg()
            self._cards = self._stills(len(self.slots()))
        out = self._wall.copy()
        u = min(1.0, max(0.0, u))
        n0 = B['n0']
        n = n0 + u * B['n_build']

        # the surrounding cards, each starting `delay` frames into the build
        slots = self.slots()
        for i, (x0, y0, x1, y1, side, delay) in enumerate(slots):
            du = (n - (n0 + delay)) / max(1.0, B['n_build'] - delay)
            if du <= 0:
                continue
            e = ease_out_pow(min(1.0, du), 2.4)
            cw, ch = (x1 - x0) * W, (y1 - y0) * H
            cx, cy = (x0 + x1) * 0.5 * W, (y0 + y1) * 0.5 * H
            off = {'left': (-1.0, 0.0), 'right': (1.0, 0.0),
                   'top': (0.0, -1.0), 'bottom': (0.0, 1.0)}[side]
            trav = (1.0 - e)
            px = cx + off[0] * (cw * 0.6 + 0.35 * W) * trav
            py = cy + off[1] * (ch * 0.6 + 0.35 * H) * trav
            # the reference leaves ONE slot empty (a bare card on the wall); the rest cycle
            # through whatever other shots the engine can give us
            if i == len(slots) - 1 or not self._cards:
                pic = np.full((int(max(4, ch)), int(max(4, cw)), 3),
                              np.array(B['wall_bgr'], np.uint8), np.uint8)
            else:
                pic = self._cards[i % len(self._cards)]
            card, alpha = card_rgba(pic, cw, ch, B['border'] * W, B['radius'] * W,
                                    REF['A']['border_bgr'])
            layer, mask = affine_rgba(card, alpha, px, py, 0.0, (W, H))
            out = fx.shadow_under(out, mask, B['shadow_sigma'] * W,
                                  (B['shadow_offset'][0] * W, B['shadow_offset'][1] * W),
                                  B['shadow_opacity'])
            out = fx.blend_mask(out, layer, mask)

        # the live card
        wfrac = track(B['w'], n)
        cw = wfrac * W
        ch = cw * H / float(W)
        cx = np.interp(n, [n0, 252], [B['cx'][0][1], B['cx'][1][1]]) * W
        cy = np.interp(n, [n0, 252], [B['cy'][0][1], B['cy'][1][1]]) * H
        card, alpha = card_rgba(live, cw, ch, B['border'] * W, B['radius'] * W,
                                REF['A']['border_bgr'])
        layer, mask = affine_rgba(card, alpha, cx, cy, 0.0, (W, H))
        out = fx.shadow_under(out, mask, B['shadow_sigma'] * W,
                              (B['shadow_offset'][0] * W, B['shadow_offset'][1] * W),
                              B['shadow_opacity'])
        out = fx.blend_mask(out, layer, mask)
        if self.letterbox > 0.001:
            out = fx.letterbox_bars(out, self.letterbox)
        return out


class SceneToPhotoCollage(TR.T):
    """
    ZkW f229-f252, the measured pull-back: the outgoing shot shrinks from full frame to a
    0.374 W dark-bordered card on a light wall while six more cards slide in from outside,
    staggered over 20 frames; then a whip slide into the next shot (kept from the old
    photo_collage so this is a drop-in upgrade).
    """
    name = 'scene_to_photo_collage'
    pre = 26 * _F
    post = 0.12
    composite_pre = composite_post = True

    def setup(self):
        P = max(0.12, float(self.ctx.beat or 0.37))
        self.build_beats = self.kw.get('build_beats')
        self.pre = float(self.build_beats) * P if self.build_beats else REF['B']['n_build'] * _F * (P / REF['beat'])
        self.col = CollageBuild(self.ctx, seed=self.kw.get('seed', 0),
                                build_beats=self.build_beats,
                                letterbox=self.kw.get('letterbox'))
        self.dir = self.kw.get('dir') or self.sign()
        self._last = None

    def composite(self, v, t):
        if v < 0:
            u = 1.0 + v / self.pre
            live = self.ctx.get_img('out', t)
            self._last = self.col.render(t, u, live)
            return self._last
        out = self._last if self._last is not None else self.col.render(
            t, 1.0, self.ctx.get_img('out', t))
        inc = self.ctx.get_img('in', t)
        w = inc.shape[1]
        k = TR.ss(v / self.post)
        off = int(round(self.dir * w * (1 - k)))
        canvas = np.empty_like(inc)
        if off >= 0:
            canvas[:, off:] = inc[:, :w - off]
            if off:
                canvas[:, :off] = out[:, w - off:]
        else:
            canvas[:, :w + off] = inc[:, -off:]
            canvas[:, w + off:] = out[:, :-off]
        return fx.line_blur(canvas, self.ctx.pw(0.08), 0)


# --------------------------------------------------------------------------------------
# 3. photo_to_phone
# --------------------------------------------------------------------------------------
class PhotoToPhone(TR.T):
    """
    ZkW f229-f262.  The outgoing shot pulls back into the photo collage (2 beats), and the
    collage turns out to be the top post of a phone feed: the camera keeps pulling back on a
    pure exponential settle (tau = 0.228 beat) until the phone sits in the incoming shot,
    tilted -5.6 deg, held in a hand, the feed scrolling.  The beat falls 3 frames (0.1 s)
    after the move starts, which is exactly the -100 ms lead the reference uses.
    """
    name = 'photo_to_phone'
    pre = 26 * _F
    post = 1.0 * 0.37152
    composite_pre = composite_post = True
    hold = False

    def setup(self):
        C = REF['C']
        P = max(0.12, float(self.ctx.beat or 0.37))
        self.build_beats = self.kw.get('build_beats')
        self.pre = float(self.build_beats) * P if self.build_beats else REF['B']['n_build'] * _F * (P / REF['beat'])
        self.settle_beats = float(self.kw.get('settle_beats', 1.0))
        # the reference holds the phone for another ~2.6 beats before the heart wipe takes it
        self.hold_beats = float(self.kw.get('hold_beats', 2.6))
        self.post = (self.settle_beats + self.hold_beats) * P
        self.tau = C['tau_frames'] * _F * (P / REF['beat'])
        self.col = CollageBuild(self.ctx, seed=self.kw.get('seed', 0),
                                build_beats=self.build_beats,
                                letterbox=self.kw.get('letterbox'))
        self.hand = bool(self.kw.get('hand', True))
        self.scroll = float(self.kw.get('scroll', C['scroll_px_per_s']))
        W, H = self.ctx.size
        land = W > H
        self.h_frac = float(self.kw.get('phone_h', 0.855 if land else 0.78))
        self.pos_x = float(self.kw.get('phone_x', C['cx'][0][1] if land else 0.40))
        self.skin = tuple(self.kw.get('skin', (168, 196, 240)))
        self._post_img = None
        self._screen = None
        self._avatars = None
        self._last = None
        self._frames = 0.0
        self._t0v = None

    # ---- the screen --------------------------------------------------------------------
    def _avatar_set(self):
        if self._avatars is not None:
            return self._avatars
        try:
            st = (self.ctx.stills(5) if self.ctx.stills else []) or []
        except Exception:
            st = []
        out = []
        for s in st:
            h, w = s.shape[:2]
            c = min(h, w)
            out.append(s[(h - c) // 2:(h - c) // 2 + c, (w - c) // 2:(w - c) // 2 + c])
        self._avatars = out or [np.full((64, 64, 3), 210, np.uint8)]
        return self._avatars

    def _make_screen(self, scroll):
        W, H = self.ctx.size
        C = REF['C']
        sh = int(round(self.h_frac * H))
        sw = int(round(sh * C['screen_ar']))
        second = None
        av = self._avatar_set()
        if av:
            a = av[-1]
            second = cv2.resize(a, (sw, int(sw * 1.0)), interpolation=cv2.INTER_AREA)
        return feed_screen(sw, sh, [self._post_img, second], av, scroll=scroll), sw, sh

    def _phone(self, t, base, k):
        """k = 1 at the switch (phone off the top of the zoom), 0 when settled."""
        W, H = self.ctx.size
        C = REF['C']
        scroll = self.scroll * max(0.0, t - self._t0) if self._t0 is not None else 0.0
        screen, sw, sh = self._make_screen(scroll)
        # settled pose, scaled up by the exponential that is still running
        # while the phone is still huge the FIRST POST must sit on the frame centre - it WAS the
        # collage a moment ago - so the phone is offset by the post's own place inside the
        # screen, and eases to its settled pose as k -> 0
        px, py = self._post_c
        # the settled pose keeps drifting the way the reference's does
        fr = max(0.0, self._frames) * (REF['beat'] / max(0.05, float(self.ctx.beat or REF['beat'])))
        drift = 1.0 + C['drift_scale'] * fr
        settled_x = self.pos_x + C['drift_x'] * fr
        sw, sh = sw * drift, sh * drift
        grow = 1.0 + (self._grow0 / max(drift, 1e-6) - 1.0) * k
        big_cx = 0.5 * W - (px - 0.5) * sw * grow
        big_cy = 0.5 * H - (py - 0.5) * sh * grow
        cx = float(settled_x * W + (big_cx - settled_x * W) * k)
        cy = float(C['cy'] * H + (big_cy - C['cy'] * H) * k)
        # the tilt eases in as the phone settles (ref -5.6 deg once it lands, 0 while it fills)
        theta = (C['theta0'] + C['drift_theta'] * fr) * (1.0 - k)
        out = base
        if self.hand and grow < 3.0:
            hl, hm = hand_layer((W, H), cx, cy, sw * grow, sh * grow, theta,
                                skin=self.skin, part='back')
            out = fx.blend_mask(out, hl, hm)
        lay, m = phone_layer((W, H), screen, cx, cy, sw * grow, sh * grow, theta,
                             keystone=0.0, pad_frac=C['body_pad'],
                             body_bgr=C['body_bgr'], edge_bgr=C['body_edge_bgr'])
        out = fx.shadow_under(out, m, 0.02 * W, (0.006 * W, 0.010 * W), 0.45)
        out = fx.blend_mask(out, lay, m)
        if self.hand and grow < 3.0:
            hl, hm = hand_layer((W, H), cx, cy, sw * grow, sh * grow, theta,
                                skin=self.skin, part='front')
            out = fx.blend_mask(out, hl, hm)
        return out

    def composite(self, v, t):
        W, H = self.ctx.size
        C = REF['C']
        if v < 0:
            u = 1.0 + v / self.pre
            live = self.ctx.get_img('out', t)
            self._last = self.col.render(t, u, live)
            self._post_img = None
            self._t0 = None
            self._t0v = None
            return self._last

        if self._post_img is None:
            # freeze the collage as the post picture (the reference's post is a still)
            src = self._last if self._last is not None else self.ctx.get_img('out', t)
            self._post_img = src.copy()
            self._t0 = t
            # how much bigger the phone is at the switch: the post filled the frame
            screen, sw, sh = self._make_screen(0.0)
            self._grow0 = float(W) / max(1.0, sw)
            self._post_c = first_post_centre(sw, sh, self._post_img)
            self._t0v = v

        k = settle(v, self.tau)
        self._frames = (v - self._t0v) * REF['fps'] if self._t0v is not None else v * REF['fps']
        base = self.ctx.get_img('in', t)
        # the shot behind the phone keeps a shallow defocus all the way (the reference's
        # background is bokeh), deeper while the camera is still moving
        base = fx.gauss_px(base, self.ctx.px(5) + self.ctx.px(9) * k)
        out = self._phone(t, base, k)
        # motion blur only while the pull-back is actually fast (ref f253-f256)
        speed = abs(settle(v, self.tau) - settle(v + 1.0 / self.ctx.fps, self.tau))
        if speed > 0.02:
            out = fx.gauss_px(out, min(self.ctx.px(16), self.ctx.pw(0.045) * speed))
        return out


# --------------------------------------------------------------------------------------
# the intro as a scene (t = 0 use, no outgoing shot needed)
# --------------------------------------------------------------------------------------
class PhotoSceneIntro:
    """`photo_to_scene` as an overlay scene: the card holds the plan's first shot (or the
    overlay's own `cards`), the paper and stickers are drawn, and at t1 the picture is the
    live shot again.  overlays: {"type": "photo_scene_intro", "t0", "t1", "cards": [...]}"""

    def __init__(self, eng, ov, canvas):
        self.eng, self.ov, self.canvas = eng, ov, canvas
        self.t0, self.t1 = float(ov['t0']), float(ov['t1'])
        W, H = canvas
        self.seed = int(ov.get('seed', 0))
        st = eng.card_stills(ov, 1)
        self.still = st[0] if st else np.full((H, W, 3), 120, np.uint8)

        class _C:
            pass
        c = _C()
        c.size = canvas
        c.beat = getattr(eng, 'P', 0.5)
        c.fps = eng.fps
        c.rng = np.random.default_rng(self.seed)
        c.stills = lambda n: eng.shot_stills(0, n) if hasattr(eng, 'shot_stills') else []
        c.get_img = lambda role, t: self._live(t)
        c.px = lambda v: v * W / 1080.0
        c.pw = lambda f: f * W
        c.flash_color = (255, 255, 255)
        self.ctx = c
        self.tr = PhotoToScene(c, beats=float(ov.get('beats', 2.69)),
                               stickers=int(ov.get('stickers', 4)), seed=self.seed)

    def _live(self, t):
        W, H = self.canvas
        try:
            return self.eng.render_shot(0, t)
        except Exception:
            return cv2.resize(self.still, (W, H), interpolation=cv2.INTER_AREA)

    def render(self, t):
        return self.tr.composite(max(0.0, t - self.t0), t)


# --------------------------------------------------------------------------------------
# registration (dict writes only - no shared file is rewritten)
# --------------------------------------------------------------------------------------
def register():
    TR.REGISTRY[PhotoToScene.name] = PhotoToScene
    TR.REGISTRY[SceneToPhotoCollage.name] = SceneToPhotoCollage
    TR.REGISTRY[PhotoToPhone.name] = PhotoToPhone
    # keep the old implementation reachable, then upgrade the old name in place
    old = TR.REGISTRY.get('photo_collage')
    if old is not None and old is not SceneToPhotoCollage:
        TR.REGISTRY.setdefault('photo_collage_legacy', old)
    TR.ALIASES['photo_collage'] = SceneToPhotoCollage.name
    TR.ALIASES.setdefault('photo_phone', PhotoToPhone.name)
    TR.ALIASES.setdefault('phone', PhotoToPhone.name)
    TR.ALIASES.setdefault('postcard_photo_intro', PhotoToScene.name)
    try:
        import styles as ST
        for fam in ST.LEAD_MS:
            ST.LEAD_MS[fam].setdefault('photo_to_scene', -80)
            ST.LEAD_MS[fam].setdefault('scene_to_photo_collage', -80)
            ST.LEAD_MS[fam].setdefault('photo_to_phone', -100)
    except Exception:
        pass


def register_scenes():
    """Called from the bottom of layouts.py so engine.SCENE_TYPES sees the scene."""
    try:
        import layouts as L
        L.SCENES.setdefault('photo_scene_intro', PhotoSceneIntro)
    except Exception:
        pass


register()

__all__ = ['PhotoToScene', 'SceneToPhotoCollage', 'PhotoToPhone', 'PhotoSceneIntro',
           'CollageBuild', 'feed_screen', 'phone_layer', 'hand_layer', 'sakura',
           'halftone_paper', 'brush_blob', 'round_rect_mask', 'card_rgba', 'affine_rgba',
           'REF', 'register', 'register_scenes']
