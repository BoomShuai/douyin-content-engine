"use strict";

// All data-originating text is inserted with textContent, never interpreted as HTML.
const state = {health:null, data:null, source:null, activeSource:null, reviews:new Map(), reviewsExported:true,
  tab:"overview", filter:"all", query:"", page:1, pageSize:15, busy:false, evidence:null, evidenceLoading:false};
const $ = id => document.getElementById(id);
const number = value => Number.isFinite(Number(value)) ? Number(value) : 0;
const fmt = value => number(value).toLocaleString("zh-CN");
const tabs = {overview:["分析总览","从评论，读懂内容的细节。","反讽识别、观点证据、样本变化与回复关系，在一个可追溯的工作区中完成。"],
  comments:["评论与观点","每一个判断，都回到原文。","保留机器输出，独立记录人工判断；观点四元组始终附带可核查的原文证据。"],
  timeline:["时序与主题","观察变化，不跳过证据。","按北京时间聚合已导入样本；变化提示不等于真实平台舆情，也不能证明事件因果。"],
  network:["互动关系","只连接样本内已提供的回复。","回复者指向被回复者。互动可见度反映当前样本，不能用于认定幕后操纵。"],
  evidence:["评测与边界","让结果有出处，让局限可见。","查看本地基准证据、数据来源与方法边界，区分已验证能力和仍待验证的问题。"]};
const humanKeys = {available:"证据可用",version:"版本",model:"模型",name:"名称",device:"运行设备",status:"状态",metrics:"评测指标",datasets:"评测数据集",dataset:"数据集",accuracy:"准确率",macro_f1:"Macro-F1",f1_sarcastic:"反讽 F1",precision:"精确率",recall:"召回率",f1:"F1",support:"样本量",methodology:"方法说明",limitations:"局限",warnings:"注意事项",source:"来源",sources:"来源",created_at:"生成时间",generated_at:"生成时间",test_count:"测试样本数",n_samples:"样本数",sha256:"SHA-256",provenance:"可追溯信息",environment:"运行环境",description:"说明",backend:"识别方式",notes:"说明",selection_bias:"模型选择偏差",scope:"使用范围",license:"许可证",licenses:"许可证",results:"结果",evaluation:"评测",metadata:"元数据",sample_count:"样本数",sarcasm_f1:"反讽 F1",model_name:"模型名称",model_id:"模型标识",checkpoint:"权重",counts:"计数",input_count:"输入条数",accepted_count:"接收条数",dropped_count:"剔除条数",duplicate_count:"重复条数",invalid_time_count:"无效时间条数"};

