// Source-level lifecycle tests using a small DOM/PDF stub. No browser or network.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import * as geometry from '../static/pdf-geometry.mjs';
import * as layout from '../static/pdf-layout.mjs';

class Element {
  constructor(tag='div') {this.tag=tag;this.children=[];this.style={setProperty(){}};this.dataset={};this.listeners={};this.scrollTop=0;this.scrollLeft=0;this.clientHeight=600;this.isConnected=true;}
  append(...children){for(const child of children){child.parentElement=this;this.children.push(child);}}
  replaceChildren(...children){for(const child of this.children)child.parentElement=null;this.children=[];this.append(...children);}
  setAttribute(){}
  addEventListener(type,fn,options){this.listeners[type]=fn;options?.signal?.addEventListener('abort',()=>delete this.listeners[type]);}
  get clientWidth(){return parseFloat(this.style.width)||632;}
  getContext(){return {};}
  contains(node){return node===this||this.children.some(child=>child.contains(node));}
  getBoundingClientRect(){return {left:0,top:0,width:parseFloat(this.style.width)||600,height:parseFloat(this.style.height)||800};}
  setPointerCapture(){}
  hasPointerCapture(){return false;}
}
const host=new Element(),scroll=new Element(),status=new Element();
host.querySelector=selector=>selector==='.pdf-scroll'?scroll:status;
let nativeSelection={isCollapsed:true,removeAllRanges(){nativeSelection={isCollapsed:true};}};
const frames=new Map();let frameId=0,documentCount=0,cancelled=0;
const changes=[],selections=[],errors=[];
const delayed=[];
let delayRenders=false,failPage=null;
const context=vm.createContext({
  ...geometry,...layout,URL,console,AbortController,setTimeout,clearTimeout,
  document:{createElement:tag=>new Element(tag)},
  window:{devicePixelRatio:1,getSelection:()=>nativeSelection,dispatchEvent(){}},Event:class{},
  requestAnimationFrame(fn){frames.set(++frameId,fn);return frameId;},cancelAnimationFrame(id){frames.delete(id);},
  ResizeObserver:class{observe(){}disconnect(){}},GlobalWorkerOptions:{},
  getDocument(){documentCount++;return {destroy:async()=>{},promise:Promise.resolve({numPages:25,getPage:async number=>({
    getViewport:({scale})=>({width:600*scale,height:800*scale,scale}),
    render(){
      let resolve,reject;
      const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});
      if(number===failPage)reject(new Error('Test page failure'));
      else if(delayRenders)delayed.push(resolve);else resolve();
      return {promise,cancel(){cancelled++;const error=new Error('cancelled');error.name='RenderingCancelledException';reject(error);}};
    },getTextContent:async()=>({items:[{str:'Page '+number}]}),cleanup(){}
  })})};},
  TextLayer:class{constructor({container}){this.container=container;}async render(){this.container.append(new Element('span'));}cancel(){}}
});
const source=fs.readFileSync(new URL('../static/pdf-reader.mjs',import.meta.url),'utf8')
  .replace(/^import .*;\r?$/gm,'').replaceAll('import.meta.url',JSON.stringify('file:///fixture/pdf-reader.mjs'));
vm.runInContext(source,context);
const reader=context.window.YannianPDF;
const options={host,paperId:'fixture',pageNumber:1,pageSizes:Array.from({length:25},()=>[600,800]),
  onPageChange:p=>changes.push(p),onSelection:s=>selections.push(s),onError:e=>errors.push(e)};
async function flush(){for(let i=0;i<12;i++){const callbacks=[...frames.values()];frames.clear();callbacks.forEach(fn=>fn());await new Promise(resolve=>setImmediate(resolve));}}
const slots=()=>scroll.children[0].children;
const frame=n=>slots()[n-1].children[0];
const canvasCount=()=>slots().filter(slot=>slot.children[0].children.some(child=>child.tag==='canvas')).length;
const find=(n,cls)=>frame(n).children.find(child=>child.className===cls);

