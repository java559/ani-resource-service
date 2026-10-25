"""Single explicit member/phase of V10 positive pair; Root GO required per call.

Run on Fedora only: --execute-authorized --member a|b --phase create|observe|
native|freeze-root-csi|delete|cleanup --plan-sha256 SHA --runner-sha256 SHA.
Optional --label provides a new bounded read label; never resends CREATE/DELETE.
Root sequence: a create/observe/native Running, then b create/observe/native.
Only Root-reviewed original helper functions handle original operation/capture,
exact root-CSI freeze and normal delete/GC. No Guest/Windows writes, placement
API, new PG/Pod/key/preset, runtime/source edits or shared service changes.
Native v3 alone accepts b replacement after its proven successful placement
Restart; all base identities stay frozen and both retired UIDs must be gone.
"""
from pathlib import Path
import argparse,datetime,fcntl,hashlib,http.cookiejar,ipaddress,json,os,re,runpy,stat,subprocess,time,types,urllib.error,urllib.request,uuid
R=Path('/home/chabking/workspace/ani-resource-service-runs/vm-mvp-20261023-01a168ce');D=R/'v10-network-positive-02'
sha=lambda raw:hashlib.sha256(raw).hexdigest()
digest=lambda obj:sha(json.dumps(obj,sort_keys=True,separators=(',',':')).encode())
def require(ok,msg):
 if not ok:raise RuntimeError(msg)
