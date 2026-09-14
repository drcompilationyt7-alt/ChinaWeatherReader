#!/usr/bin/env python3
"""
Prepare Easter Egg overlay assets (offline keyer).

One-time tool. Takes the raw AI-generated green-screen clips, trims/crops
them so only the character is left (no watermark / sky / floor), keys the
green out with a luma-aware "green dominance" matte, despills the edges and
writes each egg as a STACKED mp4:

    +-----------------+
    |  colour (RGB)   |   top half
    +-----------------+
    |  alpha matte    |   bottom half (grey, white = opaque)
    +-----------------+

core/easter-eggs.js splits the two halves at render time and recombines
them with ffmpeg's `alphamerge`, so the pipeline never has to chroma-key
anything and the assets stay plain h264 files every ffmpeg can decode.

Usage:
    python scripts/prepare-easter-eggs.py --src "C:/path/to/raw/clips" [--out core/easter-eggs]

Requires: ffmpeg/ffprobe on PATH, numpy.
"""
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

import numpy as np

MAX_WIDTH = 720   # eggs render at <= ~40% of a 1080px frame, 720 is plenty
FPS = 24

# ─── Trim / crop table (coords in source pixels) ────────────────────
# nativeSide: the screen side the character's own entry/exit happens on
# (walks in from the left, exits to the right, ...). When the egg is placed
# on the other side it gets mirrored so the motion still comes from off-screen.
# key.lo / key.hi: green-dominance ratio thresholds (see key_frame).
# feather: soft alpha ramps (fraction of asset width/height) on the edges
# where the character is cut by the asset frame. The edge that touches the
# screen edge (nativeSide) never needs one; the others would otherwise show a
# hard straight cut in the middle of the short.
EGGS = [
    dict(id='gwen-popout', match=r'Background_to_Foreground_Pop-Out',
         crop=(0, 0, 940, 768), start=2.4, end=5.875,
         feather=dict(bottom=0.12, right=0.05),
         nativeSide='left', mirror=True, widthFrac=0.36, enter='fade', exit='fade',
         desc='Gwen runs up from the distance and pops out at you'),
    dict(id='spidey-popcorn', match=r'spiderman_red_blue_version',
         crop=(0, 0, 880, 768), start=0.0, end=5.875,
         feather=dict(bottom=0.12, right=0.06, top=0.05),
         nativeSide='left', mirror=True, widthFrac=0.33, enter='slide', exit='slide',
         desc='Chubby Spidey munches popcorn, then turns to look at you'),
    dict(id='gwen-stick', match=r'she_gwen_stacy',
         crop=(300, 0, 800, 740), start=0.5, end=5.3,
         feather=dict(top=0.06),
         nativeSide='left', mirror=True, widthFrac=0.36, enter='none', exit='fade',
         desc='Gwen walks in and pokes at the video with a stick'),
    dict(id='spidey-stick', match=r'he_is_call_mr_webst',
         crop=(200, 0, 800, 740), start=0.3, end=5.875,
         feather=dict(top=0.06, right=0.06, bottom=0.05),
         nativeSide='left', mirror=True, widthFrac=0.36, enter='none', exit='fade',
         desc='Mr Webster walks in, raises his stick, then jumps at you'),
    # The flat-colour Vidu clips have noisy outline/green mixes, so key them a
    # little harder (lower hi) than the 3D-rendered ones.
    dict(id='flash-chase', match=r'3322493870732970',
         crop=(0, 400, 1080, 1300), start=0.0, end=8.04, key=dict(lo=0.16, hi=0.34, erode=2),
         feather=dict(right=0.08),
         nativeSide='left', mirror=True, widthFrac=0.32, enter='fade', exit='fade',
         desc='Flash chases a robber, loses him, watches for a bit, then runs off'),
    dict(id='spidey-chase', match=r'3323248650013817',
         crop=(0, 180, 1080, 1416), start=0.0, end=6.6, key=dict(lo=0.16, hi=0.34, erode=2),
         feather=dict(right=0.08, bottom=0.05),
         nativeSide='left', mirror=True, widthFrac=0.30, enter='fade', exit='slide',
         desc='Spidey chases a robber, loses him, and stands there watching with you'),
    dict(id='gwen-peek', match=r'3323492487095912',
         crop=(0, 0, 1080, 1840), start=0.0, end=3.05, key=dict(lo=0.18, hi=0.38, erode=2),
         feather=dict(left=0.20, top=0.08, bottom=0.08),
         nativeSide='right', mirror=True, widthFrac=0.25, enter='fade', exit='fade',
         desc='Gwen peeks in, waves, and ducks back out'),
    dict(id='gwen-tap', match=r'3323492487095912',
         crop=(0, 0, 1080, 1840), start=3.4, end=8.3, key=dict(lo=0.18, hi=0.38, erode=2),
         feather=dict(left=0.20, top=0.08, bottom=0.08),
         nativeSide='right', mirror=True, widthFrac=0.25, enter='none', exit='slide',
         desc='Gwen leans in and taps the screen to check you are paying attention'),
]
DEFAULT_KEY = dict(lo=0.22, hi=0.42)


