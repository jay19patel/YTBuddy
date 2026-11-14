import {Img, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import {cardSpring, ease, interp, stickerSpring} from '../motion';
import {useRel, useScene} from '../SceneContext';
import {SANS, THEME, useUnits} from '../theme';
import type {OutroProps} from '../types.gen';
import {InstagramIcon} from './InstagramIcon';
import {Swatches} from './Swatches';

/** Avatar on top, handle big and bold, "see you in [day 07 →]", five palette dashes; then the camera glyph scales up
 * in the centre and fills from white to the gradient. */
export const Outro: React.FC<{props: OutroProps; at: number}> = ({props, at}) => {
	const frame = useCurrentFrame();
	const {fps} = useVideoConfig();
	const {u, d} = useUnits();
	const {brand, ink, endFrame} = useScene();
	const {rel} = useRel();
	const logoAt = props.logo_at === null ? Math.max(at + d(60), endFrame - d(45)) : rel(props.logo_at);
	const out = ease(frame, logoAt, d(8));
	const logo = stickerSpring(frame, logoAt, fps);
	const fs = u * 0.08;
	return (
		<>
			<div
				style={{
					position: 'absolute',
					inset: 0,
					display: 'flex',
					flexDirection: 'column',
					alignItems: 'center',
					justifyContent: 'center',
					gap: fs * 0.35,
					fontFamily: SANS,
					color: ink,
					opacity: 1 - out,
					transform: `scale(${1 - out * 0.08})`,
				}}
			>
				{brand.avatar ? (
					<Img
						src={staticFile(brand.avatar)}
						style={{width: fs * 2, height: fs * 2, borderRadius: 999, border: `${fs * 0.07}px solid #fff`, transform: `scale(${stickerSpring(frame, at, fps)})`}}
					/>
				) : null}
				<div style={{fontWeight: 800, fontSize: fs, letterSpacing: '-0.045em', transform: `translateY(${(1 - cardSpring(frame, at + d(4), fps)) * 40}px)`, opacity: ease(frame, at + d(4), d(7))}}>
					{brand.handle}
				</div>
				<div style={{display: 'flex', alignItems: 'center', gap: '0.4em', fontSize: fs * 0.42, opacity: ease(frame, at + d(12), d(7))}}>
					see you in
					<span style={{background: THEME.palette.yellow, color: THEME.palette.ink, fontWeight: 800, padding: '0.05em 0.4em', transform: `scale(${stickerSpring(frame, at + d(16), fps)})`}}>
						{props.next_label} →
					</span>
				</div>
				<div style={{marginTop: fs * 0.3}}>
					<Swatches props={{colors: ['black', 'orange', 'blue', 'beige', 'crimson'], shape: 'dashes'}} at={at + d(22)} k={1} />
				</div>
			</div>
			{frame >= logoAt ? (
				<div style={{position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center'}}>
					<div style={{transform: `scale(${logo})`}}>
						<InstagramIcon size={u * 0.3} color="#fff" fill={interp(frame, [logoAt + d(10), logoAt + d(22)], [0, 1])} />
					</div>
				</div>
			) : null}
		</>
	);
};
