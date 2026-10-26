"""Root-approved TaskGov client projection and one directory-GET maintenance window.
No resource CREATE, permission/quota write, shared mutation or other service restart.
"""
from pathlib import Path
import argparse,base64,datetime as dt,fcntl,hashlib,json,os,re,runpy,signal,socket,ssl,subprocess,sys,time,urllib.parse,urllib.request
R=Path('/home/chabking/workspace/ani-resource-service-runs/vm-mvp-20261023-01a168ce');D=R/'v24-owner-live-prepare-01';P=R/'private/v24-owner-clients-01';Q=R/'private/v24-instance-01';PLAN='baecd36fb43b4ef0c7c6c27fa92d70c50dbd81c7914e4d80afdbfb3a8980b4a0';HELPER='259a90600141528097611138618ed49962d4e154c4094ab0b9846244d9507c9f';GOVSHA='ce21216e60b78eae0eb56a27e76082e9438c8c1f961b96f66e4b7439d027752c';GOV=3305121;TICKS=70294688

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(path,value,exclusive=False):
 path=Path(path);fd=os.open(path,os.O_WRONLY|os.O_CREAT|(os.O_EXCL if exclusive else os.O_TRUNC),0o600)
 with os.fdopen(fd,'w') as f:json.dump(value,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
def compact(v):return [{k:x.get(k) for k in ['id','version','vm_uid','vmi_uid','launcher_uid']} for x in v['vms']]
def protected(M,g):
 token=M['login']();ns='tenant-b3c946b5-7eb3-4feb-9f82-79712062ca5c';pins={'55099440-c47b-4f81-afc5-7d8e740d14dc':'7bca4671-89dd-4925-bad9-17e77401729d','068d56dd-d4fc-4ca8-8430-16c36bad0761':'03906ba4-36d4-4c11-b4c5-312c9c734274','fc154879-8a40-4b18-967c-215f5cf5992d':'9a751320-5f97-4e56-af6a-30964610321b'};rows=[]
 assert M['native']('namespace','kube-system')['metadata']['uid']=='5277649d-e28d-4a0a-ae76-8354365c63bc'
 for vmid,uid in pins.items():
  vm=M['native']('virtualmachine','vm-'+vmid,ns);vmi=M['native']('virtualmachineinstance','vm-'+vmid,ns);assert vm['metadata']['uid']==uid and vmi['status']['phase']=='Running' and any(o['uid']==uid and o.get('controller') for o in vmi['metadata']['ownerReferences']);pods=[p for p in M['native']('pods',namespace=ns,labels='kubevirt.io/created-by='+vmi['metadata']['uid'])['items'] if not p['metadata'].get('deletionTimestamp')];assert len(pods)==1 and pods[0]['status']['phase']=='Running'
  value=g['api']('GET','/api/v1/virtual-machines/'+vmid,token=token);assert value['id']==vmid and value['observedState']=='Running' and not value.get('closing');rows.append({**{k:value.get(k) for k in ['id','version','lastOperationId']},'vm_uid':uid,'vmi_uid':vmi['metadata']['uid'],'launcher_uid':pods[0]['metadata']['uid'],'vm_spec':vm['spec'],'vmi_spec':vmi['spec']})
 return {'vms':rows,'source_network':M['old']['source_network_facts'](json.loads((R/'private/resource-candidate/vm.json').read_text()))}
def endpoint_selection(M,delta):
 targets=[('ANI_ACCELERATOR_ADDR','ani-accelerator-service','db4d1538-554b-415a-a4f1-fdc490a86947','10.96.58.69'),('ANI_MODEL_ADDR','ani-model-grpc','2f75fd16-c191-4446-ba1a-8d57b189986c','10.96.44.130'),('ANI_INFERENCE_ADDR','ani-inference-grpc','6240c73a-b1a3-470e-8258-162dfc067e8c','10.96.166.234')];out=[]
 current=json.loads((R/'private/v24-instance-01/instance.json').read_text());assert current['ReleaseAddress']==delta['ANI_ACCELERATOR_ADDR'] and current['ReleaseServerName']==delta['ANI_ACCELERATOR_SERVER_NAME'],'keep the existing one formal ACC peer'
 probe="import socket,json,sys\ntry: print(json.dumps(sorted(set(v[4][0] for v in socket.getaddrinfo(sys.argv[1],19090,type=socket.SOCK_STREAM)))))\nexcept socket.gaierror: print('UNRESOLVED');sys.exit(2)"
 for key,name,uid,ip in targets:
  svc=M['native']('service',name,'ani-system');assert svc['metadata']['uid']==uid and svc['spec']['clusterIP']==ip and any(p['port']==19090 for p in svc['spec']['ports']);host,port=delta[key].rsplit(':',1);assert port=='19090';q=subprocess.run([sys.executable,'-c',probe,host],text=True,capture_output=True,timeout=5)
  if q.returncode==2 and q.stdout.strip()=='UNRESOLVED':delta[key]=ip+':19090';status='SAME_SERVICE_CLUSTER_IP_FOR_UNRESOLVABLE_DNS'
  else:assert q.returncode==0 and ip in json.loads(q.stdout),'ServiceDNS did not resolve to the guarded same Service';status='SERVICE_DNS_RETAINED'
  out.append({'environment_name':key,'service_namespace':'ani-system','service_name':name,'service_uid':uid,'service_port':19090,'selected_address':delta[key],'selection':status,'server_name_unchanged':True})
 return out
def leaf_claims(cert,now):
 assert cert.get('subjectAltName')==(('DNS','ani-governance'),),'unique exact Gov DNS SAN required'
 before=ssl.cert_time_to_seconds(cert['notBefore']);after=ssl.cert_time_to_seconds(cert['notAfter']);assert before<=now and after>now+600,'client certificate not valid for this window'
 return {'dns_san':['ani-governance'],'not_before':cert['notBefore'],'not_after':cert['notAfter'],'key_pair_loaded':True}
def leaf(path,key):
 context=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT);context.load_cert_chain(str(path),str(key));return leaf_claims(ssl._ssl._test_decode_cert(str(path)),time.time())
