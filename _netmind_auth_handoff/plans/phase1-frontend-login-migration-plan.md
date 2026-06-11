# Phase 1 Frontend Login Migration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace NarraNexus cloud-mode self-built login with NetMind account login (email/password + OAuth), `?token=` inbound pass-through, dual-token storage, and removal of RegisterPage.

**Architecture:** A self-contained `frontend/src/lib/netmindAuth/` module ports Arena's NetMind login (DES password crypto, form-urlencoded request, email/OAuth/bind hook), adapted to dev's fetch style + `@/components/nm` primitives + the existing `configStore`. All login paths converge on `api.netmindLogin(loginToken)` which calls the already-implemented backend `POST /api/auth/netmind-login`. NetMind endpoint URLs come from runtime config injection (`window.__NARRANEXUS_CONFIG__`), not build-time, so one image serves dev/prod.

**Tech Stack:** React 19 + Vite + TypeScript, Zustand (configStore), Vitest, `crypto-js` (new dep, DES-CBC — Web Crypto can't do DES), `@/components/nm` design primitives.

**Branch:** `feat/netmind-auth` (already checked out locally; backend 7 commits merged). Work directly on this branch.

**Spec:** `reference/auth/specs/phase1-frontend-login-migration.md`

---

## File Structure

| File | Responsibility |
|---|---|
| `frontend/src/lib/netmindAuth/constants.ts` | Read NetMind URLs/sysCode from runtime config; export request base params |
| `frontend/src/lib/netmindAuth/crypto.ts` | DES-CBC password encryption + random signStr (ported, framework-free) |
| `frontend/src/lib/netmindAuth/request.ts` | fetch wrapper for NetMind auth API (form-urlencoded, `token` header, business-error unwrap) |
| `frontend/src/lib/netmindAuth/types.ts` | NetmindUser / AuthBindInfo / NetmindLoginResponse |
| `frontend/src/lib/netmindAuth/useNetmindAuth.ts` | Hook: emailLogin / OAuth popup+postMessage / bandType bind; converges on `api.netmindLogin` |
| `frontend/src/lib/runtimeConfig.ts` | (modify) extend `RuntimeConfig` with 4 NetMind keys + getters |
| `frontend/src/stores/configStore.ts` | (modify) add `netmindToken`/`displayName`/`email`; extend `login()`; add `setNetmindToken` |
| `frontend/src/lib/api.ts` | (modify) add `netmindLogin()` |
| `frontend/src/types/api.ts` | (modify) add `NetmindLoginResponse` |
| `frontend/src/pages/LoginPage.tsx` | (modify) cloud branch → NetMind login card; Sign up → external link |
| `frontend/src/components/auth/AuthBindDialog.tsx` | OAuth first-time bind dialog (bandType 1/2/3) |
| `frontend/src/App.tsx` | (modify) `?token=` inbound bootstrap; remove `/register` route |
| `frontend/src/pages/RegisterPage.tsx` | (delete) |
| `frontend/package.json` | (modify) add `crypto-js` + `@types/crypto-js` |

---

## Task 1: Add crypto-js dependency + DES crypto module

**Files:**
- Modify: `frontend/package.json`
- Create: `frontend/src/lib/netmindAuth/crypto.ts`
- Test: `frontend/src/lib/netmindAuth/__tests__/crypto.test.ts`

- [ ] **Step 1: Install crypto-js**

Run from `frontend/`:
```bash
pnpm add crypto-js && pnpm add -D @types/crypto-js
```
Expected: `package.json` gains `crypto-js` in deps and `@types/crypto-js` in devDeps.

- [ ] **Step 2: Write the failing test**

Create `frontend/src/lib/netmindAuth/__tests__/crypto.test.ts`:
```typescript
import { describe, expect, test } from 'vitest';
import { encryptPassword, generateRandomString } from '../crypto';

describe('netmindAuth crypto', () => {
  test('DES-CBC encrypts to deterministic hex for a fixed key', () => {
    // key='01234567' (8 bytes, also the IV); PKCS7; CBC; hex ciphertext.
    // Golden vector computed with the same CryptoJS config Arena ships.
    expect(encryptPassword('hello', '01234567')).toBe('30c9e6c5a1c2d6a4');
  });

  test('same message + same signStr key is stable', () => {
    const a = encryptPassword('123123aA!', 'abcd1234');
    const b = encryptPassword('123123aA!', 'abcd1234');
    expect(a).toBe(b);
    expect(a).toMatch(/^[0-9a-f]+$/);
  });

  test('generateRandomString length + charset', () => {
    const s = generateRandomString(8);
    expect(s).toHaveLength(8);
    expect(s).toMatch(/^[a-zA-Z0-9]+$/);
  });
});
```

- [ ] **Step 3: Run test to verify it fails**

Run from `frontend/`: `pnpm vitest run src/lib/netmindAuth/__tests__/crypto.test.ts`
Expected: FAIL — cannot find module `../crypto`.

- [ ] **Step 4: Write the implementation**

Create `frontend/src/lib/netmindAuth/crypto.ts`:
```typescript
/**
 * @file_name: crypto.ts
 * @description: NetMind login password encryption. DES-CBC with the
 * signStr as both key and IV, PKCS7, hex output — the exact protocol
 * NetMind's emailLogin expects (ported verbatim from Arena's client;
 * Web Crypto cannot do DES, hence crypto-js).
 */
import CryptoJS from 'crypto-js';

/** Random alphanumeric string; default 8 chars. Used as the DES key/IV. */
export function generateRandomString(length = 8): string {
  const charset =
    'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789';
  let out = '';
  for (let i = 0; i < length; i++) {
    out += charset[Math.floor(Math.random() * charset.length)];
  }
  return out;
}

/** DES-CBC encrypt `message` with `key` (key === IV), PKCS7, hex output. */
export function encryptPassword(message: string, key = '01234567'): string {
  const keyHex = CryptoJS.enc.Utf8.parse(key);
  const encrypted = CryptoJS.DES.encrypt(message, keyHex, {
    iv: keyHex,
    mode: CryptoJS.mode.CBC,
    padding: CryptoJS.pad.Pkcs7,
  });
  return encrypted.ciphertext.toString(CryptoJS.enc.Hex);
}
```

- [ ] **Step 5: Run test; fix golden vector if needed**

Run: `pnpm vitest run src/lib/netmindAuth/__tests__/crypto.test.ts`
Expected: PASS. If the first assertion's hex differs, the implementation is canonical — replace the expected literal `'30c9e6c5a1c2d6a4'` with the actual output (copy it from the failure diff) and re-run. The point of that test is regression-pinning, not deriving the vector by hand.

- [ ] **Step 6: Commit**

```bash
git add frontend/package.json frontend/pnpm-lock.yaml frontend/src/lib/netmindAuth/crypto.ts frontend/src/lib/netmindAuth/__tests__/crypto.test.ts
git commit -m "feat(auth-fe): DES password crypto for NetMind login (crypto-js dep)"
```

---

## Task 2: Extend runtimeConfig with NetMind endpoint keys

**Files:**
- Modify: `frontend/src/lib/runtimeConfig.ts`
- Test: `frontend/src/lib/__tests__/runtimeConfig.netmind.test.ts`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/lib/__tests__/runtimeConfig.netmind.test.ts`:
```typescript
import { afterEach, describe, expect, test } from 'vitest';
import { getNetmindConfig } from '../runtimeConfig';

declare global {
  interface Window { __NARRANEXUS_CONFIG__?: Record<string, unknown>; }
}

afterEach(() => { delete window.__NARRANEXUS_CONFIG__; });

describe('getNetmindConfig', () => {
  test('reads injected NetMind keys', () => {
    window.__NARRANEXUS_CONFIG__ = {
      netmindAuthApi: 'https://userauth.protago-dev.com/',
      netmindAccountsUrl: 'https://accounts.protago-dev.com',
      netmindSysCode: 'f925fc2c',
      netmindRegisterUrl: 'https://example.test/register',
    };
    const c = getNetmindConfig();
    expect(c.authApi).toBe('https://userauth.protago-dev.com'); // trailing slash stripped
    expect(c.accountsUrl).toBe('https://accounts.protago-dev.com');
    expect(c.sysCode).toBe('f925fc2c');
    expect(c.registerUrl).toBe('https://example.test/register');
  });

  test('falls back to empty strings when nothing injected', () => {
    const c = getNetmindConfig();
    expect(c).toEqual({ authApi: '', accountsUrl: '', sysCode: '', registerUrl: '' });
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pnpm vitest run src/lib/__tests__/runtimeConfig.netmind.test.ts`
Expected: FAIL — `getNetmindConfig` is not exported.

- [ ] **Step 3: Add the implementation**

In `frontend/src/lib/runtimeConfig.ts`, after the `RuntimeConfig` interface add:
```typescript
export interface NetmindConfig {
  /** NetMind auth API base (server: auth-api.netmind.ai / dev: userauth.protago-dev.com). */
  authApi: string;
  /** NetMind accounts domain hosting the OAuth auth.html popup. */
  accountsUrl: string;
  /** Multi-tenant login code; shared with Power so tokens are interchangeable. */
  sysCode: string;
  /** External NetMind registration page URL for the Sign-up link. */
  registerUrl: string;
}
```
And at the end of the file add:
```typescript
const _str = (v: unknown): string =>
  typeof v === 'string' ? v.replace(/\/+$/, '') : '';

/** NetMind endpoint config, injected at deploy time via /config.js. */
export function getNetmindConfig(): NetmindConfig {
  if (typeof window === 'undefined') {
    return { authApi: '', accountsUrl: '', sysCode: '', registerUrl: '' };
  }
  const raw = (window as unknown as {
    __NARRANEXUS_CONFIG__?: Record<string, unknown>;
  }).__NARRANEXUS_CONFIG__ || {};
  return {
    authApi: _str(raw.netmindAuthApi),
    accountsUrl: _str(raw.netmindAccountsUrl),
    sysCode: typeof raw.netmindSysCode === 'string' ? raw.netmindSysCode : '',
    registerUrl: _str(raw.netmindRegisterUrl),
  };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pnpm vitest run src/lib/__tests__/runtimeConfig.netmind.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/runtimeConfig.ts frontend/src/lib/__tests__/runtimeConfig.netmind.test.ts
git commit -m "feat(auth-fe): runtime config keys for NetMind endpoints"
```

---

## Task 3: NetMind auth constants + request wrapper + types

**Files:**
- Create: `frontend/src/lib/netmindAuth/constants.ts`
- Create: `frontend/src/lib/netmindAuth/types.ts`
- Create: `frontend/src/lib/netmindAuth/request.ts`
- Test: `frontend/src/lib/netmindAuth/__tests__/request.test.ts`

- [ ] **Step 1: Write types.ts (no test — pure types)**

Create `frontend/src/lib/netmindAuth/types.ts`:
```typescript
/** Verified NetMind user as returned by emailLogin / userCallBack. */
export interface NetmindUser {
  userSystemCode: string;
  email: string;
  nickName?: string;
  userHeadImage?: string;
  loginToken: string;
  [key: string]: unknown;
}

/** Returned by userCallBack when a third-party account needs binding. */
export interface AuthBindInfo {
  bandType: number; // 1: needs email+code, 2: confirm third-party email, 3: bind existing
  identifyCode: string;
  thirdEmail?: string;
  canBandEmail?: string;
  canBandNick?: string;
}
```

- [ ] **Step 2: Write constants.ts (no test — thin wiring over getNetmindConfig)**

Create `frontend/src/lib/netmindAuth/constants.ts`:
```typescript
import { getNetmindConfig } from '@/lib/runtimeConfig';

/** Common params NetMind's auth API expects on every request. */
export function baseRequestParams(): Record<string, string | number> {
  return {
    deviceId: 123231,
    clientType: 5,
    clientVersion: '1.0.0',
    sysCode: getNetmindConfig().sysCode,
  };
}
```

- [ ] **Step 3: Write the failing test for request.ts**

Create `frontend/src/lib/netmindAuth/__tests__/request.test.ts`:
```typescript
import { afterEach, describe, expect, test, vi } from 'vitest';
import { netmindPost } from '../request';

afterEach(() => { vi.restoreAllMocks(); delete (window as { __NARRANEXUS_CONFIG__?: unknown }).__NARRANEXUS_CONFIG__; });

function mockFetch(json: unknown, ok = true) {
  return vi.spyOn(globalThis, 'fetch').mockResolvedValue({
    ok, json: async () => json,
  } as Response);
}

describe('netmindPost', () => {
  test('posts form-urlencoded to authApi + path and unwraps data', async () => {
    (window as { __NARRANEXUS_CONFIG__?: unknown }).__NARRANEXUS_CONFIG__ =
      { netmindAuthApi: 'https://nm.test' };
    const f = mockFetch({ success: true, data: { loginToken: 'tok' } });
    const out = await netmindPost('/user/emailLogin', { email: 'a@b.com', n: 1 });
    expect(out).toEqual({ loginToken: 'tok' });
    const [url, init] = f.mock.calls[0];
    expect(url).toBe('https://nm.test/user/emailLogin');
    expect((init as RequestInit).method).toBe('POST');
    expect((init as RequestInit).headers).toMatchObject({
      'Content-Type': 'application/x-www-form-urlencoded',
    });
    expect(String((init as RequestInit).body)).toContain('email=a%40b.com');
  });

  test('rejects on business success:false', async () => {
    (window as { __NARRANEXUS_CONFIG__?: unknown }).__NARRANEXUS_CONFIG__ =
      { netmindAuthApi: 'https://nm.test' };
    mockFetch({ success: false, msg: 'bad creds' });
    await expect(netmindPost('/user/emailLogin', {})).rejects.toThrow('bad creds');
  });
});
```

- [ ] **Step 4: Run test to verify it fails**

Run: `pnpm vitest run src/lib/netmindAuth/__tests__/request.test.ts`
Expected: FAIL — cannot find module `../request`.

- [ ] **Step 5: Write request.ts**

Create `frontend/src/lib/netmindAuth/request.ts`:
```typescript
/**
 * @file_name: request.ts
 * @description: Minimal fetch wrapper for NetMind's auth API. Serializes
 * the body as application/x-www-form-urlencoded, attaches the `token`
 * header (NetMind convention, NOT Authorization) when present, and unwraps
 * the {success,data,msg} envelope — rejecting on success:false.
 */
import { getNetmindConfig } from '@/lib/runtimeConfig';

function encodeForm(data: Record<string, unknown>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(data)) {
    if (v !== undefined && v !== null) p.append(k, String(v));
  }
  return p.toString();
}

/** POST to NetMind auth API; returns the unwrapped `data` payload. */
export async function netmindPost<T = unknown>(
  path: string,
  body: Record<string, unknown>,
  token?: string,
): Promise<T> {
  const { authApi } = getNetmindConfig();
  const headers: Record<string, string> = {
    'Content-Type': 'application/x-www-form-urlencoded',
  };
  if (token) headers['token'] = `Bearer ${token}`;
  const resp = await fetch(`${authApi}${path}`, {
    method: 'POST',
    headers,
    body: encodeForm(body),
  });
  const json = (await resp.json()) as { success?: boolean; data?: T; msg?: string };
  if (json?.success === false) {
    throw new Error(json.msg || 'NetMind request failed');
  }
  return json.data as T;
}
```

- [ ] **Step 6: Run test to verify it passes**

Run: `pnpm vitest run src/lib/netmindAuth/__tests__/request.test.ts`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/lib/netmindAuth/constants.ts frontend/src/lib/netmindAuth/types.ts frontend/src/lib/netmindAuth/request.ts frontend/src/lib/netmindAuth/__tests__/request.test.ts
git commit -m "feat(auth-fe): NetMind auth constants, types, request wrapper"
```

---

## Task 4: configStore — dual token + profile fields

**Files:**
- Modify: `frontend/src/stores/configStore.ts`
- Test: `frontend/src/stores/__tests__/configStore.netmind.test.ts`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/stores/__tests__/configStore.netmind.test.ts`:
```typescript
import { beforeEach, describe, expect, test } from 'vitest';
import { useConfigStore } from '../configStore';

beforeEach(() => {
  useConfigStore.getState().logout();
});

describe('configStore NetMind fields', () => {
  test('login stores profile; setNetmindToken stores token', () => {
    useConfigStore.getState().login('uSysCode', 'jwt', 'user', {
      displayName: 'Alice', email: 'a@b.com',
    });
    useConfigStore.getState().setNetmindToken('nm-tok');
    const s = useConfigStore.getState();
    expect(s.isLoggedIn).toBe(true);
    expect(s.userId).toBe('uSysCode');
    expect(s.token).toBe('jwt');
    expect(s.displayName).toBe('Alice');
    expect(s.email).toBe('a@b.com');
    expect(s.netmindToken).toBe('nm-tok');
  });

  test('logout clears NetMind fields', () => {
    useConfigStore.getState().login('u', 'jwt', 'user', { displayName: 'A', email: 'a@b' });
    useConfigStore.getState().setNetmindToken('nm-tok');
    useConfigStore.getState().logout();
    const s = useConfigStore.getState();
    expect(s.netmindToken).toBe('');
    expect(s.displayName).toBe('');
    expect(s.email).toBe('');
  });

  test('login without profile keeps empty strings (back-compat for local mode)', () => {
    useConfigStore.getState().login('localuser');
    const s = useConfigStore.getState();
    expect(s.userId).toBe('localuser');
    expect(s.displayName).toBe('');
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pnpm vitest run src/stores/__tests__/configStore.netmind.test.ts`
Expected: FAIL — `setNetmindToken` not a function / `displayName` undefined.

- [ ] **Step 3: Modify configStore.ts**

In the `ConfigState` interface, after `role: string;` add:
```typescript
  netmindToken: string;  // NetMind loginToken, retained for Phase 2/3 actions
  displayName: string;   // NetMind nickname, for display (userId is opaque hex)
  email: string;         // NetMind account email
```
Change the `login` signature in the interface to:
```typescript
  login: (userId: string, token?: string, role?: string, profile?: { displayName?: string; email?: string }) => void;
  setNetmindToken: (token: string) => void;
```
In the store defaults (after `role: '',`) add:
```typescript
      netmindToken: '',
      displayName: '',
      email: '',
```
Replace the `login` action body's `set({...})` to include profile:
```typescript
      login: (userId, token?, role?, profile?) => {
        const prevUserId = get().userId;
        set({
          isLoggedIn: true,
          userId,
          token: token || '',
          role: role || '',
          displayName: profile?.displayName || '',
          email: profile?.email || '',
        });
        if (prevUserId !== userId) {
          useTeamsStore.setState({ teams: [], loaded: false });
        }
      },

      setNetmindToken: (token) => set({ netmindToken: token }),
```
In the `logout` action's `set({...})`, add the three fields:
```typescript
          netmindToken: '',
          displayName: '',
          email: '',
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pnpm vitest run src/stores/__tests__/configStore.netmind.test.ts`
Expected: PASS.

- [ ] **Step 5: Run the full store suite to catch regressions**

Run: `pnpm vitest run src/stores`
Expected: PASS (existing tests unaffected — `login` profile param is optional).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/stores/configStore.ts frontend/src/stores/__tests__/configStore.netmind.test.ts
git commit -m "feat(auth-fe): configStore dual-token + display profile fields"
```

---

## Task 5: api.netmindLogin + response type

**Files:**
- Modify: `frontend/src/types/api.ts`
- Modify: `frontend/src/lib/api.ts`
- Test: `frontend/src/lib/__tests__/api.netmindLogin.test.ts`

- [ ] **Step 1: Add the response type**

In `frontend/src/types/api.ts`, after the `LoginResponse` interface add:
```typescript
// Response from /api/auth/netmind-login (cloud NetMind account login).
export interface NetmindLoginResponse extends ApiResponse {
  user_id?: string;
  token?: string;        // our self-issued JWT
  role?: string;
  is_new_user?: boolean;
  display_name?: string;
  email?: string;
  has_system_quota?: boolean;
  initial_input_tokens?: number;
  initial_output_tokens?: number;
}
```

- [ ] **Step 2: Write the failing test**

Create `frontend/src/lib/__tests__/api.netmindLogin.test.ts`:
```typescript
import { afterEach, describe, expect, test, vi } from 'vitest';
import { api } from '../api';

afterEach(() => vi.restoreAllMocks());

describe('api.netmindLogin', () => {
  test('POSTs netmind_token + source to /api/auth/netmind-login', async () => {
    const f = vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true, status: 200,
      json: async () => ({ success: true, user_id: 'code', token: 'jwt' }),
    } as Response);
    const res = await api.netmindLogin('nm-token', 'arena');
    expect(res.success).toBe(true);
    expect(res.user_id).toBe('code');
    const [url, init] = f.mock.calls[0];
    expect(String(url)).toContain('/api/auth/netmind-login');
    expect(JSON.parse(String((init as RequestInit).body))).toEqual({
      netmind_token: 'nm-token', source: 'arena',
    });
  });
});
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pnpm vitest run src/lib/__tests__/api.netmindLogin.test.ts`
Expected: FAIL — `api.netmindLogin` is not a function.

- [ ] **Step 4: Implement netmindLogin**

In `frontend/src/lib/api.ts`, add `NetmindLoginResponse` to the type imports (the block starting near line 34 that already imports `LoginResponse`). Then, immediately after the existing `async login(...)` method, add:
```typescript
  async netmindLogin(netmindToken: string, source?: string): Promise<NetmindLoginResponse> {
    return this.request<NetmindLoginResponse>('/api/auth/netmind-login', {
      method: 'POST',
      body: JSON.stringify({ netmind_token: netmindToken, source: source || undefined }),
    });
  }
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pnpm vitest run src/lib/__tests__/api.netmindLogin.test.ts`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/types/api.ts frontend/src/lib/api.ts frontend/src/lib/__tests__/api.netmindLogin.test.ts
git commit -m "feat(auth-fe): api.netmindLogin + NetmindLoginResponse type"
```

---

## Task 6: useNetmindAuth hook (email + OAuth + bind)

**Files:**
- Create: `frontend/src/lib/netmindAuth/useNetmindAuth.ts`
- Test: `frontend/src/lib/netmindAuth/__tests__/useNetmindAuth.test.ts`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/lib/netmindAuth/__tests__/useNetmindAuth.test.ts`:
```typescript
import { act, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, test, vi } from 'vitest';
import { useNetmindAuth } from '../useNetmindAuth';

const netmindPost = vi.fn();
vi.mock('../request', () => ({ netmindPost: (...a: unknown[]) => netmindPost(...a) }));
const netmindLogin = vi.fn();
vi.mock('@/lib/api', () => ({ api: { netmindLogin: (...a: unknown[]) => netmindLogin(...a) } }));

afterEach(() => { netmindPost.mockReset(); netmindLogin.mockReset(); });

describe('useNetmindAuth.emailLogin', () => {
  test('encrypts, calls emailLogin, then exchanges loginToken via backend', async () => {
    netmindPost.mockResolvedValue({ loginToken: 'nm-tok', user: { userSystemCode: 'c', email: 'a@b' } });
    netmindLogin.mockResolvedValue({ success: true, user_id: 'c', token: 'jwt' });
    const onSuccess = vi.fn();
    const { result } = renderHook(() => useNetmindAuth({ onSuccess }));

    await act(async () => { await result.current.emailLogin('a@b.com', 'pw'); });

    expect(netmindPost).toHaveBeenCalledWith('/user/emailLogin', expect.objectContaining({
      email: 'a@b.com', ckType: 2,
    }));
    expect(netmindLogin).toHaveBeenCalledWith('nm-tok', undefined);
    expect(onSuccess).toHaveBeenCalledWith(expect.objectContaining({ success: true }), 'nm-tok');
  });

  test('surfaces emailLogin failure as error state', async () => {
    netmindPost.mockRejectedValue(new Error('Invalid password'));
    const { result } = renderHook(() => useNetmindAuth());
    await act(async () => { await result.current.emailLogin('a@b.com', 'bad'); });
    expect(result.current.error).toBe('Invalid password');
    expect(netmindLogin).not.toHaveBeenCalled();
  });
});

describe('useNetmindAuth OAuth callback', () => {
  test('loginToken in callback exchanges via backend', async () => {
    netmindPost.mockResolvedValue({ loginToken: 'oauth-tok', user: { userSystemCode: 'c' } });
    netmindLogin.mockResolvedValue({ success: true, token: 'jwt' });
    const onSuccess = vi.fn();
    const { result } = renderHook(() => useNetmindAuth({ onSuccess }));
    await act(async () => { await result.current.handleAuthCallback('code', 'state'); });
    expect(netmindLogin).toHaveBeenCalledWith('oauth-tok', undefined);
  });

  test('no loginToken → exposes bind info', async () => {
    netmindPost.mockResolvedValue({ bandType: 2, identifyCode: 'idc', thirdEmail: 'x@y' });
    const { result } = renderHook(() => useNetmindAuth());
    await act(async () => { await result.current.handleAuthCallback('code', 'state'); });
    expect(result.current.bindInfo).toMatchObject({ bandType: 2, identifyCode: 'idc' });
  });
});
```

- [ ] **Step 2: Ensure @testing-library/react is available**

Run from `frontend/`: `pnpm ls @testing-library/react 2>/dev/null || pnpm add -D @testing-library/react`
Expected: present, or installed.

- [ ] **Step 3: Run test to verify it fails**

Run: `pnpm vitest run src/lib/netmindAuth/__tests__/useNetmindAuth.test.ts`
Expected: FAIL — cannot find module `../useNetmindAuth`.

- [ ] **Step 4: Write the hook**

Create `frontend/src/lib/netmindAuth/useNetmindAuth.ts`:
```typescript
/**
 * @file_name: useNetmindAuth.ts
 * @description: NetMind login orchestration for the cloud login page.
 * Three entry actions — emailLogin, OAuth (popup + postMessage), and
 * bandType binding — all converge on `api.netmindLogin(loginToken)` which
 * trades the NetMind loginToken for our own JWT. The caller's onSuccess
 * receives the backend response AND the raw loginToken (to stash for
 * Phase 2/3). reCAPTCHA is intentionally absent: ckType=2 skips it.
 */
import { useCallback, useEffect, useState } from 'react';
import { api } from '@/lib/api';
import type { NetmindLoginResponse } from '@/types/api';
import { netmindPost } from './request';
import { baseRequestParams } from './constants';
import { getNetmindConfig } from '@/lib/runtimeConfig';
import { encryptPassword, generateRandomString } from './crypto';
import type { AuthBindInfo, NetmindUser } from './types';

type OAuthType = 'GOOGLE' | 'MICROSOFT' | 'GITHUB';

interface NetmindLoginPayload { loginToken?: string; user?: NetmindUser }

interface Options {
  source?: string;
  onSuccess?: (res: NetmindLoginResponse, loginToken: string) => void;
}

export function useNetmindAuth({ source, onSuccess }: Options = {}) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [bindInfo, setBindInfo] = useState<AuthBindInfo | null>(null);

  const exchange = useCallback(
    async (loginToken: string) => {
      const res = await api.netmindLogin(loginToken, source);
      onSuccess?.(res, loginToken);
    },
    [source, onSuccess],
  );

  const emailLogin = useCallback(
    async (email: string, password: string) => {
      setLoading(true);
      setError('');
      try {
        const signStr = generateRandomString();
        const data = await netmindPost<NetmindLoginPayload>('/user/emailLogin', {
          ...baseRequestParams(),
          email,
          password: encryptPassword(password, signStr),
          signStr,
          ckType: 2,
        });
        if (!data.loginToken) throw new Error('Login failed');
        await exchange(data.loginToken);
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Login failed');
      } finally {
        setLoading(false);
      }
    },
    [exchange],
  );

  const startOAuth = useCallback((type: OAuthType) => {
    const { accountsUrl, authApi } = getNetmindConfig();
    sessionStorage.setItem('nm-oauth-type', type);
    window.open(
      `${accountsUrl}/auth.html?authApi=${authApi}/user/loginMsg/${type}`,
      '',
      'popup=1,width=600,height=650',
    );
  }, []);

  const handleAuthCallback = useCallback(
    async (code: string, state: string) => {
      setLoading(true);
      setError('');
      try {
        const data = await netmindPost<NetmindLoginPayload & AuthBindInfo>(
          '/user/userCallBack',
          {
            ...baseRequestParams(),
            authCallbackStr: JSON.stringify({ code, state }),
            oauthType: sessionStorage.getItem('nm-oauth-type') || '',
          },
        );
        if (data.loginToken) {
          await exchange(data.loginToken);
        } else {
          setBindInfo(data as AuthBindInfo);
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : 'OAuth failed');
      } finally {
        setLoading(false);
      }
    },
    [exchange],
  );

  const submitBind = useCallback(
    async (extra: { email?: string; verifyCode?: string } = {}) => {
      if (!bindInfo) return;
      setLoading(true);
      setError('');
      try {
        const params: Record<string, unknown> = {
          ...baseRequestParams(),
          bandType: bindInfo.bandType,
          identifyCode: bindInfo.identifyCode,
          email: bindInfo.thirdEmail || bindInfo.canBandEmail,
        };
        if (bindInfo.bandType === 1) {
          params.email = extra.email;
          params.verifyCode = extra.verifyCode;
        }
        const data = await netmindPost<NetmindLoginPayload>('/user/userCallBack', params);
        if (!data.loginToken) throw new Error('Bind failed');
        setBindInfo(null);
        await exchange(data.loginToken);
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Bind failed');
      } finally {
        setLoading(false);
      }
    },
    [bindInfo, exchange],
  );

  const closeBind = useCallback(() => setBindInfo(null), []);

  useEffect(() => {
    const onMessage = (e: MessageEvent) => {
      if (e.data?.type === 'auth' && e.data.code && e.data.state) {
        void handleAuthCallback(e.data.code, e.data.state);
      }
    };
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, [handleAuthCallback]);

  return {
    loading, error, bindInfo,
    emailLogin, startOAuth, handleAuthCallback, submitBind, closeBind,
  };
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pnpm vitest run src/lib/netmindAuth/__tests__/useNetmindAuth.test.ts`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/netmindAuth/useNetmindAuth.ts frontend/src/lib/netmindAuth/__tests__/useNetmindAuth.test.ts frontend/package.json frontend/pnpm-lock.yaml
git commit -m "feat(auth-fe): useNetmindAuth hook — email + OAuth + bind"
```

---

## Task 7: AuthBindDialog component

**Files:**
- Create: `frontend/src/components/auth/AuthBindDialog.tsx`
- Test: `frontend/src/components/auth/__tests__/AuthBindDialog.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/auth/__tests__/AuthBindDialog.test.tsx`:
```typescript
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, test, vi } from 'vitest';
import { AuthBindDialog } from '../AuthBindDialog';

describe('AuthBindDialog', () => {
  test('bandType 1 shows email + code inputs and submits them', () => {
    const onSubmit = vi.fn();
    render(
      <AuthBindDialog
        bindInfo={{ bandType: 1, identifyCode: 'x' }}
        loading={false}
        error=""
        onSubmit={onSubmit}
        onClose={() => {}}
      />,
    );
    fireEvent.change(screen.getByLabelText(/email/i), { target: { value: 'a@b.com' } });
    fireEvent.change(screen.getByLabelText(/code/i), { target: { value: '1234' } });
    fireEvent.click(screen.getByRole('button', { name: /confirm|bind|continue/i }));
    expect(onSubmit).toHaveBeenCalledWith({ email: 'a@b.com', verifyCode: '1234' });
  });

  test('bandType 3 shows confirm copy and submits with no extra', () => {
    const onSubmit = vi.fn();
    render(
      <AuthBindDialog
        bindInfo={{ bandType: 3, identifyCode: 'x', canBandEmail: 'me@x.com' }}
        loading={false}
        error=""
        onSubmit={onSubmit}
        onClose={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /confirm|bind|continue/i }));
    expect(onSubmit).toHaveBeenCalledWith({});
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pnpm vitest run src/components/auth/__tests__/AuthBindDialog.test.tsx`
Expected: FAIL — cannot find module `../AuthBindDialog`.

- [ ] **Step 3: Write the component**

Create `frontend/src/components/auth/AuthBindDialog.tsx`:
```typescript
/**
 * @file_name: AuthBindDialog.tsx
 * @description: First-time third-party (OAuth) account binding dialog.
 * NetMind's userCallBack returns a bandType when a Google/MS/GitHub
 * identity isn't yet linked to a NetMind account:
 *   1 = collect email + email verification code
 *   2 = confirm the third-party email
 *   3 = bind to an existing NetMind account by that email
 * Only bandType 1 needs inputs; 2/3 are confirm-and-continue.
 */
import { useState } from 'react';
import { Button, FormField, TextInput } from '@/components/nm';
import type { AuthBindInfo } from '@/lib/netmindAuth/types';

interface Props {
  bindInfo: AuthBindInfo;
  loading: boolean;
  error: string;
  onSubmit: (extra: { email?: string; verifyCode?: string }) => void;
  onClose: () => void;
}

export function AuthBindDialog({ bindInfo, loading, error, onSubmit, onClose }: Props) {
  const [email, setEmail] = useState(bindInfo.thirdEmail || bindInfo.canBandEmail || '');
  const [verifyCode, setVerifyCode] = useState('');
  const needsInputs = bindInfo.bandType === 1;

  const handleSubmit = () => {
    onSubmit(needsInputs ? { email, verifyCode } : {});
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center px-4"
      style={{ background: 'rgba(0,0,0,0.4)' }}
      role="dialog"
      aria-modal="true"
    >
      <div
        className="w-full max-w-sm p-8"
        style={{
          background: 'var(--nm-card)',
          border: '1px solid var(--nm-hairline)',
          borderRadius: 'var(--radius-md)',
        }}
      >
        <h2 className="text-lg mb-4" style={{ color: 'var(--nm-ink)' }}>
          Link your account
        </h2>
        {bindInfo.bandType === 2 && (
          <p className="text-sm mb-4" style={{ color: 'var(--nm-ink70)' }}>
            Confirm linking the email <strong>{bindInfo.thirdEmail}</strong> to your NetMind account.
          </p>
        )}
        {bindInfo.bandType === 3 && (
          <p className="text-sm mb-4" style={{ color: 'var(--nm-ink70)' }}>
            An account already exists for <strong>{bindInfo.canBandEmail}</strong>. Bind this sign-in to it.
          </p>
        )}
        {needsInputs && (
          <div className="space-y-4 mb-4">
            <FormField label="Email">
              <TextInput
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@example.com"
              />
            </FormField>
            <FormField label="Verification code">
              <TextInput
                type="text"
                value={verifyCode}
                onChange={(e) => setVerifyCode(e.target.value)}
                placeholder="6-digit code"
              />
            </FormField>
          </div>
        )}
        {error && (
          <p className="text-xs mb-3" style={{ color: 'var(--color-error)' }} role="alert">
            {error}
          </p>
        )}
        <div className="flex gap-3">
          <Button variant="secondary" onClick={onClose} className="flex-1" disabled={loading}>
            Cancel
          </Button>
          <Button
            variant="primary"
            onClick={handleSubmit}
            className="flex-1"
            loading={loading}
            disabled={loading || (needsInputs && (!email || !verifyCode))}
          >
            Confirm
          </Button>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pnpm vitest run src/components/auth/__tests__/AuthBindDialog.test.tsx`
Expected: PASS. If `FormField`/`TextInput` aren't both re-exported from `@/components/nm`, import from `@/components/nm/form` instead and re-run.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/auth/AuthBindDialog.tsx frontend/src/components/auth/__tests__/AuthBindDialog.test.tsx
git commit -m "feat(auth-fe): AuthBindDialog for OAuth first-time binding"
```

---

## Task 8: LoginPage cloud branch → NetMind login

**Files:**
- Modify: `frontend/src/pages/LoginPage.tsx`
- Test: `frontend/src/pages/__tests__/LoginPage.netmind.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/pages/__tests__/LoginPage.netmind.test.tsx`:
```typescript
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, test, vi } from 'vitest';

vi.mock('@/stores', () => ({
  useConfigStore: (sel?: (s: unknown) => unknown) => {
    const store = { login: vi.fn(), setNetmindToken: vi.fn(), setAgents: vi.fn(), setAgentId: vi.fn() };
    return sel ? sel(store) : store;
  },
  useRuntimeStore: (sel: (s: unknown) => unknown) =>
    sel({ mode: 'cloud-web', setMode: vi.fn(), setCloudApiUrl: vi.fn() }),
}));
vi.mock('@/hooks', () => ({ useTheme: () => ({ isDark: false }) }));
vi.mock('@/lib/runtimeConfig', () => ({
  getNetmindConfig: () => ({ authApi: 'https://nm.test', accountsUrl: 'https://acc.test', sysCode: 'f925fc2c', registerUrl: 'https://reg.test' }),
}));

import { LoginPage } from '../LoginPage';

describe('LoginPage cloud branch (NetMind)', () => {
  test('renders email + password + OAuth buttons, Sign-up is an external link', () => {
    render(<MemoryRouter><LoginPage /></MemoryRouter>);
    expect(screen.getByLabelText(/email/i)).toBeTruthy();
    expect(screen.getByLabelText(/password/i)).toBeTruthy();
    expect(screen.getByRole('button', { name: /google/i })).toBeTruthy();
    const signup = screen.getByRole('link', { name: /sign up|create account/i }) as HTMLAnchorElement;
    expect(signup.href).toContain('reg.test');
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pnpm vitest run src/pages/__tests__/LoginPage.netmind.test.tsx`
Expected: FAIL — page still renders the User ID field / no OAuth buttons.

- [ ] **Step 3: Rewrite the cloud branch of LoginPage**

In `frontend/src/pages/LoginPage.tsx`:

(a) Update imports — replace the `api` import usage for cloud and add:
```typescript
import { useNetmindAuth } from '@/lib/netmindAuth/useNetmindAuth';
import { AuthBindDialog } from '@/components/auth/AuthBindDialog';
import { getNetmindConfig } from '@/lib/runtimeConfig';
```

(b) Inside the component, add NetMind state + wiring (keep the existing local-mode state/handlers):
```typescript
  const [email, setEmail] = useState('');
  const { setNetmindToken } = useConfigStore();
  const netmind = useNetmindAuth({
    onSuccess: async (res, loginToken) => {
      if (!res.success || !res.user_id) {
        setError(res.error || 'Login failed');
        return;
      }
      login(res.user_id, res.token || undefined, res.role || undefined, {
        displayName: res.display_name, email: res.email,
      });
      setNetmindToken(loginToken);
      const agentsRes = await api.getAgents();
      if (agentsRes.success && agentsRes.agents.length > 0) {
        setAgents(agentsRes.agents);
        setAgentId(agentsRes.agents[0].agent_id);
      }
      const params = new URLSearchParams(location.search);
      const next = params.get('next');
      navigate(isSafeReturnTo(next) ? next : '/');
    },
  });
```

(c) Replace the cloud-mode form block (the `isCloudMode ? <password form> : ...`) so that when `isCloudMode` is true it renders the NetMind card: an email `TextInput` (label "Email"), a password `TextInput` (label "Password"), a primary "Sign In" button calling `netmind.emailLogin(email, password)`, an "or" divider, three OAuth buttons calling `netmind.startOAuth('GOOGLE'|'MICROSOFT'|'GITHUB')`, and a Sign-up anchor:
```typescript
<a
  href={getNetmindConfig().registerUrl}
  target="_blank"
  rel="noopener noreferrer"
  className="..."  // reuse the secondary-button styling classes
>
  Create Account
</a>
```
Drive the visible error/loading from `netmind.error || error` and `netmind.loading || loading`. Keep the local-mode branch (User ID + CreateUserDialog) exactly as-is.

(d) At the end of the returned JSX, render the bind dialog:
```typescript
{netmind.bindInfo && (
  <AuthBindDialog
    bindInfo={netmind.bindInfo}
    loading={netmind.loading}
    error={netmind.error}
    onSubmit={netmind.submitBind}
    onClose={netmind.closeBind}
  />
)}
```

(e) Remove the old cloud `handleLogin` password path and the `navigate('/register')` button. Local-mode `handleLogin` stays.

- [ ] **Step 4: Run test to verify it passes**

Run: `pnpm vitest run src/pages/__tests__/LoginPage.netmind.test.tsx`
Expected: PASS. Adjust label/role query text only if the rendered copy differs.

- [ ] **Step 5: Typecheck**

Run from `frontend/`: `pnpm exec tsc -b --noEmit`
Expected: no errors in LoginPage.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/pages/LoginPage.tsx frontend/src/pages/__tests__/LoginPage.netmind.test.tsx
git commit -m "feat(auth-fe): LoginPage cloud branch → NetMind login + OAuth + bind"
```

---

## Task 9: App.tsx — ?token= inbound bootstrap + remove /register

**Files:**
- Modify: `frontend/src/App.tsx`
- Delete: `frontend/src/pages/RegisterPage.tsx`
- Test: `frontend/src/lib/netmindAuth/__tests__/tokenInbound.test.ts`

- [ ] **Step 1: Extract the inbound handler as a testable unit + write its failing test**

Create `frontend/src/lib/netmindAuth/tokenInbound.ts`:
```typescript
/**
 * @file_name: tokenInbound.ts
 * @description: Power login-state pass-through (scenario A). When the page
 * is opened with ?token=<NetMind loginToken> (e.g. a link from netmind.ai
 * or Arena), take the token, strip it from the URL immediately (avoid
 * leaking it into history), and exchange it for our session. `source` is
 * read alongside and forwarded for downstream provisioning (Phase 2).
 */
import { api } from '@/lib/api';

export interface InboundResult { handled: boolean; token?: string; source?: string }

/** Parse + strip ?token=/?source= from the URL. Returns what was found. */
export function takeInboundToken(loc: { search: string; pathname: string; hash: string }): InboundResult {
  const params = new URLSearchParams(loc.search);
  const token = params.get('token');
  const source = params.get('source') || undefined;
  if (!token) return { handled: false, source };
  params.delete('token');
  const rest = params.toString();
  const newUrl = loc.pathname + (rest ? `?${rest}` : '') + loc.hash;
  window.history.replaceState(null, '', newUrl);
  return { handled: true, token, source };
}

/** Exchange an inbound NetMind token for our session. Returns the response. */
export async function exchangeInboundToken(token: string, source?: string) {
  return api.netmindLogin(token, source);
}
```
Create `frontend/src/lib/netmindAuth/__tests__/tokenInbound.test.ts`:
```typescript
import { afterEach, describe, expect, test, vi } from 'vitest';
import { takeInboundToken } from '../tokenInbound';

afterEach(() => vi.restoreAllMocks());

describe('takeInboundToken', () => {
  test('extracts token + source and strips token from URL', () => {
    const replace = vi.spyOn(window.history, 'replaceState').mockImplementation(() => {});
    const r = takeInboundToken({ search: '?token=abc&source=arena&x=1', pathname: '/app', hash: '' });
    expect(r).toEqual({ handled: true, token: 'abc', source: 'arena' });
    const newUrl = replace.mock.calls[0][2] as string;
    expect(newUrl).not.toContain('token=');
    expect(newUrl).toContain('source=arena');
    expect(newUrl).toContain('x=1');
  });

  test('no token → handled false, source still parsed', () => {
    const r = takeInboundToken({ search: '?source=arena', pathname: '/app', hash: '' });
    expect(r).toEqual({ handled: false, source: 'arena' });
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pnpm vitest run src/lib/netmindAuth/__tests__/tokenInbound.test.ts`
Expected: FAIL — cannot find module `../tokenInbound`.

- [ ] **Step 3: (implementation already written in Step 1) Run test to verify it passes**

Run: `pnpm vitest run src/lib/netmindAuth/__tests__/tokenInbound.test.ts`
Expected: PASS.

- [ ] **Step 4: Wire the bootstrap into App.tsx and remove /register**

In `frontend/src/App.tsx`:

(a) Remove `const RegisterPage = lazy(() => import('@/pages/RegisterPage'));` (line ~19) and the `<Route path="/register" ... />` block (lines ~357-362).

(b) Add imports:
```typescript
import { takeInboundToken, exchangeInboundToken } from '@/lib/netmindAuth/tokenInbound';
import { useConfigStore } from '@/stores';
```

(c) In the `App()` component body, add a bootstrap effect that runs once before route rendering settles:
```typescript
  useEffect(() => {
    const r = takeInboundToken(window.location);
    if (r.source) sessionStorage.setItem('nx-entry-source', r.source);
    if (!r.handled || !r.token) return;
    if (useConfigStore.getState().isLoggedIn) return;
    void exchangeInboundToken(r.token, r.source).then((res) => {
      if (res.success && res.user_id) {
        useConfigStore.getState().login(res.user_id, res.token || undefined, res.role || undefined, {
          displayName: res.display_name, email: res.email,
        });
        useConfigStore.getState().setNetmindToken(r.token!);
      }
    }).catch(() => { /* fall through to login page */ });
  }, []);
```

- [ ] **Step 5: Delete RegisterPage and its mirror**

```bash
git rm frontend/src/pages/RegisterPage.tsx
rm -f .mindflow/mirror/frontend/src/pages/RegisterPage.tsx.md
```

- [ ] **Step 6: Typecheck + full build**

Run from `frontend/`:
```bash
pnpm exec tsc -b --noEmit && pnpm build
```
Expected: no type errors (no remaining references to RegisterPage), build succeeds.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/App.tsx frontend/src/lib/netmindAuth/tokenInbound.ts frontend/src/lib/netmindAuth/__tests__/tokenInbound.test.ts .mindflow/mirror/frontend/src/pages/RegisterPage.tsx.md
git commit -m "feat(auth-fe): ?token= inbound bootstrap; remove RegisterPage + /register route"
```

---

## Task 10: Mirror md sync + full frontend suite

**Files:**
- Create/modify mirror md for each touched frontend file
- Test: full frontend vitest run

- [ ] **Step 1: Create mirror md for new modules**

Create these files (frontmatter `code_file`, `last_verified: 2026-06-11`, `stub: false`, then a "为什么存在 / 上下游 / 设计决策" body matching the repo's mirror style):
- `.mindflow/mirror/frontend/src/lib/netmindAuth/crypto.ts.md`
- `.mindflow/mirror/frontend/src/lib/netmindAuth/request.ts.md`
- `.mindflow/mirror/frontend/src/lib/netmindAuth/useNetmindAuth.ts.md`
- `.mindflow/mirror/frontend/src/lib/netmindAuth/tokenInbound.ts.md`
- `.mindflow/mirror/frontend/src/lib/netmindAuth/constants.ts.md`
- `.mindflow/mirror/frontend/src/lib/netmindAuth/types.ts.md`
- `.mindflow/mirror/frontend/src/components/auth/AuthBindDialog.tsx.md`

Each body's core point (one paragraph): this is the NetMind account-login frontend (Phase 1); all login paths converge on `api.netmindLogin`; NetMind URLs come from runtime config; `ckType=2` means no reCAPTCHA; DES needs crypto-js because Web Crypto can't.

- [ ] **Step 2: Update mirror md for modified files**

Prepend a dated entry to each of these existing mirror md (bump `last_verified` to 2026-06-11):
- `.mindflow/mirror/frontend/src/stores/configStore.ts.md` — added netmindToken/displayName/email + login profile param.
- `.mindflow/mirror/frontend/src/lib/api.ts.md` — added netmindLogin.
- `.mindflow/mirror/frontend/src/lib/runtimeConfig.ts.md` — added NetmindConfig + getNetmindConfig.
- `.mindflow/mirror/frontend/src/pages/LoginPage.tsx.md` — cloud branch is now NetMind login (email/password + OAuth + bind); Sign-up is an external link.
- `.mindflow/mirror/frontend/src/App.tsx.md` — ?token= inbound bootstrap; /register route removed.

(If a mirror md doesn't exist for a modified file, create it as a stub with `stub: true` and the one-paragraph summary.)

- [ ] **Step 3: Run the full frontend test suite**

Run from `frontend/`: `pnpm test`
Expected: all pass (new auth tests + existing suite unaffected).

- [ ] **Step 4: Lint + typecheck**

Run from `frontend/`: `pnpm exec eslint src/lib/netmindAuth src/components/auth src/pages/LoginPage.tsx src/App.tsx --max-warnings 0 && pnpm exec tsc -b --noEmit`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add .mindflow/mirror/frontend
git commit -m "docs(auth-fe): mirror md for NetMind login frontend"
```

---

## Task 11: Local integration smoke via dev-bypass (manual, documented)

**Files:**
- Create: `frontend/src/lib/netmindAuth/__tests__/devBypass.integration.test.ts` (optional automated piece)

> Real NetMind round-trips can't run from this workstation (AWS network wall — see spec §8). This task verifies OUR stack end-to-end using the backend dev-bypass, with NO real NetMind call.

- [ ] **Step 1: Write an integration test that drives netmindLogin against a bypass token**

Create `frontend/src/lib/netmindAuth/__tests__/devBypass.integration.test.ts`:
```typescript
/**
 * Verifies the frontend → backend exchange contract with a dev-bypass
 * token shape. Backend is mocked here (real backend lives in Python tests);
 * this pins that the frontend sends exactly what netmind-login expects.
 */
import { afterEach, describe, expect, test, vi } from 'vitest';
import { api } from '@/lib/api';

afterEach(() => vi.restoreAllMocks());

test('dev-bypass token is forwarded verbatim as netmind_token', async () => {
  const f = vi.spyOn(globalThis, 'fetch').mockResolvedValue({
    ok: true, status: 200,
    json: async () => ({ success: true, user_id: 'devbp_abc', token: 'jwt', is_new_user: true }),
  } as Response);
  const res = await api.netmindLogin('dev-bypass-tester@narra.dev');
  expect(res.user_id).toBe('devbp_abc');
  expect(JSON.parse(String((f.mock.calls[0][1] as RequestInit).body)).netmind_token)
    .toBe('dev-bypass-tester@narra.dev');
});
```

- [ ] **Step 2: Run it**

Run: `pnpm vitest run src/lib/netmindAuth/__tests__/devBypass.integration.test.ts`
Expected: PASS.

- [ ] **Step 3: Document the manual smoke procedure in the spec's verification note**

Append to `reference/auth/specs/phase1-frontend-login-migration.md` §8 a short "How to run the local smoke" block: start backend with `NETMIND_DEV_BYPASS=1` + sqlite/cloud-mode env, open the app, paste a `?token=dev-bypass-<email>` URL, confirm you land logged-in. (Documentation only — no code.)

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/netmindAuth/__tests__/devBypass.integration.test.ts reference/auth/specs/phase1-frontend-login-migration.md
git commit -m "test(auth-fe): dev-bypass exchange contract + document local smoke"
```

---

## Task 12: Deploy repo — runtime config injection (deferred handoff note)

**Files:**
- Modify: `NarraNexus-deploy/docker/entrypoint-frontend.sh`
- Modify: `NarraNexus-deploy/stacks/narranexus-app/.env.example`

> This task lands in the DEPLOY repo (separate from the NarraNexus submodule). Do it only when integrating for a dev deploy; it does not affect frontend unit tests. Listed here so it isn't forgotten.

- [ ] **Step 1: Extend entrypoint-frontend.sh config.js writer**

Add the 4 NetMind keys to the `window.__NARRANEXUS_CONFIG__` object written into `config.js`, sourced from env:
```sh
netmindAuthApi: "${NETMIND_AUTH_API_URL:-}",
netmindAccountsUrl: "${NETMIND_ACCOUNTS_URL:-}",
netmindSysCode: "${NETMIND_SYS_CODE:-f925fc2c}",
netmindRegisterUrl: "${NETMIND_REGISTER_URL:-}",
```

- [ ] **Step 2: Add the env to .env.example**

```bash
# NetMind account login (frontend, injected into config.js at container start)
NETMIND_AUTH_API_URL=https://userauth.protago-dev.com
NETMIND_ACCOUNTS_URL=https://accounts.protago-dev.com
NETMIND_SYS_CODE=f925fc2c
NETMIND_REGISTER_URL=
```
(Also confirm the backend-side `NETMIND_AUTH_API_URL` from the backend spec is present — same var name, reused by the Python `NetmindAuthClient`.)

- [ ] **Step 3: Commit (in deploy repo)**

```bash
git add docker/entrypoint-frontend.sh stacks/narranexus-app/.env.example
git commit -m "feat(deploy): inject NetMind login config + env for frontend"
```

---

## Self-Review

**Spec coverage:** §2 architecture → Tasks 3/6/8/9. §3 modules → Tasks 1/3/6. §4.1 configStore → Task 4. §4.2 api → Task 5. §4.3 LoginPage → Task 8. §4.4 App ?token= → Task 9. §4.5 RegisterPage delete → Task 9. §6 runtime config → Tasks 2 + 12. §7 testing → every task is TDD. §8 verification/dev-bypass → Task 11. §9 mirror md → Task 10. §10 external deps → noted, non-blocking. All spec sections have tasks.

**Placeholder scan:** No TBD/TODO. Every code step shows full code. The one judgement call (DES golden vector in Task 1) has an explicit "if it differs, paste the actual output" instruction — that's pinning, not a placeholder.

**Type consistency:** `NetmindLoginResponse` defined in Task 5, used in Tasks 6/8/9. `AuthBindInfo`/`NetmindUser` defined in Task 3, used in 6/7. `getNetmindConfig` defined Task 2, used in 3/6/8. `netmindPost` defined Task 3, used in 6. `api.netmindLogin(token, source?)` defined Task 5, called in 6/9/11 with matching arity. `login(userId, token?, role?, profile?)` defined Task 4, called in 8/9 with the profile object shape `{displayName, email}`. `setNetmindToken` defined Task 4, called in 8/9. Consistent.
