#!/usr/bin/env python3
"""Fixed, isolated V05 probe. prepare is offline; mutations need the reviewed SHA.

CREATE(start=false) -> one normal Start -> unique ready VMI/storage freeze ->
existing 55-second fence proof with exact finally ACL restore -> normal GC.
No guest account, guest login, new media, preset, permission, or native mutation.
"""
import argparse
import copy
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import runpy
import time
import uuid

RUN = Path('/home/chabking/workspace/ani-resource-service-runs/vm-mvp-20261023-01a168ce')
PRIVATE = RUN / 'private/gov-candidate'
STATE = PRIVATE / 'fence-probe-vm.json'
PLAN = PRIVATE / 'fence-probe-plan.json'
FENCE = PRIVATE / 'fence-probe-predispatch.json'
FENCE_R2 = PRIVATE / 'fence-probe-predispatch-r2.json'
CONSOLE_R2_PLAN = PRIVATE / 'fence-probe-console-r2-plan.json'
LIFECYCLE = PRIVATE / 'fence-probe-lifecycle.json'
RECEIPT = RUN / 'evidence/gov-delete-fence-probe.json'
NAME = 'vm-fence-01a168ce'
PRESET = '27165f6b-5ba3-448d-a6c5-e808da16206a'
COMPOSITE_SHA = 'f96d14f387598e8b5c706c2581c9aa1087cf845f71c6a0ec5331a4f4eac8298f'
PROBE_ACL_SHA = 'ba6bbaa414c90af01ebd02455d1780786235de839838a088674a82911bdc5aef'
ROOT = '/api/v1/virtual-machines'


def sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def components():
    gov = runpy.run_path(str(RUN / 'gov-candidate.py'))
    p = RUN / 'gov-delete-fence-predispatch.py'
    assert hashlib.sha256(p.read_bytes()).hexdigest() == COMPOSITE_SHA
    composite = runpy.run_path(str(p))['execute'].__globals__
    original = RUN / 'vm-product-import-delete.py'
    assert hashlib.sha256(original.read_bytes()).hexdigest() == '4d58eb1c5d2c59ef4aa20c60ca77b3630e32f1b7a46df3ab37c1fd559ead79d2'
    native = runpy.run_path(str(original))
    backend = runpy.run_path(str(RUN / 'vm-product-import-delete-backend.py'))
    return gov, composite, native, backend


def publish(gov, state, phase):
    state['phase'] = phase
    gov['save'](STATE, state)
    result = {'phase': phase, 'plan_sha256': state['plan_sha256'], 'name': NAME,
        'vm_id': state.get('vm_id'), 'create_operation_id': state.get('create_operation_id'),
        'create': state['create'], 'start': state['start'], 'native_before_delete': state.get('native_before_delete'),
        'family_before_delete': state.get('family_before_delete'), 'cleanup': state.get('cleanup'),
        'new_presets_or_media_or_permissions': 0, 'guest_initialization': 'OMITTED',
        'old_v3_and_tenant2': 'UNTOUCHED', 'old_v22_backend': 'UNCHANGED_NOT_MEASURED'}
    gov['save'](RECEIPT, result)


