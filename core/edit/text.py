#!/usr/bin/env python3
"""
Text for the edit engine: fonts, styled text sprites and a fast blit onto BGR frames.

Only Latin text is drawn with these fonts (emoji / CJK would render as empty squares): every string
goes through latin_only() first. Emoji are drawn as images via core/quiz/quiz_emoji.py (sticker()).

Styles follow the Study specs (core/edit/refs/*.json, overlays_text):
  title        glowing Lilita One title (the meme title card look)
  neon         Poppins Black caps, violet neon glow (Golden Hour lyric words on black)
  wide_thin    Poppins ExtraLight caps, 0.3 em tracking, soft glow (hype lyric / title words)
  bold_italic  Anton caps sheared 12 deg, dark drop shadow (drop words)
  bold_glow    Poppins ExtraBold with a coloured glow
  lyric        Poppins Light caps, 0.08 em tracking, 85% white, soft shadow (word per beat)
  hook_3d      Lilita One caps extruded in a dark teal (hook word slam)
  tiny         Poppins Light lowercase (soft romance lines)
  script       Great Vibes with a glow (postcard titles, handwriting)
  caption_bar  Poppins ExtraBold caps, near-black (drawn on a white bar)
  label        Anton caps white + shadow;  label_red: red with a red glow (meme punchline)
  kinetic      Poppins Black Italic caps, purple, dark outline (idol name scroll)
  lower        Poppins SemiBold lowercase (text on black intros)
  watermark    Poppins Medium, white (opacity set at blit time)
  sticker      Lilita One caps, black stroke, white offset shadow (comic words)
"""
import os
import unicodedata
from functools import lru_cache

import cv2
import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.path.join(HERE, 'assets', 'fonts')
QUIZ_FONTS = os.path.join(HERE, '..', 'quiz', 'assets', 'fonts')
FONTS = {
    'lilita': os.path.join(QUIZ_FONTS, 'LilitaOne-Regular.ttf'),
    'poppins_black': os.path.join(FONT_DIR, 'Poppins-Black.ttf'),
    # heavy straight geometric sans (cinematic_slowmo remake round 4: _sX2's lyric cut; SIL OFL, JulietaUla/Montserrat)
    'montserrat_black': os.path.join(FONT_DIR, 'Montserrat-Black.ttf'),
    'poppins_black_italic': os.path.join(FONT_DIR, 'Poppins-BlackItalic.ttf'),
    'poppins_extrabold': os.path.join(FONT_DIR, 'Poppins-ExtraBold.ttf'),
    'poppins_semibold': os.path.join(FONT_DIR, 'Poppins-SemiBold.ttf'),
    'poppins_medium': os.path.join(FONT_DIR, 'Poppins-Medium.ttf'),
    'poppins_light': os.path.join(FONT_DIR, 'Poppins-Light.ttf'),
    'poppins_extralight': os.path.join(FONT_DIR, 'Poppins-ExtraLight.ttf'),
    'anton': os.path.join(FONT_DIR, 'Anton-Regular.ttf'),
    'bebas': os.path.join(FONT_DIR, 'BebasNeue-Regular.ttf'),
    'script': os.path.join(FONT_DIR, 'GreatVibes-Regular.ttf'),
    # thin rounded geometric caps (i4f's lyric words); variable weight 300-700, pick one with 'quicksand@500'
    'quicksand': os.path.join(FONT_DIR, 'Quicksand-Variable.ttf'),
    # 2026-09-29 text upgrade (refs/text-animation.md, projects-study T9): free stand-ins for the editors' fonts, SIL OFL.
    # Akira Expanded Super Bold (wide heavy sans) -> Unbounded (variable 200-900, pick 'unbounded@800');
    # ChristmasMerryland (casual brush script) -> Kaushan Script; Pacifico as a rounder script alternative
    'unbounded': os.path.join(FONT_DIR, 'Unbounded-Variable.ttf'),
    'kaushan': os.path.join(FONT_DIR, 'KaushanScript-Regular.ttf'),
    'pacifico': os.path.join(FONT_DIR, 'Pacifico-Regular.ttf'),
    # 2026-10-04 template round 2: free lookalikes of the Love Again / Bullseye fonts (SIL OFL): BubbleGum -> Bubblegum Sans;
    # Integral CF (heavy geometric caps) / Lemon Milk (medium geometric caps) -> Montserrat (variable 100-900, 'montserrat@800')
    'bubblegum': os.path.join(FONT_DIR, 'BubblegumSans-Regular.ttf'),
    # A round 4 (2026-10-11): the editor's BubbleGum is a FAT puffy rounded face (Love Again 26.3-27.3 s FOUND / SOMEONE cards); Bubblegum Sans
    # is a light handwriting face. Fredoka (SIL OFL, variable: weight 300-700, width 75-125) bold + condensed is the lookalike
    'fredoka': os.path.join(FONT_DIR, 'Fredoka-Variable.ttf'),
    'montserrat': os.path.join(FONT_DIR, 'Montserrat-Variable.ttf'),
}

