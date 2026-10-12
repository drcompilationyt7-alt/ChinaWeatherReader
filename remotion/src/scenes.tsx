import React from 'react';
import {AbsoluteFill, Img, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig, Easing} from 'remotion';
import {C, HEAD, UI, W, VIS_TOP, VIS_BOT, OVERLAP} from './theme';
import {Scene} from './types';
import {Photo, InkBg, Kicker, Label, Source, useSpringAt, clamp, fmt, Check} from './ui';

/** scene-relative frame of an absolute frame (scenes start OVERLAP frames before their narration) */
export const rel = (sc: Scene, abs: number | null | undefined, fallback: number) =>
  abs == null ? fallback : abs - (sc.from - OVERLAP);

/** cue frames for a list of items: the word cue when it was found, else spread over the line */
export const itemCues = (sc: Scene, items: any[], start = OVERLAP + 6) => {
  const span = Math.max(12, (sc.durationInFrames - 30) / Math.max(1, items.length));
  return items.map((it, i) => rel(sc, it.cueFrame, start + i * span));
};

const Headline: React.FC<{lines: string[]; highlight: number; size: number; y: number; at: number}> = ({lines, highlight, size, y, at}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  return (
    <div style={{position: 'absolute', top: y, width: W, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6}}>
      {lines.map((ln, i) => {
        const s = spring({frame: frame - at - i * 5, fps, config: {damping: 11, mass: 0.6, stiffness: 160}});
        const box = spring({frame: frame - at - i * 5 - 2, fps, config: {damping: 20, stiffness: 120}});
        const hl = i === highlight;
        return (
          <div key={i} style={{position: 'relative', padding: '0 26px', transform: `scale(${interpolate(s, [0, 1], [1.8, 1])})`, opacity: Math.min(1, s * 2)}}>
            {hl && <div style={{position: 'absolute', inset: '8px 0 4px 0', background: C.red, borderRadius: 14,
              transform: `scaleX(${box})`, transformOrigin: 'left', boxShadow: '0 12px 40px rgba(226,36,36,.45)'}} />}
            <div style={{position: 'relative', fontFamily: HEAD, fontSize: size, lineHeight: 1.04, color: hl ? C.gold : C.white,
              textTransform: 'uppercase', whiteSpace: 'nowrap', textShadow: hl ? 'none' : '0 6px 30px rgba(0,0,0,.6)',
              WebkitTextStroke: hl ? undefined : '3px rgba(0,0,0,.35)'}}>{ln}</div>
          </div>
        );
      })}
    </div>
  );
};

const fitSize = (lines: string[], base: number) => {
  const longest = Math.max(...lines.map((l) => l.length));
  return Math.min(base, Math.floor((W - 120) / (longest * 0.5)));   // Anton is ~0.5 em per caps glyph
};

export const Hook: React.FC<{sc: Scene}> = ({sc}) => {
  const lines: string[] = sc.headline ?? [];
  const size = fitSize(lines, sc.size ?? 190);
  return (
    <AbsoluteFill>
      {(sc.imageFile || sc.videoFile) ? <Photo sc={{...sc, fit: 'cover'}} dim={0.25} /> : <InkBg seed={1} />}
      <Headline lines={lines} highlight={sc.highlight ?? lines.length - 1} size={size} y={sc.y ?? 520} at={2} />
      {sc.sub && <Sub text={sc.sub} y={(sc.y ?? 520) + lines.length * size * 1.08 + 30} at={16} />}
      <Source text={sc.source} />
    </AbsoluteFill>
  );
};

const Sub: React.FC<{text: string; y: number; at: number}> = ({text, y, at}) => {
  const s = useSpringAt(at);
  return (
    <div style={{position: 'absolute', top: y, width: W, textAlign: 'center', fontFamily: UI, fontWeight: 900, fontSize: 50,
      letterSpacing: 6, color: C.white, opacity: s, transform: `translateY(${(1 - s) * 20}px)`}}>{text}</div>
  );
};

export const PhotoScene: React.FC<{sc: Scene}> = ({sc}) => {
  const aspect = sc.imageAspect ?? 1.5;
  const fit = sc.videoFile ? 'cover' : sc.fit && sc.fit !== 'auto' ? sc.fit : aspect > 0.9 ? 'card' : 'cover';
  const ch = Math.min(860, (W - 80) / aspect);
  const labelTop = fit === 'cover' ? VIS_BOT - 110 : (VIS_TOP + VIS_BOT) / 2 + ch / 2 - 46;
  return (
    <AbsoluteFill>
      <Photo sc={sc} />
      <Kicker text={sc.kicker} />
      <Label text={sc.label} top={labelTop} at={rel(sc, sc.cueFrame, 16)} />
      <Source text={sc.source} />
    </AbsoluteFill>
  );
};

