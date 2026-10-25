"""Task-only real Governance candidate; run each phase under the Fedora heavy lock."""
from pathlib import Path
import hashlib,json,os,re,secrets,signal,socket,subprocess,sys,time,uuid,urllib.request,urllib.parse,urllib.error

RUN=Path('/home/chabking/workspace/ani-resource-service-runs/vm-mvp-20261023-01a168ce')
SOURCE=RUN/'gov-stage-01'; PRIVATE=RUN/'private/gov-candidate'
LABEL='vm-mvp-20261023-01a168ce'; HTTP='http://127.0.0.1:39180'
ORIGIN='http://127.0.0.1:39188'; CLUSTER='5277649d-e28d-4a0a-ae76-8354365c63bc'
DATABASE='vm_govlive_01a168ce'; OWNER='vm_govlive_owner_01a168ce'; RUNTIME='vm_govlive_runtime_01a168ce'
REDIS_IMAGE='redis@sha256:a9cc41d6d01da2aa26c219e4f99ecbeead955a7b656c1c499cce8922311b2514'
ATLAS_SHA='cfc773e5b4e845bc01d680390c174648938a7f88bf30e5a2c83ae85217c21587'
PRIVATE.mkdir(mode=0o700,parents=True,exist_ok=True); PRIVATE.chmod(0o700)

def save(path,value):
 data=value if isinstance(value,str) else json.dumps(value,indent=2)+'\n'
 # Persist the original admission identity before any HTTP mutation. Atomic
 # replacement prevents a killed driver from leaving a truncated key/body.
 temporary=path.with_name('.'+path.name+'.'+uuid.uuid4().hex+'.tmp')
 descriptor=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 try:
  with os.fdopen(descriptor,'w') as stream:
   stream.write(data);stream.flush();os.fsync(stream.fileno())
  os.replace(temporary,path)
  directory=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
  try:os.fsync(directory)
  finally:os.close(directory)
 finally:
  if temporary.exists():temporary.unlink()
def execute(args,**kwargs):
 result=subprocess.run([str(a) for a in args],stdout=subprocess.PIPE,stderr=subprocess.PIPE,**kwargs)
 if result.returncode:
  # Keep diagnostics private: SQL, configuration and credential-bearing error
  # messages must never be forwarded through the driver stdout.
  log=PRIVATE/('failure-'+str(time.time_ns())+'.log');log.write_bytes(result.stdout+result.stderr);log.chmod(0o600)
  raise RuntimeError('command failed; private diagnostics: '+str(log))
 return result.stdout.decode()
def cid():
 receipt=json.loads((RUN/'evidence/pg-fixture.json').read_text())
 value=(RUN/'private/pg/container-id').read_text().strip()
 assert receipt['container_id']==value and receipt['label']=='ani.vm.fixture='+LABEL
 assert execute(['docker','inspect','--format','{{ index .Config.Labels "ani.vm.fixture" }}',value]).strip()==LABEL
 return value
def psql(user,sql,variables=(),database=DATABASE):
 args=['docker','exec','-i',cid(),'psql','-X','-qAt','-v','ON_ERROR_STOP=1','-U',user,'-d',database]
 for key,value in variables:args+=['-v',key+'='+value]
 return execute(args,input=sql.encode())
def credentials():
 path=PRIVATE/'credentials.json'
 if path.exists():return json.loads(path.read_text())
 value={'platform_username':'vm_platform_01a168ce','platform_password':'Aa1!'+secrets.token_hex(24),'tenant_username':'vm_admin_01a168ce','tenant_password':'Aa1!'+secrets.token_hex(24),'tenant_code':'vm-mvp-01a168ce','plan_name':'VM MVP 01a168ce'}
 save(path,value);save(PRIVATE/'platform-password',value['platform_password']);return value
def download_atlas():
 path=RUN/'task-tools/atlas-v1.3.0';path.parent.mkdir(exist_ok=True)
 if not path.exists():
  temporary=path.with_suffix('.download')
  execute(['curl','--proto','=https','--http1.1','--fail','--location','--retry','0','--connect-timeout','10','--max-time','60','--continue-at','-','--output',temporary,'https://atlasbinaries.com/atlas/atlas-linux-amd64-v1.3.0'])
  assert hashlib.sha256(temporary.read_bytes()).hexdigest()==ATLAS_SHA,'Atlas checksum mismatch'
  temporary.chmod(0o700);temporary.rename(path)
 assert hashlib.sha256(path.read_bytes()).hexdigest()==ATLAS_SHA
 version=execute([path,'version']).splitlines()[0]
 assert re.search(r'\bv1\.3\.0$',version),version
 return path
def redis_fixture():
 if not (PRIVATE/'redis.json').exists():
  password=secrets.token_hex(32)
  config=PRIVATE/'redis.conf';save(config,'bind 0.0.0.0\nprotected-mode yes\nport 6379\nsave ""\nappendonly no\nmaxmemory 96mb\nrequirepass '+password+'\n')
  container=execute(['docker','run','-d','--pull=never','--name','ani-vm-gov-01a168ce-redis','--label','ani.vm.fixture='+LABEL,'--label','ani.vm.component=gov-candidate-redis','--memory','128m','--cpus','0.5','--read-only','--tmpfs','/data:rw,noexec,nosuid,size=16m,mode=1777','--user',f'{os.getuid()}:{os.getgid()}','-p','127.0.0.1::6379','-v',str(config)+':/etc/redis/task.conf:ro,Z',REDIS_IMAGE,'redis-server','/etc/redis/task.conf']).strip()
  address=execute(['docker','port',container,'6379/tcp']).strip();assert address.startswith('127.0.0.1:')
  value={'container_id':container,'address':address,'password':password,'gov_db':0,'sg_db':1,'test_db':15,'image':REDIS_IMAGE}
  value.update({f'url_db{db}':f'redis://:{password}@{address}/{db}' for db in [0,1,15]})
  save(PRIVATE/'redis.json',value)
 print(json.dumps({'phase':'redis','private_reference':str(PRIVATE/'redis.json')}))
