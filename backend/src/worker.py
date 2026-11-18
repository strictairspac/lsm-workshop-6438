import time,json,traceback
from pathlib import Path
from sqlalchemy import select
from .config.database import Session
from .models.trial import Trial
from .models.selection import Selection
from .services.trial import queue
from .services.lsm_engine import simulate
from .services.selection import process_chunk,enqueue,claim,detail,OUTCOMES
heartbeat=Path('/artifacts/worker.heartbeat')
def run_one(ident):
    with Session() as db:
        x=db.get(Trial,ident)
        if not x or x.result is not None:return
        x.status='running';db.commit()
        try:
            x.result=simulate(x.input_snapshot['entries'],x.input_snapshot['config']);x.status='passed'
        except Exception as exc:x.result={'error':str(exc)[:500]};x.status='failed'
        db.commit();Path(f'/artifacts/lsm-trial-{ident}.json').write_text(json.dumps(x.result,ensure_ascii=False,indent=2))
def run_selection(ident):
    """Evaluate one chunk, persist it, then requeue until an outcome is reached.

    Each (dataset, config) pair is committed individually with a unique index,
    so a crash mid-chunk never re-evaluates or re-charges a recorded pair.
    """
    try:
        claim(ident)
    except Exception:traceback.print_exc()
    with Session() as db:
        try:
            status=process_chunk(db,ident)
            if status and status not in OUTCOMES:enqueue(ident)
            Path(f'/artifacts/lsm-selection-{ident}.json').write_text(json.dumps(detail(db,ident),ensure_ascii=False,indent=2))
        except Exception:
            traceback.print_exc();db.rollback()
            try:enqueue(ident)
            except Exception:traceback.print_exc()
def recover():
    with Session() as db:
        for ident in db.scalars(select(Trial.id).where(Trial.status.in_(['queued','running']))):run_one(ident)
    # The consumer may have died after BLPOP but before claiming, so the active
    # marker alone cannot be trusted. When the queue is empty, re-push every
    # unresolved selection; recorded pairs are unique and never re-charged, so a
    # duplicate item only triggers an idempotent chunk.
    with Session() as db:
        pending=list(db.scalars(select(Selection.id).where(Selection.status.in_(['queued','running']))))
    if pending and queue.llen('lsm:selections')==0:
        for ident in pending:
            try:queue.rpush('lsm:selections',ident)
            except Exception:traceback.print_exc()
while True:
    try:
        heartbeat.write_text(str(time.time()))
        item=queue.blpop(['lsm:selections','lsm:jobs'],timeout=2)
        if item:
            if item[0]==b'lsm:selections':run_selection(int(item[1]))
            else:run_one(int(item[1]))
        else:recover()
    except Exception:traceback.print_exc();time.sleep(2)
