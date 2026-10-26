#!/usr/bin/env python3
"""Task-only real Gov V23 import/clone deletion; no native mutation or SQL writes.

prepare performs normal authenticated directory/network reads and freezes one
12GB, startAfterCreate=false intent. execute needs explicit Root authorization
and that plan SHA. A missed CDI-unready window is reported honestly and the
same probe is cleaned through its original product DELETE. PV/RBD/trash physical
release is separate evidence and is never inferred from an operation ACK.
"""
import argparse
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import runpy
import subprocess
import time
import urllib.parse
import uuid

RUN = Path('/home/chabking/workspace/ani-resource-service-runs/vm-mvp-20261023-01a168ce')
PRIVATE = RUN / 'private/gov-candidate'
STATE = PRIVATE / 'import-delete-vm.json'
PLAN = PRIVATE / 'import-delete-plan.json'
RECEIPT = RUN / 'evidence/gov-import-delete-product.json'
NAME = 'vm-mvp-import-delete-01a168ce'
TENANT = 'b3c946b5-7eb3-4feb-9f82-79712062ca5c'
NS = 'tenant-' + TENANT
NS_UID = '09e58ecb-fe20-454f-bc7b-2da3c406f0ac'
CLUSTER = '5277649d-e28d-4a0a-ae76-8354365c63bc'
PRESET = '1baf221e-bdd3-49f9-b69f-597bb3695c47'
SOURCE_UID = '8b235154-fae1-47ed-8f96-06af02a18ce1'
SOURCE_SHA = '61f58093344ae5c84e22f1be07a904b0e2a15f7b6f18e45ef4549e52833c69c4'
VPC = 'vpc_e413becb1f4a46468f058e32f33a6305'
SUBNET = 'subnet_fbd064f6018040dab6a8d909b7afb87f'
BASE = '/api/v1/virtual-machines'
K = ['/home/chabking/ani-four-service-deploy-20261012/tools/kubectl',
     '--kubeconfig=/home/chabking/ani-four-service-deploy-20261012/private/kubeconfig', '--request-timeout=6s']
PREPARING_PHASES = {'Pending', 'PVCBound', 'WaitForFirstConsumer', 'PendingPopulation',
                    'ImportScheduled', 'ImportInProgress', 'CloneScheduled', 'CloneInProgress',
                    'SnapshotForSmartCloneInProgress', 'SmartClonePVCInProgress', 'CSICloneInProgress',
                    'ExpansionInProgress', 'TransferInProgress', 'PrepClaimInProgress', 'RebindInProgress',
                    'CloneFromSnapshotSourceInProgress'}


def require(value, reason):
    if not value: raise RuntimeError(reason)


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def native(kind, name=None, namespace=None, labels=None):
    args = K + ['get', kind]
    if name: args += [name, '--ignore-not-found=true']
    if namespace: args += ['-n', namespace]
    if labels: args += ['-l', labels]
    raw = subprocess.run(args + ['-o', 'json'], capture_output=True, check=True, timeout=10).stdout
    return json.loads(raw) if raw.strip() else None


def ref(value):
    return None if value is None else {'kind': value['kind'], **{key: value['metadata'].get(key, '') for key in ('namespace', 'name', 'uid')}}


def owned(value, kind, owner):
    return any(item.get('kind') == kind and item.get('uid') == owner for item in value['metadata'].get('ownerReferences', []))


def login(g):
    require((PRIVATE / 'credentials.json').is_file(), 'existing task login is required')
    credentials = g['credentials']()
    token = g['api']('POST', '/api/v1/auth/password/login', {'tenant_name': credentials['tenant_code'],
        'username': credentials['tenant_username'], 'password': credentials['tenant_password']})['access_token']
    me = g['api']('GET', '/api/v1/admin/me', token=token)
    require(int(me['tenantId']) == 1 and me['username'] == credentials['tenant_username'], 'wrong authenticated task tenant')
    return token, {'id': me['id'], 'tenant_id': me['tenantId'], 'username': me['username']}


