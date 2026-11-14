import {useCurrentFrame} from 'remotion';
import {ease} from '../motion';
import {SANS, useUnits} from '../theme';

/** Thin, tall "(" and ")" at the far edges, framing a centred sentence on black scenes. */
export const Brackets: React.FC<{color: string}> = ({color}) => {
	const frame = useCurrentFrame();
	const {u, width, d} = useUnits();
	const p = ease(frame, 0, d(10));
	const style = (side: 'left' | 'right'): React.CSSProperties => ({
		position: 'absolute',
		[side]: width * 0.04 - (1 - p) * 40,
		top: '50%',
		transform: 'translateY(-54%)',
		fontFamily: SANS,
		fontWeight: 100,
		fontSize: u * 0.62,
		lineHeight: 1,
		color,
		opacity: 0.9 * p,
	});
	return (
		<>
			<div style={style('left')}>(</div>
			<div style={style('right')}>)</div>
		</>
	);
};
