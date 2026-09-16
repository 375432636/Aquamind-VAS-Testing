const test=require('node:test');const assert=require('node:assert/strict');
const load=()=>import('../voice_scenarios/live/audio.mjs');
function context(){return {currentTime:1,destination:{},createBuffer:(_c,n,r)=>({duration:n/r,copyToChannel(){}}),createBufferSource:()=>({connect(){},disconnect(){},start(t){this.started=t;},stop(){}})};}

test('three turns retain actual output time and PCM after increasingly long audio-clock pauses', async t => {
  const {Player} = await load();
  t.mock.method(global, 'setTimeout', () => 1);
  t.mock.method(global, 'clearTimeout', () => {});
  let now = 1000, output = .98;
  t.mock.method(performance, 'now', () => now);
  const played = [], drained = [];
  const p = new Player(() => {}, (pcm, meta) => played.push({pcm, meta}), owner => drained.push(owner));
  p.ctx = context();
  p.ctx.sampleRate = 48000;
  p.ctx.getOutputTimestamp = () => ({contextTime:output, performanceTime:now});
  p.rate = 16000;
  p.decoder = {decode:bytes => new Int16Array(960).fill(bytes[0])};
  t.after(() => p.stop());
  for (const [i, arrival] of [1000, 6000, 16000].entries()) {
    const turn = i + 1;
    now = arrival;
    p.ctx.currentTime = turn;
    output = turn - .02;
    await p.feed(new Uint8Array([turn]), {turn, audio_seq:turn, received:arrival * 1e6});
    await p.flush(turn);
    const entry = [...p.active][0];
    now = arrival + 50;
    p.ctx.currentTime = turn + .05;
    output = turn + .03;
    p.pump();
    now = arrival + 100;
    p.ctx.currentTime = turn + .1;
    output = turn + .08;
    entry.node.onended();
    p.pump();
    assert.equal(p.busy, false);
    const {pcm, meta} = played[i];
    assert.equal(meta.at, (arrival + 40) * 1e6);
    assert.ok(meta.received <= meta.at && meta.at <= meta.playback_observed_at_ns);
    assert.equal(meta.timing_method, 'output_timestamp');
    assert.equal(meta.output_start_confirmed, true);
    assert.equal(meta.output_end_confirmed, true);
    assert.equal(meta.turn, turn);
    assert.deepEqual(pcm, new Int16Array(960).fill(turn));
  }
  assert.equal(played.length, 3);
  assert.deepEqual(drained, [1, 2, 3]);
});
test('Web Audio queues buffered PCM at contiguous sample boundaries and records played samples',async t=>{
 const {Player}=await load();t.mock.method(global,'setTimeout',()=>1);t.mock.method(global,'clearTimeout',()=>{});
 const played=[];const p=new Player(()=>{},(pcm,meta)=>played.push([pcm.length,meta]),()=>{});p.ctx=context();p.rate=16000;p.decoder={decode:()=>new Int16Array(960)};
 for(let i=1;i<=6;i++)await p.feed(new Uint8Array([1]),{turn:1,audio_seq:i});
 const entries=[...p.active];assert.equal(entries.length,6);
 entries.slice(1).forEach((e,i)=>assert.ok(Math.abs(e.start-entries[i].start-.06)<1e-9));
 p.ctx.currentTime=entries[0].start+.025;p.stop();assert.equal(played.length,1);assert.ok(Math.abs(played[0][0]-400)<=1);assert.equal(p.busy,false);
});
test('stop drops queued decode from the cancelled generation',async t=>{
 const {Player}=await load();t.mock.method(global,'clearTimeout',()=>{});
 let count=0;const p=new Player(()=>count++,()=>{},()=>{});p.ctx=context();p.rate=16000;p.decoder={decode:()=>new Int16Array(960)};
 const pending=p.feed(new Uint8Array([1]),{turn:1});p.stop();await pending;assert.equal(count,0);assert.equal(p.busy,false);
});
test('short final audio flushes below buffering threshold and drains exactly once',async t=>{
 const {Player}=await load();t.mock.method(global,'setTimeout',()=>1);t.mock.method(global,'clearTimeout',()=>{});
 const drains=[];const p=new Player(()=>{},()=>{},owner=>drains.push(owner));p.ctx=context();p.rate=16000;p.decoder={decode:()=>new Int16Array(960)};
 await p.feed(new Uint8Array([1]),{turn:2});assert.equal(p.active.size,0);await p.flush(2);assert.equal(p.active.size,1);
 [...p.active][0].node.onended();p.pump();assert.deepEqual(drains,[2]);p.stop();
});
test('changes in output timestamp snapshots cannot introduce inter-frame overlap',async t=>{
 t.mock.method(performance,'now',()=>1000);
 const {Player}=await load();const p=new Player(()=>{},()=>{},()=>{});let snapshot=1000;p.ctx={currentTime:1,getOutputTimestamp:()=>({performanceTime:snapshot--,contextTime:1})};
 assert.equal(p.wallStart(1.06)-p.wallStart(1),60000000);
 assert.equal(p.clockEvidence.timing_method,'output_timestamp');
});

