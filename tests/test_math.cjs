// Real Marked + KaTeX distributions. No browser, network, or model requests.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {Marked}=require('../static/vendor/marked.js');
const katex=require('../static/vendor/katex/katex.min.js');
const {install}=require('../static/math.js');
const marked=new Marked();install(marked,katex);
const render=source=>marked.parse(source,{breaks:true});
let count=0;
function test(name,fn){fn();count++;console.log('PASS',name);}
test('reported indicator and Chinese cases survive Markdown escaping',()=>{
 const formula=String.raw`\[
r=\mathbf{1}\{\texttt{evaluate()}=\texttt{True}\}
=
\begin{cases}
1,&\text{通过验证}\\
0,&\text{未通过验证}
\end{cases}
\]`;
 const html=render(formula);
 assert.ok(html.includes('katex-display')&&html.includes('<mtable'));
 assert.ok(html.includes('通过验证')&&html.includes('未通过验证'));
 assert.ok(!html.includes('math-fallback')&&!html.includes('<strong>'));
});
test('inline and display delimiters work in prose, lists and blockquotes',()=>{
 const html=render(String.raw`Inline $x_i^2$ and \(\frac{a}{b}\).

- Inline $\alpha$ in a list.

> $$E=mc^2$$

Before

\[\sum_{i=1}^{n}i=\frac{n(n+1)}2\]

After`);
 assert.equal((html.match(/class="math-formula/g)||[]).length,5);
 assert.ok(html.includes('<li>')&&html.includes('<blockquote>')&&html.includes('After'));
});
test('standalone aligned and cases environments preserve row separators',()=>{
 for(const tex of [String.raw`\begin{aligned}a&=b\\c&=d\end{aligned}`,String.raw`\begin{cases}1&x>0\\0&x\le0\end{cases}`]){
  const html=render(tex);assert.ok(html.includes('<mtable')&&!html.includes('math-fallback'));
 }
});
test('code, escaped dollars, ordinary prices, links and citations remain Markdown',()=>{
 const source='`$x$`\n\n```latex\n\\[x^2\\]\n```\n\nCost $5 and $10. Escaped \\$x\\$. [P1] [Paper](https://example.org/paper).';
 const html=render(source);
 assert.ok(!html.includes('class="math-formula'));
 assert.ok(html.includes('<code>$x$</code>')&&html.includes('https://example.org/paper')&&html.includes('[P1]'));
});
test('streamed display formula waits until its closing delimiter arrives',()=>{
 const start=String.raw`\[\frac{1}{`;
 const pending=render(start);assert.ok(pending.includes('公式尚未完整'));
 const done=render(start+String.raw`2}\]`);assert.ok(done.includes('katex-display')&&!done.includes('math-fallback'));
});
test('invalid TeX preserves escaped source without breaking the next paragraph',()=>{
 const html=render(String.raw`\[\invalidCommand{<img src=x onerror=alert(1)>}\]

Still readable **answer**.`);
 assert.ok(html.includes('公式暂未排版')&&html.includes('&lt;img'));
 assert.ok(!html.includes('<img')&&html.includes('<strong>answer</strong>'));
});
test('untrusted math cannot create active links, remote images or shared macros',()=>{
 const html=render(String.raw`\[\href{javascript:alert(1)}{run}\includegraphics{https://example.org/tracker.png}\]`);
 assert.ok(!/<a\s|<img\s|href="javascript:/.test(html));
 render(String.raw`$\gdef\privateMacro{secret}x$`);
 assert.ok(render(String.raw`$\privateMacro$`).includes('math-inline-fallback'));
 assert.ok(render(String.raw`\[\def\loop{\loop}\loop\]`).includes('math-fallback'));
});
test('bundled stylesheet font URLs resolve to real local font files',()=>{
 const root=path.resolve(__dirname,'../static/vendor/katex');
 const css=fs.readFileSync(path.join(root,'katex.min.css'),'utf8');
 const fonts=[...css.matchAll(/url\(["']?(fonts\/[^)"']+)/g)].map(x=>x[1]);
 assert.ok(fonts.length>20);
 for(const font of fonts)assert.ok(fs.statSync(path.join(root,font)).size>0,font);
});
console.log(`${count} math rendering checks passed.`);
