"""
Asian pop culture quiz formats for the quiz channel, drawn with the same
engine as render_quiz.py:

  emoji   guess the anime / K-pop song from 4 emojis        (5 rounds)
  trivia  multiple choice, question read out loud            (5 rounds)
  wyr     would you rather, split screen, "but..." twists    (4 rounds)
  city    guess the city from a dot on the country map       (5 rounds)

A plan is a dict {format, topic, theme, rounds: [...]} built by
pipeline/pop-quiz-content.js (or plan_from_bank() below for local tests).
"""
import json
import math
import os
import random
import textwrap

import numpy as np
from PIL import Image, ImageDraw

import quiz_emoji as emj
import geo
import sound
from render_quiz import (FPS, H, NAVY, W, WHITE, QuizRenderer, clamp, ease_out_back, ease_out_cubic, font, pill,
                         put, ring, text_layer, with_shadow, flag_card, fmt_num, draw_watermark)

HERE = os.path.dirname(os.path.abspath(__file__))
BANK = os.path.join(HERE, 'assets', 'pop-bank.json')

POP_FORMATS = ('emoji', 'trivia', 'wyr', 'city')
GOLD = (255, 214, 0)
GREEN = (46, 204, 113)
PINK = (255, 92, 170)

PALETTE = {  # (top, bottom, accent)
    'emoji': ((58, 8, 64), (150, 28, 110), GOLD),
    'trivia': ((18, 10, 58), (72, 32, 150), (0, 229, 255)),
    'wyr': ((150, 22, 40), (20, 70, 170), WHITE),
    'city': ((70, 6, 16), (150, 24, 36), GOLD),
}
CITY_COUNTRY = {'CN': ('CHINA', '156', (160, 20, 30), GOLD), 'JP': ('JAPAN', '392', (150, 20, 50), WHITE),
                'KR': ('KOREA', '410', (18, 50, 130), (255, 90, 90))}

TOPIC_TITLE = {
    'emoji': {'anime': ('GUESS THE ANIME', 'FROM THE EMOJIS'), 'kpop song': ('GUESS THE K-POP SONG', 'FROM THE EMOJIS'),
              'kdrama': ('GUESS THE K-DRAMA', 'FROM THE EMOJIS'), 'anime character': ('GUESS THE CHARACTER', 'FROM THE EMOJIS')},
    'trivia': {'kpop': ('K-POP QUIZ', 'ONLY REAL FANS GET 5/5'), 'anime': ('ANIME QUIZ', 'EASY TO IMPOSSIBLE'),
               'vtubers': ('VTUBER QUIZ', 'HOW MANY CAN YOU GET?'), 'cdrama': ('C-DRAMA & MOVIE QUIZ', 'EASY TO IMPOSSIBLE'),
               'cities': ('ASIA CITY QUIZ', 'EASY TO IMPOSSIBLE'), 'mixed': ('ASIAN POP QUIZ', 'EASY TO IMPOSSIBLE')},
    'wyr': {'anime': ('WOULD YOU RATHER', 'ANIME EDITION'), 'kpop': ('WOULD YOU RATHER', 'K-POP EDITION'),
            'food': ('WOULD YOU RATHER', 'ASIAN FOOD EDITION'), 'cities': ('WOULD YOU RATHER', 'ASIA TRAVEL EDITION'),
            'mixed': ('WOULD YOU RATHER', 'ASIA EDITION')},
}
INTRO = {'emoji': {'anime': 'Guess the anime from the emojis!', 'kpop song': 'Guess the K-pop song from the emojis!',
                   'kdrama': 'Guess the K-drama from the emojis!', 'anime character': 'Guess the character from the emojis!'},
         'trivia': 'How many can you get right?', 'wyr': None, 'city': 'Guess the city from the map!'}

Q_MIN = {'emoji': 4.0, 'trivia': 4.5, 'wyr': 4.2, 'city': 3.0}
R_MIN = {'emoji': 1.9, 'trivia': 1.6, 'wyr': 2.0, 'city': 1.8}


