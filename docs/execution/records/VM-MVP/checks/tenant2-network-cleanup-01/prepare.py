#!/usr/bin/env python3
"""Read-only cleanup graph for the existing VM task tenant2. No DELETE exists."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import runpy
import subprocess

RUN = Path('/home/chabking/workspace/ani-resource-service-runs/vm-mvp-20261023-01a168ce')
TENANT = 'd87c4dbd-04fa-4066-9ef6-c6fa4217f316'
NS = 'tenant-' + TENANT
NS_UID = 'fa138fd5-1f35-4fa2-b561-db3d136b2404'
VM = 'e3c11c5a-cf12-4630-a8f9-f907048ffdfc'
VPC = 'vpc_c44c262a220748aab0e4b20558849409'
SUBNET = 'subnet_ea6b62fc625349bcad46ce56ce60f90d'
NAD = 'vm-mvp-tenant2-01a168ce'
NAD_UID = 'c744fbb9-fdff-46b9-8ba9-0c75cfb8e9dc'
NAD_SHA = 'bb5bb6d1ad273609354486dfa052bf46236c47583fdb6a228bab4b75fb902d30'
CLUSTER = '5277649d-e28d-4a0a-ae76-8354365c63bc'
K = ['/home/chabking/ani-four-service-deploy-20261012/tools/kubectl',
     '--kubeconfig=/home/chabking/ani-four-service-deploy-20261012/private/kubeconfig', '--request-timeout=8s']


def digest(x):
    return hashlib.sha256(json.dumps(x, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def load(name):
    p = RUN / 'evidence' / name
    return json.loads(p.read_text()), {'path': str(p), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}


def get(kind, name=None, namespace=NS):
    argv = K + ['get', kind] + ([name, '--ignore-not-found'] if name else [])
    if namespace:
        argv += ['-n', namespace]
    r = subprocess.run(argv + ['-o', 'json'], capture_output=True, text=True, timeout=12)
    assert r.returncode == 0, 'read-only native GET failed: ' + kind + ' ' + r.stderr[-200:]
    return json.loads(r.stdout) if r.stdout.strip() else None


def ref(obj):
    return {'kind': obj['kind'], **{k: obj['metadata'].get(k, '') for k in ('namespace', 'name', 'uid', 'resourceVersion')},
            'deletionTimestamp': obj['metadata'].get('deletionTimestamp')}


def prepare(output):
    assert os.environ.get('SSH_CONNECTION', '').split()[2:3] == ['172.16.101.31'], 'ssh fedora only'
    g = runpy.run_path(str(RUN / 'gov-candidate.py'))
    credential = RUN / 'private/gov-candidate/isolation-credentials.json'
    assert credential.is_file() and not credential.is_symlink() and credential.stat().st_mode & 0o077 == 0
    c = json.loads(credential.read_text()); assert c['tenant_code'] == 'vm-isolation-01a168ce'
    token = g['api']('POST', '/api/v1/auth/password/login', {'tenant_name': c['tenant_code'], 'username': c['username'], 'password': c['password']})['access_token']
    def api(path): return g['api']('GET', path, token=token)
    me = api('/api/v1/admin/me'); assert int(me['tenantId']) == 2
    accepted, accepted_ref = load('gov-tenant2-network.json')
    candidate, candidate_ref = load('tenant2-network-candidate.json')
    created_nad, created_nad_ref = load('tenant2-nad-created.json')
    cleanup, cleanup_ref = load('tenant2-cleanup-delete-cleanup-native.json')
    backend, backend_ref = load('tenant2-cleanup-delete-cleanup-backend.json')
    assert cleanup['vm_id'] == VM and cleanup['result'] == backend['result'] == 'PASS'
    assert cleanup['attachment'] == {'id': 'att_dc9331390ff6468b8a7560c406b4a1db', 'state': 'released', 'blocked': False}
    current_vm = api('/api/v1/virtual-machines/' + VM)
    assert current_vm['closing'] and current_vm['observedState'] == 'Deleted' and int(current_vm['version']) == 9
    assert api('/api/v1/virtual-machines/' + VM + '/operations/' + cleanup['operation']['id'])['state'] == 'succeeded'
    vpc = api('/api/v1/networks/vpcs/' + VPC)
    subnet = api('/api/v1/networks/subnets/' + SUBNET)
    assert vpc['id'] == VPC and vpc['cidr'] == '10.84.0.0/16' and vpc['name'] == 'vm-mvp-tenant2-01a168ce'
    assert subnet['id'] == SUBNET and subnet['vpc_id'] == VPC and subnet['cidr'] == '10.84.1.0/24' and subnet['gateway'] == '10.84.1.1'
    assert get('ns', 'kube-system', '')['metadata']['uid'] == CLUSTER
    ns = get('ns', NS, ''); assert ns['metadata']['uid'] == NS_UID and not ns['metadata'].get('deletionTimestamp')
    assert ns['metadata']['labels'].get('network.ani.io/tenant-id') == TENANT
    nad = get('network-attachment-definitions.k8s.cni.cncf.io', NAD)
    assert nad['metadata']['uid'] == NAD_UID and digest(json.loads(nad['spec']['config'])) == NAD_SHA
    nvpc = get('vpcs.networking.kubercloud.com', VPC.replace('_', '-', 1))
    nsubnet = get('subnets.networking.kubercloud.com', SUBNET.replace('_', '-', 1))
    assert nvpc['metadata']['uid'] == candidate['guards']['vpc_uid'] and nsubnet['metadata']['uid'] == candidate['guards']['subnet_uid']
    inventory = {}
    for kind in ('pods', 'persistentvolumeclaims', 'virtualmachines.kubevirt.io', 'virtualmachineinstances.kubevirt.io',
                 'datavolumes.cdi.kubevirt.io', 'virtualmachinesnapshots.snapshot.kubevirt.io',
                 'virtualmachinesnapshotcontents.snapshot.kubevirt.io', 'virtualmachinerestores.snapshot.kubevirt.io',
                 'volumesnapshots.snapshot.storage.k8s.io', 'vnics.networking.kubercloud.com',
                 'vnicips.networking.kubercloud.com', 'eips.networking.kubercloud.com', 'snats.networking.kubercloud.com',
                 'roles.rbac.authorization.k8s.io', 'rolebindings.rbac.authorization.k8s.io'):
        inventory[kind] = [ref(x) for x in get(kind)['items']]
    subnets = api('/api/v1/networks/subnets?vpc_id=' + VPC + '&limit=100')
    assert not subnets.get('next_cursor') and {x['id'] for x in subnets.get('items', [])} == {SUBNET}
    blockers = {k: v for k, v in inventory.items() if v and k not in ('roles.rbac.authorization.k8s.io', 'rolebindings.rbac.authorization.k8s.io')}
    plan = {'scope': 'one already-created tenant2 VPC/Subnet and its exact task NAD; no tenant1 or other task',
        'state': 'READ_ONLY_PREPARED_NOT_EXECUTED', 'business_mutations': 0, 'native_mutations': 0,
        'private_login_reference': str(credential), 'principal': {'tenant_id': 2, 'resource_tenant_id': TENANT, 'role_ids': me.get('roleIds', [])},
        'sources': [accepted_ref, candidate_ref, created_nad_ref, cleanup_ref, backend_ref],
        'namespace': ref(ns), 'namespace_labels': ns['metadata'].get('labels', {}), 'namespace_owner_references': ns['metadata'].get('ownerReferences', []),
        'vpc': {'business': vpc, 'native': ref(nvpc), 'native_spec': nvpc['spec'], 'native_status': nvpc.get('status', {})},
        'subnet': {'business': subnet, 'native': ref(nsubnet), 'native_spec': nsubnet['spec'], 'native_status': nsubnet.get('status', {})},
        'nad': {'ref': ref(nad), 'config_sha256': NAD_SHA, 'owner_references': nad['metadata'].get('ownerReferences', []), 'create_ack_uid': created_nad['ack']['metadata']['uid']},
        'attachment_proof': {'at': cleanup['at'], **cleanup['attachment'], 'source': cleanup_ref},
        'vm_delete_proof': {k: current_vm.get(k) for k in ('id', 'version', 'closing', 'observedState', 'addresses')},
        'namespace_inventory': inventory, 'observed_blockers': blockers,
        'creation_keys_for_identity_only': {kind: accepted['requests'][kind]['body']['idempotency_key'] for kind in ('vpc', 'subnet')},
        'delete_contract': {'client_idempotency_key': None, 'client_expected_version': None, 'body': None,
            'reason': 'both Gov Delete request protos contain only the path resource ID; Resource owns locking/version checks and reuses deleting/deleted operation',
            'observed_versions_are_guards_only': {'vpc': vpc['version'], 'subnet': subnet['version']},
            'permissions_required': ['network:subnet:delete', 'network:vpc:delete', 'network:operation:get'],
            'http_success': '202 asynchronous deleting, 200 already deleted per contract; follow actual resource.last_operation_id and GET original operation',
            'unknown_ack': 'persist target/first send; GET same resource and last_operation_id before any explicitly approved repeat; never recreate or choose a new ID'},
        'ordered_steps': [
            {'action': 'fresh guard', 'requirements': ['same tenant/cluster/namespace/VPC/Subnet/NAD UIDs', 'all tenant2 VMs Deleted and all attachment references Released/unblocked', 'no remaining native consumers/VNic/VNicIP/EIP/SNAT/LB dependency; authoritative Network admission still required', 'fresh tenant authorization; no permission grant changes']},
            {'action': 'normal Gov DELETE Subnet', 'method': 'DELETE', 'path': '/api/v1/networks/subnets/' + SUBNET, 'body': None, 'send_attempts': 0,
             'wait': 'GET same Subnet+last_operation_id until deleted/succeeded and original native Subnet UID absent; blocked/retrying is not success'},
            {'action': 'normal Gov DELETE VPC', 'method': 'DELETE', 'path': '/api/v1/networks/vpcs/' + VPC, 'body': None, 'send_attempts': 0,
             'precondition': 'only after Subnet deletion confirmed and no remaining blocking child/egress/LB',
             'wait': 'GET same VPC+last_operation_id until deleted/succeeded and original native VPC UID absent'},
            {'action': 'separate exact task NAD cleanup', 'native_delete': False,
             'precondition': 'Root-approved support cleanup: remove only the exact tenant2 trusted policy or stop the task Resource; no consumers; retain tenant1 policy/VM/source/NAD',
             'proposal': {'kind': 'NetworkAttachmentDefinition', 'namespace': NS, 'name': NAD, 'uid': NAD_UID, 'resourceVersion': nad['metadata']['resourceVersion']},
             'delete': 'fresh exact UID/resourceVersion normal DeleteOptions only after approval; no finalizer/force/namespace cascade'},
            {'action': 'Namespace retained by Network contract', 'native_delete': False,
             'reason': 'tenant Namespace is shared placement state; normal VPC deletion does not delete it; NAD has no platform owner-based auto-cleanup',
             'external_dependencies': 'SG owner must handle its namespace-specific Role/RoleBinding/support references. Any eventual exact-UID namespace deletion needs separate final-task/tenant-owner authorization after all resources and mappings are cleared; not implied by VPC DELETE'}],
        'v24_dependency': 'none found: formal V24 covers legacy compute/container/inference/network v1-v2/Instance terminal regression; tenant1 default fixture remains available',
        'protected': 'tenant1 namespace/VPC/Subnet/VMs, all shared image sources, KCN/global configuration, all unrelated task state',
        'source_contracts': ['Resource api/network/v1/network.proto DeleteVPCRequest/DeleteSubnetRequest', 'Gov api/protos/catalog/service/v1/vpc.proto and admin/service/v1/i_network.proto', 'Resource docs/specs/vpc-subnet.md section7 namespace retention and deletion guards'],
        'driver_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    receipt = {'at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'plan': plan, 'plan_sha256': digest(plan), 'api_events': g['EVENTS']}
    assert all(e['method'] == 'GET' or (e['method'] == 'POST' and e['path'] == '/api/v1/auth/password/login') for e in g['EVENTS'])
    assert not output.exists(), 'immutable prepare output already exists'
    g['save'](output, receipt)
    print(json.dumps({'result': 'READ_ONLY_PREPARED', 'plan_sha256': receipt['plan_sha256'], 'output': str(output), 'business_mutations': 0, 'native_mutations': 0, 'observed_blockers': list(blockers)}))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=('prepare',))
    p.add_argument('--output', type=Path, default=RUN / 'evidence/tenant2-network-cleanup-prepared.json')
    a = p.parse_args(); prepare(a.output)
