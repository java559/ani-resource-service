"""Prepare-only delivery: exact readonly native system-disk start evidence.

Run only on Fedora after Root pins the actual original Start observation and
the completed private four-control journal. No API authentication or Guest work.
"""
from pathlib import Path
import argparse, datetime, fcntl, hashlib, json, os, stat, subprocess, time, uuid

R = Path('/home/chabking/workspace/ani-resource-service-runs/vm-mvp-20261023-01a168ce')
D = R / 'iso-marker-02-controls'
TASK = 'vm-mvp-fedora44-iso-marker-02-01a168ce'
VMID = '96575d3e-185d-4939-a01f-b82562d7af08'
VMUID = '68f92d12-8d14-4d73-ba97-996d328c23f9'
OLD_VMI_UID = '27550850-b1fc-4fe9-99fb-6e08ef7d5a24'
CLUSTER = '5277649d-e28d-4a0a-ae76-8354365c63bc'
TENANT = 'b3c946b5-7eb3-4feb-9f82-79712062ca5c'
NS = 'tenant-' + TENANT
NSUID = '09e58ecb-fe20-454f-bc7b-2da3c406f0ac'
NATIVE = R / 'iso-marker-02/native-01.json'
NATIVE_SHA = 'bd28c5d1fc50f1be0225569f288e042c87e35d958dcabb03627a31356c576c1a'
FROZEN = R / 'iso-marker-02/frozen-request.json'
FROZEN_SHA = '108be646dda9d053f731413a212280bd73667ad0efc4a856fee688066dd348be'
JOURNAL = D / 'private/journal.json'
LOCK = Path('/home/chabking/workspace/ani-network-service-runs/net05a-heavy.lock')
K = ['/home/chabking/ani-four-service-deploy-20261012/tools/kubectl',
     '--kubeconfig=/home/chabking/ani-four-service-deploy-20261012/private/kubeconfig', '--request-timeout=8s']
NODE_UIDS = {'ani-01': '201e41ac-c50d-4c77-b118-72d1ed93e111', 'ani-02': '73564fa7-98ad-45d4-9576-be59f6d5d245'}
ORDER = ['stop', 'eject', 'boot', 'start']
BASE = '/api/v1/virtual-machines/' + VMID


def require(ok, message):
    if not ok:raise RuntimeError(message)


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value, exclusive=False):
    flags = os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | (os.O_EXCL if exclusive else os.O_TRUNC)
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream, indent=2);stream.write('\n');stream.flush();os.fsync(stream.fileno())


def regular(path, private=False):
    info = path.lstat()
    require(path.resolve() == path and stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid(), 'own exact regular reference required')
    if private:require(stat.S_IMODE(info.st_mode) == 0o600, 'private journal must remain0600')


def owned(obj, kind, uid):
    return any(row.get('kind') == kind and row.get('uid') == uid and row.get('controller') is True
               for row in obj['metadata'].get('ownerReferences', []))


def identity(obj):
    m = obj['metadata']
    return {key: m[key] for key in ['name', 'namespace', 'uid'] if key in m}


def ready(obj):return any(row.get('type') == 'Ready' and row.get('status') == 'True' for row in obj.get('status', {}).get('conditions', []))


