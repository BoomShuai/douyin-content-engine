#!/usr/bin/env node
"use strict";

// Offline unit checks run the actual browser functions; no third-party packages.
// The browser bootstrap is excluded so no DOM, server, or model is required.
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const assert = require("node:assert/strict");

const frontendPath = path.resolve(__dirname, "../douyin_engine/web/app.js");
const source = fs.readFileSync(frontendPath, "utf8");
const bootstrapMarker = '\ndocument.querySelectorAll(".nav-item").forEach(node=>node.addEventListener';
const bootstrap = source.indexOf(bootstrapMarker);
assert.ok(bootstrap > 0, "Browser bootstrap marker must exist; do not silently test an empty source.");
const context = {Intl, Date, Number, String, Map, JSON, Array, Math};
vm.createContext(context);
vm.runInContext(source.slice(0, bootstrap), context, {filename:frontendPath});
const evaluate = code => vm.runInContext(code, context);
const json = value => JSON.stringify(value);
const sameJSON = (actual, expected) => assert.equal(json(actual), json(expected));
const results = [];
function test(name, action) {
  try {action();results.push({name,status:"passed"});}
  catch(error) {results.push({name,status:"failed",message:error.message});}
}
function resetComments() {
  evaluate(`state.reviews.clear();state.query='';state.filter='all';state.data={summary:{total:4},comments:[
    {id:'empty',text:'https://example.invalid',sarcasm:{label:null,score:null,available:false,review_required:true,review_priority:'high'}},
    {id:'zero',text:'服务不好',context:'售后体验',sarcasm:{label:0,score:0.01,available:true,review_required:true,review_priority:'normal'}},
    {id:'one',text:'真是优秀的服务',sarcasm:{label:1,score:0.8,available:true,review_required:true,review_priority:'normal'}},
    {id:'high',text:'这个服务可真好',sarcasm:{label:1,score:0.5,available:true,review_required:true,review_priority:'high'}}]};`);
}

test("null label and unavailable input abstain",()=>{
  assert.equal(evaluate("machineLabel({sarcasm:{label:null,score:null,available:false}})"),"无法判定");
  assert.equal(evaluate("modelScore({sarcasm:{label:null,score:null,available:false}})"),"无法判定");
});
test("availability overrides a contradictory numeric label",()=>{
  assert.equal(evaluate("machineLabel({sarcasm:{label:0,score:0,available:false}})"),"无法判定");
  assert.equal(evaluate("modelScore({sarcasm:{label:1,score:0.9,available:false}})"),"无法判定");
});
test("missing label does not become non-sarcasm",()=>{
  assert.equal(evaluate("machineLabel({})"),"无法判定");
  assert.equal(evaluate("machineLabel({sarcasm:{label:null,available:true}})"),"无法判定");
});
test("rules null score remains unavailable",()=>assert.equal(evaluate('modelScore({sarcasm:{label:0,score:null,backend:"rules"}})'),"不提供"));
test("genuine zero model score remains a numeric zero",()=>assert.equal(evaluate("modelScore({sarcasm:{label:0,score:0,available:true}})"),"0.000"));
test("non-finite model score is never rendered as a probability",()=>assert.equal(evaluate("modelScore({sarcasm:{label:1,score:NaN,available:true}})"),"不提供"));
test("null benchmark metric does not become zero",()=>assert.equal(evaluate("benchmarkMetric(null)"),"未提供"));

for(const [name,value,expected] of [
  ["formula equals","=SUM(A1:A2)",'"\'=SUM(A1:A2)"'],
  ["formula plus after spaces","  +cmd",'"\'  +cmd"'],
  ["formula minus","-1+2",'"\'-1+2"'],
  ["formula at","@evil",'"\'@evil"'],
  ["formula after tab","\t=1",'"\'\t=1"'],
  ["formula after carriage return","\r+1",'"\'\r+1"'],
  ["quote and comma escaping",'normal, "quoted"','"normal, ""quoted"""'],
  ["null empty cell",null,'""'],
  ["numeric zero",0,'"0"'],
  ["benign Chinese text","服务很好",'"服务很好"'],
]) test(`CSV ${name}`,()=>assert.equal(evaluate(`csvCell(${json(value)})`),expected));