export const Counter: React.FC<{sc: Scene}> = ({sc}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const a = rel(sc, sc.cueFrame, OVERLAP + 4);
  const b = Math.max(a + 18, rel(sc, sc.cueEndFrame, a + Math.min(40, sc.durationInFrames * 0.45)));
  const p = interpolate(frame, [a, b], [0, 1], {...clamp, easing: Easing.out(Easing.cubic)});
  const v = (sc.countFrom ?? 0) + (sc.to - (sc.countFrom ?? 0)) * p;
  const land = spring({frame: frame - b, fps, config: {damping: 9, stiffness: 200, mass: 0.5}});
  const pulse = frame >= b ? 1 + 0.07 * Math.sin(Math.min(1, land) * Math.PI) : 1;
  const enter = useSpringAt(Math.max(2, a - 8));
  const unitS = useSpringAt(a + 6);
  const labelS = useSpringAt(a + 12);
  const text = (sc.prefix ?? '') + fmt(v, sc.decimals ?? 0) + (sc.suffix ?? '');
  const size = Math.min(sc.size ?? 300, Math.floor((W - 100) / (text.length * 0.52)));
  return (
    <AbsoluteFill>
      {(sc.imageFile || sc.videoFile) ? <Photo sc={sc} backdropOnly dim={0.15} /> : <InkBg seed={2} />}
      <Kicker text={sc.kicker} />
      <div style={{position: 'absolute', top: 430, width: W, display: 'flex', flexDirection: 'column', alignItems: 'center',
        opacity: enter, transform: `scale(${interpolate(enter, [0, 1], [0.85, 1])})`}}>
        <div style={{fontFamily: HEAD, fontSize: size, lineHeight: 1, color: C.gold, transform: `scale(${pulse})`,
          fontVariantNumeric: 'tabular-nums', textShadow: '0 0 60px rgba(247,184,52,.35), 0 10px 0 #3A060C'}}>{text}</div>
        {sc.unit && <div style={{fontFamily: HEAD, fontSize: 110, color: C.white, marginTop: 6, letterSpacing: 2,
          opacity: unitS, transform: `translateY(${(1 - unitS) * 30}px)`}}>{sc.unit}</div>}
        {sc.label && <div style={{fontFamily: UI, fontWeight: 800, fontSize: 46, color: 'rgba(245,235,225,.92)', marginTop: 18,
          maxWidth: W - 160, textAlign: 'center', opacity: labelS}}>{sc.label}</div>}
      </div>
      <Source text={sc.source} />
    </AbsoluteFill>
  );
};

export const Bars: React.FC<{sc: Scene}> = ({sc}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const items: any[] = sc.items;
  const cues = itemCues(sc, items, OVERLAP + 4);
  const mx = Math.max(...items.map((i) => i.value));
  const top = 410;
  const row = Math.min(230, (VIS_BOT - top - 20) / items.length);
  return (
    <AbsoluteFill>
      {(sc.imageFile || sc.videoFile) ? <Photo sc={sc} backdropOnly dim={0.2} /> : <InkBg seed={3} />}
      <Kicker text={sc.kicker} />
      {items.map((it, k) => {
        const s = spring({frame: frame - cues[k], fps, config: {damping: 18, stiffness: 90, mass: 0.8}});
        const appear = spring({frame: frame - cues[k] + 3, fps, config: {damping: 20, stiffness: 160}});
        const bw = (W - 160 - 250) * (it.value / mx) * s;
        const hl = !!it.highlight;
        return (
          <div key={k} style={{position: 'absolute', top: top + k * row, left: 80, opacity: appear, transform: `translateX(${(1 - appear) * -40}px)`}}>
            <div style={{fontFamily: UI, fontWeight: 800, fontSize: 44, color: hl ? C.white : 'rgba(230,220,215,.85)', marginBottom: 12}}>{it.label}</div>
            <div style={{display: 'flex', alignItems: 'center', gap: 20}}>
              <div style={{height: 92, width: Math.max(14, bw), borderRadius: 16,
                background: hl ? `linear-gradient(90deg, ${C.gold}, ${C.goldLight})` : 'linear-gradient(90deg, #6E6262, #9A8E8E)',
                boxShadow: hl ? '0 0 40px rgba(247,184,52,.35)' : 'none'}} />
              <div style={{fontFamily: HEAD, fontSize: 84, color: hl ? C.gold : C.white, fontVariantNumeric: 'tabular-nums'}}>
                {(sc.prefix ?? '') + fmt(it.value * s, sc.decimals ?? 0)}{sc.unit ?? ''}
              </div>
            </div>
          </div>
        );
      })}
      <Source text={sc.source} />
    </AbsoluteFill>
  );
};

