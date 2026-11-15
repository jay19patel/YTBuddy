import {useVideoConfig} from 'remotion';
import {THEME} from './theme.gen';
import type {Timeline} from './types.gen';

export {THEME};
export type Element = Timeline['scenes'][number]['elements'][number];
export type Palette = Record<string, string>;
export type Rect = {x: number; y: number; w: number; h: number};

export const isDevanagari = (text: string): boolean => /[\u0900-\u097F]/.test(text);

/** Line height for a block of words: Hindi needs more room than the tight Latin setting. */
export const lineHeightOf = (words: {text: string}[]): number =>
	words.some((w) => isDevanagari(w.text)) ? THEME.type.lineHeightDevanagari : THEME.type.lineHeight;

export const colorOf = (c: string | null | undefined, palette: Palette, fallback = '#FFFFFF'): string =>
	!c ? fallback : c.startsWith('#') ? c : (palette[c] ?? fallback);

export const luminance = (hex: string): number => {
	const n = parseInt(hex.slice(1), 16);
	const [r, g, b] = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((v) => v / 255);
	return 0.2126 * r + 0.7152 * g + 0.0722 * b;
};

/** Text colour on a background: near-black on light paper, white on everything else. */
export const inkOn = (hex: string): string => (luminance(hex) > 0.55 ? THEME.palette.ink : THEME.palette.white);
export const mutedOn = (hex: string): string => (luminance(hex) > 0.55 ? THEME.palette.gray : 'rgba(255,255,255,0.72)');

/** Sizes in the theme are fractions of `u` (the short side); durations are frames at 30 fps, rescaled by `d`. */
export const useUnits = () => {
	const {width, height, fps} = useVideoConfig();
	const u = Math.min(width, height);
	const vertical = height > width;
	const tu = u * (vertical ? THEME.type.verticalBoost : 1); // the unit for text sizes
	return {width, height, fps, u, tu, vertical, d: (frames: number) => Math.max(1, Math.round((frames * fps) / 30))};
};

// Devanagari letters come from Noto Sans Devanagari (Inter Tight and Playfair have none), Latin ones from the main font
export const SANS = `'${THEME.fonts.sans}', '${THEME.fonts.devanagari}', sans-serif`;
export const SERIF = `'${THEME.fonts.serif}', '${THEME.fonts.devanagari}', serif`;
export const DISPLAY = `'${THEME.fonts.display}', sans-serif`;
