"""Modelos do domínio de cadastros básicos operacionais.

Três cadastros que a gestão mantém para o trabalho do dia a dia: onde as coisas
acontecem (local), o que existe nesses lugares (equipamento) e quem atende o
condomínio (fornecedor).

Nada aqui é histórico. Um cadastro que sai de uso é desativado, e não apagado,
para que o que aponta para ele continue legível. E todo cadastro pertence a um
condomínio: esta é a fronteira do sistema — não existe cadastro sem dono, e
nada de um condomínio deve aparecer em outro.
"""
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower

from apps.condominios.models import Condominio, somente_digitos

from .constants import TipoLocal, TipoPessoa


def _texto(valor: str | None) -> str:
    """Colapsa espaços repetidos e devolve string vazia quando não há conteúdo."""
    return " ".join((valor or "").split())


def _texto_longo(valor: str | None) -> str:
    """Preserva os parágrafos de um texto livre, sem espaços nas pontas."""
    return (valor or "").strip()


class LocalOperacionalQuerySet(models.QuerySet):
    """Leituras reutilizáveis do local."""

    def ativos(self):
        """Locais em uso."""
        return self.filter(ativo=True)

    def do_condominio(self, condominio):
        """Locais deste condomínio. Sem condomínio resolvido, conjunto vazio."""
        if condominio is None or not getattr(condominio, "pk", None):
            return self.none()
        return self.filter(condominio_id=condominio.pk)

    def buscar(self, termo):
        """Locais cujo nome ou descrição contém o termo."""
        termo = (termo or "").strip()
        if not termo:
            return self
        return self.filter(Q(nome__icontains=termo) | Q(descricao__icontains=termo))


