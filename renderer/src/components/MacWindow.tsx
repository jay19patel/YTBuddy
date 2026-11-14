import {useCurrentFrame} from 'remotion';
import {ease} from '../motion';
import {useRel, useScene} from '../SceneContext';
import {SANS, THEME, useUnits} from '../theme';
import type {UiWindowProps} from '../types.gen';
import {AppBody} from './AppBody';
import {ChatInput} from './ChatInput';
import {DropZone} from './DropZone';
import {FinderList} from './FinderList';
import {FolderTree} from './FolderTree';
import {NoteCard} from './NoteCard';
import {ToolsTable} from './ToolsTable';

const LIGHTS = ['#FF5F57', '#FEBC2E', '#28C840'];

/** UI_WINDOW: a macOS-style window (traffic lights, title, 12 px corners, big soft shadow) that scales 0.95 → 1 in. */
export const MacWindow: React.FC<{props: UiWindowProps; at: number}> = ({props, at}) => {
	const frame = useCurrentFrame();
	const {d} = useUnits();
	const {visual} = useScene();
	const {rel} = useRel();
	const p = ease(frame, at, d(THEME.motion.window.frames));
	const tall = props.kind === 'note' || props.kind === 'tree';
	const ratio = tall ? 1.05 : 1.5;
	const w = Math.min(visual.w, visual.h * ratio) * 0.96;
	const h = w / ratio;
	const fs = w * 0.032; // window text scales with the window
	const textAt = props.text_at === null ? at + d(12) : rel(props.text_at);
	const body = {
		app: <AppBody props={props} at={at} />,
		finder: <FinderList items={props.items} at={at} />,
		chat: <ChatInput text={props.text} at={textAt} paste={props.paste} />,
		tools: <ToolsTable items={props.items} at={at} />,
		note: <NoteCard items={props.items} at={at} />,
		tree: <FolderTree items={props.items} at={at} />,
		drop: <DropZone items={props.items} at={at} />,
	}[props.kind];
	return (
		<div
			style={{
				position: 'absolute',
				left: visual.x + (visual.w - w) / 2,
				top: visual.y + (visual.h - h) / 2,
				width: w,
				height: h,
				background: '#FFFFFF',
				borderRadius: THEME.shadow.radius,
				boxShadow: THEME.shadow.window,
				overflow: 'visible',
				fontFamily: SANS,
				fontSize: fs,
				color: THEME.palette.ink,
				opacity: p,
				transform: `scale(${THEME.motion.window.scaleFrom + (1 - THEME.motion.window.scaleFrom) * p})`,
				display: 'flex',
				flexDirection: 'column',
			}}
		>
			<div
				style={{
					height: '2.2em',
					flex: 'none',
					display: 'flex',
					alignItems: 'center',
					padding: '0 0.8em',
					gap: '0.45em',
					borderBottom: '1px solid #ECECEC',
					background: '#F6F6F6',
					borderRadius: `${THEME.shadow.radius}px ${THEME.shadow.radius}px 0 0`,
					position: 'relative',
				}}
			>
				{LIGHTS.map((c) => (
					<div key={c} style={{width: '0.75em', height: '0.75em', borderRadius: 99, background: c}} />
				))}
				<div style={{position: 'absolute', left: 0, right: 0, textAlign: 'center', fontWeight: 600, color: '#555', fontSize: '0.9em', pointerEvents: 'none'}}>
					{props.title}
				</div>
			</div>
			<div style={{flex: 1, position: 'relative', overflow: 'hidden', borderRadius: `0 0 ${THEME.shadow.radius}px ${THEME.shadow.radius}px`}}>{body}</div>
		</div>
	);
};