test('playback clock follows real performance time after output clock drift',async t=>{
 const {Player}=await load();t.mock.method(global,'setTimeout',()=>1);t.mock.method(global,'clearTimeout',()=>{});
 let now=1000; t.mock.method(performance,'now',()=>now);
 const p=new Player(()=>{},()=>{},()=>{});p.ctx=context();
 p.ctx.getOutputTimestamp=()=>({contextTime:p.ctx.currentTime,performanceTime:now});
 p.rate=16000;p.decoder={decode:()=>new Int16Array(960)};
 await p.feed(new Uint8Array([1]),{turn:1,received:1000000000});await p.flush(1);
 const first=[...p.active][0];p.ctx.currentTime=first.start+.06;first.node.onended();p.pump();
 // A suspended/stalled audio clock must not move later replies into the past.
 now=6000;p.ctx.currentTime=2;
 await p.feed(new Uint8Array([1]),{turn:2,received:6000000000});await p.flush(2);
 const second=[...p.active][0];
 assert.ok(second.at>=6000000000,`reply was stamped ${second.at/1e9}s, before its 6s arrival`);
 assert.equal(second.at,6020000000);
 p.stop();
});

test('startup output timestamp cannot put playback before packet arrival',async t=>{
 const {Player}=await load();t.mock.method(global,'setTimeout',()=>1);t.mock.method(global,'clearTimeout',()=>{});
 t.mock.method(performance,'now',()=>5000);
 const p=new Player(()=>{},()=>{},()=>{});p.ctx=context();
 p.ctx.getOutputTimestamp=()=>({contextTime:3,performanceTime:5000});
 p.rate=16000;p.decoder={decode:()=>new Int16Array(960)};
 await p.feed(new Uint8Array([1]),{turn:1,received:5000000000});await p.flush(1);
 assert.ok([...p.active][0].at>=5000000000);
 p.stop();
});

test('a clock jump crossing an unobserved start retains evidence but is estimated',async t=>{
 const {Player}=await load();t.mock.method(global,'setTimeout',()=>1);t.mock.method(global,'clearTimeout',()=>{});
 let now=1000;t.mock.method(performance,'now',()=>now);
 const played=[];const p=new Player(()=>{},(pcm,meta)=>played.push(meta),()=>{});p.ctx=context();
 p.ctx.getOutputTimestamp=()=>({contextTime:p.ctx.currentTime,performanceTime:now});
 p.rate=16000;p.decoder={decode:()=>new Int16Array(960)};
 await p.feed(new Uint8Array([1]),{turn:1,received:1000000000});await p.flush(1);
 const entry=[...p.active][0];
 // The output device pauses before scheduled playback, then resumes.
 p.ctx.currentTime=1.08;now=6080;entry.node.onended();
 assert.equal(played[0].at,6020000000);
 assert.equal(played[0].scheduled_at_ns,1020000000);
 assert.equal(played[0].playback_observed_at_ns,6080000000);
 assert.equal(played[0].timing_model,'browser_audio_context_v2');
 assert.equal(played[0].timing_method,'context_clock_estimate');
 assert.equal(played[0].clock_issue,'clock_discontinuity');
 assert.equal(played[0].output_performance_time,6080);
 p.stop();
});

