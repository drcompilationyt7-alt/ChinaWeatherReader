#!/usr/bin/env python3
"""
Layered meme edit for "<song> acapella" shorts (Mr. WorldWideWebster).

Every acapella layer brings in one new tile with one clip whose vibe matches
the layer, and every clip keeps looping once it is on screen:

  vocals      tile 1: a dance clip, full screen
  + bass      tile 2: a cool / aura clip        (2 tiles)
  + beatbox   tile 3: a fight / action clip     (3 tiles)
  + harmony   tile 4: a cute clip               (2x2)

- A clip plays for its section and then loops with the same length (whole
  bars), so the viewer has already seen the old tiles while the new one is fresh.
- Each clip starts at the offset that puts its peak (the punch, the dance hit)
  on a beat; fight clips land on a drum hit. The loop is a whole number of
  bars, so the hit stays on the beat every time round.
- A quick glow marks only the tile that has just appeared; the big layer label
  pops up on top; all tiles bounce together on the downbeat.
- Clips are shown whole: fitted over a blurred copy of themselves, never cropped.
- Clip audio is never used; the acapella is the only soundtrack.

Inputs
  --audio      acapella mix from core/meme/acapella.py
  --timeline   its timeline.json (beats, downbeats, sections, onsets per layer)
  --clips      clips.json from core/meme/clip_finder.py (vibe, peak, hook-ranked)
  --title      top caption, e.g. "yara yara acapella"

    python core/meme/render_meme.py --audio acapella.wav --timeline timeline.json \
        --clips clips.json --title "yara yara acapella" --out meme.mp4
"""
import argparse
import json
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
VIBE = {'vocal': 'dance', 'bass': 'cool', 'beatbox': 'fight', 'harmony': 'cute'}
LAYER_LABEL = {'vocal': ('VOCALS', '🎤'), 'bass': ('+ BASS', '🗣️'), 'beatbox': ('+ BEATBOX', '🥁'), 'harmony': ('+ HARMONY', '🎶')}
LAYOUT = {
    1: [(0, 0, W, H)],
    2: [(0, 0, W, H // 2), (0, H // 2, W, H // 2)],
    3: [(0, 0, W, H // 2), (0, H // 2, W // 2, H // 2), (W // 2, H // 2, W // 2, H // 2)],
    4: [(0, 0, W // 2, H // 2), (W // 2, 0, W // 2, H // 2), (0, H // 2, W // 2, H // 2), (W // 2, H // 2, W // 2, H // 2)],
}
GLOW = (255, 214, 0)
GLOW_SEC = 0.5


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
    in and loops the file if it runs out.
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


def choose_clips(clips, n):
    """One clip per layer, matching the layer's vibe (manifest order = hook rank); different source videos."""
    chosen, used_src = [], set()
    for i in range(n):
        want = VIBE[LAYERS[i]]
        pool = [c for c in clips if c.get('vibe') == want] + [c for c in clips if c.get('vibe') != want]
        pick = next((c for c in pool if (c.get('source_id') or c['path']) not in used_src and c not in chosen), None)
        if pick is None:
            pick = next((c for c in clips if c not in chosen), clips[i % len(clips)])
        chosen.append(pick)
        used_src.add(pick.get('source_id') or pick['path'])
    return chosen


def plan(timeline, clips):
    """Per tile: section start, loop period (its section's length) and the clip offset that puts the peak on a beat."""
    beats = np.array(timeline['beats'])
    onsets = timeline.get('onsets') or {}
    sections = timeline['sections'][:4]
    chosen = choose_clips(clips, len(sections))
    tiles = []
    for i, (sec, clip) in enumerate(zip(sections, chosen)):
        s0, s1 = float(sec['start']), float(sec['end'])
        period = s1 - s0
        dur = float(clip.get('duration') or probe_duration(clip['path']))
        peak = float(clip.get('peak') if clip.get('peak') is not None else dur / 2)
        # the hit we aim at: a drum hit for fight clips, otherwise a beat, ~1 s into the section
        grid = sorted(onsets.get('beatbox') or []) if VIBE[LAYERS[i]] == 'fight' and i >= 2 else list(beats)
        targets = [b - s0 for b in grid if s0 + 0.6 <= b < s1 - 0.3] or [min(1.0, period / 2)]
        target = next((tg for tg in targets if peak - tg >= 0), targets[0])
        offset = max(0.0, peak - target)
        tiles.append({'clip': clip, 'start': s0, 'period': period, 'offset': offset, 'layer': LAYERS[i]})
    return tiles


def label_image(layer):
    """Big layer label ("VOCALS", "+ BASS", ...) that pops up on top."""
    text, emo = LAYER_LABEL.get(layer, (layer.upper(), '🎵'))
    p = pill(text, 58, (255, 214, 0), max_w=700)
    e = quiz_emoji.image(emo, 72)
    if e is not None:
        row = Image.new('RGBA', (p.size[0] + 82, max(p.size[1], 72)), (0, 0, 0, 0))
        row.alpha_composite(p, (0, (row.size[1] - p.size[1]) // 2))
        row.alpha_composite(e, (p.size[0] + 10, (row.size[1] - 72) // 2))
        p = row
    return with_shadow(p, blur=8, offset=(0, 5))


def title_image(title, emoji):
    emo = [quiz_emoji.image(e, 76) for e in (emoji or '').split(',') if e] if emoji != 'none' else []
    emo = [e for e in emo if e is not None]
    room = 1000 - sum(e.size[0] + 10 for e in emo)  # the whole row must fit the 1080 px frame
    t = text_layer(title.lower(), 76, stroke=9, max_w=room)
    if not emo:
        return t
    row = Image.new('RGBA', (t.size[0] + sum(e.size[0] + 10 for e in emo), max(t.size[1], 82)), (0, 0, 0, 0))
    row.alpha_composite(t, (0, (row.size[1] - t.size[1]) // 2))
    x = t.size[0] + 10
    for e in emo:
        row.alpha_composite(e, (x, (row.size[1] - e.size[1]) // 2))
        x += e.size[0] + 10
    return row


def caption_image(text, tile_w):
    """Meme caption for one clip (lowercase, white with a black outline)."""
    size = 52 if tile_w >= W else 40
    return text_layer(text.lower(), size, stroke=6, stroke_fill=(0, 0, 0), max_w=tile_w - 60, min_size=26)


def render(args):
    timeline = json.load(open(args.timeline, encoding='utf-8'))
    manifest = json.load(open(args.clips, encoding='utf-8'))
    clips = manifest['clips'] if isinstance(manifest, dict) else manifest
    if len(clips) < 4:
        raise SystemExit('need at least 4 clips')
    dur = float(timeline.get('duration') or timeline['sections'][-1]['end'])
    tiles = plan(timeline, clips)
    starts = [tl['start'] for tl in tiles]
    downbeats = timeline.get('downbeats') or timeline['beats'][::4]

    title = title_image(args.title, args.emoji) if args.title else None
    labels = [label_image(tl['layer']) for tl in tiles]
    captions = {}
    wm = text_layer(args.watermark, 38, fill=(255, 255, 255, 170), stroke=3, stroke_fill=(0, 0, 0, 120)) if args.watermark else None

    cmd = ['ffmpeg', '-y', '-v', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{W}x{H}', '-r', str(FPS), '-i', '-',
           '-i', args.audio, '-c:v', 'libx264', '-preset', 'medium', '-crf', '20', '-pix_fmt', 'yuv420p',
           '-c:a', 'aac', '-b:a', '192k', '-shortest', '-movflags', '+faststart', args.out]
    enc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    streams = {}  # tile -> (loop number, size, ClipStream)
    for f in range(int(dur * FPS)):
        t = f / FPS
        n = max(1, sum(1 for s in starts if s <= t))
        layout = LAYOUT[n]
        frame = Image.new('RGBA', (W, H), (0, 0, 0, 255))
        for i in range(n):
            x, y, w, h = layout[i]
            tl = tiles[i]
            loop, local = divmod(t - tl['start'], tl['period'])
            key = (int(loop), (w, h))
            if i not in streams or streams[i][:2] != key:
                if i in streams:
                    streams[i][2].close()
                streams[i] = (key[0], key[1], ClipStream(tl['clip'], w, h, offset=tl['offset'] + local))
            frame.paste(Image.fromarray(streams[i][2].next()), (x, y))
            cap = tl['clip'].get('caption')
            if cap and not tl['clip'].get('has_caption'):
                if (i, w) not in captions:
                    captions[(i, w)] = caption_image(cap, w)
                im = captions[(i, w)]
                put(frame, im, x + w / 2, y + h - im.size[1] / 2 - (150 if n == 1 else 70))
        d = ImageDraw.Draw(frame)
        for x, y, w, h in layout:
            d.rectangle([x, y, x + w - 1, y + h - 1], outline=(0, 0, 0, 255), width=4)
        # quick glow on the tile that has just appeared (the new layer), nowhere else
        age = t - starts[n - 1]
        if n > 1 and age < GLOW_SEC:
            x, y, w, h = layout[n - 1]
            a = 1 - age / GLOW_SEC
            if age < 0.08:
                frame.alpha_composite(Image.new('RGBA', (w, h), (255, 255, 255, int(90 * a))), (x, y))
            for k in range(3):
                d.rectangle([x + k * 5, y + k * 5, x + w - 1 - k * 5, y + h - 1 - k * 5],
                            outline=GLOW + (int(255 * a * (1 - k * 0.3)),), width=6)
        # all tiles bounce together on the downbeat once the beat is in
        if n >= 3:
            since_db = min([t - b for b in downbeats if b <= t] or [9])
            if since_db < 0.18:
                z = 1 + 0.07 * (1 - since_db / 0.18)
                zw, zh = int(W * z), int(H * z)
                frame = frame.resize((zw, zh), Image.BILINEAR).crop(((zw - W) // 2, (zh - H) // 2, (zw - W) // 2 + W, (zh - H) // 2 + H))
        if age < 1.8:
            put(frame, labels[n - 1], W / 2, 400, s=max(0.01, ease_out_back(age / 0.25)), a=clamp((1.8 - age) / 0.3))
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
    used = [tl['clip'] for tl in tiles]
    return {'duration': round(dur, 2), 'cuts': len(used), 'clipsUsed': [c.get('source_id') for c in used],
            'vibes': [c.get('vibe') for c in used],
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
