'use strict';
const $ = (q, root=document) => root.querySelector(q);
const $$ = (q, root=document) => [...root.querySelectorAll(q)];
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const safeUrl = url => { try { const u = new URL(url, location.origin); return ['http:','https:'].includes(u.protocol) ? u.href : '#'; } catch { return '#'; } };
const icons = {
 download:'<path d="M12 3v12m-5-5 5 5 5-5M5 17v4h14v-4"/>',
 book:'<path d="M3 4h6a3 3 0 0 1 3 3v14a4 4 0 0 0-4-3H3z"/><path d="M21 4h-6a3 3 0 0 0-3 3v14a4 4 0 0 1 4-3h5z"/>',
 search:'<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
 bulb:'<path d="M9 18h6m-5 3h4M8 14a6 6 0 1 1 8 0l-1 2H9z"/>',
 folder:'<path d="M3 6h7l2 3h9v11H3z"/>',
 settings:'<path d="m9 3-1 3-3 1 1 3-2 2 2 2-1 3 3 1 1 3h6l1-3 3-1-1-3 2-2-2-2 1-3-3-1-1-3z"/><circle cx="12" cy="12" r="3"/>',
 file:'<path d="M5 3h9l5 5v13H5zM14 3v6h5M8 13h8m-8 4h6"/>',
 arrow:'<path d="M5 12h14m-5-5 5 5-5 5"/>',
 back:'<path d="M19 12H5m5-5-5 5 5 5"/>',
 sidebar:'<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/>',
 chat:'<path d="M21 15a2 2 0 0 1-2 2H8l-5 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
 upload:'<path d="M12 16V3m-5 5 5-5 5 5M4 15v6h16v-6"/>',
 spark:'<path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5z"/>',
 link:'<path d="m10 13 4-4m-6 6-2 2a4 4 0 0 1-6-6l4-4a4 4 0 0 1 6 0m4 2 2-2a4 4 0 0 1 6 6l-4 4a4 4 0 0 1-6 0" transform="translate(1 0)"/>',
 history:'<path d="M3 11a9 9 0 1 1 2 7M3 4v7h7m2-5v7l4 2"/>',
 zotero:'<path d="M5 4h14L5 20h14"/>',
 check:'<path d="m5 12 4 4L19 6"/>',
 grid:'<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>'
};
const icon = name => `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">${icons[name] || icons.file}</svg>`;
const statuses = {spark:'灵感',exploring:'探索中',testing:'验证中',parked:'暂存'};
const state = {view:'library', projectId:null, library:{papers:[],projects:[],idea_count:0}, settings:{}, paper:null,
 para:null, selectedText:'', readerMode:'original', pdfZoom:'fit', pdfTool:'text', pdfSelection:null, page:1, messages:[], conversationId:null, chatBusy:false, chatRun:null, chatRenderedKey:null, chatContextOpen:false,
 ideas:[], idea:null, analyses:[], filter:'', ideaFilter:'all', discover:null, query:'', venue:'all', searchBusy:false,
 analysisBusy:false, searchGeneration:0, searchJob:null, searchDepth:'deep', searchWeb:true, searchLanguage:'english', searchOrder:'relevance', searchPage:1, searchAudit:false, pdfFetching:null,
 layout:readLayoutPreferences(),urlImport:{url:'',projectId:'',busy:false,result:null,error:''}};
