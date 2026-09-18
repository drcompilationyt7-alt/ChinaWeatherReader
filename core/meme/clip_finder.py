"""
Clip sourcing for the "<song> acapella" meme Shorts. Each short has four
tiles, one per acapella layer, and each tile loops one clip of a vibe:
  vocals -> dance, bass -> cool, beatbox/drums -> fight, harmony -> cute.
For every requested vibe this finds short, viral, Asia-related YouTube clips
(Shorts first) of the chosen theme and cuts a 4-6 s continuous moment around
its strongest hit: a punch for fight, the key move for dance, the reaction
for cute. Each clip reports `peak` (its strongest hit) and `hits` (all motion
impacts), so the editor can land them on the beat, plus a `hook` score for
its first 0.5 s. Clips keep the source aspect, have no audio, and all come
from different videos.

Themes (every clip in a manifest matches it, all Asia-related):
  funny     Asian pranks, fails, variety shows
  cute      Asian kids, idols being cute, pandas / shibas / Douyin pets
  anime     anime moments
  kpop      idols, K-variety, K-drama
  chinese   Douyin, Chinese variety, street moments, kung fu (Chinese queries too)
  japanese  Japanese variety, pranks, street moments, pets (not anime)
  mixed     a blend of all of them

Install (GitHub ubuntu-latest; ffmpeg/ffprobe on PATH):
  python3 -m pip install yt-dlp yt-dlp-ejs opencv-python-headless numpy --break-system-packages
YouTube also needs a JS runtime: deno on PATH (yt-dlp's default), or node,
which is used automatically when deno is missing. Two small ONNX models are
fetched once into ~/.cache/clip_finder (override with CLIP_FINDER_CACHE):
YuNet faces (230 KB) and NudeNet 320n (12 MB, exposed/swimwear body parts).

Usage:
  python core/meme/clip_finder.py --out-dir work/clips \
      [--theme funny|cute|anime|kpop|chinese|japanese|mixed] \
      [--vibes dance:2,cool:2,fight:2,cute:2] [--used used.json] \
      [--cookies cookies.txt] [--block blocked.json] [--sheet contact.png]

Writes <out-dir>/clip_NNN.mp4 and <out-dir>/clips.json (grouped by vibe in
--vibes order, best first), then prints one line:
  {"ok": true, "count": N, "manifest": "...", "vibes": {...}, "stats": {...}}
ok is false when a vibe got no clip. --used: ids as a list, {id: date}, or
[{"id"/"source_id", "date"}]; entries dated older than --used-days are
ignored. --block: channel ids, @handles, names or URLs as a list, or
{"channels": [...]}.
"""
import argparse
import concurrent.futures as cf
import glob
import importlib.util
import io
import json
import math
import os
import random
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import urllib.parse
import urllib.request
import uuid
import zipfile
from collections import Counter
from datetime import datetime, timedelta, timezone

import cv2
import numpy as np

THEMES = ('funny', 'cute', 'anime', 'kpop', 'chinese', 'japanese')
VIBES = ('dance', 'cool', 'fight', 'cute')
# Queries per vibe and theme; the non-ASCII ones search in the local language.
VIBE_QUERIES = {
    'dance': {
        'anime': ['anime dance scene', 'anime characters dancing shorts', 'anime ending dance scene',
                  'anime dance shorts', 'アニメ ダンス シーン'],
        'kpop': ['kpop dance practice', 'kpop dance challenge shorts', 'kpop idol dance shorts',
                 'kpop relay dance', 'kpop random play dance idols', '아이돌 댄스 챌린지'],
        'chinese': ['douyin dance challenge', 'chinese street dance shorts', 'chinese classical dance shorts',
                    '抖音 手势舞', '中国舞 shorts'],
        'japanese': ['japanese dance trend shorts', 'otagei dance', 'japanese street dance shorts',
                     'japanese tiktok dance trend', 'ヲタ芸'],
        'funny': ['funny dance asian variety show', 'running man dance funny', 'kpop idols funny dance',
                  'japanese variety show dance funny'],
        'cute': ['panda dancing shorts', 'shiba inu dancing', 'douyin cat dancing', 'kpop idol cute dance',
                 'anime cute dance scene'],
    },
    'cool': {
        'anime': ['anime aura moment shorts', 'anime badass entrance scene', 'anime cool walk scene',
                  'anime aura scene shorts', 'アニメ かっこいい シーン'],
        'kpop': ['kpop idol aura moments', 'kpop killing part stage', 'kpop idol stage presence shorts',
                 'kpop idol charisma moment', '아이돌 카리스마 무대'],
        'chinese': ['douyin cool skills shorts', 'chinese street skills shorts', 'chinese parkour shorts',
                    'chinese kung fu master cool', '抖音 帅气'],
        'japanese': ['japanese amazing skills shorts', 'japanese street performance shorts',
                     'japanese samurai sword skills', 'japanese kendama skills', 'かっこいい 技 ショート'],
        'funny': ['asian amazing skills shorts', 'asian street skills shorts', 'asian cool moments shorts',
                  'korean street performance cool'],
        'cute': ['shiba inu cool', 'japanese cat cool moment', 'panda cool moments', 'douyin cool dog'],
    },
    'fight': {
        'anime': ['anime fight scene', 'anime best fight moments shorts', 'anime punch scene shorts',
                  'jjk fight scene shorts', 'demon slayer fight scene shorts', 'one punch man serious punch',
                  'アニメ 戦闘シーン'],
        'kpop': ['kpop idol taekwondo', 'idol martial arts', 'kdrama action scene shorts',
                 'korean action movie fight scene', 'korea taekwondo demonstration shorts'],
        'chinese': ['kung fu shorts', 'chinese kung fu master shorts', 'wushu shorts', 'shaolin kung fu shorts',
                    '功夫 shorts', '中国功夫', '武术 表演'],
        'japanese': ['japanese karate shorts', 'kendo shorts', 'samurai fight scene', 'judo throw shorts',
                     'japanese martial arts shorts', '空手 ショート'],
        'funny': ['funny kung fu shorts', 'asian martial arts shorts', 'muay thai shorts',
                  'sepak takraw kick shorts', 'taekwondo kick shorts'],
        'cute': ['douyin cat fight funny', 'japanese cats play fighting', 'panda fight cute',
                 'shiba inu play fight', 'panda cubs play fighting'],
    },
    'cute': {
        'anime': ['cute anime moments shorts', 'anya cute moments', 'anime wholesome moments', 'chibi anime cute',
                  'アニメ かわいい シーン'],
        'kpop': ['kpop idol cute moments', 'kpop idol aegyo', 'kpop idols with puppies', 'kpop idol laughing cute',
                 '아이돌 귀여운 순간'],
        'chinese': ['douyin cute pet', 'chinese cute panda', 'douyin cute baby', '可爱 萌宠', '熊猫 可爱', '抖音 可爱'],
        'japanese': ['japanese cute cat shorts', 'shiba inu cute', 'japanese kids cute', 'かわいい猫', '柴犬 かわいい'],
        'funny': ['cute funny asian baby', 'cute pets douyin funny', 'variety show cute moments',
                  'the return of superman cute'],
        'cute': ['cute panda shorts', 'fu bao cute', 'douyin cute kitten', 'shiba inu puppy cute', 'cute baby panda',
                 'かわいい猫', '熊猫 可爱'],
    },
}
SP_SHORT = 'EgQQARgB'  # search filter: type=video, duration under 4 min

