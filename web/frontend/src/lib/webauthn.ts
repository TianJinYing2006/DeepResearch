/** 需求 20 P1-2：浏览器端 Passkey（WebAuthn）适配。
 *
 * 服务端只给 base64url JSON；此处转换为 ArrayBuffer 调 `navigator.credentials`，
 * 再把结果转回 base64url 交给后端验证。协议细节不进入组件。
 */
import { csrfHeaders, readErrorMessage } from './api'
import type { SessionUser } from '../types/api'

export type PasskeyItem = {
  credential_id: string
  name: string
  aaguid: string | null
  transports: string[]
  backup_state: boolean
  device_type: string | null
  created_at: string | null
  last_used_at: string | null
}

export type PasskeyList = {
  available: boolean
  rp_id: string
  credentials: PasskeyItem[]
}

type JsonObject = Record<string, any>

export function base64urlToBuffer(value: string): ArrayBuffer {
  const normalized = value.replace(/-/g, '+').replace(/_/g, '/')
  const padded = normalized + '='.repeat((4 - (normalized.length % 4)) % 4)
  const binary = atob(padded)
  const bytes = new Uint8Array(binary.length)
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index)
  }
  return bytes.buffer
}

export function bufferToBase64url(value: ArrayBuffer | ArrayBufferView): string {
  const bytes = value instanceof Uint8Array
    ? value
    : new Uint8Array(value instanceof ArrayBuffer ? value : value.buffer)
  let binary = ''
  for (let index = 0; index < bytes.length; index += 1) {
    binary += String.fromCharCode(bytes[index])
  }
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/g, '')
}

export function prepareCreationOptions(raw: JsonObject): PublicKeyCredentialCreationOptions {
  return {
    ...raw,
    challenge: base64urlToBuffer(String(raw.challenge)),
    user: {
      ...raw.user,
      id: base64urlToBuffer(String(raw.user.id)),
    },
    excludeCredentials: (raw.excludeCredentials ?? []).map((item: JsonObject) => ({
      ...item,
      id: base64urlToBuffer(String(item.id)),
    })),
  } as PublicKeyCredentialCreationOptions
}

export function prepareRequestOptions(raw: JsonObject): PublicKeyCredentialRequestOptions {
  return {
    ...raw,
    challenge: base64urlToBuffer(String(raw.challenge)),
    allowCredentials: (raw.allowCredentials ?? []).map((item: JsonObject) => ({
      ...item,
      id: base64urlToBuffer(String(item.id)),
    })),
  } as PublicKeyCredentialRequestOptions
}

export function credentialToJSON(credential: PublicKeyCredential): JsonObject {
  const response = credential.response
  const payload: JsonObject = {
    id: credential.id,
    rawId: bufferToBase64url(credential.rawId),
    type: credential.type,
    clientExtensionResults: credential.getClientExtensionResults(),
  }
  if ('attestationObject' in response) {
    const attestation = response as AuthenticatorAttestationResponse
    payload.response = {
      clientDataJSON: bufferToBase64url(attestation.clientDataJSON),
      attestationObject: bufferToBase64url(attestation.attestationObject),
      transports: typeof attestation.getTransports === 'function' ? attestation.getTransports() : [],
    }
  } else {
    const assertion = response as AuthenticatorAssertionResponse
    payload.response = {
      clientDataJSON: bufferToBase64url(assertion.clientDataJSON),
      authenticatorData: bufferToBase64url(assertion.authenticatorData),
      signature: bufferToBase64url(assertion.signature),
      userHandle: assertion.userHandle ? bufferToBase64url(assertion.userHandle) : null,
    }
  }
  return payload
}

export function passkeyAvailableForHost(available: boolean, rpId: string): boolean {
  if (!available || !rpId || typeof window === 'undefined') return false
  if (typeof window.PublicKeyCredential === 'undefined') return false
  const host = window.location.hostname.toLowerCase()
  const rp = rpId.toLowerCase()
  return host === rp || host.endsWith(`.${rp}`)
}

async function readJson<T>(response: Response): Promise<T> {
  if (!response.ok) throw new Error(await readErrorMessage(response))
  return (await response.json()) as T
}

export async function fetchPasskeys(): Promise<PasskeyList> {
  return readJson<PasskeyList>(await fetch('/api/auth/webauthn'))
}

export async function registerPasskey(name: string): Promise<PasskeyItem | null> {
  const begun = await readJson<{ challenge_id: string; options: JsonObject }>(
    await fetch('/api/auth/webauthn/register/begin', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...csrfHeaders() },
      body: JSON.stringify({ name }),
    }),
  )
  const credential = await navigator.credentials.create({
    publicKey: prepareCreationOptions(begun.options),
  }) as PublicKeyCredential | null
  if (!credential) throw new Error('已取消 Passkey 注册')
  const completed = await readJson<{ credential: PasskeyItem | null }>(
    await fetch('/api/auth/webauthn/register/complete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...csrfHeaders() },
      body: JSON.stringify({
        challenge_id: begun.challenge_id,
        credential: credentialToJSON(credential),
        name,
      }),
    }),
  )
  return completed.credential
}

export async function loginWithPasskey(email: string): Promise<SessionUser> {
  const begun = await readJson<{ challenge_id: string; options: JsonObject }>(
    await fetch('/api/auth/webauthn/authenticate/begin', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email }),
    }),
  )
  const credential = await navigator.credentials.get({
    publicKey: prepareRequestOptions(begun.options),
  }) as PublicKeyCredential | null
  if (!credential) throw new Error('已取消 Passkey 登录')
  const completed = await readJson<{ user: SessionUser }>(
    await fetch('/api/auth/webauthn/authenticate/complete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        challenge_id: begun.challenge_id,
        credential: credentialToJSON(credential),
      }),
    }),
  )
  return completed.user
}

export async function deletePasskey(credentialId: string, password: string): Promise<number> {
  const body = await readJson<{ remaining: number }>(
    await fetch(`/api/auth/webauthn/${encodeURIComponent(credentialId)}`, {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json', ...csrfHeaders() },
      body: JSON.stringify({ password }),
    }),
  )
  return body.remaining
}
