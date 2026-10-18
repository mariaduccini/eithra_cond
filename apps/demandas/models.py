"""Modelos do domínio de demandas operacionais.

Uma demanda é algo com consequência operacional que precisa de acompanhamento:
um vazamento, uma compra, um orçamento a pedir, um equipamento com problema, uma
reclamação que exige ação. Não é conversa, comentário, feed nem mensagem — por
isso não existe thread, curtida, menção nem anexo social.

O registro guarda o que é preciso para responder por ela: o que é, quem
responde, quando vence, quanto pesa e em que situação está. O que muda ao longo
do tempo não é escrito por cima: cada alteração que importa vira uma linha de
histórico, e o histórico nunca é reescrito.
"""
import uuid

from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from apps.condominios.models import Condominio

from .constants import (
    OrigemDemanda,
    PrioridadeDemanda,
    StatusDemanda,
    TipoEventoDemanda,
)

# Situações que encerram a demanda: depois delas nada mais depende de uma ação.
SITUACOES_ENCERRADAS = (StatusDemanda.CONCLUIDA, StatusDemanda.CANCELADA)

# Peso de cada prioridade na ordenação da lista. Quanto menor, mais à frente.
_PESO_DA_PRIORIDADE = {
    PrioridadeDemanda.URGENTE: 0,
    PrioridadeDemanda.ALTA: 1,
    PrioridadeDemanda.NORMAL: 2,
    PrioridadeDemanda.BAIXA: 3,
}


class DemandaQuerySet(models.QuerySet):
    """Leituras reutilizáveis da demanda.

    São as perguntas que o painel "Hoje" vai fazer mais adiante — o que venceu,
    o que vence agora, o que é urgente, o que é meu. Ficam aqui, e não nas
    views, para serem respondidas do mesmo jeito em qualquer tela.
    """

    def abertas(self):
        """Demandas que ainda dependem de uma ação."""
        return self.exclude(status__in=SITUACOES_ENCERRADAS)

    def vencidas(self, hoje=None):
        """Prazo já passou e a demanda continua em aberto."""
        return self.abertas().filter(prazo__lt=hoje or timezone.localdate())

    def vencendo_hoje(self, hoje=None):
        """O prazo é hoje e a demanda continua em aberto."""
        return self.abertas().filter(prazo=(hoje or timezone.localdate()))

    def proximas(self, hoje=None, dias=7):
        """Vencem nos próximos dias, sem contar hoje nem as já vencidas."""
        referencia = hoje or timezone.localdate()
        return self.abertas().filter(
            prazo__gt=referencia,
            prazo__lte=referencia + timedelta(days=dias),
        )

    def sem_prazo(self):
        """Abertas que ainda não têm data — candidatas a ganhar uma."""
        return self.abertas().filter(prazo__isnull=True)

    def urgentes(self):
        """Prioridade urgente, ainda em aberto."""
        return self.abertas().filter(prioridade=PrioridadeDemanda.URGENTE)

    def minhas(self, usuario):
        """Abertas sob responsabilidade desta pessoa."""
        if not getattr(usuario, "pk", None):
            return self.none()
        return self.abertas().filter(responsavel_id=usuario.pk)

    def na_ordem_de_atencao(self):
        """A ordem em que a lista deve ser lida: o que pesa mais primeiro.

        Urgentes, depois vencidas, depois o resto por prioridade e prazo mais
        próximo. Sem data fica no fim. Nada de algoritmo: só a leitura natural
        de quem abre a lista para decidir o que fazer agora.
        """
        hoje = timezone.localdate()
        peso = models.Case(
            *[
                models.When(prioridade=valor, then=models.Value(peso))
                for valor, peso in _PESO_DA_PRIORIDADE.items()
            ],
            default=models.Value(len(_PESO_DA_PRIORIDADE)),
            output_field=models.IntegerField(),
        )
        return self.annotate(
            _urgente=models.Case(
                models.When(prioridade=PrioridadeDemanda.URGENTE, then=0),
                default=1,
                output_field=models.IntegerField(),
            ),
            _vencida=models.Case(
                models.When(prazo__lt=hoje, then=0),
                default=1,
                output_field=models.IntegerField(),
            ),
            _peso=peso,
        ).order_by(
            "_urgente",
            "_vencida",
            "_peso",
            models.F("prazo").asc(nulls_last=True),
            "-criado_em",
        )


