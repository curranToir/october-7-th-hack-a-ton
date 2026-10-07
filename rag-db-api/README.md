# Company knowledge RAG

A small Python library, CLI, and FastAPI API for PDF and text retrieval on the DGX Spark. Postgres stores documents and 2048-dimensional `halfvec` embeddings; two vLLM servers provide Nemotron 1B embedding and reranking. Answer generation is optional and uses an external OpenAI-compatible endpoint.

## Services

Everything listens on **localhost only**. There is no application authentication: use SSH, not a public bind address.

| Service | Address | Details |
| --- | --- | --- |
| Postgres + pgvector | `127.0.0.1:5432` | Database/user `rag`, password from `POSTGRES_PASSWORD` |
| Nemotron embedder | `127.0.0.1:8101` | `nvidia/llama-nemotron-embed-1b-v2`, served as `nemotron-embed` |
| Nemotron reranker | `127.0.0.1:8102` | `nvidia/llama-nemotron-rerank-1b-v2`, served as `nemotron-rerank` |
| FastAPI (host venv) | `127.0.0.1:8100` | Interactive docs at `/docs` |

## First-time setup (on the Spark)

Already done on the Spark: `.env` exists with a generated password, the containers are running, `.venv` is installed, and migrations are applied. Repeat these steps only on a fresh machine, from the `rag-db-api/` folder of a clone of this repo:

```bash
cp .env.example .env
# Edit .env: set POSTGRES_PASSWORD and the matching password in DATABASE_URL.
# URL-encode special characters in the DATABASE_URL password.
docker compose up -d
docker compose ps
docker compose logs -f
```

Wait for healthy services before ingesting. The first start downloads about **5 GB of model weights**, so loading is not instant. Model readiness can be checked with:

```bash
curl --fail http://127.0.0.1:8101/v1/models
curl --fail http://127.0.0.1:8102/v1/models
```

Then install the package and initialize the database:

```bash
python3 -m venv .venv && . .venv/bin/activate && pip install -e '.[dev]'
python -m rag migrate
uvicorn rag.api:app --host 127.0.0.1 --port 8100 --reload
```

In a second shell, `curl http://127.0.0.1:8100/health` should report `{"db":true,"embed":true,"rerank":true}`. `/health` always returns 200; inspect the booleans. Migration is repeatable: an already-applied migration is skipped.

The project-root `.env` is loaded automatically; real environment variables override it. Connection/model settings are:

```dotenv
DATABASE_URL=postgresql://rag:<password>@127.0.0.1:5432/rag
EMBED_BASE_URL=http://127.0.0.1:8101/v1
EMBED_MODEL=nemotron-embed
RERANK_BASE_URL=http://127.0.0.1:8102
RERANK_MODEL=nemotron-rerank
ANSWER_BASE_URL=
ANSWER_API_KEY=
ANSWER_MODEL=
```

## Daily use

Activate `.venv`, then:

```bash
python -m rag --help
python -m rag ingest ./knowledge.pdf ./notes.md --metadata '{"team":"hackathon"}'
python -m rag ingest ./company-docs
python -m rag search "What is the expense approval policy?" -k 5
python -m rag search "expense approval" --no-rerank
python -m rag docs
python -m rag answer "Who approves expenses?" -k 5
uvicorn rag.api:app --host 127.0.0.1 --port 8100 --reload
```

Directories are scanned recursively for `.pdf`, `.txt`, `.md`, and `.markdown`. Identical extracted content is deduplicated by SHA-256: repeat ingestion reports `skipped`, not duplicate chunks. PDF extraction reads embedded text, not OCR for scanned images.

Upload and search:

```bash
curl --fail http://127.0.0.1:8100/ingest/file \
  -F 'file=@knowledge.pdf' -F 'metadata={"team":"hackathon"}'

curl --fail http://127.0.0.1:8100/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"What is the expense approval policy?","top_k":5,"candidates":50,"rerank":true}'

curl --fail http://127.0.0.1:8100/ingest/text \
  -H 'Content-Type: application/json' \
  -d '{"text":"Expenses require manager approval.","source":"policy-note","title":"Expenses","metadata":{"team":"hackathon"}}'
```

Uploads are saved under `data/uploads/<filename>` in the project. Multipart metadata must be a JSON object or `null` (400 otherwise); unsupported file types return 415.

