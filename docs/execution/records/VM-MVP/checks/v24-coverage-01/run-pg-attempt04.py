"""Approved one-fixture correction; original two isolated databases, service cases only."""
from pathlib import Path
import argparse,datetime,fcntl,hashlib,json,os,signal,subprocess,tarfile,time,urllib.parse
R=Path('/home/chabking/workspace/ani-resource-service-runs/vm-mvp-20261023-01a168ce');D=R/'v24-coverage-01';P=R/'private/v24-pg-01';GO='/home/chabking/go/pkg/mod/golang.org/toolchain@v0.0.1-go1.26.10.linux-amd64/bin/go'
a=argparse.ArgumentParser();a.add_argument('--execute-authorized',action='store_true',required=True);a.add_argument('--plan-sha',required=True);args=a.parse_args();pf=D/'pg-attempt04-prepared.json';assert hashlib.sha256(pf.read_bytes()).hexdigest()==args.plan_sha;plan=json.loads(pf.read_text());assert hashlib.sha256(Path(__file__).read_bytes()).hexdigest()==plan['runner_sha256']
lock=open('/home/chabking/workspace/ani-network-service-runs/net05a-heavy.lock','a')
try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
except BlockingIOError:raise SystemExit(73)
receipt_file=D/'pg-attempt04-executed.json';assert not receipt_file.exists();start=time.monotonic();cid='';phase='guards';receipt={'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'plan_sha256':args.plan_sha,'runner_sha256':plan['runner_sha256'],'attempt':4,'base_commit':plan['source_commit'],'databases':[],'tests':[],'business_api_calls':0,'candidate_or_cluster_changes':False,'new_database_or_role':False}
def psql(user,db,sql):
 p=subprocess.run(['docker','exec','-i',cid,'psql','-X','-A','-t','-v','ON_ERROR_STOP=1','-U',user,'-d',db],input=sql,text=True,capture_output=True,timeout=25)
 if p.returncode:
  f=P/'psql-error-attempt04.log';f.write_text(p.stderr);f.chmod(0o600);raise RuntimeError('psql step failed; private error only')
 return p.stdout.strip()
def manifest(src):
 return {str(p.relative_to(src)):('symlink:'+os.readlink(p) if p.is_symlink() else hashlib.sha256(p.read_bytes()).hexdigest()) for p in src.rglob('*') if p.is_file() or p.is_symlink()}
def digest(rows):return hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def identity(g):
 for name,field,table,col in ((g['database'],'database_oid','pg_database','datname'),(g['owner'],'owner_oid','pg_roles','rolname'),(g['runtime'],'runtime_oid','pg_roles','rolname')):assert psql('postgres','postgres',"SELECT oid FROM "+table+" WHERE "+col+"='"+name+"';")==g[field],'exact fixture OID changed'
 assert psql('postgres','postgres',"SELECT datdba FROM pg_database WHERE datname='"+g['database']+"';")==g['owner_oid'],'exact fixture owner changed'
try:
 priorfile=D/'pg-attempt03-executed.json';assert hashlib.sha256(priorfile.read_bytes()).hexdigest()==plan['attempt03_receipt_sha256'];prior=json.loads(priorfile.read_text());assert prior['databases']==plan['existing_databases']
 data=prior['tests'][0];assert data['group']=='data' and data['exit_code']==0 and all(any(e['Test']==n and e['Action']=='pass' for e in data['named_results']) for n in plan['databases'][0]['tests']) and not any(e['Action'] in ('fail','skip') for e in data['named_results']);receipt['data_pass_reused']={'receipt':str(priorfile),'sha256':plan['attempt03_receipt_sha256'],'named_results':data['named_results']}
 for procpath in Path('/proc').iterdir():
  if not procpath.name.isdigit():continue
  try:
   if procpath.stat().st_uid!=os.getuid():continue
   exe=Path(os.readlink(procpath/'exe')).name;cwd=os.readlink(procpath/'cwd')
   assert not (exe in ('go','compile','link','cgo','asm') and cwd.startswith(str(D/'source'))),'previous source Go process must be gone'
  except (OSError,PermissionError):pass
 fixture=json.loads((R/'evidence/pg-fixture.json').read_text());cid=(R/'private/pg/container-id').read_text().strip();assert fixture['label']=='ani.vm.fixture=vm-mvp-20261023-01a168ce' and cid==fixture['container_id']
 assert subprocess.check_output(['docker','inspect','--format','{{ index .Config.Labels "ani.vm.fixture" }}',cid],text=True).strip()=='vm-mvp-20261023-01a168ce';assert subprocess.check_output(['docker','inspect','--format','{{.State.Running}}',cid],text=True).strip()=='true'
 assert hashlib.sha256((D/'governance.tar').read_bytes()).hexdigest()==plan['archive_sha256'];src=D/'source-attempt04';assert not src.exists();src.mkdir()
 with tarfile.open(D/'governance.tar') as archive:archive.extractall(src,filter='data')
 before=manifest(src);assert len(before)==2452 and digest(before)==plan['expected_source_manifest_sha256'];overlay=plan['overlay'];target=src/overlay['path'];assert before[overlay['path']]==overlay['before_sha256'];inputfile=D/overlay['input_file'];assert hashlib.sha256(inputfile.read_bytes()).hexdigest()==overlay['sha256'];target.write_bytes(inputfile.read_bytes());after=manifest(src);assert {k for k in after if after[k]!=before[k]}=={overlay['path']} and set(before)==set(after);receipt['source']={'snapshot':str(src),'files':len(after),'original_manifest_sha256':digest(before),'manifest_sha256':digest(after),'overlay':overlay}
 tmp=D/'go-tmp';t=plan['existing_build_tmpdir'];st=tmp.stat();assert tmp.is_dir() and st.st_uid==os.getuid() and st.st_ino==t['inode'] and st.st_dev==t['device'] and (st.st_mode&0o777)==0o700;assert P.is_dir() and P.stat().st_uid==os.getuid() and (P.stat().st_mode&0o777)==0o700
 admin=urllib.parse.urlsplit((R/'private/pg/admin.dsn').read_text().strip());assert admin.hostname in ('127.0.0.1','localhost')
 for g in plan['existing_databases']:
  identity(g);db,owner,runtime=(g[k] for k in ('database','owner','runtime'))
  for role in (owner,runtime):
   ref=Path(g['dsn_references'][role]);assert ref.parent==P and ref.stat().st_uid==os.getuid() and (ref.stat().st_mode&0o777)==0o600;dsn=urllib.parse.urlsplit(ref.read_text().strip());assert dsn.username==role and dsn.path=='/'+db and dsn.hostname==admin.hostname and dsn.port==admin.port
  q="SELECT json_build_object('database',current_database(),'user',current_user,'superuser',r.rolsuper,'createdb',r.rolcreatedb,'createrole',r.rolcreaterole,'bypassrls',r.rolbypassrls,'inherit',r.rolinherit,'owner_membership',pg_has_role(current_user,'"+owner+"','MEMBER'),'schema_create',has_schema_privilege(current_user,'public','CREATE'),'temp',has_database_privilege(current_user,current_database(),'TEMP'),'dml',has_table_privilege(current_user,'sys_quota_operations','SELECT,INSERT,UPDATE,DELETE')) FROM pg_roles r WHERE r.rolname=current_user;"
  guards=json.loads(psql(runtime,db,q));assert guards['database']==db and guards['user']==runtime and guards['dml'] and not any(guards[k] for k in ('superuser','createdb','createrole','bypassrls','inherit','owner_membership','schema_create','temp'));row=dict(g);row['runtime_guards']=guards;receipt['databases'].append(row)
 group=plan['databases'][1];g=plan['existing_databases'][1];runtime=g['runtime'];env={k:v for k,v in os.environ.items() if k in ('HOME','USER','LOGNAME','LANG','LC_ALL','TZ')};env.update(PATH=str(Path(GO).parent)+':/usr/bin:/bin',GOTOOLCHAIN='local',GOWORK='off',GOMAXPROCS='2',GOFLAGS='-p=2 -mod=readonly',GOMEMLIMIT='1536MiB',GOMODCACHE=str(R/'formal-provider-02/go-mod'),GOCACHE=str(R/'cache/go-build'),GOPROXY='off',GOTMPDIR=str(tmp),ANI_TEST_DATABASE_DSN=Path(g['dsn_references'][runtime]).read_text(),ANI_TEST_DATABASE_EXCLUSIVE='1')
 logfile=P/'service-attempt04-tests.jsonl';assert not logfile.exists();cmd=[GO,'test','-json','-tags','quota_pg','-count=1','-timeout=180s',group['package'],'-run','^('+'|'.join(group['tests'])+')$'];print('RUN service 2 fixed PostgreSQL cases; reuse original two DB/OIDs',flush=True);phase='service_test'
 with logfile.open('w') as out:
  logfile.chmod(0o600);proc=subprocess.Popen(cmd,cwd=src,env=env,stdout=out,stderr=subprocess.STDOUT,start_new_session=True)
  try:code=proc.wait(timeout=600)
  except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGTERM);proc.wait(timeout=10);receipt['process_timeout_seconds']=600;raise RuntimeError('bounded service compilation/execution timed out')
 events=[]
 for line in logfile.read_text().splitlines():
  try:item=json.loads(line)
  except json.JSONDecodeError:continue
  if item.get('Test') and item.get('Action') in ('pass','fail','skip'):events.append({k:item[k] for k in ('Package','Test','Action','Elapsed') if k in item})
 receipt['tests'].append({'group':'service','command':cmd,'exit_code':code,'private_log_reference':str(logfile),'named_results':events});assert code==0 and all(any(e['Test']==n and e['Action']=='pass' for e in events) for n in group['tests']) and not any(e['Action'] in ('fail','skip') for e in events),'both original service cases must pass without skip';assert manifest(src)==after,'source changed during test';phase='normal_cleanup'
 for g in reversed(receipt['databases']):
  identity(g);db,owner,runtime=(g[k] for k in ('database','owner','runtime'));psql('postgres','postgres',f'DROP DATABASE {db}; DROP ROLE {runtime}; DROP ROLE {owner};');quoted=','.join("'"+v+"'" for v in (db,owner,runtime));assert psql('postgres','postgres','SELECT count(*) FROM pg_database WHERE datname IN ('+quoted+'); SELECT count(*) FROM pg_roles WHERE rolname IN ('+quoted+');').splitlines()==['0','0'];g['normal_cleanup']='DATABASE_AND_ROLES_DROPPED_NO_FORCE_VERIFIED_ABSENT'
 receipt['result']='PASS_SIX_REAL_PG_REGRESSIONS_DATA03_SERVICE04';print('PASS service; both isolated DBs and four roles normal DROP/absence verified',flush=True)
except Exception as e:receipt.update(result='INCOMPLETE_RETAIN_EXCLUSIVE_FIXTURE',error_type=type(e).__name__,error='private logs/fixture retained; no candidate database or service changed')
finally:
 receipt['last_phase']=phase;receipt['seconds']=round(time.monotonic()-start,3);receipt_file.write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps({'result':receipt['result'],'seconds':receipt['seconds'],'receipt':str(receipt_file)}),flush=True);fcntl.flock(lock,fcntl.LOCK_UN)
