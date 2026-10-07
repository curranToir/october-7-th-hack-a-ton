"""Wait for manually created channels, then seed once without duplicate text."""
from collections import Counter
from contextlib import redirect_stdout, redirect_stderr
from scalekit.common.exceptions import ScalekitBadRequestException
import traceback
import time
from common import ROOT, WORLD, actions, call

def main():
    api = actions()
    required = set(m['container'] for m in WORLD['slack'])
    deadline = time.monotonic() + 25 * 60
    while True:
        channels = {}
        inactive = False
        try:
            workspace = call(api, 'slack', 'slack_auth_test', identifier='jared@neptuneops.com')
            team_id = workspace.get('context_team_id', workspace.get('team_id'))
            if team_id != 'T09AH3EQ1TL':
                raise SystemExit('ABORT: unapproved Slack workspace team ID: '+str(team_id))
            cursor = None
            while True:
                args = dict(limit=200, types='public_channel,private_channel', exclude_archived=True)
                if cursor:
                    args['cursor'] = cursor
                page = call(api, 'slack', 'slack_list_channels', identifier='jared@neptuneops.com', **args)
                if any(c.get('context_team_id', 'T09AH3EQ1TL') != 'T09AH3EQ1TL' for c in page.get('channels', [])):
                    raise SystemExit('ABORT: channel context_team_id is not T09AH3EQ1TL.')
                channels.update({c['name']: c['id'] for c in page.get('channels', [])})
                cursor = page.get('response_metadata', {}).get('next_cursor')
                if not cursor:
                    break
        except ScalekitBadRequestException as exc:
            if 'not active' not in str(exc).lower():
                raise
            inactive = True
        missing = required - channels.keys()
        if not inactive and not missing:
            print('Verified Slack workspace Toir Inc; all seven routed channels visible.', flush=True)
            break
        if time.monotonic() >= deadline:
            raise SystemExit('Slack timed out: reactivate Jared in team T09AH3EQ1TL and create/join channels: '+', '.join(sorted(missing)))
        print('Waiting for Jared Slack authorization into Toir Inc.' if inactive else 'Waiting for manually created channels: '+', '.join(sorted(missing)), flush=True)
        time.sleep(min(60, max(0, deadline-time.monotonic())))
    created = Counter()
    for name in dict.fromkeys(m['container'] for m in WORLD['slack']):
        channel = channels[name]
        existing = set()
        cursor = None
        while True:
            args = dict(channel=channel, limit=100)
            if cursor:
                args['cursor'] = cursor
            page = call(api, 'slack', 'slack_fetch_conversation_history', identifier='jared@neptuneops.com', **args)
            existing.update(m.get('text', '') for m in page.get('messages', []))
            cursor = page.get('response_metadata', {}).get('next_cursor')
            if not cursor:
                break
        for item in (m for m in WORLD['slack'] if m['container'] == name):
            if item['text'] not in existing:
                call(api, 'slack', 'slack_send_message', identifier='jared@neptuneops.com', channel=channel, text=item['text'])
                existing.add(item['text'])
                created['messages'] += 1
        sample = call(api, 'slack', 'slack_fetch_conversation_history', identifier='jared@neptuneops.com', channel=channel, limit=100)
        assert all(m['text'] in {x['text'] for x in sample.get('messages', [])} for m in WORLD['slack'] if m['container'] == name)
        print(f'Read-back #{name} {channel}: {len(sample.get("messages", []))} messages')
    print('Created:', dict(created), '(zero means no new objects)')
    print('Slack seeding identity: jared@neptuneops.com; member of all seven channels. Dataset isolation is enforced by Cognee.')

if __name__ == '__main__':
    with (ROOT / 'slack-seed.log').open('a', buffering=1) as logfile, redirect_stdout(logfile), redirect_stderr(logfile):
        try:
            main()
        except BaseException:
            traceback.print_exc()
            raise
