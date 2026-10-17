#!/usr/bin/env python3
"""
AI sharpening of POOL clips (owner 2026-10-13: approved; runs ONCE per clip at pool-refresh time, never in the daily render).

    python core/edit/upscale_pool.py --in DIR_OR_GLOB ... [--backup DIR] [--model PATH] [--device cuda|cpu] [--tile 540] [--max-h 1500]
    python core/edit/upscale_pool.py --selftest

Real-ESRGAN realesr-animevideov3 (SRVGGNetCompact, 2.4 MB, BSD-3): the x4 network output is resized (area) to x2, so a 1080p clip becomes
2160p and a 9:16 crop of it is no longer an upscale. Per clip: decode with ffmpeg (rawvideo), net per frame (fp16 on CUDA, tiles on CPU),
encode h264 crf 16 at the clip fps, audio copied; the result REPLACES the clip only after it is verified (2x size, same duration, audio kept);
the original is moved to --backup (delete it once the sharp clips are verified). Clips whose height is already >= --max-h are skipped.
A finished clip gets a sidecar <clip>.up.json so a re-run skips it.

Cost (lab/prep.py notes): CPU 22-83 s per 1080p frame at 2 threads = unusable for a monthly batch; a GPU (RTX 3060 / Kaggle T4) is ~10-25
frames/s, so a 400-clip refresh (~25 k frames) is ~20-40 min on a Kaggle GPU: see scripts/edit-upscale-kaggle.js (refresh integration).
The model file is not kept in the repo: it is downloaded on demand from the Real-ESRGAN release (MODEL_URL) into MODEL_DIR.
"""
import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import time

import numpy as np

MODEL_URL = 'https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-animevideov3.pth'
MODEL_DIR = os.environ.get('EDIT_UPSCALE_MODELS') or os.path.join(os.path.expanduser('~'), '.cache', 'edit_upscale_models')
MODEL_NAME = 'realesr-animevideov3.pth'


def log(*a):
    print('[upscale]', *a, file=sys.stderr, flush=True)


def model_path(p=None):
    if p and os.path.isfile(p):
        return p
    for cand in (os.path.join(MODEL_DIR, MODEL_NAME), os.path.join(os.path.expanduser('~'), '.cache', 'pop_quiz_models', MODEL_NAME)):
        if os.path.isfile(cand):
            return cand
    os.makedirs(MODEL_DIR, exist_ok=True)
    dst = os.path.join(MODEL_DIR, MODEL_NAME)
    import urllib.request
    log('downloading', MODEL_URL)
    urllib.request.urlretrieve(MODEL_URL, dst)
    return dst


def build_net(path, device):
    import torch
    import torch.nn as nn

    class SRVGG(nn.Module):       # SRVGGNetCompact(num_feat=64, num_conv=16, upscale=4, act='prelu')
        def __init__(self):
            super().__init__()
            b = [nn.Conv2d(3, 64, 3, 1, 1), nn.PReLU(num_parameters=64)]
            for _ in range(16):
                b += [nn.Conv2d(64, 64, 3, 1, 1), nn.PReLU(num_parameters=64)]
            b += [nn.Conv2d(64, 3 * 16, 3, 1, 1)]
            self.body = nn.ModuleList(b)
            self.up = nn.PixelShuffle(4)

        def forward(self, x):
            o = x
            for m in self.body:
                o = m(o)
            return self.up(o) + nn.functional.interpolate(x, scale_factor=4, mode='nearest')
    net = SRVGG()
    sd = torch.load(path, map_location='cpu')
    net.load_state_dict(sd.get('params', sd))
    net.eval().to(device)
    if device == 'cuda':
        net.half()
    return net


def up_frame(net, img, device, tile):
    """img BGR uint8 HxWx3 -> BGR uint8 2H x 2W (x4 network output, area-resized to x2 ON the device)."""
    import torch
    import torch.nn.functional as F
    h, w = img.shape[:2]
    dt = torch.float16 if device == 'cuda' else torch.float32
    out = np.zeros((h * 2, w * 2, 3), np.uint8)
    pad = 10
    with torch.inference_mode():
        for y0 in range(0, h, tile):
            for x0 in range(0, w, tile):
                y1, x1 = min(h, y0 + tile), min(w, x0 + tile)
                ya, xa, yb, xb = max(0, y0 - pad), max(0, x0 - pad), min(h, y1 + pad), min(w, x1 + pad)
                t = torch.from_numpy(np.ascontiguousarray(img[ya:yb, xa:xb, ::-1])).to(device).permute(2, 0, 1)[None].to(dt) / 255.0
                o = F.interpolate(net(t), scale_factor=0.5, mode='area')[0]
                o = (o.clamp(0, 1) * 255 + 0.5).to(torch.uint8).permute(1, 2, 0).cpu().numpy()[:, :, ::-1]
                out[y0 * 2:y1 * 2, x0 * 2:x1 * 2] = o[(y0 - ya) * 2:(y0 - ya) * 2 + (y1 - y0) * 2, (x0 - xa) * 2:(x0 - xa) * 2 + (x1 - x0) * 2]
    return out