def source_guard(value,uid):
 assert value['metadata']['uid']==uid and not value['metadata'].get('deletionTimestamp'),'same-name Secret replacement/deletion'
 assert set(value['data'])=={'ca.crt','tls.crt','tls.key'},'only the exact three managed client fields'
def self_test():
 now=ssl.cert_time_to_seconds('Oct 24 10:00:00 2026 GMT');good={'subjectAltName':(('DNS','ani-governance'),),'notBefore':'Oct 24 09:00:00 2026 GMT','notAfter':'Oct 24 11:00:00 2026 GMT'};assert leaf_claims(good,now)['dns_san']==['ani-governance']
 for change in [{'subjectAltName':(('DNS','ani-resource-service'),)},{'subjectAltName':(('DNS','ani-governance'),('DNS','other'))},{'notAfter':'Oct 24 10:00:00 2026 GMT'},{'notBefore':'Oct 24 11:00:00 2026 GMT'}]:
  try:leaf_claims(dict(good,**change),now)
  except AssertionError:pass
  else:raise AssertionError('unsafe client certificate accepted')
 source={'metadata':{'uid':'owned'},'data':{'ca.crt':'fixture','tls.crt':'fixture','tls.key':'fixture'}};source_guard(source,'owned')
 for value in [dict(source,metadata={'uid':'other'}),dict(source,data={**source['data'],'password':'fixture'})]:
  try:source_guard(value,'owned')
  except AssertionError:pass
  else:raise AssertionError('replaced Secret or additional secret field accepted')
 print(json.dumps({'result':'PASS','pure_guards':8,'helper_imports':0,'api_calls':0,'secret_reads':0,'service_restarts':0}))
def no_sessions(M):
 s=json.loads((R/'private/sg-candidate/process.json').read_text());pin,_=M['identity'](s['pid'],s['start_ticks'],'7b58275ea629a0c3572d4224e5f07b7e05057a97d661a4d1062dd19a13612dfd');assert 39185 in M['ports'](pin['pid']);assert dt.datetime.fromisoformat(s['token_expires_at'].replace('Z','+00:00'))>dt.datetime.now(dt.timezone.utc)+dt.timedelta(minutes=5)
 with urllib.request.urlopen('http://127.0.0.1:39185/metrics',timeout=3) as f:metrics=f.read().decode()
 assert not any(float(x) for x in re.findall(r'^ani_session_active\{[^}]*\} ([0-9.eE+-]+)$',metrics,re.M)),'active SG stream'
 u=urllib.parse.urlsplit(json.loads((R/'private/gov-candidate/redis.json').read_text())['url_db1']);assert u.hostname=='127.0.0.1' and u.path=='/1'
 with socket.create_connection((u.hostname,u.port),timeout=3) as conn:
  f=conn.makefile('rb')
  def command(*args):
   parts=[str(x).encode() for x in args];conn.sendall(('*%d\r\n'%len(parts)).encode()+b''.join(('$%d\r\n'%len(x)).encode()+x+b'\r\n' for x in parts));v=f.readline();assert v[:1] in [b'+',b':'];return v[1:-2].decode()
  assert command('AUTH',urllib.parse.unquote(u.password))=='OK';assert command('SELECT',1)=='OK';assert command('ZCARD','{ani-session-gateway}:leases')=='0'
 return {'pid':pin['pid'],'start_ticks':pin['start_ticks'],'active':0,'leases':0,'unchanged':True}
