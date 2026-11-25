"""Modelos da Portaria: quem está esperado e o que de fato aconteceu."""
import hashlib
import re
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.cadastros.models import LocalOperacional
from apps.comunidade.models import Pessoa, Unidade, Veiculo
from apps.condominios.models import Condominio, somente_digitos

from .constants import (
    EventoAcessoPortaria,
    InstrucaoEntrega,
    MeioAutorizacaoPortaria,
    NaturezaObjetoCorreios,
    OrigemPortaria,
    StatusAutorizacaoPortaria,
    TipoAcessoPortaria,
)


def _texto(valor: str | None) -> str:
    return " ".join((valor or "").split())


def _texto_longo(valor: str | None) -> str:
    return (valor or "").strip()


def _placa(valor: str | None) -> str:
    return re.sub(r"[^0-9A-Za-z]", "", valor or "").upper()


def _nome_da_pessoa(pessoa) -> str:
    if pessoa is None or not getattr(pessoa, "pk", None):
        return ""
    return str(pessoa)


def _nome_do_veiculo(veiculo, placa="") -> str:
    if veiculo is not None and getattr(veiculo, "pk", None):
        return veiculo.placa
    return _placa(placa)


class AutorizacaoPortariaQuerySet(models.QuerySet):
    def do_condominio(self, condominio):
        if condominio is None or not getattr(condominio, "pk", None):
            return self.none()
        return self.filter(condominio_id=condominio.pk)

    def autorizadas(self):
        return self.filter(status=StatusAutorizacaoPortaria.AUTORIZADO)

    def esperadas_agora(self, agora=None):
        """Quem a portaria ainda aguarda receber neste momento.

        Uma autorização é uma visita planejada, e ela se cumpre de uma vez. A
        partir do instante em que gera entrada, já não se espera a chegada dela;
        e depois que essa entrada se encerra, ela é história — não volta a pedir
        passagem enquanto a janela estiver aberta. Visita nova é autorização
        nova, e essa aparece normalmente.

        O que decide isso é o próprio histórico de acessos: existe acesso para
        esta autorização, então a visita já aconteceu.
        """
        referencia = agora or timezone.localtime()
        return (
            self.autorizadas()
            .filter(inicio_previsto__lte=referencia, fim_previsto__gte=referencia)
            .exclude(acessos__id__isnull=False)
        )

    def buscar(self, termo):
        termo = (termo or "").strip()
        if not termo:
            return self
        digitos = somente_digitos(termo)
        consulta = (
            Q(nome__icontains=termo)
            | Q(empresa__icontains=termo)
            | Q(motivo__icontains=termo)
            | Q(observacao__icontains=termo)
            | Q(placa__icontains=termo.upper())
            | Q(pessoa__nome__icontains=termo)
            | Q(pessoa__nome_social__icontains=termo)
            | Q(unidade__identificador__icontains=termo)
            | Q(local__nome__icontains=termo)
            | Q(veiculo__placa__icontains=termo.upper())
        )
        if digitos:
            consulta |= (
                Q(telefone__icontains=digitos)
                | Q(documento__icontains=digitos)
            )
        return self.filter(consulta)


