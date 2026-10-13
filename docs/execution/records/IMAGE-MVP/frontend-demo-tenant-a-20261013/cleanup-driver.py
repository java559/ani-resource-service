import base64
import datetime
import hashlib
import json
import os
import pathlib
import shlex
import subprocess
import sys

ROOT = pathlib.Path('/home/chabking/ani-image-mvp-runs/cleanup-old-space-20261013')
PRIVATE = ROOT / 'private'
EVIDENCE = ROOT / 'evidence'
KUBE = ['/home/chabking/ani-four-service-deploy-20261012/tools/kubectl', '--kubeconfig', '/home/chabking/ani-four-service-deploy-20261012/private/kubeconfig']
OLD_TENANT = '08b021f2-f921-4521-b39b-5265ec81f00f'
OLD_SPACE = 'f66a5727-128d-4d8c-ac86-8cc84c6198ee'
INSTALLATION = '497fdb5a-1395-416d-8e26-35c21e8d5893'
CURRENT_TENANT = '46936e6b-3a3b-4868-8d88-6833665abb70'
ROBOT_NAME = 'robot$i-11f3de43d90707d34d4369ec2b113486663a5867'
OLD_COMMAND = '23c23af4-ccd8-4568-bae6-76e17cdf6053'

def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def write(path, value, private=False):
    body = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False, indent=2).encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600 if private else 0o644)
    with os.fdopen(fd, 'wb') as f:
        f.write(body)

def kube(args):
    p = subprocess.run(KUBE + args, capture_output=True, timeout=90)
    if p.returncode:
        raise RuntimeError('kubectl failed: ' + p.stderr.decode()[:1000])
    return p.stdout

def sql(database, statement):
    return kube(['-n', 'ani-platform', 'exec', 'postgresql-0', '--', 'sh', '-c', 'PGPASSWORD="$POSTGRES_PASSWORD" psql -U postgres -d ' + database + ' -At -v ON_ERROR_STOP=1 -c ' + shlex.quote(statement)]).decode().strip()

def rows(database, table, fields, predicate='true'):
    return json.loads(sql(database, 'SELECT coalesce(jsonb_agg(to_jsonb(t)),\'[]\'::jsonb) FROM (SELECT ' + fields + ' FROM ' + table + ' WHERE ' + predicate + ') t'))

def protected():
    return {
        'spaces': rows('ani_resource_image', 'image.spaces', '*', "space_id <> '" + OLD_SPACE + "'"),
        'credentials': rows('ani_resource_image', 'image.credentials', 'space_id,owner_scope,tenant_id,purpose,state,generation,version,robot_id,robot_name,username,expires_at', "space_id <> '" + OLD_SPACE + "'"),
        'registrations': rows('ani_resource_image', 'image.registrations', '*', "space_id <> '" + OLD_SPACE + "'"),
        'commands': rows('ani_resource_image', 'image.commands', 'command_id,space_id,tenant_id,kind,state,phase,version', "space_id <> '" + OLD_SPACE + "'"),
        'tenants': rows('ani_governance', 'sys_tenants', 'id,code,resource_tenant_id,status'),
        'old_network_namespace': json.loads(kube(['get', 'ns', 'tenant-' + OLD_TENANT, '-o', 'json']))['metadata'],
        'resource_deployment_images': [c['image'] for c in json.loads(kube(['-n', 'ani-system', 'get', 'deploy', 'ani-resource-service', '-o', 'json']))['spec']['template']['spec']['containers']],
    }

