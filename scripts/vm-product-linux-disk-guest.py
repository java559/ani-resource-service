#!/usr/bin/env python3
"""Fedora serial driver for markers on this task's product-created data disks.

Writes are restricted to an owned, attached, unformatted, unmounted SCSI data
disk identified by the real Gov receipt, native UID chain and guest serial.
This is guest evidence; it never creates disks or changes VM configuration.
"""
import argparse
import datetime
import fcntl
import hashlib
import importlib.util
import json
import os
import pathlib
import re
import shlex
import subprocess
import time

spec = importlib.util.spec_from_file_location('guest_observer', pathlib.Path(__file__).with_name('vm-product-guest-observe.py'))
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)
VM = '55099440-c47b-4f81-afc5-7d8e740d14dc'
VM_UID = '7bca4671-89dd-4925-bad9-17e77401729d'
NS = 'tenant-b3c946b5-7eb3-4feb-9f82-79712062ca5c'
NS_UID = '09e58ecb-fe20-454f-bc7b-2da3c406f0ac'


def run(args):
    import pexpect
    connection = os.environ.get('SSH_CONNECTION', '').split()
    if len(connection) != 4 or connection[2] != '172.16.101.31':
        raise RuntimeError('execute through ssh fedora only')
    if g.get('namespace', 'kube-system', '')['metadata']['uid'] != '5277649d-e28d-4a0a-ae76-8354365c63bc' or g.get('namespace', NS, '')['metadata']['uid'] != NS_UID:
        raise RuntimeError('cluster/tenant namespace identity changed')
    receipt = g.RUN / 'evidence' / ('gov-linux-disk-v3-' + args.intent + '.json')
    accepted = json.loads(receipt.read_text())
    command = accepted['command']
    if accepted['vm_id'] != VM or command['operation']['state'] != 'succeeded':
        raise RuntimeError('disk operation not confirmed by real product receipt')
    disk = next(d for d in command['disks'] if d['diskId'] == args.disk_id)
    if disk['role'] != 'data' or not disk['attached'] or disk['serial'] != args.disk_id:
        raise RuntimeError('only an attached product data disk is allowed')
    vm = g.get('vm', 'vm-' + VM, NS)
    vmi = g.get('vmi', 'vm-' + VM, NS)
    if vm['metadata']['uid'] != VM_UID or vmi['metadata']['uid'] != args.vmi_uid or not g.owned(vmi, 'VirtualMachine', VM_UID) or vmi.get('status', {}).get('phase') != 'Running':
        raise RuntimeError('product native VM/VMI identity changed')
    if vmi['spec']['domain']['firmware']['uuid'] != VM:
        raise RuntimeError('native guest identity differs')
    template = vm['spec']['template']['spec']
    device = next(d for d in template['domain']['devices']['disks'] if d['name'] == disk['volumeName'])
    volume = next(v for v in template['volumes'] if v['name'] == disk['volumeName'])
    if device.get('serial') != disk['serial'] or device.get('disk', {}).get('bus') != 'scsi':
        raise RuntimeError('native SCSI/serial contract differs')
    dv = None
    if 'dataVolume' in volume:
        dv = g.get('dv', volume['dataVolume']['name'], NS)
        if not g.owned(dv, 'VirtualMachine', VM_UID):
            raise RuntimeError('data DV owner differs')
        pvc = g.get('pvc', dv['metadata']['name'], NS)
        if not (g.owned(pvc, 'DataVolume', dv['metadata']['uid']) or g.owned(pvc, 'VirtualMachine', VM_UID)):
            raise RuntimeError('data PVC owner differs')
    else:
        pvc = g.get('pvc', volume['persistentVolumeClaim']['claimName'], NS)
        if not g.owned(pvc, 'VirtualMachine', VM_UID):
            raise RuntimeError('restored PVC owner differs')
    if pvc['spec'].get('volumeMode') != 'Block' or pvc['spec'].get('accessModes') != ['ReadWriteOnce'] or pvc.get('status', {}).get('phase') != 'Bound':
        raise RuntimeError('data disk is not Bound Block/RWO')
    pv = g.get('pv', pvc['spec']['volumeName'], '')
    if pv['spec'].get('claimRef', {}).get('uid') != pvc['metadata']['uid'] or pv['spec'].get('persistentVolumeReclaimPolicy') != 'Delete':
        raise RuntimeError('data PV claim identity/policy differs')
    active_volume = next(v for v in vmi['spec']['volumes'] if v['name'] == disk['volumeName'])
    if active_volume.get('persistentVolumeClaim', {}).get('claimName') not in (None, pvc['metadata']['name']):
        raise RuntimeError('current VMI uses a different PVC')
    volume_status = [v for v in vmi.get('status', {}).get('volumeStatus', []) if v['name'] == disk['volumeName']]
    if len(volume_status) != 1 or volume_status[0].get('phase') != 'Ready':
        raise RuntimeError('native VMI data volume is not Ready')
    if not any(active_volume.get(k, {}).get('hotpluggable') is True for k in ('persistentVolumeClaim', 'dataVolume')):
        raise RuntimeError('native VMI data volume lost hotpluggable')
    if hashlib.file_digest(g.VIRTCTL.open('rb'), 'sha256').hexdigest() != g.VIRTCTL_SHA:
        raise RuntimeError('official virtctl checksum differs')
    credentials = json.loads((g.CREDENTIALS_DIR / 'linux-guest-credentials-v3.json').read_text())
    password = credentials['password']
    result = {'vm_id': VM, 'vm': g.ref(vm), 'vmi': g.ref(vmi), 'disk_id': args.disk_id, 'serial': disk['serial'], 'mode': args.action, 'marker': args.marker, 'pvc': g.ref(pvc), 'pv': g.ref(pv), 'method': 'NATIVE_SERIAL', 'product_receipt': str(receipt), 'product_receipt_sha256': hashlib.sha256(receipt.read_bytes()).hexdigest(), 'at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'result': 'FAIL'}
    stamp = str(time.time_ns())
    output = g.RUN / 'evidence/product-guest' / ('data-disk-' + stamp + '.json')
    transcript = []
    child = pexpect.spawn(str(g.VIRTCTL), ['console', vm['metadata']['name'], '--namespace', NS, '--kubeconfig', g.K[1].split('=', 1)[1], '--timeout=20'], encoding='utf-8', timeout=40)
    def expect(pattern):
        value = child.expect(pattern)
        transcript.extend([child.before, child.after if isinstance(child.after, str) else ''])
        return value
    try:
        expect(r'Successfully connected to .* console\. Press Ctrl\+\] or Ctrl\+5 to exit console\.')
        child.sendcontrol('c')
        child.sendline('')
        if expect([r'login:', r'\$ ']) == 1:
            child.sendline('exit')
            expect(r'login:')
        child.sendline(credentials['username'])
        expect(r'[Pp]assword:')
        child.sendline(password)
        expect(r'\$ ')
        child.sendline("sudo -v -p 'ANI_SUDO_PASSWORD: '")
        if expect([r'(?:^|[\r\n])ANI_SUDO_PASSWORD: ', r'\$ ']) == 0:
            child.sendline(password)
            expect(r'\$ ')
        guest = r'''import json, os, pathlib, re, stat, subprocess
serial, expected_vm, requested, mode, marker = INPUT
read = lambda p: pathlib.Path(p).read_text().strip()
assert read('/sys/class/dmi/id/product_uuid') == expected_vm
all_disks = json.loads(subprocess.check_output(['lsblk','-b','-J','-o','NAME,PATH,SERIAL,TYPE,SIZE,FSTYPE,MOUNTPOINTS']))['blockdevices']
matches = [d for d in all_disks if d['type'] == 'disk' and d.get('serial') == serial]
assert len(matches) == 1
d = matches[0]
assert not d.get('children') and not d.get('fstype') and not any(d.get('mountpoints') or [])
assert int(d['size']) >= requested and re.fullmatch(r'sd[a-z]+',d['name'])
assert not list(pathlib.Path('/sys/class/block',d['name'],'holders').iterdir())
path = d['path']
assert path == '/dev/' + d['name'] and stat.S_ISBLK(os.stat(path).st_mode)
expected = (marker + '\n').encode()
if mode == 'write':
    fd = os.open(path, os.O_WRONLY | os.O_SYNC | os.O_NOFOLLOW)
    try:
        assert os.write(fd, expected.ljust(512,b'\0')) == 512
        os.fsync(fd)
    finally: os.close(fd)
subprocess.run(['blockdev','--flushbufs',path], check=True)
fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
try: actual = os.read(fd,512).split(b'\0',1)[0]
finally: os.close(fd)
assert actual == expected
print(json.dumps({'disk_id':serial,'serial':serial,'device':path,'size_bytes':int(d['size']),'marker':actual.decode().strip(),'boot_id':read('/proc/sys/kernel/random/boot_id'),'flushed':True,'offline_or_unmounted':True,'guest_prepared_confirmation':mode == 'prepare','result':'PASS'}))
'''.replace('INPUT', repr((disk['serial'], VM, int(disk['requestedBytes']), args.action, args.marker)))
        child.sendline('sudo -n python3 -c ' + shlex.quote(guest))
        expect(r'\$ ')
        values = []
        for line in child.before.splitlines():
            try:
                value = json.loads(line.strip())
                if isinstance(value, dict) and value.get('disk_id') == args.disk_id:
                    values.append(value)
            except json.JSONDecodeError:
                pass
        if len(values) != 1 or values[0].get('result') != 'PASS':
            raise RuntimeError('guest did not confirm the exact data disk and marker')
        result.update(values[0])
        result['supporting_evidence'] = {'vm': g.ref(vm), 'vmi': g.ref(vmi), 'pvc': g.ref(pvc), 'pv': g.ref(pv), 'native_volume': active_volume, 'native_volume_status': volume_status[0], 'native_device': device, 'transcript': str(output.with_suffix('.txt'))}
        child.sendline('exit')
        expect(r'login:')
    except Exception as error:
        transcript.append(child.before)
        result['error'] = str(error).replace(password, '[REDACTED]')[-2000:]
    finally:
        child.close(force=True)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2).replace(password, '[REDACTED]') + '\n')
        output.with_suffix('.txt').write_text(''.join(transcript).replace(password, '[REDACTED]'))
    print(json.dumps({'result': result['result'], 'vm_id': VM, 'disk_id': args.disk_id, 'action': args.action, 'receipt': str(output)}))
    return 0 if result['result'] == 'PASS' else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['write','read','prepare'])
    parser.add_argument('--intent', required=True)
    parser.add_argument('--disk-id', required=True)
    parser.add_argument('--vmi-uid', required=True)
    parser.add_argument('--marker', required=True)
    args = parser.parse_args()
    if not re.fullmatch(r'[a-zA-Z0-9-]{1,80}', args.intent) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,120}', args.marker):
        parser.error('bounded task intent/marker required')
    for value in (args.disk_id, args.vmi_uid):
        if not re.fullmatch(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}', value):
            parser.error('exact UUID required')
    with pathlib.Path('/home/chabking/workspace/ani-network-service-runs/net05a-heavy.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return run(args)


if __name__ == '__main__':
    raise SystemExit(main())
