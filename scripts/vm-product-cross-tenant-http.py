#!/usr/bin/env python3
"""One fixed V3 -> Guest2 private HTTP isolation observation on Fedora.

Default pure guards make no connections. execute requires its authorized serial
window, checks both real guest/native identities and Guest2's existing HTTP
target before and after exactly one source curl. It never creates a server,
changes accounts/network/VMs, repeats DNS/egress tests, or retries that curl.
"""
import argparse
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import sys
import time


RUN = Path('/home/chabking/workspace/ani-resource-service-runs/vm-mvp-20261023-01a168ce')
HELPER = RUN / 'vm-product-second-tenant-vm.py'
HELPER_SHA = '821a8ab88a8a14d9f13fc2b1a194623645123097a7d164e8671f5d2469b2308f'
JOURNAL = RUN / 'private/gov-candidate/cross-tenant-http-v3-guest2.json'
URL = 'http://10.84.1.2:39192/vm-mvp-01a168ce-tenant2/ef51fbf518e815b1ec825a35'
BODY = 'ANI_VM_GUEST2_HTTP_01a168ce_ef51fbf518e815b1ec825a35\n'
BODY_SHA = 'da38569533185e14be28597b55e6309ccf3f8584504598335351d4f701939b2d'
SERVER_SHA = '23895a5e2c856bd6fc1806cf287864ec30228f82da0f1b16a831174b38bbbdb9'
SERVER_SCRIPT = '/home/aniuser/vm-mvp-01a168ce-tenant2-http.py'
SOURCE = {'vm_id': '55099440-c47b-4f81-afc5-7d8e740d14dc', 'vm_uid': '7bca4671-89dd-4925-bad9-17e77401729d',
          'vmi_uid': '257f1a0d-2abc-4eaf-b14f-ec49a29431b0', 'launcher_uid': 'ceb39733-dba1-4a98-bb60-27bdf2090452',
          'tenant': 'b3c946b5-7eb3-4feb-9f82-79712062ca5c', 'namespace_uid': '09e58ecb-fe20-454f-bc7b-2da3c406f0ac',
          'node': 'ani-02', 'ip': '10.83.1.9', 'mac': 'fa:00:cc:05:b0:99', 'boot': '1b0739e4-578d-4dcf-85db-0e07f4c3b480',
          'marker_path': '/home/aniuser/vm-mvp-marker', 'marker': 'vm-mvp-01a168ce-linux-v3-browser',
          'credentials': 'linux-guest-credentials-v3.json', 'intent': 'linux-vm-v3.json', 'credential_hash_field': 'credential_file_sha256',
          'root_name': 'vd-5dcd6eee-e30c-5dd2-b72b-f7d0d5699d21', 'pvc_uid': '3ac943cb-54cd-49b7-ad88-d8ac0815ff12',
          'pv_uid': '7684003b-4d9e-41dc-9d3f-18eaddf7eba4'}
