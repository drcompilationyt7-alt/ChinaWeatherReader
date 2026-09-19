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

Source mode: every clip from the song's own anime or K-pop group
  python core/meme/clip_finder.py --out-dir work/clips \
      --source "Oshi no Ko" --source-kind anime --aliases "oshi no ko,推しの子,ai hoshino,aqua,ruby" \
      [--edit 6] [--song-audio song.mp3 --song-start 50 --song-len 14 --song-name "idol"]
The tiles are searched as "<source> dance / cool scene / fight scene / cute moments"
(for a group: dance practice / fancam / dance break / cute moments; an idol's "fight"
is their hardest-hitting choreography). A video counts only when its title names the
source or a distinctive alias; a short or common alias ("winter", "aqua") needs the
tags, channel or description to name the source too; an official channel counts on
its own. Covers, reactions, fan art, parodies and crossovers ("X but it's Y",
"Naruto x Chainsaw Man"), comment overlays and MADs are skipped; for an anime,
live action too (concert / dance-video titles, and a frame check: cameras never
repeat a frame, anime holds its drawings). The safety filters stay; the source's own
names are exempt from the unsafe-show list: mainstream shows on it (Chainsaw Man,
Dandadan...) and FranXX / DanMachi / Overlord... run as fan-service-risk sources
(stricter NudeNet, fan-service title words skipped, "funny" instead of "cute"
queries), the rest of it (ecchi-first shows, teen idol groups) is refused. Official
broadcaster / label fancams (직캠) are allowed for a group. A vibe the source cannot
fill takes the best spare clip of the source, tagged "vibe_fallback": true; ok is
false only when fewer than one clip per vibe could be found.
--edit N adds N shots for the full-song edit after the tiles: clean, high-motion
3-8 s shots of the source (4K / twixtor / raw scenes / creditless OP for anime;
stages, performances, dance practices and stage mixes of the song for a group), at
most 2 per video, other videos than the tiles where possible. They keep their audio
(AAC) and carry "motion", "has_audio", "sfx_hits" (clip times of short broadband
impacts, best first) and "sfx_score" (0-1, how usable their sound is for a sound
edit). With --song-audio, shots whose video plays the same song window
[song-start, song-start + song-len] are cut exactly to it (core/meme/align.py) and
marked "aligned": true, "align_score", "song_offset" (song time at clip t=0); they
come first. The manifest is then {"source": {...}, "song": {...}, "clips": [...],
"edit": [...]} and the stdout line adds "source", "edit_count", "aligned_count".
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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import align  # noqa: E402  (core/meme/align.py: song-window alignment, sound hits)

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
SP_VIDEO = 'EgIQAQ%3D%3D'  # type=video, any duration (edit material: raw scene packs run long)

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

# ---- Source mode (--source): every clip shows one anime or one K-pop group.
# {s} is the source name, {song} the song (queries with {song} only when --song-name is given).
SOURCE_QUERIES = {
    'anime': {
        'dance': ['{s} dance', '{s} ending dance', '{s} dance scene', '{s} {song} dance scene'],
        'cool': ['{s} cool scene', '{s} aura edit', '{s} badass moment', '{s} aura moment'],
        'fight': ['{s} fight scene', '{s} best fight', '{s} action scene'],
        'cute': ['{s} cute moments', '{s} funny moments', '{s} cute scene'],
        'edit': ['{s} 4k twixtor', '{s} raw scenes for edits', '{s} 4k scenes', '{s} best animation',
                 '{s} creditless opening', '{s} opening NCOP', '{s} {song} scene', '{s} {song} opening 4k'],
    },
    'group': {   # idols don't fight: their "fight" tile is the hardest-hitting choreography
        'dance': ['{s} dance practice', '{s} dance challenge', '{s} relay dance'],
        'cool': ['{s} fancam', '{s} visual', '{s} walking', '{s} aura'],
        'fight': ['{s} powerful dance break', '{s} intense stage', '{s} dance break'],
        'cute': ['{s} cute moments', '{s} funny moments'],
        'edit': ['{s} {song} stage', '{s} {song} performance 4k', '{s} {song} fancam', '{s} {song} dance practice',
                 '{s} {song} stage mix', '{s} {song} 4k', '{s} stage mix', '{s} stage 4k'],
    },
}
# One native-script query per vibe with a native alias ({n}), by the alias's script.
SOURCE_NATIVE = {
    'ja': {'dance': '{n} ダンス', 'cool': '{n} かっこいい', 'fight': '{n} 戦闘シーン', 'cute': '{n} かわいい',
           'edit': '{n} ノンクレジット'},
    'ko': {'dance': '{n} 안무', 'cool': '{n} 무대', 'fight': '{n} 댄스브레이크', 'cute': '{n} 귀여운',
           'edit': '{n} {song} 교차편집'},
    'zh': {'dance': '{n} 舞蹈', 'cool': '{n} 帅气', 'fight': '{n} 打斗', 'cute': '{n} 可爱', 'edit': '{n} 4K 混剪'},
}
# Single-word aliases that are also everyday words: they need the source named elsewhere.
COMMON_NAMES = {
    'rose', 'winter', 'aqua', 'ruby', 'twice', 'joy', 'hope', 'key', 'rain', 'crush', 'star', 'sunny', 'ivy',
    'may', 'june', 'april', 'summer', 'autumn', 'angel', 'honey', 'candy', 'cherry', 'lucky', 'happy', 'luna',
    'hana', 'kana', 'lisa', 'momo', 'sana', 'mina', 'mark', 'kai', 'leo', 'max', 'sky', 'blue', 'red', 'gold',
    'golden', 'idol', 'kiss', 'baby', 'akane', 'mem', 'gin', 'yuki', 'sora', 'hikari', 'rin', 'ken', 'jin', 'suga',
    'dream', 'magic', 'shine', 'lucy', 'eve', 'ash', 'ace', 'nova', 'wave', 'love', 'heart', 'power',
}
# Not the source itself: covers, reactions, fan art, games, re-edits with other audio.
NOT_SOURCE_WORDS = [
    'cover', 'covers', 'covered', 'dance cover', 'cover dance', 'reaction', 'reactions', 'react', 'reacts',
    'reacting', 'parody', 'fanmade', 'fan made', 'fan animation', 'ai cover', 'ai generated', 'ai art', 'lookalike',
    'look alike', 'impression', 'impressions', 'tutorial', 'lesson', 'in public', 'busking', 'random play',
    'random dance', 'unboxing', 'photocard', 'merch', 'haul', 'figure', 'figures', 'plush', 'fanart', 'fan art',
    'drawing', 'speedpaint', 'speed paint', 'minecraft', 'roblox', 'fortnite', 'gacha', 'sims', 'lego', 'tier list',
    'ranking', 'ranked', 'trivia', 'quiz', 'guess', 'explained', 'review', 'analysis', 'theory', 'theories', 'news',
    'piano', 'guitar', 'violin', 'drum', 'instrumental', 'vocal coach', 'mmd', 'vrchat', 'vtuber', 'nightcore',
    'sped up', 'slowed', 'reverb', '8d', 'mashup', 'mash up', 'recap', 'recaps', 'summary', 'cosplay', 'irl',
    # parodies and crossovers: the source's song over another show's footage
    "but it's", 'but its', 'but it’s', 'but it is', 'in the style of', 'crossover', 'if it was', 'if it were',
    # scrolling viewer comments over the picture; MADs (Japanese fan music videos, like "amv")
    'コメ付き', 'コメント付き', '弾幕', 'danmaku', 'with comments', '【mad', 'mad】', '[mad]', 'mad動画',
    '커버', '반응', '리액션', 'カバー', '踊ってみた', '歌ってみた', '弾いてみた', 'リアクション', '翻跳', '翻唱', '反应',
]
NOT_SOURCE_KIND = {
    'anime': ['live action', 'liveaction', 'real life', 'in real life', 'concert', 'live at', 'live in', 'live ver',
              'live version', 'live stage', 'live tour', 'live with', 'live from', 'live performance', 'live video',
              'live concert', 'tour', 'festival', 'budokan', 'first take', 'beat saber', 'osu', 'rhythm game',
              'voice actor', 'voice actress', 'seiyuu', 'ライブ映像',
              'behind the scenes', 'manga', 'manhwa', 'webtoon', 'light novel', 'gameplay', 'game', 'vs real',
              'animatic', '実写', '声優',
              # real people doing the anime's dance
              'dance practice', 'choreography', 'choreo', 'dance video', 'dance challenge', 'challenge',
              'ダンス映像', 'ダンス動画', '振付', '振り付け', '踊ってみた', 'チャレンジ'],
    'group': ['animation', 'animated', 'cartoon', 'anime', 'kid', 'kids', 'child', 'children', 'lyrics', 'ai'],
}
# More off-brand topics for a named source (its sad arcs are famous): matched on the title.
SOURCE_DARK_WORDS = ['death', 'dies', 'died', 'dying', 'die', 'stab', 'stabbed', 'stabs', 'kill', 'kills',
                     'killed', 'corpse', 'suicide', 'funeral', 'grave', '死', '殺', '사망', '죽음']
