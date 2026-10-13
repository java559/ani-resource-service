import base64
import datetime
import hashlib
import json
import os
import pathlib
import shlex
import shutil
import subprocess
import sys
import urllib.parse
import yaml

ROOT = pathlib.Path('/home/chabking/ani-image-mvp-runs/demo-tenant-a-20261013')
PRIVATE = ROOT / 'private'
EVIDENCE = ROOT / 'evidence'
KUBE = ['/home/chabking/ani-four-service-deploy-20261012/tools/kubectl', '--kubeconfig', '/home/chabking/ani-four-service-deploy-20261012/private/kubeconfig']
RESOURCE = os.environ.get('ANI_DEMO_RESOURCE', '/home/chabking/ani-four-service-deploy-20261012/bin/resource')
RUN_ID = 'image-demo-20261013'

def stamp():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def write(path, value, private=False):
    path = pathlib.Path(path)
    data = json.dumps(value, ensure_ascii=False, indent=2).encode() if not isinstance(value, bytes) else value
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600 if private else 0o644)
    with os.fdopen(fd, 'wb') as f:
        f.write(data)

def command(args, input=None, timeout=90):
    p = subprocess.run(args, input=input, capture_output=True, timeout=timeout)
    if p.returncode:
        # Never include stdout: it may be a credential response.
        raise RuntimeError('command failed: ' + shlex.join(args[:8]) + ' exit=' + str(p.returncode) + ' stderr=' + p.stderr.decode()[:1500])
    return p.stdout

def kube(args, input=None):
    return command(KUBE + args, input)

def secret(ns, name):
    result = json.loads(kube(['-n', ns, 'get', 'secret', name, '-o', 'json']))
    return {k: base64.b64decode(v) for k, v in result['data'].items()}

def sql(db, statement):
    return kube(['-n', 'ani-platform', 'exec', 'postgresql-0', '--', 'sh', '-c', 'PGPASSWORD="$POSTGRES_PASSWORD" psql -U postgres -d ' + db + ' -At -v ON_ERROR_STOP=1 -c ' + shlex.quote(statement)]).decode().strip()

def http(path, body=None, auth=True, sensitive=False):
    request = ['curl', '--silent', '--show-error', '--max-time', '50', '--dump-header', str(PRIVATE / 'http-headers'), '--output', str(PRIVATE / 'http-body'), '--write-out', '%{http_code}']
    if auth:
        request += ['--header', '@' + str(PRIVATE / 'tenant-authorization')]
    if body is not None:
        write(PRIVATE / 'request.json', body, True)
        request += ['--header', 'Content-Type: application/json', '--data-binary', '@' + str(PRIVATE / 'request.json')]
    request += ['http://127.0.0.1:27788' + path]
    code = int(command(request).decode())
    payload = json.loads((PRIVATE / 'http-body').read_text())
    record = {'time': stamp(), 'path': path, 'status': code}
    if not sensitive:
        record['request'] = body
        record['response'] = payload
    else:
        record['response_keys'] = list(payload)
    with (EVIDENCE / 'http-events.jsonl').open('a') as f:
        f.write(json.dumps(record, ensure_ascii=False) + '\n')
    if code < 200 or code >= 300:
        raise RuntimeError('HTTP ' + str(code) + ' ' + path + ' ' + json.dumps(payload, ensure_ascii=False))
    return payload