def prepare(gov, composite):
    if STATE.exists():
        state = json.loads(STATE.read_text())
        assert sha(state['plan']) == state['plan_sha256'] and json.loads(PLAN.read_text()) == state['plan']
        return state
    assert not PLAN.exists() and not FENCE.exists() and not LIFECYCLE.exists()
    config = json.loads((RUN / 'private/resource-candidate/vm.json').read_text())
    source = next(x for x in config['Service']['Media'] if x['media_key'] == 'ubuntu-24.04')
    assert source['pvc_uid'] == '8b235154-fae1-47ed-8f96-06af02a18ce1'
    assert source['checksum'] == '61f58093344ae5c84e22f1be07a904b0e2a15f7b6f18e45ef4549e52833c69c4'
    mapping = next(x for x in config['Networks'] if x['TenantID'] == 'b3c946b5-7eb3-4feb-9f82-79712062ca5c')
    body = {'data': {'name': NAME, 'presetId': PRESET, 'sourceKind': 'DISK_IMAGE', 'mediaKey': 'ubuntu-24.04',
        'systemDiskSizeGb': '12', 'idempotencyKey': str(uuid.uuid4()), 'startAfterCreate': False,
        'network': {'vpcId': mapping['VPCID'], 'subnetId': mapping['SubnetID']}}}
    keys = {action: str(uuid.uuid4()) for action, *_ in composite['CASES']}
    plan = {'name': NAME, 'create_body': body, 'start_key': str(uuid.uuid4()), 'delete_key': str(uuid.uuid4()),
        'console_key': str(uuid.uuid4()), 'negative_keys': keys, 'synthetic_disk_id_for_denied_routes': str(uuid.uuid4()),
        'synthetic_snapshot_id_for_denied_routes': str(uuid.uuid4()), 'selection': {'source': source, 'network': mapping},
        'cpu_millis': 2000, 'memory_mib': 4096, 'scope': 'one isolated task VM, normal CREATE then Start then DELETE',
        'pre_delete': 'CREATE and Start succeeded; unique Running owned VMI/launcher; exact root PV/CSI and base/-temp image IDs frozen',
        'old_replays': ['actual original CREATE', 'actual original Start', 'same DELETE', 'issued/unclaimed/unexpired console same binding/id/URL/expiry'],
        'negative_actions': [x[0] for x in composite['CASES']], 'pause_seconds_maximum': 55,
        'acl': {'database': 'vm_govlive_01a168ce', 'role': 'vm_govlive_runtime_01a168ce', 'table': 'public.sys_quota_operations',
            'change': 'REVOKE table UPDATE; GRANT UPDATE(id); finally restore exact original ACL',
            'business_row_writes': False, 'journal': str(PRIVATE / 'delete-fence-probe-acl.json'), 'helper_sha256': PROBE_ACL_SHA},
        'cleanup': 'original DELETE only; exact native UIDs/PV/VA then both mapped RBD family names/IDs and trash',
        'unexpected_ack': 'stop first admissions; observe original persisted intent, no automatic replay or new key',
        'protected': ['V3', 'tenant2', 'both ISO VMs', 'all source PVCs', 'all NADs', 'service config/binaries']}
    state = {'plan': plan, 'plan_sha256': sha(plan), 'prepared_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'create': {'body': body, 'send_attempts': 0, 'ack_unknown': False},
        'start': {'idempotency_key': plan['start_key'], 'send_attempts': 0, 'ack_unknown': False}}
    gov['save'](PLAN, plan)
    publish(gov, state, 'PREPARED_OFFLINE_ZERO_HTTP_ZERO_ACL')
    return state


def login(gov):
    c = gov['credentials']()
    token = gov['api']('POST', '/api/v1/auth/password/login', {'tenant_name': c['tenant_code'],
        'username': c['tenant_username'], 'password': c['tenant_password']})['access_token']
    me = gov['api']('GET', '/api/v1/admin/me', token=token)
    assert int(me['tenantId']) == 1 and me['username'] == c['tenant_username']
    return token


def observe_operation(gov, state, token, entry, seconds):
    deadline = time.monotonic() + seconds
    while True:
        op = gov['api']('GET', ROOT + '/' + state['vm_id'] + '/operations/' + entry['operation_id'], token=token)
        assert op['id'] == entry['operation_id'] and op['vmId'] == state['vm_id']
        entry['operation'] = op
        gov['save'](STATE, state)
        if op['state'] in ('succeeded', 'failed') or time.monotonic() >= deadline:
            return
        time.sleep(.5)


