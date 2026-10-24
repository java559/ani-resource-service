#!/usr/bin/env python3
"""One task-owned NoCloud SSH-key proof. Prepare is read-only to the product.

No key generation, arbitrary user-data, image mutation, public-network probe,
permission change or native VM mutation is provided. Resource allocation requires
the reviewed plan SHA and an explicit execution flag. Guest proof follows a
separately coordinated window after the actual VM/VMI identities are known.
"""
import argparse
import base64
import fcntl
import hashlib
import importlib.util
import inspect
import io
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import socket
import subprocess
import sys
import time
import urllib.parse
import uuid
import zlib

RUN = Path('/home/chabking/workspace/ani-resource-service-runs/vm-mvp-20261023-01a168ce')
LOCK = Path('/home/chabking/workspace/ani-network-service-runs/net05a-heavy.lock')
PRIVATE = RUN / 'private/gov-candidate'
STATE = PRIVATE / 'cloudinit-ssh-plan.json'
GUEST = PRIVATE / 'cloudinit-ssh-guest.json'
RECEIPT = RUN / 'evidence/product-cloudinit-ssh.json'
PUBLIC_KEY = Path('/home/chabking/.ssh/id_ed25519.pub')
IDENTITY = Path('/home/chabking/.ssh/id_ed25519')
NAME = 'vm-mvp-cloudinit-ssh-01a168ce'
KEY = str(uuid.uuid5(uuid.NAMESPACE_URL, 'ani://d87c4dbd-04fa-4066-9ef6-c6fa4217f316/vm-mvp-20261023-01a168ce/cloudinit-ssh-create'))
BASE = '/api/v1/virtual-machines'
CREATE_SOURCE_SHA = '6c33556aff793242fb8b3f330f676a4af8d0d3fc0a8419ab4e316c4ce2b2089f'
APPROVED_PLAN_SHA = '4e3dace8a295bab8f3551f169bccbda7af70f7204c5172ca53834ed06db967c1'
VIRTCTL = RUN / 'tools/virtctl-v1.9.0-linux-amd64'
VIRTCTL_SHA = '40ede2ee37c98a1aeed71c9c219616a05247ce2be109e1edddf0477572e8b978'
GUEST_RESULT = RUN / 'evidence/product-cloudinit-ssh-guest.json'
PORT = 39193
PROXY_NAME = 'vm-mvp-ssh-proxy-01a168ce'
PROXY_PLAN = RUN / 'evidence/product-cloudinit-ssh-proxy-prepared.json'
PROXY_IMAGE = '172.16.101.10:5000/kubevirt/cdi-importer@sha256:28134acc24521d9bc8c9250fb29a9bfe0278b771139d32ea5b162d8f3af5ee05'


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def helpers():
    require(os.environ.get('SSH_CONNECTION', '').split()[2:3] == ['172.16.101.31'], 'only ssh fedora may run this task')
    t = load('cloudinit_ssh_tenant', RUN / 'vm-product-second-tenant-vm.py')
    g = t.helper()
    return g, t


def fingerprint(path):
    result = subprocess.run(['ssh-keygen', '-l', '-E', 'sha256', '-f', str(path)],
                            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=5)
    values = [x for x in result.stdout.split() if x.startswith('SHA256:')]
    require(result.returncode == 0 and len(values) == 1, 'existing SSH identity fingerprint is unavailable')
    return values[0]


def key_input():
    require(PUBLIC_KEY.is_file() and not PUBLIC_KEY.is_symlink(), 'existing .pub identity missing or replaced by symlink')
    require(IDENTITY.is_file() and not IDENTITY.is_symlink() and IDENTITY.stat().st_mode & 0o077 == 0,
            'existing private identity reference missing or not private; never generate a replacement')
    raw = PUBLIC_KEY.read_text().rstrip('\n')
    require(len(raw.encode()) <= 16384 and not any(c in raw for c in ('\n', '\r', '\x00')), 'public key must be one line')
    parts = raw.split()
    require(len(parts) >= 2 and parts[0] == 'ssh-ed25519', 'only the already inventoried Ed25519 public key is in scope')
    base64.b64decode(parts[1], validate=True)
    pub_fp = fingerprint(PUBLIC_KEY)
    require(pub_fp == fingerprint(IDENTITY), 'existing public/private identity fingerprints differ')
    require(pub_fp == 'SHA256:HbWe63OglHojcu+O7ovQK2dQWaEBuKzE2qAP4hq7j4w', 'reviewed existing identity changed')
    return raw, {'public_path': str(PUBLIC_KEY), 'public_file_sha256': sha(PUBLIC_KEY),
                 'fingerprint': pub_fp, 'private_reference': str(IDENTITY), 'private_mode': '0600',
                 'pair_fingerprint_matches': True, 'key_generation': False}


def helper_digest(g, t):
    return digest({name: inspect.getsource(fn) for name, fn in {
        'api': g.api, 'save': g.save, 'tenant_login': t.login, 'catalog': t.catalog,
        'placement': t.placement, 'inventory': t.inventory}.items()})


def preflight(g, t, token):
    catalog = t.catalog(g, token)
    placement = t.placement(g, token, require_mapping=False)
    require(placement['network_mapping_active'] is True, 'approved tenant2 network mapping is not present')
    inventory = t.inventory(g, token)
    same_purpose = [v for v in inventory if v.get('name') == NAME]
    if same_purpose:
        require(STATE.exists(), 'same-purpose VM exists without this intent; stop for reuse review, do not adopt or recreate')
        state = json.loads(STATE.read_text())
        require(len(same_purpose) == 1 and same_purpose[0].get('id', same_purpose[0].get('vmId')) == state.get('vm_id'),
                'same-purpose VM does not match the recorded admission; stop for reuse review')
    return {'catalog': catalog, 'placement': placement,
            'same_purpose_vm_count': len(same_purpose), 'tenant_vm_count': len(inventory)}


def frozen(g, t):
    require(STATE.exists() and STATE.stat().st_mode & 0o077 == 0, 'private prepared state is missing')
    state = json.loads(STATE.read_text())
    raw, key = key_input()
    require(state['key'] == KEY and state['key_identity'] == key and state['helper_sha256'] == helper_digest(g, t),
            'frozen intent, key identity or helper changed')
    require(state['body']['data']['guestInitialization']['sshPublicKey'] == raw and digest(state['body']) == state['body_sha256'],
            'frozen body changed; do not replace its key')
    return state


def publish(g, state, phase):
    proposal = {'name': NAME, 'tenant_id': state['tenant_id'], 'idempotency_key': KEY,
                'preset_id': state['body']['data']['presetId'], 'cpu_millis': 1500, 'memory_mib': 2048,
                'root_size_gb_decimal': 12, 'volume_mode': 'Block', 'access_modes': ['ReadWriteOnce'],
                'data_disks': 0, 'media_key': 'ubuntu-24.04', 'source_kind': 'DISK_IMAGE',
                'start_after_create': True, 'network': state['body']['data']['network'],
                'guest_initialization': {'username': 'aniuser', 'console_password_set': True,
                                         'ssh_public_key_fingerprint': state['key_identity']['fingerprint']},
                'resource_budget': 'one additional VM/VMI/launcher, one independent 12GB Block root DV/PVC/PV, one VM-owned NoCloud Secret and Attachment; no data disks',
                'ssh_transport': 'task VMI virtctl port-forward to loopback only; strict host-key pin obtained through the authenticated serial console; existing Fedora identity with IdentitiesOnly/BatchMode/publickey only',
                'cleanup': 'normal Gov DELETE at fresh version using one persisted key, then exact native/PVC/PV/CSI backend and Attachment reclamation; existing four VMs and shared source/NAD/namespace preserved',
                'key_identity': state['key_identity'], 'body_sha256': state['body_sha256'],
                'driver_source_sha256': CREATE_SOURCE_SHA, 'helper_sha256': state['helper_sha256']}
    receipt = {'phase': phase, 'proposal': proposal, 'approval_plan_sha256': digest(proposal),
               'preflight': state['preflight'], 'private_state_reference': str(STATE),
               'private_guest_reference': str(GUEST), 'create_send_attempts': state['send_attempts'],
               'vm_id': state.get('vm_id'), 'operation_id': state.get('operation_id'),
               'operation': state.get('operation'), 'current': state.get('current'),
               'guest_ssh': state.get('guest_ssh', 'NOT_RUN'), 'browser': 'NOT_RUN',
               'native': state.get('native'), 'family': state.get('family'), 'delete': state.get('delete'),
               'cleanup': state.get('cleanup'), 'current_driver_source_sha256': sha(__file__),
               'public_network': 'NOT_RUN_DEFERRED_BY_USER'}
    require(receipt['approval_plan_sha256'] == APPROVED_PLAN_SHA, 'original approved CREATE proposal changed')
    g.save(RECEIPT, receipt)
    return receipt


