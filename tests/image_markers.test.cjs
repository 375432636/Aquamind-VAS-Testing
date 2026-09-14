const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const vm = require('node:vm');
const helpers = vm.runInNewContext(readFileSync('voice_scenarios/media_markers.js','utf8')+'\n({details:mediaMarkerDetails,group:groupMediaMarkers,icon:mediaMarkerIcon});',{URL});

test('image details escape message text and only offer credential-free HTTP links',()=>{
  const rows=['javascript:alert(1)','data:image/svg+xml,<svg onload=alert(1)>','https://user:pass@example.com/image.png',null,'https://example.com/image.png?x=%22'];
  const html=helpers.details(rows.map(url=>({url})),()=>'<script>bad</script>');
  assert.equal((html.match(/<a /g)||[]).length,1);
  assert.match(html,/target="_blank" rel="noopener noreferrer" referrerpolicy="no-referrer"/);
  assert.doesNotMatch(html,/<script>|<img|href="(?:javascript|data):/);
  assert.match(html,/&lt;script&gt;/);
});

test('mixed display items keep video and image icons and explicit links without auto playback',()=>{
  const markers = [
    {kind:'video',at_seconds:2,url:'https://example.com/demo.mp4'},
    {kind:'image',at_seconds:2,url:'https://example.com/poster.png'},
    {kind:'video',at_seconds:2,url:'https://example.com/demo.mp4'},
    {kind:'video',at_seconds:8,url:'javascript:alert(1)'},
  ];
  const groups=helpers.group(markers,10,1000);
  assert.deepEqual(Array.from(groups,g=>g.length),[3,1]);
  assert.equal((helpers.icon(groups[0]).match(/<svg /g)||[]).length,2);
  const html=helpers.details(markers,marker=>marker.kind);
  assert.equal((html.match(/查看视频/g)||[]).length,2);
  assert.equal((html.match(/查看图片/g)||[]).length,1);
  assert.match(html,/加载与播放时间未采集/);
  assert.match(html,/视频地址缺失或不支持打开/);
  assert.doesNotMatch(html,/<video|<iframe|<img|href="javascript:|autoplay/);
});

test('nearby images are grouped without deduplication and separate when zoomed',()=>{
  const images=[{at_seconds:2,url:'a'},{at_seconds:2.1,url:'a'},{at_seconds:8,url:'b'}];
  assert.deepEqual(Array.from(helpers.group(images,10,1000),g=>g.length),[2,1]);
  assert.deepEqual(Array.from(helpers.group(images,10,64000),g=>g.length),[1,1,1]);
});
