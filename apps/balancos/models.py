"""Modelos do domínio de balanços mensais.

O balanço mensal é o agregado canônico do documento financeiro que a
administração envia todo mês. Ele não é conta bancária, lançamento, boleto,
cobrança, fornecedor, nota fiscal, manutenção ou orçamento: é a leitura
estruturada e validável de um documento mensal.

O Eithra não é um ERP contábil. Ele interpreta o que a administração já produz e
transforma isso em compreensão, acompanhamento e transparência.

O arquivo original não é guardado no sistema de arquivos local: ele fica em
storage externo privado, e aqui se guarda apenas a referência ao objeto.
"""
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.balancos.constants import (
    LayoutBalanco,
    ProcessamentoBalanco,
    StatusBalanco,
    StatusImportacao,
    TipoVinculoEvidencia,
)
from apps.condominios.models import Condominio
from apps.core.storage import FORMATO_SHA256, erros_de_referencia_de_storage

# Tipos de imagem aceitos numa evidência visual. A lista é do domínio e vive
# aqui porque é o modelo que recusa o que não é imagem — a borda usa a mesma
# lista, para não existirem duas respostas para a mesma pergunta.
MIMES_DE_EVIDENCIA = ("image/jpeg", "image/png", "image/webp")


class BalancoMensal(models.Model):
    """Balanço de um mês de um condomínio, em uma versão.

    O registro representa o balanço já confirmado pela gestão: o resultado da
    leitura é apresentado, a gestão confirma a importação e só então o mês
    passa a valer. Não existe etapa formal de revisão.

    O mesmo mês pode ter mais de uma versão. Quando a administradora envia um
    documento corrigido, cria-se uma nova versão que substitui a anterior — e a
    anterior permanece, marcada como substituída. Nada é sobrescrito em
    silêncio.

    A competência é sempre o primeiro dia do mês de referência: ela representa
    o mês, não um dia financeiro específico.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    condominio = models.ForeignKey(
        Condominio,
        on_delete=models.PROTECT,
        related_name="balancos",
        verbose_name="condomínio",
    )
    competencia = models.DateField(
        "competência",
        help_text="Primeiro dia do mês de referência.",
    )
    versao = models.PositiveIntegerField("versão", default=1)
    substitui = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="versoes_corrigidas",
        verbose_name="substitui",
        help_text="Versão anterior deste mesmo mês, quando esta a corrige.",
    )
    status = models.CharField(
        "situação",
        max_length=20,
        choices=StatusBalanco.choices,
        default=StatusBalanco.VALIDADO,
    )
    status_processamento = models.CharField(
        "processamento",
        max_length=20,
        choices=ProcessamentoBalanco.choices,
        default=ProcessamentoBalanco.PENDENTE,
    )
    layout_detectado = models.CharField(
        "layout detectado",
        max_length=20,
        choices=LayoutBalanco.choices,
        default=LayoutBalanco.DESCONHECIDO,
    )
    schema_versao = models.PositiveIntegerField("versão do schema", default=1)

    # O que o sistema entendeu do documento, antes de qualquer confirmação
    # humana. Nunca é tratado como oficial.
    dados_extraidos = models.JSONField("dados extraídos", default=dict, blank=True)
    # O que a gestão confirmou ou corrigiu. É o estado oficial do mês.
    dados_validados = models.JSONField("dados validados", default=dict, blank=True)
    inconsistencias = models.JSONField("inconsistências", default=list, blank=True)
    # Apenas agregados úteis para listagem e comparação entre meses.
    resumo = models.JSONField("resumo", default=dict, blank=True)

    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="balancos_criados",
        verbose_name="criado por",
    )
    criado_em = models.DateTimeField("criado em", auto_now_add=True)
    atualizado_em = models.DateTimeField("atualizado em", auto_now=True)
    validado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="balancos_validados",
        verbose_name="validado por",
    )
    validado_em = models.DateTimeField("validado em", null=True, blank=True)

    class Meta:
        verbose_name = "balanço mensal"
        verbose_name_plural = "balanços mensais"
        ordering = ["-competencia", "-versao"]
        constraints = [
            # A mesma versão não existe duas vezes para o mesmo mês.
            models.UniqueConstraint(
                fields=["condominio", "competencia", "versao"],
                name="balanco_unico_por_competencia_e_versao",
            ),
            # Uma competência tem uma única versão em vigor: validada ou
            # publicada. As versões anteriores continuam guardadas, marcadas
            # como substituídas — por isso a unicidade vale apenas fora desse
            # estado. É assim que se sabe qual versão é a atual, sem precisar
            # de campo redundante.
            models.UniqueConstraint(
                fields=["condominio", "competencia"],
                condition=~Q(status=StatusBalanco.SUBSTITUIDO),
                name="balanco_em_vigor_unico_por_competencia",
            ),
            models.CheckConstraint(
                condition=Q(versao__gte=1),
                name="balanco_versao_minima",
            ),
            # A competência representa o mês inteiro, nunca um dia avulso.
            models.CheckConstraint(
                condition=Q(competencia__day=1),
                name="balanco_competencia_no_primeiro_dia",
            ),
        ]
        indexes = [
            models.Index(
                fields=["condominio", "status"],
                name="balanco_condominio_status_idx",
            ),
        ]

    def clean(self):
        super().clean()
        erros: dict[str, str] = {}

        if self.competencia and self.competencia.day != 1:
            erros["competencia"] = (
                "A competência representa o mês: use sempre o primeiro dia."
            )
        if self.versao is not None and self.versao < 1:
            erros["versao"] = "A versão começa em 1."

        if self.substitui_id:
            erros.update(self._erros_de_substituicao())

        if self.status in (StatusBalanco.VALIDADO, StatusBalanco.PUBLICADO):
            if not self.validado_em:
                erros["validado_em"] = (
                    "Um balanço validado registra quando foi validado."
                )
            if not self.validado_por_id:
                erros["validado_por"] = "Um balanço validado registra quem validou."

        if erros:
            raise ValidationError(erros)

    def _erros_de_substituicao(self) -> dict:
        """A substituição só vale dentro do mesmo mês do mesmo condomínio."""
        if self.pk and self.substitui_id == self.pk:
            return {"substitui": "Um balanço não substitui a si mesmo."}

        anterior = self.substitui
        erros: dict[str, str] = {}

        if self.condominio_id and anterior.condominio_id != self.condominio_id:
            erros["substitui"] = "A versão substituída pertence a outro condomínio."
        if self.competencia and anterior.competencia != self.competencia:
            erros["substitui"] = "A versão substituída é de outra competência."
        if (
            self.versao is not None
            and anterior.versao is not None
            and self.versao <= anterior.versao
        ):
            erros["versao"] = "A versão nova precisa ser maior que a substituída."

        return erros

    def __str__(self) -> str:
        if not self.competencia:
            return "Balanço sem competência"
        return f"Balanço {self.competencia:%m/%Y} v{self.versao} · {self.get_status_display()}"


class DocumentoBalanco(models.Model):
    """Metadados do arquivo original de um balanço.

    O arquivo em si vive em storage externo privado. Aqui ficam a referência do
    objeto e o que ajuda a reconhecê-lo: nome, tipo, tamanho e hash.

    Nunca se guarda URL pública, URL assinada, token ou senha. A URL temporária
    é gerada sob demanda, quando alguém autorizado precisa do arquivo — nunca
    fica persistida.

    O versionamento é do balanço, não do documento: uma versão corrigida é um
    novo balanço, com o seu próprio documento.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    balanco = models.ForeignKey(
        BalancoMensal,
        on_delete=models.PROTECT,
        related_name="documentos",
        verbose_name="balanço",
    )
    nome_original = models.CharField("nome original", max_length=255)
    storage_provider = models.CharField(
        "provedor", max_length=50, blank=True, default=""
    )
    storage_bucket = models.CharField(
        "local de armazenamento", max_length=150, blank=True, default=""
    )
    storage_key = models.CharField("referência do objeto", max_length=500)
    mime_type = models.CharField(
        "tipo do arquivo", max_length=120, blank=True, default=""
    )
    tamanho_bytes = models.PositiveBigIntegerField("tamanho", default=0)
    sha256 = models.CharField(
        "hash do arquivo",
        max_length=64,
        validators=[FORMATO_SHA256],
        help_text="Necessário para reconhecer o mesmo arquivo mais tarde.",
    )
    enviado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="documentos_balanco_enviados",
        verbose_name="enviado por",
    )
    enviado_em = models.DateTimeField("enviado em", default=timezone.now)
    ativo = models.BooleanField("ativo", default=True)

    class Meta:
        verbose_name = "documento de balanço"
        verbose_name_plural = "documentos de balanço"
        ordering = ["-enviado_em"]
        constraints = [
            # O mesmo arquivo não entra duas vezes no mesmo balanço. Não se
            # impõe unicidade global do hash: o mesmo PDF pode existir
            # legitimamente em condomínios distintos.
            models.UniqueConstraint(
                fields=["balanco", "sha256"],
                name="documento_unico_por_balanco_e_hash",
            ),
            models.CheckConstraint(
                condition=Q(tamanho_bytes__gte=0),
                name="documento_tamanho_nao_negativo",
            ),
        ]
        indexes = [
            # Apenas apoio à procura por arquivo repetido. A decisão de tratar
            # como duplicidade é da gestão, não do banco.
            models.Index(fields=["sha256"], name="documento_sha256_idx"),
        ]

    def clean(self):
        super().clean()
        erros: dict[str, str] = {}

        erros.update(erros_de_referencia_de_storage(self))

        if self.tamanho_bytes is not None and self.tamanho_bytes < 0:
            erros["tamanho_bytes"] = "O tamanho do arquivo não pode ser negativo."

        if erros:
            raise ValidationError(erros)

    def __str__(self) -> str:
        return self.nome_original or str(self.pk)


