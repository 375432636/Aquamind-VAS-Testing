// Match clock probes before the audio processing promise queue.
export class ClockSync {
    constructor(send, emit, now = () => performance.timeOrigin + performance.now()) {
        this.send = send; this.emit = emit; this.now = now;
        this.pending = new Map(); this.sequence = 0; this.supported = false;
    }
    receive(message, at = this.now()) {
        if (message.type !== 'clock_sync') return false;
        const resolve = this.pending.get(message.request_id);
        if (resolve) resolve({ message, at });
        return true;
    }
    async collect(phase = 'start') {
        const samples = [], deadline = performance.now() + 1000;
        for (let i = 0; i < 5 && performance.now() < deadline; i++) {
            const id = `clock-${++this.sequence}`;
            let timer;
            const reply = new Promise(resolve => {
                this.pending.set(id, resolve);
                timer = setTimeout(() => resolve(null), Math.min(250, Math.max(1, deadline - performance.now())));
            });
            const t1 = this.now();
            try {
                this.send({ type: 'clock_sync', request_id: id });
                const response = await reply;
                if (!response) break;
                this.supported = true;
                samples.push({ t1: String(Math.round(t1 * 1e6)), t2: response.message.server_received_ns,
                    t3: response.message.server_sent_ns, t4: String(Math.round(response.at * 1e6)),
                    phase, server_session_id: response.message.server_session_id });
            } catch { break; }
            finally { clearTimeout(timer); this.pending.delete(id); }
            await new Promise(resolve => setTimeout(resolve, 15));
        }
        this.emit('clock_sync_samples', { samples }, 0);
        return samples;
    }
}
