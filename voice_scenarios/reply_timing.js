function renderReplyTiming(target, timing) {
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const sec = value => value == null ? '—' : Number(value).toFixed(2) + ' s';
  const rows = timing?.sentences || [];
  const types = {answer:'正式回复',pre_speech:'过渡语',filler:'临时回复',mixed:'混合输出',unknown:'类型未关联'};
  const statuses = {completed:'已播放',interrupted:'被打断',not_played:'未播放',incomplete:'采集不完整'};
  const colors = ['#38778c','#5c6fba','#8870aa','#ad793d','#a56984','#507f74'];
  const color = (row, i) => row.kind === 'pre_speech' || row.kind === 'filler' ? '#ad793d' : colors[i % colors.length];
  function player(clip, label, index) {
    return `<div class="speech-audio"><audio controls preload="none" aria-label="第 ${index} 段 · ${label}" src="${esc(clip.path)}"></audio><div class="speech-audio-label"><span>${label} · ${sec(clip.duration_seconds)}</span><a href="${esc(clip.path)}" download aria-label="下载第 ${index} 段 WAV">WAV ↓</a></div></div>`;
  }
  function audio(row, index) {
    const played = row.audio?.played, received = row.audio?.received;
    if (played?.status === 'ready') {
      return player(played, played.complete ? '实际播放' : '实际播放部分', index) + (received?.status === 'ready' && !played.complete ? `<details class="speech-received"><summary>已收到的完整片段</summary>${player(received, '已收到音频', index)}</details>` : '');
    }
    if (received?.status === 'ready') return player(received, '已收到 · 未确认播放', index);
    return '<div class="speech-audio-unavailable">未保存可回听音频</div>';
  }
  const starts = rows.map(row => row.start_seconds).filter(value => value != null);
  const zero = Math.min(0, ...starts), end = Math.max(1, ...rows.map(row => row.end_seconds || 0));
  const range = (end - zero) * 1.02;
  let bars = '', previousEnd = zero;
  rows.forEach((row, i) => {
    if (row.start_seconds == null || row.end_seconds == null) return;
    const gap = Math.max(0, row.start_seconds - previousEnd);
    if (gap) bars += `<div class="speech-wait" style="left:${(previousEnd-zero)/range*100}%;width:${gap/range*100}%" title="等待 ${sec(gap)}"></div>`;
    const width = Math.max(.15, (row.end_seconds-row.start_seconds)/range*100);
    bars += `<button type="button" class="speech-part" data-reply-segment="${i+1}" aria-label="查看第 ${i+1} 段回复与音频" style="left:${(row.start_seconds-zero)/range*100}%;width:${width}%;background:${color(row, i)}" title="第 ${i+1} 段 · ${sec(row.start_seconds)} → ${sec(row.end_seconds)}">${width > 4 ? '#' + (i+1) : ''}</button>`;
    previousEnd = Math.max(previousEnd, row.end_seconds);
  });
  target.innerHTML = `<div class="section-heading"><div><h2>回复与回听</h2><p>输入结束 = 0 s · Python 模拟播放</p></div><span class="unit-label">${rows.length} 段回复</span></div>
    ${rows.length ? `<div class="speech-axis"><span>${sec(zero)}</span><span>${sec(zero+range/2)}</span><span>${sec(zero+range)}</span></div><div class="speech-track">${bars}</div><div class="speech-legend"><span><i></i>回复片段 · 点击定位</span><span><i class="wait-key"></i>等待</span></div>` : ''}
    ${rows.length ? rows.map((row,i)=>`<article class="speech-row" data-reply-row="${i+1}" tabindex="-1">
      <div class="speech-content"><div class="speech-heading"><span class="segment-number" style="background:${color(row,i)}">${String(i+1).padStart(2,'0')}</span><b>${types[row.kind]||esc(row.kind)}</b><span class="segment-status ${row.status==='interrupted'?'is-interrupted':''}">${statuses[row.status]||esc(row.status)}</span></div>
      <p class="speech-text">${esc(row.text)||'（未采集到播报文字）'}</p>
      <div class="speech-values"><span>开始 <b>${sec(row.start_seconds)}</b></span><span>结束 <b>${sec(row.end_seconds)}</b></span>${i ? `<span>句间等待 <b>${sec(row.gap_seconds)}</b></span>` : ''}</div></div>
      <div class="speech-player">${audio(row, i+1)}</div></article>`).join('') : '<div class="empty-state">未采集到逐段回复。内部请求时间仍可在下方查看。</div>'}
    <details class="inline-disclosure"><summary>分段与播放口径</summary><div class="detail-body"><p>按播报文字和音频包序号关联保存的 WAV；片段回听不包含句间等待。此处是播报片段，并非单次 TTS 合成请求。</p>${(timing?.limitations||[]).map(text=>`<p>${esc(text)}</p>`).join('')}</div></details>`;
  target.onclick = event => {
    const button = event.target.closest('[data-reply-segment]');
    if (!button || !target.contains(button)) return;
    target.querySelectorAll('[data-reply-row]').forEach(row => {
      const active = row.dataset.replyRow === button.dataset.replySegment;
      row.classList.toggle('speech-selected', active);
      if (active) {
        row.scrollIntoView({behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'center'});
        row.focus({preventScroll: true});
      }
    });
  };
}
