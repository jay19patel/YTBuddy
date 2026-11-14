import {useMemo} from 'react';
import {AbsoluteFill, useCurrentFrame, useVideoConfig} from 'remotion';
import {ApproveReject} from './components/ApproveReject';
import {AvatarBadge} from './components/AvatarBadge';
import {Brackets} from './components/Brackets';
import {Character} from './components/Character';
import {CARD_PAD, ColorCard} from './components/ColorCard';
import {CommentBubbles} from './components/CommentBubbles';
import {DecoColumn} from './components/DecoColumn';
import {DmCard} from './components/DmCard';
import {DraftCards} from './components/DraftCards';
import {FollowCard} from './components/FollowCard';
import {LIST_ROW_EM, ListReveal} from './components/ListReveal';
import {MacWindow} from './components/MacWindow';
import {OutlineBgWord} from './components/OutlineBgWord';
import {Outro} from './components/Outro';
import {Sticker} from './components/Sticker';
import {StoryboardRow} from './components/StoryboardRow';
import {Swatches} from './components/Swatches';
import {TagPill} from './components/TagPill';
import {TextBlock, Words} from './components/TextBlock';
import {TimelineCut} from './components/TimelineCut';
import {TypingBox} from './components/TypingBox';
import {Watermark} from './components/Watermark';
import {fitScale, type Measurable} from './fit';
import {regions} from './layout';
import {cardSpring, exitFade} from './motion';
import {SceneCtx, type SceneInfo, useRel, useScene} from './SceneContext';
import {colorOf, type Element, inkOn, luminance, mutedOn, SANS, THEME, useUnits} from './theme';
import type {Brand, Scene, TextLine} from './types.gen';

const FLOW = new Set<Element['type']>(['text_line', 'card', 'tag_pill', 'list_reveal', 'swatches']);
const isCaption = (e: Element) => e.type === 'text_line' && e.props.role === 'caption';

export const SceneView: React.FC<{scene: Scene; brand: Brand; startFrame: number}> = ({scene, brand, startFrame}) => {
	const {fps} = useVideoConfig();
	const {width, height, vertical} = useUnits();
	const palette = brand.palette;
	const r = regions(scene.layout, width, height);
	const bg = colorOf(scene.background.color, palette);
	const panel = scene.background.panel ? colorOf(scene.background.panel, palette) : bg;
	const textBg = scene.layout === 'split_panel' ? panel : bg;
	const info: SceneInfo = {
		startFrame,
		endFrame: Math.round(scene.end * fps) - startFrame,
		bg,
		textBg,
		ink: inkOn(textBg),
		muted: mutedOn(textBg),
		accent: luminance(textBg) > 0.55 ? THEME.palette.crimson : THEME.palette.yellow,
		palette,
		brand,
		text: r.text,
		visual: r.visual,
		align: r.align,
	};
	// which colour the watermark sits on
	const onPanel =
		(scene.background.kind === 'split' && (scene.watermark_pos === 'mid_left') !== vertical) ||
		(scene.background.kind === 'diagonal' && !vertical && scene.watermark_pos === 'top_right');
	return (
		<SceneCtx.Provider value={info}>
			<AbsoluteFill style={{background: bg, overflow: 'hidden'}}>
				<Background scene={scene} panel={panel} />
				{scene.bg_word ? (
					<OutlineBgWord
						text={scene.bg_word}
						color={inkOn(scene.background.kind === 'diagonal' ? panel : bg)}
						side={scene.background.kind === 'diagonal' ? 'right' : 'left'}
					/>
				) : null}
				{scene.deco_text ? <DecoColumn text={scene.deco_text} color={inkOn(bg)} /> : null}
				{scene.layout === 'center_bracket' ? <Brackets color={info.ink} /> : null}
				<Elements elements={scene.elements} />
				{scene.watermark_pos !== 'none' ? (
					<Watermark handle={brand.handle} pos={scene.watermark_pos} color={inkOn(onPanel ? panel : bg)} />
				) : null}
			</AbsoluteFill>
		</SceneCtx.Provider>
	);
};

