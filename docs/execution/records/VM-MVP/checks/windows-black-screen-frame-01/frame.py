#!/usr/bin/env python3
"""One exact Windows domain screenshot over the existing read-only connection."""
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

RUN = Path('/home/chabking/workspace/ani-resource-service-runs/vm-mvp-20261023-01a168ce')
OUT = RUN / 'windows-black-screen-frame-01'
K = ['/home/chabking/ani-four-service-deploy-20261012/tools/kubectl',
     '--kubeconfig=/home/chabking/ani-four-service-deploy-20261012/private/kubeconfig',
     '--request-timeout=5s']
NS = 'tenant-b3c946b5-7eb3-4feb-9f82-79712062ca5c'
NAME = 'vm-fc154879-8a40-4b18-967c-215f5cf5992d'
POD = 'virt-launcher-vm-fc154879-8a40-4b18-967c-215f5cf5992d-dg4kq'
VM_UID = '9a751320-5f97-4e56-af6a-30964610321b'
VMI_UID = 'fcacf24b-ed2a-468c-a503-ce19ffb95be7'
POD_UID = '059f02b2-d425-4fa5-8698-17a6b7a576d1'
DOMAIN = NS + '_' + NAME

def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def write_json(path, value):
    tmp = path.with_suffix(path.suffix + '.tmp')
    with tmp.open('w') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    tmp.replace(path)

