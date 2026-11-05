import {test} from 'node:test'
import assert from 'node:assert/strict'
import {TOAST_DURATION,collapseNotice,createToastController,retainActiveNotices} from '../../work/annotation-validation/annotation/notices.js'

// A manual clock, so "disappears on time" is asserted without sleeping.
function harness(){
  let now=0,seq=0;const pending=new Map(),seen=[]
  const controller=createToastController(toast=>seen.push(toast),{
    set:(run,ms)=>{const id=++seq;pending.set(id,{run,at:now+ms});return id},
    clear:id=>pending.delete(id),
  })
  const advance=ms=>{now+=ms;for(const [id,timer] of [...pending])if(timer.at<=now){pending.delete(id);timer.run()}}
  return {controller,advance,pending,visible:()=>seen.at(-1)}
}

test('each tone disappears on its own after its duration',()=>{
  for(const tone of ['info','success','warning','error']){
    const {controller,advance,visible,pending}=harness()
    controller.show('消息',tone);assert.equal(visible().tone,tone)
    advance(TOAST_DURATION[tone]-1);assert.ok(visible(),`${tone} still visible before its duration`)
    advance(1);assert.equal(visible(),null);assert.equal(pending.size,0)
  }
  assert.ok(TOAST_DURATION.error>TOAST_DURATION.success,'failures stay longer so they can be read')
})
test('repeating the visible message cannot keep a toast up indefinitely',()=>{
  const {controller,advance,visible}=harness()
  controller.show('已是第一帧')
  for(let elapsed=0;elapsed<TOAST_DURATION.info-500;elapsed+=500){advance(500);controller.show('已是第一帧')}
  advance(500);assert.equal(visible(),null,'the original deadline still applies')
  controller.show('已是第一帧');assert.equal(visible().message,'已是第一帧','the same message can show again once gone')
})
test('a new message replaces the old one with a fresh id and its own deadline',()=>{
  const {controller,advance,visible,pending}=harness()
  controller.show('一');const first=visible().id;advance(2000)
  controller.show('二','error');assert.notEqual(visible().id,first);assert.equal(pending.size,1)
  advance(TOAST_DURATION.info);assert.equal(visible().message,'二','the old timer must not remove the new message')
  advance(TOAST_DURATION.error-TOAST_DURATION.info);assert.equal(visible(),null)
})
test('manual dismissal cancels the pending timer',()=>{
  const {controller,visible,pending}=harness()
  controller.show('失败','error');controller.dismiss()
  assert.equal(visible(),null);assert.equal(pending.size,0)
})
test('collapsing never drops an unresolved failure, and a new failure reopens',()=>{
  let collapsed=collapseNotice([],'tracking|a');assert.deepEqual(collapsed,['tracking|a'])
  assert.deepEqual(collapseNotice(collapsed,'tracking|a'),collapsed,'collapsing twice is idempotent')
  assert.deepEqual(collapseNotice(collapsed,''),collapsed,'an absent notice cannot be collapsed')
  assert.deepEqual(retainActiveNotices(collapsed,['tracking|a']),['tracking|a'],'still failing stays collapsed')
  assert.ok(!retainActiveNotices(collapsed,['tracking|b']).includes('tracking|b'),'a different failure is not collapsed')
  collapsed=retainActiveNotices(collapsed,[]);assert.deepEqual(collapsed,[],'once resolved, a recurrence opens again')
})
