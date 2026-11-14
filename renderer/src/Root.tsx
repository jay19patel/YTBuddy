import {type CalculateMetadataFunction, Composition} from 'remotion';
import demo from '../../demo/timeline.json';
import type {Timeline} from './types.gen';
import {KineticVideo} from './Video';

type Props = {timeline: Timeline};

const DEMO = demo as unknown as Timeline;
const DEMO_VERTICAL: Timeline = {...DEMO, video: {...DEMO.video, width: 1080, height: 1920}};

// Size, fps and length come from the timeline, so Python decides them (`--props` carries the timeline).
const fromTimeline: CalculateMetadataFunction<Props> = ({props}) => ({
	width: props.timeline.video.width,
	height: props.timeline.video.height,
	fps: props.timeline.video.fps,
	durationInFrames: props.timeline.video.duration_frames,
});

export const Root: React.FC = () => (
	<>
		<Composition id="Main" component={KineticVideo} defaultProps={{timeline: DEMO}} calculateMetadata={fromTimeline} width={1920} height={1080} fps={30} durationInFrames={1} />
		<Composition id="Demo" component={KineticVideo} defaultProps={{timeline: DEMO}} calculateMetadata={fromTimeline} width={1920} height={1080} fps={30} durationInFrames={1} />
		<Composition id="DemoVertical" component={KineticVideo} defaultProps={{timeline: DEMO_VERTICAL}} calculateMetadata={fromTimeline} width={1080} height={1920} fps={30} durationInFrames={1} />
	</>
);
