#!/usr/bin/env python3
"""
Beat-cut meme edit for "<song> acapella" shorts (Mr. WorldWideWebster).

Inputs
  --audio      acapella mix from core/meme/acapella.py
  --timeline   its timeline.json (beats, downbeats, sections with active layers)
  --clips      clips.json from core/meme/clip_finder.py
  --title      top caption, e.g. "yara yara acapella"

Layout follows the layers so the "satisfying" build-up is visible:
  vocals only   -> one clip, full screen, new clip every 2 beats
  + bass        -> two clips stacked
  + beatbox     -> 2x2 grid
  + harmony     -> 2x2 grid, a cut on every beat, zoom punches on downbeats
Clip audio is never used; the acapella is the only soundtrack.

    python core/meme/render_meme.py --audio acapella.wav --timeline timeline.json \
        --clips clips.json --title "yara yara acapella" --out meme.mp4
"""
import argparse
import json
import math
import os
import random
import subprocess
import sys

import numpy as np
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'quiz'))
from render_quiz import text_layer, pill, put, with_shadow, clamp, ease_out_back  # noqa: E402
import quiz_emoji  # noqa: E402

W, H, FPS = 1080, 1920, 30
LAYER_LABEL = {'vocal': ('VOCALS', '🎤'), 'bass': ('+ BASS', '🗣️'), 'beatbox': ('+ BEATBOX', '🥁'), 'harmony': ('+ HARMONY', '🎶')}
GRID = {1: [(0, 0, W, H)], 2: [(0, 0, W, H // 2), (0, H // 2, W, H // 2)],
        4: [(0, 0, W // 2, H // 2), (W // 2, 0, W // 2, H // 2), (0, H // 2, W // 2, H // 2), (W // 2, H // 2, W // 2, H // 2)]}


class ClipStream:
    """Streams frames of one clip at a fixed size (cover-crop), looping if it runs out."""

    def __init__(self, path, w, h, start=0.0):
        self.path, self.w, self.h, self.start = path, w, h, start
        self.proc = None
        self.last = np.zeros((h, w, 3), dtype=np.uint8)
        self._open()

    def _open(self):
        vf = f'scale={self.w}:{self.h}:force_original_aspect_ratio=increase,crop={self.w}:{self.h},fps={FPS}'
        self.proc = subprocess.Popen(['ffmpeg', '-v', 'error', '-ss', f'{self.start:.3f}', '-i', self.path, '-an', '-vf', vf,
                                      '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'], stdout=subprocess.PIPE)

    def next(self):
        n = self.w * self.h * 3
        buf = self.proc.stdout.read(n)
        if len(buf) < n:  # clip ended: restart from its start (short loop reads as a meme "replay")
            self.close()
            self._open()
            buf = self.proc.stdout.read(n)
            if len(buf) < n:
                return self.last
        self.last = np.frombuffer(buf, dtype=np.uint8).reshape(self.h, self.w, 3)
        return self.last

    def close(self):
        if self.proc:
            try:
                self.proc.stdout.close()
                self.proc.kill()
            except Exception:
                pass
            self.proc = None


def plan_cuts(timeline, clips, rng):
    """For every section, a list of (t0, t1, tile_count, [clip index per tile])."""
    beats = timeline['beats']
    sections = timeline['sections']
    order = list(range(len(clips)))
    rng.shuffle(order)
    k = 0

    def next_clip():
        nonlocal k
        c = order[k % len(order)]
        k += 1
        return c

    cuts = []
    for si, sec in enumerate(sections):
        layers = sec['layers']
        tiles = 1 if len(layers) <= 1 else 2 if len(layers) == 2 else 4
        every = 2 if len(layers) < 4 else 1  # beats per cut
        sb = [b for b in beats if sec['start'] - 1e-3 <= b < sec['end']] or [sec['start']]
        bounds = [sec['start']] + [sb[j] for j in range(every, len(sb), every)] + [sec['end']]
        bounds = sorted(set(round(x, 3) for x in bounds))
        for a, b in zip(bounds[:-1], bounds[1:]):
            if b - a < 0.15:
                continue
            cuts.append({'t0': a, 't1': b, 'tiles': tiles, 'clips': [next_clip() for _ in range(tiles)], 'section': si})
    return cuts


def render(args):
    timeline = json.load(open(args.timeline, encoding='utf-8'))
    manifest = json.load(open(args.clips, encoding='utf-8'))
    clips = manifest['clips'] if isinstance(manifest, dict) else manifest
    if len(clips) < 4:
        raise SystemExit('need at least 4 clips')
    rng = random.Random(args.seed)
    dur = float(timeline.get('duration') or timeline['sections'][-1]['end'])
    cuts = plan_cuts(timeline, clips, rng)
    downbeats = timeline.get('downbeats') or timeline['beats'][::4]
    section_starts = [s['start'] for s in timeline['sections']]

    # static overlays
    title = None
    if args.title:
        title = text_layer(args.title.lower(), 78, stroke=9, max_w=960)
        emo = [quiz_emoji.image(e, 84) for e in (args.emoji or '😭✌️').split(',') if e] if args.emoji != 'none' else []
        emo = [e for e in emo if e is not None]
        if emo:
            wsum = title.size[0] + sum(e.size[0] + 10 for e in emo)
            row = Image.new('RGBA', (wsum, max(title.size[1], 90)), (0, 0, 0, 0))
            row.alpha_composite(title, (0, (row.size[1] - title.size[1]) // 2))
            x = title.size[0] + 10
            for e in emo:
                row.alpha_composite(e, (x, (row.size[1] - e.size[1]) // 2))
                x += e.size[0] + 10
            title = row
    labels = []
    for sec in timeline['sections']:
        new = [l for l in sec['layers'] if l not in (labels[-1][1] if labels else [])]
        name = new[-1] if new else sec['layers'][-1]
        text, emo = LAYER_LABEL.get(name, (name.upper(), '🎵'))
        p = pill(text, 50, (255, 214, 0), max_w=600)
        e = quiz_emoji.image(emo, 64)
        if e is not None:
            row = Image.new('RGBA', (p.size[0] + 74, max(p.size[1], 64)), (0, 0, 0, 0))
            row.alpha_composite(p, (0, (row.size[1] - p.size[1]) // 2))
            row.alpha_composite(e, (p.size[0] + 10, (row.size[1] - 64) // 2))
            p = row
        labels.append((with_shadow(p, blur=8, offset=(0, 5)), sec['layers'], sec['start']))
    wm = text_layer(args.watermark, 38, fill=(255, 255, 255, 170), stroke=3, stroke_fill=(0, 0, 0, 120)) if args.watermark else None

    cmd = ['ffmpeg', '-y', '-v', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{W}x{H}', '-r', str(FPS), '-i', '-',
           '-i', args.audio, '-c:v', 'libx264', '-preset', 'medium', '-crf', '20', '-pix_fmt', 'yuv420p',
           '-c:a', 'aac', '-b:a', '192k', '-shortest', '-movflags', '+faststart', args.out]
    enc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    streams = {}
    cur = -1
    n_frames = int(dur * FPS)
    for f in range(n_frames):
        t = f / FPS
        while cur + 1 < len(cuts) and t >= cuts[cur + 1]['t0']:
            cur += 1
            for s in streams.values():
                s.close()
            streams = {}
            c = cuts[cur]
            for ti, (x, y, w, h) in enumerate(GRID[c['tiles']]):
                clip = clips[c['clips'][ti]]
                streams[ti] = ClipStream(clip['path'], w, h, start=0.0)
        c = cuts[max(cur, 0)]
        frame = np.zeros((H, W, 3), dtype=np.uint8)
        for ti, (x, y, w, h) in enumerate(GRID[c['tiles']]):
            if ti in streams:
                frame[y:y + h, x:x + w] = streams[ti].next()
        img = Image.fromarray(frame).convert('RGBA')
        if c['tiles'] > 1:  # thin separators between tiles
            d = ImageDraw.Draw(img)
            if c['tiles'] >= 2:
                d.rectangle([0, H // 2 - 3, W, H // 2 + 3], fill=(0, 0, 0, 255))
            if c['tiles'] == 4:
                d.rectangle([W // 2 - 3, 0, W // 2 + 3, H], fill=(0, 0, 0, 255))
        # zoom punch on downbeats once the beat is in
        full = len(timeline['sections'][c['section']]['layers']) >= 3
        since_db = min([t - d for d in downbeats if d <= t] or [9])
        if full and since_db < 0.18:
            z = 1 + 0.07 * (1 - since_db / 0.18)
            zw, zh = int(W * z), int(H * z)
            img = img.resize((zw, zh), Image.BILINEAR).crop(((zw - W) // 2, (zh - H) // 2, (zw - W) // 2 + W, (zh - H) // 2 + H))
        # white flash when a layer comes in
        since_sec = min([t - s for s in section_starts if s <= t] or [9])
        if 0 < since_sec < 0.12 or (since_sec == 0 and t > 0):
            a = int(110 * (1 - since_sec / 0.12))
            img.alpha_composite(Image.new('RGBA', (W, H), (255, 255, 255, a)))
        # overlays
        if title is not None:
            put(img, title, W / 2, 250)
        for lab, _, t0 in labels:
            if t0 <= t < t0 + 1.6:
                s = ease_out_back((t - t0) / 0.25)
                put(img, lab, W / 2, 400, s=max(0.01, s), a=clamp((t0 + 1.6 - t) / 0.3))
        if wm is not None:
            put(img, wm, W / 2, 150)
        enc.stdin.write(img.convert('RGB').tobytes())
    for s in streams.values():
        s.close()
    enc.stdin.close()
    if enc.wait() != 0:
        raise SystemExit('ffmpeg failed')
    used = sorted({i for c in cuts for i in c['clips']})
    return {'duration': round(dur, 2), 'cuts': len(cuts), 'clipsUsed': [clips[i].get('source_id') for i in used],
            'channelsUsed': sorted({clips[i].get('source_channel') for i in used if clips[i].get('source_channel')})}


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument('--audio', required=True)
    ap.add_argument('--timeline', required=True)
    ap.add_argument('--clips', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--title', default='')
    ap.add_argument('--emoji', default='😭,✌️', help='comma-separated emoji after the title, or "none"')
    ap.add_argument('--watermark', default=os.environ.get('WATERMARK', ''))
    ap.add_argument('--seed', type=int, default=None)
    args = ap.parse_args()
    info = render(args)
    print(json.dumps({'ok': True, 'path': os.path.abspath(args.out), **info}, ensure_ascii=False))


if __name__ == '__main__':
    main()
