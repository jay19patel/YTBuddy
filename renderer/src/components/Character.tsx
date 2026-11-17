import {Img, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import {cardSpring, float, interp} from '../motion';
import {useRel, useScene} from '../SceneContext';
import {THEME, useUnits} from '../theme';
import type {CharacterProps} from '../types.gen';

/** CHARACTER: the presenter stands at the frame's bottom edge with a subtle idle bob. A new expression lands with a
 * small squash instead of walking in again; with a mouth-open picture the mouth moves on every spoken word. */
export const Character: React.FC<{props: CharacterProps; at: number}> = ({props, at}) => {
	const frame = useCurrentFrame();
	const {fps} = useVideoConfig();
	const {d, height, width, vertical} = useUnits();
	const {brand} = useScene();
	const {rel} = useRel();
	const src = props.image ?? brand.character;
	if (!src) return null;
	const M = THEME.motion;
	const s = props.enter ? cardSpring(frame, at, fps) : 1;
	const squash = props.enter ? 1 : interp(frame - at, [0, d(M.faceSwap.frames)], [M.faceSwap.squash, 1]);
	const h = height * (vertical ? 0.42 : 0.82);
	const bob = float(frame, d, 0, M.bob.amplitude, M.bob.period);
	const speaking = props.talk.some(([a, b]) => frame >= rel(a) && frame < rel(b));
	const open = props.talk_image !== null && speaking && Math.floor(frame / d(M.talk)) % 2 === 0;
	return (
		<div
			style={{
				position: 'absolute',
				height: h,
				bottom: 0,
				[props.side]: width * (vertical ? 0.02 : 0.04),
				transform: `translateY(${(1 - s) * h * 0.6 + bob}px) scale(${2 - squash}, ${squash})`,
				transformOrigin: 'bottom center',
			}}
		>
			{/* both pictures stay loaded, so the mouth never waits for a file */}
			<Img src={staticFile(src)} style={{height: '100%', display: 'block', opacity: open ? 0 : 1}} />
			{props.talk_image ? (
				<Img src={staticFile(props.talk_image)} style={{position: 'absolute', left: 0, top: 0, height: '100%', opacity: open ? 1 : 0}} />
			) : null}
		</div>
	);
};
