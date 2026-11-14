import {Config} from '@remotion/cli/config';

// fonts, stickers, brand images and sfx live in the repo's assets/ folder, shared with the Python pipeline
Config.setPublicDir('../assets');
Config.setVideoImageFormat('jpeg');
Config.setOverwriteOutput(true);
