#!/usr/bin/env python3
"""Pure fixtures for the single additional import-delete observer; no API calls."""
import copy
import json
from pathlib import Path
import runpy
import subprocess
import tempfile
import types
import uuid
from unittest import mock

d = runpy.run_path(str(Path(__file__).with_name('vm-product-import-delete-backend.py')))
uid = lambda: str(uuid.uuid4())
target_uid, temp_uid, reservation_uid = uid(), uid(), uid()
source = {'namespace': 'source-ns', 'pvc_name': 'protected-source'}
base = {'ref': lambda v: {'kind': v['kind'], **{k: v['metadata'].get(k, '') for k in ('namespace', 'name', 'uid')}}}
target = {'kind': 'PersistentVolumeClaim', 'metadata': {'namespace': 'tenant-ns', 'name': 'target', 'uid': target_uid}, 'spec': {}}
temp = {'kind': 'PersistentVolumeClaim', 'metadata': {'namespace': 'source-ns', 'name': 'tmp-pvc-' + target_uid,
    'uid': temp_uid, 'labels': {d['OWNED']: target_uid},
    'annotations': {'cdi.kubevirt.io/storage.clone.token': 'CLONE_AUTH_SENTINEL',
                    'cdi.kubevirt.io/storage.extended.clone.token': 'EXTENDED_AUTH_SENTINEL'}},
    'spec': {'storageClassName': 'ani-block', 'volumeMode': 'Block', 'accessModes': ['ReadWriteOnce'],
             'dataSourceRef': {'kind': 'PersistentVolumeClaim', 'name': 'protected-source'}}}
pv = {'kind': 'PersistentVolume', 'metadata': {'name': 'pv-fixture', 'uid': uid()},
      'spec': {'claimRef': {'uid': temp_uid, 'namespace': 'source-ns', 'name': temp['metadata']['name']},
               'csi': {'driver': d['DRIVER'], 'volumeHandle': '0001-0009-rook-ceph-' + d['POOL_ID'] + '-' + reservation_uid,
                       'volumeAttributes': {'pool': d['POOL'], 'clusterID': 'rook-ceph'}}}}


def reject(fn):
    try:
        fn()
    except (RuntimeError, ValueError):
        return
    raise AssertionError('invalid identity accepted')


before = copy.deepcopy(temp)
claims = d['related_claims'](base, target, [temp], source)
assert [v['metadata']['uid'] for v in claims] == [target_uid, temp_uid]
projected = d['project'](temp, base)
assert 'SENTINEL' not in json.dumps(projected) and temp == before
bad = copy.deepcopy(temp)
bad['metadata']['labels'][d['OWNED']] = uid()
reject(lambda: d['related_claims'](base, target, [bad], source))
bad = copy.deepcopy(temp)
bad['spec']['dataSourceRef']['name'] = 'foreign-source'
reject(lambda: d['related_claims'](base, target, [bad], source))
known = {v['metadata']['uid']: v for v in claims}
assert d['pv_projection'](base, temp, pv, known)['reservation_uuid'] == reservation_uid
bad = copy.deepcopy(pv)
bad['spec']['claimRef']['uid'] = uid()
reject(lambda: d['pv_projection'](base, temp, bad, known))
bad = copy.deepcopy(pv)
bad['spec']['claimRef']['name'] = 'foreign-same-uid'
reject(lambda: d['pv_projection'](base, temp, bad, known))
request, image = 'pvc-' + temp_uid, 'csi-vol-' + reservation_uid
for encoded in (reservation_uid, d['POOL_ID'] + '/' + reservation_uid):
    assert d['reservation'](request, encoded, request, image)['reverse_request_verified'] is True
reject(lambda: d['reservation'](request, reservation_uid, 'pvc-' + uid(), image))
reject(lambda: d['reservation'](request, '0000000000000005/' + reservation_uid, request, image))
reject(lambda: d['reservation'](request, reservation_uid, request, 'csi-vol-' + uid()))
# A name or a source child alone never passes the evidence predicate. A final
# ready read invalidates any earlier unready allocation observation.
def original_freeze(state, latest):
    state['native_before_delete'] = latest
    state['unready_window'] = 'MISSED' if latest['dv_phase'] == 'Succeeded' else 'UNREADY'
