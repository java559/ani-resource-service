from pathlib import Path
import datetime,fcntl,hashlib,json,os,subprocess,time
D=Path('/home/chabking/workspace/ani-resource-service-runs/vm-mvp-20261023-01a168ce/iso-marker-02');OUT=D/'boot-activity-01.json'
assert os.uname().nodename=='fedora' and Path(__file__).resolve().parent==D and not OUT.exists()
assert hashlib.sha256((D/'native-01.json').read_bytes()).hexdigest()=='bd28c5d1fc50f1be0225569f288e042c87e35d958dcabb03627a31356c576c1a'
x=json.loads((D/'native-01.json').read_text());n=x['native'];ns=x['protected_before']['network']['Namespace'];K=['/home/chabking/ani-four-service-deploy-20261012/tools/kubectl','--kubeconfig','/home/chabking/ani-four-service-deploy-20261012/private/kubeconfig','--request-timeout=5s']
def call(args):
 p=subprocess.run(K+args,capture_output=True,text=True,timeout=10);assert p.returncode==0,p.stderr[:250];return p.stdout
def guard():
 v=json.loads(call(['get','vmi',n['vmi']['name'],'-n',ns,'-o','json']));p=json.loads(call(['get','pod','virt-launcher-vm-96575d3e-185d-4939-a01f-b82562d7af08-578mb','-n',ns,'-o','json']));assert v['metadata']['uid']==n['vmi']['uid']=='27550850-b1fc-4fe9-99fb-6e08ef7d5a24' and v['status']['phase']=='Running' and p['metadata']['uid']=='e1f4c8f8-7c13-429a-9fb2-60f1a100283c' and p['status']['phase']=='Running';assert any(o['uid']==v['metadata']['uid'] and o.get('controller') for o in p['metadata']['ownerReferences']);return p['metadata']['name']
with open('/home/chabking/workspace/ani-network-service-runs/net05a-heavy.lock') as lock:
 fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);assert json.loads(call(['get','namespace','kube-system','-o','json']))['metadata']['uid']=='5277649d-e28d-4a0a-ae76-8354365c63bc';pod=guard()
 def sample():
  raw=call(['exec','-n',ns,pod,'-c','compute','--','virsh','-c','qemu+unix:///session?socket=/var/run/libvirt/virtqemud-sock','domstats',ns+'_'+n['vm']['name'],'--state','--cpu-total','--block']);rows={}
  for line in raw.splitlines():
   k,sep,v=line.strip().partition('=')
   if sep and (k=='state.state' or k.startswith('cpu.') or k.startswith('block.') and k.rsplit('.',1)[-1] in ['name','bytes','reqs']):rows[k]=v
  return rows
 before=sample();started=time.monotonic();time.sleep(8);after=sample();assert guard()==pod
 r={'result':'READONLY_DOMAIN_ACTIVITY_NOT_GUEST_INSTALL_ACCEPTANCE','at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'vm_id':x['vm_id'],'vmi_uid':n['vmi']['uid'],'launcher_uid':'e1f4c8f8-7c13-429a-9fb2-60f1a100283c','elapsed_seconds':time.monotonic()-started,'before':before,'after':after,'guest_or_vm_mutations':0,'heavy_lock_released':True}
 with OUT.open('x') as f:f.write(json.dumps(r,indent=2)+'\n')
 os.chmod(OUT,0o600);fcntl.flock(lock,fcntl.LOCK_UN);print(json.dumps(r))
