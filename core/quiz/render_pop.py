"""
Asian pop culture quiz formats for the quiz channel, drawn with the same
engine as render_quiz.py:

  emoji   guess the anime / K-pop song from 4 emojis        (5 rounds)
  trivia  multiple choice, question read out loud            (5 rounds)
  wyr     would you rather, split screen, "but..." twists    (4 rounds)
  city    guess the city from a dot on the country map       (5 rounds)
  character  guess the anime character: blurred, zoomed image that sharpens
  idol       guess the K-pop idol from a photo (same reveal)
  opening    guess the anime from 3.5 s of its opening (vinyl + visualizer)
  cityphoto  guess the Asian city from a photo (same blur-to-sharp reveal, photo never cropped)
  vtuber     guess the VTuber from their official portrait (same reveal, head-and-shoulders crop)
  duel       two AniList characters side by side, a question, then who fans actually
             picked (AniList favourites, both counts shown)            (4 rounds)
  scene      guess the anime from 4 s of its creditless opening, played in a 16:9 card
  voice      guess the character from 3 s of their voice (hidden card + soundwave)

A plan is a dict {format, topic, theme, rounds: [...]} built by
pipeline/pop-quiz-content.js (or plan_from_bank() below for local tests).
"""
import json
import math
import os
import random
import re
import textwrap

import numpy as np
from PIL import Image, ImageDraw

import quiz_emoji as emj
import geo
import media
import sound
from PIL import ImageFilter
from render_quiz import (FPS, H, NAVY, W, WHITE, QuizRenderer, clamp, ease_out_back, ease_out_cubic, font, pill,
                         put, ring, text_layer, with_shadow, flag_card, fmt_num, draw_watermark)

HERE = os.path.dirname(os.path.abspath(__file__))
BANK = os.path.join(HERE, 'assets', 'pop-bank.json')

POP_FORMATS = ('emoji', 'trivia', 'wyr', 'city', 'character', 'idol', 'opening', 'cityphoto', 'vtuber', 'duel', 'scene', 'voice')
MEDIA_FORMATS = ('character', 'idol', 'opening', 'cityphoto', 'vtuber', 'duel', 'scene', 'voice')
CLIP_FORMATS = ('opening', 'scene', 'voice')  # fetched from YouTube at render time, with spare candidates
GOLD = (255, 214, 0)
GREEN = (46, 204, 113)
PINK = (255, 92, 170)

PALETTE = {  # (top, bottom, accent)
    'emoji': ((58, 8, 64), (150, 28, 110), GOLD),
    'trivia': ((18, 10, 58), (72, 32, 150), (0, 229, 255)),
    'wyr': ((150, 22, 40), (20, 70, 170), WHITE),
    'city': ((70, 6, 16), (150, 24, 36), GOLD),
    'character': ((22, 8, 44), (96, 24, 96), GOLD),
    'idol': ((44, 6, 52), (170, 40, 120), (255, 170, 220)),
    'opening': ((6, 10, 40), (30, 44, 130), (0, 229, 255)),
    'cityphoto': ((6, 34, 52), (18, 100, 130), GOLD),
    'vtuber': ((26, 12, 66), (70, 50, 180), (130, 225, 255)),
    'duel': ((48, 6, 24), (120, 20, 60), GOLD),
    'scene': ((10, 6, 26), (58, 18, 84), (255, 110, 180)),
    'voice': ((4, 26, 38), (10, 84, 104), (70, 255, 200)),
}
CITY_COUNTRY = {'CN': ('CHINA', '156', (160, 20, 30), GOLD), 'JP': ('JAPAN', '392', (150, 20, 50), WHITE),
                'KR': ('KOREA', '410', (18, 50, 130), (255, 90, 90))}
# duel: questions asked per pair (neutral ones fit any pair; the rest need both characters to be 16+)
DUEL_NEUTRAL = ['Who would you trust to protect you?', 'Who wins in a fight?', "Who's the better teacher?",
                'Who would you rather go on an adventure with?']
DUEL_ADULT = ['Who would you rather have as your roommate?']
DUEL_W, DUEL_H = 420, 520
SCENE_W, SCENE_H = 960, 540

TOPIC_TITLE = {
    'emoji': {'anime': ('GUESS THE ANIME', 'FROM THE EMOJIS'), 'kpop song': ('GUESS THE K-POP SONG', 'FROM THE EMOJIS'),
              'kdrama': ('GUESS THE K-DRAMA', 'FROM THE EMOJIS'), 'anime character': ('GUESS THE CHARACTER', 'FROM THE EMOJIS')},
    'trivia': {'kpop': ('K-POP QUIZ', 'ONLY REAL FANS GET 5/5'), 'anime': ('ANIME QUIZ', 'EASY TO IMPOSSIBLE'),
               'vtubers': ('VTUBER QUIZ', 'HOW MANY CAN YOU GET?'), 'cdrama': ('C-DRAMA & MOVIE QUIZ', 'EASY TO IMPOSSIBLE'),
               'cities': ('ASIA CITY QUIZ', 'EASY TO IMPOSSIBLE'), 'mixed': ('ASIAN POP QUIZ', 'EASY TO IMPOSSIBLE')},
    'character': {'anime': ('GUESS THE CHARACTER', 'ANIME EDITION')},
    'idol': {'kpop': ('GUESS THE K-POP IDOL', 'EASY TO IMPOSSIBLE')},
    'opening': {'anime': ('GUESS THE ANIME', 'FROM ITS OPENING')},
    'cityphoto': {'asia': ('GUESS THE CITY', 'FROM ONE PHOTO')},
    'vtuber': {'vtubers': ('GUESS THE VTUBER', 'EASY TO IMPOSSIBLE')},
    'duel': {'anime': ('WHO WOULD YOU PICK?', 'THEN SEE WHO FANS PICKED')},
    'scene': {'anime': ('GUESS THE ANIME', 'FROM A 4 SECOND SCENE')},
    'voice': {'anime': ('GUESS THE CHARACTER', 'FROM THEIR VOICE')},
    'wyr': {'anime': ('WOULD YOU RATHER', 'ANIME EDITION'), 'kpop': ('WOULD YOU RATHER', 'K-POP EDITION'),
            'food': ('WOULD YOU RATHER', 'ASIAN FOOD EDITION'), 'cities': ('WOULD YOU RATHER', 'ASIA TRAVEL EDITION'),
            'mixed': ('WOULD YOU RATHER', 'ASIA EDITION')},
}
INTRO = {'emoji': {'anime': 'Guess the anime from the emojis!', 'kpop song': 'Guess the K-pop song from the emojis!',
                   'kdrama': 'Guess the K-drama from the emojis!', 'anime character': 'Guess the character from the emojis!'},
         'trivia': 'How many can you get right?', 'wyr': None, 'city': 'Guess the city from the map!',
         'character': 'Guess the anime character!', 'idol': 'Guess the K-pop idol!', 'opening': 'Guess the anime from its opening!',
         'cityphoto': 'Guess the city from the photo!', 'vtuber': 'Guess the VTuber!', 'duel': 'Pick one, then see who the fans picked.',
         'scene': 'Guess the anime from one scene!', 'voice': 'Guess the character by their voice!'}

