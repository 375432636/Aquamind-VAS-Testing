const report = JSON.parse(document.getElementById('data').textContent);
const turn = report.turn;
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '—').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const sec = value => value == null ? '—' : Number(value).toFixed(2);
const ms = value => value == null ? '—' : sec(value / 1000);
function secondsView(value) {
  if (Array.isArray(value)) return value.map(secondsView);
  if (!value || typeof value !== 'object') return value;
  return Object.fromEntries(Object.entries(value).map(([key, item]) => {
    const scale = key.endsWith('_ms') ? 1000 : key.endsWith('_ns') ? 1e9 : null;
    return [scale ? key.replace(/_(ms|ns)$/, '_seconds') : key, scale && typeof item === 'number' ? item / scale : secondsView(item)];
  }));
}
function table(id, headers, rows) {
  $(id).innerHTML = '<thead><tr>' + headers.map(h => '<th>' + esc(h) + '</th>').join('') + '</tr></thead><tbody>' + rows.map(row => '<tr>' + row.map(value => '<td>' + esc(typeof value === 'object' ? JSON.stringify(value) : value) + '</td>').join('') + '</tr>').join('') + '</tbody>';
}
document.addEventListener('play', event => {
  if (event.target.tagName === 'AUDIO') document.querySelectorAll('audio').forEach(audio => {if (audio !== event.target) audio.pause();});
}, true);
window.addEventListener('pagehide', () => document.querySelectorAll('audio').forEach(audio => audio.pause()));
function configSummary(data = {}) {
  const vendor = data.vendor || (data.adapter === 'openai' || data.provider === 'openai' ? 'OpenAI 兼容接口' : data.adapter || data.provider);
  return [vendor, data.model].filter(Boolean).join(' · ');
}
function configDetails(data = {}) {
  const labels = {temperature:'Temperature', top_p:'Top P', max_tokens:'Max tokens', enable_thinking:'Thinking', voice_id:'音色', speed:'语速', vol:'音量', pitch:'音调', sample_rate:'采样率', format:'格式', channel:'声道', bitrate:'码率'};
  const params = data.parameters || {};
  const values = {...params, ...(params.voice_setting || {}), ...(params.audio_setting || {})};
  return [configSummary(data), ...Object.entries(values).filter(([key,value]) => labels[key] && value != null && typeof value !== 'object').map(([key,value]) => `${labels[key]} ${value}`)].filter(Boolean).join(' · ');
}
function timeline(id, lanes) {
  const root = $(id);
  const all = lanes.flatMap(lane => lane.segments || [lane]);
  const points = lanes.flatMap(lane => lane.markers || []);
  const bounds = [...all, ...points].filter(item => Number.isFinite(item.start));
  if (!bounds.length) {root.innerHTML = '<div class="empty-state">未采集到该来源的时间数据</div>'; return;}
  const zero = Math.min(...bounds.map(item => item.start));
  const end = Math.max(...bounds.map(item => item.end ?? item.start));
  const range = Math.max(100000000, end-zero);
  const title = item => `${item.label}${item.segment_number ? ' #' + item.segment_number : ''} · ${(item.start-zero)/1e9 < 0 ? '' : '开始 '}${((item.start-zero)/1e9).toFixed(3)} s${item.end != null ? ' · 结束 ' + sec((item.end-zero)/1e9) + ' s · 耗时 ' + sec((item.end-item.start)/1e9) + ' s' : ''}${item.status && item.status !== 'ok' ? ' · ' + item.status : ''}`;
  function bar(item) {
    const index = all.indexOf(item);
    const duration = item.end != null;
    return `<button type="button" class="${duration ? 'bar' : 'point'}" data-event-index="${index}" aria-label="${esc(title(item))}" title="${esc(title(item))}" style="left:${(item.start-zero)/range*100}%;${duration ? 'width:' + Math.max(.25,(item.end-item.start)/range*100) + '%;' : ''}background:${item.color || '#407d77'}">${duration && item.segment_number && (item.end-item.start)/range>.04 ? '#' + item.segment_number : ''}</button>`;
  }
  const markerGroups = [];
  function markers(lane) {
    const groups = [];
    const width = Math.max(300, (root.clientWidth || 800)-176);
    for (const marker of [...(lane.markers || [])].sort((a,b)=>a.start-b.start)) {
      const previous = groups[groups.length-1];
      if (previous && (marker.start-previous[0].start)/range*width < 28) previous.push(marker);
      else groups.push([marker]);
    }
    if (!groups.length) return '';
    return '<div class="milestone-track">' + groups.map(group => {
      const index = markerGroups.push(group)-1;
      const caption = group.map(title).join(' / ');
      return `<button type="button" class="timeline-marker" data-marker-index="${index}" aria-label="${esc(caption)}" title="${esc(caption)}" style="left:${(group[0].start-zero)/range*100}%"><svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true"><path d="M6 1 11 6 6 11 1 6Z" fill="currentColor"/></svg>${group.length>1 ? '<span>'+group.length+'</span>' : ''}</button>`;
    }).join('') + '</div>';
  }
  root.innerHTML = `<div class="timeline-plot"><div class="axis"><span>0 s</span><span>${sec(range/1e9/2)} s</span><span>${sec(range/1e9)} s</span></div>` + lanes.map(lane => {
    const segments = lane.segments || [lane];
    const configs = [...new Set(segments.map(item=>configSummary(item.data)).filter(Boolean))];
    return `<div class="lane"><span class="lane-label">${esc(lane.label)}${configs.length ? '<small class="lane-model">'+esc(configs.join(' / '))+'</small>' : ''}</span><div><div class="track">${segments.map(bar).join('')}</div>${markers(lane)}<div class="segment-key">${segments.filter(item => item.end != null).map(item => `<span><i style="background:${item.color || '#407d77'}"></i>${item.segment_number ? '#' + item.segment_number + ' · ' : ''}${sec((item.end-item.start)/1e9)} s</span>`).join('')}</div></div></div>`;
  }).join('') + '</div><div class="span-detail" role="status" hidden></div>';
  enableTimelineMeasurement(root, range/1e9);
  root.onclick = event => {
    const button = event.target.closest('[data-event-index], [data-marker-index]');
    if (!button || !root.contains(button)) return;
    const detail = root.querySelector('.span-detail');
    if (button.dataset.markerIndex != null) {
      const group = markerGroups[Number(button.dataset.markerIndex)];
      detail.textContent = group.map(item => {
        const data = item.data || {};
        const extra = [];
        if (item.since_request_seconds != null) extra.push(`距本次请求提交 ${item.since_request_seconds.toFixed(3)} s`);
        if (data.delta_kind) extra.push(data.delta_kind === 'tool' ? '工具增量' : '正文增量');
        if (data.punctuation) extra.push(`分句标点 ${data.punctuation}`);
        if (data.trigger && data.trigger !== 'punctuation') extra.push(data.trigger === 'stream_end' ? '流结束提交' : '请求边界观测');
        if (data.text_received_ns != null) extra.push(`触发文本入队后 ${((item.start-data.text_received_ns)/1e9).toFixed(3)} s`);
        if (data.http_status != null) extra.push(`HTTP ${data.http_status}`);
        if (data.error_type) extra.push(data.error_type);
        return [title(item), ...extra].join(' · ');
      }).join('\n');
    } else {
      const item = all[Number(button.dataset.eventIndex)];
      detail.textContent = [title(item), configDetails(item.data), item.data?.purpose === 'rules' ? '规则向量' : item.data?.purpose === 'query' ? '用户问题向量' : '', item.data?.error_type || ''].filter(Boolean).join(' · ');
    }
    detail.hidden = false;
  };
}
if (!turn) renderSessionTimeline($('session-timeline'), report.session_playback, report.session_chain);
if (turn) {
  renderReplyTiming($('reply-timing'), turn.reply_timing, report.session_playback, report.turn_index);
  const events = turn.events || [], vas = turn.vas_events || [];
  const clientLabels = {sensor_sent:'传感器指令发出', input_started:'开始发送输入', first_audio_sent:'第一帧音频发出', audio_send_completed:'音频发送完成', speech_input_started:'语音开始', speech_input_finished:'语音结束', background_noise_started:'持续发送底噪', listen_stop_sent:'停止指令发出', playback_started:'开始播放回复', playback_stopped:'停止播放', playback_drained:'回复播放完成', abort_wire_sent:'打断指令发出', music_call_accepted:'音乐指令已接受', music_playback_started:'音乐开始播放', music_playback_stopped:'音乐停止播放'};
  const vadLabels = {local_vad_speech_started:'本地 VAD · 语音开始', local_vad_last_voice:'本地 VAD · 最后语音帧', local_vad_endpoint_detected:'本地 VAD · 结束', asr_speech_started:'ASR VAD · 语音开始', asr_endpoint_detected:'ASR VAD · 结束', asr_final:'ASR · 最终识别结果'};
  const colors = {asr_request:'#318494', memory_request:'#9074af', llm_request:'#5070bf', tool_call:'#b1833e', guardrail_embedding:'#b27552'};
  const ttsColors = ['#397d75','#5b80ad','#9273a4','#ac843d','#ad7187','#528488'];
  const stageLabels = {memory_request:'Memory', listen_stop_received:'VAS 收到停止', listen_stop_enqueued:'停止消息入队', listen_stop_dequeued:'停止消息出队', listen_finalize_started:'开始处理语音结束', audio_input_completed:'上行音频接收完成', asr_commit_sent:'ASR 提交结束', ...vadLabels};
  timeline('client', events.filter(event => clientLabels[event.event]).map(event => ({label:clientLabels[event.event], start:event.at_ns, data:event.data})));
  timeline('server', (turn.timeline_lanes || []).map(lane => ({
    label:stageLabels[lane.label] || lane.label,
    segments:lane.segments.map((span,index) => ({label:lane.label, segment_number:index+1, start:span.start_ns, end:span.end_ns, color:span.name==='tts_request' ? ttsColors[index%ttsColors.length] : colors[span.name], status:span.status, data:span.data})),
    markers:(lane.markers || []).map(marker=>({...marker, start:marker.start_ns}))
  })));
  document.querySelectorAll('[data-clock]').forEach(button => button.addEventListener('click', () => {
    const server = button.dataset.clock === 'server';
    $('server').hidden = !server; $('client').hidden = server;
    $('clock-label').textContent = server ? 'VAS 单调时钟 · 本轮首个已采集阶段 = 0 s' : '客户端单调时钟 · 本轮首个已采集事件 = 0 s';
    document.querySelectorAll('[data-clock]').forEach(item => item.setAttribute('aria-pressed', String(item === button)));
  }));
  const handoff = vas.filter(event => stageLabels[event.event] && event.event !== 'memory_request').sort((a,b) => a.monotonic_ns-b.monotonic_ns);
  const isVad = turn.input_settings?.mode === 'vad';
  const base = handoff.find(event => event.event === (isVad ? 'asr_endpoint_detected' : 'listen_stop_received'))?.monotonic_ns;
  table('handoff', ['事件', isVad ? '相对 ASR VAD 结束 / s' : '相对 VAS 收到停止 / s', '详情'], handoff.map(event => [stageLabels[event.event], base == null ? '—' : sec((event.monotonic_ns-base)/1e9), secondsView(event.data)]));
  table('spans', ['请求', '耗时 / s', '状态', '详情'], (turn.spans || []).map(span => [stageLabels[span.name] || span.name, ms(span.duration_ms), span.status, secondsView(span.data)]));
  table('checks', ['断言', '期望', '实际', '结果'], (turn.checks || []).map(check => [check.name.replace(/_ms(?=_|$)/,'_seconds'), check.name.includes('_ms') ? ms(check.expected) + ' s' : check.expected, check.name.includes('_ms') ? ms(check.actual) + ' s' : check.actual, check.passed ? '通过' : '失败']));
  $('raw').textContent = JSON.stringify(secondsView({interruption:turn.interruption, client:events, vas}), null, 2);
}
