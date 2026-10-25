"""One released two-Guest private HTTP proof; exact cleanup and no API mutations."""
from pathlib import Path
import argparse
import base64
import datetime
import fcntl
import hashlib
import ipaddress
import json
import os
import re
import secrets
import shlex
import stat
import subprocess
import time
import uuid
import zlib

R = Path('/home/chabking/workspace/ani-resource-service-runs/vm-mvp-20261023-01a168ce')
D = R / 'v10-network-positive-02'
TASK = 'vm-mvp-v10-positive-02-01a168ce'
TENANT = 'b3c946b5-7eb3-4feb-9f82-79712062ca5c'
NS = 'tenant-' + TENANT
NSUID = '09e58ecb-fe20-454f-bc7b-2da3c406f0ac'
CLUSTER = '5277649d-e28d-4a0a-ae76-8354365c63bc'
CREDENTIAL = R / 'private/gov-candidate/linux-guest-credentials-v3.json'
VIRTCTL = R / 'tools/virtctl-v1.9.0-linux-amd64'
VIRTCTL_SHA = '40ede2ee37c98a1aeed71c9c219616a05247ce2be109e1edddf0477572e8b978'
KUBECTL = '/home/chabking/ani-four-service-deploy-20261012/tools/kubectl'
KUBECONFIG = '/home/chabking/ani-four-service-deploy-20261012/private/kubeconfig'
LOCK = '/home/chabking/workspace/ani-network-service-runs/net05a-heavy.lock'
NETWORK = ipaddress.ip_network('10.83.1.0/24')
PORT = 39194
SERVER = r'''import http.server,json,os,pathlib,signal,socketserver,sys,time
signal.signal(signal.SIGALRM,lambda *args:sys.exit(0));signal.alarm(180)
c=json.loads(pathlib.Path(sys.argv[1]).read_text())
class H(http.server.BaseHTTPRequestHandler):
 def setup(self):super().setup();self.request.settimeout(3)
 def do_GET(self):
  body=c['marker'].encode() if self.path==c['path'] else b'not found\n'
  self.send_response(200 if self.path==c['path'] else 404);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
 def log_message(self,*args):pass
class S(http.server.HTTPServer):
 def server_bind(self):
  socketserver.TCPServer.server_bind(self);self.server_name=self.server_address[0];self.server_port=self.server_address[1]
s=S((c['ip'],c['port']),H);s.timeout=.5;end=time.monotonic()+180
fd=os.open(pathlib.Path(sys.argv[1]).parent/'ready.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
with os.fdopen(fd,'w') as f:json.dump({'pid':os.getpid(),'ip':c['ip'],'port':c['port']},f);f.flush();os.fsync(f.fileno())
try:
 while time.monotonic()<end:s.handle_request()
finally:s.server_close()
'''


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def get(kind, name, namespace=None):
    args = [KUBECTL, '--kubeconfig', KUBECONFIG, '--request-timeout=5s', 'get', kind, name]
    if namespace:
        args += ['-n', namespace]
    reply = subprocess.run(args + ['-o', 'json'], capture_output=True, text=True, timeout=10)
    require(reply.returncode == 0 and len(reply.stdout.encode()) < 2097152, 'bounded exact native GET failed')
    return json.loads(reply.stdout)


def owned(child, kind, parent):
    return any(x.get('kind') == kind and x.get('uid') == parent['metadata']['uid'] and
               x.get('name') == parent['metadata']['name'] and x.get('controller') is True
               for x in child['metadata'].get('ownerReferences', []))


