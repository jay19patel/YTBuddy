import {useCurrentFrame, useVideoConfig} from 'remotion';
import {cardSpring} from '../motion';
import {useScene} from '../SceneContext';
import {colorOf, inkOn, lineHeightOf, SANS, THEME, useUnits} from '../theme';
import type {CardProps} from '../types.gen';
import {Words} from './TextBlock';

export const CARD_PAD = {x: 0.5, y: 0.28}; // em of the card text size

/** CARD_IN: a solid colour block springs in (slight overshoot) carrying spoken words, e.g. "na ~salary~". */
export const ColorCard: React.FC<{props: CardProps; at: number; k: number}> = ({props, at, k}) => {
	const frame = useCurrentFrame();
	const {fps} = useVideoConfig();
	const {tu: u} = useUnits();
	const {palette} = useScene();
	const s = cardSpring(frame, at, fps);
	const bg = colorOf(props.color, palette);
	const off = (1 - s) * 120;
	const transform =
		props.enter === 'left' ? `translateX(${-off}%)` : props.enter === 'right' ? `translateX(${off}%)` : `translateY(${off}%)`;
	return (
		<div
			style={{
				background: bg,
				padding: `${CARD_PAD.y}em ${CARD_PAD.x}em`,
				borderRadius: '0.18em',
				fontFamily: SANS,
				fontSize: THEME.type.card.size * u * k,
				letterSpacing: THEME.type.card.tracking,
				lineHeight: lineHeightOf(props.words),
				boxShadow: THEME.shadow.card,
				opacity: frame < at ? 0 : Math.min(1, s * 2),
				transform,
			}}
		>
			<Words words={props.words} weight={THEME.type.card.weight} color={inkOn(bg)} bg={bg} align="left" />
		</div>
	);
};
