"""Relatório de import — evidência do critério de aceite do SPEC.

"509 entradas importadas com domínio/status/razão corretos (validação amostral
por domínio)" só é verificável com contagem por domínio, por ambiente e por
situação, mais a lista do que não foi resolvido. É isso que este módulo produz.
"""

from __future__ import annotations

import csv
from collections import Counter, defaultdict
from dataclasses import asdict
from io import StringIO
from pathlib import Path

from .build import BuildResult, Severity
from .cron import EntryKind


def render_markdown(result: BuildResult) -> str:
    out: list[str] = []
    w = out.append

    w(f"# Relatório de import do catálogo — `{result.host}`")
    w("")
    w(f"- Framework: `{result.framework_root}`")
    w(f"- Timezone do host: **{result.timezone or 'não identificada'}** (o crontab não declara)")
    w(f"- Jobs montados: **{len(result.jobs)}** "
      f"({len(result.enabled_jobs)} ativos, {len(result.disabled_jobs)} desabilitados)")
    w(f"- Contratos em disco: **{len(result.contracts)}** "
      f"({len(result.orphan_contracts)} órfãos)")
    w(f"- Wrappers em disco: **{len(result.wrappers)}**")
    w(f"- Aliases declarados no connections.json: **{len(result.aliases_declared)}**")
    w("")

    # -- censo de linhas de cron ---------------------------------------------
    kinds = Counter(e.kind for e in result.cron_entries)
    schedulable = [e for e in result.cron_entries if e.is_schedulable]
    w("## Censo das linhas de crontab")
    w("")
    w("| Categoria | Linhas |")
    w("|---|---:|")
    for kind in EntryKind:
        w(f"| {kind.value} | {kinds.get(kind, 0)} |")
    w(f"| **total** | **{len(result.cron_entries)}** |")
    w("")
    w(f"Entradas agendadas: **{len(schedulable)}** "
      f"({sum(1 for e in schedulable if e.enabled)} ativas, "
      f"{sum(1 for e in schedulable if not e.enabled)} desabilitadas)")
    w("")

    # -- por domínio ---------------------------------------------------------
    w("## Jobs por domínio")
    w("")
    w("| Domínio | Ativos | Desabilitados | Total | Contratos distintos |")
    w("|---|---:|---:|---:|---:|")
    by_domain: dict[str | None, list] = defaultdict(list)
    for job in result.jobs:
        by_domain[job.domain].append(job)
    for domain in sorted(by_domain, key=lambda d: (d is None, d or "")):
        jobs = by_domain[domain]
        active = sum(1 for j in jobs if j.enabled)
        distinct = len({j.contract_path for j in jobs if j.contract_path})
        w(f"| {domain or '(sem domínio)'} | {active} | {len(jobs) - active} | {len(jobs)} | {distinct} |")
    w(f"| **total** | **{len(result.enabled_jobs)}** | **{len(result.disabled_jobs)}** "
      f"| **{len(result.jobs)}** | **{len({j.contract_path for j in result.jobs if j.contract_path})}** |")
    w("")

    # -- dimensões -----------------------------------------------------------
    w("## Dimensões resolvidas")
    w("")
    envs = Counter(j.environment or "(não identificado)" for j in result.jobs)
    w("| Ambiente | Jobs |")
    w("|---|---:|")
    for env, count in envs.most_common():
        w(f"| {env} | {count} |")
    w("")

    countries = Counter(c for j in result.jobs for c in j.country_codes)
    no_country = sum(1 for j in result.jobs if not j.country_codes)
    w("| País | Jobs |")
    w("|---|---:|")
    for country, count in countries.most_common():
        w(f"| {country} | {count} |")
    if no_country:
        w(f"| (não identificado) | {no_country} |")
    w("")

    clients = Counter(j.client_code or "(não identificado)" for j in result.jobs)
    curated = sum(1 for j in result.jobs if j.client_name)
    w(f"Códigos de cliente distintos: **{len(clients)}** — "
      f"curados no vocabulário: **{curated}** de {len(result.jobs)} jobs. "
      f"Os não curados aparecem em `clients.todo-{result.host}.yaml`.")
    w("")

    # -- achados -------------------------------------------------------------
    w("## Achados")
    w("")
    by_severity = Counter(f.severity for f in result.findings)
    for severity in Severity:
        w(f"- **{severity.value}**: {by_severity.get(severity, 0)}")
    w("")

    by_kind: dict[tuple[Severity, str], list] = defaultdict(list)
    for finding in result.findings:
        by_kind[(finding.severity, finding.kind)].append(finding)

    for severity in Severity:
        rows = [(k, v) for k, v in by_kind.items() if k[0] is severity]
        if not rows:
            continue
        w(f"### {severity.value.capitalize()}")
        w("")
        for (_, kind), items in sorted(rows, key=lambda kv: -len(kv[1])):
            w(f"**{kind}** — {len(items)}")
            w("")
            for finding in items[:10]:
                w(f"- `{finding.subject}` — {finding.detail}")
            if len(items) > 10:
                w(f"- … e mais {len(items) - 10}")
            w("")

    # -- aliases -------------------------------------------------------------
    used = Counter(a for j in result.jobs for a in j.connection_aliases)
    unknown = sorted(set(used) - result.aliases_declared)
    unused = sorted(result.aliases_declared - set(used))
    w("## Aliases de conexão")
    w("")
    w(f"- Citados nos contratos dos jobs: **{len(used)}**")
    w(f"- Citados mas **não declarados** no connections.json: **{len(unknown)}**"
      + (f" → {', '.join(unknown[:15])}" if unknown else ""))
    w(f"- Declarados e não usados por job agendado: **{len(unused)}**"
      + (f" → {', '.join(unused[:15])}" if unused else ""))
    w("")

    return "\n".join(out) + "\n"


