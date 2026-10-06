// Browsing state is local to this tab; never changes scout configuration or run decisions.
let boardState=null, detailState=null, detailKey='', detailFetched=0, requestVersion=0;
let detailController=null, choicesReady=false, openDetailKeys=[];
const tableSort={http:{key:'total_ms',direction:-1},sql:{key:'total_ms',direction:-1}};
const runLabel=r=>`${r.short_id||r.id.slice(-8)} · ${r.passed===true?'PASS':r.passed===false?'FAIL':r.state} · ${num(r.score)} · ${r.started_at?new Date(r.started_at).toLocaleString('ja-JP'):''}`;
const signed=n=>n==null?'—':`${n>0?'+':''}${num(n,2)}`;
const metricNames={total_ms:'合計 ms',avg_ms:'平均 ms',p95_ms:'p95 ms',p99_ms:'p99 ms',max_ms:'最大 ms',min_ms:'最小 ms',p50_ms:'p50 ms',count:'回数',calls:'回数',errors:'エラー数',response_bytes:'転送 bytes',lock_ms:'ロック ms',rows_sent:'返却行数',rows_examined:'走査行数',rows_examined_per_call:'走査行数 / 回'};

function updateRunChoices(d){
 boardState=d;
 const q=d.metrics||{},runs=[...(q.score_history||[])].reverse();
 if(!choicesReady&&runs.length){document.querySelector('#base-select').dataset.initial=q.base?.id||'';choicesReady=true;}
 for(const id of ['run-select','base-select']){
  const select=document.getElementById(id),previous=select.value||select.dataset.initial||'';
  const options=[{value:'',label:id==='run-select'?'最新のベンチを自動表示':'比較なし'},...runs.map(r=>({value:r.id,label:runLabel(r)}))];
  if(previous&&!options.some(o=>o.value===previous))options.push({value:previous,label:previous});
  const signature=JSON.stringify(options);
  if(select.dataset.options!==signature){select.replaceChildren(...options.map(o=>{const e=el('option',o.label);e.value=o.value;return e}));select.dataset.options=signature;}
  select.value=previous;delete select.dataset.initial;
 }
 loadDetails();
}
function updateMeasurementHeader(){
 const q=boardState?.metrics||{},id=document.querySelector('#run-select').value||q.latest?.id;
 const runs=[...(q.score_history||[])].reverse(),index=runs.findIndex(r=>r.id===id),run=runs[index];
 const following=!document.querySelector('#run-select').value;
 for(const selector of ['.run-shortcuts','.measurement-options'])document.querySelector(selector).hidden=!run;
 document.querySelector('#measurement-title').textContent=run?`${following?'最新の計測':'選択中の計測'} · ${run.short_id||run.id.slice(-8)}`:'最新の計測';
 const base=document.querySelector('#base-select').value;
 document.querySelector('#measurement-context').textContent=run?`${run.passed===true?'PASS':run.passed===false?'FAIL':run.state} · ${num(run.score)}点 · ${base?'比較元 '+base.slice(-8):'比較元なし'}${following?' · 自動更新':''}`:'最初の計測が完了すると、ここに表示されます';
 document.querySelector('#previous-run').disabled=index<0||index>=runs.length-1;
 document.querySelector('#next-run').disabled=index<=0;
 document.querySelector('#latest-run').disabled=following;
}
function stepRun(offset){const runs=[...(boardState?.metrics?.score_history||[])].reverse(),id=document.querySelector('#run-select').value||boardState?.metrics?.latest?.id;const index=runs.findIndex(r=>r.id===id);if(index>=0&&runs[index+offset])chooseRun(runs[index+offset].id);}
function chooseRun(id){document.querySelector('#run-select').value=id;loadDetails(true);}
async function loadDetails(force=false){
 updateMeasurementHeader();
 const run=document.querySelector('#run-select').value||boardState?.metrics?.latest?.id;
 const base=document.querySelector('#base-select').value,limit=document.querySelector('#row-limit').value;
 const key=JSON.stringify([run,base,limit]);
 if(!run){const root=document.querySelector('#metrics'),box=el('div',null,'measurement-empty');box.append(el('h3','まだ計測結果はありません'),el('p','ベンチが完了すると、HTTP・SQLの負荷とスコアを自動で表示します。'));if(boardState?.metrics?.error){const more=disclosure(box,'取得状況を確認','empty-acquisition');more.append(el('p',boardState.metrics.error,'query-text'));}root.replaceChildren(box);renderScenarioGraph(root,{});return;}
 if(!force&&key===detailKey&&(detailController||Date.now()-detailFetched<30000))return;
 const background=key===detailKey&&detailState!=null;
 openDetailKeys=key===detailKey?[...document.querySelectorAll('#metrics details[open][data-key]')].map(x=>x.dataset.key):[];
 detailKey=key;const version=++requestVersion;
 detailController?.abort();const controller=new AbortController();detailController=controller;
 if(!background){
 detailState=null;
 document.querySelector('#metrics').replaceChildren(el('p','選択したrunの計測を取得しています…','empty'));
 document.querySelector('#metric-time').textContent='取得中';
 if(boardState)renderOverview({...boardState,metrics:{}});
 }
 try{
  const params=new URLSearchParams({run,base,limit});
  const response=await fetch('/api/metrics?'+params,{signal:controller.signal});
  if(!response.ok)throw Error(`HTTP ${response.status}`);
  const q=await response.json();if(version!==requestVersion)return;
  if(q.error)throw Error(q.error);
  console.debug('[ISUCON board] 取得コマンド・計測の根拠',{run:q.latest?.id,base:q.base?.id,collected_at:q.collected_at,sections:q.sections,coverage_issues:q.brief?.coverage_issues,review:q.brief?.review});
  const scroll=window.scrollY;
  detailState=q;detailFetched=Date.now();renderDetail(q);
  for(const node of document.querySelectorAll('#metrics details[data-key]'))node.open=openDetailKeys.includes(node.dataset.key);
  renderOverview({...boardState,metrics:q});
  if(background)window.scrollTo(0,scroll);
 }catch(e){if(version!==requestVersion||e.name==='AbortError')return;
  document.querySelector('#metrics').replaceChildren(el('p','選択したrunを取得できません。 '+e.message,'metrics-error'));
  document.querySelector('#metric-time').textContent='取得失敗';detailFetched=Date.now();
 }finally{if(version===requestVersion)detailController=null;}
}
function comparisonTable(parent,rows){table(parent,['項目','比較元','対象','差分','変化率'],rows.map(([name,b,c,delta,percent])=>[name,num(b,2),num(c,2),signed(delta),percent==null?'—':signed(percent)+'%']));}
function valueDiff(b,c){return [b,c,b!=null&&c!=null?c-b:null,b!=null&&c!=null&&b!==0?(c-b)/Math.abs(b)*100:null];}
function renderDetail(q){
 chartProbes=[];prepareWorkspace(q);q=workspaceData(q,workspace.node);
 const root=document.querySelector('#metrics');root.replaceChildren();
 document.querySelector('#metric-time').textContent='取得 '+time(q.collected_at);
 const run=q.latest,b=q.brief||{},review=b.review||{},analysis=review.latest_analysis;
 const summary=el('div',null,'panel');summary.id='run-summary';root.append(summary);const info=disclosure(summary,'仮説・分析・採否・コードの情報','run-info');info.className='run-info';
 const content=el('div',null,'detail-padding');info.append(content);
 dataList(content,[['対象',run.short_id||run.id],['比較元',q.base?.short_id||q.base?.id||'なし'],['状態',`${run.state} · ${run.passed===true?'PASS':run.passed===false?'FAIL':'判定なし'}`],['仮説',run.hypothesis],['判定',analysis?.verdict||run.analysis_status],['分析',analysis?.body],['分析の比較元',analysis?.base_run_id||'未指定'],['計測commit',(run.commit_hash||'不明')+(run.dirty?' (dirty)':'')],['現在commit',(q.current_commit||'不明')+(q.dirty?' (dirty)':'')]]);
 if(run.commit_hash!==q.current_commit||run.dirty||q.dirty)content.append(el('p','現在のコードと計測時のコードが一致するとは限りません。commitとdirtyを確認してください。','note'));
 for(const x of review.changes||[]){const decision=x.latest_decision;dataList(content,[['変更',`${x.change.id} · ${x.change.description}`],['採否',decision?.status||'未判断'],['理由',decision?.reason],['再検討',decision?.revisit]]);}
 if(review.changes_truncated)content.append(el('p','変更の採否は一部のみ表示されています。','note'));
 if(q.base){comparisonTable(summary,[['スコア',...valueDiff(q.base.score,run.score)]]);content.append(el('p','差分は対象 − 比較元。FAILのスコアや要求回数の増減だけで改善とは判断しません。','note'));}
 const messages=[...(b.benchmark_messages?.failure||[]),...(b.benchmark_messages?.errors||[]).flatMap(x=>x.samples||[]),...(b.warnings||[])];
 for(const message of messages)root.append(el('p',message,'metrics-error'));
 renderExpandedMetrics(root,q,'before');
 for(const kind of ['http','sql'])renderMetricTable(root,kind,q.sections?.[kind]?.data||{},!!q.base);
 renderExpandedMetrics(root,q,'after');
 renderHistory(root);applyWorkspace();
}
function metricRows(data,comparing){return (data.rows||[]).map(r=>comparing?r:{candidate:r,base:null,changes:{},presence:'candidate'});}
function sortRows(rows,sort){return [...rows].sort((a,b)=>{
 const value=r=>sort.key==='delta'?r.changes?.total_ms?.delta:r.candidate?.[sort.key];
 const av=value(a),bv=value(b);if(av==null)return bv==null?0:1;if(bv==null)return -1;
 return typeof av==='number'?(av-bv)*sort.direction:String(av).localeCompare(String(bv))*sort.direction;
});}
function renderMetricTable(parent,kind,data,comparing){
 const panel=metricPanel(kind==='http'?'HTTP':'SQL',kind==='http'?'RUN全体':'LOAD · digest単位');panel.id='detail-'+kind;parent.append(panel);
 if(data.error){panel.append(el('p',data.error,'metrics-error'));return;}
 for(const warning of data.warnings||[])panel.append(el('p',warning,'metrics-error'));
 if(!comparing&&tableSort[kind].key==='delta')tableSort[kind].key='total_ms';
 const count=kind==='http'?'count':'calls';
 const allRows=metricRows(data,comparing),rows=allRows.filter(r=>{const x=r.candidate||r.base;return [x.route,x.method,x.digest,x.node].join(' ').toLowerCase().includes(workspace.search[kind].toLowerCase())});
 panel.classList.add('metric-ranking');const tools=el('div',null,'table-tools');tools.append(el('span',`${rows.length} / ${data.total_count??rows.length}件${data.truncated?' · 取得上限あり':''}。ソートは取得済みの行が対象。`));
 const redraw=()=>{const replacement=el('div');renderMetricTable(replacement,kind,data,comparing);panel.replaceWith(replacement.firstChild)};
 const search=el('input');search.type='search';search.placeholder=kind==='http'?'API・サーバーを検索':'SQL・サーバーを検索';search.setAttribute('aria-label',search.placeholder);search.value=workspace.search[kind];search.addEventListener('change',()=>{workspace.search[kind]=search.value;redraw()});tools.append(search);
 panel.append(tools);
 if(!rows.length){panel.append(el('p','対象の計測なし · 未取得は0件・0msと区別します。','panel-note'));return;}
 const heads=comparing?[kind==='http'?'API / サーバー':'SQL / サーバー','比較元 合計 ms','対象 合計 ms','差分 ms','変化率','対象 回数','対象 平均 ms','対象 p95 ms']:[kind==='http'?'API / サーバー':'SQL / サーバー','合計 ms','回数','平均 ms','p95 ms'];
 const extraKeys=kind==='http'?['errors']:['lock_ms','rows_examined_per_call'];heads.push(...extraKeys.map(key=>metricNames[key]));
 const peak=Math.max(1,...rows.map(r=>(r.candidate||r.base).total_ms||0));
 table(panel,heads,sortRows(rows,tableSort[kind]).map((r,index)=>{
  const x=r.candidate||r.base,identity=el('div',null,'route');
  const label=kind==='http'?`${x.method} ${x.route}`:x.digest;
  const open=el('button',label?.length>150?label.slice(0,150)+'…':label,'detail-link');open.type='button';open.addEventListener('click',()=>openMetric(kind,r,comparing));identity.append(open,el('div',[x.node,x.source,x.window].filter(Boolean).join(' · '),'node-label'));const volume=el('div',null,'metric-volume');volume.style.width=(Math.max(0,x.total_ms||0)/peak*120)+'px';volume.title='取得した行の最大合計時間に対する比率';identity.append(volume);
  if(comparing&&r.presence!=='both')identity.append(el('span',r.presence==='added'?'対象のみ':'比較元のみ','badge'));
  if(!comparing)return [identity,num(x.total_ms,2),num(x[count]),num(x.avg_ms,2),num(x.p95_ms,2),...extraKeys.map(key=>num(x[key],2))];
  const diff=r.changes?.total_ms||{};
  return [identity,num(r.base?.total_ms,2),num(r.candidate?.total_ms,2),signed(diff.delta),diff.delta_percent==null?'—':signed(diff.delta_percent)+'%',num(r.candidate?.[count]),num(r.candidate?.avg_ms,2),num(r.candidate?.p95_ms,2),...extraKeys.map(key=>num(r.candidate?.[key],2))];
 }));
 panel.querySelectorAll('tbody tr').forEach(tr=>{const cells=tr.querySelectorAll('td');cells[comparing?2:1]?.classList.add('metric-primary');if(comparing){cells[3]?.classList.add('metric-delta');const value=Number(cells[3]?.textContent.replaceAll(',',''));if(Number.isFinite(value)&&value!==0)cells[3].classList.add(value>0?'delta-up':'delta-down');}});
 const keys=comparing?[null,null,'total_ms','delta',null,count,'avg_ms','p95_ms']:[null,'total_ms',count,'avg_ms','p95_ms'];
 keys.push(...extraKeys);
 panel.querySelectorAll('th').forEach((th,i)=>{const key=keys[i];if(!key)return;const active=tableSort[kind].key===key;th.setAttribute('aria-sort',active?(tableSort[kind].direction===-1?'descending':'ascending'):'none');const button=el('button',heads[i]+(active?(tableSort[kind].direction===-1?' ↓':' ↑'):''),'sort-heading'+(active?' active':''));button.type='button';button.title='クリックで並べ替え';button.addEventListener('click',()=>{tableSort[kind]={key,direction:active?-tableSort[kind].direction:-1};redraw()});th.replaceChildren(button);});

}
function openMetric(kind,row,comparing){
 const dialog=document.querySelector('#metric-dialog'),body=document.querySelector('#metric-dialog-body');body.replaceChildren();
 const x=row.candidate||row.base;
 body.append(el('h2',kind==='http'?'HTTP詳細':'SQL詳細'));
 body.append(el('p',`対象 ${detailState?.latest?.short_id||detailState?.latest?.id} · 比較元 ${comparing?(detailState?.base?.short_id||detailState?.base?.id||'なし'):'なし'}`,'note'));
 dataList(body,[['node',x.node],['source',x.source],['区間',kind==='sql'?(x.window||'不明'):'run全体']]);
 for(const [label,value]of (comparing?[['比較元',row.base],['対象',row.candidate]]:[['対象',row.candidate]])){
  body.append(el('h3',label));
  if(!value){body.append(el('p','このrunには対応する行がありません。','note'));continue;}
  body.append(el('pre',kind==='http'?`${value.method} ${value.route}`:value.digest,'query-text'));
  if(value.digest_id)body.append(el('p',`長いSQLはisuscope側で省略されています。digest ID: ${value.digest_id}`,'note'));
  for(const [key,reason]of Object.entries(value.unavailable||{}))body.append(el('p',`${metricNames[key]||key}: ${reason}`,'note'));
 }
 const keys=kind==='http'?['count','total_ms','avg_ms','min_ms','p50_ms','p95_ms','p99_ms','max_ms','errors','response_bytes']:['calls','total_ms','avg_ms','p95_ms','p99_ms','max_ms','lock_ms','rows_sent','rows_examined','rows_examined_per_call'];
 if(comparing)comparisonTable(body,keys.map(key=>{const d=row.changes?.[key];return [metricNames[key],row.base?.[key],row.candidate?.[key],d?.delta,d?.delta_percent]}));
 else table(body,['項目','対象'],keys.map(key=>[metricNames[key],num(x[key],2)]));
 if(kind==='http')for(const [label,value]of [['比較元',row.base],['対象',row.candidate]])if(value)table(body,[label+' HTTP status','回数'],Object.entries(value.status_counts||{}).map(([key,count])=>[key,num(count)]));
 dialog.showModal();
}
document.querySelector('#previous-run').addEventListener('click',()=>stepRun(1));
document.querySelector('#next-run').addEventListener('click',()=>stepRun(-1));
document.querySelector('#latest-run').addEventListener('click',()=>chooseRun(''));
for(const id of ['run-select','base-select','row-limit'])document.getElementById(id).addEventListener('change',()=>loadDetails(true));
document.querySelector('#reload-detail').addEventListener('click',()=>loadDetails(true));
document.querySelector('#close-detail').addEventListener('click',()=>document.querySelector('#metric-dialog').close());
initWorkspace();refresh();setInterval(refresh,5000);
