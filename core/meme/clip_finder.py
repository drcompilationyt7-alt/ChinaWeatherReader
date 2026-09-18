"""
Clip sourcing for the "<song> acapella" meme Shorts: searches YouTube for
funny Asian (Douyin, Korean variety, Japanese TV) or anime clips, downloads a
few, and cuts them into 0.6-2.5 s single-shot segments that the editor lays
over the beat. Segments are ranked on sudden motion, faces, loud peaks in the
original audio and YouTube's "most replayed" heatmap, and black, static,
caption-covered or nude/swimwear frames are skipped. Clips keep the source
aspect and have no audio.

Install (GitHub ubuntu-latest; ffmpeg/ffprobe on PATH):
  python3 -m pip install yt-dlp yt-dlp-ejs opencv-python-headless numpy --break-system-packages
YouTube also needs a JS runtime: deno on PATH (yt-dlp's default), or node,
which is used automatically when deno is missing. Two small ONNX models are
fetched once into ~/.cache/clip_finder (override with CLIP_FINDER_CACHE):
YuNet faces (230 KB) and NudeNet 320n (12 MB, exposed/swimwear body parts).
Scene cuts come from the same decoding pass (a PySceneDetect-style HSV
content detector), so scenedetect is not needed.

Usage:
  python core/meme/clip_finder.py --out-dir work/clips --count 16 \
      [--theme asian|anime|mixed] [--used used.json] [--cookies cookies.txt] \
      [--block blocked.json] [--sheet contact.png]

Writes <out-dir>/clip_NNN.mp4 and <out-dir>/clips.json, then prints one line:
  {"ok": true, "count": N, "manifest": "...", "stats": {...}}
--used: ids as a list, {id: date}, or [{"id"/"source_id", "date"}]; entries
dated older than --used-days are ignored. --block: channel ids, @handles,
names or URLs as a list, or {"channels": [...]}.
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

QUERIES = {
    'asian': [
        'douyin funny moments', 'douyin funny videos {year}', 'chinese funny video',
        'funny chinese kids', 'chinese mom funny', 'asian mom meme', 'asian parents be like',
        'chinese grandma funny', 'funny china moments', 'douyin try not to laugh',
        'chinese prank funny', 'douyin comedy skit', 'korean variety show funny',
        'running man funny moments', 'knowing bros funny moments', 'korean game show funny',
        'korean prank funny', 'the return of superman funny', '2 days 1 night funny moments',
        'workman jang sung kyu funny', 'japanese prank show', 'japanese variety show funny',
        'gaki no tsukai funny', 'japanese tv show funny moments', 'funny asian reactions',
    ],
    'anime': [
        'anime funny moments', 'anime funny scenes', 'anime funny moments {year}',
        'anime reaction meme', 'one piece funny moments', 'jjk funny moments',
        'jujutsu kaisen funny moments', 'naruto funny moments', 'spy x family funny moments',
        'demon slayer funny moments', 'haikyuu funny moments', 'gintama funny moments',
        'mob psycho 100 funny moments', 'one punch man funny moments', 'saiki k funny moments',
        'bocchi the rock funny moments', 'dragon ball super funny moments',
        'sakamoto days funny moments', 'frieren funny moments', 'blue lock funny moments',
        'kaiju no 8 funny moments', 'hunter x hunter funny moments', 'solo leveling funny moments',
        'anime dub funny moments', 'anime out of context', 'attack on titan funny moments',
        'pokemon anime funny moments', 'kaguya sama funny moments', 'wind breaker anime funny',
    ],
}
# Shorts hashtag feeds (asian only: the anime tags are mostly cosplay and edits)
HASHTAGS = ['douyinfunny', 'chinesefunny', 'funnychinese', 'koreanvariety', 'japanesefunny']
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
# Japanese game shows are a common source of sexualised "funny" clips
RISKY_RE = re.compile(r'(?=.*\bjapan)(?=.*\bgame\s?shows?\b)')
# South Asian / Arabic scripts in the title: off-theme for this channel
OFFTHEME_RE = re.compile('[؀-ۿݐ-ݿऀ-෿]')
# An "asian" candidate needs one of these in its title/tags/description/channel, or CJK text
ASIA_WORDS = [
    'china', 'chinese', 'douyin', 'korea', 'korean', 'kpop', 'k-pop', 'kdrama', 'k-drama',
    'running man', 'knowing bros', 'kocowa', 'sbs', 'kbs', 'mbc', 'jtbc', 'tvn', 'japan',
    'japanese', 'gaki', 'batsu', 'asian', 'asia', 'taiwan', 'hong kong', 'variety show',
    '2 days 1 night', '1 night 2 days', 'workman', 'return of superman', 'tokyo', 'osaka', 'seoul',
    'beijing', 'shanghai',
]
CJK_RE = re.compile('[぀-ヿ一-鿿가-힯]')
# Off-brand for a meme channel: tragedy, violence, politics, racist framing.
DARK_WORDS = [
    'dead body', 'killed', 'murder', 'accident', 'crash', 'funeral', 'tragic', 'tragedy',
    'suicide', 'abuse', 'abused', 'gore', 'bloody', 'injured', 'ccp', 'xi jinping', 'tiananmen',
    'uyghur', 'ching chong', 'chingchong', 'racist', 'racism',
]
# Ranking hints from the title: comedy up, fights / drama down.
FUNNY_WORDS = [
    'funny', 'funniest', 'meme', 'memes', 'lol', 'lmao', 'comedy', 'comedic', 'hilarious', 'joke',
    'jokes', 'prank', 'pranks', 'pranked', 'laugh', 'laughing', 'gag', 'troll', 'trolling', 'crack',
    'out of context', 'chaos', 'chaotic', 'silly', 'dumb', 'idiot', 'idiots', 'derp', 'reaction',
    'reactions', 'batsu', 'try not to laugh', 'being',
]
FUNNY_CHARS = ['😂', '🤣', '😭', '💀', '笑', '搞笑', '爆笑', '웃긴', 'ㅋㅋ', '面白', 'おもしろ']
ACTION_WORDS = [
    'fight', 'full fight', 'battle', 'vs', 'versus', 'epic', 'badass', 'sad', 'emotional',
    'death', 'dies', 'died', 'episode', 'ep', 'revealed', 'amv',
]
# Not clip material.
OFFTYPE_WORDS = [
    'asmr', 'mukbang', 'podcast', 'full episode', 'lyrics', 'official mv', 'music video',
    'trailer', 'karaoke', 'tutorial', 'recipe', 'live stream', 'livestream', 'amv', 'phonk',
    '#edit', 'edit audio', 'trollface', 'troll face', 'prank call', 'prank calls',
    'dance', 'dancing', 'dancer', 'dancers', 'choreography', '舞蹈', '跳舞', '댄스', 'ダンス',
]

FPS = 10            # analysis frame rate
SAMPLE = 3          # text / face checks on every 3rd analysis frame
NUDE_EVERY = 2      # nudity check on every 2nd sample (~1.7 per second)
GRID = 24           # coverage grid for the text mask
AN = 320            # analysis frame long side
MIN_SEG, WIN = 0.6, 2.2
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
PATHS = {}
HAAR = None
_tls = threading.local()
_lock = threading.Lock()
_procs = set()
STATS = {'searched': 0, 'rejected': Counter(), 'probed': 0, 'downloaded': 0, 'analyzed': 0,
         'segments': 0, 'seg_rejected': Counter()}
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


def search(yt, query, theme, n=20):
    if query.startswith('#'):
        url = f'https://www.youtube.com/hashtag/{query[1:]}/shorts'
    else:
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
                    'query': query, 'theme': theme})
    return res


# ---------------------------------------------------------------- filters

def _norm(text):
    return unicodedata.normalize('NFKC', text or '').lower()


def _word_re(words):
    alt = '|'.join(sorted((r'[\s_\-]*'.join(map(re.escape, w.split())) for w in words), key=len, reverse=True))
    return re.compile(r'(?<![a-z0-9])(?:' + alt + r')(?![a-z0-9])')


NSFW_RE = _word_re(NSFW_WORDS)
SHOW_RE = _word_re(UNSAFE_SHOWS)
DARK_RE = _word_re(DARK_WORDS)
OFFTYPE_RE = _word_re(OFFTYPE_WORDS)
FUNNY_RE = _word_re(FUNNY_WORDS)
ACTION_RE = _word_re(ACTION_WORDS)
ASIA_RE = _word_re(ASIA_WORDS)


def text_reason(*parts):
    """Reject reason for title/description/tags text, or None."""
    t = _norm(' '.join(p if isinstance(p, str) else ' '.join(map(str, p or [])) for p in parts))
    flat = re.sub(r'[^a-z0-9+#]+', ' ', t)
    if NSFW_RE.search(t) or any(c in t for c in NSFW_CHARS) or SHOW_RE.search(flat) or RISKY_RE.search(flat):
        return 'nsfw'
    if DARK_RE.search(t):
        return 'dark'
    if OFFTYPE_RE.search(t):
        return 'offtype'
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


def prefilter(e, used, block, min_views, max_dur):
    if e['id'] in used:
        return 'used'
    if is_blocked(e, block):
        return 'blocked'
    if e['live']:
        return 'live'
    if e['duration'] and (e['duration'] > max_dur or e['duration'] < 5):
        return 'length'
    if e['views'] is not None and e['views'] < min_views:
        return 'views'
    if OFFTHEME_RE.search(e['title']):
        return 'offtheme'
    return text_reason(e['title'], e['description'])


def probe(yt, e, used, block, max_dur, theme):
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
    if dur > max_dur or dur < 5:
        return None, 'length'
    if min(info.get('width') or 999, info.get('height') or 999) < 320:
        return None, 'lowres'
    if OFFTHEME_RE.search(info.get('title') or ''):
        return None, 'offtheme'
    if theme == 'asian':
        head = ' '.join([info.get('title') or '', info.get('channel') or ''] + list(info.get('tags') or []))
        if not (CJK_RE.search(head) or ASIA_RE.search(_norm(head + ' ' + (info.get('description') or '')))):
            return None, 'offtheme'
    why = text_reason(info.get('title'), info.get('description'), info.get('tags'),
                      info.get('categories'))
    if why:
        return None, why
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
        json.dump(info, f)
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


def analyze(path, info, offset, theme):
    """Score the usable single-shot windows of one downloaded video."""
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
    if n < FPS * MIN_SEG * 2:
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
    luma = crop.mean((1, 2))
    detail = crop.std((1, 2))
    black = (crop < 20).mean((1, 2))  # small picture on a black canvas
    flat = crop.reshape(n, -1).astype(np.int16)
    modes = np.array([np.bincount(r, minlength=256).argmax() for r in flat])
    uniform = (np.abs(flat - modes[:, None]) <= 8).mean(1)  # logo / title cards on a plain background
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

    t = offset + ss + np.arange(n) / FPS
    heat = np.zeros(n)
    if vdur >= 60 and info.get('heatmap'):
        for m in info['heatmap']:
            heat[(t >= m['start_time']) & (t < m['end_time'])] = m.get('value', 0)
        heat[t < 0.05 * vdur] = 0

    gy0, gx0 = int(y0 * GRID), int(x0 * GRID)
    gy1, gx1 = max(gy0 + 1, int(round(y1 * GRID))), max(gx0 + 1, int(round(x1 * GRID)))
    carea = max(1e-6, (x1 - x0) * (y1 - y0))
    s_text = np.array([float(s['text'][gy0:gy1, gx0:gx1].mean()) for s in samples])
    s_face = np.array([max([fw * fh / carea for _, _, fw, fh, _ in s['faces']] + [0.0]) for s in samples])
    s_face[s_face < 0.002] = 0
    s_sharp = np.array([s['sharp'] for s in samples])
    res_k = 0.8 if min(W, H) < 480 else 1.0

    event = np.minimum(1, spike / 0.06) + np.minimum(1, loud / 9)
    head = 5 if at_start else 2
    tail = (25 if vdur > 20 else 10) if at_end else 2   # skip end cards
    bounds = [0] + cuts + [n]
    segs = []
    win = int(WIN * FPS)
    for si in range(len(bounds) - 1):
        lo = bounds[si] + (2 if si else head)
        hi = bounds[si + 1] - (1 if si + 1 < len(bounds) - 1 else tail)
        if hi - lo < MIN_SEG * FPS:
            _bump('seg_rejected', 'short')
            continue
        L = min(win, hi - lo)
        scored, whys = [], Counter()
        for st in range(lo, hi - L + 1, 3):
            sc, why = _window(st, st + L, mot, spike, loud, heat, luma, detail, black, uniform,
                              s_text, s_face, s_nude, s_sharp)
            if why:
                whys[why] += 1
            else:
                scored.append((sc * res_k, st))
        if not scored:
            _bump('seg_rejected', whys.most_common(1)[0][0])
            continue
        scored.sort(reverse=True)
        picked = []
        for sc, st in scored:
            if len(picked) >= 1 + (hi - lo) // (6 * FPS):
                break
            if all(abs(st - p) >= L + FPS for _, p in picked):
                picked.append((sc, st))
        for sc, st in picked:
            e = st + L
            js = _samples(st, e, len(samples))
            pk = st + int(np.argmax(event[st:e]))
            segs.append({
                'source_id': info['id'], 'source_channel': info.get('channel') or info.get('uploader') or '',
                'source_channel_id': info.get('channel_id') or '',
                'source_title': info.get('title') or '', 'theme': theme,
                'start': round(float(t[st]), 2), 'end': round(float(t[st]) + L / FPS, 2),
                'peak': round((pk - st) / FPS, 2), 'score': round(sc, 4),
                'has_face': bool((s_face[js] >= 0.004).mean() >= 0.5),
                'motion': round(float(min(1, mot[st + 1:e].mean() / 0.05)), 3),
                'width': W - W % 2, 'height': H - H % 2,
                'content_box': [round(v, 3) for v in (x0, y0, x1, y1)],
                '_src': path, '_local': ss + st / FPS, '_dur': L / FPS,
                '_hash': dhash(thumbs[(st + e) // 2][cy0:cy1, cx0:cx1]),
            })
    _bump('analyzed')
    _bump('segments', n=len(segs))
    return segs


def _samples(a, b, n):
    js = [j for j in range(-(-a // SAMPLE), -(-b // SAMPLE)) if j < n]
    return js or [min(n - 1, ((a + b) // 2) // SAMPLE)]


def _window(a, b, mot, spike, loud, heat, luma, detail, black, uniform, s_text, s_face, s_nude, s_sharp):
    """(score, reject_reason) for frames a..b of one shot."""
    js = _samples(a, b, len(s_text))
    near = range(max(0, js[0] - NUDE_EVERY * 2), min(len(s_nude), js[-1] + NUDE_EVERY * 2 + 1))
    if s_nude[near].any():
        return 0, 'nsfw'
    lm, dt = luma[a:b].mean(), detail[a:b].mean()
    m = mot[a + 1:b].mean() if b - a > 1 else 0.0
    text, sharp = s_text[js].mean(), float(np.median(s_sharp[js]))
    if lm < 25 or black[a:b].mean() > 0.45:
        return 0, 'dark'
    if dt < 12 or uniform[a:b].max() > 0.8:
        return 0, 'blank'
    if m < 0.004 and spike[a:b].max() < 0.01:
        return 0, 'static'
    if text > 0.10:
        return 0, 'text'
    if sharp < 40:
        return 0, 'blurry'
    nm = min(1, m / 0.05)
    ns = min(1, spike[a:b].max() / 0.06)
    nl = min(1, loud[a:b].max() / 9)
    nf = float(np.minimum(1, s_face[js] / 0.02).mean())
    nh = float(heat[a:b].mean())
    sc = 0.22 * nm + 0.2 * ns + 0.23 * nl + 0.2 * nf + 0.15 * nh
    sc *= 1 - max(0, text - 0.03) * 4
    if lm < 45:
        sc *= 0.7
    if dt < 25:
        sc *= 0.7
    if sharp < 150:
        sc *= 0.7
    if b - a < 1.2 * FPS:
        sc *= 0.85
    return sc, None


# ---------------------------------------------------------------- output

def select(segs, count, per_source):
    segs = sorted(segs, key=lambda s: -s['score'])
    chosen = []

    def ok(s, cap):
        if sum(c['source_id'] == s['source_id'] for c in chosen) >= cap:
            return False
        for c in chosen:
            if bin(c['_hash'] ^ s['_hash']).count('1') <= 10:
                return False
            if c['source_id'] == s['source_id'] and s['start'] < c['end'] + 0.3 and c['start'] < s['end'] + 0.3:
                return False
        return True

    for cap in (per_source, per_source * 3):
        for s in segs:
            if len(chosen) >= count:
                break
            if s not in chosen and ok(s, cap):
                chosen.append(s)
    # interleave sources so neighbouring clips differ
    by_src = {}
    for s in chosen:
        by_src.setdefault(s['source_id'], []).append(s)
    out, queues = [], list(by_src.values())
    while any(queues):
        for q in queues:
            if q:
                out.append(q.pop(0))
    return out


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
        mid = (c['end'] - c['start']) / 2
        _, out, _ = run(['ffmpeg', '-v', 'error', '-ss', f'{mid:.2f}', '-i', c['path'], '-frames:v', '1',
                         '-f', 'image2pipe', '-vcodec', 'png', '-'], 30)
        img = cv2.imdecode(np.frombuffer(out, np.uint8), cv2.IMREAD_COLOR) if out else None
        r, q = divmod(k, cols)
        oy, ox = r * (cell + 34), q * cell
        if img is not None:
            s = cell / max(img.shape[:2])
            img = cv2.resize(img, (max(1, int(img.shape[1] * s)), max(1, int(img.shape[0] * s))))
            y, x = oy + (cell - img.shape[0]) // 2, ox + (cell - img.shape[1]) // 2
            sheet[y:y + img.shape[0], x:x + img.shape[1]] = img
        label = f'{k} {c["theme"][:5]} s{c["score"]:.2f} {"F" if c["has_face"] else "-"} {c["end"] - c["start"]:.1f}s'
        cv2.putText(sheet, label, (ox + 4, oy + cell + 14), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (230, 230, 230), 1, cv2.LINE_AA)
        cv2.putText(sheet, c['source_id'], (ox + 4, oy + cell + 29), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (150, 200, 255), 1, cv2.LINE_AA)
    cv2.imwrite(path, sheet)


# ---------------------------------------------------------------- main

def pick_queries(theme, rng):
    year = datetime.now().year
    n = {'asian': 4, 'anime': 3} if theme == 'mixed' else {theme: 6}
    out = [(q.format(year=year), g) for g, k in n.items() for q in rng.sample(QUERIES[g], k)]
    if theme != 'anime':
        out.append(('#' + rng.choice(HASHTAGS), 'asian'))
    return out


def rank(entries, rng):
    """Most-viewed first per query (with jitter), round-robin across queries."""
    by_q = {}
    for e in entries:
        pop = math.log10((e['views'] or 30000) + 1) + rng.uniform(0, 0.8)
        if e['duration'] and e['duration'] > 180:
            pop -= 0.5
        title = _norm(e['title'])
        if FUNNY_RE.search(title) or any(c in title for c in FUNNY_CHARS):
            pop += 0.7
        if ACTION_RE.search(title):
            pop -= 1.0
        if e['theme'] == 'asian':  # probe checks tags too; explicit signals go first
            head = f'{e["title"]} {e["channel"]} {e["description"]}'
            if not (CJK_RE.search(head) or ASIA_RE.search(_norm(head))):
                pop -= 1.0
        by_q.setdefault(e['query'], []).append((pop, e))
    queues = [[e for _, e in sorted(v, key=lambda p: -p[0])] for v in by_q.values()]
    rng.shuffle(queues)
    out = []
    while any(queues):
        for q in queues:
            if q:
                out.append(q.pop(0))
    return out


def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding='utf-8', errors='replace')
        except AttributeError:
            pass
    ap = argparse.ArgumentParser(description='Find and cut funny Asian / anime clips for meme Shorts')
    ap.add_argument('--out-dir', required=True)
    ap.add_argument('--count', type=int, default=16)
    ap.add_argument('--theme', choices=['asian', 'anime', 'mixed'], default='mixed')
    ap.add_argument('--used', help='JSON of video ids used recently')
    ap.add_argument('--used-days', type=int, default=30)
    ap.add_argument('--block', help='JSON of blocked channels')
    ap.add_argument('--cookies', help='Netscape cookies file for YouTube')
    ap.add_argument('--js-runtimes', default='auto', help='yt-dlp JS runtime: auto, deno, node, bun, none')
    ap.add_argument('--budget', type=float, default=330, help='total seconds')
    ap.add_argument('--per-source', type=int, default=3)
    ap.add_argument('--min-views', type=int, default=20000)
    ap.add_argument('--max-duration', type=int, default=240)
    ap.add_argument('--workers', type=int, default=3)
    ap.add_argument('--seed', type=int, help='query rotation seed (default: day number)')
    ap.add_argument('--sheet', help='also write a contact sheet PNG of the clips')
    ap.add_argument('--keep-src', action='store_true', help='keep downloaded sources')
    a = ap.parse_args()

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
    log(f'theme={a.theme} count={a.count} used={len(used)} blocked={len(block)} '
        f'cookies={"yes" if yt.cookies else "no"}')
    models = threading.Thread(target=load_models, daemon=True)
    models.start()

    # 1. search
    queries = pick_queries(a.theme, rng)
    found, seen = [], set()
    with cf.ThreadPoolExecutor(4) as ex:
        for res in ex.map(lambda qt: search(yt, qt[0], qt[1]), queries):
            for e in res:
                if e['id'] not in seen:
                    seen.add(e['id'])
                    found.append(e)
    STATS['searched'] = len(found)
    cands = []
    for e in found:
        why = prefilter(e, used, block, a.min_views, a.max_duration)
        if why:
            _bump('rejected', why)
        else:
            cands.append(e)
    cands = rank(cands, rng)
    log(f'{len(queries)} searches -> {len(found)} results, {len(cands)} pass the prefilter')
    models.join(timeout=60)

    # 2. probe, download, analyze in parallel until there are enough sources
    need = max(4, min(12, -(-a.count // a.per_source) + 2))
    stop = threading.Event()
    segs, good = [], []

    def work(e):
        # keep ~30 s at the end for cutting and the manifest
        if stop.is_set() or left() < 60:
            return None
        info, why = probe(yt, e, used, block, a.max_duration, e['theme'])
        _bump('probed')
        if not info:
            _bump('rejected', why)
            return None
        if stop.is_set() or left() < 50:
            return None
        path, offset = download(yt, info, src_dir, min(60, left() - 35))
        if not path:
            _bump('rejected', 'download')
            return None
        _bump('downloaded')
        found_segs = analyze(path, info, offset, e['theme'])
        log(f'{info["id"]} {info.get("title", "")[:50]!r}: {len(found_segs)} segments')
        return found_segs

    ex = cf.ThreadPoolExecutor(a.workers)
    futs = [ex.submit(work, e) for e in cands[:need * 3]]
    try:
        for fut in cf.as_completed(futs, timeout=max(10, left() - 30)):
            try:
                r = fut.result()
            except Exception as err:
                log(f'candidate error: {err!r}')
                continue
            if r:
                segs += r
                good.append(r[0]['source_id'])
            if len(good) >= need and len(segs) >= a.count * 2:
                break
    except cf.TimeoutError:
        log('time budget reached, using what is ready')
    stop.set()
    for f in futs:
        f.cancel()
    ex.shutdown(wait=False, cancel_futures=True)
    for p in list(_procs):
        kill_tree(p)

    # 3. select and cut
    chosen = select(segs, a.count, a.per_source)
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

    stats = dict(STATS, rejected=dict(STATS['rejected']), seg_rejected=dict(STATS['seg_rejected']),
                 sources=len(set(c['source_id'] for c in clips)), seconds=round(time.time() - T0, 1))
    log(f'stats {json.dumps(stats)}')
    print(json.dumps({'ok': bool(clips), 'count': len(clips), 'manifest': manifest, 'stats': stats}))
    sys.stdout.flush()
    os._exit(0 if clips else 1)  # don't wait on straggling worker threads


if __name__ == '__main__':
    main()
