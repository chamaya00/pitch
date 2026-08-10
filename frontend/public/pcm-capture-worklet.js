/**
 * PCM capture worklet.
 *
 * The only job here is to hand the main thread contiguous mono audio. It does
 * no analysis: pitch detection runs on the main thread over the same samples,
 * so there is exactly one copy of the audio and the recording you play back is
 * the recording that was measured.
 *
 * Blocks are batched before posting. The audio thread delivers 128 frames at a
 * time — around 375 messages a second at 48 kHz — which is enough postMessage
 * traffic to make the display stutter on its own. Batching to ~2048 frames
 * brings that to roughly 23 messages a second, still fine-grained enough for a
 * live readout.
 *
 * Plain JavaScript because `audioWorklet.addModule` loads this file directly;
 * it is not part of the bundle and is not transpiled.
 */

const BATCH_FRAMES = 2048;

class PcmCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._buffer = new Float32Array(BATCH_FRAMES);
    this._filled = 0;
    this._running = true;
    this.port.onmessage = (event) => {
      if (event.data === "stop") {
        this._flush();
        this._running = false;
      }
    };
  }

  _flush() {
    if (this._filled === 0) return;
    const block = this._buffer.slice(0, this._filled);
    // Transferred, not copied: the buffer is handed over and a fresh one is
    // allocated, so no audio-thread allocation is shared with the main thread.
    this.port.postMessage(block, [block.buffer]);
    this._filled = 0;
  }

  process(inputs) {
    if (!this._running) return false;

    const input = inputs[0];
    if (!input || input.length === 0) return true;

    // Mono: the graph upstream already mixes to one channel, and taking
    // channel 0 of a stereo device is a defined fallback rather than a guess.
    const channel = input[0];
    if (!channel) return true;

    for (let i = 0; i < channel.length; i++) {
      this._buffer[this._filled++] = channel[i];
      if (this._filled === BATCH_FRAMES) this._flush();
    }

    return true;
  }
}

registerProcessor("pcm-capture", PcmCaptureProcessor);
