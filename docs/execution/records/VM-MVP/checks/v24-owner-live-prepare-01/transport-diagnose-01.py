from pathlib import Path
import datetime as dt,fcntl,json,os,re,selectors,signal,socket,ssl,subprocess,time
R=Path('/home/chabking/workspace/ani-resource-service-runs/vm-mvp-20261023-01a168ce');D=R/'v24-owner-live-prepare-01';P=R/'private/v24-owner-clients-01';K='/home/chabking/ani-four-service-deploy-20261012/tools/kubectl';KC='/home/chabking/ani-four-service-deploy-20261012/private/kubeconfig';OUT=D/'transport-diagnosed-01.json'
def run(args,timeout=8):return subprocess.run(args,capture_output=True,text=True,timeout=timeout)
def get(kind,name,ns):
 p=run([K,'--kubeconfig='+KC,'get',kind,name,'-n',ns,'-o','json']);assert p.returncode==0;return json.loads(p.stdout)
def ticks(pid):return int(Path('/proc/'+str(pid)+'/stat').read_text().rsplit(')',1)[1].split()[19])
def error(e):
 o={'type':type(e).__name__}
 if isinstance(e,ssl.SSLCertVerificationError):o.update(verify_code=e.verify_code,verify_message=e.verify_message)
 elif isinstance(e,ssl.SSLError):o.update(reason=e.reason,library=e.library)
 elif isinstance(e,OSError):o.update(errno=e.errno,error_name=type(e).__name__)
 return o
def tls(addr,servername,files):
 start=time.monotonic();ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT);ctx.minimum_version=ssl.TLSVersion.TLSv1_3;ctx.load_verify_locations(files[0]);ctx.load_cert_chain(files[1],files[2]);ctx.check_hostname=True;ctx.verify_mode=ssl.CERT_REQUIRED
 try:
  with socket.create_connection(addr,timeout=4) as raw:
   raw.settimeout(4)
   with ctx.wrap_socket(raw,server_hostname=servername) as s:
    cert=s.getpeercert();return {'result':'VERIFIED_TLS_HANDSHAKE','elapsed_seconds':round(time.monotonic()-start,3),'tls_version':s.version(),'server_dns_sans':[v for k,v in cert.get('subjectAltName',[]) if k=='DNS'],'chain_and_hostname_verified':True,'client_keypair_loaded':True,'application_bytes_sent':0}
 except Exception as e:return {'result':'HANDSHAKE_FAILED','elapsed_seconds':round(time.monotonic()-start,3),'error':error(e),'application_bytes_sent':0}
def logs():
 text=(P/'gov-clients.log').read_text(errors='replace');out=[]
 for line in text.splitlines():
  if not re.search(r'error|warn|accelerator/profiles|/api/v1/models|deadline|transport|handshake',line,re.I):continue
  patterns=[r'code = [A-Za-z]+',r'dial tcp [0-9.:]+: [A-Za-z ]+',r'context deadline exceeded',r'i/o timeout',r'connection (?:refused|reset|closed)',r'EOF',r'authentication handshake failed',r'transport: [A-Za-z .:_\-]+',r'tls: [A-Za-z .:_\-]+',r'x509: [A-Za-z0-9 .:_\-]+',r'proxyconnect tcp: [A-Za-z0-9 .:_\-]+',r'code[=: ]+[45][0-9]{2}',r'reason[=: ]+[A-Z_]+']
  found=list(dict.fromkeys(v for p in patterns for v in re.findall(p,line)))
  ts=re.findall(r'(?:2026[-/]10[-/]24[T ]|(?:ts|time)=)[^ ]+',line)
  paths=re.findall(r'/api/v1/(?:accelerator/profiles|models)[^\s\"?]*',line)
  if found or paths:out.append({'time_tokens':ts[:2],'paths':paths,'error_patterns':found})
 return {'source_reference':str(P/'gov-clients.log'),'bytes':len(text.encode()),'sanitized_errors':out[-30:],'window_note':'UTC12:07:24-12:08:01; include log timezone representation where UTC prefix absent'}
