#!/usr/bin/env python3
"""Task-only HTTPS ISO sources for the fixed VM MVP run; execute via ssh fedora.

Uses CDI v1.66.1 HTTP secretRef/certConfigMap/checksum without shared trust,
runtime, storage, image conversion, or product authorization changes.
"""
import argparse
import base64
import datetime
import fcntl
import hashlib
import hmac
import http.server
import importlib.util
import json
import os
import pathlib
import re
import secrets
import signal
import socket
import ssl
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request

spec = importlib.util.spec_from_file_location('media_import', pathlib.Path(__file__).with_name('vm-media-import.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
p = m.p
p.OUT = p.RUN / 'evidence/media-http-source'
p.PRIVATE = p.RUN / 'private/media-http-source'
HOST, PORT = '172.16.101.31', 39191
BASE = 'https://%s:%d' % (HOST, PORT)
CM, SECRET = 'vm-mvp-media-http-ca', 'vm-mvp-media-http-auth'
NAMES = {'fedora44': 'vm-mvp-fedora44-http-source',
         'windows2025': 'vm-mvp-windows2025-http-source',
         'virtio': 'vm-mvp-virtio-http-source'}
m.SOURCES = {key: (NAMES[key], *value[1:]) for key, value in m.SOURCES.items()}
ROUTES = {'/media/%s.iso' % key: key for key in m.SOURCES}


def write_private(name, value):
    path = p.PRIVATE / name
    temporary = path.with_suffix(path.suffix + '.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')
    os.replace(temporary, path)


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def file_identity(info):
    return {'device': info.st_dev, 'inode': info.st_ino, 'bytes': info.st_size,
            'mtime_ns': info.st_mtime_ns}


def open_source(key, frozen=None):
    _, path, length, _, _ = m.SOURCES[key]
    if pathlib.Path(path).resolve() != pathlib.Path(path):
        raise RuntimeError('source path contains a symlink')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size != length:
            raise RuntimeError('source type/length changed')
        if frozen is not None and file_identity(info) != frozen:
            raise RuntimeError('source identity changed since checksum verification')
        return fd, info
    except BaseException:
        os.close(fd)
        raise


def verify_files():
    result = {}
    for key, (_, path, length, digest, _) in m.SOURCES.items():
        fd, before = open_source(key)
        with os.fdopen(fd, 'rb') as stream:
            actual = hashlib.file_digest(stream, 'sha256').hexdigest()
            after = os.fstat(stream.fileno())
        if actual != digest or file_identity(before) != file_identity(after):
            raise RuntimeError('fixed ISO checksum or identity changed: ' + key)
        result[key] = file_identity(after)
    write_private('inputs.json', result)
    return result


def process_identity(pid):
    directory = pathlib.Path('/proc') / str(pid)
    fields = (directory / 'stat').read_text().rsplit(')', 1)[1].split()
    return {'pid': pid, 'pgid': os.getpgid(pid), 'start_ticks': int(fields[19]),
            'exe': os.readlink(directory / 'exe'),
            'cmdline': [x.decode() for x in (directory / 'cmdline').read_bytes().split(b'\0') if x]}


def guarded_process():
    receipt = json.loads((p.PRIVATE / 'process.json').read_text())
    actual = process_identity(receipt['pid'])
    if any(actual[key] != receipt[key] for key in actual):
        raise RuntimeError('owned HTTPS process identity changed')
    if receipt['pgid'] != receipt['pid']:
        raise RuntimeError('HTTPS process group is not isolated')
    return receipt


def auth():
    return json.loads((p.PRIVATE / 'auth.json').read_text())


def initialize_tls():
    ca = p.RUN / 'private/pki/ca.crt'
    ca_key = p.RUN / 'private/pki/ca.key'
    if not ca.is_file() or not ca_key.is_file():
        raise RuntimeError('existing task CA files are required')
    if not (p.PRIVATE / 'server.crt').exists():
        extension = p.PRIVATE / 'server.ext'
        extension.write_text('basicConstraints=critical,CA:false\nkeyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\nsubjectAltName=IP:' + HOST + '\n')
        commands = [
            ['openssl', 'req', '-new', '-newkey', 'rsa:2048', '-nodes', '-subj', '/CN=vm-mvp-media-01a168ce', '-keyout', str(p.PRIVATE / 'server.key'), '-out', str(p.PRIVATE / 'server.csr')],
            ['openssl', 'x509', '-req', '-in', str(p.PRIVATE / 'server.csr'), '-CA', str(ca), '-CAkey', str(ca_key), '-set_serial', '0x' + secrets.token_hex(16), '-days', '2', '-sha256', '-extfile', str(extension), '-out', str(p.PRIVATE / 'server.crt')],
        ]
        for command in commands:
            result = subprocess.run(command, capture_output=True, timeout=30)
            if result.returncode:
                raise RuntimeError('task HTTPS leaf generation failed')
    result = subprocess.run(['openssl', 'verify', '-CAfile', str(ca), str(p.PRIVATE / 'server.crt')], capture_output=True, timeout=10)
    if result.returncode:
        raise RuntimeError('task HTTPS certificate verification failed')
    if not (p.PRIVATE / 'auth.json').exists():
        write_private('auth.json', {'username': 'vm-mvp-01a168ce', 'password': secrets.token_urlsafe(36)})


def request(path, method='HEAD', credential=True, headers=None):
    values = {} if headers is None else dict(headers)
    if credential:
        private = auth()
        values['Authorization'] = 'Basic ' + base64.b64encode((private['username'] + ':' + private['password']).encode()).decode()
    context = ssl.create_default_context(cafile=str(p.RUN / 'private/pki/ca.crt'))
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context))
    return opener.open(urllib.request.Request(BASE + path, headers=values, method=method), timeout=5)


