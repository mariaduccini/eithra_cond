"""Modelos do domínio de comunidade: unidades, pessoas, veículos e vínculos.

Três cadastros e três vínculos. A unidade é onde se mora; a pessoa é quem mora,
responde ou trabalha; o veículo é o que circula pelo condomínio. E o que liga
tudo isso são vínculos explícitos, com data de início e, quando chega ao fim,
data de encerramento — porque a resposta a "quem mora aqui?" muda com o tempo.

Três decisões atravessam este arquivo:

- **Pessoa não é usuário.** Quem mora no condomínio não precisa de conta no
  Eithra, e ter conta não é morar lá. A associação entre os dois é opcional e
  não concede nada por si só.
- **O estado atual não se guarda.** "Ocupada", "tem morador", "quantidade de
  moradores" são perguntas feitas aos vínculos vigentes na hora, e não colunas
  que envelhecem sozinhas.
- **Nada de um condomínio aparece em outro.** Toda entidade e todo vínculo
  carregam o seu condomínio, e a coerência entre eles é conferida antes de
  gravar.
"""
import re
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower
from django.utils import timezone

from apps.cadastros.constants import TipoPessoa
from apps.condominios.models import (
    AcessoUsuarioCondominio,
    Condominio,
    somente_digitos,
)

from .constants import OrigemInformacao, TipoVinculoPessoaUnidade


def _texto(valor: str | None) -> str:
    """Colapsa espaços repetidos e devolve string vazia quando não há conteúdo."""
    return " ".join((valor or "").split())


def _texto_longo(valor: str | None) -> str:
    """Preserva os parágrafos de um texto livre, sem espaços nas pontas."""
    return (valor or "").strip()


def _hoje(dia=None):
    """A data de referência das perguntas de vigência."""
    return dia or timezone.localdate()


def _placa(valor: str | None) -> str:
    """A placa sem separadores e em maiúsculas, que é como ela fica guardada.

    A placa brasileira tem duas formas — a antiga (ABC1234) e a do Mercosul
    (ABC1D23) —, e a única diferença entre elas está no quinto caractere. Aqui
    as duas convivem: o Eithra não consulta nenhum cadastro externo para dizer
    se uma placa existe, e quem informa responde por ela.
    """
    return re.sub(r"[^0-9A-Za-z]", "", valor or "").upper()


# ---------------------------------------------------------------------------
# Unidade
# ---------------------------------------------------------------------------


class UnidadeQuerySet(models.QuerySet):
    """Leituras reutilizáveis da unidade."""

    def ativas(self):
        """Unidades em uso."""
        return self.filter(ativa=True)

    def do_condominio(self, condominio):
        """Unidades deste condomínio. Sem condomínio resolvido, conjunto vazio."""
        if condominio is None or not getattr(condominio, "pk", None):
            return self.none()
        return self.filter(condominio_id=condominio.pk)

    def buscar(self, termo):
        """Unidades cujo identificador, bloco ou complemento contém o termo."""
        termo = (termo or "").strip()
        if not termo:
            return self
        return self.filter(
            Q(identificador__icontains=termo)
            | Q(bloco_torre__icontains=termo)
            | Q(complemento__icontains=termo)
        )


