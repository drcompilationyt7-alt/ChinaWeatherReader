"""
VISION GATE for automatically mined sound hits (monthly refresh on GitHub, 2026-10-17).

The owner's bar for a sound hit is the BY-EYE check of a dense strip: a first-time viewer instantly names the action that makes the
sound in ONE word (shoot, reload, slash / clash, sheathe, punch / kick WITH visible contact, stomp, slam, knock, clap, slap, snap,
shatter / crack, drop ...); no FX-only flashes, faces, gestures, poses, air punches, logos / subtitles, live action, dark smears.
CI cannot look, so every candidate mini (core/pools/hit_scan.py cut: <= 3 s around the transient) is shown to a vision model:
  - the clip itself (640 px, with its audio: the model hears the hit) and
  - the dense strip the by-eye review used (hit - 0.48 s .. + 0.20 s every 0.04 s, the hit frame framed red).
Two different models must both accept (HIT_VISION_MODELS, default gemini-2.5-flash + gemini-3.5-flash; with one model available
it votes twice at a higher bar), each with confidence >= HIT_VISION_MIN_CONF (0.85), no listed problem, an allowed action word,
the cause on screen BEFORE the sound, and contact visible for punch / kick / slap. Anything unclear is a reject (grow-only pool:
a missed good hit costs nothing, a bad one costs a short).

    python core/pools/hit_vision.py judge --mini FILE.mp4 --hit-t 1.20 [--tag sword]
    python core/pools/hit_vision.py calibrate --dir CALIB_DIR    (CALIB_DIR/labels.json {name: true|false} + CALIB_DIR/<name>.mp4)

Keys: GEMINI_API_KEY, GEMINI_API_KEY_2 ... (GitHub secrets; never printed / written). Requests are paced (HIT_VISION_PACE_S, 4 s).
"""
import argparse
import base64
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

BASE = 'https://generativelanguage.googleapis.com/v1beta'
MODELS = [m.strip() for m in os.environ.get('HIT_VISION_MODELS', 'gemini-2.5-flash,gemini-3.5-flash').split(',') if m.strip()]
MIN_CONF = float(os.environ.get('HIT_VISION_MIN_CONF', '0.85'))
PACE_S = float(os.environ.get('HIT_VISION_PACE_S', '4'))
WORDS = {'shoot', 'fire', 'reload', 'cock', 'slash', 'clash', 'parry', 'block', 'stab', 'sheathe', 'unsheathe', 'draw', 'punch',
         'kick', 'stomp', 'step', 'slam', 'knock', 'clap', 'slap', 'snap', 'click', 'shatter', 'crack', 'break', 'smash', 'drop',
         'hit', 'strike', 'bang', 'crash', 'land', 'whip', 'chop', 'cut', 'pierce', 'impact', 'throw', 'catch', 'grab', 'tear'}
CONTACT_WORDS = {'punch', 'kick', 'slap', 'hit', 'strike', 'impact', 'chop'}
PROBLEMS = ['live_action', 'video_game', 'logo_or_watermark', 'subtitles_or_text', 'fx_flash_only', 'face_or_reaction_only',
            'gesture_or_pose', 'no_contact_visible', 'cause_too_small', 'too_dark_or_blurry', 'cut_before_action', 'sound_off_sync',
            'nudity_or_sexual', 'minor_in_danger', 'unclear']

PROMPT = """You review clips for an anime "sound edit": every shot must show the ONE obvious physical action that makes the sharp sound,
so that a first-time viewer instantly knows what made it (a gun aimed then fired, a blade swing then the clash, a fist landing ON a body,
a boot stomping, a door slammed, a glass shattering, a reload). The sharp sound is at about {hit:.2f} s into the clip.
Image 1 is a strip of frames from {a:.2f} s to {b:.2f} s (every 0.04 s, left to right, top to bottom); the frame at the sound is framed RED.
REJECT (set clear=false) when: only an energy / FX flash or light is visible, only a face / reaction, a gesture / pose / salute / pointing,
a swing or punch without visible contact, the cause is tiny / off-screen / unreadable / too dark or blurry, the cut happens before the
action, the sound does not match the visible action, it is live action or a video game render, there is a channel logo / watermark /
subtitles / any text, nudity or sexual content. Be strict: if you hesitate, it is not clear.
Answer ONLY JSON: {{"clear": true|false, "action_word": "<one verb, e.g. shoot, reload, slash, clash, sheathe, punch, kick, stomp, slam, knock, clap, slap, snap, shatter, crack, drop>",
"cause_before_sound": true|false, "contact_visible": true|false|null, "confidence": 0.0-1.0, "problems": [subset of {problems}], "note": "<= 12 words"}}"""


