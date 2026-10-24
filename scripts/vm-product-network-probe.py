#!/usr/bin/env python3
"""Fedora-only, exact-UID product guest network observation and owned HTTP target."""
import argparse
import datetime
import fcntl
import hashlib
import http.server
import io
import json
import os
import pathlib
import secrets
import shlex
import signal
import subprocess
import sys
import time

RUN = pathlib.Path('/home/chabking/workspace/ani-resource-service-runs/vm-mvp-20261023-01a168ce')
OUT = RUN / 'evidence/product-guest'
HTTP_RECEIPT = OUT / 'http-target.json'
ADDRESS, PORT = '172.16.101.31', 39190
VM_ID = 'e1b3403b-178c-40c2-a4d5-53a37756039c'
VM_UID = 'fe9272cb-858e-4290-9c31-94876b259028'
VMI_UID = 'f4587484-ad34-4822-9342-eee625d2e772'
NS = 'tenant-b3c946b5-7eb3-4feb-9f82-79712062ca5c'
NAME = 'vm-' + VM_ID
MARKER = 'vm-mvp-01a168ce-linux-v2-browser'
K = ['/home/chabking/ani-four-service-deploy-20261012/tools/kubectl', '--kubeconfig=/home/chabking/ani-four-service-deploy-20261012/private/kubeconfig', '--request-timeout=15s']
VIRTCTL = RUN / 'tools/virtctl-v1.9.0-linux-amd64'


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + '\n')


def start_ticks(pid):
    return pathlib.Path('/proc/' + str(pid) + '/stat').read_text().split(') ', 1)[1].split()[19]


def serve():
    token = secrets.token_hex(12)
    path, body = '/vm-mvp-01a168ce-linux-v2/' + token, 'ANI_VM_HTTP_v2_01a168ce_' + token + '\n'
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            matched = self.path == path
            payload = body.encode() if matched else b'not found\n'
            self.send_response(200 if matched else 404)
            self.send_header('Content-Type', 'text/plain')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            print(json.dumps({'at': now(), 'peer': self.client_address[0], 'path': self.path, 'matched': matched, 'status': 200 if matched else 404}), flush=True)
        def log_message(self, fmt, *args):
            pass
    server = http.server.ThreadingHTTPServer((ADDRESS, PORT), Handler)
    save(HTTP_RECEIPT, {'at': now(), 'pid': os.getpid(), 'start_ticks': start_ticks(os.getpid()), 'bind': ADDRESS, 'port': PORT, 'path': path, 'body': body, 'body_sha256': hashlib.sha256(body.encode()).hexdigest(), 'script': str(pathlib.Path(__file__).resolve()), 'script_sha256': hashlib.file_digest(pathlib.Path(__file__).open('rb'), 'sha256').hexdigest(), 'access_log': str(OUT / 'http-access.jsonl'), 'scope': 'task-owned exact-path text response only; no filesystem serving'})
    server.serve_forever()


def target():
    if HTTP_RECEIPT.exists():
        old = json.loads(HTTP_RECEIPT.read_text())
        try:
            if start_ticks(old['pid']) == old['start_ticks']:
                return old
        except FileNotFoundError:
            pass
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / 'http-access.jsonl').open('a') as log:
        child = subprocess.Popen([sys.executable, str(pathlib.Path(__file__).resolve()), 'serve'], stdout=log, stderr=log, start_new_session=True)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if child.poll() is not None:
            raise RuntimeError('owned HTTP target could not bind; see task access log')
        if HTTP_RECEIPT.exists():
            candidate = json.loads(HTTP_RECEIPT.read_text())
            if candidate['pid'] == child.pid:
                return candidate
        time.sleep(.1)
    raise RuntimeError('owned HTTP target did not report readiness')


