#!/usr/bin/env python3
"""Task-only Windows product phases; never automate the installer or Guest.

Every prepare/execute needs Root's specific phase handoff. Preparing freezes one
fresh version/key/body, but sends no mutation. execute sends it at most once;
observe uses only its acknowledged operation. Unknown ACKs require an explicit
original-key recovery. No phase automatically invokes the next phase.
"""
import argparse
import copy
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import time
import uuid

RUN = Path('/home/chabking/workspace/ani-resource-service-runs/vm-mvp-20261023-01a168ce')
LOCK = Path('/home/chabking/workspace/ani-network-service-runs/net05a-heavy.lock')
PRIVATE = RUN / 'private/gov-candidate'
STATE = PRIVATE / 'windows-lifecycle.json'
VM = 'fc154879-8a40-4b18-967c-215f5cf5992d'
VM_UID = '9a751320-5f97-4e56-af6a-30964610321b'
TENANT = 'b3c946b5-7eb3-4feb-9f82-79712062ca5c'
NS = 'tenant-' + TENANT
NS_UID = '09e58ecb-fe20-454f-bc7b-2da3c406f0ac'
CLUSTER_UID = '5277649d-e28d-4a0a-ae76-8354365c63bc'
BASE = '/api/v1/virtual-machines/' + VM
DATA = {'data3': '89317f20-5600-510a-bc65-e40402c83abb',
        'data4': 'cca2005b-d936-5327-a5de-d8236385b9ba'}
ACTIONS = ('stop', 'start', 'eject-install', 'boot-system', 'attach', 'detach',
           'restart', 'force-stop', 'snapshot', 'restore', 'delete-snapshot')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def ref(value):
    return {'kind': value['kind'], **{key: value['metadata'].get(key, '')
                                    for key in ('namespace', 'name', 'uid')}}


def terminal(command):
    return command.get('operation', {}).get('state') in ('succeeded', 'failed')


def may_send(command):
    return command.get('send_attempts') == 0 and not command.get('operation_id') and not command.get('ack_unknown')


def identity(native):
    """Freeze owned objects/configuration, excluding runtime progress telemetry."""
    value = copy.deepcopy(native)
    if value.get('vmi'):
        value['vmi'].pop('volume_status', None)
    return value


def build(args, current, disks, preview=None):
    assert current['id'] == VM and current['osFamily'].lower() == 'windows' and not current['closing']
    assert args.root_handoff and re.fullmatch('[a-z0-9][a-z0-9-]{0,79}', args.intent)
    assert args.intent in args.root_handoff, 'Root handoff must name this exact intent'
    observed = current['observedState']
    assert observed in ('Running', 'Stopped'), 'wait for a stable original operation'
    body = {'data': {'expectedVersion': str(current['version']), 'idempotencyKey': str(uuid.uuid4())}}
    data = body['data']; method, route = 'POST', BASE + '/' + args.action
    if args.action in ('stop', 'restart', 'force-stop'):
        assert observed == 'Running'
        if args.action == 'stop':
            assert args.installation_complete, 'Root must confirm installation before discarding installer runtime'
        if args.action == 'force-stop':
            assert args.destructive_confirmed
            data['destructiveConfirmation'] = True
    elif args.action == 'start':
        assert observed == 'Stopped'
    elif args.action in ('eject-install', 'boot-system'):
        assert args.installation_complete and observed == 'Stopped'
        if args.action == 'eject-install':
            data['slot'] = 'INSTALL'; method, route = 'DELETE', BASE + '/media/INSTALL'
        else:
            install = next((m for m in current.get('media', []) if m['slot'] == 'INSTALL'), None)
            assert not install or not install.get('desiredMedia'), 'eject INSTALL through its original operation first'
            data['bootDevice'] = 'SYSTEM_DISK'; method, route = 'PUT', BASE + '/boot'
    elif args.action in ('attach', 'detach'):
        assert args.disk in DATA and args.disk_mode in ('cold', 'hot')
        assert observed == ('Stopped' if args.disk_mode == 'cold' else 'Running')
        disk = next(d for d in disks if d['diskId'] == DATA[args.disk])
        assert disk['role'] == 'data' and disk['serial'] == DATA[args.disk]
        assert disk['attached'] == (args.action == 'detach'), 'disk is already in the requested state'
        data['diskId'] = disk['diskId']
        route = BASE + '/disks/' + disk['diskId'] + '/' + args.action
        if args.action == 'detach' and observed == 'Running':
            assert args.guest_prepared_confirmed and args.root_evidence, 'Root must first offline/flush this exact Windows disk'
            data['guestPreparedConfirmation'] = True
    elif args.action == 'snapshot':
        assert args.snapshot_mode in ('online', 'offline')
        assert observed == ('Running' if args.snapshot_mode == 'online' else 'Stopped')
        data['name'] = 'vm-mvp-windows-' + args.intent
        route = BASE + '/snapshots'
    else:
        assert args.snapshot_id and str(uuid.UUID(args.snapshot_id)) == args.snapshot_id
        data['snapshotId'] = args.snapshot_id
        route = BASE + '/snapshots/' + args.snapshot_id
        if args.action == 'delete-snapshot':
            method = 'DELETE'
        else:
            assert args.action == 'restore' and observed == 'Stopped' and args.destructive_confirmed
            assert preview and args.preview_sha == digest(preview), 'Root must review this exact stopped preview'
            assert preview['snapshotId'] == args.snapshot_id and int(preview['version']) == int(current['version'])
            data.update(previewDigest=preview['previewDigest'], destructiveConfirmation=True)
            route += '/restore'
    return {'action': args.action, 'method': method, 'route': route, 'body': body,
            'body_sha256': digest(body), 'before': current, 'disks_before': disks,
            'root_handoff': args.root_handoff, 'root_evidence': args.root_evidence,
            'disk_mode': args.disk_mode, 'send_attempts': 0, 'ack_unknown': False,
            'prepared_at': now()}


