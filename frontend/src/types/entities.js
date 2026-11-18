/** @typedef {{id:number,name:string,description:string,entries:Array}} Dataset */
/** @typedef {{id:number,name:string,memtable_limit:number,l0_trigger:number,max_levels:number,final_compaction:boolean}} Config */
/** @typedef {{id:number,name:string,dataset_id:number,config_id:number,status:string,result:Object}} Trial */
export const entityNames={dataset:'键值批次',config:'存储配置',trial:'合并试验'};
/** @typedef {{id:number,name:string,status:string,dataset_ids:number[],config_ids:number[],write_amplification_cap:number,simulation_budget:number,chunk_size:number,total_pairs:number,evaluated_pairs:number,summary:Object}} Selection */