def infra():
 atlas=download_atlas();creds=credentials()
 if not (PRIVATE/'database-created').exists():
  exists=psql('postgres',f"SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname='{DATABASE}') OR EXISTS(SELECT 1 FROM pg_roles WHERE rolname IN ('{OWNER}','{RUNTIME}'));",database='postgres').strip()
  assert exists=='f','candidate names already exist without this task receipt; refusing adoption'
  admin=urllib.parse.urlsplit((RUN/'private/pg/admin.dsn').read_text().strip())
  owner_password=secrets.token_hex(32);runtime_password=secrets.token_hex(32)
  for name,user,password in [('owner',OWNER,owner_password),('runtime',RUNTIME,runtime_password)]:
   save(PRIVATE/(name+'.dsn'),urllib.parse.urlunsplit((admin.scheme,f'{user}:{password}@{admin.hostname}:{admin.port}','/'+DATABASE,admin.query,'')))
  psql('postgres',f"CREATE ROLE {OWNER} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS PASSWORD '{owner_password}'; CREATE ROLE {RUNTIME} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS PASSWORD '{runtime_password}'; CREATE DATABASE {DATABASE} OWNER {OWNER}; REVOKE ALL ON DATABASE {DATABASE} FROM PUBLIC; GRANT CONNECT ON DATABASE {DATABASE} TO {RUNTIME};",database='postgres')
  save(PRIVATE/'database-created',json.dumps({'database':DATABASE,'container':cid()}))
 if not (PRIVATE/'migrated').exists():
  environment=dict(os.environ,ANI_DATABASE_DSN=(PRIVATE/'owner.dsn').read_text().strip())
  for command in [('status',),('apply','--dry-run'),('apply',)]:
   output=execute([atlas,'migrate',*command,'--env','governance'],cwd=SOURCE/'app/admin/service',env=environment)
   save(PRIVATE/('atlas-'+command[0]+('-dry' if '--dry-run' in command else '')+'.log'),output)
  # This fresh database has no tenants/accounts before bootstrap. The reviewed
  # catalog data step therefore creates catalog rows without account backfill.
  psql(OWNER,(SOURCE/'sql/data/20260922_quota_catalog_backfill.sql').read_text())
  psql(OWNER,(SOURCE/'scripts/ops/sql/grant-runtime-role.sql').read_text(),(('migrate_role',OWNER),('runtime_role',RUNTIME)))
  save(PRIVATE/'migrated','Atlas v1.3.0\n')
 redis_fixture()
 if not (PRIVATE/'access-key-master.hex').exists():save(PRIVATE/'access-key-master.hex',secrets.token_hex(32))
 if not (PRIVATE/'jwt-private.pem').exists():
  execute(['openssl','genpkey','-algorithm','RSA','-pkeyopt','rsa_keygen_bits:2048','-out',PRIVATE/'jwt-private.pem'])
  (PRIVATE/'jwt-private.pem').chmod(0o600)
  execute(['openssl','pkey','-in',PRIVATE/'jwt-private.pem','-pubout','-out',PRIVATE/'jwt-public.pem'])
  (PRIVATE/'jwt-public.pem').chmod(0o600)
 redis=json.loads((PRIVATE/'redis.json').read_text())
 config={'logger':{'type':'std'},'authn':{'type':'jwt','jwt':{'method':'RS256','access_token_expires':'5400s','refresh_token_expires':'43200s'}},'authz':{'type':'casbin','casbin':{}},'data':{'database':{'driver':'postgres','source':(PRIVATE/'runtime.dsn').read_text().strip(),'migrate':False,'max_idle_connections':2,'max_open_connections':4,'connection_max_lifetime':'60s'},'redis':{'addr':redis['address'],'password':redis['password'],'db':0,'dial_timeout':'5s','read_timeout':'1s','write_timeout':'1s'}},'server':{'rest':{'addr':'127.0.0.1:39180','timeout':'30s','cors':{'allow_credentials':True,'origins':[ORIGIN],'headers':['Content-Type','Authorization','X-Request-ID','X-CSRF-Token'],'methods':['GET','POST','PUT','DELETE','OPTIONS']},'middleware':{'enable_recovery':True,'enable_validate':True,'enable_metadata':True}}}}
 (PRIVATE/'config').mkdir(mode=0o700,exist_ok=True);save(PRIVATE/'config/candidate.yaml',json.dumps(config,indent=2))
 print(json.dumps({'phase':'infra','database':DATABASE,'private_reference':str(PRIVATE),'redis_private_reference':str(PRIVATE/'redis.json')}))
def bootstrap():
 creds=credentials();environment=dict(os.environ,ANI_DATABASE_DSN=(PRIVATE/'owner.dsn').read_text().strip())
 admin=RUN/('gov-admin-guest' if (RUN/'gov-admin-guest').exists() else 'gov-admin')
 for label,args in [('init',['init','--username',creds['platform_username'],'--password-file',PRIVATE/'platform-password']),('check',['check']),('sync-preview',['sync-apis','--dry-run']),('sync',['sync-apis'])]:
  save(PRIVATE/('admin-'+label+'.log'),execute([admin,*args],env=environment))
 save(PRIVATE/'bootstrapped','real admin CLI completed\n');print(json.dumps({'phase':'bootstrap','private_reference':str(PRIVATE)}))