let toastTimer, searchTimer;
function clampAssistantRatio(value){const n=Number(value);return Number.isFinite(n)&&n>0?Math.max(.2,Math.min(.7,n)):.34;}
function applySplitWidth(){
 const ratio=clampAssistantRatio(state.layout.assistantRatio),workspace=$('#workspace'),handle=$('#reader-splitter');
 workspace.style.setProperty?.('--assistant-width',(ratio*100)+'%');
 handle?.setAttribute('aria-valuenow',String(Math.round(ratio*100)));
 handle?.setAttribute('aria-valuetext','对话区 '+Math.round(ratio*100)+'%');
}
function setSplitWidth(ratio,save=true){
 state.layout.assistantRatio=clampAssistantRatio(ratio);applySplitWidth();
 if(save)try{localStorage.setItem('yannian-layout',JSON.stringify(state.layout));}catch{}
}
function setupReaderSplitter(){
 const handle=$('#reader-splitter');if(!handle)return;let pointer=null;
 handle.onpointerdown=e=>{if(e.button!==0)return;pointer=e.pointerId;handle.setPointerCapture?.(pointer);$('#workspace').classList.add('resizing-reader');e.preventDefault();};
 handle.onpointermove=e=>{if(e.pointerId!==pointer)return;const box=$('#workspace').getBoundingClientRect();if(box.width>0)setSplitWidth((box.left+box.width-e.clientX)/box.width,false);};
 const end=e=>{if(pointer!==e.pointerId)return;pointer=null;$('#workspace').classList.remove('resizing-reader');setSplitWidth(state.layout.assistantRatio);};
 handle.onpointerup=end;handle.onpointercancel=end;handle.onlostpointercapture=end;
 handle.ondblclick=()=>setSplitWidth(.34);
 handle.onkeydown=e=>{if(!['ArrowLeft','ArrowRight','Home'].includes(e.key))return;e.preventDefault();setSplitWidth(e.key==='Home'?.34:clampAssistantRatio(state.layout.assistantRatio)+(e.key==='ArrowLeft'?.02:-.02));};
}
function readLayoutPreferences(){
 try{const saved=JSON.parse(localStorage.getItem('yannian-layout')||localStorage.getItem('yanji-layout')||'{}');return {sidebarCollapsed:saved.sidebarCollapsed===true,assistantCollapsed:saved.assistantCollapsed===true,assistantRatio:clampAssistantRatio(saved.assistantRatio)};}catch{return {sidebarCollapsed:false,assistantCollapsed:false,assistantRatio:.34};}
}
function applyLayout(){
 $('#navigation-sidebar').hidden=state.layout.sidebarCollapsed;
 $('#workspace').classList.toggle('assistant-collapsed',state.layout.assistantCollapsed);
 for(const [id,collapsed,label,shape] of [['sidebar-toggle',state.layout.sidebarCollapsed,'导航','sidebar'],['assistant-toggle',state.layout.assistantCollapsed,'对话','chat']]){
  const button=$('#'+id),text=(collapsed?'展开':'收起')+label;
  button.innerHTML=icon(shape)+'<span>'+text+'</span>';button.title=text+'栏';button.setAttribute('aria-label',text+'栏');button.setAttribute('aria-expanded',String(!collapsed));
 }
 $('#assistant-toggle').hidden=state.view!=='reader';
 applySplitWidth();
}
function togglePanel(panel,collapsed){
 const key=panel==='sidebar'?'sidebarCollapsed':'assistantCollapsed';
 state.layout[key]=typeof collapsed==='boolean'?collapsed:!state.layout[key];
 try{localStorage.setItem('yannian-layout',JSON.stringify(state.layout));}catch{}
 $('#selection-menu').hidden=true;applyLayout();
}
function positionReader(position){
 const main=$('#main-pane'),reader=$('#pdf-reader');if(!main||!reader)return;
 const top=position==='top'?main.scrollTop+reader.getBoundingClientRect().top-main.getBoundingClientRect().top-($('.topbar')?.offsetHeight||73)-8:0;
 main.scrollTo({top:Math.max(0,top),behavior:'smooth'});
}
function toast(text, error=false) {
 const el=$('#toast'); el.textContent=text; el.className='toast show'+(error?' error':'');
 clearTimeout(toastTimer); toastTimer=setTimeout(()=>el.classList.remove('show'),error?7500:4000);
}
async function api(path, options={}) {
 const init={...options,headers:{'X-Yannian':'1',...options.headers}};
 if (init.body && !(init.body instanceof FormData)) {init.headers['Content-Type']='application/json';init.body=JSON.stringify(init.body);}
 const response=await fetch('/api'+path,init);
 if(!response.ok) {let message;try {const e=await response.json();message=typeof e.detail==='string'?e.detail:'输入信息不完整，请检查必填项。';}catch{message='请求失败，请确认本地服务正在运行。';}
   const error=new Error(message);error.status=response.status;throw error;}
 return response.json();
}
async function busy(button, operation) {
 if(button?.disabled)return;
 const text=button?.innerHTML;
 if(button){button.disabled=true;button.innerHTML='<span class="spinner"></span> 处理中';}
 try{return await operation();}catch(e){toast(e.message,true);}finally{if(button?.isConnected){button.disabled=false;button.innerHTML=text;}}
}
function markdown(text,citations=[]) {
 let html=DOMPurify.sanitize(marked.parse(text||'',{breaks:true}),{FORBID_TAGS:['img','iframe','style','input','form','button']});
 const holder=document.createElement('div');holder.innerHTML=html;
 $$('a',holder).forEach(a=>{a.href=safeUrl(a.getAttribute('href'));a.target='_blank';a.rel='noopener noreferrer';});
 const byLabel=Object.fromEntries(citations.filter(c=>c.type==='paragraph').map(c=>[c.label,c]));
 const walker=document.createTreeWalker(holder,NodeFilter.SHOW_TEXT);const nodes=[];while(walker.nextNode())nodes.push(walker.currentNode);
 for(const node of nodes){if(node.parentElement.closest('pre,code,a,.math-formula,.math-fallback,math,annotation'))continue;const re=/\[(P\d+)\]/g;let match,last=0;const frag=document.createDocumentFragment();let changed=false;
  while((match=re.exec(node.textContent))){const c=byLabel[match[1]];if(!c)continue;frag.append(node.textContent.slice(last,match.index));const b=document.createElement('button');b.className='citation-button';b.dataset.action='citation';b.dataset.paragraph=c.paragraph_id;b.dataset.paper=c.paper_id;b.textContent=`第 ${c.page} 页`;frag.append(b);last=match.index+match[0].length;changed=true;}
  if(changed){frag.append(node.textContent.slice(last));node.replaceWith(frag);}}
 return holder.innerHTML;
}
function modal(title,body,setup) {
 $('#modal-content').innerHTML=`<div class="modal-header"><h2>${esc(title)}</h2><button class="icon-button" data-close aria-label="关闭">×</button></div><div class="modal-body">${body}</div>`;
 if(!$('#modal').open)$('#modal').showModal();
 $$('[data-close]',$('#modal')).forEach(b=>b.onclick=()=>$('#modal').close());
 if(setup)setup($('#modal-content'));
}
const projectOptions=(selected='',empty='暂不归入项目')=>`<option value="">${empty}</option>`+state.library.projects.map(p=>`<option value="${p.id}" ${p.id===selected?'selected':''}>${esc(p.name)}</option>`).join('');
async function refreshLibrary(){state.library=await api('/library');renderSidebar();}
function renderSidebar(){
 const nav=[['library','book','文献库',state.library.papers.length],['discover','search','发现论文',''],['ideas','bulb','Idea 笔记',state.library.idea_count]];
 $('#navigation').innerHTML=nav.map(([v,i,t,c])=>`<button class="nav-item ${state.view===v&&!state.projectId?'active':''}" data-view="${v}" title="${t}">${icon(i)}<span class="label">${t}</span><span class="count">${c}</span></button>`).join('');
 $('#project-list').innerHTML=state.library.projects.map(p=>`<button class="project-item ${state.projectId===p.id?'active':''}" data-project="${p.id}"><span class="dot" style="background:${esc(p.color)}"></span><span class="project-name">${esc(p.name)}</span><small>${p.paper_count}</small></button>`).join('');
 $('#organize').innerHTML=icon('grid')+' 相似论文归类';
 $('#zotero-button').innerHTML=icon('zotero')+' 从 Zotero 导入';
 $('#settings-button').innerHTML=icon('settings')+' 模型设置';
 $('#connection-state').textContent=state.settings.provider==='codex'?(state.settings.ready?'● Codex 已连接':'○ Codex 待登录'):(state.settings.ready?'● API 已配置':'○ API 待连接');
 $('#breadcrumb').innerHTML=`个人空间 <span>/</span> ${state.view==='reader'?'专注阅读':state.view==='idea'?'Idea 工作页':state.projectId?esc(state.library.projects.find(p=>p.id===state.projectId)?.name||'项目'):({library:'文献库',discover:'发现论文',ideas:'Idea 笔记'}[state.view]||'文献库')}`;
 $('#workspace').classList.toggle('reader-open',state.view==='reader');
 applyLayout();
}
async function navigate(view,projectId=null){
 window.YannianPDF?.clear();state.view=view;state.projectId=projectId;state.filter='';state.idea=null;$('#selection-menu').hidden=true;history.replaceState(null,'',location.pathname+(view==='discover'&&state.searchJob?'#search='+state.searchJob:''));
 if(view==='ideas')state.ideas=await api('/ideas');
 renderSidebar();render();$('#main-pane')?.scrollTo?.(0,0);window.scrollTo(0,0);
}
function render(){if(state.view==='library')renderLibrary();else if(state.view==='reader'){renderReader();renderAssistant();}else if(state.view==='discover')renderDiscover();else if(state.view==='ideas')renderIdeas();else if(state.view==='idea')renderIdeaDetail();}
function renderLibrary(){
 const project=state.library.projects.find(p=>p.id===state.projectId);
 const papers=state.library.papers.filter(p=>(!state.projectId||p.project_ids.includes(state.projectId))&&(!state.filter||(p.title+' '+p.authors+' '+p.abstract).toLowerCase().includes(state.filter.toLowerCase())));
 const allInProject=state.library.papers.filter(p=>!state.projectId||p.project_ids.includes(state.projectId));
 $('#main-content').innerHTML=`<section class="hero"><div><div class="eyebrow">${project?'A RESEARCH IN PROGRESS':'YOUR NEXT IDEA STARTS HERE'}</div><h1>${project?esc(project.name):'把论文读懂，<br>让想法长出来。'}</h1><p>${project?esc(project.description||'每一篇相关论文，都是这个方向的一个新线索。'):'在段落旁边提问，在阅读途中记下灵感。<br>从一篇论文出发，连接证据、相关工作与下一次实验。'}</p><div class="stats"><div class="stat"><strong>${allInProject.length}</strong><span>篇文献</span></div><div class="stat"><strong>${state.library.projects.length}</strong><span>个研究项目</span></div><div class="stat"><strong>${state.library.idea_count}</strong><span>个想法</span></div></div></div><div class="hero-art" aria-hidden="true"><div class="art-page"><div class="art-lines"></div></div><div class="art-note">what if…</div><span class="art-orbit">✧</span></div></section>
 <div class="section-toolbar"><h2>${project?'项目中的论文':'最近阅读'} <small> / ${papers.length}</small></h2><label class="search-field">${icon('search')}<input id="library-search" aria-label="搜索文献" placeholder="搜索标题、作者或摘要…" value="${esc(state.filter)}"></label></div>
 ${papers.length?`<div class="paper-list">${papers.map(p=>`<article class="paper-row" data-open-paper="${p.id}" tabindex="0" role="button" aria-label="阅读 ${esc(p.title)}"><div class="document-icon">${icon('file')}</div><div><span class="paper-title">${esc(p.title)}</span><div class="paper-meta">${esc(p.authors||'作者信息待补充')} · ${p.has_pdf?p.page_count+' 页':'仅元数据'} · ${esc(p.source==='upload'?'本地 PDF':p.source)}</div></div><div class="paper-tags">${p.project_ids.slice(0,2).map(id=>`<span class="tag">${esc(state.library.projects.find(x=>x.id===id)?.name)}</span>`).join('')}</div><div class="paper-year">${esc(p.year.slice(0,4)||'—')}</div></article>`).join('')}</div>`:`<div class="empty-state">${icon('book')}<h3>${state.filter?'还没有找到匹配的文献':'从你的第一篇论文开始'}</h3><p>${state.filter?'试试更短的关键词，或者切换到其他项目。':'拖入 PDF，从 arXiv 导入，或连接已有的 Zotero 文献库。<br>导入后，每个段落都可以直接提问和记录 idea。'}</p>${!state.filter?'<button class="button primary" data-action="import">＋ 导入论文</button> <button class="button" data-action="zotero">连接 Zotero</button>':''}</div>`}
 <div class="starter">${icon('spark')}<span>寻找下一条线索？</span><button data-action="discover-ai">探索 AI 论文</button>${project?'<button data-action="project-ideas">查看项目 idea</button>':''}</div>`;
 $('#library-search').oninput=e=>{const pos=e.target.selectionStart;state.filter=e.target.value;renderLibrary();$('#library-search').focus();$('#library-search').setSelectionRange(pos,pos);};
}
async function openPaper(id,paragraphId=null,pageNumber=null){
 const paper=await api('/papers/'+id);state.paper=paper;state.view='reader';state.para=paper.paragraphs.find(p=>p.id===paragraphId)||null;state.page=Math.max(1,Math.min(paper.page_count||1,Math.trunc(Number(pageNumber))||state.para?.page||1));
 state.messages=[];state.conversationId=null;state.selectedText='';state.pdfSelection=null;state.readerMode='original';state.pdfZoom='fit';$('#selection-menu').hidden=true;
 history.replaceState(null,'','#paper='+id+(paragraphId?'&paragraph='+paragraphId:''));
 renderSidebar();renderReader();renderAssistant();$('#main-pane')?.scrollTo?.(0,0);window.scrollTo(0,0);
 await restoreChat();
 if(paragraphId)setTimeout(()=>$('#para-'+paragraphId)?.scrollIntoView({block:'center',behavior:'smooth'}),80);
}
function renderReader(){
 const p=state.paper;if(!p)return;
 updateReaderLocation();
 $('#main-content').innerHTML=`<div class="reader-toolbar"><button class="button ghost" data-action="back-library">${icon('back')} 文献库</button><div class="segmented"><button data-reader-mode="paragraphs" class="${state.readerMode==='paragraphs'?'active':''}">文字提取（辅助）</button><button data-reader-mode="original" class="${state.readerMode==='original'?'active':''}">PDF 原文</button></div></div>
 <div class="eyebrow">${esc(p.source==='upload'?'YOUR READING DESK':p.source)} · ${p.page_count||0} PAGES</div><h1 class="reader-title">${esc(p.title)}</h1><div class="reader-authors">${esc(p.authors||'作者信息待补充')}${p.year?' · '+esc(p.year):''}</div>
 <div class="reader-actions"><button class="button soft" data-action="similar">${icon('search')} 找相似论文</button><button class="button ghost" data-action="paper-projects">${icon('folder')} 归入项目</button><button class="button ghost" data-action="paper-idea">${icon('bulb')} 记 idea</button>${p.has_pdf?`<a class="button ghost" href="/api/papers/${p.id}/file" target="_blank" rel="noopener">打开 PDF ↗</a>`:'<button class="button primary" data-action="fetch-pdf">'+icon('download')+' 自动获取 PDF</button><button class="button ghost" data-action="attach">手动上传</button>'}</div>
 ${p.pdf_origin?`<p class="subtle">PDF 已自动保存 · <a href="${esc(safeUrl(p.pdf_origin))}" target="_blank" rel="noopener">${p.pdf_origin.includes('arxiv.org/')?'arXiv 版本':'下载来源'} ↗</a> · ${new Date(p.pdf_fetched_at).toLocaleDateString('zh-CN')}</p>`:''}
 ${!p.has_pdf?`<div class="notice">这条文献尚未保存 PDF。点击“自动获取 PDF”，工作台会查找公开原文并保存，随后即可逐段阅读。你也可以先基于摘要提问。</div><p class="idea-body">${esc(p.abstract||'暂无摘要。')}</p>`:state.readerMode==='paragraphs'?`<div class="reading-note">将鼠标移到段落旁，提问或记下想法。也可以选中一句话。<br>文字由 PDF 提取；公式、表格和跨栏顺序请切换原文核对。</div><div class="paragraphs">${paragraphMarkup(p.paragraphs)}</div>`:originalMarkup()}`;
 if($('#pdf-page-input'))$('#pdf-page-input').onchange=e=>goToPDFPage(e.target.value);
 if($('#pdf-zoom'))$('#pdf-zoom').onchange=e=>{state.pdfZoom=e.target.value;$('#selection-menu').hidden=true;mountPDF();};
 if(p.has_pdf&&state.readerMode==='original')mountPDF();else window.YannianPDF?.clear();
}
function paragraphMarkup(paragraphs){
 if(!paragraphs.length)return '<div class="notice">没有提取到可选择的文字。请切换原文，并勾选「附上本页图像」提问；本版没有自动 OCR。</div>';
 let last=0;
 return paragraphs.map(p=>{let heading='';if(p.page!==last){heading=`<div class="page-label">PAGE ${String(p.page).padStart(2,'0')}</div>`;last=p.page;}
 return heading+`<article class="paragraph ${state.para?.id===p.id?'selected':''}" id="para-${p.id}" data-para="${p.id}"><span class="paragraph-num">${p.ordinal+1}</span><p>${esc(p.text)}</p><div class="paragraph-actions"><button data-ask-para="${p.id}">✧ 问这一段</button><button data-idea-para="${p.id}">＋ 记灵感</button><button data-original-para="${p.id}" title="在 PDF 原文中定位">原文 ↗</button></div></article>`;}).join('');
}
function originalMarkup(){
 const p=state.paper;
 return `<div class="reader-position"><span>框外滚动调整阅读区位置 · 框内滚动连续翻页</span><div class="reader-position-actions"><button class="button ghost" data-reader-position="top">阅读区置顶 ↑</button><button class="button ghost" data-reader-position="info">论文信息</button></div></div><section class="pdf-reader" id="pdf-reader"><div class="pdf-toolbar"><div class="pdf-toolbar-group"><button data-page-step="-1" ${state.page<=1?'disabled':''} title="上一页">←</button><span>第 <input id="pdf-page-input" aria-label="PDF 页码" type="number" min="1" max="${p.page_count}" value="${state.page}"> / ${p.page_count} 页</span><button data-page-step="1" ${state.page>=p.page_count?'disabled':''} title="下一页">→</button><select id="pdf-zoom" aria-label="PDF 缩放">${[['fit','适应宽度'],['1','100%'],['1.25','125%'],['1.5','150%'],['2','200%']].map(([v,t])=>`<option value="${v}" ${String(state.pdfZoom)===v?'selected':''}>${t}</option>`).join('')}</select></div><div class="pdf-toolbar-group"><button data-pdf-tool="text" class="${state.pdfTool==='text'?'active':''}">选中文字</button><button data-pdf-tool="region" class="${state.pdfTool==='region'?'active':''}">框选图片/区域</button></div></div><div class="pdf-load-status" role="status">正在加载 PDF 阅读器…</div><div class="pdf-scroll" tabindex="0" role="region" aria-label="PDF 连续阅读区域，滚动可翻页"></div></section>`;
}
function updateReaderLocation(){
 if(!state.paper)return;
 history.replaceState(null,'','#paper='+state.paper.id+'&page='+state.page+(state.pdfSelection?'&selection='+state.pdfSelection.id:state.para?'&paragraph='+state.para.id:''));
}
function updatePDFPage(page){
 state.page=Math.max(1,Math.min(state.paper.page_count||1,Math.trunc(Number(page))||1));
 const field=$('#pdf-page-input');if(field)field.value=state.page;
 const prev=$('[data-page-step="-1"]'),next=$('[data-page-step="1"]');
 if(prev)prev.disabled=state.page<=1;if(next)next.disabled=state.page>=state.paper.page_count;
 updateReaderLocation();
}
function goToPDFPage(page,rects=null){
 updatePDFPage(page);$('#selection-menu').hidden=true;
 if(!window.YannianPDF?.goToPage?.(state.page,rects))renderReader();
}
function mountPDF(){
 if(!window.YannianPDF)return;
 const paperId=state.paper.id,selected=state.pdfSelection||(state.para?.display_bbox?{page:state.para.page,kind:'text',rects:[state.para.display_bbox]}:null);
 window.YannianPDF.mount({host:$('#pdf-reader'),paperId,pageNumber:state.page,pageSizes:state.paper.page_sizes||[],zoom:state.pdfZoom,tool:state.pdfTool,selection:selected,
   onSelection:showPDFSelectionMenu,onPageChange:page=>{if(state.view==='reader'&&state.readerMode==='original'&&state.paper?.id===paperId)updatePDFPage(page);},
   onScroll:()=>{$('#selection-menu').hidden=true;},onError:e=>toast('PDF 原页加载失败：'+e.message,true)});
}
window.addEventListener?.('yannian-pdf-ready',()=>{if(state.view==='reader'&&state.readerMode==='original')mountPDF();});
function showPDFSelectionMenu(candidate,position){
 if(state.chatBusy){toast('当前回答正在生成，请稍后选择新的提问范围。');return;}
 const paperId=state.paper.id,menu=$('#selection-menu');
 menu.hidden=false;menu.style.left=Math.max(10,Math.min(window.innerWidth-240,position.x))+'px';menu.style.top=Math.max(10,Math.min(window.innerHeight-55,position.y+8))+'px';
 $('#selection-ask').textContent=candidate.kind==='region'?'分析选中区域':'问选中文字';
 const use=async(forIdea)=>{
  if(state.paper.id!==paperId||state.chatBusy)return;
  menu.hidden=true;
  try{
   const selected=await api('/papers/'+paperId+'/selections',{method:'POST',body:candidate});
   if(state.paper.id!==paperId)return;
   state.pdfSelection=selected;state.para=state.paper.paragraphs.find(p=>p.id===selected.paragraph_id)||null;
   state.selectedText=selected.text;state.page=selected.page;state.chatContextOpen=true;
   window.getSelection()?.removeAllRanges();updatePDFPage(selected.page);renderAssistant();mountPDF();
   if(forIdea)ideaModal({quote:selected.text||'PDF 第 '+selected.page+' 页的框选区域',paperId,paragraphId:selected.paragraph_id,selectionId:selected.id});
   else{togglePanel('assistant',false);$('#chat-question').value=candidate.kind==='region'?'请分析这个选区，解释图表、公式或流程表达的含义，以及它如何支持论文的结论。':'';saveChatDraft($('#chat-question').value);$('#chat-question').focus();$('#assistant-panel').scrollIntoView?.({block:'nearest',behavior:'smooth'});toast('选区已附到右侧对话，输入问题后发送。');}
  }catch(e){toast(e.message,true);}
 };
 $('#selection-ask').onclick=()=>use(false);$('#selection-idea').onclick=()=>use(true);
}
async function openPDFSelection(id){
 const selected=await api('/selections/'+id);
 if(state.paper?.id!==selected.paper_id||state.view!=='reader')await openPaper(selected.paper_id);
 state.pdfSelection=selected;state.para=state.paper.paragraphs.find(p=>p.id===selected.paragraph_id)||null;
 const alreadyReading=state.readerMode==='original'&&Boolean($('#pdf-reader'));
 state.selectedText=selected.text;state.page=selected.page;state.readerMode='original';
 if(alreadyReading){mountPDF();goToPDFPage(selected.page,selected.rects);}else renderReader();
 renderAssistant();$('#pdf-reader')?.scrollIntoView({block:'nearest',behavior:'smooth'});
}
function selectParagraph(id,focus=true){
 if(state.chatBusy){toast('当前回答正在生成，请稍后切换对话。');return;}
 state.pdfSelection=null;state.para=state.paper.paragraphs.find(p=>p.id===id)||null;state.selectedText='';$('#selection-menu').hidden=true;state.page=state.para?.page||state.page;state.chatContextOpen=Boolean(id);
 $$('.paragraph.selected').forEach(e=>e.classList.remove('selected'));$('#para-'+id)?.classList.add('selected');
 if(state.readerMode==='original')renderReader();renderAssistant();if(focus){togglePanel('assistant',false);$('#chat-question')?.focus();}
}
function newProjectModal(){
 modal('开始一个研究项目',`<form id="project-form"><label class="field">项目名称<input name="name" required maxlength="120" placeholder="例如：可靠的 LLM Agent"></label><label class="field">研究问题与关键词<textarea name="description" placeholder="这个方向要解决什么问题？填写中英文关键词可以改善相似论文归类。"></textarea></label><div class="modal-footer"><button type="button" class="button" data-close>取消</button><button class="button primary" type="submit">创建项目</button></div></form>`,()=>{
  $('#project-form').onsubmit=e=>{e.preventDefault();busy($('button[type=submit]',e.target),async()=>{const form=new FormData(e.target);const p=await api('/projects',{method:'POST',body:{name:form.get('name'),description:form.get('description')}});await refreshLibrary();$('#modal').close();await navigate('library',p.id);toast('项目已创建');});};});
}
async function fetchPaperPDF(paper){
 if(state.pdfFetching){toast('已有 PDF 正在获取，请等待当前下载完成。');return;}
 state.pdfFetching=paper.id;
 modal('自动获取 PDF',`<h3>${esc(paper.title)}</h3><div id="pdf-fetch-progress"><div class="loading"><span class="spinner"></span> 正在查找公开原文、下载并解析 PDF…</div><p class="subtle">通常需要数十秒，网络较慢时最多约 2 分半钟。下载成功后保存在这条文献中；不需要模型额度。</p></div>`);
 try{
  const result=await api('/papers/'+paper.id+'/fetch-pdf',{method:'POST'});
  await refreshLibrary();
  if(result.status==='ready'){
   if($('#pdf-fetch-progress'))$('#modal').close();
   await openPaper(paper.id);
   toast('PDF 已保存'+(result.source?' · '+result.source:'')+'，可以开始逐段阅读');
   if(result.warning)toast(result.warning,true);
  }else{
   const area=$('#pdf-fetch-progress');
   if(area)area.innerHTML=`<div class="notice">${esc(result.message)}</div><details class="pdf-attempts"><summary>查看已尝试的来源</summary>${(result.attempts||[]).map(a=>`<p><strong>${esc(a.source||'论文来源')}</strong> · ${esc(a.reason||a.status)}</p>`).join('')||'<p>本次请求未能完成。</p>'}</details><div class="pdf-fetch-actions"><button class="button primary" id="pdf-fetch-retry">重试自动获取</button>${paper.url?`<a class="button" href="${esc(safeUrl(paper.url))}" target="_blank" rel="noopener">打开论文来源 ↗</a>`:''}<button class="button ghost" id="pdf-fetch-upload">手动上传 PDF</button></div>`;
   if($('#pdf-fetch-retry'))$('#pdf-fetch-retry').onclick=()=>fetchPaperPDF(paper);
   if($('#pdf-fetch-upload'))$('#pdf-fetch-upload').onclick=()=>importModal(paper.id);
   toast(result.message,true);
  }
 }catch(e){
  const area=$('#pdf-fetch-progress');
  if(area){area.innerHTML=`<div class="notice">${esc(e.message)}</div><button class="button" id="pdf-fetch-retry">重试自动获取</button>`;$('#pdf-fetch-retry').onclick=()=>fetchPaperPDF(paper);}
  toast(e.message,true);
 }finally{state.pdfFetching=null;}
}
function importModal(attachId=null){
 modal(attachId?'为这篇文献附加 PDF':'把论文带进研究空间',`<label class="field">归入项目<select id="import-project">${projectOptions(state.projectId||'')}</select></label><label class="dropzone" id="dropzone">${icon('upload')}<p>拖入 PDF，或点击选择文件</p><small>支持多文件 · 单份最多 40 MB / 500 页</small><input type="file" id="pdf-files" accept="application/pdf,.pdf" ${attachId?'':'multiple'}></label><div id="upload-feedback"></div>${attachId?'':`<div class="import-divider">或从 arXiv 导入</div><form id="arxiv-form" class="discover-search"><input id="arxiv-value" aria-label="arXiv 编号或链接" placeholder="arxiv.org/abs/… 或论文编号" required><button class="button" type="submit">导入</button></form><div class="subtle">已有 Zotero 文献库？ <button type="button" data-action="zotero">从本机 Zotero 选择导入 ↗</button></div>`}` ,()=>{
  const upload=async files=>{const feedback=$('#upload-feedback');let count=0;for(const file of files){feedback.innerHTML=`<div class="loading"><span class="spinner"></span> 正在解析 ${esc(file.name)}…</div>`;
   try{const form=new FormData();form.append('file',file);form.append('project_id',$('#import-project').value);if(attachId)form.append('attach_id',attachId);const result=await api('/papers/upload',{method:'POST',body:form});count++;if(result.warning)toast(result.warning,true);}catch(e){feedback.innerHTML=`<div class="error-box">${esc(e.message)}</div>`;toast(file.name+'：'+e.message,true);}}
   if(count){await refreshLibrary();$('#modal').close();if(attachId)await openPaper(attachId);else await navigate('library',state.projectId);toast(`已导入 ${count} 份 PDF（重复文件会自动复用）`);}};
  $('#pdf-files').onchange=e=>upload([...e.target.files]);const drop=$('#dropzone');drop.ondragover=e=>{e.preventDefault();drop.classList.add('dragover');};drop.ondragleave=()=>drop.classList.remove('dragover');drop.ondrop=e=>{e.preventDefault();drop.classList.remove('dragover');upload([...e.dataTransfer.files]);};
  if($('#arxiv-form'))$('#arxiv-form').onsubmit=e=>{e.preventDefault();busy($('button',e.target),async()=>{const result=await api('/papers/arxiv',{method:'POST',body:{arxiv:$('#arxiv-value').value,project_id:$('#import-project').value||null}});await refreshLibrary();$('#modal').close();await openPaper(result.paper.id);if(result.warning)toast(result.warning,true);});};});
}
function settingsModal(){
 const s=state.settings;
 const models=[...(s.codex?.models||[])];if(s.codex_model&&!models.some(m=>m.id===s.codex_model))models.push({id:s.codex_model,name:s.codex_model});
 modal('连接你的研究伙伴',`<form id="settings-form"><label class="field">连接方式<select id="ai-provider"><option value="codex" ${s.provider!=='api'?'selected':''}>本机 Codex · 使用 ChatGPT 账户额度</option><option value="api" ${s.provider==='api'?'selected':''}>OpenAI API · 单独计费</option></select></label><div id="codex-settings"><p class="subtle">由本机 Codex 处理段落问答与 idea 分析，共享你现有账户的 Codex 用量限制，无需 API Key。额度不足时会提示，不会自动切换到 API。</p><div class="notice info" id="codex-status">${esc(s.codex?.message||'点击重新检测，确认本机 Codex 登录状态。')}</div><button type="button" class="button small" id="refresh-codex">重新检测登录</button><p class="subtle">如果尚未登录，请在终端运行 <code>codex login</code>，完成 ChatGPT 登录后重新检测。连接检测不消耗模型额度；“保存并测试”会发送一条简短请求。</p><label class="field">Codex 模型<select id="codex-model"><option value="">跟随本机 Codex 默认模型</option>${models.map(m=>`<option value="${esc(m.id)}" ${s.codex_model===m.id?'selected':''}>${esc(m.name)}</option>`).join('')}</select></label><label class="field">思考深度<select id="codex-effort">${[['low','快速'],['medium','标准'],['high','深入']].map(([v,t])=>`<option value="${v}" ${(s.codex_effort||'medium')===v?'selected':''}>${t}</option>`).join('')}</select><small>更深入的分析可能耗时更长，也会消耗更多账户额度。</small></label></div><div id="api-settings"><p class="subtle">通过 Responses API 调用，使用独立的 API 账户额度。</p><label class="field">API Key<input id="api-key" type="password" autocomplete="off" placeholder="${s.has_key?'已配置；留空保持当前密钥':'sk-…'}"><small>密钥仅保留在本次服务进程中，不写入浏览器存储或文献数据库。</small></label><label class="field">API 模型名称<input id="model-name" required value="${esc(s.model||'gpt-6-astra')}"></label><label class="field">API 基础地址<input id="base-url" required value="${esc(s.base_url||'https://api.openai.com/v1')}"><small>兼容服务需要支持 /responses。</small></label></div><label class="field checkbox"><input id="web-search" type="checkbox" ${s.web_search?'checked':''}> idea 分析允许联网核查论文、代码和数据集</label><p class="subtle">段落文字和勾选的页面图像会交给所选模型处理。工作台中的对话继续保存在本机，不会继承当前 Codex 窗口的聊天内容。</p><div id="settings-feedback"></div><div class="modal-footer"><button type="button" class="button" id="clear-key">清除本次密钥</button><button type="button" class="button" id="test-model">保存并测试</button><button class="button primary" type="submit">保存设置</button></div></form>`,()=>{
  const toggle=()=>{const codex=$('#ai-provider').value==='codex';$('#codex-settings').hidden=!codex;$('#api-settings').hidden=codex;$('#clear-key').hidden=codex;};
  $('#ai-provider').onchange=toggle;toggle();
  const save=async()=>{state.settings=await api('/settings',{method:'PUT',body:{provider:$('#ai-provider').value,codex_model:$('#codex-model').value,codex_effort:$('#codex-effort').value,model:$('#model-name').value,base_url:$('#base-url').value,api_key:$('#api-key').value||null,web_search:$('#web-search').checked}});if($('#api-key'))$('#api-key').value='';renderSidebar();};
  $('#settings-form').onsubmit=e=>{e.preventDefault();busy($('button[type=submit]',e.target),async()=>{await save();$('#modal').close();if(state.view==='reader')renderAssistant();toast('模型设置已保存');});};
  $('#test-model').onclick=e=>busy(e.currentTarget,async()=>{await save();const r=await api('/settings/test',{method:'POST'});if($('#settings-feedback'))$('#settings-feedback').innerHTML=`<div class="notice info">${esc(r.message)} · ${esc(r.model)} · ${r.provider==='codex'?'Codex 账户额度':'API 额度'}</div>`;});
  $('#refresh-codex').onclick=e=>busy(e.currentTarget,async()=>{const c=await api('/codex/status');state.settings.codex=c;if(state.settings.provider==='codex')state.settings.ready=c.ready;renderSidebar();if($('#codex-status'))$('#codex-status').textContent=c.message;const select=$('#codex-model');if(select&&c.models?.length){const value=select.value;select.innerHTML='<option value="">跟随本机 Codex 默认模型</option>'+c.models.map(m=>`<option value="${esc(m.id)}">${esc(m.name)}</option>`).join('');select.value=value;}});
  $('#clear-key').onclick=e=>busy(e.currentTarget,async()=>{state.settings=await api('/settings',{method:'PUT',body:{model:$('#model-name').value,base_url:$('#base-url').value,web_search:$('#web-search').checked,clear_key:true}});renderSidebar();toast(state.settings.has_key?'进程密钥已清除；环境变量密钥仍有效。':'本次密钥已清除');});});
}
function ideaModal({idea=null,quote='',paragraphId=null,paperId=null,selectionId=null,body=''}={}){
 let draft=null;try{draft=JSON.parse(localStorage.getItem('yannian-idea-draft')||localStorage.getItem('yanji-idea-draft')||'null');}catch{}
 const data=idea||((!quote&&!body&&draft)?draft:{title:'',body,quote,paragraph_id:paragraphId,paper_id:paperId,selection_id:selectionId,project_id:state.projectId||state.paper?.project_ids?.[0]||null,status:'spark'});
 modal(idea?'继续打磨这个想法':'留住刚才的那个想法',`<form id="idea-form"><label class="field">给这个想法起个名字<input id="idea-title" required maxlength="300" value="${esc(data.title)}" placeholder="如果把……用在……，会怎样？"></label><label class="field">你的想法<textarea id="idea-body" required rows="7" placeholder="问题是什么？你准备改变哪一处？为什么可能有效？">${esc(data.body)}</textarea><small id="draft-state">新想法的草稿会自动保存在此浏览器中。</small></label><div class="form-row"><label class="field">研究项目<select id="idea-project">${projectOptions(data.project_id||'')}</select></label><label class="field">当前状态<select id="idea-status">${Object.entries(statuses).map(([v,t])=>`<option value="${v}" ${v===data.status?'selected':''}>${t}</option>`).join('')}</select></label></div>${data.quote?`<div class="context-label subtle">触发这个想法的原文</div><blockquote class="context-quote">${esc(data.quote)}</blockquote>`:''}<div class="modal-footer"><button class="button" type="button" data-close>稍后继续</button><button class="button primary" type="submit">保存 idea</button></div></form>`,()=>{
  const read=()=>({title:$('#idea-title').value.trim(),body:$('#idea-body').value.trim(),status:$('#idea-status').value,project_id:$('#idea-project').value||null,
   paper_id:data.paper_id||null,paragraph_id:data.paragraph_id||null,selection_id:data.selection_id||null,quote:data.quote||''});
  $('#idea-form').oninput=()=>{if(!idea){try{localStorage.setItem('yannian-idea-draft',JSON.stringify(read()));$('#draft-state').textContent='草稿已自动保存 · '+new Date().toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'});}catch{$('#draft-state').textContent='浏览器无法保存草稿，请点击保存 idea。';}}};
  $('#idea-form').onsubmit=e=>{e.preventDefault();busy($('button[type=submit]',e.target),async()=>{const saved=await api('/ideas'+(idea?'/'+idea.id:''),{method:idea?'PUT':'POST',body:read()});if(!idea){localStorage.removeItem('yannian-idea-draft');localStorage.removeItem('yanji-idea-draft');}await refreshLibrary();$('#modal').close();toast('idea 已保存，原文关联也已留下。');if(state.view==='ideas')await navigate('ideas');else if(state.view==='idea')await openIdea(saved.id);});};});
}
function renderIdeas(){
 const ideas=state.ideas.filter(i=>(!state.projectId||i.project_id===state.projectId)&&(state.ideaFilter==='all'||i.status===state.ideaFilter));
 $('#main-content').innerHTML=`<div class="page-title"><div class="eyebrow">THOUGHTS IN PROGRESS</div><h1>想法，值得被继续。</h1><p>把阅读时的疑问变成假设，再让证据和实验帮助你判断。</p></div><div class="section-toolbar"><div class="filter-chips">${[['all','全部'],...Object.entries(statuses)].map(([v,t])=>`<button class="chip ${state.ideaFilter===v?'active':''}" data-idea-filter="${v}">${t}</button>`).join('')}</div><button class="button primary" data-action="new-idea">＋ 记一个想法</button></div>${ideas.length?`<div class="idea-grid">${ideas.map(i=>`<article class="idea-card" data-open-idea="${i.id}" role="button" tabindex="0"><span class="status-tag ${i.status}">${statuses[i.status]}</span><h3>${esc(i.title)}</h3><p>${esc(i.body)}</p>${i.quote?`<div class="quote-preview">${esc(i.quote)}</div>`:''}<footer><span>${esc(i.project_name||'未归入项目')}</span><span>${new Date(i.updated_at).toLocaleDateString('zh-CN')}</span></footer></article>`).join('')}</div>`:`<div class="empty-state">${icon('bulb')}<h3>还没有${state.ideaFilter==='all'?'记录的想法':statuses[state.ideaFilter]+'的想法'}</h3><p>不需要先想清楚。记下一个疑问、一种直觉，<br>或者选中论文中让你停下来的那段话。</p><button class="button primary" data-action="new-idea">记下第一个 idea</button></div>`}`;
}
async function openIdea(id){state.ideas=await api('/ideas');state.idea=state.ideas.find(i=>i.id===id);if(!state.idea)throw new Error('idea 不存在');state.analyses=await api('/ideas/'+id+'/analyses');state.view='idea';renderSidebar();renderIdeaDetail();window.scrollTo(0,0);}
function renderIdeaDetail(){
 const i=state.idea;if(!i)return;
 const names={novelty:'相关工作与潜在差异',resources:'可用资源',experiment:'最小验证实验'};
 $('#main-content').innerHTML=`<div class="idea-detail"><div class="reader-toolbar"><button class="button ghost" data-action="back-ideas">${icon('back')} Idea 笔记</button><button class="button" data-action="edit-idea">编辑想法</button></div><span class="status-tag ${i.status}">${statuses[i.status]}</span><h1>${esc(i.title)}</h1><div class="subtle">${esc(i.project_name||'未归入项目')} · ${new Date(i.updated_at).toLocaleDateString('zh-CN')}</div><p class="idea-body">${esc(i.body)}</p>${i.quote?`<div class="idea-source">“${esc(i.quote)}”<div><button class="button small ghost" data-action="idea-source">${esc(i.paper_title||'查看触发原文')} ↗</button></div></div>`:''}<div class="analysis-actions"><button class="button primary" data-analyze="novelty" ${state.analysisBusy?'disabled':''}>${icon('search')} 有没有类似工作</button><button class="button" data-analyze="resources" ${state.analysisBusy?'disabled':''}>${icon('folder')} 找代码与数据集</button><button class="button" data-analyze="experiment" ${state.analysisBusy?'disabled':''}>${icon('spark')} 设计验证实验</button></div><div class="analysis-search"><input type="text" id="analysis-query" aria-label="idea 检索关键词" placeholder="可选：指定英文检索关键词；留空由模型提炼"><label><input type="checkbox" id="analysis-web" ${state.settings.web_search?'checked':''}> 联网核查</label></div><p class="subtle">分析会保留检索关键词、时间与来源。查不到相同工作，只表示当前检索没有发现。</p><div id="analysis-results">${state.analysisBusy?'<div class="loading"><span class="spinner"></span> 正在检索相关工作并分析，报告完成后自动保存…</div>':''}${state.analyses.map(a=>`<section class="analysis-card"><header><span>${names[a.kind]}</span><span>${new Date(a.created_at).toLocaleString('zh-CN')}</span></header><div class="subtle">检索：${esc((a.queries||[]).join(' / '))}</div><div class="message-body">${markdown(a.content)}</div><details><summary class="subtle">来源与候选文献 (${a.sources.length})</summary><div class="resource-links">${a.sources.map(s=>`<a class="source-chip" href="${esc(safeUrl(s.url))}" target="_blank" rel="noopener">${s.type==='candidate'?'候选 · ':''}${esc(s.title)}</a>`).join('')}</div></details></section>`).join('')}</div></div>`;
}
async function analyzeIdea(kind){
 if(state.analysisBusy)return;if(!state.settings.ready){settingsModal();return;}
 const id=state.idea.id;const payload={kind,query:$('#analysis-query').value,web:$('#analysis-web').checked};state.analysisBusy=true;renderIdeaDetail();
 try{const result=await api('/ideas/'+id+'/analyze',{method:'POST',body:payload});if(state.view==='idea'&&state.idea.id===id){state.analyses.unshift(result);}toast('分析报告已保存');}
 catch(e){toast(e.message,true);}finally{state.analysisBusy=false;if(state.view==='idea')renderIdeaDetail();}
}
function englishTitle(p){return /[a-zA-Z]{3}/.test(p.title||'')&&!/[\u4e00-\u9fff]/.test(p.title||'');}
function discoveryPapers(){
 const papers=(state.discover?.papers||[]).filter(p=>(state.venue==='all'||venueMatch(p.venue,state.venue))&&(state.searchLanguage==='all'||englishTitle(p)));
 if(state.searchOrder==='recent')papers.sort((a,b)=>(Number(b.year)||0)-(Number(a.year)||0)||(b.relevance||0)-(a.relevance||0));
 else if(state.searchOrder==='cited')papers.sort((a,b)=>(b.citation_count||0)-(a.citation_count||0));
 return papers;
}
function discoveryProgress(job){
 if(!job)return '';
 const names={queued:'准备中',running:'检索进行中',completed:'本轮已完成',partial:'本轮结束 · 覆盖不完整',failed:'检索未完成',cancelled:'已停止',interrupted:'已中断'};
 const sourceNames={pending:'等待',running:'检索中',verifying:'核验论文网页',completed:'本轮完成',partial:'部分完成',failed:'来源受限 / 失败',cancelled:'已停止',timeout:'超时'};
 const attempts=Object.values(job.sources||{}).reduce((n,s)=>n+(s.attempts||0),0);
 return `<section class="discovery-progress" aria-live="polite"><header><strong>${state.searchBusy?'<span class="spinner"></span> ':''}${names[job.status]||'检索记录'}</strong><span>${Math.floor((job.elapsed_seconds||0)/60)} 分 ${(job.elapsed_seconds||0)%60} 秒${state.searchBusy?' · 正在收集，结果尚未完整':''}</span></header><p>${esc(job.phase||'')}</p><div class="discovery-totals"><span>来源请求 <b>${attempts}</b></span><span>原始记录 <b>${job.raw_count||0}</b></span><span>去重后 <b>${job.papers?.length||0}</b> 篇</span></div><div class="source-grid">${Object.entries(job.sources||{}).map(([name,s])=>`<div class="source-status ${s.status}"><strong>${esc(name)}</strong><span>${sourceNames[s.status]||esc(s.status)}</span><small>${s.count||0} 条 · ${s.pages||0} ${name==='官网补查'?'个网页已检查':'页已返回'}${s.error?'<br>'+esc(s.error):''}</small></div>`).join('')}</div>${job.reading_guide?`<div class="reading-guide"><strong>优先阅读 ${job.reading_guide.prioritized} 篇</strong><p>根据标题与摘要初筛了 ${job.reading_guide.reviewed_candidates} 篇；全部 ${job.papers?.length||0} 篇候选仍可翻页查看。</p><p>${esc(job.reading_guide.coverage||'')}</p></div>`:''}<details id="search-audit" ${state.searchAudit?'open':''}><summary>检索式、来源覆盖与分页记录</summary><p class="subtle">${esc(job.strategy||'')}<br>每个索引、每个检索式最多 ${job.pages_per_query||'—'} 页，每页 ${job.page_size||'—'} 条；合并后的候选全部保留。一次检索不代表穷尽相关论文。</p><div class="filter-chips">${(job.queries||[]).map(q=>`<span class="chip">${esc(q)}</span>`).join('')}</div>${Object.entries(job.sources||{}).map(([name,s])=>`<div class="source-audit"><strong>${esc(name)}</strong>${s.coverage?`<p>${esc(s.coverage)}</p>`:''}${(s.details||[]).map(d=>`<p>${esc(d.query||d.url||'')} ${d.page?'· 第 '+d.page+' 页':''} · ${d.count!==undefined?d.count+' 条 · ':''}${esc(d.reason||({ok:'成功',failed:'失败',verified:'官网元数据已核验',unverified:'未核验通过，未加入结果'}[d.status]||d.status))}</p>`).join('')}</div>`).join('')}</details></section>`;
}
function urlImportMarkup(){
 const d=state.urlImport,r=d.result;
 return `<div class="url-import-heading">${icon('link')}<strong>通过网址导入 PDF</strong><small>下载、保存原文并归入项目</small></div><form id="url-import-form" class="url-import-form" aria-label="通过网址导入 PDF"><label class="url-import-address">PDF 或论文页面网址<input id="url-import-address" aria-label="PDF 或论文页面网址" type="url" required maxlength="3000" placeholder="https://…/paper.pdf 或论文详情页链接" value="${esc(d.url)}" ${d.busy?'disabled':''}></label><label>归入项目<select id="url-import-project" aria-label="网址导入归入项目" ${d.busy?'disabled':''}>${projectOptions(d.projectId,'仅保存到文献库')}</select></label><button class="button primary" id="url-import-submit" ${d.busy?'disabled':''}>${d.busy?'<span class="spinner"></span> 下载解析中…':icon('download')+' 下载并保存'}</button></form><p class="url-import-hint">支持公开 PDF 直链、arXiv、OpenReview 和提供 PDF 的论文详情页。重复文件会复用，归类可在文献库中调整。</p><div class="url-import-feedback" role="status" aria-live="polite">${d.busy?'<p>正在读取链接、下载并保存 PDF，请稍候…</p>':''}${d.error?`<div class="notice">${esc(d.error)}</div>`:''}${r?`<div class="url-import-success"><div><strong>${r.duplicate?'已复用文献库中的 PDF':'PDF 已下载保存'} · ${esc(r.paper.title)}</strong><small>${r.project?'已归入「'+esc(r.project.name)+'」':'已保存在文献库'} · ${r.paper.page_count} 页</small>${r.warning?'<small>'+esc(r.warning)+'</small>':''}</div><button class="button small" data-open-paper="${esc(r.paper.id)}">打开阅读 ↗</button></div>`:''}</div>`;
}
function bindURLImport(){
 if(!$('#url-import-form'))return;
 $('#url-import-address').oninput=e=>{state.urlImport.url=e.target.value;};
 $('#url-import-project').onchange=e=>{state.urlImport.projectId=e.target.value;};
 $('#url-import-form').onsubmit=e=>{e.preventDefault();importPaperURL();};
}
function renderURLImport(){
 const panel=$('#url-import-slot');if(state.view!=='discover'||!panel)return;
 panel.innerHTML=urlImportMarkup();bindURLImport();
}
async function importPaperURL(){
 const d=state.urlImport;if(d.busy)return;
 d.url=$('#url-import-address').value.trim();d.projectId=$('#url-import-project').value;
 if(!d.url)return;
 try{const url=new URL(d.url);if(!['https:','http:'].includes(url.protocol)||url.username||url.password)throw new Error();}
 catch{d.error='请输入完整的公开 PDF 或论文页面网址，例如 https://…/paper.pdf。';renderURLImport();return;}
 d.busy=true;d.error='';d.result=null;renderURLImport();
 try{
  d.result=await api('/papers/from-url',{method:'POST',body:{url:d.url,project_id:d.projectId||null}});
  d.url='';
  await refreshLibrary();
  toast(d.result.duplicate?'已有 PDF 已复用，所选项目归类已保存。':'PDF 已下载保存，可以打开阅读。');
 }catch(e){d.error=d.result?'PDF 已保存，但列表刷新失败；重新打开文献库即可查看。':e.message;}
 finally{d.busy=false;renderURLImport();}
}
function renderDiscover(){
 const result=state.discover,papers=discoveryPapers(),size=24,pages=Math.max(1,Math.ceil(papers.length/size));state.searchPage=Math.max(1,Math.min(state.searchPage,pages));
 const shown=papers.slice((state.searchPage-1)*size,state.searchPage*size);
 $('#main-content').innerHTML=`<div class="page-title"><div class="eyebrow">FOLLOW THE NEXT QUESTION</div><h1>把相关工作，找得更充分。</h1><p>扩展研究术语，翻页检索多个学术索引，再补查论文与会议官网。</p></div><section id="url-import-slot" class="url-import-card">${urlImportMarkup()}</section><form class="discover-search" id="discover-form"><input id="discover-query" aria-label="搜索论文关键词" required value="${esc(state.query)}" ${state.searchBusy?'disabled':''} placeholder="研究问题可用中文输入，例如：医疗领域的 agent 和世界模型"><button class="button primary" ${state.searchBusy?'disabled':''}>${icon('search')} 开始检索</button></form><div class="discovery-options"><label>检索方式 <select id="discovery-depth" class="inline-select" ${state.searchBusy?'disabled':''}><option value="deep" ${state.searchDepth==='deep'?'selected':''}>深入检索 · 多查询与翻页</option><option value="quick" ${state.searchDepth==='quick'?'selected':''}>快速检索 · 各来源第一页</option></select></label><label><input id="discovery-web" type="checkbox" ${state.searchWeb?'checked':''} ${state.searchBusy?'disabled':''}> 补查会议与论文官网</label>${state.searchBusy?'<button class="button small" id="cancel-search">停止并保留已找到的结果</button>':''}</div><p class="subtle">深入检索会用所选模型扩展英文关键词、整理优先阅读的论文；勾选官网补查会进一步联网，使用现有模型连接的额度。通常需要数分钟，按实际进度显示结果。快速检索不调用模型。</p><div class="filter-chips">${[['医疗领域的 agent 和世界模型','医疗 Agent / 世界模型'],['retrieval augmented generation','RAG'],['large language model agents','LLM Agent'],['multimodal reasoning','多模态推理'],['test time scaling','推理与扩展']].map(([q,t])=>`<button class="chip" data-discover-query="${q}" ${state.searchBusy?'disabled':''}>${t} ↗</button>`).join('')}</div>${discoveryProgress(result)}${result?.warnings?.map(w=>`<div class="notice">${esc(w)}</div>`).join('')||''}<div class="section-toolbar"><h2>${result?'候选论文':'AI 研究线索'} ${result?'<small>/ 当前筛选 '+papers.length+'，总计 '+result.papers.length+'</small>':''}</h2><div class="discovery-filters"><select class="inline-select" id="language-filter" aria-label="标题语言"><option value="english" ${state.searchLanguage==='english'?'selected':''}>英文标题</option><option value="all" ${state.searchLanguage==='all'?'selected':''}>全部标题语言</option></select><select class="inline-select" id="venue-filter" aria-label="筛选会议">${['all','NeurIPS','ICML','ICLR','CVPR','ICCV','ACL','EMNLP'].map(v=>`<option value="${v}" ${state.venue===v?'selected':''}>${v==='all'?'全部来源 / 会议':v}</option>`).join('')}</select><select class="inline-select" id="search-order" aria-label="论文排序">${[['relevance','优先阅读 / 综合相关性'],['recent','年份从新到旧'],['cited','引用量（索引提供时）']].map(([v,t])=>`<option value="${v}" ${state.searchOrder===v?'selected':''}>${t}</option>`).join('')}</select></div></div>${result?`<p class="subtle">${esc(result.scope)}<br>标题保留来源原文；英文标题按字符规则筛选。预印本及 OpenReview 评审记录不等于顶会已录用。</p>`:''}<div id="search-results">${shown.length?shown.map(p=>resultMarkup(p,result.papers.indexOf(p))).join(''):state.searchBusy?'<div class="loading"><span class="spinner"></span> 仍在检索，请查看上方进度；尚无符合当前筛选的结果。</div>':`<div class="empty-state">${icon('search')}<h3>${result?'当前筛选下没有结果':'从你正在思考的研究问题开始'}</h3><p>${result?'切换标题语言或会议筛选，或用其他英文术语重新检索。请同时检查来源是否失败。':'中文研究问题会展开成多条英文查询，兼顾不同方法和术语。'}</p></div>`}</div>${papers.length?`<div class="search-pagination"><button class="button small" id="results-prev" ${state.searchPage===1?'disabled':''}>上一页</button><span>第 ${state.searchPage} / ${pages} 页 · 每页 ${size} 篇</span><button class="button small" id="results-next" ${state.searchPage===pages?'disabled':''}>下一页</button></div>`:''}`;
 $('#discover-form').onsubmit=e=>{e.preventDefault();searchPapers($('#discover-query').value);};
 bindURLImport();
 $('#discovery-depth').onchange=e=>state.searchDepth=e.target.value;$('#discovery-web').onchange=e=>state.searchWeb=e.target.checked;
 $('#venue-filter').onchange=e=>{state.venue=e.target.value;state.searchPage=1;renderDiscover();};
 $('#language-filter').onchange=e=>{state.searchLanguage=e.target.value;state.searchPage=1;renderDiscover();};
 $('#search-order').onchange=e=>{state.searchOrder=e.target.value;state.searchPage=1;renderDiscover();};
 if($('#results-prev'))$('#results-prev').onclick=()=>{state.searchPage--;renderDiscover();};
 if($('#results-next'))$('#results-next').onclick=()=>{state.searchPage++;renderDiscover();};
 if($('#search-audit'))$('#search-audit').ontoggle=e=>state.searchAudit=e.target.open;
 if($('#cancel-search'))$('#cancel-search').onclick=e=>busy(e.currentTarget,async()=>{const job=await api('/research/jobs/'+state.searchJob+'/cancel',{method:'POST'});clearTimeout(searchTimer);state.discover=job;state.searchBusy=false;if(state.view==='discover')renderDiscover();});
}
function venueMatch(venue,name){const v=(venue||'').toLowerCase();const words={NeurIPS:['neurips','nips','neural information processing'],ICML:['icml','international conference on machine learning'],ICLR:['iclr','learning representations'],CVPR:['cvpr','computer vision and pattern recognition'],ICCV:['iccv','international conference on computer vision'],ACL:['acl','annual meeting of the association for computational linguistics'],EMNLP:['emnlp','empirical methods in natural language processing']};return (words[name]||[name.toLowerCase()]).some(w=>v.includes(w));}
function resultMarkup(p,index){return `<article class="result-card">${p.reading_priority?`<div class="reading-tag">优先阅读 ${p.reading_priority} · ${esc(p.reading_group)}</div>`:''}<div class="result-meta"><span class="venue">${esc(p.venue||'会议 / 期刊信息待核实')}</span><span>${esc(p.year||'年份待核实')} · ${esc(p.source)}</span></div><div class="result-top"><h3 class="result-title"><a href="${esc(safeUrl(p.url))}" target="_blank" rel="noopener">${esc(p.title)}</a></h3></div><div class="subtle">${esc(p.authors||'作者信息待补充')}</div>${p.reading_reason?`<p class="reading-reason"><strong>相关性初筛</strong> ${esc(p.reading_reason)}</p>`:''}<p class="result-abstract">${esc(p.abstract||'该索引未返回摘要。请打开原文核对研究内容。')}</p><div class="result-footer"><select class="inline-select" id="result-project-${index}" aria-label="收录到哪个项目">${projectOptions(state.projectId||'','选择项目（可选）')}</select><button class="button small" data-collect-result="${index}">＋ 收入文献库</button><button class="button small primary" data-fetch-result="${index}">${icon('download')} 获取 PDF 并阅读</button><a href="${esc(safeUrl(p.url))}" target="_blank" rel="noopener">来源 ↗</a></div></article>`;}
async function pollSearch(jobId,generation){
 if(generation!==state.searchGeneration)return;
 try{
  const job=await api('/research/jobs/'+jobId+'?after='+(state.discover?.revision||0));
  if(generation!==state.searchGeneration)return;
  if(!job.unchanged)state.discover=job;else if(state.discover)state.discover.elapsed_seconds=job.elapsed_seconds;
  state.searchBusy=['queued','running'].includes(job.status);
  if(state.view==='discover'&&!document.activeElement?.closest('#search-results select,#url-import-slot'))renderDiscover();
  if(state.searchBusy)searchTimer=setTimeout(()=>pollSearch(jobId,generation),2500);
 }catch(e){if(generation===state.searchGeneration){toast('暂时无法读取检索进度；后台任务可能仍在进行，正在重试。',true);searchTimer=setTimeout(()=>pollSearch(jobId,generation),7000);}}
}
async function resumeSearch(jobId){
 const generation=++state.searchGeneration;clearTimeout(searchTimer);
 const job=await api('/research/jobs/'+encodeURIComponent(jobId));
 state.searchJob=job.id;state.discover=job;state.query=job.query;state.searchDepth=job.depth;state.searchWeb=job.web;
 state.searchBusy=['queued','running'].includes(job.status);state.view='discover';renderSidebar();renderDiscover();
 if(state.searchBusy)searchTimer=setTimeout(()=>pollSearch(job.id,generation),500);
}
async function searchPapers(query,similarId=null){
 query=query.trim();if(!query)return;if(state.searchBusy){toast('当前检索仍在进行，可先停止并保留结果。');return;}
 const generation=++state.searchGeneration;clearTimeout(searchTimer);state.view='discover';state.query=query;state.searchBusy=true;state.venue='all';state.searchPage=1;state.discover=null;renderSidebar();renderDiscover();
 try{
  const job=await api('/research/jobs',{method:'POST',body:{query,depth:state.searchDepth,web:state.searchWeb,paper_id:similarId}});
  if(generation!==state.searchGeneration)return;
  state.searchJob=job.id;state.discover=job;history.replaceState(null,'',location.pathname+'#search='+job.id);
  if(state.view==='discover')renderDiscover();searchTimer=setTimeout(()=>pollSearch(job.id,generation),500);
 }catch(e){if(generation===state.searchGeneration){state.searchBusy=false;toast(e.message,true);if(state.view==='discover')renderDiscover();}}
}
function paperProjectsModal(){const p=state.paper;modal('这篇论文属于哪些研究项目',`<div class="zotero-list">${state.library.projects.map(project=>`<label class="check-row"><input type="checkbox" name="paper-project" value="${project.id}" ${p.project_ids.includes(project.id)?'checked':''}><span>${esc(project.name)}</span></label>`).join('')}</div><p class="subtle">同一篇论文可以属于多个项目，PDF 只保留一份。</p><div class="modal-footer"><button class="button primary" id="save-projects">保存归类</button></div>`,()=>{$('#save-projects').onclick=e=>busy(e.currentTarget,async()=>{const chosen=$$('input[name=paper-project]:checked').map(x=>x.value);for(const project of state.library.projects){if(chosen.includes(project.id)!==p.project_ids.includes(project.id))await api('/projects/'+project.id+'/papers/'+p.id,{method:chosen.includes(project.id)?'POST':'DELETE'});}state.paper.project_ids=chosen;await refreshLibrary();$('#modal').close();toast('论文归类已更新');});});}
async function organizeModal(){
 modal('把相近的论文放在一起',`<p class="subtle">先生成建议，再选择要应用的归类。已有文献和笔记会保留。</p><div class="filter-chips"><button class="button" id="local-group">本地相似归类</button><button class="button primary" id="ai-group">✧ AI 语义归类</button></div><div id="group-results"><p class="subtle">本地方案按关键词重合匹配已有项目，也会尝试为尚未归类的相似论文新建项目。AI 方案会进一步分析论文研究问题和方法，需要模型连接。</p></div>`,()=>{
  const run=async(button,path)=>busy(button,async()=>{const result=await api(path,{method:'POST'});$('#group-results').innerHTML=`<p class="subtle">${esc(result.method)}</p>${result.suggestions.map((s,i)=>`<label class="check-row"><input type="checkbox" data-assignment="${i}" checked><span>${esc(s.paper_title)}<small>→ ${esc(s.project_name)} · ${esc(s.reason)}</small></span></label>`).join('')}${result.new_groups.map((g,i)=>`<div class="group-proposal"><label class="field checkbox"><input type="checkbox" data-group-check="${i}" checked>创建研究项目</label><input class="name-input" aria-label="新项目名称" data-group-name="${i}" value="${esc(g.name)}"><ul>${g.titles.map(t=>'<li>'+esc(t)+'</li>').join('')}</ul></div>`).join('')}${!result.suggestions.length&&!result.new_groups.length?'<div class="notice info">暂时没有足够明确的归类建议。可以导入更多相关论文，或补充项目描述中的研究关键词。</div>':'<div class="modal-footer"><button class="button primary" id="apply-groups">应用选中的归类</button></div>'}`;
   if($('#apply-groups'))$('#apply-groups').onclick=e=>busy(e.currentTarget,async()=>{const assignments=$$('[data-assignment]:checked').map(x=>result.suggestions[Number(x.dataset.assignment)]).map(s=>({paper_id:s.paper_id,project_id:s.project_id}));const new_groups=$$('[data-group-check]:checked').map(x=>{const n=Number(x.dataset.groupCheck);return {name:$(`[data-group-name="${n}"]`).value,paper_ids:result.new_groups[n].paper_ids};});await api('/organize/apply',{method:'POST',body:{assignments,new_groups}});await refreshLibrary();$('#modal').close();await navigate('library');toast('已应用选中的归类');});});
  $('#local-group').onclick=e=>run(e.currentTarget,'/organize/suggest');$('#ai-group').onclick=e=>{if(!state.settings.ready){settingsModal();return;}run(e.currentTarget,'/organize/ai');};});
}
async function zoteroModal(){
 modal('从本机 Zotero 导入',`<p class="subtle">打开 Zotero → 设置 → 高级，开启「允许本机其他应用程序与 Zotero 通信」。研念会复制选中的条目及本机 PDF，后续在研念中的归类和笔记独立保存。</p><label class="field">导入到项目<select id="zotero-project">${projectOptions(state.projectId||'')}</select></label><div id="zotero-state"><div class="loading"><span class="spinner"></span> 正在连接 Zotero…</div></div><div class="zotero-list" id="zotero-items"></div><div class="modal-footer"><button class="button" id="zotero-more" hidden>加载更多</button><button class="button primary" id="zotero-import" disabled>导入所选</button></div>`);
 let next=0;
 const load=async()=>{const r=await api('/zotero/items?start='+next);next=r.next_start;$('#zotero-items').insertAdjacentHTML('beforeend',r.items.map(i=>`<label class="check-row"><input type="checkbox" name="zotero-key" value="${esc(i.key)}"><span>${esc(i.title)}</span></label>`).join(''));$('#zotero-more').hidden=next===null;$('#zotero-import').disabled=false;$('#zotero-state').innerHTML='<p class="subtle">选择要导入的论文。每次最多 100 条；未下载到本机的 PDF 需要之后附加。</p>';};
 try{const status=await api('/zotero/status');if(!status.connected){$('#zotero-state').innerHTML=`<div class="notice">${esc(status.message)}</div><button class="button" id="zotero-retry">重新检测</button>`;$('#zotero-retry').onclick=zoteroModal;return;}await load();$('#zotero-more').onclick=e=>busy(e.currentTarget,load);$('#zotero-import').onclick=e=>busy(e.currentTarget,async()=>{const keys=$$('input[name=zotero-key]:checked').map(x=>x.value);if(!keys.length)throw new Error('请先选择论文。');const r=await api('/zotero/import',{method:'POST',body:{keys,project_id:$('#zotero-project').value||null}});await refreshLibrary();if(r.warnings.length){$('#zotero-state').innerHTML=`<div class="notice">已处理 ${r.imported.length} 条。<br>${r.warnings.map(esc).join('<br>')}</div>`;}else{$('#modal').close();await navigate('library');toast('已导入 '+r.imported.length+' 条 Zotero 文献');}});}
 catch(e){if($('#zotero-state'))$('#zotero-state').innerHTML=`<div class="error-box">${esc(e.message)}</div>`;}
}
document.addEventListener('click',async event=>{
 const el=event.target.closest('button,[data-open-paper],[data-open-idea]');if(!el)return;
 try{
  if(el.dataset.readerPosition){positionReader(el.dataset.readerPosition);return;}
  if(el.dataset.view)return await navigate(el.dataset.view);
  if(el.dataset.project)return await navigate('library',el.dataset.project);
  if(el.dataset.openPaper)return await openPaper(el.dataset.openPaper);
  if(el.dataset.openIdea)return await openIdea(el.dataset.openIdea);
  if(el.dataset.pdfTool){state.pdfTool=el.dataset.pdfTool;$('#selection-menu').hidden=true;window.getSelection()?.removeAllRanges();$$('[data-pdf-tool]').forEach(b=>b.classList.toggle('active',b.dataset.pdfTool===state.pdfTool));mountPDF();return;}
  if(el.dataset.selectionSource)return await openPDFSelection(el.dataset.selectionSource);
  if(el.dataset.selectionIdea){const selected=state.pdfSelection;if(selected)ideaModal({quote:selected.text||'PDF 第 '+selected.page+' 页的框选区域',paperId:selected.paper_id,paragraphId:selected.paragraph_id,selectionId:selected.id});return;}
  if(el.dataset.readerMode){state.readerMode=el.dataset.readerMode;$('#selection-menu').hidden=true;renderReader();return;}
  if(el.dataset.askPara){selectParagraph(el.dataset.askPara);return;}
  if(el.dataset.pdfPara){selectParagraph(el.dataset.pdfPara);return;}
  if(el.dataset.ideaPara){const p=state.paper.paragraphs.find(p=>p.id===el.dataset.ideaPara);ideaModal({quote:p.text,paragraphId:p.id,paperId:p.paper_id});return;}
  if(el.dataset.originalPara){const p=state.paper.paragraphs.find(p=>p.id===el.dataset.originalPara);state.page=p.page;state.readerMode='original';renderReader();$(`[data-pdf-para="${p.id}"]`)?.classList.add('active');window.scrollTo(0,0);return;}
  if(el.dataset.pageStep){goToPDFPage(state.page+Number(el.dataset.pageStep));return;}
  if(el.dataset.prompt){$('#chat-question').value=el.dataset.prompt;saveChatDraft(el.dataset.prompt);$('#chat-question').focus();return;}
  if(el.dataset.saveMessage){const m=state.messages[Number(el.dataset.saveMessage)];ideaModal({body:m.content,paperId:state.paper.id,paragraphId:state.para?.id||null,quote:state.para?.text||''});return;}
  if(el.dataset.ideaFilter){state.ideaFilter=el.dataset.ideaFilter;renderIdeas();return;}
  if(el.dataset.analyze)return await analyzeIdea(el.dataset.analyze);
  if(el.dataset.discoverQuery)return await searchPapers(el.dataset.discoverQuery);
  if(el.dataset.collectResult!==undefined){return busy(el,async()=>{const n=Number(el.dataset.collectResult),p=state.discover.papers[n];await api('/papers/metadata',{method:'POST',body:{...p,project_id:$('#result-project-'+n).value||null}});await refreshLibrary();toast('已收入文献库；打开文献可自动获取 PDF');});}
  if(el.dataset.fetchResult!==undefined){return busy(el,async()=>{const n=Number(el.dataset.fetchResult),p=state.discover.papers[n];const r=await api('/papers/metadata',{method:'POST',body:{...p,project_id:$('#result-project-'+n).value||null}});await refreshLibrary();await fetchPaperPDF(r.paper);});}
  switch(el.dataset.action){
   case 'fetch-pdf':await fetchPaperPDF(state.paper);break;
   case 'import':importModal();break;case 'attach':importModal(state.paper.id);break;case 'zotero':await zoteroModal();break;
   case 'discover-ai':await navigate('discover');break;case 'project-ideas':await navigate('ideas',state.projectId);break;
   case 'back-library':await navigate('library',state.projectId);break;case 'new-idea':ideaModal();break;case 'back-ideas':await navigate('ideas');break;
   case 'edit-idea':ideaModal({idea:state.idea});break;case 'idea-source':if(state.idea.selection_id)await openPDFSelection(state.idea.selection_id);else if(state.idea.paper_id)await openPaper(state.idea.paper_id,state.idea.paragraph_id);break;
   case 'paper-projects':paperProjectsModal();break;case 'paper-idea':ideaModal({paperId:state.paper.id,paragraphId:state.para?.id||null,quote:state.para?.text||''});break;
   case 'similar':await searchPapers(state.paper.title,state.paper.id);break;
   case 'clear-para':selectParagraph(null);break;case 'new-chat':newChat();break;
   case 'chat-context':chatContextModal();break;
   case 'history':await historyModal();break;
   case 'citation':if(state.paper?.id===el.dataset.paper){const p=state.paper.paragraphs.find(p=>p.id===el.dataset.paragraph);if(p){state.page=p.page;state.readerMode='original';renderReader();$('.pdf-canvas-page')?.scrollIntoView({behavior:'smooth',block:'start'});$(`[data-pdf-para="${p.id}"]`)?.classList.add('active');}}else await openPaper(el.dataset.paper,el.dataset.paragraph);break;
  }
 }catch(e){toast(e.message,true);}
});
document.addEventListener('keydown',e=>{if((e.key==='Enter'||e.key===' ')&&e.target.matches('[data-open-paper],[data-open-idea]')){e.preventDefault();e.target.click();}});
document.addEventListener('mouseup',event=>{
 if(event.target.closest('#selection-menu'))return;
 const menu=$('#selection-menu');if(state.view==='reader'&&state.readerMode==='original'){if(!event.target.closest('.pdf-canvas-page'))menu.hidden=true;return;}menu.hidden=true;if(state.view!=='reader')return;$('#selection-ask').textContent='问选中文字';
 const selection=window.getSelection();if(!selection||selection.isCollapsed||selection.toString().trim().length<3)return;
 let container=selection.getRangeAt(0).commonAncestorContainer;if(container.nodeType===3)container=container.parentElement;
 const para=container.closest?.('[data-para]');if(!para)return;
 const text=selection.toString().trim().slice(0,15000);const rect=selection.getRangeAt(0).getBoundingClientRect();
 menu.hidden=false;menu.style.left=Math.max(80,Math.min(innerWidth-230,rect.left))+'px';menu.style.top=Math.max(8,rect.top-47)+'px';
 $('#selection-ask').onclick=()=>{selectParagraph(para.dataset.para,false);state.selectedText=text;renderAssistant();$('#chat-question').focus();menu.hidden=true;selection.removeAllRanges();};
 $('#selection-idea').onclick=()=>{ideaModal({quote:text,paperId:state.paper.id,paragraphId:para.dataset.para});menu.hidden=true;selection.removeAllRanges();};
});
setupReaderSplitter();
$('#sidebar-toggle').onclick=()=>togglePanel('sidebar');
$('#assistant-toggle').onclick=()=>togglePanel('assistant');
$('#main-pane').addEventListener?.('scroll',()=>{$('#selection-menu').hidden=true;},{passive:true});
applyLayout();
$('#new-project').onclick=newProjectModal;$('#import-button').onclick=()=>importModal();$('#settings-button').onclick=settingsModal;
$('#quick-idea').onclick=()=>ideaModal();$('#organize').onclick=organizeModal;$('#zotero-button').onclick=zoteroModal;
$('.brand').onclick=e=>{e.preventDefault();navigate('library');};
async function boot(){try{const active=readChatStorage('yannian-chat-active',null);if(active){state.chatBusy=true;pollChat(active);}[state.library,state.settings]=await Promise.all([api('/library'),api('/settings')]);renderSidebar();const params=new URLSearchParams(location.hash.slice(1));if(params.get('selection')){await openPDFSelection(params.get('selection'));if(params.get('page')&&Number(params.get('page'))!==state.page)goToPDFPage(params.get('page'));}else if(params.get('paper'))await openPaper(params.get('paper'),params.get('paragraph'),params.get('page'));else if(params.get('search'))await resumeSearch(params.get('search'));else render();}catch(e){$('#main-content').innerHTML=`<div class="error-box">无法打开研究空间：${esc(e.message)}<br>请确认启动程序正在运行，然后刷新页面。</div>`;}}
boot();
