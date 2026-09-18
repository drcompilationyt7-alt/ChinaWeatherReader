#!/usr/bin/env python3
"""
Profile picture (800x800) and banner (2560x1440) for the Asian Pop Quiz
channel, drawn from scratch: a mochi mascot, Noto emoji (Apache-2.0) and the
OFL Lilita One font. Output: branding/quiz-channel/{avatar,banner}.png

    python scripts/make-quiz-branding.py
"""
import os
import sys

from PIL import Image, ImageDraw, ImageFilter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'core', 'quiz'))
import quiz_emoji  # noqa: E402
from render_quiz import text_layer, with_shadow  # noqa: E402

OUT = os.path.join(ROOT, 'branding', 'quiz-channel')
PINK_TOP, PINK_BOT = (255, 94, 168), (122, 44, 214)
NAVY = (26, 16, 60)
GOLD = (255, 214, 0)


def gradient(w, h, top, bot, diagonal=True):
    img = Image.new('RGB', (w, h))
    px = img.load()
    for y in range(h):
        for x in range(0, w, 1):
            t = ((x / w) * 0.35 + (y / h) * 0.65) if diagonal else y / h
            px[x, y] = tuple(int(top[c] + (bot[c] - top[c]) * t) for c in range(3))
    return img.convert('RGBA')


def mochi(size):
    """Cute mochi mascot with a question-mark sprig, drawn at 3x and downsampled."""
    ss = 3
    S = size * ss
    img = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # soft body: wide rounded blob
    body = [int(S * 0.08), int(S * 0.30), int(S * 0.92), int(S * 0.92)]
    shadow = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).ellipse([body[0], body[1] + S * 0.04, body[2], body[3] + S * 0.04], fill=(60, 10, 80, 110))
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(S * 0.02)))
    d.ellipse(body, fill=(255, 250, 252, 255), outline=NAVY + (255,), width=int(S * 0.022))
    # blush
    for cx in (0.30, 0.70):
        d.ellipse([S * (cx - 0.07), S * 0.66, S * (cx + 0.07), S * 0.73], fill=(255, 150, 190, 255))
    # eyes with highlights
    for cx in (0.37, 0.63):
        d.ellipse([S * (cx - 0.045), S * 0.53, S * (cx + 0.045), S * 0.64], fill=NAVY + (255,))
        d.ellipse([S * (cx - 0.02), S * 0.55, S * (cx + 0.005), S * 0.58], fill=(255, 255, 255, 255))
    # smile
    d.arc([S * 0.44, S * 0.61, S * 0.56, S * 0.70], 20, 160, fill=NAVY + (255,), width=int(S * 0.018))
    # question mark on a little leaf-shaped badge
    badge = [S * 0.60, S * 0.02, S * 0.94, S * 0.36]
    d.ellipse(badge, fill=GOLD + (255,), outline=NAVY + (255,), width=int(S * 0.02))
    q = text_layer('?', int(size * 0.26), fill=NAVY)
    q = q.resize((q.size[0] * ss, q.size[1] * ss), Image.LANCZOS)
    img.alpha_composite(q, (int((badge[0] + badge[2]) / 2 - q.size[0] / 2), int((badge[1] + badge[3]) / 2 - q.size[1] / 2)))
    return img.resize((size, size), Image.LANCZOS)


def avatar():
    size = 800
    img = gradient(size, size, PINK_TOP, PINK_BOT)
    m = mochi(640)
    img.alpha_composite(m, ((size - m.size[0]) // 2, 70))
    return img


def banner():
    w, h = 2560, 1440
    img = gradient(w, h, PINK_TOP, PINK_BOT)
    # sprinkle of topic emoji outside the text area (visible on TV/desktop)
    icons = ['🍡', '🎤', '🍜', '⛩️', '🐉', '🎮', '🌸', '🏯', '🧋', '⭐', '🎧', '🥟']
    spots = [(260, 330), (520, 1120), (2300, 330), (2040, 1120), (180, 900), (2380, 900),
             (820, 230), (1740, 230), (760, 1240), (1800, 1240), (420, 620), (2140, 620)]
    for e, (x, y) in zip(icons, spots):
        im = quiz_emoji.image(e, 150)
        if im is not None:
            im = im.rotate(((x * 7 + y) % 40) - 20, resample=Image.BICUBIC, expand=True)
            faded = im.copy()
            faded.putalpha(im.split()[3].point(lambda v: int(v * 0.55)))
            img.alpha_composite(faded, (x - im.size[0] // 2, y - im.size[1] // 2))
    # safe area (1546 x 423, centred): mascot + name + promise
    m = mochi(340)
    title = text_layer('ASIAN POP QUIZ', 150, stroke=12, stroke_fill=NAVY)
    sub = text_layer('ANIME · K-POP · ASIA  —  A NEW QUIZ EVERY DAY', 52, fill=GOLD, stroke=7, stroke_fill=NAVY)
    block_w = m.size[0] + 40 + max(title.size[0], sub.size[0])
    x0 = (w - block_w) // 2
    img.alpha_composite(with_shadow(m, blur=12, offset=(0, 10), alpha=90), (x0 - 40, (h - m.size[1]) // 2 - 40))
    tx = x0 + m.size[0] + 40
    img.alpha_composite(title, (tx, h // 2 - title.size[1] + 10))
    img.alpha_composite(sub, (tx + 6, h // 2 + 24))
    return img


def main():
    os.makedirs(OUT, exist_ok=True)
    avatar().convert('RGB').save(os.path.join(OUT, 'avatar.png'))
    banner().convert('RGB').save(os.path.join(OUT, 'banner.png'), optimize=True)
    print('written to', OUT)


if __name__ == '__main__':
    main()
