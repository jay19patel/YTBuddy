import {Img, staticFile, useCurrentFrame} from 'remotion';
import {ease} from '../motion';
import {useRel, useScene} from '../SceneContext';
import {colorOf, SANS, THEME, useUnits} from '../theme';
import type {ListRevealProps} from '../types.gen';

export const LIST_ROW_EM = 1.7;

/** LIST_REVEAL: icon + label rows, one by one (stagger 250 ms unless an item says when). */
export const ListReveal: React.FC<{props: ListRevealProps; at: number; k: number}> = ({props, at, k}) => {
	const frame = useCurrentFrame();
	const {d, tu: u} = useUnits();
	const {rel} = useRel();
	const {ink, palette} = useScene();
	return (
		<div style={{fontFamily: SANS, fontSize: THEME.type.lead.size * 1.5 * u * k, fontWeight: 700, letterSpacing: '-0.03em'}}>
			{props.items.map((it, i) => {
				const t = it.at === null ? at + i * d(THEME.motion.stagger) : rel(it.at);
				const p = ease(frame, t, d(THEME.motion.wordIn.frames));
				return (
					<div
						key={i}
						style={{
							display: 'flex',
							alignItems: 'center',
							gap: '0.4em',
							height: `${LIST_ROW_EM}em`,
							opacity: p,
							transform: `translateX(${(1 - p) * 30}px)`,
							color: it.color ? colorOf(it.color, palette) : ink,
						}}
					>
						{it.icon ? <Img src={staticFile(`stickers/${it.icon}.png`)} style={{width: '1.2em', height: '1.2em'}} /> : null}
						<span>{it.label}</span>
						{it.sub ? <span style={{opacity: 0.6, fontWeight: 400}}>{it.sub}</span> : null}
					</div>
				);
			})}
		</div>
	);
};
