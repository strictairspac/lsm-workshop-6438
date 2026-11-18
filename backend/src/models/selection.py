from datetime import datetime
from sqlalchemy.orm import Mapped,mapped_column
from sqlalchemy import String,JSON,ForeignKey,DateTime,Integer,Float,Boolean,UniqueConstraint,func
from ..config.database import Base
from ..constants.enums import SELECTION_STATUSES
class Selection(Base):
    __tablename__='selections'
    id:Mapped[int]=mapped_column(primary_key=True)
    name:Mapped[str]=mapped_column(String(120),unique=True)
    status:Mapped[str]=mapped_column(String(20),default=SELECTION_STATUSES[0])
    dataset_ids:Mapped[list]=mapped_column(JSON)
    config_ids:Mapped[list]=mapped_column(JSON)
    write_amplification_cap:Mapped[float]=mapped_column(Float)
    simulation_budget:Mapped[int]=mapped_column(Integer)
    chunk_size:Mapped[int]=mapped_column(Integer)
    total_pairs:Mapped[int]=mapped_column(Integer,default=0)
    evaluated_pairs:Mapped[int]=mapped_column(Integer,default=0)
    chunks_completed:Mapped[int]=mapped_column(Integer,default=0)
    summary:Mapped[dict|None]=mapped_column(JSON,nullable=True)
    created_at:Mapped[datetime]=mapped_column(DateTime,server_default=func.now())
    updated_at:Mapped[datetime]=mapped_column(DateTime,server_default=func.now(),onupdate=func.now())
class SelectionEval(Base):
    __tablename__='selection_evals'
    __table_args__=(UniqueConstraint('selection_id','dataset_id','config_id',name='uq_selection_eval_pair'),)
    id:Mapped[int]=mapped_column(primary_key=True)
    selection_id:Mapped[int]=mapped_column(ForeignKey('selections.id'),index=True)
    dataset_id:Mapped[int]=mapped_column(ForeignKey('datasets.id'))
    config_id:Mapped[int]=mapped_column(ForeignKey('configs.id'))
    passed:Mapped[bool]=mapped_column(Boolean,default=False)
    status:Mapped[str]=mapped_column(String(30))
    reason:Mapped[str]=mapped_column(String(300),default='')
    write_amplification:Mapped[float|None]=mapped_column(Float,nullable=True)
    read_amplification_after:Mapped[float|None]=mapped_column(Float,nullable=True)
    table_reads_after:Mapped[int|None]=mapped_column(Integer,nullable=True)
    probe_count:Mapped[int|None]=mapped_column(Integer,nullable=True)
    evidence:Mapped[dict|None]=mapped_column(JSON,nullable=True)
    created_at:Mapped[datetime]=mapped_column(DateTime,server_default=func.now())
