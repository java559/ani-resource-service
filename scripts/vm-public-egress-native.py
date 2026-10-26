#!/usr/bin/env python3
"""Prepare and execute only the two reviewed tenant1 public objects."""
import argparse,datetime,fcntl,hashlib,ipaddress,json,os,pathlib,runpy,subprocess,time,uuid
RUN=pathlib.Path('/home/chabking/workspace/ani-resource-service-runs/vm-mvp-20261023-01a168ce')
PLAN=RUN/'private/public-egress/native-plan.json'
PUBLIC=RUN/'evidence/vm-public-egress-native-prepared.json'
TASK='01a168ce'
TENANT='b3c946b5-7eb3-4feb-9f82-79712062ca5c'
NS='tenant-'+TENANT
NAMES={'eip':'vm-mvp-01a168ce-public-eip','snat':'vm-mvp-01a168ce-public-snat'}
def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def prepare():
 assert os.environ.get('SSH_CONNECTION','').split()[2:3]==['172.16.101.31']
 env=runpy.run_path(str(RUN/'vm-public-egress-inventory.py'));get,ref=env['get'],env['ref']
 with pathlib.Path('/home/chabking/workspace/ani-network-service-runs/net05a-heavy.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  old=json.loads((RUN/'evidence/vm-public-egress-inventory.json').read_text())
  assert get('namespace','kube-system')['metadata']['uid']==old['cluster']['uid']=='5277649d-e28d-4a0a-ae76-8354365c63bc'
  nodes=get('nodes')['items'];assert {o['metadata']['name'] for o in nodes}==set(env['NODES'])
  for node in nodes:
   ip,uid=env['NODES'][node['metadata']['name']]
   assert node['metadata']['uid']==uid and any(a['type']=='InternalIP' and a['address']==ip for a in node['status']['addresses'])
   assert any(c['type']=='Ready' and c['status']=='True' for c in node['status']['conditions'])
  shared=[]
  for resource in ['vlannetworks','eipgateways','subnets','eips','snats','vpcs']:
   for baseline in old['native'][resource]:
    current=get(resource+'.'+env['GROUP'],baseline['name'],baseline['namespace'] or None)
    assert current['metadata']['uid']==baseline['uid'] and current.get('spec',{})==baseline['spec'] and not current['metadata'].get('deletionTimestamp')
    assert current['metadata'].get('labels',{})==baseline['labels']
    shared.append({'resource':resource,**ref(current),'labels':baseline['labels'],'spec':baseline['spec'],'spec_sha256':digest(baseline['spec'])})
  pool=next(o for o in shared if o['resource']=='subnets')
  assert pool['name']=='pool-bb811753b25a4fe598dff9cedf5afd1d' and pool['uid']=='f92a4ccb-5858-4ab7-88a9-ab6026b781bc'
  assert pool['spec']['type']=='Public' and pool['spec']['cidrBlock']=='172.16.102.0/24'
  config=json.loads((RUN/'private/resource-candidate/vm.json').read_text())
  mapping=next(x for x in config['Networks'] if x['TenantID']==TENANT)
  assert mapping['VPCID']=='vpc_e413becb1f4a46468f058e32f33a6305' and mapping['NamespaceUID']=='09e58ecb-fe20-454f-bc7b-2da3c406f0ac'
  vpc=get('vpcs.'+env['GROUP'],mapping['VPCID'].replace('_','-',1),NS)
  subnet=get('subnets.'+env['GROUP'],mapping['KCNSubnetRef'].split('/')[-1],NS)
  assert vpc['metadata']['uid']=='f7ae3b4e-2b90-4fec-bb90-67673780a83a' and subnet['metadata']['uid']=='c24a983c-f9f4-403d-9807-8164004000a3'
  assert subnet['spec']['cidrBlock']=='10.83.1.0/24'
  protected={'vms':[],'sources':[],'nads':[],'namespaces':[]}
  for ns,vmid,uid in [(NS,'55099440-c47b-4f81-afc5-7d8e740d14dc','7bca4671-89dd-4925-bad9-17e77401729d'),('tenant-d87c4dbd-04fa-4066-9ef6-c6fa4217f316','e3c11c5a-cf12-4630-a8f9-f907048ffdfc','fde3b420-6bde-4169-9eb1-ed025c60a1a6'),(NS,'068d56dd-d4fc-4ca8-8430-16c36bad0761','03906ba4-36d4-4c11-b4c5-312c9c734274'),(NS,'fc154879-8a40-4b18-967c-215f5cf5992d','9a751320-5f97-4e56-af6a-30964610321b')]:
   vm=get('vms.kubevirt.io','vm-'+vmid,ns);assert vm['metadata']['uid']==uid and not vm['metadata'].get('deletionTimestamp')
   protected['vms'].append({**ref(vm),'spec_sha256':digest(vm['spec'])})
  for source in config['Service']['Media']:
   pvc=get('pvc',source['pvc_name'],source['namespace'])
   assert pvc['metadata']['uid']==source['pvc_uid'] and pvc['status']['phase']=='Bound' and pvc['metadata']['annotations']['vm.ani.io/source-sha256']==source['checksum']
   protected['sources'].append({**ref(pvc),'phase':'Bound','checksum':source['checksum']})
  for m in config['Networks']:
   ns=get('namespace',m['Namespace']);assert ns['metadata']['uid']==m['NamespaceUID']
   protected['namespaces'].append(ref(ns))
   nad=get('network-attachment-definitions.k8s.cni.cncf.io',m['NADName'],m['Namespace'])
   assert nad['metadata']['uid']==m['NADUID'] and digest(json.loads(nad['spec']['config']))==m['NADConfigDigest']
   protected['nads'].append({**ref(nad),'config_sha256':m['NADConfigDigest']})
  for resource,name in [('eips',NAMES['eip']),('snats',NAMES['snat'])]:
   items=get(resource+'.'+env['GROUP'])['items']
   assert not any(o['metadata']['namespace']==NS and o['metadata']['name']==name for o in items),'task target already exists; do not create/adopt'
   assert not any(o['metadata']['namespace']==NS for o in items),'tenant already has native egress; review conflicts'
  previous=json.loads(PLAN.read_text()) if PLAN.exists() else None
  intents=previous['creation_intents'] if previous else {k:str(uuid.uuid4()) for k in NAMES}
  def obj(kind,key,spec):
   return {'apiVersion':env['GROUP']+'/v1','kind':kind,'metadata':{'namespace':NS,'name':NAMES[key],'labels':{'vm-mvp-test.ani.io/run':TASK,'app.kubernetes.io/managed-by':'vm-mvp-test-driver'},'annotations':{'vm-mvp-test.ani.io/creation-intent':intents[key]}},'spec':spec}
  eip=obj('EIP','eip',{'subnet':'kcn-system/'+pool['name'],'ipVersion':'IPv4'})
  snat=obj('Snat','snat',{'eip':NAMES['eip'],'vpc':NS+'/'+vpc['metadata']['name'],'cidrs':['10.83.1.0/24'],'disable':False})
  plan={'scope':'PUBLIC_ONLY; task tenant1 native EIP+Snat; NOT Gov/Resource Network API acceptance','cluster_uid':old['cluster']['uid'],'namespace':NS,'namespace_uid':mapping['NamespaceUID'],'creation_intents':intents,'objects':{'eip':eip,'snat':snat},'target_vpc':{**ref(vpc),'spec_sha256':digest(vpc['spec'])},'target_subnet':{**ref(subnet),'spec_sha256':digest(subnet['spec'])},'shared_protected':shared,'protected':protected,'source_inventory_sha256':hashlib.sha256((RUN/'evidence/vm-public-egress-inventory.json').read_bytes()).hexdigest(),'steps':['fresh all UID/spec/identity and both target absences','journal before EIP CREATE exactly once','freeze ACK UID; observe only original EIP until Valid/Initialized and phase Available, allocated IPv4 in .193-.207','journal before Snat CREATE exactly once using recorded EIP original UID and exact VPC UID','observe original EIP+Snat Bound and address, then Root performs real guest public DNS/HTTPS checks'],'unknown_ack':'persist unknown; GET exact target/spec/intent for diagnosis, no automatic adoption or repeated CREATE; stop dependent step until reviewed identity','cleanup_order':['Snat exact original UID/RV normal DELETE and Gone','EIP exact original UID/RV normal DELETE and Gone'],'forbidden':['shared default .192 or routes','KCN code/config/restart','intranet objects/office tests','source/VM/volume/NAD mutations','SQL business writes','finalizer/force/manual backend deletion'],'dns':'READ_ONLY; no Subnet DNS/Guest changes in this plan','cluster_mutations':0,'applied':False}
  if previous:assert previous==plan,'prepared identity changed; review instead of overwrite'
  PLAN.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
  PLAN.write_text(json.dumps(plan,indent=2)+'\n');PLAN.chmod(0o600)
  result={'result':'PREPARED_ZERO_CLUSTER_WRITES','plan_sha256':digest(plan),'prepared_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'plan':plan,'execution_allowed':False}
  PUBLIC.write_text(json.dumps(result,indent=2)+'\n');PUBLIC.chmod(0o600)
  print(json.dumps({'result':result['result'],'plan_sha256':result['plan_sha256'],'receipt':str(PUBLIC),'protected_counts':{k:len(v) for k,v in protected.items()},'cluster_mutations':0}))



def atomic(path,value):
 path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
 tmp=path.with_name('.'+path.name+'.'+uuid.uuid4().hex+'.tmp')
 fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 with os.fdopen(fd,'w') as out:
  out.write(json.dumps(value,indent=2)+'\n');out.flush();os.fsync(out.fileno())
 os.replace(tmp,path)
 fd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
 try:os.fsync(fd)
 finally:os.close(fd)


def execute(args):
 assert os.environ.get('SSH_CONNECTION','').split()[2:3]==['172.16.101.31']
 assert args.execute_authorized if args.action in ('apply','cleanup') else True
 plan=json.loads(PLAN.read_text());assert digest(plan)==args.plan_sha
 env=runpy.run_path(str(RUN/'vm-public-egress-inventory.py'));get,ref=env['get'],env['ref']
 journal=PLAN.with_name('native-state.json')
 state=json.loads(journal.read_text()) if journal.exists() else {'plan_sha256':args.plan_sha,'entries':{key:{'attempts':0,'ack_unknown':False} for key in ('eip','snat')}}
 assert state['plan_sha256']==args.plan_sha
 receipt=RUN/('evidence/vm-public-egress-native-cleanup.json' if args.action=='cleanup' else 'evidence/vm-public-egress-native-live.json')
 def publish(phase):
  state['phase']=phase;atomic(journal,state)
  atomic(receipt,{'observed_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'phase':phase,'plan_sha256':args.plan_sha,'scope':plan['scope'],'entries':state['entries'],'deletions':state.get('deletions',{}),'controller_cleanup':state.get('controller_cleanup'),'protected_unchanged':True,'api_acceptance':'NOT_RUN_NATIVE_ONLY','guest_public_egress':'NOT_RUN','shared_mutations':0})
 def read(key,allow_terminating=False):
  obj=plan['objects'][key]
  p=subprocess.run(env['K']+['get',key+'s.'+env['GROUP'],obj['metadata']['name'],'-n',NS,'--ignore-not-found','-o','json'],capture_output=True,text=True,timeout=15)
  assert p.returncode==0,'original target read failed'
  if not p.stdout.strip():return None
  current=json.loads(p.stdout);entry=state['entries'][key]
  assert entry.get('uid') and current['metadata']['uid']==entry['uid'],'unknown ACK or same-name replacement: stop dependent steps'
  assert current['metadata'].get('annotations',{}).get('vm-mvp-test.ani.io/creation-intent')==plan['creation_intents'][key]
  actual=dict(current.get('spec',{}));wanted=dict(obj['spec'])
  if key=='snat':actual.setdefault('disable',False)
  assert all(actual.get(k)==v for k,v in wanted.items()),'target spec drift'
  assert allow_terminating or not current['metadata'].get('deletionTimestamp'),'target terminating'
  return current
 def guards():
  assert get('namespace','kube-system')['metadata']['uid']==plan['cluster_uid']
  nodes=get('nodes')['items'];assert {o['metadata']['name'] for o in nodes}==set(env['NODES'])
  for o in nodes:
   ip,uid=env['NODES'][o['metadata']['name']];assert o['metadata']['uid']==uid and any(a['type']=='InternalIP' and a['address']==ip for a in o['status']['addresses'])
  for frozen in plan['shared_protected']:
   current=get(frozen['resource']+'.'+env['GROUP'],frozen['name'],frozen['namespace'] or None)
   assert current['metadata']['uid']==frozen['uid'] and current.get('spec',{})==frozen['spec'] and current['metadata'].get('labels',{})==frozen['labels'] and not current['metadata'].get('deletionTimestamp'),'shared identity/spec changed'
  for kind,frozen in [('vpcs',plan['target_vpc']),('subnets',plan['target_subnet'])]:
   current=get(kind+'.'+env['GROUP'],frozen['name'],frozen['namespace']);assert current['metadata']['uid']==frozen['uid'] and digest(current['spec'])==frozen['spec_sha256']
  for category,items in plan['protected'].items():
   for frozen in items:
    kind={'vms':'vms.kubevirt.io','sources':'pvc','nads':'network-attachment-definitions.k8s.cni.cncf.io','namespaces':'namespace'}[category]
    current=get(kind,frozen['name'],frozen['namespace'] or None);assert current['metadata']['uid']==frozen['uid'] and not current['metadata'].get('deletionTimestamp'),'protected identity changed'
    if category=='vms':assert digest(current['spec'])==frozen['spec_sha256'],'protected VM template changed'
    elif category=='sources':assert current['status']['phase']=='Bound' and current['metadata']['annotations']['vm.ani.io/source-sha256']==frozen['checksum']
    elif category=='nads':assert digest(json.loads(current['spec']['config']))==frozen['config_sha256']
 def create(key):
  entry=state['entries'][key]
  assert entry['attempts']==0 and not entry['ack_unknown'] and not entry.get('uid'),'never resend a consumed create intent'
  desired=plan['objects'][key]
  existing=get(key+'s.'+env['GROUP'])['items']
  assert not any(o['metadata']['namespace']==NS for o in existing),'native tenant egress appeared; review before creation'
  entry.update(attempts=1,ack_unknown=True,request_sha256=digest(desired),sent_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
  publish(key.upper()+'_CREATE_ACK_UNKNOWN')
  try:
   p=subprocess.run(env['K']+['create','-f','-','-o','json'],input=json.dumps(desired),capture_output=True,text=True,timeout=15)
   assert p.returncode==0,'create result unknown/rejected; retain original intent and inspect exact target without resending'
   current=json.loads(p.stdout);assert current['kind']==desired['kind'] and current['metadata']['name']==desired['metadata']['name'] and current['metadata']['namespace']==NS
   assert str(uuid.UUID(current['metadata']['uid']))==current['metadata']['uid']
   entry.update(uid=current['metadata']['uid'],ack_unknown=False,ack=ref(current))
   publish(key.upper()+'_ACKED')
  except BaseException:
   atomic(journal,state);raise
 def observe(key):
  deadline=time.monotonic()+args.wait_seconds
  while True:
   current=read(key);assert current is not None,'original target disappeared'
   status=current.get('status',{});entry=state['entries'][key]
   entry['current']={'ref':ref(current),'spec':current.get('spec',{}),'status':status}
   conditions={c['type']:c['status'] for c in status.get('conditions',[])}
   if key=='eip':
    address=status.get('ipAddress') or current['spec'].get('ipAddress')
    # KCN persists the dynamically allocated address in spec.ipAddress.
    if address:
     assert ipaddress.ip_address(address) in ipaddress.ip_network('172.16.102.0/24') and 193<=int(address.split('.')[-1])<=207,'unexpected public allocation; never take shared .192'
     if entry.get('address'):assert entry['address']==address,'allocated address changed'
     entry['address']=address
    ready=conditions.get('Valid')=='True' and conditions.get('Initialized')=='True' and status.get('phase') in ('Available','Bound') and bool(address)
    if status.get('phase')=='Bound':assert status.get('boundResource',{}).get('resource')==NAMES['snat'],'EIP bound to another target'
   else:
    ready=status.get('phase')=='Bound' and conditions.get('Valid')=='True' and status.get('eipAddress')==state['entries']['eip']['address']
   publish(key.upper()+('_READY' if ready else '_PENDING'))
   if ready:return True
   if time.monotonic()>=deadline:return False
   time.sleep(1)
 with pathlib.Path('/home/chabking/workspace/ani-network-service-runs/net05a-heavy.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  guards()
  if args.action=='cleanup':
   for key in ('snat','eip'):
    current=read(key,True)
    deletions=state.setdefault('deletions',{})
    if current is not None and not current['metadata'].get('deletionTimestamp'):
     assert key not in deletions,'unknown or consumed original DELETE: observe without resending'
     if key=='eip':
      deadline=time.monotonic()+args.wait_seconds
      while current['status'].get('phase')!='Available' or current['status'].get('boundResource'):
       if time.monotonic()>=deadline:publish('EIP_WAITING_NORMAL_UNBIND');return
       time.sleep(1);current=read(key,True);assert current is not None
     metadata=current['metadata']
     options={'apiVersion':'v1','kind':'DeleteOptions','propagationPolicy':'Background','preconditions':{'uid':metadata['uid'],'resourceVersion':metadata['resourceVersion']}}
     deletions[key]={'attempts':1,'ack_unknown':True,'request':options,'sent_at':datetime.datetime.now(datetime.timezone.utc).isoformat()}
     publish(key.upper()+'_DELETE_ACK_UNKNOWN')
     path='/apis/'+env['GROUP']+'/v1/namespaces/'+NS+'/'+key+'s/'+metadata['name']
     completed=subprocess.run(env['K']+['delete','--raw='+path,'-f','-'],input=json.dumps(options),capture_output=True,text=True,timeout=15)
     assert completed.returncode==0,'original DELETE unknown/rejected; no retry or force'
     deletions[key]['ack_unknown']=False
     publish(key.upper()+'_DELETE_ACKED')
    deadline=time.monotonic()+args.wait_seconds
    while read(key,True) is not None:
     if time.monotonic()>=deadline:publish(key.upper()+'_NORMAL_GC_PENDING');return
     time.sleep(1)
    deletions.setdefault(key,{'attempts':0,'observed_already_absent':True})['gone']=True
    publish(key.upper()+'_GONE')
   expected_router='vpc.'+NS+'.'+plan['target_vpc']['name']
   pod=get('pod','kcn-ovn-central-85dc666849-5dpwf','kcn-system')
   assert pod['metadata']['uid']=='b899a84b-a7c9-4d59-964c-5b317e6336fd' and pod['status']['phase']=='Running'
   output={}
   for action in ('lr-route-list','lr-policy-list','lr-nat-list','lrp-list'):
    p=subprocess.run(env['K']+['exec','-n','kcn-system',pod['metadata']['name'],'-c','ovn-central','--','ovn-nbctl','--timeout=5',action,expected_router],capture_output=True,text=True,timeout=12)
    assert p.returncode==0,'exact OVN cleanup read failed'
    output[action]=p.stdout
   pool_ref=next(o for o in plan['shared_protected'] if o['resource']=='subnets')
   pool=get('subnets.'+env['GROUP'],pool_ref['name'],pool_ref['namespace'])
   assert pool['metadata']['uid']==pool_ref['uid'] and pool['spec']==pool_ref['spec']
   allocation=pool['status']
   address_returned=allocation.get('v4usingIPs')==1 and allocation.get('v4usingIPrange')=='172.16.102.192' and allocation.get('v4availableIPrange')=='172.16.102.193-172.16.102.207'
   nat_gone='172.16.102.193' not in output['lr-nat-list']
   policy_gone='10.83.1.0/24' not in output['lr-policy-list']
   uplink_gone='public.uplink.'+NS+'.'+plan['target_vpc']['name'] not in output['lrp-list'] and 'public.uplink.' not in output['lr-route-list']
   state['controller_cleanup']={'ovn':output,'pool_allocation':{k:allocation.get(k) for k in ('v4availableIPs','v4availableIPrange','v4usingIPs','v4usingIPrange')},'temporary_nat_gone':nat_gone,'temporary_policy_gone':policy_gone,'temporary_uplink_gone':uplink_gone,'address_193_returned':address_returned,'direct_ovn_mutations':0}
   guards()
   publish('CLEANED' if all((address_returned,nat_gone,policy_gone,uplink_gone)) else 'NATIVE_GONE_CONTROLLER_CLEANUP_PENDING')
   print(json.dumps({'phase':state['phase'],'receipt':str(receipt),'deletions':deletions,'controller_cleanup':state['controller_cleanup'],'protected_unchanged':True}));return
  for key in ('eip','snat'):
   entry=state['entries'][key]
   if not entry.get('uid'):
    assert not entry['ack_unknown'] and entry['attempts']==0,'unknown original ACK: no retry'
    if args.action!='apply':publish('NOT_YET_CREATED');return
    create(key)
   if not observe(key):return
  eip=read('eip');assert eip['status'].get('phase')=='Bound' and eip['status'].get('boundResource',{}).get('resource')==NAMES['snat']
  guards();publish('READY_NATIVE_PUBLIC_EGRESS_GUEST_PENDING')
 print(json.dumps({'phase':state['phase'],'receipt':str(receipt),'eip_uid':state['entries']['eip'].get('uid'),'address':state['entries']['eip'].get('address'),'snat_uid':state['entries']['snat'].get('uid')}))


def main():
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('action',choices=('prepare','apply','observe','cleanup'),default='prepare',nargs='?')
 parser.add_argument('--execute-authorized',action='store_true');parser.add_argument('--plan-sha')
 parser.add_argument('--wait-seconds',type=int,choices=range(31),default=20)
 args=parser.parse_args()
 if args.action=='prepare':prepare()
 else:execute(args)
if __name__=='__main__':main()
