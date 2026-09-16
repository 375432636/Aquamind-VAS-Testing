// Same mono PCM capture boundary as Console, with explicit start and tail acknowledgement.
class Capture extends AudioWorkletProcessor {
    constructor() {
        super();
        this.active = false;
        this.pcm = new Int16Array(960);
        this.n = 0;
        this.port.onmessage = ({ data }) => {
            if (data === 'start') {
                this.pcm = new Int16Array(960);
                this.n = 0;
                this.active = true;
            }
            else if (data === 'stop') {
                this.active = false;
                if (this.n)
                    this.port.postMessage(this.pcm);
                this.n = 0;
                this.port.postMessage('stopped');
            }
        };
    }
    process(inputs) {
        if (!this.active)
            return true;
        const channel = inputs[0]?.[0];
        if (channel)
            for (const sample of channel) {
                this.pcm[this.n++] = Math.max(-32768, Math.min(32767, Math.round(sample * 32767)));
                if (this.n === 960) {
                    this.port.postMessage(this.pcm);
                    this.pcm = new Int16Array(960);
                    this.n = 0;
                }
            }
        return true;
    }
}
registerProcessor('capture', Capture);
