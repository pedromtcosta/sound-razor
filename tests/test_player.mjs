// Run with: node --test tests/test_player.mjs (no npm dependencies).
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import vm from 'node:vm';

const web = new URL('../sound_razor/web/', import.meta.url);
const playerSource = readFileSync(new URL('player.js', web), 'utf8');
const {StemPlayer} = await import(`data:text/javascript;base64,${Buffer.from(playerSource).toString('base64')}`);

class AudioParam {
  value = 1;
  setTargetAtTime(value) { this.value = value; }
}
class AudioNode {
  connect(destination) { this.destination = destination; return destination; }
  disconnect() { this.disconnected = true; }
}
class BufferSource extends AudioNode {
  playbackRate = new AudioParam();
  start(time, offset) { this.time = time; this.offset = offset; }
  stop() { this.stopped = true; }
}
class Gain extends AudioNode { gain = new AudioParam(); }
class TestAudioContext {
  currentTime = 0;
  destination = {};
  loads = 0;
  audioWorklet = {addModule: async () => { this.loads++; }};
  async resume() {}
  createGain() { return new Gain(); }
  createBufferSource() { return new BufferSource(); }
}
class TestWorkletNode extends AudioNode {
  constructor(context, name, options) {
    super(); this.options = options;
    this.port = {messages: [], postMessage(data) { this.messages.push(data); }, close() { this.closed = true; }};
  }
  report(data) { this.port.onmessage?.({data}); }
}
globalThis.AudioContext = TestAudioContext;
globalThis.AudioWorkletNode = TestWorkletNode;

function player() {
  const instance = new StemPlayer();
  instance.setTracks([{name: 'guitar', buffer: {duration: 12}}, {name: 'bass', buffer: {duration: 6}}]);
  return instance;
}
function near(actual, expected) { assert.ok(Math.abs(actual - expected) < 1e-8, `${actual} != ${expected}`); }

test('slower playback shares one start, rate and pitch processor across all stems', async () => {
  const p = player();
  await p.setPlaybackRate(0.5);
  await p.play();
  assert.equal(p.context.loads, 1);
  assert.equal(p.tempoNode.options.parameterData.playbackRate, 0.5);
  for (const {source, gain} of p.nodes) {
    assert.equal(source.playbackRate.value, 0.5);
    assert.equal(source.time, 0.06);
    assert.equal(source.offset, 0);
    assert.equal(gain.destination, p.tempoNode);
  }
  p.context.currentTime = 0.15;
  assert.equal(p.position(), 0); // Wait for the first rendered audio.
  p.tempoNode.report({type: 'position', elapsed: 0.05, at: 0.2});
  p.context.currentTime = 2.1;
  near(p.position(), 1);
  p.pause();
  p.context.currentTime = 10;
  near(p.position(), 1);
  await p.play();
  near(p.nodes[0].source.offset, 1);
  assert.equal(p.context.loads, 1);
});

test('changing speed preserves the position and mix, including paused changes and reset', async () => {
  const p = player();
  await p.play();
  p.context.currentTime = 4.06;
  p.tracks[0].muted = true;
  p.tracks[1].solo = true;
  p.tracks[1].volume = 0.4;
  const oldSources = p.nodes.map(node => node.source);
  await p.setPlaybackRate(0.75);
  near(p.position(), 4);
  assert.ok(oldSources.every(source => source.stopped && source.disconnected));
  for (const node of p.nodes) near(node.source.offset, 4);
  assert.equal(p.nodes[0].gain.gain.value, 0);
  assert.equal(p.nodes[1].gain.gain.value, 0.4);
  p.pause();
  await p.setPlaybackRate(0.5);
  assert.equal(p.playing, false);
  near(p.position(), 4);
  await p.play();
  await p.setPlaybackRate(1);
  near(p.position(), 4);
  assert.equal(p.tempoNode, null);
  assert.equal(p.nodes[0].source.playbackRate.value, 1);
});

test('seek skips exhausted short stems, ignores old callbacks and ends cleanly', async () => {
  const p = player();
  await p.setPlaybackRate(0.5);
  await p.play();
  const oldNode = p.tempoNode;
  const staleMessage = oldNode.port.onmessage;
  await p.seek(8);
  assert.equal(p.nodes.length, 1);
  assert.equal(p.nodes[0].source.offset, 8);
  assert.equal(p.tempoNode.options.processorOptions.duration, 4);
  assert.ok(oldNode.disconnected && oldNode.port.closed);
  assert.deepEqual(oldNode.port.messages, [{type: 'stop'}]);
  staleMessage({data: {type: 'ended'}});
  assert.equal(p.playing, true);
  p.tempoNode.report({type: 'ended'});
  assert.equal(p.playing, false);
  assert.equal(p.position(), 0);
  assert.equal(p.nodes.length, 0);
  await p.play();
  assert.equal(p.nodes[0].source.offset, 0);
});

test('a rate change during preparation uses the latest rate; clearing cancels preparation', async () => {
  const p = player();
  const context = p.ensureContext();
  let ready;
  context.audioWorklet.addModule = () => new Promise(resolve => { ready = resolve; });
  await p.setPlaybackRate(0.5);
  const first = p.play();
  await Promise.resolve();
  assert.equal(p.starting, true);
  const second = p.setPlaybackRate(0.75);
  ready();
  await Promise.all([first, second]);
  assert.equal(p.nodes.length, 2);
  assert.ok(p.nodes.every(({source}) => source.playbackRate.value === 0.75));
  p.clear();
  p.setTracks([{buffer: {duration: 5}}]);
  let resumed;
  context.resume = () => new Promise(resolve => { resumed = resolve; });
  const pending = p.play();
  p.clear(); resumed(); await pending;
  assert.equal(p.playing, false);
  assert.equal(p.starting, false);
  assert.equal(p.nodes.length, 0);
});

