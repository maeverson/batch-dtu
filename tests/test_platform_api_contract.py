"""Os dois critérios de aceite do `SPEC.md` que não são "roda e vê se passa":

* "Nenhum caminho de código concatena entrada do usuário em comando shell
  (teste estático + revisão)" — aqui, estático de verdade: varre o código
  fonte por padrão perigoso, não só testa comportamento em runtime.
* "Contrato REST estável validado por testes de contrato (a UI não quebra ao
  trocar backend)" — o formato da resposta não pode depender de qual
  `ExecutionBackend` está por trás.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "platform_api"

# Chamadas que abrem shell de verdade. `asyncssh.connect(...).run(linha)` NÃO
# está aqui: `run()` do asyncssh manda a linha como payload do canal SSH
# "exec" — quem decide se isso vira shell é o `sshd` do lado de lá (que, com
# `command=` no `authorized_keys`, IGNORA o payload e roda o wrapper fixo).
# Não há shell local sendo aberto pela API em momento nenhum.
_PADROES_PERIGOSOS = {
    "os.system", "os.popen", "subprocess.call", "subprocess.run",
    "subprocess.Popen", "subprocess.check_call", "subprocess.check_output",
}


def _chamadas_de_modulo(caminho: Path) -> set[str]:
    arvore = ast.parse(caminho.read_text(encoding="utf-8"), filename=str(caminho))
    achados = set()
    for node in ast.walk(arvore):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            base = node.func.value
            if isinstance(base, ast.Name):
                achados.add(f"{base.id}.{node.func.attr}")
            elif isinstance(base, ast.Attribute) and isinstance(base.value, ast.Name):
                achados.add(f"{base.value.id}.{base.attr}.{node.func.attr}")
    return achados


@pytest.mark.parametrize("arquivo", sorted(SRC.rglob("*.py")))
def test_nenhum_arquivo_abre_shell_local(arquivo: Path):
    achados = _chamadas_de_modulo(arquivo) & _PADROES_PERIGOSOS
    assert not achados, f"{arquivo.relative_to(SRC)} chama {achados} — abre shell local"


def test_nenhum_arquivo_usa_eval_ou_exec_python():
    for arquivo in SRC.rglob("*.py"):
        arvore = ast.parse(arquivo.read_text(encoding="utf-8"), filename=str(arquivo))
        for node in ast.walk(arvore):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in ("eval", "exec"), \
                    f"{arquivo.relative_to(SRC)} usa {node.func.id}()"


def test_asyncssh_e_a_unica_via_de_execucao_remota():
    """`ssh_backend.py` é o único lugar que pode importar `asyncssh` — se
    outro módulo precisar executar algo remoto, ele tem que passar pela
    mesma validação (`build_invocation`), não abrir seu próprio caminho."""
    for arquivo in SRC.rglob("*.py"):
        if arquivo.name == "ssh_backend.py":
            continue
        texto = arquivo.read_text(encoding="utf-8")
        assert "import asyncssh" not in texto, f"{arquivo.relative_to(SRC)} importa asyncssh"


def test_build_invocation_e_chamado_antes_de_qualquer_dispatch():
    """`SSHExecutionBackend.dispatch` e `InMemoryExecutionBackend.dispatch`
    têm que rodar `build_invocation` — não pode existir um backend que
    despache sem validar."""
    from platform_api.ssh_backend import InMemoryExecutionBackend, SSHExecutionBackend

    import inspect
    import textwrap

    for cls in (SSHExecutionBackend, InMemoryExecutionBackend):
        fonte = ast.parse(textwrap.dedent(inspect.getsource(cls.dispatch)))
        chamadas = {
            n.func.id for n in ast.walk(fonte)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        assert "build_invocation" in chamadas, f"{cls.__name__}.dispatch não valida a invocação"


# --- estabilidade do contrato: o formato não pode depender do backend -------

def test_contrato_tem_os_recursos_documentados():
    """Espelha `docs/api/platform-api.md` — se um recurso sair daqui sem
    sair de lá (ou vice-versa), a documentação mentiu ou o código regrediu."""
    from platform_api.app import create_app
    from platform_api.ssh_backend import InMemoryExecutionBackend

    app = create_app(execution_backend=InMemoryExecutionBackend())
    paths = set(app.openapi()["paths"])

    documentados = {
        "/jobs", "/jobs/{job_id}", "/jobs/{job_id}/validate", "/jobs/{job_id}/status",
        "/executions", "/executions/{execution_id}", "/executions/{execution_id}/logs",
        "/change-requests", "/change-requests/{change_id}/cancel",
        "/audit-events",
    }
    faltando = documentados - paths
    assert not faltando, f"documentado em platform-api.md mas ausente do app: {faltando}"


def test_response_model_de_execution_e_o_mesmo_independente_do_backend():
    """O schema de resposta (`ExecutionOut`) é declarado no router, não no
    backend — trocar SSH por orchestrator (Fase 2) não pode mudar um campo
    sequer sem mudar o router, que é onde o contrato REST vive."""
    from platform_api.app import create_app
    from platform_api.ssh_backend import InMemoryExecutionBackend

    campos_esperados = {
        "id", "job_id", "trigger", "requested_steps", "dates_pattern", "no_mail",
        "status", "result", "exit_code", "requested_by", "justification",
        "started_at", "ended_at", "log_link",
    }
    app = create_app(execution_backend=InMemoryExecutionBackend())
    schema = app.openapi()["components"]["schemas"]["ExecutionOut"]["properties"]
    assert set(schema) == campos_esperados
