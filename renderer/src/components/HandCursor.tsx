import {Img, staticFile, useCurrentFrame} from 'remotion';
import {ease} from '../motion';
import {useUnits} from '../theme';

/** The 👆 hand that glides in and taps at frame `tap`. Place inside a positioned parent; it lands on its centre. */
export const HandCursor: React.FC<{tap: number; size: string}> = ({tap, size}) => {
	const frame = useCurrentFrame();
	const {d} = useUnits();
	const p = ease(frame, tap - d(12), d(12));
	const press = frame >= tap && frame < tap + d(4) ? 0.85 : 1;
	if (frame < tap - d(12)) return null;
	return (
		<Img
			src={staticFile('stickers/pointer.png')}
			style={{
				position: 'absolute',
				width: size,
				left: '55%',
				top: '45%',
				transform: `translate(${(1 - p) * 160}%, ${(1 - p) * 200}%) scale(${press})`,
				filter: 'drop-shadow(0 8px 12px rgba(0,0,0,.3))',
			}}
		/>
	);
};
