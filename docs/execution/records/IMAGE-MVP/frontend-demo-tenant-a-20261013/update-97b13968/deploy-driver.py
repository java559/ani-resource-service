import base64, datetime, hashlib, json, pathlib, shlex, subprocess, sys, copy
R=pathlib.Path('/home/chabking/ani-image-mvp-runs/update-97b13968-20261013')
E=R/'evidence'; P=R/'private'
K=['/home/chabking/ani-four-service-deploy-20261012/tools/kubectl','--kubeconfig','/home/chabking/ani-four-service-deploy-20261012/private/kubeconfig']
UID='5277649d-e28d-4a0a-ae76-8354365c63bc'
def cmd(args,data=None,timeout=120):
 p=subprocess.run(args,input=data,capture_output=True,timeout=timeout)
 if p.returncode: raise RuntimeError('exit '+str(p.returncode)+' '+p.stderr.decode()[:2000])
 return p.stdout
def k(args,data=None,timeout=120):return cmd(K+args,data,timeout)
def save(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2))
def sql(q):return k(['-n','ani-platform','exec','postgresql-0','--','sh','-c','PGPASSWORD="$POSTGRES_PASSWORD" psql -U postgres -d ani_resource_network -At -v ON_ERROR_STOP=1 -c '+shlex.quote(q)]).decode().strip()
def guard():
 assert json.loads(k(['get','ns','kube-system','-o','json']))['metadata']['uid']==UID
 return json.loads(k(['-n','ani-system','get','deploy','ani-resource-service','-o','json']))
def backup(name):
 out=k(['-n','ani-platform','exec','postgresql-0','--','sh','-c','PGPASSWORD="$POSTGRES_PASSWORD" pg_dump -U postgres -d ani_resource_network --format=custom'],timeout=180)
 p=P/name;p.write_bytes(out);p.chmod(0o600)
 save(E/(name+'.json'),{'private_path':str(p),'sha256':hashlib.sha256(out).hexdigest(),'bytes':len(out),'timestamp':datetime.datetime.now(datetime.timezone.utc).isoformat(),'format':'pg_dump custom'})
def preflight():
 d=guard();save(P/'resource-deployment-before.json',d)
 c=d['spec']['template']['spec']['containers'][0]
 assert c['name']=='server'
 assert not any(x['name']=='ANI_INSTANCE_CONFIG_FILE' for x in c['env'])
 baseline={'image':c['image'],'replicas':d['spec']['replicas'],'resourceVersion':d['metadata']['resourceVersion'],'generation':d['metadata']['generation'],'schema':sql('select count(*),max(version) from network_schema_version'),'instance_enabled':False,'other_deployments':json.loads(k(['-n','ani-system','get','deploy','-o','json']))}
 baseline['other_deployments']={x['metadata']['name']:[c['image'] for c in x['spec']['template']['spec']['containers']] for x in baseline['other_deployments']['items'] if x['metadata']['name']!='ani-resource-service'}
 assert baseline['schema']=='13|13'
 applied=sql('select version,checksum from network_schema_version order by version').splitlines()
 files=sorted((R/'source/migrations').glob('*.sql'))
 for i,row in enumerate(applied):assert row==str(i+1)+'|'+hashlib.sha256(files[i].read_bytes()).hexdigest()
 baseline['network_operations']=sql('select state,count(*) from network_operations group by state order by state')
 baseline['image_spaces']=k(['-n','ani-platform','exec','postgresql-0','--','sh','-c','PGPASSWORD="$POSTGRES_PASSWORD" psql -U postgres -d ani_resource_image -At -c "select space_id,owner_scope,tenant_id,project_name,state from image.spaces order by space_id"']).decode()
 save(E/'baseline.json',baseline)
 backup('network-schema13-preflight.dump')
 migration=files[13].read_text()
 q="BEGIN; SET LOCAL lock_timeout='10s'; SET LOCAL statement_timeout='45s'; "+migration+"; SET CONSTRAINTS ALL IMMEDIATE; SELECT count(*) FROM network_lb_retired_bindings; ROLLBACK;"
 (E/'migration-dry-run.log').write_text(sql(q))
 assert sql('select count(*),max(version) from network_schema_version')=='13|13'
 save(E/'migration-dry-run.json',{'result':'pass','migration':files[13].name,'source_sha':'97b13968d156d65fe492cb583ec9920d21d0e57a','existing_checksums':'pass','deferred_constraints':'pass','rolled_back':True})
 print('preflight, private backup and schema14 rollback trial: pass')
