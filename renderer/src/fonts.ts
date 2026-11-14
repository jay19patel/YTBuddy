import {loadFont} from '@remotion/fonts';
import {staticFile} from 'remotion';
import {THEME} from './theme';

// Local OFL fonts from assets/fonts: no network at render time, same glyphs every run.
export const fontsReady = Promise.all([
	loadFont({family: THEME.fonts.sans, url: staticFile('fonts/InterTight.ttf'), weight: '100 900'}),
	loadFont({family: THEME.fonts.serif, url: staticFile('fonts/PlayfairDisplay-Italic.ttf'), weight: '400 900', style: 'italic'}),
	loadFont({family: THEME.fonts.display, url: staticFile('fonts/BebasNeue-Regular.ttf')}),
]);