# Sexual / suggestive terms: matched on title, description and tags (whole words).
NSFW_WORDS = [
    'ecchi', 'hentai', 'nsfw', 'sexy', 'sexi', 'sexiest', 'sex', 'sexual', 'seductive', 'seduce',
    'lewd', 'lewds', 'bikini', 'oppai', 'fanservice', 'fan service', '18+', '+18', 'r18', 'r-18',
    'nude', 'nudity', 'naked', 'boob', 'boobs', 'booba', 'tits', 'titty', 'breast', 'breasts',
    'cup size', 'chest', 'chest size', 'flat chest', 'flat chested', 'bust', 'busty', 'butt', 'booty', 'ass', 'thicc', 'thick', 'thicktok', 'curvy', 'hips', 'legs',
    'feet', 'cleavage', 'lingerie', 'underwear', 'panties', 'panty', 'pantsu', 'pantyshot',
    'upskirt', 'bra', 'swimsuit', 'swimwear', 'onsen', 'hot spring', 'bath', 'bathing',
    'bathtub', 'shower', 'massage', 'nosebleed', 'nose bleed', 'pervert', 'perverted', 'pervy',
    'perv', 'hot girl', 'hot girls', 'hot mom', 'hot wife', 'hottie', 'hotties', 'hottest',
    'baddie', 'twerk', 'twerking', 'onlyfans', 'strip', 'stripper', 'striptease', 'milf',
    'waifu', 'harem', 'yuri', 'yaoi', 'loli', 'lolicon', 'shota', 'shotacon', 'jiggle',
    'bouncing', 'physics', 'gravure', 'jav', 'erotic', 'erotica', 'adults only', 'adult content',
    'mature content', 'uncensored', 'horny', 'thirst', 'thirst trap', 'smash or pass',
    'kiss scene', 'kissing scene', 'love scene', 'bed scene', 'making out', 'ahegao', 'futanari',
    'doujin', 'rule 34', 'r34', 'fetish', 'kinky', 'bdsm', 'spank', 'spanking', 'cosplay',
    'cosplayer', 'egirl', 'e-girl', 'gyaru', 'maid', 'fancam', 'schoolgirl', 'school girl',
    'cute girl', 'cute girls', 'pretty girl', 'beautiful girl', 'beauty girl', 'chinese girl',
    'korean girl', 'japanese girl', 'asian girl', 'sexy jutsu', 'harem jutsu', 'oiroke',
    'hot guy', 'hot guys', 'hot boy', 'hot boys', 'crotch', 'groin',
]
NSFW_CHARS = [
    '性感', '美女', '福利', '擦边', '诱惑', '内衣', '比基尼', '泳装', '泳衣', '丝袜', '黑丝',
    '白丝', '长腿', '美腿', '身材', '胸', '翘臀', '蜜桃臀', '热舞', '小姐姐', 'エロ', 'エッチ',
    'セクシー', '水着', 'おっぱい', '巨乳', 'グラビア', 'パンチラ', '下着', '乳', '섹시', '노출',
    '비키니', '몸매', '가슴', '19금', '직캠', '🔞', '🍑', '🍆', '💦', '🥵', '👙', '🫦', '👅',
]
# Shows whose "funny moments" are mostly fanservice or child nudity gags.
UNSAFE_SHOWS = [
    'high school dxd', 'highschool dxd', 'dxd', 'prison school', 'to love ru', 'tolove',
    'shimoneta', 'keijo', 'kanokon', 'rosario vampire', 'sekirei', 'monster musume',
    'interspecies reviewers', 'ishuzoku', 'redo of healer', 'kaifuku', 'domestic girlfriend',
    'domestic na kanojo', 'eromanga', 'kiss x sis', 'yosuga', 'dokuro', 'bokusatsu',
    'boku no pico', 'kodomo no jikan', 'made in abyss', 'highschool of the dead',
    'high school of the dead', 'kill la kill', 'testament of sister', 'shinmai maou',
    'mayo chiki', 'girls bravo', 'maken ki', 'ikkitousen', 'ikki tousen', 'queens blade',
    'queen s blade', 'valkyrie drive', 'trinity seven', 'uzaki', 'nagatoro', 'dress up darling',
    'sono bisque', 'food wars', 'shokugeki', 'worlds end harem', 'world s end harem',
    'peter grill', 'grisaia', 'masamune kun', 'hybrid x heart', 'rent a girlfriend', 'kanokari',
    'oreimo', 'hajimete no gal', 'nande koko ni sensei', 'ane log', 'chainsaw man', 'dandadan',
    'konosuba', 'mushoku', 'goblin slayer', 'fire force', 'quintuplets', 'bunny girl',
    'girlfriend girlfriend', 'seven deadly sins', 'nanatsu no taizai', 'fairy tail',
    'shin chan', 'shinchan', 'crayon shin', 'sanji', 'jiraiya', 'pervy sage', 'roshi', 'mineta',
    'happosai',
    # Japanese idol groups with teen members and gravure work
    'akb48', 'nmb48', 'ske48', 'hkt48', 'ngt48', 'stu48', 'nogizaka', 'keyakizaka', 'sakurazaka',
    'hinatazaka',
]
# Channel names run words together ("waifuonfire"), so these match anywhere in them
CHANNEL_NSFW = ['hentai', 'ecchi', 'nsfw', 'waifu', 'sexy', 'lewd', 'oppai', 'bikini', 'nude', 'thicc',
                'onlyfans', 'booty', 'gravure', 'fanservice']
# Japanese game shows are a common source of sexualised "funny" clips
RISKY_RE = re.compile(r'(?=.*\bjapan)(?=.*\bgame\s?shows?\b)')
# Off-brand for a meme channel: tragedy, violence, politics, racist framing.
DARK_WORDS = [
    'dead body', 'killed', 'murder', 'accident', 'crash', 'funeral', 'tragic', 'tragedy',
    'suicide', 'abuse', 'abused', 'gore', 'bloody', 'injured', 'ccp', 'xi jinping', 'tiananmen',
    'uyghur', 'ching chong', 'chingchong', 'racist', 'racism',
]
# Extra caution where a vibe invites it. Dance: no kids, no body-focused moves.
# Fight: staged / sport / animated fights only, no real violence.
VIBE_RISK_WORDS = {
    'dance': ['belly dance', 'pole dance', 'lap dance', 'body wave', 'body roll', 'hip roll', 'waist',
              'sexy dance', 'hot dance', 'girl dance', 'girls dance', 'shake', 'shaking', 'kid', 'kids',
              'child', 'children', 'little girl', 'little boy', 'baby', 'babies', 'student', 'students',
              'school', 'teen', 'teens', 'teenage', '儿童', '小孩', '小学生', '中学生', '学生', '萌娃',
              '腰', '子供', '女子高生', '女子中学生', '아기', '어린이', '학생', '초등학생', '중학생'],
    'fight': ['street fight', 'street fighter', 'street fighters', 'real fight', 'real footage', 'thug', 'thugs',
              'robber', 'robbery', 'brawl', 'school fight', 'bully', 'bullying', 'stabbing', 'knife',
              'gun', 'guns', 'shooting', 'war', 'police', 'arrest', 'road rage', 'caught on camera', 'cctv',
              'blood', 'mma', 'ufc', 'cage', 'bare knuckle', 'fight club', 'kids fight', 'kid fight',
              'children fight', 'girls fight', 'girl fight'],
}
# Family uploads of school kids (sports days, class videos): not ours to repost outside the cute tile
SCHOOL_KID_WORDS = ['sports day', 'elementary school', 'primary school', '運動会', '小学', '幼稚園', '초등', '小学生']
# Not clip material.
OFFTYPE_WORDS = [
    'asmr', 'mukbang', 'podcast', 'full episode', 'lyrics', 'official mv', 'music video',
    'trailer', 'karaoke', 'tutorial', 'recipe', 'live stream', 'livestream', 'amv', 'phonk',
    '#edit', 'edit audio', 'trollface', 'troll face', 'prank call', 'prank calls',
]
DANCE_WORDS = ['dance', 'dancing', 'dancer', 'dancers', 'choreography', '舞蹈', '跳舞', '댄스', 'ダンス']

# ---- Asia relevance: every source must show one of these (title, tags, channel, description)
def _chars(*ranges, neg=False):
    """Regex character class from (first, last) code points."""
    return '[' + '^' * neg + ''.join(f'{chr(a)}-{chr(b)}' for a, b in ranges) + ']'


ARABIC, INDIC = (0x0600, 0x06FF), (0x0900, 0x0DFF)
KANA, HAN, HANGUL, THAI = (0x3040, 0x30FF), (0x4E00, 0x9FFF), (0xAC00, 0xD7AF), (0x0E00, 0x0E7F)
OFFTHEME_RE = re.compile(_chars(ARABIC, (0x0750, 0x077F), INDIC))  # South Asian / Arabic scripts
OFFTHEME_WORDS = [
    'hindi', 'bangla', 'bengali', 'telugu', 'tamil', 'desi', 'urdu', 'punjabi', 'marathi',
    'kannada', 'malayalam', 'bhojpuri', 'nepali', 'pakistani', 'indian',
    'caine', 'carradine', 'kwai chang',     # the 1970s US "Kung Fu" series
    # Western studios and cartoons that borrow Asian settings
    'kung fu panda', 'dreamworks', 'disney', 'pixar', 'nickelodeon', 'cartoon network', 'spongebob',
    'looney tunes', 'tom and jerry', 'minions', 'shrek', 'amazing digital circus', 'tadc', 'miraculous',
    'doofenshmirtz', 'phineas', 'simpsons', 'family guy',
]
ANIME_WORDS = [
    'anime', 'manga', 'donghua', 'アニメ', 'jjk', 'jujutsu kaisen', 'gojo', 'one piece', 'luffy',
    'zoro', 'naruto', 'boruto', 'sasuke', 'spy x family', 'anya', 'frieren', 'haikyuu', 'bokuto',
    'blue lock', 'bachira', 'demon slayer', 'kimetsu', 'tanjiro', 'zenitsu', 'inosuke',
    'sakamoto days', 'bocchi', 'mob psycho', 'saiki', 'one punch man', 'saitama', 'pokemon',
    'pikachu', 'kaiju no 8', 'dragon ball', 'goku', 'vegeta', 'hunter x hunter', 'killua',
    'attack on titan', 'solo leveling', 'jinwoo', 'doraemon', 'ghibli', 'totoro', 'gintama',
    'kaguya', 'oshi no ko', 'bleach', 'my hero academia', 'tokyo revengers', 'nichijou',
    'wind breaker', 'kpop demon hunters', 'k-pop demon hunters', 'huntrix', 'jojo', 'kabaneri', 'magi',
    'kuroko', 'tokyo ghoul', 'evangelion', 'gundam', 'sailor moon', 'death note', 'fullmetal', 'code geass',
    'steins gate', 're zero', 'overlord', 'your name', 'kimi no na wa', 'suzume', 'violet evergarden',
    'bungo stray dogs', 'blue exorcist', 'black clover', 'mashle', 'dr stone', 'apothecary diaries',
    'dungeon meshi', 'lycoris recoil', 'shikanoko', 'zom 100', 'call of the night', 'shirobako', 'k-on',
    'vinland saga', 'toradora', 'haruhi', 'ジョジョ', 'ワンピース', 'ナルト', '呪術', '鬼滅', '進撃', 'ハイキュー',
    'ポケモン', 'ドラゴンボール', 'ガンダム', 'エヴァ', 'しかのこ', 'ブルーロック', 'スパイファミリー', '推しの子',
    'フリーレン', 'ヒロアカ', '銀魂',
]
KPOP_WORDS = [
    'kpop', 'k-pop', 'idol', 'idols', 'aegyo', 'bts', 'bangtan', 'blackpink', 'twice',
    'stray kids', 'skz', 'seventeen', 'svt', 'newjeans', 'aespa', 'le sserafim', 'lesserafim',
    'enhypen', 'txt', 'tomorrow x together', 'itzy', 'nct', 'exo', 'red velvet', 'bigbang',
    'ateez', 'illit', 'babymonster', 'riize', 'zerobaseone', 'boynextdoor', 'mamamoo', 'shinee',
    'got7', 'monsta x', 'super junior', 'snsd', 'nmixx', 'stayc', 'kiss of life', 'cortis',
    'hearts2hearts', 'jungkook', 'taehyung', 'jimin', 'jennie', 'jisoo', 'wonyoung', 'hyunjin',
    'bang chan', 'weekly idol', 'knowing bros', 'running man', 'korean variety',
    'kpop demon hunters', 'k-pop demon hunters', 'huntrix', '아이돌', '케이팝', '방탄', '블랙핑크',
    '트와이스', '세븐틴', '스트레이 키즈', '뉴진스', '에스파', '르세라핌', '엔하이픈', '런닝맨', '아는형님',
    '주간아이돌', '놀면 뭐하니', '나혼자산다', 'kdrama', 'k-drama', 'korean drama', 'korean movie',
    'korean action', 'taekwondo', '태권도',
]
CHINESE_WORDS = [
    'china', 'chinese', 'douyin', 'bilibili', 'kuaishou', 'xiaohongshu', 'weibo', 'tiktok china',
    'mandarin', 'cantonese', 'taiwan', 'taiwanese', 'hong kong', 'beijing', 'shanghai', 'shenzhen',
    'chengdu', 'chongqing', 'guangzhou', 'cdrama', 'c-drama', 'cpop', 'c-pop', 'panda', 'pandas',
    'kung fu', 'kungfu', 'wushu', 'shaolin', '抖音', '中国', '搞笑', '功夫', '武术',
]
JAPANESE_WORDS = [
    'japan', 'japanese', 'nihon', 'nippon', 'tokyo', 'osaka', 'kyoto', 'hokkaido', 'okinawa',
    'shibuya', 'gaki', 'batsu', 'owarai', 'jpop', 'j-pop', 'jdrama', 'shiba', 'shiba inu', 'akita',
    'karate', 'kendo', 'judo', 'samurai', 'otagei', 'kendama', '日本', 'ドッキリ', 'おもしろ', '面白',
    'ヲタ芸', '空手', '剣道',
]
ASIA_WORDS = ANIME_WORDS + KPOP_WORDS + CHINESE_WORDS + JAPANESE_WORDS + [
    'asia', 'asian', 'japan', 'japanese', 'korea', 'korean', 'thailand', 'thai', 'vietnam',
    'vietnamese', 'indonesia', 'indonesian', 'philippines', 'filipino', 'pinoy', 'malaysia',
    'malaysian', 'singapore', 'mongolia', 'tokyo', 'osaka', 'kyoto', 'seoul', 'busan', 'bangkok',
    'hanoi', 'saigon', 'manila', 'jakarta', 'bali', 'jpop', 'j-pop', 'jdrama', 'kdrama', 'k-drama',
    'variety show', 'gaki', 'batsu', 'owarai', 'kocowa', 'sbs', 'kbs', 'mbc', 'jtbc', 'tvn',
    '2 days 1 night', '1 night 2 days', 'workman', 'return of superman', 'shiba', 'shiba inu',
    'akita', 'fu bao', 'muay thai', 'sepak takraw',
]
HAN_RE, KANA_RE, HANGUL_RE = re.compile(_chars(HAN)), re.compile(_chars(KANA)), re.compile(_chars(HANGUL))
ASIAN_SCRIPT_RE = re.compile(_chars(KANA, HAN, HANGUL, THAI))