def import_images():
 guard();im=json.loads((E/'image.json').read_text());out=[]
 def ik(args,data=None,timeout=120):return k(['kube-system' if x=='ani-system' else x for x in args],data,timeout)
 for node in ['ani-01','ani-02','ani-03']:
  name='image-update-97b13968-'+node
  pod={'apiVersion':'v1','kind':'Pod','metadata':{'name':name,'namespace':'ani-system','labels':{'ani.io/deployment-run':'image-update-20261013'}},'spec':{'nodeName':node,'restartPolicy':'Never','activeDeadlineSeconds':900,'automountServiceAccountToken':False,'containers':[{'name':'import','image':'172.16.101.10:5000/library/postgres:17.11-bookworm','imagePullPolicy':'IfNotPresent','command':['sh','-ec','sleep 840'],'securityContext':{'runAsUser':0,'allowPrivilegeEscalation':False,'capabilities':{'drop':['ALL']}},'resources':{'requests':{'cpu':'50m','memory':'128Mi'},'limits':{'cpu':'1000m','memory':'512Mi'}},'volumeMounts':[{'name':'ctr','mountPath':'/host-ctr','readOnly':True},{'name':'socket','mountPath':'/run/containerd/containerd.sock'},{'name':'work','mountPath':'/work'}]}],'volumes':[{'name':'ctr','hostPath':{'path':'/usr/local/bin/ctr','type':'File'}},{'name':'socket','hostPath':{'path':'/run/containerd/containerd.sock','type':'Socket'}},{'name':'work','emptyDir':{'sizeLimit':'512Mi'}}]}}
  pod['metadata']['namespace']='kube-system';pod['spec']['securityContext']={'seccompProfile':{'type':'RuntimeDefault'}}
  found=json.loads(ik(['-n','ani-system','get','pod',name,'--ignore-not-found','-o','json']) or b'null')
  if found:assert found['metadata']['labels']['ani.io/deployment-run']=='image-update-20261013' and found['spec']['nodeName']==node
  else:ik(['create','-f','-'],json.dumps(pod).encode())
  ik(['-n','ani-system','wait','--for=condition=Ready','pod/'+name,'--timeout=90s'])
  ik(['-n','ani-system','cp','--no-preserve=true',im['archive'],name+':/work/resource.oci.tar'],timeout=180)
  check=ik(['-n','ani-system','exec',name,'--','sha256sum','/work/resource.oci.tar']).decode().split()[0];assert check==im['archive_sha256']
  log=ik(['-n','ani-system','exec',name,'--','/host-ctr','-n','k8s.io','images','import','--platform','linux/amd64','--digests','--base-name','localhost/ani-resource','/work/resource.oci.tar'],timeout=120)
  (E/('import-'+node+'.log')).write_bytes(log)
  rows=ik(['-n','ani-system','exec',name,'--','/host-ctr','-n','k8s.io','images','list']).decode().splitlines()
  match=[s.split() for s in rows if s.split() and s.split()[0]==im['tag']];assert len(match)==1 and match[0][2]==im['digest'],match
  for ref in [im['reference'],'localhost/ani-resource@'+im['digest']]:
   ik(['-n','ani-system','exec',name,'--','/host-ctr','-n','k8s.io','images','tag','--force',im['tag'],ref])
  names=ik(['-n','ani-system','exec',name,'--','/host-ctr','-n','k8s.io','images','list','-q']).decode().splitlines();assert im['reference'] in names
  cp={'apiVersion':'v1','kind':'Pod','metadata':{'name':'resource-image-check-97b13968-'+node,'namespace':'ani-system','labels':{'ani.io/deployment-run':'image-update-20261013'}},'spec':{'nodeName':node,'restartPolicy':'Never','activeDeadlineSeconds':120,'automountServiceAccountToken':False,'securityContext':{'runAsNonRoot':True,'runAsUser':65532,'runAsGroup':65532,'seccompProfile':{'type':'RuntimeDefault'}},'containers':[{'name':'check','image':im['reference'],'imagePullPolicy':'IfNotPresent','args':['-h'],'securityContext':{'allowPrivilegeEscalation':False,'readOnlyRootFilesystem':True,'capabilities':{'drop':['ALL']}},'resources':{'requests':{'cpu':'50m','memory':'64Mi'},'limits':{'cpu':'500m','memory':'128Mi'}}}]}}
  k(['create','-f','-'],json.dumps(cp).encode());k(['-n','ani-system','wait','--for=jsonpath={.status.phase}=Succeeded','pod/'+cp['metadata']['name'],'--timeout=60s'])
  actual=json.loads(k(['-n','ani-system','get','pod',cp['metadata']['name'],'-o','json']))['status']['containerStatuses'][0];assert actual['state']['terminated']['exitCode']==0
  out.append({'node':node,'pod':name,'reference':im['reference'],'canonical_reference':'localhost/ani-resource@'+im['digest'],'archive_sha256':check,'cri_startup_check':actual,'result':'pass'});save(E/'image-import.json',out)
  k(['-n','ani-system','delete','pod',cp['metadata']['name'],'--grace-period=0','--wait=true','--timeout=45s'])
  ik(['-n','ani-system','delete','pod',name,'--grace-period=0','--wait=true','--timeout=45s'])
  print(node+' imported '+im['reference'],flush=True)
