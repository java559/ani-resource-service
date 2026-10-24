#!/usr/bin/env python3
"""Import only the three user-confirmed ISO files, on Fedora, through CDI.

This prepares administrator media sources; VM creation still uses product APIs.
No source images are modified and no shared component configuration is changed.
"""
import argparse
import base64
import contextlib
import datetime
import fcntl
import hashlib
import importlib.util
import json
import os
import pathlib
import re
import socket
import subprocess
import time

spec = importlib.util.spec_from_file_location('native_probe', pathlib.Path(__file__).with_name('vm-native-probe.py'))
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)
ROOT = p.RUN
p.OUT = ROOT / 'evidence' / 'media-import'
p.PRIVATE = ROOT / 'private' / 'media-import'
SOURCES = {
    'fedora44': ('vm-mvp-fedora44-iso-source', '/home/chabking/Fedora-KDE-Desktop-Live-44-1.7.x86_64.iso', 3368683520, 'c8295961d4c41adbf785a31a17c21a971d3b7415fda72dcad0c11c49577bf03a', '5G'),
    'windows2025': ('vm-mvp-windows2025-iso-source', '/home/chabking/zh-cn_windows_server_2025_updated_may_2026_x64_dvd_adcb5eb0.iso', 8726618112, '33b9980c5aaa5bc40aedf20cf9d15f32edafcb6ed883049255713535a61cc40b', '12G'),
    'virtio': ('vm-mvp-virtio-iso-source', '/home/chabking/virtio-win-0.1.271.iso', 726501376, 'bbe6166ad86a490caefad438fef8aa494926cb0a1b37fa1212925cfd81656429', '2G'),
}
SC_UID = 'e7813a57-3d1c-47e4-a298-86212a59e51e'


def verify_scope():
    connection = os.environ.get('SSH_CONNECTION', '').split()
    if len(connection) != 4 or connection[2] != '172.16.101.31':
        raise RuntimeError('execute through ssh fedora only')
    p.assert_cluster()
    ns = p.get('namespace', p.NS, '')
    if ns['metadata']['uid'] != '92ab28f6-ec74-47a0-a08e-88c599a85189':
        raise RuntimeError('source namespace identity changed')
    sc = p.get('storageclass', 'ani-block', '')
    if sc['metadata']['uid'] != SC_UID or sc.get('reclaimPolicy') != 'Delete' or sc.get('provisioner') != 'rook-ceph.rbd.csi.ceph.com':
        raise RuntimeError('source RBD storage identity/policy changed')


def source_objects(key):
    name = SOURCES[key][0]
    dv = p.get('dv', name)
    registered = json.loads((p.OUT / 'objects.json').read_text())
    if registered.get('DataVolume/' + p.NS + '/' + name, {}).get('uid') != dv['metadata']['uid']:
        raise RuntimeError('unregistered/replaced source DV')
    pvc = p.get('pvc', name)
    owners = pvc['metadata'].get('ownerReferences', [])
    if not any(o.get('kind') == 'DataVolume' and o.get('uid') == dv['metadata']['uid'] for o in owners):
        raise RuntimeError('source PVC not owned by exact DV')
    if pvc['spec'].get('volumeMode', 'Filesystem') != 'Filesystem' or pvc['spec'].get('storageClassName') != 'ani-block' or pvc['spec'].get('accessModes') != ['ReadWriteOnce']:
        raise RuntimeError('ISO source must use its frozen Filesystem/RWO contract')
    p.register(pvc)
    return dv, pvc


def prepare(key):
    name, path, size, digest, capacity = SOURCES[key]
    file = pathlib.Path(path)
    if file.stat().st_size != size or hashlib.file_digest(file.open('rb'), 'sha256').hexdigest() != digest:
        raise RuntimeError('confirmed ISO bytes changed')
    obj = {'apiVersion': 'cdi.kubevirt.io/v1beta1', 'kind': 'DataVolume', 'metadata': {'name': name, 'namespace': p.NS}, 'spec': {'source': {'upload': {}}, 'contentType': 'kubevirt', 'storage': {'storageClassName': 'ani-block', 'volumeMode': 'Filesystem', 'accessModes': ['ReadWriteOnce'], 'resources': {'requests': {'storage': capacity}}}}}
    dv = p.create(obj)
    if dv['spec'] != obj['spec']:
        # CDI may add defaults; every field this task supplied must remain exact.
        if any(dv['spec'].get(k) != v for k, v in obj['spec'].items()):
            raise RuntimeError('source DV frozen spec changed')
    p.save(key + '-input', {'file': path, 'file_bytes': size, 'input_sha256': digest, 'source': p.identity(dv), 'requested': capacity, 'storage_class': 'ani-block', 'storage_class_uid': SC_UID, 'mode': 'Filesystem', 'purpose': 'read-only ISO source; no VM root/data disk'})
    print(json.dumps({'source': p.identity(dv), 'action': 'prepared'}))


