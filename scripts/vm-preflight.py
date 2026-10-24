#!/usr/bin/env python3
"""Read-only VM input inventory. Run on Fedora, never copies credentials."""
import argparse
import datetime
import hashlib
import json
import pathlib
import struct
import subprocess

p = argparse.ArgumentParser()
p.add_argument('--kubectl', required=True)
p.add_argument('--kubeconfig', required=True)
p.add_argument('--output', required=True)
p.add_argument('--media', action='append', default=[])
a = p.parse_args()
out = pathlib.Path(a.output)
out.mkdir(parents=True, exist_ok=True)
base = [a.kubectl, '--kubeconfig=' + a.kubeconfig]
def query(*args):
    return subprocess.check_output(base + list(args), text=True)
def save(name, value):
    (out / (name + '.json')).write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')
server = query('config', 'view', '--minify', '-o', 'jsonpath={.clusters[0].cluster.server}')
if server != 'https://172.16.101.10:6443':
    raise SystemExit('cluster server differs from approved 10–12 target')
nodes = json.loads(query('get', 'nodes', '-o', 'json'))
addresses = {x['address'] for n in nodes['items'] for x in n['status']['addresses'] if x['type'] == 'InternalIP'}
if addresses != {'172.16.101.10', '172.16.101.11', '172.16.101.12'}:
    raise SystemExit('node identity differs from approved 10–12 target')
identity = {'at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'server': server, 'context': query('config','current-context').strip(),
            'kube_system_uid': query('get','ns','kube-system','-o','jsonpath={.metadata.uid}'),
            'credential_reference': a.kubeconfig,
            'nodes': [{'name': n['metadata']['name'], 'uid': n['metadata']['uid'],
                       'addresses': n['status']['addresses'], 'info': n['status']['nodeInfo']} for n in nodes['items']]}
save('identity', identity)
resources = ['kubevirt -A', 'cdi', 'sc', 'storageprofiles', 'volumesnapshotclass',
             'network-attachment-definitions -A', 'namespaces', 'virtualmachines -A',
             'virtualmachineinstances -A', 'persistentvolumeclaims -A',
             'persistentvolumes', 'volumesnapshots -A', 'volumesnapshotcontents',
             'deployments -A', 'daemonsets -A', 'pods -A']
summary = {}
for resource in resources:
    name = resource.replace(' -A','')
    try:
        doc = json.loads(query('get', *resource.split(), '-o', 'json'))
        for item in doc.get('items',[]):
            item.get('metadata', {}).pop('managedFields',None)
        save(name, doc)
        summary[name] = {'count': len(doc.get('items',[])), 'status': 'READ'}
    except subprocess.CalledProcessError as e:
        summary[name] = {'status': 'READ_FAILED', 'exit_code': e.returncode}
media = []
for source in a.media:
    path = pathlib.Path(source)
    with path.open('rb') as f:
        head = f.read(4096)
        f.seek(0)
        digest = hashlib.file_digest(f, 'sha256').hexdigest()
    qcow = head[:4] == b'QFI\xfb'
    media.append({'path': str(path.resolve()), 'file_bytes': path.stat().st_size,
                  'sha256': digest, 'format': 'qcow2' if qcow else ('iso' if path.suffix == '.iso' else 'UNVERIFIED'),
                  'qcow_version': struct.unpack('>I',head[4:8])[0] if qcow else None,
                  'virtual_bytes': struct.unpack('>Q',head[24:32])[0] if qcow else None})
save('media', media)
save('summary', summary)
print(json.dumps({'identity': identity, 'resources':summary, 'media': media}, indent=2))
