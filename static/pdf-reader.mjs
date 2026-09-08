import {getDocument, GlobalWorkerOptions, TextLayer} from './vendor/pdfjs/legacy/build/pdf.mjs';
import {normalizedRect, dragRect, boxStyle} from './pdf-geometry.mjs';
import {layoutPages, nearbyPages, readingPage, capturePosition, restorePosition, PAGE_PADDING} from './pdf-layout.mjs';

GlobalWorkerOptions.workerSrc = new URL('./vendor/pdfjs/legacy/build/pdf.worker.mjs', import.meta.url).href;
const assets = new URL('./vendor/pdfjs/', import.meta.url).href;
let cachedId, loadingTask, active = null;

function highlight(container, rects, kind='text') {
  container.replaceChildren();
  for (const r of rects || []) {
    const box=document.createElement('div');box.className='pdf-mark '+kind;
    Object.assign(box.style,boxStyle(r));container.append(box);
  }
}

function selectedOn(session, row) {
  return session.selection?.page===row.number ? session.selection : null;
}

function disposeRow(row) {
  row.cancelDrag?.();row.cancelDrag=null;
  ++row.token;row.frame.onmouseup=null;
  row.renderTask?.cancel();row.textLayer?.cancel();
  if(row.canvas){row.canvas.width=0;row.canvas.height=0;}
  if(row.state==='ready')row.pdfPage?.cleanup();
  row.renderTask=null;row.textLayer=null;row.canvas=null;row.marks=null;row.region=null;row.pdfPage=null;
  row.state='idle';row.dragging=false;row.frame.replaceChildren();
}

function stopRender() {
  if(!active)return;
  active.alive=false;active.events.abort();active.observer?.disconnect();
  clearTimeout(active.resizeTimer);cancelAnimationFrame(active.tick);
  for(const row of active.rows)disposeRow(row);
  active=null;
}

function valid(session) { return Boolean(session && session===active && session.alive && session.host.isConnected); }

function statusText(session) {
  const row=session.rows[session.pageNumber-1];
  session.status.textContent=row?.state==='error' ? '此页加载失败，可点击页内“重新加载”重试。' :
    row?.state==='ready' && !row.hasText ? '连续滚动阅读 · 此页无可选文字，请使用“框选图片/区域”。' :
    session.tool==='region' ? '连续滚动阅读 · 拖出矩形选择图片、公式或区域；按 Esc 取消框选，滚轮可继续翻页。' :
    '连续滚动阅读 · 滚轮上下翻页；拖选原文文字后可提问。';
}

function applyTools(session) {
  for(const row of session.rows){
    if(session.tool!=='region')row.cancelDrag?.();
    if(row.region)row.region.hidden=session.tool!=='region';
    if(row.marks){const selected=selectedOn(session,row);highlight(row.marks,selected?.rects,selected?.kind);}
  }
  statusText(session);
}

