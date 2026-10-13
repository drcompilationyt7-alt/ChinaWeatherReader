#!/usr/bin/env python3
"""
The owner's clip rules as scores and gates (2026-10-01), used by core/edit/library.py when a preset is
named (--preset <id>) or a rule is given on the command line. Everything is measured per candidate
window and written to the manifest ("rules" on every shot, rules.json with the rejected ones), so a
builder can see why a clip was picked or dropped.

  subject   female / male / group / no-character share of the kept frames (SigLIP). subject_pref
            'female' boosts women, 'any' leaves the score alone.
  solo      one dominant subject: SigLIP "two characters / couple / group / over-the-shoulder
            conversation / comedic gag two-shot" prompts plus the anime face boxes (a second face of
            comparable size). 'strict' (beauty presets) rejects gag / conversation compositions and
            penalises every multi-person shot, 'soft' only rejects gags and conversations.
  mood      per-frame softmax over mood classes (happy / dreamy / warm / wistful-beautiful vs gloomy /
            sad / creepy vs intense / powerful vs comedic). 'beauty' wants the bright, warm, dreamy
            side and hard-rejects gloomy / sad / creepy; 'aura' wants intense / confident / powerful
            and rejects comedic / sad; 'any' measures only.
  dialogue  talking-mouth / phone-call / conversation prompts (the library's old talk class, the
            mouth-flap test and Silero speech stay as they were).
  motion    measured on the decoded window at 1x (not on SigLIP): residual motion after removing the
            global camera shift, split into the subject region (face box grown to hair and body) and
            the rest (hair strands, cloth, water, petals, sparkles, snow); anime held frames are
            skipped by comparing frames 0.3 s apart. motion_beauty is the best `take` seconds' score,
            still_frac the share of near-still steps in it. Slowed / hero / beauty slots reject
            near-still windows.

The per-preset parameters live in presets.CLIP_RULES; `rules_for` merges them with the command line.
"""
import math
import os
import subprocess

import cv2
import numpy as np

# ---------------------------------------------------------------------------- parameters

DEFAULT = dict(subject_pref='any', solo='off', mood='any', motion_min=0.0, still_max=1.0, slow=1.0,
               dialogue='reject')
SOLO = ('off', 'soft', 'strict')
MOODS = ('any', 'beauty', 'aura')

# thresholds (calibrated on the cached pools, see core/edit/refs/clips-progress.md 2026-10-01)
TH = {
    'gloom_mean': 0.75, 'gloom_peak': 0.95, 'gloom_peak_mean': 0.55,   # beauty: gloomy / sad / creepy share of the kept frames
    'aura_sad_mean': 0.6,                           # aura: sad / crying shots
    'comic_mean': 0.7,                              # comedic share (beauty and aura: reject)
    'multi_strict': 0.45, 'multi_soft': 0.72,       # multi-subject composition: reject above
    'convo': 0.5,                                   # over-the-shoulder / conversation share
    'phone': 0.6,                                   # talking on / holding a phone
    'talkmouth': 0.7,                               # mouth open / phone call share
    'distress': 0.5, 'distress_peak': 0.9, 'aura': 0.5,   # beauty: teary / sweating / anxious share; dark aura-power frames
    'male_max': 0.8,                                # beauty: a male-dominant shot (no woman in it) is rejected
}