def probe():
    checks = {}
    path = '/media/virtio.iso'
    with request(path) as response:
        checks['authenticated_head'] = response.status == 200 and int(response.headers['Content-Length']) == m.SOURCES['virtio'][2]
    with request(path, 'GET', headers={'Range': 'bytes=0-0'}) as response:
        checks['bounded_range'] = response.status == 206 and len(response.read()) == 1
    for key, target, credential, expected in [('authentication_required', path, False, 401),
                                               ('no_directory', '/', True, 404),
                                               ('no_arbitrary_file', '/media/../ca.key', True, 404)]:
        try:
            with request(target, credential=credential):
                checks[key] = False
        except urllib.error.HTTPError as error:
            checks[key] = error.code == expected
    if not all(checks.values()):
        raise RuntimeError('HTTPS transport/authentication probe failed')
    p.save('transport', {'at': now(), 'endpoint': BASE, 'checks': checks,
                         'tls_ca_reference': str(p.RUN / 'private/pki/ca.crt'),
                         'classification': 'TRANSPORT only; not Guest or VM acceptance'})
    return checks


def start():
    if (p.PRIVATE / 'process.json').exists():
        guarded_process()
        print(json.dumps({'already_running': True, 'checks': probe()}))
        return
    with socket.socket() as listener:
        listener.bind((HOST, PORT))
    verify_files()
    initialize_tls()
    script = str(pathlib.Path(__file__).resolve())
    with (p.PRIVATE / 'server.log').open('a') as log:
        child = subprocess.Popen([sys.executable, script, 'serve'], stdin=subprocess.DEVNULL,
                                 stdout=log, stderr=log, start_new_session=True, close_fds=True)
    write_private('process.json', {**process_identity(child.pid), 'started_at': now(),
                                  'endpoint': BASE, 'log_reference': str(p.PRIVATE / 'server.log'),
                                  'source_sha256': {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                                                    for path in (pathlib.Path(script), pathlib.Path(m.__file__), pathlib.Path(p.__file__))}})
    for _ in range(50):
        if child.poll() is not None:
            raise RuntimeError('task HTTPS server exited; inspect private log')
        try:
            with request('/media/virtio.iso'):
                break
        except (OSError, urllib.error.URLError):
            time.sleep(.1)
    else:
        raise RuntimeError('task HTTPS readiness timeout')
    checks = probe()
    identity = guarded_process()
    p.save('server', {**identity, 'credential_reference': str(p.PRIVATE / 'auth.json'),
                      'certificate_reference': str(p.PRIVATE / 'server.crt'), 'checks': checks})
    print(json.dumps({'pid': child.pid, 'endpoint': BASE, 'checks': checks}))


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *_):
        pass  # Never log request lines or headers containing credentials.

    def do_HEAD(self):
        self.transfer(False)

    def do_GET(self):
        self.transfer(True)

    def do_POST(self):
        self.reply(405)

    def reply(self, code, **headers):
        self.send_response(code)
        self.send_header('Content-Length', '0')
        self.send_header('Connection', 'close')
        for key, value in headers.items():
            self.send_header(key.replace('_', '-'), value)
        self.end_headers()
        self.close_connection = True

    def transfer(self, body):
        expected = self.server.authorization
        if len(self.headers.get_all('Authorization', [])) != 1 or not hmac.compare_digest(
                self.headers.get('Authorization', '').encode('utf-8'), expected.encode('ascii')):
            self.reply(401, WWW_Authenticate='Basic realm="vm-mvp-media"')
            return
        key = ROUTES.get(self.path)
        if key is None:
            self.reply(404)
            return
        fd, frozen = self.server.sources[key]
        if file_identity(os.fstat(fd)) != frozen:
            self.reply(409)
            return
        size = frozen['bytes']
        start, end, code = 0, size - 1, 200
        interval = self.headers.get('Range')
        if interval:
            found = re.fullmatch(r'bytes=(\d*)-(\d*)', interval)
            if found is None or not any(found.groups()):
                self.reply(416, Content_Range='bytes */%d' % size)
                return
            left, right = found.groups()
            if left:
                start = int(left)
                end = min(int(right), size - 1) if right else size - 1
            else:
                count = int(right)
                start, end = max(0, size - count), size - 1
            if start > end or start >= size:
                self.reply(416, Content_Range='bytes */%d' % size)
                return
            code = 206
        self.send_response(code)
        self.send_header('Content-Length', str(end - start + 1))
        self.send_header('Content-Type', 'application/octet-stream')
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Connection', 'close')
        if code == 206:
            self.send_header('Content-Range', 'bytes %d-%d/%d' % (start, end, size))
        self.end_headers()
        sent, outcome = 0, 'complete'
        try:
            if body:
                while start + sent <= end:
                    chunk = os.pread(fd, min(128 * 1024, end - start - sent + 1), start + sent)
                    if not chunk:
                        raise OSError('source unexpectedly shortened')
                    self.wfile.write(chunk)
                    sent += len(chunk)
        except OSError:
            outcome = 'client_closed_or_io_failed'
        finally:
            self.close_connection = True
            print(json.dumps({'at': now(), 'media': key, 'method': self.command,
                              'status': code, 'bytes': sent, 'outcome': outcome}), flush=True)


