import {Img, staticFile, useCurrentFrame} from 'remotion';
import {ease, interp} from '../motion';
import {useRel, useScene} from '../SceneContext';
import {THEME, useUnits} from '../theme';
import type {TimelineCutProps} from '../types.gen';

const WIDTHS = [1.4, 0.8, 1.1, 1.6, 0.6, 1.2, 0.9, 1.5, 0.7, 1.0];

/** TIMELINE_CUT: a fake edit timeline of red/gray clips; scissors sweep across and the bad takes drop out. */
export const TimelineCut: React.FC<{props: TimelineCutProps; at: number}> = ({props, at}) => {
	const frame = useCurrentFrame();
	const {d, u} = useUnits();
	const {rel} = useRel();
	const {visual} = useScene();
	const cutAt = props.cut_at === null ? at + d(15) : rel(props.cut_at);
	const h = u * 0.075;
	const sweep = ease(frame, cutAt - d(6), d(24));
	return (
		<div style={{position: 'absolute', left: visual.x, top: visual.y + visual.h * 0.22, width: visual.w, height: h * 2.4}}>
			<div style={{display: 'flex', gap: h * 0.12, height: h, opacity: ease(frame, at, d(8))}}>
				{Array.from({length: props.blocks}, (_, i) => {
					const isCut = props.cuts.includes(i);
					const order = props.cuts.indexOf(i);
					const t = cutAt + order * d(5);
					const drop = isCut ? ease(frame, t, d(10)) : 0;
					return (
						<div
							key={i}
							style={{
								flex: `${WIDTHS[i % WIDTHS.length] * (1 - (isCut ? interp(frame, [t + d(8), t + d(16)], [0, 1]) : 0))} 1 0`,
								minWidth: 0,
								borderRadius: h * 0.16,
								background: isCut ? THEME.palette.crimson : '#BDB8B0',
								opacity: 1 - drop,
								transform: `translateY(${drop * h * 1.6}px) rotate(${drop * (i % 2 ? 14 : -14)}deg)`,
							}}
						/>
					);
				})}
			</div>
			<Img
				src={staticFile('stickers/scissors.png')}
				style={{position: 'absolute', width: h * 1.5, top: -h * 1.2, left: `calc(${sweep * 92}% - ${h * 0.75}px)`, transform: 'rotate(-90deg)', opacity: frame < cutAt - d(6) ? 0 : 1}}
			/>
		</div>
	);
};