def prepare():
    ROOT.mkdir(mode=0o700)
    PRIVATE.mkdir(mode=0o700)
    EVIDENCE.mkdir(mode=0o700)
    cfg = yaml.safe_load(secret('ani-system', 'resource-configs')['config.yaml'])
    material = secret('ani-system', 'resource-runtime-private')
    image = cfg['image']
    for field in ['database_dsn_file', 'encryption_keys_file', 'harbor_ca_file', 'harbor_password_file', 'cursor_signing_key_file']:
        name = pathlib.Path(image[field]).name
        write(PRIVATE / name, material[name], True)
        image[field] = str(PRIVATE / name)
    dsn = (PRIVATE / 'image-dsn').read_text().strip()
    u = urllib.parse.urlsplit(dsn)
    if u.scheme not in ['postgres', 'postgresql'] or '@' not in u.netloc:
        raise RuntimeError('unsupported DSN format; no mutation performed')
    dsn = urllib.parse.urlunsplit((u.scheme, u.netloc.rsplit('@', 1)[0] + '@127.0.0.1:25432', u.path, u.query, u.fragment))
    write(PRIVATE / 'image-dsn', dsn.encode(), True)
    write(PRIVATE / 'resource.yaml', yaml.safe_dump(cfg).encode(), True)
    operator = {'subject': 'manual-image-demo-20261013', 'actor': 'operator-image-demo-20261013', 'installation_id': image['installation_id'], 'secret_output_directory': str(PRIVATE)}
    write(PRIVATE / 'operator.json', operator, True)
    write(PRIVATE / 'governance-admin-password', secret('ani-system', 'governance-admin-password')['password'], True)
    certdir = PRIVATE / 'registry-cert'
    certdir.mkdir(mode=0o700)
    write(certdir / 'ca.crt', material['harbor-ca.pem'], True)
    cluster = json.loads(kube(['get', 'ns', 'kube-system', '-o', 'json']))
    nodes = json.loads(kube(['get', 'nodes', '-o', 'json']))
    tenants = sql('ani_governance', "select id,name,code,resource_tenant_id,admin_user_id,status,plan_id from sys_tenants where code='tenant-a' and deleted_at is null")
    current = tenants.split('|')
    if len(current) != 7 or current[2] != 'tenant-a':
        raise RuntimeError('tenant-a not uniquely found')
    profile = {'recorded_at': stamp(), 'execution_host': 'fedora', 'cluster_uid': cluster['metadata']['uid'], 'context': command(KUBE + ['config', 'current-context']).decode().strip(), 'nodes': [{'name': n['metadata']['name'], 'addresses': n['status']['addresses']} for n in nodes['items']], 'harbor_url': image['harbor_url'], 'harbor_ca_sha256': hashlib.sha256(material['harbor-ca.pem']).hexdigest(), 'installation_id': image['installation_id'], 'platform_project': image['platform_project'], 'tenant_code': current[2], 'governance_tenant_id': current[0], 'resource_tenant_id': current[3], 'tenant_admin_user_id': current[4], 'tenant_slug': 'tenant-a-demo', 'tenant_project': 't-tenant-a-demo', 'allowed_repositories': ['demo-busybox', 'demo-nginx'], 'allowed_mutations': ['ensure current tenant-a image space', 'first publisher issuance', 'push two images into platform and tenant project', 'register four images'], 'preserve': ['all existing projects and robots', 'old t-tenant-a ownership', 'ordinary user permissions', 'existing tags'], 'no_cleanup_of_demo_images': True, 'resource_binary': RESOURCE, 'resource_binary_sha256': hashlib.sha256(pathlib.Path(RESOURCE).read_bytes()).hexdigest(), 'source_lock': json.loads(pathlib.Path('/home/chabking/ani-four-service-deploy-20261012/sources.json').read_text())}
    write(EVIDENCE / 'profile.json', profile)
    write(EVIDENCE / 'spaces-before.txt', sql('ani_resource_image', 'select space_id,owner_scope,tenant_id,project_name,state,version from image.spaces').encode())
    write(EVIDENCE / 'registrations-before.txt', sql('ani_resource_image', 'select image_id,owner_scope,tenant_id,repository,digest,display_name from image.registrations').encode())
    print(json.dumps(profile, ensure_ascii=False, indent=2))

def login():
    password_file = PRIVATE / 'tenant-password'
    if not password_file.exists():
        password_file = PRIVATE / 'governance-admin-password'
    password = password_file.read_text().strip()
    response = http('/api/v1/auth/password/login', {'tenant_name': 'tenant-a', 'username': 'admin', 'password': password}, auth=False, sensitive=True)
    token = response.get('access_token')
    if not token:
        raise RuntimeError('login did not issue access token')
    write(PRIVATE / 'tenant-authorization', ('Authorization: Bearer ' + token + '\n').encode(), True)
    # Decode only for evidence; server still validates the signature on requests.
    claims = json.loads(base64.urlsafe_b64decode(token.split('.')[1] + '==='))
    write(EVIDENCE / 'login-identity.json', {k:v for k,v in claims.items() if k not in ['jti','iss','aud']})
    print('tenant-a admin login succeeded')

