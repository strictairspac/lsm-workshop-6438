from pydantic import BaseModel,Field,model_validator
class SelectionInput(BaseModel):
    name:str=Field(min_length=2,max_length=120)
    dataset_ids:list[int]=Field(min_length=1,max_length=20)
    config_ids:list[int]=Field(min_length=1,max_length=12)
    write_amplification_cap:float=Field(gt=0,le=1000,allow_inf_nan=False)
    simulation_budget:int=Field(ge=1,le=5000)
    chunk_size:int=Field(ge=1,le=200)
    @model_validator(mode='after')
    def check_unique(self):
        if len(set(self.dataset_ids))!=len(self.dataset_ids):raise ValueError('候选写入批次不能重复')
        if len(set(self.config_ids))!=len(self.config_ids):raise ValueError('候选存储配置不能重复')
        if any(x<=0 for x in self.dataset_ids+self.config_ids):raise ValueError('批次或配置编号不合法')
        return self
