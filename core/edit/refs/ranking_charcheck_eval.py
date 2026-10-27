#!/usr/bin/env python3
"""Character-check evaluation on the pool-survey candidates (2026-10-25): the old single-AniList-picture check (v1)
vs the multi-image reference bank (v2).

Per downloaded, watermark-free survey candidate (Desktop/edit-refs/ranking/survey/deep-<topic>.json): frames at 2 fps
(448 px) of the middle 84 %, cut into consecutive 20 s chunks. Each chunk is checked
  * for its OWN entry  -> recall: a candidate "passes" when any chunk passes;
  * for every OTHER entry of the topic -> false passes (precision first: must stay 0; a flagged one is looked at by eye,
    because a chunk may really show that other character, e.g. Gojo inside a Yuta edit).
Pass rule = evaluate()'s: votes >= 4 (6 with the tagger), share >= CHAR_MIN, wins >= 2x any other character.

    python core/edit/refs/ranking_charcheck_eval.py --topics jjk_strongest,best_waifu,romance_peak --out eval.json
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import ranking_compilation as RC  # noqa: E402

SURVEY = os.path.join(os.path.expanduser('~'), 'Desktop', 'edit-refs', 'ranking', 'survey')


def check(judge, frames, entry, others):
    tgt, oth = RC.targets_and_distractors(judge, entry, others)
    mt = judge.match_tags(frames, entry.get('tags'))
    conf, det = mt if mt is not None else judge.match_open(frames, tgt, oth, [t for o in others for t in o.get('tags', [])],
                                                                            entry.get('companions', ()))
    top_other = max(det['others'].values()) if det['others'] else 0
    need = 4 if os.environ.get('RANKING_CHARCHECK') == 'v1' and det.get('method') != 'wd_tagger' else RC.MIN_VOTES.get(det.get('method'), 6)
    ok = det['votes'] >= need and conf >= RC.CHAR_MIN and det['wins'] >= 2 * top_other
    return ok, round(conf, 2), det


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--topics', default='jjk_strongest,best_waifu,romance_peak')
    ap.add_argument('--out', required=True)
    ap.add_argument('--work', default=os.path.join(RC.ROOT, 'output', 'ranking-proto'))
    ap.add_argument('--mode', default='both', choices=('v1', 'v2', 'both'))
    ap.add_argument('--max-chunks', type=int, default=3)
    a = ap.parse_args()
    RC.idle()
    judge = RC.Judge(os.path.join(a.work, 'cache'))
    # memo per frame object (the same frames are checked for several entries / both versions)
    tg = judge.tagger()
    if tg is not None:
        _call, memo = tg.__call__, {}
        type(tg).__call__ = lambda self, img: (memo.get(id(img)) or memo.setdefault(id(img), (img, _call(img))))[1]
    _fc, fmemo = RC.L.face_crops, {}
    RC.L.face_crops = lambda img, det, k: (fmemo.get((id(img), k)) or fmemo.setdefault((id(img), k), (img, _fc(img, det, k))))[1]
    _si, smemo = judge.sig.images, {}

    def images(imgs):
        import numpy as np
        miss = [im for im in imgs if id(im) not in smemo]
        if miss:
            for im, e in zip(miss, _si(miss)):
                smemo[id(im)] = (im, e)                 # the reference keeps the object (and its id) alive
        return np.stack([smemo[id(im)][1] for im in imgs]) if imgs else _si(imgs)
    judge.sig.images = images
    res = json.load(open(a.out, encoding='utf-8')) if os.path.exists(a.out) else {}
    for topic in a.topics.split(','):
        d = json.load(open(os.path.join(SURVEY, f'deep-{topic}.json'), encoding='utf-8'))
        # the survey's entries, found in today's library by name (the waifu list was re-ordered since)
        lib = [e for k in [topic] + sorted(RC.TOPICS) for e in RC.TOPICS[k]['entries']]
        ents = {r: next(e for e in lib if e['name'] == row['name']) for r, row in d['ranks'].items()}
        T = {'entries': list(ents.values())}
        for rank, row in d['ranks'].items():
            entry = ents[rank]
            others = [o for o in T['entries'] if o is not entry]
            for c in row['all']:
                key = f'{topic}/{rank}/{c["id"]}'
                if key in res and res[key].get('done'):
                    continue
                if not c.get('path') or not os.path.exists(c['path']):
                    continue
                ana = RC.decode(c['path'])
                memo_reset = [m.clear() for m in (fmemo, smemo)] + ([memo.clear()] if tg is not None else [])  # noqa: F841
                ts = sorted(ana['frames'])
                dur = ts[-1] if ts else 0
                chunks = []
                t0 = 0.08 * dur
                while t0 + 10 <= 0.92 * dur and len(chunks) < a.max_chunks:
                    chunks.append((t0, min(t0 + 20, 0.92 * dur)))
                    t0 += 20
                out = {'entry': entry['name'], 'handle': c.get('handle'), 'survey_verdict': c['verdict'], 'why': c.get('why'),
                       'chunks': []}
                for s, e in chunks:
                    fr = [ana['frames'][t] for t in ts if s <= t < e]
                    ch = {'s': round(s, 1), 'e': round(e, 1)}
                    for ver in (['v1', 'v2'] if a.mode == 'both' else [a.mode]):
                        os.environ['RANKING_CHARCHECK'] = ver
                        ok, conf, det = check(judge, fr, entry, others)
                        ch[ver] = {'own': ok, 'conf': conf, 'votes': det['votes'], 'method': det.get('method'),
                                   'others': det['others']}
                        fp = []
                        for o in others:
                            oo = [x for x in T['entries'] if x is not o]
                            ok2, conf2, det2 = check(judge, fr, o, oo)
                            if ok2:
                                fp.append({'entry': o['name'], 'conf': conf2, 'votes': det2['votes']})
                        ch[ver]['false_pass'] = fp
                    out['chunks'].append(ch)
                    print(key, ch, flush=True)
                out['done'] = True
                res[key] = out
                json.dump(res, open(a.out, 'w', encoding='utf-8'), indent=1, ensure_ascii=False, default=str)
    summ = {}
    for ver in ('v1', 'v2'):
        cand = [r for r in res.values() if r['chunks'] and ver in r['chunks'][0]]
        summ[ver] = {'candidates': len(cand),
                     'own_pass': sum(any(ch[ver]['own'] for ch in r['chunks']) for r in cand),
                     'chunks': sum(len(r['chunks']) for r in cand),
                     'own_chunk_pass': sum(ch[ver]['own'] for r in cand for ch in r['chunks']),
                     'false_pass_chunks': [(k, ch['s'], ch[ver]['false_pass']) for k, r in res.items()
                                           for ch in r['chunks'] if ver in ch and ch[ver]['false_pass']]}
    print(json.dumps(summ, indent=1, ensure_ascii=False, default=str))


if __name__ == '__main__':
    main()