class Unidade(models.Model):
    """Uma unidade do condomínio: apartamento, casa, loja, sala.

    O identificador é o que o condomínio usa no dia a dia — "101", "A-101",
    "Casa 03", "Loja 02" —, e é ele que precisa ser único dentro do condomínio.
    Bloco, andar e complemento são opcionais porque nem todo condomínio os tem.

    A unidade não guarda quem mora nela. Morador, proprietário e locatário são
    vínculos com data de início e fim, e é dos vínculos vigentes que sai a
    resposta a "quem está ligado a esta unidade agora?".
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    condominio = models.ForeignKey(
        Condominio,
        on_delete=models.PROTECT,
        related_name="unidades",
        verbose_name="Condomínio",
    )
    identificador = models.CharField(
        "Identificação",
        max_length=60,
        help_text="Como a unidade é chamada no condomínio: 101, A-101, Casa 03, Loja 02.",
    )
    bloco_torre = models.CharField(
        "Bloco ou torre", max_length=60, blank=True, default=""
    )
    andar = models.CharField(
        "Andar",
        max_length=20,
        blank=True,
        default="",
        help_text="Como o condomínio diz o andar. Pode ficar em branco.",
    )
    complemento = models.CharField(
        "Complemento", max_length=120, blank=True, default=""
    )
    observacao_interna = models.TextField(
        "Observação interna",
        blank=True,
        default="",
        help_text="Anotação da gestão. Não é mostrada ao morador.",
    )
    origem = models.CharField(
        "Origem",
        max_length=20,
        choices=OrigemInformacao.choices,
        default=OrigemInformacao.CADASTRO,
        help_text="De onde veio este cadastro.",
    )
    ativa = models.BooleanField(
        "Ativa",
        default=True,
        help_text="Desativar retira a unidade da operação sem apagar o cadastro.",
    )
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="unidades_cadastradas",
        verbose_name="Cadastrada por",
        help_text="Quem cadastrou. Preservado para rastreabilidade.",
    )
    criado_em = models.DateTimeField("Criada em", auto_now_add=True)
    atualizado_em = models.DateTimeField("Atualizada em", auto_now=True)

    objects = UnidadeQuerySet.as_manager()

    class Meta:
        verbose_name = "Unidade"
        verbose_name_plural = "Unidades"
        ordering = ["bloco_torre", "identificador"]
        constraints = [
            # "a-101" e "A-101" são a mesma unidade. Sem isso, a lista se enche
            # de repetições que só diferem na caixa.
            models.UniqueConstraint(
                Lower("identificador"),
                "condominio",
                name="unidade_unica_por_condominio",
            ),
        ]
        indexes = [
            models.Index(fields=["condominio", "ativa"], name="unidade_cond_ativa_idx"),
        ]

    def __str__(self) -> str:
        return self.rotulo

    def save(self, *args, **kwargs):
        """Guarda a unidade já normalizada, venha ela do formulário ou não."""
        self.identificador = _texto(self.identificador)
        for campo in ("bloco_torre", "andar", "complemento"):
            setattr(self, campo, _texto(getattr(self, campo)))
        self.observacao_interna = _texto_longo(self.observacao_interna)
        super().save(*args, **kwargs)

    def clean(self):
        """Recusa uma unidade incoerente antes que ela chegue ao banco.

        A identificação é única no condomínio sem diferenciar maiúsculas de
        minúsculas: "a-101" e "A-101" são a mesma unidade, e a repetição
        chegaria à lista como se fossem duas.
        """
        erros = {}
        identificador = _texto(self.identificador)
        if not identificador:
            erros["identificador"] = "Diga a identificação da unidade."
        elif self.condominio_id and Unidade.objects.filter(
            condominio_id=self.condominio_id, identificador__iexact=identificador
        ).exclude(pk=self.pk).exists():
            erros["identificador"] = (
                "Já existe uma unidade com esta identificação neste condomínio."
            )
        if erros:
            raise ValidationError(erros)

    @property
    def rotulo(self) -> str:
        """A unidade como a gestão a lê, com o bloco na frente quando houver."""
        if self.bloco_torre:
            return f"{self.bloco_torre} · {self.identificador}"
        return self.identificador

    # -- Estado atual, sempre derivado dos vínculos --------------------------

    def vinculos_vigentes(self):
        """Os vínculos de pessoas com esta unidade que valem hoje."""
        if not self.pk:
            return VinculoPessoaUnidade.objects.none()
        return self.vinculos_pessoa.vigentes()

    def pessoas_atuais(self):
        """As pessoas ligadas a esta unidade hoje, sem repetição."""
        if not self.pk:
            return Pessoa.objects.none()
        ids = self.vinculos_vigentes().values_list("pessoa_id", flat=True)
        return Pessoa.objects.filter(pk__in=list(ids)).distinct().order_by("nome")

    def _pessoas_atuais_por_tipo(self, tipo: str):
        """As pessoas ligadas hoje com um papel específico."""
        if not self.pk:
            return Pessoa.objects.none()
        ids = (
            self.vinculos_vigentes()
            .filter(tipo_vinculo=tipo)
            .values_list("pessoa_id", flat=True)
        )
        return Pessoa.objects.filter(pk__in=list(ids)).distinct().order_by("nome")

    def moradores_atuais(self):
        """Quem mora nesta unidade hoje."""
        return self._pessoas_atuais_por_tipo(TipoVinculoPessoaUnidade.MORADOR)

    def proprietarios_atuais(self):
        """Quem responde hoje como proprietário desta unidade."""
        return self._pessoas_atuais_por_tipo(TipoVinculoPessoaUnidade.PROPRIETARIO)

    def veiculos_relacionados(self):
        """Os veículos ligados hoje a esta unidade."""
        if not self.pk:
            return Veiculo.objects.none()
        ids = self.vinculos_veiculo.vigentes().values_list("veiculo_id", flat=True)
        return Veiculo.objects.filter(pk__in=list(ids)).distinct().order_by("placa")


# ---------------------------------------------------------------------------
# Pessoa
# ---------------------------------------------------------------------------


class PessoaQuerySet(models.QuerySet):
    """Leituras reutilizáveis da pessoa."""

    def ativas(self):
        """Pessoas em uso."""
        return self.filter(ativa=True)

    def do_condominio(self, condominio):
        """Pessoas deste condomínio. Sem condomínio resolvido, conjunto vazio."""
        if condominio is None or not getattr(condominio, "pk", None):
            return self.none()
        return self.filter(condominio_id=condominio.pk)

    def buscar(self, termo):
        """Pessoas cujo nome, telefone, WhatsApp, e-mail ou documento contém o termo."""
        termo = (termo or "").strip()
        if not termo:
            return self
        digitos = somente_digitos(termo)
        consulta = (
            Q(nome__icontains=termo)
            | Q(nome_social__icontains=termo)
            | Q(email__icontains=termo)
        )
        if digitos:
            consulta |= (
                Q(telefone__icontains=digitos)
                | Q(whatsapp__icontains=digitos)
                | Q(documento__icontains=digitos)
            )
        return self.filter(consulta)

    def possiveis_duplicidades(
        self, *, email="", telefone="", whatsapp="", documento="", excluir_pk=None
    ):
        """Pessoas que compartilham um contato ou documento com o informado.

        A detecção é determinística e serve para avisar, nunca para fundir: dois
        cadastros com o mesmo telefone podem ser a mesma pessoa, ou mãe e filha
        que dividem o número. Quem decide é a gestão, olhando o candidato.
        """
        condicoes = Q()
        email_limpo = _texto(email).lower()
        if email_limpo:
            condicoes |= Q(email=email_limpo)
        for campo, valor in (
            ("telefone", telefone),
            ("whatsapp", whatsapp),
            ("documento", documento),
        ):
            digitos = somente_digitos(valor)
            if digitos:
                condicoes |= Q(**{campo: digitos})
        if not condicoes:
            return self.none()
        candidatos = self.filter(condicoes)
        if excluir_pk:
            candidatos = candidatos.exclude(pk=excluir_pk)
        return candidatos


class Pessoa(models.Model):
    """Quem mora, responde ou trabalha em uma unidade do condomínio.

    Pessoa física ou jurídica — a empresa que é proprietária de uma sala entra
    por aqui também. O documento é opcional e guardado só com os dígitos; ele
    não é a identificação da pessoa, e por isso não aparece na lista.

    Uma pessoa existe sem unidade e sem conta no Eithra. O vínculo com a
    unidade é um fato à parte, com data de início e fim; o vínculo com a conta
    é opcional e não concede acesso a nada — quem governa o acesso continua
    sendo o vínculo da pessoa com o condomínio.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    condominio = models.ForeignKey(
        Condominio,
        on_delete=models.PROTECT,
        related_name="pessoas",
        verbose_name="Condomínio",
    )
    tipo_pessoa = models.CharField(
        "Tipo",
        max_length=20,
        choices=TipoPessoa.choices,
        default=TipoPessoa.NAO_INFORMADO,
    )
    nome = models.CharField(
        "Nome ou razão social",
        max_length=180,
        help_text="Como a pessoa é conhecida: nome completo ou razão social.",
    )
    nome_social = models.CharField(
        "Nome social",
        max_length=180,
        blank=True,
        default="",
        help_text="Como a pessoa prefere ser chamada, quando for diferente do nome.",
    )
    telefone = models.CharField(
        "Telefone",
        max_length=20,
        blank=True,
        default="",
        help_text="Guardado só com os dígitos.",
    )
    whatsapp = models.CharField(
        "WhatsApp",
        max_length=20,
        blank=True,
        default="",
        help_text="Guardado só com os dígitos.",
    )
    email = models.EmailField("E-mail", blank=True, default="")
    documento = models.CharField(
        "CPF ou CNPJ",
        max_length=20,
        blank=True,
        default="",
        help_text="Opcional. Guardado só com os dígitos.",
    )
    observacao_interna = models.TextField(
        "Observação interna",
        blank=True,
        default="",
        help_text="Anotação da gestão. Não é mostrada ao morador.",
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pessoas_da_comunidade",
        verbose_name="Conta no Eithra",
        help_text="Opcional. Não cria acesso nem senha: apenas liga esta pessoa a uma conta já existente.",
    )
    origem = models.CharField(
        "Origem",
        max_length=20,
        choices=OrigemInformacao.choices,
        default=OrigemInformacao.CADASTRO,
        help_text="De onde veio este cadastro.",
    )
    ativa = models.BooleanField(
        "Ativa",
        default=True,
        help_text="Desativar retira a pessoa da operação sem apagar o cadastro.",
    )
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pessoas_cadastradas",
        verbose_name="Cadastrada por",
        help_text="Quem cadastrou. Preservado para rastreabilidade.",
    )
    criado_em = models.DateTimeField("Criada em", auto_now_add=True)
    atualizado_em = models.DateTimeField("Atualizada em", auto_now=True)

    objects = PessoaQuerySet.as_manager()

    class Meta:
        verbose_name = "Pessoa"
        verbose_name_plural = "Pessoas"
        ordering = ["nome"]
        indexes = [
            models.Index(fields=["condominio", "ativa"], name="pessoa_cond_ativa_idx"),
            models.Index(fields=["condominio", "nome"], name="pessoa_cond_nome_idx"),
        ]

    def __str__(self) -> str:
        return self.nome_social or self.nome

    def save(self, *args, **kwargs):
        """Guarda a pessoa já normalizada, venha ela do formulário ou não."""
        self.nome = _texto(self.nome)
        self.nome_social = _texto(self.nome_social)
        self.telefone = somente_digitos(self.telefone)
        self.whatsapp = somente_digitos(self.whatsapp)
        self.email = (self.email or "").strip().lower()
        self.documento = somente_digitos(self.documento)
        self.observacao_interna = _texto_longo(self.observacao_interna)
        super().save(*args, **kwargs)

    def clean(self):
        """Recusa um cadastro incoerente antes que ele chegue ao banco.

        A conferência do documento é estrutural — a quantidade de dígitos — e
        acompanha o tipo informado. O Eithra não consulta cadastro externo nem
        calcula dígito verificador: quem digita o documento responde por ele.

        A conta ligada à pessoa, quando houver, precisa alcançar o condomínio
        dela. Uma pessoa do condomínio A não pode apontar para a conta de quem
        só responde pelo condomínio B.
        """
        erros = {}
        if not _texto(self.nome):
            erros["nome"] = "Diga o nome da pessoa."

        documento = somente_digitos(self.documento)
        if documento:
            if self.tipo_pessoa == TipoPessoa.PF and len(documento) != 11:
                erros["documento"] = "O CPF deve ter 11 dígitos."
            elif self.tipo_pessoa == TipoPessoa.PJ and len(documento) != 14:
                erros["documento"] = "O CNPJ deve ter 14 dígitos."

        if self.usuario_id and self.condominio_id:
            tem_acesso = AcessoUsuarioCondominio.objects.filter(
                usuario_id=self.usuario_id,
                condominio_id=self.condominio_id,
                ativo=True,
            ).exists()
            if not tem_acesso:
                erros["usuario"] = "Esta conta não tem acesso a este condomínio."

        if erros:
            raise ValidationError(erros)

    @property
    def documento_formatado(self) -> str:
        """O documento como a gestão o lê. Sem os dígitos esperados, mostra o que houver."""
        digitos = self.documento or ""
        if len(digitos) == 11:
            return f"{digitos[:3]}.{digitos[3:6]}.{digitos[6:9]}-{digitos[9:]}"
        if len(digitos) == 14:
            return (
                f"{digitos[:2]}.{digitos[2:5]}.{digitos[5:8]}/"
                f"{digitos[8:12]}-{digitos[12:]}"
            )
        return digitos

    # -- Estado atual, sempre derivado dos vínculos --------------------------

    def vinculos_vigentes(self):
        """Os vínculos desta pessoa com unidades que valem hoje."""
        if not self.pk:
            return VinculoPessoaUnidade.objects.none()
        return self.vinculos_unidade.vigentes()

    def unidades_atuais(self):
        """As unidades às quais esta pessoa está ligada hoje."""
        if not self.pk:
            return Unidade.objects.none()
        ids = self.vinculos_vigentes().values_list("unidade_id", flat=True)
        return Unidade.objects.filter(pk__in=list(ids)).distinct().order_by(
            "bloco_torre", "identificador"
        )

    def veiculos_atuais(self):
        """Os veículos ligados hoje a esta pessoa."""
        if not self.pk:
            return Veiculo.objects.none()
        ids = self.vinculos_veiculo.vigentes().values_list("veiculo_id", flat=True)
        return Veiculo.objects.filter(pk__in=list(ids)).distinct().order_by("placa")