class LocalOperacional(models.Model):
    """Um lugar do condomínio que importa para a operação.

    Bloco, torre, garagem, elevador, casa de máquinas: o que a gestão nomeia e
    cita quando fala de uma demanda ou de um equipamento. É a lista de lugares
    de um condomínio — e de mais nenhum.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    condominio = models.ForeignKey(
        Condominio,
        on_delete=models.PROTECT,
        related_name="locais",
        verbose_name="Condomínio",
    )
    nome = models.CharField(
        "Nome",
        max_length=150,
        help_text="Como este lugar é chamado no dia a dia do condomínio.",
    )
    tipo = models.CharField(
        "Tipo",
        max_length=20,
        choices=TipoLocal.choices,
        default=TipoLocal.OUTRO,
    )
    descricao = models.TextField(
        "Descrição",
        blank=True,
        default="",
        help_text="Detalhe opcional do que é este lugar.",
    )
    ativo = models.BooleanField(
        "Ativo",
        default=True,
        help_text="Desativar retira o local da operação sem apagar o cadastro.",
    )
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="locais_cadastrados",
        verbose_name="Cadastrado por",
        help_text="Quem cadastrou. Preservado para rastreabilidade.",
    )
    criado_em = models.DateTimeField("Criado em", auto_now_add=True)
    atualizado_em = models.DateTimeField("Atualizado em", auto_now=True)

    objects = LocalOperacionalQuerySet.as_manager()

    class Meta:
        verbose_name = "Local"
        verbose_name_plural = "Locais"
        ordering = ["nome"]
        constraints = [
            # Um condomínio não tem dois lugares com o mesmo nome. Reativar um
            # local é mudar o cadastro dele, e não criar um segundo.
            models.UniqueConstraint(
                Lower("nome"),
                "condominio",
                name="local_unico_por_condominio",
            ),
        ]
        indexes = [
            models.Index(fields=["condominio", "ativo"], name="local_cond_ativo_idx"),
            models.Index(fields=["condominio", "tipo"], name="local_cond_tipo_idx"),
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
            erros["nome"] = "Dê um nome ao local."
        if erros:
            raise ValidationError(erros)


class EquipamentoQuerySet(models.QuerySet):
    """Leituras reutilizáveis do equipamento."""

    def ativos(self):
        """Equipamentos em uso."""
        return self.filter(ativo=True)

    def do_condominio(self, condominio):
        """Equipamentos deste condomínio. Sem condomínio resolvido, conjunto vazio."""
        if condominio is None or not getattr(condominio, "pk", None):
            return self.none()
        return self.filter(condominio_id=condominio.pk)

    def por_local(self, local):
        """Equipamentos deste local."""
        if local is None or not getattr(local, "pk", None):
            return self.none()
        return self.filter(local_id=local.pk)

    def buscar(self, termo):
        """Equipamentos cujo nome, tipo, marca, modelo ou série contém o termo."""
        termo = (termo or "").strip()
        if not termo:
            return self
        return self.filter(
            Q(nome__icontains=termo)
            | Q(tipo_texto__icontains=termo)
            | Q(fabricante__icontains=termo)
            | Q(modelo__icontains=termo)
            | Q(numero_serie__icontains=termo)
            | Q(patrimonio__icontains=termo)
        )


class Equipamento(models.Model):
    """O que existe em um lugar e precisa de manutenção: bomba, portão, elevador.

    O equipamento pertence ao condomínio e, quando se sabe, fica em um local
    daquele mesmo condomínio. Fabricante, modelo, número de série e patrimônio
    são o que ajuda a achar a peça certa e a reconhecer o aparelho depois.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    condominio = models.ForeignKey(
        Condominio,
        on_delete=models.PROTECT,
        related_name="equipamentos",
        verbose_name="Condomínio",
    )
    local = models.ForeignKey(
        LocalOperacional,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="equipamentos",
        verbose_name="Local",
        help_text="Onde o equipamento fica. Pode ficar sem local definido.",
    )
    nome = models.CharField(
        "Nome",
        max_length=150,
        help_text="Como este equipamento é conhecido no condomínio.",
    )
    tipo_texto = models.CharField(
        "Tipo",
        max_length=80,
        blank=True,
        default="",
        help_text="Em texto livre: bomba, portão, elevador, interfone.",
    )
    fabricante = models.CharField("Fabricante", max_length=120, blank=True, default="")
    modelo = models.CharField("Modelo", max_length=120, blank=True, default="")
    numero_serie = models.CharField(
        "Número de série", max_length=80, blank=True, default=""
    )
    patrimonio = models.CharField(
        "Patrimônio",
        max_length=60,
        blank=True,
        default="",
        help_text="Identificação patrimonial, quando o condomínio usar uma.",
    )
    observacao = models.TextField(
        "Observação",
        blank=True,
        default="",
        help_text="O que se sabe sobre o equipamento e não cabe nos outros campos.",
    )
    ativo = models.BooleanField(
        "Ativo",
        default=True,
        help_text="Desativar retira o equipamento da operação sem apagar o cadastro.",
    )
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="equipamentos_cadastrados",
        verbose_name="Cadastrado por",
        help_text="Quem cadastrou. Preservado para rastreabilidade.",
    )
    criado_em = models.DateTimeField("Criado em", auto_now_add=True)
    atualizado_em = models.DateTimeField("Atualizado em", auto_now=True)

    objects = EquipamentoQuerySet.as_manager()

    class Meta:
        verbose_name = "Equipamento"
        verbose_name_plural = "Equipamentos"
        ordering = ["nome"]
        constraints = [
            models.UniqueConstraint(
                Lower("nome"),
                "condominio",
                "local",
                name="equipamento_unico_por_local",
            ),
        ]
        indexes = [
            models.Index(fields=["condominio", "ativo"], name="equip_cond_ativo_idx"),
            models.Index(fields=["condominio", "local"], name="equip_cond_local_idx"),
        ]

    def __str__(self) -> str:
        return self.nome

    def save(self, *args, **kwargs):
        """Guarda o cadastro já normalizado, venha ele do formulário ou não."""
        self.nome = _texto(self.nome)
        for campo in ("tipo_texto", "fabricante", "modelo", "numero_serie", "patrimonio"):
            setattr(self, campo, _texto(getattr(self, campo)))
        self.observacao = _texto_longo(self.observacao)
        super().save(*args, **kwargs)

    def clean(self):
        """Recusa um cadastro incoerente antes que ele chegue ao banco."""
        erros = {}
        if not _texto(self.nome):
            erros["nome"] = "Dê um nome ao equipamento."

        # O equipamento e o local dele são do mesmo condomínio. Sem isso, um
        # cadastro poderia apontar para o prédio do lado.
        if self.local_id and self.condominio_id:
            local = self.local
            if local is not None and local.condominio_id != self.condominio_id:
                erros["local"] = "O local escolhido não é deste condomínio."

        if erros:
            raise ValidationError(erros)


class FornecedorQuerySet(models.QuerySet):
    """Leituras reutilizáveis do fornecedor."""

    def ativos(self):
        """Fornecedores em uso."""
        return self.filter(ativo=True)

    def do_condominio(self, condominio):
        """Fornecedores deste condomínio. Sem condomínio resolvido, conjunto vazio."""
        if condominio is None or not getattr(condominio, "pk", None):
            return self.none()
        return self.filter(condominio_id=condominio.pk)

    def buscar(self, termo):
        """Fornecedores cujo nome, fantasia, documento, e-mail ou contato contém o termo."""
        termo = (termo or "").strip()
        if not termo:
            return self
        return self.filter(
            Q(nome_razao_social__icontains=termo)
            | Q(nome_fantasia__icontains=termo)
            | Q(documento__icontains=termo)
            | Q(email__icontains=termo)
            | Q(contato__icontains=termo)
        )


