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
  const duration = Math.max(0, Number(session.duration_seconds) || 0);
  const extent = Math.max(.01, duration);
  const percent = value => Math.max(0, Math.min(100, Number(value) / extent * 100));
  const clamp = value => Math.max(0, Math.min(duration, Number(value) || 0));
  const kindLabels = {greeting:'欢迎语',answer:'正式回复',pre_speech:'过渡语',filler:'临时回复',unknown:'类型未关联',mixed:'混合回复'};
  const waitLabels = {first_reply:'等待首句',transition:'等待正式回复',sentence:'句间等待'};
  const markerLabels = {input_end:'输入结束',first_received:'首包到达',first_playback:'开始播放',abort:'打断'};
  const items = [];
  const turns = session.turns || [];
  let zoom = Math.max(1,Math.min(64,extent*18/Math.max(1,viewport.clientWidth))), selection = null, gesture = null, pendingSeek = null;
  let animation = null, lastAutoScroll = 0;
  let fullAudioRequest = null, fullAudioUrl = null;
  startInput.max = endInput.max = String(duration);
  function itemButton(item, className, caption, contents = '') {
    const id = items.push(item) - 1;
    const span = item.end_seconds - item.start_seconds;
    const label = `第 ${item.turn_index} 轮 · ${item.label} · ${seconds(item.start_seconds)} → ${seconds(item.end_seconds)} · ${seconds(span)}${item.text ? ' · ' + item.text : ''}`;
    return `<button type="button" class="session-range ${className}" data-session-item="${id}" style="left:${percent(item.start_seconds)}%;width:${Math.max(0, span/extent*100)}%" title="${escape(label)}" aria-label="${escape(label)}">${contents}<span>${escape(caption)}</span></button>`;
  }
  let inputBars = '', replyBars = '', turnLines = '', markers = '';
  turns.forEach(row => {
    const start = row.input_start_seconds ?? row.start_seconds;
    if (start != null) turnLines += `<span class="session-turn-line" style="left:${percent(start)}%"><button type="button" data-session-turn="${row.index}" title="第 ${row.index} 轮 · ${escape(row.text)}">${String(row.index).padStart(2,'0')}</button></span>`;
    (row.input_segments || []).forEach(segment => {
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
    const label = `第 ${row.turn_index} 轮 · ${markerLabels[row.kind] || row.kind} · ${seconds(row.at_seconds)}`;
    markers += `<button type="button" class="session-marker marker-${escape(row.kind)}" data-session-marker="${escape(row.kind)}" data-session-time="${Number(row.at_seconds)}" data-session-turn-index="${row.turn_index}" style="left:${percent(row.at_seconds)}%" title="${escape(label)}" aria-label="${escape(label)}"><i></i><span>${escape(markerLabels[row.kind] || row.kind)}</span></button>`;
  });
  canvas.innerHTML = `<div class="session-ruler" id="session-ruler" aria-hidden="true"></div><div class="session-grid" aria-hidden="true"></div>${turnLines}<div class="session-input-track">${inputBars}</div><div class="session-reply-track">${replyBars}</div>${markers}<div class="session-selection" id="session-selection" hidden><button type="button" class="measure-handle handle-start" data-measure-handle="start" aria-label="调整测量起点"></button><span id="selection-caption"></span><button type="button" class="measure-handle handle-end" data-measure-handle="end" aria-label="调整测量终点"></button></div><div class="session-playhead" id="session-playhead" aria-hidden="true"><span>0.00 s</span></div>`;
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
    const x = clamp(time)/extent*canvas.clientWidth;
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
    const value = clamp(time);
    pendingSeek = value;
    const seekable = audio && Array.from({length:audio.seekable.length}, (_, index) => [audio.seekable.start(index),audio.seekable.end(index)]).some(([start,end]) => value >= start && value <= end);
    if (audio && audio.readyState >= 1 && (value === 0 || seekable || fullAudioUrl)) {
      audio.currentTime = Math.min(value, Number.isFinite(audio.duration) ? audio.duration : value);
      pendingSeek = null;
    } else if (audio && !fullAudioRequest && /^https?:$/.test(location.protocol)) {
      const notice = document.createElement('span');
      notice.className = 'audio-preparing';
      notice.setAttribute('role','status');
      notice.textContent = '正在准备完整回放…';
      target.querySelector('.session-player-row').append(notice);
      // Simple static servers may omit byte ranges. A complete local blob supports
      // reliable seeking while file:// and range-capable servers stay native.
      const resume = !audio.paused;
      fullAudioRequest = fetch(audio.currentSrc || audio.src).then(response => {
        if (!response.ok) throw new Error('audio_download_failed');
        return response.blob();
      }).then(blob => {
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
    canvas.querySelectorAll('[data-session-item]').forEach(button => button.classList.toggle('is-selected', items[Number(button.dataset.sessionItem)] === item));
    const row = turns.find(turn => turn.index === item.turn_index);
    let body = item.text ? `<p>${escape(item.text)}</p>` : '';
    if (item.item_type === 'wait') {
      const descriptions = {first_reply:'输入结束 → 首句开始播放',transition:'过渡 / 临时回复播完 → 正式回复开始播放',sentence:'上一段播完 → 下一段开始播放'};
      body = `<p>${escape(descriptions[item.kind] || '未播放声音的等待区间')}</p>`;
    }
    const stats = item.item_type === 'input' && row ? `<div class="selected-turn-metrics"><span>输入结束 <b>${seconds(row.input_end_seconds)}</b></span><span>首包到达 <b>${seconds(row.first_received_seconds)}</b></span><span>首句播放 <b>${seconds(row.first_playback_seconds)}</b></span></div>` : '';
    detail.innerHTML = `<div class="session-detail-heading"><span class="badge">第 ${item.turn_index} 轮</span><strong>${escape(item.label)}</strong><span class="detail-time">${seconds(item.start_seconds)} → ${seconds(item.end_seconds)} <b>· ${seconds(item.end_seconds-item.start_seconds)}</b></span><a href="turn-${String(item.turn_index).padStart(3,'0')}.html">内部时序 ↗</a></div>${body}${stats}`;
  }
  function activate(element, time) {
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
      detail.innerHTML = `<div class="session-detail-heading"><span class="badge">第 ${escape(marker.dataset.sessionTurnIndex)} 轮</span><strong>${escape(markerLabels[marker.dataset.sessionMarker])}</strong><span class="detail-time">${seconds(at)}</span></div>${row?.input_end_seconds != null && ['first_received','first_playback'].includes(marker.dataset.sessionMarker) ? `<p>输入结束后 <strong>${seconds(at-row.input_end_seconds)}</strong></p>` : ''}`;
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
    return clamp((event.clientX-bounds.left)/bounds.width*extent);
  }
  canvas.addEventListener('pointerdown', event => {
    if (event.button !== 0 || !duration) return;
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
  canvas.addEventListener('pointercancel', () => {gesture = null;});
  canvas.addEventListener('click', event => {
    if (event.detail !== 0 || event.target.closest('[data-measure-handle]')) return;
    activate(event.target);
  });
  canvas.addEventListener('keydown', event => {
    if (event.key === 'Escape') {selection = null;drawSelection();return;}
    const handle = event.target.closest('[data-measure-handle]');
    if (handle && selection && ['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) {
      event.preventDefault();
      const index = handle.dataset.measureHandle === 'start' ? 0 : 1;
      const delta = (event.key === 'ArrowLeft' ? -1 : 1) * (event.shiftKey ? 1 : .01);
      const value = event.key === 'Home' ? 0 : event.key === 'End' ? duration : selection[index]+delta;
      selection[index] = index === 0 ? Math.min(selection[1],clamp(value)) : Math.max(selection[0],clamp(value));
      drawSelection();
    } else if (event.target === canvas && ['ArrowLeft','ArrowRight'].includes(event.key)) {
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
    const ruler = document.getElementById('session-ruler');
    const desired = extent/(width*zoom/95);
    const base = 10**Math.floor(Math.log10(Math.max(.01,desired)));
    const step = [1,2,5,10].map(value => value*base).find(value => value >= desired) || base*10;
    let ticks = '';
    for (let t=0;t<=extent;t+=step) ticks += `<span style="left:${percent(t)}%">${Number(t.toFixed(2))} s</span>`;
    ruler.innerHTML = ticks;
    canvas.style.setProperty('--grid-step',step/extent*100+'%');
    canvas.querySelectorAll('.session-range').forEach(button => {
      button.classList.toggle('compact-range',button.clientWidth < 65);
    });
    drawSelection(false);
    positionPlayhead(pendingSeek ?? audio?.currentTime ?? 0);
    target.querySelector('[data-session-zoom="out"]').disabled = zoom <= 1;
  }
  target.querySelectorAll('[data-session-zoom]').forEach(button => button.addEventListener('click', () => {
    const oldCenter = (viewport.scrollLeft+viewport.clientWidth/2)/canvas.clientWidth;
    zoom = button.dataset.sessionZoom === 'fit' ? 1 : Math.max(1,Math.min(64,zoom*(button.dataset.sessionZoom === 'in' ? 2 : .5)));
    redrawScale();
    viewport.scrollLeft = Math.max(0,oldCenter*canvas.clientWidth-viewport.clientWidth/2);
  }));
  document.querySelectorAll('[data-session-seek]').forEach(link => link.addEventListener('click', event => {
    event.preventDefault();
    const time = Number(link.dataset.sessionSeek);
    seek(time);
    const row = turns.find(turn => (turn.input_start_seconds ?? turn.start_seconds) === time);
    if (row) showItem({...row,turn_index:row.index,item_type:'input',label:'用户输入',start_seconds:time,end_seconds:row.input_end_seconds});
    target.scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',block:'start'});
  }));
  if (audio) {
    audio.addEventListener('loadedmetadata', () => {if (pendingSeek != null) seek(pendingSeek);});
    audio.addEventListener('timeupdate', () => positionPlayhead(audio.currentTime,!audio.paused));
    const frame = () => {positionPlayhead(audio.currentTime,true);if (!audio.paused) animation = requestAnimationFrame(frame);};
    audio.addEventListener('play', () => {cancelAnimationFrame(animation);animation = requestAnimationFrame(frame);});
    audio.addEventListener('pause', () => {cancelAnimationFrame(animation);positionPlayhead(audio.currentTime);});
    audio.addEventListener('error', () => {
      detail.innerHTML = '<p class="capture-limitation">会话音频无法读取，请保留完整报告文件夹后重新打开。</p>';
    });
    window.addEventListener('pagehide', event => {if (!event.persisted && fullAudioUrl) URL.revokeObjectURL(fullAudioUrl);});
  }
  if (window.ResizeObserver) new ResizeObserver(redrawScale).observe(viewport);
  else window.addEventListener('resize',redrawScale);
  redrawScale();
  const requested = Number(new URLSearchParams(location.search).get('t'));
  if (Number.isFinite(requested) && requested > 0) {
    seek(requested);
    const row = items.find(item => item.item_type === 'reply' && requested >= item.start_seconds && requested <= item.end_seconds) || items.find(item => item.item_type === 'input' && requested >= item.start_seconds && requested <= item.end_seconds);
    if (row) showItem(row);
  }
  if (session.status === 'incomplete') {
    const notice = document.createElement('p');
    notice.className = 'session-capture-notice';
    notice.textContent = '部分记录缺失，回放未完整还原；详见下方计时口径。';
    target.querySelector('.session-player-row')?.after(notice);
  }
}
