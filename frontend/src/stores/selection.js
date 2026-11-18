import{list}from'../api/selection.js';export const selections={items:[],async load(){this.items=await list();return this.items}};