function bindSelection(session,row,text,marks,region) {
  const frame=row.frame;
  // The frame survives canvas eviction; replace its handlers with the latest text layer.
  frame.onmouseup=event=>{
    if(session.tool!=='text')return;
    const selection=window.getSelection();
    if(!selection?.rangeCount||selection.isCollapsed||!text.contains(selection.anchorNode)||!text.contains(selection.focusNode))return;
    const selectedText=selection.toString().trim().slice(0,15000);
    if(!selectedText)return;
    const rects=Array.from(selection.getRangeAt(0).getClientRects()).map(r=>normalizedRect(r,frame.getBoundingClientRect())).filter(Boolean).slice(0,256);
    if(rects.length)session.onSelection?.({kind:'text',text:selectedText,rects,page:row.number},{x:event.clientX,y:event.clientY});
  };
  let start=null,pointerId=null;
  const restore=()=>{const selected=selectedOn(session,row);highlight(marks,selected?.rects,selected?.kind);};
  row.cancelDrag=()=>{
    start=null;row.dragging=false;
    const captured=pointerId;pointerId=null;
    if(captured!==null&&region.hasPointerCapture(captured))region.releasePointerCapture(captured);
    restore();
  };
  region.onpointerdown=event=>{
    if(event.button!==0||session.tool!=='region'||start)return;event.preventDefault();window.getSelection()?.removeAllRanges();
    start={x:event.clientX,y:event.clientY};pointerId=event.pointerId;row.dragging=true;
    region.setPointerCapture(event.pointerId);highlight(marks,[]);
  };
  region.onpointermove=event=>{
    if(!start||event.pointerId!==pointerId)return;
    const drawn=dragRect(start,{x:event.clientX,y:event.clientY},frame.getBoundingClientRect());
    highlight(marks,drawn?[drawn]:[],'region');
  };
  region.onpointerup=event=>{
    if(!start||event.pointerId!==pointerId)return;
    const bounds=frame.getBoundingClientRect(),drawn=dragRect(start,{x:event.clientX,y:event.clientY},bounds);
    start=null;pointerId=null;row.dragging=false;
    if(region.hasPointerCapture(event.pointerId))region.releasePointerCapture(event.pointerId);
    if(!drawn||(drawn[2]-drawn[0])*bounds.width<6||(drawn[3]-drawn[1])*bounds.height<6){restore();return;}
    highlight(marks,[drawn],'region');
    session.onSelection?.({kind:'region',text:'',rects:[drawn],page:row.number},{x:event.clientX,y:event.clientY});
  };
  region.onpointercancel=()=>row.cancelDrag?.();
  region.onlostpointercapture=()=>{if(start)row.cancelDrag?.();};
}

async function renderRow(session,row) {
  const token=++row.token;
  const current=()=>valid(session)&&row.token===token;
  row.state='loading';
  try {
    const page=await session.doc.getPage(row.number);
    if(!current())return;
    const base=page.getViewport({scale:1});
    // Correct placeholder dimensions if the PDF metadata differs from the server's.
    const size=session.sizes[row.number-1];
    if(Math.abs(base.width-size[0])>.5||Math.abs(base.height-size[1])>.5){
      session.sizes[row.number-1]=[base.width,base.height];relayout(session);return;
    }
    row.pdfPage=page;
    const viewport=page.getViewport({scale:row.layout.width/base.width});
    row.frame.style.setProperty('--total-scale-factor',String(viewport.scale*(viewport.userUnit||1)));
    row.frame.style.setProperty('--scale-factor',String(viewport.scale));
    const canvas=document.createElement('canvas');canvas.className='pdf-canvas';canvas.setAttribute('aria-label','PDF 原文第 '+row.number+' 页');
    const pixelRatio=Math.min(window.devicePixelRatio||1,2,Math.sqrt(14_000_000/(viewport.width*viewport.height)));
    canvas.width=Math.ceil(viewport.width*pixelRatio);canvas.height=Math.ceil(viewport.height*pixelRatio);
    canvas.style.width=viewport.width+'px';canvas.style.height=viewport.height+'px';row.canvas=canvas;
    const text=document.createElement('div');text.className='textLayer';
    const marks=document.createElement('div');marks.className='pdf-marks';row.marks=marks;
    const region=document.createElement('div');region.className='pdf-region-tool';region.hidden=session.tool!=='region';row.region=region;
    row.frame.replaceChildren(canvas,text,marks,region);
    row.renderTask=page.render({canvas,canvasContext:canvas.getContext('2d'),viewport,
      transform:pixelRatio===1?null:[pixelRatio,0,0,pixelRatio,0,0]});
    await row.renderTask.promise;
    if(!current())return;
    const textContent=await page.getTextContent();
    if(!current())return;
    const layer=new TextLayer({textContentSource:textContent,container:text,viewport});row.textLayer=layer;
    await layer.render();
    if(!current())return;
    row.hasText=textContent.items.some(item=>item.str?.trim());row.state='ready';
    const selected=selectedOn(session,row);highlight(marks,selected?.rects,selected?.kind);
    bindSelection(session,row,text,marks,region);statusText(session);
  } catch(error) {
    if(!current()||error.name==='RenderingCancelledException')return;
    row.state='error';
    const message=document.createElement('div');message.className='pdf-page-error';message.textContent='第 '+row.number+' 页加载失败：'+error.message;
    const retry=document.createElement('button');retry.className='button';retry.textContent='重新加载';
    retry.onclick=()=>{disposeRow(row);sync(session);};message.append(retry);row.frame.replaceChildren(message);
    statusText(session);session.onError?.(error);
  }
}