export const VS: React.FC<{sc: Scene}> = ({sc}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const ca = rel(sc, sc.before?.cueFrame, OVERLAP + 2);
  const cb = rel(sc, sc.after?.cueFrame, ca + 26);
  const card = (c: any, k: number, at: number) => {
    const s = spring({frame: frame - at, fps, config: {damping: 12, stiffness: 150, mass: 0.7}});
    const hl = k === 1;
    return (
      <div key={k} style={{position: 'absolute', top: 440 + k * 380, left: 90, width: W - 180, height: 290, borderRadius: 38,
        background: hl ? `linear-gradient(135deg, ${C.red}, ${C.redDeep})` : 'rgba(45,36,38,.92)',
        border: hl ? `3px solid ${C.gold}` : '2px solid rgba(255,255,255,.12)', boxShadow: '0 30px 70px rgba(0,0,0,.55)',
        display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
        transform: `scale(${interpolate(s, [0, 1], [0.6, 1])}) translateY(${(1 - s) * 60}px)`, opacity: Math.min(1, s * 1.5)}}>
        <div style={{fontFamily: UI, fontWeight: 900, fontSize: 42, letterSpacing: 5, color: hl ? C.goldLight : 'rgba(225,215,210,.85)'}}>{c.label}</div>
        <div style={{fontFamily: HEAD, fontSize: 150, lineHeight: 1.05, color: C.white}}>{c.value}</div>
      </div>
    );
  };
  const arrow = spring({frame: frame - cb + 4, fps, config: {damping: 14}});
  return (
    <AbsoluteFill>
      {(sc.imageFile || sc.videoFile) ? <Photo sc={sc} backdropOnly dim={0.25} /> : <InkBg seed={4} />}
      <Kicker text={sc.kicker} />
      {card(sc.before, 0, ca)}
      <div style={{position: 'absolute', top: 742, left: W / 2 - 40, width: 0, height: 0, borderLeft: '40px solid transparent',
        borderRight: '40px solid transparent', borderTop: `48px solid ${C.gold}`, opacity: arrow, transform: `translateY(${(1 - arrow) * -20}px)`}} />
      {card(sc.after, 1, cb)}
      <Source text={sc.source} />
    </AbsoluteFill>
  );
};

export const Stack: React.FC<{sc: Scene}> = ({sc}) => {
  const frame = useCurrentFrame();
  const floors = sc.floors ?? 57;
  const days = sc.days ?? 19;
  const a = OVERLAP + 4;
  const b = sc.durationInFrames - 6;
  const t = interpolate(frame, [a, b], [0, 1], {...clamp, easing: Easing.inOut(Easing.quad)});
  const n = Math.floor(floors * t + 1e-6);
  const frac = floors * t - n;
  const base = 1080;
  const fh = 12.5;
  const tw = 300;
  const x0 = W / 2 + 60 - tw / 2;
  const topY = base - floors * fh - 110;
  return (
    <AbsoluteFill>
      <InkBg seed={5} />
      <Kicker text={sc.kicker} />
      <svg width={W} height={1920} style={{position: 'absolute'}}>
        <defs>
          <linearGradient id="fl" x1="0" x2="1"><stop offset="0" stopColor="#C9C3BE" /><stop offset="1" stopColor="#8E8884" /></linearGradient>
        </defs>
        <rect x={60} y={base} width={W - 120} height={8} fill="#5A3A3A" />
        {Array.from({length: n}).map((_, i) => (
          <g key={i}>
            <rect x={x0} y={base - (i + 1) * fh} width={tw} height={fh - 2} fill="url(#fl)" />
            <rect x={x0 + 10} y={base - (i + 1) * fh + 3} width={tw - 20} height={fh - 8} fill="#2B3F5C" opacity={0.75} />
          </g>
        ))}
        {n < floors && (
          <g>
            <line x1={x0 + tw / 2} y1={topY} x2={x0 + tw / 2} y2={base - (n + 1) * fh - (1 - frac) * 150} stroke="#ddd" strokeWidth={3} />
            <rect x={x0} y={base - (n + 1) * fh - (1 - frac) * 150} width={tw} height={fh - 2} fill={C.gold} />
          </g>
        )}
        <line x1={x0 + tw + 70} y1={base} x2={x0 + tw + 70} y2={topY} stroke={C.gold} strokeWidth={10} />
        <line x1={x0 - 130} y1={topY} x2={x0 + tw + 150} y2={topY} stroke={C.gold} strokeWidth={10} />
      </svg>
      {[['FLOORS', n, C.gold, 420], ['DAYS', t > 0 ? Math.max(1, Math.ceil(days * t)) : 0, C.white, 640]].map(([lab, v, col, y]: any) => (
        <div key={lab} style={{position: 'absolute', left: 70, top: y, width: 220, textAlign: 'center'}}>
          <div style={{fontFamily: HEAD, fontSize: 130, lineHeight: 1, color: col, fontVariantNumeric: 'tabular-nums'}}>{v}</div>
          <div style={{fontFamily: UI, fontWeight: 900, fontSize: 36, letterSpacing: 4, color: C.white}}>{lab}</div>
        </div>
      ))}
    </AbsoluteFill>
  );
};

