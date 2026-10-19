"""Modelos do domínio de estoque e compras operacionais.

O que existe aqui responde a uma pergunta simples: o que o condomínio comprou,
o que tem guardado e para onde foi o que saiu. A ideia que governa os quatro
modelos é uma só — **o saldo não é digitado, o saldo é consequência**. Não há
campo de saldo em lugar nenhum: o que há é o registro do que entrou e do que
saiu, e o saldo é a leitura desse conjunto. Assim qualquer número na tela pode
ser explicado pelas movimentações que o produziram.

Disso decorrem três decisões. A quantidade é sempre positiva, e o sentido do
movimento vem do tipo — nunca do sinal. Cadastrar um item não cria estoque:
para registrar o que já existe, usa-se um ajuste de entrada, com motivo. E
comprar não é o mesmo que consumir: a compra é o fato econômico, e os
movimentos são o efeito físico, cada um registrado com quem o provocou e quando.

Nada aqui gera despesa, altera balanço ou toca em lançamento financeiro. Os
valores são operacionais: dizem quanto se pagou, não substituem a contabilidade.
"""
import uuid

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.db.models.functions import Coalesce, Lower

from apps.cadastros.models import Fornecedor, LocalOperacional
from apps.condominios.models import Condominio
from apps.manutencoes.models import Manutencao

from .constants import (
    TIPOS_DE_ENTRADA,
    TIPOS_DE_SAIDA,
    TIPOS_QUE_EXIGEM_MOTIVO,
    DestinoDoItem,
    StatusCompra,
    TipoMovimentacaoEstoque,
    UnidadeMedida,
    direcao_do_tipo,
    situacao_do_saldo,
)

# A quantidade é sempre positiva e admite frações: 2,5 litros, 3,2 metros.
QUANTIDADE = models.DecimalField(max_digits=14, decimal_places=3)

# Dinheiro em reais, com dois centavos.
DINHEIRO = models.DecimalField(max_digits=12, decimal_places=2)

ZERO = Decimal("0")


def _texto(valor: str | None) -> str:
    """Colapsa espaços repetidos e devolve string vazia quando não há conteúdo."""
    return " ".join((valor or "").split())


def _texto_longo(valor: str | None) -> str:
    """Preserva os parágrafos de um texto livre, sem espaços nas pontas."""
    return (valor or "").strip()


def _dinheiro(valor) -> Decimal:
    """O valor como dinheiro, já arredondado ao centavo."""
    if valor is None:
        return ZERO
    return Decimal(valor).quantize(Decimal("0.01"))


class ItemEstoqueQuerySet(models.QuerySet):
    """Leituras reutilizáveis do item, sempre com o saldo derivado.

    O saldo nunca é lido de um campo: ele é calculado aqui, somando o que entra
    e subtraindo o que sai, na mesma consulta. É por isso que a lista consegue
    dizer, de uma vez, quanto cada item tem e se está abaixo do mínimo.
    """

    def do_condominio(self, condominio):
        """Itens deste condomínio. Sem condomínio resolvido, conjunto vazio."""
        if condominio is None or not getattr(condominio, "pk", None):
            return self.none()
        return self.filter(condominio_id=condominio.pk)

    def ativos(self):
        """Itens em uso."""
        return self.filter(ativo=True)

    def com_saldo(self):
        """Anota o saldo de cada item: entradas menos saídas.

        Os conjuntos de entrada e saída vêm de ``constants`` — a mesma fonte que
        o serviço usa para decidir se um movimento soma ou subtrai. Nada de
        repetir a lista de tipos aqui.
        """
        entradas = models.Sum(
            "movimentacoes__quantidade",
            filter=Q(movimentacoes__tipo__in=TIPOS_DE_ENTRADA),
        )
        saidas = models.Sum(
            "movimentacoes__quantidade",
            filter=Q(movimentacoes__tipo__in=TIPOS_DE_SAIDA),
        )
        return self.annotate(
            saldo=models.ExpressionWrapper(
                Coalesce(entradas, models.Value(ZERO), output_field=QUANTIDADE)
                - Coalesce(saidas, models.Value(ZERO), output_field=QUANTIDADE),
                output_field=QUANTIDADE,
            )
        )

    def criticos(self):
        """Itens marcados como críticos."""
        return self.filter(item_critico=True)

    def abaixo_do_minimo(self):
        """Com saldo positivo, mas menor que o mínimo definido."""
        return self.com_saldo().filter(
            estoque_minimo__gt=0, saldo__lt=models.F("estoque_minimo")
        )

    def zerados(self):
        """Sem nada disponível — o mínimo não importa quando não há saldo."""
        return self.com_saldo().filter(saldo__lte=0)

    def buscar(self, termo):
        """Itens cujo nome, descrição ou categoria contém o termo."""
        termo = (termo or "").strip()
        if not termo:
            return self
        return self.filter(
            Q(nome__icontains=termo)
            | Q(descricao__icontains=termo)
            | Q(categoria_texto__icontains=termo)
        )


