const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');

// A small stand-in for the DOM: elements keep their tag, text and children.
function node(tag,text,cls){return {tag,textContent:text??'',className:cls||'',children:[],attrs:{},events:{},
 append(...xs){this.children.push(...xs)},replaceChildren(...xs){this.children=xs},addEventListener(k,fn){this.events[k]=fn},remove(){}};}
const context=vm.createContext({el:node,num:v=>String(v),document:{createTextNode:text=>({tag:'#text',textContent:text,children:[]})}});
vm.runInContext(fs.readFileSync(__dirname+'/../scripts/board/board-records.js','utf8'),context);
const render=(text,from='docs/code-map.md')=>{const root=node('div');context.renderMarkdown(root,text,from);return root.children;};
const text=n=>n.tag==='#text'?n.textContent:(n.textContent||'')+n.children.map(text).join('');

test('headings, paragraphs, tables and code become separate blocks',()=>{
 const blocks=render('# 地図\n\n本文の1行目\n2行目\n\n| API | SQL |\n| --- | --- |\n| `GET /a` | **SELECT** |\n\n```\nlet x = 1;\n```\n');
 assert.deepEqual(blocks.map(b=>b.tag),['h2','p','div','pre']);
 assert.equal(text(blocks[1]),'本文の1行目 2行目');
 const table=blocks[2].children[0];
 assert.equal(table.children.length,2);
 const cells=table.children[1].children;
 assert.equal(cells[0].children[0].tag,'code');
 assert.equal(cells[1].children[0].tag,'strong');
 assert.equal(blocks[3].textContent,'let x = 1;');
});

test('nested lists keep their levels',()=>{
 const [list,numbered]=render('- シナリオ\n  - API\n    - handler\n- 次\n1. 番号');
 assert.equal(list.tag,'ul');
 assert.equal(numbered.tag,'ol');
 assert.equal(list.children.length,2);
 const nested=list.children[0].children.find(c=>c.tag==='ul');
 assert.equal(text(nested.children[0]).startsWith('API'),true);
 assert.equal(nested.children[0].children.find(c=>c.tag==='ul').children.length,1);
});

test('record text never becomes markup, and only http links leave the board',()=>{
 const [p]=render('<img src=x onerror=alert(1)> [公式](https://example.com) [地図](./benchmark-scenario.md) [外](javascript:void)');
 assert.equal(p.children[0].tag,'#text');
 assert.match(p.children[0].textContent,/<img/);
 const external=p.children.find(c=>c.tag==='a');
 assert.equal(external.href,'https://example.com');
 assert.equal(external.rel,'noopener noreferrer');
 // Without a listing nothing else is a record, so the other links stay text with the target as a title.
 const spans=p.children.filter(c=>c.tag==='span');
 assert.deepEqual(spans.map(s=>s.title),['./benchmark-scenario.md','javascript:void']);
});

test('relative links to another record open it on the board',()=>{
 vm.runInContext("recordState={records:[{path:'docs/benchmark-scenario.md',exists:true},{path:'docs/official/',directory:true,exists:true}]}",context);
 const [p]=render('[見取り図](benchmark-scenario.md) と [manual](official/manual.md#a)');
 const links=p.children.filter(c=>c.tag==='button');
 assert.deepEqual(links.map(l=>l.textContent),['見取り図','manual']);
 assert.equal(context.recordLink('official/manual.md#a','docs/code-map.md').path,'docs/official/manual.md');
 assert.equal(context.recordLink('../AGENTS.md','docs/code-map.md'),null);
});
