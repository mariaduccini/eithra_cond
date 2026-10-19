"""Modelos do domínio de manutenções operacionais.

Uma manutenção é a intervenção em algo do condomínio: a bomba que parou, o
elevador com falha, a dedetização programada. Ela não é um chamado nem um
processo com etapas — é o serviço em si, com o que motivou, quem responde,
quando está previsto, o que foi feito e quanto custou.

O registro guarda o que é preciso para responder por ele: o problema, o local e
o equipamento, o fornecedor quando o serviço é de terceiro, o responsável, a
previsão e os custos. O que muda ao longo do tempo não é escrito por cima: cada
alteração que importa vira uma linha de histórico, e o histórico nunca é
reescrito.

Duas coisas não se confundem aqui. ``status = CANCELADA`` é um desfecho
operacional — a manutenção deixou de acontecer, e isso fica registrado com
quem cancelou e quando. ``ativo = False`` é uma retirada administrativa: o
registro sai das listas sem apagar nada. Uma não substitui a outra.
"""
import uuid

from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from apps.cadastros.models import Equipamento, Fornecedor, LocalOperacional
from apps.condominios.models import Condominio
from apps.core.constants import Prioridade
from apps.demandas.models import Demanda

from .constants import StatusManutencao, TipoEventoManutencao, TipoManutencao

# Situações que encerram a manutenção: depois delas nada mais depende de uma ação.
SITUACOES_ENCERRADAS = (StatusManutencao.CONCLUIDA, StatusManutencao.CANCELADA)

# Peso de cada prioridade na ordenação da lista. Quanto menor, mais à frente.
_PESO_DA_PRIORIDADE = {
    Prioridade.URGENTE: 0,
    Prioridade.ALTA: 1,
    Prioridade.NORMAL: 2,
    Prioridade.BAIXA: 3,
}


def _texto(valor: str | None) -> str:
    """Colapsa espaços repetidos e devolve string vazia quando não há conteúdo."""
    return " ".join((valor or "").split())


def _texto_longo(valor: str | None) -> str:
    """Preserva os parágrafos de um texto livre, sem espaços nas pontas."""
    return (valor or "").strip()


