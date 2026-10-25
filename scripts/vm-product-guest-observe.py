#!/usr/bin/env python3
"""Fedora-only read-only serial observation of an already-created product VM.

The caller supplies the authoritative native VM/VMI UIDs and an execution window.
This driver never creates resources, changes guest configuration, or controls VM
power. It reads existing Fedora-private credentials without copying them.
"""
import argparse
import datetime
import fcntl
import hashlib
import json
import pathlib
import re
import shlex
import subprocess
import time

RUN = pathlib.Path('/home/chabking/workspace/ani-resource-service-runs/vm-mvp-20261023-01a168ce')
OUT = RUN / 'evidence/product-guest'
K = ['/home/chabking/ani-four-service-deploy-20261012/tools/kubectl', '--kubeconfig=/home/chabking/ani-four-service-deploy-20261012/private/kubeconfig', '--request-timeout=15s']
VIRTCTL = RUN / 'tools/virtctl-v1.9.0-linux-amd64'
VIRTCTL_SHA = '40ede2ee37c98a1aeed71c9c219616a05247ce2be109e1edddf0477572e8b978'
CREDENTIALS_DIR = RUN / 'private/gov-candidate'


def get(kind, name, namespace):
    args = ['get', kind, name]
    if namespace: args += ['-n', namespace]
    return json.loads(subprocess.run(K + args + ['-o', 'json'], check=True, capture_output=True, text=True, timeout=20).stdout)


def ref(obj):
    return {key: obj['metadata'].get(key, '') for key in ('namespace', 'name', 'uid')} | {'kind': obj['kind']}


def owned(obj, kind, uid):
    return any(owner.get('kind') == kind and owner.get('uid') == uid for owner in obj['metadata'].get('ownerReferences', []))


