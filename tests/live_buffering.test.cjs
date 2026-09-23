const test = require('node:test');
const assert = require('node:assert/strict');

async function harness(t) {
  const {Player} = await import('../voice_scenarios/live/audio.mjs');
  let now = 0, serial = 0;
  const tasks = new Map(), scheduled = [], played = [], states = [], drains = [];
  const later = (fn, ms) => { const id = ++serial; tasks.set(id, {at:now + ms, fn}); return id; };
  t.mock.method(global, 'setTimeout', later);
  t.mock.method(global, 'clearTimeout', id => tasks.delete(id));
  t.mock.method(performance, 'now', () => 1000 + now);
  const ctx = {
    get currentTime() { return 1 + now / 1000; }, sampleRate:48000, destination:{},
    getOutputTimestamp() { return {contextTime:this.currentTime, performanceTime:1000 + now}; },
    createBuffer(_channels, count, rate) { return {duration:count / rate, copyToChannel(){}}; },
    createBufferSource() { return {
      connect(){}, disconnect(){},
      start(at) { this.started = at; scheduled.push(this); this.endTask = later(() => this.onended?.(), (at + this.buffer.duration - ctx.currentTime) * 1000); },
      stop() { tasks.delete(this.endTask); },
    }; },
  };
  const p = new Player(() => {}, (pcm, meta) => played.push({pcm, meta}), owner => drains.push(owner), (name, data) => states.push({name, data}));
  p.ctx = ctx; p.rate = 16000; p.decoder = {decode:bytes => new Int16Array(960).fill(bytes[0])};
  t.after(() => p.stop());
  function advance(target) {
    let guard = 0;
    while (true) {
      const next = [...tasks].filter(([,value]) => value.at <= target + 1e-7).sort((a,b) => a[1].at - b[1].at)[0];
      if (!next) break;
      assert.ok(guard++ < 10000, 'timer must make progress');
      tasks.delete(next[0]); now = next[1].at; next[1].fn();
    }
    now = target;
  }
  async function feed(at, id, turn=1) { advance(at); await p.feed(new Uint8Array([id]), {turn, audio_seq:id, received:(1000 + at) * 1e6}); }
  async function finish(at, turn=1) { advance(at); await p.flush(turn); advance(at + 3000); }
  return {p, advance, feed, finish, scheduled, played, states, drains};
}

test('short startup cushions 55 ms packet jitter without gaps or sample loss', async t => {
  const h = await harness(t);
  const arrivals = [0, 100, 155, 180, 275, 300, 400, 420];
  for (const [i, at] of arrivals.entries()) await h.feed(at, i + 1);
  await h.finish(450);
  assert.equal(h.states.filter(x => x.name === 'playback_underrun').length, 0);
  assert.ok(h.scheduled[0].started <= 1.1, 'startup must remain at most 0.1 seconds');
  h.scheduled.slice(1).forEach((node, i) => assert.ok(Math.abs(node.started - (h.scheduled[i].started + .06)) < 1e-8));
  assert.deepEqual(h.played.map(x => [x.pcm.length, x.pcm[0], x.pcm.at(-1)]), arrivals.map((_,i) => [960,i+1,i+1]));
  assert.deepEqual(h.drains, [1]);
});

test('one undersupply rebuilds a bounded cushion instead of stuttering on every late packet', async t => {
  const h = await harness(t);
  const arrivals = [0,60,120,180,330,420,421,560,565,600,740,745,780,900,901,920,1080,1081,1100];
  for (const [i, at] of arrivals.entries()) await h.feed(at, i + 1);
  await h.finish(1120);
  const gaps = h.states.filter(x => x.name === 'playback_underrun');
  assert.ok(gaps.length <= 2, `repeated stutters: ${gaps.length}`);
  assert.ok(gaps.length > 0, 'real missing audio must remain visible');
  assert.ok(h.scheduled[4].started <= 1 + arrivals[4] / 1000 + .2, 'recovery wait must be bounded');
  assert.equal(h.played.reduce((n, x) => n + x.pcm.length, 0), arrivals.length * 960);
  assert.deepEqual(h.drains, [1]);
});

test('single packet without TTS stop has a startup deadline', async t => {
  const h = await harness(t);
  await h.feed(0, 1); h.advance(100);
  assert.equal(h.scheduled.length, 1);
  assert.ok(h.scheduled[0].started <= 1.1);
});

test('TTS stop flushes a tiny tail immediately and drain is emitted once', async t => {
  const h = await harness(t);
  await h.feed(0, 1); await h.finish(5);
  assert.ok(h.scheduled[0].started <= 1.025001);
  assert.equal(h.played[0].pcm.length, 960);
  assert.deepEqual(h.drains, [1]);
});

test('interrupting during startup cancels its timer and buffered audio before the next reply', async t => {
  const h = await harness(t);
  await h.feed(0, 1); h.advance(10); h.p.stop();
  h.advance(200);
  assert.equal(h.played.length, 0);
  assert.equal(h.p.busy, false);
  await h.feed(220, 2, 2); await h.finish(240, 2);
  assert.deepEqual(h.played.map(x => x.meta.turn), [2]);
  assert.deepEqual(h.drains, [2]);
});

test('a multi-second supply gap is retained, and resumed short speech is not lost', async t => {
  const h = await harness(t);
  await h.feed(0, 1); await h.feed(60, 2); h.advance(500);
  await h.feed(2000, 3); await h.finish(2010);
  assert.equal(h.played.length, 3);
  assert.ok(h.states.some(x => x.name === 'playback_underrun' && x.data.gap_seconds > 1));
  assert.ok(h.scheduled[2].started <= 3.030001);
});
