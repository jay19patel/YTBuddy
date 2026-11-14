import {useCurrentFrame, useVideoConfig} from 'remotion';
import {stickerSpring} from '../motion';
import {useItemAt} from '../SceneContext';
import {THEME} from '../theme';
import type {Item} from '../types.gen';

/** Tools table: every row is there from the start; each checkbox ticks green at its item's `at`. */
export const ToolsTable: React.FC<{items: Item[]; at: number}> = ({items, at}) => {
	const frame = useCurrentFrame();
	const {fps} = useVideoConfig();
	const itemAt = useItemAt(at);
	return (
		<div style={{padding: '1em 1.4em'}}>
			<div style={{display: 'flex', fontWeight: 700, color: '#888', fontSize: '0.8em', padding: '0 0 0.5em', borderBottom: '1px solid #eee'}}>
				<span style={{width: '38%'}}>TOOL</span>
				<span style={{flex: 1}}>WHAT IT DOES</span>
				<span>INSTALLED</span>
			</div>
			{items.map((it, i) => {
				const t = itemAt(it, i);
				const s = stickerSpring(frame, t, fps);
				const done = frame >= t;
				return (
					<div key={i} style={{display: 'flex', alignItems: 'center', padding: '0.62em 0', borderBottom: '1px solid #f1f1f1'}}>
						<span style={{width: '38%', fontWeight: 700}}>{it.label}</span>
						<span style={{flex: 1, color: '#777', fontSize: '0.9em'}}>{it.sub}</span>
						<span
							style={{
								width: '1.4em',
								height: '1.4em',
								marginRight: '1.6em',
								borderRadius: 6,
								border: done ? 'none' : '2px solid #ccc',
								background: done ? THEME.palette.green : 'transparent',
								color: '#fff',
								fontWeight: 900,
								display: 'flex',
								alignItems: 'center',
								justifyContent: 'center',
								transform: `scale(${done ? 0.7 + 0.3 * s : 1})`,
							}}
						>
							{done ? '✓' : ''}
						</span>
					</div>
				);
			})}
		</div>
	);
};