TARGET = {'vm_id': 'e3c11c5a-cf12-4630-a8f9-f907048ffdfc', 'vm_uid': 'fde3b420-6bde-4169-9eb1-ed025c60a1a6',
          'vmi_uid': 'a96282ef-44f8-49a0-ac54-09fa94c1dbe7', 'launcher_uid': '7d886b6b-026f-4f6e-87d5-2d861a6a1c79',
          'tenant': 'd87c4dbd-04fa-4066-9ef6-c6fa4217f316', 'namespace_uid': 'fa138fd5-1f35-4fa2-b561-db3d136b2404',
          'node': 'ani-01', 'ip': '10.84.1.2', 'mac': 'fa:00:75:19:33:88', 'boot': 'fe087857-bd61-4d76-b892-dbd6b69ba601',
          'marker_path': '/home/aniuser/vm-mvp-tenant2-marker', 'marker': 'vm-mvp-01a168ce-tenant2-linux-e3c11c5a-cf12-4630-a8f9-f907048ffdfc',
          'credentials': 'tenant2-linux-guest-credentials.json', 'intent': 'tenant2-linux-vm.json', 'credential_hash_field': 'guest_file_sha256',
          'root_name': 'vd-2e1b4214-937a-5c45-b5b0-0d400a553d4c', 'pvc_uid': 'ad15d3fc-2474-4f0a-8397-7f406a7bfc00',
          'pv_uid': 'b6d93bcc-1338-4fcc-aed9-f374ecd719ff'}


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def classify(curl, healthy):
    if curl.get('exit') == 0 or curl.get('http_status', 0) > 0:
        return 'FAIL_ISOLATION_HTTP_RESPONSE'
    if not healthy:
        return 'INCONCLUSIVE'
    if curl.get('exit') == 28 and 'tim' in curl.get('stderr', '').lower():
        return 'PASS_ONE_WAY_REJECTION'
    if curl.get('exit') == 7 and 'connection refused' in curl.get('stderr', '').lower():
        return 'PASS_ONE_WAY_REJECTION'
    return 'INCONCLUSIVE'


def validate_guest(value, side):
    require(value.get('username') == 'aniuser' and value.get('boot_id') == side['boot'] and value.get('marker') == side['marker'], 'guest account/boot/marker changed')
    require(value.get('instance_id') == value.get('firmware_uuid') == side['vm_id'] and value.get('datasource', '').startswith('DataSourceNoCloud'), 'NoCloud logical identity changed')
    interfaces = [x for x in value.get('addresses', []) if x.get('operstate') == 'UP' and x.get('address') == side['mac']
                  and any(a.get('local') == side['ip'] and a.get('prefixlen') == 24 for a in x.get('addr_info', []))]
    gateway = '10.83.1.1' if side is SOURCE else '10.84.1.1'
    require(len(interfaces) == 1 and any(x.get('dst') == 'default' and x.get('gateway') == gateway and x.get('protocol') == 'dhcp'
            and x.get('dev') == interfaces[0]['ifname'] for x in value.get('routes', [])), 'exact guest DHCP/MAC/route changed')


def pure_guards():
    cases = [({'exit': 0, 'http_status': 200}, True, 'FAIL_ISOLATION_HTTP_RESPONSE'),
             ({'exit': 22, 'http_status': 403}, True, 'FAIL_ISOLATION_HTTP_RESPONSE'),
             ({'exit': 28, 'http_status': 100}, True, 'FAIL_ISOLATION_HTTP_RESPONSE'),
             ({'exit': 28, 'http_status': 0, 'stderr': 'Connection timed out'}, True, 'PASS_ONE_WAY_REJECTION'),
             ({'exit': 7, 'http_status': 0, 'stderr': 'Connection refused'}, True, 'PASS_ONE_WAY_REJECTION'),
             ({'exit': 28, 'http_status': 0, 'stderr': 'Timeout'}, False, 'INCONCLUSIVE'),
             ({'exit': 7, 'http_status': 0, 'stderr': 'Could not connect'}, True, 'INCONCLUSIVE'),
             ({'exit': 52, 'http_status': 0}, True, 'INCONCLUSIVE')]
    for value, healthy, expected in cases: require(classify(value, healthy) == expected, 'isolation classification guard failed')
    for side in (SOURCE, TARGET):
        fixture = {'username': 'aniuser', 'boot_id': side['boot'], 'marker': side['marker'], 'instance_id': side['vm_id'],
                   'firmware_uuid': side['vm_id'], 'datasource': 'DataSourceNoCloud',
                   'addresses': [{'ifname': 'enp1s0', 'operstate': 'UP', 'address': side['mac'], 'addr_info': [{'local': side['ip'], 'prefixlen': 24}]}],
                   'routes': [{'dst': 'default', 'gateway': '10.83.1.1' if side is SOURCE else '10.84.1.1', 'protocol': 'dhcp', 'dev': 'enp1s0'}]}
        validate_guest(fixture, side)
        for key in ('boot_id', 'marker', 'instance_id'):
            try: validate_guest({**fixture, key: 'wrong'}, side)
            except RuntimeError: pass
            else: raise RuntimeError('guest identity drift accepted')
    print(json.dumps({'result': 'PASS_PURE_GUARDS', 'classification_cases': len(cases), 'guest_drift_cases': 6, 'cluster_commands': 0, 'guest_connections': 0, 'curl_attempts': 0}))
    return 0


