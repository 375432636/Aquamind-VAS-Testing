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
function checkExpectation(check) {
  const spec = check.expected || {};
  if (!check.category) return check.name.includes('_ms') ? ms(check.expected) + ' s' : check.expected;
  if (spec.verified === false) return '配置未核实：' + spec.reason;
  if (spec.contains_all) return spec.contains_all.map(group=>'至少包含 ' + group.join(' / ')).join('；');
  if (spec.sensor) return '发送传感器指令 ' + spec.sensor;
  if (check.category === 'tools') return [
    ...(spec.any_of?.length ? ['至少一次正常完成：' + spec.any_of.join(' / ')] : []),
    ...(spec.required || []).map(tool=>'必须 ' + tool.name + (tool.arguments ? '（' + Object.entries(tool.arguments).map(([key,value])=>key+'='+value).join('，') + '）' : '')),
    ...(spec.forbidden || []).map(name=>'禁止 ' + name),
  ].join('；') || '未指定工具要求';
  if (check.category === 'audio') return (spec.ending === 'interrupted' ? '预期打断' : '正常结束') + ' · 可解码且非空';
  return spec;
}
function checkActual(check) {
  const actual = check.actual || {};
  if (!check.category) return check.name.includes('_ms') ? ms(check.actual) + ' s' : check.actual;
  if (check.category === 'tools') return Object.entries(actual.calls || {}).map(([name,count])=>name+' × '+count).join('；') || '未观察到工具调用';
  if (check.category === 'audio') return [actual.decodable == null ? '解码未知' : actual.decodable ? '可解码' : '不可解码', actual.duration_ms == null ? '时长未知' : ms(actual.duration_ms)+' s', {normal:'正常结束',interrupted:'已打断'}[actual.ending] || '结束方式未知'].join(' · ');
  if (actual.command) return '传感器 ' + actual.command;
  return actual.text == null ? '未知' : actual.text.length > 180 ? actual.text.slice(0,180)+'…（完整内容见上方回复）' : actual.text;
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
  const labels = {temperature:'Temperature', top_p:'Top P', max_tokens:'Max tokens', voice_id:'音色', speed:'语速', vol:'音量', pitch:'音调', sample_rate:'采样率', format:'格式', channel:'声道', bitrate:'码率'};
  const params = data.parameters || {};
  const values = {...params, ...(params.voice_setting || {}), ...(params.audio_setting || {})};
  const mode = params.enable_thinking === true ? 'Thinking' : params.enable_thinking === false ? 'Unthinking' : null;
  return [configSummary(data), mode, ...Object.entries(values).filter(([key,value]) => labels[key] && value != null && typeof value !== 'object').map(([key,value]) => `${labels[key]} ${value}`)].filter(Boolean).join(' · ');
}
function beijingTime(timestamp, withDate = false) {
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en-GB', {
    timeZone:'Asia/Shanghai', year:'numeric', month:'2-digit', day:'2-digit',
    hour:'2-digit', minute:'2-digit', second:'2-digit', fractionalSecondDigits:3, hourCycle:'h23',
  }).formatToParts(new Date(timestamp)).map(part=>[part.type,part.value]));
  return (withDate ? `${parts.year}-${parts.month}-${parts.day} ` : '') + `${parts.hour}:${parts.minute}:${parts.second}.${parts.fractionalSecond}`;
}
function traceLanes(lanes, includeTurn = false) {
  const colors = {asr_request:'#318494', memory_request:'#9074af', llm_request:'#5070bf', tool_call:'#b1833e', guardrail_embedding:'#b27552'};
  const ttsColors = ['#397d75','#5b80ad','#9273a4','#ac843d','#ad7187','#528488'];
  return lanes.map(lane => ({
    source:lane.source,
    label:(includeTurn ? (lane.turn_index == null ? '会话级 · ' : `第 ${lane.turn_index} 轮 · `) : '') + (lane.source === 'client' ? '客户端 · ' : 'VAS · ') + (lane.label === 'memory_request' ? 'Memory' : lane.label),
    segments:lane.segments.map((span,index)=>({...span,label:lane.label,segment_number:index+1,start:span.plot_start_ns,end:span.plot_end_ns,color:span.name==='tts_request'?ttsColors[index%ttsColors.length]:colors[span.name]})),
    markers:(lane.markers || []).map(marker=>({...marker,start:marker.plot_start_ns,monotonic_start_ns:marker.start_ns})),
  }));
}
function traceLabel(lane) {
  const configs = [...new Set((lane.segments || [lane]).map(item=>configSummary(item.data)).filter(Boolean))];
  return esc(lane.label) + (configs.length ? '<small class="lane-model">'+esc(configs.join(' / '))+'</small>' : '');
}
function timeline(id, lanes, clock = {}, options = {}) {
  const root = $(id);
  const all = lanes.flatMap(lane => lane.segments || [lane]);
  const points = lanes.flatMap(lane => lane.markers || []);
  const bounds = [...all, ...points].filter(item => Number.isFinite(item.start));
  if (!bounds.length) {root.innerHTML = '<div class="empty-state">未采集到该来源的时间数据</div>'; return;}
  const zero = options.start_ns ?? Math.min(...bounds.map(item => item.start));
  const end = options.end_ns ?? Math.max(...bounds.map(item => item.end ?? item.start));
  const timeZero = options.time_zero_ns ?? zero;
  const range = Math.max(100000000, end-zero);
  const durationSeconds = item => item.duration_ms != null ? item.duration_ms/1000 : (item.end-item.start)/1e9;
  const title = item => `${options.embedded ? (item.turn_index == null ? '会话级 · ' : '第 '+item.turn_index+' 轮 · ') : ''}${item.label}${item.thinking_mode && !item.label.includes(item.thinking_mode) ? ' · ' + item.thinking_mode : ''}${item.segment_number ? ' #' + item.segment_number : ''}${item.wall_time_ms != null ? ' · 北京时间 ' + beijingTime(item.wall_time_ms,true) : ''} · 开始 ${((item.start-timeZero)/1e9).toFixed(3)} s${item.end != null ? ' · 结束 ' + sec((item.end-timeZero)/1e9) + ' s · 耗时 ' + sec(durationSeconds(item)) + ' s' : ''}${item.status && item.status !== 'ok' ? ' · ' + item.status : ''}`;
  function bar(item) {
    const index = all.indexOf(item);
    const duration = item.end != null;
    return `<button type="button" class="${duration ? 'bar' : 'point'}" data-event-index="${index}" aria-label="${esc(title(item))}" title="${esc(title(item))}" style="left:${(item.start-zero)/range*100}%;${duration ? 'width:' + Math.max(.25,(item.end-item.start)/range*100) + '%;' : ''}background:${item.color || '#407d77'}">${duration && item.segment_number && (item.end-item.start)/range>.04 ? '#' + item.segment_number : ''}</button>`;
  }
  const markerGroups = [];
  function markers(lane) {
    const groups = [];
    const width = Math.max(300, (root.clientWidth || 800)-(options.embedded ? 0 : 176));
    for (const marker of [...(lane.markers || [])].sort((a,b)=>a.start-b.start)) {
      const previous = groups[groups.length-1];
      if (previous && (marker.start-previous[0].start)/range*width < (['image','video'].includes(marker.kind) ? 56 : 28)) previous.push(marker);
      else groups.push([marker]);
    }
    if (!groups.length) return '';
    return '<div class="milestone-track">' + groups.map(group => {
      const index = markerGroups.push(group)-1;
      const caption = group.map(title).join(' / ');
      const media = ['image','video'].includes(group[0].kind);
      return `<button type="button" class="timeline-marker${media ? ' media-marker' : ''}" data-marker-index="${index}" aria-label="${esc(caption)}" title="${esc(caption)}" style="left:${(group[0].start-zero)/range*100}%">${media ? mediaMarkerIcon(group) : '<svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true"><path d="M6 1 11 6 6 11 1 6Z" fill="currentColor"/></svg>'}${group.length>1 ? '<span>'+group.length+'</span>' : ''}</button>`;
    }).join('') + '</div>';
  }
  const axis = clock.mode === 'wall' ? [0,range/2,range].map(offset=>`<span>${beijingTime(clock.origin_wall_time_ms+(zero+offset)/1e6)}<small>+${sec(offset/1e9)} s</small></span>`).join('') : `<span>0 s</span><span>${sec(range/1e9/2)} s</span><span>${sec(range/1e9)} s</span>`;
  root.innerHTML = `<div class="timeline-plot">${options.embedded ? '' : '<div class="axis">'+axis+'</div>'}` + lanes.map(lane => {
    const segments = lane.segments || [lane];
    return `<div class="lane" data-source="${lane.source === 'client' ? 'client' : 'server'}">${options.embedded ? '' : '<span class="lane-label">'+traceLabel(lane)+'</span>'}<div><div class="track">${segments.map(bar).join('')}</div>${markers(lane)}<div class="segment-key">${segments.filter(item => item.end != null).map(item => `<span><i style="background:${item.color || '#407d77'}"></i>${item.segment_number ? '#' + item.segment_number + ' · ' : ''}${sec(durationSeconds(item))} s</span>`).join('')}</div></div></div>`;
  }).join('') + '</div>' + (options.detail ? '' : '<div class="span-detail" role="status" hidden></div>');
  if (!options.embedded) enableTimelineMeasurement(root, range/1e9);
  function activate(element) {
    const button = element.closest('[data-event-index], [data-marker-index]');
    if (!button || !root.contains(button)) return false;
    const detail = options.detail || root.querySelector('.span-detail');
    if (button.dataset.markerIndex != null) {
      const group = markerGroups[Number(button.dataset.markerIndex)];
      if (['image','video'].includes(group[0].kind)) {
        detail.innerHTML = mediaMarkerDetails(group, title);
        detail.hidden = false;
        return true;
      }
      detail.textContent = group.map(item => {
        const data = item.data || {};
        const extra = [];
        if (item.since_request_seconds != null) extra.push(`距本次请求提交 ${item.since_request_seconds.toFixed(3)} s`);
        if (data.delta_kind) extra.push(data.delta_kind === 'tool' ? '工具增量' : '正文增量');
        if (data.punctuation) extra.push(`分句标点 ${data.punctuation}`);
        if (data.trigger && data.trigger !== 'punctuation') extra.push(data.trigger === 'stream_end' ? '流结束提交' : '请求边界观测');
        if (data.text_received_ns != null) extra.push(`触发文本入队后 ${(((item.monotonic_start_ns ?? item.start)-data.text_received_ns)/1e9).toFixed(3)} s`);
        if (data.http_status != null) extra.push(`HTTP ${data.http_status}`);
        if (data.http_request_id) extra.push(`请求 ${data.llm_request_seq ?? '未知'} · HTTP 尝试 ${data.http_request_seq ?? '未知'} · 重试 ${data.retry_count ?? '未知'} · ID ${data.http_request_id}`);
        if (data.connection_state) extra.push(`连接 ${{new:'新建',reused:'复用',unknown:'未知'}[data.connection_state] || '未知'}`);
        if ('transport_retry_count' in data) extra.push(`传输重试 ${data.transport_retry_count ?? '未知'}`);
        if (data.phase) extra.push(data.phase);
        if (['input_tokens','cached_tokens','output_tokens'].some(key=>key in data)) extra.push(`输入 / 缓存 / 输出 Token ${data.input_tokens ?? '未知'} / ${data.cached_tokens ?? '未知'} / ${data.output_tokens ?? '未知'}`);
        if ('requested_silence_duration_ms' in data) extra.push(`请求静音阈值 ${data.requested_silence_duration_ms == null ? '未知 / 不启用' : data.requested_silence_duration_ms + ' ms'}`);
        if ('confirmed_silence_duration_ms' in data) extra.push(`服务端确认阈值 ${data.confirmed_silence_duration_ms == null ? '未知' : data.confirmed_silence_duration_ms + ' ms'}`);
        if (data.error_type) extra.push(data.error_type);
        return [title(item), ...extra].join(' · ');
      }).join('\n');
    } else {
      const item = all[Number(button.dataset.eventIndex)];
      detail.textContent = [title(item), configDetails(item.data), item.data?.purpose === 'rules' ? '规则向量' : item.data?.purpose === 'query' ? '用户问题向量' : '', item.data?.error_type || ''].filter(Boolean).join(' · ');
    }
    detail.hidden = false;
    return true;
  }
  if (!options.embedded) root.onclick = event=>activate(event.target);
  return {activate};
}
if (!turn) renderSessionTimeline($('session-timeline'), report.session_playback);
if (turn) {
  renderReplyTiming($('reply-timing'), turn.reply_timing, report.session_playback, report.turn_index);
  const events = turn.events || [], vas = turn.vas_events || [];
  const vadLabels = {local_vad_speech_started:'本地 VAD · 语音开始', local_vad_last_voice:'本地 VAD · 最后语音帧', local_vad_endpoint_detected:'本地 VAD · 结束', asr_speech_started:'ASR VAD · 语音开始', asr_endpoint_detected:'ASR VAD · 结束', asr_final:'ASR · 最终识别结果'};
  const stageLabels = {memory_request:'Memory', listen_stop_received:'VAS 收到停止', listen_stop_enqueued:'停止消息入队', listen_stop_dequeued:'停止消息出队', listen_finalize_started:'开始处理语音结束', audio_input_completed:'上行音频接收完成', asr_commit_sent:'ASR 提交结束', ...vadLabels};
  const clock = turn.combined_timeline;
  $('clock-label').textContent = clock.mode === 'wall'
    ? `北京时间 ${beijingTime(clock.origin_wall_time_ms,true).slice(0,10)}（UTC+8）· 两端系统时间，未校准偏移`
    : `${clock.reason === 'clock_discontinuity' ? '绝对时间记录不连续' : '旧记录缺少绝对时间'} · 各来源独立从 0 s 开始，不能跨来源相减`;
  timeline('combined', traceLanes(clock.lanes), clock);
  const handoff = vas.filter(event => stageLabels[event.event] && event.event !== 'memory_request').sort((a,b) => a.monotonic_ns-b.monotonic_ns);
  const isVad = turn.input_settings?.mode === 'vad';
  const base = handoff.find(event => event.event === (isVad ? 'asr_endpoint_detected' : 'listen_stop_received'))?.monotonic_ns;
  table('handoff', ['事件', isVad ? '相对 ASR VAD 结束 / s' : '相对 VAS 收到停止 / s', '详情'], handoff.map(event => [stageLabels[event.event], base == null ? '—' : sec((event.monotonic_ns-base)/1e9), secondsView(event.data)]));
  table('spans', ['请求', '耗时 / s', '状态', '详情'], (turn.spans || []).map(span => [stageLabels[span.name] || span.name, ms(span.duration_ms), span.status, secondsView(span.data)]));
  table('checks', ['断言', '期望', '实际', '结果', '失败分类 / 原因'], (turn.checks || []).map(check => [({recognition:'识别',tools:'工具',reply:'回复',audio:'音频'}[check.category] || {image_items_min:'至少收到图片数',video_items_min:'至少收到视频数'}[check.name] || check.name.replace(/_ms(?=_|$)/,'_seconds')), checkExpectation(check), checkActual(check), ({passed:'通过',failed:'失败',unknown:'未知',not_applicable:'不适用'}[check.status] || (check.passed ? '通过' : '失败')), [({functional:'功能失败',diagnostic_missing:'诊断缺失',latency:'延迟超标',configuration_unverified:'配置未核实'}[check.failure_kind] || ''), check.reason || ''].filter(Boolean).join(' · ')]));
  $('raw').textContent = JSON.stringify(secondsView({interruption:turn.interruption, client:events, vas}), null, 2);
}
