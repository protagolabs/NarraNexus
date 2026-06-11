// Direct NetMind contract probe — replicates frontend emailLogin + backend /user/balance.
// DES-CBC(key=iv=signStr, PKCS7, hex) === crypto.ts encryptPassword.
//
// USAGE (the --openssl-legacy-provider flag is REQUIRED — Node 20 / OpenSSL 3
// disables single-DES by default; without it you get
// "digital envelope routines::unsupported"):
//
//   node --openssl-legacy-provider netmind_probe.mjs 13924451750@163.com 15627310563@163.com gzchao2@163.com
//
// Password is hard-coded to the shared dev test password (123123aA!).
// Override endpoint/sysCode via env: AUTH_API=... SYS_CODE=... node --openssl-legacy-provider netmind_probe.mjs <email...>
import crypto from 'node:crypto';

const AUTH_API = process.env.AUTH_API || 'https://userauth.protago-dev.com';
const SYS_CODE = process.env.SYS_CODE || 'f925fc2c';

function randStr(n = 8) {
  const cs = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789';
  let s = '';
  for (let i = 0; i < n; i++) s += cs[Math.floor(Math.random() * cs.length)];
  return s;
}
function encryptPassword(message, key) {
  const k = Buffer.from(key, 'utf8');        // 8 bytes
  const c = crypto.createCipheriv('des-cbc', k, k); // key === iv
  return Buffer.concat([c.update(message, 'utf8'), c.final()]).toString('hex');
}
function form(obj) {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(obj)) if (v != null) p.append(k, String(v));
  return p.toString();
}
const base = () => ({ deviceId: 123231, clientType: 5, clientVersion: '1.0.0', sysCode: SYS_CODE });

async function emailLogin(email, password) {
  const signStr = randStr();
  const body = form({ ...base(), email, password: encryptPassword(password, signStr), signStr, ckType: 2 });
  const r = await fetch(`${AUTH_API}/user/emailLogin`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body,
    signal: AbortSignal.timeout(15000),
  });
  const text = await r.text();
  let json; try { json = JSON.parse(text); } catch { json = { _raw: text.slice(0, 300) }; }
  return { status: r.status, json };
}
async function balance(loginToken) {
  const r = await fetch(`${AUTH_API}/user/balance`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded', token: `Bearer ${loginToken}` },
    body: form(base()),
    signal: AbortSignal.timeout(15000),
  });
  const text = await r.text();
  let json; try { json = JSON.parse(text); } catch { json = { _raw: text.slice(0, 300) }; }
  return { status: r.status, json };
}

const accounts = process.argv.slice(2);
console.log(`AUTH_API=${AUTH_API} sysCode=${SYS_CODE}\n`);
for (const email of accounts) {
  console.log(`=== ${email} ===`);
  try {
    const lg = await emailLogin(email, '123123aA!');
    const tok = lg.json?.data?.loginToken;
    console.log(`  emailLogin: HTTP ${lg.status} success=${lg.json?.success} loginToken=${tok ? tok.slice(0, 12) + '…(' + tok.length + ')' : 'NONE'} msg=${lg.json?.msg ?? ''}`);
    if (!tok) { console.log('  -> no loginToken, body:', JSON.stringify(lg.json).slice(0, 300)); continue; }
    const bal = await balance(tok);
    const u = bal.json?.data?.user || {};
    const usc = u.userSystemCode || u.user_system_code;
    console.log(`  /user/balance: HTTP ${bal.status} success=${bal.json?.success} userSystemCode=${usc || 'NONE'} email=${u.email ?? ''} nickname=${u.nickName ?? u.nickname ?? ''}`);
  } catch (e) {
    console.log(`  ERROR: ${e.name} ${e.message}`);
  }
  console.log('');
}
