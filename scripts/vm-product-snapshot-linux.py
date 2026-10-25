"""Task-owned V3 snapshot admissions; execute only in Root's approved window.

prepare/capture/preview/observe perform reads (plus normal same-user login).
command requires explicit authorization. Persisted intent bodies are immutable;
an unknown ACK can only be retried with --recover-admission and the original key.
Guest marker writes and disk-set preparation remain independent evidence.
"""
from pathlib import Path
import argparse
import ast
import contextlib
import fcntl
import hashlib
import json
import io
import re
import runpy
import shlex
import subprocess
import time
import uuid

RUN = Path('/home/chabking/workspace/ani-resource-service-runs/vm-mvp-20261023-01a168ce')
LOCK = Path('/home/chabking/workspace/ani-network-service-runs/net05a-heavy.lock')
VM = '55099440-c47b-4f81-afc5-7d8e740d14dc'
TENANT = 'b3c946b5-7eb3-4feb-9f82-79712062ca5c'
NAMESPACE = 'tenant-' + TENANT
NAMESPACE_UID = '09e58ecb-fe20-454f-bc7b-2da3c406f0ac'
VM_UID = '7bca4671-89dd-4925-bad9-17e77401729d'
CLUSTER_UID = '5277649d-e28d-4a0a-ae76-8354365c63bc'
KUBECTL = '/home/chabking/ani-four-service-deploy-20261012/tools/kubectl'
KUBECONFIG = '/home/chabking/ani-four-service-deploy-20261012/private/kubeconfig'
BASE = '/api/v1/virtual-machines/' + VM
FIVE_ACTIONS = {'restore-attached', 'restore-detached', 'restore-deleted',
                'delete-added-attached', 'retain-detached'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def ref(value):
    metadata = value['metadata']
    return {'kind': value['kind'], 'namespace': metadata.get('namespace', ''),
            'name': metadata['name'], 'uid': metadata['uid']}


def owns(value, uid):
    return any(owner.get('uid') == uid and owner.get('controller') is True
               for owner in value['metadata'].get('ownerReferences', []))


def validate_preview(value, expected):
    assert set(expected) == FIVE_ACTIONS, 'all five disk-set cases must be named explicitly'
    actual = {}
    for disk in value['disks']:
        assert disk['action'] in FIVE_ACTIONS
        actual.setdefault(disk['action'], []).append(disk['diskId'])
    assert {key: sorted(actual.get(key, [])) for key in FIVE_ACTIONS} == {
        key: sorted(expected[key]) for key in FIVE_ACTIONS}, 'restore impact differs from the frozen five sets'
    assert all(expected[key] for key in FIVE_ACTIONS), 'a required disk-set case is empty'


def validate_offline_preview(value):
    expected = {'restore-attached': ['5dcd6eee-e30c-5dd2-b72b-f7d0d5699d21',
                                     'd296c9f6-2e76-5756-bb4c-def2d0f8f2f9',
                                     '5dbc2c93-9865-54f9-8413-f198f5e9c1a6'],
                'retain-detached': ['dd773137-07f9-5b7a-9511-fda3dcf09001']}
    actual = {}
    for disk in value['disks']:
        actual.setdefault(disk['action'], []).append(disk['diskId'])
    assert {key: sorted(ids) for key, ids in actual.items()} == {
        key: sorted(ids) for key, ids in expected.items()}, 'offline preview changed a protected disk set'


def self_test():
    tree = ast.parse(Path(__file__).read_text())
    guest_source = next(node.value.func.value.value for node in ast.walk(tree)
                        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'guest' for t in node.targets)
                        and isinstance(node.value, ast.Call))
    compile(guest_source.replace('INPUT', repr((VM, [('d296c9f6-2e76-5756-bb4c-def2d0f8f2f9', 'DATA3'),
                                                    ('5dbc2c93-9865-54f9-8413-f198f5e9c1a6', 'DATA4')], True, False, False))), '<guest-b>', 'exec')
    expected = {action: [str(uuid.uuid5(uuid.NAMESPACE_OID, action))] for action in FIVE_ACTIONS}
    preview = {'disks': [{'diskId': ids[0], 'action': action} for action, ids in expected.items()]}
    validate_preview(preview, expected)
    malformed = json.loads(json.dumps(preview))
    malformed['disks'][0]['action'] = 'restore-attached' if malformed['disks'][0]['action'] != 'restore-attached' else 'restore-deleted'
    try:
        validate_preview(malformed, expected)
    except AssertionError:
        pass
    else:
        raise AssertionError('five-set mismatch was accepted')
    offline = {'disks': [{'diskId': disk, 'action': action} for action, disks in {
        'restore-attached': ['5dcd6eee-e30c-5dd2-b72b-f7d0d5699d21', 'd296c9f6-2e76-5756-bb4c-def2d0f8f2f9',
                             '5dbc2c93-9865-54f9-8413-f198f5e9c1a6'],
        'retain-detached': ['dd773137-07f9-5b7a-9511-fda3dcf09001']}.items() for disk in disks]}
    validate_offline_preview(offline)
    malformed = json.loads(json.dumps(offline)); malformed['disks'][-1]['action'] = 'delete-added-attached'
    try:
        validate_offline_preview(malformed)
    except AssertionError:
        pass
    else:
        raise AssertionError('offline preview accepted deletion of protected D6')
    original = {'data': {'idempotencyKey': 'original', 'expectedVersion': '20'}}
    assert digest(original) == digest(json.loads(json.dumps(original)))
    changed = json.loads(json.dumps(original)); changed['data']['expectedVersion'] = '21'
    assert digest(original) != digest(changed)
    print(json.dumps({'result': 'PASS', 'fixture': 'five-set exactness, offline D6 protection and immutable admission body',
                      'api_requests': 0, 'business_mutations': 0}))


