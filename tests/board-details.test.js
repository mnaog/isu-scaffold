const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const ctx=vm.createContext({num:(v,d=0)=>v==null?'—':Number(v).toLocaleString('en-US',{maximumFractionDigits:d}),time:()=> '12:00'});
const source=fs.readFileSync(__dirname+'/../scripts/operations/board-details.js','utf8');
vm.runInContext(source.slice(0,source.indexOf("document.querySelector('#previous-run').addEventListener")),ctx);

test('saved analysis is explicitly distinguished from an unrelated manual comparison',()=>{
 const q={brief:{review:{latest_analysis:{base_short_id:'12345678'}}},base:{id:'run-87654321'}};
 assert.match(ctx.analysisComparisonNote(q),/12345678/);
 q.base={id:'run-12345678'};assert.equal(ctx.analysisComparisonNote(q),'');
 q.base=null;assert.match(ctx.analysisComparisonNote(q),/異なります/);
 q.brief.review.latest_analysis={body:'no base'};q.base={id:'chosen'};
 assert.match(ctx.analysisComparisonNote(q),/比較元の記録がありません/);
});
test('analysis auto mode reports missing analysis without inventing a baseline',()=>{
 assert.match(ctx.analysisComparisonNote({base_mode:'analysis'}),/まだ記録されていません/);
 assert.equal(ctx.analysisComparisonNote({base_mode:'manual'}),'');
});
test('count and average pairs distinguish growth, speedup, zero, and missing sides',()=>{
 assert.equal(ctx.metricPair(50,200),'50 → 200');
 assert.equal(ctx.metricPair(10,5,2),'10 → 5');
 assert.equal(ctx.metricPair(null,0),'— → 0');
 assert.equal(ctx.metricPair(0,undefined),'0 → —');
});
test('analysis completion is independent of adoption and absent state is not pending',()=>{
 assert.equal(ctx.analysisLabel({passed:true,analysis_status:'complete'}),'分析済み');
 assert.equal(ctx.analysisLabel({passed:true,analysis_status:'pending'}),'分析待ち');
 assert.equal(ctx.analysisLabel({passed:true}),'分析状態未取得');
 assert.match(ctx.analysisLabel({passed:false}),/分析不要/);
});
test('stale run and deploy records never claim liveness; actual operation is separate',()=>{
 const d={activity:{deploy:{phase:'switching',recorded_at:1},last_deploy_commit:'abcdef012345'},metrics:{running:[{id:'old-run',started_at:'2026-10-06T10:00:00Z'}]}};
 const old=ctx.operationLines(d).join('\n');
 assert.match(old,/実行継続は未確認/);assert.doesNotMatch(old,/実行中/);assert.match(old,/最後に成功したdeploy: abcdef01/);
 d.activity.operation={operation:'isuscope run',started_at:'2026-10-06T10:00:00Z'};
 assert.match(ctx.operationLines(d).join('\n'),/ベンチ実行中/);
 assert.doesNotMatch(ctx.operationLines(d).join('\n'),/実行継続は未確認/);
});

