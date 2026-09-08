'use strict';
const effortNames={none:'不启用思考',minimal:'轻量思考',low:'快速思考',medium:'标准思考',high:'深入思考',xhigh:'更深入思考',max:'最大思考',ultra:'极致思考'};
let chatTimer;
let trackedChatId=null;
function readChatStorage(key,fallback={}){try{return JSON.parse(localStorage.getItem(key)||'null')||fallback;}catch{return fallback;}}
function writeChatStorage(key,value){try{localStorage.setItem(key,JSON.stringify(value));}catch{}}
function chatKey(){return (state.paper?.id||'')+':'+(state.conversationId||'new');}
function saveChatDraft(value){const drafts=readChatStorage('yannian-chat-drafts');drafts[chatKey()]=value.slice(0,12000);writeChatStorage('yannian-chat-drafts',drafts);}
function rememberConversation(){const saved=readChatStorage('yannian-last-chat');saved[state.paper.id]=state.conversationId;writeChatStorage('yannian-last-chat',saved);}
function chatChoices(){
 const provider=state.settings.provider||'codex',saved=readChatStorage('yannian-chat-options')[provider]||{};
 const models=state.settings.codex?.models||[];
 let model=saved.model??(provider==='codex'?state.settings.codex_model||'':state.settings.model||'');
 if(provider==='codex'&&model&&models.length&&!models.some(m=>m.id===model))model='';
 const selected=models.find(m=>model?m.id===model:m.is_default);
 const efforts=provider==='codex'?(selected?.efforts?.length?selected.efforts:['low','medium','high']):Object.keys(effortNames);
 let effort=saved.effort??(provider==='codex'?state.settings.codex_effort||'medium':'');
 if(effort&&!efforts.includes(effort))effort=selected?.default_effort||efforts[0];
 return {provider,models,model,effort,efforts};
}
function saveChatChoices(){
 const provider=state.settings.provider||'codex',saved=readChatStorage('yannian-chat-options');
 saved[provider]={model:$('#chat-model').value.trim(),effort:$('#chat-effort').value};
 writeChatStorage('yannian-chat-options',saved);renderAssistant();
}
function chatControls(){
 const {provider,models,model,effort,efforts}=chatChoices();
 const modelInput=provider==='codex'?`<select id="chat-model" aria-label="对话模型"><option value="">Codex 默认模型</option>${models.map(m=>`<option value="${esc(m.id)}" ${model===m.id?'selected':''}>${esc(m.name)}</option>`).join('')}${model&&!models.some(m=>m.id===model)?`<option selected value="${esc(model)}">${esc(model)}</option>`:''}</select>`:`<input id="chat-model" aria-label="对话模型" value="${esc(model)}" maxlength="120" placeholder="API 模型名称">`;
 return `<div class="chat-controls"><label>模型${modelInput}</label><label>思考深度<select id="chat-effort" aria-label="思考深度">${provider==='api'?'<option value="">模型默认</option>':''}${efforts.map(e=>`<option value="${esc(e)}" ${effort===e?'selected':''}>${esc(effortNames[e]||e)}</option>`).join('')}</select></label><button class="icon-button" id="refresh-chat-models" title="刷新可用模型" aria-label="刷新可用模型">↻</button></div>`;
}
function selectionId(selection){return selection?.selection_id||selection?.id;}
function repeatsImage(selection,previous){return selection.kind==='region'&&(previous?.attachments||[]).some(s=>s.kind==='region'&&selectionId(s)===selectionId(selection));}
function selectionAttachment(selection,{extracted=false,compact=false}={}){
 const id=selection.selection_id||selection.id;
 if(selection.kind!=='region')return `<blockquote class="selected-quote">${esc(selection.text||'')}</blockquote>`;
 const imageURL='/api/selections/'+encodeURIComponent(id)+'/image.png';
 return `<figure class="selection-attachment${compact?' selection-thumbnail':''}"><button data-selection-source="${esc(id)}" title="回到 PDF 查看原图"><img class="selection-preview" src="${imageURL}" alt="第 ${selection.page} 页的图片选区" loading="lazy"></button><figcaption>${compact?'第 '+selection.page+' 页 ↗':'图片选区 · 第 '+selection.page+' 页 · 点击回到原图'}</figcaption></figure>${extracted&&selection.text?`<details class="selection-extracted"><summary>辅助文字（图中文字可能乱序）</summary><pre>${esc(selection.text)}</pre></details>`:''}`;
}
function userMessageMarkup(message,previous=null){
 const attachments=message.attachments||[],images=attachments.filter(s=>s.kind==='region'&&!repeatsImage(s,previous));
 return (images.length?`<div class="message-attachments">${images.map(s=>selectionAttachment(s,{compact:true})).join('')}</div>`:'')+`<div class="message-body">${esc(message.display_content??message.content)}${attachments.filter(s=>s.kind!=='region').map(s=>selectionAttachment(s)).join('')}</div>`;
}
function pendingAttachmentMarkup(){
 const selection=state.pdfSelection,previous=[...state.messages].reverse().find(m=>m.role==='user');
 if(selection?.kind!=='region'||repeatsImage(selection,previous))return '';
 return `<div class="pending-attachment" aria-label="待发送的图片附件"><button class="pending-image" data-selection-source="${esc(selectionId(selection))}" title="回到 PDF 查看原图"><img src="/api/selections/${encodeURIComponent(selectionId(selection))}/image.png" alt="第 ${selection.page} 页的图片选区" width="48" height="40"></button><span>图片选区 · 第 ${selection.page} 页<small>随本条问题发送</small></span><button class="icon-button" data-action="clear-para" aria-label="移除待发送图片" title="移除待发送图片" ${state.chatBusy?'disabled':''}>×</button></div>`;
}
function focusedSelectionMarkup(){
 const selection=state.pdfSelection;
 if(selection?.kind==='region')return selectionAttachment(selection,{extracted:true});
 return `<blockquote class="context-quote selection-context-text">${esc(state.selectedText||state.para?.text||state.paper?.title||'')}</blockquote>`;
}
function renderAssistant({bottom=false}={}){
 const focused=document.activeElement?.id==='chat-question',caret=$('#chat-question')?.selectionStart;
 const old=$('#messages'),same=state.chatRenderedKey===chatKey();
 const position=same?old?.scrollTop||0:0,follow=bottom||!same||!old||old.scrollHeight-old.clientHeight-old.scrollTop<70;
 const draft=readChatStorage('yannian-chat-drafts')[chatKey()]||'';
 const pageChecked=same?$('#include-page')?.checked:!state.pdfSelection&&!state.paper?.paragraphs.length&&state.paper?.has_pdf;
 const p=state.para,selection=state.pdfSelection,run=state.chatRun;
 $('#assistant-panel').innerHTML=`<div class="assistant-header"><div class="assistant-title"><span>✧</span> 一起读论文</div><div class="assistant-head-actions"><button class="icon-button" title="查看上下文" aria-label="查看上下文" data-action="chat-context">${icon('book')}</button><button class="icon-button" title="历史对话" aria-label="历史对话" data-action="history">${icon('history')}</button><button class="icon-button" title="新对话" aria-label="新对话" data-action="new-chat">＋</button></div></div>
 <div class="assistant-focus"><button class="assistant-focus-label" data-action="chat-context" title="查看当前选区与对话上下文">${selection?(selection.kind==='region'?'图片选区':'选中文字')+' · 第 '+selection.page+' 页':p?'当前段落 · 第 '+p.page+' 页':'当前论文'}<span> · ${state.messages.filter(m=>m.role==='user').length} 轮对话</span></button>${p||selection?`<button class="icon-button" data-action="clear-para" title="清除当前选区" aria-label="清除当前选区" ${state.chatBusy?'disabled':''}>×</button>`:''}</div>
 <div class="assistant-messages" id="messages" aria-label="对话记录" tabindex="0">${messageMarkup()}</div>
 <button class="chat-to-bottom" id="chat-to-bottom" ${follow?'hidden':''}>回到最新消息 ↓</button>
 <div class="chat-progress" id="chat-progress" role="status">${state.chatBusy?`${run?.phase||'正在提交问题'} · ${esc(run?.model||chatChoices().model||'Codex 默认模型')}`:''}</div>
 <div class="assistant-composer">${chatControls()}<div class="composer-box">${pendingAttachmentMarkup()}<textarea id="chat-question" maxlength="12000" aria-label="向 AI 提问" placeholder="继续追问，或选中另一段文字 / 图片…">${esc(draft)}</textarea><div class="composer-footer"><label><input type="checkbox" id="include-page" ${pageChecked?'checked':''} ${!state.paper?.has_pdf?'disabled':''}> ${selection?'再附选区所在整页':'附上本页图像'}</label>${state.chatBusy?`<button class="button stop-chat small" id="stop-chat" ${!run?.id||run?.status==='stopping'?'disabled':''}>■ ${run?.status==='stopping'?'停止中…':'停止'}</button>`:'<button class="button primary small" id="send-chat">发送 →</button>'}</div></div><p class="composer-hint">${state.settings.ready?(state.chatBusy?'可先写下一问 · 模型与思考深度从下一条生效':'Enter 发送 · Shift + Enter 换行 · 自动保存对话'):'尚未连接模型 · 点击「模型设置」'}</p></div>`;
 state.chatRenderedKey=chatKey();
 $('#chat-question').oninput=e=>saveChatDraft(e.target.value);
 $('#chat-question').onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();sendChat();}};
 if(state.chatBusy)$('#stop-chat').onclick=stopChat;else $('#send-chat').onclick=sendChat;
 $('#chat-model').onchange=saveChatChoices;$('#chat-effort').onchange=saveChatChoices;
 $('#refresh-chat-models').onclick=e=>busy(e.currentTarget,async()=>{if(state.settings.provider==='codex')await api('/codex/status',{method:'GET'});state.settings=await api('/settings');renderAssistant();toast('模型列表已更新');});
 const scroll=$('#messages');scroll.scrollTop=follow?scroll.scrollHeight:position;
 scroll.onscroll=()=>{$('#chat-to-bottom').hidden=scroll.scrollHeight-scroll.clientHeight-scroll.scrollTop<70;};
 $('#chat-to-bottom').onclick=()=>{scroll.scrollTop=scroll.scrollHeight;$('#chat-to-bottom').hidden=true;};
 if(focused){$('#chat-question').focus();if(Number.isInteger(caret))$('#chat-question').setSelectionRange(caret,caret);}
}
function messageMarkup(){
 if(!state.messages.length)return `<div class="assistant-welcome"><div class="spark">✧</div><h3>带着一个问题开始阅读。</h3><p>选中文字、图片或公式，与我逐步讨论。<br>换选区可以接着聊，历史记录自动保存。</p><div class="prompt-suggestions">${['用直观的例子解释这里的核心思想','这一方法依赖哪些关键假设？','实验真的支持作者的结论吗？','这里有哪些值得验证的改进方向？'].map(t=>`<button data-prompt="${esc(t)}">${esc(t)} ↗</button>`).join('')}</div></div>`;
 let previous=null;
 return state.messages.map((m,i)=>{
  const body=m.role==='user'?userMessageMarkup(m,previous):`<div class="message-body">${m.content?markdown(m.content,m.citations||[]):'<span class="spinner"></span> 正在思考…'}</div>`;
  if(m.role==='user')previous=m;
  return `<div class="message ${m.role}" id="chat-message-${i}"><div class="message-label"><span>${m.role==='user'?'你的问题':'研念 · 研究伙伴'}</span>${m.role==='assistant'&&m.content?`<button class="message-action" data-save-message="${i}">保存为笔记</button>`:''}</div>${body}${m.role==='assistant'?`<div class="message-meta">${esc([m.model,effortNames[m.effort],({stopped:'已停止 · 内容未完成',failed:'生成失败',interrupted:'回答中断',running:'生成中',stopping:'正在停止'})[m.status]].filter(Boolean).join(' · '))}</div>${m.error?`<p class="chat-error">${esc(m.error)}</p>`:''}`:''}${m.citations?.length?`<div class="citations">${m.citations.filter(c=>c.type==='selection').map(c=>`<button class="source-chip" data-selection-source="${c.selection_id}">选区 · 第 ${c.page} 页 ↗</button>`).join('')}${m.citations.filter(c=>c.type==='paragraph').map(c=>`<button class="source-chip" data-action="citation" data-paragraph="${c.paragraph_id}" data-paper="${c.paper_id}">${esc(c.label)} · 第 ${c.page} 页 ↗</button>`).join('')}</div>`:''}</div>`;}).join('');
}
function renderChatMessages(){
 if(state.view!=='reader'||state.chatRenderedKey!==chatKey())return;
 const scroll=$('#messages'),position=scroll.scrollTop,follow=scroll.scrollHeight-scroll.clientHeight-position<70;
 scroll.innerHTML=messageMarkup();scroll.scrollTop=follow?scroll.scrollHeight:position;
 $('#chat-to-bottom').hidden=follow;
 $('#chat-progress').textContent=state.chatBusy?`${state.chatRun?.phase||'正在思考'} · ${state.chatRun?.model||''}`:'';
}
function acceptChatRun(run){
 state.chatRun=run;state.chatBusy=['running','stopping'].includes(run.status);
 if(state.paper?.id===run.paper_id&&state.conversationId===run.conversation_id){
  const message=state.messages.find(m=>m.id===run.message_id);
  if(message)Object.assign(message,run,{id:run.message_id,run_id:run.id,role:'assistant'});
  else state.messages.push({...run,id:run.message_id,run_id:run.id,role:'assistant'});
  renderChatMessages();
 }
 if(state.chatBusy)writeChatStorage('yannian-chat-active',run.id);
 else{writeChatStorage('yannian-chat-active',null);clearTimeout(chatTimer);if(state.view==='reader')renderAssistant();}
}
async function pollChat(runId){
 clearTimeout(chatTimer);
 trackedChatId=runId;
 try{const run=await api('/chat/runs/'+runId);if(trackedChatId!==runId)return;acceptChatRun(run);if(state.chatBusy)chatTimer=setTimeout(()=>pollChat(runId),650);}
 catch(e){if(trackedChatId!==runId)return;if(e.status===404){state.chatBusy=false;writeChatStorage('yannian-chat-active',null);if(state.view==='reader')renderAssistant();}else{if($('#chat-progress'))$('#chat-progress').textContent='连接中断，正在重新连接；回答不会因页面断线而丢失。';chatTimer=setTimeout(()=>pollChat(runId),2500);}}
}
async function sendChat(){
 if(state.chatBusy)return;const field=$('#chat-question'),question=field?.value.trim();if(!question)return;
 if(!state.settings.ready){settingsModal();return;}
 const choices=chatChoices(),payload={paper_id:state.paper.id,paragraph_id:state.para?.id||null,conversation_id:state.conversationId,question,selected_text:state.selectedText,
  include_page:$('#include-page').checked,page:state.pdfSelection?.page||state.page,selection_id:state.pdfSelection?.id||null,model:choices.model,effort:choices.effort||null};
 const pendingMessage={role:'user',content:question,attachments:state.pdfSelection?[{...state.pdfSelection,selection_id:state.pdfSelection.id}]:[]};
 saveChatDraft(question);state.chatBusy=true;state.chatRun=null;trackedChatId=null;clearTimeout(chatTimer);renderAssistant();
 let accepted=null;
 try{
  const run=await api('/chat/runs',{method:'POST',body:payload});
  accepted=run;trackedChatId=run.id;writeChatStorage('yannian-chat-active',run.id);
  const remembered=readChatStorage('yannian-last-chat');remembered[payload.paper_id]=run.conversation_id;writeChatStorage('yannian-last-chat',remembered);
  if(state.paper?.id===payload.paper_id){saveChatDraft('');state.conversationId=run.conversation_id;state.messages.push(pendingMessage);}
  acceptChatRun(run);if(state.view==='reader')renderAssistant({bottom:true});await pollChat(run.id);
 }catch(e){if(accepted){pollChat(accepted.id);}else{state.chatBusy=false;if(state.view==='reader')renderAssistant();}toast(e.message,true);}
}
async function stopChat(){
 const run=state.chatRun;if(!run?.id||run.status==='stopping')return;
 state.chatRun={...run,status:'stopping',phase:'正在停止模型'};renderAssistant();
 try{const result=await api('/chat/runs/'+run.id+'/cancel',{method:'POST'});if(trackedChatId===run.id)acceptChatRun(result);}
 catch(e){toast('停止请求尚未确认：'+e.message,true);state.chatRun={...run};renderAssistant();await pollChat(run.id);}
}
async function loadChat(conversationId,{focus=false}={}){
 const paperId=state.paper.id,r=await api('/conversations/'+conversationId);
 if(state.paper?.id!==paperId||r.conversation.paper_id!==paperId)return;
 state.conversationId=r.conversation.id;state.messages=r.messages;rememberConversation();
 if(focus){
  state.para=state.paper.paragraphs.find(p=>p.id===r.conversation.paragraph_id)||null;
  state.pdfSelection=r.conversation.selection_id?await api('/selections/'+r.conversation.selection_id):null;
  state.page=state.pdfSelection?.page||state.para?.page||state.page;state.selectedText=state.pdfSelection?.text||'';renderReader();
 }
 const active=r.messages.find(m=>['running','stopping'].includes(m.status));
 if(active){state.chatBusy=true;state.chatRun=await api('/chat/runs/'+active.run_id);pollChat(active.run_id);}
 renderAssistant({bottom:true});
}
async function restoreChat(){
 const remembered=readChatStorage('yannian-last-chat'),paperId=state.paper.id;
 let id=remembered[paperId];
 if(id===undefined){try{const items=await api('/papers/'+paperId+'/conversations');if(state.paper?.id!==paperId)return;id=items[0]?.id;}catch{}}
 if(id){try{await loadChat(id);}catch(e){if(e.status!==404)toast('历史对话暂时无法加载：'+e.message,true);}}
}
function newChat(){
 if(state.chatBusy){toast('请先停止当前回答，再创建新对话。');return;}
 state.messages=[];state.conversationId=null;state.chatRun=null;rememberConversation();renderAssistant({bottom:true});$('#chat-question').focus();
}
async function historyModal(){
 const items=await api('/papers/'+state.paper.id+'/conversations');
 modal('这篇论文的对话',items.length?`<div class="zotero-list">${items.map(c=>`<button class="check-row" data-history="${c.id}"><span>${esc(c.title)}<small>${new Date(c.created_at).toLocaleString()}</small></span></button>`).join('')}</div>`:'<p class="subtle">还没有保存的对话，发送问题后会自动记录。</p>');
 $$('[data-history]',$('#modal')).forEach(b=>b.onclick=()=>busy(b,async()=>{await loadChat(b.dataset.history);$('#modal').close();}));
}
function chatContextModal(){
 const last=[...state.messages].reverse().find(m=>m.context_info),info=last?.context_info;
 modal('对话与本轮上下文',`<p class="subtle">${info?`最近一轮带入 ${info.included_messages} / ${info.total_messages} 条历史消息${info.omitted_messages?'，较早的 '+info.omitted_messages+' 条因长度限制未附入':''}。`:'发送时会带入本次选区、检索到的原文和历史对话。'}历史记录在本机完整保留。当前选区会随追问发送；历史中的其他图片可点击缩略图重新选为当前关注。</p><h3>当前关注</h3>${focusedSelectionMarkup()}${state.pdfSelection?`<div class="selection-actions"><button data-selection-source="${esc(state.pdfSelection.id)}">回到原选区</button><button data-selection-idea="${esc(state.pdfSelection.id)}">记为 idea</button></div>`:''}${info?.sources?.length?`<h3>最近一轮参考原文</h3><div class="context-sources">${info.sources.map(c=>`<p>${esc(c.label)} · ${esc(c.title)}</p>`).join('')}</div>`:''}<h3>对话目录</h3><div class="zotero-list">${state.messages.map((m,i)=>`<button class="check-row" data-chat-jump="${i}"><span>${m.role==='user'?'你':'AI'} · ${esc((m.display_content??m.content).slice(0,140)||'正在生成…')}</span></button>`).join('')||'<p class="subtle">还没有消息。</p>'}</div>`);
 $$('[data-chat-jump]',$('#modal')).forEach(b=>b.onclick=()=>{$('#modal').close();$('#chat-message-'+b.dataset.chatJump)?.scrollIntoView({block:'start',behavior:'smooth'});});
}
