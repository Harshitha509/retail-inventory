const API = import.meta.env.VITE_API_URL || 'http://localhost:8000';
async function request<T>(path: string): Promise<T> {
  const response = await fetch(`${API}${path}`, { credentials: 'include' });
  if (!response.ok) throw new Error(`API ${path} returned ${response.status}`);
  return response.json() as Promise<T>;
}
export type InventoryRow = {id:string;name:string;category:string;price:number;currency:'INR';supplier:string|null;supplier_id:string;lead_time:number|null;stock:number;reserved:number;available:number;daily:number;sales_days:number;days_remaining:number|null;reorder_point:number;reorder_threshold:number;risk:string};
export type Recommendation = InventoryRow & {recommendation_id:number;quantity:number;risk_level:string;status:string;reason:string};
export type Overview = {products:number;at_risk:number;coverage:number;recommendations:number;risk_distribution:Record<string,number>;synthetic:boolean};
export type AuthUser = {id:number;email:string;full_name:string;role:string;must_change_password:boolean};
export type ManagedUser = {id:number;email:string;full_name:string;role:string;is_active:boolean;created_at:string|null;last_login_at:string|null;must_change_password:boolean};
export type AuditRecord = {event_id:number;actor_user_id:number|null;actor_name:string|null;action_type:string;target_entity:string|null;target_id:string|null;occurred_at:string|null;outcome:string;details:Record<string,unknown>};
export type PipelineStage = {name:string;order:number;status:string;started_at:string|null;finished_at:string|null;duration_ms:number;input_rows:number;accepted_rows:number;rejected_rows:number;details:Record<string,unknown>;error:string|null};
export type PipelineMetrics = {rows_extracted:number;valid_rows:number;new_records_inserted:number;existing_records_skipped:number;records_updated:number;rejected_rows:number};
export type PipelineRejection = {dataset:string;row_number:number|null;field:string;description:string;key?:string};
export type PipelineRun = {run_id:number;status:string;started_at:string|null;finished_at:string|null;duration_ms:number;records_processed:number;input_rows:number;accepted_rows:number;rejected_rows:number;duplicate_rows:number;metrics:PipelineMetrics;configuration:Record<string,number>;source_files:Record<string,{file:string;rows:number;bytes:number}>;validation_report:Record<string,unknown>;errors:Array<PipelineRejection|string>;warnings:string[];actor_user_id:number|null;stages:PipelineStage[]};
function cookie(name:string):string { return document.cookie.split('; ').find(part=>part.startsWith(`${name}=`))?.split('=').slice(1).join('=') ?? ''; }
async function authPost<T>(path:string,body:unknown):Promise<T>{const response=await fetch(`${API}${path}`,{method:'POST',credentials:'include',headers:{'Content-Type':'application/json',...(cookie('fieldnote_csrf')?{'X-CSRF-Token':decodeURIComponent(cookie('fieldnote_csrf'))}:{})},body:JSON.stringify(body)});if(!response.ok){const data=await response.json().catch(()=>({}));throw new Error(data.detail||'Authentication request failed');}return response.status===204?undefined as T:response.json() as Promise<T>;}
export const api = {
  me: () => request<AuthUser>('/auth/me'),
  login: (email:string,password:string) => authPost<{user:AuthUser}>('/auth/login',{email,password}),
  logout: () => authPost<{status:string}>('/auth/logout',{}),
  changePassword: (current_password:string,new_password:string) => authPost<void>('/auth/change-password',{current_password,new_password}),
  users: async (params:URLSearchParams) => request<{items:ManagedUser[];total:number;page:number;page_size:number}>(`/users?${params}`),
  createUser: (body:{email:string;full_name:string;role:string;initial_password:string}) => authPost<ManagedUser>('/users',body),
  updateUser: async (id:number,body:{full_name?:string;role?:string;is_active?:boolean}) => {const r=await fetch(`${API}/users/${id}`,{method:'PATCH',credentials:'include',headers:{'Content-Type':'application/json','X-CSRF-Token':decodeURIComponent(cookie('fieldnote_csrf'))},body:JSON.stringify(body)});if(!r.ok){const x=await r.json().catch(()=>({}));throw new Error(x.detail||'User update failed');}return r.json() as Promise<ManagedUser>;},
  resetUserPassword: (id:number,new_password:string) => authPost<{status:string;must_change_password:boolean}>(`/users/${id}/reset-password`,{new_password}),
  auditLogs: async(params:URLSearchParams) => request<{items:AuditRecord[];total:number;page:number;page_size:number}>(`/audit-logs?${params}`),
  startPipeline: (body:{safety_days:number;target_days:number}) => authPost<{run_id:number;status:string;status_url:string}>('/pipeline/run',body),
  pipelineRun: (id:number) => request<PipelineRun>(`/pipeline/runs/${id}`),
  overview: () => request<Overview>('/analytics/overview'),
  inventory: async (params = new URLSearchParams({page_size:'500'})): Promise<InventoryRow[]> => {
    const payload: unknown = await request(`/inventory?${params}`);
    if (Array.isArray(payload)) return payload as InventoryRow[];
    if (payload && typeof payload === 'object' && 'items' in payload && Array.isArray((payload as {items:unknown}).items)) return (payload as {items:InventoryRow[]}).items;
    throw new Error('Inventory API returned an unexpected response shape');
  },
  risks: async (): Promise<InventoryRow[]> => {
    const payload: unknown = await request('/inventory/risks');
    if (!Array.isArray(payload)) throw new Error('Inventory risks API returned an unexpected response shape');
    return payload as InventoryRow[];
  },
  trends: (days=30) => request<Array<{date:string;units:number;revenue:number;currency:'INR'}>>(`/sales/trends?days=${days}`),
  recommendations: () => request<Recommendation[]>('/replenishment/recommendations'),
  updateInventory: async(id:string,available:number,reserved:number) => {
    const params=new URLSearchParams({available_stock:String(available),reserved_stock:String(reserved)});
    const response=await fetch(`${API}/inventory/${encodeURIComponent(id)}?${params}`,{method:'PUT',credentials:'include',headers:{'X-CSRF-Token':decodeURIComponent(cookie('fieldnote_csrf'))}});
    if(!response.ok){const data=await response.json().catch(()=>({}));throw new Error(data.detail||'Inventory update failed');}
    return response.json() as Promise<{product_id:string;available_stock:number;reserved_stock:number}>;
  },
  history: () => request<PipelineRun[]>('/pipeline/history'),
};