# Varied, conversational narration: the same fixed line every video is what
# makes TTS channels feel robotic (and "repetitive" to YouTube's classifiers).
INTRO_POOL = {
    'character': ['Guess the anime character!', 'Okay, real otakus only. Who is this?', 'Name the character before it unblurs!',
                  "Let's see how much anime you've actually watched."],
    'idol': ['Guess the K-pop idol!', 'Real K-pop fans get these instantly.', 'Name the idol before the photo clears up!'],
    'opening': ['Guess the anime from its opening!', 'Three seconds of the opening. Name the anime.', 'Headphones on. Which anime is this?'],
    'emoji': None, 'trivia': ['How many can you get right?', "Let's see if you're a real fan.", 'Five questions. No cheating.'],
    'city': ['Guess the city from the map!', 'Where is this dot? Name the city.', 'Map nerds, this one is for you.'],
    'cityphoto': ['Guess the city from the photo!', 'Which Asian city is this?', 'Travel nerds, name the city before it unblurs!',
                  'One photo. Name the city.'],
    'vtuber': ['Guess the VTuber!', 'Chat, who is this?', 'Real VTuber fans get these instantly.', 'Name the VTuber before it unblurs!'],
    'duel': ['Pick one. Then see who the fans picked.', 'Who would you pick? The fans already voted.',
             "Two characters, one pick. Let's see if you agree with the fans."],
    'scene': ['Guess the anime from one scene!', 'Four seconds of the opening. Name the anime.', 'One scene. Which anime is it?'],
    'voice': ['Guess the character by their voice!', 'Close your eyes. Who is talking?', 'Real fans know these voices. Who is it?'],
}
REVEAL_POOL = ['{a}!', "It's {a}!", 'That was {a}.', '{a}. Did you get it?', 'Easy. {a}!']
LAST_POOL = ['Last one. This one is evil.', 'Final round. Good luck.', 'Last one, and it gets hard.']
OUTRO_POOL = ['How many did you get? Comment your score!', 'Be honest. How many did you get?', 'Drop your score in the comments!']
DUEL_OUTRO = ['How many times did you agree with the fans? Comment below!', 'Did the fans get it right? Tell me below!']

Q_MIN = {'emoji': 4.0, 'trivia': 4.5, 'wyr': 4.2, 'city': 3.0, 'character': 4.0, 'idol': 4.0, 'opening': 4.3,
         'cityphoto': 4.2, 'vtuber': 4.0, 'duel': 4.8, 'scene': 4.7, 'voice': 4.4}
R_MIN = {'emoji': 1.9, 'trivia': 1.6, 'wyr': 2.0, 'city': 1.8, 'character': 1.9, 'idol': 1.9, 'opening': 2.1,
         'cityphoto': 2.3, 'vtuber': 1.9, 'duel': 2.8, 'scene': 2.1, 'voice': 2.1}
CLIP_AT = 0.25  # clips start this long into their round (after the intro line in round 1)
CARD_W, CARD_H = 580, 640


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


def short_title(t, limit=26):
    """'Frieren: Beyond Journey's End' -> 'Frieren' when a title is too long for a small label."""
    t = re.sub(r'\s*\((tv|\d{4})\)$', '', str(t or ''), flags=re.I)  # 'Hunter x Hunter (2011)' -> 'Hunter x Hunter'
    if len(t) <= limit:
        return t
    for sep in (':', ' -', ' – '):
        head = t.split(sep)[0].strip()
        if 3 <= len(head) < len(t):
            return head
    return t


def spoken_count(n):
    """39390 -> '39 thousand' (what the narrator says; the screen shows the exact number)."""
    if n >= 10000:
        return f'{round(n / 1000)} thousand'
    if n >= 1000:
        return f'{n / 1000:.1f} thousand'.replace('.0 ', ' ')
    return str(n)


def age_of(a):
    """AniList age strings ('17-', '15-16 (series)', '6 (self asserted)', '40 Days') -> first number, or None."""
    s = str(a or '')
    if not s or re.search(r'day|week|month', s, re.I):
        return None
    m = re.search(r'\d+', s)
    return int(m.group()) if m else None


def on_backdrop(img, top=(236, 240, 255), bot=(196, 206, 244)):
    """Portraits with a transparent background go on a soft light gradient."""
    if img.mode != 'RGBA' or img.getextrema()[3][0] == 255:
        return img.convert('RGB')
    w, h = img.size
    g = np.zeros((h, w, 3), dtype=np.float32)
    for c in range(3):
        g[:, :, c] = np.linspace(top[c], bot[c], h)[:, None]
    base = Image.fromarray(g.astype(np.uint8), 'RGB').convert('RGBA')
    base.alpha_composite(img)
    return base.convert('RGB')


