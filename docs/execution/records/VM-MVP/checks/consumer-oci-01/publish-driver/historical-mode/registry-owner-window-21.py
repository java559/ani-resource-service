"""Exact registry21 node action. Credentials arrive only on stdin from the wrapper."""
from pathlib import Path
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import time

T = Path('/home/chabking/workspace/modeldev-execution-v2.1-20261022-01a15f47')
D = T / 'deployment/registry21'
OWNER = 'modeldev-execution-v2.1-20261022-01a15f47'
UNIT_SHA = '599642ae7fb4b9628fec2ade787df7d7f553b6aac34580834bc0bbe54084d06b'
CLUSTER_UID = '5277649d-e28d-4a0a-ae76-8354365c63bc'
ACTIONS = ('snapshot', 'open', 'close', 'restore-readback-only')


def validate_credentials(credential):
    assert set(credential) == {'action', 'user', 'password', 'helper_sha256'}
    assert credential['action'] in ACTIONS and credential['user'] == 'ubuntu'
    assert isinstance(credential['password'], str) and credential['password']
    assert re.fullmatch('[0-9a-f]{64}', credential['helper_sha256'])


def validate_snapshot(original, identity):
    assert original['event'] == 'SNAPSHOT' and original['window'] == '21'
    assert original['identity'] == identity and original['original_unit_sha256'] == UNIT_SHA
    assert original['runtime_dropin_absent'] and original['task_state_absent']


def durable(path, value):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(json.dumps(value, sort_keys=True, indent=2).encode() + b'\n')
        stream.flush()
        os.fsync(stream.fileno())
    path.chmod(0o600)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def context():
    k = [str(T / 'bin/kubectl'), '--kubeconfig', str(T / '.private/kubeconfig'), '--request-timeout=15s']
    ns = json.loads(subprocess.check_output(k + ['get', 'namespace', 'kube-system', '-o', 'json'], timeout=30))
    node = json.loads(subprocess.check_output(k + ['get', 'node', 'ani-01', '-o', 'json'], timeout=30))
    assert ns['metadata']['uid'] == CLUSTER_UID
    assert node['metadata']['name'] == 'ani-01'
    addresses = node['status']['addresses']
    assert any(x == {'type': 'InternalIP', 'address': '172.16.101.10'} for x in addresses)
    assert any(x == {'type': 'Hostname', 'address': 'ani-01'} for x in addresses)
    machine_id = node['status']['nodeInfo']['machineID']
    assert machine_id and len(machine_id) <= 64
    return {'cluster_uid': CLUSTER_UID, 'node_name': 'ani-01', 'node_uid': node['metadata']['uid'],
            'node_machine_id': machine_id, 'node_internal_ip': '172.16.101.10', 'task_owner': OWNER}