# ---------------------------------------------------------------------------
# Vínculo pessoa ↔ unidade
# ---------------------------------------------------------------------------


class VinculoTemporalQuerySet(models.QuerySet):
    """Leituras que todo vínculo com vigência compartilha.

    A vigência é calculada, e não guardada: um vínculo vale quando começou e
    ainda não terminou. Guardar "vigente" como situação faria a coluna
    envelhecer sozinha — o vínculo que termina hoje de manhã continuaria marcado
    como vigente à tarde.
    """

    def do_condominio(self, condominio):
        """Vínculos deste condomínio. Sem condomínio resolvido, conjunto vazio."""
        if condominio is None or not getattr(condominio, "pk", None):
            return self.none()
        return self.filter(condominio_id=condominio.pk)

    def vigentes(self, hoje=None):
        """Vínculos que já começaram e ainda não terminaram."""
        referencia = _hoje(hoje)
        return self.filter(data_inicio__lte=referencia).filter(
            Q(data_fim__isnull=True) | Q(data_fim__gte=referencia)
        )

    def encerrados(self, hoje=None):
        """Vínculos cujo fim já chegou."""
        return self.filter(data_fim__lt=_hoje(hoje))


class VinculoPessoaUnidadeQuerySet(VinculoTemporalQuerySet):
    """Leituras reutilizáveis do vínculo entre pessoa e unidade."""

    def da_unidade(self, unidade):
        """Vínculos desta unidade."""
        if unidade is None or not getattr(unidade, "pk", None):
            return self.none()
        return self.filter(unidade_id=unidade.pk)

    def da_pessoa(self, pessoa):
        """Vínculos desta pessoa."""
        if pessoa is None or not getattr(pessoa, "pk", None):
            return self.none()
        return self.filter(pessoa_id=pessoa.pk)

    def do_tipo(self, tipo: str):
        """Vínculos de um papel específico."""
        return self.filter(tipo_vinculo=tipo)