def prepare(g, t, token, principal):
    raw, identity = key_input()
    checked = preflight(g, t, token)
    if STATE.exists():
        state = frozen(g, t)
        state['preflight'] = checked
    else:
        require(not GUEST.exists(), 'guest credential already exists without its original intent')
        guest = {'username': 'aniuser', 'password': 'Vm1!' + secrets.token_hex(20)}
        g.save(GUEST, guest)
        body = {'data': {'name': NAME, 'presetId': t.PRESET, 'sourceKind': 'DISK_IMAGE',
                         'mediaKey': 'ubuntu-24.04', 'systemDiskSizeGb': '12', 'dataDisks': [],
                         'idempotencyKey': KEY, 'startAfterCreate': True,
                         'network': {'vpcId': t.VPC, 'subnetId': t.SUBNET},
                         'guestInitialization': dict(guest, sshPublicKey=raw)}}
        state = {'tenant_id': t.TENANT, 'principal': principal, 'key': KEY, 'key_identity': identity,
                 'body': body, 'body_sha256': digest(body), 'helper_sha256': helper_digest(g, t),
                 'preflight': checked, 'send_attempts': 0}
    g.save(STATE, state)
    return publish(g, state, 'PREPARED_NO_VM_POST')


def observe(g, state, token, seconds):
    require(state.get('vm_id') and state.get('operation_id'), 'no persisted ACK to query; unknown ACK requires original-key recovery review')
    deadline = time.monotonic() + seconds
    while True:
        path = BASE + '/' + state['vm_id']
        state['operation'] = g.api('GET', path + '/operations/' + state['operation_id'], token=token)
        state['current'] = g.api('GET', path, token=token)
        g.save(STATE, state)
        if str(state['operation'].get('state', '')).lower() in ('succeeded', 'failed') or time.monotonic() >= deadline:
            return publish(g, state, 'OBSERVED_ORIGINAL_OPERATION')
        time.sleep(1)


def kubectl(t, kind, name=None, namespace=None, labels=None):
    argv = t.K + ['get', kind]
    if name:
        argv += [name, '--ignore-not-found']
    if namespace:
        argv += ['-n', namespace]
    if labels:
        argv += ['-l', labels]
    result = subprocess.run(argv + ['-o', 'json'], check=True, capture_output=True, text=True, timeout=15)
    return json.loads(result.stdout) if result.stdout.strip() else None


def attachment(g, t, state):
    dsn = urllib.parse.urlsplit((RUN / 'private/resource-candidate/owner.dsn').read_text().strip())
    require(dsn.path == '/vm_resource_candidate_01a168ce', 'wrong task Resource DB')
    vmid = str(uuid.UUID(state['vm_id']))
    sql = ("SELECT COALESCE(json_agg(json_build_object('id',attachment_id,'state',state,'blocked',protocol_blocked)), '[]'::json) "
           "FROM network_attachments WHERE tenant_id='" + t.TENANT + "' AND instance_id='" + vmid + "'")
    rows = json.loads(g.psql(urllib.parse.unquote(dsn.username), sql, database=dsn.path[1:]))
    require(len(rows) <= 1, 'ambiguous proof Attachment')
    old = state.get('native', {}).get('attachment', [])
    require(not old or not rows or old[0]['id'] == rows[0]['id'], 'proof Attachment changed')
    return rows


def protect(t):
    config = json.loads((RUN / 'private/resource-candidate/vm.json').read_text())
    sources = []
    for media in config['Service']['Media']:
        pvc = kubectl(t, 'pvc', media['pvc_name'], media['namespace'])
        require(pvc and pvc['metadata']['uid'] == media['pvc_uid'] and pvc['status']['phase'] == 'Bound'
                and pvc['metadata'].get('annotations', {}).get('vm.ani.io/source-sha256') == media['checksum'], 'protected media source changed')
        sources.append({'media_key': media['media_key'], **t.ref(pvc), 'checksum': media['checksum']})
    networks = []
    for mapping in config['Networks']:
        ns = kubectl(t, 'ns', mapping['Namespace'])
        nad = kubectl(t, 'network-attachment-definitions.k8s.cni.cncf.io', mapping['NADName'], mapping['Namespace'])
        require(ns['metadata']['uid'] == mapping['NamespaceUID'] and nad['metadata']['uid'] == mapping['NADUID']
                and digest(json.loads(nad['spec']['config'])) == mapping['NADConfigDigest'], 'protected namespace/NAD changed')
        networks.append({'namespace': t.ref(ns), 'nad': t.ref(nad), 'digest': mapping['NADConfigDigest']})
    logical = ['55099440-c47b-4f81-afc5-7d8e740d14dc', 'e3c11c5a-cf12-4630-a8f9-f907048ffdfc',
               '068d56dd-d4fc-4ca8-8430-16c36bad0761', 'fc154879-8a40-4b18-967c-215f5cf5992d']
    protected = []
    for vmid in logical:
        ns = t.NS if vmid == logical[1] else 'tenant-b3c946b5-7eb3-4feb-9f82-79712062ca5c'
        vm = kubectl(t, 'vm', 'vm-' + vmid, ns)
        require(vm and vm['metadata'].get('labels', {}).get('vm.ani.io/vm-id') == vmid, 'protected VM missing or replaced')
        protected.append(t.ref(vm))
    return {'sources': sources, 'networks': networks, 'four_existing_vms': protected}


