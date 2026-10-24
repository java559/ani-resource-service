#!/usr/bin/env python3
"""V08 task-only account persistence check for the existing V3 VM.

Each live phase needs a separately approved window. self-test imports no live
helper and performs no API/Guest request. A password is sent only to an explicit
interactive password prompt; neither secret nor pexpect buffers are published.
"""
from pathlib import Path
import argparse
import base64
import fcntl
import hashlib
import json
import re
import runpy
import secrets
import shlex
import stat
import time
import uuid
import zlib

RUN = Path('/home/chabking/workspace/ani-resource-service-runs/vm-mvp-20261023-01a168ce')
LOCK = Path('/home/chabking/workspace/ani-network-service-runs/net05a-heavy.lock')
PRIVATE = RUN / 'private/gov-candidate'
STATE = PRIVATE / 'linux-account-persistence-v3.json'
NEW_PASSWORD = PRIVATE / 'linux-account-persistence-v3-password.json'
ORIGINAL_CREDENTIALS = PRIVATE / 'linux-guest-credentials-v3.json'
SNAPSHOT_DRIVER = RUN / 'vm-product-snapshot-linux.py'
VM = '55099440-c47b-4f81-afc5-7d8e740d14dc'
VM_UID = '7bca4671-89dd-4925-bad9-17e77401729d'
DISKS = {'5dcd6eee-e30c-5dd2-b72b-f7d0d5699d21',
         'd296c9f6-2e76-5756-bb4c-def2d0f8f2f9',
         '5dbc2c93-9865-54f9-8413-f198f5e9c1a6'}
D6 = 'dd773137-07f9-5b7a-9511-fda3dcf09001'
COMMANDS = {
    'stop-for-snapshot': ('stop', 'change-new'),
    'snapshot-account': ('snapshot', 'stop-for-snapshot'),
    'start-after-snapshot': ('start', 'snapshot-account'),
    'restart-new': ('restart', 'auth-new-after-start'),
    'stop-for-restore': ('stop', 'change-old'),
    'restore-account': ('restore', 'preview'),
    'start-after-restore': ('start', 'restore-account'),
}
SHELL = r'(?:^|[\r\n])[^\r\n]*aniuser@[^\r\n]*\$ '
LOGIN = r'(?:^|[\r\n])[A-Za-z0-9_.-]+ login: ?'
GUEST_READ = r'''import hashlib,json,pathlib,stat,subprocess
r=lambda p:pathlib.Path(p).read_text().strip()
vm='55099440-c47b-4f81-afc5-7d8e740d14dc'
assert r('/sys/class/dmi/id/product_uuid')==vm
assert r('/var/lib/cloud/data/instance-id')==vm
assert 'DataSourceNoCloud' in r('/var/lib/cloud/instance/datasource')
root=pathlib.Path('/home/aniuser/vm-mvp-snapshot-root')
assert root.is_file() and not root.is_symlink() and root.stat().st_size<128
assert root.read_text().strip()=='VM_MVP_SNAPSHOT_A_ROOT_01a168ce'
all_disks=json.loads(subprocess.check_output(['lsblk','-b','-J','-o','NAME,PATH,SERIAL,TYPE,SIZE,FSTYPE,MOUNTPOINTS']))['blockdevices']
values=[]
for serial,label in [('d296c9f6-2e76-5756-bb4c-def2d0f8f2f9','DATA3'),('5dbc2c93-9865-54f9-8413-f198f5e9c1a6','DATA4')]:
 found=[d for d in all_disks if d.get('serial')==serial and d['type']=='disk'];assert len(found)==1
 d=found[0];p=d['path'];assert p=='/dev/'+d['name'] and stat.S_ISBLK(pathlib.Path(p).stat().st_mode)
 assert not d.get('children') and not d.get('fstype') and not any(d.get('mountpoints') or [])
 assert not list(pathlib.Path('/sys/class/block',d['name'],'holders').iterdir())
 with open(p,'rb',buffering=0) as f:actual=f.read(512)
 assert actual[:27]==('VM_MVP_V3_'+label+'_A_01a168ce\n').encode() and actual[27:]==bytes(485)
 values.append({'disk_id':serial,'serial':serial,'marker_state':'A','original_485_byte_tail_equal':True,'bytes_written':0})
print(json.dumps({'account_proof':'ANI_ACCOUNT_READ_OK','nocloud_instance_id':vm,'firmware_uuid':vm,'datasource':'DataSourceNoCloud','boot_id':r('/proc/sys/kernel/random/boot_id'),'root_marker_state':'A','disks':values,'guest_disk_bytes_written':0}))
'''


