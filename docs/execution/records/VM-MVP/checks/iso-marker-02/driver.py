"""One new Fedora44 INSTALL_ISO request; prepare/create/observe/native are separate.

Fedora only, one existing heavy-lock window per explicit phase. No installation,
Guest action, PG, new preset/media, Windows mutation, or unknown-ACK replay.
"""
from pathlib import Path
import argparse
import datetime
import fcntl
import hashlib
import http.cookiejar
import json
import os
import runpy
import stat
import subprocess
import urllib.error
import urllib.request
import uuid

R = Path('/home/chabking/workspace/ani-resource-service-runs/vm-mvp-20261023-01a168ce')
D = R / 'iso-marker-02'
TASK = 'vm-mvp-fedora44-iso-marker-02-01a168ce'
BASE = '/api/v1/virtual-machines'
HELPER = R / 'v10-network-positive-02/inventory.py'
BASELINE = R / 'v10-network-positive-02/inventory.json'
HELPER_SHA = '6219b84ddda56dfed27d683c0d95e0bb68bc3772e8be1a6e8d7c057f9f718a45'
BASELINE_SHA = '7a3af5683325fa950b5162ad78f6c6a040a374f0357691a999b786396c48cf46'
PRESET = '27165f6b-5ba3-448d-a6c5-e808da16206a'
SOURCE = {'media_key': 'fedora-44-iso', 'namespace': 'vm-mvp-probe-01a168ce',
          'pvc_name': 'vm-mvp-fedora44-http-source',
          'pvc_uid': '44e1ab85-5e0e-4fc7-b23b-94760d1b3f00',
          'dv_uid': '739fbaee-e8ed-4162-861f-34617ad9856a',
          'pv_name': 'pvc-4ccc186a-5569-4d78-b849-63714f636df1',
          'pv_uid': '16713980-2ae0-40d7-922e-a92f069ec089',
          'checksum': '75748d952733951fd4e9754b854197aa574c361521e7c1b75e15bd3acc90d912'}
LOCK = '/home/chabking/workspace/ani-network-service-runs/net05a-heavy.lock'


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def save(path, value):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(value, f, indent=2);f.write('\n');f.flush();os.fsync(f.fileno())


def own_private(path):
    m = path.lstat()
    require(stat.S_ISREG(m.st_mode) and m.st_uid == os.getuid() and stat.S_IMODE(m.st_mode) == 0o600,
            'fixed own regular0600 private reference required')
    return json.loads(path.read_text())