def capture(g, t, state, token):
    require(state.get('vm_id'), 'no proof CREATE ACK')
    current = g.api('GET', BASE + '/' + state['vm_id'], token=token)
    require(current['id'] == state['vm_id'] and current['name'] == NAME and current['presetId'] == t.PRESET
            and current['mediaKey'] == 'ubuntu-24.04' and not current['closing'], 'proof product intent changed')
    require(kubectl(t, 'ns', 'kube-system')['metadata']['uid'] == t.CLUSTER
            and kubectl(t, 'ns', t.NS)['metadata']['uid'] == t.NS_UID, 'proof cluster/namespace changed')
    vm = kubectl(t, 'vm', 'vm-' + state['vm_id'], t.NS)
    result = {'vm_id': state['vm_id'], 'current': current, 'attachment': attachment(g, t, state), 'objects': []}
    if not vm:
        state['native_pending'] = result
        g.save(STATE, state)
        return result
    require(vm['metadata'].get('labels', {}).get('ani.io/tenant-id') == t.TENANT
            and vm['metadata'].get('labels', {}).get('vm.ani.io/vm-id') == state['vm_id'], 'proof VM labels differ')
    result['vm'] = t.ref(vm)
    result['objects'].append(t.ref(vm))
    spec = vm['spec']['template']['spec']
    require(spec['domain']['firmware']['uuid'] == state['vm_id'], 'proof firmware identity differs')
    volumes = [x for x in spec['volumes'] if 'dataVolume' in x]
    require(len(volumes) == 1, 'proof VM must have exactly one independent root')
    dv = kubectl(t, 'dv', volumes[0]['dataVolume']['name'], t.NS)
    if dv:
        require(t.owned(dv, 'VirtualMachine', vm['metadata']['uid']), 'proof DV owner differs')
        result['dv'], result['dv_phase'] = t.ref(dv), dv.get('status', {}).get('phase')
        result['objects'].append(t.ref(dv))
        pvc = kubectl(t, 'pvc', dv['metadata']['name'], t.NS)
        if pvc:
            require((t.owned(pvc, 'DataVolume', dv['metadata']['uid']) or t.owned(pvc, 'VirtualMachine', vm['metadata']['uid']))
                    and pvc['spec'].get('storageClassName') == 'ani-block' and pvc['spec'].get('volumeMode') == 'Block'
                    and pvc['spec'].get('accessModes') == ['ReadWriteOnce'], 'proof root ownership/Block contract differs')
            result['pvc'], result['pvc_phase'] = t.ref(pvc), pvc.get('status', {}).get('phase')
            result['requested_storage'] = pvc['spec']['resources']['requests']['storage']
            result['objects'].append(t.ref(pvc))
            if pvc['spec'].get('volumeName'):
                pv = kubectl(t, 'pv', pvc['spec']['volumeName'])
                require(pv['spec']['claimRef']['uid'] == pvc['metadata']['uid'], 'proof PV claim changed')
                csi = pv['spec']['csi']; attrs = csi.get('volumeAttributes', {})
                require(csi['driver'] == 'rook-ceph.rbd.csi.ceph.com' and attrs.get('pool') == 'ani-block-pool'
                        and attrs.get('clusterID') == 'rook-ceph', 'proof CSI storage differs')
                identity = str(uuid.UUID(csi['volumeHandle'][-36:]))
                image = 'csi-vol-' + identity
                require(attrs.get('imageName', image) == image, 'proof CSI image differs from handle')
                result['pv'], result['pv_csi'] = t.ref(pv), {'driver': csi['driver'], 'volumeHandle': csi['volumeHandle'],
                                                                  'pool': 'ani-block-pool', 'image': image}
                result['objects'].append(t.ref(pv))
                vas = kubectl(t, 'volumeattachments.storage.k8s.io')['items']
                result['volume_attachments'] = [t.ref(x) for x in vas if x['spec']['source'].get('persistentVolumeName') == pv['metadata']['name']]
                result['objects'] += result['volume_attachments']
    for volume in spec['volumes']:
        secret_name = volume.get('cloudInitNoCloud', {}).get('secretRef', {}).get('name')
        if secret_name:
            secret = kubectl(t, 'secret', secret_name, t.NS)
            require(secret and t.owned(secret, 'VirtualMachine', vm['metadata']['uid']), 'NoCloud Secret owner differs')
            result['nocloud_secret'] = t.ref(secret)  # Never publish Secret data.
            result['objects'].append(t.ref(secret))
    vmi = kubectl(t, 'vmi', vm['metadata']['name'], t.NS)
    if vmi:
        require(t.owned(vmi, 'VirtualMachine', vm['metadata']['uid']), 'proof VMI owner differs')
        result.update(vmi=t.ref(vmi), phase=vmi.get('status', {}).get('phase'), node=vmi.get('status', {}).get('nodeName'),
                      conditions=vmi.get('status', {}).get('conditions'), interfaces=vmi.get('status', {}).get('interfaces'))
        result['objects'].append(t.ref(vmi))
        pods = kubectl(t, 'pod', namespace=t.NS, labels='kubevirt.io/created-by=' + vmi['metadata']['uid'])['items']
        pods = [x for x in pods if t.owned(x, 'VirtualMachineInstance', vmi['metadata']['uid']) and not x['metadata'].get('deletionTimestamp')]
        require(len(pods) <= 1, 'ambiguous proof launcher')
        if pods:
            pod = pods[0]
            result['launcher'] = t.ref(pod)
            result['launcher_phase'] = pod.get('status', {}).get('phase')
            result['launcher_waiting'] = [x.get('state', {}).get('waiting') for x in pod.get('status', {}).get('containerStatuses', [])]
            result['objects'].append(t.ref(pod))
    old = state.get('native', {})
    for field in ('vm', 'dv', 'pvc', 'pv', 'pv_csi', 'vmi', 'launcher', 'nocloud_secret'):
        require(not old.get(field) or old[field] == result.get(field), 'previously frozen proof identity changed: ' + field)
    state['native'] = result
    if not state.get('protected_before'):
        state['protected_before'] = protect(t)
    state['current'] = current
    g.save(STATE, state)
    publish(g, state, 'NATIVE_CAPTURED')
    return result


def ceph(t, argv):
    reader = kubectl(t, 'pod', 'rook-ceph-tools-68d6bc7cc7-2z8tk', 'rook-ceph')
    require(reader and reader['metadata']['uid'] == 'd144f419-df30-43d0-aa23-fcbcda6e01ce'
            and reader['status']['phase'] == 'Running', 'approved read-only RBD client changed')
    result = subprocess.run(t.K + ['exec', '-n', 'rook-ceph', reader['metadata']['name'], '--'] + argv,
                            capture_output=True, timeout=8)
    if result.returncode:
        require(b'No such file or directory' in result.stderr, 'exact RBD read failed, absence unknown')
        return None
    return json.loads(result.stdout)


def image_info(t, name):
    value = ceph(t, ['rbd', 'info', 'ani-block-pool/' + name, '--format=json'])
    if value is None:
        return {'name': name, 'result': 'ABSENT'}
    require(value['name'] == name, 'RBD returned another image')
    return {k: value.get(k) for k in ('name', 'id', 'parent', 'size')} | {'result': 'PRESENT'}


def freeze_backend(g, t, state):
    native = state['native']
    require(native.get('pv_csi') and native['pvc_phase'] == 'Bound', 'root backend not allocated yet')
    name = native['pv_csi']['image']
    family = [image_info(t, name), image_info(t, name + '-temp')]
    require(family[0]['result'] == 'PRESENT', 'bound root has no measurable RBD base')
    if state.get('family'):
        for old, current in zip(state['family'], family):
            require(old['result'] == current['result'] and old.get('id') == current.get('id'), 'frozen RBD family changed')
    else:
        state['family'] = family
    g.save(STATE, state)
    publish(g, state, 'ROOT_BACKEND_FROZEN')


def serial_code(code):
    encoded = base64.b64encode(zlib.compress(code.encode())).decode()
    line = 'python3 -c ' + shlex.quote('import base64,zlib;exec(zlib.decompress(base64.b64decode(' + repr(encoded) + ')))')
    require(len(line.encode()) < 4000, 'bounded serial command exceeded')
    return line


