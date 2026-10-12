export type Word = {text: string; from: number; to: number};
export type Sfx = {file: string; peak: number};

export type Scene = {
  type: 'hook' | 'photo' | 'counter' | 'bars' | 'vs' | 'stack' | 'quote' | 'list' | 'timeline' | 'outro';
  text: string;
  from: number;
  durationInFrames: number;
  voice: string;
  voiceFrom: number;
  imageFile?: string;
  videoFile?: string;
  videoMask?: boolean;
  imageAspect?: number;
  fit?: 'cover' | 'card' | 'auto';
  focus?: [number, number];
  kicker?: string;
  label?: string;
  source?: string;
  cueFrame?: number | null;
  cueEndFrame?: number | null;
  [k: string]: any;
};

export type ShortProps = {
  slug: string;
  fps: number;
  durationInFrames: number;
  scenes: Scene[];
  words: Word[];
  sfx: Record<string, Sfx>;
  music: {file: string; gain: number; track: string};
  credits: string[];
};