def upload(key):
    name, path, size, digest, _ = SOURCES[key]
    dv, pvc = source_objects(key)
    if dv.get('status', {}).get('phase') == 'Succeeded':
        print(json.dumps({'source': p.identity(dv), 'action': 'already_uploaded_not_repeated'}))
        return
    receipt = p.OUT / (key + '-upload.json')
    if receipt.exists():
        raise RuntimeError('a previous upload attempt exists; observe native outcome before any retry')
    prime = pvc
    if pvc['spec'].get('dataSourceRef', {}).get('kind') == 'VolumeUploadSource':
        prime = p.get('pvc', 'prime-' + pvc['metadata']['uid'])
    if prime['metadata'].get('annotations', {}).get('cdi.kubevirt.io/storage.pod.ready') != 'true':
        raise RuntimeError('native upload server not ready; use status')
    file = pathlib.Path(path)
    if file.stat().st_size != size or hashlib.file_digest(file.open('rb'), 'sha256').hexdigest() != digest:
        raise RuntimeError('confirmed ISO bytes changed before upload')
    ca = p.get('cm', 'cdi-uploadproxy-signer-bundle', 'cdi')['data']['ca-bundle.crt']
    capath = p.PRIVATE / (key + '-ca.crt')
    capath.write_text(ca)
    capath.chmod(0o600)
    token = json.loads(p.command(['create', '-f', '-', '-o', 'json'], {'apiVersion': 'upload.cdi.kubevirt.io/v1beta1', 'kind': 'UploadTokenRequest', 'metadata': {'name': name, 'namespace': p.NS}, 'spec': {'pvcName': name}}).stdout)['status']['token']
    header = p.PRIVATE / (key + '-header')
    header.write_text('Authorization: Bearer ' + token + '\n')
    header.chmod(0o600)
    certdoc = json.loads(p.command(['get', 'secret', 'cdi-uploadproxy-server-cert', '-n', 'cdi', '-o', 'json']).stdout)
    cert = base64.b64decode(certdoc['data']['tls.crt']).decode()
    names = subprocess.check_output(['openssl', 'x509', '-noout', '-ext', 'subjectAltName'], input=cert, text=True)
    dns = next(x for x in re.findall(r'DNS:([^,\s]+)', names) if '*' not in x)
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    port = listener.getsockname()[1]
    listener.close()
    log = (p.OUT / (key + '-forward.log')).open('w')
    forward = subprocess.Popen(p.K + ['port-forward', '-n', 'cdi', 'service/cdi-uploadproxy', str(port) + ':443', '--address=127.0.0.1'], stdout=log, stderr=subprocess.STDOUT)
    p.save(key + '-upload', {'state': 'attempt_started', 'datavolume': p.identity(dv), 'pvc': p.identity(pvc), 'input_sha256': digest, 'at': datetime.datetime.now(datetime.timezone.utc).isoformat()})
    try:
        for _ in range(50):
            try:
                with socket.create_connection(('127.0.0.1', port), timeout=.2):
                    break
            except OSError:
                time.sleep(.2)
        else:
            raise RuntimeError('CDI forward not ready')
        result = subprocess.run(['curl', '--silent', '--show-error', '--fail-with-body', '--noproxy', '*', '--cacert', str(capath), '--resolve', dns + ':' + str(port) + ':127.0.0.1', '--header', '@' + str(header), '--data-binary', '@' + path, '--max-time', '600', '--output', str(p.OUT / (key + '-response.txt')), '--write-out', '%{http_code}', 'https://' + dns + ':' + str(port) + '/v1beta1/upload-async'], capture_output=True, text=True)
        p.save(key + '-upload', {'state': 'response_received', 'returncode': result.returncode, 'http_status': result.stdout, 'tls_verified': result.returncode == 0, 'tls_hostname': dns, 'input_sha256': digest, 'datavolume': p.identity(dv), 'pvc': p.identity(pvc), 'stderr': result.stderr[-1200:]})
        print(json.dumps({'source': name, 'returncode': result.returncode, 'http_status': result.stdout, 'tls_verified': result.returncode == 0}))
        if result.returncode:
            raise RuntimeError('upload outcome needs native observation; no blind retry')
    finally:
        forward.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            forward.wait(timeout=5)
        if forward.poll() is None:
            forward.kill()
            forward.wait()
        log.close()
        header.unlink(missing_ok=True)


def status(key):
    dv, pvc = source_objects(key)
    result = {'datavolume': p.identity(dv), 'phase': dv.get('status', {}).get('phase'), 'pvc': p.identity(pvc), 'pvc_phase': pvc.get('status', {}).get('phase'), 'capacity': pvc.get('status', {}).get('capacity'), 'conditions': dv.get('status', {}).get('conditions', [])}
    if pvc['spec'].get('volumeName'):
        pv = p.get('pv', pvc['spec']['volumeName'], '')
        if pv['spec'].get('claimRef', {}).get('uid') != pvc['metadata']['uid'] or pv['spec'].get('persistentVolumeReclaimPolicy') != 'Delete':
            raise RuntimeError('source PV identity/policy mismatch')
        result['pv'] = p.identity(pv)
        result['csi'] = pv['spec'].get('csi')
    p.save(key + '-status', result)
    print(json.dumps(result))