# UNSAFE_SHOWS entries that are mainstream enough to be a song's source (Chainsaw Man for
# "KICK BACK", Dandadan for "Otonoke"): as the --source they run as fan-service-risk sources.
# Every other UNSAFE_SHOWS entry (ecchi-first shows, shows sexualising minors, teen idol
# groups with gravure work) is refused as a source.
MAINSTREAM_RISKY = [
    'chainsaw man', 'dandadan', 'konosuba', 'fire force', 'food wars', 'shokugeki', 'dress up darling',
    'sono bisque', 'seven deadly sins', 'nanatsu no taizai', 'fairy tail', 'kill la kill', 'rent a girlfriend',
    'kanokari', 'quintuplets', 'bunny girl', 'nagatoro', 'uzaki', 'shin chan', 'shinchan', 'crayon shin',
    'masamune kun', 'girlfriend girlfriend', 'sanji', 'jiraiya', 'pervy sage', 'roshi', 'mineta', 'happosai',
]
# Not on UNSAFE_SHOWS (fine in theme mode) but with fan-service moments.
RISKY_SOURCES = ['franxx', 'darling in the franxx', 'danmachi', 'dungeon ni deai', 'pick up girls in a dungeon',
                 'overlord', 'shield hero', 'tate no yuusha', 'eminence in shadow', 'kage no jitsuryokusha']
# Titles skipped for a risk source: fan-service framing.
RISKY_TITLE_WORDS = ['hot', 'hottest', 'best girl', 'best girls', 'girls', 'girl moments', 'waifu', 'waifus', 'thicc',
                     'fanservice', 'fan service', 'body', 'curves', 'beach', 'pool', 'towel', 'kiss', 'kissing',
                     'flirt', 'flirting', 'romance', 'romantic', 'bed', 'bedroom', 'outfit', 'costume', 'bunny',
                     'nurse', 'maid', 'step on', 'simp', 'simping', 'rizz', 'attractive', 'beautiful', 'pretty',
                     'mommy', 'daddy', 'wife', 'girlfriend', 'date', 'dating', 'love', 'couple']
# Broadcaster, label and idol-channel uploads: their 직캠 / fancams are official focus cams.
OFFICIAL_CHANNELS = [
    'mnet k pop', 'mnet kpop', 'mnet', 'm2', 'mbckpop', 'mbc kpop', 'sbs kpop', 'sbskpop', 'kbs kpop', 'kbs world tv',
    'kbs', 'sbs', 'mbc', 'jtbc', 'the k pop', '1thek', 'studio choom', 'dingo music', 'dingo', 'arirang', 'hybe labels',
    'smtown', 'jyp entertainment', 'yg entertainment', 'starship', 'pledis', 'belift lab', 'ador', 'source music',
    'kq entertainment', 'the black label', 'genie music', 'stone music', 'kocowa', 'show champion', 'music bank',
    'inkigayo', 'm countdown', 'the show', 'ubc', 'mbc entertainment', 'kbs entertain', 'sbs entertainment',
]
FANCAM_WORDS = ['fancam', 'fan cam', 'fancams', '직캠']
EDIT_GOOD_WORDS = ['4k', '8k', '1080p', '1440p', '2160p', '60fps', 'twixtor', 'raw', 'raw scenes', 'clean',
                   'no text', 'textless', 'creditless', 'ncop', 'nced', 'scenes', 'scene pack', 'for edits',
                   'stage', 'performance', 'dance practice', 'choreography', 'focus', 'stage mix', 'one take',
                   'k choreo', '교차편집', '무대', 'ノンクレジット', '4k60']
OP_WORDS = ['opening', 'op', 'ncop', 'creditless', 'ending', 'ed', 'nced', 'ノンクレジット', 'オープニング',
            'エンディング', 'theme song', 'full song']

FPS = 10            # analysis frame rate
SAMPLE = 3          # text / face checks on every 3rd analysis frame
NUDE_EVERY = 2      # nudity check on every 2nd sample (~1.7 per second)
GRID = 24           # coverage grid for the text mask
AN = 320            # analysis frame long side
MIN_SEG, TARGET_SEG, MAX_SEG = 4.0, 5.0, 6.0
EDIT_MIN, EDIT_TARGET, EDIT_MAX = 3.0, 5.0, 8.0     # edit shots (aligned ones: the song window)
WHOLE_MAX = 240     # download whole videos up to this long, else only a section
ANALYZE_MAX = 90    # seconds analysed per video, centred on the heatmap peak
EDIT_MAX_DUR = 1200     # edit material may be a long scene pack (only a section is fetched)
ALIGN_MAX_DUR = 600     # audio fetched for alignment only up to this long
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
# frame thresholds, and how many hit frames drop a whole source
NUDE_T = {'exposed': 0.45, 'g_cov': 0.45, 'butt_cov': 0.75, 'breast_cov': 9.0, 'combo': (0.5, 0.45), 'drop': 3}
NUDE_STRICT = {'exposed': 0.35, 'g_cov': 0.4, 'butt_cov': 0.4, 'breast_cov': 0.4, 'combo': (0.35, 0.35), 'drop': 2}

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


def search(yt, query, theme, vibe, n=25, sp=SP_SHORT):
    url = ('https://www.youtube.com/results?search_query=' + urllib.parse.quote_plus(query)
           + '&sp=' + sp)
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


# ---------------------------------------------------------------- source mode

def _snorm(s):
    """Name matching form: NFKC, accents off Latin letters (ROSÉ -> rose), lower case,
    apostrophes dropped, other punctuation to single spaces (【推しの子】 -> 推しの子)."""
    out = []
    for ch in unicodedata.normalize('NFKD', unicodedata.normalize('NFKC', s or '')):
        if unicodedata.combining(ch) and out and out[-1].isascii():
            continue
        out.append(ch)
    s = re.sub(r"['’`]", '', unicodedata.normalize('NFC', ''.join(out)).lower())
    return ' '.join(re.sub(r'[\W_]+', ' ', s).split())


def _split_digits(s):
    return re.sub(r'(?<=[a-z])(?=[0-9])|(?<=[0-9])(?=[a-z])', ' ', s)


def _script(s):
    if KANA_RE.search(s):
        return 'ja'
    if HANGUL_RE.search(s):
        return 'ko'
    return 'zh' if HAN_RE.search(s) else None


FANCAM_RE = _word_re(FANCAM_WORDS)
SOURCE_DARK_RE = _word_re(SOURCE_DARK_WORDS)
REFUSED_SOURCE_RE = _word_re([w for w in UNSAFE_SHOWS if w not in MAINSTREAM_RISKY])
RISKY_SOURCE_RE = _word_re(MAINSTREAM_RISKY + RISKY_SOURCES)
EDIT_GOOD_RE = _word_re(EDIT_GOOD_WORDS)
OP_RE = _word_re(OP_WORDS)