def prepare_proxy(g, t, state, token):
    current = g.api('GET', BASE + '/' + state['vm_id'], token=token)
    require(current['id'] == state['vm_id'] and current['observedState'] == 'Running'
            and not current['closing'], 'proof VM no longer Running')
    checked = preflight(g, t, token)
    native = state['native']
    vm = kubectl(t, 'vm', native['vm']['name'], t.NS)
    vmi = kubectl(t, 'vmi', native['vmi']['name'], t.NS)
    require(t.ref(vm) == native['vm'] and t.ref(vmi) == native['vmi'] and vmi['status']['phase'] == 'Running', 'proof VM/VMI identity changed')
    interfaces = [x for x in vmi['status']['interfaces'] if x.get('name') == 'default']
    require(len(interfaces) == 1 and interfaces[0].get('ipAddress') == '10.84.1.3', 'exact proof Guest IP changed')
    require(kubectl(t, 'pod', PROXY_NAME, t.NS) is None, 'proxy name is not absent; never adopt it')
    node = kubectl(t, 'node', 'ani-02')
    require(node['metadata']['uid'] == t.NODE_UIDS['ani-02']
            and node['metadata']['labels'].get('kubernetes.io/hostname') == 'ani-02'
            and any(x.get('type') == 'InternalIP' and x.get('address') == '172.16.101.11'
                    for x in node['status']['addresses']), 'tool cache node identity/address changed')
    cached = [x for x in node['status'].get('images', []) if PROXY_IMAGE in x['names']]
    require(len(cached) == 1, 'reviewed already-used CDI image is not cached')
    pods = kubectl(t, 'pod', namespace=t.NS)['items']
    # All existing same-VPC pods are the two protected virt-launchers. Do not
    # turn a production launcher into a test service or modify its VM bridge.
    require(all(x['metadata'].get('labels', {}).get('kubevirt.io') == 'virt-launcher' for x in pods),
            'another same-VPC Pod exists; review reuse before allocating a proxy')
    source = checked['placement']['network_mapping_active']
    require(source is True, 'tenant2 mapping no longer active')
    manifest = {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {'name': PROXY_NAME, 'namespace': t.NS,
        'labels': {'ani.io/task': 'vm-mvp-01a168ce', 'ani.io/purpose': 'ssh-publickey-proof-proxy'},
        'annotations': {'v1.multus-cni.io/default-network': t.NS + '/' + t.NAD,
                        'networking.kubercloud.com/subnet': t.POLICY['KCNSubnetRef']}},
        'spec': {'automountServiceAccountToken': False, 'restartPolicy': 'Never', 'activeDeadlineSeconds': 1800,
            'nodeSelector': {'kubernetes.io/hostname': 'ani-02'},
            'securityContext': {'runAsNonRoot': True, 'runAsUser': 107, 'runAsGroup': 107,
                                'seccompProfile': {'type': 'RuntimeDefault'}},
            'containers': [{'name': 'proxy', 'image': PROXY_IMAGE, 'imagePullPolicy': 'Never',
                'command': ['/bin/sh', '-ec', 'test -x /bin/bash; command -v cat >/dev/null; exec sleep 1800'],
                'resources': {'requests': {'cpu': '25m', 'memory': '32Mi'}, 'limits': {'cpu': '100m', 'memory': '128Mi'}},
                'securityContext': {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True,
                                    'capabilities': {'drop': ['ALL']}}}]}}
    # Existing image has /bin/sh, cat and sleep from the successful native Block
    # probes. /bin/bash is guarded, never installed. Its builtin /dev/tcp avoids
    # assuming nc is present and forwards only this fixed guest's SSH port.
    relay = 'exec 3<>/dev/tcp/10.84.1.3/22; cat <&3 & reader=$!; cat >&3; kill "$reader" 2>/dev/null || true; wait "$reader" 2>/dev/null || true'
    proposal = {'scope': 'one temporary ordinary nonroot task Pod on the existing tenant2 VPC; actual SSH stays key-only on Fedora',
        'vm_id': state['vm_id'], 'vm': native['vm'], 'vmi': native['vmi'], 'guest_ip': '10.84.1.3',
        'namespace_uid': t.NS_UID, 'nad_uid': t.NAD_UID, 'nad_config_sha256': t.NAD_SHA,
        'cluster_uid': t.CLUSTER, 'manifest': manifest, 'manifest_sha256': digest(manifest),
        'cached_image': {'node': 'ani-02', 'node_uid': t.NODE_UIDS['ani-02'], 'node_internal_ip': '172.16.101.11', **cached[0]},
        'existing_same_namespace_pods': [t.ref(x) for x in pods],
        'proxy_command': t.K + ['exec', '-i', '-n', t.NS, PROXY_NAME, '--', '/bin/bash', '-ec', relay],
        'host_key_fingerprint': state['guest_ssh']['host_key_fingerprint'],
        'user_key_fingerprint': state['key_identity']['fingerprint'],
        'cleanup': 'stop the task-owned exec; normal UID/RV guarded Pod DELETE and observe Pod/VNic gone; normal Gov DELETE of only the new proof VM, exact root family reclamation',
        'forbidden': ['new RBAC or Service', 'image pull/build/tool installation', 'shared routes or KCN changes',
                      'privileged or nsenter', 'private-key copy', 'Guest sshd/account/network changes', 'four existing VM mutation'],
        'ordinary_pod_source': 'same proven launcher NAD/subnet annotations; direct Pod has no kubevirt or managed-VM identity labels',
        'source_sha256': sha(__file__)}
    result = {'phase': 'PREPARED_NO_PROXY_CREATE', 'proposal': proposal, 'proposal_sha256': digest(proposal),
              'create_send_attempts': 0, 'original_create_plan_sha256': APPROVED_PLAN_SHA,
              'root_authorized_correction': 'approved proposal 7f082f6b5542c1012c5f5c374701b4dc5c46b68c6c2339cbdf0db6387006d61b; Root explicitly required cached ani-02 nodeSelector with fresh UID/address before CREATE',
              'original_portforward': 'INCONCLUSIVE_BANNER_TIMEOUT_BEFORE_KEY_AUTH',
              'official_portforward_transport': 'https://github.com/kubevirt/kubevirt/blob/v1.9.0/pkg/virt-api/rest/dialers.go#L70-L96'}
    g.save(PROXY_PLAN, result)
    return result


def proxy_guard(t, state, plan, allow_pending=False):
    proposal = plan['proposal']
    require(kubectl(t, 'ns', 'kube-system')['metadata']['uid'] == t.CLUSTER
            and kubectl(t, 'ns', t.NS)['metadata']['uid'] == t.NS_UID, 'proxy cluster/namespace changed')
    vm = kubectl(t, 'vm', proposal['vm']['name'], t.NS)
    vmi = kubectl(t, 'vmi', proposal['vmi']['name'], t.NS)
    require(t.ref(vm) == proposal['vm'] and t.ref(vmi) == proposal['vmi']
            and vmi['status']['phase'] == 'Running' and not vm['metadata'].get('deletionTimestamp'), 'proxy proof VM/VMI changed')
    require(any(x.get('name') == 'default' and x.get('ipAddress') == proposal['guest_ip'] for x in vmi['status']['interfaces']), 'proxy exact Guest IP changed')
    pod = kubectl(t, 'pod', PROXY_NAME, t.NS)
    proxy = state.get('proxy', {})
    if not proxy.get('pod'):
        require(pod is None, 'proxy name exists without original ACK; do not adopt or recreate')
        return None
    require(pod and t.ref(pod) == proxy['pod'] and not pod['metadata'].get('deletionTimestamp'), 'proxy Pod identity changed')
    wanted = proposal['manifest']
    require(all(pod['spec'].get(x) == wanted['spec'][x] for x in ('nodeSelector', 'securityContext', 'automountServiceAccountToken', 'restartPolicy'))
            and all(pod['metadata'].get('annotations', {}).get(x) == y for x, y in wanted['metadata']['annotations'].items()), 'proxy Pod frozen spec/network changed')
    container = pod['spec']['containers'][0]
    require(all(container.get(x) == wanted['spec']['containers'][0][x] for x in ('image', 'imagePullPolicy', 'securityContext', 'resources', 'command')),
            'proxy container contract changed')
    if not allow_pending:
        require(pod['status'].get('phase') == 'Running' and pod['spec'].get('nodeName') == 'ani-02'
                and len(pod['status'].get('containerStatuses', [])) == 1 and pod['status']['containerStatuses'][0].get('ready') is True
                and pod['status']['containerStatuses'][0].get('imageID', '').endswith(PROXY_IMAGE.split('@')[1]), 'proxy has not started with the existing cached image')
    return pod