class VinculoPessoaUnidade(models.Model):
    """O que liga uma pessoa a uma unidade, com início e fim.

    Vínculo explícito, e não uma lista escondida no meio do modelo: é ele que
    guarda o papel (proprietário, morador, locatário...), quando começou e
    quando terminou. Mudar de unidade não apaga o vínculo antigo — encerra um e
    cria outro, para que o histórico continue legível.

    A unidade admite vários vínculos ao mesmo tempo — um proprietário, um
    locatário, três moradores —, e a mesma pessoa pode ter vínculos em mais de
    uma unidade. Não existe "só um vínculo ativo por unidade".
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    condominio = models.ForeignKey(
        Condominio,
        on_delete=models.PROTECT,
        related_name="vinculos_pessoa_unidade",
        verbose_name="Condomínio",
    )
    pessoa = models.ForeignKey(
        Pessoa,
        on_delete=models.PROTECT,
        related_name="vinculos_unidade",
        verbose_name="Pessoa",
    )
    unidade = models.ForeignKey(
        Unidade,
        on_delete=models.PROTECT,
        related_name="vinculos_pessoa",
        verbose_name="Unidade",
    )
    tipo_vinculo = models.CharField(
        "Papel na unidade",
        max_length=30,
        choices=TipoVinculoPessoaUnidade.choices,
        default=TipoVinculoPessoaUnidade.MORADOR,
    )
    rotulo_origem = models.CharField(
        "Rótulo original da fonte",
        max_length=120,
        blank=True,
        default="",
        help_text="Texto original do papel na fonte, quando houver importação histórica.",
    )
    data_inicio = models.DateField(
        "Início", help_text="Desde quando este vínculo vale."
    )
    data_fim = models.DateField(
        "Fim",
        null=True,
        blank=True,
        help_text="Quando o vínculo terminou. Em branco, segue em vigor.",
    )
    observacao_interna = models.TextField(
        "Observação interna", blank=True, default=""
    )
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="vinculos_de_unidade_criados",
        verbose_name="Registrado por",
        help_text="Quem registrou. Preservado para rastreabilidade.",
    )
    criado_em = models.DateTimeField("Criado em", auto_now_add=True)
    atualizado_em = models.DateTimeField("Atualizado em", auto_now=True)

    objects = VinculoPessoaUnidadeQuerySet.as_manager()

    class Meta:
        verbose_name = "Vínculo com unidade"
        verbose_name_plural = "Vínculos com unidades"
        ordering = ["-data_inicio", "-criado_em"]
        constraints = [
            # Registrar duas vezes o mesmo vínculo, começando no mesmo dia, é
            # engano — não são dois moradores, é o mesmo anotado duas vezes.
            models.UniqueConstraint(
                fields=["pessoa", "unidade", "tipo_vinculo", "data_inicio"],
                name="vinculo_pessoa_unidade_sem_repeticao",
            ),
            models.CheckConstraint(
                condition=Q(data_fim__isnull=True) | Q(data_fim__gte=models.F("data_inicio")),
                name="vinculo_pessoa_unidade_fim_apos_inicio",
            ),
        ]
        indexes = [
            models.Index(
                fields=["condominio", "unidade"], name="vincpessoa_cond_unid_idx"
            ),
            models.Index(
                fields=["condominio", "pessoa"], name="vincpessoa_cond_pessoa_idx"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.pessoa} · {self.unidade} · {self.get_tipo_vinculo_display()}"

    def save(self, *args, **kwargs):
        """Guarda o vínculo já normalizado, venha ele do formulário ou não."""
        self.rotulo_origem = _texto(self.rotulo_origem)
        self.observacao_interna = _texto_longo(self.observacao_interna)
        super().save(*args, **kwargs)

    def clean(self):
        """Recusa um vínculo entre condomínios diferentes ou com datas impossíveis."""
        erros = {}
        pessoa = self.pessoa if self.pessoa_id else None
        unidade = self.unidade if self.unidade_id else None

        if pessoa is not None and unidade is not None:
            if pessoa.condominio_id != unidade.condominio_id:
                erros["unidade"] = "A unidade escolhida não é deste condomínio."
            if self.condominio_id and pessoa.condominio_id != self.condominio_id:
                erros["pessoa"] = "A pessoa escolhida não é deste condomínio."
            if self.condominio_id and unidade.condominio_id != self.condominio_id:
                erros["unidade"] = "A unidade escolhida não é deste condomínio."

        if self.data_inicio and self.data_fim and self.data_fim < self.data_inicio:
            erros["data_fim"] = "O fim do vínculo não pode ser antes do início."

        if erros:
            raise ValidationError(erros)

    @property
    def encerrado(self) -> bool:
        """Se o vínculo já terminou na data de hoje."""
        return bool(self.data_fim and self.data_fim < _hoje())

    @property
    def vigente(self) -> bool:
        """Se o vínculo vale hoje."""
        return bool(
            self.data_inicio
            and self.data_inicio <= _hoje()
            and (self.data_fim is None or self.data_fim >= _hoje())
        )


# ---------------------------------------------------------------------------
# Veículo
# ---------------------------------------------------------------------------


class VeiculoQuerySet(models.QuerySet):
    """Leituras reutilizáveis do veículo."""

    def ativos(self):
        """Veículos em uso."""
        return self.filter(ativo=True)

    def do_condominio(self, condominio):
        """Veículos deste condomínio. Sem condomínio resolvido, conjunto vazio."""
        if condominio is None or not getattr(condominio, "pk", None):
            return self.none()
        return self.filter(condominio_id=condominio.pk)

    def buscar(self, termo):
        """Veículos cuja placa, marca, modelo ou cor contém o termo."""
        termo = (termo or "").strip()
        if not termo:
            return self
        return self.filter(
            Q(placa__icontains=termo.upper())
            | Q(marca__icontains=termo)
            | Q(modelo__icontains=termo)
            | Q(cor__icontains=termo)
        )


class Veiculo(models.Model):
    """O que circula pelo condomínio, identificado pela placa.

    A placa é obrigatória e única dentro do condomínio: dois cadastros com a
    mesma placa são o mesmo veículo. O Eithra aceita tanto a placa antiga
    (ABC1234) quanto a do Mercosul (ABC1D23) e não consulta nenhum serviço
    externo — quem informa a placa responde por ela.

    O veículo não pertence fixamente a uma unidade: ele troca de condutor, muda
    de vaga e pode ser compartilhado. Quem usa e a qual unidade ele se refere
    são vínculos separados, e uma pergunta não se responde pela outra.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    condominio = models.ForeignKey(
        Condominio,
        on_delete=models.PROTECT,
        related_name="veiculos",
        verbose_name="Condomínio",
    )
    placa = models.CharField(
        "Placa",
        max_length=10,
        help_text="Aceita o padrão antigo (ABC1234) e o do Mercosul (ABC1D23).",
    )
    marca = models.CharField("Marca", max_length=60, blank=True, default="")
    modelo = models.CharField("Modelo", max_length=60, blank=True, default="")
    cor = models.CharField("Cor", max_length=40, blank=True, default="")
    observacao_interna = models.TextField(
        "Observação interna", blank=True, default=""
    )
    origem = models.CharField(
        "Origem",
        max_length=20,
        choices=OrigemInformacao.choices,
        default=OrigemInformacao.CADASTRO,
        help_text="De onde veio este cadastro.",
    )
    ativo = models.BooleanField(
        "Ativo",
        default=True,
        help_text="Desativar retira o veículo da operação sem apagar o cadastro.",
    )
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="veiculos_cadastrados",
        verbose_name="Cadastrado por",
        help_text="Quem cadastrou. Preservado para rastreabilidade.",
    )
    criado_em = models.DateTimeField("Criado em", auto_now_add=True)
    atualizado_em = models.DateTimeField("Atualizado em", auto_now=True)

    objects = VeiculoQuerySet.as_manager()

    class Meta:
        verbose_name = "Veículo"
        verbose_name_plural = "Veículos"
        ordering = ["placa"]
        constraints = [
            models.UniqueConstraint(
                Lower("placa"),
                "condominio",
                name="veiculo_placa_unica_por_condominio",
            ),
        ]
        indexes = [
            models.Index(fields=["condominio", "ativo"], name="veiculo_cond_ativo_idx"),
        ]

    def __str__(self) -> str:
        return self.placa

    def save(self, *args, **kwargs):
        """Guarda o veículo já normalizado, venha ele do formulário ou não."""
        self.placa = _placa(self.placa)
        for campo in ("marca", "modelo", "cor"):
            setattr(self, campo, _texto(getattr(self, campo)))
        self.observacao_interna = _texto_longo(self.observacao_interna)
        super().save(*args, **kwargs)

    def clean(self):
        """Recusa uma placa impossível ou repetida antes que ela chegue ao banco."""
        erros = {}
        placa = _placa(self.placa)
        if not placa:
            erros["placa"] = "Informe a placa do veículo."
        elif not re.fullmatch(r"[A-Z]{3}[0-9][0-9A-Z][0-9]{2}", placa):
            erros["placa"] = (
                "A placa deve ter 3 letras, um número e mais 3 caracteres "
                "(como ABC1234 ou ABC1D23)."
            )
        elif self.condominio_id and Veiculo.objects.filter(
            condominio_id=self.condominio_id, placa=placa
        ).exclude(pk=self.pk).exists():
            erros["placa"] = "Já existe um veículo com esta placa neste condomínio."
        if erros:
            raise ValidationError(erros)

    # -- Estado atual, sempre derivado dos vínculos --------------------------

    def pessoas_atuais(self):
        """Quem utiliza este veículo hoje."""
        if not self.pk:
            return Pessoa.objects.none()
        ids = self.vinculos_pessoa.vigentes().values_list("pessoa_id", flat=True)
        return Pessoa.objects.filter(pk__in=list(ids)).distinct().order_by("nome")

    def unidades_atuais(self):
        """A quais unidades este veículo está relacionado hoje."""
        if not self.pk:
            return Unidade.objects.none()
        ids = self.vinculos_unidade.vigentes().values_list("unidade_id", flat=True)
        return Unidade.objects.filter(pk__in=list(ids)).distinct().order_by(
            "bloco_torre", "identificador"
        )


