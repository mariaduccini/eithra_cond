"""Modelos do domínio de cadastros básicos operacionais.

Três cadastros que a gestão mantém para o trabalho do dia a dia: onde as coisas
acontecem (local), o que existe nesses lugares (equipamento) e quem atende o
condomínio (fornecedor). Antes deles vêm os tipos que os classificam — tipo de
local, tipo de equipamento e especialidade do fornecedor —, porque um cadastro
sem classificação obriga a gestão a escrever o mesmo rótulo de novo em cada
linha.

Nada aqui é histórico. Um cadastro que sai de uso é desativado, e não apagado,
para que o que aponta para ele continue legível. E todo cadastro pertence a um
condomínio: esta é a fronteira do sistema — não existe cadastro sem dono, e
nada de um condomínio deve aparecer em outro.
"""
import uuid
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower
from django.utils import timezone

from apps.condominios.models import Condominio, somente_digitos

from .constants import TipoLocal, TipoPessoa


def _texto(valor: str | None) -> str:
    """Colapsa espaços repetidos e devolve string vazia quando não há conteúdo."""
    return " ".join((valor or "").split())


def _texto_longo(valor: str | None) -> str:
    """Preserva os parágrafos de um texto livre, sem espaços nas pontas."""
    return (valor or "").strip()


# ---------------------------------------------------------------------------
# Tipos: as listas que a gestão mantém para classificar o que cadastra
# ---------------------------------------------------------------------------
# Os três tipos abaixo têm a mesma forma — nome, descrição, ativo — e a mesma
# regra: o nome é único dentro do condomínio, sem distinguir maiúsculas de
# minúsculas. Escrevê-los por extenso é deliberado: cada um tem o seu
# `related_name` e o seu vocabulário, e um deles não deve poder ser trocado pelo
# outro sem que alguém perceba.


class TipoLocalOperacionalQuerySet(models.QuerySet):
    """Leituras reutilizáveis do tipo de local."""

    def ativos(self):
        """Tipos em uso."""
        return self.filter(ativo=True)

    def do_condominio(self, condominio):
        """Tipos deste condomínio. Sem condomínio resolvido, conjunto vazio."""
        if condominio is None or not getattr(condominio, "pk", None):
            return self.none()
        return self.filter(condominio_id=condominio.pk)


