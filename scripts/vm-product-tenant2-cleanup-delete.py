#!/usr/bin/env python3
"""Prepare and execute only the approved tenant2 pre-Restore cleanup case.

The original plan remains immutable. Root has approved its exact Restore after
reviewing the fresh Preview and the observed cleanup gate. Root has also
approved the original version8 Delete at that gate; no other VM is mutable.
No additional VM/import case is created; no Guest content is written.
"""
import argparse
import contextlib
import datetime
import fcntl
import runpy
import subprocess
import time
import copy
import hashlib
import json
import os
from pathlib import Path
import uuid

RUN = Path('/home/chabking/workspace/ani-resource-service-runs/vm-mvp-20261023-01a168ce')
VM = 'e3c11c5a-cf12-4630-a8f9-f907048ffdfc'
VM_UID = 'fde3b420-6bde-4169-9eb1-ed025c60a1a6'
TENANT = 'd87c4dbd-04fa-4066-9ef6-c6fa4217f316'
NS = 'tenant-' + TENANT
NS_UID = 'fa138fd5-1f35-4fa2-b561-db3d136b2404'
CREATE = '4ded9db7-6492-45d2-925a-1d551ed066c1'
POD = 'vm-mvp-cleanup-hold-root-01a168ce'
ROOT_DISK = '2e1b4214-937a-5c45-b5b0-0d400a553d4c'
ROOT_PVC = 'vd-' + ROOT_DISK
ROOT_PVC_UID = 'ad15d3fc-2474-4f0a-8397-7f406a7bfc00'
ROOT_PV_UID = 'b6d93bcc-1338-4fcc-aed9-f374ecd719ff'
ROOT_BASE = 'csi-vol-016ddb5e-363a-4557-9b14-858beafe6fe9'
IMAGE = '172.16.101.10:5000/kubevirt/cdi-importer@sha256:28134acc24521d9bc8c9250fb29a9bfe0278b771139d32ea5b162d8f3af5ee05'
BASE = '/api/v1/virtual-machines/' + VM


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def key(label):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, 'ani://' + VM + '/vm-mvp-01a168ce/cleanup-delete/' + label))


def helper_manifest():
    return {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {'name': POD, 'namespace': NS,
        'labels': {'ani.io/task': 'vm-mvp-01a168ce', 'ani.io/purpose': 'restore-cleanup-readonly-holder'},
        'ownerReferences': [{'apiVersion': 'kubevirt.io/v1', 'kind': 'VirtualMachine',
            'name': 'vm-' + VM, 'uid': VM_UID, 'controller': False, 'blockOwnerDeletion': False}],
        'annotations': {'v1.multus-cni.io/default-network': NS + '/vm-mvp-tenant2-01a168ce',
                        'networking.kubercloud.com/subnet': NS + '/subnet-ea6b62fc625349bcad46ce56ce60f90d'}},
        'spec': {'restartPolicy': 'Never', 'automountServiceAccountToken': False,
            'activeDeadlineSeconds': 1800, 'terminationGracePeriodSeconds': 10,
            'nodeSelector': {'kubernetes.io/hostname': 'ani-01'},
            'securityContext': {'runAsNonRoot': True, 'runAsUser': 107, 'runAsGroup': 107,
                                'seccompProfile': {'type': 'RuntimeDefault'}},
            'volumes': [{'name': 'held-old-root', 'persistentVolumeClaim': {'claimName': ROOT_PVC, 'readOnly': True}}],
            'containers': [{'name': 'hold', 'image': IMAGE, 'imagePullPolicy': 'Never',
                'command': ['/bin/sh', '-ec', 'test -b /dev/held-root; exec sleep 1800'],
                'volumeDevices': [{'name': 'held-old-root', 'devicePath': '/dev/held-root'}],
                'resources': {'requests': {'cpu': '10m', 'memory': '16Mi'}, 'limits': {'cpu': '50m', 'memory': '64Mi'}},
                'securityContext': {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True,
                                    'capabilities': {'drop': ['ALL']}}}]}}


def requests():
    def request(label, method, suffix, version, **fields):
        return {'phase': label, 'method': method, 'route': BASE + suffix,
                'body': {'data': {'expectedVersion': str(version), 'idempotencyKey': key(label), **fields}},
                'send_attempts': 0, 'ack_unknown': False}
    # Historical tenant2 version is 1. A fresh mismatch stops this plan; it is
    # never silently rebased onto a different operation/version.
    return [request('stop', 'POST', '/stop', 1),
            request('create-data-a', 'POST', '/disks', 2, name='vm-mvp-cleanup-data-a-01a168ce', sizeGb='2'),
            request('attach-data-a', 'POST', '/disks/{exact-data-a-disk-id}/attach', 3, diskId='{exact-data-a-disk-id}'),
            request('create-data-b', 'POST', '/disks', 4, name='vm-mvp-cleanup-data-b-01a168ce', sizeGb='2'),
            request('attach-data-b', 'POST', '/disks/{exact-data-b-disk-id}/attach', 5, diskId='{exact-data-b-disk-id}'),
            request('offline-snapshot', 'POST', '/snapshots', 6, name='vm-mvp-tenant2-cleanup-offline-01a168ce'),
            request('restore', 'POST', '/snapshots/{exact-snapshot-id}/restore', 7,
                    snapshotId='{exact-snapshot-id}', previewDigest='{Root-reviewed-fresh-preview-digest}', destructiveConfirmation=True),
            request('delete-during-cleanup', 'DELETE', '', 8)]


def validate(plan):
    assert plan['vm_id'] == VM and plan['native_vm_uid'] == VM_UID and plan['namespace_uid'] == NS_UID
    assert plan['original_create_operation_id'] == CREATE and plan['initial_version'] == 1
    assert plan['requests'] == requests() and plan['helper_manifest'] == helper_manifest()
    assert plan['writes']['new_vms'] == 0 and plan['writes']['new_disks_gb'] == [2, 2]
    assert plan['writes']['guest_content_bytes'] == 0 and plan['writes']['business_sql'] == 0
    assert plan['execution_allowed'] is False and plan['live_read_calls'] == 0
    claim = plan['root_before']['pvc']
    assert claim['name'] == ROOT_PVC and claim['uid'] == ROOT_PVC_UID
    assert plan['root_before']['pv']['uid'] == ROOT_PV_UID
    assert plan['root_before']['csi']['volumeAttributes']['imageName'] == ROOT_BASE
    assert plan['mapping_gate'] == {'NativeRestoreCompleted': True, 'MappingCommitted': True, 'CleanupCompleted': False,
                                   'old_root_pvc_same_uid': True, 'old_root_pvc_terminating': True,
                                   'old_root_pvc_protection_finalizer': True, 'holder_same_uid_running': True}
    assert len({r['body']['data']['idempotencyKey'] for r in plan['requests']}) == 8


