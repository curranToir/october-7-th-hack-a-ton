"""Small runnable check for the planted world and scenario contract."""
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def check():
    world = json.loads((ROOT/'world.json').read_text())
    scenarios = json.loads((ROOT.parent/'eval/scenarios.json').read_text())
    assert len(world['clients']) == 3
    assert len([p for p in world['people'] if p['org']=='Toir']) == 5
    assert 45 <= len(world['slack']) <= 70
    assert len({m['container'] for m in world['slack']}) == 7
    assert all(4 <= n <= 6 for n in Counter(i['container'] for i in world['github']).values())
    assert len(world['hubspot']) == 5
    assert len(world['planted_facts']) >= 12
    assert sum(f['needs']=='crm_docs' for f in world['planted_facts']) >= 5
    assert 'gdrive' not in world
    for source in ['slack','github','hubspot']:
        assert all(i['dataset'] and i['container'] for i in world[source])
    for fact in world['planted_facts']:
        assert len(set(fact['sources'])) >= 2
        assert set(fact['sources']) <= {'slack','github','hubspot'}
        assert all(any(i['dataset'] in fact['datasets'] for i in world[source]) for source in fact['sources'])
        if fact['needs']=='crm_docs':
            assert 'hubspot' in fact['sources']
    assert 10 <= len(scenarios) <= 14
    assert sum(s['needs']=='crm_docs' for s in scenarios) >= 5
    assert sum(s['kind']=='grant' for s in scenarios) >= 1
    assert sum(s['kind']=='action' for s in scenarios) >= 2
    pairs = Counter(s['question'] for s in scenarios if s['kind']=='access')
    assert sum(n==2 for n in pairs.values()) >= 2
    for s in scenarios:
        assert s['as_user'] in {'jared@neptuneops.com','curran@toirinc.com'}
        assert set(s['expected_sources']) <= {'source:slack','source:github','source:hubspot'}
        if s['kind']=='access' and s['as_user']=='curran@toirinc.com':
            assert s['must_not_mention']
        if s['kind']=='action':
            assert s['expected_action']['repo'].startswith('Toir-FDE-Team/')
    print(f"World contract: {len(world['slack'])} Slack messages, {len(world['github'])} GitHub issues, {len(world['hubspot'])} HubSpot companies, {len(world['planted_facts'])} planted facts; {len(scenarios)} scenarios including {sum(s['needs']=='crm_docs' for s in scenarios)} CRM-dependent, {sum(n==2 for n in pairs.values())} access pairs, {sum(s['kind']=='grant' for s in scenarios)} grant, {sum(s['kind']=='action' for s in scenarios)} actions.")

if __name__ == '__main__':
    check()
