#!/usr/bin/env python3
"""
Layered meme edit for "<song> acapella" shorts (Mr. WorldWideWebster).

Layout grows with the acapella layers (the big layer label pops up on top):

  vocals      1 tile, full screen
  + bass      2 tiles stacked
  + beatbox   2x2 grid
  + harmony   2x2 grid

- Each tile is its own compilation: a clip plays 3-4 s (time to get the joke),
  then the tile moves to the next one; tiles switch on different beats.
- Every new clip gets a quick glow on its tile so the eye finds it.
- No clip is shown twice in one short; each has a short context caption
  unless it carries its own.
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
LAYOUT = {
    1: [(0, 0, W, H)],
    2: [(0, 0, W, H // 2), (0, H // 2, W, H // 2)],
    4: [(0, 0, W // 2, H // 2), (W // 2, 0, W // 2, H // 2), (0, H // 2, W // 2, H // 2), (W // 2, H // 2, W // 2, H // 2)],
}
HOLD_MIN, HOLD_MAX = 3.0, 4.0
GLOW_SEC = 0.5
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


def tiles_for(n_layers):
    return 1 if n_layers <= 1 else 2 if n_layers == 2 else 4


def schedule(timeline, clips):
    """
    [(t0, t1, n_tiles, tile, clip index)]: every tile runs its own sequence of
    clips, each held 3-4 s (snapped to the beat grid), tiles staggered by a
    beat so they never switch together. A tile that exists in the next section
    carries its clip over (it keeps playing); new tiles get new clips. No clip
    is used twice; if the clips run out, a tile keeps its clip looping.
    """
    beats = np.array(timeline['beats'])
    beat = float(np.median(np.diff(beats))) if len(beats) > 1 else 0.5
    queue = list(range(len(clips)))
    out = []
    carry = {}  # tile -> (clip, planned end of that clip)
    taken = []  # switch times already used by some tile

    def snap(t):
        k = np.searchsorted(beats, t - 0.05)
        return float(beats[k]) if k < len(beats) else t

    def free(t):
        """The first beat at or after t that no other tile switches on."""
        t = snap(t)
        while any(abs(t - x) < beat * 0.5 for x in taken):
            t = snap(t + beat)
        taken.append(t)
        return t

    def hold(c):
        return min(HOLD_MAX, max(HOLD_MIN, float(clips[c].get('duration') or HOLD_MIN)))

    for sec in timeline['sections']:
        s0, s1 = float(sec['start']), float(sec['end'])
        n = tiles_for(len(sec['layers']))
        new_carry = {}
        for ti in range(n):
            if ti in carry:
                c, end = carry[ti]
                if end > s0 and end not in taken:
                    end = free(end)
            elif queue:
                c = queue.pop(0)
                end = free(s0 + hold(c))
            elif carry:  # out of clips: reuse a clip that is not on screen right now
                c = next((cc for cc, _ in carry.values()), 0)
                end = s1
            else:
                break
            t = s0
            while True:
                nxt = min(end, s1)
                if s1 - nxt < 1.2:  # don't start a clip that would only flash by
                    nxt = s1
                out.append((t, nxt, n, ti, c))
                if nxt >= s1 - 1e-3:
                    new_carry[ti] = (c, max(end, s1 + 1.2) if end > s1 else s1 + hold(c))
                    break
                if not queue:  # keep this clip to the section end
                    out[-1] = (t, s1, n, ti, c)
                    new_carry[ti] = (c, s1 + hold(c))
                    break
                t, c = nxt, queue.pop(0)
                end = free(t + hold(c))
        carry = new_carry
    return out


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
    clips = dedupe(manifest['clips'] if isinstance(manifest, dict) else manifest)
    if len(clips) < 4:
        raise SystemExit('need at least 4 different clips')
    for c in clips:
        if not c.get('duration'):
            c['duration'] = (c.get('end', 0) - c.get('start', 0)) or probe_duration(c['path'])
    dur = float(timeline.get('duration') or timeline['sections'][-1]['end'])
    plan = schedule(timeline, clips)
    sections = timeline['sections']
    downbeats = timeline.get('downbeats') or timeline['beats'][::4]

    title = title_image(args.title, args.emoji) if args.title else None
    labels = [label_image(sec['layers'][-1]) for sec in sections]
    wm = text_layer(args.watermark, 38, fill=(255, 255, 255, 170), stroke=3, stroke_fill=(0, 0, 0, 120)) if args.watermark else None

    cmd = ['ffmpeg', '-y', '-v', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{W}x{H}', '-r', str(FPS), '-i', '-',
           '-i', args.audio, '-c:v', 'libx264', '-preset', 'medium', '-crf', '20', '-pix_fmt', 'yuv420p',
           '-c:a', 'aac', '-b:a', '192k', '-shortest', '-movflags', '+faststart', args.out]
    enc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    streams = {}   # tile -> (plan entry, ClipStream)
    cap_cache = {}
    for f in range(int(dur * FPS)):
        t = f / FPS
        si = max(0, sum(1 for sec in sections if sec['start'] <= t) - 1)
        n = tiles_for(len(sections[si]['layers']))
        layout = LAYOUT[n]
        frame = Image.new('RGBA', (W, H), (0, 0, 0, 255))
        live = [e for e in plan if e[2] == n and e[0] <= t < e[1]] or [e for e in plan if e[2] == n and e[1] >= t - 0.05]
        new_tiles = []
        for e in live:
            t0, t1, _, ti, ci = e
            if ti >= n:
                continue
            x, y, w, h = layout[ti]
            if ti not in streams or streams[ti][0] != e:
                if ti in streams:
                    streams[ti][1].close()
                # the clip that carries over a layout change keeps playing where it was
                into = 0.0
                prev = [p for p in plan if p[3] == ti and p[4] == ci and p[0] < t0]
                if prev:
                    into = (t - prev[0][0]) % max(0.5, float(clips[ci]['duration']))
                streams[ti] = (e, ClipStream(clips[ci], w, h, offset=into))
            frame.paste(Image.fromarray(streams[ti][1].next()), (x, y))
            # quick glow on a tile whose clip just changed (not on a carried-over clip)
            first_show = not [p for p in plan if p[4] == ci and p[0] < t0]
            if first_show and t0 > 0 and t - t0 < GLOW_SEC:  # frame 0 is the thumbnail: no glow there
                new_tiles.append((layout[ti], t - t0))
            cap = clips[ci].get('caption')
            if cap and not clips[ci].get('has_caption'):
                key = (ci, w)
                if key not in cap_cache:
                    cap_cache[key] = caption_image(cap, w)
                im = cap_cache[key]
                put(frame, im, x + w / 2, y + h - im.size[1] / 2 - (150 if n == 1 else 70))
        for ti in [k for k in streams if k >= n]:
            streams.pop(ti)[1].close()
        d = ImageDraw.Draw(frame)
        for x, y, w, h in layout:
            d.rectangle([x, y, x + w - 1, y + h - 1], outline=(0, 0, 0, 255), width=4)
        for (x, y, w, h), age in new_tiles:
            a = 1 - age / GLOW_SEC
            if age < 0.08:
                frame.alpha_composite(Image.new('RGBA', (w, h), (255, 255, 255, int(90 * a))), (x, y))
            for k in range(3):
                d.rectangle([x + k * 5, y + k * 5, x + w - 1 - k * 5, y + h - 1 - k * 5],
                            outline=GLOW + (int(255 * a * (1 - k * 0.3)),), width=6)
        # every clip bounces together on the downbeat once the beat is in (v2 style)
        if len(sections[si]['layers']) >= 3:
            since_db = min([t - b for b in downbeats if b <= t] or [9])
            if since_db < 0.18:
                z = 1 + 0.07 * (1 - since_db / 0.18)
                zw, zh = int(W * z), int(H * z)
                frame = frame.resize((zw, zh), Image.BILINEAR).crop(((zw - W) // 2, (zh - H) // 2, (zw - W) // 2 + W, (zh - H) // 2 + H))
        # big layer label on top as each layer comes in
        since_sec = t - sections[si]['start']
        if since_sec < 1.8:
            put(frame, labels[si], W / 2, 400, s=max(0.01, ease_out_back(since_sec / 0.25)), a=clamp((1.8 - since_sec) / 0.3))
        if title is not None:
            put(frame, title, W / 2, 250)
        if wm is not None:
            put(frame, wm, W / 2, 150)
        enc.stdin.write(frame.convert('RGB').tobytes())
    for _, st in streams.values():
        st.close()
    enc.stdin.close()
    if enc.wait() != 0:
        raise SystemExit('ffmpeg failed')
    used = []
    for e in plan:
        if e[4] not in used:
            used.append(e[4])
    return {'duration': round(dur, 2), 'cuts': len(used), 'clipsUsed': [clips[i].get('source_id') for i in used],
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