class Demanda(models.Model):
    """Uma pendência operacional do condomínio, com responsável e prazo.

    A demanda é curta de propósito. Não há fluxo formal de aprovação, fila nem
    etapas de atendimento: ela nasce aberta, pode andar, esperar, concluir ou ser
    cancelada. O que dá contexto a ela — local, responsável, prazo, prioridade —
    é dado do próprio registro, não uma entidade à parte.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    condominio = models.ForeignKey(
        Condominio,
        on_delete=models.PROTECT,
        related_name="demandas",
        verbose_name="Condomínio",
    )
    titulo = models.CharField(
        "Título",
        max_length=200,
        help_text="Descrição curta e direta do que precisa de acompanhamento.",
    )
    descricao = models.TextField(
        "Descrição",
        blank=True,
        help_text="Detalhe opcional: o que se sabe até agora sobre a demanda.",
    )
    status = models.CharField(
        "Situação",
        max_length=20,
        choices=StatusDemanda.choices,
        default=StatusDemanda.ABERTA,
    )
    prioridade = models.CharField(
        "Prioridade",
        max_length=20,
        choices=PrioridadeDemanda.choices,
        default=PrioridadeDemanda.NORMAL,
    )
    origem = models.CharField(
        "Origem",
        max_length=20,
        choices=OrigemDemanda.choices,
        default=OrigemDemanda.MANUAL,
        help_text="De onde a demanda veio. Nesta fase, sempre registrada pela gestão.",
    )
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="demandas_responsavel",
        verbose_name="Responsável",
        help_text="Quem responde por ela hoje. Pode ficar sem responsável.",
    )
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="demandas_criadas",
        verbose_name="Criada por",
        help_text="Quem registrou a demanda. Preservado para rastreabilidade.",
    )
    criado_em = models.DateTimeField("Criada em", auto_now_add=True)
    atualizado_em = models.DateTimeField("Atualizada em", auto_now=True)
    prazo = models.DateField(
        "Prazo",
        null=True,
        blank=True,
        help_text="Data em que a demanda deve estar resolvida, quando houver.",
    )
    concluido_em = models.DateTimeField("Concluída em", null=True, blank=True)
    cancelado_em = models.DateTimeField("Cancelada em", null=True, blank=True)
    local_contexto = models.CharField(
        "Local",
        max_length=200,
        blank=True,
        help_text="Onde a demanda acontece: bloco, unidade, área comum, equipamento.",
    )
    ativo = models.BooleanField(
        "Ativa",
        default=True,
        help_text="Desativar retira a demanda da operação sem apagar o histórico.",
    )

    objects = DemandaQuerySet.as_manager()

    class Meta:
        verbose_name = "Demanda"
        verbose_name_plural = "Demandas"
        ordering = ["-criado_em"]
        constraints = [
            # A situação diz o que aconteceu; as datas dizem quando. As duas
            # precisam concordar, senão a demanda mente sobre o próprio passado.
            models.CheckConstraint(
                condition=(
                    models.Q(status=StatusDemanda.CONCLUIDA, concluido_em__isnull=False)
                    | (~models.Q(status=StatusDemanda.CONCLUIDA) & models.Q(concluido_em__isnull=True))
                ),
                name="demanda_concluida_registra_quando",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status=StatusDemanda.CANCELADA, cancelado_em__isnull=False)
                    | (~models.Q(status=StatusDemanda.CANCELADA) & models.Q(cancelado_em__isnull=True))
                ),
                name="demanda_cancelada_registra_quando",
            ),
        ]
        indexes = [
            models.Index(fields=["condominio", "status"], name="demanda_cond_status_idx"),
            models.Index(fields=["condominio", "prazo"], name="demanda_cond_prazo_idx"),
            models.Index(fields=["condominio", "prioridade"], name="demanda_cond_prior_idx"),
            models.Index(fields=["responsavel", "status"], name="demanda_resp_status_idx"),
        ]

    def __str__(self):
        return self.titulo

    def clean(self):
        erros = {}
        if not (self.titulo or "").strip():
            erros["titulo"] = "Escreva o que a demanda precisa resolver."
        if self.status == StatusDemanda.CONCLUIDA and self.concluido_em is None:
            erros["concluido_em"] = "Uma demanda concluída precisa registrar quando foi concluída."
        if self.status != StatusDemanda.CONCLUIDA and self.concluido_em is not None:
            erros["concluido_em"] = "Só uma demanda concluída registra a data de conclusão."
        if self.status == StatusDemanda.CANCELADA and self.cancelado_em is None:
            erros["cancelado_em"] = "Uma demanda cancelada precisa registrar quando foi cancelada."
        if self.status != StatusDemanda.CANCELADA and self.cancelado_em is not None:
            erros["cancelado_em"] = "Só uma demanda cancelada registra a data de cancelamento."
        if erros:
            raise ValidationError(erros)

    @property
    def esta_encerrada(self):
        """Concluída ou cancelada: não muda mais de situação."""
        return self.status in (StatusDemanda.CONCLUIDA, StatusDemanda.CANCELADA)


class HistoricoDemanda(models.Model):
    """O que aconteceu com a demanda, na ordem em que aconteceu.

    Guarda decisão, não ruído: quem registrou e editou, quando o responsável, a
    prioridade, o prazo ou a situação mudaram, e quando ela foi concluída ou
    cancelada. As linhas são acrescentadas e nunca reescritas — é a memória da
    demanda. Quando útil, o evento guarda o antes e o depois em ``dados_anteriores``
    e ``dados_novos``, para que a mudança possa ser lida sem adivinhação.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    demanda = models.ForeignKey(
        Demanda,
        on_delete=models.CASCADE,
        related_name="historico",
        verbose_name="Demanda",
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="eventos_de_demanda",
        verbose_name="Quem registrou",
        help_text="Quem provocou o evento. Preservado para rastreabilidade.",
    )
    data_hora = models.DateTimeField("Quando", default=timezone.now)
    tipo_evento = models.CharField(
        "Tipo de evento",
        max_length=20,
        choices=TipoEventoDemanda.choices,
    )
    descricao = models.CharField(
        "Descrição",
        max_length=255,
        help_text="O evento em linguagem humana, do jeito que deve ser lembrado.",
    )
    dados_anteriores = models.JSONField(
        "Antes",
        null=True,
        blank=True,
        help_text="Como estava antes da mudança, quando fizer sentido registrar.",
    )
    dados_novos = models.JSONField(
        "Depois",
        null=True,
        blank=True,
        help_text="Como ficou depois da mudança, quando fizer sentido registrar.",
    )

    class Meta:
        verbose_name = "Evento da demanda"
        verbose_name_plural = "Histórico da demanda"
        ordering = ["-data_hora"]
        indexes = [
            models.Index(fields=["demanda", "data_hora"], name="hist_demanda_data_idx"),
        ]

    def __str__(self):
        return f"{self.get_tipo_evento_display()} · {self.demanda}"
