#!/usr/bin/env python3
"""Single passive, no-promisc public TCP header capture through an existing daemon."""
import datetime,fcntl,hashlib,json,pathlib,runpy,select,subprocess,time,os
RUN=pathlib.Path('/home/chabking/workspace/ani-resource-service-runs/vm-mvp-20261023-01a168ce')
def main():
 env=runpy.run_path(str(RUN/'vm-public-egress-inventory.py'));get,ref=env['get'],env['ref'];K=env['K']
 with pathlib.Path('/home/chabking/workspace/ani-network-service-runs/net05a-heavy.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  assert get('namespace','kube-system')['metadata']['uid']=='5277649d-e28d-4a0a-ae76-8354365c63bc'
  node=get('node','ani-02');assert node['metadata']['uid']=='73564fa7-98ad-45d4-9576-be59f6d5d245' and any(a['type']=='InternalIP' and a['address']=='172.16.101.11' for a in node['status']['addresses'])
  pod=get('pod','kcn-ovs-ds-k58xm','kcn-system');assert pod['metadata']['uid']=='dd914acc-44cd-42b3-b2bd-0f7dc16e648b' and pod['spec']['nodeName']=='ani-02' and pod['spec'].get('hostNetwork') is True and pod['status']['phase']=='Running'
  def exec_prefix():return K+['exec','-n','kcn-system',pod['metadata']['name'],'-c','openvswitch','--']
  before=subprocess.run(exec_prefix()+['ip','-j','link','show','dev','ens35'],check=True,capture_output=True,text=True,timeout=12)
  interface=json.loads(before.stdout);assert len(interface)==1 and interface[0]['ifindex']==3 and interface[0]['address']=='00:0c:29:1a:5a:19' and interface[0]['operstate']=='UP'
  plan=json.loads((RUN/'private/public-egress/native-plan.json').read_text())
  def shared():
   values=[]
   for frozen in plan['shared_protected']:
    current=get(frozen['resource']+'.'+env['GROUP'],frozen['name'],frozen['namespace'] or None)
    assert current['metadata']['uid']==frozen['uid'] and current['spec']==frozen['spec'] and current['metadata'].get('labels',{})==frozen['labels']
    values.append({'ref':ref(current),'spec_sha256':hashlib.sha256(json.dumps(current['spec'],sort_keys=True,separators=(',',':')).encode()).hexdigest()})
   return values
  shared_before=shared()
  old=get('eips.'+env['GROUP'],'ani-default-eip-36b8b4bcec3d4396ae3d5e03dea9466b','kcn-system');assert old['metadata']['uid']=='e7c59daf-6b28-467e-b270-b043c96be9cc' and old['spec']['ipAddress']=='172.16.102.192'
  current=get('eips.'+env['GROUP'],'vm-mvp-01a168ce-public-eip','tenant-b3c946b5-7eb3-4feb-9f82-79712062ca5c');assert current['metadata']['uid']=='144954ba-6aa3-4197-a15a-3cdd78af7588' and current['spec']['ipAddress']=='172.16.102.193' and current['status']['boundResource']['nodeName']=='ani-02'
  capture=['timeout','25s','tcpdump','-p','-nn','-l','-c','20','-i','ens35','tcp and host 172.16.102.193 and host 1.1.1.1 and port 443']
  start=time.monotonic();process=subprocess.Popen(exec_prefix()+capture,stdout=subprocess.PIPE,stderr=subprocess.PIPE,bufsize=0)
  outfd,errfd=process.stdout.fileno(),process.stderr.fileno()
  collected={outfd:bytearray(),errfd:bytearray()}
  streams=[process.stdout,process.stderr];ready=False;readiness_line=None
  deadline=time.monotonic()+5
  while time.monotonic()<deadline and streams and not ready:
   available,_,_=select.select(streams,[],[],max(0,deadline-time.monotonic()))
   for stream in available:
    data=os.read(stream.fileno(),65536)
    if not data:streams.remove(stream);continue
    collected[stream.fileno()].extend(data)
   lines=bytes(collected[errfd]).decode(errors='replace').splitlines()
   readiness_line=next((line for line in lines if 'listening on ens35' in line),None)
   ready=readiness_line is not None
  if ready:print(json.dumps({'phase':'CAPTURE_READY','listening_line':readiness_line,'node':'ani-02','interface':'ens35','eip':'172.16.102.193','timeout_seconds':25}),flush=True)
  else:print(json.dumps({'phase':'CAPTURE_NOT_READY','readiness_deadline_seconds':5}),flush=True)
  tailout,tailerr=process.communicate(timeout=35)
  stdout=(bytes(collected[outfd])+tailout).decode(errors='replace')
  stderr=(bytes(collected[errfd])+tailerr).decode(errors='replace')
  after=subprocess.run(exec_prefix()+['ip','-j','link','show','dev','ens35'],check=True,capture_output=True,text=True,timeout=12)
  assert json.loads(after.stdout)==interface,'interface drift during passive capture'
  shared_after=shared()
  result={'result':'CAPTURE_COMPLETED' if ready else 'NOT_MEASURED_NO_READY_ACK','observed_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'node':ref(node),'pod':ref(pod),'existing_exec_uid':65534,'interface_before':interface,'interface_unchanged':True,'command':capture,'capture_ready':ready,'exit_code':process.returncode,'elapsed_seconds':round(time.monotonic()-start,3),'packet_headers':stdout.splitlines(),'diagnostic_stderr':stderr.splitlines(),'payload_or_pcap_saved':False,'promiscuous_requested':False,'shared_default_eip':{'uid':old['metadata']['uid'],'address':'172.16.102.192','bound_node':old['status'].get('boundResource',{}).get('nodeName')},'task_eip':{'uid':current['metadata']['uid'],'address':'172.16.102.193','bound_node':'ani-02'},'shared_before':shared_before,'shared_after':shared_after,'shared_unchanged':True,'mutations':0}
  path=RUN/'evidence/vm-public-egress-capture-synchronized.json';path.write_text(json.dumps(result,indent=2)+'\n');path.chmod(0o600)
  print(json.dumps({'result':result['result'],'receipt':str(path),'capture_ready':ready,'exit_code':process.returncode,'headers':result['packet_headers'],'stderr':result['diagnostic_stderr'],'old_192_node':result['shared_default_eip']['bound_node'],'shared_unchanged':True}),flush=True)
if __name__=='__main__':main()
