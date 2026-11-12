import {test} from 'node:test'
import assert from 'node:assert/strict'
import {buildQueue,nextOpen} from '../../work/annotation-validation/annotation/targetQueue.js'

test('possible swaps first and asked as same-or-not, then manual, partial, weak, then the rest',()=>{
  const flags={5:[{kind:'weak'}],7:[{kind:'cross',frame:20,with:8}],8:[{kind:'cross',frame:20,with:7}],3:[{kind:'partial',frame:12,last:23}]}
  const queue=buildQueue([1,2,3,4,5,7,8,9],flags,new Set([9]))
  assert.deepEqual(queue.map(e=>e.objectId),[7,8,9,3,5,1,2,4,5])
  assert.deepEqual(queue.slice(0,2).map(e=>[e.ask,e.suspectFrame]),[['same',20],['same',20]])
  assert.ok(queue.slice(2).every(e=>e.ask==='what'))
  assert.deepEqual(queue.filter(e=>e.repeat).map(e=>e.objectId),[5])  // One hidden second look at a weak target.
})
test('short rounds get no repeat card',()=>{
  assert.equal(buildQueue([1,2,3],{2:[{kind:'weak'}]},new Set()).some(e=>e.repeat),false)
})
test('next open card wraps and stops when all are answered',()=>{
  const queue=[1,2,3].map(objectId=>({objectId,ask:'what',repeat:false}))
  const done=new Set([1,2])
  assert.equal(nextOpen(queue,e=>done.has(e.objectId),0),2)
  assert.equal(nextOpen(queue,e=>done.has(e.objectId),2),2)
  done.add(3)
  assert.equal(nextOpen(queue,e=>done.has(e.objectId),0),-1)
})
test('a possible swap is no longer asked once the target has no boxes on both sides of it',()=>{
  const flags={15:[{kind:'cross',frame:6,with:23}]}
  // Split at frame 4: the target keeps frames 0-3, nothing left around frame 6.
  const split=buildQueue([15],flags,new Set(),{15:[0,1,2,3]})
  assert.deepEqual(split.map(e=>[e.ask,e.suspectFrame]),[['what',undefined]])
  // Boxes still on both sides: the question stays.
  const whole=buildQueue([15,16],flags,new Set(),{15:[0,3,5,6,7,9],16:[0,1]})
  assert.deepEqual(whole.map(e=>[e.objectId,e.ask,e.suspectFrame]),[[15,'same',6],[16,'what',undefined]])
  // Only after it (the part before was split off): nothing to compare.
  assert.equal(buildQueue([15],flags,new Set(),{15:[6,7,8]})[0].ask,'what')
})
