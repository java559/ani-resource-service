"""Task-only V3 final DELETE fence proof. prepare/help never pause or send HTTP.

Execute only after Root's explicit final-Delete window. This driver owns the
global heavy lock itself; do not wrap execute in a second flock. The original
ACL helper's SQL/guards remain unchanged in its inherited-lock copy. Its exact
ACL restore always runs in finally before continuation or any result is emitted.
"""
from pathlib import Path
import argparse
import contextlib
import datetime
import fcntl
import hashlib
import io
import json
import os
import runpy
import signal
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

RUN = Path('/home/chabking/workspace/ani-resource-service-runs/vm-mvp-20261023-01a168ce')
LOCK = Path('/home/chabking/workspace/ani-network-service-runs/net05a-heavy.lock')
VM = '55099440-c47b-4f81-afc5-7d8e740d14dc'
CREATE = 'f14d60ba-54f2-458d-8e59-458b9d90404c'
ORIGINAL_ACL_SHA = 'fdbe348407af3d840f926ddac1b2bf5db2ac7e83b1d63fc67b5aec74e437ea1d'
INHERITED_ACL_SHA = '41e0d1dbf18cf1e5ae91902a4471f8e1ed4ea67c24d9d7cee0f9830644e5706f'
BASE = '/api/v1/virtual-machines/' + VM
ACL_INHERITED_PATH = RUN / 'vm-gov-delete-fence-pause-inherited.py'
ACL_JOURNAL = RUN / 'private/gov-candidate/delete-fence-acl.json'
ACL_RECEIPT = RUN / 'evidence/gov-delete-fence-acl.json'
LIVE_RECEIPT = RUN / 'evidence/gov-delete-fence-predispatch-live-v3.json'
CASES = [
    ('start', 'POST', '/start', {}), ('stop', 'POST', '/stop', {}),
    ('restart', 'POST', '/restart', {}), ('force-stop', 'POST', '/force-stop', {'destructiveConfirmation': True}),
    ('create-disk', 'POST', '/disks', {'name': 'pre-dispatch-fence-probe', 'sizeGb': '1'}),
    ('attach-disk', 'POST', '/disks/{disk}/attach', {}),
    ('detach-disk', 'POST', '/disks/{disk}/detach', {'guestPreparedConfirmation': True}),
    ('delete-disk', 'DELETE', '/disks/{disk}', {}),
    ('set-media', 'PUT', '/media/INSTALL', {'mediaKey': 'ubuntu-24.04-install'}),
    ('eject-media', 'DELETE', '/media/INSTALL', {}),
    ('set-boot', 'PUT', '/boot', {'bootDevice': 'SYSTEM_DISK'}),
    ('create-snapshot', 'POST', '/snapshots', {'name': 'pre-dispatch-fence-probe'}),
    ('delete-snapshot', 'DELETE', '/snapshots/{snapshot}', {}),
    ('restore-snapshot', 'POST', '/snapshots/{snapshot}/restore', {'previewDigest': 'a' * 64, 'destructiveConfirmation': True}),
    ('console', 'POST', '/console-sessions', {}),
]


def setup():
    gov = runpy.run_path(str(RUN / 'gov-candidate.py'))
    return gov, gov['PRIVATE'] / 'linux-delete-predispatch-v3.json'


def prepare(gov, path):
    origin = json.loads((gov['PRIVATE'] / 'linux-vm-v3.json').read_text())
    assert origin['vm_id'] == VM and origin['operation_id'] == CREATE
    if path.exists():
        state = json.loads(path.read_text())
    else:
        state = {'vm_id': VM, 'create_operation_id': CREATE, 'phase': 'PREPARED_NO_HTTP',
                 'delete_key': str(uuid.uuid4()), 'snapshot_id': str(uuid.uuid4()),
                 'negative_keys': {action: str(uuid.uuid4()) for action, *_ in CASES},
                 'delete_send_attempts': 0, 'negative_requests_sent': 0}
        gov['save'](path, state)
    assert state['vm_id'] == VM and state['create_operation_id'] == CREATE
    if 'console_key' not in state:
        assert state['phase'] == 'PREPARED_NO_HTTP'
        state['console_key'] = str(uuid.uuid4())
        gov['save'](path, state)
    return state


