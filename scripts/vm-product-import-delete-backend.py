#!/usr/bin/env python3
"""One additional, task-only CDI-unready DELETE with pre-DELETE backend identity.

This fixed generation preserves the first run and its NOT_MEASURED result.
prepare is read-only apart from task evidence. execute needs a separately
approved plan SHA; no controller/storage changes or backend deletions exist here.
"""
import argparse
import copy
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import subprocess
import time
import uuid

RUN = Path('/home/chabking/workspace/ani-resource-service-runs/vm-mvp-20261023-01a168ce')
BASE_SHA = '4d58eb1c5d2c59ef4aa20c60ca77b3630e32f1b7a46df3ab37c1fd559ead79d2'
STATE = RUN / 'private/gov-candidate/import-delete-backend-v2.json'
PLAN = RUN / 'private/gov-candidate/import-delete-backend-v2-plan.json'
RECEIPT = RUN / 'evidence/gov-import-delete-backend-v2.json'
NAME = 'vm-mvp-import-delete-backend-v2-01a168ce'
READER = {'kind': 'Pod', 'namespace': 'rook-ceph', 'name': 'rook-ceph-tools-68d6bc7cc7-2z8tk',
          'uid': 'd144f419-df30-43d0-aa23-fcbcda6e01ce'}
POOL = 'ani-block-pool'
POOL_ID = '0000000000000004'
DRIVER = 'rook-ceph.rbd.csi.ceph.com'
OWNED = 'cdi.kubevirt.io/OwnedByUID'
SOURCES = ['https://github.com/ceph/ceph-csi/blob/devel/internal/journal/voljournal.go',
           'https://github.com/ceph/ceph-csi/blob/devel/internal/rbd/clone.go',
           'https://github.com/kubevirt/containerized-data-importer/blob/main/pkg/controller/clone/csi-clone.go']
ORIGINAL_APPROVED_PLAN = 'c732585292b6e1f61e5231afd18be72a0a1281d098d8b5312f2456039b44a3d1'
ROOT_POLICY_CORRECTION = 'Root explicitly approved the original c732 plan/body8d1265 on 2026-10-24, then required one absolute 30s capture deadline, remaining-time subprocess bounds, and same-key normal DELETE after timeout/missing/ready; no new resource or CREATE body.'
GENERATION = 'backend-v2'
ROOT_V3_AUTHORIZATION = 'Root explicitly approved only one additional backend-v3 after v2 normal cleanup on 2026-10-24: same source UID/hash, 12GB, startfalse/noinit/0VMI; only new unique name/key; fix exact rados stdout NoSuchKey, retain strict 30s/unready gate, normal DELETE, and no fourth case.'


class CaptureDeadline(RuntimeError):
    pass


class CaptureUnavailable(RuntimeError):
    pass


def remaining(base, maximum):
    deadline = base.get('_capture_deadline')
    if deadline is None:
        return maximum
    value = deadline - time.monotonic()
    if value <= 0:
        raise CaptureDeadline('absolute capture deadline reached; continue normal DELETE, no measurement claim')
    return min(maximum, value)


@contextlib.contextmanager
def bounded_reads(base, deadline):
    """Only these native/CSI reads share the task's absolute deadline."""
    original = base['native']
    def native(kind, name=None, namespace=None, labels=None):
        argv = base['K'] + ['get', kind]
        if name: argv += [name, '--ignore-not-found=true']
        if namespace: argv += ['-n', namespace]
        if labels: argv += ['-l', labels]
        raw = subprocess.run(argv + ['-o', 'json'], capture_output=True, check=True,
                             timeout=remaining(base, 8)).stdout
        return json.loads(raw) if raw.strip() else None
    base['_capture_deadline'], base['native'] = deadline, native
    try:
        yield
    finally:
        base['native'] = original
        base.pop('_capture_deadline', None)


def require(value, reason):
    if not value:
        raise RuntimeError(reason)


def load_base():
    path = RUN / 'vm-product-import-delete.py'
    require(hashlib.sha256(path.read_bytes()).hexdigest() == BASE_SHA, 'historical driver SHA changed')
    base = runpy.run_path(str(path))
    env = base['capture'].__globals__
    env.update(STATE=STATE, PLAN=PLAN, RECEIPT=RECEIPT, NAME=NAME)
    return env