LOOK = {  # SigLIP prompt groups per kind
    'anime': {
        'm_pos': ['a happy smiling anime girl', 'a dreamy beautiful anime girl in soft light',
                  'a bright warm anime scene in golden sunlight',
                  'a beautiful anime girl with flowing hair in a colourful bright scene',
                  'a cute anime girl with a gentle smile', 'a lovely anime girl blushing shyly'],
        'm_neu': ['an ordinary anime scene', 'an anime character with a neutral expression', 'a calm anime girl portrait',
                  'an anime character in a bright room', 'an anime school scene in daylight', 'an anime girl looking to the side'],
        'm_gloomy': ['a gloomy depressed anime character in dark light', 'a dark moody anime scene with deep shadows',
                     'a lonely anime character in a dim room', 'a bleak dreary anime scene'],
        'm_sad': ['a sad crying anime character', 'an anime character with a hopeless dejected face',
                  'an anime character weeping with tears'],
        'm_creepy': ['a horror anime scene', 'a creepy anime character with a sinister grin',
                     'an unsettling dark anime scene with red glowing eyes'],
        'm_intense': ['an intense powerful anime character with a fierce stare', 'an anime character powering up with glowing energy',
                      'a confident anime character walking in dramatic light'],
        'm_comic': ['a comedic anime gag with an exaggerated funny face', 'a goofy chibi anime reaction with sweat drops'],
        # round 11 (cinematic_slowmo critics): a teary / sweating / flustered face and a dark aura frame read as distress
        # or gloom in a dreamy beauty edit
        'm_distress': ['an anxious nervous anime girl with a sweat drop and a trembling mouth', 'a crying anime girl with tears in her eyes and clenched teeth',
                       'a flustered distressed anime girl with glistening teary eyes', 'a frightened anime girl with a worried frown'],
        'm_aura': ['an anime character in front of a dark purple aura with flames of energy', 'an anime character surrounded by a dark menacing aura',
                   'an anime character with a sinister smirk and a glowing dark power aura'],
        's_f': ['a beautiful anime girl', 'a female anime character with long hair'],
        's_m': ['a handsome anime boy', 'a male anime character, a man'],
        's_g': ['a group of several anime characters together'],
        's_n': ['an anime scene with no characters, only scenery'],
        'c_solo': ['a single anime character alone in the frame', 'a portrait of one anime character'],
        'c_two': ['two anime characters standing close together', 'a couple of anime characters facing each other',
                  'a group of anime characters'],
        'c_convo': ['an over-the-shoulder shot of two characters talking', 'two anime characters having a conversation'],
        'c_gag': ['two anime characters blushing in a comedic scene', 'a funny anime gag with two characters posing'],
        't_talk': ['an anime character with the mouth wide open speaking', 'an anime character talking to someone'],
        't_phone': ['an anime character talking on a telephone', 'an anime character holding a phone receiver to the ear',
                    'an anime character holding a smartphone to the ear'],
        't_quiet': ['an anime character with a closed mouth and a calm expression', 'an anime character looking at the camera in silence'],
        'v_bad': ['a menacing anime villain', 'a monster or demon creature', 'a non-human alien creature'],
        'v_ok': ['a human anime character', 'an ordinary anime girl or boy'],
        'a_yes': ['a stunning glamorous anime girl with striking eyes', 'a beautiful anime girl with long hair flowing in the wind',
                  'an anime girl in a pretty outfit beside sparkling water in summer', 'an anime girl in neon city night lights with a confident alluring look',
                  'an anime girl with sparkles and glowing light particles around her'],
        'a_no': ['a plain unremarkable anime character', 'a dull ordinary anime frame'],
    },
    'group': {
        'm_pos': ['a happy smiling woman', 'a dreamy beautiful woman in soft light', 'a bright warm scene in golden sunlight',
                  'a graceful idol with flowing hair in a colourful bright scene', 'a cute idol with a gentle smile'],
        'm_neu': ['an ordinary scene', 'a person with a neutral expression', 'a calm portrait of a woman',
                  'a person in a bright room', 'a stage in bright light'],
        'm_gloomy': ['a gloomy dark moody scene with deep shadows', 'a lonely person in a dim room', 'a bleak dreary scene'],
        'm_sad': ['a sad crying person', 'a person with a hopeless dejected face'],
        'm_creepy': ['a horror scene', 'a creepy person with a sinister grin', 'an unsettling dark scene with red glowing eyes'],
        'm_intense': ['a powerful idol with a fierce confident stare', 'a dramatic stage with intense light and smoke',
                      'a confident idol walking in dramatic light'],
        'm_comic': ['a goofy funny face with an exaggerated expression', 'a variety show reaction'],
        'm_distress': ['an anxious nervous person with a worried face', 'a crying person with tears in the eyes'],
        'm_aura': ['a person in front of dark smoke and a menacing glow', 'a person with a sinister smirk in dark light'],
        's_f': ['a beautiful woman', 'a female K-pop idol'],
        's_m': ['a handsome man', 'a male K-pop idol'],
        's_g': ['a group of several people together'],
        's_n': ['a scene with no people, only a set or scenery'],
        'c_solo': ['a single person alone in the frame', 'a portrait of one person'],
        'c_two': ['two people standing close together', 'a group of people'],
        'c_convo': ['two people having a conversation', 'an over-the-shoulder shot of two people talking'],
        'c_gag': ['two people making funny faces together'],
        't_talk': ['a person speaking with the mouth open to the camera', 'a person talking to someone'],
        't_phone': ['a person talking on a telephone', 'a person holding a phone to the ear'],
        't_quiet': ['a person with a closed mouth and a calm expression'],
        'v_bad': ['a menacing villain', 'a monster costume'],
        'v_ok': ['an ordinary person'],
        'a_yes': ['a stunning glamorous idol with striking eyes', 'a beautiful woman with long hair flowing in the wind',
                  'a gorgeous idol in a pretty outfit with glowing lights around her', 'a confident alluring idol in neon night lights'],
        'a_no': ['a plain unremarkable person', 'a dull ordinary video frame'],
    },
}
SUBJ_CLASSES = ('s_f', 's_m', 's_g', 's_n')
MOOD_NEG = ('m_gloomy', 'm_sad', 'm_creepy')


