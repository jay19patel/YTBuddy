import {Img, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import {cardSpring, float} from '../motion';
import {useScene} from '../SceneContext';
import {THEME, useUnits} from '../theme';

/** CHARACTER: your full-body presenter image stands at the frame's bottom edge with a subtle idle bob. */
export const Character: React.FC<{side: 'left' | 'right'; at: number}> = ({side, at}) => {
	const frame = useCurrentFrame();
	const {fps} = useVideoConfig();
	const {d, height, width, vertical} = useUnits();
	const {brand} = useScene();
	if (!brand.character) return null;
	const s = cardSpring(frame, at, fps);
	const h = height * (vertical ? 0.42 : 0.82);
	const bob = float(frame, d, 0, THEME.motion.bob.amplitude, THEME.motion.bob.period);
	return (
		<Img
			src={staticFile(brand.character)}
			style={{
				position: 'absolute',
				height: h,
				bottom: 0,
				[side]: width * (vertical ? 0.02 : 0.04),
				transform: `translateY(${(1 - s) * h * 0.6 + bob}px)`,
				transformOrigin: 'bottom center',
			}}
		/>
	);
};