def sha(value):
    return hashlib.sha256(value).hexdigest()


def validate_inventory(disks):
    assert {d['diskId'] for d in disks if d.get('attached')} == DISKS, 'attached disk set differs'
    retained = [d for d in disks if d['diskId'] == D6]
    assert len(retained) == 1 and not retained[0].get('attached'), 'D6 must remain detached'
    assert retained[0].get('preparationState') == 'Bound', 'D6 must remain Bound'


def require_completed(state, name):
    if name in COMMANDS:
        value = state['commands'].get(name, {})
        assert value.get('operation', {}).get('state') == 'succeeded', 'observe original operation first: ' + name
    elif name == 'preview':
        assert state.get('previews', {}).get('offline'), 'Root must review the exact stopped preview'
    else:
        assert state.get('guest', {}).get(name, {}).get('result') == 'PASS', 'Guest prerequisite not proven: ' + name


def validate_public(value):
    if isinstance(value, dict):
        for key, item in value.items():
            assert key.lower() not in {'password', 'password_hash', 'ciphertext', 'userdata', 'user_data',
                                       'raw_transcript', 'pexpect_error', 'secret_data_sha256'}, 'secret field in public receipt'
            validate_public(item)
    elif isinstance(value, list):
        for item in value:
            validate_public(item)


def self_test():
    compile(GUEST_READ, '<read-only-account-proof>', 'exec')
    disks = [{'diskId': d, 'attached': True} for d in sorted(DISKS)]
    disks += [{'diskId': D6, 'attached': False, 'preparationState': 'Bound'}]
    validate_inventory(disks)
    for mutation in ('extra-attached', 'd6-attached', 'd6-unready'):
        bad = json.loads(json.dumps(disks))
        if mutation == 'extra-attached': bad.append({'diskId': str(uuid.uuid4()), 'attached': True})
        elif mutation == 'd6-attached': bad[-1]['attached'] = True
        else: bad[-1]['preparationState'] = 'Pending'
        try: validate_inventory(bad)
        except AssertionError: pass
        else: raise AssertionError('inventory mismatch accepted')
    state = {'commands': {'stop-for-snapshot': {'operation': {'state': 'running'}}}}
    try: require_completed(state, 'stop-for-snapshot')
    except AssertionError: pass
    else: raise AssertionError('unresolved command accepted')
    for key in ('password', 'password_hash', 'ciphertext', 'raw_transcript', 'pexpect_error', 'secret_data_sha256'):
        try: validate_public({'guest': {key: 'fixture'}})
        except AssertionError: pass
        else: raise AssertionError('secret-like public field accepted')
    validate_public({'guest': {'result': 'UNKNOWN', 'error_type': 'TIMEOUT', 'stage': 'WAIT_PASSWD_COMPLETION'}})
    assert ORIGINAL_CREDENTIALS != NEW_PASSWORD and STATE.name != 'linux-snapshots-v3.json'
    assert not any(item in GUEST_READ for item in ('passwd', 'chpasswd', 'usermod', 'open(p,\'wb\''))
    print(json.dumps({'result': 'PASS', 'pure_guards': 12, 'api_requests': 0, 'guest_commands': 0,
                      'scope': 'fixed three disks plus detached D6, original-op prerequisite, public secret exclusion'}))