test('an observed output start survives an ended callback delayed across suspension',async t=>{
 const {Player}=await load();t.mock.method(global,'setTimeout',()=>1);t.mock.method(global,'clearTimeout',()=>{});
 let now=1000,output=.98;t.mock.method(performance,'now',()=>now);
 const played=[];const p=new Player(()=>{},(_pcm,meta)=>played.push(meta),()=>{});p.ctx=context();p.ctx.sampleRate=48000;
 p.ctx.getOutputTimestamp=()=>({contextTime:output,performanceTime:now});
 p.rate=16000;p.decoder={decode:()=>new Int16Array(960)};
 await p.feed(new Uint8Array([1]),{turn:1,received:1e9});await p.flush(1);const entry=[...p.active][0];
 now=1050;p.ctx.currentTime=1.05;output=1.03;p.pump();
 now=1100;p.ctx.currentTime=1.1;output=1.08;p.pump();
 now=6100;p.ctx.currentTime=1.12;output=1.1;entry.node.onended();
 assert.equal(played.length,1);assert.equal(played[0].at,1040000000);
 assert.equal(played[0].clock_observed_at_ns,1050000000);
 assert.equal(played[0].playback_observed_at_ns,6100000000);
 assert.equal(played[0].timing_method,'output_timestamp');p.stop();
});

test('a clock jump inside an observed frame preserves its start and downgrades its span',async t=>{
 const {Player}=await load();t.mock.method(global,'setTimeout',()=>1);t.mock.method(global,'clearTimeout',()=>{});
 let now=1000,output=.98;t.mock.method(performance,'now',()=>now);
 const played=[];const p=new Player(()=>{},(_pcm,meta)=>played.push(meta),()=>{});p.ctx=context();p.ctx.sampleRate=48000;
 p.ctx.getOutputTimestamp=()=>({contextTime:output,performanceTime:now});p.rate=16000;p.decoder={decode:()=>new Int16Array(960)};
 await p.feed(new Uint8Array([1]),{turn:1,received:1e9});await p.flush(1);const entry=[...p.active][0];
 now=1050;p.ctx.currentTime=1.05;output=1.03;p.pump();
 now=6100;p.ctx.currentTime=1.1;output=1.08;entry.node.onended();
 assert.equal(played[0].at,1040000000);assert.equal(played[0].timing_method,'context_clock_estimate');
 assert.equal(played[0].clock_issue,'clock_discontinuity');p.stop();
});

test('render completion waits for output confirmation without adding a playback buffer',async t=>{
 const {Player}=await load();t.mock.method(global,'setTimeout',()=>1);t.mock.method(global,'clearTimeout',()=>{});
 let now=1000,output=.8;t.mock.method(performance,'now',()=>now);
 const played=[],drains=[];const p=new Player(()=>{},(_pcm,meta)=>played.push(meta),owner=>drains.push(owner));p.ctx=context();p.ctx.sampleRate=48000;
 p.ctx.getOutputTimestamp=()=>({contextTime:output,performanceTime:now});p.rate=16000;p.decoder={decode:()=>new Int16Array(960)};
 await p.feed(new Uint8Array([1]),{turn:1,received:1e9});await p.flush(1);const entry=[...p.active][0];
 assert.equal(entry.node.started,1.02);
 now=1080;p.ctx.currentTime=1.08;output=.88;entry.node.onended();p.pump();
 assert.equal(played.length,0);assert.equal(drains.length,0);assert.equal(p.active.size,0);assert.equal(p.busy,true);
 now=1220;p.ctx.currentTime=1.22;output=1.02;p.pump();assert.equal(played.length,0);
 now=1280;p.ctx.currentTime=1.28;output=1.08;p.pump();
 assert.equal(played[0].at,1220000000);assert.equal(played[0].timing_method,'output_timestamp');
 assert.equal(played[0].output_start_confirmed,true);assert.equal(played[0].output_end_confirmed,true);
 assert.deepEqual(drains,[1]);assert.equal(p.busy,false);p.stop();
});

test('a backwards output-clock correction cannot masquerade as accurate overlapping frames',async t=>{
 const {Player}=await load();t.mock.method(global,'setTimeout',()=>1);t.mock.method(global,'clearTimeout',()=>{});
 let now=1000,output=.97;t.mock.method(performance,'now',()=>now);
 const played=[];const p=new Player(()=>{},(_pcm,meta)=>played.push(meta),()=>{});p.ctx=context();p.ctx.sampleRate=48000;
 p.ctx.getOutputTimestamp=()=>({contextTime:output,performanceTime:now});p.rate=16000;p.decoder={decode:()=>new Int16Array(960)};
 await p.feed(new Uint8Array([1]),{turn:1,received:1e9});await p.feed(new Uint8Array([1]),{turn:1,received:1e9});await p.flush(1);
 const [first,second]=[...p.active];
 now=1080;p.ctx.currentTime=1.08;output=1.05;p.pump();first.node.onended();
 now=1110;p.ctx.currentTime=1.11;output=1.08;p.pump();
 now=1140;p.ctx.currentTime=1.14;output=1.12;second.node.onended();
 now=1160;p.ctx.currentTime=1.16;output=1.14;p.pump();
 assert.equal(played.length,2);assert.equal(played[0].timing_method,'output_timestamp');
 assert.equal(played[1].timing_method,'context_clock_estimate');assert.equal(played[1].clock_issue,'clock_discontinuity');p.stop();
});

