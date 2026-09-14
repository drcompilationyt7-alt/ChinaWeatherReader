#!/usr/bin/env python3
"""
Title similarity / k-NN performance prediction.

Reads JSON on stdin:
    {"history": [{"title": "...", "score": 12.3}, ...], "candidates": ["...", ...]}
Writes one JSON line on stdout:
    {"backend": "minilm" | "charngram", "predictions": [{"title", "predicted", "neighbors": [...]}, ...]}

For every candidate title the predicted score is the similarity-weighted mean
of the scores of its k most similar past titles. Embeddings come from the free
sentence-transformers model `all-MiniLM-L6-v2` (CPU, ~90 MB, cached under
~/.cache/huggingface); if that is not installed a character n-gram cosine
similarity is used instead so the pipeline never breaks.
"""
import json
import math
import sys

K = 5
TEMPERATURE = 8.0   # sharper weighting of the closest neighbours


def read_input():
    raw = sys.stdin.read()
    data = json.loads(raw) if raw.strip() else {}
    history = [h for h in data.get('history', []) if h.get('title')]
    candidates = [c for c in data.get('candidates', []) if isinstance(c, str) and c.strip()]
    return history, candidates


def embed_minilm(texts):
    from sentence_transformers import SentenceTransformer  # noqa: WPS433 (optional dependency)
    model = SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2', device='cpu')
    vecs = model.encode(texts, normalize_embeddings=True, batch_size=64, show_progress_bar=False)
    return [list(map(float, v)) for v in vecs]


def _ngrams(text, n=3):
    t = ' ' + ' '.join(text.lower().split()) + ' '
    return [t[i:i + n] for i in range(max(1, len(t) - n + 1))]


def embed_charngram(texts):
    # sparse tf vectors as dicts; cosine computed later
    vecs = []
    for t in texts:
        d = {}
        for g in _ngrams(t):
            d[g] = d.get(g, 0) + 1
        norm = math.sqrt(sum(v * v for v in d.values())) or 1.0
        vecs.append({k: v / norm for k, v in d.items()})
    return vecs


def cosine(a, b):
    if isinstance(a, dict):
        if len(a) > len(b):
            a, b = b, a
        return sum(v * b.get(k, 0.0) for k, v in a.items())
    return sum(x * y for x, y in zip(a, b))


def main():
    history, candidates = read_input()
    if not candidates:
        print(json.dumps({'backend': 'none', 'predictions': []}))
        return
    if not history:
        print(json.dumps({'backend': 'none', 'predictions': [{'title': c, 'predicted': None, 'neighbors': []} for c in candidates]}))
        return

    texts = [h['title'] for h in history] + candidates
    backend = 'minilm'
    try:
        vecs = embed_minilm(texts)
    except Exception as e:  # model not installed / offline
        sys.stderr.write(f'minilm unavailable ({e.__class__.__name__}), using char n-grams\n')
        backend = 'charngram'
        vecs = embed_charngram(texts)

    hist_vecs = vecs[:len(history)]
    cand_vecs = vecs[len(history):]
    predictions = []
    for ci, cand in enumerate(candidates):
        sims = [(cosine(cand_vecs[ci], hv), hi) for hi, hv in enumerate(hist_vecs)]
        sims.sort(reverse=True)
        top = sims[:K]
        weights = [math.exp(TEMPERATURE * s) for s, _ in top]
        wsum = sum(weights) or 1.0
        predicted = sum(w * float(history[hi].get('score', 0.0)) for w, (_, hi) in zip(weights, top)) / wsum
        predictions.append({
            'title': cand,
            'predicted': round(predicted, 2),
            'neighbors': [{'title': history[hi]['title'], 'sim': round(s, 3), 'score': history[hi].get('score', 0)} for s, hi in top[:3]],
        })
    print(json.dumps({'backend': backend, 'predictions': predictions}, ensure_ascii=False))


if __name__ == '__main__':
    main()