def load_helper():
    require(os.environ.get('SSH_CONNECTION', '').split()[2:3] == ['172.16.101.31'], 'only ssh fedora is authorized')
    require(hashlib.sha256(HELPER.read_bytes()).hexdigest() == HELPER_SHA, 'fixed serial/helper source changed')
    spec = importlib.util.spec_from_file_location('cross_tenant_helpers', HELPER)
    m = importlib.util.module_from_spec(spec); sys.modules[spec.name] = m; spec.loader.exec_module(m)
    return m, m.helper()


def native(m, side):
    ns = 'tenant-' + side['tenant']; namespace = m.get('ns', ns); vm = m.get('vm', 'vm-' + side['vm_id'], ns); vmi = m.get('vmi', vm['metadata']['name'], ns)
    require(namespace['metadata']['uid'] == side['namespace_uid'] and namespace['metadata']['labels'].get('network.ani.io/tenant-id') == side['tenant'], 'namespace UID/tenant changed')
    require(vm['metadata']['uid'] == side['vm_uid'] and vm['metadata']['labels'].get('ani.io/tenant-id') == side['tenant']
            and not vm['metadata'].get('deletionTimestamp') and vmi['metadata']['uid'] == side['vmi_uid']
            and m.owned(vmi, 'VirtualMachine', side['vm_uid']) and not vmi['metadata'].get('deletionTimestamp') and vmi['status'].get('phase') == 'Running'
            and vmi['status'].get('nodeName') == side['node'] and vmi['spec']['domain']['firmware']['uuid'] == side['vm_id'], 'fixed VM/VMI/firmware/node changed')
    pods = json.loads(m.subprocess_run(m.K + ['get', 'pods', '-n', ns, '-l', 'kubevirt.io/created-by=' + side['vmi_uid'], '-o', 'json']))['items']
    pods = [p for p in pods if m.owned(p, 'VirtualMachineInstance', side['vmi_uid']) and not p['metadata'].get('deletionTimestamp') and p.get('status', {}).get('phase') == 'Running'
            and any(c['name'] == 'compute' for c in p['spec']['containers'])]
    require(len(pods) == 1 and pods[0]['metadata']['uid'] == side['launcher_uid'] and pods[0]['spec'].get('nodeName') == side['node'], 'unique primary launcher UID changed')
    node = m.get('node', side['node']); require(node['metadata']['uid'] == m.NODE_UIDS[side['node']], 'permitted node UID changed')
    interface = [x for x in vmi['status'].get('interfaces', []) if x.get('name') == 'default']
    require(len(interface) == 1 and interface[0].get('ipAddress') == side['ip'] and interface[0].get('mac') == side['mac'], 'native address/MAC changed')
    pvc = m.get('pvc', side['root_name'], ns); pv = m.get('pv', pvc['spec']['volumeName'])
    require(pvc['metadata']['uid'] == side['pvc_uid'] and pvc['spec'].get('volumeMode') == 'Block' and pvc['status'].get('phase') == 'Bound'
            and pv['metadata']['uid'] == side['pv_uid'] and pv['spec']['claimRef']['uid'] == side['pvc_uid'], 'root Block PVC/PV identity changed')
    return {'namespace_uid': namespace['metadata']['uid'], 'vm': m.ref(vm), 'vmi': m.ref(vmi), 'launcher': m.ref(pods[0]), 'node': m.ref(node),
            'pvc': m.ref(pvc), 'pv': m.ref(pv), 'ip': side['ip'], 'mac': side['mac']}