def write_jobs_csv(result: BuildResult, path: Path) -> None:
    rows = [asdict(job) for job in result.jobs]
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    buffer = StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    for row in rows:
        writer.writerow({k: _flatten(v) for k, v in row.items()})
    path.write_text(buffer.getvalue(), encoding="utf-8")


def write_findings_csv(result: BuildResult, path: Path) -> None:
    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["severidade", "tipo", "sujeito", "detalhe"])
    for finding in result.findings:
        writer.writerow(finding.as_row())
    path.write_text(buffer.getvalue(), encoding="utf-8")


def write_clients_mapping(result: BuildResult, path: Path) -> None:
    """Mapa `código -> nome`, derivado do campo `client` dos contratos.

    Antes de descobrir que o contrato DECLARA o cliente, este arquivo era um
    stub com `null` para o owner preencher 59 códigos à mão. Agora o nome vem
    do dado: para cada código, o nome dominante entre os contratos dos seus
    jobs. Nada é inventado — o que exige decisão humana sai comentado:

    * código ambíguo: dois clientes reais dividem o mesmo código de 3 letras;
    * outlier: um contrato declara cliente diferente dos demais do código, o
      que costuma ser erro de cópia — e num step `upload_remote` significa
      mandar arquivo para o cliente errado.
    """
    from collections import Counter, defaultdict

    por_codigo: dict[str, Counter] = defaultdict(Counter)
    exemplos: dict[str, set[str]] = defaultdict(set)
    for job in result.jobs:
        if job.client_code and job.contract_client:
            por_codigo[job.client_code][job.contract_client] += 1
            exemplos[job.client_code].add(job.process_name)

    sem_contrato = sorted(
        {j.client_code for j in result.jobs if j.client_code and not j.contract_client}
    )

    lines = [
        "# Mapa codigo -> nome do cliente, DERIVADO do campo `client` dos contratos.",
        "# Gerado por `catalog import`. Cole o bloco `clients:` em",
        "# seed/mappings/vocabulary.yaml depois de revisar os casos comentados.",
        "#",
        "# ATENCAO aos blocos marcados AMBIGUO e OUTLIER: exigem decisao humana.",
        "clients:",
    ]
    for code in sorted(por_codigo):
        nomes = por_codigo[code]
        (dominante, quantos), *resto = nomes.most_common()
        lines.append(f"  {code}: {dominante}")
        lines.append(f"    # {quantos} job(s); ex.: {sorted(exemplos[code])[0]}")
        for nome, poucos in resto:
            marca = "OUTLIER" if poucos * 10 <= quantos else "AMBIGUO"
            lines.append(f"    # {marca}: {poucos} job(s) declaram '{nome}'")

    if sem_contrato:
        lines.append("")
        lines.append("# Codigos sem contrato resolvido (nome nao derivavel; preencher a mao):")
        for code in sem_contrato:
            lines.append(f"#   {code}: null")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_clients_todo(result: BuildResult, path: Path) -> None:
    """Stub de curadoria para códigos cujo nome não veio do contrato."""
    evidence: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for job in result.jobs:
        if not job.client_code or job.client_name:
            continue
        entry = evidence[job.client_code]
        if job.domain:
            entry["dominios"].add(job.domain)
        for country in job.country_codes:
            entry["paises"].add(country)
        entry["exemplos"].add(job.process_name)
        for alias in job.connection_aliases:
            entry["aliases"].add(alias)

    lines = [
        "# Curadoria de clientes — gerado por `catalog import`, preencher `nome`.",
        "# Não adivinhe: cliente errado num job de upload_remote significa enviar",
        "# arquivo ao cliente errado. Confirme com o owner do domínio.",
        "clients:",
    ]
    for code in sorted(evidence):
        data = evidence[code]
        lines.append(f"  {code}:")
        lines.append("    nome: null")
        lines.append(f"    dominios: [{', '.join(sorted(data['dominios']))}]")
        lines.append(f"    paises: [{', '.join(sorted(data['paises']))}]")
        lines.append(f"    jobs: {len(data['exemplos'])}")
        for example in sorted(data["exemplos"])[:3]:
            lines.append(f"    # ex.: {example}")
        if data["aliases"]:
            lines.append(f"    # aliases: {', '.join(sorted(data['aliases'])[:6])}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _flatten(value: object) -> object:
    if isinstance(value, (tuple, list)):
        return "|".join(str(v) for v in value)
    return value


# ---------------------------------------------------------------------------
# Relatórios do catálogo persistido (ver catalog/db/queries.py)
# ---------------------------------------------------------------------------
ACAO_POR_ACHADO = {
    "alias-nao-declarado": "Declarar o alias no connections.json do host, ou confirmar job morto",
    "contrato-invalido": "Corrigir o contrato (violacao de schema)",
    "contrato-json-invalido": "Corrigir o JSON — o arquivo nao parseia",
    "contrato-ausente": "Restaurar o contrato ou remover a entrada do crontab",
    "wrapper-ausente": "Restaurar o wrapper ou remover a entrada do crontab",
    "cliente-outlier-no-contrato": "Confirmar o campo `client` do contrato",
    "drift-nao-gerenciado": "Crontab editado fora do fluxo: reverter ou abrir change_request",
    "mudanca-pendente-vencida": "Aplicar a linha-alvo — o cron nao parou sozinho",
    "dimensao-divergente": "Conferir a dimensao declarada no contrato",
}


def render_triagem(triagem, *, limite_por_grupo: int = 12) -> str:
    """Achados agrupados por quem decide, com o fingerprint de cada decisão."""
    linhas = [
        f"# Triagem de achados — `{triagem.host}`",
        "",
        "**Contém nomes de cliente, IPs e caminhos internos — não commitar.**",
        "",
        f"Reconciliação: `{triagem.run_id or 'nenhuma'}`. "
        f"Achados abertos: **{len(triagem.achados)}**.",
        "",
        "Depois da decisão, registre com "
        "`catalog explain <fingerprint> --reason \"...\" --actor <voce>` — a explicação "
        "sobrevive às reconciliações seguintes e é o que fecha o critério "
        "*sem divergência não explicada*.",
        "",
    ]
    if not triagem.achados:
        linhas += ["Nenhum achado aberto. O critério de reconciliação está fechado para este host.", ""]
        return "\n".join(linhas)

    erros = sum(1 for a in triagem.achados if a.severity == "erro")
    linhas += [f"Por severidade: **{erros} erros**, "
               f"{sum(1 for a in triagem.achados if a.severity == 'aviso')} avisos, "
               f"{sum(1 for a in triagem.achados if a.severity == 'info')} infos.", ""]

    for grupo, achados in triagem.por_grupo.items():
        ativos = sum(1 for a in achados if a.job_enabled)
        marca = ""
        if any(a.entrega_a_cliente for a in achados):
            marca = " — ⚠️ **entrega a cliente (`upload_remote`)**"
        linhas += [f"## {grupo} — {len(achados)} achado(s), {ativos} em job ativo{marca}", ""]
        for achado in achados[:limite_por_grupo]:
            linhas += [
                f"- **{achado.kind}** ({achado.severity}) — `{achado.subject}`",
                f"  - {achado.detail}",
            ]
            if achado.job_process:
                linhas.append(
                    f"  - job: `{achado.job_process}` "
                    f"({'ativo' if achado.job_enabled else 'desabilitado'})"
                )
            acao = ACAO_POR_ACHADO.get(achado.kind)
            if acao:
                linhas.append(f"  - ação: {acao}")
            linhas.append(f"  - `catalog explain {achado.fingerprint}`")
        if len(achados) > limite_por_grupo:
            linhas.append(f"- … e mais {len(achados) - limite_por_grupo} neste grupo")
        linhas.append("")
    return "\n".join(linhas)


def render_amostra(host: str, amostra: dict, *, seed: int) -> str:
    """Checklist de conferência humana — o critério 'validação amostral'."""
    total = sum(len(v) for v in amostra.values())
    linhas = [
        f"# Validação amostral por domínio — `{host}`",
        "",
        f"Amostra determinística (semente `{seed}`): **{total} jobs** em "
        f"{len(amostra)} domínios. A mesma semente devolve a mesma amostra — "
        "a conferência pode ser refeita e auditada.",
        "",
        "Para cada job, confira **domínio, status e razão** contra o crontab e o contrato. "
        "Marque a caixa quando conferido.",
        "",
    ]
    for dominio, itens in amostra.items():
        linhas += [f"## {dominio} — {len(itens)} job(s)", ""]
        for item in itens:
            agendas = ", ".join(
                f"`{expr or 'on-demand'}`{'' if ativa else ' (desabilitada)'}"
                for expr, ativa in item.schedules
            ) or "sem agenda"
            linhas += [
                f"- [ ] **`{item.process_name}`**",
                f"  - catálogo: domínio `{item.domain}` · status `{item.status}` · "
                f"ambiente `{item.environment}` · cliente `{item.client}`",
                f"  - agendas: {agendas}",
            ]
            if item.status_reason:
                razao = item.status_reason.replace("\n", " ⏎ ")[:200]
                linhas.append(f"  - razão registrada: _{razao}_")
            if item.contrato_declara:
                declara = " · ".join(
                    f"{k}=`{v}`" for k, v in item.contrato_declara.items() if v
                )
                linhas.append(f"  - contrato declara: {declara}")
        linhas.append("")
    return "\n".join(linhas)


def render_historico(job, eventos) -> str:
    linhas = [
        f"# Histórico — `{job.process_name}` (`{job.host}`)",
        "",
        f"Domínio `{job.domain}` · ambiente `{job.environment}` · "
        f"cliente `{job.client_name or job.client_code}` · status `{job.status}`",
        "",
    ]
    if not eventos:
        return "\n".join(linhas + ["Sem histórico registrado.", ""])

    for evento in eventos:
        quando = evento.quando.strftime("%Y-%m-%d %H:%M") if evento.quando else "?"
        linhas.append(
            f"- `{quando}` **{evento.tipo} v{evento.versao}** por `{evento.quem}` — {evento.resumo}"
        )
        antes = (evento.detalhe or {}).get("antes") or {}
        depois = (evento.detalhe or {}).get("depois") or {}
        for campo in sorted(antes):
            linhas.append(f"    - `{campo}`: {antes[campo]!r} → {depois.get(campo)!r}")
        violacoes = ((evento.detalhe or {}).get("validation") or {}).get("violations") or []
        for v in violacoes[:3]:
            linhas.append(f"    - schema: {v.get('path')} — {v.get('message')}")
    linhas.append("")
    return "\n".join(linhas)