def main(a):
    require(a.execute_readonly and os.uname().nodename == 'fedora' and Path(__file__).resolve() == D / 'after-start-native.py', 'explicit fixed Fedora readonly execution required')
    require(sha(__file__) == a.runner_sha256, 'runner changed')
    require(a.start_observe.parent == D and a.start_observe.name.endswith('.json') and a.start_observe != D / 'after-start-native.json', 'actual controls Start observation path required')
    require(a.controls_journal == JOURNAL, 'only original controls private journal is allowed')
    for path in [NATIVE, FROZEN, a.start_observe, a.controls_journal]:regular(path, path == JOURNAL)
    require(sha(NATIVE) == NATIVE_SHA and sha(FROZEN) == FROZEN_SHA and sha(a.start_observe) == a.start_observe_sha256 and sha(JOURNAL) == a.controls_journal_sha256, 'fixed original and Root-pinned actual references changed')
    original = json.loads(NATIVE.read_text());plan = json.loads(FROZEN.read_text())
    observation = json.loads(a.start_observe.read_text());journal = json.loads(JOURNAL.read_text())
    require(plan['task_name'] == original['task_name'] == journal['task_name'] == observation['task_name'] == TASK, 'task identity differs')
    require(original['vm_id'] == journal['vm_id'] == observation['vm_id'] == VMID and original['native']['vm']['uid'] == VMUID and original['native']['vmi']['uid'] == OLD_VMI_UID, 'original VM/native identity differs')
    require(plan['cluster_uid'] == CLUSTER and plan['resource_tenant_id'] == TENANT and plan['namespace'] == {'name': NS, 'uid': NSUID}, 'frozen cluster/tenant/namespace differs')
    require(journal['native_receipt_sha256'] == NATIVE_SHA and set(journal['commands']) == set(ORDER), 'all four original control intents required')
    operations = {};prior_version = 1
    routes = {'stop': ('POST', BASE + '/stop'), 'eject': ('DELETE', BASE + '/media/INSTALL'), 'boot': ('PUT', BASE + '/boot'), 'start': ('POST', BASE + '/start')}
    for intent in ORDER:
        command = journal['commands'][intent];operation = command.get('operation', {})
        require(command['intent'] == intent and (command['method'], command['route']) == routes[intent] and command['send_attempts'] == 1 and command['outcome'] == 'ACK_PERSISTED', 'each original control must have exactly one persisted send')
        require(int(command['before_version']) == prior_version and str(command['body']['data']['expectedVersion']) == str(prior_version), 'four original versions must be consecutive from createv1')
        require(str(uuid.UUID(command['operation_id'])) == command['operation_id'] and operation.get('id') == command['operation_id'] and operation.get('vmId') == VMID and operation.get('state') == 'succeeded' and int(operation['intentVersion']) == prior_version + 1, 'original operation must have succeeded at its exact version')
        if intent == 'eject':require(command['body']['data']['slot'] == 'INSTALL', 'original Eject must target INSTALL')
        if intent == 'boot':require(command['body']['data']['bootDevice'] == 'SYSTEM_DISK', 'original Boot must select SYSTEM_DISK')
        prior_version += 1
        operations[intent] = {key: operation.get(key) for key in ['id', 'vmId', 'action', 'state', 'intentVersion']}
    require(len({row['id'] for row in operations.values()}) == 4, 'four distinct original operations required')
    current = observation.get('current', {});start = operations['start']
    require(observation.get('result') == 'PASS_ORIGINAL_CONTROL_OBSERVATION' and observation.get('phase') == 'observe' and observation.get('intent') == 'start' and observation.get('session_revoked') is True and observation.get('heavy_lock_released') is True, 'actual completed normal Start observation/logout required')
    require(observation.get('operation') == journal['commands']['start']['operation'] or all(observation.get('operation', {}).get(key) == start[key] for key in start), 'Start observation must report the original succeeded operation')
    require(current.get('id') == VMID and current.get('name') == TASK and int(current.get('version', 0)) == prior_version == 5 and current.get('observedState') == 'Running' and current.get('closing') is False and current.get('lastOperationId') == start['id'], 'actual current Runningv5 after original Start required; oldv1 rejected')
    volumes = original['native']['volumes'];root = next(row for row in volumes if row['role'] == 'root');installer = next(row for row in volumes if row['role'] == 'installer')
    require(len(volumes) == 2 and {row['role'] for row in volumes} == {'root', 'installer'}, 'exact original root and retained installer only')
    root_disk = next(row for row in original['native']['domain_disks'] if row.get('serial'))
    out = D / 'after-start-native.json';private = D / 'private/after-start-native'
    require(not out.exists() and not private.exists(), 'preserve original capture; do not rerun')
    os.umask(0o077)
    with LOCK.open('r') as lock:
        try:fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:print(json.dumps({'result': 'LOCK_BUSY', 'cluster_reads': 0}));return 73
        private.mkdir(mode=0o700, parents=True)
        record = {'task_name': TASK, 'vm_id': VMID, 'stage': 'exact-native-capture', 'result': 'STARTED',
                  'started_at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'runner_sha256': a.runner_sha256,
                  'original_native_sha256': NATIVE_SHA, 'frozen_request_sha256': FROZEN_SHA,
                  'start_observe': {'path': str(a.start_observe), 'sha256': a.start_observe_sha256},
                  'controls_journal_reference': {'path': str(JOURNAL), 'sha256': a.controls_journal_sha256},
                  'operations': operations, 'current': current, 'calls': [], 'cluster_writes': 0,
                  'api_auth_calls': 0, 'API_calls': 0, 'PG_calls': 0, 'Guest_actions': 0,
                  'Ceph_calls': 0, 'network_probes': 0, 'credential_values_or_hashes_public': False}
        deadline = time.monotonic() + 180
        def get(label, kind, name=None, namespace=None, selector=None):
            require(time.monotonic() < deadline and not any(row['label'] == label for row in record['calls']), 'bounded unique exact read only')
            require((isinstance(name, str) and selector is None) or (kind == 'pods' and name is None and selector and selector.startswith('kubevirt.io/created-by=')), 'no broad scan')
            args = K + ['get', kind] + ([name] if name else ['-l', selector]) + (['-n', namespace] if namespace else []) + ['-o', 'json']
            call = {'label': label, 'kind': kind, 'name': name, 'namespace': namespace, 'selector': selector};record['calls'].append(call);save(out, record)
            reply = subprocess.run(args, capture_output=True, timeout=min(15, max(0.1, deadline - time.monotonic())))
            for suffix, raw in [('stdout', reply.stdout), ('stderr', reply.stderr)]:
                fd = os.open(private / (label + '.' + suffix), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, 'wb') as stream:stream.write(raw)
            call['exit'] = reply.returncode;save(out, record);require(reply.returncode == 0, 'bounded exact ' + label + ' read failed')
            return json.loads(reply.stdout)
        try:
            save(out, record, exclusive=True)
            require(get('cluster', 'namespace', 'kube-system')['metadata']['uid'] == CLUSTER and get('tenant-namespace', 'namespace', NS)['metadata']['uid'] == NSUID, 'original cluster/namespace changed')
            vm = get('vm', 'virtualmachine', original['native']['vm']['name'], NS);vmi = get('vmi', 'virtualmachineinstance', original['native']['vm']['name'], NS)
            require(vm['metadata']['uid'] == VMUID and not vm['metadata'].get('deletionTimestamp') and vm['metadata']['labels'].get('vm.ani.io/vm-id') == VMID and vm['metadata']['labels'].get('ani.io/tenant-id') == TENANT, 'exact original native VM/business ownership changed')
            template = vm['spec']['template']['spec'];domain = template['domain'];disks = domain['devices']['disks'];vm_volumes = template['volumes']
            require(domain['firmware']['uuid'] == VMID and len(disks) == 1 and disks[0]['name'] == root['volume'] and disks[0].get('serial') == root_disk['serial'] and disks[0].get('disk') == root_disk['disk'] and disks[0].get('bootOrder') == 1, 'only original root disk at unique boot1 required')
            require(len(vm_volumes) == 1 and vm_volumes[0]['name'] == root['volume'] and vm_volumes[0].get('dataVolume', {}).get('name') == root['dv']['name'], 'INSTALL device/volume must be absent; original root volume required')
            require(vmi['metadata']['uid'] != OLD_VMI_UID and owned(vmi, 'VirtualMachine', VMUID) and not vmi['metadata'].get('deletionTimestamp') and vmi['status']['phase'] == 'Running' and ready(vmi), 'new Running Ready VMI owned by original VM required; original2755 rejected')
            node_name = vmi['status']['nodeName'];require(node_name in NODE_UIDS, 'only ani01/ani02 authorized')
            node = get('node', 'node', node_name);require(node['metadata']['uid'] == NODE_UIDS[node_name] and ready(node), 'authorized current node UID/Ready differs')
            vmi_disks = vmi['spec']['domain']['devices']['disks'];vmi_volumes = vmi['spec']['volumes']
            require(vmi['spec']['domain']['firmware']['uuid'] == VMID and len(vmi_disks) == 1 and all(vmi_disks[0].get(key) == disks[0].get(key) for key in ['name', 'serial', 'disk', 'bootOrder']), 'current VMI firmware/root-only disk identity must match original VM template')
            require(len(vmi_volumes) == 1 and vmi_volumes[0]['name'] == root['volume'] and (vmi_volumes[0].get('dataVolume', {}).get('name') == root['dv']['name'] or vmi_volumes[0].get('persistentVolumeClaim', {}).get('claimName') == root['pvc']['name']), 'current VMI must use only the exact original root DV/PVC')
            statuses = vmi['status'].get('volumeStatus', []);require(len(statuses) == 1 and statuses[0]['name'] == root['volume'] and bool(statuses[0].get('target')) and statuses[0].get('phase') not in ['Pending', 'Failed'], 'original root volumeStatus must have its real target and no failing/pending state')
            pods = get('owned-launcher', 'pods', namespace=NS, selector='kubevirt.io/created-by=' + vmi['metadata']['uid'])['items']
            require(len(pods) == 1, 'exactly one current VMI launcher required')
            launcher = pods[0]
            require(owned(launcher, 'VirtualMachineInstance', vmi['metadata']['uid']) and launcher['metadata']['namespace'] == NS and not launcher['metadata'].get('deletionTimestamp') and launcher['status']['phase'] == 'Running' and launcher['spec']['nodeName'] == node_name and any(row['name'] == 'compute' and row.get('ready') is True and 'running' in row.get('state', {}) for row in launcher['status'].get('containerStatuses', [])), 'unique owned current launcher must be Running/computeReady on VMI node')
            storage = []
            for row in volumes:
                role = row['role'];dv = get(role + '-dv', 'datavolume', row['dv']['name'], NS);pvc = get(role + '-pvc', 'pvc', row['pvc']['name'], NS);pv = get(role + '-pv', 'pv', row['pv']['name'])
                require(dv['metadata']['uid'] == row['dv']['uid'] and owned(dv, 'VirtualMachine', VMUID) and dv['status']['phase'] == 'Succeeded' and pvc['metadata']['uid'] == row['pvc']['uid'] and owned(pvc, 'DataVolume', row['dv']['uid']) and pvc['status']['phase'] == 'Bound' and pvc['spec']['volumeName'] == row['pv']['name'], 'original ' + role + ' DV/PVC identity/ownership/readiness differs')
                require(pv['metadata']['uid'] == row['pv']['uid'] and pv['status']['phase'] == 'Bound' and pv['spec']['claimRef']['uid'] == row['pvc']['uid'] and pv['spec']['claimRef']['name'] == row['pvc']['name'] and pv['spec']['claimRef']['namespace'] == NS and all(pv['spec']['csi'].get(key) == value for key, value in row['csi'].items()), 'original ' + role + ' PV/CSI/claim identity differs')
                require(pvc['spec'].get('volumeMode', 'Filesystem') == ('Block' if role == 'root' else 'Filesystem') and all(not obj['metadata'].get('deletionTimestamp') for obj in [dv, pvc, pv]), 'original rootBlock/installerFilesystem chain must remain retained')
                storage.append({**row, 'namespace': NS, 'volume_mode': pvc['spec'].get('volumeMode', 'Filesystem'), 'dv_owner_vm_uid': VMUID, 'pvc_owner_dv_uid': row['dv']['uid'], 'pv_claim_pvc_uid': row['pvc']['uid']})
            vm_after = get('vm-final', 'virtualmachine', vm['metadata']['name'], NS);vmi_after = get('vmi-final', 'virtualmachineinstance', vmi['metadata']['name'], NS);pod_after = get('launcher-final', 'pod', launcher['metadata']['name'], NS)
            require(vm_after['metadata']['uid'] == VMUID and vm_after['spec'] == vm['spec'] and vmi_after['metadata']['uid'] == vmi['metadata']['uid'] and vmi_after['spec'] == vmi['spec'] and owned(vmi_after, 'VirtualMachine', VMUID) and vmi_after['status']['phase'] == 'Running' and ready(vmi_after) and vmi_after['status']['nodeName'] == node_name and pod_after['metadata']['uid'] == launcher['metadata']['uid'] and owned(pod_after, 'VirtualMachineInstance', vmi['metadata']['uid']) and pod_after['status']['phase'] == 'Running' and pod_after['spec']['nodeName'] == node_name and any(row['name'] == 'compute' and row.get('ready') is True and 'running' in row.get('state', {}) for row in pod_after['status'].get('containerStatuses', [])) and not any(obj['metadata'].get('deletionTimestamp') for obj in [vm_after, vmi_after, pod_after]), 'capture identities/spec changed during bounded read window')
            record.update(result='PASS_NATIVE_SYSTEM_DISK_STARTED_ONLY', stage='complete', cluster_uid=CLUSTER, namespace={'name': NS, 'uid': NSUID},
                          native={'vm': identity(vm), 'vmi': {**identity(vmi), 'phase': 'Running', 'ready': True, 'node': node_name, 'owner_vm_uid': VMUID},
                                  'launcher': {**identity(launcher), 'phase': 'Running', 'compute_ready': True, 'node': node_name, 'owner_vmi_uid': vmi['metadata']['uid']},
                                  'node': {'name': node_name, 'uid': node['metadata']['uid']}, 'storage': storage},
                          vm_spec=vm['spec'], vmi_spec=vmi['spec'], root_volume_status=statuses,
                          native_system_disk_boot=True, install_device_and_volume_absent=True, retained_installer_original_chain=True,
                          Guest_login_verified=False, Guest_marker_verified=False,
                          conclusion='Native system-disk start only; no Guest login or marker claim')
        except Exception as error:
            record.update(result='STOPPED_READONLY_CAPTURE', error_class=type(error).__name__, reason=str(error) if isinstance(error, RuntimeError) else 'bounded native read or required native field unavailable; private exact evidence retained')
        finally:
            record.update(finished_at=datetime.datetime.now(datetime.timezone.utc).isoformat(), heavy_lock_released=True)
            save(out, record);fcntl.flock(lock, fcntl.LOCK_UN)
    print(json.dumps({'result': record['result'], 'receipt': str(out), 'receipt_sha256': sha(out), 'heavy_lock_released': True}), flush=True)
    return 0 if record['result'].startswith('PASS_') else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute-readonly', action='store_true', required=True)
    parser.add_argument('--runner-sha256', required=True)
    parser.add_argument('--start-observe', type=Path, required=True)
    parser.add_argument('--start-observe-sha256', required=True)
    parser.add_argument('--controls-journal', type=Path, required=True)
    parser.add_argument('--controls-journal-sha256', required=True)
    raise SystemExit(main(parser.parse_args()))