class TipoLocalOperacional(models.Model):
    """Como o condomínio classifica seus lugares: área comum, garagem, torre.

    Antes esta lista era fixa no código. Aqui ela é cadastro de cada
    condomínio: a gestão nomeia os seus, e o campo antigo continua guardado
    enquanto os locais já existentes não forem conferidos um a um.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    condominio = models.ForeignKey(
        Condominio,
        on_delete=models.PROTECT,
        related_name="tipos_de_local",
        verbose_name="Condomínio",
    )
    nome = models.CharField(
        "Nome",
        max_length=120,
        help_text="Como o condomínio chama este tipo de lugar.",
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
        related_name="tipos_de_local_cadastrados",
        verbose_name="Cadastrado por",
        help_text="Quem cadastrou. Preservado para rastreabilidade.",
    )
    criado_em = models.DateTimeField("Criado em", auto_now_add=True)
    atualizado_em = models.DateTimeField("Atualizado em", auto_now=True)

    objects = TipoLocalOperacionalQuerySet.as_manager()

    class Meta:
        verbose_name = "Tipo de local"
        verbose_name_plural = "Tipos de local"
        ordering = ["nome"]
        constraints = [
            # "Garagem" e "garagem" são o mesmo tipo. Sem isso, a lista se
            # enche de repetições que só diferem na caixa.
            models.UniqueConstraint(
                Lower("nome"),
                "condominio",
                name="tipo_local_unico_por_condominio",
            ),
        ]
        indexes = [
            models.Index(
                fields=["condominio", "ativo"], name="tipolocal_cond_ativo_idx"
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
            erros["nome"] = "Dê um nome ao tipo de local."
        if erros:
            raise ValidationError(erros)


class TipoEquipamentoQuerySet(models.QuerySet):
    """Leituras reutilizáveis do tipo de equipamento."""

    def ativos(self):
        """Tipos em uso."""
        return self.filter(ativo=True)

    def do_condominio(self, condominio):
        """Tipos deste condomínio. Sem condomínio resolvido, conjunto vazio."""
        if condominio is None or not getattr(condominio, "pk", None):
            return self.none()
        return self.filter(condominio_id=condominio.pk)


class TipoEquipamento(models.Model):
    """Como o condomínio classifica o que mantém: bomba, portão, elevador.

    Substitui, aos poucos, o texto livre do campo antigo: um texto livre vira
    trezentas grafias do mesmo equipamento e nenhuma lista confiável.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    condominio = models.ForeignKey(
        Condominio,
        on_delete=models.PROTECT,
        related_name="tipos_de_equipamento",
        verbose_name="Condomínio",
    )
    nome = models.CharField(
        "Nome",
        max_length=120,
        help_text="Como o condomínio chama este tipo de equipamento.",
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
        related_name="tipos_de_equipamento_cadastrados",
        verbose_name="Cadastrado por",
        help_text="Quem cadastrou. Preservado para rastreabilidade.",
    )
    criado_em = models.DateTimeField("Criado em", auto_now_add=True)
    atualizado_em = models.DateTimeField("Atualizado em", auto_now=True)

    objects = TipoEquipamentoQuerySet.as_manager()

    class Meta:
        verbose_name = "Tipo de equipamento"
        verbose_name_plural = "Tipos de equipamento"
        ordering = ["nome"]
        constraints = [
            models.UniqueConstraint(
                Lower("nome"),
                "condominio",
                name="tipo_equipamento_unico_por_condominio",
            ),
        ]
        indexes = [
            models.Index(
                fields=["condominio", "ativo"], name="tipoequip_cond_ativo_idx"
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
            erros["nome"] = "Dê um nome ao tipo de equipamento."
        if erros:
            raise ValidationError(erros)


class EspecialidadeFornecedorQuerySet(models.QuerySet):
    """Leituras reutilizáveis da especialidade."""

    def ativos(self):
        """Especialidades em uso."""
        return self.filter(ativo=True)

    def do_condominio(self, condominio):
        """Especialidades deste condomínio. Sem condomínio resolvido, conjunto vazio."""
        if condominio is None or not getattr(condominio, "pk", None):
            return self.none()
        return self.filter(condominio_id=condominio.pk)


class EspecialidadeFornecedor(models.Model):
    """O que um fornecedor sabe fazer: elétrica, hidráulica, elevadores.

    É o que permite perguntar "quem atende hidráulica?" sem depender de alguém
    ter escrito isso no nome do fornecedor.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    condominio = models.ForeignKey(
        Condominio,
        on_delete=models.PROTECT,
        related_name="especialidades_de_fornecedor",
        verbose_name="Condomínio",
    )
    nome = models.CharField(
        "Nome",
        max_length=120,
        help_text="Como o condomínio chama esta especialidade.",
    )
    descricao = models.TextField(
        "Descrição",
        blank=True,
        default="",
        help_text="Detalhe opcional do que entra nesta especialidade.",
    )
    ativo = models.BooleanField(
        "Ativo",
        default=True,
        help_text="Desativar retira a especialidade das opções novas sem apagar o cadastro.",
    )
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="especialidades_cadastradas",
        verbose_name="Cadastrado por",
        help_text="Quem cadastrou. Preservado para rastreabilidade.",
    )
    criado_em = models.DateTimeField("Criado em", auto_now_add=True)
    atualizado_em = models.DateTimeField("Atualizado em", auto_now=True)

    objects = EspecialidadeFornecedorQuerySet.as_manager()

    class Meta:
        verbose_name = "Especialidade"
        verbose_name_plural = "Especialidades"
        ordering = ["nome"]
        constraints = [
            models.UniqueConstraint(
                Lower("nome"),
                "condominio",
                name="especialidade_unica_por_condominio",
            ),
        ]
        indexes = [
            models.Index(
                fields=["condominio", "ativo"], name="especialidade_cond_ativo_idx"
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
            erros["nome"] = "Dê um nome à especialidade."
        if erros:
            raise ValidationError(erros)


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
        help_text="Classificação antiga, mantida enquanto os locais existentes não forem conferidos.",
    )
    tipo_local = models.ForeignKey(
        TipoLocalOperacional,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="locais",
        verbose_name="Tipo de local",
        help_text="Classificação mantida pelo condomínio. Pode ficar sem tipo definido.",
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
            models.Index(
                fields=["condominio", "tipo_local"], name="local_cond_tipolocal_idx"
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
            erros["nome"] = "Dê um nome ao local."

        # O local e o tipo dele são do mesmo condomínio. Sem isso, a lista de
        # tipos de um prédio poderia classificar um lugar do prédio do lado.
        if self.tipo_local_id and self.condominio_id:
            tipo_local = self.tipo_local
            if tipo_local is not None and tipo_local.condominio_id != self.condominio_id:
                erros["tipo_local"] = "O tipo escolhido não é deste condomínio."

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
        help_text="Em texto livre: bomba, portão, elevador, interfone. Classificação antiga, mantida enquanto os equipamentos existentes não forem conferidos.",
    )
    tipo_equipamento = models.ForeignKey(
        TipoEquipamento,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="equipamentos",
        verbose_name="Tipo de equipamento",
        help_text="Classificação mantida pelo condomínio. Pode ficar sem tipo definido.",
    )
    data_aquisicao = models.DateField(
        "Data de aquisição",
        null=True,
        blank=True,
        help_text="Quando o condomínio adquiriu o equipamento, se souber.",
    )
    data_instalacao = models.DateField(
        "Data de instalação",
        null=True,
        blank=True,
        help_text="Quando o equipamento entrou em operação, se souber.",
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
            models.Index(
                fields=["condominio", "tipo_equipamento"],
                name="equip_cond_tipoequip_idx",
            ),
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

        # Mesma regra para o tipo: a lista de um condomínio não classifica o
        # equipamento de outro.
        if self.tipo_equipamento_id and self.condominio_id:
            tipo_equipamento = self.tipo_equipamento
            if (
                tipo_equipamento is not None
                and tipo_equipamento.condominio_id != self.condominio_id
            ):
                erros["tipo_equipamento"] = "O tipo escolhido não é deste condomínio."

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

    def indicados_para_moradores(self):
        """Os fornecedores que podem ser mostrados aos moradores agora.

        Três condições, todas necessárias: o fornecedor está em uso, a gestão o
        indicou e a gestão o liberou para os moradores. Tirar qualquer uma delas
        já muda a resposta na leitura seguinte — nada disso é guardado.
        """
        return self.filter(
            ativo=True, indicado_para_moradores=True, ativo_para_moradores=True
        )

    def indicacao_sem_revalidacao(self, *, janela_dias, referencia=None):
        """Indicações publicadas cuja validação passou da janela de revalidação.

        A janela é parâmetro, e não regra fixa: o intervalo defensável depende do
        que foi indicado, e quem pergunta é que sabe qual usar. Uma indicação sem
        validação registrada também entra — não há como dizer que está em dia.
        """
        momento = referencia or timezone.now()
        return self.indicados_para_moradores().filter(
            Q(validado_em__isnull=True)
            | Q(validado_em__lt=momento - timedelta(days=janela_dias))
        )


class Fornecedor(models.Model):
    """Quem atende o condomínio: a empresa do elevador, o eletricista, a dedetizadora.

    O documento identifica o fornecedor dentro do condomínio: dois cadastros com
    o mesmo CPF ou CNPJ são o mesmo fornecedor. Sem documento informado, o
    cadastro continua válido — e vários podem existir sem documento.

    Há um segundo uso, menor, além do trabalho do condomínio: a gestão pode
    indicar um fornecedor aos moradores. O que o morador vê é escrito em campos
    próprios — descrição, telefone, WhatsApp, site e observação —, nunca lido
    dos campos internos. O telefone da portaria não é o telefone que o morador
    pode chamar, e copiar um no outro seria decidir pelo condomínio o que fica
    visível.
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

    # -- Disponibilização para moradores ------------------------------------
    # Indicar e liberar são duas decisões, e não uma: a indicação diz que a
    # gestão confia no fornecedor; a liberação diz que ele pode ser mostrado
    # agora. Quem libera assina — e é essa assinatura que permite perguntar,
    # mais tarde, há quanto tempo a indicação não é revista.
    indicado_para_moradores = models.BooleanField(
        "Indicar este fornecedor aos moradores",
        default=False,
        help_text="A indicação sozinha não mostra nada aos moradores.",
    )
    ativo_para_moradores = models.BooleanField(
        "Disponível para moradores",
        default=False,
        help_text="Só fica disponível o fornecedor indicado e em uso no condomínio.",
    )
    descricao_publica = models.TextField(
        "Descrição para moradores",
        blank=True,
        default="",
        help_text="O que o morador precisa saber para procurar este fornecedor.",
    )
    telefone_publico = models.CharField(
        "Telefone para moradores",
        max_length=20,
        blank=True,
        default="",
        help_text="Guardado só com os dígitos. Vazio, nenhum outro telefone é mostrado no lugar.",
    )
    whatsapp_publico = models.CharField(
        "WhatsApp para moradores",
        max_length=20,
        blank=True,
        default="",
        help_text="Guardado só com os dígitos.",
    )
    site_publico = models.URLField(
        "Site",
        blank=True,
        default="",
        help_text="Endereço completo, começando por https://",
    )
    observacao_publica = models.TextField(
        "Observação para moradores",
        blank=True,
        default="",
        help_text="Horário de atendimento, como o serviço funciona, o que o morador precisa levar.",
    )
    validado_em = models.DateTimeField(
        "Validado em",
        null=True,
        blank=True,
        help_text="Quando a liberação aos moradores foi decidida. Preenchido pelo sistema.",
    )
    validado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="fornecedores_indicados",
        verbose_name="Validado por",
        help_text="Quem liberou o fornecedor aos moradores. Preservado para rastreabilidade.",
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
        self.telefone_publico = somente_digitos(self.telefone_publico)
        self.whatsapp_publico = somente_digitos(self.whatsapp_publico)
        self.site_publico = (self.site_publico or "").strip()
        self.descricao_publica = _texto_longo(self.descricao_publica)
        self.observacao_publica = _texto_longo(self.observacao_publica)
        super().save(*args, **kwargs)

    def clean(self):
        """Recusa um documento que não corresponde ao tipo informado.

        A conferência é estrutural: a quantidade de dígitos. O Eithra não
        consulta cadastro externo nem calcula dígito verificador — quem digita o
        documento responde por ele.

        A mesma conferência recusa o inverso da indicação: liberar aos moradores
        um fornecedor que a gestão não indicou seria mostrar sem ter decidido.
        """
        erros = {}
        if not _texto(self.nome_razao_social):
            erros["nome_razao_social"] = "Diga o nome do fornecedor."

        if self.ativo_para_moradores and not self.indicado_para_moradores:
            erros["ativo_para_moradores"] = (
                "Para o fornecedor ficar disponível aos moradores, indique-o primeiro."
            )

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

    @property
    def disponivel_para_moradores(self) -> bool:
        """Se este fornecedor pode aparecer aos moradores agora.

        A mesma pergunta que ``indicados_para_moradores()`` responde em conjunto,
        lida a partir do próprio registro: em uso, indicado e liberado. Tirar o
        fornecedor de uso ou retirar a indicação já muda a resposta na leitura
        seguinte — a lista não decide pelo morador.
        """
        return bool(self.ativo and self.indicado_para_moradores and self.ativo_para_moradores)


class FornecedorEspecialidade(models.Model):
    """O que um fornecedor atende: elétrica, hidráulica, elevadores.

    Vínculo explícito, e não um campo de texto no fornecedor: o mesmo
    fornecedor atende mais de uma especialidade, e a mesma especialidade é
    atendida por vários. Com isso, "quem atende hidráulica?" tem resposta sem
    depender de alguém ter escrito isso no nome.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    fornecedor = models.ForeignKey(
        Fornecedor,
        on_delete=models.CASCADE,
        related_name="especialidades",
        verbose_name="Fornecedor",
    )
    especialidade = models.ForeignKey(
        EspecialidadeFornecedor,
        on_delete=models.PROTECT,
        related_name="fornecedores",
        verbose_name="Especialidade",
    )
    observacao = models.CharField(
        "Observação",
        max_length=120,
        blank=True,
        default="",
        help_text="O que varia neste fornecedor para esta especialidade.",
    )
    criado_em = models.DateTimeField("Criado em", auto_now_add=True)

    class Meta:
        verbose_name = "Especialidade do fornecedor"
        verbose_name_plural = "Especialidades dos fornecedores"
        ordering = ["especialidade__nome"]
        constraints = [
            # O mesmo par não se repete: marcar duas vezes não acrescenta nada.
            models.UniqueConstraint(
                fields=["fornecedor", "especialidade"],
                name="fornecedor_especialidade_unica",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.fornecedor} · {self.especialidade}"

    def save(self, *args, **kwargs):
        """Guarda o vínculo já normalizado, venha ele do formulário ou não."""
        self.observacao = _texto(self.observacao)
        super().save(*args, **kwargs)

    def clean(self):
        """Recusa um vínculo entre condomínios diferentes."""
        erros = {}
        if self.fornecedor_id and self.especialidade_id:
            fornecedor = self.fornecedor
            especialidade = self.especialidade
            if (
                fornecedor is not None
                and especialidade is not None
                and fornecedor.condominio_id != especialidade.condominio_id
            ):
                erros["especialidade"] = (
                    "A especialidade escolhida não é deste condomínio."
                )
        if erros:
            raise ValidationError(erros)
