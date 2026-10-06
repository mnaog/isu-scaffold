const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const ctx=vm.createContext({num:(v,d=0)=>v==null?'—':Number(v).toLocaleString('en-US',{maximumFractionDigits:d}),time:()=> '12:00'});
const source=fs.readFileSync(__dirname+'/../scripts/operations/board-details.js','utf8');
vm.runInContext(source.slice(0,source.indexOf("document.querySelector('#previous-run').addEventListener")),ctx);

test('saved analysis is explicitly distinguished from an unrelated manual comparison',()=>{
 const q={brief:{review:{latest_analysis:{base_run_id:'run-12345678'}}},base:{id:'run-87654321'}};
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