def prompt_groups(kind):
    return LOOK['anime' if kind == 'anime' else 'group']


def rules_for(preset, cli=None, table=None):
    """The rule set of one run: presets.CLIP_RULES[preset] (backward compatible: nothing for an unknown
    or missing preset), overridden by the command line (dict of non-None values). Returns None when
    no rule asks for anything, so the library behaves exactly as before."""
    r = dict(DEFAULT)
    found = False
    if table is None:
        try:
            import presets as _p
            table = getattr(_p, 'CLIP_RULES', {}) or {}
        except Exception:
            table = {}
    if preset and preset in table:
        r.update(table[preset])
        found = True
    for k, v in (cli or {}).items():
        if v is not None:
            r[k] = v
            found = True
    if not found:
        return None
    r['preset'] = preset or ''
    if r['solo'] not in SOLO:
        r['solo'] = 'off'
    if r['mood'] not in MOODS:
        r['mood'] = 'any'
    return r


# ---------------------------------------------------------------------------- SigLIP terms

def _softmax(x):
    x = x - x.max(1, keepdims=True)
    e = np.exp(x)
    return e / e.sum(1, keepdims=True)


def _grp(lg, idx, names):
    """(frames, len(names)) best-prompt logit of each group."""
    return np.stack([lg[:, idx[n]].max(1) for n in names], 1)


def _face_geometry(c, kind):
    """Share of key frames with a second face of comparable size (>= 0.3 x the largest, both at least
    0.004 of the frame): two characters side by side or face to face."""
    faces = c.get('anime_faces') if kind == 'anime' else c.get('real_faces')
    if not faces:
        return 0.0, 0
    multi = n = 0
    for _, bs in faces.items():
        a = sorted((b[2] * b[3] for b in bs if b[2] * b[3] >= 0.004), reverse=True)
        if not a:
            continue
        n += 1
        if len(a) >= 2 and a[1] >= 0.3 * a[0]:
            multi += 1
    return (multi / n if n else 0.0), n