class Harbor:
    def __init__(self):
        value = json.loads(kube(['-n', 'ani-system', 'get', 'secret', 'resource-runtime-private', '-o', 'json']))['data']
        write(PRIVATE / 'ca.pem', base64.b64decode(value['harbor-ca.pem']), True)
        password = base64.b64decode(value['harbor-password']).strip()
        self.basic = base64.b64encode(b'admin:' + password).decode()
        self.csrf = ''
    def call(self, method, path):
        header = 'Authorization: Basic ' + self.basic + '\n'
        if self.csrf:
            header += 'X-Harbor-Csrf-Token: ' + self.csrf + '\n'
        write(PRIVATE / 'headers', header.encode(), True)
        args = ['curl', '--silent', '--show-error', '--max-time', '25', '--cacert', str(PRIVATE / 'ca.pem'), '--header', '@' + str(PRIVATE / 'headers'), '--cookie-jar', str(PRIVATE / 'cookies'), '--cookie', str(PRIVATE / 'cookies'), '--request', method, '--dump-header', str(PRIVATE / 'response-headers'), '--output', str(PRIVATE / 'response-body'), '--write-out', '%{http_code}', 'https://172.16.101.10:30003' + path]
        p = subprocess.run(args, capture_output=True, timeout=30)
        if p.returncode:
            raise RuntimeError('Harbor transport failed; no automatic retry')
        code = int(p.stdout)
        for line in (PRIVATE / 'response-headers').read_text().splitlines():
            if line.lower().startswith('x-harbor-csrf-token:'):
                self.csrf = line.split(':', 1)[1].strip()
        raw = (PRIVATE / 'response-body').read_text()
        body = json.loads(raw) if raw.strip() else None
        with (EVIDENCE / 'harbor-events.jsonl').open('a') as f:
            f.write(json.dumps({'time': now(), 'method': method, 'path': path, 'status': code, 'body': body}, ensure_ascii=False) + '\n')
        return code, body
    def close(self):
        for name in ['headers', 'cookies', 'response-headers', 'response-body', 'ca.pem']:
            (PRIVATE / name).unlink(missing_ok=True)

def prepare():
    ROOT.mkdir(mode=0o700)
    PRIVATE.mkdir(mode=0o700)
    EVIDENCE.mkdir(mode=0o700)
    before = protected()
    write(EVIDENCE / 'protected-before.json', before)
    cluster = json.loads(kube(['get', 'ns', 'kube-system', '-o', 'json']))
    if cluster['metadata']['uid'] != '5277649d-e28d-4a0a-ae76-8354365c63bc':
        raise RuntimeError('cluster UID differs')
    if any(x['resource_tenant_id'] == OLD_TENANT for x in before['tenants']):
        raise RuntimeError('old UUID is assigned to a Governance tenant; stop')
    predicate = "space_id='" + OLD_SPACE + "' AND tenant_id='" + OLD_TENANT + "' AND owner_scope='tenant'"
    backup = {table: rows('ani_resource_image', 'image.' + table, '*', predicate) for table in ['spaces', 'credentials', 'commands', 'registrations']}
    if len(backup['spaces']) != 1 or len(backup['credentials']) != 1 or len(backup['commands']) != 1 or backup['registrations']:
        raise RuntimeError('unexpected old row inventory')
    space, credential, command = backup['spaces'][0], backup['credentials'][0], backup['commands'][0]
    if space['project_name'] != 't-tenant-a' or space['harbor_project_id'] != 6 or space['installation_id'] != INSTALLATION:
        raise RuntimeError('space identity mismatch')
    if credential['purpose'] != 'pull' or credential['robot_id'] != 5 or credential['robot_name'] != ROBOT_NAME:
        raise RuntimeError('credential identity mismatch')
    if command['command_id'] != OLD_COMMAND or command['state'] != 'succeeded':
        raise RuntimeError('old command is unresolved or differs')
    write(PRIVATE / 'old-image-rows.json', backup, True)
    inventory = {'recorded_at': now(), 'cluster_uid': cluster['metadata']['uid'], 'old_space': {k:space[k] for k in ['space_id','tenant_id','project_name','harbor_project_id','installation_id','state','version']}, 'old_robot': {k:credential[k] for k in ['robot_id','robot_name','purpose','generation','version']}, 'old_command': {k:command[k] for k in ['command_id','kind','state','phase']}, 'row_counts': {k:len(v) for k,v in backup.items()}, 'private_backup_sha256': hashlib.sha256((PRIVATE / 'old-image-rows.json').read_bytes()).hexdigest(), 'preserve': ['current tenant-a t-tenant-a-demo', 'ani-platform-images and its two registrations', 'all other Image rows', 'Governance tenants and permissions', 'Network namespace tenant-' + OLD_TENANT, 'Resource deployment'], 'user_authorized_deletion': True}
    write(EVIDENCE / 'inventory.json', inventory)
    print(json.dumps(inventory, ensure_ascii=False))