test('processor loading failures leave playback stopped and normal speed usable', async () => {
  const p = player();
  p.ensureContext().audioWorklet.addModule = async () => { throw new Error('Module unavailable'); };
  await p.setPlaybackRate(0.5);
  await assert.rejects(p.play(), /Could not prepare slower playback/);
  assert.equal(p.playing, false);
  assert.equal(p.starting, false);
  assert.equal(p.nodes.length, 0);
  assert.equal(p.tempoModule, null);
  await p.setPlaybackRate(1);
  await p.play();
  assert.equal(p.playing, true);
  for (const invalid of [0, -1, 0.49, 1.01, NaN, Infinity, '0.5']) {
    await assert.rejects(p.setPlaybackRate(invalid), RangeError);
  }
  assert.equal(p.playbackRate, 1);
});

function processor(sampleRate, rate, duration) {
  const registry = new Map();
  const scope = vm.createContext({
    sampleRate, currentFrame: 0, console,
    AudioWorkletProcessor: class {
      port = {messages: [], postMessage(data) { this.messages.push(data); }};
    },
    registerProcessor(name, type) { registry.set(name, type); },
  });
  // Execute the actual bundled DSP and worklet; only module loading is adapted for Node.
  vm.runInContext(readFileSync(new URL('vendor/soundtouch/processor.js', web), 'utf8')
    .replace('export { SoundTouchProcessor };', ''), scope);
  vm.runInContext(readFileSync(new URL('tempo-worklet.js', web), 'utf8').replace(/^import[^\n]+\n/, ''), scope);
  const Processor = registry.get('sound-razor-tempo');
  return {scope, instance: new Processor({processorOptions: {startTime: 0.06, duration, playbackRate: rate}})};
}

for (const sampleRate of [44100, 48000]) {
  for (const rate of [0.5, 0.75, 0.95]) {
    test(`actual DSP keeps a 440 Hz tone and stereo phase at ${rate * 100}% / ${sampleRate} Hz`, () => {
      const duration = 0.6;
      const {scope, instance} = processor(sampleRate, rate, duration);
      const parameters = {pitch: [1], pitchSemitones: [0], playbackRate: [rate]};
      const audio = [];
      const startFrame = Math.ceil(0.06 * sampleRate);
      const sourceEnd = startFrame + Math.ceil(duration / rate * sampleRate);
      let active = true;
      while (active && scope.currentFrame < sampleRate * 3) {
        const left = new Float32Array(128), right = new Float32Array(128);
        for (let i = 0; i < 128; i++) {
          const frame = scope.currentFrame + i;
          if (frame >= startFrame && frame < sourceEnd) {
            left[i] = 0.25 * Math.sin(2 * Math.PI * 440 * rate * (frame - startFrame) / sampleRate);
            right[i] = -left[i];
          }
        }
        const output = [new Float32Array(128), new Float32Array(128)];
        active = instance.process([scope.currentFrame < sourceEnd ? [left, right] : []], [output], parameters);
        for (let i = 0; i < 128; i++) {
          assert.ok(Number.isFinite(output[0][i]));
          assert.ok(Math.abs(output[0][i] + output[1][i]) < 1e-6, 'Stereo timing changed');
          audio.push(output[0][i]);
        }
        scope.currentFrame += 128;
      }
      assert.equal(active, false, 'The processor must finish after draining the audio');
      assert.equal(instance.port.messages.at(-1).type, 'ended');
      const firstSound = audio.findIndex(value => Math.abs(value) > 0.001);
      assert.ok(firstSound >= startFrame, 'Playback must not begin before its scheduled start');
      assert.ok(audio.slice(sourceEnd).some(value => Math.abs(value) > 0.01), 'Buffered tail was cut off');
      const from = firstSound + Math.round(sampleRate * 0.15);
      const to = from + Math.round(sampleRate * 0.25);
      let crossings = 0;
      for (let i = from + 1; i < to; i++) if (audio[i - 1] <= 0 && audio[i] > 0) crossings++;
      const frequency = crossings * sampleRate / (to - from);
      assert.ok(Math.abs(frequency - 440) <= 5, `Expected 440 Hz, got ${frequency} Hz`);
      const playedDuration = (audio.length - firstSound) / sampleRate;
      assert.ok(Math.abs(playedDuration - duration / rate) < 0.04, `Unexpected duration: ${playedDuration}`);
    });
  }
}

test('a stopped worklet releases playback without emitting more audio', () => {
  const {instance} = processor(44100, 0.5, 10);
  instance.port.onmessage({data: {type: 'stop'}});
  assert.equal(instance.process([], [], {}), false);
});

test('seeking to the last millisecond finishes without a long buffering stall', () => {
  const sampleRate = 44100;
  const {scope, instance} = processor(sampleRate, 0.5, 0.001);
  const parameters = {pitch: [1], pitchSemitones: [0], playbackRate: [0.5]};
  let active = true;
  while (active && scope.currentFrame < sampleRate / 2) {
    active = instance.process([[]], [[new Float32Array(128), new Float32Array(128)]], parameters);
    scope.currentFrame += 128;
  }
  assert.equal(active, false);
  assert.equal(instance.port.messages.at(-1).type, 'ended');
});
