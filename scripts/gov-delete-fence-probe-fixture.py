#!/usr/bin/env python3
"""Pure task fixtures. PostgreSQL, HTTP, Redis, native and Ceph calls are doubles."""
import ast
import contextlib
import copy
import datetime
import io
import json
from pathlib import Path
import runpy
import tempfile
import uuid

ROOT = Path(__file__).parent
probe = runpy.run_path(str(ROOT / 'gov-delete-fence-probe.py'))
proof = runpy.run_path(str(ROOT / 'gov-delete-fence-predispatch.py'))['execute'].__globals__
vm, create, start = (str(uuid.uuid4()) for _ in range(3))


def reject(fn):
    try:
        fn()
    except (AssertionError, RuntimeError):
        return
    raise AssertionError('unsafe transition accepted')


state = {'create': {'body': {'data': {'name': 'vm-fence-01a168ce', 'startAfterCreate': False}},
          'send_attempts': 1, 'ack_unknown': True}, 'start': {}}
reject(lambda: probe['create_or_start']({'api': lambda *a, **k: (_ for _ in ()).throw(AssertionError('no API allowed'))}, state, 'fake', 'create', 0))
source = 'csi-vol-source-fixture'
family = [{'name': 'csi-vol-known', 'result': 'PRESENT', 'id': 'base123',
           'parent': {'pool': 'ani-block-pool', 'image': 'csi-vol-known-temp', 'id': 'temp123'}},
          {'name': 'csi-vol-known-temp', 'result': 'PRESENT', 'id': 'temp123',
           'parent': {'pool': 'ani-block-pool', 'image': source, 'id': 'source123'}}]