def fresh(row):
    vm = get('virtualmachines.kubevirt.io', row['vm']['name'], NS)
    vmi = get('virtualmachineinstances.kubevirt.io', row['vmi']['name'], NS)
    pod = get('pod', row['launcher']['name'], NS)
    require(all(x['metadata']['uid'] == row[key]['uid'] and x['metadata'].get('namespace') == NS and
                not x['metadata'].get('deletionTimestamp') for key, x in [('vm', vm), ('vmi', vmi), ('launcher', pod)]), 'exact VM/VMI/launcher UID changed')
    require(vm['metadata']['labels'].get('vm.ani.io/vm-id') == row['vm_id'] and
            vm['metadata']['labels'].get('ani.io/tenant-id') == TENANT and
            vm['spec']['template']['spec']['domain']['firmware']['uuid'] == row['vm_id'] and
            vmi['spec']['domain']['firmware']['uuid'] == row['vm_id'], 'native tenant/firmware binding differs')
    require(owned(vmi, 'VirtualMachine', vm) and owned(pod, 'VirtualMachineInstance', vmi) and
            pod['metadata']['labels'].get('kubevirt.io/created-by') == row['vmi']['uid'], 'current launcher owner chain differs')
    require(vmi['status']['phase'] == pod['status']['phase'] == 'Running' and
            vmi['status']['nodeName'] == pod['spec']['nodeName'] == row['vmi']['node'] == row['launcher']['node'], 'native running/node binding differs')
    interfaces = [x for x in vmi['status'].get('interfaces', []) if x.get('name') == 'default' and x.get('ipAddress')]
    require(len(interfaces) == 1 and interfaces[0]['ipAddress'] == row['vmi']['ip'], 'frozen primary Guest address changed')


