import assert from 'node:assert/strict';
import {normalizedRect,dragRect,boxStyle} from '../static/pdf-geometry.mjs';

for(const scale of [.5,1,2]){
  const page={left:140,top:90,width:600*scale,height:800*scale};
  const rect={left:page.left+60*scale,top:page.top+160*scale,right:page.left+300*scale,bottom:page.top+400*scale};
  assert.deepEqual(normalizedRect(rect,page),[.1,.2,.5,.5]);
  assert.deepEqual(dragRect({x:rect.right,y:rect.bottom},{x:rect.left,y:rect.top},page),[.1,.2,.5,.5]);
}
assert.deepEqual(normalizedRect({left:-30,top:-20,right:200,bottom:180},{left:0,top:0,width:100,height:100}),[0,0,1,1]);
assert.equal(normalizedRect({left:110,top:0,right:120,bottom:10},{left:0,top:0,width:100,height:100}),null);
assert.deepEqual(boxStyle([.1,.2,.5,.5]),{left:'10%',top:'20%',width:'40%',height:'30%'});
console.log('PDF selection geometry: zoom, reverse drag, page clipping and highlight placement passed.');
