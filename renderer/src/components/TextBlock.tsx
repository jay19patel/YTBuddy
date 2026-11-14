import {useScene, useRel} from '../SceneContext';
import {colorOf, SANS, THEME, useUnits} from '../theme';
import type {TextLineProps, Word} from '../types.gen';
import {WORD_GAP_EM} from '../fit';
import {AnimatedWord} from './AnimatedWord';

/** Words of one line, each appearing on its own spoken frame; the next word ghosts in just before. */
export const Words: React.FC<{words: Word[]; weight: number; color: string; bg: string; align: 'left' | 'center'}> = ({
	words,
	weight,
	color,
	bg,
	align,
}) => {
	const {rel} = useRel();
	const {d} = useUnits();
	return (
		<div style={{display: 'flex', flexWrap: 'wrap', columnGap: `${WORD_GAP_EM}em`, alignItems: 'baseline', justifyContent: align === 'center' ? 'center' : 'flex-start'}}>
			{words.map((w, i) => {
				const at = rel(w.start);
				const ghostFrom = i > 0 ? Math.max(rel(words[i - 1].start), at - d(THEME.motion.ghost.frames)) : null;
				return <AnimatedWord key={i} word={w} at={at} ghostFrom={ghostFrom} weight={weight} color={color} bg={bg} />;
			})}
		</div>
	);
};

/** A lead-in, hero or sub line of the stacked text block. `k` is the fit-to-safe-area scale. */
export const TextBlock: React.FC<{props: TextLineProps; k: number}> = ({props, k}) => {
	const {tu: u} = useUnits();
	const {ink, muted, textBg, palette, align} = useScene();
	const role = THEME.type[props.role];
	const secondary = props.role === 'lead' || props.role === 'sub';
	const color = props.color ? colorOf(props.color, palette) : secondary ? muted : ink;
	return (
		<div style={{fontFamily: SANS, fontSize: role.size * u * k, letterSpacing: role.tracking, lineHeight: THEME.type.lineHeight}}>
			<Words words={props.words} weight={role.weight} color={color} bg={textBg} align={align} />
		</div>
	);
};
