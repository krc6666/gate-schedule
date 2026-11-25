import type {HandoffPreference} from './workbench-model';

export type HandoffRoute = {indices:number[];flightNo:string};

/** Group explicit links for editing; flight scope labels belong to each edge, not the group. */
export function handoffRoutes(rows:HandoffPreference[]):HandoffRoute[] {
  const ordinary=rows.map((row,index)=>({row,index})).filter(({row})=>row.mode!=='chain');
  const sources=new Map(ordinary.map(({row,index})=>[JSON.stringify([row.flightNo,row.arrivalRole]),index]));
  const next=new Map<number,number>(),incoming=new Set<number>();
  for(const {row,index} of ordinary){
    if(!row.enabled||row.boardingRoles.length!==1)continue;
    const role=row.boardingRoles[0];
    if(!['same-flight','later-overlap','earlier-overlap'].includes(row.targetFlightScopes?.[role]??''))continue;
    const target=sources.get(JSON.stringify([row.flightNo,role]));
    if(target!==undefined&&target!==index){next.set(index,target);incoming.add(target);}
  }
  const routes:HandoffRoute[]=[],shown=new Set<number>();
  const append=(index:number)=>{
    const indices:number[]=[];
    for(let step:number|undefined=index;step!==undefined&&!indices.includes(step);step=next.get(step)){
      indices.push(step);shown.add(step);
    }
    routes.push({indices,flightNo:rows[index].flightNo});
  };
  for(const {index} of ordinary)if(!incoming.has(index))append(index);
  for(const {index} of ordinary)if(!shown.has(index))append(index);
  return routes.sort((a,b)=>Math.min(...a.indices)-Math.min(...b.indices));
}
