function syncTimelineRowHeights(rows, labels) {
  rows.forEach((row,index)=>{row.style.minHeight=labels[index].style.minHeight='';});
  const heights=rows.map((row,index)=>row.hidden?0:Math.max(58,row.getBoundingClientRect().height,labels[index].getBoundingClientRect().height));
  rows.forEach((row,index)=>{row.style.minHeight=labels[index].style.minHeight=heights[index]+'px';});
  return heights.reduce((sum,value)=>sum+value,0);
}

function sessionInputRanges(row, markers = []) {
  const segments = (row.input_segments || []).filter(s=>Number.isFinite(s.start_seconds) && Number.isFinite(s.end_seconds) && s.end_seconds > s.start_seconds);
  if (!segments.length) return [];
  const first = Math.min(...segments.map(s=>s.start_seconds));
  const last = Math.max(...segments.map(s=>s.end_seconds));
  const ptt = kind=>markers.find(m=>m.turn_index === row.index && m.kind === kind && Number.isFinite(m.at_seconds))?.at_seconds;
  const start = ptt('ptt_start'), stop = ptt('ptt_stop');
  // This is the recorded input interval, not a reconstruction of packet pacing.
  // Keep packet-level data untouched for drill-down and audio reconstruction.
  if (start != null || stop != null) {
    const begin = start ?? first, end = stop ?? last;
    return end > begin ? [{kind:'speech',start_seconds:begin,end_seconds:end,input_scope:'ptt',stop_recorded:stop != null}] : [];
  }
  const speech = segments.filter(s=>s.kind !== 'background');
  if (!speech.length) return [{kind:'background',start_seconds:first,end_seconds:last}];
  const begin = Math.min(...speech.map(s=>s.start_seconds));
  const end = Math.max(begin,row.input_end_seconds ?? Math.max(...speech.map(s=>s.end_seconds)));
  return [
    ...(first < begin ? [{kind:'background',start_seconds:first,end_seconds:begin}] : []),
    ...(end > begin ? [{kind:'speech',start_seconds:begin,end_seconds:end,input_scope:'utterance'}] : []),
    ...(last > end ? [{kind:'background',start_seconds:end,end_seconds:last}] : []),
  ];
}

