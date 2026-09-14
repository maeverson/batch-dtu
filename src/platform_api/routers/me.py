"""`GET /me` — identidade e escopo resolvido do usuário autenticado.

O Back Office usa isto para renderização condicionada a role (SPEC, item
"Renderização condicionada a role") sem duplicar a regra de `authz.py`: as
roles vêm do token, `visible_domains`/`visible_environments` vêm do mesmo
`Scope` que todo endpoint de escrita já usa para autorizar. Isto não é uma
segunda fonte de verdade — é a MESMA fonte, só exposta para a UI decidir o
que mostrar antes de tentar (o servidor continua sendo quem autoriza de
fato; a UI só evita oferecer uma ação que o 403 recusaria).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..authz import Scope
from ..deps import get_current_user, get_scope
from ..schemas import MeOut
from ..security import AuthenticatedUser

router = APIRouter(tags=["identidade"])


@router.get("/me", response_model=MeOut)
def eu(
    user: AuthenticatedUser = Depends(get_current_user),
    scope: Scope = Depends(get_scope),
) -> MeOut:
    domains = scope.visible_domains()
    environments = scope.visible_environments()
    return MeOut(
        subject=user.subject,
        roles=sorted(user.roles),
        visible_domains=sorted(domains) if domains is not None else None,
        visible_environments=sorted(environments) if environments is not None else None,
    )