# ---- Vibe match: dance / fight / cute sources must say so in title, tags or description.
VIBE_WORDS = {
    'dance': ['dance', 'dances', 'dancing', 'dancer', 'choreo', 'choreography', 'challenge', 'dance practice',
              'dance cover', 'relay dance', 'random dance', 'otagei', '舞蹈', '跳舞', '舞', '手势舞', '댄스', '춤',
              'ダンス', '踊', 'ヲタ芸'],
    'cool': ['aura', 'cool', 'badass', 'swag', 'swagger', 'sigma', 'entrance', 'walk', 'charisma', 'killing part',
             'stage presence', 'skills', 'skill', 'amazing', 'master', 'parkour', 'epic', 'legendary', 'goat',
             '帅', '酷', 'かっこいい', '카리스마', '🔥', '🗿', '😎'],
    'fight': ['fight', 'fights', 'fighting', 'battle', 'punch', 'kick', 'kung fu', 'kungfu', 'martial arts',
              'karate', 'taekwondo', 'wushu', 'shaolin', 'judo', 'kendo', 'boxing', 'muay thai', 'action', 'vs',
              'sword', 'samurai', 'takraw', '功夫', '武术', '打斗', '戦闘', '空手', '剣道', '태권도', '액션'],
    'cute': ['cute', 'cutie', 'adorable', 'wholesome', 'aegyo', 'baby', 'puppy', 'puppies', 'kitten', 'kitty',
             'cat', 'cats', 'panda', 'shiba', 'chibi', 'laugh', 'laughing', 'giggle', 'smile', 'smiling', 'sweet',
             'healing', 'aww', '可爱', '萌', '熊猫', 'かわいい', '可愛い', '猫', '귀여', '힐링', '🥹', '🥰'],
}
VIBE_REQUIRED = ('dance', 'fight', 'cute')
# Ranking: standalone moments over compilations and reaction videos.
GENERIC_WORDS = ['compilation', 'full episode', 'episode', 'ep', 'full', 'reaction', 'reacts', 'review',
                 'explained', 'ranking', 'top 10', 'top 5']

FPS = 10            # analysis frame rate
SAMPLE = 3          # text / face checks on every 3rd analysis frame
NUDE_EVERY = 2      # nudity check on every 2nd sample (~1.7 per second)
GRID = 24           # coverage grid for the text mask
AN = 320            # analysis frame long side
MIN_SEG, TARGET_SEG, MAX_SEG = 4.0, 5.0, 6.0
WHOLE_MAX = 240     # download whole videos up to this long, else only a section
ANALYZE_MAX = 90    # seconds analysed per video, centred on the heatmap peak
MODELS = {
    'face': ('face_detection_yunet_2023mar.onnx', 100_000,
             'https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/'
             'face_detection_yunet_2023mar.onnx'),
    # the GitHub release asset needs a login, so take the copy bundled in the PyPI wheel (AGPL-3.0)
    'nude': ('nudenet_320n.onnx', 5_000_000, 'https://pypi.org/pypi/nudenet/3.4.2/json#nudenet/320n.onnx'),
}
# NudeNet 320n classes used: exposed buttocks, female breast, female genitalia, anus, male genitalia
NUDE_EXPOSED = [2, 3, 4, 6, 14]
NUDE_G_COV, NUDE_BELLY, NUDE_BREAST_COV, NUDE_BUTT_COV = 0, 13, 16, 17

T0 = time.time()
NOW = datetime.now(timezone.utc)
PATHS = {}
HAAR = None
_tls = threading.local()
_lock = threading.Lock()
_procs = set()
STATS = {'searched': 0, 'candidates': 0, 'probed': 0, 'kept': 0, 'downloaded': 0, 'analyzed': 0,
         'segments': 0, 'rejected': Counter(), 'seg_rejected': Counter()}
try:
    cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
except AttributeError:
    pass


def log(msg):
    print(f'[clips {time.time() - T0:5.1f}s] {msg}', file=sys.stderr, flush=True)


def _bump(key, reason=None, n=1):
    with _lock:
        if reason is None:
            STATS[key] += n
        else:
            STATS[key][reason] += n


def popen(cmd, stderr=subprocess.PIPE):
    """Popen in its own process group, tracked so it can be killed on exit."""
    kw = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=stderr, **kw)
    _procs.add(p)
    return p


def run(cmd, timeout):
    """Run a command, killing its whole process tree on timeout."""
    p = popen(cmd)
    try:
        out, err = p.communicate(timeout=max(1, timeout))
        return p.returncode, out, err.decode('utf-8', 'replace')
    except subprocess.TimeoutExpired:
        kill_tree(p)
        try:
            p.communicate(timeout=5)
        except Exception:
            pass
        return None, b'', 'timeout'
    finally:
        _procs.discard(p)


def kill_tree(p):
    try:
        if os.name == 'nt':
            subprocess.run(['taskkill', '/F', '/T', '/PID', str(p.pid)], capture_output=True)
        else:
            os.killpg(p.pid, signal.SIGKILL)
    except Exception:
        p.kill()


def err_line(err):
    lines = [ln for ln in (err or '').strip().splitlines() if ln.strip() and 'Deprecated Feature' not in ln]
    pick = [ln for ln in lines if 'ERROR' in ln] or lines or ['no error output (stopped early?)']
    return pick[-1][:180]


# ---------------------------------------------------------------- yt-dlp

class YT:
    def __init__(self, cookies, js):
        self.cookies = cookies if cookies and os.path.exists(cookies) else None
        # cookie copies live outside the output dir so they never end up in an artifact
        self.workdir = tempfile.mkdtemp(prefix='clip_finder_')
        self.base = [sys.executable, '-m', 'yt_dlp', '--ignore-config', '--no-warnings',
                     '--socket-timeout', '20', '--retries', '2', '--force-ipv4']
        if js == 'auto':
            js = None if shutil.which('deno') else next(
                (r for r in ('node', 'bun') if shutil.which(r)), None)
        if js and js != 'none':
            self.base += ['--js-runtimes', js]
        if importlib.util.find_spec('yt_dlp_ejs') is None:
            self.base += ['--remote-components', 'ejs:github']

    def __call__(self, args, timeout):
        # yt-dlp rewrites the cookie jar on exit, so parallel calls each get a copy
        cmd, tmp = list(self.base), None
        if self.cookies:
            tmp = os.path.join(self.workdir, f'cookies_{uuid.uuid4().hex}.txt')
            shutil.copyfile(self.cookies, tmp)
            cmd += ['--cookies', tmp]
        try:
            return run(cmd + args, timeout)
        finally:
            if tmp and os.path.exists(tmp):
                os.remove(tmp)


def search(yt, query, theme, vibe, n=25):
    url = ('https://www.youtube.com/results?search_query=' + urllib.parse.quote_plus(query)
           + '&sp=' + SP_SHORT)
    rc, out, err = yt(['--flat-playlist', '-J', '--playlist-end', str(n), url], 45)
    if rc != 0:
        log(f'search failed "{query}": {err_line(err)}')
        return []
    try:
        entries = json.loads(out).get('entries') or []
    except ValueError:
        return []
    res = []
    for e in entries:
        if not e or not e.get('id') or e.get('_type') not in (None, 'url'):
            continue
        res.append({'id': e['id'], 'title': e.get('title') or '', 'description': e.get('description') or '',
                    'duration': e.get('duration'), 'views': e.get('view_count'),
                    'channel': e.get('channel') or e.get('uploader') or '',
                    'channel_id': e.get('channel_id') or '', 'uploader_id': e.get('uploader_id') or '',
                    'live': e.get('live_status') in ('is_live', 'is_upcoming'),
                    'query': query, 'theme': theme, 'vibe': vibe})
    return res


