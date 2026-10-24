#!/usr/bin/env python3
"""Five-object VM task network driver, run only through ssh fedora.

Default preflight is read-only against Kubernetes. It freezes a private atomic
source/UID registry. apply/cleanup require the exact approved plan SHA and a
human approval reference. A request is journaled before sending; an unknown
CREATE is never sent again. observe only GETs objects with a recorded original
UID. A lost ACK without an original UID remains unresolved, never adopted by
name/labels/nonce alone.

No KCN configuration, shared object, SQL, OVN, finalizer, workload or source
volume mutation is implemented. Native readiness is not Guest acceptance.
"""

import argparse
import copy
import datetime
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid


RUN = Path('/home/chabking/workspace/ani-resource-service-runs/vm-mvp-20261023-01a168ce')
PRIVATE = RUN / 'private/network-environment'
PUBLIC = RUN / 'evidence/network-environment.json'
LOCK = Path('/home/chabking/workspace/ani-network-service-runs/net05a-heavy.lock')
K = ['/home/chabking/ani-four-service-deploy-20261012/tools/kubectl',
     '--kubeconfig=/home/chabking/ani-four-service-deploy-20261012/private/kubeconfig',
     '--request-timeout=10s']
PLAN_SHA = '00d2328cf6f9172472ebc69a79ddf69f4c6bc4601874abee143592780945c175'
TASK = '01a168ce'
GROUP = 'networking.kubercloud.com'
CLUSTER_UID = '5277649d-e28d-4a0a-ae76-8354365c63bc'
NS1 = 'tenant-b3c946b5-7eb3-4feb-9f82-79712062ca5c'
NS2 = 'tenant-d87c4dbd-04fa-4066-9ef6-c6fa4217f316'
SUBNET = 'vm-mvp-01a168ce-intranet'
POOL = ipaddress.ip_network('10.232.254.240/29')
INTENT = 'vm-mvp-test.ani.io/creation-intent'
SOURCE = 'vm-mvp-test.ani.io/source-sha256'
ORDER = ('subnet', 'eip1', 'eip2', 'snat1', 'snat2')
DELETE_ORDER = ('snat1', 'snat2', 'eip1', 'eip2', 'subnet')
TENANTS = {
    NS1: {'uid': '09e58ecb-fe20-454f-bc7b-2da3c406f0ac',
          'vpc': 'vpc-e413becb1f4a46468f058e32f33a6305',
          'vpc_uid': 'f7ae3b4e-2b90-4fec-bb90-67673780a83a', 'vpc_cidr': '10.83.0.0/16',
          'subnet': 'subnet-fbd064f6018040dab6a8d909b7afb87f',
          'subnet_uid': 'c24a983c-f9f4-403d-9807-8164004000a3', 'cidr': '10.83.1.0/24',
          'vm': 'vm-55099440-c47b-4f81-afc5-7d8e740d14dc',
          'vm_uid': '7bca4671-89dd-4925-bad9-17e77401729d'},
    NS2: {'uid': 'fa138fd5-1f35-4fa2-b561-db3d136b2404',
          'vpc': 'vpc-c44c262a220748aab0e4b20558849409',
          'vpc_uid': '2f2e25c2-55e1-49bf-b284-4d7504b32f2b', 'vpc_cidr': '10.84.0.0/16',
          'subnet': 'subnet-ea6b62fc625349bcad46ce56ce60f90d',
          'subnet_uid': '29cdddaf-c09b-49df-9323-dd776d8ec929', 'cidr': '10.84.1.0/24',
          'vm': 'vm-e3c11c5a-cf12-4630-a8f9-f907048ffdfc',
          'vm_uid': 'fde3b420-6bde-4169-9eb1-ed025c60a1a6'},
}
NODES = {'ani-01': ('172.16.101.10', '201e41ac-c50d-4c77-b118-72d1ed93e111'),
         'ani-02': ('172.16.101.11', '73564fa7-98ad-45d4-9576-be59f6d5d245'),
         'ani-03': ('172.16.101.12', 'af992ed9-cbe1-4f64-8fac-7433808bfab2')}