def log(*a):
    print('[hit_vision]', *a, file=sys.stderr, flush=True)


def keys():
    ks = [os.environ[k] for k in sorted(os.environ) if re.fullmatch(r'GEMINI_API_KEY(_\d+)?', k) and os.environ.get(k)]
    return ks


def small(path, out):
    """640 px wide, crf 26, AAC 96k: what the model sees (and the strip source)."""
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-nostdin', '-i', path, '-vf', 'scale=640:-2', '-c:v', 'libx264', '-crf', '26',
                    '-preset', 'veryfast', '-threads', '2', '-c:a', 'aac', '-ac', '2', '-b:a', '96k', '-movflags', '+faststart', out], check=True,
                   capture_output=True)
    return out


def strip(path, hit_t, out, pre=0.48, post=0.20, step=0.04, cols=6, cw=213):
    import cv2
    import numpy as np
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    ts = [hit_t + k * step for k in range(-int(round(pre / step)), int(round(post / step)) + 1)]
    cells = []
    for t in ts:
        idx = int(round(t * fps))
        if idx < 0 or (n and idx >= n):
            continue
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, f = cap.read()
        if not ok:
            continue
        h = int(round(f.shape[0] * cw / f.shape[1]))
        f = cv2.resize(f, (cw, h), interpolation=cv2.INTER_AREA)
        if abs(t - hit_t) < step / 2:
            cv2.rectangle(f, (0, 0), (cw - 1, h - 1), (0, 0, 255), 4)
        cells.append(f)
    cap.release()
    if not cells:
        return None, (0, 0)
    h = cells[0].shape[0]
    rows = (len(cells) + cols - 1) // cols
    sheet = np.zeros((rows * h, cols * cw, 3), np.uint8)
    for i, c in enumerate(cells):
        r, k = divmod(i, cols)
        sheet[r * h:(r + 1) * h, k * cw:(k + 1) * cw] = c[:h]
    cv2.imwrite(out, sheet, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return out, (max(0.0, ts[0]), ts[-1])


_LAST = {'t': 0.0}
_DEAD = set()


def _call(model, key, parts, timeout=120):
    wait = PACE_S - (time.time() - _LAST['t'])
    if wait > 0:
        time.sleep(wait)
    _LAST['t'] = time.time()
    body = json.dumps({'contents': [{'role': 'user', 'parts': parts}],
                       'generationConfig': {'temperature': 0, 'responseMimeType': 'application/json', 'maxOutputTokens': 400}}).encode()
    req = urllib.request.Request(f'{BASE}/models/{model}:generateContent', data=body,
                                 headers={'Content-Type': 'application/json', 'x-goog-api-key': key})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read().decode())
    txt = ''.join(p.get('text', '') for c in d.get('candidates') or [] for p in (c.get('content') or {}).get('parts') or [])
    m = re.search(r'\{.*\}', txt, re.S)
    return json.loads(m.group(0)) if m else None


def ask(model, parts):
    """One answer from `model`, rotating the keys on quota / server errors. None = unavailable."""
    ks = keys()
    if not ks:
        raise RuntimeError('no GEMINI_API_KEY* in the environment')
    for attempt in range(len(ks) * 2):
        k = ks[(attempt + hash(model)) % len(ks)]
        if (model, k) in _DEAD:
            continue
        try:
            return _call(model, k, parts)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None                      # model not served
            if e.code in (400, 401, 403):
                _DEAD.add((model, k))
            time.sleep(min(30, 3 * (attempt + 1)))
        except (urllib.error.URLError, TimeoutError, ValueError, OSError):
            time.sleep(3)
    return None


