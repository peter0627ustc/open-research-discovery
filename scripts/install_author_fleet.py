#!/usr/bin/env python3
"""Install only the dedicated author fleet runtime; preserve legacy campaigns."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path('/root/open-research-discovery-author-fleet-20260905')
CONF = Path('/root/.config/opr-author-fleet')


def seed():
    # This opaque credential file is copied via SSH; never print its contents.
    env = json.loads(Path('/root/.claude/settings.json').read_text()).get('env', {})
    keep = {k:v for k,v in env.items() if k.startswith('ANTHROPIC_') or k in ('LKM_ACCESS_KEY','GAIA_LKM_ACCESS_KEY')}
    assert keep.get('LKM_ACCESS_KEY') and keep.get('ANTHROPIC_AUTH_TOKEN', keep.get('ANTHROPIC_API_KEY'))
    assert keep['LKM_ACCESS_KEY'] == keep.get('GAIA_LKM_ACCESS_KEY')
    CONF.mkdir(parents=True,exist_ok=True,mode=0o700)
    path = CONF/'runtime.json'
    if path.exists():
        raise RuntimeError('Credential seed already exists; refusing overwrite')
    path.write_text(json.dumps({'env':keep,'gitlab_token':Path('/root/.config/gewulab-publisher/token').read_text().strip()}))
    path.chmod(0o600)
    print('Credential bundle prepared (values withheld)')


def install():
    os.environ['PATH']='/usr/local/bin:/root/.local/bin:'+os.environ['PATH']
    assert ROOT.is_dir()
    uv_python = Path('/root/.local/share/uv/python')
    alias = uv_python/'cpython-3.13-linux-x86_64-gnu'
    if not alias.exists():
        alias.symlink_to(uv_python/'cpython-3.13.14-linux-x86_64-gnu', target_is_directory=True)
    (CONF/'runtime.json').chmod(0o600)
    env = json.loads((CONF/'runtime.json').read_text())
    # The dedicated publisher has its own token file; do not replace old credentials.
    (CONF/'gitlab-token').write_text(env['gitlab_token']+'\n')
    (CONF/'gitlab-token').chmod(0o600)
    skill = ROOT/'.agents/skills/research-evidence-search'
    for target in (ROOT/'.claude/skills/research-evidence-search',Path('/root/.claude/skills/research-evidence-search')):
        target.parent.mkdir(parents=True,exist_ok=True)
        if target.exists() and target.resolve()!=skill.resolve():
            backup = target.with_name(target.name+'.pre-author-fleet-20260905')
            if backup.exists():
                raise RuntimeError('Skill backup already exists; inspect manually')
            target.rename(backup)
        if not target.exists():
            target.symlink_to(skill,target_is_directory=True)
    helper = Path('/usr/local/bin/opr-evidence')
    helper_target = ROOT/'bin/opr-evidence'
    helper_target.parent.mkdir(exist_ok=True)
    helper_target.write_text(f'#!/bin/sh\nexec {ROOT}/.venv/bin/python {ROOT}/scripts/evidence_http.py "$@"\n')
    helper_target.chmod(0o755)
    if helper.is_symlink() and helper.resolve()==(ROOT/'scripts/evidence_http.py').resolve():
        helper.unlink()  # upgrade only our earlier symlink, never user content
    if helper.exists() and helper.resolve()!=helper_target.resolve():
        raise RuntimeError('Existing evidence helper has a different owner')
    if not helper.exists():
        helper.symlink_to(helper_target)
    subprocess.run(['uv','sync','--frozen','--no-dev','--python','/root/.local/share/uv/python/cpython-3.13.14-linux-x86_64-gnu/bin/python3.13'],cwd=ROOT,check=True)
    for binary in ['claude','gaia','git','opr-evidence']:
        if not shutil.which(binary):
            raise RuntimeError('Missing runtime dependency: '+binary)
    units = Path('/etc/systemd/system')
    runtime = ROOT/'scripts/author_fleet_runtime.py'
    python = ROOT/'.venv/bin/python'
    common = f'WorkingDirectory={ROOT}\nEnvironment=PATH=/usr/local/bin:/root/.local/bin:/usr/bin:/bin\n'
    (units/'opr-author-fleet.service').write_text('[Unit]\nDescription=Author literature campaign (4 authors x 8 agents)\nAfter=network-online.target\n\n[Service]\nType=simple\n'+common+f'ExecStart={python} {runtime} run\nKillMode=control-group\nTimeoutStopSec=120\nRestart=no\nUMask=0077\n\n[Install]\nWantedBy=multi-user.target\n')
    (units/'opr-author-publish.service').write_text('[Unit]\nDescription=Publish only ready author problems\nAfter=network-online.target\n\n[Service]\nType=oneshot\n'+common+f'Environment=GEWU_GITLAB_TOKEN_FILE={CONF}/gitlab-token\nEnvironment=GEWU_GITLAB_NAMESPACE=peter0627ustc\nEnvironment=GEWU_GITLAB_URL=https://git.gewu-lab.ai\nExecStart={python} {ROOT}/scripts/author_fleet_publication.py --publisher {ROOT}/scripts/publish_ready_to_gewulab.py --runs-root {ROOT}/work/runs --workers 4 --max-items 80\nUMask=0077\n')
    (units/'opr-author-publish.timer').write_text('[Unit]\nDescription=Check ready author problems every 2 minutes\n\n[Timer]\nOnBootSec=2min\nOnUnitInactiveSec=2min\nPersistent=true\n\n[Install]\nWantedBy=timers.target\n')
    subprocess.run(['systemctl','daemon-reload'],check=True)
    print('Installed; campaign and publisher not started yet')


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['seed','install'])
    args=parser.parse_args()
    seed() if args.action=='seed' else install()