def probe(path):
    out = subprocess.check_output([
        'ffprobe', '-v', 'error', '-select_streams', 'v:0',
        '-show_entries', 'stream=width,height:format=duration', '-of', 'json', path
    ]).decode('utf-8')
    j = json.loads(out)
    s = j['streams'][0]
    return int(s['width']), int(s['height']), float(j['format']['duration'])


def read_frames(src, crop, start, dur):
    x, y, w, h = crop
    w -= w % 2
    h -= h % 2
    vf = f'crop={w}:{h}:{x}:{y}'
    if w > MAX_WIDTH:
        vf += f',scale={MAX_WIDTH}:-2:flags=lanczos'
    vf += f',fps={FPS}'
    cmd = ['ffmpeg', '-v', 'error', '-ss', f'{start:.3f}', '-i', src, '-t', f'{dur:.3f}', '-an',
           '-vf', vf, '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-']
    raw = subprocess.check_output(cmd)
    # figure out the output size from the filter (scale keeps aspect, even height)
    if w > MAX_WIDTH:
        ow = MAX_WIDTH
        oh = int(round(h * MAX_WIDTH / w / 2)) * 2
    else:
        ow, oh = w, h
    n = len(raw) // (ow * oh * 3)
    return np.frombuffer(raw, np.uint8)[:n * ow * oh * 3].reshape(n, oh, ow, 3)


def erode3(a):
    er = a.copy()
    er[1:-1, 1:-1] = np.minimum.reduce([
        a[:-2, :-2], a[:-2, 1:-1], a[:-2, 2:],
        a[1:-1, :-2], a[1:-1, 1:-1], a[1:-1, 2:],
        a[2:, :-2], a[2:, 1:-1], a[2:, 2:],
    ])
    return er


def key_frame(rgb, lo, hi, erode=1):
    """
    Green-dominance matte: ratio = (G - max(R,B)) / G.
    Background green (bright or in a dark vignette) has a high ratio, while
    white / black / grey / red / blue / yellow content stays near or below 0,
    so luma no longer matters the way it does for a UV chroma key.
    Returns (despilled rgb uint8, alpha uint8).
    """
    f = rgb.astype(np.float32)
    r, g, b = f[..., 0], f[..., 1], f[..., 2]
    mx = np.maximum(r, b)
    ratio = (g - mx) / np.maximum(g, 1.0)
    alpha = np.clip((hi - ratio) / (hi - lo), 0.0, 1.0)

    # erode (kills the chroma-subsampling halo / noisy outline ring) then soften
    er = alpha
    for _ in range(max(1, int(erode))):
        er = erode3(er)
    hgt, wid = er.shape
    soft = er
    for _ in range(2):  # two 3x3 box passes ~ a gentle 5x5 blur
        p = np.pad(soft, 1, mode='edge')
        soft = sum(p[i:i + hgt, j:j + wid] for i in range(3) for j in range(3)) / 9.0

    # despill: green may never exceed the other channels by much
    g2 = np.minimum(g, mx * 1.05 + 4.0)
    out = np.stack([r, g2, b], axis=-1)
    return np.clip(out, 0, 255).astype(np.uint8), (soft * 255.0 + 0.5).astype(np.uint8)