class Driver:
    def __init__(self):
        assert os.environ.get('SSH_CONNECTION', '').split()[2:3] == ['172.16.101.31'], 'ssh fedora only'
        self.g = runpy.run_path(str(RUN / 'gov-candidate.py'))
        self.w = runpy.run_path(str(RUN / 'vm-product-windows-disks.py'))
        assert self.w['VM'] == VM and self.w['VMUID'] == VM_UID and self.w['NSUID'] == NS_UID
        self.api, self.save = self.g['api'], self.g['save']
        self.token = self.w['login'](self.g)
        self.api('GET', '/api/v1/admin/me', token=self.token)
        if STATE.exists():
            assert STATE.stat().st_mode & 0o077 == 0
            self.state = json.loads(STATE.read_text())
        else:
            self.state = {'vm_id': VM, 'native_vm_uid': VM_UID, 'commands': {}, 'previews': {}}
        assert self.state['vm_id'] == VM and self.state['native_vm_uid'] == VM_UID

    def get(self, suffix=''):
        return self.api('GET', BASE + suffix, token=self.token)

    def persist(self, intent, value):
        self.save(STATE, self.state)
        public = {'at': now(), 'vm_id': VM, 'native_vm_uid': VM_UID, 'intent': intent,
                  'source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  'private_state_reference': str(STATE), 'guest_commands': 0, **value}
        self.save(RUN / ('evidence/product-windows-lifecycle-' + intent + '.json'), public)
        return public

    def native(self, disks):
        get, owned = self.w['native'], self.w['owned']
        assert get('namespace', 'kube-system')['metadata']['uid'] == CLUSTER_UID
        ns = get('namespace', NS)
        assert ns['metadata']['uid'] == NS_UID and ns['metadata']['labels']['network.ani.io/tenant-id'] == TENANT
        vm = get('vm', 'vm-' + VM, NS)
        assert vm['metadata']['uid'] == VM_UID
        assert vm['metadata']['labels']['ani.io/tenant-id'] == TENANT
        assert vm['metadata']['labels']['vm.ani.io/vm-id'] == VM
        template = vm['spec']['template']['spec']
        assert vm['spec']['runStrategy'] == 'Manual' and template['domain']['firmware']['uuid'] == VM
        vmi = get('vmi', 'vm-' + VM, NS, optional=True)
        assert vmi is None or owned(vmi, 'VirtualMachine', VM_UID)
        pods = get('pods', '-l=kubevirt.io=virt-launcher', NS)['items']
        launchers = [p for p in pods if not p['metadata'].get('deletionTimestamp') and
                     any(o.get('kind') == 'VirtualMachineInstance' and o.get('controller') and
                         o.get('uid') == (vmi or {}).get('metadata', {}).get('uid')
                         for o in p['metadata'].get('ownerReferences', []))]
        assert len(launchers) <= 1
        if vmi and vmi.get('status', {}).get('phase') == 'Running':
            assert len(launchers) == 1
        storage = []
        original = json.loads((RUN / 'evidence/product-windows2025-iso-native.json').read_text())
        names = {p['ref']['name'] for p in original['pvcs']}
        sql = "SELECT record FROM vm.disks WHERE tenant_id='" + TENANT + "' AND vm_id='" + VM + "' ORDER BY disk_id;"
        records = {}
        for raw in self.g['psql']('postgres', sql, database='vm_resource_candidate_01a168ce').splitlines():
            record = json.loads(raw)
            if not record['Tombstone']:
                assert record['ID'] not in records
                records[record['ID']] = record
        current_refs, disk_names = {}, {}
        for disk in disks:
            record = records[disk['diskId']]
            assert (record['Role'], record['Serial'], record['Attached']) == (disk['role'], disk['serial'], disk['attached'])
            for physical in (record['PVC'], record['DV']):
                if physical['Name']:
                    assert physical['Namespace'] == NS
            name = record['PVC']['Name'] or record['DV']['Name']
            assert name and name not in current_refs, 'logical disk has no unique durable physical mapping'
            current_refs[name], disk_names[disk['diskId']] = record, name
            names.add(name)
            volume = next((v for v in template.get('volumes', []) if v['name'] == disk['volumeName']), None)
            if volume is not None:
                if 'dataVolume' in volume:
                    assert volume['dataVolume']['name'] == record['DV']['Name']
                else:
                    assert volume.get('persistentVolumeClaim', {}).get('claimName') == name
        for volume in template.get('volumes', []):
            if 'dataVolume' in volume:
                names.add(volume['dataVolume']['name'])
            elif 'persistentVolumeClaim' in volume:
                names.add(volume['persistentVolumeClaim']['claimName'])
        for name in sorted(names):
            pvc = get('pvc', name, NS, optional=True)
            record = current_refs.get(name)
            if record:
                assert pvc is not None, 'durable disk PVC is missing'
            if pvc is None:
                continue
            if record:
                dv_ref = record['DV']
                dv = get('dv', dv_ref['Name'], NS) if dv_ref['Name'] else None
                assert dv is None or owned(dv, 'VirtualMachine', VM_UID)
                if dv_ref['UID']:
                    assert dv and dv['metadata']['uid'] == dv_ref['UID']
                if record['PVC']['UID']:
                    assert pvc['metadata']['uid'] == record['PVC']['UID']
                assert owned(pvc, 'DataVolume', dv['metadata']['uid']) if dv else owned(pvc, 'VirtualMachine', VM_UID)
            else:
                dv = get('dv', name, NS, optional=True)
            assert owned(pvc, 'VirtualMachine', VM_UID) or dv and owned(dv, 'VirtualMachine', VM_UID) and owned(pvc, 'DataVolume', dv['metadata']['uid'])
            item = {'pvc': ref(pvc), 'phase': pvc.get('status', {}).get('phase'),
                    'mode': pvc['spec'].get('volumeMode'), 'dv': ref(dv) if dv else None}
            if pvc['spec'].get('volumeName'):
                pv = get('pv', pvc['spec']['volumeName'])
                assert pv['spec']['claimRef']['uid'] == pvc['metadata']['uid']
                item.update(pv=ref(pv), csi=pv['spec'].get('csi'), reclaim_policy=pv['spec']['persistentVolumeReclaimPolicy'])
            storage.append(item)
        for disk in disks:
            expected = disk_names[disk['diskId']]
            assert any(s['pvc']['name'] == expected for s in storage), 'logical disk has no verified current physical mapping'
        return {'vm': ref(vm), 'vmi': None if vmi is None else {**ref(vmi), 'phase': vmi.get('status', {}).get('phase'),
                    'node': vmi.get('status', {}).get('nodeName'), 'volume_status': vmi.get('status', {}).get('volumeStatus', [])},
                'launcher': ref(launchers[0]) if launchers else None, 'template': template,
                'template_sha256': digest(template), 'storage': storage}

    def capture(self, intent):
        current, disks = self.get(), self.get('/disks').get('items', [])
        assert current['id'] == VM and current['osFamily'].lower() == 'windows' and not current['closing']
        assert set(DATA.values()) <= {d['diskId'] for d in disks}
        native = self.native(disks)
        return self.persist(intent, {'phase': 'READ_ONLY_NATIVE_AND_STORAGE', 'current': current,
                                    'disks': disks, 'native': native, 'business_mutations': 0})

    def prepare(self, args):
        assert args.intent not in self.state['commands'], 'original intent exists: observe it instead'
        assert all(terminal(c) for c in self.state['commands'].values()), 'observe unresolved original intent first'
        current = self.get()
        previous = self.get('/operations/' + current['lastOperationId'])
        assert previous['vmId'] == VM and previous['state'] == 'succeeded'
        disks = self.get('/disks').get('items', [])
        command = build(args, current, disks, self.state['previews'].get(args.snapshot_id))
        command['native_before'] = self.native(disks)
        command['snapshot_ids_before'] = [s['snapshotId'] for s in self.get('/snapshots').get('items', [])] if args.action == 'snapshot' else []
        self.state['commands'][args.intent] = command
        return self.persist(args.intent, {'phase': 'PREPARED_SEND0', 'command': command, 'business_mutations': 0})

    def execute(self, args):
        assert args.execute_authorized and args.root_handoff
        command = self.state['commands'][args.intent]
        assert args.action is None or args.action == command['action']
        assert command['root_handoff'] == args.root_handoff and command['body_sha256'] == digest(command['body'])
        if command.get('operation_id'):
            return self.observe(args.intent, args.wait_seconds)
        assert may_send(command) or args.recover_admission and command['ack_unknown']
        if may_send(command):
            current = self.get()
            assert current['id'] == VM and not current['closing']
            assert int(current['version']) == int(command['body']['data']['expectedVersion'])
            assert current['lastOperationId'] == command['before']['lastOperationId']
            native = self.native(self.get('/disks').get('items', []))
            assert identity(native) == identity(command['native_before']), 'review changed native identity/template/storage before sending'
        assert not command.get('explicit_http_error'), 'explicit rejection needs Root review, not automatic replay'
        command.update(send_attempts=command['send_attempts'] + 1, ack_unknown=True)
        self.persist(args.intent, {'phase': 'SEND_DURABLE', 'command': command})
        try:
            accepted = self.api(command['method'], command['route'], command['body'], self.token)
        except self.g['ProductAPIError'] as error:
            command['explicit_http_error'] = {'status': error.status, 'reason': error.reason}
            self.persist(args.intent, {'phase': 'EXPLICIT_HTTP_ERROR', 'command': command})
            raise
        command.update(accepted=accepted, http_status=self.g['EVENTS'][-1]['status'])
        self.persist(args.intent, {'phase': 'ACK_RECEIVED', 'command': command})
        assert command['http_status'] == 202 and accepted['vmId'] == VM
        assert str(uuid.UUID(accepted['id'])) == accepted['id']
        command.update(operation_id=accepted['id'], ack_unknown=False)
        self.persist(args.intent, {'phase': 'ACK_PINNED', 'command': command})
        return self.observe(args.intent, args.wait_seconds)

    def observe(self, intent, wait_seconds):
        command = self.state['commands'][intent]
        assert command.get('operation_id'), 'unknown ACK: explicit original-key recovery required; never guess an ID'
        deadline = time.monotonic() + wait_seconds
        while True:
            operation = self.get('/operations/' + command['operation_id'])
            assert operation['vmId'] == VM and operation['id'] == command['operation_id']
            command.update(operation=operation, current=self.get(), disks=self.get('/disks').get('items', []))
            if command['action'] == 'snapshot':
                fresh = [s for s in self.get('/snapshots').get('items', []) if s['snapshotId'] not in command['snapshot_ids_before']]
                assert len(fresh) <= 1
                if fresh:
                    assert fresh[0]['sourceVmId'] == VM and fresh[0]['sourceVmUid'] == VM_UID
                    command['snapshot'] = fresh[0]
            result = self.persist(intent, {'phase': 'ORIGINAL_OPERATION_OBSERVED', 'command': command})
            if terminal(command) or time.monotonic() >= deadline:
                return result
            time.sleep(1)

    def preview(self, args):
        assert args.snapshot_id and str(uuid.UUID(args.snapshot_id)) == args.snapshot_id
        current = self.get(); assert current['observedState'] == 'Stopped' and not current['closing']
        preview = self.api('POST', BASE + '/snapshots/' + args.snapshot_id + '/restore-preview',
                           {'data': {'snapshotId': args.snapshot_id}}, self.token)
        assert self.g['EVENTS'][-1]['status'] == 200
        assert preview['snapshotId'] == args.snapshot_id and int(preview['version']) == int(current['version'])
        assert re.fullmatch('[0-9a-f]{64}', preview['previewDigest'])
        self.state['previews'][args.snapshot_id] = preview
        return self.persist('preview-' + args.snapshot_id, {'phase': 'READ_ONLY_PREVIEW', 'preview': preview,
                                                          'preview_sha256': digest(preview), 'business_mutations': 0})


def collector_fixture_checks():
    """Exercise actual native collection with only PG/Kubernetes read boundaries faked."""
    from tempfile import TemporaryDirectory
    global RUN
    original_run = RUN
    calls = []
    disk_id = DATA['data3']
    logical = {'diskId': disk_id, 'serial': disk_id, 'role': 'data',
               'volumeName': 'disk-' + disk_id.replace('-', ''), 'attached': False}
    restored_name = 'restore-owned-disk-' + disk_id.replace('-', '')
    record = {'ID': disk_id, 'Role': 'data', 'Serial': disk_id, 'Attached': False, 'Tombstone': False,
              'PVC': {'Namespace': NS, 'Name': restored_name, 'UID': 'pvc-restored'},
              'DV': {'Namespace': '', 'Name': '', 'UID': ''}}

    def collect(value, api_disk=None, pvc_uid=None, dv_uid=None, volume=None):
        api_disk = copy.deepcopy(logical if api_disk is None else api_disk)
        records = [] if value is None else [copy.deepcopy(value)]
        name = (value or record)['PVC']['Name'] or (value or record)['DV']['Name']
        actual_pvc_uid = pvc_uid or (value or record)['PVC']['UID']
        dv_ref = (value or record)['DV']
        owner = {'kind': 'DataVolume', 'uid': dv_ref['UID'], 'controller': True} if dv_ref['Name'] else {
            'kind': 'VirtualMachine', 'uid': VM_UID, 'controller': True}
        pvc = {'kind': 'PersistentVolumeClaim', 'metadata': {'name': name, 'namespace': NS,
                    'uid': actual_pvc_uid, 'ownerReferences': [owner]},
               'spec': {'volumeMode': 'Block', 'accessModes': ['ReadWriteOnce'], 'volumeName': 'pv-fixture'},
               'status': {'phase': 'Bound'}}
        objects = {('namespace', 'kube-system', None): {'metadata': {'uid': CLUSTER_UID}},
                   ('namespace', NS, None): {'metadata': {'uid': NS_UID,
                       'labels': {'network.ani.io/tenant-id': TENANT}}},
                   ('vm', 'vm-' + VM, NS): {'kind': 'VirtualMachine',
                       'metadata': {'name': 'vm-' + VM, 'namespace': NS, 'uid': VM_UID,
                                    'labels': {'ani.io/tenant-id': TENANT, 'vm.ani.io/vm-id': VM}},
                       'spec': {'runStrategy': 'Manual', 'template': {'spec': {
                           'domain': {'firmware': {'uuid': VM}}, 'volumes': [] if volume is None else [volume]}}}},
                   ('vmi', 'vm-' + VM, NS): None,
                   ('pods', '-l=kubevirt.io=virt-launcher', NS): {'items': []},
                   ('pvc', name, NS): pvc,
                   ('pv', 'pv-fixture', None): {'kind': 'PersistentVolume',
                       'metadata': {'name': 'pv-fixture', 'uid': 'pv-original'},
                       'spec': {'claimRef': {'name': name, 'namespace': NS, 'uid': actual_pvc_uid},
                                'persistentVolumeReclaimPolicy': 'Delete', 'csi': {'volumeHandle': 'owned-volume'}}}}
        if dv_ref['Name']:
            objects[('dv', dv_ref['Name'], NS)] = {'kind': 'DataVolume',
                'metadata': {'name': dv_ref['Name'], 'namespace': NS, 'uid': dv_uid or dv_ref['UID'],
                             'ownerReferences': [{'kind': 'VirtualMachine', 'uid': VM_UID, 'controller': True}]}}

        def get(kind, name, namespace=None, optional=False):
            calls.append((kind, name, namespace))
            value = objects.get((kind, name, namespace))
            assert value is not None or optional, 'fixture was asked for an unexpected required object'
            return copy.deepcopy(value)

        def owned(value, kind, uid):
            return any(o.get('kind') == kind and o.get('uid') == uid and o.get('controller')
                       for o in value['metadata'].get('ownerReferences', []))

        def psql(owner, query, database):
            assert owner == 'postgres' and database == 'vm_resource_candidate_01a168ce'
            assert "tenant_id='" + TENANT + "'" in query and "vm_id='" + VM + "'" in query
            return '\n'.join(json.dumps(item) for item in records)

        driver = Driver.__new__(Driver)
        driver.w, driver.g = {'native': get, 'owned': owned}, {'psql': psql}
        return driver.native([api_disk])

    try:
        with TemporaryDirectory(prefix='windows-collector-') as directory:
            RUN = Path(directory)
            (RUN / 'evidence').mkdir()
            (RUN / 'evidence/product-windows2025-iso-native.json').write_text('{"pvcs": []}')
            result = collect(record)
            assert [item['pvc']['name'] for item in result['storage']] == [restored_name]
            assert result['storage'][0]['pvc']['uid'] == 'pvc-restored' and result['storage'][0]['dv'] is None
            assert not any(kind == 'pvc' and name == 'vd-' + disk_id for kind, name, namespace in calls)
            legacy = copy.deepcopy(record)
            legacy['PVC'].update(Name='vd-' + disk_id, UID='pvc-legacy')
            legacy['DV'].update(Namespace=NS, Name='vd-' + disk_id, UID='dv-original')
            assert collect(legacy)['storage'][0]['dv']['uid'] == 'dv-original'
            attached = dict(logical, attached=True)
            attached_record = dict(record, Attached=True)
            volume = {'name': logical['volumeName'], 'persistentVolumeClaim': {'claimName': restored_name}}
            assert collect(attached_record, attached, volume=volume)['storage'][0]['pvc']['uid'] == 'pvc-restored'
            wrong_namespace = copy.deepcopy(record); wrong_namespace['PVC']['Namespace'] = 'other-tenant'
            wrong_serial = dict(record, Serial='another-disk')
            rejected = [(record, {'pvc_uid': 'same-name-replacement'}),
                        (legacy, {'dv_uid': 'same-name-replacement'}),
                        (wrong_namespace, {}), (wrong_serial, {}), (None, {}),
                        (attached_record, {'api_disk': attached,
                            'volume': {'name': logical['volumeName'], 'persistentVolumeClaim': {'claimName': 'stale-physical-pvc'}}})]
            for value, kwargs in rejected:
                try:
                    collect(value, **kwargs)
                except (AssertionError, KeyError):
                    pass
                else:
                    raise AssertionError('stale or unowned physical mapping was accepted')
    finally:
        RUN = original_run
    return 3 + len(rejected)


def self_test():
    args = argparse.Namespace(action='stop', intent='installed-stop', root_handoff='Root approves installed-stop',
        root_evidence='Root-browser-evidence', installation_complete=True, destructive_confirmed=False,
        disk='data3', disk_mode='hot', guest_prepared_confirmed=False, snapshot_mode='online', snapshot_id=None, preview_sha=None)
    current = {'id': VM, 'osFamily': 'windows', 'closing': False, 'observedState': 'Running', 'version': '5'}
    disks = [{'diskId': DATA['data3'], 'serial': DATA['data3'], 'role': 'data', 'attached': True}]
    command = build(args, current, disks)
    assert command['body']['data']['expectedVersion'] == '5' and may_send(command)
    assert not may_send(dict(command, send_attempts=1, ack_unknown=True))
    assert not may_send(dict(command, operation_id=str(uuid.uuid4())))
    checks = 3
    for field, value in [('installation_complete', False), ('root_handoff', 'other-intent')]:
        bad = copy.copy(args); setattr(bad, field, value)
        try:
            build(bad, current, disks)
        except AssertionError:
            checks += 1
        else:
            raise AssertionError('unsafe handoff accepted: ' + field)
    args.action = 'detach'
    try:
        build(args, current, disks)
    except AssertionError:
        checks += 1
    else:
        raise AssertionError('hot detach accepted without Root Guest preparation')
    args.guest_prepared_confirmed = True
    assert build(args, current, disks)['body']['data']['guestPreparedConfirmation'] is True; checks += 1
    args.action = 'boot-system'; current.update(observedState='Stopped', media=[{'slot': 'INSTALL', 'desiredMedia': 'windows-server-2025-iso'}])
    try:
        build(args, current, disks)
    except AssertionError:
        checks += 1
    else:
        raise AssertionError('system boot accepted before INSTALL ejection')
    current['media'] = []
    assert build(args, current, disks)['body']['data']['bootDevice'] == 'SYSTEM_DISK'; checks += 1
    assert command['body_sha256'] != digest({'data': dict(command['body']['data'], expectedVersion='6')}); checks += 1
    native = {'vmi': {'uid': 'original', 'volume_status': [{'phase': 'Pending'}]}, 'storage': [{'pvc': {'uid': 'disk'}}]}
    progress = copy.deepcopy(native); progress['vmi']['volume_status'] = [{'phase': 'Ready'}]
    assert identity(native) == identity(progress); checks += 1
    replacement = copy.deepcopy(progress); replacement['storage'][0]['pvc']['uid'] = 'replacement'
    assert identity(native) != identity(replacement); checks += 1
    stopped = copy.deepcopy(current)
    args.action, args.disk_mode = 'attach', 'cold'; disks[0]['attached'] = False
    assert build(args, stopped, disks)['route'].endswith('/' + DATA['data3'] + '/attach'); checks += 1
    args.action = 'force-stop'
    try:
        build(args, dict(current, observedState='Running'), disks)
    except AssertionError:
        checks += 1
    else:
        raise AssertionError('ForceStop accepted without destructive confirmation')
    args.action, args.destructive_confirmed = 'restore', True
    args.snapshot_id = str(uuid.uuid4())
    preview = {'snapshotId': args.snapshot_id, 'version': '5', 'previewDigest': 'a' * 64}
    args.preview_sha = digest(preview)
    assert build(args, stopped, disks, preview)['body']['data']['previewDigest'] == 'a' * 64; checks += 1
    stale = dict(preview, version='4'); args.preview_sha = digest(stale)
    try:
        build(args, stopped, disks, stale)
    except AssertionError:
        checks += 1
    else:
        raise AssertionError('stale restore preview accepted')
    checks += collector_fixture_checks()
    print(json.dumps({'result': 'PASS', 'pure_guards': checks, 'live_helper_imports': 0, 'http_requests': 0, 'guest_commands': 0}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=('self-test', 'capture', 'prepare', 'execute', 'observe', 'preview'))
    parser.add_argument('--action', choices=ACTIONS)
    parser.add_argument('--intent')
    parser.add_argument('--root-handoff')
    parser.add_argument('--root-evidence')
    parser.add_argument('--installation-complete', action='store_true')
    parser.add_argument('--guest-prepared-confirmed', action='store_true')
    parser.add_argument('--destructive-confirmed', action='store_true')
    parser.add_argument('--disk', choices=DATA)
    parser.add_argument('--disk-mode', choices=('cold', 'hot'))
    parser.add_argument('--snapshot-mode', choices=('online', 'offline'))
    parser.add_argument('--snapshot-id')
    parser.add_argument('--preview-sha')
    parser.add_argument('--execute-authorized', action='store_true')
    parser.add_argument('--recover-admission', action='store_true')
    parser.add_argument('--wait-seconds', type=int, choices=range(31), default=0)
    args = parser.parse_args()
    if args.phase == 'self-test':
        self_test(); return
    if args.phase in ('prepare', 'execute', 'observe', 'capture'):
        assert args.intent and re.fullmatch('[a-z0-9][a-z0-9-]{0,79}', args.intent)
    if args.phase == 'prepare':
        assert args.action
    with LOCK.open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit(73)
        driver = Driver()
        if args.phase == 'capture':
            result = driver.capture(args.intent)
        elif args.phase == 'preview':
            result = driver.preview(args)
        elif args.phase == 'prepare':
            result = driver.prepare(args)
        elif args.phase == 'execute':
            result = driver.execute(args)
        else:
            result = driver.observe(args.intent, args.wait_seconds)
        print(json.dumps({'phase': result['phase'], 'vm_id': VM, 'intent': args.intent,
                          'operation_id': result.get('command', {}).get('operation_id'),
                          'state': result.get('command', {}).get('operation', {}).get('state'),
                          'version': result.get('command', {}).get('current', {}).get('version'),
                          'expected_version': result.get('command', {}).get('body', {}).get('data', {}).get('expectedVersion')}))


if __name__ == '__main__':
    main()