def project(value, base):
    """No annotations, credentials, full native objects, or full CSI responses."""
    return {**base['ref'](value), 'owners': value['metadata'].get('ownerReferences', []),
            'owned_by_target_uid': value['metadata'].get('labels', {}).get(OWNED),
            'spec': value.get('spec', {}), 'phase': value.get('status', {}).get('phase')}


def related_claims(base, target, candidates, source):
    result = [target]
    for value in candidates:
        require(value['metadata'].get('labels', {}).get(OWNED) == target['metadata']['uid'],
                'CDI intermediate lacks exact target UID ownership label')
        if value['metadata']['uid'] == target['metadata']['uid']:
            continue
        require(value['metadata']['namespace'] in (target['metadata']['namespace'], source['namespace']),
                'intermediate escaped the two approved namespaces')
        ds = value['spec'].get('dataSourceRef') or value['spec'].get('dataSource') or {}
        require(value['metadata']['namespace'] == source['namespace'] and ds.get('kind') == 'PersistentVolumeClaim'
                and ds.get('name') == source['pvc_name'] and not ds.get('namespace'),
                'intermediate is not a same-source CSI clone; do not attribute it by name')
        require(value['spec'].get('storageClassName') == 'ani-block' and value['spec'].get('volumeMode') == 'Block'
                and value['spec'].get('accessModes') == ['ReadWriteOnce'], 'intermediate storage contract differs')
        result.append(value)
    return result


def pv_projection(base, claim, pv, known_claims):
    cr = pv['spec']['claimRef']
    require(cr.get('uid') in known_claims and all(cr.get(key) == known_claims[cr['uid']]['metadata'][key]
            for key in ('namespace', 'name')), 'PV claim identity is outside this owned clone chain')
    csi = pv['spec'].get('csi', {})
    handle = csi.get('volumeHandle', '')
    require(csi.get('driver') == DRIVER and handle.startswith('0001-0009-rook-ceph-' + POOL_ID + '-'),
            'unexpected CSI driver/cluster/pool handle')
    identity = str(uuid.UUID(handle[-36:]))
    require(handle.endswith(identity), 'invalid CSI handle UUID')
    attrs = csi.get('volumeAttributes', {})
    require(attrs.get('pool') == POOL and attrs.get('clusterID') == 'rook-ceph', 'CSI pool or cluster differs')
    image = attrs.get('imageName') or 'csi-vol-' + identity
    require(image == 'csi-vol-' + identity, 'CSI image name differs from the exact handle')
    return {'claim': base['ref'](claim), 'pv': base['ref'](pv), 'claim_ref': pv['spec']['claimRef'],
            'volume_handle': handle, 'pool': POOL, 'image': image, 'reservation_uuid': identity}


def reservation(request, encoded, reverse, image):
    """Bidirectional CSI journal attribution, never guessed from source children."""
    parts = encoded.strip().split('/')
    require(len(parts) in (1, 2) and (len(parts) == 1 or parts[0] == POOL_ID), 'unknown reservation pool encoding')
    identity = str(uuid.UUID(parts[-1]))
    require(parts[-1] == identity and reverse.strip() == request, 'CSI reservation reverse request mismatch')
    require(image.strip() == 'csi-vol-' + identity, 'CSI journal image does not match its reservation')
    return {'request': request, 'reservation_uuid': identity, 'pool': POOL, 'image': image.strip(),
            'directory': 'csi.volumes.default', 'key': 'csi.volume.' + request,
            'reverse_object': 'csi.volume.' + identity, 'reverse_request_verified': True}


def exact_stdout_journal_miss(completed, argv):
    if completed.returncode != 1 or len(argv) != 7 or argv[:4] != ['rados', '-p', POOL, 'getomapval'] or argv[-1] != '-':
        return False
    obj, key = argv[4:6]
    uid = r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}'
    directory = obj == 'csi.volumes.default' and re.fullmatch(r'csi\.volume\.pvc-' + uid, key)
    reverse = re.fullmatch(r'csi\.volume\.' + uid, obj) and key in ('csi.volname', 'csi.imagename')
    if not (directory or reverse):
        return False
    expected = ('No such key: ' + POOL + '/' + obj + '/' + key).encode()
    return completed.stdout.rstrip(b'\r\n') == expected and completed.stderr.strip() in (b'', b'command terminated with exit code 1')


