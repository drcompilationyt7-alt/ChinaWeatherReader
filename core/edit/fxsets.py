"""
Set pieces of the fx / typography group (core/edit/refs/fx.json, reference WGvtliE1298).

engine.Overlay dispatches here for the overlay types whose picture is BUILT rather than filmed:

    music_card3d   f0-87     a now-playing card in 3D over a black bokeh field, typewriter lyric 13.2 chars/s
    bokeh          f0-87     60 small discs screened over BLACK (the card's background), never over footage
    ink_blot       f107-121  low-frequency-noise blot to 35 % coverage, interior to black, bold word mark
    word3d         f149-192  per-word fly-through: smeared entry, 2-8 frame hold, exit past the lens
    ground_text    f216-238  the word standing in the plate's ground plane, contact shadow, plate-matched smear
    glass_set      f292-330  near-black set, perspective filmstrip travelling with motion blur, crack web + sparks
    white_set      f331-375  luma-217 white set with drifting leaf particles (the letters_fly background)
    polaroids      f376-413  3 big foreshortened polaroids, back to front, shadows, motion-blurred entry
    closing_3d     f472-495  two stacked extruded lines with a cast shadow, letter-by-letter, then held
    fade_out       f494-503  gain 1 - i/9 over 10 frames @30, linear in luma
    watermark_fx   whole     bottom-left 0.02 / 0.955, cap 2.4 %H, white at 0.55, never animated

Every function is `fn(ov, img, t)` with `ov` the engine Overlay (ov.ov = the plan dict, ov.eng = the engine,
ov._cache = a per-overlay dict). All numbers are from fx.json; px values quoted "@1920" scale with the width.
"""
import math
import os
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'meme'))
import edit_fx as fx  # noqa: E402
import text as T  # noqa: E402

F30 = 1 / 30.0
CAP_RATIO = 0.667       # cap height / font size of the Poppins / Anton faces used here (calibrated on renders)


def _ease_out(u, p=3):
    u = min(1.0, max(0.0, u))
    return 1 - (1 - u) ** p


def _stills(ov, n, lo=60.0, hi=215.0):
    """Card content: stills whose mean luma is readable (a near-black or blown still makes an unreadable card)."""
    c = ov._cache
    if 'stills' not in c:
        got = ov.eng.card_stills(ov.ov, n + 3)
        good = [s for s in got if lo <= float(cv2.cvtColor(cv2.resize(s, (64, 36)), cv2.COLOR_BGR2GRAY).mean()) <= hi]
        for s in got:                                       # still short: re-expose what is left
            if len(good) >= n:
                break
            if not any(s is g for g in good):
                m = max(8.0, float(s.mean()))
                good.append(fx.gain_u8(s, min(3.0, max(0.5, 120.0 / m))))
        c['stills'] = good[:n]
    return c['stills']


def _blit_blur(img, sprite, x, y, scale, blur, ang, alpha=1.0):
    T.blit(img, sprite, x, y, alpha=alpha, scale=scale, blur_len=blur, blur_ang=ang, blur_max=401)


# ---------------------------------------------------------------- bokeh + music card

def bokeh_field(size, t, n=60, seed=9):
    """60 discs, radius 6-26 px @1920 (0.3-1.4 %W), drifting slowly; float layer over black."""
    w, h = size
    rng = np.random.default_rng(seed)
    out = np.zeros((h, w, 3), np.float32)
    cols = ((1.0, 0.55, 0.25), (1.0, 0.75, 0.45), (0.9, 0.9, 1.0), (0.55, 0.9, 0.6))
    for _ in range(int(n)):
        x = ((float(rng.uniform(0, 1)) + 0.010 * t * float(rng.uniform(-1, 1))) % 1.0) * w
        y = ((float(rng.uniform(0, 1)) + 0.006 * t * float(rng.uniform(-1, 1))) % 1.0) * h
        r = float(rng.uniform(6, 26)) * w / 1920.0
        c = cols[int(rng.integers(0, len(cols)))]
        a = float(rng.uniform(0.15, 0.75))
        cv2.circle(out, (int(x), int(y)), max(1, int(r)), tuple(v * a for v in c), -1, cv2.LINE_AA)
    return cv2.GaussianBlur(out, (0, 0), max(1.0, 0.0022 * w))


