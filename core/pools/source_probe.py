"""
Light quality probe of one candidate SOURCE video for the song top-up (pipeline/song-topup.js gate 3):
downloads a 10 s low-res preview (yt-dlp --download-sections, <= 720p) and checks it with the existing detectors:
  * burned-in subtitles / captions: core/meme/edit_fx.text_bands (the detector render_meme.py trims with)
  * static watermark / logo: a corner whose pixels hold still while the picture moves and that is full of edges
  * borders / letterbox / pillarbox: black bars on the frame edges (a cropped or framed re-upload)
  * sharpness: the 85th percentile Laplacian variance over a 6x8 block grid (core/edit/library.local_sharpness, copied: that
    module pulls in heavy dependencies), median over the sampled frames at 640 px width
Prints one JSON line {ok, clean, reasons, sharp, text_top, text_bottom, watermark, border}. clean = no reason to reject.
  python core/pools/source_probe.py --id VIDEOID [--start 40] [--seconds 10] [--file preview.mp4]
Thresholds: PROBE_MIN_SHARP (default 60), PROBE_MAX_BORDER (0.04), PROBE_MAX_TEXT (0.02).
"""
import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile

import cv2
import numpy as np

cv2.setNumThreads(int(os.environ.get('PROBE_THREADS', '2')))

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
MIN_SHARP = float(os.environ.get('PROBE_MIN_SHARP', '60'))
MAX_BORDER = float(os.environ.get('PROBE_MAX_BORDER', '0.04'))
MAX_TEXT = float(os.environ.get('PROBE_MAX_TEXT', '0.02'))


def local_sharpness(g, gy=6, gx=8):
    """Laplacian variance of the sharpest part of a grey frame (library.local_sharpness)."""
    h, w = g.shape[:2]
    bh, bw = h // gy, w // gx
    if bh < 4 or bw < 4:
        return 0.0
    lap = cv2.Laplacian(g[:bh * gy, :bw * gx], cv2.CV_32F).reshape(gy, bh, gx, bw)
    v = lap.var(axis=(1, 3))
    return float(np.percentile(v, 85))


def border_fraction(frames):
    """Share of the frame (max of the two axes) taken by near-black bars on its edges, median over frames."""
    out = []
    for g in frames:
        h, w = g.shape
        rows = g.mean(axis=1) < 12
        cols = g.mean(axis=0) < 12
        def run(a):
            n = 0
            for v in a:
                if not v:
                    break
                n += 1
            return n
        top, bot, left, right = run(rows), run(rows[::-1]), run(cols), run(cols[::-1])
        out.append(max((top + bot) / h, (left + right) / w))
    return float(np.median(out)) if out else 0.0