def proxy_create(g, t, state, approved_sha):
    plan = json.loads(PROXY_PLAN.read_text())
    require(digest(plan['proposal']) == plan['proposal_sha256'] == approved_sha, 'approved corrected proxy proposal differs')
    proposal = plan['proposal']
    require(proposal['vm_id'] == state['vm_id'] and proposal['manifest']['metadata']['name'] == PROXY_NAME
            and proposal['manifest']['spec']['nodeSelector'] == {'kubernetes.io/hostname': 'ani-02'}, 'proxy escaped approved task or cached node')
    node = kubectl(t, 'node', 'ani-02')
    require(node['metadata']['uid'] == t.NODE_UIDS['ani-02'] and any(x.get('type') == 'InternalIP' and x.get('address') == '172.16.101.11' for x in node['status']['addresses'])
            and any(PROXY_IMAGE in x['names'] for x in node['status']['images']), 'cached-node UID/address/image changed')
    nad = kubectl(t, 'network-attachment-definitions.k8s.cni.cncf.io', t.NAD, t.NS)
    require(nad['metadata']['uid'] == t.NAD_UID and digest(json.loads(nad['spec']['config'])) == t.NAD_SHA, 'proxy NAD changed')
    proxy_guard(t, state, plan)
    require(not state.get('proxy'), 'proxy CREATE was already attempted; observe its original ACK')
    state['proxy'] = {'plan_sha256': approved_sha, 'create_send_attempts': 1, 'outcome': 'ACK_UNKNOWN_UNTIL_PERSISTED'}
    g.save(STATE, state)
    created = subprocess.run(t.K + ['create', '-f', '-', '-o', 'json'], input=json.dumps(proposal['manifest']),
                             check=True, capture_output=True, text=True, timeout=15)
    pod = json.loads(created.stdout)
    require(pod['metadata']['name'] == PROXY_NAME and pod['metadata']['namespace'] == t.NS, 'proxy ACK changed')
    state['proxy'].update(pod=t.ref(pod), outcome='ACK_PERSISTED')
    g.save(STATE, state)
    return proxy_observe(g, t, state)


def proxy_observe(g, t, state):
    plan = json.loads(PROXY_PLAN.read_text())
    require(state.get('proxy', {}).get('pod'), 'no original proxy ACK; unknown result needs explicit recovery')
    pod = proxy_guard(t, state, plan, allow_pending=True)
    result = {'pod': t.ref(pod), 'phase': pod['status'].get('phase'), 'node': pod['spec'].get('nodeName'),
              'conditions': pod['status'].get('conditions'), 'container_statuses': pod['status'].get('containerStatuses'),
              'annotations': {k: v for k, v in pod['metadata'].get('annotations', {}).items() if 'network' in k},
              'plan_sha256': state['proxy']['plan_sha256'], 'create_send_attempts': state['proxy']['create_send_attempts']}
    vnic_name = pod['metadata'].get('annotations', {}).get('networking.kubercloud.com/vnic')
    if vnic_name:
        vnic = kubectl(t, 'vnics.networking.kubercloud.com', vnic_name, t.NS)
        require(vnic and any(x.get('uid') == pod['metadata']['uid'] and x.get('kind') == 'Pod' for x in vnic['metadata'].get('ownerReferences', [])), 'proxy VNic owner differs')
        result['vnic'] = t.ref(vnic)
        state['proxy']['vnic'] = t.ref(vnic)
    g.save(RUN / 'evidence/product-cloudinit-ssh-proxy.json', result)
    g.save(STATE, state)
    return result


def proxy_ssh(g, t, state):
    plan = json.loads(PROXY_PLAN.read_text())
    pod = proxy_guard(t, state, plan)
    require(state['proxy']['plan_sha256'] == plan['proposal_sha256'] == digest(plan['proposal']), 'frozen proxy plan changed')
    prior = state['guest_ssh']
    require(prior['serial_identity']['instance_id'] == prior['serial_identity']['firmware_uuid'] == state['vm_id']
            and prior['serial_identity']['datasource'].startswith('DataSourceNoCloud'), 'no authenticated serial identity/hostkey pin')
    old_known = (PRIVATE / 'cloudinit-ssh-known-hosts').read_text().strip().split()
    require(len(old_known) == 3 and old_known[1] == 'ssh-ed25519', 'authenticated serial host key pin missing')
    blob = base64.b64decode(old_known[2], validate=True)
    require('SHA256:' + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip('=') == prior['host_key_fingerprint'], 'serial host key pin changed')
    alias = 'vm-cloudinit-ssh-' + state['vm_id']
    known = PRIVATE / 'cloudinit-ssh-proxy-known-hosts'
    line = alias + ' ' + ' '.join(old_known[1:]) + '\n'
    if known.exists():
        require(not known.is_symlink() and known.read_text() == line and known.stat().st_mode & 0o077 == 0, 'proxy host-key pin changed')
    else:
        with known.open('x') as handle:
            os.chmod(known, 0o600); handle.write(line); handle.flush(); os.fsync(handle.fileno())
    marker = 'vm-mvp-01a168ce-cloudinit-ssh-' + state['vm_id']
    code = '''import getpass,json,os,pathlib,subprocess
r=lambda p:pathlib.Path(p).read_text().strip()
assert getpass.getuser()=='aniuser'
p=pathlib.Path('/home/aniuser/vm-mvp-cloudinit-ssh-marker'); expected=MARKER+'\\n'; created=False
if p.exists():
 assert p.is_file() and not p.is_symlink() and p.read_text()==expected,'foreign marker'
else:
 fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 with os.fdopen(fd,'w') as f:f.write(expected);f.flush();os.fsync(f.fileno())
 d=os.open(p.parent,os.O_DIRECTORY);os.fsync(d);os.close(d);created=True
print('ANI_SSH_PROOF='+json.dumps({'username':getpass.getuser(),'marker_path':str(p),'marker':p.read_text().strip(),'marker_created_fsynced':created,'instance_id':r('/var/lib/cloud/data/instance-id'),'boot_id':r('/proc/sys/kernel/random/boot_id'),'authorized_fingerprints':subprocess.check_output(['ssh-keygen','-l','-E','sha256','-f','/home/aniuser/.ssh/authorized_keys'],text=True).split()}))'''.replace('MARKER+', repr(marker) + '+')
    result = {'result': 'INCONCLUSIVE', 'layer': 'GUEST', 'browser': 'NOT_RUN', 'public_network': 'NOT_RUN',
              'vm_id': state['vm_id'], 'vm': state['native']['vm'], 'vmi': state['native']['vmi'], 'proxy': t.ref(pod),
              'serial_identity': prior['serial_identity'], 'host_key_fingerprint': prior['host_key_fingerprint'],
              'key_fingerprint': state['key_identity']['fingerprint'], 'driver_sha256': sha(__file__),
              'transport': 'existing Fedora SSH identity signs through nonroot ordinary same-VPC Pod, no private-key transfer',
              'original_portforward': 'INCONCLUSIVE_BANNER_TIMEOUT_BEFORE_KEY_AUTH'}
    command = ['ssh', '-vv', '-F', '/dev/null', '-i', str(IDENTITY), '-o', 'UserKnownHostsFile=' + str(known),
               '-o', 'GlobalKnownHostsFile=/dev/null', '-o', 'HostKeyAlias=' + alias, '-o', 'StrictHostKeyChecking=yes',
               '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes', '-o', 'PreferredAuthentications=publickey',
               '-o', 'PasswordAuthentication=no', '-o', 'KbdInteractiveAuthentication=no', '-o', 'ConnectTimeout=8',
               '-o', 'ProxyCommand=' + shlex.join(plan['proposal']['proxy_command']), 'aniuser@' + alias, serial_code(code)]
    completed = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
    g.save(PRIVATE / 'cloudinit-ssh-proxy-client.log', completed.stderr)
    result['ssh_exit'] = completed.returncode
    values = [json.loads(x.split('=', 1)[1]) for x in completed.stdout.splitlines() if x.startswith('ANI_SSH_PROOF=')]
    if completed.returncode == 0 and len(values) == 1:
        value = values[0]
        require(value['username'] == 'aniuser' and value['instance_id'] == state['vm_id']
                and value['boot_id'] == prior['serial_identity']['boot_id'] and value['marker'] == marker
                and state['key_identity']['fingerprint'] in value['authorized_fingerprints']
                and re.search(r'Authenticated to .* using "publickey"', completed.stderr), 'SSH proof/key/guest identity differs')
        proxy_guard(t, state, plan)
        result.update(result='PASS', guest_proof=value, authentication='publickey', strict_host_key_checking=True,
                      password_authentication_attempted=False, existing_identity_only=True)
    else:
        result['error'] = 'strict SSH did not complete; exact private client diagnostic retained, no key-auth success claim'
    g.save(RUN / 'evidence/product-cloudinit-ssh-proxy-guest.json', result)
    state['guest_ssh'] = result
    g.save(STATE, state)
    return publish(g, state, 'PROXY_GUEST_SSH_' + result['result'])