def read_program(side, http=False):
    code = '''import hashlib,json,os,pathlib,pwd,re,subprocess,urllib.request
r=lambda p:pathlib.Path(p).read_text().strip()
j=lambda a:json.loads(subprocess.check_output(a))
p=pathlib.Path(MARKER_PATH);assert p.is_file() and not p.is_symlink() and p.stat().st_size<=4096
v={'username':os.environ.get('SUDO_USER'),'boot_id':r('/proc/sys/kernel/random/boot_id'),'marker':p.read_text().strip(),'instance_id':r('/var/lib/cloud/data/instance-id'),'firmware_uuid':r('/sys/class/dmi/id/product_uuid'),'datasource':r('/var/lib/cloud/instance/datasource'),'addresses':j(['ip','-j','address']),'routes':j(['ip','-j','route'])}
'''.replace('MARKER_PATH', repr(side['marker_path']))
    if http:
        code += '''p=pathlib.Path('/home/aniuser/vm-mvp-01a168ce-tenant2-http.py');q=pathlib.Path('/home/aniuser/.vm-mvp-01a168ce-http.json');uid=pwd.getpwnam('aniuser').pw_uid
assert uid>0 and p.is_file() and not p.is_symlink() and p.stat().st_uid==uid and q.is_file() and not q.is_symlink()
h=json.loads(q.read_text());proc=pathlib.Path('/proc/'+str(h['pid']))
assert h['pid']==1187 and h['start_ticks']=='51889' and h['script']==str(p) and h['script_sha256']==SERVER_SHA and hashlib.sha256(p.read_bytes()).hexdigest()==SERVER_SHA
assert h['vm_id']==TARGET_VM and h['port']==39192 and h['path']==HTTP_PATH and h['body']==HTTP_BODY
assert proc.joinpath('stat').read_text().split(') ',1)[1].split()[19]==h['start_ticks'] and proc.joinpath('cmdline').read_bytes().rstrip(b'\\0').split(b'\\0')[1:]==[str(p).encode()]
assert next(x for x in proc.joinpath('status').read_text().splitlines() if x.startswith('Uid:')).split()[1:]==[str(uid)]*4
listen=subprocess.check_output(['ss','-ltn','sport = :39192'],text=True);assert re.search(r'(?m)^LISTEN\\s+\\d+\\s+\\d+\\s+0\\.0\\.0\\.0:39192\\s',listen)
with urllib.request.urlopen('http://127.0.0.1:39192'+h['path'],timeout=3) as response:data=response.read();status=response.status
assert status==200 and data==HTTP_BODY.encode() and hashlib.sha256(data).hexdigest()==HTTP_SHA
v['http']={'process':h,'source_sha256':SERVER_SHA,'listen':listen,'loopback_status':status,'loopback_exact_body':True,'loopback_body_sha256':hashlib.sha256(data).hexdigest(),'process_owner_uid':uid}
'''
        replacements = {'SERVER_SHA': repr(SERVER_SHA), 'TARGET_VM': repr(TARGET['vm_id']), 'HTTP_PATH': repr(URL.split(':39192', 1)[1]), 'HTTP_BODY': repr(BODY), 'HTTP_SHA': repr(BODY_SHA)}
        for key in sorted(replacements, key=len, reverse=True): code = re.sub(r'\b' + key + r'\b', lambda _: replacements[key], code)
    return code + "print('ANI_CROSS_READ='+json.dumps(v))"