untouched = copy.deepcopy(family)
fresh = [{'name': x['name'], 'result': 'ABSENT'} for x in family]
assert probe['preserve_family'](family, fresh, source) == untouched and family == untouched
replacement = copy.deepcopy(family); replacement[1]['id'] = 'new456'
reject(lambda: probe['preserve_family'](family, replacement, source))
bad_parent = copy.deepcopy(family); bad_parent[0]['parent']['id'] = 'foreign456'
reject(lambda: probe['preserve_family'](family, bad_parent, source))
bad_parent = copy.deepcopy(family); bad_parent[1]['parent']['image'] = 'foreign-source'
reject(lambda: probe['preserve_family'](family, bad_parent, source))
with tempfile.TemporaryDirectory(prefix='vm-fence-fixture-') as directory:
    path = Path(directory)
    probe_env = probe['material'].__globals__
    probe_env['LIFECYCLE'] = path / 'lifecycle.json'
    actual = {'vm_id': vm, 'create_operation_id': create, 'create': state['create'],
        'start': {'operation': {'state': 'succeeded'}, 'operation_id': start, 'send_attempts': 1,
                  'body': {'data': {'expectedVersion': '1', 'idempotencyKey': str(uuid.uuid4())}}},
        'plan': {'synthetic_disk_id_for_denied_routes': str(uuid.uuid4())}}
    material = probe['material']({}, actual)
    assert [x['label'] for x in material['old_commands']] == ['start']
    assert material['old_commands'][0]['operation_id'] == start
    assert material['create_body'] == state['create']['body'] and 'guestInitialization' not in material['create_body']['data']
    (path / 'seed-receipt.json').write_text(json.dumps({'tenant_id': 1, 'resource_tenant_id': str(uuid.uuid4())}))
    acl, acl_receipt, result_path = path / 'acl.json', path / 'acl-result.json', path / 'fence-result.json'
    timeline = []
    def save(p, value):
        p.write_text(json.dumps(value))
    def acl_call(action, fd):
        timeline.append(action)
        if action == 'pause':
            save(acl, {'phase': 'paused'})
            raise TimeoutError('simulated interruption after durable ACL intent')
        assert action == 'restore'
        save(acl, {'phase': 'restored'})
        save(acl_receipt, {'original': {'acl': 'before'}, 'current': {'acl': 'before'}})
        return {'phase': 'restored'}
    current = {'id': vm, 'version': '2', 'observedState': 'Running', 'closing': False}
    ticket = {'sessionId': str(uuid.uuid4()), 'wsUrl': 'ws://fixture.invalid/no-stream',
              'expiresAt': (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=60)).isoformat()}
    def request(gov, access, method, route, body=None, deadline=None):
        if method == 'GET':
            return 200, current
        assert method == 'POST' and route.endswith('/console-sessions'), 'no DELETE may occur after the injected interruption'
        return 202, ticket
    proof.update(VM=vm, CREATE=create, BASE='/api/v1/virtual-machines/' + vm, ACL_JOURNAL=acl,
        ACL_RECEIPT=acl_receipt, LIVE_RECEIPT=result_path, target_material=lambda gov: material,
        request=request, acl_call=acl_call, sg_keys=lambda *a: {'issued_session': {'state': 'issued'}},
        identities=lambda *a: {'gov': [{'operation_id': create}], 'resource': {
            'operations': [{'state': 'succeeded'}, {'state': 'succeeded'}],
            'vm': {'id': vm, 'version': 2, 'closing': False}}})
    gov = {'PRIVATE': path, 'save': save, 'credentials': lambda: {'tenant_code': 'fixture', 'tenant_username': 'fixture', 'tenant_password': 'fixture'},
           'api': lambda *a, **k: {'access_token': 'fixture'}}
    target = {'phase': 'PREPARED_NO_HTTP', 'delete_key': str(uuid.uuid4()), 'console_key': str(uuid.uuid4()), 'lock_fd': 100}
    with contextlib.redirect_stdout(io.StringIO()):
        reject(lambda: proof['execute'](gov, path / 'state.json', target))
    result = json.loads(result_path.read_text())
    assert timeline == ['pause', 'restore'] and result['exact_acl_restored'] is True and result['pass'] is False
    assert target['phase'] == 'RESTORED_NEEDS_INSPECTION' and result.get('delete_operation_id') is None
    assert target['console']['http_status'] == 202 and target['console']['private_ack'] == ticket
    acl.unlink(); acl_receipt.unlink(); timeline.clear()
    wrong = {'phase': 'PREPARED_NO_HTTP', 'delete_key': str(uuid.uuid4()), 'console_key': str(uuid.uuid4()), 'lock_fd': 100}
    proof['request'] = lambda gov, access, method, route, body=None, deadline=None: (200, current if method == 'GET' else ticket)
    reject(lambda: proof['execute'](gov, path / 'wrong-status.json', wrong))
    assert wrong['phase'] == 'CONSOLE_ISSUE_PENDING' and wrong['console']['http_status'] == 200
    assert wrong['console']['private_ack'] == ticket and timeline == [] and not acl.exists()


def ddl_strings(path):
    tree = ast.parse(path.read_text())
    return [ast.dump(node.args[1], include_attributes=False) for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'sql' and len(node.args) > 1
        and isinstance(node.args[1], ast.JoinedStr) and any(isinstance(x, ast.Constant) and str(x.value).startswith('BEGIN;') for x in node.args[1].values)]
assert ddl_strings(ROOT / 'vm-gov-delete-fence-pause-inherited.py') == ddl_strings(ROOT / 'vm-gov-delete-fence-probe-pause-inherited.py')
print(json.dumps({'result': 'PASS', 'actual_http_native_pg_redis_ceph_calls': 0,
    'checks': ['unknown-ACK-no-automatic-POST', 'only-actual-Start-replay', 'no-guest-initialization',
               'interruption-after-pause-restores-exact-ACL-first', 'no-DELETE-after-interruption', 'derived-ACL-DDL-AST-identical',
               'fresh-ABSENT-keeps-first-image-ID', 'same-name-new-ID-rejected', 'foreign-family-parent-rejected', 'contract202-ACK-persisted-before-assertion', 'wrong200-rejected-with-ACK-kept-no-ACL']}))
