import type { Session } from './types'

const KEY = 'heatshield.demo.session'
const BASE = '/api/v1'
let session: Session | null = null
try { session = JSON.parse(sessionStorage.getItem(KEY) || 'null') as Session | null } catch { sessionStorage.removeItem(KEY) }

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) { super(message); this.status = status }
}

export function getSession() { return session }
export function setSession(value: Session | null) {
  session = value
  if (value) sessionStorage.setItem(KEY, JSON.stringify(value))
  else sessionStorage.removeItem(KEY)
}

export async function request<T>(path: string, method = 'GET', body?: unknown, idempotent = false): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json' }
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  if (session) headers.Authorization = `Bearer ${session.access_token}`
  if (idempotent) headers['Idempotency-Key'] = crypto.randomUUID()
  const response = await fetch(`${BASE}${path}`, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) })
  const data: unknown = await response.json().catch(() => null)
  if (!response.ok) {
    if (response.status === 401 && path !== '/auth/login') {
      setSession(null)
      window.dispatchEvent(new Event('heatshield:session-expired'))
    }
    const detail = data && typeof data === 'object' && 'detail' in data ? (data as { detail: unknown }).detail : data
    const message = typeof detail === 'string' ? detail : Array.isArray(detail)
      ? detail.map(item => `${item.loc?.slice(1).join('.') || 'Input'}: ${item.msg || 'Invalid value'}`).join('; ')
      : (detail && typeof detail === 'object' && 'message' in detail ? String(detail.message) : `Request failed (${response.status}).`)
    throw new ApiError(response.status, response.status === 409 ? `${message} Refresh this scenario before trying again.` : message)
  }
  return data as T
}