def create_or_start(gov, state, token, action, seconds, native=None):
    entry = state[action]
    if not entry.get('operation_id'):
        assert entry['send_attempts'] == 0 and not entry['ack_unknown'], 'unknown ACK: observation required, never auto-POST'
        if action == 'create':
            assert native is not None
            config = json.loads((RUN / 'private/resource-candidate/vm.json').read_text())
            source, mapping = state['plan']['selection']['source'], state['plan']['selection']['network']
            assert next(x for x in config['Service']['Media'] if x['media_key'] == 'ubuntu-24.04') == source
            assert [x for x in config['Networks'] if x['TenantID'] == mapping['TenantID']] == [mapping]
            assert native['native']('namespace', 'kube-system')['metadata']['uid'] == native['CLUSTER']
            assert native['native']('namespace', native['NS'])['metadata']['uid'] == native['NS_UID']
            source_pvc = native['native']('pvc', source['pvc_name'], source['namespace'])
            assert source_pvc['metadata']['uid'] == source['pvc_uid'] and source_pvc['status']['phase'] == 'Bound'
            assert source_pvc['metadata']['annotations']['vm.ani.io/source-sha256'] == source['checksum']
            nad = native['native']('network-attachment-definitions.k8s.cni.cncf.io', mapping['NADName'], native['NS'])
            assert nad['metadata']['uid'] == mapping['NADUID'] and sha(json.loads(nad['spec']['config'])) == mapping['NADConfigDigest']
            for plural, identifier in (('vpcs', mapping['VPCID']), ('subnets', mapping['SubnetID'])):
                network = gov['api']('GET', '/api/v1/networks/' + plural + '/' + identifier, token=token)
                assert network['id'] == identifier and network['state'] == 'available' and network['observation_stale'] is False
            selected = gov['api']('GET', '/api/v1/compute-presets?usage=VIRTUAL_MACHINE&compute_type=cpu&page=1&pageSize=100', token=token)
            preset = [x for x in selected['items'] if x['id'] == PRESET]
            assert len(preset) == 1 and preset[0]['enabled'] and preset[0]['selectable']
            assert int(preset[0]['cpu_millis']) == 2000 and int(preset[0]['memory_mib']) == 4096 and not preset[0].get('gpu')
            media = gov['api']('GET', '/api/v1/virtual-machine-media', token=token)['items']
            assert any(x['mediaKey'] == 'ubuntu-24.04' and x['kind'] == 'DISK_IMAGE' for x in media)
            route, body = ROOT, entry['body']
        else:
            assert state['create']['operation']['state'] == 'succeeded'
            current = gov['api']('GET', ROOT + '/' + state['vm_id'], token=token)
            assert current['id'] == state['vm_id'] and current['observedState'] == 'Stopped' and not current['closing']
            body = {'data': {'idempotencyKey': entry['idempotency_key'], 'expectedVersion': current['version']}}
            entry.update(body=body, expected_version=int(current['version']))
            route = ROOT + '/' + state['vm_id'] + '/start'
        entry.update(send_attempts=1, ack_unknown=True)
        gov['save'](STATE, state)
        accepted = gov['api']('POST', route, body, token=token)
        vmid, operation = (accepted['resourceId'], accepted['operationId']) if action == 'create' else (accepted['vmId'], accepted['id'])
        assert str(uuid.UUID(vmid)) == vmid and str(uuid.UUID(operation)) == operation
        if action == 'create':
            state.update(vm_id=vmid, create_operation_id=operation)
        else:
            assert vmid == state['vm_id']
        entry.update(operation_id=operation, ack_unknown=False, ack=accepted)
        gov['save'](STATE, state)
    observe_operation(gov, state, token, entry, seconds)
    publish(gov, state, action.upper() + '_' + entry['operation']['state'].upper())


def preserve_family(original, current, source_image):
    if original:
        assert [x['name'] for x in original] == [x['name'] for x in current], 'RBD family names changed'
        for before, fresh in zip(original, current):
            if fresh['result'] == 'PRESENT':
                assert before['result'] == 'PRESENT' and fresh['id'] == before['id'], 'same-name RBD replacement'
    else:
        original = copy.deepcopy(current)
    base, temp = current
    if base.get('parent'):
        assert base['parent']['pool'] == 'ani-block-pool' and base['parent']['image'] == temp['name']
        assert original[1]['result'] == 'PRESENT' and base['parent']['id'] == original[1]['id'], 'base parent identity changed'
    if temp.get('parent'):
        assert temp['parent']['pool'] == 'ani-block-pool' and temp['parent']['image'] == source_image, 'temp parent escaped frozen source'
    for before, fresh in zip(original, current):
        if before.get('parent') and fresh.get('parent'):
            assert all(before['parent'].get(k) == fresh['parent'].get(k) for k in ('pool', 'image', 'id', 'snapshot')), 'frozen parent identity changed'
    return original


