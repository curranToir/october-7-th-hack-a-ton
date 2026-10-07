import { describe, expect, test } from 'bun:test';
import { ConnectionType, ConnectorStatus } from '../agents/research/node_modules/@scalekit-sdk/node';
import { parseOptions, setupToir, type SetupClient, type Options } from './scalekit-setup';

const options: Options = { account: '904469541651', region: 'us-east-1', check: false };
const settings = {
  SCALEKIT_ENVIRONMENT_URL: 'https://toir.example.scalekit.com',
  SCALEKIT_CLIENT_ID: 'test-client', SCALEKIT_CLIENT_SECRET: 'fixture-client-secret',
  SCALEKIT_CONNECTION_NAME: 'exa', SCALEKIT_ACCOUNT_ID: 'toir',
};
const connection = { keyId: 'exa', providerKey: 'EXA', type: ConnectionType.API_KEY };

function fixture(input: {
  exists?: boolean; active?: boolean; wrongProvider?: boolean; wrongAccount?: boolean;
  settings?: Record<string, string>; providerFailure?: boolean; createFailure?: boolean;
  neverActive?: boolean; extraAccounts?: unknown[];
} = {}) {
  const awsCalls: string[][] = [];
  const creates: unknown[][] = [];
  const upserts: unknown[] = [];
  let active = input.active ?? false;
  const client = {
    connection: {
      listAppConnections: async () => ({ connections: input.exists ? [
        { ...connection, providerKey: input.wrongProvider ? 'OTHER' : 'EXA' },
      ] : [], nextPageToken: '' }),
      createEnvironmentConnection: async (...args: unknown[]) => {
        creates.push(args);
        if (input.createFailure) throw new Error('vendor-response-containing-fixture-client-secret');
        return { connection };
      },
    },
    actions: {
      providers: { listProviders: async () => {
        if (input.providerFailure) throw new Error('vendor-response-containing-fixture-client-secret');
        return { providers: [{ identifier: 'EXA', displayName: 'Exa', authPatterns: [{ type: 'API_KEY' }] }], nextPageToken: '' };
      } },
      listConnectedAccounts: async () => ({ connectedAccounts: [
        ...(active ? [{ connector: 'exa', identifier: 'toir', status: ConnectorStatus.ACTIVE }] : []),
        ...(input.extraAccounts ?? []),
      ], nextPageToken: '' }),
      upsertConnectedAccount: async (value: unknown) => {
        upserts.push(value);
        active = !input.neverActive;
        return { connectedAccount: { status: active ? ConnectorStatus.ACTIVE : ConnectorStatus.PENDING_AUTH } };
      },
    },
  } as unknown as SetupClient;
  const dependencies = {
    aws: async (args: string[]) => {
      awsCalls.push(args);
      if (args[0] === 'sts') return { Account: input.wrongAccount ? '111111111111' : options.account };
      return { SecretString: JSON.stringify(args.at(-1)?.endsWith('/exa') ?
        { EXA_API_KEY: 'fixture-exa-key' } : { ...settings, ...input.settings }) };
    },
    client: () => client,
  };
  return { dependencies, awsCalls, creates, upserts };
}

describe('Scalekit operator setup', () => {
  test('creates only Exa and upserts the Toir account using native staticAuth fields', async () => {
    const f = fixture();
    expect(await setupToir(options, f.dependencies)).toContain('ACTIVE');
    expect(f.creates).toEqual([[{ providerKey: 'EXA', type: ConnectionType.API_KEY, keyId: 'exa' }, { isApp: true }]]);
    expect(f.upserts).toEqual([{ connectionName: 'exa', identifier: 'toir', authorizationDetails: {
      details: { case: 'staticAuth', value: { details: { api_key: 'fixture-exa-key' } } },
    } }]);
  });

  test('read-only check never reads the Exa secret or mutates connection/account', async () => {
    const f = fixture({ exists: true, active: true });
    expect(await setupToir({ ...options, check: true }, f.dependencies)).toContain('No account changes');
    expect(f.awsCalls.some(args => args.at(-1)?.endsWith('/exa'))).toBe(false);
    expect(f.creates).toHaveLength(0);
    expect(f.upserts).toHaveLength(0);
  });

  test('existing matching connection is reused and credentials can rotate', async () => {
    const f = fixture({ exists: true, active: true });
    await setupToir(options, f.dependencies);
    expect(f.creates).toHaveLength(0);
    expect(f.upserts).toHaveLength(1);
  });

  test('wrong AWS account stops before any secret load', async () => {
    const f = fixture({ wrongAccount: true });
    await expect(setupToir(options, f.dependencies)).rejects.toThrow('does not match');
    expect(f.awsCalls).toEqual([['sts', 'get-caller-identity']]);
  });

  test('refuses a connection belonging to a different provider', async () => {
    const f = fixture({ exists: true, wrongProvider: true });
    await expect(setupToir(options, f.dependencies)).rejects.toThrow('not the built-in Exa');
    expect(f.upserts).toHaveLength(0);
    expect(f.creates).toHaveLength(0);
  });

  test('refuses an unrelated connected-account identifier from configuration', async () => {
    const f = fixture({ settings: { SCALEKIT_ACCOUNT_ID: 'someone-else' } });
    await expect(setupToir(options, f.dependencies)).rejects.toThrow('only manages connection exa and account toir');
    expect(f.creates).toHaveLength(0);
    expect(f.upserts).toHaveLength(0);
  });

  test('does not mistake another account for Toir during check', async () => {
    const f = fixture({ exists: true, extraAccounts: [{ connector: 'exa', identifier: 'another-user', status: ConnectorStatus.ACTIVE }] });
    await expect(setupToir({ ...options, check: true }, f.dependencies)).rejects.toThrow('missing or not ACTIVE');
    expect(f.upserts).toHaveLength(0);
  });

  test('missing connection in check mode has no mutations', async () => {
    const f = fixture();
    await expect(setupToir({ ...options, check: true }, f.dependencies)).rejects.toThrow('connection is missing');
    expect(f.creates).toHaveLength(0);
    expect(f.upserts).toHaveLength(0);
  });

  test('creation permissions failure is actionable without vendor response leakage', async () => {
    const f = fixture({ createFailure: true });
    let message = '';
    try { await setupToir(options, f.dependencies); } catch (error) { message = (error as Error).message; }
    expect(message).toContain('sso:write');
    expect(message).toContain('AgentKit > Connections > Create Connection');
    expect(message).not.toContain('fixture-client-secret');
    expect(f.upserts).toHaveLength(0);
  });

  test('caps authentication registration retries if account never becomes active', async () => {
    const f = fixture({ exists: true, neverActive: true });
    await expect(setupToir(options, f.dependencies)).rejects.toThrow('not ACTIVE after setup');
    expect(f.upserts).toHaveLength(2);
  });

  test('requires HTTPS before sending Scalekit credentials', async () => {
    const f = fixture({ settings: { SCALEKIT_ENVIRONMENT_URL: 'http://example.com' } });
    await expect(setupToir(options, f.dependencies)).rejects.toThrow('must use HTTPS');
    expect(f.upserts).toHaveLength(0);
  });

  test('CLI rejects alternate destinations and never echoes unknown argument values', () => {
    expect(parseOptions([])).toEqual(options);
    expect(parseOptions(['--help'])).toBeNull();
    expect(() => parseOptions(['--region', 'us-west-2'])).toThrow('restricted');
    expect(() => parseOptions(['--key', 'fixture-exa-key'])).toThrow('Invalid arguments. Use --help.');
  });
});