def request(gov, access, method, route, body=None, deadline=None):
    remaining = 8 if deadline is None else min(8, deadline - time.monotonic())
    assert remaining > 0, 'fence window deadline reached'
    headers = {'Content-Type': 'application/json', 'Origin': gov['ORIGIN'], 'Authorization': 'Bearer ' + access}
    req = urllib.request.Request(gov['HTTP'] + route, method=method, headers=headers,
        data=None if body is None else json.dumps(body).encode())
    try:
        with urllib.request.urlopen(req, timeout=remaining) as response:
            raw = response.read()
            value = json.loads(raw) if raw else {}
            return response.status, value.get('data', value) if isinstance(value, dict) else value
    except urllib.error.HTTPError as error:
        try:
            value = json.loads(error.read())
        except Exception:
            value = {}
        return error.code, {'reason': value.get('reason', 'unknown')}


def identities(gov, seed):
    resource = urllib.parse.urlsplit((RUN / 'private/resource-candidate/owner.dsn').read_text().strip())
    assert resource.path == '/vm_resource_candidate_01a168ce'
    sql = gov['psql']
    tenant = seed['resource_tenant_id']
    rows = json.loads(sql(gov['OWNER'], "SELECT COALESCE(json_agg(json_build_object('operation_id',operation_id,'create_operation_id',create_operation_id,'action',action,'dispatch_state',dispatch_state,'attempt_count',attempt_count,'lease_generation',lease_generation) ORDER BY operation_id),'[]'::json) FROM sys_quota_operations WHERE tenant_id=" + str(int(seed['tenant_id'])) + " AND resource_id='" + VM + "';"))
    owner = json.loads(sql(urllib.parse.unquote(resource.username), "SELECT json_build_object('operations',COALESCE((SELECT json_agg(json_build_object('operation_id',id,'action',action,'state',state) ORDER BY id) FROM vm.operations WHERE tenant_id='" + tenant + "' AND vm_id='" + VM + "'),'[]'::json),'vm',(SELECT json_build_object('id',id,'version',version,'closing',closing) FROM vm.virtual_machines WHERE tenant_id='" + tenant + "' AND id='" + VM + "'));", database=resource.path[1:]))
    return {'gov': rows, 'resource': owner}


def sg_keys(gov, session_id=None):
    config = json.loads((gov['PRIVATE'] / 'redis.json').read_text())
    host, port = config['address'].rsplit(':', 1)
    with socket.create_connection((host, int(port)), timeout=3) as connection:
        stream = connection.makefile('rb')

        def read():
            line = stream.readline()
            kind, value = line[:1], line[1:-2]
            if kind == b'+': return value
            if kind == b'-': raise RuntimeError('private SG Redis read failed')
            if kind == b':': return int(value)
            if kind == b'$':
                size = int(value)
                if size < 0: return None
                data = stream.read(size)
                assert stream.read(2) == b'\r\n'
                return data
            if kind == b'*': return [read() for _ in range(int(value))]
            raise RuntimeError('invalid private Redis response')

        def call(*parts):
            encoded = [str(part).encode() for part in parts]
            connection.sendall(b'*' + str(len(encoded)).encode() + b'\r\n' + b''.join(
                b'$' + str(len(part)).encode() + b'\r\n' + part + b'\r\n' for part in encoded))
            return read()

        assert call('AUTH', config['password']) == b'OK' and call('SELECT', 1) == b'OK'
        cursor, keys = b'0', set()
        while True:
            cursor, batch = call('SCAN', cursor.decode(), 'COUNT', 100)
            keys.update(batch)
            if cursor == b'0': break
        sessions = {key for key in keys if key.startswith(b'{ani-session-gateway}:session:')
            and call('HGET', key.decode(), 'instance_id') == VM.encode()}
        digest = lambda values: sorted(hashlib.sha256(value).hexdigest() for value in values)
        result = {'all_key_hashes': digest(keys), 'vm_session_key_hashes': digest(sessions)}
        if session_id:
            key = '{ani-session-gateway}:session:' + session_id
            assert call('HGET', key, 'instance_id') == VM.encode()
            result['issued_session'] = {'id': session_id,
                'state': call('HGET', key, 'state').decode(),
                'ticket_expires_at_unix_ms': int(call('HGET', key, 'ticket_expires_at')),
                'ticket_ciphertext_sha256': hashlib.sha256(call('HGET', key, 'ticket_ciphertext')).hexdigest()}
        return result


def issue_metrics(deadline=None):
    timeout = 5 if deadline is None else min(5, deadline - time.monotonic())
    assert timeout > 0
    with urllib.request.urlopen('http://127.0.0.1:39185/metrics', timeout=timeout) as response:
        samples = sorted(line for line in response.read().decode().splitlines() if line.startswith('ani_session_create_total{'))
        assert any('mode="vnc"' in line for line in samples), 'pre-issued VNC ticket must have a visible Issue metric sample'
        return samples


