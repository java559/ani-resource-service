#!/usr/bin/env python3
"""Task-only snapshot environment driver; default action is read-only preflight.

Run only through ssh fedora. No action grants approval: mutations additionally
require --execute-approved and a reference to the human approval. The optional
KubeVirt vmStateStorageClass change is a separate action, never part of apply.

preflight --objects <snapshot-environment-proposed-objects.json>
status
apply --execute-approved --approval-reference <record>
vm-state-enable --execute-approved --approval-reference <separate record>
vm-state-restore --execute-approved --approval-reference <record>
restore --execute-approved --approval-reference <record>

Unknown writes are observed first. If a prepared create/patch is still absent
or at its original value, another write needs --retry-unknown <journal-id>.
Delete uses the API's UID/resourceVersion preconditions, not kubectl's ordinary
name-based deletion. No workload, CSI backend or finalizer is manipulated.
"""

import argparse
import copy
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid


RUN = Path('/home/chabking/workspace/ani-resource-service-runs/vm-mvp-20261023-01a168ce')
OUT = RUN / 'evidence/snapshot-environment'
LOCK = Path('/home/chabking/workspace/ani-network-service-runs/net05a-heavy.lock')
K = ['/home/chabking/ani-four-service-deploy-20261012/tools/kubectl',
     '--kubeconfig=/home/chabking/ani-four-service-deploy-20261012/private/kubeconfig',
     '--request-timeout=15s']
TASK = 'vm-mvp-20261023-01a168ce'
CLUSTER_UID = '5277649d-e28d-4a0a-ae76-8354365c63bc'
KV_UID = 'a3f905a6-9829-42d2-8f18-5ff30192ffc8'
SC_UID = 'e7813a57-3d1c-47e4-a298-86212a59e51e'
PROFILE_UID = '83354750-8885-41e0-9c6d-5334ffdd4bcb'
RETAIN_UID = 'a1568367-0da2-4ede-86c0-f32ea790772d'
CDI_UID = '4f9c62da-a503-48e0-bbed-9bfd2509e542'
NODES = {'ani-01': ('172.16.101.10', '201e41ac-c50d-4c77-b118-72d1ed93e111'),
         'ani-02': ('172.16.101.11', '73564fa7-98ad-45d4-9576-be59f6d5d245'),
         'ani-03': ('172.16.101.12', 'af992ed9-cbe1-4f64-8fac-7433808bfab2')}
DRIVER = 'rook-ceph.rbd.csi.ceph.com'
SC = 'ani-vm-mvp-block-01a168ce'
WFFC = 'ani-vm-mvp-block-wffc-01a168ce'
TASK_CLASSES = (SC, WFFC)
VSC = 'ani-vm-mvp-rbd-delete-01a168ce'
PROPOSAL_SHA = '4708213138582571dfe45265e1f8b00ca52228eb15bd4107d7ac6bd9caeb1c1a'
INTENT = 'vm-mvp-test.ani.io/creation-intent'
SOURCE = 'vm-mvp-test.ani.io/source-sha256'
RESOURCES = {
    'StorageClass': ('storageclasses.storage.k8s.io', '/apis/storage.k8s.io/v1/storageclasses/'),
    'VolumeSnapshotClass': ('volumesnapshotclasses.snapshot.storage.k8s.io', '/apis/snapshot.storage.k8s.io/v1/volumesnapshotclasses/'),
    'StorageProfile': ('storageprofiles.cdi.kubevirt.io', '/apis/cdi.kubevirt.io/v1beta1/storageprofiles/'),
}
OBJECT_NAMES = {'StorageClass': SC, 'StorageProfile': SC, 'VolumeSnapshotClass': VSC,
                'WFFCStorageClass': WFFC, 'WFFCStorageProfile': WFFC}
CLASS_ROLES = ('StorageClass', 'WFFCStorageClass')
PROFILE_ROLES = ('StorageProfile', 'WFFCStorageProfile')
CREATE_ROLES = ('StorageClass', 'VolumeSnapshotClass', 'WFFCStorageClass')
RESTORE_ORDER = ('WFFCStorageClass', 'WFFCStorageProfile', 'StorageClass',
                 'StorageProfile', 'VolumeSnapshotClass')
DEFAULT_ANNOTATIONS = ('storageclass.kubernetes.io/is-default-class',
                       'storageclass.beta.kubernetes.io/is-default-class',
                       'snapshot.storage.kubernetes.io/is-default-class')


class NotConverged(RuntimeError):
    """A native status may converge; identity/spec failures may not be retried."""


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def object_kind(role):
    return role.removeprefix('WFFC')