const Background: React.FC<{scene: Scene; panel: string}> = ({scene, panel}) => {
	const frame = useCurrentFrame();
	const {fps} = useVideoConfig();
	const {width, height, vertical} = useUnits();
	const kind = scene.background.kind;
	if (kind === 'color') return null;
	const s = cardSpring(frame, 0, fps);
	if (kind === 'split') {
		const p = regions('split_panel', width, height).panel!;
		return (
			<div
				style={{
					position: 'absolute',
					left: p.x,
					top: p.y,
					width: p.w,
					height: p.h,
					background: panel,
					transform: vertical ? `translateY(${(s - 1) * 100}%)` : `translateX(${(s - 1) * 100}%)`,
				}}
			/>
		);
	}
	const pts = vertical
		? `0 ${height * 0.62}px, ${width}px ${height * 0.5}px, ${width}px ${height}px, 0 ${height}px`
		: `${width * 0.66}px 0, ${width}px 0, ${width}px ${height}px, ${width * 0.54}px ${height}px`;
	return <AbsoluteFill style={{background: panel, clipPath: `polygon(${pts})`, transform: `translateX(${(1 - s) * 50}%)`}} />;
};

type Block = {els: Element[]; start: number; end: number};

const Elements: React.FC<{elements: Element[]}> = ({elements}) => {
	const frame = useCurrentFrame();
	const {tu: u, d} = useUnits();
	const {rel, relEnd} = useRel();
	const scene = useScene();
	const flow = elements.filter((e) => FLOW.has(e.type) && !isCaption(e));
	const blocks = useMemo(() => {
		const out: Block[] = [];
		for (const e of flow) { // storyboard order is column order
			const s = rel(e.start);
			const end = relEnd(e.end) ?? scene.endFrame;
			const last = out[out.length - 1];
			if (last && s < last.end) {
				last.els.push(e);
				last.end = Math.max(last.end, end);
			} else out.push({els: [e], start: s, end});
		}
		return out;
	}, [flow, rel, relEnd, scene.endFrame]);
	const gap = THEME.layout.gap * u;
	const k = useMemo(
		() => Math.min(1, ...blocks.map((b) => fitScale(measurables(b.els, u), scene.text.w, scene.text.h, gap))),
		[blocks, u, scene.text.w, scene.text.h, gap],
	);
	return (
		<>
			{blocks.map((b, i) =>
				frame >= b.start && frame < b.end + d(THEME.motion.exit) ? (
					<div
						key={i}
						style={{
							position: 'absolute',
							left: scene.text.x,
							top: scene.text.y,
							width: scene.text.w,
							height: scene.text.h,
							display: 'flex',
							flexDirection: 'column',
							justifyContent: 'center',
							alignItems: scene.align === 'center' ? 'center' : 'flex-start',
							textAlign: scene.align,
							gap: gap * k,
						}}
					>
						{groupCards(b.els).map((g, j) => (
							<FlowItem key={j} group={g} k={k} />
						))}
					</div>
				) : null,
			)}
			{elements
				.filter((e) => !FLOW.has(e.type) || isCaption(e))
				.map((e, i) => {
					const at = rel(e.start);
					const end = relEnd(e.end);
					if (frame < at || (end !== null && frame >= end)) return null;
					return (
						<div key={i} style={{position: 'absolute', inset: 0, opacity: exitFade(frame, end, d)}}>
							<Visual el={e} at={at} />
						</div>
					);
				})}
		</>
	);
};

/** Consecutive cards sit side by side in one row. */
const groupCards = (els: Element[]): Element[][] => {
	const out: Element[][] = [];
	for (const e of els) {
		const last = out[out.length - 1];
		if (e.type === 'card' && last && last[0].type === 'card') last.push(e);
		else out.push([e]);
	}
	return out;
};

const measurables = (els: Element[], u: number): Measurable[] =>
	groupCards(els).map((g): Measurable => {
		const e = g[0];
		const T = THEME.type;
		switch (e.type) {
			case 'text_line': {
				const role = T[e.props.role];
				return {kind: 'words', words: e.props.words, size: role.size * u, weight: role.weight, tracking: role.tracking};
			}
			case 'card':
				return {
					kind: 'row',
					items: g.map((c) => {
						const size = T.card.size * u;
						return {words: c.type === 'card' ? c.props.words : [], size, padX: CARD_PAD.x * size, padY: CARD_PAD.y * size};
					}),
				};
			case 'list_reveal':
				return {kind: 'fixed', height: e.props.items.length * LIST_ROW_EM * T.lead.size * 1.5 * u};
			case 'tag_pill':
				return {kind: 'fixed', height: T.pill * u * 1.7};
			default:
				return {kind: 'fixed', height: u * 0.05};
		}
	});

