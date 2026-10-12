import {loadFont as loadAnton} from '@remotion/google-fonts/Anton';
import {loadFont as loadInter} from '@remotion/google-fonts/Inter';
import {loadFont as loadMontserrat} from '@remotion/google-fonts/Montserrat';

export const HEAD = loadAnton('normal', {weights: ['400'], subsets: ['latin']}).fontFamily;
export const UI = loadInter('normal', {weights: ['600', '800', '900'], subsets: ['latin']}).fontFamily;
export const CAP = loadMontserrat('normal', {weights: ['800', '900'], subsets: ['latin']}).fontFamily;

export const C = {
  red: '#E22424',
  redDeep: '#960E16',
  redInk: '#3A060C',
  gold: '#F7B834',
  goldLight: '#FFDC78',
  ink: '#0E0A0C',
  white: '#FAF7F0',
  grey: '#8F8585',
};

export const W = 1080;
export const H = 1920;
// safe areas for Shorts: the bottom ~380 px hold the title / buttons, the right ~140 px the action rail
export const VIS_TOP = 230;
export const VIS_BOT = 1110;
export const CAP_Y = 1270;
export const OVERLAP = 8; // frames each scene starts before its narration (the entrance animation)
