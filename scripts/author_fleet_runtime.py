#!/usr/bin/env python3
"""Scoped fleet runtime: credentials outside repositories, durable run checkpoints."""
import argparse
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
SECRETS = pathlib.Path('/root/.config/opr-author-fleet/runtime.json')
RUN_ID = 'author-literature-20260905'


def environment():
    data = json.loads(SECRETS.read_text())
    for key, value in data['env'].items():
        if key.startswith('ANTHROPIC_') or key in ('LKM_ACCESS_KEY', 'GAIA_LKM_ACCESS_KEY'):
            os.environ[key] = value
    os.environ['PATH'] = '/usr/local/bin:/root/.local/bin:' + os.environ.get('PATH', '')
    return data


def preflight():
    environment()
    from open_research_discovery.agent import ClaudeRunner
    from open_research_discovery.common import dump_json_atomic
    from open_research_discovery.lkm import collect_paper_open_questions
    from open_research_discovery.common import load_yaml
    config = load_yaml(ROOT / 'campaign.yaml')
    target = ROOT / 'preflight'
    target.mkdir(exist_ok=True)
    checks = {}
    for route in ['web', 'crossref']:
        proc = subprocess.run(['opr-evidence','search','Witten topological quantum field theory', '--route',route,'--limit','2'], capture_output=True,text=True,timeout=100)
        payload = json.loads(proc.stdout)
        checks[route] = proc.returncode == 0 and payload.get('status') == 'ok' and bool(payload.get('items'))
        dump_json_atomic(target / (route + '.json'), payload)
    proc = subprocess.run(['opr-evidence','fetch','https://arxiv.org/abs/2401.00001'], capture_output=True,text=True,timeout=100)
    payload = json.loads(proc.stdout)
    checks['fetch'] = proc.returncode == 0 and payload.get('status') == 'ok' and 'arxiv' in payload.get('text','').lower()
    dump_json_atomic(target / 'fetch.json',payload)
    graph = collect_paper_open_questions(doi='10.1007/bf01223371', access_key=os.environ['LKM_ACCESS_KEY'], timeout=60)
    dump_json_atomic(target / 'lkm.json', graph)
    checks['lkm'] = isinstance(graph.get('open_questions'), list)
    schema = {'type':'object','properties':{'ok':{'type':'boolean'}},'required':['ok'],'additionalProperties':False}
    dump_json_atomic(target/'schema.json',schema)
    runner = ClaudeRunner(repository_root=ROOT, executable=config['agents']['claude_executable'],model='GLM-5.3',timeout_seconds=600)
    result = runner.run(role='research', cwd=target, schema_path=target/'schema.json',output_path=target/'agent.json',events_path=target/'agent-events.jsonl',
        prompt='Runtime tool test only, no problem research. Invoke Skill research-evidence-search; read its source references as required. Then use Bash to execute opr-evidence search "Witten topological quantum field theory" --route crossref --limit 1 and write stdout to agent-search.json. Use Bash to run gaia search lkm knowledge "topological quantum field theory" --scopes claim --retrieval-mode hybrid --limit 1 --no-hint and write stdout to agent-lkm.json. Read both files. Use Write to write TOOL_CHECK_OK into agent-marker.txt. Return {"ok":true} only if Skill invocation, both real lookups and marker write succeeded. Never output credentials. Do not launch nested agents.')
    checks['agent'] = result.output.get('ok') is True
    checks['marker'] = (target/'agent-marker.txt').is_file() and (target/'agent-marker.txt').read_text().strip()=='TOOL_CHECK_OK'
    for label, filename in [('agent_search','agent-search.json'),('agent_lkm','agent-lkm.json')]:
        path = target/filename
        checks[label] = path.is_file() and path.stat().st_size > 50
        if label == 'agent_search' and checks[label]:
            checks[label] = json.loads(path.read_text()).get('status')=='ok'
        if label == 'agent_lkm' and checks[label]:
            checks[label] = json.loads(path.read_text()).get('code') == 0
    token = json.loads(SECRETS.read_text())['gitlab_token']
    request = urllib.request.Request('https://git.gewu-lab.ai/api/v4/user', headers={'PRIVATE-TOKEN':token})
    with urllib.request.urlopen(request,timeout=45) as response:
        account = json.loads(response.read())
    checks['gitlab'] = account.get('username') == 'peter0627ustc'
    payload = {'checks':checks,'ok':all(checks.values()),'lkm_key_sha256':hashlib.sha256(os.environ['LKM_ACCESS_KEY'].encode()).hexdigest(),'time':time.time()}
    dump_json_atomic(ROOT/'preflight-result.json',payload)
    print(json.dumps(payload),flush=True)
    if not payload['ok']:
        raise RuntimeError('Preflight failed; campaign is not authorized to start without working retrieval')


def run():
    environment()
    if not json.loads((ROOT/'preflight-result.json').read_text())['ok']:
        raise RuntimeError('Missing successful preflight')
    state = ROOT/'work/runs'/RUN_ID/'state.json'
    cli = ROOT/'.venv/bin/discovery'
    for attempt in range(7):
        if state.exists():
            data = json.loads(state.read_text())
            if data.get('status') == 'completed':
                return
            if data.get('status') == 'partial_failed' and data.get('summary') and not data['summary'].get('failed_papers'):
                print(json.dumps({'event':'valid_papers_finished','remaining':'input_issues_require_manual_correction'}),flush=True)
                return
        command = [str(cli),'campaign','resume',str(state.parent)] if state.exists() else [str(cli),'campaign','run',str(ROOT/'campaign.yaml'),'--run-id',RUN_ID]
        result = subprocess.run(command,check=False)
        if state.exists() and json.loads(state.read_text()).get('status') == 'completed':
            return
        print(json.dumps({'event':'campaign_attempt_finished','attempt':attempt+1,'returncode':result.returncode}),flush=True)
        if attempt < 6:
            time.sleep(900)
    raise RuntimeError('Retry budget exhausted; preserved checkpoints for operator review')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action',choices=['preflight','run'])
    args = parser.parse_args()
    preflight() if args.action == 'preflight' else run()