def ceph(base, argv):
    pod = base['native']('pod', READER['name'], READER['namespace'])
    if not pod or pod['metadata']['uid'] != READER['uid'] or pod['status']['phase'] != 'Running':
        raise CaptureUnavailable('approved read-only Ceph tools pod unavailable; no replacement client')
    completed = subprocess.run(base['K'] + ['exec', '-n', READER['namespace'], READER['name'], '--'] + argv,
                               capture_output=True, timeout=remaining(base, 8))
    if completed.returncode:
        missing = b'No such file or directory' in completed.stderr or b'No such key' in completed.stderr or exact_stdout_journal_miss(completed, argv)
        if not missing:
            raise CaptureUnavailable('exact Ceph read failed; no absent conclusion')
        return None
    return completed.stdout


def omap(base, obj, key):
    raw = ceph(base, ['rados', '-p', POOL, 'getomapval', obj, key, '-'])
    return None if raw is None else raw.decode().strip()


def image_info(base, name):
    raw = ceph(base, ['rbd', 'info', POOL + '/' + name, '--format=json'])
    if raw is None:
        return {'name': name, 'result': 'ABSENT'}
    value = json.loads(raw)
    require(value.get('name') == name and re.fullmatch(r'[0-9a-f]+', value.get('id', '')),
            'RBD returned a different image identity')
    return {'name': name, 'result': 'PRESENT', 'id': value['id'], 'size': value.get('size'),
            'parent': value.get('parent')}


def capture_backend(base, g, state):
    result = base['capture'](g, state)
    result['backend'] = {'related_claims': [], 'pvs': [], 'mappings': [], 'physical_images': [],
                         'measurable': False, 'source': 'NOT_CAPTURED_BEFORE_DELETE'}
    state['last_native'] = result  # Preserve known UID evidence if a later read times out.
    if not result.get('pvc'):
        return result
    source = state['plan']['selection']['source']
    target = base['native']('pvc', result['pvc']['name'], base['NS'])
    require(target and target['metadata']['uid'] == result['pvc']['uid'], 'target PVC changed during capture')
    ds = target['spec'].get('dataSourceRef', {})
    if ds.get('kind') == 'VolumeCloneSource':
        namespace = ds.get('namespace') or target['metadata'].get('annotations', {}).get('cdi.kubevirt.io/dataSourceNamespace') or base['NS']
        require(namespace in (base['NS'], source['namespace']), 'clone source reference escaped approved namespaces')
        vcs = base['native']('volumeclonesources.cdi.kubevirt.io', ds['name'], namespace)
        if vcs:
            require(vcs['metadata']['namespace'] == source['namespace'] and vcs['spec']['source'].get('kind') == 'PersistentVolumeClaim'
                    and vcs['spec']['source'].get('name') == source['pvc_name'], 'VolumeCloneSource differs from frozen source')
            result['backend']['volume_clone_source'] = project(vcs, base)
    candidates = []
    for namespace in (base['NS'], source['namespace']):
        candidates.extend(base['native']('pvc', namespace=namespace, labels=OWNED + '=' + target['metadata']['uid'])['items'])
    claims = related_claims(base, target, candidates, source)
    known = {item['metadata']['uid']: item for item in claims}
    data = result['backend']
    data['related_claims'] = [project(item, base) for item in claims]
    for claim in claims:
        if claim['spec'].get('volumeName'):
            pv = base['native']('pv', claim['spec']['volumeName'])
            require(pv is not None, 'named PV vanished during capture')
            data['pvs'].append(pv_projection(base, claim, pv, known))
        request = 'pvc-' + claim['metadata']['uid']
        encoded = omap(base, 'csi.volumes.default', 'csi.volume.' + request)
        if encoded is None:
            continue
        identity = str(uuid.UUID(encoded.split('/')[-1]))
        obj = 'csi.volume.' + identity
        reverse, image = omap(base, obj, 'csi.volname'), omap(base, obj, 'csi.imagename')
        if reverse is None or image is None:
            continue  # an incomplete reservation is not a usable attribution
        mapping = reservation(request, encoded, reverse, image)
        mapping['claim'] = base['ref'](claim)
        matching = [item for item in data['pvs'] if item['claim']['uid'] == claim['metadata']['uid']]
        require(not matching or matching[0]['reservation_uuid'] == mapping['reservation_uuid'], 'PV and journal disagree')
        data['mappings'].append(mapping)
        for name in (mapping['image'], mapping['image'] + '-temp'):
            info = image_info(base, name)
            info.update(pool=POOL, request=request, reservation_uuid=identity)
            data['physical_images'].append(info)
    # A real PV with the exact owned claim UID is independently sufficient, even
    # when the CSI request journal no longer exposes its temporary reservation.
    for pv in data['pvs']:
        if any(item['image'] == pv['image'] for item in data['mappings']):
            continue
        mapping = {key: pv[key] for key in ('claim', 'reservation_uuid', 'pool', 'image', 'volume_handle')}
        mapping['basis'] = 'PV_EXACT_CLAIM_UID_AND_CSI_HANDLE'
        data['mappings'].append(mapping)
        for name in (pv['image'], pv['image'] + '-temp'):
            info = image_info(base, name)
            info.update(pool=POOL, request='pvc-' + pv['claim']['uid'], reservation_uuid=pv['reservation_uuid'])
            data['physical_images'].append(info)
    data['measurable'] = any(item['result'] == 'PRESENT' for item in data['physical_images'])
    data['source'] = 'EXACT_PVC_UID_PV_OR_BIDIRECTIONAL_CSI_JOURNAL' if data['measurable'] else 'NOT_CAPTURED_BEFORE_DELETE'
    return result