# ---------------------------------------------------------------------------
# Vínculo veículo ↔ pessoa
# ---------------------------------------------------------------------------


class VinculoVeiculoPessoaQuerySet(VinculoTemporalQuerySet):
    """Leituras reutilizáveis do vínculo entre veículo e pessoa."""

    def do_veiculo(self, veiculo):
        """Vínculos deste veículo."""
        if veiculo is None or not getattr(veiculo, "pk", None):
            return self.none()
        return self.filter(veiculo_id=veiculo.pk)

    def da_pessoa(self, pessoa):
        """Vínculos desta pessoa."""
        if pessoa is None or not getattr(pessoa, "pk", None):
            return self.none()
        return self.filter(pessoa_id=pessoa.pk)


class VinculoVeiculoPessoa(models.Model):
    """Quem utiliza um veículo, com início e fim.

    Vínculo separado do vínculo com a unidade de propósito: o mesmo carro pode
    ser usado por quem mora em uma unidade e estar relacionado a outra. Uma
    pergunta não se responde pela outra.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    condominio = models.ForeignKey(
        Condominio,
        on_delete=models.PROTECT,
        related_name="vinculos_veiculo_pessoa",
        verbose_name="Condomínio",
    )
    veiculo = models.ForeignKey(
        Veiculo,
        on_delete=models.PROTECT,
        related_name="vinculos_pessoa",
        verbose_name="Veículo",
    )
    pessoa = models.ForeignKey(
        Pessoa,
        on_delete=models.PROTECT,
        related_name="vinculos_veiculo",
        verbose_name="Pessoa",
    )
    data_inicio = models.DateField("Início", help_text="Desde quando este vínculo vale.")
    data_fim = models.DateField(
        "Fim",
        null=True,
        blank=True,
        help_text="Quando o vínculo terminou. Em branco, segue em vigor.",
    )
    observacao = models.TextField("Observação", blank=True, default="")
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="vinculos_de_veiculo_com_pessoa_criados",
        verbose_name="Registrado por",
        help_text="Quem registrou. Preservado para rastreabilidade.",
    )
    criado_em = models.DateTimeField("Criado em", auto_now_add=True)
    atualizado_em = models.DateTimeField("Atualizado em", auto_now=True)

    objects = VinculoVeiculoPessoaQuerySet.as_manager()

    class Meta:
        verbose_name = "Vínculo de veículo com pessoa"
        verbose_name_plural = "Vínculos de veículos com pessoas"
        ordering = ["-data_inicio", "-criado_em"]
        constraints = [
            models.UniqueConstraint(
                fields=["veiculo", "pessoa", "data_inicio"],
                name="vinculo_veiculo_pessoa_sem_repeticao",
            ),
            models.CheckConstraint(
                condition=Q(data_fim__isnull=True) | Q(data_fim__gte=models.F("data_inicio")),
                name="vinculo_veiculo_pessoa_fim_apos_inicio",
            ),
        ]
        indexes = [
            models.Index(fields=["condominio", "veiculo"], name="vincveicpess_cond_veic_idx"),
            models.Index(fields=["condominio", "pessoa"], name="vincveicpess_cond_pess_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.veiculo} · {self.pessoa}"

    def save(self, *args, **kwargs):
        """Guarda o vínculo já normalizado, venha ele do formulário ou não."""
        self.observacao = _texto_longo(self.observacao)
        super().save(*args, **kwargs)

    def clean(self):
        """Recusa um vínculo entre condomínios diferentes ou com datas impossíveis."""
        erros = {}
        veiculo = self.veiculo if self.veiculo_id else None
        pessoa = self.pessoa if self.pessoa_id else None

        if veiculo is not None and pessoa is not None:
            if veiculo.condominio_id != pessoa.condominio_id:
                erros["pessoa"] = "A pessoa escolhida não é deste condomínio."
            if self.condominio_id and veiculo.condominio_id != self.condominio_id:
                erros["veiculo"] = "O veículo escolhido não é deste condomínio."

        if self.data_inicio and self.data_fim and self.data_fim < self.data_inicio:
            erros["data_fim"] = "O fim do vínculo não pode ser antes do início."

        if erros:
            raise ValidationError(erros)

    @property
    def encerrado(self) -> bool:
        """Se o vínculo já terminou na data de hoje."""
        return bool(self.data_fim and self.data_fim < _hoje())

    @property
    def vigente(self) -> bool:
        """Se o vínculo vale hoje."""
        return bool(
            self.data_inicio
            and self.data_inicio <= _hoje()
            and (self.data_fim is None or self.data_fim >= _hoje())
        )


# ---------------------------------------------------------------------------
# Vínculo veículo ↔ unidade
# ---------------------------------------------------------------------------


class VinculoVeiculoUnidadeQuerySet(VinculoTemporalQuerySet):
    """Leituras reutilizáveis do vínculo entre veículo e unidade."""

    def do_veiculo(self, veiculo):
        """Vínculos deste veículo."""
        if veiculo is None or not getattr(veiculo, "pk", None):
            return self.none()
        return self.filter(veiculo_id=veiculo.pk)

    def da_unidade(self, unidade):
        """Vínculos desta unidade."""
        if unidade is None or not getattr(unidade, "pk", None):
            return self.none()
        return self.filter(unidade_id=unidade.pk)


class VinculoVeiculoUnidade(models.Model):
    """A qual unidade um veículo está relacionado, com início e fim.

    É uma pergunta diferente de "quem utiliza este veículo". O carro de uma
    empresa pode estar relacionado à unidade onde fica guardado sem que a
    empresa seja quem o dirige, e o contrário também.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    condominio = models.ForeignKey(
        Condominio,
        on_delete=models.PROTECT,
        related_name="vinculos_veiculo_unidade",
        verbose_name="Condomínio",
    )
    veiculo = models.ForeignKey(
        Veiculo,
        on_delete=models.PROTECT,
        related_name="vinculos_unidade",
        verbose_name="Veículo",
    )
    unidade = models.ForeignKey(
        Unidade,
        on_delete=models.PROTECT,
        related_name="vinculos_veiculo",
        verbose_name="Unidade",
    )
    data_inicio = models.DateField("Início", help_text="Desde quando este vínculo vale.")
    data_fim = models.DateField(
        "Fim",
        null=True,
        blank=True,
        help_text="Quando o vínculo terminou. Em branco, segue em vigor.",
    )
    observacao = models.TextField("Observação", blank=True, default="")
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="vinculos_de_veiculo_com_unidade_criados",
        verbose_name="Registrado por",
        help_text="Quem registrou. Preservado para rastreabilidade.",
    )
    criado_em = models.DateTimeField("Criado em", auto_now_add=True)
    atualizado_em = models.DateTimeField("Atualizado em", auto_now=True)

    objects = VinculoVeiculoUnidadeQuerySet.as_manager()

    class Meta:
        verbose_name = "Vínculo de veículo com unidade"
        verbose_name_plural = "Vínculos de veículos com unidades"
        ordering = ["-data_inicio", "-criado_em"]
        constraints = [
            models.UniqueConstraint(
                fields=["veiculo", "unidade", "data_inicio"],
                name="vinculo_veiculo_unidade_sem_repeticao",
            ),
            models.CheckConstraint(
                condition=Q(data_fim__isnull=True) | Q(data_fim__gte=models.F("data_inicio")),
                name="vinculo_veiculo_unidade_fim_apos_inicio",
            ),
        ]
        indexes = [
            models.Index(fields=["condominio", "veiculo"], name="vincveicunid_cond_veic_idx"),
            models.Index(fields=["condominio", "unidade"], name="vincveicunid_cond_unid_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.veiculo} · {self.unidade}"

    def save(self, *args, **kwargs):
        """Guarda o vínculo já normalizado, venha ele do formulário ou não."""
        self.observacao = _texto_longo(self.observacao)
        super().save(*args, **kwargs)

    def clean(self):
        """Recusa um vínculo entre condomínios diferentes ou com datas impossíveis."""
        erros = {}
        veiculo = self.veiculo if self.veiculo_id else None
        unidade = self.unidade if self.unidade_id else None

        if veiculo is not None and unidade is not None:
            if veiculo.condominio_id != unidade.condominio_id:
                erros["unidade"] = "A unidade escolhida não é deste condomínio."
            if self.condominio_id and veiculo.condominio_id != self.condominio_id:
                erros["veiculo"] = "O veículo escolhido não é deste condomínio."

        if self.data_inicio and self.data_fim and self.data_fim < self.data_inicio:
            erros["data_fim"] = "O fim do vínculo não pode ser antes do início."

        if erros:
            raise ValidationError(erros)

    @property
    def encerrado(self) -> bool:
        """Se o vínculo já terminou na data de hoje."""
        return bool(self.data_fim and self.data_fim < _hoje())

    @property
    def vigente(self) -> bool:
        """Se o vínculo vale hoje."""
        return bool(
            self.data_inicio
            and self.data_inicio <= _hoje()
            and (self.data_fim is None or self.data_fim >= _hoje())
        )
