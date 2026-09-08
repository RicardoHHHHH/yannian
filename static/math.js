/* Protect TeX from Markdown escaping before rendering. No network resources or shared macros. */
(function(root){
 'use strict';
 const cache=new Map(),installed=new WeakSet();
 const escapeHTML=text=>String(text).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const environments='equation\\*?|align\\*?|aligned|alignat\\*?|gather\\*?|gathered|cases|matrix|pmatrix|bmatrix|vmatrix|Vmatrix|split';
 const environmentStart=new RegExp('^\\\\begin\\{('+environments+')\\}');
 function escaped(text,index){let count=0;while(index>0&&text[--index]==='\\')count++;return count%2===1;}
 function closing(text,delimiter,start){
  for(let i=start;i<text.length;i++)if(text.startsWith(delimiter,i)&&!escaped(text,i))return i;
  return -1;
 }
 function token(src,block){
  const leading=block?(src.match(/^ {0,3}/)||[''])[0].length:0,text=src.slice(leading);
  let left,right,display,keep=false;
  if(text.startsWith('$$')){left=right='$$';display=true;}
  else if(text.startsWith('\\[')){left='\\[';right='\\]';display=true;}
  else if(!block&&text.startsWith('\\(')){left='\\(';right='\\)';display=false;}
  else if(!block&&text[0]==='$'&&text[1]&&!/\s|\$/.test(text[1])){left=right='$';display=false;}
  else{const env=text.match(environmentStart);if(!env)return;left=env[0];right='\\end{'+env[1]+'}';display=true;keep=true;}
  const end=closing(text,right,left.length);
  if(end<0){
   // A streaming display formula must not become a wall of partially escaped TeX.
   if(display&&block)return {type:'yannianMathBlock',raw:src,tex:keep?text:text.slice(left.length),display:true,pending:true};
   return;
  }
  const tex=keep?text.slice(0,end+right.length):text.slice(left.length,end);
  if(left==='$'&&(/\n/.test(tex)||/\s$/.test(tex)||/\d/.test(text[end+1]||'')))return;
  if(!tex.trim())return;
  return {type:block?'yannianMathBlock':'yannianMathInline',raw:src.slice(0,leading+end+right.length),tex,display};
 }
 function render(token,katex){
  const key=JSON.stringify([token.tex,token.display,token.pending]);
  if(cache.has(key))return cache.get(key);
  let html;
  if(token.pending){
   html=`<details class="math-fallback"><summary>公式尚未完整 · 展开原文</summary><code>${escapeHTML(token.tex)}</code></details>`;
  }else{
   try{
    if(token.tex.length>16000)throw new Error('Formula too long');
    html=`<span class="math-formula ${token.display?'math-block':'math-inline'}">${katex.renderToString(token.tex,{displayMode:token.display,throwOnError:true,trust:false,strict:'ignore',maxExpand:1000,maxSize:12,macros:{},output:'htmlAndMathml'})}</span>`;
   }catch{
    html=token.display?`<details class="math-fallback"><summary>公式暂未排版 · 展开原文</summary><code>${escapeHTML(token.tex)}</code></details>`:`<code class="math-inline-fallback" title="公式暂未排版">${escapeHTML(token.tex)}</code>`;
   }
  }
  if(cache.size>=256)cache.delete(cache.keys().next().value);
  cache.set(key,html);return html;
 }
 function install(marked,katex){
  if(installed.has(marked))return;installed.add(marked);
  const startBlock=new RegExp('(?:^|\\n) {0,3}(?:\\$\\$|\\\\\\[|\\\\begin\\{(?:'+environments+')\\})');
  const startInline=/\$|\\\(|\\\[|\\begin\{/;
  marked.use({extensions:[
   {name:'yannianMathBlock',level:'block',start(src){return src.match(startBlock)?.index;},tokenizer(src){return token(src,true);},renderer(t){return render(t,katex)+'\n';}},
   {name:'yannianMathInline',level:'inline',start(src){return src.match(startInline)?.index;},tokenizer(src){return token(src,false);},renderer(t){return render(t,katex);}}
  ]});
 }
 const api={install,token};root.YannianMath=api;
 if(typeof module==='object'&&module.exports)module.exports=api;
 if(root.marked&&root.katex)install(root.marked,root.katex);
})(globalThis);
