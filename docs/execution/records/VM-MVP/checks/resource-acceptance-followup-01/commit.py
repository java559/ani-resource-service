"""Commit only the five already-validated acceptance drivers, preserving the WT.
No runtime tree, original index, original HEAD, remote or service mutation.
"""
from pathlib import Path
import datetime, hashlib, json, os, subprocess

W = Path('/home/chabking/.codex/worktrees/vm-mvp-20261023/ani-resource-service')
D = Path(__file__).parent
P = Path('/home/chabking/.codex/tasks/vm-mvp-20261023-01a168ce/resource-followup-prepare-01')
BASE = '8589b66f1370d8811777a87f3b02f6acba144818'
REF = 'refs/heads/codex/vm-mvp-resource-acceptance-01a168ce'
INDEX = D / 'acceptance.index'
env = {**os.environ, 'GIT_OPTIONAL_LOCKS': '0'}

def git(*args, alternate=False, data=None):
    e = {**env, **({'GIT_INDEX_FILE': str(INDEX)} if alternate else {})}
    p = subprocess.run(['git', '-C', str(W), *args], env=e, input=data, capture_output=True)
    assert p.returncode == 0, p.stderr.decode()
    return p.stdout

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

assert not INDEX.exists() and not (D / 'committed.json').exists()
index = Path(git('rev-parse', '--git-path', 'index').decode().strip())
index = index if index.is_absolute() else W / index
before = {'head': git('rev-parse', 'HEAD').decode().strip(), 'index_sha256': sha(index)}
assert before['head'] == '0a53b99c8d9f126fd02e6cdde2841c8fbea9046b'
assert before['index_sha256'] == 'e0c038ed7373292305e13f09b0ff1f9c3677aab5bb613023fda11c576f21be76'
assert subprocess.run(['git', '-C', str(W), 'show-ref', '--verify', '--quiet', REF], env=env).returncode == 1
prepared = json.loads((P / 'prepared.json').read_text())
assert prepared['published_runtime_ref'] == BASE and prepared['runtime_delta'] == []
for row in json.loads((P / 'runtime-byte-manifest.json').read_text())['files']:
    f = W / row['path']
    assert f.is_file() and sha(f) == row['current']['sha256'], row['path']
rows = prepared['script_delta']
assert len(rows) == 5 and all(sha(W / r['path']) == r['current']['sha256'] for r in rows)
paths = sorted(r['path'] for r in rows)
git('read-tree', BASE, alternate=True)
git('add', '--', *paths, alternate=True)
changed = sorted(p.decode() for p in git('diff', '--cached', '--name-only', '-z', BASE, alternate=True).split(b'\0') if p)
assert changed == paths
git('diff', '--cached', '--check', BASE, alternate=True)
tree = git('write-tree', alternate=True).decode().strip()
(D / 'diff.patch').write_bytes(git('diff', '--cached', BASE, '--', *paths, alternate=True))
(D / 'diff-stat.txt').write_bytes(git('diff', '--cached', '--stat', BASE, alternate=True))
message = b'test(vm): preserve account, restore and cleanup acceptance drivers\n\nAdd the exercised account-persistence and tenant cleanup drivers, Windows\ndisk/lifecycle drivers, and snapshot collection fixes. Runtime, API, module\nand migration bytes remain identical to 8589b66f. Windows guest acceptance\nremains incomplete; these drivers do not claim a completed OS matrix.\n'
commit = git('commit-tree', tree, '-p', BASE, data=message).decode().strip()
git('update-ref', REF, commit, '0' * 40)
after = {'head': git('rev-parse', 'HEAD').decode().strip(), 'index_sha256': sha(index)}
assert after == before
out = {'at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'result': 'COMMITTED_NOT_PUSHED', 'commit': commit, 'parent': BASE, 'tree': tree, 'ref': REF, 'files': rows, 'runtime_unchanged_files': prepared['runtime_files_compared'], 'original_before': before, 'original_after': after, 'validation': 'Reuse unchanged runtime make verify and actual Fedora driver-specific pure/live receipts; no new implementation change or test rerun.', 'remote_mutation': False}
(D / 'committed.json').write_text(json.dumps(out, indent=2) + '\n')
print(json.dumps({k: out[k] for k in ('result', 'commit', 'tree', 'ref', 'runtime_unchanged_files')}))
