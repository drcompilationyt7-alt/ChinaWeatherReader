#!/usr/bin/env python3
"""
Clip library for the edit pipelines (core/edit): finds, vets and cuts the shots every edit type
is built from. The owner's rule is the whole point of this file: the clips must be GOOD. No
subtitles or burned-in text, no dialogue / talking-head scenes, crisp and visually striking,
and safe (nothing sexual or fan-service, no gore).

    python core/edit/library.py --mode source|character|mood|dance --query "<anime/group/character/mood>"
        [--aliases "a,b"] [--kind anime|group] [--count 12] [--min-dur 2 --max-dur 8] [--with-audio]
        [--need-sfx] [--char-ref-images url1,url2] --out-dir DIR
        [--budget 540] [--analyse-secs 120] [--pace 15] [--probe-pace S] [--page-pace 2.5]
        [--no-global-pace] [--cache-dir DIR] [--cache-max-mb 6000] [--used used.json]
        [--block blocked.json] [--cookies cookies.txt] [--sheet sheet.jpg] [--seed N]
        [--min-sharp N] [--min-aesthetic 0.6] [--no-crop] [--no-text-check]
        [--local a.mp4,dir/*.mkv] [--local-only] [--debug-dir DIR] [--reject-sheet rej.jpg]

Modes
  source     every shot from one anime (creditless OP/ED, 4K twixtor / raw scene packs first) or one
             K-pop group (official MVs, broadcast stages, performance videos, dance practices)
  character  every shot shows one character. Anime: the AniList image in
             core/quiz/assets/anime-characters.json (+ --char-ref-images) and SigLIP: the zero-shot
             "<name> from <anime>" prompt against the show's other characters (plus "nobody"), and
             face-crop similarity against the references and the other characters' images, refined
             with the run's own confident crops. K-pop idol: YuNet + SFace against kpop-idols.json
             photos. Characters / shows flagged nsfw are refused; "character.adult" tells planners
             whether attractiveness framing is allowed (minimum listed age >= 18).
  mood       mood / scene words ("sad rain", "aura", "sunset romance"): anime or K-pop scenes that fit
  dance      dancing only: anime ED/OP dance sequences and dance scenes, or a group's dance
             practices / performances; SigLIP dance share and a periodic-motion test

Sound edits: --with-audio keeps the clip audio (AAC in the cut files) and uses the sound spec's
looser hit detector; --need-sfx also rejects shots without a usable sound hit (sfx_score >= 0.15),
ranks by it and switches the scene preference to the sound-edit one.

Scene preferences (SCENES, learned from core/edit/refs/*.json style.clip_selection)
  Each mode looks for the kinds of moment its reference edits are cut from, and for their shot-scale
  mix. The moment prompts are scored with SigLIP against a "nothing happening" baseline ("moment" in
  shots.json, 0-1) and the framing mix is both a mild per-shot preference ("frame_fit") and a soft
  quota in select(), so the returned pool has roughly the reference mix instead of twelve close-ups.
    source  (general.json)   expressive faces .45, wind / turning .25, action .2, scenery .1;
                             shot scale 55 % close-up / 30 % medium / 15 % wide
    character (character.json) the 11 ranked moments (eye contact, smile, wind, looking back,
                             walking to camera, wink, aura stare, glowing eyes, reaching out,
                             running, eyes ECU); 52 / 28 / 12 plus 8 % other
    dance   (dance.json)     dancing only, full body or waist-up; 15 / 45 / 40
    mood    (general.json)   emotion and scenery weighted up; 40 / 30 / 30
    sound   (sound.json)     close-ups of hands / objects / feet making a crisp transient; 80/15/5
    K-pop   (kpop.json)      posing, hair flips, eye contact, wink, hand-to-camera; 60 / 30 / 10
  Quality floors come from the same reports: --min-sharp defaults to 400 (anime; character.json
  measures 380-2610 at 320 px), 250 (anime dance, dance.md) and 300 (live action), and
  --min-aesthetic 0.6 drops flat, dull shots.

Pipeline: YouTube search (cached, paced) -> title / tag rules (clip_finder safety and relevance
filters; someone else's edit / AMV / MAD / manga animation; subtitled uploads, by SUBS_TITLE_RE
here because clip_finder.SUBS_RE is broken and never matches a Latin title; singer MVs for an
anime; nsfw-flagged shows and characters; channels remembered for burning their name into videos)
-> metadata probe (cached; age limit, >= 720p) -> download (<= 1080p, whole file unless huge,
cached, >= --pace s apart) -> one decode pass per source at 10 fps: scene cuts and flashes, motion,
motion area, sharpness, edge text mask, NudeNet (1 fps), content box -> continuous shots of min..max
dur -> cheap gates (dark without highlights, blank, static, blurry, nude, split-screen composites)
-> PP-OCRv3 DB text detection (burned-in subtitles, lyrics, credits, titles: line-shaped boxes that
hold for 1 s, subtitle lines, multi-line cards; logos and watermarks in a border band are cropped
off via content_box; the same text across shots inside the picture, or a faint fixed overlay,
rejects the source) -> NudeNet on every kept frame of the shot -> anime face boxes and a mouth-flap
test (mouth moving while the eyes hold still) -> Silero VAD speech ratio (singing in OPs / MVs /
twixtor packs is not dialogue) and sound hits -> SigLIP on the best 10 shots per source: striking /
fight / aura / action / dance / cute / close-up / scenery / performance vs talking heads, text,
credits, junk; aesthetics; anime vs live action; clip_judge safety and gore prompts plus scene
prompts; one scene per shot -> dialogue rules -> sharpness and look floors -> score -> near-duplicate
removal (SigLIP embeddings of every key-frame pair + dHash) -> per-source caps and the framing quota
-> cut, cropped to the content box so the delivered file itself has no bars and no channel logo
-> one last gate on the cut file: core/meme/edit_fx.text_bands, the live renderer's own burned-in
text detector. Anything it would have to trim away is dropped and a spare candidate takes its place.

Writes DIR/shot_NNN.mp4 (h264 crf 16, source fps, <= 1080p; AAC audio with --with-audio),
DIR/sheet.jpg (contact sheet) and DIR/shots.json:
  {"mode", "query", "kind", "character": {"name", "parent", "adult", "min_age", "refs"} | null,
   "shots": [{"path", "duration", "width", "height", "fps", "tags", "motion", "aesthetic", "has_speech",
              "has_text", "sfx_hits", "sfx_score", "peak", "hits", "character_match", "source_id",
              "source_title", "source_channel", "content_box",
              extras: "score", "start", "end" (source seconds), "framing" (closeup|medium|wide),
              "faces", "speech_ratio", "sharpness", "luma", "hue", "colorful", "periodic", "area",
              "anime", "pretty", "creditless", "twixtor", "official", "source_kind", "has_audio",
              "music_audio", "views", "source_channel_id", "watermark_cropped",
              "moment", "scene", "frame_fit" (how well the shot fits its mode's reference scenes),
              "text_band" [top, bottom] from edit_fx.text_bands (always [0, 0]: anything else was
              dropped), "mouth_flap", "mouth_frames", "talk", "big_face",
              "source_box" (the content box in the SOURCE frame) and "cropped"}], "stats": {...}}
  shots best first. tags from dance, fight, aura, cute, closeup, scenery, action, performance.
  motion: mean frame change / 0.05 (1.0 = strong); aesthetic 0-1; peak / hits / sfx_hits: seconds
  into the shot file. "content_box" is the usable part of THE FILE: [0, 0, 1, 1] whenever the crop
  was already applied (the normal case), so a caller may always honour it and never crops twice.
and prints one line: {"ok", "count", "manifest", "mode", "query", "stats"} (ok: at least half of
--count, and at least 3). Exit code 1 when no shot was found.

Cache layout ($EDIT_CACHE, or --cache-dir, default <tmp>/edit-cache) - shared by every agent:
  search/<sha>.json     a search-results page, 3 days
  info/<video id>.json  yt-dlp metadata, 14 days. info/_watermark_channels.json remembers uploaders
                        that burn their name into their videos; their other uploads are skipped.
  src/<id>__all.<ext>   the downloaded source (or <id>__<a>-<b>.<ext> for a section of a huge one),
                        plus <id>.info.json. LRU-pruned to --cache-max-mb (default 6 GB); the files
                        a run just used are never pruned, so the shots stay there to be reused.
  ana/<key>.json        one source's candidate windows and their measurements, and
  emb/<key>.npz         their SigLIP embeddings. A source analysed once is re-scored later (new
                        mode, new query, new thresholds) without decoding it again: this is why a
                        warm run takes under a minute. `key` ends in __v<VERSION>, so bumping
                        VERSION when the analysis changes retires the old entries by itself.
  emb/text_<sha>.npz    the SigLIP text-prompt embeddings.
  refs/<sha>.img        character reference portraits.
  net.lock, net-pace.json   the cross-process download cadence (see GlobalPacer).
  To reuse what a run downloaded: the source file is src/<source_id>__all.*, and shots.json gives
  every shot's "source_id", "start" and "end" in source seconds, so a tuning agent can re-cut any
  shot straight out of the cache with no network at all (add --local-only to be sure of that).

Models (fetched once into ~/.cache/clip_finder, or $CLIP_FINDER_CACHE): clip_finder's YuNet and
NudeNet, deepghs anime face detector (YOLOv8n, 12 MB, MIT), opencv_zoo PP-OCRv3 text detector
(2.4 MB, Apache-2.0), Silero VAD (2.2 MB, MIT; torch JIT), SFace (K-pop character mode only, 37 MB),
google/siglip-base-patch16-224 (transformers, as clip_judge.py).

Network: every yt-dlp call goes through GlobalPacer, a cadence shared by all processes on this PC
through net.lock / net-pace.json in the cache directory. Downloads and metadata probes are >= --pace
seconds apart (15 by default) counting across agents, search pages >= --page-pace, and a bot-block
or rate limit seen by any agent backs every agent off (60 s doubling to 30 min, forgotten after 30
calm minutes; a clean download steps it back down). A search, metadata or source cache hit returns
before the pacer is asked, so a warm cache never waits and never touches the network. --no-global-pace
falls back to a per-process gap (tests only).

Cost: about 25-35 s of CPU per source (decode, text, faces, SigLIP) plus the paced downloads.
Measured on this 16-core PC with EDIT_THREADS=6: a 12-shot run takes 9-10 min cold (Demon Slayer
source 550 s, Jujutsu Kaisen --with-audio 525 s, LE SSERAFIM dance 378 s) and 45-125 s once the
sources are cached. On a 2-core runner expect roughly 3x the CPU part.
$EDIT_THREADS caps torch threads; --analyse-secs trades coverage for time.

CI install (ubuntu-latest):
  pip install torch --index-url https://download.pytorch.org/whl/cpu
  pip install yt-dlp yt-dlp-ejs opencv-python-headless numpy transformers sentencepiece protobuf pillow
"""
import argparse
import concurrent.futures as cf_
import glob
import hashlib
import json
import math
import os
import queue
import random
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from collections import Counter

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, 'core', 'meme'))
import clip_finder as cf  # noqa: E402  (search, text rules, NudeNet, YuNet, text mask, helpers)
import align  # noqa: E402  (sound hits)
import clip_judge as cj  # noqa: E402  (SigLIP model id and safety prompts)
sys.path.insert(0, HERE)
import gpu as _gpu  # noqa: E402  (NVENC / CUDA decode when usable; no-op on CI)
try:
    import clip_taste  # noqa: E402  (reference exemplars + attractiveness head)
except Exception as _e:   # pragma: no cover - the library still works on its old ranking
    clip_taste = None
    print(f'[library] clip_taste unavailable: {_e!r}', file=sys.stderr)
try:
    import clip_rules  # noqa: E402  (owner's clip rules: subject, solo, mood, dialogue, motion)
except Exception as _e:   # pragma: no cover
    clip_rules = None
    print(f'[library] clip_rules unavailable: {_e!r}', file=sys.stderr)

try:    # our own worker threads do the parallelism; OpenCV's inner threads only fight torch's
    cv2.setNumThreads(1)
except Exception:
    pass

VERSION = 13                     # analysis cache version: bump when the analysis changes
T0 = time.time()
FPS_A = 10                      # analysis frame rate
KEEP = 5                        # every 5th analysis frame (2 fps) is kept for the models
LONG = 640                      # decoded frame long side
GW, GH = 16, 9                  # motion-area grid (landscape; swapped for vertical sources)
WHOLE_MAX = 300                 # download whole videos up to this long, else a section
SECTION = 75                    # section length for very large videos
ANA_MAX = 120                   # seconds analysed per source
MAX_SRC_DUR = 1800
SIGLIP_PER_SOURCE = 10          # candidates per source that get the text / face / SigLIP checks
TAGS = ('dance', 'fight', 'aura', 'cute', 'closeup', 'scenery', 'action', 'performance')
MODES = ('source', 'character', 'mood', 'dance')

MODEL_URLS = {
    'anime_face': ('anime_face_v14n.onnx', 10_000_000,
                   'https://huggingface.co/deepghs/anime_face_detection/resolve/main/face_detect_v1.4_n/model.onnx'),
    'text_det': ('text_det_cn_ppocrv3.onnx', 2_000_000,
                 'https://github.com/opencv/opencv_zoo/raw/main/models/text_detection_ppocr/'
                 'text_detection_cn_ppocrv3_2023may.onnx'),
    'vad': ('silero_vad.jit', 1_500_000,
            'https://github.com/snakers4/silero-vad/raw/master/src/silero_vad/data/silero_vad.jit'),
    'sface': ('face_recognition_sface_2021dec.onnx', 30_000_000,
              'https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/'
              'face_recognition_sface_2021dec.onnx'),
}

# ---------------------------------------------------------------- queries
# (template, kind of material). {s}: source / group, {c}: character, {a}: the character's anime or
# group, {q}: query words, {n}: a native-script alias.
QUERIES = {
    ('source', 'anime'): [
        ('{s} creditless opening', 'op'), ('{s} NCOP NCED 4k', 'op'), ('{s} 4k twixtor', 'twixtor'),
        ('{s} twixtor clips for edits', 'twixtor'), ('{s} raw scenes 4k', 'raw'), ('{s} scenepack 4k', 'raw'),
        ('{s} 4k scenes for edits', 'raw'), ('{s} creditless ending', 'op'), ('{s} best fight scene 4k 60fps', 'scene'),
        ('{s} iconic scenes 4k', 'raw'), ('{s} clips for edits 4k no cc', 'raw'),
    ],
    ('source', 'group'): [
        ('{s} official MV', 'mv'), ('{s} MV 4K', 'mv'), ('{s} official performance video 4k', 'perf'), ('{s} stage 4k 60fps', 'stage'), ('{s} comeback stage', 'stage'),
        ('{s} performance video', 'perf'), ('{s} dance practice', 'practice'), ('{s} stage mix', 'stage'),
    ],
    ('character', 'anime'): [
        ('{c} 4k twixtor', 'twixtor'), ('{c} twixtor clips for edits', 'twixtor'), ('{c} scenepack 4k', 'raw'),
        ('{c} {a} 4k scenes', 'raw'), ('{c} raw scenes for edits', 'raw'), ('{c} clips for edits 4k', 'raw'),
        ('{c} {a} twixtor 60fps', 'twixtor'),
    ],
    ('character', 'group'): [
        ('{c} {a} fancam 4k', 'fancam'), ('{c} focus cam 4k', 'fancam'), ('{c} {a} official MV', 'mv'),
        ('{c} {a} stage close up 4k', 'stage'), ('{c} {a} performance', 'perf'),
    ],
    ('mood', 'anime'): [
        ('anime {q} scenes 4k', 'raw'), ('{q} anime twixtor', 'twixtor'), ('anime {q} scenepack for edits', 'raw'),
        ('{q} anime clips 4k 60fps', 'raw'), ('{q} anime scene 4k', 'scene'),
    ],
    ('mood', 'group'): [
        ('kpop {q} MV', 'mv'), ('kpop {q} stage 4k', 'stage'), ('kpop {q} performance video', 'perf'),
    ],
    ('dance', 'anime'): [
        ('{q} anime dance scene', 'dance'), ('{q} ending dance creditless', 'op'), ('{q} dance scene 4k 60fps', 'dance'),
        ('anime dance scene 4k {q}', 'dance'), ('{q} anime ending dance NCED', 'op'),
        ('{q} dance twixtor 4k', 'twixtor'), ('{q} dance scene raw 4k for edits', 'raw'),
    ],
    # no anime named: the cute / fun / striking dances of ANY anime that edits reuse (owner, 2026-09-26:
    # the Chika dance, Spy x Family dance scenes, dancing endings). One generic search, then the
    # named dances of ANIME_DANCES (shuffled per day) so a run does not land on the same three.
    ('dance', 'anime_any'): [
        ('chika dance creditless 4k', 'op'), ('spy x family ending dance creditless 4k', 'op'),
    ],
    ('dance', 'group'): [
        ('{q} dance practice', 'practice'), ('{q} dance practice 4k', 'practice'), ('{q} choreography video', 'practice'),
        ('{q} performance 4k 60fps', 'perf'), ('{q} dance break stage', 'stage'), ('{q} relay dance', 'practice'),
    ],
}
# The dances of ANY anime that read as cute, fun or striking and that edits reuse (owner, 2026-09-26),
# for `--mode dance --kind anime` with no query: (search, kind of material). Creditless OP / ED
# dance sequences first, then the in-episode dance scenes. Shows clip_finder flags as fan-service
# risks (Dandadan, Kill la Kill, Konosuba, My Dress-Up Darling, Chainsaw Man) are left out on purpose,
# and nothing body-focused: the owner's rule is attractiveness framing for adult characters only.
ANIME_DANCES = [
    ('kaguya-sama chika dance creditless 4k', 'op'), ('kaguya-sama love is war ending dance nced 4k', 'op'),
    ('spy x family ending dance creditless 4k', 'op'), ('spy x family anya dance scene 4k', 'dance'),
    ('oshi no ko idol opening creditless 4k', 'op'), ('oshi no ko b komachi dance scene 4k', 'dance'),
    ('lycoris recoil ending dance creditless 4k', 'op'), ('ya boy kongming chikichiki banban opening creditless 4k', 'op'),
    ('haruhi suzumiya hare hare yukai dance creditless', 'op'), ('lucky star opening dance creditless 4k', 'op'),
    ('death parade opening dance creditless 4k', 'op'), ('skip and loafer opening dance creditless 4k', 'op'),
    ('wotakoi opening dance creditless 4k', 'op'), ('mashle bling bang bang born opening creditless 4k', 'op'),
    ('zombie land saga dance scene 4k', 'dance'), ('zombie land saga opening creditless 4k', 'op'),
    ('love live dance performance 4k', 'dance'), ('idolmaster dance performance 4k 60fps', 'dance'),
    ('uma musume winning live dance 4k', 'dance'), ('kpop demon hunters golden dance scene 4k', 'dance'),
    ('kpop demon hunters soda pop dance scene 4k', 'dance'), ('nichijou opening dance creditless', 'op'),
    ('toradora ending dance creditless', 'op'), ('cells at work ending dance creditless 4k', 'op'),
    ('jashin-chan dropkick opening dance creditless', 'op'), ('himouto umaru-chan opening dance creditless', 'op'),
    ('blend s opening dance creditless 4k', 'op'), ('precure ending dance creditless 4k', 'op'),
    ('aikatsu dance performance 4k', 'dance'), ('hanayamata yosakoi dance scene 4k', 'dance'),
    ('dance dance danseur ballet scene 4k', 'dance'), ('welcome to the ballroom dance scene 4k', 'dance'),
    ('shine post dance performance 4k', 'dance'), ('idoly pride dance performance 4k', 'dance'),
    ('wake up girls dance performance 4k', 'dance'), ('bocchi the rock ending dance creditless 4k', 'op'),
]
# the shows of ANIME_DANCES: with no anime named, a candidate video must name one of them (the
# generic searches otherwise fill the pool with popular non-dance endings: JJK ED 4, Magi, Bucky)
ANIME_DANCE_SHOW_RE = cf._word_re([
    'kaguya sama', 'kaguya-sama', 'chika', 'spy x family', 'spy family', 'anya', 'oshi no ko', 'b komachi',
    'lycoris recoil', 'kongming', 'paripi koumei', 'haruhi', 'hare hare yukai', 'lucky star', 'death parade',
    'skip and loafer', 'skip to loafer', 'wotakoi', 'wotaku ni koi', 'mashle', 'bling bang bang born',
    'zombie land saga', 'zombieland saga', 'love live', 'idolmaster', 'idolm@ster', 'uma musume', 'umamusume',
    'kpop demon hunters', 'k-pop demon hunters', 'huntrix', 'huntr/x', 'nichijou', 'toradora', 'cells at work',
    'hataraku saibou', 'jashin chan', 'jashin-chan', 'dropkick on my devil', 'umaru', 'blend s', 'precure',
    'pretty cure', 'aikatsu', 'hanayamata', 'dance dance danseur', 'welcome to the ballroom', 'ballroom e youkoso',
    'shine post', 'idoly pride', 'wake up girls', 'bocchi the rock',
])
NATIVE_Q = {'source': {'ja': '{n} ノンクレジット', 'ko': '{n} 무대 4K', 'zh': '{n} 4K 混剪'},
            'dance': {'ja': '{n} ダンス ノンクレジット', 'ko': '{n} 안무 연습', 'zh': '{n} 舞蹈'},
            'character': {'ja': '{n} 4K', 'ko': '{n} 직캠 4K', 'zh': '{n} 4K'}}

# ---------------------------------------------------------------- title rules
CREDITLESS_RE = cf._word_re(['creditless', 'ncop', 'nced', 'non credit', 'non-credit', 'no credit', 'no credits',
                             'textless', 'clean opening', 'clean ending', 'no text', 'no cc', 'ノンクレジット', '논크레딧'])
TWIXTOR_RE = cf._word_re(['twixtor', 'twixter'])
RAW_RE = cf._word_re(['raw', 'raw scenes', 'raw clips', 'scenepack', 'scene pack', 'scenepacks', 'for edits', 'for edit',
                      'for editing', 'clips for', '4k scenes', 'all scenes', 'no watermark'])
QUALITY_RE = cf._word_re(['4k', '8k', '2160p', '1440p', '1080p', '60fps', '4k60', '4k 60fps', 'uhd', 'hd', 'bluray',
                          'blu ray', 'bd'])
MV_RE = cf._word_re(['official mv', 'official music video', 'music video', 'official video', 'm v', 'mv', 'official m v'])
STAGE_RE = cf._word_re(['stage', 'performance', 'live stage', 'dance practice', 'choreography', 'focus cam', 'fancam',
                        'comeback', 'music bank', 'inkigayo', 'm countdown', 'show champion', 'the show',
                        'studio choom', 'it s live', 'its live', '무대', '안무', '직캠', '교차편집'])
OP_RE = cf.OP_RE
DANCE_TITLE_RE = cf._word_re(cf.VIBE_WORDS['dance'] + ['ending', 'ed', 'nced'])
# someone else's edit (effects, text, their own music) or a fan re-cut
EDIT_ALLOWED_RE = re.compile(r'(?:for|to)\s+edit(?:s|ing)?|edit(?:ing)?\s+(?:clips|scenes|pack|material|use)'
                             r'|(?:clips|scenes|scenepack|scene pack|twixtor)\s+(?:for|to)\s+edit(?:s|ing)?', re.I)
OTHER_EDIT_RE = cf._word_re(['edit', 'edits', 'amv', 'mmv', 'gmv', 'velocity', 'capcut', 'alight motion',
                             'after effects', 'flow edit', 'tiktok edit', 'badass edit', 'sad edit', 'quickie',
                             'hardstyle', 'phonk', 'slowed', 'reverb', 'sped up', 'nightcore', 'meme', 'memes',
                             'shitpost', 'compilation of edits', 'audio edit', 'mashup', 'mad', 'fmv',
                             'manga animation', 'manga edit', 'webtoon', 'manhwa', 'ai animation', 'ai generated',
                             'reaction', 'reacts', 'review', 'explained', 'recap', 'podcast', 'interview',
                             'behind the scenes', 'making of', 'vlog', 'unboxing', 'dub', 'dubbed', 'fandub',
                             'eng sub', 'engsub', 'english sub'])
# dialogue-heavy material
TALKY_RE = cf._word_re(['funny moments', 'funny scenes', 'moments', 'episode', 'full episode', 'ep', 'eps', 'dialogue',
                        'conversation', 'talks', 'talking', 'interview', 'variety', 'radio', 'asmr', 'q a', 'live stream',
                        'vlive', 'weverse live', 'behind', 'bts film', 'teaser talk'])