def bokeh(ov, img, t):
    o = ov.ov
    h, w = img.shape[:2]
    dim = float(o.get('dim', 1.0))                         # 1.0 = over BLACK, as measured
    base = fx.gain_u8(img, 1.0 - dim) if dim < 1.0 else np.zeros_like(img)
    glow = ov._cache.get('bg')
    if glow is None:                                       # the faint blue depth haze behind the discs
        yy, xx = np.mgrid[0:h // 8 + 1, 0:w // 8 + 1].astype(np.float32)
        g = np.exp(-(((xx * 8 - 0.5 * w) / (0.55 * w)) ** 2 + ((yy * 8 - 0.5 * h) / (0.5 * h)) ** 2))
        glow = cv2.resize(g, (w, h))[..., None] * np.array([0.16, 0.08, 0.03], np.float32)
        ov._cache['bg'] = glow
    lay = bokeh_field((w, h), t, int(o.get('n', 60)), int(o.get('seed', 9)))
    return fx.screen_layer(base, lay * float(o.get('opacity', 1.0)) + glow, 1.0)


def _rounded(size, radius, color):
    w, h = size
    im = np.zeros((h, w, 4), np.uint8)
    m = np.zeros((h, w), np.uint8)
    r = int(radius)
    cv2.rectangle(m, (r, 0), (w - r, h), 255, -1)
    cv2.rectangle(m, (0, r), (w, h - r), 255, -1)
    for cx, cy in ((r, r), (w - r, r), (r, h - r), (w - r, h - r)):
        cv2.circle(m, (cx, cy), r, 255, -1, cv2.LINE_AA)
    im[..., :3] = color
    im[..., 3] = m
    return im


def _card_face(ov, n_chars, frac):
    """The flat card (BGRA): cover thumb, title, artist, the lyric typed to n_chars, a thin waveform line."""
    o, c = ov.ov, ov._cache
    CW, CH = 1200, 520
    if 'card0' not in c:
        face = _rounded((CW, CH), 46, (0, 0, 0))
        yy = np.linspace(0, 1, CH, dtype=np.float32)[:, None, None]
        top, bot = np.array(o.get('color_top', (236, 170, 60)), np.float32), np.array(o.get('color_bot', (214, 130, 30)), np.float32)
        face[..., :3] = (top * (1 - yy) + bot * yy).astype(np.uint8) * np.ones((1, CW, 1), np.uint8)
        st = _stills(ov, 1)
        if st:
            th = cv2.resize(st[0][:, (st[0].shape[1] - st[0].shape[0]) // 2:(st[0].shape[1] + st[0].shape[0]) // 2], (150, 150),
                            interpolation=cv2.INTER_AREA)
            face[40:190, 46:196, :3] = th
            cv2.rectangle(face, (46, 40), (196, 190), (250, 250, 250, 255), 3)
        bgr = np.ascontiguousarray(face[..., :3])
        ti = T.render(o.get('title', ''), 'lower', 58, case='none')
        ar = T.render(o.get('artist', ''), 'lower', 40, case='none', opacity=0.7)
        T.blit(bgr, ti.img, 226 + ti.size[0] / 2 - ti.pad, 86)
        T.blit(bgr, ar.img, 226 + ar.size[0] / 2 - ar.pad, 150)
        cv2.line(bgr, (560, 440), (1100, 440), (255, 245, 225), 2, cv2.LINE_AA)
        rng = np.random.default_rng(4)
        for k in range(16):
            xk = 566 + k * 9
            hk = int(rng.uniform(3, 22))
            cv2.line(bgr, (xk, 440 - hk), (xk, 440 + hk), (255, 250, 240), 3, cv2.LINE_AA)
        face[..., :3] = bgr
        c['card0'] = face
        c['lyric'] = T.render(o.get('text', ''), 'lower', 76, case='none', font='poppins_semibold', tracking=0.06)
    key = (n_chars, round(frac, 1))
    if c.get('card_key') != key:
        face = c['card0'].copy()
        bgr = np.ascontiguousarray(face[..., :3])
        ly = c['lyric']
        T.blit(bgr, ly.crop_letters(n_chars, frac), 92 + ly.size[0] / 2 - ly.pad, 300)
        face[..., :3] = bgr
        c['card_key'], c['card'] = key, face
    return c['card']


def music_card3d(ov, img, t):
    """
    fx.json music_card_intro_3d: over 80 frames @30 the card's yaw unwinds ry 26 -> 3 deg while its width grows
    0.34 -> 1.6 of the canvas (exponentially: the camera flies INTO the lyric), on black + bokeh. The lyric types
    at 13.2 chars/s with a 3-frame per-character fade.
    """
    o = ov.ov
    h, w = img.shape[:2]
    since = t - ov.t0
    dur = float(o.get('dur', 80 * F30))
    u = min(1.0, max(0.0, since / dur))
    base = bokeh(ov, img, t)
    cps = float(o.get('cps', 13.2))
    pos = max(0.0, since - float(o.get('type_at', 0.35))) * cps
    face = _card_face(ov, int(pos), min(1.0, (pos - int(pos)) / (cps * 3 * F30)))
    w0, w1 = float(o.get('w0', 0.34)), float(o.get('w1', 1.6))
    cw = w * w0 * (w1 / w0) ** u
    ry = 26.0 + (3.0 - 26.0) * _ease_out(u, 2)
    ch = cw * face.shape[0] / face.shape[1]
    # the camera aims at the lyric's typing head, so the card slides left as it grows
    cx = 0.5 * w + cw * (0.10 - 0.42 * u)
    cy = 0.5 * h - ch * 0.08 * u
    quad = fx.card_quad((w, h), cx, cy, cw, ch, rx=4.0 * (1 - u), ry=-ry, rz=-3.0 * (1 - u))
    src = np.array([[0, 0], [face.shape[1], 0], [face.shape[1], face.shape[0]], [0, face.shape[0]]], np.float32)
    M = cv2.getPerspectiveTransform(src, quad.astype(np.float32))
    lay = cv2.warpPerspective(face, M, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
    m = lay[..., 3].astype(np.float32) / 255.0
    # a translucent glass slab behind the card, offset in depth
    q2 = fx.card_quad((w, h), cx - 0.05 * cw, cy - 0.03 * ch, cw * 1.08, ch * 1.12, rx=4.0 * (1 - u), ry=-ry, rz=-3.0 * (1 - u))
    slab = np.zeros((h, w), np.uint8)
    cv2.fillConvexPoly(slab, np.round(q2 * 16).astype(np.int32), 255, cv2.LINE_AA, shift=4)
    out = fx.blend_mask(base, np.full_like(base, (150, 110, 40)), slab.astype(np.float32) / 255.0 * 0.35)
    out = fx.blend_mask(out, np.ascontiguousarray(lay[..., :3]), m)
    rate = math.log(w1 / w0) / max(1e-3, dur) / ov.eng.fps      # ln-scale per output frame
    return fx.zoom_blur_c(out, min(0.05, 0.9 * rate), steps=8, down=1) if rate > 0.004 else out


# ---------------------------------------------------------------- ink blot

def ink_mask(size, progress, seed=3, side='top', coverage=0.35, cache=None):
    """Low-frequency noise + an edge bias, thresholded at the level that gives `coverage * progress` coverage."""
    w, h = size
    sw, sh = max(16, w // 8), max(16, h // 8)
    key = ('ink', sw, sh, seed, side)
    field = cache.get(key) if cache is not None else None
    if field is None:
        rng = np.random.default_rng(seed)
        nz = cv2.GaussianBlur(rng.uniform(0, 1, (sh, sw)).astype(np.float32), (0, 0), sw / 14.0)
        nz2 = cv2.GaussianBlur(rng.uniform(0, 1, (sh, sw)).astype(np.float32), (0, 0), sw / 40.0)
        nz = (nz - nz.min()) / max(1e-6, float(np.ptp(nz))) + 0.35 * (nz2 - nz2.min()) / max(1e-6, float(np.ptp(nz2)))
        yy, xx = np.mgrid[0:sh, 0:sw].astype(np.float32)
        d = {'top': yy / sh, 'bottom': 1 - yy / sh, 'left': xx / sw, 'right': 1 - xx / sw}.get(side, yy / sh)
        hx = np.abs(xx / sw - 0.62) if side in ('top', 'bottom') else np.abs(yy / sh - 0.5)
        field = nz * 0.55 - d * 1.25 - hx * 0.6
        if cache is not None:
            cache[key] = field
    cov = max(0.0, min(0.98, coverage * min(1.0, max(0.0, progress))))
    if cov <= 0.002:
        return np.zeros((h, w), np.float32)
    thr = float(np.quantile(field, 1.0 - cov))
    m = np.clip((field - thr) * 60.0 + 0.5, 0, 1)
    m = cv2.resize(m, (w, h), interpolation=cv2.INTER_CUBIC)
    return np.clip(cv2.GaussianBlur(m, (0, 0), max(1.0, 0.004 * w)), 0, 1)


def ink_blot(ov, img, t):
    o = ov.ov
    h, w = img.shape[:2]
    since = t - ov.t0
    p = _ease_out(since / max(0.1, float(o.get('grow', 15 * F30))), 2)
    m = ink_mask((w, h), p, int(o.get('seed', 3)), o.get('side', 'top'), float(o.get('coverage', 0.35)), ov._cache)
    floor = float(o.get('floor', 0.12))
    out = (img.astype(np.float32) * (1.0 - m[..., None] * (1.0 - floor) * 1.08)).clip(0, 255).astype(np.uint8)
    if o.get('text'):
        if ov._cache.get('mark') is None:
            ov._cache['mark'] = T.render(o['text'], 'caption_bar', int(float(o.get('size', 0.05)) / CAP_RATIO * h),
                                         fill=(255, 255, 255), case=o.get('case', 'lower'), max_w=int(0.4 * w))
            mf = ink_mask((w, h), 1.0, int(o.get('seed', 3)), o.get('side', 'top'), float(o.get('coverage', 0.35)), ov._cache)
            dist = cv2.distanceTransform((mf > 0.5).astype(np.uint8), cv2.DIST_L2, 3)
            yx = np.unravel_index(int(np.argmax(dist)), dist.shape)
            ov._cache['mark_xy'] = (float(yx[1]), float(max(yx[0], 0.09 * h)))
        a = min(1.0, max(0.0, (p - 0.15) / 0.3))
        if a > 0:
            x, y = ov._cache['mark_xy']
            T.blit(out, ov._cache['mark'].img, float(o['x']) * w if 'x' in o else x, float(o['y']) * h if 'y' in o else y, alpha=a)
    return out


# ---------------------------------------------------------------- word3d

def _subject(ov, t):
    """(x, y) of the subject of the shot on screen at t, as canvas fractions, or None."""
    try:
        eng = ov.eng
        s = eng.shots[eng.shot_at(t)]
        f = s['_cam'].get('focus') or eng.foot(s, None).prep.focus
        return float(f[0]), float(f[1])
    except Exception:
        return None


def word3d(ov, img, t):
    """
    Each word is a point in space the camera flies past. Read off f148-158 ("and"): 3-4 frames of smeared ENTRY
    (cap 10.8 -> 20 %H, arriving along its depth line from the vanishing point), a sharp HOLD of 2-8 frames that
    keeps creeping toward the lens (cap 20 -> 25.3 %H), then an EXIT past the lens in 4 frames with a smear that
    doubles every frame; it never fades and is killed once its cap height passes 26 %H x the exit growth.
    `park: true` words stay small at depth instead of exiting (f176: "when" and "and" behind "i'm").
    """
    o = ov.ov
    h, w = img.shape[:2]
    fps = float(ov.eng.fps)
    cap_in, cap_hold, cap_out = float(o.get('cap0', 0.108)), float(o.get('cap_hold', 0.20)), float(o.get('cap1', 0.253))
    n_in = float(o.get('entry_f', 4))
    n_out = float(o.get('exit_f', 3))                  # round 4: x1.25 / frame for 3 frames, alpha 1, 0.6, 0.25
    vertical = bool(o.get('vertical', h > w))
    vp = o.get('vp') or ([0.5, 0.40] if vertical else [0.42, 0.5])          # OFF-centre vanishing point
    live = []
    for i, (wd, sp) in enumerate(ov._sprites or []):
        k = (t - float(wd['t0'])) * 30.0
        hold = float(wd.get('hold', o.get('hold_f', 5)))
        if k < -n_in:
            continue
        park = bool(wd.get('park'))
        if not park and k >= hold + n_out:
            continue
        if park and t > float(wd.get('t1', ov.t1)):
            continue
        live.append((i, wd, sp, k, hold, park))
    if not live:
        return img
    img = img.copy()

    def state(i, wd, k, hold, park):
        rng = np.random.default_rng(int(wd.get('seed', i * 37 + 11)))
        # LEGIBILITY: the word lives in the negative space BESIDE the subject (f153-158: next to the face,
        # never on it) - across from the shot's focus at 16:9, under / over it at 9:16.
        sub = _subject(ov, float(wd['t0']))
        if vertical:
            sx0, sy0 = 0.5, (0.70 if (sub is None or sub[1] < 0.55) else 0.28)
            jx, jy = 0.06, 0.04
        else:
            sx0 = 0.5 if sub is None else min(0.76, max(0.24, sub[0] + (0.30 if sub[0] < 0.5 else -0.30)))
            sy0, jx, jy = 0.5, (0.20 if sub is None else 0.04), 0.08
        sx = float(wd['x']) if 'x' in wd else sx0 + float(rng.uniform(-jx, jx))
        sy = float(wd['y']) if 'y' in wd else sy0 + float(rng.uniform(-jy, jy))
        if k < 0:                                   # entry: x1.21 per frame, the measured 10.8 -> 23.6 in 4
            cap = cap_in * (cap_hold / cap_in) ** (1 + k / n_in)
        elif k < hold or park and k < hold:
            cap = cap_hold * (cap_out / cap_hold) ** (k / max(1.0, hold))
        elif park:
            cap = cap_out * (0.42 + 0.58 * math.exp(-(k - hold) / 2.5))
        else:
            cap = cap_out * 1.25 ** (k - hold)      # f157-160: x1.25 / frame; the SLIP carries it out, not the size
        g = cap / cap_hold
        x = (vp[0] + (sx - vp[0]) * g) * w
        y = (vp[1] + (sy - vp[1]) * g) * h
        if not park and k >= hold:                  # passing the lens: the lateral slip doubles every frame
            j = k - hold
            d = (-1.0 if sx < vp[0] else 1.0) if not vertical else 0.0
            dv = (-1.0 if sy < vp[1] else 1.0) if vertical else 0.0
            slip = 0.032 * (1.9 ** j - 1) / 0.9
            x, y = x + d * slip * w, y + dv * slip * w
        if k < 0:                                   # arriving: a lateral swing in from the far side, decelerating
            j = -k / n_in
            x += (1.0 if sx >= vp[0] else -1.0) * 0.16 * w * j ** 2 * (0.0 if vertical else 1.0)
            y += (1.0 if sy >= vp[1] else -1.0) * 0.16 * w * j ** 2 * (1.0 if vertical else 0.0)
        return x, y, cap

    order = sorted(live, key=lambda r: state(r[0], r[1], r[3], r[4], r[5])[2])
    for i, wd, sp, k, hold, park in order[-int(o.get('max_alive', 3)):]:
        x, y, cap = state(i, wd, k, hold, park)
        x2, y2, cap2 = state(i, wd, k + 30.0 / fps, hold, park)
        per30 = fps / 30.0                          # the smear is specified per frame @30
        dx, dy = (x2 - x) * per30, (y2 - y) * per30
        blur = 0.5 * math.hypot(dx, dy) + 0.25 * abs(cap2 - cap) * per30 * h
        if 0 <= k < hold or park and k >= hold + 3:
            blur = 0.0 if blur < 0.004 * w else blur
        ang = math.degrees(math.atan2(dy, dx)) if blur > 1 else 0.0
        sc = cap / cap_in / ov.REND
        fit = 0.88 * w / max(1.0, sp.size[0] * cap_out / cap_in / ov.REND)     # a long word at 9:16: fit the HOLD width
        al = 1.0
        if not park and k >= hold:                  # passing the lens: alpha 1, 0.6, 0.25 over the 3 exit frames
            j = k - hold
            al = max(0.0, (1.0, 0.6, 0.25, 0.0)[min(3, int(j))] * (1 - (j - int(j))) + (0.6, 0.25, 0.0, 0.0)[min(3, int(j))] * (j - int(j)))
        _blit_blur(img, sp.img, x, y, sc * min(1.0, fit), blur, ang, alpha=al)
    return img


# ---------------------------------------------------------------- ground text

def ground_text(ov, img, t):
    """
    f216-238: the word stands IN the plate's ground plane, low and foreshortened (glyph height / width ~0.3),
    darker than the plate, with a contact shadow, and it carries exactly the plate's radial smear; the camera
    travels over it and it leaves under the bottom edge in 11 frames @30.
    """
    o = ov.ov
    h, w = img.shape[:2]
    sp = ov._sprite
    if sp is None:
        return img
    exit_f = float(o.get('exit_f', 11))
    dur = max(0.2, ov.t1 - ov.t0)
    k = (t - (ov.t1 - exit_f * F30)) * 30.0            # frames into the exit (< 0 = resting on the ground)
    e = max(0.0, k) / exit_f
    grow = 1.0 + (float(o.get('grow', 4.2)) - 1.0) * e ** 2.2
    rest = 1.0 + 0.10 * (t - ov.t0) / dur               # the slow push before the travel
    s = grow * rest
    cx = (float(o.get('x', 0.5)) + float(o.get('drift', -0.10)) * e ** 2) * w
    cy = (float(o.get('y', 0.70)) + float(o.get('travel', 0.62)) * e ** 2.2) * h
    ww = float(o.get('width', 0.42)) * w * s
    hh = ww * sp.size[1] / max(1, sp.size[0])
    tilt = float(o.get('tilt', 78.0))
    quad = fx.card_quad((w, h), cx, cy, ww, hh / max(0.05, math.cos(math.radians(tilt))) * float(o.get('aspect', 0.30)) / 0.30 * 0.62,
                        rx=tilt, rz=float(o.get('roll', -3.0)), focal=1.1)
    src = np.array([[0, 0], [sp.size[0], 0], [sp.size[0], sp.size[1]], [0, sp.size[1]]], np.float32)
    M = cv2.getPerspectiveTransform(src, quad.astype(np.float32))
    lay = cv2.warpPerspective(sp.img, M, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
    m = lay[..., 3].astype(np.float32) / 255.0
    if float(m.max()) < 0.01:
        return img
    # lit like the plate: the glyphs take the plate's own light (darker than a white title) and tint
    plate_l = float(cv2.resize(img, (32, 18), interpolation=cv2.INTER_AREA).mean())
    tone = float(o.get('tone', min(0.95, max(0.72, plate_l / 255.0 + 0.45))))   # f222-236: pale letters, LIGHTER than the grass
    glyph = (lay[..., :3].astype(np.float32) * tone * (0.75 + 0.25 * np.linspace(1.0, 0.55, h, dtype=np.float32)[:, None, None]))
    glyph = glyph.clip(0, 255).astype(np.uint8)
    # contact shadow: the plate under and just below the glyphs x0.55, feather 1.5 %W
    sh_m = cv2.warpAffine(m, np.float32([[1, 0, 0], [0, 1, 0.012 * w * s]]), (w, h))
    sh_m = np.clip(cv2.GaussianBlur(np.maximum(m, sh_m), (0, 0), 0.015 * w), 0, 1)
    out = (img.astype(np.float32) * (1.0 - 0.45 * sh_m[..., None])).astype(np.uint8)
    out = fx.blend_mask(out, glyph, m * float(o.get('opacity', 0.96)))
    # the SAME smear the plate carries this frame, applied to plate + word together inside the word's band
    bl = float(o.get('plate_blur', 0.027)) * (1.0 + 5.0 * e ** 2)
    if bl >= 0.01:
        y0 = int(max(0, quad[:, 1].min() - 0.08 * h))
        sm = fx.zoom_blur_c(out, min(0.22, bl), steps=12, center=(0.5, float(o.get('horizon', 0.42))), down=1)
        ramp = np.clip((np.arange(h, dtype=np.float32) - y0) / max(1.0, 0.10 * h), 0, 1)[:, None, None]
        out = (out.astype(np.float32) * (1 - ramp) + sm.astype(np.float32) * ramp).astype(np.uint8)
    return out


# ---------------------------------------------------------------- glass set (filmstrip + cracks)

def _strip_texture(thumbs, cells=9, cell=(240, 150)):
    cw, chh = cell
    pad, band = 10, 34
    H = chh + 2 * band
    W = cells * (cw + pad) + pad
    st = np.full((H, W, 3), 14, np.uint8)
    for i in range(cells):
        x = pad + i * (cw + pad)
        if thumbs:
            st[band:band + chh, x:x + cw] = cv2.resize(thumbs[i % len(thumbs)], (cw, chh), interpolation=cv2.INTER_AREA)
    for row in (8, H - 8 - 18):
        for x in range(6, W - 20, 30):
            cv2.rectangle(st, (x, row), (x + 16, row + 18), (238, 238, 238), -1)
    return st


def glass_set(ov, img, t):
    """
    f292-330: a near-black set (luma 27-50). A 35 mm strip crosses it in PERSPECTIVE (near / far width ~2.5 : 1,
    cell band ~12 %H), travelling >= 0.5 W in 16 frames with a 0.5 x |dx| motion blur; a spider crack web is
    screened over the whole frame at 0.55 with 4-6 specular sparks.
    """
    o = ov.ov
    h, w = img.shape[:2]
    since = t - ov.t0
    c = ov._cache
    keep = float(o.get('plate', 0.10))                     # how much of the plate survives under the set
    base = fx.gain_u8(fx.gauss_px(img, 0.012 * w), keep) if keep > 0 else np.zeros_like(img)
    if 'haze' not in c:
        rng = np.random.default_rng(int(o.get('seed', 7)))
        hz = cv2.GaussianBlur(rng.uniform(0, 1, (h // 16 + 1, w // 16 + 1)).astype(np.float32), (0, 0), 3.0)
        hz = (hz - hz.min()) / max(1e-6, float(np.ptp(hz)))
        c['haze'] = (cv2.resize(hz, (w, h)) ** 2)[..., None] * np.array([0.16, 0.14, 0.14], np.float32)
        c['strip'] = _strip_texture(_stills(ov, int(o.get('thumbs', 6))))
    out = fx.screen_layer(base, c['haze'], 1.0)
    st = c['strip']
    speed = float(o.get('speed', 0.5 / (16 * F30)))         # W per second: >= 0.5 W over 16 frames
    settle = 1.0 - math.exp(-since / 0.35)                  # arrives fast and eases (tau ~ 10 frames)
    trav = float(o.get('travel', 0.75)) * settle + 0.04 * since
    L = 2.6 * w
    sh_ = float(o.get('cell_h', 0.12)) * h * 1.45 * 1.6     # near-side height; the far side projects to ~12 %H
    cx, cy = (1.25 - trav) * w, (0.40 + 0.10 * settle) * h
    quad = fx.card_quad((w, h), cx, cy, L, sh_, rx=float(o.get('rx', 12.0)), ry=float(o.get('ry', 42.0)),
                        rz=float(o.get('angle', -14.0)), focal=0.9)
    lay, m = fx.warp_to_quad(st, quad, (w, h))
    v = speed * math.exp(-since / 0.35) / 0.35 * 0.75 * 0.35 + 0.04      # W / s
    bl = 0.5 * abs(v) * w / 30.0
    if bl >= 2:
        ang = float(o.get('angle', -14.0))
        lay = fx.line_blur(lay, bl, ang)
        m = fx.line_blur(cv2.cvtColor((m * 255).astype(np.uint8), cv2.COLOR_GRAY2BGR), bl, ang)[..., 0].astype(np.float32) / 255.0
    out = fx.shadow_under(out, m, 0.015 * w, (0.0, 0.01 * w), 0.5)
    out = fx.blend_mask(out, lay, m)
    p = min(1.0, since / max(0.1, float(o.get('crack_grow', 0.30))))
    key = ('cr', round(p, 1))
    if c.get('cr_key') != key:
        c['cr_key'] = key
        c['cr'] = fx.spider_cracks((w, h), float(o.get('x', 0.5)) * w, float(o.get('y', 0.5)) * h, p, seed=int(o.get('seed', 7)),
                                   rings=6, arms=18, sparks=int(o.get('sparks', 5)))
    return fx.screen_layer(out, c['cr'], float(o.get('opacity', 0.55)))


# ---------------------------------------------------------------- white set + letters

def white_set(ov, img, t):
    """f331-375: the white infinite studio - luma 217, saturation 4, a soft grey floor gradient, 48-142 leaf specks."""
    o = ov.ov
    h, w = img.shape[:2]
    c = ov._cache
    if 'bg' not in c:
        yy = np.linspace(0, 1, h, dtype=np.float32)[:, None]
        xx = np.linspace(-1, 1, w, dtype=np.float32)[None, :]
        lum = 250.0 - 9.0 * np.clip((yy - 0.62) * 2.2, 0, 1) - 4.0 * xx ** 2     # round 3: the set is WHITE (>= 245)
        c['bg'] = np.repeat(lum[..., None], 3, 2).clip(0, 255).astype(np.uint8)
        rng = np.random.default_rng(int(o.get('seed', 5)))
        c['pt'] = [(float(rng.uniform(0, 1)), float(rng.uniform(0, 1)), float(rng.uniform(0.3, 1.0)), float(rng.uniform(0, 6.28)))
                   for _ in range(int(o.get('n', 64)))]
    out = c['bg'].copy()
    since = t - ov.t0
    # round 3: 40-80 brown debris sprites of 0.5-3 %W; the big near ones are out of focus (drawn first, then
    # one blur of the flat set), the small far ones stay crisp
    for big in (True, False):
        for x, y, z, a in c['pt']:
            if (z > 0.72) != big:
                continue
            g = 1.0 + 0.35 * since * z                          # the specks stream past the camera
            px, py = (0.5 + (x - 0.5) * g) * w, (0.5 + (y - 0.5) * g) * h
            if -0.05 * w <= px < 1.05 * w and -0.05 * h <= py < 1.05 * h:
                r = max(1, int((0.0025 + 0.0125 * max(0.0, z - 0.3) / 0.7 * (1.0 if big else 0.35)) * w * g))
                cv2.ellipse(out, (int(px), int(py)), (r, max(1, int(r * 0.55))), math.degrees(a + since * z), 0, 360,
                            (40 + 30 * z, 70 + 30 * z, 105 + 20 * z), -1, cv2.LINE_AA)
        if big:
            out = fx.gauss_px(out, 0.004 * w)
    k = float(o.get('plate', 0.0))
    return out if k <= 0 else cv2.addWeighted(out, 1 - k, img, k, 0)


def gradient_fill(sprite_img, c0=(45, 45, 50), c1=(150, 120, 70)):
    """Replace a BGRA sprite's colour by a horizontal gradient c0 -> c1 (BGR); alpha is kept."""
    out = sprite_img.copy()
    wv = np.linspace(0, 1, out.shape[1], dtype=np.float32)[None, :, None]
    out[..., :3] = (np.array(c0, np.float32) * (1 - wv) + np.array(c1, np.float32) * wv).astype(np.uint8)
    return out


def letters_fly(ov, img, t):
    """
    f331-375: every letter of ONE pre-laid-out word box starts at x = 1.35 W and eases into its own slot over
    5 frames @30, i x 3 frames apart, smeared along ITS OWN motion by 0.5 x |d| (60-140 px @1920 on its first
    two frames). Cap height 25 %H, horizontal gradient fill. Works for multi-line text ("I WAS / IN IT").
    """
    o = ov.ov
    h, w = img.shape[:2]
    c = ov._cache
    since = t - ov.t0
    fps = float(ov.eng.fps)
    if 'lines' not in c:
        style = o.get('style', 'set_fx')
        cap = float(o.get('cap', 0.25))
        size = int(cap / CAP_RATIO * h * (0.80 if style == 'set_fx' else 1.0))
        lines = [ln for ln in str(o.get('text', '')).split('/') if ln.strip()]
        laid, idx = [], 0
        maxw = float(o.get('max_w', 0.86)) * w
        sprs = [T.render(ln.strip(), style, size, tracking=float(o.get('tracking', 0.0))) for ln in lines]
        k = min(1.0, maxw / max(1, max(s.size[0] - 2 * s.pad for s in sprs)))
        if k < 1.0:
            sprs = [T.render(ln.strip(), style, int(size * k), tracking=float(o.get('tracking', 0.0))) for ln in lines]
        lh = (sprs[0].size[1] - 2 * sprs[0].pad) * float(o.get('leading', 0.78))
        y0 = float(o.get('y', 0.5)) * h - lh * (len(sprs) - 1) / 2
        for li, sp in enumerate(sprs):
            simg = gradient_fill(sp.img, *o['gradient']) if o.get('gradient') else sp.img
            left = float(o.get('x', 0.5)) * w - (sp.size[0] - 2 * sp.pad) / 2 + li * float(o.get('line_shift', 0.0)) * w
            ww = max(1.0, float(sp.size[0] - 2 * sp.pad))
            ksz = int(size * k) if k < 1.0 else size
            for j, (ch, xa, xb) in enumerate(sp.letters):
                if ch.strip():
                    # round 3: every letter is its OWN sprite. Slicing the word sprite cut through the
                    # neighbouring glyph (the stray tick / the 'AN|' clip the critic saw).
                    g1 = T.render(ch, style, ksz, tracking=0.0)
                    gi = g1.img
                    if o.get('gradient'):
                        c0, c1 = [np.array(v, np.float32) for v in o['gradient']]
                        ua, ub = xa / ww, xb / ww
                        gi = gradient_fill(gi, tuple(c0 * (1 - ua) + c1 * ua), tuple(c0 * (1 - ub) + c1 * ub))
                    laid.append((idx, gi, left + (xa + xb) / 2.0, y0 + li * lh))
                    idx += 1
        c['lines'] = laid
    flat = bool(o.get('flat', False))
    if not flat:
        base = img
        img = np.zeros_like(base)
        alp = np.zeros_like(base)
    else:
        img = img.copy()
    stag = float(o.get('stagger', 2 * F30))
    x_from = float(o.get('from_x', 1.35)) * w
    fade = min(1.0, (ov.t1 - t) / 0.12) if o.get('fade_out') else 1.0
    for idx, glyph, sx, sy in c['lines']:
        age = since - idx * stag
        if age < 0:
            continue

        def pos(a):
            e = _ease_out(a / (5 * F30), 3)
            return x_from + (sx - x_from) * e, sy + float(o.get('from_dy', 0.0)) * h * (1 - e)
        x, y = pos(age)
        x2, y2 = pos(age + 1.0 / fps)
        dx, dy = (x2 - x) * fps / 30.0, (y2 - y) * fps / 30.0
        d = math.hypot(dx, dy)
        sc = 1.0 + 0.35 * (1 - _ease_out(age / (5 * F30), 3))               # arrives larger (nearer) and settles
        bl, ba = (0.5 * d if d > 3 else 0.0), (math.degrees(math.atan2(dy, dx)) if d > 3 else 0.0)
        _blit_blur(img, glyph, x, y, sc, bl, ba, alpha=fade)
        if not flat:
            wh = glyph.copy()
            wh[..., :3] = 255
            _blit_blur(alp, wh, x, y, sc, bl, ba, alpha=fade)
    if flat:
        return img
    # the word PLANE is yawed ~20 deg and rolled -7 deg, and drifts left as the camera tracks (-1.2 %W per frame
    # at first, 12 %W in total so that the word never leaves the picture)
    ry = math.radians(float(o.get('ry', 20.0)))
    roll = math.radians(float(o.get('roll', -7.0)))
    drift = -float(o.get('drift', 0.12)) * w * (1 - math.exp(-since * 30.0 / 10.0))
    cx, cy = float(o.get('x', 0.5)) * w, float(o.get('y', 0.5)) * h
    f = 1.4 * w
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    dst = []
    for px, py in src:
        X, Y = px - cx, py - cy
        Z = X * math.sin(ry)
        X2, Y2 = X * math.cos(ry) * f / (f + Z), Y * f / (f + Z)
        dst.append([cx + drift + X2 * math.cos(roll) - Y2 * math.sin(roll), cy + X2 * math.sin(roll) + Y2 * math.cos(roll)])
    H = cv2.getPerspectiveTransform(src, np.float32(dst))
    col = cv2.warpPerspective(img, H, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
    al = cv2.warpPerspective(alp, H, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
    ys, xs = np.nonzero(al[..., 0])
    if not len(ys):
        return base
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    out = base.copy()
    a = al[y0:y1, x0:x1].astype(np.float32) / 255.0
    out[y0:y1, x0:x1] = np.clip(out[y0:y1, x0:x1].astype(np.float32) * (1 - a) + col[y0:y1, x0:x1].astype(np.float32), 0, 255).astype(np.uint8)
    return out


def closing_3d(ov, img, t):
    """
    f472-495: two stacked lines of extruded caps (cap 23.6 %H, tracking 0, second line offset right 0.12 W) with a
    cast shadow down-right (3 % of the cap, sigma 2 %W, alpha 0.35), assembled letter by letter and then HELD.
    Occlusion by a foreground object needs a segmentation model; place the phrase where the plate has no subject.
    """
    o = dict(ov.ov)
    o.setdefault('style', 'closing_3d')
    o.setdefault('cap', 0.236)
    o.setdefault('line_shift', 0.12)
    o.setdefault('stagger', 1.5 * F30)
    o.setdefault('from_x', -0.25)
    h, w = img.shape[:2]
    keep = ov.ov
    ov.ov = o
    try:
        lay = letters_fly(ov, np.zeros_like(img), t)
        key = letters_fly(ov, np.full_like(img, 255), t)
    finally:
        ov.ov = keep
    # recover the text's coverage from the two composites (alpha = 1 - (white - black) / 255)
    m = 1.0 - (key.astype(np.float32) - lay.astype(np.float32)).mean(2) / 255.0
    m = np.clip(m, 0, 1)
    cap_px = float(o['cap']) * h
    off = max(2.0, 0.03 * cap_px) * 3.0
    out = fx.shadow_under(img, m, 0.02 * w, (off, off), 0.35)
    fg = np.where(m[..., None] > 0.004, lay.astype(np.float32) / np.maximum(m[..., None], 0.004), 0).clip(0, 255).astype(np.uint8)
    return fx.blend_mask(out, fg, m)


# ---------------------------------------------------------------- polaroids

def polaroids(ov, img, t):
    """
    f376-413: three polaroids 0.30-0.36 W wide, tilted ry +-25 / rx 8 deg (a real trapezoid), overlapping in
    depth, drawn back to front with a soft shadow, arriving 4-6 frames apart with a 0.5 x |dpos| smear.
    """
    o = ov.ov
    h, w = img.shape[:2]
    n = int(o.get('n', 3))
    c = ov._cache
    if 'cards' not in c:
        c['cards'] = [fx.polaroid_card(cv2.resize(s, (600, 600 * s.shape[0] // s.shape[1]))[:, 60:540], 0.05, 0.10)
                      for s in _stills(ov, n)]
    since = t - ov.t0
    fps = float(ov.eng.fps)
    out = img
    slots = o.get('slots') or ([(0.34, 0.27, -22.0, -4.0, 0.56), (0.64, 0.50, 10.0, 3.0, 0.52), (0.38, 0.74, 22.0, 5.0, 0.55)] if h > w else
                               [(0.22, 0.47, -25.0, -4.0, 0.34), (0.52, 0.52, 8.0, 3.0, 0.30), (0.80, 0.46, 25.0, 5.0, 0.33)])
    order = sorted(range(min(n, len(c['cards']))), key=lambda i: slots[i % len(slots)][4])
    for i in order:
        card = c['cards'][i]
        sx, sy, ry, rz, cwf = slots[i % len(slots)]
        age = since - i * float(o.get('stagger', 5 * F30))
        if age < 0:
            continue

        def pos(a):
            e = _ease_out(a / (7 * F30), 3)
            return (sx + (1.0 - e) * 0.9) * w, (sy - (1.0 - e) * 0.10) * h, e
        x, y, e = pos(age)
        x2, y2, _ = pos(age + 1.0 / fps)
        cw = cwf * float(o.get('size', 1.0)) * w * (1.0 + 0.02 * since)
        quad = fx.card_quad((w, h), x, y, cw, cw * card.shape[0] / card.shape[1], rx=float(o.get('rx', 8.0)),
                            ry=ry * (1.0 + 0.6 * (1 - e)), rz=rz, focal=1.0)
        lay, m = fx.warp_to_quad(card, quad, (w, h))
        d = math.hypot(x2 - x, y2 - y) * fps / 30.0
        if d > 4:
            ang = math.degrees(math.atan2(y2 - y, x2 - x))
            lay = fx.line_blur(lay, 0.5 * d, ang)
            m = fx.line_blur(cv2.cvtColor((m * 255).astype(np.uint8), cv2.COLOR_GRAY2BGR), 0.5 * d, ang)[..., 0].astype(np.float32) / 255.0
        out = fx.shadow_under(out, m, 0.02 * w, (0.008 * w, 0.012 * w), 0.35)
        out = fx.blend_mask(out, lay, m)
    return out


# ---------------------------------------------------------------- small ones

def fade_out(ov, img, t):
    """fx.json fade_out: gain 1 - i/9 over 10 frames @30, LINEAR in luma (a gain, not an alpha to a card)."""
    # evaluated per OUTPUT frame (20 steps @60 for 10 frames @30): strictly decreasing, no stair-step judder
    fps = float(getattr(ov.eng, 'fps', 60))
    n = max(2.0, float(ov.ov.get('frames', 10)) * fps / 30.0)
    i = int(round((t - ov.t0) * fps))
    return fx.gain_u8(img, min(1.0, max(0.0, 1.0 - i / (n - 1.0))))


_RB_MASK = {}


def radial_blur(ov, img, t):
    """
    fx.json sustained_radial_push (f204-292), round 3: the first version (amount 0.006-0.012) was invisible.
    The outer field smears 3 -> 8 % of the long side, ramping over the shot; a core of radius 0.18 x the long
    side stays SHARP (the subject is always readable); 16+ steps at half size.
    {"amount": 0.06, "amount_end": 0.16, "center": [x, y], "core": 0.18}
    """
    o = ov.ov
    h, w = img.shape[:2]
    a0 = float(o.get('amount', 0.06))
    a1 = float(o.get('amount_end', a0))
    u = min(1.0, max(0.0, (t - ov.t0) / max(1e-3, ov.t1 - ov.t0)))
    a = min(0.16, a0 + (a1 - a0) * u)
    if a < 0.01:
        return img
    c = o.get('center') or [0.5, 0.5]
    core = float(o.get('core', 0.18))
    key = (w, h, round(float(c[0]), 2), round(float(c[1]), 2), core)
    m = _RB_MASK.get(key)
    if m is None:
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        L = float(max(w, h))
        r = np.sqrt((xx - float(c[0]) * w) ** 2 + (yy - float(c[1]) * h) ** 2) / L
        m = np.clip((r - core) / max(1e-3, 0.5 - core), 0.0, 1.0)
        m = (m * m * (3 - 2 * m)).astype(np.float32)
        _RB_MASK.clear()
        _RB_MASK[key] = m
    blurred = fx.zoom_blur_c(img, a, steps=int(o.get('steps', 16)), center=(float(c[0]), float(c[1])), down=int(o.get('down', 2)))
    return fx.blend_mask(img, blurred, m)


def watermark_fx(ov, img, t):
    """fx.json watermark: bottom left at (0.02, 0.955), cap 2.4 %H, white at 0.55, never animated."""
    h, w = img.shape[:2]
    if ov._cache.get('wm') is None:
        ov._cache['wm'] = T.render(ov.ov.get('text', ''), 'lower', int(0.024 / CAP_RATIO * h), case='upper', font='poppins_medium')
    sp = ov._cache['wm']
    img = img.copy()
    T.blit(img, sp.img, 0.02 * w + sp.size[0] / 2 - sp.pad, 0.955 * h, alpha=float(ov.ov.get('opacity', 0.55)))
    return img


def set_exit(ov, img, t):
    """
    A built set REPLACES the picture, so the transition of the shot underneath it is never seen. These are the same
    measured joins applied to the finished set picture over its LAST frames (place this overlay after the set's):
      whip_white  f322-333: 10 growth frames x1.48 to 15 %W, smear 2 x the step, white to alpha 0.83
      dolly_white f400-413: geometric shrink x1.10 over 14 frames to ln s -0.46 onto white, white 0.82 k^2.2
    """
    kind = ov.ov.get('kind', 'whip_white')
    h, w = img.shape[:2]
    n = 10 if kind == 'whip_white' else 14
    k = int(min(n - 1, max(0, math.floor((n * F30 - (ov.t1 - t)) * 30.0 + 1e-6))))
    if ov.t1 - t > n * F30:
        return img
    if kind == 'whip_white':
        step = 0.150 * 1.48 ** (k - (n - 1))
        img = fx.aniso_blur(img, 2.0 * step * w, 0.0, 0.69 + 0.31 * k / (n - 1))
        return fx.cap_white(fx.fill_mix(img, (255, 255, 255), 0.83 * ((k + 1) / float(n)) ** 2.6))
    steps = np.array([1.10 ** j for j in range(n)], float)
    ln = -0.46 * float(steps[:k + 1].sum() / steps.sum())
    sc = math.exp(ln)
    M = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), 0.0, sc)
    img = cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=(245, 245, 245))
    u = (k + 1) / float(n)
    return fx.cap_white(fx.fill_mix(fx.gauss_px(img, 0.002 * w * u), (255, 255, 255), 0.82 * u ** 2.2))


def set_entry(ov, img, t):
    """The spin settle (f292-306) applied to a built set: lands rolled 57 deg at x1.68, both unwinding with tau 0.156 s."""
    v = t - ov.t0
    if v > 18 * F30:
        return img
    h, w = img.shape[:2]
    d = float(ov.ov.get('dir', 1.0))
    roll = d * 57.0 * math.exp(-v / 0.156)
    sc = math.exp(math.log(1.68) * math.exp(-v / 0.156))
    M = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), roll, sc)
    img = cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT101)
    # round 3: blur = 0.5 x the per-frame (@30) arc, gone by frame 8-9 (was 1.5 x and soft to frame 13)
    arc = roll - d * 57.0 * math.exp(-(v + F30) / 0.156)
    k = min(1.0, max(0.0, (8.0 - v * 30.0) / 3.0))
    return fx.rot_blur(img, 0.5 * arc * k)


HANDLERS = {'set_exit': set_exit, 'set_entry': set_entry, 'music_card3d': music_card3d, 'bokeh': bokeh, 'ink_blot': ink_blot, 'word3d': word3d, 'ground_text': ground_text,
            'glass_set': glass_set, 'white_set': white_set, 'letters_fly': letters_fly, 'closing_3d': closing_3d,
            'polaroids': polaroids, 'fade_out': fade_out, 'watermark_fx': watermark_fx, 'radial_blur': radial_blur}


# ================================================================ round 4 (critic pass 2): set pieces rebuilt
# The functions above stay reachable with {"legacy": true}; HANDLERS is rebound at the end of this block.

def _over(canvas, sprite, cx, cy):
    """Straight-alpha 'over' of a BGRA sprite onto a BGRA canvas, centred at (cx, cy); in place."""
    sh, sw = sprite.shape[:2]
    x0, y0 = int(round(cx - sw / 2)), int(round(cy - sh / 2))
    a0, b0 = max(0, x0), max(0, y0)
    a1, b1 = min(canvas.shape[1], x0 + sw), min(canvas.shape[0], y0 + sh)
    if a1 <= a0 or b1 <= b0:
        return canvas
    s = sprite[b0 - y0:b1 - y0, a0 - x0:a1 - x0].astype(np.float32)
    d = canvas[b0:b1, a0:a1].astype(np.float32)
    sa, da = s[..., 3:4] / 255.0, d[..., 3:4] / 255.0
    oa = sa + da * (1 - sa)
    rgb = (s[..., :3] * sa + d[..., :3] * da * (1 - sa)) / np.maximum(oa, 1e-4)
    canvas[b0:b1, a0:a1, :3] = np.clip(rgb, 0, 255).astype(np.uint8)
    canvas[b0:b1, a0:a1, 3:4] = np.clip(oa * 255.0, 0, 255).astype(np.uint8)
    return canvas


def _face_box(ov, t, w, h):
    """The subject's box (x0, y0, x1, y1) in px around the shot focus - text must keep IoU 0 with it."""
    sub = _subject(ov, t) or (0.5, 0.42)
    return ((sub[0] - 0.15) * w, (sub[1] - 0.20) * h, (sub[0] + 0.15) * w, (sub[1] + 0.22) * h)


def _overlap(a, b):
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def _chunky_letters(text, cap_px, font='lilita', face=(232, 210, 168), side=(154, 122, 74), rim=(255, 244, 222),
                    depth=0.28, direction=(0.75, -0.55), case='upper'):
    """Per-letter extruded sprites with 3-tone shading: lit face (top-to-bottom gradient), darker wall, rim light."""
    size = max(12, int(cap_px / CAP_RATIO))
    n = max(3, int(depth * cap_px))
    out = []
    for ch in text:
        if ch == ' ':
            out.append(None)
            continue
        sp = T.render(ch, 'closing_3d', size, font=font, fill=tuple(face), case=case,
                      extrude=(n, tuple(direction), tuple(side)), bevel=(max(1.0, 0.012 * cap_px), tuple(rim), 0.9),
                      shadow=False, glow=[])
        im = sp.img.copy()
        # the wall gets darker away from the face, the face brighter toward the top (one key light, top-left)
        g = np.linspace(1.10, 0.80, im.shape[0], dtype=np.float32)[:, None, None]
        im[..., :3] = np.clip(im[..., :3].astype(np.float32) * g, 0, 255).astype(np.uint8)
        out.append((im, sp.size[0] - 2 * sp.pad))
    return out, size


# ---------------------------------------------------------------- closing 3D words (f468-495)

def closing_3d_v2(ov, img, t):
    """
    Chunky extruded letters standing IN the scene: depth 0.28 x cap, face #E8D2A8 / wall #9A7A4A / rim light,
    the word plane yawed ry 20 / rx 10 deg, anchored in the negative space (IoU 0 with the subject box), a cast
    shadow skewed along the light (sigma 1.2 %W, alpha 0.45), each letter tumbling 60 -> 0 deg over 8 frames @30.
    """
    o = ov.ov
    if o.get('legacy'):
        return closing_3d(ov, img, t)
    h, w = img.shape[:2]
    c = ov._cache
    lines = [ln.strip() for ln in str(o.get('text', '')).split('/') if ln.strip()]
    if not lines:
        return img
    vertical = h > w
    if 'ltr' not in c:
        max_w = float(o.get('max_w', 0.80 if vertical else 0.46)) * w
        cap = float(o.get('cap', 0.20 if not vertical else 0.085)) * h
        fb = _face_box(ov, ov.t0, w, h)
        while True:
            # the biggest size (down to 40 % of the asked cap) at which SOME corner keeps IoU 0 with the subject
            rows = [_chunky_letters(ln, cap, font=o.get('font', 'lilita'))[0] for ln in lines]
            widths = [sum((0.35 * cap if L is None else L[1]) for L in r) for r in rows]
            bw, bh = max(widths) * 1.15, cap * 1.35 * len(lines) + 0.3 * cap
            cands = [(0.05 * w, 0.93 * h - bh), (0.95 * w - bw, 0.93 * h - bh), (0.05 * w, 0.09 * h), (0.95 * w - bw, 0.09 * h)]
            if 'x' in o and 'y' in o:
                cands = [(float(o['x']) * w - bw / 2, float(o['y']) * h - bh / 2)]
            best = min(cands, key=lambda p: _overlap((p[0], p[1], p[0] + bw, p[1] + bh), fb))
            clear = _overlap((best[0], best[1], best[0] + bw, best[1] + bh), fb) <= 0.0
            if (max(widths) <= max_w and clear) or cap < 0.045 * h:
                break
            cap *= 0.88
        c['ltr'], c['cap'], c['widths'] = rows, cap, widths
        c['box'] = (best[0], best[1], bw, bh)
        c['right'] = best[0] + bw / 2 > 0.5 * w
    rows, cap, widths = c['ltr'], c['cap'], c['widths']
    bx, by, bw, bh = c['box']
    since = (t - ov.t0) * 30.0
    stag = float(o.get('stagger_f', 1.5))
    tum_f = float(o.get('tumble_f', 8.0))
    n_total = sum(len(r) for r in rows)
    done = since >= n_total * stag + tum_f + 1
    if done and 'flat' in c:
        flat = c['flat']
    else:
        pad = int(cap * 0.8)
        flat = np.zeros((int(bh) + 2 * pad, int(bw) + 2 * pad, 4), np.uint8)
        k = 0
        for li, r in enumerate(rows):
            x = pad + (float(o.get('line_shift', 0.10)) * bw if li % 2 else 0.0)
            ybase = pad + cap * 0.75 + li * cap * 1.32
            for L in r:
                if L is None:
                    x += 0.35 * cap
                    k += 1
                    continue
                im, adv = L
                age = since - k * stag
                k += 1
                if age < 0:
                    x += adv
                    continue
                e = _ease_out(age / tum_f, 3)
                ang = 60.0 * (1 - e) * (1 if k % 2 else -1)
                sq = max(0.12, math.cos(math.radians(75.0 * (1 - e))))      # the tumble seen edge-on at first
                spr = T.transform(im, 1.0, ang, sq, 1.0) if e < 0.999 else im
                drop = (1 - e) ** 2 * cap * 1.4
                if e < 0.85:
                    spr = T.motion_blur(spr, 0.5 * drop * 0.35, 90.0, 121)
                a = min(1.0, age / 2.0)
                if a < 1:
                    spr = spr.copy()
                    spr[..., 3] = (spr[..., 3] * a).astype(np.uint8)
                _over(flat, spr, x + adv / 2, ybase - drop)
                x += adv
        if done:
            c['flat'] = flat
    ry = float(o.get('ry', 20.0)) * (-1.0 if c['right'] else 1.0)
    fw, fh = flat.shape[1], flat.shape[0]
    quad = fx.card_quad((w, h), bx + bw / 2, by + bh / 2, fw, fh, rx=float(o.get('rx', 10.0)), ry=ry,
                        rz=float(o.get('roll', -2.0)), focal=1.4)
    src = np.array([[0, 0], [fw, 0], [fw, fh], [0, fh]], np.float32)
    lay = cv2.warpPerspective(flat, cv2.getPerspectiveTransform(src, quad), (w, h), flags=cv2.INTER_LINEAR,
                              borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
    m = lay[..., 3].astype(np.float32) / 255.0
    if float(m.max()) < 0.01:
        return img
    # cast shadow: the coverage skewed away from the key light and flattened onto the surface
    sk = np.float32([[1, -0.55, 0.55 * (by + bh) + 0.010 * w], [0, 0.45, 0.55 * (by + bh) + 0.012 * w]])
    sh = cv2.warpAffine(m, sk, (w, h))
    out = fx.shadow_under(img, np.maximum(sh, m * 0.6), 0.012 * w, (0.006 * w, 0.006 * w), 0.45)
    return fx.blend_mask(out, np.ascontiguousarray(lay[..., :3]), m)


# ---------------------------------------------------------------- ground text (f216-238)

def detect_ground(img):
    """
    Horizon row (fraction of H) of a WIDE plate with a visible ground region, or None. A plate qualifies when one
    horizontal luma edge between 35 and 78 % of the height is consistent over >= 5 of 8 column bands and the
    region under it is calmer than a face / medium shot would be. Medium shots and close-ups return None.
    """
    g = cv2.cvtColor(cv2.resize(img, (160, 90), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY).astype(np.float32)
    g = cv2.GaussianBlur(g, (0, 0), 1.5)
    rows = range(int(0.35 * 90), int(0.78 * 90))
    best, by = 0.0, None
    for y in rows:
        up, dn = g[max(0, y - 6):y].mean(0), g[y:y + 6].mean(0)
        d = (up - dn).reshape(8, 20).mean(1)
        agree = max(int((d > 7).sum()), int((d < -7).sum()))
        score = agree * float(np.abs(d).mean())
        if agree >= 5 and score > best:
            best, by = score, y
    if by is None:
        return None
    below = g[by + 3:]
    if below.size == 0 or float(np.abs(np.diff(below, axis=1)).mean()) > 9.0:
        return None
    return by / 90.0


def ground_text_v2(ov, img, t):
    """
    The word STANDS on the plate's ground: upright chunky billboards (one per letter, extruded 6-10 px @1920,
    staggered +-5 % in depth), just under the horizon, growing as the camera pushes over them (3 %/frame @30
    in the last half), carrying the plate's radial smear, and leaving under the lens in 4 frames with a
    8 -> 25 %W smear. Refuses (returns the plate untouched + a warning) when the plate has no ground region.
    """
    o = ov.ov
    if o.get('legacy'):
        return ground_text(ov, img, t)
    h, w = img.shape[:2]
    c = ov._cache
    if 'hz' not in c:
        hz = o.get('horizon')
        c['hz'] = float(hz) if hz is not None else detect_ground(img)
        if c['hz'] is None:
            try:
                ov.eng.warnings.append('ground_text refused: no ground region in the plate at %.2f s' % t)
            except Exception:
                pass
    hz = c['hz']
    if hz is None:
        return img
    if 'ltr' not in c:
        cap0 = float(o.get('cap', 0.085)) * h * 1.8                    # rendered big, blitted down
        plate_l = float(cv2.resize(img, (32, 18), interpolation=cv2.INTER_AREA)[int(18 * hz):].mean())
        tone = min(1.0, max(0.70, plate_l / 255.0 + 0.55))
        face = tuple(int(v * tone) for v in (246, 244, 240))
        side = tuple(int(v * tone * 0.55) for v in (225, 222, 230))
        ltr, _ = _chunky_letters(str(o.get('text', '')), cap0, font=o.get('font', 'poppins_extrabold'), face=face, side=side,
                                 rim=(255, 255, 255), depth=float(o.get('depth', 0.10)), direction=(0.7, -0.7),
                                 case=o.get('case', 'lower'))
        rng = np.random.default_rng(int(o.get('seed', 3)))
        c['ltr'] = ltr
        c['z'] = [float(rng.uniform(-0.05, 0.05)) for _ in ltr]
        c['rot'] = [float(rng.uniform(-5, 5)) for _ in ltr]
        c['total'] = sum((0.3 * cap0 if L is None else L[1] * 1.04) for L in ltr)
        c['cap0'] = cap0
    exit_f = float(o.get('exit_f', 4))
    k_all = (t - ov.t0) * 30.0
    k_exit = (t - (ov.t1 - exit_f * F30)) * 30.0
    n_hold = max(1.0, (ov.t1 - ov.t0) * 30.0 - exit_f)
    # push: 0.6 %/frame resting, 3 %/frame over the last 8 frames before the exit, then x1.55 per frame
    ln_s = 0.006 * min(k_all, n_hold) + 0.024 * max(0.0, min(k_all, n_hold) - (n_hold - 8))
    if k_exit > 0:
        ln_s += math.log(1.55) * k_exit
    s = math.exp(ln_s)
    if 'vx' not in c:
        vx = 0.5
        sub = _subject(ov, ov.t0)
        half = float(o.get('width', 0.40)) / 2 + 0.06
        if sub is not None and abs(sub[0] - vx) < half:                 # never over the subject: slide to the free side
            vx = sub[0] - half if sub[0] > 0.5 else sub[0] + half
            vx = min(1.0 - half + 0.04, max(half - 0.04, vx))
        c['vx'] = float(o.get('x', vx))
    vx = c['vx']
    y0 = hz + float(o.get('drop', 0.20))                                # where the letters' feet rest
    base_w = float(o.get('width', 0.40)) * w
    sc0 = base_w / max(1.0, c['total'])
    out = img.copy()
    pb = float(o.get('plate_blur', 0.05))
    fps = float(ov.eng.fps)
    x = -c['total'] / 2
    items = []
    for L, z, rot in zip(c['ltr'], c['z'], c['rot']):
        if L is None:
            x += 0.3 * c['cap0']
            continue
        im, adv = L
        adv *= 1.04
        si = s * (1.0 + z * 2.0)                                       # nearer letters are bigger AND lower
        px = (vx * w) + (x + adv / 2) * sc0 * si
        feet = (hz + (y0 - hz) * si) * h
        items.append((si, im, px, feet, rot))
        x += adv
    for si, im, px, feet, rot in sorted(items, key=lambda r: r[0]):
        sc = sc0 * si
        hh = im.shape[0] * sc
        cy = feet - hh * 0.5 + hh * 0.18                                # the sprite's pad sits under the feet
        if cy - hh / 2 > h:
            continue
        # contact shadow on the ground under the letter
        ew, eh = int(im.shape[1] * sc * 0.42), max(2, int(hh * 0.05))
        if 0 < feet < h + eh:
            shd = np.zeros((h, w), np.float32)
            cv2.ellipse(shd, (int(px + 0.1 * ew), int(feet)), (max(2, ew), eh), 0, 0, 360, 1.0, -1, cv2.LINE_AA)
            out = fx.shadow_under(out, shd, 0.006 * w * si, (0, 0), 0.5)
        dx, dy = px - vx * w, cy - hz * h
        dist = math.hypot(dx, dy)
        blur = pb * dist * (1.0 + 0.0 * si)
        if k_exit > 0:
            blur = max(blur, (0.08 + 0.17 * min(1.0, k_exit / exit_f)) * w * 0.5)
        elif k_all >= n_hold - 8:
            blur = max(blur, 0.5 * 0.024 * dist * 30.0 / fps * 2.0)
        ang = math.degrees(math.atan2(dy, dx)) if dist > 1 else 90.0
        T.blit(out, im, px, cy, alpha=1.0, scale=sc, rot=rot * 0.4, blur_len=blur if blur >= 2.0 else 0.0,
               blur_ang=ang, blur_max=401)
    return out


# ---------------------------------------------------------------- ink blot (f104-121)

def _ink_field(size, seed):
    w, h = size
    sw, sh = max(32, w // 6), max(18, h // 6)
    rng = np.random.default_rng(seed)
    acc = np.zeros((sh, sw), np.float32)
    for div, amp in ((5, 1.0), (11, 0.55), (26, 0.32)):                 # 3 octaves
        n = rng.random((div + 2, int(div * sw / sh) + 2)).astype(np.float32)
        acc += cv2.resize(n, (sw, sh), interpolation=cv2.INTER_CUBIC) * amp
    acc = (acc - acc.mean()) / max(1e-6, float(acc.std()))
    return acc


def ink_blot_v2(ov, img, t):
    """
    An organic splash growing from a POINT near the subject: radius ~ sqrt(t), edge displaced 4-8 %W by 3-octave
    noise, a secondary blot 10 frames later, interior = 0.35 x the luma-inverted plate over black (never a flat
    fill). The mark is tiny lower-case type (3.5 %H) typed at 13 cps by the subject's collar, then blurs out.
    """
    o = ov.ov
    if o.get('legacy'):
        return ink_blot(ov, img, t)
    h, w = img.shape[:2]
    c = ov._cache
    if 'nz' not in c:
        c['nz'] = _ink_field((w, h), int(o.get('seed', 3)))
        sub = _subject(ov, ov.t0) or (0.5, 0.42)
        sx = sub[0] + (0.22 if sub[0] < 0.5 else -0.22)
        c['seeds'] = [(float(o.get('x', sx)), float(o.get('y', sub[1] - 0.12)), 0.0, 1.0),
                      (float(o.get('x2', sx + (0.16 if sx < 0.5 else -0.16))), float(o.get('y2', sub[1] + 0.22)), 10.0, 0.55)]
        c['sub'] = sub
        sh_, sw_ = c['nz'].shape
        yy, xx = np.mgrid[0:sh_, 0:sw_].astype(np.float32)
        c['grid'] = (xx / sw_, yy / sh_ * h / w)
    f = (t - ov.t0) * 30.0
    grow_f = float(o.get('grow_f', 15))
    cov = float(o.get('coverage', 0.35))
    Rmax = math.sqrt(cov * h / w / math.pi)                             # in units of W
    xx, yy = c['grid']
    m = np.zeros_like(c['nz'])
    for sx, sy, delay, amp in c['seeds']:
        u = min(1.0, max(0.0, (f - delay) / grow_f))
        if u <= 0:
            continue
        R = Rmax * amp * math.sqrt(u)
        d = np.sqrt((xx - sx) ** 2 + (yy - sy * h / w) ** 2)
        edge = R * (1.0 + 0.0 * d) + c['nz'] * float(o.get('rough', 0.06)) * min(1.0, 3.0 * u)
        m = np.maximum(m, np.clip((edge - d) / 0.012 + 0.5, 0, 1))
    end_f = (ov.t1 - t) * 30.0
    if end_f < 6:
        m = m * max(0.0, end_f / 6.0)
    m = cv2.GaussianBlur(cv2.resize(m, (w, h), interpolation=cv2.INTER_CUBIC), (0, 0), max(1.0, 0.0015 * w)).clip(0, 1)
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    inv = cv2.merge([255 - g] * 3).astype(np.float32) * 0.35
    out = img.astype(np.float32) * (1 - m[..., None]) + inv * m[..., None]
    out = np.clip(out, 0, 255).astype(np.uint8)
    if o.get('text'):
        if 'mark' not in c:
            c['mark'] = T.render(str(o['text']), 'lower', int(float(o.get('size', 0.035)) / CAP_RATIO * h), case='lower',
                                 font='poppins_medium', tracking=0.04, max_w=int(0.5 * w))
        sp = c['mark']
        sub = c['sub']
        pos = max(0.0, (t - ov.t0) - float(o.get('type_at', 0.2))) * float(o.get('cps', 13.0))
        n = int(pos)
        if pos > 0:
            spr = sp.crop_letters(n, min(1.0, (pos - n) / (13.0 * 3 * F30)))
            bl = 0.006 * w * max(0.0, 1.0 - end_f / 6.0) if end_f < 6 else 0.0
            if bl > 0.3:
                spr = cv2.GaussianBlur(spr, (0, 0), bl)
            T.blit(out, spr, float(o.get('mark_x', sub[0])) * w, float(o.get('mark_y', min(0.9, sub[1] + 0.20))) * h,
                   alpha=min(1.0, end_f / 6.0) if end_f < 6 else 1.0)
    return out


# ---------------------------------------------------------------- bokeh field + music card (f0-87)

_BOKEH_SPR = {}


def _disc(r, soft):
    key = (int(r), round(soft, 1))
    d = _BOKEH_SPR.get(key)
    if d is None:
        n = int(r * 2.6) | 1
        yy, xx = np.mgrid[0:n, 0:n].astype(np.float32)
        rr = np.sqrt((xx - n // 2) ** 2 + (yy - n // 2) ** 2)
        d = np.clip((r - rr) / max(1.0, soft * r), 0, 1)
        d = d * (0.75 + 0.25 * np.clip(rr / max(1.0, r), 0, 1))       # a slightly brighter rim, like a real lens disc
        if len(_BOKEH_SPR) > 200:
            _BOKEH_SPR.clear()
        _BOKEH_SPR[key] = d.astype(np.float32)
    return d


def bokeh_field_v2(size, t, n=42, seed=9, down=2):
    """
    Large SOFT discs: radius log-uniform 6-110 px @1920, softness 30-60 % of the radius, 70 % blue / violet,
    additive, drifting with parallax 0.3-1.2 %W/s by depth, plus one horizontal anamorphic flare top-left
    (length 25 %W, alpha 0.5, slow flicker). Float layer at full size.
    """
    w, h = size[0] // down, size[1] // down
    rng = np.random.default_rng(seed)
    out = np.zeros((h, w, 3), np.float32)
    cols = ((1.0, 0.35, 0.25), (1.0, 0.45, 0.55), (0.95, 0.55, 0.35), (1.0, 0.85, 0.75), (0.9, 0.9, 1.0), (0.75, 0.95, 1.0))
    pk = (0.27, 0.22, 0.21, 0.12, 0.10, 0.08)
    for _ in range(int(n)):
        z = float(rng.uniform(0, 1))
        r = math.exp(rng.uniform(math.log(6.0), math.log(110.0))) * size[0] / 1920.0 / down
        x = ((float(rng.uniform(-0.1, 1.1)) + (0.003 + 0.009 * z) * t * (1 if rng.random() < 0.5 else -1)) % 1.2 - 0.1) * w
        y = ((float(rng.uniform(-0.1, 1.1)) + 0.004 * z * t) % 1.2 - 0.1) * h
        col = cols[int(rng.choice(len(cols), p=pk))]
        a = float(rng.uniform(0.28, 0.80)) * (0.55 if r > 40 * size[0] / 1920.0 / down else 1.0)
        d = _disc(max(2.0, r), float(rng.uniform(0.3, 0.6)))
        n_ = d.shape[0]
        x0, y0 = int(x) - n_ // 2, int(y) - n_ // 2
        a0, b0, a1, b1 = max(0, x0), max(0, y0), min(w, x0 + n_), min(h, y0 + n_)
        if a1 <= a0 or b1 <= b0:
            continue
        out[b0:b1, a0:a1] += d[b0 - y0:b1 - y0, a0 - x0:a1 - x0, None] * (np.array(col, np.float32) * a)
    fl = np.zeros((h, w), np.float32)
    fy, fx_ = int(0.13 * h), int(0.20 * w)
    cv2.ellipse(fl, (fx_, fy), (int(0.125 * w), max(1, int(0.004 * h))), 0, 0, 360, 1.0, -1, cv2.LINE_AA)
    cv2.circle(fl, (fx_, fy), max(2, int(0.012 * w)), 1.0, -1, cv2.LINE_AA)
    fl = cv2.GaussianBlur(fl, (0, 0), max(1.0, 0.006 * w)) + 0.6 * cv2.GaussianBlur(fl, (0, 0), max(1.0, 0.03 * w))
    out += fl[..., None] * np.array([1.0, 0.85, 0.6], np.float32) * 0.5 * (0.8 + 0.2 * math.sin(t * 2.3))
    out = out + 0.5 * cv2.GaussianBlur(out, (0, 0), max(1.0, 0.01 * w))           # bloom
    return cv2.resize(out, size, interpolation=cv2.INTER_LINEAR) if down > 1 else out


def bokeh_v2(ov, img, t):
    o = ov.ov
    if o.get('legacy'):
        return bokeh(ov, img, t)
    dim = float(o.get('dim', 1.0))
    base = fx.gain_u8(img, 1.0 - dim) if dim < 1.0 else np.zeros_like(img)
    lay = bokeh_field_v2((img.shape[1], img.shape[0]), t - ov.t0, int(o.get('n', 42)), int(o.get('seed', 9)))
    return fx.screen_layer(base, lay * float(o.get('opacity', 1.0)), 1.0)


# keys read off WGvtliE1298 f0-87: (frame @30, card width / W, yaw deg, x offset in card widths, smear %W, gain)
CARD_KEYS = ((0, 1.30, 32.0, 0.10, 6.0, 0.0), (3, 1.05, 28.0, 0.08, 4.0, 0.45), (6, 0.80, 21.0, 0.03, 1.0, 0.9),
             (8, 0.74, 19.0, 0.02, 0.0, 1.0), (28, 0.70, 17.0, 0.0, 0.0, 1.0), (36, 1.15, 13.0, -0.12, 1.5, 1.0),
             (44, 1.75, 9.0, -0.22, 3.0, 1.0), (52, 1.80, 7.0, -0.30, 0.8, 1.0), (72, 1.62, 6.0, -0.40, 0.0, 1.0),
             (78, 1.10, 9.0, -0.20, 1.5, 1.0), (84, 0.55, 12.0, 0.0, 0.5, 0.9), (88, 0.40, 14.0, 0.0, 0.5, 0.0))


def _key(keys, f):
    if f <= keys[0][0]:
        return keys[0][1:]
    for a, b in zip(keys, keys[1:]):
        if f <= b[0]:
            u = (f - a[0]) / max(1e-6, b[0] - a[0])
            u = u * u * (3 - 2 * u)
            return tuple(a[i] * (1 - u) + b[i] * u if i != 1 else a[1] * (b[1] / a[1]) ** u for i in range(1, 6))
    return keys[-1][1:]


def _card_face_v2(ov, since):
    o, c = ov.ov, ov._cache
    CW, CH = 1200, 520
    if 'face0' not in c:
        face = _rounded((CW, CH), 46, (0, 0, 0))
        xx = np.linspace(0, 1, CW, dtype=np.float32)[None, :, None]
        yy = np.linspace(0, 1, CH, dtype=np.float32)[:, None, None]
        c0 = np.array(o.get('color_a', (232, 168, 47)), np.float32)      # BGR of #2FA8E8
        c1 = np.array(o.get('color_b', (240, 214, 73)), np.float32)      # BGR of #49D6F0
        u = np.clip(0.55 * xx + 0.45 * yy, 0, 1)
        face[..., :3] = (c0 * (1 - u) + c1 * u).astype(np.uint8)
        st = _stills(ov, 1)
        bgr = np.ascontiguousarray(face[..., :3])
        if st:
            s0 = st[0]
            sq = s0[:, (s0.shape[1] - s0.shape[0]) // 2:(s0.shape[1] + s0.shape[0]) // 2] if s0.shape[1] > s0.shape[0] else s0
            bgr[40:190, 46:196] = cv2.resize(sq, (150, 150), interpolation=cv2.INTER_AREA)
            cv2.rectangle(bgr, (46, 40), (196, 190), (250, 250, 250), 3)
        ti = T.render(o.get('title', ''), 'lower', 56, case='none', font='poppins_semibold')
        ar = T.render(o.get('artist', ''), 'lower', 38, case='none', opacity=0.7)
        T.blit(bgr, ti.img, 226 + ti.size[0] / 2 - ti.pad, 86)
        T.blit(bgr, ar.img, 226 + ar.size[0] / 2 - ar.pad, 146)
        cv2.line(bgr, (560, 440), (1100, 440), (255, 250, 235), 2, cv2.LINE_AA)
        face[..., :3] = bgr
        c['face0'] = face
        c['ly'] = T.render(o.get('text', ''), 'lower', 54, case='none', font='poppins_semibold', tracking=0.05)
    cps = float(o.get('cps', 13.2))
    pos = max(0.0, since - float(o.get('type_at', 0.35))) * cps
    key = round(pos, 2)
    if c.get('fkey') == key:
        return c['face']
    face = c['face0'].copy()
    bgr = np.ascontiguousarray(face[..., :3])
    ly = c['ly']
    em = 54.0
    x = 92.0
    rng = np.random.default_rng(4)
    hs = rng.uniform(3, 22, 40)
    for kx in range(min(40, int(since * 9))):                            # the waveform draws itself with the song
        cv2.line(bgr, (566 + kx * 9, int(440 - hs[kx])), (566 + kx * 9, int(440 + hs[kx])), (255, 252, 244), 3, cv2.LINE_AA)
    for i, (ch, x0, x1) in enumerate(ly.letters):
        age_f = (pos - i) / cps * 30.0                                   # frames @30 since this letter was typed
        if age_f <= 0:
            break
        a = min(1.0, age_f / 3.0)                                        # 3-frame per-character fade
        x += 0.55 * em * max(0.0, 1.0 - age_f / 4.0)                     # tracking 0.6 em -> 0.05 em over 4 frames
        if ch != ' ':
            spr, _ = ly.letter(i)
            T.blit(bgr, spr, x + spr.shape[1] / 2 - ly.pad * 0.5, 300, alpha=a)
        x += (x1 - x0) + 0.05 * em
    face[..., :3] = bgr
    c['head_x'] = x
    c['fkey'], c['face'] = key, face
    return face


def music_card3d_v2(ov, img, t):
    """
    The now-playing card flown by KEYS read off the reference (CARD_KEYS): it arrives LARGE and smeared out of
    black, swoops back to 0.74 W by frame 8, drifts, pushes through to 1.75 W following the typing head with a
    3 %W smear, holds while the lyric types, and pulls back out defocused. Glass rim (a second translucent slab
    with the blurred backdrop), cyan gradient #2FA8E8 -> #49D6F0, tracking animating in on the newest letters.
    """
    o = ov.ov
    if o.get('legacy'):
        return music_card3d(ov, img, t)
    h, w = img.shape[:2]
    since = t - ov.t0
    total = float(o.get('dur', 88 * F30))
    f = since / max(1e-3, total) * 88.0
    cwf, ry, xoff, smear, gain = _key(CARD_KEYS, f)
    if h > w:
        cwf *= 1.45                                                      # 9:16: the card is read across the narrow frame
    base = bokeh_v2(ov, img, t)
    base = fx.gain_u8(base, min(1.0, f / 8.0))
    face = _card_face_v2(ov, since)
    cw = cwf * w
    ch = cw * face.shape[0] / face.shape[1]
    fol = min(1.0, max(0.0, -xoff / 0.22))                              # the camera follows the typing head
    head = float(ov._cache.get('head_x', 600.0)) / face.shape[1] - 0.5
    cx = (1 - fol) * (0.5 * w + xoff * cw) + fol * (0.60 * w - head * cw)
    cy = 0.5 * h - fol * 0.06 * ch
    rx, rz = 5.0, -4.0 * ry / 20.0
    quad = fx.card_quad((w, h), cx, cy, cw, ch, rx=rx, ry=-ry, rz=rz)
    q2 = fx.card_quad((w, h), cx - 0.045 * cw, cy - 0.02 * ch, cw * 1.10, ch * 1.16, rx=rx, ry=-ry, rz=rz)
    slab = np.zeros((h, w), np.uint8)
    cv2.fillConvexPoly(slab, np.round(q2 * 16).astype(np.int32), 255, cv2.LINE_AA, shift=4)
    slab = cv2.GaussianBlur(slab, (0, 0), max(1.0, 0.004 * w)).astype(np.float32) / 255.0
    glass = cv2.addWeighted(fx.gauss_px(base, 0.01 * w), 0.6, np.full_like(base, (120, 96, 40)), 0.4, 0)
    out = fx.blend_mask(base, glass, slab * 0.55)
    src = np.array([[0, 0], [face.shape[1], 0], [face.shape[1], face.shape[0]], [0, face.shape[0]]], np.float32)
    lay = cv2.warpPerspective(face, cv2.getPerspectiveTransform(src, quad.astype(np.float32)), (w, h), flags=cv2.INTER_LINEAR,
                              borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
    m = lay[..., 3].astype(np.float32) / 255.0
    out = fx.shadow_under(out, m, 0.02 * w, (0.01 * w, 0.014 * w), 0.45)
    out = fx.blend_mask(out, np.ascontiguousarray(lay[..., :3]), m)
    if smear >= 0.3:
        out = fx.line_blur(out, smear / 100.0 * w, -8.0)
    if f > 76:
        out = fx.gauss_px(out, 0.004 * w * min(1.0, (f - 76) / 8.0))
    return fx.gain_u8(out, gain) if gain < 0.999 else out


# ---------------------------------------------------------------- glass set: irregular fracture (f292-330)

def fracture_layer(size, cx, cy, progress, seed=0, arms=11, shards=32):
    """
    Irregular glass fracture (NOT a cobweb): random-walk radial cracks that branch (p 0.25, +-20-40 deg), no
    concentric rings - at most 2 broken arcs - line alpha 0.15-0.6, plus 25-40 filled triangular shards
    (alpha 0.2-0.8, blur 0-6 px) catching the light. Float layer at full size, meant to be screened.
    """
    w, h = size
    rng = np.random.default_rng(seed)
    out = np.zeros((h, w), np.float32)
    R = 0.95 * max(w, h) * min(1.0, max(0.0, progress))

    def walk(x, y, a, length, alpha, depth=0):
        n = max(3, int(length / (0.03 * w)))
        pts = [(x, y)]
        for i in range(n):
            a += float(rng.uniform(-0.22, 0.22))
            x, y = x + math.cos(a) * length / n, y + math.sin(a) * length / n
            pts.append((x, y))
            if depth < 2 and rng.random() < 0.25 * (0.5 if depth else 1.0):
                walk(x, y, a + math.radians(float(rng.uniform(20, 40))) * (1 if rng.random() < 0.5 else -1),
                     length * (1 - i / n) * float(rng.uniform(0.35, 0.7)), alpha * 0.7, depth + 1)
        cv2.polylines(out, [np.round(np.array(pts, np.float32) * 16).astype(np.int32)], False, float(alpha), max(1, int(round(w / 900.0))), cv2.LINE_AA, shift=4)
        return pts

    for k in range(int(arms)):
        a = k * 2 * math.pi / arms + float(rng.uniform(-0.3, 0.3))
        walk(cx, cy, a, R * float(rng.uniform(0.35, 1.0)), float(rng.uniform(0.15, 0.6)))
    for _ in range(2):                                                  # at most two BROKEN arcs
        rr = R * float(rng.uniform(0.15, 0.4))
        a0 = float(rng.uniform(0, 360))
        cv2.ellipse(out, (int(cx), int(cy)), (int(rr), int(rr * 0.9)), 0, a0, a0 + float(rng.uniform(25, 70)),
                    float(rng.uniform(0.15, 0.4)), 1, cv2.LINE_AA)
    lay = np.zeros((h, w), np.float32)
    for _ in range(int(shards * min(1.0, progress * 1.5))):
        a = float(rng.uniform(0, 2 * math.pi))
        d = R * float(rng.uniform(0.08, 0.8))
        px, py = cx + math.cos(a) * d, cy + math.sin(a) * d * 0.8
        sz = float(rng.uniform(0.012, 0.06)) * w
        tri = np.array([(px + math.cos(a + q) * sz * float(rng.uniform(0.4, 1.0)),
                         py + math.sin(a + q) * sz * float(rng.uniform(0.4, 1.0))) for q in (0.0, 2.2, 4.1)], np.float32)
        one = np.zeros((h, w), np.float32)
        cv2.fillConvexPoly(one, np.round(tri * 16).astype(np.int32), float(rng.uniform(0.2, 0.8)), cv2.LINE_AA, shift=4)
        x0, y0 = int(max(0, tri[:, 0].min() - 20)), int(max(0, tri[:, 1].min() - 20))
        x1, y1 = int(min(w, tri[:, 0].max() + 20)), int(min(h, tri[:, 1].max() + 20))
        if x1 <= x0 or y1 <= y0:
            continue
        bl = float(rng.uniform(0, 6)) * w / 1920.0
        roi = one[y0:y1, x0:x1]
        if bl > 0.6:
            roi = cv2.GaussianBlur(roi, (0, 0), bl)
        # a shard is a gradient, not a flat patch: bright toward one corner
        gy = np.linspace(1.0, 0.25, roi.shape[0], dtype=np.float32)[:, None]
        lay[y0:y1, x0:x1] = np.maximum(lay[y0:y1, x0:x1], roi * gy)
    lines = out * 1.6 + cv2.GaussianBlur(out, (0, 0), 2.5) * 1.4
    g = np.clip(lines + lay * 0.9, 0, 1.8)
    return np.repeat(g[..., None], 3, 2) * np.array([1.0, 0.97, 0.94], np.float32)


def glass_set_v2(ov, img, t):
    """
    The cracked-glass memory space: smoky GREY backdrop (not blue-black noise), a perspective filmstrip that
    arrives with a 6 %W -> 0 smear over 8 frames and is out of focus toward its ends (sigma up to 0.4 %W), an
    irregular fracture layer with bright shard sprites, and the hero polaroid (0.18 W) at the centre.
    """
    o = ov.ov
    if o.get('legacy'):
        return glass_set(ov, img, t)
    h, w = img.shape[:2]
    since = t - ov.t0
    c = ov._cache
    if 'haze' not in c:
        rng = np.random.default_rng(int(o.get('seed', 7)))
        acc = np.zeros((h, w), np.float32)
        for div, amp in ((3, 1.0), (7, 0.6), (16, 0.3)):
            n = rng.random((div + 2, int(div * w / h) + 2)).astype(np.float32)
            acc += cv2.resize(n, (w, h), interpolation=cv2.INTER_CUBIC) * amp
        acc = (acc - acc.min()) / max(1e-6, float(np.ptp(acc)))
        c['haze'] = np.clip((18 + 62 * acc ** 1.5)[..., None] * np.array([1.0, 0.98, 0.97], np.float32), 0, 255).astype(np.uint8)
        stl = _stills(ov, int(o.get('thumbs', 6)) + 1)
        c['strip'] = _strip_texture(stl[1:] or stl)
        c['hero'] = fx.polaroid_card(cv2.resize(stl[0], (480, 480 * stl[0].shape[0] // stl[0].shape[1]))[:, 60:420], 0.06, 0.14) if stl else None
        c['fr'] = fracture_layer((w, h), float(o.get('x', 0.5)) * w, float(o.get('y', 0.5)) * h, 1.0, seed=int(o.get('seed', 7)))
        ends = np.abs(np.linspace(-1, 1, c['strip'].shape[1], dtype=np.float32))
        blr = cv2.GaussianBlur(c['strip'], (0, 0), 0.004 * 1920 * 0.6)
        k = np.clip((ends - 0.35) / 0.5, 0, 1)[None, :, None]
        c['strip'] = (c['strip'].astype(np.float32) * (1 - k) + blr.astype(np.float32) * k).astype(np.uint8)   # DOF at the ends
    keep = float(o.get('plate', 0.0))
    out = c['haze'].copy() if keep <= 0 else cv2.addWeighted(c['haze'], 1 - keep, fx.gauss_px(img, 0.012 * w), keep, 0)
    st = c['strip']
    f = since * 30.0
    settle = 1.0 - math.exp(-f / 4.0)
    trav = float(o.get('travel', 0.75)) * settle + 0.04 * since
    L = 2.6 * w
    sh_ = float(o.get('cell_h', 0.12)) * h * 2.3
    cx, cy = (1.25 - trav) * w, (0.40 + 0.10 * settle) * h
    ang = float(o.get('angle', -14.0))
    quad = fx.card_quad((w, h), cx, cy, L, sh_, rx=float(o.get('rx', 12.0)), ry=float(o.get('ry', 42.0)), rz=ang, focal=0.9)
    lay, m = fx.warp_to_quad(st, quad, (w, h))
    bl = 0.06 * w * max(0.0, 1.0 - f / 8.0)                             # 6 %W -> 0 over 8 frames
    if bl >= 2:
        lay = fx.line_blur(lay, bl, ang)
        m = fx.line_blur(cv2.cvtColor((m * 255).astype(np.uint8), cv2.COLOR_GRAY2BGR), bl, ang)[..., 0].astype(np.float32) / 255.0
    out = fx.shadow_under(out, m, 0.015 * w, (0.0, 0.01 * w), 0.5)
    out = fx.blend_mask(out, lay, m)
    if c['hero'] is not None:
        hero = c['hero']
        e = _ease_out(f / 10.0, 3)
        hw = float(o.get('hero_w', 0.18)) * w * (1.35 - 0.35 * e)
        q = fx.card_quad((w, h), float(o.get('x', 0.5)) * w, (float(o.get('y', 0.5)) + 0.06) * h, hw, hw * hero.shape[0] / hero.shape[1],
                         rx=6.0, ry=-14.0 + 40.0 * (1 - e), rz=6.0 + 50.0 * (1 - e))
        hl, hm = fx.warp_to_quad(hero, q, (w, h))
        if e < 0.9:
            hl = fx.gauss_px(hl, 0.006 * w * (1 - e))
            hm = cv2.GaussianBlur(hm, (0, 0), max(0.5, 0.006 * w * (1 - e)))
        out = fx.shadow_under(out, hm, 0.015 * w, (0.006 * w, 0.010 * w), 0.5)
        out = fx.blend_mask(out, hl, hm)
    p = min(1.0, f / max(1.0, float(o.get('crack_grow_f', 9))))
    return fx.screen_layer(out, c['fr'] * p, float(o.get('opacity', 0.9)))


# ---------------------------------------------------------------- polaroids: shared orbit entry

def polaroids_v2(ov, img, t):
    """
    Three BIG polaroids (0.30-0.36 W at 16:9, 0.50 W at 9:16), strongly foreshortened (ry 25-35, rx 8, roll
    +-6-12 deg), all three arriving within 6 frames on ONE shared camera orbit, smeared with the triangular
    shutter (no banding), back to front with soft shadows; near cards overlap the frame edge.
    """
    o = ov.ov
    if o.get('legacy'):
        return polaroids(ov, img, t)
    h, w = img.shape[:2]
    n = int(o.get('n', 3))
    c = ov._cache
    if 'cards' not in c:
        good = []
        for s in _stills(ov, n + 2):
            hsv = cv2.cvtColor(cv2.resize(s, (96, 54)), cv2.COLOR_BGR2HSV)
            g = hsv[..., 2].astype(np.float32) / 255.0
            if float(hsv[..., 1].mean()) / 255.0 > 0.8 or float(np.percentile(g, 98) - np.percentile(g, 2)) < 0.5:
                continue                                                # a blown / flat still makes a bad card
            good.append(s)
        if len(good) < n:
            good = (good + _stills(ov, n + 2))[:n]
        c['cards'] = [fx.polaroid_card(cv2.resize(s, (600, 600 * s.shape[0] // s.shape[1]))[:, 60:540], 0.05, 0.12) for s in good[:n]]
    f = (t - ov.t0) * 30.0
    fps = float(ov.eng.fps)
    slots = o.get('slots') or ([(0.36, 0.26, -30.0, -9.0, 0.52), (0.64, 0.50, 26.0, 7.0, 0.50), (0.40, 0.75, 32.0, -11.0, 0.54)] if h > w else
                               [(0.20, 0.40, -32.0, -10.0, 0.36), (0.52, 0.44, 25.0, 6.0, 0.31), (0.82, 0.38, 34.0, 12.0, 0.35)])
    order = sorted(range(min(n, len(c['cards']))), key=lambda i: slots[i % len(slots)][4])
    out = img

    def orbit(ff):
        e = _ease_out(ff / 9.0, 3)
        return e, (1 - e) * 0.85, (1 - e) * 22.0, 1.0 + 0.004 * ff          # ease, x sweep (W), extra yaw, slow push

    for i in order:
        card = c['cards'][i]
        sx, sy, ry, rz, cwf = slots[i % len(slots)]
        age = f - i * float(o.get('stagger_f', 2.0))                     # all three inside 6 frames
        if age < 0:
            continue
        e, sweep, yaw, push = orbit(age)
        e2, sweep2, _, _ = orbit(age + 30.0 / fps)
        x, y = (0.5 + (sx - 0.5) * push + sweep) * w, (0.5 + (sy - 0.5) * push - 0.08 * (1 - e)) * h
        x2 = (0.5 + (sx - 0.5) * push + sweep2) * w
        cw = cwf * float(o.get('size', 1.0)) * w * push
        quad = fx.card_quad((w, h), x, y, cw, cw * card.shape[0] / card.shape[1], rx=float(o.get('rx', 8.0)), ry=ry + yaw,
                            rz=rz * (1.0 + 0.8 * (1 - e)), focal=0.62)
        bgra = np.dstack([card, np.full(card.shape[:2], 255, np.uint8)])
        src = np.array([[0, 0], [card.shape[1], 0], [card.shape[1], card.shape[0]], [0, card.shape[0]]], np.float32)
        lay = cv2.warpPerspective(bgra, cv2.getPerspectiveTransform(src, quad), (w, h), flags=cv2.INTER_LINEAR,
                                  borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
        d = abs(x2 - x) * fps / 30.0
        if d > 4:
            sm = T.motion_blur(lay, 0.5 * d, 0.0, 401)
            pad = (sm.shape[1] - w) // 2
            lay = sm[pad:pad + h, pad:pad + w] if pad > 0 else sm
        m = lay[..., 3].astype(np.float32) / 255.0
        out = fx.shadow_under(out, m, 0.02 * w, (0.008 * w, 0.014 * w), 0.35)
        out = fx.blend_mask(out, np.ascontiguousarray(lay[..., :3]), m)
    return out


HANDLERS.update({'closing_3d': closing_3d_v2, 'ground_text': ground_text_v2, 'ink_blot': ink_blot_v2, 'bokeh': bokeh_v2,
                 'music_card3d': music_card3d_v2, 'glass_set': glass_set_v2, 'polaroids': polaroids_v2})


# ------------------------------------------------------------------ blur_flow outro (ZkWQloS4jtM 33.47-37.5 s)
_OUTRO_CACHE = {}


def zkw_outro(ov, img, t):
    """
    ZkWQ's end card, read off its frames at 6 fps: the picture goes to a dark charcoal vignette (lit from the top), the
    creator's round avatar comes out of a big defocused ring and settles centred at ~0.36 H with a soft white rim
    (0.5 s, ease-out), holds, fades out by `avatar_end`; then a glowing pink down-chevron with a dot pulses in the centre
    (scale +-12 % on the beat) until the end. Overlay keys: image (avatar path), avatar_end (s after t0), color, beat.
    """
    o = ov.ov
    h, w = img.shape[:2]
    since = t - ov.t0
    key = ('bg', w, h)
    if key not in _OUTRO_CACHE:
        ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
        r = np.sqrt(((xs - w * 0.5) / (0.75 * w)) ** 2 + ((ys + 0.2 * h) / (1.1 * h)) ** 2)
        g = np.clip(1.0 - r, 0, 1) ** 1.6
        base = np.array([9, 10, 9], np.float32) + g[..., None] * np.array([34, 37, 36], np.float32)
        _OUTRO_CACHE[key] = base.astype(np.uint8)
    bg = _OUTRO_CACHE[key]
    k_in = min(1.0, since / 0.12)
    out = fx.mix_u8(img, bg, k_in) if k_in < 1 else bg.copy()
    a_end = float(o.get('avatar_end', 1.55))
    path = o.get('image')
    if path and since < a_end + 0.3:
        ak = ('av', path)
        if ak not in _OUTRO_CACHE:
            av = cv2.imread(path, cv2.IMREAD_UNCHANGED)
            _OUTRO_CACHE[ak] = av
        av = _OUTRO_CACHE[ak]
        if av is not None:
            e = 1 - (1 - min(1.0, since / 0.5)) ** 3
            d = int(h * (0.95 - 0.59 * e))
            d = max(8, d)
            a = cv2.resize(av, (d, d), interpolation=cv2.INTER_AREA)
            if a.shape[2] == 3:
                a = np.dstack([a, np.full(a.shape[:2], 255, np.uint8)])
            yy, xx = np.mgrid[0:d, 0:d]
            circ = (((xx - d / 2 + 0.5) ** 2 + (yy - d / 2 + 0.5) ** 2) <= (d / 2 - 1) ** 2).astype(np.float32)
            m = a[..., 3].astype(np.float32) / 255.0 * circ
            col = a[..., :3]
            sig = (1 - e) * 0.03 * w
            if sig > 0.5:
                col = cv2.GaussianBlur(col, (0, 0), sig)
                m = cv2.GaussianBlur(m, (0, 0), sig)
            fade = min(1.0, max(0.0, (a_end + 0.3 - since) / 0.3)) * min(1.0, since / 0.15)
            lay = np.zeros_like(out)
            mm = np.zeros((h, w), np.float32)
            x0, y0 = w // 2 - d // 2, h // 2 - d // 2
            xa, ya, xb, yb = max(0, x0), max(0, y0), min(w, x0 + d), min(h, y0 + d)
            lay[ya:yb, xa:xb] = col[ya - y0:yb - y0, xa - x0:xb - x0]
            mm[ya:yb, xa:xb] = m[ya - y0:yb - y0, xa - x0:xb - x0]
            # soft white rim + glow
            ring = np.zeros((h, w), np.float32)
            cv2.circle(ring, (w // 2, h // 2), max(2, d // 2 - 1), 1.0, max(2, int(0.006 * h)), cv2.LINE_AA)
            glow = cv2.GaussianBlur(ring, (0, 0), 0.012 * h)
            out = fx.blend_mask(out, lay, mm * fade)
            rim = np.clip(ring * 0.85 + glow * 1.6, 0, 1) * fade * min(1.0, e * 1.5)
            out = fx.blend_mask(out, np.full_like(out, 235), rim)
    c0 = float(o.get('chevron_at', a_end + 0.2))
    if since >= c0:
        u = since - c0
        beat = float(o.get('beat', 0.733))
        pulse = 1.0 + 0.12 * math.exp(-((u % beat) / 0.12))
        grow = 1 - (1 - min(1.0, u / 0.25)) ** 3
        s = float(o.get('chevron', 0.062)) * h * pulse * grow
        cx, cy = w / 2.0, h * 0.5
        lay = np.zeros((h, w), np.float32)
        pts = np.array([[cx - 1.2 * s, cy - 0.35 * s], [cx, cy + 0.75 * s], [cx + 1.2 * s, cy - 0.35 * s]], np.int32)
        cv2.polylines(lay, [pts], False, 1.0, max(2, int(0.42 * s)), cv2.LINE_AA)
        dy = 0.15 * s * math.sin(2 * math.pi * u / beat)
        dm = np.array([[cx, cy - 1.25 * s + dy], [cx + 0.28 * s, cy - 0.95 * s + dy], [cx, cy - 0.65 * s + dy],
                       [cx - 0.28 * s, cy - 0.95 * s + dy]], np.int32)
        cv2.fillConvexPoly(lay, dm, 1.0, cv2.LINE_AA)
        glow = cv2.GaussianBlur(lay, (0, 0), 0.02 * h)
        colr = tuple(int(c) for c in (o.get('color') or (205, 190, 255)))   # BGR soft pink
        core = np.clip(lay, 0, 1)
        out = fx.blend_mask(out, np.full_like(out, colr), np.clip(glow * 2.2, 0, 1) * grow)
        out = fx.blend_mask(out, np.full_like(out, 250), core * grow)
    return out


HANDLERS['zkw_outro'] = zkw_outro
