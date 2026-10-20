/**
 * Hard guards before a quiz short can be uploaded (every format). 2026-10-19: an emoji "festival edition"
 * with 0 rounds (the LLM was down, no festival emoji items existed) rendered as a 2.8 s intro + outro and
 * was published. Every check here returns a list of problems; an empty list means "ok to publish".
 *
 *   checkPlan(plan, need)            before rendering: round count, a real answer per round, edition membership,
 *                                    local picture files (exist, non-empty, not blank)
 *   checkRender(res, need)           after rendering: rounds kept, video length, sampled frames not blank / static
 */
const fs = require('fs');
const path = require('path');
const { execFile } = require('child_process');
const learn = require('../core/learning-models');
const { themePool, loadThemes } = require('./pop-quiz-content');

const ROOT = path.join(__dirname, '..');
const QA = path.join(ROOT, 'core', 'quiz', 'qa_guard.py');
const PAIR_FORMATS = ['wyr', 'duel'];
const ART_FORMATS = ['character', 'silhouette', 'eyes', 'outfit'];
const LLM_FORMATS = ['wyr', 'trivia', 'emoji'];
const MIN_ROUNDS = 3;            // the renderer's own floor (clip formats keep >= 3 of their candidates)
const MIN_ROUND_SEC = 3.5;       // shortest question + reveal in core/quiz/render_pop.py (Q_MIN >= 3 s, plus the reveal)
const OUTRO_SEC = 2.8;
const MAX_SEC = 180;

/** Rounds a short must have: 3 for the quick cut, else the format's default (4 for pairs, 5 otherwise). */
function requiredRounds(format, length, lengths = { quick: 3, standard: null }) {
  return lengths[length] || (PAIR_FORMATS.includes(format) ? 4 : 5);
}

/** The answer text of a round, as the description / history record it. */
function roundLabel(r) {
  if (!r) return '';
  if (typeof r.answer === 'string') return r.answer;
  if (r.question && Array.isArray(r.options)) return r.options[r.answer] || '';
  if (r.a) return `${r.a} / ${r.b}`;
  if (r.left && r.right) return `${r.left.name || ''} / ${r.right.name || ''}`;
  return r.name || r.title || '';
}

const real = s => typeof s === 'string' && s.trim().length > 0 && !/^(undefined|null|nan)$/i.test(s.trim()) && !/undefined/.test(s);

/** Local picture files a round uses (remote URLs are fetched by the renderer and checked through the rendered video). */
function localFiles(r) {
  const own = x => ['image_file', 'mask_file', 'crop_file', 'photo_file'].map(k => x && x[k]).filter(v => typeof v === 'string' && v && !/^https?:/.test(v));
  return [...own(r), ...(r.a && typeof r.a === 'object' ? own(r.a) : []), ...(r.b && typeof r.b === 'object' ? own(r.b) : [])];
}

function runQA(args) {
  const py = learn.pythonCommand();
  if (!py) return Promise.resolve(null);
  return new Promise(resolve => {
    execFile(py, [QA, ...args], { timeout: 5 * 60000, maxBuffer: 4 * 1024 * 1024 }, (err, stdout) => {
      try { resolve(JSON.parse(String(stdout).trim().split('\n').pop())); } catch { resolve(null); }
    });
  });
}

/** Edition membership: every round must be one of the edition's items. */
function editionProblems(plan) {
  const ed = plan.edition && (plan.edition.id || plan.edition);
  if (!ed) return [];
  const out = [];
  const fmt = plan.format;
  if (ART_FORMATS.includes(fmt)) {
    const ids = new Set(themePool(ed, fmt).map(i => String(i.id)));
    plan.rounds.forEach((r, i) => { if (!ids.has(String(r.id))) out.push(`round ${i + 1} (${roundLabel(r)}) is not a ${ed} ${fmt} item`); });
  } else if (LLM_FORMATS.includes(fmt)) {
    plan.rounds.forEach((r, i) => { if (r.edition !== ed) out.push(`round ${i + 1} (${roundLabel(r)}) was not written for the ${ed} edition`); });
  } else if (fmt === 'duel') {
    let th = null;
    try { th = loadThemes().themes[ed]; } catch {}
    if (!th) out.push(`unknown edition ${ed}`);
    else if (!th.duel_base_art) {   // themed art only: both characters come from the theme
      const ids = new Set(themePool(ed, 'character').map(i => String(i.id)));
      plan.rounds.forEach((r, i) => {
        if (!r.a || !r.b || !ids.has(String(r.a.id)) || !ids.has(String(r.b.id))) out.push(`duel ${i + 1} is not a ${ed} pair`);
      });
    }
  } else {
    out.push(`format ${fmt} has no ${ed} edition`);
  }
  return out;
}

/** Before rendering. */
async function checkPlan(plan, need) {
  const out = [];
  const rounds = (plan && plan.rounds) || [];
  if (rounds.length < need) out.push(`plan has ${rounds.length} rounds, needs ${need}`);
  rounds.forEach((r, i) => { if (!real(roundLabel(r))) out.push(`round ${i + 1} has no answer`); });
  out.push(...editionProblems(plan || {}));
  const files = rounds.flatMap(localFiles);
  if (files.length) {
    const qa = await runQA(['--images', files.join(',')]);
    if (!qa || !qa.images) out.push('picture check could not run');
    else for (const im of qa.images.filter(x => !x.ok)) {
      out.push(`picture ${path.basename(im.path)} ${!im.exists ? 'missing' : !im.size ? 'empty' : `blank (std ${im.std})`}`);
    }
  }
  return out;
}

/** After rendering: the renderer's result ({path, duration, plan}). */
async function checkRender(res, need) {
  const out = [];
  const rounds = (res && res.plan && res.plan.rounds) || [];
  const floor = Math.min(need, MIN_ROUNDS);
  if (rounds.length < floor) out.push(`video has ${rounds.length} rounds, needs at least ${floor}`);
  rounds.forEach((r, i) => { if (!real(roundLabel(r))) out.push(`rendered round ${i + 1} has no answer`); });
  if (!res || !res.path || !fs.existsSync(res.path) || !fs.statSync(res.path).size) {
    out.push('video file missing or empty');
    return out;
  }
  const qa = await runQA(['--video', res.path]);
  const v = qa && qa.video;
  if (!v || !v.duration) { out.push('video check could not run'); return out; }
  const minSec = OUTRO_SEC + MIN_ROUND_SEC * Math.max(floor, rounds.length);
  if (v.duration < minSec) out.push(`video is ${v.duration}s, expected >= ${minSec.toFixed(1)}s for ${rounds.length} rounds`);
  if (v.duration > MAX_SEC) out.push(`video is ${v.duration}s (> ${MAX_SEC}s)`);
  if (!v.sampled) out.push('no frames could be read');
  else {
    // generous limits: a quiz holds one card per round, so neighbouring samples can be near-identical; only a video
    // that is mostly flat colour, or never changes at all, fails
    if (v.blankShare > 0.5) out.push(`${Math.round(v.blankShare * 100)} % of sampled frames are blank`);
    if (v.staticShare > 0.9) out.push(`${Math.round(v.staticShare * 100)} % of sampled frames are unchanged (static video)`);
  }
  return out;
}

module.exports = { requiredRounds, roundLabel, checkPlan, checkRender, editionProblems };
