#!/usr/bin/env python3
"""
Profile picture (800x800) and banner (2560x1440) for Mr. WorldWideWebster:
a broke NEET who makes fan edits for his hobby and his oshi. Drawn from
scratch: Noto emoji (Apache-2.0), the OFL Lilita One font, and a Japanese
system font for the headband (Yu Gothic / Meiryo on Windows, Noto CJK on
Linux). Output: branding/meme-channel/{avatar,banner}.png

    python scripts/make-meme-branding.py
"""
import math
import os
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'core', 'quiz'))
import quiz_emoji  # noqa: E402
from render_quiz import text_layer, with_shadow  # noqa: E402

OUT = os.path.join(ROOT, 'branding', 'meme-channel')
NIGHT_TOP, NIGHT_BOT = (22, 12, 48), (78, 20, 110)
PINK, CYAN, GOLD, NAVY = (255, 70, 170), (60, 230, 255), (255, 214, 0), (20, 10, 40)
JP_FONTS = ['C:/Windows/Fonts/YuGothB.ttc', 'C:/Windows/Fonts/meiryob.ttc',
            '/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc', '/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc']


def jp_font(size):
    for p in JP_FONTS:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    raise SystemExit('no Japanese font found (install fonts-noto-cjk)')


def gradient(w, h, top, bot):
    y = Image.linear_gradient('L').resize((w, h))
    return Image.composite(Image.new('RGB', (w, h), bot), Image.new('RGB', (w, h), top), y).convert('RGBA')


def glow(size, color, radius, alpha=200):
    """A soft neon blob."""
    im = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(im).ellipse([size * 0.25, size * 0.25, size * 0.75, size * 0.75], fill=color + (alpha,))
    return im.filter(ImageFilter.GaussianBlur(radius))