def accept(v, bar):
    if not isinstance(v, dict):
        return False, 'no answer'
    word = str(v.get('action_word') or '').lower().strip()
    probs = [p for p in (v.get('problems') or []) if p in PROBLEMS]
    try:
        conf = float(v.get('confidence') or 0)
    except (TypeError, ValueError):
        conf = 0.0
    if not v.get('clear'):
        return False, 'not clear: ' + ','.join(probs)[:60]
    if probs:
        return False, 'problems: ' + ','.join(probs)[:60]
    if conf < bar:
        return False, f'confidence {conf:.2f}'
    if not v.get('cause_before_sound'):
        return False, 'cause not before the sound'
    w0 =word.split()[0] if word else ''
    if w0 not in WORDS:
        return False, f'action word {word!r}'
    if w0 in CONTACT_WORDS and v.get('contact_visible') is not True:
        return False, 'no contact'
    return True, w0


def judge(mini, hit_t, tag=None, tmp=None):
    """{'ok', 'word', 'votes': [{model, answer, ok, why}], 'why'}"""
    tmp = tmp or tempfile.mkdtemp(prefix='hitvis-')
    base = os.path.splitext(os.path.basename(mini))[0]
    sm = small(mini, os.path.join(tmp, base + '.640.mp4'))
    st, (a, b) = strip(sm, float(hit_t), os.path.join(tmp, base + '.strip.jpg'))
    if not st:
        return {'ok': False, 'why': 'no frames', 'votes': []}
    parts = [{'inline_data': {'mime_type': 'image/jpeg', 'data': base64.b64encode(open(st, 'rb').read()).decode()}},
             {'inline_data': {'mime_type': 'video/mp4', 'data': base64.b64encode(open(sm, 'rb').read()).decode()},
              'video_metadata': {'fps': 12}},
             {'text': PROMPT.format(hit=float(hit_t), a=a, b=b, problems=', '.join(PROBLEMS))}]
    votes = []
    models = [m for m in MODELS]
    for m in models:
        ans = ask(m, parts)
        if ans is None:
            continue
        ok, why = accept(ans, MIN_CONF)
        votes.append({'model': m, 'answer': ans, 'ok': ok, 'why': why})
    if len(votes) == 1:                           # one model served: it must clear a higher bar
        ok, why = accept(votes[0]['answer'], max(MIN_CONF, 0.92))
        votes[0].update(ok=ok, why=why)
    good = votes and all(v['ok'] for v in votes)
    words = [v['why'] for v in votes if v['ok']]
    out = {'ok': bool(good), 'word': words[0] if good else None, 'votes': votes, 'strip': st,
           'why': 'accepted' if good else ('no model answered' if not votes else '; '.join(f"{v['model']}: {v['why']}" for v in votes if not v['ok']))}
    return out


def calibrate(d):
    labels = json.load(open(os.path.join(d, 'labels.json'), encoding='utf-8'))
    rows = []
    for name, lab in sorted(labels.items()):
        p = os.path.join(d, name + '.mp4')
        if not os.path.isfile(p):
            continue
        hit_t = float(lab['hit_t'])
        r = judge(p, hit_t, tmp=os.path.join(d, '_tmp'))
        rows.append({'name': name, 'label': bool(lab['ok']), 'pred': r['ok'], 'why': r['why'],
                     'conf': [((v.get('answer') or {}).get('confidence')) for v in r['votes']]})
        log(f"{name}: label {lab['ok']} pred {r['ok']} ({r['why'][:80]})")
    tp = sum(r['label'] and r['pred'] for r in rows)
    fp = sum((not r['label']) and r['pred'] for r in rows)
    fn = sum(r['label'] and not r['pred'] for r in rows)
    tn = sum((not r['label']) and not r['pred'] for r in rows)
    rep = {'n': len(rows), 'tp': tp, 'fp': fp, 'fn': fn, 'tn': tn,
           'precision': round(tp / max(1, tp + fp), 3), 'recall': round(tp / max(1, tp + fn), 3), 'models': MODELS, 'min_conf': MIN_CONF,
           'rows': rows}
    return rep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['judge', 'calibrate'])
    ap.add_argument('--mini')
    ap.add_argument('--hit-t', type=float)
    ap.add_argument('--tag')
    ap.add_argument('--dir')
    ap.add_argument('--out')
    a = ap.parse_args()
    r = judge(a.mini, a.hit_t, a.tag) if a.cmd == 'judge' else calibrate(a.dir)
    s = json.dumps(r, indent=1, default=str)
    if a.out:
        with open(a.out, 'w', encoding='utf-8') as f:
            f.write(s)
    print(s if a.cmd == 'judge' else json.dumps({k: v for k, v in r.items() if k != 'rows'}))


if __name__ == '__main__':
    main()
