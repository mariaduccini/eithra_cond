"""Modelos do domínio de governança.

Governança é o que liga um condomínio às obrigações que ele assumiu: contratos
com fornecedores, garantias do que foi comprado ou instalado, e documentos que
precisam valer. Três perguntas governam tudo o que está aqui — "isto ainda
vale?", "o que isto cobre?" e "quando isto vence?".

Três decisões se repetem em todos os modelos:

- **Nada de situação guardada.** Vencido, vencendo, vigente e vazio são leituras
  do vencimento diante da data de hoje, calculadas no momento da pergunta. Um
  campo de situação precisaria ser reescrito todos os dias por alguém — e
  ficaria errado no primeiro dia em que ninguém reescrevesse.
- **O que é coberto fica em vínculo explícito.** Uma garantia cobre um
  equipamento, a manutenção que o instalou e o item de compra que o originou;
  um documento pode valer para um contrato e para um equipamento ao mesmo
  tempo. São tabelas próprias, com a integridade garantida pelo banco — nunca
  texto livre, nunca referência genérica.
- **Este app olha para os outros, e não o contrário.** Contratos, garantias e
  documentos leem cadastros, manutenções, estoque e rotinas; nenhum desses
  domínios conhece governança. É o que mantém o grafo de apps sem ciclo.

Nada aqui gera despesa, altera balanço ou cria lançamento financeiro: valor de
contrato é informação operacional, diz quanto se paga, não substitui a
contabilidade.
"""
import uuid
from datetime import date, timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q
from django.db.models.functions import Lower
from django.utils import timezone

from apps.cadastros.models import Equipamento, Fornecedor, LocalOperacional
from apps.condominios.models import Condominio
from apps.core.storage import FORMATO_SHA256, erros_de_referencia_de_storage
from apps.estoque.models import ItemCompra
from apps.manutencoes.models import Manutencao
from apps.rotinas.models import RotinaRecorrente

from .constants import TOM_POR_SITUACAO, PeriodicidadeCobranca, SituacaoVigencia


def _texto(valor: str | None) -> str:
    """Colapsa espaços repetidos e devolve string vazia quando não há conteúdo."""
    return " ".join((valor or "").split())


def _texto_longo(valor: str | None) -> str:
    """Preserva os parágrafos de um texto livre, sem espaços nas pontas."""
    return (valor or "").strip()


def _hoje(dia: date | None = None) -> date:
    """A data de referência: a informada, ou a de hoje."""
    return dia or timezone.localdate()


def _recusa_outro_condominio(
    erros: dict, campo: str, alvo, condominio_id, recado: str
) -> None:
    """Registra a recusa quando o alvo escolhido não é do mesmo condomínio.

    Toda relação entre dois registros do Eithra atravessa a fronteira do
    condomínio ou não existe: a lista de um prédio nunca classifica, cobre ou
    documenta o prédio do lado.
    """
    if alvo is not None and condominio_id and alvo.condominio_id != condominio_id:
        erros[campo] = recado


def _situacao_por_prazo(
    *,
    ativo: bool,
    data_inicio: date | None,
    data_fim: date | None,
    antecedencia_dias: int | None,
    referencia: date,
    sem_data_final: str = SituacaoVigencia.VIGENTE,
) -> str:
    """Diz em que ponto do prazo o registro está, na data da pergunta.

    Vale para contrato e para garantia, que têm a mesma forma de prazo: começam
    em uma data, terminam em outra (ou não terminam), e pedem atenção antes de
    acabar. O que muda entre os dois é apenas o nome das situações.
    """
    if not ativo:
        return SituacaoVigencia.INATIVO
    if data_inicio is not None and data_inicio > referencia:
        return SituacaoVigencia.FUTURO
    if data_fim is None:
        return sem_data_final
    if data_fim < referencia:
        return SituacaoVigencia.VENCIDO
    if data_fim <= referencia + timedelta(days=antecedencia_dias or 0):
        return SituacaoVigencia.VENCENDO
    return SituacaoVigencia.VIGENTE


def _data_menos_dias(campo_data: str, campo_dias: str):
    """A data do registro menos os dias do próprio registro.

    Serve para comparar prazos que não são iguais entre si: cada contrato avisa
    com a antecedência que definiu, cada garantia tem o seu prazo, e cada tipo
    de documento decide quando lembrar. A conta é feita no banco, para que a
    lista continue sendo um QuerySet — e não uma leitura linha a linha na
    memória, que ficaria mais lenta a cada registro novo.
    """
    return models.ExpressionWrapper(
        F(campo_data) - F(campo_dias),
        output_field=models.DateField(),
    )


# ---------------------------------------------------------------------------
# Tipos: as listas que a gestão mantém para classificar o que cadastra
# ---------------------------------------------------------------------------


class TipoContratoQuerySet(models.QuerySet):
    """Leituras reutilizáveis do tipo de contrato."""

    def ativos(self):
        """Tipos em uso."""
        return self.filter(ativo=True)

    def do_condominio(self, condominio):
        """Tipos deste condomínio. Sem condomínio resolvido, conjunto vazio."""
        if condominio is None or not getattr(condominio, "pk", None):
            return self.none()
        return self.filter(condominio_id=condominio.pk)