def preflight(g, token):
    presets = g['api']('GET', '/api/v1/compute-presets?usage=VIRTUAL_MACHINE&page=1&pageSize=100', token=token)['items']
    selected = [item for item in presets if item['id'] == PRESET]
    require(len(selected) == 1 and selected[0]['selectable'] and selected[0]['enabled'] and selected[0]['compute_type'] == 'cpu'
            and int(selected[0]['cpu_millis']) == 1500 and int(selected[0]['memory_mib']) == 2048 and not selected[0].get('gpu'), 'fixed VM-usage CPU preset unavailable')
    media = g['api']('GET', '/api/v1/virtual-machine-media', token=token)['items']
    selected_media = [item for item in media if item['mediaKey'] == 'ubuntu-24.04']
    require(len(selected_media) == 1 and selected_media[0]['kind'] == 'DISK_IMAGE' and selected_media[0]['osFamily'] == 'linux'
            and selected_media[0]['initializationCapability'] == 'CLOUD_INIT_NOCLOUD' and selected_media[0]['initializationVerified'] is True, 'verified normal Ubuntu source unavailable')
    vpc = g['api']('GET', '/api/v1/networks/vpcs/' + VPC, token=token)
    subnet = g['api']('GET', '/api/v1/networks/subnets/' + SUBNET, token=token)
    require(vpc['id'] == VPC and subnet['id'] == SUBNET and subnet['vpc_id'] == VPC
            and all(item['state'] == 'available' and item['observation_stale'] is False for item in (vpc, subnet)), 'original task network unavailable')
    config = json.loads((RUN / 'private/resource-candidate/vm.json').read_text())
    sources = [item for item in config['Service']['Media'] if item['media_key'] == 'ubuntu-24.04']
    require(len(sources) == 1 and sources[0]['pvc_uid'] == SOURCE_UID and sources[0]['checksum'] == SOURCE_SHA, 'trusted source UID/hash changed')
    require(native('namespace', 'kube-system')['metadata']['uid'] == CLUSTER and native('namespace', NS)['metadata']['uid'] == NS_UID, 'cluster or tenant namespace changed')
    source = native('pvc', sources[0]['pvc_name'], sources[0]['namespace'])
    require(source and source['metadata']['uid'] == SOURCE_UID and source['status']['phase'] == 'Bound'
            and source['metadata']['annotations']['vm.ani.io/source-sha256'] == SOURCE_SHA and source['spec']['volumeMode'] == 'Block', 'protected source PVC changed')
    mappings = [item for item in config['Networks'] if item['TenantID'] == TENANT and item['VPCID'] == VPC and item['SubnetID'] == SUBNET]
    require(len(mappings) == 1, 'original managed network mapping missing')
    mapping = mappings[0]
    nad = native('network-attachment-definitions.k8s.cni.cncf.io', mapping['NADName'], NS)
    require(nad and nad['metadata']['uid'] == mapping['NADUID'] and digest(json.loads(nad['spec']['config'])) == mapping['NADConfigDigest'], 'original NAD changed')
    return {'compute': selected[0], 'public_media': selected_media[0], 'source': sources[0], 'source_pvc': ref(source), 'network': mapping}


def publish(g, state, phase):
    state['phase'] = phase
    g['save'](STATE, state)
    result = {'at': now(), 'phase': phase, 'name': NAME, 'plan_sha256': state['plan_sha256'],
        'private_state_reference': str(STATE), 'create': state['create'], 'delete': state['delete'],
        'vm_id': state.get('vm_id'), 'create_operation_id': state.get('create_operation_id'),
        'native_before_delete': state.get('native_before_delete'), 'last_native': state.get('last_native'),
        'cdi_unready_observed': state.get('cdi_unready_observed', False), 'unready_window': state.get('unready_window'),
        'cleanup': state.get('cleanup'), 'implicit_start_observed': state.get('implicit_start_observed', False),
        'delete_association_verified': state.get('delete_association_verified', False),
        'bottom_storage': {'rbd': 'NOT_MEASURED', 'trash': 'NOT_MEASURED'}, 'vm_posts_other_than_this_probe': 0}
    g['save'](RECEIPT, result)
    return result


