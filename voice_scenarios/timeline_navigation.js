// Coordinates stay on the displayed axis; snapping never aligns client/server clocks.
function timelineSnap(time, points, pixelsPerSecond, bypass = false) {
  if (bypass || !(pixelsPerSecond > 0)) return {time,point:null};
  let best = null, distance = 10 / pixelsPerSecond;
  for (const point of points) {
    const delta = Math.abs(point.time-time);
    if (Number.isFinite(point.time) && delta <= distance) {best=point;distance=delta;}
  }
  return {time:best ? best.time : time, point:best};
}
function layoutTimelineFlags(flags, width) {
  const ends = [];
  const items = flags.map(flag=> {
    const size = Math.min(width,flag.width);
    const left = Math.max(0,Math.min(width-size,flag.x-size/2));
    let row=ends.findIndex(end=>end+6<=left);
    if(row<0)row=ends.length;
    ends[row]=left+size;
    return {...flag,width:size,left,row};
  });
  return {items,rows:ends.length};
}
function waitEvidence(wait, lanes) {
  return lanes.map((lane,index)=> {
    if(lane.turn_index!==wait.turn_index)return null;
    const intervals=(lane.segments||[]).map(span=>[Math.max(wait.start_seconds,span.start/1e9),Math.min(wait.end_seconds,span.end/1e9)]).filter(([a,b])=>b>a).sort((a,b)=>a[0]-b[0]);
    let overlap=0,end=-Infinity;
    for(const [a,b] of intervals){overlap+=Math.max(0,b-Math.max(a,end));end=Math.max(end,b);}
    return overlap>0?{index,label:lane.label,overlap}:null;
  }).filter(Boolean).sort((a,b)=>b.overlap-a.overlap);
}
function conciseMilestone(marker) {
  const name=marker.event || marker.name;
  const names={local_vad_speech_started:'本地 VAD · 开始',local_vad_last_voice:'本地 VAD · 末帧',local_vad_endpoint_detected:'本地 VAD · 结束',asr_speech_started:'ASR VAD · 开始',asr_endpoint_detected:'ASR VAD · 结束',llm_first_token:'首 Token',tts_first_pcm:'首音频',tts_segment_ready:'可合成',asr_partial:'识别首字',asr_final:'识别完成'};
  if(names[name])return names[name];
  return marker.label.replace(/首个有效增量（First Token）/,'首 Token').replace(/(?:LLM|TTS|ASR|Memory) 请求/,'').replace('工具调用','工具').replace('语音开始','开始').replace('最后语音帧','末帧');
}
