// Evidence navigation only: no inferred causal links, collection or run mutations.
const workspace={node:'',search:{http:'',sql:''}};
function scopedRows(data,node){return {...data,rows:(data?.rows||[]).filter(r=>!node||(r.candidate||r.base||r).node===node)};}
function workspaceData(q,node){
 if(!node)return q;
 const b=q.brief||{};
 const items=data=>data?{...data,items:(data.items||[]).filter(r=>r.node===node)}:data;
 return {...q,sections:Object.fromEntries(Object.entries(q.sections||{}).map(([k,s])=>[k,{...s,data:scopedRows(s.data||{},node)}])),
  timeline:scopedRows(q.timeline||{},node),mysql:scopedRows(q.mysql||{},node),graph_http:scopedRows(q.graph_http||{},node),
  brief:{...b,hosts:(b.hosts||[]).filter(r=>r.node===node),clients:(b.clients||[]).filter(r=>r.node===node),upstreams:items(b.upstreams),cpu:items(b.cpu)}};
}
function prepareWorkspace(q){
 const select=document.querySelector('#node-select'),nodes=new Set();
 for(const h of q.brief?.hosts||[])nodes.add(h.node);
 for(const section of Object.values(q.sections||{}))for(const r of section.data?.rows||[]){const x=r.candidate||r.base||r;if(x.node)nodes.add(x.node);}
 const values=[...nodes].filter(Boolean).sort();if(workspace.node&&!nodes.has(workspace.node))values.push(workspace.node);
 select.replaceChildren(...[['','全サーバー'],...values.map(n=>[n,n])].map(([v,t])=>{const o=el('option',t);o.value=v;return o}));select.value=workspace.node;
}
function applyWorkspace(){
 document.querySelector('#workspace-context').textContent=`${workspace.node||'全サーバー'} · HTTPは計測全体、SQL・時系列は負荷走行中。スコア・採否・計測の不足は全サーバー共通。`;
}
function renderHistory(root){
 const panel=metricPanel('ベンチ走行履歴','直近100件の終了したベンチ');panel.id='detail-history';panel.classList.add('history-table');root.append(panel);
 panel.append(el('p','ベンチを選ぶと、各グラフ・表が切り替わります。','panel-note'));
 table(panel,['ベンチ','試したこと','状態','分析','スコア','開始','commit'],[...(boardState?.metrics?.score_history||[])].reverse().map(r=>{const a=el('button',r.short_id||r.id.slice(-8),'detail-link');a.addEventListener('click',()=>chooseRun(r.id));const hypothesis=el('div',r.hypothesis||'記録なし','history-hypothesis');return [a,hypothesis,resultBadge(r),analysisLabel(r),num(r.score),new Date(r.started_at).toLocaleString('ja-JP'),(r.commit_hash||'不明').slice(0,8)+(r.dirty?' dirty':'')]}));
}
function initWorkspace(){
 document.querySelector('#node-select').addEventListener('change',e=>{workspace.node=e.target.value;if(detailState)renderDetail(detailState)});
}