function pump(session) {
  if(!valid(session))return;
  while(session.loading<2){
    const row=session.rows.filter(r=>r.wanted&&r.state==='idle').sort((a,b)=>Math.abs(a.number-session.pageNumber)-Math.abs(b.number-session.pageNumber))[0];
    if(!row)break;
    ++session.loading;
    renderRow(session,row).finally(()=>{--session.loading;pump(session);});
  }
}

function sync(session) {
  if(!valid(session)||!session.rows.length)return;
  const next=readingPage(session.layouts,session.scroll.scrollTop,session.scroll.clientHeight);
  if(next!==session.pageNumber){session.pageNumber=next;session.onPageChange?.(next);statusText(session);}
  const wanted=new Set(nearbyPages(session.layouts,session.scroll.scrollTop,session.scroll.clientHeight));
  const nativeSelection=window.getSelection();
  for(const row of session.rows){
    // Preserve the text layer beneath an active drag or native text selection.
    row.wanted=wanted.has(row.number)||row.dragging||Boolean(nativeSelection&&!nativeSelection.isCollapsed&&row.frame.contains(nativeSelection.anchorNode));
    if(!row.wanted&&row.state!=='idle')disposeRow(row);
  }
  pump(session);
}

function schedule(session) {
  if(session.tick)return;
  session.tick=requestAnimationFrame(()=>{session.tick=0;sync(session);});
}

function relayout(session, preserve=true) {
  const position=capturePosition(session.layouts,session.scroll.scrollTop);
  const oldWidth=session.list?.clientWidth||1;
  const center=(session.scroll.scrollLeft+session.scroll.clientWidth/2)/oldWidth;
  session.width=session.scroll.clientWidth;
  session.layouts=layoutPages(session.sizes,session.zoom,session.width-PAGE_PADDING*2);
  session.list.style.width=Math.max(session.width-PAGE_PADDING*2,...session.layouts.map(p=>p.width))+'px';
  for(const row of session.rows){
    disposeRow(row);row.layout=session.layouts[row.number-1];
    row.slot.style.width=row.frame.style.width=row.layout.width+'px';
    row.slot.style.height=row.frame.style.height=row.layout.height+'px';
  }
  if(preserve){
    session.scroll.scrollTop=restorePosition(session.layouts,position);
    session.scroll.scrollLeft=Math.max(0,center*session.list.clientWidth-session.scroll.clientWidth/2);
  }
  schedule(session);
}

function goToPage(number,rects=null) {
  const session=active;if(!valid(session))return false;
  session.pageNumber=Math.max(1,Math.min(session.doc?.numPages||session.pageCount||500,Math.trunc(Number(number))||1));
  session.pendingRects=rects;
  const row=session.rows[session.pageNumber-1];
  if(row){
    const y=rects?.length?Math.min(...rects.map(r=>r[1]))*row.layout.height-70:-PAGE_PADDING;
    session.scroll.scrollTop=Math.max(0,row.layout.top+y);
    if(rects?.length){
      const x=(Math.min(...rects.map(r=>r[0]))+Math.max(...rects.map(r=>r[2])))/2;
      session.scroll.scrollLeft=Math.max(0,(session.list.clientWidth-row.layout.width)/2+x*row.layout.width-session.scroll.clientWidth/2);
    }
    sync(session);
  }
  session.onPageChange?.(session.pageNumber);
  return true;
}