def siglip_terms(c, lg, idx, kind):
    """All the SigLIP rule terms of one candidate from its (frames x prompts) logits. `idx` maps a
    group name to its prompt columns."""
    # mood: each negative / special class against the best of the pleasant + neutral anchors, so an
    # ordinary face is neither gloomy nor creepy (a softmax over all the classes made every plain
    # shot "creepy")
    pos_l = lg[:, idx['m_pos']].max(1)
    neu_l = lg[:, idx['m_neu']].max(1)
    ref = np.maximum(pos_l, neu_l)

    def vs(name, base, T=3.0):
        return 1 / (1 + np.exp(-(lg[:, idx[name]].max(1) - base) / T))
    g_f, s_f, c_f = vs('m_gloomy', ref), vs('m_sad', ref), vs('m_creepy', ref)
    neg_f = np.maximum(np.maximum(g_f, s_f), c_f)
    neg, neg_max = float(neg_f.mean()), float(neg_f.max())
    pos = float((1 / (1 + np.exp(-(pos_l - neu_l) / 3.0))).mean())
    intense = float(vs('m_intense', neu_l).mean())
    comic = float(vs('m_comic', ref).mean())
    distress_f = vs('m_distress', ref) if 'm_distress' in idx else np.zeros(len(ref))
    aura_f = vs('m_aura', ref) if 'm_aura' in idx else np.zeros(len(ref))
    distress, aura_p = float(distress_f.mean()), float(aura_f.mean())
    distress_peak = float(distress_f.max()) if len(distress_f) else 0.0
    ps = _softmax(_grp(lg, idx, SUBJ_CLASSES)).mean(0)
    subj = dict(zip(SUBJ_CLASSES, ps))
    # composition: multi-person prompts against the solo ones, frame-wise
    y = lg[:, idx['c_two']].max(1) - lg[:, idx['c_solo']].max(1)
    multi_p = float((1 / (1 + np.exp(-y / 2.0))).mean())
    convo_p = float((1 / (1 + np.exp(-(lg[:, idx['c_convo']].max(1) - lg[:, idx['c_solo']].max(1)) / 2.0))).mean())
    gag_p = float((1 / (1 + np.exp(-(lg[:, idx['c_gag']].max(1) - lg[:, idx['c_solo']].max(1)) / 2.0))).mean())
    geo, _ = _face_geometry(c, kind)
    multi = float(np.clip(0.6 * multi_p + 0.4 * geo, 0, 1))
    solo = 1.0 - max(multi, 0.8 * convo_p, 0.8 * gag_p)
    ph_ref = np.maximum(np.maximum(lg[:, idx['t_quiet']].max(1), lg[:, idx['m_neu']].max(1)), lg[:, idx['s_n']].max(1))
    ph = float((1 / (1 + np.exp(-(lg[:, idx['t_phone']].max(1) - ph_ref) / 2.0))).mean())
    vl = float((1 / (1 + np.exp(-(lg[:, idx['v_bad']].max(1) - lg[:, idx['v_ok']].max(1)) / 2.0))).mean())
    ap = float((1 / (1 + np.exp(-(lg[:, idx['a_yes']].max(1) - lg[:, idx['a_no']].max(1)) / 4.0))).mean())
    tk = float((1 / (1 + np.exp(-(lg[:, idx['t_talk']].max(1) - lg[:, idx['t_quiet']].max(1)) / 2.0))).mean())
    return {
        'gloomy': round(float(g_f.mean()), 3), 'sad': round(float(s_f.mean()), 3), 'creepy': round(float(c_f.mean()), 3),
        'intense': round(intense, 3), 'comic': round(comic, 3), 'distress': round(distress, 3), 'distress_peak': round(distress_peak, 3), 'aura': round(aura_p, 3),
        'mood_pos': round(pos, 3), 'mood_neg': round(neg, 3), 'mood_neg_max': round(neg_max, 3),
        'female': round(float(subj['s_f']), 3), 'male': round(float(subj['s_m']), 3), 'group': round(float(subj['s_g']), 3),
        'noone': round(float(subj['s_n']), 3),
        'multi': round(multi, 3), 'multi_sig': round(multi_p, 3), 'multi_geo': round(geo, 3),
        'convo': round(convo_p, 3), 'gag': round(gag_p, 3), 'solo': round(solo, 3),
        'talk_mouth': round(tk, 3), 'phone': round(ph, 3), 'villain': round(vl, 3), 'appeal': round(ap, 3),
    }


