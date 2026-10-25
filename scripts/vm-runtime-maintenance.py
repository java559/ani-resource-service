#!/usr/bin/env python3
"""Approved, narrowly scoped containerd ownership change, driven from Fedora."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path('/home/chabking/workspace/ani-resource-service-runs/vm-mvp-20261023-01a168ce')
K = ['/home/chabking/ani-four-service-deploy-20261012/tools/kubectl',
     '--kubeconfig=/home/chabking/ani-four-service-deploy-20261012/private/kubeconfig']
EXPECTED_UID = '5277649d-e28d-4a0a-ae76-8354365c63bc'
HASHES = ['dc3a233b4002a4aea7e31eb59b0a93d624a58e21676e9bea582c81f49cf69f93',
          'dc3a233b4002a4aea7e31eb59b0a93d624a58e21676e9bea582c81f49cf69f93',
          'a192311851c04172988c8dc06763b57d3f8a3cb9222065e460b495b9aa04208b']
env = os.environ.copy()
env['SSHPASS'] = (ROOT/'private/node-password').read_text()
def check(args, **kw): return subprocess.check_output(args,text=True,**kw)
uid = check(K + ['get','ns','kube-system','-o','jsonpath={.metadata.uid}'])
if uid != EXPECTED_UID: raise SystemExit('approved cluster identity changed')
script = '''import hashlib,json,os,pathlib,shutil,subprocess,sys,tomllib
p=pathlib.Path('/etc/containerd/config.toml')
b=p.read_bytes(); expected=sys.argv[1]
if hashlib.sha256(b).hexdigest()!=expected: raise SystemExit('source config changed')
assert tomllib.loads(b.decode())['version']==2
marker=b'  [plugins."io.containerd.grpc.v1.cri"]\\n'
assert b.count(marker)==1
assert b'device_ownership_from_security_context' not in b
new=b.replace(marker,marker+b'    device_ownership_from_security_context = true\\n',1)
backup=pathlib.Path('/var/backups/ani-vm-mvp-20261023-01a168ce')
backup.mkdir(mode=0o700,exist_ok=True)
target=backup/'config.toml.before'
if target.exists(): raise SystemExit('backup already exists; inspect before resuming')
shutil.copy2(p,target)
candidate=p.parent/'.vm-mvp-20261023.toml'
candidate.write_bytes(new);os.chmod(candidate,p.stat().st_mode & 0o777)
dump=subprocess.check_output(['containerd','--config',str(candidate),'config','dump'],stderr=subprocess.DEVNULL,text=True)
effective=tomllib.loads(dump)
assert effective['plugins']['io.containerd.cri.v1.runtime']['device_ownership_from_security_context'] is True
os.chown(candidate,p.stat().st_uid,p.stat().st_gid)
os.replace(candidate,p)
try:
 subprocess.run(['systemctl','restart','containerd'],check=True,timeout=90)
 subprocess.run(['systemctl','is-active','--quiet','containerd'],check=True)
 effective=tomllib.loads(subprocess.check_output(['containerd','config','dump'],stderr=subprocess.DEVNULL,text=True))
 assert effective['plugins']['io.containerd.cri.v1.runtime']['device_ownership_from_security_context'] is True
except BaseException:
 shutil.copy2(target,p)
 subprocess.run(['systemctl','restart','containerd'],check=True,timeout=90)
 raise
print(json.dumps({'before_sha256':expected,'after_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'backup':str(target),'effective_device_ownership':True,'service':'active'}))
'''
records=[]
for n in range(3):
    host=f'172.16.101.{10+n}'
    args=['sshpass','-e','ssh','-o','StrictHostKeyChecking=yes',f'ubuntu@{host}',
          'sudo','-n','python3','-',HASHES[n]]
    result=json.loads(check(args,input=script,env=env))
    result.update({'node':f'ani-0{n+1}','ip':host})
    records.append(result)
    (ROOT/'evidence/runtime-maintenance.json').write_text(json.dumps(records,indent=2)+'\n')
    subprocess.run(K+['wait','--for=condition=Ready',f'node/ani-0{n+1}','--timeout=120s'],check=True)
    check(K+['get','--raw=/readyz'])
    print(json.dumps(result),flush=True)
print('runtime configuration and service checks completed; native Block probe remains NOT_RUN')