class AutorizacaoPortaria(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    condominio = models.ForeignKey(
        Condominio,
        on_delete=models.PROTECT,
        related_name="autorizacoes_portaria",
        verbose_name="Condomínio",
    )
    tipo_acesso = models.CharField(
        "Tipo",
        max_length=30,
        choices=TipoAcessoPortaria.choices,
        default=TipoAcessoPortaria.VISITANTE,
    )
    status = models.CharField(
        "Situação",
        max_length=24,
        choices=StatusAutorizacaoPortaria.choices,
        default=StatusAutorizacaoPortaria.AUTORIZADO,
    )
    origem = models.CharField(
        "Origem",
        max_length=30,
        choices=OrigemPortaria.choices,
        default=OrigemPortaria.PORTARIA_MANUAL,
    )
    pessoa = models.ForeignKey(
        Pessoa,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="autorizacoes_portaria",
        verbose_name="Pessoa conhecida",
    )
    unidade = models.ForeignKey(
        Unidade,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="autorizacoes_portaria",
        verbose_name="Unidade",
    )
    local = models.ForeignKey(
        LocalOperacional,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="autorizacoes_portaria",
        verbose_name="Local",
    )
    veiculo = models.ForeignKey(
        Veiculo,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="autorizacoes_portaria",
        verbose_name="Veículo conhecido",
    )
    autorizador_pessoa = models.ForeignKey(
        Pessoa,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="autorizacoes_que_autorizou_na_portaria",
        verbose_name="Autorizado por",
    )
    nome = models.CharField("Nome", max_length=180, blank=True, default="")
    autorizador_nome = models.CharField(
        "Nome de quem autorizou", max_length=180, blank=True, default=""
    )
    meio_autorizacao = models.CharField(
        "Meio de autorização",
        max_length=20,
        choices=MeioAutorizacaoPortaria.choices,
        blank=True,
        default="",
    )
    telefone = models.CharField("Telefone", max_length=20, blank=True, default="")
    documento = models.CharField("Documento", max_length=30, blank=True, default="")
    empresa = models.CharField("Empresa", max_length=180, blank=True, default="")
    placa = models.CharField("Placa", max_length=10, blank=True, default="")
    motivo = models.CharField("Motivo", max_length=180, blank=True, default="")
    observacao = models.TextField("Observação", blank=True, default="")
    natureza_objeto = models.CharField(
        "Natureza do objeto",
        max_length=20,
        choices=NaturezaObjetoCorreios.choices,
        blank=True,
        default="",
        help_text="Usado nas entregas dos Correios, quando souber.",
    )
    instrucao_entrega = models.CharField(
        "Instrução de entrega",
        max_length=24,
        choices=InstrucaoEntrega.choices,
        blank=True,
        default="",
        help_text="Como a entrega deve ser tratada no destino.",
    )
    inicio_previsto = models.DateTimeField("Previsto para")
    fim_previsto = models.DateTimeField("Até quando")
    registrado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="autorizacoes_registradas_na_portaria",
        verbose_name="Registrado por",
    )
    resultado_registrado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="resultados_registrados_na_portaria",
        verbose_name="Resultado registrado por",
        null=True,
        blank=True,
    )
    resultado_registrado_em = models.DateTimeField(
        "Resultado registrado em", null=True, blank=True
    )
    criado_em = models.DateTimeField("Criado em", auto_now_add=True)
    atualizado_em = models.DateTimeField("Atualizado em", auto_now=True)

    objects = AutorizacaoPortariaQuerySet.as_manager()

    class Meta:
        verbose_name = "Autorização de portaria"
        verbose_name_plural = "Autorizações de portaria"
        ordering = ["inicio_previsto", "nome"]
        indexes = [
            models.Index(
                fields=["condominio", "status", "inicio_previsto", "fim_previsto"],
                name="portaria_autoriza_janela_idx",
            )
        ]

    def __str__(self):
        return f"{self.nome_para_exibir} · {self.destino_para_exibir}"

    def save(self, *args, **kwargs):
        self.nome = _texto(self.nome)
        self.autorizador_nome = _texto(self.autorizador_nome)
        self.telefone = somente_digitos(self.telefone)
        self.documento = _texto(self.documento).upper()
        self.empresa = _texto(self.empresa)
        self.placa = _placa(self.placa)
        self.motivo = _texto(self.motivo)
        self.observacao = _texto_longo(self.observacao)
        super().save(*args, **kwargs)

    def clean(self):
        erros = {}
        if not self.pessoa_id and not _texto(self.nome):
            erros["nome"] = "Informe quem está chegando."
        if not self.unidade_id and not self.local_id:
            erros["unidade"] = "Informe a unidade ou o local de destino."
        if self.inicio_previsto and self.fim_previsto and self.fim_previsto < self.inicio_previsto:
            erros["fim_previsto"] = "O fim previsto não pode ser antes do início."
        for campo in ("pessoa", "unidade", "local", "veiculo", "autorizador_pessoa"):
            alvo = getattr(self, campo, None)
            if alvo is not None and getattr(alvo, "condominio_id", None) != self.condominio_id:
                erros[campo] = "O registro escolhido não é deste condomínio."
        if erros:
            raise ValidationError(erros)

    @property
    def nome_para_exibir(self) -> str:
        return _nome_da_pessoa(self.pessoa) or self.nome or "Sem identificação"

    @property
    def placa_para_exibir(self) -> str:
        return _nome_do_veiculo(self.veiculo, self.placa)

    @property
    def destino_para_exibir(self) -> str:
        if self.unidade_id:
            return self.unidade.rotulo
        if self.local_id:
            return self.local.nome
        return "Destino não informado"

    @property
    def esta_expirada(self) -> bool:
        return self.fim_previsto < timezone.localtime()

    @property
    def situacao_para_exibir(self) -> str:
        if self.status == StatusAutorizacaoPortaria.AGUARDANDO:
            return "Aguardando confirmação"
        if self.status == StatusAutorizacaoPortaria.NAO_AUTORIZADO:
            return "Não autorizado"
        if self.status == StatusAutorizacaoPortaria.SEM_RESPOSTA:
            return "Sem resposta"
        return "Autorizado"

    @property
    def autorizador_para_exibir(self) -> str:
        return _nome_da_pessoa(self.autorizador_pessoa) or self.autorizador_nome


