#!/usr/bin/env python3
"""Start one bounded task-owned PostgreSQL instance on Fedora."""
import json
from pathlib import Path
import secrets
import subprocess
import time

run=Path('/home/chabking/workspace/ani-resource-service-runs/vm-mvp-20261023-01a168ce')
private=run/'private/pg'
private.mkdir(mode=0o700,parents=True,exist_ok=False)
image='docker.io/library/postgres@sha256:4ef4dbc939d61acea57712655ddb4b4ab27419c913f94cca0cd57cb3ea3c2280'
def check(args):return subprocess.check_output(args,text=True).strip()
password=secrets.token_hex(32)
env=private/'postgres.env';env.write_text('POSTGRES_PASSWORD='+password+'\n');env.chmod(0o600)
subprocess.run(['docker','image','inspect',image],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True)
cid=check(['docker','run','-d','--label','ani.vm.fixture=vm-mvp-20261023-01a168ce',
           '--memory=768m','--memory-swap=768m','--cpus=1',
           '--mount','type=tmpfs,destination=/var/lib/postgresql',
           '--env-file',str(env),'-p','127.0.0.1::5432',image])
(private/'container-id').write_text(cid)
ready=False
for i in range(45):
    if subprocess.run(['docker','exec',cid,'pg_isready','-h','127.0.0.1','-U','postgres'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0:
        ready=True;break
    time.sleep(1)
if not ready:raise SystemExit('PG fixture not ready; inspect task-owned container')
port=check(['docker','port',cid,'5432/tcp']).split(':')[-1]
dsn=private/'admin.dsn';dsn.write_text(f'postgres://postgres:{password}@127.0.0.1:{port}/postgres?sslmode=disable');dsn.chmod(0o600)
summary={'container_id':cid,'image':image,'port':int(port),'host':'127.0.0.1','dsn_reference':str(dsn),'label':'ani.vm.fixture=vm-mvp-20261023-01a168ce'}
(run/'evidence/pg-fixture.json').write_text(json.dumps(summary,indent=2)+'\n')
print(json.dumps(summary))
