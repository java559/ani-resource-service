#!/usr/bin/env python3
"""Three exact ServiceIP TCP observations; PF metadata preparation only."""
import concurrent.futures
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import time

R = Path('/home/chabking/workspace/ani-resource-service-runs/vm-mvp-20261023-01a168ce')
D = R / 'v24-route-preflight-02'
PLAN = D / 'transport-prepared-02.json'
PLAN_SHA = '3c762c12742886929a6fca72d2a848c3e6e817e73167914d01aefe1926d06475'
K = ['/home/chabking/ani-four-service-deploy-20261012/tools/kubectl',
     '--kubeconfig=/home/chabking/ani-four-service-deploy-20261012/private/kubeconfig', '--request-timeout=3s']

def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def main():
    assert os.environ.get('SSH_CONNECTION', '').split()[2:3] == ['172.16.101.31']
    lock = open('/home/chabking/workspace/ani-network-service-runs/net05a-heavy.lock', 'a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return 73
    D.mkdir(exist_ok=True)
    assert not (D / 'observed.json').exists(), 'single-use observation already exists'
    started = time.monotonic()
    result = {'at': now(), 'host': 'fedora/172.16.101.31', 'plan_sha256': PLAN_SHA,
              'source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'result': 'PREFLIGHT_NOT_COMPLETED', 'commands': [], 'targets': [],
              'tcp_application_bytes_sent': 0, 'tls_or_rpc_requests': 0,
              'secret_reads': 0, 'cluster_or_config_writes': 0, 'gov_terms': 0,
              'port_forwards_started': 0}

    def get(kind, name=None, namespace=None, selector=None):
        args = ['get', kind]
        if name:
            args.append(name)
        if namespace:
            args += ['-n', namespace]
        if selector:
            args += ['-l', selector]
        args += ['-o', 'json']
        before = time.monotonic()
        p = subprocess.run(K + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=4)
        result['commands'].append({'argv': args, 'exit_code': p.returncode,
                                   'elapsed_seconds': round(time.monotonic() - before, 3)})
        assert p.returncode == 0, p.stderr.decode(errors='replace')[:2000]
        return json.loads(p.stdout)

    def projection(value):
        return {key: value['metadata'].get(key) for key in ('namespace', 'name', 'uid', 'resourceVersion')}

    def controller(value, kind, expected_uid=None):
        owners = [x for x in value['metadata'].get('ownerReferences', []) if x.get('controller')]
        assert len(owners) == 1 and owners[0]['kind'] == kind
        if expected_uid:
            assert owners[0]['uid'] == expected_uid
        return owners[0]

    try:
        assert hashlib.sha256(PLAN.read_bytes()).hexdigest() == PLAN_SHA
        plan = json.loads(PLAN.read_text())
        assert get('namespace', 'kube-system')['metadata']['uid'] == plan['cluster_uid'] == '5277649d-e28d-4a0a-ae76-8354365c63bc'
        for target in plan['targets']:
            svc = get('service', target['service'], target['namespace'])
            assert svc['metadata']['uid'] == target['service_uid'] and not svc['metadata'].get('deletionTimestamp')
            assert svc['spec']['clusterIP'] == target['cluster_ip'] and svc['spec'].get('selector') == target['selector']
            ports = [p for p in svc['spec']['ports'] if p['port'] == 19090 and p.get('protocol', 'TCP') == 'TCP']
            assert len(ports) == 1 and target['port'] == 19090
            result['targets'].append({'service': projection(svc), 'cluster_ip': target['cluster_ip'],
                                      'port': 19090, 'service_port': ports[0], 'selector': target['selector']})

        def connect(index):
            target = result['targets'][index]
            address = target['cluster_ip']
            assert address in ('10.96.58.69', '10.96.44.130', '10.96.166.234')
            before = time.monotonic()
            observation = {'started_at': now(), 'connect_calls': 1, 'timeout_seconds': 2,
                           'destination': address + ':19090'}
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
                client.settimeout(2)
                try:
                    client.connect((address, 19090))
                    observation.update(result='CONNECTED', errno=0)
                except OSError as exc:
                    observation.update(result='UNREACHABLE', exception=type(exc).__name__, errno=exc.errno, error=str(exc))
            observation['elapsed_seconds'] = round(time.monotonic() - before, 6)
            return observation

        tcp_started = time.monotonic()
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            observations = list(executor.map(connect, range(3)))
        for target, observed in zip(result['targets'], observations):
            target['tcp'] = observed
        result['tcp_window_seconds'] = round(time.monotonic() - tcp_started, 6)
        result['result'] = 'DIRECT_TCP_ALL_CONNECTED_ROUTE_ONLY' if all(x['result'] == 'CONNECTED' for x in observations) else 'DIRECT_TCP_UNAVAILABLE_PF_PREPARED_NOT_STARTED'

        if any(x['result'] != 'CONNECTED' for x in observations):
            for target, actual in zip(plan['targets'], result['targets']):
                selector = ','.join(key + '=' + value for key, value in sorted(target['selector'].items()))
                pods = get('pods', namespace=target['namespace'], selector=selector)['items']
                ready = [pod for pod in pods if not pod['metadata'].get('deletionTimestamp') and
                         pod.get('status', {}).get('phase') == 'Running' and
                         any(c['type'] == 'Ready' and c['status'] == 'True' for c in pod['status'].get('conditions', []))]
                assert len(ready) == 1, 'expected one Ready Pod for ' + target['service']
                pod = ready[0]
                owner_rs = controller(pod, 'ReplicaSet')
                rs = get('replicaset', owner_rs['name'], target['namespace'])
                assert rs['metadata']['uid'] == owner_rs['uid'] and not rs['metadata'].get('deletionTimestamp')
                owner_deploy = controller(rs, 'Deployment', target['deployment_uid'])
                deploy = get('deployment', owner_deploy['name'], target['namespace'])
                assert deploy['metadata']['uid'] == target['deployment_uid'] and not deploy['metadata'].get('deletionTimestamp')
                assert all(pod['metadata']['labels'].get(k) == v for k, v in target['selector'].items())
                declared = [p for c in pod['spec'].get('containers', []) for p in c.get('ports', []) if p['containerPort'] == 19090]
                assert declared, 'expected Pod grpc containerPort 19090'
                bind_host, bind_port = target['fallback_loopback_address'].split(':')
                assert bind_host == '127.0.0.1' and int(bind_port) in (43941, 43942, 43943)
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                    listener.bind((bind_host, int(bind_port)))
                actual['fallback'] = {'state': 'METADATA_FROZEN_NOT_STARTED', 'pod': projection(pod),
                                      'pod_node': pod['spec']['nodeName'], 'pod_phase': pod['status']['phase'],
                                      'replicaset': projection(rs), 'deployment': projection(deploy),
                                      'bind': target['fallback_loopback_address'], 'port_state': 'FREE_AT_CHECK_ONLY',
                                      'argv': K[:2] + ['-n', target['namespace'], 'port-forward', '--address=127.0.0.1',
                                                      'pod/' + pod['metadata']['name'], bind_port + ':19090'],
                                      'process_identity': 'NOT_CREATED',
                                      'cleanup_if_later_authorized': 'Record owned PID/startticks/exe/full argv/listener inode/PodUID/ServiceUID. TERM only exact own process after Gov restores original address or no client references; verify processGone/listenerGone; identity mismatch stops, no KILL.'}
    except Exception as exc:
        result['result'] = 'PREFLIGHT_STOPPED'
        result['error'] = type(exc).__name__ + ': ' + str(exc)[:3000]
    finally:
        result['finished_at'] = now()
        result['elapsed_seconds'] = round(time.monotonic() - started, 3)
        result['limitations'] = ['TCP connection is transport reachability only, not TLS/identity/RPC/business acceptance.',
                                 'No automatic fallback or port-forward start is authorized or performed.']
        p = D / 'observed.json'
        p.write_text(json.dumps(result, indent=2) + '\n')
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()
    print(json.dumps({'result': result['result'], 'elapsed_seconds': result['elapsed_seconds'],
                      'targets': [{'service': t['service']['name'], 'tcp': t.get('tcp'), 'fallback': t.get('fallback', {}).get('state')} for t in result['targets']],
                      'error': result.get('error')}))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
