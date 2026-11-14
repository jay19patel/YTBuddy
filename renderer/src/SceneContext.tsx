import {createContext, useContext} from 'react';
import {useVideoConfig} from 'remotion';
import type {Brand} from './types.gen';
import {THEME, type Palette, type Rect} from './theme';

export type SceneInfo = {
	startFrame: number;
	endFrame: number; // scene-relative
	bg: string; // hex of the main background
	textBg: string; // hex behind the text area (the panel on split scenes)
	ink: string;
	muted: string;
	accent: string;
	palette: Palette;
	brand: Brand;
	text: Rect;
	visual: Rect;
	align: 'left' | 'center';
};

export const SceneCtx = createContext<SceneInfo | null>(null);

export const useScene = (): SceneInfo => {
	const s = useContext(SceneCtx);
	if (!s) throw new Error('useScene() outside a scene');
	return s;
};

/** Seconds on the video timeline → frame inside the current scene (the same rounding as pipeline.schema.frame_of). */
export const useRel = () => {
	const {fps} = useVideoConfig();
	const {startFrame, endFrame} = useScene();
	const rel = (sec: number) => Math.round(sec * fps) - startFrame;
	const relEnd = (sec: number | null) => (sec === null ? null : Math.min(rel(sec), endFrame));
	return {rel, relEnd};
};

/** When a list item appears: its own `at`, else staggered after the element starts. */
export const useItemAt = (at: number) => {
	const {rel} = useRel();
	const {fps} = useVideoConfig();
	const stagger = Math.max(1, Math.round((THEME.motion.stagger * fps) / 30));
	return (it: {at: number | null}, i: number) => (it.at === null ? at + i * stagger : rel(it.at));
};

/** Base font size for mock UI in the visual area: scales with the area so 9:16 and 16:9 both fit. */
export const useVisualFont = (mult = 1) => {
	const {visual} = useScene();
	return Math.min(visual.w, visual.h * 1.5) * 0.032 * mult;
};
