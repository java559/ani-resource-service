#!/usr/bin/env python3
"""Task-only read inventory; no native, database or service mutation paths."""
import datetime,fcntl,json,os,pathlib,subprocess
RUN=pathlib.Path('/home/chabking/workspace/ani-resource-service-runs/vm-mvp-20261023-01a168ce')
K=['/home/chabking/ani-four-service-deploy-20261012/tools/kubectl','--kubeconfig=/home/chabking/ani-four-service-deploy-20261012/private/kubeconfig','--request-timeout=10s']
GROUP='networking.kubercloud.com'
NODES={'ani-01':('172.16.101.10','201e41ac-c50d-4c77-b118-72d1ed93e111'),'ani-02':('172.16.101.11','73564fa7-98ad-45d4-9576-be59f6d5d245'),'ani-03':('172.16.101.12','af992ed9-cbe1-4f64-8fac-7433808bfab2')}
def command(args):
 p=subprocess.run(K+args,capture_output=True,text=True,timeout=15)
 assert p.returncode==0 or args[:2]==['auth','can-i'] and p.returncode==1,'read command failed: '+args[0]+' '+args[1]
 return p.stdout
def get(kind,name=None,ns=None):
 return json.loads(command(['get',kind]+([name] if name else [])+(['-n',ns] if ns else ['-A'] if not name else [])+['-o','json']))
def ref(o):
 m=o['metadata'];return {k:m.get(k,'') for k in ['namespace','name','uid','resourceVersion','deletionTimestamp']}
def project(o):
 return {'kind':o['kind'],**ref(o),'labels':o['metadata'].get('labels',{}),'owners':o['metadata'].get('ownerReferences',[]),'spec':o.get('spec',{}),'status':o.get('status',{})}
def main():
 assert os.environ.get('SSH_CONNECTION','').split()[2:3]==['172.16.101.31']
 with pathlib.Path('/home/chabking/workspace/ani-network-service-runs/net05a-heavy.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  ns=get('namespace','kube-system');assert ns['metadata']['uid']=='5277649d-e28d-4a0a-ae76-8354365c63bc'
  nodes=get('nodes')['items'];assert {o['metadata']['name'] for o in nodes}==set(NODES)
  facts=[]
  for o in nodes:
   address,uid=NODES[o['metadata']['name']];assert o['metadata']['uid']==uid and any(a['type']=='InternalIP' and a['address']==address for a in o['status']['addresses'])
   facts.append({**ref(o),'addresses':o['status']['addresses'],'conditions':o['status']['conditions'],'labels':o['metadata'].get('labels',{}),'kubelet':o['status']['nodeInfo']['kubeletVersion']})
  available=command(['api-resources','--api-group='+GROUP,'-o','name']).splitlines()
  result={'observed_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'ssh_host':'fedora','cluster':ref(ns),'nodes':facts,'api_resources':available,'native':{},'permissions':{},'mutations':0,'scope':'PUBLIC_ONLY; intranet five-object task remains paused'}
  for resource in ['vlannetworks','eipgateways','subnets','eips','snats','vpcs']:
   name=resource+'.'+GROUP
   if name not in available:result['native'][resource]={'available':False};continue
   items=get(name)['items']
   if resource=='subnets':items=[o for o in items if o.get('spec',{}).get('type')=='Public']
   if resource=='vpcs':items=[o for o in items if o['metadata'].get('namespace')=='kcn-system']
   result['native'][resource]=[project(o) for o in items]
  cfg=get('configmap','kcn-config','kcn-system')
  result['kcn_config']={**ref(cfg),'keys':sorted(cfg.get('data',{})),'selected':{k:v for k,v in cfg.get('data',{}).items() if k in ('managedDevices','defaultVPC','defaultVpc','nodeGroups','publicNetworks','dnsServers','dnsServer','clusterDNS','defaultDNSServers')}}
  config=json.loads((RUN/'private/resource-candidate/vm.json').read_text())
  result['task_subnet_dns']=[]
  for mapping in config['Networks']:
   subnet=get('subnets.'+GROUP,mapping['KCNSubnetRef'].split('/')[-1],mapping['Namespace'])
   result['task_subnet_dns'].append({**ref(subnet),'type':subnet.get('spec',{}).get('type'),'cidrBlock':subnet.get('spec',{}).get('cidrBlock'),'dnsServers':subnet.get('spec',{}).get('dnsServers'),'enableDHCP':subnet.get('spec',{}).get('enableDHCP'),'dhcpOptions':subnet.get('spec',{}).get('dhcpOptions')})
  for verb,resource in [('get','nodes'),('list','vlannetworks.'+GROUP),('create','vlannetworks.'+GROUP),('create','eipgateways.'+GROUP),('create','subnets.'+GROUP),('create','eips.'+GROUP),('create','snats.'+GROUP)]:
   result['permissions'][verb+' '+resource]=command(['auth','can-i',verb,resource]+(['-n','kcn-system'] if resource not in ('nodes','vlannetworks.'+GROUP,'eipgateways.'+GROUP) else [])).strip()
  path=RUN/'evidence/vm-public-egress-inventory.json';path.write_text(json.dumps(result,indent=2)+'\n');path.chmod(0o600)
  print(json.dumps({'result':'READ_ONLY_COMPLETE','receipt':str(path),'native_counts':{k:len(v) if isinstance(v,list) else v for k,v in result['native'].items()},'cluster_uid':ns['metadata']['uid'],'mutations':0}))
if __name__=='__main__':main()