state = {}
b = {'freeze_before_delete': original_freeze, 'PREPARING_PHASES': {'CSICloneInProgress'}}
d['freeze'](state, {'dv_phase': 'CSICloneInProgress', 'backend': {'measurable': False, 'related_claims': [projected]}}, b)
assert not state.get('backend_before_delete')
d['freeze'](state, {'dv_phase': 'CSICloneInProgress', 'backend': {'measurable': True, 'related_claims': [projected], 'physical_images': [{'id': '123'}]}}, b)
assert state['backend_before_delete']['physical_images'][0]['id'] == '123'
latest = {'dv_phase': 'Succeeded', 'backend': {'measurable': True, 'related_claims': [projected]}}
d['freeze_final'](state, latest, b)
assert state['backend_before_delete'] is None and state['unready_window'] == 'MISSED'
d['freeze_final'](state, {'dv_phase': 'CSICloneInProgress', 'backend': {'measurable': False, 'related_claims': [projected]}}, b)
assert state['backend_before_delete'] is None
# Every native subprocess consumes the same absolute budget. The fourth gets
# only six remaining seconds; the fifth must not be started at all. Restore the
# ordinary native adapter afterward so normal DELETE/GC is never fenced out.
env = d['capture_window'].__globals__
clock, timeouts = [100.0], []
def native_run(argv, **kwargs):
    timeouts.append(kwargs['timeout'])
    clock[0] += kwargs['timeout']
    return types.SimpleNamespace(stdout=b'{}')
original_native = lambda *a, **k: None
bounded = {'K': ['fixture-kubectl'], 'native': original_native}
with mock.patch.object(env['time'], 'monotonic', lambda: clock[0]), mock.patch.object(env['subprocess'], 'run', native_run):
    with d['bounded_reads'](bounded, 130.0):
        for _ in range(4):
            bounded['native']('pvc', 'owned', 'tenant-ns')
        reject(lambda: bounded['native']('pvc', 'must-not-start', 'tenant-ns'))
assert timeouts == [8, 8, 8, 6] and bounded['native'] is original_native and '_capture_deadline' not in bounded
# The Ceph command is also limited by remaining time, after its reader lookup.
reader = {'metadata': {'uid': d['READER']['uid']}, 'status': {'phase': 'Running'}}
with mock.patch.object(env['time'], 'monotonic', lambda: 129.0), mock.patch.object(env['subprocess'], 'run', return_value=types.SimpleNamespace(returncode=0, stdout=b'{}')) as run:
    assert d['ceph']({'K': ['fixture-kubectl'], 'native': lambda *a: reader, '_capture_deadline': 130.0}, ['rbd', 'info', 'exact-image']) == b'{}'
    assert run.call_args.kwargs['timeout'] == 1
# Match the exact observed rados stdout miss, including its original request
# pool/object/key. Foreign values and unrelated/permission failures still fail.
journal_args = ['rados', '-p', d['POOL'], 'getomapval', 'csi.volumes.default', 'csi.volume.pvc-' + target_uid, '-']
missing = ('No such key: ' + d['POOL'] + '/csi.volumes.default/csi.volume.pvc-' + target_uid + '\n').encode()
miss = types.SimpleNamespace(returncode=1, stdout=missing, stderr=b'command terminated with exit code 1\n')
assert d['exact_stdout_journal_miss'](miss, journal_args)
assert not d['exact_stdout_journal_miss'](miss, journal_args[:-2] + ['csi.volume.pvc-' + uid(), '-'])
assert not d['exact_stdout_journal_miss'](types.SimpleNamespace(returncode=1, stdout=b'arbitrary ' + missing, stderr=b''), journal_args)
assert not d['exact_stdout_journal_miss'](types.SimpleNamespace(returncode=1, stdout=missing, stderr=b'Permission denied'), journal_args)
assert not d['exact_stdout_journal_miss'](miss, ['rbd', 'info', 'foreign'])
with mock.patch.object(env['subprocess'], 'run', return_value=miss):
    assert d['ceph']({'K': ['fixture-kubectl'], 'native': lambda *a: reader}, journal_args) is None
with mock.patch.object(env['subprocess'], 'run', return_value=types.SimpleNamespace(returncode=1, stdout=b'', stderr=b'No such file or directory')):
    assert d['ceph']({'K': ['fixture-kubectl'], 'native': lambda *a: reader}, ['rbd', 'info', 'exact']) is None
# A legal missing journal is not terminal. The real deadline loop continues to
# the next poll, which can capture a real attribution before the same deadline.
clock, polls = [100.0], []
poll_base = {'K': ['fixture-kubectl'], 'native': original_native,
             'freeze_before_delete': original_freeze, 'PREPARING_PHASES': {'CSICloneInProgress'}}
def journal_run(argv, **kwargs):
    if 'get' in argv:
        return types.SimpleNamespace(stdout=json.dumps(reader).encode())
    return miss
def next_capture(base, g, current):
    polls.append(len(polls))
    measured = len(polls) > 1
    if not measured:
        assert env['omap'](base, 'csi.volumes.default', 'csi.volume.pvc-' + target_uid) is None
    return {'vm': {'uid': 'vm'}, 'dv': {'uid': 'dv'}, 'pvc': {'uid': target_uid}, 'dv_phase': 'CSICloneInProgress',
            'backend': {'measurable': measured, 'related_claims': [], 'physical_images': [{'id': 'actual-fixture-id'}] if measured else []}}