def judge(c, t, r, luma=None):
    """Rule gates and multiplier from the terms t (siglip_terms) under rule set r. Returns
    (reject reason | None, multiplier, tier multiplier, extra terms)."""
    ex = {}
    bright = float(np.clip((c.get('luma', 120) - 70) / 60.0, 0, 1)) if c.get('luma') is not None else 1.0
    t['bright'] = round(bright, 3)
    mood = r['mood']
    if mood == 'beauty':
        fit = (0.45 + 0.55 * t['mood_pos']) * (1 - 0.8 * t['mood_neg']) * (0.55 + 0.45 * bright)
        t['mood_fit'] = round(fit, 3)
        if t['mood_neg'] >= TH['gloom_mean'] or (t['mood_neg_max'] >= TH['gloom_peak'] and t['mood_neg'] >= TH['gloom_peak_mean']):
            return 'gloomy', 0.0, 0.0, ex
        if t['comic'] >= TH['comic_mean']:
            return 'comic', 0.0, 0.0, ex
        # round 11: tears / sweat / clenched teeth (distress) and a dark aura frame never belong in a dreamy beauty edit
        if t.get('distress', 0.0) >= TH['distress'] or t.get('distress_peak', 0.0) >= TH['distress_peak']:
            return 'distress', 0.0, 0.0, ex
        if t.get('aura', 0.0) >= TH['aura'] and (c.get('luma') is None or c.get('luma', 120) < 130):
            return 'aura_dark', 0.0, 0.0, ex
        if c.get('luma') is not None and c['luma'] < 100 and (t.get('aura', 0.0) >= 0.3 or c.get('colorful', 0) >= 150):
            return 'aura_dark', 0.0, 0.0, ex
    elif mood == 'aura':
        fit = (0.2 + 0.8 * float(np.clip(t['intense'] / 0.6, 0, 1))) * (1 - 0.5 * t['sad'])
        t['mood_fit'] = round(fit, 3)
        if t['comic'] >= TH['comic_mean'] or t['sad'] >= TH['aura_sad_mean']:
            return 'off_mood', 0.0, 0.0, ex
    else:
        fit = 1.0
        t['mood_fit'] = 1.0
    sol = r['solo']
    if sol == 'strict':
        if t['convo'] >= TH['convo'] or t['gag'] >= TH['convo'] or t['multi'] >= TH['multi_strict']:
            return 'not_solo', 0.0, 0.0, ex
    elif sol == 'soft':
        if t['convo'] >= TH['convo'] or t['gag'] >= TH['convo'] or t['multi'] >= TH['multi_soft']:
            return 'not_solo', 0.0, 0.0, ex
    if r['dialogue'] == 'reject' and t['phone'] >= TH['phone']:
        return 'phone', 0.0, 0.0, ex
    if r['dialogue'] == 'reject' and t['talk_mouth'] >= TH['talkmouth'] and c.get('motion_raw', 1) < 0.03:
        return 'talk_mouth', 0.0, 0.0, ex
    # multipliers
    mult = ((0.55 + 0.45 * fit) if mood == 'beauty' else (0.4 + 0.6 * fit)) if mood != 'any' else 1.0
    tier = (0.6 + 0.4 * fit) if mood != 'any' else 1.0
    if r['subject_pref'] == 'female':
        if r.get('male_reject', True) and t['male'] >= TH['male_max'] and t['female'] < 0.3:
            return 'male_subject', 0.0, 0.0, ex
        fp = float(np.clip(t['female'] + 0.35 * t['group'], 0, 1))
        sm = 0.55 + 0.45 * fp
        mult *= sm
        tier *= 0.5 + 0.5 * fp
        t['subject_mult'] = round(sm, 3)
    if sol != 'off':
        sm2 = (0.5 + 0.5 * t['solo']) if sol == 'strict' else (0.8 + 0.2 * t['solo'])
        mult *= sm2
        tier *= sm2
        t['solo_mult'] = round(sm2, 3)
    if r.get('appeal'):
        apn = float(np.clip((t['appeal'] - 0.25) / 0.5, 0, 1))
        mult *= 0.85 + 0.15 * apn
        tier *= 0.9 + 0.1 * apn
        t['appeal_mult'] = round(0.85 + 0.15 * apn, 3)
    t['rules_mult'] = round(mult, 3)
    return None, mult, tier, ex