KID_RE = cf._word_re(['kid', 'kids', 'child', 'children', 'baby', 'student', 'students', 'teen', 'teens', 'school girl',
                      'schoolgirl', '儿童', '小孩', '小学生', '中学生', '子供', '아기', '어린이', '초등학생'])
# clip_finder.SUBS_RE was written as re.compile('\b(sub|...)\b') in a string whose \b became literal
# backspace characters, so it only ever matches its CJK alternatives: a title ending "| Subtitles"
# walks straight through it. This is our own copy with real word edges. "cc" is deliberately left
# out: in edit-pack titles it means colour correction ("4k cc", "No CC"), not closed captions.
SUBS_TITLE_RE = cf._word_re(['sub', 'subs', 'subbed', 'subtitle', 'subtitles', 'subtitulos', 'subtitulado',
                             'eng sub', 'engsub', 'english sub', 'hardsub', 'hardsubbed', 'soft sub',
                             'closed caption', 'closed captions', 'legendado', 'karaoke',
                             '字幕', '자막', '자막포함'])
# lyrics / romaji uploads usually put the words in the description, but sometimes burn them in:
# a penalty, not a rejection (the frame checks decide).
LYRICS_RE = cf._word_re(['lyrics', 'lyric', 'romaji', 'romanized', 'translation', '歌詞', '가사'])
# "Creditless No Subtitle" advertises the opposite, so the negated forms are stripped out first
NO_SUBS_RE = re.compile(r'(?<![a-z0-9])(?:no|non|without|zero|free\s+of|0)[\s_\-]*'
                        r'(?:hard|soft|eng(?:lish)?)?[\s_\-]*'
                        r'(?:subs?|subbed|subtitles?|captions?|cc|字幕|자막)(?![a-z0-9])', re.I)


def _load_assets():
    base = os.path.join(ROOT, 'core', 'quiz', 'assets')
    out = {}
    for name, key in (('anime-characters.json', 'characters'), ('anime-list.json', 'anime'), ('kpop-idols.json', 'idols')):
        try:
            with open(os.path.join(base, name), encoding='utf-8') as f:
                d = json.load(f)
            out[key] = d.get(key) if isinstance(d, dict) else d
        except (OSError, ValueError):
            out[key] = []
    return out


ASSETS = _load_assets()


def _names(s):
    return cf._snorm(s)


NSFW_SHOWS = sorted({_names(t) for a in ASSETS['anime'] if a.get('nsfw') for t in (a.get('title'), a.get('romaji')) if t}
                    - {''}, key=len, reverse=True)
NSFW_CHARS_FULL = sorted({_names(c['name']) for c in ASSETS['characters'] if c.get('nsfw') and ' ' in c['name'].strip()},
                         key=len, reverse=True)
NSFW_SHOW_RE = cf._word_re(NSFW_SHOWS) if NSFW_SHOWS else None
# Owner 2026-09-29: fan-service shows are allowed for edits (Darling in the Franxx, My Dress-Up Darling, ...);
# only hentai / explicit sources are refused. The quiz 'nsfw' flag (Ecchi genre) no longer blocks a source.
# The shot-level SigLIP safety gate (nudity / underwear / suggestive, strict for minors) still applies.
ALLOW_FANSERVICE = True
# Owner 2026-10-12: blood / gore is ALLOWED in the sound edit and the cinematic slow-mo (anime action; fight scenes
# with blood are often the best sound sources). The gore score is still measured and logged; it only stops being a
# reject for these presets (main() turns it on from --preset; env EDIT_ALLOW_GORE=1 forces it). Nudity / sexual,
# minors, hentai sources and subtitles stay hard gates.
GORE_OK_PRESETS = {'sound_hit_sync', 'cinematic_slowmo'}
ALLOW_GORE = os.environ.get('EDIT_ALLOW_GORE', '') == '1'
EXPLICIT_RE = re.compile(r'hentai|\bporn|\bnude\b|\bnudity\b|uncensored|\br-?18\b|(?<![0-9])18 ?\+|onlyfans|\bjav\b|\bxxx\b|\bnsfw\b', re.I)
NSFW_CHAR_RE = cf._word_re(NSFW_CHARS_FULL) if NSFW_CHARS_FULL else None

# ---------------------------------------------------------------- SigLIP prompts
# classes: name -> prompts. GOOD classes double as the tags; BAD classes reject.
PROMPTS = {
    'anime': {
        'good': {
            'fight': ['an epic anime fight scene', 'an anime character throwing a powerful punch',
                      'an anime sword fight with sparks'],
            'aura': ['an anime character with a glowing aura of energy', 'a cool anime character with glowing eyes',
                     'an intense anime character with a powerful stare'],
            'action': ['a fast anime action scene with an explosion', 'an anime character running at high speed',
                       'an anime character leaping through the air'],
            'dance': ['an anime character dancing', 'anime characters doing a dance move', 'an anime idol dancing on stage'],
            'cute': ['a cute anime character smiling happily', 'an adorable anime character making a cute pose',
                     'a chibi anime character'],
            'closeup': ["a dramatic close-up of an anime character's face", 'an extreme close-up of anime eyes'],
            'scenery': ['a beautiful anime landscape with dramatic lighting', 'an anime city at night with lights',
                        'an anime sky with clouds at sunset'],
            'performance': ['an anime idol singing on a stage with lights', 'an anime concert performance'],
            'emotion': ['an anime character with hair blowing in the wind', 'an anime character crying with tears',
                        'an anime character looking back over the shoulder'],
        },
        'bad': {
            'talk': ['two anime characters talking to each other', 'an anime character talking in a calm dialogue scene',
                     'anime characters sitting at a table talking', 'a close-up of an anime character speaking with a neutral face'],
            'text': ['an anime frame with subtitles at the bottom', 'ending credits text over an anime scene',
                     'a title card with big text', 'a screen full of text'],
            'junk': ['a black screen', 'a blurry out of focus frame', 'a manga page in black and white',
                     'a video game screenshot', 'a youtube intro with a subscribe button', 'a low quality compressed frame'],
            'offkind': ['a live-action video of real people', 'a photo of a real person'],
        },
        'pretty': ['a stunning, beautifully animated anime frame with rich colors and detail',
                   'a gorgeous cinematic anime shot'],
        'ugly': ['a dull, plain, low quality anime frame', 'a boring flat frame with nothing happening'],
        'kind_yes': ['a frame from a Japanese anime', 'a 2D animated cartoon frame', 'hand-drawn anime characters'],
        'kind_no': ['a live-action video of real people', 'a photo of real people', 'a person performing on a TV stage'],
        'framing': {'face': ["a close-up of a character's face"], 'medium': ['a character shown from the waist up'],
                    'wide': ['a wide shot with small figures', 'a full body shot of a character standing']},
    },
    'group': {
        'good': {
            'dance': ['a K-pop idol group dancing on stage', 'a dancer doing a powerful dance move',
                      'a dance practice video in a studio'],
            'performance': ['a K-pop idol performing on a stage with colorful lights',
                            'a singer performing on stage with a microphone'],
            'closeup': ["a close-up of a K-pop idol's face", 'a beautiful close-up portrait of a singer'],
            'aura': ['a K-pop idol with a confident stare at the camera', 'a stylish music video shot with dramatic lighting'],
            'cute': ['a K-pop idol smiling cutely', 'an idol making a heart sign with the hands'],
            'scenery': ['a cinematic music video set with colorful lights', 'a beautiful landscape in a music video'],
            'action': ['a dynamic music video scene with fast motion', 'fireworks and confetti on a stage'],
            'fight': ['a martial arts fight scene'],
            'emotion': ['a singer with hair blowing in the wind', 'an idol turning around and looking back'],
        },
        'bad': {
            'talk': ['a person being interviewed', 'people talking on a TV variety show',
                     'a person talking to the camera in a vlog', 'people sitting and chatting'],
            'text': ['a TV screen with captions and subtitles', 'a news broadcast with a lower third caption',
                     'a title card with big text', 'a screen full of text'],
            'junk': ['a black screen', 'a blurry out of focus frame', 'a low quality compressed frame',
                     'an audience crowd holding phones', 'a still photo of a poster'],
            'offkind': ['an anime cartoon frame', 'a 2D animated cartoon'],
        },
        'pretty': ['a stunning cinematic high quality music video frame', 'a beautiful sharp stage photo with vivid lights'],
        'ugly': ['a dull, plain, low quality video frame', 'a boring flat frame with nothing happening'],
        'kind_yes': ['a live-action video of real people', 'a photo of real people', 'a person performing on a TV stage'],
        'kind_no': ['a frame from a Japanese anime', 'a 2D animated cartoon frame'],
        'framing': {'face': ["a close-up of a person's face"], 'medium': ['a person shown from the waist up'],
                    'wide': ['a wide shot of a stage with small figures', 'a full body shot of a dancer']},
    },
}
UNSAFE_EXTRA = {'anime': ['an anime girl bathing in a lake', 'a naked anime character seen from behind',
                          'an anime character in a swimsuit', 'an anime character bending over in a short skirt',
                          'an anime girl in underwear', 'a sexy anime pose showing cleavage'],
                'group': ['a woman in a bikini', 'a close-up of a body in a tight outfit', 'a suggestive body shot']}
SAFE_EXTRA = {'anime': ['an anime character in a school uniform', 'an anime character in a coat',
                        'an anime character in armor or battle clothes', 'an anime character in casual clothes',
                        "a close-up of an anime character's face", "an extreme close-up of an anime character's eyes",
                        "an anime character's hand", 'an anime character with glowing energy'],
              'group': ['a K-pop idol in a stage outfit', 'a person in a jacket', 'a dancer in training clothes',
                        "a close-up of a singer's face", 'a dancer in a hoodie and pants']}
CLEAN_EXTRA = ['a scene lit with red light', 'a dark red glowing background', 'red flowers and petals',
               'fire and flames', 'a red sunset sky', 'a character in red clothes']
TAG_CLASSES = ('fight', 'aura', 'action', 'dance', 'cute', 'closeup', 'scenery', 'performance')

# ---------------------------------------------------------------- what the references cut to
# The kinds of moment each edit type is built from, taken from core/edit/refs/*.json
# (style.clip_selection) and the reports next to them. `moment` prompts are scored against `plain`
# and raise a shot's score; `framing` is the shot-scale mix the references measured, used both as a
# mild per-shot preference and as a quota in select().
SCENES = {
    ('anime', 'source'): {          # refs/general.json content_mix: faces .45 motion .25 action .2 scenery .1
        'why': 'general.json clip_selection.content_mix + shot_scale_mix',
        'moment': ['a close-up of an anime character with an intense expressive face',
                   'an anime character smiling or grinning', 'an anime character with tears in the eyes',
                   'an anime character glaring with a fierce look',
                   'an anime character with hair and clothes blowing in the wind',
                   'an anime character turning around and looking back over the shoulder',
                   'an anime character running or leaping through the air',
                   'an epic anime fight with fire, lightning or sparks',
                   'an anime character powering up with glowing energy',
                   'a beautiful anime sky, city or rain with dramatic light'],
        'framing': {'closeup': 0.55, 'medium': 0.30, 'wide': 0.15},
    },
    ('anime', 'character'): {       # refs/character.json clip_selection.moments_ranked + framing_targets
        'why': 'character.json clip_selection.moments_ranked + framing_targets',
        'moment': ['an anime character looking straight at the camera, eye contact',
                   'an anime character smiling or laughing',
                   'an anime character with hair and clothes moving in the wind',
                   'an anime character turning and looking back over the shoulder',
                   'an anime character walking toward the camera',
                   'an anime character winking or making a peace sign',
                   'an anime character with a confident, cool stare',
                   'an anime character powering up with glowing eyes',
                   'an anime character reaching out a hand toward the camera',
                   'an anime character running, jumping or spinning',
                   'an extreme close-up of anime eyes'],
        'framing': {'closeup': 0.52, 'medium': 0.28, 'wide': 0.12, 'rest': 0.08},
    },
    ('anime', 'dance'): {           # refs/dance.json clip_selection: dancing only, full body / waist-up
        'why': 'dance.json clip_selection.what + siglip.dance',
        'moment': ['an anime character dancing', 'an anime girl doing a dance move', 'an anime idol dance',
                   'a full body shot of an anime character dancing',
                   'an anime character dancing in front of a bright clean background'],
        'framing': {'closeup': 0.15, 'medium': 0.45, 'wide': 0.40},
    },
    ('anime', 'mood'): {
        'why': 'general.json content_mix, weighted toward scenery / emotion for mood queries',
        'moment': ['an anime character with an emotional expression',
                   'an anime character with hair and clothes blowing in the wind',
                   'a beautiful anime landscape with dramatic light',
                   'an anime city or sky at night', 'an anime scene in the rain',
                   'an anime character standing alone in a wide shot'],
        'framing': {'closeup': 0.40, 'medium': 0.30, 'wide': 0.30},
    },
    ('anime', 'sound'): {           # refs/sound.json clip_selection.must: ~80 % hands / objects / feet
        'why': 'sound.json clip_selection.must (80 % close-ups of hands, objects or feet)',
        'moment': ['a close-up of a fist hitting something with impact',
                   'a close-up of two hands clapping', 'a close-up of a sword clashing with sparks',
                   'a close-up of a gun being fired', 'a close-up of a foot stomping the ground',
                   'a close-up of a hand snapping its fingers',
                   'a close-up of an object slammed down on a table',
                   'a close-up of a match being struck', 'a close-up of a door slamming shut',
                   'a close-up of hands gripping a sword handle'],
        'framing': {'closeup': 0.80, 'medium': 0.15, 'wide': 0.05},
    },
    ('group', 'source'): {          # refs/kpop.json clip_selection.content + shot_scale_mix_beat_section
        'why': 'kpop.json clip_selection.content + shot_scale_mix_beat_section',
        'moment': ['a K-pop idol posing and looking straight into the camera',
                   'an idol flipping her hair', 'an idol winking or smirking at the camera',
                   'an idol reaching a hand toward the camera',
                   'a close-up of an idol singing on stage under coloured lights',
                   'an idol doing a sharp dance move on stage',
                   'an idol turning her head toward the camera'],
        'framing': {'closeup': 0.60, 'medium': 0.30, 'wide': 0.10},
    },
    ('group', 'dance'): {           # refs/kpop.json: dance practices, performance films, stages
        'why': 'kpop.json clip_selection.sources (dance practice / performance film / stage)',
        'moment': ['a K-pop idol group dancing in a practice room',
                   'a dancer doing a powerful dance move on stage',
                   'a full body shot of idols dancing in formation',
                   'a dance break with a sharp pose', 'an idol dancing alone in front of the camera'],
        'framing': {'closeup': 0.15, 'medium': 0.40, 'wide': 0.45},
    },
    ('group', 'mood'): {
        'why': 'kpop.json clip_selection.content, weighted toward music-video mood',
        'moment': ['a cinematic K-pop music video shot with dramatic lighting',
                   'an idol posing in a colourful music video set',
                   'a close-up of an idol with an emotional expression',
                   'a beautiful wide shot in a music video'],
        'framing': {'closeup': 0.45, 'medium': 0.35, 'wide': 0.20},
    },
}
SCENES[('group', 'character')] = dict(SCENES[('group', 'source')], framing={'closeup': 0.60, 'medium': 0.30, 'wide': 0.10},
                                      why='kpop.json clip_selection.subject (one idol per shot) + content')
SCENES[('group', 'sound')] = dict(SCENES[('group', 'source')],
                                  why='sound.json is anime-only; K-pop falls back to the stage content mix')
PLAIN = {'anime': ['an ordinary anime frame with nothing much happening', 'a plain flat anime shot',
                   'an anime background with nobody doing anything', 'a boring anime frame'],
         'group': ['an ordinary video frame with nothing much happening', 'a plain flat video shot',
                   'an empty stage with nobody doing anything', 'a boring video frame']}


# ---------------------------------------------------------------- utilities

def log(msg):
    print(f'[library {time.time() - T0:5.1f}s] {msg}', file=sys.stderr, flush=True)


STATS = {'searched': 0, 'candidates': 0, 'probed': 0, 'probe_ok': 0, 'downloaded': 0, 'analysed': 0,
         'cached_sources': 0, 'windows': 0, 'siglip_images': 0, 'kept': 0,
         'rejected': Counter(), 'shot_rejected': Counter()}
_slock = threading.Lock()


def bump(key, reason=None, n=1):
    with _slock:
        if reason is None:
            STATS[key] += n
        else:
            STATS[key][reason] += n


def sha(s):
    return hashlib.sha1(s.encode('utf-8')).hexdigest()[:16]


def ffprobe(path):
    """(width, height, fps, duration, has_audio) or None."""
    r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries',
                        'stream=codec_type,width,height,avg_frame_rate,r_frame_rate:format=duration',
                        '-of', 'json', path], capture_output=True, text=True, timeout=60)
    try:
        d = json.loads(r.stdout or '{}')
    except ValueError:
        return None
    v = next((s for s in d.get('streams', []) if s.get('codec_type') == 'video'), None)
    if not v:
        return None

    def rate(x):
        try:
            n, dd = str(x).split('/')
            return float(n) / float(dd) if float(dd) else 0.0
        except (ValueError, ZeroDivisionError):
            return 0.0
    fps = rate(v.get('avg_frame_rate')) or rate(v.get('r_frame_rate')) or 30.0
    if fps > 120:
        fps = rate(v.get('r_frame_rate')) or 30.0
    audio = any(s.get('codec_type') == 'audio' for s in d.get('streams', []))
    return int(v['width']), int(v['height']), fps, float(d.get('format', {}).get('duration') or 0), audio


def model_path(key):
    name, min_size, url = MODEL_URLS[key]
    cache = os.environ.get('CLIP_FINDER_CACHE') or os.path.join(os.path.expanduser('~'), '.cache', 'clip_finder')
    path = os.path.join(cache, name)
    try:
        if os.path.exists(path) and os.path.getsize(path) >= min_size:
            return path
        os.makedirs(cache, exist_ok=True)
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        data = urllib.request.urlopen(req, timeout=90).read()
        if len(data) < min_size:
            raise ValueError(f'short download ({len(data)} bytes)')
        with open(path + '.tmp', 'wb') as f:
            f.write(data)
        os.replace(path + '.tmp', path)
        return path
    except Exception as e:
        log(f'model {key} unavailable: {e}')
        return None


class Cache:
    def __init__(self, root, max_mb):
        self.root, self.max_mb = root, max_mb
        for sub in ('search', 'info', 'src', 'ana', 'refs', 'emb'):
            os.makedirs(os.path.join(root, sub), exist_ok=True)

    def path(self, sub, name):
        return os.path.join(self.root, sub, name)

    def get(self, sub, key, ttl_days=None):
        p = self.path(sub, key + '.json')
        if not os.path.exists(p):
            return None
        if ttl_days is not None and time.time() - os.path.getmtime(p) > ttl_days * 86400:
            return None
        try:
            with open(p, encoding='utf-8') as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def put(self, sub, key, obj):
        p = self.path(sub, key + '.json')
        tmp = p + f'.{os.getpid()}.{threading.get_ident()}.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(obj, f, ensure_ascii=False)
        os.replace(tmp, p)

    def prune(self, keep=()):
        """Delete the least recently used source files beyond max_mb (never the ones in keep)."""
        files = [p for p in glob.glob(self.path('src', '*')) if os.path.isfile(p) and not p.endswith('.json')]
        files.sort(key=lambda p: os.path.getatime(p))
        total = sum(os.path.getsize(p) for p in files)
        keep = {os.path.abspath(k) for k in keep}
        for p in files:
            if total <= self.max_mb * 1e6:
                break
            if os.path.abspath(p) in keep:
                continue
            total -= os.path.getsize(p)
            try:
                os.remove(p)
                vid = os.path.basename(p).split('__')[0]
                for a in glob.glob(self.path('ana', f'{vid}__*')):
                    os.remove(a)
            except OSError:
                pass


class Pacer:
    """Minimum gaps between YouTube requests of a kind (bursts get this PC bot-blocked)."""

    def __init__(self, gaps):
        self.gaps, self.last, self.lock = gaps, {}, threading.Lock()

    def wait(self, kind):
        while True:
            with self.lock:
                now = time.time()
                due = self.last.get(kind, 0) + self.gaps.get(kind, 0)
                if now >= due:
                    self.last[kind] = now
                    return
            time.sleep(min(1.0, due - now))

    def penalise(self, err=''):
        return 0.0

    def reward(self):
        pass


# ---------------------------------------------------------------- global (cross-process) pacing

NET_LOCK = 'net.lock'                # the mutex every agent's library run takes before a yt-dlp call
NET_STATE = 'net-pace.json'          # {"last": {kind: epoch}, "block_until", "block_level", ...}
BLOCK_MAX = 1800.0                   # longest shared back-off
# yt-dlp's words for "YouTube is refusing this machine"
BOTBLOCK_RE = re.compile(r"confirm(?:\s+that)?\s+you(?:'re| are)?\s+not\s+a\s+bot|sign in to confirm"
                         r"|http error 429|too many requests|rate.?limit|temporarily blocked"
                         r"|blocked from|unable to download api page|player response|failed to extract any player"
                         r"|not a bot|use --cookies|requires (?:login|authentication)", re.I)


class FileLock:
    """A cross-process mutex on a lock file (works the same on Windows and POSIX). A lock left
    behind by a crashed agent is stolen once it is `stale` seconds old."""

    def __init__(self, path, stale=90.0):
        self.path, self.stale, self.fd, self.ok = path, stale, None, False

    def acquire(self, timeout=30.0):
        end = time.time() + timeout
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
                try:
                    os.write(fd, f'{os.getpid()} {time.time():.0f}'.encode())
                except OSError:
                    pass
                self.fd, self.ok = fd, True
                return True
            except FileExistsError:
                try:
                    age = time.time() - os.path.getmtime(self.path)
                except OSError:
                    age = 0.0
                if age > self.stale:
                    try:
                        os.remove(self.path)
                        continue
                    except OSError:
                        pass
            except OSError:
                pass
            if time.time() >= end:
                self.ok = False
                return False
            time.sleep(0.05 + random.random() * 0.15)

    def release(self):
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None
        self.ok = False
        try:
            os.remove(self.path)
        except OSError:
            pass

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()
        return False


class GlobalPacer:
    """Pacing shared by EVERY process on this PC, not just this one.

    Several agents call the library at the same time, and YouTube bot-blocks this machine after a
    burst from any of them. So the cadence lives in the cache directory: `net.lock` serialises the
    decision and `net-pace.json` holds the last request time per kind plus a back-off that a
    bot-block seen by ANY agent turns on for ALL of them (60 s doubling to 30 min, forgotten after
    30 calm minutes).

    Only requests that really go to the network come here: a search, metadata or source-file cache
    hit returns before `wait()` is called, so a warm cache runs at full speed and never waits.
    """

    def __init__(self, gaps, root, name='library'):
        self.gaps, self.root, self.name = gaps, root, name
        self.local = Pacer(gaps)                 # fallback, and the in-process queue
        self.turn = threading.Lock()             # one thread of this process asks at a time
        self.ok = True
        try:
            os.makedirs(root, exist_ok=True)
            self.state_path = os.path.join(root, NET_STATE)
            self.flock = FileLock(os.path.join(root, NET_LOCK))
        except OSError:
            self.ok = False

    def _read(self):
        try:
            with open(self.state_path, encoding='utf-8') as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write(self, d):
        tmp = f'{self.state_path}.{os.getpid()}.tmp'
        try:
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(d, f)
            os.replace(tmp, self.state_path)
        except OSError:
            try:
                os.remove(tmp)
            except OSError:
                pass

    def wait(self, kind):
        """Block until this process holds the next `kind` slot. Slots are handed out one at a time
        across all processes, so N agents share one cadence instead of N of them."""
        if not self.ok:
            return self.local.wait(kind)
        gap = float(self.gaps.get(kind, 0.0))
        said = False
        with self.turn:
            deadline = time.time() + BLOCK_MAX + 120
            while True:
                if not self.flock.acquire(timeout=20):
                    log('network pacing lock is busy; falling back to this process\'s own gap')
                    return self.local.wait(kind)
                try:
                    d = self._read()
                    now = time.time()
                    due = max(float(d.get('last', {}).get(kind) or 0.0) + gap,
                              float(d.get('block_until') or 0.0))
                    if now >= due or now >= deadline:
                        d.setdefault('last', {})[kind] = now
                        d['by'] = f'{self.name}:{os.getpid()}'
                        self._write(d)
                        self.local.last[kind] = now
                        return
                    wait = due - now
                finally:
                    self.flock.release()
                if wait > 20 and not said:
                    said = True
                    log(f'network paced: {wait:.0f}s to wait for the shared {kind} slot '
                        f'(another agent is downloading, or a bot-block back-off is running)')
                time.sleep(min(5.0, max(0.2, wait)))

    def penalise(self, err=''):
        """A bot-block or rate limit anywhere: every process backs off, doubling each time."""
        if not self.ok:
            return 0.0
        with self.flock as fl:
            if not fl.ok:
                return 0.0
            d = self._read()
            now = time.time()
            lvl = 0 if now - float(d.get('block_at') or 0.0) > BLOCK_MAX else int(d.get('block_level') or 0)
            lvl = min(6, lvl + 1)
            wait = min(BLOCK_MAX, 60.0 * (2 ** (lvl - 1)))
            d.update({'block_level': lvl, 'block_at': now,
                      'block_until': max(float(d.get('block_until') or 0.0), now + wait),
                      'block_err': str(err)[:200], 'block_by': f'{self.name}:{os.getpid()}'})
            self._write(d)
        log(f'bot-block / rate limit ({str(err)[:90]}): every agent backs off {wait / 60:.0f} min (level {lvl})')
        return wait

    def reward(self):
        """A clean download: step the shared back-off level back down."""
        if not self.ok:
            return
        with self.flock as fl:
            if not fl.ok:
                return
            d = self._read()
            if int(d.get('block_level') or 0) <= 0:
                return
            d['block_level'] = int(d['block_level']) - 1
            d['block_until'] = min(float(d.get('block_until') or 0.0), time.time())
            self._write(d)