class Session:
    def __init__(self, m, side):
        import pexpect
        self.side, self.m, self.transcript = side, m, io.StringIO()
        path = m.PRIVATE / side['credentials']; intent = json.loads((m.PRIVATE / side['intent']).read_text())
        require(not path.is_symlink() and path.stat().st_mode & 0o077 == 0 and hashlib.sha256(path.read_bytes()).hexdigest() == intent[side['credential_hash_field']], 'original private credential file changed')
        self.secret = json.loads(path.read_text()); require(self.secret['username'] == 'aniuser', 'fixed original guest username changed')
        virtctl = RUN / 'tools/virtctl-v1.9.0-linux-amd64'
        require(hashlib.sha256(virtctl.read_bytes()).hexdigest() == '40ede2ee37c98a1aeed71c9c219616a05247ce2be109e1edddf0477572e8b978', 'official virtctl changed')
        self.child = pexpect.spawn(str(virtctl), ['console', 'vm-' + side['vm_id'], '--namespace', 'tenant-' + side['tenant'], '--kubeconfig', m.K[1].split('=', 1)[1], '--timeout=20'], encoding='utf-8', timeout=30)
        self.child.logfile_read = self.transcript
        self.authentication = {'method': 'REUSED_EXISTING_TASK_SESSION'}

    def connect(self):
        c = self.child; c.expect(r'Successfully connected to .* console\. Press Ctrl\+\] or Ctrl\+5 to exit console\.'); c.sendline('')
        if c.expect([r'login:', r'\$ ']) == 0:
            c.sendline('aniuser'); c.expect(r'[Pp]assword:'); started = time.monotonic(); c.sendline(self.secret['password'])
            hit = c.expect([r'\$ ', r'Login incorrect'], timeout=90)
            self.authentication = {'method': 'NORMAL_ORIGINAL_PASSWORD_LOGIN', 'password_to_shell_seconds': time.monotonic() - started}
            require(hit == 0, 'original password explicitly rejected')
        c.sendline("python3 -c 'import getpass;print(\"ANI_CROSS_USER=\"+getpass.getuser())'")
        c.expect(r'(?:^|[\r\n])ANI_CROSS_USER=([^\r\n]+)\r?\n'); user = c.match.group(1); c.expect(r'\$ ')
        require(user == 'aniuser', 'existing TTY belongs to another user; no sudo password is sent')
        c.sendline("sudo -v -p 'ANI_CROSS_SUDO: '")
        if c.expect([r'(?:^|[\r\n])ANI_CROSS_SUDO: ', r'\$ ']) == 0:
            c.sendline(self.secret['password']); c.expect(r'\$ ')

    def run(self, code, tag, sudo=False):
        c = self.child; c.sendline(self.m.serial_line(code, sudo))
        match = c.expect([r'(?:^|[\r\n])' + tag + r'=(\{[^\r\n]+\})\r?\n', r'\$ '], timeout=30)
        require(match == 0, 'guest command ended without its complete result: ' + c.before[-1500:])
        value = json.loads(c.match.group(1)); c.expect(r'\$ '); return value

    def read(self, http=False):
        value = self.run(read_program(self.side, http), 'ANI_CROSS_READ', sudo=True)
        validate_guest(value, self.side); return value

    def close(self):
        self.child.close(force=True)  # Only the owned local virtctl process.