with mock.patch.dict(env, {'capture_backend': next_capture}), mock.patch.object(env['time'], 'monotonic', lambda: clock[0]), mock.patch.object(env['time'], 'sleep', lambda seconds: clock.__setitem__(0, clock[0] + seconds)), mock.patch.object(env['subprocess'], 'run', journal_run):
    polled = {}; d['capture_window'](poll_base, {'save': lambda *a: None}, polled, 30)
assert len(polls) == 2 and polled['capture_window']['result'] == 'IDENTITY_CAPTURED' and polled['capture_window']['elapsed_seconds'] == .25
# Exercise the real run() branch: a timed-out capture with no VM/DV/PVC identity
# must still call the original normal product DELETE exactly once. No backend
# rescan is allowed in the final fence, and no NOT_MEASURED result becomes PASS.
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory); selection = {'source': {}, 'network': {}}
    plan = {'selection': selection}; plan_sha = 'fixed-fixture-plan'
    prepared = {'plan': plan, 'plan_sha256': plan_sha, 'vm_id': uid(), 'create_operation_id': uid(),
                'create': {'send_attempts': 1}, 'delete': {'outcome': 'not_sent', 'send_attempts': 0}}
    (root / 'state').write_text(json.dumps(prepared)); (root / 'plan').write_text(json.dumps(plan))
    deletes, final_reads, published = [], [], []
    fake = {'K': ['fixture-kubectl'], 'native': original_native, 'capture': lambda *a: (_ for _ in ()).throw(AssertionError('slow backend rescan')),
            'freeze_before_delete': original_freeze, 'PREPARING_PHASES': {'CSICloneInProgress'},
            'login': lambda g: ('fixture-token', {}), 'digest': lambda p: plan_sha, 'preflight': lambda *a: selection,
            'observe_cleanup': lambda g, s, token, seconds: s.update(phase='NORMAL_DELETE_OBSERVED')}
    def accept(g, s, token, delete=False, replay=False):
        if not delete: return  # already acknowledged original CREATE
        assert replay is False
        deletes.append(s['vm_id'])
        latest = fake['capture'](g, s); fake['freeze_before_delete'](s, latest)
        s['delete'].update(operation_id=uid(), send_attempts=1)
    fake['accept'] = accept
    def fresh_owner(base, s):
        final_reads.append(s['vm_id'])
        return {'dv_phase': None, 'final_owner_chain_verified': False}
    def timed_out(*a):
        raise subprocess.TimeoutExpired('fixture-native', .1)
    args = types.SimpleNamespace(action='execute', execute_authorized=True, plan_sha=plan_sha, replay_original=False, wait_seconds=30)
    changes = {'STATE': root / 'state', 'PLAN': root / 'plan', 'capture_backend': timed_out, 'final_owner_read': fresh_owner,
               'publish': lambda base, g, s, phase: published.append((phase, s.get('backend_result')))}
    with mock.patch.dict(env, changes):
        observed = d['run'](fake, {'save': lambda *a: None}, args)
    assert len(deletes) == len(final_reads) == 1 and observed['delete']['send_attempts'] == 1
    assert observed['capture_window']['result'] == 'TIMED_OUT_NOT_MEASURED'
    assert observed['backend_result']['result'] == 'NOT_MEASURED' and published[-1][0] == 'NORMAL_DELETE_OBSERVED'
# A final owner/phase check preserves an already complete attribution while CDI
# is still unready, but a final Ready or unverified chain invalidates that claim.
state = {'backend_before_delete': {'physical_images': [{'id': 'known'}]}}
d['freeze_final'](state, {'dv_phase': 'CSICloneInProgress', 'final_owner_chain_verified': True}, b)
assert state['backend_before_delete']['physical_images'][0]['id'] == 'known'
d['freeze_final'](state, {'dv_phase': 'Succeeded', 'final_owner_chain_verified': True}, b)
assert state['backend_before_delete'] is None
print(json.dumps({'result': 'PASS', 'api_calls': 0, 'native_calls': 0, 'ceph_calls': 0,
                  'checks': ['target-UID-owned-cross-namespace-clone', 'foreign-source-rejected',
                    'PV-claim-UID-and-name-namespace', 'bidirectional-reservation', 'foreign-pool-rejected',
                    'missing-handle-not-measured', 'final-ready-not-unready', 'public-no-clone-auth-private-unchanged',
                    'shared-absolute-native-deadline', 'ceph-remaining-time-bound', 'capture-timeout-still-normal-delete',
                    'final-owner-read-no-backend-rescan', 'stdout-missing-exact-pool-object-key',
                    'stdout-foreign-or-permission-not-absent', 'stdout-missing-keeps-remaining-window-polling']}))