def serve():
    connection = os.environ.get('SSH_CONNECTION', '').split()
    if len(connection) != 4 or connection[2] != HOST:
        raise RuntimeError('server must run on ssh fedora')
    frozen = json.loads((p.PRIVATE / 'inputs.json').read_text())
    sources = {}
    for key in m.SOURCES:
        fd, _ = open_source(key, frozen[key])
        sources[key] = (fd, frozen[key])
    private = auth()
    server = http.server.ThreadingHTTPServer((HOST, PORT), Handler)
    server.daemon_threads = True
    server.sources = sources
    server.authorization = 'Basic ' + base64.b64encode((private['username'] + ':' + private['password']).encode()).decode()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(p.PRIVATE / 'server.crt', p.PRIVATE / 'server.key')
    server.socket = context.wrap_socket(server.socket, server_side=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        for fd, _ in sources.values():
            os.close(fd)


def expected_spec(key):
    _, _, _, digest, capacity = m.SOURCES[key]
    return {'source': {'http': {'url': BASE + '/media/' + key + '.iso', 'secretRef': SECRET,
                               'certConfigMap': CM, 'checksum': 'sha256:' + digest}},
            'contentType': 'kubevirt',
            'storage': {'storageClassName': 'ani-block', 'volumeMode': 'Filesystem',
                        'accessModes': ['ReadWriteOnce'], 'resources': {'requests': {'storage': capacity}}}}


def validate_dv(key):
    dv = p.get('dv', NAMES[key])
    registered = json.loads((p.OUT / 'objects.json').read_text())
    reference = registered.get('DataVolume/' + p.NS + '/' + NAMES[key])
    if reference != p.identity(dv):
        raise RuntimeError('unregistered or replaced HTTP source')
    wanted = expected_spec(key)
    if any(dv['spec'].get(field) != value for field, value in wanted.items()):
        raise RuntimeError('HTTP source spec/checksum/trust changed')
    return dv


def validate_source(key):
    validate_dv(key)
    dv, pvc = m.source_objects(key)
    return dv, pvc


def prepare(key):
    guarded_process()
    probe()
    if key != 'virtio':
        prior, _ = validate_source('virtio')
        if prior.get('status', {}).get('phase') != 'Succeeded':
            raise RuntimeError('VirtIO HTTPS native import must succeed before another ISO')
    ca = (p.RUN / 'private/pki/ca.crt').read_text()
    cm = p.create({'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': {'name': CM, 'namespace': p.NS},
                   'immutable': True, 'data': {'ca.pem': ca}})
    if cm.get('data') != {'ca.pem': ca} or cm.get('immutable') is not True:
        raise RuntimeError('registered task CA config changed')
    private = auth()
    data = {key: base64.b64encode(value.encode()).decode() for key, value in
            [('accessKeyId', private['username']), ('secretKey', private['password'])]}
    try:
        secret = p.create({'apiVersion': 'v1', 'kind': 'Secret', 'metadata': {'name': SECRET, 'namespace': p.NS},
                           'immutable': True, 'type': 'Opaque', 'data': data})
    except Exception:
        raise RuntimeError('task endpoint Secret request failed; details suppressed') from None
    if secret.get('data') != data or secret.get('immutable') is not True:
        raise RuntimeError('registered endpoint credentials changed')
    obj = {'apiVersion': 'cdi.kubevirt.io/v1beta1', 'kind': 'DataVolume',
           'metadata': {'name': NAMES[key], 'namespace': p.NS}, 'spec': expected_spec(key)}
    dv = p.create(obj)
    if any(dv['spec'].get(field) != value for field, value in obj['spec'].items()):
        raise RuntimeError('CDI did not retain required checksum/trust/source contract')
    p.save(key + '-input', {'at': now(), 'source': p.identity(dv), 'file': m.SOURCES[key][1],
                            'input_bytes': m.SOURCES[key][2], 'input_sha256': m.SOURCES[key][3],
                            'requested': m.SOURCES[key][4], 'mode': 'Filesystem',
                            'content_type': 'kubevirt', 'spec': obj['spec'],
                            'secret': p.identity(secret), 'ca_configmap': p.identity(cm),
                            'note': 'async native import; not yet source-ready, catalog or Guest acceptance'})
    print(json.dumps({'prepared': p.identity(dv), 'asynchronous': True}))


def status(key):
    dv = validate_dv(key)
    if p.get('pvc', NAMES[key], optional=True) is None:
        result = {'datavolume': p.identity(dv), 'phase': dv.get('status', {}).get('phase'),
                  'pvc': 'NOT_YET_CREATED', 'conditions': dv.get('status', {}).get('conditions', [])}
        p.save(key + '-status', result)
        print(json.dumps(result))
        return
    m.status(key)


def cleanup_source(key):
    dv, pvc = validate_source(key)
    if dv.get('status', {}).get('phase') not in ('Succeeded', 'Failed'):
        raise RuntimeError('import is still active; do not interrupt blindly')
    pv = p.get('pv', pvc['spec']['volumeName'], '')
    if pv['spec'].get('claimRef', {}).get('uid') != pvc['metadata']['uid'] or pv['spec'].get('persistentVolumeReclaimPolicy') != 'Delete':
        raise RuntimeError('source PV identity or reclaim policy changed')
    attributes = pv['spec'].get('csi', {}).get('volumeAttributes', {})
    volume = {'pvc': p.identity(pvc), 'pv': p.identity(pv),
              'backend_pool': attributes.get('pool'), 'backend_image': attributes.get('imageName')}
    reader = p.backend_reader()
    volume['backend_before'] = p.backend_info(reader, volume)
    p.save(key + '-cleanup-input', {'reader': reader, 'volume': volume, 'datavolume': p.identity(dv)})
    result = p.delete_exact(p.identity(dv))
    print(json.dumps({'source': key, 'result': result, 'reclamation': 'NOT_YET_VERIFIED'}))


def cleanup_status(key):
    frozen = json.loads((p.OUT / (key + '-cleanup-input.json')).read_text())
    volume, reader = frozen['volume'], frozen['reader']
    pvc = p.get('pvc', volume['pvc']['name'], optional=True)
    pv = p.get('pv', volume['pv']['name'], '', optional=True)
    for obj, ref in [(pvc, volume['pvc']), (pv, volume['pv'])]:
        if obj is not None and p.identity(obj) != ref:
            raise RuntimeError('same-name replacement encountered during cleanup')
    backend = p.backend_info(reader, volume)
    trash = p.rbd_read(reader, ['trash', 'ls', volume['backend_pool'], '--format=json'])
    if trash.returncode:
        raise RuntimeError('backend trash read failed')
    matching = [x for x in json.loads(trash.stdout) if x.get('name') == volume['backend_image'] or x.get('id') == volume['backend_before'].get('image_id')]
    passed = pvc is None and pv is None and backend['result'] == 'ABSENT' and not matching
    p.save(key + '-cleanup', {**frozen, 'at': now(), 'pvc_absent': pvc is None, 'pv_absent': pv is None,
                              'backend_after': backend, 'matching_trash': matching,
                              'result': 'PASS' if passed else 'PENDING'})
    print(json.dumps({'source': key, 'result': 'PASS' if passed else 'PENDING'}))


def stop():
    receipt = guarded_process()
    registered = json.loads((p.OUT / 'objects.json').read_text()) if (p.OUT / 'objects.json').exists() else {}
    for ref in registered.values():
        if ref['kind'] == 'DataVolume':
            dv = p.get('dv', ref['name'], optional=True)
            if dv and (p.identity(dv) != ref or dv.get('status', {}).get('phase') not in ('Succeeded', 'Failed')):
                raise RuntimeError('owned native import is still active or replaced')
    os.killpg(receipt['pgid'], signal.SIGTERM)
    for _ in range(50):
        try:
            actual = process_identity(receipt['pid'])
            if actual['start_ticks'] != receipt['start_ticks']:
                raise RuntimeError('PID reused during shutdown')
        except FileNotFoundError:
            break
        time.sleep(.1)
    else:
        raise RuntimeError('owned HTTPS process did not stop; no broad kill')
    p.save('server-stopped', {'at': now(), 'process': receipt})
    (p.PRIVATE / 'process.json').rename(p.PRIVATE / ('process-stopped-%d.json' % receipt['pid']))
    print(json.dumps({'stopped_pid': receipt['pid']}))


def cleanup_infra():
    registered = json.loads((p.OUT / 'objects.json').read_text())
    for key, name in NAMES.items():
        if 'DataVolume/' + p.NS + '/' + name not in registered:
            continue
        if p.get('dv', name, optional=True) is not None:
            raise RuntimeError('source DV still exists; cleanup-source first')
        result = json.loads((p.OUT / (key + '-cleanup.json')).read_text())
        if result.get('result') != 'PASS':
            raise RuntimeError('exact source PVC/PV/RBD reclamation is not confirmed')
    if (p.PRIVATE / 'process.json').exists():
        stop()
    results = []
    for kind, name, resource in [('Secret', SECRET, 'secrets'), ('ConfigMap', CM, 'configmaps')]:
        ref = registered.get(kind + '/' + p.NS + '/' + name)
        if ref is None:
            continue
        obj = p.get(kind, name, optional=True)
        if obj is not None:
            if p.identity(obj) != ref:
                raise RuntimeError('task HTTPS infrastructure UID replaced')
            path = '/api/v1/namespaces/' + p.NS + '/' + resource + '/' + name
            options = {'apiVersion': 'v1', 'kind': 'DeleteOptions', 'preconditions': {'uid': ref['uid']}}
            p.command(['delete', '--raw', path, '-f', '-'], options)
        if p.get(kind, name, optional=True) is not None:
            raise RuntimeError('task HTTPS infrastructure deletion pending')
        results.append({'object': ref, 'absent': True})
    for name in ('auth.json', 'server.key', 'server.csr', 'server.ext', 'inputs.json'):
        (p.PRIVATE / name).unlink(missing_ok=True)
    p.save('infrastructure-cleanup', {'at': now(), 'objects': results, 'server_stopped': True,
                                      'private_credentials_removed': True,
                                      'shared_task_ca_preserved': True})
    print(json.dumps({'infrastructure_cleanup': 'PASS', 'shared_task_ca_preserved': True}))


def plan():
    return {'endpoint': BASE, 'namespace': p.NS, 'ca_configmap': CM, 'endpoint_secret': SECRET,
            'sources': {key: {'name': NAMES[key], 'file': value[1], 'bytes': value[2],
                              'sha256': value[3], 'spec': expected_spec(key)} for key, value in m.SOURCES.items()},
            'order': ['start', 'prepare virtio', 'status virtio until native Succeeded',
                      'prepare windows2025', 'prepare fedora44 only in a later approved window'],
            'protected': ['original three ISO files', 'old Fedora upload DV/PVC and EOF evidence',
                          'Ubuntu Block source and its registration', 'all shared configuration']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['plan', 'start', 'serve', 'probe', 'prepare', 'status',
                                         'hash', 'hash-result', 'cleanup-source', 'cleanup-status', 'cleanup-infra', 'stop'])
    parser.add_argument('source', nargs='?', choices=m.SOURCES)
    args = parser.parse_args()
    os.umask(0o077)
    if args.action == 'serve':
        serve()
        return 0
    if args.action == 'plan':
        print(json.dumps(plan(), indent=2))
        return 0
    if args.action in ('prepare', 'status', 'hash', 'hash-result', 'cleanup-source', 'cleanup-status') and args.source is None:
        parser.error('this action requires an explicit source')
    p.OUT.mkdir(parents=True, exist_ok=True)
    p.PRIVATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    with pathlib.Path('/home/chabking/workspace/ani-network-service-runs/net05a-heavy.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('LOCK_BUSY')
            return 73
        m.verify_scope()
        if args.action == 'prepare':
            crd = p.get('crd', 'datavolumes.cdi.kubevirt.io', '')
            version = next(item for item in crd['spec']['versions'] if item['name'] == 'v1beta1')
            fields = version['schema']['openAPIV3Schema']['properties']['spec']['properties']['source']['properties']['http']['properties']
            if any(fields.get(name, {}).get('type') != 'string' for name in ('checksum', 'secretRef', 'certConfigMap')):
                raise RuntimeError('native CDI schema lacks checksum/auth/CA fields')
        if args.action == 'start':
            start()
        elif args.action == 'probe':
            print(json.dumps(probe()))
        elif args.action == 'stop':
            stop()
        elif args.action == 'cleanup-infra':
            cleanup_infra()
        else:
            {'prepare': prepare, 'status': status, 'hash': lambda key: (validate_source(key), m.hash_source(key)),
             'hash-result': lambda key: (validate_source(key), m.hash_result(key)),
             'cleanup-source': cleanup_source, 'cleanup-status': cleanup_status}[args.action](args.source)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
