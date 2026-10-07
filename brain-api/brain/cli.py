from . import config
import argparse
import asyncio
import json
import os
import sys

def main():
    parser = argparse.ArgumentParser(prog="python -m brain")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("serve")
    pull = sub.add_parser("pull")
    pull.add_argument("--as-user", required=True)
    pull.add_argument("--sources", nargs="+", choices=["slack", "github", "hubspot"])
    pull.add_argument("--from-recorded", action="store_true")
    grant = sub.add_parser("grant")
    grant.add_argument("--owner", required=True)
    grant.add_argument("--grantee", required=True)
    grant.add_argument("--dataset", required=True)
    for name in ("access", "auth-links"):
        command = sub.add_parser(name)
        command.add_argument("--user", required=True)
    args = parser.parse_args()
    if args.command == "serve":
        import uvicorn
        uvicorn.run("brain.api:app", host=os.environ["BRAIN_API_HOST"], port=int(os.environ["BRAIN_API_PORT"]), workers=1)
        return
    async def run():
        if args.command == "auth-links":
            from .scalekit_pull import auth_links
            for connection, link in (await auth_links(args.user)).items():
                print(f"{connection}: {link}")
            return
        from respan import Respan
        from . import memory
        respan = Respan()
        try:
            async with memory.writer_lock:
                await memory.initialize()
                if args.command == "access":
                    result = await memory.access(args.user)
                elif args.command == "grant":
                    result = await memory.grant(args.owner, args.grantee, args.dataset)
                else:
                    from .scalekit_pull import pull
                    result = await pull(args.as_user, args.sources, args.from_recorded)
                print(json.dumps(result, ensure_ascii=False))
        finally:
            respan.flush()
    try:
        asyncio.run(run())
    except (ValueError, PermissionError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