class Fornecedor(models.Model):
    """Quem atende o condomínio: a empresa do elevador, o eletricista, a dedetizadora.

    O documento identifica o fornecedor dentro do condomínio: dois cadastros com
    o mesmo CPF ou CNPJ são o mesmo fornecedor. Sem documento informado, o
    cadastro continua válido — e vários podem existir sem documento.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    condominio = models.ForeignKey(
        Condominio,
        on_delete=models.PROTECT,
        related_name="fornecedores",
        verbose_name="Condomínio",
    )
    nome_razao_social = models.CharField(
        "Nome ou razão social",
        max_length=180,
        help_text="Como o fornecedor se chama formalmente.",
    )
    nome_fantasia = models.CharField(
        "Nome fantasia", max_length=180, blank=True, default=""
    )
    tipo_pessoa = models.CharField(
        "Tipo",
        max_length=20,
        choices=TipoPessoa.choices,
        default=TipoPessoa.NAO_INFORMADO,
    )
    documento = models.CharField(
        "CPF ou CNPJ",
        max_length=20,
        blank=True,
        default="",
        help_text="Guardado só com os dígitos. Aceita ser digitado com máscara.",
    )
    email = models.EmailField("E-mail", blank=True, default="")
    telefone = models.CharField(
        "Telefone",
        max_length=20,
        blank=True,
        default="",
        help_text="Guardado só com os dígitos.",
    )
    contato = models.CharField(
        "Pessoa de contato",
        max_length=120,
        blank=True,
        default="",
        help_text="Com quem se fala no dia a dia, quando não é o próprio fornecedor.",
    )
    observacao = models.TextField(
        "Observação",
        blank=True,
        default="",
        help_text="O que se sabe sobre o fornecedor e não cabe nos outros campos.",
    )
    ativo = models.BooleanField(
        "Ativo",
        default=True,
        help_text="Desativar retira o fornecedor da operação sem apagar o cadastro.",
    )
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="fornecedores_cadastrados",
        verbose_name="Cadastrado por",
        help_text="Quem cadastrou. Preservado para rastreabilidade.",
    )
    criado_em = models.DateTimeField("Criado em", auto_now_add=True)
    atualizado_em = models.DateTimeField("Atualizado em", auto_now=True)

    objects = FornecedorQuerySet.as_manager()

    class Meta:
        verbose_name = "Fornecedor"
        verbose_name_plural = "Fornecedores"
        ordering = ["nome_razao_social"]
        constraints = [
            # Dentro do mesmo condomínio, o mesmo documento é o mesmo
            # fornecedor. Sem documento, nada é comparado — dois cadastros sem
            # CPF ou CNPJ não são o mesmo fornecedor por isso.
            models.UniqueConstraint(
                fields=["condominio", "documento"],
                condition=~Q(documento=""),
                name="fornecedor_doc_unico_cond",
            ),
        ]
        indexes = [
            models.Index(fields=["condominio", "ativo"], name="fornec_cond_ativo_idx"),
            models.Index(
                fields=["condominio", "tipo_pessoa"], name="fornec_cond_tipo_idx"
            ),
        ]

    def __str__(self) -> str:
        return self.nome_fantasia or self.nome_razao_social

    def save(self, *args, **kwargs):
        """Guarda o cadastro já normalizado, venha ele do formulário ou não."""
        self.nome_razao_social = _texto(self.nome_razao_social)
        self.nome_fantasia = _texto(self.nome_fantasia)
        self.contato = _texto(self.contato)
        self.documento = somente_digitos(self.documento)
        self.telefone = somente_digitos(self.telefone)
        self.email = (self.email or "").strip().lower()
        self.observacao = _texto_longo(self.observacao)
        super().save(*args, **kwargs)

    def clean(self):
        """Recusa um documento que não corresponde ao tipo informado.

        A conferência é estrutural: a quantidade de dígitos. O Eithra não
        consulta cadastro externo nem calcula dígito verificador — quem digita o
        documento responde por ele.
        """
        erros = {}
        if not _texto(self.nome_razao_social):
            erros["nome_razao_social"] = "Diga o nome do fornecedor."

        documento = somente_digitos(self.documento)
        if documento:
            if self.tipo_pessoa == TipoPessoa.PF and len(documento) != 11:
                erros["documento"] = "O CPF deve ter 11 dígitos."
            elif self.tipo_pessoa == TipoPessoa.PJ and len(documento) != 14:
                erros["documento"] = "O CNPJ deve ter 14 dígitos."
            elif self.condominio_id and Fornecedor.objects.filter(
                condominio_id=self.condominio_id, documento=documento
            ).exclude(pk=self.pk).exists():
                # Dentro do mesmo condomínio, o mesmo documento é o mesmo
                # fornecedor. A regra vale aqui para a tela recusar antes de
                # gravar, e não só quando o banco já barrou.
                erros["documento"] = (
                    "Já existe um fornecedor com este documento neste condomínio."
                )

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