class Driver:
    def __init__(self):
        self.helper = runpy.run_path(str(RUN / 'gov-candidate.py'))
        self.api, self.save = self.helper['api'], self.helper['save']
        self.private = self.helper['PRIVATE']
        self.path = self.private / 'linux-snapshots-v3.json'
        self.state = json.loads(self.path.read_text()) if self.path.exists() else {
            'vm_id': VM, 'native_vm_uid': VM_UID, 'commands': {}, 'snapshots': {}, 'previews': {}}
        assert self.state['vm_id'] == VM and self.state['native_vm_uid'] == VM_UID
        # Existing credentials only: this driver cannot bootstrap a new principal.
        credentials = json.loads((self.private / 'credentials.json').read_text())
        self.token = self.api('POST', '/api/v1/auth/password/login', {
            'tenant_name': credentials['tenant_code'], 'username': credentials['tenant_username'],
            'password': credentials['tenant_password']})['access_token']
        self.api('GET', '/api/v1/admin/me', token=self.token)

    def get(self, suffix=''):
        return self.api('GET', BASE + suffix, token=self.token)

    def kubectl(self, resource, name=None, namespace=NAMESPACE):
        args = [KUBECTL, '--kubeconfig=' + KUBECONFIG, '--request-timeout=10s', 'get', resource]
        if namespace:
            args += ['-n', namespace]
        if name:
            args += [name, '--ignore-not-found']
        args += ['-o', 'json']
        result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
        assert result.returncode == 0, 'bounded native read failed: ' + resource
        return json.loads(result.stdout) if result.stdout.strip() else None

    def persist(self, intent, value):
        self.save(self.path, self.state)
        public = {'vm_id': VM, 'native_vm_uid': VM_UID, 'intent': intent,
                  'driver_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  'private_state_reference': str(self.path), **value}
        self.save(RUN / ('evidence/gov-linux-snapshot-v3-' + intent + '.json'), public)
        return public

    def native(self, disks):
        assert self.kubectl('namespace', 'kube-system', namespace='')['metadata']['uid'] == CLUSTER_UID
        namespace = self.kubectl('namespace', NAMESPACE, namespace='')
        assert namespace['metadata']['uid'] == NAMESPACE_UID
        assert namespace['metadata']['labels']['network.ani.io/tenant-id'] == TENANT
        vm = self.kubectl('virtualmachine.kubevirt.io', 'vm-' + VM)
        assert vm['metadata']['uid'] == VM_UID
        labels = vm['metadata']['labels']
        assert labels['ani.io/tenant-id'] == TENANT and labels['vm.ani.io/vm-id'] == VM
        vmi = self.kubectl('virtualmachineinstance.kubevirt.io', 'vm-' + VM)
        if vmi:
            assert owns(vmi, VM_UID)
        pods = self.kubectl('pods')['items']
        launchers = [pod for pod in pods if vmi and owns(pod, vmi['metadata']['uid'])
                     and pod['metadata'].get('deletionTimestamp') is None
                     and pod['metadata'].get('labels', {}).get('kubevirt.io') == 'virt-launcher']
        assert len(launchers) <= 1, 'ambiguous primary launcher'
        template = vm['spec']['template']['spec']
        volumes = {volume['name']: volume for volume in template.get('volumes', [])}
        result = {'namespace': ref(namespace), 'vm': ref(vm),
                  'template_sha256': digest(template), 'run_strategy': vm['spec'].get('runStrategy'),
                  'vmi': None if not vmi else {**ref(vmi), 'phase': vmi.get('status', {}).get('phase'),
                                               'node': vmi.get('status', {}).get('nodeName')},
                  'launcher': None if not launchers else ref(launchers[0]), 'disks': []}
        for disk in disks:
            if not disk.get('attached'):
                continue
            volume = volumes[disk['volumeName']]
            dv = None
            if 'dataVolume' in volume:
                dv = self.kubectl('datavolumes.cdi.kubevirt.io', volume['dataVolume']['name'])
                assert dv and owns(dv, VM_UID), 'disk DV ownership differs'
                pvc_name = dv['metadata']['name']
            else:
                pvc_name = volume['persistentVolumeClaim']['claimName']
            pvc = self.kubectl('pvc', pvc_name)
            assert pvc and (owns(pvc, dv['metadata']['uid']) if dv else owns(pvc, VM_UID)), 'disk PVC ownership differs'
            assert pvc['spec']['volumeMode'] == 'Block' and pvc['spec']['accessModes'] == ['ReadWriteOnce']
            pv = self.kubectl('pv', pvc['spec']['volumeName'], namespace='')
            claim = pv['spec']['claimRef']
            assert claim['uid'] == pvc['metadata']['uid'] and claim['namespace'] == NAMESPACE and claim['name'] == pvc_name
            csi = pv['spec']['csi']; attributes = csi.get('volumeAttributes', {})
            result['disks'].append({'disk_id': disk['diskId'], 'role': disk['role'],
                'volume_name': disk['volumeName'], 'serial': disk['serial'],
                'vmi_volume_status': [] if not vmi else [
                    {key: item[key] for key in ('name', 'phase', 'target', 'hotplugVolume') if key in item}
                    for item in vmi.get('status', {}).get('volumeStatus', []) if item['name'] == disk['volumeName']],
                'vmi_hotpluggable': None if not vmi else any(
                    item.get(kind, {}).get('hotpluggable') is True
                    for item in vmi['spec'].get('volumes', []) if item['name'] == disk['volumeName']
                    for kind in ('dataVolume', 'persistentVolumeClaim')),
                'dv': None if not dv else {**ref(dv), 'phase': dv.get('status', {}).get('phase')},
                'pvc': {**ref(pvc), 'phase': pvc.get('status', {}).get('phase'),
                        'volume_mode': pvc['spec']['volumeMode'], 'access_modes': pvc['spec']['accessModes']},
                'pv': ref(pv), 'csi': {'driver': csi['driver'], 'volume_handle': csi['volumeHandle'],
                                     'pool': attributes.get('pool'), 'image_name': attributes.get('imageName')}})
        return result

    def capture(self, intent, initial=False):
        current, disks = self.get(), self.get('/disks').get('items', [])
        assert current['id'] == VM and not current['closing']
        native = self.native(disks)
        snapshots = self.get('/snapshots').get('items', [])
        value = {'phase': 'READ_ONLY_PREPARED' if initial else 'READ_ONLY_CAPTURE',
                 'current': current, 'disks': disks, 'native': native, 'snapshots': snapshots,
                 'business_mutations': 0}
        if initial and 'baseline' not in self.state:
            assert int(current['version']) == 20 and current['observedState'] == 'Running'
            assert len(disks) == 3 and all(disk['attached'] for disk in disks)
            assert sorted(disk['role'] for disk in disks) == ['data', 'data', 'system']
            previous = json.loads((self.private / 'linux-disks-v3.json').read_text())
            for label in ('data3', 'data4'):
                assert previous['plans'][label]['disk_id'] in {disk['diskId'] for disk in disks}
            self.state['baseline'] = value
        return self.persist(intent, value)

    def preview(self, mode, expected_path):
        snapshot = self.state['snapshots'][mode]
        current = self.get()
        assert current['observedState'] == 'Stopped' and not current['closing']
        data = {'snapshotId': snapshot['snapshotId']}
        value = self.api('POST', BASE + '/snapshots/' + snapshot['snapshotId'] + '/restore-preview', {'data': data}, self.token)
        assert self.helper['EVENTS'][-1]['status'] == 200
        assert int(value['version']) == int(current['version']) and value['snapshotId'] == snapshot['snapshotId']
        assert re.fullmatch('[0-9a-f]{64}', value['previewDigest'])
        if expected_path:
            path = Path(expected_path).resolve(strict=True)
            assert path.is_relative_to(self.private.resolve()), 'expected disk sets must be task-private'
            validate_preview(value, json.loads(path.read_text()))
        if mode == 'offline':
            validate_offline_preview(value)
        self.state['previews'][mode] = value
        return self.persist('preview-' + mode, {'phase': 'READ_ONLY_PREVIEW', 'preview': value,
                            'five_sets_checked': bool(expected_path), 'offline_sets_checked': mode == 'offline',
                            'business_mutations': 0})

    def snapshot_native(self, mode):
        snapshot = self.state['snapshots'][mode]
        assert snapshot['ready'] and snapshot['completeness'] == 'COMPLETE'
        command = next(item for item in self.state['commands'].values()
                       if item['action'] == 'snapshot' and item['mode'] == mode)
        assert command['operation']['state'] == 'succeeded'
        native = self.kubectl('virtualmachinesnapshot.snapshot.kubevirt.io', 'vm-snap-' + snapshot['snapshotId'])
        assert native['status']['sourceUID'] == VM_UID and native['status']['readyToUse']
        content = self.kubectl('virtualmachinesnapshotcontent.snapshot.kubevirt.io',
                              native['status']['virtualMachineSnapshotContentName'])
        assert content['spec']['virtualMachineSnapshotName'] == native['metadata']['name']
        assert content['spec']['source']['virtualMachine']['metadata']['uid'] == VM_UID
        assert content['status']['readyToUse']
        backups = {item['volumeName']: item for item in content['spec']['volumeBackups']}
        assert set(native['status']['snapshotVolumes']['includedVolumes']) == {d['volumeName'] for d in snapshot['disks']}
        volumes = []
        for disk in snapshot['disks']:
            backup = backups[disk['volumeName']]
            source = backup['persistentVolumeClaim']['metadata']
            vs = self.kubectl('volumesnapshot.snapshot.storage.k8s.io', backup['volumeSnapshotName'])
            assert vs['spec']['source']['persistentVolumeClaimName'] == source['name']
            assert vs['status']['readyToUse'] and any(
                item.get('apiVersion') == 'snapshot.kubevirt.io/v1beta1'
                and item.get('kind') == 'VirtualMachineSnapshotContent'
                and item.get('name') == content['metadata']['name']
                and item.get('uid') == content['metadata']['uid']
                for item in vs['metadata'].get('ownerReferences', []))
            vsc = self.kubectl('volumesnapshotcontent.snapshot.storage.k8s.io',
                               vs['status']['boundVolumeSnapshotContentName'], namespace='')
            assert vsc['spec']['volumeSnapshotRef']['uid'] == vs['metadata']['uid']
            assert vsc['status']['readyToUse'] and vsc['status']['snapshotHandle']
            cls = self.kubectl('volumesnapshotclass.snapshot.storage.k8s.io',
                               vs['spec']['volumeSnapshotClassName'], namespace='')
            assert cls['metadata']['uid'] == '4ef77654-dcae-4cca-b709-0d570426cf4e'
            assert cls['deletionPolicy'] == vsc['spec']['deletionPolicy'] == 'Delete'
            assert cls['driver'] == vsc['spec']['driver'] == 'rook-ceph.rbd.csi.ceph.com'
            volumes.append({'disk_id': disk['diskId'], 'volume_name': disk['volumeName'],
                            'source_pvc': {key: source[key] for key in ('name', 'namespace', 'uid')},
                            'volume_snapshot': {**ref(vs), 'status': vs['status']},
                            'volume_snapshot_content': {**ref(vsc), 'status': vsc['status']},
                            'snapshot_class': {**ref(cls), 'deletion_policy': cls['deletionPolicy'], 'driver': cls['driver']}})
        indications = native['status'].get('indications', [])
        if mode == 'offline':
            assert command['before']['observedState'] == 'Stopped' and 'Online' not in indications
        return self.persist(mode + '-a-native', {'phase': 'READ_ONLY_NATIVE_SNAPSHOT',
                            'snapshot_id': snapshot['snapshotId'], 'operation_id': command['operation_id'],
                            'snapshot': {**ref(native), 'status': native['status']},
                            'content': {**ref(content), 'status': content['status']}, 'volumes': volumes,
                            'consistency': 'OFFLINE_STOPPED' if mode == 'offline' else 'ONLINE_NO_GUEST_AGENT_CRASH_CONSISTENT',
                            'business_mutations': 0})

    def guest_b(self, authorized, read_a=False, absent_d3=False, offline_c=False):
        assert authorized or read_a, 'Guest writes require the separately approved execution window'
        import pexpect
        observer = runpy.run_path(str(RUN / 'vm-product-guest-observe.py'))
        current, disks = self.get(), self.get('/disks').get('items', [])
        assert current['observedState'] == 'Running'
        native = self.native(disks)
        if read_a or offline_c:
            assert self.state['commands']['online-a-restore']['operation']['state'] == 'succeeded'
            assert native['vmi']['uid'] != '257f1a0d-2abc-4eaf-b14f-ec49a29431b0'
            assert not next(d for d in disks if d['diskId'] == 'dd773137-07f9-5b7a-9511-fda3dcf09001')['attached']
            if offline_c:
                snapshot = self.state['snapshots']['offline']
                assert snapshot['ready'] and snapshot['completeness'] == 'COMPLETE'
                assert self.state['commands']['offline-a-create']['operation']['state'] == 'succeeded'
        else:
            assert int(current['version']) == 21
            original_shape = json.loads(json.dumps(native))
            # The initial capture predates these optional observation fields.
            for disk in original_shape['disks']:
                disk.pop('vmi_volume_status', None); disk.pop('vmi_hotpluggable', None)
            assert original_shape == self.state['baseline']['native'], 'original three-disk native chain changed'
            assert native['vmi']['uid'] == '257f1a0d-2abc-4eaf-b14f-ec49a29431b0'
        virtctl = observer['VIRTCTL']
        assert hashlib.sha256(virtctl.read_bytes()).hexdigest() == observer['VIRTCTL_SHA']
        credentials = json.loads((self.private / 'linux-guest-credentials-v3.json').read_text())
        assert credentials['username'] == 'aniuser'
        password = credentials['password']
        data_disks = [disk for disk in disks if disk['role'] == 'data' and disk['attached']]
        expected = {'5dbc2c93-9865-54f9-8413-f198f5e9c1a6'}
        if not absent_d3:
            expected.add('d296c9f6-2e76-5756-bb4c-def2d0f8f2f9')
        assert {disk['serial'] for disk in data_disks} == expected
        guest = r'''import json,os,pathlib,stat,subprocess
vm, disks, read_a, absent_d3, offline_c = INPUT
r=lambda p:pathlib.Path(p).read_text().strip()
assert r('/sys/class/dmi/id/product_uuid')==vm and r('/var/lib/cloud/data/instance-id')==vm
all_disks=json.loads(subprocess.check_output(['lsblk','-b','-J','-o','NAME,PATH,SERIAL,TYPE,SIZE,FSTYPE,MOUNTPOINTS']))['blockdevices']
if absent_d3:assert not any(d.get('serial')=='d296c9f6-2e76-5756-bb4c-def2d0f8f2f9' for d in all_disks)
checked=[]
for serial,label in disks:
 found=[d for d in all_disks if d.get('serial')==serial and d['type']=='disk']
 assert len(found)==1
 d=found[0];p=d['path'];assert p=='/dev/'+d['name'] and stat.S_ISBLK(os.stat(p).st_mode)
 assert not d.get('children') and not d.get('fstype') and not any(d.get('mountpoints') or [])
 assert not list(pathlib.Path('/sys/class/block',d['name'],'holders').iterdir())
 a=('VM_MVP_V3_'+label+'_A_01a168ce\n').encode();b=('VM_MVP_V3_'+label+'_'+('C' if offline_c else 'B')+'_01a168ce\n').encode()
 assert len(a)==len(b)==27
 with open(p,'rb',buffering=0) as f:before=f.read(512)
 assert before[:27]==a
 checked.append((serial,label,p,b,before))
root=pathlib.Path('/home/aniuser/vm-mvp-snapshot-root')
assert root.is_file() and not root.is_symlink() and root.stat().st_size<128
assert root.read_text().strip()=='VM_MVP_SNAPSHOT_A_ROOT_01a168ce'
if not read_a:
 for serial,label,p,b,before in checked:
  subprocess.run(['dd','of='+p,'bs=27','count=1','conv=notrunc,fsync','status=none'],input=b,check=True)
  subprocess.run(['blockdev','--flushbufs',p],check=True)
  with open(p,'rb',buffering=0) as f:after=f.read(512)
  assert after==b+before[27:]
 with root.open('w') as f:f.write('VM_MVP_SNAPSHOT_'+('C' if offline_c else 'B')+'_ROOT_01a168ce\n');f.flush();os.fsync(f.fileno())
 os.sync()
assert root.read_text().strip()=='VM_MVP_SNAPSHOT_'+('A' if read_a else ('C' if offline_c else 'B'))+'_ROOT_01a168ce'
print(json.dumps({'result':'PASS','read_only':read_a,'d3_absent':absent_d3,'root_marker':root.read_text().strip(),'boot_id':r('/proc/sys/kernel/random/boot_id'),'disks':[{'disk_id':s,'serial':s,'device':p,'marker':(old[:27] if read_a else b).decode().strip(),'offset':0,'bytes_written':0 if read_a else 27,'outside_range_unchanged':True,'flushed':not read_a,'offline_or_unmounted':True,'guest_prepared_confirmation':not read_a} for s,l,p,b,old in checked]}))
'''.replace('INPUT', repr((VM, [(d['serial'], 'DATA3' if d['serial'].startswith('d296') else 'DATA4') for d in data_disks], read_a, absent_d3, offline_c)))
        child = pexpect.spawn(str(virtctl), ['console', 'vm-' + VM, '--namespace', NAMESPACE,
                              '--kubeconfig', KUBECONFIG, '--timeout=20'], encoding='utf-8', timeout=35)
        result = {'result': 'FAIL', 'vm_id': VM, 'method': 'NATIVE_SERIAL', 'native': native,
                  'authentication_sent': False, 'guest_program_sent': False, 'stage': 'CONNECTING'}
        try:
            child.expect(r'Successfully connected to .* console\. Press Ctrl\+\] or Ctrl\+5 to exit console\.')
            result['stage'] = 'WAIT_LOGIN_PROMPT'
            child.sendcontrol('c'); child.sendline('')
            if child.expect([r'login:', r'\$ ']) == 1:
                child.sendline('exit'); child.expect(r'login:')
            child.sendline(credentials['username']); child.expect(r'[Pp]assword:')
            result.update(authentication_sent=True, stage='WAIT_AUTHENTICATED_SHELL')
            child.sendline(password); child.expect(r'\$ ', timeout=90)
            result['stage'] = 'SHELL_READY'
            child.sendline("sudo -v -p 'ANI_SUDO_PASSWORD: '")
            if child.expect([r'(?:^|[\r\n])ANI_SUDO_PASSWORD: ', r'\$ ']) == 0:
                child.sendline(password); child.expect(r'\$ ')
            import base64, zlib
            encoded = base64.b64encode(zlib.compress(guest.encode())).decode()
            line = 'import base64,zlib;exec(zlib.decompress(base64.b64decode(' + repr(encoded) + ')))'
            command = 'sudo -n python3 -c ' + shlex.quote(line)
            assert len(command.encode()) < 4000
            result.update(guest_program_sent=True, stage='WAIT_GUEST_PROGRAM_RESULT')
            child.sendline(command); child.expect(r'\$ ')
            values = []
            for line in child.before.splitlines():
                try:
                    value = json.loads(line.strip())
                    if isinstance(value, dict) and value.get('root_marker') == 'VM_MVP_SNAPSHOT_' + ('A' if read_a else ('C' if offline_c else 'B')) + '_ROOT_01a168ce':
                        values.append(value)
                except json.JSONDecodeError:
                    pass
            assert len(values) == 1 and values[0]['result'] == 'PASS', 'guest fixed markers did not confirm'
            result.update(values[0]); result['stage'] = 'GUEST_RESULT_CONFIRMED'
            child.sendline('exit'); child.expect(r'login:')
        except Exception as error:
            result['error'] = str(error).replace(password, '[REDACTED]')[-1000:]
        finally:
            child.close(force=True)
        key = 'guest-d3-absent' if absent_d3 else ('guest-offline-c' if offline_c else ('guest-read-a' if read_a else 'guest-b'))
        self.state[key.replace('-', '_')] = result
        for disk in [] if read_a else result.get('disks', []):
            proof = {**disk, 'result': result['result'], 'vm_id': VM, 'method': 'NATIVE_SERIAL',
                     'supporting_evidence': {'native': native, 'root_marker': result['root_marker']}}
            self.save(RUN / ('evidence/gov-linux-snapshot-v3-' + key + '-' + disk['disk_id'] + '.json'), proof)
        receipt = self.persist(key,
                               {'phase': 'GUEST_A_READBACK' if read_a else ('GUEST_C_READBACK' if offline_c else 'GUEST_B_READBACK'), 'guest': result})
        assert result['result'] == 'PASS', result.get('error')
        return receipt

    def recover_guest_c(self, restored_a=False, absent_d3=False, prepare_d3=False):
        import base64, pexpect, zlib
        assert not prepare_d3 or (restored_a and not absent_d3)
        current, disks = self.get(), self.get('/disks').get('items', [])
        assert current['observedState'] == 'Running'
        native = self.native(disks)
        assert native['vmi']['phase'] == 'Running'
        if restored_a:
            restore = self.state['commands']['offline-a-restore']
            assert restore['operation']['state'] == 'succeeded'
            assert all(restore['owner_checkpoints']['snapshot'][key] is True for key in
                       ('NativeRestoreCompleted', 'MappingCommitted', 'CleanupCompleted'))
            assert native['vmi']['uid'] != self.state['guest_c_recovery']['native']['vmi']['uid']
            assert not next(d for d in disks if d['diskId'] == 'dd773137-07f9-5b7a-9511-fda3dcf09001')['attached']
            assert next(d for d in disks if d['diskId'] == 'd296c9f6-2e76-5756-bb4c-def2d0f8f2f9')['attached'] is (not absent_d3)
            expected_marker = 'A'
        else:
            assert not absent_d3
            prior = self.state['guest_offline_c']
            assert prior['authentication_sent'] and prior['guest_program_sent']
            assert prior['stage'] == 'WAIT_GUEST_PROGRAM_RESULT' and prior['result'] == 'FAIL'
            assert int(current['version']) == 35 and native['vmi']['uid'] == prior['native']['vmi']['uid']
            frozen = lambda value: {d['disk_id']: (d['serial'], d['pvc']['uid'], d['pv']['uid'], d['csi']) for d in value['disks']}
            assert frozen(native) == frozen(prior['native'])
            expected_marker = 'C'
        observer = runpy.run_path(str(RUN / 'vm-product-guest-observe.py'))
        virtctl = observer['VIRTCTL']
        assert hashlib.sha256(virtctl.read_bytes()).hexdigest() == observer['VIRTCTL_SHA']
        credentials = json.loads((self.private / 'linux-guest-credentials-v3.json').read_text())
        assert credentials['username'] == 'aniuser'
        password = credentials['password']
        # Original writes used a 512-byte marker padded with zeroes. All later
        # authorized snapshot writes changed only its first 27 bytes.
        guest = r'''import hashlib,json,pathlib,stat,subprocess
expected_marker = EXPECTED_MARKER
absent_d3 = ABSENT_D3
prepare_d3 = PREPARE_D3
r=lambda p:pathlib.Path(p).read_text().strip()
assert r('/sys/class/dmi/id/product_uuid')=='55099440-c47b-4f81-afc5-7d8e740d14dc'
assert r('/var/lib/cloud/data/instance-id')=='55099440-c47b-4f81-afc5-7d8e740d14dc'
all_disks=json.loads(subprocess.check_output(['lsblk','-b','-J','-o','NAME,PATH,SERIAL,TYPE,SIZE,FSTYPE,MOUNTPOINTS']))['blockdevices']
if absent_d3:assert not any(d.get('serial')=='d296c9f6-2e76-5756-bb4c-def2d0f8f2f9' for d in all_disks)
values=[]
for serial,label in ([('5dbc2c93-9865-54f9-8413-f198f5e9c1a6','DATA4')] if absent_d3 else [('d296c9f6-2e76-5756-bb4c-def2d0f8f2f9','DATA3'),('5dbc2c93-9865-54f9-8413-f198f5e9c1a6','DATA4')]):
 found=[d for d in all_disks if d.get('serial')==serial and d['type']=='disk'];assert len(found)==1
 d=found[0];p=d['path'];assert p=='/dev/'+d['name'] and stat.S_ISBLK(pathlib.Path(p).stat().st_mode)
 assert not d.get('children') and not d.get('fstype') and not any(d.get('mountpoints') or [])
 assert not list(pathlib.Path('/sys/class/block',d['name'],'holders').iterdir())
 if prepare_d3 and label=='DATA3':subprocess.run(['blockdev','--flushbufs',p],check=True)
 with open(p,'rb',buffering=0) as f:actual=f.read(512)
 markers={('VM_MVP_V3_'+label+'_'+tag+'_01a168ce\n').encode():tag for tag in 'ABC'}
 values.append({'disk_id':serial,'serial':serial,'device':p,'marker_state':markers.get(actual[:27],'UNKNOWN'),'tail_sha256':hashlib.sha256(actual[27:]).hexdigest(),'original_485_byte_tail_equal':actual[27:]==bytes(485),'flushed':prepare_d3 and label=='DATA3','offline_or_unmounted':True,'guest_prepared_confirmation':prepare_d3 and label=='DATA3','bytes_written':0})
root=pathlib.Path('/home/aniuser/vm-mvp-snapshot-root');assert root.is_file() and not root.is_symlink() and root.stat().st_size<128
known={'VM_MVP_SNAPSHOT_'+tag+'_ROOT_01a168ce':tag for tag in 'ABC'}
root_state=known.get(root.read_text().strip(),'UNKNOWN')
ok=root_state==expected_marker and all(d['marker_state']==expected_marker and d['original_485_byte_tail_equal'] for d in values)
print(json.dumps({'result':'PASS' if ok else 'PARTIAL','read_only':not prepare_d3,'d3_absent':absent_d3,'root_marker_state':root_state,'boot_id':r('/proc/sys/kernel/random/boot_id'),'disks':values,'guest_bytes_written':0}))
'''.replace('EXPECTED_MARKER', repr(expected_marker)).replace('ABSENT_D3', repr(absent_d3)).replace('PREPARE_D3', repr(prepare_d3))
        compile(guest, '<read-only-snapshot-marker>', 'exec')
        result = {'result': 'UNKNOWN', 'method': 'AUTHENTICATED_NATIVE_SERIAL' if restored_a else 'AUTHENTICATED_EXISTING_NATIVE_SERIAL', 'native': native,
                  'read_only': True, 'ctrl_c_sent': False, 'logout_sent': False, 'guest_bytes_written': 0,
                  'tail_basis': 'Initial product guest write padded each marker to 512 bytes with zeroes; subsequent writes changed only bytes 0-26.',
                  'expected_marker': expected_marker, 'stage': 'WAIT_ORIGINAL_PROGRAM_OR_SHELL'}
        child = pexpect.spawn(str(virtctl), ['console', 'vm-' + VM, '--namespace', NAMESPACE,
                              '--kubeconfig', KUBECONFIG, '--timeout=20'], encoding='utf-8', timeout=120)
        deadline = time.monotonic() + 180
        def wait(pattern, seconds):
            remaining = deadline - time.monotonic()
            assert remaining > 0, 'read-only recovery total 180s budget exhausted'
            return child.expect(pattern, timeout=min(seconds, remaining))
        try:
            wait(r'Successfully connected to .* console\. Press Ctrl\+\] or Ctrl\+5 to exit console\.', 20)
            try:
                prompt = wait([r'\$ ', r'login:'] if restored_a else r'\$ ', 10)
            except pexpect.TIMEOUT:
                # A blank line queues a shell prompt after a still-running
                # program. It sends no signal and cannot interrupt os.sync().
                child.sendline(''); result['blank_line_sent'] = True
                prompt = wait([r'\$ ', r'login:'] if restored_a else r'\$ ', 120)
            if restored_a and prompt == 1:
                child.sendline(credentials['username']); wait(r'[Pp]assword:', 35)
                child.sendline(password); result['authentication_sent'] = True
                wait(r'\$ ', 90)
            result['original_shell_returned'] = True
            child.sendline("sudo -v -p 'ANI_SUDO_PASSWORD: '")
            if wait([r'(?:^|[\r\n])ANI_SUDO_PASSWORD: ', r'\$ '], 90) == 0:
                child.sendline(password); wait(r'\$ ', 90)
            line = 'import base64,zlib;exec(zlib.decompress(base64.b64decode(' + repr(base64.b64encode(zlib.compress(guest.encode())).decode()) + ')))'
            command = 'sudo -n python3 -c ' + shlex.quote(line)
            assert len(command.encode()) < 4000
            result['stage'] = 'WAIT_READ_ONLY_' + expected_marker + '_RESULT'
            child.sendline(command); wait(r'\$ ', 120)
            values = []
            for line in child.before.splitlines():
                try:
                    value = json.loads(line.strip())
                    if isinstance(value, dict) and 'root_marker_state' in value:
                        values.append(value)
                except json.JSONDecodeError:
                    pass
            assert len(values) == 1, 'read-only marker result not complete'
            result.update(values[0]); result['stage'] = 'D3_GUEST_PREPARED' if prepare_d3 else 'READ_ONLY_' + expected_marker + '_CONFIRMED'
        except Exception as error:
            result['error'] = str(error).replace(password, '[REDACTED]')[-1000:]
        finally:
            child.close(force=True)
        key = 'guest-offline-d3-prepared' if prepare_d3 else ('guest-offline-d3-absent' if absent_d3 else ('guest-offline-a-readback' if restored_a else 'guest-offline-c-recovery'))
        self.state[key.replace('-', '_') if restored_a else 'guest_c_recovery'] = result
        if prepare_d3 and result['result'] == 'PASS':
            disk = next(d for d in result['disks'] if d['disk_id'] == 'd296c9f6-2e76-5756-bb4c-def2d0f8f2f9')
            assert disk['flushed'] and disk['guest_prepared_confirmation']
            self.save(RUN / ('evidence/gov-linux-snapshot-v3-' + key + '-proof.json'),
                      {**disk, 'result': 'PASS', 'vm_id': VM, 'method': 'NATIVE_SERIAL',
                       'supporting_evidence': {'native': native, 'root_marker_state': result['root_marker_state'],
                                               'boot_id': result['boot_id']}})
        return self.persist(key,
                            {'phase': 'GUEST_D3_PREPARED' if prepare_d3 else ('GUEST_A_READ_ONLY_READBACK' if restored_a else 'GUEST_C_READ_ONLY_RECOVERY'), 'guest': result})

    def disk(self, args):
        assert args.execute_authorized and args.disk_action and args.disk_label and args.intent
        assert self.state.get('guest_b', {}).get('result') == 'PASS'
        helper = runpy.run_path(str(RUN / 'gov-disks-v3.py'))
        existing = helper['load']()
        if args.disk_label in ('data5', 'data6') and args.disk_label not in existing['plans']:
            existing['plans'][args.disk_label] = {'name': 'vm-mvp-snapshot-' + args.disk_label + '-v3', 'size_gb': 2}
            self.save(self.private / 'linux-disks-v3.json', existing)
        proof = None
        if args.disk_action == 'detach':
            disk_id = existing['plans'][args.disk_label]['disk_id']
            proof = args.guest_prepared_evidence or str(RUN / ('evidence/gov-linux-snapshot-v3-guest-b-' + disk_id + '.json'))
        if args.intent == 'offline-a-restored-reattach-data3' and args.intent not in existing['commands']:
            assert args.disk_action == 'attach' and args.disk_label == 'data3'
            absent = self.state['guest_offline_d3_absent']
            initial = self.state['guest_offline_a_readback']
            assert absent['result'] == 'PASS' and absent['d3_absent'] and absent['root_marker_state'] == 'A'
            assert absent['boot_id'] == initial['boot_id'] and absent['native']['vmi']['uid'] == initial['native']['vmi']['uid']
            before = json.loads((RUN / 'evidence/gov-linux-snapshot-v3-offline-disks-after-restore.json').read_text())['disks']
            current = self.storage('offline-a-detached-storage')['disks']
            physical = lambda rows: {d['ID']: (d['Serial'], d['observed_pvc'], d['observed_pv'], d['csi'])
                                     for d in rows if not d['Tombstone']}
            assert physical(before) == physical(current), 'reattach must preserve all four existing physical volumes'
            assert all(not d['Attached'] for d in current if d['ID'] in
                       ('d296c9f6-2e76-5756-bb4c-def2d0f8f2f9', 'dd773137-07f9-5b7a-9511-fda3dcf09001'))
        with contextlib.redirect_stdout(io.StringIO()):
            helper['run'](argparse.Namespace(action=args.disk_action, label=args.disk_label,
                          intent=args.intent, guest_prepared_evidence=proof, wait_seconds=args.wait_seconds))
        # Existing disk facility owns its command journal and public receipt.
        path = RUN / ('evidence/gov-linux-disk-v3-' + args.intent + '.json')
        value = json.loads(path.read_text())
        return {'phase': 'EXISTING_DISK_DRIVER_RETURNED', 'intent': args.intent,
                'command': value['command'], 'receipt': str(path)}

    def storage(self, intent):
        sql = "SELECT record FROM vm.disks WHERE tenant_id='" + TENANT + "' AND vm_id='" + VM + "' ORDER BY disk_id;"
        rows = self.helper['psql']('postgres', sql, database='vm_resource_candidate_01a168ce').splitlines()
        result = []
        for raw in rows:
            disk = json.loads(raw)
            value = {key: disk[key] for key in ('ID', 'Role', 'Serial', 'Attached', 'Tombstone', 'DV', 'PVC', 'Cleanup')}
            if disk['Tombstone']:
                result.append(value); continue
            dv = self.kubectl('datavolumes.cdi.kubevirt.io', disk['DV']['Name']) if disk['DV']['Name'] else None
            if disk['DV']['UID']:
                assert dv and dv['metadata']['uid'] == disk['DV']['UID'] and owns(dv, VM_UID)
            pvc_name = disk['PVC']['Name'] or disk['DV']['Name']
            pvc = self.kubectl('pvc', pvc_name)
            assert pvc and (owns(pvc, dv['metadata']['uid']) if dv else owns(pvc, VM_UID))
            if disk['PVC']['UID']:
                assert pvc['metadata']['uid'] == disk['PVC']['UID']
            value['observed_pvc'] = {**ref(pvc), 'phase': pvc.get('status', {}).get('phase'),
                                     'volume_mode': pvc['spec'].get('volumeMode'),
                                     'access_modes': pvc['spec'].get('accessModes')}
            assert pvc['spec'].get('volumeMode') == 'Block' and pvc['spec'].get('accessModes') == ['ReadWriteOnce']
            if pvc['spec'].get('volumeName'):
                pv = self.kubectl('pv', pvc['spec']['volumeName'], namespace='')
                assert pv['spec']['claimRef']['uid'] == pvc['metadata']['uid']
                csi = pv['spec']['csi']; attributes = csi.get('volumeAttributes', {})
                value['observed_pv'] = ref(pv)
                value['csi'] = {'driver': csi['driver'], 'volume_handle': csi['volumeHandle'],
                                'pool': attributes.get('pool'), 'image_name': attributes.get('imageName')}
            result.append(value)
        return self.persist(intent, {'phase': 'READ_ONLY_ALL_DISK_STORAGE', 'disks': result,
                                     'current': self.get(), 'business_mutations': 0})

    def cleanup(self, mode='online'):
        command = self.state['commands'][mode + '-a-restore']
        assert command['operation']['state'] == 'succeeded'
        checkpoints = command['owner_checkpoints']['snapshot']
        assert all(checkpoints[key] is True for key in ('NativeRestoreCompleted', 'MappingCommitted', 'CleanupCompleted'))
        if mode == 'online':
            initial = self.state['baseline']['native']['disks']
            before = json.loads((RUN / 'evidence/gov-linux-snapshot-v3-five-sets-before-restore.json').read_text())['disks']
            d5 = next(disk for disk in before if disk['ID'] == '45d5a224-42b7-526d-90eb-239577d2262a')
            retired = list(initial) + [{'disk_id': d5['ID'], 'dv': {key.lower(): value for key, value in d5['DV'].items()},
                                      'pvc': d5['observed_pvc'], 'pv': d5['observed_pv'], 'csi': d5['csi']}]
        else:
            initial = json.loads((RUN / 'evidence/gov-linux-snapshot-v3-offline-c-stopped.json').read_text())['native']['disks']
            before = json.loads((RUN / 'evidence/gov-linux-snapshot-v3-offline-c-storage-before-restore.json').read_text())['disks']
            retired = list(initial)
        remaining, native = [], []
        for disk in retired:
            for kind, resource in (('dv', 'datavolumes.cdi.kubevirt.io'), ('pvc', 'pvc'), ('pv', 'pv')):
                old = disk[kind]
                if old is None:
                    continue
                obj = self.kubectl(resource, old['name'], '' if kind == 'pv' else NAMESPACE)
                item = {'disk_id': disk['disk_id'], 'old': old, 'gone': obj is None}
                if obj:
                    item['current'] = ref(obj); remaining.append(kind + ':' + old['name'])
                native.append(item)
        backend = runpy.run_path(str(RUN / 'vm-product-import-delete-backend.py'))
        adapter = {'K': [KUBECTL, '--kubeconfig=' + KUBECONFIG, '--request-timeout=10s'],
                   'native': lambda kind, name, namespace: self.kubectl(kind, name, namespace)}
        names = [disk['csi']['image_name'] for disk in retired]
        root_name = next(disk['csi']['image_name'] for disk in retired if disk['disk_id'] == '5dcd6eee-e30c-5dd2-b72b-f7d0d5699d21')
        if mode == 'online':
            names.append(root_name + '-temp')  # Already identified old V3 root CSI family.
        images = [backend['image_info'](adapter, name) for name in names]
        raw = backend['ceph'](adapter, ['rbd', 'trash', 'ls', 'ani-block-pool', '--format=json'])
        assert raw is not None
        trash = [item for item in json.loads(raw) if item.get('name') in names]
        remaining += [item['name'] for item in images if item['result'] != 'ABSENT']
        remaining += ['trash:' + item['id'] for item in trash]
        current_storage = self.storage('five-sets-after-restore' if mode == 'online' else 'offline-disks-after-restore')['disks']
        d6old = next(disk for disk in before if disk['ID'] == 'dd773137-07f9-5b7a-9511-fda3dcf09001')
        d6 = next(disk for disk in current_storage if disk['ID'] == d6old['ID'])
        assert not d6['Attached'] and not d6['Tombstone']
        assert all(d6[key] == d6old[key] for key in ('observed_pvc', 'observed_pv', 'csi'))
        for disk in initial:
            restored = next(item for item in current_storage if item['ID'] == disk['disk_id'])
            assert restored['Attached'] and not restored['Tombstone'] and restored['Serial'] == disk['serial']
            assert restored['observed_pvc']['uid'] != disk['pvc']['uid']
        key = 'restore_cleanup' if mode == 'online' else 'offline_restore_cleanup'
        self.state[key] = {'result': 'PASS' if not remaining else 'PENDING_NORMAL_GC',
            'native': native, 'backend_images': images, 'matching_trash': trash, 'remaining': remaining,
            'd6_detached_original_volume_preserved': True, 'three_original_logical_ids_restored_to_new_pvcs': True,
            'force_or_manual_backend_delete': False}
        return self.persist(mode + '-a-cleanup', {'phase': 'RESTORE_PHYSICAL_CLEANUP',
                            'cleanup': self.state[key], 'business_mutations': 0})

    def command(self, args):
        assert args.execute_authorized, 'business mutation requires the separately approved execution window'
        assert args.intent and re.fullmatch('[a-z0-9][a-z0-9-]{0,79}', args.intent)
        command = self.state['commands'].get(args.intent)
        if command is None:
            assert not args.recover_admission
            for prior in self.state['commands'].values():
                assert prior.get('operation', {}).get('state') in ('succeeded', 'failed'), 'observe the unresolved original intent first'
            current = self.get(); assert current['id'] == VM and not current['closing']
            data = {'idempotencyKey': str(uuid.uuid4()), 'expectedVersion': str(current['version'])}
            route, method = BASE + '/' + args.action, 'POST'
            command = {'action': args.action, 'mode': args.mode, 'before': current,
                       'send_attempts': 0, 'ack_unknown': False}
            if args.action == 'snapshot':
                assert args.mode not in self.state['snapshots'], 'mode already owns a snapshot'
                assert current['observedState'] == ('Running' if args.mode == 'online' else 'Stopped')
                data['name'] = 'vm-mvp-linux-v3-' + args.mode
                route = BASE + '/snapshots'
                command['snapshot_ids_before'] = [item['snapshotId'] for item in self.get('/snapshots').get('items', [])]
                command['inventory'] = [disk for disk in self.get('/disks').get('items', []) if disk['attached']]
            elif args.action in ('stop', 'start'):
                assert current['observedState'] == ('Running' if args.action == 'stop' else 'Stopped')
            else:
                snapshot = self.state['snapshots'][args.mode]
                data['snapshotId'] = snapshot['snapshotId']
                route = BASE + '/snapshots/' + snapshot['snapshotId']
                if args.action == 'restore':
                    assert current['observedState'] == 'Stopped'
                    preview = self.state['previews'][args.mode]
                    assert int(preview['version']) == int(current['version'])
                    data.update(previewDigest=preview['previewDigest'], destructiveConfirmation=True)
                    route += '/restore'
                else:
                    assert args.action == 'delete-snapshot'; method = 'DELETE'
            command.update(body={'data': data}, method=method, route=route)
            command['body_sha256'] = digest(command['body'])
            self.state['commands'][args.intent] = command
            self.persist(args.intent, {'phase': 'INTENT_FROZEN', 'command': command})
        assert command['action'] == args.action and command['mode'] == args.mode
        assert digest(command['body']) == command['body_sha256']
        if 'operation_id' not in command:
            assert command['send_attempts'] == 0 or (args.recover_admission and command['ack_unknown']), 'unknown ACK: use explicit original-admission recovery only'
            command['send_attempts'] += 1; command['ack_unknown'] = True
            self.persist(args.intent, {'phase': 'SEND_DURABLE', 'command': command})
            try:
                accepted = self.api(command['method'], command['route'], command['body'], self.token)
            except self.helper['ProductAPIError'] as error:
                command['http_error'] = {'status': error.status, 'reason': error.reason}
                self.persist(args.intent, {'phase': 'EXPLICIT_HTTP_ERROR', 'command': command})
                raise
            # Persist the actual ACK before validating its contract.
            command['accepted'] = accepted; command['http_status'] = self.helper['EVENTS'][-1]['status']
            self.persist(args.intent, {'phase': 'ACK_RECEIVED', 'command': command})
            assert command['http_status'] == 202 and accepted['vmId'] == VM
            assert str(uuid.UUID(accepted['id'])) == accepted['id']
            command.update(operation_id=accepted['id'], ack_unknown=False)
            self.persist(args.intent, {'phase': 'ACK_PINNED', 'command': command})
        return self.observe(args.intent, args.wait_seconds)

    def observe(self, intent, wait_seconds):
        command = self.state['commands'][intent]
        assert 'operation_id' in command, 'ACK unknown: do not guess the operation ID'
        deadline = time.monotonic() + wait_seconds
        while True:
            operation = self.get('/operations/' + command['operation_id'])
            assert operation['id'] == command['operation_id'] and operation['vmId'] == VM
            command['operation'], command['current'] = operation, self.get()
            command['disks'] = self.get('/disks').get('items', [])
            if command['action'] == 'snapshot':
                fresh = [item for item in self.get('/snapshots').get('items', [])
                         if item['snapshotId'] not in command['snapshot_ids_before']]
                assert len(fresh) <= 1, 'new snapshot identity is ambiguous'
                if fresh:
                    snapshot = fresh[0]
                    assert snapshot['sourceVmId'] == VM and snapshot['sourceVmUid'] == VM_UID
                    assert {disk['diskId'] for disk in snapshot['disks']} == {disk['diskId'] for disk in command['inventory']}
                    original = self.state['snapshots'].get(command['mode'], snapshot)
                    assert original['snapshotId'] == snapshot['snapshotId']
                    self.state['snapshots'][command['mode']] = snapshot
                    command['snapshot'] = snapshot
            if command['action'] == 'restore':
                oid = str(uuid.UUID(command['operation_id']))
                sql = "SELECT json_build_object('id',id,'state',state,'step',record->>'Step','snapshot',record->'Snapshot') FROM vm.operations WHERE tenant_id='" + TENANT + "' AND vm_id='" + VM + "' AND id='" + oid + "';"
                raw = self.helper['psql']('postgres', sql, database='vm_resource_candidate_01a168ce').strip()
                assert raw
                command['owner_checkpoints'] = json.loads(raw)
            receipt = self.persist(intent, {'phase': 'ORIGINAL_OPERATION_OBSERVED', 'command': command})
            if operation['state'] in ('succeeded', 'failed') or time.monotonic() >= deadline:
                return receipt
            time.sleep(2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=('prepare', 'capture', 'storage', 'cleanup', 'preview', 'snapshot-native', 'command', 'observe', 'guest-b', 'guest-read-a', 'guest-d3-absent', 'guest-offline-c', 'guest-recover-c', 'guest-read-offline-a', 'guest-offline-d3-absent', 'guest-offline-d3-prepare', 'disk', 'self-test'))
    parser.add_argument('--mode', choices=('online', 'offline'), default='online')
    parser.add_argument('--action', choices=('snapshot', 'stop', 'restore', 'start', 'delete-snapshot'))
    parser.add_argument('--intent')
    parser.add_argument('--disk-action', choices=('create', 'attach', 'detach', 'delete'))
    parser.add_argument('--disk-label', choices=('data3', 'data4', 'data5', 'data6'))
    parser.add_argument('--guest-prepared-evidence')
    parser.add_argument('--expected-five-sets')
    parser.add_argument('--execute-authorized', action='store_true')
    parser.add_argument('--recover-admission', action='store_true')
    parser.add_argument('--wait-seconds', type=int, choices=range(31), default=0)
    args = parser.parse_args()
    if args.phase == 'self-test':
        self_test(); return
    with LOCK.open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({'phase': 'LOCK_BUSY', 'business_mutations': 0})); raise SystemExit(73)
        driver = Driver()
        if args.phase in ('prepare', 'capture'):
            result = driver.capture('prepared' if args.phase == 'prepare' else args.intent or 'capture', args.phase == 'prepare')
        elif args.phase == 'preview':
            result = driver.preview(args.mode, args.expected_five_sets)
        elif args.phase == 'snapshot-native':
            result = driver.snapshot_native(args.mode)
        elif args.phase == 'command':
            assert args.action; result = driver.command(args)
        elif args.phase in ('guest-b', 'guest-read-a', 'guest-d3-absent', 'guest-offline-c'):
            result = driver.guest_b(args.execute_authorized, args.phase in ('guest-read-a', 'guest-d3-absent'),
                                    args.phase == 'guest-d3-absent', args.phase == 'guest-offline-c')
        elif args.phase == 'guest-recover-c':
            result = driver.recover_guest_c()
        elif args.phase == 'guest-read-offline-a':
            result = driver.recover_guest_c(restored_a=True)
        elif args.phase == 'guest-offline-d3-absent':
            result = driver.recover_guest_c(restored_a=True, absent_d3=True)
        elif args.phase == 'guest-offline-d3-prepare':
            assert args.execute_authorized
            result = driver.recover_guest_c(restored_a=True, prepare_d3=True)
        elif args.phase == 'disk':
            result = driver.disk(args)
        elif args.phase == 'storage':
            assert args.intent; result = driver.storage(args.intent)
        elif args.phase == 'cleanup':
            result = driver.cleanup(args.mode)
        else:
            assert args.intent; result = driver.observe(args.intent, args.wait_seconds)
        # Full, non-secret evidence is on disk; stdout stays concise.
        print(json.dumps({'phase': result['phase'], 'vm_id': VM, 'intent': result['intent'],
                          'version': result.get('current', result.get('command', {}).get('current', {})).get('version'),
                          'operation': result.get('command', {}).get('operation'),
                          'receipt': result.get('receipt', str(RUN / ('evidence/gov-linux-snapshot-v3-' + result['intent'] + '.json')))}))


if __name__ == '__main__':
    main()
