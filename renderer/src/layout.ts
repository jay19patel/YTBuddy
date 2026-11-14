import {THEME, type Rect} from './theme';
import type {Scene} from './types.gen';

export type Regions = {text: Rect; visual: Rect; panel: Rect | null; align: 'left' | 'center'};

/** Where text and visuals go for each layout. 9:16 is not a crop of 16:9: side-by-side areas become top/bottom. */
export const regions = (layout: Scene['layout'], width: number, height: number): Regions => {
	const vertical = height > width;
	const mx = width * THEME.layout.safe;
	const my = height * THEME.layout.safe;
	const safe: Rect = {x: mx, y: my, w: width - 2 * mx, h: height - 2 * my};
	const cols = (split: number, gap = 0.04): [Rect, Rect] => [
		{x: mx, y: my, w: width * split - mx, h: safe.h},
		{x: width * (split + gap), y: my, w: width * (1 - split - gap) - mx, h: safe.h},
	];
	const rows = (split: number, gap = 0.02): [Rect, Rect] => [
		{x: mx, y: my, w: safe.w, h: height * split - my},
		{x: mx, y: height * (split + gap), w: safe.w, h: height * (1 - split - gap) - my},
	];
	switch (layout) {
		case 'center_bracket':
		case 'outro':
			return {text: {...safe, x: width * (vertical ? 0.2 : 0.12), w: width * (vertical ? 0.6 : 0.76)}, visual: safe, panel: null, align: 'center'};
		case 'text_plus_window': {
			const [text, visual] = vertical ? rows(0.38) : cols(0.42);
			return {text, visual, panel: null, align: 'left'};
		}
		case 'text_plus_character': {
			const [text, visual] = vertical ? rows(0.55) : cols(0.58, 0);
			return {text, visual, panel: null, align: 'left'};
		}
		case 'split_panel': {
			const panel: Rect = vertical ? {x: 0, y: 0, w: width, h: height * 0.4} : {x: 0, y: 0, w: width * 0.42, h: height};
			const text: Rect = vertical
				? {x: mx, y: my, w: safe.w, h: panel.h - 2 * my}
				: {x: mx, y: my, w: panel.w - 2 * mx, h: safe.h};
			const visual: Rect = vertical
				? {x: mx, y: panel.h + my, w: safe.w, h: height - panel.h - 2 * my}
				: {x: panel.w + mx, y: my, w: width - panel.w - 2 * mx, h: safe.h};
			return {text, visual, panel, align: 'left'};
		}
		default: {
			const [text, visual] = vertical ? rows(0.6) : cols(0.62, 0);
			return {text, visual, panel: null, align: 'left'};
		}
	}
};