| Route | Result |
| --- | --- |
| `GET /health` | Reachability booleans for DB/embedder/reranker |
| `POST /ingest/text` | `{source, document_id, chunks, skipped}` |
| `POST /ingest/file` | Same ingestion result; multipart `file`, optional `metadata` |
| `POST /search` | `{"hits":[...]}`; defaults: `top_k=5`, `candidates=50`, `rerank=true` |
| `POST /answer` | `{"answer":"...","hits":[...]}`; body `{"query":"...","top_k":5}` |
| `GET /documents` | List of `{id, source, title, metadata, created_at, chunks}` |
| `DELETE /documents/{id}` | 204 (also deletes chunks), or 404 when missing |

Model service failures return 502 with a `detail` message naming the service URL. CLI model/configuration/input failures print a one-line error and exit 1.

## How retrieval works

1. Embed the query; get vector top-N by cosine distance using the HNSW index.
2. Get English full-text search top-N from Postgres.
3. Fuse the two rankings with reciprocal rank fusion (RRF).
4. Rerank the fused candidates with Nemotron, then return the best `top_k` hits.

The library adds the required **`query: `** and **`passage: `** prefixes automatically; callers supply plain text. Embeddings retain all 2048 dimensions and are stored as `halfvec(2048)`. Reranked scores are relevance probabilities in 0–1 (vLLM applies a sigmoid to the reranker logit); `--no-rerank` returns RRF scores.

## Optional grounded answers

Set these in `.env` or the shell, then restart the API (or start a fresh CLI process):

```dotenv
ANSWER_BASE_URL=https://api.openai.com/v1
ANSWER_API_KEY=<your-key>
ANSWER_MODEL=<a-chat-model-you-can-access>
```

Any server implementing OpenAI-compatible `POST /chat/completions` works, including Anthropic's OpenAI-compatible endpoint (`ANSWER_BASE_URL=https://api.anthropic.com/v1`, `ANSWER_MODEL=claude-haiku-4-5` or another Claude model). `ANSWER_BASE_URL` includes the API prefix, such as `/v1`, but not `/chat/completions`. The API key is optional for servers without authentication.

The answer prompt uses only retrieved context and cites sources as `[1]`, `[2]`, etc., corresponding to returned hits. Retrieved text is sent to the configured provider; choose a provider appropriate for your company data. No local generation model is launched.

When the base URL or model is empty, `/answer` returns **503** with `{"detail":"answer endpoint not configured: set ANSWER_BASE_URL/ANSWER_MODEL"}`; `python -m rag answer` reports the same configuration error and exits 1.

## Laptop access and shutdown

From your laptop (over Tailscale), keep this tunnel open:

```bash
ssh -L 8100:127.0.0.1:8100 jlyon@waffle-spark
```

Then use `http://127.0.0.1:8100` on the laptop. Curran can SSH into the Spark and use the same project and venv directly. Do not change service binds to `0.0.0.0`.

Stop the API with Ctrl-C; stop the containers with:

```bash
docker compose down
```

Database data persists in the `pgdata` volume; uploaded files stay in the host project directory. Do not use `docker compose down -v` if you want to retain the corpus.

## Shared access on the Spark (Jared + Curran)

The Spark holds a sparse clone of the hackathon repo at `/srv/october-7-th-hack-a-ton` with only this `rag-db-api/` folder checked out. The stack runs from `/srv/october-7-th-hack-a-ton/rag-db-api` (`~/rag-db-api` is a symlink to it for jlyon). Both `jlyon` and `curran` are in the `hackathon` group; group ACLs keep every new file editable by both, and `.venv` is shared.

- `git pull` needs no credentials (the repo is public). Pushing from the Spark uses your own GitHub key via agent forwarding, so connect with `ssh -A`. No GitHub credentials are stored on the Spark.
- `curran` has no general sudo and no Docker access, and can't read `/home/jlyon`. His SSH login lands in this folder with the venv activated.
- Container control for curran: `sudo oct7-rag-ctl ps`, `sudo oct7-rag-ctl logs [-f] [db|embed|rerank]`, `sudo oct7-rag-ctl restart [db|embed|rerank]`. `restart` doesn't apply `compose.yaml` changes; jlyon runs `docker compose up -d` for that.
