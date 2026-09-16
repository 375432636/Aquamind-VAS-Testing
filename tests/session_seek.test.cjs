const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const vm = require('node:vm');

// Exercise the renderer's actual seek path with asynchronously loaded audio.
function player(readyState, available) {
  const source = readFileSync('voice_scenarios/session_timeline.js', 'utf8');
  const seekSource = source.slice(source.indexOf('  function seek('), source.indexOf('  function showItem('));
  let resolveDownload, requests = 0;
  const audio = {readyState, duration:100, currentTime:0, paused:true, src:'session.wav',
    seekable:{length:available ? 1 : 0, start:()=>0, end:()=>100},
    load(){this.currentTime=0;this.readyState=1;}, play(){return Promise.resolve();}};
  const context = vm.createContext({audio,scale:{audioTime:Number},location:{protocol:'http:'},
    document:{createElement:()=>({setAttribute(){},remove(){}})},
    target:{querySelector:()=>({append(){}})},URL:{createObjectURL:()=> 'blob:session'},
    fetch:()=>{requests++;return new Promise(resolve=>{resolveDownload=resolve;});},
    positionPlayhead(){},revealTime(){}});
  vm.runInContext(`let pendingSeek=null, fullAudioRequest=null, fullAudioUrl=null; ${seekSource}`, context);
  return {audio, seek:time=>vm.runInContext(`seek(${time})`,context), requests:()=>requests,
    metadata:()=>vm.runInContext('if(pendingSeek!=null)seek(pendingSeek)',context),
    async download(){resolveDownload({ok:true,blob:async()=>({})});await vm.runInContext('fullAudioRequest',context);}};
}

test('turn auto-seek waits for audio metadata without an unnecessary full download',()=>{
  const p=player(0,false);p.seek(23.32);
  assert.equal(p.requests(),0);
  p.audio.readyState=1;p.audio.seekable.length=1;p.metadata();
  assert.equal(p.audio.currentTime,23.32);
});

test('static-server audio fallback preserves a newer seek completed while downloading',async()=>{
  const p=player(1,false);p.seek(23.32);
  assert.equal(p.requests(),1);
  p.audio.seekable.length=1;p.seek(42);
  assert.equal(p.audio.currentTime,42);
  await p.download();p.metadata();
  assert.equal(p.audio.currentTime,42);
});
