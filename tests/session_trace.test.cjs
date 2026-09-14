const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const vm = require('node:vm');

test('one session scale includes early and late VAS events without changing audio seek time',()=>{
  const scale=vm.runInNewContext(readFileSync('voice_scenarios/session_timeline.js','utf8')+'\nsessionTimelineScale;');
  const session={duration_seconds:20,vas_timeline:{axis_start_seconds:-2,axis_end_seconds:30}};
  const view=scale(session);
  assert.equal(view.extent,32);
  assert.equal(view.percent(0),6.25);
  assert.equal(view.percent(14),50);
  assert.equal(view.atFraction(.5),14);
  assert.equal(view.clamp(-1),-1);
  assert.equal(view.audioTime(-1),0);
  assert.equal(view.clamp(25),25);
  assert.equal(view.audioTime(25),20);
  assert.equal(session.duration_seconds,20);
  const legacy=scale({duration_seconds:20});
  assert.equal(legacy.percent(10),50);
  assert.equal(legacy.atFraction(.5),10);
  assert.ok(scale({}).extent>0);
});
