import pathlib,subprocess,json,hashlib,os,time,fcntl,shutil
R=pathlib.Path('/home/chabking/workspace/ani-resource-service-runs/network-stack-release-20261023')
BASE='gcr.io/distroless/static-debian12@sha256:52dcfbabb7457ea47c82f6e13af8c8a4a1d9f7b0145142b3ecab20f2b888411d'
os.environ['DOCKER_BUILDKIT']='0';os.umask(0o077);rec={'status':'WAITING_LOCK','stages':[],'images':{},'started':time.time()}
def save():(R/'backend-images.json').write_text(json.dumps(rec,indent=2))
def step(name,args,budget=600):
 print(name,flush=True);s={'name':name,'command':args};rec['stages'].append(s);save()
 with (R/'logs'/(name+'.log')).open('wb') as f:p=subprocess.run(args,stdout=f,stderr=subprocess.STDOUT,timeout=budget)
 s['exit_code']=p.returncode;save()
 if p.returncode:raise RuntimeError(name+' failed')
lock=open('/home/chabking/workspace/ani-network-service-runs/net05a-heavy.lock','a');fcntl.flock(lock,fcntl.LOCK_EX)
try:
 b=json.loads((R/'integrated-gov-receipt.json').read_text());assert b['status']=='PASS'
 rec['status']='RUNNING';save()
 for name,revision,source,binary,dest,cmd in [
  ('res','63e138174831ba7cf32452e6216143a147dadc82',R/'res','res','/app/ani-resource-service',['/app/ani-resource-service']),
  ('gov',b['version'],R/'gov-integrated','gov','/app/bin/server',['/app/bin/server','-c','/app/configs']),
  ('gov-admin',b['version'],R/'gov-integrated','gov-admin','/app/bin/admin',['/app/bin/admin'])]:
  ctx=R/(name+'-image');ctx.mkdir(exist_ok=True);
  if (ctx/'binary').exists():(ctx/'binary').chmod(0o755)
  shutil.copy2(R/'bin'/binary,ctx/'binary');(ctx/'binary').chmod(0o555)
  manifest_sha=b['final_manifest_sha256'] if name!='res' else hashlib.sha256((R/'res-manifest.json').read_bytes()).hexdigest()
  label='org.opencontainers.image.revision="'+revision+'" io.ani.source-manifest-sha256="'+manifest_sha+'"'
  (ctx/'Dockerfile').write_text('FROM '+BASE+'\nWORKDIR /app\nCOPY binary '+dest+'\nLABEL '+label+'\nENTRYPOINT '+json.dumps(cmd if name=='res' else [])+'\nCMD '+json.dumps([] if name=='res' else cmd)+'\n')
  notice=source/('THIRD_PARTY_NOTICES.md' if name!='res' else 'THIRD_PARTY_NOTICES.go-kratos-layout.txt')
  if notice.is_file():shutil.copy2(notice,ctx/'THIRD_PARTY_NOTICES.md');(ctx/'Dockerfile').write_text((ctx/'Dockerfile').read_text()+'COPY THIRD_PARTY_NOTICES.md /app/THIRD_PARTY_NOTICES.md\n')
  tag='172.16.101.10:5000/ani/network-release-'+name+':20261023'
  step(name+'-package',['docker','build','--pull=false','--network=none','-t',tag,str(ctx)])
  step(name+'-push',['skopeo','copy','--dest-tls-verify=true','--dest-authfile','/home/chabking/workspace/ani-resource-service-runs/network-stack-release-20261023/harbor-auth.private.json','--dest-cert-dir','/home/chabking/workspace/ani-resource-service-runs/network-stack-release-20261023/harbor-certs','--digestfile',str(R/(name+'.digest')),'docker-daemon:'+tag,'docker://'+tag.replace('172.16.101.10:5000/ani/','172.16.101.10:30003/icn-01a15337-release/')])
  im=json.loads(subprocess.check_output(['docker','image','inspect',tag]))[0]
  rec['images'][name]={'image':tag.replace('172.16.101.10:5000/ani/','172.16.101.10:30003/icn-01a15337-release/').rsplit(':',1)[0]+'@'+(R/(name+'.digest')).read_text().strip(),'revision':revision,'binary_sha256':hashlib.sha256((R/'bin'/binary).read_bytes()).hexdigest()};save()
 old='ani-governance-atlas:v1.3.0-8bf0f6d19dbd-nonroot';oldid=json.loads(subprocess.check_output(['docker','image','inspect',old]))[0]['Id'];ctx=R/'atlas-image';ctx.mkdir(exist_ok=True);shutil.copytree(R/'gov-integrated/migrations',ctx/'migrations',dirs_exist_ok=True)
 (ctx/'Dockerfile').write_text('FROM '+oldid+'\nCOPY migrations/ /app/migrations/\nUSER 65532:65532\n')
 tag='172.16.101.10:5000/ani/network-release-atlas:20261023';step('atlas-package',['docker','build','--pull=false','--network=none','-t',tag,str(ctx)]);name='atlas';step('atlas-push',['skopeo','copy','--dest-tls-verify=true','--dest-authfile','/home/chabking/workspace/ani-resource-service-runs/network-stack-release-20261023/harbor-auth.private.json','--dest-cert-dir','/home/chabking/workspace/ani-resource-service-runs/network-stack-release-20261023/harbor-certs','--digestfile',str(R/'atlas.digest'),'docker-daemon:'+tag,'docker://'+tag.replace('172.16.101.10:5000/ani/','172.16.101.10:30003/icn-01a15337-release/')]);im=json.loads(subprocess.check_output(['docker','image','inspect',tag]))[0];rec['images']['atlas']={'image':tag.replace('172.16.101.10:5000/ani/','172.16.101.10:30003/icn-01a15337-release/').rsplit(':',1)[0]+'@'+(R/(name+'.digest')).read_text().strip()}
 rec['status']='PASS'
except BaseException as e:rec.update(status='FAIL',error=str(e));raise
finally:rec['finished']=time.time();save();fcntl.flock(lock,fcntl.LOCK_UN)
print(json.dumps(rec['images'],indent=2))