def freeze(state, latest, base):
    base['freeze_before_delete'](state, latest)
    old = state.setdefault('backend_history', {})
    for item in latest.get('backend', {}).get('related_claims', []):
        key = item['namespace'] + '/' + item['name']
        require(key not in old or old[key]['uid'] == item['uid'], 'same-name intermediate PVC replacement')
        old[key] = item
    if latest.get('backend', {}).get('measurable') and latest.get('dv_phase') in base['PREPARING_PHASES']:
        state['backend_before_delete'] = copy.deepcopy(latest['backend'])


def freeze_final(state, latest, base):
    # Final reads deliberately do not rescan the slow CSI backend. They only
    # validate the frozen VM/DV/PVC chain and the current CDI phase.
    base['freeze_before_delete'](state, latest)
    if latest.get('dv_phase') not in base['PREPARING_PHASES'] or not latest.get('final_owner_chain_verified'):
        state['backend_before_delete'] = None


def capture_window(base, g, state, seconds):
    started = time.monotonic()
    deadline = started + min(seconds, 30)
    state['capture_window'] = {'maximum_seconds': min(seconds, 30), 'absolute_deadline': deadline, 'result': 'CAPTURING'}
    latest = state.get('last_native', {})
    with bounded_reads(base, deadline):
        while True:
            try:
                remaining(base, 8)
                latest = capture_backend(base, g, state)
                state['last_native'] = latest
                if latest.get('vm') and latest.get('dv') and latest.get('pvc'):
                    if latest.get('dv_phase') != 'Succeeded' and latest.get('dv_phase') not in base['PREPARING_PHASES']:
                        state['capture_window']['result'] = 'UNRECOGNIZED_PHASE_NOT_MEASURED'
                        state['backend_before_delete'] = None
                        break
                    freeze(state, latest, base)
                    g['save'](STATE, state)
                    if latest.get('dv_phase') == 'Succeeded' or latest['backend']['measurable']:
                        state['capture_window']['result'] = 'READY_WINDOW_MISSED' if latest['dv_phase'] == 'Succeeded' else 'IDENTITY_CAPTURED'
                        break
                time.sleep(min(.25, remaining(base, .25)))
            except (CaptureDeadline, subprocess.TimeoutExpired, CaptureUnavailable, subprocess.CalledProcessError, ValueError) as error:
                latest = state.get('last_native', latest)
                state['capture_window'].update(result='TIMED_OUT_NOT_MEASURED' if isinstance(error, (CaptureDeadline, subprocess.TimeoutExpired)) else 'READ_UNAVAILABLE_NOT_MEASURED',
                                               error_type=type(error).__name__)
                state['backend_before_delete'] = None
                break
    state['capture_window']['elapsed_seconds'] = time.monotonic() - started
    g['save'](STATE, state)
    return latest


