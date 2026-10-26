#!/usr/bin/env python3
"""Bounded PostgreSQL ACL fault for the task's real Gov pre-dispatch test.

No business rows are changed. UPDATE(id) keeps SELECT FOR UPDATE usable while
the worker cannot persist its claim. Restore the exact original ACL before
allowing the original DELETE to dispatch. This is not a product control API.
"""
import argparse
import datetime
import fcntl
import json
import os
from pathlib import Path
import runpy
import uuid

RUN = Path('/home/chabking/workspace/ani-resource-service-runs/vm-mvp-20261023-01a168ce')
DB = 'vm_govlive_01a168ce'
OWNER = 'vm_govlive_owner_01a168ce'
ROLE = 'vm_govlive_runtime_01a168ce'
TABLE = 'public.sys_quota_operations'
STATE = RUN / 'private/gov-candidate/delete-fence-probe-acl.json'
RECEIPT = RUN / 'evidence/gov-delete-fence-probe-acl.json'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('inspect', 'pause', 'restore'))
    args = parser.parse_args()
    connection = os.environ.get('SSH_CONNECTION', '').split()
    assert len(connection) == 4 and connection[2] == '172.16.101.31', 'ssh fedora only'
    driver = runpy.run_path(str(RUN / 'gov-candidate.py'))
    assert (driver['DATABASE'], driver['OWNER'], driver['RUNTIME']) == (DB, OWNER, ROLE)
    sql, save = driver['psql'], driver['save']

    def snapshot():
        query = f"""SELECT jsonb_build_object(
          'database',current_database(),'database_owner',pg_get_userbyid(d.datdba),
          'table_owner',pg_get_userbyid(c.relowner),'table_acl',c.relacl::text,
          'table_grants',(SELECT jsonb_agg(jsonb_build_object('grantor',pg_get_userbyid(x.grantor),
            'grantee',CASE WHEN x.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(x.grantee) END,
            'privilege',x.privilege_type,'grantable',x.is_grantable)
            ORDER BY x.grantor,x.grantee,x.privilege_type) FROM aclexplode(c.relacl) x),
          'columns',COALESCE((SELECT jsonb_object_agg(a.attname,a.attacl::text)
            FROM pg_attribute a WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped AND a.attacl IS NOT NULL),'{{}}'::jsonb),
          'runtime',jsonb_build_object('superuser',r.rolsuper,'inherit',r.rolinherit,'bypassrls',r.rolbypassrls),
          'memberships',(SELECT count(*) FROM pg_auth_members m WHERE m.member=r.oid),
          'select',has_table_privilege(r.oid,c.oid,'SELECT'),
          'insert',has_table_privilege(r.oid,c.oid,'INSERT'),
          'delete',has_table_privilege(r.oid,c.oid,'DELETE'),
          'update',has_table_privilege(r.oid,c.oid,'UPDATE'),
          'id_update',has_column_privilege(r.oid,c.oid,'id','UPDATE'),
          'dispatch_update',has_column_privilege(r.oid,c.oid,'dispatch_state','UPDATE'))
          FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
          CROSS JOIN pg_roles r CROSS JOIN pg_database d
          WHERE c.relname='sys_quota_operations' AND n.nspname='public'
          AND r.rolname='{ROLE}' AND d.datname=current_database();"""
        value = json.loads(sql(OWNER, query))
        assert value['database'] == DB and value['database_owner'] == OWNER and value['table_owner'] == OWNER
        assert value['runtime'] == {'superuser': False, 'inherit': False, 'bypassrls': False}
        assert value['memberships'] == 0 and all(value[k] for k in ('select', 'insert', 'delete'))
        return value

    def paused(value, before):
        expected = dict(before)
        expected.update(update=False, dispatch_update=False,
                        columns={'id': '{' + ROLE + '=w/' + OWNER + '}'},
                        table_grants=[g for g in before['table_grants']
                                      if not (g['grantee'] == ROLE and g['privilege'] == 'UPDATE')])
        # relacl text ordering is not significant; the full grant list above is.
        return all(value[k] == v for k, v in expected.items() if k != 'table_acl')

    lock_path = Path('/home/chabking/workspace/ani-network-service-runs/net05a-heavy.lock')
    inherited = os.environ.get('ANI_VM_TEST_HEAVY_LOCK_FD')
    if inherited is not None:
        descriptor = int(inherited)
        expected, actual = lock_path.stat(), os.fstat(descriptor)
        assert descriptor > 2 and (expected.st_dev, expected.st_ino) == (actual.st_dev, actual.st_ino)
        # dup shares the parent's already locked open-file-description. The
        # unchanged flock below confirms it; closing this alias keeps the
        # parent's original descriptor locked throughout finally restoration.
        lock_context = os.fdopen(os.dup(descriptor), 'a')
    else:
        lock_context = lock_path.open('a')
    with lock_context as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        current = snapshot()
        state = json.loads(STATE.read_text()) if STATE.exists() else None
        if args.action == 'inspect':
            save(RECEIPT, {'action': 'inspect', 'snapshot': current, 'changed': False})
            print(json.dumps({'action': 'inspect', 'receipt': str(RECEIPT), 'table_update': current['update'], 'changed': False}))
            return
        if args.action == 'pause':
            if state is None:
                assert current['update'] and current['id_update'] and current['dispatch_update'] and current['columns'] == {}
                updates = [g for g in current['table_grants'] if g['grantee'] == ROLE and g['privilege'] == 'UPDATE']
                assert updates == [{'grantor': OWNER, 'grantee': ROLE, 'privilege': 'UPDATE', 'grantable': False}]
                origin = json.loads((RUN / 'private/gov-candidate/fence-probe-vm.json').read_text())
                assert str(uuid.UUID(origin['vm_id'])) == origin['vm_id']
                assert origin['plan']['name'] == 'vm-fence-01a168ce'
                assert origin['create']['operation']['state'] == 'succeeded'
                assert origin['start']['operation']['state'] == 'succeeded'
                assert origin['plan']['create_body']['data']['startAfterCreate'] is False
                assert 'guestInitialization' not in origin['plan']['create_body']['data']
                assert str(uuid.UUID(origin['create_operation_id'])) == origin['create_operation_id']
                pending = int(sql(OWNER, "SELECT count(*) FROM public.sys_quota_operations WHERE dispatch_state IN ('QUEUED','DISPATCHING','UNKNOWN');").strip())
                assert pending == 0, 'outbox has unresolved operations: do not pause a possibly in-flight dispatch'
                original_state = sql(OWNER, "SELECT dispatch_state FROM public.sys_quota_operations WHERE operation_id='" + origin['create_operation_id'] + "' AND resource_id='" + origin['vm_id'] + "' AND action='/vm.v1.VMService/AcceptCreate';").strip()
                assert original_state == 'ACKED', 'original CREATE must already have a durable owner ACK'
                state = {'before': current, 'intent_at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'phase': 'pause-intent'}
                save(STATE, state)
            assert state['phase'] in ('pause-intent', 'paused'), 'a restored fault cannot be silently reapplied'
            if current == state['before']:
                assert state['phase'] == 'pause-intent'
                sql(OWNER, f'BEGIN; REVOKE UPDATE ON {TABLE} FROM {ROLE}; GRANT UPDATE (id) ON {TABLE} TO {ROLE}; COMMIT;')
                current = snapshot()
            assert paused(current, state['before']), 'unexpected ACL: inspect before any recovery'
            if 'paused' in state:
                assert current == state['paused'], 'paused ACL drifted'
            state.update(phase='paused', paused=current)
        else:
            assert state is not None and state['phase'] in ('pause-intent', 'paused', 'restore-intent', 'restored')
            if current != state['before']:
                assert paused(current, state['before']), 'ACL drifted; do not overwrite it'
                if 'paused' in state:
                    assert current == state['paused'], 'recorded paused ACL drifted'
                state['paused'] = current
                state['phase'] = 'restore-intent'
                save(STATE, state)
                sql(OWNER, f'BEGIN; REVOKE UPDATE (id) ON {TABLE} FROM {ROLE}; GRANT UPDATE ON {TABLE} TO {ROLE}; COMMIT;')
                current = snapshot()
            assert current == state['before'], 'original full ACL not restored'
            state['phase'] = 'restored'
        save(STATE, state)
        save(RECEIPT, {'action': args.action, 'phase': state['phase'], 'original': state['before'], 'current': current,
                       'business_row_writes': False, 'private_state_reference': str(STATE)})
        print(json.dumps({'action': args.action, 'phase': state['phase'], 'receipt': str(RECEIPT), 'business_row_writes': False}))


if __name__ == '__main__':
    main()