def freeze_native(gov, native, backend, state):
    assert state['create']['operation']['state'] == state['start']['operation']['state'] == 'succeeded'
    observed = native['capture'](gov, state)
    assert observed['dv_phase'] == 'Succeeded' and all(observed[x] for x in ('vm', 'vmi', 'dv', 'pvc', 'pv'))
    vmi = native['native']('vmi', observed['vmi']['name'], observed['vmi']['namespace'])
    assert vmi['metadata']['uid'] == observed['vmi']['uid'] and vmi['status']['phase'] == 'Running'
    pods = native['native']('pod', namespace=native['NS'], labels='kubevirt.io/created-by=' + vmi['metadata']['uid'])['items']
    launchers = [x for x in pods if not x['metadata'].get('deletionTimestamp') and native['owned'](x, 'VirtualMachineInstance', vmi['metadata']['uid'])]
    assert len(launchers) == 1 and launchers[0]['status']['phase'] == 'Running'
    observed['launcher'] = native['ref'](launchers[0])
    csi = observed['pv_csi']; attrs = csi['volumeAttributes']; image = attrs['imageName']
    identity = str(uuid.UUID(image.removeprefix('csi-vol-')))
    assert csi['driver'] == 'rook-ceph.rbd.csi.ceph.com' and attrs['pool'] == 'ani-block-pool'
    assert csi['volumeHandle'].endswith(identity) and image == 'csi-vol-' + identity
    family = [backend['image_info'](native, image), backend['image_info'](native, image + '-temp')]
    assert family[0]['result'] == 'PRESENT'
    vas = native['native']('volumeattachments.storage.k8s.io')['items']
    observed['volume_attachments'] = [native['ref'](x) for x in vas
        if x.get('spec', {}).get('source', {}).get('persistentVolumeName') == observed['pv']['name']]
    previous = state.get('native_before_delete')
    if previous:
        for key in ('vm', 'vmi', 'launcher', 'dv', 'pvc', 'pv', 'pv_csi', 'dv_spec', 'attachment_id', 'volume_attachments'):
            assert previous[key] == observed[key], 'previously frozen native/storage identity changed: ' + key
    frozen_family = preserve_family(state.get('family_before_delete'), family, 'csi-vol-0c6f1f97-d348-4cdd-bdb6-5ae1e713c43a')
    state.update(native_before_delete=previous or observed, family_before_delete=frozen_family,
                 native_fresh=observed, family_fresh=family)
    gov['save'](STATE, state)
    if not previous:
        gov['save'](RUN / 'evidence/gov-delete-fence-probe-native.json', {'vm_id': state['vm_id'], 'native': observed, 'family': frozen_family})
    gov['save'](RUN / 'evidence/gov-delete-fence-probe-native-current.json',
                {'vm_id': state['vm_id'], 'native': observed, 'family': family, 'frozen_family': frozen_family})


def material(gov, state):
    vmid = state['vm_id']; old = state['start']
    assert old['operation']['state'] == 'succeeded' and old['send_attempts'] == 1
    lifecycle = {'vm_id': vmid, 'create_operation_id': state['create_operation_id'], 'commands': {
        'start': {'action': 'start', 'body': old['body'], 'operation_id': old['operation_id']}}}
    if LIFECYCLE.exists():
        existing = json.loads(LIFECYCLE.read_text())
        assert existing == lifecycle, 'a prior DELETE must be observed rather than repeated'
    return {'lifecycle_path': LIFECYCLE, 'lifecycle': lifecycle,
        'old_commands': [{'label': 'start', 'method': 'POST', 'route': ROOT + '/' + vmid + '/start',
            'body': old['body'], 'operation_id': old['operation_id']}],
        'disk': state['plan']['synthetic_disk_id_for_denied_routes'], 'create_body': state['create']['body']}


def prepare_console_r2(gov, state):
    if CONSOLE_R2_PLAN.exists():
        return json.loads(CONSOLE_R2_PLAN.read_text())
    old = json.loads(FENCE.read_text())
    assert old['vm_id'] == state['vm_id'] and old['create_operation_id'] == state['create_operation_id']
    assert old['phase'] == 'CONSOLE_ISSUE_PENDING' and old['delete_send_attempts'] == 0
    assert old['console']['send_attempts'] == 1 and not (PRIVATE / 'delete-fence-probe-acl.json').exists()
    evidence = json.loads((RUN / 'evidence/gov-delete-fence-probe-console-attribution.json').read_text())
    assert evidence['same_original_key_vm_and_binding'] and evidence['ticket_expired_at_observation']
    recovery = {'generation': 'console-r2-only', 'vm_id': state['vm_id'], 'create_operation_id': state['create_operation_id'],
        'original_plan_sha256': state['plan_sha256'], 'original_fence_state_sha256': hashlib.sha256(FENCE.read_bytes()).hexdigest(),
        'old_console_key_sha256': hashlib.sha256(old['console_key'].encode()).hexdigest(),
        'old_session_id': evidence['session_id'], 'old_ticket_expired': True, 'old_wire_ack_not_retained': True,
        'console_key': str(uuid.uuid4()), 'expected_version': int(old['console']['body']['data']['expectedVersion']),
        'expected_http_status': 202, 'private_ack_before_assertion': True,
        'unchanged': ['CREATE/Start/DELETE/negative keys and bodies', 'same existing VM', 'original r1 evidence', 'ACL SQL and 55s finally restore'],
        'fence_state': str(FENCE_R2), 'receipt': str(RUN / 'evidence/gov-delete-fence-probe-predispatch-live-r2.json')}
    recovery['console_body'] = {'data': {'idempotencyKey': recovery['console_key'], 'expectedVersion': str(recovery['expected_version'])}}
    gov['save'](CONSOLE_R2_PLAN, recovery)
    return recovery