def prepare(g, token, principal):
    if STATE.exists():
        state = json.loads(STATE.read_text())
        require(digest(state['plan']) == state['plan_sha256'] and PLAN.read_text() == json.dumps(state['plan'], indent=2) + '\n', 'original plan changed')
        return state
    require(not PLAN.exists(), 'orphan original plan exists; inspect rather than replace its keys')
    selected = preflight(g, token)
    key = str(uuid.uuid4())
    body = {'data': {'name': NAME, 'presetId': PRESET, 'sourceKind': 'DISK_IMAGE', 'mediaKey': 'ubuntu-24.04',
        'systemDiskSizeGb': '12', 'idempotencyKey': key, 'startAfterCreate': False, 'network': {'vpcId': VPC, 'subnetId': SUBNET}}}
    require('guestInitialization' not in body['data'], 'this probe must have no guest initialization')
    plan = {'name': NAME, 'resource_tenant_id': TENANT, 'namespace_uid': NS_UID, 'create_body': body,
        'delete_key': str(uuid.uuid4()), 'selection': selected, 'principal': principal,
        'window_policy': 'Observe actual owned VM+DV+PVC and CDI non-ready phase before DELETE; if already Succeeded, report missed and perform only normal cleanup.',
        'native_mutations': 'NONE', 'business_sql_writes': False, 'implicit_start_allowed': False}
    state = {'plan': plan, 'plan_sha256': digest(plan), 'prepared_at': now(),
        'create': {'body': body, 'send_attempts': 0, 'outcome': 'not_sent'},
        'delete': {'idempotency_key': plan['delete_key'], 'send_attempts': 0, 'outcome': 'not_sent'}}
    g['save'](PLAN, plan)
    publish(g, state, 'PREPARED_NO_VM_POST')
    return state


def capture(g, state):
    vmid = state['vm_id']
    vm = native('vm', 'vm-' + vmid, NS)
    if not vm: return {'at': now(), 'vm': None, 'vmi': ref(native('vmi', 'vm-' + vmid, NS))}
    require(vm['metadata']['labels'].get('ani.io/tenant-id') == TENANT and vm['metadata']['labels'].get('vm.ani.io/vm-id') == vmid, 'native VM ownership differs')
    vmuid = vm['metadata']['uid']
    vmi = native('vmi', vm['metadata']['name'], NS)
    if vmi: require(owned(vmi, 'VirtualMachine', vmuid), 'unexpected VMI owner')
    state['implicit_start_observed'] = state.get('implicit_start_observed', False) or vmi is not None or bool(vm.get('status', {}).get('stateChangeRequests'))
    require(vm['spec']['runStrategy'] == 'Manual' and not any('cloudInitNoCloud' in item for item in vm['spec']['template']['spec']['volumes']), 'unexpected run strategy or guest initialization')
    dvmatches = native('dv', namespace=NS, labels='vm.ani.io/vm-id=' + vmid)['items']
    require(len(dvmatches) <= 1, 'this single-root probe acquired unexpected extra DVs')
    result = {'at': now(), 'vm': ref(vm), 'vmi': ref(vmi), 'run_strategy': vm['spec']['runStrategy'],
              'attachment_id': vm['metadata']['labels'].get('network.ani.io/attachment-id'), 'dv': None, 'pvc': None, 'pv': None}
    if dvmatches:
        dv = dvmatches[0]
        source = dv['spec'].get('source', {}).get('pvc', {})
        require(owned(dv, 'VirtualMachine', vmuid) and source.get('namespace') == state['plan']['selection']['source']['namespace']
                and source.get('name') == state['plan']['selection']['source']['pvc_name'], 'root clone source/owner differs')
        result.update(dv=ref(dv), dv_phase=dv.get('status', {}).get('phase', ''), dv_status=dv.get('status', {}), dv_spec=dv['spec'])
        pvc = native('pvc', dv['metadata']['name'], NS)
        if pvc:
            require(owned(pvc, 'DataVolume', dv['metadata']['uid']) or owned(pvc, 'VirtualMachine', vmuid), 'root PVC owner differs')
            require(pvc['spec']['volumeMode'] == 'Block' and pvc['spec']['accessModes'] == ['ReadWriteOnce'], 'root is not Block/RWO')
            result.update(pvc=ref(pvc), pvc_phase=pvc.get('status', {}).get('phase', ''), pvc_spec=pvc['spec'])
            if pvc['spec'].get('volumeName'):
                pv = native('pv', pvc['spec']['volumeName'])
                require(pv and pv['spec']['claimRef']['uid'] == pvc['metadata']['uid'], 'root PV claim UID differs')
                result.update(pv=ref(pv), pv_csi=pv['spec'].get('csi'), pv_reclaim_policy=pv['spec']['persistentVolumeReclaimPolicy'])
    return result


