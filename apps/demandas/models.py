"""Modelos do domínio de demandas operacionais.

Uma demanda é algo com consequência operacional que precisa de acompanhamento:
um vazamento, uma compra, um orçamento a pedir, um equipamento com problema, uma
reclamação que exige ação. Não é conversa, comentário, feed nem mensagem — por
isso não existe thread, curtida, menção nem anexo social. O que existe é a
evidência: a foto ou o vídeo que sustenta o que se sabe sobre a demanda, presa a
ela e, quando houver, ao evento que a trouxe.

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

from apps.cadastros.models import Equipamento, LocalOperacional
from apps.condominios.models import Condominio
from apps.core.storage import FORMATO_SHA256, erros_de_referencia_de_storage

from .constants import (
    MIMES_DE_EVIDENCIA,
    AguardandoTipo,
    ImpactoDemanda,
    OrigemDemanda,
    PrioridadeDemanda,
    RiscoDemanda,
    StatusDemanda,
    TipoEventoDemanda,
    TipoMidiaEvidencia,
    TipoProblema,
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
    local = models.ForeignKey(
        LocalOperacional,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="demandas",
        verbose_name="Local",
        help_text="Onde a demanda acontece. Pode ficar sem local definido.",
    )
    local_contexto = models.CharField(
        "Local",
        max_length=200,
        blank=True,
        help_text="Onde a demanda acontece: bloco, unidade, área comum, equipamento.",
    )
    # A classificação do que a demanda é. Não substitui o relato: ela existe
    # para agrupar, reconhecer repetição e sugerir o próximo passo. Vazio quer
    # dizer "não identificado" — e "não identificado" é diferente de "outro".
    equipamento = models.ForeignKey(
        Equipamento,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="demandas",
        verbose_name="Equipamento",
        help_text="O equipamento envolvido, quando a demanda é sobre um.",
    )
    tipo_problema = models.CharField(
        "Tipo de problema",
        max_length=20,
        choices=TipoProblema.choices,
        null=True,
        blank=True,
        help_text="A natureza do que está acontecendo, quando identificada.",
    )
    impacto = models.CharField(
        "Impacto",
        max_length=20,
        choices=ImpactoDemanda.choices,
        null=True,
        blank=True,
        help_text="Até onde a consequência alcança, quando identificado.",
    )
    risco = models.CharField(
        "Risco",
        max_length=20,
        choices=RiscoDemanda.choices,
        null=True,
        blank=True,
        help_text="O risco identificado. Vazio é 'não avaliado', que é diferente de "
        "'sem risco'.",
    )
    classificacao_sugerida = models.JSONField(
        "Classificação sugerida",
        null=True,
        blank=True,
        help_text="O que o EITHRA propôs ao registrar a demanda, com origem e "
        "confiança. Nunca é tratado como oficial: existe para comparar sugestão, "
        "confirmação e correção mais adiante.",
    )
    # O acompanhamento operacional. Não é situação: diz o que a demanda espera
    # agora, o que vem depois, para quando foi prometida e o que está travando.
    # Nada aqui muda o ciclo da demanda — previsão vencida não conclui, espera
    # pelo fornecedor não altera situação, impedimento resolvido não encerra.
    aguardando_tipo = models.CharField(
        "Aguardando",
        max_length=20,
        choices=AguardandoTipo.choices,
        blank=True,
        default=AguardandoTipo.NINGUEM,
        help_text="Por quem — ou pelo quê — a demanda espera agora. Vazio é não "
        "estar aguardando ninguém.",
    )
    aguardando_descricao = models.CharField(
        "O que se espera",
        max_length=200,
        blank=True,
        help_text="Quem ou o que exatamente se espera, quando couber um nome.",
    )
    proxima_acao = models.CharField(
        "Próxima ação",
        max_length=200,
        blank=True,
        help_text="O próximo passo combinado, com as palavras da operação.",
    )
    previsao = models.DateField(
        "Previsão",
        null=True,
        blank=True,
        help_text="A data prometida por quem está sendo aguardado. É diferente do "
        "prazo: não é compromisso da demanda e vencê-la não muda nada.",
    )
    impedimento = models.CharField(
        "Impedimento",
        max_length=200,
        blank=True,
        help_text="O que está travando a demanda, quando houver. Vazio é não haver "
        "impedimento conhecido.",
    )
    responsavel_acompanhamento = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="demandas_acompanhadas",
        verbose_name="Quem acompanha",
        help_text="Quem segue a demanda de perto. É diferente de quem responde "
        "por ela.",
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
            # Reconhecer repetição no mesmo equipamento é uma leitura por
            # condomínio: o índice segue a mesma pergunta que a leitura faz.
            models.Index(fields=["condominio", "equipamento"], name="demanda_cond_equip_idx"),
        ]

    def __str__(self):
        return self.titulo

    def clean(self):
        erros = {}
        if not (self.titulo or "").strip():
            erros["titulo"] = "Escreva o que a demanda precisa resolver."
        if self.local_id and self.local.condominio_id != self.condominio_id:
            erros["local"] = "O local escolhido não é deste condomínio."
        # Regra objetiva: equipamento e demanda são da mesma casa. Sem isso, a
        # classificação apontaria para um aparelho que não existe neste prédio.
        if self.equipamento_id and self.equipamento.condominio_id != self.condominio_id:
            erros["equipamento"] = "O equipamento escolhido não é deste condomínio."
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

    @property
    def local_para_exibir(self) -> str:
        """O local como deve ser lido: o cadastro atual, ou o texto legado.

        Demandas antigas guardaram o local como texto livre em
        ``local_contexto``. Enquanto não houver um local cadastrado, esse texto
        continua sendo a resposta — nada é apagado nem preenchido sozinho.
        """
        if self.local_id:
            return self.local.nome
        return self.local_contexto or ""

    @property
    def tem_acompanhamento(self) -> bool:
        """Há o que mostrar no bloco de acompanhamento."""
        return bool(
            self.aguardando_tipo
            or self.aguardando_descricao
            or self.proxima_acao
            or self.previsao
            or self.impedimento
            or self.responsavel_acompanhamento_id
        )

    @property
    def aguardando_para_exibir(self) -> str:
        """Por quem se espera, como deve ser lido.

        O nome próprio diz mais que a categoria; sem nome, a categoria
        responde. Sem nenhum dos dois, não se está aguardando ninguém.
        """
        if self.aguardando_descricao:
            return self.aguardando_descricao
        if self.aguardando_tipo:
            return self.get_aguardando_tipo_display()
        return ""

    @property
    def previsao_vencida(self) -> bool:
        """A data prometida passou e a demanda continua de pé.

        É um sinal para quem acompanha, não uma consequência: nada muda de
        situação por causa disso.
        """
        if not self.previsao or self.esta_encerrada:
            return False
        return self.previsao < timezone.localdate()

    @property
    def ultima_movimentacao(self):
        """O último evento da demanda, como ele ficou na memória.

        É daqui que sai "quando foi a última movimentação", e não de
        ``atualizado_em``: o carimbo de atualização anda a cada gravação, sem
        dizer se alguma coisa de fato aconteceu. A resposta honesta está no
        histórico, que só guarda o que mudou. Por isso também não existe campo
        para isso — a informação já está lá, derivada.
        """
        return self.historico.order_by("-data_hora").first()


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


class EvidenciaDemanda(models.Model):
    """Uma foto ou um vídeo que sustenta o que se sabe sobre a demanda.

    A evidência não é documento nem anexo social: é a prova do que foi
    relatado. Por isso ela sempre acompanha uma movimentação — o relato que a
    trouxe, a mudança de situação que ela explica ou o próprio ato de anexá-la
    depois —, e nunca aparece solta no histórico. Anexar uma evidência nunca
    conclui, nunca cancela e nunca cria uma demanda: o arquivo confirma o
    relato, não decide por ele.

    A frase que a explica vive no evento, e não aqui: guardar a mesma
    explicação nos dois lugares criaria duas versões da mesma história. O que
    fica nesta linha é só o necessário para reencontrar o arquivo — a
    referência do objeto no bucket privado, o tipo real, o tamanho, o resumo do
    conteúdo e quem anexou, quando. Nunca uma URL pública ou assinada: o acesso
    é gerado sob demanda, como nos demais arquivos privados.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    demanda = models.ForeignKey(
        Demanda,
        on_delete=models.PROTECT,
        related_name="evidencias",
        verbose_name="Demanda",
    )
    evento = models.ForeignKey(
        HistoricoDemanda,
        on_delete=models.PROTECT,
        related_name="evidencias",
        verbose_name="Movimentação",
        help_text="O evento que a evidência acompanha e explica.",
    )
    tipo_midia = models.CharField(
        "Tipo de mídia",
        max_length=10,
        choices=TipoMidiaEvidencia.choices,
    )
    nome_original = models.CharField(
        "Nome do arquivo enviado",
        max_length=255,
        blank=True,
        default="",
        help_text="Como o arquivo chegou. O nome físico não depende dele.",
    )

    # Referência do objeto no storage privado. Nunca a URL em si.
    storage_provider = models.CharField("Provedor", max_length=50, blank=True, default="")
    storage_bucket = models.CharField(
        "Local de armazenamento", max_length=150, blank=True, default=""
    )
    storage_key = models.CharField("Referência do objeto", max_length=500)
    mime_type = models.CharField("Tipo do arquivo", max_length=120)
    tamanho_bytes = models.PositiveBigIntegerField("Tamanho", default=0)
    sha256 = models.CharField(
        "Resumo do conteúdo",
        max_length=64,
        validators=[FORMATO_SHA256],
        help_text="Reconhece o mesmo arquivo repetido na mesma demanda.",
    )

    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="evidencias_demanda_criadas",
        verbose_name="Quem anexou",
        help_text="Preservado para rastreabilidade.",
    )
    criado_em = models.DateTimeField("Quando", auto_now_add=True)

    class Meta:
        verbose_name = "Evidência da demanda"
        verbose_name_plural = "Evidências da demanda"
        ordering = ["criado_em"]
        constraints = [
            # O mesmo arquivo não entra duas vezes na mesma demanda. Sem isso,
            # um envio repetido encheria o histórico com a mesma prova.
            models.UniqueConstraint(
                fields=["demanda", "sha256"],
                name="demanda_evidencia_unica_por_hash",
            ),
            models.CheckConstraint(
                condition=models.Q(tamanho_bytes__gte=0),
                name="demanda_evidencia_tamanho_nao_negativo",
            ),
            # Só foto e vídeo entram aqui. PDF e executável são recusados no
            # banco, e não apenas na validação da borda.
            models.CheckConstraint(
                condition=models.Q(mime_type__in=MIMES_DE_EVIDENCIA),
                name="demanda_evidencia_mime_permitido",
            ),
        ]
        indexes = [
            # Apoio à listagem das evidências de uma demanda.
            models.Index(
                fields=["demanda", "criado_em"],
                name="demanda_evid_data_idx",
            ),
        ]

    def clean(self):
        super().clean()
        erros: dict[str, str] = {}

        erros.update(erros_de_referencia_de_storage(self))

        if self.tamanho_bytes is not None and self.tamanho_bytes < 0:
            erros["tamanho_bytes"] = "O tamanho da evidência não pode ser negativo."

        # A prova pertence à mesma demanda que o evento que ela sustenta: sem
        # isso, uma foto de um caso apareceria no histórico de outro.
        if self.evento_id and self.evento.demanda_id != self.demanda_id:
            erros["evento"] = "A movimentação escolhida não é desta demanda."

        if erros:
            raise ValidationError(erros)

    def __str__(self) -> str:
        return f"Evidência {self.get_tipo_midia_display()} · {self.demanda}"
