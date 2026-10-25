"""Approved Plan2/tenant1Role3 minimum canonical read qualification + one read slice.
No quota definitions/values/prices, workload creation, allocation or import.
"""
from pathlib import Path
import datetime, fcntl, hashlib, json, os, runpy, traceback, urllib.error, urllib.parse, urllib.request
R=Path('/home/chabking/workspace/ani-resource-service-runs/vm-mvp-20261023-01a168ce');D=R/'v24-owner-live-prepare-01';P=R/'private/v24-owner-clients-01/qualification'
SPECS={
 'accelerator:profile:list':('ACCELERATOR',['/api/v1/accelerator/profiles']),
 'model:service:read':('MODEL',['/api/v1/models','/api/v1/models/{model_id}/versions','/api/v1/model-versions/{model_version_id}']),
 'instance:preset:read':('INSTANCE',['/api/v1/compute-presets'])}
def save(path,value,exclusive=False):
 fd=os.open(path,os.O_WRONLY|os.O_CREAT|(os.O_EXCL if exclusive else os.O_TRUNC),0o600)
 with os.fdopen(fd,'w') as out:json.dump(value,out,indent=2);out.write('\n');out.flush();os.fsync(out.fileno())
def unwrap(value):return value.get('data',value) if isinstance(value,dict) else value
def rows(value):
 if isinstance(value,list):return value
 if isinstance(value,dict):return next((value[k] for k in ('items','profiles','models','versions') if isinstance(value.get(k),list)),[])
 return []