def prepare(evidence):
    path = evidence / 'product-tenant2-linux-native.json'
    native = json.loads(path.read_text())
    assert native['vm_id'] == VM and native['vm']['uid'] == VM_UID and native['namespace_uid'] == NS_UID
    assert native['root_disk']['diskId'] == ROOT_DISK and native['node']['name'] == 'ani-01'
    plan = {'scope': 'one existing tenant2 VM; root plus exactly two new cold-attached blank Block data disks',
        'vm_id': VM, 'native_vm_uid': VM_UID, 'namespace': NS, 'namespace_uid': NS_UID,
        'original_create_operation_id': CREATE, 'initial_version': 1,
        'historical_native_receipt': str(path), 'historical_native_receipt_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
        'historical_is_fresh': False, 'root_before': {k: native[k] for k in ('root_disk', 'dv', 'pvc', 'pv', 'node')},
        'requests': requests(), 'helper_manifest': helper_manifest(),
        'mapping_gate': {'NativeRestoreCompleted': True, 'MappingCommitted': True, 'CleanupCompleted': False,
                        'old_root_pvc_same_uid': True, 'old_root_pvc_terminating': True,
                        'old_root_pvc_protection_finalizer': True, 'holder_same_uid_running': True},
        'writes': {'new_vms': 0, 'new_disks_gb': [2, 2], 'gov_stop': 1, 'gov_cold_attach': 2,
                   'gov_offline_snapshot': 1, 'gov_restore': 1, 'gov_final_delete': 1,
                   'temporary_pods': 1, 'possible_cni_owned_vnic': 1, 'guest_content_bytes': 0,
                   'business_sql': 0, 'backend_writes': 0, 'shared_config': 0, 'manual_finalizer_changes': 0},
        'fresh_prerequisites': ['Root approves exact plan and irreversible deletion of this already-verified tenant2 VM',
            'cluster kube-system UID5277649d-e28d-4a0a-ae76-8354365c63bc; original three node UIDs/IPs',
            'normal tenant2 login/me and role; CREATE succeeded and VM Running version1 before Stop',
            'same VM/NS/root DV-PVC-PV-CSI UIDs; all other VMs/source/NAD protected baseline captured after V08',
            'Stop succeeded; no VMI/primary launcher or pending native power request; attachment same Reserved',
            'each new 2GB disk is same logical serial and Block/RWO physical UID, cold Attach only; no implicit Start',
            'offline Snapshot ready COMPLETE includes exactly root+data-a+data-b; exact VS/VSC/CSI identities frozen',
            'ani-01 same node UID/internalIP; exact helper image digest already cached, no pull/install/build',
            'only old root Bound PVC claimed readOnly; helper name absent; Root approves exact manifest before one CREATE',
            'holder Running same UID, RO CSI attachment and Block mode observed; Root reviews fresh restore preview'],
        'phase_boundaries': ['normal Stop => create/attach a => create/attach b => SnapshotReady => holder Ready',
            'read-only Preview => Root approves digest => normal Restore; never Start this disposable case',
            'read-only PG exact VM/op checkpoint plus old-root PVC/holder UID proves mapping_gate; otherwise NO Delete',
            'normal Gov Delete once, tied to original CREATE, fresh version8 and original immutable key/body',
            'normal exact holder UID/RV DELETE if still present; already Gone by same-VM owner GC means no second DELETE',
            'only original Delete GET; collect all old/new PVC/PV/CSI, snapshot/restore artifacts and source-root temp family'],
        'cleanup': ['preserve old and restored current/retired PVC/PV/CSI refs before Delete, including new data disks',
            'normal UID/RV Pod deletion, background GC only; no grace0/force/finalizer patch',
            'Pod/VNic/VM/VMI/launcher/DV/PVC/PV/VolumeAttachment/VS/VSC/Restore gone under original UIDs',
            'same Network attachment Released/unblocked and task address withdrawn',
            'exact owned RBD base/temp and snapshot handles absent plus matching trash empty; wait normal GC',
            'Ubuntu original source/NAD/NS/other tenant resources unchanged; no manual RBD/SQL cleanup'],
        'failure_recovery': ['unknown CREATE/DELETE ACK: durable pre-send journal then exact name/UID/intent observation; no blind second send',
            'all Gov mutation intents freeze body/version before send; operation ID pinned before any next phase',
            'helper timeout before Restore: normal exact UID/RV holder cleanup, no automatic VM final Delete',
            'Restore complete but cleanup gate absent: retain observed state and report MISS; never fake checkpoints',
            'if case interrupted after holder creation, release only this holder normally; preserve original operations/refs'],
        'native_timing_basis': {'source': 'https://github.com/kubevirt/kubevirt/blob/v1.9.0/pkg/storage/snapshot/restore.go',
            'restore_complete': '267-286 calls deleteObsoleteVolumes, then sets complete',
            'obsolete_dv': '1202-1223 only sends DV Delete; does not wait for PVC/PV disappearance',
            'target_ready': '600-614 requires no VMI, not no ordinary Pods'},
        'driver_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'execution_allowed': False, 'live_read_calls': 0, 'business_mutations': 0}
    plan['root_before']['csi'] = native['pv_csi']
    validate(plan)
    return {'phase': 'PREPARED_NO_LIVE_CALLS', 'plan': plan, 'plan_sha256': digest(plan)}


def self_test():
    manifest = helper_manifest()
    claim = manifest['spec']['volumes'][0]['persistentVolumeClaim']
    assert claim == {'claimName': ROOT_PVC, 'readOnly': True}
    assert manifest['spec']['automountServiceAccountToken'] is False
    assert manifest['spec']['containers'][0]['imagePullPolicy'] == 'Never'
    assert manifest['spec']['containers'][0]['command'][-1] == 'test -b /dev/held-root; exec sleep 1800'
    assert manifest['metadata']['ownerReferences'][0]['uid'] == VM_UID
    assert [int(r['body']['data']['expectedVersion']) for r in requests()] == list(range(1, 9))
    assert len({r['body']['data']['idempotencyKey'] for r in requests()}) == 8
    bad = copy.deepcopy(manifest); bad['spec']['volumes'][0]['persistentVolumeClaim']['readOnly'] = False
    assert digest(bad) != digest(manifest)
    for forbidden in ('delete-during-cleanup',):
        try: live_request(forbidden, {})
        except AssertionError: pass
        else: raise AssertionError('unauthorized stage accepted')
    exact = live_request('attach-data-a', {'data-a': '11111111-1111-4111-8111-111111111111'})
    assert exact['body']['data']['expectedVersion'] == '3' and exact['body']['data']['diskId'] in exact['route']
    restore = live_request('restore', {})
    assert restore['body']['data'] == {'expectedVersion': '7', 'idempotencyKey': '2ab670aa-944e-5722-9295-c56fac5c0139',
        'snapshotId': RESTORE_SNAPSHOT, 'previewDigest': RESTORE_DIGEST, 'destructiveConfirmation': True}
    assert restore['route'] == BASE + '/snapshots/' + RESTORE_SNAPSHOT + '/restore'
    assert restore_gate_check({'NativeRestoreCompleted': True, 'MappingCommitted': True, 'CleanupCompleted': False}, True, True)
    for checkpoint in ('NativeRestoreCompleted', 'MappingCommitted', 'CleanupCompleted'):
        bad = {'NativeRestoreCompleted': True, 'MappingCommitted': True, 'CleanupCompleted': False}
        bad[checkpoint] = not bad[checkpoint]
        assert not restore_gate_check(bad, True, True)
    assert not restore_gate_check({'NativeRestoreCompleted': True, 'MappingCommitted': True, 'CleanupCompleted': False}, False, True)
    deletion = next(r for r in requests() if r['phase'] == 'delete-during-cleanup')
    assert deletion['body']['data'] == {'expectedVersion': '8', 'idempotencyKey': '4d1dcb8b-a6d1-5fa5-b9d1-11fab9a03490'}
    assert deletion['method'] == 'DELETE' and deletion['route'] == BASE
    assert delete_ack_operation({'http_status': 202, 'accepted': {'operationId': 'f4d5fcfc-b938-457d-82a7-79bd4002436e', 'resourceId': VM, 'replayed': False, 'result': 'QUEUED_FOR_OWNER'}}) == 'f4d5fcfc-b938-457d-82a7-79bd4002436e'
    try: delete_ack_operation({'http_status': 202, 'accepted': {'id': 'f4d5fcfc-b938-457d-82a7-79bd4002436e', 'vmId': VM}})
    except (KeyError, AssertionError): pass
    else: raise AssertionError('Command wire shape cannot replace durable Delete ACK')
    print(json.dumps({'result': 'PASS', 'pure_guards': 21, 'helper_imports': 0, 'api_calls': 0, 'guest_commands': 0}))



APPROVED_PLAN = 'fd629d556c1b99140e46e6c959ab2888285f5eadf1a05f4ef5f4557b5d4c569f'
ALLOWED = ('stop', 'create-data-a', 'attach-data-a', 'create-data-b', 'attach-data-b', 'offline-snapshot', 'restore')
RESTORE_SNAPSHOT = 'e1083370-757b-5f66-bb5a-81920d78f1d7'
RESTORE_SNAPSHOT_UID = '8bd93f36-8014-4115-b599-0178bc421d94'
RESTORE_DIGEST = '70035de7532951f76979ae5315846ba9e22f32ea1f7e824624cecb697da434e5'
STATE = RUN / 'private/gov-candidate/tenant2-cleanup-delete-live.json'
LOCK = Path('/home/chabking/workspace/ani-network-service-runs/net05a-heavy.lock')
K = ['/home/chabking/ani-four-service-deploy-20261012/tools/kubectl',
     '--kubeconfig=/home/chabking/ani-four-service-deploy-20261012/private/kubeconfig', '--request-timeout=10s']
NODES = {'ani-01': ('201e41ac-c50d-4c77-b118-72d1ed93e111', '172.16.101.10'),
         'ani-02': ('73564fa7-98ad-45d4-9576-be59f6d5d245', '172.16.101.11'),
         'ani-03': ('af992ed9-cbe1-4f64-8fac-7433808bfab2', '172.16.101.12')}
OTHERS = {'55099440-c47b-4f81-afc5-7d8e740d14dc': '7bca4671-89dd-4925-bad9-17e77401729d',
          '068d56dd-d4fc-4ca8-8430-16c36bad0761': '03906ba4-36d4-4c11-b4c5-312c9c734274',
          'fc154879-8a40-4b18-967c-215f5cf5992d': '9a751320-5f97-4e56-af6a-30964610321b'}
NS1 = 'tenant-b3c946b5-7eb3-4feb-9f82-79712062ca5c'

def native(kind, name=None, namespace=NS):
    args = K + ['get', kind] + ([name, '--ignore-not-found'] if name else [])
    if namespace:
        args += ['-n', namespace]
    result = subprocess.run(args + ['-o', 'json'], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, 'native read failed (no privilege fallback): ' + result.stderr[-350:]
    return json.loads(result.stdout) if result.stdout.strip() else None

def identity(obj):
    return {'kind': obj['kind'], **{key: obj['metadata'].get(key, '') for key in ('namespace', 'name', 'uid')}}

def owns(obj, uid):
    return any(owner.get('uid') == uid and owner.get('controller') is True
               for owner in obj['metadata'].get('ownerReferences', []))

def live_request(phase, disk_ids):
    assert phase in ALLOWED, 'VM Delete not authorized by this execution entry'
    request = copy.deepcopy(next(item for item in requests() if item['phase'] == phase))
    if phase.startswith('attach-'):
        label = phase.removeprefix('attach-'); disk_id = str(uuid.UUID(disk_ids[label]))
        request['route'] = request['route'].replace('{exact-' + label + '-disk-id}', disk_id)
        request['body']['data']['diskId'] = disk_id
    if phase == 'restore':
        request['route'] = BASE + '/snapshots/' + RESTORE_SNAPSHOT + '/restore'
        request['body']['data'].update(snapshotId=RESTORE_SNAPSHOT, previewDigest=RESTORE_DIGEST)
    return request


def restore_gate_check(checkpoints, old_root_blocked, holder_running):
    return (checkpoints.get('NativeRestoreCompleted') is True and checkpoints.get('MappingCommitted') is True
            and checkpoints.get('CleanupCompleted') is False and old_root_blocked and holder_running)


def holder_remaining_seconds(obj):
    created = datetime.datetime.fromisoformat(obj['metadata']['creationTimestamp'].replace('Z', '+00:00'))
    return (created + datetime.timedelta(seconds=obj['spec']['activeDeadlineSeconds']) - datetime.datetime.now(datetime.timezone.utc)).total_seconds()


def delete_ack_operation(command):
    accepted = command['accepted']
    assert command['http_status'] == 202 and accepted['resourceId'] == VM
    assert accepted['result'] == 'QUEUED_FOR_OWNER' and accepted['replayed'] is False
    return str(uuid.UUID(accepted['operationId']))

class Live:
    def __init__(self):
        assert os.environ.get('SSH_CONNECTION', '').split()[2:3] == ['172.16.101.31'], 'ssh fedora only'
        self.g = runpy.run_path(str(RUN / 'gov-candidate.py'))
        self.api, self.save = self.g['api'], self.g['save']
        frozen = json.loads((RUN / 'private/gov-candidate/tenant2-cleanup-delete-prepared.json').read_text())
        assert frozen['plan_sha256'] == digest(frozen['plan']) == APPROVED_PLAN
        validate(frozen['plan']); self.plan = frozen['plan']
        self.state = json.loads(STATE.read_text()) if STATE.exists() else {
            'plan_sha256': APPROVED_PLAN, 'vm_id': VM, 'native_vm_uid': VM_UID, 'commands': {}, 'disk_ids': {}}
        assert self.state['plan_sha256'] == APPROVED_PLAN and self.state['vm_id'] == VM
        credpath = RUN / 'private/gov-candidate/isolation-credentials.json'
        assert not credpath.is_symlink() and credpath.stat().st_mode & 0o077 == 0
        creds = json.loads(credpath.read_text()); assert creds['tenant_code'] == 'vm-isolation-01a168ce'
        self.token = self.api('POST', '/api/v1/auth/password/login', {
            'tenant_name': creds['tenant_code'], 'username': creds['username'], 'password': creds['password']})['access_token']
        me = self.api('GET', '/api/v1/admin/me', token=self.token)
        assert int(me['tenantId']) == 2 and me['username'] == creds['username']
        roles = {int(item) for item in me.get('roleIds', [])} | ({int(me['roleId'])} if me.get('roleId') else set())
        assert 4 in roles, 'original task tenant2 role missing; no grant changes'
        self.principal = {key: me.get(key) for key in ('id', 'tenantId', 'username', 'roleId', 'roleIds')}

    def get(self, suffix=''):
        return self.api('GET', BASE + suffix, token=self.token)

    def persist(self, phase, **value):
        self.save(STATE, self.state)
        public = {'at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'phase': phase,
                  'vm_id': VM, 'native_vm_uid': VM_UID, 'plan_sha256': APPROVED_PLAN,
                  'driver_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  'private_state_reference': str(STATE), 'principal': self.principal,
                  'authorization': 'Root approved exact version7 Restore and original version8 Delete at observed cleanup gate',
                  'guest_content_writes': 0, 'shared_changes': 0, **value}
        path = RUN / 'evidence/tenant2-cleanup-delete-live.json'; self.save(path, public)
        self.save(RUN / ('evidence/tenant2-cleanup-delete-' + phase + '.json'), public)
        return public

    def cluster(self):
        assert native('ns', 'kube-system', '')['metadata']['uid'] == '5277649d-e28d-4a0a-ae76-8354365c63bc'
        node_items = native('nodes', namespace='')['items']; assert len(node_items) == 3
        for node in node_items:
            uid, ip = NODES[node['metadata']['name']]
            assert node['metadata']['uid'] == uid and any(x['type'] == 'InternalIP' and x['address'] == ip for x in node['status']['addresses'])
        namespace = native('ns', NS, ''); assert namespace['metadata']['uid'] == NS_UID
        assert namespace['metadata']['labels']['network.ani.io/tenant-id'] == TENANT
        nad = native('network-attachment-definitions', 'vm-mvp-tenant2-01a168ce')
        assert nad['metadata']['uid'] == 'c744fbb9-fdff-46b9-8ba9-0c75cfb8e9dc'
        assert digest(json.loads(nad['spec']['config'])) == 'bb5bb6d1ad273609354486dfa052bf46236c47583fdb6a228bab4b75fb902d30'
        source = native('pvc', 'vm-mvp-ubuntu-2404-source', 'vm-mvp-probe-01a168ce')
        assert source['metadata']['uid'] == '8b235154-fae1-47ed-8f96-06af02a18ce1'
        assert source['metadata']['annotations']['vm.ani.io/source-sha256'] == '61f58093344ae5c84e22f1be07a904b0e2a15f7b6f18e45ef4549e52833c69c4'
        assert source['status']['phase'] == 'Bound' and source['spec']['volumeMode'] == 'Block'
        node = next(n for n in node_items if n['metadata']['name'] == 'ani-01')
        assert any(c['type'] == 'Ready' and c['status'] == 'True' for c in node['status'].get('conditions', [])), 'ani-01 not Ready'
        sc = native('sc', 'ani-block', '')
        assert sc['metadata']['uid'] == 'e7813a57-3d1c-47e4-a298-86212a59e51e' and sc['reclaimPolicy'] == 'Delete'
        return node

    def protected(self):
        result = {'other_vms': {}, 'pvcs': {}, 'sources': {}, 'nads': {}, 'namespaces': {}}
        for ns in (NS1, NS, 'vm-mvp-probe-01a168ce'):
            result['namespaces'][ns] = identity(native('ns', ns, ''))
        for vid, uid in OTHERS.items():
            obj = native('vm', 'vm-' + vid, NS1); assert obj['metadata']['uid'] == uid
            vmi = native('vmi', 'vm-' + vid, NS1)
            result['other_vms'][vid] = {'ref': identity(obj), 'spec_sha256': digest(obj['spec']),
                                      'vmi': None if vmi is None else identity(vmi)}
            for volume in obj['spec']['template']['spec'].get('volumes', []):
                name = volume.get('dataVolume', {}).get('name') or volume.get('persistentVolumeClaim', {}).get('claimName')
                if name:
                    pvc = native('pvc', name, NS1); pv = native('pv', pvc['spec']['volumeName'], '')
                    result['pvcs'][NS1 + '/' + name] = {'pvc': identity(pvc), 'pvc_spec_sha256': digest(pvc['spec']),
                                                       'pv': identity(pv), 'csi': pv['spec']['csi']}
        for name in ('vm-mvp-ubuntu-2404-source', 'vm-mvp-fedora44-http-source',
                     'vm-mvp-windows2025-http-source', 'vm-mvp-virtio-http-source'):
            obj = native('pvc', name, 'vm-mvp-probe-01a168ce')
            result['sources'][name] = {'ref': identity(obj), 'spec_sha256': digest(obj['spec']),
                                      'annotations_sha256': digest(obj['metadata'].get('annotations', {}))}
        for ns in (NS1, NS):
            for obj in native('network-attachment-definitions', namespace=ns)['items']:
                result['nads'][ns + '/' + obj['metadata']['name']] = {'ref': identity(obj), 'spec_sha256': digest(obj['spec'])}
        return result

    def check_protected(self):
        current = self.protected()
        if 'protected_baseline' in self.state:
            assert current == self.state['protected_baseline'], 'protected current VM/storage/source/NAD identity changed'
        return current

    def target(self, stopped=False):
        obj = native('vm', 'vm-' + VM); assert obj['metadata']['uid'] == VM_UID
        assert obj['metadata']['labels']['ani.io/tenant-id'] == TENANT and obj['metadata']['labels']['vm.ani.io/vm-id'] == VM
        vmi = native('vmi', 'vm-' + VM)
        if vmi:
            assert owns(vmi, VM_UID)
        if stopped:
            assert vmi is None and obj['spec'].get('runStrategy') == 'Manual'
            assert not obj.get('status', {}).get('stateChangeRequests'), 'power transition still pending'
            old_vmis = self.state.get('initial_vmi_uids', [])
            active = [p for p in native('pods')['items'] if p['metadata'].get('labels', {}).get('kubevirt.io') == 'virt-launcher'
                      and any(o.get('uid') in old_vmis for o in p['metadata'].get('ownerReferences', []))]
            assert not active, 'original primary launcher not gone'
        pvc = native('pvc', ROOT_PVC); assert pvc['metadata']['uid'] == ROOT_PVC_UID
        pv = native('pv', pvc['spec']['volumeName'], '')
        assert pv['metadata']['uid'] == ROOT_PV_UID and pv['spec']['claimRef']['uid'] == ROOT_PVC_UID
        assert pv['spec']['csi']['volumeAttributes']['imageName'] == ROOT_BASE
        assert pvc['status']['phase'] == 'Bound' and pvc['spec']['volumeMode'] == 'Block'
        assert pvc['spec']['accessModes'] == ['ReadWriteOnce']
        dv = native('dv', ROOT_PVC); assert dv['metadata']['uid'] == self.plan['root_before']['dv']['uid'] and owns(dv, VM_UID)
        assert owns(pvc, dv['metadata']['uid'])
        return {'vm': identity(obj), 'template_sha256': digest(obj['spec']['template']),
                'vmi': None if vmi is None else identity(vmi), 'root_dv': identity(dv),
                'root_pvc': identity(pvc), 'root_pv': identity(pv), 'root_csi': pv['spec']['csi']}

    def attachment(self, stopped=False):
        rows = json.loads(self.g['psql']('postgres', "SELECT COALESCE(json_agg(json_build_object('id',attachment_id,'state',state,'blocked',protocol_blocked)), '[]'::json) FROM network_attachments WHERE tenant_id='" + TENANT + "' AND instance_id='" + VM + "';", database='vm_resource_candidate_01a168ce'))
        assert len(rows) == 1 and not rows[0]['blocked']
        if 'attachment_id' in self.state:
            assert rows[0]['id'] == self.state['attachment_id']
        if stopped:
            assert rows[0]['state'].lower() == 'reserved'
        return rows[0]

    def fresh(self):
        self.cluster(); current = self.get(); disks = self.get('/disks').get('items', [])
        assert current['id'] == VM and not current['closing'] and int(current['version']) == 1 and current['observedState'] == 'Running'
        op = self.get('/operations/' + CREATE); assert op['state'] == 'succeeded' and op['vmId'] == VM
        assert len(disks) == 1 and disks[0]['diskId'] == ROOT_DISK and disks[0]['attached']
        native_target = self.target(); protection = self.check_protected(); attachment = self.attachment()
        if 'protected_baseline' not in self.state:
            self.state.update(protected_baseline=protection, attachment_id=attachment['id'],
                              initial_vmi_uids=[native_target['vmi']['uid']] if native_target['vmi'] else [])
        return self.persist('fresh', current=current, disks=disks, native=native_target,
                            attachment=attachment, protected_baseline=protection, business_mutations=0)

    def command(self, phase, wait, recover_admission=False):
        assert phase in ALLOWED and 'protected_baseline' in self.state
        self.cluster(); self.check_protected(); stopped = phase != 'stop'
        current = self.get(); self.target(stopped)
        assert not current['closing'] and int(current['version']) == int(next(r for r in requests() if r['phase'] == phase)['body']['data']['expectedVersion'])
        previous = ALLOWED.index(phase) - 1
        if previous >= 0:
            prior = self.state['commands'][ALLOWED[previous]]
            assert self.get('/operations/' + prior['operation_id'])['state'] == 'succeeded'
            assert current['observedState'] == 'Stopped' and not current.get('addresses')
            self.attachment(True)
        if phase == 'offline-snapshot':
            self.physical(require_ready=True)
            assert {d['diskId'] for d in self.get('/disks').get('items', [])} == {ROOT_DISK, *self.state['disk_ids'].values()}
            assert all(d['attached'] for d in self.get('/disks').get('items', []))
        if phase == 'restore':
            assert self.state['snapshot']['snapshotId'] == RESTORE_SNAPSHOT
            assert self.state['native_snapshot']['snapshot']['uid'] == RESTORE_SNAPSHOT_UID
            self.holder(0); assert self.state['holder']['ready'], 'approved original holder must still be Ready'
            assert holder_remaining_seconds(native('pod', POD)) > 0, 'holder deadline elapsed; release original Pod normally, do not Restore'
            preview = self.preview()['preview']
            assert preview['previewDigest'] == RESTORE_DIGEST and preview['snapshotUid'] == RESTORE_SNAPSHOT_UID
            old = copy.deepcopy(self.state['physical']); self.physical(require_ready=True)
            assert [(x['disk']['diskId'], x['pvc'], x['pv'], x['csi']) for x in self.state['physical']] == [(x['disk']['diskId'], x['pvc'], x['pv'], x['csi']) for x in old]
        request = live_request(phase, self.state['disk_ids']); command = self.state['commands'].get(phase)
        if command is None:
            command = {**request, 'body_sha256': digest(request['body']), 'before': current,
                       'snapshot_ids_before': [x['snapshotId'] for x in self.get('/snapshots').get('items', [])] if phase == 'offline-snapshot' else []}
            self.state['commands'][phase] = command; self.persist(phase + '-intent', command=command)
            if phase == 'restore':
                command['old_physical'] = copy.deepcopy(self.state['physical'])
                command['reviewed_preview'] = copy.deepcopy(self.state['preview'])
                command['authorization'] = 'Root reviewed cfcffd646844614fdc2de480439f7dbf6ab6df95ebd9d175562812d182acd849 and approved this exact Restore only'
                self.persist('restore-intent', command=command)
        assert command['body_sha256'] == digest(command['body']) and command['body'] == request['body'] and command['route'] == request['route']
        if not command.get('operation_id'):
            if command['send_attempts']:
                assert recover_admission and phase == 'attach-data-a' and command['send_attempts'] == 1 and command['ack_unknown'], 'no blind or third resend'
                assert 'HTTP 503 reason=VM_DEPENDENCY_NOT_READY' in command.get('transport_error', '')
                assert int(current['version']) == 3 and current['observedState'] == 'Stopped'
                key_value = command['body']['data']['idempotencyKey']
                owner = json.loads(self.g['psql']('postgres', "SELECT COALESCE(json_agg(json_build_object('id',id,'state',state,'action',action)),'[]'::json) FROM vm.operations WHERE tenant_id='" + TENANT + "' AND vm_id='" + VM + "' AND idempotency_key='" + key_value + "';", database='vm_resource_candidate_01a168ce'))
                gov = json.loads(self.g['psql']('postgres', "SELECT COALESCE(json_agg(json_build_object('id',operation_id,'state',dispatch_state,'action',action)),'[]'::json) FROM sys_quota_operations WHERE tenant_id=2 AND resource_tenant_id='" + TENANT + "' AND resource_id='" + VM + "' AND idempotency_key='" + key_value + "';"))
                assert owner == [] and gov == [], 'original operation now exists: observe it, do not resend'
                self.physical(require_ready=True)
                history = command.setdefault('attempt_history', [])
                assert not history, 'recovery was already attempted'
                archived = []
                for suffix in ('sending', 'unknown-or-rejected'):
                    old = RUN / ('evidence/tenant2-cleanup-delete-' + phase + '-' + suffix + '.json')
                    target = RUN / ('evidence/tenant2-cleanup-delete-' + phase + '-attempt01-' + suffix + '.json')
                    assert not target.exists()
                    target.write_bytes(old.read_bytes())
                    archived.append({'path': str(target), 'sha256': hashlib.sha256(target.read_bytes()).hexdigest()})
                history.append({'attempt': 1, 'http_status': 503, 'error': command['transport_error'], 'original_phase_receipts': archived})
                command['recovery_guard'] = {'resource_ops': owner, 'gov_ops': gov, 'key_sha256': hashlib.sha256(key_value.encode()).hexdigest(), 'same_body_sha256': command['body_sha256'], 'native_ready_verified': True, 'authorization': 'Root explicitly approved exactly one same-key/body replay after two-repo absence; never reset send count'}
            else:
                assert not recover_admission and not command['ack_unknown']
            command['send_attempts'] += 1; command['ack_unknown'] = True; self.persist(phase + '-sending', command=command)
            started = time.monotonic()
            try:
                accepted = self.api(command['method'], command['route'], command['body'], self.token)
                command['last_http_elapsed_seconds'] = round(time.monotonic() - started, 6)
            except Exception as error:
                command['last_http_elapsed_seconds'] = round(time.monotonic() - started, 6)
                command['transport_error'] = str(error); self.persist(phase + '-unknown-or-rejected', command=command); raise
            command['accepted'] = accepted; command['http_status'] = self.g['EVENTS'][-1]['status']
            self.persist(phase + '-ack', command=command)
            assert command['http_status'] == 202 and accepted['vmId'] == VM
            command['operation_id'] = str(uuid.UUID(accepted['id'])); command['ack_unknown'] = False
            self.persist(phase + '-pinned', command=command)
        return self.observe(phase, wait)

    def observe(self, phase, wait):
        command = self.state['commands'][phase]; assert command.get('operation_id'), 'unknown ACK: no guessed operation or resend'
        end = time.monotonic() + wait
        while True:
            op = self.get('/operations/' + command['operation_id']); current = self.get()
            assert op['id'] == command['operation_id'] and op['vmId'] == VM
            command.update(operation=op, current=current, disks=self.get('/disks').get('items', []))
            if phase.startswith('create-'):
                label = phase.removeprefix('create-'); matches = [d for d in command['disks'] if d['name'] == command['body']['data']['name']]
                assert len(matches) <= 1
                if matches:
                    d = matches[0]; assert d['role'] == 'data' and str(d['sizeGb']) == '2' and d['serial'] == d['diskId']
                    assert self.state['disk_ids'].get(label, d['diskId']) == d['diskId']; self.state['disk_ids'][label] = d['diskId']
            if phase == 'offline-snapshot':
                fresh = [s for s in self.get('/snapshots').get('items', []) if s['snapshotId'] not in command['snapshot_ids_before']]
                assert len(fresh) <= 1
                if fresh:
                    snap = fresh[0]; assert snap['sourceVmId'] == VM and snap['sourceVmUid'] == VM_UID
                    assert {d['diskId'] for d in snap['disks']} == {ROOT_DISK, *self.state['disk_ids'].values()}
                    self.state['snapshot'] = command['snapshot'] = snap
            value = self.persist(phase + '-observed', command=command)
            if op['state'] in ('succeeded', 'failed') or time.monotonic() >= end:
                return value
            time.sleep(2)

    def physical(self, require_ready=False):
        result = []
        for disk in self.get('/disks').get('items', []):
            if disk['diskId'] not in {ROOT_DISK, *self.state['disk_ids'].values()}:
                raise AssertionError('unexpected extra disk')
            dv = native('dv', 'vd-' + disk['diskId']); pvc = native('pvc', 'vd-' + disk['diskId'])
            value = {'disk': disk, 'dv': None if dv is None else identity(dv),
                     'pvc': None if pvc is None else identity(pvc)}
            if dv:
                assert owns(dv, VM_UID)
            if pvc:
                assert dv and owns(pvc, dv['metadata']['uid']) and pvc['spec']['volumeMode'] == 'Block' and pvc['spec']['accessModes'] == ['ReadWriteOnce']
                assert pvc['spec']['storageClassName'] == 'ani-block'
                if pvc['spec'].get('volumeName'):
                    pv = native('pv', pvc['spec']['volumeName'], ''); assert pv['spec']['claimRef']['uid'] == pvc['metadata']['uid']
                    value.update(pv=identity(pv), csi=pv['spec']['csi'], pvc_phase=pvc['status'].get('phase'), dv_phase=dv.get('status', {}).get('phase'))
            if require_ready:
                assert value.get('pvc_phase') == 'Bound' and value.get('dv_phase') == 'Succeeded', 'disk native preparation not ready; do not Start'
            result.append(value)
        self.state['physical'] = result
        return self.persist('physical', current=self.get(), disks=result, business_mutations=0)

    def snapshot_native(self):
        self.target(True); snap = self.state['snapshot']
        assert snap['ready'] and snap['completeness'] == 'COMPLETE'
        assert self.state['commands']['offline-snapshot']['operation']['state'] == 'succeeded'
        expected = {d['volumeName']: d for d in snap['disks']}; assert len(expected) == 3
        obj = native('virtualmachinesnapshot.snapshot.kubevirt.io', 'vm-snap-' + snap['snapshotId'])
        assert obj['status']['sourceUID'] == VM_UID and obj['status']['readyToUse']
        assert set(obj['status']['snapshotVolumes']['includedVolumes']) == set(expected)
        content = native('virtualmachinesnapshotcontent.snapshot.kubevirt.io', obj['status']['virtualMachineSnapshotContentName'])
        assert content['spec']['source']['virtualMachine']['metadata']['uid'] == VM_UID and content['status']['readyToUse']
        assert 'Online' not in obj['status'].get('indications', [])
        volumes = []
        physical = {item['disk']['diskId']: item for item in self.state['physical']}
        for backup in content['spec']['volumeBackups']:
            assert backup['volumeName'] in expected
            disk_id = expected[backup['volumeName']]['diskId']
            assert backup['persistentVolumeClaim']['metadata']['uid'] == physical[disk_id]['pvc']['uid']
            vs = native('volumesnapshot.snapshot.storage.k8s.io', backup['volumeSnapshotName'])
            assert vs['spec']['source']['persistentVolumeClaimName'] == backup['persistentVolumeClaim']['metadata']['name']
            assert vs['status']['readyToUse'] and any(o.get('uid') == content['metadata']['uid'] for o in vs['metadata'].get('ownerReferences', []))
            vsc = native('volumesnapshotcontent.snapshot.storage.k8s.io', vs['status']['boundVolumeSnapshotContentName'], '')
            assert vsc['spec']['volumeSnapshotRef']['uid'] == vs['metadata']['uid'] and vsc['status']['readyToUse'] and vsc['status']['snapshotHandle']
            cls = native('volumesnapshotclass.snapshot.storage.k8s.io', vs['spec']['volumeSnapshotClassName'], '')
            assert cls['metadata']['uid'] == '4ef77654-dcae-4cca-b709-0d570426cf4e' and cls['deletionPolicy'] == vsc['spec']['deletionPolicy'] == 'Delete'
            assert cls['driver'] == vsc['spec']['driver'] == 'rook-ceph.rbd.csi.ceph.com'
            volumes.append({'disk_id': expected[backup['volumeName']]['diskId'], 'volume': backup['volumeName'],
                            'source_pvc': {k: backup['persistentVolumeClaim']['metadata'].get(k) for k in ('name', 'namespace', 'uid')},
                            'vs': identity(vs), 'vsc': identity(vsc), 'snapshot_handle': vsc['status']['snapshotHandle'], 'class': identity(cls)})
        assert len(volumes) == 3
        self.state['native_snapshot'] = {'snapshot': identity(obj), 'content': identity(content), 'volumes': volumes}
        return self.persist('snapshot-native', native=self.state['native_snapshot'], snapshot=snap, business_mutations=0)

    def holder(self, wait):
        self.cluster(); self.check_protected(); self.target(True); self.attachment(True)
        current = self.get(); assert int(current['version']) == 7 and current['observedState'] == 'Stopped' and not current['closing']
        assert self.state.get('native_snapshot') and self.state['snapshot']['ready'] and self.state['snapshot']['completeness'] == 'COMPLETE'
        manifest = helper_manifest(); holder = self.state.get('holder')
        if not holder:
            node = self.cluster(); assert any(IMAGE in x['names'] for x in node['status'].get('images', [])), 'image exact digest not cached; noPull'
            assert native('pod', POD) is None, 'same-name Pod exists; no adoption'
            holder = {'manifest': manifest, 'manifest_sha256': digest(manifest), 'send_attempts': 1, 'ack_unknown': True}
            self.state['holder'] = holder; self.persist('holder-intent', holder=holder)
            result = subprocess.run(K + ['create', '-f', '-', '-o', 'json'], input=json.dumps(manifest), capture_output=True, text=True, timeout=15)
            holder['create_exit'] = result.returncode; holder['create_stderr'] = result.stderr
            if result.returncode:
                self.persist('holder-create-rejected', holder=holder); raise RuntimeError('holder CREATE rejected; no privilege bypass or resend')
            obj = json.loads(result.stdout); holder.update(ref=identity(obj), ack_unknown=False)
            self.persist('holder-created', holder=holder)
        assert holder.get('ref'), 'unknown holder ACK: observe exact original manifest only; do not recreate'
        end = time.monotonic() + wait
        while True:
            obj = native('pod', POD); assert obj and identity(obj) == holder['ref']
            assert any(o.get('uid') == VM_UID for o in obj['metadata'].get('ownerReferences', []))
            assert obj['spec']['volumes'][0]['persistentVolumeClaim'] == {'claimName': ROOT_PVC, 'readOnly': True}
            assert obj['spec']['containers'][0]['image'] == IMAGE and obj['spec']['containers'][0]['imagePullPolicy'] == 'Never'
            holder['status'] = obj.get('status', {}); holder['ready'] = (obj['spec'].get('nodeName') == 'ani-01' and obj.get('status', {}).get('phase') == 'Running' and any(c['type'] == 'Ready' and c['status'] == 'True' for c in obj.get('status', {}).get('conditions', [])))
            holder['volume_attachments'] = [identity(a) for a in native('volumeattachments', namespace='')['items'] if a['spec'].get('source', {}).get('persistentVolumeName') == self.plan['root_before']['pv']['name'] and a['spec'].get('nodeName') == 'ani-01' and a.get('status', {}).get('attached') is True]
            value = self.persist('holder-observed', holder=holder, current=current)
            if holder['ready'] or obj.get('status', {}).get('phase') in ('Failed', 'Succeeded') or time.monotonic() >= end:
                return value
            time.sleep(2)

    def holder_cleanup(self):
        holder = self.state['holder']; assert holder.get('ref'), 'no exact registered UID'
        obj = native('pod', POD)
        if obj is not None:
            assert identity(obj) == holder['ref']
            vnic_name = obj['metadata'].get('annotations', {}).get('networking.kubercloud.com/vnic')
            if vnic_name:
                vnic = native('vnics.networking.kubercloud.com', vnic_name)
                holder['vnic_name'] = vnic_name
                if vnic:
                    assert any(x.get('kind') == 'Pod' and x.get('uid') == holder['ref']['uid'] for x in vnic['metadata'].get('ownerReferences', []))
                    holder['vnic'] = identity(vnic)
            if not holder.get('cleanup_send_attempts'):
                body = {'apiVersion': 'v1', 'kind': 'DeleteOptions', 'preconditions': {'uid': obj['metadata']['uid'], 'resourceVersion': obj['metadata']['resourceVersion']}}
                holder.update(cleanup_send_attempts=1, cleanup_preconditions=body['preconditions'])
                self.persist('holder-cleanup-intent', holder=holder)
                result = subprocess.run(K + ['delete', '--raw', '/api/v1/namespaces/' + NS + '/pods/' + POD, '-f', '-'], input=json.dumps(body), capture_output=True, text=True, timeout=15)
                holder['cleanup'] = {'exit': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr}
                self.persist('holder-cleanup-ack', holder=holder)
        holder['gone'] = native('pod', POD) is None
        return self.persist('holder-cleanup', holder=holder, business_vm_delete_requests=0)

    def restore_gate(self):
        command = self.state['commands']['restore']; op_id = str(uuid.UUID(command['operation_id']))
        self.cluster(); self.check_protected()
        vm = native('vm', 'vm-' + VM); assert vm['metadata']['uid'] == VM_UID
        assert native('vmi', 'vm-' + VM) is None and vm['spec']['runStrategy'] == 'Manual'
        rows = json.loads(self.g['psql']('postgres', "SELECT json_build_object('operation',json_build_object('ID',o.id,'State',o.state,'Step',o.record->'Step','Snapshot',o.record->'Snapshot'),'vm',json_build_object('Version',v.version,'Closing',v.closing,'Native',v.current_configuration->'Native','InitializationSecret',v.current_configuration->'InitializationSecret','Disks',v.current_configuration->'Disks','RetiredDisks',v.current_configuration->'RetiredDisks')) FROM vm.operations o JOIN vm.virtual_machines v ON v.tenant_id=o.tenant_id AND v.id=o.vm_id WHERE o.tenant_id='" + TENANT + "' AND o.vm_id='" + VM + "' AND o.id='" + op_id + "';", database='vm_resource_candidate_01a168ce'))
        assert rows['operation']['ID'] == op_id and rows['vm']['Version'] == 8 and not rows['vm']['Closing']
        assert rows['vm']['Native']['UID'] == VM_UID
        progress = rows['operation']['Snapshot'] or {}
        checkpoints = {k: progress.get(k) for k in ('NativeRestoreCompleted', 'MappingCommitted', 'CleanupCompleted')}
        holder = native('pod', POD); assert holder and identity(holder) == self.state['holder']['ref']
        assert holder['spec']['volumes'][0]['persistentVolumeClaim'] == {'claimName': ROOT_PVC, 'readOnly': True}
        remaining = holder_remaining_seconds(holder)
        holder_running = holder['status'].get('phase') == 'Running' and not holder['metadata'].get('deletionTimestamp') and remaining > 0
        old_root = native('pvc', ROOT_PVC)
        if old_root:
            assert old_root['metadata']['uid'] == ROOT_PVC_UID
        old_blocked = bool(old_root and old_root['metadata'].get('deletionTimestamp') and 'kubernetes.io/pvc-protection' in old_root['metadata'].get('finalizers', []))
        expected = {ROOT_DISK, *self.state['disk_ids'].values()}
        current = rows['vm']['Disks']; assert {x['ID'] for x in current} == expected
        old = {x['disk']['diskId']: x for x in command['old_physical']}
        new_mapping = []
        for disk in current:
            ref = disk['PVC']; value = {'disk_id': disk['ID'], 'pvc': ref, 'dv': disk['DV'], 'cleanup': disk.get('Cleanup')}
            pvc = native('pvc', ref['Name']) if ref.get('Name') else None
            if checkpoints['MappingCommitted'] is True:
                assert pvc, 'committed restored PVC missing; do not report gate'
            if pvc:
                assert pvc['metadata']['uid'] == ref['UID']
                if checkpoints['MappingCommitted'] is True:
                    assert owns(pvc, VM_UID) and pvc['metadata']['uid'] != old[disk['ID']]['pvc']['uid']
                    assert pvc['spec']['volumeMode'] == 'Block' and pvc['spec']['accessModes'] == ['ReadWriteOnce'] and pvc['spec']['storageClassName'] == 'ani-block'
                if pvc['spec'].get('volumeName'):
                    pv = native('pv', pvc['spec']['volumeName'], ''); assert pv['spec']['claimRef']['uid'] == pvc['metadata']['uid']
                    value.update(pv=identity(pv), csi=pv['spec']['csi'])
            new_mapping.append(value)
        restore_ref = progress.get('NativeRestore') or {}; restore = None
        if restore_ref.get('UID'):
            restore = native('virtualmachinerestore.snapshot.kubevirt.io', restore_ref['Name'])
            assert restore and restore['metadata']['uid'] == restore_ref['UID'] and owns(restore, VM_UID)
            assert restore['spec']['virtualMachineSnapshotName'] == self.state['native_snapshot']['snapshot']['name']
        gate = restore_gate_check(checkpoints, old_blocked, holder_running)
        if gate:
            assert any(d['ID'] == ROOT_DISK and d['PVC']['UID'] == ROOT_PVC_UID for d in rows['vm']['RetiredDisks'])
            assert {d['ID'] for d in progress['RestoredDisks']} == expected
            assert {d['ID']: d['PVC'] for d in progress['RestoredDisks']} == {d['ID']: d['PVC'] for d in current}
            assert restore and restore['status'].get('complete') is True
        value = self.persist('restore-cleanup-gate-observed', mapping_gate_pass=gate, checkpoints=checkpoints,
            durable=rows, new_mapping=new_mapping, old_physical=command['old_physical'],
            native_restore=None if restore is None else {'ref': identity(restore), 'status': restore.get('status', {})},
            old_root=None if old_root is None else {'ref': identity(old_root), 'deletion_timestamp': old_root['metadata'].get('deletionTimestamp'), 'finalizers': old_root['metadata'].get('finalizers', [])},
            holder={'ref': identity(holder), 'phase': holder['status'].get('phase'), 'running': holder_running, 'active_deadline_seconds': holder['spec']['activeDeadlineSeconds'], 'creation_timestamp': holder['metadata']['creationTimestamp'], 'remaining_seconds': round(remaining, 3)},
            known_old_root_family={'base': ROOT_BASE, 'temp': ROOT_BASE + '-temp', 'temp_image_id': '20cc871b765f9e', 'reference': '/home/chabking/.codex/tasks/vm-mvp-20261023-01a168ce/final-source-cleanup-read-01/observed.json', 'not_attributed_to_historical_import_delete': True},
            protected_unchanged=True, vm_delete_requests=0)
        self.state['restore_gate'] = value; self.save(STATE, self.state)
        return value

    def delete_during_cleanup(self, wait):
        phase = 'delete-during-cleanup'
        command = self.state['commands'].get(phase)
        if command is None:
            gate = self.restore_gate(); assert gate['mapping_gate_pass'], 'no Delete outside fresh real cleanup gate'
            create = self.get('/operations/' + CREATE); current = self.get()
            assert create['id'] == CREATE and create['vmId'] == VM and create['state'] == 'succeeded'
            assert int(current['version']) == 8 and not current['closing']
            request = copy.deepcopy(next(x for x in requests() if x['phase'] == phase))
            command = {**request, 'body_sha256': digest(request['body']), 'before': current,
                'original_create_operation_id': CREATE, 'gate': gate,
                'authorization': 'Root reviewed actual gate 0ff0ca0bc8f463654ecd9f466ff9ba20f300729cf578cd84d1df548715c86084 and approved original Delete key/body once'}
            secret_ref = gate['durable']['vm']['InitializationSecret'] or {}
            if secret_ref.get('UID'):
                secret = native('secret', secret_ref['Name'])
                assert secret['metadata']['uid'] == secret_ref['UID'] and owns(secret, VM_UID)
                command['initialization_secret'] = identity(secret)
            command['network_before'] = self.attachment(True)
            self.state['commands'][phase] = command
            self.persist('delete-during-cleanup-intent', command=command)
        assert command['body_sha256'] == digest(command['body']) and command['body'] == next(x for x in requests() if x['phase'] == phase)['body']
        if not command.get('operation_id') and not command.get('accepted'):
            assert command['send_attempts'] == 0 and not command['ack_unknown'], 'unknown ACK: only original lookup, never blind resend'
            command['send_attempts'] = 1; command['ack_unknown'] = True
            self.persist('delete-during-cleanup-sending', command=command)
            started = time.monotonic()
            try:
                accepted = self.api('DELETE', BASE, command['body'], self.token)
                command['last_http_elapsed_seconds'] = round(time.monotonic() - started, 6)
            except Exception as error:
                command['last_http_elapsed_seconds'] = round(time.monotonic() - started, 6)
                command['transport_error'] = str(error); self.persist('delete-during-cleanup-unknown-or-rejected', command=command); raise
            command.update(accepted=accepted, http_status=self.g['EVENTS'][-1]['status'])
            self.persist('delete-during-cleanup-ack', command=command)
        if not command.get('operation_id'):
            # Gov Delete has its durable-admission response, unlike Command.
            # A saved successful ACK is pinned directly, never sent again.
            command.update(operation_id=delete_ack_operation(command), ack_unknown=False)
            self.persist('delete-during-cleanup-pinned', command=command)
        # The original holder remains present through the accepted Delete.
        # Release only this registered Pod; owner GC may have removed it first.
        if not self.state['holder'].get('cleanup') and not self.state['holder'].get('gone'):
            self.holder_cleanup()
        return self.observe(phase, wait)

    def cleanup_native(self):
        command = self.state['commands']['delete-during-cleanup']; assert command['send_attempts'] == 1
        self.cluster(); self.check_protected()
        operation = self.get('/operations/' + command['operation_id']); current = self.get()
        restore_operation = self.get('/operations/' + self.state['commands']['restore']['operation_id'])
        gate = command['gate']; refs = [gate['native_restore']['ref'], self.state['holder']['ref']]
        refs += [self.state['native_snapshot'][k] for k in ('snapshot', 'content')]
        for volume in self.state['native_snapshot']['volumes']:
            refs += [volume['vs'], volume['vsc']]
        for volume in gate['old_physical']:
            refs += [volume[k] for k in ('dv', 'pvc', 'pv')]
        for volume in gate['new_mapping']:
            refs += [volume[k] for k in ('pvc', 'pv')]
        refs += self.state['holder'].get('volume_attachments', [])
        refs += [{'kind': 'VirtualMachine', 'namespace': NS, 'name': 'vm-' + VM, 'uid': VM_UID}]
        refs += [{'kind': 'VirtualMachineInstance', 'namespace': NS, 'name': 'vm-' + VM, 'uid': uid} for uid in self.state['initial_vmi_uids']]
        historical = json.loads((RUN / 'evidence/product-tenant2-linux-native.json').read_text())
        assert historical['vm']['uid'] == VM_UID
        if historical.get('launcher'): refs.append(historical['launcher'])
        if command.get('initialization_secret'): refs.append(command['initialization_secret'])
        if self.state['holder'].get('vnic'): refs.append(self.state['holder']['vnic'])
        resources = {'VirtualMachine': 'virtualmachines.kubevirt.io', 'VirtualMachineInstance': 'virtualmachineinstances.kubevirt.io',
            'VirtualMachineRestore': 'virtualmachinerestores.snapshot.kubevirt.io', 'VirtualMachineSnapshot': 'virtualmachinesnapshots.snapshot.kubevirt.io',
            'VirtualMachineSnapshotContent': 'virtualmachinesnapshotcontents.snapshot.kubevirt.io', 'VolumeSnapshot': 'volumesnapshots.snapshot.storage.k8s.io',
            'VolumeSnapshotContent': 'volumesnapshotcontents.snapshot.storage.k8s.io', 'VolumeAttachment': 'volumeattachments.storage.k8s.io',
            'DataVolume': 'datavolumes.cdi.kubevirt.io', 'PersistentVolumeClaim': 'pvc', 'PersistentVolume': 'pv', 'Pod': 'pod', 'Secret': 'secret',
            'VNic': 'vnics.networking.kubercloud.com'}
        measured, seen = [], set()
        for ref in refs:
            lower = {k: ref.get(k, ref.get('UID' if k == 'uid' else k.capitalize(), '')) for k in ('kind', 'namespace', 'name', 'uid')}
            if not lower['uid'] or lower['uid'] in seen: continue
            seen.add(lower['uid'])
            obj = native(resources[lower['kind']], lower['name'], lower['namespace'])
            assert obj is None or obj['metadata']['uid'] == lower['uid'], 'same-name foreign object, no absent conclusion'
            measured.append({'original': lower, 'gone': obj is None})
        pv_names = {v['pv']['name'] for v in gate['old_physical']} | {v['pv']['name'] for v in gate['new_mapping']}
        volume_attachments = [identity(a) for a in native('volumeattachments', namespace='')['items'] if a['spec'].get('source', {}).get('persistentVolumeName') in pv_names]
        holder_vnics = [identity(v) for v in native('vnics.networking.kubercloud.com')['items'] if any(o.get('uid') == self.state['holder']['ref']['uid'] for o in v['metadata'].get('ownerReferences', []))]
        attachment = self.attachment()
        passed = (operation['state'] == 'succeeded' and current['closing'] and current['observedState'] == 'Deleted'
            and not current.get('addresses') and self.get('/disks').get('items', []) == []
            and all(x['gone'] for x in measured) and not volume_attachments and not holder_vnics
            and attachment['state'].lower() == 'released' and not attachment['blocked'])
        self.state['cleanup_native'] = {'result': 'PASS' if passed else 'PENDING_NORMAL_GC', 'operation': operation,
            'restore_operation': restore_operation, 'current': current, 'objects': measured, 'volume_attachments_for_six_pvs': volume_attachments,
            'holder_owner_uid_vnics': holder_vnics, 'holder_vnic_historical_uid_measured': bool(self.state['holder'].get('vnic')),
            'attachment': attachment, 'protected_unchanged': True}
        return self.persist('cleanup-native', **self.state['cleanup_native'], backend='NOT_MEASURED_IN_THIS_NATIVE_PHASE')

    def cleanup_backend(self):
        assert self.state['commands']['delete-during-cleanup']['send_attempts'] == 1
        backend = runpy.run_path(str(RUN / 'vm-product-import-delete-backend.py'))
        base = {'native': lambda kind, name=None, namespace=None: native(kind, name, namespace or ''), 'K': K}
        gate = self.state['commands']['delete-during-cleanup']['gate']
        originals = [{'name': v['csi']['volumeAttributes']['imageName'], 'basis': 'frozen exact owned PVC/PV/CSI handle'}
                     for v in gate['old_physical'] + gate['new_mapping']]
        originals.append({'name': ROOT_BASE + '-temp', 'id': '20cc871b765f9e', 'basis': gate['known_old_root_family']})
        result = {'result': 'NOT_MEASURED', 'images': [], 'trash_measured': False, 'manual_backend_writes': 0}
        for original in originals:
            try:
                value = backend['image_info'](base, original['name'])
                assert value['result'] == 'ABSENT' or not original.get('id') or value['id'] == original['id']
                result['images'].append({'original': original, 'current': value})
            except Exception as error:
                result['error'] = type(error).__name__ + ': ' + str(error)
                self.state['cleanup_backend'] = result
                return self.persist('cleanup-backend', **result)
            self.persist('cleanup-backend-partial', **result)
        try:
            raw = backend['ceph'](base, ['rbd', 'trash', 'ls', '-p', 'ani-block-pool', '--format=json'])
            assert raw is not None, 'trash read unavailable, never infer absence'
            trash = json.loads(raw); result['trash_measured'] = True
            for value in result['images']:
                original = value['original']
                value['matching_trash'] = [x for x in trash if x.get('name') == original['name'] or original.get('id') and x.get('id') == original['id']]
            result['result'] = 'PASS' if all(x['current']['result'] == 'ABSENT' and not x['matching_trash'] for x in result['images']) else 'PENDING_NORMAL_GC'
        except Exception as error:
            result['error'] = type(error).__name__ + ': ' + str(error)
        self.state['cleanup_backend'] = result
        return self.persist('cleanup-backend', **result, historical_import_delete_cases='UNCHANGED')

    def preview(self):
        self.cluster(); self.check_protected(); self.target(True); self.attachment(True)
        holder = self.state['holder']; assert holder['ready']
        obj = native('pod', POD); assert identity(obj) == holder['ref'] and obj['status']['phase'] == 'Running'
        snap = self.state['snapshot']; current = self.get(); assert int(current['version']) == 7 and not current['closing']
        value = self.api('POST', BASE + '/snapshots/' + snap['snapshotId'] + '/restore-preview', {'data': {'snapshotId': snap['snapshotId']}}, self.token)
        assert self.g['EVENTS'][-1]['status'] == 200 and int(value['version']) == 7
        assert value['snapshotId'] == snap['snapshotId'] and len(value['previewDigest']) == 64
        assert {d['diskId'] for d in value['disks']} == {ROOT_DISK, *self.state['disk_ids'].values()}
        assert all(d['action'] == 'restore-attached' for d in value['disks'])
        self.state['preview'] = value
        return self.persist('preview-for-root-review', preview=value, holder=holder, current=current,
                            protected_unchanged=True, restore_requests=0, delete_requests=0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=('prepare', 'self-test', 'fresh', 'command', 'observe', 'physical', 'snapshot-native', 'holder', 'holder-cleanup', 'preview', 'restore-gate', 'delete-during-cleanup', 'cleanup-native', 'cleanup-backend'))
    parser.add_argument('--evidence', type=Path, default=RUN / 'evidence')
    parser.add_argument('--output', type=Path, default=RUN / 'private/gov-candidate/tenant2-cleanup-delete-prepared.json')
    parser.add_argument('--intent', choices=ALLOWED + ('delete-during-cleanup',))
    parser.add_argument('--execute-authorized', action='store_true')
    parser.add_argument('--recover-admission', action='store_true', help='only the explicitly approved once-only same AttachA body/key after exact two-repo absence')
    parser.add_argument('--wait-seconds', type=int, choices=range(21), default=0)
    args = parser.parse_args()
    if args.phase == 'self-test':
        self_test(); return
    if args.phase != 'prepare':
        with LOCK.open('a') as lock:
            end = time.monotonic() + 5
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB); break
                except BlockingIOError:
                    if time.monotonic() >= end:
                        print('LOCK_BUSY'); raise SystemExit(73)
                    time.sleep(.2)
            driver = Live()
            if args.phase in ('command', 'holder', 'holder-cleanup', 'delete-during-cleanup'):
                assert args.execute_authorized, 'explicit stage authorization required'
            if args.phase == 'fresh': result = driver.fresh()
            elif args.phase == 'command': result = driver.command(args.intent, args.wait_seconds, args.recover_admission)
            elif args.phase == 'observe': result = driver.observe(args.intent, args.wait_seconds)
            elif args.phase == 'physical': result = driver.physical()
            elif args.phase == 'snapshot-native': result = driver.snapshot_native()
            elif args.phase == 'holder': result = driver.holder(args.wait_seconds)
            elif args.phase == 'holder-cleanup': result = driver.holder_cleanup()
            elif args.phase == 'restore-gate': result = driver.restore_gate()
            elif args.phase == 'delete-during-cleanup': result = driver.delete_during_cleanup(args.wait_seconds)
            elif args.phase == 'cleanup-native': result = driver.cleanup_native()
            elif args.phase == 'cleanup-backend': result = driver.cleanup_backend()
            else: result = driver.preview()
            print(json.dumps({'phase': result['phase'], 'vm_id': VM, 'operation': result.get('command', {}).get('operation'), 'holder_ready': result.get('holder', {}).get('ready'), 'preview_digest': result.get('preview', {}).get('previewDigest'), 'mapping_gate_pass': result.get('mapping_gate_pass'), 'checkpoints': result.get('checkpoints'), 'receipt': str(RUN / 'evidence/tenant2-cleanup-delete-live.json')}))
        return
    assert os.environ.get('SSH_CONNECTION', '').split()[2:3] == ['172.16.101.31'], 'ssh fedora only'
    value = prepare(args.evidence)
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as handle:
        json.dump(value, handle, indent=2); handle.write('\n'); handle.flush(); os.fsync(handle.fileno())
    print(json.dumps({'phase': value['phase'], 'plan_sha256': value['plan_sha256'], 'plan': str(args.output),
                      'api_calls': 0, 'business_mutations': 0}))


if __name__ == '__main__':
    main()