# ---------------------------------------------------------------- filters

def _norm(text):
    return unicodedata.normalize('NFKC', text or '').lower()


def _word_re(words):
    """Whole-word match; word edges only matter for ASCII letters/digits (not CJK)."""
    def one(w):
        body = r'[\s_\-]*'.join(map(re.escape, w.split()))
        head = r'(?<![a-z0-9])' if w[0].isascii() and w[0].isalnum() else ''
        tail = r'(?![a-z0-9])' if w[-1].isascii() and w[-1].isalnum() else ''
        return head + body + tail
    return re.compile('|'.join(one(w) for w in sorted(words, key=len, reverse=True)))


NSFW_RE = _word_re(NSFW_WORDS)
SHOW_RE = _word_re(UNSAFE_SHOWS)
DARK_RE = _word_re(DARK_WORDS)
OFFTYPE_RE = _word_re(OFFTYPE_WORDS)
DANCE_RE = _word_re(DANCE_WORDS)
VIBE_RISK_RE = {v: _word_re(w) for v, w in VIBE_RISK_WORDS.items()}
SCHOOL_KID_RE = _word_re(SCHOOL_KID_WORDS)
VIBE_RE = {v: _word_re(w) for v, w in VIBE_WORDS.items()}
GENERIC_RE = _word_re(GENERIC_WORDS)
OFFTHEME_WORD_RE = _word_re(OFFTHEME_WORDS)
THEME_RE = {'anime': _word_re(ANIME_WORDS), 'kpop': _word_re(KPOP_WORDS), 'chinese': _word_re(CHINESE_WORDS),
            'japanese': _word_re(JAPANESE_WORDS)}
ASIA_RE = _word_re(ASIA_WORDS)


def _text(*parts):
    return _norm(' '.join(p if isinstance(p, str) else ' '.join(map(str, p or [])) for p in parts))


def text_reason(*parts, vibe=None):
    """Reject reason for title/description/tags text, or None."""
    t = _text(*parts)
    flat = re.sub(r'[^a-z0-9+#]+', ' ', t)
    if NSFW_RE.search(t) or any(c in t for c in NSFW_CHARS) or SHOW_RE.search(flat) or RISKY_RE.search(flat):
        return 'nsfw'
    if vibe == 'dance' and VIBE_RISK_RE['dance'].search(re.sub(r'stray[\s_\-]*kids', 'skz', t)):
        return 'nsfw'
    if vibe in ('cool', 'fight') and SCHOOL_KID_RE.search(t):
        return 'kids'
    if DARK_RE.search(t) or (vibe == 'fight' and VIBE_RISK_RE['fight'].search(t)):
        return 'dark'
    if OFFTYPE_RE.search(t) or (vibe != 'dance' and DANCE_RE.search(t)):
        return 'offtype'
    return None


def channel_nsfw(meta):
    name = _norm(' '.join(str(meta.get(k) or '') for k in ('channel', 'uploader', 'uploader_id')))
    return any(w in name for w in CHANNEL_NSFW)


def offtheme(title, tags=()):
    t = ' '.join([title or ''] + list(tags or []))
    return bool(OFFTHEME_RE.search(t) or OFFTHEME_WORD_RE.search(_norm(t)))


def relevant(meta, theme, query=''):
    """True if title / tags / channel / description tie the video to the theme's part of Asia."""
    head = ' '.join([meta.get('title') or '', meta.get('channel') or meta.get('uploader') or '']
                    + list(meta.get('tags') or []))
    text = _norm(head + ' ' + (meta.get('description') or ''))
    # a native-script title from a theme-specific search belongs to it
    q = _norm(query)
    title = meta.get('title') or ''
    if theme == 'anime' and ('anime' in q or 'アニメ' in q) and KANA_RE.search(title):
        return True
    if theme == 'kpop' and any(w in q for w in ('kpop', 'idol', '아이돌', '케이팝')) and HANGUL_RE.search(title):
        return True

    if theme == 'kpop':     # idols and K-variety, not just any Korean video
        return bool(THEME_RE['kpop'].search(text))
    if theme == 'chinese':  # Han characters without kana: Chinese rather than Japanese
        return bool(THEME_RE['chinese'].search(text) or (HAN_RE.search(head) and not KANA_RE.search(head)))
    if theme == 'anime':
        return bool(THEME_RE['anime'].search(text))
    if theme == 'japanese':  # live-action Japan; anime has its own theme
        return bool((THEME_RE['japanese'].search(text) or KANA_RE.search(head))
                    and not THEME_RE['anime'].search(_norm(head)))
    return bool(ASIA_RE.search(text) or ASIAN_SCRIPT_RE.search(head))


def vibe_match(meta, vibe):
    return bool(VIBE_RE[vibe].search(_text(meta.get('title'), meta.get('tags'), meta.get('description'))))


def _ids_from(obj, days):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    def recent(d):
        if d in (None, '', True):
            return True
        try:
            if isinstance(d, (int, float)):
                dt = datetime.fromtimestamp(d / (1000 if d > 1e11 else 1), timezone.utc)
            else:
                dt = datetime.fromisoformat(str(d).replace('Z', '+00:00'))
                dt = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            return dt >= cutoff
        except (ValueError, OverflowError, OSError):
            return True

    def vid(s):
        m = re.search(r'(?:v=|shorts/|youtu\.be/)([\w-]{11})', str(s))
        return m.group(1) if m else str(s).strip()

    out = set()
    if isinstance(obj, dict):
        lst = obj.get('ids') or obj.get('used') or obj.get('videos')
        if isinstance(lst, list):
            return _ids_from(lst, days)
        for k, v in obj.items():
            if recent(v.get('date') or v.get('used_at') if isinstance(v, dict) else v):
                out.add(vid(k))
    elif isinstance(obj, list):
        for v in obj:
            if isinstance(v, dict):
                i = v.get('id') or v.get('source_id') or v.get('video_id') or v.get('url')
                if i and recent(v.get('date') or v.get('used_at') or v.get('ts')):
                    out.add(vid(i))
            elif v:
                out.add(vid(v))
    return out


def load_json(path):
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (ValueError, OSError) as e:
        log(f'cannot read {path}: {e}')
        return None


def load_block(path):
    obj = load_json(path)
    if isinstance(obj, dict):
        obj = obj.get('channels') or obj.get('blocked') or list(obj.keys())
    keys = set()
    for v in obj or []:
        if isinstance(v, dict):
            v = v.get('channel_id') or v.get('channel') or v.get('id') or ''
        v = str(v).strip().lower().rstrip('/')
        if v:
            keys.add(v.rsplit('/', 1)[-1])
    return keys


def is_blocked(e, block):
    if not block:
        return False
    for k in (e.get('channel_id'), e.get('uploader_id'), e.get('channel'), e.get('channel_url')):
        k = str(k or '').strip().lower().rstrip('/')
        if k and (k in block or k.rsplit('/', 1)[-1] in block):
            return True
    return False


def prefilter(e, used, block, min_views, max_dur):
    if e['id'] in used:
        return 'used'
    if is_blocked(e, block):
        return 'blocked'
    if e['live']:
        return 'live'
    if e['duration'] and (e['duration'] > max_dur or e['duration'] < MIN_SEG + 0.5):
        return 'length'
    if e['views'] is not None and e['views'] < min_views:
        return 'views'
    if offtheme(e['title']):
        return 'offtheme'
    if channel_nsfw(e):
        return 'nsfw'
    return text_reason(e['title'], e['description'], vibe=e['vibe'])


def probe(yt, e, used, block, max_dur, min_views):
    """Full metadata for a candidate, or (None, reason)."""
    rc, out, err = yt(['-J', '--no-playlist', '--skip-download',
                       f'https://www.youtube.com/watch?v={e["id"]}'], 45)
    if rc != 0:
        log(f'probe failed {e["id"]}: {err_line(err)}')
        return None, 'unavailable'
    try:
        info = json.loads(out)
    except ValueError:
        return None, 'unavailable'
    if (info.get('age_limit') or 0) > 0:
        return None, 'age'
    if info.get('availability') not in (None, 'public', 'unlisted'):
        return None, 'unavailable'
    if info.get('live_status') in ('is_live', 'is_upcoming', 'post_live'):
        return None, 'live'
    if info.get('id') in used:
        return None, 'used'
    if is_blocked(info, block):
        return None, 'blocked'
    dur = info.get('duration') or 0
    if dur > max_dur or dur < MIN_SEG + 0.5:
        return None, 'length'
    if (info.get('view_count') or 0) < min_views:
        return None, 'views'
    if min(info.get('width') or 999, info.get('height') or 999) < 320:
        return None, 'lowres'
    if offtheme(info.get('title'), info.get('tags')):
        return None, 'offtheme'
    why = text_reason(info.get('title'), info.get('description'), info.get('tags'),
                      info.get('categories'), vibe=e['vibe'])
    if why:
        return None, why
    if channel_nsfw(info):
        return None, 'nsfw'
    title = (info.get('title') or '')[:60]
    if not relevant(info, e['theme'], e['query']):
        log(f'not {e["theme"]}-related, skipped {info["id"]}: {title!r}')
        return None, 'offtheme'
    if e['vibe'] in VIBE_REQUIRED and not vibe_match(info, e['vibe']):
        log(f'not {e["vibe"]}, skipped {info["id"]}: {title!r}')
        return None, 'offvibe'
    return info, None


def window(info):
    """(start, end) worth analysing: the whole video, or ANALYZE_MAX s around the heatmap peak."""
    dur = info.get('duration') or 0
    if dur <= ANALYZE_MAX:
        return 0.0, float(dur)
    hm = [m for m in info.get('heatmap') or [] if 0.06 * dur < m.get('start_time', 0) < 0.94 * dur]
    peak = max(hm, key=lambda m: m.get('value', 0))['start_time'] if hm else dur * 0.35
    b = min(float(dur), max(0.0, peak - ANALYZE_MAX / 2) + ANALYZE_MAX)
    return b - ANALYZE_MAX, b


