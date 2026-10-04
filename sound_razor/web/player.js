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
    this.duration = 0;
    this.volume = 0.7;
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
      ? this.offset + Math.max(0, this.context.currentTime - this.startedAt) : this.offset);
  }
  stopNodes() {
    for (const {source, gain} of this.nodes) {
      source.onended = null;
      try { source.stop(); } catch (_) { /* Already ended. */ }
      source.disconnect(); gain.disconnect();
    }
    this.nodes = [];
  }
  pause() {
    this.generation++;
    this.offset = this.position();
    this.playing = false;
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
    if (!this.tracks.length || this.playing) return;
    const generation = this.generation;
    const context = this.ensureContext();
    await context.resume();
    if (generation !== this.generation || this.playing) return;
    if (this.offset >= this.duration) this.offset = 0;
    this.startedAt = context.currentTime + 0.06;
    this.playing = true;
    const longest = Math.max(...this.tracks.map(t => t.buffer.duration));
    this.nodes = this.tracks.map(track => {
      const source = context.createBufferSource();
      const gain = context.createGain();
      source.buffer = track.buffer;
      source.connect(gain).connect(this.master);
      gain.gain.value = this.trackGain(track);
      if (track.buffer.duration === longest) {
        source.onended = () => {
          if (this.playing && this.position() >= this.duration - 0.02) {
            this.pause(); this.offset = 0;
          }
        };
      }
      source.start(this.startedAt, Math.min(this.offset, track.buffer.duration));
      return {source, gain, track};
    });
  }
  async seek(seconds) {
    const resume = this.playing;
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