# ---------------------------------------------------------------------------- motion at 1x

def _decode_gray(path, t0, dur, width=192, fps=10, threads=2):
    cap = cv2.VideoCapture(path)
    W = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
    H = cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
    cap.release()
    if not W or not H:
        return None
    h = max(16, int(round(width * H / W / 2)) * 2)
    cmd = ['ffmpeg', '-v', 'error', '-threads', str(threads), '-ss', f'{max(0.0, t0):.3f}', '-t', f'{dur:.3f}', '-i', path,
           '-an', '-vf', f'fps={fps},scale={width}:{h}:flags=area,format=gray', '-f', 'rawvideo', '-']
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=120)
    except Exception:
        return None
    n = len(p.stdout) // (width * h)
    if n < 4:
        return None
    return np.frombuffer(p.stdout[:n * width * h], np.uint8).reshape(n, h, width)


def _subject_mask(shape, faces_by_key, content_box=None):
    """Boolean mask of the subject region: every face box of the candidate (kept frames) grown to
    hair and upper body. No face: the central 60 % of the frame."""
    h, w = shape
    m = np.zeros((h, w), bool)
    got = False
    for _, bs in (faces_by_key or {}).items():
        for b in bs:
            if b[2] * b[3] < 0.004:
                continue
            x, y, bw, bh = b[0], b[1], b[2], b[3]
            x0, x1 = max(0.0, x - 0.9 * bw), min(1.0, x + 1.9 * bw)
            y0, y1 = max(0.0, y - 0.7 * bh), min(1.0, y + 3.2 * bh)
            m[int(y0 * h):int(math.ceil(y1 * h)), int(x0 * w):int(math.ceil(x1 * w))] = True
            got = True
    if not got:
        m[int(0.2 * h):int(0.8 * h), int(0.2 * w):int(0.8 * w)] = True
    return m, got


