"""Configuração da Platform API — o que o processo recusa subir.

Três decisões de implantação viram teste aqui, porque todas as três já
falharam em ambiente real quando ficaram só na documentação:

* **G3** — sem `known_hosts`, a API não sobe. Verificar a chave do host é a
  relação de confiança com o host do `main.sh`; sem ela o canal aceita
  qualquer servidor que responda naquele IP.
* **G1** — ambiente e host são do DEPLOY. Sem eles não há como saber que
  parque esta instância opera.
* **New Relic** — `LOG_BACKEND=newrelic` sem chave de consulta produziria um
  `GET /executions/{id}/logs` que só falha em runtime, para o operador.
"""

from __future__ import annotations

import pytest

from platform_api.authz import Scope, require_operate
from platform_api.config import ConfigurationError, Settings, SSHBackendSettings


def _ssh(**kwargs) -> SSHBackendSettings:
    base = {"host": "172.21.86.76", "port": 22, "username": "backoffice_svc",
            "key_path": "/run/secrets/backoffice_svc", "known_hosts": None,
            "allow_unknown_hosts": False}
    return SSHBackendSettings(**{**base, **kwargs})


def test_sem_known_hosts_nao_sobe():
    with pytest.raises(ConfigurationError, match="SSH_BACKEND_KNOWN_HOSTS"):
        _ssh().validate()


def test_known_hosts_inexistente_nao_sobe(tmp_path):
    with pytest.raises(ConfigurationError, match="inexistente"):
        _ssh(known_hosts=str(tmp_path / "nao-existe")).validate()


def test_known_hosts_valido_sobe(tmp_path):
    arquivo = tmp_path / "known_hosts"
    arquivo.write_text("172.21.86.76 ssh-ed25519 AAAA...\n", encoding="utf-8")
    _ssh(known_hosts=str(arquivo)).validate()          # não levanta


def test_escape_de_verificacao_e_explicito():
    """Existe UM caminho para rodar sem verificar a chave — e ele tem nome,
    para aparecer em revisão de manifesto."""
    _ssh(allow_unknown_hosts=True).validate()          # não levanta


def test_log_backend_newrelic_exige_chave_de_consulta(tmp_path):
    arquivo = tmp_path / "known_hosts"
    arquivo.write_text("x\n", encoding="utf-8")
    settings = Settings(
        environment="UAT", host="com-ins-bch-mdw-dtu-1",
        ssh=_ssh(known_hosts=str(arquivo)), log_backend="newrelic",
    )
    with pytest.raises(ConfigurationError, match="NEW_RELIC_ACCOUNT_ID"):
        settings.validate()


def test_log_backend_desconhecido_nao_sobe(tmp_path):
    arquivo = tmp_path / "known_hosts"
    arquivo.write_text("x\n", encoding="utf-8")
    settings = Settings(environment="UAT", host="h", ssh=_ssh(known_hosts=str(arquivo)),
                        log_backend="splunk")
    with pytest.raises(ConfigurationError, match="LOG_BACKEND"):
        settings.validate()


def test_ambiente_vazio_nao_sobe(tmp_path):
    arquivo = tmp_path / "known_hosts"
    arquivo.write_text("x\n", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="PLATFORM_ENVIRONMENT"):
        Settings(environment="", host="h", ssh=_ssh(known_hosts=str(arquivo))).validate()


def test_from_env_le_o_escopo_do_deploy(monkeypatch):
    monkeypatch.setenv("PLATFORM_ENVIRONMENT", "prod")
    monkeypatch.setenv("PLATFORM_HOST", "srv-sftp-2")
    monkeypatch.setenv("SSH_BACKEND_HOST", "172.17.37.120")
    monkeypatch.setenv("SSH_BACKEND_PORT", "22")
    settings = Settings.from_env()
    assert settings.environment == "PROD"          # normalizado
    assert settings.is_prod
    assert settings.host == "srv-sftp-2"
    assert settings.ssh.host == "172.17.37.120"


# --- RBAC vindo só do token ---------------------------------------------

class _Job:
    def __init__(self, environment="UAT", host="com-ins-bch-mdw-dtu-1"):
        self.environment = environment
        self.host = host
        self.process_name = "uat_x"
        self.domain = "reportes"


def _scope(*roles, environment="UAT", host="com-ins-bch-mdw-dtu-1") -> Scope:
    return Scope(subject="u", display_name="u@x", token_roles=frozenset(roles),
                 environment=environment, host=host)


def test_operator_opera_em_uat():
    require_operate(_scope("batch.operator"), _Job())          # não levanta


def test_operator_nao_opera_em_prod():
    escopo = _scope("batch.operator", environment="PROD", host="srv-sftp-2")
    with pytest.raises(Exception, match="operator-prod"):
        require_operate(escopo, _Job(environment="PROD", host="srv-sftp-2"))


def test_admin_nao_atravessa_o_deploy():
    """A separação de ambiente é do deploy, não da role: nem `batch.admin`
    opera um job que esta instância não serve."""
    escopo = _scope("batch.admin", environment="UAT", host="com-ins-bch-mdw-dtu-1")
    with pytest.raises(Exception, match="não é servido por esta instância"):
        require_operate(escopo, _Job(environment="PROD", host="srv-sftp-2"))


def test_viewer_nao_opera():
    with pytest.raises(Exception, match="batch.operator"):
        require_operate(_scope("batch.viewer"), _Job())