class TipoContrato(models.Model):
    """Como o condomínio classifica seus contratos: manutenção, seguro, locação.

    A lista é de cada condomínio e não vem pronta: "manutenção preventiva" quer
    dizer uma coisa em um prédio e outra em outro. O que se evita é o rótulo
    reescrito em cada contrato, com a grafia de quem digitou naquele dia.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    condominio = models.ForeignKey(
        Condominio,
        on_delete=models.PROTECT,
        related_name="tipos_de_contrato",
        verbose_name="Condomínio",
    )
    nome = models.CharField(
        "Nome",
        max_length=120,
        help_text="Como o condomínio chama este tipo de contrato.",
    )
    descricao = models.TextField(
        "Descrição",
        blank=True,
        default="",
        help_text="Detalhe opcional do que entra neste tipo.",
    )
    ativo = models.BooleanField(
        "Ativo",
        default=True,
        help_text="Desativar retira o tipo das opções novas sem apagar o cadastro.",
    )
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="tipos_de_contrato_cadastrados",
        verbose_name="Cadastrado por",
        help_text="Quem cadastrou. Preservado para rastreabilidade.",
    )
    criado_em = models.DateTimeField("Criado em", auto_now_add=True)
    atualizado_em = models.DateTimeField("Atualizado em", auto_now=True)

    objects = TipoContratoQuerySet.as_manager()

    class Meta:
        verbose_name = "Tipo de contrato"
        verbose_name_plural = "Tipos de contrato"
        ordering = ["nome"]
        constraints = [
            # "Seguro" e "seguro" são o mesmo tipo.
            models.UniqueConstraint(
                Lower("nome"),
                "condominio",
                name="tipo_contrato_unico_por_condominio",
            ),
        ]
        indexes = [
            models.Index(
                fields=["condominio", "ativo"], name="tipocontrato_cond_ativo_idx"
            ),
        ]

    def __str__(self) -> str:
        return self.nome

    def save(self, *args, **kwargs):
        """Guarda o cadastro já normalizado, venha ele do formulário ou não."""
        self.nome = _texto(self.nome)
        self.descricao = _texto_longo(self.descricao)
        super().save(*args, **kwargs)

    def clean(self):
        """Recusa um cadastro incoerente antes que ele chegue ao banco."""
        erros = {}
        if not _texto(self.nome):
            erros["nome"] = "Dê um nome ao tipo de contrato."
        if erros:
            raise ValidationError(erros)


class TipoDocumentoQuerySet(models.QuerySet):
    """Leituras reutilizáveis do tipo de documento."""

    def ativos(self):
        """Tipos em uso."""
        return self.filter(ativo=True)

    def do_condominio(self, condominio):
        """Tipos deste condomínio. Sem condomínio resolvido, conjunto vazio."""
        if condominio is None or not getattr(condominio, "pk", None):
            return self.none()
        return self.filter(condominio_id=condominio.pk)


class TipoDocumento(models.Model):
    """Como o condomínio classifica seus documentos: ata, laudo, certificado.

    O tipo carrega a expectativa do condomínio sobre o documento: se ele vence,
    e com quantos dias de antecedência a gestão quer ser lembrada. É por isso
    que a antecedência mora aqui, e não no documento — ela é decisão do tipo, e
    não de cada exemplar.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    condominio = models.ForeignKey(
        Condominio,
        on_delete=models.PROTECT,
        related_name="tipos_de_documento",
        verbose_name="Condomínio",
    )
    nome = models.CharField(
        "Nome",
        max_length=120,
        help_text="Como o condomínio chama este tipo de documento.",
    )
    exige_validade = models.BooleanField(
        "Exige validade",
        default=False,
        help_text="Quando marcado, um documento deste tipo precisa ter data de validade.",
    )
    antecedencia_alerta_padrao_dias = models.PositiveIntegerField(
        "Avisar com quantos dias de antecedência",
        default=30,
        help_text="Quantos dias antes do vencimento a gestão quer ser lembrada.",
    )
    descricao = models.TextField(
        "Descrição",
        blank=True,
        default="",
        help_text="Detalhe opcional do que entra neste tipo.",
    )
    ativo = models.BooleanField(
        "Ativo",
        default=True,
        help_text="Desativar retira o tipo das opções novas sem apagar o cadastro.",
    )
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="tipos_de_documento_cadastrados",
        verbose_name="Cadastrado por",
        help_text="Quem cadastrou. Preservado para rastreabilidade.",
    )
    criado_em = models.DateTimeField("Criado em", auto_now_add=True)
    atualizado_em = models.DateTimeField("Atualizado em", auto_now=True)

    objects = TipoDocumentoQuerySet.as_manager()

    class Meta:
        verbose_name = "Tipo de documento"
        verbose_name_plural = "Tipos de documento"
        ordering = ["nome"]
        constraints = [
            models.UniqueConstraint(
                Lower("nome"),
                "condominio",
                name="tipo_documento_unico_por_condominio",
            ),
            models.CheckConstraint(
                condition=Q(antecedencia_alerta_padrao_dias__gte=0),
                name="tipodoc_antecedencia_nao_negativa",
            ),
        ]
        indexes = [
            models.Index(
                fields=["condominio", "ativo"], name="tipodoc_cond_ativo_idx"
            ),
        ]

    def __str__(self) -> str:
        return self.nome

    def save(self, *args, **kwargs):
        """Guarda o cadastro já normalizado, venha ele do formulário ou não."""
        self.nome = _texto(self.nome)
        self.descricao = _texto_longo(self.descricao)
        super().save(*args, **kwargs)

    def clean(self):
        """Recusa um cadastro incoerente antes que ele chegue ao banco."""
        erros = {}
        if not _texto(self.nome):
            erros["nome"] = "Dê um nome ao tipo de documento."
        if erros:
            raise ValidationError(erros)


# ---------------------------------------------------------------------------
# Contrato
# ---------------------------------------------------------------------------


class ContratoFornecedorQuerySet(models.QuerySet):
    """Leituras reutilizáveis do contrato.

    São as perguntas que a lista faz: o que está em vigor, o que vence em
    breve, o que já venceu, qual reajuste se aproxima, de qual contrato a
    decisão de renovar já está na hora. Nada disso é guardado: cada pergunta é
    respondida na hora, a partir do prazo e de hoje.
    """

    def ativos(self):
        """Contratos que ainda contam."""
        return self.filter(ativo=True)

    def do_condominio(self, condominio):
        """Contratos deste condomínio. Sem condomínio resolvido, conjunto vazio."""
        if condominio is None or not getattr(condominio, "pk", None):
            return self.none()
        return self.filter(condominio_id=condominio.pk)

    def vigentes(self, hoje=None):
        """Ativos que já começaram e ainda não terminaram.

        Prazo indeterminado — sem data final — conta como vigente: um contrato
        sem prazo não vence, e tratá-lo como vencido seria pior.
        """
        referencia = _hoje(hoje)
        return (
            self.ativos()
            .filter(data_inicio__lte=referencia)
            .filter(Q(data_fim__isnull=True) | Q(data_fim__gte=referencia))
        )

    def vencendo(self, hoje=None):
        """Em vigor cujo fim já entrou na antecedência que o próprio contrato pediu.

        A conta usa o número do contrato, e não um prazo único do sistema: quem
        sabe com quantos dias quer ser lembrado é o contrato.
        """
        referencia = _hoje(hoje)
        return (
            self.vigentes(referencia)
            .annotate(
                abertura_do_aviso=_data_menos_dias(
                    "data_fim", "antecedencia_alerta_dias"
                )
            )
            .filter(abertura_do_aviso__lte=referencia)
        )

    def vencidos(self, hoje=None):
        """Ativos cujo prazo terminou."""
        return self.ativos().filter(data_fim__lt=_hoje(hoje))

    def reajustes_proximos(self, hoje=None, dias=30):
        """Ativos com data de reajuste marcada dentro dos próximos dias."""
        referencia = _hoje(hoje)
        return self.ativos().filter(
            data_proximo_reajuste__gte=referencia,
            data_proximo_reajuste__lte=referencia + timedelta(days=dias),
        )

    def prazos_rescisao_proximos(self, hoje=None):
        """Em vigor cuja data de decidir sobre a renovação já chegou.

        Sem prazo de aviso de rescisão informado, não há decisão a tomar — e o
        contrato não entra nesta lista.
        """
        referencia = _hoje(hoje)
        return (
            self.vigentes(referencia)
            .filter(prazo_aviso_rescisao_dias__isnull=False)
            .annotate(
                abertura_do_aviso=_data_menos_dias(
                    "data_fim", "prazo_aviso_rescisao_dias"
                )
            )
            .filter(abertura_do_aviso__lte=referencia)
        )

    def por_fornecedor(self, fornecedor):
        """Contratos deste fornecedor."""
        if fornecedor is None or not getattr(fornecedor, "pk", None):
            return self.none()
        return self.filter(fornecedor_id=fornecedor.pk)

    def buscar(self, termo):
        """Contratos cujo objeto, número, descrição ou fornecedor contém o termo."""
        termo = _texto(termo)
        if not termo:
            return self
        return self.filter(
            Q(objeto__icontains=termo)
            | Q(numero_referencia__icontains=termo)
            | Q(descricao__icontains=termo)
            | Q(indice_reajuste_texto__icontains=termo)
            | Q(fornecedor__nome_razao_social__icontains=termo)
            | Q(fornecedor__nome_fantasia__icontains=termo)
        )

    def na_ordem_de_atencao(self):
        """Do prazo mais próximo ao mais distante; sem prazo, por último.

        É a ordem em que a lista fica útil: o que termina antes aparece antes,
        e o contrato indeterminado não empurra os outros para baixo.
        """
        return self.order_by(F("data_fim").asc(nulls_last=True), "objeto")


