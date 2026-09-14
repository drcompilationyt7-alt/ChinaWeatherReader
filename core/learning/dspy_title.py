#!/usr/bin/env python3
"""
DSPy title writer — a prompt that optimizes itself against our performance.

The program (instructions + few-shot demos) is compiled by DSPy against a
metric derived from the channel's own stats: a generated title scores well
when it is similar (MiniLM embeddings) to titles that performed well for us.
The compiled program is saved to memory/dspy-title-program.json and reused
at generation time; it is re-optimized about once a week when enough new
scored videos exist (see --optimize / --status).

LM: Gemini through LiteLLM using the same free GEMINI_API_KEY(_2..8) secrets
the pipeline already has (keys are tried in turn on rate limits), or a local
Ollama model (OLLAMA_MODEL) as a fallback.

Modes (JSON on stdin, one JSON line on stdout):
  --status
  --generate  {"context": {"country","category","summary","source_title","transcript"}, "insights": "..."}
              -> {"available": true, "titles": ["...", "...", "..."], "compiled": true}
  --optimize  {"trainset": [{"context": {...}, "title": "...", "score": 1.2}, ...],
               "history": [{"title": "...", "score": 1.2}, ...], "maxExamples": 24}
              -> {"ok": true, "examples": 24, "savedTo": "...", "metricMean": 0.63}
"""
import argparse
import datetime as dt
import importlib.util
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(HERE, '..', '..', 'memory')
PROGRAM_PATH = os.path.join(MEM, 'dspy-title-program.json')
STATUS_PATH = os.path.join(MEM, 'dspy-title-status.json')
SIM_PATH = os.path.join(HERE, '..', 'title-similarity.py')


def out(obj):
    print(json.dumps(obj, ensure_ascii=False))