class ItemEstoque(models.Model):
    """Algo físico que pode entrar, sair, ser consumido e acabar.

    O item é o cadastro do que o condomínio controla: o filtro de água, a
    lâmpada do corredor, o material de limpeza. Ele guarda o mínimo que se
    espera ter e se é crítico, mas **não guarda quanto há**: essa resposta vem
    das movimentações, sempre atual.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    condominio = models.ForeignKey(
        Condominio,
        on_delete=models.PROTECT,
        related_name="itens_estoque",
        verbose_name="Condomínio",
    )
    nome = models.CharField(
        "Item",
        max_length=150,
        help_text="Como o item é conhecido no condomínio.",
    )
    descricao = models.TextField(
        "Descrição",
        blank=True,
        default="",
        help_text="O que se sabe sobre o item e não cabe no nome.",
    )
    categoria_texto = models.CharField(
        "Categoria",
        max_length=60,
        blank=True,
        default="",
        help_text="Agrupamento livre, escrito por quem cadastra.",
    )
    unidade = models.CharField(
        "Unidade",
        max_length=20,
        choices=UnidadeMedida.choices,
        default=UnidadeMedida.UNIDADE,
    )
    local_armazenamento = models.ForeignKey(
        LocalOperacional,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="itens_estoque",
        verbose_name="Local de armazenamento",
        help_text="Onde o item costuma ficar guardado.",
    )
    estoque_minimo = models.DecimalField(
        "Estoque mínimo",
        max_digits=14,
        decimal_places=3,
        default=ZERO,
        help_text="Quanto se espera ter em mãos. Serve para perceber a falta.",
    )
    item_critico = models.BooleanField(
        "Item crítico",
        default=False,
        help_text="Marca o item cuja falta merece atenção. Não dispara compra sozinho.",
    )
    controla_validade = models.BooleanField(
        "Controla validade",
        default=False,
        help_text="Indica que a validade importa na entrada. Não gera baixa automática.",
    )
    ativo = models.BooleanField(
        "Ativo",
        default=True,
        help_text="Desativar retira o item da operação sem apagar o que já aconteceu.",
    )
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="itens_estoque_cadastrados",
        verbose_name="Cadastrado por",
        help_text="Quem cadastrou. Preservado para rastreabilidade.",
    )
    criado_em = models.DateTimeField("Criado em", auto_now_add=True)
    atualizado_em = models.DateTimeField("Atualizado em", auto_now=True)

    objects = ItemEstoqueQuerySet.as_manager()

    class Meta:
        verbose_name = "Item de estoque"
        verbose_name_plural = "Itens de estoque"
        ordering = ["nome"]
        constraints = [
            # Não existe mínimo negativo: quanto se espera ter é zero ou mais.
            models.CheckConstraint(
                condition=Q(estoque_minimo__gte=0),
                name="item_estoque_minimo_nao_negativo",
            ),
            # O mesmo item não se repete no condomínio. A comparação ignora
            # maiúsculas e minúsculas de propósito: "Lâmpada LED", "lampada led"
            # e "LÂMPADA LED" são o mesmo item, e o banco recusa a duplicidade
            # antes que ela vire duas linhas com saldos separados.
            models.UniqueConstraint(
                Lower("nome"),
                "condominio",
                name="item_estoque_nome_unico_cond",
            ),
        ]
        indexes = [
            models.Index(fields=["condominio", "ativo"], name="item_est_cond_ativo_idx"),
            models.Index(
                fields=["condominio", "item_critico"], name="item_est_cond_critico_idx"
            ),
            models.Index(
                fields=["condominio", "local_armazenamento"],
                name="item_est_cond_local_idx",
            ),
        ]

    def __str__(self):
        return self.nome

    def save(self, *args, **kwargs):
        """Guarda o item já normalizado, venha ele do formulário ou não."""
        self.nome = _texto(self.nome)
        self.descricao = _texto_longo(self.descricao)
        self.categoria_texto = _texto(self.categoria_texto)
        super().save(*args, **kwargs)

    def clean(self):
        """Recusa um item incoerente antes que ele chegue ao banco."""
        erros = {}

        if not _texto(self.nome):
            erros["nome"] = "Escreva o nome do item."

        if (
            self.local_armazenamento_id
            and self.condominio_id
            and self.local_armazenamento.condominio_id != self.condominio_id
        ):
            erros["local_armazenamento"] = "O local escolhido não é deste condomínio."

        if self.estoque_minimo is not None and self.estoque_minimo < 0:
            erros["estoque_minimo"] = "O estoque mínimo não pode ser negativo."

        # A checagem aqui existe para dar uma resposta humana: sem ela, a
        # duplicidade só apareceria como erro de banco, sem dizer o porquê.
        if self.condominio_id and _texto(self.nome):
            repetido = (
                ItemEstoque.objects.filter(
                    condominio_id=self.condominio_id, nome__iexact=_texto(self.nome)
                )
                .exclude(pk=self.pk)
                .exists()
            )
            if repetido:
                erros["nome"] = (
                    "Já existe um item com este nome neste condomínio. "
                    "Use o mesmo cadastro em vez de criar outro."
                )

        if erros:
            raise ValidationError(erros)

    @property
    def situacao_do_saldo(self) -> str:
        """Como o saldo deve ser lido agora — derivado, nunca guardado.

        Depende de ``saldo`` estar anotado na consulta. Sem ele, a resposta é a
        ausência: é melhor não dizer nada do que dizer um saldo inventado.
        """
        saldo = getattr(self, "saldo", None)
        if saldo is None:
            return ""
        return situacao_do_saldo(saldo, self.estoque_minimo)

    @property
    def esta_zerado(self) -> bool:
        """Diz se não há nada disponível."""
        saldo = getattr(self, "saldo", None)
        return saldo is not None and saldo <= 0


class MovimentacaoEstoqueQuerySet(models.QuerySet):
    """Leituras reutilizáveis da movimentação."""

    def do_condominio(self, condominio):
        """Movimentações deste condomínio. Sem condomínio, conjunto vazio."""
        if condominio is None or not getattr(condominio, "pk", None):
            return self.none()
        return self.filter(condominio_id=condominio.pk)

    def do_item(self, item):
        """Movimentações deste item."""
        if item is None or not getattr(item, "pk", None):
            return self.none()
        return self.filter(item_id=item.pk)

    def entradas(self):
        """Só o que somou ao saldo."""
        return self.filter(tipo__in=TIPOS_DE_ENTRADA)

    def saidas(self):
        """Só o que subtraiu do saldo."""
        return self.filter(tipo__in=TIPOS_DE_SAIDA)

    def por_manutencao(self, manutencao):
        """Movimentações ligadas a uma manutenção."""
        if manutencao is None or not getattr(manutencao, "pk", None):
            return self.none()
        return self.filter(manutencao_id=manutencao.pk)

    def recentes(self):
        """Do mais recente para o mais antigo."""
        return self.order_by("-criado_em")


class MovimentacaoEstoque(models.Model):
    """O efeito físico no estoque — o que entrou, o que saiu e por quê.

    É também a trilha de auditoria do saldo: por isso não existe um histórico
    paralelo. Cada linha diz o item, o tipo, a quantidade, quando aconteceu e
    quem registrou, e aponta para a origem quando ela existe — a linha da
    compra que trouxe o material ou a manutenção que o consumiu.

    A quantidade é sempre positiva; o tipo diz se soma ou subtrai. E o registro
    nunca é apagado: emendar o passado esconderia o que de fato houve.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    condominio = models.ForeignKey(
        Condominio,
        on_delete=models.PROTECT,
        related_name="movimentacoes_estoque",
        verbose_name="Condomínio",
    )
    item = models.ForeignKey(
        ItemEstoque,
        on_delete=models.PROTECT,
        related_name="movimentacoes",
        verbose_name="Item",
    )
    tipo = models.CharField(
        "Tipo",
        max_length=24,
        choices=TipoMovimentacaoEstoque.choices,
    )
    quantidade = models.DecimalField(
        "Quantidade",
        max_digits=14,
        decimal_places=3,
        help_text="Sempre positiva. O tipo é que diz se entra ou sai.",
    )
    compra_item = models.ForeignKey(
        "ItemCompra",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="movimentacoes",
        verbose_name="Item da compra",
        help_text="A linha que originou a entrada, quando veio de uma compra.",
    )
    manutencao = models.ForeignKey(
        Manutencao,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="movimentacoes_de_estoque",
        verbose_name="Manutenção",
        help_text="O serviço que consumiu o material, quando foi para uma manutenção.",
    )
    motivo = models.CharField(
        "Motivo",
        max_length=255,
        blank=True,
        default="",
        help_text="Por que o movimento aconteceu. Obrigatório em uso, ajuste e perda.",
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="movimentacoes_de_estoque",
        verbose_name="Registrado por",
        help_text="Quem registrou o movimento. Preservado para rastreabilidade.",
    )
    criado_em = models.DateTimeField("Quando", auto_now_add=True)

    objects = MovimentacaoEstoqueQuerySet.as_manager()

    class Meta:
        verbose_name = "Movimentação de estoque"
        verbose_name_plural = "Movimentações de estoque"
        ordering = ["-criado_em"]
        constraints = [
            # A quantidade é sempre positiva: o sinal vem do tipo, e não do
            # número. Sem isso, um "-5" de entrada viraria uma saída disfarçada.
            models.CheckConstraint(
                condition=Q(quantidade__gt=0),
                name="mov_estoque_quantidade_positiva",
            ),
            # Uso, ajuste e perda só existem quando alguém explica o porquê.
            models.CheckConstraint(
                condition=(
                    ~Q(tipo__in=TIPOS_QUE_EXIGEM_MOTIVO)
                    | (Q(motivo__isnull=False) & ~Q(motivo=""))
                ),
                name="mov_estoque_motivo_quando_exigido",
            ),
        ]
        indexes = [
            models.Index(fields=["condominio", "criado_em"], name="mov_est_cond_data_idx"),
            models.Index(fields=["item", "criado_em"], name="mov_est_item_data_idx"),
            models.Index(fields=["condominio", "tipo"], name="mov_est_cond_tipo_idx"),
        ]

    def __str__(self):
        return f"{self.get_tipo_display()} · {self.item}"

    def save(self, *args, **kwargs):
        """Guarda o movimento já normalizado."""
        self.motivo = _texto(self.motivo)
        super().save(*args, **kwargs)

    def clean(self):
        """Recusa uma movimentação incoerente antes que ela chegue ao banco."""
        erros = {}

        if self.quantidade is not None and self.quantidade <= 0:
            erros["quantidade"] = "A quantidade precisa ser maior que zero."

        try:
            direcao_do_tipo(self.tipo)
        except ValueError:
            erros["tipo"] = "Tipo de movimentação inválido."

        if (
            self.item_id
            and self.condominio_id
            and self.item.condominio_id != self.condominio_id
        ):
            erros["item"] = "O item escolhido não é deste condomínio."

        if (
            self.manutencao_id
            and self.condominio_id
            and self.manutencao.condominio_id != self.condominio_id
        ):
            erros["manutencao"] = "A manutenção escolhida não é deste condomínio."

        if self.compra_item_id:
            if self.compra_item.compra.condominio_id != self.condominio_id:
                erros["compra_item"] = "A compra escolhida não é deste condomínio."
            if self.item_id and self.compra_item.item_estoque_id != self.item_id:
                erros["compra_item"] = "A linha da compra é de outro item."

        if self.tipo in TIPOS_QUE_EXIGEM_MOTIVO and not _texto(self.motivo):
            erros["motivo"] = "Explique o motivo deste movimento."

        if erros:
            raise ValidationError(erros)

    @property
    def direcao(self) -> str:
        """Se este movimento soma ou subtrai do saldo."""
        try:
            return direcao_do_tipo(self.tipo)
        except ValueError:
            return ""

    @property
    def soma_ao_saldo(self) -> bool:
        """Diz se este movimento entra no saldo."""
        return self.direcao == "entrada"