def main(a):
    require(a.execute_authorized and os.uname().nodename == 'fedora' and Path(__file__).resolve().parent == D and
            sha(__file__) == a.runner_sha256, 'fixed Fedora phase/source required')
    require(sha(HELPER) == HELPER_SHA and sha(BASELINE) == BASELINE_SHA, 'original read-only helpers/baseline changed')
    h = runpy.run_path(str(HELPER));old = json.loads(BASELINE.read_text())
    label = a.phase + '-' + a.label
    require(all(c.isalnum() or c == '-' for c in label) and len(label) < 70, 'bounded unique phase label required')
    out = D / (label + '.json');private = D / 'private' / label
    require(not out.exists() and not private.exists(), 'retain original phase evidence; no implicit repeat')
    frozen = D / 'frozen-request.json';statepath = D / 'private/state.json'
    if a.phase != 'prepare':
        require(a.prepared_sha256 and sha(frozen) == a.prepared_sha256, 'Root-reviewed frozen request SHA required')
        prepared = json.loads((D / 'prepare-01.json').read_text())
        require(prepared['result'] == 'PASS_PREPARE_NO_CREATE' and prepared.get('session_revoked') is True,
                'original prepare/logout must pass before further phase')
    os.umask(0o077)
    with open(LOCK) as lock:
        try:fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({'result': 'LOCK_BUSY', 'api_calls': 0}));return 73
        private.mkdir(mode=0o700, parents=True)
        record = {'task_name': TASK, 'phase': a.phase, 'stage': 'physical-identity',
                  'runner_sha256': a.runner_sha256, 'started_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  'business_send_attempts': 0, 'cluster_writes': 0, 'PG_calls': 0, 'Guest_actions': 0,
                  'Windows_mutations': 0, 'preset_or_media_writes': 0, 'calls': [],
                  'credential_values_or_value_hashes_public': False}
        token = None;jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(jar))
        def flush():save(out, record)
        def kube(kind, name=None, ns=None, labels=None):
            args = h['K'] + ['get', kind] + ([name] if isinstance(name, str) else (name or []))
            if ns:args += ['-n', ns]
            if labels:args += ['-l', labels]
            p = subprocess.run(args + ['--ignore-not-found', '-o', 'json'], capture_output=True, timeout=15)
            require(p.returncode == 0, 'bounded exact native read failed')
            return json.loads(p.stdout) if p.stdout.strip() else None
        def api(name, method, path, body=None, authenticated=True):
            auth = path in ['/api/v1/auth/password/login', '/api/v1/auth/logout']
            require(method == 'GET' or (method == 'POST' and auth) or
                    (a.phase == 'create' and method == 'POST' and path == BASE), 'outside explicit phase API scope')
            if not auth and method != 'GET':
                record['business_send_attempts'] += 1
                require(record['business_send_attempts'] == 1, 'one original CREATE attempt only')
            call = {'label': name, 'method': method, 'path': path, 'send_attempts': 1, 'outcome': 'ATTEMPTING'}
            record['calls'].append(call);flush()
            headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
            if authenticated:
                require(token, 'own normal tenant session required');headers['Authorization'] = 'Bearer ' + token
            req = urllib.request.Request('http://127.0.0.1:39180' + path,
                data=json.dumps(body).encode() if body is not None else None, headers=headers, method=method)
            try:
                try:response = opener.open(req, timeout=20)
                except urllib.error.HTTPError as error:response = error
                with response:raw = response.read(4 << 20);status = response.status
                p = private / (name + '.response.json');p.write_bytes(raw);os.chmod(p, 0o600)
                call.update(http_status=status, outcome='RESPONDED');flush()
                require(status in [200, 201, 202, 204], 'normal tenant API failed; retain original intent without resend')
                return json.loads(raw) if raw.strip() else {}
            except Exception:
                call['outcome'] = 'FAILED_OR_UNKNOWN_RETAIN_NO_RESEND';flush();raise
        def protect():
            require(kube('namespace', 'kube-system')['metadata']['uid'] == h['CLUSTER'] and
                    kube('namespace', h['NS'])['metadata']['uid'] == h['NSUID'], 'original cluster/namespace differs')
            gp, _ = h['process'](35161, 74249534, old['task_processes']['governance']['binary_sha256'])
            rp, env = h['process'](3881653, 72749000, old['task_processes']['resource']['binary_sha256'])
            require(gp == old['task_processes']['governance'] and rp == old['task_processes']['resource'],
                    'fixed task PID/start/binary/argv/listeners differ')
            cfg = json.loads(Path(env['ANI_VM_CONFIG_FILE']).read_text())
            networks = [n for n in cfg['Networks'] if n['TenantID'] == h['TENANT'] and
                        n['VPCID'] == h['VPC'] and n['SubnetID'] == h['SUBNET']]
            require(networks == [old['network']], 'original task network mapping differs')
            n = old['network'];nad = kube('network-attachment-definitions.k8s.cni.cncf.io', n['NADName'], h['NS'])
            require(nad['metadata']['uid'] == n['NADUID'] and digest(json.loads(nad['spec']['config'])) == n['NADConfigDigest'], 'original NAD differs')
            w = old['windows'];vm = kube('virtualmachine', 'vm-' + w['vm_id'], h['NS'])
            vmi = kube('virtualmachineinstance', 'vm-' + w['vm_id'], h['NS'])
            require(vm['metadata']['uid'] == w['vm_uid'] and digest(vm['spec']) == w['vm_spec_sha256'] and
                    vmi['metadata']['uid'] == w['vmi_uid'] and vmi['status']['phase'] == 'Running' and
                    not vm['metadata'].get('deletionTimestamp') and not vmi['metadata'].get('deletionTimestamp'), 'Windows18 identity/spec differs')
            pvcs = kube('pvc', [p['name'] for p in w['pvc_refs']], h['NS'])['items']
            require({p['metadata']['name']: p['metadata']['uid'] for p in pvcs} ==
                    {p['name']: p['uid'] for p in w['pvc_refs']} and all(not p['metadata'].get('deletionTimestamp') for p in pvcs), 'Windows five PVC identities differ')
            source = [m for m in cfg['Service']['Media'] if m['media_key'] == SOURCE['media_key']]
            require(len(source) == 1 and all(source[0][k] == SOURCE[k] for k in ['media_key', 'namespace', 'pvc_name', 'pvc_uid', 'checksum']), 'fixed Fedora44 source configuration differs')
            pvc = kube('pvc', SOURCE['pvc_name'], SOURCE['namespace']);dv = kube('datavolume', SOURCE['pvc_name'], SOURCE['namespace'])
            pv = kube('pv', SOURCE['pv_name'])
            require(pvc['metadata']['uid'] == SOURCE['pvc_uid'] and pvc['status']['phase'] == 'Bound' and
                    pvc['metadata']['annotations']['vm.ani.io/source-sha256'] == SOURCE['checksum'] and
                    dv['metadata']['uid'] == SOURCE['dv_uid'] and dv['status']['phase'] == 'Succeeded' and
                    pv['metadata']['uid'] == SOURCE['pv_uid'] and pvc['spec']['volumeName'] == SOURCE['pv_name'] and
                    pv['spec']['claimRef']['uid'] == SOURCE['pvc_uid'] and
                    all(not x['metadata'].get('deletionTimestamp') for x in [pvc, dv, pv]), 'Fedora44 source UID/checksum/binding differs')
            return {'task_processes': {'governance': gp, 'resource': rp}, 'windows': w,
                    'source': SOURCE, 'network': n, 'namespace_uid': h['NSUID']}
        try:
            flush();before = protect();record['protected_before'] = before;flush()
            cred = own_private(R / 'private/gov-candidate/credentials.json')
            require(cred['tenant_code'] == old['gov_tenant_code'], 'original task tenant credential reference differs')
            token = api('login', 'POST', '/api/v1/auth/password/login', {'tenant_name': cred['tenant_code'],
                        'username': cred['tenant_username'], 'password': cred['tenant_password']}, False)['access_token']
            principal = api('principal', 'GET', '/api/v1/admin/me')
            require(str(principal['tenantId']) == '1', 'only original task tenant1 is allowed')
            w = api('windows-product', 'GET', BASE + '/' + h['WIN'])
            require(str(w['version']) == '18' and w['observedState'] == 'Running' and w['closing'] is False, 'Windows product18 differs')
            if a.phase == 'prepare':
                require(a.label == '01' and not frozen.exists() and not statepath.exists(), 'one fresh independent prepare only')
                nodes = kube('nodes', list(h['NODES']))['items'];pods = kube('pods', ['-A'])['items']
                require({n['metadata']['name']: (n['metadata']['uid'], next(x['address'] for x in n['status']['addresses'] if x['type'] == 'InternalIP')) for n in nodes} == h['NODES'], 'three node identities differ')
                capacity = {}
                for node in nodes:
                    name = node['metadata']['name'];active = [p for p in pods if p['spec'].get('nodeName') == name and p.get('status', {}).get('phase') not in ['Succeeded', 'Failed']]
                    req = [h['requests'](p) for p in active];alloc = node['status']['allocatable']
                    capacity[name] = {'uid': node['metadata']['uid'], 'ready': any(c['type'] == 'Ready' and c['status'] == 'True' for c in node['status']['conditions']),
                        'unschedulable': node['spec'].get('unschedulable', False), 'taints': node['spec'].get('taints', []),
                        'kvm': alloc.get('devices.kubevirt.io/kvm', '0'), 'nonterminal_pods': len(active),
                        'unrequested_cpu_millis': h['quantity'](alloc['cpu'], True) - sum(q['cpu'] for q in req),
                        'unrequested_memory_bytes': h['quantity'](alloc['memory']) - sum(q['memory'] for q in req)}
                record['node_capacity'] = capacity
                require(all(n['ready'] for n in capacity.values()) and any(not n['unschedulable'] and not n['taints'] and h['quantity'](n['kvm']) > 0 and n['unrequested_cpu_millis'] >= 2000 and n['unrequested_memory_bytes'] >= 5 * 2**30 for n in capacity.values()), 'ordinary scheduler capacity needs Root review; no create')
                presets = api('presets', 'GET', '/api/v1/compute-presets?usage=VIRTUAL_MACHINE&compute_type=cpu&page=1&pageSize=100')['items']
                chosen = [p for p in presets if p['id'] == PRESET]
                require(len(chosen) == 1 and chosen[0]['selectable'] is True and chosen[0]['enabled'] is True and chosen[0]['compute_type'] == 'cpu' and int(chosen[0]['cpu_millis']) == 2000 and int(chosen[0]['memory_mib']) == 4096, 'fixed existing CPU preset is not selectable')
                media = api('media', 'GET', '/api/v1/virtual-machine-media')['items']
                iso = [m for m in media if m.get('mediaKey') == SOURCE['media_key']]
                require(len(iso) == 1 and iso[0]['kind'] == 'INSTALL_ISO' and iso[0]['osFamily'] == 'linux' and iso[0]['architecture'] == 'amd64', 'fixed Fedora44 ISO directory unavailable')
                items = []
                for page in range(1, 6):
                    listed = api('vm-list-' + str(page), 'GET', BASE + '?page=' + str(page) + '&pageSize=100')
                    items.extend(listed.get('items', []))
                    if len(items) >= int(listed['total']):break
                    require(listed.get('items'), 'normal VM list incomplete')
                else:raise RuntimeError('bounded own VM inventory exceeded')
                record['current_vms'] = [{k: v[k] for k in ['id', 'name', 'version', 'observedState', 'closing'] if k in v} for v in items]
                require(not any(v['name'] == TASK for v in items), 'new unique name already exists; never adopt another VM')
                body = {'data': {'idempotencyKey': str(uuid.uuid4()), 'name': TASK, 'presetId': PRESET,
                    'sourceKind': 'INSTALL_ISO', 'mediaKey': SOURCE['media_key'], 'systemDiskSizeGb': '48',
                    'dataDisks': [], 'startAfterCreate': True, 'network': {'vpcId': h['VPC'], 'subnetId': h['SUBNET']}}}
                plan = {'task_name': TASK, 'cluster_uid': h['CLUSTER'], 'resource_tenant_id': h['TENANT'],
                        'namespace': {'name': h['NS'], 'uid': h['NSUID']}, 'source': SOURCE,
                        'preset': chosen[0], 'media': iso[0], 'body': body, 'protected': before}
                save(frozen, plan);save(statepath, {'task_name': TASK, 'body': body, 'send_attempts': 0})
                record.update(result='PASS_PREPARE_NO_CREATE', frozen_request=str(frozen), frozen_sha256=sha(frozen),
                              preset_id=PRESET, media_key=SOURCE['media_key'], root_gb=48, data_disks=0, start_after_create=True,
                              guest_initialization='OMITTED', manual_install='NOT_RUN')
            else:
                plan = json.loads(frozen.read_text());state = own_private(statepath)
                require(plan['task_name'] == state['task_name'] == TASK and state['body'] == plan['body'] and before == plan['protected'], 'frozen request/protection differs')
                if a.phase == 'create':
                    require(state['send_attempts'] == 0 and not state.get('operation_id'), 'CREATE already sent or unknown; never resend')
                    state.update(send_attempts=1, outcome='ACK_UNKNOWN');save(statepath, state)
                    ack = api('create', 'POST', BASE, state['body'])
                    require(str(uuid.UUID(ack['resourceId'])) == ack['resourceId'] and str(uuid.UUID(ack['operationId'])) == ack['operationId'], 'original CREATE ACK IDs invalid; no replay')
                    state.update(vm_id=ack['resourceId'], operation_id=ack['operationId'], outcome='ACK_PERSISTED');save(statepath, state)
                    record.update(result='PASS_ORIGINAL_CREATE_ACK_ONLY', vm_id=state['vm_id'], operation_id=state['operation_id'])
                else:
                    require(state.get('operation_id') and state.get('vm_id') and state['send_attempts'] == 1, 'known original operation required; no adoption/replay')
                    vmid = state['vm_id'];operation = api('operation', 'GET', BASE + '/' + vmid + '/operations/' + state['operation_id'])
                    current = api('current', 'GET', BASE + '/' + vmid)
                    require(operation['id'] == state['operation_id'] and operation['vmId'] == vmid and current['id'] == vmid, 'original operation/resource IDs differ')
                    record.update(vm_id=vmid, operation_id=state['operation_id'], operation={k: operation.get(k) for k in ['id', 'vmId', 'action', 'state', 'reason']},
                                  current={k: current.get(k) for k in ['id', 'name', 'version', 'observedState', 'closing', 'lastOperationId']})
                    if a.phase == 'native':
                        require(operation['state'] == 'succeeded' and current['name'] == TASK and not current['closing'], 'original create must succeed before native freeze')
                        vm = kube('virtualmachine', 'vm-' + vmid, h['NS']);vmi = kube('virtualmachineinstance', 'vm-' + vmid, h['NS'])
                        require(vm['metadata']['labels'].get('vm.ani.io/vm-id') == vmid and vm['metadata']['labels'].get('ani.io/tenant-id') == h['TENANT'] and vm['spec']['template']['spec']['domain']['firmware']['uuid'] == vmid, 'exact new native VM owner/business mapping differs')
                        require(vmi and vmi['spec']['domain']['firmware']['uuid'] == vmid and any(o['uid'] == vm['metadata']['uid'] and o['kind'] == 'VirtualMachine' for o in vmi['metadata']['ownerReferences']), 'exact VMI owner/firmware differs')
                        rows = []
                        for volume in vm['spec']['template']['spec']['volumes']:
                            if 'dataVolume' in volume:
                                name = volume['dataVolume']['name'];dv = kube('datavolume', name, h['NS']);pvc = kube('pvc', name, h['NS'])
                                require(dv['spec']['source'] == {'blank': {}} and pvc['spec'].get('volumeMode') == 'Block', 'new system disk must be blank Block')
                                role = 'root'
                            else:
                                require('persistentVolumeClaim' in volume, 'only root DV plus owned ISO PVC expected')
                                pvc = kube('pvc', volume['persistentVolumeClaim']['claimName'], h['NS'])
                                owner = [o for o in pvc['metadata'].get('ownerReferences', []) if o['kind'] == 'DataVolume' and o.get('controller')]
                                require(len(owner) == 1, 'new installer PVC must have one CDI owner')
                                name = owner[0]['name'];dv = kube('datavolume', name, h['NS'])
                                require(dv['metadata']['uid'] == owner[0]['uid'] and dv['spec']['source']['pvc'] == {'namespace': SOURCE['namespace'], 'name': SOURCE['pvc_name']}, 'dedicated ISO clone source/UID differs')
                                role = 'installer'
                            require(any(o['kind'] == 'VirtualMachine' and o['uid'] == vm['metadata']['uid'] and o.get('controller') for o in dv['metadata']['ownerReferences']) and pvc['status']['phase'] == 'Bound' and any(o['kind'] == 'DataVolume' and o['uid'] == dv['metadata']['uid'] for o in pvc['metadata'].get('ownerReferences', [])), 'VM-owned ISO/root DV/PVC chain differs')
                            pv = kube('pv', pvc['spec']['volumeName']);require(pv['spec']['claimRef']['uid'] == pvc['metadata']['uid'], 'new root/ISO PV claim differs')
                            rows.append({'role': role, 'volume': volume['name'], 'dv': {'name': name, 'uid': dv['metadata']['uid']}, 'pvc': {'name': pvc['metadata']['name'], 'uid': pvc['metadata']['uid']}, 'pv': {'name': pv['metadata']['name'], 'uid': pv['metadata']['uid']}, 'csi': {k: pv['spec'].get('csi', {}).get(k) for k in ['driver', 'volumeHandle']}})
                        require(len(rows) == 2 and {r['role'] for r in rows} == {'root', 'installer'}, 'exactly blank root plus owned installer ISO expected')
                        record['native'] = {'vm': {'name': vm['metadata']['name'], 'uid': vm['metadata']['uid']}, 'vmi': {'name': vmi['metadata']['name'], 'uid': vmi['metadata']['uid'], 'phase': vmi['status']['phase'], 'node': vmi['status'].get('nodeName')}, 'volumes': rows, 'domain_disks': vm['spec']['template']['spec']['domain']['devices']['disks']}
                    record['result'] = 'PASS_ORIGINAL_OPERATION_OBSERVATION' if a.phase == 'observe' else 'PASS_EXACT_NEW_ISO_NATIVE_IDENTITY'
            require(protect() == before, 'protected existing Task/Windows/source identities changed')
        except Exception as error:
            record.update(result='STOPPED_RETAIN_ORIGINAL_NO_RESEND', error_class=type(error).__name__,
                          message=str(error)[:300] if isinstance(error, RuntimeError) else 'bounded exact phase failed; private original retained')
        finally:
            if token or any(True for _ in jar):
                try:
                    reply = api('logout', 'POST', '/api/v1/auth/logout', {'application': 'console'}, False)
                    require(reply.get('session_revoked', reply.get('sessionRevoked')) is True, 'normal own session logout unconfirmed')
                    record['session_revoked'] = True
                    (private / 'login.response.json').unlink()
                except Exception:record.update(result='STOPPED_SESSION_REVOKE_UNCONFIRMED', session_revoked=False)
            jar.clear();token = None
            record['finished_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat();record['heavy_lock_released'] = True
            flush();fcntl.flock(lock, fcntl.LOCK_UN)
    print(json.dumps({'result': record['result'], 'receipt': str(out), 'receipt_sha256': sha(out), 'heavy_lock_released': True}), flush=True)
    return 0 if record['result'].startswith('PASS_') else 1


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--phase', choices=['prepare', 'create', 'observe', 'native'], required=True)
    p.add_argument('--label', default='01')
    p.add_argument('--execute-authorized', action='store_true', required=True)
    p.add_argument('--runner-sha256', required=True)
    p.add_argument('--prepared-sha256')
    raise SystemExit(main(p.parse_args()))
