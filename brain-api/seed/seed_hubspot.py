"""Seed the fictional companies and their associated commercial records."""
from collections import Counter
from datetime import datetime, timezone
from common import WORLD, actions, call
from check_world import check

def records(api, noun, properties):
    out = []
    after = None
    while True:
        args = dict(limit=100, properties=properties)
        if after:
            args['after'] = str(after)
        page = call(api, 'hubspot', f'hubspot_{noun}_list', **args)
        out.extend(page.get('results', []))
        after = page.get('paging', {}).get('next', {}).get('after')
        if not after:
            return out

def main():
    check()
    api = actions()
    created = Counter()
    pipelines = call(api, 'hubspot', 'hubspot_pipelines_list', object_type='deals')['results']
    pipeline = next((p for p in pipelines if p['label'] == 'Toir FDE'), None)
    if pipeline is None:
        stages = [{'label':label, 'displayOrder':i, 'metadata':{'probability':prob}} for i,(label,prob) in enumerate([('discovery','0.1'),('negotiation','0.4'),('legal review','0.6'),('procurement approval','0.8'),('closed won','1.0'),('closed lost','0.0')])]
        pipeline = call(api, 'hubspot', 'hubspot_pipeline_create', object_type='deals', label='Toir FDE', display_order=1, stages=stages)
        created['pipelines'] += 1
    stage_ids = {s['label']:s['id'] for s in pipeline['stages']}
    companies = {r['properties'].get('name'):r for r in records(api, 'companies', ['name'])}
    contacts = {r['properties'].get('email'):r for r in records(api, 'contacts', ['email'])}
    deals = {r['properties'].get('dealname'):r for r in records(api, 'deals', ['dealname'])}
    notes = {(r['properties'].get('hs_note_body') or '').split('\n', 1)[0]:r for r in records(api, 'notes', ['hs_note_body'])}
    def associate(kind, child_id, company_id):
        call(api, 'hubspot', 'hubspot_association_create', from_object_type=kind, from_object_id=str(child_id), to_object_type='companies', to_object_id=str(company_id))
        created['association_calls'] += 1
    for company in WORLD['hubspot']:
        name = company['title']
        if name not in companies:
            companies[name] = call(api, 'hubspot', 'hubspot_company_create', name=name, domain=company['domain'], description='Fictional Toir FDE hackathon sandbox engagement.')
            created['companies'] += 1
        company_id = companies[name]['id']
        for person in company['contacts']:
            email = person['email']
            if email not in contacts:
                first, _, last = person['name'].partition(' ')
                contacts[email] = call(api, 'hubspot', 'hubspot_contact_create', email=email, firstname=first, lastname=last, jobtitle=person['title'], company=name)
                created['contacts'] += 1
            associate('contacts', contacts[email]['id'], company_id)
        for deal in company['deals']:
            if deal['title'] not in deals:
                deals[deal['title']] = call(api, 'hubspot', 'hubspot_deal_create', dealname=deal['title'], amount=deal['amount'], closedate=deal['close_date']+'T00:00:00Z', pipeline=pipeline['id'], dealstage=stage_ids[deal['stage']], description=f"Commercial owner: {deal['owner']}. Current stage: {deal['stage']}. Currency: USD.")
                created['deals'] += 1
            associate('deals', deals[deal['title']]['id'], company_id)
        for note in company['notes']:
            body = note['title']+'\n'+note['body']
            if note['title'] not in notes:
                notes[note['title']] = call(api, 'hubspot', 'hubspot_note_create', props={'hs_note_body':body,'hs_timestamp':datetime.now(timezone.utc).isoformat()})
                created['notes'] += 1
            associate('notes', notes[note['title']]['id'], company_id)
        result = call(api, 'hubspot', 'hubspot_company_get', company_id=str(company_id), properties=['name','domain'])
        assert result['properties']['name'] == name
        print('Read-back company:', name, company_id)
        note = company['notes'][-1]
        sample = call(api, 'hubspot', 'hubspot_note_get', note_id=str(notes[note['title']]['id']), properties=['hs_note_body'])
        assert sample['properties']['hs_note_body'] == note['title']+'\n'+note['body']
        linked = call(api, 'hubspot', 'hubspot_record_associations_get', object_type='companies', object_id=str(company_id), to_object_type='notes', limit=100)
        assert int(notes[note['title']]['id']) in {int(x.get('toObjectId', x.get('id'))) for x in linked.get('results', [])}
        print('Read-back note:', note['title'], len(sample['properties']['hs_note_body']), 'characters;', len(linked.get('results', [])), 'associated notes')
    print('Created:', dict(created), '(zero object counts means no new objects; associations are idempotent upserts)')

if __name__ == '__main__':
    main()