def download(yt, info, src_dir, timeout):
    """(path, offset): offset is where the file starts in the source video."""
    vid = info['id']
    ij = os.path.join(src_dir, f'{vid}.info.json')
    with open(ij, 'w', encoding='utf-8') as f:
        json.dump({k: v for k, v in info.items() if not k.startswith('_')}, f)
    args = ['-f', 'bv*+ba/b', '-S', 'res:720,vcodec:h264,acodec:aac', '--merge-output-format', 'mkv',
            '--no-part', '-o', os.path.join(src_dir, f'{vid}.%(ext)s')]
    offset = 0.0
    if (info.get('duration') or 0) > WHOLE_MAX:
        # ffmpeg-based section download: slow on some videos, so only for long ones
        offset, b = window(info)
        args += ['--download-sections', f'*{offset:.1f}-{b:.1f}']
    end = time.time() + timeout
    err = ''
    for src in (['--load-info-json', ij], [f'https://www.youtube.com/watch?v={vid}']):
        rc, _, err = yt(src + args, end - time.time())
        files = [p for p in glob.glob(os.path.join(src_dir, f'{vid}.*'))
                 if p.rsplit('.', 1)[-1] in ('mkv', 'mp4', 'webm') and os.path.getsize(p) > 50000]
        if rc == 0 and files:
            return files[0], offset
        for p in glob.glob(os.path.join(src_dir, f'{vid}.*')):
            if not p.endswith('.info.json'):
                os.remove(p)
        if rc is None or end - time.time() < 20:
            break
    log(f'download failed {vid}: {err_line(err)}')
    return None, 0.0


def velocity(info):
    """Views per day since upload."""
    try:
        up = datetime.strptime(info.get('upload_date') or '', '%Y%m%d').replace(tzinfo=timezone.utc)
        days = max(1.0, (NOW - up).total_seconds() / 86400)
    except ValueError:
        days = 365.0
    return (info.get('view_count') or 0) / days


def popularity(info):
    """0-1 from views and view velocity."""
    v, vel = info.get('view_count') or 0, velocity(info)
    return (0.6 * min(1, max(0, (math.log10(v + 1) - 4.5) / 3))
            + 0.4 * min(1, max(0, (math.log10(vel + 1) - 2) / 3)))


def length_bonus(dur):
    """Standalone Shorts first, long compilations last."""
    if not dur:
        return 0.6
    if 6 <= dur <= 40:
        return 1.0
    if dur <= 60:
        return 0.7
    return 0.0 if dur <= 120 else -0.7


def title_bonus(meta, vibe):
    t = _text(meta.get('title'))
    b = 0.8 if VIBE_RE[vibe].search(t) else (-1.0 if vibe in VIBE_REQUIRED else 0.0)
    return b - (0.4 if GENERIC_RE.search(t) else 0.0)


def pre_rank(entries, rng):
    """Order search results before probing: views, shortness, vibe words, Asia signal."""
    def key(e):
        sc = math.log10((e['views'] or 100000) + 1) + length_bonus(e['duration']) + title_bonus(e, e['vibe'])
        if not relevant(e, e['theme'], e['query']):
            sc -= 0.7   # tags may still carry it, so probe later rather than drop
        return sc + rng.uniform(0, 0.5)
    by_q = {}
    for e in sorted(entries, key=key, reverse=True):
        by_q.setdefault(e['query'], []).append(e)
    queues = list(by_q.values())
    rng.shuffle(queues)
    out = []
    while any(queues):
        for q in queues:
            if q:
                out.append(q.pop(0))
    return out


def source_rank(info, rng):
    """Order probed sources for download: views, view velocity, Shorts, vertical, vibe words."""
    sc = math.log10((info.get('view_count') or 0) + 1) + 0.6 * math.log10(velocity(info) + 1)
    sc += length_bonus(info.get('duration')) + title_bonus(info, info['_vibe'])
    if (info.get('height') or 0) > (info.get('width') or 0):
        sc += 0.5
    return sc + rng.uniform(0, 0.3)


# ---------------------------------------------------------------- analysis

def load_models():
    global HAAR
    cache = os.environ.get('CLIP_FINDER_CACHE') or os.path.join(os.path.expanduser('~'), '.cache', 'clip_finder')
    for key, (name, min_size, url) in MODELS.items():
        path = os.path.join(cache, name)
        try:
            if not os.path.exists(path) or os.path.getsize(path) < min_size:
                os.makedirs(cache, exist_ok=True)
                url, _, member = url.partition('#')
                data = urllib.request.urlopen(url, timeout=30).read()
                if member:  # PyPI JSON -> wheel -> file inside it
                    meta = json.loads(data)
                    whl = next(u['url'] for u in meta['urls'] if u['filename'].endswith('.whl'))
                    data = zipfile.ZipFile(io.BytesIO(urllib.request.urlopen(whl, timeout=60).read())).read(member)
                if len(data) < min_size:
                    raise ValueError('short download')
                with open(path + '.tmp', 'wb') as f:
                    f.write(data)
                os.replace(path + '.tmp', path)
            PATHS[key] = path
        except Exception as e:
            log(f'{key} model unavailable: {e}')
    if 'face' not in PATHS or not hasattr(cv2, 'FaceDetectorYN'):
        PATHS.pop('face', None)
        haar = os.path.join(getattr(getattr(cv2, 'data', None), 'haarcascades', ''), 'haarcascade_frontalface_default.xml')
        if hasattr(cv2, 'CascadeClassifier') and os.path.exists(haar):
            HAAR = haar
    if 'nude' not in PATHS:
        log('WARNING: no nudity check on frames; relying on title/tag filters only')


def faces(img):
    """[(x, y, w, h, conf)] in 0-1 frame units."""
    h, w = img.shape[:2]
    if 'face' in PATHS:
        det = getattr(_tls, 'yunet', None)
        if det is None:
            det = _tls.yunet = cv2.FaceDetectorYN.create(PATHS['face'], '', (w, h), 0.7, 0.3, 20)
        det.setInputSize((w, h))
        _, found = det.detect(img)
        if found is None:
            return []
        return [(f[0] / w, f[1] / h, f[2] / w, f[3] / h, float(f[-1])) for f in found]
    if HAAR:
        det = getattr(_tls, 'haar', None)
        if det is None:
            det = _tls.haar = cv2.CascadeClassifier(HAAR)
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        return [(x / w, y / h, fw / w, fh / h, 0.8) for x, y, fw, fh in det.detectMultiScale(g, 1.2, 5)]
    return []


def nude(img):
    """True if NudeNet sees exposed private parts or swimwear/underwear-level coverage."""
    if 'nude' not in PATHS:
        return False
    net = getattr(_tls, 'nude', None)
    if net is None:
        net = _tls.nude = cv2.dnn.readNet(PATHS['nude'])
    m = max(img.shape[:2])
    sq = cv2.copyMakeBorder(img, 0, m - img.shape[0], 0, m - img.shape[1], cv2.BORDER_CONSTANT)
    net.setInput(cv2.dnn.blobFromImage(sq, 1 / 255.0, (320, 320), swapRB=True))
    sc = np.squeeze(net.forward()).T[:, 4:].max(0)  # best score per class
    # tuned to over-reject: a bruised knee still scores ~0.7 "exposed breast"
    return bool(sc[NUDE_EXPOSED].max() >= 0.45 or sc[NUDE_G_COV] >= 0.45 or sc[NUDE_BUTT_COV] >= 0.6
                or sc[NUDE_BREAST_COV] >= 0.7 or (sc[NUDE_BREAST_COV] >= 0.45 and sc[NUDE_BELLY] >= 0.45))


_K3 = np.ones((3, 3), np.uint8)


