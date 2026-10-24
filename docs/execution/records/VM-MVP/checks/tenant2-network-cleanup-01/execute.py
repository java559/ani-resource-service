#!/usr/bin/env python3
"""Only the Root-approved tenant2 Subnet then VPC DELETE; no native mutations."""
import argparse
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
DIR = RUN / 'tenant2-network-cleanup-01'
STATE = DIR / 'private-state.json'
LOCK = Path('/home/chabking/workspace/ani-network-service-runs/net05a-heavy.lock')
PLAN = '890715bc8e05e192754ae08391990b56d30778052e24fd4b0457845dcdc788d7'
VM = 'e3c11c5a-cf12-4630-a8f9-f907048ffdfc'
IDS = {'subnet': 'subnet_ea6b62fc625349bcad46ce56ce60f90d', 'vpc': 'vpc_c44c262a220748aab0e4b20558849409'}
CONSUMERS = ('pods', 'persistentvolumeclaims', 'virtualmachines.kubevirt.io', 'virtualmachineinstances.kubevirt.io',
             'datavolumes.cdi.kubevirt.io', 'virtualmachinesnapshots.snapshot.kubevirt.io',
             'virtualmachinesnapshotcontents.snapshot.kubevirt.io', 'virtualmachinerestores.snapshot.kubevirt.io',
             'volumesnapshots.snapshot.storage.k8s.io', 'vnics.networking.kubercloud.com',
             'vnicips.networking.kubercloud.com', 'eips.networking.kubercloud.com', 'snats.networking.kubercloud.com')