def freeze_before_delete(state, latest):
    previous = state.get('native_before_delete', {})
    for field in ('vm', 'dv', 'pvc', 'pv'):
        old = previous.get(field)
        current = latest.get(field)
        if old and current:
            require(old == current, 'frozen native identity changed before DELETE')
        elif old:
            latest[field] = old
    state['native_before_delete'] = latest
    phase = latest.get('dv_phase')
    if phase == 'Succeeded':
        state['unready_window'] = 'MISSED_ALREADY_READY_BEFORE_DELETE'
    elif phase in PREPARING_PHASES and latest.get('vm') and latest.get('dv') and latest.get('pvc'):
        state.update(cdi_unready_observed=True, unready_window='CAPTURED_CDI_UNREADY_BEFORE_DELETE')


def verify_delete_association(g, state):
    operation = state['delete']['operation_id']
    require(str(uuid.UUID(operation)) == operation, 'invalid DELETE operation identity')
    raw = g['psql'](g['OWNER'], "SELECT json_build_object('create_operation_id',create_operation_id,'resource_id',resource_id,'resource_tenant_id',resource_tenant_id,'action',action,'canonical_request',canonical_request) FROM sys_quota_operations WHERE tenant_id=1 AND operation_id='" + operation + "';")
    row = json.loads(raw)
    require(row['create_operation_id'] == state['create_operation_id'] and row['resource_id'] == state['vm_id']
            and row['resource_tenant_id'] == TENANT and row['action'] == '/vm.v1.VMService/AcceptDelete'
            and json.loads(row['canonical_request'])['delete_expected_version'] == state['delete']['expected_version'], 'original CREATE association or DELETE version changed')
    state['delete_association_verified'] = True


def accept(g, state, token, delete=False, replay=False):
    entry = state['delete' if delete else 'create']
    if entry.get('operation_id'): return
    if entry['outcome'] != 'not_sent':
        require(entry['outcome'] == 'ack_unknown' and replay and state.get('ack_unknown_observed_at'), 'unknown original ACK needs observation and explicit exact-key replay')
    if delete and 'body' not in entry:
        current = g['api']('GET', BASE + '/' + state['vm_id'], token=token)
        require(int(current['version']) > 0, 'owner version is not yet known; wait rather than invent expected_version')
        entry.update(expected_version=int(current['version']), body={'data': {'idempotencyKey': entry['idempotency_key'], 'expectedVersion': str(current['version'])}})
    if delete:
        latest = capture(g, state)
        freeze_before_delete(state, latest)
    entry.update(send_attempts=entry['send_attempts'] + 1, outcome='ack_unknown', last_send_at=now())
    g['save'](STATE, state)
    try:
        ack = g['api']('DELETE' if delete else 'POST', BASE + '/' + state['vm_id'] if delete else BASE, entry['body'], token)
    except Exception as error:
        entry['error'] = {'type': type(error).__name__}
        if isinstance(error, g['ProductAPIError']):
            entry['error'].update(status=error.status, reason=error.reason)
            if error.status in (400, 401, 403, 404, 409, 412, 422): entry['outcome'] = 'rejected'
        g['save'](STATE, state)
        raise RuntimeError('original product admission has no usable ACK; original key/body retained') from None
    require(str(uuid.UUID(ack['resourceId'])) == ack['resourceId'] and str(uuid.UUID(ack['operationId'])) == ack['operationId'], 'invalid admission ACK identity')
    if delete: require(ack['resourceId'] == state['vm_id'], 'DELETE returned a different resource')
    else: state.update(vm_id=ack['resourceId'], create_operation_id=ack['operationId'])
    entry.update(operation_id=ack['operationId'], outcome='acknowledged', ack=ack)
    g['save'](STATE, state)
    if delete:
        verify_delete_association(g, state)
        g['save'](STATE, state)