def execute():
    m, g = load_helper(); sessions = []; result = {'at': m.now(), 'scope': 'one V3 ani02 -> tenant2 ani01 exact private HTTP rejection; not full V10', 'url': URL, 'curl_attempts': 0, 'result': 'INCONCLUSIVE'}
    intent = {'source': SOURCE, 'target': TARGET, 'url': URL, 'target_body_sha256': BODY_SHA, 'server_source_sha256': SERVER_SHA}
    journal = json.loads(JOURNAL.read_text()) if JOURNAL.exists() else {'intent': intent, 'curl_attempts': 0}
    require(journal['intent'] == intent and journal['curl_attempts'] == 0, 'this exact source curl was already attempted; never retry it automatically')
    g.save(JOURNAL, journal)
    try:
        with m.lock():
            require(m.get('ns', 'kube-system')['metadata']['uid'] == m.CLUSTER, 'wrong cluster')
            result['native_before'] = {'source': native(m, SOURCE), 'target': native(m, TARGET)}
            target = Session(m, TARGET); sessions.append(target); target.connect(); result['target_before'] = target.read(http=True)
            source = Session(m, SOURCE); sessions.append(source); source.connect(); result['source_before'] = source.read()
            result['authentication'] = {'source': source.authentication, 'target': target.authentication}
            code = '''import hashlib,json,pathlib,re,subprocess,time
started=time.monotonic();v={'boot_before':pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()}
try:
 p=subprocess.run(['curl','--ipv4','--silent','--show-error','--noproxy','*','--proto','=http','--connect-timeout','8','--max-time','8','--write-out','\\nANI_CURL_HTTP=%{http_code}\\n',URL],capture_output=True,timeout=12)
 out=p.stdout.decode(errors='replace');h=re.search(r'\\nANI_CURL_HTTP=(\\d{3})\\n$',out)
 v.update(exit=p.returncode,http_status=int(h.group(1)) if h else 0,stdout=out[:4096],stderr=p.stderr.decode(errors='replace')[:2048],body_sha256=hashlib.sha256(p.stdout).hexdigest())
except Exception as e:v['error']=type(e).__name__+': '+str(e)
v.update(elapsed_seconds=time.monotonic()-started,boot_after=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip(),marker=pathlib.Path('/home/aniuser/vm-mvp-marker').read_text().strip())
print('ANI_CROSS_CURL='+json.dumps(v))'''
            code = re.sub(r'\bURL\b', lambda _: repr(URL), code)
            journal['curl_attempts'] = 1; journal['started_at'] = m.now(); g.save(JOURNAL, journal); result['curl_attempts'] = 1
            result['curl'] = source.run(code, 'ANI_CROSS_CURL')
            require(result['curl']['boot_before'] == result['curl']['boot_after'] == SOURCE['boot'] and result['curl']['marker'] == SOURCE['marker'], 'source guest changed during the one request')
            result['target_after'] = target.read(http=True)
            result['native_after'] = {'source': native(m, SOURCE), 'target': native(m, TARGET)}
            require(result['native_before'] == result['native_after'], 'native identity changed during the observation')
            result['result'] = classify(result['curl'], healthy=True)
            result['healthy_pre_post'] = 'PASS'
    except Exception as error:
        result['error'] = type(error).__name__ + ': ' + str(error)[-2000:]
        if result.get('curl'): result['result'] = classify(result['curl'], healthy=False)
    finally:
        for session in sessions: session.close()
        stem = RUN / 'evidence/product-guest' / ('cross-tenant-http-' + str(time.time_ns())); stem.parent.mkdir(parents=True, exist_ok=True)
        redacted = json.dumps(result, indent=2)
        for session in sessions: redacted = redacted.replace(session.secret['password'], '[REDACTED]')
        stem.with_suffix('.json').write_text(redacted + '\n')
        for index, session in enumerate(sessions):
            text = session.transcript.getvalue()
            for other in sessions: text = text.replace(other.secret['password'], '[REDACTED]')
            stem.with_name(stem.name + '-' + str(index)).with_suffix('.txt').write_text(text)
        journal['receipt_reference'] = str(stem.with_suffix('.json')); journal['result'] = result['result']; g.save(JOURNAL, journal)
    print(json.dumps({'result': result['result'], 'curl_attempts': result['curl_attempts'], 'healthy_pre_post': result.get('healthy_pre_post'), 'receipt': str(stem.with_suffix('.json'))}))
    return 0 if result['result'] == 'PASS_ONE_WAY_REJECTION' else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('check-guards', 'execute'), nargs='?', default='check-guards')
    parser.add_argument('--execute-authorized', action='store_true')
    args = parser.parse_args()
    if args.action == 'check-guards':
        require(not args.execute_authorized, 'pure guards do not accept an execution flag'); return pure_guards()
    require(args.execute_authorized, 'fresh explicit serial execution authorization required')
    return execute()


if __name__ == '__main__':
    try: raise SystemExit(main())
    except Exception as error: raise SystemExit('STOP: ' + str(error))
