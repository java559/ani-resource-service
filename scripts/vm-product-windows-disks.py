#!/usr/bin/env python3
"""Create the two reviewed, unmounted Windows data disks through real Gov.

Only the original 3GB/4GB bodies, versions and keys are accepted. The second
intent waits for the first original operation; unknown ACKs never retry a POST.
"""
import argparse
import copy
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import runpy
import time
import uuid

RUN = Path('/home/chabking/workspace/ani-resource-service-runs/vm-mvp-20261023-01a168ce')
PRIVATE = RUN / 'private/gov-candidate'
STATE = PRIVATE / 'windows-data-disks-plan.json'
RECEIPT = RUN / 'evidence/product-windows-data-disks-prepared.json'
VM = 'fc154879-8a40-4b18-967c-215f5cf5992d'
VMUID = '9a751320-5f97-4e56-af6a-30964610321b'
TENANT = 'b3c946b5-7eb3-4feb-9f82-79712062ca5c'
NS = 'tenant-' + TENANT
NSUID = '09e58ecb-fe20-454f-bc7b-2da3c406f0ac'
BASE = '/api/v1/virtual-machines/' + VM
APPROVED_PLAN = '0afd5ee4d2d5109a47106a608156a57071766d2e99efd05f64140030e390c303'
PREPARE_SOURCE = '284773a5c85cc538daf0959de7b56f6f401989978804d4e724723d673264ce68'
K = ['/home/chabking/ani-four-service-deploy-20261012/tools/kubectl',
     '--kubeconfig=/home/chabking/ani-four-service-deploy-20261012/private/kubeconfig']


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def commands(version):
    return [{'label': 'data' + str(size), 'method': 'POST', 'route': BASE + '/disks',
             'body': {'data': {'name': 'vm-mvp-windows-data' + str(size) + '-01a168ce',
                               'sizeGb': str(size), 'expectedVersion': str(version + index),
                               'idempotencyKey': str(uuid.uuid5(uuid.NAMESPACE_URL,
                                   'ani://' + VM + '/vm-mvp-20261023-01a168ce/create-data' + str(size))) }},
             'send_attempts': 0, 'ack_unknown': False}
            for index, size in enumerate((3, 4))]


def validate(plan):
    assert plan['vm_id'] == VM and plan['native_vm_uid'] == VMUID
    assert plan['namespace'] == NS and plan['namespace_uid'] == NSUID
    assert plan['commands'] == commands(plan['initial_version'])
    assert plan['disk_mode'] == 'Block' and plan['disk_access_modes'] == ['ReadWriteOnce']
    assert plan['initially_attached'] is False and plan['total_decimal_gb'] == 7
    assert len({x['body']['data']['idempotencyKey'] for x in plan['commands']}) == 2
    for command in plan['commands']:
        assert set(command['body']['data']) == {'name', 'sizeGb', 'expectedVersion', 'idempotencyKey'}


def native(kind, name, namespace=None, optional=False):
    import subprocess
    args = K + ['get', kind, name]
    if namespace:
        args += ['-n', namespace]
    if optional:
        args += ['--ignore-not-found=true']
    raw = subprocess.check_output(args + ['-o', 'json'], timeout=5)
    return json.loads(raw) if raw.strip() else None


def owned(value, kind, uid):
    return any(x.get('kind') == kind and x.get('uid') == uid and x.get('controller')
               for x in value['metadata'].get('ownerReferences', []))