export const Quote: React.FC<{sc: Scene}> = ({sc}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const at = rel(sc, sc.cueFrame, OVERLAP + 6);
  const words: string[] = String(sc.quote).split(' ');
  const who = useSpringAt(at + words.length * 4 + 8);
  const mark = useSpringAt(2);
  return (
    <AbsoluteFill>
      {(sc.imageFile || sc.videoFile) ? <Photo sc={sc} backdropOnly dim={0.3} /> : <InkBg seed={6} />}
      <Kicker text={sc.kicker} />
      <div style={{position: 'absolute', top: 380, width: W, textAlign: 'center', fontFamily: 'Georgia, serif', fontWeight: 700,
        fontSize: 300, lineHeight: 1, color: C.red, opacity: mark, transform: `scale(${mark})`}}>{'“'}</div>
      <div style={{position: 'absolute', top: 620, left: 90, width: W - 180, textAlign: 'center', fontFamily: 'Georgia, serif',
        fontStyle: 'italic', fontWeight: 700, fontSize: 104, lineHeight: 1.15, color: C.white}}>
        {words.map((w, i) => {
          const s = spring({frame: frame - at - i * 4, fps, config: {damping: 16, stiffness: 140}});
          return <span key={i} style={{display: 'inline-block', marginRight: 26, opacity: s, transform: `translateY(${(1 - s) * 30}px)`}}>{w}</span>;
        })}
      </div>
      <div style={{position: 'absolute', top: 900, width: W, textAlign: 'center', fontFamily: UI, fontWeight: 800, fontSize: 46,
        color: C.gold, opacity: who, transform: `translateY(${(1 - who) * 20}px)`}}>{'— ' + (sc.who ?? '')}</div>
      <Source text={sc.source} />
    </AbsoluteFill>
  );
};

export const List: React.FC<{sc: Scene}> = ({sc}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const items: any[] = sc.items;
  const cues = itemCues(sc, items);
  const gap = Math.min(170, (VIS_BOT - 420) / items.length);
  return (
    <AbsoluteFill>
      {(sc.imageFile || sc.videoFile) ? <Photo sc={sc} backdropOnly dim={0.3} /> : <InkBg seed={7} />}
      <Kicker text={sc.kicker} />
      {items.map((it, k) => {
        const s = spring({frame: frame - cues[k], fps, config: {damping: 15, stiffness: 150, mass: 0.7}});
        const tick = interpolate(frame, [cues[k] + 4, cues[k] + 14], [0, 1], clamp);
        const cross = it.icon === 'cross';
        return (
          <div key={k} style={{position: 'absolute', top: 410 + k * gap, left: 90, width: W - 180, height: gap - 28, borderRadius: 26,
            background: 'rgba(24,10,14,.85)', border: `2px solid ${cross ? 'rgba(226,36,36,.8)' : C.gold}`,
            display: 'flex', alignItems: 'center', gap: 26, padding: '0 30px', boxShadow: '0 18px 40px rgba(0,0,0,.4)',
            opacity: s, transform: `translateX(${(1 - s) * -120}px)`}}>
            <Check size={64} color={cross ? C.red : C.gold} cross={cross} progress={tick} />
            <div style={{fontFamily: UI, fontWeight: 800, fontSize: 52, color: C.white}}>{it.text}</div>
          </div>
        );
      })}
      <Source text={sc.source} />
    </AbsoluteFill>
  );
};