class ContratoFornecedor(models.Model):
    """O que o condomínio contratou com um fornecedor, por quanto tempo e por quanto.

    O contrato guarda o objeto — o que foi contratado —, o prazo e o valor
    operacional. O que ele cobre (equipamentos, locais, rotinas preventivas)
    fica nos vínculos, porque o mesmo contrato de manutenção cobre vários
    desses ao mesmo tempo.

    A situação não é guardada: vencido, vencendo e vigente são lidos do prazo
    diante da data de hoje.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    condominio = models.ForeignKey(
        Condominio,
        on_delete=models.PROTECT,
        related_name="contratos",
        verbose_name="Condomínio",
    )
    fornecedor = models.ForeignKey(
        Fornecedor,
        on_delete=models.PROTECT,
        related_name="contratos",
        verbose_name="Fornecedor",
        help_text="Com quem o condomínio contratou.",
    )
    tipo_contrato = models.ForeignKey(
        TipoContrato,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="contratos",
        verbose_name="Tipo de contrato",
        help_text="Classificação mantida pelo condomínio. Pode ficar sem tipo definido.",
    )
    numero_referencia = models.CharField(
        "Número ou referência",
        max_length=60,
        blank=True,
        default="",
        help_text="Como o contrato é identificado no documento, quando houver número.",
    )
    objeto = models.CharField(
        "Objeto",
        max_length=200,
        help_text="O que o condomínio contratou, em uma linha.",
    )
    descricao = models.TextField(
        "Descrição",
        blank=True,
        default="",
        help_text="O que mais importa saber sobre este contrato.",
    )
    data_inicio = models.DateField("Início", help_text="Quando o contrato começou a valer.")
    data_fim = models.DateField(
        "Fim",
        null=True,
        blank=True,
        help_text="Quando o contrato termina. Em branco, o prazo é indeterminado.",
    )
    valor = models.DecimalField(
        "Valor",
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Valor operacional do contrato, quando o condomínio quiser registrar.",
    )
    periodicidade_cobranca = models.CharField(
        "Periodicidade da cobrança",
        max_length=20,
        choices=PeriodicidadeCobranca.choices,
        blank=True,
        default="",
        help_text="De quanto em quanto tempo a cobrança chega.",
    )
    renovacao_automatica = models.BooleanField(
        "Renovação automática",
        default=False,
        help_text="Quando marcado, o contrato se renova sem nova assinatura.",
    )
    antecedencia_alerta_dias = models.PositiveIntegerField(
        "Avisar com quantos dias de antecedência",
        default=60,
        help_text="Quantos dias antes do fim a gestão quer ser lembrada.",
    )
    prazo_aviso_rescisao_dias = models.PositiveIntegerField(
        "Prazo de aviso para rescindir",
        null=True,
        blank=True,
        help_text="Quantos dias antes do fim o condomínio precisa avisar que não renova.",
    )
    data_proximo_reajuste = models.DateField(
        "Próximo reajuste",
        null=True,
        blank=True,
        help_text="Quando está previsto o próximo reajuste, se houver.",
    )
    indice_reajuste_texto = models.CharField(
        "Índice de reajuste",
        max_length=120,
        blank=True,
        default="",
        help_text="Como o índice é chamado no contrato: IGP-M, IPCA, índice próprio.",
    )
    responsavel_interno = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="contratos_sob_responsabilidade",
        verbose_name="Responsável",
        help_text="Quem acompanha este contrato. O contrato permanece se a pessoa sair.",
    )
    observacao = models.TextField(
        "Observação",
        blank=True,
        default="",
        help_text="O que se sabe sobre o contrato e não cabe nos outros campos.",
    )
    ativo = models.BooleanField(
        "Ativo",
        default=True,
        help_text="Desativar encerra o acompanhamento sem apagar o histórico.",
    )
    # A autoria é do cadastro feito no Eithra, e por isso acompanha o usuário
    # que o fez. Um contrato que já existia antes do registro — incorporado do
    # histórico do condomínio — não teve autor aqui: fica vazio, e quem sustenta
    # o registro é a proveniência da carga, não este campo.
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="contratos_cadastrados",
        verbose_name="Cadastrado por",
        help_text=(
            "Quem cadastrou. Preservado para rastreabilidade. Fica sem "
            "preenchimento quando o contrato vem do histórico do condomínio."
        ),
    )
    criado_em = models.DateTimeField("Criado em", auto_now_add=True)
    atualizado_em = models.DateTimeField("Atualizado em", auto_now=True)

    objects = ContratoFornecedorQuerySet.as_manager()

    class Meta:
        verbose_name = "Contrato"
        verbose_name_plural = "Contratos"
        ordering = ["-data_inicio", "objeto"]
        constraints = [
            # Um contrato não termina antes de começar. A incoerência é erro, e
            # não um estado válido que alguém possa corrigir depois.
            models.CheckConstraint(
                condition=Q(data_fim__isnull=True) | Q(data_fim__gte=F("data_inicio")),
                name="contrato_fim_apos_inicio",
            ),
            models.CheckConstraint(
                condition=Q(antecedencia_alerta_dias__gte=0),
                name="contrato_antecedencia_nao_negativa",
            ),
            models.CheckConstraint(
                condition=Q(prazo_aviso_rescisao_dias__isnull=True)
                | Q(prazo_aviso_rescisao_dias__gte=0),
                name="contrato_prazo_rescisao_nao_negativo",
            ),
            models.CheckConstraint(
                condition=Q(valor__isnull=True) | Q(valor__gte=0),
                name="contrato_valor_nao_negativo",
            ),
            # O reajuste marcado não pode ser anterior ao próprio início do
            # contrato: seria um reajuste de um prazo que ainda não existia.
            models.CheckConstraint(
                condition=Q(data_proximo_reajuste__isnull=True)
                | Q(data_proximo_reajuste__gte=F("data_inicio")),
                name="contrato_reajuste_apos_inicio",
            ),
        ]
        indexes = [
            models.Index(fields=["condominio", "ativo"], name="contrato_cond_ativo_idx"),
            models.Index(fields=["condominio", "data_fim"], name="contrato_cond_fim_idx"),
            models.Index(fields=["fornecedor", "ativo"], name="contrato_forn_ativo_idx"),
            models.Index(
                fields=["data_proximo_reajuste"], name="contrato_reajuste_idx"
            ),
            models.Index(
                fields=["condominio", "tipo_contrato"], name="contrato_cond_tipo_idx"
            ),
        ]

    def __str__(self) -> str:
        return self.objeto

    def save(self, *args, **kwargs):
        """Guarda o contrato já normalizado, venha ele do formulário ou não."""
        self.objeto = _texto(self.objeto)
        self.numero_referencia = _texto(self.numero_referencia)
        self.indice_reajuste_texto = _texto(self.indice_reajuste_texto)
        self.descricao = _texto_longo(self.descricao)
        self.observacao = _texto_longo(self.observacao)
        super().save(*args, **kwargs)

    def clean(self):
        """Recusa um contrato incoerente antes que ele chegue ao banco."""
        erros = {}
        if not _texto(self.objeto):
            erros["objeto"] = "Diga o que foi contratado."

        # O fornecedor e o tipo são do mesmo condomínio do contrato.
        _recusa_outro_condominio(
            erros,
            "fornecedor",
            getattr(self, "fornecedor", None),
            self.condominio_id,
            "O fornecedor escolhido não é deste condomínio.",
        )
        _recusa_outro_condominio(
            erros,
            "tipo_contrato",
            getattr(self, "tipo_contrato", None),
            self.condominio_id,
            "O tipo escolhido não é deste condomínio.",
        )

        # A mesma regra do banco, dita em português: a tela recusa antes de
        # gravar, e a gestão lê o motivo em vez de ver um erro de sistema.
        if self.data_fim and self.data_inicio and self.data_fim < self.data_inicio:
            erros["data_fim"] = "O fim do contrato não pode ser anterior ao início."

        if erros:
            raise ValidationError(erros)

    # -- Situação e prazos, sempre lidos do prazo --------------------------
    def situacao_em(self, dia: date | None = None) -> str:
        """A situação do contrato na data informada."""
        return _situacao_por_prazo(
            ativo=self.ativo,
            data_inicio=self.data_inicio,
            data_fim=self.data_fim,
            antecedencia_dias=self.antecedencia_alerta_dias,
            referencia=_hoje(dia),
        )

    @property
    def situacao(self) -> str:
        """A situação do contrato hoje."""
        return self.situacao_em()

    @property
    def tom(self) -> str:
        """A cor com que a situação aparece na tela."""
        return TOM_POR_SITUACAO[self.situacao]

    def prazo_de_decisao_proximo_em(self, dia: date | None = None) -> bool:
        """Diz se a hora de decidir sobre a renovação já chegou.

        Depende do aviso de rescisão: sem prazo de aviso, não há decisão a
        tomar e o contrato não pede nada. Vale apenas para contrato em vigor —
        o que já venceu não é decisão, é fato.
        """
        if not self.ativo or self.data_fim is None or self.prazo_aviso_rescisao_dias is None:
            return False
        referencia = _hoje(dia)
        if self.situacao_em(referencia) not in (
            SituacaoVigencia.VIGENTE,
            SituacaoVigencia.VENCENDO,
        ):
            return False
        return self.data_fim - timedelta(days=self.prazo_aviso_rescisao_dias) <= referencia

    @property
    def prazo_de_decisao_proximo(self) -> bool:
        """A mesma pergunta, para a data de hoje."""
        return self.prazo_de_decisao_proximo_em()


class ContratoEquipamento(models.Model):
    """Um equipamento coberto pelo contrato.

    Vínculo explícito: o mesmo contrato cobre a bomba e o portão, e o mesmo
    equipamento pode estar em mais de um contrato ao longo do tempo.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    contrato = models.ForeignKey(
        ContratoFornecedor,
        on_delete=models.CASCADE,
        related_name="equipamentos_vinculados",
        verbose_name="Contrato",
    )
    equipamento = models.ForeignKey(
        Equipamento,
        on_delete=models.PROTECT,
        related_name="contratos",
        verbose_name="Equipamento",
    )
    observacao = models.CharField(
        "Observação",
        max_length=120,
        blank=True,
        default="",
        help_text="O que varia neste equipamento dentro deste contrato.",
    )
    criado_em = models.DateTimeField("Criado em", auto_now_add=True)

    class Meta:
        verbose_name = "Equipamento do contrato"
        verbose_name_plural = "Equipamentos do contrato"
        ordering = ["equipamento__nome"]
        constraints = [
            models.UniqueConstraint(
                fields=["contrato", "equipamento"],
                name="contrato_equipamento_unico",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.contrato} · {self.equipamento}"

    def save(self, *args, **kwargs):
        """Guarda o vínculo já normalizado, venha ele do formulário ou não."""
        self.observacao = _texto(self.observacao)
        super().save(*args, **kwargs)

    def clean(self):
        """Recusa um equipamento de outro condomínio."""
        erros = {}
        _recusa_outro_condominio(
            erros,
            "equipamento",
            getattr(self, "equipamento", None),
            getattr(self, "contrato", None) and self.contrato.condominio_id,
            "O equipamento escolhido não é deste condomínio.",
        )
        if erros:
            raise ValidationError(erros)


class ContratoLocal(models.Model):
    """Um local coberto pelo contrato: a garagem, a casa de máquinas, a torre."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    contrato = models.ForeignKey(
        ContratoFornecedor,
        on_delete=models.CASCADE,
        related_name="locais_vinculados",
        verbose_name="Contrato",
    )
    local = models.ForeignKey(
        LocalOperacional,
        on_delete=models.PROTECT,
        related_name="contratos",
        verbose_name="Local",
    )
    observacao = models.CharField(
        "Observação",
        max_length=120,
        blank=True,
        default="",
        help_text="O que varia neste local dentro deste contrato.",
    )
    criado_em = models.DateTimeField("Criado em", auto_now_add=True)

    class Meta:
        verbose_name = "Local do contrato"
        verbose_name_plural = "Locais do contrato"
        ordering = ["local__nome"]
        constraints = [
            models.UniqueConstraint(
                fields=["contrato", "local"],
                name="contrato_local_unico",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.contrato} · {self.local}"

    def save(self, *args, **kwargs):
        """Guarda o vínculo já normalizado, venha ele do formulário ou não."""
        self.observacao = _texto(self.observacao)
        super().save(*args, **kwargs)

    def clean(self):
        """Recusa um local de outro condomínio."""
        erros = {}
        _recusa_outro_condominio(
            erros,
            "local",
            getattr(self, "local", None),
            getattr(self, "contrato", None) and self.contrato.condominio_id,
            "O local escolhido não é deste condomínio.",
        )
        if erros:
            raise ValidationError(erros)


class ContratoRotina(models.Model):
    """Uma preventiva coberta pelo contrato.

    O vínculo vive aqui, do lado da governança: a rotina não conhece contrato,
    e continua existindo sem nenhum. O que se ganha é poder responder "esta
    preventiva está coberta por qual contrato?" sem que o domínio de rotinas
    precise saber que contratos existem.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    contrato = models.ForeignKey(
        ContratoFornecedor,
        on_delete=models.CASCADE,
        related_name="rotinas_vinculadas",
        verbose_name="Contrato",
    )
    rotina = models.ForeignKey(
        RotinaRecorrente,
        on_delete=models.PROTECT,
        related_name="contratos",
        verbose_name="Rotina",
    )
    observacao = models.CharField(
        "Observação",
        max_length=120,
        blank=True,
        default="",
        help_text="O que varia nesta rotina dentro deste contrato.",
    )
    criado_em = models.DateTimeField("Criado em", auto_now_add=True)

    class Meta:
        verbose_name = "Rotina do contrato"
        verbose_name_plural = "Rotinas do contrato"
        ordering = ["rotina__titulo"]
        constraints = [
            models.UniqueConstraint(
                fields=["contrato", "rotina"],
                name="contrato_rotina_unico",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.contrato} · {self.rotina}"

    def save(self, *args, **kwargs):
        """Guarda o vínculo já normalizado, venha ele do formulário ou não."""
        self.observacao = _texto(self.observacao)
        super().save(*args, **kwargs)

    def clean(self):
        """Recusa uma rotina de outro condomínio."""
        erros = {}
        _recusa_outro_condominio(
            erros,
            "rotina",
            getattr(self, "rotina", None),
            getattr(self, "contrato", None) and self.contrato.condominio_id,
            "A rotina escolhida não é deste condomínio.",
        )
        if erros:
            raise ValidationError(erros)


# ---------------------------------------------------------------------------
# Garantia
# ---------------------------------------------------------------------------


class GarantiaQuerySet(models.QuerySet):
    """Leituras reutilizáveis da garantia."""

    def ativos(self):
        """Garantias que ainda contam."""
        return self.filter(ativo=True)

    def do_condominio(self, condominio):
        """Garantias deste condomínio. Sem condomínio resolvido, conjunto vazio."""
        if condominio is None or not getattr(condominio, "pk", None):
            return self.none()
        return self.filter(condominio_id=condominio.pk)

    def vigentes(self, hoje=None):
        """Ativas que já começaram e ainda não terminaram."""
        referencia = _hoje(hoje)
        return (
            self.ativos()
            .filter(data_inicio__lte=referencia)
            .filter(Q(data_fim__isnull=True) | Q(data_fim__gte=referencia))
        )

    def vencendo(self, hoje=None):
        """Em vigor cujo fim entrou na antecedência pedida pela própria garantia."""
        referencia = _hoje(hoje)
        return (
            self.vigentes(referencia)
            .annotate(
                abertura_do_aviso=_data_menos_dias(
                    "data_fim", "antecedencia_alerta_dias"
                )
            )
            .filter(abertura_do_aviso__lte=referencia)
        )

    def vencidas(self, hoje=None):
        """Ativas cujo prazo terminou."""
        return self.ativos().filter(data_fim__lt=_hoje(hoje))

    def por_fornecedor(self, fornecedor):
        """Garantias ligadas a este fornecedor."""
        if fornecedor is None or not getattr(fornecedor, "pk", None):
            return self.none()
        return self.filter(fornecedor_id=fornecedor.pk)

    def buscar(self, termo):
        """Garantias cuja descrição, número ou fornecedor contém o termo."""
        termo = _texto(termo)
        if not termo:
            return self
        return self.filter(
            Q(descricao__icontains=termo)
            | Q(numero_referencia__icontains=termo)
            | Q(fornecedor__nome_razao_social__icontains=termo)
            | Q(fornecedor__nome_fantasia__icontains=termo)
        )

    def na_ordem_de_atencao(self):
        """Do prazo mais próximo ao mais distante; sem prazo, por último."""
        return self.order_by(F("data_fim").asc(nulls_last=True), "descricao")


class Garantia(models.Model):
    """O que ainda está garantido, por quanto tempo e por quem.

    A garantia descreve o prazo e o fornecedor. O que ela cobre fica nos
    vínculos — um equipamento, a manutenção que o instalou, o item de compra
    que o trouxe. Uma garantia pode cobrir vários objetos, e mais de um tipo de
    objeto ao mesmo tempo: é assim que o mesmo serviço responde pela bomba nova
    e pela instalação dela.

    Sem vínculo nenhum, a garantia ainda não diz nada — e é o domínio que
    recusa encerrar a operação nesse estado, e não o banco, que não alcança
    outra tabela.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    condominio = models.ForeignKey(
        Condominio,
        on_delete=models.PROTECT,
        related_name="garantias",
        verbose_name="Condomínio",
    )
    fornecedor = models.ForeignKey(
        Fornecedor,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="garantias",
        verbose_name="Fornecedor",
        help_text="Quem responde pela garantia, quando for um fornecedor cadastrado.",
    )
    descricao = models.CharField(
        "Descrição",
        max_length=200,
        help_text="O que esta garantia assegura, em uma linha.",
    )
    numero_referencia = models.CharField(
        "Número ou referência",
        max_length=80,
        blank=True,
        default="",
        help_text="Como a garantia é identificada no documento, quando houver número.",
    )
    data_inicio = models.DateField("Início", help_text="A partir de quando a garantia vale.")
    data_fim = models.DateField(
        "Fim",
        null=True,
        blank=True,
        help_text="Até quando a garantia vale. Em branco, o prazo é indeterminado.",
    )
    antecedencia_alerta_dias = models.PositiveIntegerField(
        "Avisar com quantos dias de antecedência",
        default=30,
        help_text="Quantos dias antes do fim a gestão quer ser lembrada.",
    )
    observacao = models.TextField(
        "Observação",
        blank=True,
        default="",
        help_text="O que se sabe sobre a garantia e não cabe nos outros campos.",
    )
    ativo = models.BooleanField(
        "Ativa",
        default=True,
        help_text="Desativar retira a garantia do acompanhamento sem apagar o histórico.",
    )
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="garantias_cadastradas",
        verbose_name="Cadastrado por",
        help_text="Quem cadastrou. Preservado para rastreabilidade.",
    )
    criado_em = models.DateTimeField("Criado em", auto_now_add=True)
    atualizado_em = models.DateTimeField("Atualizado em", auto_now=True)

    objects = GarantiaQuerySet.as_manager()

    class Meta:
        verbose_name = "Garantia"
        verbose_name_plural = "Garantias"
        ordering = ["-data_inicio", "descricao"]
        constraints = [
            models.CheckConstraint(
                condition=Q(data_fim__isnull=True) | Q(data_fim__gte=F("data_inicio")),
                name="garantia_fim_apos_inicio",
            ),
            models.CheckConstraint(
                condition=Q(antecedencia_alerta_dias__gte=0),
                name="garantia_antecedencia_nao_negativa",
            ),
        ]
        indexes = [
            models.Index(fields=["condominio", "data_fim"], name="garantia_cond_fim_idx"),
            models.Index(fields=["fornecedor", "ativo"], name="garantia_forn_ativo_idx"),
            models.Index(fields=["condominio", "ativo"], name="garantia_cond_ativo_idx"),
        ]

    def __str__(self) -> str:
        return self.descricao

    def save(self, *args, **kwargs):
        """Guarda a garantia já normalizada, venha ela do formulário ou não."""
        self.descricao = _texto(self.descricao)
        self.numero_referencia = _texto(self.numero_referencia)
        self.observacao = _texto_longo(self.observacao)
        super().save(*args, **kwargs)

    def clean(self):
        """Recusa uma garantia incoerente antes que ela chegue ao banco."""
        erros = {}
        if not _texto(self.descricao):
            erros["descricao"] = "Diga o que esta garantia assegura."

        _recusa_outro_condominio(
            erros,
            "fornecedor",
            getattr(self, "fornecedor", None),
            self.condominio_id,
            "O fornecedor escolhido não é deste condomínio.",
        )

        if self.data_fim and self.data_inicio and self.data_fim < self.data_inicio:
            erros["data_fim"] = "O fim da garantia não pode ser anterior ao início."

        if erros:
            raise ValidationError(erros)

    def situacao_em(self, dia: date | None = None) -> str:
        """A situação da garantia na data informada."""
        return _situacao_por_prazo(
            ativo=self.ativo,
            data_inicio=self.data_inicio,
            data_fim=self.data_fim,
            antecedencia_dias=self.antecedencia_alerta_dias,
            referencia=_hoje(dia),
        )

    @property
    def situacao(self) -> str:
        """A situação da garantia hoje."""
        return self.situacao_em()

    @property
    def tom(self) -> str:
        """A cor com que a situação aparece na tela."""
        return TOM_POR_SITUACAO[self.situacao]


class GarantiaEquipamento(models.Model):
    """Um equipamento coberto pela garantia."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    garantia = models.ForeignKey(
        Garantia,
        on_delete=models.CASCADE,
        related_name="equipamentos",
        verbose_name="Garantia",
    )
    equipamento = models.ForeignKey(
        Equipamento,
        on_delete=models.PROTECT,
        related_name="garantias",
        verbose_name="Equipamento",
    )
    observacao = models.CharField(
        "Observação",
        max_length=120,
        blank=True,
        default="",
        help_text="O que varia neste equipamento dentro desta garantia.",
    )
    criado_em = models.DateTimeField("Criado em", auto_now_add=True)

    class Meta:
        verbose_name = "Equipamento da garantia"
        verbose_name_plural = "Equipamentos da garantia"
        ordering = ["equipamento__nome"]
        constraints = [
            models.UniqueConstraint(
                fields=["garantia", "equipamento"],
                name="garantia_equipamento_unico",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.garantia} · {self.equipamento}"

    def save(self, *args, **kwargs):
        """Guarda o vínculo já normalizado, venha ele do formulário ou não."""
        self.observacao = _texto(self.observacao)
        super().save(*args, **kwargs)

    def clean(self):
        """Recusa um equipamento de outro condomínio."""
        erros = {}
        _recusa_outro_condominio(
            erros,
            "equipamento",
            getattr(self, "equipamento", None),
            getattr(self, "garantia", None) and self.garantia.condominio_id,
            "O equipamento escolhido não é deste condomínio.",
        )
        if erros:
            raise ValidationError(erros)


class GarantiaManutencao(models.Model):
    """Uma manutenção coberta pela garantia — o serviço que instalou a peça.

    É o vínculo que responde pela garantia de serviço: a mesma garantia cobre o
    equipamento trocado e o trabalho de quem o trocou.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    garantia = models.ForeignKey(
        Garantia,
        on_delete=models.CASCADE,
        related_name="manutencoes",
        verbose_name="Garantia",
    )
    manutencao = models.ForeignKey(
        Manutencao,
        on_delete=models.PROTECT,
        related_name="garantias",
        verbose_name="Manutenção",
    )
    observacao = models.CharField(
        "Observação",
        max_length=120,
        blank=True,
        default="",
        help_text="O que varia nesta manutenção dentro desta garantia.",
    )
    criado_em = models.DateTimeField("Criado em", auto_now_add=True)

    class Meta:
        verbose_name = "Manutenção da garantia"
        verbose_name_plural = "Manutenções da garantia"
        ordering = ["-criado_em"]
        constraints = [
            models.UniqueConstraint(
                fields=["garantia", "manutencao"],
                name="garantia_manutencao_unica",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.garantia} · {self.manutencao}"

    def save(self, *args, **kwargs):
        """Guarda o vínculo já normalizado, venha ele do formulário ou não."""
        self.observacao = _texto(self.observacao)
        super().save(*args, **kwargs)

    def clean(self):
        """Recusa uma manutenção de outro condomínio."""
        erros = {}
        _recusa_outro_condominio(
            erros,
            "manutencao",
            getattr(self, "manutencao", None),
            getattr(self, "garantia", None) and self.garantia.condominio_id,
            "A manutenção escolhida não é deste condomínio.",
        )
        if erros:
            raise ValidationError(erros)


class GarantiaItemCompra(models.Model):
    """Um item de compra coberto pela garantia — o que foi comprado.

    O item de compra não tem condomínio próprio: ele pertence à compra, e é a
    compra que diz de qual condomínio ele é. Por isso a conferência de fronteira
    aqui passa pela compra.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    garantia = models.ForeignKey(
        Garantia,
        on_delete=models.CASCADE,
        related_name="itens_de_compra",
        verbose_name="Garantia",
    )
    item_compra = models.ForeignKey(
        ItemCompra,
        on_delete=models.PROTECT,
        related_name="garantias",
        verbose_name="Item de compra",
    )
    observacao = models.CharField(
        "Observação",
        max_length=120,
        blank=True,
        default="",
        help_text="O que varia neste item dentro desta garantia.",
    )
    criado_em = models.DateTimeField("Criado em", auto_now_add=True)

    class Meta:
        verbose_name = "Item de compra da garantia"
        verbose_name_plural = "Itens de compra da garantia"
        ordering = ["-criado_em"]
        constraints = [
            models.UniqueConstraint(
                fields=["garantia", "item_compra"],
                name="garantia_item_compra_unico",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.garantia} · {self.item_compra}"

    def save(self, *args, **kwargs):
        """Guarda o vínculo já normalizado, venha ele do formulário ou não."""
        self.observacao = _texto(self.observacao)
        super().save(*args, **kwargs)

    def clean(self):
        """Recusa um item de compra de outro condomínio."""
        erros = {}
        if self.item_compra_id and self.garantia_id:
            item = self.item_compra
            if item is not None and item.compra.condominio_id != self.garantia.condominio_id:
                erros["item_compra"] = "O item escolhido não é deste condomínio."
        if erros:
            raise ValidationError(erros)


# ---------------------------------------------------------------------------
# Documento
# ---------------------------------------------------------------------------


class DocumentoOperacionalQuerySet(models.QuerySet):
    """Leituras reutilizáveis do documento.

    Documento sem data de validade não vence: ata e regimento valem enquanto
    ninguém os substituir. É por isso que "sem validade" é uma resposta, e não
    a falta de uma.
    """

    def ativos(self):
        """Documentos que ainda contam."""
        return self.filter(ativo=True)

    def do_condominio(self, condominio):
        """Documentos deste condomínio. Sem condomínio resolvido, conjunto vazio."""
        if condominio is None or not getattr(condominio, "pk", None):
            return self.none()
        return self.filter(condominio_id=condominio.pk)

    def com_validade(self):
        """Ativos que têm data de validade."""
        return self.ativos().filter(data_validade__isnull=False)

    def sem_validade(self):
        """Ativos sem data de validade."""
        return self.ativos().filter(data_validade__isnull=True)

    def vencidos(self, hoje=None):
        """Ativos cuja validade passou."""
        return self.com_validade().filter(data_validade__lt=_hoje(hoje))

    def vencendo(self, hoje=None):
        """Válidos cuja validade entrou na antecedência do próprio tipo.

        A antecedência é do tipo do documento, e não do exemplar: é a mesma
        pergunta para todos os laudos do condomínio.
        """
        referencia = _hoje(hoje)
        return (
            self.com_validade()
            .filter(data_validade__gte=referencia)
            .annotate(
                abertura_do_aviso=_data_menos_dias(
                    "data_validade", "tipo_documento__antecedencia_alerta_padrao_dias"
                )
            )
            .filter(abertura_do_aviso__lte=referencia)
        )

    def do_tipo(self, tipo_documento):
        """Documentos deste tipo."""
        if tipo_documento is None or not getattr(tipo_documento, "pk", None):
            return self.none()
        return self.filter(tipo_documento_id=tipo_documento.pk)

    def buscar(self, termo):
        """Documentos cujo título, número ou emissor contém o termo."""
        termo = _texto(termo)
        if not termo:
            return self
        return self.filter(
            Q(titulo__icontains=termo)
            | Q(numero_referencia__icontains=termo)
            | Q(emissor__icontains=termo)
        )

    def na_ordem_de_atencao(self):
        """Da validade mais próxima à mais distante; sem validade, por último."""
        return self.order_by(F("data_validade").asc(nulls_last=True), "titulo")


class DocumentoOperacional(models.Model):
    """Um documento do condomínio, com validade quando ela existe.

    Ata, laudo, certificado, apólice, regimento: o documento pertence ao
    condomínio e pode não pertencer a mais nada. O que ele documenta fica nos
    vínculos — e o mesmo arquivo pode valer para um contrato e para um
    equipamento ao mesmo tempo, sem ser guardado duas vezes.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    condominio = models.ForeignKey(
        Condominio,
        on_delete=models.PROTECT,
        related_name="documentos",
        verbose_name="Condomínio",
    )
    tipo_documento = models.ForeignKey(
        TipoDocumento,
        on_delete=models.PROTECT,
        related_name="documentos",
        verbose_name="Tipo de documento",
    )
    titulo = models.CharField(
        "Título",
        max_length=180,
        help_text="Como este documento é conhecido no condomínio.",
    )
    numero_referencia = models.CharField(
        "Número ou referência",
        max_length=80,
        blank=True,
        default="",
        help_text="Como o documento é identificado nele mesmo, quando houver número.",
    )
    data_emissao = models.DateField(
        "Emissão",
        null=True,
        blank=True,
        help_text="Quando o documento foi emitido, se souber.",
    )
    data_validade = models.DateField(
        "Validade",
        null=True,
        blank=True,
        help_text="Até quando o documento vale. Em branco, ele não vence.",
    )
    emissor = models.CharField(
        "Emissor",
        max_length=180,
        blank=True,
        default="",
        help_text="Quem emitiu o documento, quando não for um fornecedor cadastrado.",
    )
    observacao = models.TextField(
        "Observação",
        blank=True,
        default="",
        help_text="O que se sabe sobre o documento e não cabe nos outros campos.",
    )
    ativo = models.BooleanField(
        "Ativo",
        default=True,
        help_text="Desativar retira o documento do acompanhamento sem apagar o histórico.",
    )
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="documentos_cadastrados",
        verbose_name="Cadastrado por",
        help_text="Quem cadastrou. Preservado para rastreabilidade.",
    )
    criado_em = models.DateTimeField("Criado em", auto_now_add=True)
    atualizado_em = models.DateTimeField("Atualizado em", auto_now=True)

    objects = DocumentoOperacionalQuerySet.as_manager()

    class Meta:
        verbose_name = "Documento"
        verbose_name_plural = "Documentos"
        ordering = ["-criado_em", "titulo"]
        constraints = [
            # Um número identifica um documento dentro do condomínio. Sem
            # número informado, nada é comparado: duas atas guardadas sem
            # número não são o mesmo documento por isso.
            models.UniqueConstraint(
                fields=["condominio", "tipo_documento", "numero_referencia"],
                condition=~Q(numero_referencia=""),
                name="documento_numero_unico_por_tipo",
            ),
        ]
        indexes = [
            models.Index(
                fields=["condominio", "data_validade"], name="doc_cond_validade_idx"
            ),
            models.Index(fields=["tipo_documento", "ativo"], name="doc_tipo_ativo_idx"),
            models.Index(fields=["condominio", "ativo"], name="doc_cond_ativo_idx"),
        ]

    def __str__(self) -> str:
        return self.titulo

    def save(self, *args, **kwargs):
        """Guarda o documento já normalizado, venha ele do formulário ou não."""
        self.titulo = _texto(self.titulo)
        self.numero_referencia = _texto(self.numero_referencia)
        self.emissor = _texto(self.emissor)
        self.observacao = _texto_longo(self.observacao)
        super().save(*args, **kwargs)

    def clean(self):
        """Recusa um documento incoerente antes que ele chegue ao banco.

        Duas regras moram aqui porque dependem de outra tabela, e o banco não
        as alcança: o tipo é do mesmo condomínio do documento, e um tipo que
        exige validade não aceita documento sem data de validade.
        """
        erros = {}
        if not _texto(self.titulo):
            erros["titulo"] = "Dê um título ao documento."

        _recusa_outro_condominio(
            erros,
            "tipo_documento",
            getattr(self, "tipo_documento", None),
            self.condominio_id,
            "O tipo escolhido não é deste condomínio.",
        )

        tipo = getattr(self, "tipo_documento", None)
        if tipo is not None and tipo.exige_validade and self.data_validade is None:
            erros["data_validade"] = (
                f"Um documento do tipo «{tipo.nome}» precisa ter data de validade."
            )

        if erros:
            raise ValidationError(erros)

    def situacao_em(self, dia: date | None = None) -> str:
        """A situação do documento na data informada.

        Sem data de validade, a resposta é "sem validade" — e não "vencido" nem
        "vigente": o documento simplesmente não foi feito para vencer.
        """
        if not self.ativo:
            return SituacaoVigencia.INATIVO
        if self.data_validade is None:
            return SituacaoVigencia.SEM_VALIDADE

        referencia = _hoje(dia)
        if self.data_validade < referencia:
            return SituacaoVigencia.VENCIDO

        antecedencia = 0
        if self.tipo_documento_id:
            antecedencia = self.tipo_documento.antecedencia_alerta_padrao_dias or 0
        if self.data_validade <= referencia + timedelta(days=antecedencia):
            return SituacaoVigencia.VENCENDO
        return SituacaoVigencia.VIGENTE

    @property
    def situacao(self) -> str:
        """A situação do documento hoje."""
        return self.situacao_em()

    @property
    def tom(self) -> str:
        """A cor com que a situação aparece na tela."""
        return TOM_POR_SITUACAO[self.situacao]

    @property
    def relacionado_a(self) -> str:
        """A que este documento está ligado, na ordem em que a gestão procura.

        Um certificado pode valer para o contrato e para o equipamento; a tela
        mostra o primeiro contexto — contrato, depois equipamento, garantia e
        fornecedor —, e "Condomínio" quando o documento não pertence a mais
        nada, que é o caso de uma ata ou de um regimento.
        """
        vinculo_contrato = self.contratos_vinculados.first()
        if vinculo_contrato is not None:
            return str(vinculo_contrato.contrato)

        vinculo_equipamento = self.equipamentos_vinculados.first()
        if vinculo_equipamento is not None:
            return str(vinculo_equipamento.equipamento)

        vinculo_garantia = self.garantias_vinculadas.first()
        if vinculo_garantia is not None:
            return str(vinculo_garantia.garantia)

        vinculo_fornecedor = self.fornecedores_vinculados.first()
        if vinculo_fornecedor is not None:
            return str(vinculo_fornecedor.fornecedor)

        return "Condomínio"


class ArquivoDocumento(models.Model):
    """O arquivo de um documento, guardado em storage privado.

    Guarda-se a referência do objeto — provedor, bucket e chave —, e nunca o
    arquivo em disco local nem uma URL pública. O acesso é gerado sob demanda,
    para quem tem autorização confirmada, com validade curta.

    Um documento pode ter mais de um arquivo: o original, o aditivo, o
    comprovante de entrega. Todos pertencem ao mesmo documento.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    documento = models.ForeignKey(
        DocumentoOperacional,
        on_delete=models.CASCADE,
        related_name="arquivos",
        verbose_name="Documento",
    )
    nome_original = models.CharField(
        "Nome do arquivo",
        max_length=255,
        help_text="Como o arquivo chegou, para a gestão reconhecê-lo.",
    )
    storage_provider = models.CharField(
        "Provedor", max_length=50, blank=True, default=""
    )
    storage_bucket = models.CharField(
        "Local de armazenamento", max_length=150, blank=True, default=""
    )
    storage_key = models.CharField("Referência do objeto", max_length=500)
    mime_type = models.CharField("Tipo do arquivo", max_length=120)
    tamanho_bytes = models.PositiveBigIntegerField("Tamanho", default=0)
    sha256 = models.CharField(
        "Hash do arquivo",
        max_length=64,
        validators=[FORMATO_SHA256],
        help_text="Reconhece o mesmo arquivo repetido no mesmo documento.",
    )
    enviado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="documentos_enviados",
        verbose_name="Enviado por",
        help_text="Quem enviou. Preservado para rastreabilidade.",
    )
    enviado_em = models.DateTimeField("Enviado em", default=timezone.now)
    ativo = models.BooleanField(
        "Ativo",
        default=True,
        help_text="Desativar retira o arquivo de circulação sem apagar o registro.",
    )

    class Meta:
        verbose_name = "Arquivo do documento"
        verbose_name_plural = "Arquivos do documento"
        ordering = ["-enviado_em"]
        constraints = [
            # O mesmo arquivo não entra duas vezes no mesmo documento. Não se
            # impõe unicidade global do hash: o mesmo PDF pode existir
            # legitimamente em condomínios distintos.
            models.UniqueConstraint(
                fields=["documento", "sha256"],
                name="arquivo_unico_por_documento_e_hash",
            ),
            models.CheckConstraint(
                condition=Q(tamanho_bytes__gte=0),
                name="arquivo_tamanho_nao_negativo",
            ),
        ]
        indexes = [
            models.Index(fields=["sha256"], name="arqdocumento_sha_idx"),
        ]

    def __str__(self) -> str:
        return self.nome_original

    def clean(self):
        """Recusa uma referência que seja, na verdade, um endereço público."""
        super().clean()
        erros = erros_de_referencia_de_storage(self)
        if erros:
            raise ValidationError(erros)


class DocumentoFornecedor(models.Model):
    """Um documento que pertence a um fornecedor: contrato social, certidão."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    documento = models.ForeignKey(
        DocumentoOperacional,
        on_delete=models.CASCADE,
        related_name="fornecedores_vinculados",
        verbose_name="Documento",
    )
    fornecedor = models.ForeignKey(
        Fornecedor,
        on_delete=models.PROTECT,
        related_name="documentos",
        verbose_name="Fornecedor",
    )
    criado_em = models.DateTimeField("Criado em", auto_now_add=True)

    class Meta:
        verbose_name = "Fornecedor do documento"
        verbose_name_plural = "Fornecedores do documento"
        ordering = ["fornecedor__nome_razao_social"]
        constraints = [
            models.UniqueConstraint(
                fields=["documento", "fornecedor"],
                name="documento_fornecedor_unico",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.documento} · {self.fornecedor}"

    def clean(self):
        """Recusa um fornecedor de outro condomínio."""
        erros = {}
        _recusa_outro_condominio(
            erros,
            "fornecedor",
            getattr(self, "fornecedor", None),
            getattr(self, "documento", None) and self.documento.condominio_id,
            "O fornecedor escolhido não é deste condomínio.",
        )
        if erros:
            raise ValidationError(erros)


class DocumentoContrato(models.Model):
    """Um documento que pertence a um contrato: a minuta assinada, o aditivo."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    documento = models.ForeignKey(
        DocumentoOperacional,
        on_delete=models.CASCADE,
        related_name="contratos_vinculados",
        verbose_name="Documento",
    )
    contrato = models.ForeignKey(
        ContratoFornecedor,
        on_delete=models.PROTECT,
        related_name="documentos",
        verbose_name="Contrato",
    )
    criado_em = models.DateTimeField("Criado em", auto_now_add=True)

    class Meta:
        verbose_name = "Contrato do documento"
        verbose_name_plural = "Contratos do documento"
        ordering = ["-criado_em"]
        constraints = [
            models.UniqueConstraint(
                fields=["documento", "contrato"],
                name="documento_contrato_unico",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.documento} · {self.contrato}"

    def clean(self):
        """Recusa um contrato de outro condomínio."""
        erros = {}
        _recusa_outro_condominio(
            erros,
            "contrato",
            getattr(self, "contrato", None),
            getattr(self, "documento", None) and self.documento.condominio_id,
            "O contrato escolhido não é deste condomínio.",
        )
        if erros:
            raise ValidationError(erros)


class DocumentoEquipamento(models.Model):
    """Um documento que pertence a um equipamento: o manual, o laudo, o certificado."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    documento = models.ForeignKey(
        DocumentoOperacional,
        on_delete=models.CASCADE,
        related_name="equipamentos_vinculados",
        verbose_name="Documento",
    )
    equipamento = models.ForeignKey(
        Equipamento,
        on_delete=models.PROTECT,
        related_name="documentos",
        verbose_name="Equipamento",
    )
    criado_em = models.DateTimeField("Criado em", auto_now_add=True)

    class Meta:
        verbose_name = "Equipamento do documento"
        verbose_name_plural = "Equipamentos do documento"
        ordering = ["equipamento__nome"]
        constraints = [
            models.UniqueConstraint(
                fields=["documento", "equipamento"],
                name="documento_equipamento_unico",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.documento} · {self.equipamento}"

    def clean(self):
        """Recusa um equipamento de outro condomínio."""
        erros = {}
        _recusa_outro_condominio(
            erros,
            "equipamento",
            getattr(self, "equipamento", None),
            getattr(self, "documento", None) and self.documento.condominio_id,
            "O equipamento escolhido não é deste condomínio.",
        )
        if erros:
            raise ValidationError(erros)


class DocumentoGarantia(models.Model):
    """Um documento que pertence a uma garantia: o termo, o certificado dela."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    documento = models.ForeignKey(
        DocumentoOperacional,
        on_delete=models.CASCADE,
        related_name="garantias_vinculadas",
        verbose_name="Documento",
    )
    garantia = models.ForeignKey(
        Garantia,
        on_delete=models.PROTECT,
        related_name="documentos",
        verbose_name="Garantia",
    )
    criado_em = models.DateTimeField("Criado em", auto_now_add=True)

    class Meta:
        verbose_name = "Garantia do documento"
        verbose_name_plural = "Garantias do documento"
        ordering = ["-criado_em"]
        constraints = [
            models.UniqueConstraint(
                fields=["documento", "garantia"],
                name="documento_garantia_unico",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.documento} · {self.garantia}"

    def clean(self):
        """Recusa uma garantia de outro condomínio."""
        erros = {}
        _recusa_outro_condominio(
            erros,
            "garantia",
            getattr(self, "garantia", None),
            getattr(self, "documento", None) and self.documento.condominio_id,
            "A garantia escolhida não é deste condomínio.",
        )
        if erros:
            raise ValidationError(erros)