def main():
 assert os.environ.get('SSH_CONNECTION','').split()[2:3]==['172.16.101.31'];assert not OUT.exists()
 lock=open('/home/chabking/workspace/ani-network-service-runs/net05a-heavy.lock','a')
 try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 except BlockingIOError:raise SystemExit(73)
 assert get('namespace','kube-system','default')['metadata']['uid']=='5277649d-e28d-4a0a-ae76-8354365c63bc'
 proc=json.loads((R/'private/gov-candidate/process.json').read_text());assert proc['pid']==3424470 and ticks(proc['pid'])==70793927
 env=json.loads((R/'private/v24-instance-01/gov-runtime-env.json').read_text());assert isinstance(env,dict)
 targets=[('ACC','ani-accelerator-service','db4d1538-554b-415a-a4f1-fdc490a86947','10.96.58.69','ani-accelerator-service.ani-accelerator.svc.cluster.local','ANI_ACCELERATOR',43941,False),('MODEL','ani-model-grpc','2f75fd16-c191-4446-ba1a-8d57b189986c','10.96.44.130','ani-model.ani.internal','ANI_MODEL',43942,False),('INFERENCE','ani-inference-grpc','6240c73a-b1a3-470e-8258-162dfc067e8c','10.96.166.234','ani-inference.ani.internal','ANI_INFERENCE',43943,True)]
 result={'state':'RUNNING','started_at':dt.datetime.now(dt.timezone.utc).isoformat(),'logs':logs(),'targets':[],'new_http_or_rpc_requests':0,'configuration_changes':0,'gov_terms':0,'shared_writes':0};forwards=[]
 try:
  for label,name,uid,ip,san,prefix,port,inffiles in targets:
   svc=get('service',name,'ani-system');assert svc['metadata']['uid']==uid and svc['spec']['clusterIP']==ip and not svc['metadata'].get('deletionTimestamp');assert any(p['port']==19090 for p in svc['spec']['ports']);assert env[prefix+'_ADDR']==ip+':19090' and env[prefix+'_SERVER_NAME']==san
   suffixes=['_CA_FILE','_CERT_FILE','_KEY_FILE'] if inffiles else ['_CA','_CERT','_KEY'];files=[env[prefix+x] for x in suffixes];assert all(Path(p).is_file() and str(p).startswith(str(P)) for p in files)
   row={'name':label,'service':{'namespace':'ani-system','name':name,'uid':uid,'cluster_ip':ip,'port':19090},'server_name':san,'direct':tls((ip,19090),san,files)};result['targets'].append(row)
   if row['direct']['result']!='VERIFIED_TLS_HANDSHAKE':
    route=run(['ip','-j','route','get',ip],3);parsed=json.loads(route.stdout) if route.returncode==0 else [];row['route']=[{k:v.get(k) for k in ['dst','gateway','dev','prefsrc','table','type','flags'] if k in v} for v in parsed]
    ss=run(['ss','-H','-ltn','sport','=',':'+str(port)],3);assert ss.returncode==0 and not ss.stdout.strip(),'task loopback port busy'
    log=P/('tls-pf-'+label.lower()+'.log');f=log.open('x');argv=[K,'--kubeconfig='+KC,'-n','ani-system','port-forward','--address=127.0.0.1','service/'+name,str(port)+':19090'];child=subprocess.Popen(argv,stdin=subprocess.DEVNULL,stdout=f,stderr=subprocess.STDOUT,start_new_session=True);identity={'pid':child.pid,'start_ticks':ticks(child.pid),'local_address':'127.0.0.1','local_port':port,'service_uid':uid,'remote_port':19090};forwards.append((child,identity,f));row['port_forward']=dict(identity)
    deadline=time.monotonic()+6;ready=False
    while time.monotonic()<deadline and child.poll() is None:
     if 'Forwarding from 127.0.0.1:'+str(port) in log.read_text():ready=True;break
     time.sleep(.1)
    row['port_forward']['ready']=ready
    if ready:row['forwarded_tls']=tls(('127.0.0.1',port),san,files)
    else:row['forwarded_tls']={'result':'NOT_RUN_FORWARD_NOT_READY'}
    assert get('service',name,'ani-system')['metadata']['uid']==uid
    if child.poll() is None:
     assert ticks(child.pid)==identity['start_ticks'];child.send_signal(signal.SIGTERM)
     try:child.wait(timeout=5)
     except subprocess.TimeoutExpired:row['port_forward']['stop']='TERM_WAIT_TIMEOUT';raise RuntimeError('owned PF did not stop')
    row['port_forward'].update(stop='NORMAL_TERM_GONE',exit_code=child.returncode);f.close()
  result['state']='BOUNDED_TLS_COMPARISON_COMPLETE'
 except Exception as e:result.update(state='STOPPED',failure=error(e));raise
 finally:
  for child,identity,f in forwards:
   if child.poll() is None and ticks(child.pid)==identity['start_ticks']:
    child.send_signal(signal.SIGTERM)
    try:child.wait(timeout=5)
    except subprocess.TimeoutExpired:result['owned_pf_term_timeout']=identity
   if not f.closed:f.close()
  result['finished_at']=dt.datetime.now(dt.timezone.utc).isoformat();OUT.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result),flush=True);fcntl.flock(lock,fcntl.LOCK_UN)
if __name__=='__main__':main()