def normal_get(g,token,route):
 try:
  value=g['api']('GET',route,token=token);raw=P/'directory-responses.json';history=json.loads(raw.read_text()) if raw.exists() else [];history.append({'route':route,'http':g['EVENTS'][-1]['status'],'response':value});save(raw,history);return value,{'route':route,'http':g['EVENTS'][-1]['status']}
 except Exception as e:
  reason=getattr(e,'reason','READ_UNAVAILABLE');reason=reason if re.fullmatch(r'[A-Za-z0-9_.:-]{1,120}',reason) else 'REASON_PRIVATE';return None,{'route':route,'http':getattr(e,'status',None),'reason':reason}
def records(v,*keys):
 if isinstance(v,list):return v
 if isinstance(v,dict):
  for key in keys:
   if isinstance(v.get(key),list):return v[key]
 return []
def directories(g,token):
 result={'requests':[],'gpu_presets':[],'gpu_profiles':[],'inference_presets':[],'models':[],'own_versions':[],'ready_model_version':None,'logical_cluster':'5277649d-e28d-4a0a-ae76-8354365c63bc'}
 fields=['id','name','code','compute_type','enabled','usage','usages','cpu_millis','memory_mib','gpu','gpu_selection','cluster_id','profile_id','profile_version','state','status']
 for key,route,keys in [('gpu_presets','/api/v1/compute-presets?usage=CONTAINER_INSTANCE&compute_type=GPU&page=1&pageSize=100',('items',)),('gpu_profiles','/api/v1/accelerator/profiles?cluster_id=5277649d-e28d-4a0a-ae76-8354365c63bc',('items','profiles')),('quota','/api/v1/me/quota-accounts',('items','accounts')),('inference_presets','/api/v1/compute-presets?usage=INFERENCE&page=1&pageSize=100',('items',)),('models','/api/v1/models',('items','models'))]:
  value,event=normal_get(g,token,route);result['requests'].append(event)
  if value is None:continue
  rows=records(value,*keys)
  if key=='quota':result[key]={'response_received':True,'accounts':[{k:x.get(k) for k in ['quota_code','quotaCode','unit','limit','occupied','available','over_limit']} for x in rows]}
  elif key=='models':result[key]=[{k:x.get(k) for k in ['id','tenant_id','name','status']} for x in rows]
  else:result[key]=[{k:x.get(k) for k in fields if k in x} for x in rows]
 # Only IDs actually returned under the same authenticated tenant are queried.
 for model in result['models'][:3]:
  mid=model.get('id');assert isinstance(mid,str) and '/' not in mid
  value,event=normal_get(g,token,'/api/v1/models/'+urllib.parse.quote(mid,safe='')+'/versions');result['requests'].append(event)
  for v in records(value,'items','versions')[:3]:
   vid=v.get('id');assert isinstance(vid,str) and '/' not in vid
   response,event=normal_get(g,token,'/api/v1/model-versions/'+urllib.parse.quote(vid,safe=''));result['requests'].append(event)
   actual=response.get('version',response) if isinstance(response,dict) else None
   if actual:
    assert actual.get('id')==vid;row={k:actual.get(k) for k in ['id','model_id','tenant_id','status']};result['own_versions'].append(row)
    if str(actual.get('status','')).lower()=='ready':result['ready_model_version']=row;break
  if result['ready_model_version']:break
 result['inference_input_status']='SAME_TENANT_READY_VERSION_RETURNED_NOT_CREATE_AUTHORIZATION' if result['ready_model_version'] else 'READY_VERSION_NOT_PROVEN_NO_CREATE'
 result['gpu_input_status']='ACTUAL_SAME527_CATALOG_REQUIRES_REVIEW_NO_CREATE' if result['gpu_presets'] and result['gpu_profiles'] else 'SAME527_GPU_PRESET_PROFILE_NOT_PROVEN_NO_CREATE';return result

