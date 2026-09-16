const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const vm = require('node:vm');

const render = vm.runInNewContext(
  readFileSync('voice_scenarios/reply_timing.js', 'utf8') + '\nrenderReplyTiming;'
);

for (const status of ['invalid', 'estimated', 'partial', 'valid']) {
  test(`reply details distinguish ${status} timing without inventing a playback position`, () => {
    const usable = status === 'partial' || status === 'valid';
    const target = {innerHTML:''};
    render(target, {
      playback_clock:{status},
      sentences:[{index:1, kind:'answer', text:'保留原始回答', status:usable ? 'completed' : 'timing_unavailable'}],
    }, {segments:usable ? [{turn_index:2, index:1, start_seconds:14.5, end_seconds:14.6}] : []}, 2);
    assert.match(target.innerHTML, /保留原始回答/);
    if (usable) {
      assert.match(target.innerHTML, /report\.html\?t=14\.500#session-timeline/);
      assert.match(target.innerHTML, /14\.50 s → 14\.60 s/);
      assert.match(target.innerHTML, status === 'partial' ? /未确认尾部/ : /时间与会话播放保持一致/);
    } else {
      assert.match(target.innerHTML, /播放时刻无法确认/);
      assert.match(target.innerHTML, /未记录播放时间/);
      assert.doesNotMatch(target.innerHTML, /class="button speech-session-link"/);
      assert.doesNotMatch(target.innerHTML, /时间与会话播放保持一致/);
    }
  });
}
