import {DISPLAY, THEME, useUnits} from '../theme';
import {InstagramIcon} from './InstagramIcon';

/** Your handle in condensed caps with the camera glyph; fixed within a scene, top-right or mid-left. */
export const Watermark: React.FC<{handle: string; pos: 'top_right' | 'mid_left'; color: string}> = ({handle, pos, color}) => {
	const {u, width, height} = useUnits();
	const fs = THEME.type.watermark * u;
	const mx = width * THEME.layout.safe;
	const my = height * THEME.layout.safe;
	return (
		<div
			style={{
				position: 'absolute',
				...(pos === 'top_right' ? {right: mx, top: my * 0.8} : {left: mx * 0.5, top: '50%', transform: 'translateY(-50%)'}),
				display: 'flex',
				alignItems: 'center',
				gap: fs * 0.35,
				fontFamily: DISPLAY,
				fontSize: fs,
				letterSpacing: '0.04em',
				color,
				opacity: 0.85,
			}}
		>
			<InstagramIcon size={fs * 0.95} color={color} />
			<span style={{paddingTop: fs * 0.08}}>{handle.toUpperCase()}</span>
		</div>
	);
};
