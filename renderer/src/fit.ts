import {measureText} from '@remotion/layout-utils';
import {isDevanagari, lineHeightOf, SANS, SERIF, THEME} from './theme';
import type {Word} from './types.gen';

const T = THEME.type;
export const WORD_GAP_EM = 0.24;
export const BOX_PAD_EM = 0.16; // highlight box padding on each side

export type Measurable =
	| {kind: 'words'; words: Word[]; size: number; weight: number; tracking: string}
	| {kind: 'row'; items: {words: Word[]; size: number; padX: number; padY: number}[]} // cards side by side
	| {kind: 'fixed'; height: number};

const wordWidth = (w: Word, size: number, weight: number, tracking: string): number => {
	const serif = w.style === 'serif';
	const fs = serif ? size * T.serifScale : size;
	const m = measureText({
		text: w.text,
		fontFamily: serif ? SERIF : SANS,
		fontSize: fs,
		fontWeight: serif ? 900 : w.style === 'bold' || w.style === 'highlight' ? T.boldWeight : weight,
		letterSpacing: isDevanagari(w.text) ? '0' : tracking,
		additionalStyles: serif ? {fontStyle: 'italic'} : {},
	});
	return m.width + (w.style === 'highlight' ? 2 * BOX_PAD_EM * fs : 0);
};

/** Greedy wrap: how many lines and how wide the widest word is. */
export const wrap = (words: Word[], size: number, weight: number, tracking: string, maxWidth: number) => {
	let lines = 1;
	let x = 0;
	let widest = 0;
	let tall = size;
	for (const w of words) {
		const ww = wordWidth(w, size, weight, tracking);
		widest = Math.max(widest, ww);
		if (w.style === 'serif') tall = size * T.serifScale;
		const next = x === 0 ? ww : x + WORD_GAP_EM * size + ww;
		if (next > maxWidth && x > 0) {
			lines++;
			x = ww;
		} else x = next;
	}
	return {lines, widest, height: lines * tall * lineHeightOf(words), width: lines === 1 ? x : maxWidth};
};

const heightAt = (m: Measurable, k: number, maxWidth: number): number | null => {
	if (m.kind === 'fixed') return m.height * k;
	if (m.kind === 'words') {
		const r = wrap(m.words, m.size * k, m.weight, m.tracking, maxWidth);
		return r.widest > maxWidth ? null : r.height;
	}
	let rows = 1;
	let x = 0;
	let rowH = 0;
	let total = 0;
	for (const it of m.items) {
		const inner = maxWidth - 2 * it.padX * k;
		const r = wrap(it.words, it.size * k, T.card.weight, T.card.tracking, inner);
		if (r.widest > inner) return null;
		const cw = r.width + 2 * it.padX * k;
		const ch = r.height + 2 * it.padY * k;
		if (x > 0 && x + cw > maxWidth) {
			rows++;
			total += rowH;
			x = 0;
			rowH = 0;
		}
		x += cw + it.padX * k;
		rowH = Math.max(rowH, ch);
	}
	return total + rowH + (rows - 1) * it0gap(m);
};
const it0gap = (m: Measurable) => (m.kind === 'row' && m.items[0] ? m.items[0].padY : 0);

/** The largest scale (≤ 1) at which every block fits the box: no word wider than the box, total height inside it. */
export const fitScale = (blocks: Measurable[], width: number, height: number, gap: number): number => {
	for (let k = 1; k > 0.3; k -= 0.04) {
		let total = gap * k * Math.max(0, blocks.length - 1);
		let ok = true;
		for (const b of blocks) {
			const h = heightAt(b, k, width);
			if (h === null) {
				ok = false;
				break;
			}
			total += h;
		}
		if (ok && total <= height) return k;
	}
	return 0.3;
};