def save(path,value):
 path.parent.mkdir(parents=True,exist_ok=True,mode=0o700);fd=os.open(path,os.O_CREAT|os.O_WRONLY|os.O_TRUNC|os.O_NOFOLLOW,0o600)
 with os.fdopen(fd,'w') as f:json.dump(value,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
def private_json(path):
 p=Path(path);st=p.lstat();require(stat.S_ISREG(st.st_mode) and not p.is_symlink() and st.st_uid==os.getuid() and stat.S_IMODE(st.st_mode)==0o600,'existing own regular0600 private reference required');return json.loads(p.read_text())
def main(a):
 require(a.execute_authorized and os.uname().nodename=='fedora' and sha(Path(__file__).read_bytes())==a.runner_sha256 and sha((D/'prepared.json').read_bytes())==a.plan_sha256,'Root GO and exact Fedora runner/plan required');plan=json.loads((D/'prepared.json').read_text());require(sha((D/'inventory.json').read_bytes())==plan['inventory_sha256'],'fixed original readonly inventory changed');inv=json.loads((D/'inventory.json').read_text());require(inv['result']=='PASS_READONLY_V10_INPUTS_FROZEN_NO_CREATE' and inv['session_revoked'] is True,'original task qualification incomplete');require(re.fullmatch('[a-z0-9-]{1,32}',a.label),'bounded unique phase label required')
 member=plan['members'][a.member];name=member['name'];statepath=D/'private'/a.member/'state.json';out=D/(a.member+'-'+a.phase+'-'+a.label+'.json');private=D/'private'/(a.member+'-'+a.phase+'-'+a.label);require(not out.exists() and not private.exists(),'prior phase outcome exists; retain it and choose explicit fresh observation label only');lock=open('/home/chabking/workspace/ani-network-service-runs/net05a-heavy.lock','r');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);private.mkdir(parents=True,mode=0o700);record={'result':'STARTED','phase':a.phase,'member':a.member,'task_name':plan['task_name'],'started_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'runner_sha256':a.runner_sha256,'plan_sha256':a.plan_sha256,'calls':[],'helper_events':[],'business_send_attempts':0,'guest_actions':0,'placement_mutations':0,'service_changes':0,'new_PG_or_fixture':False,'credentials_or_hashes_public':False};token=None;jar=http.cookiejar.CookieJar();opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(jar));state={};captured={}
 def flush():save(out,record)
 def api(method,path,body=None,token_arg=None,authenticated=True,**kwargs):
  nonlocal token
  passed=kwargs.pop('token',token_arg);require(not kwargs and path.startswith('/api/v1/'),'normal Gov route only');require(not passed or passed==token,'own phase session only')
  authpath=path in ['/api/v1/auth/password/login','/api/v1/auth/logout'];allowed=method=='GET' or authpath or (a.phase=='create' and method=='POST' and path=='/api/v1/virtual-machines') or (a.phase=='delete' and method=='DELETE' and state.get('vm_id') and path=='/api/v1/virtual-machines/'+state['vm_id']);require(allowed,'outside Root-released one-member phase')
  if method!='GET' and not authpath:record['business_send_attempts']+=1;require(record['business_send_attempts']==1,'at most one normal lifecycle send per phase')
  row={'n':len(record['calls'])+1,'method':method,'path':path,'outcome':'ATTEMPTING'};record['calls'].append(row);flush();headers={'Content-Type':'application/json','Accept':'application/json'}
  if authenticated:require(token,'own normal session required');headers['Authorization']='Bearer '+token
  rawbody=json.dumps(body).encode() if body is not None else None
  if rawbody is not None:save(private/(str(row['n'])+'.request.json'),body)
  request=urllib.request.Request('http://127.0.0.1:39180'+path,data=rawbody,headers=headers,method=method)
  try:
   try:response=opener.open(request,timeout=20)
   except urllib.error.HTTPError as e:response=e
   with response:raw=response.read(4<<20);status=response.status
   p=private/(str(row['n'])+'.response.json');p.write_bytes(raw);os.chmod(p,0o600);row.update(outcome='RESPONDED',http_status=status);flush();require(status in [200,201,202,204],'normal Gov request failed; preserve original intent/outcome');return json.loads(raw) if raw.strip() else {}
  except Exception:row['outcome']='UNKNOWN_OR_FAILED_RETAIN_NO_RESEND';flush();raise
 def kube(kind,name=None,namespace=None,labels=None):
  args=t.K+['get',kind]+([name] if isinstance(name,str) else (name or []))+(['-n',namespace] if namespace else [])+(['-l',labels] if labels else [])+['--ignore-not-found','-o','json'];p=subprocess.run(args,capture_output=True,timeout=15);require(p.returncode==0,'bounded exact native read unavailable');return json.loads(p.stdout) if p.stdout.strip() else None
 def protect(unused):
  n=plan['network'];w=inv['windows'];require(kube('namespace','kube-system')['metadata']['uid']==plan['cluster_uid'] and kube('namespace',n['Namespace'])['metadata']['uid']==n['NamespaceUID'],'original cluster/namespace differs');vm=kube('virtualmachine','vm-'+w['vm_id'],n['Namespace']);vmi=kube('virtualmachineinstance','vm-'+w['vm_id'],n['Namespace']);require(vm['metadata']['uid']==w['vm_uid'] and digest(vm['spec'])==w['vm_spec_sha256'] and vmi['metadata']['uid']==w['vmi_uid'] and vmi['status']['phase']=='Running','original Windows18 native identity differs');pvcs=kube('pvc',[p['name'] for p in w['pvc_refs']],n['Namespace'])['items'];require({p['metadata']['name']:p['metadata']['uid'] for p in pvcs}=={p['name']:p['uid'] for p in w['pvc_refs']} and all(not p['metadata'].get('deletionTimestamp') for p in pvcs),'Windows5PVC identities differ/deleting');nad=kube('network-attachment-definitions.k8s.cni.cncf.io',n['NADName'],n['Namespace']);require(nad['metadata']['uid']==n['NADUID'] and digest(json.loads(nad['spec']['config']))==n['NADConfigDigest'],'original task NAD changed');source=plan['source'];pvc=kube('pvc',source['pvc_name'],source['namespace']);require(pvc['metadata']['uid']==source['pvc_uid'] and pvc['status']['phase']=='Bound' and pvc['metadata']['annotations']['vm.ani.io/source-sha256']==source['checksum'],'original Ubuntu source changed');return {'windows_vm_uid':w['vm_uid'],'windows_vm_spec_sha256':w['vm_spec_sha256'],'windows_vmi_uid':w['vmi_uid'],'windows_pvc_uids':[p['uid'] for p in w['pvc_refs']],'source_uid':source['pvc_uid'],'source_content_sha256':source['checksum'],'nad_uid':n['NADUID'],'nad_config_sha256':n['NADConfigDigest'],'namespace_uid':n['NamespaceUID']}
 def projected_current(value):return {k:v for k,v in value.items() if k in ['id','name','version','desiredState','observedState','reason','lastOperationId','closing']}
 def publish(g,s,phase):
  event={'phase':phase,'vm_id':s.get('vm_id'),'operation_id':s.get('operation_id'),'current':projected_current(s.get('current',{}))}
  if 'delete' in s:event['delete']={k:v for k,v in s['delete'].items() if k in ['key','send_attempts','operation_id','outcome']};event['delete_operation']={k:v for k,v in s['delete'].get('operation',{}).items() if k in ['id','vmId','state','reason']}
  if 'cleanup' in s:event['cleanup']={k:v for k,v in s['cleanup'].items() if k in ['result','remaining_original_objects','rbd_family','normal_gov_delete_only','manual_backend_delete']}
  record['helper_events'].append(event);flush();return event
 def write_pair():
  loaded={}
  for k in ['a','b']:
   p=D/'private'/k/'state.json'
   if not p.exists():return
   s=private_json(p);n=s.get('native',{})
   if n.get('phase')!='Running' or not all(n.get(x) for x in ['vm','vmi','launcher','pvc','pv','pv_csi']) or not s.get('native_freeze'):return
   if k=='b' and not s.get('placement_restart'):return
   loaded[k]=(s,n)
  states={}
  for k,(s,n) in loaded.items():
   c=n['current'];require(c['id']==s['vm_id'] and c['observedState']=='Running' and not c['closing'],'fresh native current identity differs');attachment=n.get('attachment') or [];require(len(attachment)==1 and attachment[0]['state']=='attached' and attachment[0]['blocked'] is False,'fresh exact member Attachment not yet attached');f=s['native_freeze'];roots=[x for x in c['disks'] if x['role']=='system'];require(len(roots)==1,'one root serial required');ips={x['address'] for x in c.get('addresses',[]) if ipaddress.ip_address(x['address']) in ipaddress.ip_network('10.83.1.0/24')};primary=[x for x in n['interfaces'] if x.get('name')=='default'];require(len(primary)==1 and primary[0].get('linkState')=='up','one live default VMI interface required');vmi_ips={p for x in primary for p in [x.get('ipAddress'),*x.get('ipAddresses',[])] if p};require(len(ips)==1 and next(iter(ips))==primary[0].get('ipAddress') and next(iter(ips)) in vmi_ips,'same-subnet measured fresh API/default VMI IP required');ip=next(iter(ips));node=n['node'];require(f['launcher_node']==node and f['bios'] is True,'launcher node/BIOS mismatch');states[k]={'vm_id':s['vm_id'],'vm':{x:n['vm'][x] for x in ['name','namespace','uid']},'vmi':{**{x:n['vmi'][x] for x in ['name','namespace','uid']},'node':node,'ip':ip},'launcher':{**{x:n['launcher'][x] for x in ['name','namespace','uid']},'node':node},'root_disk':{'serial':roots[0]['serial'],'pvc':{x:n['pvc'][x] for x in ['name','uid']},'pv':{'name':n['pv']['name'],'uid':n['pv']['uid'],'csi':n['pv_csi']}},'firmware':'bios','native_vm_uid':n['vm']['uid'],'vmi_uid':n['vmi']['uid'],'namespace':plan['namespace']['name'],'namespace_uid':plan['namespace']['uid'],'node':node,'current_ip':ip,'attachment':attachment,'api_address_projection':{'addresses':c.get('addresses',[]),'result':'MATCHED_FRESH_API_AND_PRIMARY_VMI'}}
  require(states['b']['node']=='ani-01' and loaded['b'][0]['placement_restart']['result']=='ACCEPTED_LEGITIMATE_RESTART_GENERATION','placed b generation not confirmed');same=states['a']['node']==states['b']['node'];result={'task_name':plan['task_name'],'cluster_uid':plan['cluster_uid'],'resource_tenant_id':plan['resource_tenant_id'],'namespace':plan['namespace'],'network':{'vpc_id':plan['network']['VPCID'],'subnet_id':plan['network']['SubnetID'],'nad':{'name':plan['network']['NADName'],'uid':plan['network']['NADUID'],'config_sha256':plan['network']['NADConfigDigest']},'firmware':'bios'},'vms':states,'cross_node':not same,'result':'NOT_RUN_SAME_NODE_NORMAL_CLEANUP_REQUIRED' if same else 'PASS_TWO_RUNNING_NATIVE_IDENTITIES_CROSS_NODE_HTTP_NOT_RUN'};p=D/'native-inventory-placed.json';raw=(json.dumps(result,sort_keys=True,indent=2)+'\n').encode()
  if p.exists():require(p.read_bytes()==raw,'frozen pair native inventory changed; no overwrite')
  else:
   with p.open('xb') as f:f.write(raw)
  record['pair_native_inventory']={'path':str(p),'sha256':sha(raw),'cross_node':not same};flush()
 def capture_native():
  nonlocal state
  require(sha((D/'placement-prepared.json').read_bytes())=='5a587c50e2c46f707d9d3bc1988d4cb030bef7012fd6c454735c0ccbb9ebd5da' and sha((D/'native-inventory.json').read_bytes())=='53de73d6884bfe562d9f67cbee6277e97d3fad7dac49a72b6af0d02be44b0db1','fixed placement plan/original same-node inventory changed')
  if a.member!='b':return m['capture'](g,t,state,token)
  placed=json.loads((D/'placement-prepared.json').read_text());proof=private_json(D/'private/placement/state.json');old=json.loads((D/'native-inventory.json').read_text())['vms']['b'];require(state['vm_id']==placed['vm_id']==old['vm_id'] and proof['vm_id']==state['vm_id'] and proof.get('restart_send_attempts')==1 and proof.get('restart_outcome')=='ACK_PERSISTED' and proof.get('operation_id')=='6215e488-0493-49c0-9ccc-98099ef35c8f','exact original placement Restart ACK required')
  op=api('GET','/api/v1/virtual-machines/'+state['vm_id']+'/operations/'+proof['operation_id']);require(op['id']==proof['operation_id'] and op['vmId']==state['vm_id'] and op['action']=='restart' and op['state']=='succeeded' and str(op['intentVersion'])=='2','only original successful version2 Restart may replace frozen generation');record['placement_restart_operation']={k:op.get(k) for k in ['id','vmId','action','intentVersion','state','reason']};flush()
  vm=kube('virtualmachine',old['vm']['name'],t.NS);original=private_json(D/'private/placement/original-vm.json');spec=json.loads(json.dumps(vm['spec']));selector=spec['template']['spec'].pop('nodeSelector',None);require(vm['metadata']['uid']==old['vm']['uid']==placed['native_vm_uid'] and selector==placed['node_selector'] and spec==original['spec'] and not vm['metadata'].get('deletionTimestamp'),'original b VM/base spec differs from exact selector delta')
  vmi=kube('virtualmachineinstance',old['vmi']['name'],t.NS);require(vmi and vmi['metadata']['uid']=='f948fcac-9f17-4748-8cec-be490db54eca' and vmi['metadata']['uid']!=old['vmi']['uid'] and not vmi['metadata'].get('deletionTimestamp') and vmi['status']['phase']=='Running' and vmi['status']['nodeName']=='ani-01' and any(o['kind']=='VirtualMachine' and o['uid']==old['vm']['uid'] for o in vmi['metadata'].get('ownerReferences',[])),'replacement VMI not Running on ani01 under original VM')
  retired=kube('pod',old['launcher']['name'],t.NS);require(not retired or retired['metadata']['uid']!=old['launcher']['uid'],'old launcher UID still exists; wait original Restart, never clear freeze')
  pods=kube('pod',namespace=t.NS,labels='kubevirt.io/created-by='+vmi['metadata']['uid'])['items'];pods=[p for p in pods if not p['metadata'].get('deletionTimestamp') and any(o['kind']=='VirtualMachineInstance' and o['uid']==vmi['metadata']['uid'] for o in p['metadata'].get('ownerReferences',[]))];require(len(pods)==1 and pods[0]['metadata']['uid']!=old['launcher']['uid'] and pods[0]['status']['phase']=='Running' and pods[0]['spec']['nodeName']=='ani-01','one new original-owner Running ani01 launcher required');pod=pods[0]
  prior=state.get('placement_restart')
  if prior:
   require(prior['operation_id']==op['id'] and state['native']['vmi']['uid']==vmi['metadata']['uid'] and state['native']['launcher']['uid']==pod['metadata']['uid'],'only already-accepted replacement generation may be refreshed');return m['capture'](g,t,state,token)
  require(state['native']['vmi']['uid']==old['vmi']['uid'] and state['native']['launcher']['uid']==old['launcher']['uid'],'original generation must match frozen same-node inventory');history=D/'private/b/state-before-placement-restart.json'
  if history.exists():require(private_json(history)==state,'retained original state differs; do not overwrite history')
  else:
   history.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
   fd=os.open(history,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
   with os.fdopen(fd,'w') as f:json.dump(state,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
  candidate=json.loads(json.dumps(state));base={key:candidate['native'].get(key) for key in ['vm','dv','pvc','pv','pv_csi','nocloud_secret']};candidate['native'].pop('vmi');candidate['native'].pop('launcher');candidate_path=private/'restart-generation-candidate.private.json';env['STATE']=candidate_path
  try:n=m['capture'](g,t,candidate,token)
  finally:env['STATE']=statepath
  require(n.get('phase')=='Running' and n['vmi']['uid']==vmi['metadata']['uid'] and n['launcher']['uid']==pod['metadata']['uid'] and all(n.get(key)==value and value for key,value in base.items()),'rootVM/DV/PVC/PV/CSI/NoCloud Secret must remain exact');require(str(n['current']['version'])=='2' and n['current']['lastOperationId']==op['id'] and not n['current']['closing'],'fresh product must retain exact Restart version2 intent')
  candidate['placement_restart']={'result':'ACCEPTED_LEGITIMATE_RESTART_GENERATION','operation_id':op['id'],'intent_version':'2','old_vmi_uid':old['vmi']['uid'],'old_launcher_uid':old['launcher']['uid'],'old_vmi_uid_gone':True,'old_launcher_uid_gone':True,'new_vmi_uid':n['vmi']['uid'],'new_launcher_uid':n['launcher']['uid'],'preserved_base_identity_fields':list(base),'prior_private_state':str(history)};state=candidate;record['accepted_restart_generation']={k:v for k,v in candidate['placement_restart'].items() if k!='prior_private_state'};flush();return n

 try:
  flush()
  for filename,h in plan['original_helpers'].items():require(sha((R/filename).read_bytes())==h,'approved original helper changed')
  m=runpy.run_path(str(R/'vm-product-cloudinit-ssh.py'));t=types.SimpleNamespace(**runpy.run_path(str(R/'vm-product-second-tenant-vm.py')));t.TENANT=plan['resource_tenant_id'];t.NS=plan['namespace']['name'];t.NS_UID=plan['namespace']['uid'];t.PRESET=plan['preset_id'];t.CLUSTER=plan['cluster_uid'];original_g=runpy.run_path(str(R/'gov-candidate.py'));g=types.SimpleNamespace(**original_g);g.api=api;g.save=save
  # Reuse the original identity/proc helpers, without their old login or large protect.
  h=R/'v24-live-prep-01/b-candidates-maintain.py';require(sha(h.read_bytes())=='259a90600141528097611138618ed49962d4e154c4094ab0b9846244d9507c9f','task process helper changed');pid=runpy.run_path(str(h));gp,ge=pid['identity'](35161,74249534,'ce21216e60b78eae0eb56a27e76082e9438c8c1f961b96f66e4b7439d027752c');rp,resource_env=pid['identity'](3881653,72749000,'a83260360bee7f5453f7266c4ce52f07e971548e2e71bb95fd89fdc43d091aeb');require({39180,39187}<=pid['ports'](35161) and {39181,39183,39184}<=pid['ports'](3881653),'original task listeners changed');require(gp['argv']==inv['task_processes']['governance']['argv'] and rp['argv']==inv['task_processes']['resource']['argv'],'original process argv differs');record['protected_before']=protect(t)
  if statepath.exists():state=private_json(statepath)
  elif a.phase=='create':
   cred=private_json(plan['guest_credential_reference']);require(cred['username']=='aniuser','original guest username differs');keypath=Path(plan['ssh_public_key_reference']);key=keypath.read_text().strip();require(key.startswith('ssh-ed25519 ') and '\n' not in key and m['fingerprint'](keypath)==plan['ssh_public_key_fingerprint'],'original Fedora public identity differs');state={'key':member['idempotency_key'],'body':{'data':{**member['body_without_guest_credentials'],'guestInitialization':{**cred,'sshPublicKey':key}}},'send_attempts':0,'protected_before':record['protected_before']};save(statepath,state)
  else:raise RuntimeError('no existing exact original member state')
  env=m['capture'].__globals__;original_kube=env['kubectl']
  def tracked_kube(*args,**kwargs):
   obj=original_kube(*args,**kwargs)
   if obj and obj.get('kind')=='VirtualMachine' and obj['metadata']['name']=='vm-'+str(state.get('vm_id')):captured['vm']=obj
   if obj and obj.get('kind') in ['List','PodList']:
    for x in obj.get('items',[]):
     if x.get('kind')=='Pod':captured.setdefault('pods',[]).append(x)
   return obj
  env.update(STATE=statepath,NAME=name,protect=protect,publish=publish,kubectl=tracked_kube)
  creds=g.credentials();require(creds['tenant_code']==inv['gov_tenant_code'],'original tenant login reference scope changed');auth=api('POST','/api/v1/auth/password/login',{'tenant_name':creds['tenant_code'],'username':creds['tenant_username'],'password':creds['tenant_password']},authenticated=False);token=auth['access_token']
  if a.phase=='create':
   require(state['send_attempts']==0 and not state.get('vm_id'),'CREATE already sent/unknown; original operation observation only');checked=t.catalog(g,token);require(checked['media']['initializationVerified'] is True,'verified NoCloud catalog required');require(not any(v['name']==name for v in t.inventory(g,token)),'member name already exists; do not adopt');state['send_attempts']=1;state['create_outcome']='ACK_UNKNOWN_UNTIL_PERSISTED';save(statepath,state);ack=api('POST',m['BASE'],state['body'],token);require(str(uuid.UUID(ack['resourceId']))==ack['resourceId'] and str(uuid.UUID(ack['operationId']))==ack['operationId'],'normal CREATE ACK IDs unavailable');state.update(vm_id=ack['resourceId'],operation_id=ack['operationId'],create_outcome='ACK_PERSISTED');save(statepath,state);publish(g,state,'CREATE_ACK')
  elif a.phase=='observe':m['observe'](g,state,token,20)
  elif a.phase=='native':
   n=capture_native()
   if n.get('phase')=='Running' and n.get('launcher'):
    raw=captured['vm'];firmware=raw['spec']['template']['spec']['domain']['firmware'];networks=raw['spec']['template']['spec']['networks'];require(firmware['uuid']==state['vm_id'] and 'efi' not in firmware.get('bootloader',{}) and len(networks)==1 and networks[0].get('multus',{}).get('networkName') in [plan['network']['NADName'],t.NS+'/'+plan['network']['NADName']],'same BIOS/NAD native contract differs');pods=[p for p in captured.get('pods',[]) if p['metadata']['uid']==n['launcher']['uid']];require(len(pods)==1,'captured original launcher missing');state['native_freeze']={'bios':True,'launcher_node':pods[0]['spec']['nodeName']};save(statepath,state);write_pair()
  elif a.phase=='freeze-root-csi':m['freeze_backend'](g,t,state)
  elif a.phase=='delete':m['delete'](g,t,state,token,20)
  elif a.phase=='cleanup':m['cleanup'](g,t,state,token)
  require(protect(t)==record['protected_before'],'original Windows/source/network changed');record['result']='PASS_EXPLICIT_MEMBER_PHASE_ORIGINAL_OUTCOME_RECORDED';record['vm_id']=state.get('vm_id');record['operation_id']=state.get('operation_id')
 except Exception as e:record.update(result='STOPPED_RETAIN_ORIGINAL_MEMBER_STATE_NO_RESEND',error_type=type(e).__name__,reason=str(e) if isinstance(e,RuntimeError) else 'exact phase failed; private state retained')
 finally:
  if token or any(True for _ in jar):
   try:reply=api('POST','/api/v1/auth/logout',{'application':'console'},authenticated=False);require(reply.get('session_revoked',reply.get('sessionRevoked')) is True,'normal own session revoke unconfirmed');record['session_revoked']=True;require(record['calls'][0]['path']=='/api/v1/auth/password/login','own login log ordering differs');(private/'1.request.json').unlink();(private/'1.response.json').unlink()
   except Exception:record.update(result='STOPPED_AUTH_SESSION_REVOCATION_UNCONFIRMED',session_revoked=False)
  jar.clear();token=None;record['finished_at']=datetime.datetime.now(datetime.timezone.utc).isoformat();record['heavy_lock_released']=True;flush();lock.close()
 print(json.dumps({'result':record['result'],'receipt':str(out),'receipt_sha256':sha(out.read_bytes()),'heavy_lock_released':True}),flush=True);return 0 if record['result'].startswith('PASS_') else 1
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--execute-authorized',action='store_true');p.add_argument('--phase',choices=['create','observe','native','freeze-root-csi','delete','cleanup'],required=True);p.add_argument('--member',choices=['a','b'],required=True);p.add_argument('--label',default='01');p.add_argument('--plan-sha256',required=True);p.add_argument('--runner-sha256',required=True);raise SystemExit(main(p.parse_args()))
