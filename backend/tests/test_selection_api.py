import os,sys,tempfile
os.environ['DATABASE_URL']='sqlite:///'+os.path.join(tempfile.mkdtemp(),'api.db')
os.environ['JWT_SECRET']='test-secret'
os.environ['REDIS_URL']='redis://127.0.0.1:6399/15'
os.environ['SEED_DEMO']='false'
sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from fastapi.testclient import TestClient
from src.config.database import Base,engine,Session
from src.models.user import User
from src.models.dataset import Dataset
from src.models.config import Config
from src.models.audit import Audit
from src.models.trial import Trial
from src.utils.password import hash_password
from src.services import selection as svc
from src.services import trial as trial_svc
DEMO=[{'op':'put','key':'sensor-01','value':'18.2'},{'op':'put','key':'sensor-02','value':'19.1'},{'op':'delete','key':'sensor-02'},{'op':'put','key':'sensor-03','value':'22.4'}]
class FakeQueue:
    def __init__(self):self.jobs=[];self.active=set();self.items=[]
    def sadd(self,key,ident):self.active.add(ident);return 1
    def srem(self,key,ident):self.active.discard(ident)
    def rpush(self,key,ident):self.jobs.append((key,ident));return 1
    def ping(self):return True
@pytest.fixture()
def client():
    Base.metadata.drop_all(engine);Base.metadata.create_all(engine)
    q=FakeQueue();svc.queue=q;trial_svc.queue=q
    with Session() as db:
        for role in ('admin','operator','viewer'):
            db.add(User(email=role+'@beiling.test',role=role,password_hash=hash_password('EdgeStore!17')))
        db.add(Dataset(name='demo',description='d',entries=DEMO))
        for name,params in [('small',(2,2,3,True)),('retain',(2,3,3,False))]:
            db.add(Config(name=name,memtable_limit=params[0],l0_trigger=params[1],max_levels=params[2],final_compaction=params[3]))
        db.commit()
    from src.main import app
    with TestClient(app) as c:
        c.queue=svc.queue
        yield c
def auth(c,role='operator'):
    r=c.post('/auth/login',json={'email':role+'@beiling.test','password':'EdgeStore!17'})
    assert r.status_code==200
    return {'Authorization':'Bearer '+r.json()['token']}
def payload(**kw):
    base=dict(name='size-1',dataset_ids=[1],config_ids=[1,2],write_amplification_cap=100,simulation_budget=4,chunk_size=2)
    base.update(kw);return base
def test_create_returns_queued_and_enqueues_once(client):
    h=auth(client)
    r=client.post('/selections',json=payload(),headers=h)
    assert r.status_code==202,r.text
    body=r.json()
    assert body['status']=='queued' and body['total_pairs']==2
    assert client.queue.jobs==[('lsm:selections',body['id'])]
def test_viewer_cannot_create(client):
    h=auth(client,'viewer')
    assert client.post('/selections',json=payload(),headers=h).status_code==403
def test_missing_candidates_404_and_duplicate_409(client):
    h=auth(client)
    assert client.post('/selections',json=payload(dataset_ids=[99]),headers=h).status_code==404
    assert client.post('/selections',json=payload(),headers=h).status_code==202
    assert client.post('/selections',json=payload(),headers=h).status_code==409
def test_unauthenticated_rejected(client):
    assert client.get('/selections').status_code in (401,403)
def test_detail_lists_batches_and_evidence(client):
    h=auth(client)
    ident=client.post('/selections',json=payload(),headers=h).json()['id']
    with Session() as db:
        svc.process_chunk(db,ident)
    r=client.get(f'/selections/{ident}',headers=h)
    assert r.status_code==200
    body=r.json()
    assert body['datasets'][0]['name']=='demo'
    assert body['configs'][0]['params']['memtable_limit']==2
    assert len(body['evals'])>=1
    assert body['evals'][0]['evidence']['probe_count']>0
    assert body['summary']['ranking']
    exp=client.get(f'/selections/{ident}/export',headers=h)
    assert exp.status_code==200 and 'attachment' in exp.headers['content-disposition']
def test_resume_only_while_unresolved(client):
    h=auth(client)
    ident=client.post('/selections',json=payload(write_amplification_cap=0.01),headers=h).json()['id']
    with Session() as db:svc.process_chunk(db,ident)
    r=client.post(f'/selections/{ident}/resume',headers=h)
    assert r.status_code==409,r.text
def test_invalid_limits_422(client):
    h=auth(client)
    for bad in (dict(simulation_budget=0),dict(chunk_size=0),dict(write_amplification_cap=0),dict(dataset_ids=[]),dict(dataset_ids=[1,1])):
        assert client.post('/selections',json=payload(**bad),headers=h).status_code==422,bad
def test_queue_cap_429(client):
    h=auth(client)
    for i in range(5):
        assert client.post('/selections',json=payload(name=f'cap-{i}'),headers=h).status_code==202
    assert client.post('/selections',json=payload(name='cap-over'),headers=h).status_code==429
def test_trials_still_work(client):
    h=auth(client)
    r=client.post('/trials',json={'name':'normal-trial','dataset_id':1,'config_id':1},headers=h)
    assert r.status_code==202,r.text
