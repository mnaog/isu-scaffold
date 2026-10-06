const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const context=vm.createContext({num:v=>v==null?'—':String(v)});
vm.runInContext(fs.readFileSync(__dirname+'/../scripts/operations/board-scenario.js','utf8'),context);
const build=(q,counts=false)=>context.buildScenario(q,counts);
const edge={from_route:'GET /a',to_route:'POST /b',count:17};
const q=()=>({latest:{id:'selected'},survey:{run:{id:'initial'},transitions:{items:[edge]},quality:{rows:[{metric:'transition.ordering_version',value:2},...['overlap_count','ambiguous_count'].map(name=>({metric:'transition.'+name,value:0,labels:{from:edge.from_route,to:edge.to_route}}))]}},graph_http:{rows:[{method:'GET',route:'/a',node:'n1',count:99,p95_ms:12,total_ms:30},{method:'GET',route:'/a',node:'n2',count:7,p95_ms:80,total_ms:100}]}});
test('topology stays from survey; labels preserve per-node selected run measurements',()=>{
 const graph=build(q());assert.equal(graph.routes.length,2);assert.match(graph.source,/n1 \| 99回/);assert.match(graph.source,/n2 \| 7回/);assert.match(graph.source,/p95 12ms/);assert.match(graph.source,/p95 80ms/);assert.match(graph.source,/選択runの計測なし/);assert.equal(graph.uncertain,0);assert.doesNotMatch(graph.source,/survey 17回/);assert.match(build(q(),true).source,/survey 17回/);
});
test('legacy, overlapping, and missing quality all produce uncertain edges',()=>{
 for(const mode of ['legacy','overlap','missing']){const data=q();if(mode==='legacy')delete data.survey.quality;else if(mode==='overlap')data.survey.quality.rows[1].value=3;else data.survey.quality.rows.pop();const graph=build(data);assert.equal(graph.uncertain,1);assert.match(graph.source,/-\.->/);}
});
test('log strings cannot add Mermaid syntax or HTML',()=>{
 const data=q();data.survey.transitions.items=[{...edge,from_route:'GET /a"]\nX-->Y<script>&#34;'}];const graph=build(data);assert.doesNotMatch(graph.source,/<script>/);assert.doesNotMatch(graph.source,/\nX-->/);assert.match(graph.source,/#34;/);assert.match(graph.source,/#10;/);
});
test('graph size is bounded and reported as partial',()=>{
 const data=q();data.survey.transitions.items=Array.from({length:90},(_,i)=>({...edge,to_route:'GET /'+i}));const graph=build(data);assert.equal(graph.routes.length,81);assert.equal(graph.truncated,true);
});
test('layout terminates on cycles, self-links and disconnected routes without overlapping cards',()=>{
 const data=q();data.survey.transitions.items=[edge,{from_route:'POST /b',to_route:'GET /a',count:1},{from_route:'GET /alone',to_route:'GET /alone',count:1}];
 const graph=build(data),layout=context.layoutScenario(graph,data.survey.transitions.items);
 assert.equal(layout.nodes.size,3);
 for(const a of layout.nodes.values()){assert.ok(a.y+a.h<=layout.height);for(const b of layout.nodes.values())if(a!==b&&a.x===b.x)assert.ok(a.y+a.h<=b.y||b.y+b.h<=a.y);}
});
