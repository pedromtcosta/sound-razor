SoundTouchJS by Steve "Cutter" Blades, based on SoundTouch by Olli Parviainen.

Source: https://github.com/cutterbl/SoundTouchJS
Package: @soundtouchjs/audio-worklet 2.1.1, licensed under MPL-2.0 (see LICENSE).
Archive: https://registry.npmjs.org/@soundtouchjs/audio-worklet/-/audio-worklet-2.1.1.tgz
Archive SHA-256: 360c233284a44c30f6a7a20578e48f2011c988c86b737d422bc73da37bf8a24b

processor.js is the package's self-contained .dist/soundtouch-processor.js bundle.
The only local change is an appended SoundTouchProcessor export for the app's
scheduled playback and end-of-stream handling. Its original source map, including
the TypeScript source, is included as soundtouch-processor.js.map.

The bundle includes @soundtouchjs/core, @soundtouchjs/worklet-base and
@soundtouchjs/interpolation-strategy-lanczos from the same project and release.
