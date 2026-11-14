import {useEffect, useState} from 'react';
import {AbsoluteFill, cancelRender, continueRender, delayRender, Sequence, useVideoConfig} from 'remotion';
import {overlapFrames, TransitionIn} from './components/Transitions';
import {fontsReady} from './fonts';
import {SceneView} from './Scene';
import {useUnits} from './theme';
import type {Timeline} from './types.gen';

/** The whole video: one Sequence per scene. A scene stays mounted under the next one while its transition runs. */
export const KineticVideo: React.FC<{timeline: Timeline}> = ({timeline}) => {
	const {fps} = useVideoConfig();
	const {d} = useUnits();
	const [handle] = useState(() => delayRender('fonts'));
	const [ready, setReady] = useState(false);
	useEffect(() => {
		fontsReady
			.then(() => {
				setReady(true);
				continueRender(handle);
			})
			.catch((e) => cancelRender(e));
	}, [handle]);
	if (!ready) return null; // text is measured for fitting, so nothing renders before the fonts are in
	const scenes = timeline.scenes;
	return (
		<AbsoluteFill style={{background: '#000'}}>
			{scenes.map((s, i) => {
				const from = Math.round(s.start * fps);
				const next = scenes[i + 1];
				const dur = Math.round(s.end * fps) - from + (next ? overlapFrames(next.transition_in, d) : 0);
				return (
					<Sequence key={s.id} from={from} durationInFrames={Math.max(1, dur)} name={s.id}>
						<TransitionIn kind={s.transition_in}>
							<SceneView scene={s} brand={timeline.video.brand} startFrame={from} />
						</TransitionIn>
					</Sequence>
				);
			})}
		</AbsoluteFill>
	);
};