class AcessoPortariaQuerySet(models.QuerySet):
    def do_condominio(self, condominio):
        if condominio is None or not getattr(condominio, "pk", None):
            return self.none()
        return self.filter(condominio_id=condominio.pk)

    def abertas(self):
        return self.filter(saida_em__isnull=True)

    def entradas_recentes(self):
        return self.order_by("-entrada_em")

    def saidas_recentes(self):
        return self.exclude(saida_em__isnull=True).order_by("-saida_em")

    def buscar(self, termo):
        termo = (termo or "").strip()
        if not termo:
            return self
        digitos = somente_digitos(termo)
        consulta = (
            Q(nome__icontains=termo)
            | Q(empresa__icontains=termo)
            | Q(motivo__icontains=termo)
            | Q(observacao__icontains=termo)
            | Q(placa__icontains=termo.upper())
            | Q(pessoa__nome__icontains=termo)
            | Q(pessoa__nome_social__icontains=termo)
            | Q(unidade__identificador__icontains=termo)
            | Q(local__nome__icontains=termo)
            | Q(veiculo__placa__icontains=termo.upper())
            | Q(autorizacao__nome__icontains=termo)
        )
        if digitos:
            consulta |= (
                Q(telefone__icontains=digitos)
                | Q(documento__icontains=digitos)
            )
        return self.filter(consulta)


class AcessoPortaria(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    condominio = models.ForeignKey(
        Condominio,
        on_delete=models.PROTECT,
        related_name="acessos_portaria",
        verbose_name="Condomínio",
    )
    autorizacao = models.ForeignKey(
        AutorizacaoPortaria,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="acessos",
        verbose_name="Autorização",
    )
    tipo_acesso = models.CharField(
        "Tipo",
        max_length=30,
        choices=TipoAcessoPortaria.choices,
        default=TipoAcessoPortaria.VISITANTE,
    )
    origem = models.CharField(
        "Origem",
        max_length=30,
        choices=OrigemPortaria.choices,
        default=OrigemPortaria.PORTARIA_MANUAL,
    )
    pessoa = models.ForeignKey(
        Pessoa,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="acessos_portaria",
        verbose_name="Pessoa conhecida",
    )
    unidade = models.ForeignKey(
        Unidade,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="acessos_portaria",
        verbose_name="Unidade",
    )
    local = models.ForeignKey(
        LocalOperacional,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="acessos_portaria",
        verbose_name="Local",
    )
    veiculo = models.ForeignKey(
        Veiculo,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="acessos_portaria",
        verbose_name="Veículo conhecido",
    )
    nome = models.CharField("Nome", max_length=180, blank=True, default="")
    telefone = models.CharField("Telefone", max_length=20, blank=True, default="")
    documento = models.CharField("Documento", max_length=30, blank=True, default="")
    empresa = models.CharField("Empresa", max_length=180, blank=True, default="")
    placa = models.CharField("Placa", max_length=10, blank=True, default="")
    motivo = models.CharField("Motivo", max_length=180, blank=True, default="")
    observacao = models.TextField("Observação", blank=True, default="")
    natureza_objeto = models.CharField(
        "Natureza do objeto",
        max_length=20,
        choices=NaturezaObjetoCorreios.choices,
        blank=True,
        default="",
        help_text="Usado nas entregas dos Correios, quando souber.",
    )
    instrucao_entrega = models.CharField(
        "Instrução de entrega",
        max_length=24,
        choices=InstrucaoEntrega.choices,
        blank=True,
        default="",
        help_text="Como a entrega deve ser tratada no destino.",
    )
    # Referência da foto da chegada no armazenamento privado. É evidência
    # opcional: sem foto, todos estes campos ficam vazios. Guarda-se a
    # referência do objeto — nunca URL pública nem o arquivo em disco local.
    foto_storage_provider = models.CharField(
        "Provedor da foto", max_length=50, blank=True, default=""
    )
    foto_storage_bucket = models.CharField(
        "Local da foto", max_length=150, blank=True, default=""
    )
    foto_storage_key = models.CharField(
        "Referência da foto", max_length=500, blank=True, default=""
    )
    foto_mime_type = models.CharField(
        "Tipo da foto", max_length=120, blank=True, default=""
    )
    foto_tamanho_bytes = models.PositiveBigIntegerField("Tamanho da foto", default=0)
    chave_presenca = models.CharField(
        "Chave da presença",
        max_length=64,
        editable=False,
        db_index=True,
    )
    entrada_em = models.DateTimeField("Entrada em", default=timezone.now)
    saida_em = models.DateTimeField("Saída em", null=True, blank=True)
    entrada_registrada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="entradas_registradas_na_portaria",
        verbose_name="Entrada registrada por",
    )
    saida_registrada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="saidas_registradas_na_portaria",
        verbose_name="Saída registrada por",
        null=True,
        blank=True,
    )
    criado_em = models.DateTimeField("Criado em", auto_now_add=True)
    atualizado_em = models.DateTimeField("Atualizado em", auto_now=True)

    objects = AcessoPortariaQuerySet.as_manager()

    class Meta:
        verbose_name = "Acesso de portaria"
        verbose_name_plural = "Acessos de portaria"
        ordering = ["-entrada_em"]
        constraints = [
            models.UniqueConstraint(
                fields=["condominio", "chave_presenca"],
                condition=Q(saida_em__isnull=True),
                name="portaria_presenca_aberta_sem_duplicidade",
            )
        ]
        indexes = [
            models.Index(
                fields=["condominio", "entrada_em"], name="portaria_entrada_idx"
            ),
            models.Index(fields=["condominio", "saida_em"], name="portaria_saida_idx"),
        ]

    def __str__(self):
        return f"{self.nome_para_exibir} · {self.destino_para_exibir}"

    def save(self, *args, **kwargs):
        self.nome = _texto(self.nome)
        self.telefone = somente_digitos(self.telefone)
        self.documento = _texto(self.documento).upper()
        self.empresa = _texto(self.empresa)
        self.placa = _placa(self.placa)
        self.motivo = _texto(self.motivo)
        self.observacao = _texto_longo(self.observacao)
        self.chave_presenca = self._gerar_chave_presenca()
        super().save(*args, **kwargs)

    def clean(self):
        erros = {}
        if not self.pessoa_id and not _texto(self.nome):
            erros["nome"] = "Informe quem chegou."
        if not self.unidade_id and not self.local_id:
            erros["unidade"] = "Informe a unidade ou o local de destino."
        if self.saida_em and self.saida_em < self.entrada_em:
            erros["saida_em"] = "A saída não pode ser antes da entrada."
        for campo in ("pessoa", "unidade", "local", "veiculo"):
            alvo = getattr(self, campo, None)
            if alvo is not None and getattr(alvo, "condominio_id", None) != self.condominio_id:
                erros[campo] = "O registro escolhido não é deste condomínio."
        if self.autorizacao_id and self.autorizacao.condominio_id != self.condominio_id:
            erros["autorizacao"] = "A autorização escolhida não é deste condomínio."
        if erros:
            raise ValidationError(erros)

    def _gerar_chave_presenca(self) -> str:
        base = "|".join(
            [
                str(self.condominio_id or ""),
                str(self.tipo_acesso or ""),
                str(self.pessoa_id or ""),
                str(self.unidade_id or ""),
                str(self.local_id or ""),
                str(self.veiculo_id or ""),
                _texto(self.nome).upper(),
                _texto(self.documento).upper(),
                _placa(self.placa),
            ]
        )
        return hashlib.sha256(base.encode("utf-8")).hexdigest()

    @property
    def nome_para_exibir(self) -> str:
        return _nome_da_pessoa(self.pessoa) or self.nome or "Sem identificação"

    @property
    def placa_para_exibir(self) -> str:
        return _nome_do_veiculo(self.veiculo, self.placa)

    @property
    def destino_para_exibir(self) -> str:
        if self.unidade_id:
            return self.unidade.rotulo
        if self.local_id:
            return self.local.nome
        return "Destino não informado"

    @property
    def esta_dentro(self) -> bool:
        return self.saida_em is None