def resource_for(role):
    return RESOURCES[object_kind(role)]


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def identity(obj):
    m = obj['metadata']
    return {'kind': obj['kind'], 'name': m['name'], 'namespace': m.get('namespace', ''),
            'uid': m['uid'], 'resourceVersion': m['resourceVersion']}


def intent_body(obj):
    """All desired fields, excluding server bookkeeping and controller status."""
    body = {k: copy.deepcopy(v) for k, v in obj.items() if k not in ('metadata', 'status')}
    body['metadata'] = {k: copy.deepcopy(obj['metadata'][k]) for k in
                        ('name', 'namespace', 'labels', 'annotations', 'ownerReferences')
                        if k in obj['metadata']}
    return body


def atomic_json(path, value, exclusive=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(value, indent=2, sort_keys=True) + '\n').encode()
    if exclusive:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    else:
        tmp = path.with_suffix('.tmp')
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)


def command(args, body=None):
    result = subprocess.run(K + args, input=None if body is None else json.dumps(body),
                            text=True, capture_output=True, timeout=25)
    require(result.returncode == 0, 'kubectl failed: ' + result.stderr[-1800:])
    return result.stdout


def get(resource, name, namespace='', optional=False):
    args = ['get', resource, name, '-o', 'json']
    if namespace:
        args += ['-n', namespace]
    if optional:
        args += ['--ignore-not-found=true']
    raw = command(args).strip()
    return json.loads(raw) if raw else None


def listing(resource, namespaced=False):
    return json.loads(command(['get', resource, '-o', 'json'] + (['-A'] if namespaced else [])))['items']


def same_uid(obj, uid, description):
    require(obj is not None and obj['metadata']['uid'] == uid, description + ' UID changed/absent')
    require(not obj['metadata'].get('deletionTimestamp'), description + ' is terminating')


def no_default(obj):
    annotations = obj['metadata'].get('annotations', {})
    require(not any(str(annotations.get(k, '')).lower() == 'true' for k in DEFAULT_ANNOTATIONS),
            'task class must never be default')


def native_profile_owner(profile, sc):
    owners = profile['metadata'].get('ownerReferences', [])
    require(len(owners) == 1 and owners[0].get('apiVersion') == 'cdi.kubevirt.io/v1beta1'
            and owners[0].get('kind') == 'CDI' and owners[0].get('name') == 'cdi'
            and owners[0].get('uid') == CDI_UID and owners[0].get('controller') is True,
            'task Profile does not have the exact native CDI controller owner')
    require(profile['metadata'].get('labels', {}).get('app.kubernetes.io/managed-by') == 'cdi-controller', 'not a native CDI profile')
    require(profile['metadata'].get('creationTimestamp', '') >= sc['metadata']['creationTimestamp'], 'same-name Profile predates task SC')


def verify_scope(ready=False):
    connection = os.environ.get('SSH_CONNECTION', '').split()
    require(len(connection) == 4 and connection[2] == '172.16.101.31', 'run through ssh fedora only')
    server = command(['config', 'view', '--minify', '-o', 'jsonpath={.clusters[0].cluster.server}'])
    require(server == 'https://172.16.101.10:6443', 'unapproved API endpoint')
    same_uid(get('namespace', 'kube-system'), CLUSTER_UID, 'cluster')
    nodes = listing('nodes')
    require({x['metadata']['name'] for x in nodes} == set(NODES), 'cluster node set changed')
    for node in nodes:
        ip, uid = NODES[node['metadata']['name']]
        same_uid(node, uid, 'node ' + node['metadata']['name'])
        addresses = node['status'].get('addresses', [])
        require(any(x['type'] == 'InternalIP' and x['address'] == ip for x in addresses), 'node address changed')
        if ready:
            require(any(x['type'] == 'Ready' and x['status'] == 'True' for x in node['status']['conditions']), 'node not Ready')
            require(not any(x['type'].endswith('Pressure') and x['status'] == 'True' for x in node['status']['conditions']), 'node under pressure')


