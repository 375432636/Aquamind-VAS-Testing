const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const vm = require('node:vm');

function renderer() {
  const measurements = [];
  const detail = {hidden:true, textContent:''};
  const root = {innerHTML:'', clientWidth:1000, contains:()=>true, querySelector:()=>detail};
  const context = {document:{getElementById:id=>id==='data'?{textContent:'{"turn":null}'}:root, addEventListener(){}},
    window:{addEventListener(){}}, URL, renderSessionTimeline(){}, enableTimelineMeasurement(...args){measurements.push(args);}};
  const render = vm.runInNewContext(readFileSync('voice_scenarios/media_markers.js','utf8')+'\n'+readFileSync('voice_scenarios/report.js','utf8')+'\ntimeline;',context);
  return {root,detail,render,measurements};
}

test('milestones are accessible buttons on the existing lane, including close points',()=>{
  const {root,detail,render}=renderer();
  render('server',[{label:'LLM #1',segments:[{label:'LLM',start:0,end:2e9,data:{model:'qwen-test',adapter:'openai'}}],
    markers:[{label:'First Token',start:1e9,since_request_seconds:1}, {label:'首个播报文本',start:1.001e9}]}]);
  assert.match(root.innerHTML,/timeline-marker/);
  assert.match(root.innerHTML,/aria-label="[^"]*First Token/);
  assert.match(root.innerHTML,/qwen-test/);
  assert.equal((root.innerHTML.match(/class="lane"/g)||[]).length,1);
  root.onclick({target:{closest:()=>({dataset:{markerIndex:'0'}})}});
  assert.equal(detail.hidden,false);
  assert.match(detail.textContent,/First Token/);
  assert.match(detail.textContent,/首个播报文本/);
  assert.match(detail.textContent,/1\.000 s/);
});

test('marker-only incomplete requests keep a valid time axis',()=>{
  const {root,render}=renderer();
  render('server',[{label:'ASR',segments:[],markers:[{label:'ASR 最终识别结果',start:12e9}]}]);
  assert.match(root.innerHTML,/timeline-marker/);
  assert.doesNotMatch(root.innerHTML,/NaN|Infinity/);
});

test('business evidence shows readable requirements and explicit unknown values',()=>{
  const context={document:{getElementById:()=>({textContent:'{"turn":null}'}),addEventListener(){}},window:{addEventListener(){}},renderSessionTimeline(){}};
  const format=vm.runInNewContext(readFileSync('voice_scenarios/report.js','utf8')+'\n({expected:checkExpectation,actual:checkActual});',context);
  assert.match(format.expected({category:'tools',expected:{required:[{name:'News-getTodayNewsByTopic'}],forbidden:['DrawLots-drawLot']}}),/必须.*News-getTodayNewsByTopic.*禁止.*DrawLots-drawLot/);
  assert.equal(format.actual({category:'audio',actual:{decodable:true,duration_ms:1200,ending:'normal'}}),'可解码 · 1.20 s · 正常结束');
  assert.equal(format.actual({category:'reply',actual:{text:null}}),'未知');
});