def resource_log_marks():
    result = {}
    for path in sorted((RUN / 'private/resource-candidate').glob('server*.log')):
        value = path.stat()
        result[str(path)] = {'device': value.st_dev, 'inode': value.st_ino, 'size': value.st_size}
    assert result, 'Resource request log is required to prove no new Resolve'
    return result


def no_new_resolve(marks):
    assert set(resource_log_marks()) == set(marks), 'Resource log set changed during replay'
    count = 0
    for name, mark in marks.items():
        path = Path(name)
        value = path.stat()
        assert value.st_dev == mark['device'] and value.st_ino == mark['inode'] and value.st_size >= mark['size']
        with path.open('rb') as source:
            source.seek(mark['size'])
            count += source.read().count(b'ResolveConsoleTarget')
    assert count == 0, 'console replay unexpectedly called ResolveConsoleTarget'
    return {'resolve_console_target_log_records': count, 'request_log_files_checked': len(marks)}


def acl_call(action, lock_fd):
    original = RUN / 'vm-gov-delete-fence-pause.py'
    inherited = ACL_INHERITED_PATH
    assert hashlib.sha256(original.read_bytes()).hexdigest() == ORIGINAL_ACL_SHA
    assert hashlib.sha256(inherited.read_bytes()).hexdigest() == INHERITED_ACL_SHA
    previous_argv, previous_fd = sys.argv, os.environ.get('ANI_VM_TEST_HEAVY_LOCK_FD')
    captured = io.StringIO()
    try:
        sys.argv = [str(inherited), action]
        os.environ['ANI_VM_TEST_HEAVY_LOCK_FD'] = str(lock_fd)
        with contextlib.redirect_stdout(captured):
            runpy.run_path(str(inherited), run_name='__main__')
    finally:
        sys.argv = previous_argv
        if previous_fd is None: os.environ.pop('ANI_VM_TEST_HEAVY_LOCK_FD', None)
        else: os.environ['ANI_VM_TEST_HEAVY_LOCK_FD'] = previous_fd
    return json.loads(captured.getvalue().splitlines()[-1])


def target_material(gov):
    """The default V3 target; a fixed task probe can supply its own material."""
    origin = json.loads((gov['PRIVATE'] / 'linux-vm-v3.json').read_text())
    assert origin['vm_id'] == VM and origin['operation_id'] == CREATE
    lifecycle_path = gov['PRIVATE'] / 'linux-lifecycle-v3.json'
    lifecycle = json.loads(lifecycle_path.read_text())
    assert lifecycle['vm_id'] == VM and lifecycle['create_operation_id'] == CREATE and 'delete' not in lifecycle['commands']
    old_commands = []
    for label in ('stop', 'start'):
        old = lifecycle['commands'][label]
        assert old.get('operation_id') and old['action'] == label
        old_commands.append({'label': label, 'method': 'POST', 'route': BASE + '/' + label,
                             'body': old['body'], 'operation_id': old['operation_id']})
    disks = json.loads((gov['PRIVATE'] / 'linux-disks-v3.json').read_text())
    old = disks['commands']['attach-data3']
    old_commands.append({'label': 'attach-data3', 'method': old['method'], 'route': old['route'],
                         'body': old['body'], 'operation_id': old['operation_id']})
    guestpath = Path(origin['credential_reference'])
    assert hashlib.sha256(guestpath.read_bytes()).hexdigest() == origin['credential_file_sha256']
    create_body = json.loads(json.dumps(origin['request_body_without_password']))
    create_body['data']['guestInitialization']['password'] = json.loads(guestpath.read_text())['password']
    return {'lifecycle_path': lifecycle_path, 'lifecycle': lifecycle, 'old_commands': old_commands,
            'disk': disks['plans']['data3']['disk_id'], 'create_body': create_body}