class Console:
    def __init__(self, row, stage):
        self.stage = stage
        self.row = row;self.child = None;self.ready = False;self.logged_in = False
        self.logout_confirmed = False;self.login_attempts = 0;self.logout_error_class = None;self.prompt_diagnostics = []
        self.inherited_sudo_password_sends = 0

    def login(self, credential):
        import pexpect
        self.shell = re.escape(credential['username']) + r'@[A-Za-z0-9_.-]+(?:\x1b\[[0-?]*[ -/]*[@-~])*:[^\r\n]*\$ '
        self.login_prompt = r'[A-Za-z0-9_.-]+(?:\x1b\[[0-?]*[ -/]*[@-~])* login: ?'
        self.stage('connect-serial')
        self.child = pexpect.spawn(str(VIRTCTL), ['console', self.row['vm']['name'], '--namespace', NS,
            '--kubeconfig', KUBECONFIG, '--timeout=20'], encoding='utf-8', echo=False, timeout=90)
        c = self.child;c.logfile = c.logfile_read = c.logfile_send = None
        c.expect(r'Successfully connected to .* console\.', timeout=20);self.stage('await-initial-user-shell-or-getty');c.sendline('')
        initial = c.expect([self.shell, self.login_prompt, r'ANI_V10_SUDO: '], timeout=90)
        require(initial != 2, 'stale fixed sudo prompt: stop without password; Root must resolve original pending process')
        if initial == 0:
            self.stage('close-inherited-original-user-shell')
            c.sendline('exit');c.expect(self.login_prompt, timeout=20)
            self.stage('inherited-session-getty-confirmed')
        self.login_attempts = 1
        self.stage('normal-login-password-prompt')
        c.sendline(credential['username']);c.expect(r'[Pp]assword:', timeout=20);c.sendline(credential['password'])
        self.stage('normal-login-user-shell')
        require(c.expect([self.shell, r'Login incorrect'], timeout=90) == 0, 'single normal Guest login failed')
        self.ready = self.logged_in = True

    def run(self, code):
        require(self.child is not None and self.ready, 'original authenticated serial shell required')
        envelope = "import json,traceback\ntry:\n exec(" + repr(code) + ",globals())\n reply={'ok':True,'value':result}\nexcept Exception as error:reply={'ok':False,'error_class':type(error).__name__,'message':str(error)[:300]}\nprint('ANI_V10_REPLY='+json.dumps(reply))\n"
        encoded = base64.b64encode(zlib.compress(envelope.encode())).decode();c = self.child
        self.ready = False;c.sendline("_ani_v10_payload=''; printf 'ANI_V10_CHUNK=0:0\\n'")
        c.expect(r'(?:^|[\r\n])ANI_V10_CHUNK=0:0\r?\n', timeout=5);c.expect(self.shell, timeout=5)
        self.ready = True
        for index, offset in enumerate(range(0, len(encoded), 240), 1):
            chunk = encoded[offset:offset + 240];length = offset + len(chunk)
            line = '_ani_v10_payload="${_ani_v10_payload}"' + shlex.quote(chunk) + "; printf 'ANI_V10_CHUNK=" + str(index) + ":%s\\n' \"${#_ani_v10_payload}\""
            require(len(line.encode()) < 380, 'short serial chunk required')
            self.ready = False;c.sendline(line)
            c.expect(r'(?:^|[\r\n])ANI_V10_CHUNK=' + str(index) + ':' + str(length) + r'\r?\n', timeout=5)
            c.expect(self.shell, timeout=5);self.ready = True
        command = 'python3 -c ' + shlex.quote('import base64,zlib,sys;exec(zlib.decompress(base64.b64decode(sys.argv[1])))') + ' "$_ani_v10_payload"'
        require(len(command.encode()) < 250, 'short final command required')
        self.ready = False;c.sendline(command)
        c.expect(r'(?:^|[\r\n])ANI_V10_REPLY=(\{[^\r\n]+\})\r?\n', timeout=25)
        reply = json.loads(c.match.group(1));c.expect(self.shell, timeout=15);self.ready = True
        return reply

    def classify_prompt(self, stage):
        # Only structural flags/counts leave the TTY; never serialize text or hashes.
        raw = self.child.before if self.child is not None and isinstance(self.child.before, str) else ''
        csi = r'\x1b\[[0-?]*[ -/]*[@-~]'
        osc = r'\x1b\][^\x07]*(?:\x07|\x1b\\)'
        clean = re.sub(osc, '', re.sub(csi, '', raw))
        self.prompt_diagnostics.append({'stage': stage, 'ansi_csi_count': len(re.findall(csi, raw)),
            'ansi_osc_count': len(re.findall(osc, raw)),
            'login_suffix': bool(re.search(r'login:\s*\Z', clean)),
            'finite_hostname_before_login': bool(re.search(r'[A-Za-z0-9_.-]+ login:\s*\Z', clean)),
            'password_prompt_suffix': bool(re.search(r'[Pp]assword:\s*\Z', clean)),
            'fixed_sudo_prompt_suffix': bool(re.search(r'ANI_V10_SUDO:\s*\Z', clean)),
            'user_shell_suffix': bool(re.search(self.shell, clean)),
            'generic_dollar_suffix': bool(re.search(r'\$\s*\Z', clean)),
            'raw_text_or_hash_exported': False})

    def close(self):
        if self.child is not None:
            if self.ready:
                try:
                    self.stage('normal-logout')
                    self.child.sendline('unset _ani_v10_payload; exit')
                    try:self.child.expect(self.login_prompt, timeout=8)
                    except Exception:
                        self.child.sendline('');self.child.expect(self.login_prompt, timeout=8)
                    self.logout_confirmed = True
                except Exception as error:
                    self.logout_error_class = type(error).__name__;self.classify_prompt('normal-logout')
            self.child.close(force=True)  # Only our Fedora virtctl; no Guest Ctrl-C or signal.


