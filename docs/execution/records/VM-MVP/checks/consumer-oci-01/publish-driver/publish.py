#!/usr/bin/env python3
"""Prepare/check three frozen OCI archives; registry actions require explicit approval.

No login, pull, build, deployment, tag replacement, or automatic copy retry exists.
Registry V2 tag writes are not atomic create-only: the caller must reserve these
three new task tags exclusively for the whole absence-check/copy/readback window.
"""
import argparse
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import tarfile
import time

RUN = Path('/home/chabking/workspace/ani-resource-service-runs/vm-mvp-20261023-01a168ce')
DIRECTORY = RUN / 'consumer-oci-01/publish-driver'
PLAN_SHA = '59054e929b454b8af8cfaeec428d7c3f9e8ec334503899df961f9b8bab85b096'
ENDPOINT = '172.16.101.10:5000'
SKOPEO = '/usr/bin/skopeo'
SERVICES = ('resource', 'session', 'governance')


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def regular(path, private=False):
    require(path.is_absolute() and not path.is_symlink(), 'absolute non-symlink file required')
    info = path.stat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid(), 'owned regular file required')
    require(not (info.st_mode & 0o022), 'group/world-writable input rejected')
    if private:
        require(stat.S_IMODE(info.st_mode) == 0o600, 'private input must be mode 0600')


def atomic(path, value, mode=0o600):
    temporary = path.with_name(path.name + '.tmp-' + str(os.getpid()))
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream, sort_keys=True, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    parent = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)


def timestamp():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def load_plan(directory):
    path = directory / 'frozen-publish-prepared.json'
    regular(path)
    require(file_sha(path) == PLAN_SHA, 'frozen plan hash changed')
    plan = json.loads(path.read_text())
    require(plan['registry']['endpoint'] == ENDPOINT, 'registry changed')
    require(tuple(im['service'] for im in plan['images']) == SERVICES, 'service list changed')
    return {im['service']: im for im in plan['images']}


def local_metadata(directory, image):
    archive = Path(image['archive'])
    require(archive.is_relative_to(RUN), 'archive outside exact Fedora task')
    regular(archive)
    require(archive.stat().st_size == image['archive_bytes'], 'archive size changed')
    require(file_sha(archive) == image['archive_sha256'], 'archive hash changed')
    receipt = directory / 'source-receipts' / (image['service'] + '.json')
    regular(receipt)
    require(file_sha(receipt) == image['artifact_receipt_sha256'], 'artifact receipt changed')
    tag = image['local_image_tag'].split(':', 1)[1]
    expected = image['expected_manifest_digest']
    with tarfile.open(archive, 'r:') as bundle:
        index = json.load(bundle.extractfile('index.json'))
        matches = [d for d in index['manifests']
                   if d.get('annotations', {}).get('org.opencontainers.image.ref.name') == tag]
        require(len(matches) == 1, 'exact OCI ref must select one manifest')
        descriptor = matches[0]
        require(descriptor['digest'] == expected, 'OCI tag points to unexpected manifest')
        raw = bundle.extractfile('blobs/' + expected.replace(':', '/')).read()
        require('sha256:' + digest(raw) == expected, 'raw OCI manifest hash changed')
        require(json.loads(raw)['mediaType'] == 'application/vnd.oci.image.manifest.v1+json',
                'single OCI image manifest required')
    destination = image['proposed_destination_tag']
    require(destination.startswith(ENDPOINT + '/ani/') and destination.endswith(':' + tag),
            'exact task destination required')
    require(image['future_command_not_executed'][-2:] == [
        'oci-archive:' + str(archive) + ':' + tag, 'docker://' + destination],
        'source or destination differs from frozen command')
    return {'service': image['service'], 'archive_sha256': image['archive_sha256'],
            'artifact_receipt_sha256': image['artifact_receipt_sha256'],
            'manifest_digest': expected, 'destination': destination,
            'source_commit': image['source_commit'], 'source_tree': image['source_tree']}


def confirmed_absent(code, stdout, stderr, image):
    """Fail closed: only the exact reference's terminal registry missing error."""
    if code != 1 or stdout:
        return False
    reference = image['proposed_destination_tag']
    tag = reference.rsplit(':', 1)[1]
    repository = reference.rsplit(':', 1)[0]
    prefix = 'reading manifest ' + tag + ' in ' + repository + ': '
    text = stderr.decode('utf-8', 'replace')
    # Skopeo wraps the registry error in a timestamped fatal message. Match its
    # complete final cause, not arbitrary appearances of "unknown" or HTTP 404.
    tail = re.search(re.escape(prefix) + r'(manifest unknown|name unknown)(?:[ :][^"\n]*)?"?\s*$', text)
    return tail is not None