function el(tag, className, text) {
  const node=document.createElement(tag);
  if(className) node.className=className;
  if(text!==undefined && text!==null) node.textContent=String(text);
  return node;
}
function append(parent,...children) {children.flat().forEach(child=>{if(child!==null&&child!==undefined)parent.append(child);});return parent;}
function button(text, className, action) {const node=el("button",className,text);node.type="button";node.addEventListener("click",action);return node;}
function tag(text, kind) {return el("span",`tag ${kind||""}`,text);}
function hasMachineLabel(comment) {return comment.sarcasm?.available!==false&&[0,1].includes(comment.sarcasm?.label);}
function machineLabel(comment) {return !hasMachineLabel(comment)?"无法判定":comment.sarcasm.label===1?"反讽":"非反讽";}
function modelScore(comment,digits=3) {
  const score=comment.sarcasm?.score;
  if(!hasMachineLabel(comment))return "无法判定";
  return typeof score==="number"&&Number.isFinite(score)?score.toFixed(digits):"不提供";
}
function reviewPriority(comment) {
  if(state.reviews.has(comment.id))return 0;
  return !hasMachineLabel(comment)?3:comment.sarcasm?.review_priority==="high"?2:comment.sarcasm?.review_required?1:0;
}
function dateText(value) {
  if(!value) return "时间未知";
  const date=new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : new Intl.DateTimeFormat("zh-CN",{timeZone:"Asia/Shanghai",year:"numeric",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",hour12:false}).format(date);
}
function notify(message,error=false) {
  const target=$("feedback");target.hidden=false;target.className=error?"error":"";target.textContent=message;
}
function toast(message) {$("toast").textContent=message;$("toast").hidden=false;window.clearTimeout(toast.timer);toast.timer=window.setTimeout(()=>{$("toast").hidden=true;},3500);}
async function request(url, options={}) {
  const response=await fetch(url,{...options,headers:{"Accept":"application/json",...(options.body?{"Content-Type":"application/json"}:{}),...(options.headers||{})},cache:"no-store"});
  let payload;
  try {payload=await response.json();} catch(_) {throw new Error(`服务返回了无法读取的响应（HTTP ${response.status}）。请检查本地服务是否仍在运行。`);}
  if(!response.ok) throw new Error(payload.error?.message||`请求失败（HTTP ${response.status}）`);
  return payload;
}
function setBusy(value) {
  state.busy=value;document.body.classList.toggle("busy",value);
  ["analyze-button","demo-button","single-button","backend-select","bucket-select","file-input"].forEach(id=>{$(id).disabled=value;});
  if(!value) $("analyze-button").disabled=!state.source;
  $("analyze-button").textContent=value?"分析中……":"开始分析 →";
  $("source-heading").closest("section").setAttribute("aria-busy",String(value));
}
async function loadHealth() {
  try {
    state.health=await request("/api/health");
    const model=state.health.model||{};const badge=$("health-badge");badge.replaceChildren(el("span","status-dot"));
    const states={ready:"本地模型就绪",loaded:"本地模型已加载",available:"本地模型可用",not_loaded:"模型可用 · 按需加载",lazy:"模型可用 · 按需加载",missing:"服务在线 · 规则可用 / 模型未配置",unavailable:"模型暂不可用",error:"模型加载异常"};
    badge.append(document.createTextNode(states[model.status]||`本地服务在线 · ${model.status||"可用"}`));
    badge.classList.toggle("offline",["unavailable","error"].includes(model.status));
    $("version").textContent=`v${state.health.version||"1.0.1"}`;
    const limits=state.health.limits||{};
    $("data-limit").textContent=limits.max_rows?`每批最多 ${fmt(limits.max_rows)} 条${limits.max_bytes?` · ${Math.round(number(limits.max_bytes)/1048576)} MB`:""} · 仅本机处理`:"仅本机处理 · 不自动采集平台数据";
  } catch(error) {
    $("health-badge").classList.add("offline");$("health-badge").replaceChildren(el("span","status-dot"),document.createTextNode("本地服务未连接"));
    notify("无法连接本地分析服务。请使用项目启动命令启动服务，再刷新页面。",true);
  }
}
function canReplace() {return !state.reviews.size||state.reviewsExported||window.confirm("当前批次有尚未导出的人工复核。继续分析会清除本页复核记录，是否继续？建议先取消并导出。");}
async function loadFile(file) {
  if(!file||state.busy) return;
  const extension=file.name.split(".").pop().toLowerCase();
  if(!["csv","json"].includes(extension)){notify("仅支持 .csv 或 .json 文件，请使用输入模板。",true);return;}
  if(state.health?.limits?.max_bytes&&file.size>state.health.limits.max_bytes){notify(`文件超过 ${Math.round(state.health.limits.max_bytes/1048576)} MB 上限，请拆分后导入。`,true);return;}
  try {
    const content=await file.text();
    if(state.busy) return;
    if(!content.trim()){notify("文件内容为空，请检查后重新选择。",true);return;}
    if(content.includes("\uFFFD")){notify("文件中出现无法按 UTF-8 读取的字符。请将文件另存为 UTF-8 编码后重试，避免静默破坏评论内容。",true);return;}
    state.source={content,format:extension,source_name:"local_import"};
    $("file-name").textContent=file.name;$("file-description").textContent=`${(file.size/1024).toFixed(1)} KB · 已读取，点击“开始分析”提交`;
    $("analyze-button").disabled=false;$("feedback").hidden=true;
  }catch(error){notify(`读取文件失败：${error.message}`,true);}
}
async function analyze(source) {
  if(state.busy||!source||!canReplace()) return;
  setBusy(true);notify("正在执行本地分析。模型首次加载可能需要片刻，大批量数据请耐心等待。");$("feedback").className="loading";
  const started=performance.now();
  try {
    const data=await request("/api/analyze",{method:"POST",body:JSON.stringify({...source,backend:$("backend-select").value,bucket:$("bucket-select").value})});
    if(!data.summary||!Array.isArray(data.comments)) throw new Error("分析响应缺少必要字段，请检查项目版本是否匹配。");
    state.data=data;state.activeSource=source;state.reviews.clear();state.reviewsExported=true;state.page=1;state.filter="all";state.query="";
    renderAll();
    notify(`分析完成 · 接收 ${fmt(data.summary.total)} 条评论 · 本次用时 ${((performance.now()-started)/1000).toFixed(1)} 秒。模型输出仍需结合原文核查。`);
  }catch(error){notify(`分析未完成：${error.message} 没有自动切换到规则模式。${state.data?"下方仍保留上一次成功分析的结果。":""}`,true);}
  finally{setBusy(false);loadHealth();}
}
async function loadDemo() {
  if(state.busy) return;
  try {
    const demo=await request("/api/demo");
    state.source={records:demo.records,source_name:demo.source_name||"synthetic_demo"};
    $("file-name").textContent="人工构造演示样例";$("file-description").textContent=`${fmt(demo.records?.length)} 条功能样例 · 非真实平台采集`;
    await analyze(state.source);
  }catch(error){notify(`演示数据载入失败：${error.message}`,true);}
}
function switchTab(name) {
  if(!tabs[name]) return;
  state.tab=name;
  document.querySelectorAll(".nav-item").forEach(node=>{const active=node.dataset.tab===name;node.classList.toggle("active",active);if(active)node.setAttribute("aria-current","page");else node.removeAttribute("aria-current");});
  document.querySelectorAll(".view-panel").forEach(node=>{node.hidden=node.id!==`panel-${name}`;});
  $("breadcrumb-current").textContent=tabs[name][0];$("page-title").textContent=tabs[name][1];$("page-subtitle").textContent=tabs[name][2];
  if(name==="evidence"&&!state.evidence&&!state.evidenceLoading)loadEvidence();
}
function section(title,subtitle,numberText) {
  const card=el("section","card");const header=el("div","card-header");
  append(header,append(el("div"),el("h2",null,title),subtitle?el("p",null,subtitle):null),numberText?el("span","section-number",numberText):null);
  const body=el("div","card-body");append(card,header,body);return {card,body,header};
}
function empty(title,description,glyph="◫") {
  const node=el("div","card empty-state");append(node,el("div","empty-glyph",glyph),el("h2",null,title),el("p",null,description));return node;
}
function renderAll() {
  renderOverview();renderComments();renderTimeline();renderNetwork();renderEvidence();
  if(!state.data) return;
  const data=state.data;
  $("run-banner").hidden=false;$("analysis-toolbar").hidden=false;
  const source=data.provenance?.source_name||data.provenance?.source||state.activeSource?.source_name||"导入数据";
  $("run-source").textContent=String(source);$("run-description").textContent=`· ${fmt(data.summary.total)} 条已分析 · ${data.backend==="rules"?"规则模式":"本地模型"}`;
  $("run-time").textContent=`${dateText(data.created_at)} / 北京时间`;
  $("demo-notice").hidden=!(String(source).includes("synthetic_demo")||state.activeSource?.source_name==="synthetic_demo"||data.provenance?.synthetic===true);
  $("rules-notice").hidden=data.backend!=="rules";
  $("nav-review-count").textContent=fmt(data.summary.review_count);
  renderReviewSummary();
}
function metric(label,value,note,index,featured=false) {
  const card=el("div",`card metric${featured?" featured":""}`);
  append(card,el("span","metric-number",index),el("p","metric-label",label),el("p","metric-value",fmt(value)),el("p","metric-note",note));return card;
}
function quality(label,value) {return append(el("div","quality-item"),el("span",null,label),el("strong",null,value));}
function renderOverview() {
  const panel=$("panel-overview");panel.replaceChildren();
  if(!state.data) {
    const blank=empty("准备好数据，就可以开始。","当前没有分析结果。选择文件，或点击上方演示按钮，建立一个属于你的可追溯分析批次。");
    const steps=el("div","steps");
    [["01","导入评论与上下文","支持 CSV / JSON，保留真实时间和回复关系。"],["02","分析与逐条核查","模型识别配合原文证据，人工复核独立保存。"],["03","导出可追溯结果","下载结构化结果，保留方法、来源与局限。"]].forEach(row=>append(steps,append(el("div","step-card"),el("span",null,row[0]),append(el("div"),el("h3",null,row[1]),el("p",null,row[2])))));
    append(blank,steps);panel.append(blank);return;
  }
  const d=state.data,s=d.summary;
  const unscorable=s.unscorable_count??d.comments.filter(c=>!hasMachineLabel(c)).length;
  const scored=s.sarcasm_scored_count??d.comments.filter(hasMachineLabel).length;
  const metrics=el("div","metrics-grid");append(metrics,metric("分析评论",s.total,"去空 / 去重后进入本批次","01",true),metric("机器判为反讽",s.sarcasm_count,"原始机器标签 · 不等于人工确认","02"),metric("需人工复核",s.review_count,"所有模型输出仍需核查 · 分数仅辅助排队","03"),metric("提取观点",s.opinion_count,"词典 / 规则四元组 · 附原文证据","04"));panel.append(metrics);
  const coverage=el("div","label-coverage card");append(coverage,el("strong",null,"反讽标签分布"),tag(`反讽 ${fmt(s.sarcasm_count)}`,"sarcasm"),tag(`非反讽 ${fmt(Math.max(0,scored-number(s.sarcasm_count)))}`,"neutral"),tag(`无法判定 ${fmt(unscorable)}`,"review"),el("span","muted","无法判定不计作非反讽，也不显示 0 分。"));panel.append(coverage);
  const grid=el("div","section-grid");
  const q=section("数据质量与覆盖","不补造缺失信息，明确分析能看见什么。","02");
  append(q.body,append(el("div","quality-grid"),quality("有效评论",fmt(s.total)),quality("带有效时间",`${fmt(s.dated_count)} / ${fmt(s.total)}`),quality("反讽可判定",fmt(scored)),quality("反讽无法判定",fmt(unscorable)),quality("化名作者",fmt(s.author_count)),quality("样本内回复关系",fmt(d.network?.edges?.length)),quality("悬空回复",fmt(d.network?.missing_parent_count)),quality("未知作者评论",fmt(d.network?.unknown_author_count))));
  append(q.body,el("p","quality-extra","缺失时间的评论不会进入时序曲线；未知作者或缺失父评论不会被补成互动边。"));
  if(d.ingest){const details=el("details");append(details,el("summary",null,"查看导入质量记录"),renderValue(d.ingest));q.body.append(details);}
  const model=section("当前分析方法","真实后端状态，不以规则伪装模型能力。","03");
  const modelInfo=d.model||state.health?.model||{};
  append(model.body,el("p","model-name",d.backend==="rules"?"有限规则基线":modelInfo.name||modelInfo.model_name||"本地 RoBERTa 反讽模型"),append(el("div","chip-row"),tag(d.backend==="rules"?"规则模式":"本地模型",d.backend==="rules"?"rule":"sarcasm"),tag(`设备 ${modelInfo.device||"见评测记录"}`)),el("p","model-caption","反讽分数未校准，不是正确率；高低分只辅助安排复核优先级，不能自动免除人工核查。清洗后无可读评论等情况显示“无法判定”。普通负面评价不等于反讽，也不因反讽标签自动翻转情感。"));
  const boundary=el("ul","boundary-list");[["观点提取","词典 / 规则基线，隐式指代与目标仍需复核。"],["主题与变化","样本级描述，不代表平台整体舆情。"],["互动关系","只用已给出的回复，不能识别“幕后推手”。"]].forEach(([a,b])=>append(boundary,append(el("li"),el("strong",null,`${a}：`),document.createTextNode(b))));append(model.body,boundary);
  append(grid,q.card,model.card);panel.append(grid);
  const bottom=el("div","section-grid");const preview=section("回到评论原文","优先展示待复核评论，保留原始机器标签。","04");
  const list=el("div","preview-list");const samples=[...d.comments].sort((a,b)=>reviewPriority(b)-reviewPriority(a)).slice(0,3);
  samples.forEach(c=>{const item=el("div","preview-item");append(item,append(el("div"),el("p",null,c.text),commentTags(c)),button("查看证据 →","text-button",()=>openComment(c.id)));append(list,item);});append(preview.body,list);
  const sentiment=section("情感分布","字面情感规则基线，不是人工真值。","05");
  const bar=el("div","sentiment-bar");[["positive",s.positive_count],["negative",s.negative_count],["neutral",s.neutral_count]].forEach(([kind,n])=>{const seg=el("span",kind);seg.style.width=`${s.total?number(n)/s.total*100:0}%`;bar.append(seg);});
  const legend=el("div","legend");[["正面",s.positive_count,"#a4ba86"],["负面",s.negative_count,"#cea898"],["中性",s.neutral_count,"#dce1d5"]].forEach(([name,n,color])=>{const item=el("span",null,`${name} ${fmt(n)}`);item.style.setProperty("--legend-color",color);legend.append(item);});
  append(sentiment.body,bar,legend,el("p","model-caption","混合情感、反讽、否定和语境省略会影响规则判断。应通过人工复核验证结论。"));
  append(bottom,preview.card,sentiment.card);panel.append(bottom);
  const warnings=[...(d.warnings||[])];
  if(warnings.length){const note=section("本批次注意事项","这些限制应随结果一并阅读。","!");const ul=el("ul","warning-list");warnings.forEach(w=>ul.append(el("li",null,w)));note.body.append(ul);panel.append(note.card);}
}
function commentTags(c) {
  const row=el("div","chip-row");append(row,tag(`机器：${machineLabel(c)}`,!hasMachineLabel(c)?"review":c.sarcasm.label===1?"sarcasm":"neutral"));
  if(c.sarcasm?.review_required&&!state.reviews.has(c.id))row.append(tag(c.sarcasm.review_priority==="high"||!hasMachineLabel(c)?"优先复核":"常规复核","review"));
  if(state.reviews.has(c.id))row.append(tag(`人工：${state.reviews.get(c.id).label===1?"反讽":"非反讽"}`,"human"));return row;
}
function filteredComments() {
  if(!state.data) return [];
  const filtered=state.data.comments.filter(c=>{
    const haystack=`${c.id||""} ${c.text||""} ${c.context||""}`.toLocaleLowerCase();
    if(state.query&&!haystack.includes(state.query.toLocaleLowerCase()))return false;
    if(state.filter==="review")return Boolean(c.sarcasm?.review_required)&&!state.reviews.has(c.id);
    if(state.filter==="sarcasm")return hasMachineLabel(c)&&c.sarcasm.label===1;
    if(state.filter==="non_sarcasm")return hasMachineLabel(c)&&c.sarcasm.label===0;
    if(state.filter==="unscorable")return !hasMachineLabel(c);
    if(state.filter==="high_priority")return reviewPriority(c)>=2;
    if(state.filter==="reviewed")return state.reviews.has(c.id);
    if(state.filter==="opinions")return Boolean(c.opinions?.length);
    return true;
  });
  return ["review","high_priority"].includes(state.filter)?filtered.sort((a,b)=>reviewPriority(b)-reviewPriority(a)):filtered;
}
function renderComments() {
  const panel=$("panel-comments");panel.replaceChildren();
  if(!state.data){panel.append(empty("暂无评论可复核","请先导入或分析一个批次。这里将逐条展示模型输出、观点证据与独立人工复核。","≡"));return;}
  const card=el("div","card");
  const tools=el("div","comment-tools");const search=append(el("div","input-field search-field"),el("label",null,"搜索评论、上下文或 ID"));
  const input=el("input","search-input");input.type="search";input.placeholder="输入关键词，例如：客服、发货、评论 ID";input.value=state.query;input.setAttribute("aria-label","搜索评论");
  input.addEventListener("input",()=>{state.query=input.value;state.page=1;renderCommentTable(card);});search.append(input);
  const filterField=append(el("div","input-field"),el("label",null,"筛选范围"));const select=el("select");select.setAttribute("aria-label","筛选评论");
  [["all","全部评论"],["review","待复核（尚未处理）"],["high_priority","优先复核（尚未处理）"],["sarcasm","机器判为反讽"],["non_sarcasm","机器判为非反讽"],["unscorable","机器无法判定"],["reviewed","已人工复核"],["opinions","包含观点四元组"]].forEach(([value,label])=>{const opt=el("option",null,label);opt.value=value;select.append(opt);});select.value=state.filter;
  select.addEventListener("change",()=>{state.filter=select.value;state.page=1;renderCommentTable(card);});filterField.append(select);append(tools,search,filterField);card.append(tools);panel.append(card);renderCommentTable(card);
}
function renderCommentTable(card) {
  card.querySelectorAll(".table-wrap,.table-footer,.table-note,.chart-empty").forEach(node=>node.remove());
  const records=filteredComments();const pages=Math.max(1,Math.ceil(records.length/state.pageSize));state.page=Math.min(state.page,pages);
  const table=el("table");const header=el("tr");["评论原文 / 上下文","机器反讽标签","模型分数¹","字面情感","观点","核查"].forEach(label=>header.append(el("th",null,label)));append(table,append(el("thead"),header));const body=el("tbody");
  records.slice((state.page-1)*state.pageSize,state.page*state.pageSize).forEach(c=>{
    const row=el("tr");const text=el("td","comment-cell");append(text,el("p",null,c.text),el("small",null,c.context||`评论 ID：${c.id}`));
    const machine=el("td","nowrap");append(machine,commentTags(c));
    const score=el("td","score-cell nowrap");
    if(hasMachineLabel(c)&&typeof c.sarcasm?.score==="number"&&Number.isFinite(c.sarcasm.score)){append(score,document.createTextNode(modelScore(c)));const track=el("span","score-track");const fill=el("span");fill.style.width=`${Math.max(0,Math.min(1,c.sarcasm.score))*100}%`;append(track,fill);append(score,track);}else append(score,el("span","muted",modelScore(c)));
    const mood=c.sentiment?.label||"neutral";const sentiment=append(el("td","nowrap"),tag({positive:"正面",negative:"负面",neutral:"中性"}[mood]||mood,mood));
    append(row,text,machine,score,sentiment,el("td","nowrap",fmt(c.opinions?.length)),append(el("td","nowrap"),button("证据 / 复核","text-button",()=>openComment(c.id))));body.append(row);
  });
  table.append(body);append(card,append(el("div","table-wrap"),table));
  if(!records.length)card.append(el("p","chart-empty","没有符合当前条件的评论。可以调整关键词或筛选范围。"));
  const foot=el("div","table-footer");const pagination=el("div");const prev=button("← 上一页","text-button",()=>{state.page--;renderCommentTable(card);});prev.disabled=state.page<=1;
  const next=button("下一页 →","text-button",()=>{state.page++;renderCommentTable(card);});next.disabled=state.page>=pages;
  append(pagination,prev,el("span",null,`${state.page} / ${pages}`),next);append(foot,el("span",null,`共 ${fmt(records.length)} 条 · 每页 ${state.pageSize} 条`),pagination);append(card,foot,el("p","table-note",`¹ 未校准的反讽模型分数，不是正确率或人工置信度；规则模式不提供分数。此表及打印仅展示当前筛选的第 ${state.page} 页（共 ${pages} 页）。JSON / CSV 导出包含全批次，不受分页或筛选影响。`));
}
function openComment(id) {
  const c=state.data?.comments.find(row=>row.id===id);if(!c)return;
  const detail=$("comment-detail");detail.replaceChildren();
  append(detail,append(el("div","detail-meta"),el("span",null,`ID ${c.id}`),el("span",null,c.author_id?`作者 ${c.author_id}`:"作者未知"),el("span",null,dateText(c.published_at))),commentTags(c),el("blockquote","detail-text",c.text),el("p","detail-context",`上下文：${c.context||"未提供"}`));
  const machine=append(el("section","detail-section"),el("h3",null,"机器判断 · 保留原始结果"));
  append(machine,el("p",null,`反讽标签：${machineLabel(c)}　后端：${c.sarcasm?.backend||state.data.backend}　未校准分数：${modelScore(c,4)}`));
  append(machine,el("p",null,`复核安排：${c.sarcasm?.review_priority==="high"||!hasMachineLabel(c)?"优先复核":"常规复核"}。分数不代表经过校准的可靠性，任何模型输出均不能免除人工核查。`));
  if(c.sarcasm?.reasons?.length)machine.append(el("p",null,`方法 / 提示：${c.sarcasm.reasons.join("；")}`));
  append(machine,el("p",null,`字面情感：${{positive:"正面",negative:"负面",neutral:"中性"}[c.sentiment?.label]||"未知"}。词典证据：${c.sentiment?.evidence?.join("、")||"无匹配词"}。不因反讽标签自动翻转。`));detail.append(machine);
  const opinions=append(el("section","detail-section"),el("h3",null,`观点四元组 · ${fmt(c.opinions?.length)} 项`));
  if(!c.opinions?.length)opinions.append(el("p",null,"规则未提取出明确观点。这不表示原文没有观点，隐式表达仍需人工阅读。"));
  (c.opinions||[]).forEach(o=>{
    const box=el("article","opinion-item");const grid=el("div","opinion-grid");
    [["评价人",o.holder||"未知 / 未提供"],["评价对象",o.target||"未知 / 待确认"],["具体观点",o.opinion],["情感",{positive:"正面",negative:"负面",neutral:"中性"}[o.sentiment]||o.sentiment]].forEach(([k,v])=>append(grid,append(el("div"),el("span",null,`${k}　`),el("strong",null,v))));
    append(box,grid,el("blockquote",null,o.evidence),el("p","opinion-caption",`原文字符位置 [${o.start}, ${o.end}) · ${o.method||"规则基线"}${o.review_required?" · 建议人工复核":""}`));opinions.append(box);
  });detail.append(opinions);
  const review=append(el("section","detail-section"),el("h3",null,"人工复核 · 与机器标签独立"),el("p",null,"请选择你的反讽判断，并记录理由。此处是人工工作记录，不会自动成为已验证的标准答案。"));
  const current=state.reviews.get(c.id);const note=el("textarea");note.rows=3;note.maxLength=2000;note.placeholder="复核备注，例如：上下文中有字面褒义与实际贬义的冲突……";note.value=current?.note||"";note.setAttribute("aria-label","人工复核备注");
  const controls=el("div","review-buttons");
  const save=label=>{state.reviews.set(c.id,{label,note:note.value,reviewed_at:new Date().toISOString(),status:"human_unverified"});state.reviewsExported=false;renderReviewSummary();renderOverview();renderComments();openComment(c.id);toast("复核已保存在当前页面，请导出留存。");};
  append(controls,button("人工：非反讽",`button secondary small ${current?.label===0?"selected":""}`,()=>save(0)),button("人工：反讽",`button secondary small ${current?.label===1?"selected":""}`,()=>save(1)),button("清除复核","button secondary small",()=>{state.reviews.delete(c.id);state.reviewsExported=false;renderReviewSummary();renderOverview();renderComments();openComment(c.id);toast("已清除人工复核，机器输出保持不变。");}));
  append(review,note,controls,el("p","review-note",current?`上次保存：${dateText(current.reviewed_at)}。修改备注后，再点击判断按钮保存。`:"输入备注后，点击判断按钮保存。关闭未保存的备注不会修改结果。"));detail.append(review);
  if(!$("comment-dialog").open)$("comment-dialog").showModal();
}
function renderReviewSummary() {$("review-summary").textContent=`人工复核 ${fmt(state.reviews.size)} / ${fmt(state.data?.summary?.total)} 条`;}

function svg(tagName,attributes={},text) {
  const node=document.createElementNS("http://www.w3.org/2000/svg",tagName);
  Object.entries(attributes).forEach(([key,value])=>node.setAttribute(key,String(value)));
  if(text!==undefined)node.textContent=String(text);return node;
}
function timelineSegments(records,key,signed=false) {
  const segments=[];let segment=[];
  const flush=()=>{if(segment.length)segments.push(segment);segment=[];};
  records.forEach((row,index)=>{
    // A sparse gap is an absent interval, not evidence of a smooth transition.
    if(row.gap_before)flush();
    const empty=row.is_empty===true||row.count===0;
    if((signed&&empty)||typeof row[key]!=="number"||!Number.isFinite(row[key])){flush();return;}
    segment.push({index,row});
  });flush();return segments;
}
function chart(records,key,title,signed=false) {
  const container=el("div","chart-container");if(!records.length){container.append(el("p","chart-empty","没有可用的时间记录。缺失时间的评论仍可在评论页复核。"));return container;}
  const W=720,H=245,p={left:45,right:20,top:23,bottom:40};const width=W-p.left-p.right,height=H-p.top-p.bottom;
  const maximum=signed?1:Math.max(1,...records.map(r=>number(r[key])));const minimum=signed?-1:0;
  const x=index=>p.left+(records.length===1?width/2:index*width/(records.length-1));const y=value=>p.top+(maximum-number(value))/(maximum-minimum)*height;
  const root=svg("svg",{viewBox:`0 0 ${W} ${H}`,role:"img","aria-label":title});root.append(svg("title",{},title));
  for(let i=0;i<5;i++){const value=minimum+(maximum-minimum)*i/4;const position=y(value);root.append(svg("line",{x1:p.left,y1:position,x2:W-p.right,y2:position,class:"chart-grid"}));root.append(svg("text",{x:p.left-10,y:position+3,"text-anchor":"end",class:"chart-axis-label"},signed?value.toFixed(1):Math.round(value)));}
  const step=Math.max(1,Math.ceil(records.length/6));records.forEach((row,i)=>{if(i%step===0||i===records.length-1){const label=String(row.time).replace("T"," ").slice(5,16);root.append(svg("text",{x:x(i),y:H-14,"text-anchor":"middle",class:"chart-axis-label"},label));}});
  timelineSegments(records,key,signed).forEach(segment=>{
    root.append(svg("polyline",{points:segment.map(({index,row})=>`${x(index)},${y(row[key])}`).join(" "),class:signed?"chart-sentiment":"chart-primary"}));
    segment.forEach(({index,row})=>{const dot=svg("circle",{cx:x(index),cy:y(row[key]),r:records.length<60?3:1.6,class:"chart-dot"});dot.append(svg("title",{},`${row.time}：${signed?number(row[key]).toFixed(3):fmt(row[key])}`));root.append(dot);});
  });
  records.forEach((row,i)=>{if(row.gap_before&&i>0){const position=(x(i-1)+x(i))/2;root.append(svg("line",{x1:position,y1:p.top,x2:position,y2:H-p.bottom,stroke:"#c9b48f","stroke-dasharray":"3 4"}));root.append(svg("text",{x:position+4,y:p.top+8,class:"chart-axis-label"},"缺失间隔"));}});
  container.append(root);return container;
}
function evidenceButtons(ids) {const row=el("div","event-evidence");(ids||[]).slice(0,8).forEach(id=>row.append(button(`评论 ${id} ↗`,"text-button",()=>openComment(id))));return row;}
function renderTimeline() {
  const panel=$("panel-timeline");panel.replaceChildren();
  if(!state.data){panel.append(empty("等待带时间的评论","时序分析只使用有效的原始时间，不会补造日期。请先导入并分析数据。","⌁"));return;}
  const d=state.data,timeline=d.timeline||[];
  const count=section("评论样本量","北京时间（Asia/Shanghai）；这里是导入样本，不代表平台总量。","01");count.body.className="chart-card";append(count.body,chart(timeline,"count","各时间桶的评论样本数量"),el("p","chart-legend",`有效时间 ${fmt(d.summary.dated_count)} 条 / 全部 ${fmt(d.summary.total)} 条。空时间桶保留为 0；时间范围过大时请查看本批次警告。`));panel.append(count.card);
  const grid=el("div","section-grid");grid.style.marginTop="20px";
  const mood=section("字面情感均值","词典加权分数范围 [−1, 1] · 非人工真实态度","02");mood.body.className="chart-card";append(mood.body,chart(timeline,"sentiment_mean","各时间桶词典加权情感分数均值，范围负一到一",true),el("p","chart-legend","这里是连续词典分数的均值，不是情感标签 ±1 的均值。空桶与缺失间隔均断线；时间桶顺序等距展示，稀疏间隔以虚线标示。"));
  const topics=section("样本主题词","词典主题基线，不是 BERTopic / LLM 聚类。","03");const chips=el("div","chip-row");
  (d.topics||[]).slice(0,20).forEach(topic=>{const b=button("","topic-chip",()=>showEvidenceSet(topic.comment_ids,`主题词“${topic.term}”`));append(b,el("span",null,topic.term),el("small",null,fmt(topic.count)));chips.append(b);});
  if(!d.topics?.length)topics.body.append(el("p","model-caption","当前词典没有匹配到主题。未知主题不会被编造为已识别类别。"));else topics.body.append(chips);
  append(grid,mood.card,topics.card);panel.append(grid);
  const changes=section("样本变化提示","每个提示都附带可定位的评论；不构成事件或因果判断。","04");
  if(!d.turning_points?.length)changes.body.append(el("p","model-caption","当前批次没有符合规则的变化提示。小样本、缺失时间或不连续时间段均可能限制变化分析。"));
  (d.turning_points||[]).forEach(point=>{const item=el("article","event-item");append(item,append(el("div","event-meta"),el("span",null,dateText(point.time)),tag(`Δ ${number(point.delta)>0?"+":""}${number(point.delta).toFixed(3)}`,number(point.delta)>0?"positive":"negative")),el("p",null,point.description),evidenceButtons(point.evidence_ids));changes.body.append(item);});panel.append(changes.card);
  if(timeline.length){const buckets=section("时间桶明细","完整时序数据随 JSON 结果导出；此处最多显示前 80 桶。","05");buckets.card.style.marginTop="20px";buckets.body.className="table-wrap";const table=el("table");const head=el("tr");["时间（北京时间）","样本","均值 / 方差","反讽","无法判定","待复核","原文证据"].forEach(t=>head.append(el("th",null,t)));append(table,append(el("thead"),head));const tbody=el("tbody");timeline.slice(0,80).forEach(row=>{const tr=el("tr");const timestamp=el("td","nowrap",dateText(row.time));if(row.gap_before)append(timestamp,el("br"),el("small","muted",`此前缺失 ${fmt(row.gap_before.empty_buckets)} 桶`));append(tr,timestamp,el("td",null,fmt(row.count)),el("td","nowrap",row.is_empty===true||row.count===0?"无样本":`${benchmarkMetric(row.sentiment_mean,3)} / ${benchmarkMetric(row.sentiment_variance,3)}`),el("td",null,fmt(row.sarcasm_count)),el("td",null,row.unscorable_count===undefined?"未提供":fmt(row.unscorable_count)),el("td",null,fmt(row.review_count)),append(el("td"),evidenceButtons((row.representative_ids||[]).slice(0,2))));tbody.append(tr);});table.append(tbody);buckets.body.append(table);panel.append(buckets.card);}
}
function showEvidenceSet(ids,label) {
  const rows=(ids||[]).map(id=>state.data?.comments.find(c=>c.id===id)).filter(Boolean);
  const detail=$("comment-detail");detail.replaceChildren();append(detail,el("h3",null,label),el("p","model-caption",`共 ${fmt(rows.length)} 条可定位证据，点击进入单条评论复核。`));
  rows.slice(0,50).forEach(c=>append(detail,append(el("article","event-item"),el("p",null,c.text),button(`查看评论 ${c.id} →`,"text-button",()=>openComment(c.id)))));
  if(!rows.length)detail.append(el("p","model-caption","没有可定位的评论证据。"));if(!$("comment-dialog").open)$("comment-dialog").showModal();
}
function networkGraphic(network) {
  const box=el("div","network-canvas");
  if(!network.edges?.length){append(box,el("p","chart-empty","未获得可验证的回复边。请提供 author_id 和指向批次内评论的 parent_id；系统不会补造关系。"));return box;}
  const nodes=[...(network.nodes||[])].sort((a,b)=>number(b.score)-number(a.score)).slice(0,36);const visible=new Set(nodes.map(n=>n.id));const edges=(network.edges||[]).filter(e=>visible.has(e.source)&&visible.has(e.target));
  const root=svg("svg",{viewBox:"0 0 680 420",role:"img","aria-label":"样本内回复关系图，方向为回复者指向被回复者。点击节点查看证据。"});root.append(svg("title",{},"样本内回复关系；仅展示评分最高的最多 36 个节点。"));
  const defs=svg("defs");const marker=svg("marker",{id:"reply-arrow",viewBox:"0 0 10 10",refX:9,refY:5,markerWidth:5,markerHeight:5,orient:"auto-start-reverse"});marker.append(svg("path",{d:"M 0 1 L 9 5 L 0 9 z",fill:"#91af98"}));defs.append(marker);root.append(defs);
  const positions=new Map();nodes.forEach((node,i)=>{const angle=(i/Math.max(nodes.length,1))*Math.PI*2-Math.PI/2;const ring=nodes.length>12&&i%3===0?.57:1;positions.set(node.id,{x:340+Math.cos(angle)*250*ring,y:202+Math.sin(angle)*146*ring});});
  edges.forEach(edge=>{const a=positions.get(edge.source),b=positions.get(edge.target);const dx=b.x-a.x,dy=b.y-a.y,distance=Math.hypot(dx,dy)||1;const sx=a.x+dx/distance*18,sy=a.y+dy/distance*18,tx=b.x-dx/distance*20,ty=b.y-dy/distance*20;const line=svg("path",{d:`M ${sx} ${sy} Q ${(sx+tx)/2+dy*.07} ${(sy+ty)/2-dx*.07} ${tx} ${ty}`,class:"network-edge","stroke-width":Math.min(4,1+Math.log1p(number(edge.weight))),"marker-end":"url(#reply-arrow)"});line.append(svg("title",{},`${edge.source} → ${edge.target}：${edge.weight} 次回复`));root.append(line);});
  nodes.forEach((node,i)=>{const p=positions.get(node.id),group=svg("g",{class:"network-group",tabindex:"0",role:"button","aria-label":`作者 ${node.id}，${node.comment_count} 条评论，查看证据`});
    const radius=i<3?19:14;group.append(svg("circle",{cx:p.x,cy:p.y,r:radius,class:`network-node${i<3?" major":""}`}));group.append(svg("text",{x:p.x,y:p.y+3,class:"network-label"},String(i+1).padStart(2,"0")));group.append(svg("text",{x:p.x,y:p.y+radius+14,class:"network-label"},String(node.id).length>16?`${String(node.id).slice(0,13)}…`:node.id));
    const action=()=>showEvidenceSet(node.evidence_ids,`作者 ${node.id} 的评论证据`);group.addEventListener("click",action);group.addEventListener("keydown",event=>{if(event.key==="Enter"||event.key===" "){event.preventDefault();action();}});root.append(group);
  });box.append(root);return box;
}
function renderNetwork() {
  const panel=$("panel-network");panel.replaceChildren();if(!state.data){panel.append(empty("只画有依据的关系","导入原始作者标识与批次内回复关系后，才能构建互动图。未知字段保持未知。","⌘"));return;}
  const network=state.data.network||{nodes:[],edges:[]};const graph=section("样本内回复关系网络","方向：回复者 → 被回复者 · 节点为批次内化名作者，不等于完全匿名","01");graph.body.className="";
  const summary=el("div","network-summary");[["作者节点",network.nodes?.length],["已知回复边",network.edges?.length],["悬空父评论",network.missing_parent_count],["未知作者评论",network.unknown_author_count]].forEach(([label,n])=>append(summary,append(el("span"),document.createTextNode(label),el("strong",null,fmt(n)))));
  append(graph.body,summary,networkGraphic(network),el("p","chart-legend","图中最多展示评分最高的 36 个节点，连线只显示这些节点间已有的回复。节点编号与下方列表一致；完整关系随 JSON 导出。"));panel.append(graph.card);
  const ranking=section("高互动可见度节点","排序是描述性指标，不是影响力真值，更不是“幕后推手”证据。","02");ranking.card.style.marginTop="20px";ranking.body.className="table-wrap";
  const table=el("table");const header=el("tr");["排名","化名作者","评论数","收到 / 发出回复","点赞合计","PageRank","综合评分","原文证据"].forEach(t=>header.append(el("th",null,t)));append(table,append(el("thead"),header));const tbody=el("tbody");
  [...(network.nodes||[])].sort((a,b)=>number(b.score)-number(a.score)).slice(0,50).forEach((node,i)=>{const row=el("tr");append(row,el("td",`rank-number${i<3?" top":""}`,String(i+1).padStart(2,"0")),el("td",null,node.id),el("td",null,fmt(node.comment_count)),el("td",null,`${fmt(node.in_degree)} / ${fmt(node.out_degree)}`),el("td",null,fmt(node.likes)),el("td",null,number(node.pagerank).toFixed(4)),el("td",null,number(node.score).toFixed(3)),append(el("td","nowrap"),button("查看证据 →","text-button",()=>showEvidenceSet(node.evidence_ids,`作者 ${node.id} 的评论证据`))));tbody.append(row);});append(table,tbody);append(ranking.body,table);if(!network.nodes?.length)ranking.body.append(el("p","chart-empty","没有可用作者节点，无法进行节点排名。"));panel.append(ranking.card);
  const note=section("方法与限制",network.method||"仅依据样本内已提供的回复关系。","03");note.card.style.marginTop="20px";const list=el("ul","warning-list");[...(network.warnings||[]),"作者标识只做批次内化名化，不承诺完全匿名；演示夹具中的回复关系不是平台实采证据。", "节点表最多显示前 50 项。点赞和回复数受采样、采集时间与缺失数据影响；不能据此认定账号操纵或因果影响。"].forEach(text=>list.append(el("li",null,text)));note.body.append(list);panel.append(note.card);
}
function renderValue(value,depth=0) {
  if(value===null||value===undefined)return el("span","muted","未提供");
  if(depth>7)return el("span",null,JSON.stringify(value));
  if(Array.isArray(value)){const ul=el("ul","evidence-array");value.slice(0,200).forEach(item=>ul.append(append(el("li"),renderValue(item,depth+1))));if(value.length>200)ul.append(el("li","muted",`其余 ${value.length-200} 项请查看原始证据文件。`));return ul;}
  if(typeof value==="object"){const dl=el("dl","evidence-object");Object.entries(value).forEach(([key,item])=>append(dl,el("dt",null,humanKeys[key]||key),append(el("dd"),renderValue(item,depth+1))));return dl;}
  if(typeof value==="boolean")return el("span",null,value?"是":"否");
  if(typeof value==="string"&&/^https?:\/\/[^\s]+$/.test(value)){const link=el("a","evidence-link",value);link.href=value;link.target="_blank";link.rel="noopener noreferrer";return link;}
  return el("span",null,typeof value==="number"&&!Number.isInteger(value)?value.toFixed(6):String(value));
}
async function loadEvidence() {
  state.evidenceLoading=true;renderEvidence();
  try{state.evidence=await request("/api/evidence");}
  catch(error){state.evidence={available:false,message:`无法读取评测证据：${error.message}`};}
  finally{state.evidenceLoading=false;renderEvidence();}
}
function benchmarkMetric(value,digits=4) {return typeof value==="number"&&Number.isFinite(value)?value.toFixed(digits):"未提供";}
function overlapWarning(results) {
  const affected=(results||[]).filter(result=>number(result.train_overlap?.identical_comment_count)>0||number(result.train_overlap?.identical_model_input_count)>0);
  if(!affected.length)return null;
  const notice=el("section","overlap-warning");append(notice,el("h3",null,"检测到训练 / 评测重叠：不能当独立零样本泛化"));const list=el("ul","warning-list");
  affected.forEach(result=>list.append(el("li",null,`${result.dataset}：与原始 ToSarcasm 训练集相同的评论 ${fmt(result.train_overlap.identical_comment_count)} 条；完整模型输入相同 ${fmt(result.train_overlap.identical_model_input_count)} 条${result.train_overlap.shared_topic_count!==undefined?`；共享话题 ${fmt(result.train_overlap.shared_topic_count)} 个`:""}。完整快照分数包含这些重叠样本。`)));
  append(notice,list,el("p",null,"完整快照结果仅作对照。请同时阅读下方排重敏感性结果；排重后仍不能排除近重复、增强数据或历史选模偏差。"));return notice;
}
function overlapAuditView(audit) {
  const box=el("section","overlap-audit");append(box,el("h3",null,"重叠敏感性分析 · 与完整快照同时报告"));
  if(!audit||audit.available===false){append(box,el("p","model-caption","尚未载入独立排重审计，不应把上方完整快照分数当作独立泛化能力。"));return box;}
  if(!Array.isArray(audit.results)){append(box,renderValue(audit));return box;}
  const names={full_snapshot:"完整快照（保留重叠）",exclude_identical_model_input:"排除相同模型输入",exclude_identical_comment:"排除相同评论",exclude_shared_topic:"排除共享话题"};
  append(box,el("p","model-caption",`${audit.title||"存量预测复算"}。各过滤方案独立应用，不能混称联合过滤；没有重新训练或据此调阈值。`));
  const wrap=el("div","table-wrap benchmark-table");const table=el("table");const head=el("tr");["敏感性方案","方法","保留 / 排除","准确率","Macro-F1","反讽 F1"].forEach(name=>head.append(el("th",null,name)));append(table,append(el("thead"),head));const body=el("tbody");
  audit.results.forEach(result=>{
    [["已配置模型",result.m4],["固定 TF-IDF 基线",result.tfidf]].forEach(([label,metrics])=>{
      if(!metrics)return;const primary=result.analysis===audit.primary_sensitivity_analysis;const row=el("tr",label==="已配置模型"?"model-row":"baseline-row");
      append(row,el("td",null,`${names[result.analysis]||result.analysis}${primary?" · 主敏感性方案":""}`),el("td","nowrap",label),el("td","nowrap",`${fmt(result.n)} / ${fmt(result.excluded_count)}`));["accuracy","macro_f1","sarcasm_f1"].forEach(key=>row.append(el("td","benchmark-number",benchmarkMetric(metrics[key]))));body.append(row);
    });
  });append(table,body);append(wrap,table);box.append(wrap);
  const constraints=el("ul","warning-list");(audit.limitations||[]).forEach(item=>constraints.append(el("li",null,item)));box.append(constraints);
  return box;
}
function benchmarkView(data) {
  const view=el("div","benchmark-view");
  append(view,el("h3",null,data.title||"冻结已配置模型与固定基线比较"),el("p","model-caption",`${data.model_name||"本地已配置模型"} · 评测时间 ${dateText(data.audit_utc)}`));
  if(data.model_selection)view.append(el("p","info-strip",data.model_selection));
  append(view,overlapWarning(data.results));
  const wrap=el("div","table-wrap benchmark-table");const table=el("table");const head=el("tr");
  ["数据集 / 划分","方法","样本量","准确率","Macro-F1","反讽精确率","反讽召回率","反讽 F1"].forEach(title=>head.append(el("th",null,title)));append(table,append(el("thead"),head));const body=el("tbody");
  data.results.forEach(result=>{
    [["已配置模型",result.model],["固定 TF-IDF 基线",result.baseline]].forEach(([label,metrics])=>{
      if(!metrics||typeof metrics!=="object")return;
      const row=el("tr",label==="已配置模型"?"model-row":"baseline-row");
      append(row,el("td","nowrap",result.dataset),el("td","nowrap",label),el("td","nowrap",typeof metrics.n==="number"?fmt(metrics.n):"未提供"));
      ["accuracy","macro_f1","sarcasm_precision","sarcasm_recall","sarcasm_f1"].forEach(key=>row.append(el("td","benchmark-number",benchmarkMetric(metrics[key]))));body.append(row);
    });
  });append(table,body);append(wrap,table);append(view,wrap,el("p","model-caption","表中数字直接读取交付基准文件，保留四位小数；dev 与 test 分开报告，不合并计算。FGVIrony 是跨域二分类迁移，不是六分类，也不是抖音现场验收。"));
  if(data.overlap_audit||(data.results||[]).some(result=>number(result.train_overlap?.identical_comment_count)>0||number(result.train_overlap?.identical_model_input_count)>0))view.append(overlapAuditView(data.overlap_audit));
  if(data.limitations?.length){append(view,el("h3","benchmark-subheading","重要限制"));const list=el("ul","warning-list");data.limitations.forEach(item=>list.append(el("li",null,item)));view.append(list);}
  if(data.environment){const chips=el("div","chip-row benchmark-environment");Object.entries(data.environment).forEach(([key,value])=>chips.append(tag(`${humanKeys[key]||key}: ${value}`)));view.append(chips);}
  const audit=el("details","benchmark-details");append(audit,el("summary",null,"分数据集审计：区间、混淆矩阵、子型与速度"));
  data.results.forEach(result=>{
    const group=el("section","benchmark-dataset");append(group,el("h3",null,result.dataset));
    if(result.category_accuracy){
      append(group,el("p","model-caption","子型表仍是二分类是否判对；非 Non-Irony 子型的“判对比例”对应检出率，不表示能区分这些子型。"));
      const subtable=el("table");const header=el("tr");["原始子型","样本量","二分类判对数","判对比例"].forEach(label=>header.append(el("th",null,label)));append(subtable,append(el("thead"),header));const rows=el("tbody");
      Object.entries(result.category_accuracy).forEach(([name,value])=>{const ratio=typeof value.n==="number"&&value.n>0&&typeof value.correct==="number"?value.correct/value.n:null;append(rows,append(el("tr"),el("td",null,name),el("td",null,typeof value.n==="number"?fmt(value.n):"未提供"),el("td",null,typeof value.correct==="number"?fmt(value.correct):"未提供"),el("td",null,benchmarkMetric(ratio))));});append(subtable,rows);append(group,append(el("div","table-wrap"),subtable));
    }
    const supplemental={};["uncertainty","train_overlap","inference_seconds","ms_per_comment_batched","timing_note","predictions"].forEach(key=>{if(result[key]!==undefined)supplemental[key]=result[key];});
    supplemental.confusion_matrix={model:result.model?.confusion_matrix,baseline:result.baseline?.confusion_matrix,order:result.model?.confusion_matrix_order};
    append(group,renderValue(supplemental));audit.append(group);
  });view.append(audit);
  const methods=el("details","benchmark-details");append(methods,el("summary",null,"模型训练历史、固定基线与预处理"),renderValue({model_training_history:data.model_training_history,baseline_recipe:data.baseline_recipe,preprocessing:data.preprocessing}));view.append(methods);
  const provenance=el("details","benchmark-details");append(provenance,el("summary",null,"数据来源、排除记录、版本与文件哈希"),renderValue({sources:data.sources,model_files:data.model_files,references:data.references}));view.append(provenance);
  return view;
}
function renderEvidence() {
  const panel=$("panel-evidence");panel.replaceChildren();const stack=el("div","evidence-stack");
  const intro=el("section","card evidence-card");append(intro,el("h2",null,"使用边界 · 请先阅读"),el("p",null,"本工具提供的是本地研究分析能力，不声称完成了生产部署、真实抖音采集验收、行业最优（SOTA）或因果识别。演示样例不是性能评测数据。"),el("p",null,"公开版默认使用反讽规则演示；可选接入用户自己的本地模型。观点抽取、字面情感、主题与变化检测属于可解释规则基线。模型分数未校准，普通负面评价不等于反讽。人工复核不能替代正式盲评。"),el("p",null,"公开版本不附带历史训练数据、权重或评测记录；不继承旧实验指标。构造样例用于功能演示，不能替代真实业务验证。"));stack.append(intro);
  const evidence=section("本地基准证据","展示当前版本实际生成的证据，不预置或美化指标。","01");evidence.header.append(button("刷新证据","text-button",loadEvidence));
  if(state.evidenceLoading)evidence.body.append(el("p","model-caption","正在读取本地评测文件……"));
  else if(!state.evidence||state.evidence.available===false)append(evidence.body,el("p","model-caption",state.evidence?.message||"尚未读取到可用的评测证据。此时不展示任何准确率或 F1 声明。点击刷新可重新检查。"),state.evidence?renderValue(state.evidence):null);
  else {
    evidence.body.append(Array.isArray(state.evidence.results)?benchmarkView(state.evidence):renderValue(state.evidence));
    const raw=el("details");append(raw,el("summary",null,"查看完整证据 JSON"),el("pre","evidence-raw",JSON.stringify(state.evidence,null,2)));evidence.body.append(raw);
  }
  stack.append(evidence.card);
  if(state.data){const methodology=section("本批次方法与来源","方法、导入记录与运行标识随 JSON 一并导出。","02");append(methodology.body,renderValue({run_id:state.data.run_id,created_at:state.data.created_at,backend:state.data.backend,model:state.data.model,methodology:state.data.methodology,provenance:state.data.provenance}));stack.append(methodology.card);}
  panel.append(stack);
}
function exportPayload() {
  return {...state.data,exported_at:new Date().toISOString(),review_policy:"Original machine labels are unchanged. Human reviews are separate, unverified annotations, not ground truth.",human_reviews:Array.from(state.reviews,([comment_id,review])=>({comment_id,...review})),comments:state.data.comments.map(c=>({...c,human_review:state.reviews.get(c.id)||null}))};
}
function download(content,filename,type) {
  const blob=new Blob([content],{type});const link=el("a");link.href=URL.createObjectURL(blob);link.download=filename;document.body.append(link);link.click();link.remove();window.setTimeout(()=>URL.revokeObjectURL(link.href),30000);
}
function csvCell(value) {
  let text=value===undefined||value===null?"":String(value);
  // Protect spreadsheet consumers, including leading whitespace and control characters.
  if(/^[\s\uFEFF]*[=+\-@\t\r]/u.test(text)||/^[\t\r]/u.test(text))text="'"+text;
  return `"${text.replace(/"/g,'""')}"`;
}
function exportResults(format) {
  if(!state.data)return;
  const stamp=new Date().toISOString().replace(/[:.]/g,"-");
  if(format==="json")download(JSON.stringify(exportPayload(),null,2),`content-analysis-${stamp}.json`,"application/json;charset=utf-8");
  else {
    const columns=["id","text","context","author_id","published_at","video_id","parent_id","likes","reply_count","source","sarcasm_backend","machine_sarcasm_label","machine_sarcasm_status","machine_sarcasm_available","uncalibrated_model_score","machine_review_required","machine_review_priority","literal_sentiment","opinions_json","human_review_label","human_review_note","human_reviewed_at","human_review_status","run_id"];
    const rows=state.data.comments.map(c=>{const human=state.reviews.get(c.id)||{};return [c.id,c.text,c.context,c.author_id,c.published_at,c.video_id,c.parent_id,c.likes,c.reply_count,c.source,c.sarcasm?.backend,c.sarcasm?.label,hasMachineLabel(c)?"scored":"unscorable",hasMachineLabel(c),c.sarcasm?.score,c.sarcasm?.review_required,c.sarcasm?.review_priority,c.sentiment?.label,JSON.stringify(c.opinions||[]),human.label,human.note,human.reviewed_at,human.status,state.data.run_id];});
    download("\uFEFF"+[columns,...rows].map(row=>row.map(csvCell).join(",")).join("\r\n"),`content-analysis-${stamp}.csv`,"text/csv;charset=utf-8");
  }
  state.reviewsExported=true;toast(`已导出完整 ${fmt(state.data.summary.total)} 条结果及 ${fmt(state.reviews.size)} 条独立人工复核。`);
}

document.querySelectorAll(".nav-item").forEach(node=>node.addEventListener("click",()=>switchTab(node.dataset.tab)));
document.querySelector(".brand").addEventListener("click",event=>{event.preventDefault();switchTab("overview");});
$("file-input").addEventListener("change",event=>loadFile(event.target.files[0]));
const dropZone=$("file-zone");["dragenter","dragover"].forEach(name=>dropZone.addEventListener(name,event=>{event.preventDefault();dropZone.classList.add("dragover");}));["dragleave","drop"].forEach(name=>dropZone.addEventListener(name,event=>{event.preventDefault();dropZone.classList.remove("dragover");}));dropZone.addEventListener("drop",event=>loadFile(event.dataTransfer.files[0]));
$("analyze-button").addEventListener("click",()=>analyze(state.source));$("demo-button").addEventListener("click",loadDemo);
$("single-toggle").addEventListener("click",()=>{const expanded=$("single-form").hidden;$("single-form").hidden=!expanded;$("single-toggle").setAttribute("aria-expanded",String(expanded));if(expanded)$("single-text").focus();});
$("single-form").addEventListener("submit",event=>{event.preventDefault();const text=$("single-text").value.trim();if(!text){notify("请先输入评论正文。",true);$("single-text").focus();return;}analyze({records:[{id:"single-1",text,context:$("single-context").value.trim()}],source_name:"single_comment"});});
$("close-dialog").addEventListener("click",()=>$("comment-dialog").close());$("comment-dialog").addEventListener("click",event=>{if(event.target===$("comment-dialog")){const rect=$("comment-dialog").getBoundingClientRect();if(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom)$("comment-dialog").close();}});
$("export-json").addEventListener("click",()=>exportResults("json"));$("export-csv").addEventListener("click",()=>exportResults("csv"));
$("print-button").addEventListener("click",async()=>{if(!state.data){toast("请先分析一个批次，再打印报告。");return;}if(!state.evidence)await loadEvidence();window.print();});
window.addEventListener("beforeunload",event=>{if(state.reviews.size&&!state.reviewsExported){event.preventDefault();event.returnValue="";}});
renderAll();switchTab("overview");loadHealth();