class CompraQuerySet(models.QuerySet):
    """Leituras reutilizáveis da compra."""

    def do_condominio(self, condominio):
        """Compras deste condomínio. Sem condomínio resolvido, conjunto vazio."""
        if condominio is None or not getattr(condominio, "pk", None):
            return self.none()
        return self.filter(condominio_id=condominio.pk)

    def por_status(self, status):
        """Compras nesta situação."""
        if not status:
            return self
        return self.filter(status=status)

    def por_fornecedor(self, fornecedor):
        """Compras deste fornecedor."""
        if fornecedor is None or not getattr(fornecedor, "pk", None):
            return self
        return self.filter(fornecedor_id=fornecedor.pk)

    def por_manutencao(self, manutencao):
        """Compras ligadas a esta manutenção."""
        if manutencao is None or not getattr(manutencao, "pk", None):
            return self.none()
        return self.filter(manutencao_origem_id=manutencao.pk)

    def confirmadas(self):
        """Só o que já valeu."""
        return self.filter(status=StatusCompra.CONFIRMADA)

    def recentes(self):
        """Da mais recente para a mais antiga, pela data do fato."""
        return self.order_by("-data_compra", "-criado_em")

    def buscar(self, termo):
        """Compras cujo documento ou observação contém o termo."""
        termo = (termo or "").strip()
        if not termo:
            return self
        return self.filter(
            Q(numero_documento__icontains=termo)
            | Q(observacao__icontains=termo)
            | Q(fornecedor__nome_razao_social__icontains=termo)
            | Q(fornecedor__nome_fantasia__icontains=termo)
        )

    def com_total(self):
        """Anota o total da compra: a soma das linhas.

        O total não é um campo — é o que as linhas somam. Guardá-lo seria
        manter dois lugares para o mesmo número, e um deles ficaria errado.
        """
        return self.annotate(
            total=models.ExpressionWrapper(
                Coalesce(
                    models.Sum("itens__valor_total"),
                    models.Value(ZERO),
                    output_field=DINHEIRO,
                ),
                output_field=DINHEIRO,
            )
        )