def deploy():
 d=guard();before=json.loads((P/'resource-deployment-before.json').read_text());im=json.loads((E/'image.json').read_text())
 assert d['spec']==before['spec'],'deployment changed during preparation'
 assert len(json.loads((E/'image-import.json').read_text()))==3
 k(['-n','ani-system','scale','deploy/ani-resource-service','--replicas=0','--current-replicas=1'])
 selector=','.join(key+'='+value for key,value in d['spec']['selector']['matchLabels'].items())
 k(['-n','ani-system','wait','--for=delete','pod','-l',selector,'--timeout=120s'])
 backup('network-schema13-before-migration.dump')
 job=json.loads(pathlib.Path('/home/chabking/ani-four-service-deploy-20261012/resource-migrate.json').read_text());job['metadata']['name']='resource-migrate-image-20261013-97b13968';job['metadata']['labels']={'ani.io/deployment-run':'image-update-20261013'};job['spec']['template']['spec']['containers'][0]['image']=im['reference'];save(E/'migration-job.json',job)
 k(['create','-f','-'],json.dumps(job).encode())
 try:k(['-n','ani-system','wait','--for=condition=Complete','job/'+job['metadata']['name'],'--timeout=100s'])
 finally:(E/'migration-job.log').write_bytes(k(['-n','ani-system','logs','job/'+job['metadata']['name']]))
 assert sql('select count(*),max(version) from network_schema_version')=='14|14'
 patch=[{'op':'test','path':'/spec/template/spec/containers/0/image','value':before['spec']['template']['spec']['containers'][0]['image']},{'op':'replace','path':'/spec/template/spec/containers/0/image','value':im['reference']},{'op':'replace','path':'/spec/replicas','value':1}]
 k(['-n','ani-system','patch','deploy','ani-resource-service','--type=json','-p',json.dumps(patch)])
 k(['-n','ani-system','rollout','status','deploy/ani-resource-service','--timeout=180s'],timeout=210)
 after=guard();expected=copy.deepcopy(before['spec']);expected['template']['spec']['containers'][0]['image']=im['reference'];assert after['spec']==expected,'unexpected spec change'
 save(P/'resource-deployment-after.json',after)
 other=json.loads(k(['-n','ani-system','get','deploy','-o','json']));other={x['metadata']['name']:[c['image'] for c in x['spec']['template']['spec']['containers']] for x in other['items'] if x['metadata']['name']!='ani-resource-service'};assert other==json.loads((E/'baseline.json').read_text())['other_deployments']
 pods=json.loads(k(['-n','ani-system','get','pods','-l',selector,'-o','json']));save(E/'resource-pods.json',[{'name':p['metadata']['name'],'node':p['spec']['nodeName'],'containers':p['status']['containerStatuses']} for p in pods['items']])
 (E/'resource-startup.log').write_bytes(k(['-n','ani-system','logs','deploy/ani-resource-service','-c','server']))
 save(E/'deployment-result.json',{'result':'pass','source_sha':im['source_sha'],'reference':im['reference'],'cluster_uid':UID,'schema_before':13,'schema_after':14,'instance_enabled':False,'deployment_spec_only_image_changed':True,'other_deployment_images_unchanged':True,'available_replicas':after['status']['availableReplicas'],'timestamp':datetime.datetime.now(datetime.timezone.utc).isoformat()})
 k(['-n','ani-system','delete','job',job['metadata']['name'],'--wait=true','--timeout=45s'])
 print('schema14 migration and Resource rollout: pass')
if __name__=='__main__':globals()[sys.argv[1]]()