def proxy_cleanup(g, t, state):
    proxy = state['proxy']
    require(proxy.get('pod'), 'no original proxy ACK')
    pod = kubectl(t, 'pod', PROXY_NAME, t.NS)
    require(not pod or t.ref(pod) == proxy['pod'], 'proxy Pod replaced; never delete foreign object')
    if pod and not proxy.get('delete_send_attempts'):
        require(kubectl(t, 'ns', t.NS)['metadata']['uid'] == t.NS_UID, 'proxy namespace changed')
        request = {'apiVersion': 'v1', 'kind': 'DeleteOptions', 'preconditions': {
            'uid': proxy['pod']['uid'], 'resourceVersion': pod['metadata']['resourceVersion']}, 'propagationPolicy': 'Background'}
        proxy.update(delete_send_attempts=1, delete_request=request, delete_outcome='ACK_UNKNOWN_UNTIL_PERSISTED')
        g.save(STATE, state)
        ack = subprocess.run(t.K + ['delete', '--raw', '/api/v1/namespaces/' + t.NS + '/pods/' + PROXY_NAME, '-f', '-'],
                             input=json.dumps(request), check=True, capture_output=True, text=True, timeout=15)
        response = json.loads(ack.stdout)
        require(response.get('metadata', {}).get('uid') == proxy['pod']['uid'] or response.get('status') == 'Success', 'proxy DELETE ACK differs')
        proxy['delete_outcome'] = 'ACK_PERSISTED'; g.save(STATE, state)
        pod = kubectl(t, 'pod', PROXY_NAME, t.NS)
    vnic = None
    if proxy.get('vnic'):
        vnic = kubectl(t, 'vnics.networking.kubercloud.com', proxy['vnic']['name'], t.NS)
        require(not vnic or t.ref(vnic) == proxy['vnic'], 'proxy VNic replaced; absence not inferred')
    proxy['cleanup'] = {'result': 'PASS' if not pod and not vnic else 'PENDING_NORMAL_GC',
                        'pod_gone': pod is None, 'vnic_gone': vnic is None, 'manual_vnic_delete': False,
                        'force_or_finalizer_patch': False, 'delete_send_attempts': proxy.get('delete_send_attempts', 0)}
    g.save(STATE, state)
    g.save(RUN / 'evidence/product-cloudinit-ssh-proxy-cleanup.json', proxy)
    return publish(g, state, 'PROXY_' + proxy['cleanup']['result'])


