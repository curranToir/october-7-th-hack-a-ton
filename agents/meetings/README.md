# Meeting analysis worker

This private Python/FastAPI worker turns a saved customer transcript into structured
notes, customer needs, next steps, issue drafts and named company/person mentions.
It calls the existing Respan gateway and validates all issue/mention evidence
against exact transcript quotes. Supplied transcript text is untrusted data.

The worker does not join Zoom, store records, approve tasks, create GitHub issues
or conduct research. The coordinator owns those operations and calls this worker
at `POST /v1/analyses`. `GET /v1/capabilities` reports model configuration;
`GET /health` and `GET /ready` report process health even while the key is missing.

```sh
# From the repository root; inject RESPAN_API_KEY securely into this process.
.venv/bin/python -m uvicorn agents.meetings.main:app --host 127.0.0.1 --port 8004 --reload
```

Set the coordinator's local `MEETING_AGENT_URL=http://127.0.0.1:8004`.
`RESPAN_MODEL` defaults to `gpt-5-mini`. Kubernetes runs this worker on private port
8000 using `infrastructure/docker/meetings.Dockerfile`, the Respan-only
`meetings-runtime` secret, no durable volume and coordinator-only network ingress.
It must never receive Recall, GitHub, database or Scalekit credentials.

Meeting analysis uses the Respan gateway, but this worker does not yet initialize
LangChain tracing or propagate meeting trace context across the HTTP boundary.
Correlated coordinator-to-meeting analysis traces remain unverified.

See [the meeting integration runbook](../../docs/meeting-agent.md) for the design,
Recall setup, demo, approval gate, public callback requirements and recovery.
