// Timeline-local gestures: trackpad pinch, Safari gestures and touch screens.
// Normal wheel scrolling and browser zoom outside the chart remain native.
function bindTimelineGestures(viewport, controls) {
  let pending = null, frame = null, touch = null, safari = null;
  const limit = value => Math.max(1, Math.min(64, value));
  function flush() {
    frame = null;
    if (!pending) return;
    const update = pending; pending = null;
    controls.zoom(update.zoom, update.x, update.from);
  }
  function scale(factor, x, from = x) {
    if (!Number.isFinite(factor) || factor <= 0) return;
    controls.cancelSelection();
    pending = {zoom:limit((pending?.zoom ?? controls.getZoom()) * factor),x,from:pending?.from ?? from};
    if (frame == null) frame = requestAnimationFrame(flush);
  }
  function settle() { if (frame != null) cancelAnimationFrame(frame); flush(); }
  viewport.addEventListener('wheel', event => {
    if (!event.ctrlKey) return;
    event.preventDefault();
    if (safari) return;
    const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? 400 : 1;
    scale(Math.exp(Math.max(-2,Math.min(2,-event.deltaY*unit*.006))),event.clientX);
  }, {passive:false});
  viewport.addEventListener('gesturestart', event => {
    event.preventDefault(); settle(); safari = {scale:event.scale || 1,x:event.clientX};
    controls.cancelSelection();
  }, {passive:false});
  viewport.addEventListener('gesturechange', event => {
    if (!safari) return;
    event.preventDefault(); scale(event.scale/safari.scale,event.clientX,safari.x);
    safari = {scale:event.scale,x:event.clientX};
  }, {passive:false});
  viewport.addEventListener('gestureend', event => {event.preventDefault();settle();safari=null;}, {passive:false});
  function pair(touches) {
    const [a,b] = touches;
    return {distance:Math.hypot(b.clientX-a.clientX,b.clientY-a.clientY),x:(a.clientX+b.clientX)/2};
  }
  viewport.addEventListener('touchstart', event => {
    event.preventDefault(); controls.cancelSelection();
    if (event.touches.length >= 2) touch = {mode:'pinch',...pair(event.touches)};
    else if (!touch) {
      const first=event.touches[0];
      touch = {mode:'pan',x:first.clientX,y:first.clientY,startX:first.clientX,startY:first.clientY,target:event.target,moved:false};
    }
  }, {passive:false});
  viewport.addEventListener('touchmove', event => {
    if (!touch) return;
    event.preventDefault();
    if (event.touches.length >= 2) {
      const next=pair(event.touches);
      if (touch.mode === 'pinch' && touch.distance > 0) scale(next.distance/touch.distance,next.x,touch.x);
      touch={mode:'pinch',...next};
    } else if (touch.mode === 'pan' && event.touches.length === 1) {
      const next=event.touches[0];
      if (Math.hypot(next.clientX-touch.startX,next.clientY-touch.startY)>4) touch.moved=true;
      if(touch.moved){viewport.scrollLeft+=touch.x-next.clientX;viewport.scrollTop+=touch.y-next.clientY;}
      touch.x=next.clientX;touch.y=next.clientY;
    }
  }, {passive:false});
  viewport.addEventListener('touchend', event => {
    if (!touch) return;
    event.preventDefault();
    if (event.touches.length) return;
    const completed=touch;touch=null;settle();
    if(completed.mode==='pan'&&!completed.moved)controls.tap?.(completed.target,completed.x);
  }, {passive:false});
  viewport.addEventListener('touchcancel', () => {touch=null;settle();});
}