class Source:
    """--source: the anime or K-pop group every clip must show."""

    def __init__(self, name, kind, aliases=(), song=''):
        self.name, self.kind, self.song = name.strip(), kind, (song or '').strip()
        raw = [self.name] + [a.strip() for a in aliases if a and a.strip()]
        self.strong, self.weak, seen = [], [], set()
        for r in raw:
            k = _snorm(r)
            if k and k not in seen:
                seen.add(k)
                (self.weak if self._is_weak(k) else self.strong).append(k)
        if not self.strong:             # "PSY" alone: the name itself has to do
            self.strong = [_snorm(self.name)]
            self.weak = [w for w in self.weak if w not in self.strong]
        self._strong = [self._pattern(k, True) for k in self.strong]
        self._weak = [self._pattern(k, False) for k in self.weak]
        names = ' '.join(self.strong + self.weak)

        def without_names(words):
            return _word_re([w for w in words if not _word_re([w]).search(names)])
        self.not_re = without_names(NOT_SOURCE_WORDS + NOT_SOURCE_KIND[kind])
        self.song_pat = self._pattern(_snorm(self.song), True) if _snorm(self.song) else None
        self.native = [r for r in raw if not r.isascii() and _script(r)]
        self.official_set = [_snorm(c) for c in OFFICIAL_CHANNELS]
        # the source's own names are exempt from the unsafe-show title check (other shows are not)
        flat = [re.sub(r'[^a-z0-9+#]+', ' ', k).strip() for k in self.strong + self.weak]
        flat = [k for k in flat if k]
        self._own = [re.compile(r'(?<![a-z0-9])' + r'\s*'.join(map(re.escape, k.split())) + r'(?![a-z0-9])')
                     for k in flat]
        both = flat + [_split_digits(k) for k in flat]      # "nogizaka46" -> also "nogizaka 46"
        self.refused = any(REFUSED_SOURCE_RE.search(k) or NSFW_RE.search(k) for k in both) or any(
            c in k for k in self.strong + self.weak for c in NSFW_CHARS)
        self.risky = not self.refused and any(SHOW_RE.search(k) or RISKY_SOURCE_RE.search(k) for k in both)
        self.risky_re = without_names(RISKY_TITLE_WORDS)

    @staticmethod
    def _is_weak(k):
        if not k.isascii():
            return len(k.replace(' ', '')) < 2
        return ' ' not in k and (len(k) <= 3 or k in COMMON_NAMES)

    @staticmethod
    def _pattern(k, compact):
        """Matcher for one normalised name: ASCII names as whole words (spaces optional, so
        "new jeans" == "newjeans"), long ones also inside hashtags; others as substrings."""
        c = k.replace(' ', '')
        if not k.isascii():
            return lambda text, ctext: c in ctext
        rx = re.compile(r'(?<![a-z0-9])' + r'\s*'.join(map(re.escape, k.split())) + r'(?![a-z0-9])')
        return lambda text, ctext: bool(rx.search(text) or (compact and len(c) >= 6 and c in ctext))

    @staticmethod
    def _any(pats, text):
        ctext = text.replace(' ', '')
        return any(p(text, ctext) for p in pats)

    def level(self, meta):
        """2: the title names the source; 1: an official / source-named channel, or a short or
        common alias in the title backed by the tags, channel or description; 0: not the source."""
        title = _snorm(meta.get('title'))
        if self._any(self._strong, title):
            return 2
        chan = _snorm(meta.get('channel') or meta.get('uploader') or '')
        if chan and (self._any(self._strong, chan) or chan in self.weak):
            return 1
        if self._weak and self._any(self._weak, title):
            rest = _snorm(' '.join([str(t) for t in meta.get('tags') or []] + [meta.get('description') or '']))
            if self._any(self._strong, rest):
                return 1
        return 0

    def weak_title(self, meta):
        return bool(self._weak) and self._any(self._weak, _snorm(meta.get('title')))

    def has_song(self, meta):
        return bool(self.song_pat) and self._any([self.song_pat], _snorm(meta.get('title')))

    def official(self, meta):
        chan = _snorm(meta.get('channel') or meta.get('uploader') or '')
        return bool(chan) and (any(chan == o or chan.startswith(o + ' ') for o in self.official_set)
                               or self._any(self._strong, chan))

    def reason(self, meta, vibe):
        """Reject reason for a candidate's text in source mode, or None. Safety checks as in
        theme mode (fancams allowed from official channels; the source's own names exempt from
        the unsafe-show list); off-type / not-the-source words and violence words are matched
        on the title (and tags), where they describe the video."""
        official = self.official(meta)
        title = meta.get('title') or ''
        t = _text(title, meta.get('description'), meta.get('tags'), meta.get('categories'))
        head = _text(title, meta.get('tags'))
        if official:
            t, head = FANCAM_RE.sub(' ', t), FANCAM_RE.sub(' ', head)
            t, head = t.replace('직캠', ' '), head.replace('직캠', ' ')
        flat = re.sub(r'[^a-z0-9+#]+', ' ', t)
        other = flat
        for rx in self._own:
            other = rx.sub(' ', other)
        if (NSFW_RE.search(t) or any(c in t for c in NSFW_CHARS) or SHOW_RE.search(other)
                or SHOW_RE.search(_split_digits(other)) or RISKY_RE.search(flat)):
            return 'nsfw'
        if channel_nsfw(meta):
            return 'nsfw'
        if self.risky and self.risky_re.search(_norm(title)):
            return 'nsfw'
        dance_text = t if vibe == 'dance' else head
        if (vibe == 'dance' or self.kind == 'group') and VIBE_RISK_RE['dance'].search(
                re.sub(r'stray[\s_\-]*kids', 'skz', dance_text)):
            return 'nsfw'
        if vibe != 'cute' and SCHOOL_KID_RE.search(t):
            return 'kids'
        tt = _norm(title)
        if DARK_RE.search(t) or VIBE_RISK_RE['fight'].search(head) or SOURCE_DARK_RE.search(tt):
            return 'dark'
        if OFFTYPE_RE.search(tt):
            return 'offtype'
        if self.not_re.search(tt):
            return 'notsource'
        # "Naruto x Chainsaw Man OP", 【ちいかわ×チェンソーマン】: another show in it ("Spy x Family"
        # itself is fine: the source's own names are taken out first)
        rest = _snorm(title.replace('×', ' x ').replace('✕', ' x '))
        for k in sorted(self.strong + self.weak, key=len, reverse=True):
            if k.isascii():
                rest = re.sub(r'(?<![a-z0-9])' + r'\s*'.join(map(re.escape, k.split())) + r'(?![a-z0-9])',
                              ' qqownqq ', rest)
            else:
                rest = rest.replace(k, ' qqownqq ')
        for a, b in re.findall(r'(\S+)\s+x\s+(\S+)', rest):     # "Denji x Power" pairs its own names
            if self.kind == 'anime' and 'qqownqq' in (a, b) and (a, b) != ('qqownqq', 'qqownqq'):
                return 'notsource'
        return None


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


# burned-in subtitles / lyrics: the full-screen edit crops them mid-word (text detection misses small subs)
SUBS_RE = re.compile(r'(sub|subs|subbed|subtitles?|eng ?sub|english sub|lyrics?|romaji|karaoke|cc)|字幕|歌詞|자막|가사', re.I)


def prefilter(e, used, block, min_views, max_dur, src=None):
    if e['id'] in used:
        return 'used'
    if SUBS_RE.search(e.get('title') or ''):  # every clip (tiles too): the owner wants no subtitles, ever
        return 'subtitles'
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
    if src:
        why = src.reason(e, e['vibe'])
        if why:
            return why
        # a common alias alone may still be backed by the tags: probe decides
        return None if src.level(e) or src.weak_title(e) else 'offsource'
    return text_reason(e['title'], e['description'], vibe=e['vibe'])


def probe(yt, e, used, block, max_dur, min_views, src=None):
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
    if min(info.get('width') or 999, info.get('height') or 999) < (360 if e['vibe'] == 'edit' else 320):
        return None, 'lowres'
    if offtheme(info.get('title'), info.get('tags')):
        return None, 'offtheme'
    if src:
        why = src.reason(info, e['vibe'])
        if why:
            return None, why
        lvl = src.level(info)
        if not lvl:
            log(f'not {src.name}, skipped {info["id"]}: {(info.get("title") or "")[:60]!r}')
            return None, 'offsource'
        info['_level'] = lvl
        return info, None
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


def download(yt, info, src_dir, timeout, section=None, tag=''):
    """(path, offset): offset is where the file starts in the source video. section=(a, b)
    fetches just that part; tag keeps several files of one video apart."""
    vid = info['id']
    name = f'{vid}__{tag}' if tag else vid
    ij = os.path.join(src_dir, f'{vid}.info.json')
    with open(ij, 'w', encoding='utf-8') as f:
        json.dump({k: v for k, v in info.items() if not k.startswith('_')}, f)
    args = ['-f', 'bv*+ba/b', '-S', 'res:720,vcodec:h264,acodec:aac', '--merge-output-format', 'mkv',
            '--no-part', '-o', os.path.join(src_dir, f'{name}.%(ext)s')]
    offset = 0.0
    if section:
        offset, b = section
        args += ['--download-sections', f'*{offset:.2f}-{b:.2f}']
    elif (info.get('duration') or 0) > WHOLE_MAX:
        # ffmpeg-based section download: slow on some videos, so only for long ones
        offset, b = window(info)
        args += ['--download-sections', f'*{offset:.1f}-{b:.1f}']
    end = time.time() + timeout
    err = ''
    outs = [os.path.join(src_dir, f'{name}.{ext}') for ext in ('mkv', 'mp4', 'webm')]
    for src in (['--load-info-json', ij], [f'https://www.youtube.com/watch?v={vid}']):
        rc, _, err = yt(src + args, end - time.time())
        files = [p for p in outs if os.path.exists(p) and os.path.getsize(p) > 50000]
        if rc == 0 and files:
            return files[0], offset
        for p in glob.glob(os.path.join(src_dir, f'{name}.*')):
            if not p.endswith('.info.json'):
                os.remove(p)
        if rc is None or end - time.time() < 20:
            break
    log(f'download failed {vid}: {err_line(err)}')
    return None, 0.0