def fit_contain(img, max_w, max_h):
    """Scale a picture to fit the box, never cropping it (the card takes the picture's shape)."""
    w, h = img.size
    s = min(max_w / w, max_h / h)
    return img.resize((max(2, int(w * s)) // 2 * 2, max(2, int(h * s)) // 2 * 2), Image.LANCZOS)


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
        self._cards = {}
        self.clip_at = {}  # round -> seconds into the round when its clip starts
        self._frames = (None, None)  # scene: (round, decoded frames), one round in memory at a time
        self._order_rounds()

    def _order_rounds(self):
        """Easy to hard, always: a topic short of hard questions (or a failed opening
        download replaced by a spare) must not put an EASY label after a MEDIUM one."""
        if self.fmt != 'wyr':
            self.plan['rounds'].sort(key=lambda r: int(r.get('difficulty', 2)))

    # ── audio lines ──
    def voice_lines(self):
        rounds = self.plan['rounds']
        intro = INTRO[self.fmt]
        if isinstance(intro, dict):
            intro = intro.get(self.topic, 'Guess it from the emojis!')
        elif INTRO_POOL.get(self.fmt):
            intro = self.rng.choice(INTRO_POOL[self.fmt])
        ask, ans = [], []
        for r in rounds:
            if self.fmt == 'trivia':
                ask.append(r['question'])
                ans.append(f"{r['options'][r['answer']]}!")
            elif self.fmt == 'wyr':
                ask.append(f"Would you rather {r['a'][0].lower() + r['a'][1:]}, or {r['b'][0].lower() + r['b'][1:]}?")
                tw = r.get('twistA') or r.get('twistB') or ''
                ans.append(f"{tw[0].upper() + tw[1:]}!" if tw else None)
            elif self.fmt in ('opening', 'scene'):
                ask.append(None)
                ans.append(self.rng.choice(REVEAL_POOL).format(a=r['title']))
            elif self.fmt == 'duel':
                a, b = r['a'], r['b']
                ask.append(f"{r['question']} {self._spoken(a['name'])}, or {self._spoken(b['name'])}?")
                w, l = (a, b) if r['winner'] == 'a' else (b, a)
                # the counts are on screen; the narrator only says them when the vote was close
                if w['favourites'] - l['favourites'] < max(300, 0.05 * w['favourites']):
                    ans.append(f"So close! {self._spoken(w['name'])}, by {spoken_count(w['favourites'] - l['favourites'])} favorites.")
                else:
                    ans.append(self.rng.choice(['Fans picked {w}!', '{w} wins it!', 'The fans chose {w}.', 'Easy. {w}!'])
                               .format(w=self._spoken(w['name'])))
            else:
                ask.append(None)
                name = (r.get('answer') or r.get('name')).split(' (')[0]
                ans.append(self.rng.choice(REVEAL_POOL).format(a=name))
        outro = ('Which would you pick? Comment below!' if self.fmt == 'wyr' else self.rng.choice(DUEL_OUTRO) if self.fmt == 'duel'
                 else self.rng.choice(OUTRO_POOL))
        return intro, ask, ans, outro

    @staticmethod
    def _spoken(name):
        """Names as the narrator should say them: 'Monkey D. Luffy' stays, 'L Lawliet' -> 'L'."""
        return {'L Lawliet': 'L'}.get(name, name)

    def build_pop_timeline(self, ask_d, ans_d):
        t = 0.0
        segs = []
        for i in range(len(self.plan['rounds'])):
            q = max(Q_MIN[self.fmt], (ask_d[i] or 0) + {'trivia': 2.3, 'wyr': 2.3, 'duel': 2.0}.get(self.fmt, 0))
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
            elif fmt == 'duel':  # rounds run from a clear fan favourite to the closest vote
                last = i == len(rounds) - 1
                label = 'FINAL ROUND  ·  CLOSEST VOTE' if last and int(r.get('difficulty', 2)) == 3 else f'ROUND {i + 1} / {len(rounds)}'
                col = (255, 64, 96) if last else WHITE
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
        if fmt == 'duel':
            self.outro_layers.update({
                'l1': text_layer('DID YOU AGREE', 118, stroke=10, max_w=1000),
                'l2': text_layer('WITH THE FANS?', 118, stroke=10, max_w=1000),
                'score': text_layer(f'?/{len(rounds)}', 300, fill=GOLD, stroke=14),
                'cta': pill('COMMENT YOUR PICKS', 64, GOLD, max_w=940),
            })

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

    # ── media formats ──
    def _card(self, w=CARD_W, h=CARD_H, color=WHITE):
        """Rounded frame + shadow and the photo mask for a w x h picture (cached per size and colour)."""
        key = (w, h, color)
        if key not in self._cards:
            b = 14
            card = Image.new('RGBA', (w + 2 * b, h + 2 * b), (0, 0, 0, 0))
            ImageDraw.Draw(card).rounded_rectangle([0, 0, card.size[0] - 1, card.size[1] - 1], radius=30, fill=color + (255,))
            m = Image.new('L', (w, h), 0)
            ImageDraw.Draw(m).rounded_rectangle([0, 0, w - 1, h - 1], radius=20, fill=255)
            self._cards[key] = (with_shadow(card), m)
        return self._cards[key]

    def _photo_card(self):
        """White rounded frame + shadow, drawn once; the (animated) photo is pasted inside."""
        card, self._photo_mask = self._card()
        return card

    @staticmethod
    def _fit_photo(img, top_bias, w=CARD_W, h=CARD_H):
        """Crop to the card's aspect (faces sit high in photos) and resize."""
        iw, ih = img.size
        ar = w / h
        if iw / ih > ar:
            nw = int(ih * ar)
            img = img.crop(((iw - nw) // 2, 0, (iw - nw) // 2 + nw, ih))
        else:
            nh = int(iw / ar)
            y0 = int(max(0, min(ih - nh, (ih - nh) * top_bias)))
            img = img.crop((0, y0, iw, y0 + nh))
        return on_backdrop(img).resize((w, h), Image.LANCZOS)

    @staticmethod
    def _fit_portrait(img):
        """VTuber portraits: head and shoulders only (the top of the picture, 72% of its width)."""
        iw, ih = img.size
        cw = iw * 0.72
        ch = cw * CARD_H / CARD_W
        if ch > ih:
            ch, cw = ih, ih * CARD_W / CARD_H
        x0 = (iw - cw) / 2
        return on_backdrop(img.crop((int(x0), 0, int(x0 + cw), int(ch)))).resize((CARD_W, CARD_H), Image.LANCZOS)

    def _prep_character(self, r):
        self._photo_card()
        img = media.fetch_image(r['image'])
        photo = self._fit_portrait(img) if self.fmt == 'vtuber' else self._fit_photo(img, 0.15)
        name = r.get('name') or r['answer']
        ans, _ = self._answer_pill(name)
        if self.fmt == 'vtuber':
            sub = r.get('agency', '') + (' (graduated)' if r.get('graduated') else '')
        else:
            sub = r.get('anime') if self.fmt in ('character', 'voice') else r.get('group')
        subl = text_layer(sub.upper(), 50, fill=self.accent, stroke=6, max_w=960) if sub else None
        return {'photo': photo, 'answer': ans, 'sub': subl}

    _prep_idol = _prep_character
    _prep_vtuber = _prep_character

    def _prep_cityphoto(self, r):
        photo = fit_contain(on_backdrop(media.fetch_image(r['image'])), 940, 600)
        card, mask = self._card(*photo.size)
        ans, _ = self._answer_pill(r['name'])
        cname = CITY_COUNTRY[r['iso2']][0]
        flag = flag_card(r['iso2'], 84, 58, border=4, radius=8)
        label = text_layer(cname, 50, fill=WHITE, stroke=6)
        country = Image.new('RGBA', (flag.size[0] + label.size[0] - 30, max(flag.size[1], label.size[1])), (0, 0, 0, 0))
        country.alpha_composite(flag, (0, (country.size[1] - flag.size[1]) // 2))
        country.alpha_composite(label, (flag.size[0] - 30, (country.size[1] - label.size[1]) // 2))
        fact = text_layer(r['fact'].upper(), 44, fill=self.accent, stroke=6, max_w=980) if r.get('fact') else None
        author, _, lic = str(r.get('credit') or '').rpartition(', ')
        author = author if len(author) <= 34 else author[:32].rstrip(' ,(') + '…'
        credit = text_layer(f'PHOTO: {author}, {lic}' if author else f'PHOTO: {lic}', 22, fill=(255, 255, 255, 215), stroke=3,
                            stroke_fill=(0, 0, 0, 160), max_w=photo.size[0] - 40, min_size=14) if r.get('credit') else None
        return {'photo': photo, 'card': card, 'mask': mask, 'answer': ans, 'country': country, 'fact': fact, 'credit': credit}

    def _blur_zoom(self, ph, tl, q, focus_y):
        """The shared question effect: zoomed in and blurred, sharpening as the timer runs down."""
        w, h = ph.size
        if tl >= q:
            return ph
        p = clamp(tl / q)
        z = 2.1 - 1.1 * ease_out_cubic(p)
        radius = 26 * (1 - p) ** 1.3 + 1.5
        if z > 1.001:
            cw, ch = w / z, h / z
            x0, y0 = (w - cw) / 2, (h - ch) * focus_y
            ph = ph.crop((int(x0), int(y0), int(x0 + cw), int(y0 + ch))).resize((w, h), Image.BILINEAR)
        if radius > 0.5:
            f = max(1, int(radius / 3))
            small = ph.resize((w // f, h // f), Image.BILINEAR).filter(ImageFilter.GaussianBlur(radius / f))
            ph = small.resize((w, h), Image.BILINEAR)
        return ph

    def _draw_cityphoto(self, frame, i, tl, q, pop, L):
        cx, cy = W / 2, 868
        s = 0.92 + 0.08 * pop
        put(frame, L['card'], cx, cy, s=s)
        ph = self._blur_zoom(L['photo'], tl, q, 0.45).convert('RGBA')
        ph.putalpha(L['mask'])
        put(frame, ph, cx, cy, s=s)
        bottom = cy + L['photo'].size[1] / 2
        if tl < q:
            self.draw_timer(frame, q - tl, q, y=1345)
        else:
            rt = tl - q
            if L['credit'] is not None:
                put(frame, L['credit'], cx, bottom - 22, a=clamp(rt / 0.3))
            put(frame, L['country'], W / 2, bottom + 62, s=max(0.01, ease_out_back(rt / 0.3)))
            put(frame, L['answer'], W / 2, 1345, s=max(0.01, ease_out_back((rt - 0.05) / 0.3)))
            if L['fact'] is not None:
                put(frame, L['fact'], W / 2, 1448, a=clamp((rt - 0.2) / 0.25))

    def _prep_opening(self, r):
        cover = self._fit_photo(media.fetch_image(r['cover']), 0.0) if r.get('cover') else None
        ans, _ = self._answer_pill(r['title'])
        sub = text_layer(str(r.get('year') or '').upper() or 'ANIME OPENING', 46, fill=self.accent, stroke=6, max_w=900)
        # per-frame spectrum bars of the snippet (computed once)
        a = r['_audio']
        hop = sound.SR // 30
        bands = []
        for k in range(0, len(a) - 2048, hop):
            spec = np.abs(np.fft.rfft(a[k:k + 2048] * np.hanning(2048)))[:600]
            edges = np.geomspace(3, 600, 29).astype(int)
            edges = np.maximum(edges, np.arange(29) + 3)  # log spacing collapses at the low end
            bands.append([float(spec[edges[i]:max(edges[i] + 1, edges[i + 1])].mean()) for i in range(28)])
        bands = np.nan_to_num(np.array(bands)) if bands else np.zeros((1, 28))
        bands = np.clip(bands / (float(np.percentile(bands, 95)) or 1.0), 0, 1.2)
        return {'cover': cover, 'answer': ans, 'sub': sub, 'bands': bands}

    def _vinyl(self, size, angle):
        if not hasattr(self, '_disc'):
            ss = 2
            S = size * ss
            d0 = Image.new('RGBA', (S, S), (0, 0, 0, 0))
            d = ImageDraw.Draw(d0)
            d.ellipse([0, 0, S - 1, S - 1], fill=(16, 16, 20, 255))
            for k in range(8, 46, 3):
                rr = S / 2 * k / 50
                d.ellipse([S / 2 - rr, S / 2 - rr, S / 2 + rr, S / 2 + rr], outline=(40, 40, 48, 255), width=2)
            lr = S * 0.2
            d.ellipse([S / 2 - lr, S / 2 - lr, S / 2 + lr, S / 2 + lr], fill=self.accent + (255,))
            self._disc_q = text_layer('?', int(size * 0.2), fill=NAVY)  # pasted upright, the record spins under it
            # a highlight so the spin is visible
            hl = Image.new('RGBA', (S, S), (0, 0, 0, 0))
            ImageDraw.Draw(hl).pieslice([0, 0, S - 1, S - 1], -30, 10, fill=(255, 255, 255, 28))
            d0.alpha_composite(hl)
            self._disc = d0.resize((size, size), Image.LANCZOS)
        disc = self._disc.rotate(-angle, resample=Image.BICUBIC)
        q = self._disc_q
        disc.alpha_composite(q, ((size - q.size[0]) // 2, (size - q.size[1]) // 2))
        return disc

    def _draw_character(self, frame, i, tl, q, pop, L):
        cx, cy = W / 2, 900
        card = self._photo_card()
        put(frame, card, cx, cy, s=0.92 + 0.08 * pop)
        focus = {'idol': 0.12, 'vtuber': 0.2}.get(self.fmt, 0.3)  # zoom towards the face
        ph = self._blur_zoom(L['photo'], tl, q, focus).convert('RGBA')
        ph.putalpha(self._photo_mask)
        put(frame, ph, cx, cy, s=0.92 + 0.08 * pop)
        if tl < q:
            self.draw_timer(frame, q - tl, q, y=1345)
        else:
            rt = tl - q
            put(frame, L['answer'], W / 2, 1335, s=max(0.01, ease_out_back(rt / 0.3)))
            if L['sub'] is not None:
                put(frame, L['sub'], W / 2, 1440, a=clamp((rt - 0.15) / 0.2))

    _draw_idol = _draw_character
    _draw_vtuber = _draw_character

    # ── duel: two characters, one question, the fans' pick ──
    def _prep_duel(self, r):
        a, b = r['a'], r['b']
        win = 0 if r['winner'] == 'a' else 1
        sides = []
        for c in (a, b):
            photo = self._fit_photo(media.fetch_image(c['image']), 0.1, DUEL_W, DUEL_H).convert('RGBA')
            _, mask = self._card(DUEL_W, DUEL_H)
            photo.putalpha(mask)
            heart = emoji_img('❤️', 42)
            num = text_layer(f"{c['favourites']:,}", 46, fill=NAVY)
            fav = Image.new('RGBA', (heart.size[0] + num.size[0] + 60, max(heart.size[1], num.size[1]) + 22), (0, 0, 0, 0))
            ImageDraw.Draw(fav).rounded_rectangle([0, 0, fav.size[0] - 1, fav.size[1] - 1], radius=fav.size[1] // 2, fill=WHITE + (255,))
            fav.alpha_composite(heart, (24, (fav.size[1] - heart.size[1]) // 2))
            fav.alpha_composite(num, (24 + heart.size[0] + 8, (fav.size[1] - num.size[1]) // 2))
            show = short_title(c.get('anime'), 24).upper()
            show = (text_layer(show, 32, fill=self.accent, stroke=5, max_w=480, min_size=24) if len(show) <= 34 else
                    text_block(show, 32, fill=self.accent, stroke=5, max_w=480, max_lines=2))  # one line unless very long
            sides.append({'photo': photo, 'name': text_layer(c['name'].upper(), 50, stroke=6, max_w=480), 'anime': show,
                          'fav': with_shadow(fav, blur=8, offset=(0, 5))})
        w, l = (a, b) if win == 0 else (b, a)
        verdict = text_layer(f"FANS PICKED {w['name'].split(' (')[0].upper()}", 72, fill=GOLD, stroke=8, max_w=1000)
        counts = text_layer(f"{w['favourites']:,} VS {l['favourites']:,} FAVORITES ON ANILIST", 36, stroke=5, max_w=980)
        from render_quiz import check_mark
        return {'sides': sides, 'win': win, 'q': text_block(r['question'].upper(), 68, max_w=980, max_lines=2),
                'verdict': verdict, 'counts': counts, 'check': check_mark(96, GREEN)}

    def _draw_duel(self, frame, i, tl, q, pop, L):
        xs, cy = (282, 798), 1000
        white, _ = self._card(DUEL_W, DUEL_H)
        gold, _ = self._card(DUEL_W, DUEL_H, GOLD)
        rt = tl - q
        for k, sd in enumerate(L['sides']):
            t_k = tl + self.pop_offset - 0.12 * k
            s = 0.88 + 0.12 * ease_out_back(t_k / 0.32)
            a = 1.0
            if rt >= 0:
                g = ease_out_cubic(rt / 0.35)
                s = 1 + (0.06 if k == L['win'] else -0.06) * g
                a = 1.0 if k == L['win'] else 1 - 0.5 * g
            put(frame, gold if (rt >= 0 and k == L['win']) else white, xs[k], cy, s=s, a=a)
            put(frame, sd['photo'], xs[k], cy, s=s, a=a)
            put(frame, sd['name'], xs[k], 1318, a=a)
            put(frame, sd['anime'], xs[k], 1374 + (sd['anime'].size[1] - 44) / 2, a=a)
            if rt >= 0:
                put(frame, sd['fav'], xs[k], cy + DUEL_H / 2 * s - 48, s=max(0.01, ease_out_back((rt - 0.1 * k) / 0.3)), a=max(a, 0.8))
                if k == L['win'] and rt > 0.3:
                    put(frame, L['check'], xs[k] + DUEL_W / 2 - 12, cy - DUEL_H / 2 + 12, s=max(0.01, ease_out_back((rt - 0.3) / 0.3)))
        if tl < q:
            put(frame, L['q'], W / 2, 640, s=0.9 + 0.1 * pop)
            self.draw_timer(frame, q - tl, q, y=cy)
        else:
            put(frame, L['q'], W / 2, 640, a=1 - clamp(rt / 0.08))
            put(frame, L['verdict'], W / 2, 612, s=max(0.01, ease_out_back((rt - 0.1) / 0.35)))
            put(frame, L['counts'], W / 2, 690, a=clamp((rt - 0.25) / 0.25))

    # ── scene: a few seconds of the opening, played in a 16:9 card ──
    def _prep_scene(self, r):
        cover = self._fit_photo(media.fetch_image(r['cover']), 0.0) if r.get('cover') else None
        ans, _ = self._answer_pill(r['title'])
        sub = text_layer(str(r.get('year') or '').upper() or 'ANIME', 46, fill=self.accent, stroke=6, max_w=900)
        vw, vh = r.get('_w') or 1280, r.get('_h') or 720
        s = min(SCENE_W / vw, SCENE_H / vh)
        size = (int(vw * s) // 2 * 2, int(vh * s) // 2 * 2)  # the card takes the picture's shape: fitted, never cropped
        card, mask = self._card(*size)
        return {'cover': cover, 'answer': ans, 'sub': sub, 'size': size, 'card': card, 'mask': mask}

    def _scene_frames(self, i):
        """Decoded frames of round i's clip at 30 fps, fitted (never cropped) to the card."""
        if self._frames[0] != i:
            import subprocess
            self._frames = (None, None)  # free the previous round first
            w, h = self.layers[i]['size']
            crop = self.plan['rounds'][i].get('_crop')
            vf = (f'crop={crop[0]}:{crop[1]}:{crop[2]}:{crop[3]},' if crop else '') + f'fps={FPS},scale={w}:{h}:flags=bicubic,setsar=1'
            r = subprocess.run(['ffmpeg', '-v', 'error', '-i', self.plan['rounds'][i]['_clip'], '-vf', vf,
                                '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'], capture_output=True)
            n = len(r.stdout) // (w * h * 3)
            frames = np.frombuffer(r.stdout[:n * w * h * 3], dtype=np.uint8).reshape(n, h, w, 3) if n else np.zeros((1, h, w, 3), np.uint8)
            self._frames = (i, frames)
        return self._frames[1]

    def _draw_scene(self, frame, i, tl, q, pop, L):
        cx, cy = W / 2, 862
        card = L['card']
        at = self.clip_at.get(i, CLIP_AT)
        if tl < q + 0.35:
            fr = self._scene_frames(i)
            k = int(max(0.0, tl - at) * FPS)
            scr = Image.fromarray(fr[min(len(fr) - 1, k)], 'RGB').convert('RGBA')
            scr.putalpha(L['mask'])
            fade = 1 - clamp((tl - q) / 0.35)
            s = (0.92 + 0.08 * pop) * (1 - 0.1 * (1 - fade))
            put(frame, card, cx, cy, s=s, a=fade)
            put(frame, scr, cx, cy, s=s, a=fade)
        if tl < q:
            self.draw_timer(frame, q - tl, q, y=1345)
        else:
            rt = tl - q
            if L['cover'] is not None:
                cover_card = self._photo_card()
                ph = L['cover'].convert('RGBA')
                ph.putalpha(self._photo_mask)
                s = 0.85 + 0.15 * ease_out_back((rt - 0.1) / 0.35)
                a = clamp((rt - 0.1) / 0.2)
                put(frame, cover_card, cx, cy + 28, s=s, a=a)
                put(frame, ph, cx, cy + 28, s=s, a=a)
            put(frame, L['answer'], W / 2, 1335, s=max(0.01, ease_out_back(rt / 0.3)))
            put(frame, L['sub'], W / 2, 1440, a=clamp((rt - 0.15) / 0.2))

    # ── voice: a hidden card with a speaker and a live soundwave ──
    def _prep_voice(self, r):
        base = self._prep_character(r)
        a = r['_audio']
        hop = sound.SR // FPS
        edges = np.geomspace(5, 230, 25).astype(int)  # FFT bins, ~100 Hz .. 5 kHz (speech)
        edges = np.maximum(edges, np.arange(25) + 5)  # at least one bin per band
        bands = []
        for k in range(0, max(1, len(a) - 2048), hop):
            spec = np.abs(np.fft.rfft(a[k:k + 2048] * np.hanning(2048)))
            bands.append([float(spec[edges[j]:max(edges[j] + 1, edges[j + 1])].mean()) for j in range(24)])
        bands = np.nan_to_num(np.array(bands)) if bands else np.zeros((1, 24))
        bands = np.clip(bands / (float(np.percentile(bands, 95)) or 1.0), 0, 1.2)
        # the hidden side of the card: dark panel, a speaker in a ring, "who is talking?"
        panel = np.zeros((CARD_H, CARD_W, 3), dtype=np.float32)
        for c, (t0, b0) in enumerate(zip((16, 40, 58), (6, 16, 30))):
            panel[:, :, c] = np.linspace(t0, b0, CARD_H)[:, None]
        hidden = Image.fromarray(panel.astype(np.uint8), 'RGB').convert('RGBA')
        qm = text_layer('?', 420, fill=self.accent + (38,), stroke=0)
        hidden.alpha_composite(qm, ((CARD_W - qm.size[0]) // 2, (CARD_H - qm.size[1]) // 2 - 20))
        spk = emoji_img('🔊', 150)
        hidden.alpha_composite(spk, ((CARD_W - spk.size[0]) // 2, 225 - spk.size[1] // 2))
        who = text_layer('WHO IS TALKING?', 46, fill=WHITE, stroke=0)
        hidden.alpha_composite(who, ((CARD_W - who.size[0]) // 2, 560))
        return {**base, 'bands': bands, 'hidden': hidden}

    def _draw_voice(self, frame, i, tl, q, pop, L):
        cx, cy = W / 2, 900
        card = self._photo_card()
        s = 0.92 + 0.08 * pop
        at = self.clip_at.get(i, CLIP_AT)
        rt = tl - q
        put(frame, card, cx, cy, s=s)
        if rt < 0.12:
            hid = L['hidden'].copy()
            d = ImageDraw.Draw(hid)
            k = int((tl - at) * FPS)
            vals = L['bands'][k] if 0 <= k < len(L['bands']) else np.zeros(24)
            bw, gap, base = 14, 8, 445
            x = CARD_W / 2 - (24 * bw + 23 * gap) / 2
            for v in vals:
                h = 12 + 150 * float(v)
                d.rounded_rectangle([x, base - h / 2, x + bw, base + h / 2], radius=7, fill=self.accent + (235,))
                x += bw + gap
            lvl = float(np.mean(vals))
            for ring_r, alpha in ((118 + 30 * lvl, 170), (150 + 55 * lvl, 70)):
                d.ellipse([CARD_W / 2 - ring_r, 225 - ring_r, CARD_W / 2 + ring_r, 225 + ring_r], outline=self.accent + (alpha,), width=5)
            hid.putalpha(self._photo_mask)
            put(frame, hid, cx, cy, s=s)
        if rt >= 0:
            # quick unblur of the real picture at the reveal
            ph = self._blur_zoom(L['photo'], 0.35 + 0.65 * clamp(rt / 0.35), 1.0, 0.3) if rt < 0.35 else L['photo']
            ph = ph.convert('RGBA')
            ph.putalpha(self._photo_mask.point(lambda v: int(v * clamp(rt / 0.12))))
            put(frame, ph, cx, cy, s=s * (1 + 0.04 * math.sin(clamp(rt / 0.3) * math.pi)))
            put(frame, L['answer'], W / 2, 1335, s=max(0.01, ease_out_back(rt / 0.3)))
            if L['sub'] is not None:
                put(frame, L['sub'], W / 2, 1440, a=clamp((rt - 0.15) / 0.2))
        else:
            self.draw_timer(frame, q - tl, q, y=1345)

    def _draw_opening(self, frame, i, tl, q, pop, L):
        cx, cy = W / 2, 840
        if tl < q:
            disc = self._vinyl(560, tl * 200)
            put(frame, disc, cx, cy, s=0.9 + 0.1 * pop)
            k = min(len(L['bands']) - 1, max(0, int((tl - 0.25) * 30)))
            vals = L['bands'][k] if 0.25 <= tl <= 0.25 + len(L['bands']) / 30 else np.zeros(28)
            d = ImageDraw.Draw(frame)
            bw, gap, base = 22, 10, 1215
            x = W / 2 - (28 * bw + 27 * gap) / 2
            for v in vals:
                h = 12 + 150 * float(v)
                d.rounded_rectangle([x, base - h, x + bw, base], radius=8, fill=self.accent + (230,))
                x += bw + gap
            self.draw_timer(frame, q - tl, q, y=1345)
        else:
            rt = tl - q
            if L['cover'] is not None:
                card = self._photo_card()
                ph = L['cover'].convert('RGBA')
                ph.putalpha(self._photo_mask)
                s = 0.85 + 0.15 * ease_out_back(rt / 0.35)
                put(frame, card, cx, cy + 50, s=s)
                put(frame, ph, cx, cy + 50, s=s)
            put(frame, L['answer'], W / 2, 1335, s=max(0.01, ease_out_back(rt / 0.3)))
            put(frame, L['sub'], W / 2, 1440, a=clamp((rt - 0.15) / 0.2))

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

    def _load_clips(self):
        """Fetch the YouTube clips (opening audio, scene video, voice) in parallel; spares replace failures."""
        want = self.plan.get('want') or min(5, len(self.plan['rounds']))
        rounds = _clip_rounds(self.plan['rounds'], want, self.fmt)
        if len(rounds) < 3:
            raise RuntimeError(f'only {len(rounds)} {self.fmt} clips could be fetched')
        self.plan['rounds'] = rounds
        self._order_rounds()

    _load_openings = _load_clips

    def make_pop_audio(self, clips, n):
        intro, asks, anss, outro = clips[0], clips[1:1 + n], clips[1 + n:1 + 2 * n], clips[1 + 2 * n]
        voice = []
        if self.fmt in CLIP_FORMATS:  # the clip plays during each question
            tts = [c for c in clips if c is not None]
            ref = float(np.median([_active_rms(c) for c in tts])) if tts else 0.1
            for i, (s, r) in enumerate(zip(self.segs, self.plan['rounds'])):
                a = r['_audio']
                if self.fmt == 'voice':  # speech: as loud as the narrator, a touch louder
                    a = a * min(1.1 * ref / (_active_rms(a) or 1), 0.95 / (np.abs(a).max() or 1))
                else:
                    a = a * 0.55
                voice.append((s['start'] + self.clip_at.get(i, CLIP_AT), a))
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
        m = sound.music(self.duration, seed=self.plan.get('seed') or 0, bpm=self.rng.choice([100, 108, 116, 124])) \
            if self.use_music and self.fmt not in ('opening', 'scene') else None
        return sound.mix(self.duration, fx, voice, m, music_gain=0.4 if self.fmt == 'voice' else 0.55, target_lufs=-14.0)

    def render(self, out_path):
        import subprocess
        import time
        t0 = time.time()
        if self.fmt in CLIP_FORMATS:
            self._load_clips()
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
        # the first question also has to fit the intro line before it (and a clip must not play under it)
        if clips[0] is not None and self.fmt in ('trivia', 'wyr', 'duel', 'scene', 'voice'):
            extra = dur[0] + 0.2
            for k, s in enumerate(self.segs):
                s['start'] += 0 if k == 0 else extra
                s['reveal'] += extra
                s['end'] += extra
            self.outro_start += extra
            self.duration += extra
            if self.fmt in ('scene', 'voice'):
                self.clip_at[0] = extra + CLIP_AT
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
        # AAC can push a sharp transient back over -1 dBTP: measure the upload and, if so, lower the audio a touch
        lufs_i, tp = sound.measure_file(out_path)
        if tp is not None and tp > -1.2:
            # re-master under a lower peak ceiling rather than turning the whole mix down (keeps -14 LUFS)
            wav2 = os.path.join(self.workdir, 'audio-tp.wav')
            sound.write_wav(wav2, sound.master(audio, -14.0, tp_ceiling=max(-8.0, -3.0 - (tp + 1.5))))
            fixed = out_path + '.tp.mp4'
            r = subprocess.run(['ffmpeg', '-y', '-v', 'error', '-i', out_path, '-i', wav2, '-map', '0:v', '-map', '1:a', '-c:v', 'copy',
                                '-c:a', 'aac', '-b:a', '192k', '-ar', '44100', '-shortest', '-movflags', '+faststart', fixed])
            if r.returncode == 0:
                os.replace(fixed, out_path)
                lufs_i, tp = sound.measure_file(out_path)
        return {'duration': round(self.duration, 2), 'frames': nf, 'renderSec': round(time.time() - t0, 1),
                'voice': sum(c is not None for c in clips), 'voiceLines': len(idx), 'lufs': lufs_i, 'truePeak': tp}


def _active_rms(x):
    """RMS of the parts of a clip that are actually sounding (pauses would drag a plain RMS down)."""
    x = np.asarray(x, dtype=np.float64)
    if not len(x):
        return 0.0
    k = int(0.02 * sound.SR)
    env = np.sqrt(np.convolve(x ** 2, np.ones(k) / k, mode='same'))
    act = env > 0.1 * (env.max() or 1)
    return float(np.sqrt(np.mean(x[act] ** 2))) if act.any() else 0.0


def _fetch_clip(fmt, r):
    """One round's clip: (round with '_audio' etc., None) or (None, reason)."""
    if fmt == 'scene':
        got, info = media.fetch_scene(r['title'], r.get('romaji'))
        if got is None:
            return None, info
        return {**r, '_clip': got['path'], '_audio': got['audio'], '_w': info['width'], '_h': info['height'], '_crop': info.get('crop'),
                'source': {k: info[k] for k in ('video', 'videoTitle', 'start')}}, None
    if fmt == 'voice':
        audio, info = media.fetch_voice(r['name'], r.get('anime'))
    else:
        audio, info = media.fetch_opening(r['title'], r.get('romaji'))
    return (None, info) if audio is None else ({**r, '_audio': audio, 'source': info}, None)


def _clip_rounds(rounds, want, fmt='opening'):
    """
    Fetch clips in parallel. The first `want` rounds are the planned easy-to-hard ramp, the rest
    spares: a failed round is replaced by the spare closest to its difficulty, so the ramp holds.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    ex = ThreadPoolExecutor(max_workers=4)
    futs = {ex.submit(_fetch_clip, fmt, r): k for k, r in enumerate(rounds)}
    got, done = {}, set()
    for f in as_completed(futs):
        k = futs[f]
        done.add(k)
        try:
            rr, why = f.result()
        except Exception as e:
            rr, why = None, str(e)
        if rr is None:
            print(f'{fmt} skipped: {rounds[k].get("title") or rounds[k].get("name")}: {str(why)[:160]}', file=__import__('sys').stderr)
        else:
            got[k] = rr
        if all(j in got for j in range(min(want, len(rounds)))):
            break  # the whole ramp worked: no need to wait for the spares
    ex.shutdown(wait=False, cancel_futures=True)
    spares = [k for k in sorted(got) if k >= want]
    out = []
    for j in range(min(want, len(rounds))):
        if j in got:
            out.append(got[j])
        elif spares:
            d = int(rounds[j].get('difficulty', 2))
            k = min(spares, key=lambda s: (abs(int(rounds[s].get('difficulty', 2)) - d), s))
            spares.remove(k)
            out.append(got[k])
    return out


def _opening_rounds(rounds, want):
    return _clip_rounds(rounds, want, 'opening')


# ── local plans from the starter bank (the pipeline builds plans itself) ──

MEDIA_FILES = {'character': ('anime-characters.json', 'characters'), 'idol': ('kpop-idols.json', 'idols'),
               'opening': ('anime-list.json', 'anime'), 'scene': ('anime-list.json', 'anime'),
               'voice': ('anime-characters.json', 'characters'), 'cityphoto': ('city-photos.json', 'cities'),
               'vtuber': ('vtubers.json', 'vtubers')}
MEDIA_TOPIC = {'idol': 'kpop', 'cityphoto': 'asia', 'vtuber': 'vtubers'}
# clips can fail to download: spare candidates per level (hard shows fail most often: fewer uploads)
SPARES = {'opening': [2, 3, 2, 3, 1], 'scene': [3, 3, 2, 3, 1], 'voice': [3, 2, 3, 1, 2, 1]}
RAMPS = {3: [1, 2, 3], 4: [1, 2, 2, 3], 5: [1, 1, 2, 2, 3]}
DUEL_SKIP = {'bakemonogatari'}  # fan-service heavy even where AniList does not tag it
# openings with fan-service shots, although AniList does not flag the show (the scene quiz plays video)
SCENE_SKIP = ['Bakemonogatari', 'Kakegurui', "DON'T TOY WITH ME, MISS NAGATORO", 'The Pet Girl of Sakurasou', "Masamune-kun's Revenge",
              'Fire Force', 'Akame ga Kill!', 'Rascal Does Not Dream of Bunny Girl Senpai', 'Arifureta', 'The Misfit of Demon King Academy',
              "Miss Kobayashi's Dragon Maid", 'Cyberpunk: Edgerunners', 'The Quintessential Quintuplets', 'Nisekoi', 'Monogatari']


def franchise(title):
    """'Attack on Titan Final Season' -> 'attack on': one entry per series in a quiz (mirrors pop-quiz-content.js)."""
    t = str(title or '').lower()
    head = re.split(r'[:\-–]', t)[0]
    words = re.findall(r'[a-z0-9]+', head if len(head.strip()) >= 4 else t)
    return ' '.join(words[:2])


def is_sequel(title):
    return bool(re.search(r'\b(season|part|cour)\s*\d|final season|\b(ii|iii|iv)\b|\d+(st|nd|rd|th) season|\barc\b|√a|:re\b|\bmovie\b|\?$',
                          str(title or ''), re.I))


def same_series(a, b):
    """'noragami' / 'noragami aragoto': one franchise key is the start of the other."""
    x, y = franchise(a).split(), franchise(b).split()
    k = min(len(x), len(y))
    return k > 0 and x[:k] == y[:k]


def media_pool(fmt, items):
    """What each format may draw from: nothing flagged nsfw, one entry (the most popular) per series for scenes
    and openings (a guess is the show's name, and sequel openings often match the first season's upload)."""
    if fmt in ('scene', 'opening'):
        keep = []
        for a in sorted(items, key=lambda a: -(a.get('popularity') or 0)):
            if (not a.get('nsfw') and not is_sequel(a['title']) and not any(same_series(a['title'], s) for s in SCENE_SKIP)
                    and not any(same_series(a['title'], k['title']) for k in keep)):
                keep.append(a)
        return keep
    if fmt == 'voice':  # voice-line videos exist for the well-known characters only
        return [{**c, 'difficulty': 1 if k < 25 else 2 if k < 70 else 3} for k, c in enumerate(items[:150]) if not c.get('nsfw')]
    return items


def duel_rounds(chars, n, rng, fresh=lambda key: True):
    """Pairs of popular characters (same series, else same gender among the top 80) with a question each.
    Difficulty = how close the fan vote is: 1 = clear favourite ... 3 = within 20%."""
    top = [c for c in chars[:150] if not c.get('nsfw') and franchise(c['anime']) not in DUEL_SKIP]
    rank = {c['id']: k for k, c in enumerate(top)}
    same, cross = [], []
    for x in range(len(top)):
        for y in range(x + 1, len(top)):
            a, b = top[x], top[y]
            if franchise(a['anime']) == franchise(b['anime']):
                same.append((a, b))
            elif rank[a['id']] < 80 and rank[b['id']] < 80 and a.get('gender') == b.get('gender') and a.get('gender') in ('Male', 'Female'):
                cross.append((a, b))

    def level(p):
        hi, lo = max(p[0]['favourites'], p[1]['favourites']), min(p[0]['favourites'], p[1]['favourites'])
        r = hi / max(1, lo)
        return 1 if r >= 1.6 else 2 if r >= 1.2 else 3

    rounds, used, asked, shows = [], set(), set(), set()
    for d in RAMPS.get(n, RAMPS[4]):
        pick = None
        for pool in ((same, cross) if rng.random() < 0.75 else (cross, same)):
            cand = [p for p in pool if level(p) == d and p[0]['id'] not in used and p[1]['id'] not in used
                    and fresh(f"duel:{min(p[0]['id'], p[1]['id'])}-{max(p[0]['id'], p[1]['id'])}")]
            cand = [p for p in cand if not {franchise(p[0]['anime']), franchise(p[1]['anime'])} & shows] or cand  # vary the shows
            if cand:
                pick = rng.choice(cand)
                shows.update((franchise(pick[0]['anime']), franchise(pick[1]['anime'])))
                break
        if not pick:
            continue
        a, b = pick if rng.random() < 0.5 else pick[::-1]
        used.update((a['id'], b['id']))
        ages = [age_of(c.get('age')) for c in (a, b)]
        grown = all(x is not None and x >= 16 for x in ages)
        qs = [q for q in DUEL_NEUTRAL + (DUEL_ADULT if grown else []) if q not in asked]
        special = ('Best girl?' if a.get('gender') == b.get('gender') == 'Female' else
                   'Best boy?' if a.get('gender') == b.get('gender') == 'Male' else None) if grown else None
        q = special if special and special not in asked and rng.random() < 0.5 else rng.choice(qs or DUEL_NEUTRAL)
        asked.add(q)
        keep = ('id', 'name', 'anime', 'image', 'favourites')
        w = a if a['favourites'] >= b['favourites'] else b
        rounds.append({'a': {k: a[k] for k in keep}, 'b': {k: b[k] for k in keep}, 'question': q, 'difficulty': d,
                       'winner': 'a' if w is a else 'b', 'answer': w['name']})
    return rounds


def plan_from_bank(fmt, topic=None, seed=None, n=None, country=None):
    rng = random.Random(seed)
    if fmt == 'duel':
        chars = json.load(open(os.path.join(HERE, 'assets', 'anime-characters.json'), encoding='utf-8'))['characters']
        return {'format': 'duel', 'topic': 'anime', 'seed': seed, 'rounds': duel_rounds(chars, n or 4, rng)}
    if fmt in MEDIA_FILES:
        f, k = MEDIA_FILES[fmt]
        pool = media_pool(fmt, json.load(open(os.path.join(HERE, 'assets', f), encoding='utf-8'))[k])
        n = n or 5
        ramp = RAMPS.get(n, RAMPS[5])
        spare_d = SPARES.get(fmt, [])
        rounds, used = [], set()
        for d in ramp + spare_d:
            ok = [x for x in pool if id(x) not in used and (fmt != 'scene' or not any(same_series(x['title'], r['title']) for r in rounds))]
            cand = [x for x in ok if x.get('difficulty', 2) == d] or ok
            it = rng.choice(cand)
            used.add(id(it))
            rounds.append({**it, 'answer': it.get('name') or it.get('title')})
        return {'format': fmt, 'topic': MEDIA_TOPIC.get(fmt, 'anime'), 'seed': seed, 'rounds': rounds, 'want': n}
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
