"""Explicit Stop, Eject INSTALL, Boot SYSTEM_DISK, Start for one installed ISO VM.

Every phase uses a fresh normal tenant session and the original heavy lock.
Root must first attest manual installation and root-marker write in a fixed
release file, and pin the actual final Windows hot-attach-D4 proof. Only its
Windows spec digest, product version and last operation replace the old pins. No Guest action, unknown-ACK replay, or old ISO-state access.
"""
from pathlib import Path
import argparse, datetime, fcntl, hashlib, http.cookiejar, json, os, runpy, stat, subprocess, urllib.error, urllib.request, uuid

R = Path('/home/chabking/workspace/ani-resource-service-runs/vm-mvp-20261023-01a168ce')
D = R / 'iso-marker-02-controls'
TASK = 'vm-mvp-fedora44-iso-marker-02-01a168ce'
VMID = '96575d3e-185d-4939-a01f-b82562d7af08'
CREATE_OP = 'f9299104-142c-4696-b729-07aaac5da896'
NATIVE_SHA = 'bd28c5d1fc50f1be0225569f288e042c87e35d958dcabb03627a31356c576c1a'
NATIVE_VM_UID = '68f92d12-8d14-4d73-ba97-996d328c23f9'
FROZEN = R / 'iso-marker-02/frozen-request.json'
FROZEN_SHA = '108be646dda9d053f731413a212280bd73667ad0efc4a856fee688066dd348be'
HELPER = R / 'v10-network-positive-02/inventory.py'
HELPER_SHA = '6219b84ddda56dfed27d683c0d95e0bb68bc3772e8be1a6e8d7c057f9f718a45'
LOCK = '/home/chabking/workspace/ani-network-service-runs/net05a-heavy.lock'
ORDER = ['stop', 'eject', 'boot', 'start']
BASE = '/api/v1/virtual-machines/' + VMID
HOT_D4 = R / 'windows-online-hotloop-02/verified-hot-attach-d4.json'
WINDOWS_LAUNCHER = {'name': 'virt-launcher-vm-fc154879-8a40-4b18-967c-215f5cf5992d-5qxps', 'uid': '2bd0e41a-40ac-497e-84c8-93d1ba42a0ec'}


def require(ok, message):
    if not ok:raise RuntimeError(message)


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def digest(value):return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def save(path, value):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as f:json.dump(value, f, indent=2);f.write('\n');f.flush();os.fsync(f.fileno())


def own_json(path):
    m = path.lstat()
    require(stat.S_ISREG(m.st_mode) and m.st_uid == os.getuid() and stat.S_IMODE(m.st_mode) == 0o600, 'own regular0600 reference required')
    return json.loads(path.read_text())



def windows_after_online_hot(plan, release, namespace):
    path = Path(release.get('windows_hot_attach_d4_receipt', ''))
    expected_sha = release.get('windows_hot_attach_d4_sha256', '')
    require(path == HOT_D4 and path.resolve() == HOT_D4 and not path.is_symlink() and len(expected_sha) == 64 and all(c in '0123456789abcdef' for c in expected_sha), 'Root must pin the exact final Windows hot-attach-D4 path/SHA')
    require(sha(path) == expected_sha, 'final Windows hot-attach-D4 proof changed')
    proof = json.loads(path.read_text());p = proof['proof'];n = p['native'];current = p['current'];original = plan['protected']['windows']
    require(proof.get('phase') == 'NATIVE_VERIFIED_HOT_ATTACH_D4' and proof.get('intent') == 'verified-hot-attach-d4' and proof.get('session_revoked') is True, 'actual completed final hot-attach-D4 verification required')
    require(proof['vm_id'] == current['id'] == original['vm_id'] and proof['native_vm_uid'] == n['vm']['uid'] == original['vm_uid'], 'same original Windows VM required')
    require(n['vmi']['uid'] == original['vmi_uid'] and n['vmi']['phase'] == 'Running' and n['vmi']['node'] == original['node'] and {k: n['launcher'][k] for k in ['name', 'uid']} == WINDOWS_LAUNCHER, 'original Windows VMI/launcher/node must remain unchanged')
    require(all(n[k]['namespace'] == namespace for k in ['vm', 'vmi', 'launcher']) and all(n[k]['name'] == 'vm-' + original['vm_id'] for k in ['vm', 'vmi']), 'original Windows namespace/native names required')
    refs = [row['pvc'] for row in n['storage']]
    require(len(refs) == len(original['pvc_refs']) == 5 and {row['name']: row['uid'] for row in refs} == {row['name']: row['uid'] for row in original['pvc_refs']} and all(row['namespace'] == namespace for row in refs), 'all five original Windows PVC mappings must remain unchanged')
    require(str(current['version']).isdigit() and int(current['version']) > 18 and current['observedState'] == 'Running' and current['closing'] is False and str(uuid.UUID(current['lastOperationId'])) == current['lastOperationId'], 'actual final Windows current/version/lastop required')
    require(p['vm_spec']['template']['spec']['domain']['firmware']['uuid'] == original['vm_id'], 'final Windows spec belongs to original VM')
    w = dict(original)
    w.update(vm_spec_sha256=digest(p['vm_spec']), product_version=str(current['version']), last_operation_id=current['lastOperationId'])
    return w, {'path': str(path), 'sha256': expected_sha}