def penlight(length, color, angle):
    """Idol-concert penlight: grey grip, glowing tube, rotated."""
    w = int(length * 0.16)
    im = Image.new('RGBA', (w * 3, length + w * 2), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    x0, grip = w, int(length * 0.28)
    tube = [x0, w, x0 + w, w + length - grip]
    halo = Image.new('RGBA', im.size, (0, 0, 0, 0))
    ImageDraw.Draw(halo).rounded_rectangle([tube[0] - w * 0.4, tube[1] - w * 0.4, tube[2] + w * 0.4, tube[3] + w * 0.2],
                                           radius=w, fill=color + (230,))
    im.alpha_composite(halo.filter(ImageFilter.GaussianBlur(w * 0.6)))
    d.rounded_rectangle(tube, radius=w // 2 - 2, fill=tuple(min(255, c + 90) for c in color) + (255,))
    d.rounded_rectangle([x0 + w * 0.25, w * 1.3, x0 + w * 0.55, w + (length - grip) * 0.8], radius=w // 5, fill=(255, 255, 255, 190))
    d.rounded_rectangle([x0 - 2, w + length - grip, x0 + w + 2, w + length], radius=w // 3, fill=(70, 70, 86, 255),
                        outline=(30, 30, 40, 255), width=max(2, w // 8))
    return im.rotate(angle, resample=Image.BICUBIC, expand=True)


def headband(face):
    """
    White hachimaki with a red 推し (oshi) wrapped round the forehead of a
    face image: the band is clipped to the head's outline, the knot and its
    tails stick out on the right. Returns a layer the size of the face plus
    room for the tails.
    """
    fw, fh = face.size
    pad = int(fw * 0.32)
    im = Image.new('RGBA', (fw + pad, fh), (0, 0, 0, 0))
    h = int(fh * 0.17)
    top = int(fh * 0.17)
    band = Image.new('RGBA', (fw, fh), (0, 0, 0, 0))
    d = ImageDraw.Draw(band)
    d.polygon([(0, top + h * 0.35), (fw, top), (fw, top + h), (0, top + h * 1.3)], fill=(252, 250, 246, 255))
    d.line([(0, top + h * 0.35), (fw, top)], fill=(60, 40, 60, 255), width=max(2, fw // 160))
    d.line([(0, top + h * 1.3), (fw, top + h)], fill=(60, 40, 60, 255), width=max(2, fw // 160))
    f = jp_font(int(h * 0.78))
    txt = '推し'
    bb = d.textbbox((0, 0), txt, font=f)
    cy = top + h * 0.62
    d.text((fw * 0.47 - (bb[2] - bb[0]) / 2 - bb[0], cy - (bb[3] - bb[1]) / 2 - bb[1]), txt, font=f, fill=(220, 20, 50, 255))
    # only where the head is (a hair wider, so the band sits on it)
    mask = face.split()[3].point(lambda v: 255 if v > 20 else 0).filter(ImageFilter.MaxFilter(9))
    band.putalpha(Image.composite(band.split()[3], Image.new('L', band.size, 0), mask))
    im.alpha_composite(band)
    # knot on the right edge, two tails flying out
    d = ImageDraw.Draw(im)
    kx, ky = int(fw * 0.955), int(top + h * 0.55)
    ol, wdt = (60, 40, 60, 255), max(2, fw // 160)
    d.polygon([(kx, ky - h * 0.15), (kx + pad * 0.95, ky - h * 1.25), (kx + pad * 0.8, ky - h * 0.5)], fill=(248, 246, 240, 255), outline=ol, width=wdt)
    d.polygon([(kx, ky + h * 0.15), (kx + pad * 0.9, ky + h * 0.55), (kx + pad * 0.62, ky + h * 1.05)], fill=(248, 246, 240, 255), outline=ol, width=wdt)
    r = h * 0.42
    d.ellipse([kx - r, ky - r, kx + r, ky + r], fill=(244, 242, 236, 255), outline=ol, width=wdt)
    return im


def mascot(size):
    """The NEET: tired happy-crying face, oshi headband, a penlight up in each hand."""
    S = size
    im = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    # penlights crossed behind the head: grips hidden, glowing tubes out on both sides
    for color, ang, side in ((PINK, 34, 'left'), (CYAN, -34, 'right')):
        p = penlight(int(S * 0.7), color, ang)
        x = int(S * 0.0) if side == 'left' else S - p.size[0]
        im.alpha_composite(p, (x, int(S * 0.8) - p.size[1]))
    fs = int(S * 0.64)
    face = quiz_emoji.image('🥲', fs)
    fx, fy = (S - fs) // 2 - int(S * 0.04), int(S * 0.2)
    im.alpha_composite(with_shadow(face, blur=14, offset=(0, 12), alpha=110), (fx - 40, fy - 40))  # with_shadow pads 40 px
    im.alpha_composite(headband(face), (fx, fy))
    return im


def avatar():
    size = 800
    img = gradient(size, size, NIGHT_TOP, NIGHT_BOT)
    img.alpha_composite(glow(size, PINK, 90, 150), (-220, 180))
    img.alpha_composite(glow(size, CYAN, 90, 120), (240, 200))
    m = mascot(760)
    img.alpha_composite(m, ((size - m.size[0]) // 2, 30))
    return img


def banner():
    w, h = 2560, 1440
    img = gradient(w, h, NIGHT_TOP, NIGHT_BOT)
    for color, (x, y), a in ((PINK, (-300, 300), 150), (CYAN, (1900, 250), 120), (PINK, (1300, 700), 70)):
        img.alpha_composite(glow(1100, color, 140, a), (x, y))
    # the NEET's room around the safe area (visible on TV / desktop only)
    icons = ['🍜', '💸', '🎧', '🎤', '💿', '🎟️', '🧸', '✨', '🍜', '💜', '📼', '🎮']
    spots = [(260, 330), (520, 1120), (2300, 330), (2040, 1120), (180, 900), (2380, 900),
             (820, 230), (1740, 230), (760, 1240), (1800, 1240), (420, 620), (2140, 620)]
    for e, (x, y) in zip(icons, spots):
        im = quiz_emoji.image(e, 150)
        if im is not None:
            im = im.rotate(((x * 7 + y) % 40) - 20, resample=Image.BICUBIC, expand=True)
            im.putalpha(im.split()[3].point(lambda v: int(v * 0.6)))
            img.alpha_composite(im, (x - im.size[0] // 2, y - im.size[1] // 2))
    # safe area (1546 x 423, centred): mascot + name + the story in one line
    m = mascot(380)
    title = text_layer('MR. WORLDWIDEWEBSTER', 120, stroke=11, stroke_fill=NAVY, max_w=1000)
    sub = text_layer('broke NEET  ·  daily fan edits for my oshi', 54, fill=GOLD, stroke=7, stroke_fill=NAVY, max_w=1080)
    tags = text_layer('ANIME  ·  K-POP  ·  J-POP  ·  C-POP  ACAPELLA EDITS', 40, fill=(255, 255, 255), stroke=5,
                      stroke_fill=NAVY, max_w=1080)
    block_w = m.size[0] + 30 + max(title.size[0], sub.size[0], tags.size[0])
    x0 = (w - block_w) // 2
    img.alpha_composite(m, (x0, (h - m.size[1]) // 2 - 10))
    tx = x0 + m.size[0] + 30
    img.alpha_composite(title, (tx, h // 2 - title.size[1] - 6))
    img.alpha_composite(sub, (tx + 4, h // 2 + 8))
    img.alpha_composite(tags, (tx + 6, h // 2 + 8 + sub.size[1] + 6))
    return img


def main():
    os.makedirs(OUT, exist_ok=True)
    avatar().convert('RGB').save(os.path.join(OUT, 'avatar.png'))
    banner().convert('RGB').save(os.path.join(OUT, 'banner.png'), optimize=True)
    print('written to', OUT)


if __name__ == '__main__':
    main()
