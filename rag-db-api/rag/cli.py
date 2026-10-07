import argparse
import json
from pathlib import Path
import sys

from rag.answer import AnswerNotConfigured, answer
from rag.config import settings
from rag.db import connect, migrate
from rag.ingest import ingest_path
from rag.models import ModelServiceError
from rag.search import search


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest and search company knowledge.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate", help="Apply database migrations")
    ingest_parser = commands.add_parser("ingest", help="Ingest files or directories")
    ingest_parser.add_argument("paths", metavar="PATH", nargs="+", type=Path)
    ingest_parser.add_argument("--metadata", metavar="JSON")
    search_parser = commands.add_parser("search", help="Search the knowledge base")
    search_parser.add_argument("query", metavar="QUERY")
    search_parser.add_argument("-k", type=int, default=5, metavar="N")
    search_parser.add_argument("--no-rerank", action="store_true")
    answer_parser = commands.add_parser("answer", help="Answer with cited sources")
    answer_parser.add_argument("query", metavar="QUERY")
    answer_parser.add_argument("-k", type=int, default=5, metavar="N")
    commands.add_parser("docs", help="List ingested documents")
    args = parser.parse_args()

    try:
        if args.command == "migrate":
            versions = migrate()
            print("\n".join(versions) if versions else "Database is up to date.")
            return 0
        if args.command in {"search", "answer"} and args.k < 1:
            raise ValueError("-k must be positive")
        if args.command == "answer" and (
            not settings.answer_base_url or not settings.answer_model
        ):
            raise AnswerNotConfigured(
                "answer endpoint not configured: set ANSWER_BASE_URL/ANSWER_MODEL"
            )
        metadata = None
        if args.command == "ingest" and args.metadata is not None:
            metadata = json.loads(args.metadata)
            if metadata is not None and not isinstance(metadata, dict):
                raise ValueError("metadata must be a JSON object or null")

        with connect() as conn:
            if args.command == "ingest":
                for path in args.paths:
                    for result in ingest_path(conn, path, metadata):
                        status = "skipped" if result.skipped else f"{result.chunks} chunks"
                        print(f"{result.source}: {status} (document {result.document_id})")
            elif args.command == "search":
                hits = search(conn, args.query, top_k=args.k, use_rerank=not args.no_rerank)
                for rank, hit in enumerate(hits, 1):
                    print(f"{rank}. {hit.score:.6f} {hit.source} #{hit.ord}")
                    print(hit.content[:300])
            elif args.command == "answer":
                hits = search(conn, args.query, top_k=args.k)
                print(answer(args.query, hits))
                print("\nSources:")
                for rank, hit in enumerate(hits, 1):
                    print(f"[{rank}] {hit.source} #{hit.ord}")
            elif args.command == "docs":
                rows = conn.execute(
                    "SELECT d.id, d.source, d.title, d.metadata, d.created_at, "
                    "count(c.id) AS chunks FROM documents d "
                    "LEFT JOIN chunks c ON c.document_id = d.id "
                    "GROUP BY d.id ORDER BY d.id"
                ).fetchall()
                for row in rows:
                    print(
                        f"{row['id']}\t{row['chunks']} chunks\t{row['source']}\t"
                        f"{row['title'] or ''}\t{row['created_at']}"
                    )
    except (ModelServiceError, AnswerNotConfigured, ValueError) as error:
        print(f"error: {' '.join(str(error).splitlines())}", file=sys.stderr)
        return 1
    return 0