def main():
 assert os.environ.get('SSH_CONNECTION','').split()[2:3]==['172.16.101.31']
 lock=open('/home/chabking/workspace/ani-network-service-runs/net05a-heavy.lock','a')
 try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 except BlockingIOError:raise SystemExit(73)
 output=D/'qualified-directory-01.json';assert not output.exists() and not P.exists(),'preserve unknown/original API ACKs'
 P.mkdir(mode=0o700);result={'state':'STARTED','started_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'sql_writes':0,'new_quota_definitions_values_prices':0,'workload_creates':0,'gpu_allocations':0,'model_imports':0,'operations':[],'gets':[]};phase='current-gov'
 try:
  applied=json.loads((D/'applied-03.json').read_text());pin=applied['new_process'];assert pin['pid']==3424470 and pin['start_ticks']==70793927
  M=runpy.run_path(str(R/'v24-live-prep-01/b-candidates-maintain.py'));assert M['identity'](pin['pid'],pin['start_ticks'],pin['binary_sha256'])[0]==pin
  q=runpy.run_path(str(R/'v24-live-prep-01/b-qualification.py'));g=q['g'];platform=q['platform']();tenant=q['tenant']();history=[]
  def request(method,path,body=None,token=None,mutation=False):
   assert path.startswith('/api/v1/') and not path.startswith('//')
   entry={'method':method,'path':path,'body':body,'send_attempts':0,'ack_unknown':False}
   if mutation:
    assert method in ('POST','PUT') and path in ('/api/v1/admin/permissions','/api/v1/admin/roles/3','/api/v1/admin/plan-modules') or method=='PUT' and path.startswith('/api/v1/admin/permissions/')
    index=len(result['operations']);result['operations'].append(entry);save(P/('intent-'+str(index)+'.json'),entry,True);entry.update(send_attempts=1,ack_unknown=True);save(P/('send-'+str(index)+'.json'),entry,True);save(output,result)
   data=None if body is None else json.dumps(body).encode();req=urllib.request.Request(g['HTTP']+path,data=data,headers={'Content-Type':'application/json','Origin':g['ORIGIN'],'Authorization':'Bearer '+token},method=method)
   try:
    with urllib.request.urlopen(req,timeout=35) as response:status=response.status;raw=response.read(2*1024*1024);value=json.loads(raw) if raw else {}
   except urllib.error.HTTPError as error:status=error.code;raw=error.read(2*1024*1024);value=json.loads(raw) if raw else {}
   entry.update(status=status,ack_unknown=False);history.append({'method':method,'path':path,'status':status,'response':value});save(P/'responses.json',history)
   if mutation:entry['ack_received']=True;save(P/('ack-'+str(index)+'.json'),entry,True);save(output,result)
   if not 200<=status<300:
    public={'method':method,'route':path,'http':status,'body':{k:value.get(k) for k in ('code','reason','message') if k in value}};result['gets'].append(public);save(output,result);return None,public
   event={'method':method,'route':path,'http':status};return unwrap(value),event
  def required_get(path):
   value,event=request('GET',path,token=platform);assert value is not None,'platform canonical read unavailable';return value
  phase='canonical-source-and-current-qualification'
  consumers=[v for v in rows(required_get('/api/v1/admin/tenants?noPaging=true')) if int(v.get('planId',0))==2];credentials=g['credentials']();other=json.loads((g['PRIVATE']/'isolation-credentials.json').read_text());allowed={credentials['tenant_code'],other['tenant_code']};assert consumers and {int(v['id']) for v in consumers}.issubset({1,2}) and all(v['code'] in allowed for v in consumers)
  role=required_get('/api/v1/admin/roles/3');assert int(role['tenantId'])==1 and int(role['id'])==3
  apis=rows(required_get('/api/v1/admin/apis?noPaging=true'));permissions=rows(required_get('/api/v1/admin/permissions?noPaging=true'));modules=rows(required_get('/api/v1/admin/plan-modules?noPaging=true'));save(P/'before.json',{'role':role,'apis':apis,'permissions':permissions,'modules':modules,'plan2_consumers':consumers},True)
  result['plan2_consumers']=[{k:v.get(k) for k in ('id','code','planId','status')} for v in consumers]
  # Capture the actual pre-qualification 403 bodies once. Older harness only
  # retained their reason; those historical wire bodies remain NOT_CAPTURED.
  result['before_denials']=[]
  for path in ('/api/v1/accelerator/profiles?cluster_id=5277649d-e28d-4a0a-ae76-8354365c63bc','/api/v1/models'):
   value,event=request('GET',path,token=tenant);result['before_denials'].append(event)
  expected={};canonical={}
  for code,(module,paths) in SPECS.items():
   ids=set()
   for path in paths:
    matches=[a for a in apis if a.get('method')=='GET' and a.get('path')==path and a.get('status')=='ON' and a.get('businessModule')==module];assert len(matches)==1,'canonical active GET/module missing or ambiguous';ids.add(int(matches[0]['id']))
   matches=[p for p in permissions if p.get('code')==code];assert len(matches)<=1;canonical[code]=(ids,matches)
  phase='minimum-read-permission-unions'
  for code,(module,paths) in SPECS.items():
   ids,matches=canonical[code]
   if not matches:
    permission,event=request('POST','/api/v1/admin/permissions',{'data':{'name':code,'code':code,'status':'ON','apiIds':sorted(ids)}},platform,True);assert permission is not None
   else:
    permission=matches[0];union=sorted(ids|{int(v) for v in permission.get('apiIds',[])})
    if permission.get('status')!='ON' or union!=sorted(int(v) for v in permission.get('apiIds',[])):
     reply,event=request('PUT','/api/v1/admin/permissions/'+str(permission['id']),{'data':{'status':'ON','apiIds':union},'updateMask':'status,apiIds'},platform,True);assert reply is not None
  active=rows(required_get('/api/v1/admin/permissions?noPaging=true'))
  for code,(ids,_) in canonical.items():
   match=[p for p in active if p.get('code')==code and p.get('status')=='ON'];assert len(match)==1 and ids.issubset({int(v) for v in match[0].get('apiIds',[])});expected[code]=int(match[0]['id'])
  phase='known-plan2-and-role3-unions'
  for module in ('ACCELERATOR','MODEL','INSTANCE'):
   if not any(int(v.get('planId',0))==2 and v.get('module')==module for v in modules):
    reply,event=request('POST','/api/v1/admin/plan-modules',{'data':{'planId':2,'module':module}},platform,True);assert reply is not None
  original_ids={int(v) for v in role.get('permissions',[])};union=sorted(original_ids|set(expected.values()))
  if union!=sorted(original_ids):
   fresh=required_get('/api/v1/admin/roles/3');assert fresh==role,'role changed since immutable union input'
   reply,event=request('PUT','/api/v1/admin/roles/3',{'data':{'tenantId':1,'permissions':union},'updateMask':'permissions'},platform,True);assert reply is not None
  after=required_get('/api/v1/admin/roles/3');assert int(after['tenantId'])==1 and {int(v) for v in after['permissions']}==set(union)
  for key in ('id','tenantId','code','name','status','dataScope','menus','depts'):
   assert after.get(key)==role.get(key),'unrelated role setting changed'
  result['qualification']={'role_id':3,'tenant_id':1,'permission_codes':expected,'old_permission_ids':sorted(original_ids),'union_permission_ids':union,'all_old_permissions_preserved':True,'plan2_only_known_task_consumers':True};save(P/'after-role.json',after,True)
  phase='single-corrected-normal-get-slice';tenant=q['tenant']();result['catalogs']={}
  for name,path,token in [('gpu_presets','/api/v1/compute-presets?usage=CONTAINER_INSTANCE&compute_type=gpu&page=1&pageSize=100',tenant),('gpu_profiles','/api/v1/accelerator/profiles?cluster_id=5277649d-e28d-4a0a-ae76-8354365c63bc',tenant),('tenant1_quota','/api/v1/admin/tenants/1/quota-accounts',platform),('own_models','/api/v1/models',tenant)]:
   value,event=request('GET',path,token=token);result['gets'].append(event)
   result['catalogs'][name]=(rows(value) if name=='tenant1_quota' else [{k:r.get(k) for k in ('id','name','tenant_id','tenantId','status','state','cluster_id','clusterId','compute_type','enabled','selectable','unavailable_reason','gpu','profile_id','profile_version','code','quotaCode','quota_code','unit','limit','occupied','available')} for r in rows(value)]) if value is not None else None
  result['own_versions']=[];result['ready_model_version']=None
  for model in (result['catalogs'].get('own_models') or [])[:3]:
   mid=model.get('id');assert isinstance(mid,str) and '/' not in mid
   versions,event=request('GET','/api/v1/models/'+urllib.parse.quote(mid,safe='')+'/versions',token=tenant);result['gets'].append(event)
   for version in rows(versions)[:3]:
    vid=version.get('id');assert isinstance(vid,str) and '/' not in vid
    actual,event=request('GET','/api/v1/model-versions/'+urllib.parse.quote(vid,safe=''),token=tenant);result['gets'].append(event)
    if actual:
     actual=actual.get('version',actual);assert actual.get('id')==vid;row={k:actual.get(k) for k in ('id','model_id','modelId','tenant_id','tenantId','status')};result['own_versions'].append(row)
     if str(actual.get('status','')).lower()=='ready':result['ready_model_version']=row
  assert M['identity'](pin['pid'],pin['start_ticks'],pin['binary_sha256'])[0]==pin
  result['state']='QUALIFIED_CORRECTED_READS_RECORDED_NO_CREATE'
 except Exception as error:
  location=traceback.extract_tb(error.__traceback__)[-1];result.update(state='STOPPED_PRESERVE_ORIGINAL_API_ACKS_NO_AUTOMATIC_RETRY',phase=phase,failure_type=type(error).__name__,file=Path(location.filename).name,line=location.lineno)
 finally:
  result['finished_at']=datetime.datetime.now(datetime.timezone.utc).isoformat();save(output,result);print(json.dumps({'state':result['state'],'phase':result.get('phase'),'line':result.get('line'),'operations':len(result['operations']),'receipt':str(output)}));fcntl.flock(lock,fcntl.LOCK_UN)
if __name__=='__main__':main()
