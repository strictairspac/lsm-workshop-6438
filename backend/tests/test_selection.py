import os,sys,tempfile
os.environ['DATABASE_URL']='sqlite:///'+os.path.join(tempfile.mkdtemp(),'selection.db')
os.environ['JWT_SECRET']='test-secret'
os.environ['REDIS_URL']='redis://127.0.0.1:6399/15'
os.environ['SEED_DEMO']='false'
sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from src.config.database import Base,engine,Session
from src.models.dataset import Dataset
from src.models.config import Config
from src.models.selection import Selection,SelectionEval
from src.services import selection as svc
from src.schemas.selection import SelectionInput
DEMO=[{'op':'put','key':'sensor-01','value':'18.2'},{'op':'put','key':'sensor-02','value':'19.1'},{'op':'put','key':'sensor-03','value':'22.4'},{'op':'put','key':'sensor-04','value':'17.0'},{'op':'put','key':'sensor-01','value':'18.8'},{'op':'delete','key':'sensor-02'},{'op':'put','key':'sensor-05','value':'21.6'},{'op':'put','key':'sensor-06','value':'20.3'},{'op':'delete','key':'sensor-03'},{'op':'put','key':'sensor-07','value':'23.0'},{'op':'put','key':'sensor-01','value':'19.0'},{'op':'put','key':'sensor-08','value':'18.9'}]
RESUR=[{'op':'put','key':'sensor-a','value':'old'},{'op':'put','key':'sensor-b','value':'b'},{'op':'put','key':'sensor-c','value':'c'},{'op':'put','key':'sensor-d','value':'d'},{'op':'delete','key':'sensor-a'},{'op':'put','key':'sensor-e','value':'e'}]
# Two batches where the average-best configuration is not the worst-batch-best one.
DIVERGE_A=[{'op':'put','key':'k2','value':'4'},{'op':'delete','key':'k4'},{'op':'put','key':'k3','value':'8'},{'op':'put','key':'k4','value':'7'},{'op':'put','key':'k4','value':'5'},{'op':'put','key':'k5','value':'1'},{'op':'put','key':'k4','value':'6'},{'op':'delete','key':'k1'},{'op':'put','key':'k2','value':'6'},{'op':'put','key':'k5','value':'4'},{'op':'put','key':'k1','value':'3'},{'op':'put','key':'k1','value':'5'},{'op':'put','key':'k4','value':'6'},{'op':'put','key':'k5','value':'6'},{'op':'put','key':'k2','value':'2'}]
DIVERGE_B=DIVERGE_A+[{'op':'put','key':'zz','value':'1'}]
class FakeQueue:
    def __init__(self):self.jobs=[];self.active=set()
    def sadd(self,key,ident):self.active.add(ident);return 1
    def srem(self,key,ident):self.active.discard(ident)
    def rpush(self,key,ident):self.jobs.append((key,ident));return 1
@pytest.fixture(autouse=True)
def fresh():
    Base.metadata.drop_all(engine);Base.metadata.create_all(engine)
    svc.queue=FakeQueue()
    with Session() as db:
        for name,entries in [('demo',DEMO),('resur',RESUR),('div-a',DIVERGE_A),('div-b',DIVERGE_B)]:
            db.add(Dataset(name=name,description='d',entries=entries))
        for name,params in [('small',(2,2,3,True)),('retain',(2,3,3,False)),('wide',(4,2,4,True)),('avg-best',(2,2,3,False)),('worst-best',(2,3,3,False))]:
            db.add(Config(name=name,memtable_limit=params[0],l0_trigger=params[1],max_levels=params[2],final_compaction=params[3]))
        db.commit()
def make(**kw):
    base=dict(name='s',dataset_ids=[1],config_ids=[2],write_amplification_cap=2.0,simulation_budget=10,chunk_size=4)
    base.update(kw)
    with Session() as db:return svc.create(db,SelectionInput(**base))
def run(db,ident,safety=100):
    for _ in range(safety):
        status=svc.process_chunk(db,ident)
        if status in svc.OUTCOMES:return status
    return status