class EvidenciaVisualBalanco(models.Model):
    """Uma foto que explica algo do mês, presa a um contexto do balanço.

    A evidência é complementar e nunca substitui lançamento, documento, nota
    fiscal ou justificativa: ela existe para mostrar, não para provar. Por isso
    o vínculo é obrigatório — uma foto solta não explica despesa nenhuma — e por
    isso a legenda é curta: é uma frase sobre a imagem, não um relatório.

    O arquivo vive no mesmo bucket privado dos documentos, e aqui fica apenas a
    referência do objeto. Nunca se guarda URL pública nem assinada: o acesso é
    gerado sob demanda, como no documento original.

    ``exibir_aos_moradores`` não publica nada. Ele apenas marca que a gestão
    assume a imagem como elegível para uma transparência futura — a publicação
    para o morador será uma fase própria, com decisão própria.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    balanco = models.ForeignKey(
        BalancoMensal,
        on_delete=models.PROTECT,
        related_name="evidencias",
        verbose_name="balanço",
    )
    tipo_vinculo = models.CharField(
        "vínculo",
        max_length=20,
        choices=TipoVinculoEvidencia.choices,
    )
    referencia = models.CharField(
        "referência do vínculo",
        max_length=64,
        blank=True,
        default="",
        help_text=(
            "Identificador do lançamento, código da categoria, ou vazio no "
            "destaque do mês."
        ),
    )

    # Referência do objeto no storage privado. Nunca a URL em si.
    storage_provider = models.CharField("provedor", max_length=50, blank=True, default="")
    storage_bucket = models.CharField(
        "local de armazenamento", max_length=150, blank=True, default=""
    )
    storage_key = models.CharField("referência do objeto", max_length=500)
    mime_type = models.CharField("tipo da imagem", max_length=120)
    tamanho_bytes = models.PositiveBigIntegerField("tamanho", default=0)
    sha256 = models.CharField(
        "hash do arquivo",
        max_length=64,
        validators=[FORMATO_SHA256],
        help_text="Reconhece a mesma imagem repetida no mesmo vínculo.",
    )

    legenda = models.CharField("legenda", max_length=200, blank=True, default="")
    exibir_aos_moradores = models.BooleanField(
        "elegível para os moradores", default=False
    )

    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="evidencias_balanco_criadas",
        verbose_name="criado por",
    )
    criado_em = models.DateTimeField("criado em", auto_now_add=True)
    atualizado_em = models.DateTimeField("atualizado em", auto_now=True)

    # Remover uma evidência é desativá-la, não apagá-la: o registro permanece
    # para que o histórico do mês não perca a explicação que já existiu.
    ativo = models.BooleanField("ativo", default=True)
    removido_em = models.DateTimeField("removido em", null=True, blank=True)

    class Meta:
        verbose_name = "evidência visual de balanço"
        verbose_name_plural = "evidências visuais de balanço"
        ordering = ["criado_em"]
        constraints = [
            # A mesma imagem não entra duas vezes no mesmo vínculo. A unicidade
            # vale apenas entre as ativas: uma evidência removida não impede
            # que a gestão anexe de novo a mesma foto e mude de ideia.
            models.UniqueConstraint(
                fields=["balanco", "tipo_vinculo", "referencia", "sha256"],
                condition=Q(ativo=True),
                name="evidencia_unica_por_vinculo_e_hash",
            ),
            models.CheckConstraint(
                condition=Q(tamanho_bytes__gte=0),
                name="evidencia_tamanho_nao_negativo",
            ),
            # Só imagem entra aqui. PDF, vídeo e executável são recusados no
            # banco, e não apenas na validação da borda.
            models.CheckConstraint(
                condition=Q(mime_type__in=MIMES_DE_EVIDENCIA),
                name="evidencia_mime_permitido",
            ),
            # Uma evidência inativa é uma evidência removida: sem a data, não
            # se saberia quando ela saiu do mês.
            models.CheckConstraint(
                condition=~Q(ativo=False, removido_em__isnull=True),
                name="evidencia_removida_registra_quando",
            ),
        ]
        indexes = [
            # Apoio à listagem de um vínculo — e à contagem do limite de fotos.
            models.Index(
                fields=["balanco", "tipo_vinculo", "referencia"],
                name="evidencia_vinculo_idx",
            ),
            # Apoio à futura consulta de transparência: só ativas e elegíveis.
            models.Index(
                fields=["balanco", "ativo", "exibir_aos_moradores"],
                name="evidencia_visivel_idx",
            ),
        ]

    def clean(self):
        super().clean()
        erros: dict[str, str] = {}

        erros.update(erros_de_referencia_de_storage(self))

        if self.tamanho_bytes is not None and self.tamanho_bytes < 0:
            erros["tamanho_bytes"] = "O tamanho da imagem não pode ser negativo."

        if self.tipo_vinculo == TipoVinculoEvidencia.DESTAQUE and self.referencia:
            erros["referencia"] = (
                "O destaque do mês não é um lançamento: não há referência a guardar."
            )
        if self.tipo_vinculo != TipoVinculoEvidencia.DESTAQUE and not self.referencia:
            erros["referencia"] = "Escolha a que esta foto está ligada."

        if erros:
            raise ValidationError(erros)

    def __str__(self) -> str:
        return self.legenda or f"Evidência {self.get_tipo_vinculo_display()} · {self.pk}"


# Tom visual de cada estado na listagem. O texto é que informa o que aconteceu;
# a cor apenas ajuda a ler a lista de longe.
TOM_POR_STATUS_DE_IMPORTACAO = {
    StatusImportacao.RECEBIDO: "info",
    StatusImportacao.PROCESSANDO: "info",
    StatusImportacao.PRONTO: "primary",
    StatusImportacao.ERRO: "danger",
    StatusImportacao.CONFIRMADO: "success",
    StatusImportacao.DESCARTADO: "secondary",
}


class ImportacaoBalanco(models.Model):
    """Tentativa de importação de um balanço, ainda não confirmada.

    Este registro representa um arquivo recebido — não um balanço. Enquanto
    existe só aqui, nada do mês mudou: nenhum valor passa a valer, nenhuma
    competência é criada. O ``BalancoMensal`` só nasce quando a gestão confirma
    a importação.

    É por isso que upload, processamento e resultado preliminar não são
    balanço. Confundir os dois faria o sistema aceitar como oficial um arquivo
    que ninguém conferiu.

    A importação é transitória: pode expirar ou ser descartada. Quando
    descartada, o registro permanece, com a data do descarte, para não apagar
    histórico técnico em silêncio.

    O arquivo vive em storage externo privado. Aqui ficam apenas a referência
    do objeto e os metadados que ajudam a reconhecê-lo — nunca URL pública,
    URL assinada, token ou segredo.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    condominio = models.ForeignKey(
        Condominio,
        on_delete=models.PROTECT,
        related_name="importacoes_balanco",
        verbose_name="condomínio",
    )
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="importacoes_balanco_criadas",
        verbose_name="recebido por",
    )
    criado_em = models.DateTimeField("recebido em", auto_now_add=True)
    atualizado_em = models.DateTimeField("atualizado em", auto_now=True)

    status = models.CharField(
        "situação da importação",
        max_length=20,
        choices=StatusImportacao.choices,
        default=StatusImportacao.RECEBIDO,
    )

    # Identificação do arquivo, como ele chegou.
    nome_original = models.CharField("nome do arquivo", max_length=255)
    mime_type = models.CharField(
        "tipo do arquivo", max_length=120, blank=True, default=""
    )
    tamanho_bytes = models.PositiveBigIntegerField("tamanho", default=0)
    sha256 = models.CharField(
        "hash do arquivo",
        max_length=64,
        validators=[FORMATO_SHA256],
        help_text="Reconhece o mesmo arquivo para o mesmo condomínio.",
    )

    # Referência do objeto no storage privado. Nunca a URL em si.
    storage_provider = models.CharField(
        "provedor", max_length=50, blank=True, default=""
    )
    storage_bucket = models.CharField(
        "local de armazenamento", max_length=150, blank=True, default=""
    )
    storage_key = models.CharField(
        "referência do objeto", max_length=500, blank=True, default=""
    )

    # O layout já pode ser detectado na entrada. Mesmo assim, não se antecipa
    # leitura financeira: resultado continua vazio e sem inconsistências.
    schema_versao = models.PositiveIntegerField("versão do schema", default=1)
    layout_detectado = models.CharField(
        "layout detectado",
        max_length=20,
        choices=LayoutBalanco.choices,
        default=LayoutBalanco.DESCONHECIDO,
    )
    resultado_extraido = models.JSONField("resultado da leitura", default=dict, blank=True)
    inconsistencias = models.JSONField("inconsistências", default=list, blank=True)

    # Texto humano e curto sobre a falha de recebimento. Nunca guarda segredo,
    # URL assinada ou resposta bruta do provedor.
    erro_processamento = models.TextField("erro no recebimento", blank=True, default="")

    expira_em = models.DateTimeField("expira em", null=True, blank=True)
    confirmado_em = models.DateTimeField("confirmado em", null=True, blank=True)
    descartado_em = models.DateTimeField("descartado em", null=True, blank=True)

    class Meta:
        verbose_name = "importação de balanço"
        verbose_name_plural = "importações de balanço"
        ordering = ["-criado_em"]
        constraints = [
            models.CheckConstraint(
                condition=Q(tamanho_bytes__gte=0),
                name="importacao_tamanho_nao_negativo",
            ),
            # O descarte e a confirmação são exclusivos: uma importação não tem
            # os dois desfechos ao mesmo tempo.
            models.CheckConstraint(
                condition=~Q(confirmado_em__isnull=False, descartado_em__isnull=False),
                name="importacao_um_unico_desfecho",
            ),
        ]
        indexes = [
            # Apoio à detecção de duplicidade por condomínio e arquivo. Não há
            # unicidade: o mesmo PDF pode voltar legitimamente, e a decisão de
            # tratar como repetição é da gestão, não do banco.
            models.Index(
                fields=["condominio", "sha256"],
                name="importacao_condominio_sha_idx",
            ),
            models.Index(
                fields=["condominio", "status"],
                name="importacao_cond_status_idx",
            ),
        ]

    def clean(self):
        super().clean()
        erros: dict[str, str] = {}

        erros.update(erros_de_referencia_de_storage(self))

        if self.tamanho_bytes is not None and self.tamanho_bytes < 0:
            erros["tamanho_bytes"] = "O tamanho do arquivo não pode ser negativo."

        if self.confirmado_em and self.descartado_em:
            erros["descartado_em"] = (
                "Uma importação não é confirmada e descartada ao mesmo tempo."
            )

        if erros:
            raise ValidationError(erros)

    @property
    def descartada(self) -> bool:
        return self.status == StatusImportacao.DESCARTADO

    @property
    def tom_do_estado(self) -> str:
        """Tom visual do estado, para a listagem. Nunca a única informação."""
        return TOM_POR_STATUS_DE_IMPORTACAO.get(self.status, "secondary")

    def _situacao_e_tom(self) -> tuple[str, str]:
        """O que aconteceu com a importação, dito a quem está lendo a lista.

        O estado técnico responde "em que passo o arquivo está"; a gestão
        precisa saber outra coisa: se já existe leitura para conferir, se ainda
        não há o que ler, ou se o mês já passou a valer. Por isso a frase vem do
        que a importação tem — não só do passo em que ela parou.
        """
        if self.confirmado_em:
            return "Balanço confirmado", "success"
        if self.descartada:
            return self.get_status_display(), "secondary"
        if self.status in (StatusImportacao.RECEBIDO, StatusImportacao.PROCESSANDO):
            return self.get_status_display(), self.tom_do_estado
        if self.status == StatusImportacao.ERRO:
            return self.get_status_display(), "danger"
        if (
            self.status == StatusImportacao.PRONTO
            and self.layout_detectado == LayoutBalanco.ATUAL_CANONICO
            and self.resultado_extraido
        ):
            return "Aguardando confirmação", "primary"
        if self.resultado_extraido:
            return self.get_status_display(), self.tom_do_estado
        return "Dados ainda não lidos", "secondary"

    @property
    def situacao_para_a_gestao(self) -> str:
        """A situação da importação em linguagem de gestão, não de pipeline."""
        return self._situacao_e_tom()[0]

    @property
    def tom_da_situacao(self) -> str:
        """Tom visual da situação. O texto é que informa; a cor só ajuda."""
        return self._situacao_e_tom()[1]

    def __str__(self) -> str:
        return f"{self.nome_original} · {self.get_status_display()}"