class HistoricoAcessoPortaria(models.Model):
    """O que aconteceu com um acesso depois de registrado — e quem registrou.

    Linha acrescentada, nunca reescrita: a aplicação não atualiza nem apaga o
    que já foi anotado. Guarda o aviso de encomenda aberto por WhatsApp, para
    que "o morador foi avisado?" tenha resposta sem depender da memória de quem
    estava no turno.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    acesso = models.ForeignKey(
        AcessoPortaria,
        on_delete=models.PROTECT,
        related_name="historico",
        verbose_name="Acesso",
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="eventos_de_portaria",
        verbose_name="Registrado por",
        help_text="Quem provocou o evento. Preservado para rastreabilidade.",
    )
    evento = models.CharField(
        "Evento", max_length=24, choices=EventoAcessoPortaria.choices
    )
    descricao = models.CharField(
        "Descrição",
        max_length=255,
        help_text="O evento em linguagem humana, do jeito que deve ser lembrado.",
    )
    data_hora = models.DateTimeField("Quando", default=timezone.now)

    class Meta:
        verbose_name = "Evento do acesso"
        verbose_name_plural = "Histórico dos acessos"
        ordering = ["-data_hora"]
        indexes = [
            models.Index(fields=["acesso", "data_hora"], name="portaria_hist_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.get_evento_display()} · {self.acesso}"
