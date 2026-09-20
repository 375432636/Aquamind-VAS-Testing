import { ClockSync } from './clock_sync.mjs';
import { Recorder, Player, ns, encodePCM } from './audio.mjs?v=5';
const $ = id => document.getElementById(id);
let vas, record, turn = 0, serverListenTurn = 0, owner = 0, pendingOwner = 0, outputActive = false, seq = 0, rate = 16000, recorder, mic = false, auto = false, ending = false, ready = false, active = false, discard = false, chain = Promise.resolve();
let clockSync, clockSyncTask;
let recording = true;
let captureStart = null, captureGeneration = 0, inputSamples = 0, inputPeak = 0, diagnosticsActive = false;
let batch = [], batchSequence = 0, waiters = [], played = new Set(), lastVoice = 0, startedVoice = false, currentMode = 'manual';
let vadTurnHasStt = false, vadResponseStarted = false, vadPendingFrames = [];
const captureFinishedTurns = new Set();
const status = text => $('status').textContent = text;
function error(e) { $('error').hidden = false; $('error').textContent = e.message || String(e); }
function message(role, text) {
    if (!text)
        return;
    $('messages').querySelector?.('.placeholder')?.remove();
    const p = document.createElement('p');
    p.className = 'message ' + role;
    p.textContent = text;
    $('messages').append(p);
    p.scrollIntoView({ block: 'nearest' });
}
function emit(event, data = {}, index = turn, at = ns()) {
    if (!recording)
        return;
    batch.push({ event, data, turn: index, at_ns: at, wall_time_ns: Math.round((performance.timeOrigin + at / 1e6) * 1e6) });
    if (batch.length >= 20)
        flush();
}
function flush() {
    if (batch.length && record?.readyState === 1) {
        record.send(JSON.stringify({ sequence: ++batchSequence, events: batch }));
        batch = [];
    }
}
setInterval(flush, 100);
function send(value) {
    if (vas?.readyState !== 1)
        throw Error('VAS 连接已关闭');
    vas.send(JSON.stringify(value));
}
function waitFor(test, ms = 15000) { return new Promise((resolve, reject) => { const entry = { test, resolve, reject }; entry.timer = setTimeout(() => { waiters = waiters.filter(x => x !== entry); reject(Error('等待服务器确认超时')); }, ms); waiters.push(entry); }); }
function notify(m) {
    for (const w of [...waiters])
        if (w.test(m)) {
            clearTimeout(w.timer);
            waiters = waiters.filter(x => x !== w);
            w.resolve(m);
        }
}
function controls(on) {
    ready = on;
    for (const id of ['text', 'send', 'talk', 'interrupt', 'finish'])
        $(id).disabled = !on;
    $('connect').disabled = on;
    for (const id of ['environment', 'mac', 'token', 'ota', 'recording'])
        $(id).disabled = on;
}
function recordingControls() {
    const enabled = $('recording').checked;
    $('connect').textContent = enabled ? '连接并开始记录' : '直接连接对话';
    $('finish').textContent = enabled ? '结束并生成报告' : '断开连接';
    $('clock-sync').disabled = !enabled;
}
function finishInputCapture(index = turn) {
    if (!index || currentMode === 'text' || captureFinishedTurns.has(index))
        return;
    captureFinishedTurns.add(index);
    if (lastVoice)
        emit('speech_input_finished', { source: 'client_energy_estimate', description: '客户端能量估计，非 VAS VAD' }, index, lastVoice);
    emit('input_capture_finished', { samples: inputSamples, peak: inputPeak, status: inputPeak > 0 ? 'captured' : 'silent' }, index);
}
function done(index, interrupted = false) {
    if (index && index === turn && active) {
        finishInputCapture(index);
        emit('turn_finished', { interrupted }, index);
        active = false;
    }
    if (!ending) {
        status(mic && currentMode === 'vad' ? '持续聆听中' : '已连接');
    }
}
function openReportTurn(mode, text = '', at = ns(), continuous = false) {
    turn++;
    active = true;
    currentMode = mode;
    startedVoice = false;
    lastVoice = 0;
    inputSamples = 0;
    inputPeak = 0;
    vadTurnHasStt = false;
    vadResponseStarted = false;
    emit('turn_started', { mode, text, server_listen_turn_id: serverListenTurn, ...(continuous ? { continuous: true } : {}) }, turn, at);
}
function recordInputFrame(frame, index = turn) {
    inputSamples += frame.samples;
    inputPeak = Math.max(inputPeak, frame.peak);
    emit('input_audio_frame_sent', frame.data, index, frame.at);
    if (frame.isSpeech) {
        lastVoice = frame.at;
        if (!startedVoice) {
            startedVoice = true;
            emit('input_speech_started', { source: 'client_energy_estimate' }, index, frame.at);
        }
    }
}
function openNextVadReportTurn(at) {
    if (active) {
        finishInputCapture(turn);
        emit('turn_finished', { interrupted: true, reason: 'vad_next_utterance' }, turn, at);
        active = false;
    }
    const firstSpeech = vadPendingFrames.findIndex(frame => frame.isSpeech);
    const start = firstSpeech >= 0 ? Math.max(0, firstSpeech - 8) : Math.max(0, vadPendingFrames.length - 50);
    const frames = vadPendingFrames.splice(start);
    vadPendingFrames = [];
    const startedAt = frames[0]?.at || at;
    openReportTurn('vad', '', startedAt, true);
    emit('input_started', { continuous: true }, turn, startedAt);
    for (const frame of frames)
        recordInputFrame(frame, turn);
}
const player = new Player((pcm, meta) => {
    if (recording)
        emit('audio_received', { pcm: encodePCM(pcm), sample_rate: rate, audio_seq: meta.audio_seq, response_listen_turn_id: meta.turn || null }, meta.turn, meta.received);
}, (pcm, meta) => {
    if (!recording)
        return;
    const timing = Object.fromEntries([
        'timing_model','timing_method','clock_observed_at_ns','audio_context_time',
        'scheduled_context_time','output_context_time','output_performance_time',
        'clock_tolerance_ms','scheduled_at_ns','playback_observed_at_ns',
        'clock_issue','output_start_confirmed','output_end_confirmed',
        'playback_end_observed_at_ns',
    ].filter(key=>meta[key]!==undefined).map(key=>[key,meta[key]]));
    if (!played.has(meta.turn)) {
        played.add(meta.turn);
        emit('playback_started', timing, meta.turn, meta.at);
    }
    emit('playback_frame_started', { pcm: encodePCM(pcm), sample_rate: rate, audio_seq: meta.audio_seq, ...timing }, meta.turn, meta.at);
}, index => { emit('playback_drained', {}, index); done(index); }, (event, data) => emit(event, data, owner));
function inputLevel(pcm) {
    let peak = 0;
    for (const sample of pcm)
        peak = Math.max(peak, Math.abs(sample));
    $('mic-level').value = Math.min(100, peak / 32768 * 300);
    return peak;
}
async function microphoneChoices() {
    if (typeof navigator === 'undefined' || !navigator.mediaDevices?.enumerateDevices)
        return;
    const selected = $('mic-device').value;
    const devices = await navigator.mediaDevices.enumerateDevices();
    $('mic-device').replaceChildren();
    for (const device of [{ deviceId: '', label: '系统默认麦克风' }, ...devices.filter(d => d.kind === 'audioinput' && d.deviceId !== 'default')]) {
        const option = document.createElement('option');
        option.value = device.deviceId;
        option.textContent = device.label || '麦克风';
        $('mic-device').append(option);
    }
    $('mic-device').value = selected;
}
async function stopMic(manual = false) {
    captureGeneration++;
    if (captureStart && !mic) {
        await captureStart;
        return;
    }
    if (!mic)
        return;
    mic = false;
    const stoppedRecorder = recorder;
    try {
        await stoppedRecorder.stop();
    }
    finally {
        if (recorder === stoppedRecorder)
            recorder = null;
    }
    $('mic-device').disabled = false;
    $('mode').disabled = false;
    $('mic-check').disabled = false;
    $('mic-level').value = 0;
    $('mic-status').textContent = '麦克风已停止';
    if (currentMode === 'vad')
        $('talk').textContent = '开始连续聆听';
    finishInputCapture(turn);
    vadPendingFrames = [];
    if (inputPeak === 0) {
        $('mic-status').textContent = '未录到声音，请检查麦克风或选择其他输入设备';
    }
    if (manual) {
        try {
            send({ type: 'listen', state: 'stop', session_id: window.sessionId });
            emit('listen_stop_sent', { mode: currentMode });
            $('input-control').textContent = '已发送 stop · 等待 VAS 完成识别；服务端接收与 VAD 状态见报告';
        }
        catch (e) {
            emit('listen_stop_failed', { mode: currentMode, message: e.message });
            $('input-control').textContent = 'stop 发送失败 · 请检查连接';
            throw e;
        }
    }
}
async function interrupt(fromCapture = false) {
    const keepListening = auto && mic && currentMode === 'vad';
    if (!keepListening) {
        auto = false;
        if (!fromCapture)
            await stopMic(false);
    }
    if (!active && !player.busy)
        return;
    const old = turn;
    const stopped = waitFor(m => m.type === 'tts' && m.state === 'stop', 3000);
    discard = true;
    send({ type: 'abort', reason: 'wake_word_detected', session_id: window.sessionId });
    emit('abort_sent', {}, old);
    player.stop();
    emit('playback_stopped', {}, old);
    try {
        await stopped;
    }
    catch (e) {
        error(Error('未收到打断结束确认，请结束会话后重新连接'));
        ready = false;
        controls(false);
        $('finish').disabled = false;
        return;
    }
    done(old, true);
    status(keepListening ? '已打断 · 持续聆听中' : '已打断');
}
async function begin(mode, text = '') {
    if (!ready || ending)
        throw Error('请先连接');
    if (mode !== 'vad' && (active || player.busy))
        throw Error('请等待本轮播放完成，或先点击“打断回复”');
    serverListenTurn++;
    openReportTurn(mode, text);
    send({ type: 'listen', state: 'start', mode: mode === 'vad' ? 'auto' : 'manual', session_id: window.sessionId });
    emit('listen_start_sent', { mode, wire_mode: mode === 'vad' ? 'auto' : 'manual' });
    $('input-control').textContent = mode === 'manual' ? '已发送 start/manual · 持续传送语音，松开后发送 stop' : mode === 'vad' ? '已发送 start/auto · 等待 VAS 自动判断结束' : '文字输入';
}
async function startMic() {
    if (mic || ending)
        return;
    if (microphoneProbe)
        throw Error('请先停止麦克风检测');
    if (captureStart)
        return captureStart;
    const generation = ++captureGeneration, mode = $('mode').value;
    captureStart = (async () => {
        let localRecorder;
        try {
            if (mode !== 'vad' && (active || player.busy)) {
                const resumeAuto = auto;
                await interrupt(true);
                auto = resumeAuto;
            }
            if (generation !== captureGeneration || ending)
                return;
            if (!ready)
                throw Error('请先连接');
            $('mic-status').textContent = '正在准备麦克风…';
            $('mic-device').disabled = true;
            $('mode').disabled = true;
            $('mic-check').disabled = true;
            localRecorder = new Recorder((pcm, encoded, offset) => {
                // Keep the final worklet frame during stop(), but reject any
                // late callback after that recorder has been closed/replaced.
                if (vas?.readyState !== 1 || recorder !== localRecorder)
                    return;
                vas.send(encoded);
                const at = ns();
                if (offset === 0)
                    emit('input_started', {}, turn, at);
                const peak = inputLevel(pcm);
                const isSpeech = peak > 500;
                if (recording) {
                    const frame = {
                        at, peak, isSpeech, samples: pcm.length,
                        data: { pcm: encodePCM(pcm), sample_rate: 16000, pcm_offset_samples: offset, stream: mode === 'vad' ? 'uplink' : 'input', is_speech: isSpeech },
                    };
                    if (mode === 'vad' && vadResponseStarted)
                        vadPendingFrames.push(frame);
                    else
                        recordInputFrame(frame);
                    if (vadPendingFrames.length > 1000)
                        vadPendingFrames.splice(0, vadPendingFrames.length - 1000);
                }
                else {
                    inputSamples += pcm.length;
                    inputPeak = Math.max(inputPeak, peak);
                    if (isSpeech)
                        lastVoice = at;
                }
                if (inputSamples >= 16000 && inputPeak === 0)
                    $('mic-status').textContent = '麦克风数据全部为静音，请检查输入设备';
            });
            await localRecorder.prepare($('mic-device').value);
            await microphoneChoices();
            if (generation !== captureGeneration || ending) {
                await localRecorder.stop();
                $('mic-status').textContent = '已取消录音';
                $('mic-device').disabled = false;
                $('mode').disabled = false;
                $('mic-check').disabled = false;
                return;
            }
            await begin(mode);
            recorder = localRecorder;
            inputSamples = 0;
            inputPeak = 0;
            mic = true;
            await recorder.start();
            $('mic-status').textContent = mode === 'vad' ? '持续收音并上传 · 回复播放期间也保持开启' : '正在录音 · 松开空格结束';
            if (mode === 'vad')
                $('talk').textContent = '停止连续聆听';
            status('正在聆听');
        }
        catch (e) {
            mic = false;
            if (mode === 'vad')
                auto = false;
            $('mic-device').disabled = false;
            $('mode').disabled = false;
            $('mic-check').disabled = false;
            await localRecorder?.stop().catch(() => { });
            if (recorder === localRecorder)
                recorder = null;
            emit('recording_error', { message: e.message }, 0);
            $('mic-status').textContent = '录音未开始，请检查麦克风权限';
            throw e;
        }
    })();
    try {
        await captureStart;
    }
    finally {
        captureStart = null;
    }
}
async function receive(raw) {
    if (typeof raw !== 'string') {
        if (discard)
            return;
        const meta = { turn: owner, audio_seq: ++seq, received: ns() };
        emit('audio_packet_received', { audio_seq: seq, bytes: raw.byteLength }, owner, meta.received);
        await player.feed(new Uint8Array(raw), meta);
        return;
    }
    const m = JSON.parse(raw);
    if (m.type === 'diagnostics') {
        if (!recording)
            return;
        if (m.state === 'started') {
            diagnosticsActive = true;
            emit('diagnostics_started', m, 0);
        }
        else if (m.state === 'events')
            emit('diagnostics', m, 0);
        else if (m.state === 'error') {
            emit('diagnostics_error', { error: m.error }, 0);
            error(Error('本次未能获取完整 VAS 诊断数据，原始原因已保存到会话记录。'));
        }
    }
    else if (m.type === 'hello') {
        rate = m.audio_params?.sample_rate || 16000;
        if (m.audio_params?.format !== 'opus')
            throw Error('当前客户端需要 Opus 音频');
        await player.format(rate);
        window.sessionId = m.session_id;
        emit('hello_received', m, 0);
    }
    else if (m.type === 'stt') {
        if (mic && currentMode === 'vad' && vadTurnHasStt && vadResponseStarted)
            openNextVadReportTurn(ns());
        pendingOwner = turn;
        if (mic && currentMode === 'vad')
            active = true;
        emit('stt', m);
        if (mic && currentMode === 'vad')
            vadTurnHasStt = true;
        if (currentMode !== 'text')
            message('user', m.text);
        // Streaming STT is a transcript update, not a microphone endpoint.
        // VAD capture spans replies and pauses until the user stops it.
    }
    else if (m.type === 'tts') {
        if (m.state === 'start' && !outputActive) {
            owner = pendingOwner;
            outputActive = true;
            discard = false;
            status(mic && currentMode === 'vad' ? '正在回复 · 持续聆听中' : '正在回复');
        }
        emit('tts_' + m.state, { ...m, response_listen_turn_id: owner || null, is_session_output: !owner }, owner);
        if (m.state === 'start' && mic && currentMode === 'vad' && vadTurnHasStt)
            vadResponseStarted = true;
        if (m.state === 'sentence_start')
            message('assistant', m.text);
        if (m.state === 'stop') {
            outputActive = false;
            if (!discard)
                await player.flush(owner);
        }
    }
    else if (m.type === 'mcp') {
        const p = m.payload || {};
        if (p.id !== undefined) {
            let result;
            if (p.method === 'initialize')
                result = { protocolVersion: '2024-11-05', capabilities: { tools: {} }, serverInfo: { name: 'voice-testing-browser', version: '1' } };
            else if (p.method === 'tools/list')
                result = { tools: [] };
            send({ type: 'mcp', payload: { jsonrpc: '2.0', id: p.id, ...(result ? { result } : { error: { code: -32601, message: 'Unsupported client tool' } }) } });
        }
        emit('mcp', m);
    }
    else
        emit(m.type || 'control', m);
    notify(m);
}
async function connect() {
    $('error').hidden = true;
    $('connect').disabled = true;
    $('downloads').hidden = true;
    const started = ns();
    ending = false;
    recording = $('recording').checked;
    $('recording').disabled = true;
    clockSync = undefined;
    clockSyncTask = undefined;
    if (record) {
        record.onclose = null;
        record.close();
        record = null;
    }
    try {
        if (!/^(?:[\da-f]{2}:){5}[\da-f]{2}$/i.test($('mac').value.trim()))
            throw Error('请填写有效 MAC 地址');
        await player.prime();
        const config = { environment: $('environment').value, device_id: $('mac').value.trim(), recording };
        let token = $('token').value;
        status('正在建立连接');
        const r = await fetch('/api/sessions', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(config) });
        if (!r.ok)
            throw Error(await r.text());
        const session = await r.json();
        if (recording) {
            record = new WebSocket(location.origin.replace(/^http/, 'ws') + session.record_url);
            await new Promise((resolve, reject) => { record.onopen = resolve; record.onerror = () => reject(Error('本地记录服务连接失败')); });
            record.onmessage = e => {
                const m = JSON.parse(e.data);
                if (m.state === 'error')
                    error(Error(m.message));
                if (m.state === 'finished') {
                    $('downloads').replaceChildren();
                    for (const [key, label] of [['report', '打开静态 HTML 报告'], ['excel', '下载 Excel 报告']]) {
                        const a = document.createElement('a');
                        a.href = m[key];
                        a.textContent = label;
                        a.target = '_blank';
                        $('downloads').append(a);
                    }
                    $('downloads').hidden = false;
                    status('报告已生成');
                }
            };
            record.onclose = () => {
                if (!ending) {
                    error(Error('本地记录连接中断，已停止对话；部分报告保存在本地会话目录'));
                    vas?.close();
                    controls(false);
                }
            };
        }
        turn = 0;
        serverListenTurn = 0;
        owner = 0;
        pendingOwner = 0;
        outputActive = false;
        discard = false;
        diagnosticsActive = false;
        seq = 0;
        active = false;
        vadTurnHasStt = false;
        vadResponseStarted = false;
        vadPendingFrames = [];
        captureFinishedTurns.clear();
        played.clear();
        batch = [];
        emit('connection_started', {}, 0, started);
        flush();
        if ($('ota').checked) {
            const r = await fetch('/api/ota', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...config, token, body: { version: 2, application: { name: 'aquamind-console', version: '1.0.0' } } }) });
            if (!r.ok)
                throw Error('OTA 校验失败：' + r.status);
            const ota = await r.json();
            token = ota.websocket?.token || token;
        }
        const url = new URL(session.ws_url);
        url.searchParams.set('device-id', config.device_id);
        url.searchParams.set('client-id', 'aquamind-console');
        vas = new WebSocket(url);
        vas.binaryType = 'arraybuffer';
        chain = Promise.resolve();
        clockSync = recording ? new ClockSync(send, emit) : undefined;
        vas.onmessage = e => {
            const received = performance.timeOrigin + performance.now();
            if (typeof e.data === 'string') {
                try { if (clockSync?.receive(JSON.parse(e.data), received)) return; } catch { /* normal receive handles malformed messages */ }
            }
            chain = chain.then(() => receive(e.data)).catch(e => { error(e); emit('client_error', { message: e.message }); finish(false).catch(error); }); };
        await new Promise((resolve, reject) => { const timer = setTimeout(() => reject(Error('VAS 连接超时')), 15000); vas.onopen = () => { clearTimeout(timer); resolve(); }; vas.onerror = () => { clearTimeout(timer); reject(Error('VAS 连接失败，请检查地址、认证与证书')); }; });
        emit('connection_opened', {}, 0);
        vas.onclose = () => {
            if (!ending) {
                error(Error(recording ? 'VAS 连接中断，正在导出已收到的数据' : 'VAS 连接中断，请重新连接'));
                finish(false).catch(error);
            }
        };
        if (recording) {
            const diagnostic = waitFor(m => m.type === 'diagnostics' && ['started', 'error'].includes(m.state), 5000);
            send({ type: 'diagnostics', state: 'start', level: 'frame' });
            await diagnostic.catch(e => error(e));
        }
        const hello = waitFor(m => m.type === 'hello');
        send({ type: 'hello', device_id: config.device_id, token, version: 1, transport: 'websocket', features: { mcp: true }, audio_params: { format: 'opus', sample_rate: 16000, channels: 1, frame_duration: 60 } });
        await hello;
        clockSyncTask = recording && $('clock-sync')?.checked !== false ? clockSync.collect() : Promise.resolve();
        controls(true);
        status('已连接');
        localStorage.setItem('testing-mac', config.device_id);
    }
    catch (e) {
        error(e);
        ending = true;
        vas?.close();
        if (record?.readyState === 1) {
            flush();
            record.send(JSON.stringify({ action: 'finish', complete: false }));
        }
        controls(false);
        status('连接失败');
    }
}
async function finish(complete = true) {
    if (ending)
        return;
    ending = true;
    auto = false;
    controls(false);
    $('recording').disabled = true;
    $('connect').disabled = true;
    try {
        await closeMicrophoneProbe().catch(e => { error(e); complete = false; });
        await stopMic(false).catch(e => { error(e); complete = false; });
        await player.chain.catch(e => { error(e); complete = false; });
        if (active || player.busy) {
            if (vas?.readyState === 1)
                send({ type: 'abort', reason: 'wake_word_detected' });
            player.stop();
            emit('playback_stopped', {}, owner);
            done(turn, true);
        }
        await clockSyncTask;
        if (vas?.readyState === 1 && clockSync?.supported) await clockSync.collect('end');
        if (vas?.readyState === 1 && diagnosticsActive) {
            const final = waitFor(m => m.type === 'diagnostics' && m.finished && m.next_seq === m.end_seq, 15000);
            emit('diagnostics_finish_requested', {}, 0);
            send({ type: 'diagnostics', state: 'finish' });
            await final.then(() => emit('diagnostics_finish_confirmed', {}, 0),
                () => emit('diagnostics_finish_timeout', { timeout_seconds: 15 }, 0));
        }
        await chain;
        emit('connection_closed', {}, 0);
        flush();
        if (record?.readyState === 1)
            record.send(JSON.stringify({ action: 'finish', complete }));
    }
    finally {
        vas?.close();
        $('connect').disabled = false;
        $('recording').disabled = false;
        if (!recording)
            status('已断开');
    }
}
$('recording').onchange = recordingControls;
recordingControls();
$('connect').onclick = connect;
$('finish').onclick = () => finish().catch(error);
$('interrupt').onclick = () => interrupt().catch(error);
$('text-form').onsubmit = async (e) => {
    e.preventDefault();
    const text = $('text').value.trim();
    if (!text)
        return;
    try {
        if (mic)
            throw Error('请先停止录音再发送文字');
        auto = false;
        await begin('text', text);
        emit('text_sent', { text });
        send({ type: 'listen', state: 'detect', text, session_id: window.sessionId });
        message('user', text);
        $('text').value = '';
    }
    catch (e) {
        error(e);
    }
};
let held = false;
$('talk').onpointerdown = async (e) => {
    e.preventDefault();
    if (!ready)
        return;
    try {
        if ($('mode').value === 'vad') {
            auto = !auto;
            if (auto)
                await startMic();
            else
                await stopMic(false);
        }
        else {
            held = true;
            if (e.isTrusted)
                $('talk').setPointerCapture(e.pointerId);
            await startMic();
            if (!held)
                await stopMic(true);
        }
    }
    catch (e) {
        error(e);
    }
};
$('talk').onpointerup = $('talk').onpointercancel = () => {
    held = false;
    if ($('mode').value === 'manual')
        stopMic(true).catch(error);
};
function editable(target) { return target?.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target?.tagName); }
function releaseSpace() {
    if (!held)
        return;
    held = false;
    if ($('mode').value === 'manual')
        return stopMic(true).catch(error);
}
window.addEventListener('keydown', e => {
    if (e.code !== 'Space' || e.repeat || e.ctrlKey || e.metaKey || e.altKey || e.isComposing || editable(e.target) || $('mode').value !== 'manual' || !ready || held)
        return;
    e.preventDefault();
    held = true;
    startMic().then(() => {
        if (!held)
            return stopMic(true);
    }).catch(e => { held = false; error(e); });
});
window.addEventListener('keyup', e => {
    if (e.code === 'Space' && held) {
        e.preventDefault();
        return releaseSpace();
    }
});
window.addEventListener('blur', releaseSpace);
$('mode').onchange = () => {
    const manual = $('mode').value === 'manual';
    $('talk').textContent = manual ? '按住说话 · 松开结束' : '开始连续聆听';
    $('voice-hint').textContent = manual
        ? '按住空格或说话按钮持续录音，松开才发送结束信号。不会因本地静音自动停止。文字输入框内空格用于输入。'
        : '持续发送麦克风语音和静音帧，回复播放期间也不会暂停。由 VAS 判断结束与续说；再次点击停止收音。';
    $('input-control').textContent = manual ? '手动结束 · 等待按下空格或说话按钮' : 'VAD 自动结束 · 等待开始';
};
$('mode').onchange();
$('mac').value = localStorage.getItem('testing-mac') || '';
window.addEventListener('pagehide', () => { recorder?.stop().catch(() => { }); closeMicrophoneProbe().catch(() => { }); player.stop(); vas?.close(); record?.close(); });
let microphoneProbe = null, previewUrl = null;
async function closeMicrophoneProbe() {
    const probe = microphoneProbe;
    if (!probe)
        return;
    microphoneProbe = null;
    probe.cancelled = true;
    await probe.prepared.catch(() => { });
    await probe.recorder.stop();
    $('mic-check').textContent = '检测麦克风';
    $('mic-device').disabled = false;
    $('mic-level').value = 0;
    $('mic-status').textContent = '麦克风已停止';
    return probe;
}
$('mic-check').onclick = async () => {
    if (mic || captureStart) {
        error(Error('请先结束当前录音'));
        return;
    }
    if (microphoneProbe) {
        const probe = await closeMicrophoneProbe();
        const samples = probe.parts.reduce((sum, part) => sum + part.length, 0);
        const bytes = new ArrayBuffer(44 + samples * 2), view = new DataView(bytes);
        function ascii(at, value) {
            for (let i = 0; i < value.length; i++)
                view.setUint8(at + i, value.charCodeAt(i));
        }
        ascii(0, 'RIFF');
        view.setUint32(4, 36 + samples * 2, true);
        ascii(8, 'WAVE');
        ascii(12, 'fmt ');
        view.setUint32(16, 16, true);
        view.setUint16(20, 1, true);
        view.setUint16(22, 1, true);
        view.setUint32(24, 16000, true);
        view.setUint32(28, 32000, true);
        view.setUint16(32, 2, true);
        view.setUint16(34, 16, true);
        ascii(36, 'data');
        view.setUint32(40, samples * 2, true);
        let offset = 44;
        for (const part of probe.parts)
            for (const sample of part) {
                view.setInt16(offset, sample, true);
                offset += 2;
            }
        if (previewUrl)
            URL.revokeObjectURL(previewUrl);
        previewUrl = URL.createObjectURL(new Blob([bytes], { type: 'audio/wav' }));
        $('mic-preview').src = previewUrl;
        $('mic-preview').hidden = false;
        $('mic-check').textContent = '检测麦克风';
        $('mic-device').disabled = false;
        $('mic-level').value = 0;
        $('mic-status').textContent = probe.peak ? '已录到声音，可以回听；检测音频未发送到 VAS' : '没有录到声音，请选择其他麦克风并检查浏览器权限';
        return;
    }
    const probe = { parts: [], peak: 0 };
    microphoneProbe = probe;
    probe.recorder = new Recorder(pcm => { probe.parts.push(pcm.slice()); probe.peak = Math.max(probe.peak, inputLevel(pcm)); });
    try {
        $('mic-check').disabled = true;
        probe.prepared = probe.recorder.prepare($('mic-device').value);
        await probe.prepared;
        if (probe.cancelled)
            return;
        await microphoneChoices();
        if (probe.cancelled)
            return;
        await probe.recorder.start();
        $('mic-check').textContent = '停止检测并回听';
        $('mic-device').disabled = true;
        $('mic-status').textContent = '正在检测，请说话；音频仅保留在当前页面';
    }
    catch (e) {
        microphoneProbe = null;
        await probe.recorder.stop().catch(() => { });
        error(e);
    }
    finally {
        $('mic-check').disabled = false;
    }
};
