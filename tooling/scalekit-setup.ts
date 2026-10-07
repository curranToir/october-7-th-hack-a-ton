#!/usr/bin/env bun
/** Operator-only setup. Secret values remain in process memory and never enter argv. */
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { parseArgs } from 'node:util';
import {
  ScalekitClient, ConnectionType, ConnectorStatus,
} from '../agents/research/node_modules/@scalekit-sdk/node';

const execFileAsync = promisify(execFile);
const ACCOUNT = '904469541651';
const REGION = 'us-east-1';
const SECRET_PREFIX = '/company-brain-hackathon';
const DASHBOARD_STEP = 'In Scalekit, open AgentKit > Connections > Create Connection, select Exa, and name the connection exa.';

export class SetupError extends Error {}
export type Options = { account: string; region: string; check: boolean };
export type AwsJson = (args: string[]) => Promise<unknown>;
export type SetupClient = {
  connection: Pick<ScalekitClient['connection'], 'listAppConnections' | 'createEnvironmentConnection'>;
  actions: Pick<ScalekitClient['actions'], 'upsertConnectedAccount' | 'listConnectedAccounts'> & {
    providers: Pick<ScalekitClient['actions']['providers'], 'listProviders'>;
  };
};
type Secret = Record<string, string>;

export function parseOptions(args: string[]): Options | null {
  let values;
  try {
    ({ values } = parseArgs({ args, options: {
      account: { type: 'string', default: ACCOUNT },
      region: { type: 'string', default: REGION },
      check: { type: 'boolean', default: false },
      help: { type: 'boolean', default: false },
    }, strict: true, allowPositionals: false }));
  } catch {
    throw new SetupError('Invalid arguments. Use --help. Credentials must not be passed as arguments.');
  }
  if (values.help) return null;
  if (values.account !== ACCOUNT || values.region !== REGION) {
    throw new SetupError(`This Toir setup is restricted to AWS account ${ACCOUNT}, region ${REGION}.`);
  }
  return { account: values.account, region: values.region, check: values.check };
}

function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new SetupError('A configuration response was not a JSON object; no response contents were logged.');
  }
  return value as Record<string, unknown>;
}

async function call<T>(operation: () => Promise<T>, message: string): Promise<T> {
  try { return await operation(); }
  catch { throw new SetupError(message); }
}

/** AWS subprocess output is piped, bounded, parsed in memory and never printed. */
export function awsJson(region: string): AwsJson {
  return async (args) => {
    const response = await call(() => execFileAsync('aws', [
      ...args, '--region', region, '--output', 'json', '--no-cli-pager',
    ], {
      encoding: 'utf8', timeout: 30_000, maxBuffer: 1024 * 1024,
      env: { ...process.env, AWS_PAGER: '', AWS_CLI_AUTO_PROMPT: 'off' },
    }), 'AWS request failed. Check local AWS authentication, region and Secrets Manager read permission.');
    return call(async () => JSON.parse(response.stdout), 'AWS returned an invalid response; its contents were not logged.');
  };
}

async function readSecret(aws: AwsJson, name: 'scalekit' | 'exa', fields: string[]): Promise<Secret> {
  const response = object(await aws(['secretsmanager', 'get-secret-value', '--secret-id', `${SECRET_PREFIX}/${name}`]));
  if (typeof response.SecretString !== 'string') {
    throw new SetupError(`Store the ${name} secret with manage.py secret-set --name ${name} first.`);
  }
  let value: Record<string, unknown>;
  try { value = object(JSON.parse(response.SecretString)); }
  catch { throw new SetupError(`The ${name} secret must contain a JSON object; contents were not logged.`); }
  for (const field of fields) {
    if (typeof value[field] !== 'string' || !(value[field] as string).trim()) {
      throw new SetupError(`The ${name} secret is missing ${field}; rerun manage.py secret-set --name ${name}.`);
    }
  }
  return Object.fromEntries(fields.map(field => [field, (value[field] as string).trim()]));
}