async function mount(options) {
  const {host,paperId,pageNumber=1,zoom='fit',tool='text',selection,pageSizes=[],onSelection,onPageChange,onScroll,onError}=options;
  if(!host?.isConnected)return;
  if(active?.host===host&&active.paperId===paperId){
    const zoomChanged=active.zoom!==zoom;
    Object.assign(active,{zoom,tool,selection,onSelection,onPageChange,onScroll,onError});
    if(zoomChanged&&active.rows.length)relayout(active);
    applyTools(active);return;
  }
  stopRender();
  const session={host,paperId,pageNumber,zoom,tool,selection,onSelection,onPageChange,onScroll,onError,
    status:host.querySelector('.pdf-load-status'),scroll:host.querySelector('.pdf-scroll'),
    rows:[],layouts:[],sizes:[],loading:0,tick:0,alive:true,events:new AbortController(),pageCount:pageSizes.length,
    pendingRects:selection?.page===pageNumber?selection.rects:null};
  active=session;session.status.textContent='正在加载连续 PDF 阅读器…';
  try {
    if(cachedId!==paperId){
      loadingTask?.destroy().catch(()=>{});cachedId=paperId;
      loadingTask=getDocument({url:'/api/papers/'+encodeURIComponent(paperId)+'/file',
        cMapUrl:assets+'cmaps/',cMapPacked:true,standardFontDataUrl:assets+'standard_fonts/',wasmUrl:assets+'wasm/',
        isEvalSupported:false,enableXfa:false});
    }
    session.doc=await loadingTask.promise;
    if(!valid(session))return;
    const first=await session.doc.getPage(1);
    if(!valid(session))return;
    const base=first.getViewport({scale:1});
    session.sizes=Array.from({length:session.doc.numPages},(_,i)=>pageSizes[i]?.every(n=>Number.isFinite(n)&&n>0)?[...pageSizes[i]]:[base.width,base.height]);
    session.list=document.createElement('div');session.list.className='pdf-page-list';
    session.rows=session.sizes.map((_,i)=>{
      const slot=document.createElement('div');slot.className='pdf-page-slot';
      const frame=document.createElement('div');frame.className='pdf-canvas-page';frame.dataset.page=String(i+1);
      frame.setAttribute('role','group');frame.setAttribute('aria-label','PDF 第 '+(i+1)+' 页');
      const label=document.createElement('div');label.className='pdf-page-number';label.textContent=(i+1)+' / '+session.doc.numPages;
      slot.append(frame,label);session.list.append(slot);
      return {number:i+1,slot,frame,state:'idle',token:0,wanted:false,dragging:false};
    });
    session.scroll.replaceChildren(session.list);relayout(session,false);
    goToPage(session.pageNumber,session.pendingRects);
    session.scroll.addEventListener('scroll',()=>{session.onScroll?.();schedule(session);},{passive:true,signal:session.events.signal});
    session.observer=new ResizeObserver(()=>{
      if(!valid(session))return;
      if(Math.abs(session.scroll.clientWidth-session.width)>=2){
        clearTimeout(session.resizeTimer);session.resizeTimer=setTimeout(()=>{if(valid(session))relayout(session);},140);
      }else schedule(session);
    });
    session.observer.observe(session.scroll);statusText(session);
  }catch(error){
    if(!valid(session))return;
    session.status.textContent='PDF 加载失败：'+error.message;onError?.(error);
  }
}

function clear(){stopRender();}
window.YannianPDF={mount,clear,goToPage};
// Keep already-open tabs compatible during the rename.
window.YanjiPDF=window.YannianPDF;
window.dispatchEvent(new Event('yannian-pdf-ready'));
window.dispatchEvent(new Event('yanji-pdf-ready'));