def start():
 assert (PRIVATE/'bootstrapped').exists()
 if (PRIVATE/'process.json').exists():raise RuntimeError('candidate process receipt exists; inspect before restarting')
 probe=socket.socket();probe.bind(('127.0.0.1',39180));probe.close()
 pki=RUN/'private/pki'
 for name in ['ca.crt','gov-resource.crt','gov-resource.key','gov-session.crt','gov-session.key']:assert (pki/name).is_file()
 environment={k:v for k,v in os.environ.items() if not k.startswith(('ANI_','GWA_AUTH_','GOWIND_'))}
 environment.update(dict(GOMAXPROCS='2',GOMEMLIMIT='512MiB',ANI_ACCESS_KEY_ENCRYPTION_KEY_FILE=str(PRIVATE/'access-key-master.hex'),GWA_AUTH_JWT_PRIVATE_KEY=(PRIVATE/'jwt-private.pem').read_text(),GWA_AUTH_JWT_PUBLIC_KEY=(PRIVATE/'jwt-public.pem').read_text(),GWA_AUTH_CONSOLE_ORIGINS=ORIGIN,GWA_AUTH_BOSS_ORIGINS=ORIGIN,GWA_AUTH_REFRESH_COOKIE_INSECURE='true',ANI_INSTANCE_ADDR='127.0.0.1:39181',ANI_INSTANCE_CA=str(pki/'ca.crt'),ANI_INSTANCE_CERT=str(pki/'gov-resource.crt'),ANI_INSTANCE_KEY=str(pki/'gov-resource.key'),ANI_INSTANCE_CLUSTER_ID=CLUSTER,ANI_VM_V1_WRITES='true',ANI_NETWORK_ADDR='127.0.0.1:39181',ANI_NETWORK_CA=str(pki/'ca.crt'),ANI_NETWORK_CERT=str(pki/'gov-resource.crt'),ANI_NETWORK_KEY=str(pki/'gov-resource.key'),ANI_INSTANCE_SG_ADDR='127.0.0.1:39182',ANI_INSTANCE_SG_SERVER_NAME='ani-session-gateway',ANI_INSTANCE_SG_CA_FILE=str(pki/'ca.crt'),ANI_INSTANCE_SG_CERT_FILE=str(pki/'gov-session.crt'),ANI_INSTANCE_SG_KEY_FILE=str(pki/'gov-session.key')))
 log=(PRIVATE/'server.log').open('ab');os.chmod(PRIVATE/'server.log',0o600)
 binary=RUN/('gov-server-guest' if (RUN/'gov-server-guest').exists() else 'gov-server')
 process=subprocess.Popen([str(binary),'-c',str(PRIVATE/'config')],cwd=SOURCE,env=environment,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
 save(PRIVATE/'process.json',{'pid':process.pid,'binary':str(binary),'binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),'config_reference':str(PRIVATE/'config'),'http':HTTP})
 for _ in range(60):
  if process.poll() is not None:raise RuntimeError('candidate exited; inspect private server.log')
  try:
   with socket.create_connection(('127.0.0.1',39180),timeout=1):break
  except OSError:time.sleep(0.5)
 else:raise RuntimeError('candidate listener did not start')
 print(json.dumps({'phase':'started','pid':process.pid,'http':HTTP,'private_reference':str(PRIVATE)}))
def upgrade():
 assert (RUN/'gov-server-guest').is_file()
 receipt=json.loads((PRIVATE/'process.json').read_text());pid=receipt['pid']
 args=(Path('/proc')/str(pid)/'cmdline').read_bytes().split(b'\0')
 assert args[0].decode()==receipt['binary'] and str(PRIVATE/'config').encode() in args,'candidate process identity changed'
 os.kill(pid,signal.SIGTERM)
 for _ in range(100):
  if not (Path('/proc')/str(pid)).exists():break
  time.sleep(0.1)
 else:raise RuntimeError('task candidate did not exit after TERM; inspect before continuing')
 (PRIVATE/'process.json').rename(PRIVATE/'process-before-guest.json')
 start()
EVENTS=[]
class ProductAPIError(RuntimeError):
 def __init__(self,method,path,status,reason):
  self.status=status;self.reason=reason
  super().__init__(f'product API {method} {path} failed: HTTP {status} reason={reason}')
def api(method,path,body=None,token=None):
 headers={'Content-Type':'application/json','Origin':ORIGIN}
 if token:headers['Authorization']='Bearer '+token
 request=urllib.request.Request(HTTP+path,data=None if body is None else json.dumps(body).encode(),headers=headers,method=method)
 try:
  with urllib.request.urlopen(request,timeout=35) as response:
   EVENTS.append({'method':method,'path':path,'status':response.status});raw=response.read();value=json.loads(raw) if raw else {}
   return value.get('data',value) if isinstance(value,dict) else value
 except urllib.error.HTTPError as error:
  try:reason=json.loads(error.read()).get('reason','unknown')
  except Exception:reason='unknown'
  raise ProductAPIError(method,path,error.code,reason)
def prepare_catalog(token):
 # Use the product PermissionService under the bootstrapped platform authority.
 # The failed default full-catalog align remains untouched; only reviewed task
 # domain routes receive explicit permissions and no existing grants are removed.
 specs=[]
 for filename,module,business in [('scripts/ops/sql/register-vm-permissions.sql','VMService','VIRTUAL_MACHINE'),('sql/data/20261009_network_permissions.sql',None,'NETWORK')]:
  text=(SOURCE/filename).read_text()
  for method,path,code in re.findall(r"\('([A-Z]+)','([^']+)','([^']+)'\)",text):
   if code.startswith(('vm:','network:vpc:','network:subnet:')) or code=='network:operation:get':specs.append((method,path,code,module,business))
 text=(SOURCE/'scripts/ops/sql/register-compute-permissions.sql').read_text()
 for method,path,action,rpc in re.findall(r"\('([A-Z]+)','([^']+)','([^']+)','([^']+)'\)",text):specs.append((method,path,'compute:preset:admin:'+action,'ComputePresetService','SYSTEM'))
 specs.append(('GET','/api/v1/compute-presets','vm:compute-preset:read','ComputePresetService','SYSTEM'))
 apis=api('GET','/api/v1/admin/apis?noPaging=true',token=token).get('items',[])
 permissions=api('GET','/api/v1/admin/permissions?noPaging=true',token=token).get('items',[])
 wanted={}
 for method,path,code,module,business in specs:
  matches=[a for a in apis if a.get('path')==path and a.get('method')==method]
  if not matches and method=='GET' and module=='VMService':
   alias=path.replace('{vm_id}','{vmId}').replace('{operation_id}','{operationId}').replace('{snapshot_id}','{snapshotId}')
   matches=[a for a in apis if a.get('path')==alias and a.get('method')==method and a.get('module')=='VMService' and a.get('businessModule')=='VIRTUAL_MACHINE']
   assert len(matches)==1,'VM runtime route missing/ambiguous: '+method+' '+path
   api('PUT','/api/v1/admin/apis/'+str(matches[0]['id']),{'data':{'path':path},'updateMask':'path'},token)
   matches[0]['path']=path
  assert len(matches)==1,'catalog route missing/ambiguous: '+method+' '+path
  route=matches[0]
  assert route.get('businessModule')==business and (module is None or route.get('module')==module),'unexpected route owner: '+path
  assert route.get('status')=='ON','inactive route: '+path
  aid=int(route['id']);wanted.setdefault(code,set()).add(aid)
  if code!='vm:compute-preset:read':
   frozen="('"+method+"','"+path+"')" in (SOURCE/'sql/bootstrap/001_initial.sql').read_text()
   assert not any(aid in [int(v) for v in p.get('apiIds',[])] and p.get('code')!=code and not (frozen and p.get('code')=='sys:platform_admin') for p in permissions),'foreign route grant: '+path
 for code,ids in wanted.items():
  matches=[p for p in permissions if p.get('code')==code];assert len(matches)<=1
  if not matches:api('POST','/api/v1/admin/permissions',{'data':{'name':code,'code':code,'status':'ON','apiIds':sorted(ids)}},token)
  else:
   existing=matches[0];union=ids|{int(v) for v in existing.get('apiIds',[])}
   if existing.get('status')!='ON' or union!={int(v) for v in existing.get('apiIds',[])}:api('PUT','/api/v1/admin/permissions/'+str(existing['id']),{'data':{'status':'ON','apiIds':sorted(union)},'updateMask':'status,apiIds'},token)
 save(PRIVATE/'catalog-prepared.json',{'codes':sorted(wanted),'routes':len(specs),'events':EVENTS})
def seed_api():
 creds=credentials();platform=api('POST','/api/v1/auth/platform/password/login',{'username':creds['platform_username'],'password':creds['platform_password']})
 token=platform['access_token'];save(PRIVATE/'platform-token.json',platform)
 api('GET','/api/v1/admin/me',token=token)
 prepare_catalog(token)
 plans=api('GET','/api/v1/admin/plans?noPaging=true',token=token)['items']
 matching=[p for p in plans if p.get('name')==creds['plan_name']]
 if not matching:
  api('POST','/api/v1/admin/plans',{'data':{'name':creds['plan_name'],'version':'FREE','expiryPolicy':'READONLY','description':'Task VM acceptance only'}},token)
  matching=[p for p in api('GET','/api/v1/admin/plans?noPaging=true',token=token)['items'] if p.get('name')==creds['plan_name']]
 assert len(matching)==1;plan=int(matching[0]['id'])
 present=api('GET','/api/v1/admin/plan-modules?noPaging=true',token=token).get('items',[])
 for module in ['DASHBOARD','OPM','NETWORK','VIRTUAL_MACHINE']:
  if not any(int(p.get('planId',0))==plan and p.get('module')==module for p in present):api('POST','/api/v1/admin/plan-modules',{'data':{'planId':plan,'module':module}},token)
 tenants=api('GET','/api/v1/admin/tenants?noPaging=true',token=token).get('items',[])
 matching=[t for t in tenants if t.get('code')==creds['tenant_code']]
 if not matching:
  api('POST','/api/v1/admin/tenants:with-admin',{'tenant':{'name':creds['tenant_code'],'code':creds['tenant_code'],'planId':plan,'type':'TRIAL','status':'ON','auditStatus':'APPROVED'},'user':{'username':creds['tenant_username'],'realname':'VM task administrator','status':'NORMAL'},'password':creds['tenant_password'],'activationMode':'IMMEDIATE'},token)
  matching=[t for t in api('GET','/api/v1/admin/tenants?noPaging=true',token=token).get('items',[]) if t.get('code')==creds['tenant_code']]
 assert len(matching)==1;tenant=int(matching[0]['id'])
 roles=[r for r in api('GET','/api/v1/admin/roles?noPaging=true',token=token).get('items',[]) if int(r.get('tenantId',0))==tenant]
 assert len(roles)==1;role_id=int(roles[0]['id'])
 role=api('GET',f'/api/v1/admin/roles/{role_id}',token=token)
 permissions=api('GET','/api/v1/admin/permissions?noPaging=true',token=token).get('items',[])
 platform_roles=[r for r in api('GET','/api/v1/admin/roles?noPaging=true',token=token).get('items',[]) if r.get('code')=='platform:admin' and int(r.get('tenantId',0))==0]
 assert len(platform_roles)==1
 platform_role_id=int(platform_roles[0]['id']);platform_role=api('GET',f'/api/v1/admin/roles/{platform_role_id}',token=token)
 platform_permissions={int(p) for p in platform_role.get('permissions',[])}
 platform_permissions.update(int(p['id']) for p in permissions if p.get('code','').startswith('compute:preset:admin:'))
 api('PUT',f'/api/v1/admin/roles/{platform_role_id}',{'data':{'tenantId':0,'permissions':sorted(platform_permissions)},'updateMask':'permissions'},token)
 selected={int(p['id']) for p in permissions if p.get('code','').startswith(('vm:','network:vpc:','network:subnet:')) or p.get('code')=='network:operation:get'}
 assert any(p.get('code')=='vm:create' for p in permissions)
 selected.update(int(p) for p in role.get('permissions',[]))
 api('PUT',f'/api/v1/admin/roles/{role_id}',{'data':{'tenantId':tenant,'permissions':sorted(selected)},'updateMask':'permissions'},token)
 login=api('POST','/api/v1/auth/password/login',{'tenant_name':creds['tenant_code'],'username':creds['tenant_username'],'password':creds['tenant_password']})
 save(PRIVATE/'tenant-token.json',login)
 api('GET','/api/v1/admin/me',token=login['access_token'])
 mapping=json.loads(psql(OWNER,f"SELECT json_build_object('tenant_id',id,'resource_tenant_id',resource_tenant_id,'tenant_code',code) FROM sys_tenants WHERE id={tenant};").strip())
 mapping.update({'plan_id':plan,'role_id':role_id,'http':HTTP,'origin':ORIGIN,'private_credentials_reference':str(PRIVATE/'credentials.json'),'tenant_token_reference':str(PRIVATE/'tenant-token.json'),'redis_private_reference':str(PRIVATE/'redis.json'),'events':EVENTS})
 save(PRIVATE/'seed-receipt.json',mapping)
 public=dict(mapping);public.pop('tenant_token_reference');public.pop('private_credentials_reference')
 (RUN/'evidence/gov-candidate.json').write_text(json.dumps(public,indent=2)+'\n')
 print(json.dumps({'phase':'product-api-seeded','tenant_id':tenant,'tenant_code':mapping['tenant_code'],'resource_tenant_id':mapping['resource_tenant_id'],'private_reference':str(PRIVATE/'seed-receipt.json')}))
def resources():
 creds=credentials();seed=json.loads((PRIVATE/'seed-receipt.json').read_text())
 tenant=api('POST','/api/v1/auth/password/login',{'tenant_name':creds['tenant_code'],'username':creds['tenant_username'],'password':creds['tenant_password']})['access_token']
 platform=api('POST','/api/v1/auth/platform/password/login',{'username':creds['platform_username'],'password':creds['platform_password']})['access_token']
 private=PRIVATE/'resource-inputs.json'
 state=json.loads(private.read_text()) if private.exists() else {'vpc_key':str(uuid.uuid4()),'subnet_key':str(uuid.uuid4())}
 save(private,state)
 if 'compute_preset_id' not in state:
  presets=api('GET','/api/v1/admin/compute-presets?code=vm-mvp-01a168ce',token=platform).get('items',[])
  if presets:preset=presets[0]
  else:preset=api('POST','/api/v1/admin/compute-presets',{'data':{'code':'vm-mvp-01a168ce','name':'VM MVP 1500m 2048MiB','description':'Task VM acceptance','compute_type':'cpu','cpu_millis':'1500','memory_mib':'2048','enabled':True}},platform)
  state['compute_preset_id']=preset['id'];save(private,state)
 if 'vpc_id' not in state:
  vpc=api('POST','/api/v1/networks/vpcs',{'name':'vm-mvp-main','cidr':'10.83.0.0/16','description':'Task VM main chain','idempotency_key':state['vpc_key']},tenant)
  state['vpc_id']=vpc['id'];save(private,state)
 def ready(path):
  last={}
  for _ in range(40):
   last=api('GET',path,token=tenant)
   if last.get('state','').upper() in ['READY','RESOURCE_STATE_READY','AVAILABLE']:return last
   if last.get('state','').upper() in ['FAILED','ERROR','RESOURCE_STATE_FAILED']:break
   time.sleep(0.5)
  raise RuntimeError('task network pending: state='+last.get('state','')+' reason='+last.get('reason',''))
 ready('/api/v1/networks/vpcs/'+state['vpc_id'])
 if 'subnet_id' not in state:
  subnet=api('POST','/api/v1/networks/subnets',{'name':'vm-mvp-main','vpc_id':state['vpc_id'],'cidr':'10.83.1.0/24','gateway':'10.83.1.1','description':'Task VM main chain','idempotency_key':state['subnet_key']},tenant)
  state['subnet_id']=subnet['id'];save(private,state)
 ready('/api/v1/networks/subnets/'+state['subnet_id'])
 receipt={key:seed[key] for key in ['resource_tenant_id','tenant_code','tenant_id']}
 receipt.update({'namespace':None,'namespace_uid':None,'kcn_subnet_ref':None,'vpc_id':state['vpc_id'],'subnet_id':state['subnet_id'],'compute_preset_id':state['compute_preset_id'],'events':EVENTS})
 # Native placement/UID/ref remain unknown until the Resource owner verifies
 # its observed objects; do not invent provider identity from public IDs.
 (RUN/'evidence/gov-candidate-tenant.json').write_text(json.dumps(receipt,indent=2)+'\n')
 print(json.dumps(receipt))
def prepare_linux(generation=3):
 assert generation==3
 suffix='-v'+str(generation);path=PRIVATE/('linux-vm'+suffix+'.json')
 previous=json.loads((PRIVATE/'linux-vm-v2.json').read_text())
 if path.exists():state=json.loads(path.read_text())
 else:
  secretpath=PRIVATE/('linux-guest-credentials'+suffix+'.json');assert not secretpath.exists(),'new credential name already exists without its intent'
  save(secretpath,{'username':'aniuser','password':'Vm1!'+secrets.token_hex(20)})
  state={'idempotency_key':str(uuid.uuid4()),'media_key':previous['media_key'],'preset_id':previous['preset_id'],'vpc_id':previous['vpc_id'],'subnet_id':previous['subnet_id'],'name':'vm-mvp-linux-main'+suffix,'credential_reference':str(secretpath),'credential_file_sha256':hashlib.sha256(secretpath.read_bytes()).hexdigest(),'requires_directory_validation':True}
  state['request_body_without_password']={'data':{'name':state['name'],'presetId':state['preset_id'],'sourceKind':'DISK_IMAGE','mediaKey':state['media_key'],'systemDiskSizeGb':'12','idempotencyKey':state['idempotency_key'],'startAfterCreate':True,'network':{'vpcId':state['vpc_id'],'subnetId':state['subnet_id']},'guestInitialization':{'username':'aniuser'}}}
  save(path,state)
 assert 'operation_id' not in state and state['idempotency_key']!=previous['idempotency_key'] and state['credential_reference']!=previous['credential_reference']
 receipt={'phase':'linux-prepare-v3','post_executed':False,'name':state['name'],'media_key':state['media_key'],'compute_preset_id':state['preset_id'],'vpc_id':state['vpc_id'],'subnet_id':state['subnet_id'],'system_disk_size_gb':12,'credential_private_reference':state['credential_reference'],'private_intent_reference':str(path),'directory_validation_required_before_first_post':state['requires_directory_validation'],'v1_v2_records_preserved':True}
 save(RUN/'evidence/gov-linux-prepared-v3.json',receipt);print(json.dumps(receipt))
def linux_create(generation=1):
 assert generation in [1,2,3]
 suffix="" if generation==1 else '-v'+str(generation)
 assert (RUN/'gov-server-guest').is_file(),'guest-initialization candidate has not been built'
 process=json.loads((PRIVATE/'process.json').read_text())
 assert process['binary']==str(RUN/'gov-server-guest'),'candidate must be upgraded before initialization'
 creds=credentials();tenant=api('POST','/api/v1/auth/password/login',{'tenant_name':creds['tenant_code'],'username':creds['tenant_username'],'password':creds['tenant_password']})['access_token']
 statepath=PRIVATE/('linux-vm'+suffix+'.json')
 if statepath.exists():state=json.loads(statepath.read_text())
 else:
  # Only a NEW intent resolves product directories. A frozen-key replay does
  # not silently rebind to a later catalog, source, network or guest password.
  placement=json.loads((RUN/'evidence/gov-candidate-tenant.json').read_text())
  presets=api('GET','/api/v1/compute-presets?usage=VIRTUAL_MACHINE',token=tenant).get('items',[])
  selected=[p for p in presets if p.get('id')==placement['compute_preset_id']]
  assert len(selected)==1 and selected[0].get('selectable') is True,'product VM compute preset not selectable'
  media=api('GET','/api/v1/virtual-machine-media',token=tenant).get('items',[])
  verified=[m for m in media if m.get('mediaKey')=='ubuntu-24.04' and m.get('kind')=='DISK_IMAGE' and m.get('osFamily')=='linux' and m.get('initializationCapability')=='CLOUD_INIT_NOCLOUD' and m.get('initializationVerified') is True]
  assert len(verified)==1,'one explicitly verified Linux NoCloud product source required'
  secretpath=PRIVATE/('linux-guest-credentials'+suffix+'.json')
  assert not secretpath.exists(),'new intent credential name already exists without its state'
  save(secretpath,{'username':'aniuser','password':'Vm1!'+secrets.token_hex(20)})
  state={'idempotency_key':str(uuid.uuid4()),'media_key':verified[0]['mediaKey'],'preset_id':placement['compute_preset_id'],'vpc_id':placement['vpc_id'],'subnet_id':placement['subnet_id'],'name':'vm-mvp-linux-main'+suffix,'credential_reference':str(secretpath),'credential_file_sha256':hashlib.sha256(secretpath.read_bytes()).hexdigest()}
  save(statepath,state)
 if 'operation_id' not in state and state.get('requires_directory_validation',False):
  presets=api('GET','/api/v1/compute-presets?usage=VIRTUAL_MACHINE',token=tenant).get('items',[])
  assert sum(p.get('id')==state['preset_id'] and p.get('selectable') is True for p in presets)==1,'prepared VM preset is no longer selectable'
  media=api('GET','/api/v1/virtual-machine-media',token=tenant).get('items',[])
  assert sum(m.get('mediaKey')==state['media_key'] and m.get('kind')=='DISK_IMAGE' and m.get('osFamily')=='linux' and m.get('initializationCapability')=='CLOUD_INIT_NOCLOUD' and m.get('initializationVerified') is True for m in media)==1,'prepared source is not verified Linux NoCloud'
  state['requires_directory_validation']=False;save(statepath,state)
 secretpath=Path(state['credential_reference'])
 if 'operation_id' not in state:
  guest=json.loads(secretpath.read_text())
  if 'credential_file_sha256' in state:assert hashlib.sha256(secretpath.read_bytes()).hexdigest()==state['credential_file_sha256'],'frozen create credential file changed'
  if 'request_body_without_password' not in state:
   state['request_body_without_password']={'data':{'name':state['name'],'presetId':state['preset_id'],'sourceKind':'DISK_IMAGE','mediaKey':state['media_key'],'systemDiskSizeGb':'12','idempotencyKey':state['idempotency_key'],'startAfterCreate':True,'network':{'vpcId':state['vpc_id'],'subnetId':state['subnet_id']},'guestInitialization':{'username':guest['username']}}}
   save(statepath,state)
  body=json.loads(json.dumps(state['request_body_without_password']));body['data']['guestInitialization']['password']=guest['password']
  accepted=api('POST','/api/v1/virtual-machines',body,tenant)
  state['operation_id']=accepted['operationId'];state['vm_id']=accepted['resourceId'];save(statepath,state)
  replay=api('POST','/api/v1/virtual-machines',body,tenant)
  assert replay.get('replayed') is True and replay['operationId']==state['operation_id'] and replay['resourceId']==state['vm_id'],'original VM create replay changed'
 operation=api('GET','/api/v1/virtual-machines/'+state['vm_id']+'/operations/'+state['operation_id'],token=tenant)
 current=api('GET','/api/v1/virtual-machines/'+state['vm_id'],token=tenant)
 public={'vm_id':state['vm_id'],'operation_id':state['operation_id'],'media_key':state['media_key'],'operation':operation,'current':current,'events':EVENTS,'credential_private_reference':str(secretpath)}
 (RUN/('evidence/gov-linux-product'+suffix+'.json')).write_text(json.dumps(public,indent=2)+'\n');print(json.dumps(public))
def denied_api(method,path,body,token):
 headers={'Content-Type':'application/json','Origin':ORIGIN,'Authorization':'Bearer '+token}
 request=urllib.request.Request(HTTP+path,data=None if body is None else json.dumps(body).encode(),headers=headers,method=method)
 try:
  with urllib.request.urlopen(request,timeout=35) as response:
   response.read();raise AssertionError('cross-tenant request unexpectedly accepted: '+method+' '+path+' status='+str(response.status))
 except urllib.error.HTTPError as error:
  try:reason=json.loads(error.read()).get('reason','unknown')
  except Exception:reason='unknown'
  assert error.code in [403,404],'unexpected denial status: '+str(error.code)+' reason='+reason
  result={'method':method,'path':path,'status':error.code,'reason':reason};EVENTS.append(result);return result

def isolation():
 main=credentials();platform=api('POST','/api/v1/auth/platform/password/login',{'username':main['platform_username'],'password':main['platform_password']})['access_token']
 seed=json.loads((PRIVATE/'seed-receipt.json').read_text());vmstate=json.loads((PRIVATE/'linux-vm.json').read_text())
 secrets_path=PRIVATE/'isolation-credentials.json'
 if not secrets_path.exists():save(secrets_path,{'tenant_code':'vm-isolation-01a168ce','username':'vm_isolation_01a168ce','password':'Vm1!'+secrets.token_hex(24)})
 other=json.loads(secrets_path.read_text())
 if 'idempotency_keys' not in other:
  other['idempotency_keys']={suffix:str(uuid.uuid4()) for suffix in ['start','console-sessions']};save(secrets_path,other)
 matches=[t for t in api('GET','/api/v1/admin/tenants?noPaging=true',token=platform).get('items',[]) if t.get('code')==other['tenant_code']]
 if not matches:
  api('POST','/api/v1/admin/tenants:with-admin',{'tenant':{'name':other['tenant_code'],'code':other['tenant_code'],'planId':seed['plan_id'],'type':'TRIAL','status':'ON','auditStatus':'APPROVED'},'user':{'username':other['username'],'realname':'VM isolation task administrator','status':'NORMAL'},'password':other['password'],'activationMode':'IMMEDIATE'},platform)
  matches=[t for t in api('GET','/api/v1/admin/tenants?noPaging=true',token=platform).get('items',[]) if t.get('code')==other['tenant_code']]
 assert len(matches)==1;tenant_id=int(matches[0]['id'])
 roles=[r for r in api('GET','/api/v1/admin/roles?noPaging=true',token=platform).get('items',[]) if int(r.get('tenantId',0))==tenant_id];assert len(roles)==1
 role_id=int(roles[0]['id']);role=api('GET','/api/v1/admin/roles/'+str(role_id),token=platform)
 permissions=api('GET','/api/v1/admin/permissions?noPaging=true',token=platform).get('items',[])
 selected={int(p['id']) for p in permissions if p.get('code','').startswith(('vm:','network:vpc:','network:subnet:')) or p.get('code')=='network:operation:get'}
 selected.update(int(v) for v in role.get('permissions',[]))
 api('PUT','/api/v1/admin/roles/'+str(role_id),{'data':{'tenantId':tenant_id,'permissions':sorted(selected)},'updateMask':'permissions'},platform)
 token=api('POST','/api/v1/auth/password/login',{'tenant_name':other['tenant_code'],'username':other['username'],'password':other['password']})['access_token']
 listed=api('GET','/api/v1/virtual-machines',token=token).get('items',[])
 assert all(v.get('id')!=vmstate['vm_id'] for v in listed),'foreign VM leaked through list'
 denied=[];base='/api/v1/virtual-machines/'+vmstate['vm_id']
 denied.append(denied_api('GET',base,None,token))
 denied.append(denied_api('GET',base+'/operations/'+vmstate['operation_id'],None,token))
 for suffix in ['start','console-sessions']:
  denied.append(denied_api('POST',base+'/'+suffix,{'data':{'idempotencyKey':other['idempotency_keys'][suffix],'expectedVersion':'1'}},token))
 placement=json.loads((RUN/'evidence/gov-candidate-tenant.json').read_text())
 for kind in ['vpcs','subnets']:
  denied.append(denied_api('GET','/api/v1/networks/'+kind+'/'+placement['vpc_id' if kind=='vpcs' else 'subnet_id'],None,token))
 mapping=json.loads(psql(OWNER,f"SELECT json_build_object('tenant_id',id,'resource_tenant_id',resource_tenant_id,'tenant_code',code) FROM sys_tenants WHERE id={tenant_id};").strip())
 result={'principal':mapping,'foreign_vm_id':vmstate['vm_id'],'foreign_vm_absent_from_list':True,'denied':denied,'events':EVENTS,'private_credentials_reference':str(secrets_path)}
 (RUN/'evidence/gov-isolation-product.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
def credential_audit():
 guest=json.loads((PRIVATE/'linux-guest-credentials.json').read_text());state=json.loads((PRIVATE/'linux-vm.json').read_text())
 operation=state['operation_id'];assert str(uuid.UUID(operation))==operation
 stored=json.loads(psql(OWNER,"SELECT json_build_object('canonical_request',canonical_request,'ack_json',ack_json,'dispatch_state',dispatch_state) FROM sys_quota_operations WHERE operation_id='"+operation+"';").strip())
 canonical=json.loads(stored['canonical_request'])
 assert guest['password'] not in json.dumps(stored),'plaintext found in durable operation'
 assert canonical.get('guest_credentials_ciphertext','').startswith('vm-guest:v1:'),'encrypted private envelope absent'
 assert 'password' not in canonical['create_spec']['intent'].get('guest_initialization',{}),'ordinary CreateSpec contains password'
 logs=[PRIVATE/'server.log',RUN/'private/resource-candidate/server.log']
 for path in logs:
  assert path.is_file() and guest['password'].encode() not in path.read_bytes(),'guest password present in candidate log'
 bodies=psql(OWNER,"SELECT COALESCE(request_body,'') FROM sys_api_audit_logs WHERE api_operation='/admin.service.v1.VMService/CreateVM';")
 assert guest['password'] not in bodies,'guest password present in API audit body'
 seed=json.loads((PRIVATE/'seed-receipt.json').read_text());tenant_id=int(seed['tenant_id'])
 counts=json.loads(psql(OWNER,"SELECT json_build_object('vm_charge_count',(SELECT count(*) FROM sys_quota_charges WHERE operation_id='"+operation+"'),'tenant_account_count',(SELECT count(*) FROM sys_quota_accounts WHERE tenant_id="+str(tenant_id)+"));").strip())
 assert counts['vm_charge_count']==0,'VM was incorrectly charged'
 result={'quota':counts,'operation_id':operation,'dispatch_state':stored['dispatch_state'],'canonical_envelope_encrypted':True,'ordinary_spec_password_absent':True,'candidate_log_password_absent':True,'api_audit_password_absent':True if bodies.splitlines() else None,'api_audit_records':len(bodies.splitlines())}
 (RUN/'evidence/gov-credential-audit.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
def prepare_lifecycle(generation=2):
 assert generation in [2,3]
 suffix='-v'+str(generation)
 vmstate=json.loads((PRIVATE/('linux-vm'+suffix+'.json')).read_text());v1=json.loads((PRIVATE/'linux-vm.json').read_text())
 seed=json.loads((PRIVATE/'seed-receipt.json').read_text())
 assert vmstate['vm_id']!=v1['vm_id'] and vmstate['operation_id']!=v1['operation_id'] and vmstate['idempotency_key']!=v1['idempotency_key']
 assert vmstate['credential_reference']!=v1['credential_reference'] and Path(vmstate['credential_reference']).is_file()
 path=PRIVATE/('linux-lifecycle'+suffix+'.json')
 if path.exists():state=json.loads(path.read_text())
 else:
  state={'vm_id':vmstate['vm_id'],'create_operation_id':vmstate['operation_id'],'resource_tenant_id':seed['resource_tenant_id'],'credential_reference':vmstate['credential_reference'],'commands':{}}
  save(path,state)
 assert state['vm_id']==vmstate['vm_id'] and state['create_operation_id']==vmstate['operation_id'] and not state['commands'],'preparation must not overwrite any issued lifecycle command'
 receipt={'phase':'lifecycle-prepare'+suffix,'vm_id':state['vm_id'],'create_operation_id':state['create_operation_id'],'private_command_reference':str(path),'credential_private_reference':vmstate['credential_reference'],'power_commands_executed':False,'command_count':0,'expected_version_policy':'Read current version only at each first action; persist independent key/version/body before the first POST','v1_records_preserved':True}
 save(RUN/('evidence/gov-linux-lifecycle-prepared'+suffix+'.json'),receipt);print(json.dumps(receipt))
def lifecycle(action,generation=1,intent_label=None):
 assert generation in [1,2,3]
 suffix="" if generation==1 else '-v'+str(generation)
 assert action in ['stop','start','restart','force-stop','delete']
 if intent_label is not None:assert re.fullmatch(r'[a-z0-9][a-z0-9-]{0,79}',intent_label)
 command_name=intent_label or action
 creds=credentials();seed=json.loads((PRIVATE/'seed-receipt.json').read_text());vmstate=json.loads((PRIVATE/('linux-vm'+suffix+'.json')).read_text())
 token=api('POST','/api/v1/auth/password/login',{'tenant_name':creds['tenant_code'],'username':creds['tenant_username'],'password':creds['tenant_password']})['access_token']
 path=PRIVATE/('linux-lifecycle'+suffix+'.json')
 state=json.loads(path.read_text()) if path.exists() else {'vm_id':vmstate['vm_id'],'create_operation_id':vmstate['operation_id'],'resource_tenant_id':seed['resource_tenant_id'],'commands':{}}
 assert state['vm_id']==vmstate['vm_id'] and state['create_operation_id']==vmstate['operation_id']
 base='/api/v1/virtual-machines/'+state['vm_id']
 command=state['commands'].get(command_name)
 if command is None:
  current=api('GET',base,token=token)
  version=int(current['version']);assert version>0 and not current.get('closing',False)
  expected='Stopped' if action=='start' else 'Running'
  if action!='delete':assert current.get('observedState','').lower()==expected.lower(),'power precondition not observed: '+current.get('observedState','')
  key=str(uuid.uuid4());body={'data':{'idempotencyKey':key,'expectedVersion':str(version)}}
  if action=='force-stop':body['data']['destructiveConfirmation']=True
  command={'action':action,'idempotency_key':key,'expected_version':version,'body':body,'before':current,'send_attempts':0}
  if intent_label is not None:command['intent_label']=intent_label
  state['commands'][command_name]=command;save(path,state)
 assert command['action']==action,'an original intent cannot change its action'
 if 'operation_id' not in command:
  # If Governance's first ACK is lost, replay EXACTLY the same admission key,
  # first version and body. Governance's pure LookupCommandReplay and owner
  # unique key return the original operation; this is not native power retry.
  command['send_attempts']+=1;command['ack_unknown']=True;save(path,state)
  method='DELETE' if action=='delete' else 'POST';url=base if action=='delete' else base+'/'+action
  try:accepted=api(method,url,command['body'],token)
  except Exception:
   save(path,state);raise
  operation=accepted['operationId'] if action=='delete' else accepted['id']
  assert str(uuid.UUID(operation))==operation
  command['operation_id']=operation;command['ack_unknown']=False;command['accepted']=accepted;save(path,state)
 # Once operation identity is known, all subsequent invocations are reads.
 operation=api('GET',base+'/operations/'+command['operation_id'],token=token)
 try:current=api('GET',base,token=token)
 except ProductAPIError as error:
  if action!='delete' or error.status!=404:raise
  current={'query_status':404,'reason':error.reason}
 command['last_operation']=operation;command['last_current']=current;save(path,state)
 result={'vm_id':state['vm_id'],'create_operation_id':state['create_operation_id'],'action':action,'operation_id':command['operation_id'],'expected_version':command['expected_version'],'admission_send_attempts':command['send_attempts'],'operation':operation,'current':current,'events':EVENTS,'private_command_reference':str(path)}
 if intent_label is not None:result['intent_label']=intent_label
 if action=='delete':
  deleteid=command['operation_id'];assert str(uuid.UUID(deleteid))==deleteid
  record=json.loads(psql(OWNER,"SELECT json_build_object('operation_id',operation_id,'create_operation_id',create_operation_id,'resource_id',resource_id,'resource_tenant_id',resource_tenant_id,'action',action,'canonical_request',canonical_request) FROM sys_quota_operations WHERE tenant_id="+str(int(seed['tenant_id']))+" AND operation_id='"+deleteid+"';").strip())
  assert record['create_operation_id']==state['create_operation_id'] and record['resource_id']==state['vm_id'] and record['resource_tenant_id']==state['resource_tenant_id'] and record['action']=='/vm.v1.VMService/AcceptDelete','DELETE changed the original CREATE association'
  frozen=json.loads(record['canonical_request']);assert frozen['delete_expected_version']==command['expected_version'],'DELETE overwrote its first expected version'
  placement=json.loads((RUN/'evidence/gov-candidate-tenant.json').read_text())
  result['delete_association_verified']=True
  result['cleanup']={'resource_tenant_id':state['resource_tenant_id'],'namespace':placement['namespace'],'namespace_uid':placement['namespace_uid'],'vm_id':state['vm_id'],'create_operation_id':state['create_operation_id'],'delete_operation_id':deleteid,'system_and_data_disks':command['before'].get('disks',[]),'business_delete_succeeded':operation.get('state','').lower()=='succeeded','native_uid_guarded_absence':'PENDING_RESOURCE_OWNER_VERIFICATION','vpc_id':placement['vpc_id'],'subnet_id':placement['subnet_id'],'compute_preset_id':placement['compute_preset_id']}
  result['cleanup']['frozen_network']=frozen['create_spec']['network']
  result['cleanup']['frozen_namespace_uid']=frozen['create_spec']['namespace_uid']
  storage_receipt=RUN/('evidence/product-linux-v'+str(generation)+'-storage-cleanup.json')
  if storage_receipt.is_file():
   result['cleanup']['storage_cleanup_evidence']=str(storage_receipt)
   independent=json.loads(storage_receipt.read_text())
   checks=independent.get('checks',{})
   if independent.get('vm_id')==state['vm_id'] and independent.get('result')=='PASS' and checks.get('original_native_uids_absent') is True:
    result['cleanup']['native_uid_guarded_absence']='PASS_INDEPENDENT_UID_MEASUREMENT'
    result['cleanup']['same_attachment_released']=checks.get('same_attachment_released') is True
  output='gov-linux-delete-cleanup'+suffix+('-'+intent_label if intent_label else '')+'.json'
 else:output='gov-linux-power-'+action+suffix+('-'+intent_label if intent_label else '')+'.json'
 (RUN/'evidence'/output).write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
if __name__=='__main__':
 actions={'redis':redis_fixture,'infra':infra,'bootstrap':bootstrap,'start':start,'seed-api':seed_api,'resources':resources,'upgrade':upgrade,'linux-create':linux_create,'isolation':isolation,'credential-audit':credential_audit,'power-stop':lambda:lifecycle('stop'),'power-start':lambda:lifecycle('start'),'power-restart':lambda:lifecycle('restart'),'power-force-stop':lambda:lifecycle('force-stop'),'product-delete':lambda:lifecycle('delete'),'linux-create-v2':lambda:linux_create(2),'lifecycle-prepare-v2':prepare_lifecycle,'power-stop-v2':lambda:lifecycle('stop',2),'power-start-v2':lambda:lifecycle('start',2),'power-restart-v2':lambda:lifecycle('restart',2),'power-force-stop-v2':lambda:lifecycle('force-stop',2),'product-delete-v2':lambda:lifecycle('delete',2),'linux-prepare-v3':prepare_linux,'linux-create-v3':lambda:linux_create(3),'lifecycle-prepare-v3':lambda:prepare_lifecycle(3),'power-stop-v3':lambda:lifecycle('stop',3),'power-start-v3':lambda:lifecycle('start',3),'power-restart-v3':lambda:lifecycle('restart',3),'power-force-stop-v3':lambda:lifecycle('force-stop',3),'product-delete-v3':lambda:lifecycle('delete',3)}
 assert len(sys.argv)==2 and sys.argv[1] in actions
 actions[sys.argv[1]]()
