#!/usr/bin/env python3
"""
Layered meme edit for "<song> acapella" shorts (Mr. WorldWideWebster).

Every acapella layer opens one more tile, and each tile is its own little
compilation of Asian funny / cute / anime / K-pop moments:

  vocals      1 tile, full screen
  + bass      2 tiles (the first one stays on top)
  + beatbox   3 tiles
  + harmony   4 tiles, 2x2

- Each tile is a mini-video: the clips of its section (~3 s each, on the beat).
  Once its section is over it replays on loop, so old tiles are familiar while
  the new one is fresh; the newcomer gets a glowing border, a flash and its
  layer label. All clips of a short share one theme (funny, cute, anime, ...).
- No clip is shown twice in one short.
- Each tile pulses on its own layer's hits (tile 2 on the bass notes, tile 3 on
  the beatbox, tile 4 on the harmony), so it looks like that tile "sings" its part.
- Clips are shown whole: fitted over a blurred copy of themselves, never cropped.
- Clip audio is never used; the acapella is the only soundtrack.

Inputs
  --audio      acapella mix from core/meme/acapella.py
  --timeline   its timeline.json (beats, sections, onsets per layer)
  --clips      clips.json from core/meme/clip_finder.py (strongest hook first)
  --title      top caption, e.g. "yara yara acapella"

    python core/meme/render_meme.py --audio acapella.wav --timeline timeline.json \
        --clips clips.json --title "yara yara acapella" --out meme.mp4
"""
import argparse
import json
import math
import os
import subprocess
import sys

import numpy as np
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'quiz'))
from render_quiz import text_layer, pill, put, with_shadow, clamp, ease_out_back  # noqa: E402
import quiz_emoji  # noqa: E402

