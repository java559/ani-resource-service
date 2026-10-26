#!/usr/bin/env python3
"""Task-only native Block probes. Execute on Fedora under the shared lock.

This is V02 environment evidence, never ANI product acceptance. Credentials
remain on Fedora; public output records immutable identities, never token data.
"""
import argparse
import base64
import contextlib
import datetime
import fcntl
import hashlib
import json
import os
import pathlib
import re
import secrets
import socket
import subprocess
import time

RUN = pathlib.Path('/home/chabking/workspace/ani-resource-service-runs/vm-mvp-20261023-01a168ce')
OUT = RUN / 'evidence/native-block'
PRIVATE = RUN / 'private/native-block'
NS = 'vm-mvp-probe-01a168ce'
K = ['/home/chabking/ani-four-service-deploy-20261012/tools/kubectl', '--kubeconfig=/home/chabking/ani-four-service-deploy-20261012/private/kubeconfig']
SOURCE = 'vm-mvp-ubuntu-2404-source'
IMAGE = pathlib.Path('/home/chabking/ubuntu-24.04-server-cloudimg-amd64.img')
IMAGE_SHA = '04e8f2461a16a44e78a370284471f29616cdd9baf1893df25807b71f985b6c94'
VIRTCTL_SHA = '40ede2ee37c98a1aeed71c9c219616a05247ce2be109e1edddf0477572e8b978'
VIRTCTL_CHECKSUM_SOURCE = 'https://github.com/kubevirt/kubevirt/releases/expanded_assets/v1.9.0'
NODES = {'ani-01': '172.16.101.10', 'ani-02': '172.16.101.11', 'ani-03': '172.16.101.12'}
NODE_UIDS = {'ani-01': '201e41ac-c50d-4c77-b118-72d1ed93e111', 'ani-02': '73564fa7-98ad-45d4-9576-be59f6d5d245', 'ani-03': 'af992ed9-cbe1-4f64-8fac-7433808bfab2'}
LABELS = {'ani.io/task': 'vm-mvp-01a168ce', 'app.kubernetes.io/managed-by': 'ani-vm-native-probe'}


def command(args, data=None, check=True):
    r = subprocess.run(K + args, input=None if data is None else json.dumps(data), text=True, capture_output=True)
    if check and r.returncode:
        raise RuntimeError('kubectl ' + ' '.join(args[:3]) + ': ' + r.stderr[-1600:])
    return r


def get(kind, name=None, namespace=NS, optional=False):
    args = ['get', kind]
    if name: args.append(name)
    if namespace: args += ['-n', namespace]
    r = command(args + ['-o', 'json'], check=not optional)
    if r.returncode:
        if optional and '(NotFound)' in r.stderr: return None
        raise RuntimeError('unconfirmed API read for ' + kind + ': ' + r.stderr[-1200:])
    return json.loads(r.stdout)


def save(name, value):
    (OUT / (name + '.json')).write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')


def identity(obj):
    m = obj['metadata']
    return {'apiVersion': obj['apiVersion'], 'kind': obj['kind'], 'namespace': m.get('namespace', ''), 'name': m['name'], 'uid': m['uid']}


def register(obj):
    state = json.loads((OUT / 'objects.json').read_text()) if (OUT / 'objects.json').exists() else {}
    v = identity(obj)
    key = v['kind'] + '/' + v['namespace'] + '/' + v['name']
    if key in state and state[key]['uid'] != v['uid']:
        raise RuntimeError('task object identity changed: ' + key)
    state[key] = v
    save('objects', state)
    return obj


def create(obj):
    kind = obj['kind']
    existing = get(kind, obj['metadata']['name'], obj['metadata'].get('namespace', ''), optional=True)
    if existing:
        state = json.loads((OUT / 'objects.json').read_text()) if (OUT / 'objects.json').exists() else {}
        ident = identity(existing)
        key = ident['kind'] + '/' + ident['namespace'] + '/' + ident['name']
        if key not in state or state[key]['uid'] != ident['uid']:
            raise RuntimeError('refusing to adopt existing unregistered object: ' + key)
        return existing
    obj['metadata']['labels'] = {**obj['metadata'].get('labels', {}), **LABELS}
    return register(json.loads(command(['create', '-f', '-', '-o', 'json'], obj).stdout))


def assert_cluster():
    server = command(['config', 'view', '--minify', '-o', 'jsonpath={.clusters[0].cluster.server}']).stdout
    if server != 'https://172.16.101.10:6443': raise RuntimeError('wrong API server')
    system = get('ns', 'kube-system', '')
    if system['metadata']['uid'] != '5277649d-e28d-4a0a-ae76-8354365c63bc': raise RuntimeError('wrong cluster UID')
    nodes = get('nodes', namespace='')['items']
    actual = {n['metadata']['name']: next(a['address'] for a in n['status']['addresses'] if a['type'] == 'InternalIP') for n in nodes}
    if actual != NODES: raise RuntimeError('wrong node identities')
    if {n['metadata']['name']: n['metadata']['uid'] for n in nodes} != NODE_UIDS: raise RuntimeError('node UIDs changed since approved maintenance input')


def dv(name, source, size, owner=None):
    meta = {'name': name, 'namespace': NS}
    if owner: meta['ownerReferences'] = [{'apiVersion': 'kubevirt.io/v1', 'kind': 'VirtualMachine', 'name': owner['metadata']['name'], 'uid': owner['metadata']['uid'], 'controller': True, 'blockOwnerDeletion': True}]
    return create({'apiVersion': 'cdi.kubevirt.io/v1beta1', 'kind': 'DataVolume', 'metadata': meta, 'spec': {'source': source, 'storage': {'storageClassName': 'ani-block', 'volumeMode': 'Block', 'accessModes': ['ReadWriteOnce'], 'resources': {'requests': {'storage': size}}}}})


