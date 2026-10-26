#!/usr/bin/env python3
"""Observe only the fixed V3 product guest after an explicitly authorized window.

The default check-guards action is pure data validation. probe-v3 requires the
current VMI UID, previously observed boot ID and a fresh execution authorization.
It reuses the existing exact-path HTTP target without starting or stopping it.
No guest configuration, account, VM lifecycle or shared networking is changed.
"""
import argparse
import datetime
import fcntl
import hashlib
import io
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import time


RUN = Path('/home/chabking/workspace/ani-resource-service-runs/vm-mvp-20261023-01a168ce')
OUT = RUN / 'evidence/product-guest'
CLUSTER = '5277649d-e28d-4a0a-ae76-8354365c63bc'
TENANT = 'b3c946b5-7eb3-4feb-9f82-79712062ca5c'
NS = 'tenant-' + TENANT
NS_UID = '09e58ecb-fe20-454f-bc7b-2da3c406f0ac'
VM_ID = '55099440-c47b-4f81-afc5-7d8e740d14dc'
VM_UID = '7bca4671-89dd-4925-bad9-17e77401729d'
NAME = 'vm-' + VM_ID
MARKER = 'vm-mvp-01a168ce-linux-v3-browser'
PRODUCT = RUN / 'evidence/gov-linux-product-v3.json'
CREDENTIALS = RUN / 'private/gov-candidate/linux-guest-credentials-v3.json'
HTTP_RECEIPT = OUT / 'http-target.json'
HTTP_SCRIPT = RUN / 'vm-product-network-probe.py'
HTTP_SCRIPT_SHA = 'ab7ecd0649db04752a9ae380dd7cb6b908d52bb460035219bab83f070c5ccdb4'
HTTP_PATH = '/vm-mvp-01a168ce-linux-v2/5b2160d72615913154120a32'
HTTP_BODY_SHA = 'aa1f9602b53f0c7a8c6b4a2aaa7481e69bbcb3faa9677a4c6b53935145d2288a'
K = ['/home/chabking/ani-four-service-deploy-20261012/tools/kubectl',
     '--kubeconfig=/home/chabking/ani-four-service-deploy-20261012/private/kubeconfig', '--request-timeout=10s']
VIRTCTL = RUN / 'tools/virtctl-v1.9.0-linux-amd64'
VIRTCTL_SHA = '40ede2ee37c98a1aeed71c9c219616a05247ce2be109e1edddf0477572e8b978'
DNS_UID = '52a9e852-be01-4642-8a5d-bc479ecc4e72'
UUID = re.compile(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}')


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha(path):
    return hashlib.file_digest(path.open('rb'), 'sha256').hexdigest()


def get(kind, name, namespace=''):
    args = K + ['get', kind, name] + (['-n', namespace] if namespace else []) + ['-o', 'json']
    return json.loads(subprocess.run(args, check=True, capture_output=True, text=True, timeout=15).stdout)


def owned(obj, kind, uid):
    return any(x.get('kind') == kind and x.get('uid') == uid and x.get('controller') is True
               for x in obj['metadata'].get('ownerReferences', []))


def validate_http_record(value):
    require(value['pid'] == 1560132 and value['start_ticks'] == '63915511', 'HTTP target process receipt changed')
    require(value['bind'] == '172.16.101.31' and value['port'] == 39190 and value['path'] == HTTP_PATH,
            'HTTP target endpoint changed')
    require(value['script'] == str(HTTP_SCRIPT) and value['script_sha256'] == HTTP_SCRIPT_SHA,
            'original HTTP serve script identity changed')
    require(value['body_sha256'] == HTTP_BODY_SHA and hashlib.sha256(value['body'].encode()).hexdigest() == HTTP_BODY_SHA,
            'HTTP target exact body changed')


def existing_http_target():
    value = json.loads(HTTP_RECEIPT.read_text())
    validate_http_record(value)
    ticks = Path('/proc/' + str(value['pid']) + '/stat').read_text().split(') ', 1)[1].split()[19]
    argv = Path('/proc/' + str(value['pid']) + '/cmdline').read_bytes().rstrip(b'\0').split(b'\0')
    require(ticks == value['start_ticks'] and len(argv) == 3 and argv[1] == str(HTTP_SCRIPT).encode() and argv[2] == b'serve',
            'existing HTTP process is absent or PID/command was replaced; never start a replacement here')
    require(sha(HTTP_SCRIPT) == HTTP_SCRIPT_SHA, 'HTTP serve script bytes changed; preserve its cleanup guard')
    return value


