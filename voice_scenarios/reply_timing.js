function renderReplyTiming(target, timing, session, turnIndex) {
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const sec = value => value == null ? '—' : Number(value).toFixed(2) + ' s';
  const rows = timing?.sentences || [];
  const sessionRows = (session?.segments || []).filter(row => row.turn_index === turnIndex);
  const types = {answer:'正式回复',pre_speech:'过渡语',filler:'临时回复',mixed:'混合输出',unknown:'类型未关联'};
  const statuses = {completed:'已播放',interrupted:'被打断',not_played:'未播放',incomplete:'采集不完整'};
  target.innerHTML = `<div class="section-heading"><div><h2>回复内容</h2><p>时间与会话播放保持一致</p></div><span class="unit-label">${rows.length} 段回复</span></div>` + (rows.length ? rows.map((row, i) => {
    const absolute = sessionRows.find(item => item.index === (row.index || i + 1));
    const start = absolute?.start_seconds, end = absolute?.end_seconds;
    const temporary = row.kind === 'pre_speech' || row.kind === 'filler';
    return `<article class="speech-row"><div class="speech-content"><div class="speech-heading"><span class="segment-number ${temporary ? 'temporary' : ''}">${String(i+1).padStart(2,'0')}</span><b>${types[row.kind] || esc(row.kind)}</b><span class="segment-status ${row.status === 'interrupted' ? 'is-interrupted' : ''}">${statuses[row.status] || esc(row.status)}</span></div><p class="speech-text">${esc(row.text) || '（未采集到播报文字）'}</p><div class="speech-values"><span>会话时间 <b>${sec(start)} → ${sec(end)}</b></span>${row.gap_seconds != null ? `<span>距上一段 <b>${sec(row.gap_seconds)}</b></span>` : ''}</div></div>${start != null ? `<a class="button speech-session-link" href="report.html?t=${Number(start).toFixed(3)}#session-timeline" aria-label="在完整会话中回听第 ${i+1} 段">会话中回听 ↗</a>` : '<span class="segment-unavailable">未记录播放时间</span>'}</article>`;
  }).join('') : '<div class="empty-state">未采集到逐段回复文字</div>');
}