def admin(action, body, output=None):
    name = action + '-' + str(len(list(EVIDENCE.glob('admin-*'))))
    write(PRIVATE / 'admin-request.json', body, True)
    args = [RESOURCE, '-conf', str(PRIVATE / 'resource.yaml'), '-image-operator-config', str(PRIVATE / 'operator.json'), '-image-admin=' + action, '-image-admin-input', str(PRIVATE / 'admin-request.json')]
    if output is not None:
        args += ['-image-secret-output', str(output)]
    started = stamp()
    result = command(args)
    write(EVIDENCE / ('admin-' + name + '.txt'), result)
    write(EVIDENCE / ('admin-' + name + '.json'), {'started_at': started, 'finished_at': stamp(), 'command': args, 'request': body, 'exit': 0})
    return result

def authfile(delivery, name):
    c = delivery['credential']
    username = c.get('Username', c.get('username'))
    if not username or not delivery.get('secret'):
        raise RuntimeError('credential delivery incomplete')
    authority = json.loads((EVIDENCE / 'profile.json').read_text())['harbor_url'].removeprefix('https://')
    path = PRIVATE / (name + '-auth.json')
    write(path, {'auths': {authority: {'auth': base64.b64encode((username + ':' + delivery['secret']).encode()).decode()}}}, True)
    write(EVIDENCE / (name + '-credential.json'), c)
    return path

def platform_issue():
    state = sql('ani_resource_image', "select state,generation,version from image.credentials where owner_scope='platform' and purpose='publisher'")
    if state:
        raise RuntimeError('platform publisher already exists; preserve its credential')
    output = PRIVATE / 'platform-publisher-delivery.json'
    result = admin('issue-platform-publisher', {'idempotency_key': RUN_ID + ':platform-publisher', 'expected_version': 0, 'rotate': False}, output)
    delivery = json.loads(output.read_text())
    authfile(delivery, 'platform')
    print(result.decode())
    print('Platform publisher issued; secret retained only in private files.')

def harbor_get(path):
    args = ['curl', '--silent', '--show-error', '--max-time', '30', '--cacert', str(PRIVATE / 'harbor-ca.pem'), '--header', '@' + str(PRIVATE / 'harbor-authorization'), '--output', str(PRIVATE / 'harbor-response'), '--write-out', '%{http_code}', 'https://172.16.101.10:30003' + path]
    code = int(command(args).decode())
    value = json.loads((PRIVATE / 'harbor-response').read_text())
    return code, value

def sources():
    for repo, source in [('demo-busybox', 'docker.io/library/busybox:1.37.0'), ('demo-nginx', 'docker.io/library/nginx:1.28-alpine')]:
        raw = command(['skopeo', 'inspect', '--raw', 'docker://' + source], timeout=180)
        manifest = json.loads(raw)
        digest = 'sha256:' + hashlib.sha256(raw).hexdigest()
        if 'manifests' in manifest:
            matches = [v for v in manifest['manifests'] if v.get('platform', {}).get('os') == 'linux' and v.get('platform', {}).get('architecture') == 'amd64' and not v.get('platform', {}).get('variant')]
            if len(matches) != 1:
                raise RuntimeError('source does not have one linux/amd64 descriptor')
            selected = matches[0]['digest']
        else:
            selected = digest
        fixed = source.rsplit(':', 1)[0] + '@' + selected
        raw_selected = command(['skopeo', 'inspect', '--raw', 'docker://' + fixed], timeout=180)
        if 'sha256:' + hashlib.sha256(raw_selected).hexdigest() != selected:
            raise RuntimeError('source manifest digest mismatch')
        target = ROOT / 'images' / repo
        target.parent.mkdir(exist_ok=True, mode=0o700)
        if target.exists():
            raise RuntimeError('source directory already exists; inspect before reusing')
        args = ['skopeo', 'copy', '--preserve-digests', 'docker://' + fixed, 'dir:' + str(target)]
        started = stamp()
        result = command(args, timeout=300)
        write(EVIDENCE / (repo + '-source-copy.txt'), result)
        write(EVIDENCE / (repo + '-source-manifest.json'), raw_selected)
        record = {'source': source, 'source_index_digest': digest, 'selected_digest': selected, 'platform': {'os': 'linux', 'architecture': 'amd64'}, 'fixed_source': fixed, 'local_directory': str(target), 'copied_at': stamp(), 'started_at': started, 'command': args, 'exit': 0, 'tag': 'v20261013'}
        write(EVIDENCE / (repo + '-source.json'), record)
        print(json.dumps(record, ensure_ascii=False))