def wrap_lines(text, size, max_w, max_lines=3, min_size=40):
    """Split text into lines that fit max_w, shrinking the font if needed."""
    while True:
        f = font(size)
        words, lines, cur = text.split(), [], ''
        for w in words:
            t = (cur + ' ' + w).strip()
            if f.getbbox(t)[2] <= max_w or not cur:
                cur = t
            else:
                lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        if len(lines) <= max_lines or size <= min_size:
            return lines, size
        size -= 6


def text_block(text, size, fill=WHITE, stroke=8, max_w=940, max_lines=3, line_gap=8):
    lines, size = wrap_lines(text, size, max_w - 2 * stroke, max_lines)
    imgs = [text_layer(l, size, fill=fill, stroke=stroke, max_w=max_w) for l in lines]
    w = max(i.size[0] for i in imgs)
    h = sum(i.size[1] for i in imgs) - (len(imgs) - 1) * (stroke + 6 - line_gap)
    out = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    y = 0
    for i in imgs:
        out.alpha_composite(i, ((w - i.size[0]) // 2, y))
        y += i.size[1] - (stroke + 6 - line_gap)
    return out


def bar(d, x0, y0, x1, y1, fill):
    """Rounded bar that survives zero width (Pillow needs width > 2 * radius)."""
    r = (y1 - y0) // 2
    if x1 - x0 < 2 * r + 2:
        x1 = x0 + 2 * r + 2
    d.rounded_rectangle([x0, y0, x1, y1], radius=r, fill=fill)


def emoji_img(e, size):
    img = emj.image(e, size)
    if img is None:  # no picture available: fall back to the glyph in the text font
        img = text_layer(e, int(size * 0.7), fill=NAVY, stroke=4, stroke_fill=WHITE)
    return img


def gradient_bg(top, bot, accent, split=False):
    arr = np.zeros((H, W, 3), dtype=np.float32)
    if split:  # would-you-rather: hard split at the "OR"
        arr[:900] = top
        arr[900:] = bot
        shade = np.linspace(0.75, 1.0, H)[:, None, None]
        arr *= shade
    else:
        for c in range(3):
            arr[:, :, c] = np.linspace(top[c], bot[c], H)[:, None]
    dots = np.array(geo.dot_world(2160, 1080, spacing=20, radius=3), dtype=np.float32)[:, :W] / 255.0 * 0.10
    for c in range(3):
        arr[520:1600, :, c] = arr[520:1600, :, c] * (1 - dots) + accent[c] * dots
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), 'RGB').convert('RGBA')


