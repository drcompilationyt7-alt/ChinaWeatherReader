#!/usr/bin/env python3
"""
CI repair loop helper (2026-10-23, owner: "if the github when creating the slow mo edit is not good we have to fix it, we must
publish a video every day"). Reads a judge report of the RENDERED file and writes what the next attempt must change:

    python core/edit/judge_repair.py --type slowmo --report work/judge/report.json --out repair.json
    python core/edit/judge_repair.py --type sound  --report work/judge_sound/report.json --out repair.json [--min 9.0]

-> {"pass": bool, "overall": x, "failed": {criterion: score}, "action": "none|retime|recut|reseed|new_song",
    "moves": [[cut, to]], "avoid": [cut], "need": [t], "lyric_t0": {entry: t}, "why": [...]}   (edit seconds; no words)

slow-mo:
  3_lyrics          per-word rows of the judge (lyric_words: which word, its sung onset on the rendered audio) -> lyric_t0
                    (entry -> onset - LW_LEAD); a dropped word / no per-word data -> reseed
  11 / 13 / 14 / 12 mid-wave and held-tail switches -> avoid; missed strong wave starts -> need; loose groove switches and
                    off-sync line starts -> moves onto the attack (- 0.03 s, the refs' tight lead)
  1 / 2 / 7         smoothness / beauty / variety: another clip order (reseed)
  4 / 6 / 9 / 5     mix, length, no-SFX, layout: the song / section (new_song)
sound: below the bar -> reseed first, then new_song (the sound judge has no per-cut repair).
The pipeline decides the order: retime / recut keep the song + seed (only the flagged parts change, CINE_PART_CACHE
reuses the unchanged video parts), then reseed, then a new song.
"""
import argparse
import json
import os
import sys

LEAD = 0.03
CUT_KEYS = ('11_waves', '12_sync', '13_hold_outro', '14_groove', '8_cuts', '10_holds')
CLIP_KEYS = ('1_smooth', '2_beauty', '7_variety')
SONG_KEYS = ('4_mix', '5_layout', '6_length', '9_no_sfx')


def slowmo(rep, bar=8.0):
    sc = rep.get('scores') or {}
    c = rep.get('cand') or {}
    failed = {k: v for k, v in sc.items() if v is not None and float(v) < bar}
    out = dict(pass_=bool(rep.get('pass')), overall=rep.get('overall'), failed=failed, moves=[], avoid=[], need=[],
               lyric_t0={}, why=[])
    lw = c.get('lyric_words') or {}
    for r in lw.get('rows') or []:
        if not r.get('ok'):
            out['lyric_t0'][str(r['entry'])] = round(float(r['onset']) - LEAD, 3)
    if lw.get('rows'):
        out['why'].append(f"lyrics: {len(out['lyric_t0'])} of {len(lw['rows'])} words off")
    w = c.get('waves') or {}
    out['avoid'] += [float(x) for x in w.get('bad') or []]
    out['need'] += [float(m['t']) - 0.05 for m in w.get('missed') or [] if isinstance(m, dict) and m.get('t') is not None]
    for x in (c.get('tail_outro') or {}).get('bad') or []:
        out['avoid'].append(float(x[0] if isinstance(x, (list, tuple)) else x))
    for r in (c.get('groove') or {}).get('rows') or []:
        if r.get('kind') == 'loose' and r.get('attack_after') is not None and abs(float(r['attack_after'])) <= 0.4:
            out['moves'].append([float(r['cut']), round(float(r['cut']) + float(r['attack_after']) - LEAD, 3)])
    sy = c.get('sync') or {}
    for r in sy.get('rows') or []:
        if r.get('cut') is not None and r.get('err') is not None and abs(float(r['err'])) > 0.15 and r.get('start') is not None:
            out['moves'].append([float(r['cut']), round(float(r['start']) - LEAD, 3)])
    for r in ((sy.get('lines') or {}).get('rows') or []):
        if r.get('cut') is not None and r.get('err') is not None and abs(float(r['err'])) > 0.15 and r.get('attack') is not None:
            out['moves'].append([float(r['cut']), round(float(r['attack']) - LEAD, 3)])
    lyr_fixable = '3_lyrics' in failed and bool(out['lyric_t0']) and not (lw.get('dropped'))
    cut_fixable = any(k in failed for k in CUT_KEYS) and bool(out['moves'] or out['avoid'] or out['need'])
    if not failed and out['pass_']:
        action = 'none'
    elif any(k in failed for k in SONG_KEYS):
        action = 'new_song'
    elif any(k in failed for k in CLIP_KEYS) or ('3_lyrics' in failed and not lyr_fixable) or \
            (any(k in failed for k in CUT_KEYS) and not cut_fixable):
        action = 'reseed'
    elif cut_fixable:
        action = 'recut'
    elif lyr_fixable:
        action = 'retime'
    else:
        action = 'reseed'                    # the overall bar only: another clip order
    if action in ('recut',) and lyr_fixable:
        out['why'].append('recut + lyric re-time')
    out['action'] = action
    out['pass'] = out.pop('pass_')
    return out


def sound(rep, min_overall=9.0):
    card = rep.get('scorecard') or {}
    sc = card.get('scores') or {}
    vals = [float(v) for k, v in sc.items() if v is not None and not str(k).startswith('9_overall')]
    mean = round(sum(vals) / len(vals), 2) if vals else 0.0
    failed = {k: v for k, v in sc.items() if v is not None and float(v) < 8.0 and not str(k).startswith('12_')}
    ok = bool(card.get('PASS')) and mean >= min_overall
    return dict(**{'pass': ok}, overall=mean, failed=failed, action='none' if ok else 'reseed', moves=[], avoid=[], need=[],
                lyric_t0={}, why=[f'mean {mean} (bar {min_overall}), strict {bool(card.get("PASS"))}'])


def warm_models():
    """load (download into the CI cache) every model the slow-mo / sound renders and judges use; exit 1 on a failure"""
    import time
    t0 = time.time()
    import torchaudio.pipelines as tp
    tp.WAV2VEC2_ASR_BASE_960H.get_model()
    tp.WAV2VEC2_ASR_LARGE_LV60K_960H.get_model()
    from demucs.pretrained import get_model
    get_model('htdemucs')
    from faster_whisper import WhisperModel
    for m in ('base', 'medium'):
        WhisperModel(m, device='cpu', compute_type='int8')
    print(f'models warm in {time.time() - t0:.0f} s')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--type', choices=['slowmo', 'sound'])
    ap.add_argument('--report')
    ap.add_argument('--out')
    ap.add_argument('--min', type=float, default=float(os.environ.get('SOUND_JUDGE_MIN', '9.0')))
    ap.add_argument('--warm-models', action='store_true', help='pre-flight: load / download every judge + lyric model')
    a = ap.parse_args()
    if a.warm_models:
        try:
            warm_models()
        except Exception as e:  # noqa: BLE001
            print(f'model warm-up failed: {type(e).__name__}: {e}')
            sys.exit(1)
        sys.exit(0)
    if not (a.type and a.report and a.out):
        ap.error('--type, --report and --out are required')
    try:
        with open(a.report, encoding='utf-8') as f:
            rep = json.load(f)
    except (OSError, ValueError) as e:
        res = {'pass': False, 'overall': None, 'failed': {}, 'action': 'reseed', 'why': [f'no judge report: {e}']}
    else:
        res = slowmo(rep) if a.type == 'slowmo' else sound(rep, a.min)
    with open(a.out, 'w', encoding='utf-8') as f:
        json.dump(res, f, indent=1)
    print(json.dumps(res))
    sys.exit(0)


if __name__ == '__main__':
    main()