function sessionTimelineScale(session) {
  const duration = Math.max(0,Number(session.duration_seconds) || 0);
  const start = session.view_start_seconds ?? Math.min(0,session.vas_timeline?.axis_start_seconds ?? 0);
  const end = Math.max(start+.01,session.view_end_seconds ?? Math.max(duration,session.vas_timeline?.axis_end_seconds ?? duration));
  const extent = Math.max(.01,end-start);
  const clamp = value=>Math.max(start,Math.min(end,Number(value) || 0));
  return {duration,start,end,extent,clamp,
    audioTime:value=>Math.max(0,Math.min(duration,Number(value) || 0)),
    percent:value=>(clamp(value)-start)/extent*100,
    atFraction:value=>clamp(start+value*extent),
  };
}
function timelineLaneVisible(category, mode) {
  return mode === 'all' || (mode === 'details' ? category !== 'key_moments' : category === 'key_moments');
}
function timelineViewMode(preferred, lanes) {
  const mode = ['key','details','all'].includes(preferred) ? preferred : 'key';
  return mode === 'key' && !lanes.some(lane=>lane.category==='key_moments') ? 'details' : mode;
}
function renderSessionTimeline(target, session = {}) {
  if (!target) return;
  const canvas = document.getElementById('session-canvas');
  const viewport = document.getElementById('session-viewport');
  const audio = document.getElementById('session-player');
  const detail = document.getElementById('session-detail');
  const startInput = document.getElementById('measure-start');
  const endInput = document.getElementById('measure-end');
  const result = document.getElementById('measure-duration');
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const seconds = value => value == null ? '—' : Number(value).toFixed(2) + ' s';
  const scale = sessionTimelineScale(session);
  const {duration,extent,percent,clamp} = scale;
  const trace = session.vas_timeline || {lanes:[]};
  const vasLanes = traceLanes(trace.lanes || [], !session.view_turn_index);
  const clockCaption = time=>trace.mode === 'wall' ? ' · 北京时间 '+beijingTime(trace.origin_wall_time_ms+time*1000,true) : '';
  const kindLabels = {greeting:'欢迎语',answer:'正式回复',pre_speech:'过渡语',filler:'临时回复',unknown:'类型未关联',mixed:'混合回复'};
  const waitLabels = {first_reply:'等待首句',first_packet:'等待首包',interrupted:'等待中打断',no_reply:'未收到回复',transition:'等待正式回复',sentence:'句间等待'};
  const markerLabels = {sensor_sent:'传感器触发',input_end:'输入结束',ptt_start:'PTT 语音开始',ptt_stop:'PTT 语音结束',first_received:'首包到达',first_playback:'开始播放',abort:'打断'};
  const pttDetails = {
    ptt_start:'客户端已发送 listen/start（manual），开始本轮语音输入。此点记录开始指令的发送时刻。',
    ptt_stop:'客户端已发送 listen/stop，结束本轮语音输入。此点记录停止指令的发送时刻，不代表 VAS 已完成识别。',
  };
  const items = [];
  const turns = session.turns || [];
  const mediaPoints = (session.media_markers || []).filter(row=>Number.isFinite(row.at_seconds) && row.at_seconds >= scale.start && row.at_seconds <= scale.end);
  let mediaGroups = [];
  canvas.classList.toggle('has-media-markers', mediaPoints.length > 0);
  target.classList.toggle('has-vas',vasLanes.length > 0);
  let zoom = session.view_turn_index ? 1 : Math.max(1,Math.min(64,extent*18/Math.max(1,viewport.clientWidth))), selection = null, gesture = null, pendingSeek = null;
  let animation = null, lastAutoScroll = 0;
  let fullAudioRequest = null, fullAudioUrl = null;
  let selectedTurn = null, looping = false, inspectedTime = null, snapEnabled = true;
  const expandedLanes = new Set();
  let preferredView = null;
  try {preferredView = localStorage.getItem('voice-lab.timeline-view');} catch { /* Offline reports can disallow storage. */ }
  let viewMode = timelineViewMode(preferredView, vasLanes);
  target.querySelector('.session-tools').insertAdjacentHTML('afterend', `<div class="friendly-tools">
    <button data-friendly="expand" aria-pressed="false">展开时间轴</button><button data-friendly="snap" aria-pressed="true">磁吸：开</button><button data-friendly="current">定位当前轮</button>
    <label class="timeline-view-picker">显示 <select data-timeline-view aria-label="时间轴显示内容"><option value="key">只看关键点</option><option value="details">只看详细环节</option><option value="all">全部显示</option></select></label>
    <button data-friendly="zoom-selection">放大选区</button><button data-friendly="play-selection">播放选区</button>
    <button data-friendly="loop" aria-pressed="false">循环选区</button>
    <label>长等待 ≥ <input data-wait-threshold type="number" min="0" step="0.5" value="3"> s</label>
    <button data-friendly="next-wait">下一处长等待</button>
    <span>Alt 暂停磁吸 · 空格播放 · +/− 缩放 · Esc 清除</span><span class="event-legend">单击时间条展开 / 折叠事件 · ○ 开始 · ◇ 首个输出 · □ 结束</span><output class="snap-status" aria-live="polite"></output></div>
    <div class="wait-shortcuts" aria-label="等待区间"></div>`);

  function highlightTurn(index) {
    selectedTurn = index;
    canvas.querySelectorAll('[data-turn-index], [data-session-item]').forEach(node=> {
      const turnIndex = node.dataset.turnIndex || items[Number(node.dataset.sessionItem)]?.turn_index;
      node.classList.toggle('is-muted-turn', Boolean(index && turnIndex && Number(turnIndex)!==index));
    });
    target.querySelectorAll('.session-vas-label').forEach((node,i)=>node.classList.toggle('is-muted-turn',Boolean(index && vasLanes[i]?.turn_index && vasLanes[i].turn_index!==index)));
  }

  startInput.min = endInput.min = String(scale.start);
  startInput.max = endInput.max = String(scale.end);
  function itemButton(item, className, caption, contents = '') {
    const id = items.push(item) - 1;
    const span = item.end_seconds - item.start_seconds;
    const label = `第 ${item.turn_index} 轮 · ${item.label} · ${seconds(item.start_seconds)} → ${seconds(item.end_seconds)} · ${seconds(span)}${clockCaption(item.start_seconds)}${item.text ? ' · ' + item.text : ''}`;
    return `<button type="button" class="session-range ${className}" data-session-item="${id}" style="left:${percent(item.start_seconds)}%;width:${Math.max(0, span/extent*100)}%" title="${escape(label)}" aria-label="${escape(label)}">${contents}<span>${escape(caption)}</span></button>`;
  }
  let inputBars = '', replyBars = '', turnLines = '', markers = scale.start <= 0 && scale.end >= 0 ? '<span class="connection-flag" style="left:'+percent(0)+'%" title="客户端开始尝试连接 · 0 s">连接开始</span>' : '';
  turns.forEach(row => {
    const start = row.input_start_seconds ?? row.start_seconds;
    const hasPttStart = (session.markers || []).some(marker=>marker.turn_index===row.index && marker.kind==='ptt_start');
    if (start != null && !hasPttStart) turnLines += `<span class="session-turn-line" style="left:${percent(start)}%"><button type="button" data-session-turn="${row.index}" title="第 ${row.index} 轮 · ${escape(row.text)}">${String(row.index).padStart(2,'0')}</button></span>`;
    sessionInputRanges(row, session.markers).forEach(segment => {
      if (segment.start_seconds == null || segment.end_seconds == null) return;
      const background = segment.kind === 'background';
      inputBars += itemButton({...segment, turn_index:row.index, text:row.text, label:background ? '持续底噪' : '用户输入', item_type:'input'}, background ? 'range-background' : 'range-input', background ? '底噪' : row.text || `输入 ${row.index}`);
    });
  });
  (session.waits || []).forEach(row => {
    if (row.start_seconds == null || row.end_seconds == null) return;
    replyBars += itemButton({...row, item_type:'wait', label:waitLabels[row.kind] || '等待'}, 'range-wait', `${waitLabels[row.kind] || '等待'} ${seconds(row.end_seconds-row.start_seconds)}`);
  });
  (session.segments || []).forEach(row => {
    if (row.start_seconds == null || row.end_seconds == null) return;
    const temporary = ['pre_speech','filler'].includes(row.kind);
    const unknown = ['unknown','mixed'].includes(row.kind);
    const span = row.end_seconds-row.start_seconds;
    const intervals = (row.intervals || []).filter(interval => interval.end_seconds > interval.start_seconds);
    const fills = intervals.length && span > 0 ? `<svg class="playback-intervals" viewBox="0 0 1000 40" preserveAspectRatio="none" aria-hidden="true">${intervals.map(interval => `<rect x="${Math.max(0,(interval.start_seconds-row.start_seconds)/span*1000)}" y="0" width="${Math.max(0,(Math.min(row.end_seconds,interval.end_seconds)-Math.max(row.start_seconds,interval.start_seconds))/span*1000)}" height="40"/>`).join('')}</svg>` : '';
    replyBars += itemButton({...row, item_type:'reply', label:kindLabels[row.kind] || '回复'}, `range-reply ${temporary ? 'range-temporary' : unknown ? 'range-unknown' : 'range-answer'} ${fills ? 'has-intervals' : ''}`, `${kindLabels[row.kind] || '回复'} · ${row.text || '#' + row.index}`, fills);
  });
  (session.markers || []).forEach(row => {
    if (row.at_seconds == null) return;
    const isPtt = Boolean(pttDetails[row.kind]);
    const label = `第 ${row.turn_index} 轮 · ${markerLabels[row.kind] || row.kind} · ${Number(row.at_seconds).toFixed(isPtt ? 3 : 2)} s${clockCaption(row.at_seconds)}`;
    const caption = (isPtt ? `${String(row.turn_index).padStart(2,'0')} · ` : '') + (markerLabels[row.kind] || row.kind);
    markers += `<button type="button" class="session-marker marker-${escape(row.kind)}" data-session-marker="${escape(row.kind)}" data-session-time="${Number(row.at_seconds)}" data-session-turn-index="${row.turn_index}" style="left:${percent(row.at_seconds)}%" title="${escape(label)}" aria-label="${escape(label)}"><i></i><span>${escape(caption)}</span></button>`;
  });
  const pinnedHeight = 150;
  let vasTop = pinnedHeight+26;
  const vasContent = vasLanes.length ? `<div class="session-vas-heading" style="top:${vasTop-32}px">关键时序 · 每行比较两个点 · 点击连线测量</div><div class="session-vas" id="session-vas" style="top:${vasTop}px"></div>` : '';
  target.style.setProperty('--session-pinned-height', pinnedHeight+'px');
  canvas.innerHTML = `<div class="session-pinned-tracks"><div class="session-ruler" id="session-ruler" aria-hidden="true"></div>${turnLines}<div class="session-input-track">${inputBars}</div><div class="session-reply-track">${replyBars}</div>${markers}</div><div class="session-media-markers"></div><div class="session-grid" aria-hidden="true"></div>${vasContent}<div class="session-selection" id="session-selection" hidden><button type="button" class="measure-handle handle-start" data-measure-handle="start" aria-label="调整测量起点"></button><span id="selection-caption"></span><button type="button" class="measure-handle handle-end" data-measure-handle="end" aria-label="调整测量终点"></button></div><div class="session-playhead" id="session-playhead" aria-hidden="true"><span>0.00 s</span></div>`;
  let vasController = null;
  const labelRoot = document.getElementById('session-track-labels');
  {
    labelRoot.innerHTML = `<div class="session-pinned-labels"><span>用户语音</span><span>回复播放</span></div><div class="session-vas-labels" style="top:${vasTop}px">${vasLanes.map((lane,index)=>'<div class="session-vas-label"><button class="lane-toggle" data-lane-toggle="'+index+'" aria-label="'+escape(lane.label)+' · 展开或折叠事件" aria-expanded="false"><span class="lane-chevron" aria-hidden="true">▸</span>'+traceLabel(lane)+'</button></div>').join('')}</div>`;
  }
  const snapPoints = [
    ...items.flatMap(item=>[{time:item.start_seconds,label:item.label+'开始'},{time:item.end_seconds,label:item.label+'结束'}]),
    ...(session.markers||[]).map(m=>({time:m.at_seconds,label:markerLabels[m.kind]||m.kind})),
    ...mediaPoints.map(m=>({time:m.at_seconds,label:m.label})),
    ...vasLanes.flatMap(lane=>[
      ...lane.segments.flatMap(span=>[{time:span.start/1e9,label:lane.label+'开始',category:lane.category},{time:span.end/1e9,label:lane.label+'结束',category:lane.category}]),
      ...lane.markers.map(m=>({time:m.start/1e9,label:m.label,category:lane.category})),
    ]),
  ].filter(p=>Number.isFinite(p.time)&&p.time>=scale.start&&p.time<=scale.end);
  const overlay = document.getElementById('session-selection');
  const caption = document.getElementById('selection-caption');
  const playhead = document.getElementById('session-playhead');
  function drawSelection(updateInputs = true) {
    overlay.hidden = !selection;
    if (!selection) {
      startInput.value = endInput.value = '';
      result.value = result.textContent = 'Δ — s';
      return;
    }
    const [start, end] = selection;
    overlay.style.left = percent(start) + '%';
    overlay.style.width = (end-start)/extent*100 + '%';
    caption.textContent = `Δ ${seconds(end-start)}`;
    result.value = result.textContent = `Δ ${seconds(end-start)}`;
    if (updateInputs) {
      startInput.value = start.toFixed(2);
      endInput.value = end.toFixed(2);
    }
    overlay.querySelectorAll('[data-measure-handle]').forEach(handle => {
      const value = handle.dataset.measureHandle === 'start' ? start : end;
      handle.setAttribute('aria-label', `调整测量${handle.dataset.measureHandle === 'start' ? '起点' : '终点'}，${seconds(value)}，方向键微调`);
    });
  }
  function revealTime(time) {
    const x = percent(time)/100*canvas.clientWidth;
    if (x < viewport.scrollLeft+20 || x > viewport.scrollLeft+viewport.clientWidth-50) viewport.scrollLeft = Math.max(0,x-viewport.clientWidth*.3);
  }
  function positionPlayhead(time, follow = false) {
    const value = clamp(time);
    playhead.style.left = percent(value) + '%';
    playhead.querySelector('span').textContent = seconds(value);
    playhead.querySelector('span').style.transform = `translateX(${percent(value) < 1 ? '0' : percent(value) > 99 ? '-100%' : '-50%'})`;
    if (follow && !gesture && performance.now()-lastAutoScroll>800) {
      revealTime(value);
      lastAutoScroll = performance.now();
    }
  }
  function seek(time) {
    const value = scale.audioTime(time);
    pendingSeek = value;
    const seekable = audio && Array.from({length:audio.seekable.length}, (_, index) => [audio.seekable.start(index),audio.seekable.end(index)]).some(([start,end]) => value >= start && value <= end);
    if (audio && audio.readyState >= 1 && (value === 0 || seekable || fullAudioUrl)) {
      audio.currentTime = Math.min(value, Number.isFinite(audio.duration) ? audio.duration : value);
      pendingSeek = null;
    } else if (audio && audio.readyState >= 1 && !fullAudioRequest && /^https?:$/.test(location.protocol)) {
      const notice = document.createElement('span');
      notice.className = 'audio-preparing';
      notice.setAttribute('role','status');
      notice.textContent = '正在准备完整回放…';
      target.querySelector('.session-player-row').append(notice);
      // Simple static servers may omit byte ranges. A complete local blob supports
      // reliable seeking while file:// and range-capable servers stay native.
      fullAudioRequest = fetch(audio.currentSrc || audio.src).then(response => {
        if (!response.ok) throw new Error('audio_download_failed');
        return response.blob();
      }).then(blob => {
        // Native metadata/seek events may already have fulfilled a newer seek.
        // Preserve that position before replacing the source resets the player.
        pendingSeek = pendingSeek ?? audio.currentTime;
        const resume = !audio.paused;
        fullAudioUrl = URL.createObjectURL(blob);
        audio.src = fullAudioUrl;
        audio.load();
        if (resume) audio.play().catch(() => {});
        notice.remove();
      }).catch(() => {
        notice.textContent = '音频尚未准备完成，请稍后重新定位或下载完整报告后打开。';
        fullAudioRequest = null;
      });
    }
    positionPlayhead(value);
    revealTime(value);
  }
  function showItem(item) {
    highlightTurn(item.turn_index);
    canvas.querySelectorAll('[data-session-item]').forEach(button => button.classList.toggle('is-selected', items[Number(button.dataset.sessionItem)] === item));
    const row = turns.find(turn => turn.index === item.turn_index);
    canvas.querySelectorAll('.wait-overlap').forEach(node=>node.classList.remove('wait-overlap'));
    let body = item.text ? `<p>${escape(item.text)}</p>` : '';
    if (item.item_type === 'input' && item.kind !== 'background') {
      body += `<small>${item.input_scope === 'ptt' ? '本次按住说话的完整录音区间，包含期间静音。' : '本次语音合并显示，包含句内停顿。'}原始发包时间保持不变。${item.stop_recorded === false ? ' 未记录停止信号，结束位置取最后采集音频。' : ''}</small>`;
    }
    if (item.item_type === 'wait') {
      const descriptions = {first_reply:'输入结束 → 首句开始播放',first_packet:'输入结束 → 客户端收到首包音频。播放时钟异常，不能据此确定实际首音时间。',interrupted:'输入结束 → 客户端发出打断；这段等待内未收到回复音频。',no_reply:'输入结束 → 本轮最后一个客户端记录；截至该时刻未收到回复音频。',transition:'过渡 / 临时回复播完 → 正式回复开始播放',sentence:'上一段播完 → 下一段开始播放'};
      selection=[item.start_seconds,item.end_seconds];drawSelection();
      const evidence=waitEvidence(item,vasLanes);
      evidence.forEach(stage=>canvas.querySelectorAll('#session-vas .lane')[stage.index]?.classList.add('wait-overlap'));
      body = `<p>${escape(descriptions[item.kind] || '未播放声音的等待区间')}</p><div class="wait-evidence">${evidence.length ? evidence.map(stage=>`<button data-wait-lane="${stage.index}">${escape(stage.label.replace(/^第 \d+ 轮 · /,''))} · ${seconds(stage.overlap)}</button>`).join('') : '<span>该窗口内没有可对应的 VAS 请求记录。</span>'}</div><small>同窗阶段仅供定位，不能单凭重叠判定阻塞原因；跨端精度请查看页面校准状态。</small>`;
    }
    const stats = item.item_type === 'input' && row ? `<div class="selected-turn-metrics"><span>输入结束 <b>${seconds(row.input_end_seconds)}</b></span><span>首包到达 <b>${seconds(row.first_received_seconds)}</b></span><span>首句播放 <b>${seconds(row.first_playback_seconds)}</b></span></div>` : '';
    detail.innerHTML = `<div class="session-detail-heading"><span class="badge">第 ${item.turn_index} 轮</span><strong>${escape(item.label)}</strong><span class="detail-time">${seconds(item.start_seconds)} → ${seconds(item.end_seconds)} <b>· ${seconds(item.end_seconds-item.start_seconds)}</b>${escape(clockCaption(item.start_seconds))}</span><a href="turn-${String(item.turn_index).padStart(3,'0')}.html">内部时序 ↗</a></div>${body}${stats}`;
  }
  function activate(element, time) {
    if (element.closest('#session-vas')) {
      // VAS timestamps are uncalibrated: inspecting them must not seek audio.
      const exactMarker=element.closest('[data-time-seconds]');
      if(exactMarker)time=Number(exactMarker.dataset.timeSeconds);
      const turnIndex=element.closest('[data-turn-index]')?.dataset.turnIndex;
      vasController?.activate(element);
      if(Number.isFinite(time)){inspectedTime=time;positionPlayhead(time);}
      if (turnIndex) highlightTurn(Number(turnIndex));
      return;
    }
    const mediaButton = element.closest('[data-session-media]');
    if (mediaButton) {
      const group = mediaGroups[Number(mediaButton.dataset.sessionMedia)];
      detail.innerHTML = mediaMarkerDetails(group, marker=>`第 ${marker.turn_index} 轮期间 · ${marker.label} · ${seconds(marker.at_seconds)}`);
      seek(group[0].at_seconds);
      return;
    }
    const itemButton = element.closest('[data-session-item]');
    const marker = element.closest('[data-session-marker]');
    const turnButton = element.closest('[data-session-turn]');
    if (itemButton) {
      const item = items[Number(itemButton.dataset.sessionItem)];
      showItem(item);
      seek(time ?? item.start_seconds);
    } else if (marker) {
      const row = turns.find(turn => turn.index === Number(marker.dataset.sessionTurnIndex));
      const at = Number(marker.dataset.sessionTime);
      const kind = marker.dataset.sessionMarker;
      const explanation = pttDetails[kind];
      detail.innerHTML = `<div class="session-detail-heading"><span class="badge">第 ${escape(marker.dataset.sessionTurnIndex)} 轮</span><strong>${escape(markerLabels[kind])}</strong><span class="detail-time">${explanation ? at.toFixed(3)+' s' : seconds(at)}${escape(clockCaption(at))}</span></div>${explanation ? `<p>${escape(explanation)}</p>` : ''}${row?.input_end_seconds != null && ['first_received','first_playback'].includes(kind) ? `<p>输入结束后 <strong>${seconds(at-row.input_end_seconds)}</strong></p>` : ''}`;
      seek(at);
    } else if (turnButton) {
      const row = turns.find(turn => turn.index === Number(turnButton.dataset.sessionTurn));
      if (row) {
        showItem({...row,turn_index:row.index,item_type:'input',label:'用户输入',start_seconds:row.input_start_seconds ?? row.start_seconds,end_seconds:row.input_end_seconds});
        seek(row.input_start_seconds ?? row.start_seconds);
      }
    } else seek(time ?? 0);
  }
  function timeAt(event) {
    const bounds = canvas.getBoundingClientRect();
    const raw=scale.atFraction((event.clientX-bounds.left)/bounds.width);
    const hit=timelineSnap(raw,snapPoints.filter(p=>!p.category||timelineLaneVisible(p.category,viewMode)),bounds.width/extent,event.altKey||!snapEnabled);
    target.querySelector('.snap-status').textContent=hit.point ? '已吸附 · '+hit.point.label+' · '+seconds(hit.time) : '';
    playhead.classList.toggle('is-snapped',Boolean(hit.point));
    return hit.time;
  }
  canvas.addEventListener('pointerdown', event => {
    // Native disclosures and text selection must not become timeline gestures.
    if (event.target.closest('details')) return;
    if (event.pointerType === 'touch' || event.button !== 0 || !extent) return;
    inspectedTime=null;
    const handle = event.target.closest('[data-measure-handle]');
    gesture = {pointerId:event.pointerId,clientX:event.clientX,start:timeAt(event),target:event.target,handle:handle?.dataset.measureHandle,selection:selection?.slice(),dragged:false};
    canvas.setPointerCapture(event.pointerId);
  });
  canvas.addEventListener('pointermove', event => {
    if (!gesture || gesture.pointerId !== event.pointerId) return;
    if (Math.abs(event.clientX-gesture.clientX) < 4 && !gesture.dragged) return;
    gesture.dragged = true;
    const time = timeAt(event);
    const anchor = gesture.handle && gesture.selection ? gesture.selection[gesture.handle === 'start' ? 1 : 0] : gesture.start;
    selection = [Math.min(anchor,time),Math.max(anchor,time)];
    drawSelection();
  });
  canvas.addEventListener('pointerup', event => {
    if (!gesture || gesture.pointerId !== event.pointerId) return;
    const completed = gesture;
    gesture = null;
    if (canvas.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId);
    if (!completed.dragged && !completed.handle) activate(completed.target,timeAt(event));
  });
  canvas.addEventListener('pointercancel', () => {if(gesture){selection=gesture.selection;const id=gesture.pointerId;gesture=null;if(canvas.hasPointerCapture(id))canvas.releasePointerCapture(id);drawSelection();}});
  canvas.addEventListener('click', event => {
    if (event.target.closest('details')) return;
    if (event.detail !== 0 || event.target.closest('[data-measure-handle]')) return;
    activate(event.target);
  });
  target.addEventListener('keydown', event => {
    if (event.target.closest('details,input,textarea,select,[contenteditable="true"]')) return;
    if (event.code === 'Space' && event.target.tagName !== 'BUTTON' && audio) {
      event.preventDefault(); audio.paused ? audio.play().catch(()=>{}) : audio.pause(); return;
    }
    if (['+','=','-','0'].includes(event.key)) {
      event.preventDefault(); target.querySelector('[data-session-zoom="'+(event.key==='0'?'fit':event.key==='-'?'out':'in')+'"]').click(); return;
    }
    if (event.key === 'Escape') {if(target.classList.contains('timeline-expanded')){target.querySelector('[data-friendly=expand]').click();return;}selection = null;looping=false;highlightTurn(null);drawSelection();return;}
    const handle = event.target.closest('[data-measure-handle]');
    if (handle && selection && ['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) {
      event.preventDefault();
      const index = handle.dataset.measureHandle === 'start' ? 0 : 1;
      const delta = (event.key === 'ArrowLeft' ? -1 : 1) * (event.shiftKey ? 1 : .01);
      const value = event.key === 'Home' ? scale.start : event.key === 'End' ? scale.end : selection[index]+delta;
      selection[index] = index === 0 ? Math.min(selection[1],clamp(value)) : Math.max(selection[0],clamp(value));
      drawSelection();
    } else if (['ArrowLeft','ArrowRight'].includes(event.key)) {
      event.preventDefault();
      seek((pendingSeek ?? audio?.currentTime ?? 0)+(event.key === 'ArrowLeft' ? -1 : 1));
    }
  });
  [startInput,endInput].forEach(input => {
    const update = normalize => {
      if (startInput.value === '' || endInput.value === '') return;
      const start = clamp(startInput.value), end = clamp(endInput.value);
      selection = [Math.min(start,end),Math.max(start,end)];
      drawSelection(normalize);
      if (normalize) revealTime(selection[0]);
    };
    input.addEventListener('input', () => update(false));
    input.addEventListener('change', () => update(true));
  });
  document.getElementById('measure-clear').addEventListener('click', () => {selection = null;drawSelection();});
  function redrawScale() {
    const width = Math.max(1,viewport.clientWidth);
    canvas.style.width = width*zoom + 'px';
    canvas.querySelectorAll('.marker-ptt_start,.marker-ptt_stop').forEach(marker=> {
      const label = marker.querySelector('span');
      const x = percent(Number(marker.dataset.sessionTime))/100*width*zoom;
      label.style.left = Math.max(8-x,Math.min(10,width*zoom-x-label.offsetWidth+6))+'px';
    });
    mediaGroups = groupMediaMarkers(mediaPoints, extent, width*zoom);
    const mediaLayout=layoutTimelineFlags(mediaGroups.map(group=>({x:percent(group[0].at_seconds)/100*width*zoom,width:28})),width*zoom);
    vasTop=pinnedHeight+26+(mediaGroups.length?mediaLayout.rows*30+8:0);
    if(vasLanes.length){const heading=canvas.querySelector('.session-vas-heading');heading.style.top=(vasTop-26)+'px';heading.textContent=viewMode==='key'?'关键时序 · 每行比较两个点 · 点击连线测量':viewMode==='details'?'详细环节 · 点击时间条展开事件':'关键点与详细环节';canvas.querySelector('#session-vas').style.top=vasTop+'px';labelRoot.querySelector('.session-vas-labels').style.top=vasTop+'px';}
    canvas.querySelector('.session-media-markers').innerHTML = mediaGroups.map((group,index)=> {
      const caption = group.map(marker=>`第 ${marker.turn_index} 轮期间 · ${marker.label} · ${seconds(marker.at_seconds)}`).join(' / ');
      return `<button type="button" class="session-media-marker" data-session-media="${index}" style="left:${percent(group[0].at_seconds)}%;top:${mediaLayout.items[index].row*30}px" title="${escape(caption)}" aria-label="${escape(caption)}">${mediaMarkerIcon(group)}</button>`;
    }).join('');
    const ruler = document.getElementById('session-ruler');
    const desired = extent/(width*zoom/95);
    const base = 10**Math.floor(Math.log10(Math.max(.01,desired)));
    const step = [1,2,5,10].map(value => value*base).find(value => value >= desired) || base*10;
    let ticks = '';
    for (let t=Math.ceil(scale.start/step)*step;t<=scale.end;t+=step) ticks += `<span style="left:${percent(t)}%">${Number(t.toFixed(2))} s${trace.mode === 'wall' ? '<small>'+beijingTime(trace.origin_wall_time_ms+t*1000)+'</small>' : ''}</span>`;
    ruler.innerHTML = ticks;
    canvas.style.setProperty('--grid-step',step/extent*100+'%');
    canvas.style.setProperty('--grid-offset',percent(Math.ceil(scale.start/step)*step)+'%');
    if (vasLanes.length) {
      vasController = timeline('session-vas',vasLanes,trace,{embedded:true,start_ns:scale.start*1e9,end_ns:scale.end*1e9,time_zero_ns:0,detail,expandedLanes,onLaneToggle:()=>redrawScale(),onInterval:(start,end)=>{selection=[start,end];drawSelection();}});
      const labels = [...labelRoot.querySelectorAll('.session-vas-label')];
      const rows = [...canvas.querySelectorAll('#session-vas .lane')];
      labels.forEach((label,index)=>{
        label.hidden=rows[index].hidden=!timelineLaneVisible(vasLanes[index].category,viewMode);
        label.style.minHeight='';
        const button=label.querySelector('[data-lane-toggle]');
        if(vasLanes[index].category==='key_moments') {
          button.disabled=true;button.classList.add('key-lane-label');
          button.removeAttribute('aria-expanded');button.setAttribute('aria-label',vasLanes[index].label+' · 始终展开');
          button.querySelector('.lane-chevron').textContent='◆';return;
        }
        button.setAttribute('aria-expanded',String(expandedLanes.has(index)));
        button.querySelector('.lane-chevron').textContent=expandedLanes.has(index)?'▾':'▸';
      });
      canvas.style.height = vasTop+syncTimelineRowHeights(rows,labels)+24+'px';
      labelRoot.style.height=canvas.style.height;
      labelRoot.parentElement.style.height=viewport.clientHeight+'px';
    }
    if(!vasLanes.length){canvas.style.height=(vasTop+16)+'px';labelRoot.style.height=canvas.style.height;}
    canvas.querySelectorAll('.session-range').forEach(button => {
      button.classList.toggle('compact-range',button.clientWidth < 65);
    });
    highlightTurn(selectedTurn);
    drawSelection(false);
    positionPlayhead(inspectedTime ?? pendingSeek ?? audio?.currentTime ?? 0);
    target.querySelector('[data-session-zoom="out"]').disabled = zoom <= 1;
    target.querySelector('[data-session-zoom="in"]').disabled = zoom >= 64;
    syncPinnedTracks();
  }
  function applyZoom(value, clientX, fromX = clientX) {
    const bounds=viewport.getBoundingClientRect();
    const anchor=Number.isFinite(fromX) ? fromX-bounds.left : viewport.clientWidth/2;
    const destination=Number.isFinite(clientX) ? clientX-bounds.left : viewport.clientWidth/2;
    const fraction=(viewport.scrollLeft+anchor)/canvas.clientWidth;
    zoom=Math.max(1,Math.min(64,value));
    redrawScale();
    viewport.scrollLeft=Math.max(0,Math.min(canvas.clientWidth-viewport.clientWidth,fraction*canvas.clientWidth-destination));
    lastAutoScroll=performance.now()+1000;
  }
  bindTimelineGestures(viewport, {
    getZoom:()=>zoom,zoom:applyZoom,
    cancelSelection:()=>{
      if(gesture){selection=gesture.selection;const id=gesture.pointerId;gesture=null;if(canvas.hasPointerCapture(id))canvas.releasePointerCapture(id);drawSelection();}
      lastAutoScroll=performance.now()+1000;
    },
    tap:(element,x)=>activate(element,timeAt({clientX:x})),
  });
  target.querySelectorAll('[data-session-zoom]').forEach(button => button.addEventListener('click', () => {
    applyZoom(button.dataset.sessionZoom === 'fit' ? 1 : zoom*(button.dataset.sessionZoom === 'in' ? 2 : .5));
  }));
  document.querySelectorAll('[data-session-seek]').forEach(link => link.addEventListener('click', event => {
    event.preventDefault();
    const time = Number(link.dataset.sessionSeek);
    seek(time);
    const row = turns.find(turn => (turn.input_start_seconds ?? turn.start_seconds) === time);
    if (row) showItem({...row,turn_index:row.index,item_type:'input',label:'用户输入',start_seconds:time,end_seconds:row.input_end_seconds});
    target.scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',block:'start'});
  }));
  const shortcuts = target.querySelector('.wait-shortcuts');
  (session.waits || []).filter(w=>w.kind!=='sentence').sort((a,b)=>(b.end_seconds-b.start_seconds)-(a.end_seconds-a.start_seconds)).slice(0,session.view_turn_index?2:6).forEach(wait=> {
    const button = document.createElement('button');
    button.textContent = `第 ${wait.turn_index} 轮 · ${wait.kind==='first_reply'?'说完 → 首声':wait.kind==='transition'?'临时结束 → 正式开始':waitLabels[wait.kind]} · ${seconds(wait.end_seconds-wait.start_seconds)}`;
    button.onclick=()=>{viewport.scrollTop=0;selection=[wait.start_seconds,wait.end_seconds];drawSelection();showItem({...wait,label:waitLabels[wait.kind],item_type:'wait'});seek(wait.start_seconds);};
    shortcuts.append(button);
  });
  target.querySelectorAll('[data-friendly]').forEach(button=>button.addEventListener('click',()=> {
    const action=button.dataset.friendly;
    if(action==='snap'){snapEnabled=!snapEnabled;button.setAttribute('aria-pressed',String(snapEnabled));button.textContent='磁吸：'+(snapEnabled?'开':'关');target.querySelector('.snap-status').textContent='';}
    if(action==='expand'){const expanded=target.classList.toggle('timeline-expanded');button.setAttribute('aria-pressed',String(expanded));button.textContent=expanded?'收起时间轴':'展开时间轴';redrawScale();}
    if(action==='current'){const row=turns.find(t=>t.index===selectedTurn)||turns.find(t=>(t.start_seconds??0)<=(audio?.currentTime??0)&&(t.end_seconds??duration)>=(audio?.currentTime??0));if(row){highlightTurn(row.index);seek(row.input_start_seconds??row.start_seconds);}}
    if(action==='zoom-selection' && selection && selection[1]>selection[0]){zoom=Math.max(1,Math.min(64,extent/(selection[1]-selection[0])*0.85));redrawScale();viewport.scrollLeft=Math.max(0,percent(selection[0])/100*canvas.clientWidth-30);}
    if(action==='play-selection' && selection && audio){seek(selection[0]);audio.play().catch(()=>{});}
    if(action==='loop'){looping=!looping;button.setAttribute('aria-pressed',String(looping));}
    if(action==='next-wait'){
      const threshold=Math.max(0,Number(target.querySelector('[data-wait-threshold]').value)||0);
      const waits=(session.waits||[]).filter(w=>w.end_seconds-w.start_seconds>=threshold).sort((a,b)=>a.start_seconds-b.start_seconds);
      const wait=waits.find(w=>w.start_seconds>(pendingSeek??audio?.currentTime??0)+.01)||waits[0];
      if(wait){viewport.scrollTop=0;selection=[wait.start_seconds,wait.end_seconds];drawSelection();showItem({...wait,item_type:'wait',label:waitLabels[wait.kind]});seek(wait.start_seconds);}else detail.textContent='没有达到该阈值的等待区间';
    }
  }));
  const viewPicker=target.querySelector('[data-timeline-view]');
  viewPicker.value=viewMode;
  function changeView(mode) {
    viewMode=timelineViewMode(mode,vasLanes);
    viewPicker.value=viewMode;
    try {localStorage.setItem('voice-lab.timeline-view',viewMode);} catch { /* Switching still works without storage. */ }
    viewport.scrollTop=0;
    redrawScale();
  }
  viewPicker.addEventListener('change',()=>changeView(viewPicker.value));
  target.addEventListener('click',event=>{const button=event.target.closest('[data-wait-lane]');if(!button)return;if(viewMode==='key')changeView('details');const row=canvas.querySelectorAll('#session-vas .lane')[Number(button.dataset.waitLane)];if(row){viewport.scrollTop=Math.max(0,vasTop+row.offsetTop-pinnedHeight-8);row.classList.add('wait-overlap');}});
  labelRoot.addEventListener('click',event=>{
    const button=event.target.closest('[data-lane-toggle]');
    if(button)vasController?.toggleLane(Number(button.dataset.laneToggle));
  });
  // Native details toggles change row height without changing the viewport.
  // Reflow matching labels without rerendering (which would close the details).
  canvas.addEventListener('toggle',event=>{
    if(event.target.tagName!=='DETAILS' || !event.target.closest('#session-vas'))return;
    const rows=[...canvas.querySelectorAll('#session-vas .lane')];
    const labels=[...labelRoot.querySelectorAll('.session-vas-label')];
    canvas.style.height=vasTop+syncTimelineRowHeights(rows,labels)+24+'px';
    labelRoot.style.height=canvas.style.height;
  },true);
  if (audio) {
    audio.addEventListener('timeupdate',()=>{if(selection && audio.currentTime>=selection[1]){if(looping){seek(selection[0]);}else if(!audio.paused && target.dataset.selectionPlaying==='true'){audio.pause();target.dataset.selectionPlaying='false';}}});
    target.querySelector('[data-friendly="play-selection"]').addEventListener('click',()=>{target.dataset.selectionPlaying='true';});
    audio.addEventListener('loadedmetadata', () => {if (pendingSeek != null) seek(pendingSeek);});
    audio.addEventListener('timeupdate', () => {if(!audio.paused)inspectedTime=null;positionPlayhead(inspectedTime ?? audio.currentTime,!audio.paused);});
    const frame = () => {positionPlayhead(audio.currentTime,true);if (!audio.paused) animation = requestAnimationFrame(frame);};
    audio.addEventListener('play', () => {inspectedTime=null;cancelAnimationFrame(animation);animation = requestAnimationFrame(frame);});
    audio.addEventListener('pause', () => {cancelAnimationFrame(animation);positionPlayhead(audio.currentTime);});
    audio.addEventListener('error', () => {
      detail.innerHTML = '<p class="capture-limitation">会话音频无法读取，请保留完整报告文件夹后重新打开。</p>';
    });
    window.addEventListener('pagehide', event => {if (!event.persisted && fullAudioUrl) URL.revokeObjectURL(fullAudioUrl);});
  }
  function syncPinnedTracks() {
    labelRoot.querySelector('.session-vas-labels').style.transform=`translateY(${-viewport.scrollTop}px)`;
    playhead.style.top=(viewport.scrollTop+32)+'px';
    overlay.style.top=(viewport.scrollTop+36)+'px';
  }
  viewport.addEventListener('scroll',syncPinnedTracks);
  if (window.ResizeObserver) new ResizeObserver(redrawScale).observe(viewport);
  else window.addEventListener('resize',redrawScale);
  redrawScale();
  const requested = Number(new URLSearchParams(location.search).get('t') ?? session.initial_time_seconds);
  if (Number.isFinite(requested) && requested > 0) {
    seek(requested);
    const row = items.find(item => item.item_type === 'reply' && requested >= item.start_seconds && requested <= item.end_seconds) || items.find(item => item.item_type === 'input' && requested >= item.start_seconds && requested <= item.end_seconds);
    if (row) showItem(row);
  }
  if ((session.media_markers || []).length > mediaPoints.length) {
    const notice = document.createElement('p');
    notice.className = 'session-capture-notice';
    notice.textContent = '有图片或视频消息在回放时间范围之外，可在对应轮次的客户端时序查看。';
    target.append(notice);
  }
  if (session.status === 'incomplete') {
    const notice = document.createElement('p');
    notice.className = 'session-capture-notice';
    notice.textContent = '部分记录缺失，回放未完整还原；详见下方计时口径。';
    target.querySelector('.session-player-row')?.after(notice);
  }
}
