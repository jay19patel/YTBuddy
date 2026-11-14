import {THEME} from '../theme';
import {KeyBadge} from './KeyBadge';
import {TypingText} from './TypingText';

/** A chat app: a short history skeleton and an input where the prompt is typed (or pasted with a ⌘V badge). */
export const ChatInput: React.FC<{text: string; at: number; paste: boolean}> = ({text, at, paste}) => (
	<div style={{display: 'flex', height: '100%'}}>
		<div style={{width: '22%', background: '#F4F3EE', padding: '1em 0.8em', display: 'flex', flexDirection: 'column', gap: '0.7em'}}>
			{[70, 55, 80, 45].map((w, i) => (
				<div key={i} style={{height: '0.55em', width: `${w}%`, background: '#DEDCD3', borderRadius: 99}} />
			))}
		</div>
		<div style={{flex: 1, display: 'flex', flexDirection: 'column', justifyContent: 'flex-end', padding: '1.2em', background: '#FAF9F5'}}>
			<div
				style={{
					position: 'relative',
					minHeight: '5.5em',
					border: '1.5px solid #E2E0D8',
					borderRadius: '0.9em',
					background: '#fff',
					padding: '0.8em 1em',
					fontSize: '1.05em',
					lineHeight: 1.35,
					boxShadow: '0 4px 14px rgba(0,0,0,.06)',
				}}
			>
				<TypingText text={text} at={at} paste={paste} color={THEME.palette.ink} />
				{paste ? <KeyBadge label="⌘V" at={at} size="1.3em" /> : null}
				<div style={{position: 'absolute', right: '0.7em', bottom: '0.6em', width: '1.6em', height: '1.6em', borderRadius: 8, background: THEME.palette.orange}} />
			</div>
		</div>
	</div>
);