# colours are RGB here (PIL); sprites are converted to BGRA for the frames
STYLES = {
    'title': dict(font='lilita', case='upper', fill=(255, 255, 255), stroke=4, stroke_fill=(20, 10, 40), glow=[(22, 0.9, None), (22, 0.9, None)]),
    'neon': dict(font='poppins_black', case='upper', fill=(222, 196, 255), glow=[(6, 0.9, (155, 77, 255)), (24, 0.6, (155, 77, 255))]),
    'wide_thin': dict(font='poppins_extralight', case='upper', fill=(255, 255, 255), tracking=0.3, glow=[(8, 0.6, None)]),
    'bold_italic': dict(font='anton', case='upper', fill=(255, 255, 255), italic=0.21, shadow=(3, 3, 3, 0.8)),
    'bold_glow': dict(font='poppins_extrabold', fill=(255, 255, 255), glow=[(10, 0.8, (255, 170, 120))]),
    'lyric': dict(font='poppins_light', case='upper', fill=(255, 255, 255), opacity=0.85, tracking=0.08, shadow=(0, 2, 8, 0.35)),
    'hook_3d': dict(font='lilita', case='upper', fill=(255, 255, 255), extrude=(10, (1.0, 1.5), (14, 58, 74))),
    # ZkWQ creator card (blur_flow): chunky white caps extruded in a soft pink, soft white glow, over a defocused plate
    'zkw_card': dict(font='lilita', case='upper', fill=(255, 250, 252), extrude=(9, (0.8, 1.4), (236, 168, 186)),
                     glow=[(14, 0.35, (255, 225, 235))]),
    'tiny': dict(font='poppins_light', case='lower', fill=(255, 255, 255), shadow=(0, 1, 4, 0.4)),
    'script': dict(font='script', fill=(255, 255, 255), glow=[(12, 0.55, None)], shadow=(0, 2, 6, 0.35)),
    'caption_bar': dict(font='poppins_extrabold', case='upper', fill=(23, 17, 16)),
    'label': dict(font='anton', case='upper', fill=(255, 255, 255), shadow=(0, 3, 6, 0.8)),
    'label_red': dict(font='anton', case='upper', fill=(224, 21, 31), glow=[(12, 0.8, (224, 21, 31))], shadow=(0, 2, 4, 0.6)),
    'kinetic': dict(font='poppins_black_italic', case='upper', fill=(155, 48, 217), stroke=6, stroke_fill=(25, 8, 40)),
    'lower': dict(font='poppins_semibold', case='lower', fill=(255, 255, 255)),
    'watermark': dict(font='poppins_medium', fill=(255, 255, 255), glow=[(6, 0.35, (0, 0, 0))]),
    'watermark_italic': dict(font='poppins_medium', fill=(255, 255, 255), italic=0.2),
    'watermark_glow': dict(font='poppins_extrabold', fill=(255, 255, 255), glow=[(12, 0.8, (255, 140, 40))]),
    'watermark_script': dict(font='script', fill=(255, 255, 255), glow=[(10, 0.5, None)]),
    'sticker': dict(font='lilita', case='upper', fill=(255, 255, 255), stroke=6, stroke_fill=(0, 0, 0), shadow=(6, 6, 0, 1.0)),
    # --- fx group (core/edit/refs/fx.json, WGvtliE1298): hero word / set word / closing extruded word
    # hero_fx: geometric rounded sans, LOWERCASE, pure white, no stroke, no shadow; two-radius glow.
    # Calibrated against the reference HALO PROFILE, not its fitted gaussian amplitudes: the target is
    # excess-over-background 145 at 2 px, 98 at 4.5, 57 at 8, 35 at 12.5, 26 at 18, 21 at 27, 16 at 38,
    # 11 at 55 (px @1920). sigma 0.31 %W gain 0.70 + sigma 4.0 %W gain 0.30 reproduces all eight to a
    # mean relative error of 5.0 % (measured with the same distance transform the reference was read with).
    # Radii are px at a 1080-wide canvas; engine.py rescales them with the canvas (glow= override).
    # Side by side with f153-158 the 4.0 %W / 0.30 halo bloomed the word into a blob and the semibold
    # cut was visibly heavier than the reference's medium weight: back to the spec's 2.0 %W wide tap.
    'hero_fx': dict(font='poppins_medium', case='lower', fill=(255, 255, 255),
                    glow=[(3.35, 0.62, None), (21.6, 0.12, None)]),
    # set_fx: condensed bold grotesque CAPS, dark charcoal with a warm bronze rim toward the lit side
    'set_fx': dict(font='anton', case='upper', fill=(54, 49, 45), glow=[(7, 0.22, (196, 140, 74))]),
    # closing_3d: heavy grotesque caps, cream/gold body with a darker wall, lit top edge, soft cast shadow
    # (side wall 10-14 % of the cap height, shadow offset 3 % cap, sigma 2 %W, alpha 0.35)
    'closing_3d': dict(font='lilita', case='upper', fill=(225, 190, 150),
                       extrude=(12, (0.8, 1.0), (195, 140, 90)), bevel=(1.5, (255, 238, 214), 0.85),
                       shadow=(6, 8, 14, 0.35)),
    # --- lyric text (core/edit/lyric_text.py; *-av.md text sections). glow_rel / shadow_rel / extrude_rel are
    # fractions of the font size, applied by lyric_text at render time (render() itself ignores them).
    # hype_typography (zawB / m3lv / i4f): tall condensed caps, cream white, strongest halo of the group
    'lyric_hype': dict(font='bebas', case='upper', fill=(253, 249, 245), tracking=0.16,
                       glow_rel=[(0.035, 0.95, None), (0.12, 0.55, None)]),
    # hype_typography, i4f (PJUNKIE): thin rounded geometric caps (Quicksand ~500), white, wide tracking, bright halo
    'lyric_hype_wide': dict(font='quicksand@500', case='upper', fill=(255, 252, 248), tracking=0.10,
                            glow_rel=[(0.03, 1.0, None), (0.10, 0.6, (255, 236, 220))]),
    'lyric_hype_wide_ink': dict(font='quicksand@600', case='upper', fill=(161, 95, 84), tracking=0.10,
                                shadow_rel=(0.0, 0.01, 0.03, 0.25)),
    # hype_typography, i4f's words on a blown-out plate: dark ink (#a15f54 / #25212a), no glow, a faint shadow
    'lyric_hype_ink': dict(font='bebas', case='upper', fill=(84, 95, 161), tracking=0.16,
                           shadow_rel=(0.0, 0.01, 0.03, 0.25)),
    # blur_flow (S2rz): pastel lilac caps (#cccbf0) with a lilac halo
    'lyric_pastel': dict(font='poppins_semibold', case='upper', fill=(204, 203, 240), tracking=0.10,
                         glow_rel=[(0.04, 0.9, (190, 180, 255)), (0.14, 0.5, (139, 134, 204))]),
    # cinematic_slowmo (_sX2): saturated purple Poppins Black (#a21fe3..#c539ec), glow +18..+35, halo +28..+48
    'lyric_neon': dict(font='poppins_extrabold', case='upper', fill=(178, 40, 232), tracking=0.06,
                       glow_rel=[(0.05, 0.95, (162, 31, 227)), (0.18, 0.6, (197, 57, 236))]),
    # char_quick_velocity (yUui / TG / KSjp): thin caps, warm off-white, no glow (a faint shadow for legibility)
    'lyric_thin': dict(font='poppins_light', case='upper', fill=(238, 234, 222), opacity=0.92, tracking=0.08,
                       shadow_rel=(0.0, 0.01, 0.03, 0.35)),
    # char_quick_velocity, YH variant: chunky 3D hook words in cyan (#89e0f8) with a dark teal wall
    'lyric_hook3d': dict(font='lilita', case='upper', fill=(137, 224, 248), extrude_rel=(0.06, (0.8, 1.2), (14, 58, 74))),
    # char_soft_romance (rfsj): small light lowercase with a soft glow
    'lyric_soft': dict(font='poppins_light', case='lower', fill=(240, 236, 234), glow_rel=[(0.06, 0.55, None)],
                       shadow_rel=(0.0, 0.01, 0.04, 0.3)),
    # kpop_text_strobe (pB7P): rounded bold lowercase sans at 85 % white, no stroke, no glow
    'lyric_round': dict(font='poppins_semibold', case='lower', fill=(255, 255, 255), opacity=0.85),
    # blur_flow / S2rz remake (refs frames 1.5, 5.0, 11.5, 17.5, 20.8 s): the intro's rounded chunky title with a blue
    # halo ("hyperspace" card), the typed dialogue caption (heavy rounded sans, white, cold halo) and the body's
    # sung words (geometric caps, white-lilac core, lilac halo)
    's2rz_title': dict(font='lilita', fill=(250, 252, 255), tracking=0.03, glow=[(5, 0.95, (90, 170, 255)), (18, 0.55, (60, 120, 235))]),
    # S2rz hook title (reference 1.0-2.3 s): chunky rounded caps face with a deep blue extrusion and the blue halo
    's2rz_title3d': dict(font='lilita', fill=(252, 253, 255), tracking=0.03, extrude=(9, (0.7, 1.3), (25, 60, 150)),
                         glow=[(5, 0.9, (90, 170, 255)), (20, 0.5, (60, 120, 235))]),
    's2rz_caption': dict(font='poppins_extrabold', fill=(250, 251, 253), glow=[(4, 0.75, (130, 200, 255)), (14, 0.35, (80, 140, 230))]),
    's2rz_word': dict(font='quicksand@600', case='upper', fill=(242, 238, 255), tracking=0.06,
                      glow_rel=[(0.03, 0.95, (205, 195, 255)), (0.12, 0.6, (140, 128, 232))]),
    # 2026-09-29 text upgrade: the project-file typography (Love Again, projects-study T9): a wide heavy sans (172-211 px
    # @1080p) and a script (89-122 px), white fill; the look (bevel, glow, shadow, motion) comes from text_fx.FX
    'wide_heavy': dict(font='unbounded@800', case='upper', fill=(255, 255, 255), tracking=0.02),
    'script_brush': dict(font='kaushan', fill=(255, 255, 255)),
    # free lookalike of the rough heavy caps face of the Bullseye / Purgify edits (Chinese Rocks): heavy condensed caps
    'rocks_caps': dict(font='anton', case='upper', fill=(255, 255, 255), tracking=0.03),
    # The Beach's Element 3D title (char_quick_velocity): wide heavy caps, warm white face, a deep slate extrusion
    'ed_integral': dict(font='montserrat@800', case='upper', fill=(255, 255, 255), tracking=0.02),
    'ed_lemon': dict(font='montserrat@600', case='upper', fill=(255, 255, 255), tracking=0.06),
    # A round 6: the published FOUND / SOMEONE letters are fat and wide with tight gaps -> full width, a third of the editor's tracking
    'ed_bubble': dict(font='fredoka@700,100', fill=(255, 255, 255), case='upper', ae_tracking_k=0.3),      # A round 5: the editor's BubbleGum is a capitals-only face (published FOUND / SOMEONE)
    'beach_3d': dict(font='unbounded@800', case='upper', fill=(246, 244, 238), tracking=0.03,
                     extrude_rel=(0.07, (0.7, 1.1), (38, 44, 58)), glow_rel=[(0.05, 0.45, (170, 200, 255))])
}


