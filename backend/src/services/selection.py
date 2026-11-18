from fractions import Fraction
from sqlalchemy import select,func
from sqlalchemy.exc import IntegrityError
from fastapi import HTTPException
from ..models.selection import Selection,SelectionEval
from ..models.dataset import Dataset
from ..models.config import Config
from .config import serialize as config_dict
from .lsm_engine import simulate
from .trial import queue
OUTCOMES=('proven_optimal','no_solution','insufficient_evidence')
ACTIVE=('queued','running')
ACTIVE_KEY='lsm:selection_active'
def enqueue(ident):
    """Push once: the active set makes recovery idempotent."""
    if queue.sadd(ACTIVE_KEY,ident):queue.rpush('lsm:selections',ident)
def claim(ident):
    """Consumer takes ownership; a crash before requeue is recovered from the DB."""
    queue.srem(ACTIVE_KEY,ident)
def serialize(x):
    return {'id':x.id,'name':x.name,'status':x.status,'dataset_ids':x.dataset_ids,'config_ids':x.config_ids,
            'write_amplification_cap':x.write_amplification_cap,'simulation_budget':x.simulation_budget,
            'chunk_size':x.chunk_size,'total_pairs':x.total_pairs,'evaluated_pairs':x.evaluated_pairs,
            'budget_remaining':max(x.simulation_budget-x.evaluated_pairs,0),'chunks_completed':x.chunks_completed,
            'summary':x.summary,'created_at':str(x.created_at),'updated_at':str(x.updated_at)}
def _names(db,sel):
    datasets={d.id:d for d in db.scalars(select(Dataset).where(Dataset.id.in_(sel.dataset_ids)))} if sel.dataset_ids else {}
    configs={c.id:c for c in db.scalars(select(Config).where(Config.id.in_(sel.config_ids)))} if sel.config_ids else {}
    return datasets,configs
def _serialize_eval(e,datasets,configs):
    return {'dataset_id':e.dataset_id,'dataset_name':datasets[e.dataset_id].name if e.dataset_id in datasets else '已缺失',
            'config_id':e.config_id,'config_name':configs[e.config_id].name if e.config_id in configs else '已缺失',
            'passed':e.passed,'status':e.status,'reason':e.reason,'write_amplification':e.write_amplification,
            'read_amplification_after':e.read_amplification_after,'table_reads_after':e.table_reads_after,
            'probe_count':e.probe_count,'evidence':e.evidence,'created_at':str(e.created_at)}
def listing(db):return [serialize(x) for x in db.scalars(select(Selection).order_by(Selection.id.desc()))]
def detail(db,ident):
    x=db.get(Selection,ident)
    if not x:raise HTTPException(404,'选型任务不存在')
    datasets,configs=_names(db,x)
    data=serialize(x)
    data['datasets']=[{'id':i,'name':datasets[i].name if i in datasets else '已缺失','operation_count':len(datasets[i].entries) if i in datasets else None} for i in x.dataset_ids]
    data['configs']=[{'id':i,'name':configs[i].name if i in configs else '已缺失','params':config_dict(configs[i]) if i in configs else None} for i in x.config_ids]
    data['evals']=[_serialize_eval(e,datasets,configs) for e in db.scalars(select(SelectionEval).where(SelectionEval.selection_id==x.id).order_by(SelectionEval.id))]
    return data
def create(db,data):
    busy=db.scalar(select(func.count()).select_from(Selection).where(Selection.status.in_(ACTIVE)))
    if busy>=5:raise HTTPException(429,'待运行选型任务上限5个')
    datasets=list(db.scalars(select(Dataset).where(Dataset.id.in_(data.dataset_ids))))
    configs=list(db.scalars(select(Config).where(Config.id.in_(data.config_ids))))
    if len(datasets)!=len(data.dataset_ids) or len(configs)!=len(data.config_ids):
        raise HTTPException(404,'候选写入批次或存储配置不存在')
    if db.scalar(select(Selection).where(Selection.name==data.name)):raise HTTPException(409,'选型任务名称已存在')
    x=Selection(name=data.name,dataset_ids=data.dataset_ids,config_ids=data.config_ids,
                write_amplification_cap=data.write_amplification_cap,simulation_budget=data.simulation_budget,
                chunk_size=data.chunk_size,total_pairs=len(data.dataset_ids)*len(data.config_ids))
    db.add(x);db.commit();db.refresh(x)
    enqueue(x.id)
    return serialize(x)
def _rows(db,sel):
    return {(e.dataset_id,e.config_id):e for e in db.scalars(select(SelectionEval).where(SelectionEval.selection_id==sel.id))}