def guard_native():
    assert native('namespace', 'kube-system')['metadata']['uid'] == '5277649d-e28d-4a0a-ae76-8354365c63bc'
    assert native('namespace', NS)['metadata']['uid'] == NSUID
    original = json.loads((RUN / 'evidence/product-windows2025-iso-native.json').read_text())
    vm = native('vm', 'vm-' + VM, NS)
    assert vm['metadata']['uid'] == VMUID == original['vm']['uid']
    assert vm['metadata']['labels']['ani.io/tenant-id'] == TENANT
    assert vm['metadata']['labels']['vm.ani.io/vm-id'] == VM
    template = vm['spec']['template']
    assert vm['spec']['runStrategy'] == 'Manual'
    assert template['spec']['domain']['firmware']['uuid'] == VM
    vmi = native('vmi', 'vm-' + VM, NS)
    assert owned(vmi, 'VirtualMachine', VMUID)
    protected = {'vm': original['vm'], 'vmi_uid': vmi['metadata']['uid'],
                 'template_sha256': digest(template), 'boot': 'INSTALL_MEDIA', 'volumes': []}
    assert len(original['datavolumes']) == len(original['pvcs']) == len(original['pvs']) == 3
    dvs = {}
    for item in original['datavolumes']:
        old = item['ref']; obj = native('dv', old['name'], NS)
        assert obj['metadata']['uid'] == old['uid'] and owned(obj, 'VirtualMachine', VMUID)
        dvs[old['uid']] = old
    for item in original['pvcs']:
        old = item['ref']; obj = native('pvc', old['name'], NS)
        assert obj['metadata']['uid'] == old['uid'] and obj['spec'] == item['spec']
        assert obj.get('status', {}).get('phase') == 'Bound'
        assert owned(obj, 'VirtualMachine', VMUID) or any(owned(obj, 'DataVolume', u) for u in dvs)
        pv = native('pv', obj['spec']['volumeName'])
        prior = [v for v in original['pvs'] if v['ref']['name'] == pv['metadata']['name']]
        assert len(prior) == 1
        assert pv['metadata']['uid'] == prior[0]['ref']['uid']
        assert pv['spec']['claimRef'] == prior[0]['claim_ref'] and pv['spec']['csi'] == prior[0]['csi']
        assert pv['spec']['claimRef']['uid'] == old['uid']
        protected['volumes'].append({'pvc': old, 'pv': prior[0]['ref'],
                                    'volume_handle': pv['spec']['csi']['volumeHandle'],
                                    'mode': obj['spec']['volumeMode'],
                                    'requested_storage': obj['spec']['resources']['requests']['storage']})
    assert sorted(x['mode'] for x in protected['volumes']) == ['Block', 'Filesystem', 'Filesystem']
    for slot in ('media-install', 'media-driver'):
        disk = next(x for x in template['spec']['domain']['devices']['disks'] if x['name'] == slot)
        assert disk['cdrom']['bus'] == 'sata' and disk['cdrom']['readonly'] is True
    protected['datavolumes'] = list(dvs.values())
    return protected