def inventory():
    existing = get('ns', NS, '', optional=True)
    save('scope', {'at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'host': socket.gethostname(), 'namespace': NS, 'nodes': NODES, 'protected': ['every object outside probe namespace', 'all shared StorageClasses/feature gates/runtime/KCN', 'original user image files'], 'purpose': 'native Block environment probes only', 'namespace_existed': bool(existing)})
    if existing:
        previous = json.loads((OUT / 'objects.json').read_text()) if (OUT / 'objects.json').exists() else {}
        if previous.get('Namespace//' + NS, {}).get('uid') != existing['metadata']['uid']:
            raise RuntimeError('probe namespace already exists without this run UID registration; no adoption')
    for kind in ['services', 'configmaps', 'deployments']:
        items = get(kind, namespace='cdi')['items']
        save('cdi-' + kind, [{'identity': identity(x), 'keys': list(x.get('data', {})), 'images': [c['image'] for c in x.get('spec', {}).get('template', {}).get('spec', {}).get('containers', [])], 'spec': x.get('spec') if kind == 'services' else None} for x in items])
    print(json.dumps({'namespace_exists': bool(existing), 'services': [x['metadata']['name'] for x in get('services', namespace='cdi')['items']]}))


def prepare():
    if not (OUT / 'scope.json').exists(): raise RuntimeError('inventory must record scope first')
    create({'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': NS}})
    dv(SOURCE, {'upload': {}}, '8G')
    dv('vm-mvp-blank-probe', {'blank': {}}, '2G')
    status()


def status():
    result = {'at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'datavolumes': [], 'pvcs': [], 'vms': [], 'vmis': [], 'pods': []}
    for resource, key in [('dv', 'datavolumes'), ('pvc', 'pvcs'), ('vm', 'vms'), ('vmi', 'vmis'), ('pods', 'pods')]:
        listing = get(resource, optional=True)
        if not listing: continue
        for x in listing['items']:
            v = identity(x)
            v['status'] = x.get('status')
            if resource == 'pvc': v['spec'] = {k: x['spec'].get(k) for k in ['volumeMode', 'accessModes', 'storageClassName', 'volumeName', 'resources']}
            if resource == 'pods': v['security'] = {'pod': x['spec'].get('securityContext'), 'node': x['spec'].get('nodeName'), 'containers': [{'name': c['name'], 'securityContext': c.get('securityContext'), 'volumeDevices': c.get('volumeDevices')} for c in x['spec']['containers']]}
            result[key].append(v)
    events = get('events', optional=True)
    result['warnings'] = [{'reason': x.get('reason'), 'message': x.get('message'), 'object': x.get('involvedObject'), 'count': x.get('count')} for x in (events or {}).get('items', []) if x.get('type') == 'Warning']
    save('status', result)
    print(json.dumps(result, ensure_ascii=False))


def brief():
    result = {'at': datetime.datetime.now(datetime.timezone.utc).isoformat()}
    for key, resource in [('datavolumes', 'dv'), ('pvcs', 'pvc'), ('vms', 'vm'), ('vmis', 'vmi'), ('pods', 'pods')]:
        result[key] = [{**identity(x), 'phase': x.get('status', {}).get('phase'), 'ready': x.get('status', {}).get('ready'), 'printableStatus': x.get('status', {}).get('printableStatus'), 'node': x.get('spec', {}).get('nodeName'), 'conditions': x.get('status', {}).get('conditions', [])} for x in get(resource)['items']]
    save('brief', result)
    print(json.dumps(result))


def kvm():
    result = [{'node': x['metadata']['name'], 'uid': x['metadata']['uid'], 'capacity': {k: v for k, v in x['status'].get('capacity', {}).items() if k.startswith('devices.kubevirt.io/')}, 'allocatable': {k: v for k, v in x['status'].get('allocatable', {}).items() if k.startswith('devices.kubevirt.io/')}, 'kvm_schedulable_label': x['metadata'].get('labels', {}).get('kubevirt.io/schedulable')} for x in get('nodes', namespace='')['items']]
    save('kvm-allocatable', result)
    print(json.dumps(result))


def kvm_diagnostics():
    kvm()
    pods = json.loads(command(['get', 'pods', '--all-namespaces', '-o', 'json']).stdout)['items']
    allocated = []
    handlers = []
    for pod in pods:
        if pod['metadata'].get('labels', {}).get('kubevirt.io') == 'virt-handler':
            r = command(['logs', '-n', pod['metadata']['namespace'], pod['metadata']['name'], '-c', 'virt-handler', '--tail=300'], check=False)
            relevant = [x for x in r.stdout.splitlines() if re.search(r'kvm|device.*plugin|/dev/|unhealthy|vhost', x, re.I)]
            handlers.append({'pod': identity(pod), 'node': pod['spec'].get('nodeName'), 'phase': pod['status'].get('phase'), 'log_exit': r.returncode, 'device_log': relevant})
        requests = [{'container': c['name'], 'kvm': c.get('resources', {}).get('requests', {}).get('devices.kubevirt.io/kvm')} for c in pod['spec']['containers'] if c.get('resources', {}).get('requests', {}).get('devices.kubevirt.io/kvm')]
        if requests:
            allocated.append({'pod': identity(pod), 'node': pod['spec'].get('nodeName'), 'phase': pod['status'].get('phase'), 'deleting': pod['metadata'].get('deletionTimestamp'), 'requests': requests, 'owners': pod['metadata'].get('ownerReferences', []), 'task_owned': pod['metadata'].get('labels', {}).get('ani.io/task') == LABELS['ani.io/task']})
    vmis = json.loads(command(['get', 'vmi', '--all-namespaces', '-o', 'json']).stdout)['items']
    result = {'at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'device_plugin': handlers, 'kvm_requesting_pods': allocated, 'vmis': [{'vmi': identity(v), 'phase': v.get('status', {}).get('phase'), 'node': v.get('status', {}).get('nodeName'), 'owners': v['metadata'].get('ownerReferences', []), 'task_owned': v['metadata'].get('labels', {}).get('ani.io/task') == LABELS['ani.io/task']} for v in vmis], 'scope': 'read-only; no workload eviction, device/plugin or KubeVirt configuration mutation'}
    save('kvm-diagnostics', result)
    print(json.dumps(result))


def source_hash_progress():
    frozen = json.loads((OUT / 'source-hash-input.json').read_text())
    pod = get('pod', frozen['pod']['name'])
    if pod['metadata']['uid'] != frozen['pod']['uid'] or pod['spec'].get('securityContext', {}).get('runAsUser') != 107: raise RuntimeError('hash probe identity or nonroot security changed')
    if pod['status'].get('phase') != 'Running': print(json.dumps({'pod': identity(pod), 'phase': pod['status'].get('phase')})); return
    shell = 'test "$(id -u)" = 107; id; for p in /proc/[0-9]*; do read -r c < "$p/comm" || continue; if test "$c" = sha256sum; then printf "%s\\n" "$p"; cat "$p/io"; fi; done'
    r = command(['exec', '-n', NS, pod['metadata']['name'], '-c', 'hash', '--', 'sh', '-ec', shell], check=False)
    result = {'pod': identity(pod), 'at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'exit': r.returncode, 'progress': r.stdout, 'stderr': r.stderr[-1000:], 'scope': 'read only existing hash process I/O counters; no second content read'}
    save('source-hash-progress', result)
    print(json.dumps(result))


def storage_diagnostics():
    nodes = [{'node': x['metadata']['name'], 'uid': x['metadata']['uid'], 'conditions': x['status'].get('conditions', [])} for x in get('nodes', namespace='')['items']]
    pods = get('pods')['items']
    task_pods = [{'pod': identity(x), 'node': x['spec'].get('nodeName'), 'phase': x.get('status', {}).get('phase'), 'reason': x.get('status', {}).get('reason'), 'message': x.get('status', {}).get('message'), 'conditions': x.get('status', {}).get('conditions', []), 'containers': [{'name': c['name'], 'ready': c.get('ready'), 'restartCount': c.get('restartCount'), 'state': c.get('state')} for c in x.get('status', {}).get('initContainerStatuses', []) + x.get('status', {}).get('containerStatuses', [])]} for x in pods]
    volumes = [{'pvc': identity(x), 'phase': x.get('status', {}).get('phase'), 'conditions': x.get('status', {}).get('conditions', []), 'volumeMode': x['spec'].get('volumeMode'), 'volumeName': x['spec'].get('volumeName')} for x in get('pvc')['items']]
    events = [{'reason': x.get('reason'), 'message': x.get('message'), 'object': x.get('involvedObject'), 'eventTime': x.get('eventTime'), 'lastTimestamp': x.get('lastTimestamp'), 'series': x.get('series'), 'count': x.get('count')} for x in get('events')['items'] if x.get('type') == 'Warning']
    reader = backend_reader()
    def read(args):
        try:
            r = subprocess.run(K + ['--request-timeout=20s', 'exec', '-n', reader['namespace'], reader['name'], '--'] + args, capture_output=True, text=True, timeout=25)
            result = {'command': args, 'exit': r.returncode, 'stderr': r.stderr[-1600:]}
            try: result['output'] = json.loads(r.stdout)
            except json.JSONDecodeError: result['output'] = r.stdout[-1600:]
            return result
        except subprocess.TimeoutExpired:
            return {'command': args, 'result': 'READ_TIMEOUT', 'timeout_seconds': 25}
    ceph = read(['ceph', 'health', 'detail', '--format=json'])
    source = json.loads((OUT / 'source-ready.json').read_text())
    backend = read(['rbd', 'info', source['backend_pool'] + '/' + source['backend_image'], '--format=json'])
    result = {'at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'nodes': nodes, 'task_pods': task_pods, 'task_pvcs': volumes, 'warnings': events, 'backend_reader': reader, 'ceph_health_detail': ceph, 'source_rbd_info': backend, 'scope': 'single read-only diagnosis; original hash process retained; no runtime/CSI/KCN/KubeVirt/backend mutation'}
    save('storage-diagnostics', result)
    print(json.dumps(result))


def upload():
    source = get('dv', SOURCE)
    known = json.loads((OUT / 'objects.json').read_text())
    key = 'DataVolume/' + NS + '/' + SOURCE
    if known[key]['uid'] != source['metadata']['uid']: raise RuntimeError('source DV identity changed')
    if source.get('status', {}).get('phase') == 'Succeeded':
        print('source already succeeded; upload not repeated'); return
    pvc = get('pvc', SOURCE)
    if pvc['spec'].get('volumeMode') != 'Block': raise RuntimeError('source PVC is not Block')
    upload_pvc = pvc
    if pvc['spec'].get('dataSourceRef', {}).get('kind') == 'VolumeUploadSource':
        upload_pvc = get('pvc', 'prime-' + pvc['metadata']['uid'])
    if upload_pvc['metadata'].get('annotations', {}).get('cdi.kubevirt.io/storage.pod.ready') != 'true': raise RuntimeError('CDI upload server is not ready; run status')
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    for previous in ('upload.json', 'upload-response.txt', 'uploadproxy-port-forward.log'):
        path = OUT / previous
        if path.exists(): path.rename(OUT / (path.stem + '-prior-' + stamp + path.suffix))
    if hashlib.file_digest(IMAGE.open('rb'), 'sha256').hexdigest() != IMAGE_SHA: raise RuntimeError('media changed')
    ca = get('cm', 'cdi-uploadproxy-signer-bundle', 'cdi')['data']['ca-bundle.crt']
    capath = PRIVATE / 'uploadproxy-ca.crt'; capath.write_text(ca); capath.chmod(0o600)
    token_request = {'apiVersion': 'upload.cdi.kubevirt.io/v1beta1', 'kind': 'UploadTokenRequest', 'metadata': {'name': SOURCE, 'namespace': NS}, 'spec': {'pvcName': SOURCE}}
    token = json.loads(command(['create', '-f', '-', '-o', 'json'], token_request).stdout)['status']['token']
    header = PRIVATE / 'upload-header'; header.write_text('Authorization: Bearer ' + token + '\n'); header.chmod(0o600)
    certdoc = json.loads(command(['get', 'secret', 'cdi-uploadproxy-server-cert', '-n', 'cdi', '-o', 'json']).stdout)
    cert = base64.b64decode(certdoc['data']['tls.crt']).decode()
    names = subprocess.check_output(['openssl', 'x509', '-noout', '-ext', 'subjectAltName'], input=cert, text=True)
    dns = next(x for x in re.findall(r'DNS:([^,\s]+)', names) if '*' not in x)
    listener = socket.socket(); listener.bind(('127.0.0.1', 0)); port = listener.getsockname()[1]; listener.close()
    log = (OUT / 'uploadproxy-port-forward.log').open('w')
    forward = subprocess.Popen(K + ['port-forward', '-n', 'cdi', 'service/cdi-uploadproxy', str(port) + ':443', '--address=127.0.0.1'], stdout=log, stderr=subprocess.STDOUT)
    try:
        for _ in range(50):
            try:
                with socket.create_connection(('127.0.0.1', port), timeout=.2): break
            except OSError: time.sleep(.2)
        else: raise RuntimeError('uploadproxy port-forward failed')
        cmd = ['curl', '--silent', '--show-error', '--fail-with-body', '--noproxy', '*', '--cacert', str(capath), '--resolve', dns + ':' + str(port) + ':127.0.0.1', '--header', '@' + str(header), '--data-binary', '@' + str(IMAGE), '--max-time', '600', '--output', str(OUT / 'upload-response.txt'), '--write-out', '%{http_code}', 'https://' + dns + ':' + str(port) + '/v1beta1/upload-async']
        r = subprocess.run(cmd, capture_output=True, text=True)
        save('upload', {'returncode': r.returncode, 'http_status': r.stdout, 'tls_verified': r.returncode == 0, 'tls_hostname': dns, 'ca_sha256': hashlib.sha256(ca.encode()).hexdigest(), 'source_file_sha256': IMAGE_SHA, 'source_dv_uid': source['metadata']['uid'], 'stderr': r.stderr[-1200:]})
        print(json.dumps({'upload_returncode': r.returncode, 'http_status': r.stdout, 'tls_verified': r.returncode == 0}))
        if r.returncode: raise RuntimeError('native CDI upload failed; response saved')
    finally:
        forward.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired): forward.wait(timeout=5)
        if forward.poll() is None: forward.kill()
        log.close(); header.unlink(missing_ok=True)


def upload_observe():
    status()
    pvc = get('pvc', SOURCE)
    pod_name = 'cdi-upload-' + ('prime-' + pvc['metadata']['uid'] if pvc['spec'].get('dataSourceRef', {}).get('kind') == 'VolumeUploadSource' else SOURCE)
    pod = get('pod', pod_name, optional=True)
    if pod:
        r = command(['logs', pod_name, '-n', NS, '-c', 'cdi-upload-server', '--tail=100'], check=False)
        logs = re.sub(r'eyJ[A-Za-z0-9_.-]{40,}', '[REDACTED_TOKEN]', r.stdout)
        save('upload-native-observation', {'pod': identity(pod), 'log_exit': r.returncode, 'logs': logs})
        print(logs[-9000:])


def create_node(node):
    if node not in NODES: raise RuntimeError('node outside scope')
    source = get('dv', SOURCE)
    if source.get('status', {}).get('phase') != 'Succeeded': raise RuntimeError('Ubuntu source is not yet available; blank path remains independent')
    name = 'vm-mvp-' + node
    root, data = name + '-root', name + '-data'
    marker = 'VM_BLOCK_PROBE_01a168ce_' + node
    spec = {'nodeSelector': {'kubernetes.io/hostname': node}, 'domain': {'resources': {'requests': {'memory': '1Gi', 'cpu': '1000m'}}, 'cpu': {'cores': 1}, 'devices': {'autoattachPodInterface': False, 'disks': [{'name': 'root', 'disk': {'bus': 'virtio'}, 'serial': 'vmroot-probe', 'bootOrder': 1}, {'name': 'data', 'disk': {'bus': 'scsi'}, 'serial': 'vmdata-probe'}, {'name': 'cloudinit', 'disk': {'bus': 'virtio'}}]}}, 'volumes': [{'name': 'root', 'dataVolume': {'name': root}}, {'name': 'data', 'dataVolume': {'name': data}}, {'name': 'cloudinit', 'cloudInitNoCloud': {'secretRef': {'name': name + '-cloudinit'}}}]}
    vm = create({'apiVersion': 'kubevirt.io/v1', 'kind': 'VirtualMachine', 'metadata': {'name': name, 'namespace': NS}, 'spec': {'runStrategy': 'Manual', 'template': {'metadata': {'labels': LABELS}, 'spec': spec}}})
    dv(root, {'pvc': {'namespace': NS, 'name': SOURCE}}, '8G', vm)
    dv(data, {'blank': {}}, '2G', vm)
    password_file = PRIVATE / (node + '-password')
    if not password_file.exists(): password_file.write_text(secrets.token_urlsafe(24)); password_file.chmod(0o600)
    script = '#!/bin/bash\nset -euo pipefail\ndev=$(lsblk -nr -o NAME,SERIAL,TYPE | awk \'$2=="vmdata-probe" && $3=="disk" {print $1}\')\ntest -n "$dev"\ntest "$(echo "$dev" | wc -l)" = 1\nprintf "' + marker + '\\n" | dd of="/dev/$dev" bs=512 count=1 conv=fsync\nprintf "' + marker + '\\n" >/var/lib/vm-mvp-root-marker\nsync\ndd if="/dev/$dev" bs=512 count=1 2>/dev/null | grep -a "' + marker + '"\necho "' + marker + '_PASS"\n'
    userdata = {'users': [{'name': 'vmprobe', 'groups': ['sudo'], 'lock_passwd': False, 'plain_text_passwd': password_file.read_text(), 'sudo': ['ALL=(ALL) NOPASSWD:ALL']}], 'ssh_pwauth': False, 'chpasswd': {'expire': False}, 'write_files': [{'path': '/usr/local/bin/vm-block-probe', 'permissions': '0755', 'content': script}], 'runcmd': [['bash', '/usr/local/bin/vm-block-probe']]}
    create({'apiVersion': 'v1', 'kind': 'Secret', 'metadata': {'name': name + '-cloudinit', 'namespace': NS, 'ownerReferences': [{'apiVersion': 'kubevirt.io/v1', 'kind': 'VirtualMachine', 'name': name, 'uid': vm['metadata']['uid']}]}, 'type': 'Opaque', 'stringData': {'userdata': '#cloud-config\n' + json.dumps(userdata)}})
    print(json.dumps({'created': name, 'uid': vm['metadata']['uid'], 'node': node, 'marker': marker, 'network': 'no NIC; Block probe only'}))


def power(node, action):
    if node not in NODES: raise RuntimeError('node outside scope')
    name = 'vm-mvp-' + node
    registered = json.loads((OUT / 'objects.json').read_text())['VirtualMachine/' + NS + '/' + name]
    vm = get('vm', name)
    if vm['metadata']['uid'] != registered['uid']: raise RuntimeError('VM UID changed')
    requests = vm.get('status', {}).get('stateChangeRequests', [])
    vmi = get('vmi', name, optional=True)
    if requests: raise RuntimeError('native power request still pending; observe its outcome before retrying')
    if action == 'start' and vmi: raise RuntimeError('existing VMI must finish before another native Start')
    if action == 'stop' and not vmi:
        print(json.dumps({'already_stopped': True, 'vm': registered})); return
    path = '/apis/subresources.kubevirt.io/v1/namespaces/' + NS + '/virtualmachines/' + name + '/' + action
    command(['replace', '--raw', path, '-f', '-'], {})
    save('power-' + node + '-' + action + '-' + str(int(time.time())), {'at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'requested': action, 'vm': registered, 'vmi_before': identity(vmi) if vmi else None, 'request_before': requests})
    print(json.dumps({'requested': action, 'vm': registered}))


def tool():
    folder = RUN / 'tools'; folder.mkdir(exist_ok=True)
    target = folder / 'virtctl-v1.9.0-linux-amd64'
    url = 'https://github.com/kubevirt/kubevirt/releases/download/v1.9.0/virtctl-v1.9.0-linux-amd64'
    if not target.exists():
        temporary = target.with_suffix('.download')
        r = subprocess.run(['curl', '--silent', '--show-error', '--fail', '--location', '--proto', '=https', '--proto-redir', '=https', '--continue-at', '-', '--max-time', '60', '--output', str(temporary), url], capture_output=True, text=True)
        save('virtctl-download-' + str(int(time.time())), {'source': url, 'official_sha256': VIRTCTL_SHA, 'checksum_source': VIRTCTL_CHECKSUM_SOURCE, 'returncode': r.returncode, 'downloaded_bytes': temporary.stat().st_size if temporary.exists() else 0, 'stderr': r.stderr[-1200:]})
        if r.returncode: raise RuntimeError('official virtctl download incomplete; safe partial retained for resume')
        if hashlib.file_digest(temporary.open('rb'), 'sha256').hexdigest() != VIRTCTL_SHA: raise RuntimeError('virtctl differs from official v1.9.0 SHA; refusing execution')
        if temporary.open('rb').read(4) != b'\x7fELF': raise RuntimeError('virtctl download was not an ELF binary')
        temporary.rename(target); target.chmod(0o755)
    digest = hashlib.file_digest(target.open('rb'), 'sha256').hexdigest()
    if digest != VIRTCTL_SHA: raise RuntimeError('existing virtctl differs from official SHA; refusing execution')
    version = subprocess.run([str(target), 'version', '--client'], capture_output=True, text=True)
    if version.returncode or 'v1.9.0' not in version.stdout: raise RuntimeError('official pinned virtctl version check failed')
    save('virtctl-tool', {'source': url, 'sha256': digest, 'official_checksum_source': VIRTCTL_CHECKSUM_SOURCE, 'official_sha256_matched': True, 'version_exit': version.returncode, 'version': version.stdout})
    print(json.dumps({'virtctl': str(target), 'sha256': digest, 'version_exit': version.returncode}))


def console(node, prepare_persistence=False):
    import pexpect
    if node not in NODES: raise RuntimeError('node outside scope')
    name = 'vm-mvp-' + node
    expected = 'VM_BLOCK_PROBE_01a168ce_' + node
    target = RUN / 'tools/virtctl-v1.9.0-linux-amd64'
    password = (PRIVATE / (node + '-password')).read_text()
    persistence_file = OUT / ('persistence-' + node + '.json')
    persistence = json.loads(persistence_file.read_text()) if persistence_file.exists() else None
    runtime_password_file = PRIVATE / (node + '-runtime-password')
    runtime_password = runtime_password_file.read_text() if runtime_password_file.exists() else ''
    if persistence:
        expected = persistence['runtime_marker']
        if persistence['phase'] in ('password_change_requested', 'prepared'):
            if not runtime_password: raise RuntimeError('runtime probe password missing; keep unknown outcome for recovery')
            password = runtime_password
    transcript = []
    vm, vmi = get('vm', name), get('vmi', name)
    registered = json.loads((OUT / 'objects.json').read_text())['VirtualMachine/' + NS + '/' + name]
    if vm['metadata']['uid'] != registered['uid'] or not any(x.get('kind') == 'VirtualMachine' and x.get('uid') == registered['uid'] and x.get('name') == name for x in vmi['metadata'].get('ownerReferences', [])):
        raise RuntimeError('probe VM/VMI identity changed')
    if persistence and persistence['vm']['uid'] != vm['metadata']['uid']: raise RuntimeError('persistence evidence belongs to another VM UID')
    if not target.exists() or hashlib.file_digest(target.open('rb'), 'sha256').hexdigest() != VIRTCTL_SHA: raise RuntimeError('official SHA-verified v1.9.0 virtctl is required')
    launchers = [p for p in get('pods')['items'] if p['metadata'].get('labels', {}).get('kubevirt.io') == 'virt-launcher' and any(x.get('kind') == 'VirtualMachineInstance' and x.get('uid') == vmi['metadata']['uid'] for x in p['metadata'].get('ownerReferences', [])) and not p['metadata'].get('deletionTimestamp')]
    if len(launchers) != 1: raise RuntimeError('expected one immutable task launcher')
    pod = launchers[0]
    compute = next(c for c in pod['spec']['containers'] if c['name'] == 'compute')
    uid = compute.get('securityContext', {}).get('runAsUser', pod['spec'].get('securityContext', {}).get('runAsUser'))
    if uid != 107 or vmi.get('status', {}).get('nodeName') != node: raise RuntimeError('launcher is not native UID107 on the exact requested node')
    executable = str(target)
    arguments = ['console', name, '--namespace', NS, '--kubeconfig', K[1].split('=', 1)[1], '--timeout=20']
    console_driver = 'official SHA-verified v1.9.0 virtctl KubeVirt serialconsole subresource over native authenticated TLS'
    child = pexpect.spawn(executable, arguments, encoding='utf-8', timeout=30)
    def expect(pattern):
        result = child.expect(pattern)
        transcript.append(child.before); transcript.append(child.after if isinstance(child.after, str) else '')
        return result
    try:
        child.sendline('')
        if expect([r'login:', r'\$ ']) == 1:
            child.sendline('exit'); expect(r'login:')
        child.sendline('vmprobe')
        expect(r'[Pp]assword:'); child.sendline(password)
        expect(r'\$ ')
        code = 'import json,subprocess,os; d=json.loads(subprocess.check_output(["lsblk","-J","-o","NAME,SERIAL,TYPE"]))["blockdevices"]; p=next(x["name"] for x in d if x.get("serial")=="vmdata-probe" and x["type"]=="disk"); print(json.dumps({"root":open("/var/lib/vm-mvp-root-marker").read().strip(),"data":open("/dev/"+p,"rb").read(512).rstrip(bytes([0,10])).decode(),"disk":p,"username":os.environ.get("SUDO_USER"),"datasource":open("/var/lib/cloud/instance/datasource").read().strip(),"nocloud_instance_id":open("/var/lib/cloud/data/instance-id").read().strip(),"firmware_uuid":open("/sys/class/dmi/id/product_uuid").read().strip(),"boot_id":open("/proc/sys/kernel/random/boot_id").read().strip(),"os_release":open("/etc/os-release").read()}))'
        child.sendline("sudo -n python3 -c '" + code + "'")
        expect(r'\$ ')
        raw = child.before
        values = []
        for line in raw.splitlines():
            try:
                value = json.loads(line.strip())
                if isinstance(value, dict) and 'root' in value and 'data' in value: values.append(value)
            except json.JSONDecodeError: pass
        if len(values) != 1 or values[0]['root'] != expected or values[0]['data'] != expected or values[0].get('username') != 'vmprobe' or not values[0].get('datasource', '').startswith('DataSourceNoCloud'):
            raise RuntimeError('guest did not return both exact persisted Block markers')
        volume_refs = {volume: identity(get('pvc', name + '-' + volume)) for volume in ('root', 'data')}
        if prepare_persistence and not persistence:
            marker = 'VM_RUNTIME_PERSIST_01a168ce_' + secrets.token_hex(12)
            write_code = 'import json,subprocess,os; d=json.loads(subprocess.check_output(["lsblk","-J","-o","NAME,SERIAL,TYPE"]))["blockdevices"]; p=next(x["name"] for x in d if x.get("serial")=="vmdata-probe" and x["type"]=="disk"); m="' + marker + '"; f=open("/var/lib/vm-mvp-root-marker","w"); f.write(m); f.flush(); os.fsync(f.fileno()); f.close(); f=open("/dev/"+p,"r+b",buffering=0); f.write(m.encode().ljust(512,bytes([0]))); os.fsync(f.fileno()); f.close(); os.sync(); print("RUNTIME_MARKERS_WRITTEN")'
            child.sendline("sudo -n python3 -c '" + write_code + "'")
            expect(r'\$ ')
            if not any(line.strip() == 'RUNTIME_MARKERS_WRITTEN' for line in child.before.splitlines()): raise RuntimeError('runtime marker write not acknowledged')
            if not runtime_password:
                runtime_password = secrets.token_urlsafe(24)
                runtime_password_file.write_text(runtime_password); runtime_password_file.chmod(0o600)
            persistence = {'at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'phase': 'password_change_requested', 'scope': 'native environment probe only', 'vm': identity(vm), 'vmi_before': identity(vmi), 'volume_refs': volume_refs, 'runtime_marker': marker, 'observed_before': values[0], 'purpose': 'prove Stop/Start preserves post-cloud-init account state and both Block markers'}
            save('persistence-' + node, persistence)
            child.sendline('sudo -n passwd vmprobe')
            expect(r'New password:'); child.sendline(runtime_password)
            expect(r'Retype new password:'); child.sendline(runtime_password)
            expect(r'\$ ')
            if 'password updated successfully' not in child.before: raise RuntimeError('runtime password change outcome unconfirmed; retain private recovery input')
            persistence['phase'] = 'prepared'; persistence['runtime_password_rotated'] = True
            save('persistence-' + node, persistence)
        elif persistence and vmi['metadata']['uid'] != persistence['vmi_before']['uid']:
            previous = persistence['observed_before']
            actual = values[0]
            if volume_refs != persistence['volume_refs'] or actual['boot_id'] == previous['boot_id'] or actual['firmware_uuid'] != previous['firmware_uuid'] or actual['nocloud_instance_id'] != previous['nocloud_instance_id']:
                raise RuntimeError('Stop/Start changed disk identity or NoCloud logical identity, or guest did not reboot')
            save('persistence-' + node + '-result', {'at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'result': 'PASS', 'layer': 'GUEST', 'scope': 'native environment probe only', 'vm': identity(vm), 'vmi_before': persistence['vmi_before'], 'vmi_after': identity(vmi), 'volume_refs': volume_refs, 'post_initialization_password_login': True, 'observed_before': previous, 'observed_after': actual, 'runtime_marker': persistence['runtime_marker']})
        save('guest-' + node + '-' + str(int(time.time())), {'at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'result': 'PASS', 'layer': 'GUEST', 'scope': 'native environment probe only', 'console_driver': console_driver, 'node': node, 'vm': identity(get('vm', name)), 'vmi': identity(get('vmi', name)), 'launcher': identity(pod), 'native_uid': uid, 'observed': values[0]})
        print(json.dumps({'node': node, 'native_guest_root_and_data': 'PASS', 'observed': values[0]}))
        child.sendline('exit')
        expect(r'login:')
    except Exception as error:
        error_text = str(error).replace(password, '[REDACTED]')
        if runtime_password: error_text = error_text.replace(runtime_password, '[REDACTED]')
        save('guest-error-' + node + '-' + str(int(time.time())), {'at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'result': 'FAIL', 'layer': 'GUEST', 'scope': 'native environment probe only', 'console_driver': console_driver, 'node': node, 'vm': identity(vm), 'vmi': identity(vmi), 'error': error_text[-2000:]})
        raise RuntimeError('native guest verification failed; redacted failure and serial transcript saved') from None
    finally:
        child.close(force=True)
        sanitized = ''.join(transcript).replace(password, '[REDACTED]')
        if runtime_password: sanitized = sanitized.replace(runtime_password, '[REDACTED]')
        (OUT / ('console-' + node + '.txt')).write_text(sanitized)


def device(node):
    if node not in NODES: raise RuntimeError('node outside scope')
    pvc = get('pvc', 'vm-mvp-blank-probe')
    if pvc['spec'].get('volumeMode') != 'Block' or pvc['status'].get('phase') != 'Bound': raise RuntimeError('blank Block PVC not bound')
    name = 'vm-mvp-device-' + node
    marker = 'NATIVE_BLOCK_UID107_01a168ce_' + node
    shell = 'test "$(id -u)" = 107; test -b /dev/probe-block; id; printf "' + marker + '\\n" | dd of=/dev/probe-block bs=512 count=1 conv=fsync; dd if=/dev/probe-block bs=512 count=1 2>/dev/null; sync'
    pod = create({'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {'name': name, 'namespace': NS}, 'spec': {'nodeSelector': {'kubernetes.io/hostname': node}, 'restartPolicy': 'Never', 'securityContext': {'runAsUser': 107, 'runAsGroup': 107, 'runAsNonRoot': True, 'seccompProfile': {'type': 'RuntimeDefault'}}, 'containers': [{'name': 'block', 'image': '172.16.101.10:5000/kubevirt/cdi-importer:v1.66.1', 'imagePullPolicy': 'IfNotPresent', 'command': ['/bin/sh', '-ec', shell], 'securityContext': {'allowPrivilegeEscalation': False, 'capabilities': {'drop': ['ALL']}}, 'volumeDevices': [{'name': 'block', 'devicePath': '/dev/probe-block'}]}], 'volumes': [{'name': 'block', 'persistentVolumeClaim': {'claimName': pvc['metadata']['name']}}]}})
    print(json.dumps({'device_probe_requested': identity(pod), 'node': node, 'layer': 'LIVE', 'guest': 'NOT_RUN'}))


def device_result(node):
    if node not in NODES: raise RuntimeError('node outside scope')
    name = 'vm-mvp-device-' + node
    pod = get('pod', name)
    if pod.get('status', {}).get('phase') != 'Succeeded': raise RuntimeError('nonroot device probe has not succeeded')
    logs = command(['logs', name, '-n', NS, '-c', 'block']).stdout
    marker = 'NATIVE_BLOCK_UID107_01a168ce_' + node
    if 'uid=107' not in logs or marker not in logs: raise RuntimeError('native device log lacks exact UID and disk marker')
    save('device-' + node, {'pod': identity(pod), 'node': node, 'layer': 'LIVE', 'result': 'PASS', 'guest': 'NOT_RUN', 'logs': logs, 'securityContext': pod['spec'].get('securityContext'), 'containerSecurityContext': pod['spec']['containers'][0].get('securityContext')})
    delete_exact(identity(pod))
    print(json.dumps({'node': node, 'nonroot_Block_read_write': 'PASS', 'GUEST': 'NOT_RUN', 'pod_deleted_with_UID_precondition': pod['metadata']['uid']}))


def delete_exact(ref):
    if ref['namespace'] != NS: raise RuntimeError('delete outside probe namespace')
    obj = get(ref['kind'], ref['name'], optional=True)
    if obj is None: return 'absent'
    if obj['metadata']['uid'] != ref['uid']: raise RuntimeError('refusing same-name replacement delete')
    resources = {'Pod': ('v1', 'pods'), 'Secret': ('v1', 'secrets'), 'VirtualMachine': ('kubevirt.io/v1', 'virtualmachines'), 'DataVolume': ('cdi.kubevirt.io/v1beta1', 'datavolumes')}
    api, resource = resources[ref['kind']]
    path = ('/api/' if api == 'v1' else '/apis/') + api + '/namespaces/' + NS + '/' + resource + '/' + ref['name']
    options = {'apiVersion': 'v1', 'kind': 'DeleteOptions', 'preconditions': {'uid': ref['uid']}, 'propagationPolicy': 'Foreground'}
    command(['delete', '--raw', path, '-f', '-'], options)
    return 'delete_requested'


def source_result():
    source = get('dv', SOURCE); pvc = get('pvc', SOURCE)
    if source.get('status', {}).get('phase') != 'Succeeded' or pvc['status'].get('phase') != 'Bound' or pvc['spec'].get('volumeMode') != 'Block' or pvc['spec'].get('accessModes') != ['ReadWriteOnce']:
        raise RuntimeError('source is not a succeeded Block/RWO upload')
    pv = get('pv', pvc['spec']['volumeName'], '')
    attrs = pv['spec'].get('csi', {}).get('volumeAttributes', {})
    result = {'source_file': str(IMAGE), 'source_file_sha256': IMAGE_SHA, 'source_file_bytes': 621284352, 'virtual_bytes': 3758096384, 'datavolume': identity(source), 'pvc': identity(pvc), 'volume_mode': pvc['spec']['volumeMode'], 'access_modes': pvc['spec']['accessModes'], 'requested': pvc['spec']['resources']['requests']['storage'], 'capacity': pvc['status'].get('capacity'), 'pv': identity(pv), 'reclaim_policy': pv['spec']['persistentVolumeReclaimPolicy'], 'csi_driver': pv['spec'].get('csi', {}).get('driver'), 'backend_image': attrs.get('imageName'), 'backend_pool': attrs.get('pool'), 'content_checksum_note': 'input qcow2 checksum; CDI converts to Block raw. Not asserted equal to raw PVC bytes.', 'retained_for': 'root subsequent product-source preparation; native probe only'}
    save('source-ready', result); print(json.dumps(result))


def source_hash():
    pvc = get('pvc', SOURCE)
    source = get('dv', SOURCE)
    if source.get('status', {}).get('phase') != 'Succeeded' or pvc.get('status', {}).get('phase') != 'Bound' or pvc['spec'].get('volumeMode') != 'Block': raise RuntimeError('source is not ready for immutable content freeze')
    pod = create({'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {'name': 'vm-mvp-source-hash', 'namespace': NS}, 'spec': {'nodeSelector': {'kubernetes.io/hostname': 'ani-03'}, 'restartPolicy': 'Never', 'securityContext': {'runAsUser': 107, 'runAsGroup': 107, 'runAsNonRoot': True, 'seccompProfile': {'type': 'RuntimeDefault'}}, 'containers': [{'name': 'hash', 'image': '172.16.101.10:5000/kubevirt/cdi-importer:v1.66.1', 'imagePullPolicy': 'IfNotPresent', 'command': ['/bin/sh', '-ec', 'test "$(id -u)" = 107; test -b /dev/source-block; id; sha256sum /dev/source-block'], 'securityContext': {'allowPrivilegeEscalation': False, 'capabilities': {'drop': ['ALL']}}, 'volumeDevices': [{'name': 'source', 'devicePath': '/dev/source-block'}]}], 'volumes': [{'name': 'source', 'persistentVolumeClaim': {'claimName': SOURCE, 'readOnly': True}}]}})
    save('source-hash-input', {'pvc': identity(pvc), 'datavolume': identity(source), 'pod': identity(pod), 'input_qcow2_sha256': IMAGE_SHA, 'requested_storage': pvc['spec']['resources']['requests']['storage'], 'capacity': pvc['status'].get('capacity'), 'scope': 'hash entire native raw Block device read-only exactly once'})
    print(json.dumps({'source_hash_requested': identity(pod), 'source_pvc': identity(pvc), 'read_only': True}))


def source_hash_result():
    frozen = json.loads((OUT / 'source-hash-input.json').read_text())
    pod = get('pod', frozen['pod']['name']); pvc = get('pvc', SOURCE); source = get('dv', SOURCE)
    if pod['metadata']['uid'] != frozen['pod']['uid'] or pvc['metadata']['uid'] != frozen['pvc']['uid'] or source['metadata']['uid'] != frozen['datavolume']['uid']: raise RuntimeError('source hash target identity changed')
    if pvc['spec']['resources']['requests']['storage'] != frozen['requested_storage'] or pvc['status'].get('capacity') != frozen['capacity']: raise RuntimeError('source capacity changed during whole-device content freeze')
    if pod.get('status', {}).get('phase') != 'Succeeded': raise RuntimeError('whole-device source hash not complete')
    logs = command(['logs', pod['metadata']['name'], '-n', NS, '-c', 'hash']).stdout
    digest = re.findall(r'^([a-f0-9]{64})\s+/dev/source-block\s*$', logs, re.M)
    if 'uid=107' not in logs or len(digest) != 1: raise RuntimeError('source hash lacks exact nonroot whole-device result')
    patch = [{'op': 'test', 'path': '/metadata/uid', 'value': pvc['metadata']['uid']}, {'op': 'add', 'path': '/metadata/annotations/vm.ani.io~1source-sha256', 'value': digest[0]}]
    command(['patch', 'pvc', SOURCE, '-n', NS, '--type=json', '-p', json.dumps(patch)])
    verified = get('pvc', SOURCE)
    if verified['metadata']['uid'] != frozen['pvc']['uid'] or verified['metadata'].get('annotations', {}).get('vm.ani.io/source-sha256') != digest[0]: raise RuntimeError('source management annotation did not bind frozen identity')
    result = {**frozen, 'raw_block_sha256': digest[0], 'input_virtual_bytes': 3758096384, 'content_type': 'kubevirt', 'volume_mode': 'Block', 'source_annotation': 'vm.ani.io/source-sha256', 'logs': logs, 'semantics': 'input SHA is immutable qcow2 file; raw SHA covers the entire converted capacity-sized Block device including padding; distinct content identities'}
    save('source-content-frozen', result)
    delete_exact(identity(pod))
    print(json.dumps(result))


def backend_reader():
    candidates = [p for p in get('pods', namespace='rook-ceph')['items'] if p['metadata'].get('labels', {}).get('app') == 'rook-ceph-tools' and p.get('status', {}).get('phase') == 'Running' and not p['metadata'].get('deletionTimestamp')]
    if len(candidates) != 1: raise RuntimeError('expected one existing Rook tools reader; no privileged client will be created')
    return identity(candidates[0])


def rbd_read(reader, args):
    pod = get('pod', reader['name'], reader['namespace'])
    if pod['metadata']['uid'] != reader['uid']: raise RuntimeError('backend reader identity changed')
    return command(['exec', '-n', reader['namespace'], reader['name'], '--', 'rbd'] + args, check=False)


def backend_info(reader, volume):
    pool, image = volume['backend_pool'], volume['backend_image']
    if not pool or not image or not re.fullmatch(r'[A-Za-z0-9_.-]+', pool) or not re.fullmatch(r'[A-Za-z0-9_.-]+', image): raise RuntimeError('invalid task backend image identity')
    r = rbd_read(reader, ['info', pool + '/' + image, '--format=json'])
    if r.returncode == 0:
        info = json.loads(r.stdout)
        return {'result': 'PRESENT', 'image_id': info.get('id'), 'name': info.get('name'), 'size': info.get('size'), 'block_name_prefix': info.get('block_name_prefix')}
    if r.returncode == 2 and 'No such file or directory' in r.stderr:
        return {'result': 'ABSENT', 'exit_code': r.returncode, 'stderr': r.stderr[-1200:]}
    raise RuntimeError('unconfirmed RBD read: ' + r.stderr[-1200:])


def cleanup():
    refs = json.loads((OUT / 'objects.json').read_text())
    owned_disks = {ref['name']: ref['uid'] for ref in refs.values() if ref['kind'] == 'DataVolume' and ref['name'] != SOURCE}
    volumes = []
    for pvc in get('pvc')['items']:
        dv_uid = owned_disks.get(pvc['metadata']['name'])
        if not dv_uid: continue
        if not any(owner.get('kind') == 'DataVolume' and owner.get('uid') == dv_uid for owner in pvc['metadata'].get('ownerReferences', [])):
            raise RuntimeError('task disk PVC no longer belongs to its registered DV UID')
        pvname = pvc['spec'].get('volumeName')
        if not pvname: continue
        pv = get('pv', pvname, '')
        attrs = pv['spec'].get('csi', {}).get('volumeAttributes', {})
        volumes.append({'pvc': identity(pvc), 'pv': identity(pv), 'backend_pool': attrs.get('pool'), 'backend_image': attrs.get('imageName'), 'reclaim_policy': pv['spec']['persistentVolumeReclaimPolicy']})
    if volumes:
        reader = backend_reader()
        for volume in volumes: volume['backend_before'] = backend_info(reader, volume)
        save('cleanup-volume-refs', volumes)
        save('cleanup-backend-reader', reader)
    results = []
    for ref in refs.values():
        if ref['kind'] == 'Namespace' or (ref['kind'] == 'DataVolume' and ref['name'] == SOURCE): continue
        results.append({'object': ref, 'result': delete_exact(ref)})
    save('cleanup-requests', results)
    print(json.dumps({'requested': results, 'retained': [NS, SOURCE]}))


def cleanup_status():
    volumes = json.loads((OUT / 'cleanup-volume-refs.json').read_text())
    reader = json.loads((OUT / 'cleanup-backend-reader.json').read_text())
    results, trash_by_pool = [], {}
    for volume in volumes:
        pvc = get('pvc', volume['pvc']['name'], optional=True)
        pv = get('pv', volume['pv']['name'], '', optional=True)
        native_absent = (pvc is None or pvc['metadata']['uid'] != volume['pvc']['uid']) and (pv is None or pv['metadata']['uid'] != volume['pv']['uid'])
        backend = backend_info(reader, volume)
        if volume['backend_pool'] not in trash_by_pool:
            trash_read = rbd_read(reader, ['trash', 'ls', volume['backend_pool'], '--format=json'])
            if trash_read.returncode: raise RuntimeError('backend trash read failed: ' + trash_read.stderr[-1200:])
            trash_by_pool[volume['backend_pool']] = json.loads(trash_read.stdout)
        trash = [x for x in trash_by_pool[volume['backend_pool']] if x.get('name') == volume['backend_image'] or (volume['backend_before'].get('image_id') and x.get('id') == volume['backend_before']['image_id'])]
        results.append({**volume, 'native_original_UIDs_absent': native_absent, 'backend_after': backend, 'matching_trash': trash, 'result': 'PASS' if native_absent and backend['result'] == 'ABSENT' and not trash else 'PENDING'})
    save('cleanup-reclaim', {'reader': reader, 'volumes': results, 'retained': [NS, SOURCE], 'scope': 'exact task PVC/PV UIDs and RBD image identity; read-only backend verification'})
    print(json.dumps({'reclaim': [{'pvc': x['pvc']['name'], 'result': x['result']} for x in results], 'retained': [NS, SOURCE]}))


def final_state():
    refs = json.loads((OUT / 'objects.json').read_text())
    namespace = get('namespace', NS, '')
    if namespace['metadata']['uid'] != refs['Namespace//' + NS]['uid']: raise RuntimeError('probe namespace identity changed')
    template = '{range .items[*]}{.kind}{"\\t"}{.metadata.name}{"\\t"}{.metadata.uid}{"\\n"}{end}'
    listed = command(['get', 'vm,vmi,pod,dv,pvc,secret', '-n', NS, '-o', 'jsonpath=' + template]).stdout
    objects = [{'kind': kind, 'name': name, 'uid': uid} for line in listed.splitlines() if line for kind, name, uid in [line.split('\t')]]
    remaining = [obj for obj in objects if not (obj['kind'] in ('DataVolume', 'PersistentVolumeClaim') and obj['name'] == SOURCE)]
    source, pvc = get('dv', SOURCE), get('pvc', SOURCE)
    frozen = json.loads((OUT / 'source-content-frozen.json').read_text())
    if identity(source) != frozen['datavolume'] or identity(pvc) != frozen['pvc'] or pvc['metadata'].get('annotations', {}).get('vm.ani.io/source-sha256') != frozen['raw_block_sha256']:
        raise RuntimeError('retained source does not match the frozen UID/checksum')
    result = {'at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'scope': 'native probe cleanup only; source retained for product acceptance', 'namespace': identity(namespace), 'remaining_objects': objects, 'probe_transients_absent': not remaining, 'source': {'datavolume': identity(source), 'phase': source.get('status', {}).get('phase'), 'pvc': identity(pvc), 'volume_mode': pvc['spec']['volumeMode'], 'requested': pvc['spec']['resources']['requests']['storage'], 'capacity': pvc['status']['capacity'], 'raw_block_sha256_annotation': frozen['raw_block_sha256']}, 'backend_reclaim': json.loads((OUT / 'cleanup-reclaim.json').read_text())}
    save('probe-final-state', result)
    print(json.dumps({'probe_transients_absent': not remaining, 'retained_source': result['source'], 'backend_reclaim': [x['result'] for x in result['backend_reclaim']['volumes']]}))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['inventory', 'prepare', 'status', 'brief', 'kvm', 'kvm-diagnostics', 'storage-diagnostics', 'upload', 'upload-observe', 'create-node', 'start', 'stop', 'tool', 'console', 'prepare-persistence', 'device', 'device-result', 'source-result', 'source-hash', 'source-hash-progress', 'source-hash-result', 'cleanup', 'cleanup-status', 'final-state'])
    parser.add_argument('--node', choices=list(NODES))
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True); PRIVATE.mkdir(parents=True, exist_ok=True); PRIVATE.chmod(0o700)
    with open('/home/chabking/workspace/ani-network-service-runs/net05a-heavy.lock', 'a') as lock:
        deadline = time.monotonic() + 10
        while True:
            try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB); break
            except BlockingIOError:
                if time.monotonic() >= deadline: print('LOCK_BUSY'); return 73
                time.sleep(.25)
        assert_cluster()
        if args.action in ('inventory', 'prepare', 'status', 'brief', 'kvm', 'upload', 'tool', 'cleanup'): globals()[args.action]()
        elif args.action == 'create-node': create_node(args.node)
        elif args.action == 'console': console(args.node)
        elif args.action == 'prepare-persistence': console(args.node, prepare_persistence=True)
        elif args.action == 'device': device(args.node)
        elif args.action == 'device-result': device_result(args.node)
        elif args.action == 'source-result': source_result()
        elif args.action == 'source-hash': source_hash()
        elif args.action == 'source-hash-progress': source_hash_progress()
        elif args.action == 'source-hash-result': source_hash_result()
        elif args.action == 'upload-observe': upload_observe()
        elif args.action == 'cleanup-status': cleanup_status()
        elif args.action == 'final-state': final_state()
        elif args.action == 'kvm-diagnostics': kvm_diagnostics()
        elif args.action == 'storage-diagnostics': storage_diagnostics()
        else: power(args.node, args.action)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