def runtime_identity(vmi_uid):
    ns, vm, vmi = get('ns', NS), get('vm', NAME, NS), get('vmi', NAME, NS)
    require(ns['metadata']['uid'] == NS_UID and ns['metadata'].get('labels', {}).get('network.ani.io/tenant-id') == TENANT,
            'fixed namespace UID or normal tenant label changed')
    require(vm['metadata']['uid'] == VM_UID and vm['metadata'].get('labels', {}).get('ani.io/tenant-id') == TENANT and
            vm['metadata'].get('labels', {}).get('vm.ani.io/vm-id') == VM_ID and not vm['metadata'].get('deletionTimestamp'),
            'fixed product VM identity changed')
    require(vmi['metadata']['uid'] == vmi_uid and owned(vmi, 'VirtualMachine', VM_UID) and
            not vmi['metadata'].get('deletionTimestamp') and vmi.get('status', {}).get('phase') == 'Running',
            'explicit current VMI window changed')
    require(vmi['spec']['domain']['firmware']['uuid'] == VM_ID, 'logical firmware UUID changed')
    interfaces = [x for x in vmi['status'].get('interfaces', []) if x.get('name') == 'default' and x.get('ipAddress')]
    require(len(interfaces) == 1, 'current default VMI address is ambiguous')
    address = interfaces[0]['ipAddress']
    require(ipaddress.ip_address(address) in ipaddress.ip_network('10.83.1.0/24'), 'V3 address left the fixed task subnet')
    return {'namespace_uid': ns['metadata']['uid'], 'vm_uid': vm['metadata']['uid'], 'vmi_uid': vmi_uid,
            'node': vmi['status'].get('nodeName'), 'current_ip': address}


def validate_guest_identity(value, boot_id, native_ip):
    require(value.get('username') == 'aniuser' and value.get('marker') == MARKER, 'real original account/marker check failed')
    require(value.get('boot_id') == boot_id and UUID.fullmatch(boot_id), 'authorized guest boot window changed')
    require(value.get('firmware_uuid') == VM_ID and value.get('instance_id') == VM_ID and
            value.get('datasource', '').startswith('DataSourceNoCloud'), 'real V3 NoCloud identity differs')
    interfaces = [x for x in value.get('addresses', []) if x.get('operstate') == 'UP' and
                  any(a.get('family') == 'inet' and a.get('local') == native_ip and a.get('prefixlen') == 24
                      for a in x.get('addr_info', []))]
    require(len(interfaces) == 1, 'guest current IP/prefix/UP differs from the exact native VMI')
    require(any(x.get('dst') == 'default' and x.get('gateway') == '10.83.1.1' and x.get('protocol') == 'dhcp' and
                x.get('dev') == interfaces[0]['ifname'] for x in value.get('routes', [])), 'real DHCP default route is missing')


def dns_pass(observed, service_ip, dns_ip):
    getent = observed.get('dns_getent', {})
    resolved = observed.get('dns_resolvectl', {})
    uplink = observed.get('dns_uplink', {})
    answers = {line.split()[0] for line in getent.get('stdout', '').splitlines() if line.split()}
    resolved_ips = set(re.findall(r'(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])', resolved.get('stdout', '')))
    uplink_ips = set(re.findall(r'(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])', uplink.get('stdout', '')))
    return getent.get('exit') == resolved.get('exit') == uplink.get('exit') == 0 and answers == {service_ip} and resolved_ips == {service_ip} and dns_ip in uplink_ips


def validate_dns_service(service):
    meta, spec = service['metadata'], service['spec']
    require(meta.get('name') == 'coredns' and meta.get('namespace') == 'kube-system' and meta.get('uid') == DNS_UID
            and not meta.get('deletionTimestamp') and meta.get('labels', {}).get('k8s-app') == 'kube-dns'
            and meta.get('labels', {}).get('kubernetes.io/name') == 'CoreDNS' and spec.get('clusterIP') == '10.96.0.3',
            'actual CoreDNS Service UID/labels/IP changed')
    require({('UDP', 53), ('TCP', 53)} <= {(p.get('protocol', 'TCP'), p.get('port')) for p in spec.get('ports', [])},
            'actual CoreDNS Service must expose UDP53 and TCP53')