function scoreChart(runs){
 const make=(tag,text,cls)=>({tag,text,cls,attrs:{},style:{},children:[],events:{},append(...xs){this.children.push(...xs)},replaceChildren(...xs){this.children=xs},setAttribute(k,v){this.attrs[k]=v},addEventListener(k,fn){this.events[k]=fn}});
 const root=make('div'),context=vm.createContext({document:{querySelector:selector=>selector==='#run-select'?{value:''}:root},el:make,svgEl:(tag,attrs,text)=>Object.assign(make(tag,text),{attrs}),num:v=>String(v),short:v=>v?.slice(0,8),selectedRun:null,chooseRun:id=>context.chosen=id});
 const html=fs.readFileSync(__dirname+'/../scripts/operations/board.html','utf8');
 vm.runInContext(html.slice(html.indexOf('function resultBadge'),html.indexOf('function overviewSelection')),context);
 vm.runInContext(html.slice(html.indexOf('function renderScoreHistory'),html.indexOf('function renderScouts')),context);
 context.renderScoreHistory({score_history:runs});
 const flatten=n=>[n,...n.children.flatMap(flatten)];
 return {nodes:flatten(root),context};
}
test('score history spaces irregular and simultaneous runs equally, including failures',()=>{
 const runs=[['a','2026-01-01T00:00:00Z',100,true],['b','2026-01-01T00:00:00Z',null,false],['c','2026-02-01T00:00:00Z',200,true]].map(([id,started_at,score,passed])=>({id,started_at,score,passed,state:passed?'complete':'failed'}));
 const {nodes,context}=scoreChart(runs);
 const points=nodes.filter(n=>n.attrs.class==='score-point');
 const xs=points.map(n=>n.children[0].attrs.cx);
 assert.equal(xs[1]-xs[0],xs[2]-xs[1]);
 assert.equal(points.length,3);
 assert.match(points[1].attrs['aria-label'],/2回目.*FAIL/);
 points[2].events.click();assert.equal(context.chosen,'c');
 assert.ok(nodes.some(n=>n.text==='2/1 00:00'||String(n.text).includes('2/1')));
});
test('single score point and dense history retain usable geometry',()=>{
 const run={id:'one',started_at:'2026-01-01T00:00:00Z',score:0,passed:true,state:'complete'};
 const single=scoreChart([run]).nodes.find(n=>n.attrs.class==='score-point');
 assert.ok(Number.isFinite(single.children[0].attrs.cx));
 const {nodes}=scoreChart(Array.from({length:100},(_,i)=>({...run,id:String(i)})));
 const points=nodes.filter(n=>n.attrs.class==='score-point');
 assert.ok(points[1].children[0].attrs.cx-points[0].children[0].attrs.cx>=28);
 assert.equal(points.length,100);
});

test('chart highlights selection without duplicating details or numeric x-axis labels',()=>{
 const run={id:'one',started_at:'2026-01-01T00:00:00Z',score:100,passed:true,state:'complete',hypothesis:'inspect one change'};
 const {nodes}=scoreChart([run]);
 const point=nodes.find(n=>n.attrs.class==='score-point');
 assert.equal(point.attrs['aria-pressed'],'true');
 assert.equal(point.events.mouseenter,undefined);
 assert.equal(point.events.focus,undefined);
 assert.ok(!nodes.some(n=>n.text==='inspect one change'));
 assert.equal(nodes.filter(n=>n.attrs.class==='score-axis'&&n.attrs.y===204).length,0);
 assert.ok(nodes.some(n=>n.attrs.class==='score-axis'&&n.attrs.y===208&&String(n.text).includes(':')));
});
test('comparison rows get their identity back from key on both sides',()=>{
 const row=ctx.withIdentity({key:{node:'app1',digest:'select 1',window:'-'},presence:'added',base:null,candidate:{calls:3}});
 assert.equal(row.candidate.node,'app1');assert.equal(row.candidate.digest,'select 1');assert.equal(row.candidate.calls,3);
 assert.equal(row.candidate.window,undefined);assert.equal(row.base,null);
 assert.equal(ctx.withIdentity({key:{node:'app1',window:'load'},base:{calls:1},candidate:{calls:2}}).base.window,'load');
});
test('values shared by every comparison row come back from common',()=>{
 const row=ctx.withIdentity({key:{digest:'select 1'},base:{calls:1},candidate:{calls:2}},{node:'app1',window:'load'});
 assert.equal(row.candidate.node,'app1');assert.equal(row.base.window,'load');assert.equal(row.candidate.digest,'select 1');
});
test('deltas isuscope leaves out are computed from both sides',()=>{
 const d=ctx.valueDelta(200,150);assert.equal(d.delta,-50);assert.equal(d.delta_percent,-25);
 assert.equal(ctx.valueDelta(0,5).delta_percent,null);assert.deepEqual({...ctx.valueDelta(null,5)},{});
});