def download_audio(yt, info, src_dir, timeout):
    """A small audio-only copy of the whole video (for alignment), or None."""
    vid = info['id']
    ij = os.path.join(src_dir, f'{vid}.info.json')
    with open(ij, 'w', encoding='utf-8') as f:
        json.dump({k: v for k, v in info.items() if not k.startswith('_')}, f)
    out = os.path.join(src_dir, f'{vid}__aud.%(ext)s')
    end = time.time() + timeout
    err = ''
    for src in (['--load-info-json', ij], [f'https://www.youtube.com/watch?v={vid}']):
        rc, _, err = yt(src + ['-f', 'ba[abr<=80]/wa/ba/b', '--no-part', '-o', out], end - time.time())
        files = [p for p in glob.glob(os.path.join(src_dir, f'{vid}__aud.*')) if os.path.getsize(p) > 20000]
        if rc == 0 and files:
            return files[0]
        for p in glob.glob(os.path.join(src_dir, f'{vid}__aud.*')):
            os.remove(p)
        if rc is None or end - time.time() < 10:
            break
    log(f'audio download failed {vid}: {err_line(err)}')
    return None


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


def title_bonus(meta, vibe, soft=False):
    t = _text(meta.get('title'))
    b = 0.8 if VIBE_RE[vibe].search(t) else (-(0.3 if soft else 1.0) if vibe in VIBE_REQUIRED else 0.0)
    return b - (0.4 if GENERIC_RE.search(t) else 0.0)


def edit_bonus(meta, src):
    """Edit material: clean 4K / raw / creditless / stage words, the song, resolution."""
    t = _text(meta.get('title'))
    b = 0.8 * bool(EDIT_GOOD_RE.search(t)) - 0.5 * bool(GENERIC_RE.search(t))
    if src and src.has_song(meta):
        b += 1.2
    if min(meta.get('width') or 0, meta.get('height') or 0) >= 1080:
        b += 0.4
    return b


def pre_rank(entries, rng, src=None):
    """Order search results before probing: views, shortness, vibe words, Asia signal
    (source mode: how clearly the title names the source)."""
    def key(e):
        if e['vibe'] == 'edit':
            sc = 0.5 * math.log10((e['views'] or 100000) + 1) + edit_bonus(e, src)
        else:
            sc = (math.log10((e['views'] or 100000) + 1) + length_bonus(e['duration'])
                  + title_bonus(e, e['vibe'], soft=src is not None))
        if src:
            sc -= 0.7 * (src.level(e) < 2)
        elif not relevant(e, e['theme'], e['query']):
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


def source_rank(info, rng, src=None):
    """Order probed sources for download: views, view velocity, Shorts, vertical, vibe words."""
    if info['_vibe'] == 'edit':
        sc = 0.5 * math.log10((info.get('view_count') or 0) + 1) + edit_bonus(info, src)
        return sc + 0.5 * (info.get('_level') == 2) + rng.uniform(0, 0.3)
    sc = math.log10((info.get('view_count') or 0) + 1) + 0.6 * math.log10(velocity(info) + 1)
    sc += length_bonus(info.get('duration')) + title_bonus(info, info['_vibe'], soft=src is not None)
    if (info.get('height') or 0) > (info.get('width') or 0):
        sc += 0.5
    if src:
        sc += 0.5 * (info.get('_level') == 2)
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


