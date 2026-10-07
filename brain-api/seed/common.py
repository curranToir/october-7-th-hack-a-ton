"""Shared world and authenticated Scalekit calls; no secret output."""
import json
import os
from pathlib import Path
from dotenv import load_dotenv
from scalekit import ScalekitClient

ROOT = Path(__file__).resolve().parent
WORLD = json.loads((ROOT / 'world.json').read_text())
IDENTIFIER = 'curran@toirinc.com'
load_dotenv(ROOT.parent / '.env')

def actions():
    return ScalekitClient(env_url=os.environ['SCALEKIT_ENVIRONMENT_URL'], client_id=os.environ['SCALEKIT_CLIENT_ID'], client_secret=os.environ['SCALEKIT_CLIENT_SECRET']).actions

def plain(value):
    if hasattr(value, 'model_dump'):
        return value.model_dump()
    if hasattr(value, 'DESCRIPTOR'):
        from google.protobuf.json_format import MessageToDict
        return MessageToDict(value, preserving_proto_field_name=True)
    return value

def call(api, connection, tool_name, *, identifier=IDENTIFIER, **inputs):
    result = plain(api.execute_tool(tool_name=tool_name, tool_input=inputs, connection_name=connection, identifier=identifier).data)
    if isinstance(result, str):
        result = json.loads(result)
    if isinstance(result, dict) and result.get('ok') is False:
        raise RuntimeError(f'{tool_name}: {result.get("error", "failed")}')
    return result

if __name__ == '__main__':
    import sys
    api = actions()
    token = None
    while True:
        result = plain(api.list_tools(connection_name=sys.argv[1], page_token=token))
        for t in result.get('tools', []):
            t = plain(t)['definition']
            schema = t.get('input_schema', {})
            if isinstance(schema, str):
                schema = json.loads(schema)
            print(t['name'], sorted(schema.get('properties', {})))
        token = result.get('next_page_token')
        if not token:
            break
