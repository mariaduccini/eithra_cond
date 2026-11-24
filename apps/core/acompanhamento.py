"""A jornada de acompanhamento: a Home e o registro canônico, ligados.

Quem supervisiona vê uma ação na Home e precisa conduzi-la sem cadastrar nada:
começar o acompanhamento, deixar uma atualização e, quando for o caso, encerrar.
Cada gesto já existe no domínio do próprio registro — começar a manutenção,
iniciar a ocorrência, marcar o andamento da demanda. Este módulo só escolhe, para
cada origem, qual gesto do domínio chamar e reúne o pouco que a tela precisa
mostrar, sempre com a permissão perguntada ao domínio.

Não há motor novo nem tabela nova. O registro operado é sempre o original — a
manutenção, a ocorrência, a demanda —, e o que se grava é o que o domínio já
grava, no histórico que já existe. A leitura derivada da supervisão continua
sendo a autoridade sobre bloco e ordem; aqui só se leva a pessoa até o registro
e se traduz cada toque no gesto certo.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import get_object_or_404

from apps.core.supervisao import ORIGEM_DEMANDA, ORIGEM_MANUTENCAO, ORIGEM_ROTINA
from apps.demandas.constants import StatusDemanda, TipoEventoDemanda
from apps.demandas.models import Demanda
from apps.demandas.permissoes import (
    condominios_para_ver as condominios_das_demandas,
    pode_adicionar_observacao,
    status_permitidos_para as situacoes_da_demanda,
)
from apps.demandas.services import (
    alterar_status as alterar_status_da_demanda,
    registrar_observacao,
)
from apps.manutencoes.constants import StatusManutencao, TipoEventoManutencao
from apps.manutencoes.models import Manutencao
from apps.manutencoes.permissoes import (
    condominios_para_ver as condominios_das_manutencoes,
    pode_concluir as pode_concluir_manutencao,
    pode_editar_manutencao,
    status_permitidos_para as situacoes_da_manutencao,
)
from apps.manutencoes.services import (
    alterar_status as alterar_status_da_manutencao,
    concluir_manutencao,
    registrar_atualizacao as registrar_atualizacao_da_manutencao,
)
from apps.rotinas.constants import ResultadoExecucao
from apps.rotinas.models import ExecucaoRotina
from apps.rotinas.permissoes import (
    execucoes_para_ver,
    pode_concluir as pode_concluir_execucao,
    pode_iniciar as pode_iniciar_execucao,
    pode_ver_execucao,
)
from apps.rotinas.services import (
    concluir_execucao,
    iniciar_execucao,
    registrar_atualizacao as registrar_atualizacao_da_execucao,
)

# Os gestos que a tela oferece. São poucos e sempre os mesmos nomes, mesmo que
# cada origem os execute de um jeito — a linguagem é uma só.
GESTO_INICIAR = "iniciar"
GESTO_ATUALIZAR = "atualizar"
GESTO_ENCERRAR = "encerrar"


@dataclass(frozen=True)
class Acompanhamento:
    """O registro canônico e o pouco que a tela precisa saber sobre ele.

    Guarda o registro original, como ele é chamado, onde acontece e quem o
    executa — além das permissões já decididas pelo domínio (começar, atualizar,
    encerrar), do momento em que o acompanhamento começou e do que já foi
    registrado. Nada aqui é cópia guardada: é a leitura do que o domínio já tem.
    """

    origem: str
    objeto: object
    titulo: str
    contexto: str
    fornecedor: str
    fornecedor_vinculado: bool
    iniciado_em: object | None
    atualizacoes: list
    pode_iniciar: bool
    pode_atualizar: bool
    pode_encerrar: bool
    resultado_opcoes: list = field(default_factory=list)

    @property
    def mostra_prestador(self) -> bool:
        """Se a tela mostra o bloco de prestador.

        Só a visita — a manutenção — carrega um prestador nesta jornada. A
        ocorrência de rotina e a demanda não declaram quem executa, e inventar
        esse bloco para elas seria afirmar um vínculo que o registro não tem.
        """
        return self.origem == ORIGEM_MANUTENCAO

    @property
    def nome_do_prestador(self) -> str:
        """O nome do prestador como a tela o lê.

        Quando há cadastro vinculado, é o nome dele. Quando não há, resta o nome
        que a própria visita informa — o título do registro —, porque a ausência
        de cadastro não pode esconder quem foi combinado.
        """
        return self.fornecedor or self.titulo

    @property
    def exige_texto_no_encerramento(self) -> bool:
        """Se encerrar exige dizer o que foi feito.

        A manutenção não termina em silêncio: o domínio cobra o serviço
        realizado. A ocorrência cobra o resultado encontrado, e a demanda apenas
        muda de situação — para as duas, o texto é opcional.
        """
        return self.origem == ORIGEM_MANUTENCAO


def _contexto(condominio, local: str = "") -> str:
    """Onde o registro acontece, como a tela lê: condomínio e, se houver, local."""
    partes = [condominio.nome] if condominio is not None else []
    if local:
        partes.append(local)
    return " · ".join(partes)


def _nome_do_fornecedor(registro) -> tuple[str, bool]:
    """O nome do prestador vinculado, e se existe cadastro por trás dele."""
    fornecedor = getattr(registro, "fornecedor", None)
    if fornecedor is None:
        return "", False
    return (getattr(fornecedor, "nome_razao_social", "") or ""), True


def _atualizacoes_da_manutencao(manutencao) -> list[dict]:
    """O que o acompanhamento desta manutenção já registrou, do mais novo ao antigo."""
    eventos = (
        manutencao.historico.filter(tipo_evento=TipoEventoManutencao.ACOMPANHAMENTO)
        .select_related("usuario")
        .order_by("-data_hora")
    )
    return [_linha_do_evento(evento) for evento in eventos]


def _atualizacoes_da_demanda(demanda) -> list[dict]:
    """O que já se observou sobre a demanda, do mais novo ao antigo."""
    eventos = (
        demanda.historico.filter(tipo_evento=TipoEventoDemanda.OBSERVACAO)
        .select_related("usuario")
        .order_by("-data_hora")
    )
    return [_linha_do_evento(evento) for evento in eventos]


def _linha_do_evento(evento) -> dict:
    """Um evento do histórico como a tela o apresenta."""
    usuario = getattr(evento, "usuario", None)
    return {
        "texto": evento.descricao,
        "autor": getattr(usuario, "full_name", "") or getattr(usuario, "email", ""),
        "quando": evento.data_hora,
    }


def _para_manutencao(manutencao, usuario) -> Acompanhamento:
    situacoes = situacoes_da_manutencao(usuario, manutencao)
    fornecedor, vinculado = _nome_do_fornecedor(manutencao)
    return Acompanhamento(
        origem=ORIGEM_MANUTENCAO,
        objeto=manutencao,
        titulo=manutencao.titulo,
        contexto=_contexto(manutencao.condominio, getattr(manutencao.local, "nome", "")),
        fornecedor=fornecedor,
        fornecedor_vinculado=vinculado,
        iniciado_em=manutencao.data_inicio,
        atualizacoes=_atualizacoes_da_manutencao(manutencao),
        # Não se começa o que já começou: enquanto o serviço está em andamento,
        # o gesto de início não é oferecido — o que cabe ali é atualizar e
        # encerrar. Assim como a ocorrência, que só inicia enquanto pendente.
        pode_iniciar=(
            manutencao.status != StatusManutencao.EM_ANDAMENTO
            and StatusManutencao.EM_ANDAMENTO in situacoes
        ),
        pode_atualizar=pode_editar_manutencao(usuario, manutencao),
        pode_encerrar=pode_concluir_manutencao(usuario, manutencao),
    )


def _para_rotina(execucao, usuario) -> Acompanhamento:
    rotina = execucao.rotina
    fornecedor, vinculado = _nome_do_fornecedor(rotina)
    return Acompanhamento(
        origem=ORIGEM_ROTINA,
        objeto=execucao,
        titulo=rotina.titulo,
        contexto=_contexto(execucao.condominio, getattr(rotina.local, "nome", "")),
        fornecedor=fornecedor,
        fornecedor_vinculado=vinculado,
        iniciado_em=execucao.iniciado_em,
        atualizacoes=_atualizacoes_da_execucao(execucao),
        pode_iniciar=pode_iniciar_execucao(usuario, execucao),
        pode_atualizar=(pode_ver_execucao(usuario, execucao) and not execucao.esta_encerrada),
        pode_encerrar=pode_concluir_execucao(usuario, execucao),
        resultado_opcoes=list(ResultadoExecucao.choices),
    )


def _atualizacoes_da_execucao(execucao) -> list[dict]:
    """O relato acumulado da ocorrência, quando houver.

    A ocorrência não tem histórico próprio: o relato vive no mesmo campo do que
    se encontrou. Aqui ele aparece como a única linha de memória que existe.
    """
    texto = (execucao.observacao_execucao or "").strip()
    if not texto:
        return []
    return [{"texto": texto, "autor": "", "quando": None}]


def _para_demanda(demanda, usuario) -> Acompanhamento:
    situacoes = situacoes_da_demanda(usuario, demanda)
    return Acompanhamento(
        origem=ORIGEM_DEMANDA,
        objeto=demanda,
        titulo=demanda.titulo,
        contexto=_contexto(demanda.condominio, demanda.local_para_exibir or ""),
        fornecedor="",
        fornecedor_vinculado=False,
        iniciado_em=None,
        atualizacoes=_atualizacoes_da_demanda(demanda),
        pode_iniciar=False,
        pode_atualizar=pode_adicionar_observacao(usuario, demanda),
        pode_encerrar=(StatusDemanda.CONCLUIDA in situacoes),
    )


def obter_acompanhamento(*, origem: str, pk, usuario) -> Acompanhamento:
    """O registro que a tela vai operar, sempre dentro do que a pessoa alcança.

    O identificador vem da tela e não é aceito como verdade: o registro é
    buscado no conjunto que a própria permissão do domínio libera. Fora do
    escopo — outro condomínio, identificador inventado —, a resposta é 404.
    """
    if origem == ORIGEM_MANUTENCAO:
        manutencao = get_object_or_404(
            Manutencao.objects.filter(
                condominio__in=condominios_das_manutencoes(usuario)
            ).select_related("condominio", "local", "fornecedor"),
            pk=pk,
        )
        return _para_manutencao(manutencao, usuario)

    if origem == ORIGEM_ROTINA:
        execucao = get_object_or_404(
            execucoes_para_ver(usuario).select_related(
                "condominio", "rotina", "rotina__local", "rotina__fornecedor"
            ),
            pk=pk,
        )
        return _para_rotina(execucao, usuario)

    if origem == ORIGEM_DEMANDA:
        demanda = get_object_or_404(
            Demanda.objects.filter(
                condominio__in=condominios_das_demandas(usuario)
            ).select_related("condominio", "local"),
            pk=pk,
        )
        return _para_demanda(demanda, usuario)

    raise Http404("Origem de acompanhamento desconhecida.")


def iniciar(acompanhamento: Acompanhamento, *, usuario) -> None:
    """Começa o acompanhamento, pelo gesto que o domínio já oferece.

    Começar uma manutenção ou uma ocorrência registra o momento do início pelo
    relógio do servidor, dentro do próprio serviço do domínio. A permissão é a
    que o domínio concede — sem ela, a operação é negada.
    """
    if not acompanhamento.pode_iniciar:
        raise PermissionDenied("Você não pode iniciar este acompanhamento.")

    if acompanhamento.origem == ORIGEM_MANUTENCAO:
        alterar_status_da_manutencao(
            manutencao=acompanhamento.objeto,
            usuario=usuario,
            novo_status=StatusManutencao.EM_ANDAMENTO,
        )
        return

    if acompanhamento.origem == ORIGEM_ROTINA:
        iniciar_execucao(execucao=acompanhamento.objeto, usuario=usuario)
        return

    raise PermissionDenied("Este acompanhamento não tem início próprio.")


def registrar_atualizacao(acompanhamento: Acompanhamento, *, usuario, texto: str) -> None:
    """Deixa uma atualização curta no histórico canônico do registro."""
    if not acompanhamento.pode_atualizar:
        raise PermissionDenied("Você não pode atualizar este acompanhamento.")

    if acompanhamento.origem == ORIGEM_MANUTENCAO:
        registrar_atualizacao_da_manutencao(acompanhamento.objeto, usuario, texto)
        return

    if acompanhamento.origem == ORIGEM_ROTINA:
        registrar_atualizacao_da_execucao(
            execucao=acompanhamento.objeto, usuario=usuario, texto=texto
        )
        return

    registrar_observacao(demanda=acompanhamento.objeto, usuario=usuario, texto=texto)


def encerrar(
    acompanhamento: Acompanhamento, *, usuario, texto: str = "", resultado: str = ""
) -> None:
    """Encerra o acompanhamento pelo fechamento que o domínio já tem.

    O fechamento não é recriado aqui: cada origem usa a sua própria conclusão.
    Onde a conclusão exige dizer o que se encontrou ou o que foi feito, o domínio
    cobra — e a tela devolve a recusa como veio.
    """
    if not acompanhamento.pode_encerrar:
        raise PermissionDenied("Você não pode encerrar este acompanhamento.")

    if acompanhamento.origem == ORIGEM_MANUTENCAO:
        concluir_manutencao(
            manutencao=acompanhamento.objeto,
            usuario=usuario,
            descricao_servico=texto,
        )
        return

    if acompanhamento.origem == ORIGEM_ROTINA:
        execucao = acompanhamento.objeto
        concluir_execucao(
            execucao=execucao,
            usuario=usuario,
            resultado=resultado,
            observacao=execucao.observacao_execucao,
        )
        return

    alterar_status_da_demanda(
        demanda=acompanhamento.objeto,
        usuario=usuario,
        novo_status=StatusDemanda.CONCLUIDA,
    )