def guest(g, t, state, token):
    import pexpect
    state['operation'] = g.api('GET', BASE + '/' + state['vm_id'] + '/operations/' + state['operation_id'], token=token)
    require(state['operation']['id'] == state['operation_id'] and state['operation']['vmId'] == state['vm_id']
            and state['operation']['state'] == 'succeeded', 'original CREATE has not converged; observe it, do not issue Guest commands')
    native = capture(g, t, state, token)
    require(native.get('phase') == 'Running' and native.get('launcher_phase') == 'Running'
            and native.get('pvc_phase') == 'Bound', 'proof guest is not Running with a Bound root')
    freeze_backend(g, t, state)
    require(sha(VIRTCTL) == VIRTCTL_SHA, 'official virtctl checksum differs')
    credential = json.loads(GUEST.read_text())
    require(credential['username'] == 'aniuser' and GUEST.stat().st_mode & 0o077 == 0, 'private original guest credential differs')
    password = credential['password']; transcript = io.StringIO()
    result = {'vm_id': state['vm_id'], 'native': native, 'layer': 'GUEST', 'browser': 'NOT_RUN',
              'public_network': 'NOT_RUN', 'key_fingerprint': state['key_identity']['fingerprint'],
              'driver_sha256': sha(__file__), 'result': 'INCONCLUSIVE'}
    if GUEST_RESULT.exists():
        prior = RUN / 'evidence/product-cloudinit-ssh-guest-attempt01.json'
        require(not prior.exists(), 'two guest attempts already recorded; stop rather than loop')
        with prior.open('x') as handle:
            os.chmod(prior, 0o600); handle.write(GUEST_RESULT.read_text()); handle.flush(); os.fsync(handle.fileno())
        result['prior_attempt_reference'] = str(prior)
    child = pexpect.spawn(str(VIRTCTL), ['console', native['vm']['name'], '--namespace', t.NS,
                           '--kubeconfig', t.K[1].split('=', 1)[1], '--timeout=20'], encoding='utf-8', timeout=30)
    child.logfile_read = transcript
    tunnel = None
    try:
        child.expect(r'Successfully connected to .* console\. Press Ctrl\+\] or Ctrl\+5 to exit console\.')
        child.sendline('')
        hit = child.expect([r'login:', r'\$ '])
        if hit == 1:
            child.sendline('exit'); child.expect(r'login:')
        child.sendline(credential['username']); child.expect(r'[Pp]assword:'); started = time.monotonic()
        child.sendline(password)
        hit = child.expect([r'\$ ', r'Login incorrect'], timeout=90)
        result['password_to_shell_seconds'] = time.monotonic() - started
        require(hit == 0, 'normal serial authentication was explicitly rejected')
        child.sendline("sudo -v -p 'ANI_SSH_SUDO: '")
        if child.expect([r'(?:^|[\r\n])ANI_SSH_SUDO: ', r'\$ ']) == 0:
            child.sendline(password); child.expect(r'\$ ')
        code = '''import os,json,pathlib,subprocess
r=lambda p:pathlib.Path(p).read_text().strip()
print('ANI_SSH_PIN='+json.dumps({'username':os.environ.get('SUDO_USER'),'instance_id':r('/var/lib/cloud/data/instance-id'),'datasource':r('/var/lib/cloud/instance/datasource'),'firmware_uuid':r('/sys/class/dmi/id/product_uuid'),'boot_id':r('/proc/sys/kernel/random/boot_id'),'host_public_key':r('/etc/ssh/ssh_host_ed25519_key.pub'),'authorized_fingerprints':subprocess.check_output(['ssh-keygen','-l','-E','sha256','-f','/home/aniuser/.ssh/authorized_keys'],text=True).split(),'tcp_listeners':subprocess.check_output(['ss','-lnt'],text=True),'ssh_socket':subprocess.run(['systemctl','show','ssh.socket','--property=ActiveState,Listen'],capture_output=True,text=True).stdout}))'''
        child.sendline('sudo -n ' + serial_code(code))
        child.expect(r'(?:^|[\r\n])ANI_SSH_PIN=(\{[^\r\n]+\})\r?\n', timeout=20)
        pin = json.loads(child.match.group(1)); child.expect(r'\$ ')
        require(pin['username'] == 'aniuser' and pin['instance_id'] == pin['firmware_uuid'] == state['vm_id']
                and pin['datasource'].startswith('DataSourceNoCloud')
                and state['key_identity']['fingerprint'] in pin['authorized_fingerprints'], 'authenticated guest NoCloud/key identity differs')
        host_parts = pin.pop('host_public_key').split()
        require(len(host_parts) >= 2 and host_parts[0] == 'ssh-ed25519', 'guest host key is not Ed25519')
        host_blob = base64.b64decode(host_parts[1], validate=True)
        host_fingerprint = 'SHA256:' + base64.b64encode(hashlib.sha256(host_blob).digest()).decode().rstrip('=')
        known = PRIVATE / 'cloudinit-ssh-known-hosts'
        line = '[127.0.0.1]:' + str(PORT) + ' ' + ' '.join(host_parts[:2]) + '\n'
        if known.exists():
            require(not known.is_symlink() and known.read_text() == line and known.stat().st_mode & 0o077 == 0, 'known_hosts pin changed')
        else:
            with known.open('x') as handle:
                os.chmod(known, 0o600); handle.write(line); handle.flush(); os.fsync(handle.fileno())
        result['serial_identity'] = pin
        result['host_key_fingerprint'] = host_fingerprint
        child.sendline('exit'); child.expect(r'login:'); child.close()
        with socket.socket() as check:
            check.bind(('127.0.0.1', PORT))
        argv = [str(VIRTCTL), '--kubeconfig', t.K[1].split('=', 1)[1], 'port-forward',
                'vmi/' + native['vmi']['name'] + '/' + t.NS, 'tcp/' + str(PORT) + ':22', '--address=127.0.0.1']
        log = (PRIVATE / 'cloudinit-ssh-port-forward.log').open('ab')
        tunnel = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
        process = Path('/proc/' + str(tunnel.pid))
        ticks = process.joinpath('stat').read_text().split(') ', 1)[1].split()[19]
        process_ack = {'pid': tunnel.pid, 'start_ticks': ticks, 'argv': argv, 'loopback_port': PORT, 'host_key_fingerprint': host_fingerprint}
        g.save(PRIVATE / 'cloudinit-ssh-port-forward.json', process_ack)
        ready = time.monotonic() + 8
        while True:
            require(tunnel.poll() is None, 'owned loopback port-forward exited')
            # A TCP connect would itself open a real KubeVirt portforward and
            # block this single accept loop while virt-api dials the guest.
            # Read the task-owned listener instead; SSH is the first connection.
            entries = Path('/proc/net/tcp').read_text().splitlines()[1:]
            if any(x.split()[1] == '0100007F:' + format(PORT, '04X') and x.split()[3] == '0A' for x in entries):
                break
            require(time.monotonic() < ready, 'loopback port-forward did not listen')
            time.sleep(.1)
        marker = 'vm-mvp-01a168ce-cloudinit-ssh-' + state['vm_id']
        marker_path = '/home/aniuser/vm-mvp-cloudinit-ssh-marker'
        code = '''import getpass,json,os,pathlib,subprocess
r=lambda p:pathlib.Path(p).read_text().strip()
assert getpass.getuser()=='aniuser'
p=pathlib.Path(MARKER_PATH); expected=MARKER+'\\n'; created=False
if p.exists():
 assert p.is_file() and not p.is_symlink() and p.read_text()==expected,'foreign marker'
else:
 fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 with os.fdopen(fd,'w') as f:f.write(expected);f.flush();os.fsync(f.fileno())
 d=os.open(p.parent,os.O_DIRECTORY);os.fsync(d);os.close(d);created=True
print('ANI_SSH_PROOF='+json.dumps({'username':getpass.getuser(),'marker_path':str(p),'marker':p.read_text().strip(),'marker_created_fsynced':created,'instance_id':r('/var/lib/cloud/data/instance-id'),'boot_id':r('/proc/sys/kernel/random/boot_id'),'authorized_fingerprints':subprocess.check_output(['ssh-keygen','-l','-E','sha256','-f','/home/aniuser/.ssh/authorized_keys'],text=True).split()}))'''.replace('MARKER_PATH', repr(marker_path)).replace('MARKER+', repr(marker) + '+')
        command = ['ssh', '-vv', '-F', '/dev/null', '-p', str(PORT), '-i', str(IDENTITY),
                   '-o', 'UserKnownHostsFile=' + str(known), '-o', 'GlobalKnownHostsFile=/dev/null',
                   '-o', 'StrictHostKeyChecking=yes', '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes',
                   '-o', 'PreferredAuthentications=publickey', '-o', 'PasswordAuthentication=no',
                   '-o', 'KbdInteractiveAuthentication=no', '-o', 'ConnectTimeout=8',
                   'aniuser@127.0.0.1', serial_code(code)]
        checked = subprocess.run(command, capture_output=True, text=True, timeout=30)
        g.save(PRIVATE / 'cloudinit-ssh-client.log', checked.stderr)
        require(checked.returncode == 0, 'strict publickey-only SSH failed; private client diagnostic retained')
        values = [json.loads(x.split('=', 1)[1]) for x in checked.stdout.splitlines() if x.startswith('ANI_SSH_PROOF=')]
        require(len(values) == 1, 'SSH returned no unique guest proof')
        proof = values[0]
        require(proof['username'] == 'aniuser' and proof['instance_id'] == state['vm_id']
                and proof['boot_id'] == pin['boot_id']
                and proof['marker'] == marker and state['key_identity']['fingerprint'] in proof['authorized_fingerprints']
                and re.search(r'Authenticated to .* using "publickey"', checked.stderr), 'real SSH identity/key/marker differs')
        after = capture(g, t, state, token)
        require(after['vm'] == native['vm'] and after['vmi'] == native['vmi'] and after['pv'] == native['pv'], 'proof native identity changed during SSH')
        result.update(result='PASS', ssh_exit=checked.returncode, authentication='publickey',
                      strict_host_key_checking=True, existing_identity_only=True, password_authentication_attempted=False,
                      guest_proof=proof, loopback_tunnel=process_ack)
    except Exception as error:
        result['error_type'] = type(error).__name__
        result['error'] = str(error).replace(password, '[REDACTED]') if isinstance(error, RuntimeError) else 'private serial/SSH tool diagnostic retained'
        g.save(PRIVATE / 'cloudinit-ssh-serial-tail.txt', transcript.getvalue().replace(password, '[REDACTED]'))
    finally:
        child.close(force=True)
        if tunnel:
            ack = json.loads((PRIVATE / 'cloudinit-ssh-port-forward.json').read_text())
            proc = Path('/proc/' + str(tunnel.pid))
            if proc.exists() and tunnel.poll() is None:
                require(proc.joinpath('stat').read_text().split(') ', 1)[1].split()[19] == ack['start_ticks']
                        and proc.joinpath('cmdline').read_bytes().rstrip(b'\0').split(b'\0') == [x.encode() for x in ack['argv']], 'owned port-forward process guard failed')
                tunnel.terminate()
                tunnel.wait(timeout=8)
            result['owned_port_forward_stopped'] = tunnel.poll() is not None
        g.save(GUEST_RESULT, result)
        state['guest_ssh'] = result
        g.save(STATE, state)
        publish(g, state, 'GUEST_SSH_' + result['result'])
    return result