def attachment(g, state):
    dsn = urllib.parse.urlsplit((RUN / 'private/resource-candidate/owner.dsn').read_text().strip())
    require(dsn.path == '/vm_resource_candidate_01a168ce', 'wrong task Resource database')
    vmid = state['vm_id']; require(str(uuid.UUID(vmid)) == vmid, 'invalid accepted VM ID')
    rows = json.loads(g['psql'](urllib.parse.unquote(dsn.username), "SELECT COALESCE(json_agg(json_build_object('id',attachment_id,'state',state,'blocked',protocol_blocked,'released_at',released_at)),'[]'::json) FROM network_attachments WHERE tenant_id='" + TENANT + "' AND instance_id='" + vmid + "';", database=dsn.path[1:]))
    require(len(rows) <= 1, 'unexpected attachment identities for this probe')
    frozen = state.get('native_before_delete', {}).get('attachment_id')
    require(not rows or not frozen or rows[0]['id'] == frozen, 'original Attachment identity changed')
    return rows


def observe_cleanup(g, state, token, seconds):
    verify_delete_association(g, state)
    deadline = time.monotonic() + seconds
    frozen = state.get('native_before_delete', {})
    while True:
        operation = g['api']('GET', BASE + '/' + state['vm_id'] + '/operations/' + state['delete']['operation_id'], token=token)
        remaining = []
        for field, kind in [('vm', 'vm'), ('dv', 'dv'), ('pvc', 'pvc'), ('pv', 'pv')]:
            original = frozen.get(field)
            if not original: continue
            value = native(kind, original['name'], original.get('namespace') or None)
            if value:
                require(value['metadata']['uid'] == original['uid'], 'same-name native replacement encountered; do not touch it')
                remaining.append(ref(value))
        vmi = native('vmi', 'vm-' + state['vm_id'], NS)
        state['implicit_start_observed'] = state.get('implicit_start_observed', False) or vmi is not None
        related = attachment(g, state)
        cleanup = {'delete_operation': operation, 'remaining_original_native_objects': remaining,
            'attachment': related, 'no_implicit_start_observed': not state.get('implicit_start_observed', False),
            'pv_absence': 'PASS' if frozen.get('pv') and not any(item['kind'] == 'PersistentVolume' for item in remaining) else 'NOT_MEASURED_OR_NOT_ABSENT',
            'rbd': 'NOT_MEASURED', 'trash': 'NOT_MEASURED'}
        state['cleanup'] = cleanup
        if operation['state'] == 'succeeded' and not remaining and vmi is None and len(related) == 1 and related[0]['state'] == 'released' and related[0]['blocked'] is False:
            phase = 'PRODUCT_NATIVE_GC_DONE_BOTTOM_STORAGE_PENDING' if state.get('unready_window') == 'CAPTURED_CDI_UNREADY_BEFORE_DELETE' else 'UNREADY_WINDOW_NOT_CAPTURED_PRODUCT_CLEANED'
            publish(g, state, phase); return
        if time.monotonic() >= deadline:
            publish(g, state, 'SAME_DELETE_PENDING_NO_NEW_POST'); return
        g['save'](STATE, state)
        time.sleep(.5)


