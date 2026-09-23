const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const vm = require('node:vm');
const ranges = () => vm.runInNewContext(readFileSync('voice_scenarios/session_timeline.js', 'utf8') + '\nsessionInputRanges;');

test('one PTT recording is one bar across fragmented speech and silence without moving its boundaries', () => {
  const row = {index:2, input_start_seconds:14.01, input_end_seconds:17.6, input_segments:[
    {kind:'speech',start_seconds:14.01,end_seconds:14.2},
    {kind:'background',start_seconds:14.2,end_seconds:14.3},
    {kind:'speech',start_seconds:14.32,end_seconds:17.66},
  ]};
  const original = JSON.stringify(row);
  const result = ranges()(row, [{turn_index:2,kind:'ptt_start',at_seconds:14},{turn_index:2,kind:'ptt_stop',at_seconds:17.6}]);
  assert.equal(result.length,1);
  assert.equal(result[0].start_seconds,14);
  assert.equal(result[0].end_seconds,17.6);
  assert.equal(result[0].kind,'speech');
  assert.equal(JSON.stringify(row),original);
});

test('VAD has one spoken interval and separate leading and trailing background', () => {
  const result = ranges()({index:1,input_start_seconds:1,input_end_seconds:3,input_segments:[
    {kind:'background',start_seconds:1,end_seconds:1.5},
    {kind:'speech',start_seconds:1.5,end_seconds:2},
    {kind:'background',start_seconds:2,end_seconds:2.2},
    {kind:'speech',start_seconds:2.2,end_seconds:3},
    {kind:'background',start_seconds:3,end_seconds:5},
  ]}, []);
  assert.deepEqual(Array.from(result,r=>[r.kind,r.start_seconds,r.end_seconds]),[
    ['background',1,1.5],['speech',1.5,3],['background',3,5],
  ]);
});

test('missing PTT stop uses captured range without fabricating a stop, and recordings never join turns', () => {
  const rows = [1,2].map(index=>({index,input_segments:[{kind:'speech',start_seconds:index*10,end_seconds:index*10+1}]}));
  const markers = [{turn_index:1,kind:'ptt_start',at_seconds:9.9}];
  assert.deepEqual(rows.flatMap(row=>Array.from(ranges()(row,markers),r=>[r.start_seconds,r.end_seconds])),[[9.9,11],[20,21]]);
  assert.equal(ranges()({index:3,input_segments:[]},[{turn_index:3,kind:'ptt_start',at_seconds:30}]).length,0);
});
