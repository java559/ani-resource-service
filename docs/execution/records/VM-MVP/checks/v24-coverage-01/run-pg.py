"""Six fixed PG regressions, two disposable DBs on the existing task PG only."""
from pathlib import Path
import argparse,datetime,fcntl,hashlib,json,os,re,secrets,signal,subprocess,tarfile,time,urllib.parse
R=Path('/home/chabking/workspace/ani-resource-service-runs/vm-mvp-20261023-01a168ce');D=R/'v24-coverage-01';P=R/'private/v24-pg-01';GO='/home/chabking/go/pkg/mod/golang.org/toolchain@v0.0.1-go1.26.10.linux-amd64/bin/go'
a=argparse.ArgumentParser();a.add_argument('--execute-authorized',action='store_true',required=True);a.add_argument('--plan-sha',required=True);args=a.parse_args();planfile=D/'pg-prepared.json';assert hashlib.sha256(planfile.read_bytes()).hexdigest()==args.plan_sha;plan=json.loads(planfile.read_text());assert hashlib.sha256(Path(__file__).read_bytes()).hexdigest()==plan['runner_sha256']
lock=open('/home/chabking/workspace/ani-network-service-runs/net05a-heavy.lock','a')
try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
except BlockingIOError:raise SystemExit(73)
assert not (D/'pg-executed.json').exists() and not P.exists(),'new single-run namespace only'
receipt={'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'plan_sha256':args.plan_sha,'source_commit':plan['source_commit'],'databases':[],'tests':[],'candidate_or_cluster_changes':False,'business_api_calls':0,'new_services_or_containers':0};start=time.monotonic();cid='';private_created=False

def psql(user,db,sql,variables=()):
 cmd=['docker','exec','-i',cid,'psql','-X','-A','-t','-v','ON_ERROR_STOP=1','-U',user,'-d',db]
 for k,v in variables:cmd+=['-v',k+'='+v]
 p=subprocess.run(cmd,input=sql,text=True,capture_output=True,timeout=25)
 if p.returncode:
  if private_created:
   e=P/'psql-error.log';e.write_text(p.stderr);e.chmod(0o600)
  raise RuntimeError('psql step failed; private error only')
 return p.stdout.strip()

try:
 fixture=json.loads((R/'evidence/pg-fixture.json').read_text());assert fixture['label']=='ani.vm.fixture=vm-mvp-20261023-01a168ce';cid=(R/'private/pg/container-id').read_text().strip();assert cid==fixture['container_id']
 assert subprocess.check_output(['docker','inspect','--format','{{ index .Config.Labels "ani.vm.fixture" }}',cid],text=True).strip()=='vm-mvp-20261023-01a168ce'
 assert subprocess.check_output(['docker','inspect','--format','{{.State.Running}}',cid],text=True).strip()=='true'
 assert hashlib.sha256((D/'governance.tar').read_bytes()).hexdigest()==plan['archive_sha256']
 src=D/'source';assert not src.exists();src.mkdir()
 with tarfile.open(D/'governance.tar') as archive:archive.extractall(src,filter='data')
 for row in plan['migration_inputs']:assert hashlib.sha256((src/row['path']).read_bytes()).hexdigest()==row['sha256'],row['path']
 names=[x[k] for x in plan['databases'] for k in ('database','owner','runtime')];assert len(set(names))==6 and all(re.fullmatch(r'vm_v24(?:data|svc)01_(?:owner_|runtime_)?01a168ce',n) for n in names)
 quoted=','.join("'"+n+"'" for n in names)
 assert not psql('postgres','postgres','SELECT datname FROM pg_database WHERE datname IN ('+quoted+'); SELECT rolname FROM pg_roles WHERE rolname IN ('+quoted+');'),'new DB and roles must all be absent'
 P.mkdir(mode=0o700);private_created=True;admin=urllib.parse.urlsplit((R/'private/pg/admin.dsn').read_text().strip());assert admin.hostname in ('127.0.0.1','localhost')
 for group in plan['databases']:
  db,owner,runtime=(group[k] for k in ('database','owner','runtime'));ownerpass=secrets.token_hex(32);runtimepass=secrets.token_hex(32)
  psql('postgres','postgres',f"CREATE ROLE {owner} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS PASSWORD '{ownerpass}'; CREATE ROLE {runtime} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS PASSWORD '{runtimepass}'; CREATE DATABASE {db} OWNER {owner}; REVOKE ALL ON DATABASE {db} FROM PUBLIC; GRANT CONNECT ON DATABASE {db} TO {runtime};")
  refs={}
  for role,password in ((owner,ownerpass),(runtime,runtimepass)):
   ref=P/(role+'.dsn');dsn=urllib.parse.urlunsplit((admin.scheme,f'{role}:{password}@{admin.hostname}:{admin.port}','/'+db,admin.query,''));ref.write_text(dsn);ref.chmod(0o600);refs[role]=str(ref)
  for row in plan['migration_inputs']:
   if row['path']=='scripts/ops/sql/grant-runtime-role.sql':psql(owner,db,(src/row['path']).read_text(),(('migrate_role',owner),('runtime_role',runtime)))
   else:psql(owner,db,(src/row['path']).read_text())
  guards=psql(runtime,db,"SELECT json_build_object('database',current_database(),'user',current_user,'superuser',r.rolsuper,'createdb',r.rolcreatedb,'createrole',r.rolcreaterole,'bypassrls',r.rolbypassrls,'inherit',r.rolinherit,'owner_membership',pg_has_role(current_user,'"+owner+"','MEMBER'),'schema_create',has_schema_privilege(current_user,'public','CREATE'),'temp',has_database_privilege(current_user,current_database(),'TEMP'),'dml',has_table_privilege(current_user,'sys_quota_operations','SELECT,INSERT,UPDATE,DELETE')) FROM pg_roles r WHERE r.rolname=current_user;")
  g=json.loads(guards);assert g['database']==db and g['user']==runtime and g['dml'] and not any(g[k] for k in ('superuser','createdb','createrole','bypassrls','inherit','owner_membership','schema_create','temp'))
  receipt['databases'].append({'database':db,'owner':owner,'runtime':runtime,'dsn_references':refs,'runtime_guards':g,'database_oid':psql('postgres','postgres',"SELECT oid FROM pg_database WHERE datname='"+db+"';"),'owner_oid':psql('postgres','postgres',"SELECT oid FROM pg_roles WHERE rolname='"+owner+"';"),'runtime_oid':psql('postgres','postgres',"SELECT oid FROM pg_roles WHERE rolname='"+runtime+"';")})
  env={k:v for k,v in os.environ.items() if k in ('HOME','USER','LOGNAME','LANG','LC_ALL','TZ')};env.update(PATH=str(Path(GO).parent)+':/usr/bin:/bin',GOTOOLCHAIN='local',GOWORK='off',GOMAXPROCS='2',GOFLAGS='-p=2 -mod=readonly',GOMEMLIMIT='1536MiB',GOMODCACHE=str(R/'formal-provider-02/go-mod'),GOCACHE=str(R/'cache/go-build'),GOPROXY='off',ANI_TEST_DATABASE_DSN=Path(refs[runtime]).read_text(),ANI_TEST_DATABASE_EXCLUSIVE='1')
  logfile=P/(group['group']+'-tests.jsonl');cmd=[GO,'test','-json','-tags','quota_pg','-count=1','-timeout=180s',group['package'],'-run','^('+'|'.join(group['tests'])+')$'];print('RUN',group['group'],len(group['tests']),'fixed PostgreSQL cases',flush=True)
  with logfile.open('w') as out:
   logfile.chmod(0o600);proc=subprocess.Popen(cmd,cwd=src,env=env,stdout=out,stderr=subprocess.STDOUT,start_new_session=True)
   try:code=proc.wait(timeout=210)
   except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGTERM);proc.wait(timeout=10);raise RuntimeError('bounded PG test compilation/execution timed out')
  events=[]
  for line in logfile.read_text().splitlines():
   try:item=json.loads(line)
   except json.JSONDecodeError:continue
   if item.get('Test') and item.get('Action') in ('pass','fail','skip'):events.append({k:item[k] for k in ('Package','Test','Action','Elapsed') if k in item})
  receipt['tests'].append({'group':group['group'],'command':cmd,'exit_code':code,'private_log_reference':str(logfile),'named_results':events});assert code==0 and all(any(e['Test']==t and e['Action']=='pass' for e in events) for t in group['tests']) and not any(e['Action'] in ('skip','fail') for e in events),'all six cases must actually pass without skip'
  print('PASS',group['group'],flush=True)
 for group,created in zip(plan['databases'],receipt['databases']):
  db,owner,runtime=(group[k] for k in ('database','owner','runtime'))
  assert psql('postgres','postgres',"SELECT oid FROM pg_database WHERE datname='"+db+"';")==created['database_oid']
  assert psql('postgres','postgres',"SELECT oid FROM pg_roles WHERE rolname='"+owner+"';")==created['owner_oid'] and psql('postgres','postgres',"SELECT oid FROM pg_roles WHERE rolname='"+runtime+"';")==created['runtime_oid']
  psql('postgres','postgres',f'DROP DATABASE {db}; DROP ROLE {runtime}; DROP ROLE {owner};');created['normal_cleanup']='DATABASE_AND_ROLES_DROPPED_NO_FORCE'
 receipt['result']='PASS_SIX_REAL_PG_REGRESSIONS'
except Exception as e:
 receipt['result']='INCOMPLETE_RETAIN_EXCLUSIVE_FIXTURE';receipt['error_type']=type(e).__name__;receipt['error']='private logs/fixture retained; no candidate database or service changed'
finally:
 receipt['seconds']=round(time.monotonic()-start,3);(D/'pg-executed.json').write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps({'result':receipt['result'],'seconds':receipt['seconds'],'receipt':str(D/'pg-executed.json')}),flush=True);fcntl.flock(lock,fcntl.LOCK_UN)