for(const [filter,expected] of [
  ["unscorable",["empty"]],
  ["non_sarcasm",["zero"]],
  ["sarcasm",["one","high"]],
  ["high_priority",["empty","high"]],
  ["review",["empty","high","zero","one"]],
]) test(`filter ${filter} and review ordering`,()=>{
  resetComments();evaluate(`state.filter=${json(filter)}`);sameJSON(evaluate("filteredComments().map(c=>c.id)"),expected);
});
test("search includes context",()=>{
  resetComments();evaluate("state.query='售后'");sameJSON(evaluate("filteredComments().map(c=>c.id)"),["zero"]);
});
test("reviewed rows leave pending priority queue",()=>{
  resetComments();evaluate("state.reviews.set('empty',{label:0,note:'人工记录'});state.filter='high_priority'");
  sameJSON(evaluate("filteredComments().map(c=>c.id)"),["high"]);
});
test("export retains original null and adds an independent human label",()=>{
  resetComments();evaluate("state.reviews.set('empty',{label:0,note:'人工记录',status:'human_unverified'})");
  const payload=evaluate("exportPayload()");
  assert.equal(payload.comments[0].sarcasm.label,null);
  assert.equal(payload.comments[0].human_review.label,0);
  assert.equal(payload.human_reviews[0].comment_id,"empty");
  assert.equal(payload.human_reviews[0].status,"human_unverified");
  assert.equal(evaluate("state.data.comments[0].human_review"),undefined);
});
test("conflicting human label does not overwrite machine label",()=>{
  resetComments();evaluate("state.reviews.set('one',{label:0,note:'不同判断'})");
  assert.equal(evaluate("exportPayload().comments[2].sarcasm.label"),1);
  assert.equal(evaluate("exportPayload().comments[2].human_review.label"),0);
});
test("time is converted to Asia Shanghai",()=>assert.equal(evaluate("dateText('2026-08-30T18:00:00Z')"),"2026/08/31 02:00"));

function segmentIndices(records,key,signed) {
  return evaluate(`timelineSegments(${json(records)},${json(key)},${signed}).map(segment=>segment.map(point=>point.index))`);
}
test("empty bucket breaks sentiment line and adds no zero sentiment point",()=>sameJSON(segmentIndices([
  {count:1,sentiment_mean:0.5},{count:0,sentiment_mean:0,is_empty:true},{count:1,sentiment_mean:-0.5},
],"sentiment_mean",true),[[0],[2]]));
test("explicit is_empty also breaks a contradictory sentiment row",()=>sameJSON(segmentIndices([
  {count:1,sentiment_mean:0.5},{count:1,sentiment_mean:0,is_empty:true},{count:1,sentiment_mean:-0.5},
],"sentiment_mean",true),[[0],[2]]));
test("zero comment count remains an actual count point",()=>sameJSON(segmentIndices([
  {count:1},{count:0,is_empty:true},{count:2},
],"count",false),[[0,1,2]]));
test("sparse gap breaks sentiment line",()=>sameJSON(segmentIndices([
  {count:1,sentiment_mean:0.5},{count:1,sentiment_mean:-0.5,gap_before:{empty_buckets:40}},{count:1,sentiment_mean:0.2},
],"sentiment_mean",true),[[0],[1,2]]));
test("sparse gap also breaks count line",()=>sameJSON(segmentIndices([
  {count:1},{count:2,gap_before:{empty_buckets:40}},{count:1},
],"count",false),[[0],[1,2]]));
test("null sentiment value is not cast to neutral",()=>sameJSON(segmentIndices([
  {count:1,sentiment_mean:0.5},{count:1,sentiment_mean:null},{count:1,sentiment_mean:-0.5},
],"sentiment_mean",true),[[0],[2]]));
test("empty timeline returns no segments",()=>sameJSON(segmentIndices([],"sentiment_mean",true),[]));
test("frontend contains no unsafe HTML insertion or executable evaluation",()=>{
  assert.doesNotMatch(source,/\.innerHTML\s*=|\.outerHTML\s*=|\beval\s*\(/u);
  const html=fs.readFileSync(path.resolve(__dirname,"../douyin_engine/web/index.html"),"utf8");
  assert.doesNotMatch(html,/<style\b|\sstyle\s*=/iu);
});

const failed=results.filter(result=>result.status==="failed").length;
process.stdout.write(JSON.stringify({suite:"frontend_offline",source:"douyin_engine/web/app.js",total:results.length,passed:results.length-failed,failed,tests:results},null,2)+"\n");
process.exitCode=failed?1:0;