def observe(args):
    import pexpect
    if get('namespace', 'kube-system', '')['metadata']['uid'] != '5277649d-e28d-4a0a-ae76-8354365c63bc': raise RuntimeError('wrong cluster identity')
    tenant = json.loads((RUN / 'evidence/gov-candidate-tenant.json').read_text())
    product = json.loads((RUN / 'evidence' / args.product_receipt).read_text())
    namespace, tenant_id, vm_id = tenant['namespace'], tenant['resource_tenant_id'], product['vm_id']
    if vm_id != args.vm_id or product.get('media_key') != 'ubuntu-24.04': raise RuntimeError('unexpected product logical VM or media intent')
    ns = get('namespace', namespace, '')
    if ns['metadata']['uid'] != tenant['namespace_uid'] or ns['metadata'].get('labels', {}).get('network.ani.io/tenant-id') != tenant_id:
        raise RuntimeError('trusted tenant namespace identity changed')
    vms = json.loads(subprocess.run(K + ['get', 'vm', '-n', namespace, '-l', 'vm.ani.io/vm-id=' + vm_id, '-o', 'json'], check=True, capture_output=True, text=True, timeout=20).stdout)['items']
    if len(vms) != 1 or vms[0]['metadata']['uid'] != args.vm_uid: raise RuntimeError('expected one authoritative product VM UID')
    vm = vms[0]; name = vm['metadata']['name']
    if vm['metadata'].get('labels', {}).get('ani.io/tenant-id') != tenant_id: raise RuntimeError('VM tenant identity changed')
    vmi = get('vmi', name, namespace)
    if vmi['metadata']['uid'] != args.vmi_uid or not owned(vmi, 'VirtualMachine', args.vm_uid) or vmi.get('status', {}).get('phase') != 'Running':
        raise RuntimeError('authoritative product VMI is not Running under the expected VM')
    if vmi['spec']['domain']['firmware']['uuid'] != vm_id: raise RuntimeError('native logical firmware identity changed')
    pods = json.loads(subprocess.run(K + ['get', 'pods', '-n', namespace, '-l', 'kubevirt.io/created-by=' + args.vmi_uid, '-o', 'json'], check=True, capture_output=True, text=True, timeout=20).stdout)['items']
    pods = [pod for pod in pods if not pod['metadata'].get('deletionTimestamp') and pod.get('status', {}).get('phase') == 'Running']
    if len(pods) != 1 or not owned(pods[0], 'VirtualMachineInstance', args.vmi_uid): raise RuntimeError('product launcher identity is ambiguous')
    pod = pods[0]
    compute = next(container for container in pod['spec']['containers'] if container['name'] == 'compute')
    uid = compute.get('securityContext', {}).get('runAsUser', pod['spec'].get('securityContext', {}).get('runAsUser'))
    if uid != 107: raise RuntimeError('product launcher is not native UID107')
    volumes = []
    for volume in vm['spec']['template']['spec'].get('volumes', []):
        if 'dataVolume' not in volume: continue
        dv = get('dv', volume['dataVolume']['name'], namespace)
        pvc = get('pvc', dv['metadata']['name'], namespace)
        if not owned(dv, 'VirtualMachine', args.vm_uid) or not (owned(pvc, 'DataVolume', dv['metadata']['uid']) or owned(pvc, 'VirtualMachine', args.vm_uid)):
            raise RuntimeError('product disk ownership changed')
        if pvc['spec'].get('volumeMode') != 'Block': raise RuntimeError('product disk is not Block')
        volumes.append({'volume': volume['name'], 'dv': ref(dv), 'pvc': ref(pvc), 'volume_mode': 'Block', 'requested': pvc['spec']['resources']['requests']['storage'], 'capacity': pvc['status'].get('capacity')})
    if not volumes: raise RuntimeError('no product Block disk found')
    if hashlib.file_digest(VIRTCTL.open('rb'), 'sha256').hexdigest() != VIRTCTL_SHA: raise RuntimeError('official virtctl checksum differs')
    credential_ref = CREDENTIALS_DIR / args.credentials_ref
    credentials = json.loads(credential_ref.read_text())
    username, password = credentials['username'], credentials['password']
    if username != 'aniuser' or not password: raise RuntimeError('unexpected product credential identity')
    transcript = []
    child = pexpect.spawn(str(VIRTCTL), ['console', name, '--namespace', namespace, '--kubeconfig', K[1].split('=', 1)[1], '--timeout=20'], encoding='utf-8', timeout=30)
    envelope = {'at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'layer': 'GUEST', 'scope': 'real product VM serial observation; BROWSER is separate', 'product_receipt_reference': str(RUN / 'evidence' / args.product_receipt), 'private_credentials_reference': str(credential_ref), 'vm_id': vm_id, 'tenant_id': tenant_id, 'namespace': ref(ns), 'vm': ref(vm), 'vmi': ref(vmi), 'launcher': ref(pod), 'node': vmi['status'].get('nodeName'), 'native_uid': uid, 'volumes': volumes, 'vmi_interfaces': vmi['status'].get('interfaces', []), 'vm_networks': vm['spec']['template']['spec'].get('networks', []), 'launcher_network_status': pod['metadata'].get('annotations', {}).get('k8s.v1.cni.cncf.io/network-status'), 'virtctl_sha256': VIRTCTL_SHA, 'driver_sha256': hashlib.file_digest(pathlib.Path(__file__).open('rb'), 'sha256').hexdigest()}
    def expect(pattern):
        result = child.expect(pattern)
        transcript.extend([child.before, child.after if isinstance(child.after, str) else ''])
        return result
    try:
        expect(r'Successfully connected to .* console\. Press Ctrl\+\] or Ctrl\+5 to exit console\.')
        # A prior disconnected observer may leave its sudo prompt on this TTY.
        # Cancel only that foreground command before normal login/logout.
        child.sendcontrol('c')
        child.sendline('')
        if expect([r'login:', r'\$ ']) == 1:
            child.sendline('exit'); expect(r'login:')
        child.sendline(username); expect(r'[Pp]assword:'); child.sendline(password); expect(r'\$ ')
        child.sendline("sudo -v -p 'ANI_SUDO_PASSWORD: '")
        if expect([r'(?:^|[\r\n])ANI_SUDO_PASSWORD: ', r'\$ ']) == 0:
            child.sendline(password); expect(r'\$ ')
        code = '''import json, subprocess, os, pathlib
r = lambda p: pathlib.Path(p).read_text().strip()
j = lambda a: json.loads(subprocess.check_output(a))
m = pathlib.Path(MARKER_PATH)
ss = subprocess.run(["/usr/sbin/sshd", "-T"], capture_output=True, text=True) if pathlib.Path("/usr/sbin/sshd").exists() else None
sc = dict(line.split(" ", 1) for line in ss.stdout.splitlines() if " " in line) if ss and ss.returncode == 0 else {}
directives = []
for p in [pathlib.Path("/etc/ssh/sshd_config"), *sorted(pathlib.Path("/etc/ssh/sshd_config.d").glob("*.conf"))]:
    if not p.is_file(): continue
    for number, line in enumerate(p.read_text().splitlines(), 1):
        fields = line.strip().split()
        if fields and fields[0].lower() in {"include", "passwordauthentication", "pubkeyauthentication", "permitrootlogin", "kbdinteractiveauthentication", "match"}:
            directives.append({"file": str(p), "line": number, "directive": line.strip()})
safe = m.resolve().is_relative_to(pathlib.Path("/home/aniuser")) and not m.is_symlink() and m.is_file() and m.stat().st_size <= 4096
print(json.dumps({"username": os.environ.get("SUDO_USER"), "os_release": r("/etc/os-release"), "datasource": r("/var/lib/cloud/instance/datasource"), "nocloud_instance_id": r("/var/lib/cloud/data/instance-id"), "firmware_uuid": r("/sys/class/dmi/id/product_uuid"), "boot_id": r("/proc/sys/kernel/random/boot_id"), "addresses": j(["ip", "-j", "address"]), "routes": j(["ip", "-j", "route", "show", "table", "all"]), "disks": j(["lsblk", "-b", "-J", "-o", "NAME,SERIAL,TYPE,SIZE,FSTYPE,MOUNTPOINTS"]), "resolv_conf": r("/etc/resolv.conf"), "sshd_effective": {k: sc.get(k) for k in ["passwordauthentication", "pubkeyauthentication", "permitrootlogin"]}, "sshd_config_test": {"returncode": ss.returncode if ss else None, "stderr": ss.stderr[-1024:] if ss else "sshd unavailable"}, "sshd_directives": directives, "marker_path": str(m), "marker": m.read_text().strip() if safe else None}))'''.replace('MARKER_PATH', repr(args.marker_path))
        child.sendline('sudo -n python3 -c ' + shlex.quote(code)); expect(r'\$ ')
        values = []
        for line in child.before.splitlines():
            try:
                value = json.loads(line.strip())
                if isinstance(value, dict) and 'nocloud_instance_id' in value and 'addresses' in value: values.append(value)
            except json.JSONDecodeError: pass
        if len(values) != 1: raise RuntimeError('guest did not produce one complete read-only observation')
        observed = values[0]
        envelope['observed'] = observed
        if observed['username'] != username or observed['firmware_uuid'] != vm_id or observed['nocloud_instance_id'] != vm_id or not observed['datasource'].startswith('DataSourceNoCloud'):
            raise RuntimeError('product guest identity or NoCloud initialization differs')
        envelope['authentication_and_initialization'] = 'PASS'
        envelope['ssh_password_authentication_disabled'] = 'PASS' if observed['sshd_effective']['passwordauthentication'] == 'no' else 'NOT_VERIFIED'
        envelope['network'] = 'OBSERVED; no connectivity assertion'
        envelope['marker'] = 'NOT_RUN' if args.expected_marker is None else ('PASS' if observed['marker'] == args.expected_marker else 'FAIL')
        if envelope['marker'] == 'FAIL': raise RuntimeError('existing product marker differs from the expected value')
        envelope['result'] = 'PASS'
        child.sendline('exit'); expect(r'login:')
    except Exception as error:
        transcript.append(child.before)
        envelope['result'] = 'FAIL'
        envelope['error'] = str(error).replace(password, '[REDACTED]')[-2000:]
    finally:
        child.close(force=True)
        stamp = str(int(time.time()))
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / ('observe-' + stamp + '.json')).write_text(json.dumps(envelope, indent=2, ensure_ascii=False).replace(password, '[REDACTED]') + '\n')
        (OUT / ('console-' + stamp + '.txt')).write_text(''.join(transcript).replace(password, '[REDACTED]'))
    print(json.dumps({'result': envelope['result'], 'vm': ref(vm), 'vmi': ref(vmi), 'authentication_and_initialization': envelope.get('authentication_and_initialization'), 'network': envelope.get('network'), 'marker': envelope.get('marker'), 'receipt': str(OUT / ('observe-' + stamp + '.json'))}))
    return 0 if envelope['result'] == 'PASS' else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--vm-id', required=True)
    parser.add_argument('--vm-uid', required=True)
    parser.add_argument('--vmi-uid', required=True)
    parser.add_argument('--product-receipt', choices=['gov-linux-product.json', 'gov-linux-product-v2.json'], default='gov-linux-product.json')
    parser.add_argument('--credentials-ref', choices=['linux-guest-credentials.json', 'linux-guest-credentials-v2.json'], default='linux-guest-credentials.json')
    parser.add_argument('--marker-path', default='/home/aniuser/vm-mvp-marker')
    parser.add_argument('--expected-marker')
    args = parser.parse_args()
    for value in (args.vm_id, args.vm_uid, args.vmi_uid):
        if not re.fullmatch(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}', value): parser.error('native UID must be a UUID')
    if not args.marker_path.startswith('/home/aniuser/') or len(args.marker_path) > 256 or '\n' in args.marker_path or '\x00' in args.marker_path:
        parser.error('marker must be a bounded existing path under the product user home')
    if args.product_receipt.endswith('-v2.json') != args.credentials_ref.endswith('-v2.json'):
        parser.error('product receipt and private credentials must belong to the same intent')
    with open('/home/chabking/workspace/ani-network-service-runs/net05a-heavy.lock', 'a') as lock:
        deadline = time.monotonic() + 10
        while True:
            try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB); break
            except BlockingIOError:
                if time.monotonic() >= deadline: print('LOCK_BUSY'); return 73
                time.sleep(.25)
        return observe(args)


if __name__ == '__main__':
    raise SystemExit(main())
