from pathlib import Path
import datetime, hashlib, json, os, subprocess

W = Path('/home/chabking/.codex/worktrees/vm-mvp-20261023/ani-resource-service')
D = Path(__file__).parent
U = 'ssh://git@ssh.github.com:443/zhangzhe-ctrl/ani-resource-service.git'
C = json.loads((D / 'committed.json').read_text())
REF = C['ref']
OID = C['commit']
H = D / 'hooks'
env = {**os.environ, 'GIT_OPTIONAL_LOCKS': '0'}

def git(*args):
    return subprocess.run(['git', '-C', str(W), *args], env=env, capture_output=True, timeout=90)

def checked(*args):
    p = git(*args)
    assert p.returncode == 0, p.stderr.decode()
    return p.stdout

assert C['result'] == 'COMMITTED_NOT_PUSHED' and not (D / 'push-attempt.json').exists()
assert checked('rev-parse', REF).decode().strip() == OID
assert checked('rev-parse', OID + '^').decode().strip() == C['parent']
assert checked('rev-parse', 'HEAD').decode().strip() == C['original_before']['head']
index = Path(checked('rev-parse', '--git-path', 'index').decode().strip())
index = index if index.is_absolute() else W / index
assert hashlib.sha256(index.read_bytes()).hexdigest() == C['original_before']['index_sha256']
assert git('config', '--get', 'core.hooksPath').returncode == 1
default = Path(checked('rev-parse', '--git-path', 'hooks/pre-push').decode().strip())
default = default if default.is_absolute() else W / default
assert not default.exists(), 'existing hook must not be overridden'
before = checked('ls-remote', '--refs', U, REF)
(D / 'remote-before.log').write_bytes(before)
assert before.strip() == b'', 'only a new task branch is authorized by this executor'
H.mkdir(exist_ok=False)
hook = H / 'pre-push'
hook.write_text('#!/usr/bin/env python3\nimport sys\nrows=[x.split() for x in sys.stdin.read().splitlines() if x]\nexpected=' + repr([REF, OID, REF, '0' * 40]) + '\nassert len(rows)==1 and rows[0]==expected, "deny non-exact or non-new push"\nassert sys.argv[2]==' + repr(U) + '\n')
hook.chmod(0o700)
attempt = {'at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'remote_url': U, 'ref': REF, 'commit': OID, 'send_count': 1, 'result': 'PUSH_ATTEMPTED_AWAITING_OBSERVATION'}
with (D / 'push-attempt.json').open('x') as f:
    f.write(json.dumps(attempt, indent=2) + '\n'); f.flush(); os.fsync(f.fileno())
p = git('-c', 'core.hooksPath=' + str(H), 'push', U, REF + ':' + REF)
(D / 'push.stdout').write_bytes(p.stdout)
(D / 'push.stderr').write_bytes(p.stderr)
after = checked('ls-remote', '--refs', U, REF)
(D / 'remote-after.log').write_bytes(after)
assert after.decode().strip() == OID + '\t' + REF, 'unknown remote result: never repeat push blindly'
assert p.returncode == 0, 'remote exists at exact OID but push exit is nonzero; preserve outcome'
assert checked('rev-parse', 'HEAD').decode().strip() == C['original_before']['head']
assert hashlib.sha256(index.read_bytes()).hexdigest() == C['original_before']['index_sha256']
out = {**attempt, 'at_completed': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'result': 'PASS_EXACT_NEW_TASK_BRANCH', 'push_exit': p.returncode, 'remote_oid': OID, 'original_head_index_preserved': True, 'runtime_unchanged': True, 'main_merge_tag_release_deploy': False}
(D / 'published.json').write_text(json.dumps(out, indent=2) + '\n')
print(json.dumps(out))