def fence(gov, composite, native, backend, state, fd, recovery=None):
    freeze_native(gov, native, backend, state)
    plan = state['plan']
    fence_path = FENCE_R2 if recovery else FENCE
    assert not fence_path.exists(), 'existing fence state requires read-only recovery; never re-pause'
    if recovery:
        assert recovery['vm_id'] == state['vm_id'] and recovery['original_plan_sha256'] == state['plan_sha256']
        assert hashlib.sha256(FENCE.read_bytes()).hexdigest() == recovery['original_fence_state_sha256']
    target = {'vm_id': state['vm_id'], 'create_operation_id': state['create_operation_id'], 'phase': 'PREPARED_NO_HTTP',
        'delete_key': plan['delete_key'], 'snapshot_id': plan['synthetic_snapshot_id_for_denied_routes'],
        'negative_keys': plan['negative_keys'], 'console_key': recovery['console_key'] if recovery else plan['console_key'],
        'delete_send_attempts': 0, 'negative_requests_sent': 0, 'lock_fd': fd}
    if recovery:
        assert recovery['console_body'] == {'data': {'idempotencyKey': recovery['console_key'], 'expectedVersion': str(recovery['expected_version'])}}
        target['prepared_expected_version'] = recovery['expected_version']
    composite.update(VM=state['vm_id'], CREATE=state['create_operation_id'], BASE=ROOT + '/' + state['vm_id'],
        ACL_INHERITED_PATH=RUN / 'vm-gov-delete-fence-probe-pause-inherited.py', INHERITED_ACL_SHA=PROBE_ACL_SHA,
        ACL_JOURNAL=PRIVATE / 'delete-fence-probe-acl.json', ACL_RECEIPT=RUN / 'evidence/gov-delete-fence-probe-acl.json',
        LIVE_RECEIPT=RUN / ('evidence/gov-delete-fence-probe-predispatch-live-r2.json' if recovery else 'evidence/gov-delete-fence-probe-predispatch-live.json'), target_material=lambda driver: material(driver, state))
    composite['CASES'] = [(a, m, r, {'mediaKey': 'fedora-44-iso'} if a == 'set-media' else extra)
                          for a, m, r, extra in composite['CASES']]
    gov['save'](fence_path, target)
    composite['execute'](gov, fence_path, target)


