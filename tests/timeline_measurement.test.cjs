const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const vm = require('node:vm');
const enable = vm.runInNewContext(readFileSync('voice_scenarios/timeline_measurement.js', 'utf8') + '\nenableTimelineMeasurement;');

class Element {
  constructor() { this.listeners = {}; this.style = {}; this.value = ''; this.dataset = {}; this.hidden = true; this.capture = null; }
  addEventListener(type, callback) { (this.listeners[type] ??= []).push(callback); }
  emit(type, fields = {}) {
    const event = {button:0, pointerId:1, clientX:200, target:this, preventDefault(){this.prevented=true;}, stopImmediatePropagation(){this.stopped=true;}, ...fields};
    for (const listener of this.listeners[type] || []) {listener(event); if (event.stopped) break;}
    return event;
  }
  insertAdjacentHTML() {}
  getBoundingClientRect() {return {left:200,width:1000,right:1200};}
  setPointerCapture(id) {this.capture=id;}
  hasPointerCapture(id) {return this.capture===id;}
  releasePointerCapture() {this.capture=null;}
  closest() {return this.dataset.chainHandle ? this : null;}
}
function chart(duration=10) {
  const root=new Element(); root.id='server';
  const nodes=Object.fromEntries(['.timeline-plot','.axis','.chain-selection','[data-chain-start]','[data-chain-end]','[data-chain-duration]','[data-chain-clear]','[data-chain-caption]'].map(key=>[key,new Element()]));
  root.querySelector=selector=>nodes[selector];
  nodes['.timeline-plot'].querySelector=root.querySelector;
  enable(root,duration);
  return {root,nodes,plot:nodes['.timeline-plot'], start:nodes['[data-chain-start]'], end:nodes['[data-chain-end]'], output:nodes['[data-chain-duration]'], overlay:nodes['.chain-selection']};
}
function drag(c, from, to) {c.plot.emit('pointerdown',{clientX:from});c.plot.emit('pointermove',{clientX:to});c.plot.emit('pointerup',{clientX:to});}

test('drag measures relative seconds and suppresses the resulting event-detail click only',()=>{
  const c=chart(); drag(c,400,650);
  assert.equal(c.start.value,'2.00'); assert.equal(c.end.value,'4.50'); assert.equal(c.output.textContent,'Δ 2.50 s');
  assert.equal(c.overlay.hidden,false);
  assert.equal(c.plot.emit('click',{detail:1}).stopped,true);
  assert.equal(c.plot.emit('click',{detail:1}).stopped,undefined);
});
test('normal event click is preserved; labels do not start measurement',()=>{
  const c=chart();c.plot.emit('pointerdown',{clientX:500});c.plot.emit('pointerup',{clientX:501});
  assert.equal(c.plot.capture,null); assert.equal(c.overlay.hidden,true); assert.equal(c.plot.emit('click',{detail:1}).stopped,undefined);
  drag(c,50,900); assert.equal(c.overlay.hidden,true);
});
test('reverse drags and pointer release outside the chart clamp to its own extent',()=>{
  const c=chart(); drag(c,1000,50); assert.equal(c.start.value,'0.00'); assert.equal(c.end.value,'8.00');
  drag(c,400,2000); assert.equal(c.start.value,'2.00'); assert.equal(c.end.value,'10.00');
});
test('numeric inputs, clear and Escape provide non-drag operation',()=>{
  const c=chart(); c.start.value='7';c.end.value='2';c.end.emit('change');
  assert.equal(c.output.textContent,'Δ 5.00 s'); assert.equal(c.start.value,'2.00');
  c.root.emit('keydown',{key:'Escape'});assert.equal(c.overlay.hidden,true);assert.equal(c.start.value,'');
  drag(c,400,700);c.nodes['[data-chain-clear]'].emit('click');assert.equal(c.output.textContent,'Δ — s');
});
test('cancelled gestures restore the previous selection and clocks stay independent',()=>{
  const server=chart(10),client=chart(100);drag(server,400,600);drag(client,400,600);
  assert.equal(server.output.textContent,'Δ 2.00 s');assert.equal(client.output.textContent,'Δ 20.00 s');
  server.plot.emit('pointerdown',{clientX:300});server.plot.emit('pointermove',{clientX:700});server.plot.emit('pointercancel');
  assert.equal(server.output.textContent,'Δ 2.00 s');
});
test('keyboard handles adjust endpoints without moving outside the selected interval',()=>{
  const c=chart();drag(c,400,600);const handle=new Element();handle.dataset.chainHandle='start';
  c.root.emit('keydown',{key:'ArrowRight',target:handle}); assert.equal(c.start.value,'2.01');
  c.root.emit('keydown',{key:'End',target:handle});assert.equal(c.start.value,'4.00');
});