def delete(g, t, state, token, seconds):
    require(state.get('native', {}).get('pv_csi') and state.get('family'), 'freeze exact backend before DELETE')
    entry = state.get('delete')
    if not entry:
        current = g.api('GET', BASE + '/' + state['vm_id'], token=token)
        require(current['id'] == state['vm_id'] and not current['closing'], 'proof VM is already closing without original DELETE')
        key = str(uuid.uuid5(uuid.NAMESPACE_URL, 'ani://' + state['vm_id'] + '/cloudinit-ssh-delete'))
        entry = {'key': key, 'body': {'data': {'idempotencyKey': key, 'expectedVersion': current['version']}}, 'send_attempts': 0}
        state['delete'] = entry; g.save(STATE, state)
    if not entry.get('operation_id'):
        require(entry['send_attempts'] == 0, 'DELETE ACK unknown: query original intent; do not blindly resend')
        entry['send_attempts'] = 1; entry['outcome'] = 'ACK_UNKNOWN_UNTIL_PERSISTED'; g.save(STATE, state)
        accepted = g.api('DELETE', BASE + '/' + state['vm_id'], entry['body'], token)
        require(accepted['resourceId'] == state['vm_id'], 'DELETE ACK differs')
        entry.update(operation_id=accepted['operationId'], outcome='ACK_PERSISTED'); g.save(STATE, state)
    deadline = time.monotonic() + seconds
    while True:
        entry['operation'] = g.api('GET', BASE + '/' + state['vm_id'] + '/operations/' + entry['operation_id'], token=token)
        require(entry['operation']['vmId'] == state['vm_id'] and entry['operation']['id'] == entry['operation_id'], 'DELETE query differs')
        g.save(STATE, state)
        if entry['operation']['state'] in ('succeeded', 'failed') or time.monotonic() >= deadline:
            return publish(g, state, 'ORIGINAL_DELETE_OBSERVED')
        time.sleep(1)


def cleanup(g, t, state, token):
    require(state.get('delete', {}).get('operation_id'), 'no original DELETE ACK')
    entry = state['delete']
    entry['operation'] = g.api('GET', BASE + '/' + state['vm_id'] + '/operations/' + entry['operation_id'], token=token)
    product_current = g.api('GET', BASE + '/' + state['vm_id'], token=token)
    require(product_current['id'] == state['vm_id'], 'cleanup product VM identity changed')
    state['current'] = product_current
    remaining = []
    for original in state['native']['objects']:
        current = kubectl(t, original['kind'], original['name'], original.get('namespace'))
        require(not current or current['metadata']['uid'] == original['uid'], 'same-name replacement; absence not inferred')
        if current:
            remaining.append(t.ref(current))
    trash = ceph(t, ['rbd', 'trash', 'ls', '-p', 'ani-block-pool', '--format=json'])
    require(trash is not None, 'RBD trash read unavailable')
    family = []
    for original in state['family']:
        current = image_info(t, original['name'])
        require(current['result'] == 'ABSENT' or current.get('id') == original.get('id'), 'RBD family replaced')
        family.append({'original': original, 'current': current, 'matching_trash': [x for x in trash
                       if x.get('name') == original['name'] or original.get('id') and x.get('id') == original['id']]})
    rows = attachment(g, t, state)
    protected = protect(t)
    require(protected == state['protected_before'], 'protected source/NAD/NS/four VM identity changed')
    passed = entry['operation']['state'] == 'succeeded' and product_current['observedState'] == 'Deleted' and product_current['closing'] is True \
        and product_current.get('disks', []) == [] and not remaining and len(rows) == 1 and rows[0]['state'] == 'released' \
        and rows[0]['blocked'] is False and all(x['current']['result'] == 'ABSENT' and not x['matching_trash'] for x in family)
    state['cleanup'] = {'result': 'PASS' if passed else 'PENDING_NORMAL_GC', 'remaining_original_objects': remaining,
                        'rbd_family': family, 'attachment': rows, 'protected': protected,
                        'normal_gov_delete_only': True, 'manual_backend_delete': False, 'old_v22': 'UNCHANGED_NOT_MEASURED'}
    g.save(STATE, state)
    return publish(g, state, 'CLEANED' if passed else 'NORMAL_GC_PENDING')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'execute', 'observe', 'native', 'guest', 'proxy-prepare', 'proxy-create', 'proxy-observe', 'proxy-ssh', 'proxy-cleanup', 'delete', 'cleanup'])
    parser.add_argument('--approved-plan-sha')
    parser.add_argument('--execute-authorized', action='store_true')
    parser.add_argument('--wait-seconds', type=int, default=20, choices=range(0, 21))
    args = parser.parse_args()
    with LOCK.open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 73
        g, t = helpers()
        token, principal = t.login(g)
        if args.action == 'prepare':
            result = prepare(g, t, token, principal)
        else:
            state = frozen(g, t)
            if args.action in ('guest', 'delete', 'proxy-create', 'proxy-ssh', 'proxy-cleanup'):
                require(args.execute_authorized, 'explicit authorized task Guest/DELETE window required')
            if args.action == 'native':
                capture(g, t, state, token)
                if state.get('native', {}).get('pv_csi'):
                    freeze_backend(g, t, state)
                result = publish(g, state, 'NATIVE_CAPTURED')
            elif args.action == 'proxy-prepare':
                prepared = prepare_proxy(g, t, state, token)
                print(json.dumps({'phase': prepared['phase'], 'proposal_sha256': prepared['proposal_sha256'], 'receipt': str(PROXY_PLAN)}))
                return 0
            elif args.action == 'proxy-create':
                proxy_create(g, t, state, args.approved_plan_sha)
                result = publish(g, state, 'PROXY_CREATE_ACK_PERSISTED')
            elif args.action == 'proxy-observe':
                proxy_observe(g, t, state)
                result = publish(g, state, 'PROXY_OBSERVED')
            elif args.action == 'proxy-ssh':
                result = proxy_ssh(g, t, state)
            elif args.action == 'proxy-cleanup':
                result = proxy_cleanup(g, t, state)
            elif args.action == 'guest':
                guest(g, t, state, token)
                result = publish(g, state, 'GUEST_SSH_' + state['guest_ssh']['result'])
            elif args.action == 'delete':
                result = delete(g, t, state, token, args.wait_seconds)
            elif args.action == 'cleanup':
                result = cleanup(g, t, state, token)
            else:
                result = None
            if args.action == 'execute':
                require(sha(__file__) == CREATE_SOURCE_SHA, 'CREATE implementation changed; never resend this original admission')
                require(args.execute_authorized, 'Root must approve the concrete resource plan before VM POST')
                current_receipt = publish(g, state, 'FROZEN_PLAN_REVIEW')
                require(args.approved_plan_sha == current_receipt['approval_plan_sha256'], 'approved plan SHA does not match this driver and frozen intent')
                state['preflight'] = preflight(g, t, token)
                require(state['send_attempts'] == 0 and not state.get('vm_id'), 'create was already sent; query original ACK or review explicit original-key replay, never blindly POST')
                state['send_attempts'] = 1
                state['outcome'] = 'ACK_UNKNOWN_UNTIL_PERSISTED'
                g.save(STATE, state)
                accepted = g.api('POST', BASE, state['body'], token)
                state.update(vm_id=accepted['resourceId'], operation_id=accepted['operationId'], outcome='ACK_PERSISTED')
                g.save(STATE, state)
            if args.action in ('execute', 'observe'):
                result = observe(g, state, token, args.wait_seconds)
        print(json.dumps({'phase': result['phase'], 'approval_plan_sha256': result['approval_plan_sha256'],
                          'receipt': str(RECEIPT), 'vm_id': result['vm_id'],
                          'create_send_attempts': result['create_send_attempts']}))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as error:
        # API errors contain only status/reason. Never print a request, key blob,
        # password, Secret, response body or private diagnostics.
        print(json.dumps({'result': 'STOPPED', 'error_type': type(error).__name__,
                          'error': str(error) if isinstance(error, RuntimeError) else 'private task input/tool failure'}))
        sys.exit(1)