def final_owner_read(base, state):
    """A short fresh fence before product DELETE, never another backend scan."""
    result = {'at': base['now'](), 'vm': None, 'dv': None, 'pvc': None,
              'dv_phase': None, 'final_owner_chain_verified': False}
    try:
        with bounded_reads(base, time.monotonic() + 10):
            vm = base['native']('vm', 'vm-' + state['vm_id'], base['NS'])
            if not vm:
                return result
            require(vm['metadata'].get('labels', {}).get('ani.io/tenant-id') == base['TENANT']
                    and vm['metadata'].get('labels', {}).get('vm.ani.io/vm-id') == state['vm_id'], 'fresh VM identity differs')
            result['vm'] = base['ref'](vm)
            expected = state.get('native_before_delete', {}).get('vm')
            require(not expected or result['vm'] == expected, 'fresh VM UID replaced')
            require(vm['spec']['runStrategy'] == 'Manual' and not vm.get('status', {}).get('stateChangeRequests'), 'implicit Start observed')
            disks = base['native']('dv', namespace=base['NS'], labels='vm.ani.io/vm-id=' + state['vm_id'])['items']
            require(len(disks) <= 1, 'fresh owner chain has extra DVs')
            if not disks:
                return result
            dv = disks[0]
            require(base['owned'](dv, 'VirtualMachine', vm['metadata']['uid']), 'fresh DV owner differs')
            result.update(dv=base['ref'](dv), dv_phase=dv.get('status', {}).get('phase'))
            pvc = base['native']('pvc', dv['metadata']['name'], base['NS'])
            if not pvc:
                return result
            require(base['owned'](pvc, 'DataVolume', dv['metadata']['uid']) or base['owned'](pvc, 'VirtualMachine', vm['metadata']['uid']), 'fresh PVC owner differs')
            result['pvc'] = base['ref'](pvc)
            for key in ('dv', 'pvc'):
                original = state.get('native_before_delete', {}).get(key)
                require(not original or original == result[key], 'fresh owner UID replaced: ' + key)
            result['final_owner_chain_verified'] = True
    except (CaptureDeadline, subprocess.TimeoutExpired, subprocess.CalledProcessError) as error:
        result.update(dv_phase=None, fresh_read_error=type(error).__name__)
    return result


def normal_delete(base, g, state, token, replay=False):
    original_capture, original_freeze = base['capture'], base['freeze_before_delete']
    base['capture'] = lambda gov, current: final_owner_read(base, current)
    base['freeze_before_delete'] = lambda current, latest: freeze_final(current, latest, {**base, 'freeze_before_delete': original_freeze})
    try:
        base['accept'](g, state, token, delete=True, replay=replay)
    finally:
        base['capture'], base['freeze_before_delete'] = original_capture, original_freeze


def publish(base, g, state, phase):
    base['publish'](g, state, phase)
    public = json.loads(RECEIPT.read_text())
    public.update(backend_before_delete=state.get('backend_before_delete'),
                  related_claims_history=list(state.get('backend_history', {}).values()),
                  backend_result=state.get('backend_result', 'NOT_MEASURED'),
                  capture_window=state.get('capture_window'), policy_correction=state.get('policy_correction'),
                  historical_run='UNCHANGED_NOT_MEASURED')
    g['save'](RECEIPT, public)


