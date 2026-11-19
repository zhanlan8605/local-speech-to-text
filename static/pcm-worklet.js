/* 麦克风 PCM 采集（AudioWorklet）。
 *
 * 输入：AudioContext 的浮点音频（Chrome/Edge 中通常已被浏览器重采样到 16kHz）
 * 输出：16kHz / 16bit / 单声道 PCM 的 ArrayBuffer，按固定帧长投递给主线程
 *
 * 主线程拿到后直接作为 WebSocket 二进制帧发送，不需要任何容器格式。
 */

class PcmCaptureProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const opts = (options && options.processorOptions) || {};
    this.targetRate = opts.targetRate || 16000;
    this.inputRate = opts.inputRate || sampleRate;
    this.frameSize = opts.frameSize || 2048; // 16kHz 下约 128ms
    this.step = this.inputRate / this.targetRate;

    this.rest = new Float32Array(0);
    this.cursor = 0;

    this.frame = new Int16Array(this.frameSize);
    this.filled = 0;
  }

  _emit(sample) {
    let value = sample;
    if (value > 1) value = 1;
    else if (value < -1) value = -1;

    this.frame[this.filled] = Math.round(value < 0 ? value * 32768 : value * 32767);
    this.filled += 1;

    if (this.filled >= this.frameSize) {
      const buffer = this.frame.buffer;
      this.port.postMessage(buffer, [buffer]);
      this.frame = new Int16Array(this.frameSize);
      this.filled = 0;
    }
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || input.length === 0) return true;
    const channel = input[0];
    if (!channel || channel.length === 0) return true;

    // 已经是目标采样率：直接逐样本转换
    if (Math.abs(this.step - 1) < 1e-6) {
      for (let i = 0; i < channel.length; i += 1) this._emit(channel[i]);
      return true;
    }

    // 其他采样率：保留上一帧尾部，做线性插值重采样
    const merged = new Float32Array(this.rest.length + channel.length);
    merged.set(this.rest, 0);
    merged.set(channel, this.rest.length);

    let index = this.cursor;
    while (index < merged.length - 1) {
      const i0 = Math.floor(index);
      const frac = index - i0;
      const sample = merged[i0] * (1 - frac) + merged[i0 + 1] * frac;
      this._emit(sample);
      index += this.step;
    }

    const keepFrom = Math.min(Math.floor(index), merged.length - 1);
    this.rest = merged.slice(keepFrom);
    this.cursor = index - keepFrom;
    return true;
  }
}

registerProcessor("pcm-capture", PcmCaptureProcessor);
