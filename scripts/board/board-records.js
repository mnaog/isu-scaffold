// The required records (config/records.json): where each one lives, whether it exists, and its text.
// Markdown is turned into DOM nodes with textContent only; nothing from a record becomes HTML.
let recordState=null;
function recordWhen(record){
 if(!record.exists)return '未作成';
 const parts=[];
 if(record.modified_at)parts.push('更新 '+new Date(record.modified_at*1000).toLocaleString('ja-JP',{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'}));
 if(record.last_commit)parts.push('commit '+record.last_commit.commit);
 return parts.join(' · ');
}
function renderRecords(data){
 recordState=data;
 const root=document.querySelector('#records');root.replaceChildren();
 const records=data?.records||[];
 const made=records.filter(r=>r.exists).length;
 document.querySelector('#records-count').textContent=`${made}/${records.length}`;
 if(data?.error){root.append(el('p',data.error,'error-text'));return;}
 for(const record of records){
  const card=el('button',null,'record-card'+(record.exists?'':' missing'));card.type='button';
  const head=el('div',null,'record-head');
  head.append(el('span',record.title,'record-title'),el('span',record.timing,'record-timing'));
  card.append(head,el('p',record.path,'record-path'),el('p',record.contents,'record-contents'));
  const foot=el('p',null,'record-state');
  foot.append(el('span',recordWhen(record)));
  if(record.directory&&record.exists)foot.append(el('span',`${record.files.length}件`));
  if(record.uncommitted)foot.append(el('span','未commitの変更あり','record-dirty'));
  card.append(foot);
  card.addEventListener('click',()=>record.directory?openRecordDirectory(record):openRecord(record.path,record));
  root.append(card);
 }
}
function recordDialog(){
 const dialog=document.querySelector('#record-dialog'),body=document.querySelector('#record-dialog-body');
 body.replaceChildren();
 if(!dialog.open)dialog.showModal();
 return body;
}
function openRecordDirectory(record){
 const body=recordDialog();
 body.append(el('h2',record.title),el('p',record.path,'record-path'),el('p',record.contents,'note'));
 if(!record.exists){body.append(el('p','まだ作られていません。','note'));return;}
 const list=el('ul',null,'record-files');
 for(const file of record.files){
  const item=el('li'),open=el('button',file.path.slice(record.path.length),'record-link');open.type='button';
  open.addEventListener('click',()=>openRecord(file.path,record));
  item.append(open,el('span',` ${num(file.size)} B`,'record-path'));list.append(item);
 }
 body.append(list);
}
async function openRecord(path,record){
 const body=recordDialog();
 body.append(el('h2',record?.title||path),el('p',path,'record-path'));
 if(record&&!record.directory&&record.exists===false){body.append(el('p','まだ作られていません。置き場所と書く内容:','note'),el('p',record.contents,'note'));return;}
 const loading=el('p','読み込み中','note');body.append(loading);
 try{
  const response=await fetch('/api/record?path='+encodeURIComponent(path));
  if(!response.ok)throw Error(response.status);
  const data=await response.json();loading.remove();
  if(data.error){body.append(el('p',data.error,'note'));return;}
  const meta=[];
  if(data.last_commit)meta.push(`最終commit ${data.last_commit.commit} ${data.last_commit.subject}`);
  if(data.truncated)meta.push(`先頭${num(data.text.length)}文字だけを表示しています`);
  if(meta.length)body.append(el('p',meta.join(' · '),'note'));
  const doc=el('div',null,'record-doc');body.append(doc);
  if(/\.md$/i.test(path))renderMarkdown(doc,data.text,path);
  else if(/\.json$/i.test(path)){let text=data.text;try{text=JSON.stringify(JSON.parse(text),null,2)}catch(e){}doc.append(el('pre',text))}
  else doc.append(el('pre',data.text));
 }catch(e){loading.textContent='読み込めませんでした '+e.message;}
}
function recordLink(href,from){
 // A relative link to another record opens it here; other relative paths stay plain text.
 if(/^https?:\/\//.test(href))return {external:href};
 const parts=from.split('/').slice(0,-1);
 for(const part of href.split('#')[0].split('/')){if(part==='..')parts.pop();else if(part&&part!=='.')parts.push(part);}
 const path=parts.join('/');
 const record=(recordState?.records||[]).find(r=>r.path===path||(r.directory&&path.startsWith(r.path)));
 return record?{path,record}:null;
}
function inlineMarkdown(parent,text,from){
 const pattern=/(`[^`]+`)|(\*\*[^*]+\*\*)|\[([^\]]+)\]\(([^)\s]+)\)/g;
 let last=0,match;
 while((match=pattern.exec(text))){
  if(match.index>last)parent.append(document.createTextNode(text.slice(last,match.index)));
  if(match[1])parent.append(el('code',match[1].slice(1,-1)));
  else if(match[2])parent.append(el('strong',match[2].slice(2,-2)));
  else{
   const target=recordLink(match[4],from);
   if(target?.external){const a=el('a',match[3]);a.href=target.external;a.target='_blank';a.rel='noopener noreferrer';parent.append(a);}
   else if(target){const a=el('button',match[3],'record-link');a.type='button';a.addEventListener('click',()=>openRecord(target.path,target.record.path===target.path?target.record:{title:target.path}));parent.append(a);}
   else{const span=el('span',match[3]);span.title=match[4];parent.append(span);}
  }
  last=pattern.lastIndex;
 }
 if(last<text.length)parent.append(document.createTextNode(text.slice(last)));
}
function tableCells(line){return line.trim().replace(/^\||\|$/g,'').split('|').map(cell=>cell.trim());}
function renderMarkdown(parent,text,from=''){
 const lines=text.replace(/\r\n?/g,'\n').split('\n');
 let paragraph=[],lists=[];
 const flush=()=>{if(paragraph.length){const p=el('p');inlineMarkdown(p,paragraph.join(' '),from);parent.append(p);paragraph=[];}};
 const closeLists=()=>{lists=[];};
 for(let i=0;i<lines.length;i++){
  const line=lines[i];
  const fence=line.match(/^\s*(```|~~~)/);
  if(fence){flush();closeLists();const code=[];i++;while(i<lines.length&&!lines[i].trim().startsWith(fence[1])){code.push(lines[i]);i++;}parent.append(el('pre',code.join('\n')));continue;}
  if(!line.trim()){flush();closeLists();continue;}
  const heading=line.match(/^(#{1,6})\s+(.*)$/);
  if(heading){flush();closeLists();const h=el('h'+Math.min(6,heading[1].length+1));inlineMarkdown(h,heading[2],from);parent.append(h);continue;}
  if(/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(line)){flush();closeLists();parent.append(el('hr'));continue;}
  if(line.trim().startsWith('|')&&i+1<lines.length&&/^\s*\|?\s*:?-{2,}/.test(lines[i+1])){
   flush();closeLists();
   const table=el('table'),head=el('tr');
   for(const cell of tableCells(line)){const th=el('th');inlineMarkdown(th,cell,from);head.append(th);}
   table.append(head);i+=2;
   while(i<lines.length&&lines[i].trim().startsWith('|')){const tr=el('tr');for(const cell of tableCells(lines[i])){const td=el('td');inlineMarkdown(td,cell,from);tr.append(td);}table.append(tr);i++;}
   i--;const wrap=el('div',null,'record-table');wrap.append(table);parent.append(wrap);continue;
  }
  const item=line.match(/^(\s*)([-*+]|\d+[.)])\s+(.*)$/);
  if(item){
   flush();
   const indent=item[1].replace(/\t/g,'  ').length,ordered=/\d/.test(item[2]);
   while(lists.length&&indent<lists[lists.length-1].indent)lists.pop();
   // A numbered item after bullets at the same level starts a new list, and the other way round.
   if(lists.length&&indent===lists[lists.length-1].indent&&lists[lists.length-1].ordered!==ordered)lists.pop();
   let current=lists[lists.length-1];
   if(!current||indent>current.indent){
    const list=el(ordered?'ol':'ul');
    if(current?.last)current.last.append(list);else parent.append(list);
    current={indent,ordered,list,last:null};lists.push(current);
   }
   const li=el('li');inlineMarkdown(li,item[3],from);current.list.append(li);current.last=li;continue;
  }
  if(lists.length&&/^\s+\S/.test(line)){const last=lists[lists.length-1].last;last.append(document.createTextNode(' '));inlineMarkdown(last,line.trim(),from);continue;}
  const quote=line.match(/^>\s?(.*)$/);
  if(quote){flush();closeLists();const q=el('blockquote');inlineMarkdown(q,quote[1],from);parent.append(q);continue;}
  closeLists();paragraph.push(line.trim());
 }
 flush();
}
let recordsLoading=false;
async function refreshRecords(){
 if(recordsLoading)return;recordsLoading=true;
 try{const response=await fetch('/api/records');if(!response.ok)throw Error(response.status);renderRecords(await response.json());}
 catch(e){document.querySelector('#records').replaceChildren(el('p','記録の一覧を取得できません '+e.message,'error-text'));}
 finally{recordsLoading=false;}
}
if(typeof document!=='undefined'&&document.querySelector?.('#records')){
 document.querySelector('#close-record').addEventListener('click',()=>document.querySelector('#record-dialog').close());
 refreshRecords();setInterval(refreshRecords,30000);
}
