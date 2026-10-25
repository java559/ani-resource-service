#!/usr/bin/env python3
"""Project one approved existing Harbor identity privately; read scopes/tags only."""
import argparse
import base64
import datetime
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import ssl
import stat
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

RUN = Path('/home/chabking/workspace/ani-resource-service-runs/vm-mvp-20261023-01a168ce')
DIRECTORY = RUN / 'consumer-oci-01/publish-driver/harbor-v2'
PLAN_SHA = '9895ba779bcdaca828e25b7059af7b4070a09342e1e49e9c5f216c886fb7bb07'
PUBLISHER_SHA = '3a376f9a80d1293e3206d5ba360f7465562a7834b7376e248eb7ab0589b50509'
HOST = '172.16.101.10:30003'
PROJECT = 'icn-01a15337-release'
SECRET_UID = 'bfd0b923-2098-44bd-bb29-45e79af08b46'
CLUSTER_UID = '5277649d-e28d-4a0a-ae76-8354365c63bc'
K = ['/home/chabking/ani-four-service-deploy-20261012/tools/kubectl', '--kubeconfig',
     '/home/chabking/ani-four-service-deploy-20261012/private/kubeconfig', '--request-timeout=4s']


def require(condition, reason):
    if not condition:
        raise RuntimeError(reason)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute-readonly-authorized', action='store_true')
    parser.add_argument('--approved-plan-sha256', required=True)
    parser.add_argument('--approved-publisher-sha256', required=True)
    parser.add_argument('--continue-after-project-api403', action='store_true')
    args = parser.parse_args()
    require(args.execute_readonly_authorized and args.approved_plan_sha256 == PLAN_SHA and
            args.approved_publisher_sha256 == PUBLISHER_SHA, 'exact approved readonly scope required')
    require(os.environ.get('SSH_CONNECTION', '').split()[2:3] == ['172.16.101.31'], 'Fedora only')
    require(Path(__file__).resolve().parent == DIRECTORY, 'exact task directory required')
    require(sha(DIRECTORY / 'frozen-publish-prepared.json') == PLAN_SHA and
            sha(DIRECTORY / 'publish.py') == PUBLISHER_SHA, 'approved source changed')
    with Path('/home/chabking/workspace/ani-network-service-runs/net05a-heavy.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('LOCK_BUSY')
            return 73
        spec = importlib.util.spec_from_file_location('publisher', DIRECTORY / 'publish.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.certificate_guard()
        images = module.load_plan(DIRECTORY)
        private = DIRECTORY / 'private'
        private.mkdir(mode=0o700, exist_ok=True)
        require(not private.is_symlink() and stat.S_IMODE(private.stat().st_mode) == 0o700,
                'private directory mode changed')
        output = DIRECTORY / ('authority-preflight-continued.json' if args.continue_after_project_api403
                              else 'authority-preflight.json')
        require(not output.exists(), 'preflight already attempted; inspect its recorded outcome')
        previous = None
        if args.continue_after_project_api403:
            original = DIRECTORY / 'authority-preflight.json'
            previous = json.loads(original.read_text())
            require(previous['plan_sha256'] == PLAN_SHA and previous['result'] == 'STOPPED_NO_REGISTRY_WRITE'
                    and previous['reason'] == 'project identity GET returned HTTP 403',
                    'continuation requires the exact original project-management API403')
        receipt = {'result': 'STARTED', 'started_at': utc(), 'plan_sha256': PLAN_SHA,
                   'publisher_sha256': PUBLISHER_SHA, 'preflight_source_sha256': sha(Path(__file__)),
                   'cluster_uid': CLUSTER_UID, 'endpoint': HOST, 'project': PROJECT,
                   'registry_requests': 0, 'registry_writes': 0, 'registry_logins': 0,
                   'service_changes': 0, 'credential_values_or_hashes_public': False,
                   'checks': [], 'scopes': [], 'target_tags': []}
        if previous:
            receipt['retained_project_api403'] = {'reference': str(original), 'sha256': sha(original)}
            receipt['scope_correction'] = 'Root authorized exact historical manifest positive instead of project-management API; same identity, no permission escalation.'
        start = time.monotonic()
        deadline = start + 30

        def save():
            module.atomic(output, receipt, 0o644)

        def remaining():
            value = deadline - time.monotonic()
            require(value > 0, 'readonly preflight deadline reached')
            return min(value, 6)

        def k(stage, argv):
            q = subprocess.run(K + argv, capture_output=True, timeout=remaining())
            require(q.returncode == 0, stage + ' kubectl failed; no retry')
            return q.stdout

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None

        context = ssl.create_default_context(cafile=str(DIRECTORY / 'certs/ca.crt'))
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(),
                                            urllib.request.HTTPSHandler(context=context))

        def get(stage, path, authorization, accept='application/json'):
            require(path.startswith('/') and not path.startswith('//'), 'exact Harbor path required')
            receipt['registry_requests'] += 1
            receipt['current_stage'] = stage
            save()
            request = urllib.request.Request('https://' + HOST + path,
                      headers={'Authorization': authorization, 'Accept': accept}, method='GET')
            try:
                with opener.open(request, timeout=remaining()) as response:
                    body = response.read((2 << 20) + 1)
                    status = response.status
            except urllib.error.HTTPError as error:
                status, body = error.code, error.read((2 << 20) + 1)
            require(len(body) <= 2 << 20, stage + ' response too large')
            if status != 200:
                # Preserve the original response privately, never its content or
                # hash in the public receipt (tokens may occur in server errors).
                path = private / (output.stem + '-http-' + str(receipt['registry_requests']) + '.body')
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(body)
            return status, body

        save()
        try:
            cluster = json.loads(k('cluster identity', ['get', 'namespace', 'kube-system', '-o', 'json']))
            require(cluster['metadata']['uid'] == CLUSTER_UID, 'cluster identity changed')
            node = json.loads(k('node identity', ['get', 'node', 'ani-01', '-o', 'json']))
            require(node['metadata']['uid'] == '201e41ac-c50d-4c77-b118-72d1ed93e111' and
                    {'type': 'InternalIP', 'address': '172.16.101.10'} in node['status']['addresses'],
                    'Harbor endpoint node identity changed')
            meta_args = ['get', 'secret', 'icn-01a15337-release-pull', '-n', 'ani-system', '-o',
                         'jsonpath={.metadata.uid}|{.metadata.resourceVersion}|{.type}']
            before = k('existing identity metadata', meta_args).decode().split('|')
            require(before[0] == SECRET_UID and before[2] == 'kubernetes.io/dockerconfigjson',
                    'existing identity reference changed')
            authfile = private / 'harbor-auth.json'
            if previous:
                require(before[1] == previous['existing_identity_source']['resource_version'],
                        'existing Secret changed after private projection')
                module.regular(authfile, private=True)
                projected = json.loads(authfile.read_text())
                require(set(projected) == {'auths'} and set(projected['auths']) == {HOST},
                        'exact endpoint-only private projection required')
                auth = projected['auths'][HOST]['auth']
            else:
                encoded = k('approved endpoint auth projection', ['get', 'secret', 'icn-01a15337-release-pull',
                            '-n', 'ani-system', '-o', r'jsonpath={.data.\.dockerconfigjson}'])
                configuration = json.loads(base64.b64decode(encoded, validate=True))
                auth = configuration['auths'][HOST]['auth']
            decoded = base64.b64decode(auth, validate=True)
            require(b':' in decoded and decoded.split(b':', 1)[0] and decoded.split(b':', 1)[1],
                    'endpoint identity is not a usable existing Basic credential')
            after = k('identity metadata after projection', meta_args).decode().split('|')
            require(after == before, 'existing identity changed during projection')
            projected = {'auths': {HOST: {'auth': auth}}}
            if authfile.exists():
                module.regular(authfile, private=True)
                require(json.loads(authfile.read_text()) == projected, 'existing task identity differs')
            else:
                module.atomic(authfile, projected)
            receipt['existing_identity_source'] = {'namespace': 'ani-system',
                'secret': 'icn-01a15337-release-pull', 'uid': SECRET_UID, 'resource_version': before[1]}
            receipt['auth_file_reference'] = str(authfile)
            receipt['auth_file_mode'] = '0600'
            receipt['checks'].append('exact existing endpoint identity projected privately; no identity mutation')
            basic = 'Basic ' + auth
            if previous:
                repository = PROJECT + '/network-release-res'
                legacy_digest = 'sha256:9a3d22813b20d0cc7c3b1382a42640a97ed8e0b22ffba5344c33a53da52f494e'
                query = urllib.parse.urlencode({'service': 'harbor-registry',
                        'scope': 'repository:' + repository + ':pull'})
                status, body = get('historical repository pull token', '/service/token?' + query, basic)
                require(status == 200, 'historical repository token HTTP ' + str(status))
                token = json.loads(body)['token']
                status, body = get('exact historical manifest project positive',
                    '/v2/' + repository + '/manifests/' + legacy_digest, 'Bearer ' + token,
                    'application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json')
                require(status == 200 and 'sha256:' + hashlib.sha256(body).hexdigest() == legacy_digest,
                        'historical project manifest positive failed')
                receipt['project_identity'] = {'name': PROJECT, 'proof': 'strict TLS exact previously published manifest',
                    'repository': repository, 'manifest_digest': legacy_digest, 'http_status': status,
                    'project_management_api_permission': 'not requested; original403 retained'}
            else:
                status, body = get('exact existing project identity', '/api/v2.0/projects/' + PROJECT, basic)
                require(status == 200, 'project identity GET returned HTTP ' + str(status))
                project = json.loads(body)
                require(project['name'] == PROJECT and isinstance(project['project_id'], int) and
                        project['project_id'] > 0, 'existing project identity mismatch')
                receipt['project_identity'] = {'name': project['name'], 'project_id': project['project_id']}
            save()
            for service in module.SERVICES:
                image = images[service]
                reference = image['proposed_destination_tag']
                repository, tag = reference[len(HOST) + 1:].rsplit(':', 1)
                query = urllib.parse.urlencode({'service': 'harbor-registry',
                        'scope': 'repository:' + repository + ':pull,push'})
                status, body = get(service + ' scoped token', '/service/token?' + query, basic)
                require(status == 200, service + ' token GET returned HTTP ' + str(status))
                token = json.loads(body).get('token')
                require(isinstance(token, str) and token, service + ' token missing')
                segment = token.split('.')[1]
                claims = json.loads(base64.urlsafe_b64decode(segment + '=' * (-len(segment) % 4)))
                grants = [set(item['actions']) for item in claims.get('access', [])
                          if item.get('type') == 'repository' and item.get('name') == repository]
                granted = set.union(*grants) if grants else set()
                receipt['scopes'].append({'service': service, 'repository': repository,
                    'granted_actions': sorted(granted), 'pull_push': {'pull', 'push'} <= granted})
                save()
                require({'pull', 'push'} <= granted, service + ' exact repository scope insufficient')
                status, body = get(service + ' exact tag absence', '/v2/' + repository + '/manifests/' + tag,
                    'Bearer ' + token, 'application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json')
                codes = [item.get('code') for item in json.loads(body).get('errors', [])] if status == 404 else []
                absent = status == 404 and bool(codes) and all(c in ('MANIFEST_UNKNOWN', 'NAME_UNKNOWN') for c in codes)
                receipt['target_tags'].append({'service': service, 'reference': reference,
                    'http_status': status, 'registry_error_codes': codes, 'confirmed_absent': absent})
                save()
                require(absent, service + ' exact target not conclusively absent; no write permitted')
            module.atomic(private / 'authority.json', {'plan_sha256': PLAN_SHA,
                'publisher_sha256': PUBLISHER_SHA, 'authfile_sha256': sha(authfile),
                'secret_uid': SECRET_UID, 'secret_resource_version': before[1],
                'project_identity': receipt['project_identity'], 'preflight_finished_at': utc()})
            receipt['result'] = 'READY_STRICT_TLS_SCOPES_AND_THREE_NEW_TAGS_ABSENT'
        except (RuntimeError, OSError, ValueError, KeyError, IndexError, subprocess.TimeoutExpired) as error:
            # No raw HTTP response, token, credential or its hash enters public output.
            receipt['result'] = 'STOPPED_NO_REGISTRY_WRITE'
            receipt['error_class'] = type(error).__name__
            receipt['reason'] = str(error) if isinstance(error, RuntimeError) else 'stage failed; no retry or permission escalation'
        finally:
            receipt['elapsed_seconds'] = round(time.monotonic() - start, 3)
            receipt['finished_at'] = utc()
            save()
            print(json.dumps(receipt, sort_keys=True))
        return 0 if receipt['result'].startswith('READY_') else 1


if __name__ == '__main__':
    raise SystemExit(main())