def measure_motion(path, t0, dur, faces_by_key=None, take=None, lag=3, fps=10):
    """Residual motion of the window at 1x. Returns a dict (motion_beauty 0-1, still_frac, subject,
    secondary, curve) or None when the window cannot be decoded. `curve` is the per-step score (one
    value per analysis frame, 10 fps) so the caller can choose the best `take` seconds."""
    g = _decode_gray(path, t0, dur, fps=fps)
    if g is None or len(g) <= lag + 1:
        return None
    gf = [cv2.GaussianBlur(f, (3, 3), 0).astype(np.float32) for f in g]
    h, w = gf[0].shape
    mask, has_face = _subject_mask((h, w), faces_by_key)
    outside = ~mask
    subj_v, sec_v, frac_v, raw_v, cam_v = [], [], [], [], []
    for i in range(len(gf) - lag):
        a, b = gf[i], gf[i + lag]
        a8, b8 = g[i], g[i + lag]
        cam = 0.0
        # camera move (pan / push / zoom / roll) removed first: a similarity transform fitted on
        # tracked features, so a slow push on a still drawing does not count as movement
        pts = cv2.goodFeaturesToTrack(a8, 160, 0.01, 5)
        if pts is not None and len(pts) >= 12:
            nxt, st, _ = cv2.calcOpticalFlowPyrLK(a8, b8, pts, None, winSize=(15, 15), maxLevel=2)
            ok = st.ravel() == 1
            if ok.sum() >= 10:
                M, inl = cv2.estimateAffinePartial2D(pts[ok], nxt[ok], method=cv2.RANSAC, ransacReprojThreshold=1.2)
                if M is not None:
                    sc = math.hypot(M[0, 0], M[1, 0])
                    if abs(sc - 1) <= 0.15 and abs(M[0, 2]) <= 0.1 * w and abs(M[1, 2]) <= 0.1 * h:
                        a = cv2.warpAffine(a, M, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
                        cam = abs(sc - 1) * 10 + (abs(M[0, 2]) / w + abs(M[1, 2]) / h) * 5
        d = np.abs(a - b - (float(a.mean()) - float(b.mean())))      # a fade / flash is not movement
        raw_v.append(float(np.abs(gf[i] - b).mean()))
        cam_v.append(cam)
        subj_v.append(float(d[mask].mean()) if mask.any() else 0.0)
        o = d[outside] if outside.any() else d.ravel()
        sec_v.append(float(o.mean()))
        frac_v.append(float((o > 9).mean()))
    subj_v, sec_v, frac_v, raw_v, cam_v = map(np.array, (subj_v, sec_v, frac_v, raw_v, cam_v))
    # per-step 0-1 (calibrated on the cached pools: held portraits measure 0.05-0.3 grey levels of
    # residual, a turning head / drifting hair 1-5): 1.2 levels in the subject region is clearly moving,
    # 0.7 levels outside it or 2 % of its pixels changing is wind / cloth / water / particles
    s_sub = np.clip(subj_v / 1.2, 0, 1)
    s_sec = np.maximum(np.clip(sec_v / 0.7, 0, 1), np.clip(frac_v / 0.02, 0, 1))
    step = np.maximum(0.7 * s_sub + 0.3 * s_sec, 0.5 * s_sec + 0.2 * s_sub)
    # smooth over 0.5 s: motion is a sustained thing, one jerk is not
    k = np.ones(3) / 3
    cur = np.convolve(step, k, 'same') if len(step) >= 3 else step
    L = max(2, int(round((take or len(cur) / fps) * fps)))
    if L >= len(cur):
        off, L = 0, len(cur)
    else:
        cs = np.concatenate([[0.0], np.cumsum(cur)])
        off = int(np.argmax(cs[L:] - cs[:-L]))
    seg = cur[off:off + L]
    return {
        'motion_beauty': round(float(0.7 * np.median(seg) + 0.3 * np.mean(seg)), 3), 'still_frac': round(float((seg < 0.15).mean()), 3),
        'subject_motion': round(float(np.mean(s_sub[off:off + L])), 3),
        'secondary_motion': round(float(np.mean(s_sec[off:off + L])), 3),
        'raw_subject': round(float(np.median(subj_v)), 3), 'raw_secondary': round(float(np.median(sec_v)), 3),
        'raw_frac': round(float(np.median(frac_v)), 4), 'raw_diff': round(float(np.median(raw_v)), 3), 'camera': round(float(np.median(cam_v)), 3),
        'face_found': bool(has_face), 'window_off': round(off / fps, 2), 'window_len': round(L / fps, 2),
        'curve': [round(float(v), 3) for v in cur],
    }


def motion_gate(mm, r):
    """Reject reason for the motion terms under rule set r (None = pass)."""
    if mm is None:
        return None
    need = r.get('motion_min', 0.0)
    if not need:
        return None
    slow = max(0.35, float(r.get('slow', 1.0) or 1.0))
    need = need / math.sqrt(slow)             # a slowed shot shows less movement per second
    if mm['motion_beauty'] < need or mm['still_frac'] > r.get('still_max', 1.0):
        return 'still'
    return None


def motion_mult(mm, r):
    if mm is None or not r.get('motion_min'):
        return 1.0
    return 0.7 + 0.3 * float(np.clip(mm['motion_beauty'] / 0.6, 0, 1))


# ---------------------------------------------------------------------------- character roster prior
# refs/edit-roster.json (built by refs/edit_roster.py from edit-search evidence + AniList favourites): the
# characters most often featured in cute / beautiful and in aura / hype edits. A GUIDE: roster clips get a boost,
# an off-roster known character or a villain / monster design a mild penalty for beauty presets; nothing is
# filtered and the safety gate never looks at it.
ROSTER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'refs', 'edit-roster.json')
_ROSTER = {}
ROSTER_BOOST = 0.18          # x weight (0-1) on the score of a roster character
ROSTER_SHOW_BOOST = 0.09     # x best weight of the show when only the show is named
OFF_ROSTER = 0.92            # beauty: a known character that is not on the roster
VILLAIN_MULT = 0.85          # beauty: villain / monster / non-human design (SigLIP)