await reader.mount(options);await flush();
assert.equal(slots().length,25,'All page slots must exist to support continuous scrolling.');
assert.ok(canvasCount()>0&&canvasCount()<5,'Only nearby pages need canvases.');
const firstCanvas=find(1,'pdf-canvas'),originalList=scroll.children[0];
const pages=layout.layoutPages(options.pageSizes,'fit',600);
scroll.scrollTop=pages[1].top+200;scroll.listeners.scroll();await flush();
assert.equal(changes.at(-1),2);assert.ok(find(2,'pdf-canvas'));

scroll.scrollTop=pages[11].top+300;scroll.listeners.scroll();await flush();
assert.equal(changes.at(-1),12);assert.equal(firstCanvas.width,0,'Evicted canvases must release backing pixels.');
assert.ok(canvasCount()<5);assert.equal(scroll.children[0],originalList);
const position=scroll.scrollTop,canvas=find(12,'pdf-canvas');
await reader.mount({...options,pageNumber:12,tool:'region'});await flush();
assert.equal(scroll.scrollTop,position);assert.equal(find(12,'pdf-canvas'),canvas);
assert.equal(find(12,'pdf-region-tool').hidden,false);
const region=find(12,'pdf-region-tool');
region.onpointerdown({button:0,clientX:60,clientY:160,pointerId:1,preventDefault(){}});
region.onpointerup({clientX:300,clientY:400,pointerId:1});
assert.equal(selections.at(-1).page,12);assert.deepEqual(Array.from(selections.at(-1).rects[0]),[.1,.2,.5,.5]);

const selected={page:12,kind:'region',rects:[[.1,.2,.5,.5]]};
await reader.mount({...options,pageNumber:12,tool:'text',selection:selected,zoom:'1.5'});await flush();
const zoomed=layout.layoutPages(options.pageSizes,'1.5',600);
const restored=layout.capturePosition(zoomed,scroll.scrollTop);
assert.equal(restored.page,12);assert.ok(Math.abs(restored.fraction-.375)<1e-9);
assert.equal(find(12,'pdf-marks').children.length,1);assert.equal(find(13,'pdf-marks')?.children.length||0,0);
const text=find(12,'textLayer'),span=text.children[0];
nativeSelection={isCollapsed:false,rangeCount:1,anchorNode:span,focusNode:span,toString:()=> 'A selected passage',getRangeAt:()=>({getClientRects:()=>[{left:120,top:320,right:600,bottom:800}]})};
frame(12).onmouseup({clientX:600,clientY:800});
assert.equal(selections.at(-1).page,12);assert.equal(selections.at(-1).text,'A selected passage');
assert.deepEqual(Array.from(selections.at(-1).rects[0]),[.1,.2,.5,.5]);
nativeSelection={isCollapsed:true};
reader.goToPage(25);await flush();assert.ok(find(25,'pdf-canvas'));assert.equal(changes.at(-1),25);
reader.goToPage(12,selected.rects);await flush();assert.equal(find(12,'pdf-marks').children.length,1);
assert.equal(documentCount,1,'Tool changes, zoom and page jumps must reuse the PDF document.');

delayRenders=true;reader.goToPage(20);await flush();
reader.goToPage(4);delayRenders=false;delayed.splice(0).forEach(resolve=>resolve());await flush();
assert.ok(cancelled>0);assert.ok(find(4,'pdf-canvas'));assert.equal(find(20,'pdf-canvas'),undefined);
failPage=8;reader.goToPage(8);await flush();
assert.equal(errors.length,1);assert.equal(frame(8).children[0].className,'pdf-page-error');
failPage=null;frame(8).children[0].children[0].onclick();await flush();assert.ok(find(8,'pdf-canvas'));
reader.clear();assert.equal(scroll.listeners.scroll,undefined);assert.equal(canvasCount(),0);
console.log('PDF reader lifecycle: 25 continuous pages, lazy eviction, page tracking, tool/zoom position, page-bound selections, jump/return, cancellation, retry and cleanup passed (DOM/PDF stubs, no browser).');