const FlowItem: React.FC<{group: Element[]; k: number}> = ({group, k}) => {
	const frame = useCurrentFrame();
	const {d, tu: u} = useUnits();
	const {rel, relEnd} = useRel();
	const {align} = useScene();
	const e = group[0];
	const fade = exitFade(frame, relEnd(e.end), d);
	if (e.type === 'card')
		return (
			<div style={{display: 'flex', flexWrap: 'wrap', justifyContent: align === 'center' ? 'center' : 'flex-start', gap: CARD_PAD.x * THEME.type.card.size * u * k}}>
				{group.map((c, i) =>
					c.type === 'card' ? (
						<div key={i} style={{opacity: exitFade(frame, relEnd(c.end), d)}}>
							<ColorCard props={c.props} at={rel(c.start)} k={k} />
						</div>
					) : null,
				)}
			</div>
		);
	const at = rel(e.start);
	const inner =
		e.type === 'text_line' ? (
			<TextBlock props={e.props} k={k} />
		) : e.type === 'tag_pill' ? (
			<TagPill props={e.props} at={at} k={k} />
		) : e.type === 'list_reveal' ? (
			<ListReveal props={e.props} at={at} k={k} />
		) : e.type === 'swatches' ? (
			<Swatches props={e.props} at={at} k={k} />
		) : null;
	return <div style={{opacity: fade}}>{inner}</div>;
};

const Visual: React.FC<{el: Element; at: number}> = ({el, at}) => {
	const {rel} = useRel();
	switch (el.type) {
		case 'sticker':
			return <Sticker props={el.props} at={at} />;
		case 'ui_window':
			return <MacWindow props={el.props} at={at} />;
		case 'typing':
			return <TypingBox props={el.props} at={at} />;
		case 'comment_bubbles':
			return <CommentBubbles props={el.props} at={at} />;
		case 'button_state': {
			const tap = el.props.tap_at === null ? null : rel(el.props.tap_at);
			return el.props.variant === 'follow' ? (
				<FollowCard at={at} tap={tap ?? at + 20} />
			) : (
				<ApproveReject variant={el.props.variant} at={at} tap={tap} />
			);
		}
		case 'timeline_cut':
			return <TimelineCut props={el.props} at={at} />;
		case 'storyboard':
			return <StoryboardRow props={el.props} at={at} />;
		case 'draft_cards':
			return <DraftCards count={el.props.count} at={at} approved={el.props.approved_at === null ? null : rel(el.props.approved_at)} />;
		case 'dm_card':
			return <DmCard props={el.props} at={at} />;
		case 'character':
			return <Character side={el.props.side} at={at} />;
		case 'avatar_badge':
			return <AvatarBadge x={el.props.x} y={el.props.y} at={at} />;
		case 'outro':
			return <Outro props={el.props} at={at} />;
		case 'text_line':
			return <CaptionBar el={el} />;
		default:
			return null;
	}
};

/** A caption line in a dark bar across the bottom ("aur sab ke sab"). */
const CaptionBar: React.FC<{el: TextLine}> = ({el}) => {
	const {tu: u, height} = useUnits();
	const {palette} = useScene();
	const fs = THEME.type.caption.size * u;
	return (
		<div
			style={{
				position: 'absolute',
				left: 0,
				right: 0,
				bottom: 0,
				height: height * 0.11,
				background: colorOf('charcoal', palette),
				display: 'flex',
				alignItems: 'center',
				justifyContent: 'center',
				fontFamily: SANS,
				fontSize: fs * 1.3,
				letterSpacing: THEME.type.caption.tracking,
				lineHeight: 1,
			}}
		>
			<Words words={el.props.words} weight={THEME.type.caption.weight} color="#fff" bg={colorOf('charcoal', palette)} align="center" />
		</div>
	);
};