def stop():
    receipt = json.loads(HTTP_RECEIPT.read_text())
    pid = receipt['pid']
    try:
        if start_ticks(pid) != receipt['start_ticks']:
            raise RuntimeError('HTTP PID was reused; refuse to signal')
        command = pathlib.Path('/proc/' + str(pid) + '/cmdline').read_bytes().split(b'\0')
        if receipt['script'].encode() not in command or b'serve' not in command:
            raise RuntimeError('HTTP process identity differs; refuse to signal')
        os.kill(pid, signal.SIGTERM)
        receipt['stopped_at'] = now()
        receipt['stop_signal'] = 'SIGTERM exact PID/start ticks/command'
    except FileNotFoundError:
        receipt['already_absent_at'] = now()
    save(OUT / 'http-cleanup.json', receipt)
    print(json.dumps({'http_cleanup': str(OUT / 'http-cleanup.json'), 'pid': pid}))


def get(kind, name, namespace=''):
    args = K + ['get', kind, name] + (['-n', namespace] if namespace else []) + ['-o', 'json']
    return json.loads(subprocess.run(args, check=True, capture_output=True, text=True, timeout=20).stdout)


def probe():
    import pexpect
    if get('ns', 'kube-system')['metadata']['uid'] != '5277649d-e28d-4a0a-ae76-8354365c63bc':
        raise RuntimeError('wrong cluster')
    vm, vmi = get('vm', NAME, NS), get('vmi', NAME, NS)
    if vm['metadata']['uid'] != VM_UID or vmi['metadata']['uid'] != VMI_UID or vmi.get('status', {}).get('phase') != 'Running':
        raise RuntimeError('authorized product VM/VMI window changed')
    if not any(x.get('uid') == VM_UID and x.get('kind') == 'VirtualMachine' for x in vmi['metadata'].get('ownerReferences', [])):
        raise RuntimeError('product VMI owner differs')
    if hashlib.file_digest(VIRTCTL.open('rb'), 'sha256').hexdigest() != '40ede2ee37c98a1aeed71c9c219616a05247ce2be109e1edddf0477572e8b978':
        raise RuntimeError('official virtctl changed')
    dns_ip = get('svc', 'kubernetes', 'default')['spec']['clusterIP']
    http = target()
    url = 'http://' + ADDRESS + ':' + str(PORT) + http['path']
    credentials = json.loads((RUN / 'private/gov-candidate/linux-guest-credentials-v2.json').read_text())
    username, password = credentials['username'], credentials['password']
    if username != 'aniuser': raise RuntimeError('unexpected product user')
    transcript = io.StringIO()
    child = pexpect.spawn(str(VIRTCTL), ['console', NAME, '--namespace', NS, '--kubeconfig', K[1].split('=', 1)[1], '--timeout=20'], encoding='utf-8', timeout=40)
    child.logfile_read = transcript
    result = {'at': now(), 'layer': 'GUEST', 'scope': 'authorized real product network probes and existing browser marker readback', 'vm_id': VM_ID, 'vm_uid': VM_UID, 'vmi_uid': VMI_UID, 'http_target_reference': str(HTTP_RECEIPT), 'expected_dns_service_ip': dns_ip, 'driver_sha256': hashlib.file_digest(pathlib.Path(__file__).open('rb'), 'sha256').hexdigest()}
    try:
        child.expect(r'Successfully connected to .* console\. Press Ctrl\+\] or Ctrl\+5 to exit console\.')
        child.sendline('')
        if child.expect([r'login:', r'\$ ']) == 1:
            child.sendline('exit'); child.expect(r'login:')
        child.sendline(username); child.expect(r'[Pp]assword:'); child.sendline(password); child.expect(r'\$ ')
        code = '''import subprocess,json,pathlib,getpass,hashlib
def run(argv):
    try:
        p = subprocess.run(argv, capture_output=True, timeout=18)
        return {"argv":argv,"exit":p.returncode,"stdout":p.stdout.decode(errors="replace")[:8192],"stderr":p.stderr.decode(errors="replace")[:2048],"body_sha256":hashlib.sha256(p.stdout).hexdigest()}
    except (OSError,subprocess.TimeoutExpired) as e:
        return {"argv":argv,"error":str(e)}
marker=pathlib.Path("/home/aniuser/vm-mvp-marker")
print("ANI_NETWORK_RESULT="+json.dumps({"username":getpass.getuser(),"marker":marker.read_text().strip() if marker.is_file() and not marker.is_symlink() and marker.stat().st_size<=4096 else None,"boot_id":pathlib.Path("/proc/sys/kernel/random/boot_id").read_text().strip(),"address":run(["ip","-j","address"]),"routes":run(["ip","-j","route"]),"target_route":run(["ip","-j","route","get","172.16.101.31"]),"dns_getent":run(["getent","ahostsv4","kubernetes.default.svc.cluster.local"]),"dns_resolvectl":run(["resolvectl","query","kubernetes.default.svc.cluster.local"]),"dns_uplink":run(["resolvectl","dns"]),"http":run(["curl","--fail","--show-error","--silent","--connect-timeout","5","--max-time","12",HTTP_URL]),"https_egress":run(["curl","--fail","--show-error","--silent","--connect-timeout","5","--max-time","12","https://example.com/"]),"ssh_units":run(["systemctl","show","ssh.socket","ssh.service","-p","Id","-p","ActiveState","-p","SubState"]),"ssh_listen":run(["ss","-ltn","sport = :22"]),"ssh_runtime_directory_exists":pathlib.Path("/run/sshd").is_dir()}))'''.replace('HTTP_URL', repr(url))
        child.sendline('python3 -c ' + shlex.quote(code))
        child.expect(r'(?:^|[\r\n])ANI_NETWORK_RESULT=(\{[^\r\n]+\})\r?\n', timeout=90)
        observed = json.loads(child.match.group(1))
        child.expect(r'\$ ')
        child.sendline('exit'); child.expect(r'login:')
        result['observed'] = observed
        result['marker'] = 'PASS' if observed['marker'] == MARKER else 'FAIL'
        result['dns'] = 'PASS' if observed['dns_getent'].get('exit') == 0 and dns_ip in observed['dns_getent']['stdout'].split() and observed['dns_resolvectl'].get('exit') == 0 and dns_ip in observed['dns_resolvectl']['stdout'] else 'FAIL'
        result['http'] = 'PASS' if observed['http'].get('exit') == 0 and observed['http']['stdout'] == http['body'] and observed['http']['body_sha256'] == http['body_sha256'] else 'FAIL'
        result['https_egress'] = 'PASS' if observed['https_egress'].get('exit') == 0 and observed['https_egress'].get('stdout') else 'FAIL'
        result['result'] = 'PASS' if all(result[x] == 'PASS' for x in ('marker', 'dns', 'http', 'https_egress')) else 'PARTIAL_FAIL'
    except Exception as error:
        result['result'] = 'FAIL'
        result['error'] = str(error).replace(password, '[REDACTED]')[-2000:]
    finally:
        child.close(force=True)
        stamp = str(int(time.time()))
        save(OUT / ('network-' + stamp + '.json'), result)
        (OUT / ('network-console-' + stamp + '.txt')).write_text(transcript.getvalue().replace(password, '[REDACTED]'))
    print(json.dumps({k:v for k,v in result.items() if k != 'observed'}))
    return 0 if result['result'] == 'PASS' else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['probe', 'serve', 'stop'])
    args = parser.parse_args()
    if args.action == 'serve': return serve()
    with open('/home/chabking/workspace/ani-network-service-runs/net05a-heavy.lock', 'a') as lock:
        deadline = time.monotonic() + 10
        while True:
            try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB); break
            except BlockingIOError:
                if time.monotonic() >= deadline: print('LOCK_BUSY'); return 73
                time.sleep(.25)
        return probe() if args.action == 'probe' else stop()


if __name__ == '__main__':
    raise SystemExit(main())