def push(scope):
    profile = json.loads((EVIDENCE / 'profile.json').read_text())
    project = profile['platform_project'] if scope == 'platform' else profile['tenant_project']
    authority = profile['harbor_url'].removeprefix('https://')
    for repo in profile['allowed_repositories']:
        source = json.loads((EVIDENCE / (repo + '-source.json')).read_text())
        tag = source['tag']
        check_path = '/api/v2.0/projects/' + project + '/repositories/' + repo + '/artifacts/' + tag
        code, exists = harbor_get(check_path)
        if code != 404:
            raise RuntimeError('destination must be absent before push: status=' + str(code))
        reference = authority + '/' + project + '/' + repo + ':' + tag
        args = ['skopeo', 'copy', '--preserve-digests', '--dest-authfile', str(PRIVATE / (scope + '-auth.json')), '--dest-cert-dir', str(PRIVATE / 'registry-cert'), 'dir:' + source['local_directory'], 'docker://' + reference]
        started = stamp()
        result = command(args, timeout=300)
        write(EVIDENCE / (scope + '-' + repo + '-push.txt'), result)
        raw = command(['skopeo', 'inspect', '--raw', '--authfile', str(PRIVATE / (scope + '-auth.json')), '--cert-dir', str(PRIVATE / 'registry-cert'), 'docker://' + reference])
        digest = 'sha256:' + hashlib.sha256(raw).hexdigest()
        if digest != source['selected_digest']:
            raise RuntimeError('destination digest differs from source')
        code, artifact = harbor_get(check_path)
        if code != 200 or artifact.get('digest') != digest:
            raise RuntimeError('Harbor artifact readback mismatch')
        record = {'scope': scope, 'reference': reference, 'digest': digest, 'started_at': started, 'finished_at': stamp(), 'command': args, 'exit': 0, 'harbor_artifact': artifact}
        write(EVIDENCE / (scope + '-' + repo + '-push.json'), record)
        print(scope, repo, 'pushed and digest verified', digest)

def platform_register():
    for repo, name in [('demo-busybox', 'BusyBox 1.37.0 · 平台演示'), ('demo-nginx', 'Nginx 1.28 Alpine · 平台演示')]:
        image = json.loads((EVIDENCE / ('platform-' + repo + '-push.json')).read_text())
        body = {'idempotency_key': RUN_ID + ':platform:' + repo, 'image_reference': image['reference'], 'display_name': name, 'description': '2026-10-13 前端镜像目录演示；官方源镜像，固定 linux/amd64 Digest。', 'purposes': ['container'], 'accelerator': 'none'}
        raw = admin('register-platform', body).decode()
        for line in raw.splitlines():
            value = json.loads(line)
            if value.get('action') == 'register-platform':
                write(EVIDENCE / ('platform-' + repo + '-registration.json'), value['result'])
                print(json.dumps(value, ensure_ascii=False))

def tenant_enable():
    response = http('/api/v1/images/space:enable', {'slug': 'tenant-a-demo', 'idempotencyKey': RUN_ID + ':tenant-enable'})
    write(EVIDENCE / 'tenant-space.json', response)
    if response.get('projectName') != 't-tenant-a-demo' or response.get('state') != 'available':
        raise RuntimeError('tenant space is not available as expected')
    print(json.dumps(response, ensure_ascii=False))

def tenant_issue():
    metadata = http('/api/v1/images/publisher-credential')
    if metadata.get('state') != 'not_issued' or int(metadata.get('version', '0')) != 0:
        raise RuntimeError('tenant publisher is already issued; preserve its secret')
    delivery = http('/api/v1/images/publisher-credential:issue', {'idempotencyKey': RUN_ID + ':tenant-publisher', 'expectedVersion': '0'}, sensitive=True)
    write(PRIVATE / 'tenant-publisher-delivery.json', delivery, True)
    authfile(delivery, 'tenant')
    print(json.dumps(delivery['credential'], ensure_ascii=False))

def tenant_register():
    for repo, name in [('demo-busybox', 'BusyBox 1.37.0 · 租户演示'), ('demo-nginx', 'Nginx 1.28 Alpine · 租户演示')]:
        image = json.loads((EVIDENCE / ('tenant-' + repo + '-push.json')).read_text())
        body = {'idempotencyKey': RUN_ID + ':tenant:' + repo, 'imageReference': image['reference'], 'displayName': name, 'description': '2026-10-13 前端镜像目录演示；官方源镜像，固定 linux/amd64 Digest。', 'purposes': ['container'], 'accelerator': 'none'}
        result = http('/api/v1/images/registrations', body)
        write(EVIDENCE / ('tenant-' + repo + '-registration.json'), result)
        print(json.dumps(result, ensure_ascii=False))

