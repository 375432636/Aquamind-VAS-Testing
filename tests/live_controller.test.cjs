const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
async function setup(options={}){
 const handlers={};
 const elements={};for(const id of ['status','error','messages','environment','5090-address-field','5090-address','mac','token','ota','connect','finish','text','send','talk','interrupt','mode','mic-status','downloads','text-form','mic-device','mic-level','mic-check','mic-preview','input-control','voice-hint'])elements[id]={value:'',disabled:false,hidden:false,append(){},replaceChildren(){},scrollIntoView(){}};
 elements.mac.value='00:00:00:00:00:21';elements.environment.value='dev';elements.mode.value='manual';elements.ota.checked=false;
 elements['5090-address'].value='5090-tailscale';
 const sockets=[],recorders=[],players=[],requests=[],encoded=[];
 elements.recording={checked:options.recording!==false,disabled:false};
 elements['robot-monitor']={checked:true,disabled:false};
 const robotFields=['called','action','product_refs','expression','navigation'].map(name=>({dataset:{robotField:name},value:name==='called'?'required':'monitor',disabled:false}));
 elements['clock-sync']={checked:false,disabled:false};
 class Socket{
  constructor(url){this.url=String(url);this.readyState=1;this.sent=[];sockets.push(this);queueMicrotask(()=>this.onopen?.());}
  receive(value){this.onmessage?.({data:JSON.stringify(value)});}
  send(raw){if(options.failStop&&typeof raw==='string'&&JSON.parse(raw).type==='listen'&&JSON.parse(raw).state==='stop')throw Error('test stop send failed');this.sent.push(raw);if(typeof raw!=='string'||this.url.includes('/record'))return;const m=JSON.parse(raw);queueMicrotask(()=>{
   if(m.type==='diagnostics'){if(m.state==='start')this.receive({type:'diagnostics',state:'started',schema_version:1,level:'frame',server_session_id:'test'});else if(m.state==='finish'&&!options.dropFinal)this.receive({type:'diagnostics',state:'events',schema_version:1,server_session_id:'test',events:[],complete:true,finished:true,next_seq:0,end_seq:0});else this.receive({type:'diagnostics',state:'error',error:'unsupported diagnostic control'});}
   if(m.type==='hello')this.receive({type:'hello',session_id:'test',audio_params:{format:'opus',sample_rate:16000}});
   if(m.type==='abort')this.receive({type:'tts',state:'stop'});
  });}
  close(){this.readyState=3;}
 }
 class Recorder{constructor(callback){this.callback=callback;recorders.push(this);}async prepare(){if(options.prepare)await options.prepare();}async start(){this.callback(new Int16Array(960).fill(options.silent?0:1024),new Uint8Array([1]),0);}async stop(){if(options.tail&&!this.stopped)this.callback(new Int16Array(960).fill(512),new Uint8Array([2]),960);this.stopped=true;}}
 class Player{constructor(a,b,c){players.push(this);this.onDrain=c;this.busy=false;this.chain=Promise.resolve();}async feed(){this.busy=true;}async prime(){}async format(){}async flush(turn){this.onDrain(turn);}stop(){} }
 let now=0;
 const {ClockSync} = await import('../voice_scenarios/live/clock_sync.mjs');
 const ctx={ClockSync,document:{getElementById:id=>elements[id],querySelectorAll:selector=>selector==='[data-robot-field]'?robotFields:[],createElement:()=>({scrollIntoView(){}})},window:{addEventListener(name,fn){handlers[name]=fn;}},performance:{timeOrigin:1700000000000,now:()=>++now},location:{origin:'http://localhost'},localStorage:{getItem:()=> '00:00:00:00:00:21',setItem(){}},setTimeout:(fn,ms)=>setTimeout(fn,options.dropFinal&&ms>=5000?5:ms),clearTimeout,setInterval(){},WebSocket:Socket,Recorder,Player,ns:()=>++now*1000000,encodePCM:()=> {encoded.push(true);return 'AQABAA==';},Blob,URL,console,Uint8Array,Int16Array,fetch:async(url,init)=>{requests.push({url,...init});return {ok:true,json:async()=>({ws_url:'ws://local/vas',...(JSON.parse(init?.body||'{}').recording===false?{}:{record_url:'/record',id:'1'})})};}};
 vm.createContext(ctx);vm.runInContext(fs.readFileSync('voice_scenarios/live/app.mjs','utf8').replace(/^import .*?;\n/gm,''),ctx);
 await elements.connect.onclick();await tick();assert.equal(elements.status.textContent,'已连接',elements.error.textContent);
 const peer=sockets.find(s=>s.url.startsWith('ws://local/vas')),record=sockets.find(s=>s.url.includes('/record'));
 return {elements,robotFields,handlers,recorders,players,peer,record,requests,encoded,sockets,sent:()=>peer.sent.filter(x=>typeof x==='string').map(JSON.parse)};
}
test('robot output expectations are sent with each recorded session',async()=>{
 const {elements,robotFields,requests}=await setup();
 assert.deepEqual(JSON.parse(requests[0].body).robot_output,{monitor:true,called:true});
 await elements.finish.onclick();
 robotFields.find(field=>field.dataset.robotField==='action').value='required';
 robotFields.find(field=>field.dataset.robotField==='navigation').value='forbidden';
 await elements.connect.onclick();
 assert.deepEqual(JSON.parse(requests.at(-1).body).robot_output,{monitor:true,called:true,action:true,navigation:false});
});
test('5090 environment exposes Tailscale and LAN endpoint selection',async()=>{
 const {elements,requests}=await setup();await elements.finish.onclick();
 elements.environment.value='5090';elements.environment.onchange();
 assert.equal(elements['5090-address-field'].hidden,false);
 elements['5090-address'].value='5090-lan';await elements.connect.onclick();
 assert.equal(JSON.parse(requests.at(-1).body).environment,'5090-lan');
 assert.equal(elements.environment.disabled,true);assert.equal(elements['5090-address'].disabled,true);
});
test('local environment is sent without a 5090 address override',async()=>{
 const {elements,requests}=await setup();await elements.finish.onclick();
 elements.environment.value='local';elements.environment.onchange();
 assert.equal(elements['5090-address-field'].hidden,true);
 await elements.connect.onclick();
 assert.equal(JSON.parse(requests.at(-1).body).environment,'local');
});
test('chat-only connects without diagnostics, clock sync, recording socket or reports',async()=>{
 const {elements,sent,record,peer,requests,sockets}=await setup({recording:false});
 assert.equal(JSON.parse(requests[0].body).recording,false);
 assert.equal(record,undefined);assert.equal(sockets.length,1);
 assert.equal(sent().some(m=>['diagnostics','clock_sync'].includes(m.type)),false);
 assert.equal(elements.recording.disabled,true);assert.equal(elements.finish.textContent,'断开连接');
 elements.text.value='直接对话';await elements['text-form'].onsubmit({preventDefault(){}});
 assert.equal(sent().find(m=>m.state==='detect').text,'直接对话');
 await elements.finish.onclick();
 assert.equal(peer.readyState,3);assert.equal(elements.downloads.hidden,true);
 assert.equal(elements.recording.disabled,false);assert.equal(elements.status.textContent,'已断开');
});
test('chat-only preserves PTT and continuous VAD without encoding audio for logs',async()=>{
 const {elements,handlers,recorders,peer,sent,encoded}=await setup({recording:false});
 handlers.keydown(key());await tick();handlers.keyup(key());await tick();
 assert.deepEqual(sent().filter(m=>m.type==='listen').map(m=>m.state),['start','stop']);
 await elements.interrupt.onclick();await tick();
 elements.mode.value='vad';await elements.talk.onpointerdown({preventDefault(){},isTrusted:false});
 peer.receive({type:'stt',text:'继续聊'});peer.receive({type:'tts',state:'start'});await tick();
 const recorder=recorders.at(-1);recorder.callback(new Int16Array(960),new Uint8Array([2]),960);
 assert.notEqual(recorder.stopped,true);assert.equal(encoded.length,0);
 assert.equal(sent().filter(m=>m.type==='listen').at(-1).mode,'auto');
 await elements.finish.onclick();assert.equal(recorder.stopped,true);
});
test('recording can be switched off and back on between connections',async()=>{
 const {elements,sockets}=await setup();
 await elements.finish.onclick();
 elements.recording.checked=false;elements.recording.onchange();
 assert.equal(elements.connect.textContent,'直接连接对话');
 await elements.connect.onclick();
 const chat=sockets.at(-1);
 assert.equal(chat.sent.filter(x=>typeof x==='string').map(JSON.parse).some(m=>m.type==='diagnostics'),false);
 await elements.finish.onclick();
 elements.recording.checked=true;elements.recording.onchange();
 await elements.connect.onclick();
 assert.equal(elements.finish.textContent,'结束并生成报告');
 assert.equal(sockets.at(-2).url,'ws://localhost/record');
 assert.ok(sockets.at(-1).sent.filter(x=>typeof x==='string').map(JSON.parse).some(m=>m.type==='diagnostics'&&m.state==='start'));
 await elements.finish.onclick();
});
test('manual microphone sends a real frame before listen stop',async()=>{
 const {elements,sent,peer}=await setup();await elements.talk.onpointerdown({preventDefault(){},isTrusted:false});elements.talk.onpointerup();await tick();
 assert.ok(peer.sent.some(x=>x instanceof Uint8Array));assert.deepEqual(sent().filter(x=>x.type==='listen').map(x=>x.state),['start','stop']);
});
test('VAD keeps uploading microphone frames through STT and reply playback',async()=>{
 const {elements,sent,peer,recorders}=await setup();elements.mode.value='vad';await elements.talk.onpointerdown({preventDefault(){},isTrusted:false});
 const mic=recorders[0];peer.receive({type:'stt',text:'模拟识别'});await tick();
 assert.notEqual(mic.stopped,true,'STT must not close the continuous VAD microphone');
 peer.receive({type:'tts',state:'start'});await tick();
 const count=peer.sent.filter(x=>x instanceof Uint8Array).length;
 mic.callback(new Int16Array(960),new Uint8Array([2]),960);
 mic.callback(new Int16Array(960).fill(1024),new Uint8Array([3]),1920);
 assert.equal(peer.sent.filter(x=>x instanceof Uint8Array).length,count+2);
 assert.deepEqual(sent().filter(x=>x.type==='listen').map(x=>[x.state,x.mode]),[['start','auto']]);
 assert.match(elements['mic-status'].textContent,/持续|正在聆听/);
 await elements.talk.onpointerdown({preventDefault(){},isTrusted:false});assert.equal(mic.stopped,true);
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


test('missing final diagnostics preserves successful client capture and records the cause',async()=>{
 const {elements,record}=await setup({dropFinal:true});
 await elements.finish.onclick();
 const messages=record.sent.filter(x=>typeof x==='string').map(JSON.parse);
 assert.equal(messages.at(-1).action,'finish');
 assert.equal(messages.at(-1).complete,true);
 const events=messages.flatMap(x=>x.events||[]);
 assert.equal(events.filter(e=>e.event==='diagnostics_finish_requested').length,1);
 assert.equal(events.filter(e=>e.event==='diagnostics_finish_timeout').length,1);
 assert.ok(events.some(e=>e.event==='connection_closed'));
});


test('VAD remains one live recorder after reply drain and uploads silence and next speech',async()=>{
 const {elements,sent,peer,record,recorders}=await setup();elements.mode.value='vad';await elements.talk.onpointerdown({preventDefault(){},isTrusted:false});
 const mic=recorders[0];peer.receive({type:'stt',text:'第一句'});peer.receive({type:'tts',state:'start'});await tick();
 peer.onmessage({data:new Uint8Array([9]).buffer});await tick();
 mic.callback(new Int16Array(960),new Uint8Array([2]),960);
 peer.receive({type:'tts',state:'stop'});await tick();
 mic.callback(new Int16Array(960).fill(1024),new Uint8Array([3]),1920);
 peer.receive({type:'stt',text:'第二句'});await tick();
 assert.equal(recorders.length,1);assert.notEqual(mic.stopped,true);
 assert.deepEqual(peer.sent.filter(x=>x instanceof Uint8Array).map(x=>x[0]),[1,2,3]);
 assert.deepEqual(sent().filter(x=>x.type==='listen').map(x=>[x.state,x.mode]),[['start','auto']]);
 await elements.talk.onpointerdown({preventDefault(){},isTrusted:false});assert.equal(mic.stopped,true);
 const stoppedCount=peer.sent.length;mic.callback(new Int16Array(960),new Uint8Array([4]),2880);assert.equal(peer.sent.length,stoppedCount);
 await elements.finish.onclick();
 const events=record.sent.filter(x=>typeof x==='string').flatMap(x=>JSON.parse(x).events||[]);
 assert.deepEqual(events.filter(e=>e.event==='turn_started').map(e=>e.turn),[1,2]);
 assert.deepEqual(events.filter(e=>e.event==='stt').map(e=>e.turn),[1,2]);
 assert.ok(events.some(e=>e.event==='input_audio_frame_sent'&&e.turn===2));
});

test('streaming STT updates before a reply stay in one VAD report turn',async()=>{
 const {elements,peer,record}=await setup();elements.mode.value='vad';await elements.talk.onpointerdown({preventDefault(){},isTrusted:false});
 peer.receive({type:'stt',text:'你可以'});peer.receive({type:'stt',text:'你可以听到我吗'});peer.receive({type:'tts',state:'start'});peer.receive({type:'tts',state:'stop'});await tick();
 await elements.talk.onpointerdown({preventDefault(){},isTrusted:false});await elements.finish.onclick();
 const events=record.sent.filter(x=>typeof x==='string').flatMap(x=>JSON.parse(x).events||[]);
 assert.deepEqual(events.filter(e=>e.event==='turn_started').map(e=>e.turn),[1]);
 assert.deepEqual(events.filter(e=>e.event==='stt').map(e=>e.turn),[1,1]);
});

test('PTT followed by two VAD utterances produces three turns in one report session',async()=>{
 const {elements,handlers,peer,record,recorders,sent}=await setup();
 handlers.keydown(key());await tick();handlers.keyup(key());await tick();
 peer.receive({type:'stt',text:'PTT 问题'});peer.receive({type:'tts',state:'start'});peer.receive({type:'tts',state:'stop'});await tick();
 elements.mode.value='vad';await elements.talk.onpointerdown({preventDefault(){},isTrusted:false});
 const mic=recorders.at(-1);peer.receive({type:'stt',text:'VAD 第一问'});peer.receive({type:'tts',state:'start'});peer.receive({type:'tts',state:'stop'});await tick();
 mic.callback(new Int16Array(960).fill(1024),new Uint8Array([3]),1920);peer.receive({type:'stt',text:'VAD 第二问'});await tick();
 await elements.talk.onpointerdown({preventDefault(){},isTrusted:false});await elements.finish.onclick();
 const events=record.sent.filter(x=>typeof x==='string').flatMap(x=>JSON.parse(x).events||[]);
 assert.deepEqual(events.filter(e=>e.event==='turn_started').map(e=>[e.turn,e.data.mode]),[[1,'manual'],[2,'vad'],[3,'vad']]);
 assert.deepEqual(events.filter(e=>e.event==='turn_started').map(e=>e.data.server_listen_turn_id),[1,2,2]);
 assert.deepEqual(events.filter(e=>e.event==='stt').map(e=>[e.turn,e.data.text]),[[1,'PTT 问题'],[2,'VAD 第一问'],[3,'VAD 第二问']]);
 assert.deepEqual(sent().filter(e=>e.type==='listen').map(e=>[e.state,e.mode]),[['start','manual'],['stop',undefined],['start','auto']]);
});

test('starting VAD during welcome playback does not abort or wait for the speaker',async()=>{
 const {elements,players,sent,recorders}=await setup();players[0].busy=true;elements.mode.value='vad';
 await elements.talk.onpointerdown({preventDefault(){},isTrusted:false});
 assert.equal(recorders.length,1);assert.notEqual(recorders[0].stopped,true);
 assert.equal(sent().filter(x=>x.type==='abort').length,0);
 assert.equal(sent().filter(x=>x.type==='listen'&&x.state==='start').length,1);
 await elements.talk.onpointerdown({preventDefault(){},isTrusted:false});
});

test('explicit reply interrupt leaves continuous VAD capture running',async()=>{
 const {elements,peer,sent,recorders}=await setup();elements.mode.value='vad';await elements.talk.onpointerdown({preventDefault(){},isTrusted:false});
 peer.receive({type:'stt',text:'第一句'});peer.receive({type:'tts',state:'start'});await tick();
 await elements.interrupt.onclick();await tick();
 assert.equal(sent().filter(x=>x.type==='abort').length,1);assert.notEqual(recorders[0].stopped,true);
 recorders[0].callback(new Int16Array(960),new Uint8Array([2]),960);
 assert.equal(peer.sent.filter(x=>x instanceof Uint8Array).length,2);
 await elements.talk.onpointerdown({preventDefault(){},isTrusted:false});
});

test('finish stops continuous VAD and drains its final frame before closing',async()=>{
 const {elements,recorders,peer}=await setup({tail:true});elements.mode.value='vad';await elements.talk.onpointerdown({preventDefault(){},isTrusted:false});
 peer.receive({type:'stt',text:'识别文本'});await tick();await elements.finish.onclick();
 assert.equal(recorders[0].stopped,true);assert.equal(peer.readyState,3);
 assert.deepEqual(peer.sent.filter(x=>x instanceof Uint8Array).map(x=>x[0]),[1,2]);
});
