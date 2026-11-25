import { lazy, Suspense, useCallback, useEffect, useMemo, useState, type FormEvent } from 'react';
import { LayoutDashboard, Boxes, TriangleAlert, Truck, ChartNoAxesCombined, Database, Leaf, ChevronDown, Users, ScrollText, UserRound, LogOut, PackageCheck, Bell } from 'lucide-react';
import { api, type AuthUser, type InventoryRow, type Overview, type PipelineRun, type Recommendation } from './api';
import { ROUTES, navigate, routeLink, routePath, useBrowserLocation } from './routing';

const OverviewPage=lazy(()=>import('./pages').then(m=>({default:m.OverviewPage})));
const AnalyticsPage=lazy(()=>import('./pages').then(m=>({default:m.AnalyticsPage})));
const AuditPage=lazy(()=>import('./pages').then(m=>({default:m.AuditPage})));
const DataOperationsPage=lazy(()=>import('./pages').then(m=>({default:m.DataOperationsPage})));
const InventoryPage=lazy(()=>import('./pages').then(m=>({default:m.InventoryPage})));
const NotFoundPage=lazy(()=>import('./pages').then(m=>({default:m.NotFoundPage})));
const PasswordChange=lazy(()=>import('./pages').then(m=>({default:m.PasswordChange})));
const ProfilePage=lazy(()=>import('./pages').then(m=>({default:m.ProfilePage})));
const ReplenishmentPage=lazy(()=>import('./pages').then(m=>({default:m.ReplenishmentPage})));
const StockoutPage=lazy(()=>import('./pages').then(m=>({default:m.StockoutPage})));
const UsersPage=lazy(()=>import('./pages').then(m=>({default:m.UsersPage})));

type Trend = {date:string;units:number;revenue:number;currency:'INR'};
const navItems = [
  ['Overview',ROUTES.overview,LayoutDashboard],['Inventory',ROUTES.inventory,Boxes],['Stockout intelligence',ROUTES.stockout,TriangleAlert],
  ['Replenishment',ROUTES.replenishment,Truck],['Analytics',ROUTES.analytics,ChartNoAxesCombined],['Data Operations',ROUTES.operations,Database],
] as const;