def _norm(t):
    import re
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9 ]+', ' ', (t or '').lower())).strip()


def _toks(name):
    parts = [p for p in _norm(name).split() if p]
    out = []
    if len(parts) >= 2:
        out += [' '.join(parts), ' '.join(reversed(parts))]
    out += [p for p in parts if len(p) >= 4]
    if not out and parts:
        out.append(parts[0])
    return list(dict.fromkeys(out)), (len(parts) == 1 and len(parts[0]) < 4)


_TOKRE = {}


def _tok_re(tk):
    """Compiled word-boundary pattern of a roster token (speed 2026-10-09: the roster has more than re's 512-pattern cache, so every call
    recompiled: 6 s of a window run's setup)."""
    r = _TOKRE.get(tk)
    if r is None:
        import re
        r = _TOKRE[tk] = re.compile(r'(?<![a-z0-9])' + re.escape(tk) + r'(?![a-z0-9])')
    return r


def _hit(title_n, name, anime):
    toks, short = _toks(name)
    for tk in toks:
        if _tok_re(tk).search(title_n):
            if short:
                key = [w for w in _norm(anime).split() if len(w) >= 4][:2]
                if not any(k in title_n for k in key):
                    return False
            return True
    return False


def load_roster(path=None):
    p = path or ROSTER_PATH
    if p in _ROSTER:
        return _ROSTER[p]
    data = None
    try:
        import json
        with open(p, encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        data = None
    _ROSTER[p] = data
    return data


def roster_terms(title, family, char_name=None, roster=None):
    """{'weight', 'match', 'on_roster', 'known_off'} for a source title (and the targeted character in character
    mode) under the beauty / aura roster. weight 0 when nothing matched."""
    r = roster if roster is not None else load_roster()
    if not r or family not in ('beauty', 'aura'):
        return None
    rows = r.get(family) or []
    tn = _norm(title)
    best, who = 0.0, None
    if char_name:
        for row in rows:
            if _norm(row['name']) == _norm(char_name):
                best, who = row['weight'], row['name']
                break
    if who is None:
        for row in rows:
            if row['weight'] > best and _hit(tn, row['name'], row['anime']):
                best, who = row['weight'], row['name']
    show_w, show = 0.0, None
    if who is None:
        for row in rows:
            sn = _norm(row['anime'].split(':')[0])
            if len(sn) >= 4 and sn in tn and row['weight'] * ROSTER_SHOW_BOOST / ROSTER_BOOST > show_w:
                show_w, show = row['weight'], row['anime']
    known_off = False
    if who is None and show is None:
        names = {_norm(row['name']) for row in rows}
        for k in r.get('known_characters') or []:
            if _norm(k['name']) not in names and _hit(tn, k['name'], k['anime']):
                known_off = True
                break
    return {'weight': round(best, 3), 'match': who or (('show:' + show) if show else None), 'show_weight': round(show_w, 3),
            'known_off': known_off, 'family': family}


def roster_mult(rt, villain=0.0):
    """Score multiplier from roster_terms (+ the villain / monster term for beauty). (mult, tier mult)"""
    if not rt:
        return 1.0, 1.0
    m = 1.0
    if rt['weight']:
        m = 1 + ROSTER_BOOST * rt['weight']
    elif rt['show_weight']:
        m = 1 + ROSTER_SHOW_BOOST * rt['show_weight']
    elif rt['family'] == 'beauty' and rt['known_off']:
        m = OFF_ROSTER
    if rt['family'] == 'beauty' and villain >= 0.45:
        m *= VILLAIN_MULT
    return m, (0.5 + 0.5 * m) if m < 1 else min(1.1, m)