def main(a):
    require(a.execute_authorized and os.uname().nodename == 'fedora' and Path(__file__).resolve().parent == D and sha(__file__) == a.runner_sha256, 'fixed Fedora phase/source required')
    require(sha(FROZEN) == FROZEN_SHA and sha(HELPER) == HELPER_SHA and sha(a.native_receipt) == a.native_sha256 == NATIVE_SHA and sha(a.root_release) == a.root_release_sha256, 'fixed original preparation/native/release required')
    require(a.native_receipt.resolve() == R / 'iso-marker-02/native-01.json' and a.root_release.resolve().parent == D, 'exact task-local evidence required')
    plan = json.loads(FROZEN.read_text());native = json.loads(a.native_receipt.read_text());release = json.loads(a.root_release.read_text())
    require(plan['task_name'] == TASK and native['result'] == 'PASS_EXACT_NEW_ISO_NATIVE_IDENTITY' and native['vm_id'] == VMID and native['operation_id'] == CREATE_OP, 'exact original new ISO VM required')
    require(release.get('task_name') == TASK and release.get('vm_id') == VMID and release.get('native_receipt_sha256') == a.native_sha256 and release.get('runner_sha256') == a.runner_sha256 and release.get('manual_install_completed') is True and release.get('root_marker_written') is True, 'Root must confirm manual installation and root marker before controls')
    h = runpy.run_path(str(HELPER));pin = dict(plan['protected']);target = native['native'];volumes = target['volumes']
    pin['windows'], hot_d4_reference = windows_after_online_hot(plan, release, h['NS'])
    require(target['vm']['uid'] == NATIVE_VM_UID and target['vmi']['phase'] == 'Running', 'original native01 VM UID/Running proof required')
    require(len(volumes) == 2 and {x['role'] for x in volumes} == {'root', 'installer'}, 'original root and retained installer chains required')
    intent = a.intent if a.phase == 'observe' else a.phase
    require(intent in ORDER and (a.phase == 'observe' or not a.intent), 'one explicit control or original observation required')
    label = a.phase + '-' + (intent + '-' if a.phase == 'observe' else '') + a.label
    require(all(c.isalnum() or c == '-' for c in label) and len(label) < 70, 'bounded unique phase label required')
    out = D / (label + '.json');private = D / 'private' / label;statepath = D / 'private/journal.json'
    require(not out.exists() and not private.exists(), 'retain original journal/phase; no implicit repeat')
    os.umask(0o077)
    with open(LOCK) as lock:
        try:fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:print(json.dumps({'result': 'LOCK_BUSY', 'api_calls': 0}));return 73
        private.mkdir(mode=0o700, parents=True)
        record = {'task_name': TASK, 'vm_id': VMID, 'phase': a.phase, 'intent': intent, 'stage': 'guards',
                  'runner_sha256': a.runner_sha256, 'native_receipt_sha256': a.native_sha256, 'root_release_sha256': a.root_release_sha256,
                  'windows_hot_attach_d4_reference': hot_d4_reference,
                  'started_at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'business_send_attempts': 0,
                  'cluster_writes': 0, 'PG_calls': 0, 'Guest_actions': 0, 'Windows_mutations': 0,
                  'credential_values_or_value_hashes_public': False, 'calls': []}
        token = None;jar = http.cookiejar.CookieJar();opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(jar))
        journal = own_json(statepath) if statepath.exists() else {'task_name': TASK, 'vm_id': VMID, 'native_receipt_sha256': a.native_sha256, 'commands': {}}
        require(journal['task_name'] == TASK and journal['vm_id'] == VMID and journal['native_receipt_sha256'] == a.native_sha256, 'exact new controls journal required')
        def flush():save(out, record)
        def kube(kind, name=None, ns=None):
            args = h['K'] + ['get', kind] + ([name] if isinstance(name, str) else (name or [])) + (['-n', ns] if ns else [])
            p = subprocess.run(args + ['--ignore-not-found', '-o', 'json'], capture_output=True, timeout=15)
            require(p.returncode == 0, 'bounded exact native read unavailable')
            return json.loads(p.stdout) if p.stdout.strip() else None
        def owned(obj, kind, uid):return any(o['kind'] == kind and o['uid'] == uid and o.get('controller') for o in obj['metadata'].get('ownerReferences', []))
        def guard():
            require(kube('namespace', 'kube-system')['metadata']['uid'] == plan['cluster_uid'] and kube('namespace', h['NS'])['metadata']['uid'] == h['NSUID'], 'original cluster/namespace differs')
            gp, _ = h['process'](35161, 74249534, pin['task_processes']['governance']['binary_sha256'])
            rp, env = h['process'](3881653, 72749000, pin['task_processes']['resource']['binary_sha256'])
            require({'governance': gp, 'resource': rp} == pin['task_processes'], 'original task processes/listeners differ')
            cfg = json.loads(Path(env['ANI_VM_CONFIG_FILE']).read_text());n = pin['network'];s = pin['source']
            require([x for x in cfg['Networks'] if x['TenantID'] == h['TENANT'] and x['VPCID'] == h['VPC'] and x['SubnetID'] == h['SUBNET']] == [n], 'original task network differs')
            nad = kube('network-attachment-definitions.k8s.cni.cncf.io', n['NADName'], h['NS'])
            require(nad['metadata']['uid'] == n['NADUID'] and digest(json.loads(nad['spec']['config'])) == n['NADConfigDigest'], 'original NAD differs')
            source = kube('pvc', s['pvc_name'], s['namespace']);sdv = kube('datavolume', s['pvc_name'], s['namespace']);spv = kube('pv', s['pv_name'])
            require(source['metadata']['uid'] == s['pvc_uid'] and source['status']['phase'] == 'Bound' and source['metadata']['annotations']['vm.ani.io/source-sha256'] == s['checksum'] and sdv['metadata']['uid'] == s['dv_uid'] and sdv['status']['phase'] == 'Succeeded' and spv['metadata']['uid'] == s['pv_uid'] and spv['spec']['claimRef']['uid'] == s['pvc_uid'], 'original Fedora source chain differs')
            w = pin['windows'];wvm = kube('virtualmachine', 'vm-' + w['vm_id'], h['NS']);wvmi = kube('virtualmachineinstance', 'vm-' + w['vm_id'], h['NS'])
            wpvc = kube('pvc', [p['name'] for p in w['pvc_refs']], h['NS'])['items']
            wlauncher = kube('pod', WINDOWS_LAUNCHER['name'], h['NS'])
            require(wlauncher['metadata']['uid'] == WINDOWS_LAUNCHER['uid'] and owned(wlauncher, 'VirtualMachineInstance', w['vmi_uid']) and wlauncher['status']['phase'] == 'Running' and wlauncher['spec']['nodeName'] == w['node'] and not wlauncher['metadata'].get('deletionTimestamp') and all(not p['metadata'].get('deletionTimestamp') for p in wpvc), 'original Windows launcher/fivePVC identity differs')
            require(wvm['metadata']['uid'] == w['vm_uid'] and digest(wvm['spec']) == w['vm_spec_sha256'] and wvmi['metadata']['uid'] == w['vmi_uid'] and wvmi['status']['phase'] == 'Running' and owned(wvmi, 'VirtualMachine', w['vm_uid']) and {p['metadata']['name']: p['metadata']['uid'] for p in wpvc} == {p['name']: p['uid'] for p in w['pvc_refs']}, 'Windows final-hot original native/fivePVC differs')
            vm = kube('virtualmachine', target['vm']['name'], h['NS'])
            require(vm['metadata']['uid'] == target['vm']['uid'] and vm['metadata']['labels'].get('vm.ani.io/vm-id') == VMID and vm['metadata']['labels'].get('ani.io/tenant-id') == h['TENANT'] and vm['spec']['template']['spec']['domain']['firmware']['uuid'] == VMID and not vm['metadata'].get('deletionTimestamp'), 'exact new ISO VM business/firmware/native identity differs')
            initial_root = next(d for d in target['domain_disks'] if d.get('serial'))
            disks = vm['spec']['template']['spec']['domain']['devices']['disks']
            require(sum(d.get('serial') == initial_root['serial'] and d['name'] == initial_root['name'] for d in disks) == 1, 'exact root logical serial differs')
            for row in volumes:
                dv = kube('datavolume', row['dv']['name'], h['NS']);pvc = kube('pvc', row['pvc']['name'], h['NS']);pv = kube('pv', row['pv']['name'])
                require(dv['metadata']['uid'] == row['dv']['uid'] and owned(dv, 'VirtualMachine', target['vm']['uid']) and pvc['metadata']['uid'] == row['pvc']['uid'] and pvc['status']['phase'] == 'Bound' and owned(pvc, 'DataVolume', row['dv']['uid']) and pv['metadata']['uid'] == row['pv']['uid'] and pv['spec']['claimRef']['uid'] == row['pvc']['uid'] and all(pv['spec']['csi'].get(k) == v for k, v in row['csi'].items()) and all(not x['metadata'].get('deletionTimestamp') for x in [dv, pvc, pv]), 'new root/retained ISO DV/PVC/PV/CSI chain differs')
            return {'windows_vm_uid': w['vm_uid'], 'windows_vmi_uid': w['vmi_uid'], 'windows_pvc_uids': [p['uid'] for p in w['pvc_refs']], 'source_uid': s['pvc_uid'], 'nad_uid': n['NADUID'], 'target_vm_uid': target['vm']['uid'], 'storage': volumes}
        def api(name, method, path, body=None, authenticated=True):
            auth = path in ['/api/v1/auth/password/login', '/api/v1/auth/logout']
            route = {'stop': ('POST', BASE + '/stop'), 'eject': ('DELETE', BASE + '/media/INSTALL'), 'boot': ('PUT', BASE + '/boot'), 'start': ('POST', BASE + '/start')}[intent]
            require(method == 'GET' or (auth and method == 'POST') or (a.phase != 'observe' and (method, path) == route), 'outside exact explicit control scope')
            if not auth and method != 'GET':
                record['business_send_attempts'] += 1;require(record['business_send_attempts'] == 1, 'one original business send only')
            call = {'label': name, 'method': method, 'path': path, 'outcome': 'ATTEMPTING'};record['calls'].append(call);flush()
            headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
            if authenticated:require(token, 'own normal tenant session required');headers['Authorization'] = 'Bearer ' + token
            req = urllib.request.Request('http://127.0.0.1:39180' + path, data=json.dumps(body).encode() if body is not None else None, headers=headers, method=method)
            try:
                try:reply = opener.open(req, timeout=20)
                except urllib.error.HTTPError as error:reply = error
                with reply:raw = reply.read(4 << 20);status = reply.status
                p = private / (name + '.response.json');p.write_bytes(raw);os.chmod(p, 0o600);call.update(http_status=status, outcome='RESPONDED');flush()
                require(status in [200, 201, 202, 204], 'normal API failed; retain original without resend')
                return json.loads(raw) if raw.strip() else {}
            except Exception:call['outcome'] = 'UNKNOWN_OR_FAILED_RETAIN_NO_RESEND';flush();raise
        def observe(command, name):
            require(command.get('operation_id') and command['send_attempts'] == 1, 'known original operation only; unknown ACK cannot be resent/adopted')
            op = api(name, 'GET', BASE + '/operations/' + command['operation_id'])
            require(op['id'] == command['operation_id'] and op['vmId'] == VMID, 'original operation/resource identity differs')
            command['operation'] = op;save(statepath, journal)
            return op
        try:
            flush();record['protected_before'] = guard();flush()
            cred = own_json(R / 'private/gov-candidate/credentials.json')
            require(cred['tenant_code'] == 'vm-mvp-01a168ce', 'only original task tenant login allowed')
            token = api('login', 'POST', '/api/v1/auth/password/login', {'tenant_name': cred['tenant_code'], 'username': cred['tenant_username'], 'password': cred['tenant_password']}, False)['access_token']
            principal = api('principal', 'GET', '/api/v1/admin/me');require(str(principal['tenantId']) == '1', 'only task tenant1 is allowed')
            w = pin['windows'];win = api('windows-product', 'GET', '/api/v1/virtual-machines/' + h['WIN'])
            require(win['id'] == w['vm_id'] == h['WIN'] and str(win['version']) == w['product_version'] and win['lastOperationId'] == w['last_operation_id'] and win['observedState'] == 'Running' and not win['closing'], 'Windows current differs from actual final-hot proof')
            winop = api('windows-final-hot-operation', 'GET', '/api/v1/virtual-machines/' + h['WIN'] + '/operations/' + w['last_operation_id'])
            require(winop['id'] == w['last_operation_id'] and winop['vmId'] == w['vm_id'] and winop['action'] == 'attach-disk' and winop['state'] == 'succeeded' and str(winop['intentVersion']) == w['product_version'], 'original final Windows attach operation/version not succeeded')
            if a.phase == 'observe':
                require(intent in journal['commands'], 'existing original command required')
                op = observe(journal['commands'][intent], 'original-operation')
                current = api('current', 'GET', BASE)
                require(current['id'] == VMID and current['name'] == TASK, 'exact new ISO product identity differs')
                record.update(result='PASS_ORIGINAL_CONTROL_OBSERVATION', operation={k: op.get(k) for k in ['id', 'vmId', 'action', 'intentVersion', 'state', 'reason']})
            else:
                require(intent not in journal['commands'], 'intent already frozen/sent; observe original operation without replay')
                index = ORDER.index(intent)
                require(set(journal['commands']) == set(ORDER[:index]), 'strict Stop then Eject then Boot then Start sequence required')
                if index:
                    prior = journal['commands'][ORDER[index - 1]];op = observe(prior, 'prior-original-operation')
                    require(op['state'] == 'succeeded', 'prior original operation must succeed before next control')
                current = api('current-before', 'GET', BASE)
                require(current['id'] == VMID and current['name'] == TASK and not current['closing'] and int(current['version']) > 0 and current['observedState'] == ('Running' if intent == 'stop' else 'Stopped'), 'exact new ISO state/version not ready for this control')
                require(current['lastOperationId'] == (CREATE_OP if index == 0 else prior['operation_id']), 'unexpected intervening VM intent; no control sent')
                data = {'idempotencyKey': str(uuid.uuid4()), 'expectedVersion': str(current['version'])}
                method, route = 'POST', BASE + '/' + intent
                if intent == 'eject':method, route = 'DELETE', BASE + '/media/INSTALL';data['slot'] = 'INSTALL'
                if intent == 'boot':method, route = 'PUT', BASE + '/boot';data['bootDevice'] = 'SYSTEM_DISK'
                command = {'intent': intent, 'method': method, 'route': route, 'body': {'data': data}, 'send_attempts': 0, 'before_version': str(current['version'])}
                journal['commands'][intent] = command;save(statepath, journal)
                command.update(send_attempts=1, outcome='ACK_UNKNOWN');save(statepath, journal)
                ack = api('control', method, route, command['body'])
                require(ack.get('vmId') == VMID and str(uuid.UUID(ack['id'])) == ack['id'], 'original control ACK invalid; no replay')
                command.update(operation_id=ack['id'], outcome='ACK_PERSISTED');save(statepath, journal)
                record.update(result='PASS_ORIGINAL_CONTROL_ACK_ONLY', operation_id=ack['id'], expected_version=data['expectedVersion'])
            record['current'] = {k: current.get(k) for k in ['id', 'name', 'version', 'lastOperationId', 'observedState', 'closing']}
            require(guard() == record['protected_before'], 'protected original identities changed')
        except Exception as error:
            record.update(result='STOPPED_RETAIN_ORIGINAL_NO_RESEND', error_class=type(error).__name__, message=str(error)[:300] if isinstance(error, RuntimeError) else 'bounded exact phase failed; private journal retained')
        finally:
            if token or any(True for _ in jar):
                try:
                    logout = api('logout', 'POST', '/api/v1/auth/logout', {'application': 'console'}, False)
                    require(logout.get('session_revoked', logout.get('sessionRevoked')) is True, 'own normal session revoke unconfirmed')
                    record['session_revoked'] = True;(private / 'login.response.json').unlink()
                except Exception:record.update(result='STOPPED_SESSION_REVOKE_UNCONFIRMED', session_revoked=False)
            jar.clear();token = None;record['finished_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat();record['heavy_lock_released'] = True
            flush();fcntl.flock(lock, fcntl.LOCK_UN)
    print(json.dumps({'result': record['result'], 'receipt': str(out), 'receipt_sha256': sha(out), 'heavy_lock_released': True}), flush=True)
    return 0 if record['result'].startswith('PASS_') else 1


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--phase', choices=ORDER + ['observe'], required=True)
    p.add_argument('--intent', choices=ORDER)
    p.add_argument('--label', default='01')
    p.add_argument('--execute-authorized', action='store_true', required=True)
    for name in ['runner-sha256', 'native-sha256', 'root-release-sha256']:p.add_argument('--' + name, required=True)
    for name in ['native-receipt', 'root-release']:p.add_argument('--' + name, type=Path, required=True)
    raise SystemExit(main(p.parse_args()))