class Driver:
    def __init__(self, source_sha):
        assert re.fullmatch('[0-9a-f]{64}', source_sha or ''), 'pin the final snapshot helper source'
        assert sha(SNAPSHOT_DRIVER.read_bytes()) == source_sha, 'snapshot helper source changed'
        self.module = runpy.run_path(str(SNAPSHOT_DRIVER))
        assert self.module['VM'] == VM and self.module['VM_UID'] == VM_UID
        assert sha(SNAPSHOT_DRIVER.read_bytes()) == source_sha, 'helper changed while loading'
        self.base = self.module['Driver']()  # Normal existing-principal Gov login; never called by pure guards.
        self.state = json.loads(STATE.read_text()) if STATE.exists() else None
        if self.state:
            assert self.state['vm_id'] == VM and self.state['native_vm_uid'] == VM_UID
            assert self.state['snapshot_helper_sha256'] == source_sha
            self.base.state, self.base.path = self.state, STATE
            self.base.persist = self.persist

    def persist(self, intent, value):
        self.base.save(STATE, self.state)
        public = {'vm_id': VM, 'native_vm_uid': VM_UID, 'intent': intent,
                  'driver_sha256': sha(Path(__file__).read_bytes()),
                  'snapshot_helper_sha256': self.state['snapshot_helper_sha256'],
                  'private_state_reference': str(STATE), **value}
        validate_public(public)
        path = RUN / ('evidence/gov-linux-account-v3-' + intent + '.json')
        self.base.save(path, public)
        return {'result': value.get('result', value.get('guest', {}).get('result')),
                'phase': value['phase'], 'receipt': str(path)}

    def secrets_guard(self, establish=False):
        assert stat.S_IMODE(ORIGINAL_CREDENTIALS.stat().st_mode) == 0o600
        cred = json.loads(ORIGINAL_CREDENTIALS.read_text())
        assert cred['username'] == 'aniuser' and cred['password']
        native_vm = self.base.kubectl('virtualmachine.kubevirt.io', 'vm-' + VM)
        assert native_vm['metadata']['uid'] == VM_UID
        names = set()
        for volume in native_vm['spec']['template']['spec']['volumes']:
            cloud = volume.get('cloudInitNoCloud', {})
            for key in ('userDataSecretRef', 'networkDataSecretRef'):
                if key in cloud: names.add(cloud[key]['name'])
        assert names, 'NoCloud Secret references missing'
        guard = {'original_credentials_file_sha256': sha(ORIGINAL_CREDENTIALS.read_bytes()), 'secrets': []}
        for name in sorted(names):
            secret = self.base.kubectl('secret', name)
            assert secret and self.module['owns'](secret, VM_UID)
            guard['secrets'].append({'reference': self.module['ref'](secret),
                                    'secret_data_sha256': self.module['digest'](secret.get('data', {}))})
        if establish: self.state['secret_guard'] = guard
        else: assert guard == self.state['secret_guard'], 'initial Secret or original credential file changed'
        return {'original_credentials_file_unchanged': True, 'initial_secret_data_unchanged': True,
                'initial_secrets': [item['reference'] for item in guard['secrets']]}

    def capture(self):
        current = self.base.get(); disks = self.base.get('/disks').get('items', [])
        assert current['id'] == VM and not current['closing']
        validate_inventory(disks)
        native = self.base.native(disks)
        if self.state:
            d6 = next(d for d in disks if d['diskId'] == D6)
            assert d6 == self.state['retained_d6'], 'D6 changed during account check'
            if self.state.get('retained_d6_physical'):
                physical = self.state['retained_d6_physical']
                pvc = self.base.kubectl('pvc', physical['observed_pvc']['name'])
                assert pvc and pvc['metadata']['uid'] == physical['observed_pvc']['uid']
                assert pvc['status']['phase'] == 'Bound' and pvc['spec']['volumeMode'] == 'Block'
                pv = self.base.kubectl('pv', physical['observed_pv']['name'], namespace='')
                assert pv and pv['metadata']['uid'] == physical['observed_pv']['uid']
                assert pv['spec']['claimRef']['uid'] == pvc['metadata']['uid']
                assert pv['spec']['csi']['volumeHandle'] == physical['csi']['volume_handle']
        return current, disks, native

    def freeze(self):
        assert self.state is None and not NEW_PASSWORD.exists(), 'this account intent already exists'
        current, disks, native = self.capture()
        assert current['observedState'] == 'Running' and native['vmi']['phase'] == 'Running'
        snapshots = self.base.get('/snapshots').get('items', [])
        assert len(snapshots) == 2 and all(s['ready'] and s['completeness'] == 'COMPLETE' for s in snapshots)
        self.state = {'vm_id': VM, 'native_vm_uid': VM_UID,
                      'snapshot_helper_sha256': sha(SNAPSHOT_DRIVER.read_bytes()),
                      'commands': {}, 'snapshots': {}, 'previews': {}, 'guest': {},
                      'baseline': {'current': current, 'disks': disks, 'native': native,
                                   'snapshot_ids': [s['snapshotId'] for s in snapshots]},
                      'retained_d6': next(d for d in disks if d['diskId'] == D6)}
        self.base.state, self.base.path, self.base.persist = self.state, STATE, self.persist
        protection = self.secrets_guard(establish=True)
        self.base.storage('d6-baseline')
        storage = json.loads((RUN / 'evidence/gov-linux-account-v3-d6-baseline.json').read_text())
        self.state['retained_d6_physical'] = next(d for d in storage['disks'] if d['ID'] == D6)
        assert self.state['retained_d6_physical']['observed_pvc']['phase'] == 'Bound'
        # Generated only on Fedora in the explicitly approved live freeze phase.
        self.base.save(NEW_PASSWORD, {'username': 'aniuser', 'password': 'Aa3!' + secrets.token_urlsafe(45)})
        assert stat.S_IMODE(NEW_PASSWORD.stat().st_mode) == 0o600
        return self.persist('prepared', {'phase': 'FROZEN_NOT_CHANGED', 'current': current,
                            'native': native, 'protected': protection, 'business_mutations': 0,
                            'guest_commands': 0, 'new_credentials_private_reference': str(NEW_PASSWORD)})

    def command(self, intent, recover, approved_digest):
        action, prerequisite = COMMANDS[intent]
        require_completed(self.state, prerequisite)
        current, disks, native = self.capture()
        self.secrets_guard()
        if intent == 'start-after-snapshot':
            assert self.state.get('snapshot_native_complete'), 'strict third offline snapshot is not ready'
        if action == 'restore':
            preview = self.state['previews']['offline']
            assert approved_digest and approved_digest == preview['previewDigest'], 'Root exact preview approval required'
        command = self.state['commands'].get(intent)
        if command is None:
            assert not recover
            assert all(c.get('operation', {}).get('state') in ('succeeded', 'failed') for c in self.state['commands'].values())
            assert current['observedState'] == ('Stopped' if action in ('snapshot', 'start', 'restore') else 'Running')
            if action in ('snapshot', 'start', 'restore'):
                assert native['vmi'] is None and native['launcher'] is None, 'Stopped requires VMI/launcher Gone'
            data = {'idempotencyKey': str(uuid.uuid4()), 'expectedVersion': str(current['version'])}
            route = self.module['BASE'] + '/' + action
            command = {'action': action, 'mode': 'offline', 'before': current, 'native_before': native,
                       'send_attempts': 0, 'ack_unknown': False}
            if action == 'snapshot':
                data['name'] = 'vm-mvp-linux-v3-account-v08'
                route = self.module['BASE'] + '/snapshots'
                command['snapshot_ids_before'] = [s['snapshotId'] for s in self.base.get('/snapshots').get('items', [])]
                command['inventory'] = [d for d in disks if d['attached']]
            elif action == 'restore':
                snapshot = self.state['snapshots']['offline']; preview = self.state['previews']['offline']
                assert int(preview['version']) == int(current['version'])
                data.update(snapshotId=snapshot['snapshotId'], previewDigest=approved_digest, destructiveConfirmation=True)
                route = self.module['BASE'] + '/snapshots/' + snapshot['snapshotId'] + '/restore'
            command.update(body={'data': data}, method='POST', route=route)
            command['body_sha256'] = self.module['digest'](command['body'])
            self.state['commands'][intent] = command
            self.persist(intent, {'phase': 'INTENT_FROZEN', 'command': command})
        args = argparse.Namespace(execute_authorized=True, intent=intent, action=action, mode='offline',
                                  recover_admission=recover, wait_seconds=0)
        if action != 'restart': return self.base.command(args)
        # Restart is the only action absent from the reused snapshot driver.
        assert self.module['digest'](command['body']) == command['body_sha256']
        if 'operation_id' not in command:
            assert command['send_attempts'] == 0 or (recover and command['ack_unknown'])
            command['send_attempts'] += 1; command['ack_unknown'] = True
            self.persist(intent, {'phase': 'SEND_DURABLE', 'command': command})
            accepted = self.base.api('POST', command['route'], command['body'], self.base.token)
            command['accepted'] = accepted; command['http_status'] = self.base.helper['EVENTS'][-1]['status']
            self.persist(intent, {'phase': 'ACK_RECEIVED', 'command': command})
            assert command['http_status'] == 202 and accepted['vmId'] == VM
            assert str(uuid.UUID(accepted['id'])) == accepted['id']
            command.update(operation_id=accepted['id'], ack_unknown=False)
            self.persist(intent, {'phase': 'ACK_PINNED', 'command': command})
        return self.base.observe(intent, 0)

    def guest(self, phase):
        import pexpect
        recovery = phase.startswith('recover-')
        target = phase.removeprefix('recover-')
        changing = target in ('change-new', 'change-old') and not recovery
        if target == 'change-new':
            if not recovery: assert not self.state['guest'].get(target), 'do not repeat a password-change attempt'
        elif target == 'auth-new-after-start': require_completed(self.state, 'start-after-snapshot')
        elif target == 'auth-new-after-restart': require_completed(self.state, 'restart-new')
        elif target == 'change-old':
            require_completed(self.state, 'auth-new-after-restart')
            if not recovery: assert not self.state['guest'].get(target), 'do not repeat a password-change attempt'
        else:
            assert target == 'auth-new-after-restore'; require_completed(self.state, 'start-after-restore')
        if recovery:
            assert self.state['guest'].get(target, {}).get('result') == 'UNKNOWN', 'no uncertain password change to recover'
        current, disks, native = self.capture()
        assert current['observedState'] == 'Running' and native['vmi']['phase'] == 'Running'
        protection = self.secrets_guard()
        old = json.loads(ORIGINAL_CREDENTIALS.read_text())['password']
        assert stat.S_IMODE(NEW_PASSWORD.stat().st_mode) == 0o600
        new = json.loads(NEW_PASSWORD.read_text())['password']
        assert old != new and '\n' not in new and '\r' not in new
        final = old if target == 'change-old' else new
        initial = final if recovery else new if target == 'change-old' else old if target == 'change-new' else new
        observer = runpy.run_path(str(RUN / 'vm-product-guest-observe.py'))
        virtctl = observer['VIRTCTL']; assert sha(virtctl.read_bytes()) == observer['VIRTCTL_SHA']
        result = {'result': 'UNKNOWN', 'stage': 'CONNECT_CONSOLE', 'native': native,
                  'authentication_method': 'FRESH_NORMAL_CONSOLE_PASSWORD_LOGIN', 'password_change_sent': False,
                  'password_version': 'original' if target == 'change-old' else 'new-test',
                  'ctrl_c_sent': False, 'raw_buffers_recorded': False, 'protected': protection}
        if changing:
            self.state['guest'][target] = result
            self.persist(target, {'phase': 'GUEST_PHASE_DURABLE', 'guest': result})
        child = pexpect.spawn(str(virtctl), ['console', 'vm-' + VM, '--namespace', self.module['NAMESPACE'],
                              '--kubeconfig', self.module['KUBECONFIG'], '--timeout=20'],
                              encoding='utf-8', timeout=40, echo=False)
        child.logfile = child.logfile_read = child.logfile_send = None
        deadline = time.monotonic() + 360
        def expect(pattern, limit=40):
            remaining = deadline - time.monotonic()
            assert remaining > 0, 'bounded console window exhausted'
            return child.expect(pattern, timeout=min(limit, remaining))
        def fresh_login(password, no_input_until_prompt=False):
            result['stage'] = 'WAIT_KNOWN_IDLE_PROMPT'
            try: prompt = expect([SHELL, LOGIN], 15 if not no_input_until_prompt else 120)
            except pexpect.TIMEOUT:
                assert not no_input_until_prompt, 'uncertain passwd has not naturally returned to a known idle prompt'
                child.sendline(''); prompt = expect([SHELL, LOGIN], 60)
            if prompt == 0:
                child.sendline('exit'); expect(LOGIN)
            result['stage'] = 'FRESH_LOGIN_PASSWORD_PROMPT'
            child.sendline('aniuser'); expect(r'[Pp]assword:')
            child.sendline(password)
            assert expect([SHELL, r'Login incorrect'], 120) == 0, 'normal login rejected'
            result['fresh_normal_login_confirmed'] = True
        try:
            expect(r'Successfully connected to .* console\. Press Ctrl\+\] or Ctrl\+5 to exit console\.', 20)
            fresh_login(initial, recovery)
            if changing:
                result['stage'] = 'WAIT_PASSWD_CURRENT_PROMPT'
                result['password_change_sent'] = True
                self.persist(target, {'phase': 'PASSWD_ATTEMPT_DURABLE', 'guest': result})
                child.sendline('LC_ALL=C passwd')
                expect(r'(?:Current password:|\(current\) UNIX password:)')
                child.sendline(initial); expect(r'New password:')
                child.sendline(final); expect(r'Retype new password:')
                child.sendline(final); result['stage'] = 'WAIT_PASSWD_COMPLETION'
                expect(r'passwd: password updated successfully', 60)
                expect(SHELL); result['passwd_completed'] = True
                # Only after explicit passwd completion may the shell log out.
                child.sendline('exit'); expect(LOGIN)
                child.sendline('aniuser'); expect(r'[Pp]assword:'); child.sendline(final)
                assert expect([SHELL, r'Login incorrect'], 120) == 0
                result['fresh_login_after_change_confirmed'] = True
            result['stage'] = 'READ_NOCLOUD_AND_EXISTING_A_MARKERS'
            # Fresh login above proves account authentication independently of sudo.
            child.sendline("sudo -k; sudo -v -p 'ANI_ACCOUNT_SUDO: '")
            expect(r'(?:^|[\r\n])ANI_ACCOUNT_SUDO: '); child.sendline(final); expect(SHELL)
            line = 'import base64,zlib;exec(zlib.decompress(base64.b64decode(' + repr(base64.b64encode(zlib.compress(GUEST_READ.encode())).decode()) + ')))'
            child.sendline('sudo -n python3 -c ' + shlex.quote(line)); expect(SHELL, 75)
            found = []
            for raw in child.before.splitlines():
                try: value = json.loads(raw.strip())
                except json.JSONDecodeError: continue
                if isinstance(value, dict) and value.get('account_proof') == 'ANI_ACCOUNT_READ_OK': found.append(value)
            assert len(found) == 1
            proof = found[0]
            before = self.state['guest'].get('auth-new-after-start')
            if target == 'auth-new-after-restart':
                assert before and native['vmi']['uid'] != before['native']['vmi']['uid']
                assert proof['boot_id'] != before['proof']['boot_id']
            if target == 'auth-new-after-start':
                assert native['vmi']['uid'] != self.state['baseline']['native']['vmi']['uid']
            if target == 'auth-new-after-restore':
                prior = self.state['guest']['change-old']
                assert native['vmi']['uid'] != prior['native']['vmi']['uid']
                assert all(self.state['commands']['restore-account']['owner_checkpoints']['snapshot'][key] is True
                           for key in ('NativeRestoreCompleted', 'MappingCommitted', 'CleanupCompleted'))
            child.sendline('sudo -k'); expect(SHELL)
            child.sendline('exit'); expect(LOGIN)
            fresh_native = self.base.native(disks)
            assert fresh_native == native, 'native identity changed during authentication proof'
            result.update(result='PASS', stage='ACCOUNT_AND_A_MARKERS_CONFIRMED', proof=proof)
            result['protected'] = self.secrets_guard()
        except Exception as error:
            # pexpect exception strings/buffers may contain a password. Never serialize them.
            result['error_type'] = type(error).__name__
        finally:
            child.close(force=True)  # Disconnect virtctl only; never signal a Guest process.
        self.state['guest'][target] = result
        return self.persist(phase, {'phase': 'GUEST_ACCOUNT_PROOF', 'guest': result})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=('self-test', 'freeze', 'command', 'observe', 'snapshot-native', 'preview',
                        'change-new', 'recover-change-new', 'auth-new-after-start', 'auth-new-after-restart',
                        'change-old', 'recover-change-old', 'auth-new-after-restore'))
    parser.add_argument('--snapshot-helper-sha256')
    parser.add_argument('--execute-authorized', action='store_true')
    parser.add_argument('--intent', choices=tuple(COMMANDS))
    parser.add_argument('--recover-admission', action='store_true')
    parser.add_argument('--approved-preview-digest')
    parser.add_argument('--wait-seconds', type=int, choices=range(31), default=0)
    args = parser.parse_args()
    if args.phase == 'self-test': self_test(); return
    assert args.execute_authorized, 'only run live phases in Root explicit execution window'
    with LOCK.open('a') as lock:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({'phase': 'LOCK_BUSY', 'business_mutations': 0, 'guest_commands': 0})); raise SystemExit(73)
        driver = Driver(args.snapshot_helper_sha256)
        if args.phase == 'freeze': value = driver.freeze()
        else:
            assert driver.state, 'freeze the final baseline first'
            if args.phase == 'command':
                assert args.intent; value = driver.command(args.intent, args.recover_admission, args.approved_preview_digest)
            elif args.phase == 'observe':
                assert args.intent; value = driver.base.observe(args.intent, args.wait_seconds)
            elif args.phase == 'snapshot-native':
                value = driver.base.snapshot_native('offline')
                driver.state['snapshot_native_complete'] = True; driver.base.save(STATE, driver.state)
            elif args.phase == 'preview':
                require_completed(driver.state, 'stop-for-restore')
                value = driver.base.preview('offline', None)
            else: value = driver.guest(args.phase)
        print(json.dumps(value))


if __name__ == '__main__':
    try: main()
    except SystemExit: raise
    except Exception as error:
        print(json.dumps({'result': 'STOPPED', 'error_type': type(error).__name__,
                          'details': 'No exception string or pexpect buffer is public; inspect the last durable stage.'}))
        raise SystemExit(1)
