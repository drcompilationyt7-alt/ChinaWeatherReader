#!/usr/bin/env python3
"""
"Rotate your phone" hint for the ROTATED shorts (sound edit and slow-mo: the 16:9 frame turned 90 deg into 9:16; owner 2026-10-13).

A small, semi-transparent corner OVERLAY on the playing video (never an intro: nothing is delayed, paused or dimmed, the edit starts at full action in
frame 0): a phone icon (~9 % of the width) with a looping 90 deg turn and a curved arrow, and tiny text "rotate phone" under it in the channel font
(Montserrat, like the watermark). It is UPRIGHT (drawn on the delivered 1080x1920 frame, not on the rotated master), sits on the left edge at 56 %
of the height (outside the Shorts UI: right buttons, top 12 %, caption / bottom 30 %), shows from t = 0 for ~5 s (fades in over 0.25 s and out over
0.5 s) and once more mid-video for ~2.5 s. watermark.burn() turns it on only for rot90 deliveries (env EDIT_ROTATE_HINT=0 = off); never on landscape longs.

    from rotate_hint import apply, shows_for
    frame = apply(frame_rgb_uint8, t_seconds, shows=shows_for(duration_s))
    python core/edit/rotate_hint.py --selftest [--sheet out.jpg]
"""
import argparse
import math
import os

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SS = 3                      # supersampling of the sprites
FIRST_S, AGAIN_S = 5.0, 2.5
FADE_IN, FADE_OUT = 0.25, 0.5
CENTER_X, CENTER_Y = 0.115, 0.56     # of the frame: left edge, above the caption zone
TEXT = 'rotate phone'
_CACHE = {}


def shows_for(duration):
    """[(start, length)] of the shows: from frame 0, and once more mid-video when the video is long enough."""
    out = [(0.0, FIRST_S)]
    if duration and duration >= 16:
        out.append((round(duration * 0.55, 2), AGAIN_S))
    return out


def _font(px):
    from PIL import ImageFont
    try:
        f = ImageFont.truetype(os.path.join(HERE, 'assets', 'fonts', 'Montserrat-Variable.ttf'), px)
        f.set_variation_by_axes([600])
        return f
    except Exception:  # noqa: BLE001
        return ImageFont.load_default()