NODE = r'''
from pathlib import Path
import base64,datetime,hashlib,json,os,signal,socket,subprocess,time
unit=Path('/etc/systemd/system/ani-image-registry.service')
unit_sha='599642ae7fb4b9628fec2ade787df7d7f553b6aac34580834bc0bbe54084d06b'
binary=Path('/home/ubuntu/ani-kubeflow-first-install-dee542a-20261004/materials-frozen-dee542a-attempt-01/bin/hauler')
binary_sha='4eaf2d370feb3fe26ad58863e79e49c8c7f4579b675dc42a46a798c437e03a61'
service='ani-image-registry.service'
state=Path('/run/ani-modeldev-v21-registry-owner-window-21')
dropdir=Path('/run/systemd/system/ani-image-registry.service.d')
dropin=dropdir/'90-modeldev-v21-owner-window-21.conf'
assert socket.gethostname()=='ani-01' and Path('/etc/machine-id').read_text().strip()==EXPECTED['node_machine_id']
assert hashlib.sha256(unit.read_bytes()).hexdigest()==unit_sha
assert hashlib.sha256(binary.read_bytes()).hexdigest()==binary_sha
owner=json.dumps({'window':'21',**EXPECTED},sort_keys=True,separators=(',',':'))
command=str(binary)+' store serve registry --port 5000 --directory /var/lib/ani-installer/ani-lab/work/registry-data --readonly=false --store /var/lib/ani-installer/ani-lab/work/hauler-store'
content='[Service]\nExecStart=\nExecStart='+command+'\n'
def properties():
    return subprocess.check_output(['systemctl','show',service,'-p','ExecStart','-p','ActiveState','-p','MainPID','-p','DropInPaths'],text=True,timeout=30)
def readonly():
    props=properties()
    assert '--readonly=true' in props and '--readonly=false' not in props and 'ActiveState=active\n' in props and 'DropInPaths=\n' in props
    assert not state.exists() and not dropdir.exists()
    return props
def emit(event,**fields):
    print(json.dumps({'event':event,'window':'21','original_unit_sha256':unit_sha,'identity':EXPECTED,'observed_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),**fields}),flush=True)
if ACTION in ('snapshot','restore-readback-only'):
    props=readonly()
    if ACTION=='snapshot':
        assert '--readonly' in subprocess.check_output([str(binary),'store','serve','registry','--help'],text=True,timeout=30)
        emit('SNAPSHOT',properties=props,binary_sha256=binary_sha,readonly_flag_supported=True,runtime_dropin_absent=True,task_state_absent=True)
    else:
        emit('RESTORE_READBACK_ONLY',properties=props,runtime_dropin_removed=True,task_state_absent=True,mutations=0)
elif ACTION=='close':
    assert state.is_dir() and not state.is_symlink() and (state/'owner').read_text()==owner
    assert dropin.is_file() and dropin.read_text()==content
    with (state/'finish').open('x') as f:f.write('finish\n');f.flush();os.fsync(f.fileno())
    emit('CLOSE_REQUESTED')
else:
    props=readonly()
    start=time.monotonic()
    state.mkdir(mode=0o700)
    with (state/'owner').open('x') as f:f.write(owner);f.flush();os.fsync(f.fileno())
    def stop(signum,frame):raise KeyboardInterrupt()
    for sig in (signal.SIGHUP,signal.SIGINT,signal.SIGTERM):signal.signal(sig,stop)
    try:
        with (state/'dropin-source').open('x') as f:f.write(content);f.flush();os.fsync(f.fileno())
        (state/'dropin-source').chmod(0o644)
        dropdir.mkdir(mode=0o755)
        os.link(state/'dropin-source',dropin)
        subprocess.run(['systemctl','daemon-reload'],check=True,timeout=30)
        subprocess.run(['systemctl','restart',service],check=True,timeout=60)
        props=properties()
        assert '--readonly=false' in props and 'ActiveState=active\n' in props
        opened=datetime.datetime.now(datetime.timezone.utc)
        emit('WRITE_WINDOW_OPEN',properties=props,automatic_restore_seconds=1800,opened_at=opened.isoformat(),expires_at=(opened+datetime.timedelta(seconds=max(0,1800-(time.monotonic()-start)))).isoformat(),runtime_dropin=str(dropin),finish_signal=str(state/'finish'))
        while time.monotonic()-start<1800 and not (state/'finish').exists():time.sleep(1)
    finally:
        assert hashlib.sha256(unit.read_bytes()).hexdigest()==unit_sha,'Original unit changed; do not overwrite an unknown unit'
        assert (state/'owner').read_text()==owner,'Task state owner changed'
        if dropin.exists():
            assert dropin.read_text()==content,'Task drop-in changed; retain for inspection'
            dropin.unlink()
        if dropdir.exists():dropdir.rmdir()
        subprocess.run(['systemctl','daemon-reload'],check=True,timeout=30)
        subprocess.run(['systemctl','restart',service],check=True,timeout=60)
        for name in ('finish','owner','dropin-source'):
            p=state/name
            if p.exists():p.unlink()
        state.rmdir()
        props=readonly()
        emit('READONLY_RESTORED',properties=props,runtime_dropin_removed=True,task_state_absent=True)
'''