def execute(gov, path, state):
    assert state['phase'] == 'PREPARED_NO_HTTP', 'inspect the original run; never re-pause or replace an intent'
    assert not ACL_JOURNAL.exists(), 'prior ACL journal exists; recover it first'
    seed = json.loads((gov['PRIVATE'] / 'seed-receipt.json').read_text())
    material = target_material(gov)
    creds = gov['credentials']()
    access = gov['api']('POST', '/api/v1/auth/password/login', {'tenant_name': creds['tenant_code'],
        'username': creds['tenant_username'], 'password': creds['tenant_password']})['access_token']
    status, current = request(gov, access, 'GET', BASE)
    assert status == 200 and current['id'] == VM and not current['closing']
    assert current['observedState'] in ('Running', 'Stopped') and int(current['version']) > 0
    before = identities(gov, seed)
    assert {row['operation_id'] for row in before['gov']} == {CREATE}
    assert all(row['state'] in ('succeeded', 'failed') for row in before['resource']['operations'])
    assert before['resource']['vm'] == {'id': VM, 'version': int(current['version']), 'closing': False}
    version = int(current['version'])
    if 'prepared_expected_version' in state:
        assert version == state['prepared_expected_version'], 'prepared console intent version changed'
    body = {'data': {'idempotencyKey': state['delete_key'], 'expectedVersion': str(version)}}
    lifecycle_path, lifecycle = material['lifecycle_path'], material['lifecycle']
    command = {'action': 'delete', 'idempotency_key': state['delete_key'], 'expected_version': version,
               'body': body, 'before': current, 'send_attempts': 0, 'ack_unknown': False}
    lifecycle['commands']['delete'] = command
    old_commands, disk = material['old_commands'], material['disk']
    console_body = {'data': {'idempotencyKey': state['console_key'], 'expectedVersion': str(version)}}
    state.update(phase='CONSOLE_ISSUE_PENDING', console={'body': console_body, 'send_attempts': 1})
    gov['save'](path, state)
    status, ticket = request(gov, access, 'POST', BASE + '/console-sessions', console_body)
    state['console'].update(http_status=status, private_ack=ticket,
        ack_received_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
    gov['save'](path, state)  # Persist the wire ACK privately before any assertion.
    assert status == 202 and ticket.get('sessionId') and ticket.get('wsUrl') and ticket.get('expiresAt')
    expires = datetime.datetime.fromisoformat(ticket['expiresAt'].replace('Z', '+00:00')).timestamp()
    assert expires > time.time() + 10, 'original ticket must remain valid through immediate post-DELETE replay'
    state['console'].update(session=ticket, websocket_opened=False)
    gov['save'](path, state)
    # All no-new-session deltas use the intentionally pre-issued ticket baseline.
    sg_before = sg_keys(gov, ticket['sessionId'])
    assert sg_before['issued_session']['state'] == 'issued'
    state.update(phase='EXECUTION_PREPARED', expected_version=version, before=before,
                 sg_before=sg_before, old_replays=old_commands, delete_body=body)
    gov['save'](lifecycle_path, lifecycle)
    gov['save'](path, state)
    result = {'scope': 'LIVE_GOV_DURABLE_DELETE_BEFORE_OWNER_DISPATCH', 'vm_id': VM,
              'create_operation_id': CREATE, 'expected_version': version, 'before': before,
              'new_intents': [], 'old_replays': [], 'queries': [], 'pass': False,
              'business_sql_writes': False, 'acl_restore': 'NOT_YET_NEEDED'}
    receipt = LIVE_RECEIPT
    error = None
    started = time.monotonic()
    deadline = started + 55

    def interrupted(signum, frame):
        raise TimeoutError('bounded fence window interrupted; ACL restoration has priority')

    previous_handlers = {number: signal.signal(number, interrupted) for number in (signal.SIGALRM, signal.SIGTERM, signal.SIGINT)}
    signal.setitimer(signal.ITIMER_REAL, 55)
    try:
        result['acl_pause'] = acl_call('pause', state['lock_fd'])
        state['phase'] = 'ACL_PAUSED'
        command.update(send_attempts=1, ack_unknown=True)
        state['delete_send_attempts'] = 1
        gov['save'](lifecycle_path, lifecycle)
        gov['save'](path, state)  # Durable first key/version/body before the only first admission.
        status, accepted = request(gov, access, 'DELETE', BASE, body, deadline)
        assert status == 202 and accepted['resourceId'] == VM and accepted.get('replayed', False) is False
        delete_id = accepted['operationId']
        assert str(uuid.UUID(delete_id)) == delete_id
        command.update(operation_id=delete_id, accepted=accepted, ack_unknown=False)
        state.update(delete_operation_id=delete_id, phase='DELETE_ACCEPTED_PAUSED')
        result['delete_operation_id'] = delete_id
        gov['save'](lifecycle_path, lifecycle)
        gov['save'](path, state)
        # Replay first while the original ticket is still issued and unexpired.
        assert expires > time.time(), 'ticket expired before replay; do not claim expired replay is allowed'
        metrics_before = issue_metrics(deadline)
        log_marks = resource_log_marks()
        status, replayed_ticket = request(gov, access, 'POST', BASE + '/console-sessions', console_body, deadline)
        state['console'].update(replay_http_status=status, private_replay_ack=replayed_ticket)
        gov['save'](path, state)
        assert status == 202 and replayed_ticket == ticket, 'correct console replay must retain session, ticket URL and expiry'
        assert expires > time.time(), 'ticket expired during replay; validity proof is inconclusive'
        metrics_after = issue_metrics(deadline)
        assert metrics_after == metrics_before, 'console replay entered SG Issue/CreateSession'
        sg_replayed = sg_keys(gov, ticket['sessionId'])
        assert sg_replayed['issued_session'] == sg_before['issued_session'], 'console ticket was claimed/reissued/changed'
        result['console_replay'] = {'status': status, 'session_id': ticket['sessionId'],
            'expires_at': ticket['expiresAt'], 'same_ticket_url': True, 'same_expiry': True,
            'issued_unclaimed_before_and_after': True, 'unexpired_at_replay': True,
            'sg_issue_metrics_before': metrics_before, 'sg_issue_metrics_after': metrics_after,
            'no_new_issue': True, 'no_new_resolve': no_new_resolve(log_marks), 'websocket_opened': False}
        time.sleep(0.5)  # Existing outbox poll can try and fail its denied claim.

        def prove_predispatch():
            observed = identities(gov, seed)
            delete = [row for row in observed['gov'] if row['operation_id'] == delete_id]
            assert len(delete) == 1 and delete[0]['action'] == '/vm.v1.VMService/AcceptDelete'
            assert delete[0]['create_operation_id'] == CREATE and delete[0]['dispatch_state'] == 'QUEUED'
            assert delete[0]['attempt_count'] == delete[0]['lease_generation'] == 0
            assert {row['operation_id'] for row in observed['gov']} == {CREATE, delete_id}
            assert observed['resource'] == before['resource'], 'owner changed while DELETE must remain undispatched'
            return observed

        result['paused_before_requests'] = prove_predispatch()
        for action, method, suffix, extra in CASES:
            data = dict(idempotencyKey=state['negative_keys'][action], expectedVersion=str(version), **extra)
            route = BASE + suffix.format(disk=disk, snapshot=state['snapshot_id'])
            state['negative_requests_sent'] += 1
            gov['save'](path, state)
            status, value = request(gov, access, method, route, {'data': data}, deadline)
            item = {'action': action, 'method': method, 'route': route, 'status': status, 'reason': value.get('reason')}
            result['new_intents'].append(item)
            assert status == 409 and value.get('reason') == 'VM_OPERATION_CONFLICT', 'new intent was not denied by the durable Gov fence: ' + action
        status, still_open = request(gov, access, 'GET', BASE, deadline=deadline)
        assert status == 200 and still_open['id'] == VM and still_open['closing'] is False and int(still_open['version']) == version
        result['owner_not_closing_product_read'] = still_open
        create_body = material['create_body']
        status, replay = request(gov, access, 'POST', '/api/v1/virtual-machines', create_body, deadline)
        assert status == 202 and replay['replayed'] is True and replay['operationId'] == CREATE and replay['resourceId'] == VM
        result['create_replay'] = {'status': status, 'operation_id': replay['operationId'], 'replayed': True}
        status, replay = request(gov, access, 'DELETE', BASE, body, deadline)
        assert status == 202 and replay['replayed'] is True and replay['operationId'] == delete_id and replay['resourceId'] == VM
        result['delete_replay'] = {'status': status, 'operation_id': replay['operationId'], 'replayed': True}
        for old in old_commands:
            status, replay = request(gov, access, old['method'], old['route'], old['body'], deadline)
            assert status == 202 and replay['id'] == old['operation_id'] and replay['vmId'] == VM
            result['old_replays'].append({'label': old['label'], 'status': status, 'operation_id': replay['id']})
        for operation in [CREATE, delete_id] + [old['operation_id'] for old in old_commands]:
            status, value = request(gov, access, 'GET', BASE + '/operations/' + operation, deadline=deadline)
            assert status == 200 and value['id'] == operation and value['vmId'] == VM
            result['queries'].append({'status': status, 'operation_id': operation})
        result['paused_after_requests'] = prove_predispatch()
        sg_after = sg_keys(gov)
        assert not (set(sg_after['all_key_hashes']) - set(sg_before['all_key_hashes']))
        assert not (set(sg_after['vm_session_key_hashes']) - set(sg_before['vm_session_key_hashes']))
        result.update(sg_before=sg_before, sg_after=sg_after, no_new_owner_operations=True,
                      no_new_console_sessions=True, fence_checks_pass=True)
    except BaseException as caught:
        error = caught
        result['failure'] = str(caught)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        for number in (signal.SIGTERM, signal.SIGINT): signal.signal(number, signal.SIG_IGN)
        try:
            # Pause writes its recovery intent before ACL DDL. No state means
            # it could not have performed DDL; otherwise restore is unconditional.
            if ACL_JOURNAL.exists():
                result['acl_restore'] = acl_call('restore', state['lock_fd'])
                acl = json.loads(ACL_JOURNAL.read_text())
                final = json.loads(ACL_RECEIPT.read_text())
                assert acl['phase'] == 'restored' and final['current'] == final['original']
                result['exact_acl_restored'] = True
            else:
                result['acl_restore'] = 'NOT_NEEDED_NO_DURABLE_PAUSE_INTENT'
        except BaseException as restoration:
            result.update(acl_restore_error=str(restoration), exact_acl_restored=False)
            error = restoration
        finally:
            for number, previous in previous_handlers.items(): signal.signal(number, previous)
        result['paused_window_seconds'] = round(time.monotonic() - started, 3)
        state['phase'] = 'ACL_RESTORED' if result.get('exact_acl_restored') else 'STOPPED_ACL_REQUIRES_INSPECTION'
        gov['save'](path, state)
        gov['save'](receipt, result)
    # No business follow-up is permitted before exact restoration is proven.
    if not result.get('exact_acl_restored'):
        raise RuntimeError('ACL restoration not confirmed; stop and recover the existing journal') from error
    if state.get('delete_operation_id'):
        while time.monotonic() < started + 60:
            current_identity = identities(gov, seed)
            owned = [row for row in current_identity['resource']['operations'] if row['operation_id'] == state['delete_operation_id']]
            if owned:
                assert len(owned) == 1 and owned[0]['action'] == '/vm.v1.VMService/AcceptDelete'
                result['original_delete_continued'] = {'owner_operation': owned[0], 'identity_snapshot': current_identity}
                break
            time.sleep(0.5)
    result['pass'] = error is None and result.get('fence_checks_pass') is True and result.get('exact_acl_restored') is True and 'original_delete_continued' in result
    result['result'] = 'PASS' if result['pass'] else 'FAIL_OR_CONTINUATION_PENDING'
    state['phase'] = 'COMPLETE' if result['pass'] else 'RESTORED_NEEDS_INSPECTION'
    gov['save'](path, state)
    gov['save'](receipt, result)
    print(json.dumps({'result': result['result'], 'scope': result['scope'], 'receipt': str(receipt),
        'delete_operation_id': state.get('delete_operation_id'), 'new_intents_checked': len(result['new_intents']),
        'exact_acl_restored': result.get('exact_acl_restored'), 'paused_window_seconds': result['paused_window_seconds']}), flush=True)
    if error: raise RuntimeError('fence proof failed after exact ACL restoration; inspect public receipt') from error


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'execute'))
    args = parser.parse_args()
    gov, path = setup()
    state = prepare(gov, path)
    if args.action == 'prepare':
        print(json.dumps({'phase': state['phase'], 'vm_id': VM, 'create_operation_id': CREATE,
            'private_state_reference': str(path), 'pause_executed': False, 'http_requests': 0,
            'api_cases': [{'action': a, 'method': m, 'route': BASE + route} for a, m, route, _ in CASES],
            'old_replays': ['original CREATE', 'same DELETE', 'original Stop', 'original Start', 'original attach-data3', 'pre-DELETE issued/unclaimed/unexpired console same session/ticket/expiry'],
            'console_baseline': 'Issue once before pause; no WS; replay immediately after DELETE; SG delta baseline after pre-issue',
            'acl_original_sha256': ORIGINAL_ACL_SHA, 'acl_inherited_sha256': INHERITED_ACL_SHA,
            'recovery_command': 'python3 ' + str(RUN / 'vm-gov-delete-fence-pause.py') + ' restore'}))
        return
    with LOCK.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state['lock_fd'] = lock.fileno()
        execute(gov, path, state)


if __name__ == '__main__':
    main()