def _sprites(w):
    """(static RGBA float 0-1: pill + text + arrow, icon RGBA float, geometry) for a frame width w."""
    if w in _CACHE:
        return _CACHE[w]
    from PIL import Image, ImageDraw, ImageFilter
    u = w / 1080.0
    S = SS
    pw, ph = int(150 * u), int(176 * u)                                   # ~14 % of the width: a corner badge
    pill = Image.new('RGBA', (pw * S, ph * S), (0, 0, 0, 0))
    d = ImageDraw.Draw(pill)
    d.rounded_rectangle((0, 0, pw * S - 1, ph * S - 1), radius=int(26 * u * S), fill=(255, 255, 255, 30), outline=(255, 255, 255, 105), width=max(2, S))
    fs = int(21 * u * S)
    f = _font(fs)
    tw = d.textlength(TEXT, font=f)
    tx, ty = int((pw * S - tw) / 2), int(ph * S - fs * 1.75)
    sh = Image.new('RGBA', pill.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).text((tx + S, ty + S), TEXT, font=f, fill=(0, 0, 0, 110))
    pill = Image.alpha_composite(pill, sh.filter(ImageFilter.GaussianBlur(1.5 * S)))
    ImageDraw.Draw(pill).text((tx, ty), TEXT, font=f, fill=(255, 255, 255, 235))
    cx, cy, r = pw * S // 2, int(66 * u * S), int(44 * u * S)
    d = ImageDraw.Draw(pill)
    d.arc((cx - r, cy - r, cx + r, cy + r), start=205, end=335, fill=(255, 255, 255, 225), width=max(3, int(4.5 * u * S)))
    a = math.radians(335)
    ex, ey = cx + r * math.cos(a), cy + r * math.sin(a)
    t = a + math.pi / 2
    ah = int(14 * u * S)
    d.polygon([(ex + ah * math.cos(t), ey + ah * math.sin(t)), (ex + ah * 0.7 * math.cos(t + 2.3), ey + ah * 0.7 * math.sin(t + 2.3)),
               (ex + ah * 0.7 * math.cos(t - 2.3), ey + ah * 0.7 * math.sin(t - 2.3))], fill=(255, 255, 255, 225))
    pill = pill.resize((pw, ph), Image.LANCZOS)
    isz = int(80 * u)
    icon = Image.new('RGBA', (isz * S, isz * S), (0, 0, 0, 0))
    di = ImageDraw.Draw(icon)
    pw_, ph_ = int(30 * u * S), int(52 * u * S)
    x0, y0 = (isz * S - pw_) // 2, (isz * S - ph_) // 2
    di.rounded_rectangle((x0, y0, x0 + pw_, y0 + ph_), radius=int(7 * u * S), outline=(255, 255, 255, 240), width=max(3, int(4 * u * S)), fill=(255, 255, 255, 55))
    di.rounded_rectangle((x0 + pw_ // 2 - int(6 * u * S), y0 + int(5 * u * S), x0 + pw_ // 2 + int(6 * u * S), y0 + int(8 * u * S)),
                         radius=int(2 * u * S), fill=(255, 255, 255, 220))
    icon = icon.resize((isz, isz), Image.LANCZOS)
    _CACHE[w] = (pill, icon, {'pw': pw, 'ph': ph, 'isz': isz, 'icx': pw // 2, 'icy': int(66 * u)})
    return _CACHE[w]


def _ease(x):
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


def envelope(t, shows):
    """(opacity 0-1, icon turn 0-1) at time t. The icon loops: turn to landscape (0.7 s), hold (0.5 s), back (0.5 s), rest (0.4 s)."""
    best = (0.0, 0.0)
    for t0, ln in shows:
        u = t - t0
        if u < 0 or u > ln:
            continue
        a = _ease(u / FADE_IN) * (1.0 - _ease((u - (ln - FADE_OUT)) / FADE_OUT))
        ph = u % 2.1
        turn = _ease(ph / 0.7) if ph < 1.2 else (1.0 - _ease((ph - 1.2) / 0.5) if ph < 1.7 else 0.0)
        if a > best[0]:
            best = (a, turn)
    return best


def apply(fr, t, shows=None):
    """The frame (RGB uint8, UPRIGHT portrait) with the hint at time t; untouched outside the shows. No dimming, no blur of the picture."""
    a, turn = envelope(t, shows if shows is not None else shows_for(0))
    if a <= 0.01:
        return fr
    from PIL import Image
    h, w = fr.shape[:2]
    pill, icon, g = _sprites(w)
    sp = np.asarray(pill, np.float32) / 255.0
    ic = np.asarray(icon.rotate(-90.0 * turn, resample=Image.BICUBIC), np.float32) / 255.0
    cx, cy = int(w * CENTER_X), int(h * CENTER_Y)
    x0, y0 = cx - g['pw'] // 2, cy - g['ph'] // 2
    x1, y1 = x0 + g['pw'], y0 + g['ph']
    x0c, y0c, x1c, y1c = max(0, x0), max(0, y0), min(w, x1), min(h, y1)
    if x1c <= x0c or y1c <= y0c:
        return fr
    region = fr[y0c:y1c, x0c:x1c].astype(np.float32)
    sa = sp[y0c - y0:y1c - y0, x0c - x0:x1c - x0]
    out = region * (1 - sa[..., 3:4] * a) + sa[..., :3] * 255.0 * sa[..., 3:4] * a
    iw_ = ic.shape[1]
    ix, iy = x0 + g['icx'] - iw_ // 2, y0 + g['icy'] - ic.shape[0] // 2
    jx0, jy0, jx1, jy1 = max(x0c, ix), max(y0c, iy), min(x1c, ix + iw_), min(y1c, iy + ic.shape[0])
    if jx1 > jx0 and jy1 > jy0:
        sub = ic[jy0 - iy:jy1 - iy, jx0 - ix:jx1 - ix]
        o = out[jy0 - y0c:jy1 - y0c, jx0 - x0c:jx1 - x0c]
        out[jy0 - y0c:jy1 - y0c, jx0 - x0c:jx1 - x0c] = o * (1 - sub[..., 3:4] * a) + sub[..., :3] * 255.0 * sub[..., 3:4] * a
    res = fr.copy()
    res[y0c:y1c, x0c:x1c] = np.clip(out, 0, 255).astype(np.uint8)
    return res


def selftest(sheet=None):
    h, w = 1920, 1080
    base = np.zeros((h, w, 3), np.uint8)
    base[:] = (60, 90, 140)
    base[::40, :] = (230, 200, 80)
    sh = shows_for(32.0)
    assert sh[0] == (0.0, FIRST_S) and len(sh) == 2 and shows_for(9.0) == [(0.0, FIRST_S)]
    assert np.array_equal(apply(base, 8.0, sh), base)                      # nothing between the shows
    assert not np.array_equal(apply(base, 0.1, sh), base)                  # it starts at the very beginning: nothing is delayed
    ts = [0.1, 0.3, 0.8, 1.3, 1.8, 4.8]
    outs = [apply(base, t, sh) for t in ts]
    # only the corner badge changes: the rest of the frame is bit-identical (no dimming of the video)
    diff = np.any(outs[2] != base, axis=2)
    ys, xs = np.nonzero(diff.any(axis=1))[0], np.nonzero(diff.any(axis=0))[0]
    assert 0.40 * h < ys.min() and ys.max() < 0.70 * h and xs.max() < 0.25 * w, (ys.min(), ys.max(), xs.max())
    print('rotate_hint selftest OK (rows', ys.min(), ys.max(), 'cols', xs.min(), xs.max(), ')')
    if sheet:
        import cv2
        cells = [cv2.resize(o[int(0.42 * h):int(0.7 * h), 0:int(0.3 * w)], (216, 280)) for o in outs]
        cv2.imwrite(sheet, np.hstack(cells)[:, :, ::-1])


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--selftest', action='store_true')
    ap.add_argument('--sheet')
    a = ap.parse_args()
    if a.selftest:
        selftest(a.sheet)