/** Refuse pagination loops instead of treating an incomplete list as absent. */
async function collect<T>(fetch: (token?: string) => Promise<{ items: T[]; nextPageToken: string }>): Promise<T[]> {
  const result: T[] = [];
  const seen = new Set<string>();
  let token: string | undefined;
  do {
    const page = await fetch(token);
    result.push(...page.items);
    token = page.nextPageToken || undefined;
    if (token && (seen.has(token) || seen.size >= 100)) {
      throw new SetupError('Scalekit pagination did not finish; no further changes were attempted.');
    }
    if (token) seen.add(token);
  } while (token);
  return result;
}

async function accountStatus(client: SetupClient): Promise<ConnectorStatus | undefined> {
  const accounts = await collect(async (pageToken) => {
    const response = await call(() => client.actions.listConnectedAccounts({
      connectionName: 'exa', identifier: 'toir', pageSize: 30, pageToken,
    }), 'Could not read the Toir connected-account status. Check Scalekit API credentials and permissions.');
    return { items: response.connectedAccounts, nextPageToken: response.nextPageToken };
  });
  // List responses omit authorization details. Do not fetch full account credentials to check status.
  const matches = accounts.filter(account => account.connector === 'exa' && account.identifier === 'toir');
  if (matches.length > 1) throw new SetupError('Multiple Toir Exa accounts matched; inspect the Scalekit dashboard before retrying.');
  return matches[0]?.status;
}

