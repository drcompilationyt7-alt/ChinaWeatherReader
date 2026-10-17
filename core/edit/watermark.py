#!/usr/bin/env python3
"""
Channel watermark for finished edits: the same burned-in handle as compose_blocks.channel_watermark (owner 2026-10-11:
small, ~42 % white with a soft shadow, outside the Shorts UI zones, drifting slowly between safe positions so it cannot be
cropped). Kept as its own small module so the sound / slow-mo presets do not import the (paused) block compositor.

    python core/edit/watermark.py --in video.mp4 --out video_wm.mp4 [--text @zeroyenotaku]
    burn(src, dst, text='@zeroyenotaku')       # one CPU pass: ffmpeg decode -> numpy -> libx264 (audio copied)

Env EDIT_WATERMARK overrides the text ('' = off: the file is copied).
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

WM_SAFE = ((0.07, 0.15), (0.40, 0.14), (0.62, 0.30), (0.42, 0.55), (0.08, 0.60), (0.20, 0.36))
WM_SAFE_LAND = ((0.06, 0.08), (0.40, 0.07), (0.70, 0.10), (0.68, 0.80), (0.38, 0.84), (0.06, 0.78))
# 9:16 letterbox delivery (a 16:9 master on black at y .33-.67): drift inside the TOP black band, never over the picture
WM_SAFE_LETTERBOX = ((0.07, 0.16), (0.40, 0.14), (0.62, 0.22), (0.30, 0.27), (0.08, 0.25), (0.50, 0.18))
# rot90 delivery: the handle is upright in the 16:9 MASTER; master (x, y) lands at portrait ((1 - y) W, x H), so these
# master positions keep it out of the Shorts UI (right 22 % buttons, top 12 %, bottom 32 %) once rotated
WM_SAFE_ROT90 = ((0.15, 0.75), (0.40, 0.80), (0.50, 0.42), (0.30, 0.30), (0.15, 0.55), (0.45, 0.62))
WM_LEG_S = 7.0
_WM = {}


def _sprite(txt, h):
    key = (txt, h)
    if key not in _WM:
        from PIL import Image, ImageDraw, ImageFont, ImageFilter
        fs = max(12, int(h * 0.026))
        try:
            f = ImageFont.truetype(os.path.join(HERE, 'assets', 'fonts', 'Montserrat-Variable.ttf'), fs)
            f.set_variation_by_axes([650])
        except Exception:  # noqa: BLE001
            f = ImageFont.load_default()
        tw = int(f.getlength(txt)) + 2 * fs
        im = Image.new('L', (tw, int(fs * 2.2)), 0)
        ImageDraw.Draw(im).text((fs, int(fs * 0.4)), txt, font=f, fill=255)
        sh = im.filter(ImageFilter.GaussianBlur(max(1.0, fs * 0.08)))
        _WM[key] = (np.asarray(im, np.float32) / 255.0, np.asarray(sh, np.float32) / 255.0)
    return _WM[key]


def apply(fr, u, txt='@zeroyenotaku', safe=None):
    """Burn the handle into an RGB / BGR uint8 frame at output time u (seconds)."""
    if not txt:
        return fr
    h, w = fr.shape[:2]
    m, sh = _sprite(txt, h)
    mh, mw = m.shape
    safe = safe or (WM_SAFE_LAND if w > h else WM_SAFE)
    leg = u / WM_LEG_S
    i = int(np.floor(leg))
    e = leg - i
    e = e * e * (3 - 2 * e)
    a_, b_ = safe[i % len(safe)], safe[(i + 1) % len(safe)]
    cx, cy = a_[0] + (b_[0] - a_[0]) * e, a_[1] + (b_[1] - a_[1]) * e
    x0 = int(np.clip(cx * w, 0, w - mw))
    y0 = int(np.clip(cy * h, 0, h - mh))
    reg = fr[y0:y0 + mh, x0:x0 + mw].astype(np.float32)
    sa = (sh[:reg.shape[0], :reg.shape[1]] * 0.30)[..., None]
    ta = (m[:reg.shape[0], :reg.shape[1]] * 0.42)[..., None]
    reg = reg * (1 - sa)
    reg = reg * (1 - ta) + 255.0 * ta
    fr[y0:y0 + mh, x0:x0 + mw] = np.clip(reg, 0, 255).astype(np.uint8)
    return fr


def _probe(path):
    r = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries',
                        'stream=width,height,r_frame_rate', '-of', 'json', path], capture_output=True, text=True)
    s = json.loads(r.stdout)['streams'][0]
    num, den = s['r_frame_rate'].split('/')
    return int(s['width']), int(s['height']), float(num) / float(den)


def burn(src, dst, text=None, crf=16, preset='medium', threads=0, rot90=False, letterbox=False, rotate_hint=None):
    """rot90: the picture is a 16:9 master turned 90 deg clockwise into 9:16 (sound edit 'rot90' delivery); the handle
    is then written upright in the MASTER's frame (it turns with the picture, as the references' own marks do)."""
    text = os.environ.get('EDIT_WATERMARK', '@zeroyenotaku') if text is None else text
    if not text:
        shutil.copy2(src, dst)
        return dst
    w, h, fps = _probe(src)
    # owner 2026-10-13: ROTATED deliveries (rot90) get the small 'rotate phone' corner hint (upright, over the playing video, never on landscape longs)
    hint = (rot90 if rotate_hint is None else rotate_hint) and os.environ.get('EDIT_ROTATE_HINT', '1') != '0' and h > w
    shows = None
    if hint:
        import rotate_hint as RH
        try:
            dur = float(subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', src],
                                       capture_output=True, text=True).stdout.strip() or 0)
        except ValueError:
            dur = 0.0
        shows = RH.shows_for(dur)
    dec = subprocess.Popen(['ffmpeg', '-v', 'error', '-i', src, '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'],
                           stdout=subprocess.PIPE)
    enc_cmd = ['ffmpeg', '-y', '-v', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{w}x{h}', '-r', f'{fps:.6f}',
               '-i', '-', '-i', src, '-map', '0:v', '-map', '1:a?', '-c:v', 'libx264', '-preset', preset, '-crf', str(crf),
               '-pix_fmt', 'yuv420p', '-c:a', 'copy', '-movflags', '+faststart']
    if threads:
        enc_cmd += ['-threads', str(threads)]
    enc = subprocess.Popen(enc_cmd + [dst], stdin=subprocess.PIPE)
    n = w * h * 3
    k = 0
    while True:
        buf = dec.stdout.read(n)
        if len(buf) < n:
            break
        fr = np.frombuffer(buf, np.uint8).reshape(h, w, 3).copy()
        if rot90:
            up = np.ascontiguousarray(np.rot90(fr, 1))          # counter-clockwise: the master upright
            fr = np.ascontiguousarray(np.rot90(apply(up, k / fps, text, WM_SAFE_ROT90), -1))
        else:
            fr = apply(fr, k / fps, text, WM_SAFE_LETTERBOX if letterbox else None)
        if hint:
            fr = np.ascontiguousarray(RH.apply(fr, k / fps, shows))
        enc.stdin.write(fr.tobytes())
        k += 1
    enc.stdin.close()
    dec.wait()
    if enc.wait() != 0 or not os.path.exists(dst):
        raise RuntimeError('watermark encode failed')
    return dst


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--in', dest='src', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--text')
    a = ap.parse_args()
    burn(a.src, a.out, a.text)
    print(json.dumps({'ok': True, 'out': a.out}))


if __name__ == '__main__':
    main()