test('image points share one lane and expose arrival time and safe links on click',()=>{
  const {root,detail,render}=renderer();
  render('client',[{label:'图片到达',segments:[],markers:[
    {label:'图片 #1 到达',kind:'image',start:2e9,url:'https://example.com/a.png'},
    {label:'图片 #2 到达',kind:'image',start:2.001e9,url:'javascript:alert(1)'},
  ]}]);
  assert.equal((root.innerHTML.match(/class="lane"/g)||[]).length,1);
  assert.match(root.innerHTML,/aria-label="[^"]*图片 #1 到达/);
  root.onclick({target:{closest:()=>({dataset:{markerIndex:'0'}})}});
  assert.equal(detail.hidden,false);
  assert.match(detail.innerHTML,/客户端收到图片地址/);
  assert.match(detail.innerHTML,/https:\/\/example.com\/a.png/);
  assert.doesNotMatch(detail.innerHTML,/href="javascript:/);
});

test('mixed video and image markers open a single detail panel with both links',()=>{
  const {root,detail,render}=renderer();
  render('client',[{label:'视频 / 图片到达',segments:[],markers:[
    {label:'视频 #1 到达',kind:'video',start:2e9,url:'https://example.com/demo.mp4'},
    {label:'图片 #1 到达',kind:'image',start:2e9,url:'https://example.com/poster.png'},
  ]}]);
  assert.equal((root.innerHTML.match(/data-marker-index=/g)||[]).length,1);
  assert.match(root.innerHTML,/aria-label="[^"]*视频 #1 到达[^"]*图片 #1 到达/);
  root.onclick({target:{closest:()=>({dataset:{markerIndex:'0'}})}});
  assert.equal(detail.hidden,false);
  assert.match(detail.innerHTML,/查看视频/);
  assert.match(detail.innerHTML,/查看图片/);
  assert.equal((detail.innerHTML.match(/开始 0.000 s/g)||[]).length,2);
});

test('one chart shows client and VAS with Beijing wall time and monotonic durations',()=>{
  const {root,detail,render}=renderer();
  const epoch=Date.UTC(2026,8,14,8,0,0);
  render('combined',[
    {label:'客户端 · 发送音频',source:'client',segments:[],markers:[{label:'语音结束',start:0,wall_time_ms:epoch}]},
    {label:'VAS · LLM #1',source:'server',segments:[{label:'LLM',start:3e9,end:5.1e9,duration_ms:2000,wall_time_ms:epoch+3000}],
      markers:[{label:'First Token',start:4e9,monotonic_start_ns:101e9,wall_time_ms:epoch+4000,since_request_seconds:1,data:{text_received_ns:100e9}}]},
  ],{mode:'wall',origin_wall_time_ms:epoch});
  assert.match(root.innerHTML,/16:00:00/);
  assert.doesNotMatch(root.innerHTML,/08:00:00|00:00:00/);
  assert.match(root.innerHTML,/客户端 · 发送音频/);
  assert.match(root.innerHTML,/VAS · LLM #1/);
  assert.match(root.innerHTML,/耗时 2.00 s/);
  root.onclick({target:{closest:()=>({dataset:{markerIndex:'1'}})}});
  assert.match(detail.textContent,/北京时间.*2026-09-14 16:00:04/);
  assert.match(detail.textContent,/触发文本入队后 1.000 s/);
  assert.match(detail.textContent,/距本次请求提交 1.000 s/);
});

test('legacy relative chart keeps genuine server wall times only in details',()=>{
  const {root,detail,render}=renderer();
  render('combined',[{label:'VAS',segments:[],markers:[{label:'ASR',start:0,wall_time_ms:Date.UTC(2026,8,14,16,0,0)}]}],{mode:'relative'});
  assert.match(root.innerHTML,/<span>0 s<\/span>/);
  root.onclick({target:{closest:()=>({dataset:{markerIndex:'0'}})}});
  assert.match(detail.textContent,/北京时间.*2026-09-15 00:00:00/);
});

test('embedded VAS lanes use the session extent and common details without a second ruler',()=>{
  const {root,render,measurements}=renderer();
  const detail={hidden:true,textContent:''};
  const controller=render('session-vas',[{label:'第 2 轮 · LLM #1',segments:[{
    label:'LLM #1',turn_index:2,start:3e9,end:5e9,duration_ms:2000,data:{vendor:'DashScope',model:'qwen-test'}
  }],markers:[{label:'First Token',turn_index:2,start:4e9,since_request_seconds:1}]}],{},
  {embedded:true,start_ns:-2e9,end_ns:8e9,time_zero_ns:0,detail});
  assert.doesNotMatch(root.innerHTML,/class="axis"|class="span-detail"/);
  assert.equal(measurements.length,0);
  assert.match(root.innerHTML,/left:50%;width:20%/);
  assert.match(root.innerHTML,/第 2 轮 · LLM #1.*开始 3.000 s/);
  assert.equal(controller.activate({closest:()=>({dataset:{markerIndex:'0'}})}),true);
  assert.match(detail.textContent,/第 2 轮 · First Token.*距本次请求提交 1.000 s/);
  controller.activate({closest:()=>({dataset:{eventIndex:'0'}})});
  assert.match(detail.textContent,/DashScope · qwen-test/);
});


test('overview milestone clicks show request mode and ASR first character',()=>{
  const {root,render}=renderer();
  const detail={hidden:true,textContent:''};
  const controller=render('session-vas',[{label:'第 3 轮 · LLM #1 · Unthinking',segments:[],markers:[
    {label:'ASR 首字（首个非空中间结果）',turn_index:3,start:1e9,since_request_seconds:.2},
    {label:'First Token',turn_index:3,start:3e9,thinking_mode:'Unthinking',since_request_seconds:.3},
  ]}],{}, {embedded:true,start_ns:0,end_ns:5e9,detail});
  assert.match(root.innerHTML,/ASR 首字/);
  assert.match(root.innerHTML,/First Token · Unthinking/);
  controller.activate({closest:()=>({dataset:{markerIndex:'1'}})});
  assert.match(detail.textContent,/第 3 轮 · First Token · Unthinking/);
  assert.match(detail.textContent,/距本次请求提交 0.300 s/);
});
