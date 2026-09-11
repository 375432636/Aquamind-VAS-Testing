const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const vm = require('node:vm');

function renderer() {
  const detail = {hidden:true, textContent:''};
  const root = {innerHTML:'', clientWidth:1000, contains:()=>true, querySelector:()=>detail};
  const context = {document:{getElementById:id=>id==='data'?{textContent:'{"turn":null}'}:root, addEventListener(){}},
    window:{addEventListener(){}}, renderSessionTimeline(){}, enableTimelineMeasurement(){}};
  const render = vm.runInNewContext(readFileSync('voice_scenarios/report.js','utf8')+'\ntimeline;',context);
  return {root,detail,render};
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