def source_objects():
    objects = {'sc': get('storageclasses', 'ani-block'),
               'profile': get('storageprofiles.cdi.kubevirt.io', 'ani-block'),
               'retain': get('volumesnapshotclasses.snapshot.storage.k8s.io', 'ani-rbd-retain'),
               'kv': get('kubevirts.kubevirt.io', 'kubevirt', 'kubevirt'),
               'cdi': get('cdis.cdi.kubevirt.io', 'cdi')}
    for key, uid in (('sc', SC_UID), ('profile', PROFILE_UID), ('retain', RETAIN_UID), ('kv', KV_UID), ('cdi', CDI_UID)):
        same_uid(objects[key], uid, key)
    native_profile_owner(objects['profile'], objects['sc'])
    require(objects['sc']['provisioner'] == DRIVER and objects['sc']['reclaimPolicy'] == 'Delete', 'source SC policy changed')
    require(objects['retain']['driver'] == DRIVER and objects['retain']['deletionPolicy'] == 'Retain', 'original Retain class changed')
    kv = objects['kv']
    require(kv['status'].get('phase') == 'Deployed' and kv['status'].get('observedKubeVirtVersion') == 'v1.9.0', 'KubeVirt version/readiness changed')
    return objects


def rbd_choices(pin_original=False):
    """The new second RBD class may not make another existing choice ambiguous."""
    profiles = {x['metadata']['name']: x for x in listing('storageprofiles.cdi.kubevirt.io')}
    classes = {x['metadata']['name']: x for x in listing('volumesnapshotclasses.snapshot.storage.k8s.io')}
    choices = []
    for sc in listing('storageclasses'):
        if sc.get('provisioner') != DRIVER or sc['metadata']['name'] in TASK_CLASSES:
            continue
        name = sc['metadata']['name']
        profile = profiles.get(name)
        require(profile is not None, 'RBD StorageProfile missing: ' + name)
        selected = profile.get('spec', {}).get('snapshotClass')
        observed = profile.get('status', {}).get('snapshotClass')
        require(profile.get('status', {}).get('storageClass') == name
                and profile.get('status', {}).get('provisioner') == DRIVER, 'RBD profile status identity mismatch: ' + name)
        if name == 'ani-block' and pin_original and selected is None:
            require(observed == 'ani-rbd-retain', 'original observed snapshot choice drifted')
            selected = observed
        require(bool(selected), 'RBD profile needs an explicit safe selection before adding a class: ' + name)
        require(selected == observed, 'RBD profile selection not converged: ' + name)
        target = classes.get(selected)
        require(target is not None and target.get('driver') == DRIVER and not target['metadata'].get('deletionTimestamp'), 'invalid RBD snapshot selection: ' + name)
        choices.append({'storageclass': identity(sc), 'profile': identity(profile),
                        'snapshotclass': identity(target), 'selection': selected})
    return choices


def load_proposal(path, sources):
    raw = Path(path).read_bytes()
    require(hashlib.sha256(raw).hexdigest() == PROPOSAL_SHA, 'reviewed proposal bytes changed')
    values = json.loads(raw)['items']
    roles = {(object_kind(role), name): role for role, name in OBJECT_NAMES.items()}
    require(len(values) == 5 and all((x['kind'], x['metadata']['name']) in roles for x in values), 'unexpected proposal objects')
    proposed = {roles[(x['kind'], x['metadata']['name'])]: x for x in values}
    validate_proposal(proposed, sources)
    return proposed


def validate_proposal(proposed, sources):
    """Pure data guards, also usable without contacting the cluster."""
    require(set(proposed) == set(OBJECT_NAMES), 'five distinct reviewed objects required')
    for role, obj in proposed.items():
        require(obj['kind'] == object_kind(role), 'proposal kind changed')
        require(obj['metadata'] == {'name': OBJECT_NAMES[role],
                                    'labels': {'ani.vm.fixture': TASK}}, 'proposal metadata changed')
        no_default(obj)
    for role in CLASS_ROLES:
        sc = proposed[role]
        for key in ('provisioner', 'parameters', 'reclaimPolicy', 'allowVolumeExpansion', 'mountOptions', 'allowedTopologies'):
            require(sc.get(key) == sources['sc'].get(key), 'SC proposal does not exactly clone current source: ' + role + '/' + key)
        require(sc.get('volumeBindingMode') == ('Immediate' if role == 'StorageClass' else 'WaitForFirstConsumer'), 'SC binding mode changed')
    for role in PROFILE_ROLES:
        claims = [{'accessModes': ['ReadWriteOnce'], 'volumeMode': 'Block'}]
        if role == 'StorageProfile':
            claims.append({'accessModes': ['ReadWriteOnce'], 'volumeMode': 'Filesystem'})
        require(proposed[role].get('spec') == {'snapshotClass': VSC, 'claimPropertySets': claims}, 'Profile selection/modes changed')
    vsc = proposed['VolumeSnapshotClass']
    require(vsc['driver'] == sources['retain']['driver'] and vsc['parameters'] == sources['retain']['parameters'] and vsc['deletionPolicy'] == 'Delete', 'VSC proposal differs from reviewed Delete clone')


