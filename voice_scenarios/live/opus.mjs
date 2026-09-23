// Adapted from Aquamind Console utils/opus: same libopus encoder/decoder.
/**
 * Opus Encoder/Decoder wrapper for WebAssembly libopus
 * Based on reference implementation from aquamindtestpage.html
 */
import { AUDIO_CONFIG } from './audio_config.mjs';
let opusLoaded = false;
let loadPromise = null;
/**
 * Load the Opus WASM library
 */
export async function loadOpusLibrary() {
    if (opusLoaded && window.ModuleInstance) {
        return true;
    }
    if (loadPromise) {
        return loadPromise;
    }
    loadPromise = new Promise((resolve) => {
        // Check if already loaded
        if (window.ModuleInstance) {
            opusLoaded = true;
            resolve(true);
            return;
        }
        // Check Module.instance (libopus.js export style)
        if (window.Module?.instance && typeof window.Module.instance._opus_decoder_get_size === 'function') {
            window.ModuleInstance = window.Module.instance;
            opusLoaded = true;
            resolve(true);
            return;
        }
        // Check global Module
        if (window.Module && typeof window.Module._opus_decoder_get_size === 'function') {
            window.ModuleInstance = window.Module;
            opusLoaded = true;
            resolve(true);
            return;
        }
        // Load script dynamically
        const script = document.createElement('script');
        // Static files in public/ are served from root, regardless of app base path
        script.src = '/live/assets/libopus.js';
        script.async = true;
        script.onload = () => {
            // Wait a bit for Module to initialize
            setTimeout(() => {
                if (window.Module?.instance) {
                    window.ModuleInstance = window.Module.instance;
                    opusLoaded = true;
                    resolve(true);
                }
                else if (window.Module && typeof window.Module._opus_decoder_get_size === 'function') {
                    window.ModuleInstance = window.Module;
                    opusLoaded = true;
                    resolve(true);
                }
                else {
                    console.error('[Opus] Failed to initialize after script load');
                    resolve(false);
                }
            }, 100);
        };
        script.onerror = () => {
            console.error('[Opus] Failed to load libopus.js');
            resolve(false);
        };
        document.head.appendChild(script);
    });
    return loadPromise;
}
/**
 * Opus Encoder class
 */
export class OpusEncoder {
    mod = null;
    encoderPtr = 0;
    sampleRate = AUDIO_CONFIG.sampleRate;
    channels = AUDIO_CONFIG.channels;
    frameSize = AUDIO_CONFIG.frameSize;
    maxPacketSize = 4000;
    async init() {
        const loaded = await loadOpusLibrary();
        if (!loaded || !window.ModuleInstance) {
            console.error('[OpusEncoder] Library not available');
            return false;
        }
        this.mod = window.ModuleInstance;
        try {
            const encoderSize = this.mod._opus_encoder_get_size(this.channels);
            this.encoderPtr = this.mod._malloc(encoderSize);
            if (!this.encoderPtr) {
                throw new Error('Failed to allocate encoder memory');
            }
            // OPUS_APPLICATION_VOIP = 2048
            const err = this.mod._opus_encoder_init(this.encoderPtr, this.sampleRate, this.channels, 2048);
            if (err < 0) {
                throw new Error(`Encoder init failed: ${err}`);
            }
            // Set bitrate (16kbps) - OPUS_SET_BITRATE = 4002
            this.mod._opus_encoder_ctl(this.encoderPtr, 4002, 16000);
            // Set complexity (5) - OPUS_SET_COMPLEXITY = 4010
            this.mod._opus_encoder_ctl(this.encoderPtr, 4010, 5);
            // Enable DTX - OPUS_SET_DTX = 4016
            this.mod._opus_encoder_ctl(this.encoderPtr, 4016, 1);
            return true;
        }
        catch (error) {
            console.error('[OpusEncoder] Init error:', error);
            this.destroy();
            return false;
        }
    }
    encode(pcmData) {
        if (!this.mod || !this.encoderPtr) {
            console.error('[OpusEncoder] Not initialized');
            return null;
        }
        try {
            // Allocate memory for PCM data
            const pcmPtr = this.mod._malloc(pcmData.length * 2);
            // Copy PCM data to HEAP
            for (let i = 0; i < pcmData.length; i++) {
                this.mod.HEAP16[(pcmPtr >> 1) + i] = pcmData[i];
            }
            // Allocate output buffer
            const outPtr = this.mod._malloc(this.maxPacketSize);
            // Encode
            const encodedLen = this.mod._opus_encode(this.encoderPtr, pcmPtr, this.frameSize, outPtr, this.maxPacketSize);
            if (encodedLen < 0) {
                throw new Error(`Encode failed: ${encodedLen}`);
            }
            // Copy encoded data
            const opusData = new Uint8Array(encodedLen);
            for (let i = 0; i < encodedLen; i++) {
                opusData[i] = this.mod.HEAPU8[outPtr + i];
            }
            // Free memory
            this.mod._free(pcmPtr);
            this.mod._free(outPtr);
            return opusData;
        }
        catch (error) {
            console.error('[OpusEncoder] Encode error:', error);
            return null;
        }
    }
    destroy() {
        if (this.mod && this.encoderPtr) {
            this.mod._free(this.encoderPtr);
            this.encoderPtr = 0;
        }
        this.mod = null;
    }
}
/**
 * Opus Decoder class
 */