export default function App(){
  const location=useBrowserLocation();
  const path=routePath(location);
  const [user,setUser]=useState<AuthUser|null>(null);
  const [authChecking,setAuthChecking]=useState(true);
  const [authError,setAuthError]=useState('');
  const [email,setEmail]=useState('');
  const [password,setPassword]=useState('');
  const [authBusy,setAuthBusy]=useState(false);
  const [overview,setOverview]=useState<Overview|null>(null);
  const [inventory,setInventory]=useState<InventoryRow[]>([]);
  const [risks,setRisks]=useState<InventoryRow[]>([]);
  const [trends,setTrends]=useState<Trend[]>([]);
  const [trendDays,setTrendDays]=useState(30);
  const [recommendations,setRecommendations]=useState<Recommendation[]>([]);
  const [history,setHistory]=useState<PipelineRun[]>([]);
  const [selectedRun,setSelectedRun]=useState<PipelineRun|null>(null);
  const [loading,setLoading]=useState(true);
  const [errors,setErrors]=useState<Record<string,string>>({});
  const [pipelineBusy,setPipelineBusy]=useState(false);
  const [pipelineMessage,setPipelineMessage]=useState('');
  const [pipelineError,setPipelineError]=useState('');

  useEffect(()=>{let active=true;api.me().then(value=>{if(active)setUser(value);}).catch(()=>{if(active)setUser(null);}).finally(()=>{if(active)setAuthChecking(false);});return()=>{active=false;};},[]);
  useEffect(()=>{if(authChecking)return;if(!user){if(path!== '/login')navigate('/login',true);return;}if(path==='/login'||path==='/')navigate(ROUTES.overview,true);},[authChecking,user,path]);

  const loadCore=useCallback(async()=>{
    setLoading(true);const next:Record<string,string>={};
    const results=await Promise.allSettled([api.overview(),api.inventory(),api.risks(),api.trends(trendDays),api.recommendations(),api.history()]);
    const [o,i,r,t,recs,h]=results;
    if(o.status==='fulfilled')setOverview(o.value);else next.overview=reasonText(o.reason,'Analytics could not be loaded.');
    if(i.status==='fulfilled')setInventory(i.value);else next.inventory=reasonText(i.reason,'Inventory could not be loaded.');
    if(r.status==='fulfilled')setRisks(r.value);else next.risks=reasonText(r.reason,'Stockout risks could not be loaded.');
    if(t.status==='fulfilled')setTrends(t.value);else next.trends=reasonText(t.reason,'Sales trends could not be loaded.');
    if(recs.status==='fulfilled')setRecommendations(recs.value);else next.recommendations=reasonText(recs.reason,'Replenishment recommendations could not be loaded.');
    if(h.status==='fulfilled')setHistory(h.value);else next.history=reasonText(h.reason,'Pipeline history could not be loaded.');
    setErrors(next);setLoading(false);
  },[trendDays]);
  useEffect(()=>{if(user&&!user.must_change_password)void loadCore();},[user,loadCore]);
  const refreshInventory=useCallback(async()=>{try{setInventory(await api.inventory());setErrors(previous=>{const next={...previous};delete next.inventory;return next;});}catch(reason){setErrors(previous=>({...previous,inventory:reasonText(reason,'Inventory could not be loaded.')}));}},[]);
  const refreshRun=useCallback(async(id:number)=>{const run=await api.pipelineRun(id);setSelectedRun(run);setHistory(previous=>[run,...previous.filter(item=>item.run_id!==id)]);return run;},[]);
  const selectRun=useCallback((id:number)=>refreshRun(id),[refreshRun]);
  const runPipeline=useCallback(async()=>{
    if(pipelineBusy)return;setPipelineBusy(true);setPipelineError('');setPipelineMessage('');
    try{const started=await api.startPipeline({safety_days:5,target_days:30});let current=await refreshRun(started.run_id);let attempts=0;
      while(['RUNNING','QUEUED'].includes(current.status)&&attempts<300){await new Promise(resolve=>window.setTimeout(resolve,1000));current=await refreshRun(started.run_id);attempts++;}
      if(['RUNNING','QUEUED'].includes(current.status))throw new Error(`Run ${current.run_id} is still running; its status remains in history.`);
      setPipelineMessage(`Run ${current.run_id} finished with ${current.status}.`);await loadCore();
    }catch(reason){setPipelineError(reasonText(reason,'Pipeline request failed.'));}
    finally{setPipelineBusy(false);api.history().then(setHistory).catch(()=>undefined);}
  },[pipelineBusy,refreshRun,loadCore]);

  const doLogin=async(event:FormEvent)=>{event.preventDefault();setAuthBusy(true);setAuthError('');try{const result=await api.login(email,password);setUser(result.user);setPassword('');navigate(result.user.must_change_password?'/profile':ROUTES.overview,true);}catch(reason){setAuthError(reasonText(reason,'Sign in failed.'));}finally{setAuthBusy(false);}};
  const doLogout=async()=>{try{await api.logout();}catch{/* local state still clears so the protected app is left */}finally{setUser(null);setPassword('');navigate('/login',true);}};
  const pageTitle=useMemo(()=>({[ROUTES.overview]:'Overview',[ROUTES.inventory]:'Inventory',[ROUTES.stockout]:'Stockout intelligence',[ROUTES.replenishment]:'Replenishment',[ROUTES.analytics]:'Analytics',[ROUTES.operations]:'Data Operations',[ROUTES.users]:'User Management',[ROUTES.audit]:'Audit Logs',[ROUTES.profile]:'Profile / Account Settings'} as Record<string,string>)[path]??'Page not found',[path]);
  const canRun=['ADMIN','DATA_ENGINEER'].includes(user?.role??'');
  const setDays=useCallback((days:number)=>setTrendDays(days),[]);
  const onPasswordDone=useCallback(()=>{void doLogout();},[]);

  if(authChecking)return <div className="auth-screen"><div className="auth-card" role="status">Restoring secure session…</div></div>;
  if(!user)return <div className="auth-screen"><form className="auth-card" onSubmit={doLogin}><Brand auth/><p className="eyebrow">SECURE WORKSPACE</p><h1>Welcome back.</h1><p className="sub">Sign in to your inventory workspace.</p><label>Email<input type="email" autoComplete="username" required maxLength={320} value={email} onChange={event=>setEmail(event.target.value)}/></label><label>Password<input type="password" autoComplete="current-password" required maxLength={256} value={password} onChange={event=>setPassword(event.target.value)}/></label>{authError&&<div role="alert" className="auth-error">{authError}</div>}<button className="primary" disabled={authBusy}>{authBusy?'Signing in…':'Sign in'}</button><small>Accounts are provisioned by an administrator.</small></form></div>;
  if(user.must_change_password)return <div className="auth-screen"><Suspense fallback={<div className="auth-card" role="status">Loading account settings…</div>}><PasswordChange required onDone={onPasswordDone}/></Suspense></div>;

  let page;
  if(path===ROUTES.overview||path==='/')page=<OverviewPage overview={overview} inventory={inventory} risks={risks} trends={trends} history={history} loading={loading} error={[errors.overview,errors.inventory,errors.risks,errors.trends,errors.history].filter(Boolean).join(' ')} onViewAll={()=>navigate(`${ROUTES.stockout}?risk=at-risk`)}/>;
  else if(path===ROUTES.inventory)page=<InventoryPage rows={inventory} loading={loading} error={errors.inventory??''} canUpdate={['ADMIN','INVENTORY_MANAGER'].includes(user.role)} onRefresh={refreshInventory}/>;
  else if(path===ROUTES.stockout)page=<StockoutPage rows={inventory} loading={loading} error={errors.inventory??''}/>;
  else if(path===ROUTES.replenishment)page=<ReplenishmentPage items={recommendations} loading={loading} error={errors.recommendations??''}/>;
  else if(path===ROUTES.analytics)page=<AnalyticsPage inventory={inventory} trends={trends} loading={loading} error={errors.trends??errors.inventory??''} onDaysChange={setDays}/>;
  else if(path===ROUTES.operations)page=<DataOperationsPage allowed={canRun} canInspect={['ADMIN','DATA_ENGINEER'].includes(user.role)} busy={pipelineBusy} error={pipelineError||errors.history||''} message={pipelineMessage} run={runPipeline} history={history} selected={selectedRun} select={selectRun}/>;
  else if(path===ROUTES.users&&user.role==='ADMIN')page=<UsersPage/>;
  else if(path===ROUTES.audit&&user.role==='ADMIN')page=<AuditPage/>;
  else if(path===ROUTES.profile)page=<ProfilePage user={user} onPasswordDone={onPasswordDone}/>;
  else page=<NotFoundPage/>;

  return <div className="shell"><aside className="sidebar"><Brand/><div className="workspace">DEMO OPERATING CONTEXT <ChevronDown size={13}/><b>Bengaluru · Karnataka · India</b></div><nav aria-label="Primary navigation">
    {navItems.map(([label,href,Icon])=><a key={href} href={href} aria-current={path===href?'page':undefined} className={path===href?'active':''} onClick={event=>routeLink(event,href)}><Icon size={17}/>{label}{label==='Stockout intelligence'&&overview&&overview.at_risk>0&&<i>{overview.at_risk}</i>}</a>)}
    {user.role==='ADMIN'&&<><a href={ROUTES.users} className={path===ROUTES.users?'active':''} aria-current={path===ROUTES.users?'page':undefined} onClick={event=>routeLink(event,ROUTES.users)}><Users size={17}/>User Management</a><a href={ROUTES.audit} className={path===ROUTES.audit?'active':''} aria-current={path===ROUTES.audit?'page':undefined} onClick={event=>routeLink(event,ROUTES.audit)}><ScrollText size={17}/>Audit Logs</a></>}
    <a href={ROUTES.profile} className={path===ROUTES.profile?'active':''} aria-current={path===ROUTES.profile?'page':undefined} onClick={event=>routeLink(event,ROUTES.profile)}><UserRound size={17}/>Profile / Account Settings</a>
  </nav><div className="sidebottom"><div className="avatar">{user.full_name.slice(0,2).toUpperCase()}</div><span className="user-identity">{user.full_name}<small>{user.role}</small></span><button className="logout-button" onClick={()=>void doLogout()} aria-label="Log out"><LogOut size={15}/><span>Log out</span></button></div></aside>
    <main><header><div><span className="crumb">Workspace /</span> {pageTitle}</div><div className="headright"><span className="demo">{overview?.synthetic?'● SYNTHETIC DEMO DATA':'● LIVE DATABASE'}</span><button className="icon" aria-label="Notifications"><Bell size={17}/></button><div className="avatar">{user.full_name.slice(0,2).toUpperCase()}</div></div></header><section className="content"><Suspense fallback={<div className="panel state-panel" role="status">Loading page…</div>}>{page}</Suspense><footer>FIELDNOTE INVENTORY <span>Reliable stock, fewer surprises. · Bengaluru, India</span></footer></section></main>
  </div>;
}

function Brand({auth=false}:{auth?:boolean}){return <div className={`brand ${auth?'auth-brand':''}`}><div className="logo"><Leaf size={19}/></div><span>fieldnote<small>RETAIL INTELLIGENCE</small></span></div>;}
function reasonText(reason:unknown,fallback:string){return reason instanceof Error?reason.message:fallback;}
