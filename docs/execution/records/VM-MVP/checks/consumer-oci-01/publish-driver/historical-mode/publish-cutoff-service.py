"""Registry10: one formal service push, original-outcome readback, then restore proof."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
import urllib.request

T = Path('/home/chabking/workspace/modeldev-execution-v2.1-20261022-01a15f47')
D = T / 'deployment/registry10/modeldev'
B = T / 'deployment/cutoff-boundary-service-886afca610d1'
REVISION = '886afca610d16b92692c6dab8185f3a2c64dcb5b'
TREE = '59fa3e8d93b5fe21f7192e08863c831a918d0c62'
REPOSITORY = 'https://github.com/zhangzhe-ctrl/ani-modeldev-service'
REPO = 'ani/modeldev-service-execution-v21'
TAG = 'v21-886afca610d1'
REFERENCE = '172.16.101.10:5000/' + REPO + ':' + TAG
IMAGE_ID = '5adc7b6347d96097a31ee06130fc92d18da9f146a958331c7a9dc0eb75bf9f5f'
BINARY_SHA = '247f0dfa8f086787ad80fc16c32f83bb8ad9f91754d600013c7900f1db0eb87d'
BUILD_SHA = 'f71bc29f8d884565e8d342055a35461649fe065fcd817ba6b9b3496999dd69d9'
GATE_SHA = 'ee4248dfe78ed06719781ac594f4544f3d110e1ed710d8fb721920557e5bc5f4'
MANIFEST_SHA = '5e71e7dd4376e71c3f126fc9f765e8dd4dc88a82cb65539eb2abae230b1b7299'
MANIFEST_INPUT_SHA = 'c4569f6aac15122f0c47681e3d14fb4214c3b3e626b9fe84d866d99fe7733622'
UNIT_SHA = '599642ae7fb4b9628fec2ade787df7d7f553b6aac34580834bc0bbe54084d06b'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def durable(path, value):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    raw = value if isinstance(value, bytes) else json.dumps(value, sort_keys=True, indent=2).encode() + b'\n'
    with path.open('xb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    path.chmod(0o600)
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:os.fsync(descriptor)
    finally:os.close(descriptor)


def validate_proof(proof, gate, manifest):
    assert proof['state'] == 'FORMAL_IMAGE_BUILT_VERIFIED_NOT_PUSHED' and proof['component'] == 'service'
    assert proof['source_commit'] == REVISION and proof['source_tree'] == TREE and proof['source_repository'] == REPOSITORY
    assert proof['image_reference'] == REFERENCE and proof['image_id'] == IMAGE_ID
    assert proof['git_clean'] is True and proof['mandatory_gate'] == 'PASS' and proof['toolchain'] == 'go1.26.10'
    assert proof['runtime_user'] == '10001:10001' and proof['binary_readback_and_nonroot_smoke_passed'] is True
    assert proof['binary_sha256'] == {'ani-modeldev-service': BINARY_SHA}
    assert proof['temporary_verification_containers_removed'] and proof['registry_published'] is False
    assert proof['unchanged_webhook_steps_pipeline_not_rebuilt'] is True
    assert proof['gate_receipt_sha256'] == GATE_SHA and proof['file_manifest_sha256'] == MANIFEST_SHA
    assert proof['manifest_input_sha256'] == MANIFEST_INPUT_SHA
    assert gate['status'] == 'PASS' and gate['exit_code'] == 0 and gate['file_manifest_sha256'] == MANIFEST_SHA
    assert gate['source_bytes_unchanged_after_check'] is True and gate['file_count'] == 689
    assert manifest['file_manifest_sha256'] == MANIFEST_SHA and manifest['file_count'] == 689
    assert set(manifest['only_changed_paths']) == {'internal/data/finetuningmodel/live.go', 'internal/data/finetuningmodel/live_test.go'}
    labels = proof['source_labels']
    assert labels['org.opencontainers.image.revision'] == REVISION and labels['io.ani.source-tree'] == TREE
    assert labels['org.opencontainers.image.source'] == REPOSITORY


def load_proof():
    raw = (B / 'image-verification.json').read_bytes()
    assert sha(raw) == BUILD_SHA
    proof = json.loads(raw)
    gate_path = T / 'contracts/cutoff-boundary-review-fix/verify-receipt.json'
    assert proof['gate_receipt'] == str(gate_path)
    gate_raw = gate_path.read_bytes()
    manifest_raw = (gate_path.parent / 'source-01.json').read_bytes()
    assert sha(gate_raw) == GATE_SHA and sha(manifest_raw) == MANIFEST_INPUT_SHA
    validate_proof(proof, json.loads(gate_raw), json.loads(manifest_raw))
    assert sha((B / 'ani-modeldev-service').read_bytes()) == BINARY_SHA
    assert sha((B / 'image-binary').read_bytes()) == BINARY_SHA
    return proof


def validate_image(proof, image):
    assert image['Id'].removeprefix('sha256:') == IMAGE_ID
    assert image['Config']['User'] == '10001:10001' and image['Config']['Entrypoint'] == ['/ani-modeldev-service']
    assert image['Config']['Labels'] == proof['source_labels']
    assert image['RootFS']['Layers'] and all(re.fullmatch('sha256:[0-9a-f]{64}', item) for item in image['RootFS']['Layers'])


def active_window():
    root = T / 'evidence'
    assert not (root / 'registry-owner-window-10-readonly_restored.json').exists()
    opened = json.loads((root / 'registry-owner-window-10-write_window_open.json').read_bytes())
    assert opened['event'] == 'WRITE_WINDOW_OPEN' and opened['window'] == '10' and opened['original_unit_sha256'] == UNIT_SHA
    assert opened['automatic_restore_seconds'] == 1800
    assert datetime.now(timezone.utc) < datetime.fromisoformat(opened['expires_at'])
    identity = json.loads((root / 'registry-owner-window-10-snapshot.json').read_bytes())['identity']
    assert opened['identity'] == identity
    issued = json.loads((T / 'deployment/registry10/open-issued.json').read_bytes())
    assert issued['identity'] == identity and issued['original_unit_sha256'] == UNIT_SHA
    controller = Path('/proc') / str(opened['controller_pid'])
    assert (controller / 'stat').read_text().split(') ', 1)[1].split()[19] == opened['controller_start_ticks']
    return opened


def validate_restored(restored, identity, issued_at):
    assert restored['event'] == 'RESTORE_READBACK_ONLY' and restored['window'] == '10'
    assert restored['identity'] == identity and restored['original_unit_sha256'] == UNIT_SHA
    assert restored['runtime_dropin_removed'] is True and restored['task_state_absent'] is True and restored['mutations'] == 0
    assert datetime.fromisoformat(restored['observed_at']).timestamp() >= issued_at
    properties = dict(line.split('=', 1) for line in restored['properties'].splitlines())
    assert properties['ActiveState'] == 'active' and int(properties['MainPID']) > 0 and properties['DropInPaths'] == ''
    assert '--readonly=true' in properties['ExecStart'] and '--readonly=false' not in properties['ExecStart']


def get(url, method='GET'):
    headers = {'Accept': 'application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json'}
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers, method=method), timeout=30) as response:
        assert response.status == 200
        raw = response.read((16 << 20) + 1) if method == 'GET' else b''
        assert len(raw) <= 16 << 20
        return raw, dict(response.headers)


def readback(proof, image):
    base = 'http://172.16.101.10:5000/v2/' + REPO
    raw, headers = get(base + '/manifests/' + TAG)
    digest = 'sha256:' + sha(raw)
    digest_raw, digest_headers = get(base + '/manifests/' + digest)
    reads = []
    for alias, data, metadata in [(TAG, raw, headers), (digest, digest_raw, digest_headers)]:
        assert 'sha256:' + sha(data) == digest and metadata['Docker-Content-Digest'] == digest
        manifest = json.loads(data)
        assert manifest['schemaVersion'] == 2 and manifest['config']['digest'] == 'sha256:' + IMAGE_ID
        _, head = get(base + '/manifests/' + alias, 'HEAD')
        assert head['Docker-Content-Digest'] == digest
        reads.append({'requested_by': 'tag' if alias == TAG else 'digest', 'GET': 200, 'HEAD': 200, 'manifest_sha256': digest})
    config_raw, _ = get(base + '/blobs/sha256:' + IMAGE_ID)
    assert sha(config_raw) == IMAGE_ID
    config = json.loads(config_raw)
    assert config['config']['Labels'] == proof['source_labels'] and config['config']['User'] == '10001:10001'
    assert config['config']['Entrypoint'] == ['/ani-modeldev-service']
    assert config['rootfs']['diff_ids'] == image['RootFS']['Layers']
    for name, contents in [('manifest-tag.json', raw), ('manifest-digest.json', digest_raw), ('config.json', config_raw)]:
        path = D / name
        if path.exists():assert path.read_bytes() == contents
        else:durable(path, contents)
    return {'state': 'FORMAL_IMAGE_PUSH_TAG_DIGEST_CONFIG_SOURCE_READBACK_PASS', 'name': 'modeldev',
        'observed_at': datetime.now(timezone.utc).isoformat(), 'source_commit': REVISION, 'source_tree': TREE,
        'build_receipt': str(B / 'image-verification.json'), 'build_receipt_sha256': BUILD_SHA,
        'immutable_reference': '172.16.101.10:5000/' + REPO + '@' + digest, 'manifest_digest': digest,
        'config_digest': 'sha256:' + IMAGE_ID, 'source_labels': proof['source_labels'],
        'rootfs_diffids_equal': True, 'binary_readback_equal_source_build': True, 'reads': reads,
        'registry_readonly_restore_pending': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['push', 'readback-only', 'verify-restored'])
    action = parser.parse_args().action
    os.umask(0o077)
    proof = load_proof()
    D.mkdir(mode=0o700, parents=True, exist_ok=True)
    if action == 'verify-restored':
        issued = json.loads((D / 'push-issued.json').read_bytes())
        original = json.loads((T / 'evidence/registry-owner-window-10-snapshot.json').read_bytes())
        restored_path = T / 'evidence/registry-owner-window-10-restore_readback_only.json'
        restored = json.loads(restored_path.read_bytes())
        validate_restored(restored, original['identity'], issued['issued_at_unix'])
        receipt = json.loads((D / 'readback-receipt.json').read_bytes())
        assert receipt['source_commit'] == REVISION and receipt['source_tree'] == TREE and receipt['build_receipt_sha256'] == BUILD_SHA
        receipt.update(registry_readonly_restore_pending=False, registry_readonly_restore_receipt=str(restored_path),
            registry_readonly_restore_receipt_sha256=sha(restored_path.read_bytes()))
        final = D / 'registry-receipt.json'
        if final.exists():assert json.loads(final.read_bytes()) == receipt
        else:durable(final, receipt)
        print(json.dumps({**receipt, 'receipt': str(final)}), flush=True)
        return
    image = json.loads(subprocess.check_output(['podman', 'image', 'inspect', REFERENCE], timeout=30))[0]
    validate_image(proof, image)
    if action == 'push':
        window = active_window()
        assert not (D / 'readback-receipt.json').exists()
        assert not (D / 'registry.digest').exists() and not (D / 'push.log').exists()
        durable(D / 'push-issued.json', {'image_reference': REFERENCE, 'image_id': IMAGE_ID,
            'source_commit': REVISION, 'source_tree': TREE, 'build_receipt_sha256': BUILD_SHA,
            'window_identity': window['identity'], 'issued_at_unix': time.time()})
        with (D / 'push.log').open('x') as log:
            result = subprocess.run(['podman', 'push', '--tls-verify=false', '--digestfile', str(D / 'registry.digest'),
                IMAGE_ID, 'docker://' + REFERENCE], stdout=log, stderr=subprocess.STDOUT, timeout=1200)
        durable(D / 'push-exit.json', {'exit_code': result.returncode})
        assert result.returncode == 0, 'Unknown push: read back the original tag; never issue a second push'
    else:
        issued = json.loads((D / 'push-issued.json').read_bytes())
        assert issued['image_reference'] == REFERENCE and issued['image_id'] == IMAGE_ID and issued['build_receipt_sha256'] == BUILD_SHA
    receipt = readback(proof, image)
    digest_path = D / 'registry.digest'
    if digest_path.exists():assert digest_path.read_text().strip() == receipt['manifest_digest']
    path = D / 'readback-receipt.json'
    if path.exists():
        previous = json.loads(path.read_bytes())
        assert {k: v for k, v in previous.items() if k != 'observed_at'} == {k: v for k, v in receipt.items() if k != 'observed_at'}
        receipt = previous
    else:durable(path, receipt)
    print(json.dumps({**receipt, 'receipt': str(path), 'next': 'finally close window, independent restore-readback-only, then verify-restored'}), flush=True)


if __name__ == '__main__':
    main()