W, H, FPS = 1080, 1920, 30
LAYERS = ['vocal', 'bass', 'beatbox', 'harmony']
LAYER_LABEL = {'vocal': ('VOCALS', '🎤'), 'bass': ('+ BASS', '🗣️'), 'beatbox': ('+ BEATBOX', '🥁'), 'harmony': ('+ HARMONY', '🎶')}
# tile i keeps its role (layer i); the layout grows as tiles are added
LAYOUT = {
    1: [(0, 0, W, H)],
    2: [(0, 0, W, H // 2), (0, H // 2, W, H // 2)],
    3: [(0, 0, W, H // 2), (0, H // 2, W // 2, H // 2), (W // 2, H // 2, W // 2, H // 2)],
    4: [(0, 0, W // 2, H // 2), (W // 2, 0, W // 2, H // 2), (0, H // 2, W // 2, H // 2), (W // 2, H // 2, W // 2, H // 2)],
}
GLOW = (255, 214, 0)


def probe_duration(path):
    r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', path],
                       capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 3.0


class ClipStream:
    """
    One clip at a fixed tile size, the WHOLE picture visible: fitted over a
    blurred, darkened copy of itself. Letterbox bars in the source
    (content_box from clip_finder) are trimmed first. Starts `offset` seconds
    in and loops.
    """

    def __init__(self, clip, w, h, offset=0.0):
        self.path, self.w, self.h = clip['path'], w, h
        self.box = clip.get('content_box') or [0, 0, 1, 1]
        self.offset = offset
        self.proc = None
        self.last = np.zeros((h, w, 3), dtype=np.uint8)
        self._open()

    def _open(self):
        x0, y0, x1, y1 = self.box
        trim = f'crop=iw*{x1 - x0:.4f}:ih*{y1 - y0:.4f}:iw*{x0:.4f}:ih*{y0:.4f},' if (x1 - x0) * (y1 - y0) < 0.97 else ''
        w, h = self.w, self.h
        vf = (f'[0:v]{trim}split[a][b];'
              f'[a]scale={w // 4}:{h // 4}:force_original_aspect_ratio=increase,crop={w // 4}:{h // 4},'
              f'boxblur=6:2,eq=brightness=-0.18:saturation=1.2,scale={w}:{h}[bg];'
              f'[b]scale={w}:{h}:force_original_aspect_ratio=decrease[fg];'
              f'[bg][fg]overlay=(W-w)/2:(H-h)/2,fps={FPS}')
        self.proc = subprocess.Popen(['ffmpeg', '-v', 'error', '-stream_loop', '-1', '-ss', f'{self.offset:.3f}',
                                      '-i', self.path, '-an', '-filter_complex', vf,
                                      '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'], stdout=subprocess.PIPE)

    def next(self):
        n = self.w * self.h * 3
        buf = self.proc.stdout.read(n)
        if len(buf) == n:
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


def dedupe(clips):
    """Drop repeated segments (same source and overlapping window)."""
    out = []
    for c in clips:
        if any(o.get('source_id') == c.get('source_id') and o.get('source_id')
               and min(o.get('end', 0), c.get('end', 0)) > max(o.get('start', 0), c.get('start', 0)) for o in out):
            continue
        out.append(c)
    return out


def schedule(timeline, clips):
    """
    Tile i gets its own mini-video: the clips that fill section i (about 3 s
    each, on the beat grid). After its section the tile replays that same
    mini-video on loop, so the old tiles are familiar while the new one is
    fresh. Returns (starts, [(start, period, [(offset, length, clip index)])]).
    """
    beats = np.array(timeline['beats'])
    secs = timeline['sections'][:4]
    queue = list(range(len(clips)))
    tiles = []
    for sec in secs:
        s0, s1 = float(sec['start']), float(sec['end'])
        period = s1 - s0
        k = max(1, min(len(queue) or 1, int(round(period / 3.0))))
        inner = [b - s0 for b in beats if s0 + 0.5 < b < s1 - 0.5]
        cuts = [0.0]
        for j in range(1, k):  # split points on the beat nearest to an even split
            target = period * j / k
            cuts.append(min(inner, key=lambda b: abs(b - target)) if inner else target)
        cuts = sorted(set(cuts)) + [period]
        parts = []
        for a, b in zip(cuts[:-1], cuts[1:]):
            c = queue.pop(0) if queue else (parts[-1][2] if parts else 0)
            parts.append((a, b - a, c))
        tiles.append((s0, period, parts))
    return [t[0] for t in tiles], tiles


def tile_frame_ref(tile, t):
    """(part index, seconds into that clip) a tile shows at time t (looping its mini-video)."""
    s0, period, parts = tile
    local = (t - s0) % period if period > 0 else 0.0
    for k, (a, length, _) in enumerate(parts):
        if local < a + length or k == len(parts) - 1:
            return k, max(0.0, local - a)


def label_image(layer):
    text, emo = LAYER_LABEL.get(layer, (layer.upper(), '🎵'))
    p = pill(text, 46, (255, 214, 0), max_w=520)
    e = quiz_emoji.image(emo, 58)
    if e is not None:
        row = Image.new('RGBA', (p.size[0] + 66, max(p.size[1], 58)), (0, 0, 0, 0))
        row.alpha_composite(p, (0, (row.size[1] - p.size[1]) // 2))
        row.alpha_composite(e, (p.size[0] + 8, (row.size[1] - 58) // 2))
        p = row
    return with_shadow(p, blur=8, offset=(0, 5))


def title_image(title, emoji):
    t = text_layer(title.lower(), 76, stroke=9, max_w=940)
    emo = [quiz_emoji.image(e, 80) for e in (emoji or '').split(',') if e] if emoji != 'none' else []
    emo = [e for e in emo if e is not None]
    if not emo:
        return t
    row = Image.new('RGBA', (t.size[0] + sum(e.size[0] + 10 for e in emo), max(t.size[1], 86)), (0, 0, 0, 0))
    row.alpha_composite(t, (0, (row.size[1] - t.size[1]) // 2))
    x = t.size[0] + 10
    for e in emo:
        row.alpha_composite(e, (x, (row.size[1] - e.size[1]) // 2))
        x += e.size[0] + 10
    return row


def pulse_at(t, onsets):
    """1 right after a hit, decaying to 0 within ~0.2 s."""
    if not onsets:
        return 0.0
    k = np.searchsorted(onsets, t, side='right') - 1
    if k < 0:
        return 0.0
    return math.exp(-(t - onsets[k]) / 0.09)


def render(args):
    timeline = json.load(open(args.timeline, encoding='utf-8'))
    manifest = json.load(open(args.clips, encoding='utf-8'))
    clips = dedupe(manifest['clips'] if isinstance(manifest, dict) else manifest)
    if len(clips) < 4:
        raise SystemExit('need at least 4 different clips')
    for c in clips:
        if not c.get('duration'):
            c['duration'] = (c.get('end', 0) - c.get('start', 0)) or probe_duration(c['path'])
    dur = float(timeline.get('duration') or timeline['sections'][-1]['end'])
    starts, tiles = schedule(timeline, clips)
    onsets = {k: sorted(v) for k, v in (timeline.get('onsets') or {}).items()}

    title = title_image(args.title, args.emoji) if args.title else None
    labels = [label_image(LAYERS[i]) for i in range(len(starts))]
    wm = text_layer(args.watermark, 38, fill=(255, 255, 255, 170), stroke=3, stroke_fill=(0, 0, 0, 120)) if args.watermark else None

    cmd = ['ffmpeg', '-y', '-v', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{W}x{H}', '-r', str(FPS), '-i', '-',
           '-i', args.audio, '-c:v', 'libx264', '-preset', 'medium', '-crf', '20', '-pix_fmt', 'yuv420p',
           '-c:a', 'aac', '-b:a', '192k', '-shortest', '-movflags', '+faststart', args.out]
    enc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    streams = {}  # tile -> (entry index, size, ClipStream)
    for f in range(int(dur * FPS)):
        t = f / FPS
        n = max(1, sum(1 for s in starts if s <= t))
        layout = LAYOUT[n]
        frame = Image.new('RGBA', (W, H), (0, 0, 0, 255))
        for i in range(n):
            x, y, w, h = layout[i]
            k, into = tile_frame_ref(tiles[i], t)
            ci = tiles[i][2][k][2]
            # a new part (or a loop back to the start, or a layout change) reopens the stream there
            if i not in streams or streams[i][0] != k or streams[i][1] != (w, h) or into < 1.0 / FPS:
                if i in streams:
                    streams[i][2].close()
                streams[i] = (k, (w, h), ClipStream(clips[ci], w, h, offset=into % max(0.5, float(clips[ci]['duration']))))
            tile = Image.fromarray(streams[i][2].next())
            s = 1.0 + 0.045 * pulse_at(t, onsets.get(LAYERS[i], []))
            since = t - starts[i]
            if since < 0.25:  # the new tile pops in
                s *= 0.86 + 0.14 * ease_out_back(since / 0.25)
            if abs(s - 1) > 0.002:
                sw, sh = int(w * s), int(h * s)
                big = tile.resize((sw, sh), Image.BILINEAR)
                if s > 1:
                    tile = big.crop(((sw - w) // 2, (sh - h) // 2, (sw - w) // 2 + w, (sh - h) // 2 + h))
                else:
                    bg = Image.new('RGB', (w, h), (0, 0, 0))
                    bg.paste(big, ((w - sw) // 2, (h - sh) // 2))
                    tile = bg
            frame.paste(tile, (x, y))
        d = ImageDraw.Draw(frame)
        for i in range(n):
            x, y, w, h = layout[i]
            d.rectangle([x, y, x + w - 1, y + h - 1], outline=(0, 0, 0, 255), width=4)
        # the newest tile announces itself: flash on that tile, glowing border, label
        i_new = n - 1
        since_new = t - starts[i_new]
        if n > 1 or since_new < 1.8:
            x, y, w, h = layout[i_new]
            if 0 < since_new < 0.15:
                flash = Image.new('RGBA', (w, h), (255, 255, 255, int(120 * (1 - since_new / 0.15))))
                frame.alpha_composite(flash, (x, y))
            if n > 1 and since_new < 2.4:
                glow = (0.55 + 0.45 * math.sin(since_new * 2 * math.pi * 2.2)) * clamp((2.4 - since_new) / 0.6)
                for k in range(3):
                    d.rectangle([x + k * 5, y + k * 5, x + w - 1 - k * 5, y + h - 1 - k * 5],
                                outline=GLOW + (int(255 * glow * (1 - k * 0.3)),), width=6)
        if since_new < 1.8:
            put(frame, labels[i_new], x + w / 2, y + (h * 0.78 if n == 1 else h - 90),
                s=max(0.01, ease_out_back(since_new / 0.25)), a=clamp((1.8 - since_new) / 0.3))
        if title is not None:
            put(frame, title, W / 2, 250)
        if wm is not None:
            put(frame, wm, W / 2, 150)
        enc.stdin.write(frame.convert('RGB').tobytes())
    for *_, st in streams.values():
        st.close()
    enc.stdin.close()
    if enc.wait() != 0:
        raise SystemExit('ffmpeg failed')
    used = [clips[c] for _, _, parts in tiles for _, _, c in parts]
    return {'duration': round(dur, 2), 'cuts': len(used), 'clipsUsed': [c.get('source_id') for c in used],
            'channelsUsed': sorted({c.get('source_channel') for c in used if c.get('source_channel')})}


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