def _ra(row):return Fraction(row.table_reads_after,row.probe_count)
def _viable(sel,evals):
    out=[]
    for cfg in sel.config_ids:
        rows=[evals.get((ds,cfg)) for ds in sel.dataset_ids]
        if any(r is not None and not r.passed for r in rows):continue
        if all(r is not None for r in rows):
            ras=[_ra(r) for r in rows]
            out.append({'config_id':cfg,'worst':max(ras),'mean':sum(ras,Fraction(0))/len(ras),'per_batch':ras})
    return out
def _best(sel,evals):
    viable=_viable(sel,evals)
    return min(viable,key=lambda b:(b['worst'],b['mean'],b['config_id'])) if viable else None
def _pending(sel,evals,best):
    pending=[]
    for cfg in sel.config_ids:
        rows=[evals.get((ds,cfg)) for ds in sel.dataset_ids]
        if any(r is not None and not r.passed for r in rows):continue
        known=[r for r in rows if r is not None]
        if known and best is not None and max(_ra(r) for r in known)>best['worst']:continue
        pending.extend((ds,cfg) for ds in sel.dataset_ids if (ds,cfg) not in evals)
    return pending
def _status(sel,evals,best):
    infeasible=all(any(e is not None and not e.passed for e in (evals.get((ds,c)) for ds in sel.dataset_ids)) for c in sel.config_ids)
    if infeasible:return 'no_solution'
    if best is not None and not _pending(sel,evals,best):return 'proven_optimal'
    if len(evals)>=sel.simulation_budget:return 'insufficient_evidence'
    return 'running'
def _summary(sel,status,best,evals):
    configs=[]
    for cfg in sel.config_ids:
        rows=[evals.get((ds,cfg)) for ds in sel.dataset_ids]
        failed=[r for r in rows if r is not None and not r.passed]
        known=[r for r in rows if r is not None]
        entry={'config_id':cfg,'evaluated':len(known),'required':len(sel.dataset_ids)}
        if failed:
            entry.update(state='infeasible',reason=failed[0].reason,failing_dataset_id=failed[0].dataset_id)
        elif len(known)==len(sel.dataset_ids):
            ras=[_ra(r) for r in known]
            entry.update(state='viable',worst_read_amplification=float(max(ras)),mean_read_amplification=float(sum(ras,Fraction(0))/len(ras)),
                         reason='全部批次读值正确且写放大达标')
        else:
            lb=max((_ra(r) for r in known),default=None)
            entry.update(state='partial',reason='演算预算截断，尚未覆盖全部批次',
                         lower_bound_worst_read_amplification=float(lb) if lb is not None else None)
            if lb is not None and best is not None and lb>best['worst']:
                entry.update(state='dominated_by_bound',reason='已测批次的最大读放大已高于当前胜出配置，无法在最大读放大上取胜')
        configs.append(entry)
    pairs=len(sel.dataset_ids)*len(sel.config_ids)
    pending=_pending(sel,evals,best)
    out={'outcome':status,'write_amplification_cap':sel.write_amplification_cap,
         'budget':{'limit':sel.simulation_budget,'used':len(evals),'remaining':max(sel.simulation_budget-len(evals),0)},
         'pairs':{'total':pairs,'evaluated':len(evals),'pending':len(pending),'pruned':max(pairs-len(evals)-len(pending),0)},
         'ranking':'以每份候选配置在各批次的最大合并后读放大择优（最差批次优先），并列时比较平均读放大，再按配置编号。',
         'chunks_completed':sel.chunks_completed,'configs':configs}
    if best:
        per_batch=[]
        for ds in sel.dataset_ids:
            r=evals[(ds,best['config_id'])]
            per_batch.append({'dataset_id':ds,'read_amplification_after':r.read_amplification_after,'table_reads_after':r.table_reads_after,
                              'probe_count':r.probe_count,'write_amplification':r.write_amplification,'oracle_verified':r.passed})
        out['winner']={'config_id':best['config_id'],'worst_read_amplification':float(best['worst']),'mean_read_amplification':float(best['mean']),'per_batch':per_batch}
    notes={'proven_optimal':'全部候选均已有结论或不可能在最大读放大上优于胜出配置，结论已证最优。',
           'no_solution':'每份候选配置都至少在一个批次读值错误或写放大超限，没有可行配置。',
           'insufficient_evidence':'演算预算已用完，仍有候选组合未覆盖，不能证明当前领先者最优。',
           'running':'按批次组合分批演算中，进度与已完成组合已落盘。',
           'queued':'选型任务已入队，等待 worker 分批演算。'}
    out['note']=notes[status]
    return out