def prepare():
    assert os.environ.get('SSH_CONNECTION', '').split()[2:3] == ['172.16.101.31'], 'ssh fedora only'
    assert (PRIVATE / 'credentials.json').exists(), 'reuse existing tenant credentials only'
    g = runpy.run_path(str(RUN / 'gov-candidate.py'))
    credentials = g['credentials']()
    token = g['api']('POST', '/api/v1/auth/password/login', {
        'tenant_name': credentials['tenant_code'], 'username': credentials['tenant_username'],
        'password': credentials['tenant_password']})['access_token']
    current = g['api']('GET', BASE, token=token)
    disks = g['api']('GET', BASE + '/disks', token=token).get('items', [])
    assert current['id'] == VM and not current['closing'] and int(current['version']) >= 1
    operation = g['api']('GET', BASE + '/operations/' + current['lastOperationId'], token=token)
    assert operation['vmId'] == VM and operation['state'] == 'succeeded'
    assert not any(x.get('role') == 'data' for x in disks), 'review existing data disks instead of creating duplicates'
    protection = guard_native()
    candidate = {'vm_id': VM, 'native_vm_uid': VMUID, 'namespace': NS, 'namespace_uid': NSUID,
                 'tenant_id': TENANT, 'initial_version': int(current['version']),
                 'commands': commands(int(current['version'])), 'disk_mode': 'Block',
                 'disk_access_modes': ['ReadWriteOnce'], 'initially_attached': False,
                 'total_decimal_gb': 7, 'additional_compute': 0,
                 'platform_defaults': {'bus': 'scsi', 'serial': 'stable Resource logical disk ID', 'hotpluggable': True},
                 'protection': protection, 'public_vm_before': current, 'public_disks_before': disks,
                 'last_operation_before': operation,
                 'second_prerequisite': 'first original operation succeeded, first new logical disk captured and still unattached; same VM/version+1 and original root/ISO/native guards',
                 'unknown_ack': 'persist original key/body before any future POST; observe original intent, never send a new key or blindly retry',
                 'cleanup': 'normal Gov DeleteDisk for these exact unattached logical disk IDs at fresh version, or final normal VM Delete; exact DV/PVC/PV/backend reclamation; preserve root, two ISO copies and their source media',
                 'helper_source_sha256': hashlib.sha256((RUN / 'gov-candidate.py').read_bytes()).hexdigest(),
                 'driver_source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    validate(candidate)
    if STATE.exists():
        previous = json.loads(STATE.read_text())
        assert previous['plan'] == candidate, 'existing frozen intent changed; stop for review'
    else:
        g['save'](STATE, {'plan': candidate, 'plan_sha256': digest(candidate)})
    receipt = {'phase': 'PREPARED_NO_DISK_POST', 'at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
               'plan': candidate, 'plan_sha256': digest(candidate), 'private_state_reference': str(STATE),
               'create_disk_posts': 0, 'attach_posts': 0, 'guest_commands': 0,
               'api_events': g['EVENTS']}
    g['save'](RECEIPT, receipt)
    print(json.dumps({'phase': receipt['phase'], 'plan_sha256': receipt['plan_sha256'],
                      'initial_version': candidate['initial_version'], 'commands': candidate['commands'],
                      'protected_volume_count': len(protection['volumes']), 'receipt': str(RECEIPT)}))


def self_test():
    plan = {'vm_id': VM, 'native_vm_uid': VMUID, 'namespace': NS, 'namespace_uid': NSUID,
            'initial_version': 3, 'commands': commands(3), 'disk_mode': 'Block',
            'disk_access_modes': ['ReadWriteOnce'], 'initially_attached': False, 'total_decimal_gb': 7}
    validate(plan)
    for field, value in [('initially_attached', True), ('total_decimal_gb', 8), ('native_vm_uid', str(uuid.uuid4()))]:
        bad = dict(plan, **{field: value})
        try:
            validate(bad)
        except AssertionError:
            continue
        raise AssertionError('invalid disk plan accepted: ' + field)
    bad = json.loads(json.dumps(plan)); bad['commands'][1]['body']['data']['expectedVersion'] = '3'
    try:
        validate(bad)
    except AssertionError:
        pass
    else:
        raise AssertionError('second command did not retain sequential version')
    assert may_send({'send_attempts': 0, 'ack_unknown': False})
    assert not may_send({'send_attempts': 1, 'ack_unknown': True})
    assert not may_send({'send_attempts': 0, 'operation_id': str(uuid.uuid4())})
    assert second_ready({'operation': {'state': 'succeeded'}}, 4)
    assert not second_ready({'operation': {'state': 'running'}}, 4)
    assert not second_ready({'operation': {'state': 'succeeded'}}, 3)
    print(json.dumps({'result': 'PASS', 'pure_guards': 11, 'api_calls': 0, 'native_calls': 0}))


def may_send(command):
    return command.get('send_attempts') == 0 and not command.get('ack_unknown') and not command.get('operation_id')


def second_ready(previous, version):
    return previous.get('operation', {}).get('state') == 'succeeded' and version == 4


def login(g):
    assert (PRIVATE / 'credentials.json').is_file()
    assert (PRIVATE / 'credentials.json').stat().st_mode & 0o077 == 0
    credentials = g['credentials']()
    return g['api']('POST', '/api/v1/auth/password/login', {
        'tenant_name': credentials['tenant_code'], 'username': credentials['tenant_username'],
        'password': credentials['tenant_password']})['access_token']


def frozen():
    assert STATE.stat().st_mode & 0o077 == 0
    state = json.loads(STATE.read_text())
    plan = state['plan']
    validate(plan)
    assert digest(plan) == state['plan_sha256'] == APPROVED_PLAN
    assert plan['driver_source_sha256'] == PREPARE_SOURCE
    assert plan['helper_source_sha256'] == hashlib.sha256((RUN / 'gov-candidate.py').read_bytes()).hexdigest()
    state.setdefault('execution', {})
    return state


def persist(g, state, phase):
    g['save'](STATE, state)
    value = {'at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'phase': phase,
             'vm_id': VM, 'plan_sha256': state['plan_sha256'],
             'prepared_receipt': str(RECEIPT), 'private_state_reference': str(STATE),
             'commands': state['execution'], 'attach_posts': 0, 'power_posts': 0, 'guest_commands': 0,
             'execution_source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
             'api_events': g['EVENTS']}
    g['save'](RUN / 'evidence/product-windows-data-disks-executed.json', value)
    return value


def public_state(g, token):
    current = g['api']('GET', BASE, token=token)
    disks = g['api']('GET', BASE + '/disks', token=token).get('items', [])
    assert current['id'] == VM and not current['closing']
    return current, disks


def capture_disk(disk):
    assert disk['role'] == 'data' and not disk['attached'] and disk['serial'] == disk['diskId']
    assert disk['volumeName'] == 'disk-' + disk['diskId'].replace('-', '')
    # This is NewInitialDisk's persisted application declaration, not a CSI or
    # backend PVC naming assumption. All actual objects are UID/owner checked.
    name = 'vd-' + disk['diskId']
    dv = native('dv', name, NS, optional=True)
    result = {'logical_disk': disk, 'dv': None, 'pvc': None, 'pv': None,
              'mounted': False, 'guest': 'NOT_RUN', 'device_hotplug': 'NOT_RUN'}
    if not dv:
        result['physical_identity'] = 'DECLARATION_NOT_OBSERVED'
        return result
    assert owned(dv, 'VirtualMachine', VMUID)
    storage = dv['spec']['storage']
    assert dv['spec']['source'] == {'blank': {}} and dv['spec']['contentType'] == 'kubevirt'
    assert storage['volumeMode'] == 'Block' and storage['accessModes'] == ['ReadWriteOnce']
    assert storage['resources']['requests']['storage'] == str(disk['sizeGb']) + 'G'
    result['dv'] = {'name': name, 'namespace': NS, 'uid': dv['metadata']['uid'],
                    'phase': dv.get('status', {}).get('phase'), 'storage': storage}
    pvc = native('pvc', name, NS, optional=True)
    if not pvc:
        result['physical_identity'] = 'PVC_NOT_OBSERVED_YET'
        return result
    assert owned(pvc, 'DataVolume', dv['metadata']['uid']) or owned(pvc, 'VirtualMachine', VMUID)
    assert pvc['spec']['volumeMode'] == 'Block' and pvc['spec']['accessModes'] == ['ReadWriteOnce']
    result['pvc'] = {'name': name, 'namespace': NS, 'uid': pvc['metadata']['uid'],
                     'phase': pvc.get('status', {}).get('phase'),
                     'requested_storage': pvc['spec']['resources']['requests']['storage']}
    if pvc['spec'].get('volumeName'):
        pv = native('pv', pvc['spec']['volumeName'])
        claim = pv['spec']['claimRef']
        assert claim['uid'] == pvc['metadata']['uid'] and claim['name'] == name and claim['namespace'] == NS
        result['pv'] = {'name': pv['metadata']['name'], 'uid': pv['metadata']['uid'],
                        'claim_ref': claim, 'csi': pv['spec'].get('csi'),
                        'reclaim_policy': pv['spec']['persistentVolumeReclaimPolicy']}
    result['physical_identity'] = 'PVC_AND_PV_UID_CAPTURED' if result['pv'] else 'PV_NOT_OBSERVED_YET'
    for kind in ('vm', 'vmi'):
        value = native(kind, 'vm-' + VM, NS)
        spec = value['spec']['template']['spec'] if kind == 'vm' else value['spec']
        assert not any(x['name'] == disk['volumeName'] for x in spec.get('volumes', []))
        assert not any(x.get('serial') == disk['serial'] for x in spec['domain']['devices']['disks'])
    result['persistent_and_current_vmi_unreferenced'] = True
    return result


def execute(args):
    assert os.environ.get('SSH_CONNECTION', '').split()[2:3] == ['172.16.101.31'], 'ssh fedora only'
    state = frozen()
    plan = state['plan']
    index = 0 if args.label == 'data3' else 1
    intended = plan['commands'][index]
    g = runpy.run_path(str(RUN / 'gov-candidate.py'))
    token = login(g)
    current, disks = public_state(g, token)
    protection = guard_native()
    assert protection == plan['protection'], 'original root/ISO/template/VM/VMI guard changed'
    assert [x for x in disks if x['role'] != 'data'] == plan['public_disks_before']
    command = state['execution'].get(args.label)
    if command is None:
        assert args.action == 'execute' and args.execute_authorized and args.plan_sha == APPROVED_PLAN
        assert int(current['version']) == int(intended['body']['data']['expectedVersion'])
        assert not any(x.get('name') == intended['body']['data']['name'] for x in disks)
        if index == 1:
            previous = state['execution'].get('data3', {})
            assert second_ready(previous, int(current['version']))
            assert current['lastOperationId'] == previous['operation_id']
            first = [x for x in disks if x['diskId'] == previous['disk']['diskId']]
            assert len(first) == 1 and not first[0]['attached']
        else:
            assert current['lastOperationId'] == plan['last_operation_before']['id']
            assert not any(x['role'] == 'data' for x in disks)
        command = copy.deepcopy(intended)
        command.update(before=current, disks_before=disks, body_sha256=digest(intended['body']))
        state['execution'][args.label] = command
        persist(g, state, 'ORIGINAL_INTENT_PERSISTED_SEND0')
    assert command['body'] == intended['body'] and command['route'] == intended['route']
    assert command['body_sha256'] == digest(intended['body'])
    if not command.get('operation_id'):
        if not may_send(command) or args.action != 'execute':
            command.update(current=current, disks=disks)
            persist(g, state, 'ACK_UNKNOWN_OBSERVE_ONLY_NO_REPLAY')
            print(json.dumps({'phase': 'ACK_UNKNOWN_OBSERVE_ONLY_NO_REPLAY', 'label': args.label}))
            return
        assert args.execute_authorized and args.plan_sha == APPROVED_PLAN
        command.update(send_attempts=1, ack_unknown=True,
                       source_sha256_at_send=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                       sent_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
        persist(g, state, 'ONE_POST_IN_FLIGHT')
        try:
            ack = g['api']('POST', command['route'], command['body'], token)
        except g['ProductAPIError'] as error:
            command['explicit_http_error'] = {'status': error.status, 'reason': error.reason}
            persist(g, state, 'EXPLICIT_HTTP_ERROR_NO_RETRY')
            raise
        assert ack['vmId'] == VM and str(uuid.UUID(ack['id'])) == ack['id']
        command.update(operation_id=ack['id'], ack=ack, ack_unknown=False)
        persist(g, state, 'ORIGINAL_OPERATION_ACKNOWLEDGED')
    end = time.monotonic() + args.wait_seconds
    while True:
        command['operation'] = g['api']('GET', BASE + '/operations/' + command['operation_id'], token=token)
        current, disks = public_state(g, token)
        command.update(current=current, disks=disks)
        matches = [x for x in disks if x['role'] == 'data' and x['name'] == intended['body']['data']['name']
                   and str(x['sizeGb']) == intended['body']['data']['sizeGb']]
        if matches:
            assert len(matches) == 1 and not matches[0]['attached']
            command['disk'] = matches[0]
            if command['operation']['state'] in ('succeeded', 'failed') or time.monotonic() >= end:
                command['physical'] = capture_disk(matches[0])
        phase = 'ORIGINAL_OPERATION_PENDING'
        if command['operation']['state'] == 'succeeded':
            assert len(matches) == 1
            version = int(intended['body']['data']['expectedVersion']) + 1
            assert int(command['operation']['intentVersion']) == version
            assert int(current['version']) >= version
            if int(current['version']) == version:
                assert current['lastOperationId'] == command['operation_id']
            command['protection_after'] = guard_native()
            assert command['protection_after'] == plan['protection']
            phase = 'CREATE_DISK_SUCCEEDED_UNATTACHED'
        persist(g, state, phase)
        if command['operation']['state'] in ('succeeded', 'failed') or time.monotonic() >= end:
            print(json.dumps({'phase': phase, 'label': args.label, 'operation_id': command['operation_id'],
                              'state': command['operation']['state'], 'send_attempts': command['send_attempts'],
                              'version': current['version'], 'disk': command.get('disk'),
                              'physical': command.get('physical')}))
            return
        time.sleep(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'self-test', 'execute', 'observe'))
    parser.add_argument('label', nargs='?', choices=('data3', 'data4'))
    parser.add_argument('--execute-authorized', action='store_true')
    parser.add_argument('--plan-sha')
    parser.add_argument('--wait-seconds', type=int, default=20, choices=range(31))
    args = parser.parse_args()
    if args.action == 'self-test':
        self_test()
        return
    with Path('/home/chabking/workspace/ani-network-service-runs/net05a-heavy.lock').open('a') as lock:
        end = time.monotonic() + 5
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= end:
                    raise SystemExit(73)
                time.sleep(.1)
        if args.action == 'prepare':
            prepare()
        else:
            assert args.label
            execute(args)


if __name__ == '__main__':
    main()