class ManutencaoQuerySet(models.QuerySet):
    """Leituras reutilizáveis da manutenção.

    São as perguntas que a lista e o painel fazem: o que está atrasado pela data
    prevista, o que vence agora, o que é urgente, o que é meu. A regra de
    atraso usa apenas ``data_prevista`` — planejamento —, e nunca as datas reais
    de início e conclusão, que dizem o que aconteceu, não o que era esperado.
    """

    def abertas(self):
        """Manutenções que ainda dependem de uma ação."""
        return self.exclude(status__in=SITUACOES_ENCERRADAS)

    def atrasadas(self, hoje=None):
        """A data prevista passou e a manutenção continua em aberto."""
        return self.abertas().filter(data_prevista__lt=hoje or timezone.localdate())

    def previstas_para_hoje(self, hoje=None):
        """Previstas para hoje e ainda em aberto."""
        return self.abertas().filter(data_prevista=(hoje or timezone.localdate()))

    def proximas(self, hoje=None, dias=7):
        """Previstas para os próximos dias, sem contar hoje nem as atrasadas."""
        referencia = hoje or timezone.localdate()
        return self.abertas().filter(
            data_prevista__gt=referencia,
            data_prevista__lte=referencia + timedelta(days=dias),
        )

    def sem_previsao(self):
        """Abertas que ainda não têm data prevista."""
        return self.abertas().filter(data_prevista__isnull=True)

    def urgentes(self):
        """Prioridade urgente, ainda em aberto."""
        return self.abertas().filter(prioridade=Prioridade.URGENTE)

    def minhas(self, usuario):
        """Abertas sob responsabilidade desta pessoa."""
        if not getattr(usuario, "pk", None):
            return self.none()
        return self.abertas().filter(responsavel_id=usuario.pk)

    def do_condominio(self, condominio):
        """Manutenções deste condomínio. Sem condomínio resolvido, conjunto vazio."""
        if condominio is None or not getattr(condominio, "pk", None):
            return self.none()
        return self.filter(condominio_id=condominio.pk)

    def da_demanda(self, demanda):
        """Manutenções que nasceram desta demanda."""
        if demanda is None or not getattr(demanda, "pk", None):
            return self.none()
        return self.filter(demanda_origem_id=demanda.pk)

    def na_ordem_de_atencao(self):
        """A ordem em que a lista deve ser lida: o que pesa mais primeiro.

        Urgentes, depois as atrasadas pela data prevista, depois o resto por
        prioridade e previsão mais próxima. Sem previsão fica no fim. Nada de
        algoritmo: só a leitura natural de quem abre a lista para decidir o que
        fazer agora.
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
                models.When(prioridade=Prioridade.URGENTE, then=0),
                default=1,
                output_field=models.IntegerField(),
            ),
            _atrasada=models.Case(
                models.When(data_prevista__lt=hoje, then=0),
                default=1,
                output_field=models.IntegerField(),
            ),
            _peso=peso,
        ).order_by(
            "_urgente",
            "_atrasada",
            "_peso",
            models.F("data_prevista").asc(nulls_last=True),
            "-criado_em",
        )


class Manutencao(models.Model):
    """Um serviço a ser feito em algo do condomínio, com previsão e custo.

    A manutenção é curta de propósito. Não há fluxo de aprovação, ordem de
    serviço nem etapas: ela nasce aberta, pode ser programada, começar, esperar,
    concluir ou ser cancelada. Local, equipamento e fornecedor são cadastros do
    próprio condomínio — a manutenção aponta para eles, e não os recria.

    Os custos aqui são operacionais: dizem quanto se esperava gastar e quanto se
    gastou. O Eithra não gera despesa, não cria lançamento e não toca no
    balanço a partir daqui.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    condominio = models.ForeignKey(
        Condominio,
        on_delete=models.PROTECT,
        related_name="manutencoes",
        verbose_name="Condomínio",
    )
    demanda_origem = models.ForeignKey(
        Demanda,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="manutencoes_geradas",
        verbose_name="Demanda de origem",
        help_text="De onde a manutenção veio, quando nasceu de uma demanda. "
        "Preserva a procedência sem alterar a demanda.",
    )
    tipo = models.CharField(
        "Tipo",
        max_length=20,
        choices=TipoManutencao.choices,
        default=TipoManutencao.CORRETIVA,
    )
    titulo = models.CharField(
        "Título",
        max_length=200,
        help_text="Descrição curta e direta do serviço.",
    )
    descricao_problema = models.TextField(
        "Problema",
        blank=True,
        help_text="O que está acontecendo e motivou a manutenção.",
    )
    descricao_servico = models.TextField(
        "Serviço realizado",
        blank=True,
        help_text="O que foi feito. Obrigatório para concluir a manutenção.",
    )
    status = models.CharField(
        "Situação",
        max_length=20,
        choices=StatusManutencao.choices,
        default=StatusManutencao.ABERTA,
    )
    prioridade = models.CharField(
        "Prioridade",
        max_length=20,
        choices=Prioridade.choices,
        default=Prioridade.NORMAL,
    )
    local = models.ForeignKey(
        LocalOperacional,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="manutencoes",
        verbose_name="Local",
        help_text="Onde o serviço acontece. Pode ficar sem local definido.",
    )
    equipamento = models.ForeignKey(
        Equipamento,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="manutencoes",
        verbose_name="Equipamento",
        help_text="O que recebe o serviço, quando é um equipamento.",
    )
    fornecedor = models.ForeignKey(
        Fornecedor,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="manutencoes",
        verbose_name="Fornecedor",
        help_text="Quem executa o serviço, quando é terceirizado.",
    )
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="manutencoes_responsavel",
        verbose_name="Responsável",
        help_text="Quem responde por ela hoje. Pode ficar sem responsável.",
    )
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="manutencoes_criadas",
        verbose_name="Criada por",
        help_text="Quem registrou a manutenção. Preservado para rastreabilidade.",
    )
    data_prevista = models.DateField(
        "Data prevista",
        null=True,
        blank=True,
        help_text="Quando o serviço está planejado para acontecer.",
    )
    data_inicio = models.DateTimeField("Iniciada em", null=True, blank=True)
    data_conclusao = models.DateTimeField("Concluída em", null=True, blank=True)
    custo_previsto = models.DecimalField(
        "Custo previsto",
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Quanto se espera gastar. Valor operacional, não contábil.",
    )
    custo_real = models.DecimalField(
        "Custo real",
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Quanto custou de fato. Valor operacional, não contábil.",
    )
    criado_em = models.DateTimeField("Criada em", auto_now_add=True)
    atualizado_em = models.DateTimeField("Atualizada em", auto_now=True)
    ativo = models.BooleanField(
        "Ativa",
        default=True,
        help_text="Desativar retira a manutenção da operação sem apagar o histórico.",
    )

    objects = ManutencaoQuerySet.as_manager()

    class Meta:
        verbose_name = "Manutenção"
        verbose_name_plural = "Manutenções"
        ordering = ["-criado_em"]
        constraints = [
            # Custo é dinheiro gasto: não existe negativo. O vazio continua
            # válido — "não sei ainda" é uma resposta honesta.
            models.CheckConstraint(
                condition=models.Q(custo_previsto__isnull=True)
                | models.Q(custo_previsto__gte=0),
                name="manut_custo_previsto_nao_negativo",
            ),
            models.CheckConstraint(
                condition=models.Q(custo_real__isnull=True)
                | models.Q(custo_real__gte=0),
                name="manut_custo_real_nao_negativo",
            ),
            # A situação diz o que aconteceu; a data diz quando. Uma manutenção
            # cancelada não registra conclusão — cancelar não é concluir.
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status=StatusManutencao.CONCLUIDA,
                        data_conclusao__isnull=False,
                    )
                    | (
                        ~models.Q(status=StatusManutencao.CONCLUIDA)
                        & models.Q(data_conclusao__isnull=True)
                    )
                ),
                name="manutencao_concluida_registra_quando",
            ),
        ]
        indexes = [
            models.Index(fields=["condominio", "status"], name="manut_cond_status_idx"),
            models.Index(fields=["condominio", "tipo"], name="manut_cond_tipo_idx"),
            models.Index(
                fields=["condominio", "data_prevista"], name="manut_cond_prevista_idx"
            ),
            models.Index(fields=["responsavel", "status"], name="manut_resp_status_idx"),
            models.Index(
                fields=["equipamento", "status"], name="manut_equip_status_idx"
            ),
            models.Index(fields=["fornecedor", "status"], name="manut_fornec_status_idx"),
        ]

    def __str__(self):
        return self.titulo

    def save(self, *args, **kwargs):
        """Guarda a manutenção já normalizada, venha ela do formulário ou não."""
        self.titulo = _texto(self.titulo)
        self.descricao_problema = _texto_longo(self.descricao_problema)
        self.descricao_servico = _texto_longo(self.descricao_servico)
        super().save(*args, **kwargs)

    def clean(self):
        """Recusa uma manutenção incoerente antes que ela chegue ao banco.

        Nada aqui pertence a outro condomínio: local, equipamento, fornecedor e
        demanda de origem são do mesmo condomínio da manutenção. E, quando há
        equipamento com local definido, o local da manutenção não pode apontar
        para outro lugar — a escolha explícita divergente é recusada, nunca
        sobrescrita em silêncio.
        """
        erros = {}
        if not _texto(self.titulo):
            erros["titulo"] = "Escreva o que precisa ser feito."

        if (
            self.local_id
            and self.condominio_id
            and self.local.condominio_id != self.condominio_id
        ):
            erros["local"] = "O local escolhido não é deste condomínio."

        if (
            self.equipamento_id
            and self.condominio_id
            and self.equipamento.condominio_id != self.condominio_id
        ):
            erros["equipamento"] = "O equipamento escolhido não é deste condomínio."

        if (
            self.fornecedor_id
            and self.condominio_id
            and self.fornecedor.condominio_id != self.condominio_id
        ):
            erros["fornecedor"] = "O fornecedor escolhido não é deste condomínio."

        if (
            self.demanda_origem_id
            and self.condominio_id
            and self.demanda_origem.condominio_id != self.condominio_id
        ):
            erros["demanda_origem"] = "A demanda de origem é de outro condomínio."

        # O equipamento e o local precisam concordar: um equipamento que fica em
        # um lugar não pode ser atendido em outro sem que alguém diga isso.
        if self.equipamento_id and self.local_id:
            local_do_equipamento = self.equipamento.local
            if (
                local_do_equipamento is not None
                and local_do_equipamento.pk != self.local_id
            ):
                erros["local"] = (
                    f"O equipamento escolhido fica em {local_do_equipamento.nome}. "
                    "Ajuste o local para combinar com o equipamento."
                )

        if self.status == StatusManutencao.CONCLUIDA:
            if self.data_conclusao is None:
                erros["data_conclusao"] = (
                    "Uma manutenção concluída precisa registrar quando foi concluída."
                )
            if not _texto_longo(self.descricao_servico):
                erros["descricao_servico"] = (
                    "Descreva o serviço realizado antes de concluir a manutenção."
                )
        elif self.data_conclusao is not None:
            erros["data_conclusao"] = (
                "Só uma manutenção concluída registra a data de conclusão."
            )

        if self.custo_previsto is not None and self.custo_previsto < 0:
            erros["custo_previsto"] = "O custo previsto não pode ser negativo."
        if self.custo_real is not None and self.custo_real < 0:
            erros["custo_real"] = "O custo real não pode ser negativo."

        if erros:
            raise ValidationError(erros)

    @property
    def esta_encerrada(self) -> bool:
        """Concluída ou cancelada: não muda mais de situação sem decisão de gestão."""
        return self.status in SITUACOES_ENCERRADAS


