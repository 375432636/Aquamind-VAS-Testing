const report = JSON.parse(document.getElementById('data').textContent);
if (typeof location !== 'undefined' && /(?:[?&])clock=raw(?:&|$)/.test(location.search) && report.raw_view) {
  Object.assign(report, report.raw_view);
  const label = document.getElementById('clock-sync-status');
  if (label) label.textContent = '原始时间 · 未应用跨端校准';
  const timelineLabel = document.getElementById('session-clock-label');
  if (timelineLabel && report.session_playback?.vas_timeline?.mode === 'relative') timelineLabel.textContent = '相对时间 · 客户端与 VAS 各自从 0 s 开始，不能跨来源相减';
  else if (timelineLabel?.textContent?.startsWith('北京时间')) timelineLabel.textContent = '北京时间 UTC+8 · 会话起点 = 0 s · 原始时间，未应用跨端校准';
}
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
function memoryDetails(evidence) {
  if (!evidence) return '';
  return [evidence.label, ...['returned','injected'].map(kind=> {
    const content=evidence[kind] || {};
    const label=kind==='returned'?'Memory 返回内容（格式化）':'本轮实际补充的 Memory';
    const state=!content.complete?' · 数据不完整':content.truncated?' · 已截断':'';
    return `${label}${state}\n${content.text || (content.complete?'（无）':'未采集')}`;
  })].join('\n\n');
}
function candidateDetails(data = {}) {
  if (!data.pipeline_attempt_id) return '';
  const state={adopted:'已采用',discarded:'已弃用'}[data.selection] || '等待判定';
  const reason={memory_changed:'Memory 有新内容',guardrail_blocked:'护栏拦截',turn_cancelled:'本轮已打断',pipeline_error:'流水线失败'}[data.reason];
  return [state,reason,data.pipeline_role==='memory_replacement'?'补充 Memory 重发':'并行候选请求',data.replaces_attempt_id?`替代请求 ${data.replaces_attempt_id}`:''].filter(Boolean).join(' · ');
}
function knowledgeDetails(evidence) {
  if (!evidence) return '';
  return [evidence.label, ...['query','returned'].map(kind=> {
    const content=evidence[kind] || {};
    const label=kind==='query'?'查询词':'工具返回正文';
    const state=[!content.complete?'数据不完整':'',content.truncated?'已截断':''].filter(Boolean).join(' · ');
    return `${label}${state?' · '+state:''}\n${content.text || (content.complete && content.available?'（空返回）':'未采集')}`;
  })].join('\n\n');
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
    source:lane.source, turn_index:lane.turn_index, category:lane.category,
    intervals:lane.intervals, missing:lane.missing, key_id:lane.key_id, llm_number:lane.llm_number,
    label:(includeTurn ? (lane.turn_index == null ? '会话级 · ' : `第 ${lane.turn_index} 轮 · `) : '') + (lane.source === 'mixed' ? '' : lane.source === 'client' ? '客户端 · ' : 'VAS · ') + (lane.label === 'memory_request' ? 'Memory' : lane.label),
    segments:lane.segments.map((span,index)=>({...span,label:lane.label,segment_number:index+1,start:span.plot_start_ns,end:span.plot_end_ns,color:span.name==='tts_request'?ttsColors[index%ttsColors.length]:colors[span.name]})),
    markers:(lane.markers || []).map(marker=>({...marker,start:marker.plot_start_ns,monotonic_start_ns:marker.start_ns,
      color:marker.color || (lane.category === 'tts_request' ? ttsColors[Math.max(0,lane.segments.findIndex(s=>s.span_id===marker.span_id))%ttsColors.length] : colors[lane.category] || '#176879')})),
  }));
}
function keyDuration(interval) {
  if(!interval) return '未采集';
  if(interval.duration_seconds==null) return '不可比较';
  const value=interval.duration_seconds.toFixed(3)+' s';
  return interval.clock_basis==='wall_unaligned' ? '≈ '+value+' · 未校时' : interval.clock_basis==='causal_bounded' ? '≈ '+value+' ± '+(interval.uncertainty_seconds*1000).toFixed(3)+' ms' : value;
}
function traceLabel(lane) {
  if(lane.category==='key_moments') {
    const pair=lane.intervals?.[0];
    const names=pair ? `${pair.from_label} → ${pair.to_label}` : lane.markers.map(m=>m.label).join(' → ');
    return `<strong class="pair-heading">${esc(lane.label)}</strong><small class="pair-names">${esc(names)}</small><b class="pair-duration">${esc(keyDuration(pair))}</b>`;
  }
  const configs = [...new Set((lane.segments || [lane]).map(item=>configSummary(item.data)).filter(Boolean))];
  return esc(lane.label) + (configs.length ? '<small class="lane-model">'+esc(configs.join(' / '))+'</small>' : '');
}
function ttsTextDetails(item) {
  const evidence = item.tts_evidence;
  if (!evidence) return '';
  if (evidence.status !== 'matched') return '文本未关联 · '+(evidence.reason || '没有足够的句子关联证据');
  return `${evidence.source_label}：${evidence.text}\n未采集精确 TTS 请求文本。`;
}
function intervalOrderNote(item) {
  if (!(item.duration_seconds < 0)) return '';
  return item.clock_basis === 'monotonic'
    ? '；后者先发生，不是负耗时'
    : '；墙钟位置倒序，未校时不能判断实际先后';
}
function timeline(id, lanes, clock = {}, options = {}) {
  const root = $(id);
  const expandedLanes = options.expandedLanes || new Set();
  const all = lanes.flatMap(lane => lane.segments || [lane]);
  const points = lanes.flatMap(lane => lane.markers || []);
  const bounds = [...all, ...points].filter(item => Number.isFinite(item.start));
  if (!bounds.length) {root.innerHTML = '<div class="empty-state">未采集到该来源的时间数据</div>'; return;}
  const zero = options.start_ns ?? Math.min(...bounds.map(item => item.start));
  const end = options.end_ns ?? Math.max(...bounds.map(item => item.end ?? item.start));
  const timeZero = options.time_zero_ns ?? zero;
  const range = Math.max(100000000, end-zero);
  const durationSeconds = item => item.duration_ms != null ? item.duration_ms/1000 : (item.end-item.start)/1e9;
  const title = item => `${options.embedded ? (item.turn_index == null ? '会话级 · ' : '第 '+item.turn_index+' 轮 · ') : ''}${item.label}${item.thinking_mode && !item.label.includes(item.thinking_mode) ? ' · ' + item.thinking_mode : ''}${item.segment_number ? ' #' + item.segment_number : ''}${item.wall_time_ms != null ? (clock.mode === 'bounded' ? ' · VAS 原始北京时间 ' : ' · 北京时间 ') + beijingTime(item.wall_time_ms,true) : ''} · 开始 ${((item.start-timeZero)/1e9).toFixed(3)} s${item.end != null ? ' · 结束 ' + sec((item.end-timeZero)/1e9) + ' s · 耗时 ' + sec(durationSeconds(item)) + ' s' : ''}${item.status && item.status !== 'ok' ? ' · ' + item.status : ''}`;
  function bar(item, laneIndex) {
    const index = all.indexOf(item);
    const duration = item.end != null;
    const evidence=item.tts_evidence;
    const caption=evidence ? `#${item.segment_number} ${evidence.status==='matched'?evidence.text:'文本未关联'}` : duration && item.segment_number && (item.end-item.start)/range>.04 ? '#' + item.segment_number : '';
    const description=[title(item),ttsTextDetails(item)].filter(Boolean).join('\n');
    return `<button type="button" class="${duration ? 'bar' : 'point'}${evidence?' tts-text-bar':''}" data-event-index="${index}" aria-expanded="${expandedLanes.has(laneIndex)}" aria-label="${esc(description)} · 点击${expandedLanes.has(laneIndex)?'折叠':'展开'}事件" title="${esc(description)} · 点击${expandedLanes.has(laneIndex)?'折叠':'展开'}事件" style="left:${(item.start-zero)/range*100}%;${duration ? 'width:' + Math.max(.25,(item.end-item.start)/range*100) + '%;' : ''}background:${item.color || '#407d77'}">${esc(caption)}</button>`;
  }
  const markerGroups = [];
  const keyIntervals = [];
  function keyMoments(lane) {
    const width = Math.max(100,(root.clientWidth || 800)-(options.embedded ? 0 : 176));
    const ordered = [...lane.markers];
    const flags = ordered.map(m=>{
      const x=(m.start-zero)/range*width, size=Math.min(170,width);
      return {x,left:Math.max(0,Math.min(width-size,x-size/2)),width:size};
    });
    const close = flags.length>1 && Math.abs(flags[1].left-flags[0].left)<flags[0].width+8;
    const checkpoints=ordered.map((m,i)=>{
      const index=points.indexOf(m);markerGroups[index]=[m];const f=flags[i];
      const caption=`${m.label} · ${((m.start-timeZero)/1e9).toFixed(3)} s`;
      return `<button type="button" class="key-checkpoint pair-endpoint ${i===0?'pair-start':'pair-end'}" data-marker-index="${index}" data-time-seconds="${(m.start-timeZero)/1e9}" aria-label="${esc(title(m))}" title="${esc(title(m))}" style="left:${(m.start-zero)/range*100}%;--point-offset:${f.left-f.x}px;--point-width:${f.width}px;--point-top:${close&&i===1?43:0}px"><i aria-hidden="true"></i><span>${esc(caption)}</span></button>`;
    }).join('');
    const dimensions=(lane.intervals||[]).map(m=>{
      const id=keyIntervals.push(m)-1, low=Math.min(m.start_ns,m.end_ns), high=Math.max(m.start_ns,m.end_ns);
      const caption=keyDuration(m), description=`${m.from_label} → ${m.to_label} · ${caption} · ${m.note}${intervalOrderNote(m)}`;
      const wide=(high-low)/range*width>135;
      return `<button type="button" class="pair-line ${m.clock_basis==='monotonic'?'':'unaligned'}" data-key-interval="${id}" style="left:${(low-zero)/range*100}%;width:${(high-low)/range*100}%" aria-label="${esc(description)}" title="${esc(description)}">${wide?'<span>'+esc(caption)+'</span>':''}</button>`;
    }).join('');
    return `<div class="key-pair" style="height:${close?65:46}px">${dimensions}${checkpoints}</div>${lane.missing?.length?'<div class="key-notes">未采集：'+esc(lane.missing.join('、'))+'</div>':''}`;
  }
  function markers(lane, laneIndex) {
    const width = Math.max(100, (root.clientWidth || 800)-(options.embedded ? 0 : 176));
    const ordered = [...(lane.markers || [])].sort((a,b)=>a.start-b.start);
    if (!ordered.length) return '';
    const expanded = expandedLanes.has(laneIndex);
    const groups = expanded ? ordered.map(marker=>[marker]) : [ordered];
    const captions = groups.map(group=>group.length>1 ? group.length+' 个事件 · 点击查看' : conciseMilestone(group[0]));
    const layout = layoutTimelineFlags(groups.map(group=>({x:(group[0].start-zero)/range*width,width:group.length>1?42:24})),width);
    return '<div class="milestone-track primary-track" style="height:'+layout.rows*25+'px">' + groups.map((group,i) => {
      const index = points.indexOf(group[0]);
      markerGroups[index] = group;
      const summary = group.length>1;
      const caption = summary ? `${group.length} 个事件 · ${((group[0].start-timeZero)/1e9).toFixed(3)} → ${((group.at(-1).start-timeZero)/1e9).toFixed(3)} s · 点击查看全部时间` : title(group[0]);
      const media = !summary && ['image','video'].includes(group[0].kind);
      const flag = layout.items[i];
      const name=group[0].event||'';
      const symbol= /first|partial/.test(name)||/First Token/.test(group[0].label)?'◇': /finished|final|closed|endpoint|last_voice/.test(name)?'□': /started|opened/.test(name)?'○':'·';
      return `<button type="button" class="timeline-marker${media ? ' media-marker' : ''} primary-marker compact-milestone${summary?' event-summary':''} severity-${group.some(m=>m.severity==='error')?'error':group.some(m=>m.severity==='warning')?'warning':'info'}" data-marker-index="${index}" data-time-seconds="${(group[0].start-timeZero)/1e9}" aria-label="${esc(caption)}" title="${esc(caption)}" style="left:${(group[0].start-zero)/range*100}%;top:${flag.row*25}px;--flag-offset:${flag.left-flag.x}px;--flag-width:${flag.width}px;color:${group[0].color || '#405ca3'}"><i class="milestone-anchor" aria-hidden="true"></i><span class="event-symbol" aria-hidden="true">${summary?'◇ '+group.length:media?mediaMarkerIcon(group):symbol}</span><span class="milestone-label">${esc(captions[i])}</span></button>`;
    }).join('') + '</div>';
  }
  function toggleLane(index) {
    if (!Number.isInteger(index) || !lanes[index]) return;
    expandedLanes.has(index) ? expandedLanes.delete(index) : expandedLanes.add(index);
    if (options.onLaneToggle) options.onLaneToggle(index, expandedLanes.has(index));
    else {
      // Keep the plot itself (and its measurement handlers/selection) alive.
      const lane = lanes[index], row = root.querySelector(`[data-lane-index="${index}"]`);
      row.querySelector('.lane-content').innerHTML = laneContent(lane,index);
      row.dataset.expanded=String(expandedLanes.has(index));
      const toggle = row.querySelector('[data-lane-toggle]');
      toggle.setAttribute('aria-expanded',String(expandedLanes.has(index)));
      toggle.querySelector('.lane-chevron').textContent=expandedLanes.has(index)?'▾':'▸';
    }
  }
  function laneContent(lane,index) {
    if (lane.category === 'key_moments') return keyMoments(lane);
    const segments = lane.segments || [lane];
    const keys=segments.filter(item => item.end != null).map(item => {
      const prefix=`<i style="background:${item.color || '#407d77'}"></i>${item.segment_number ? '#' + item.segment_number + ' · ' : ''}${sec(durationSeconds(item))} s`;
      if (!item.tts_evidence) return `<span>${prefix}</span>`;
      const text=item.tts_evidence.status==='matched'?item.tts_evidence.text:'文本未关联';
      return `<button type="button" class="sentence-key" data-sentence-index="${all.indexOf(item)}" title="${esc(title(item)+'\n'+ttsTextDetails(item))}" aria-label="${esc('第 '+item.segment_number+' 段 · '+ttsTextDetails(item))}">${prefix}<span class="sentence-preview">${esc(text)}</span></button>`;
    }).join('');
    const memories=segments.filter(item=>item.memory_evidence).map(item=>item.memory_evidence);
    const memory=expandedLanes.has(index)?memories.map(item=>`<details class="memory-evidence"><summary>${esc(item.label)} · 查看返回内容与采用内容</summary><pre>${esc(memoryDetails(item))}</pre></details>`).join(''):'';
    const knowledge=expandedLanes.has(index)?segments.filter(item=>item.knowledge_evidence).map(item=>`<details class="memory-evidence knowledge-evidence"><summary>知识库 · ${esc(item.knowledge_evidence.label)} · 查看查询与返回内容</summary><pre>${esc(knowledgeDetails(item.knowledge_evidence))}</pre></details>`).join(''):'';
    const missing=(lane.missing || []).length ? '<div class="key-notes">未采集：'+esc(lane.missing.join('、'))+'</div>' : '';
    return `${markers(lane,index)}<div class="track">${segments.map(item=>bar(item,index)).join('')}</div><div class="segment-key${segments.some(item=>item.tts_evidence)?' has-tts-text':''}">${keys}</div>${missing}${memory}${knowledge}`;
  }
  const axis = clock.mode === 'wall' ? [0,range/2,range].map(offset=>`<span>${beijingTime(clock.origin_wall_time_ms+(zero+offset)/1e6)}<small>+${sec(offset/1e9)} s</small></span>`).join('') : `<span>0 s</span><span>${sec(range/1e9/2)} s</span><span>${sec(range/1e9)} s</span>`;
  root.innerHTML = `<div class="timeline-plot">${options.embedded ? '' : '<div class="axis">'+axis+'</div>'}` + lanes.map((lane,index) => {
    return `<div class="lane" data-category="${lane.category || ''}" data-lane-index="${index}" data-expanded="${expandedLanes.has(index)}" data-turn-index="${lane.turn_index ?? ''}" data-source="${lane.source === 'client' ? 'client' : 'server'}">${options.embedded ? '' : '<button type="button" class="lane-label lane-toggle" data-lane-toggle="'+index+'" aria-expanded="'+expandedLanes.has(index)+'"><span class="lane-chevron" aria-hidden="true">'+(expandedLanes.has(index)?'▾':'▸')+'</span>'+traceLabel(lane)+'</button>'}<div class="lane-content">${laneContent(lane,index)}</div></div>`;
  }).join('') + '</div>' + (options.detail ? '' : '<div class="span-detail" role="status" hidden></div>');
  if (!options.embedded) enableTimelineMeasurement(root, range/1e9, bounds.flatMap(item=>[{time:(item.start-zero)/1e9,label:item.label}, ...(item.end != null ? [{time:(item.end-zero)/1e9,label:item.label+"结束"}] : [])]));
  function activate(element) {
    const button = element.closest('[data-event-index], [data-marker-index], [data-lane-toggle], [data-sentence-index], [data-key-interval]');
    if (!button || !root.contains(button)) return false;
    if (button.dataset.laneToggle != null) {toggleLane(Number(button.dataset.laneToggle));return true;}
    const detail = options.detail || root.querySelector('.span-detail');
    if (button.dataset.keyInterval != null) {
      const m=keyIntervals[Number(button.dataset.keyInterval)];
      const duration=m.duration_seconds == null?'不可比较':m.duration_seconds.toFixed(3)+' s';
      detail.textContent=`${m.from_label} → ${m.to_label} · ${duration}\n${m.note}${intervalOrderNote(m)}`;
      detail.hidden=false;
      if(m.clock_basis!=='unavailable') options.onInterval?.(Math.min(m.start_ns,m.end_ns)/1e9,Math.max(m.start_ns,m.end_ns)/1e9);
      return true;
    }
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
        if (item.evidence_note) extra.push(item.evidence_note);
        if (item.position_note) extra.push(item.position_note);
        if (item.severity) extra.push({info:"Info · 信息",warning:"Warning · 警告",error:"Error · 错误"}[item.severity]);
        if (item.since_request_seconds != null) extra.push(`距本次请求提交 ${item.since_request_seconds.toFixed(3)} s`);
        if (data.delta_kind) extra.push(data.delta_kind === 'tool' ? '工具增量' : '正文增量');
        if (data.punctuation) extra.push(`分句标点 ${data.punctuation}`);
        if (data.trigger && data.trigger !== 'punctuation') extra.push(data.trigger === 'stream_end' ? '流结束提交' : '请求边界观测');
        if (data.text_received_ns != null) extra.push(`${item.event==='tts_segment_ready'?'对应文本块入队后':'触发文本入队后'} ${(((item.monotonic_start_ns ?? item.start)-data.text_received_ns)/1e9).toFixed(3)} s`);
        if (item.event==='tts_segment_ready') extra.push('此点记录 TTS 消费线程完成分段，不是 LLM 该句完整可用的准确时刻');
        if (item.tts_evidence) extra.push(ttsTextDetails(item));
        if (data.http_status != null) extra.push(`HTTP ${data.http_status}`);
        if (data.http_request_id) extra.push(`请求 ${data.llm_request_seq ?? '未知'} · HTTP 尝试 ${data.http_request_seq ?? '未知'} · 重试 ${data.retry_count ?? '未知'} · ID ${data.http_request_id}`);
        if (data.connection_state) extra.push(`连接 ${{new:'新建',reused:'复用',unknown:'未知'}[data.connection_state] || '未知'}`);
        if ('transport_retry_count' in data) extra.push(`传输重试 ${data.transport_retry_count ?? '未知'}`);
        if (data.phase) extra.push(data.phase);
        if (['input_tokens','cached_tokens','output_tokens'].some(key=>key in data)) extra.push(`输入 / 缓存 / 输出 Token ${data.input_tokens ?? '未知'} / ${data.cached_tokens ?? '未知'} / ${data.output_tokens ?? '未知'}`);
        if ('requested_silence_duration_ms' in data) extra.push(`请求静音阈值 ${data.requested_silence_duration_ms == null ? '未知 / 不启用' : data.requested_silence_duration_ms /1000 + ' s'}`);
        if ('confirmed_silence_duration_ms' in data) extra.push(`服务端确认阈值 ${data.confirmed_silence_duration_ms == null ? '未知' : data.confirmed_silence_duration_ms /1000 + ' s'}`);
        if (data.error_type) extra.push(data.error_type);
        return [title(item), ...extra, candidateDetails(data), memoryDetails(item.memory_evidence)].filter(Boolean).join(' · ');
      }).join('\n');
    } else {
      const item = all[Number(button.dataset.sentenceIndex ?? button.dataset.eventIndex)];
      const laneIndex = lanes.findIndex(lane=>(lane.segments || [lane]).includes(item));
      const restoreFocus = document.activeElement === button;
      if (button.dataset.sentenceIndex == null) toggleLane(laneIndex);
      if (restoreFocus && button.dataset.sentenceIndex == null) root.querySelector(`[data-event-index="${button.dataset.eventIndex}"]`)?.focus({preventScroll:true});
      detail.textContent = [title(item), configDetails(item.data), ttsTextDetails(item), candidateDetails(item.data), memoryDetails(item.memory_evidence), knowledgeDetails(item.knowledge_evidence), item.data?.purpose === 'rules' ? '规则向量' : item.data?.purpose === 'query' ? '用户问题向量' : '', item.data?.error_type || ''].filter(Boolean).join(' · ');
    }
    detail.hidden = false;
    return true;
  }
  if (!options.embedded) root.onclick = event=>activate(event.target);
  return {activate,toggleLane};
}
if (!turn) renderSessionTimeline($('session-timeline'), report.session_playback);
if (turn) {
  renderReplyTiming($('reply-timing'), turn.reply_timing, report.session_playback, report.turn_index);
  const events = turn.events || [], vas = turn.vas_events || [];
  const vadLabels = {local_vad_speech_started:'本地 VAD · 语音开始', local_vad_last_voice:'本地 VAD · 最后语音帧', local_vad_endpoint_detected:'本地 VAD · 结束', asr_speech_started:'ASR VAD · 语音开始', asr_endpoint_detected:'ASR VAD · 结束', asr_final:'ASR · 最终识别结果'};
  const stageLabels = {memory_request:'Memory', listen_stop_received:'VAS 收到停止', listen_stop_enqueued:'停止消息入队', listen_stop_dequeued:'停止消息出队', listen_finalize_started:'开始处理语音结束', audio_input_completed:'上行音频接收完成', asr_commit_sent:'ASR 提交结束', ...vadLabels};
  renderSessionTimeline($('session-timeline'), report.session_playback);
  const handoff = vas.filter(event => stageLabels[event.event] && event.event !== 'memory_request').sort((a,b) => a.monotonic_ns-b.monotonic_ns);
  const isVad = turn.input_settings?.mode === 'vad';
  const base = handoff.find(event => event.event === (isVad ? 'asr_endpoint_detected' : 'listen_stop_received'))?.monotonic_ns;
  table('handoff', ['事件', isVad ? '相对 ASR VAD 结束 / s' : '相对 VAS 收到停止 / s', '详情'], handoff.map(event => [stageLabels[event.event], base == null ? '—' : sec((event.monotonic_ns-base)/1e9), secondsView(event.data)]));
  table('spans', ['请求', '耗时 / s', '状态', '详情'], (turn.spans || []).map(span => [stageLabels[span.name] || span.name, ms(span.duration_ms), span.status, secondsView(span.data)]));
  table('checks', ['断言', '期望', '实际', '结果', '失败分类 / 原因'], (turn.checks || []).map(check => [({recognition:'识别',tools:'工具',reply:'回复',audio:'音频'}[check.category] || {image_items_min:'至少收到图片数',video_items_min:'至少收到视频数'}[check.name] || check.name.replace(/_ms(?=_|$)/,'_seconds')), checkExpectation(check), checkActual(check), ({passed:'通过',failed:'失败',unknown:'未知',not_applicable:'不适用'}[check.status] || (check.passed ? '通过' : '失败')), [({functional:'功能失败',diagnostic_missing:'诊断缺失',latency:'延迟超标',configuration_unverified:'配置未核实'}[check.failure_kind] || ''), check.reason || ''].filter(Boolean).join(' · ')]));
  $('raw').textContent = JSON.stringify(secondsView({interruption:turn.interruption, client:events, vas}), null, 2);
}
