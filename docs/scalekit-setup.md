# Connect Toir to Exa through Scalekit

The operator helper creates the `exa` connection in the Toir Scalekit environment, registers connected-account identifier `toir`, and checks that Scalekit reports `ACTIVE`. It does not run a paid search or contact Exa directly. The research pod uses the same connection/account identifiers.

The helper is restricted to AWS account `904469541651`, region `us-east-1`, and Secrets Manager prefix `/company-brain-hackathon`. It uses the Scalekit SDK version locked in `agents/research` and the local AWS CLI. Run commands below from the repository root.

## Establish the operator accounts

1. Sign in to [Scalekit](https://app.scalekit.com/) and create/select the Toir environment. Under **Developers > API Credentials**, obtain its environment URL, client ID and client secret. Creating an environment-scoped connection through the SDK requires workspace credentials with `sso:write`; listing the connections requires read permission.
2. Sign in to the Toir [Exa account](https://dashboard.exa.ai/api-keys). Under **Management > API Keys**, create a key for Toir research. Confirm the account has the usage allowance needed before running research.
3. If SDK connection creation is unavailable for the Scalekit credential, use **AgentKit > Connections > Create Connection**, select **Exa**, click **Create**, and set the connection name to **exa**. The helper will reuse that connection. Do not substitute an SSO connection or a custom provider with the same name.

These steps follow [Scalekit's Exa setup](https://docs.scalekit.com/agentkit/connectors/exa/). The helper uses the SDK's environment-connection API and discovers the built-in provider identifier rather than guessing its casing. Its `upsertConnectedAccount` payload uses `staticAuth.details.api_key`, as documented for Exa.

## Store credentials using hidden prompts

Apply the infrastructure secret resources first if they are not present:

```sh
.venv/bin/python infrastructure/deployment/scripts/manage.py \
  --account 904469541651 --region us-east-1 stack-update
```

Store the Scalekit values directly in AWS Secrets Manager:

```sh
.venv/bin/python infrastructure/deployment/scripts/manage.py \
  --account 904469541651 --region us-east-1 secret-set --name scalekit
```

The command prompts without echo for all five fields. Enter the environment URL, client ID and client secret from Scalekit, followed by `exa` for `SCALEKIT_CONNECTION_NAME` and `toir` for `SCALEKIT_ACCOUNT_ID`.

Store the Exa key with the separate hidden prompt:

```sh
.venv/bin/python infrastructure/deployment/scripts/manage.py \
  --account 904469541651 --region us-east-1 secret-set --name exa
```

No environment file is needed. Do not paste credentials into a shell command, chat, committed file or CLI argument. The setup helper captures AWS secret output in memory, suppresses vendor error bodies, and prints only a sanitized result. The Exa secret is for the local setup operator; the research pod receives Scalekit credentials and does not receive the Exa key.

## Register and verify the connection

Ensure the research agent's locked dependencies are installed, then run the helper:

```sh
.deployment/tools/node_modules/.bin/bun install --cwd agents/research --frozen-lockfile
.deployment/tools/node_modules/.bin/bun tooling/scalekit-setup.ts \
  --account 904469541651 --region us-east-1
```

The helper validates AWS identity before retrieving secrets. It refuses mismatched connection/provider/account names. If `exa` exists, it is reused; the `toir` account receives the currently stored Exa key, supporting credential rotation. Other connected accounts are not changed. The SDK may require a second upsert for an initially pending account, so setup permits at most two attempts and fails if `ACTIVE` is not confirmed.

After success, activate the stored runtime credentials on the server:

```sh
.venv/bin/python infrastructure/deployment/scripts/manage.py \
  --account 904469541651 --region us-east-1 secret-sync --restart
```

This uses the deployment workflow's drain/restart behavior. Run one research request through the web app to verify actual search access; `ACTIVE` is a Scalekit status check, not proof of Exa credits or live search success.

For subsequent checks without changing the connection/account or reading the Exa key:

```sh
.deployment/tools/node_modules/.bin/bun tooling/scalekit-setup.ts \
  --account 904469541651 --region us-east-1 --check
```

The check reads Scalekit client credentials from AWS to authenticate, then uses account summaries rather than retrieving connected-account authorization details. A missing connection, inactive account or API failure exits nonzero with a sanitized next step.

For Exa rotation, store the replacement with `secret-set --name exa`, rerun setup, and then run a research request. For Scalekit credential rotation, replace the `scalekit` secret, run `--check`, and run `secret-sync --restart` to refresh pod credentials. AWS remains the authoritative operator store; Scalekit also stores the Exa credential needed for its authenticated proxy.

## Local checks

```sh
.deployment/tools/node_modules/.bin/bun test tooling/scalekit-setup.test.ts
```

These tests use in-memory fake SDK/AWS responses and make no cloud requests. They cover new connection creation, repeat setup, read-only checks, target restrictions, malformed configuration, inactive accounts and redacted errors.
