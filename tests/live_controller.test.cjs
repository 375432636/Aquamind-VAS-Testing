const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
async function setup(options={}){
 const handlers={};
 const elements={};for(const id of ['status','error','messages','environment','mac','token','ota','connect','finish','text','send','talk','interrupt','mode','mic-status','downloads','text-form','mic-device','mic-level','mic-check','mic-preview','input-control','voice-hint'])elements[id]={value:'',disabled:false,append(){},replaceChildren(){},scrollIntoView(){}};
 elements.mac.value='00:00:00:00:00:21';elements.environment.value='dev';elements.mode.value='manual';elements.ota.checked=false;
 const sockets=[],recorders=[];
 class Socket{
  constructor(url){this.url=String(url);this.readyState=1;this.sent=[];sockets.push(this);queueMicrotask(()=>this.onopen?.());}
  receive(value){this.onmessage?.({data:JSON.stringify(value)});}
  send(raw){if(options.failStop&&typeof raw==='string'&&JSON.parse(raw).type==='listen'&&JSON.parse(raw).state==='stop')throw Error('test stop send failed');this.sent.push(raw);if(typeof raw!=='string'||this.url.includes('/record'))return;const m=JSON.parse(raw);queueMicrotask(()=>{
   if(m.type==='diagnostics'){if(m.state==='start')this.receive({type:'diagnostics',state:'started',schema_version:1,level:'frame',server_session_id:'test'});else if(m.state==='finish')this.receive({type:'diagnostics',state:'events',schema_version:1,server_session_id:'test',events:[],complete:true,finished:true,next_seq:0,end_seq:0});else this.receive({type:'diagnostics',state:'error',error:'unsupported diagnostic control'});}
   if(m.type==='hello')this.receive({type:'hello',session_id:'test',audio_params:{format:'opus',sample_rate:16000}});
   if(m.type==='abort')this.receive({type:'tts',state:'stop'});
  });}
  close(){this.readyState=3;}
 }
 class Recorder{constructor(callback){this.callback=callback;recorders.push(this);}async prepare(){if(options.prepare)await options.prepare();}async start(){this.callback(new Int16Array(960).fill(options.silent?0:1024),new Uint8Array([1]),0);}async stop(){if(options.tail&&!this.stopped)this.callback(new Int16Array(960).fill(512),new Uint8Array([2]),960);this.stopped=true;}}
 class Player{constructor(a,b,c){this.onDrain=c;this.busy=false;this.chain=Promise.resolve();}async prime(){}async format(){}async flush(turn){this.onDrain(turn);}stop(){} }
 let now=0;
 const ctx={document:{getElementById:id=>elements[id],createElement:()=>({scrollIntoView(){}})},window:{addEventListener(name,fn){handlers[name]=fn;}},performance:{timeOrigin:1700000000000},location:{origin:'http://localhost'},localStorage:{getItem:()=> '00:00:00:00:00:21',setItem(){}},setTimeout,clearTimeout,setInterval(){},WebSocket:Socket,Recorder,Player,ns:()=>++now*1000000,encodePCM:()=> 'AQABAA==',Blob,URL,console,Uint8Array,Int16Array,fetch:async()=>({ok:true,json:async()=>({ws_url:'ws://local/vas',record_url:'/record',id:'1'})})};
 vm.createContext(ctx);vm.runInContext(fs.readFileSync('voice_scenarios/live/app.mjs','utf8').replace(/^import .*?;\n/,''),ctx);
 await elements.connect.onclick();await tick();assert.equal(elements.status.textContent,'已连接',elements.error.textContent);
 return {elements,handlers,recorders,peer:sockets[1],record:sockets[0],sent:()=>sockets[1].sent.filter(x=>typeof x==='string').map(JSON.parse)};
}
test('manual microphone sends a real frame before listen stop',async()=>{
 const {elements,sent,peer}=await setup();await elements.talk.onpointerdown({preventDefault(){},isTrusted:false});elements.talk.onpointerup();await tick();
 assert.ok(peer.sent.some(x=>x instanceof Uint8Array));assert.deepEqual(sent().filter(x=>x.type==='listen').map(x=>x.state),['start','stop']);
});
test('VAD endpoint stops microphone without sending manual stop',async()=>{
 const {elements,sent,peer}=await setup();elements.mode.value='vad';await elements.talk.onpointerdown({preventDefault(){},isTrusted:false});peer.receive({type:'stt',text:'模拟识别'});await tick();
 assert.deepEqual(sent().filter(x=>x.type==='listen').map(x=>[x.state,x.mode]),[['start','auto']]);
 assert.equal(elements['mic-status'].textContent,'麦克风已停止');
});
test('abort is acknowledged before a new text turn can start',async()=>{
 const {elements,sent,peer}=await setup();elements.text.value='第一轮';await elements['text-form'].onsubmit({preventDefault(){}});peer.receive({type:'tts',state:'start'});await tick();
 await elements.interrupt.onclick();await tick();elements.text.value='第二轮';await elements['text-form'].onsubmit({preventDefault(){}});
 assert.deepEqual(sent().filter(x=>x.type==='listen'&&x.state==='detect').map(x=>x.text),['第一轮','第二轮']);assert.equal(sent().filter(x=>x.type==='abort').length,1);
});

