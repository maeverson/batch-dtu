"""`GET /me` — identidade, roles do Entra e escopo desta instância.

O Back Office usa isto para renderização condicionada a role (SPEC, item
"Renderização condicionada a role") sem duplicar a regra de `authz.py`, e
para mostrar de cara QUAL ambiente/host aquela instância opera — com um
deploy por ambiente, essa é a informação que evita o operador achar que está
em UAT quando está em PROD.

Continua valendo: o servidor é quem autoriza de fato; a UI só evita oferecer
uma ação que o 403 recusaria.
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
    return MeOut(
        subject=user.subject,
        display_name=user.display_name,
        roles=sorted(user.roles),
        environment=scope.environment,
        host=scope.host,
        is_admin=scope.is_admin,
    )
