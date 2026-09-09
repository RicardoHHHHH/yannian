// Source-unit checks only: no browser is launched and no network is accessed.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const nodes = new Map();
const storedValues = new Map();
const documentListeners = new Map();
function classes(){const values=new Set();return {toggle(name,on){on=on??!values.has(name);if(on)values.add(name);else values.delete(name);return on;},add(name){values.add(name);},remove(name){values.delete(name);},contains(name){return values.has(name);}};}
function node(selector) {
  if (!nodes.has(selector)) nodes.set(selector, {innerHTML:'',textContent:'',value:'',classList:classes(),style:{},attributes:{},setAttribute(k,v){this.attributes[k]=v;},getAttribute(k){return this.attributes[k];},scrollTop:0,scrollTo(a,b){this.scrollTop=typeof a==='object'?a.top:b;},getBoundingClientRect(){return this.rect||{top:0,left:0,width:800,height:800};},focus(){},scrollIntoView(){},setSelectionRange(){},showModal(){this.open=true;},close(){this.open=false;},querySelector:node,querySelectorAll:()=>[]});
  return nodes.get(selector);
}
const context = vm.createContext({
 document:{querySelector:node,querySelectorAll:()=>[],addEventListener(type,fn){documentListeners.set(type,fn);}},
 location:{origin:'http://127.0.0.1:8765',pathname:'/',hash:''},
 history:{replaceState(){}}, URL, URLSearchParams, console,
 fetch:()=>new Promise(()=>{}), setTimeout,clearTimeout,
 window:{scrollTo(){}}, localStorage:{getItem(k){return storedValues.get(k)||null;},setItem(k,v){storedValues.set(k,v);},removeItem(k){storedValues.delete(k);}}
});
vm.runInContext(fs.readFileSync(path.join(root,'static','assistant.js'),'utf8'),context);
vm.runInContext(fs.readFileSync(path.join(root,'static','app.js'),'utf8'),context);
const run = code => vm.runInContext(code,context);
let passed=0;
function test(name, fn){fn();passed++;console.log('PASS',name);}
test('rename restores legacy layout and prefers newly saved settings',()=>{
 storedValues.set('yanji-layout',JSON.stringify({sidebarCollapsed:true,assistantCollapsed:false}));
 assert.equal(run('readLayoutPreferences().sidebarCollapsed'),true);
 storedValues.set('yannian-layout',JSON.stringify({sidebarCollapsed:false,assistantCollapsed:true}));
 assert.equal(run('readLayoutPreferences().sidebarCollapsed'),false);
 assert.equal(run('readLayoutPreferences().assistantCollapsed'),true);
 storedValues.clear();
});
test('HTML escaping prevents source metadata from becoming markup',()=>{
 assert.equal(run(`esc('<img src=x onerror="bad">')`),'&lt;img src=x onerror=&quot;bad&quot;&gt;');
});
test('unsafe result URLs are not rendered as executable schemes',()=>{
 assert.equal(run(`safeUrl('javascript:alert(1)')`),'#');
 assert.equal(run(`safeUrl('https://arxiv.org/abs/2005.11401')`),'https://arxiv.org/abs/2005.11401');
});
test('empty library offers working import and Zotero actions',()=>{
 run('renderLibrary()');const html=node('#main-content').innerHTML;
 assert.ok(html.includes('data-action="import"')&&html.includes('data-action="zotero"'));
 assert.ok(!html.includes('data-open-paper='));
});
test('paper rows use real IDs and escape paper titles',()=>{
 run(`state.library.papers=[{id:'paper-a',title:'<script>unsafe</script>',authors:'Author',abstract:'abc',source:'upload',has_pdf:true,page_count:2,year:'2026',project_ids:[]}];renderLibrary()`);
 const html=node('#main-content').innerHTML;
 assert.ok(html.includes('data-open-paper="paper-a"'));
 assert.ok(html.includes('&lt;script&gt;unsafe&lt;/script&gt;'));
 assert.ok(!html.includes('<script>unsafe'));
});
test('conference filters do not classify arXiv as accepted',()=>{
 assert.equal(run(`venueMatch('arXiv · 预印本','NeurIPS')`),false);
 assert.equal(run(`venueMatch('Advances in Neural Information Processing Systems','NeurIPS')`),true);
});
test('paragraph questions and idea actions preserve paragraph IDs',()=>{
 const markup=run(`paragraphMarkup([{id:'para-a',paper_id:'paper-a',page:2,ordinal:4,text:'A test paragraph'}])`);
 assert.ok(markup.includes('data-ask-para="para-a"'));
 assert.ok(markup.includes('data-idea-para="para-a"'));
 assert.ok(markup.includes('PAGE 02'));
});
test('idea states render the saved body and source excerpt',()=>{
 run(`state.view='ideas';state.ideas=[{id:'idea-a',title:'Saved idea',body:'Saved hypothesis',status:'testing',quote:'Source excerpt',updated_at:'2026-09-07T00:00:00Z'}];renderIdeas()`);
 const html=node('#main-content').innerHTML;
 assert.ok(html.includes('Saved hypothesis')&&html.includes('Source excerpt'));
 assert.ok(html.includes('验证中'));
});
test('Codex login displays connected even with no API key',()=>{
 run(`state.settings={provider:'codex',ready:true,has_key:false,codex:{ready:true,message:'已连接',models:[]}};renderSidebar()`);
 assert.equal(node('#connection-state').textContent,'● Codex 已连接');
});
test('Codex settings hide API credentials and expose login detection',()=>{
 node('#ai-provider').value='codex';
 run('settingsModal()');
 assert.equal(node('#api-settings').hidden,true);
 assert.equal(node('#codex-settings').hidden,false);
 assert.ok(node('#modal-content').innerHTML.includes('codex login'));
 assert.ok(node('#modal-content').innerHTML.includes('共享你现有账户的 Codex 用量限制'));
 node('#ai-provider').value='api';node('#ai-provider').onchange();
 assert.equal(node('#api-settings').hidden,false);
 assert.equal(node('#codex-settings').hidden,true);
});
test('discovery keeps English source titles and filters Chinese titles explicitly',()=>{
 assert.equal(run(`englishTitle({title:'Medical World Models for Clinical Decision Making'})`),true);
 assert.equal(run(`englishTitle({title:'医疗世界模型综述'})`),false);
 run(`state.discover={papers:[{title:'Medical Agents',year:'2024'},{title:'医疗智能体',year:'2026'}]};state.venue='all';state.searchLanguage='english';state.searchOrder='recent'`);
 assert.equal(run('discoveryPapers().length'),1);
 run(`state.searchLanguage='all'`);assert.equal(run('discoveryPapers().length'),2);
});
test('DeepSeek preset fills protocol and models without reusing another service key',()=>{
 run(`state.settings={provider:'api',api_preset:'openai',model:'api-old',base_url:'https://old.example/v1',api_presets:{deepseek:{name:'DeepSeek',model:'deepseek-v4-flash',base_url:'https://api.deepseek.com',protocol:'chat_completions',images:false,reasoning:true,models:['deepseek-v4-flash']}}}`);
 node('#ai-provider').value='api';run('settingsModal()');
 node('#api-key').value='old-service-key';node('#ai-provider').value='deepseek';node('#ai-provider').onchange();
 assert.equal(node('#api-key').value,'');assert.equal(node('#base-url').value,'https://api.deepseek.com');
 assert.equal(node('#api-protocol').value,'chat_completions');assert.equal(node('#model-name').value,'deepseek-v4-flash');
 assert.equal(node('#web-search').disabled,true);assert.equal(node('#api-images').checked,false);
 assert.ok(node('#api-capability-note').textContent.includes('视觉模型'));
 node('#ai-provider').value='codex';node('#ai-provider').onchange();
 assert.equal(node('#model-name').disabled,true);assert.equal(node('#web-search').disabled,false);
});
test('API model preferences are isolated by destination and follow DeepSeek thinking capabilities',()=>{
 run(`state.settings={provider:'api',api_preset:'deepseek',base_url:'https://api.deepseek.com',api_protocol:'chat_completions',model:'deepseek-v4-flash'};writeChatStorage('yannian-chat-options',{[chatOptionsKey()]:{model:'deepseek-v4-pro',effort:'max'}})`);
 assert.equal(run('chatChoices().model'),'deepseek-v4-pro');
 assert.equal(run('chatChoices().effort'),'max');assert.equal(run(`chatChoices().efforts.includes('medium')`),false);
 run(`state.settings={provider:'api',api_preset:'custom',base_url:'https://other.example/v1',api_protocol:'chat_completions',model:'other-model',api_capabilities:{efforts:[]}}`);
 assert.equal(run('chatChoices().model'),'other-model');assert.equal(run('chatChoices().effort'),'');assert.equal(run('chatChoices().efforts.length'),0);
 storedValues.delete('yannian-chat-options');
 run(`state.settings={provider:'codex',ready:true,has_key:false,codex:{models:[]}}`);
});
test('discovery pagination preserves papers after the first 24 instead of truncating the search',()=>{
 run(`state.searchLanguage='english';state.searchPage=1;state.searchBusy=false;state.searchOrder='relevance';state.discover={status:'completed',papers:Array.from({length:60},(_,i)=>({title:'Medical Agent Paper '+i,url:'https://example.org/'+i,year:'2025',source:'arXiv',abstract:'Evidence',venue:'arXiv'})),sources:{},queries:['medical agents']};renderDiscover()`);
 assert.ok(node('#main-content').innerHTML.includes('当前筛选 60，总计 60'));
 assert.equal((node('#main-content').innerHTML.match(/class="result-card"/g)||[]).length,24);
 node('#results-next').onclick();
 assert.ok(node('#main-content').innerHTML.includes('Medical Agent Paper 47'));
 node('#results-next').onclick();
 assert.ok(node('#main-content').innerHTML.includes('Medical Agent Paper 59'));
});
test('running and failed sources are not presented as exhaustive completion',()=>{
 run(`state.searchBusy=true`);
 const html=run(`discoveryProgress({status:'running',phase:'Still searching',papers:[],sources:{arXiv:{status:'running',pages:1,count:50},Crossref:{status:'failed',pages:0,count:0}},queries:['medical agents']})`);
 assert.ok(html.includes('结果尚未完整')&&html.includes('来源受限 / 失败'));
 assert.ok(!html.includes('本轮已完成'));
});
test('reading guide retains original titles and escapes model supplied reasons',()=>{
 const html=run(`resultMarkup({title:'EHRWorld: A World Model',url:'https://arxiv.org/abs/2602.03569',source:'arXiv',reading_priority:1,reading_group:'医疗世界模型',reading_reason:'<img src=x onerror=bad>'},0)`);
 assert.ok(html.includes('EHRWorld: A World Model')&&html.includes('医疗世界模型'));
 assert.ok(html.includes('&lt;img src=x onerror=bad&gt;')&&!html.includes('<img src=x'));
 const progress=run(`discoveryProgress({status:'completed',papers:[{},{}],reading_guide:{prioritized:1,reviewed_candidates:2,coverage:'<script>bad</script>'}})`);
 assert.ok(progress.includes('优先阅读 1 篇')&&progress.includes('全部 2 篇候选'));
 assert.ok(!progress.includes('<script>'));
});
test('every discovery source offers automatic PDF acquisition including DOI-only records',()=>{
 const html=run(`resultMarkup({title:'A medical research article',url:'https://doi.org/10.123/example',source:'Crossref'},7)`);
 assert.ok(html.includes('data-fetch-result="7"')&&html.includes('获取 PDF 并阅读'));
});