def sha(x):
    return hashlib.sha256(json.dumps(x, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def accepted_id(kind, value):
    assert value['id'] == IDS[kind] and value['state'] in ('deleting', 'deleted')
    return str(uuid.UUID(value['last_operation_id']))


def self_test():
    for kind in IDS:
        assert accepted_id(kind, {'id': IDS[kind], 'state': 'deleting', 'last_operation_id': '11111111-1111-4111-8111-111111111111'}) == '11111111-1111-4111-8111-111111111111'
        for field, value in (('id', IDS['vpc' if kind == 'subnet' else 'subnet']), ('state', 'available')):
            bad = {'id': IDS[kind], 'state': 'deleting', 'last_operation_id': '11111111-1111-4111-8111-111111111111'}; bad[field] = value
            try: accepted_id(kind, bad)
            except AssertionError: pass
            else: raise AssertionError('foreign target or nonaccepted state passed')
    print(json.dumps({'result': 'PASS', 'pure_guards': 6, 'helper_imports': 0, 'api_calls': 0, 'native_calls': 0}))


class Driver:
    def __init__(self):
        assert os.environ.get('SSH_CONNECTION', '').split()[2:3] == ['172.16.101.31'], 'ssh fedora only'
        frozen = RUN / 'evidence/tenant2-network-cleanup-prepared.json'
        assert hashlib.sha256(frozen.read_bytes()).hexdigest() == 'd34c43e9395e60edd60e1857b2dcb4566e66863577b89b4cf040ed0013ea1dd8'
        receipt = json.loads(frozen.read_text()); assert sha(receipt['plan']) == receipt['plan_sha256'] == PLAN
        self.plan = receipt['plan']; self.prep = runpy.run_path(str(DIR / 'prepare.py'))
        self.g = runpy.run_path(str(RUN / 'gov-candidate.py'))
        case = RUN / 'vm-product-tenant2-cleanup-delete.py'
        assert hashlib.sha256(case.read_bytes()).hexdigest() == '926b83d7995f17e3929fde05f7f38ba192f1deb9818893a8c0addeb17921c7ba'
        module = runpy.run_path(str(case)); self.reader = module['Live'].__new__(module['Live'])
        credential = Path(self.plan['private_login_reference'])
        assert not credential.is_symlink() and credential.stat().st_mode & 0o077 == 0
        c = json.loads(credential.read_text()); assert c['tenant_code'] == 'vm-isolation-01a168ce'
        self.token = self.g['api']('POST', '/api/v1/auth/password/login', {'tenant_name': c['tenant_code'], 'username': c['username'], 'password': c['password']})['access_token']
        me = self.api('/api/v1/admin/me'); assert int(me['tenantId']) == 2
        self.state = json.loads(STATE.read_text()) if STATE.exists() else {'plan_sha256': PLAN, 'commands': {}}
        assert self.state['plan_sha256'] == PLAN

    def api(self, path):
        return self.g['api']('GET', path, token=self.token)

    def route(self, kind):
        return '/api/v1/networks/' + kind + 's/' + IDS[kind]

    def save(self, phase):
        self.g['save'](STATE, self.state)
        value = {'at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'phase': phase,
            'plan_sha256': PLAN, 'driver_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'private_state_reference': str(STATE), 'commands': self.state['commands'],
            'protected_baseline': self.state.get('protected_baseline'), 'protected_unchanged': self.state.get('protected_unchanged'),
            'business_sql_writes': 0, 'native_mutations': 0, 'nad_namespace_rbac_mutations': 0, 'api_events': self.g['EVENTS']}
        self.g['save'](DIR / 'executed.json', value); self.g['save'](DIR / (phase + '.json'), value)
        return value

    def protect(self):
        now = self.reader.protected()
        assert self.state.get('protected_baseline', now) == now, 'protected tenant1 VM/source/NAD/namespace changed'
        self.state.setdefault('protected_baseline', now); self.state['protected_unchanged'] = True

    def fresh(self, kind):
        self.reader.cluster(); self.protect()
        tenant = self.prep['TENANT']; subnet = self.prep['SUBNET']
        rows = json.loads(self.g['psql']('postgres', "SELECT COALESCE(json_agg(json_build_object('id',attachment_id,'vm',instance_id,'state',state,'blocked',protocol_blocked)),'[]'::json) FROM network_attachments WHERE tenant_id='" + tenant + "' AND subnet_id='" + subnet + "';", database='vm_resource_candidate_01a168ce'))
        assert rows and all(x['state'] == 'released' and x['blocked'] is False for x in rows)
        vm = self.api('/api/v1/virtual-machines/' + VM)
        assert vm['closing'] and vm['observedState'] == 'Deleted' and int(vm['version']) == 9
        for resource in CONSUMERS:
            assert self.prep['get'](resource)['items'] == [], 'native consumer remains: ' + resource
        if kind == 'vpc':
            prior = self.state['commands']['subnet']; assert prior.get('complete')
            assert self.api(self.route('subnet'))['state'] == 'deleted'
            assert self.prep['get']('subnets.networking.kubercloud.com')['items'] == []
            children = self.api('/api/v1/networks/subnets?vpc_id=' + IDS['vpc'] + '&limit=100')
            assert not children.get('items') and not children.get('next_cursor')
        value = self.api(self.route(kind)); assert value['id'] == IDS[kind] and value['state'] == 'available'
        native = self.prep['get'](kind + 's.networking.kubercloud.com', IDS[kind].replace('_', '-', 1))
        frozen = self.plan[kind]['native']
        assert native['metadata']['uid'] == frozen['uid'] and not native['metadata'].get('deletionTimestamp')
        assert value['cidr'] == self.plan[kind]['business']['cidr'] and value['name'] == self.plan[kind]['business']['name']
        return {'resource': value, 'native': self.prep['ref'](native), 'attachments': rows}

    def execute(self, kind, wait):
        command = self.state['commands'].get(kind)
        if command is None:
            before = self.fresh(kind)
            command = {'method': 'DELETE', 'route': self.route(kind), 'body': None, 'client_idempotency_key': None,
                'client_expected_version': None, 'send_attempts': 0, 'ack_unknown': False, 'before': before}
            self.state['commands'][kind] = command; self.save(kind + '-intent')
        if not command.get('operation_id'):
            assert not command['send_attempts'] and not command['ack_unknown'], 'unknown ACK: observe, never auto resend'
            command.update(send_attempts=1, ack_unknown=True, first_send_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
            self.save(kind + '-sending')
            try:
                reply = self.g['api']('DELETE', command['route'], token=self.token)
            except Exception as error:
                command['transport_error'] = str(error); self.save(kind + '-unknown-or-rejected'); raise
            command.update(accepted=reply, http_status=self.g['EVENTS'][-1]['status']); self.save(kind + '-ack')
            # Current Gov contract explicitly documents 200 accepted snapshots.
            assert command['http_status'] in (200, 202)
            command.update(operation_id=accepted_id(kind, reply), ack_unknown=False); self.save(kind + '-pinned')
        return self.observe(kind, wait)

    def observe(self, kind, wait):
        command = self.state['commands'][kind]; assert command['send_attempts'] == 1
        end = time.monotonic() + wait
        while True:
            value = self.api(self.route(kind))
            if not command.get('operation_id'):
                candidate = accepted_id(kind, value)
                operation = self.api('/api/v1/networks/operations/' + candidate)
                assert operation['resource_id'] == IDS[kind] and operation['kind'] == 'delete_' + kind
                command.update(operation_id=candidate, ack_unknown=False, recovered_from_exact_resource=True)
            operation = self.api('/api/v1/networks/operations/' + command['operation_id'])
            assert operation['id'] == command['operation_id'] and operation['resource_id'] == IDS[kind] and operation['kind'] == 'delete_' + kind
            assert value['last_operation_id'] == command['operation_id']
            obj = self.prep['get'](kind + 's.networking.kubercloud.com', IDS[kind].replace('_', '-', 1))
            assert obj is None or obj['metadata']['uid'] == self.plan[kind]['native']['uid']
            command.update(current=value, operation=operation, original_native_gone=obj is None,
                complete=value['state'] == 'deleted' and operation['state'] == 'succeeded' and obj is None)
            self.save(kind + '-observed')
            if command['complete'] or time.monotonic() >= end:
                self.protect(); return self.save(kind + '-complete' if command['complete'] else kind + '-pending')
            time.sleep(2)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=('self-test', 'execute', 'observe'))
    p.add_argument('--kind', choices=('subnet', 'vpc'))
    p.add_argument('--execute-authorized', action='store_true')
    p.add_argument('--wait-seconds', type=int, choices=range(21), default=0)
    a = p.parse_args()
    if a.action == 'self-test': self_test(); return
    assert a.kind and (a.action != 'execute' or a.execute_authorized)
    with LOCK.open('a') as lock:
        end = time.monotonic() + 5
        while True:
            try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB); break
            except BlockingIOError:
                if time.monotonic() >= end: raise SystemExit(73)
                time.sleep(.2)
        d = Driver(); r = d.execute(a.kind, a.wait_seconds) if a.action == 'execute' else d.observe(a.kind, a.wait_seconds)
        c = r['commands'][a.kind]
        print(json.dumps({'phase': r['phase'], 'kind': a.kind, 'operation_id': c.get('operation_id'), 'state': c.get('current', {}).get('state'), 'operation_state': c.get('operation', {}).get('state'), 'native_gone': c.get('original_native_gone'), 'complete': c.get('complete'), 'send_attempts': c['send_attempts'], 'protected_unchanged': r['protected_unchanged']}))


if __name__ == '__main__': main()