test('a mapping conflict with the preceding frame remains explicitly estimated',async t=>{
 const {Player}=await load();t.mock.method(global,'setTimeout',()=>1);t.mock.method(global,'clearTimeout',()=>{});
 let now=1000,output=.97;t.mock.method(performance,'now',()=>now);
 const played=[];const p=new Player(()=>{},(_pcm,meta)=>played.push(meta),()=>{});p.ctx=context();p.ctx.sampleRate=48000;
 p.ctx.getOutputTimestamp=()=>({contextTime:output,performanceTime:now});p.rate=16000;p.decoder={decode:()=>new Int16Array(960)};
 await p.feed(new Uint8Array([1]),{turn:1,received:1e9});await p.feed(new Uint8Array([1]),{turn:1,received:1e9});await p.flush(1);
 const [first,second]=[...p.active];
 now=1080;p.ctx.currentTime=1.08;output=1.05;p.pump();first.node.onended();
 now=1090;p.ctx.currentTime=1.09;output=1.07;p.pump();
 now=1100;p.ctx.currentTime=1.1;output=1.08;p.pump();
 now=1160;p.ctx.currentTime=1.16;output=1.14;second.node.onended();
 assert.equal(played.length,2);assert.equal(played[0].at,1050000000);assert.equal(played[1].at,1100000000);
 assert.equal(played[1].timing_method,'context_clock_estimate');assert.equal(played[1].clock_issue,'overlapping_output_mapping');p.stop();
});

test('stop preserves rendered but unconfirmed output as an estimate',async t=>{
 const {Player}=await load();t.mock.method(global,'setTimeout',()=>1);t.mock.method(global,'clearTimeout',()=>{});
 let now=1000,output=.8;t.mock.method(performance,'now',()=>now);
 const played=[];const p=new Player(()=>{},(_pcm,meta)=>played.push(meta),()=>{});p.ctx=context();p.ctx.sampleRate=48000;
 p.ctx.getOutputTimestamp=()=>({contextTime:output,performanceTime:now});p.rate=16000;p.decoder={decode:()=>new Int16Array(960)};
 await p.feed(new Uint8Array([1]),{turn:1,received:1e9});await p.flush(1);const entry=[...p.active][0];
 now=1080;p.ctx.currentTime=1.08;output=.88;entry.node.onended();assert.equal(played.length,0);p.stop();
 assert.equal(played.length,1);assert.equal(played[0].timing_method,'context_clock_estimate');
 assert.equal(played[0].clock_issue,'output_unconfirmed');assert.equal(played[0].output_start_confirmed,false);
 assert.equal(played[0].output_end_confirmed,false);assert.equal(p.busy,false);
});

test('a very short frame with tolerated clock jitter can finish confirmation on the next pump',async t=>{
 const {Player}=await load();t.mock.method(global,'setTimeout',()=>1);t.mock.method(global,'clearTimeout',()=>{});
 let now=1000,output=1;t.mock.method(performance,'now',()=>now);
 const played=[];const p=new Player(()=>{},(_pcm,meta)=>played.push(meta),()=>{});p.ctx=context();p.ctx.sampleRate=48000;
 p.ctx.getOutputTimestamp=()=>({contextTime:output,performanceTime:now});p.rate=16000;p.decoder={decode:()=>new Int16Array(40)};
 await p.feed(new Uint8Array([1]),{turn:1,received:1e9});await p.flush(1);const entry=[...p.active][0];
 now=1018;p.ctx.currentTime=1.03;output=1.023;entry.node.onended();assert.equal(played.length,0);
 now=1030;p.ctx.currentTime=1.04;output=1.035;p.pump();
 assert.equal(played.length,1);assert.ok(played[0].at<=played[0].playback_observed_at_ns);
 assert.equal(p.busy,false);p.stop();
});
