const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const vm = require('node:vm');
const helpers = vm.runInNewContext(readFileSync('voice_scenarios/image_markers.js','utf8')+'\n({details:imageMarkerDetails,group:groupImageMarkers});',{URL});

test('image details escape message text and only offer credential-free HTTP links',()=>{
  const rows=['javascript:alert(1)','data:image/svg+xml,<svg onload=alert(1)>','https://user:pass@example.com/image.png',null,'https://example.com/image.png?x=%22'];
  const html=helpers.details(rows.map(url=>({url})),()=>'<script>bad</script>');
  assert.equal((html.match(/<a /g)||[]).length,1);
  assert.match(html,/target="_blank" rel="noopener noreferrer" referrerpolicy="no-referrer"/);
  assert.doesNotMatch(html,/<script>|<img|href="(?:javascript|data):/);
  assert.match(html,/&lt;script&gt;/);
});

test('nearby images are grouped without deduplication and separate when zoomed',()=>{
  const images=[{at_seconds:2,url:'a'},{at_seconds:2.1,url:'a'},{at_seconds:8,url:'b'}];
  assert.deepEqual(Array.from(helpers.group(images,10,1000),g=>g.length),[2,1]);
  assert.deepEqual(Array.from(helpers.group(images,10,64000),g=>g.length),[1,1,1]);
});
