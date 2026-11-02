import assert from 'node:assert/strict'
import test from 'node:test'
import { editableRange, neighbourSuspects, plausibleNext, tailFrames, withSize } from '../../work/annotation-validation/annotation/linkedEdits.js'

const box = (x, w = 2, h = 2, y = 10) => ({ x, y, width: w, height: h })
const track = (boxes, manual = []) => boxes.map((b, i) => b && { frameIndex: i, bbox: b, manual: manual.includes(i) }).filter(Boolean)

test('after shrinking one frame, the still-inflated neighbours are reported, not the steady ones', () => {
  const t = track([box(10), box(10, 4, 4), box(10, 4, 4), box(10, 2, 2), box(10, 4, 4), box(10, 2, 2), box(10)])
  assert.deepEqual(neighbourSuspects(t, 3, 5, 1000, 1000), [1, 2, 4])
  // A human box may carry padding on purpose and is never reported.
  assert.deepEqual(neighbourSuspects(track(t.map(x => x.bbox), [2]), 3, 5, 1000, 1000), [1, 4])
  assert.deepEqual(neighbourSuspects(t, 3, 1, 1000, 1000), [2, 4])
})

test('a box drifting off the head by more than half a head width is reported', () => {
  const t = track([box(10), box(10.6), box(11.2)])
  assert.deepEqual(neighbourSuspects(t, 0, 5, 1000, 1000), [2])
})

test('editable range stops at a gap or a manual box and respects the span', () => {
  const t = track([box(1), box(1), null, box(1), box(1), box(1), box(1), box(1)], [6])
  assert.deepEqual(editableRange(t, 4, 5), { before: [3], after: [5] })
  assert.deepEqual(editableRange(t, 1, 5), { before: [0], after: [] })
  assert.deepEqual(editableRange(track(Array(20).fill(box(1))), 10, 3), { before: [7, 8, 9], after: [11, 12, 13] })
})

test('tail frames run from the current frame to the end of the continuous track', () => {
  const t = track([box(1), box(1), box(1), box(1), null, box(1)])
  assert.deepEqual(tailFrames(t, 1), [1, 2, 3])
  assert.deepEqual(tailFrames(t, 4), [])
})

test('withSize keeps the centre and stays inside the frame', () => {
  assert.deepEqual(withSize(box(10, 4, 4), 2, 2), { x: 11, y: 11, width: 2, height: 2 })
  assert.deepEqual(withSize({ x: 99, y: 0, width: 1, height: 1 }, 4, 4), { x: 96, y: 0, width: 4, height: 4 })
})

test('fill accepts the same head nearby and rejects a jump or a different size', () => {
  assert.ok(plausibleNext(box(10), box(11), 1000, 1000))
  assert.ok(!plausibleNext(box(10), box(20), 1000, 1000))
  assert.ok(!plausibleNext(box(10), box(10, 6, 6), 1000, 1000))
})