def identity_code(row, username):
    return r'''import json,pathlib,os,pwd,subprocess
r=lambda p:pathlib.Path(p).read_text().strip()
assert os.getuid()==1000 and pwd.getpwuid(os.getuid()).pw_name==USERNAME
value={'instance_id':r('/var/lib/cloud/data/instance-id'),'datasource':r('/var/lib/cloud/instance/datasource'),'boot_id':r('/proc/sys/kernel/random/boot_id')}
assert value['instance_id']==VMID and value['datasource'].startswith('DataSourceNoCloud')
addresses=json.loads(subprocess.check_output(['ip','-j','address'],text=True,timeout=5))
assert sum(1 for interface in addresses for address in interface.get('addr_info',[]) if address.get('family')=='inet' and address.get('local')==IP and address.get('prefixlen')==24)==1
value.update(uid=os.getuid(),username_matches_credential=True,address=IP,guest_dmi_readback=False);result=value
'''.replace('VMID', repr(row['vm_id'])).replace('IP', repr(row['vmi']['ip'])).replace('USERNAME', repr(username))


def start_code(cfg):
    return 'CONFIG=' + repr(cfg) + '\nSERVER=' + repr(SERVER) + '\n' + r'''import hashlib,json,os,pathlib,subprocess,sys,time,urllib.request
assert os.getuid()==1000
c=CONFIG;p=pathlib.Path(c['directory']);p.mkdir(mode=0o700)
assert p.parent==pathlib.Path('/tmp') and not p.is_symlink()
for name,data in [('server.py',SERVER),('config.json',json.dumps(c))]:
 fd=os.open(p/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 with os.fdopen(fd,'w') as f:f.write(data)
args=[sys.executable,str(p/'server.py'),str(p/'config.json')]
server=subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
info={'pid':server.pid,'argv':args,'start_ticks':pathlib.Path('/proc/'+str(server.pid)+'/stat').read_text().rsplit(') ',1)[1].split()[19],'token':c['token'],'directory':str(p)}
fd=os.open(p/'process.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
with os.fdopen(fd,'w') as f:json.dump(info,f)
began=time.monotonic();deadline=began+10
while not (p/'ready.json').exists() and server.poll() is None and time.monotonic()<deadline:time.sleep(.05)
assert server.poll() is None and (p/'ready.json').is_file(), 'temporary server did not acknowledge successful bind'
assert json.loads((p/'ready.json').read_text())=={'pid':server.pid,'ip':c['ip'],'port':c['port']}
ready_seconds=time.monotonic()-began
result={'uid':os.getuid(),'pid':server.pid,'start_ticks':info['start_ticks'],'bind_acknowledged':True,'server_reverse_dns_disabled':True,'readiness_seconds':ready_seconds,'self_status':None,'self_marker_equal':False,'expected_body_sha256':hashlib.sha256(c['marker'].encode()).hexdigest(),'actual_body_sha256':None}
try:
 response=urllib.request.build_opener(urllib.request.ProxyHandler({})).open('http://'+c['ip']+':'+str(c['port'])+c['path'],timeout=5)
 with response:
  body=response.read(2048);result.update(self_status=response.status,self_marker_equal=body==c['marker'].encode(),actual_body_sha256=hashlib.sha256(body).hexdigest())
except Exception as e:result.update(error_class=type(e).__name__,message=str(e)[:300])
'''


def request_code(peer, expected_boot):
    return 'PEER=' + repr(peer) + '\nEXPECTED_BOOT=' + repr(expected_boot) + '\n' + r'''import hashlib,os,pathlib,urllib.request
assert os.getuid()==1000
result={'uid':os.getuid(),'status':None,'marker_equal':False,'boot_equal':pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()==EXPECTED_BOOT,'expected_body_sha256':hashlib.sha256(PEER['marker'].encode()).hexdigest(),'actual_body_sha256':None}
try:
 response=urllib.request.build_opener(urllib.request.ProxyHandler({})).open('http://'+PEER['ip']+':'+str(PEER['port'])+PEER['path'],timeout=8)
 with response:
  body=response.read(2048);result.update(status=response.status,marker_equal=body==PEER['marker'].encode(),actual_body_sha256=hashlib.sha256(body).hexdigest())
except Exception as e:result.update(error_class=type(e).__name__,message=str(e)[:300])
'''


