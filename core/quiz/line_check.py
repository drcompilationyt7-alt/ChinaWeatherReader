"""Topic / format consistency of the narrator lines and on-screen titles (rules: line-rules.json, shared with
pipeline/quiz-line-check.js). 2026-10-23: a K-pop song emoji quiz said "Guess the anime from these emojis!".

  problems(plan, text, kind='narration', name=None, others=(), n=None) -> list of reasons ([] = fine)
      kind 'narration': intro / outro / reveal template / pool line: word categories, other editions, counts
      kind 'screen':    on-screen title / subtitle: word categories, counts (n/n)
      kind 'content':   trivia question, wyr option / reason, duel question: placeholders and counts only
      name / others:    a reveal must contain its own answer (name) and no other round's answer (others)
"""
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
RULES = json.load(open(os.path.join(HERE, 'line-rules.json'), encoding='utf-8'))
CATS = {k: re.compile(v, re.I) for k, v in RULES['categories'].items()}
EDS = {k: re.compile(v, re.I) for k, v in RULES['editions'].items()}
NUM = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10}
COUNT = re.compile(r'\b(\d+|' + '|'.join(NUM) + r')\s+(?:\w+\s+)?(?:' + RULES['count_nouns'] + r')\b', re.I)
ALL_N = re.compile(r'\ball\s+(\d+|' + '|'.join(NUM) + r')\b', re.I)
SLASH = re.compile(r'\b(\d+)\s*/\s*(\d+)\b')


def edition_id(plan):
    ed = plan.get('edition')
    return (ed.get('id') if isinstance(ed, dict) else ed) or None


def allowed(plan):
    """Word categories a line of this quiz may use."""
    fmt, topic = plan.get('format'), plan.get('topic')
    t = RULES['topic'].get(topic)
    if t == '*':
        return set(CATS)
    return set(RULES['format'].get(fmt, [])) | set(t or []) | set(RULES['format_topic'].get(f'{fmt}|{topic}', []))


def _num(s):
    s = s.lower()
    return int(s) if s.isdigit() else NUM.get(s)


def problems(plan, text, kind='narration', name=None, others=(), n=None):
    s = str(text or '').strip()
    if not s:
        return []
    out = []
    if re.search(r'[{}]|\bundefined\b|\bNaN\b', s):
        out.append('placeholder left in the line')
    n = n if n is not None else len(plan.get('rounds') or [])
    body = s
    if name:
        if name.lower() not in s.lower():
            out.append(f'reveal does not say its answer "{name}"')
        body = re.sub(re.escape(name), ' ', body, flags=re.I)
    for o in others or ():
        if o and (not name or o.lower() not in name.lower()) and re.search(r'\b' + re.escape(o) + r'\b', body, re.I):
            out.append(f'names another round\'s answer "{o}"')
    if kind != 'content':
        ok = allowed(plan)
        for k, rx in CATS.items():
            if k not in ok and rx.search(body):
                out.append(f'wrong {k} word "{rx.search(body).group(0)}" for {plan.get("format")} / {plan.get("topic")}')
    if kind == 'narration':
        ed = edition_id(plan)
        for k, rx in EDS.items():
            if k != ed and rx.search(body):
                out.append(f'names the {k} edition ({"plain video" if not ed else ed + " edition"})')
    for m in COUNT.finditer(body):
        v = _num(m.group(1))
        if v is not None and v != n and v > 1 and not (plan.get('format') == 'duel' and v == 2):   # duel: two characters per round
            out.append(f'says "{m.group(0)}" but the video has {n} rounds')
    for m in ALL_N.finditer(body):
        v = _num(m.group(1))
        if v is not None and v != n:
            out.append(f'says "{m.group(0)}" but the video has {n} rounds')
    for m in SLASH.finditer(body):
        if int(m.group(1)) == int(m.group(2)) and int(m.group(1)) != n:
            out.append(f'says "{m.group(0)}" but the video has {n} rounds')
    return out


def tidy(s):
    """'Toradora!. Did you get it?' -> 'Toradora! Did you get it?' (a name ending in ! or . before the template's own mark)."""
    return re.sub(r'([!?.])[.!](?!\.)', r'\1', str(s)) if s else s


def dry_run(path, out_path):
    """CLI: python line_check.py --dry-run plans.json out.json (scripts/quiz-line-audit.js). Every plan goes through the renderer's
    line picking (no media, no TTS, no render): the lines it would speak, the title card, the pool / AI lines the check dropped,
    and any problem left in the final lines (must be none). Pop plans: render_pop.PopRenderer; geography: render_quiz."""
    import contextlib
    import io
    import random
    import render_pop
    import render_quiz
    plans = json.load(open(path, encoding='utf-8'))
    res = []
    for item in plans:
        plan = item['plan']
        if plan.get('want'):
            plan['rounds'] = plan['rounds'][:plan['want']]   # the renderer keeps `want` of the candidates
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            if plan['format'] in render_pop.POP_FORMATS:
                r = render_pop.PopRenderer(plan, voice=False, music=False)
                r._order_rounds()
                intro, asks, anss, outro = r.voice_lines()
                lines = [intro] + asks + anss + [outro]
                meta = r.spoken_meta
                screen = r.screen_text()
            else:
                r = render_quiz.QuizRenderer(plan, voice=False, music=False)
                lines = r.check_lines(r.voice_lines())
                meta = [{k: v for k, v in x.items() if k != 'line'} for x in r.spoken]
                lines = [x['line'] for x in r.spoken]
                screen = list(render_quiz.TITLE.get(plan['format'], ()))
        left = []
        spoken = []
        for k, m in enumerate(meta):
            if k >= len(lines) or not lines[k]:
                continue
            spoken.append({**m, 'line': lines[k]})
            for p in problems(plan, lines[k], m['kind'], m.get('name'), m.get('others') or ()):
                left.append(f"{m['slot']} {lines[k]!r}: {p}")
        for t in screen:
            for p in problems(plan, t, 'screen'):
                left.append(f'screen {t!r}: {p}')
        res.append({'id': item['id'], 'spoken': spoken, 'screen': screen, 'swaps': getattr(r, 'line_swaps', None) or [],
                    'left': left, 'log': [x for x in err.getvalue().splitlines() if x.startswith('line check')]})
    json.dump(res, open(out_path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(json.dumps({'plans': len(res), 'left': sum(len(x['left']) for x in res), 'swaps': sum(len(x['swaps']) for x in res)}))


if __name__ == '__main__':
    import sys
    if len(sys.argv) == 4 and sys.argv[1] == '--dry-run':
        dry_run(sys.argv[2], sys.argv[3])
    else:
        print(problems({'format': 'emoji', 'topic': 'kpop song', 'rounds': [1, 2, 3]}, 'Guess the anime from these emojis!'))
