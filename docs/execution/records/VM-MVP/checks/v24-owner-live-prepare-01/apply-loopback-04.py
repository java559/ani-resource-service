"""Root-authorized exact three task PFs and same-binary Gov ADDR-only correction."""
from pathlib import Path
import datetime as dt,fcntl,hashlib,json,os,runpy,signal,subprocess,time,traceback,urllib.parse
R=Path('/home/chabking/workspace/ani-resource-service-runs/vm-mvp-20261023-01a168ce');D=R/'v24-owner-live-prepare-01';P=R/'private/v24-owner-clients-01/transport-loopback-01';Q=R/'private/v24-instance-01';O=D/'applied-loopback-04.json';K='/home/chabking/ani-four-service-deploy-20261012/tools/kubectl';KC='/home/chabking/ani-four-service-deploy-20261012/private/kubeconfig';PHASE='entry'
TARGETS=[('ACC','ani-accelerator-service','db4d1538-554b-415a-a4f1-fdc490a86947','10.96.58.69','b47a695d-50d8-4231-a786-6cb6c6b01804','ANI_ACCELERATOR_ADDR',43941),('MODEL','ani-model-grpc','2f75fd16-c191-4446-ba1a-8d57b189986c','10.96.44.130','f358e30f-842d-419a-9b25-4178caabda0f','ANI_MODEL_ADDR',43942),('INFERENCE','ani-inference-grpc','6240c73a-b1a3-470e-8258-162dfc067e8c','10.96.166.234','9efe7f65-4ccb-4bb9-b841-874851b78b21','ANI_INFERENCE_ADDR',43943)]
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,v,exclusive=False):
 fd=os.open(p,os.O_WRONLY|os.O_CREAT|(os.O_EXCL if exclusive else os.O_TRUNC),0o600)
 with os.fdopen(fd,'w') as f:json.dump(v,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
def cmd(argv,timeout=8):
 p=subprocess.run(argv,capture_output=True,text=True,timeout=timeout);assert p.returncode==0,'bounded command failed; output not public';return p.stdout

def phase(s):
 global PHASE
 PHASE=s;print(json.dumps({'phase':s}),flush=True)
def get(kind,name=None,labels=None):
 a=[K,'--kubeconfig='+KC,'-n','ani-system','get',kind]
 if name:a.append(name)
 if labels:a+=['-l',labels]
 return json.loads(cmd(a+['-o','json']))
def owner(v,kind):
 a=[o for o in v['metadata'].get('ownerReferences',[]) if o.get('controller') and o['kind']==kind];assert len(a)==1;return a[0]
def service_target(t):
 label,name,uid,ip,depuid,key,port=t;s=get('service',name);assert s['metadata']['uid']==uid and s['spec']['clusterIP']==ip and not s['metadata'].get('deletionTimestamp');assert any(p['port']==19090 for p in s['spec']['ports']);selector=s['spec']['selector'];assert selector
 pods=[p for p in get('pods',labels=','.join(k+'='+v for k,v in sorted(selector.items())))['items'] if not p['metadata'].get('deletionTimestamp') and p['status']['phase']=='Running' and any(c['type']=='Ready' and c['status']=='True' for c in p['status'].get('conditions',[]))];assert len(pods)==1,'selected ReadyPod must be unique'
 pod=pods[0];rsref=owner(pod,'ReplicaSet');rs=get('replicaset',rsref['name']);assert rs['metadata']['uid']==rsref['uid'];depref=owner(rs,'Deployment');dep=get('deployment',depref['name']);assert depref['uid']==depuid==dep['metadata']['uid'] and dep['status'].get('readyReplicas',0)>=1
 return {'label':label,'service':{'name':name,'namespace':'ani-system','uid':uid,'cluster_ip':ip,'port':19090,'selector':selector},'pod':{k:pod['metadata'][k] for k in ['name','namespace','uid']},'replicaset':{'name':rs['metadata']['name'],'uid':rsref['uid']},'deployment':{'name':depref['name'],'uid':depuid},'environment_key':key,'loopback_port':port}
def main():
 assert os.environ.get('SSH_CONNECTION','').split()[2:3]==['172.16.101.31'];assert not O.exists() and not P.exists(),'do not repeat unknown maintenance'
 lock=open('/home/chabking/workspace/ani-network-service-runs/net05a-heavy.lock','a')
 try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 except BlockingIOError:raise SystemExit(73)
 receipt={'state':'STARTED','started_at':dt.datetime.now(dt.timezone.utc).isoformat(),'source_sha256':sha(__file__),'resource_restarts':0,'sg_restarts':0,'vm_mutations':0,'shared_changes':0,'new_workload_creates':0,'gpu_allocations':0,'model_imports':0,'quota_writes':0,'normal_directory_gets':[],'port_forwards':[],'rollback':{}}
 def record():receipt['phase']=PHASE;save(O,receipt)
 record();M=None;A=None;child=None;stopped=False;forwards=[];pin=None;env=None;oldptr=None
 try:
  phase('existing-helper-current-process');assert sha(R/'v24-live-prep-01/b-candidates-maintain.py')=='259a90600141528097611138618ed49962d4e154c4094ab0b9846244d9507c9f';assert sha(D/'apply-owner-clients-attempt03.py')=='4a0a7608f2adcffc222c42f829a79db5432af2dc53295fdf4a73e28711417c8d';M=runpy.run_path(str(R/'v24-live-prep-01/b-candidates-maintain.py'));A=runpy.run_path(str(D/'apply-owner-clients-attempt03.py'));g=M['gov'];pointer=R/'private/gov-candidate/process.json';oldptr=json.loads(pointer.read_text());assert oldptr['pid']==3424470 and int(oldptr['start_ticks'])==70793927;pin,env=M['identity'](3424470,70793927,'ce21216e60b78eae0eb56a27e76082e9438c8c1f961b96f66e4b7439d027752c');assert env==json.loads((Q/'gov-runtime-env.json').read_text()) and env['GOMAXPROCS']=='2' and env['GOMEMLIMIT']=='512MiB';assert {39180,39187}.issubset(M['ports'](pin['pid']));receipt['old_process']=pin
  phase('pending-session-protected-guards');receipt['sessions_before']=A['no_sessions'](M);receipt['pending_before']=A['maintenance_pending'](M);before=A['protected'](M,g);receipt['protected_before']=A['compact'](before);resource=M['identity'](int((R/'private/resource-candidate/pid').read_text()))[0];sg=json.loads((R/'private/sg-candidate/process.json').read_text());config=Path(pin['argv'][pin['argv'].index('-c')+1]);conf={str(p.relative_to(config)):sha(p) for p in config.rglob('*') if p.is_file()};assert conf
  phase('service-ready-pod-owner-guards');targets=[service_target(t) for t in TARGETS];delta={t[5]:'127.0.0.1:'+str(t[6]) for t in TARGETS};assert all(env[t[5]]==t[3]+':19090' for t in TARGETS);newenv={**env,**delta};assert set(k for k in env if env[k]!=newenv[k])==set(delta);receipt['only_environment_delta']=delta;receipt['server_names_certificates_other_client_fields_unchanged']=True;receipt['selected_targets']=targets;record()
  for t in targets:assert not cmd(['ss','-H','-ltn','sport','=',':'+str(t['loopback_port'])],3).strip(),'loopback port busy'
  P.mkdir(mode=0o700);save(P/'original-process-environment.json',{'process':pin,'env':env,'pointer':oldptr,'config_guard':conf},True)
  phase('start-exact-task-loopback-forwards')
  for t in targets:
   log=P/(t['label'].lower()+'-port-forward.log');fd=os.open(log,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);argv=[K,'--kubeconfig='+KC,'-n','ani-system','port-forward','--address=127.0.0.1','pod/'+t['pod']['name'],str(t['loopback_port'])+':19090']
   with os.fdopen(fd,'ab') as f:p=subprocess.Popen(argv,stdin=subprocess.DEVNULL,stdout=f,stderr=subprocess.STDOUT,start_new_session=True)
   identity=M['identity'](p.pid)[0];row={**identity,'service':t['service'],'selected_pod':t['pod'],'replicaset':t['replicaset'],'deployment':t['deployment'],'log_reference':str(log),'listen':'127.0.0.1:'+str(t['loopback_port']),'target_port':19090};forwards.append((p,row));receipt['port_forwards'].append(row);record();deadline=time.monotonic()+8
   while time.monotonic()<deadline:
    assert p.poll() is None,'owned PF exited'
    if 'Forwarding from 127.0.0.1:'+str(t['loopback_port']) in log.read_text():break
    time.sleep(.1)
   else:raise RuntimeError('PF readiness deadline')
   assert M['ports'](p.pid)=={t['loopback_port']} and service_target(next(x for x in TARGETS if x[0]==t['label']))==t;row['ready']=True;record()
  save(P/'port-forwards.json',receipt['port_forwards'],True)
  def launch(suffix,desired):
   nonlocal child
   path=P/('gov-'+suffix+'.log');fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
   with os.fdopen(fd,'ab') as f:child=subprocess.Popen(pin['argv'],cwd=pin['cwd'],env=desired,stdin=subprocess.DEVNULL,stdout=f,stderr=subprocess.STDOUT,start_new_session=True)
   for _ in range(150):
    assert child.poll() is None,'owned Gov exited'
    current=M['identity'](child.pid,binary_sha=pin['binary_sha256'])[0]
    if {39180,39187}.issubset(M['ports'](child.pid)):return current
    time.sleep(.1)
   raise RuntimeError('Gov listener timeout')
  phase('fresh-before-normal-term');receipt['pending_pre_term']=A['maintenance_pending'](M);A['no_sessions'](M);assert M['identity'](pin['pid'],pin['start_ticks'],pin['binary_sha256'])[0]==pin;M['stop'](pin);stopped=True;receipt['old_normal_term']=True;record();phase('same-binary-loopback-gov-start');applied=launch('loopback',newenv);receipt['new_process']=applied;record()
  phase('post-start-protection');after=A['protected'](M,g);assert after==before;assert {str(p.relative_to(config)):sha(p) for p in config.rglob('*') if p.is_file()}==conf;assert M['identity'](applied['pid'],applied['start_ticks'],pin['binary_sha256'])[1]==newenv;assert M['identity'](resource['pid'],resource['start_ticks'],resource['binary_sha256'])[0]==resource;assert json.loads((R/'private/sg-candidate/process.json').read_text())==sg;receipt['pending_after']=A['maintenance_pending'](M);receipt['protected_after']=A['compact'](after);receipt['protected_unchanged']=True;receipt['same_binary_config_other_env_budget']=True
  for p,row in forwards:assert p.poll() is None and M['identity'](p.pid,row['start_ticks'])[0]=={k:row[k] for k in ['pid','start_ticks','argv','cwd','binary_sha256']}
  save(pointer,{**oldptr,'pid':applied['pid'],'start_ticks':applied['start_ticks'],'loopback_transport_receipt':str(O)});save(Q/'gov-runtime-env.json',newenv);procfile=Q/'processes.json';pv=json.loads(procfile.read_text());pv['governance']=applied;pv['loopback_transport_receipt']=str(O);save(procfile,pv);save(P/'active-private-cleanup-refs.json',{'gov':applied,'port_forwards':receipt['port_forwards'],'private_root':str(P),'stop_after_gov_release':True},True);receipt['new_private_cleanup_reference']=str(P);receipt['state']='TRANSPORT_CONFIG_APPLIED_PROTECTED';record()
  phase('normal-own-directory-get-once');q=runpy.run_path(str(R/'v24-live-prep-01/b-qualification.py'));token=q['tenant']();history=[]
  def read(route):
   value,event=A['normal_get'](g,token,route);receipt['normal_directory_gets'].append(event);save(P/'directory-private-responses.json',history+[{'route':route,'response':value}]);history.append({'route':route,'response':value});record();return value
  profiles=read('/api/v1/accelerator/profiles?cluster_id=5277649d-e28d-4a0a-ae76-8354365c63bc');models=read('/api/v1/models');receipt['profiles']=[{k:x.get(k) for k in ['id','profile_id','profile_version','cluster_id','clusterId','name','enabled','selectable','unavailable_reason','state','status','vendor','memory_mib','memory_block_mib','total_memory_mib'] if k in x} for x in A['records'](profiles,'items','profiles')];receipt['own_models']=[{k:x.get(k) for k in ['id','tenant_id','tenantId','name','status'] if k in x} for x in A['records'](models,'items','models')];receipt['own_versions']=[]
  for model in receipt['own_models'][:3]:
   mid=model['id'];assert isinstance(mid,str) and '/' not in mid;versions=read('/api/v1/models/'+urllib.parse.quote(mid,safe='')+'/versions')
   for version in A['records'](versions,'items','versions')[:3]:
    vid=version['id'];assert isinstance(vid,str) and '/' not in vid;actual=read('/api/v1/model-versions/'+urllib.parse.quote(vid,safe=''));actual=actual.get('version',actual) if isinstance(actual,dict) else None
    if actual:assert actual['id']==vid;receipt['own_versions'].append({k:actual.get(k) for k in ['id','model_id','modelId','tenant_id','tenantId','status'] if k in actual})
  receipt['state']='TRANSPORT_APPLIED_NORMAL_READS_RECORDED_NO_CREATE';record()
 except BaseException as e:
  location=traceback.extract_tb(e.__traceback__)[-1];receipt['failure']={'phase':PHASE,'file':Path(location.filename).name,'line':location.lineno,'error_type':type(e).__name__};receipt['rollback']['needed']=True
  if stopped:
   if child and child.poll() is None:M['stop'](M['identity'](child.pid,binary_sha=pin['binary_sha256'])[0])
   restored=launch('restored',env);save(pointer,{**oldptr,'pid':restored['pid'],'start_ticks':restored['start_ticks'],'loopback_failed_receipt':str(O)});save(Q/'gov-runtime-env.json',env);pv=json.loads((Q/'processes.json').read_text());pv['governance']=restored;save(Q/'processes.json',pv);receipt['rollback'].update(original_environment_and_config=True,restored_process=restored)
  for p,row in forwards:
   if p.poll() is None:M['stop']({k:row[k] for k in ['pid','start_ticks','argv','cwd','binary_sha256']})
   row['normal_term_gone']=p.poll() is not None
  receipt['state']='STOPPED_OR_ORIGINAL_CONFIGURATION_RESTORED';record();raise
 finally:
  receipt['finished_at']=dt.datetime.now(dt.timezone.utc).isoformat();record();fcntl.flock(lock,fcntl.LOCK_UN);print(json.dumps({'state':receipt['state'],'new_pid':receipt.get('new_process',{}).get('pid'),'failure':receipt.get('failure'),'receipt':str(O),'port_forward_pids':[x['pid'] for x in receipt['port_forwards']],'gets':receipt['normal_directory_gets']}),flush=True)
if __name__=='__main__':main()