def main():
    assert os.environ.get('SSH_CONNECTION', '').split()[2:3] == ['172.16.101.31'], 'Fedora SSH host required'
    lock = open('/home/chabking/workspace/ani-network-service-runs/net05a-heavy.lock', 'a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print('BUSY: no screenshot attempted')
        return 73
    OUT.mkdir(parents=True, exist_ok=True)
    receipt_path = OUT / 'observed.json'
    assert not receipt_path.exists(), 'single-use output already exists'
    started = time.monotonic()
    deadline = started + 20
    receipt = {'started_at': now(), 'execution_host': 'fedora/172.16.101.31',
               'result': 'NOT_MEASURED', 'commands': [], 'identity': {},
               'screenshot_attempts': 0, 'guest_commands': 0, 'guest_input': 0,
               'cluster_writes': 0, 'vm_or_service_changes': 0,
               'pod_temporary_files_created': 0,
               'connection': 'existing default libvirt URI with virsh -r',
               'limitations': ['A native frame is diagnostic evidence only; it does not prove installation or guest acceptance.']}

    def command(stage, args, budget=5):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('overall 20-second budget exhausted before ' + stage)
        before = time.monotonic()
        try:
            p = subprocess.run(K + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               timeout=min(remaining, budget), check=False)
        except subprocess.TimeoutExpired as exc:
            receipt['commands'].append({'stage': stage, 'argv': args, 'result': 'TIMEOUT',
                                         'elapsed_seconds': round(time.monotonic() - before, 3)})
            if exc.stderr:
                (OUT / (stage.replace(' ', '-') + '.stderr')).write_bytes(exc.stderr[:8192])
            raise
        receipt['commands'].append({'stage': stage, 'argv': args, 'exit_code': p.returncode,
                                     'elapsed_seconds': round(time.monotonic() - before, 3)})
        return p

    def get(stage, resource, name, uid, namespace=None):
        args = ['get', resource, name]
        if namespace:
            args += ['-n', namespace]
        p = command(stage, args + ['-o', 'json'])
        if p.returncode:
            raise RuntimeError(stage + ': ' + p.stderr.decode(errors='replace')[:2000])
        value = json.loads(p.stdout)
        meta = value['metadata']
        assert meta['uid'] == uid and meta['name'] == name and not meta.get('deletionTimestamp'), stage + ' identity drift'
        if namespace:
            assert meta['namespace'] == namespace, stage + ' namespace drift'
        receipt['identity'][stage] = {'name': name, 'uid': uid, 'resource_version': meta['resourceVersion']}
        return value

    def controller(value, kind, uid):
        owners = [x for x in value['metadata'].get('ownerReferences', []) if x.get('controller')]
        assert len(owners) == 1 and owners[0]['kind'] == kind and owners[0]['uid'] == uid, 'controller identity drift'

    try:
        get('cluster', 'namespace', 'kube-system', '5277649d-e28d-4a0a-ae76-8354365c63bc')
        get('namespace', 'namespace', NS, '09e58ecb-fe20-454f-bc7b-2da3c406f0ac')
        get('native VM', 'virtualmachine', NAME, VM_UID, NS)
        vmi = get('VMI', 'virtualmachineinstance', NAME, VMI_UID, NS)
        pod = get('primary launcher', 'pod', POD, POD_UID, NS)
        node = get('node', 'node', 'ani-01', '201e41ac-c50d-4c77-b118-72d1ed93e111')
        controller(vmi, 'VirtualMachine', VM_UID)
        controller(pod, 'VirtualMachineInstance', VMI_UID)
        assert vmi['status']['phase'] == 'Running' and vmi['status']['nodeName'] == 'ani-01', 'VMI not current Running on expected node'
        assert pod['status']['phase'] == 'Running' and pod['spec']['nodeName'] == 'ani-01', 'launcher placement drift'
        assert any(a['type'] == 'InternalIP' and a['address'] == '172.16.101.10' for a in node['status']['addresses']), 'node address drift'
        compute = [x for x in pod['status'].get('containerStatuses', []) if x['name'] == 'compute']
        assert len(compute) == 1 and compute[0].get('state', {}).get('running') and compute[0]['restartCount'] == 0, 'compute not original running container'
        receipt['compute'] = {key: compute[0].get(key) for key in ('containerID', 'imageID', 'ready', 'restartCount')}
        prefix = ['exec', '-n', NS, POD, '-c', 'compute', '--', 'virsh', '-r', '-q']
        help_result = command('installed screenshot help', prefix + ['help', 'screenshot'])
        (OUT / 'virsh-screenshot-help.stdout').write_bytes(help_result.stdout)
        (OUT / 'virsh-screenshot-help.stderr').write_bytes(help_result.stderr)
        assert help_result.returncode == 0 and b'--screen' in help_result.stdout and b'screenshot' in help_result.stdout, 'installed screenshot command unavailable'
        receipt['screenshot_attempts'] = 1
        frame = command('single read-only screenshot', prefix + ['screenshot', DOMAIN, '/dev/stdout', '--screen', '0'], budget=8)
        (OUT / 'screenshot.stderr').write_bytes(frame.stderr)
        receipt['screenshot'] = {'exit_code': frame.returncode, 'stdout_bytes': len(frame.stdout),
                                 'stderr': frame.stderr.decode(errors='replace')[:8192],
                                 'target': '/dev/stdout (stream only; no Pod file)', 'screen': 0}
        if frame.returncode:
            (OUT / 'screenshot-error.stdout').write_bytes(frame.stdout[:8192])
            receipt['result'] = 'READ_ONLY_SCREENSHOT_REJECTED'
            receipt['screenshot']['stdout'] = frame.stdout.decode(errors='replace')[:8192]
            receipt['limitations'].append('Read-only screenshot failed; no read-write connection, QMP fallback or additional frame request was attempted.')
        else:
            assert len(frame.stdout) <= 16 * 1024 * 1024, 'unexpected frame size'
            raw = OUT / 'native-frame.bin'
            raw.write_bytes(frame.stdout)
            receipt['screenshot'].update({'path': str(raw), 'sha256': hashlib.sha256(frame.stdout).hexdigest(),
                                         'format': 'PNG' if frame.stdout.startswith(b'\x89PNG\r\n\x1a\n') else 'PPM' if frame.stdout.startswith((b'P6', b'P3')) else 'UNKNOWN'})
            receipt['result'] = 'NATIVE_FRAME_CAPTURED_PENDING_VISUAL_INSPECTION'
    except Exception as exc:
        receipt['result'] = 'NOT_MEASURED'
        receipt['error'] = type(exc).__name__ + ': ' + str(exc)[:3000]
    finally:
        receipt['elapsed_seconds'] = round(time.monotonic() - started, 3)
        receipt['finished_at'] = now()
        receipt['cleanup'] = {'pod_temporary_files_created': 0, 'pod_temporary_files_remaining': 0,
                              'cluster_resources_created': 0, 'method': 'stdout stream only'}
        write_json(receipt_path, receipt)
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()
    print(json.dumps({'result': receipt['result'], 'elapsed_seconds': receipt['elapsed_seconds'],
                      'receipt': str(receipt_path), 'screenshot_attempts': receipt['screenshot_attempts']}))
    return 0

if __name__ == '__main__':
    sys.exit(main())