def held_frames(path, ss, span, each=2.0):
    """Per short stretch of the span (up to 6, apart): the share of consecutive frames (native
    rate) that repeat the previous one: identical at 160x90, or, at 32x18 where a film-grain
    overlay (Chainsaw Man) averages out, far smaller than the stretch's typical change. Anime
    holds each drawing for 2-3 frames (still so in 60 fps interpolated uploads): ~0.15-0.9,
    pans animated on ones aside. A camera almost never repeats a frame: ~0."""
    w, h = 160, 90
    out_ratios = []
    chunks = int(min(6, max(1, span // (2 * each))))
    for k in range(chunks):
        t = max(0.0, ss + (k + 0.5) * span / chunks - each / 2)
        _, out, _ = run(['ffmpeg', '-v', 'error', '-ss', f'{t:.2f}', '-t', f'{each:.2f}', '-i', path, '-map', '0:v:0',
                         '-vf', f'scale={w}:{h}:flags=area', '-fps_mode', 'passthrough', '-f', 'rawvideo',
                         '-pix_fmt', 'gray', '-'], 30)
        f = np.frombuffer(out[:len(out) // (w * h) * (w * h)], np.uint8).reshape(-1, h, w).astype(np.float32)
        if len(f) >= 20:
            d = np.abs(np.diff(f, axis=0)).mean((1, 2))
            c = f.reshape(len(f), h // 5, 5, w // 5, 5).mean((2, 4))
            dc = np.abs(np.diff(c, axis=0)).mean((1, 2))
            out_ratios.append(float(max((d < 0.3).mean(), (dc < 0.15 * np.percentile(dc, 90)).mean())))
    return out_ratios


def live_action(path, ss, span, n=10):
    """For an anime source: True when the file looks like live action (a dance-practice video,
    a school sports clip under the anime's song, a cosplayer): half of the sampled stretches
    never repeat a frame (a camera; a CG show animated on ones is sacrificed), or, when the
    footage is not clearly animated, several real faces in a quarter of the sampled frames
    (YuNet finds at most one face in most anime frames)."""
    held = held_frames(path, ss, span)
    if held and sum(r < 0.06 for r in held) >= max(min(2, len(held)), len(held) / 2):
        return True
    if (held and np.median(held) >= 0.12) or ('face' not in PATHS and not HAAR):
        return False
    size = 640 * 360 * 3
    vf = ['-map', '0:v:0', '-vf', 'scale=640:360:force_original_aspect_ratio=decrease,pad=640:360:(ow-iw)/2:(oh-ih)/2',
          '-f', 'rawvideo', '-pix_fmt', 'bgr24']

    def grab(args, timeout):
        _, out, _ = run(['ffmpeg', '-v', 'error'] + args + vf + ['-'], timeout)
        return [np.frombuffer(out[i:i + size], np.uint8).reshape(360, 640, 3) for i in range(0, len(out) - size + 1, size)]

    # keyframes only: cheap to decode, and YouTube files have one every 1-4 s
    frames = grab(['-skip_frame', 'nokey', '-ss', f'{ss:.2f}', '-t', f'{span:.2f}', '-i', path, '-fps_mode', 'passthrough'], 60)
    if len(frames) < 6:
        frames = [f for k in range(n) for f in grab(['-ss', f'{ss + (k + 0.5) * span / n:.2f}', '-i', path,
                                                      '-frames:v', '1'], 20)]
    if len(frames) < 3:
        return False
    frames = frames[::max(1, len(frames) // n)][:n]
    counts = np.array([sum(1 for f in faces(img) if f[4] >= 0.8) for img in frames])
    return bool((counts >= 2).mean() >= 0.25)


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
    # tuned to over-reject exposure: a bruised knee still scores ~0.7 "exposed breast".
    # A covered chest on its own is just a fitted outfit (idol stage dresses scored
    # >= 0.7 and cost a quarter of K-pop sources); with a bare midriff it is
    # swimwear-like and still rejected. clip_judge.py re-checks for suggestive frames.
    # A fan-service-risk --source uses the stricter NUDE_STRICT.
    t = NUDE_T
    return bool(sc[NUDE_EXPOSED].max() >= t['exposed'] or sc[NUDE_G_COV] >= t['g_cov']
                or sc[NUDE_BUTT_COV] >= t['butt_cov'] or sc[NUDE_BREAST_COV] >= t['breast_cov']
                or (sc[NUDE_BREAST_COV] >= t['combo'][0] and sc[NUDE_BELLY] >= t['combo'][1]))


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


def _decode(path, info, offset, ss, span, min_n):
    """Frames of the file from ss (file seconds) for span seconds at FPS with the per-frame
    measures every clip kind uses; None when unreadable, under min_n frames or nude."""
    mi = media_info(path)
    if not mi:
        return None
    W, H, fdur, has_audio = mi
    span = min(fdur - ss, span) if fdur else span
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
    while i < FPS * (max(ANALYZE_MAX, span) + 5):
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
    if n < min_n:
        return None
    s_nude = np.array([s['nude'] for s in samples])
    if s_nude.sum() >= NUDE_T['drop']:
        log(f'{info["id"]}: nudity check hit on {int(s_nude.sum())} frames, dropping source')
        _bump('rejected', 'nsfw_frames')
        return None

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
    c = {
        'n': n, 'crop': crop, 'mot': mot, 'spike': spike, 'loud': loud, 'heat': heat, 'cuts': cuts,
        'hit': hit, 'smooth': smooth,
        'luma': crop.mean((1, 2)), 'detail': crop.std((1, 2)),
        'black': (crop < 20).mean((1, 2)),                                # small picture on a black canvas
        'uniform': (np.abs(flat - modes[:, None]) <= 8).mean(1),          # plain text / logo cards
        'text': np.array([float(s['text'][gy0:gy1, gx0:gx1].mean()) for s in samples]),
        'tgrid': np.stack([s['text'][gy0:gy1, gx0:gx1] for s in samples]),
        'face': s_face, 'nude': s_nude, 'sharp': np.array([s['sharp'] for s in samples]),
        'lowres': min(W, H) < 480, 'vertical': H > W,
        # not per-frame
        'W': W, 'H': H, 'has_audio': has_audio, 'ss': ss, 'span': span, 't': t, 'vdur': vdur,
        'at_start': at_start, 'at_end': at_end, 'box': (x0, y0, x1, y1), 'cbox': (cy0, cy1, cx0, cx1),
        'thumbs': thumbs,
    }
    return c


def _segment(c, info, path, theme, vibe, s, e, sc, extra, pop):
    """Manifest entry (plus private _keys) for frames s..e of a decoded file."""
    W, H, t, (cy0, cy1, cx0, cx1) = c['W'], c['H'], c['t'], c['cbox']
    return {
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
        'content_box': [round(v, 3) for v in c['box']],
        '_src': path, '_local': c['ss'] + s / FPS, '_dur': (e - s) / FPS, '_s': s, '_e': e,
        '_hash': dhash(c['thumbs'][(s + e) // 2][cy0:cy1, cx0:cx1]),
    }


def analyze(path, info, offset, theme, vibe):
    """Up to 3 non-overlapping 4-6 s moments of one video for a vibe, best first."""
    a, b = window(info)
    c = _decode(path, info, offset, max(0.0, a - offset), b - a, FPS * MIN_SEG)
    if not c:
        return []
    n, cuts, spike, heat, hit, smooth, loud = c['n'], c['cuts'], c['spike'], c['heat'], c['hit'], c['smooth'], c['loud']
    if vibe == 'dance':     # sustained movement, then the sharpest move
        ev = np.minimum(1, smooth / 0.05) + 0.5 * np.minimum(1, spike / 0.05) + 0.5 * heat
    elif vibe == 'fight':
        ev = hit + heat
    else:
        ev = np.minimum(1, spike / 0.06) + np.minimum(1, loud / 9) + 1.2 * heat
    c['ev'], c['vibe'] = np.convolve(ev, np.ones(5) / 5, mode='same'), vibe
    short = c['vdur'] <= 60
    lo = 1 if short or not c['at_start'] else 5
    hi = n - ((3 if short else 25) if c['at_end'] else 1)   # Shorts loop, long videos end on cards
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
        segs.append(_segment(c, info, path, theme, vibe, s, e, sc, extra, pop))
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


def _build(p, lo, hi, cuts, target=TARGET_SEG, shortest=MIN_SEG):
    """(start, end) frames of a 4-6 s moment around peak p: inside its shot when the shot is long
    enough, else across shots starting and ending on cuts where possible."""
    L, lmin = int(target * FPS), int(shortest * FPS)
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


# ---------------------------------------------------------------- edit material

def analyze_edit(path, info, offset, ss, span, theme, force=False):
    """Shots for the full-song edit from one file section, best first: up to 2 non-overlapping
    3-8 s shots around the strongest motion, re-centred on a strong sound hit next to it when
    there is one. force: the whole section is the one shot (an aligned song window)."""
    c = _decode(path, info, offset, ss, span, int(FPS * EDIT_MIN))
    if not c:
        return []
    n = c['n']
    fx = None
    if c['has_audio']:
        y = align.decode(path, c['ss'], c['span'], runner=run, sr=align.SR_FX, pad=True)
        fx = align.sfx_frames(y) if y is not None else None
    snd = np.zeros(n)
    for t, st in (align.sfx_hits(fx, 0, n / FPS, max_hits=40) if fx and not force else []):
        k = int(round(t * FPS))
        if 0 <= k < n:
            snd[k] = max(snd[k], st)
    ev = (np.minimum(1, c['smooth'] / 0.05) + 0.6 * np.minimum(1, c['spike'] / 0.05) + 0.5 * c['heat']
          + 0.8 * np.convolve(snd, np.ones(3), mode='same'))
    c['ev'], c['vibe'] = np.convolve(ev, np.ones(5) / 5, mode='same'), 'edit'
    if force:
        wins = [(0, n)]
    else:
        short = c['vdur'] <= 60
        lo = 1 if short or not c['at_start'] else 5
        hi = n - ((3 if short else 25) if c['at_end'] else 1)
        if hi - lo < EDIT_MIN * FPS:
            _bump('seg_rejected', 'short')
            return []
        peaks = []
        for p in _peaks(c['ev'], lo, hi, k=6, gap=30):
            near = [k for k in range(max(lo, p - 6), min(hi, p + 7)) if snd[k] >= 0.5]
            peaks.append(max(near, key=lambda k: snd[k]) if near else p)
        wins = [w for w in (_build(p, lo, hi, c['cuts'], EDIT_TARGET, EDIT_MIN) for p in peaks) if w]
    scored, whys = [], Counter()
    for s, e in wins:
        sc, why, extra = _score_edit(c, s, e, fx, force)
        if why:
            whys[why] += 1
        else:
            scored.append((sc, s, e, extra))
    if not scored:
        if whys:
            _bump('seg_rejected', 'edit_' + whys.most_common(1)[0][0])
        return []
    scored.sort(key=lambda r: -r[0])
    pop = popularity(info)
    segs = []
    for sc, s, e, extra in scored:
        if any(s < o['_e'] and o['_s'] < e for o in segs):
            continue
        seg = _segment(c, info, path, theme, 'edit', s, e, sc, extra, pop)
        seg.update({k: extra[k] for k in ('has_audio', 'sfx_hits', 'sfx_score', 'watermark')})
        seg['score'] = round(float(0.8 * sc + 0.2 * pop), 4)
        segs.append(seg)
        if len(segs) == (1 if force else 2):
            break
    _bump('analyzed')
    return segs


def _score_edit(c, s, e, fx, forced):
    """(score, reject_reason, extras) of frames s..e as edit material: striking motion, sharp,
    clean (no big text, no watermark), few cuts; plus its sound hits."""
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
    if (m < 0.003 if forced else (m < 0.006 and c['spike'][s:e].max() < 0.015)):
        return 0, 'static', None
    if (c['crop'][s:e].std(0) < 2.5).mean() > 0.6:
        return 0, 'still', None
    if text.mean() > (0.45 if forced else 0.3):
        return 0, 'text', None
    if sharp < 40:
        return 0, 'blurry', None
    dur = (e - s) / FPS
    n_cuts = sum(1 for k in c['cuts'] if s < k < e)
    if not forced and n_cuts / dur > 2.5:
        return 0, 'strobe', None        # flashing / rapid-fire cuts: unusable and hard on the eyes
    hit = c['hit'][s:e]
    hits = _local_peaks(hit, max(0.5, 0.4 * hit.max()))
    p = int(np.argmax(hit)) / FPS
    t0, t1 = s / FPS, e / FPS
    fh = align.sfx_hits(fx, t0, t1) if fx else []
    fs = align.sfx_score(fx, t0, t1, fh) if fx else 0.0
    sfx = [round(t - t0, 2) for t, _ in fh]
    if sfx and (abs(sfx[0] - p) <= 0.5 or fh[0][1] >= 0.6):
        p = sfx[0]                      # the sound hit is the moment to land on the beat
    nm = min(1, m / 0.05)
    steady = float((c['mot'][s + 1:e] > 0.012).mean())
    hj = _samples(s, s + 6, len(c['face']))
    hook = (0.4 * min(1, c['mot'][s + 1:s + 5].mean() / 0.04)
            + 0.35 * min(1, c['face'][hj].max() / 0.03)
            + 0.25 * min(1, c['detail'][s:s + 5].mean() / 60))
    persist = (c['tgrid'][js] > 0.3).mean(0)
    watermark = bool((persist >= 0.8).any())
    has_caption = bool(np.median(text) >= 0.02)
    sc = (0.3 * nm + 0.15 * steady + 0.15 * min(1, hit.max()) + 0.1 * min(1, len(hits) / 4) + 0.1 * hook
          + 0.1 * min(1, dt / 60) + 0.1 * fs)
    if not forced and n_cuts / dur > 0.8:
        sc *= 0.7                       # already someone else's fast edit
    if sharp < 150:
        sc *= 0.8
    if c['lowres']:
        sc *= 0.8
    if has_caption:
        sc *= 0.75
    if watermark:
        sc *= 0.9
    if lm < 45:
        sc *= 0.85
    loop = 1 - min(1, float(np.abs(c['crop'][s] - c['crop'][e - 1]).mean()) / 40)
    return sc, None, {
        'hook': hook, 'peak': round(p, 2), 'hits': [round(h / FPS, 2) for h in hits][:12],
        'loop': round(loop, 3), 'cuts': n_cuts, 'has_caption': has_caption,
        # uncapped here (1.0 = strong motion) so the editor can order shots by it
        'has_face': bool((c['face'][js] >= 0.004).mean() >= 0.5), 'motion': round(float(m / 0.05), 3),
        'has_audio': bool(c['has_audio']), 'sfx_hits': sfx, 'sfx_score': round(float(fs), 3),
        'watermark': watermark}


# ---------------------------------------------------------------- output

TITLE_JUNK_RE = re.compile(_chars((0x30, 0x39), (0x61, 0x7A), KANA, HAN, HANGUL, neg=True) + '+')


def _title_key(s):
    return TITLE_JUNK_RE.sub('', _norm(s['source_title']))[:30]


def _series(title):
    """"Chibi Titans 2" and "Chibi Titans 4" are one series: treat them like one channel."""
    return ' '.join(_norm(title).split()[:2])


def spread_channels(infos, series=True):
    """Keep rank order but put each channel's / series' first video ahead of its repeats
    (series=False in source mode, where every title starts with the source's name)."""
    seen, first, rest = set(), [], []
    for i in infos:
        keys = {i.get('channel_id') or i.get('channel') or i['id']}
        if series:
            keys.add(_series(i.get('title')))
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


# Source mode: which other vibes stand in best for a vibe the source cannot fill.
FALLBACK_W = {'dance': {'fight': 0.9, 'cool': 0.8, 'cute': 0.7}, 'cool': {'fight': 0.9, 'dance': 0.85, 'cute': 0.7},
              'fight': {'dance': 0.95, 'cool': 0.9, 'cute': 0.6}, 'cute': {'cool': 0.8, 'dance': 0.8, 'fight': 0.6}}


def select_source(segs, wanted):
    """select() for source mode: every clip from a different video, other channels first. A vibe
    the source could not fill takes the best spare moment of another vibe, relabelled and
    tagged vibe_fallback (found_as: its own vibe); each round fills every vibe's own pool first."""
    pools = {v: sorted([s for s in segs if s['vibe'] == v], key=lambda s: -s['score']) for v in wanted}
    chosen = {v: [] for v in wanted}
    picked = []

    def ok(s, same_channel_ok):
        chan = s['source_channel_id'] or s['source_channel']
        for c in picked:
            if c['source_id'] == s['source_id']:
                return False
            if not same_channel_ok and chan and (c['source_channel_id'] or c['source_channel']) == chan:
                return False
            if bin(c['_hash'] ^ s['_hash']).count('1') <= 10 or (_title_key(c) and _title_key(c) == _title_key(s)):
                return False
        return True

    def first(cands):
        for same_channel_ok in (False, True):
            s = next((s for s in cands if ok(s, same_channel_ok)), None)
            if s:
                return s
        return None

    for rnd in range(max(wanted.values(), default=0)):
        short = []
        for v, k in wanted.items():
            if len(chosen[v]) > rnd or len(chosen[v]) >= k:
                continue
            s = first(pools[v])
            if s:
                chosen[v].append(dict(s, vibe_fallback=False))
                picked.append(s)
            else:
                short.append(v)
        for v in short:
            spare = sorted([s for s in segs if s['vibe'] != v],
                           key=lambda s: -s['score'] * FALLBACK_W[v].get(s['vibe'], 0.5))
            s = first(spare)
            if s:
                chosen[v].append(dict(s, vibe=v, vibe_fallback=True, found_as=s['vibe']))
                picked.append(s)
    return [s for v in wanted for s in chosen[v]]


def select_edit(shots, n, tiles):
    """Up to n edit shots, aligned ones first, then by score. Other videos than the tiles where
    possible; at most 2 shots per video (one aligned), never overlapping each other or a tile
    clip of the same video, no near-duplicate frames."""
    tile_ids = {c['source_id'] for c in tiles}
    order = sorted(shots, key=lambda s: (not s.get('aligned'), -(s.get('align_score', 0) + s['score'])))
    picked = []

    def overlaps(a, b):
        return a['source_id'] == b['source_id'] and a['start'] < b['end'] and b['start'] < a['end']

    for allow_tile_videos in (False, True):
        for s in order:
            if len(picked) >= n:
                break
            if s in picked or (not allow_tile_videos and s['source_id'] in tile_ids):
                continue
            same = [p for p in picked if p['source_id'] == s['source_id']]
            if len(same) >= 2 or (s.get('aligned') and any(p.get('aligned') for p in same)):
                continue
            if any(overlaps(s, p) for p in picked) or any(overlaps(s, c) for c in tiles):
                continue
            if any(bin(p['_hash'] ^ s['_hash']).count('1') <= 8 for p in picked):
                continue
            picked.append(s)
    return picked


def cut(seg, out_path, audio=False):
    """Clip file for a segment: h264, no audio (audio=True keeps the first audio track as AAC)."""
    amap = ['-map', '0:a:0?', '-c:a', 'aac', '-b:a', '160k', '-ac', '2', '-ar', '44100'] if audio else ['-an']
    rc, _, _ = run(['ffmpeg', '-v', 'error', '-y', '-ss', f'{seg["_local"]:.3f}', '-i', seg['_src'],
                    '-t', f'{seg["_dur"]:.3f}', '-map', '0:v:0'] + amap + ['-sn', '-dn',
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
        if c['vibe'] == 'edit':
            label = (f'{k} edit {c["duration"]:.1f}s pk{c["peak"]:.1f} m{c["motion"]:.2f} '
                     + (f'AL{c["align_score"]:.2f}' if c.get('aligned') else f'sfx{c["sfx_score"]:.2f}'))
            sub = f'{c["source_id"]} {len(c["sfx_hits"])}fx' + (' wm' if c.get('watermark') else '')
        else:
            label = (f'{k} {c["vibe"]} {c["duration"]:.1f}s pk{c["peak"]:.1f} {len(c["hits"])}hits '
                     f'h{c["hook"]:.2f} s{c["score"]:.2f}')
            sub = f'{c["source_id"]} {c["theme"]}' + (f' fb:{c["found_as"]}' if c.get('vibe_fallback') else '')
        color = (120, 255, 160) if c.get('aligned') else (230, 230, 230)
        cv2.putText(sheet, label, (ox + 4, oy + cell + 14), cv2.FONT_HERSHEY_SIMPLEX, 0.37, color, 1, cv2.LINE_AA)
        cv2.putText(sheet, sub, (ox + 4, oy + cell + 29), cv2.FONT_HERSHEY_SIMPLEX, 0.37,
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


def pick_source_queries(src, vibes, n_edit, theme, rng):
    """(query, theme, vibe, search filter): 3 per tile vibe plus one native-script query when
    there is a native alias; 5 (+1 native) for the edit material. Queries naming the song
    first; a fan-service-risk source searches "funny" rather than "cute" moments."""
    table = SOURCE_QUERIES[src.kind]
    native = src.native[0] if src.native else None
    script = _script(native) if native else None
    out = []
    for v in list(vibes) + (['edit'] if n_edit else []):
        pool = [q for q in table[v] if src.song or '{song}' not in q]
        if v == 'cute' and src.risky:
            pool = ['{s} funny moments', '{s} funny scene', '{s} comedy scene']
        k = 5 if v == 'edit' else 3
        pick = [q for q in pool if '{song}' in q][:k if v == 'edit' else 1]
        rest = [q for q in pool if q not in pick]
        pick += [pool[0]] if pool[0] not in pick and len(pick) < k else []     # the plainest query always
        rest = [q for q in rest if q not in pick]
        pick += rng.sample(rest, max(0, min(k - len(pick), len(rest))))
        qs = [q.format(s=src.name, song=src.song) for q in pick]
        if script and v in SOURCE_NATIVE[script]:
            nq = SOURCE_NATIVE[script][v]
            if v == 'fight' and script == 'ko' and src.kind == 'anime':
                nq = '{n} 액션'
            if v == 'cute' and src.risky:
                nq = {'ja': '{n} 面白いシーン', 'ko': '{n} 웃긴 장면', 'zh': '{n} 搞笑'}[script]
            qs.append(' '.join(nq.format(n=native, song=src.song).split()))
        out += [(q, theme, v, SP_VIDEO if v == 'edit' else SP_SHORT) for q in dict.fromkeys(qs)]
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
    ap.add_argument('--source', help='anime title or K-pop group / artist: every clip shows it')
    ap.add_argument('--source-kind', choices=['anime', 'group'], help='default: anime when the name is a known anime')
    ap.add_argument('--aliases', default='', help='comma list of other names: characters, members, native titles')
    ap.add_argument('--edit', type=int, default=0, help='also collect N shots for the full-song edit (--source)')
    ap.add_argument('--song-audio', help='the song (for aligning edit shots that play it)')
    ap.add_argument('--song-start', type=float, default=0.0, help='song seconds where the full-mix part starts')
    ap.add_argument('--song-len', type=float, default=15.0, help='seconds of song the full-mix part plays')
    ap.add_argument('--song-name', default='', help='song title, to find stages / openings of it')
    ap.add_argument('--used', help='JSON of video ids used recently')
    ap.add_argument('--used-days', type=int, default=30)
    ap.add_argument('--block', help='JSON of blocked channels')
    ap.add_argument('--cookies', help='Netscape cookies file for YouTube')
    ap.add_argument('--js-runtimes', default='auto', help='yt-dlp JS runtime: auto, deno, node, bun, none')
    ap.add_argument('--budget', type=float, help='total seconds (default 330; with --source 360, +5 per --edit shot)')
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
    src = None
    if a.source and a.source.strip():
        kind = a.source_kind or ('anime' if THEME_RE['anime'].search(_norm(a.source)) else 'group')
        src = Source(a.source, kind, a.aliases.split(','), a.song_name)
        if theme == 'mixed':
            theme = 'anime' if kind == 'anime' else 'kpop'
    n_edit = max(0, a.edit) if src else 0
    if a.edit and not src:
        log('--edit needs --source; no edit material in theme mode')
    budget = a.budget or (330 if not src else 360 + 5 * min(n_edit, 10))

    def left():
        return budget - (time.time() - T0)

    out_dir = os.path.abspath(a.out_dir)
    src_dir = os.path.join(out_dir, '_src')
    os.makedirs(src_dir, exist_ok=True)
    for old in glob.glob(os.path.join(out_dir, 'clip_*.mp4')) + glob.glob(os.path.join(out_dir, 'edit_*.mp4')):
        os.remove(old)
    manifest = os.path.join(out_dir, 'clips.json')
    if src and src.refused:
        log(f'source {src.name!r} is on the unsafe-show list; refusing it (use theme mode)')
        print(json.dumps({'ok': False, 'count': 0, 'manifest': manifest, 'vibes': {v: 0 for v in wanted},
                          'source': src.name, 'edit_count': 0, 'aligned_count': 0, 'error': 'unsafe source'}))
        sys.stdout.flush()
        os._exit(1)
    if src and src.risky:
        NUDE_T.update(NUDE_STRICT)
    rng = random.Random(a.seed if a.seed is not None else int(time.time() // 86400))
    used = _ids_from(load_json(a.used), a.used_days) if a.used else set()
    block = load_block(a.block)
    yt = YT(a.cookies, a.js_runtimes)
    if src:
        STATS.update({'align_tried': 0, 'align_found': 0, 'edit_shots': 0})
        log(f'source={src.name!r} ({src.kind}{", fan-service risk: strict nudity checks" if src.risky else ""}) '
            f'names={src.strong} weak={src.weak} edit={n_edit} vibes={wanted} used={len(used)} '
            f'blocked={len(block)} cookies={"yes" if yt.cookies else "no"} budget={budget:.0f}s')
    else:
        log(f'theme={theme} vibes={wanted} used={len(used)} blocked={len(block)} '
            f'cookies={"yes" if yt.cookies else "no"}')
    models = threading.Thread(target=load_models, daemon=True)
    models.start()
    ref = None
    if src and n_edit and a.song_audio:
        try:
            ref = align.SongRef(a.song_audio, a.song_start, a.song_len, runner=run)
            log(f'song window {ref.start:.1f}-{ref.start + ref.length:.1f}s of {ref.dur:.0f}s for alignment')
        except Exception as err:
            log(f'song audio unusable, no alignment: {err!r}')

    # 1. search every vibe at once
    if src:
        # edit queries first: a video both find (the song's stage) is worth more as edit material
        queries = sorted(pick_source_queries(src, wanted, n_edit, theme, rng), key=lambda q: q[2] != 'edit')
    else:
        queries = [(q, th, v, SP_SHORT) for q, th, v in pick_queries(theme, wanted, rng)]
    found, seen = [], set()
    with cf.ThreadPoolExecutor(6 if src else 4) as ex:
        for res in ex.map(lambda q: search(yt, q[0], q[1], q[2], 20 if q[2] == 'edit' else 25, q[3]), queries):
            for e in res:
                if e['id'] not in seen:
                    seen.add(e['id'])
                    found.append(e)
    STATS['searched'] = len(found)

    def limits(v):
        """(min views, max duration) for a candidate of vibe v."""
        if v == 'edit':
            return max(1000, a.min_views // 10), EDIT_MAX_DUR
        return (max(3000, a.min_views // 3) if src else a.min_views), a.max_duration

    kinds = list(wanted) + (['edit'] if n_edit else [])
    cands = {v: [] for v in kinds}
    for e in found:
        why = prefilter(e, used, block, *limits(e['vibe']), src=src)
        if why:
            _bump('rejected', why)
        else:
            cands[e['vibe']].append(e)
    cands = {v: pre_rank(c, rng, src) for v, c in cands.items()}
    STATS['candidates'] = sum(len(c) for c in cands.values())
    log(f'{len(queries)} searches -> {len(found)} results; candidates '
        + ', '.join(f'{v} {len(c)}' for v, c in cands.items()))

    # 2. probe the best candidates of each vibe, then rank by views, velocity, shortness
    def do_probe(e):
        if left() < 90:
            return None
        min_views, max_dur = limits(e['vibe'])
        info, why = probe(yt, e, used, block, max_dur, min_views, src)
        _bump('probed')
        if not info:
            _bump('rejected', why)
            return None
        info['_theme'], info['_vibe'] = e['theme'], e['vibe']
        return info

    if src:
        batch = [e for v, k in wanted.items() for e in cands[v][:k * 3 + 5]] + cands.get('edit', [])[:n_edit * 2 + 6]
    else:
        batch = [e for v, k in wanted.items() for e in cands[v][:k * 4 + 6]]
    infos = {v: [] for v in kinds}
    with cf.ThreadPoolExecutor(6) as ex:
        for info in ex.map(do_probe, batch):
            if info:
                infos[info['_vibe']].append(info)
    for v in infos:
        infos[v] = spread_channels(sorted(infos[v], key=lambda i: -source_rank(i, rng, src)), series=not src)
    if n_edit:
        for i in infos['edit']:
            t = _norm(i.get('title'))
            i['_align'] = bool(ref) and (i.get('duration') or 0) <= ALIGN_MAX_DUR and (
                src.has_song(i) or (src.kind == 'anime' and bool(OP_RE.search(t)))
                or (src.kind == 'group' and not src.song))
        infos['edit'].sort(key=lambda i: not i['_align'])      # stable: rank order within each group
    STATS['kept'] = sum(len(i) for i in infos.values())
    log(f'{STATS["probed"]} probed; kept ' + ', '.join(f'{v} {len(i)}' for v, i in infos.items())
        + (f'; {sum(i["_align"] for i in infos["edit"])} edit sources to align' if n_edit and ref else ''))
    models.join(timeout=60)

    # 3. download + analyse, vibes interleaved so the time budget is shared
    need = {v: k + 1 for v, k in wanted.items()}      # sources with a usable moment
    edit_need = n_edit + 2 if n_edit else 0            # shots, a couple spare for de-duplication
    align_need = min(n_edit, 4) if ref else 0
    reserve = 45 if n_edit else 30                     # seconds kept for cutting and the manifest
    good = Counter()
    align_left = [sum(1 for i in infos.get('edit', []) if i['_align'])]
    stop = threading.Event()
    segs, shots = [], []

    def not_anime(info, path, ss, span):
        """Anime source: the video is live action (real dancers, a school show, a cosplayer)."""
        if src and src.kind == 'anime' and live_action(path, ss, span):
            log(f'{info["id"]} {info.get("title", "")[:45]!r}: live action, not the anime; skipped')
            _bump('rejected', 'live_action')
            return True
        return False

    def work(info):
        v = info['_vibe']
        # keep ~30 s at the end for cutting and the manifest
        if stop.is_set() or good[v] >= need[v] or left() < reserve + 20:
            return None
        path, offset = download(yt, info, src_dir, min(60, left() - reserve - 5))
        if not path:
            _bump('rejected', 'download')
            return None
        _bump('downloaded')
        wa, wb = window(info)
        if not_anime(info, path, max(0.0, wa - offset), wb - wa):
            return None
        found_segs = analyze(path, info, offset, theme if src else info['_theme'], v)
        log(f'{v}: {info["id"]} {info.get("title", "")[:45]!r} ({info.get("duration")}s, '
            f'{info.get("view_count")} views): {len(found_segs)} moments')
        return found_segs

    def aligned(info):
        """(shots, file): the shot of a video that plays our song window, cut exactly to it ([] if
        none), and the downloaded video when it can be reused for unaligned shots."""
        _bump('align_tried')
        dur = info.get('duration') or 0
        S = ref.start
        if dur <= WHOLE_MAX:
            # stages / practices / openings are short: fetch the whole video (chunked, fast) and
            # align on its own audio track, so the offset is measured on the file that gets cut
            path, offset = download(yt, info, src_dir, min(60, left() - reserve - 5))
            if not path:
                _bump('rejected', 'download')
                return [], None
            _bump('downloaded')
            ya = align.decode(path, runner=run, pad=True)
            loc = hit = ref.locate(ya)
            keep = (path, offset)
        else:
            # long videos: a small audio-only copy first, then just the matching section (section
            # downloads stream at about playback speed, so only for these)
            aud = download_audio(yt, info, src_dir, min(45, left() - reserve - 5))
            hit = ref.locate(align.decode(aud, runner=run, pad=True)) if aud else None
            keep = None
        if not hit or hit.get('weak'):
            log(f'align: {info["id"]} {info.get("title", "")[:40]!r}: no match'
                + (f' (weak: score {hit["score"]} z {hit["z"]} ratio {hit["ratio"]})' if hit else ''))
            return [], keep
        if keep is None:
            v0, v1 = hit['t0'] + hit['song_from'] - S, hit['t0'] + hit['song_to'] - S
            path, offset = download(yt, info, src_dir, min(90, left() - reserve - 5),
                                    section=(max(0.0, v0 - 3), min(dur, v1 + 3)), tag='al')
            if not path:
                return [], None
            _bump('downloaded')
            # the section starts on a keyframe up to several seconds before the request, and its
            # audio track may differ from the audio-only one by tens of ms: find the window again
            ya = align.decode(path, runner=run, pad=True)
            loc = ref.locate(ya, around=hit['t0'] - offset, search=12.0)
            if not loc:
                log(f'align: {info["id"]}: window lost in the downloaded section')
                return [], None
        lv0 = max(0.0, loc['t0'] + loc['song_from'] - S)
        lv1 = min(len(ya) / align.SR, loc['t0'] + loc['song_to'] - S)
        if lv1 - lv0 < align.MIN_COVER:
            return [], keep
        # live-action check on the same stretch the unaligned path uses (a 14 s window alone is
        # too little to tell fast animation on ones from a camera)
        wa, wb = window(info) if keep else (offset, offset + len(ya) / align.SR)
        if not_anime(info, path, max(0.0, wa - offset), wb - wa):
            return [], 'skip'
        found_shots = analyze_edit(path, info, offset, lv0, lv1 - lv0, theme, force=True)
        for s in found_shots:
            s.update({'aligned': True, 'align_score': hit['score'], 'song_offset': round(S + lv0 - loc['t0'], 3),
                      'audio_is_song': True, 'sfx_hits': [], 'sfx_score': 0.0, '_local': lv0, '_dur': lv1 - lv0,
                      'duration': round(lv1 - lv0, 2)})
        log(f'align: {info["id"]} {info.get("title", "")[:40]!r}: song {loc["song_from"]:.1f}-{loc["song_to"]:.1f}s '
            f'at {offset + loc["t0"]:.2f}s (score {hit["score"]}, z {hit["z"]}, ratio {hit["ratio"]}): '
            f'{"kept" if found_shots else "rejected on frames"}')
        if found_shots:
            _bump('align_found')
        return found_shots, (path, offset)

    def edit_work(info):
        if stop.is_set() or left() < reserve + 20:
            return None
        found_shots, have = [], None
        if info.get('_align'):
            try:
                if good['_aligned'] < align_need:
                    found_shots, have = aligned(info)
            finally:
                with _lock:
                    align_left[0] -= 1
            if found_shots:
                return found_shots
        if have == 'skip' or good['_edit'] >= edit_need or stop.is_set() or left() < reserve + 20:
            return None
        if have:            # the video fetched for alignment: its best shots are still edit material
            path, offset = have
        else:
            path, offset = download(yt, info, src_dir, min(60, left() - reserve - 5))
            if not path:
                _bump('rejected', 'download')
                return None
            _bump('downloaded')
        wa, wb = window(info)
        if not_anime(info, path, max(0.0, wa - offset), wb - wa):
            return None
        found_shots = analyze_edit(path, info, offset, max(0.0, wa - offset), wb - wa, theme)
        for s in found_shots:
            s['aligned'] = False
        log(f'edit: {info["id"]} {info.get("title", "")[:45]!r} ({info.get("duration")}s): {len(found_shots)} shots')
        return found_shots

    def done():
        tiles = all(good[v] >= need[v] for v in wanted)
        edits = good['_edit'] >= edit_need and (good['_aligned'] >= align_need or align_left[0] <= 0)
        return tiles and edits

    order = []
    queues = [list(infos[v]) for v in wanted]
    while any(queues):
        for q in queues:
            if q:
                order.append(q.pop(0))
    if n_edit:      # one edit source after every two tile sources, aligned candidates first
        tiles_order, order = order, []
        edits = list(infos['edit'])
        while tiles_order or edits:
            order += tiles_order[:2] + edits[:1]
            tiles_order, edits = tiles_order[2:], edits[1:]
    ex = cf.ThreadPoolExecutor(a.workers)
    futs = [ex.submit(edit_work if i['_vibe'] == 'edit' else work, i) for i in order]
    try:
        for fut in cf.as_completed(futs, timeout=max(10, left() - reserve)):
            try:
                r = fut.result()
            except Exception as err:
                log(f'candidate error: {err!r}')
                continue
            if r and r[0]['vibe'] == 'edit':
                shots += r
                good['_edit'] += len(r)
                good['_aligned'] += sum(1 for s in r if s.get('aligned'))
            elif r:
                segs += r
                good[r[0]['vibe']] += 1
            if done():
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
    chosen = select_source(segs, wanted) if src else select(segs, wanted, mixed=theme == 'mixed')
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
    edits = []
    if n_edit:
        picked = select_edit(shots, n_edit, chosen)

        def do_edit(k_s):
            k, s = k_s
            p = os.path.join(out_dir, f'edit_{k:03d}.mp4')
            if not cut(s, p, audio=True):
                return s, None
            if s.get('aligned'):    # measure the song time at clip t=0 on the file itself
                exp = ref.start - s['song_offset']
                loc = ref.locate(align.decode(p, runner=run), around=exp, search=0.6)
                if loc:
                    s['song_offset'] = round(ref.start - loc['t0'], 3)
                    s['align_check'] = loc['score']
                else:
                    log(f'align: {s["source_id"]}: the cut clip does not match the song, marked unaligned')
                    s.update({'aligned': False, 'audio_is_song': False})
            return s, p

        with cf.ThreadPoolExecutor(2) as cx:
            for s, p in cx.map(do_edit, enumerate(picked)):
                if p:
                    c = {'path': p}
                    c.update({k: v for k, v in s.items() if not k.startswith('_')})
                    edits.append(c)
        edits.sort(key=lambda c: not c.get('aligned'))
        STATS['edit_shots'] = len(shots)
    with open(manifest, 'w', encoding='utf-8') as f:
        if src:
            json.dump({'source': {'name': src.name, 'kind': src.kind, 'aliases': src.strong + src.weak,
                                  'risky': src.risky},
                       'song': ({'name': src.song, 'audio': os.path.abspath(a.song_audio), 'start': ref.start,
                                 'len': ref.length} if ref else None),
                       'clips': clips, 'edit': edits}, f, ensure_ascii=False, indent=1)
        else:
            json.dump(clips, f, ensure_ascii=False, indent=1)
    if a.sheet and (clips or edits):
        contact_sheet(clips + edits, a.sheet)
    if not a.keep_src:
        shutil.rmtree(src_dir, ignore_errors=True)
    shutil.rmtree(yt.workdir, ignore_errors=True)

    per_vibe = {v: sum(1 for c in clips if c['vibe'] == v) for v in wanted}
    stats = dict(STATS, rejected=dict(STATS['rejected']), seg_rejected=dict(STATS['seg_rejected']),
                 sources=len(set(c['source_id'] for c in clips)), seconds=round(time.time() - T0, 1))
    log(f'stats {json.dumps(stats)}')
    line = {'ok': bool(clips) and all(per_vibe.values()), 'count': len(clips), 'manifest': manifest,
            'vibes': per_vibe, 'stats': stats}
    if src:
        line.update({'source': src.name, 'source_kind': src.kind, 'edit_count': len(edits),
                     'aligned_count': sum(1 for c in edits if c.get('aligned')),
                     'fallbacks': sum(1 for c in clips if c.get('vibe_fallback'))})
    print(json.dumps(line))
    sys.stdout.flush()
    os._exit(0 if clips else 1)  # don't wait on straggling worker threads


if __name__ == '__main__':
    main()
