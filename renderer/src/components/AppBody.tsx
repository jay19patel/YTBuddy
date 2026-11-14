import {Img, staticFile} from 'remotion';
import {THEME} from '../theme';
import type {UiWindowProps} from '../types.gen';
import {TypingText} from './TypingText';

/** A generic app window: your screenshot when given, otherwise a desktop-app skeleton with a greeting. */
export const AppBody: React.FC<{props: UiWindowProps; at: number}> = ({props, at}) =>
	props.image ? (
		<Img src={staticFile(props.image)} style={{width: '100%', height: '100%', objectFit: 'cover'}} />
	) : (
		<div style={{display: 'flex', height: '100%', background: '#FAF9F5'}}>
			<div style={{width: '22%', background: '#F0EEE6', padding: '1em 0.8em', display: 'flex', flexDirection: 'column', gap: '0.7em'}}>
				<div style={{height: '1.6em', borderRadius: 8, background: THEME.palette.orange, opacity: 0.9}} />
				{[80, 60, 70, 50, 65].map((w, i) => (
					<div key={i} style={{height: '0.55em', width: `${w}%`, background: '#DCD9CE', borderRadius: 99}} />
				))}
			</div>
			<div style={{flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: '1em'}}>
				<div style={{fontSize: '1.9em', fontWeight: 700, letterSpacing: '-0.03em'}}>
					<TypingText text={props.text || 'how can I help you today?'} at={at + 6} caret={false} />
				</div>
				<div style={{width: '70%', height: '3.2em', borderRadius: '0.9em', border: '1.5px solid #E2E0D8', background: '#fff'}} />
			</div>
		</div>
	);