def measure_after_delete(base, state):
    frozen = state.get('backend_before_delete')
    if not frozen:
        return {'result': 'NOT_MEASURED', 'reason': 'no owned physical image captured before unready DELETE'}
    claims = list(state.get('backend_history', {}).values())
    remaining = []
    for item in claims:
        current = base['native']('pvc', item['name'], item['namespace'])
        require(not current or current['metadata']['uid'] == item['uid'], 'same-name PVC replacement after DELETE')
        if current:
            remaining.append(base['ref'](current))
    pvs = []
    for item in frozen['pvs']:
        current = base['native']('pv', item['pv']['name'])
        require(not current or current['metadata']['uid'] == item['pv']['uid'], 'same-name PV replacement after DELETE')
        if current:
            pvs.append(base['ref'](current))
    trash = json.loads(ceph(base, ['rbd', 'trash', 'ls', '-p', POOL, '--format=json']))
    wanted = {item['name']: item for item in frozen['physical_images']}
    images = []
    for name, original in wanted.items():
        current = image_info(base, name)
        require(current['result'] == 'ABSENT' or original['result'] != 'PRESENT' or current['id'] == original['id'],
                'RBD same-name image replacement; do not infer cleanup')
        matches = [item for item in trash if item.get('name') == name or original.get('id') and item.get('id') == original['id']]
        images.append({'original': original, 'current': current, 'matching_trash': matches})
    passed = not remaining and not pvs and all(item['current']['result'] == 'ABSENT' and not item['matching_trash'] for item in images)
    return {'result': 'PASS' if passed else 'PENDING_NORMAL_GC', 'remaining_pvc': remaining, 'remaining_pv': pvs, 'images': images,
            'manual_backend_delete': False, 'at': base['now']()}


def run(base, g, args):
    token, principal = base['login'](g)
    if args.action == 'prepare':
        fresh = not STATE.exists()
        if fresh:
            reader = base['native']('pod', READER['name'], READER['namespace'])
            require(reader and base['ref'](reader) == READER and reader['status']['phase'] == 'Running', 'Ceph reader changed')
            sc = base['native']('sc', 'ani-block')
            require(sc['provisioner'] == DRIVER and sc['parameters']['pool'] == POOL
                    and sc['parameters']['clusterID'] == 'rook-ceph', 'approved StorageClass differs')
        state = base['prepare'](g, token, principal)
        if fresh:
            state['plan'].update(generation=GENERATION, historical_driver_sha256=BASE_SHA, backend_reader=READER,
                backend_policy={'maximum_capture_seconds': 30, 'require_cdi_unready_at_final_delete_read': True,
                    'required_identity': 'owned PVC UID -> exact request -> reservation UUID -> reverse request + image -> actual image ID',
                    'missing_identity': 'NOT_MEASURED; normal DELETE cleanup, never wait for Ready to claim unready',
                    'scope': 'two approved namespaces, exact OwnedByUID PVCs, exact journal keys/images only',
                    'upstream_schema_sources': SOURCES, 'upstream_branch_is_runtime_version_evidence': False})
            state['plan_sha256'] = base['digest'](state['plan'])
            g['save'](PLAN, state['plan'])
        policy = {'root_authorization': ROOT_POLICY_CORRECTION, 'original_approved_plan_sha256': ORIGINAL_APPROVED_PLAN,
                  'shared_absolute_capture_deadline_seconds': 30, 'remaining_time_bounds_all_capture_subprocesses': True,
                  'timeout_missing_ready': 'normal same-key Gov DELETE; measurement/unready PASS forbidden',
                  'final_reads': 'fresh VM/DV/PVC UID/owner and DV phase only; no slow backend rescan',
                  'unknown_ack': 'observe only; no automatic or explicit replay in this task'}
        if GENERATION == 'backend-v3':
            policy['additional_v3_authorization'] = ROOT_V3_AUTHORIZATION
            policy['authorized_create_changes_from_v2'] = ['name', 'idempotencyKey']
        if state.get('policy_correction') != policy:
            require(state['create']['send_attempts'] == state['delete']['send_attempts'] == 0 and not state.get('vm_id'), 'policy correction cannot change an admitted case')
            if GENERATION == 'backend-v2':
                require(base['digest'](state['plan']['create_body']) == '8d1265c70b220de42f122e1394fd7d1cb47db3af62cd92eb152f17d8621c7832', 'approved original CREATE body changed')
            else:
                original = json.loads((RUN / 'private/gov-candidate/import-delete-backend-v2-plan.json').read_text())['create_body']['data']
                created = state['plan']['create_body']['data']
                require(created['name'] == NAME and created['idempotencyKey'] != original['idempotencyKey']
                        and {k: v for k, v in created.items() if k not in ('name', 'idempotencyKey')} ==
                            {k: v for k, v in original.items() if k not in ('name', 'idempotencyKey')}, 'v3 exceeded the only authorized name/key differences')
            state['policy_correction'] = policy
            state['plan']['backend_policy'].update(policy)
            state['plan_sha256'] = base['digest'](state['plan'])
            g['save'](PLAN, state['plan'])
        publish(base, g, state, 'PREPARED_NO_VM_POST')
        return state
    state = json.loads(STATE.read_text())
    require(base['digest'](state['plan']) == state['plan_sha256'] and json.loads(PLAN.read_text()) == state['plan'], 'frozen plan changed')
    if args.action == 'execute':
        require(args.execute_authorized and args.plan_sha == state['plan_sha256'], 'approved exact new-run plan SHA required')
        selected = base['preflight'](g, token)
        require(selected['source'] == state['plan']['selection']['source'] and selected['network'] == state['plan']['selection']['network'], 'source/network drift')
        base['accept'](g, state, token, replay=args.replay_original)
    if not state.get('vm_id'):
        require(args.action == 'observe', 'unknown CREATE ACK requires original observation; do not guess/adopt a VM')
        visible = g['api']('GET', base['BASE'] + '?page=1&pageSize=100', token=token)['items']
        state['same_name_inventory_only'] = [{'id': item['id'], 'name': item['name']} for item in visible if item['name'] == NAME]
        state['ack_unknown_observed_at'] = base['now']()
        publish(base, g, state, 'UNKNOWN_CREATE_ACK_NO_ADOPTION')
        return state
    if not state['delete'].get('operation_id'):
        if state['delete']['outcome'] == 'ack_unknown':
            if args.action == 'observe':
                state['original_create_operation'] = g['api']('GET', base['BASE'] + '/' + state['vm_id'] + '/operations/' + state['create_operation_id'], token=token)
                state['ack_unknown_observed_at'] = base['now']()
                publish(base, g, state, 'UNKNOWN_DELETE_ACK_OBSERVED_NO_NEW_POST')
                return state
            require(False, 'unknown DELETE ACK: observe original operation, never resend')
        latest = capture_window(base, g, state, args.wait_seconds)
        if args.action == 'observe':
            state['ack_unknown_observed_at'] = base['now']()
            publish(base, g, state, 'READ_ONLY_NO_DELETE')
            return state
        # Cleanup is mandatory even when no native chain appeared before the
        # deadline. Product DELETE owns reconciliation; never leave this VM
        # accepted merely because a measurement could not be made.
        normal_delete(base, g, state, token)
    base['observe_cleanup'](g, state, token, 0)
    state['backend_result'] = measure_after_delete(base, state)
    publish(base, g, state, state['phase'])
    return state


