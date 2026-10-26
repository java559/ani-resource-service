#!/usr/bin/env python3
"""One bounded read of the two task egress UIDs, controller result and exact LR."""
import datetime,fcntl,json,pathlib,runpy,subprocess,time
RUN=pathlib.Path('/home/chabking/workspace/ani-resource-service-runs/vm-mvp-20261023-01a168ce')
NS='tenant-b3c946b5-7eb3-4feb-9f82-79712062ca5c'
VPC='vpc-e413becb1f4a46468f058e32f33a6305'
def main():
 env=runpy.run_path(str(RUN/'vm-public-egress-inventory.py'));get,ref=env['get'],env['ref'];K=env['K']
 with pathlib.Path('/home/chabking/workspace/ani-network-service-runs/net05a-heavy.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  assert get('namespace','kube-system')['metadata']['uid']=='5277649d-e28d-4a0a-ae76-8354365c63bc'
  state=json.loads((RUN/'private/public-egress/native-state.json').read_text())
  result={'observed_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'objects':{},'events':{},'controller_logs':[],'ovn':{},'mutations':0,'scope':'only task tenant1 public egress; no native/OVN/KCN/config changes'}
  terms=[VPC];earliest=[]
  for key in ('eip','snat'):
   entry=state['entries'][key];name='vm-mvp-01a168ce-public-'+key
   obj=get(key+'s.'+env['GROUP'],name,NS);assert obj['metadata']['uid']==entry['uid']
   result['objects'][key]={'ref':ref(obj),'spec':obj['spec'],'status':obj.get('status',{})}
   terms.extend([name,entry['uid']]);earliest.append(entry['sent_at'])
   events=json.loads(env['command'](['get','events','-n',NS,'--field-selector=involvedObject.uid='+entry['uid'],'-o','json']))['items']
   result['events'][key]=[{'type':o.get('type'),'reason':o.get('reason'),'message':o.get('message'),'count':o.get('count'),'first':o.get('firstTimestamp'),'last':o.get('lastTimestamp')} for o in events]
  vpc=get('vpcs.'+env['GROUP'],VPC,NS);assert vpc['metadata']['uid']=='f7ae3b4e-2b90-4fec-bb90-67673780a83a'
  router=vpc['status']['boundResources']['router'];assert router=='vpc.'+NS+'.'+VPC
  result['vpc']={'ref':ref(vpc),'spec':vpc['spec'],'status':vpc['status']}
  pods=get('pods',ns='kcn-system')['items']
  ovn=next(p for p in pods if p['metadata']['name']=='kcn-ovn-central-85dc666849-5dpwf')
  assert ovn['metadata']['uid']=='b899a84b-a7c9-4d59-964c-5b317e6336fd' and ovn['status']['phase']=='Running'
  result['ovn_pod']=ref(ovn)
  def exec_read(argv):
   p=subprocess.run(K+['exec','-n','kcn-system',ovn['metadata']['name'],'-c','ovn-central','--']+argv,capture_output=True,text=True,timeout=12)
   return {'exit_code':p.returncode,'stdout':p.stdout,'stderr':p.stderr}
  for action in ('lr-route-list','lr-policy-list','lr-nat-list'):
   result['ovn'][action]=exec_read(['ovn-nbctl','--timeout=5',action,router])
  result['ovn']['router-ports']=exec_read(['ovn-nbctl','--timeout=5','lrp-list',router])
  since=min(earliest).replace('+00:00','Z')
  for pod in pods:
   if not pod['metadata']['name'].startswith('kcn-controller-'):continue
   p=subprocess.run(K+['logs','-n','kcn-system',pod['metadata']['name'],'-c','kcn-controller','--since-time='+since,'--tail=3000','--timestamps'],capture_output=True,text=True,timeout=15)
   result['controller_logs'].append({'ref':ref(pod),'exit_code':p.returncode,'since':since,'matching_lines':[line for line in p.stdout.splitlines() if any(term in line for term in terms)]})
  started=time.monotonic()
  p=subprocess.run(['curl','--connect-timeout','4','--max-time','8','--silent','--show-error','--output','/dev/null','--write-out','%{http_code} %{remote_ip} %{time_connect} %{time_total}','https://1.1.1.1/cdn-cgi/trace'],capture_output=True,text=True,timeout=10)
  result['fedora_same_target_once']={'request_count':1,'exit_code':p.returncode,'timing':p.stdout,'stderr':p.stderr,'elapsed_seconds':round(time.monotonic()-started,3),'tls_verification':'DEFAULT_ENABLED','guest_acceptance':False}
  path=RUN/'evidence/vm-public-egress-diagnosis.json';path.write_text(json.dumps(result,indent=2)+'\n');path.chmod(0o600)
  print(json.dumps({'result':'READ_ONLY_COMPLETE','receipt':str(path),'ovn':result['ovn'],'fedora_same_target_once':result['fedora_same_target_once'],'eip_node':result['objects']['eip']['status'].get('boundResource',{}).get('nodeName'),'controller_matching_line_count':sum(len(x['matching_lines']) for x in result['controller_logs'])}))
if __name__=='__main__':main()
