import assert from 'node:assert/strict';
import {layoutPages,nearbyPages,readingPage,capturePosition,restorePosition,rasterTiles,TILE_PIXELS,PAGE_GAP} from '../static/pdf-layout.mjs';

for(const dpr of [1,3,4,6,NaN]){
 const width=600.37,height=800.19,tiles=rasterTiles(width,height,dpr),scale=dpr===6?6:4;
 assert.ok(tiles.every(t=>t.scale===scale&&t.width<=TILE_PIXELS&&t.height<=TILE_PIXELS));
 assert.ok(tiles.every(t=>t.x%TILE_PIXELS===0&&t.y%TILE_PIXELS===0));
 assert.equal(new Set(tiles.map(t=>t.key)).size,tiles.length);
 assert.equal(tiles.reduce((sum,t)=>sum+t.width*t.height,0),Math.ceil(width*scale)*Math.ceil(height*scale),'Tiles cover all source pixels exactly once.');
 assert.equal(Math.max(...tiles.map(t=>t.x+t.width)),Math.ceil(width*scale));
 assert.equal(Math.max(...tiles.map(t=>t.y+t.height)),Math.ceil(height*scale));
}
const view={left:8000,top:14000,right:9200,bottom:14800};
const large=rasterTiles(12000,18000,4,view);
assert.ok(large.length<=12,'A huge page only allocates the visible region.');
assert.ok(large.every(t=>t.scale===4),'Page size must never reduce sampling quality.');
assert.ok(large.every(t=>t.x+t.width>view.left*4&&t.x<view.right*4&&t.y+t.height>view.top*4&&t.y<view.bottom*4));
const elsewhere=rasterTiles(12000,18000,4,{left:0,top:0,right:1200,bottom:800});
assert.ok(elsewhere.every(t=>!large.some(old=>old.key===t.key)));
assert.deepEqual(rasterTiles(600,800,1,{left:-900,top:0,right:-1,bottom:800}),[]);
assert.deepEqual(rasterTiles(600,800,1,{left:0,top:900,right:600,bottom:1000}),[]);

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
for(const zoom of ['fit','1','1.25','1.5','2','3','4']){
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
console.log('PDF layout: 4x/6x tiles, exact coverage, bounded visible regions without downsampling, 500-page window, mixed sizes and zoom/resize anchors passed.');
