#!/usr/bin/env python3
"""
Render one World Quiz short: 1080x1920, 30 fps, ~25 s, original graphics only.

    python core/quiz/render_quiz.py --format flag --out quiz.mp4 [--seed N]
        [--theme world|asia|europe|africa|americas] [--avoid JP,FR,...] [--rounds 3|4|5]
        [--no-voice] [--no-music] [--voice en-US-AndrewNeural]

The last stdout line is JSON: {"ok": true, "path": ..., "duration": ..., "plan": {...}}

Frames are drawn with Pillow and piped to ffmpeg; audio (voice, SFX, music)
is mixed in numpy (see sound.py). Layout keeps everything important inside
the Shorts safe area (top ~180 px and bottom ~420 px carry YouTube's UI).
"""
import argparse
import json
import math
import os
import random
import subprocess
import sys
import tempfile
import time

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import geo  # noqa: E402
import plan as planner  # noqa: E402
import sound  # noqa: E402

ASSETS = os.path.join(HERE, 'assets')
FONT_PATH = os.path.join(ASSETS, 'fonts', 'LilitaOne-Regular.ttf')
W, H, FPS = 1080, 1920, 30

NAVY = (11, 21, 51)
WHITE = (255, 255, 255)
ACCENT = {'flag': (255, 200, 61), 'shape': (61, 220, 151), 'capital': (255, 120, 110),
          'bigger': (79, 195, 247), 'crowd': (190, 150, 255)}
LEVEL_COLOR = {'easy': (61, 220, 132), 'medium': (255, 197, 61), 'hard': (255, 128, 64), 'insane': (255, 64, 96)}
# background gradients (top, bottom) per format, so formats look distinct in the feed
BG = {'flag': ((10, 22, 58), (28, 52, 120)), 'shape': ((6, 36, 44), (14, 88, 92)),
      'capital': ((44, 12, 40), (104, 34, 78)), 'bigger': ((8, 26, 60), (20, 78, 130)),
      'crowd': ((30, 14, 64), (70, 40, 140))}
TITLE = {'flag': ('GUESS THE FLAG', 'EASY TO IMPOSSIBLE'), 'shape': ('GUESS THE COUNTRY', 'FROM ITS SHAPE'),
         'capital': ('NAME THE CAPITAL', 'EASY TO IMPOSSIBLE'), 'bigger': ('WHICH IS BIGGER?', 'REAL SIZE, NOT THE MAP'),
         'crowd': ('MORE PEOPLE?', 'WHICH COUNTRY HAS MORE')}
THEME_TAG = {'asia': 'ASIA EDITION', 'europe': 'EUROPE EDITION', 'africa': 'AFRICA EDITION', 'americas': 'AMERICAS EDITION'}
INTRO_LINE = {'flag': 'Guess the flag in three seconds!', 'shape': 'Guess the country by its shape!',
              'capital': 'Name the capital city!', 'bigger': 'Which country is actually bigger?',
              'crowd': 'Which country has more people?'}

Q_TIME = {'single': 3.0, 'pair': 3.3}
REVEAL_MIN = {'single': 1.45, 'pair': 2.2}
OUTRO = 2.7

_fonts = {}


def font(size):
    size = int(size)
    if size not in _fonts:
        _fonts[size] = ImageFont.truetype(FONT_PATH, size)
    return _fonts[size]


def clamp(x, a=0.0, b=1.0):
    return max(a, min(b, x))


def ease_out_cubic(x):
    x = clamp(x)
    return 1 - (1 - x) ** 3


def ease_out_back(x, s=1.6):
    x = clamp(x)
    return 1 + (s + 1) * (x - 1) ** 3 + s * (x - 1) ** 2


def fmt_num(n):
    if n is None:
        return '?'
    if n >= 1e9:
        return f'{n / 1e9:.2f}B'.replace('.00B', 'B')
    if n >= 1e6:
        v = n / 1e6
        return f'{v:.0f}M' if v >= 100 else f'{v:.1f}M'.replace('.0M', 'M')
    if n >= 1e3:
        return f'{n / 1e3:.0f}K'
    return str(int(n))


