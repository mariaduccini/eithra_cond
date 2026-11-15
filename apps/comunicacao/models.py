"""Modelos da capacidade transversal de comunicação.

O comunicado é o card que a gestão faz para o morador: o que vai ser dito, com
que tom, para quem, e o arquivo que sustenta isso. Ele não copia o estado da
origem — aponta para ela. Demanda e manutenção continuam donas dos próprios
dados; a comunicação guarda apenas os fatos que são dela: o texto do aviso, o
público, o arquivo gerado e quem o gerou.

A referência à origem é explícita — duas chaves estrangeiras opcionais, e não uma
referência genérica: assim o banco sabe exatamente o que a comunicação aponta, o
isolamento por condomínio é uma coluna, e nada depende de um ``content_type`` que
só existe em tempo de execução.
"""
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from apps.condominios.models import Condominio
from apps.core.storage import FORMATO_SHA256, erros_de_referencia_de_storage
from apps.demandas.models import Demanda
from apps.manutencoes.models import Manutencao

from .constants import (
    LIMITE_DO_LOCAL,
    LIMITE_DO_PUBLICO,
    LIMITE_DA_MENSAGEM,
    LIMITE_DO_TITULO,
    MIMES_DE_COMUNICADO,
    ModeloComunicado,
    OrigemComunicado,
    OrientacaoDoCard,
    PublicoComunicado,
    StatusComunicacao,
    TipoEventoComunicacao,
)


def _texto(valor: str | None) -> str:
    """Colapsa espaços e devolve string vazia quando não há conteúdo."""
    return " ".join((valor or "").split())


def _texto_longo(valor: str | None) -> str:
    """Preserva os parágrafos de um texto livre, sem espaços nas pontas."""
    return (valor or "").strip()


class ComunicacaoQuerySet(models.QuerySet):
    """Leituras reutilizáveis da comunicação.

    São perguntas que mais de uma tela faz: o que existe neste condomínio, o que
    já está pronto para compartilhar e o que partiu de uma origem. Ficam aqui, e
    não nas views, para serem respondidas do mesmo jeito em qualquer lugar.
    """

    def do_condominio(self, condominio):
        """Só o que é deste condomínio — a fronteira de isolamento."""
        if condominio is None or not getattr(condominio, "pk", None):
            return self.none()
        return self.filter(condominio_id=condominio.pk)

    def ativas(self):
        """As que ainda participam da operação."""
        return self.filter(ativo=True)

    def prontas(self):
        """As que já têm card gerado."""
        return self.filter(status=StatusComunicacao.PRONTA)


