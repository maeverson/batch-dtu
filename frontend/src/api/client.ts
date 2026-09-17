import { apiBaseUrl } from '../config'
import type { ApiErrorBody } from './types'

export class ApiError extends Error {
  status: number
  constructor(status: number, detail: string) {
    super(detail)
    this.status = status
  }
}

// Sem instrução de SSH em lugar nenhum (docs/back-office/CLAUDE.md: "nunca
// acessa o plano de execução diretamente") — este é o ÚNICO ponto de contato
// da UI com o backend, e é sempre HTTP contra a Platform API.
async function request<T>(
  path: string,
  token: string | undefined,
  init?: RequestInit,
): Promise<T> {
  const headers = new Headers(init?.headers)
  headers.set('Accept', 'application/json')
  if (init?.body) headers.set('Content-Type', 'application/json')
  if (token) headers.set('Authorization', `Bearer ${token}`)

  const resposta = await fetch(`${apiBaseUrl}${path}`, { ...init, headers })

  if (!resposta.ok) {
    let detail = `${resposta.status} ${resposta.statusText}`
    try {
      const corpo = (await resposta.json()) as ApiErrorBody
      if (corpo.detail) detail = corpo.detail
    } catch {
      // corpo não era JSON — mantém o detail genérico
    }
    throw new ApiError(resposta.status, detail)
  }

  if (resposta.status === 204) return undefined as T
  return (await resposta.json()) as T
}

export function apiGet<T>(path: string, token: string | undefined): Promise<T> {
  return request<T>(path, token, { method: 'GET' })
}

export function apiPost<T>(path: string, token: string | undefined, body?: unknown): Promise<T> {
  return request<T>(path, token, { method: 'POST', body: body ? JSON.stringify(body) : undefined })
}

export function apiPatch<T>(path: string, token: string | undefined, body?: unknown): Promise<T> {
  return request<T>(path, token, { method: 'PATCH', body: body ? JSON.stringify(body) : undefined })
}

export function apiPut<T>(path: string, token: string | undefined, body?: unknown): Promise<T> {
  return request<T>(path, token, { method: 'PUT', body: body ? JSON.stringify(body) : undefined })
}

// `DELETE` com corpo: o motivo é obrigatório do lado do servidor — desativar
// um job sem justificativa registrada não é uma operação que exista aqui.
export function apiDelete<T>(path: string, token: string | undefined, body?: unknown): Promise<T> {
  return request<T>(path, token, { method: 'DELETE', body: body ? JSON.stringify(body) : undefined })
}

export function qs(params: Record<string, string | undefined | null>): string {
  const entradas = Object.entries(params).filter(([, v]) => v != null && v !== '')
  if (entradas.length === 0) return ''
  return '?' + new URLSearchParams(entradas as [string, string][]).toString()
}