def cleanup(gov, native, backend, state, token, recovery=False):
    target = json.loads((FENCE_R2 if recovery else FENCE).read_text())
    assert target['vm_id'] == state['vm_id'] and target['create_operation_id'] == state['create_operation_id']
    assert target.get('delete_operation_id') and target['delete_send_attempts'] == 1
    operation = gov['api']('GET', ROOT + '/' + state['vm_id'] + '/operations/' + target['delete_operation_id'], token=token)
    assert operation['id'] == target['delete_operation_id'] and operation['vmId'] == state['vm_id']
    frozen = state['native_before_delete']; remaining = []
    for key, kind in (('vm', 'vm'), ('vmi', 'vmi'), ('launcher', 'pod'), ('dv', 'dv'), ('pvc', 'pvc'), ('pv', 'pv')):
        ref = frozen[key]
        current = native['native'](kind, ref['name'], ref.get('namespace') or None)
        assert not current or current['metadata']['uid'] == ref['uid'], 'same-name replacement; stop rather than infer absence'
        if current:
            remaining.append(native['ref'](current))
    for ref in frozen['volume_attachments']:
        current = native['native']('volumeattachments.storage.k8s.io', ref['name'])
        assert not current or current['metadata']['uid'] == ref['uid'], 'VA replacement; stop rather than infer absence'
        if current:
            remaining.append(native['ref'](current))
    trash = json.loads(backend['ceph'](native, ['rbd', 'trash', 'ls', '-p', 'ani-block-pool', '--format=json']))
    families = []
    for original in state['family_before_delete']:
        value = backend['image_info'](native, original['name'])
        assert value['result'] == 'ABSENT' or original['result'] != 'PRESENT' or value['id'] == original['id'], 'RBD image replacement'
        matches = [x for x in trash if x.get('name') == original['name'] or original.get('id') and x.get('id') == original['id']]
        families.append({'original': original, 'current': value, 'matching_trash': matches})
    attachments = native['attachment'](gov, state)
    released = len(attachments) == 1 and attachments[0]['state'] == 'released' and attachments[0]['blocked'] is False
    passed = operation['state'] == 'succeeded' and not remaining and released and all(
        x['current']['result'] == 'ABSENT' and not x['matching_trash'] for x in families)
    state['cleanup'] = {'result': 'PASS' if passed else 'PENDING_NORMAL_GC', 'delete_operation': operation,
        'remaining_original_objects': remaining, 'attachment': attachments, 'rbd_family': families,
        'backend_manual_delete': False, 'old_v22': 'UNCHANGED_NOT_MEASURED'}
    publish(gov, state, 'CLEANED' if passed else 'ORIGINAL_DELETE_OR_GC_PENDING')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'create', 'start', 'freeze-native', 'fence', 'observe', 'cleanup', 'prepare-console-r2', 'fence-r2', 'cleanup-r2'))
    parser.add_argument('--execute-authorized', action='store_true')
    parser.add_argument('--plan-sha')
    parser.add_argument('--console-r2-plan-sha')
    parser.add_argument('--wait-seconds', type=int, choices=range(31), default=30)
    args = parser.parse_args()
    assert os.environ.get('SSH_CONNECTION', '').split()[2:3] == ['172.16.101.31']
    gov, composite, native, backend = components()
    with Path('/home/chabking/workspace/ani-network-service-runs/net05a-heavy.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = prepare(gov, composite)
        if args.action == 'prepare-console-r2':
            assert sha(state['plan']) == args.plan_sha
            recovery = prepare_console_r2(gov, state)
            print(json.dumps({'phase': 'PREPARED_R2_ZERO_HTTP_ZERO_ACL', 'console_r2_plan_sha256': sha(recovery),
                'private_plan_reference': str(CONSOLE_R2_PLAN), 'vm_id': state['vm_id'], 'old_state_preserved': True}))
            return
        if args.action != 'prepare':
            assert sha(state['plan']) == args.plan_sha
            acl_path = PRIVATE / 'delete-fence-probe-acl.json'
            if acl_path.exists():
                acl = json.loads(acl_path.read_text())
                public_acl = json.loads((RUN / 'evidence/gov-delete-fence-probe-acl.json').read_text())
                assert acl['phase'] == 'restored' and public_acl['current'] == public_acl['original'], 'restore exact ACL before any business follow-up'
            if args.action in ('create', 'start', 'fence', 'fence-r2'):
                assert args.execute_authorized, 'separately approved execute window required'
            token = login(gov)
            if args.action in ('create', 'start'):
                create_or_start(gov, state, token, args.action, args.wait_seconds, native)
            elif args.action == 'freeze-native':
                freeze_native(gov, native, backend, state)
            elif args.action in ('fence', 'fence-r2'):
                recovery = None
                if args.action == 'fence-r2':
                    recovery = json.loads(CONSOLE_R2_PLAN.read_text())
                    assert sha(recovery) == args.console_r2_plan_sha
                fence(gov, composite, native, backend, state, lock.fileno(), recovery)
            elif args.action in ('cleanup', 'cleanup-r2'):
                cleanup(gov, native, backend, state, token, args.action == 'cleanup-r2')
            else:
                for entry in (state['create'], state['start']):
                    if entry.get('operation_id'):
                        observe_operation(gov, state, token, entry, 0)
                publish(gov, state, 'READ_ONLY_ORIGINAL_OPERATIONS')
        print(json.dumps({'phase': state['phase'], 'plan_sha256': state['plan_sha256'], 'private_plan_reference': str(PLAN),
            'receipt': str(RECEIPT), 'vm_id': state.get('vm_id'), 'create_send_attempts': state['create']['send_attempts'],
            'start_send_attempts': state['start']['send_attempts'], 'pause_executed': False if args.action == 'prepare' else 'SEE_SEPARATE_FENCE_RECEIPT'}))


if __name__ == '__main__':
    main()
