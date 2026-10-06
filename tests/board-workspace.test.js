const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const ctx=vm.createContext({num:v=>String(v),metricRows:(data,compare)=>(data.rows||[]).map(r=>compare?r:{candidate:r})});
vm.runInContext(fs.readFileSync(__dirname+'/../scripts/operations/board-workspace.js','utf8'),ctx);
const row={candidate:{node:'n1',method:'GET',route:'/a',count:200,avg_ms:5,total_ms:1000,errors:0},base:{node:'n1',count:50,avg_ms:10,total_ms:500,errors:0}};
test('node scope filters both comparison sides without rewriting run totals or original data',()=>{
 const q={brief:{hosts:[{node:'n1'},{node:'n2'}],coverage_issues:{total_count:2}},latest:{score:100},sections:{http:{data:{rows:[row,{base:{node:'n2'}}],truncated:true,total_count:100}}},timeline:{rows:[{node:'n1'},{node:'n2'}]}};
 const filtered=ctx.workspaceData(q,'n2');
 assert.equal(filtered.sections.http.data.rows.length,1);assert.equal(filtered.timeline.rows.length,1);
 assert.equal(filtered.latest.score,100);assert.equal(filtered.brief.coverage_issues.total_count,2);
 assert.equal(q.sections.http.data.rows.length,2);assert.equal(filtered.sections.http.data.truncated,true);
});