def unique_limit():
    statement = """DO $probe$
DECLARE violated text;
BEGIN
  BEGIN
    INSERT INTO image.spaces(space_id,owner_scope,tenant_id,installation_id,registry_authority,project_name)
    SELECT gen_random_uuid(),owner_scope,tenant_id,installation_id,registry_authority,'t-limit-probe-20261013'
    FROM image.spaces WHERE owner_scope='tenant' AND tenant_id='%s';
    RAISE EXCEPTION 'second tenant space unexpectedly accepted';
  EXCEPTION WHEN unique_violation THEN
    GET STACKED DIAGNOSTICS violated = CONSTRAINT_NAME;
    IF violated <> 'image_space_one_per_tenant' THEN
      RAISE EXCEPTION 'unexpected unique constraint %%',violated;
    END IF;
  END;
END $probe$;
SELECT count(*) FROM image.spaces WHERE owner_scope='tenant' AND tenant_id='%s';
SELECT count(*) FROM image.spaces WHERE project_name='t-limit-probe-20261013';
""" % (CURRENT_TENANT, CURRENT_TENANT)
    output = sql('ani_resource_image', statement)
    if output.splitlines()[-2:] != ['1', '0']:
        raise RuntimeError('duplicate space probe changed rows unexpectedly')
    state = sql('ani_resource_image', "SELECT indisunique,indisvalid,indisready FROM pg_index WHERE indexrelid='image.image_space_one_per_tenant'::regclass")
    if state != 't|t|t':
        raise RuntimeError('tenant unique index is not ready/valid')
    result = {'time': now(), 'index': 'image.image_space_one_per_tenant', 'unique_valid_ready': True, 'second_space_rejected_by_live_database': True, 'sqlstate': '23505', 'current_tenant_space_count': 1, 'probe_rows_remaining': 0, 'probe_sql': statement, 'output': output}
    write(EVIDENCE / 'one-space-per-tenant.json', result)
    print(json.dumps({k:v for k,v in result.items() if k not in ['probe_sql','output']}, ensure_ascii=False))

