"""Study tool for the ranking / list compilation references (Zero Yen Otaku, 2026-10-24).

Per reference video: cut list (frame-difference), 1 fps index sheet, per-second random 20-60 frame
sheets (owner review rule), OCR of on-screen text per 0.5 s (RapidOCR), audio RMS / onset / novelty
(song changes) -> <out>/<id>.json + sheets. Run with the venv that has cv2 + librosa + rapidocr.
  python ranking_ref_study.py <video.mp4> <outdir> [--sheets]
"""
import json, os, random, sys
import cv2
import numpy as np


def tile(frames, out, cols=5, w=216):
    tiles = []
    for t, f in frames:
        f = cv2.resize(f, (w, int(f.shape[0] * w / f.shape[1])))
        cv2.putText(f, f'{t:.2f}', (4, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 3)
        cv2.putText(f, f'{t:.2f}', (4, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
        tiles.append(f)
    if not tiles:
        return
    while len(tiles) % cols:
        tiles.append(np.zeros_like(tiles[0]))
    rows = [np.hstack(tiles[i:i + cols]) for i in range(0, len(tiles), cols)]
    cv2.imwrite(out, np.vstack(rows), [cv2.IMWRITE_JPEG_QUALITY, 82])


def one_pass(path, outd, vid, sheets=True, ocr_step=0.5):
    """One sequential decode: cuts, 1 fps index, random 20-60 frames per second sheets, OCR frames."""
    from rapidocr_onnxruntime import RapidOCR
    eng = RapidOCR()
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    n_est = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    rnd = random.Random()
    pick = set()
    for s in range(int(n_est / fps) + 1):
        k = rnd.randint(20, min(60, int(round(fps))))
        pick |= {int(s * fps) + j for j in rnd.sample(range(int(round(fps))), min(k, int(round(fps))))}
    per_s, index, ocr = {}, [], []
    prev, i, cuts = None, 0, []
    ocr_every = max(1, int(round(fps * ocr_step)))
    while True:
        ok, f = cap.read()
        if not ok:
            break
        t = i / fps
        s = cv2.resize(f, (96, 128))
        h = cv2.calcHist([cv2.cvtColor(s, cv2.COLOR_BGR2HSV)], [0, 1], None, [16, 8], [0, 180, 0, 256])
        h = cv2.normalize(h, None).flatten()
        if prev is not None and 1 - cv2.compareHist(prev, h, cv2.HISTCMP_CORREL) > 0.45:
            cuts.append(round(t, 3))
        prev = h
        if i % int(round(fps)) == int(round(fps)) // 2:
            index.append((t, f.copy()))
        if sheets and i in pick:
            lst = per_s.setdefault(int(t), [])
            if len(lst) < 20:
                lst.append((t, cv2.resize(f, (108, int(f.shape[0] * 108 / f.shape[1])))))
        if i % ocr_every == 0:
            H, W = f.shape[:2]
            res, _ = eng(cv2.resize(f, (W // 2, H // 2)))
            for box, txt, conf in res or []:
                if conf < 0.6:
                    continue
                xs = [p[0] / (W / 2) for p in box]; ys = [p[1] / (H / 2) for p in box]
                ocr.append({'t': round(t, 2), 'text': txt, 'conf': round(float(conf), 2),
                            'box': [round(min(xs), 3), round(min(ys), 3), round(max(xs), 3), round(max(ys), 3)]})
        i += 1
    tile(index, os.path.join(outd, f'{vid}_1fps.jpg'), cols=10, w=162)
    for sec, fr in per_s.items():
        tile(fr, os.path.join(outd, f'{vid}_s{sec:02d}.jpg'), cols=10, w=108)
    return fps, i, cuts, ocr


def audio(path):
    import librosa, subprocess, tempfile
    wav = tempfile.mktemp(suffix='.wav')
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', path, '-ac', '1', '-ar', '22050', wav], check=True)
    y, sr = librosa.load(wav, sr=22050)
    os.remove(wav)
    hop = 512
    rms = librosa.feature.rms(y=y, hop_length=hop)[0]
    tt = librosa.frames_to_time(np.arange(len(rms)), sr=sr, hop_length=hop)
    tempo, beats = librosa.beat.beat_track(y=y, sr=sr, hop_length=hop)
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=hop)
    mf = librosa.feature.mfcc(y=y, sr=sr, hop_length=hop, n_mfcc=13)
    feat = np.vstack([chroma, mf / 50])
    # novelty: distance between 2 s windows before / after each frame (song changes)
    w = int(2 * sr / hop)
    nov = np.zeros(feat.shape[1])
    for i in range(w, feat.shape[1] - w):
        a = feat[:, i - w:i].mean(1); b = feat[:, i:i + w].mean(1)
        nov[i] = np.linalg.norm(a - b)
    per_s = [round(float(20 * np.log10(rms[(tt >= s) & (tt < s + 1)].mean() + 1e-6)), 1) for s in range(int(tt[-1]) + 1)]
    peaks = []
    thr = np.percentile(nov, 93)
    for i in range(1, len(nov) - 1):
        if nov[i] > thr and nov[i] == nov[max(0, i - w):i + w].max():
            peaks.append(round(float(tt[i]), 2))
    return {'tempo': float(np.atleast_1d(tempo)[0]), 'rms_db_per_s': per_s, 'novelty_peaks': peaks,
            'silences': [round(float(t), 2) for t, r in zip(tt, rms) if r < 0.005][:200:10]}


if __name__ == '__main__':
    path, outd = sys.argv[1], sys.argv[2]
    os.makedirs(outd, exist_ok=True)
    vid = os.path.splitext(os.path.basename(path))[0]
    fps, n, cuts, ocr = one_pass(path, outd, vid, sheets='--sheets' in sys.argv)
    res = {'id': vid, 'fps': fps, 'frames': n, 'dur': round(n / fps, 2), 'cuts': cuts, 'ocr': ocr, 'audio': audio(path)}
    json.dump(res, open(os.path.join(outd, f'{vid}.json'), 'w', encoding='utf8'), ensure_ascii=False, indent=1)
    print(vid, 'dur', res['dur'], 'cuts', len(cuts), 'ocr', len(ocr))