export async function setupToir(
  options: Options,
  dependencies: {
    aws: AwsJson;
    client: (secret: Secret) => SetupClient;
  },
): Promise<string> {
  if (options.account !== ACCOUNT || options.region !== REGION) {
    throw new SetupError('Unexpected AWS destination; no credentials were loaded.');
  }
  const identity = object(await dependencies.aws(['sts', 'get-caller-identity']));
  if (identity.Account !== options.account) {
    throw new SetupError('The authenticated AWS account does not match the Toir account; no credentials were loaded.');
  }
  const secret = await readSecret(dependencies.aws, 'scalekit', [
    'SCALEKIT_ENVIRONMENT_URL', 'SCALEKIT_CLIENT_ID', 'SCALEKIT_CLIENT_SECRET',
    'SCALEKIT_CONNECTION_NAME', 'SCALEKIT_ACCOUNT_ID',
  ]);
  if (secret.SCALEKIT_CONNECTION_NAME !== 'exa' || secret.SCALEKIT_ACCOUNT_ID !== 'toir') {
    throw new SetupError('This helper only manages connection exa and account toir. Correct those fields in the Scalekit secret first.');
  }
  let url: URL;
  try { url = new URL(secret.SCALEKIT_ENVIRONMENT_URL); }
  catch { throw new SetupError('SCALEKIT_ENVIRONMENT_URL must be a valid HTTPS environment URL.'); }
  if (url.protocol !== 'https:' || url.username || url.password || url.search || url.hash) {
    throw new SetupError('SCALEKIT_ENVIRONMENT_URL must use HTTPS without embedded credentials, query or fragment.');
  }
  const client = await call(async () => dependencies.client(secret), 'Could not initialize Scalekit; check the stored environment URL and credentials.');
  const connections = await collect(async pageToken => {
    const response = await call(() => client.connection.listAppConnections({ pageSize: 30, pageToken }),
      `Could not list Scalekit app connections. Check API credential permissions. ${DASHBOARD_STEP}`);
    return { items: response.connections, nextPageToken: response.nextPageToken };
  });
  const matches = connections.filter(connection => connection.keyId === 'exa');
  if (matches.length > 1) throw new SetupError('Multiple exa connections matched; inspect the Scalekit dashboard before retrying.');
  let connection: { keyId?: string; providerKey: string; type: ConnectionType } | undefined = matches[0];
  if (!connection && options.check) {
    throw new SetupError(`The exa connection is missing. Run setup without --check or create it in the dashboard. ${DASHBOARD_STEP}`);
  }
  const providers = await collect(async pageToken => {
    const response = await call(() => client.actions.providers.listProviders({ pageSize: 30, pageToken }),
      `Could not discover the built-in Exa provider. Check Scalekit permissions. ${DASHBOARD_STEP}`);
    return { items: response.providers, nextPageToken: response.nextPageToken };
  });
  const exaProviders = providers.filter(provider =>
    provider.displayName.toLowerCase() === 'exa' || provider.identifier.toLowerCase() === 'exa');
  if (exaProviders.length !== 1 || !exaProviders[0].authPatterns.some(pattern => pattern.type === 'API_KEY')) {
    throw new SetupError(`The built-in Exa API-key provider was not uniquely available. ${DASHBOARD_STEP}`);
  }
  const providerKey = exaProviders[0].identifier;
  if (connection && (connection.providerKey !== providerKey || connection.type !== ConnectionType.API_KEY)) {
    throw new SetupError('The existing exa connection is not the built-in Exa API-key connection; it was not changed.');
  }
  if (options.check) {
    const status = await accountStatus(client);
    if (status !== ConnectorStatus.ACTIVE) {
      throw new SetupError('The Toir Exa connected account is missing or not ACTIVE. Run setup without --check to apply the stored Exa key.');
    }
    return 'Toir Exa connection is ACTIVE. No account changes or search calls were made.';
  }
  // Validate all operator inputs before the first Scalekit mutation.
  const exa = await readSecret(dependencies.aws, 'exa', ['EXA_API_KEY']);
  if (!connection) {
    const created = await call(() => client.connection.createEnvironmentConnection({
      providerKey, type: ConnectionType.API_KEY, keyId: 'exa',
    }, { isApp: true }),
    `Could not create the Exa connection. Creation requires workspace credentials with sso:write. ${DASHBOARD_STEP} Then rerun setup.`);
    connection = created.connection;
    if (!connection || connection.keyId !== 'exa' || connection.providerKey !== providerKey || connection.type !== ConnectionType.API_KEY) {
      throw new SetupError('Scalekit returned an unexpected connection; inspect the dashboard before retrying. No account credential was sent.');
    }
  }
  // Refuse an ambiguous account list before upsert; unrelated identifiers are never targeted.
  await accountStatus(client);
  const authorizationDetails = { details: {
    case: 'staticAuth' as const, value: { details: { api_key: exa.EXA_API_KEY } },
  } };
  for (let attempt = 0; attempt < 2; attempt += 1) {
    await call(() => client.actions.upsertConnectedAccount({
      connectionName: 'exa', identifier: 'toir', authorizationDetails,
    }), 'Could not register the Toir Exa account. Verify the stored Exa key and Scalekit API permissions; provider error details were not logged.');
    if (await accountStatus(client) === ConnectorStatus.ACTIVE) {
      return 'Toir Exa connection is ACTIVE. No search call was made. Run manage.py secret-sync --restart to activate server credentials.';
    }
  }
  throw new SetupError('The Toir Exa account is not ACTIVE after setup. Inspect AgentKit > Connections > exa > Connected Accounts and verify the Exa key.');
}

if (import.meta.main) {
  try {
    const options = parseOptions(process.argv.slice(2));
    if (!options) {
      console.log('Usage: bun tooling/scalekit-setup.ts [--account 904469541651] [--region us-east-1] [--check]\nReads project AWS secrets in memory. --check performs metadata reads only and does not load the Exa key.');
    } else {
      console.log(await setupToir(options, {
        aws: awsJson(options.region),
        client: secret => new ScalekitClient(secret.SCALEKIT_ENVIRONMENT_URL, secret.SCALEKIT_CLIENT_ID,
          secret.SCALEKIT_CLIENT_SECRET, { timeoutMs: 20_000 }),
      }));
    }
  } catch (error) {
    console.error(error instanceof SetupError ? error.message : 'Scalekit setup failed; error details were withheld to protect credentials.');
    process.exitCode = 1;
  }
}
