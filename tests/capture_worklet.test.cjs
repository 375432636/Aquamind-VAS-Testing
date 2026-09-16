const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
function capture(){let Klass;const frames=[];class Processor{constructor(){this.port={postMessage:data=>frames.push(typeof data==='string'?data:Int16Array.from(data))};}}vm.runInNewContext(fs.readFileSync('voice_scenarios/live/capture-worklet.js','utf8'),{AudioWorkletProcessor:Processor,Int16Array,registerProcessor:(_,C)=>Klass=C});return {node:new Klass(),frames};}
test('actual worklet preserves nonzero microphone samples and flushes the short tail',()=>{
 const {node,frames}=capture();node.process([[new Float32Array(960).fill(.25)]]);assert.equal(frames.length,0);
 node.port.onmessage({data:'start'});node.process([[new Float32Array(1000).fill(.25)]]);node.port.onmessage({data:'stop'});
 assert.equal(frames.length,3);assert.equal(frames[0].length,960);assert.ok(frames[0].every(x=>x===8192));assert.equal(frames[1][39],8192);assert.equal(frames[1][40],0);assert.equal(frames[2],'stopped');
});
