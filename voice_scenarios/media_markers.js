/* Presentation only: never fetch media URLs while opening a diagnostic report. */
const IMAGE_MARKER_ICON = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8" cy="8" r="1.5"/><path d="m3 17 6-6 4 4 3-3 5 5"/></svg>';
const VIDEO_MARKER_ICON = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="m9 7 8 5-8 5Z"/></svg>';
function mediaMarkerIcon(markers) {
  const kinds = new Set(markers.map(marker=>marker.kind));
  return (kinds.has('image') ? IMAGE_MARKER_ICON : '') + (kinds.has('video') ? VIDEO_MARKER_ICON.replace('<svg ', '<svg class="media-icon-video" ') : '');
}
function mediaMarkerLabel(markers) {
  return [...new Set(markers.map(marker=>marker.kind))].map(kind=>kind === 'video' ? '视频' : '图片').join(' / ');
}
function mediaMarkerDetails(markers, caption) {
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  return `<p class="muted">客户端收到${mediaMarkerLabel(markers)}地址的时间，加载与播放时间未采集。</p>` + markers.map(marker => {
    const label = marker.kind === 'video' ? '视频' : '图片';
    let href = null;
    try {
      const url = new URL(marker.url);
      if (['http:','https:'].includes(url.protocol) && url.hostname && !url.username && !url.password) href = url.href;
    } catch (_) {}
    return `<div class="media-marker-detail"><strong>${escape(caption(marker))}</strong>${href ? `<a href="${escape(href)}" target="_blank" rel="noopener noreferrer" referrerpolicy="no-referrer">查看${label} ↗</a>` : `<span>${label}地址缺失或不支持打开</span>`}<code>${escape(marker.url || `未提供${label}地址`)}</code></div>`;
  }).join('');
}
function groupMediaMarkers(markers) {
  // Keep one event per button, including identical timestamps and duplicate URLs.
  return [...markers].sort((a,b)=>a.at_seconds-b.at_seconds).map(marker=>[marker]);
}