export const Timeline: React.FC<{sc: Scene}> = ({sc}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const ev: any[] = sc.events;
  const cues = itemCues(sc, ev);
  const top = 420;
  const bot = VIS_BOT - 60;
  const step = (bot - top) / Math.max(1, ev.length - 1);
  const lineTo = interpolate(frame, [cues[0], cues[ev.length - 1] + 6], [top, bot], clamp);
  return (
    <AbsoluteFill>
      <InkBg seed={8} />
      <Kicker text={sc.kicker} />
      <div style={{position: 'absolute', left: 196, top, width: 8, height: lineTo - top, background: C.gold, borderRadius: 4}} />
      {ev.map((e, k) => {
        const s = spring({frame: frame - cues[k], fps, config: {damping: 12, stiffness: 170, mass: 0.6}});
        const y = top + k * step;
        return (
          <div key={k}>
            <div style={{position: 'absolute', left: 200 - 24, top: y - 24, width: 48, height: 48, borderRadius: 24, background: C.red,
              border: `6px solid ${C.gold}`, transform: `scale(${s})`}} />
            <div style={{position: 'absolute', left: 250, top: y - 70, opacity: s, transform: `translateX(${(1 - s) * 50}px)`}}>
              <div style={{fontFamily: HEAD, fontSize: 96, lineHeight: 1, color: C.gold}}>{e.year}</div>
              <div style={{fontFamily: UI, fontWeight: 800, fontSize: 50, color: C.white, maxWidth: W - 340, lineHeight: 1.15}}>{e.text}</div>
            </div>
          </div>
        );
      })}
    </AbsoluteFill>
  );
};

export const Outro: React.FC<{sc: Scene}> = ({sc}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const logo = useSpringAt(2, {damping: 10, stiffness: 120});
  const q = useSpringAt(10);
  const btn = useSpringAt(sc.durationInFrames - 62);
  const pressAt = sc.durationInFrames - 34;
  const press = frame >= pressAt ? 1 - 0.09 * Math.sin(Math.min(1, (frame - pressAt) / 8) * Math.PI) : 1;
  const subbed = frame >= pressAt + 3;
  const cursor = interpolate(frame, [sc.durationInFrames - 58, pressAt], [0, 1], {...clamp, easing: Easing.out(Easing.cubic)});
  const words = String(sc.question ?? '').split(' ');
  return (
    <AbsoluteFill>
      {(sc.imageFile || sc.videoFile) ? <Photo sc={sc} backdropOnly dim={0.35} /> : <InkBg seed={9} />}
      <Img src={staticFile('brand/avatar.png')} style={{position: 'absolute', left: W / 2 - 150, top: 330, width: 300, height: 300, borderRadius: 150,
        transform: `scale(${logo}) rotate(${(1 - logo) * -25}deg)`, boxShadow: '0 20px 60px rgba(226,36,36,.45)'}} />
      <div style={{position: 'absolute', top: 690, left: 70, width: W - 140, textAlign: 'center', fontFamily: HEAD, fontSize: 104, lineHeight: 1.08,
        color: C.white, opacity: q, transform: `translateY(${(1 - q) * 30}px)`}}>
        {words.map((w, i) => {
          const s = spring({frame: frame - 10 - i * 3, fps, config: {damping: 14}});
          return <span key={i} style={{display: 'inline-block', marginRight: 22, opacity: s}}>{w}</span>;
        })}
      </div>
      <div style={{position: 'absolute', top: 1000, left: W / 2 - 280, width: 560, height: 124, borderRadius: 62,
        background: subbed ? '#3D3637' : C.red, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 18,
        fontFamily: UI, fontWeight: 900, fontSize: 52, letterSpacing: 2, color: C.white, boxShadow: '0 18px 50px rgba(0,0,0,.45)',
        opacity: btn, transform: `scale(${btn * press})`}}>
        {subbed ? '✓ SUBSCRIBED' : 'SUBSCRIBE'}
      </div>
      <svg width={90} height={90} viewBox="0 0 24 24" style={{position: 'absolute', opacity: btn * (cursor > 0 ? 1 : 0),
        left: interpolate(cursor, [0, 1], [W - 160, W / 2 + 120]), top: interpolate(cursor, [0, 1], [1320, 1050]), transform: `scale(${press})`}}>
        <path d="M5 2 L19 13 L12 14 L15.5 21 L12.5 22.3 L9 15.3 L5 19 Z" fill="#fff" stroke="#000" strokeWidth={1.2} />
      </svg>
    </AbsoluteFill>
  );
};

export const SCENES: Record<string, React.FC<{sc: Scene}>> = {
  hook: Hook, photo: PhotoScene, counter: Counter, bars: Bars, vs: VS, stack: Stack, quote: Quote, list: List, timeline: Timeline, outro: Outro,
};
