"""Exactly three read-only image-by-ID metadata reads; no introspection/write."""
from pathlib import Path
import datetime,fcntl,hashlib,json,os,subprocess,time
D=Path('/home/chabking/workspace/ani-resource-service-runs/vm-mvp-20261023-01a168ce/gc-normal-path-01')
K=['/home/chabking/ani-four-service-deploy-20261012/tools/kubectl','--kubeconfig=/home/chabking/ani-four-service-deploy-20261012/private/kubeconfig','--request-timeout=6s']
INNER=r'''import rados,rbd,json,sys,itertools
image_id=sys.argv[1]
assert image_id in ('20cc87c1cde11d','20cc87b511820b','20cc87987d1f99')
x={'image_id':image_id,'read_only':True,'snapshots':[],'writes':0}
c=rados.Rados(conffile='/etc/ceph/ceph.conf',name='client.admin',conf={'client_mount_timeout':'5','rados_mon_op_timeout':'5','rados_osd_op_timeout':'5'})
try:
 c.connect(timeout=5)
 with c.open_ioctx('ani-block-pool') as io:
  with rbd.Image(io,image_id=image_id,read_only=True) as image:
   snapshots=list(itertools.islice(image.list_snaps(),13));x['snapshot_count_at_most13']=len(snapshots)
   if len(snapshots)>12:x['result']='SNAPSHOT_BOUND_EXCEEDED_NO_CHILD_READ'
   else:
    for snap in snapshots:
     row={k:v for k,v in snap.items() if k in ('id','name','size')};x['snapshots'].append(row)
     try:
      if hasattr(image,'get_snap_namespace_type'):row['namespace_type']=image.get_snap_namespace_type(snap['id'])
      if hasattr(image,'set_snap_by_id'):image.set_snap_by_id(snap['id']);row['read_view']='set_snap_by_id'
      else:image.set_snap(snap['name']);row['read_view']='set_snap(name)'
      children=list(itertools.islice(image.list_children(),17));row['children']=children[:16];row['children_bound_exceeded']=len(children)>16;row['children_api']='list_children';image.set_snap(None)
     except Exception as e:row['children_read_error']={'class':type(e).__name__,'message':str(e)[:512]}
    x['result']='READONLY_SNAP_LIST_COMPLETE'
except Exception as e:x.update({'result':'READONLY_OPEN_OR_READ_FAILED','error_class':type(e).__name__,'error':str(e)[:512]})
finally:c.shutdown()
print(json.dumps(x,sort_keys=True,default=str))
'''
REMOTE='''import subprocess,time,json,datetime
code=CODE_VALUE
known=[('20cc87c1cde11d','root','csi-vol-27cbaa0a-050c-49ac-be04-e413fd724a00'),('20cc87b511820b','D3','csi-vol-0c9aca8f-bcc4-47bb-a120-013d90d690ac'),('20cc87987d1f99','D4','csi-vol-d7a7fc2f-419d-44ab-bec3-815631c460f5')]
x={'started_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'total_budget_seconds':45,'per_image_budget_seconds':15,'pool':'ani-block-pool','identity_reference':'original client.admin /etc/ceph/ceph.conf and /etc/ceph/keyring','writes':0,'trash_get_calls':0,'child_image_open_calls':0,'rows':[]};start=time.monotonic()
for image_id,disk,name in known:
 remaining=45-(time.monotonic()-start)
 if remaining<=0:x['result']='TOTAL_BUDGET_REACHED_NO_RETRY';break
 t=time.monotonic();row={'image_id':image_id,'disk':disk,'historical_image_name':name,'timeout_seconds':min(15,remaining)};x['rows'].append(row)
 try:
  p=subprocess.run(['python3','-c',code,image_id],capture_output=True,timeout=min(15,remaining));row['exit']=p.returncode
  if p.returncode==0:row['read']=json.loads(p.stdout)
  else:row['read_error']=p.stderr.decode(errors='replace')[:1024]
 except subprocess.TimeoutExpired:row['result']='TIMEOUT_NO_RETRY'
 row['elapsed_seconds']=round(time.monotonic()-t,3)
x.setdefault('result','THREE_BOUNDED_READONLY_ATTEMPTS_COMPLETE');x['elapsed_seconds']=round(time.monotonic()-start,3);x['finished_at']=datetime.datetime.now(datetime.timezone.utc).isoformat();print(json.dumps(x,sort_keys=True))
'''.replace('CODE_VALUE',repr(INNER))
def main():
 assert os.environ.get('SSH_CONNECTION','').split()[2:3]==['172.16.101.31']
 output=D/'three-snap-readonly.json';assert not output.exists()
 lock=Path('/home/chabking/workspace/ani-network-service-runs/net05a-heavy.lock').open('a')
 try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 except BlockingIOError:print('LOCK_BUSY_NO_REQUEST');return 73
 p=subprocess.run(K+['exec','-n','rook-ceph','rook-ceph-tools-68d6bc7cc7-2z8tk','-c','rook-ceph-tools','--','timeout','48','python3','-c',REMOTE],capture_output=True,timeout=53)
 x={'source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'kubectl_exit':p.returncode,'prior_preconnection_collector_error_retained_in':'readonly.json/three_snapshot_dependencies','fresh_cluster_or_version_or_task_or_schedule_queries':0,'backend_writes':0,'cluster_writes':0}
 if p.returncode==0:x['result']=json.loads(p.stdout)
 else:x['error']=p.stderr.decode(errors='replace')[:1024]
 output.write_text(json.dumps(x,sort_keys=True,indent=2)+'\n');print(json.dumps(x,sort_keys=True));return 0
if __name__=='__main__':raise SystemExit(main())