def main():
    os.umask(0o077)
    credential = json.load(sys.stdin)
    validate_credentials(credential)
    action = credential['action']
    assert hashlib.sha256(Path(__file__).read_bytes()).hexdigest() == credential['helper_sha256']
    identity = context()
    D.mkdir(mode=0o700, parents=True, exist_ok=True)
    snapshot = T / 'evidence/registry-owner-window-21-snapshot.json'
    if action in ['open', 'close']:
        assert snapshot.exists(), 'First capture the original read-only identity and unit'
        original = json.loads(snapshot.read_bytes())
        validate_snapshot(original, identity)
        if action == 'close':
            assert (D / 'open-issued.json').exists(), 'No original task-owned open attempt'
        durable(D / (action + '-issued.json'), {'action': action, 'identity': identity, 'original_unit_sha256': UNIT_SHA, 'issued_at_unix': time.time()})
    auth = T / '.private' / ('registry21-node-auth-' + str(os.getpid()))
    secret, askpass = auth / 'password', auth / 'askpass'
    auth_created = False
    def cleanup():
        if not auth_created:return
        for p in [secret, askpass]:
            if p.exists():p.unlink()
        if auth.exists():auth.rmdir()
    process = None
    try:
        auth.mkdir(mode=0o700)
        auth_created = True
        with secret.open('x') as f:f.write(credential['password'])
        secret.chmod(0o600)
        with askpass.open('x') as f:f.write('#!/bin/sh\nexec cat '+str(secret)+'\n')
        askpass.chmod(0o700)
        env = dict(os.environ, SSH_ASKPASS=str(askpass), SSH_ASKPASS_REQUIRE='force', DISPLAY=':0')
        command = ['ssh', '-o', 'StrictHostKeyChecking=yes', '-o', 'PreferredAuthentications=password', '-o', 'NumberOfPasswordPrompts=1', '-o', 'ConnectTimeout=15', 'ubuntu@172.16.101.10', 'sudo -n python3 -']
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, bufsize=1)
        process.stdin.write('ACTION='+repr(action)+'\nEXPECTED='+repr(identity)+'\n'+NODE)
        process.stdin.close()
        for line in process.stdout:
            result = json.loads(line)
            assert result['identity'] == identity and result['window'] == '21' and result['original_unit_sha256'] == UNIT_SHA
            cleanup()
            if result['event'] == 'WRITE_WINDOW_OPEN':
                result['controller_pid'] = os.getpid()
                result['controller_start_ticks'] = Path('/proc/self/stat').read_text().split(') ', 1)[1].split()[19]
            path = T / 'evidence' / ('registry-owner-window-21-'+result['event'].lower()+'.json')
            if path.exists():
                assert result['event'] in ['SNAPSHOT', 'RESTORE_READBACK_ONLY'], 'A mutation receipt already exists; inspect the original action'
                path = D / 'observations' / (result['event'].lower()+'-'+str(time.time_ns())+'.json')
            durable(path, result)
            print(json.dumps({**result, 'receipt': str(path)}), flush=True)
        error = process.stderr.read()
        rc = process.wait()
        if rc:
            durable(D / ('node-error-'+str(time.time_ns())+'.json'), {'action': action, 'exit_code': rc, 'stderr_redacted': error.replace(credential['password'], '<redacted>')[-1500:]})
            print(json.dumps({'event': 'NODE_ACTION_FAILED', 'window': '21', 'exit_code': rc, 'unknown_actions_must_not_be_reissued': True, 'readback_action': 'restore-readback-only'}), flush=True)
        return rc
    finally:
        cleanup()
        if process is not None and process.poll() is None:
            process.terminate()
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill();process.wait()


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as error:
        print(json.dumps({'event': 'ACTION_STOPPED_NO_AUTOMATIC_RETRY', 'window': '21', 'error_type': type(error).__name__}), flush=True)
        sys.exit(1)