class Driver:
    def __init__(self, args):
        self.args = args
        self.backup = json.loads((OUT / 'backup.json').read_text())
        self.state = json.loads((OUT / 'state.json').read_text())
        require(self.state['task'] == TASK and self.state['backup_sha256'] == digest(self.backup), 'backup/source registry changed')
        require(self.backup['cluster_uid'] == CLUSTER_UID and self.backup['proposal_file_sha256'] == PROPOSAL_SHA, 'wrong task source registry')

    def save(self):
        self.state['updated_at'] = now()
        atomic_json(OUT / 'state.json', self.state)

    def entry(self, key, operation, before, after):
        entries = [x for x in self.state['journal'] if x['key'] == key]
        if entries:
            item = entries[-1]
            require(item['before'] == before and item['after'] == after, 'journal intent changed: ' + key)
            return item, False
        item = {'id': str(uuid.uuid4()), 'key': key, 'operation': operation,
                'before': copy.deepcopy(before), 'after': copy.deepcopy(after),
                'phase': 'prepared', 'at': now(), 'attempts': 0}
        self.state['journal'].append(item)
        self.save()
        return item, True

    def mutate(self, item, fresh, args, body):
        if not fresh:
            require(self.args.retry_unknown == item['id'], 'observe unresolved write; explicit retry required: ' + item['id'])
        item['attempts'] += 1
        item['approval_reference'] = self.args.approval_reference
        item['phase'] = 'unknown'
        self.save()  # Durable BEFORE the request, including timeout/crash outcomes.
        try:
            raw = command(args, body)
        except BaseException as error:
            item['last_error'] = str(error)[-1800:]
            self.save()
            raise
        return json.loads(raw) if raw.strip() else None

    def mark(self, item, obj, phase='observed'):
        item['phase'] = phase
        item['observed_at'] = now()
        if obj is not None:
            item['observed'] = identity(obj)
        self.save()

    def create(self, kind):
        desired = copy.deepcopy(self.backup['proposed'][kind])
        key = 'create:' + kind
        item, fresh = self.entry(key, 'create', None, desired)
        annotations = desired['metadata'].setdefault('annotations', {})
        annotations.update({INTENT: item['id'], SOURCE: self.state['backup_sha256']})
        resource = resource_for(kind)[0]
        current = get(resource, desired['metadata']['name'], optional=True)
        if current is None:
            require(kind not in self.state['registry'], 'registered task object disappeared; refuse recreation')
            current = self.mutate(item, fresh, ['create', '-f', '-', '-o', 'json'], desired)
        require(current is not None and intent_body(current) == intent_body(desired), 'same-name foreign or drifted object: ' + kind)
        same_uid(current, self.state['registry'].get(kind, {}).get('uid', current['metadata']['uid']), kind)
        no_default(current)
        self.state['registry'][kind] = identity(current) | {'origin': 'task-create', 'creation_intent': item['id']}
        self.mark(item, current)
        return current

    def recover_registry(self):
        """GET-only recovery, including a lost create response; never creates."""
        for kind in CREATE_ROLES:
            if kind in self.state['registry']:
                continue
            entries = [x for x in self.state['journal'] if x['key'] == 'create:' + kind and x['attempts'] > 0]
            if not entries:
                continue
            item = entries[-1]
            desired = copy.deepcopy(self.backup['proposed'][kind])
            desired['metadata'].setdefault('annotations', {}).update({
                INTENT: item['id'], SOURCE: self.state['backup_sha256']})
            current = get(resource_for(kind)[0], desired['metadata']['name'], optional=True)
            if current is not None:
                require(intent_body(current) == intent_body(desired), 'unknown write found foreign/drifted object: ' + kind)
                self.state['registry'][kind] = identity(current) | {'origin': 'task-create', 'creation_intent': item['id']}
                self.mark(item, current, 'observed-after-unknown-response')
        for class_role, profile_role in zip(CLASS_ROLES, PROFILE_ROLES):
            if class_role not in self.state['registry'] or profile_role in self.state['registry']:
                continue
            class_name = OBJECT_NAMES[class_role]
            sc = get('storageclasses', class_name, optional=True)
            profile = get('storageprofiles.cdi.kubevirt.io', class_name, optional=True)
            if sc is not None and profile is not None:
                same_uid(sc, self.state['registry'][class_role]['uid'], 'task SC')
                native_profile_owner(profile, sc)
                require(profile.get('spec', {}) == {}, 'unregistered generated Profile has foreign desired fields')
                self.state['registry'][profile_role] = identity(profile) | {
                    'origin': 'native-CDI-after-task-SC', 'source_sc_uid': sc['metadata']['uid'],
                    'owner_uid': CDI_UID, 'original_spec': {}}
                self.save()

    def patch_field(self, key, resource, name, namespace, expected_uid, path, before, after):
        """Preserve every unowned field; test UID and the just-read RV atomically."""
        item, fresh = self.entry(key, 'patch', before, after)
        current = get(resource, name, namespace)
        same_uid(current, expected_uid, key)
        parts = path.strip('/').split('/')
        parent = current
        for part in parts[:-1]:
            require(part in parent, 'patch parent absent: ' + path)
            parent = parent[part]
        value = {'present': parts[-1] in parent, 'value': parent.get(parts[-1])}
        if value == after:
            self.mark(item, current, 'observed-converged')
            return current
        require(value == before, 'owned field drifted: ' + key)
        patch = [{'op': 'test', 'path': '/metadata/uid', 'value': expected_uid},
                 {'op': 'test', 'path': '/metadata/resourceVersion', 'value': current['metadata']['resourceVersion']}]
        if after['present']:
            patch.append({'op': 'add', 'path': path, 'value': after['value']})
        else:
            patch.append({'op': 'remove', 'path': path})
        args = ['patch', resource, name, '--type=json', '-p', json.dumps(patch), '-o', 'json']
        if namespace:
            args += ['-n', namespace]
        result = self.mutate(item, fresh, args, None)
        self.mark(item, result)
        return result

    def validate_sources(self):
        current = source_objects()
        for key in ('sc', 'retain', 'cdi'):
            require(intent_body(current[key]) == intent_body(self.backup['sources'][key]), 'source specification changed: ' + key)
        old_config = copy.deepcopy(self.backup['sources']['kv']['spec'].get('configuration', {}))
        config = copy.deepcopy(current['kv']['spec'].get('configuration', {}))
        if config.get('vmStateStorageClass') == SC and any(x['key'] == 'kv:enable' for x in self.state['journal']):
            config.pop('vmStateStorageClass')
        require(config == old_config, 'unowned KubeVirt configuration drift')
        return current

    def original_pin(self):
        before_spec = self.backup['sources']['profile'].get('spec', {})
        current = get('storageprofiles.cdi.kubevirt.io', 'ani-block')
        spec = copy.deepcopy(current.get('spec', {}))
        spec.pop('snapshotClass', None)
        require(spec == before_spec, 'original StorageProfile has unreviewed spec changes')
        self.patch_field('profile:pin', 'storageprofiles.cdi.kubevirt.io', 'ani-block', '', PROFILE_UID,
                         '/spec/snapshotClass', {'present': False, 'value': None},
                         {'present': True, 'value': 'ani-rbd-retain'})

    def generated_profile(self, class_role, profile_role):
        require(class_role in self.state['registry'], 'task SC not registered')
        expected_sc = self.state['registry'][class_role]
        class_name = OBJECT_NAMES[class_role]
        require(class_name == OBJECT_NAMES[profile_role], 'task SC/Profile source mismatch')
        sc = get('storageclasses', class_name)
        same_uid(sc, expected_sc['uid'], 'task SC')
        profile = None
        for _ in range(15):
            profile = get('storageprofiles.cdi.kubevirt.io', class_name, optional=True)
            if profile:
                break
            time.sleep(1)
        require(profile is not None, 'CDI has not generated task StorageProfile; observe and resume')
        native_profile_owner(profile, sc)
        registered = self.state['registry'].get(profile_role)
        if registered:
            same_uid(profile, registered['uid'], 'task Profile')
            require(registered['source_sc_uid'] == sc['metadata']['uid'], 'task Profile source registry drift')
        else:
            require(profile.get('spec', {}) == {}, 'new generated Profile already has foreign desired fields')
            self.state['registry'][profile_role] = identity(profile) | {
                'origin': 'native-CDI-after-task-SC', 'source_sc_uid': sc['metadata']['uid'],
                'owner_uid': CDI_UID, 'original_spec': {}}
            self.save()
        self.patch_field('profile:task-spec:' + class_name, 'storageprofiles.cdi.kubevirt.io', class_name, '', profile['metadata']['uid'],
                         '/spec', {'present': True, 'value': {}},
                         {'present': True, 'value': self.backup['proposed'][profile_role]['spec']})
        self.patch_field('profile:task-label:' + class_name, 'storageprofiles.cdi.kubevirt.io', class_name, '', profile['metadata']['uid'],
                         '/metadata/labels/ani.vm.fixture', {'present': False, 'value': None},
                         {'present': True, 'value': TASK})

    def converged(self):
        original = get('storageprofiles.cdi.kubevirt.io', 'ani-block')
        same_uid(original, PROFILE_UID, 'original Profile')
        require(original.get('spec', {}).get('snapshotClass') == 'ani-rbd-retain', 'original Profile desired selection drifted')
        if original.get('status', {}).get('snapshotClass') != 'ani-rbd-retain':
            raise NotConverged('original Profile Retain not converged')
        for role in PROFILE_ROLES:
            profile = get('storageprofiles.cdi.kubevirt.io', OBJECT_NAMES[role])
            same_uid(profile, self.state['registry'][role]['uid'], 'task Profile')
            expected = self.backup['proposed'][role]['spec']
            require(profile.get('spec') == expected, 'task Profile desired fields drifted')
            if (profile.get('status', {}).get('snapshotClass') != VSC
                    or profile.get('status', {}).get('claimPropertySets') != expected['claimPropertySets']):
                raise NotConverged('task Profile Delete/modes not converged: ' + role)
            require(profile['metadata'].get('labels', {}).get('ani.vm.fixture') == TASK, 'task Profile label not registered')
        for kind in CREATE_ROLES:
            obj = get(resource_for(kind)[0], OBJECT_NAMES[kind])
            same_uid(obj, self.state['registry'][kind]['uid'], kind)
            no_default(obj)
        rbd_choices()

    def apply(self):
        self.recover_registry()
        self.validate_sources()
        rbd_choices(pin_original=True)
        self.original_pin()
        self.create('StorageClass')
        self.generated_profile('StorageClass', 'StorageProfile')
        # Recheck all other RBD selections immediately before introducing class 2.
        rbd_choices()
        self.create('VolumeSnapshotClass')
        self.create('WFFCStorageClass')
        self.generated_profile('WFFCStorageClass', 'WFFCStorageProfile')
        for attempt in range(10):
            try:
                self.converged()
                break
            except NotConverged:
                if attempt == 9:
                    raise
                time.sleep(1)
        self.state['apply_result'] = 'CONVERGED'
        self.save()

    def vm_state(self, enable):
        key = 'kv:enable' if enable else 'kv:restore'
        if enable:
            self.validate_sources()
            self.converged()
            before, after = {'present': False, 'value': None}, {'present': True, 'value': SC}
        else:
            require(any(x['key'] == 'kv:enable' and x['attempts'] > 0 for x in self.state['journal']), 'no task vmStateStorageClass write to restore')
            before, after = {'present': True, 'value': SC}, {'present': False, 'value': None}
        self.patch_field(key, 'kubevirts.kubevirt.io', 'kubevirt', 'kubevirt', KV_UID,
                         '/spec/configuration/vmStateStorageClass', before, after)

    def references(self):
        refs = []
        for resource, match in (
            ('persistentvolumeclaims', lambda x: x.get('spec', {}).get('storageClassName') in TASK_CLASSES),
            ('datavolumes.cdi.kubevirt.io', lambda x: any(x.get('spec', {}).get(k, {}).get('storageClassName') in TASK_CLASSES for k in ('storage', 'pvc'))),
            ('volumesnapshots.snapshot.storage.k8s.io', lambda x: x.get('spec', {}).get('volumeSnapshotClassName') == VSC),
            ('virtualmachines.kubevirt.io', lambda x: any(any(y.get('spec', {}).get(k, {}).get('storageClassName') in TASK_CLASSES for k in ('storage', 'pvc')) for y in x.get('spec', {}).get('dataVolumeTemplates', []))),
            ('virtualmachinesnapshotcontents.snapshot.kubevirt.io', lambda x: any(y.get('persistentVolumeClaim', {}).get('spec', {}).get('storageClassName') in TASK_CLASSES for y in x.get('spec', {}).get('volumeBackups', []))),
        ):
            refs += [identity(x) for x in listing(resource, True) if match(x)]
        refs += [identity(x) for x in listing('persistentvolumes') if x.get('spec', {}).get('storageClassName') in TASK_CLASSES]
        refs += [identity(x) for x in listing('volumesnapshotcontents.snapshot.storage.k8s.io') if x.get('spec', {}).get('volumeSnapshotClassName') == VSC]
        return refs

    def no_references(self):
        kv = get('kubevirts.kubevirt.io', 'kubevirt', 'kubevirt')
        same_uid(kv, KV_UID, 'KubeVirt')
        require(kv['spec'].get('configuration', {}).get('vmStateStorageClass') not in TASK_CLASSES,
                'restore vmStateStorageClass through its separate explicit action first')
        refs = self.references()
        self.state['last_reference_observation'] = {'at': now(), 'references': refs}
        self.save()
        require(not refs, 'task class still referenced; no deletion: ' + json.dumps(refs))

    def delete(self, kind):
        registered = self.state['registry'].get(kind)
        if registered is None:
            require(get(resource_for(kind)[0], OBJECT_NAMES[kind], optional=True) is None,
                    'unregistered same-name object; refuse adoption/deletion: ' + kind)
            return
        current = get(resource_for(kind)[0], registered['name'], optional=True)
        item, fresh = self.entry('delete:' + kind, 'delete', {'uid': registered['uid']}, None)
        if current is None:
            self.mark(item, None, 'absent')
            return
        require(current['metadata']['uid'] == registered['uid'], 'task object replaced; refuse deletion: ' + kind)
        if kind in PROFILE_ROLES:
            expected_owner = [{'apiVersion': 'cdi.kubevirt.io/v1beta1', 'kind': 'CDI', 'name': 'cdi', 'uid': CDI_UID}]
            owners = [{k: owner.get(k) for k in ('apiVersion', 'kind', 'name', 'uid')}
                      for owner in current['metadata'].get('ownerReferences', [])]
            require(owners == expected_owner and current.get('spec', {}) in
                    ({}, self.backup['proposed'][kind]['spec']), 'task Profile owner/spec drift before cleanup')
        else:
            desired = copy.deepcopy(self.backup['proposed'][kind])
            desired['metadata'].setdefault('annotations', {}).update({
                INTENT: registered['creation_intent'], SOURCE: self.state['backup_sha256']})
            require(intent_body(current) == intent_body(desired), 'task class changed outside this driver; refuse deletion')
        self.no_references()
        if not current['metadata'].get('deletionTimestamp'):
            # Upstream kubectl v0.35 delete --raw -f - sends this DeleteOptions
            # body. Ordinary kubectl delete does not make UID/RV preconditions.
            options = {'apiVersion': 'v1', 'kind': 'DeleteOptions', 'preconditions': {
                'uid': registered['uid'], 'resourceVersion': current['metadata']['resourceVersion']}}
            self.mutate(item, fresh, ['delete', '--raw=' + resource_for(kind)[1] + registered['name'], '-f', '-'], options)
        else:
            self.mark(item, current, 'terminating')
        for _ in range(10):
            current = get(resource_for(kind)[0], registered['name'], optional=True)
            if current is None:
                self.mark(item, None, 'absent')
                return
            require(current['metadata']['uid'] == registered['uid'], 'same-name replacement during deletion')
            time.sleep(1)
        raise RuntimeError('normal deletion still pending; retain registry, never force finalizers')

    def restore(self):
        self.recover_registry()
        self.no_references()
        # Removing the SC first lets CDI retire its generated profile normally.
        for role in RESTORE_ORDER:
            self.delete(role)
        require(get('volumesnapshotclasses.snapshot.storage.k8s.io', VSC, optional=True) is None, 'task VSC not absent')
        if any(x['key'] == 'profile:pin' and x['attempts'] > 0 for x in self.state['journal']):
            self.patch_field('profile:restore', 'storageprofiles.cdi.kubevirt.io', 'ani-block', '', PROFILE_UID,
                             '/spec/snapshotClass', {'present': True, 'value': 'ani-rbd-retain'},
                             {'present': False, 'value': None})
        self.state['restore_result'] = 'OWNED_OBJECTS_ABSENT_AND_ORIGINAL_FIELD_RESTORED'
        self.save()

    def status(self):
        self.recover_registry()
        objects = {}
        for kind in OBJECT_NAMES:
            obj = get(resource_for(kind)[0], OBJECT_NAMES[kind], optional=True)
            objects[kind] = None if obj is None else {'identity': identity(obj),
                'registered_uid_match': obj['metadata']['uid'] == self.state['registry'].get(kind, {}).get('uid'),
                'terminating': bool(obj['metadata'].get('deletionTimestamp'))}
        return {'task': TASK, 'at': now(), 'mutation_performed': False, 'objects': objects,
                'task_references': self.references(), 'journal': self.state['journal'],
                'vmStateStorageClass': get('kubevirts.kubevirt.io', 'kubevirt', 'kubevirt')['spec'].get('configuration', {}).get('vmStateStorageClass')}