def run(g, args):
    token, principal = login(g)
    if args.action == 'prepare':
        state = prepare(g, token, principal)
        publish(g, state, 'PREPARED_NO_VM_POST')
    else:
        state = json.loads(STATE.read_text())
        require(digest(state['plan']) == state['plan_sha256'] and json.loads(PLAN.read_text()) == state['plan'], 'frozen plan changed')
        if args.action == 'execute':
            require(args.execute_authorized and args.plan_sha == state['plan_sha256'], 'explicit approved plan SHA required')
            current = preflight(g, token)
            require(current['source'] == state['plan']['selection']['source'] and current['network'] == state['plan']['selection']['network'], 'frozen source/network drifted')
            accept(g, state, token, replay=args.replay_original)
        elif not state.get('vm_id'):
            visible = g['api']('GET', BASE + '?page=1&pageSize=100', token=token)['items']
            state['ack_unknown_observed_at'] = now()
            state['same_name_inventory_only'] = [{'id': item['id'], 'name': item['name']} for item in visible if item['name'] == NAME]
            publish(g, state, 'UNKNOWN_ACK_OBSERVED_NO_ADOPTION'); return state
        if state['delete']['outcome'] == 'ack_unknown' and not state['delete'].get('operation_id'):
            if args.action == 'observe':
                state['last_native'] = capture(g, state)
                state['original_create_operation'] = g['api']('GET', BASE + '/' + state['vm_id'] + '/operations/' + state['create_operation_id'], token=token)
                state['ack_unknown_observed_at'] = now()
                publish(g, state, 'UNKNOWN_DELETE_ACK_OBSERVED_NO_NEW_POST'); return state
            accept(g, state, token, delete=True, replay=args.replay_original)
        if state['delete'].get('operation_id'):
            observe_cleanup(g, state, token, args.wait_seconds)
        else:
            end = time.monotonic() + args.wait_seconds
            while True:
                observed = capture(g, state)
                state['last_native'] = observed
                phase = observed.get('dv_phase', '')
                if observed.get('vm') and observed.get('dv') and observed.get('pvc') and phase:
                    require(phase == 'Succeeded' or phase in PREPARING_PHASES, 'CDI phase is not a verified preparing/importing/cloning window; inspect before cleanup')
                    freeze_before_delete(state, observed)
                    g['save'](STATE, state)
                    break
                if time.monotonic() >= end:
                    publish(g, state, 'NATIVE_OWNER_CHAIN_NOT_YET_OBSERVED_NO_DELETE'); return state
                time.sleep(.25)
            # Only execute can admit DELETE; observe is always read-only.
            if args.action == 'execute':
                accept(g, state, token, delete=True, replay=args.replay_original)
                observe_cleanup(g, state, token, args.wait_seconds)
            else: publish(g, state, 'NATIVE_OBSERVED_NO_DELETE')
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'execute', 'observe'))
    parser.add_argument('--execute-authorized', action='store_true')
    parser.add_argument('--plan-sha')
    parser.add_argument('--replay-original', action='store_true')
    parser.add_argument('--wait-seconds', type=int, default=30, choices=range(0, 46))
    args = parser.parse_args()
    require(os.environ.get('SSH_CONNECTION', '').split()[2:3] == ['172.16.101.31'], 'only ssh fedora may drive this task')
    require(args.action == 'execute' or not args.execute_authorized and not args.replay_original, 'mutation flags only apply to explicitly authorized execute')
    g = runpy.run_path(str(RUN / 'gov-candidate.py'))
    with Path('/home/chabking/workspace/ani-network-service-runs/net05a-heavy.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = run(g, args)
    print(json.dumps({'phase': state.get('phase'), 'plan_sha256': state['plan_sha256'], 'private_plan_reference': str(PLAN),
        'receipt': str(RECEIPT), 'vm_id': state.get('vm_id'), 'create_send_attempts': state['create']['send_attempts'],
        'delete_send_attempts': state['delete']['send_attempts'], 'cdi_unready_observed': state.get('cdi_unready_observed', False)}), flush=True)


if __name__ == '__main__':
    main()
