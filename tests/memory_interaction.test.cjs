const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const vm = require('node:vm');

test('Memory disclosure and text selection keep native pointer ownership', () => {
  const source = readFileSync('voice_scenarios/session_timeline.js', 'utf8');
  const start = source.indexOf("  canvas.addEventListener('pointerdown'");
  const end = source.indexOf("  canvas.addEventListener('pointermove'", start);
  let listener, captures = 0, inspected = 0;
  const context = vm.createContext({
    extent: 10, inspectedTime: null, gesture: null, selection: null,
    timeAt: () => {inspected++; return 2;},
    canvas: {addEventListener: (_, callback) => {listener = callback;},
      setPointerCapture: () => {captures++;}},
  });
  vm.runInContext(source.slice(start, end), context);
  for (const tag of ['SUMMARY', 'PRE']) {
    const target = {tagName: tag, closest: selector =>
      selector.includes('details') ? {tagName: 'DETAILS'} : null};
    listener({pointerType: 'mouse', button: 0, pointerId: 1, target, clientX: 20});
    assert.equal(captures, 0, `${tag}: pointer capture retargets the native click away from Memory`);
    assert.equal(inspected, 0, `${tag}: reading Memory must not move the time cursor`);
  }
  listener({pointerType: 'mouse', button: 0, pointerId: 2,
    target: {closest: () => null}, clientX: 20});
  assert.equal(captures, 1, 'ordinary timeline drags still capture the pointer');
});
