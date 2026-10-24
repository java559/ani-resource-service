#!/usr/bin/env python3
"""One exact frame API call over the existing default libvirt connection."""
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import struct
import zlib
import sys
import time

RUN = Path('/home/chabking/workspace/ani-resource-service-runs/vm-mvp-20261023-01a168ce')
OUT = RUN / 'windows-black-screen-frame-02'
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
               'connection': 'existing default libvirt URI and default Pod user/authentication; only fixed screenshot API',
               'prior_read_only_rejection': 'windows-black-screen-frame-01/observed.json',
               'approval': 'Root explicit frame02 authorization: omit client -r only; no domain/Guest/QMP/configuration action',
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
        frame_args = ['exec', '-n', NS, POD, '-c', 'compute', '--',
                      'virsh', '-q', 'screenshot', DOMAIN, '/dev/stdout', '--screen', '0']
        assert frame_args == ['exec', '-n', 'tenant-b3c946b5-7eb3-4feb-9f82-79712062ca5c',
                              'virt-launcher-vm-fc154879-8a40-4b18-967c-215f5cf5992d-dg4kq',
                              '-c', 'compute', '--', 'virsh', '-q', 'screenshot',
                              'tenant-b3c946b5-7eb3-4feb-9f82-79712062ca5c_vm-fc154879-8a40-4b18-967c-215f5cf5992d',
                              '/dev/stdout', '--screen', '0'], 'fixed frame argv drift'
        receipt['screenshot_attempts'] = 1
        frame = command('single default-connection frame API', frame_args, budget=8)
        (OUT / 'screenshot.stderr').write_bytes(frame.stderr)
        receipt['screenshot'] = {'exit_code': frame.returncode, 'stdout_bytes': len(frame.stdout),
                                 'stderr': frame.stderr.decode(errors='replace')[:8192],
                                 'target': '/dev/stdout (stream only; no Pod file)', 'screen': 0}
        if frame.returncode:
            (OUT / 'screenshot-error.stdout').write_bytes(frame.stdout[:8192])
            receipt['result'] = 'FRAME_API_REJECTED'
            receipt['screenshot']['stdout'] = frame.stdout.decode(errors='replace')[:8192]
            receipt['limitations'].append('The sole approved screenshot call failed; no alternative command, QMP fallback or additional frame request was attempted.')
        else:
            assert len(frame.stdout) <= 16 * 1024 * 1024, 'unexpected frame size'
            raw = OUT / 'native-frame.bin'
            raw.write_bytes(frame.stdout)
            receipt['screenshot'].update({'path': str(raw), 'sha256': hashlib.sha256(frame.stdout).hexdigest(),
                                         'format': 'PNG' if frame.stdout.startswith(b'\x89PNG\r\n\x1a\n') else 'PPM' if frame.stdout.startswith((b'P6', b'P3')) else 'UNKNOWN'})
            png = OUT / 'native-frame.png'
            if frame.stdout.startswith(b'\x89PNG\r\n\x1a\n'):
                assert frame.stdout[12:16] == b'IHDR', 'PNG missing IHDR'
                width, height = struct.unpack('>II', frame.stdout[16:24])
                png.write_bytes(frame.stdout)
                conversion = 'none; native PNG bytes'
            else:
                data = frame.stdout
                position = 0
                def token():
                    nonlocal position
                    while position < len(data):
                        if data[position] in b' \t\r\n':
                            position += 1
                        elif data[position] == 35:
                            position = data.index(b'\n', position) + 1
                        else:
                            break
                    begin = position
                    while position < len(data) and data[position] not in b' \t\r\n#':
                        position += 1
                    return data[begin:position]
                assert token() == b'P6', 'native stream is neither PNG nor supported binary PPM'
                width, height, maximum = int(token()), int(token()), int(token())
                assert 0 < width <= 8192 and 0 < height <= 8192 and maximum == 255, 'unsupported PPM geometry/range'
                assert data[position] in b' \t\r\n', 'PPM missing raster separator'
                position += 2 if data[position:position+2] == b'\r\n' else 1
                pixels = data[position:]
                assert len(pixels) == width * height * 3, 'PPM raster length mismatch; no second screenshot'
                def chunk(kind, payload):
                    return struct.pack('>I', len(payload)) + kind + payload + struct.pack('>I', zlib.crc32(kind + payload) & 0xffffffff)
                stride = width * 3
                scanlines = b''.join(b'\0' + pixels[y*stride:(y+1)*stride] for y in range(height))
                png.write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
                                + chunk(b'IDAT', zlib.compress(scanlines)) + chunk(b'IEND', b''))
                conversion = 'lossless P6 RGB8 to PNG using Python standard-library zlib; pixels unchanged'
                receipt['pixel_summary'] = {'minimum_channel_value': min(pixels), 'maximum_channel_value': max(pixels),
                                            'all_channels_zero': not any(pixels)}
            assert width > 0 and height > 0, 'empty frame'
            receipt['png'] = {'path': str(png), 'sha256': hashlib.sha256(png.read_bytes()).hexdigest(),
                              'width': width, 'height': height, 'conversion': conversion}
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
