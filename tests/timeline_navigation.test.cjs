const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const vm=require('node:vm');
function api(){return vm.runInNewContext(readFileSync('voice_scenarios/timeline_navigation.js','utf8')+'\n({timelineSnap,layoutTimelineFlags,waitEvidence});');}
test('snapping is pixel based, precise, optional and includes negative server times',()=>{
 const {timelineSnap}=api(); const points=[{time:-1,label:'VAS'},{time:1.234567,label:'首音频'},{time:4,label:'结束'}];
 assert.equal(timelineSnap(1.25,points,100).time,1.234567);
 assert.equal(timelineSnap(1.25,points,1000).point,null);
 assert.equal(timelineSnap(1.25,points,100,true).time,1.25);
 assert.equal(timelineSnap(-.99,points,100).time,-1);
 assert.equal(timelineSnap(2,points,100).point,null);
});
test('VAD flags keep separate anchors with collision-free labels, even at boundaries',()=>{
 const {layoutTimelineFlags}=api();
 const layout=layoutTimelineFlags([{x:0,width:100},{x:1,width:110},{x:20,width:110},{x:300,width:90}],300);
 assert.equal(layout.items.length,4);
 for(const flag of layout.items){assert.ok(flag.left>=0);assert.ok(flag.left+flag.width<=300);}
 for(let i=0;i<layout.items.length;i++)for(let j=i+1;j<layout.items.length;j++){
 const a=layout.items[i],b=layout.items[j];if(a.row===b.row)assert.ok(a.left+a.width+6<=b.left||b.left+b.width+6<=a.left);
 }
 assert.equal(layout.items[1].x,1);
});
test('wait evidence clips overlaps and never attributes another turn or a late stage',()=>{
 const {waitEvidence}=api();
 const found=waitEvidence({turn_index:2,start_seconds:10,end_seconds:14},[
 {label:'LLM',turn_index:2,segments:[{start:9e9,end:12e9}]},
 {label:'TTS',turn_index:2,segments:[{start:13e9,end:15e9}]},
 {label:'old',turn_index:1,segments:[{start:10e9,end:14e9}]},
 {label:'later',turn_index:2,segments:[{start:20e9,end:21e9}]},
 ]);
 assert.equal(found.length,2);assert.equal(found[0].label,'LLM');assert.equal(found[0].overlap,2);
 assert.equal(found[1].overlap,1);
});
