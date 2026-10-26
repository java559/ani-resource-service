"""Exactly one bounded native trash list; publish only seven historical identities."""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import selectors
import subprocess
import time

K = ['/home/chabking/ani-four-service-deploy-20261012/tools/kubectl', '--kubeconfig=/home/chabking/ani-four-service-deploy-20261012/private/kubeconfig', '--request-timeout=8s']


def bounded_list(command, limit):
    # Neither the complete pool list nor stderr is written to disk or printed.
    p = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    s = selectors.DefaultSelector()
    s.register(p.stdout, selectors.EVENT_READ, 'stdout')
    s.register(p.stderr, selectors.EVENT_READ, 'stderr')
    data, errors, state = bytearray(), 0, 'COMPLETE'
    end = time.monotonic() + 10
    try:
        while s.get_map():
            if time.monotonic() >= end:
                state = 'TIMEOUT_NOT_MEASURED'; break
            for key, _ in s.select(min(0.1, max(0, end - time.monotonic()))):
                remaining = limit - len(data) if key.data == 'stdout' else 65536
                if remaining == 0:
                    state = 'OUTPUT_LIMIT_NOT_MEASURED'; break
                part = os.read(key.fileobj.fileno(), min(65536, remaining))
                if not part:
                    s.unregister(key.fileobj); continue
                if key.data == 'stdout': data.extend(part)
                else: errors += len(part)
            if state != 'COMPLETE': break
        if state != 'COMPLETE':
            p.terminate()  # Only this owned read-only kubectl child.
        try: code = p.wait(timeout=max(0.1, end - time.monotonic()) if state == 'COMPLETE' else 1)
        except subprocess.TimeoutExpired:
            state = 'TIMEOUT_NOT_MEASURED'
            p.kill(); code = p.wait()  # Bound only the local request process; no cluster write.
        return state, code, bytes(data), errors
    finally:
        s.close(); p.stdout.close(); p.stderr.close()


def main():
    a = argparse.ArgumentParser(); a.add_argument('--inputs', required=True); a.add_argument('--inputs-sha256', required=True); a.add_argument('--output', required=True); args = a.parse_args()
    source = Path(args.inputs).read_bytes()
    assert hashlib.sha256(source).hexdigest() == args.inputs_sha256
    inputs = json.loads(source); out = Path(args.output)
    assert not out.exists(), 'No retry or overwrite of the single original attempt'
    known = inputs['known']
    assert len(known) == 7 and len({x['id'] for x in known}) == len({x['name'] for x in known}) == 7
    assert all(x['pool'] == inputs['pool'] == 'ani-block-pool' for x in known)
    r = {'state': 'STARTED', 'started_at': dt.datetime.now(dt.timezone.utc).isoformat(), 'inputs_sha256': args.inputs_sha256, 'observer_source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), 'sources': inputs['sources'], 'backend_writes': 0, 'metadata_rechecks': 0, 'list_send_attempts': 0, 'commands': [], 'observed': [{**x, 'trash': 'NOT_MEASURED'} for x in known]}
    try:
        t = inputs['tool_pod']
        for argv in [K + ['get', 'namespace', 'kube-system', '-o', 'json'], K + ['get', 'pod', t['name'], '-n', t['namespace'], '-o', 'json']]:
            result = subprocess.run(argv, capture_output=True, timeout=10)
            r['commands'].append({'argv': argv, 'exit': result.returncode})
            assert result.returncode == 0
            value = json.loads(result.stdout)
            if value['kind'] == 'Namespace':
                assert value['metadata']['uid'] == inputs['cluster_uid']
                r['cluster_uid'] = value['metadata']['uid']
            else:
                assert value['metadata']['uid'] == t['uid'] and value['status']['phase'] == 'Running' and not value['metadata'].get('deletionTimestamp')
                assert len(value['spec']['containers']) == 1 and value['spec']['containers'][0]['name'] == t['container']
                assert any(c['name'] == t['container'] and c.get('state', {}).get('running') for c in value['status']['containerStatuses'])
                r['tool_pod'] = t
        command = K + ['exec', '-n', t['namespace'], t['name'], '-c', t['container'], '--', 'rbd', 'trash', 'ls', '-p', inputs['pool'], '--format', 'json']
        r['list_send_attempts'] = 1
        start = time.monotonic()
        state, code, raw, stderr_bytes = bounded_list(command, inputs['stdout_limit_bytes'])
        r['commands'].append({'argv': command, 'exit': code, 'outcome': state, 'timeout_seconds': 10, 'stdout_limit_bytes': inputs['stdout_limit_bytes'], 'stdout_bytes': len(raw), 'stderr_bytes': stderr_bytes, 'elapsed_seconds': round(time.monotonic() - start, 3)})
        if state != 'COMPLETE' or code != 0:
            r['state'] = state if state != 'COMPLETE' else 'CLI_ERROR_NOT_MEASURED'
        else:
            entries = json.loads(raw); assert isinstance(entries, list)
            r['listed_entry_count'] = len(entries)
            for old in r['observed']:
                same_id = [e for e in entries if e.get('id') == old['id']]
                same_name = [e for e in entries if e.get('name') == old['name']]
                exact = [e for e in same_id if e.get('name') == old['name']]
                old['trash'] = 'PRESENT' if len(exact) == 1 and len(same_id) == len(same_name) == 1 else 'ABSENT' if not same_id and not same_name else 'IDENTITY_CONFLICT'
                old['exact_match_count'] = len(exact)
                if exact: old['matching_entry'] = {k: exact[0][k] for k in ['id', 'name', 'deletion_time', 'deferment_end'] if k in exact[0]}
            r['matched_known_count'] = sum(x['trash'] == 'PRESENT' for x in r['observed'])
            r['absent_known_count'] = sum(x['trash'] == 'ABSENT' for x in r['observed'])
            r['state'] = 'ALL_SEVEN_KNOWN_TRASH_ABSENT' if r['absent_known_count'] == 7 else 'PENDING_NORMAL_GC' if all(x['trash'] in ['PRESENT', 'ABSENT'] for x in r['observed']) else 'IDENTITY_CONFLICT_NOT_MEASURED'
    except Exception as e:
        r['state'] = 'GUARD_OR_READ_ERROR_NOT_MEASURED'; r['failure_type'] = type(e).__name__
    r['finished_at'] = dt.datetime.now(dt.timezone.utc).isoformat()
    descriptor = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w') as f: json.dump(r, f, indent=2); f.write('\n'); f.flush(); os.fsync(f.fileno())
    print(json.dumps({'state': r['state'], 'list_send_attempts': r['list_send_attempts'], 'known': len(known), 'present': r.get('matched_known_count'), 'absent': r.get('absent_known_count'), 'receipt': str(out)}))


if __name__ == '__main__': main()