def check_guards():
    base = {'username': 'aniuser', 'marker': MARKER, 'boot_id': VM_ID, 'firmware_uuid': VM_ID,
            'instance_id': VM_ID, 'datasource': 'DataSourceNoCloud [seed=/dev/vdb]',
            'addresses': [{'ifname': 'enp1s0', 'operstate': 'UP', 'addr_info': [{'family': 'inet', 'local': '10.83.1.8', 'prefixlen': 24}]}],
            'routes': [{'dst': 'default', 'gateway': '10.83.1.1', 'protocol': 'dhcp', 'dev': 'enp1s0'}]}
    validate_guest_identity(base, VM_ID, '10.83.1.8')
    rejected = 0
    for field, changed in [('username', 'root'), ('marker', 'wrong'), ('boot_id', TENANT), ('firmware_uuid', TENANT),
                           ('instance_id', TENANT), ('datasource', 'unknown'), ('addresses', []), ('routes', [])]:
        try:
            validate_guest_identity({**base, field: changed}, VM_ID, '10.83.1.8')
        except RuntimeError:
            rejected += 1
    require(rejected == 8, 'guest identity negative guard failed')
    http = {'pid': 1560132, 'start_ticks': '63915511', 'bind': '172.16.101.31', 'port': 39190,
            'path': HTTP_PATH, 'script': str(HTTP_SCRIPT), 'script_sha256': HTTP_SCRIPT_SHA,
            'body': 'ANI_VM_HTTP_v2_01a168ce_5b2160d72615913154120a32\n', 'body_sha256': HTTP_BODY_SHA}
    validate_http_record(http)
    http_rejected = 0
    for field, changed in [('pid', 1), ('bind', '0.0.0.0'), ('path', '/'), ('script_sha256', '0' * 64), ('body', 'wrong')]:
        try:
            validate_http_record({**http, field: changed})
        except RuntimeError:
            http_rejected += 1
    require(http_rejected == 5, 'existing HTTP identity negative guard failed')
    dns = {'dns_getent': {'exit': 0, 'stdout': '10.96.0.1 STREAM kubernetes.default.svc.cluster.local\n'},
           'dns_resolvectl': {'exit': 0, 'stdout': 'kubernetes.default.svc.cluster.local: 10.96.0.1\n'},
           'dns_uplink': {'exit': 0, 'stdout': 'Link 2 (enp1s0): 10.96.0.3\n'}}
    require(dns_pass(dns, '10.96.0.1', '10.96.0.3'), 'exact DNS positive guard failed')
    dns['dns_getent']['stdout'] = '10.96.0.10 STREAM wrong\n'
    require(not dns_pass(dns, '10.96.0.1', '10.96.0.3'), 'substring DNS answer accepted')
    service = {'metadata': {'name': 'coredns', 'namespace': 'kube-system', 'uid': DNS_UID,
                           'labels': {'k8s-app': 'kube-dns', 'kubernetes.io/name': 'CoreDNS'}},
               'spec': {'clusterIP': '10.96.0.3', 'ports': [{'protocol': 'UDP', 'port': 53}, {'protocol': 'TCP', 'port': 53}]}}
    validate_dns_service(service)
    try:
        validate_dns_service({**service, 'spec': {**service['spec'], 'ports': [{'protocol': 'UDP', 'port': 53}]}})
    except RuntimeError:
        pass
    else:
        raise RuntimeError('missing TCP53 was accepted')
    print(json.dumps({'result': 'PASS_PURE_GUARDS', 'guest_negative_guards': rejected, 'http_negative_guards': http_rejected, 'dns_exact_answer_guards': 2,
                      'cluster_commands': 0, 'guest_connections': 0, 'http_server_changes': 0}))
    return 0


