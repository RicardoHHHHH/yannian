// Optional real raster regression: install @napi-rs/canvas in a test environment,
// then run: node tests/test_pdf_raster.mjs path/to/a/public-paper.pdf
// No network, browser, local library database or model calls are used.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {readFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {rasterTiles} from '../static/pdf-layout.mjs';

const require=createRequire(import.meta.url),native=require('@napi-rs/canvas');
Object.assign(globalThis,{DOMMatrix:native.DOMMatrix,Path2D:native.Path2D,ImageData:native.ImageData});
const {getDocument}=await import('../static/vendor/pdfjs/legacy/build/pdf.mjs');
assert.ok(process.argv[2],'Provide a local public/test PDF path.');
const assets=fileURLToPath(new URL('../static/vendor/pdfjs/',import.meta.url));
const task=getDocument({data:new Uint8Array(await readFile(process.argv[2])),
  standardFontDataUrl:assets+'standard_fonts/',cMapUrl:assets+'cmaps/',cMapPacked:true,
  wasmUrl:assets+'wasm/',isEvalSupported:false});
const document=await task.promise,results=[];
try{
  for(const number of [...new Set([1,Math.min(4,document.numPages)])]){
    const page=await document.getPage(number),base=page.getViewport({scale:1});
    const viewport=page.getViewport({scale:600/base.width}),scale=4;
    const width=Math.ceil(viewport.width*scale),height=Math.ceil(viewport.height*scale);
    const full=native.createCanvas(width,height),stitched=native.createCanvas(width,height);
    await page.render({canvas:full,canvasContext:full.getContext('2d'),viewport,transform:[scale,0,0,scale,0,0]}).promise;
    const start=performance.now(),tiles=rasterTiles(viewport.width,viewport.height,1);
    for(const tile of tiles){
      const canvas=native.createCanvas(tile.width,tile.height);
      await page.render({canvas,canvasContext:canvas.getContext('2d'),viewport,
        transform:[scale,0,0,scale,-tile.x,-tile.y]}).promise;
      stitched.getContext('2d').drawImage(canvas,tile.x,tile.y);canvas.width=canvas.height=0;
    }
    const expected=full.getContext('2d').getImageData(0,0,width,height).data;
    const actual=stitched.getContext('2d').getImageData(0,0,width,height).data;
    let changed=0,totalError=0,ink=0;
    for(let i=0;i<expected.length;i+=4){
      const error=Math.max(...[0,1,2,3].map(c=>Math.abs(expected[i+c]-actual[i+c])));
      if(error>8)changed++;
      totalError+=error;
      if(expected[i]<240||expected[i+1]<240||expected[i+2]<240)ink++;
    }
    const pixels=width*height;
    assert.ok(ink>pixels*.001,'The reference must contain real page content.');
    assert.ok(changed/pixels<.0001&&totalError/pixels<.03,'Tiles must match a full-resolution page, including boundaries.');
    results.push({page:number,width,height,tiles:tiles.length,changedPixels:changed,meanError:totalError/pixels,renderMs:Math.round(performance.now()-start)});
    full.width=full.height=stitched.width=stitched.height=0;page.cleanup();
  }
  console.log(JSON.stringify({pdfPages:document.numPages,sampling:4,results}));
}finally{await task.destroy();}