# ---------------------------------------------------------------- YouTube

class Tube:
    def __init__(self, args, cache, pacer):
        self.yt = cf.YT(args.cookies, args.js_runtimes)
        self.cache, self.pacer = cache, pacer
        self.max_h = args.max_height
        self.empty = 0

    def search(self, query, n=25, sp=cf.SP_VIDEO):
        key = sha(f'{query}|{sp}|{n}')
        hit = self.cache.get('search', key, ttl_days=3)
        if hit is not None:                       # cache hit: no lock, no wait, no network
            return hit
        self.pacer.wait('page')
        res = cf.search(self.yt, query, 'edit', 'edit', n, sp)
        bump('searched')
        if res:
            self.empty = 0
            self.cache.put('search', key, res)
        else:
            # cf.search swallows the error; two empty result pages in a row is what a block looks like
            self.empty += 1
            if self.empty >= 2:
                self.pacer.penalise('two search pages came back empty')
                self.empty = 0
        return res

    def probe(self, vid):
        hit = self.cache.get('info', vid, ttl_days=14)
        if hit is not None:                       # cache hit: no lock, no wait, no network
            return hit, None
        self.pacer.wait('probe')
        rc, out, err = self.yt(['-J', '--no-playlist', '--skip-download', f'https://www.youtube.com/watch?v={vid}'], 60)
        bump('probed')
        if rc != 0:
            log(f'probe failed {vid}: {cf.err_line(err)}')
            if BOTBLOCK_RE.search(err or ''):
                self.pacer.penalise(cf.err_line(err))
            return None, 'unavailable'
        try:
            info = json.loads(out)
        except ValueError:
            return None, 'unavailable'
        slim = {k: v for k, v in info.items() if k not in ('thumbnails', 'automatic_captions', 'subtitles')}
        self.cache.put('info', vid, slim)
        return slim, None

    def download(self, info, section=None, timeout=150):
        """(path, offset) of the video (or section) at <= max_h, h264 preferred; cached."""
        vid = info['id']
        tag = f'{vid}__{section[0]:.0f}-{section[1]:.0f}' if section else f'{vid}__all'
        have = [p for p in glob.glob(self.cache.path('src', tag + '.*'))
                if not p.endswith('.json') and not p.endswith('.part') and os.path.getsize(p) > 100_000]
        if have:                                  # cache hit: no lock, no wait, no network
            os.utime(have[0])
            return have[0], (section[0] if section else 0.0)
        ij = self.cache.path('src', f'{vid}.info.json')
        with open(ij, 'w', encoding='utf-8') as f:
            json.dump(info, f)
        h = self.max_h
        args = ['-f', f'bv*[height<={h}]+ba/b[height<={h}]/bv*+ba/b', '-S', f'res:{h},fps,vcodec:h264,acodec:aac',
                '--merge-output-format', 'mkv', '--no-part', '-o', self.cache.path('src', tag + '.%(ext)s')]
        if section:
            args += ['--download-sections', f'*{section[0]:.1f}-{section[1]:.1f}']
        self.pacer.wait('download')
        end = time.time() + timeout
        err = ''
        for attempt, src in enumerate((['--load-info-json', ij], [f'https://www.youtube.com/watch?v={vid}'])):
            if attempt:                     # the retry is another yt-dlp call: it waits its own slot
                t_w = time.time()
                self.pacer.wait('download')
                end += time.time() - t_w
            rc, _, err = self.yt(src + args, end - time.time())
            files = [p for p in glob.glob(self.cache.path('src', tag + '.*'))
                     if not p.endswith('.json') and os.path.getsize(p) > 100_000]
            if rc == 0 and files:
                bump('downloaded')
                self.pacer.reward()
                mb = os.path.getsize(files[0]) / 1e6
                log(f'downloaded {vid}{" section" if section else ""}: {mb:.0f} MB in {timeout - (end - time.time()):.0f}s')
                return files[0], (section[0] if section else 0.0)
            for p in glob.glob(self.cache.path('src', tag + '.*')):
                try:
                    os.remove(p)
                except OSError:
                    pass
            if BOTBLOCK_RE.search(err or ''):
                self.pacer.penalise(cf.err_line(err))
                break                       # the fallback URL would only make the block worse
            if rc is None or end - time.time() < 20:
                break
        log(f'download failed {vid}: {cf.err_line(err)}')
        return None, 0.0


# ---------------------------------------------------------------- models

class AnimeFaces:
    """deepghs anime face detector (YOLOv8n): [(x, y, w, h, conf)] in 0-1 frame units."""
    S = 640

    def __init__(self, path):
        self.path, self.tls = path, threading.local()

    def __call__(self, img, conf=0.35):
        net = getattr(self.tls, 'net', None)
        if net is None:
            net = self.tls.net = cv2.dnn.readNetFromONNX(self.path)
        h, w = img.shape[:2]
        sc = self.S / max(h, w)
        nh, nw = max(1, int(h * sc)), max(1, int(w * sc))
        pad = np.full((self.S, self.S, 3), 114, np.uint8)
        pad[:nh, :nw] = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA)
        net.setInput(cv2.dnn.blobFromImage(pad, 1 / 255.0, (self.S, self.S), swapRB=True))
        o = np.squeeze(net.forward())
        if o.ndim != 2:
            return []
        if o.shape[0] < o.shape[1]:
            o = o.T
        keep = o[:, 4] >= conf
        if not keep.any():
            return []
        b, c = o[keep, :4], o[keep, 4]
        rects = [[float(x - bw / 2) / sc, float(y - bh / 2) / sc, float(bw) / sc, float(bh) / sc] for x, y, bw, bh in b]
        idx = np.array(cv2.dnn.NMSBoxes(rects, c.tolist(), conf, 0.45)).flatten()
        out = []
        for i in idx:
            x, y, bw, bh = rects[int(i)]
            out.append((max(0.0, x / w), max(0.0, y / h), min(1.0, bw / w), min(1.0, bh / h), float(c[int(i)])))
        return sorted(out, key=lambda f: -f[2] * f[3])


class TextDet:
    """PP-OCRv3 DB text detector: [(x0, y0, x1, y1, conf)] in 0-1 units. Detection only, no OCR."""

    def __init__(self, path):
        self.path, self.tls = path, threading.local()

    def __call__(self, img):
        h, w = img.shape[:2]
        if w >= h:
            tw, th = 640, max(32, int(round(640 * h / w / 32)) * 32)
        else:
            th, tw = 640, max(32, int(round(640 * w / h / 32)) * 32)
        models = getattr(self.tls, 'models', None)
        if models is None:
            models = self.tls.models = {}
        m = models.get((tw, th))
        if m is None:
            m = cv2.dnn.TextDetectionModel_DB(self.path)
            m.setBinaryThreshold(0.3)
            m.setPolygonThreshold(0.5)
            m.setMaxCandidates(200)
            m.setUnclipRatio(2.0)
            m.setInputParams(1.0 / 255.0, (tw, th), (122.67891434, 116.66876762, 104.00698793), True)
            models[(tw, th)] = m
        im = cv2.resize(img, (tw, th), interpolation=cv2.INTER_AREA)
        try:
            polys, confs = m.detect(im)
        except cv2.error:
            return []
        out = []
        for p, c in zip(polys, confs):
            x, y, bw, bh = cv2.boundingRect(np.array(p, np.int32))
            if bw < 4 or bh < 4:
                continue
            out.append((x / tw, y / th, (x + bw) / tw, (y + bh) / th, float(c)))
        return out


class VAD:
    """Silero VAD (torch JIT): speech probability per 32 ms at 16 kHz. Not thread-safe: locked."""

    def __init__(self, path):
        import torch
        self.torch = torch
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            self.m = torch.jit.load(path, map_location='cpu').eval()
        self.lock = threading.Lock()

    def probs(self, y):
        n = len(y) // 512
        if n < 4:
            return np.zeros(0)
        x = self.torch.from_numpy(np.ascontiguousarray(y[:n * 512], np.float32))
        with self.lock, self.torch.inference_mode():
            self.m.reset_states()
            try:
                p = self.m.audio_forward(x.unsqueeze(0), 16000)
                return np.asarray(p, np.float32).reshape(-1)[:n]
            except Exception:
                self.m.reset_states()
                return np.array([float(self.m(x[i * 512:(i + 1) * 512], 16000)) for i in range(n)], np.float32)


def speech_heuristic(y):
    """No VAD model: speech-likeness per 32 ms from a 3-8 Hz syllable modulation of the 300-3400 Hz
    band plus tonality. Rough; only a fallback."""
    n = len(y) // 512
    if n < 32:
        return np.zeros(max(0, n))
    fr = y[:n * 512].reshape(n, 512) * np.hanning(512)
    sp = np.abs(np.fft.rfft(fr, axis=1)) ** 2
    f = np.fft.rfftfreq(512, 1 / 16000)
    band = sp[:, (f >= 300) & (f <= 3400)]
    e = 10 * np.log10(band.sum(1) + 1e-10)
    flat = np.exp(np.log(band + 1e-12).mean(1)) / (band.mean(1) + 1e-12)
    out = np.zeros(n)
    k = 31      # ~1 s windows
    for i in range(0, n - k, 8):
        seg = e[i:i + k] - e[i:i + k].mean()
        spec = np.abs(np.fft.rfft(seg * np.hanning(k)))
        mf = np.fft.rfftfreq(k, 0.032)
        mod = spec[(mf >= 3) & (mf <= 8)].sum() / (spec[1:].sum() + 1e-9)
        tonal = float(np.median(flat[i:i + k]) < 0.08)
        loud = float(e[i:i + k].max() > np.median(e) + 3)
        out[i:i + k] = np.maximum(out[i:i + k], min(1.0, mod * 1.8) * tonal * loud)
    return out


class Siglip:
    def __init__(self, cache):
        import torch
        from transformers import AutoModel, AutoProcessor
        from transformers.utils import logging as tlog
        tlog.set_verbosity_error()
        self.torch = torch
        torch.set_num_threads(int(os.environ.get('EDIT_THREADS') or 0) or max(1, os.cpu_count() or 1))
        self.model = AutoModel.from_pretrained(cj.MODEL).eval()
        self.proc = AutoProcessor.from_pretrained(cj.MODEL)
        self.scale = float(self.model.logit_scale.exp())
        self.bias = float(self.model.logit_bias)
        # GPU when usable (gpu.torch_device: CUDA present, not CI, EDIT_GPU != 0): fp16 weights, bigger image batches;
        # CPU keeps fp32 and batch 16 exactly as before. Embeddings come back as fp32 numpy either way.
        self.dev = _gpu.torch_device()
        self.dtype = torch.float16 if self.dev == 'cuda' else torch.float32
        self.batch = 64 if self.dev == 'cuda' else 16
        if self.dev == 'cuda':
            try:
                self.model = self.model.to(self.dev, dtype=self.dtype)
            except Exception:  # noqa: BLE001  (out of memory / driver trouble -> stay on the CPU)
                self.model, self.dev, self.dtype, self.batch = self.model.float().cpu(), 'cpu', torch.float32, 16
        self.cache_file = cache.path('emb', f'text_{sha(cj.MODEL)}.npz')
        self.text_cache = {}
        try:
            d = np.load(self.cache_file, allow_pickle=False)
            self.text_cache = {k: d[k] for k in d.files}
        except Exception:
            pass
        self.lock = threading.Lock()

    def text(self, prompts):
        miss = [p for p in dict.fromkeys(prompts) if sha(p) not in self.text_cache]
        if miss:
            with self.torch.inference_mode():
                for i in range(0, len(miss), 32):
                    t = self.proc(text=miss[i:i + 32], padding='max_length', return_tensors='pt')
                    t = {k: v.to(self.dev) for k, v in t.items()}
                    e = cj._tensor(self.model.get_text_features(**t)).float()
                    e = (e / e.norm(dim=-1, keepdim=True)).cpu().numpy().astype(np.float32)
                    for p, v in zip(miss[i:i + 32], e):
                        self.text_cache[sha(p)] = v
            try:
                np.savez(self.cache_file, **self.text_cache)
            except OSError:
                pass
        return np.stack([self.text_cache[sha(p)] for p in prompts])

    def images(self, imgs):
        """BGR uint8 images -> (N, D) unit embeddings."""
        from PIL import Image
        if not imgs:
            return np.zeros((0, 768), np.float32)
        out = []
        with self.lock, self.torch.inference_mode():
            for i in range(0, len(imgs), self.batch):
                pil = [Image.fromarray(cv2.cvtColor(im, cv2.COLOR_BGR2RGB)) for im in imgs[i:i + self.batch]]
                x = self.proc(images=pil, return_tensors='pt')
                x = {k: (v.to(self.dev, dtype=self.dtype) if v.is_floating_point() else v.to(self.dev))
                     for k, v in x.items()}
                e = cj._tensor(self.model.get_image_features(**x)).float()
                out.append((e / e.norm(dim=-1, keepdim=True)).cpu().numpy().astype(np.float32))
        bump('siglip_images', n=len(imgs))
        return np.concatenate(out)

    def logits(self, img, txt):
        return img @ txt.T * self.scale + self.bias


class RealFaceID:
    """K-pop character mode: YuNet + SFace identity features."""

    def __init__(self, sface):
        self.rec = cv2.FaceRecognizerSF.create(sface, '')
        self.tls = threading.local()
        self.lock = threading.Lock()

    def feats(self, img):
        if 'face' not in cf.PATHS:
            return []
        h, w = img.shape[:2]
        det = getattr(self.tls, 'det', None)
        if det is None:
            det = self.tls.det = cv2.FaceDetectorYN.create(cf.PATHS['face'], '', (w, h), 0.7, 0.3, 20)
        det.setInputSize((w, h))
        _, found = det.detect(img)
        out = []
        for row in (found if found is not None else [])[:3]:
            with self.lock:
                f = self.rec.feature(self.rec.alignCrop(img, row)).reshape(-1)
            out.append(f / (np.linalg.norm(f) + 1e-9))
        return out


class Models:
    def __init__(self, need_sface):
        cf.load_models()
        p = model_path('anime_face')
        self.anime_faces = AnimeFaces(p) if p else None
        p = model_path('text_det')
        self.text = TextDet(p) if p and hasattr(cv2.dnn, 'TextDetectionModel_DB') else None
        if not self.text:
            log('WARNING: no DB text detector; falling back to the edge text mask only')
        self.vad = None
        p = model_path('vad')
        if p:
            try:
                self.vad = VAD(p)
            except Exception as e:
                log(f'VAD unavailable ({e!r}); speech heuristic instead')
        self.face_id = None
        if need_sface and hasattr(cv2, 'FaceRecognizerSF'):
            p = model_path('sface')
            if p:
                try:
                    self.face_id = RealFaceID(p)
                except Exception as e:
                    log(f'SFace unavailable: {e!r}')


# ---------------------------------------------------------------- target

def fetch_image(url, cache):
    """A reference image (URL or local path) as BGR, cached."""
    if os.path.exists(url):
        return cv2.imread(url, cv2.IMREAD_COLOR)
    p = cache.path('refs', sha(url) + '.img')
    if not os.path.exists(p):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (edit library)'})
            data = urllib.request.urlopen(req, timeout=30).read()
            with open(p, 'wb') as f:
                f.write(data)
        except Exception as e:
            log(f'reference image unavailable {url[:80]}: {e}')
            return None
    img = cv2.imdecode(np.fromfile(p, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        try:
            from PIL import Image
            img = cv2.cvtColor(np.array(Image.open(p).convert('RGB')), cv2.COLOR_RGB2BGR)
        except Exception:
            return None
    return img


def min_age(s):
    nums = [int(x) for x in re.findall(r'\d+', str(s or ''))]
    return min(nums) if nums else None


class Target:
    def __init__(self, a):
        self.mode, self.kind = a.mode, a.kind
        self.query = ' '.join((a.query or '').split())
        self.aliases = [x.strip() for x in (a.aliases or '').split(',') if x.strip()]
        self.theme = 'anime' if self.kind == 'anime' else 'kpop'
        self.src = None             # cf.Source the title must name (source / group / anime of a character)
        self.char_src = None        # cf.Source for the character's name
        self.char = None            # anime-characters.json / kpop-idols.json entry
        self.char_name = None
        self.parent = None          # the character's anime / group
        self.ref_urls = [u.strip() for u in (a.char_ref_images or '').split(',') if u.strip()]
        self.neg = []               # (name, image url) of other characters of the show / group
        self.refused = None
        self.adult = None
        q = _names(self.query)
        if not q:
            if self.mode != 'dance' and self.mode != 'mood':
                self.refused = 'empty query'
            return
        if self.mode in ('source', 'dance') and self._known_source(q):
            self.src = cf.Source(self.query, 'anime' if self.kind == 'anime' else 'group', self.aliases)
        elif self.mode == 'source':
            self.src = cf.Source(self.query, 'anime' if self.kind == 'anime' else 'group', self.aliases)
        if self.mode == 'character':
            self._character(q)
        if self.src is not None and self.src.refused:
            self.refused = f'unsafe source {self.query!r}'
        for name in [self.query] + self.aliases + ([self.parent] if self.parent else []):
            n = _names(name)
            if n and n in NSFW_SHOWS and not ALLOW_FANSERVICE:
                self.refused = f'{name!r} is flagged nsfw in anime-list.json'

    def _known_source(self, q):
        if self.aliases:
            return True
        if self.kind == 'anime':
            return any(q in (_names(x.get('title')), _names(x.get('romaji'))) for x in ASSETS['anime']) or bool(
                cf.THEME_RE['anime'].search(q))
        return any(q == _names(x.get('group')) for x in ASSETS['idols']) or bool(cf.THEME_RE['kpop'].search(q))

    def _character(self, q):
        parts = q.split()
        rev = ' '.join(reversed(parts))
        if self.kind == 'anime':
            pool = ASSETS['characters']
            hit = next((c for c in pool if _names(c['name']) in (q, rev)), None)
            if hit is None and self.aliases:
                al = {_names(x) for x in self.aliases}
                hit = next((c for c in pool if _names(c['name']) in al), None)
            self.char = hit
            self.char_name = hit['name'] if hit else self.query
            self.parent = hit['anime'] if hit else next(
                (x for x in self.aliases if any(_names(x) in (_names(a.get('title')), _names(a.get('romaji')))
                                                for a in ASSETS['anime'])), None)
            if hit:
                if hit.get('nsfw') and not ALLOW_FANSERVICE:
                    self.refused = f'{hit["name"]} is flagged nsfw in anime-characters.json'
                if hit.get('image'):
                    self.ref_urls.insert(0, hit['image'])
                self.neg = [(c['name'], c['image']) for c in pool if c['anime'] == hit['anime']
                            and c['name'] != hit['name'] and c.get('image')][:10]
                self.adult = (min_age(hit.get('age')) or 0) >= 18
            names = [self.char_name, ' '.join(reversed(self.char_name.split()))] + self.aliases
            names += [p for p in self.char_name.split() if len(p) >= 4]
            self.char_src = cf.Source(self.char_name, 'anime', names[1:])
            if self.parent:
                a = next((x for x in ASSETS['anime'] if _names(x.get('title')) == _names(self.parent)), None)
                al = [a['romaji']] if a and a.get('romaji') else []
                self.src = cf.Source(self.parent, 'anime', al)
        else:
            pool = ASSETS['idols']
            hit = next((c for c in pool if _names(c['name']) == q and (
                not self.aliases or any(_names(c.get('group')) == _names(x) for x in self.aliases))), None)
            self.char = hit
            self.char_name = hit['name'] if hit else self.query
            self.parent = hit['group'] if hit else (self.aliases[0] if self.aliases else None)
            if hit and hit.get('image'):
                self.ref_urls.insert(0, hit['image'])
                self.neg = [(c['name'], c['image']) for c in pool if c.get('group') == hit.get('group')
                            and c['name'] != hit['name'] and c.get('image')][:10]
            self.adult = None
            self.char_src = cf.Source(self.char_name, 'group', self.aliases)
            if self.parent:
                self.src = cf.Source(self.parent, 'group', [])

    def fill(self, tpl, native=None):
        s = tpl.format(s=self.query, c=self.char_name or self.query, a=self.parent or '', q=self.query, n=native or '')
        return ' '.join(s.split())

    def queries(self, rng, n):
        table = QUERIES[(self.mode, self.kind)]
        any_dance = self.mode == 'dance' and self.kind == 'anime' and not self.query
        if any_dance:
            table = QUERIES[('dance', 'anime_any')] + ANIME_DANCES
        out = []
        for tpl, what in table:
            if '{a}' in tpl and not self.parent:
                continue
            out.append((self.fill(tpl), what))
        head, rest = out[:2], out[2:]
        rng.shuffle(rest)
        out = head + rest
        nat = [x for x in [self.query] + self.aliases if not x.isascii() and cf._script(x)]
        if nat and self.mode in NATIVE_Q:
            tpl = NATIVE_Q[self.mode].get(cf._script(nat[0]))
            if tpl:
                out.insert(2, (self.fill(tpl, nat[0]), 'native'))
        seen, res = set(), []
        for q, w in out:
            if q.lower() not in seen:
                seen.add(q.lower())
                res.append((q, w))
        return res[:n]

    def level(self, meta):
        """How clearly the video is the target: 2 names it in the title, 1 weaker, 0 not."""
        if self.mode == 'character':
            lv = self.char_src.level(meta) if self.char_src else 0
            if lv:
                return lv + 0.5 if lv == 2 else lv
            return 0.5 if self.src and self.src.level(meta) else 0
        if self.src:
            return self.src.level(meta)
        return 1


def _meta_text(meta):
    return cf._text(meta.get('title'), meta.get('tags'), meta.get('description'))


def text_reason(meta, tgt, full=False):
    """Reject reason for a candidate video from its title (and tags / description when full)."""
    title = meta.get('title') or ''
    nt = cf._norm(title)
    if cf.SUBS_RE.search(title) or SUBS_TITLE_RE.search(NO_SUBS_RE.sub(' ', nt)):
        return 'subtitles'
    stripped = EDIT_ALLOWED_RE.sub(' ', nt)
    if OTHER_EDIT_RE.search(stripped):
        return 'other_edit'
    if cf.offtheme(title, meta.get('tags') if full else ()):
        return 'offtheme'
    if (cf.channel_nsfw(meta) and not ALLOW_FANSERVICE) or EXPLICIT_RE.search(' '.join(
            str(meta.get(k) or '') for k in ('title', 'channel', 'uploader')) + ' ' + ' '.join(meta.get('tags') or [])):
        return 'nsfw'
    flat = _names(' '.join([title] + (list(meta.get('tags') or []) if full else [])))
    own = {_names(x) for x in [tgt.query, tgt.parent or ''] + tgt.aliases}
    if NSFW_SHOW_RE and not ALLOW_FANSERVICE and any(m.group(0) not in own for m in NSFW_SHOW_RE.finditer(flat)):
        return 'nsfw_show'
    if NSFW_CHAR_RE and not ALLOW_FANSERVICE and NSFW_CHAR_RE.search(flat):
        return 'nsfw_char'
    m = dict(meta)
    if tgt.kind == 'group':
        # official MVs are the best K-pop material: clip_finder's "offtype" MV words don't apply
        m['title'] = MV_RE.sub(' ', nt)
    if not full:
        m['description'], m['tags'] = '', []
    vibe = 'dance' if tgt.mode == 'dance' else 'edit'
    src = tgt.src or tgt.char_src
    if src is not None:
        why = src.reason(m, vibe)
        if why:
            return why
    else:
        vb = 'dance' if tgt.mode == 'dance' else 'cool'
        why = cf.text_reason(m.get('title'), m.get('description') or '', m.get('tags') or [], vibe=vb)
        if why == 'offtype':
            # off-type words describe the video only in the title / tags (as Source.reason does):
            # a creditless ED's description naming the song's "music video" or "lyrics" is not one
            why = cf.text_reason(m.get('title'), '', m.get('tags') or [], vibe=vb)
        if why:
            return why
    if tgt.kind == 'anime' and KID_RE.search(nt) and tgt.mode == 'dance':
        return 'kids'
    if tgt.kind == 'anime' and (MV_RE.search(nt) or 'music clip' in nt or 'lyric' in nt):
        return 'singer_mv'
    return None


def relevant(meta, tgt, full):
    """Does the video belong to the target? (probe decides for weak title-only signals)"""
    if tgt.mode == 'character':
        lv = tgt.level(meta)
        return lv >= 1 or (not full and lv >= 0.5)
    if tgt.src is not None:
        lv = tgt.src.level(meta)
        if not lv and not full and tgt.src.weak_title(meta):
            return True
        if not lv:
            return False
    else:
        if not cf.relevant(meta, tgt.theme, '') and full:
            return False
    if tgt.mode == 'dance':
        t = _meta_text(meta) if full else cf._norm(meta.get('title'))
        if tgt.kind == 'anime' and not tgt.query:
            # any-anime dance: one of the curated dance shows, by title, and a dance / OP / ED upload
            if not ANIME_DANCE_SHOW_RE.search(cf._norm(meta.get('title'))):
                return False
            return bool(DANCE_TITLE_RE.search(t) or OP_RE.search(t)) or not full
        return bool(DANCE_TITLE_RE.search(t)) or not full
    return True


def material(meta):
    t = cf._norm(meta.get('title'))
    return {'creditless': bool(CREDITLESS_RE.search(t)), 'twixtor': bool(TWIXTOR_RE.search(t)),
            'raw': bool(RAW_RE.search(t)), 'op': bool(OP_RE.search(t)), 'quality': bool(QUALITY_RE.search(t)),
            'mv': bool(MV_RE.search(t)), 'stage': bool(STAGE_RE.search(t)), 'talky': bool(TALKY_RE.search(t)),
            'lyrics': bool(LYRICS_RE.search(t))}


def pre_score(e, tgt, rng, official=False):
    m = material(e)
    views = e.get('views') or e.get('view_count') or 0
    sc = 0.45 * math.log10(views + 10)
    sc += 1.0 * m['creditless'] + 0.8 * m['twixtor'] + 0.6 * m['raw'] + 0.4 * m['quality']
    sc += 0.3 * (m['op'] and tgt.kind == 'anime')
    if tgt.kind == 'group':
        sc += 0.9 * official + 0.7 * (m['mv'] and tgt.mode != 'dance') + 0.6 * m['stage']
        # a static wide practice room is dance material, not the member close-ups the K-pop song
        # edits cut to (kpop.json: 60 % close-ups)
        if tgt.mode != 'dance' and re.search(r'dance practice|choreography|relay dance', cf._norm(e.get('title'))):
            sc -= 0.5
    sc -= 1.2 * m['talky'] + 0.6 * m['lyrics']      # lyric / romaji uploads sometimes burn the words in
    lv = tgt.level(e)
    sc += 0.6 * min(2.5, lv)
    dur = e.get('duration') or 0
    if dur:
        sc += 0.3 if 20 <= dur <= WHOLE_MAX else (-0.6 if dur > 900 else 0.0)
    return sc + rng.uniform(0, 0.4)


# ---------------------------------------------------------------- analysis

def heat_window(info, length):
    dur = float(info.get('duration') or 0)
    if dur <= length:
        return 0.0, dur
    hm = [m for m in info.get('heatmap') or [] if 0.06 * dur < m.get('start_time', 0) < 0.94 * dur]
    peak = max(hm, key=lambda m: m.get('value', 0))['start_time'] if hm else dur * 0.35
    a = max(0.0, min(dur - length, peak - length / 2))
    return a, a + length


def size_estimate(info, max_h=1080):
    """Bytes of the <= max_h video + audio we would fetch (None when unknown)."""
    fs = info.get('formats') or []
    vids = [f for f in fs if f.get('vcodec') not in (None, 'none') and (f.get('height') or 0) <= max_h]
    auds = [f for f in fs if f.get('vcodec') in (None, 'none') and f.get('acodec') not in (None, 'none')]

    def size(f):
        return f.get('filesize') or f.get('filesize_approx') or (
            (f.get('tbr') or 0) * 125 * (info.get('duration') or 0) if f.get('tbr') else None)
    v = [size(f) for f in sorted(vids, key=lambda f: (f.get('height') or 0, f.get('fps') or 0))[-3:]]
    v = [x for x in v if x]
    a = [size(f) for f in auds]
    a = [x for x in a if x]
    if not v:
        return None
    return max(v) + (max(a) if a else 0)


def plan_source(info, ana_max, max_h=1080):
    """(download section or None, analysis start, analysis span) in source seconds. Whole files are
    fetched with chunked HTTP (fast); a section goes through ffmpeg at about playback speed, so it is
    only used for very large files, and kept short."""
    dur = float(info.get('duration') or 0)
    est = size_estimate(info, max_h)
    if dur <= WHOLE_MAX or (est is not None and est <= 400e6) or (est is None and dur <= 900):
        a, b = heat_window(info, ana_max)
        return None, a, b - a
    a, b = heat_window(info, min(SECTION, ana_max))
    return (max(0.0, a - 1.5), min(dur, b + 1.5)), a, b - a


def jpg(img, q=90):
    ok, buf = cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, q])
    return buf.tobytes() if ok else b''