def static_watermark(frames, frac=0.16):
    """True when a corner holds still (low temporal std) while it is full of edges, and the rest of the picture moves."""
    if len(frames) < 6:
        return False
    a = np.stack(frames).astype(np.float32)
    h, w = a.shape[1:]
    ch, cw = int(h * frac), int(w * frac)
    centre_motion = float(a[:, h // 4:3 * h // 4, w // 4:3 * w // 4].std(axis=0).mean())
    if centre_motion < 6:
        return False                       # a still picture: nothing to compare against
    for ys, xs in ((slice(0, ch), slice(0, cw)), (slice(0, ch), slice(w - cw, w)), (slice(h - ch, h), slice(0, cw)), (slice(h - ch, h), slice(w - cw, w))):
        c = a[:, ys, xs]
        still = float(c.std(axis=0).mean()) < 0.35 * centre_motion and float(c.std(axis=0).mean()) < 7
        gx = np.abs(np.diff(c.mean(axis=0), axis=1)).mean()
        gy = np.abs(np.diff(c.mean(axis=0), axis=0)).mean()
        if still and (gx + gy) > 14:
            return True
    return False


def analyse(frames, text=(0.0, 0.0)):
    """frames: list of grey uint8 frames at 640 px width. text: (top, bottom) from edit_fx.text_bands."""
    sharp = float(np.median([local_sharpness(g) for g in frames])) if frames else 0.0
    border = border_fraction(frames)
    wm = static_watermark(frames)
    reasons = []
    if len(frames) < 6:
        reasons.append('preview too short')
    if sharp < MIN_SHARP:
        reasons.append(f'soft ({sharp:.0f} < {MIN_SHARP:.0f})')
    if max(text) > MAX_TEXT:
        reasons.append(f'burned-in text ({text[0]:.2f} top / {text[1]:.2f} bottom)')
    if wm:
        reasons.append('static watermark / logo')
    if border > MAX_BORDER:
        reasons.append(f'borders ({border:.2f})')
    return {'sharp': round(sharp, 1), 'text_top': round(text[0], 3), 'text_bottom': round(text[1], 3), 'watermark': bool(wm),
            'border': round(border, 3), 'reasons': reasons, 'clean': not reasons}


def read_frames(path, n=10):
    r = subprocess.run(['ffmpeg', '-v', 'error', '-threads', '2', '-i', path, '-an', '-vf', 'fps=1,scale=640:-2,format=gray', '-frames:v', str(n), '-f', 'rawvideo', '-'],
                       capture_output=True, timeout=120)
    p = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width,height', '-of', 'csv=p=0', path],
                       capture_output=True, text=True, timeout=30)
    w, h = [int(x) for x in p.stdout.strip().split(',')[:2]]
    hh = int(round(640 * h / w / 2) * 2)
    a = np.frombuffer(r.stdout, np.uint8)
    k = a.size // (640 * hh)
    return [a[i * 640 * hh:(i + 1) * 640 * hh].reshape(hh, 640) for i in range(k)]


def text_bands(path):
    try:
        sys.path.insert(0, os.path.join(ROOT, 'core', 'meme'))
        import edit_fx
        t, b = edit_fx.text_bands(path)
        return float(t), float(b)
    except Exception:
        return 0.0, 0.0


LAST_ERR = ''


def download(vid, start, seconds, out_dir):
    cmd = [sys.executable, '-m', 'yt_dlp', '--no-warnings', '-q', '-f', 'bv*[height<=720]+ba/b[height<=720]/worst',
           '--merge-output-format', 'mp4', '--download-sections', f'*{start}-{start + seconds}', '--force-keyframes-at-cuts', '--postprocessor-args', 'ffmpeg:-threads 2', '--socket-timeout', '20',
           '-o', os.path.join(out_dir, 'prev.%(ext)s'), f'https://www.youtube.com/watch?v={vid}']
    cmd[3:3] = ['--force-ipv4', '--retries', '2']   # same flags as core/meme/clip_finder.py (works on the runner)
    ck = os.environ.get('YT_COOKIES')
    if ck and os.path.exists(ck):
        tmp = os.path.join(out_dir, 'cookies.txt')   # yt-dlp rewrites the jar on exit: work on a private copy
        shutil.copyfile(ck, tmp)
        cmd[3:3] = ['--cookies', tmp]
    r = subprocess.run(cmd, capture_output=True, timeout=300, text=True)
    global LAST_ERR
    LAST_ERR = ' '.join((r.stderr or '').strip().splitlines()[-1:])[:160]
    fs = [f for f in glob.glob(os.path.join(out_dir, 'prev.*')) if not f.endswith('.part')]
    merged = [f for f in fs if '.f' not in os.path.basename(f)[4:9]]   # prev.f401.mp4 = an unmerged stream
    fs = merged or fs
    return max(fs, key=os.path.getsize) if fs else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--id')
    ap.add_argument('--file')
    ap.add_argument('--start', type=int, default=40)
    ap.add_argument('--seconds', type=int, default=10)
    a = ap.parse_args()
    with tempfile.TemporaryDirectory(prefix='probe-') as d:
        path = a.file or (download(a.id, a.start, a.seconds, d) if a.id else None)
        if not path:
            print(json.dumps({'ok': False, 'clean': False, 'reasons': ['preview unavailable' + (': ' + LAST_ERR if LAST_ERR else '')]}))
            return
        try:
            res = analyse(read_frames(path), text_bands(path))
            res['ok'] = True
        except Exception as e:
            res = {'ok': False, 'clean': False, 'reasons': [f'probe failed: {e!r}'[:120]]}
        print(json.dumps(res))


if __name__ == '__main__':
    main()
