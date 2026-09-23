const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
function setup(){
 const handlers={},frames=[];let zoom=2,cancels=0,taps=0;
 const viewport={scrollLeft:100,scrollTop:200,addEventListener:(k,v)=>handlers[k]=v};
 const changes=[];
 const bind=vm.runInNewContext(fs.readFileSync('voice_scenarios/timeline_gestures.js','utf8')+'\nbindTimelineGestures;',{
  requestAnimationFrame:fn=>{frames.push(fn);return frames.length;},cancelAnimationFrame:()=>{}
 });
 bind(viewport,{getZoom:()=>zoom,zoom:(z,x,from)=>{zoom=z;changes.push({z,x,from});},cancelSelection:()=>cancels++,tap:()=>taps++});
 const fire=(type,props={})=>{let prevented=false;handlers[type]({...props,preventDefault:()=>prevented=true});return prevented;};
 const flush=()=>{const pending=frames.splice(0);pending.forEach(f=>f());};
 return {fire,flush,viewport,changes,state:()=>({zoom,cancels,taps})};
}
test('trackpad pinch zooms around the cursor, while ordinary two-finger scrolling stays native',()=>{
 const t=setup();assert.equal(t.fire('wheel',{ctrlKey:false,deltaY:50}),false);assert.equal(t.changes.length,0);
 assert.equal(t.fire('wheel',{ctrlKey:true,deltaY:-100,deltaMode:0,clientX:400}),true);t.flush();
 assert.ok(t.state().zoom>2);assert.equal(t.changes[0].x,400);assert.equal(t.changes[0].from,400);
});
test('rapid wheel updates are batched and zoom is bounded',()=>{
 const t=setup();for(let i=0;i<30;i++)t.fire('wheel',{ctrlKey:true,deltaY:-100,clientX:300});
 assert.equal(t.changes.length,0);t.flush();assert.equal(t.changes.length,1);assert.equal(t.state().zoom,64);
});
const touch=(x,y=100)=>({clientX:x,clientY:y});
test('touch pinch does not measure or activate a reply and preserves moving midpoint',()=>{
 const t=setup();t.fire('touchstart',{touches:[touch(100),touch(200)]});
 t.fire('touchmove',{touches:[touch(75),touch(275)]});t.flush();
 assert.equal(t.state().zoom,4);assert.equal(t.changes[0].x,175);assert.equal(t.changes[0].from,150);
 t.fire('touchend',{touches:[touch(75)]});t.fire('touchend',{touches:[]});
 assert.equal(t.state().taps,0);assert.ok(t.state().cancels>0);
});
test('one-finger touch pans and tap remains available',()=>{
 const t=setup();t.fire('touchstart',{touches:[touch(100,100)]});t.fire('touchmove',{touches:[touch(80,70)]});t.fire('touchend',{touches:[]});
 assert.equal(t.viewport.scrollLeft,120);assert.equal(t.viewport.scrollTop,230);assert.equal(t.state().taps,0);
 t.fire('touchstart',{touches:[touch(90)]});t.fire('touchend',{touches:[]});assert.equal(t.state().taps,1);
});
test('Safari gesture zoom is supported without applying duplicate wheel zoom',()=>{
 const t=setup();t.fire('gesturestart',{scale:1,clientX:200});t.fire('gesturechange',{scale:1.5,clientX:200});
 t.fire('wheel',{ctrlKey:true,deltaY:-100,clientX:200});t.fire('gestureend',{});t.flush();assert.equal(t.state().zoom,3);
});
