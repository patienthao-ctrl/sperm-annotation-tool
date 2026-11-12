import {test} from 'node:test'
import assert from 'node:assert/strict'
import {describeTrackingProgress,formatElapsed} from '../../work/annotation-validation/annotation/trackingProgress.js'
const base={startFrame:0,endFrame:99,endpoint:true,combine:true}
const states=view=>view.steps.map(s=>`${s.key}:${s.state}`).join(' ')

test('endpoint run shows three ordered stages and queued work as all pending',()=>{
  const view=describeTrackingProgress({...base,status:'queued',stage:'queued'})
  assert.equal(view.mode,'queued');assert.equal(states(view),'forward:pending backward:pending combine:pending')
  assert.equal(describeTrackingProgress({...base,endpoint:false,combine:false,status:'running',stage:'tracking',lastProcessedFrame:3}).steps.length,1)
})
test('model preparation is indeterminate rather than a guessed fraction',()=>{
  const view=describeTrackingProgress({...base,status:'running',stage:'preparing_model'})
  assert.equal(view.mode,'preparing');assert.equal(view.steps[0].state,'active');assert.equal(view.steps[0].fraction,null)
})
test('forward fraction and text come from the last processed frame',()=>{
  const view=describeTrackingProgress({...base,status:'running',stage:'tracking',lastProcessedFrame:49,elapsedSeconds:75.4})
  assert.equal(view.steps[0].fraction,.5);assert.equal(view.detail,'第 50 / 100 帧');assert.equal(view.compact,'正向 50/100')
  assert.deepEqual(view.covered,[0,49]);assert.equal(view.frame,49);assert.equal(view.direction,1);assert.equal(view.elapsed,'1:15')
})
test('backward walks from the tail and marks forward done',()=>{
  const view=describeTrackingProgress({...base,status:'running',stage:'checking',phase:'backward',phaseFrom:99,phaseTo:0,phaseCurrent:72,lastProcessedFrame:99})
  assert.equal(states(view),'forward:done backward:active combine:pending')
  assert.equal(view.steps[1].fraction,27/99);assert.equal(view.direction,-1);assert.deepEqual(view.covered,[72,99]);assert.equal(view.frame,72)
})
test('combine counts every frame and never exceeds the range',()=>{
  const view=describeTrackingProgress({...base,status:'running',stage:'checking',phase:'combine',phaseFrom:0,phaseTo:99,phaseCurrent:120})
  assert.equal(states(view),'forward:done backward:done combine:active');assert.equal(view.steps[2].fraction,1);assert.equal(view.detail,'第 100 / 100 帧')
})
test('a stale phase field from an older stage does not move the bar backwards',()=>{
  const view=describeTrackingProgress({...base,status:'running',stage:'tracking',phase:'backward',phaseFrom:99,phaseTo:0,phaseCurrent:10,lastProcessedFrame:20})
  assert.equal(view.steps[0].state,'active');assert.equal(view.detail,'第 21 / 100 帧')
})
test('success and result loading show every stage complete',()=>{
  for(const input of [{status:'success'},{status:'running',stage:'checking',loadingResult:true}]){
    const view=describeTrackingProgress({...base,...input});assert.equal(view.mode,'finishing');assert.ok(view.steps.every(s=>s.state==='done'&&s.fraction===1))
  }
})
test('single-frame and malformed numbers stay within 0..1',()=>{
  const view=describeTrackingProgress({...base,startFrame:5,endFrame:5,status:'running',stage:'tracking',lastProcessedFrame:Number.NaN})
  assert.equal(view.steps[0].fraction,1);assert.equal(formatElapsed(undefined),'');assert.equal(formatElapsed(3725),'1:02:05')
})
test('prelabel shows detection by processed frames, then linking',()=>{
  const pre={startFrame:0,endFrame:34,endpoint:false,combine:false,prelabel:true}
  assert.equal(states(describeTrackingProgress({...pre,status:'queued',stage:'queued'})),'detect:pending link:pending')
  const preparing=describeTrackingProgress({...pre,status:'running',stage:'detecting'})
  assert.equal(preparing.mode,'preparing');assert.equal(preparing.steps[0].fraction,null)
  const running=describeTrackingProgress({...pre,status:'running',stage:'detecting',lastProcessedFrame:6})
  assert.equal(running.detail,'第 7 / 35 帧');assert.equal(running.steps[0].fraction,7/35);assert.deepEqual(running.covered,[0,6])
  assert.equal(states(describeTrackingProgress({...pre,status:'running',stage:'linking',lastProcessedFrame:34})),'detect:done link:active')
  assert.equal(describeTrackingProgress({...pre,status:'success'}).mode,'finishing')
})