class Comunicacao(models.Model):
    """Um comunicado ao morador, com o card que o representa.

    A comunicação é curta de propósito. Não há fluxo de aprovação nem fila: ela
    nasce como rascunho, é revisada e, quando o card é gerado, fica pronta para
    ser compartilhada. O que a originou é uma referência — a demanda ou a
    manutenção continua sendo a dona daqueles dados.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    condominio = models.ForeignKey(
        Condominio,
        on_delete=models.PROTECT,
        related_name="comunicacoes",
        verbose_name="Condomínio",
    )
    modelo = models.CharField(
        "Modelo",
        max_length=20,
        choices=ModeloComunicado.choices,
        default=ModeloComunicado.AVISO,
        help_text="O tom e o desenho do card.",
    )
    orientacao = models.CharField(
        "Orientação",
        max_length=12,
        choices=OrientacaoDoCard.choices,
        default=OrientacaoDoCard.VERTICAL,
        help_text="Como o card se apresenta quando aberto no celular.",
    )
    titulo = models.CharField(
        "Título",
        max_length=LIMITE_DO_TITULO,
        help_text="A frase curta que o morador lê primeiro.",
    )
    mensagem = models.TextField(
        "Mensagem",
        blank=True,
        help_text="O texto do comunicado, com as palavras de quem escreve.",
    )
    local = models.CharField(
        "Local",
        max_length=LIMITE_DO_LOCAL,
        blank=True,
        default="",
        help_text="Onde o aviso vale: bloco, torre, área comum, equipamento.",
    )
    periodo = models.DateField(
        "Data ou período",
        null=True,
        blank=True,
        help_text="Quando o comunicado vale, quando houver uma data.",
    )
    publico = models.CharField(
        "Público",
        max_length=20,
        choices=PublicoComunicado.choices,
        default=PublicoComunicado.TODOS,
        help_text="A quem o comunicado se destina.",
    )
    publico_descricao = models.CharField(
        "Detalhe do público",
        max_length=LIMITE_DO_PUBLICO,
        blank=True,
        default="",
        help_text="Quais unidades ou qual bloco, quando o público não é todos.",
    )
    # A origem é referência, não cópia. Os dois vínculos são opcionais e
    # exclusivos: um comunicado aponta para uma demanda, para uma manutenção, ou
    # para nenhuma das duas — nunca para as duas ao mesmo tempo.
    origem_tipo = models.CharField(
        "Origem",
        max_length=20,
        choices=OrigemComunicado.choices,
        default=OrigemComunicado.AVULSO,
        help_text="De onde o comunicado partiu.",
    )
    origem_demanda = models.ForeignKey(
        Demanda,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="comunicacoes",
        verbose_name="Demanda de origem",
        help_text="A demanda que motivou o comunicado, quando houver.",
    )
    origem_manutencao = models.ForeignKey(
        Manutencao,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="comunicacoes",
        verbose_name="Manutenção de origem",
        help_text="A manutenção que motivou o comunicado, quando houver.",
    )
    # O card gerado vive em bucket privado: aqui fica só a referência do objeto.
    # Nunca uma URL pública ou assinada — o acesso é gerado sob demanda.
    storage_provider = models.CharField(
        "Provedor", max_length=50, blank=True, default=""
    )
    storage_bucket = models.CharField(
        "Local de armazenamento", max_length=150, blank=True, default=""
    )
    storage_key = models.CharField(
        "Referência do objeto", max_length=500, blank=True, default=""
    )
    mime_type = models.CharField("Tipo do arquivo", max_length=120, blank=True, default="")
    tamanho_bytes = models.PositiveBigIntegerField("Tamanho", default=0)
    sha256 = models.CharField(
        "Resumo do conteúdo",
        max_length=64,
        blank=True,
        default="",
        validators=[FORMATO_SHA256],
        help_text="Reconhece o card gerado, para comparar duas gerações.",
    )
    status = models.CharField(
        "Situação",
        max_length=20,
        choices=StatusComunicacao.choices,
        default=StatusComunicacao.RASCUNHO,
    )
    gerado_em = models.DateTimeField(
        "Card gerado em",
        null=True,
        blank=True,
        help_text="Quando o arquivo final passou a existir.",
    )
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="comunicacoes_criadas",
        verbose_name="Criada por",
        help_text="Quem registrou a comunicação. Preservado para rastreabilidade.",
    )
    criado_em = models.DateTimeField("Criada em", auto_now_add=True)
    atualizado_em = models.DateTimeField("Atualizada em", auto_now=True)
    ativo = models.BooleanField(
        "Ativa",
        default=True,
        help_text="Desativar retira a comunicação da operação sem apagar o histórico.",
    )

    objects = ComunicacaoQuerySet.as_manager()

    class Meta:
        verbose_name = "Comunicação"
        verbose_name_plural = "Comunicações"
        ordering = ["-criado_em"]
        constraints = [
            # A origem declarada e os vínculos precisam concordar: um comunicado
            # de demanda aponta para a demanda, um de manutenção para a
            # manutenção, e um avulso não aponta para nenhuma das duas.
            models.CheckConstraint(
                condition=(
                    models.Q(
                        origem_tipo=OrigemComunicado.DEMANDA,
                        origem_demanda__isnull=False,
                        origem_manutencao__isnull=True,
                    )
                    | models.Q(
                        origem_tipo=OrigemComunicado.MANUTENCAO,
                        origem_manutencao__isnull=False,
                        origem_demanda__isnull=True,
                    )
                    | (
                        models.Q(origem_demanda__isnull=True, origem_manutencao__isnull=True)
                        & ~models.Q(
                            origem_tipo__in=[
                                OrigemComunicado.DEMANDA,
                                OrigemComunicado.MANUTENCAO,
                            ]
                        )
                    )
                ),
                name="comunicacao_origem_coerente",
            ),
            # O card sai em PNG. Qualquer outro tipo é recusado no banco, e não
            # apenas na validação da borda. Sem arquivo, o tipo fica vazio.
            models.CheckConstraint(
                condition=models.Q(mime_type="")
                | models.Q(mime_type__in=MIMES_DE_COMUNICADO),
                name="comunicacao_mime_permitido",
            ),
            models.CheckConstraint(
                condition=models.Q(tamanho_bytes__gte=0),
                name="comunicacao_tamanho_nao_negativo",
            ),
            # "Pronta" exige arquivo: a situação não mente sobre o que existe.
            models.CheckConstraint(
                condition=~models.Q(status=StatusComunicacao.PRONTA)
                | ~models.Q(storage_key=""),
                name="comunicacao_pronta_tem_arquivo",
            ),
            # A situação diz o que aconteceu; a data diz quando. As duas precisam
            # concordar, senão a comunicação mente sobre o próprio passado.
            models.CheckConstraint(
                condition=(
                    models.Q(status=StatusComunicacao.PRONTA, gerado_em__isnull=False)
                    | (
                        ~models.Q(status=StatusComunicacao.PRONTA)
                        & models.Q(gerado_em__isnull=True)
                    )
                ),
                name="comunicacao_pronta_registra_quando",
            ),
        ]
        indexes = [
            models.Index(
                fields=["condominio", "status"], name="comunicacao_cond_status_idx"
            ),
            models.Index(
                fields=["condominio", "criado_em"], name="comunicacao_cond_criado_idx"
            ),
        ]

    def __str__(self) -> str:
        return self.titulo

    def clean(self):
        """Recusa uma comunicação incoerente antes que ela chegue ao banco."""
        erros: dict[str, str] = {}
        erros.update(erros_de_referencia_de_storage(self))

        if not (self.titulo or "").strip():
            erros["titulo"] = "Escreva o título do comunicado."

        if len((self.titulo or "").strip()) > LIMITE_DO_TITULO:
            erros["titulo"] = "O título é longo demais para o card."

        if len((self.mensagem or "").strip()) > LIMITE_DA_MENSAGEM:
            erros["mensagem"] = "A mensagem é longa demais para o card."

        # A origem e o vínculo não podem discordar — a mesma regra do banco, aqui
        # para a recusa chegar com uma frase de gestão.
        if self.origem_demanda_id and self.origem_manutencao_id:
            erros["origem_tipo"] = "Escolha uma origem só: demanda ou manutenção."
        if self.origem_tipo == OrigemComunicado.DEMANDA and not self.origem_demanda_id:
            erros["origem_demanda"] = "Um comunicado de demanda precisa da demanda."
        if self.origem_tipo == OrigemComunicado.MANUTENCAO and not self.origem_manutencao_id:
            erros["origem_manutencao"] = (
                "Um comunicado de manutenção precisa da manutenção."
            )
        if self.origem_tipo not in (OrigemComunicado.DEMANDA, OrigemComunicado.MANUTENCAO):
            if self.origem_demanda_id or self.origem_manutencao_id:
                erros["origem_tipo"] = "Esta origem não aponta para demanda nem manutenção."

        # Isolamento: a origem é do mesmo condomínio do comunicado. Sem isso, um
        # aviso de um prédio poderia apontar para a demanda de outro.
        if (
            self.origem_demanda_id
            and self.condominio_id
            and self.origem_demanda.condominio_id != self.condominio_id
        ):
            erros["origem_demanda"] = "A demanda escolhida não é deste condomínio."
        if (
            self.origem_manutencao_id
            and self.condominio_id
            and self.origem_manutencao.condominio_id != self.condominio_id
        ):
            erros["origem_manutencao"] = "A manutenção escolhida não é deste condomínio."

        if erros:
            raise ValidationError(erros)

    def save(self, *args, **kwargs):
        """Guarda o texto já normalizado, venha de onde vier."""
        self.titulo = _texto(self.titulo)
        self.mensagem = _texto_longo(self.mensagem)
        self.local = _texto(self.local)
        self.publico_descricao = _texto(self.publico_descricao)
        super().save(*args, **kwargs)

    @property
    def tem_imagem(self) -> bool:
        """Diz se o card já foi gerado e guardado."""
        return bool((self.storage_key or "").strip())

    @property
    def esta_pronta(self) -> bool:
        """A comunicação já pode ser compartilhada."""
        return self.status == StatusComunicacao.PRONTA and self.tem_imagem

    @property
    def publico_para_exibir(self) -> str:
        """O público como deve ser lido.

        O detalhe — quais unidades, qual bloco — diz mais que a categoria; sem
        ele, vale o rótulo do público escolhido.
        """
        if self.publico_descricao:
            return self.publico_descricao
        return self.get_publico_display()

    @property
    def periodo_por_extenso(self) -> str:
        """A data do comunicado como a gestão a lê, ou a ausência dela."""
        return self.periodo.strftime("%d/%m/%Y") if self.periodo else ""

    @property
    def descricao_da_origem(self) -> str:
        """De onde o comunicado veio, com o nome do registro de origem."""
        if self.origem_tipo == OrigemComunicado.DEMANDA and self.origem_demanda_id:
            return f"Demanda · {self.origem_demanda.titulo}"
        if self.origem_tipo == OrigemComunicado.MANUTENCAO and self.origem_manutencao_id:
            return f"Manutenção · {self.origem_manutencao.titulo}"
        return self.get_origem_tipo_display()


class HistoricoComunicacao(models.Model):
    """O que aconteceu com a comunicação, na ordem em que aconteceu.

    Guarda decisão, não ruído: quando o comunicado foi criado, quando o texto foi
    revisado e quando o card passou a existir. As linhas são acrescentadas e nunca
    reescritas — é a memória da comunicação. Quando útil, o evento guarda o antes e
    o depois em ``dados_anteriores`` e ``dados_novos``.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    comunicacao = models.ForeignKey(
        Comunicacao,
        on_delete=models.CASCADE,
        related_name="historico",
        verbose_name="Comunicação",
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="eventos_de_comunicacao",
        verbose_name="Quem registrou",
        help_text="Quem provocou o evento. Preservado para rastreabilidade.",
    )
    data_hora = models.DateTimeField("Quando", default=timezone.now)
    tipo_evento = models.CharField(
        "Tipo de evento",
        max_length=20,
        choices=TipoEventoComunicacao.choices,
    )
    descricao = models.CharField(
        "Descrição",
        max_length=255,
        help_text="O evento em linguagem humana, do jeito que deve ser lembrado.",
    )
    dados_anteriores = models.JSONField("Antes", null=True, blank=True)
    dados_novos = models.JSONField("Depois", null=True, blank=True)

    class Meta:
        verbose_name = "Evento da comunicação"
        verbose_name_plural = "Histórico da comunicação"
        ordering = ["-data_hora"]
        indexes = [
            models.Index(
                fields=["comunicacao", "data_hora"], name="hist_comunic_data_idx"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.get_tipo_evento_display()} · {self.comunicacao}"
