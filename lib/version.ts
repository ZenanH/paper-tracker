import metadata from '../package.json' with { type: 'json' };

// Package managers use three components; product and image tags use 1.x.
export const APP_VERSION = metadata.version.replace(/\.0$/, '');
