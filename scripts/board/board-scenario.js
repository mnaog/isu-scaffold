// The graph structure comes from the initial survey. HTTP labels always use the selected run.
const scenarioPrefs={edgeCounts:true,scale:1};
let scenarioRenderId=0;
// Mermaid entity escapes keep log-derived strings out of diagram syntax and HTML.
function mermaidText(value){return String(value).replace(/[&#"<>`\\\r\n]/g,c=>`#${c.codePointAt(0)};`);}
function buildScenario(q,showCounts=false){
 const survey=q.survey||{},all=survey.transitions?.items||[],edges=all.slice(0,80);
 const routes=[...new Set(edges.flatMap(e=>[e.from_route,e.to_route]))].sort();
 const ids=new Map(routes.map((route,i)=>[route,'api'+i]));
 const metrics=new Map(routes.map(route=>[route,[]]));
 for(const row of q.graph_http?.rows||[]){const route=`${row.method} ${row.route}`;if(metrics.has(route))metrics.get(route).push(row);}
 const qualities=survey.quality?.rows||[],version=qualities.some(r=>r.metric==='transition.ordering_version'&&r.value>=2);
 const peak=Math.max(1,...[...metrics.values()].flat().map(r=>r.total_ms??0));
 const lines=['flowchart LR',`%% Structure: ${mermaidText(survey.run||'unknown')}`,`%% HTTP values: ${mermaidText(q.latest?.short_id||'unknown')}`];
 for(const route of routes){const rows=metrics.get(route),max=Math.max(0,...rows.map(r=>r.total_ms??0));
  const label=[mermaidText(route),...rows.slice(0,4).flatMap(r=>[mermaidText(`${r.node} | ${num(r.count)}回 | error ${num(r.errors)}`),mermaidText(`avg ${num(r.avg_ms,2)}ms | p95 ${num(r.p95_ms,2)}ms`),mermaidText(`合計 ${num(r.total_ms,1)}ms`)])];
  if(rows.length>4)label.push(`ほか ${rows.length-4} node（クリックで詳細）`);
  if(!rows.length)label.push('選択runの計測なし');
  const style=!rows.length?'missing':max/peak>=.6?'hot':max/peak>=.25?'warm':'normal';
  lines.push(`${ids.get(route)}["${label.join('<br/>')}"]:::${style}`);
 }
 let uncertain=0;
 for(const edge of edges){const match=qualities.filter(r=>r.labels?.from===edge.from_route&&r.labels?.to===edge.to_route);
  const overlap=match.find(r=>r.metric==='transition.overlap_count')?.value,ambiguous=match.find(r=>r.metric==='transition.ambiguous_count')?.value;
  const unsure=!version||overlap==null||ambiguous==null||overlap>0||ambiguous>0;if(unsure)uncertain++;
  const label=showCounts?`|"survey ${edge.count}回${unsure?' / 順序注意':''}"|`:'';
  lines.push(`${ids.get(edge.from_route)} ${unsure?'-.->':'-->'}${label} ${ids.get(edge.to_route)}`);
 }
 lines.push('classDef normal fill:#303d3b,stroke:#809e94,color:#edf1ee','classDef warm fill:#403d34,stroke:#b9aa84,color:#fff0cf','classDef hot fill:#493a36,stroke:#c99680,color:#fff0e8','classDef missing fill:#363b42,stroke:#929ba6,color:#c2c9d1,stroke-dasharray:4 3');
 return {source:lines.join('\n'),routes,ids,metrics,uncertain,truncated:all.length>80||survey.transitions?.truncated,version};
}
function openScenarioRoute(route,rows,q){
 const dialog=document.querySelector('#metric-dialog'),body=document.querySelector('#metric-dialog-body');body.replaceChildren(el('h2',route));
 body.append(el('p',`計測run ${q.latest.short_id} · nodeごとの値（p95は合算していません）`,'note'));
 if(!rows.length)body.append(el('p','選択runで対応するHTTP計測が取得できていません。0回とは判断しません。','note'));
 for(const row of rows){const button=el('button',`${row.node} · ${num(row.count)}回 · HTTP詳細`);button.type='button';button.addEventListener('click',()=>{dialog.close();openMetric('http',{candidate:row,base:null,changes:{}},false)});body.append(button);}
 if(!dialog.open)dialog.showModal();
}
function renderScenarioPlaceholder(panel){
 const banner=el('div',null,'scenario-example-banner');banner.append(el('strong','表示例 · 実測データではありません'),el('p','計測後に、実際のAPIの流れと処理時間を表示します。'));panel.append(banner);
 const wrap=el('div',null,'scenario-preview-wrap');
 // Static illustration only; no API or log content is inserted as HTML.
 wrap.innerHTML=`<svg class="scenario-preview-graph" viewBox="0 0 1080 330" role="img" aria-label="表示例：ログインから一覧取得へ進み、詳細取得と検索に分岐するAPIの流れ">
 <defs><marker id="example-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M 0 0 L 10 5 L 0 10" fill="#738b99"/></marker></defs>
 <g stroke="#59656e" stroke-dasharray="3 8" opacity=".4"><path d="M150 20V310M500 20V310M870 20V310"/></g>
 <g fill="none" stroke="#738b99" stroke-width="2" marker-end="url(#example-arrow)"><path d="M168 155 C280 155 370 155 482 155"/><path d="M518 155 C665 155 700 80 852 80"/><path d="M518 155 C665 155 700 245 852 245"/></g>
 <g fill="#9dc5b7" stroke="#293036" stroke-width="5"><circle cx="150" cy="155" r="14"/><circle cx="500" cy="155" r="18"/><circle cx="870" cy="80" r="14"/><circle cx="870" cy="245" r="14"/></g>
 <g text-anchor="middle" font-size="15"><text x="150" y="195">POST /login</text><text x="500" y="199">GET /items</text><text x="870" y="120">GET /items/:id</text><text x="870" y="285">GET /search</text></g>
 <g text-anchor="middle" class="sample-sub"><text x="150" y="219">回数 — · 平均 — ms</text><text x="500" y="223">回数 — · 平均 — ms</text><text x="870" y="144">回数 — · 平均 — ms</text><text x="870" y="309">回数 — · 平均 — ms</text></g></svg>`;
 panel.append(wrap);
}
// Breadth-first layers describe observed connections, not a causal or time axis.
function layoutScenario(graph,edges){
 const incoming=new Set(edges.map(e=>e.to_route)),layers=new Map(),queue=[];
 const walk=root=>{layers.set(root,0);queue.push(root);while(queue.length){const from=queue.shift();for(const e of edges.filter(e=>e.from_route===from)){if(!layers.has(e.to_route)){layers.set(e.to_route,layers.get(from)+1);queue.push(e.to_route);}}}};
 for(const route of graph.routes)if(!incoming.has(route)&&!layers.has(route))walk(route);
 for(const route of graph.routes)if(!layers.has(route))walk(route);
 const columns=[];for(const route of graph.routes){const i=layers.get(route);(columns[i]??=[]).push(route);}
 const nodes=new Map();let height=300;
 for(const [column,routes]of columns.entries()){let y=65;for(const route of routes){const rows=graph.metrics.get(route)||[],h=75+rows.length*38;nodes.set(route,{x:70+column*310,y,h});y+=h+65;}height=Math.max(height,y);}
 return {nodes,width:Math.max(700,columns.length*310+70),height};
}
function transitionWidth(count,maximum){return 1+5*Math.max(0,Number(count)||0)/Math.max(1,maximum);}
function drawScenarioSvg(canvas,graph,q){
 const edges=(q.survey.transitions.items||[]).slice(0,80),layout=layoutScenario(graph,edges),id='flow-arrow-'+(++scenarioRenderId);
 const svg=svgEl('svg',{viewBox:`0 0 ${layout.width} ${layout.height}`,role:'img','aria-label':'APIの観測された接続とサーバー別処理時間'});
 svg.style.width=Math.max(canvas.clientWidth-36,layout.width*.8)*scenarioPrefs.scale+'px';
 const defs=svgEl('defs'),marker=svgEl('marker',{id,viewBox:'0 0 10 10',refX:9,refY:5,markerWidth:8,markerHeight:8,markerUnits:'userSpaceOnUse',orient:'auto'});marker.append(svgEl('path',{d:'M0 0 L10 5 L0 10',fill:'#82949d'}));defs.append(marker);svg.append(defs);
 const maxTransitions=Math.max(1,...edges.map(e=>Number(e.count)||0));
 const qualities=q.survey.quality?.rows||[],peak=Math.max(1,...[...graph.metrics.values()].flat().map(r=>r.total_ms||0));
 for(const [i,e]of edges.entries()){
  const a=layout.nodes.get(e.from_route),b=layout.nodes.get(e.to_route),forward=b.x>a.x;
  const x=a.x+250,y=a.y+22,tx=forward?b.x:b.x+250,ty=b.y+22;
  const d=forward?`M${x} ${y} C${x+50} ${y} ${tx-50} ${ty} ${tx} ${ty}`:`M${x} ${y} C${x+60+i%3*15} ${y-65} ${tx+65} ${ty-65} ${tx} ${ty}`;
  const quality=qualities.filter(r=>r.labels?.from===e.from_route&&r.labels?.to===e.to_route),uncertain=!graph.version||['transition.overlap_count','transition.ambiguous_count'].some(metric=>{const r=quality.find(r=>r.metric===metric);return !r||r.value>0});
  const path=svgEl('path',{d,fill:'none',stroke:'#82949d','stroke-width':transitionWidth(e.count,maxTransitions),'stroke-opacity':.65,'stroke-dasharray':uncertain?'5 5':'none','marker-end':`url(#${id})`});path.append(svgEl('title',{},`${e.from_route} → ${e.to_route} · 初回 ${e.count}回${uncertain?' · 順序注意':''}`));svg.append(path);
  if(scenarioPrefs.edgeCounts)svg.append(svgEl('text',{x:(x+tx)/2,y:(y+ty)/2-8,class:'flow-small'},num(e.count)+'回'));
 }
 for(const [route,n]of layout.nodes){
  const rows=graph.metrics.get(route),g=svgEl('g',{transform:`translate(${n.x} ${n.y})`,tabindex:0,role:'button','aria-label':route+' のHTTP詳細',class:'flow-api'});
  g.append(svgEl('rect',{width:250,height:n.h,rx:10,fill:'#30363b',stroke:'#4a555e'}),svgEl('circle',{cx:0,cy:22,r:5,fill:'#9dc5b7'}));
  const title=svgEl('text',{x:14,y:25,class:'flow-route'},route.length>29?route.slice(0,28)+'…':route);title.append(svgEl('title',{},route));g.append(title);
  if(!rows.length)g.append(svgEl('text',{x:14,y:57,class:'flow-small'},'選択したベンチでは未取得'));
  for(const [i,r]of rows.entries()){const y=51+i*38;g.append(svgEl('text',{x:14,y,class:'flow-small'},`${r.node} · ${num(r.count)}回`),svgEl('text',{x:14,y:y+16,class:'flow-value'},`平均 ${num(r.avg_ms,1)} ms · p95 ${num(r.p95_ms,1)} ms`));}
  const max=Math.max(0,...rows.map(r=>r.total_ms||0));g.append(svgEl('rect',{x:14,y:n.h-29,width:222,height:3,rx:1.5,fill:'#424b52'}),svgEl('rect',{x:14,y:n.h-29,width:222*max/peak,height:3,rx:1.5,fill:'#b9a486'}),svgEl('text',{x:14,y:n.h-10,class:'flow-small'},rows.length?`最大サーバー合計 ${num(max,0)} ms`:'合計 —'));
  const open=()=>openScenarioRoute(route,rows,q);g.addEventListener('click',open);g.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();open()}});svg.append(g);
 }
 canvas.replaceChildren(svg);
}
function renderScenarioGraph(parent,q){
 const survey=q.survey||{},panel=insightPanel(parent,'transitions','APIの流れと処理時間',survey.run?`構造 ${survey.run}`:'未計測');
 if(!survey.transitions?.items?.length){renderScenarioPlaceholder(panel);return;}
 sectionNotice(panel,survey);sectionNotice(panel,q.graph_http);sectionNotice(panel,survey.quality);
 panel.append(el('p',`矢印の太さ・回数は初回surveyの遷移頻度、カードの処理時間は選択run ${q.latest?.short_id||q.latest?.id||'—'}。並行処理の合計時間はベンチ全体の待ち時間ではありません。`,'panel-note'));
 const toolbar=el('div',null,'scenario-toolbar'),label=el('label'),checkbox=el('input');checkbox.type='checkbox';checkbox.checked=scenarioPrefs.edgeCounts;label.append(checkbox,document.createTextNode('初回の遷移回数を表示'));toolbar.append(label);
 for(const [text,scale]of [['縮小',.75],['等倍',1],['拡大',1.25]]){const button=el('button',text);button.type='button';button.addEventListener('click',()=>{scenarioPrefs.scale=scale;const svg=canvas.querySelector('svg');if(svg)svg.style.width=`${svg.viewBox.baseVal.width*scale}px`});toolbar.append(button);}
 const download=el('button','Mermaidを保存');download.type='button';toolbar.append(download);const settings=disclosure(panel,'図の表示設定・保存','scenario-settings');settings.className='scenario-settings';settings.append(toolbar);
 const note=el('p',null,'scenario-summary'),canvas=el('div',null,'scenario-canvas');panel.append(note,canvas);
 const disclosureNode=disclosure(panel,'Mermaidソースと読み方','scenario-source'),source=el('pre',null,'scenario-source');disclosureNode.append(source,el('p','線幅は表示中の最大遷移回数を基準に1〜6pxで表します。バーは各APIで最も合計時間が大きいサーバーの値を、図全体の最大値に対して表示します。配置は接続関係に基づき、時間軸ではありません。破線は重複実行・同時刻・旧ログなどで順序を確定できない矢印です。実線も観測順であり、因果関係の保証ではありません。','panel-note'));
 let graph;
 async function draw(){
  graph=buildScenario(q,scenarioPrefs.edgeCounts);source.textContent=graph.source;
  note.textContent=`${graph.routes.length} API · ${Math.min(80,survey.transitions.items.length)} 遷移 · 順序注意 ${graph.uncertain}本。ノードをクリックしてHTTP詳細へ。${graph.truncated?' 遷移は上位の一部です。':''}${!graph.version?' 旧形式のsurveyのため、すべての矢印を破線で表示しています。':''}`;
  drawScenarioSvg(canvas,graph,q);
 }
 checkbox.addEventListener('change',()=>{scenarioPrefs.edgeCounts=checkbox.checked;draw()});
 download.addEventListener('click',()=>{const url=URL.createObjectURL(new Blob([graph.source+'\n'],{type:'text/plain;charset=utf-8'}));const link=el('a');link.href=url;link.download='scenario-'+(q.latest.short_id||'run')+'.mmd';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000)});
 draw();
 const extra=(q.graph_http?.rows||[]).filter(row=>!graph.metrics.has(`${row.method} ${row.route}`));
 if(extra.length){const more=disclosure(panel,`初回の図にない計測API（${extra.length}行）`,'scenario-unmapped');insightTable(more,['node','API','回数','合計 ms'],extra.map(r=>[r.node,textCell(`${r.method} ${r.route}`),num(r.count),num(r.total_ms,1)]));}
}
