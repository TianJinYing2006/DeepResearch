import { describe, expect, it } from 'vitest'

import {
  base64urlToBuffer,
  bufferToBase64url,
  credentialToJSON,
  prepareCreationOptions,
  prepareRequestOptions,
} from './webauthn'

function bytes(value: ArrayBuffer | ArrayBufferView): number[] {
  const view = value instanceof Uint8Array
    ? value
    : new Uint8Array(value instanceof ArrayBuffer ? value : value.buffer)
  return Array.from(view)
}

describe('webauthn base64url 转换', () => {
  it('encode / decode 往返一致', () => {
    expect(bufferToBase64url(new Uint8Array([1, 2, 3]))).toBe('AQID')
    expect(bytes(base64urlToBuffer('AQID'))).toEqual([1, 2, 3])
    expect(bufferToBase64url(base64urlToBuffer('a-_b'))).toBe('a-_b')
  })
})

describe('webauthn 选项转换', () => {
  it('registration options 把 challenge / user.id / excludeCredentials 转为 ArrayBuffer', () => {
    const options = prepareCreationOptions({
      challenge: 'AQID',
      rp: { id: 'localhost', name: 'iKaoju' },
      user: { id: 'BAUG', name: 'a@example.com', displayName: 'a@example.com' },
      excludeCredentials: [{ id: 'BwgJ', type: 'public-key', transports: ['internal'] }],
    })
    expect(bytes(options.challenge)).toEqual([1, 2, 3])
    expect(bytes(options.user.id)).toEqual([4, 5, 6])
    expect(bytes(options.excludeCredentials![0].id)).toEqual([7, 8, 9])
  })

  it('authentication options 把 challenge / allowCredentials 转为 ArrayBuffer', () => {
    const options = prepareRequestOptions({
      challenge: 'AQID',
      rpId: 'localhost',
      allowCredentials: [{ id: 'BAUG', type: 'public-key' }],
    })
    expect(bytes(options.challenge)).toEqual([1, 2, 3])
    expect(bytes(options.allowCredentials![0].id)).toEqual([4, 5, 6])
  })
})

describe('webauthn 凭据序列化', () => {
  it('注册凭据带 attestationObject 与 transports', () => {
    const credential = {
      id: 'credential-1',
      rawId: new Uint8Array([1, 2, 3]).buffer,
      type: 'public-key',
      response: {
        clientDataJSON: new Uint8Array([4, 5]).buffer,
        attestationObject: new Uint8Array([6, 7]).buffer,
        getTransports: () => ['internal'],
      },
      getClientExtensionResults: () => ({ credProps: { rk: true } }),
    } as unknown as PublicKeyCredential

    const payload = credentialToJSON(credential)
    expect(payload.id).toBe('credential-1')
    expect(payload.rawId).toBe('AQID')
    expect(payload.response).toMatchObject({
      clientDataJSON: 'BAU',
      attestationObject: 'Bgc',
      transports: ['internal'],
    })
    expect(payload.clientExtensionResults).toEqual({ credProps: { rk: true } })
  })

  it('登录凭据带 authenticatorData / signature / userHandle', () => {
    const credential = {
      id: 'credential-2',
      rawId: new Uint8Array([9]).buffer,
      type: 'public-key',
      response: {
        clientDataJSON: new Uint8Array([1]).buffer,
        authenticatorData: new Uint8Array([2]).buffer,
        signature: new Uint8Array([3]).buffer,
        userHandle: new Uint8Array([4]).buffer,
      },
      getClientExtensionResults: () => ({}),
    } as unknown as PublicKeyCredential

    const payload = credentialToJSON(credential)
    expect(payload.response).toMatchObject({
      clientDataJSON: 'AQ',
      authenticatorData: 'Ag',
      signature: 'Aw',
      userHandle: 'BA',
    })
  })
})
