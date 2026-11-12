import {test} from 'node:test'
import assert from 'node:assert/strict'
import {planBlocks,openHints} from '../../work/annotation-validation/annotation/scanBlocks.js'

const covers=(blocks,x,y)=>blocks.some(b=>x>=b.x0&&x<=b.x1&&y>=b.y0&&y<=b.y1)
test('a 4K frame in a 1230x820 view is cut into 3x3 overlapping blocks that cover everything',()=>{
  const blocks=planBlocks(4096,3000,1230,820)
  assert.equal(blocks.length,9)
  for(let x=0;x<=1;x+=.05)for(let y=0;y<=1;y+=.05)assert.ok(covers(blocks,x,y),`${x},${y}`)
  assert.ok(blocks[0].x1>blocks[1].x0)  // Neighbours overlap so a head on the seam is seen whole.
})
test('blocks go in a snake so each step moves to a neighbour',()=>{
  const blocks=planBlocks(4096,3000,1230,820)
  assert.deepEqual(blocks.map(b=>[b.row,b.col]),[[0,0],[0,1],[0,2],[1,2],[1,1],[1,0],[2,0],[2,1],[2,2]])
  for(let i=1;i<blocks.length;i++)assert.equal(Math.abs(blocks[i].row-blocks[i-1].row)+Math.abs(blocks[i].col-blocks[i-1].col),1)
})
test('a small frame that already fits is a single block',()=>{
  assert.deepEqual(planBlocks(800,600,1800,1400),[{x0:0,y0:0,x1:1,y1:1,row:0,col:0}])
  assert.deepEqual(planBlocks(0,0,100,100),[])
})
test('hints already covered by a box are not shown',()=>{
  assert.deepEqual(openHints([[0,0,10,10],[100,100,110,110]],[[2,2,12,12]]),[[100,100,110,110]])
})
