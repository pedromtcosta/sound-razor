import {SoundTouchProcessor} from './vendor/soundtouch/processor.js';

// All stems enter one processor so time stretching cannot move them apart.
class MixTempoProcessor extends SoundTouchProcessor {
  constructor(options) {
    super(options);
    const {startTime, duration, playbackRate} = options.processorOptions;
    this.startFrame = Math.ceil(startTime * sampleRate);
    this.rate = playbackRate;
    this.totalFrames = Math.ceil(duration * sampleRate / playbackRate);
    this.renderedFrames = 0;
    this.nextReport = 0;
    this.stopped = false;
    this.silence = new Float32Array(128);
    this.port.onmessage = ({data}) => { if (data.type === 'stop') this.stopped = true; };
  }

  process(inputs, outputs, parameters) {
    if (this.stopped) return false;
    const output = outputs[0];
    const blockSize = output[0].length;
    const start = Math.max(0, this.startFrame - currentFrame);
    if (start >= blockSize) return true;
    const count = blockSize - start;
    if (this.silence.length < blockSize) this.silence = new Float32Array(blockSize);
    const input = inputs[0];
    // Keep feeding silence after the sources end to drain the buffered audio.
    const left = input?.[0] ?? this.silence;
    const right = input?.[1] ?? left;
    const result = this.processCore(
      [[left.subarray(start, start + count), right.subarray(start, start + count)]],
      [output.map(channel => channel.subarray(start, start + count))], parameters,
    );
    const rendered = Math.min(result.toExtract, this.totalFrames - this.renderedFrames);
    if (rendered < result.toExtract) output.forEach(channel => channel.fill(0, start + rendered));
    this.renderedFrames += rendered;
    if (rendered && (this.renderedFrames >= this.nextReport || rendered < count)) {
      this.port.postMessage({
        type: 'position', elapsed: this.renderedFrames * this.rate / sampleRate,
        at: (currentFrame + start + rendered) / sampleRate,
      });
      this.nextReport = this.renderedFrames + sampleRate / 20;
    }
    if (this.renderedFrames >= this.totalFrames) {
      this.port.postMessage({type: 'ended'});
      return false;
    }
    return true;
  }
}

registerProcessor('sound-razor-tempo', MixTempoProcessor);