def preflight(args):
    if (OUT / 'backup.json').exists() and not (OUT / 'state.json').exists():
        # No cluster writes can have run without state.json. Recover only the
        # interrupted local backup publication; never replace backup bytes.
        backup = json.loads((OUT / 'backup.json').read_text())
        require(backup['task'] == TASK and backup['cluster_uid'] == CLUSTER_UID
                and backup['proposal_file_sha256'] == PROPOSAL_SHA, 'foreign/incomplete backup')
        atomic_json(OUT / 'state.json', {'task': TASK, 'at': now(), 'backup_sha256': digest(backup),
                    'registry': {}, 'journal': []}, exclusive=True)
        return {'task': TASK, 'action': 'preflight', 'cluster_mutation_performed': False,
                'result': 'INTERRUPTED_LOCAL_STATE_PUBLICATION_RECOVERED'}
    require(not (OUT / 'backup.json').exists() and not (OUT / 'state.json').exists(),
            'existing backup/registry must never be replaced; use status or resume')
    sources = source_objects()
    require('snapshotClass' not in sources['profile'].get('spec', {}), 'original snapshotClass no longer absent')
    require('vmStateStorageClass' not in sources['kv']['spec'].get('configuration', {}), 'vmStateStorageClass no longer absent')
    for role, name in OBJECT_NAMES.items():
        require(get(resource_for(role)[0], name, optional=True) is None, 'task name already occupied: ' + role)
    proposed = load_proposal(args.objects, sources)
    choices = rbd_choices(pin_original=True)
    backup = {'task': TASK, 'at': now(), 'cluster_uid': CLUSTER_UID, 'nodes': NODES,
              'proposal_file_sha256': PROPOSAL_SHA, 'source_path_reference': str(Path(args.objects).resolve()),
              'sources': sources, 'rbd_choices': choices, 'proposed': proposed,
              'vm_state_before': {'present': False, 'value': None},
              'profile_snapshot_class_before': {'present': False, 'value': None}}
    atomic_json(OUT / 'backup.json', backup, exclusive=True)
    state = {'task': TASK, 'at': now(), 'backup_sha256': digest(backup), 'registry': {}, 'journal': []}
    atomic_json(OUT / 'state.json', state, exclusive=True)
    return {'task': TASK, 'action': 'preflight', 'cluster_mutation_performed': False,
            'backup': str(OUT / 'backup.json'), 'backup_sha256': state['backup_sha256'], 'rbd_choices': choices}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('action', nargs='?', default='preflight', choices=(
        'preflight', 'status', 'apply', 'vm-state-enable', 'vm-state-restore', 'restore'))
    parser.add_argument('--objects', type=Path, default=Path(__file__).resolve().parents[1] /
                        'docs/execution/records/VM-MVP/snapshot-environment-proposed-objects.json')
    parser.add_argument('--execute-approved', action='store_true')
    parser.add_argument('--approval-reference')
    parser.add_argument('--retry-unknown', help='exact journal id; never retries a different unknown write')
    args = parser.parse_args()
    mutating = args.action not in ('preflight', 'status')
    require(not mutating or (args.execute_approved and args.approval_reference),
            'mutation requires explicit --execute-approved and --approval-reference')
    require(mutating or not args.execute_approved, 'read-only action does not accept an execution approval flag')
    # A script invocation cannot infer that the human has approved any action.
    # The caller must supply the actual approval reference after it exists.
    verify_scope(ready=args.action in ('preflight', 'apply', 'vm-state-enable'))
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / 'driver.lock').open('a') as local_lock:
        try:
            fcntl.flock(local_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('another snapshot environment driver is running')
        heavy = None
        if mutating:
            heavy = LOCK.open('a')
            deadline = time.monotonic() + 10
            while True:
                try:
                    fcntl.flock(heavy, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        print('heavy lock busy; no mutation', file=sys.stderr)
                        return 73
                    time.sleep(.2)
        try:
            if args.action == 'preflight':
                result = preflight(args)
            else:
                driver = Driver(args)
                if args.action == 'status':
                    result = driver.status()
                elif args.action == 'apply':
                    driver.apply()
                    result = {'task': TASK, 'action': args.action, 'result': driver.state['apply_result'],
                              'vmStateStorageClass_changed': False}
                elif args.action.startswith('vm-state-'):
                    driver.vm_state(args.action == 'vm-state-enable')
                    result = {'task': TASK, 'action': args.action, 'result': 'OWNED_FIELD_CONVERGED'}
                else:
                    driver.restore()
                    result = {'task': TASK, 'action': args.action, 'result': driver.state['restore_result']}
            print(json.dumps(result, indent=2))
        finally:
            if heavy is not None:
                heavy.close()
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (RuntimeError, OSError, subprocess.SubprocessError, KeyError, ValueError) as error:
        print('STOP: ' + str(error), file=sys.stderr)
        sys.exit(1)
