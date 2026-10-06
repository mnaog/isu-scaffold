// Presentation of saved isuscope results only. No live server polling or collection.
let chartProbes=[];
function probeCharts(seconds){for(const probe of chartProbes)probe(seconds);}
const percentText=value=>value==null?'—':num(value,1)+'%';
const insightColors=['#eca17d','#9dc5aa','#aebfe8','#d5b77c','#d39ebe','#8ac7cf'];
function insightPanel(parent,id,title,context='選択run'){
 const panel=metricPanel(title,context);panel.id='detail-'+id;parent.append(panel);return panel;
}
function sectionNotice(panel,data){
 if(data?.error)panel.append(el('p',data.error,'insight-warning'));
 if(data?.truncated)panel.append(el('p',`取得上限のため一部を表示しています（全${num(data.total_count)}件）。`,'insight-warning'));
 for(const warning of data?.warnings||[])panel.append(el('p',warning,'insight-warning'));
}
function textCell(text,cls='metric-name'){return el('div',text,cls);}
function insightTable(panel,headers,rows){if(rows.length)table(panel,headers,rows);else panel.append(el('p','このrunには表示できる計測がありません。','panel-note'));}
function lineChart(parent,title,unit,series,window){
 const card=el('div',null,'mini-chart'),heading=el('div',null,'chart-heading');heading.append(el('h3',`${title} · ${unit}`));card.append(heading);parent.append(card);
 const points=series.flatMap(s=>s.points).filter(p=>Number.isFinite(p.y));
 if(!points.length){card.classList.add('chart-missing');card.append(el('p','未取得','panel-note'));return;}
 const x0=window?.from_seconds??Math.min(...points.map(p=>p.x)),x1=Math.max(x0+1,window?.to_seconds??Math.max(...points.map(p=>p.x)));
 const y0=Math.min(0,...points.map(p=>p.y)),y1=Math.max(1,...points.map(p=>p.y))*1.08;
 const px=x=>45+(x-x0)/(x1-x0)*505,py=y=>116-(y-y0)/(y1-y0)*100;
 const svg=svgEl('svg',{viewBox:'0 0 570 150',role:'img','aria-label':`${title}。負荷走行開始からの経過秒。値は各点のツールチップに表示。`});card.append(svg);
 for(let i=0;i<3;i++){const value=y0+(y1-y0)*i/2,y=py(value);svg.append(svgEl('line',{x1:45,x2:550,y1:y,y2:y,class:'score-grid'}),svgEl('text',{x:39,y:y+3,'text-anchor':'end',class:'score-axis'},num(value,1)));}
 for(const x of [x0,(x0+x1)/2,x1])svg.append(svgEl('text',{x:px(x),y:140,'text-anchor':'middle',class:'score-axis'},num(x-x0,1)+'s'));
 const cursor=svgEl('line',{x1:45,x2:45,y1:12,y2:116,stroke:'var(--accent)','stroke-dasharray':'3 3',visibility:'hidden'});svg.append(cursor);
 const readout=el('div','カーソルで同じ経過秒を照合','chart-probe');card.append(readout);
 chartProbes.push(seconds=>{if(seconds==null){cursor.setAttribute('visibility','hidden');readout.textContent='カーソルで同じ経過秒を照合';return;}const target=x0+seconds;cursor.setAttribute('visibility',target>x1?'hidden':'visible');cursor.setAttribute('x1',px(target));cursor.setAttribute('x2',px(target));readout.textContent=num(seconds,1)+'s · '+series.map(s=>{const p=[...s.points].sort((a,b)=>Math.abs(a.x-target)-Math.abs(b.x-target))[0];return s.name+': '+(p&&Math.abs(p.x-target)<=(window?.bucket_seconds||5)/2&&Number.isFinite(p.y)?num(p.y,2)+' '+unit:'未取得')}).join(' / ');});
 svg.setAttribute('tabindex','0');svg.setAttribute('aria-label',title+'。左右矢印で同じ経過秒を照合');let probe=0;
 svg.addEventListener('pointermove',e=>{const box=svg.getBoundingClientRect();probe=Math.max(0,Math.min(x1-x0,((e.clientX-box.left)/box.width*570-45)/505*(x1-x0)));probeCharts(probe)});
 svg.addEventListener('pointerleave',()=>probeCharts(null));svg.addEventListener('keydown',e=>{if(!['ArrowLeft','ArrowRight'].includes(e.key))return;e.preventDefault();probe=Math.max(0,Math.min(x1-x0,probe+(e.key==='ArrowRight'?1:-1)*(window?.bucket_seconds||5)));probeCharts(probe)});
 const legend=el('div',null,'chart-legend');heading.append(legend);
 series.forEach((s,index)=>{
  const color=insightColors[index%insightColors.length],label=el('span',s.name);label.style.color=color;legend.append(label);
  let d='',previous=null;
  for(const p of [...s.points].sort((a,b)=>a.x-b.x)){
   if(!Number.isFinite(p.y)){previous=null;continue;}
   const gap=previous!=null&&p.x-previous>(window?.bucket_seconds||5)*1.5;
   d+=(previous==null||gap?'M':'L')+px(p.x)+' '+py(p.y)+' ';previous=p.x;
   const dot=svgEl('circle',{cx:px(p.x),cy:py(p.y),r:2.3,fill:color});dot.append(svgEl('title',{},`${s.name} · ${num(p.x-x0,1)}s · ${num(p.y,2)} ${unit}`));svg.append(dot);
  }
  svg.append(svgEl('path',{d,fill:'none',stroke:color,'stroke-width':1.8}));
 });
}
function renderTimeline(parent,data={}){
 const load=data.window?.name==='load';
 const panel=insightPanel(parent,'timeline',load?'負荷走行のタイムライン':'ベンチ全体のタイムライン',`${(data.window?.name||'whole').toUpperCase()} · 5秒区間`);sectionNotice(panel,data);
 panel.append(el('p',(load?'横軸は負荷走行開始からの経過秒。':'横軸はベンチ開始からの経過秒。')+'点にカーソルを合わせると値を表示します。p95は区間内の分位値の最大で、全要求をまとめ直したp95ではありません。','panel-note'));
 if(data.window?.edges==='approximate')panel.append(el('p','区間境界は近似です。境界をまたぐbucketがあります。','insight-warning'));
 const rows=data.rows||[],nodes=[...new Set(rows.map(r=>r.node))].sort(),grid=el('div',null,'chart-grid');panel.append(grid);
 const specs=[['リクエスト数','件/区間','http_requests'],['応答時間 p95の最大','ms','http_p95_max_ms'],['HTTPエラー数','件/区間','http_errors'],['CPU 平均','%','cpu_busy_avg_percent'],['SQL 呼び出し数','回/区間','db_calls'],['SQL 合計時間','ms/区間','db_duration_total_ms'],['メモリ使用量 平均','MiB','memory_used_avg_mib'],['Disk util. 最大','%','disk_util_max_percent']];
 for(const [title,unit,key]of specs)lineChart(grid,title,unit,nodes.map(node=>({name:node,points:rows.filter(r=>r.node===node).map(r=>({x:r.from_seconds,y:r[key]}))})),data.window);
}
function renderHosts(parent,b){
 const panel=insightPanel(parent,'hosts','ホスト・サービスの負荷',(b.hosts_window||'whole').toUpperCase()),grid=el('div',null,'insight-grid');panel.append(grid);
 for(const h of b.hosts||[]){
  const card=el('div',null,'insight-card');grid.append(card);card.append(el('h3',h.node),el('div',percentText(h.cpu_busy_avg_percent),'insight-number'),el('p','CPU 平均'));
  const bar=el('div',null,'insight-bar'),fill=el('i');fill.style.width=Math.min(100,Math.max(0,h.cpu_busy_avg_percent||0))+'%';bar.append(fill);card.append(bar);
  dataList(card,[['CPU peak',percentText(h.cpu_busy_max_percent)],['最繁忙core',percentText(h.busiest_core_max_percent)],['I/O wait',percentText(h.iowait_avg_percent)],['steal',percentText(h.steal_avg_percent)],['メモリpeak',h.memory_used_max_mib==null?'—':num(h.memory_used_max_mib,1)+' MiB'],['Disk peak',percentText(h.disk_util_max_percent)],['Load peak',num(h.load1_max,2)],['PSI',h.pressure?`${h.pressure.resource} ${num(h.pressure.max_percent,1)}%`:'—']]);
  for(const service of h.top_services||[])card.append(el('p',`${service.service} · CPU peak ${num(service.cpu_cores_max,2)} cores`));
 }
 const quiet=b.quiet_hosts;
 if(quiet){
  const card=el('div',null,'insight-card');grid.append(card);card.append(el('h3',`待機中 · ${quiet.nodes.join(', ')}`),el('div',percentText(quiet.cpu_busy_max_percent),'insight-number'),el('p','CPU peak（この中の最大）'));
  dataList(card,[['最繁忙core',percentText(quiet.busiest_core_max_percent)],['I/O wait',percentText(quiet.iowait_avg_percent)],['PSI peak',percentText(quiet.pressure_max_percent)],['Disk peak',percentText(quiet.disk_util_max_percent)]]);
 }
 if(!b.hosts?.length&&!quiet)panel.append(el('p','ホスト計測がありません。','panel-note'));
}
function renderMysql(parent,data={}){
 const panel=insightPanel(parent,'mysql','MySQL ステータス',`${(data.window?.name||'whole').toUpperCase()} · 5秒区間`);sectionNotice(panel,data);
 const definitions=[['mysql.threads_running','実行中thread','threads'],['mysql.threads_connected','接続thread','threads'],['mysql.queries_per_second','クエリ数','回/s'],['mysql.row_lock_waits_per_second','行ロック待ち','回/s'],['mysql.row_lock_time_ms_per_second','行ロック待ち時間','ms/s'],['mysql.log_waits_per_second','ログ待ち','回/s'],['mysql.buffer_pool_reads_per_second','buffer pool 物理読み込み','回/s'],['mysql.buffer_pool_read_requests_per_second','buffer pool 読み込み要求','回/s'],['mysql.data_fsyncs_per_second','fsync','回/s']];
 const grid=el('div',null,'chart-grid');panel.append(grid);
 for(const [metric,title,unit]of definitions){const rows=(data.rows||[]).filter(r=>r.metric===metric),groups=new Map();
  for(const r of rows){const name=[r.node,r.labels?.collector].filter(Boolean).join(' · ');if(!groups.has(name))groups.set(name,[]);groups.get(name).push({x:r.from_seconds,y:r.value});}
  lineChart(grid,title,unit,[...groups].map(([name,points])=>({name,points})),data.window);
 }
}
function renderExpandedMetrics(root,q,position){
 const b=q.brief||{};
 if(position==='before'){
  renderScenarioGraph(root,q);renderHosts(root,b);renderTimeline(root,q.timeline);return;
 }
 renderMysql(root,q.mysql);
 const upstream=insightPanel(root,'upstream','nginx upstream','試行単位 · 選択run');sectionNotice(upstream,b.upstreams);
 upstream.append(el('p','retryを含む試行単位の値です。cache HIT/MISSは現在の標準計測には含まれません。','panel-note'));
 insightTable(upstream,['入口node','接続先','試行数','再試行','接続 p95 ms','ヘッダー p95 ms','応答 p95 ms'],(b.upstreams?.items||[]).map(r=>[r.node,textCell(r.upstream),num(r.requests),num(r.retried_requests),num(r.connect_p95_ms,2),num(r.header_p95_ms,2),num(r.response_p95_ms,2)]));
 const clients=insightPanel(root,'clients','クライアント接続',(b.hosts_window||'whole').toUpperCase());
 insightTable(clients,['node','使用中接続 平均','使用中接続 peak','新規接続 /s','要求 / 接続','次要求まで p95 ms'],(b.clients||[]).map(r=>[r.node,num(r.connections_in_use_avg,1),num(r.connections_in_use_max,1),num(r.connections_opened_per_second,1),num(r.requests_per_connection_avg,1),num(r.request_gap_ms?.p95,2)]));
 const cpu=insightPanel(root,'cpu','CPUプロファイル · 上位関数','選択run');sectionNotice(cpu,b.cpu);
 insightTable(cpu,['node','process / binary','symbol','sample %','source'],(b.cpu?.items||[]).map(r=>[r.node,textCell([r.process,r.binary].filter(Boolean).join(' / ')),textCell(r.symbol),num(r.sample_percent,2),r.source]));
 const health=insightPanel(root,'coverage','計測の取得状況','選択run');sectionNotice(health,b.coverage_issues);
 if(b.coverage_issues?.total_count===0)health.append(el('p','報告された計測の問題はありません。','panel-note'));
 else insightTable(health,['項目','collector','node','状態','不足・エラー'],(b.coverage_issues?.items||[]).map(r=>[r.section,r.collector,(r.nodes||[]).join(', '),r.status,textCell([...(r.missing_metrics||[]),...(r.errors||[])].join(' / '))]));
}
