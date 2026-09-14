"""Lock por processo — a formalização do SOP Zinli: nunca duas execuções do
mesmo job em voo ao mesmo tempo (`modules/platform-api/SPEC.md`, requisito 3;
`docs/riscos.md` documenta o incidente de contenção/timeout que motiva isto).

`pg_try_advisory_xact_lock` porque é **não-bloqueante**: uma segunda
requisição para o mesmo job não fica pendurada esperando a primeira — ela
recebe 409 na hora. Serializar datas dentro de UMA execução é o `main.sh`
quem faz (`--dates-pattern-files` com datas separadas por vírgula processa
uma de cada vez, internamente); o lock aqui impede uma SEGUNDA requisição
concorrente para o mesmo `job_id`, que é a classe de incidente real.

Lock de transação: libera sozinho no commit/rollback da sessão que o pediu —
não existe caminho de código que o segure além da vida da requisição.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session


class JobLocked(Exception):
    """Já existe execução em voo para este job."""


def try_lock_job(session: Session, job_id: uuid.UUID) -> None:
    # `hashtext` reduz o UUID a um bigint estável — mesmo job, mesma chave,
    # sempre. Colisão entre dois jobs diferentes é teoricamente possível (é
    # hash), mas o pior caso é serializar dois jobs que não precisavam — nunca
    # o oposto (deixar dois do MESMO job passarem ao mesmo tempo).
    obtido = session.execute(
        select(func.pg_try_advisory_xact_lock(func.hashtext(str(job_id))))
    ).scalar()
    if not obtido:
        raise JobLocked(f"já existe execução em andamento para o job {job_id}")
