import {interpolateColors, useCurrentFrame} from 'remotion';
import {BOX_PAD_EM} from '../fit';
import {interp, wordIn} from '../motion';
import {useScene} from '../SceneContext';
import {colorOf, inkOn, isDevanagari, SANS, SERIF, THEME, useUnits} from '../theme';
import type {Word} from '../types.gen';
import {HighlightBox} from './HighlightBox';
import {SerifReveal} from './SerifReveal';
import {StrikeThrough} from './StrikeThrough';

const M = THEME.motion;

/** One spoken word. Invisible (but holding its place) until said; a gray blurred ghost just before; then WORD_IN.
 * `at` and `ghostFrom` are scene-relative frames. */
export const AnimatedWord: React.FC<{
	word: Word;
	at: number;
	ghostFrom: number | null;
	weight: number;
	color: string;
	bg: string; // what is behind the word: picks text colour inside a white highlight box
}> = ({word, at, ghostFrom, weight, color, bg}) => {
	const frame = useCurrentFrame();
	const {d} = useUnits();
	const {palette, accent} = useScene();
	const t = frame - at;
	// negative tracking would crush Devanagari vowel signs into the letters: Hindi words keep normal spacing
	const base: React.CSSProperties = {
		display: 'inline-block',
		position: 'relative',
		whiteSpace: 'pre',
		fontWeight: weight,
		letterSpacing: isDevanagari(word.text) ? '0' : undefined,
	};

	if (t < 0) {
		const ghost = ghostFrom !== null && frame >= ghostFrom;
		return (
			<span style={{...base, opacity: ghost ? M.ghost.opacity : 0, filter: `blur(${M.ghost.blur}px)`, color: THEME.palette.gray}}>
				{inner(word)}
			</span>
		);
	}

	switch (word.style) {
		case 'serif':
			return <SerifReveal word={word} at={at} />;
		case 'highlight': {
			const box = colorOf(word.color ?? 'yellow', palette);
			const textColor = box.toUpperCase() === THEME.palette.white ? bg : inkOn(box);
			const wiped = d(M.highlight.frames);
			const pop = interp(t, [wiped, wiped + d(4)], [M.highlight.pop, 1]);
			return (
				<span style={{...base, padding: `0 ${BOX_PAD_EM}em`, fontWeight: THEME.type.boldWeight}}>
					<HighlightBox color={box} at={at} />
					<span
						style={{
							position: 'relative',
							display: 'inline-block',
							color: textColor,
							opacity: t < wiped ? 0 : 1,
							transform: `scale(${t < wiped ? M.highlight.pop : pop})`,
						}}
					>
						{word.text}
					</span>
				</span>
			);
		}
		default: {
			const target = word.style === 'accent' ? colorOf(word.color ?? accent, palette) : color;
			const c = word.style === 'accent' ? interpolateColors(t, [0, d(M.accent.frames)], [color, target]) : color;
			return (
				<span
					style={{
						...base,
						...wordIn(frame, at, d),
						color: c,
						fontWeight: word.style === 'bold' ? THEME.type.boldWeight : weight,
					}}
				>
					{word.text}
					{word.style === 'strike' ? <StrikeThrough at={at} color={word.color ? colorOf(word.color, palette) : c} /> : null}
				</span>
			);
		}
	}
};

const inner = (w: Word) => (
	<span
		style={
			w.style === 'serif'
				? {fontFamily: SERIF, fontStyle: 'italic', fontWeight: 900, fontSize: `${THEME.type.serifScale}em`}
				: w.style === 'highlight'
					? {padding: `0 ${BOX_PAD_EM}em`, fontFamily: SANS}
					: {}
		}
	>
		{w.text}
	</span>
);
