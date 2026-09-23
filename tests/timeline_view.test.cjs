const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const vm = require('node:vm');

function helpers() {
  return vm.runInNewContext(readFileSync('voice_scenarios/session_timeline.js', 'utf8') + '\n({timelineLaneVisible, timelineViewMode});');
}

test('detail-only view hides critical pairs but keeps VAD and every request type', () => {
  const {timelineLaneVisible: visible} = helpers();
  assert.equal(visible('key_moments', 'details'), false);
  for (const category of ['vad', 'asr_request', 'llm_request', 'tts_request', 'memory_request', 'tool_call', 'unknown']) {
    assert.equal(visible(category, 'details'), true);
    assert.equal(visible(category, 'key'), false);
    assert.equal(visible(category, 'all'), true);
  }
  assert.equal(visible('key_moments', 'key'), true);
  assert.equal(visible('key_moments', 'all'), true);
});

test('overview and turn views restore all three preferences, old reports keep their details visible', () => {
  const {timelineViewMode: mode} = helpers();
  const lanes = [{category:'key_moments'}, {category:'llm_request'}];
  for (const preference of ['key', 'details', 'all']) assert.equal(mode(preference, lanes), preference);
  assert.equal(mode(null, lanes), 'key');
  assert.equal(mode('invalid', lanes), 'key');
  assert.equal(mode('key', [{category:'llm_request'}]), 'details');
});

test('opening and closing evidence details resizes matching labels and releases old height',()=>{
  const sync = vm.runInNewContext(readFileSync('voice_scenarios/session_timeline.js','utf8')+'\nsyncTimelineRowHeights;');
  function node(height,hidden=false){return {height,hidden,style:{minHeight:'400px'},getBoundingClientRect(){return {height:Math.max(this.height,parseFloat(this.style.minHeight)||0)};}};}
  const rows=[node(280),node(60),node(100,true)], labels=[node(30),node(40),node(30)];
  assert.equal(sync(rows,labels),340);
  assert.equal(labels[0].style.minHeight,'280px');
  assert.equal(rows[1].style.minHeight,labels[1].style.minHeight);
  rows[0].height=80;
  assert.equal(sync(rows,labels),140);
  assert.equal(labels[0].style.minHeight,'80px');
});
