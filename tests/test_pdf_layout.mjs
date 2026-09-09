import assert from 'node:assert/strict';
import {layoutPages,nearbyPages,readingPage,capturePosition,restorePosition,canvasSize,PAGE_GAP} from '../static/pdf-layout.mjs';

assert.deepEqual(canvasSize(600,800,1),{width:1200,height:1600,scaleX:2,scaleY:2});
assert.deepEqual(canvasSize(600,800,3),{width:1800,height:2400,scaleX:3,scaleY:3});
assert.deepEqual(canvasSize(600,800,NaN),canvasSize(600,800,1));
for(const [w,h,dpr] of [[1600,2200,3],[12000,18000,4],[100,30000,3],[30000,100,3]]){
 const pixels=canvasSize(w,h,dpr);
 assert.ok(pixels.width*pixels.height<=20_000_000);
 assert.ok(pixels.width<=8192&&pixels.height<=8192);
 assert.equal(pixels.scaleX,pixels.width/w);assert.equal(pixels.scaleY,pixels.height/h);
}

const sizes=Array.from({length:500},()=>[600,800]);
const pages=layoutPages(sizes,'fit',600);
assert.equal(pages.length,500);
assert.equal(pages[1].top,pages[0].bottom+PAGE_GAP);
assert.equal(readingPage(pages,pages[1].top,600),2);
assert.equal(readingPage(pages,pages[498].top,600),499);
assert.equal(readingPage(pages,pages[499].bottom,600),500);
for(const page of pages){
  const visible=nearbyPages(pages,page.top,600);
  assert.ok(visible.includes(page.page));
  assert.ok(visible.length<=4,'Long PDFs must render a bounded window, not all 500 pages.');
}
const position=capturePosition(pages,pages[79].top+pages[79].height*.65);
assert.equal(position.page,80);assert.ok(Math.abs(position.fraction-.65)<1e-10);
for(const zoom of ['fit','1','1.25','1.5','2']){
  const resized=layoutPages(sizes,zoom,420);
  const top=restorePosition(resized,position);
  const restored=capturePosition(resized,top);
  assert.equal(restored.page,80);assert.ok(Math.abs(restored.fraction-.65)<1e-10);
}
const mixed=layoutPages([[600,800],[800,600],[400,1200]],'fit',600);
assert.deepEqual(mixed.map(p=>p.height),[800,450,1800]);
assert.equal(mixed[2].top,mixed[1].bottom+PAGE_GAP);
assert.ok(nearbyPages(mixed,mixed[1].bottom-20,600).includes(3));
assert.equal(readingPage(layoutPages([[600,800]],'fit',600),0,600),1);
console.log('PDF layout: continuous page boundaries, 500-page lazy window, mixed sizes, current page and zoom/resize anchors passed.');