def latin_only(text):
    """Drop what the Latin fonts can't draw (emoji, symbols, CJK): they would render as squares."""
    keep = [ch for ch in str(text or '') if ord(ch) < 0x2000 and unicodedata.category(ch)[0] in 'LNPZS'
            and not unicodedata.category(ch).startswith('So')]
    return ' '.join(''.join(keep).split())


@lru_cache(maxsize=128)
def font(name, size):
    wght = None
    if isinstance(name, str) and '@' in name:
        name, wght = name.split('@', 1)
    path = FONTS.get(name, name)
    try:
        f = ImageFont.truetype(path, int(max(6, size)))
        if wght:
            try:
                f.set_variation_by_axes([float(v) for v in wght.split(',')])     # 'name@700' or 'name@700,75' (axes in the font's order)
            except Exception:
                pass
        return f
    except OSError:
        return ImageFont.truetype(FONTS['lilita'], int(max(6, size)))


def _case(text, case):
    if case == 'upper':
        return text.upper()
    if case == 'lower':
        return text.lower()
    return text


class Sprite:
    """A rendered text image (BGRA uint8, straight alpha) plus the x range of every letter (for type-on)."""

    def __init__(self, bgra, letters, baseline_pad):
        self.img = bgra
        self.letters = letters          # [(char, x0, x1)] in image px
        self.pad = baseline_pad

    @property
    def size(self):
        return self.img.shape[1], self.img.shape[0]

    def crop_letters(self, n, frac=1.0):
        """Only the first n letters (the n-th at alpha frac): type-on reveals."""
        if n >= len(self.letters):
            return self.img
        out = self.img.copy()
        n = max(0, n)
        x0 = int(self.letters[n][1]) + self.pad
        x1 = int(self.letters[n][2]) + self.pad
        if frac > 0:
            out[:, x0:x1, 3] = (out[:, x0:x1, 3] * min(1.0, frac)).astype(np.uint8)
            out[:, x1:, 3] = 0
        else:
            out[:, x0:, 3] = 0
        return out

    def letter(self, i):
        """The i-th letter alone (BGRA crop) and its x offset in the sprite."""
        _, x0, x1 = self.letters[i]
        a, b = max(0, int(x0) + self.pad // 2), min(self.img.shape[1], int(x1) + self.pad + self.pad // 2)
        return self.img[:, a:b], a


def render(text, style='title', size=100, max_w=None, min_size=20, **over):
    """Render text in a style -> Sprite. Keyword overrides: fill, font, tracking, glow, stroke, italic, case, opacity."""
    st = dict(STYLES.get(style, STYLES['title']))
    st.update({k: v for k, v in over.items() if v is not None})
    text = _case(latin_only(text), st.get('case'))
    if not text:
        text = ' '
    size = int(size)
    while True:
        spr = _render(text, st, size)
        if not max_w or spr.size[0] - 2 * spr.pad <= max_w or size <= min_size:
            return spr
        size = max(min_size, int(size * max(0.6, max_w / max(1, spr.size[0] - 2 * spr.pad)) * 0.98))


def _render(text, st, size):
    f = font(st.get('font', 'lilita'), size)
    tracking = float(st.get('tracking', 0.0)) * size
    stroke = int(st.get('stroke', 0))
    glow = st.get('glow') or []
    shadow = st.get('shadow')
    extrude = st.get('extrude')
    pad = int(max([g[0] * 2.2 for g in glow] + [stroke * 2 + 4, 8] + ([abs(shadow[0]) + abs(shadow[1]) + shadow[2] * 2] if shadow else [])
                  + ([extrude[0] * max(abs(extrude[1][0]), abs(extrude[1][1])) + 4] if extrude else [])))
    # letter positions
    xs, x = [], 0.0
    for ch in text:
        w = f.getlength(ch)
        xs.append((ch, x, x + w))
        x += w + tracking
    width = int(x - tracking + 2) if text else 1
    asc, desc = f.getmetrics()
    height = asc + desc + 2
    italic = float(st.get('italic', 0.0))
    extra_w = int(abs(italic) * height) + 2
    W_, H_ = width + 2 * pad + extra_w, height + 2 * pad
    fill = tuple(st.get('fill', (255, 255, 255)))
    txt = Image.new('RGBA', (W_, H_), (0, 0, 0, 0))
    d = ImageDraw.Draw(txt)
    for ch, x0, _ in xs:
        d.text((pad + x0, pad), ch, font=f, fill=fill + (255,), stroke_width=stroke,
               stroke_fill=tuple(st.get('stroke_fill', (0, 0, 0))) + (255,))
    if italic:
        # shear: x' = x + italic * (H - y) keeps the baseline in place
        txt = txt.transform(txt.size, Image.AFFINE, (1, italic, -italic * (pad + asc), 0, 1, 0), resample=Image.BICUBIC)
    out = Image.new('RGBA', (W_, H_), (0, 0, 0, 0))
    alpha = txt.split()[3]
    if extrude:
        n, (dx, dy), col = extrude
        for k in range(n, 0, -1):
            lay = Image.new('RGBA', (W_, H_), tuple(col) + (0,))
            lay.putalpha(alpha)
            out.alpha_composite(lay, (int(round(dx * k)), int(round(dy * k))))
    if shadow:
        dx, dy, blur, a = shadow
        sh = Image.new('RGBA', (W_, H_), (0, 0, 0, 0))
        sh.putalpha(alpha.point(lambda v: int(v * a)))
        if blur:
            sh = sh.filter(ImageFilter.GaussianBlur(blur))
        out.alpha_composite(sh, (int(dx), int(dy)))
    for rad, a, col in glow:
        c = tuple(col) if col else fill
        g = Image.new('RGBA', (W_, H_), c + (0,))
        g.putalpha(alpha.filter(ImageFilter.GaussianBlur(rad)).point(lambda v: min(255, int(v * a * 1.6))))
        out.alpha_composite(g)
    out.alpha_composite(txt)
    bevel = st.get('bevel')
    if bevel:
        # a lit top-left edge over the face (the closing 3D word's bevel + specular)
        off, bcol, ba = bevel
        lit = Image.new('RGBA', (W_, H_), tuple(bcol) + (0,))
        edge = ImageChops.subtract(alpha, alpha.filter(ImageFilter.MinFilter(3)))
        lit.putalpha(edge.point(lambda v: int(v * ba)))
        o = int(round(off))
        out.alpha_composite(ImageChops.offset(lit, -o, -o) if o else lit)
    op = float(st.get('opacity', 1.0))
    arr = np.array(out)
    if op < 1:
        arr[..., 3] = (arr[..., 3] * op).astype(np.uint8)
    bgra = arr[..., [2, 1, 0, 3]].copy()
    return Sprite(bgra, xs, pad)


def rgba_from_pil(img):
    """PIL RGBA image -> BGRA numpy (for emoji images from quiz_emoji)."""
    a = np.array(img.convert('RGBA'))
    return a[..., [2, 1, 0, 3]].copy()


def transform(bgra, scale=1.0, rot=0.0, sx=1.0, sy=1.0):
    """Scale (uniform x sx/sy) and rotate a BGRA image about its centre into a canvas that fits it."""
    if abs(scale * sx - 1) < 1e-3 and abs(scale * sy - 1) < 1e-3 and abs(rot) < 0.05:
        return bgra
    h, w = bgra.shape[:2]
    kx, ky = scale * sx, scale * sy
    c, s = np.cos(np.radians(rot)), np.sin(np.radians(rot))
    corners = np.array([[-w / 2, -h / 2], [w / 2, -h / 2], [w / 2, h / 2], [-w / 2, h / 2]]) * [kx, ky]
    R = np.array([[c, s], [-s, c]])
    pts = corners @ R
    nw, nh = int(np.ceil(np.ptp(pts[:, 0]))) + 2, int(np.ceil(np.ptp(pts[:, 1]))) + 2
    M = np.array([[kx * c, ky * s, 0], [-kx * s, ky * c, 0]], np.float64)
    M[:, 2] = [nw / 2 - (M[0, 0] * w / 2 + M[0, 1] * h / 2), nh / 2 - (M[1, 0] * w / 2 + M[1, 1] * h / 2)]
    if nw < 2 or nh < 2:
        return np.zeros((2, 2, 4), np.uint8)
    return cv2.warpAffine(bgra, M, (nw, nh), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))