test('URL imports show measured download progress and distinguish parsing from transfer',()=>{
 run(`state.urlImport.progress={phase:'downloading',downloaded_bytes:5000000,total_bytes:10000000,bytes_per_second:250000,elapsed_seconds:20,parallel:true,source:'<script>bad</script>'}`);
 const downloading=run('urlImportProgressMarkup()');
 assert.ok(downloading.includes('5.0 / 10.0 MB')&&downloading.includes('250 KB/s')&&downloading.includes('20 秒'));
 assert.ok(downloading.includes('value="5000000"')&&!downloading.includes('<script>'));
 run(`state.urlImport.progress={phase:'parsing',downloaded_bytes:10000000,total_bytes:10000000,elapsed_seconds:40}`);
 assert.ok(run('urlImportProgressMarkup()').includes('PDF 已下载，正在解析并保存'));
 assert.ok(!run('urlImportProgressMarkup()').includes('<progress'));
});
test('metadata reader offers automatic download and saved PDF shows provenance',()=>{
 run(`state.paper={id:'paper-a',title:'Selected paper',source:'Crossref',has_pdf:false,paragraphs:[]};renderReader()`);
 assert.ok(node('#main-content').innerHTML.includes('data-action="fetch-pdf"'));
 assert.ok(!node('#main-content').innerHTML.includes('需要先上传 PDF'));
 run(`state.paper.has_pdf=true;state.paper.pdf_origin='https://arxiv.org/pdf/2602.03569';state.paper.pdf_fetched_at='2026-09-07T00:00:00Z';state.readerMode='paragraphs';renderReader()`);
 assert.ok(node('#main-content').innerHTML.includes('arXiv 版本'));
 assert.ok(!node('#main-content').innerHTML.includes('data-action="fetch-pdf"'));
});
async function checkPDFSelectionFlow(){
 run(`window.innerWidth=1400;window.innerHeight=900;window.getSelection=()=>({removeAllRanges(){}});window.pdfMountCount=0;window.YannianPDF={mount(args){window.lastPDFMount=args;window.pdfMountCount++},clear(){},goToPage(page,rects){window.lastPDFJump={page,rects};return true;}};markdown=x=>esc(x);
 const fixturePaper={id:'selected-paper',title:'PDF fixture',source:'arXiv',has_pdf:true,page_count:2,paragraphs:[{id:'selected-para',paper_id:'selected-paper',page:2,ordinal:1,text:'A selected caption',bbox:'[60,80,300,300]',display_bbox:[.1,.1,.5,.375]}],page_sizes:[[600,800],[600,800]],project_ids:[]};
 const fixtureSelection={id:'selection-a',paper_id:'selected-paper',paragraph_id:'selected-para',page:2,kind:'region',text:'A selected caption',rects:[[.1,.1,.5,.375]],image_url:'/api/selections/selection-a/image.png'};
 const fixtureRun={id:'run-a',message_id:'message-a',paper_id:'selected-paper',status:'completed',content:'Figure explanation',conversation_id:'conv-a',citations:[{type:'selection',selection_id:'selection-a',page:2}]};
 api=async(path,options={})=>{if(path==='/papers/selected-paper')return fixturePaper;if(path.endsWith('/selections'))return fixtureSelection;if(path==='/chat/runs'){window.sentPDFQuestion=options.body;return fixtureRun;}if(path==='/chat/runs/run-a')return fixtureRun;if(path==='/conversations/conv-a')return {conversation:{id:'conv-a',paper_id:'selected-paper'},messages:[{role:'assistant',...fixtureRun}]};throw new Error(path)};
 state.settings.ready=true;state.chatBusy=false;state.readerMode='paragraphs';`);
 await run(`openPaper('selected-paper')`);
 assert.equal(run('state.readerMode'),'original');
 assert.ok(node('#main-content').innerHTML.includes('id="pdf-reader"')&&!node('#main-content').innerHTML.includes('class="paragraphs"'));
 assert.ok(node('#main-content').innerHTML.includes('框选图片/区域'));
 const mountCount=run('window.pdfMountCount');
 node('#chat-question').value='An unfinished question';
 run(`window.lastPDFMount.onPageChange(2)`);
 assert.equal(run('state.page'),2);assert.equal(node('#pdf-page-input').value,2);
 assert.equal(node('[data-page-step="1"]').disabled,true);
 assert.equal(node('#chat-question').value,'An unfinished question');
 run(`goToPDFPage(-10)`);assert.equal(run('window.lastPDFJump.page'),1);
 run(`goToPDFPage(999)`);assert.equal(run('window.lastPDFJump.page'),2);
 assert.equal(run('window.pdfMountCount'),mountCount,'Page changes must scroll within the existing reader.');
 passed++;console.log('PASS continuous scrolling updates page controls without remounting or losing the question draft');
 run(`showPDFSelectionMenu({kind:'region',rects:[[.1,.1,.5,.375]],text:'',page:2},{x:300,y:200})`);
 await node('#selection-ask').onclick();
 assert.ok(node('#assistant-panel').innerHTML.includes('/api/selections/selection-a/image.png'));
 assert.equal(run('state.pdfSelection.id'),'selection-a');
 run(`window.lastPDFMount.onPageChange(1);mountPDF()`);
 assert.equal(run('window.lastPDFMount.selection.page'),2,'Scrolling must preserve the selection on its original page.');
 node('#chat-question').value='Explain this figure';node('#include-page').checked=false;
 await run('sendChat()');
 assert.equal(run('window.sentPDFQuestion.selection_id'),'selection-a');
 assert.equal(run('window.sentPDFQuestion.page'),2);
 assert.ok(node('#assistant-panel').innerHTML.includes('data-selection-source="selection-a"'));
 passed++;console.log('PASS default PDF reader, region selection, preview, correct-page chat payload and source return');
 await run(`openPaper('selected-paper','selected-para',1)`);
 assert.equal(run('state.page'),1);
 assert.equal(run('window.lastPDFMount.selection.page'),2);
 passed++;console.log('PASS restoring a reading page preserves a paragraph anchor on another page');
}
async function checkWorkspaceControls(){
 run(`state.view='reader';state.layout={sidebarCollapsed:false,assistantCollapsed:false};applyLayout()`);
 node('#chat-question').value='Keep this question';
 run(`togglePanel('sidebar');togglePanel('assistant')`);
 assert.equal(node('#navigation-sidebar').hidden,true);
 assert.equal(node('#workspace').classList.contains('assistant-collapsed'),true);
 assert.equal(node('#assistant-toggle').hidden,false);
 assert.equal(node('#assistant-toggle').getAttribute('aria-expanded'),'false');
 assert.equal(run('readLayoutPreferences().sidebarCollapsed'),true);
 run(`togglePanel('sidebar');togglePanel('assistant')`);
 assert.equal(node('#navigation-sidebar').hidden,false);
 assert.equal(node('#workspace').classList.contains('assistant-collapsed'),false);
 assert.equal(node('#chat-question').value,'Keep this question');
 passed++;console.log('PASS independent sidebar toggles, visible reopen controls, persistent layout and preserved question');

 node('#main-pane').scrollTop=120;node('#main-pane').rect={top:100};
 node('#pdf-reader').rect={top:550};node('.topbar').offsetHeight=73;
 node('.pdf-scroll').scrollTop=5432;
 run(`positionReader('top')`);assert.equal(node('#main-pane').scrollTop,489);
 assert.equal(node('.pdf-scroll').scrollTop,5432);
 run(`positionReader('info')`);assert.equal(node('#main-pane').scrollTop,0);
 assert.equal(node('.pdf-scroll').scrollTop,5432);
 passed++;console.log('PASS outer reader positioning does not change the PDF page scroll position');

 run(`state.view='discover';state.searchBusy=false;state.discover=null;state.library={papers:[],projects:[{id:'project-url',name:'Medical agents',color:'#54786a'}],idea_count:0};state.urlImport={url:'',projectId:'project-url',busy:false,result:null,error:''};renderDiscover();
 api=async(path,options={})=>{if(path==='/papers/from-url'){window.urlImportPayload=options.body;return {paper:{id:'url-paper',title:'A downloaded paper',page_count:11},project:{id:'project-url',name:'Medical agents'},duplicate:true};}if(path==='/library')return state.library;throw new Error(path)};`);
 assert.ok(node('#main-content').innerHTML.includes('url-import-form'));
 const discoveryHTML=node('#main-content').innerHTML;
 node('#url-import-address').value='https://papers.example/paper.pdf';node('#url-import-project').value='project-url';
 await run('importPaperURL()');
 assert.equal(run('window.urlImportPayload.project_id'),'project-url');
 assert.equal(run('window.urlImportPayload.url'),'https://papers.example/paper.pdf');
 assert.equal(run('state.urlImport.busy'),false);
 assert.ok(node('#url-import-slot').innerHTML.includes('已复用文献库中的 PDF'));
 assert.ok(node('#url-import-slot').innerHTML.includes('已归入「Medical agents」'));
 assert.ok(node('#url-import-slot').innerHTML.includes('data-open-paper="url-paper"'));
 assert.equal(node('#main-content').innerHTML,discoveryHTML,'Downloading a URL must not reset the search results.');
 passed++;console.log('PASS URL import submits project, displays duplicate/saved state, and preserves discovery results');

 node('#url-import-address').value='https://papers.example/fail.pdf';
 run(`api=async()=>{throw new Error('Download unavailable')}`);
 await run('importPaperURL()');
 assert.equal(run('state.urlImport.busy'),false);
 assert.equal(run('state.urlImport.url'),'https://papers.example/fail.pdf');
 assert.ok(node('#url-import-slot').innerHTML.includes('Download unavailable'));
 passed++;console.log('PASS failed URL imports retain the URL and allow retry');
}
async function checkChatControls(){
 run(`state.view='reader';state.chatBusy=false;state.chatRun=null;state.messages=[];state.conversationId='conv-a';state.chatRenderedKey=null;
 state.settings={ready:true,provider:'codex',codex_model:'model-a',codex_effort:'medium',codex:{models:[{id:'model-a',name:'Model A',efforts:['low','high'],default_effort:'low',is_default:true},{id:'model-b',name:'Model B',efforts:['medium','xhigh'],default_effort:'medium'}]}};
 state.layout={sidebarCollapsed:false,assistantCollapsed:false,assistantRatio:.34};
 window.pdfMountCount=0;saveChatDraft('Keep my next question');renderAssistant();`);
 const source=node('#assistant-panel').innerHTML;
 assert.ok(source.includes('chat-model')&&source.includes('chat-effort'));
 assert.ok(source.includes('Keep my next question'));
 assert.equal(run('chatChoices().effort'),'low','Unsupported effort must fall back to the selected model default.');
 node('#chat-model').value='model-b';node('#chat-effort').value='high';
 node('#chat-model').onchange();
 assert.equal(run('chatChoices().model'),'model-b');
 assert.equal(run('chatChoices().effort'),'medium');
 assert.equal(run(`readChatStorage('yannian-chat-drafts')[chatKey()]`),'Keep my next question');
 passed++;console.log('PASS in-panel model choices respect supported efforts and preserve next-question drafts');

 node('#workspace').rect={left:200,width:1000,top:0};
 node('#workspace').style.setProperty=function(k,v){this[k]=v;};
 const beforePage=run('state.page');node('#chat-question').value='Unchanged while dragging';
 node('#reader-splitter').onpointerdown({button:0,pointerId:7,preventDefault(){}});
 node('#reader-splitter').onpointermove({pointerId:7,clientX:750});
 node('#reader-splitter').onpointerup({pointerId:7});
 assert.equal(run('readLayoutPreferences().assistantRatio'),.45);
 assert.equal(node('#workspace').style['--assistant-width'],'45%');
 assert.equal(node('#chat-question').value,'Unchanged while dragging');
 assert.equal(run('state.page'),beforePage);assert.equal(run('window.pdfMountCount'),0);
 node('#reader-splitter').onkeydown({key:'ArrowLeft',preventDefault(){}});
 assert.equal(Math.round(run('state.layout.assistantRatio')*100),47);
 node('#reader-splitter').ondblclick();assert.equal(run('state.layout.assistantRatio'),.34);
 assert.equal(run('clampAssistantRatio(Infinity)'),.34);
 assert.equal(run('clampAssistantRatio(999)'),.7);
 passed++;console.log('PASS pointer and keyboard resizing persists width without remounting PDF or discarding draft');

 node('#messages').scrollHeight=2500;node('#messages').clientHeight=500;node('#messages').scrollTop=180;
 run(`state.messages=[{id:'answer-a',role:'assistant',content:'Streaming answer',status:'running'}];renderChatMessages()`);
 assert.equal(node('#messages').scrollTop,180);assert.equal(node('#chat-to-bottom').hidden,false);
 node('#messages').scrollTop=1990;run('renderChatMessages()');
 assert.equal(node('#messages').scrollTop,2500);
 passed++;console.log('PASS live answers preserve history scroll and follow new output only near the bottom');

 run(`state.conversationId='conv-a';state.messages=[{id:'answer-a',role:'assistant',content:'Partial answer',status:'running'}];
 state.chatRun={id:'active-a',message_id:'answer-a',paper_id:state.paper.id,conversation_id:'conv-a',status:'running',content:'Partial answer',citations:[]};
 trackedChatId='active-a';state.chatBusy=true;saveChatDraft('Next question');
 api=async(path,options={})=>{window.cancelRequest={path,method:options.method};return {...state.chatRun,status:'stopped'};};`);
 await run('stopChat()');
 assert.equal(run('window.cancelRequest.path'),'/chat/runs/active-a/cancel');
 assert.equal(run('window.cancelRequest.method'),'POST');assert.equal(run('state.chatBusy'),false);
 assert.equal(run('state.messages[0].content'),'Partial answer');
 assert.equal(run(`readChatStorage('yannian-chat-drafts')[chatKey()]`),'Next question');
 assert.ok(node('#assistant-panel').innerHTML.includes('已停止'));
 passed++;console.log('PASS stop calls backend cancellation and keeps the partial answer and next draft');

 run(`state.chatBusy=false;state.pdfSelection=null;state.conversationId='conv-a';selectParagraph('selected-para',false)`);
 assert.equal(run('state.conversationId'),'conv-a');assert.equal(run('state.messages.length'),1);
 passed++;console.log('PASS changing paragraph context continues the current conversation');

 run(`api=(path)=>path.endsWith('old-run')?new Promise(resolve=>window.finishOldPoll=resolve):Promise.resolve({id:'new-run',message_id:'new-answer',status:'completed',content:'New answer',paper_id:state.paper.id,conversation_id:'conv-a',citations:[]});window.oldPoll=pollChat('old-run');`);
 await run(`pollChat('new-run')`);
 run(`window.finishOldPoll({id:'old-run',status:'running',content:'Stale answer',paper_id:state.paper.id,conversation_id:'conv-a'})`);
 await run('window.oldPoll');
 assert.equal(run('state.chatRun.id'),'new-run');assert.equal(run('state.chatBusy'),false);
 passed++;console.log('PASS late polling results cannot overwrite a newer conversation turn');

 run(`state.pdfSelection={id:'figure-selection',kind:'region',page:2,text:'MESSY FIGURE LABELS',image_url:'/api/selections/figure-selection/image.png'};state.selectedText='MESSY FIGURE LABELS';`);
 const contextMarkup=run('focusedSelectionMarkup()');
 assert.ok(contextMarkup.includes('<img')&&contextMarkup.includes('/api/selections/figure-selection/image.png'));
 assert.ok(!contextMarkup.includes('context-quote'));
 assert.ok(contextMarkup.includes('<details class="selection-extracted">')&&!contextMarkup.includes('selection-extracted" open'));
 const userMarkup=run(`userMessageMarkup({content:'Explain\\nMESSY FIGURE LABELS',display_content:'Explain this figure',attachments:[{selection_id:'figure-selection',kind:'region',page:2,text:'MESSY FIGURE LABELS'}]})`);
 assert.ok(userMarkup.includes('Explain this figure')&&userMarkup.includes('<img'));
 assert.ok(userMarkup.includes('selection-thumbnail'));
 assert.ok(userMarkup.indexOf('<img')<userMarkup.indexOf('class="message-body"'),'The thumbnail belongs above the question bubble.');
 assert.ok(!userMarkup.includes('MESSY FIGURE LABELS'));
 passed++;console.log('PASS figure attachments show the original crop and hide extracted layout text by default');
}
async function checkAttachmentLifecycle(){
 run(`state.view='reader';state.messages=[];state.conversationId=null;state.chatBusy=false;state.chatRun=null;
 state.pdfSelection={...fixtureSelection,id:'image-first',text:'AUXILIARY FIGURE TEXT'};state.selectedText=state.pdfSelection.text;renderAssistant();`);
 let html=node('#assistant-panel').innerHTML;
 assert.ok(!html.slice(0,html.indexOf('id="messages"')).includes('<img'),'No image may be pinned above the scrolling conversation.');
 assert.ok(html.includes('class="pending-attachment"'));
 assert.ok(!html.includes('AUXILIARY FIGURE TEXT'));
 node('#chat-question').value='Explain the attached figure';node('#include-page').checked=false;
 run(`api=async()=>{throw new Error('Temporary submission failure')}`);
 await run('sendChat()');
 assert.ok(run('pendingAttachmentMarkup()').includes('image-first'));
 assert.equal(run(`readChatStorage('yannian-chat-drafts')[chatKey()]`),'Explain the attached figure');
 assert.equal(run('state.messages.length'),0);
 passed++;console.log('PASS image preview stays beside the draft, never above messages, and survives failed submission');

 run(`window.attachmentTurn=0;window.attachmentPayloads=[];
 api=async(path,options={})=>{if(path==='/chat/runs'){window.attachmentPayloads.push(options.body);window.attachmentTurn++;return {id:'attachment-run-'+window.attachmentTurn,message_id:'attachment-answer-'+window.attachmentTurn,paper_id:state.paper.id,conversation_id:'attachment-chat',status:'completed',content:'Answer',citations:[]};}if(path.startsWith('/chat/runs/'))return state.chatRun;throw new Error(path);};`);
 await run('sendChat()');
 assert.equal(run('pendingAttachmentMarkup()'),'');
 assert.ok(!node('#assistant-panel').innerHTML.includes('class="pending-attachment"'));
 assert.equal(run('state.pdfSelection.id'),'image-first','Sending must keep the image context for follow-up questions.');
 assert.equal(run('window.attachmentPayloads[0].selection_id'),'image-first');
 node('#chat-question').value='How does that explain the result?';
 await run('sendChat()');
 assert.equal(run('window.attachmentPayloads[1].selection_id'),'image-first');
 assert.equal((run('messageMarkup()').match(/<img /g)||[]).length,1,'Consecutive questions about the same image share one thumbnail.');
 assert.equal(run(`state.messages.filter(m=>m.role==='user').every(m=>m.attachments[0].id==='image-first')`),true,'Presentation must not discard historical attachments.');
 run(`window.restoredMessages=JSON.parse(JSON.stringify(state.messages));for(const m of window.restoredMessages)for(const s of m.attachments||[])delete s.id;
 api=async()=>({conversation:{id:'attachment-chat',paper_id:state.paper.id},messages:window.restoredMessages});`);
 await run(`loadChat('attachment-chat')`);
 assert.equal(run('pendingAttachmentMarkup()'),'');
 assert.equal((run('messageMarkup()').match(/<img /g)||[]).length,1);
 passed++;console.log('PASS accepted images move into messages; follow-ups and restored history keep context without duplicate previews');

 run(`state.pdfSelection={...state.pdfSelection,id:'image-second',page:1};renderAssistant()`);
 assert.ok(run('pendingAttachmentMarkup()').includes('image-second'));
 run(`state.messages.push({role:'user',content:'Compare this image',attachments:[{...state.pdfSelection,selection_id:state.pdfSelection.id}]});renderAssistant()`);
 assert.equal(run('pendingAttachmentMarkup()'),'');
 assert.equal((run('messageMarkup()').match(/<img /g)||[]).length,2);
 run(`newChat();saveChatDraft('Keep this draft')`);
 assert.ok(run('pendingAttachmentMarkup()').includes('image-second'));
 run(`selectParagraph(null,false)`);
 assert.equal(run('pendingAttachmentMarkup()'),'');
 assert.equal(run(`readChatStorage('yannian-chat-drafts')[chatKey()]`),'Keep this draft');
 passed++;console.log('PASS a different selection and new chat get an attachment; removing it preserves the question draft');
}
async function checkPDFEscape(){
 run(`state.view='reader';state.readerMode='original';state.pdfTool='region';state.pdfSelection={...fixtureSelection};state.para=fixturePaper.paragraphs[0];state.selectedText='Caption';state.page=2;
 state.messages=[{role:'user',content:'Saved question',attachments:[{...fixtureSelection}]}];state.conversationId='esc-chat';state.chatBusy=true;state.chatRun={id:'still-running',status:'running'};saveChatDraft('Keep my draft');`);
 node('#selection-menu').hidden=false;node('#modal').open=false;
 let prevented=0;
 const escape=overrides=>documentListeners.get('keydown')({key:'Escape',target:{matches:()=>false},preventDefault(){prevented++;},...overrides});
 node('#modal').open=true;escape();assert.equal(run('state.pdfTool'),'region');
 node('#modal').open=false;escape({isComposing:true});assert.equal(run('state.pdfTool'),'region');
 escape({target:{matches:selector=>selector==='select'}});assert.equal(run('state.pdfTool'),'region');
 escape();
 assert.equal(prevented,1);assert.equal(run('state.pdfTool'),'text');assert.equal(node('#selection-menu').hidden,true);
 assert.equal(run('state.pdfSelection'),null);assert.equal(run('state.para'),null);assert.equal(run('state.selectedText'),'');
 assert.equal(run('window.lastPDFMount.tool'),'text');assert.equal(run('window.lastPDFMount.selection'),null);
 assert.equal(run('state.page'),2);assert.equal(run('state.chatRun.id'),'still-running');assert.equal(run('state.chatBusy'),true);
 assert.equal(run('state.messages[0].attachments[0].id'),'selection-a');
 assert.equal(run(`readChatStorage('yannian-chat-drafts')[chatKey()]`),'Keep my draft');
 escape();assert.equal(prevented,1,'Idle Escape should be left to the browser.');
 passed++;console.log('PASS Escape exits region mode and clears current selection while preserving reading position, draft, history and active answer');

 run(`state.chatBusy=false;state.pdfTool='region';state.pdfSelection=null;
 api=()=>new Promise(resolve=>window.finishSelectionSave=resolve);
 showPDFSelectionMenu({kind:'region',rects:[[.1,.1,.5,.5]],text:'',page:2},{x:100,y:100});`);
 const saving=node('#selection-ask').onclick();
 escape();run(`window.finishSelectionSave(fixtureSelection)`);await saving;
 assert.equal(run('state.pdfSelection'),null);assert.equal(run('state.pdfTool'),'text');
 assert.equal(run(`readChatStorage('yannian-chat-drafts')[chatKey()]`),'Keep my draft');
 assert.equal(run('state.messages.length'),1);
 passed++;console.log('PASS a selection save finishing after Escape cannot reopen the attachment or replace the draft');
}
async function checkReadingEvidence(){
 run(`state.settings={provider:'codex',ready:true,codex_model:'',codex_effort:'low',supports_web_search:true,codex:{models:[{id:'model-a',name:'A',is_default:true,efforts:['low']}]}};
 writeChatStorage('yannian-chat-options',{});state.chatBusy=false;state.chatRun=null;state.messages=[];state.pdfSelection=null;state.conversationId=null;state.view='reader';renderAssistant();saveChatDraft('Keep next question');`);
 assert.equal(run('chatChoices().webMode'),'auto');assert.equal(run('chatChoices().paperScope'),'full');
 assert.ok(node('#assistant-panel').innerHTML.includes('id="chat-web-mode"'));
 assert.ok(node('#assistant-panel').innerHTML.includes('整篇论文'));
 node('#chat-model').value='';node('#chat-effort').value='low';node('#chat-web-mode').value='off';node('#chat-paper-scope').value='focused';
 node('#chat-web-mode').onchange();
 assert.equal(run('chatChoices().webMode'),'off');assert.equal(run('chatChoices().paperScope'),'focused');
 assert.equal(run(`readChatStorage('yannian-chat-drafts')[chatKey()]`),'Keep next question');
 passed++;console.log('PASS default full-document/auto-search controls persist explicit preferences without losing drafts');
 node('#chat-question').value='联网搜寻细节';node('#include-page').checked=false;
 run(`api=async(path,options={})=>{if(path==='/chat/runs'){window.evidencePayload=options.body;return {id:'evidence-run',message_id:'evidence-answer',paper_id:state.paper.id,conversation_id:'evidence-chat',status:'completed',content:'Answer',citations:[]};}return state.chatRun;};`);
 await run('sendChat()');
 assert.equal(run('window.evidencePayload.web_mode'),'off');assert.equal(run('window.evidencePayload.paper_scope'),'focused');
 run(`window.evidence={document:{full_text:true,included_blocks:267,total_blocks:267,included_pages:Array.from({length:12},(_,i)=>i+1),total_pages:12,total_chars:46943,included_chars:46943},network:{status:'searched',searched:true}};state.messages=[{role:'assistant',content:'Answer',context_info:window.evidence}];renderChatMessages();chatContextModal();`);
 assert.ok(node('#messages').innerHTML.includes('全文文本 267/267 段'));
 assert.ok(node('#messages').innerHTML.includes('已调用联网工具'));
 assert.ok(node('#modal-content').innerHTML.includes('12 / 12 页'));
 run(`window.evidence.document.full_text=false;window.evidence.network={status:'not_observed',searched:false}`);
 assert.ok(run('chatEvidenceLabel(window.evidence)').includes('部分原文'));
 assert.ok(run('chatEvidenceLabel(window.evidence)').includes('未检测到联网记录'));
 passed++;console.log('PASS request carries explicit scope/network and messages/context dialog display measured evidence coverage');
 run(`state.settings.supports_web_search=false;state.chatBusy=false;renderAssistant();api=async()=>{throw new Error('当前模型接口没有联网工具')};`);
 assert.ok(node('#assistant-panel').innerHTML.includes('当前接口无联网工具'));
 node('#chat-question').value='Please search online';const count=run('state.messages.length');
 await run('sendChat()');
 assert.equal(run('state.messages.length'),count);
 assert.equal(run(`readChatStorage('yannian-chat-drafts')[chatKey()]`),'Please search online');
 passed++;console.log('PASS unsupported-network requests retain the question instead of silently answering offline');
}
checkPDFSelectionFlow().then(checkWorkspaceControls).then(checkChatControls).then(checkAttachmentLifecycle).then(checkPDFEscape).then(checkReadingEvidence).then(()=>console.log(`${passed} frontend source-unit checks passed. Visual/browser checks are separate.`)).catch(e=>{console.error(e);process.exitCode=1;});