def process_chunk(db,ident):
    sel=db.get(Selection,ident)
    if not sel:return None
    if sel.status in OUTCOMES:return sel.status
    sel.status='running';db.commit()
    evals=_rows(db,sel);processed=0
    try:
        while processed<sel.chunk_size and len(evals)<sel.simulation_budget:
            pending=_pending(sel,evals,_best(sel,evals))
            if not pending:break
            ds,cfg=pending[0]
            _evaluate(db,sel,ds,cfg)
            evals=_rows(db,sel);processed+=1
            sel.evaluated_pairs=len(evals);db.commit()
    except Exception:
        # Persist the partial chunk before surfacing the error: recorded pairs
        # stay charged and the next resume continues from the first unrecorded
        # pair instead of replaying the whole chunk.
        db.rollback()
        evals=_rows(db,sel);best=_best(sel,evals)
        sel.evaluated_pairs=len(evals)
        status='insufficient_evidence' if len(evals)>=sel.simulation_budget else 'running'
        sel.status=status;sel.summary=_summary(sel,status,best,evals)
        db.commit()
        raise
    if processed:sel.chunks_completed=(sel.chunks_completed or 0)+1
    best=_best(sel,evals)
    status=_status(sel,evals,best)
    sel.status=status;sel.evaluated_pairs=len(evals);sel.summary=_summary(sel,status,best,evals)
    db.commit()
    return status
def _evaluate(db,sel,dataset_id,config_id):
    existing=db.scalar(select(SelectionEval).where(SelectionEval.selection_id==sel.id,
        SelectionEval.dataset_id==dataset_id,SelectionEval.config_id==config_id))
    if existing:return existing
    dataset=db.get(Dataset,dataset_id);config=db.get(Config,config_id)
    if not dataset or not config:
        row=SelectionEval(selection_id=sel.id,dataset_id=dataset_id,config_id=config_id,passed=False,status='execution_failed',
                          reason='候选批次或配置已被移除',evidence={'error':'候选批次或配置已被移除'})
        db.add(row);db.commit();return row
    try:
        r=simulate(dataset.entries,config_dict(config))
        exact=r['physical_entry_writes']/r['operation_count']
        passed=exact<=sel.write_amplification_cap+1e-12
        status='passed' if passed else 'write_amplification_exceeded'
        reason='' if passed else f"写放大 {round(exact,4)} 超过上限 {sel.write_amplification_cap:g}"
        evidence={'verified':r['verified'],'operation_count':r['operation_count'],'unique_input_keys':r['unique_input_keys'],
                  'live_keys':r['live_keys'],'flushes':r['flushes'],'compactions':r['compactions'],
                  'physical_entry_writes':r['physical_entry_writes'],'write_amplification':r['write_amplification'],
                  'read_amplification_before':r['read_amplification_before'],'read_amplification_after':r['read_amplification_after'],
                  'table_reads_before':r['table_reads_before'],'table_reads_after':r['table_reads_after'],
                  'probe_count':r['probe_count'],'tombstones_removed':r['tombstones_removed'],
                  'before_levels':r['before_levels'],'after_levels':r['after_levels'],
                  'actual_checksum':r['actual_checksum'],'oracle_checksum':r['oracle_checksum'],
                  'checksums_match':r['actual_checksum']==r['oracle_checksum']}
        row=SelectionEval(selection_id=sel.id,dataset_id=dataset_id,config_id=config_id,passed=passed,status=status,reason=reason,
                          write_amplification=r['write_amplification'],read_amplification_after=r['read_amplification_after'],
                          table_reads_after=r['table_reads_after'],probe_count=r['probe_count'],evidence=evidence)
    except Exception as exc:
        row=SelectionEval(selection_id=sel.id,dataset_id=dataset_id,config_id=config_id,passed=False,status='execution_failed',
                          reason=str(exc)[:300],evidence={'error':str(exc)[:300]})
    db.add(row)
    try:db.commit()
    except IntegrityError:
        # Another consumer recorded this pair first; keep one row and one charge.
        db.rollback()
        return db.scalar(select(SelectionEval).where(SelectionEval.selection_id==sel.id,
            SelectionEval.dataset_id==dataset_id,SelectionEval.config_id==config_id))
    return row
def resume(db,ident):
    sel=db.get(Selection,ident)
    if not sel:raise HTTPException(404,'选型任务不存在')
    if sel.status in OUTCOMES:raise HTTPException(409,'选型任务已有结论，不需要继续演算')
    if sel.evaluated_pairs>=sel.simulation_budget:raise HTTPException(409,'演算预算已用完，无法继续')
    # Explicit user resume must push even if the active marker is stale; the
    # chunk loop is idempotent, so an extra item only triggers a no-op.
    queue.sadd(ACTIVE_KEY,sel.id);queue.rpush('lsm:selections',sel.id)
    return serialize(sel)