def approvals(args):
    require(args.root_authorized and args.approved_plan_sha256 == PLAN_SHA,
            'registry actions require explicit Root authorization and exact plan SHA')
    require(args.approved_driver_sha256 == file_sha(Path(__file__)), 'approved driver hash required')
    require(args.exclusive_new_task_tags, 'exclusive task tag window must be confirmed')
    if args.transport == 'explicit-http':
        require(args.approved_insecure_endpoint == ENDPOINT,
                'HTTP/TLS-disable requires explicit approval for this exact endpoint')
    else:
        require(not args.approved_insecure_endpoint, 'insecure approval incompatible with strict TLS')
    if args.auth_file:
        path = Path(args.auth_file)
        regular(path, private=True)
        require(args.approved_auth_file_sha256 and file_sha(path) == args.approved_auth_file_sha256,
                'approved private auth file hash required')
        require(not args.anonymous_authorized, 'select exactly one approved auth mode')
        auth_data = json.loads(path.read_text())
        require(isinstance(auth_data.get('auths'), dict) and
                not (set(auth_data) - {'auths'}), 'credential-helper configuration rejected')
    else:
        require(args.anonymous_authorized and not args.approved_auth_file_sha256,
                'explicit anonymous authorization or approved auth file required')


def registry_action(args, image, metadata):
    approvals(args)
    private = DIRECTORY / 'private'
    private.mkdir(mode=0o700, exist_ok=True)
    require(not private.is_symlink() and stat.S_IMODE(private.stat().st_mode) == 0o700,
            'task private directory mode changed')
    state_path = private / (args.service + '.json')
    if state_path.exists():
        regular(state_path, private=True)
    state = json.loads(state_path.read_text()) if state_path.exists() else {
        'service': args.service, 'plan_sha256': PLAN_SHA, 'copy_attempts': 0,
        'metadata': metadata, 'events': []}
    require(state['metadata'] == metadata and state['plan_sha256'] == PLAN_SHA,
            'prior state input mismatch')
    if args.action == 'publish':
        require(state['copy_attempts'] == 0, 'copy already attempted; reconcile only, never resend')
    else:
        require(state['copy_attempts'] == 1, 'reconcile requires the recorded original copy attempt')
    auth = Path(args.auth_file) if args.auth_file else private / 'anonymous-auth.json'
    if not args.auth_file and not auth.exists():
        atomic(auth, {'auths': {}})
    regular(auth, private=True)
    if not args.auth_file:
        require(json.loads(auth.read_text()) == {'auths': {}}, 'anonymous auth file is not empty')
    tls = 'true' if args.transport == 'strict-tls' else 'false'
    # A task-local auth file plus explicit no-creds avoids global login/helpers.
    inspect_auth = ['--authfile', str(auth)] + ([] if args.auth_file else ['--no-creds'])
    copy_auth = ['--dest-authfile', str(auth)] + ([] if args.auth_file else ['--dest-no-creds'])
    state['transport'] = args.transport
    state['auth'] = {'mode': 'approved-file' if args.auth_file else 'approved-anonymous',
                     'reference': str(auth), 'sha256': file_sha(auth)}
    atomic(state_path, state)

    def run(stage, argv, timeout):
        require(file_sha(auth) == state['auth']['sha256'], 'approved auth file changed')
        event = {'stage': stage, 'started_at': timestamp(), 'timeout_seconds': timeout}
        state['events'].append(event)
        atomic(state_path, state)
        process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   start_new_session=True)
        started = time.monotonic()
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt, InterruptedError):
            os.killpg(process.pid, signal.SIGTERM)
            try:
                stdout, stderr = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                stdout, stderr = process.communicate()
            event['interrupted_or_timed_out'] = True
        for suffix, body in [('stdout', stdout), ('stderr', stderr)]:
            path = private / (args.service + '-' + str(len(state['events'])) + '-' + stage + '.' + suffix)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(body)
            event[suffix + '_private_reference'] = str(path)
        event.update(exit_code=process.returncode, elapsed_seconds=round(time.monotonic() - started, 3),
                     finished_at=timestamp())
        atomic(state_path, state)
        return process.returncode, stdout, stderr, event

    def inspect(stage):
        return run(stage, [SKOPEO, '--command-timeout', '20s', 'inspect', '--raw', '--no-tags',
                   '--retry-times=0', '--tls-verify=' + tls] + inspect_auth +
                   ['docker://' + image['proposed_destination_tag']], 25)

    if args.action == 'publish':
        code, stdout, stderr, event = inspect('target-absence')
        require(not event.get('interrupted_or_timed_out') and confirmed_absent(code, stdout, stderr, image),
                'target is not conclusively absent; no write performed')
        require(local_metadata(DIRECTORY, image) == metadata, 'source changed after absence check')
        state.update(copy_attempts=1, copy_outcome='UNKNOWN_BEFORE_COPY_START', attempt_recorded_at=timestamp())
        atomic(state_path, state)  # durable before any registry write
        command = [SKOPEO, '--command-timeout', str(args.copy_timeout) + 's', 'copy',
                   '--preserve-digests', '--retry-times=0', '--image-parallel-copies=2',
                   '--dest-tls-verify=' + tls] + copy_auth + image['future_command_not_executed'][-2:]
        code, stdout, stderr, event = run('copy-once', command, args.copy_timeout + 5)
        state['copy_outcome'] = 'EXIT_ZERO_AWAITING_INDEPENDENT_READ' if code == 0 else 'UNKNOWN_RECONCILE_ONLY'
        atomic(state_path, state)
        require(code == 0 and not event.get('interrupted_or_timed_out'),
                'copy outcome unknown; original attempt retained, use reconcile only')
    code, stdout, stderr, event = inspect('independent-manifest-read')
    require(code == 0 and not event.get('interrupted_or_timed_out'),
            'digest read incomplete; original attempt retained, reconcile only')
    actual = 'sha256:' + digest(stdout)
    require(actual == image['expected_manifest_digest'], 'registry digest differs; never overwrite or retry')
    state.update(copy_outcome='PUBLISHED_DIGEST_VERIFIED', independently_read_digest=actual,
                 verified_at=timestamp())
    atomic(state_path, state)
    result = {'result': 'PUBLISHED_DIGEST_VERIFIED', 'metadata': metadata,
              'copy_attempts': state['copy_attempts'], 'plan_sha256': PLAN_SHA,
              'transport': args.transport, 'verified_at': state['verified_at'],
              'deployments': 0, 'registry_logins': 0, 'automatic_copy_retries': 0,
              'private_state_reference': str(state_path)}
    atomic(DIRECTORY / (args.service + '-published.json'), result, 0o644)
    return result


