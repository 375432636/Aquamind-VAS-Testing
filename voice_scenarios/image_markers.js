/* Presentation only: never fetch image URLs while opening a diagnostic report. */
const IMAGE_MARKER_ICON = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8" cy="8" r="1.5"/><path d="m3 17 6-6 4 4 3-3 5 5"/></svg>';
function imageMarkerDetails(markers, caption) {
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  return '<p class="muted">客户端收到图片地址的时间，图片加载完成时间未采集。</p>' + markers.map(marker => {
    let href = null;
    try {
      const url = new URL(marker.url);
      if (['http:','https:'].includes(url.protocol) && url.hostname && !url.username && !url.password) href = url.href;
    } catch (_) {}
    return `<div class="image-marker-detail"><strong>${escape(caption(marker))}</strong>${href ? `<a href="${escape(href)}" target="_blank" rel="noopener noreferrer" referrerpolicy="no-referrer">查看图片 ↗</a>` : '<span>图片地址缺失或不支持打开</span>'}<code>${escape(marker.url || '未提供图片地址')}</code></div>`;
  }).join('');
}
function groupImageMarkers(markers, extent, width) {
  const groups = [];
  for (const marker of [...markers].sort((a,b)=>a.at_seconds-b.at_seconds)) {
    const previous = groups[groups.length-1];
    if (previous && (marker.at_seconds-previous[0].at_seconds)/extent*width < 36) previous.push(marker);
    else groups.push([marker]);
  }
  return groups;
}