def load_similarity():
    spec = importlib.util.spec_from_file_location('title_similarity', SIM_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def gemini_keys():
    keys = [os.environ.get('GEMINI_API_KEY')] + [os.environ.get(f'GEMINI_API_KEY_{i}') for i in range(2, 9)]
    return [k for k in keys if k]


def make_lm(dspy, key=None):
    model = os.environ.get('GEMINI_MODEL', 'gemini-2.5-flash')
    if key:
        return dspy.LM(f'gemini/{model}', api_key=key, max_tokens=600, temperature=0.8, cache=False, num_retries=1)
    ollama_model = os.environ.get('OLLAMA_MODEL', 'gemma4:latest')
    return dspy.LM(f'ollama_chat/{ollama_model}', api_base=os.environ.get('OLLAMA_HOST', 'http://127.0.0.1:11434'),
                   max_tokens=600, temperature=0.8, cache=False, num_retries=1)


def lm_candidates(dspy):
    """LMs to try in order: each Gemini key, then Ollama."""
    lms = [make_lm(dspy, k) for k in gemini_keys()]
    lms.append(make_lm(dspy, None))
    return lms


def build_program(dspy):
    class ShortsTitle(dspy.Signature):
        """Write a clickable YouTube Shorts title for an Asian edits channel (k-pop, anime, dance, Asian daily life).
        Under 50 characters, specific to what happens in the clip, curiosity gap, 1-2 fitting emojis, no clickbait lies,
        no generic '<Country> clip' titles. Give the best title plus two alternatives with different hooks."""
        country = dspy.InputField()
        category = dspy.InputField()
        summary = dspy.InputField(desc='what happens in the clip')
        source_title = dspy.InputField(desc='the original title (context only, write a new one)')
        insights = dspy.InputField(desc='what has worked on this channel so far')
        title = dspy.OutputField(desc='best title')
        alt_title_1 = dspy.OutputField(desc='alternative with a different hook')
        alt_title_2 = dspy.OutputField(desc='another alternative')

    class TitleWriter(dspy.Module):
        def __init__(self):
            super().__init__()
            self.write = dspy.Predict(ShortsTitle)

        def forward(self, country, category, summary, source_title, insights):
            return self.write(country=country or 'unknown', category=category or 'other', summary=summary or '',
                              source_title=source_title or '', insights=insights or '')

    return TitleWriter()


def load_program(dspy):
    prog = build_program(dspy)
    compiled = False
    if os.path.exists(PROGRAM_PATH):
        try:
            prog.load(PROGRAM_PATH)
            compiled = True
        except Exception as e:
            sys.stderr.write(f'could not load compiled program: {e}\n')
    return prog, compiled


def clean_title(t):
    t = str(t or '').strip().strip('"').strip()
    return t[:100]


# ─── proxy metric: similarity-weighted performance of past titles ───────────

class ProxyMetric:
    def __init__(self, history):
        self.sim = load_similarity()
        self.history = [h for h in history if h.get('title')]
        texts = [h['title'] for h in self.history]
        self.backend = 'minilm'
        try:
            self.vecs = self.sim.embed_minilm(texts) if texts else []
            self._embed = self.sim.embed_minilm
        except Exception:
            self.backend = 'charngram'
            self.vecs = self.sim.embed_charngram(texts) if texts else []
            self._embed = self.sim.embed_charngram
        scores = sorted(float(h.get('score', 0)) for h in self.history)
        self.lo = scores[0] if scores else 0.0
        self.hi = scores[-1] if scores else 1.0
        self.threshold = scores[int(len(scores) * 0.6)] if scores else 0.0

    def predicted(self, title):
        if not self.vecs:
            return 0.0
        v = self._embed([title])[0]
        sims = sorted(((self.sim.cosine(v, hv), i) for i, hv in enumerate(self.vecs)), reverse=True)[:5]
        w = [math.exp(8.0 * s) for s, _ in sims]
        return sum(wi * float(self.history[i].get('score', 0)) for wi, (_, i) in zip(w, sims)) / (sum(w) or 1.0)

    def normalized(self, title):
        p = self.predicted(title)
        return (p - self.lo) / (self.hi - self.lo) if self.hi > self.lo else 0.5

    def __call__(self, example, pred, trace=None):
        t = clean_title(getattr(pred, 'title', ''))
        if len(t) < 6 or len(t) > 60:
            return 0.0 if trace is None else False
        score = self.normalized(t)
        if trace is not None:  # bootstrapping: accept only demos that look like our winners
            return self.predicted(t) >= self.threshold
        return score


def run_with_lms(dspy, fn):
    """Try each LM in turn (rate limits, missing keys); returns fn() result or raises the last error."""
    last = None
    for lm in lm_candidates(dspy):
        try:
            dspy.configure(lm=lm)
            return fn()
        except Exception as e:
            last = e
            sys.stderr.write(f'LM failed ({lm.model}): {str(e)[:120]}\n')
    raise last if last else RuntimeError('no LM available')


def cmd_status():
    try:
        import dspy  # noqa: F401
        available = True
    except Exception as e:
        out({'available': False, 'reason': f'dspy missing: {e.__class__.__name__}'})
        return
    status = {}
    try:
        status = json.load(open(STATUS_PATH, encoding='utf-8'))
    except Exception:
        pass
    out({'available': available, 'compiled': os.path.exists(PROGRAM_PATH), 'status': status,
         'keys': len(gemini_keys())})


def cmd_generate(data):
    try:
        import dspy
    except Exception as e:
        out({'available': False, 'reason': f'dspy missing: {e.__class__.__name__}', 'titles': []})
        return
    ctx = data.get('context') or {}
    prog, compiled = load_program(dspy)

    def go():
        return prog(country=ctx.get('country'), category=ctx.get('category'), summary=ctx.get('summary'),
                    source_title=ctx.get('source_title') or ctx.get('sourceTitle'), insights=data.get('insights') or '')
    try:
        pred = run_with_lms(dspy, go)
    except Exception as e:
        out({'available': True, 'titles': [], 'compiled': compiled, 'error': str(e)[:160]})
        return
    titles = []
    for t in (getattr(pred, 'title', ''), getattr(pred, 'alt_title_1', ''), getattr(pred, 'alt_title_2', '')):
        t = clean_title(t)
        if len(t) >= 6 and t not in titles:
            titles.append(t)
    out({'available': True, 'titles': titles, 'compiled': compiled})


def cmd_optimize(data):
    try:
        import dspy
    except Exception as e:
        out({'ok': False, 'reason': f'dspy missing: {e.__class__.__name__}'})
        return
    train = [t for t in data.get('trainset', []) if t.get('title') and isinstance(t.get('context'), dict)]
    history = data.get('history') or [{'title': t['title'], 'score': t.get('score', 0)} for t in train]
    max_examples = int(data.get('maxExamples', 24))
    if len(train) < 8:
        out({'ok': False, 'reason': f'only {len(train)} training examples (need 8)'})
        return
    # labeled demos = our better performers; keep the set small to protect the free quota
    train.sort(key=lambda t: float(t.get('score', 0)), reverse=True)
    train = train[:max_examples]
    examples = []
    for t in train:
        c = t['context']
        examples.append(dspy.Example(country=c.get('country') or 'unknown', category=c.get('category') or 'other',
                                     summary=c.get('summary') or '', source_title=c.get('source_title') or c.get('sourceTitle') or '',
                                     insights=data.get('insights') or '', title=t['title'],
                                     alt_title_1=t.get('alt1') or t['title'], alt_title_2=t.get('alt2') or t['title'])
                        .with_inputs('country', 'category', 'summary', 'source_title', 'insights'))
    metric = ProxyMetric(history)
    prog = build_program(dspy)

    def go():
        opt = dspy.BootstrapFewShot(metric=metric, max_bootstrapped_demos=4, max_labeled_demos=6, max_rounds=1)
        return opt.compile(prog, trainset=examples)
    try:
        compiled = run_with_lms(dspy, go)
    except Exception as e:
        out({'ok': False, 'reason': str(e)[:200]})
        return
    os.makedirs(MEM, exist_ok=True)
    compiled.save(PROGRAM_PATH)
    # quick self-evaluation on a handful of examples (no extra bootstrap)
    scores = []
    try:
        for ex in examples[:6]:
            pred = compiled(country=ex.country, category=ex.category, summary=ex.summary, source_title=ex.source_title, insights=ex.insights)
            scores.append(metric(ex, pred))
    except Exception:
        pass
    status = {'optimizedAt': dt.datetime.now(dt.timezone.utc).isoformat(), 'examples': len(examples),
              'metricBackend': metric.backend, 'metricMean': round(sum(scores) / len(scores), 3) if scores else None,
              'historySize': len(history)}
    with open(STATUS_PATH, 'w', encoding='utf-8') as fh:
        json.dump(status, fh, indent=2)
    out({'ok': True, 'savedTo': PROGRAM_PATH, **status})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--status', action='store_true')
    ap.add_argument('--generate', action='store_true')
    ap.add_argument('--optimize', action='store_true')
    args = ap.parse_args()
    if args.status:
        cmd_status()
        return
    raw = sys.stdin.read()
    data = json.loads(raw) if raw.strip() else {}
    if args.generate:
        cmd_generate(data)
    elif args.optimize:
        cmd_optimize(data)
    else:
        cmd_status()


if __name__ == '__main__':
    main()