def self_test():
    image = {'proposed_destination_tag': ENDPOINT + '/ani/example:vm-task'}
    absent = b'time="2026-10-24T00:00:00Z" level=fatal msg="reading manifest vm-task in ' + \
        ENDPOINT.encode() + b'/ani/example: manifest unknown"\n'
    require(confirmed_absent(1, b'', absent, image), 'exact missing case')
    require(not confirmed_absent(1, b'', absent.replace(b'vm-task', b'other'), image), 'wrong tag accepted')
    require(not confirmed_absent(1, b'', b'unauthorized: manifest unknown', image), 'auth error accepted')
    require(not confirmed_absent(1, b'', b'HTTP 404 from proxy', image), 'generic 404 accepted')
    require(not confirmed_absent(0, b'{}', b'', image), 'existing target accepted')
    require(not confirmed_absent(-15, b'', absent, image), 'timeout accepted')
    base = dict(root_authorized=True, approved_plan_sha256=PLAN_SHA,
                approved_driver_sha256=file_sha(Path(__file__)), exclusive_new_task_tags=True,
                transport='strict-tls', approved_insecure_endpoint=None, auth_file=None,
                approved_auth_file_sha256=None, anonymous_authorized=True)
    approvals(argparse.Namespace(**base))
    for change in [dict(root_authorized=False), dict(approved_plan_sha256='0' * 64),
                   dict(transport='explicit-http'), dict(exclusive_new_task_tags=False)]:
        try:
            approvals(argparse.Namespace(**(base | change)))
        except RuntimeError:
            continue
        raise RuntimeError('unauthorized registry guard unexpectedly accepted')
    print(json.dumps({'result': 'PASS', 'pure_guard_cases': 11, 'registry_requests': 0}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('local-check', 'self-test', 'publish', 'reconcile'))
    parser.add_argument('--service', choices=SERVICES)
    parser.add_argument('--root-authorized', action='store_true')
    parser.add_argument('--approved-plan-sha256')
    parser.add_argument('--approved-driver-sha256')
    parser.add_argument('--exclusive-new-task-tags', action='store_true')
    parser.add_argument('--transport', choices=('strict-tls', 'explicit-http'), default='strict-tls')
    parser.add_argument('--approved-insecure-endpoint', help='approval for HTTP/unverified TLS; not automatic fallback')
    parser.add_argument('--auth-file', help='existing approved mode-0600 auth file; contents never printed')
    parser.add_argument('--approved-auth-file-sha256')
    parser.add_argument('--anonymous-authorized', action='store_true')
    parser.add_argument('--copy-timeout', type=int, choices=range(1, 181), default=120)
    args = parser.parse_args()
    def interrupt(signum, frame):
        raise InterruptedError('task driver interrupted; never resend an attempted copy')
    signal.signal(signal.SIGTERM, interrupt)
    if args.action == 'self-test':
        self_test()
        return 0
    require(args.service is not None, 'select one frozen service')
    require(os.environ.get('SSH_CONNECTION', '').split()[2:3] == ['172.16.101.31'], 'Fedora only')
    require(Path(__file__).resolve().parent == DIRECTORY, 'exact task driver directory required')
    with Path('/home/chabking/workspace/ani-network-service-runs/net05a-heavy.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('LOCK_BUSY')
            return 73
        image = load_plan(DIRECTORY)[args.service]
        metadata = local_metadata(DIRECTORY, image)
        if args.action == 'local-check':
            print(json.dumps({'result': 'LOCAL_INPUTS_VERIFIED', 'metadata': metadata, 'registry_requests': 0}))
        else:
            print(json.dumps(registry_action(args, image, metadata)))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError, ValueError) as error:
        # Never echo subprocess stderr or credential content to public output.
        print(json.dumps({'result': 'STOPPED', 'reason': str(error)}))
        raise SystemExit(1)
