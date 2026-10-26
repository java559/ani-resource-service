#!/usr/bin/env python3
"""Export only this publication's command logs, excluding credential state."""
import base64
import hashlib
import json
from pathlib import Path
import re

ROOT = Path('/home/chabking/workspace/ani-resource-service-runs/vm-mvp-20261023-01a168ce/consumer-oci-01/publish-driver')
DIRECTORY = ROOT / 'harbor-v2'
PRIVATE = DIRECTORY / 'private'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    auth_bytes = (PRIVATE / 'harbor-auth.json').read_bytes()
    encoded = json.loads(auth_bytes)['auths']['172.16.101.10:30003']['auth'].encode()
    decoded = base64.b64decode(encoded, validate=True)
    username, password = decoded.split(b':', 1)
    sensitive = {auth_bytes, encoded, decoded, username, password}
    sensitive |= {hashlib.sha256(value).hexdigest().encode() for value in sensitive}
    sensitive.add(json.loads((PRIVATE / 'authority.json').read_text())['authfile_sha256'].encode())
    sensitive = sorted((x for x in sensitive if x), key=len, reverse=True)
    target = DIRECTORY / 'public-command-logs'
    target.mkdir(exist_ok=False)
    evidence = {'result': 'PUBLIC_LOG_EXPORT', 'credential_values_or_hashes_exported': False,
                'raw_private_state_exported': False, 'commands': [], 'historical_error_bodies': []}

    def export(source, name):
        assert source.is_file() and not source.is_symlink() and source.resolve().is_relative_to(PRIVATE)
        data = source.read_bytes()
        original = data
        for value in sensitive:
            data = data.replace(value, b'<redacted-existing-registry-credential>')
        data = re.sub(rb'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+',
                      b'<redacted-token>', data)
        assert not any(value in data for value in sensitive)
        output = target / name
        output.write_bytes(data)
        return {'path': str(output.relative_to(DIRECTORY)), 'sha256': sha(data),
                'bytes': len(data), 'redacted': data != original,
                'byte_identical_to_original_when_not_redacted': data == original}

    for service in ('resource', 'session', 'governance'):
        state = json.loads((PRIVATE / (service + '.json')).read_text())
        assert state['copy_attempts'] == 1 and state['copy_outcome'] == 'PUBLISHED_DIGEST_VERIFIED'
        for event in state['events']:
            record = {key: event[key] for key in ('stage', 'started_at', 'finished_at', 'exit_code',
                                                   'elapsed_seconds', 'timeout_seconds')}
            record['service'] = service
            record['stdout'] = export(Path(event['stdout_private_reference']), service + '-' + event['stage'] + '.stdout')
            record['stderr'] = export(Path(event['stderr_private_reference']), service + '-' + event['stage'] + '.stderr')
            evidence['commands'].append(record)
    for name in ('http-1.body', 'authority-preflight-continued-http-4.body',
                 'authority-preflight-ready-http-2.body', 'authority-preflight-ready-http-4.body',
                 'skopeo-resource-absence.stdout', 'skopeo-resource-absence.stderr'):
        source = PRIVATE / name
        if source.exists():
            evidence['historical_error_bodies'].append(export(source, 'historical-' + name))
    (DIRECTORY / 'public-command-evidence.json').write_text(json.dumps(evidence, indent=2) + '\n')
    # Check only this exact publication's public closure, never other tasks or
    # private directories. Print filenames/counts only, never credential bytes.
    checked = []
    for path in ROOT.rglob('*'):
        if not path.is_file() or 'private' in path.relative_to(ROOT).parts or '__pycache__' in path.parts:
            continue
        body = path.read_bytes()
        assert not any(value in body for value in sensitive), 'private value in public file: ' + str(path.relative_to(ROOT))
        checked.append(str(path.relative_to(ROOT)))
    result = {'result': 'PASS', 'public_files_checked': len(checked),
              'command_log_pairs': len(evidence['commands']),
              'historical_logs': len(evidence['historical_error_bodies']),
              'redacted_files': sum(item[stream]['redacted'] for item in evidence['commands']
                                    for stream in ('stdout', 'stderr')) +
                                sum(item['redacted'] for item in evidence['historical_error_bodies']),
              'credential_content_or_hash_public': False}
    (DIRECTORY / 'public-export-check.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