def verify():
    profile = json.loads((EVIDENCE / 'profile.json').read_text())
    space = http('/api/v1/images/space')
    result = {'verified_at': stamp(), 'space': space}
    for scope in ['platform', 'tenant']:
        page = http('/api/v1/images/registrations?scope=' + scope + '&limit=20')
        if int(page['total']) != 2 or len(page['items']) != 2:
            raise RuntimeError(scope + ' catalog must contain the two demo registrations')
        result[scope] = page
        for item in page['items']:
            detail = http('/api/v1/images/registrations/' + item['image_id'] + '?scope=' + scope)
            if detail['digest'] != item['digest']:
                raise RuntimeError('detail digest differs from list')
    ownership = sql('ani_resource_image', "select tenant_id from image.spaces where space_id='" + space['spaceId'] + "'")
    if ownership != profile['resource_tenant_id']:
        raise RuntimeError('live tenant UUID ownership mismatch')
    result['database_tenant_id'] = ownership
    write(EVIDENCE / 'frontend-api-verification.json', result)
    print(json.dumps(result, ensure_ascii=False, indent=2))

def candidate():
    image = ROOT / 'candidate-image'
    image.mkdir(mode=0o700)
    shutil.copy2(ROOT / 'bin/resource', image / 'server')
    shutil.copy2('/etc/ssl/certs/ca-certificates.crt', image / 'ca-certificates.crt')
    old_context = pathlib.Path('/home/chabking/ani-four-service-deploy-20261012/images/resource')
    notices = ''
    for name in ['LICENSE', 'LICENSE.md', 'THIRD_PARTY_NOTICES.md']:
        if (old_context / name).is_file():
            shutil.copy2(old_context / name, image / name)
            notices += 'COPY ' + name + ' /licenses/' + name + '\n'
    patch = hashlib.sha256((EVIDENCE / 'repair.patch').read_bytes()).hexdigest()
    parent = 'a57b6760bf2dd7dd7e682d340db226037f529d5c'
    dockerfile = 'FROM scratch\nUSER 65532:65532\nCOPY server /app/resource-service\nCOPY ca-certificates.crt /etc/ssl/certs/ca-certificates.crt\n' + notices + 'ENTRYPOINT ["/app/resource-service"]\nLABEL org.opencontainers.image.source="https://github.com/zhangzhe-ctrl/ani-resource-service" org.opencontainers.image.revision="' + parent + '" io.ani.source.patch.sha256="' + patch + '"\n'
    write(image / 'Dockerfile', dockerfile.encode())
    tag = 'localhost/ani-resource:image-artifact-fix-20261013-' + patch[:12]
    log = command(['podman', 'build', '--network=none', '-t', tag, str(image)], timeout=180)
    write(EVIDENCE / 'candidate-image-build.txt', log)
    archive = ROOT / 'resource-artifact-fix.oci.tar'
    command(['podman', 'save', '--format', 'oci-archive', '-o', str(archive), tag], timeout=180)
    info = json.loads(command(['skopeo', 'inspect', 'oci-archive:' + str(archive)]))
    result = {'tag': tag, 'digest': info['Digest'], 'reference': tag + '@' + info['Digest'], 'archive': str(archive), 'binary_sha256': hashlib.sha256((ROOT / 'bin/resource').read_bytes()).hexdigest(), 'source_parent': parent, 'patch_sha256': patch, 'built_at': stamp()}
    write(EVIDENCE / 'candidate-image.json', result)
    patch_request = {'spec': {'template': {'spec': {'containers': [{'name': 'server', 'image': result['reference']}]}}}}
    write(EVIDENCE / 'deployment-patch.json', patch_request)
    print(json.dumps(result, indent=2))

if __name__ == '__main__':
    {'prepare': prepare, 'login': login, 'platform-issue': platform_issue, 'sources': sources, 'platform-push': lambda: push('platform'), 'platform-register': platform_register, 'tenant-enable': tenant_enable, 'tenant-issue': tenant_issue, 'tenant-push': lambda: push('tenant'), 'tenant-register': tenant_register, 'verify': verify, 'candidate': candidate}[sys.argv[1]]()
