"""Convenção de nomes do legado -> dimensões do catálogo.

Os nomes de contrato e de wrapper carregam ambiente, cliente, país e domínio:

    prd_dal_col_bs2.json  ->  PROD | cliente dal | país CO | base2

Esta camada não adivinha nada: resolve apenas o que está declarado no
vocabulário de curadoria (`seed/mappings/vocabulary.yaml`). Token não mapeado é
preservado como código bruto e sinalizado, para que o relatório de import
mostre exatamente o que falta curar.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

import yaml

DEFAULT_VOCABULARY = Path(__file__).resolve().parents[2] / "seed" / "mappings" / "vocabulary.yaml"


class Vocabulary:
    """Tabelas de curadoria carregadas do YAML."""

    def __init__(self, data: dict) -> None:
        self.environments: dict[str, str] = {k.lower(): v for k, v in (data.get("environments") or {}).items()}
        self.countries: dict[str, str] = {k.lower(): v for k, v in (data.get("countries") or {}).items()}
        self.clients: dict[str, str] = {k.lower(): v for k, v in (data.get("clients") or {}).items()}
        self.domains: dict[str, list[str]] = {k: [a.lower() for a in v] for k, v in (data.get("domains") or {}).items()}
        self.variant_suffixes: set[str] = {s.lower() for s in (data.get("variant_suffixes") or [])}
        self.shared_client_codes: dict[str, set[str]] = {
            k.lower(): {n.lower() for n in v}
            for k, v in (data.get("shared_client_codes") or {}).items()
        }

    @classmethod
    def load(cls, path: Path | None = None) -> Vocabulary:
        target = path or DEFAULT_VOCABULARY
        with target.open(encoding="utf-8") as handle:
            return cls(yaml.safe_load(handle) or {})

    @cached_property
    def abbrev_to_domain(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for domain, abbrevs in self.domains.items():
            for abbrev in abbrevs:
                out[abbrev] = domain
        return out


@dataclass(frozen=True)
class ProcessName:
    """Resultado do parse de um nome de processo (contrato ou wrapper)."""

    raw: str
    tokens: tuple[str, ...]
    environment: str | None = None          # PROD | UAT | TEST | DEV
    environment_token: str | None = None     # prd | prod | pro | uat ...
    client_code: str | None = None           # codigo bruto, ex. "nuvy_dal"
    client_name: str | None = None           # nome canonico, se curado
    country_codes: tuple[str, ...] = ()      # ISO alpha-2, ex. ("SV", "CR")
    country_tokens: tuple[str, ...] = ()     # tokens brutos, ex. ("slv", "cri")
    domain_from_name: str | None = None       # dominio deduzido da abreviacao no nome
    domain_abbrev: str | None = None
    process_suffix: str | None = None        # o que vem depois da abreviacao de dominio
    flags: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_fully_resolved(self) -> bool:
        return bool(self.environment and self.client_code and self.country_codes and self.domain_from_name)


def parse_process_name(name: str, vocab: Vocabulary, domain_dir: str | None = None) -> ProcessName:
    """Decompõe o nome de um contrato/wrapper nas dimensões do catálogo.

    `domain_dir` é o domínio vindo do caminho em disco (`processes/<dominio>/`),
    que é a fonte da verdade. Divergência entre ele e a abreviação embutida no
    nome é sinalizada em `flags`, nunca corrigida em silêncio.
    """
    stem = name
    for suffix in (".json", ".sh"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]

    tokens = tuple(t for t in stem.split("_") if t != "")
    flags: list[str] = []

    if not tokens:
        return ProcessName(raw=name, tokens=(), flags=("nome-vazio",))

    # 1. ambiente: sempre o primeiro token, quando reconhecido
    env_token = tokens[0].lower()
    environment = vocab.environments.get(env_token)
    cursor = 1 if environment else 0
    if environment is None:
        flags.append("ambiente-nao-identificado")
        env_token = None

    # 2. abreviacao de dominio: primeiro token reconhecido a partir do cursor.
    #    Delimita a fronteira entre cliente/pais (antes) e sufixo (depois).
    domain_pos = None
    for index in range(cursor, len(tokens)):
        if tokens[index].lower() in vocab.abbrev_to_domain:
            domain_pos = index
            break

    if domain_pos is None:
        flags.append("dominio-nao-identificado-no-nome")
        middle = tokens[cursor:]
        domain_abbrev = None
        domain_from_name = None
        suffix_tokens: tuple[str, ...] = ()
    else:
        middle = tokens[cursor:domain_pos]
        domain_abbrev = tokens[domain_pos].lower()
        domain_from_name = vocab.abbrev_to_domain[domain_abbrev]
        suffix_tokens = tokens[domain_pos + 1 :]

    if domain_dir and domain_from_name and domain_dir != domain_from_name:
        flags.append(f"dominio-divergente:dir={domain_dir},nome={domain_from_name}")

    # 3. país, em dois passes.
    #
    #    (a) tokens de país no FIM do trecho do meio, que é o caso canônico
    #        `env_cliente_pais_abrev_...` (inclusive vários: ..._slv_cri_bs2).
    #
    #    (b) se (a) não achou nada, o país costuma vir logo depois do cliente e
    #        o resto do nome é descrição do processo — padrão de nomes sem
    #        abreviação de domínio, como `prd_ban_dom_extraer_apertura_gaveta`.
    #        Nesse caso o que sobra depois do país entra no sufixo.
    #
    #    Em ambos, sempre sobra ao menos um token para o cliente.
    country_tokens: list[str] = []
    country_codes: list[str] = []
    while len(middle) > 1 and middle[-1].lower() in vocab.countries:
        token = middle[-1].lower()
        country_tokens.insert(0, token)
        country_codes.insert(0, vocab.countries[token])
        middle = middle[:-1]

    if not country_tokens:
        position = next(
            (i for i in range(1, len(middle)) if middle[i].lower() in vocab.countries), None
        )
        if position is not None:
            end = position
            while end < len(middle) and middle[end].lower() in vocab.countries:
                token = middle[end].lower()
                country_tokens.append(token)
                country_codes.append(vocab.countries[token])
                end += 1
            suffix_tokens = tuple(middle[end:]) + suffix_tokens
            middle = middle[:position]

    if not country_tokens:
        flags.append("pais-nao-identificado")

    client_code = "_".join(middle).lower() if middle else None
    if client_code is None:
        flags.append("cliente-nao-identificado")
    client_name = vocab.clients.get(client_code) if client_code else None
    if client_code and client_name is None:
        flags.append("cliente-nao-curado")

    process_suffix = "_".join(suffix_tokens).lower() if suffix_tokens else None
    if suffix_tokens and {t.lower() for t in suffix_tokens} & vocab.variant_suffixes:
        flags.append("sufixo-de-variante")
    if any(_looks_like_date(t) for t in suffix_tokens):
        flags.append("data-no-nome")

    return ProcessName(
        raw=name,
        tokens=tokens,
        environment=environment,
        environment_token=env_token,
        client_code=client_code,
        client_name=client_name,
        country_codes=tuple(country_codes),
        country_tokens=tuple(country_tokens),
        domain_from_name=domain_from_name,
        domain_abbrev=domain_abbrev,
        process_suffix=process_suffix,
        flags=tuple(flags),
    )


def _looks_like_date(token: str) -> bool:
    """ddmmyyyy, yyyymmdd ou yyyymm — indica contrato pontual, tipicamente one-off."""
    return token.isdigit() and len(token) in (6, 8)