def cleanup_code(cfg):
    return 'CONFIG=' + repr(cfg) + '\n' + r'''import json,os,pathlib,signal,stat,sys,time
assert os.getuid()==1000
c=CONFIG;p=pathlib.Path(c['directory']);result={'process_gone':False,'directory_gone':False,'term_sent':False}
if not p.exists():result.update(process_gone=True,directory_gone=True)
else:
 m=p.lstat();assert p.parent==pathlib.Path('/tmp') and stat.S_ISDIR(m.st_mode) and m.st_uid==1000 and stat.S_IMODE(m.st_mode)==0o700 and not p.is_symlink()
 assert {x.name for x in p.iterdir()} <= {'server.py','config.json','process.json','ready.json'}
 for f in p.iterdir():
  m=f.lstat();assert stat.S_ISREG(m.st_mode) and m.st_uid==1000 and stat.S_IMODE(m.st_mode)==0o600
 stored=json.loads((p/'config.json').read_text());assert stored==c
 info=json.loads((p/'process.json').read_text());assert info['token']==c['token'] and info['directory']==str(p)
 assert info['argv']==[sys.executable,str(p/'server.py'),str(p/'config.json')]
 proc=pathlib.Path('/proc')/str(info['pid'])
 if proc.exists():
  fields=proc.joinpath('stat').read_text().rsplit(') ',1)[1].split();assert fields[19]==info['start_ticks']
  if fields[0]!='Z':
   assert proc.joinpath('cmdline').read_bytes().rstrip(b'\0').split(b'\0')==[x.encode() for x in info['argv']]
   os.kill(info['pid'],signal.SIGTERM);result['term_sent']=True
  end=time.monotonic()+3
  while proc.exists() and time.monotonic()<end and proc.joinpath('stat').read_text().rsplit(') ',1)[1].split()[0]!='Z':time.sleep(.1)
 assert not proc.exists() or proc.joinpath('stat').read_text().rsplit(') ',1)[1].split()[0]=='Z'
 result['process_gone']=True
 for name in ['ready.json','process.json','config.json','server.py']:
  if (p/name).exists():(p/name).unlink()
 p.rmdir();result['directory_gone']=not p.exists()
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute-authorized', action='store_true', required=True)
    parser.add_argument('--runner-sha256', required=True)
    parser.add_argument('--inventory', type=Path, required=True)
    parser.add_argument('--inventory-sha256', required=True)
    parser.add_argument('--root-release', type=Path, required=True)
    parser.add_argument('--root-release-sha256', required=True)
    args = parser.parse_args()
    require(os.uname().nodename == 'fedora' and Path(__file__).resolve().parent == D and sha(__file__) == args.runner_sha256, 'fixed Fedora source required')
    require(sha(args.inventory) == args.inventory_sha256 and args.inventory.resolve() == D / 'native-inventory-placed.json', 'fixed new-pair inventory required')
    inventory = json.loads(args.inventory.read_text())
    require(inventory['task_name'] == TASK and inventory['cluster_uid'] == CLUSTER and inventory['resource_tenant_id'] == TENANT and
            inventory['namespace'] == {'name': NS, 'uid': NSUID} and set(inventory['vms']) == {'a', 'b'}, 'own Task tenant/namespace and exact new pair required')
    rows = inventory['vms']
    require(rows['a']['vm_id'] != rows['b']['vm_id'] and rows['a']['vmi']['node'] != rows['b']['vmi']['node'] and
            rows['a']['vmi']['ip'] != rows['b']['vmi']['ip'], 'two distinct VMs, nodes and private addresses required before Guest calls')
    for row in rows.values():
        require(str(uuid.UUID(row['vm_id'])) == row['vm_id'] and row['vm_id'] not in {'068d56dd-d4fc-4ca8-8430-16c36bad0761', 'fc154879-8a40-4b18-967c-215f5cf5992d'} and
                row['vm']['name'] == row['vmi']['name'] == 'vm-' + row['vm_id'] and row['vm']['namespace'] == row['vmi']['namespace'] == row['launcher']['namespace'] == NS and
                row['firmware'] == 'bios' and ipaddress.ip_address(row['vmi']['ip']) in NETWORK, 'only new exact Linux/private-network bindings allowed')
        aliases = {'native_vm_uid': row['vm']['uid'], 'vmi_uid': row['vmi']['uid'], 'namespace': NS, 'namespace_uid': NSUID, 'node': row['vmi']['node'], 'current_ip': row['vmi']['ip']}
        require(all(row.get(k, v) == v for k, v in aliases.items()), 'structured/flat inventory aliases differ')
    require(sha(args.root_release) == args.root_release_sha256, 'fixed Root release required')
    release = json.loads(args.root_release.read_text())
    require(release.get('authorized_phase') == 'guest-http-main' and release.get('task_name') == TASK and
            release.get('inventory_sha256') == args.inventory_sha256 and release.get('runner_sha256') == args.runner_sha256, 'Root must release this source and actual inventory')
    output = D / 'guest-http-placed-v6.json';require(not output.exists(), 'original one-shot output exists; no retry or overwrite')
    os.umask(0o077)
    with open(LOCK) as lock:
        try:fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({'result': 'LOCK_BUSY', 'guest_calls': 0}), flush=True);return 73
        fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600);os.close(fd)
        record = {'task_name': TASK, 'result': 'RUNNING', 'runner_sha256': args.runner_sha256, 'inventory_sha256': args.inventory_sha256,
                  'root_release_sha256': args.root_release_sha256, 'started_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  'api_calls': 0, 'cluster_writes': 0, 'privileged_guest_programs': 0, 'public_dns_office_requests': 0, 'server_lifetime_seconds': 180,
                  'secret_values_or_value_hashes_public': False, 'stage': 'native-guards', 'guests': {}, 'servers': {}, 'requests': {}, 'cleanup': {}, 'logout': {}}
        consoles = {};attempted = set();configs = {};sensitive = []
        def public(value):
            if isinstance(value, dict):return {k: public(v) for k, v in value.items()}
            if isinstance(value, list):return [public(v) for v in value]
            if isinstance(value, str):
                for secret in sensitive:value = value.replace(secret, '<REDACTED>')
            return value
        def save():output.write_text(json.dumps(public(record), indent=2) + '\n')
        def answer(reply):
            if not reply.get('ok'):raise RuntimeError('Guest envelope ' + str(reply.get('error_class')) + ': ' + str(reply.get('message')))
            return reply['value']
        def error_message(error):
            # pexpect exception strings include raw tty buffers; never publish them.
            return str(error)[:400] if isinstance(error, RuntimeError) else 'bounded stage raised ' + type(error).__name__
        save()
        try:
            require(get('namespace', 'kube-system')['metadata']['uid'] == CLUSTER and get('namespace', NS)['metadata']['uid'] == NSUID, 'fresh cluster/namespace UID differs')
            for row in rows.values():fresh(row)
            require(sha(VIRTCTL) == VIRTCTL_SHA, 'pinned existing virtctl changed')
            metadata = CREDENTIAL.lstat();require(stat.S_ISREG(metadata.st_mode) and stat.S_IMODE(metadata.st_mode) == 0o600 and metadata.st_uid == os.geteuid(), 'original private credential must be regular 0600')
            credential = json.loads(CREDENTIAL.read_text());require(set(credential) == {'username', 'password'} and all(isinstance(x, str) and x for x in credential.values()), 'existing credential shape changed')
            sensitive.extend(credential.values())
            nonce = secrets.token_hex(20);sensitive.append(nonce)
            for role, row in rows.items():
                configs[role] = {'token': nonce, 'directory': '/tmp/vm-mvp-v10-positive-02-' + nonce + '-' + role,
                                 'ip': row['vmi']['ip'], 'port': PORT, 'path': '/v10/' + nonce + '/' + role,
                                 'marker': 'ANI_V10_PRIVATE_' + role + '_' + secrets.token_hex(24) + '\n'}
                sensitive.append(configs[role]['marker'])
                def stage(value, role=role):
                    record['stage'] = role + ':' + value
                    record.setdefault('inherited_sudo_password_sends', {})[role] = consoles[role].inherited_sudo_password_sends
                    save()
                console = Console(row, stage);consoles[role] = console;console.login(credential)
                stage('guest-identity')
                identity = answer(console.run(identity_code(row, credential['username'])));record['guests'][role] = identity;save()
            credential.clear()
            for role, console in consoles.items():
                attempted.add(role);record['stage'] = 'start-server-' + role;save()
                result = answer(console.run(start_code(configs[role])))
                record['servers'][role] = result;save()
                require(result['uid'] == 1000 and result.get('self_status') == 200 and result.get('self_marker_equal') is True, 'one server self-GET failed')
            for role, peer in [('a', 'b'), ('b', 'a')]:
                for row in rows.values():fresh(row)
                record['stage'] = 'single-http-' + role + '-to-' + peer;save()
                result = answer(consoles[role].run(request_code(configs[peer], record['guests'][role]['boot_id'])))
                record['requests'][role + '_to_' + peer] = result;save()
            require(len(record['requests']) == 2 and all(x.get('uid') == 1000 and x.get('status') == 200 and x.get('marker_equal') is True and x.get('boot_equal') is True for x in record['requests'].values()), 'actual private HTTP response/marker/boot mismatch')
            record['result'] = 'PASS_CROSSNODE_BIDIRECTIONAL_GUEST_HTTP_UNIQUE_MARKERS'
        except Exception as error:
            record.update(result='STOPPED_RETAIN_ORIGINAL_ATTEMPT_NO_RETRY', failure_stage=record['stage'], error_class=type(error).__name__, message=error_message(error))
            if type(error).__name__ in {'TIMEOUT', 'EOF'}:
                for console in consoles.values():console.classify_prompt(record['stage'])
        finally:
            for role, console in consoles.items():
                if role in attempted:
                    try:
                        cleanup = answer(console.run(cleanup_code(configs[role])));record['cleanup'][role] = cleanup
                    except Exception as error:record['cleanup'][role] = {'confirmed': False, 'error_class': type(error).__name__, 'message': error_message(error)}
                else:record['cleanup'][role] = {'process_gone': True, 'directory_gone': True, 'server_not_attempted': True}
                console.close();record['logout'][role] = {'login_attempts': console.login_attempts, 'inherited_sudo_password_sends': console.inherited_sudo_password_sends, 'confirmed': console.logout_confirmed,
                    'failure_stage': None if console.logout_confirmed else record['stage'], 'error_class': console.logout_error_class}
            record['prompt_diagnostics'] = {role: console.prompt_diagnostics for role, console in consoles.items() if console.prompt_diagnostics}
            cleanup_ok = len(consoles) == 2 and all(x.get('process_gone') and x.get('directory_gone') for x in record['cleanup'].values())
            logout_ok = len(consoles) == 2 and all(x['confirmed'] for x in record['logout'].values())
            record['cleanup_confirmed'] = cleanup_ok;record['logout_confirmed'] = logout_ok
            if record['result'].startswith('PASS_') and not (cleanup_ok and logout_ok):record['result'] = 'STOPPED_HTTP_PASSED_CLEANUP_OR_LOGOUT_UNCONFIRMED'
            record['finished_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat();record['heavy_lock_released'] = True
            save();fcntl.flock(lock, fcntl.LOCK_UN)
            print(json.dumps({'result': record['result'], 'receipt': str(output), 'receipt_sha256': sha(output), 'heavy_lock_released': True}), flush=True)
    return 0 if record['result'].startswith('PASS_') else 1


if __name__ == '__main__':
    raise SystemExit(main())
