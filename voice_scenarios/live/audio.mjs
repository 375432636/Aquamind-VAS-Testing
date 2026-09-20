// Console's AudioWorklet → libopus recording and continuous Web Audio scheduling,
// extracted from React lifecycle for the independent testing page. No media relay.
import { OpusEncoder, OpusDecoder } from './opus.mjs';
export const ns = () => Math.round(performance.now() * 1e6);
export const encodePCM = pcm => {
    const bytes = new Uint8Array(pcm.buffer, pcm.byteOffset, pcm.byteLength);
    let s = '';
    for (let i = 0; i < bytes.length; i++)
        s += String.fromCharCode(bytes[i]);
    return btoa(s);
};
export class Recorder {
    constructor(onFrame) { this.onFrame = onFrame; this.offset = 0; }
    async prepare(deviceId) {
        try {
            this.encoder = new OpusEncoder();
            if (!await this.encoder.init())
                throw Error('Opus 编码器初始化失败');
            this.stream = await navigator.mediaDevices.getUserMedia({ audio: { sampleRate: 16000, channelCount: 1, echoCancellation: true, noiseSuppression: true, ...(deviceId ? { deviceId: { exact: deviceId } } : {}) } });
            this.ctx = new AudioContext({ sampleRate: 16000 });
            await this.ctx.resume();
            if (this.ctx.sampleRate !== 16000)
                throw Error('浏览器未提供 16 kHz 录音，请使用 Chrome');
            await this.ctx.audioWorklet.addModule('/live/assets/capture-worklet.js?v=4');
            this.node = new AudioWorkletNode(this.ctx, 'capture');
            this.offset = 0;
            this.node.port.onmessage = e => {
                if (e.data === 'stopped') {
                    this.stopped?.();
                    return;
                }
                const pcm = e.data;
                const encoded = this.encoder.encode(pcm);
                if (encoded) {
                    this.onFrame(pcm, encoded, this.offset);
                    this.offset += pcm.length;
                }
            };
            this.source = this.ctx.createMediaStreamSource(this.stream);
            this.gain = this.ctx.createGain();
            this.gain.gain.value = 0;
            this.source.connect(this.node);
            this.node.connect(this.gain);
            this.gain.connect(this.ctx.destination);
        }
        catch (e) {
            this.stream?.getTracks().forEach(t => t.stop());
            await this.ctx?.close();
            this.encoder?.destroy();
            this.node = null;
            throw e;
        }
    }
    async start() {
        if (!this.node)
            throw Error('麦克风尚未就绪');
        this.offset = 0;
        this.node.port.postMessage('start');
    }
    async stop() {
        if (!this.node)
            return;
        try {
            await new Promise((resolve, reject) => { const timer = setTimeout(() => reject(Error('麦克风尾帧未确认')), 2000); this.stopped = () => { clearTimeout(timer); resolve(); }; this.node.port.postMessage('stop'); });
        }
        finally {
            this.node.disconnect();
            this.source.disconnect();
            this.stream.getTracks().forEach(t => t.stop());
            await this.ctx.close();
            this.encoder.destroy();
            this.node = null;
        }
    }
}
export class Player {
    constructor(onDecoded, onPlayed, onDrain, onState) { Object.assign(this, { onDecoded, onPlayed, onDrain, onState }); this.queue = []; this.active = new Set(); this.pendingPlayback = new Set(); this.cursor = 0; this.flushing = false; this.chain = Promise.resolve(); this.epoch = 0; }
    async prime() { this.ctx ||= new AudioContext({ latencyHint: 'interactive' }); await this.ctx.resume(); this.clockOffset = undefined; }
    async format(rate) {
        if (![8000, 12000, 16000, 24000, 48000].includes(rate))
            throw Error('不支持的播放采样率');
        this.decoder?.destroy();
        this.rate = rate;
        this.decoder = new OpusDecoder({ sampleRate: rate, channels: 1, frameSize: rate * .12 });
        if (!await this.decoder.init())
            throw Error('Opus 解码器初始化失败');
    }
    feed(bytes, meta) {
        const epoch = this.epoch;
        this.chain = this.chain.then(() => {
            if (epoch !== this.epoch)
                return;
            const pcm = this.decoder?.decode(bytes);
            if (!pcm?.length)
                throw Error('收到无法解码的音频');
            this.onDecoded(pcm, meta);
            this.queue.push({ pcm: pcm.slice(), meta });
            this.flushing = false;
            this.pump();
        });
        return this.chain;
    }
    readClock() {
        const observed = performance.now(), context = this.ctx.currentTime;
        const stamp = this.ctx.getOutputTimestamp?.();
        const quantum = 128 / (this.ctx.sampleRate || 48000) * 1000;
        const valid = Number.isFinite(stamp?.performanceTime) && stamp.performanceTime > 0
            && Number.isFinite(stamp.contextTime) && stamp.contextTime >= 0
            && stamp.contextTime <= context + quantum / 1000
            && stamp.performanceTime <= observed + quantum
            && observed - stamp.performanceTime < 1000;
        return { observed, context, stamp, quantum, valid,
            offset: valid ? stamp.performanceTime - stamp.contextTime * 1000 : null };
    }
    wallStart(time, earliest = 0, sample = this.readClock()) {
        // AudioContext can stall/suspend independently of performance.now(). A
        // lifetime offset moves later replies into the past. Re-sample the
        // output clock, retaining only sub-render-quantum jitter within a burst.
        const { observed, context, stamp, quantum } = sample;
        const candidate = stamp?.performanceTime + (time - stamp?.contextTime) * 1000;
        const outputValid = sample.valid
            && candidate * 1e6 >= earliest
            && (time < context || candidate >= observed - quantum);
        const offset = outputValid
            ? stamp.performanceTime - stamp.contextTime * 1000
            : observed - context * 1000 + (this.ctx.outputLatency || 0) * 1000;
        const method = outputValid ? 'output_timestamp' : 'context_clock_estimate';
        if (this.clockOffset === undefined || this.clockMethod !== method
            || Math.abs(offset - this.clockOffset) > quantum * 2
            || (this.clockOffset + time * 1000) * 1e6 < earliest) {
            this.clockOffset = offset;
        }
        this.clockMethod = method;
        this.clockEvidence = {
            timing_model: 'browser_audio_context_v2', timing_method: method,
            clock_observed_at_ns: Math.round(observed * 1e6),
            audio_context_time: context, scheduled_context_time: time,
            output_context_time: stamp?.contextTime ?? null,
            output_performance_time: stamp?.performanceTime ?? null,
            clock_tolerance_ms: quantum * 2,
        };
        return Math.round((this.clockOffset + time * 1000) * 1e6);
    }
    observePlayback(force = false) {
        if (!this.active.size && !this.pendingPlayback.size)
            return;
        const sample = this.readClock();
        const entries = [...this.active, ...this.pendingPlayback].sort((a, b) => a.start - b.start);
        for (const entry of entries) {
            const end = entry.start + (entry.count ?? entry.pcm.length) / entry.rate;
            if (!entry.outputEndConfirmed) {
                const startPassed = sample.valid && sample.stamp.contextTime + 1e-9 >= entry.start;
                // A discontinuity entirely before the frame is harmless. Across
                // its start or duration we cannot locate the pause within PCM.
                if (entry.playback || startPassed) {
                    if (!sample.valid || !entry.lastClock?.valid)
                        entry.clockIssue ||= 'output_clock_unavailable';
                    else if (Math.abs(sample.offset - entry.lastClock.offset) > sample.quantum * 2)
                        entry.clockIssue ||= 'clock_discontinuity';
                }
                if (!entry.playback && (startPassed || force || (entry.count !== undefined && !sample.valid))) {
                    const at = this.wallStart(entry.start, entry.meta.received || 0, sample);
                    const confirmed = startPassed && at <= Math.round(sample.observed * 1e6);
                    if (confirmed || force || !sample.valid) {
                        entry.playback = { at, ...this.clockEvidence, output_start_confirmed: confirmed };
                        if (!confirmed)
                            entry.clockIssue ||= 'output_unconfirmed';
                        const previous = this.lastPlayback;
                        if (previous && entry.start >= previous.start
                            && at < previous.playback.at + (previous.count ?? previous.pcm.length) / previous.rate * 1e9 - 1)
                            entry.clockIssue ||= 'overlapping_output_mapping';
                        this.lastPlayback = entry;
                    }
                }
                // onended describes render completion. In particular, Bluetooth
                // output can still be playing an earlier frame at this point.
                const endAt = sample.valid ? sample.stamp.performanceTime + (end - sample.stamp.contextTime) * 1000 : Infinity;
                if (entry.playback && sample.valid && sample.stamp.contextTime + 1e-9 >= end && endAt <= sample.observed + 1e-6) {
                    entry.outputEndConfirmed = true;
                    entry.endObserved = Math.round(sample.observed * 1e6);
                }
                entry.lastClock = sample;
            }
            if (entry.count === undefined || !entry.playback || (!entry.outputEndConfirmed && !force && sample.valid))
                continue;
            if (!entry.outputEndConfirmed)
                entry.clockIssue ||= 'output_unconfirmed';
            this.pendingPlayback.delete(entry);
            this.onPlayed(entry.pcm.slice(0, entry.count), {
                ...entry.meta, ...entry.playback,
                ...(entry.clockIssue ? { timing_method: 'context_clock_estimate', clock_issue: entry.clockIssue } : {}),
                output_end_confirmed: Boolean(entry.outputEndConfirmed),
                playback_end_observed_at_ns: entry.endObserved ?? null,
                scheduled_at_ns: entry.at,
                playback_observed_at_ns: ns(),
            });
        }
    }
    pump() {
        clearTimeout(this.timer);
        this.observePlayback();
        // Start on the first decoded packet, including after an interruption or
        // underrun. The 20 ms scheduling lead below covers small arrival jitter;
        // subsequent packets keep their contiguous sample boundaries.
        while (this.queue.length && this.cursor - this.ctx.currentTime < 1) {
            const packet = this.queue.shift();
            const start = Math.max(this.cursor, this.ctx.currentTime + (this.cursor > this.ctx.currentTime ? 0 : .02));
            if (this.cursor && start - this.cursor > .02)
                this.onState?.('playback_underrun', { gap_seconds: start - this.cursor });
            const buffer = this.ctx.createBuffer(1, packet.pcm.length, this.rate);
            const floats = new Float32Array(packet.pcm.length);
            for (let i = 0; i < floats.length; i++)
                floats[i] = packet.pcm[i] / 32768;
            buffer.copyToChannel(floats, 0);
            const node = this.ctx.createBufferSource();
            node.buffer = buffer;
            node.connect(this.ctx.destination);
            if (!this.active.size) this.clockOffset = undefined;
            const sample = this.readClock();
            const at = this.wallStart(start, packet.meta.received || 0, sample);
            const entry = { node, start, at, rate: this.rate, lastClock: sample, ...packet };
            this.active.add(entry);
            this.owner = packet.meta.turn;
            node.onended = () => this.commit(entry, packet.pcm.length);
            node.start(start);
            this.cursor = start + buffer.duration;
        }
        if (this.flushing && !this.active.size && !this.pendingPlayback.size && !this.queue.length) {
            this.flushing = false;
            this.cursor = 0;
            this.onDrain(this.owner);
            return;
        }
        this.timer = setTimeout(() => this.pump(), 20);
    }
    commit(entry, count) {
        if (!this.active.delete(entry))
            return;
        entry.node.onended = null;
        entry.node.disconnect();
        if (count > 0) {
            entry.count = count;
            this.pendingPlayback.add(entry);
            this.observePlayback();
        }
    }
    async flush(owner) { await this.chain; this.owner = owner; this.flushing = true; this.pump(); }
    stop() {
        this.epoch++;
        clearTimeout(this.timer);
        this.observePlayback();
        for (const entry of [...this.active]) {
            const count = Math.max(0, Math.min(entry.pcm.length, Math.floor((this.ctx.currentTime - entry.start) * entry.rate)));
            entry.node.onended = null;
            entry.node.stop();
            this.commit(entry, count);
        }
        // Preserve interrupted PCM, but do not call unobserved device output exact.
        this.observePlayback(true);
        this.queue = [];
        this.cursor = 0;
        this.flushing = false;
    }
    get busy() { return Boolean(this.queue.length || this.active.size || this.pendingPlayback.size); }
}