def unjpg(b):
    return cv2.imdecode(np.frombuffer(b, np.uint8), cv2.IMREAD_COLOR)


def colorfulness(img):
    b, g, r = [c.astype(np.float32) for c in cv2.split(cv2.resize(img, (160, 90)))]
    rg, yb = r - g, 0.5 * (r + g) - b
    return float(np.sqrt(rg.std() ** 2 + yb.std() ** 2) + 0.3 * np.sqrt(rg.mean() ** 2 + yb.mean() ** 2))


def hue_of(img):
    hsv = cv2.cvtColor(cv2.resize(img, (64, 36)), cv2.COLOR_BGR2HSV)
    s = hsv[..., 1].astype(np.float32)
    if s.sum() < 1:
        return 0.0
    ang = hsv[..., 0].astype(np.float32) * (2 * np.pi / 180)
    return float((np.degrees(np.arctan2((np.sin(ang) * s).sum(), (np.cos(ang) * s).sum())) + 360) % 360)


def decode_audio(path, ss, span, sr):
    rc, out, _ = cf.run(['ffmpeg', '-v', 'error', '-ss', f'{ss:.3f}', '-t', f'{span:.3f}', '-i', path, '-map', '0:a:0',
                         '-ac', '1', '-ar', str(sr), '-f', 's16le', '-'], 120)
    if rc != 0 or not out:
        return None
    return np.frombuffer(out, np.int16).astype(np.float32) / 32768


def sfx_hits_loose(fr, t0, t1, max_hits=8, gap=0.2, flat_min=0.12, tonal_min=0.10, rise_min=4.5, over_min=3.0):
    """align.sfx_hits with the sound-edit spec's candidate thresholds (refs/sound.json): the stock
    thresholds catch only 62 % of the reference hits."""
    flux, db, flat, tonal = fr['flux'], fr['db'], fr['flat'], fr['tonal']
    F = align.FPS_FX
    n = len(flux)
    i0, i1 = max(0, int(t0 * F)), min(n, int(t1 * F))
    if i1 - i0 < F * 0.5:
        return []
    seg = flux[i0:i1]
    med = float(np.median(seg))
    thr = max(float(np.percentile(seg, 95)), med + 5 * float(np.median(np.abs(seg - med))), 2.5)
    med_db = float(np.median(db[i0:i1]))
    out = []
    for k in range(max(i0, 1), min(i1, n - 1)):
        if flux[k] < thr or flux[k] < flux[k - 1] or flux[k] < flux[k + 1]:
            continue
        pk = float(db[k:k + 6].max())
        over, rise = pk - med_db, pk - float(np.median(db[max(0, k - 30):max(1, k - 2)]))
        fl, ton = float(flat[k:k + 6].max()), float(np.median(tonal[k:k + 15]))
        if over < over_min or rise < rise_min or fl < flat_min or ton < tonal_min:
            continue
        st = (0.35 * min(1, flux[k] / (2 * thr)) + 0.25 * min(1, over / 15) + 0.2 * min(1, rise / 20)
              + 0.2 * min(1, fl / 0.4))
        out.append((k / F, round(st, 3)))
    picked = []
    for t, s in sorted(out, key=lambda x: -x[1]):
        if all(abs(t - u) >= gap for u, _ in picked):
            picked.append((round(t, 3), s))
        if len(picked) >= max_hits:
            break
    return picked


def beatiness(fr, t0=0.0, t1=None):
    """0-1: how strongly the sound repeats a beat (a song) over t0..t1 (onset autocorrelation)."""
    F = align.FPS_FX
    flux = fr['flux']
    i0, i1 = int(t0 * F), min(len(flux), int((t1 if t1 is not None else len(flux) / F) * F))
    x = flux[i0:i1]
    if len(x) < 3 * F:
        return 0.0
    x = np.convolve(x, np.ones(3) / 3, 'same')
    x = x - x.mean()
    ac = np.correlate(x, x, 'full')[len(x) - 1:]
    lo, hi = int(0.3 * F), min(len(ac) - 1, int(1.0 * F))
    return float(max(0.0, ac[lo:hi].max() / (ac[0] + 1e-9))) if hi > lo else 0.0


class SourceAnalysis:
    """Everything the scorer needs from one source file: candidate windows with cheap features,
    DB text, faces, audio features and their kept frames."""

    def __init__(self, info, path, offset, ss, span):
        self.info, self.path, self.offset, self.ss, self.span = info, path, offset, ss, span
        self.cands = []
        self.frames = {}        # kept frame index -> jpeg bytes (640 long side)
        self.meta = {}
        self.reason = None
        self.debug = None