NADS = ((NS1, 'vm-mvp-main', 'ac77bddd-82a3-48b9-9916-fbef18f35610',
         '0bcb0f9ea70ebbe2f3381fc9fc563b74698467a4138f6567d37108c653cb381e'),
        (NS1, 'vm-mvp-main-v2', '8f366897-8905-4f2a-9d6b-4ace4e18a41f',
         'bb5bb6d1ad273609354486dfa052bf46236c47583fdb6a228bab4b75fb902d30'),
        (NS2, 'vm-mvp-tenant2-01a168ce', 'c744fbb9-fdff-46b9-8ba9-0c75cfb8e9dc',
         'bb5bb6d1ad273609354486dfa052bf46236c47583fdb6a228bab4b75fb902d30'))


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def atomic(path, value, exclusive=False):
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    data = (json.dumps(value, indent=2, sort_keys=True) + '\n').encode()
    tmp = path if exclusive else path.with_name(path.name + '.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else os.O_TRUNC), 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    if not exclusive:
        os.replace(tmp, path)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def identity(obj):
    m = obj['metadata']
    return {'apiVersion': obj['apiVersion'], 'kind': obj['kind'], 'name': m['name'],
            'namespace': m.get('namespace', ''), 'uid': m['uid'],
            'resourceVersion': m['resourceVersion']}


def same_uid(obj, expected, terminating=False):
    require(obj is not None and obj['metadata']['uid'] == expected, 'absent/replaced protected or registered UID')
    require(terminating or not obj['metadata'].get('deletionTimestamp'), 'protected object terminating')


def objects():
    def obj(kind, ns, name, spec):
        return {'apiVersion': GROUP + '/v1', 'kind': kind,
                'metadata': {'namespace': ns, 'name': name, 'labels': {'vm-mvp-test.ani.io/run': TASK}},
                'spec': spec}
    result = {'subnet': obj('Subnet', 'kcn-system', SUBNET, {
        'type': 'Intranet', 'ipVersion': 'IPv4', 'cidrBlock': str(POOL),
        'gatewayIP': '10.232.254.241', 'gateway': 'kcn-system/kcn-cluster',
        'excludeIPs': ['10.232.254.241'], 'allowedNamespaces': {'from': 'Selector',
            'selector': {'matchExpressions': [{'key': 'kubernetes.io/metadata.name',
                'operator': 'In', 'values': [NS1, NS2]}]}}, 'enableDHCP': False, 'natOutgoing': False})}
    for i, ns in enumerate((NS1, NS2), 1):
        stem = 'vm-mvp-01a168ce-' + ('tenant2-' if i == 2 else '') + 'intranet'
        result['eip' + str(i)] = obj('EIP', ns, stem + '-eip',
            {'subnet': 'kcn-system/' + SUBNET, 'ipVersion': 'IPv4'})
        result['snat' + str(i)] = obj('Snat', ns, stem + '-snat',
            {'eip': stem + '-eip', 'vpc': ns + '/' + TENANTS[ns]['vpc'],
             'cidrs': [TENANTS[ns]['cidr']], 'disable': False})
    return result


def resource(kind):
    return {'Subnet': 'subnets', 'EIP': 'eips', 'Snat': 'snats'}[kind] + '.' + GROUP


def normalized_spec(kind, spec):
    value = copy.deepcopy(spec)
    if kind == 'Subnet':
        for key in ('enableDHCP', 'natOutgoing'):
            value.setdefault(key, False)  # Native Go bool,omitempty serializes false as absent.
    if kind == 'EIP':
        value.pop('ipAddress', None)  # Only the controller-assigned field; checked and pinned separately.
    return value


def owned(obj, desired, entry, source_hash, registered=None):
    m = obj['metadata']; annotations = m.get('annotations', {})
    require(obj['apiVersion'] == desired['apiVersion'] and obj['kind'] == desired['kind']
            and m['name'] == desired['metadata']['name']
            and m['namespace'] == desired['metadata']['namespace'], 'native object identity drift')
    require(m.get('labels', {}).get('vm-mvp-test.ani.io/run') == TASK
            and annotations.get(INTENT) == entry['id'] and annotations.get(SOURCE) == source_hash,
            'foreign same-name object or creation intent mismatch')
    require(not m.get('ownerReferences'), 'unexpected owner on task-created native object')
    require(not any(k.startswith('network.ani.io/') for k in m.get('labels', {})), 'must not impersonate managed Network object')
    require(normalized_spec(obj['kind'], obj['spec']) == normalized_spec(desired['kind'], desired['spec']), 'task native spec drift')
    if registered:
        same_uid(obj, registered['uid'], terminating=True)
    if obj['kind'] == 'EIP':
        address = obj['spec'].get('ipAddress')
        if address:
            require(ipaddress.ip_address(address) in POOL
                    and str(address) not in ('10.232.254.240', '10.232.254.241', '10.232.254.247'),
                    'EIP controller allocation outside reviewed usable range')
        if registered and registered.get('allocated_ip'):
            require(address == registered['allocated_ip'], 'registered EIP allocation drift')


def conditions(obj, *required):
    values = {c['type']: c['status'] for c in obj.get('status', {}).get('conditions', [])}
    return all(values.get(k) == 'True' for k in required) and values.get('Ready') != 'False'


def subnet_ready(obj):
    return (conditions(obj, 'Valid', 'Initialized', 'Ready')
            and obj.get('status', {}).get('observedGeneration') == obj['metadata']['generation'])


def eip_ready(obj):
    return (conditions(obj, 'Valid', 'Initialized') and bool(obj['spec'].get('ipAddress'))
            and obj.get('status', {}).get('phase') in ('Available', 'Bound')
            and obj.get('status', {}).get('gateway') == 'kcn-system/kcn-cluster')


def snat_ready(snat, eip):
    bound = eip.get('status', {}).get('boundResource') or {}
    return (conditions(snat, 'Valid') and snat.get('status', {}).get('phase') == 'Bound'
            and eip_ready(eip) and eip['status'].get('phase') == 'Bound'
            and bound.get('resourceType') == 'Snat' and bound.get('resource') == snat['metadata']['name']
            and bound.get('vpc') == snat['spec']['vpc']
            and bound.get('observedGeneration') == snat['metadata']['generation']
            and bound.get('disabled') is False and bound.get('nodeName') in NODES
            and snat.get('status', {}).get('eipAddress') == eip['spec']['ipAddress'])


def parse_networks(raw):
    try:
        values = json.loads(raw)
    except ValueError:
        if raw.strip().startswith('[') and raw.strip().endswith(']'):
            values = [v.strip().strip('\"\'') for v in raw.strip()[1:-1].split(',')]
        else:
            lines = [v.strip() for v in raw.splitlines() if v.strip() and not v.lstrip().startswith('#')]
            require(all(v.startswith('- ') for v in lines), 'unsupported intranetNetworks representation')
            values = [v[2:].strip().strip('\"\'') for v in lines]
    require(isinstance(values, list) and values, 'intranetNetworks must be a nonempty CIDR list')
    networks = [ipaddress.ip_network(v, strict=True) for v in values]
    require(all(v.version == 4 and v.prefixlen > 0 for v in networks), 'invalid intranetNetworks scope')
    for cidr in ('172.16.101.0/24', '10.96.0.0/16', '10.16.0.0/16'):
        require(any(ipaddress.ip_network(cidr).subnet_of(v) for v in networks), 'required destination no longer covered')
    return networks


def no_conflicts(inventory, registered):
    found = []
    for obj in inventory:
        own = registered.get('subnet')
        if own and obj['kind'] == 'Subnet' and obj['metadata']['uid'] == own['uid']:
            continue
        spec = obj.get('spec', {})
        values = [spec[k] for k in ('cidrBlock', 'cidrBlockV6', 'podCIDR') if spec.get(k)]
        values += spec.get('cidrs', []) + spec.get('podCIDRs', [])
        for value in set(values):
            net = ipaddress.ip_network(value, strict=True)
            if net.version == 4 and net.overlaps(POOL):
                found.append({'identity': identity(obj), 'cidr': value})
    require(not found, 'candidate CIDR overlaps existing allocation: ' + json.dumps(found))


class Kube:
    def call(self, args, body=None):
        result = subprocess.run(K + args, input=None if body is None else json.dumps(body),
                                capture_output=True, text=True, timeout=15)
        require(result.returncode == 0, 'kubectl failed: ' + result.stderr[-1200:])
        return json.loads(result.stdout) if result.stdout.strip() else None

    def get(self, kind, ns, name):
        args = ['get', kind, name, '--ignore-not-found=true', '-o', 'json']
        return self.call(args + (['-n', ns] if ns else []))

    def listing(self, kind, namespaced=True):
        return self.call(['get', kind, '-o', 'json'] + (['-A'] if namespaced else []))['items']

    def create(self, body):
        return self.call(['create', '-f', '-', '-o', 'json'], body)

    def delete(self, obj):
        m = obj['metadata']; plural = resource(obj['kind']).split('.', 1)[0]
        path = '/apis/' + GROUP + '/v1/namespaces/' + m['namespace'] + '/' + plural + '/' + m['name']
        options = {'apiVersion': 'v1', 'kind': 'DeleteOptions', 'propagationPolicy': 'Background',
                   'preconditions': {'uid': m['uid'], 'resourceVersion': m['resourceVersion']}}
        return self.call(['delete', '--raw=' + path, '-f', '-'], options)


def scope(kube):
    connection = os.environ.get('SSH_CONNECTION', '').split()
    require(len(connection) == 4 and connection[2] == '172.16.101.31', 'only ssh fedora may execute')
    server = subprocess.run(K + ['config', 'view', '--minify', '-o', 'jsonpath={.clusters[0].cluster.server}'],
                            capture_output=True, text=True, timeout=10)
    require(server.returncode == 0 and server.stdout == 'https://172.16.101.10:6443', 'unapproved API endpoint')
    same_uid(kube.get('namespace', '', 'kube-system'), CLUSTER_UID)
    nodes = kube.listing('nodes', False)
    require({n['metadata']['name'] for n in nodes} == set(NODES), 'cluster node set changed')
    for node in nodes:
        ip, uid = NODES[node['metadata']['name']]; same_uid(node, uid)
        require(any(a['type'] == 'InternalIP' and a['address'] == ip for a in node['status']['addresses']), 'node IP drift')
    return nodes


def sources(kube):
    result = {}
    for ns, expected in TENANTS.items():
        namespace = kube.get('namespace', '', ns); same_uid(namespace, expected['uid'])
        labels = namespace['metadata'].get('labels', {})
        require(labels.get('kubernetes.io/metadata.name') == ns
                and labels.get('network.ani.io/tenant-id') == ns.removeprefix('tenant-'), 'tenant namespace label drift')
        result[ns] = {'namespace': identity(namespace)}
        for kind, uid_key, name_key, cidr in (('VPC', 'vpc_uid', 'vpc', expected['vpc_cidr']),
                                             ('Subnet', 'subnet_uid', 'subnet', expected['cidr'])):
            obj = kube.get(kind.lower() + 's.' + GROUP, ns, expected[name_key]); same_uid(obj, expected[uid_key])
            spec = {'allowedNamespaces': {'from': 'Same'}, 'ipVersion': 'IPv4', 'cidrBlock': cidr}
            if kind == 'Subnet':
                spec.update(type='VPC', gateway=ns + '/' + expected['vpc'],
                            gatewayIP=str(ipaddress.ip_network(cidr).network_address + 1))
            require(obj['spec'] == spec and subnet_ready(obj), 'task VPC/Subnet spec or generation/readiness drift')
            result[ns][kind] = {'identity': identity(obj), 'spec': obj['spec']}
    default = kube.get('vpcs.' + GROUP, 'kcn-system', 'kcn-cluster')
    same_uid(default, 'd0235860-f51a-4e63-8f4f-1ca1b34bc04f')
    require(default['spec'] == {'allowedNamespaces': {'from': 'All'}, 'ipVersion': 'IPv4'}
            and subnet_ready(default) and default['status'].get('default') is True
            and default['status'].get('boundResources', {}).get('router') == 'vpc.kcn-system.kcn-cluster',
            'default VPC spec/router/readiness drift')
    result['default'] = {'identity': identity(default), 'spec': default['spec']}
    config = kube.get('configmap', 'kcn-system', 'kcn-config')
    same_uid(config, 'b4be2ccc-1f81-466b-8a66-84f7a2c99115')
    networks = parse_networks(config.get('data', {}).get('intranetNetworks', ''))
    require(not any(POOL.overlaps(n) for n in networks), 'pool overlaps an existing intranet destination')
    result['config'] = {'identity': identity(config), 'data': config.get('data', {}), 'binaryData': config.get('binaryData', {})}
    service = kube.get('servicecidrs.networking.k8s.io', '', 'kubernetes')
    same_uid(service, '2b0fda89-ea60-4588-b989-e4d40e36d7ce')
    require(service['spec']['cidrs'] == ['10.96.0.0/16'], 'ServiceCIDR changed')
    result['servicecidr'] = {'identity': identity(service), 'spec': service['spec']}
    return result


def protections(kube):
    result = {}
    pvc = kube.get('pvc', 'vm-mvp-probe-01a168ce', 'vm-mvp-ubuntu-2404-source')
    same_uid(pvc, '8b235154-fae1-47ed-8f96-06af02a18ce1')
    require(pvc['spec'].get('volumeMode') == 'Block' and pvc['status']['phase'] == 'Bound'
            and pvc['metadata'].get('annotations', {}).get('vm.ani.io/source-sha256')
            == '61f58093344ae5c84e22f1be07a904b0e2a15f7b6f18e45ef4549e52833c69c4', 'protected source content/mode drift')
    result['source'] = {'identity': identity(pvc), 'spec': pvc['spec'], 'source_sha256': pvc['metadata']['annotations']['vm.ani.io/source-sha256']}
    for ns, name, uid, expected_hash in NADS:
        nad = kube.get('network-attachment-definitions.k8s.cni.cncf.io', ns, name); same_uid(nad, uid)
        require(digest(json.loads(nad['spec']['config'])) == expected_hash, 'protected NAD config drift')
        result[ns + '/' + name] = {'identity': identity(nad), 'spec': nad['spec']}
    for ns, item in TENANTS.items():
        vm = kube.get('virtualmachines.kubevirt.io', ns, item['vm']); same_uid(vm, item['vm_uid'])
        result[ns + '/' + item['vm']] = {'identity': identity(vm)}
    return result


def stable(value):
    value = copy.deepcopy(value)
    def visit(obj):
        if isinstance(obj, dict):
            obj.pop('resourceVersion', None)
            for v in obj.values():
                visit(v)
        elif isinstance(obj, list):
            for v in obj:
                visit(v)
    visit(value)
    return value


def authorization(args):
    require(args.execute_approved and args.approved_plan_sha == PLAN_SHA and args.approval_reference,
            'apply/cleanup require explicit human approval reference and exact reviewed plan SHA')


class Driver:
    def __init__(self, kube, directory=PRIVATE, wait_seconds=15):
        self.kube = kube; self.directory = directory; self.wait_seconds = wait_seconds
        self.backup = json.loads((directory / 'backup.json').read_text())
        self.state = json.loads((directory / 'state.json').read_text())
        require(self.backup['plan_sha256'] == PLAN_SHA and self.state['source_sha256'] == digest(self.backup)
                and self.state['task'] == TASK, 'private registry/source backup drift')

    def save(self):
        self.state['updated_at'] = now(); atomic(self.directory / 'state.json', self.state)

    def guard(self):
        require(stable(sources(self.kube)) == stable(self.backup['sources']), 'frozen network source spec/UID drift')
        require(stable(protections(self.kube)) == stable(self.backup['protected']), 'protected source/NAD/VM identity drift')

    def current(self, role):
        desired = self.backup['objects'][role]; meta = desired['metadata']
        obj = self.kube.get(resource(desired['kind']), meta['namespace'], meta['name'])
        registered = self.state['registry'].get(role); entry = self.state['create'].get(role)
        require(entry is None or registered is not None,
                'unknown CREATE has no recorded original UID; retain dependencies even if name is now absent')
        if obj is not None:
            require(entry is not None and entry['attempts'] == 1, 'same-name object was not sent by this registry')
            owned(obj, desired, entry, self.state['source_sha256'], registered)
            address = obj['spec'].get('ipAddress') if obj['kind'] == 'EIP' else None
            if address and not registered.get('allocated_ip'):
                registered['allocated_ip'] = address; self.save()
        return obj

    def create(self, role):
        obj = self.current(role)
        if obj is not None:
            require(not obj['metadata'].get('deletionTimestamp'), 'task object terminating; do not recreate')
            return obj
        require(role not in self.state['registry'], 'registered object disappeared; never recreate')
        require(role not in self.state['create'], 'unknown CREATE is absent; stop, never resend')
        entry = {'id': str(uuid.uuid4()), 'phase': 'unknown', 'attempts': 1, 'at': now()}
        self.state['create'][role] = entry; self.save()  # Persist before the first and only POST.
        body = copy.deepcopy(self.backup['objects'][role])
        body['metadata']['annotations'] = {INTENT: entry['id'], SOURCE: self.state['source_sha256']}
        obj = self.kube.create(body)
        owned(obj, self.backup['objects'][role], entry, self.state['source_sha256'])
        self.state['registry'][role] = identity(obj); entry['phase'] = 'acknowledged'; self.save()
        return self.current(role)

    def wait(self, role, predicate):
        deadline = time.monotonic() + self.wait_seconds
        while True:
            obj = self.current(role)
            if obj is not None and predicate(obj):
                return obj
            if time.monotonic() >= deadline:
                raise RuntimeError('native status pending: ' + role + '; observe, no unknown write retry')
            time.sleep(1)

    def apply(self):
        self.guard()
        inventory = self.kube.listing('subnets.' + GROUP) + self.kube.listing('vpcs.' + GROUP)
        inventory += self.kube.listing('servicecidrs.networking.k8s.io', False) + self.kube.listing('nodes', False)
        no_conflicts(inventory, self.state['registry'])
        # Refuse every foreign name BEFORE the first mutation.
        for role in ORDER:
            self.current(role)
        self.create('subnet'); self.wait('subnet', subnet_ready)
        for role in ('eip1', 'eip2'):
            self.guard(); self.create(role); self.wait(role, eip_ready)
        for role, eip_role in (('snat1', 'eip1'), ('snat2', 'eip2')):
            self.guard(); self.create(role)
            self.wait(role, lambda snat: snat_ready(snat, self.current(eip_role)))
        self.guard()
        return {'result': 'NATIVE_FIVE_OBJECTS_BOUND', 'guest_acceptance': 'NOT_RUN_BY_THIS_DRIVER'}

    def absent(self, role):
        obj = self.current(role)
        require(obj is None, 'child still exists; retain parent: ' + role)

    def deletion_dependencies(self, role, obj):
        if role.startswith('eip'):
            for child in ('snat1', 'snat2'):
                self.absent(child)
            require(obj.get('status', {}).get('phase') == 'Available'
                    and not obj.get('status', {}).get('boundResource'), 'EIP has not unbound; retain it')
        elif role == 'subnet':
            for child in DELETE_ORDER[:-1]:
                self.absent(child)
            refs = []
            for kind in ('eips', 'vnics'):
                for item in self.kube.listing(kind + '.' + GROUP):
                    raw = item.get('spec', {}).get('subnet', '')
                    full = raw if '/' in raw else item['metadata']['namespace'] + '/' + raw
                    if full == 'kcn-system/' + SUBNET:
                        refs.append(identity(item))
            require(not refs and obj.get('status', {}).get('v4usingIPs', 0) == 0, 'subnet still referenced/allocated; retain it')

    def delete(self, role):
        obj = self.current(role)
        if obj is None:
            if role in self.state['delete']:
                self.state['delete'][role]['phase'] = 'observed-absent'; self.save()
            return
        self.deletion_dependencies(role, obj)
        entry = self.state['delete'].get(role)
        if obj['metadata'].get('deletionTimestamp') and entry is None:
            self.state['delete'][role] = {'at': now(), 'phase': 'observed-already-terminating',
                'attempts': 0, 'uid': obj['metadata']['uid'],
                'resourceVersion': obj['metadata']['resourceVersion']}
            self.save()
        if not obj['metadata'].get('deletionTimestamp'):
            require(entry is None, 'unknown DELETE still present; no automatic resend')
            self.state['delete'][role] = {'at': now(), 'phase': 'unknown', 'attempts': 1,
                'uid': obj['metadata']['uid'], 'resourceVersion': obj['metadata']['resourceVersion']}
            self.save()  # Native UID/RV preconditions are in the actual DeleteOptions body.
            self.kube.delete(obj)
        deadline = time.monotonic() + self.wait_seconds
        while self.current(role) is not None:
            if time.monotonic() >= deadline:
                raise RuntimeError('normal DELETE pending; retain dependency and never force finalizer: ' + role)
            time.sleep(1)
        self.state['delete'][role]['phase'] = 'absent'; self.save()

    def cleanup(self):
        self.guard()
        for role in DELETE_ORDER:
            self.guard(); self.delete(role)
        self.guard()
        return {'result': 'REGISTERED_NATIVE_OBJECTS_ABSENT',
                'direct_ovn_or_backend_mutation': False, 'data_plane_cleanup': 'REQUIRES_INDEPENDENT_READ_ONLY_VERIFICATION'}

    def observe(self):
        values = {}
        for role in ORDER:
            if role not in self.state['registry']:
                values[role] = {'result': 'UNKNOWN_ACK_NO_RECORDED_UID' if role in self.state['create']
                                else 'NOT_REQUESTED', 'get_by_name_not_used_to_adopt': True}
                continue
            obj = self.current(role)
            values[role] = None if obj is None else {'identity': identity(obj), 'spec': obj['spec'],
                'status': obj.get('status', {}), 'deletionTimestamp': obj['metadata'].get('deletionTimestamp')}
        return {'result': 'OBSERVED_ONLY', 'objects': values, 'no_write_reissued': True}


def preflight(kube, nodes):
    require(not (PRIVATE / 'backup.json').exists() and not (PRIVATE / 'state.json').exists(),
            'existing backup/registry: observe it; never overwrite/rebase')
    current_sources = sources(kube); protected = protections(kube); desired = objects()
    inventory = kube.listing('subnets.' + GROUP) + kube.listing('vpcs.' + GROUP)
    inventory += kube.listing('servicecidrs.networking.k8s.io', False) + nodes
    no_conflicts(inventory, {})
    for value in desired.values():
        meta = value['metadata']
        require(kube.get(resource(value['kind']), meta['namespace'], meta['name']) is None, 'proposed name already exists')
    backup = {'at': now(), 'plan_sha256': PLAN_SHA, 'cluster_uid': CLUSTER_UID,
              'sources': current_sources, 'protected': protected, 'objects': desired,
              'cidr_inventory': [{'identity': identity(o), 'spec': o.get('spec', {})} for o in inventory]}
    atomic(PRIVATE / 'backup.json', backup, exclusive=True)
    atomic(PRIVATE / 'state.json', {'at': now(), 'task': TASK, 'source_sha256': digest(backup),
           'registry': {}, 'create': {}, 'delete': {}}, exclusive=True)
    return {'result': 'READ_ONLY_PREFLIGHT_PASS', 'source_sha256': digest(backup),
            'private_backup_reference': str(PRIVATE / 'backup.json'),
            'five_names_absent': True, 'cidr_conflict_inventory_count': len(inventory)}


def fixtures():
    """External API doubles only; exercise the actual driver state transitions."""
    checked = []
    def rejects(label, function):
        try:
            function()
        except (RuntimeError, ValueError):
            checked.append(label); return
        raise RuntimeError('fixture failed to reject: ' + label)
    def fake_obj(role):
        value = copy.deepcopy(objects()[role]); value['metadata'].update(uid='uid-' + role, resourceVersion='1', generation=1)
        return value
    class Fake:
        def __init__(self):
            self.values = {}; self.posts = []; self.deletes = []; self.lose = False; self.pending = False
        def get(self, kind, ns, name):
            return copy.deepcopy(self.values.get((kind, ns, name)))
        def listing(self, kind, namespaced=True):
            return [copy.deepcopy(v) for (k, _, _), v in self.values.items() if k == kind]
        def create(self, body):
            self.posts.append(copy.deepcopy(body))
            if self.lose:
                raise RuntimeError('external unknown ACK')
            value = copy.deepcopy(body); value['metadata'].update(uid='new-' + body['kind'], resourceVersion='123', generation=1)
            self.values[(resource(body['kind']), body['metadata']['namespace'], body['metadata']['name'])] = value
            return copy.deepcopy(value)
        def delete(self, body):
            self.deletes.append(identity(body))
            if not self.pending:
                del self.values[(resource(body['kind']), body['metadata']['namespace'], body['metadata']['name'])]
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp); backup = {'plan_sha256': PLAN_SHA, 'objects': objects()}
        atomic(directory / 'backup.json', backup)
        atomic(directory / 'state.json', {'task': TASK, 'source_sha256': digest(backup), 'registry': {}, 'create': {}, 'delete': {}})
        fake = Fake(); driver = Driver(fake, directory, 0)
        fake.lose = True
        rejects('unknown_ack_persisted', lambda: driver.create('subnet'))
        rejects('unknown_ack_absent_never_reposted', lambda: driver.create('subnet'))
        rejects('unknown_ack_absent_cannot_be_declared_cleaned', lambda: driver.delete('subnet'))
        require(len(fake.posts) == 1 and driver.state['create']['subnet']['attempts'] == 1, 'unknown CREATE retried')
        original = fake.posts[0]; original['metadata'].update(uid='original-uid', resourceVersion='2', generation=1)
        key = (resource('Subnet'), 'kcn-system', SUBNET); fake.values[key] = copy.deepcopy(original)
        rejects('unknown_ack_present_nonce_cannot_adopt_without_original_uid', lambda: driver.current('subnet'))
        require(not driver.state['registry'] and driver.observe()['objects']['subnet']['result']
                == 'UNKNOWN_ACK_NO_RECORDED_UID', 'unknown original UID was invented')
        checked.append('observer_never_adopts_unknown_uid_by_name')
        # The remaining fixtures start from an actual original ACK identity.
        driver.state['registry']['subnet'] = identity(original); driver.save()
        fake.values[key]['metadata']['uid'] = 'foreign-uid'
        rejects('same_name_uid_replacement', lambda: driver.current('subnet'))
        fake.values[key] = copy.deepcopy(original); fake.values[key]['metadata']['annotations'][INTENT] = 'foreign'
        rejects('same_name_nonce_changed', lambda: driver.current('subnet'))
        fake.values[key] = copy.deepcopy(original); fake.values[key]['spec']['gateway'] = 'foreign'
        rejects('spec_drift', lambda: driver.current('subnet'))
        fake.values[key] = copy.deepcopy(original)
        fake.pending = True
        rejects('pending_normal_delete_retains_parent', lambda: driver.delete('subnet'))
        require(len(fake.deletes) == 1 and fake.deletes[0]['uid'] == 'original-uid'
                and fake.deletes[0]['resourceVersion'] == '2', 'delete identity/RV not passed')
        rejects('unknown_delete_never_reissued', lambda: driver.delete('subnet'))
        require(len(fake.deletes) == 1, 'DELETE retried')
        checked.append('delete_receives_exact_uid_and_resourceversion')
        require((directory / 'state.json').stat().st_mode & 0o777 == 0o600, 'state not private600')
        checked.append('atomic_private_registry_600')
    require(len(objects()) == 5 and set(objects()) == set(ORDER), 'proposal count drift')
    checked.append('exact_five_objects_two_tenants')
    args = argparse.Namespace(execute_approved=True, approved_plan_sha='old-three-object-plan', approval_reference='fixture')
    rejects('old_plan_approval_rejected', lambda: authorization(args))
    args.approved_plan_sha = PLAN_SHA; args.approval_reference = ''
    rejects('missing_human_reference_rejected', lambda: authorization(args))
    original = fake_obj('eip1'); desired = objects()['eip1']; entry = {'id': 'nonce'}
    original['metadata']['annotations'] = {INTENT: 'nonce', SOURCE: 'source'}
    original['spec']['ipAddress'] = '10.232.254.242'; owned(original, desired, entry, 'source')
    checked.append('native_auto_allocated_eip_in_reviewed_range')
    original['spec']['ipAddress'] = '172.16.102.192'
    rejects('eip_outside_pool', lambda: owned(original, desired, entry, 'source'))
    original['spec']['ipAddress'] = '10.232.254.243'
    rejects('eip_allocated_address_drift', lambda: owned(original, desired, entry, 'source',
            {'uid': original['metadata']['uid'], 'allocated_ip': '10.232.254.242'}))
    conflict = fake_obj('subnet'); conflict['spec']['cidrBlock'] = '10.232.0.0/16'
    rejects('existing_supernet_conflict', lambda: no_conflicts([conflict], {}))
    conflict['spec']['cidrBlock'] = '10.232.254.244/30'
    rejects('existing_nested_subnet_conflict', lambda: no_conflicts([conflict], {}))
    conflict['spec']['cidrBlock'] = '10.84.1.0/24'; no_conflicts([conflict], {})
    checked.append('nonoverlapping_original_tenant_subnet_allowed')
    parse_networks('- 172.16.101.0/24\n- 10.96.0.0/16\n- 10.16.0.0/16\n')
    rejects('config_required_destination_missing', lambda: parse_networks('["10.16.0.0/16"]'))
    eip = fake_obj('eip1'); eip['status'] = {'phase': 'Bound', 'boundResource': {'resourceType': 'Snat'}}
    driver = object.__new__(Driver); driver.absent = lambda _: None
    rejects('parent_eip_bound_retained', lambda: driver.deletion_dependencies('eip1', eip))
    transport = Kube(); calls = []
    transport.call = lambda args, body=None: calls.append((args, body))
    actual = fake_obj('snat1'); transport.delete(actual)
    options = calls[0][1]
    require(options['preconditions'] == {'uid': actual['metadata']['uid'], 'resourceVersion': '1'}
            and options['propagationPolicy'] == 'Background' and 'gracePeriodSeconds' not in options,
            'actual DeleteOptions lost native UID/RV or forced grace')
    checked.append('transport_delete_uid_rv_preconditions_no_force')
    return {'result': 'PURE_EXTERNAL_BOUNDARY_FIXTURES_PASS', 'checks': checked, 'count': len(checked),
            'cluster_commands': 0, 'cluster_mutations': 0, 'guest_connections': 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('action', nargs='?', default='preflight', choices=('fixture', 'preflight', 'observe', 'apply', 'cleanup'))
    parser.add_argument('--plan', type=Path, default=RUN / 'network-connectivity-change-plan-20261024.md')
    parser.add_argument('--execute-approved', action='store_true')
    parser.add_argument('--approved-plan-sha')
    parser.add_argument('--approval-reference')
    parser.add_argument('--wait-seconds', type=int, default=15)
    args = parser.parse_args(); require(0 <= args.wait_seconds <= 20, 'bounded native observation must be 0..20 seconds')
    os.umask(0o077)
    if args.action == 'fixture':
        result = fixtures(); print(json.dumps(result, indent=2)); return 0
    require(hashlib.sha256(args.plan.read_bytes()).hexdigest() == PLAN_SHA, 'reviewed five-object plan bytes changed')
    mutating = args.action in ('apply', 'cleanup')
    if mutating:
        authorization(args)
    else:
        require(not args.execute_approved and not args.approved_plan_sha, 'read-only action does not consume mutation approval')
    PRIVATE.mkdir(parents=True, mode=0o700, exist_ok=True)
    with (PRIVATE / 'driver.lock').open('a') as local, LOCK.open('a') as heavy:
        fcntl.flock(local, fcntl.LOCK_EX | fcntl.LOCK_NB)
        deadline = time.monotonic() + 5
        while True:
            try:
                fcntl.flock(heavy, fcntl.LOCK_EX | fcntl.LOCK_NB); break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    print('heavy lock busy; no API requests', file=sys.stderr); return 73
                time.sleep(.1)
        kube = Kube(); nodes = scope(kube)
        if args.action == 'preflight':
            result = preflight(kube, nodes)
        else:
            driver = Driver(kube, wait_seconds=args.wait_seconds)
            result = getattr(driver, args.action)()
        result.update(at=now(), action=args.action, plan_sha256=PLAN_SHA,
                      script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                      approval_reference=args.approval_reference if mutating else None)
        atomic(PUBLIC, result); print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (RuntimeError, ValueError, OSError, KeyError, subprocess.SubprocessError) as error:
        print('STOP: ' + str(error), file=sys.stderr)
        sys.exit(1)