class PopRenderer(QuizRenderer):
    def __init__(self, plan, **kw):
        super().__init__({**plan, 'format': 'flag'}, **kw)  # base init needs a known format
        self.plan = plan
        self.fmt = plan['format']
        self.topic = plan.get('topic', 'mixed')
        self.kind = 'pop'
        if self.fmt == 'city':
            iso = plan['rounds'][0]['iso2']
            cname, _, top, acc = CITY_COUNTRY[iso]
            self.pal = (tuple(max(0, c // 3) for c in top), top, acc)
        else:
            self.pal = PALETTE[self.fmt]
        self.accent = self.pal[2]

    # ── audio lines ──
    def voice_lines(self):
        rounds = self.plan['rounds']
        intro = INTRO[self.fmt]
        if isinstance(intro, dict):
            intro = intro.get(self.topic, 'Guess it from the emojis!')
        ask, ans = [], []
        for r in rounds:
            if self.fmt == 'trivia':
                ask.append(r['question'])
                ans.append(f"{r['options'][r['answer']]}!")
            elif self.fmt == 'wyr':
                ask.append(f"Would you rather {r['a'][0].lower() + r['a'][1:]}, or {r['b'][0].lower() + r['b'][1:]}?")
                tw = r.get('twistA') or r.get('twistB') or ''
                ans.append(f"{tw[0].upper() + tw[1:]}!" if tw else None)
            else:
                ask.append(None)
                ans.append(f"{r['answer'].split(' (')[0]}!")
        outro = 'Which would you pick? Comment below!' if self.fmt == 'wyr' else 'How many did you get? Comment your score!'
        return intro, ask, ans, outro

    def build_pop_timeline(self, ask_d, ans_d):
        t = 0.0
        segs = []
        for i in range(len(self.plan['rounds'])):
            q = max(Q_MIN[self.fmt], (ask_d[i] or 0) + (2.3 if self.fmt in ('trivia', 'wyr') else 0))
            rev = max(R_MIN[self.fmt], (ans_d[i] or 0) + 0.5)
            segs.append({'i': i, 'start': t, 'reveal': t + q, 'end': t + q + rev})
            t += q + rev
        self.segs = segs
        self.outro_start = segs[-1]['end'] if segs else t  # exactly the last round's end (float sums differ)
        self.duration = self.outro_start + 2.8

    # ── static layers ──
    def prepare_pop(self):
        fmt, rounds = self.fmt, self.plan['rounds']
        if fmt == 'city':
            cname = CITY_COUNTRY[rounds[0]['iso2']][0]
            title, sub = 'GUESS THE CITY', f'{cname} EDITION'
        else:
            opts = TOPIC_TITLE[fmt]
            title, sub = opts.get(self.topic) or opts.get('mixed') or next(iter(opts.values()))
        self.title = text_layer(title, 112, stroke=10, max_w=980)
        self.subtitle = text_layer(sub, 46, fill=self.accent, stroke=6, max_w=900)
        self.levels = []
        for i, r in enumerate(rounds):
            if fmt == 'wyr':
                label = f'ROUND {i + 1} / {len(rounds)}'
                col = (255, 255, 255)
            else:
                d = min(3, max(1, int(r.get('difficulty', 2))))
                if i == len(rounds) - 1 and d == 3:
                    word, col = 'IMPOSSIBLE', (255, 64, 96)
                else:
                    word, col = {1: ('EASY', (61, 220, 132)), 2: ('MEDIUM', (255, 197, 61)), 3: ('HARD', (255, 128, 64))}[d]
                label = f"LEVEL {i + 1}  ·  {word}"
            self.levels.append(pill(label, 44, col, max_w=700))
        self.layers = [getattr(self, f'_prep_{fmt}')(r) for r in rounds]
        self.ring_size = 190
        self.outro_layers = {
            'l1': text_layer('WHICH WOULD' if fmt == 'wyr' else 'HOW MANY', 124, stroke=10),
            'l2': text_layer('YOU PICK?' if fmt == 'wyr' else 'DID YOU GET?', 124, stroke=10),
            'score': text_layer('A OR B?' if fmt == 'wyr' else f'?/{len(rounds)}', 230 if fmt == 'wyr' else 300,
                                fill=self.accent if fmt != 'wyr' else GOLD, stroke=14),
            'cta': pill('COMMENT BELOW' if fmt == 'wyr' else 'COMMENT YOUR SCORE', 64, GOLD if fmt == 'wyr' else self.accent, max_w=940),
            'follow': text_layer('NEW QUIZ EVERY DAY', 52, fill=(255, 255, 255, 230), stroke=6),
        }

    def _answer_pill(self, text):
        main, _, sub = text.partition(' (')
        p = with_shadow(pill(main.upper(), 90, WHITE, fg=NAVY, max_w=980, pad_y=20), blur=10, offset=(0, 8))
        s = text_layer(sub.rstrip(')').upper(), 50, fill=self.accent, stroke=6, max_w=900) if sub else None
        return p, s

    def _prep_emoji(self, r):
        ems = r['emojis'][:4]
        size = 190 if len(ems) >= 4 else 220
        gap = 26
        w = len(ems) * size + (len(ems) - 1) * gap + 70
        panel = Image.new('RGBA', (w, size + 70), (0, 0, 0, 0))
        ImageDraw.Draw(panel).rounded_rectangle([0, 0, w - 1, size + 69], radius=40, fill=(255, 255, 255, 235))
        imgs = [emoji_img(e, size) for e in ems]
        ans, sub = self._answer_pill(r['answer'])
        fact = text_layer(r['fact'].upper(), 44, fill=GOLD, stroke=6, max_w=960) if r.get('fact') else None
        return {'panel': with_shadow(panel), 'imgs': imgs, 'size': size, 'gap': gap, 'answer': ans, 'sub': sub, 'fact': fact}

    def _prep_trivia(self, r):
        icon = emoji_img(r.get('emoji') or '❓', 150)
        q = text_block(r['question'], 76, max_w=960, max_lines=3)
        opts = []
        for k, o in enumerate(r['options']):
            label = f"{'ABCD'[k]}   {o}"
            wide = max(pill(label, 58, WHITE, max_w=880, pad_y=18).size[0], 760)
            opts.append({'base': pill(label, 58, WHITE, fg=NAVY, max_w=880, pad_y=18, radius=26, min_w=wide),
                         'good': pill(label, 58, GREEN, fg=WHITE, max_w=880, pad_y=18, radius=26, min_w=wide)})
        return {'icon': icon, 'q': q, 'opts': opts}

    def _prep_wyr(self, r):
        return {
            'ea': emoji_img(r['emojiA'], 230), 'eb': emoji_img(r['emojiB'], 230),
            'ta': text_block(r['a'].upper(), 72, max_w=940, max_lines=2), 'tb': text_block(r['b'].upper(), 72, max_w=940, max_lines=2),
            'wa': text_block(r['twistA'].upper(), 50, fill=GOLD, stroke=6, max_w=920, max_lines=2) if r.get('twistA') else None,
            'wb': text_block(r['twistB'].upper(), 50, fill=GOLD, stroke=6, max_w=920, max_lines=2) if r.get('twistB') else None,
            'or': self._or_badge(),
        }

    def _or_badge(self):
        size = 150
        img = Image.new('RGBA', (size * 3, size * 3), (0, 0, 0, 0))
        ImageDraw.Draw(img).ellipse([0, 0, size * 3 - 1, size * 3 - 1], fill=WHITE + (255,))
        img = img.resize((size, size), Image.LANCZOS)
        t = text_layer('OR', 76, fill=NAVY)
        img.alpha_composite(t, ((size - t.size[0]) // 2, (size - t.size[1]) // 2 + 2))
        return with_shadow(img, blur=10, offset=(0, 6))

    def _prep_city(self, r):
        if not hasattr(self, '_city_map'):
            iso = self.plan['rounds'][0]['iso2']
            numeric = CITY_COUNTRY[iso][1]
            pts = [(c['lon'], c['lat']) for c in self.plan['rounds']]
            mask, xy = geo.fit_mask_points(numeric, 860, 680, pts)
            from render_quiz import silhouette
            self._city_map = silhouette(mask, (245, 245, 245), outline=(255, 255, 255), outline_px=0)
            # silhouette() pads by outline_px + 2 and with_shadow() adds its own padding
            pad = 2 + (self._city_map.size[0] - (mask.size[0] + 4)) // 2
            self._city_xy = [(x + pad, y + pad) for x, y in xy]
            self._city_idx = 0
        i = self._city_idx
        self._city_idx += 1
        ans, sub = self._answer_pill(r['name'])
        fact = text_layer(r['fact'].upper(), 46, fill=self.accent, stroke=6, max_w=960) if r.get('fact') else None
        return {'xy': self._city_xy[i], 'answer': ans, 'fact': fact}

    # ── frames ──
    def draw_round(self, frame, i, tl, seg):
        L = self.layers[i]
        q = seg['reveal'] - seg['start']
        pop = ease_out_back((tl + self.pop_offset) / 0.32)
        getattr(self, f'_draw_{self.fmt}')(frame, i, tl, q, pop, L)

    def _draw_emoji(self, frame, i, tl, q, pop, L):
        cy = 860
        put(frame, L['panel'], W / 2, cy, s=0.9 + 0.1 * pop)
        n = len(L['imgs'])
        total = n * L['size'] + (n - 1) * L['gap']
        x0 = W / 2 - total / 2 + L['size'] / 2
        for k, im in enumerate(L['imgs']):
            t_k = tl + self.pop_offset - 0.12 * k
            s = ease_out_back(t_k / 0.3) if t_k < 0.3 else 1 + 0.04 * math.sin((tl - k * 0.2) * 5)
            put(frame, im, x0 + k * (L['size'] + L['gap']), cy, s=max(0.01, s))
        if tl < q:
            self.draw_timer(frame, q - tl, q)
        else:
            rt = tl - q
            put(frame, L['answer'], W / 2, 1300, s=max(0.01, ease_out_back(rt / 0.3)))
            if L['sub'] is not None:
                put(frame, L['sub'], W / 2, 1400, a=clamp((rt - 0.1) / 0.2))
            if L['fact'] is not None:
                put(frame, L['fact'], W / 2, 1400 + (70 if L['sub'] is not None else 0), a=clamp((rt - 0.25) / 0.25))

    def _draw_trivia(self, frame, i, tl, q, pop, L):
        r = self.plan['rounds'][i]
        put(frame, L['icon'], W / 2, 640, s=0.8 + 0.2 * pop)
        put(frame, L['q'], W / 2, 800 + L['q'].size[1] / 2 - 40)
        y0 = 1010
        for k, o in enumerate(L['opts']):
            t_k = tl + self.pop_offset - 0.2 - 0.1 * k
            s = ease_out_back(t_k / 0.3)
            y = y0 + k * 118
            if tl >= q:
                rt = tl - q
                if k == r['answer']:
                    put(frame, o['good'], W / 2, y, s=1 + 0.05 * math.sin(clamp(rt / 0.3) * math.pi))
                else:
                    put(frame, o['base'], W / 2, y, a=1 - 0.6 * clamp(rt / 0.25))
            else:
                put(frame, o['base'], W / 2, y, s=max(0.01, s))
        if tl < q:
            frac = clamp((q - tl) / q)
            d = ImageDraw.Draw(frame)
            bar(d, 140, 935, 940, 953, (255, 255, 255, 60))
            col = (255, 72, 96) if (q - tl) <= 1.0 else self.accent
            bar(d, 140, 935, 140 + int(800 * frac), 953, col + (255,))

    def _draw_wyr(self, frame, i, tl, q, pop, L):
        put(frame, L['ea'], W / 2, 560, s=0.85 + 0.15 * pop)
        put(frame, L['ta'], W / 2, 770)
        put(frame, L['eb'], W / 2, 1110, s=0.85 + 0.15 * ease_out_back((tl + self.pop_offset - 0.15) / 0.32))
        put(frame, L['tb'], W / 2, 1310)
        put(frame, L['or'], W / 2, 900, s=0.8 + 0.2 * pop)
        if tl < q:
            frac = clamp((q - tl) / q)
            d = ImageDraw.Draw(frame)
            half = int(420 * frac)
            if half > 16:
                bar(d, int(W / 2 - 95 - half), 893, int(W / 2 - 95), 907, WHITE + (230,))
                bar(d, int(W / 2 + 95), 893, int(W / 2 + 95 + half), 907, WHITE + (230,))
        else:
            rt = tl - q
            for key, y in (('wa', 855), ('wb', 1400)):
                if L[key] is not None:
                    put(frame, L[key], W / 2, y, s=max(0.01, ease_out_back(rt / 0.35)))

    def _draw_city(self, frame, i, tl, q, pop, L):
        cx, cy = W / 2, 900
        m = self._city_map
        put(frame, m, cx, cy)
        x = cx - m.size[0] / 2 + L['xy'][0]
        y = cy - m.size[1] / 2 + L['xy'][1]
        d = ImageDraw.Draw(frame)
        ph = (tl * 1.6) % 1.0
        r_out = 18 + 34 * ph
        d.ellipse([x - r_out, y - r_out, x + r_out, y + r_out], outline=(255, 40, 60, int(255 * (1 - ph))), width=6)
        s = 0.6 + 0.4 * ease_out_back((tl + self.pop_offset) / 0.3)
        r_in = 17 * s
        d.ellipse([x - r_in - 4, y - r_in - 4, x + r_in + 4, y + r_in + 4], fill=WHITE + (255,))
        d.ellipse([x - r_in, y - r_in, x + r_in, y + r_in], fill=(235, 30, 50, 255))
        if tl < q:
            self.draw_timer(frame, q - tl, q, y=1350)
        else:
            rt = tl - q
            put(frame, L['answer'], W / 2, 1330, s=max(0.01, ease_out_back(rt / 0.3)))
            if L['fact'] is not None:
                put(frame, L['fact'], W / 2, 1440, a=clamp((rt - 0.2) / 0.25))

    def frame_at(self, t):
        frame = self.bg.copy()
        draw_watermark(frame)
        if t >= self.outro_start:
            self.draw_outro(frame, t - self.outro_start)
            return frame
        seg = next((s for s in self.segs if t < s['end']), self.segs[-1])
        i = seg['i']
        tl = t - seg['start']
        self.pop_offset = 0.35 if i == 0 else 0.0
        top = 250 if self.fmt != 'wyr' else 230
        put(frame, self.title, W / 2, top)
        put(frame, self.subtitle, W / 2, top + 90)
        if self.fmt != 'wyr':
            put(frame, self.levels[i], W / 2, top + 174, s=0.6 + 0.4 * ease_out_back((tl + self.pop_offset) / 0.3))
            self.draw_progress(frame, i)
        else:
            put(frame, self.levels[i], W / 2, top + 170)
        self.draw_round(frame, i, tl, seg)
        return frame

    def make_pop_audio(self, clips, n):
        intro, asks, anss, outro = clips[0], clips[1:1 + n], clips[1 + n:1 + 2 * n], clips[1 + 2 * n]
        voice = []
        if intro is not None:
            voice.append((0.15, intro))
        for i, s in enumerate(self.segs):
            if asks[i] is not None:
                voice.append((s['start'] + (0.15 if i else 0.15 + (len(intro) / sound.SR + 0.2 if intro is not None else 0)), asks[i]))
            if anss[i] is not None:
                voice.append((s['reveal'] + 0.08, anss[i]))
        if outro is not None:
            voice.append((self.outro_start + 0.2, outro))
        fx = []
        wh, tk, tkh = sound.whoosh(), sound.tick(), sound.tick(high=True)
        for s in self.segs:
            if s['start'] > 0:
                fx.append((s['start'] - 0.05, wh, 0.35))
            q = s['reveal'] - s['start']
            k = max(0.0, q - 3.0)  # tick only through the last 3 seconds
            while k < q - 0.05:
                last = (q - k) <= 1.0
                fx.append((s['start'] + k, tkh if last else tk, 0.45 if last else 0.32))
                k += 0.25 if last else 0.5
            fx.append((s['reveal'], sound.ding(), 0.6))
            fx.append((s['reveal'] + 0.02, sound.pop(), 0.35))
        fx.append((self.outro_start, wh, 0.4))
        fx.append((self.outro_start + 0.1, sound.chime(), 0.5))
        m = sound.music(self.duration, seed=self.plan.get('seed') or 0, bpm=self.rng.choice([100, 108, 116, 124])) if self.use_music else None
        return sound.mix(self.duration, fx, voice, m)

    def render(self, out_path):
        import subprocess
        import time
        t0 = time.time()
        intro, asks, anss, outro = self.voice_lines()
        n = len(self.plan['rounds'])
        lines = [intro] + asks + anss + [outro]
        idx = [k for k, l in enumerate(lines) if l]
        clips = [None] * len(lines)
        if self.use_voice and idx:
            got = sound.tts_lines([lines[k] for k in idx], voices=[self.voice_name] if self.voice_name else None, workdir=self.workdir)
            for k, c in zip(idx, got):
                clips[k] = c
        dur = [(len(c) / sound.SR) if c is not None else 0 for c in clips]
        self.build_pop_timeline(dur[1:1 + n], dur[1 + n:1 + 2 * n])
        # the first question also has to fit the intro line before it
        if clips[0] is not None and self.fmt in ('trivia', 'wyr'):
            extra = dur[0] + 0.2
            for k, s in enumerate(self.segs):
                s['start'] += 0 if k == 0 else extra
                s['reveal'] += extra
                s['end'] += extra
            self.outro_start += extra
            self.duration += extra
        self.prepare_pop()
        top, bot, acc = self.pal
        self.bg = gradient_bg(top, bot, acc, split=self.fmt == 'wyr')
        audio = self.make_pop_audio(clips, n)
        wav = os.path.join(self.workdir, 'audio.wav')
        sound.write_wav(wav, audio)
        cmd = ['ffmpeg', '-y', '-v', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{W}x{H}', '-r', str(FPS),
               '-i', '-', '-i', wav, '-c:v', 'libx264', '-preset', 'medium', '-crf', '19', '-pix_fmt', 'yuv420p',
               '-profile:v', 'high', '-c:a', 'aac', '-b:a', '192k', '-ar', '44100', '-shortest',
               '-movflags', '+faststart', out_path]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        nf = int(round(self.duration * FPS))
        for k in range(nf):
            proc.stdin.write(self.frame_at(k / FPS).convert('RGB').tobytes())
        proc.stdin.close()
        if proc.wait() != 0:
            raise RuntimeError('ffmpeg failed')
        return {'duration': round(self.duration, 2), 'frames': nf, 'renderSec': round(time.time() - t0, 1),
                'voice': sum(c is not None for c in clips), 'voiceLines': len(idx)}


# ── local plans from the starter bank (the pipeline builds plans itself) ──

def plan_from_bank(fmt, topic=None, seed=None, n=None, country=None):
    rng = random.Random(seed)
    bank = json.load(open(BANK, encoding='utf-8'))
    if fmt == 'city':
        country = country or rng.choice(['CN', 'JP', 'KR'])
        pool = [c for c in bank['cities'] if c['iso2'] == country]
        n = n or 5
        by_d = sorted(pool, key=lambda c: (c['difficulty'], rng.random()))
        ramp = [1, 1, 2, 2, 3][:n]
        rounds, used = [], set()
        for d in ramp:
            cand = [c for c in pool if c['difficulty'] == d and c['name'] not in used] or [c for c in by_d if c['name'] not in used]
            c = rng.choice(cand)
            used.add(c['name'])
            rounds.append({**c, 'answer': c['name']})
        return {'format': 'city', 'topic': country, 'seed': seed, 'rounds': rounds}
    items = bank[fmt]
    topics = sorted({i['topic'] for i in items})
    topic = topic or rng.choice(topics)
    pool = [i for i in items if i['topic'] == topic]
    n = n or (4 if fmt == 'wyr' else 5)
    if fmt == 'wyr':
        rounds = rng.sample(pool, min(n, len(pool)))
    else:
        ramp = [1, 1, 2, 2, 3][:n]
        rounds, used = [], set()
        for d in ramp:
            cand = [i for i in pool if i.get('difficulty', 2) == d and id(i) not in used] or [i for i in pool if id(i) not in used]
            it = rng.choice(cand)
            used.add(id(it))
            rounds.append(it)
    return {'format': fmt, 'topic': topic, 'seed': seed, 'rounds': rounds}