def test_worst_batch_ranking_beats_average():
    make(name='diverge',dataset_ids=[3,4],config_ids=[4,5],write_amplification_cap=100,simulation_budget=4,chunk_size=4)
    with Session() as db:
        status=run(db,1)
        s=svc.detail(db,1)['summary']
        assert status=='proven_optimal'
        assert s['winner']['config_id']==5
        by={c['config_id']:c for c in s['configs']}
        assert by[4]['mean_read_amplification']<by[5]['mean_read_amplification']
        assert by[5]['worst_read_amplification']<by[4]['worst_read_amplification']
def test_write_amplification_cap_marks_config_infeasible():
    make(name='cap',dataset_ids=[1,2],config_ids=[1,2],write_amplification_cap=2.0,simulation_budget=6,chunk_size=2)
    with Session() as db:
        status=run(db,1)
        d=svc.detail(db,1)
        assert status=='proven_optimal'
        failed=[e for e in d['evals'] if not e['passed']]
        assert failed and all(e['status']=='write_amplification_exceeded' for e in failed)
        assert all(e['reason'].startswith('写放大') for e in failed)
def test_no_solution_when_every_config_over_cap():
    make(name='none',dataset_ids=[1],config_ids=[1,2,3],write_amplification_cap=0.5,simulation_budget=3,chunk_size=3)
    with Session() as db:
        assert run(db,1)=='no_solution'
def test_budget_exhaustion_reports_insufficient_evidence():
    make(name='tight',dataset_ids=[1,2],config_ids=[2,3,1],write_amplification_cap=100,simulation_budget=2,chunk_size=1)
    with Session() as db:
        assert run(db,1)=='insufficient_evidence'
        s=svc.detail(db,1)['summary']
        assert s['budget']['used']==2 and s['budget']['remaining']==0
        assert s['pairs']['pending']>0
def test_resume_skips_recorded_pairs_and_keeps_budget():
    make(name='resume',dataset_ids=[1,2],config_ids=[2,3],write_amplification_cap=100,simulation_budget=2,chunk_size=1)
    with Session() as db:
        assert svc.process_chunk(db,1)=='running'
        d=svc.detail(db,1)
        assert d['evaluated_pairs']==1
        # crash between chunks: re-running resumes at the next pair, no re-charge
        svc.resume(db,1)
        assert ('lsm:selections',1) in svc.queue.jobs
        assert svc.detail(db,1)['evaluated_pairs']==1
        assert svc.process_chunk(db,1)=='insufficient_evidence'
        d=svc.detail(db,1)
        assert d['evaluated_pairs']==2
        assert len({(e['dataset_id'],e['config_id']) for e in d['evals']})==2
        with pytest.raises(Exception):svc.resume(db,1)
def test_interrupted_chunk_persists_partial_progress():
    make(name='crash',dataset_ids=[1,2],config_ids=[2,3],write_amplification_cap=100,simulation_budget=4,chunk_size=4)
    original=svc._evaluate;calls={'n':0}
    def flaky(db,sel,dataset_id,config_id):
        calls['n']+=1
        if calls['n']==3:raise RuntimeError('worker interrupted')
        return original(db,sel,dataset_id,config_id)
    svc._evaluate=flaky
    with Session() as db:
        with pytest.raises(RuntimeError):svc.process_chunk(db,1)
        d=svc.detail(db,1)
        assert d['evaluated_pairs']==2
        assert d['summary']['budget']['used']==2
        assert d['status']=='running'
    svc._evaluate=original
    with Session() as db:
        assert run(db,1)=='proven_optimal'
        d=svc.detail(db,1)
        assert d['evaluated_pairs']==4
        assert d['summary']['budget']['used']==4
def test_detail_includes_evidence_per_pair():
    make(name='evidence',dataset_ids=[1,2],config_ids=[2],write_amplification_cap=100,simulation_budget=2,chunk_size=2)
    with Session() as db:
        run(db,1)
        d=svc.detail(db,1)
        assert len(d['evals'])==2
        for e in d['evals']:
            assert e['evidence']['checksums_match'] is True
            assert e['evidence']['read_amplification_after']==e['read_amplification_after']
            assert e['dataset_name'] and e['config_name']
def test_duplicate_candidates_rejected():
    with pytest.raises(Exception):SelectionInput(name='x',dataset_ids=[1,1],config_ids=[1],write_amplification_cap=2,simulation_budget=4,chunk_size=2)