def execute(args):
 assert os.environ.get('SSH_CONNECTION','').split()[2:3]==['172.16.101.31'];assert args.execute_authorized and args.plan_sha==PLAN and sha(D/'next-slice.json')==PLAN
 assert not (D/'applied.json').exists() and not P.exists(),'preserve prior/unknown maintenance result; no automatic repeat'
 helper=R/'v24-live-prep-01/b-candidates-maintain.py';assert sha(helper)==HELPER;M=runpy.run_path(str(helper));g=M['gov'];receipt={'state':'STARTED','plan_sha256':PLAN,'script_sha256':sha(__file__),'resource_restarts':0,'sg_restarts':0,'vm_mutations':0,'shared_writes':0,'application_mutations':0,'rollback':{},'new_private_cleanup_reference':str(P)}
 def record():save(D/'applied.json',receipt)
 checkpoint=json.loads(Path(args.cpu_cleanup_checkpoint).read_text());assert sha(args.cpu_cleanup_checkpoint)==args.cpu_cleanup_sha and checkpoint.get('cpu_original_delete_ledger_native_complete') is True,'CPU owner checkpoint incomplete'
 assert checkpoint.get('actual_owner_receipts'),'missing actual CPU owner cleanup receipts'
 for item in checkpoint['actual_owner_receipts']:assert sha(item['path'])==item['sha256'],'CPU cleanup evidence changed'
 receipt['cpu_checkpoint']={'reference':args.cpu_cleanup_checkpoint,'sha256':args.cpu_cleanup_sha}
 pin,env=M['identity'](GOV,TICKS,GOVSHA);assert pin==json.loads((R/'v24-live-prep-01/b-candidates-maintained-02.json').read_text())['governance_applied'];assert env['GOMAXPROCS']=='2' and env['GOMEMLIMIT']=='512MiB';assert {39180,39187}.issubset(M['ports'](GOV))
 proposal=json.loads((D/'next-slice.json').read_text());delta={**proposal['first_minimal_gpu_slice']['only_gov_environment_append'],**proposal['inference_additional_slice']['only_after_root_review_environment_append']};assert not any(env.get(k) for k in delta),'new client keys already active; do not overwrite'
 receipt['sg_guard']=no_sessions(M);receipt['pending_before']=M['pending']();before=protected(M,g);receipt['protected_before']=compact(before);resource_before,_=M['identity'](int((R/'private/resource-candidate/pid').read_text().strip()));sg_before=json.loads((R/'private/sg-candidate/process.json').read_text());receipt['endpoint_selection']=endpoint_selection(M,delta)
 config=Path(pin['argv'][pin['argv'].index('-c')+1]);config_bytes={str(p.relative_to(config)):sha(p) for p in config.rglob('*') if p.is_file()};assert config_bytes
 P.mkdir(mode=0o700);save(P/'original-process-and-env.json',{'pin':pin,'env':env,'config_bytes_private_guard':config_bytes},exclusive=True);pointer=R/'private/gov-candidate/process.json';old_pointer=json.loads(pointer.read_text());assert old_pointer['pid']==GOV;save(P/'original-process-pointer.json',old_pointer,exclusive=True);receipt['original_process']=pin;receipt['environment_delta_references']=delta;record()
 for source in proposal['proposed_private_projection']:
  v=M['native']('secret',source['source']['name'],source['source']['namespace']);source_guard(v,source['source']['uid'])
  dest=Path(source['destination_private_directory']);assert dest.parent==P;dest.mkdir(mode=0o700)
  for name in ['ca.crt','tls.crt','tls.key']:
   fd=os.open(dest/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
   with os.fdopen(fd,'wb') as f:f.write(base64.b64decode(v['data'][name],validate=True));f.flush();os.fsync(f.fileno())
  receipt.setdefault('client_certificates',[]).append({'source_namespace':source['source']['namespace'],'source_name':source['source']['name'],'source_uid':v['metadata']['uid'],'source_resource_version':v['metadata']['resourceVersion'],'private_directory':str(dest),**leaf(dest/'tls.crt',dest/'tls.key')})
 applied=None;child=None;stopped=False
 def launch(suffix,newenv):
  nonlocal child
  log=P/('gov-'+suffix+'.log');fd=os.open(log,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
  with os.fdopen(fd,'ab') as f:child=subprocess.Popen(pin['argv'],cwd=pin['cwd'],env=newenv,stdin=subprocess.DEVNULL,stdout=f,stderr=subprocess.STDOUT,start_new_session=True)
  for _ in range(150):
   assert child.poll() is None,'owned new Gov exited; raw log stays private'
   current,_=M['identity'](child.pid,binary_sha=pin['binary_sha256'])
   if {39180,39187}.issubset(M['ports'](child.pid)):return current
   time.sleep(.1)
  raise RuntimeError('new Gov listeners not ready')
 try:
  assert M['identity'](GOV,TICKS,pin['binary_sha256'])[0]==pin;M['stop'](pin);stopped=True;receipt['normal_term_old_gov']=True;record();applied=launch('clients',dict(env,**delta));receipt['new_process']=applied;record()
  token=M['login']();after=protected(M,g);assert before==after;assert {str(p.relative_to(config)):sha(p) for p in config.rglob('*') if p.is_file()}==config_bytes;assert M['identity'](applied['pid'],applied['start_ticks'],GOVSHA)[1]==dict(env,**delta);assert M['identity'](resource_before['pid'],resource_before['start_ticks'],resource_before['binary_sha256'])[0]==resource_before;assert json.loads((R/'private/sg-candidate/process.json').read_text())==sg_before;receipt['pending_after']=M['pending']();receipt['protected_after']=compact(after);receipt['protected_unchanged']=True;receipt['original_config_and_other_environment_unchanged']=True
  save(pointer,{**old_pointer,'pid':applied['pid'],'start_ticks':applied['start_ticks'],'owner_clients_receipt':str(D/'applied.json')});save(Q/'gov-runtime-env.json',dict(env,**delta));procfile=Q/'processes.json'
  if procfile.exists():p=json.loads(procfile.read_text());p['governance']=applied;p['owner_clients_receipt']=str(D/'applied.json');save(procfile,p)
  receipt['state']='CONFIG_APPLIED_VERIFIED';record();receipt['normal_directory_gets']=directories(g,token);receipt['state']='CONFIG_APPLIED_DIRECTORY_BLOCKED' if any(not e.get('http') or not 200<=e['http']<300 for e in receipt['normal_directory_gets']['requests']) else 'CONFIG_APPLIED_AND_NORMAL_GETS_RECORDED';record()
 except BaseException:
  receipt['rollback']['needed']=True
  if child and child.poll() is None:
   current,_=M['identity'](child.pid,binary_sha=pin['binary_sha256']);M['stop'](current)
  if stopped:
   restored=launch('restored',env);save(pointer,{**old_pointer,'pid':restored['pid'],'start_ticks':restored['start_ticks'],'owner_clients_rollback_receipt':str(D/'applied.json')});save(Q/'gov-runtime-env.json',env);receipt['rollback'].update(restored_process=restored,original_env_and_config=True)
   procfile=Q/'processes.json'
   if procfile.exists():p=json.loads(procfile.read_text());p['governance']=restored;save(procfile,p)
  receipt['state']='FAILED_EXACT_GOV_ROLLBACK_OR_NORMAL_TERM_PENDING';record();raise
 receipt['finished_at']=dt.datetime.now(dt.timezone.utc).isoformat();record();print(json.dumps({'state':receipt['state'],'new_pid':applied['pid'],'receipt':str(D/'applied.json'),'directory_requests':len(receipt['normal_directory_gets']['requests']),'private_cleanup_reference':str(P)}))
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['self-test','execute']);p.add_argument('--execute-authorized',action='store_true');p.add_argument('--plan-sha');p.add_argument('--cpu-cleanup-checkpoint');p.add_argument('--cpu-cleanup-sha');a=p.parse_args()
 if a.action=='self-test':return self_test()
 lock=open('/home/chabking/workspace/ani-network-service-runs/net05a-heavy.lock','a')
 try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 except BlockingIOError:raise SystemExit(73)
 try:execute(a)
 except Exception as e:
  out=D/'applied.json'
  if out.exists():v=json.loads(out.read_text());v['failure_type']=type(e).__name__;v['stopped_without_automatic_retry']=True;save(out,v)
  print(json.dumps({'result':'STOPPED_NO_AUTOMATIC_RETRY','error_type':type(e).__name__,'private_reference':str(P)}));raise SystemExit(1)
 finally:fcntl.flock(lock,fcntl.LOCK_UN)
if __name__=='__main__':main()
