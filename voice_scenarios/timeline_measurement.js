// Measures the displayed axis only (uncalibrated across sources). Never seeks audio.
function enableTimelineMeasurement(root, duration, snapPoints = []) {
  const extent = Number(duration);
  if (!(extent > 0)) return;
  const plot = root.querySelector('.timeline-plot');
  const axis = plot.querySelector('.axis');
  root.insertAdjacentHTML('afterbegin', `<div class="chain-measure-tools"><span class="measurement-hint">拖动划线测量 · Esc 清除${root.id === 'combined' ? ' · 跨来源间隔未校时' : ''}</span><div class="measurement-controls" role="group" aria-label="${root.id === 'combined' ? '客户端与 VAS' : root.id === 'server' ? 'VAS 内部' : '客户端'}链路区间测量"><label>起点 <input data-chain-start type="number" min="0" max="${extent}" step="0.01" placeholder="—" aria-label="链路测量起点，秒"> s</label><label>终点 <input data-chain-end type="number" min="0" max="${extent}" step="0.01" placeholder="—" aria-label="链路测量终点，秒"> s</label><output data-chain-duration aria-live="polite">Δ — s</output><button type="button" data-chain-clear>清除</button></div></div>`);
  plot.insertAdjacentHTML('beforeend', '<div class="chain-selection-layer"><div class="chain-selection" hidden><button type="button" class="measure-handle handle-start" data-chain-handle="start" aria-label="调整链路测量起点"></button><span data-chain-caption></span><button type="button" class="measure-handle handle-end" data-chain-handle="end" aria-label="调整链路测量终点"></button></div></div>');
  const overlay = plot.querySelector('.chain-selection');
  const startInput = root.querySelector('[data-chain-start]');
  const endInput = root.querySelector('[data-chain-end]');
  const result = root.querySelector('[data-chain-duration]');
  const caption = root.querySelector('[data-chain-caption]');
  const clamp = value => Math.max(0, Math.min(extent, Number(value)));
  let selection = null, gesture = null, suppressClick = false;
  function draw(updateInputs = true) {
    overlay.hidden = !selection;
    result.textContent = selection ? `Δ ${(selection[1]-selection[0]).toFixed(2)} s` : 'Δ — s';
    if (updateInputs) {
      startInput.value = selection ? selection[0].toFixed(2) : '';
      endInput.value = selection ? selection[1].toFixed(2) : '';
    }
    if (!selection) return;
    overlay.style.left = `${selection[0]/extent*100}%`;
    overlay.style.width = `${(selection[1]-selection[0])/extent*100}%`;
    caption.textContent = result.textContent;
  }
  function at(event) {
    const bounds = axis.getBoundingClientRect();
    return timelineSnap(clamp((event.clientX-bounds.left)/Math.max(1,bounds.width)*extent),snapPoints,bounds.width/extent,event.altKey).time;
  }
  function release() {
    const id = gesture?.pointerId;
    gesture = null;
    if (id != null && plot.hasPointerCapture(id)) plot.releasePointerCapture(id);
  }
  function clear() {release(); selection = null; draw();}
  function move(event) {
    if (!gesture || event.pointerId !== gesture.pointerId) return;
    if (!gesture.dragged && Math.abs(event.clientX-gesture.clientX) < 4) return;
    gesture.dragged = true;
    if (!plot.hasPointerCapture(event.pointerId)) plot.setPointerCapture(event.pointerId);
    const anchor = gesture.handle && gesture.before ? gesture.before[gesture.handle === 'start' ? 1 : 0] : gesture.start;
    const end = at(event);
    selection = [Math.min(anchor,end),Math.max(anchor,end)];
    draw();
  }
  plot.addEventListener('pointerdown', event => {
    if (event.target.closest('details')) return;
    const bounds = axis.getBoundingClientRect();
    if (event.button !== 0 || gesture || event.clientX < bounds.left || event.clientX > bounds.right) return;
    const handle = event.target.closest('[data-chain-handle]');
    gesture = {pointerId:event.pointerId, clientX:event.clientX, start:at(event), handle:handle?.dataset.chainHandle, before:selection?.slice(), dragged:false};
  });
  plot.addEventListener('pointermove', move);
  plot.addEventListener('pointerup', event => {
    if (!gesture || gesture.pointerId !== event.pointerId) return;
    move(event);
    suppressClick = gesture.dragged;
    release();
  });
  plot.addEventListener('pointercancel', () => {
    if (gesture) selection = gesture.before || null;
    release(); draw();
  });
  plot.addEventListener('lostpointercapture', () => {gesture = null;});
  // Capture only an actual drag's synthetic click; ordinary span clicks still open details.
  plot.addEventListener('click', event => {
    if (!suppressClick) return;
    suppressClick = false;
    if (event.detail !== 0) {event.preventDefault(); event.stopImmediatePropagation();}
  }, true);
  [startInput,endInput].forEach(input => {
    const update = normalize => {
      if (startInput.value === '' || endInput.value === '') {selection = null; draw(false); return;}
      const start = Number(startInput.value), end = Number(endInput.value);
      if (!Number.isFinite(start) || !Number.isFinite(end)) return;
      selection = [Math.min(clamp(start),clamp(end)),Math.max(clamp(start),clamp(end))];
      draw(normalize);
    };
    input.addEventListener('input', () => update(false));
    input.addEventListener('change', () => update(true));
  });
  root.querySelector('[data-chain-clear]').addEventListener('click', clear);
  root.addEventListener('keydown', event => {
    if (event.key === 'Escape') {clear(); return;}
    const handle = event.target.closest('[data-chain-handle]');
    if (!selection || !handle || !['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
    event.preventDefault();
    const index = handle.dataset.chainHandle === 'start' ? 0 : 1;
    const delta = (event.key === 'ArrowLeft' ? -1 : 1)*(event.shiftKey ? 1 : .01);
    const value = event.key === 'Home' ? 0 : event.key === 'End' ? extent : selection[index]+delta;
    selection[index] = index === 0 ? Math.min(selection[1],clamp(value)) : Math.max(selection[0],clamp(value));
    draw();
  });
}
