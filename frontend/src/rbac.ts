// Espelho, só para a UI decidir o que OFERECER, de `src/platform_api/authz.py`.
// Quem autoriza de verdade é sempre o servidor (todo POST/PATCH pode voltar
// 403 mesmo com isto liberando o botão) — isto existe só para não oferecer
// uma ação que o servidor recusaria, não para decidir permissão de verdade.
//
// Desde que o RBAC passou a vir inteiro do Entra ID, a regra ficou simples:
// role no token + ambiente da instância. Não há mais conjunto de domínios
// visíveis para conferir — o servidor só serve um ambiente e um host.

import type { Environment, Job, Me } from './api/types'

const PROD_ENVIRONMENTS: ReadonlySet<Environment> = new Set(['PROD'])

export function requiredOperateRole(environment: Environment | null): string {
  return environment && PROD_ENVIRONMENTS.has(environment) ? 'batch.operator-prod' : 'batch.operator'
}

export function isAdmin(me: Me | undefined): boolean {
  return !!me?.is_admin || !!me?.roles.includes('batch.admin')
}

/** Só para habilitar/desabilitar o botão — a verdade é o 403 do servidor. */
export function canAttemptOperate(me: Me | undefined, job: Job): boolean {
  if (!me) return false
  // O job só chega aqui se a instância o serve (o servidor já filtrou por
  // ambiente/host); resta a role.
  if (me.roles.includes('batch.admin')) return true
  return me.roles.includes(requiredOperateRole(job.environment))
}

export function isViewerOnly(me: Me | undefined): boolean {
  if (!me) return true
  const podeOperar = me.roles.some((r) =>
    ['batch.operator', 'batch.operator-prod', 'batch.admin'].includes(r),
  )
  return !podeOperar
}

export function roleLabel(role: string): string {
  return (
    {
      'batch.viewer': 'Viewer',
      'batch.operator': 'Operator',
      'batch.operator-prod': 'Operator PROD',
      'batch.admin': 'Admin',
    } as Record<string, string>
  )[role] ?? role
}