const key=(target={tagName:'BODY'},repeat=false)=>({code:'Space',key:' ',target,repeat,preventDefault(){},isComposing:false});
test('space anywhere outside editable fields starts one recording and release stops it',async()=>{
 const {handlers,sent}=await setup();assert.equal(typeof handlers.keydown,'function');
 handlers.keydown(key());handlers.keydown(key(undefined,true));await tick();handlers.keyup(key());await tick();
 assert.deepEqual(sent().filter(x=>x.type==='listen').map(x=>x.state),['start','stop']);
});
test('space in text field types normally; blur releases an active recording',async()=>{
 const {handlers,sent}=await setup();assert.equal(typeof handlers.keydown,'function');
 handlers.keydown(key({tagName:'INPUT'}));await tick();assert.equal(sent().filter(x=>x.type==='listen').length,0);
 handlers.keydown(key());await tick();handlers.blur();await tick();assert.equal(sent().filter(x=>x.state==='stop'&&x.type==='listen').length,1);
});
test('release during microphone initialization does not create an empty VAS turn',async()=>{
 let release;const pending=new Promise(r=>release=r);const {elements,sent}=await setup({prepare:()=>pending});
 const pressing=elements.talk.onpointerdown({preventDefault(){},isTrusted:false});await tick();elements.talk.onpointerup();release();await pressing;await tick();
 assert.equal(sent().filter(x=>x.type==='listen').length,0);
});
test('finish uses VAS finish control and waits for terminal diagnostics',async()=>{
 const {elements,sent,record}=await setup();await elements.finish.onclick();
 assert.equal(sent().filter(x=>x.type==='diagnostics').at(-1).state,'finish');
 assert.equal(JSON.parse(record.sent.at(-1)).complete,true);
});
test('holding space during a reply aborts it and starts the next recording',async()=>{
 const {elements,handlers,sent,peer}=await setup();elements.text.value='先讲一句';await elements['text-form'].onsubmit({preventDefault(){}});peer.receive({type:'stt',text:'先讲一句'});peer.receive({type:'tts',state:'start'});await tick();
 handlers.keydown(key());await tick();await tick();handlers.keyup(key());await tick();
 assert.equal(sent().filter(x=>x.type==='abort').length,1);
 assert.deepEqual(sent().filter(x=>x.type==='listen').map(x=>x.state),['start','detect','start','stop']);
});
test('all-zero microphone data is visibly flagged on release',async()=>{
 const {elements,handlers,record}=await setup({silent:true});handlers.keydown(key());await tick();handlers.keyup(key());await tick();await elements.finish.onclick();
 assert.match(elements['mic-status'].textContent,/未录到声音/);
 const events=record.sent.filter(x=>typeof x==='string').flatMap(x=>JSON.parse(x).events||[]);
 assert.equal(events.find(e=>e.event==='input_capture_finished').data.peak,0);
});

test('microphone check produces a local audible WAV without sending VAS audio',async()=>{
 const {elements,peer}=await setup();await elements['mic-check'].onclick();await elements['mic-check'].onclick();
 assert.match(elements['mic-status'].textContent,/已录到声音/);
 const recording=await fetch(elements['mic-preview'].src);const bytes=await recording.arrayBuffer();const wav=new DataView(bytes);
 assert.equal(wav.getUint32(24,true),16000);assert.equal(wav.getInt16(44,true),1024);
 assert.equal(peer.sent.filter(x=>x instanceof Uint8Array).length,0);
 URL.revokeObjectURL(elements['mic-preview'].src);
});
test('IME space shortcut does not begin a voice turn',async()=>{
 const {handlers,sent}=await setup();handlers.keydown({...key(),ctrlKey:true});await tick();
 assert.equal(sent().filter(x=>x.type==='listen').length,0);
});

test('ending the session cancels a microphone check still preparing and releases it',async()=>{
 let release;const pending=new Promise(r=>release=r);const {elements,recorders}=await setup({prepare:()=>pending});
 const checking=elements['mic-check'].onclick();const finishing=elements.finish.onclick();release();await checking;await finishing;
 assert.equal(recorders[0].stopped,true);
});


test('push-to-talk holds through silence and STT, then sends exactly one stop after the tail',async()=>{
 const {elements,handlers,recorders,peer,sent}=await setup({tail:true});
 handlers.keydown(key());await tick();
 assert.deepEqual(sent().filter(x=>x.type==='listen').map(x=>[x.state,x.mode]),[['start','manual']]);
 recorders[0].callback(new Int16Array(960),new Uint8Array([0]),960);
 peer.receive({type:'stt',text:'提前识别'});await tick();
 assert.equal(recorders[0].stopped,undefined);
 assert.equal(sent().filter(x=>x.type==='listen'&&x.state==='stop').length,0);
 handlers.keyup(key());handlers.blur();await tick();
 const uplink=peer.sent.filter(x=>x instanceof Uint8Array || (typeof x==='string'&&JSON.parse(x).type==='listen'));
 assert.deepEqual(uplink.map(x=>typeof x==='string'?JSON.parse(x).state:'audio'),['start','audio','audio','audio','stop']);
 assert.match(elements['input-control'].textContent,/已发送 stop/);
});

test('failed stop is recorded as failure rather than a sent stop',async()=>{
 const {elements,handlers,record}=await setup({failStop:true});
 handlers.keydown(key());await tick();handlers.keyup(key());await tick();await elements.finish.onclick();
 const events=record.sent.filter(x=>typeof x==='string').flatMap(x=>JSON.parse(x).events||[]);
 assert.equal(events.filter(e=>e.event==='listen_stop_sent').length,0);
 assert.equal(events.filter(e=>e.event==='listen_stop_failed').length,1);
 assert.match(elements['input-control'].textContent,/stop 发送失败/);
});