class Compra(models.Model):
    """O que o condomínio adquiriu: de quem, quando, quanto custou.

    A compra é o fato econômico. Ela nasce como rascunho, sem efeito nenhum, e
    só ao ser confirmada é que o destino de cada linha é aplicado — o que era
    para o estoque entra no estoque, o que era para uso imediato só fica
    registrado. Confirmar duas vezes não duplica nada: a segunda recusa.

    O total não é campo: é a soma das linhas. Os valores aqui são operacionais e
    não geram despesa, não alteram balanço e não criam lançamento financeiro.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    condominio = models.ForeignKey(
        Condominio,
        on_delete=models.PROTECT,
        related_name="compras",
        verbose_name="Condomínio",
    )
    fornecedor = models.ForeignKey(
        Fornecedor,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="compras",
        verbose_name="Fornecedor",
        help_text="De quem foi comprado, quando se sabe.",
    )
    data_compra = models.DateField(
        "Data da compra",
        help_text="Quando a compra aconteceu.",
    )
    numero_documento = models.CharField(
        "Documento",
        max_length=60,
        blank=True,
        default="",
        help_text="Nota, cupom ou recibo, quando houver.",
    )
    observacao = models.TextField(
        "Observação",
        blank=True,
        default="",
        help_text="O que se sabe sobre a compra e não cabe nos outros campos.",
    )
    status = models.CharField(
        "Situação",
        max_length=12,
        choices=StatusCompra.choices,
        default=StatusCompra.RASCUNHO,
    )
    manutencao_origem = models.ForeignKey(
        Manutencao,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="compras_originadas",
        verbose_name="Manutenção de origem",
        help_text="A que serviço a compra atende, quando atende. Não altera a manutenção.",
    )
    confirmada_em = models.DateTimeField(
        "Confirmada em",
        null=True,
        blank=True,
        help_text="Quando a compra passou a valer. Vazio enquanto é rascunho.",
    )
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="compras_registradas",
        verbose_name="Registrada por",
        help_text="Quem registrou a compra. Preservado para rastreabilidade.",
    )
    criado_em = models.DateTimeField("Criada em", auto_now_add=True)
    atualizado_em = models.DateTimeField("Atualizada em", auto_now=True)

    objects = CompraQuerySet.as_manager()

    class Meta:
        verbose_name = "Compra"
        verbose_name_plural = "Compras"
        ordering = ["-data_compra", "-criado_em"]
        constraints = [
            # A situação diz o que aconteceu; a data diz quando. Uma compra
            # confirmada registra o momento, e as outras não registram.
            models.CheckConstraint(
                condition=(
                    Q(status=StatusCompra.CONFIRMADA, confirmada_em__isnull=False)
                    | (
                        ~Q(status=StatusCompra.CONFIRMADA)
                        & Q(confirmada_em__isnull=True)
                    )
                ),
                name="compra_confirmada_registra_quando",
            ),
        ]
        indexes = [
            models.Index(fields=["condominio", "status"], name="compra_cond_status_idx"),
            models.Index(
                fields=["condominio", "data_compra"], name="compra_cond_data_idx"
            ),
            models.Index(
                fields=["fornecedor", "data_compra"], name="compra_forn_data_idx"
            ),
        ]

    def __str__(self):
        return f"Compra de {self.data_compra:%d/%m/%Y}"

    def save(self, *args, **kwargs):
        """Guarda a compra já normalizada."""
        self.numero_documento = _texto(self.numero_documento)
        self.observacao = _texto_longo(self.observacao)
        super().save(*args, **kwargs)

    def clean(self):
        """Recusa uma compra incoerente antes que ela chegue ao banco."""
        erros = {}

        if (
            self.fornecedor_id
            and self.condominio_id
            and self.fornecedor.condominio_id != self.condominio_id
        ):
            erros["fornecedor"] = "O fornecedor escolhido não é deste condomínio."

        if (
            self.manutencao_origem_id
            and self.condominio_id
            and self.manutencao_origem.condominio_id != self.condominio_id
        ):
            erros["manutencao_origem"] = (
                "A manutenção de origem não é deste condomínio."
            )

        if self.status == StatusCompra.CONFIRMADA and self.confirmada_em is None:
            erros["confirmada_em"] = (
                "Uma compra confirmada precisa registrar quando foi confirmada."
            )
        elif self.status != StatusCompra.CONFIRMADA and self.confirmada_em is not None:
            erros["confirmada_em"] = (
                "Só uma compra confirmada registra a data de confirmação."
            )

        if erros:
            raise ValidationError(erros)

    @property
    def esta_confirmada(self) -> bool:
        """Diz se a compra já valeu."""
        return self.status == StatusCompra.CONFIRMADA

    @property
    def pode_editar(self) -> bool:
        """Só o rascunho ainda muda: o que já valeu não se reescreve."""
        return self.status == StatusCompra.RASCUNHO

    def total_calculado(self):
        """A soma das linhas, calculada na hora. Zero quando não há linhas."""
        agregado = self.itens.aggregate(t=models.Sum("valor_total"))["t"]
        return _dinheiro(agregado)


class ItemCompraQuerySet(models.QuerySet):
    """Leituras reutilizáveis da linha de compra."""

    def da_compra(self, compra):
        """As linhas desta compra."""
        if compra is None or not getattr(compra, "pk", None):
            return self.none()
        return self.filter(compra_id=compra.pk)

    def para_estoque(self):
        """As linhas cujo destino inclui o estoque."""
        return self.filter(
            destino__in=[DestinoDoItem.ESTOQUE, DestinoDoItem.MISTO]
        )


class ItemCompra(models.Model):
    """Uma linha da compra, com o destino já dito.

    Cada linha sabe para onde o que foi comprado vai: consumo no ato, estoque,
    ou parte em cada um. No misto, as duas quantidades precisam somar
    exatamente o que foi comprado — a conta que não fecha é recusada pelo banco.

    O item de estoque é obrigatório quando o destino inclui o estoque. Ele é
    **escolhido**, nunca criado em silêncio: quem decide o que entra no estoque
    é a gestão, não a linha da compra.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    compra = models.ForeignKey(
        Compra,
        on_delete=models.CASCADE,
        related_name="itens",
        verbose_name="Compra",
    )
    item_estoque = models.ForeignKey(
        ItemEstoque,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="itens_de_compra",
        verbose_name="Item de estoque",
        help_text="Qual item do estoque esta linha repõe. Obrigatório quando o destino inclui estoque.",
    )
    descricao = models.CharField(
        "Descrição",
        max_length=200,
        help_text="O que foi comprado, como aparece no documento.",
    )
    unidade = models.CharField(
        "Unidade",
        max_length=20,
        choices=UnidadeMedida.choices,
        default=UnidadeMedida.UNIDADE,
    )
    quantidade = models.DecimalField(
        "Quantidade", max_digits=14, decimal_places=3
    )
    valor_unitario = models.DecimalField(
        "Valor unitário", max_digits=12, decimal_places=2, default=ZERO
    )
    valor_total = models.DecimalField(
        "Total da linha",
        max_digits=12,
        decimal_places=2,
        default=ZERO,
        help_text="Calculado pela quantidade vezes o valor unitário.",
    )
    destino = models.CharField(
        "Destino",
        max_length=14,
        choices=DestinoDoItem.choices,
        default=DestinoDoItem.ESTOQUE,
    )
    quantidade_uso_imediato = models.DecimalField(
        "Quantidade de uso imediato",
        max_digits=14,
        decimal_places=3,
        default=ZERO,
    )
    quantidade_estoque = models.DecimalField(
        "Quantidade para o estoque",
        max_digits=14,
        decimal_places=3,
        default=ZERO,
    )
    manutencao = models.ForeignKey(
        Manutencao,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="itens_de_compra",
        verbose_name="Manutenção",
        help_text="O serviço a que o uso imediato se refere, quando houver.",
    )
    validade = models.DateField(
        "Validade",
        null=True,
        blank=True,
        help_text="Quando o item controla validade. Informativo nesta fase.",
    )
    criado_em = models.DateTimeField("Criada em", auto_now_add=True)
    atualizado_em = models.DateTimeField("Atualizada em", auto_now=True)

    objects = ItemCompraQuerySet.as_manager()

    class Meta:
        verbose_name = "Item da compra"
        verbose_name_plural = "Itens da compra"
        ordering = ["criado_em"]
        constraints = [
            models.CheckConstraint(
                condition=Q(quantidade__gt=0),
                name="item_compra_quantidade_positiva",
            ),
            models.CheckConstraint(
                condition=Q(valor_unitario__gte=0),
                name="item_compra_valor_nao_negativo",
            ),
            models.CheckConstraint(
                condition=Q(quantidade_uso_imediato__gte=0)
                & Q(quantidade_estoque__gte=0),
                name="item_compra_partes_nao_negativas",
            ),
            # O destino e as quantidades precisam contar a mesma história: no
            # uso imediato tudo é consumido, no estoque tudo é guardado, e no
            # misto as duas partes somam exatamente o que foi comprado. Uma
            # divisão que não fecha seria um erro silencioso no saldo.
            models.CheckConstraint(
                condition=(
                    Q(
                        destino=DestinoDoItem.USO_IMEDIATO,
                        quantidade_estoque=0,
                        quantidade_uso_imediato=models.F("quantidade"),
                    )
                    | Q(
                        destino=DestinoDoItem.ESTOQUE,
                        quantidade_uso_imediato=0,
                        quantidade_estoque=models.F("quantidade"),
                    )
                    | (
                        Q(
                            destino=DestinoDoItem.MISTO,
                            quantidade_uso_imediato__gt=0,
                            quantidade_estoque__gt=0,
                        )
                        & Q(
                            quantidade=models.F("quantidade_uso_imediato")
                            + models.F("quantidade_estoque")
                        )
                    )
                ),
                name="item_compra_destino_coerente",
            ),
            # Quando o destino inclui o estoque, o item precisa estar escolhido.
            models.CheckConstraint(
                condition=Q(destino=DestinoDoItem.USO_IMEDIATO)
                | Q(item_estoque__isnull=False),
                name="item_compra_estoque_tem_item",
            ),
        ]
        indexes = [
            models.Index(fields=["item_estoque"], name="item_compra_item_idx"),
        ]

    def __str__(self):
        return self.descricao

    def save(self, *args, **kwargs):
        """Guarda a linha já normalizada e com o total recalculado.

        O total é sempre recalculado aqui, a partir da quantidade e do valor
        unitário. O valor que o navegador enviou não é aceito: quem multiplica
        é o backend.
        """
        self.descricao = _texto(self.descricao)
        self.valor_total = _dinheiro(
            (self.quantidade or ZERO) * (self.valor_unitario or ZERO)
        )
        super().save(*args, **kwargs)

    def clean(self):
        """Recusa uma linha incoerente antes que ela chegue ao banco."""
        erros = {}

        if not _texto(self.descricao):
            erros["descricao"] = "Escreva o que foi comprado."

        if self.quantidade is not None and self.quantidade <= 0:
            erros["quantidade"] = "A quantidade precisa ser maior que zero."

        if self.valor_unitario is not None and self.valor_unitario < 0:
            erros["valor_unitario"] = "O valor unitário não pode ser negativo."

        if self.destino != DestinoDoItem.USO_IMEDIATO and not self.item_estoque_id:
            erros["item_estoque"] = (
                "Escolha o item de estoque que esta linha repõe."
            )

        if (
            self.item_estoque_id
            and self.compra_id
            and self.item_estoque.condominio_id != self.compra.condominio_id
        ):
            erros["item_estoque"] = "O item escolhido não é deste condomínio."

        if (
            self.manutencao_id
            and self.compra_id
            and self.manutencao.condominio_id != self.compra.condominio_id
        ):
            erros["manutencao"] = "A manutenção escolhida não é deste condomínio."

        if self.compra_id and not self.compra.pode_editar:
            erros["compra"] = (
                "Uma compra confirmada não muda mais. Registre um novo movimento."
            )

        if erros:
            raise ValidationError(erros)

    @property
    def quantidade_que_vai_para_o_estoque(self):
        """Quanto desta linha entra no estoque. Uso imediato não entra."""
        if self.destino == DestinoDoItem.ESTOQUE:
            return self.quantidade
        if self.destino == DestinoDoItem.MISTO:
            return self.quantidade_estoque
        return ZERO