def cleanup():
    inventory = json.loads((EVIDENCE / 'inventory.json').read_text())
    h = Harbor()
    try:
        code, project = h.call('GET', '/api/v2.0/projects/6')
        if code != 200 or project['project_id'] != 6 or project['name'] != 't-tenant-a' or project['metadata']['public'] != 'false':
            raise RuntimeError('Harbor project identity differs')
        code, repositories = h.call('GET', '/api/v2.0/projects/t-tenant-a/repositories?page_size=100')
        if code != 200 or repositories or project.get('repo_count') != 0:
            raise RuntimeError('old project is not empty; stop before delete')
        code, robot = h.call('GET', '/api/v2.0/robots/5')
        expected_description = 'ani-image:v1:' + INSTALLATION + ':' + OLD_SPACE + ':pull:1:' + OLD_COMMAND
        expected_permissions = [{'access':[{'action':'pull','effect':'allow','resource':'repository'}],'kind':'project','namespace':name} for name in ['t-tenant-a','ani-platform-images']]
        if code != 200 or robot['id'] != 5 or robot['name'] != ROBOT_NAME or robot['description'] != expected_description or robot['permissions'] != expected_permissions:
            raise RuntimeError('Harbor robot ownership differs')
        for kind in ['pods','deployments,statefulsets,daemonsets,jobs,cronjobs']:
            for x in json.loads(kube(['get',kind,'-A','-o','json']))['items']:
                spec=x['spec'];spec=spec.get('jobTemplate',{}).get('spec',spec);spec=spec.get('template',{}).get('spec',spec)
                if any('/t-tenant-a/' in c.get('image','') for c in spec.get('containers',[])+spec.get('initContainers',[])):
                    raise RuntimeError('old project is referenced by a workload')
        for x in json.loads(kube(['get','secret','-A','-o','json']))['items']:
            if x.get('type') != 'kubernetes.io/dockerconfigjson':
                continue
            cfg=json.loads(base64.b64decode(x['data']['.dockerconfigjson']))
            for a in cfg.get('auths',{}).values():
                username=a.get('username','')
                if not username and a.get('auth'):
                    username=base64.b64decode(a['auth']).decode().split(':',1)[0]
                if username==ROBOT_NAME:
                    raise RuntimeError('old Robot is referenced by Kubernetes Secret')
        # Fence the exact old space in a short transaction; do not hold a SQL
        # transaction across Harbor HTTP. There is no current Governance owner.
        fence = "BEGIN; SET LOCAL lock_timeout='5s'; SELECT pg_advisory_xact_lock(hashtextextended('image.space:" + OLD_SPACE + "',0)); UPDATE image.spaces SET state='blocked',reason='OPERATOR_CLEANUP',version=version+1,updated_at=clock_timestamp() WHERE space_id='" + OLD_SPACE + "' AND tenant_id='" + OLD_TENANT + "' AND project_name='t-tenant-a' AND harbor_project_id=6 AND version=" + str(inventory['old_space']['version']) + " RETURNING space_id; COMMIT;"
        output=sql('ani_resource_image',fence)
        if OLD_SPACE not in output:
            raise RuntimeError('space CAS fence failed')
        write(EVIDENCE / 'fence.txt', output.encode())
        for endpoint in ['/api/v2.0/robots/5','/api/v2.0/projects/6']:
            code, body=h.call('DELETE',endpoint)
            if code not in [200,202,204]:
                raise RuntimeError('delete not confirmed: '+endpoint+' status='+str(code))
            code, body=h.call('GET',endpoint)
            if code != 404:
                raise RuntimeError('deleted object still exists: '+endpoint)
        predicate="space_id='" + OLD_SPACE + "' AND tenant_id='" + OLD_TENANT + "' AND owner_scope='tenant'"
        deletes = 'BEGIN; SET LOCAL lock_timeout=\'5s\'; SELECT pg_advisory_xact_lock(hashtextextended(\'image.space:' + OLD_SPACE + '\',0)); '
        for table in ['registrations','credentials','commands','spaces']:
            deletes += 'DELETE FROM image.' + table + ' WHERE ' + predicate + '; '
        deletes += 'COMMIT;'
        output=sql('ani_resource_image',deletes)
        write(EVIDENCE / 'database-deletion.txt', output.encode())
        counts={table:int(sql('ani_resource_image','SELECT count(*) FROM image.'+table+' WHERE '+predicate)) for table in ['spaces','credentials','commands','registrations']}
        if any(counts.values()):
            raise RuntimeError('old database rows remain')
        after=protected()
        before=json.loads((EVIDENCE / 'protected-before.json').read_text())
        if after != before:
            raise RuntimeError('protected records changed; inspect exact diff')
        write(EVIDENCE / 'protected-after.json',after)
        result={'finished_at':now(),'old_database_counts':counts,'deleted_project_id':6,'deleted_project_name':'t-tenant-a','deleted_robot_id':5,'Harbor_GET_after_delete':'404 for project and robot','protected_records_unchanged':True,'remaining_tenant_space_count':len([v for v in after['spaces'] if v['owner_scope']=='tenant']),'remaining_platform_space_count':len([v for v in after['spaces'] if v['owner_scope']=='platform']),'preserved_network_namespace':'tenant-'+OLD_TENANT,'private_backup_retained':str(PRIVATE / 'old-image-rows.json'),'deployment_changed':False}
        write(EVIDENCE / 'result.json',result)
        print(json.dumps(result,ensure_ascii=False))
    finally:
        h.close()

if __name__ == '__main__':
    {'prepare':prepare,'limit':unique_limit,'cleanup':cleanup}[sys.argv[1]]()
