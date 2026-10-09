export class ApiError extends Error { constructor(message,code,status,detail){super(message);this.code=code;this.status=status;this.detail=detail} }
export async function request(path,{method='GET',body,signal}={}) {
  const response = await fetch(`/api${path}`, {method,signal,credentials:'same-origin',headers:body===undefined?{}:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)})
  const data = await response.json().catch(()=>({}))
  if(!response.ok){const error=data.error||{};throw new ApiError(error.message||`${response.status} ${response.statusText}`,error.code,response.status,error.detail)}
  return data
}
export const post = (path,body={})=>request(path,{method:'POST',body})
export function queryString(values){const params=new URLSearchParams();Object.entries(values).forEach(([key,value])=>{if(value!==undefined&&value!==null&&value!=='')params.set(key,Array.isArray(value)?value.join(','):String(value))});return params.toString()}
export const isActiveJob = job=>!!job&&['queued','running'].includes(job.status)
export const paperKey = paper=>paper?.paper_key||''
export function safeUrl(value){try{const url=new URL(value,location.origin);return ['https:','http:'].includes(url.protocol)?url.href:''}catch{return ''}}
export function acceptsJobSnapshot(current,incoming,activeProjectId){return !!current&&!!incoming&&current.job_id===incoming.job_id&&current.project_id===activeProjectId&&incoming.project_id===activeProjectId}