def motion_blur(bgra, length, angle=0.0, max_len=81):
    """
    Directional blur of a BGRA sprite, colour and alpha together (fx.json: a word that passes the lens is
    smeared 60-140 px @1920 on its first / last frames and NEVER fades out). `length` px, `angle` deg.
    """
    if bgra is None or length < 1.5:
        return bgra
    # round 3: (1) PREMULTIPLIED alpha - filtering straight BGR drags the transparent pixels' black into the
    # smear and the glyph arrives as a grey slab; (2) a TRIANGULAR line kernel (a shutter that opens and
    # closes) instead of a box, whose hard ends showed as vertical stripe banding; (3) a 1-D separable pass
    # for horizontal / vertical smears, so long smears (up to 401 px) stay cheap.
    n = int(min(int(max_len), max(3, round(length * 1.35))))      # a triangle needs ~1.35 x the box length for the same spread
    n += 1 - n % 2
    tri = 1.0 - np.abs(np.linspace(-1.0, 1.0, n, dtype=np.float32)) * (n - 1.0) / n
    tri /= float(tri.sum())
    pad = n // 2 + 1
    big = cv2.copyMakeBorder(bgra, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=(0, 0, 0, 0)).astype(np.float32)
    big[..., :3] *= big[..., 3:4] / 255.0
    a = float(angle) % 180.0
    one = np.ones((1,), np.float32)
    if a < 1.0 or a > 179.0:
        out = cv2.sepFilter2D(big, -1, tri, one, borderType=cv2.BORDER_CONSTANT)
    elif abs(a - 90.0) < 1.0:
        out = cv2.sepFilter2D(big, -1, one, tri, borderType=cv2.BORDER_CONSTANT)
    else:
        k = np.zeros((n, n), np.float32)
        k[n // 2, :] = tri
        k = cv2.warpAffine(k, cv2.getRotationMatrix2D((n / 2 - 0.5, n / 2 - 0.5), -float(angle), 1.0), (n, n))
        s = float(k.sum())
        if s <= 1e-6:
            return bgra
        out = cv2.filter2D(big, -1, k / s, borderType=cv2.BORDER_CONSTANT)
    al = np.maximum(out[..., 3:4], 1e-3)
    out[..., :3] = out[..., :3] * (255.0 / al)
    return np.clip(out, 0, 255).astype(np.uint8)


def blit(frame, bgra, cx, cy, alpha=1.0, scale=1.0, rot=0.0, sx=1.0, sy=1.0, blur_y=0, blur_len=0.0, blur_ang=0.0, blur_max=81):
    """Composite a BGRA sprite centred at (cx, cy) onto a BGR frame in place (float math only inside its box)."""
    if alpha <= 0.003 or bgra is None:
        return frame
    img = transform(bgra, scale, rot, sx, sy)
    if blur_len >= 1.5:
        img = motion_blur(img, blur_len, blur_ang, blur_max)
    if blur_y >= 3:
        img = cv2.blur(img, (1, int(blur_y)))
    h, w = img.shape[:2]
    x0, y0 = int(round(cx - w / 2)), int(round(cy - h / 2))
    fx0, fy0 = max(0, x0), max(0, y0)
    fx1, fy1 = min(frame.shape[1], x0 + w), min(frame.shape[0], y0 + h)
    if fx1 <= fx0 or fy1 <= fy0:
        return frame
    src = img[fy0 - y0:fy1 - y0, fx0 - x0:fx1 - x0]
    a = src[..., 3:4].astype(np.float32) * (alpha / 255.0)
    dst = frame[fy0:fy1, fx0:fx1]
    if frame.ndim == 3 and frame.shape[2] == 4:
        # 2026-09-29 text upgrade (text_fx.py): a 4-channel frame is a PREMULTIPLIED BGRA layer (colour already
        # multiplied by its alpha) - "over": C = src * a + C (1 - a), A = a + A (1 - a); the layer is warped / glowed
        # as a whole and composited later
        frame[fy0:fy1, fx0:fx1, :3] = (dst[..., :3] * (1 - a) + src[..., :3] * a).astype(frame.dtype)
        frame[fy0:fy1, fx0:fx1, 3:4] = (dst[..., 3:4] * (1 - a) + 255.0 * a).astype(frame.dtype)
        return frame
    frame[fy0:fy1, fx0:fx1] = (dst * (1 - a) + src[..., :3] * a).astype(np.uint8)
    return frame


def emoji_sprite(emo, size):
    """An emoji as a BGRA sprite (quiz_emoji images), or None."""
    try:
        import sys
        sys.path.insert(0, os.path.join(HERE, '..', 'quiz'))
        import quiz_emoji  # noqa: E402
        im = quiz_emoji.image(emo, int(size))
        return rgba_from_pil(im) if im is not None else None
    except Exception:
        return None