def probe(path):
    r = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries',
                        'stream=width,height,r_frame_rate:format=duration', '-of', 'json', path], capture_output=True, text=True)
    d = json.loads(r.stdout or '{}')
    s = (d.get('streams') or [{}])[0]
    a, b = (s.get('r_frame_rate') or '24/1').split('/')
    ra = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'a:0', '-show_entries', 'stream=codec_type', '-of', 'csv=p=0', path],
                        capture_output=True, text=True)
    return {'w': int(s.get('width') or 0), 'h': int(s.get('height') or 0), 'fps': float(a) / max(1.0, float(b)),
            'dur': float((d.get('format') or {}).get('duration') or 0), 'audio': bool(ra.stdout.strip())}


def upscale_clip(net, src, dst, device, tile):
    pr = probe(src)
    w, h = pr['w'], pr['h']
    dec = subprocess.Popen(['ffmpeg', '-v', 'error', '-nostdin', '-i', src, '-an', '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-'], stdout=subprocess.PIPE)
    cmd = ['ffmpeg', '-y', '-v', 'error', '-nostdin', '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-s', f'{w * 2}x{h * 2}', '-r', f'{pr["fps"]:.5f}', '-i', '-',
           '-i', src, '-map', '0:v', '-map', '1:a?', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '16', '-pix_fmt', 'yuv420p',
           '-c:a', 'copy', '-movflags', '+faststart', dst]
    enc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    n = 0
    fb = w * h * 3
    while True:
        buf = dec.stdout.read(fb)
        if len(buf) < fb:
            break
        enc.stdin.write(up_frame(net, np.frombuffer(buf, np.uint8).reshape(h, w, 3), device, tile).tobytes())
        n += 1
    enc.stdin.close()
    enc.wait()
    dec.wait()
    return n


def verify(src, dst, n):
    a, b = probe(src), probe(dst)
    ok = b['w'] == a['w'] * 2 and b['h'] == a['h'] * 2 and abs(b['dur'] - a['dur']) < 0.25 and (b['audio'] or not a['audio'])
    return ok, {'src': (a['w'], a['h']), 'dst': (b['w'], b['h']), 'dur': (round(a['dur'], 2), round(b['dur'], 2)), 'frames': n}


def run(paths, backup=None, model=None, device=None, tile=540, max_h=1500, limit=None, list_file=None):
    import torch
    device = device or ('cuda' if torch.cuda.is_available() else 'cpu')
    net = build_net(model_path(model), device)
    files = []
    if list_file:
        paths = list(paths or []) + [x.strip() for x in open(list_file, encoding='utf-8') if x.strip()]
    for p in paths or []:
        files += sorted(glob.glob(os.path.join(p, '*.mp4'))) if os.path.isdir(p) else sorted(glob.glob(p))
    files = [f for f in files if not f.endswith('.up.tmp.mp4')]
    done = skipped = failed = 0
    t0 = time.time()
    for f in files:
        side = f + '.up.json'
        if os.path.exists(side) or probe(f)['h'] >= max_h:
            skipped += 1
            continue
        if limit and done >= limit:
            break
        tmp = f + '.up.tmp.mp4'
        try:
            n = upscale_clip(net, f, tmp, device, tile)
            ok, info = verify(f, tmp, n)
        except Exception as e:  # noqa: BLE001
            ok, info = False, {'error': str(e)[:120]}
        if not ok:
            failed += 1
            log('FAILED', os.path.basename(f), info)
            if os.path.exists(tmp):
                os.remove(tmp)
            continue
        if backup:
            os.makedirs(backup, exist_ok=True)
            shutil.move(f, os.path.join(backup, os.path.basename(f)))
        else:
            os.remove(f)
        os.replace(tmp, f)
        with open(side, 'w') as fh:
            json.dump(dict(info, model=MODEL_NAME, device=device), fh)
        done += 1
        log(f'{os.path.basename(f)}: {info["frames"]} frames, {time.time() - t0:.0f}s total')
    print(json.dumps({'upscaled': done, 'skipped': skipped, 'failed': failed, 'device': device, 'secs': round(time.time() - t0)}))
    return failed == 0


def selftest():
    """One synthetic 64x64 clip through the net on CPU: x2 size, audio kept, sidecar written, original backed up."""
    import tempfile
    d = tempfile.mkdtemp(prefix='up_selftest_')
    clip = os.path.join(d, 'c.mp4')
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc=size=64x64:rate=12:duration=0.5', '-f', 'lavfi', '-i', 'sine=d=0.5',
                    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', clip], check=True)
    ok = run([d], backup=os.path.join(d, 'bak'), device='cpu', tile=64, max_h=1500)
    pr = probe(clip)
    assert ok and pr['w'] == 128 and pr['h'] == 128 and pr['audio'], pr
    assert os.path.exists(clip + '.up.json') and os.path.exists(os.path.join(d, 'bak', 'c.mp4'))
    shutil.rmtree(d, ignore_errors=True)
    print('upscale_pool selftest OK')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--in', dest='inp', nargs='*')
    ap.add_argument('--backup')
    ap.add_argument('--model')
    ap.add_argument('--device')
    ap.add_argument('--tile', type=int, default=540)
    ap.add_argument('--max-h', type=int, default=1500)
    ap.add_argument('--limit', type=int)
    ap.add_argument('--list', dest='list_file')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        selftest()
    else:
        sys.exit(0 if run(a.inp, a.backup, a.model, a.device, a.tile, a.max_h, a.limit, a.list_file) else 1)