export class OpusDecoder {
    mod = null;
    decoderPtr = 0;
    sampleRate;
    channels;
    frameSize;
    constructor(config = AUDIO_CONFIG) {
        this.sampleRate = config.sampleRate;
        this.channels = config.channels;
        this.frameSize = config.frameSize;
    }
    async init() {
        const loaded = await loadOpusLibrary();
        if (!loaded || !window.ModuleInstance) {
            console.error('[OpusDecoder] Library not available');
            return false;
        }
        this.mod = window.ModuleInstance;
        try {
            const decoderSize = this.mod._opus_decoder_get_size(this.channels);
            this.decoderPtr = this.mod._malloc(decoderSize);
            if (!this.decoderPtr) {
                throw new Error('Failed to allocate decoder memory');
            }
            const err = this.mod._opus_decoder_init(this.decoderPtr, this.sampleRate, this.channels);
            if (err < 0) {
                throw new Error(`Decoder init failed: ${err}`);
            }
            return true;
        }
        catch (error) {
            console.error('[OpusDecoder] Init error:', error);
            this.destroy();
            return false;
        }
    }
    decode(opusData) {
        if (!this.mod || !this.decoderPtr) {
            console.error('[OpusDecoder] Not initialized');
            return null;
        }
        try {
            // Allocate memory for Opus data
            const dataPtr = this.mod._malloc(opusData.length);
            for (let i = 0; i < opusData.length; i++) {
                this.mod.HEAPU8[dataPtr + i] = opusData[i];
            }
            // Allocate output buffer for PCM
            const pcmPtr = this.mod._malloc(this.frameSize * this.channels * 2);
            // Decode
            const decodedSamples = this.mod._opus_decode(this.decoderPtr, dataPtr, opusData.length, pcmPtr, this.frameSize, 0 // no FEC
            );
            if (decodedSamples < 0) {
                throw new Error(`Decode failed: ${decodedSamples}`);
            }
            // Copy decoded PCM data
            const pcmData = new Int16Array(decodedSamples * this.channels);
            for (let i = 0; i < pcmData.length; i++) {
                pcmData[i] = this.mod.HEAP16[(pcmPtr >> 1) + i];
            }
            // Free memory
            this.mod._free(dataPtr);
            this.mod._free(pcmPtr);
            return pcmData;
        }
        catch (error) {
            console.error('[OpusDecoder] Decode error:', error);
            return null;
        }
    }
    destroy() {
        if (this.mod && this.decoderPtr) {
            this.mod._free(this.decoderPtr);
            this.decoderPtr = 0;
        }
        this.mod = null;
    }
}
/**
 * Convert Int16 PCM to Float32 for Web Audio API
 */
export function int16ToFloat32(int16Data) {
    const float32Data = new Float32Array(int16Data.length);
    for (let i = 0; i < int16Data.length; i++) {
        float32Data[i] = int16Data[i] / (int16Data[i] < 0 ? 0x8000 : 0x7fff);
    }
    return float32Data;
}
/**
 * Convert Float32 to Int16 PCM
 */
export function float32ToInt16(float32Data) {
    const int16Data = new Int16Array(float32Data.length);
    for (let i = 0; i < float32Data.length; i++) {
        const s = Math.max(-1, Math.min(1, float32Data[i]));
        int16Data[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }
    return int16Data;
}
