import{datasets}from'../stores/dataset.js';import{configs}from'../stores/config.js';import{selections}from'../stores/selection.js';import{create,detail,resume,download}from'../api/selection.js';import{header}from'../components/common/header.js';import{table}from'../components/common/table.js';import{outcome}from'../constants/selection.js';import{esc}from'../utils/html.js';import{submit,notify}from'../hooks/feedback.js';import{session}from'../stores/session.js';
const pct=x=>x==null?'—':Number(x).toFixed(4);
function outcomeBadge(status){const o=outcome(status);return '<span class="badge '+esc(o.tone)+'">'+esc(o.label)+'</span>'}
function summaryLine(s){
  if(!s)return esc('等待第一批演算');
  const b=s.budget||{};const p=s.pairs||{};
  return `预算 ${b.used}/${b.limit} · 已评估 ${p.evaluated}/${p.total} 组合`+(p.pruned?` · 剪枝跳过 ${p.pruned}`:'')
}
function configTable(s){
  const rows=(s?.summary?.configs||[]).map(c=>{
    const win=s?.summary?.winner?.config_id===c.config_id;
    const state={viable:'可行',infeasible:'不可行',partial:'未覆盖完',dominated_by_bound:'边界剪枝'}[c.state]||c.state;
    const worst=c.worst_read_amplification??c.lower_bound_worst_read_amplification;
    return [esc('# '+c.config_id)+(win?' <span class="badge passed">胜出</span>':''),esc(state),`${c.evaluated}/${c.required}`,pct(worst),pct(c.mean_read_amplification),esc(c.reason||'')]
  });
  return table(['配置','结论','已评估批次','最差批次读放大','平均读放大','说明'],rows)
}
function evalTable(s){
  const rows=(s.evals||[]).map(e=>[esc(e.dataset_name)+' <small>#'+e.dataset_id+'</small>',esc(e.config_name)+' <small>#'+e.config_id+'</small>',
    e.passed?'<span class="badge passed">通过</span>':'<span class="badge failed">'+esc(e.status==='write_amplification_exceeded'?'写放大超限':'执行失败')+'</span>',
    pct(e.write_amplification),pct(e.read_amplification_after),
    `${e.table_reads_after??'—'}/${e.probe_count??'—'}`,
    e.evidence?.checksums_match?'<span class="badge passed">Oracle 一致</span>':'<span class="badge failed">未核对</span>',
    esc(e.reason||'')])
  return table(['批次','配置','写放大结论','写放大','合并后读放大','范围命中/点查','键值','说明'],rows)
}
function evidenceDetail(s,e){
  const r=e.evidence||{};const levels=list=>'<div class="layers">'+list.map(l=>'<div class="layer"><span class="mono">L'+l.level+'</span><div class="slabs">'+(l.tables.length?l.tables.map(t=>'<span class="slab">'+t.entries+' entries / '+t.tombstones+' tombstones<br>'+esc(t.min_key)+' … '+esc(t.max_key)+'</span>').join(''):'<span class="muted">空层</span>')+'</div></div>').join('')+'</div>';
  if(!r.verified)return '<p class="error">'+esc(r.error||e.reason||'无可用证据')+'</p>';
  return `<div class="metrics"><div><small>写放大 · 条目口径</small><strong>${r.write_amplification} ×</strong></div><div><small>合并后读放大</small><strong>${r.read_amplification_after}</strong></div><div><small>存活键 / 操作</small><strong>${r.live_keys} / ${r.operation_count}</strong></div><div><small>刷盘 / 合并</small><strong>${r.flushes} / ${r.compactions}</strong></div></div>
  <p class="modelnote">合并前 ${r.read_amplification_before} → 合并后 ${r.read_amplification_after}；范围命中 ${r.table_reads_before} → ${r.table_reads_after} 次，点查集合 ${r.probe_count} 键。<br>actual: ${esc(r.actual_checksum)}<br>oracle: ${esc(r.oracle_checksum)}</p>
  <div class="split"><section><h3>合并前层级</h3>${levels(r.before_levels||[])}<h3>合并后层级</h3>${levels(r.after_levels||[])}</section></div>`;
}
export async function selectionsPage(el){
  const ds=await datasets.load();const cs=await configs.load();const ss=await selections.load();
  const id=Number(location.hash.split('/')[1])||0;const chosen=id?await detail(id):null;
  const writable=session.get().role!=='viewer';
  el.innerHTML=header('05','多个站点共用一套配置，也要守住最差批次','为候选配置与写入批次做限额演算：每个组合都必须读出正确键值且写放大不超过上限，再按最差批次的合并后读放大择优。预算用完时明确区分已证最优、无解与证据不足。')
  +`<div class="split"><section class="panel"><h2>选型任务</h2><div class="actions" style="margin:0 0 20px"><button id="refresh">刷新进度</button><span class="muted">${ss.length} 个任务 · 排名口径：最差批次优先</span></div>${table(['任务','候选','结论','演算额度','进度'],ss.map(s=>[esc(s.name)+'<br><small>#'+s.id+' / '+esc(s.created_at)+'</small>',`${s.dataset_ids.length} 批次 × ${s.config_ids.length} 配置`,outcomeBadge(s.status),`${s.evaluated_pairs}/${s.simulation_budget}`,summaryLine(s.summary)+'<div class="actions" style="margin-top:8px"><a href="#selections/'+s.id+'">查看各批次证据 →</a></div>']))}</section><aside class="panel"><h2>${writable?'发起限额选型':'只读说明'}</h2>${writable?`<form id="selection-form"><label>任务名称</label><input name="name" required maxlength="120" value="sizing-${Date.now().toString().slice(-6)}"><label>候选写入批次 · 可多选</label><select name="dataset_ids" multiple size="${Math.min(Math.max(ds.length,3),6)}">${ds.map(x=>`<option value="${x.id}">${esc(x.name)} / ${x.operation_count}条</option>`).join('')}</select><label>候选存储配置 · 可多选</label><select name="config_ids" multiple size="${Math.min(Math.max(cs.length,3),6)}">${cs.map(x=>`<option value="${x.id}" ${x.id===cs[0]?.id?'selected':''}>${esc(x.name)}</option>`).join('')}</select><div class="actions" style="gap:14px"><div style="flex:1"><label>写放大上限</label><input type="number" name="write_amplification_cap" min="0.01" max="1000" step="0.01" value="2" required></div><div style="flex:1"><label>演算次数上限</label><input type="number" name="simulation_budget" min="1" max="5000" value="24" required></div><div style="flex:1"><label>每批评估组合数</label><input type="number" name="chunk_size" min="1" max="200" value="4" required></div></div><div class="actions"><button class="primary" type="submit">提交限额选型 →</button></div></form><p class="helper">按住 Ctrl/⌘ 多选。组合总数 = 批次 × 配置；按最差批次读放大择优，平手再比平均读放大。每评估一个组合消耗一次演算额度，已落盘组合重跑不重复扣减。额度用尽时区分“已证最优 / 无解 / 证据不足”。</p>`:''}</aside></div>`
  +`<section class="panel" id="detail"></section>`;
  el.querySelector('#refresh').onclick=()=>selectionsPage(el);
  const form=el.querySelector('#selection-form');
  if(form)form.onsubmit=e=>{e.preventDefault();submit(form,async()=>{
    const fd=new FormData(form);const payload={name:fd.get('name'),dataset_ids:[...form.dataset_ids.selectedOptions].map(o=>Number(o.value)),config_ids:[...form.config_ids.selectedOptions].map(o=>Number(o.value)),write_amplification_cap:Number(fd.get('write_amplification_cap')),simulation_budget:Number(fd.get('simulation_budget')),chunk_size:Number(fd.get('chunk_size'))};
    if(!payload.dataset_ids.length||!payload.config_ids.length)throw Error('至少选择一个批次和一份配置');
    const made=await create(payload);notify('选型任务已提交，worker 将分批演算');location.hash='selections/'+made.id;await selectionsPage(el)})};
  const box=el.querySelector('#detail');
  if(!chosen){box.innerHTML='<div class="empty">选择上方任务的“查看各批次证据”，逐组合核对写放大、读放大与 Oracle 摘要。</div>';return}
  const s=chosen.summary||{};const winner=s.winner;
  const winnerName=chosen.configs.find(c=>c.id===winner?.config_id)?.name;
  const resumable=['queued','running'].includes(chosen.status)&&chosen.evaluated_pairs<chosen.simulation_budget;
  box.innerHTML=`<div class="actions" style="margin-top:0"><h2 style="margin:0">${esc(chosen.name)}</h2>${outcomeBadge(chosen.status)}<button id="download">下载完整 JSON ↓</button>${resumable&&writable?'<button id="resume">重新入队继续演算</button>':''}</div>
  <div class="metrics"><div><small>演算额度 · 已用 / 上限</small><strong>${chosen.evaluated_pairs} / ${chosen.simulation_budget}</strong></div><div><small>组合 · 已评估 / 总数</small><strong>${s.pairs?.evaluated??0} / ${s.pairs?.total??chosen.total_pairs}</strong></div><div><small>写放大上限</small><strong>${chosen.write_amplification_cap} ×</strong></div><div><small>最差批次读放大 · 胜出</small><strong>${pct(winner?.worst_read_amplification)}</strong></div></div>
  <div class="modelnote">${esc(s.ranking||'')}<br>结论：<b>${esc(outcome(chosen.status).label)}</b> — ${esc(s.note||'')}${winnerName?'<br>胜出配置：<b>'+esc(winnerName)+'</b>（#'+winner.config_id+'）':''}<br>中断或服务重启后，worker 会按数据库已落盘的组合续跑，不重复消耗额度。普通单次试验仍在“合并队列”照常使用。</div>
  <h2 style="margin-top:30px">候选配置排名</h2>${configTable(chosen)}
  ${winner?`<h2 style="margin-top:30px">胜出配置逐批次表现</h2>${table(['批次','合并后读放大','范围命中','写放大','Oracle'],(winner.per_batch||[]).map(b=>[esc(chosen.datasets.find(d=>d.id===b.dataset_id)?.name||('#'+b.dataset_id)),pct(b.read_amplification_after),`${b.table_reads_after}/${b.probe_count}`,pct(b.write_amplification),b.oracle_verified?'<span class="badge passed">一致</span>':'<span class="badge failed">不一致</span>']))}`:''}
  <h2 style="margin-top:30px">各批次证据</h2>${evalTable(chosen)}
  <div id="evidence"></div>`;
  const dl=el.querySelector('#download');if(dl)dl.onclick=()=>download(chosen.id).catch(e=>notify(e.message));
  const rb=el.querySelector('#resume');if(rb)rb.onclick=()=>submit(box,async()=>{await resume(chosen.id);notify('已重新入队，worker 将从未落盘组合继续');await selectionsPage(el)});
  const ev=el.querySelector('#evidence');
  if(chosen.evals?.length){
    ev.innerHTML='<h2 style="margin-top:30px">逐组合查看</h2><label>已完成组合</label><select id="eval-pick">'+chosen.evals.map((e,i)=>`<option value="${i}">${esc(e.dataset_name)} × ${esc(e.config_name)} · ${esc(e.status)}</option>`).join('')+'</select><div id="eval-body" class="panel subtle"></div>';
    const body=ev.querySelector('#eval-body');const pick=ev.querySelector('#eval-pick');
    const show=()=>{body.innerHTML=evidenceDetail(chosen,chosen.evals[Number(pick.value)||0]||chosen.evals[0])};pick.onchange=show;show();
  }
}