def hash_source(key):
    dv, pvc = source_objects(key)
    if dv.get('status', {}).get('phase') != 'Succeeded' or pvc.get('status', {}).get('phase') != 'Bound':
        raise RuntimeError('source not ready')
    name = SOURCES[key][0] + '-hash'
    pod = p.create({'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {'name': name, 'namespace': p.NS}, 'spec': {'nodeSelector': {'kubernetes.io/hostname': 'ani-03'}, 'restartPolicy': 'Never', 'securityContext': {'runAsUser': 107, 'runAsGroup': 107, 'runAsNonRoot': True, 'seccompProfile': {'type': 'RuntimeDefault'}}, 'containers': [{'name': 'hash', 'image': '172.16.101.10:5000/kubevirt/cdi-importer:v1.66.1', 'imagePullPolicy': 'IfNotPresent', 'command': ['/bin/sh', '-ec', 'test "$(id -u)" = 107; test -f /source/disk.img; stat -c "BYTES=%s" /source/disk.img; sha256sum /source/disk.img'], 'securityContext': {'allowPrivilegeEscalation': False, 'capabilities': {'drop': ['ALL']}}, 'volumeMounts': [{'name': 'source', 'mountPath': '/source', 'readOnly': True}]}], 'volumes': [{'name': 'source', 'persistentVolumeClaim': {'claimName': pvc['metadata']['name'], 'readOnly': True}}]}})
    p.save(key + '-hash-input', {'pod': p.identity(pod), 'datavolume': p.identity(dv), 'pvc': p.identity(pvc), 'read_only': True})
    print(json.dumps({'hash_requested': p.identity(pod)}))


def hash_result(key):
    frozen = json.loads((p.OUT / (key + '-hash-input.json')).read_text())
    dv, pvc = source_objects(key)
    pod = p.get('pod', frozen['pod']['name'])
    if p.identity(dv) != frozen['datavolume'] or p.identity(pvc) != frozen['pvc'] or p.identity(pod) != frozen['pod']:
        raise RuntimeError('source/hash object replacement')
    if pod.get('status', {}).get('phase') != 'Succeeded':
        raise RuntimeError('read-only hash has not completed')
    logs = p.command(['logs', pod['metadata']['name'], '-n', p.NS, '-c', 'hash']).stdout
    digests = re.findall(r'^([a-f0-9]{64})\s+/source/disk.img\s*$', logs, re.M)
    lengths = re.findall(r'^BYTES=([0-9]+)$', logs, re.M)
    if len(digests) != 1 or len(lengths) != 1 or int(lengths[0]) < SOURCES[key][2]:
        raise RuntimeError('source hash/size not authoritative')
    digest = digests[0]
    old = pvc['metadata'].get('annotations', {}).get('vm.ani.io/source-sha256')
    if old and old != digest:
        raise RuntimeError('source digest changed')
    patch = [{'op': 'test', 'path': '/metadata/uid', 'value': pvc['metadata']['uid']}, {'op': 'test', 'path': '/metadata/resourceVersion', 'value': pvc['metadata']['resourceVersion']}, {'op': 'add', 'path': '/metadata/annotations/vm.ani.io~1source-sha256', 'value': digest}]
    p.command(['patch', 'pvc', pvc['metadata']['name'], '-n', p.NS, '--type=json', '-p', json.dumps(patch)])
    p.save(key + '-ready', {'datavolume': p.identity(dv), 'pvc': p.identity(pvc), 'checksum': digest, 'source_file_sha256': SOURCES[key][3], 'source_file_bytes': SOURCES[key][2], 'actual_disk_file_bytes': int(lengths[0]), 'mode': 'Filesystem', 'content_type': 'kubevirt', 'read_only_hash': True})
    p.delete_exact(p.identity(pod))
    print(json.dumps({'source': p.identity(pvc), 'checksum': digest, 'actual_disk_file_bytes': int(lengths[0]), 'hash_pod_delete_requested': True}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'upload', 'status', 'hash', 'hash-result'])
    parser.add_argument('source', choices=SOURCES)
    args = parser.parse_args()
    p.OUT.mkdir(parents=True, exist_ok=True)
    p.PRIVATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    with pathlib.Path('/home/chabking/workspace/ani-network-service-runs/net05a-heavy.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        verify_scope()
        {'prepare': prepare, 'upload': upload, 'status': status, 'hash': hash_source, 'hash-result': hash_result}[args.action](args.source)


if __name__ == '__main__':
    main()
