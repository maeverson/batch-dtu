// Espelho, só para a UI decidir o que OFERECER, de `src/platform_api/authz.py`.
// Quem autoriza de verdade é sempre o servidor (todo POST/PATCH pode voltar
// 403 mesmo com isto liberando o botão) — isto existe só para não oferecer
// uma ação que o servidor recusaria, não para decidir permissão de verdade.
// A diferença de propósito em relação a `authz.py`: aqui não há acesso a
// `role_binding` por job, só ao agregado de `GET /me` (domínios/ambientes
// visíveis, união de todos os bindings do usuário) — por isso isto é mais
// permissivo do que a regra real quando os bindings são heterogêneos entre
// domínios/ambientes. Nesse caso o servidor é quem corta.

import type { Environment, Job, Me } from './api/types'

const PROD_ENVIRONMENTS: ReadonlySet<Environment> = new Set(['PROD'])

export function requiredOperateRole(environment: Environment | null): string {
  return environment && PROD_ENVIRONMENTS.has(environment) ? 'batch.operator-prod' : 'batch.operator'
}

export function isAdmin(me: Me | undefined): boolean {
  return !!me?.roles.includes('batch.admin')
}

function inVisibleSet(set: string[] | null | undefined, value: string | null): boolean {
  if (set == null) return true // null = todos (algum binding sem escopo nessa dimensão)
  if (value == null) return false
  return set.includes(value)
}

/** Só para habilitar/desabilitar o botão — a verdade é o 403 do servidor. */
export function canAttemptOperate(me: Me | undefined, job: Job): boolean {
  if (!me) return false
  if (me.roles.includes('batch.admin')) return true
  const necessaria = requiredOperateRole(job.environment)
  if (!me.roles.includes(necessaria)) return false
  return (
    inVisibleSet(me.visible_domains, job.domain) &&
    inVisibleSet(me.visible_environments, job.environment)
  )
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