def feather_alpha(alpha, feather):
    """Multiply alpha by linear ramps on the requested edges (fractions of size)."""
    if not feather:
        return alpha
    h, w = alpha.shape
    a = alpha.astype(np.float32) / 255.0
    if feather.get('left'):
        n = max(1, int(w * feather['left']))
        a[:, :n] *= np.linspace(0.0, 1.0, n, dtype=np.float32)[None, :]
    if feather.get('right'):
        n = max(1, int(w * feather['right']))
        a[:, w - n:] *= np.linspace(1.0, 0.0, n, dtype=np.float32)[None, :]
    if feather.get('top'):
        n = max(1, int(h * feather['top']))
        a[:n, :] *= np.linspace(0.0, 1.0, n, dtype=np.float32)[:, None]
    if feather.get('bottom'):
        n = max(1, int(h * feather['bottom']))
        a[h - n:, :] *= np.linspace(1.0, 0.0, n, dtype=np.float32)[:, None]
    return (a * 255.0 + 0.5).astype(np.uint8)


def write_stacked(frames, alphas, out_path):
    n, h, w, _ = frames.shape
    cmd = ['ffmpeg', '-y', '-v', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{w}x{h * 2}',
           '-r', str(FPS), '-i', '-', '-an', '-c:v', 'libx264', '-preset', 'slow', '-crf', '17',
           '-pix_fmt', 'yuv444p', '-movflags', '+faststart', out_path]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for i in range(n):
        a3 = np.repeat(alphas[i][..., None], 3, axis=-1)
        p.stdin.write(np.concatenate([frames[i], a3], axis=0).tobytes())
    p.stdin.close()
    if p.wait() != 0:
        raise RuntimeError(f'ffmpeg failed writing {out_path}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True, help='dir with raw green-screen clips')
    ap.add_argument('--out', default=os.path.join(os.path.dirname(__file__), '..', 'core', 'easter-eggs'))
    ap.add_argument('--only', default=None, help='comma separated egg ids')
    args = ap.parse_args()

    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)
    files = [f for f in os.listdir(args.src) if re.search(r'\.(mp4|mov|webm)$', f, re.I)]
    only = set(args.only.split(',')) if args.only else None

    manifest_path = os.path.join(out_dir, 'manifest.json')
    manifest = {'version': 2, 'layout': 'stacked-alpha', 'generatedAt': datetime.now(timezone.utc).isoformat(), 'eggs': []}
    if only and os.path.exists(manifest_path):
        # keep the untouched entries when re-running for a subset
        try:
            old = json.load(open(manifest_path, encoding='utf-8'))
            manifest['eggs'] = [e for e in old.get('eggs', []) if e['id'] not in only]
        except Exception:
            pass

    for egg in EGGS:
        if only and egg['id'] not in only:
            continue
        src = next((f for f in files if re.search(egg['match'], f)), None)
        if not src:
            print(f"[skip] {egg['id']}: no source matches /{egg['match']}/")
            continue
        key = {**DEFAULT_KEY, **egg.get('key', {})}
        dur = max(0.1, egg['end'] - egg['start'])
        print(f"[prep] {egg['id']} <- {src[:48]} ...", end=' ', flush=True)
        frames = read_frames(os.path.join(args.src, src), egg['crop'], egg['start'], dur)
        rgb_out = np.empty_like(frames)
        a_out = np.empty(frames.shape[:3], np.uint8)
        for i in range(frames.shape[0]):
            rgb_out[i], a_out[i] = key_frame(frames[i], key['lo'], key['hi'], key.get('erode', 1))
            a_out[i] = feather_alpha(a_out[i], egg.get('feather'))
        out_file = f"{egg['id']}.mp4"
        out_path = os.path.join(out_dir, out_file)
        write_stacked(rgb_out, a_out, out_path)
        _, _, dur_out = probe(out_path)
        n, h, w, _ = frames.shape
        print(f"{w}x{h} {dur_out:.2f}s {os.path.getsize(out_path) // 1024}KB")
        manifest['eggs'].append({
            'id': egg['id'], 'file': out_file,
            'width': w, 'height': h, 'duration': round(dur_out, 3),
            'nativeSide': egg['nativeSide'], 'mirror': egg['mirror'],
            'widthFrac': egg['widthFrac'], 'enter': egg['enter'], 'exit': egg['exit'],
            'desc': egg['desc'],
        })

    manifest['eggs'].sort(key=lambda e: [x['id'] for x in EGGS].index(e['id']) if e['id'] in [x['id'] for x in EGGS] else 99)
    with open(manifest_path, 'w', encoding='utf-8') as fh:
        json.dump(manifest, fh, indent=2)
    print(f"\nWrote {len(manifest['eggs'])} eggs + manifest.json to {out_dir}")


if __name__ == '__main__':
    main()