def text_mask(g):
    """Burned-in text lines: dense strong-edge blobs, wide and short."""
    h, w = g.shape
    grad = cv2.morphologyEx(g, cv2.MORPH_GRADIENT, _K3)
    bw = (grad > 90).astype(np.uint8)
    bw = cv2.morphologyEx(bw, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (max(5, w // 32), 1)))
    st = cv2.connectedComponentsWithStats(bw, connectivity=8)[2][1:]
    _, _, bw_, bh, area = st.T
    ls = max(h, w)
    keep = ((bh >= 0.018 * ls) & (bh <= 0.14 * ls) & (bw_ >= 2.2 * bh) & (bw_ >= 0.08 * w)
            & (area >= 0.45 * bw_ * bh))
    mask = np.zeros((h, w), np.float32)
    for x0, y0, ww, hh in st[keep][:, :4]:
        mask[y0:y0 + hh, x0:x0 + ww] = 1
    return mask


def media_info(path):
    r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'stream=codec_type,width,height:format=duration',
                        '-of', 'json', path], capture_output=True, text=True, timeout=30)
    d = json.loads(r.stdout or '{}')
    v = next((s for s in d.get('streams', []) if s.get('codec_type') == 'video'), None)
    if not v:
        return None
    audio = any(s.get('codec_type') == 'audio' for s in d.get('streams', []))
    return v['width'], v['height'], float(d.get('format', {}).get('duration') or 0), audio


def loudness(path, ss, span, n):
    """Per-analysis-frame loudness in dB (-80 when silent / no audio)."""
    db = np.full(n, -80.0)
    rc, out, _ = run(['ffmpeg', '-v', 'error', '-ss', f'{ss:.2f}', '-t', f'{span:.2f}', '-i', path,
                      '-map', '0:a:0', '-ac', '1', '-ar', '8000', '-f', 's16le', '-'], 60)
    if rc != 0 or not out:
        return db
    x = np.frombuffer(out, np.int16).astype(np.float32) / 32768
    hop = 8000 // FPS
    m = min(n, len(x) // hop)
    if m:
        rms = np.sqrt((x[:m * hop].reshape(m, hop) ** 2).mean(1))
        db[:m] = 20 * np.log10(rms + 1e-4)
    return db


def rolling_median(x, k):
    k = max(1, min(k, len(x)) | 1)
    xp = np.pad(x, k // 2, mode='edge')
    return np.median(np.lib.stride_tricks.sliding_window_view(xp, k), axis=1)


def content_box(thumbs):
    """Active picture area (x0, y0, x1, y1) in 0-1 units: trims static dark or flat borders."""
    if len(thumbs) < 30:
        return 0.0, 0.0, 1.0, 1.0
    st = np.stack(thumbs[::2]).astype(np.float32)
    act, mean = st.std(0), st.mean(0)
    h, w = act.shape

    def border(axis, rev):
        a, m, flat = act.mean(axis), mean.mean(axis), mean.std(axis)
        dead = (a < 2.0) & ((m < 40) | (flat < 10))
        dead = dead[::-1] if rev else dead
        k = 0
        while k < len(dead) and dead[k]:
            k += 1
        return k

    top, bot, left, right = border(1, False), border(1, True), border(0, False), border(0, True)
    if h - top - bot < 0.4 * h:
        top = bot = 0
    if w - left - right < 0.4 * w:
        left = right = 0
    return left / w, top / h, (w - right) / w, (h - bot) / h


def dhash(g):
    s = cv2.resize(g, (9, 8), interpolation=cv2.INTER_AREA)
    return int(''.join('1' if b else '0' for b in (s[:, 1:] > s[:, :-1]).flatten()), 2)


def heat_curve(info, t):
    """Most-replayed value per analysis frame, or zeros when it is only a retention curve."""
    hm, dur = info.get('heatmap') or [], info.get('duration') or 0
    out = np.zeros(len(t))
    rest = [m['value'] for m in hm if m.get('start_time', 0) >= 0.12 * dur]
    # the start is always "most replayed"; Shorts only give a decaying curve with no bump
    if dur < 8 or not rest or max(rest) < rest[0] + 0.1:
        return out
    for m in hm:
        out[(t >= m['start_time']) & (t < m['end_time'])] = m.get('value', 0)
    out[t < 0.12 * dur] = 0
    return out / max(rest)


def analyze(path, info, offset, theme, vibe):
    """Up to 3 non-overlapping 4-6 s moments of one video for a vibe, best first."""
    mi = media_info(path)
    if not mi:
        return []
    W, H, fdur, has_audio = mi
    a, b = window(info)
    ss = max(0.0, a - offset)
    span = min(fdur - ss, b - a) if fdur else b - a
    vdur = info.get('duration') or 0
    at_start, at_end = offset + ss < 0.5, offset + ss + span > vdur - 0.5
    if W >= H:
        aw, ah = AN, max(2, int(round(AN * H / W / 2)) * 2)
    else:
        ah, aw = AN, max(2, int(round(AN * W / H / 2)) * 2)
    tw, th = max(8, aw // 5), max(8, ah // 5)
    proc = popen(['ffmpeg', '-v', 'error', '-ss', f'{ss:.2f}', '-t', f'{span:.2f}', '-i', path,
                  '-map', '0:v:0', '-vf', f'fps={FPS},scale={aw}:{ah}:flags=area',
                  '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-'], stderr=subprocess.DEVNULL)
    size = aw * ah * 3
    thumbs, hsv_d, samples = [], [], []
    prev = None
    i = 0
    while i < FPS * (ANALYZE_MAX + 5):
        buf = proc.stdout.read(size)
        if len(buf) < size:
            break
        f = np.frombuffer(buf, np.uint8).reshape(ah, aw, 3)
        small = cv2.resize(f, (tw, th), interpolation=cv2.INTER_AREA)
        thumbs.append(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY))
        hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV).astype(np.int16)
        hsv_d.append(0.0 if prev is None else float(np.abs(hsv - prev).mean()))
        prev = hsv
        if i % SAMPLE == 0:
            j = i // SAMPLE
            g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
            samples.append({'text': cv2.resize(text_mask(g), (GRID, GRID), interpolation=cv2.INTER_AREA),
                            'sharp': float(cv2.Laplacian(g[ah // 5:ah - ah // 5, aw // 5:aw - aw // 5], cv2.CV_32F).var()),
                            'faces': faces(f), 'nude': nude(f) if j % NUDE_EVERY == 0 else False})
        i += 1
    proc.stdout.close()
    proc.kill()
    proc.wait()
    _procs.discard(proc)
    n = len(thumbs)
    if n < FPS * MIN_SEG:
        return []
    s_nude = np.array([s['nude'] for s in samples])
    if s_nude.sum() >= 3:
        log(f'{info["id"]}: nudity check hit on {int(s_nude.sum())} frames, dropping source')
        _bump('rejected', 'nsfw_frames')
        return []

    x0, y0, x1, y1 = content_box(thumbs)
    cy0, cx0 = int(y0 * th), int(x0 * tw)
    cy1, cx1 = max(cy0 + 1, int(round(y1 * th))), max(cx0 + 1, int(round(x1 * tw)))
    crop = np.stack([t[cy0:cy1, cx0:cx1] for t in thumbs]).astype(np.float32)
    flat = crop.reshape(n, -1).astype(np.int16)
    modes = np.array([np.bincount(r, minlength=256).argmax() for r in flat])
    mot = np.zeros(n)
    mot[1:] = np.abs(np.diff(crop, axis=0)).mean((1, 2)) / 255
    hd = np.array(hsv_d)

    # scene cuts: content jump well above its neighbourhood (PySceneDetect-style)
    base = rolling_median(hd, 11)
    cuts = [k for k in range(1, n) if hd[k] >= 45 or (hd[k] >= 22 and hd[k] >= 2.6 * base[k] + 3)]
    for k in cuts:
        mot[k] = mot[k - 1]
    spike = np.maximum(0, mot - rolling_median(mot, 31))
    db = loudness(path, ss, span, n) if has_audio else np.full(n, -80.0)
    loud = np.clip(db - rolling_median(db, 61), 0, None)
    loud[db < -45] = 0
    onset = np.zeros(n)
    onset[2:] = np.maximum(0, db[2:] - db[:-2])
    onset[db < -45] = 0
    t = offset + ss + np.arange(n) / FPS
    heat = heat_curve(info, t)

    gy0, gx0 = int(y0 * GRID), int(x0 * GRID)
    gy1, gx1 = max(gy0 + 1, int(round(y1 * GRID))), max(gx0 + 1, int(round(x1 * GRID)))
    carea = max(1e-6, (x1 - x0) * (y1 - y0))
    s_face = np.array([max([fw * fh / carea for _, _, fw, fh, _ in s['faces']] + [0.0]) for s in samples])
    s_face[s_face < 0.002] = 0
    # motion impacts (hits): sudden motion, helped by a loudness onset
    hit = np.minimum(1, spike / 0.05) + 0.6 * np.minimum(1, onset / 6)
    smooth = np.convolve(mot, np.ones(10) / 10, mode='same')
    if vibe == 'dance':     # sustained movement, then the sharpest move
        ev = np.minimum(1, smooth / 0.05) + 0.5 * np.minimum(1, spike / 0.05) + 0.5 * heat
    elif vibe == 'fight':
        ev = hit + heat
    else:
        ev = np.minimum(1, spike / 0.06) + np.minimum(1, loud / 9) + 1.2 * heat
    c = {
        'n': n, 'crop': crop, 'mot': mot, 'spike': spike, 'loud': loud, 'heat': heat, 'cuts': cuts,
        'hit': hit, 'ev': np.convolve(ev, np.ones(5) / 5, mode='same'), 'vibe': vibe,
        'luma': crop.mean((1, 2)), 'detail': crop.std((1, 2)),
        'black': (crop < 20).mean((1, 2)),                                # small picture on a black canvas
        'uniform': (np.abs(flat - modes[:, None]) <= 8).mean(1),          # plain text / logo cards
        'text': np.array([float(s['text'][gy0:gy1, gx0:gx1].mean()) for s in samples]),
        'face': s_face, 'nude': s_nude, 'sharp': np.array([s['sharp'] for s in samples]),
        'lowres': min(W, H) < 480, 'vertical': H > W,
    }
    short = vdur <= 60
    lo = 1 if short or not at_start else 5
    hi = n - ((3 if short else 25) if at_end else 1)        # Shorts loop, long videos end on cards
    if hi - lo < MIN_SEG * FPS:
        _bump('seg_rejected', 'short')
        return []
    if hi - lo <= MAX_SEG * FPS:
        wins = [(lo, hi)]                                    # the whole clip is the moment
    else:
        wins = [w for w in (_build(p, lo, hi, cuts) for p in _peaks(c['ev'], lo, hi)) if w]
    scored, whys = [], Counter()
    for s, e in wins:
        sc, why, extra = _score(c, s, e)
        if why:
            whys[why] += 1
        else:
            scored.append((sc, s, e, extra))
    if not scored:
        if whys:
            _bump('seg_rejected', whys.most_common(1)[0][0])
        return []
    scored.sort(key=lambda r: -r[0])
    pop = popularity(info)
    segs = []
    for sc, s, e, extra in scored:
        if any(s < o['_e'] and o['_s'] < e for o in segs):
            continue
        segs.append({
            'source_id': info['id'], 'source_channel': info.get('channel') or info.get('uploader') or '',
            'source_channel_id': info.get('channel_id') or '',
            'source_title': info.get('title') or '', 'theme': theme, 'vibe': vibe,
            'views': info.get('view_count') or 0,
            'start': round(float(t[s]), 2), 'end': round(float(t[s]) + (e - s) / FPS, 2),
            'duration': round((e - s) / FPS, 2), 'peak': extra['peak'], 'hits': extra['hits'],
            'hook': round(float(extra['hook']), 3), 'score': round(float(0.65 * sc + 0.35 * pop), 4),
            'loop': extra['loop'], 'cuts': extra['cuts'],
            'has_face': extra['has_face'], 'has_caption': extra['has_caption'],
            'motion': extra['motion'], 'width': W - W % 2, 'height': H - H % 2,
            'content_box': [round(v, 3) for v in (x0, y0, x1, y1)],
            '_src': path, '_local': ss + s / FPS, '_dur': (e - s) / FPS, '_s': s, '_e': e,
            '_hash': dhash(thumbs[(s + e) // 2][cy0:cy1, cx0:cx1]),
        })
        if len(segs) == 3:
            break
    _bump('analyzed')
    _bump('segments', n=len(segs))
    return segs


def _peaks(evs, lo, hi, k=4, gap=40):
    """Frames of the k strongest event peaks, at least gap frames apart."""
    picks = []
    for i in np.argsort(-evs[lo:hi]) + lo:
        if all(abs(i - j) >= gap for j in picks):
            picks.append(int(i))
            if len(picks) == k:
                break
    return picks


def _build(p, lo, hi, cuts):
    """(start, end) frames of a 4-6 s moment around peak p: inside its shot when the shot is long
    enough, else across shots starting and ending on cuts where possible."""
    L, lmin = int(TARGET_SEG * FPS), int(MIN_SEG * FPS)
    s0 = max([lo] + [k + 1 for k in cuts if k <= p])
    e0 = min([hi] + [k for k in cuts if k > p])
    if e0 - s0 >= lmin:
        L = min(L, e0 - s0)
        s = min(max(p - int(0.4 * L), s0), e0 - L)     # peak ~40 % in: setup, hit, follow-through
        return s, s + L
    s = p - 18
    near = [k + 1 for k in cuts if abs(k + 1 - s) <= 6]
    s = max(lo, near[0] if near else s)
    e = min(hi, s + L)
    ends = [k for k in cuts if abs(k - e) <= 6 and k - s >= lmin]
    e = ends[-1] if ends else e
    if e - s < lmin:
        s = max(lo, e - lmin)
    return (s, e) if e - s >= lmin else None


def _samples(a, b, n):
    js = [j for j in range(-(-a // SAMPLE), -(-b // SAMPLE)) if j < n]
    return js or [min(n - 1, ((a + b) // 2) // SAMPLE)]


def _local_peaks(x, floor, gap=3):
    """Indices of local maxima above floor, strongest first, at least gap apart."""
    idx = [i for i in range(1, len(x) - 1) if x[i] >= floor and x[i] >= x[i - 1] and x[i] >= x[i + 1]]
    out = []
    for i in sorted(idx, key=lambda i: -x[i]):
        if all(abs(i - j) >= gap for j in out):
            out.append(i)
    return sorted(out)


def _score(c, s, e):
    """(score, reject_reason, extras) of window frames s..e for the vibe in c."""
    js = _samples(s, e, len(c['text']))
    near = range(max(0, js[0] - NUDE_EVERY * 2), min(len(c['nude']), js[-1] + NUDE_EVERY * 2 + 1))
    if c['nude'][near].any():
        return 0, 'nsfw', None
    lm, dt = c['luma'][s:e].mean(), c['detail'][s:e].mean()
    m = c['mot'][s + 1:e].mean()
    text, sharp = c['text'][js], float(np.median(c['sharp'][js]))
    if lm < 25 or c['black'][s:e].mean() > 0.7:
        return 0, 'dark', None
    if dt < 12 or (c['uniform'][s:e] > 0.8).mean() > 0.2:
        return 0, 'blank', None
    if m < 0.004 and c['spike'][s:e].max() < 0.01:
        return 0, 'static', None
    if (c['crop'][s:e].std(0) < 2.5).mean() > 0.6:   # chat screenshots / stills with a small moving inset
        return 0, 'still', None
    if text.mean() > 0.45:          # captions are welcome, text cards are not
        return 0, 'text', None
    if sharp < 40:
        return 0, 'blurry', None
    vibe = c['vibe']
    hit = c['hit'][s:e]
    hits = _local_peaks(hit, max(0.5, 0.4 * hit.max()))
    key = hit if vibe in ('fight', 'dance') else c['ev'][s:e]
    p = int(np.argmax(key))
    face = c['face'][js]
    nf = float(np.minimum(1, face / 0.03).mean())
    hj = _samples(s, s + 6, len(c['face']))
    hook = (0.4 * min(1, c['mot'][s + 1:s + 5].mean() / 0.04)
            + 0.35 * min(1, c['face'][hj].max() / 0.03)
            + 0.25 * min(1, c['detail'][s:s + 5].mean() / 60))
    loop = 1 - min(1, float(np.abs(c['crop'][s] - c['crop'][e - 1]).mean()) / 40)
    punch = min(1, c['ev'][s + p] / 1.2)
    nm = min(1, m / 0.05)
    n_cuts = sum(1 for k in c['cuts'] if s < k < e)
    if vibe == 'dance':
        steady = float((c['mot'][s + 1:e] > 0.012).mean())
        sc = (0.3 * nm + 0.25 * steady + 0.1 * min(1, len(hits) / 6) + 0.15 * hook + 0.1 * loop
              + 0.1 * min(1, dt / 60))
        sc *= (1.0, 0.8, 0.65)[min(n_cuts, 2)]          # dance reads best as one continuous shot
    elif vibe == 'fight':
        sc = (0.3 * min(1, hit.max()) + 0.2 * min(1, len(hits) / 4) + 0.15 * nm + 0.15 * hook
              + 0.1 * min(1, c['loud'][s:e].max() / 9) + 0.1 * float(c['heat'][s:e].max()))
        sc *= 1.0 if n_cuts <= 3 else 0.8 if n_cuts <= 6 else 0.6
    elif vibe == 'cool':
        sc = (0.25 * punch + 0.2 * min(1, dt / 60) + 0.15 * nf + 0.15 * hook + 0.1 * loop + 0.15 * nm)
        sc *= (1.0, 0.9, 0.8, 0.7)[min(n_cuts, 3)]
    else:  # cute
        sc = (0.25 * nf + 0.2 * punch + 0.15 * hook + 0.15 * min(1, lm / 120) + 0.1 * loop
              + 0.15 * min(1, m / 0.03))
        sc *= (1.0, 0.9, 0.8, 0.7)[min(n_cuts, 3)]
    if sharp < 150:
        sc *= 0.8
    if lm < 45 and vibe != 'cool':
        sc *= 0.8
    if c['lowres']:
        sc *= 0.85
    has_caption = bool(np.median(text) >= 0.02)
    sc += 0.05 * c['vertical'] + 0.02 * has_caption
    return sc, None, {
        'hook': hook, 'peak': round(p / FPS, 2), 'hits': [round(h / FPS, 2) for h in hits][:12],
        'loop': round(loop, 3), 'cuts': n_cuts, 'has_caption': has_caption,
        'has_face': bool((face >= 0.004).mean() >= 0.5), 'motion': round(float(nm), 3)}


# ---------------------------------------------------------------- output

TITLE_JUNK_RE = re.compile(_chars((0x30, 0x39), (0x61, 0x7A), KANA, HAN, HANGUL, neg=True) + '+')


def _title_key(s):
    return TITLE_JUNK_RE.sub('', _norm(s['source_title']))[:30]


def _series(title):
    """"Chibi Titans 2" and "Chibi Titans 4" are one series: treat them like one channel."""
    return ' '.join(_norm(title).split()[:2])


def spread_channels(infos):
    """Keep rank order but put each channel's / series' first video ahead of its repeats."""
    seen, first, rest = set(), [], []
    for i in infos:
        keys = {i.get('channel_id') or i.get('channel') or i['id'], _series(i.get('title'))}
        (rest if keys & seen else first).append(i)
        seen |= keys
    return first + rest


def select(segs, wanted, mixed=False):
    """Per vibe, the best segments; every clip from a different video, other channels (and for
    mixed, other themes) first. Returns clips grouped by vibe in `wanted` order, best first."""
    pools = {v: sorted([s for s in segs if s['vibe'] == v], key=lambda s: -s['score']) for v in wanted}
    chosen = {v: [] for v in wanted}
    picked = []

    def series(s):
        return _series(s['source_title'])

    def ok(s, same_channel_ok, new_theme):
        chan = s['source_channel_id'] or s['source_channel']
        if new_theme and any(c['theme'] == s['theme'] for c in picked):
            return False
        for c in picked:
            if c['source_id'] == s['source_id']:
                return False
            if not same_channel_ok and ((chan and (c['source_channel_id'] or c['source_channel']) == chan)
                                        or series(c) == series(s)):
                return False
            if bin(c['_hash'] ^ s['_hash']).count('1') <= 10 or (_title_key(c) and _title_key(c) == _title_key(s)):
                return False    # the same viral clip re-uploaded by another channel
        return True

    passes = ((False, True), (False, False), (True, False)) if mixed else ((False, False), (True, False))
    for rnd in range(max(wanted.values(), default=0)):
        for v, k in wanted.items():
            if len(chosen[v]) > rnd or len(chosen[v]) >= k:
                continue
            for same_channel_ok, new_theme in passes:
                s = next((s for s in pools[v] if s not in picked and ok(s, same_channel_ok, new_theme)), None)
                if s:
                    chosen[v].append(s)
                    picked.append(s)
                    break
    return [s for v in wanted for s in chosen[v]]


def cut(seg, out_path):
    rc, _, _ = run(['ffmpeg', '-v', 'error', '-y', '-ss', f'{seg["_local"]:.3f}', '-i', seg['_src'],
                    '-t', f'{seg["_dur"]:.3f}', '-map', '0:v:0', '-an', '-sn', '-dn',
                    '-vf', 'scale=trunc(iw/2)*2:trunc(ih/2)*2', '-c:v', 'libx264', '-preset', 'veryfast',
                    '-crf', '18', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', out_path], 60)
    return rc == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 1000


def contact_sheet(clips, path, cell=240, cols=4):
    rows = -(-len(clips) // cols)
    sheet = np.full((rows * (cell + 34), cols * cell, 3), 24, np.uint8)
    for k, c in enumerate(clips):
        _, out, _ = run(['ffmpeg', '-v', 'error', '-ss', f'{c["peak"]:.2f}', '-i', c['path'], '-frames:v', '1',
                         '-f', 'image2pipe', '-vcodec', 'png', '-'], 30)
        img = cv2.imdecode(np.frombuffer(out, np.uint8), cv2.IMREAD_COLOR) if out else None
        r, q = divmod(k, cols)
        oy, ox = r * (cell + 34), q * cell
        if img is not None:
            s = cell / max(img.shape[:2])
            img = cv2.resize(img, (max(1, int(img.shape[1] * s)), max(1, int(img.shape[0] * s))))
            y, x = oy + (cell - img.shape[0]) // 2, ox + (cell - img.shape[1]) // 2
            sheet[y:y + img.shape[0], x:x + img.shape[1]] = img
        label = (f'{k} {c["vibe"]} {c["duration"]:.1f}s pk{c["peak"]:.1f} {len(c["hits"])}hits '
                 f'h{c["hook"]:.2f} s{c["score"]:.2f}')
        cv2.putText(sheet, label, (ox + 4, oy + cell + 14), cv2.FONT_HERSHEY_SIMPLEX, 0.37, (230, 230, 230), 1, cv2.LINE_AA)
        cv2.putText(sheet, f'{c["source_id"]} {c["theme"]}', (ox + 4, oy + cell + 29), cv2.FONT_HERSHEY_SIMPLEX, 0.37,
                    (150, 200, 255), 1, cv2.LINE_AA)
    cv2.imwrite(path, sheet)


# ---------------------------------------------------------------- main

def parse_vibes(spec):
    out = {}
    for part in spec.split(','):
        name, _, k = part.strip().partition(':')
        if name not in VIBES:
            raise SystemExit(f'unknown vibe {name!r}; use {", ".join(VIBES)}')
        out[name] = max(1, int(k or 1))
    return out


def pick_queries(theme, vibes, rng):
    """(query, theme, vibe): 4 per vibe (5 for mixed), at least one in the local language when there is one."""
    out = []
    for v in vibes:
        if theme == 'mixed':
            pool = [(q, th) for th in THEMES for q in VIBE_QUERIES[v][th]]
            k = 5
        else:
            pool = [(q, theme) for q in VIBE_QUERIES[v][theme]]
            k = 4
        native = [x for x in pool if not x[0].isascii()]
        pick = rng.sample(native, min(1, len(native)))
        pick += rng.sample([x for x in pool if x not in pick], min(k - len(pick), len(pool) - len(pick)))
        out += [(q, th, v) for q, th in pick]
    return out


def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding='utf-8', errors='replace')
        except AttributeError:
            pass
    ap = argparse.ArgumentParser(description='Find and cut short Asia-related clips per vibe for meme Shorts')
    ap.add_argument('--out-dir', required=True)
    ap.add_argument('--theme', choices=list(THEMES) + ['mixed', 'asian'], default='mixed',
                    help='asian is an old name for funny')
    ap.add_argument('--vibes', default='dance:2,cool:2,fight:2,cute:2', help='vibe:count pairs')
    ap.add_argument('--used', help='JSON of video ids used recently')
    ap.add_argument('--used-days', type=int, default=30)
    ap.add_argument('--block', help='JSON of blocked channels')
    ap.add_argument('--cookies', help='Netscape cookies file for YouTube')
    ap.add_argument('--js-runtimes', default='auto', help='yt-dlp JS runtime: auto, deno, node, bun, none')
    ap.add_argument('--budget', type=float, default=330, help='total seconds')
    ap.add_argument('--min-views', type=int, default=30000)
    ap.add_argument('--max-duration', type=int, default=240)
    ap.add_argument('--workers', type=int, default=3)
    ap.add_argument('--seed', type=int, help='query rotation seed (default: day number)')
    ap.add_argument('--sheet', help='also write a contact sheet PNG of the clips')
    ap.add_argument('--keep-src', action='store_true', help='keep downloaded sources')
    ap.add_argument('--count', type=int, help=argparse.SUPPRESS)       # old options, ignored
    ap.add_argument('--per-source', type=int, help=argparse.SUPPRESS)
    a = ap.parse_args()
    theme = 'funny' if a.theme == 'asian' else a.theme
    wanted = parse_vibes(a.vibes)

    def left():
        return a.budget - (time.time() - T0)

    out_dir = os.path.abspath(a.out_dir)
    src_dir = os.path.join(out_dir, '_src')
    os.makedirs(src_dir, exist_ok=True)
    for old in glob.glob(os.path.join(out_dir, 'clip_*.mp4')):
        os.remove(old)
    manifest = os.path.join(out_dir, 'clips.json')
    rng = random.Random(a.seed if a.seed is not None else int(time.time() // 86400))
    used = _ids_from(load_json(a.used), a.used_days) if a.used else set()
    block = load_block(a.block)
    yt = YT(a.cookies, a.js_runtimes)
    log(f'theme={theme} vibes={wanted} used={len(used)} blocked={len(block)} '
        f'cookies={"yes" if yt.cookies else "no"}')
    models = threading.Thread(target=load_models, daemon=True)
    models.start()

    # 1. search every vibe at once
    queries = pick_queries(theme, wanted, rng)
    found, seen = [], set()
    with cf.ThreadPoolExecutor(4) as ex:
        for res in ex.map(lambda q: search(yt, *q), queries):
            for e in res:
                if e['id'] not in seen:
                    seen.add(e['id'])
                    found.append(e)
    STATS['searched'] = len(found)
    cands = {v: [] for v in wanted}
    for e in found:
        why = prefilter(e, used, block, a.min_views, a.max_duration)
        if why:
            _bump('rejected', why)
        else:
            cands[e['vibe']].append(e)
    cands = {v: pre_rank(c, rng) for v, c in cands.items()}
    STATS['candidates'] = sum(len(c) for c in cands.values())
    log(f'{len(queries)} searches -> {len(found)} results; candidates '
        + ', '.join(f'{v} {len(c)}' for v, c in cands.items()))

    # 2. probe the best candidates of each vibe, then rank by views, velocity, shortness
    def do_probe(e):
        if left() < 90:
            return None
        info, why = probe(yt, e, used, block, a.max_duration, a.min_views)
        _bump('probed')
        if not info:
            _bump('rejected', why)
            return None
        info['_theme'], info['_vibe'] = e['theme'], e['vibe']
        return info

    batch = [e for v, k in wanted.items() for e in cands[v][:k * 4 + 6]]
    infos = {v: [] for v in wanted}
    with cf.ThreadPoolExecutor(6) as ex:
        for info in ex.map(do_probe, batch):
            if info:
                infos[info['_vibe']].append(info)
    for v in infos:
        infos[v] = spread_channels(sorted(infos[v], key=lambda i: -source_rank(i, rng)))
    STATS['kept'] = sum(len(i) for i in infos.values())
    log(f'{STATS["probed"]} probed; kept ' + ', '.join(f'{v} {len(i)}' for v, i in infos.items()))
    models.join(timeout=60)

    # 3. download + analyse, vibes interleaved so the time budget is shared
    need = {v: k + 1 for v, k in wanted.items()}      # sources with a usable moment
    good = Counter()
    stop = threading.Event()
    segs = []

    def work(info):
        v = info['_vibe']
        # keep ~30 s at the end for cutting and the manifest
        if stop.is_set() or good[v] >= need[v] or left() < 50:
            return None
        path, offset = download(yt, info, src_dir, min(60, left() - 35))
        if not path:
            _bump('rejected', 'download')
            return None
        _bump('downloaded')
        found_segs = analyze(path, info, offset, info['_theme'], v)
        log(f'{v}: {info["id"]} {info.get("title", "")[:45]!r} ({info.get("duration")}s, '
            f'{info.get("view_count")} views): {len(found_segs)} moments')
        return found_segs

    order = []
    queues = [list(infos[v]) for v in wanted]
    while any(queues):
        for q in queues:
            if q:
                order.append(q.pop(0))
    ex = cf.ThreadPoolExecutor(a.workers)
    futs = [ex.submit(work, i) for i in order]
    try:
        for fut in cf.as_completed(futs, timeout=max(10, left() - 30)):
            try:
                r = fut.result()
            except Exception as err:
                log(f'candidate error: {err!r}')
                continue
            if r:
                segs += r
                good[r[0]['vibe']] += 1
            if all(good[v] >= need[v] for v in wanted):
                break
    except cf.TimeoutError:
        log('time budget reached, using what is ready')
    stop.set()
    for f in futs:
        f.cancel()
    ex.shutdown(wait=False, cancel_futures=True)
    for p in list(_procs):
        kill_tree(p)

    # 4. select and cut
    chosen = select(segs, wanted, mixed=theme == 'mixed')
    clips = []

    def do_cut(k_s):
        k, s = k_s
        p = os.path.join(out_dir, f'clip_{k:03d}.mp4')
        return (s, p) if cut(s, p) else (s, None)

    with cf.ThreadPoolExecutor(2) as cx:
        for s, p in cx.map(do_cut, enumerate(chosen)):
            if p:
                c = {'path': p}
                c.update({k: v for k, v in s.items() if not k.startswith('_')})
                clips.append(c)
    with open(manifest, 'w', encoding='utf-8') as f:
        json.dump(clips, f, ensure_ascii=False, indent=1)
    if a.sheet and clips:
        contact_sheet(clips, a.sheet)
    if not a.keep_src:
        shutil.rmtree(src_dir, ignore_errors=True)
    shutil.rmtree(yt.workdir, ignore_errors=True)

    per_vibe = {v: sum(1 for c in clips if c['vibe'] == v) for v in wanted}
    stats = dict(STATS, rejected=dict(STATS['rejected']), seg_rejected=dict(STATS['seg_rejected']),
                 sources=len(set(c['source_id'] for c in clips)), seconds=round(time.time() - T0, 1))
    log(f'stats {json.dumps(stats)}')
    print(json.dumps({'ok': bool(clips) and all(per_vibe.values()), 'count': len(clips), 'manifest': manifest,
                      'vibes': per_vibe, 'stats': stats}))
    sys.stdout.flush()
    os._exit(0 if clips else 1)  # don't wait on straggling worker threads


if __name__ == '__main__':
    main()