def fmt_area(km2):
    if km2 >= 1e6:
        return f'{km2 / 1e6:.1f}M km²'.replace('.0M', 'M')
    return f'{int(round(km2, -3)):,} km²' if km2 >= 10000 else f'{int(km2):,} km²'


# ─── drawing helpers ─────────────────────────────────────────────────────────

def text_layer(text, size, fill=WHITE, stroke=0, stroke_fill=NAVY, max_w=None, min_size=30):
    """Tight RGBA image of a single line of text, shrunk to fit max_w."""
    while True:
        f = font(size)
        bbox = f.getbbox(text, stroke_width=stroke)
        tw = bbox[2] - bbox[0]
        if not max_w or tw <= max_w or size <= min_size:
            break
        size = max(min_size, int(size * max_w / tw) - 1)
    pad = stroke + 6
    img = Image.new('RGBA', (bbox[2] - bbox[0] + 2 * pad, bbox[3] - bbox[1] + 2 * pad), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((pad - bbox[0], pad - bbox[1]), text, font=f, fill=fill,
                             stroke_width=stroke, stroke_fill=stroke_fill)
    return img


def pill(text, size, bg, fg=NAVY, pad_x=34, pad_y=16, max_w=None, radius=None):
    t = text_layer(text, size, fill=fg, max_w=(max_w - 2 * pad_x) if max_w else None)
    w, h = t.size[0] + 2 * pad_x, t.size[1] + 2 * pad_y
    img = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(img).rounded_rectangle([0, 0, w - 1, h - 1], radius=radius or h // 2, fill=bg)
    img.alpha_composite(t, ((w - t.size[0]) // 2, (h - t.size[1]) // 2))
    return img


def with_shadow(img, blur=18, offset=(0, 14), alpha=150, spread=0):
    pad = blur * 2 + max(abs(offset[0]), abs(offset[1])) + spread
    out = Image.new('RGBA', (img.size[0] + 2 * pad, img.size[1] + 2 * pad), (0, 0, 0, 0))
    a = img.split()[3]
    if spread:
        a = a.filter(ImageFilter.MaxFilter(spread * 2 + 1))
    sh = Image.new('RGBA', img.size, (0, 0, 0, 255))
    sh.putalpha(a.point(lambda v: v * alpha // 255))
    out.alpha_composite(sh, (pad + offset[0], pad + offset[1]))
    out = out.filter(ImageFilter.GaussianBlur(blur))
    out.alpha_composite(img, (pad, pad))
    return out


def load_flag(iso2):
    return Image.open(os.path.join(ASSETS, 'flags', iso2.lower() + '.png')).convert('RGBA')


def flag_card(iso2, max_w, max_h, border=12, radius=22):
    flag = load_flag(iso2)
    s = min((max_w - 2 * border) / flag.size[0], (max_h - 2 * border) / flag.size[1])
    flag = flag.resize((max(1, int(flag.size[0] * s)), max(1, int(flag.size[1] * s))), Image.LANCZOS)
    w, h = flag.size[0] + 2 * border, flag.size[1] + 2 * border
    card = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(card).rounded_rectangle([0, 0, w - 1, h - 1], radius=radius, fill=WHITE)
    m = Image.new('L', flag.size, 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, flag.size[0] - 1, flag.size[1] - 1], radius=max(4, radius - border), fill=255)
    fa = flag.split()[3]
    flag.putalpha(Image.fromarray(np.minimum(np.array(fa), np.array(m))))
    card.alpha_composite(flag, (border, border))
    return with_shadow(card)


def silhouette(mask, fill, outline=WHITE, outline_px=6, texture=None):
    """Coloured (or flag-textured) country shape with a white outline."""
    pad = outline_px + 2
    m = Image.new('L', (mask.size[0] + 2 * pad, mask.size[1] + 2 * pad), 0)
    m.paste(mask, (pad, pad))
    grown = m.filter(ImageFilter.MaxFilter(outline_px * 2 + 1)) if outline_px else m
    img = Image.new('RGBA', m.size, outline + (0,))
    img.putalpha(grown)
    if texture is not None:
        tx = texture.copy()
        s = max(m.size[0] / tx.size[0], m.size[1] / tx.size[1])
        tx = tx.resize((int(tx.size[0] * s) + 1, int(tx.size[1] * s) + 1), Image.LANCZOS)
        tx = tx.crop(((tx.size[0] - m.size[0]) // 2, (tx.size[1] - m.size[1]) // 2,
                      (tx.size[0] - m.size[0]) // 2 + m.size[0], (tx.size[1] - m.size[1]) // 2 + m.size[1]))
        tx.putalpha(m)
        img.alpha_composite(tx)
    else:
        body = Image.new('RGBA', m.size, fill + (255,))
        body.putalpha(m)
        img.alpha_composite(body)
    return with_shadow(img, blur=14, offset=(0, 12), alpha=140)


def ring(size, frac, color, width=16, bg=(255, 255, 255, 60)):
    ss = 3
    big = Image.new('RGBA', (size * ss, size * ss), (0, 0, 0, 0))
    d = ImageDraw.Draw(big)
    box = [width * ss // 2, width * ss // 2, size * ss - width * ss // 2, size * ss - width * ss // 2]
    d.ellipse(box, fill=(11, 21, 51, 170))
    d.arc(box, 0, 360, fill=bg, width=width * ss)
    if frac > 0:
        d.arc(box, -90, -90 + 360 * frac, fill=color + (255,), width=width * ss)
    return big.resize((size, size), Image.LANCZOS)


def scaled(img, s):
    if abs(s - 1) < 1e-3:
        return img
    return img.resize((max(1, int(img.size[0] * s)), max(1, int(img.size[1] * s))), Image.BILINEAR)


def faded(img, a):
    if a >= 0.999:
        return img
    out = img.copy()
    out.putalpha(img.split()[3].point(lambda v: int(v * a)))
    return out


def put(frame, img, cx, cy, s=1.0, a=1.0):
    if img is None or a <= 0.01 or s <= 0.01:
        return
    _put_clipped(frame, faded(scaled(img, s), a), cx, cy)


def _put_clipped(frame, im, cx, cy):
    x, y = int(cx - im.size[0] / 2), int(cy - im.size[1] / 2)
    sx, sy = max(0, -x), max(0, -y)
    ex, ey = min(im.size[0], W - x), min(im.size[1], H - y)
    if ex <= sx or ey <= sy:
        return
    frame.alpha_composite(im.crop((sx, sy, ex, ey)), (x + sx, y + sy))


def check_mark(size, color):
    ss = 3
    img = Image.new('RGBA', (size * ss, size * ss), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([0, 0, size * ss - 1, size * ss - 1], fill=color + (255,))
    pts = [(0.27, 0.53), (0.44, 0.69), (0.74, 0.35)]
    d.line([(x * size * ss, y * size * ss) for x, y in pts], fill=WHITE + (255,), width=int(size * ss * 0.12), joint='curve')
    return img.resize((size, size), Image.LANCZOS)


# ─── background ──────────────────────────────────────────────────────────────

def build_background(fmt, rng):
    top, bot = BG[fmt]
    grad = np.zeros((H, 1, 3), dtype=np.float32)
    for c in range(3):
        grad[:, 0, c] = np.linspace(top[c], bot[c], H)
    map_w = 2160
    dots = geo.dot_world(map_w, 1080, spacing=20, radius=3)
    tint = ACCENT[fmt]
    strip = np.repeat(grad, map_w * 2, axis=1)
    d = np.array(dots, dtype=np.float32) / 255.0 * 0.13
    y0 = 520
    for k in range(2):
        region = strip[y0:y0 + 1080, k * map_w:(k + 1) * map_w]
        for c in range(3):
            region[:, :, c] = region[:, :, c] * (1 - d) + tint[c] * d
    # soft spotlight behind the main card
    yy, xx = np.mgrid[0:H, 0:1]
    spot = np.exp(-((yy - 880) / 520.0) ** 2) * 0.10
    strip = strip * (1 - spot[..., None]) + 255 * spot[..., None]
    return Image.fromarray(np.clip(strip, 0, 255).astype(np.uint8), 'RGB').convert('RGBA'), map_w


# ─── the renderer ────────────────────────────────────────────────────────────

class QuizRenderer:
    def __init__(self, plan, voice=True, music=True, voice_name=None, workdir=None):
        self.plan = plan
        self.fmt = plan['format']
        self.kind = 'pair' if self.fmt in ('bigger', 'crowd') else 'single'
        self.rng = random.Random(plan.get('seed'))
        self.accent = ACCENT[self.fmt]
        self.use_voice = voice
        self.use_music = music
        self.voice_name = voice_name
        self.workdir = workdir or tempfile.mkdtemp(prefix='quiz-')
        self.pop_offset = 0.0

    # ── timeline ──
    def build_timeline(self, answer_durs):
        t = 0.0
        segs = []
        for i, r in enumerate(self.plan['rounds']):
            q = Q_TIME[self.kind]
            rev = max(REVEAL_MIN[self.kind], (answer_durs[i] or 0) + 0.45)
            segs.append({'i': i, 'start': t, 'reveal': t + q, 'end': t + q + rev})
            t += q + rev
        self.segs = segs
        self.outro_start = t
        self.duration = t + OUTRO

    # ── static layers ──
    def prepare(self):
        fmt, rounds = self.fmt, self.plan['rounds']
        title, sub = TITLE[fmt]
        if self.plan.get('theme') in THEME_TAG and fmt in ('flag', 'shape', 'capital'):
            sub = THEME_TAG[self.plan['theme']]
        self.title = text_layer(title, 116, stroke=10, max_w=980)
        self.subtitle = text_layer(sub, 46, fill=self.accent, stroke=6, max_w=900)
        self.levels = []
        for i, r in enumerate(rounds):
            if self.kind == 'single':
                label = f"LEVEL {i + 1}  ·  {r['levelLabel']}"
                col = LEVEL_COLOR[r['level']]
            else:
                label = f"ROUND {i + 1} / {len(rounds)}"
                col = self.accent
            self.levels.append(pill(label, 44, col, max_w=700))
        self.cards_q, self.cards_a, self.answers, self.subs = [], [], [], []
        self.pair_layers = []
        for r in rounds:
            if self.kind == 'single':
                self._prepare_single(r)
            else:
                self._prepare_pair(r)
        self.ring_size = 190
        self.outro_layers = {
            'l1': text_layer('HOW MANY', 128, stroke=10),
            'l2': text_layer('DID YOU GET?', 128, stroke=10),
            'score': text_layer(f"?/{len(rounds)}", 300, fill=self.accent, stroke=14),
            'cta': pill('COMMENT YOUR SCORE', 64, self.accent, max_w=940),
            'follow': text_layer('NEW QUIZ EVERY DAY', 52, fill=(255, 255, 255, 230), stroke=6),
        }

    def _prepare_single(self, r):
        fmt = self.fmt
        if fmt == 'flag':
            q = flag_card(r['iso2'], 860, 560)
            a = q
            sub = f"NOT {r['decoy'].upper()}!" if r.get('decoy') else (f"CAPITAL: {r['capital'].upper()}" if r.get('capital') else r.get('continent', '').upper())
        elif fmt == 'shape':
            mask, _ = geo.fit_mask(r['numeric'], 800, 640)
            q = silhouette(mask, self.accent)
            a = silhouette(mask, self.accent, texture=load_flag(r['iso2']))
            sub = f"POPULATION: {fmt_num(r['population'])}" if r.get('population') else (r.get('continent') or '').upper()
        else:  # capital
            card = flag_card(r['iso2'], 620, 380)
            ask = text_layer('THE CAPITAL OF', 54, fill=(255, 255, 255, 235), stroke=6)
            name = text_layer(r['name'].upper(), 104, stroke=9, max_w=940)
            w = max(card.size[0], ask.size[0], name.size[0])
            h = card.size[1] + ask.size[1] + name.size[1] - 30
            q = Image.new('RGBA', (w, h), (0, 0, 0, 0))
            q.alpha_composite(card, ((w - card.size[0]) // 2, 0))
            q.alpha_composite(ask, ((w - ask.size[0]) // 2, card.size[1] - 40))
            q.alpha_composite(name, ((w - name.size[0]) // 2, card.size[1] - 40 + ask.size[1] - 6))
            a = q
            sub = f"NOT {r['decoy'].upper()}!" if r.get('decoy') else None
        self.cards_q.append(q)
        self.cards_a.append(a)
        self.answers.append(with_shadow(pill(r['answer'].upper(), 92, WHITE, fg=NAVY, max_w=980, pad_y=20), blur=10, offset=(0, 8)))
        self.subs.append(text_layer(sub, 50, fill=LEVEL_COLOR['medium'] if r.get('decoy') else WHITE, stroke=6, max_w=900) if sub else None)

    def _prepare_pair(self, r):
        L, R = r['left'], r['right']
        box_w, box_h = 440, 470
        layer = {'names': [], 'q': [], 'a': [], 'labels': [], 'true_scale': [1.0, 1.0]}
        if self.fmt == 'bigger':
            ext = [geo.extent_km(L['numeric']), geo.extent_km(R['numeric'])]
            fit = [max(e[0] / (box_w - 20), e[1] / (box_h - 20)) for e in ext]   # km/px to fit each box
            common = max(fit)                                                   # both at the same km/px
            for side, c in (('left', L), ('right', R)):
                k = 0 if side == 'left' else 1
                mask = geo.mask_at_scale(c['numeric'], fit[k])
                layer['q'].append(silhouette(mask, self.accent, outline_px=5))
                layer['a'].append(silhouette(mask, self.accent, outline_px=5, texture=load_flag(c['iso2'])))
                layer['true_scale'][k] = fit[k] / common
                layer['labels'].append(pill(fmt_area(c['areaKm2']), 46, WHITE, max_w=460))
        else:
            for c in (L, R):
                card = flag_card(c['iso2'], 430, 300, border=10, radius=18)
                layer['q'].append(card)
                layer['a'].append(card)
                layer['labels'].append(None)  # population counts up, drawn per frame
        for c in (L, R):
            layer['names'].append(text_layer(c['name'].upper(), 66, stroke=7, max_w=470))
        layer['vs'] = self._vs_badge()
        layer['check'] = check_mark(92, (46, 204, 113))
        self.pair_layers.append(layer)
        self.answers.append(None)
        self.subs.append(None)

    def _vs_badge(self):
        size = 124
        img = Image.new('RGBA', (size * 3, size * 3), (0, 0, 0, 0))
        ImageDraw.Draw(img).ellipse([0, 0, size * 3 - 1, size * 3 - 1], fill=self.accent + (255,))
        img = img.resize((size, size), Image.LANCZOS)
        t = text_layer('VS', 62, fill=NAVY)
        img.alpha_composite(t, ((size - t.size[0]) // 2, (size - t.size[1]) // 2 + 2))
        return with_shadow(img, blur=8, offset=(0, 6))

    # ── audio ──
    def voice_lines(self):
        rounds = self.plan['rounds']
        lines = [INTRO_LINE[self.fmt]]
        for r in rounds:
            if self.fmt == 'bigger':
                lines.append(f"{r['answer']}! About {r['ratio']:g} times bigger.")
            elif self.fmt == 'crowd':
                lines.append(f"{r['answer']}! About {r['ratio']:g} times more people.")
            else:
                lines.append(f"{r['answer']}!")
        lines.append('Last one!')
        lines.append('How many did you get? Comment your score!')
        return lines

    def make_audio(self, clips):
        n_r = len(self.plan['rounds'])
        voice = []
        if clips[0] is not None:
            voice.append((0.15, clips[0]))
        for i, s in enumerate(self.segs):
            if clips[1 + i] is not None:
                voice.append((s['reveal'] + 0.08, clips[1 + i]))
        if clips[1 + n_r] is not None and n_r >= 3:
            voice.append((self.segs[-1]['start'] + 0.05, clips[1 + n_r]))
        if clips[2 + n_r] is not None:
            voice.append((self.outro_start + 0.2, clips[2 + n_r]))
        fx = []
        wh = sound.whoosh()
        tk, tkh = sound.tick(), sound.tick(high=True)
        for s in self.segs:
            if s['start'] > 0:
                fx.append((s['start'] - 0.05, wh, 0.35))
            q = s['reveal'] - s['start']
            k = 0.0
            while k < q - 0.05:
                last = (q - k) <= 1.0
                fx.append((s['start'] + k, tkh if last else tk, 0.5 if last else 0.38))
                k += 0.5 if not last else 0.25
            fx.append((s['reveal'], sound.ding(), 0.6))
            fx.append((s['reveal'] + 0.02, sound.pop(), 0.35))
        fx.append((self.outro_start, wh, 0.4))
        fx.append((self.outro_start + 0.1, sound.chime(), 0.5))
        m = sound.music(self.duration, seed=self.plan.get('seed') or 0,
                        bpm=self.rng.choice([104, 108, 112, 116, 120])) if self.use_music else None
        return sound.mix(self.duration, fx, voice, m)

    # ── frames ──
    def draw_header(self, frame, i, t_local, t):
        # title + subtitle stay; the level pill pops on every new round
        put(frame, self.title, W / 2, 262)
        put(frame, self.subtitle, W / 2, 352)
        if i is not None:
            s = ease_out_back((t_local + self.pop_offset) / 0.3)
            put(frame, self.levels[i], W / 2, 436, s=0.6 + 0.4 * s)
            self.draw_progress(frame, i)

    def draw_progress(self, frame, i):
        n = len(self.plan['rounds'])
        seg_w, gap, y = 118, 16, 512
        total = n * seg_w + (n - 1) * gap
        d = ImageDraw.Draw(frame)
        x = (W - total) / 2
        for k in range(n):
            col = self.accent + (255,) if k < i else (WHITE + (255,) if k == i else (255, 255, 255, 45))
            d.rounded_rectangle([x, y, x + seg_w, y + 14], radius=7, fill=col)
            x += seg_w + gap

    def draw_single(self, frame, i, t_local, seg):
        q = seg['reveal'] - seg['start']
        cy = 900 if self.fmt != 'capital' else 880
        pop_in = ease_out_back((t_local + self.pop_offset) / 0.32)
        s = 0.86 + 0.14 * pop_in
        if t_local < q:
            put(frame, self.cards_q[i], W / 2, cy, s=s)
            self.draw_timer(frame, q - t_local, q)
        else:
            rt = t_local - q
            if self.fmt == 'shape':
                blend = clamp(rt / 0.25)
                put(frame, self.cards_q[i], W / 2, cy, a=1 - blend)
                put(frame, self.cards_a[i], W / 2, cy, a=blend)
            else:
                bump = 1 + 0.05 * math.sin(clamp(rt / 0.3) * math.pi)
                put(frame, self.cards_a[i], W / 2, cy, s=bump)
            ps = ease_out_back(rt / 0.3)
            put(frame, self.answers[i], W / 2, 1320, s=max(0.01, ps))
            if self.subs[i] is not None and rt > 0.18:
                put(frame, self.subs[i], W / 2, 1440 + 20 * (1 - ease_out_cubic((rt - 0.18) / 0.25)), a=clamp((rt - 0.18) / 0.2))

    def draw_pair(self, frame, i, t_local, seg):
        r = self.plan['rounds'][i]
        L = self.pair_layers[i]
        q = seg['reveal'] - seg['start']
        xs = (292, 788)
        cy = 860
        pop_in = ease_out_back((t_local + self.pop_offset) / 0.32)
        win = 0 if r['winner'] == 'left' else 1
        for k in range(2):
            s_in = 0.86 + 0.14 * pop_in
            if t_local < q:
                put(frame, L['q'][k], xs[k], cy, s=s_in)
                put(frame, L['names'][k], xs[k], 1150)
            else:
                rt = t_local - q
                g = ease_out_cubic(rt / 0.55)
                s = 1 + (L['true_scale'][k] - 1) * g if self.fmt == 'bigger' else 1.0
                img = L['a'][k] if self.fmt == 'bigger' and rt > 0.12 else L['q'][k]
                alpha = 1.0 if k == win else 1 - 0.45 * clamp(rt / 0.3)
                put(frame, img, xs[k], cy, s=max(0.02, s), a=alpha)
                put(frame, L['names'][k], xs[k], 1150, a=alpha)
                if self.fmt == 'bigger':
                    put(frame, L['labels'][k], xs[k], 1242, a=clamp((rt - 0.3) / 0.25))
                else:
                    side = r['left'] if k == 0 else r['right']
                    val = (side.get('population') or 0) * ease_out_cubic(rt / 0.7)
                    lab = pill(fmt_num(val), 50, WHITE if k == win else (200, 205, 220), max_w=460)
                    put(frame, lab, xs[k], 1242, a=clamp(rt / 0.15))
                if k == win and rt > 0.25:
                    put(frame, L['check'], xs[k] + 150, cy - 170, s=ease_out_back((rt - 0.25) / 0.3))
        if t_local < q:
            put(frame, L['vs'], W / 2, cy, s=0.8 + 0.2 * pop_in)
            self.draw_timer(frame, q - t_local, q, y=1345)

    def draw_timer(self, frame, remaining, total, y=1330):
        frac = clamp(remaining / total)
        last = remaining <= 1.0
        col = (255, 72, 96) if last else self.accent
        pulse = 1 + 0.06 * max(0.0, 1 - ((total - remaining) % 1.0) / 0.25)
        rg = ring(self.ring_size, frac, col)
        put(frame, rg, W / 2, y, s=pulse)
        num = text_layer(str(max(1, math.ceil(remaining - 1e-6))), 104, fill=WHITE, stroke=0)
        put(frame, num, W / 2, y + 2, s=pulse)

    def draw_outro(self, frame, t):
        o = self.outro_layers
        a = ease_out_back(t / 0.35)
        put(frame, o['l1'], W / 2, 640, s=max(0.01, a))
        put(frame, o['l2'], W / 2, 780, s=max(0.01, ease_out_back((t - 0.08) / 0.35)))
        pulse = 1 + 0.05 * math.sin(t * 2 * math.pi * 1.6)
        put(frame, o['score'], W / 2, 1030, s=max(0.01, ease_out_back((t - 0.2) / 0.4)) * pulse)
        put(frame, o['cta'], W / 2, 1280, s=max(0.01, ease_out_back((t - 0.45) / 0.35)))
        put(frame, o['follow'], W / 2, 1400, a=clamp((t - 0.7) / 0.3))

    def frame_at(self, t):
        off = int(t * 14) % self.map_w
        frame = self.bg.crop((off, 0, off + W, H))
        if t >= self.outro_start:
            put(frame, self.title, W / 2, 262, a=1 - clamp((t - self.outro_start) / 0.25))
            self.draw_outro(frame, t - self.outro_start)
            return frame
        seg = next(s for s in self.segs if t < s['end'])
        i = seg['i']
        tl = t - seg['start']
        self.pop_offset = 0.35 if i == 0 else 0.0  # frame 0 is the feed thumbnail: no pop-in
        self.draw_header(frame, i, tl, t)
        if self.kind == 'single':
            self.draw_single(frame, i, tl, seg)
        else:
            self.draw_pair(frame, i, tl, seg)
        return frame

    # ── output ──
    def render(self, out_path):
        t0 = time.time()
        lines = self.voice_lines()
        clips = sound.tts_lines(lines, voices=[self.voice_name] if self.voice_name else None,
                                workdir=self.workdir) if self.use_voice else [None] * len(lines)
        n_r = len(self.plan['rounds'])
        answer_durs = [(len(c) / sound.SR) if c is not None else 0 for c in clips[1:1 + n_r]]
        self.build_timeline(answer_durs)
        self.prepare()
        self.bg, self.map_w = build_background(self.fmt, self.rng)
        audio = self.make_audio(clips)
        wav = os.path.join(self.workdir, 'audio.wav')
        sound.write_wav(wav, audio)
        cmd = ['ffmpeg', '-y', '-v', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{W}x{H}', '-r', str(FPS),
               '-i', '-', '-i', wav, '-c:v', 'libx264', '-preset', 'medium', '-crf', '19', '-pix_fmt', 'yuv420p',
               '-profile:v', 'high', '-c:a', 'aac', '-b:a', '192k', '-ar', '44100', '-shortest',
               '-movflags', '+faststart', out_path]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        n = int(round(self.duration * FPS))
        for k in range(n):
            proc.stdin.write(self.frame_at(k / FPS).convert('RGB').tobytes())
        proc.stdin.close()
        if proc.wait() != 0:
            raise RuntimeError('ffmpeg failed')
        return {'duration': round(self.duration, 2), 'frames': n, 'renderSec': round(time.time() - t0, 1),
                'voice': sum(c is not None for c in clips), 'voiceLines': len(clips)}


def main():
    # JSON result must survive non-UTF-8 consoles (Windows cp932/cp1252)
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument('--format', default='flag', choices=planner.FORMATS)
    ap.add_argument('--out', required=True)
    ap.add_argument('--seed', type=int, default=None)
    ap.add_argument('--theme', default='world')
    ap.add_argument('--avoid', default='')
    ap.add_argument('--rounds', type=int, default=None, help='3 (quick) .. 5; pair formats max 4')
    ap.add_argument('--no-voice', action='store_true')
    ap.add_argument('--no-music', action='store_true')
    ap.add_argument('--voice', default=None)
    ap.add_argument('--frame', type=float, default=None, help='only save a PNG of this timestamp')
    args = ap.parse_args()
    seed = args.seed if args.seed is not None else random.randint(1, 10 ** 9)
    avoid = [x.strip().upper() for x in args.avoid.split(',') if x.strip()]
    p = planner.make_plan(args.format, seed=seed, avoid=avoid, theme=args.theme, rounds=args.rounds)
    if len(p['rounds']) < 3:
        print(json.dumps({'ok': False, 'reason': 'not enough questions', 'plan': p}))
        sys.exit(1)
    r = QuizRenderer(p, voice=not args.no_voice, music=not args.no_music, voice_name=args.voice)
    if args.frame is not None:
        r.build_timeline([1.0] * len(p['rounds']))
        r.prepare()
        r.bg, r.map_w = build_background(r.fmt, r.rng)
        r.frame_at(args.frame).convert('RGB').save(args.out)
        print(json.dumps({'ok': True, 'path': args.out, 'plan': p}))
        return
    info = r.render(args.out)
    print(json.dumps({'ok': True, 'path': os.path.abspath(args.out), **info, 'plan': p}, ensure_ascii=False))


if __name__ == '__main__':
    main()
