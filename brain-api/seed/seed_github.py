"""Run on the tower (gh auth), using --world when downloaded separately."""
import argparse
import base64
from collections import Counter
import json
from pathlib import Path
import subprocess
import sys

ORG = 'Toir-FDE-Team'

def gh(*args, data=None, missing_ok=False):
    result = subprocess.run(['gh', *args], input=json.dumps(data) if data is not None else None, text=True, capture_output=True)
    if result.returncode:
        if missing_ok and '404' in result.stderr:
            return None
        raise RuntimeError(result.stderr.strip()+' '+result.stdout.strip())
    return json.loads(result.stdout) if result.stdout.strip() else None

def api(path, method='GET', data=None, missing_ok=False):
    args = ['api', path, '--method', method]
    if data is not None:
        args.extend(['--input', '-'])
    return gh(*args, data=data, missing_ok=missing_ok)

def pages(path):
    return [item for page in gh('api', path, '--paginate', '--slurp') for item in page]

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--world', type=Path, default=Path(__file__).with_name('world.json'))
    args = parser.parse_args()
    world = json.loads(args.world.read_text(encoding='utf-8'))
    if api('orgs/'+ORG, missing_ok=True) is None:
        sys.exit('create org Toir-FDE-Team first')
    created = Counter()
    for repo in dict.fromkeys(x['container'] for x in world['github']):
        full = ORG+'/'+repo
        if api('repos/'+full, missing_ok=True) is None:
            api('orgs/'+ORG+'/repos', method='POST', data={'name':repo,'private':True,'description':'Fictional Toir FDE hackathon engagement','auto_init':True})
            created['repos'] += 1
        if repo in {'globex-clinical-rag','toir-playbooks'}:
            api('repos/'+full+'/collaborators/curranToir', method='PUT', data={'permission':'push'})
        labels = {x['name'] for x in pages('repos/'+full+'/labels?per_page=100')}
        items = [x for x in world['github'] if x['container'] == repo]
        for label in sorted({label for item in items for label in item['labels']}):
            if label not in labels:
                try:
                    api('repos/'+full+'/labels', method='POST', data={'name':label,'color':'2563eb','description':'Toir engagement '+label})
                    created['labels'] += 1
                except RuntimeError as exc:
                    # GitHub may initialize default labels after a brand-new repo's first read.
                    if 'already_exists' not in str(exc):
                        raise
        existing_readme = api('repos/'+full+'/readme', missing_ok=True)
        readme = '# '+repo+'\n\nFictional Toir FDE engagement.\n\n'+'\n\n'.join(x['title']+'\n'+x['body'] for x in items)+'\n'
        if existing_readme is None or base64.b64decode(existing_readme['content']).decode() != readme:
            payload = {'message':'Seed engagement README','content':base64.b64encode(readme.encode()).decode()}
            if existing_readme:
                payload['sha'] = existing_readme['sha']
            api('repos/'+full+'/contents/README.md', method='PUT', data=payload)
            created['readmes'] += 1
        issues = {x['title']:x for x in pages('repos/'+full+'/issues?state=all&per_page=100') if 'pull_request' not in x}
        eligible = {x['login'].lower() for x in pages('repos/'+full+'/assignees?per_page=100')}
        for item in items:
            assigned = [item['assignee']] if item['assignee'].lower() in eligible else []
            if not assigned:
                print(f"Pending collaborator acceptance: assign {full} / {item['title']} to {item['assignee']} after invitation acceptance; rerun this script.")
            if item['title'] not in issues:
                issues[item['title']] = api('repos/'+full+'/issues', method='POST', data={'title':item['title'],'body':item['body'],'labels':item['labels'],'assignees':assigned})
                created['issues'] += 1
            number = issues[item['title']]['number']
            if assigned and item['assignee'].lower() not in {x['login'].lower() for x in issues[item['title']]['assignees']}:
                api(f'repos/{full}/issues/{number}', method='PATCH', data={'assignees':assigned})
                created['assignee_updates'] += 1
            comments = {c['body'] for c in pages(f'repos/{full}/issues/{number}/comments?per_page=100')}
            for text in item['comments']:
                if text not in comments:
                    api(f'repos/{full}/issues/{number}/comments', method='POST', data={'body':text})
                    created['comments'] += 1
        print('Read-back:', full, len(issues), 'issues')
    print('Created:', dict(created), '(zero means no new objects)')
    print('Curran must accept invitations to globex-clinical-rag and toir-playbooks. Do not grant Curran the Acme or Initech repos. Authorize github-connect in Scalekit for each identity using their own GitHub account.')

if __name__ == '__main__':
    main()
