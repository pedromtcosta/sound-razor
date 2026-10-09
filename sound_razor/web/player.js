// One AudioContext clock and one scheduled start time for every stem.
export class StemPlayer {
  constructor() {
    this.context = null;
    this.master = null;
    this.tracks = [];
    this.nodes = [];
    this.offset = 0;
    this.startedAt = 0;
    this.playing = false;
    this.starting = false;
    this.duration = 0;
    this.volume = 0.7;
    this.playbackRate = 1;
    this.tempoModule = null;
    this.tempoNode = null;
    this.onError = null;
    this.generation = 0;
  }
  ensureContext() {
    if (!this.context) {
      this.context = new AudioContext();
      this.master = this.context.createGain();
      this.master.gain.value = this.volume;
      this.master.connect(this.context.destination);
    }
    return this.context;
  }
  position() {
    return Math.min(this.duration, this.playing
      ? this.offset + Math.max(0, this.context.currentTime - this.startedAt) * this.playbackRate : this.offset);
  }
  stopNodes() {
    for (const {source, gain} of this.nodes) {
      source.onended = null;
      try { source.stop(); } catch (_) { /* Already ended. */ }
      source.disconnect(); gain.disconnect();
    }
    this.nodes = [];
    if (this.tempoNode) {
      this.tempoNode.port.onmessage = null;
      this.tempoNode.onprocessorerror = null;
      this.tempoNode.port.postMessage({type: 'stop'});
      this.tempoNode.disconnect();
      this.tempoNode.port.close();
      this.tempoNode = null;
    }
  }
  pause() {
    this.generation++;
    this.offset = this.position();
    this.playing = false;
    this.starting = false;
    this.stopNodes();
  }
  clear() {
    this.generation++;
    this.pause(); this.offset = 0; this.duration = 0; this.tracks = [];
  }
  setTracks(tracks) {
    this.clear();
    this.tracks = tracks.map(t => ({...t, muted: false, solo: false, volume: 1}));
    this.duration = Math.max(0, ...tracks.map(t => t.buffer.duration));
  }
  async play() {
    if (!this.tracks.length || this.playing || this.starting) return;
    const generation = this.generation;
    const context = this.ensureContext();
    this.starting = true;
    try {
      await context.resume();
      if (generation !== this.generation) return;
      if (this.playbackRate !== 1) await this.loadTempoModule(context);
      if (generation !== this.generation) return;
      if (this.offset >= this.duration) this.offset = 0;
      const startTime = context.currentTime + 0.06;
      this.startedAt = startTime;
      let destination = this.master;
      if (this.playbackRate !== 1) {
        this.tempoNode = new AudioWorkletNode(context, 'sound-razor-tempo', {
          numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [2],
          parameterData: {playbackRate: this.playbackRate},
          processorOptions: {startTime, duration: this.duration - this.offset, playbackRate: this.playbackRate},
        });
        this.tempoNode.connect(this.master);
        destination = this.tempoNode;
        // The processor reports rendered audio, including its initial buffering delay.
        this.startedAt = Infinity;
        this.tempoNode.port.onmessage = ({data}) => {
          if (generation !== this.generation || !this.playing) return;
          if (data.type === 'position') this.startedAt = data.at - data.elapsed / this.playbackRate;
          if (data.type === 'ended') { this.pause(); this.offset = 0; }
        };
        this.tempoNode.onprocessorerror = () => {
          if (generation !== this.generation) return;
          this.pause();
          this.onError?.(new Error('Slower playback stopped unexpectedly. Try playing again or choose 100% speed.'));
        };
      }
      this.playing = true;
      for (const track of this.tracks) {
        if (track.buffer.duration <= this.offset) continue;
        const source = context.createBufferSource();
        const gain = context.createGain();
        source.buffer = track.buffer;
        source.playbackRate.value = this.playbackRate;
        source.connect(gain).connect(destination);
        gain.gain.value = this.trackGain(track);
        if (!this.tempoNode && track.buffer.duration === this.duration) {
          source.onended = () => {
            if (generation === this.generation && this.playing && this.position() >= this.duration - 0.02) {
              this.pause(); this.offset = 0;
            }
          };
        }
        this.nodes.push({source, gain, track});
        source.start(startTime, this.offset);
      }
    } catch (error) {
      if (generation !== this.generation) return;
      this.pause();
      throw error;
    } finally {
      if (generation === this.generation) this.starting = false;
    }
  }
  async loadTempoModule(context) {
    if (!context.audioWorklet) throw new Error('Slower playback is unavailable in this browser. Choose 100% speed or use a current browser.');
    if (!this.tempoModule) {
      this.tempoModule = context.audioWorklet.addModule('/tempo-worklet.js').catch(error => {
        this.tempoModule = null;
        throw new Error('Could not prepare slower playback. Try again or choose 100% speed.', {cause: error});
      });
    }
    await this.tempoModule;
  }
  async setPlaybackRate(rate) {
    if (!Number.isFinite(rate) || rate < 0.5 || rate > 1) throw new RangeError('Playback speed must be between 50% and 100%.');
    if (rate === this.playbackRate) return;
    const resume = this.playing || this.starting;
    this.pause();
    this.playbackRate = rate;
    if (resume) await this.play();
  }
  async seek(seconds) {
    const resume = this.playing || this.starting;
    this.pause();
    this.offset = Math.max(0, Math.min(this.duration, seconds));
    if (resume) await this.play();
  }
  trackGain(track) {
    const soloing = this.tracks.some(t => t.solo);
    return track.muted || (soloing && !track.solo) ? 0 : track.volume;
  }
  updateMix() {
    for (const {gain, track} of this.nodes) {
      gain.gain.setTargetAtTime(this.trackGain(track), this.context.currentTime, 0.015);
    }
  }
  setVolume(value) {
    this.volume = value;
    if (this.master) this.master.gain.setTargetAtTime(value, this.context.currentTime, 0.015);
  }
}
