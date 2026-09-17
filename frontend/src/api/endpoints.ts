// Um lugar só para montar path/params de cada recurso — as páginas chamam
// isto (via `useQuery`) e as ações de escrita (via `apiPost`/`apiPatch`
// diretamente), nunca `fetch` cru. Espelha `docs/api/platform-api.md`.

import { qs } from './client'

export const paths = {
  me: () => '/me',
  jobs: (f: { domain?: string; client?: string; environment?: string; status?: string }) =>
    `/jobs${qs(f)}`,
  job: (id: string) => `/jobs/${id}`,
  jobSchedules: (id: string) => `/jobs/${id}/schedules`,
  jobContract: (id: string) => `/jobs/${id}/contract`,
  jobReconciliation: (id: string) => `/jobs/${id}/reconciliation`,
  jobValidate: (id: string) => `/jobs/${id}/validate`,
  jobStatus: (id: string) => `/jobs/${id}/status`,
  executions: (f: { job_id?: string; result?: string }) => `/executions${qs(f)}`,
  execution: (id: string) => `/executions/${id}`,
  executionLogs: (id: string) => `/executions/${id}/logs`,
  changeRequests: (f: { state?: string; host?: string }) => `/change-requests${qs(f)}`,
  changeRequestCancel: (id: string) => `/change-requests/${id}/cancel`,
  adminJobs: (f: { q?: string; domain?: string; status?: string }) => `/admin/jobs${qs(f)}`,
  adminJob: (id: string) => `/admin/jobs/${id}`,
  adminJobContract: (id: string) => `/admin/jobs/${id}/contract`,
}