def probe(args):
    import pexpect
    require(os.environ.get('SSH_CONNECTION', '').split()[2:3] == ['172.16.101.31'], 'probe only through ssh fedora')
    require(get('ns', 'kube-system')['metadata']['uid'] == CLUSTER, 'wrong cluster')
    product = json.loads(PRODUCT.read_text())
    require(product.get('vm_id') == VM_ID and product.get('media_key') == 'ubuntu-24.04', 'fixed V3 admission receipt changed')
    native = runtime_identity(args.vmi_uid)
    require(sha(VIRTCTL) == VIRTCTL_SHA, 'official virtctl checksum changed')
    http = existing_http_target()
    svc, dns = get('svc', 'kubernetes', 'default'), get('svc', 'coredns', 'kube-system')
    validate_dns_service(dns)
    service_ip, dns_ip = svc['spec']['clusterIP'], dns['spec']['clusterIP']
    require(service_ip == '10.96.0.1' and dns_ip == '10.96.0.3', 'fixed Service/DNS target changed')
    require(not CREDENTIALS.is_symlink() and CREDENTIALS.stat().st_mode & 0o077 == 0, 'private V3 credential permissions changed')
    credentials = json.loads(CREDENTIALS.read_text())
    username, password = credentials['username'], credentials['password']
    require(username == 'aniuser' and password, 'fixed V3 original account is unavailable')
    result = {'at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'layer': 'GUEST', 'scope': 'fixed V3 read-only serial network; BROWSER separate',
              **native, 'vm_id': VM_ID, 'expected_boot_id': args.boot_id, 'product_receipt_reference': str(PRODUCT),
              'private_credentials_reference': str(CREDENTIALS), 'http_target_reference': str(HTTP_RECEIPT),
              'service_uid': svc['metadata']['uid'], 'service_ip': service_ip, 'dns_uid': dns['metadata']['uid'], 'dns_ip': dns_ip,
              'driver_sha256': sha(Path(__file__)), 'identity_precondition': 'NOT_RUN', 'dns': 'NOT_RUN', 'http': 'NOT_RUN', 'https_egress': 'NOT_RUN'}
    transcript = io.StringIO()
    child = pexpect.spawn(str(VIRTCTL), ['console', NAME, '--namespace', NS, '--kubeconfig', K[1].split('=', 1)[1], '--timeout=20'], encoding='utf-8', timeout=30)
    child.logfile_read = transcript
    def guest(code, tag, sudo=False, timeout=40):
        line = ('sudo -n ' if sudo else '') + 'python3 -c ' + shlex.quote(code)
        require(len(line.encode()) < 4000, 'guest command exceeds bounded serial TTY input')
        child.sendline(line)
        child.expect(r'(?:^|[\r\n])' + tag + r'=(\{[^\r\n]+\})\r?\n', timeout=timeout)
        value = json.loads(child.match.group(1))
        child.expect(r'\$ ')
        return value
    try:
        child.expect(r'Successfully connected to .* console\. Press Ctrl\+\] or Ctrl\+5 to exit console\.')
        child.sendline('')
        if child.expect([r'login:', r'\$ ']) == 1:
            child.sendline('exit'); child.expect(r'login:')
        child.sendline(username); child.expect(r'[Pp]assword:'); child.sendline(password); child.expect(r'\$ ')
        child.sendline("sudo -v -p 'ANI_V3_SUDO: '")
        if child.expect([r'(?:^|[\r\n])ANI_V3_SUDO: ', r'\$ ']) == 0:
            child.sendline(password); child.expect(r'\$ ')
        precheck = '''import json,os,pathlib,subprocess
r=lambda p:pathlib.Path(p).read_text().strip()
j=lambda a:json.loads(subprocess.check_output(a))
m=pathlib.Path('/home/aniuser/vm-mvp-marker')
safe=m.is_file() and not m.is_symlink() and m.stat().st_size<=4096
print('ANI_V3_IDENTITY='+json.dumps({'username':os.environ.get('SUDO_USER'),'marker':m.read_text().strip() if safe else None,'boot_id':r('/proc/sys/kernel/random/boot_id'),'firmware_uuid':r('/sys/class/dmi/id/product_uuid'),'instance_id':r('/var/lib/cloud/data/instance-id'),'datasource':r('/var/lib/cloud/instance/datasource'),'addresses':j(['ip','-j','address']),'routes':j(['ip','-j','route'])}))'''
        identity = guest(precheck, 'ANI_V3_IDENTITY', sudo=True)
        result['identity'] = identity
        validate_guest_identity(identity, args.boot_id, native['current_ip'])
        result['identity_precondition'] = 'PASS'
        # Network commands are reached only after real account/marker/boot/
        # NoCloud/DHCP identity checks; no guest network configuration is written.
        code = '''import json,pathlib,subprocess,hashlib
def run(a):
 try:
  p=subprocess.run(a,capture_output=True,timeout=18)
  return {'argv':a,'exit':p.returncode,'stdout':p.stdout.decode(errors='replace')[:8192],'stderr':p.stderr.decode(errors='replace')[:2048],'body_sha256':hashlib.sha256(p.stdout).hexdigest()}
 except (OSError,subprocess.TimeoutExpired) as e:return {'argv':a,'error':type(e).__name__+': '+str(e)}
print('ANI_V3_NETWORK='+json.dumps({'boot_id':pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip(),'target_route':run(['ip','-j','route','get','172.16.101.31']),'dns_getent':run(['getent','ahostsv4','kubernetes.default.svc.cluster.local']),'dns_resolvectl':run(['resolvectl','query','kubernetes.default.svc.cluster.local']),'dns_uplink':run(['resolvectl','dns']),'http':run(['curl','--fail','--show-error','--silent','--connect-timeout','5','--max-time','12',HTTP_URL]),'https_egress':run(['curl','--fail','--show-error','--silent','--connect-timeout','5','--max-time','12','https://example.com/']),'ssh_units':run(['systemctl','show','ssh.socket','ssh.service','-p','Id','-p','ActiveState','-p','SubState']),'ssh_listen':run(['ss','-ltn','sport = :22'])}))'''.replace('HTTP_URL', repr('http://172.16.101.31:39190' + http['path']))
        observed = guest(code, 'ANI_V3_NETWORK', timeout=120)
        result['observed'] = observed
        require(observed['boot_id'] == args.boot_id and runtime_identity(args.vmi_uid) == native, 'guest/native changed during the observation')
        result['dns'] = 'PASS' if dns_pass(observed, service_ip, dns_ip) else 'FAIL'
        result['http'] = 'PASS' if observed['http'].get('exit') == 0 and observed['http'].get('stdout') == http['body'] and observed['http'].get('body_sha256') == HTTP_BODY_SHA else 'FAIL'
        result['https_egress'] = 'PASS' if observed['https_egress'].get('exit') == 0 and observed['https_egress'].get('stdout') else 'FAIL'
        result['result'] = 'PASS' if all(result[x] == 'PASS' for x in ('dns', 'http', 'https_egress')) else 'PARTIAL_FAIL'
        child.sendline('exit'); child.expect(r'login:')
    except Exception as error:
        result.update(result='FAIL', error=str(error).replace(password, '[REDACTED]')[-2000:])
    finally:
        child.close(force=True)
        OUT.mkdir(parents=True, exist_ok=True)
        stem = OUT / ('network-v3-' + str(time.time_ns()))
        stem.with_suffix('.json').write_text(json.dumps(result, indent=2).replace(password, '[REDACTED]') + '\n')
        stem.with_suffix('.txt').write_text(transcript.getvalue().replace(password, '[REDACTED]'))
    print(json.dumps({'result': result['result'], 'vmi_uid': args.vmi_uid, 'identity_precondition': result['identity_precondition'],
                      'dns': result['dns'], 'http': result['http'], 'https_egress': result['https_egress'], 'receipt': str(stem.with_suffix('.json'))}))
    return 0 if result['result'] == 'PASS' else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', nargs='?', choices=('check-guards', 'probe-v3'), default='check-guards')
    parser.add_argument('--vmi-uid', help='explicit authoritative current VMI UID from Root')
    parser.add_argument('--boot-id', help='previously verified guest boot ID for this VMI window')
    parser.add_argument('--execute-authorized', action='store_true', help='fresh authorized read-only serial window')
    args = parser.parse_args()
    if args.action == 'check-guards':
        require(not args.execute_authorized and not args.vmi_uid and not args.boot_id, 'pure guards do not accept execution inputs')
        return check_guards()
    require(args.execute_authorized and args.vmi_uid and UUID.fullmatch(args.vmi_uid) and args.boot_id and UUID.fullmatch(args.boot_id),
            'probe-v3 requires explicit authorization, current VMI UID and known boot ID')
    with Path('/home/chabking/workspace/ani-network-service-runs/net05a-heavy.lock').open('a') as lock:
        end = time.monotonic() + 5
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= end:
                    print('LOCK_BUSY; no cluster or guest commands sent')
                    return 73
                time.sleep(.2)
        return probe(args)


if __name__ == '__main__':
    raise SystemExit(main())