def main():
    global GENERATION, STATE, PLAN, RECEIPT, NAME
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'execute', 'observe'))
    parser.add_argument('--execute-authorized', action='store_true')
    parser.add_argument('--plan-sha')
    parser.add_argument('--replay-original', action='store_true')
    parser.add_argument('--wait-seconds', type=int, choices=range(31), default=30)
    parser.add_argument('--case-generation', choices=('backend-v2', 'backend-v3'), default='backend-v2')
    args = parser.parse_args()
    GENERATION = args.case_generation
    if GENERATION == 'backend-v3':
        STATE = RUN / 'private/gov-candidate/import-delete-backend-v3.json'
        PLAN = RUN / 'private/gov-candidate/import-delete-backend-v3-plan.json'
        RECEIPT = RUN / 'evidence/gov-import-delete-backend-v3.json'
        NAME = 'vm-mvp-import-delete-backend-v3-01a168ce'
    require(os.environ.get('SSH_CONNECTION', '').split()[2:3] == ['172.16.101.31'], 'only ssh fedora may drive this task')
    require(args.action == 'execute' or not args.execute_authorized and not args.replay_original, 'mutation flags require execute')
    require(not args.replay_original, 'this task permits original-operation observation, never unknown-ACK replay')
    base = load_base()
    g = runpy.run_path(str(RUN / 'gov-candidate.py'))
    with Path('/home/chabking/workspace/ani-network-service-runs/net05a-heavy.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = run(base, g, args)
    print(json.dumps({'phase': state['phase'], 'plan_sha256': state['plan_sha256'], 'receipt': str(RECEIPT),
                      'private_plan_reference': str(PLAN), 'vm_id': state.get('vm_id'),
                      'create_send_attempts': state['create']['send_attempts'], 'delete_send_attempts': state['delete']['send_attempts']}))


if __name__ == '__main__':
    main()