class HistoricoManutencao(models.Model):
    """O que aconteceu com a manutenção, na ordem em que aconteceu.

    Guarda decisão, não ruído: quem registrou e editou, quando o responsável, a
    prioridade, a data prevista ou a situação mudaram, quando o serviço foi
    descrito e quando ela foi concluída ou cancelada. As linhas são acrescentadas
    e nunca reescritas — é a memória da manutenção. Quando útil, o evento guarda
    o antes e o depois em ``dados_anteriores`` e ``dados_novos``, para que a
    mudança possa ser lida sem adivinhação.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    manutencao = models.ForeignKey(
        Manutencao,
        on_delete=models.CASCADE,
        related_name="historico",
        verbose_name="Manutenção",
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="eventos_de_manutencao",
        verbose_name="Quem registrou",
        help_text="Quem provocou o evento. Preservado para rastreabilidade.",
    )
    data_hora = models.DateTimeField("Quando", default=timezone.now)
    tipo_evento = models.CharField(
        "Tipo de evento",
        max_length=20,
        choices=TipoEventoManutencao.choices,
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
        verbose_name = "Evento da manutenção"
        verbose_name_plural = "Histórico da manutenção"
        ordering = ["-data_hora"]
        indexes = [
            models.Index(fields=["manutencao", "data_hora"], name="hist_manut_data_idx"),
        ]

    def __str__(self):
        return f"{self.get_tipo_evento_display()} · {self.manutencao}"
