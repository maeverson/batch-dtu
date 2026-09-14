import { useEffect, useRef, useState } from 'react'
import { useAuth } from 'react-oidc-context'
import { apiGet, ApiError } from './client'
import { paths } from './endpoints'
import type { Me } from './types'

interface QueryState<T> {
  data: T | undefined
  error: ApiError | Error | undefined
  loading: boolean
  reload: () => void
}

/**
 * Busca `path` autenticado e, se `pollMs` for dado, repete no intervalo —
 * é o "tempo real" possível na Fase 1 (SPEC item 4): a API de execução hoje
 * é síncrona (`POST /executions` só responde quando o SSH termina), então
 * "monitorar em tempo real" quer dizer reconsultar status/logs no intervalo,
 * não um stream real. Ver `pages/ExecutionDetailPage.tsx` para o porquê disso
 * ainda vale a pena: o Loki (via promtail) pode continuar recebendo linhas
 * por alguns segundos depois da execução terminar.
 */
export function useQuery<T>(
  path: string | null,
  opts?: { pollMs?: number },
): QueryState<T> {
  const { user } = useAuth()
  const token = user?.access_token
  const [data, setData] = useState<T>()
  const [error, setError] = useState<ApiError | Error>()
  const [loading, setLoading] = useState(true)
  const tick = useRef(0)

  const reload = () => {
    tick.current += 1
    setLoading(true)
  }

  useEffect(() => {
    if (!path || !token) return
    let cancelado = false
    setLoading(true)
    apiGet<T>(path, token)
      .then((r) => {
        if (!cancelado) {
          setData(r)
          setError(undefined)
        }
      })
      .catch((e: unknown) => {
        if (!cancelado) setError(e as ApiError)
      })
      .finally(() => {
        if (!cancelado) setLoading(false)
      })
    return () => {
      cancelado = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, token, tick.current])

  useEffect(() => {
    if (!opts?.pollMs || !path || !token) return
    const id = setInterval(reload, opts.pollMs)
    return () => clearInterval(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, token, opts?.pollMs])

  return { data, error, loading, reload }
}

export function useToken(): string | undefined {
  const { user } = useAuth()
  return user?.access_token
}

/** `GET /me` — usado em toda a casca (nav, gates de role) e nas páginas que
 * precisam decidir o que oferecer (`rbac.ts`). */
export function useMe(): QueryState<Me> {
  return useQuery<Me>(paths.me())
}
