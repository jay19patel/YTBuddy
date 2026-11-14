import {useCurrentFrame, useVideoConfig} from 'remotion';
import {interp, stickerSpring} from '../motion';
import {SANS} from '../theme';

/** A keyboard key ("⌘V") that pops when a prompt is pasted, then fades after a second. */
export const KeyBadge: React.FC<{label: string; at: number; size: number | string}> = ({label, at, size}) => {
	const frame = useCurrentFrame();
	const {fps} = useVideoConfig();
	if (frame < at) return null;
	const s = stickerSpring(frame, at, fps);
	const press = interp(frame - at, [4, 7, 10], [1, 0.92, 1]);
	return (
		<div
			style={{
				position: 'absolute',
				right: '6%',
				top: '-30%',
				fontFamily: SANS,
				fontWeight: 700,
				fontSize: size,
				color: '#fff',
				background: '#1B181D',
				padding: '0.25em 0.55em',
				borderRadius: '0.25em',
				boxShadow: '0 0.12em 0 #000, 0 10px 24px rgba(0,0,0,.3)',
				opacity: interp(frame - at, [fps * 1.4, fps * 1.7], [1, 0]),
				transform: `scale(${s * press}) rotate(-6deg)`,
			}}
		>
			{label}
		</div>
	);
};