def analyse(info, path, offset, ss, span, tgt, models, args):
    """Decode one source window and cut it into candidate shots with cheap features and gates."""
    res = SourceAnalysis(info, path, offset, ss, span)
    mi = ffprobe(path)
    if not mi:
        res.reason = 'unreadable'
        return res
    W, H, fps, fdur, has_audio = mi
    span = min(span, max(0.0, fdur - ss)) if fdur else span
    if span < args.min_dur + 0.5:
        res.reason = 'short'
        return res
    vertical = H > W
    if min(W, H) < 700 and not (vertical and H >= 960):
        res.reason = 'lowres'
        return res
    if W >= H:
        dw, dh = LONG, max(2, int(round(LONG * H / W / 2)) * 2)
    else:
        dh, dw = LONG, max(2, int(round(LONG * W / H / 2)) * 2)
    aw, ah = (320, max(2, int(round(320 * dh / dw / 2)) * 2)) if dw >= dh else (max(2, int(round(320 * dw / dh / 2)) * 2), 320)
    tw, th = max(8, aw // 5), max(8, ah // 5)
    gw, gh = (GW, GH) if dw >= dh else (GH, GW)
    mw, mh = gw * 10, gh * 10
    tmr = {'t0': time.time()}
    proc = cf.popen(['ffmpeg', '-v', 'error', '-skip_loop_filter', 'all', '-ss', f'{ss:.3f}', '-t', f'{span:.3f}',
                     '-i', path, '-map', '0:v:0',
                     '-vf', f'fps={FPS_A},scale={dw}:{dh}:flags=area', '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-'],
                    stderr=subprocess.DEVNULL)
    size = dw * dh * 3
    thumbs, hsv_d, areas, samples, g10 = [], [], [], {}, []
    fw10, fh10 = (256, max(2, int(round(256 * dh / dw)))) if dw >= dh else (max(2, int(round(256 * dw / dh))), 256)
    frames = {}
    prev_hsv = prev_m = None
    i = 0
    real = tgt.kind == 'group'
    try:
        while i < FPS_A * (span + 2):
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            f = np.frombuffer(buf, np.uint8).reshape(dh, dw, 3)
            small = cv2.resize(f, (tw, th), interpolation=cv2.INTER_AREA)
            thumbs.append(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY))
            if not real:
                g10.append(cv2.cvtColor(cv2.resize(f, (fw10, fh10), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY))
            hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV).astype(np.int16)
            hsv_d.append(0.0 if prev_hsv is None else float(np.abs(hsv - prev_hsv).mean()))
            prev_hsv = hsv
            mg = cv2.cvtColor(cv2.resize(f, (mw, mh), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY).astype(np.int16)
            if prev_m is None:
                areas.append(np.zeros((gh, gw), np.float32))
            else:
                areas.append(np.abs(mg - prev_m).reshape(gh, 10, gw, 10).mean((1, 3)).astype(np.float32))
            prev_m = mg
            if i % 3 == 0:
                g = cv2.cvtColor(cv2.resize(f, (aw, ah), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
                samples[i] = {'text': float(cf.text_mask(g).mean()),
                              'sharp': float(cv2.Laplacian(g[ah // 5:ah - ah // 5, aw // 5:aw - aw // 5], cv2.CV_32F).var()),
                              'sharp_loc': local_sharpness(g)}
            if i % KEEP == 0:
                frames[i] = jpg(f)
                s = samples.setdefault(i, {})
                if i % (2 * KEEP) == 0:
                    s['nude'] = nude_level(f)
                if real:
                    s['faces'] = [tuple(round(float(v), 4) for v in x) for x in cf.faces(f) if x[4] >= 0.7]
            i += 1
    finally:
        try:
            proc.stdout.close()
        except Exception:
            pass
        proc.kill()
        proc.wait()
        cf._procs.discard(proc)
    n = len(thumbs)
    if n < FPS_A * args.min_dur:
        res.reason = 'short'
        return res
    hard = sum(1 for s in samples.values() if s.get('nude') == 2)
    soft = sum(1 for s in samples.values() if s.get('nude') == 1)
    n_checked = max(1, sum(1 for s in samples.values() if 'nude' in s))
    strict = bool(tgt.src and tgt.src.risky) or (tgt.mode == 'character' and tgt.adult is False)
    if hard >= cf.NUDE_T['drop'] or (hard + soft) / n_checked > (0.08 if strict else 0.2) or (strict and hard + soft >= cf.NUDE_T['drop']):
        log(f'{info["id"]}: NudeNet hit on {hard} + {soft} soft of {n_checked} frames, dropping the source')
        res.reason = 'nsfw_frames'
        return res
    x0, y0, x1, y1 = cf.content_box(thumbs)
    cy0, cx0 = int(y0 * th), int(x0 * tw)
    cy1, cx1 = max(cy0 + 1, int(round(y1 * th))), max(cx0 + 1, int(round(x1 * tw)))
    crop = np.stack([t[cy0:cy1, cx0:cx1] for t in thumbs]).astype(np.float32)
    flat = crop.reshape(n, -1).astype(np.int16)
    modes = np.array([np.bincount(r, minlength=256).argmax() for r in flat])
    mot = np.zeros(n)
    mot[1:] = np.abs(np.diff(crop, axis=0)).mean((1, 2)) / 255
    hd = np.array(hsv_d)
    base = cf.rolling_median(hd, 11)
    lum = crop.mean((1, 2))
    cuts = set(k for k in range(1, n) if hd[k] >= 45 or (hd[k] >= 22 and hd[k] >= 2.6 * base[k] + 3))
    # flashes (a white or black frame or two between shots) are cuts too
    for k in range(1, n - 1):
        if abs(lum[k] - lum[k - 1]) > 60 and abs(lum[k] - lum[k + 1]) > 40:
            cuts.add(k)
    cuts = sorted(cuts)
    for k in cuts:
        mot[k] = mot[k - 1]
    ga = np.stack(areas)
    gy0, gy1 = int(y0 * gh), max(int(y0 * gh) + 1, int(round(y1 * gh)))
    gx0, gx1 = int(x0 * gw), max(int(x0 * gw) + 1, int(round(x1 * gw)))
    ga = ga[:, gy0:gy1, gx0:gx1]
    area = (ga > 7).mean((1, 2))
    for k in cuts:
        area[k] = area[k - 1]
    spike = np.maximum(0, mot - cf.rolling_median(mot, 31))
    detail = crop.std((1, 2))
    black = (crop < 20).mean((1, 2))
    hi_lum = np.percentile(crop.reshape(n, -1), 99, axis=1)
    uniform = (np.abs(flat - modes[:, None]) <= 8).mean(1)

    tmr['decode'] = time.time()
    # audio
    speech = np.zeros(0)
    fx = None
    onset = np.zeros(n)
    if has_audio:
        y16 = decode_audio(path, ss, span, 16000)
        if y16 is not None and len(y16) > 16000:
            speech = models.vad.probs(y16) if models.vad else speech_heuristic(y16)
            hop = 16000 // FPS_A
            m = min(n, len(y16) // hop)
            db = np.full(n, -80.0)
            if m:
                db[:m] = 20 * np.log10(np.sqrt((y16[:m * hop].reshape(m, hop) ** 2).mean(1)) + 1e-4)
            onset[2:] = np.maximum(0, db[2:] - db[:-2])
            onset[db < -45] = 0
        y22 = align.decode(path, ss, span, runner=cf.run, sr=align.SR_FX, pad=True)
        fx = align.sfx_frames(y22) if y22 is not None and len(y22) > align.SR_FX else None
    hit = np.minimum(1, spike / 0.05) + 0.6 * np.minimum(1, onset / 6)
    beat = beatiness(fx) if fx else 0.0
    mat = material(info)
    music_source = bool(mat['op'] or mat['twixtor'] or mat['mv'] or mat['stage'] or tgt.kind == 'group'
                        or beat >= 0.42)
    res.meta = {'W': W, 'H': H, 'fps': round(fps, 3), 'has_audio': bool(has_audio), 'beat': round(beat, 3),
                'music_source': music_source, 'box': [round(v, 4) for v in (x0, y0, x1, y1)], 'n': n,
                'nude_frames': [k for k, s in samples.items() if s.get('nude')]}

    # shots: continuous stretches between cuts, windows of min..max dur
    bounds = [0] + [k for k in cuts] + [n]
    lo_min, lo_max = int(round(args.min_dur * FPS_A)), int(round(args.max_dur * FPS_A))
    ev = np.convolve(np.minimum(1, mot / 0.04) + 0.6 * np.minimum(1, hit), np.ones(7) / 7, mode='same')
    windows = []
    for s0, e0 in zip(bounds[:-1], bounds[1:]):
        s, e = s0 + 1, e0 - 1        # never the frame of a cut
        if s0 == 0 and offset + ss > 0.5:
            s = max(s, 2)
        if e - s < lo_min:
            continue
        if e - s <= lo_max:
            windows.append((s, e))
            continue
        # a long take (a dance, a slow pan): tiled into max-length windows, each slid toward the
        # strongest moment of its stretch, up to 6
        L = lo_max
        k = min(6, (e - s) // L)
        for j in range(k):
            lo_, hi_ = s + j * (e - s) // k, s + (j + 1) * (e - s) // k
            p = lo_ + int(np.argmax(ev[lo_:hi_])) if hi_ > lo_ else lo_
            a0 = int(min(max(lo_, p - int(0.45 * L)), hi_ - L)) if hi_ - lo_ >= L else lo_
            windows.append((a0, min(e, a0 + L)))
    bump('windows', n=len(windows))

    kept_idx = sorted(frames)

    def kept_in(s, e):
        return [k for k in kept_idx if s <= k < e]

    def sample_vals(key, s, e):
        return [samples[k][key] for k in range(s - s % 3, e, 3) if k in samples and key in samples[k] and k >= s]

    cands = []
    dbg = [] if args.debug_dir else None
    for s, e in windows:
        why = None
        dur = (e - s) / FPS_A
        ks = kept_in(s, e)
        keys = sorted({ks[min(len(ks) - 1, int(round(q * (len(ks) - 1))))] for q in (0.2, 0.5, 0.8)}) if ks else []
        near_nude = [k for k in res.meta['nude_frames'] if s - 15 <= k < e + 15]
        lm, dt = float(lum[s:e].mean()), float(detail[s:e].mean())
        m = float(mot[s + 1:e].mean())
        ar = area[s + 1:e]
        a75 = float(np.percentile(ar, 75)) if len(ar) else 0.0
        sharp_v = sample_vals('sharp', s, e)
        sharp = float(np.median(sharp_v)) if sharp_v else 0.0
        sl_v = sample_vals('sharp_loc', s, e)
        sharp_loc = float(np.median(sl_v)) if sl_v else sharp
        tm = sample_vals('text', s, e)
        text_mask = float(np.mean(tm)) if tm else 0.0
        sp_e = 0.0
        if getattr(args, 'allow_dialogue', False) and len(speech):
            seg_ = speech[int(s / FPS_A * 16000 / 512):int(e / FPS_A * 16000 / 512)]
            sp_e = float((seg_ > 0.5).mean()) if len(seg_) else 0.0
        if near_nude:
            why = 'nsfw'
        elif lm < 16 or (lm < 34 and float(np.median(hi_lum[s:e])) < 150) or black[s:e].mean() > 0.75:
            why = 'dark'
        elif lm > 235:
            why = 'white'
        elif dt < 12 or (uniform[s:e] > 0.8).mean() > 0.2:
            why = 'blank'
        elif m < 0.0022 and a75 < 0.02 and spike[s:e].max() < 0.01 and sp_e < 0.3:        # a voiced still (talking scene) is kept with --allow-dialogue
            why = 'static'
        elif sharp < 90 and sharp_loc < 250:
            why = 'blurry'
        elif text_mask > 0.3:
            why = 'text'
        elif len(kept_in(s, e)) < 2:
            why = 'short'
        if why:
            bump('shot_rejected', why)
            if dbg is not None:
                dbg.append({'s': s, 'e': e, 'why': why, 'keys': keys, 'line': f'lum{lm:.0f} det{dt:.0f} m{m:.4f} '
                            f'ar{a75:.3f} sh{sharp:.0f} tm{text_mask:.2f}'})
            continue
        hh = hit[s:e]
        hits = cf._local_peaks(hh, max(0.5, 0.4 * float(hh.max()))) if len(hh) > 2 else []
        peak = int(np.argmax(hh)) if len(hh) else 0
        # periodic motion (dance): motion autocorrelation at 0.4-1.0 s
        mm = mot[s + 1:e] - mot[s + 1:e].mean()
        per = 0.0
        if len(mm) > 20 and mm.std() > 1e-5:
            ac = np.correlate(mm, mm, 'full')[len(mm) - 1:]
            per = float(max(0.0, ac[4:min(len(ac), 11)].max() / (ac[0] + 1e-9)))
        t0, t1 = s / FPS_A, e / FPS_A
        sp = 0.0
        if len(speech):
            j0, j1 = int(t0 * 16000 / 512), int(t1 * 16000 / 512)
            seg = speech[j0:j1]
            sp = float((seg > 0.5).mean()) if len(seg) else 0.0
        sfx, sfx_sc = [], 0.0
        if fx:
            hs = sfx_hits_loose(fx, t0, t1) if args.with_audio else align.sfx_hits(fx, t0, t1)
            sfx = [round(t - t0, 2) for t, _ in hs]
            sfx_sc = align.sfx_score(fx, t0, t1, hs) if hs else 0.0
        faces_k = {k: samples[k].get('faces', []) for k in keys} if real else {}
        cand = {
            's': s, 'e': e, 'start': round(offset + ss + t0, 3), 'end': round(offset + ss + t1, 3),
            'local': round(ss + t0, 3), 'duration': round(dur, 2),
            'motion_raw': round(m, 5), 'area75': round(a75, 4), 'area_med': round(float(np.median(ar)) if len(ar) else 0, 4),
            'sharp': round(sharp, 1), 'sharp_loc': round(sharp_loc, 1), 'luma': round(lm, 1),
            'ev': [round(float(v), 2) for v in ev[s:e]], 'detail': round(dt, 2), 'text_mask': round(text_mask, 4),
            'hits': [round(h / FPS_A, 2) for h in hits][:12], 'peak': round(peak / FPS_A, 2), 'periodic': round(per, 3),
            'speech_ratio': round(sp, 3), 'sfx_hits': sfx[:8], 'sfx_score': round(float(sfx_sc), 3),
            'keys': keys, 'kept': ks, 'real_faces': {str(k): v for k, v in faces_k.items()},
            'hash': cf.dhash(thumbs[(s + e) // 2][cy0:cy1, cx0:cx1]),
        }
        cands.append(cand)

    # color / contrast on the middle key frame, and a cheap pre-score to pick who gets the models
    split_ok = []
    for c in cands:
        if split_frame(unjpg(frames[c['keys'][len(c['keys']) // 2]]), (x0, y0, x1, y1)):
            bump('shot_rejected', 'split_frame')
            if dbg is not None:
                dbg.append({'s': c['s'], 'e': c['e'], 'why': 'split_frame', 'keys': c['keys'], 'line': ''})
            continue
        split_ok.append(c)
    cands = split_ok
    for c in cands:
        mid = unjpg(frames[c['keys'][len(c['keys']) // 2]])
        c['colorful'] = round(colorfulness(mid), 1)
        c['hue'] = round(hue_of(mid), 1)
        c['contrast'] = round(float(cv2.cvtColor(mid, cv2.COLOR_BGR2GRAY).std()), 1)
        c['pre'] = (0.3 * min(1, c['motion_raw'] / 0.03) + 0.2 * min(1, c['area75'] / 0.3)
                    + 0.25 * min(1, max(0, math.log(max(c['sharp'], 1) / 100) / math.log(15)))
                    + 0.15 * min(1, c['colorful'] / 80) + 0.1 * min(1, c['contrast'] / 60)
                    - 0.3 * min(1, c['speech_ratio'] / 0.5) * (not music_source))

    tmr['audio_shots'] = time.time()
    # DB text: a sparse pass over the whole window for the watermark map, then candidate by candidate
    cands.sort(key=lambda c: -c['pre'])
    boxes = {}

    def db(k):
        if k not in boxes:
            boxes[k] = models.text(unjpg(frames[k])) if models.text else []
        return boxes[k]
    for k in kept_idx[::4]:
        db(k)
    wm = watermark({k: [b for b in v if line_like(b, dw / dh)] for k, v in boxes.items()}, cuts, n, frames)
    faint = persistent_marks(frames, cuts, n, (x0, y0, x1, y1))
    wm['faint'] = faint
    res.meta['watermark'] = wm
    inside_faint = [b for b in faint if not edge_zone(b)]
    if wm.get('center') or inside_faint:
        log(f'{info["id"]}: burned-in text across shots inside the picture '
            f'({wm.get("center") or inside_faint}); source dropped')
        res.reason = 'watermark'
        res.meta['watermark_uploader'] = bool(wm.get('brand') or inside_faint)
        return res
    wm['boxes'] = wm['boxes'] + faint
    crop_box = [x0, y0, x1, y1]
    if wm['boxes']:
        nb = crop_for(wm['boxes'], crop_box)
        if nb is None:
            log(f'{info["id"]}: a burned-in logo / watermark sits inside the picture; source dropped')
            res.reason = 'watermark'
            return res
        crop_box = nb
    res.meta['content_box'] = [round(v, 4) for v in crop_box]

    kept = []
    for c in cands:
        if len(kept) >= getattr(args, 'siglip_per_source', SIGLIP_PER_SOURCE):
            break
        per = [[b for b in db(k) if not inside_any(b, wm['boxes']) and overlaps_box(b, crop_box)]
               for k in c['kept']]
        big = overlay_text(per, dw / dh)
        c['text_boxes'] = len(big)
        if big:
            bump('shot_rejected', 'text')
            if dbg is not None:
                dbg.append({'s': c['s'], 'e': c['e'], 'why': 'text_db', 'keys': c['keys'],
                            'line': f'{len(big)} boxes e.g. ' + ' '.join(f'[{b[0]:.2f},{b[1]:.2f},{b[2]:.2f},{b[3]:.2f}]' for b in big[:2])})
            continue
        # NudeNet on every kept frame in and around the shot (decode checked only every other one)
        near = [k for k in kept_idx if c['s'] - 15 <= k < c['e'] + 15]
        for k in near:
            if 'nude' not in samples.setdefault(k, {}):
                samples[k]['nude'] = nude_level(unjpg(frames[k]))
        if any(samples[k].get('nude') for k in near):
            bump('shot_rejected', 'nsfw')
            if dbg is not None:
                dbg.append({'s': c['s'], 'e': c['e'], 'why': 'nsfw', 'keys': c['keys'], 'line': 'NudeNet'})
            continue
        kept.append(c)
    # faces (anime detector) on the key frames of the survivors
    tmr['text'] = time.time()
    face_conf = 0.25 if tgt.mode == 'character' else 0.35
    if tgt.kind == 'anime' and models.anime_faces:
        for c in kept:
            c['anime_faces'] = {str(k): models.anime_faces(unjpg(frames[k]), conf=face_conf) for k in c['keys']}
            mf, mc = mouth_flap(g10, c['s'], c['e'], c['anime_faces'], c['keys'])
            c['mouth_flap'], c['mouth_frames'] = round(mf, 3), mc
    for c in kept:
        c.pop('kept', None)
        # detail at 640 px: a soft upscale passes the 320 px floors but has no fine line work here
        v = [local_sharpness(cv2.cvtColor(unjpg(frames[k]), cv2.COLOR_BGR2GRAY)) for k in c['keys']]
        c['sharp640'] = round(float(np.median(v)), 1) if v else 0.0
    tmr['faces'] = time.time()
    keys_ = ['t0', 'decode', 'audio_shots', 'text', 'faces']
    res.meta['timing'] = {k: round(tmr[k] - tmr[p], 1) for p, k in zip(keys_, keys_[1:])}
    res.meta['timing']['frames'] = n
    kept.sort(key=lambda c: -c['pre'])
    res.cands = kept
    need_frames = {k for c in kept for k in c['keys']}
    if dbg is not None:
        need_frames |= {k for d in dbg for k in d['keys']}
        res.debug = dbg
    res.frames = {k: frames[k] for k in need_frames}
    bump('analysed')
    return res


def local_sharpness(g, gy=6, gx=8):
    """Laplacian variance of the sharpest part of a grey frame: the 85th percentile over a gy x gx
    grid of blocks. A glowing / fiery / backlit shot is soft over most of the frame while its subject
    (the face, the line art, the eyes) is crisp; the centre-crop variance calls that shot soft, this
    does not. A genuinely soft upscale stays low everywhere."""
    h, w = g.shape[:2]
    bh, bw = h // gy, w // gx
    if bh < 4 or bw < 4:
        return 0.0
    lap = cv2.Laplacian(g[:bh * gy, :bw * gx], cv2.CV_32F).reshape(gy, bh, gx, bw)
    return float(np.percentile(lap.var(axis=(1, 3)), 85))


def split_frame(img, box):
    """Two pictures in one frame (a stacked or side-by-side composite): a thin uniform bar, or a
    perfectly straight seam, running across the whole picture away from its edges."""
    x0, y0, x1, y1 = box
    h, w = img.shape[:2]
    im = img[int(y0 * h):max(int(y0 * h) + 2, int(y1 * h)), int(x0 * w):max(int(x0 * w) + 2, int(x1 * w))]
    g = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY).astype(np.float32)
    for arr in (g, g.T):
        H_, W_ = arr.shape
        if H_ < 40 or W_ < 40:
            continue
        rs = arr.std(1)                                     # a uniform row
        rm = arr.mean(1)
        lo, hi = int(0.15 * H_), int(0.85 * H_)
        flat = rs < 6
        for y in range(lo, hi):
            if not flat[y]:
                continue
            k = y
            while k < hi and flat[k] and abs(rm[k] - rm[y]) < 12:
                k += 1
            if 2 <= k - y <= 0.08 * H_:
                above, below = arr[max(0, y - 12):y], arr[k:k + 12]
                if len(above) and len(below) and above.std() > 12 and below.std() > 12:
                    return True
            if k > y:
                flat[y:k] = False
        # a seam: an edge along the full width, much stronger than the rows around it
        d = np.abs(np.diff(arr, axis=0))
        strong = (d > 40).mean(1)
        for y in range(lo, min(hi, len(strong))):
            if strong[y] >= 0.9 and np.median(strong[max(0, y - 8):y + 9]) < 0.2:
                return True
    return False


def nude_level(img):
    """clip_finder's NudeNet check with the rule that fired: 2 exposed body parts, 1 swimwear-like
    coverage (a covered chest with a bare midriff, covered crotch / buttocks), 0 none. Any level
    rejects the shots around it; only exposure (or coverage all over the source) drops a source,
    so a girl group's stage in crop tops keeps its close-ups."""
    if 'nude' not in cf.PATHS:
        return 0
    net = getattr(cf._tls, 'nude', None)
    if net is None:
        net = cf._tls.nude = cv2.dnn.readNet(cf.PATHS['nude'])
    m = max(img.shape[:2])
    sq = cv2.copyMakeBorder(img, 0, m - img.shape[0], 0, m - img.shape[1], cv2.BORDER_CONSTANT)
    net.setInput(cv2.dnn.blobFromImage(sq, 1 / 255.0, (320, 320), swapRB=True))
    sc = np.squeeze(net.forward()).T[:, 4:].max(0)
    t = cf.NUDE_T
    if sc[cf.NUDE_EXPOSED].max() >= t['exposed']:
        return 2
    if (sc[cf.NUDE_G_COV] >= t['g_cov'] or sc[cf.NUDE_BUTT_COV] >= t['butt_cov'] or sc[cf.NUDE_BREAST_COV] >= t['breast_cov']
            or (sc[cf.NUDE_BREAST_COV] >= t['combo'][0] and sc[cf.NUDE_BELLY] >= t['combo'][1])):
        return 1
    return 0


def line_like(b, aspect):
    """A box shaped like a line of text: wide and short (Latin, horizontal CJK), or a narrow
    vertical column (Japanese credits). DB also fires on hair, sparks and patterns, which come out
    square or huge."""
    w, h = b[2] - b[0], b[3] - b[1]
    if w <= 0 or h <= 0:
        return False
    pa = w * aspect / h                     # pixel aspect ratio of the box
    if 0.012 <= h <= 0.13 and pa >= 1.9:
        return True
    return w * aspect <= 0.11 and pa <= 0.42 and h >= 0.06


def overlay_text(per_frame, aspect):
    """Burned-in text in a shot from the DB boxes of its kept frames (2 fps): a line-shaped box
    at the same place in two neighbouring samples (subtitles, credits and titles hold for 1 s or
    more; false hits on moving content don't), a wide line in subtitle position, or three lines at
    once (credits, a title card)."""
    lines = [[b for b in bs if line_like(b, aspect)] for bs in per_frame]
    out = []
    for i, bs in enumerate(lines):
        nxt = lines[i + 1] if i + 1 < len(lines) else []
        prv = lines[i - 1] if i > 0 else []
        for b in bs:
            w, h = b[2] - b[0], b[3] - b[1]
            cy, cx = (b[1] + b[3]) / 2, (b[0] + b[2]) / 2
            if any(iou(b, o) >= 0.3 for o in nxt + prv):
                out.append(b)
            elif (cy >= 0.74 or cy <= 0.16) and 0.2 <= cx <= 0.8 and w * aspect / h >= 5:
                out.append(b)
        if len(bs) >= 3:
            out += bs
    return out


def mouth_flap(g10, s, e, faces, keys, min_area=0.012):
    """Share of frames (10 fps) where a face's mouth changes while its eyes hold still: anime
    dialogue. The face is the largest one on the nearest key frame."""
    if not g10 or not keys:
        return 0.0, 0
    H2, W2 = g10[0].shape
    hits = cnt = 0
    for j in range(max(1, s + 1), min(e, len(g10))):
        k = min(keys, key=lambda q: abs(q - j))
        fb = [b for b in faces.get(str(k), []) if b[2] * b[3] >= min_area]
        if not fb:
            continue
        x, y, w, h = fb[0][0] * W2, fb[0][1] * H2, fb[0][2] * W2, fb[0][3] * H2
        mx0, mx1, my0, my1 = int(x + 0.22 * w), int(x + 0.78 * w) + 1, int(y + 0.6 * h), int(y + 0.97 * h) + 1
        ux0, ux1, uy0, uy1 = int(x + 0.1 * w), int(x + 0.9 * w) + 1, int(y + 0.15 * h), int(y + 0.5 * h) + 1
        a, b = g10[j].astype(np.int16), g10[j - 1].astype(np.int16)
        mm = a[max(0, my0):my1, max(0, mx0):mx1]
        uu = a[max(0, uy0):uy1, max(0, ux0):ux1]
        if mm.size < 12 or uu.size < 12:
            continue
        dm = float(np.abs(mm - b[max(0, my0):my1, max(0, mx0):mx1]).mean())
        du = float(np.abs(uu - b[max(0, uy0):uy1, max(0, ux0):ux1]).mean())
        cnt += 1
        if dm >= 3.0 and dm >= 2.5 * du + 1.0:
            hits += 1
    return (hits / cnt if cnt else 0.0), cnt


CROP_TB, CROP_LR = 0.18, 0.12      # the most a logo crop may take off the top / bottom, or a side


def edge_zone(b):
    """A box in the outer band of the frame, where a content-box crop can remove it (broadcast
    bugs and channel logos sit in the top / bottom band or a side)."""
    return b[3] <= CROP_TB or b[1] >= 1 - CROP_TB or b[2] <= CROP_LR or b[0] >= 1 - CROP_LR


def persistent_marks(frames, cuts, n, box, thr=22):
    """Faint fixed overlays (an uploader's semi-transparent name, a channel bug): pixels that are
    edges in most shots of the source. Each shot votes once, so a long static shot can't fake it;
    scene edges rarely line up across different shots."""
    ks = sorted(frames)
    if len(ks) < 10:
        return []
    shot_of = np.searchsorted(np.array(list(cuts) + [n + 1]), np.array(ks), side='right')
    shots = {}
    for k, sh in zip(ks, shot_of):
        shots.setdefault(int(sh), []).append(k)
    ring_check = False
    if len(shots) < 5:
        # a one-take video (a stage "원테이크" cam, a dance practice) has no cuts to vote with:
        # every kept frame votes instead (0.5 s apart, so a moving camera changes the picture
        # between votes), and a mark found this way must sit in the frame's edge band, be big
        # enough to be a logo and have moving surroundings, so a static shot cannot fake one
        if len(ks) < 40:
            return []
        shots = {k: [k] for k in ks}
        ring_check = True
    acc, m = None, 0
    for sh, kk in shots.items():
        pres = None
        for k in kk[:4]:
            g = cv2.cvtColor(unjpg(frames[k]), cv2.COLOR_BGR2GRAY)
            e = cv2.morphologyEx(g, cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8)) > thr
            pres = e.astype(np.float32) if pres is None else pres + e
        pres = (pres / min(4, len(kk))) >= 0.5
        acc = pres.astype(np.float32) if acc is None else acc + pres
        m += 1
    P = acc / m
    h, w = P.shape
    mask = (P >= 0.72).astype(np.uint8)
    x0, y0, x1, y1 = box
    pad = 0.03
    mask[:, :int((x0 + pad) * w)] = 0
    mask[:, int((x1 - pad) * w):] = 0
    mask[:int((y0 + pad) * h), :] = 0
    mask[int((y1 - pad) * h):, :] = 0
    mask = cv2.dilate(mask, np.ones((5, 9), np.uint8))
    num, lab, st, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    var = None
    if ring_check:
        g = np.stack([cv2.cvtColor(unjpg(frames[k]), cv2.COLOR_BGR2GRAY).astype(np.float32)
                      for k in ks[::max(1, len(ks) // 32)]])
        var = g.std(0)
    out = []
    for i in range(1, num):
        bx, by, bw, bh, area = st[i]
        if bw >= 0.03 * w and 0.012 * h <= bh <= 0.15 * h and bw * bh <= 0.06 * w * h and area >= 40:
            b4 = [float(bx) / w, float(by) / h, float(bx + bw) / w, float(by + bh) / h]
            if var is not None:
                # one-take mode: an edge-band mark only, logo-sized, with moving surroundings
                # (a semi-transparent bug follows the background's brightness, so the mark
                # itself is not required to be steady)
                if not edge_zone(b4) or bw * bh < 0.004 * w * h:
                    continue
                r0, r1 = max(0, by - bh), min(h, by + 2 * bh)
                c0, c1 = max(0, bx - bw // 2), min(w, bx + bw + bw // 2)
                ring = var[r0:r1, c0:c1].copy()
                ring[by - r0:by - r0 + bh, bx - c0:bx - c0 + bw] = np.nan
                if not np.nanmedian(ring) >= 12:
                    continue
            out.append([round(v, 4) for v in b4])
    if var is not None and len(out) > 3:
        return []                   # too many "marks" in a one-take video: the picture, not a logo
    return out


def text_patch(frames, k, b):
    """Edge map of a text box, normalised, for "is this the same text?" comparisons."""
    img = unjpg(frames[k]) if k in frames else None
    if img is None:
        return None
    h, w = img.shape[:2]
    x0, y0 = max(0, int(b[0] * w) - 2), max(0, int(b[1] * h) - 2)
    x1, y1 = min(w, int(b[2] * w) + 2), min(h, int(b[3] * h) + 2)
    if x1 - x0 < 6 or y1 - y0 < 4:
        return None
    g = cv2.cvtColor(img[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
    e = cv2.morphologyEx(g, cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8)).astype(np.float32)
    e = cv2.resize(e, (64, 16), interpolation=cv2.INTER_AREA).reshape(-1)
    e -= e.mean()
    nrm = float(np.linalg.norm(e))
    return e / nrm if nrm > 1e-3 else None


def watermark(boxes, cuts, n, frames=None):
    """Text boxes that stay put across most sampled frames and several shots: a channel logo,
    a broadcaster bug, an editor's watermark ("boxes"). The same text at the same place in two or
    more different shots is an overlay too: inside the picture ("center") it marks an uploader
    that watermarks its videos."""
    ks = sorted(boxes)
    if len(ks) < 6:
        return {'boxes': [], 'center': [], 'brand': []}
    shot_of = np.searchsorted(np.array(cuts + [n + 1]), np.array(ks), side='right')
    groups = []
    for k, sh in zip(ks, shot_of):
        for b in boxes[k]:
            for g in groups:
                if iou(g['box'], b) > 0.4:
                    g['frames'].add(k)
                    g['shots'].add(int(sh))
                    if frames is not None and int(sh) not in g['same'] and g['patch'] is not None:
                        pt = text_patch(frames, k, g['first'])
                        if pt is not None and float(pt @ g['patch']) >= 0.7:
                            g['same'].add(int(sh))      # the same text in another shot: an overlay
                            g['same_k'].append(k)
                    g['box'] = [min(g['box'][0], b[0]), min(g['box'][1], b[1]), max(g['box'][2], b[2]), max(g['box'][3], b[3])]
                    break
            else:
                groups.append({'box': list(b[:4]), 'first': list(b[:4]), 'frames': {k}, 'shots': {int(sh)},
                               'same': {int(sh)}, 'same_k': [k],
                               'patch': text_patch(frames, k, b) if frames is not None else None})
    n_shots = len(set(int(s) for s in shot_of))
    out = [g['box'] for g in groups
           if (len(g['frames']) >= 0.4 * len(ks) and len(g['shots']) >= min(3, n_shots))
           or (edge_zone(g['box']) and len(g['same']) >= 2 and len(g['frames']) >= 3)]
    inside = [g for g in groups if len(g['frames']) >= 3 and g['box'] not in out and not edge_zone(g['box'])
              and g['box'][2] - g['box'][0] >= 0.06 and len(g['same']) >= 3]
    # a name burned into the whole video (not one credit line that outlives a cut): the uploader's
    brand = [g for g in inside if max(g['same_k']) - min(g['same_k']) >= 30 * FPS_A]
    return {'boxes': [[round(v, 4) for v in b] for b in out],
            'center': [[round(v, 4) for v in g['box']] for g in inside],
            'brand': [[round(v, 4) for v in g['box']] for g in brand]}


def iou(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / u if u > 0 else 0.0


def inside_any(b, boxes, pad=0.01):
    return any(b[0] >= w[0] - pad and b[1] >= w[1] - pad and b[2] <= w[2] + pad and b[3] <= w[3] + pad for w in boxes)


def overlaps_box(b, box):
    return b[2] > box[0] and b[0] < box[2] and b[3] > box[1] and b[1] < box[3]


def crop_for(wboxes, box):
    """Shrink the content box so no watermark box is inside it, cutting at most CROP_TB off the top
    or bottom and CROP_LR off a side (the cheapest cut wins); None when that is not enough."""
    x0, y0, x1, y1 = box
    for b in wboxes:
        if not overlaps_box(b, [x0, y0, x1, y1]):
            continue
        opts = []
        if b[3] - y0 <= CROP_TB:
            opts.append(('y0', b[3] + 0.01, b[3] + 0.01 - y0))
        if y1 - b[1] <= CROP_TB:
            opts.append(('y1', b[1] - 0.01, y1 - b[1] + 0.01))
        if b[2] - x0 <= CROP_LR:
            opts.append(('x0', b[2] + 0.01, b[2] + 0.01 - x0))
        if x1 - b[0] <= CROP_LR:
            opts.append(('x1', b[0] - 0.01, x1 - b[0] + 0.01))
        if not opts:
            return None
        side, v, _ = min(opts, key=lambda o: o[2])
        if side == 'y0':
            y0 = v
        elif side == 'y1':
            y1 = v
        elif side == 'x0':
            x0 = v
        else:
            x1 = v
    return [x0, y0, x1, y1]


# ---------------------------------------------------------------- scoring

class Scorer:
    def __init__(self, tgt, sig, models, args, cache):
        self.tgt, self.sig, self.models, self.args = tgt, sig, models, args
        P = PROMPTS['anime' if tgt.kind == 'anime' else 'group']
        self.P = P
        self.classes = [('g', k, v) for k, v in P['good'].items()] + [('b', k, v) for k, v in P['bad'].items()]
        self.class_names = [k for _, k, _ in self.classes]
        texts = [p for _, _, v in self.classes for p in v]
        self.cls_idx = []
        i = 0
        for _, _, v in self.classes:
            self.cls_idx.append(list(range(i, i + len(v))))
            i += len(v)
        kind = 'anime' if tgt.kind == 'anime' else 'group'
        # the scene types the references of this edit type are cut from
        # --need-sfx is the sound-edit pipeline (refs/sound.json): it wants the hit close-ups, not
        # the song-edit content mix. --with-audio alone only keeps the clip sound, so it keeps the
        # scene mix of its own mode.
        self.scene = SCENES.get((kind, 'sound' if args.need_sfx and tgt.mode != 'dance' else tgt.mode)) \
            or SCENES[(kind, 'source')]
        extra = {
            'pretty': P['pretty'], 'ugly': P['ugly'], 'kind_yes': P['kind_yes'], 'kind_no': P['kind_no'],
            'unsafe': cj.UNSAFE + UNSAFE_EXTRA[kind], 'safe': cj.SAFE + SAFE_EXTRA[kind],
            # the same list without the swimsuit prompts: used ONLY for a confirmed-adult character in a
            # face / upper-body / scene framing (see score()); nudity, underwear, bare skin and
            # suggestive-pose prompts stay in it, so they are exactly as strict as before
            'unsafe_core': [p for p in cj.UNSAFE + UNSAFE_EXTRA[kind] if 'swimsuit' not in p and 'bikini' not in p],
            'gore': cj.GORE, 'clean': cj.CLEAN + CLEAN_EXTRA,
            'f_face': P['framing']['face'], 'f_medium': P['framing']['medium'], 'f_wide': P['framing']['wide'],
            'moment': self.scene['moment'], 'plain': PLAIN[kind],
        }
        if tgt.mode == 'mood' and tgt.query:
            q = tgt.query
            extra['query'] = ([f'an anime scene that feels {q}', f'{q} anime scene', f'an anime frame: {q}']
                              if tgt.kind == 'anime' else [f'a K-pop music video scene: {q}', f'{q} K-pop stage'])
            extra['neutral'] = (['an ordinary anime scene', 'an anime frame'] if tgt.kind == 'anime'
                                else ['an ordinary music video frame', 'a K-pop stage'])
        self.rules = getattr(args, 'rules', None)          # clip_rules rule set (dict) or None
        if self.rules and clip_rules is not None:
            for k, v in clip_rules.prompt_groups(kind).items():
                extra['r_' + k] = v
        self.extra_idx = {}
        for k, v in extra.items():
            self.extra_idx[k] = list(range(len(texts), len(texts) + len(v)))
            texts += v
        self.char = None
        if tgt.mode == 'character' and tgt.kind == 'anime':
            anime = tgt.parent or 'anime'
            names = [tgt.char_name] + [nm for nm, _ in tgt.neg]
            self.char_names = names
            self.extra_idx['char_names'] = list(range(len(texts), len(texts) + len(names) + 1))
            texts += [f'{nm} from {anime}' for nm in names] + ['an anime background scenery with no characters']
        self.texts = texts
        self.T = sig.text(texts)
        self.taste = None          # clip_taste.Taste, set by main() unless --no-taste

    # -- character references
    def build_char(self, cache):
        """Reference embeddings: the target's face crops and full images, and the other characters'."""
        tgt = self.tgt
        if tgt.mode != 'character':
            return
        refs = [fetch_image(u, cache) for u in tgt.ref_urls]
        refs = [r for r in refs if r is not None]
        negs = []
        for nm, u in tgt.neg:
            im = fetch_image(u, cache)
            if im is not None:
                negs.append((nm, im))
        if tgt.kind == 'group':
            fid = self.models.face_id
            self.char = {'kind': 'real',
                         't': [f for r in refs for f in (fid.feats(r) if fid else [])[:1]],
                         'n': [f for _, im in negs for f in (fid.feats(im) if fid else [])[:1]]}
            log(f'character refs: {len(self.char["t"])} target faces, {len(self.char["n"])} other members')
            return
        t_imgs, t_full, n_imgs, n_owner = [], [], [], []
        for r in refs:
            t_full.append(r)
            t_imgs += face_crops(r, self.models.anime_faces, 1) or [r]
        for k, (nm, im) in enumerate(negs):
            crops = face_crops(im, self.models.anime_faces, 1) or [im]
            n_imgs += crops
            n_owner += [k] * len(crops)
        te = self.sig.images(t_imgs) if t_imgs else np.zeros((0, 768), np.float32)
        tf = self.sig.images(t_full) if t_full else np.zeros((0, 768), np.float32)
        ne = self.sig.images(n_imgs) if n_imgs else np.zeros((0, 768), np.float32)
        self.char = {'kind': 'anime', 't_face': te, 't_full': tf, 'n_face': ne, 'n_owner': n_owner, 'extra': []}
        log(f'character refs: {len(te)} target crops ({len(refs)} images), {len(ne)} crops of {len(negs)} other characters')

    def char_score(self, c):
        """0-1: is the target character in this shot (and how clearly)."""
        ch = self.char
        if not ch:
            return None
        if ch['kind'] == 'real':
            fs = c.get('_face_ids') or []
            if not fs or not ch['t']:
                return 0.0
            best = 0.0
            for f in fs:
                st = max(float(f @ t) for t in ch['t'])
                sn = max([float(f @ t) for t in ch['n']] + [0.0])
                best = max(best, st if st >= sn else st - (sn - st))
            return float(np.clip((best - 0.2) / 0.3, 0, 1))
        fe = c.get('_face_emb')
        tf = ch['t_face'] if not len(ch['extra']) else np.concatenate([ch['t_face'], np.stack(ch['extra'])])
        face_part, margin = 0.0, -1.0
        if fe is not None and len(fe) and len(tf):
            st = (fe @ tf.T).max(1)
            sn = (fe @ ch['n_face'].T).max(1) if len(ch['n_face']) else np.zeros(len(fe))
            mg = st - sn
            j = int(np.argmax(mg + 0.5 * st))
            margin, sim = float(mg[j]), float(st[j])
            c['_char_sim'], c['_char_margin'] = round(sim, 3), round(margin, 3)
            face_part = float(np.clip((margin + 0.03) / 0.08, 0, 1)) * float(np.clip((sim - 0.6) / 0.15, 0, 1))
        # zero-shot name prompts over the whole frames
        lg = c['_logits']
        idx = self.extra_idx.get('char_names')
        name_p = 0.0
        if idx:
            z = lg[:, idx]
            p = np.exp(z - z.max(1, keepdims=True))
            p = p / p.sum(1, keepdims=True)
            name_p = float(p[:, 0].max())
        c['_name_p'] = round(name_p, 3)
        prior = {2.5: 0.1, 2: 0.07, 1: 0.03}.get(c.get('_level'), 0.0)
        name_s = float(np.clip((name_p - 0.3) / 0.6, 0, 1))
        m = 0.55 * name_s + 0.45 * face_part + prior
        if not any((c.get('anime_faces') or {}).values()):
            m *= 0.7                    # no face found: the character may not be on screen at all
        return float(np.clip(m, 0, 1))

    def refine_char(self, cands):
        """Self-training: the most confident face crops of this run join the target references (the
        AniList portrait and the show's frames differ in style)."""
        ch = self.char
        if not ch or ch['kind'] != 'anime':
            return 0
        pool = []
        for c in cands:
            fe = c.get('_face_emb')
            if fe is None or not len(fe) or c.get('_margin_idx') is None:
                continue
            if c.get('_name_p', 0) >= 0.9 and c.get('_level', 0) >= 2 and c.get('_char_sim', 0) >= 0.6:
                pool.append((c['_name_p'] + c['_char_sim'], fe[c['_margin_idx']]))
        pool.sort(key=lambda x: -x[0])
        add = [e for _, e in pool[:12]]
        # keep only crops that agree with each other (one identity, not a second character)
        if len(add) >= 3:
            A = np.stack(add)
            agree = (A @ A.T).mean(1)
            add = [a for a, g in zip(add, agree) if g >= np.median(agree) - 0.05]
        ch['extra'] = add
        return len(add)

    # -- per-candidate
    def embed(self, sa):
        """SigLIP embeddings for a source's best candidates: key frames plus face crops."""
        cs = sa.cands[:getattr(self.args, 'siglip_per_source', SIGLIP_PER_SOURCE)]
        imgs, owners = [], []
        for c in cs:
            for k in c['keys']:
                imgs.append(unjpg(sa.frames[k]))
                owners.append((id(c), 'f'))
        crops_for = {}
        if self.tgt.mode == 'character' and self.tgt.kind == 'anime':
            for c in cs:
                lst = []
                for k in c['keys']:
                    fr = unjpg(sa.frames[k])
                    boxes = (c.get('anime_faces') or {}).get(str(k)) or []
                    for b in boxes[:2]:
                        if b[2] * b[3] >= 0.004:
                            lst.append(crop_face(fr, b))
                crops_for[id(c)] = lst[:3]
                for im in lst[:3]:
                    imgs.append(im)
                    owners.append((id(c), 'c'))
        if self.tgt.mode == 'character' and self.tgt.kind == 'group' and self.models.face_id:
            for c in cs:
                fs = []
                for k in c['keys']:
                    fs += self.models.face_id.feats(unjpg(sa.frames[k]))
                c['_face_ids'] = fs
        E = self.sig.images(imgs)
        byc = {id(c): {'f': [], 'c': []} for c in cs}
        for (cid, kind), e in zip(owners, E):
            byc[cid][kind].append(e)
        for c in cs:
            c['_emb'] = np.stack(byc[id(c)]['f'])
            c['_face_emb'] = np.stack(byc[id(c)]['c']) if byc[id(c)]['c'] else None
        return cs

    def score(self, c, sa_meta, info):
        """Fills c with SigLIP features, gates and the final score. Returns reject reason or None."""
        tgt, a = self.tgt, self.args
        lg = self.sig.logits(c['_emb'], self.T)                 # (frames, prompts)
        c['_logits'] = lg
        # class logits per frame (best prompt), softmax across all quality classes
        cl = np.stack([lg[:, ix].max(1) for ix in self.cls_idx], 1)
        p = np.exp(cl - cl.max(1, keepdims=True))
        p = p / p.sum(1, keepdims=True)
        names = self.class_names
        pm = dict(zip(names, p.mean(0)))
        px = dict(zip(names, p.max(0)))
        good = [k for g, k, _ in self.classes if g == 'g']
        bad = [k for g, k, _ in self.classes if g == 'b']
        p_bad = float(sum(pm[k] for k in bad))

        def two(yes, no, reduce='mean', temp=1.0):
            """Frame-wise "yes rather than no" from the best prompt of each group. `temp` widens the
            sigmoid: SigLIP's logit gaps are tens of units, so temp=1 saturates to 0/1 and only
            answers "which one", while a larger temp keeps the margin and can be ranked."""
            y = lg[:, self.extra_idx[yes]].max(1)
            n_ = lg[:, self.extra_idx[no]].max(1)
            v = 1 / (1 + np.exp(-(y - n_) / temp))
            return float(v.mean() if reduce == 'mean' else v.max()), v

        pretty, _ = two('pretty', 'ugly')
        kind_p, _ = two('kind_yes', 'kind_no')
        unsafe_m, uv = two('unsafe', 'safe')
        gore_m, gv = two('gore', 'clean')
        unsafe_x, gore_x = float(uv.max()), float(gv.max())
        fr = np.stack([lg[:, self.extra_idx[k]].max(1) for k in ('f_face', 'f_medium', 'f_wide')], 1)
        fr = np.exp(fr - fr.max(1, keepdims=True))
        fr = (fr / fr.sum(1, keepdims=True)).mean(0)
        framing = ('closeup', 'medium', 'wide')[int(np.argmax(fr))]
        # tags: share among the good classes
        gs = np.array([pm[k] for k in TAG_CLASSES])
        gs = gs / max(1e-9, gs.sum())
        order = np.argsort(-gs)
        tags = [TAG_CLASSES[i] for i in order[:3] if gs[i] >= 0.18 or i == order[0]]
        big_face = 0.0
        if tgt.kind == 'anime':
            for k, bs in (c.get('anime_faces') or {}).items():
                for b in bs:
                    big_face = max(big_face, b[2] * b[3])
            c['faces'] = max([len(bs) for bs in (c.get('anime_faces') or {}).values()] + [0])
        else:
            for k, bs in (c.get('real_faces') or {}).items():
                for b in bs:
                    big_face = max(big_face, b[2] * b[3])
            c['faces'] = max([len(bs) for bs in (c.get('real_faces') or {}).values()] + [0])
        # the framing classes the references count (kpop.json shot_scale_mix, character.json
        # framing_targets): a face filling a good part of the frame is a close-up whatever SigLIP
        # says, a visible but smaller face makes it at least a medium shot.
        if big_face >= 0.12:
            framing = 'closeup'
        elif big_face >= 0.035 and framing == 'wide':
            framing = 'medium'
        if framing == 'closeup' and 'closeup' not in tags:
            tags.append('closeup')
        c.update({'tags': tags, 'framing': framing, 'anime': round(kind_p if tgt.kind == 'anime' else 1 - kind_p, 3),
                  'p_bad': round(p_bad, 3), 'p_talk': round(float(pm['talk']), 3), 'p_text': round(float(px['text']), 3),
                  'p_junk': round(float(pm['junk']), 3), 'unsafe': round(unsafe_m, 3), 'unsafe_max': round(unsafe_x, 3),
                  'gore': round(gore_m, 3), 'gore_max': round(gore_x, 3), 'pretty': round(pretty, 3),
                  'big_face': round(big_face, 4)})
        c['_good'] = {k: round(float(pm[k]), 3) for k in good}

        # ---- gates
        E = c['_emb']
        if len(E) >= 2:
            sims = E @ E.T
            c['scene_sim'] = round(float(sims[np.triu_indices(len(E), 1)].min()), 3)
            if c['scene_sim'] < 0.55:
                return 'mixed_scenes'
        strict = (tgt.mode == 'character' and tgt.adult is False) or bool(tgt.src and tgt.src.risky)
        c['swim_relaxed'] = False
        if (not strict and tgt.mode == 'character' and tgt.adult is True and tgt.kind == 'anime'
                and (framing == 'closeup' or c.get('faces', 0) >= 1)):
            # owner 2026-10-01: a swimsuit / pool shot of a CONFIRMED adult character is fine when the
            # picture is a face / upper body / whole scene (a face is on screen); only the swimsuit
            # prompt is dropped from the unsafe group, the other prompts keep the strict thresholds.
            um2, uv2 = two('unsafe_core', 'safe')
            ux2 = float(uv2.max())
            if (unsafe_x >= 0.93 or unsafe_m >= 0.75) and not (ux2 >= 0.93 or um2 >= 0.75):
                c['swim_relaxed'] = True
                c['unsafe_with_swim'] = [round(unsafe_m, 3), round(unsafe_x, 3)]
            unsafe_m, unsafe_x = um2, ux2
            c['unsafe'], c['unsafe_max'] = round(unsafe_m, 3), round(unsafe_x, 3)
        if unsafe_x >= (0.85 if strict else 0.93) or unsafe_m >= (0.6 if strict else 0.75):
            return 'unsafe'
        if (gore_m >= 0.6 or gore_x >= 0.85) and not ALLOW_GORE:
            return 'gore'
        if kind_p < 0.35:
            return 'not_anime' if tgt.kind == 'anime' else 'anime_in_live_action'
        if float(px['text']) >= 0.6:
            return 'text_siglip'
        if pm['junk'] >= 0.45 or pm['offkind'] >= 0.45:
            return 'junk'
        # crispness: the references are key-animation / 4K material (refs measure Laplacian variance
        # at 320 px of 380-2610 for character edits, median 1035 for song edits, >= 250 for dance)
        if c['sharp'] < (a.min_sharp if a.min_sharp is not None else 0):
            # effects animation (fire, explosions, energy) and motion-blurred action are soft by
            # nature but are exactly what the references cut to (labelled pools 2026-09-27: every
            # usable "soft" reject was vivid and frame-filling, every true soft shot was dull-coloured
            # or empty); a shot that is still detailed at 640 px is not an upscale either
            fx_soft = c['colorful'] >= 78 and c['area75'] >= 0.15 and c['sharp'] >= 120
            if not fx_soft and (c.get('sharp640') or 0.0) < 600:
                return 'soft'
        # dialogue / talking heads: speech (not singing) or a "talking" frame, on a still shot where
        # only a mouth moves
        music = sa_meta.get('music_source')
        has_speech = c['speech_ratio'] >= 0.3 and not music
        c['has_speech'] = bool(c['speech_ratio'] >= 0.3 and not (music and c['speech_ratio'] < 0.7))
        still = c['area75'] < 0.07 and c['motion_raw'] < 0.012
        mouth_only = c['area_med'] < 0.03 and c['motion_raw'] < 0.008
        # --allow-dialogue (fix round 3, calm / build-up sections of the template composer): voiced scenes pass these gates
        # (every other gate stays: text / subtitles, safety, sharpness, solo, ...); needs speech to be present
        mf = c.get('mouth_flap') or 0.0
        if getattr(a, 'allow_dialogue', False):
            if c['speech_ratio'] < 0.25:
                return 'no_dialogue'
        else:
            if has_speech and (still or pm['talk'] >= 0.25):
                return 'dialogue'
            if pm['talk'] >= 0.45 and (still or has_speech):
                return 'talking'
            if mouth_only and pm['talk'] >= 0.25 and not (framing == 'closeup' and pretty >= 0.6):
                return 'talking_still'
            mf = c.get('mouth_flap') or 0.0
            if (mf >= 0.2 and (c.get('mouth_frames') or 0) >= 8 and c['motion_raw'] < 0.02
                    and (pm['talk'] >= 0.2 or c['speech_ratio'] >= 0.3)):
                return 'mouth_flap'
            if tgt.mode != 'character' and pm['talk'] >= 0.55 and c['motion_raw'] < 0.015:
                return 'talking'
        if a.need_sfx and c['sfx_score'] < 0.15:
            return 'no_sfx'

        # ---- scores
        sharp_n = float(np.clip(math.log(max(c['sharp'], 1) / 100) / math.log(15), 0, 1))
        color_n = float(np.clip(c['colorful'] / 80, 0, 1))
        expo = float(np.clip(1 - abs(c['luma'] - 125) / 110, 0, 1))
        contrast_n = float(np.clip(c['contrast'] / 60, 0, 1))
        aesthetic = 0.45 * pretty + 0.25 * sharp_n + 0.15 * color_n + 0.15 * (0.5 * expo + 0.5 * contrast_n)
        c['aesthetic'] = round(aesthetic, 3)
        if aesthetic < (a.min_aesthetic or 0):
            return 'plain'
        mot_n = float(np.clip(c['motion_raw'] / 0.035, 0, 1))
        area_n = float(np.clip(c['area75'] / 0.3, 0, 1))
        motion_sc = 0.6 * mot_n + 0.4 * area_n
        if framing == 'closeup' and c['area75'] >= 0.04 and not still:
            motion_sc = max(motion_sc, 0.55)            # hair / light drifting over a close-up
        good_share = 1 - p_bad
        rel = 1.0
        if tgt.mode == 'mood' and 'query' in self.extra_idx:
            rel, _ = two('query', 'neutral')
        elif tgt.mode == 'dance':
            dshare = float(gs[TAG_CLASSES.index('dance')])
            rel = float(np.clip(0.6 * dshare / 0.35 + 0.4 * c['periodic'] / 0.35, 0, 1))
            if dshare < 0.12 and c['periodic'] < 0.15:
                return 'not_dance'
            if still:
                return 'static_dance'
        elif tgt.mode == 'character':
            cm = self.char_score(c)
            c['character_match'] = round(cm, 3)
            rel = cm
        # ---- is this the kind of moment the references cut to? (SCENES, from refs/*.json)
        moment, mv = two('moment', 'plain', 'mean', temp=8.0)
        c['moment'] = round(moment, 3)
        fpref = self.scene['framing']
        best_f = max(fpref.get(k, 0.0) for k in ('closeup', 'medium', 'wide'))
        frame_fit = 0.35 + 0.65 * fpref.get(framing, 0.0) / max(best_f, 1e-6)
        c['frame_fit'] = round(frame_fit, 3)
        scene_s = 0.68 * float(np.clip((moment - 0.35) / 0.45, 0, 1)) + 0.32 * frame_fit
        c['scene'] = round(scene_s, 3)
        mat = info.get('_mat') or {}
        bonus = (0.4 * mat.get('creditless', False) + 0.3 * mat.get('twixtor', False) + 0.2 * mat.get('raw', False)
                 + 0.3 * bool(info.get('_official')) + 0.2 * (min(sa_meta['W'], sa_meta['H']) >= 1080))
        score = (0.24 * good_share + 0.23 * aesthetic + 0.16 * motion_sc + 0.14 * rel + 0.15 * scene_s
                 + 0.08 * min(1.0, bonus))
        if tgt.mode == 'character':
            score *= 0.35 + 0.65 * rel
        if c['has_speech']:
            score *= 0.85
        if pm['talk'] >= 0.3:
            score *= 1 - 0.5 * min(1.0, (float(pm['talk']) - 0.3) / 0.5)
        if mf >= 0.1:
            score *= 0.85
        if a.need_sfx or a.with_audio:
            score += (0.25 if a.need_sfx else 0.05) * c['sfx_score']
        if still and framing != 'closeup':
            score *= 0.85
        c['base_score'] = round(float(score), 4)
        if self.taste is not None:
            why = self.taste_score(c, framing, still, mot_n, area_n, rel, score)
            if why:
                return why
        else:
            c['score'] = round(float(score), 4)
        c['_rel'] = round(rel, 3)
        if self.rules and clip_rules is not None:
            why = self.rules_score(c, lg)
            if why:
                return why
        return None

    def rules_score(self, c, lg):
        """The owner's clip rules (clip_rules): subject / solo / mood / talking-mouth terms from the
        SigLIP logits, hard rejects and a multiplier on the score. Every term lands in c['rules']."""
        r = self.rules
        idx = {k[2:]: v for k, v in self.extra_idx.items() if k.startswith('r_')}
        t = clip_rules.siglip_terms(c, lg, idx, 'anime' if self.tgt.kind == 'anime' else 'group')
        why, mult, tier, _ = clip_rules.judge(c, t, r)
        t['swim_relaxed'] = bool(c.get('swim_relaxed'))
        if c.get('unsafe_with_swim'):
            t['unsafe_with_swim'] = c['unsafe_with_swim']
        c['rules'] = t
        if why:
            t['rejected'] = why
            return 'rules_' + why
        fam = {'beauty': 'beauty', 'aura': 'aura'}.get(r.get('mood'))
        if fam and r.get('roster', True):
            title = (c.get('_info') or {}).get('title') or ''
            # character mode: the target only counts for shots that really show it (a weak match is just a source title)
            chn = self.tgt.char_name if self.tgt.mode == 'character' and (c.get('character_match') or 0) >= 0.6 else None
            rt = clip_rules.roster_terms(title, fam, chn)
            if rt:
                rm, rtier = clip_rules.roster_mult(rt, t.get('villain', 0.0))
                mult, tier = mult * rm, tier * rtier
                t['roster'] = rt
                t['roster_mult'] = round(rm, 3)
        c['score_pre_rules'] = c['score']
        c['score'] = round(float(c['score']) * mult, 4)
        if c.get('taste') is not None:
            c['taste_r'] = round(float(c['taste']) * tier, 4)
        return None

    def taste_score(self, c, framing, still, mot_n, area_n, rel, base):
        """The owner's bar: would a top edit channel cut to this shot? Reference-exemplar similarity,
        the attractiveness head, composition, vividness and crispness (clip_taste), blended with the
        old score, plus the gates for dull shots."""
        t = self.taste
        f = t.features(c['_emb'])
        faces = c.get('anime_faces') if self.tgt.kind == 'anime' else c.get('real_faces')
        comp = clip_taste.composition(faces, len(c.get('keys') or []) or 3)
        vivid = clip_taste.vividness(c['colorful'], c['contrast'])
        crisp = clip_taste.crispness(c.get('sharp_loc') or c['sharp'], c.get('sharp640') or 0.0)
        att = f.get('attract', 0.5)
        ex = f['ex']
        c.update({'exemplar': ex, 'exemplar_raw': f['ex_raw'], 'ex_role': f.get('ex_role'), 'attract': att,
                  'attract_raw': f.get('attract_raw'), 'composition': round(comp, 3), 'vivid': round(vivid, 3),
                  'crisp': round(crisp, 3)})
        # sakuga-ish: motion that fills the frame, or a close-up where something moves
        act = float(np.clip(0.6 * mot_n + 0.4 * area_n, 0, 1))
        # weights from the labelled pools of 2026-09-27 (editor labels on every scored candidate of
        # the JJK / Gojo / LE SSERAFIM / dance runs): exemplar similarity and the "moment" scene
        # score separate usable from dull best (AUC .80-.86), vividness and composition help, the
        # attractiveness head and crispness did not (AUC .45 and .28: crisp shots are the dull
        # scenery), so they carry no weight until the head is retrained on labelled pools.
        scene_s = float(c.get('scene') or 0.45)
        taste = 0.40 * ex + 0.20 * scene_s + 0.15 * vivid + 0.15 * comp + 0.10 * act
        if framing == 'wide' and c.get('big_face', 0.0) < 0.03 and act < 0.5:
            taste *= 0.85               # an empty wide (streets, stairs, small figures) is filler
        c['taste'] = round(float(taste), 4)
        # gates: a shot that neither looks like the references nor reads as attractive is dull,
        # a near-still one is dull unless the preset holds still heroes or it is a big sharp face
        # (a stare the planner can hold on: Sukuna / Gojo close-ups in the labelled pools)
        if att < TASTE_GATES['att_min'] and ex < TASTE_GATES['ex_min']:
            return 'dull'
        if (still and not t.still_ok and act < 0.15 and att < TASTE_GATES['still_att']
                and c.get('big_face', 0.0) < TASTE_GATES['still_face']):
            return 'dull_still'
        if crisp < TASTE_GATES['crisp_min']:
            return 'soft_upscale'
        score = TASTE_W * taste + (1 - TASTE_W) * base
        if self.tgt.mode == 'character':
            score *= 0.35 + 0.65 * rel
        c['score'] = round(float(score), 4)
        return None


def crop_face(img, b, grow=(0.45, 0.6, 0.45, 0.35)):
    """Face box grown to take in the hair (identity lives there for anime characters), square-ish."""
    h, w = img.shape[:2]
    x, y, bw, bh = b[0] * w, b[1] * h, b[2] * w, b[3] * h
    x0, y0 = max(0, int(x - grow[0] * bw)), max(0, int(y - grow[1] * bh))
    x1, y1 = min(w, int(x + bw + grow[2] * bw)), min(h, int(y + bh + grow[3] * bh))
    return img[y0:max(y0 + 2, y1), x0:max(x0 + 2, x1)].copy()


def face_crops(img, det, k):
    if det is None or img is None:
        return []
    return [crop_face(img, b) for b in det(img)[:k] if b[2] * b[3] >= 0.003]


# taste blend weight and gates (clip_taste; calibrated on labelled pools, see clip_taste.py)
TASTE_W = 0.7
TASTE_GATES = {'att_min': 0.15, 'ex_min': 0.2, 'still_att': 0.4, 'still_face': 0.10, 'crisp_min': 0.12}
# select(): shots whose taste is at least this are the "striking" tier; more of them are taken from
# one source (past the per-source cap) before any lower shot from another source is used. On the
# labelled pools this took the JJK list from 9 / 12 usable to 12 / 12 (10 striking) and the Gojo
# list to 12 / 12, because the filler had been dull wides from other packs.
TASTE_TIER = 0.70


# ---------------------------------------------------------------- selection and output

def select(cands, count, cap, dedupe_cos=0.95, framing_mix=None, tier=None):
    """The best shots, one round per source at a time (so a single pack cannot fill the whole
    list), never the same moment twice, at most `cap` per source (twice that only if short).

    `framing_mix` (the reference shot-scale mix of this edit type, SCENES[...]['framing']) is a
    soft quota: a framing may take at most its share + 25 % of the list while other framings are
    still available, so the pool comes out close to what the references cut and the planner is not
    handed twelve close-ups."""
    order = sorted(cands, key=lambda c: -c['score'])
    picked = []
    quota = None
    if framing_mix and count >= 4:
        quota = {k: max(1, int(math.ceil((framing_mix.get(k, 0.0) + 0.25) * count)))
                 for k in ('closeup', 'medium', 'wide')}

    def dup(c, p):
        if bin(c['hash'] ^ p['hash']).count('1') <= 8:
            return True
        return float((c['_emb'] @ p['_emb'].T).max()) >= dedupe_cos

    by_src = {}
    for c in order:
        by_src.setdefault(c['source_id'], []).append(c)
    srcs = sorted(by_src, key=lambda s: -by_src[s][0]['score'])
    # two tiers: the striking shots (taste >= TASTE_TIER) fill the list first, past the per-source
    # cap if need be; only then the rest (see TASTE_TIER). Without taste scores there is one tier.
    tiers = ([lambda c: c.get('taste') is not None and c.get('taste_r', c['taste']) >= tier, lambda c: True]
             if tier is not None and any(c.get('taste') is not None for c in cands) else [lambda c: True])
    for ok in tiers:
        for over in (False, True):          # first pass respects the quota, second fills the rest
            if len(picked) >= count:
                break
            for limit in list(range(1, cap + 1)) + [cap * 2, count]:
                for sid in srcs:
                    if len(picked) >= count:
                        break
                    if sum(1 for p in picked if p['source_id'] == sid) >= limit:
                        continue
                    for c in by_src[sid]:
                        if not ok(c) or c in picked or any(dup(c, p) for p in picked):
                            continue
                        if quota is not None and not over:
                            f = c.get('framing')
                            if f in quota and sum(1 for p in picked if p.get('framing') == f) >= quota[f]:
                                continue
                        picked.append(c)
                        break
                if len(picked) >= count:
                    break
        if len(picked) >= count:
            break
    return sorted(picked, key=lambda c: -c['score'])


def apply_motion_rules(cands, rejected, r, count, take):
    """Owner rule "clips must MOVE": measure the best `take` seconds of the best candidates at 1x
    (clip_rules.measure_motion), drop near-still windows for rule sets that ask for motion, scale the
    score by the motion term and remember the moving stretch for trim_to_best. Candidates beyond the
    measured top are dropped when motion is required (they were ranked too low to matter)."""
    need = bool(r.get('motion_min'))
    order = sorted(cands, key=lambda c: -c['score'])
    limit = max(36, count * 4)
    out = []
    t0 = time.time()
    for i, c in enumerate(order):
        if i >= limit:
            if not need:
                out.append(c)
            continue
        win = c['duration'] if not take else min(c['duration'], max(take, 1.0))
        mm = clip_rules.measure_motion(c['_path'], c['local'], c['duration'], c.get('anime_faces') or c.get('real_faces'),
                                       take=win)
        t = c.setdefault('rules', {})
        if mm is None:
            t['motion_beauty'] = None
            out.append(c)
            continue
        c['_mb_off'] = mm['window_off'] if (need and take and c['duration'] > take + 0.4) else None
        for k in ('motion_beauty', 'still_frac', 'subject_motion', 'secondary_motion', 'face_found', 'window_off'):
            t[k] = mm[k]
        why = clip_rules.motion_gate(mm, r)
        if why:
            t['rejected'] = why
            c['_why'] = 'rules_' + why
            bump('shot_rejected', 'rules_' + why)
            rejected.append(c)
            continue
        m = clip_rules.motion_mult(mm, r)
        c['score'] = round(float(c['score']) * m, 4)
        if c.get('taste_r') is not None:
            c['taste_r'] = round(float(c['taste_r']) * m, 4)
        t['motion_mult'] = round(m, 3)
        out.append(c)
    log(f'rules: motion measured on {min(limit, len(order))} windows in {time.time() - t0:.0f}s, '
        f'{len(order) - len(out) - max(0, len(order) - limit) * need} still / dropped')
    return out


_fx_mod = {}


def text_band_check(path):
    """(top, bottom) fractions the live renderer would have to trim off this file because of
    burned-in text: core/meme/edit_fx.text_bands, the same detector render_meme.py uses. Returns
    None when edit_fx is unavailable."""
    fx = _fx_mod.get('fx', 0)
    if fx == 0:
        try:
            import edit_fx as fx           # core/meme is already on sys.path
        except Exception as e:
            log(f'edit_fx unavailable ({e!r}): the text-band check on the cut files is off')
            fx = None
        _fx_mod['fx'] = fx
    if fx is None:
        return None
    try:
        t, b = fx.text_bands(path)
        return round(float(t), 3), round(float(b), 3)
    except Exception as e:
        log(f'text_bands failed on {os.path.basename(path)}: {e!r}')
        return None


def trim_to_best(c, take, min_dur):
    """Per-shot trimming: keep the best `take` seconds of the candidate (the stretch with the most
    motion and sound hits, lightly pulled to the middle, clip_taste.best_window) and move its
    times, peak and hits with it. Nothing happens when the shot is already short enough or the
    per-frame interest curve is missing (an old cache entry)."""
    ev = c.get('ev')
    if not take or not ev or clip_taste is None:
        return False
    take = max(min_dur, float(take))
    if c['duration'] <= take + 0.4:
        return False
    L = int(round(take * FPS_A))
    off, L = clip_taste.best_window(ev, L)
    if c.get('_mb_off') is not None:        # the owner's motion rule: the stretch that really moves at 1x
        off = int(max(0, min(len(ev) - L, round(c['_mb_off'] * FPS_A))))
    t0, t1 = off / FPS_A, (off + L) / FPS_A
    c['trim'] = [round(t0, 2), round(t1, 2), round(c['duration'], 2)]
    c['local'] = round(c['local'] + t0, 3)
    c['start'] = round(c['start'] + t0, 3)
    c['end'] = round(c['start'] + (t1 - t0), 3)
    c['duration'] = round(t1 - t0, 2)
    c['hits'] = [round(h - t0, 2) for h in c.get('hits') or [] if t0 <= h < t1]
    c['sfx_hits'] = [round(h - t0, 2) for h in c.get('sfx_hits') or [] if t0 <= h < t1]
    seg = ev[off:off + L]
    c['peak'] = round(int(np.argmax(seg)) / FPS_A, 2) if len(seg) else 0.0
    return True


def cut_shot(c, out_path, with_audio, box=None):
    """Cut the shot out of its source. `box` is the content box: the picture without letterbox bars
    and without the band a channel logo / watermark sits in. Cutting it away here means the file
    itself is clean, so nothing downstream has to remember to crop it."""
    amap = (['-map', '0:a:0?', '-c:a', 'aac', '-b:a', '192k', '-ac', '2', '-ar', '48000'] if with_audio else ['-an'])
    vf = 'scale=trunc(iw/2)*2:trunc(ih/2)*2'
    if box and (box[0] > 0.002 or box[1] > 0.002 or box[2] < 0.998 or box[3] < 0.998):
        x0, y0, x1, y1 = [float(v) for v in box]
        vf = (f'crop=w=trunc(iw*{max(0.05, x1 - x0):.6f}/2)*2:h=trunc(ih*{max(0.05, y1 - y0):.6f}/2)*2:'
              f'x=trunc(iw*{max(0.0, x0):.6f}/2)*2:y=trunc(ih*{max(0.0, y0):.6f}/2)*2,') + vf
    rc, _, err = cf.run(_gpu.convert(['ffmpeg', '-v', 'error', '-y'] + _gpu.hwdec(c['_path']) + ['-ss', f'{c["local"]:.3f}', '-i', c['_path'],
                         '-t', f'{c["duration"]:.3f}', '-map', '0:v:0'] + amap +
                        ['-sn', '-dn', '-vf', vf, '-c:v', 'libx264', '-preset', 'veryfast',
                         '-crf', '16', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', out_path], 'final'), 180)
    ok = rc == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 1000
    if not ok:
        log(f'cut failed {c["source_id"]}@{c["start"]}: {cf.err_line(err)}')
    return ok


def sheet(rows, path, title='', cell=256):
    """Contact sheet: one row per shot, three frames (20/50/80 %) plus labels."""
    if not rows:
        return
    th = int(cell * 9 / 16)
    lab = 44
    cols = 2
    rw = cols * (3 * cell + 12)
    nrows = -(-len(rows) // cols)
    img = np.full((nrows * (th + lab) + 30, rw, 3), 22, np.uint8)
    cv2.putText(img, title[:150], (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (240, 240, 240), 1, cv2.LINE_AA)
    for k, (frames, l1, l2, color) in enumerate(rows):
        r, q = divmod(k, cols)
        oy, ox = 30 + r * (th + lab), q * (3 * cell + 12)
        for j, fr in enumerate(frames[:3]):
            if fr is None:
                continue
            h, w = fr.shape[:2]
            s = min(cell / w, th / h)
            im = cv2.resize(fr, (max(1, int(w * s)), max(1, int(h * s))), interpolation=cv2.INTER_AREA)
            y, x = oy + (th - im.shape[0]) // 2, ox + j * cell + (cell - im.shape[1]) // 2
            img[y:y + im.shape[0], x:x + im.shape[1]] = im
        cv2.putText(img, l1[:120], (ox + 4, oy + th + 16), cv2.FONT_HERSHEY_SIMPLEX, 0.42, color, 1, cv2.LINE_AA)
        cv2.putText(img, l2[:120], (ox + 4, oy + th + 34), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (150, 200, 255), 1, cv2.LINE_AA)
    cv2.imwrite(path, img, [cv2.IMWRITE_JPEG_QUALITY, 88])


def file_frames(path, dur):
    """Three frames of a cut shot for the contact sheet. The seek goes AFTER -i: input seeking lands
    on the nearest key frame and hands back a black or half-decoded frame near the end of a short
    clip, which made good shots look broken on the sheet."""
    out = []
    for q in (0.2, 0.5, 0.8):
        rc, b, _ = cf.run(['ffmpeg', '-v', 'error', '-i', path, '-ss', f'{dur * q:.3f}', '-frames:v', '1',
                           '-vf', 'scale=512:-2', '-f', 'image2pipe', '-vcodec', 'png', '-'], 30)
        out.append(cv2.imdecode(np.frombuffer(b, np.uint8), cv2.IMREAD_COLOR) if b else None)
    return out


# ---------------------------------------------------------------- main

_flag_lock = threading.Lock()


def flag_channel(cache, info):
    """Remember an uploader that burns its name into its videos: its other videos are skipped."""
    key = info.get('channel_id') or info.get('channel') or ''
    if not key or info.get('_official'):
        return
    with _flag_lock:
        d = cache.get('info', '_watermark_channels') or {}
        d[key] = {'channel': info.get('channel') or '', 'video': info.get('id'), 'date': time.strftime('%Y-%m-%d')}
        cache.put('info', '_watermark_channels', d)


def flagged(cache, e):
    d = cache.get('info', '_watermark_channels') or {}
    return bool(d) and any(k and k in d for k in (e.get('channel_id'), e.get('channel')))


def local_info(path):
    mi = ffprobe(path)
    vid = 'local_' + sha(os.path.abspath(path))[:10]
    return {'id': vid, 'title': os.path.splitext(os.path.basename(path))[0], 'channel': 'local', 'channel_id': 'local',
            'duration': mi[3] if mi else 0, 'view_count': 0, '_local_path': os.path.abspath(path)}


def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding='utf-8', errors='replace')
        except AttributeError:
            pass
    ap = argparse.ArgumentParser(description='Find, vet and cut good shots for the edit pipelines')
    ap.add_argument('--mode', choices=MODES, required=True)
    ap.add_argument('--query', default='')
    ap.add_argument('--aliases', default='')
    ap.add_argument('--kind', choices=['anime', 'group'], default='anime')
    ap.add_argument('--count', type=int, default=12)
    ap.add_argument('--min-dur', type=float, default=2.0)
    ap.add_argument('--max-dur', type=float, default=8.0)
    ap.add_argument('--with-audio', action='store_true', help='keep the clip audio (AAC) and loosen the sound-hit detector')
    ap.add_argument('--need-sfx', action='store_true', help='only shots with a usable sound hit (sound edits)')
    ap.add_argument('--char-ref-images', default='', help='character mode: more reference images (URLs or paths)')
    ap.add_argument('--out-dir', required=True)
    ap.add_argument('--budget', type=float, default=540, help='seconds; stops starting new sources after it')
    ap.add_argument('--pace', type=float, default=float(os.environ.get('EDIT_LIBRARY_PACE') or 15),
                    help='minimum seconds between two video downloads')
    ap.add_argument('--probe-pace', type=float, default=None,
                    help='minimum seconds between metadata (-J) calls; default --pace')
    ap.add_argument('--page-pace', type=float, default=float(os.environ.get('EDIT_LIBRARY_PAGE_PACE') or 2.5),
                    help='minimum seconds between search-result pages')
    ap.add_argument('--no-global-pace', action='store_true',
                    help='do not share the cadence with the other processes (tests only)')
    ap.add_argument('--cache-dir', default=os.environ.get('EDIT_CACHE') or os.path.join(tempfile.gettempdir(), 'edit-cache'))
    ap.add_argument('--cache-max-mb', type=float, default=6000)
    ap.add_argument('--max-height', type=int, default=1080)
    ap.add_argument('--analyse-secs', type=float, default=ANA_MAX, help='seconds of each source analysed (CPU cost)')
    ap.add_argument('--max-sources', type=int, default=0, help='sources to download at most (default from --count)')
    ap.add_argument('--searches', type=int, default=6)
    ap.add_argument('--used', help='JSON of recently used source ids / shot keys (skipped)')
    ap.add_argument('--used-days', type=int, default=30)
    ap.add_argument('--block', help='JSON of blocked channels')
    ap.add_argument('--cookies')
    ap.add_argument('--js-runtimes', default='auto')
    ap.add_argument('--min-views', type=int, default=2000)
    ap.add_argument('--local', default='', help='comma list / globs of local videos to analyse as sources')
    ap.add_argument('--local-only', action='store_true', help='no YouTube at all')
    ap.add_argument('--allow-dialogue', action='store_true', help='calm-section pool: keep ONLY voiced scenes (speech >= .25), the dialogue gates are off')
    ap.add_argument('--seed', type=int)
    ap.add_argument('--workers', type=int, default=2)
    ap.add_argument('--sheet', help='contact sheet JPEG of the chosen shots (default DIR/sheet.jpg)')
    ap.add_argument('--reject-sheet', help='contact sheet of scored-and-rejected shots (tuning)')
    ap.add_argument('--debug-dir', help='per-source sheets of every window with its gate / score (tuning)')
    ap.add_argument('--dump-pool', help='tuning: write every scored candidate (features, embeddings, key frames) here')
    ap.add_argument('--siglip-per-source', type=int, default=16,
                    help='candidates per source that get the text / face / SigLIP checks (CPU cost)')
    ap.add_argument('--preset', default='',
                    help='the preset being built (core/edit/refs/presets-catalog.json id): its reference shots are '
                         'the exemplars candidates are compared with; default: every reference of the mode')
    ap.add_argument('--take-len', type=float, default=None,
                    help='trim each shot to its best moment of this many seconds (default: the preset take '
                         'length, or no trimming without --preset)')
    ap.add_argument('--no-taste', action='store_true',
                    help='score without the reference exemplars / attractiveness head (the pre-2026-09-26 ranking)')
    ap.add_argument('--no-rules', action='store_true',
                    help='ignore the owner clip rules (presets.CLIP_RULES / core/edit/clip_rules.py) for this run')
    ap.add_argument('--subject', choices=['female', 'any'], default=None,
                    help='clip rule: prefer female characters (default: the preset rule)')
    ap.add_argument('--solo', choices=['off', 'soft', 'strict'], default=None,
                    help='clip rule: one dominant subject; strict rejects gag / conversation / two-shot compositions')
    ap.add_argument('--mood', choices=['any', 'beauty', 'aura'], default=None,
                    help='clip rule: beauty = bright / warm / dreamy and no gloomy / sad / creepy; aura = intense / confident')
    ap.add_argument('--motion-min', type=float, default=None,
                    help='clip rule: reject windows whose residual motion at 1x (clip_rules.measure_motion) is below this 0-1 value')
    ap.add_argument('--pool-cache', action='store_true',
                    help='no search: score every cached analysis (ana/emb of this kind) as the pool; no network (tuning / review)')
    ap.add_argument('--no-crop', action='store_true',
                    help='do not crop the cut files to their content box (leaves bars / logos in, '
                         'and reports the box in "content_box" for the caller to apply)')
    ap.add_argument('--no-text-check', action='store_true',
                    help='skip the edit_fx.text_bands check on the cut files (tuning only)')
    ap.add_argument('--min-aesthetic', type=float, default=0.60,
                    help='reject shots below this combined look score (pretty + sharpness + colour + exposure)')
    ap.add_argument('--min-sharp', type=float, default=None,
                    help='Laplacian variance floor at 320 px (default 150 for anime, 110 for live action; '
                         'refs measure 380-2610 for character edits and >= 250 for dance)')
    a = ap.parse_args()
    global ALLOW_GORE
    if a.preset in GORE_OK_PRESETS:
        ALLOW_GORE = True
    a.rules = None
    if clip_rules is not None and not a.no_rules:
        a.rules = clip_rules.rules_for(a.preset, {'subject_pref': a.subject, 'solo': a.solo, 'mood': a.mood,
                                                  'motion_min': a.motion_min})
    a.min_dur = max(0.8, a.min_dur)
    a.max_dur = max(a.min_dur + 0.5, a.max_dur)
    if a.min_sharp is None:
        # refs/character.json quality: "sharpness lapvar at 320 px >= 400 (refs 380-2610)";
        # refs/dance.md quality_targets: shot sharpness median >= 250; kpop asks for a sharp face.
        a.min_sharp = {('anime', 'dance'): 250.0}.get((a.kind, a.mode), 400.0 if a.kind == 'anime' else 300.0)
    out_dir = os.path.abspath(a.out_dir)
    os.makedirs(out_dir, exist_ok=True)
    for old in glob.glob(os.path.join(out_dir, 'shot_*.mp4')):
        os.remove(old)
    manifest = os.path.join(out_dir, 'shots.json')

    def finish(shots, extra=None, code=None):
        stats = dict(STATS, rejected=dict(STATS['rejected']), shot_rejected=dict(STATS['shot_rejected']),
                     seconds=round(time.time() - T0, 1))
        doc = {'mode': a.mode, 'query': a.query, 'kind': a.kind, 'character': extra, 'shots': shots,
               'created': time.strftime('%Y-%m-%dT%H:%M:%S'), 'stats': stats}
        with open(manifest, 'w', encoding='utf-8') as f:
            json.dump(doc, f, ensure_ascii=False, indent=1)
        ok = len(shots) >= max(1, min(a.count, max(3, math.ceil(0.5 * a.count))))
        print(json.dumps({'ok': ok, 'count': len(shots), 'manifest': manifest, 'mode': a.mode, 'query': a.query,
                          'stats': stats}, ensure_ascii=False))
        sys.stdout.flush()
        os._exit(code if code is not None else (0 if shots else 1))

    tgt = Target(a)
    if tgt.mode == 'dance' and tgt.kind == 'anime' and not tgt.query:
        # named anime dances are niche uploads (the first run lost 41 of 77 results to the 2000
        # view floor); the frame gates (lowres, soft, text) still decide the quality
        a.min_views = min(a.min_views, 500)
    if tgt.refused:
        log(f'refused: {tgt.refused}')
        STATS['rejected']['refused'] += 1
        finish([], {'refused': tgt.refused})
    cache = Cache(os.path.abspath(a.cache_dir), a.cache_max_mb)
    if tgt.src is not None and tgt.src.risky:
        cf.NUDE_T.update(cf.NUDE_STRICT)        # fan-service-risk show: clip_finder's strict thresholds
        log(f'{tgt.query!r} is a fan-service-risk source: strict nudity checks')
    gaps = {'page': a.page_pace, 'probe': a.pace if a.probe_pace is None else a.probe_pace, 'download': a.pace}
    pacer = Pacer(gaps) if a.no_global_pace else GlobalPacer(gaps, cache.root, name=f'{a.mode}:{a.query[:20]}')
    rng = random.Random(a.seed if a.seed is not None else int(time.time() // 86400))
    used = cf._ids_from(cf.load_json(a.used), a.used_days) if a.used else set()
    block = cf.load_block(a.block)
    log(f'mode={a.mode} kind={a.kind} query={tgt.query!r} char={tgt.char_name!r} parent={tgt.parent!r} '
        f'adult={tgt.adult} refs={len(tgt.ref_urls)} count={a.count} dur={a.min_dur}-{a.max_dur}s budget={a.budget:.0f}s')

    holder = {}

    def load_all():
        try:
            holder['models'] = Models(need_sface=a.mode == 'character' and a.kind == 'group')
        except Exception as e:
            holder['err'] = e
    mt = threading.Thread(target=load_all, daemon=True)
    mt.start()

    def left():
        return a.budget - (time.time() - T0)

    # ---- candidates: local files and YouTube
    infos = []
    for spec in [x.strip() for x in a.local.split(',') if x.strip()]:
        for p in sorted(glob.glob(spec)) or ([spec] if os.path.exists(spec) else []):
            infos.append(local_info(p))
    tube = None
    if not a.local_only and not a.pool_cache:
        tube = Tube(a, cache, pacer)
        qs = tgt.queries(rng, a.searches)
        found, seen = [], set()
        for q, what in qs:
            if left() < 60:
                break
            res = tube.search(q, 25, cf.SP_VIDEO)
            log(f'search {q!r}: {len(res)}')
            for e in res:
                if e['id'] not in seen:
                    seen.add(e['id'])
                    e['_what'] = what
                    found.append(e)
        cands = []
        for e in found:
            why = None
            if e['id'] in used:
                why = 'used'
            elif cf.is_blocked(e, block):
                why = 'blocked'
            elif flagged(cache, e):
                why = 'watermark_channel'
            elif e.get('live'):
                why = 'live'
            elif e.get('duration') and (e['duration'] > MAX_SRC_DUR or e['duration'] < a.min_dur + 1):
                why = 'length'
            elif e.get('views') is not None and e['views'] < a.min_views:
                why = 'views'
            else:
                why = text_reason(e, tgt) or (None if relevant(e, tgt, False) else 'offtarget')
            if why:
                bump('rejected', why)
            else:
                cands.append(e)
        STATS['candidates'] = len(cands)
        cands.sort(key=lambda e: -pre_score(e, tgt, rng))
        log(f'{len(found)} results -> {len(cands)} candidates: '
            + '; '.join(f'{e["id"]} {e["title"][:40]!r}' for e in cands[:6]))
        want_src = a.max_sources or max(6, min(16, a.count // 2 + 5))
        probed = []
        for e in cands[:want_src * 3]:
            if left() < 150 or len(probed) >= int(want_src * 1.6):
                break
            info, why = tube.probe(e['id'])
            if not info:
                bump('rejected', why)
                continue
            why = None
            if (info.get('age_limit') or 0) > 0:
                why = 'age'
            elif info.get('availability') not in (None, 'public', 'unlisted'):
                why = 'unavailable'
            elif info.get('live_status') in ('is_live', 'is_upcoming', 'post_live'):
                why = 'live'
            elif cf.is_blocked(info, block):
                why = 'blocked'
            else:
                heights = [f.get('height') or 0 for f in info.get('formats') or [] if f.get('vcodec') not in (None, 'none')]
                widths = [f.get('width') or 0 for f in info.get('formats') or [] if f.get('vcodec') not in (None, 'none')]
                hmax, wmax = max(heights + [info.get('height') or 0]), max(widths + [info.get('width') or 0])
                info['_maxres'] = (wmax, hmax)
                if min(wmax, hmax) < 700 and not (hmax > wmax and hmax >= 960):
                    why = 'lowres'
                else:
                    why = text_reason(info, tgt, full=True) or (None if relevant(info, tgt, True) else 'offtarget')
            if why:
                bump('rejected', why)
                log(f'skip {info["id"]} ({why}): {(info.get("title") or "")[:60]!r}')
                continue
            bump('probe_ok')
            info['_what'] = e.get('_what')
            probed.append(info)
        for i in probed:
            src = tgt.src or tgt.char_src
            i['_official'] = bool(src and tgt.kind == 'group' and src.official(i))
            i['_mat'] = material(i)
            wmax, hmax = i.get('_maxres') or (0, 0)
            est = size_estimate(i, a.max_height) or 0
            i['_rank'] = (pre_score(i, tgt, rng, i['_official']) + 0.3 * (min(wmax, hmax) >= 1440) + 0.2 * bool(i.get('heatmap'))
                          - 0.5 * max(0.0, est - 150e6) / 100e6)
        probed.sort(key=lambda i: -i['_rank'])
        infos += cf.spread_channels(probed, series=False)[:want_src]
    for i in infos:
        i.setdefault('_mat', material(i))
        i['_level'] = tgt.level(i) if not i.get('_local_path') else 2
    log(f'{len(infos)} sources to analyse: ' + '; '.join(f'{i["id"]} {(i.get("title") or "")[:36]!r}' for i in infos[:12]))

    mt.join(timeout=300)
    if 'models' not in holder:
        log(f'models failed to load: {holder.get("err")!r}')
        finish([], code=1)
    models = holder['models']
    try:
        sig = Siglip(cache)
    except Exception as e:
        log(f'SigLIP unavailable ({e!r}): cannot vet shots, giving up')
        finish([], code=1)
    scorer = Scorer(tgt, sig, models, a, cache)
    if not a.no_taste and clip_taste is not None:
        try:
            scorer.taste = clip_taste.Taste(a.kind, a.mode, a.preset, a.need_sfx)
            log(f'taste: {scorer.taste.why}; attractiveness head {"on" if scorer.taste.head is not None else "off"}')
        except Exception as e:
            log(f'taste unavailable ({e!r}): the old ranking only')
    scorer.build_char(cache)
    char_info = None
    if tgt.mode == 'character':
        char_info = {'name': tgt.char_name, 'parent': tgt.parent, 'adult': tgt.adult,
                     'min_age': min_age((tgt.char or {}).get('age')), 'refs': tgt.ref_urls}

    # ---- download + analyse (workers) and score (main thread)
    results = queue.Queue()
    stop = threading.Event()
    if a.pool_cache:
        # review / tuning: every cached analysis of this kind is the pool (one per source, no network)
        seen_ids = set()
        for f in sorted(glob.glob(cache.path('ana', f'*__v{VERSION}_*_{a.kind}_*.json'))):
            ck = os.path.basename(f)[:-5]
            sid = ck.split('__')[0]
            ip = cache.path('info', sid + '.json')
            srcs = glob.glob(cache.path('src', sid + '__*'))
            srcs = [x for x in srcs if not x.endswith('.json')]
            if sid in seen_ids or not os.path.exists(ip) or not srcs or not os.path.exists(cache.path('emb', ck + '.npz')):
                continue
            hit = cache.get('ana', ck)
            if not hit:
                continue
            seen_ids.add(sid)
            info = cf.load_json(ip)
            info['_mat'] = material(info)
            info['_level'] = 2
            results.put(('cached', info, srcs[0], ck, hit))
        log(f'pool-cache: {results.qsize()} cached sources')
    good_by_src = Counter()

    def work(info):
        if stop.is_set() or left() < 45:
            return
        try:
            if info.get('_local_path'):
                path, offset = info['_local_path'], 0.0
                dur = float(info.get('duration') or 0)
                a0, a1 = heat_window(info, a.analyse_secs)
                ss, span = a0, a1 - a0
                if dur <= 0:
                    return
            else:
                section, a0, span = plan_source(info, a.analyse_secs, a.max_height)
                ck = (f'{info["id"]}__{a0:.0f}-{span:.0f}__v{VERSION}_{a.min_dur:.1f}_{a.max_dur:.1f}_{int(a.with_audio)}_{a.kind}'
                      f'_fc{int(a.mode == "character")}_r{int(bool(tgt.src and tgt.src.risky))}')
                hit = cache.get('ana', ck)
                path, offset = tube.download(info, section, timeout=max(40, min(240, left() - 30)))
                if not path:
                    bump('rejected', 'download')
                    return
                ss = max(0.0, a0 - offset)
                if hit and os.path.exists(cache.path('emb', ck + '.npz')):
                    results.put(('cached', info, path, ck, hit))
                    return
            sa = analyse(info, path, offset, ss, span, tgt, models, a)
            if sa.reason:
                bump('rejected', sa.reason)
                log(f'{info["id"]}: source rejected ({sa.reason})')
                if sa.meta.get('watermark_uploader') and not info.get('_local_path'):
                    flag_channel(cache, info)
                return
            sa.cache_key = None if info.get('_local_path') else ck
            results.put(('fresh', info, path, sa))
        except Exception as e:
            import traceback
            log(f'source {info.get("id")} failed: {e!r}\n{traceback.format_exc()[-800:]}')

    pool = cf_.ThreadPoolExecutor(max(1, a.workers))
    futs = []
    next_i = 0
    target_good = math.ceil(a.count * 1.7)
    all_cands, rejected_scored = [], []
    cap = {'source': 3, 'character': 4, 'mood': 2, 'dance': 3}[a.mode]

    def enough():
        if a.pool_cache:
            return False
        usable = sum(min(v, cap) for v in good_by_src.values())
        return usable >= target_good

    def launch():
        nonlocal next_i
        while next_i < len(infos) and sum(1 for f in futs if not f.done()) < max(1, a.workers) and not stop.is_set():
            futs.append(pool.submit(work, infos[next_i]))
            next_i += 1

    def consume(item):
        kind = item[0]
        info, path = item[1], item[2]
        if kind == 'cached':
            ck, hit = item[3], item[4]
            d = np.load(cache.path('emb', ck + '.npz'), allow_pickle=False)
            cs = hit['cands']
            meta = hit['meta']
            for j, c in enumerate(cs):
                c['_emb'] = d[f'e{j}'] if f'e{j}' in d.files else None
                c['_face_emb'] = d[f'c{j}'] if f'c{j}' in d.files else None
            cs = [c for c in cs if c['_emb'] is not None]
            bump('cached_sources')
            frames = None
        else:
            sa = item[3]
            t_e = time.time()
            cs = scorer.embed(sa)
            sa.meta.setdefault('timing', {})['siglip'] = round(time.time() - t_e, 1)
            log(f'{info["id"]} timing {sa.meta.get("timing")}')
            meta = sa.meta
            frames = sa.frames
            if sa.cache_key:
                try:
                    arrs = {}
                    for j, c in enumerate(cs):
                        arrs[f'e{j}'] = c['_emb'].astype(np.float32)
                        if c.get('_face_emb') is not None:
                            arrs[f'c{j}'] = c['_face_emb'].astype(np.float32)
                    np.savez(cache.path('emb', sa.cache_key + '.npz'), **arrs)
                    keep = [{k: v for k, v in c.items() if not k.startswith('_')} for c in cs]
                    cache.put('ana', sa.cache_key, {'cands': keep, 'meta': meta})
                except Exception as e:
                    log(f'analysis cache write failed: {e!r}')
        n_ok = 0
        for c in cs:
            c.update({'_path': path, '_info': info, '_meta': meta, '_frames': frames,
                      'source_id': info['id'], '_level': info.get('_level')})
            if f'{info["id"]}@{c["start"]:.1f}' in used:
                continue
            why = scorer.score(c, meta, info)
            if why:
                bump('shot_rejected', why)
                c['_why'] = why
                rejected_scored.append(c)
            else:
                all_cands.append(c)
                n_ok += 1
                if tgt.mode != 'character' or c.get('character_match', 0) >= 0.5:
                    good_by_src[info['id']] += 1
        log(f'{info["id"]} {(info.get("title") or "")[:44]!r}: {len(cs)} candidates scored, {n_ok} pass'
            + (' (cached)' if kind == 'cached' else ''))
        if a.debug_dir and kind == 'fresh':
            try:
                debug_sheet(item[3], cs, os.path.join(a.debug_dir, f'{info["id"]}.jpg'), tgt)
            except Exception as e:
                log(f'debug sheet failed: {e!r}')

    launch()
    while True:
        try:
            item = results.get(timeout=1.0)
            consume(item)
        except queue.Empty:
            pass
        if enough() or left() < 40:
            stop.set()
        if not stop.is_set():
            launch()
        if all(f.done() for f in futs) and results.empty() and (stop.is_set() or next_i >= len(infos)):
            break
    pool.shutdown(wait=False, cancel_futures=True)
    while not results.empty():
        consume(results.get())

    # ---- character: refine the references on this run's confident crops, re-score
    if tgt.mode == 'character' and scorer.char and scorer.char['kind'] == 'anime':
        pool_c = all_cands + [c for c in rejected_scored if c.get('_why') == 'not_character']
        for c in pool_c:
            fe = c.get('_face_emb')
            if fe is not None and len(fe) and scorer.char:
                tf = scorer.char['t_face']
                st = (fe @ tf.T).max(1) if len(tf) else np.zeros(len(fe))
                sn = (fe @ scorer.char['n_face'].T).max(1) if len(scorer.char['n_face']) else np.zeros(len(fe))
                c['_margin_idx'] = int(np.argmax(st - sn + 0.5 * st))
        added = scorer.refine_char(pool_c)
        if added:
            log(f'character refs refined with {added} confident crops from this run')
            for c in all_cands:
                why = scorer.score(c, c['_meta'], c['_info'])
                if why:
                    c['_why'] = why
            rejected_scored += [c for c in all_cands if c.get('_why')]
            all_cands = [c for c in all_cands if not c.get('_why')]
    if tgt.mode == 'character':
        thr = 0.5
        low = [c for c in all_cands if (c.get('character_match') or 0) < thr]
        for c in low:
            c['_why'] = 'not_character'
            bump('shot_rejected', 'not_character')
        rejected_scored += low
        all_cands = [c for c in all_cands if (c.get('character_match') or 0) >= thr]

    for c in all_cands:
        c['source_title'] = c['_info'].get('title') or ''
    if a.dump_pool:
        try:
            dump_pool(all_cands + rejected_scored, a.dump_pool, tgt)
        except Exception as e:
            log(f'pool dump failed: {e!r}')
    take_rule = a.take_len if a.take_len is not None else (scorer.taste.take if scorer.taste is not None else None)
    if scorer.rules and clip_rules is not None and all_cands:
        all_cands = apply_motion_rules(all_cands, rejected_scored, scorer.rules, a.count, take_rule)
    # a few spares: the cut files are checked once more for burned-in text and the failures replaced
    chosen = select(all_cands, a.count + (0 if a.no_text_check else max(2, a.count // 3)), cap,
                    framing_mix=scorer.scene['framing'], tier=TASTE_TIER if scorer.taste is not None else None)
    log(f'{len(all_cands)} shots pass; chosen {len(chosen)} from {len(set(c["source_id"] for c in chosen))} sources')

    # ---- cut and write
    shots = []
    for c in chosen:
        if len(shots) >= a.count:
            break
        k = len(shots)
        p = os.path.join(out_dir, f'shot_{k:03d}.mp4')
        box = c['_meta'].get('content_box') or c['_meta'].get('box') or [0, 0, 1, 1]
        cropped = not a.no_crop and (box[0] > 0.002 or box[1] > 0.002 or box[2] < 0.998 or box[3] < 0.998)
        take = a.take_len if a.take_len is not None else (scorer.taste.take if scorer.taste is not None else None)
        trim_to_best(c, take, a.min_dur)
        if not cut_shot(c, p, a.with_audio, box if cropped else None):
            continue
        # last gate, on the cut file itself: the renderer's own subtitle / caption band detector
        # (core/meme/edit_fx.text_bands). Anything it would have to trim away is not a clean shot.
        tb = None
        if not a.no_text_check:
            tb = text_band_check(p)
            if tb and max(tb) > 0:
                log(f'shot {c["source_id"]}@{c["start"]:.1f} has a burned-in text band {tb}: dropped')
                bump('shot_rejected', 'text_band')
                c['_why'] = 'text_band'
                rejected_scored.append(c)
                try:
                    os.remove(p)
                except OSError:
                    pass
                continue
        mi = ffprobe(p)
        info, meta = c['_info'], c['_meta']
        shots.append({
            'path': p, 'duration': round(mi[3], 3) if mi and mi[3] else c['duration'],
            'width': mi[0] if mi else meta['W'], 'height': mi[1] if mi else meta['H'],
            'fps': round(mi[2], 3) if mi else meta['fps'],
            'tags': c['tags'], 'motion': round(c['motion_raw'] / 0.05, 3), 'aesthetic': c['aesthetic'],
            'has_speech': bool(c.get('has_speech')), 'has_text': False,
            'sfx_hits': c['sfx_hits'], 'sfx_score': c['sfx_score'], 'peak': c['peak'], 'hits': c['hits'],
            'character_match': c.get('character_match'),
            'source_id': c['source_id'], 'source_title': info.get('title') or '',
            'source_channel': info.get('channel') or info.get('uploader') or '',
            'content_box': [0, 0, 1, 1] if cropped else box, 'source_box': box, 'cropped': bool(cropped),
            'score': c['score'], 'start': c['start'], 'end': c['end'], 'framing': c['framing'],
            'faces': c.get('faces', 0), 'speech_ratio': c['speech_ratio'], 'sharpness': c['sharp'],
            'luma': c['luma'], 'hue': c['hue'], 'colorful': c['colorful'], 'periodic': c['periodic'],
            'area': c['area75'], 'anime': c['anime'], 'pretty': c['pretty'],
            'moment': c.get('moment'), 'scene': c.get('scene'), 'frame_fit': c.get('frame_fit'),
            'text_band': list(tb or (0.0, 0.0)), 'mouth_flap': c.get('mouth_flap') or 0.0,
            'mouth_frames': c.get('mouth_frames') or 0, 'talk': c.get('p_talk'), 'big_face': c.get('big_face'),
            'creditless': bool(info.get('_mat', {}).get('creditless')), 'twixtor': bool(info.get('_mat', {}).get('twixtor')),
            'official': bool(info.get('_official')), 'source_kind': a.kind, 'has_audio': bool(a.with_audio and meta.get('has_audio')),
            'music_audio': bool(meta.get('music_source')), 'views': info.get('view_count') or 0,
            'source_channel_id': info.get('channel_id') or '', 'watermark_cropped': bool(meta.get('watermark', {}).get('boxes')),
            'exemplar': c.get('exemplar'), 'role': c.get('ex_role'), 'attract': c.get('attract'),
            'composition': c.get('composition'), 'vivid': c.get('vivid'), 'crisp': c.get('crisp'),
            'taste': c.get('taste'), 'base_score': c.get('base_score'), 'trim': c.get('trim'),
            'sharp_local': c.get('sharp_loc'), 'preset': a.preset or None,
            'swim_relaxed': bool(c.get('swim_relaxed')), 'unsafe_with_swim': c.get('unsafe_with_swim'),
            'rules': c.get('rules'), 'score_pre_rules': c.get('score_pre_rules'),
        })
    bump('kept', n=len(shots))
    if scorer.rules:
        try:    # every rule term of every candidate that reached the rules (picked or not)
            seen = {id(c) for c in chosen}
            rows = [{'source_id': c['source_id'], 'start': c['start'], 'score': c.get('score'), 'picked': id(c) in seen,
                     'why': c.get('_why'), 'rules': c.get('rules'),
                     'gates': {k: c.get(k) for k in ('unsafe', 'unsafe_max', 'gore', 'p_talk', 'p_text', 'sharp', 'aesthetic')}}
                    for c in (all_cands + rejected_scored)]
            with open(os.path.join(out_dir, 'rules.json'), 'w', encoding='utf-8') as f:
                json.dump({'rules': scorer.rules, 'candidates': rows}, f, ensure_ascii=False)
        except Exception as e:
            log(f'rules.json failed: {e!r}')
    sheet_path = a.sheet or os.path.join(out_dir, 'sheet.jpg')
    try:
        rows = []
        for k, s in enumerate(shots):
            rows.append((file_frames(s['path'], s['duration']),
                         f'{k} s{s["score"]:.2f} {"/".join(s["tags"])} {s["framing"]} m{s["motion"]:.2f} a{s["aesthetic"]:.2f}'
                         + (f' ch{s["character_match"]:.2f}' if s.get('character_match') is not None else '')
                         + f' mo{s.get("moment") or 0:.2f} sp{s["speech_ratio"]:.2f}'
                         + (f' ex{s["exemplar"]:.2f} at{s["attract"]:.2f} {s.get("role") or ""}' if s.get('exemplar') is not None else ''),
                         f'{s["source_id"]} {s["duration"]:.1f}s sh{s["sharpness"]:.0f} {s["source_title"][:48]}',
                         (120, 255, 160)))
        sheet(rows, sheet_path, f'{a.mode} {a.kind} "{a.query}": {len(shots)} shots')
        if a.reject_sheet:
            rej = sorted(rejected_scored, key=lambda c: -(c.get('score') or c.get('pre') or 0))[:40]
            rrows = []
            for c in rej:
                fr = c.get('_frames')
                imgs = [unjpg(fr[k]) for k in c['keys']] if fr else file_frames_src(c)
                rrows.append((imgs, f'{c["_why"]} talk{c.get("p_talk", 0):.2f} txt{c.get("p_text", 0):.2f} '
                                    f'sp{c["speech_ratio"]:.2f} ar{c["area75"]:.3f} m{c["motion_raw"]:.4f}'
                                    + (f' ch{c.get("character_match", 0) or 0:.2f}' if tgt.mode == 'character' else '')
                                    + f' u{c.get("unsafe", 0):.2f}',
                              f'{c["source_id"]}@{c["start"]:.1f} {(c["_info"].get("title") or "")[:50]}', (120, 160, 255)))
            sheet(rrows, a.reject_sheet, f'rejected after scoring ({len(rejected_scored)})')
    except Exception as e:
        log(f'sheet failed: {e!r}')
    keep = [c['_path'] for c in chosen]
    if not a.pool_cache:
        cache.prune(keep)
    log(f'stats: {json.dumps(dict(STATS, rejected=dict(STATS["rejected"]), shot_rejected=dict(STATS["shot_rejected"])))}')
    finish(shots, char_info)


def debug_sheet(sa, scored, path, tgt):
    """Every window of a source: cheap-gate rejects, text rejects, SigLIP-scored (reason or score),
    and the unscored rest, in time order."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    rows = []
    items = [(d['s'], d['keys'], f'X {d["why"]}', d['line'], (90, 90, 255)) for d in (sa.debug or [])]
    ids = {id(c) for c in scored}
    for c in sa.cands:
        if id(c) in ids:
            why = c.get('_why')
            l1 = (f'{"X " + why if why else "OK"} s{c.get("score", 0):.2f} {"/".join(c.get("tags", []))} '
                  f'talk{c.get("p_talk", 0):.2f} bad{c.get("p_bad", 0):.2f} pr{c.get("pretty", 0):.2f} '
                  f'u{c.get("unsafe", 0):.2f}/{c.get("unsafe_max", 0):.2f} g{c.get("gore", 0):.2f}/{c.get("gore_max", 0):.2f} '
                  f'an{c.get("anime", 0):.2f} mf{c.get("mouth_flap", 0) or 0:.2f}/{c.get("mouth_frames", 0) or 0}'
                  + (f' ch{c.get("character_match") or 0:.2f} n{c.get("_name_p", 0):.2f} '
                     f'sim{c.get("_char_sim", 0):.2f} mg{c.get("_char_margin", 0):.2f}' if tgt.mode == 'character' else ''))
            col = (90, 90, 255) if why else (120, 255, 160)
        else:
            l1, col = f'- unscored pre{c["pre"]:.2f}', (200, 200, 200)
        l2 = (f'{c["s"] / FPS_A:.1f}s m{c["motion_raw"]:.4f} ar{c["area75"]:.3f}/{c["area_med"]:.3f} sh{c["sharp"]:.0f} '
              f'sp{c["speech_ratio"]:.2f} per{c["periodic"]:.2f} fx{c["sfx_score"]:.2f} col{c["colorful"]:.0f}')
        items.append((c['s'], c['keys'], l1, l2, col))
    for s, keys, l1, l2, col in sorted(items, key=lambda x: x[0]):
        rows.append(([unjpg(sa.frames[k]) for k in keys if k in sa.frames], f'{s / FPS_A:.1f}s ' + l1, l2, col))
    sheet(rows, path, f'{sa.info["id"]} {(sa.info.get("title") or "")[:90]} beat{sa.meta.get("beat", 0):.2f} '
                      f'music{int(bool(sa.meta.get("music_source")))} wm{sa.meta.get("watermark", {}).get("boxes")}')


def dump_pool(cands, out_dir, tgt):
    """Tuning: every SigLIP-scored candidate (kept and rejected) with its features, embeddings and
    key frames, for labelling and for training / checking the attractiveness head."""
    os.makedirs(out_dir, exist_ok=True)
    rows, arrs = [], {}
    for j, c in enumerate(cands):
        fr = c.get('_frames')
        imgs = [unjpg(fr[k]) for k in c['keys'] if k in fr] if fr else file_frames_src(c)
        imgs = [im for im in imgs if im is not None]
        if imgs:
            h = 200
            strip = np.hstack([cv2.resize(im, (int(h * im.shape[1] / im.shape[0]), h)) for im in imgs])
            cv2.imwrite(os.path.join(out_dir, f'c{j:04d}.jpg'), strip, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if c.get('_emb') is not None:
            arrs[f'e{j}'] = np.asarray(c['_emb'], np.float32)
        rows.append({'j': j, 'id': f'{c["source_id"]}@{c["start"]:.1f}', 'why': c.get('_why'),
                     'title': (c.get('_info') or {}).get('title') or '', 'query': tgt.query, 'mode': tgt.mode,
                     'kind': tgt.kind, **{k: v for k, v in c.items() if not k.startswith('_') and k not in (
                         'kept', 'keys', 'real_faces', 'anime_faces', 'hash')}})
    np.savez_compressed(os.path.join(out_dir, 'pool.npz'), **arrs)
    with open(os.path.join(out_dir, 'pool.json'), 'w', encoding='utf-8') as f:
        json.dump(rows, f, ensure_ascii=False, indent=0, default=float)
    log(f'pool of {len(rows)} candidates dumped to {out_dir}')


def file_frames_src(c):
    out = []
    for q in (0.2, 0.5, 0.8):
        t = c['local'] + c['duration'] * q
        rc, b, _ = cf.run(['ffmpeg', '-v', 'error', '-ss', f'{t:.2f}', '-i', c['_path'], '-frames:v', '1',
                           '-vf', 'scale=512:-2', '-f', 'image2pipe', '-vcodec', 'png', '-'], 30)
        out.append(cv2.imdecode(np.frombuffer(b, np.uint8), cv2.IMREAD_COLOR) if b else None)
    return out


if __name__ == '__main__':
    main()
