const test = require('node:test');
const assert = require('node:assert/strict');
test('browser uses reception time, correlates probes and emits samples', async () => {
  const {ClockSync} = await import('../voice_scenarios/live/clock_sync.mjs');
  const events=[]; let now=1000;
  const sync=new ClockSync(message => {
    now+=20;
    sync.receive({type:'clock_sync',request_id:message.request_id,server_session_id:'s',server_received_ns:'1010000000',server_sent_ns:'1010000000'},now);
  }, (...event)=>events.push(event), ()=>now);
  const result=await sync.collect();
  assert.equal(result.length,5);
  assert.equal(result[0].t4,'1020000000');
  assert.equal(sync.pending.size,0);
  assert.equal(events[0][0],'clock_sync_samples');
});
test('unsupported browser probe times out without failing conversation', async () => {
  const {ClockSync} = await import('../voice_scenarios/live/clock_sync.mjs');
  const sync=new ClockSync(()=>{},()=>{});
  assert.deepEqual(await sync.collect(),[]);
  assert.equal(sync.pending.size,0);
  assert.equal(sync.supported,false);
});
