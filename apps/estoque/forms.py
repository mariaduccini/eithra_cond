"""Formulários do domínio de estoque e compras operacionais.

Pede-se apenas o necessário. No item, nome e condomínio bastam; unidade, mínimo,
criticidade e validade aparecem em progressive disclosure. No movimento, o tipo
já limita o que pode ser feito: quem não responde pelo condomínio não recebe
perda, vencimento nem ajuste entre as escolhas — a lista é a mesma que o domínio
aceita, e não uma checagem solta na tela.

Nada de campo técnico, saldo digitado ou identificador interno. O que o
formulário não pergunta, o sistema deriva: o saldo vem das movimentações, o total
da linha vem da quantidade vezes o valor unitário, e o destino da linha decide
quanto vai para o estoque e quanto é consumido no ato.
"""
from django import forms
from django.contrib.auth import get_user_model
from django.db.models import Q

from apps.cadastros.models import Fornecedor, LocalOperacional
from apps.condominios.models import Condominio
from apps.demandas.models import Demanda
from apps.manutencoes.models import Manutencao

from .constants import (
    TIPOS_DE_MOVIMENTO_MANUAL,
    DestinoDoItem,
    TipoDocumentoCompra,
    StatusCompra,
    TipoMovimentacaoEstoque,
    UnidadeMedida,
)
from .models import ItemEstoque

# A ordem em que os movimentos manuais aparecem: primeiro o que sai, depois o que
# corrige. Entrada por compra e saída para manutenção têm caminho próprio e não
# se oferecem aqui.
ORDEM_DOS_MOVIMENTOS = TIPOS_DE_MOVIMENTO_MANUAL

# Leituras da lista de itens: não é filtro de relatório, é o que merece atenção.
FILTROS_DE_ITEM = (
    ("", "Todos os itens"),
    ("abaixo", "Abaixo do mínimo"),
    ("zerados", "Zerados"),
    ("criticos", "Itens críticos"),
)


def _escolhas_do_condominio(modelo, condominio, atual=None):
    """Os registros do condomínio oferecidos numa escolha.

    Só os que estão em uso — mais aquele que o registro já aponta, para que um
    cadastro desativado depois continue legível sem voltar a ser oferecido.
    """
    if condominio is None or not getattr(condominio, "pk", None):
        return modelo.objects.none()

    queryset = modelo.objects.do_condominio(condominio)
    if atual is not None and getattr(atual, "pk", None):
        return queryset.filter(Q(ativo=True) | Q(pk=atual.pk))
    return queryset.ativos()


def _manutencoes_do_condominio(condominio, atual=None):
    """As manutenções do condomínio que ainda fazem sentido escolher."""
    if condominio is None or not getattr(condominio, "pk", None):
        return Manutencao.objects.none()

    queryset = Manutencao.objects.filter(condominio=condominio)
    if atual is not None and getattr(atual, "pk", None):
        return queryset.filter(Q(ativo=True) | Q(pk=atual.pk))
    return queryset.filter(ativo=True)


def _demandas_do_condominio(condominio, atual=None):
    """As demandas do condomínio que ainda fazem sentido escolher."""
    if condominio is None or not getattr(condominio, "pk", None):
        return Demanda.objects.none()

    queryset = Demanda.objects.filter(condominio=condominio)
    if atual is not None and getattr(atual, "pk", None):
        return queryset.filter(Q(ativo=True) | Q(pk=atual.pk))
    return queryset.filter(ativo=True)


class ItemEstoqueForm(forms.Form):
    """Cria ou edita o cadastro de um item do estoque.

    O item não guarda saldo: cadastrá-lo é só dizer que ele passa a ser
    controlado. O que já existe entra por um ajuste de entrada, com motivo.
    """

    condominio = forms.ModelChoiceField(
        label="Condomínio",
        queryset=Condominio.objects.none(),
        empty_label=None,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    nome = forms.CharField(
        label="Item",
        max_length=150,
        error_messages={"required": "Escreva o nome do item."},
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Ex.: Lâmpada LED 9W",
                "autocomplete": "off",
            }
        ),
    )
    categoria_texto = forms.CharField(
        label="Categoria",
        required=False,
        max_length=60,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Ex.: Elétrica, Hidráulica, Limpeza",
                "autocomplete": "off",
            }
        ),
    )
    unidade = forms.ChoiceField(
        label="Unidade",
        choices=UnidadeMedida.choices,
        initial=UnidadeMedida.UNIDADE,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    local_armazenamento = forms.ModelChoiceField(
        label="Local de armazenamento",
        queryset=LocalOperacional.objects.none(),
        required=False,
        empty_label="Sem local definido",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    estoque_minimo = forms.DecimalField(
        label="Estoque mínimo",
        required=False,
        min_value=0,
        max_digits=14,
        decimal_places=3,
        error_messages={"min_value": "O estoque mínimo não pode ser negativo."},
        widget=forms.NumberInput(
            attrs={"class": "form-control", "step": "0.001", "min": "0"}
        ),
    )
    item_critico = forms.BooleanField(
        label="Item crítico",
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    controla_validade = forms.BooleanField(
        label="Controla validade",
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    descricao = forms.CharField(
        label="Descrição",
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 3,
                "placeholder": "O que se sabe sobre o item e não cabe no nome",
            }
        ),
    )

    def __init__(
        self, *args, condominios=None, condominio=None, item=None, **kwargs
    ):
        super().__init__(*args, **kwargs)

        self.item_editado = item
        permitidos = condominios if condominios is not None else Condominio.objects.none()
        campo_condominio = self.fields["condominio"]

        if condominio is not None:
            campo_condominio.queryset = Condominio.objects.filter(pk=condominio.pk)
            campo_condominio.initial = condominio.pk
            campo_condominio.widget = forms.HiddenInput()
            self.condominio_efetivo = condominio
        else:
            campo_condominio.queryset = permitidos
            elegiveis = list(permitidos)
            if len(elegiveis) == 1:
                campo_condominio.initial = elegiveis[0].pk
                campo_condominio.widget = forms.HiddenInput()
            self.condominio_efetivo = self._condominio_escolhido(permitidos)

        atual = self.condominio_efetivo
        self.fields["local_armazenamento"].queryset = _escolhas_do_condominio(
            LocalOperacional, atual, getattr(item, "local_armazenamento", None)
        )

    def _condominio_escolhido(self, permitidos):
        """Qual condomínio vale para montar as listas de escolha.

        Vale o que a pessoa enviou, desde que esteja entre os permitidos; senão,
        o único elegível, quando houver só um.
        """
        enviado = self.data.get("condominio") if self.is_bound else None
        if enviado:
            escolhido = permitidos.filter(pk=enviado).first()
            if escolhido is not None:
                return escolhido

        elegiveis = list(permitidos)
        return elegiveis[0] if len(elegiveis) == 1 else None

    def clean_nome(self):
        nome = " ".join((self.cleaned_data["nome"] or "").split())
        if not nome:
            raise forms.ValidationError("Escreva o nome do item.")
        return nome

    def clean_categoria_texto(self):
        return " ".join((self.cleaned_data["categoria_texto"] or "").split())

    def clean_descricao(self):
        return (self.cleaned_data["descricao"] or "").strip()

    def clean(self):
        """Recusa o item que repete outro do mesmo condomínio.

        A resposta é humana de propósito: o banco também recusaria a duplicidade,
        mas não diria por quê. Aqui, quem cadastra entende que deve usar o
        cadastro que já existe.
        """
        dados = super().clean()
        nome = dados.get("nome")
        condominio = dados.get("condominio")
        if not nome or condominio is None:
            return dados

        repetido = (
            ItemEstoque.objects.filter(condominio=condominio, nome__iexact=nome)
            .exclude(pk=getattr(self.item_editado, "pk", None))
            .exists()
        )
        if repetido:
            self.add_error(
                "nome",
                "Já existe um item com este nome neste condomínio. "
                "Use o mesmo cadastro em vez de criar outro.",
            )
        return dados


class MovimentacaoEstoqueForm(forms.Form):
    """Registra um movimento feito à mão: uso, perda, vencimento ou ajuste."""

    tipo = forms.ChoiceField(
        label="Movimento",
        choices=(),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    quantidade = forms.DecimalField(
        label="Quantidade",
        min_value=0,
        max_digits=14,
        decimal_places=3,
        error_messages={
            "required": "Informe a quantidade.",
            "min_value": "A quantidade precisa ser maior que zero.",
        },
        widget=forms.NumberInput(
            attrs={"class": "form-control", "step": "0.001", "min": "0"}
        ),
    )
    motivo = forms.CharField(
        label="Motivo",
        required=False,
        max_length=255,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Por que este movimento aconteceu",
                "autocomplete": "off",
            }
        ),
    )

    def __init__(self, *args, permitidos=(), **kwargs):
        super().__init__(*args, **kwargs)
        alcancaveis = set(permitidos)
        self.fields["tipo"].choices = [
            (tipo, TipoMovimentacaoEstoque(tipo).label)
            for tipo in ORDEM_DOS_MOVIMENTOS
            if tipo in alcancaveis
        ]

    def clean_quantidade(self):
        quantidade = self.cleaned_data["quantidade"]
        if quantidade <= 0:
            raise forms.ValidationError("A quantidade precisa ser maior que zero.")
        return quantidade

    def clean_motivo(self):
        return " ".join((self.cleaned_data["motivo"] or "").split())


class SaidaParaManutencaoForm(forms.Form):
    """Registra a retirada de material para uma manutenção.

    A manutenção precisa ser de um condomínio em que a pessoa alcance o estoque,
    e o item precisa ser do mesmo condomínio da manutenção. Ter saldo é conferido
    pelo serviço, no momento da retirada — aqui se confere a quem pertence.
    """

    manutencao = forms.ModelChoiceField(
        label="Manutenção",
        queryset=Manutencao.objects.none(),
        empty_label=None,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    item = forms.ModelChoiceField(
        label="Item",
        queryset=ItemEstoque.objects.none(),
        empty_label=None,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    quantidade = forms.DecimalField(
        label="Quantidade",
        min_value=0,
        max_digits=14,
        decimal_places=3,
        error_messages={
            "required": "Informe a quantidade retirada.",
            "min_value": "A quantidade precisa ser maior que zero.",
        },
        widget=forms.NumberInput(
            attrs={"class": "form-control", "step": "0.001", "min": "0"}
        ),
    )
    motivo = forms.CharField(
        label="Observação",
        required=False,
        max_length=255,
        widget=forms.TextInput(
            attrs={"class": "form-control", "autocomplete": "off"}
        ),
    )

    def __init__(self, *args, manutencoes=None, itens=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["manutencao"].queryset = (
            manutencoes if manutencoes is not None else Manutencao.objects.none()
        )
        self.fields["item"].queryset = (
            itens if itens is not None else ItemEstoque.objects.none()
        )

    def clean_quantidade(self):
        quantidade = self.cleaned_data["quantidade"]
        if quantidade <= 0:
            raise forms.ValidationError("A quantidade precisa ser maior que zero.")
        return quantidade

    def clean_motivo(self):
        return " ".join((self.cleaned_data["motivo"] or "").split())

    def clean(self):
        dados = super().clean()
        manutencao = dados.get("manutencao")
        item = dados.get("item")
        if manutencao is None or item is None:
            return dados

        if item.condominio_id != manutencao.condominio_id:
            self.add_error(
                "item", "O item escolhido não é do condomínio desta manutenção."
            )
        return dados


class CompraForm(forms.Form):
    """Cria ou edita o cabeçalho de uma compra."""

    condominio = forms.ModelChoiceField(
        label="Condomínio",
        queryset=Condominio.objects.none(),
        empty_label=None,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    data_compra = forms.DateField(
        label="Data da compra",
        input_formats=["%Y-%m-%d", "%d/%m/%Y"],
        error_messages={"required": "Informe a data da compra."},
        widget=forms.DateInput(
            attrs={"class": "form-control", "type": "date"}, format="%Y-%m-%d"
        ),
    )
    fornecedor = forms.ModelChoiceField(
        label="Fornecedor",
        queryset=Fornecedor.objects.none(),
        required=False,
        empty_label="Sem fornecedor",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    forma_pagamento = forms.CharField(
        label="Forma de pagamento",
        required=False,
        max_length=60,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Ex.: Pix, cartão ou transferência",
                "autocomplete": "off",
            }
        ),
    )
    numero_documento = forms.CharField(
        label="Documento",
        required=False,
        max_length=60,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Nota, cupom ou recibo",
                "autocomplete": "off",
            }
        ),
    )
    manutencao_origem = forms.ModelChoiceField(
        label="Manutenção de origem",
        queryset=Manutencao.objects.none(),
        required=False,
        empty_label="Sem manutenção de origem",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    demanda_origem = forms.ModelChoiceField(
        label="Demanda de origem",
        queryset=Demanda.objects.none(),
        required=False,
        empty_label="Sem demanda de origem",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    observacao = forms.CharField(
        label="Observação",
        required=False,
        widget=forms.Textarea(
            attrs={"class": "form-control", "rows": 3}
        ),
    )

    def __init__(
        self, *args, condominios=None, condominio=None, compra=None, **kwargs
    ):
        super().__init__(*args, **kwargs)

        permitidos = condominios if condominios is not None else Condominio.objects.none()
        campo_condominio = self.fields["condominio"]

        if condominio is not None:
            campo_condominio.queryset = Condominio.objects.filter(pk=condominio.pk)
            campo_condominio.initial = condominio.pk
            campo_condominio.widget = forms.HiddenInput()
            self.condominio_efetivo = condominio
        else:
            campo_condominio.queryset = permitidos
            elegiveis = list(permitidos)
            if len(elegiveis) == 1:
                campo_condominio.initial = elegiveis[0].pk
                campo_condominio.widget = forms.HiddenInput()
            self.condominio_efetivo = self._condominio_escolhido(permitidos)

        atual = self.condominio_efetivo
        self.fields["fornecedor"].queryset = _escolhas_do_condominio(
            Fornecedor, atual, getattr(compra, "fornecedor", None)
        )
        self.fields["manutencao_origem"].queryset = _manutencoes_do_condominio(
            atual, getattr(compra, "manutencao_origem", None)
        )
        self.fields["demanda_origem"].queryset = _demandas_do_condominio(
            atual, getattr(compra, "demanda_origem", None)
        )
        if compra is not None and compra.data_compra is None:
            self.fields["data_compra"].required = False

    def _condominio_escolhido(self, permitidos):
        """Qual condomínio vale para montar as listas de escolha."""
        enviado = self.data.get("condominio") if self.is_bound else None
        if enviado:
            escolhido = permitidos.filter(pk=enviado).first()
            if escolhido is not None:
                return escolhido

        elegiveis = list(permitidos)
        return elegiveis[0] if len(elegiveis) == 1 else None

    def clean_numero_documento(self):
        return " ".join((self.cleaned_data["numero_documento"] or "").split())

    def clean_forma_pagamento(self):
        return " ".join((self.cleaned_data["forma_pagamento"] or "").split())

    def clean_observacao(self):
        return (self.cleaned_data["observacao"] or "").strip()


class ImportarCompraForm(forms.Form):
    """Recebe o primeiro documento antes de sugerir qualquer dado da compra."""

    condominio = forms.ModelChoiceField(
        label="Condomínio",
        queryset=Condominio.objects.none(),
        empty_label=None,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    tipo_documento = forms.ChoiceField(
        label="Tipo de documento",
        choices=TipoDocumentoCompra.choices,
        initial=TipoDocumentoCompra.NOTA_FISCAL,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    arquivo = forms.FileField(
        label="Nota fiscal, recibo ou cupom",
        widget=forms.FileInput(
            attrs={
                "class": "form-control",
                "accept": ".pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png",
                "capture": "environment",
            }
        ),
    )

    def __init__(self, *args, condominios=None, **kwargs):
        super().__init__(*args, **kwargs)
        permitidos = (
            condominios
            if condominios is not None
            else Condominio.objects.none()
        )
        self.fields["condominio"].queryset = permitidos
        elegiveis = list(permitidos)
        if len(elegiveis) == 1:
            self.fields["condominio"].initial = elegiveis[0].pk
            self.fields["condominio"].widget = forms.HiddenInput()


class ReembolsoCompraForm(forms.Form):
    """Captura o pedido de reembolso sem abrir uma conta financeira paralela."""

    comprada_por_outra_pessoa = forms.BooleanField(
        label="Compra feita por outra pessoa?",
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    precisa_reembolso = forms.BooleanField(
        label="Precisa de reembolso?",
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    reembolso_para = forms.ModelChoiceField(
        label="Reembolsar para",
        queryset=get_user_model().objects.none(),
        required=False,
        empty_label="Selecione um usuário",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    valor_reembolso = forms.DecimalField(
        label="Valor do reembolso",
        required=False,
        min_value=0,
        max_digits=12,
        decimal_places=2,
        widget=forms.NumberInput(
            attrs={"class": "form-control", "min": "0.01", "step": "0.01"}
        ),
    )
    observacao_reembolso = forms.CharField(
        label="Observação do reembolso",
        required=False,
        max_length=500,
        widget=forms.Textarea(
            attrs={"class": "form-control", "rows": 2}
        ),
    )

    def __init__(self, *args, condominio=None, total=None, **kwargs):
        super().__init__(*args, **kwargs)
        if condominio is not None:
            self.fields["reembolso_para"].queryset = (
                get_user_model()
                .objects.filter(
                    is_active=True,
                    acessos_condominio__condominio=condominio,
                    acessos_condominio__ativo=True,
                )
                .distinct()
                .order_by("full_name", "email")
            )
        self.total_compra = total

    def clean(self):
        dados = super().clean()
        outra_pessoa = dados.get("comprada_por_outra_pessoa")
        precisa = dados.get("precisa_reembolso")
        usuario = dados.get("reembolso_para")
        valor = dados.get("valor_reembolso")

        if precisa and not outra_pessoa:
            self.add_error(
                "precisa_reembolso",
                "Marque que a compra foi feita por outra pessoa.",
            )
        if precisa:
            if usuario is None:
                self.add_error("reembolso_para", "Escolha um usuário do condomínio.")
            if valor is None:
                self.add_error("valor_reembolso", "Informe o valor do reembolso.")
            elif valor <= 0:
                self.add_error(
                    "valor_reembolso", "O valor precisa ser maior que zero."
                )
            elif self.total_compra is not None and valor > self.total_compra:
                self.add_error(
                    "valor_reembolso",
                    "O reembolso não pode superar o total da compra.",
                )
        elif usuario is not None or valor is not None:
            self.add_error(
                "precisa_reembolso",
                "Os campos de reembolso só se aplicam quando ele é solicitado.",
            )
        return dados


class CadastrarItemExtraidoForm(forms.Form):
    """Revisa nome e categoria antes de criar um item no catálogo canônico."""

    nome = forms.CharField(
        label="Nome do item",
        max_length=150,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    categoria_texto = forms.CharField(
        label="Categoria",
        max_length=60,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )


class ItemCompraForm(forms.Form):
    """Uma linha da compra, com o destino já dito.

    O item de estoque é escolhido, nunca criado em silêncio. No destino misto, a
    pessoa diz quanto é consumido no ato; o restante vai para o estoque — quem
    reparte é o domínio, para a conta sempre fechar.
    """

    descricao = forms.CharField(
        label="O que foi comprado",
        max_length=200,
        error_messages={"required": "Escreva o que foi comprado."},
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Como aparece no documento",
                "autocomplete": "off",
            }
        ),
    )
    unidade = forms.ChoiceField(
        label="Unidade",
        choices=UnidadeMedida.choices,
        initial=UnidadeMedida.UNIDADE,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    quantidade = forms.DecimalField(
        label="Quantidade",
        min_value=0,
        max_digits=14,
        decimal_places=3,
        error_messages={
            "required": "Informe a quantidade.",
            "min_value": "A quantidade precisa ser maior que zero.",
        },
        widget=forms.NumberInput(
            attrs={"class": "form-control", "step": "0.001", "min": "0"}
        ),
    )
    valor_unitario = forms.DecimalField(
        label="Valor unitário",
        required=False,
        min_value=0,
        max_digits=12,
        decimal_places=2,
        error_messages={"min_value": "O valor unitário não pode ser negativo."},
        widget=forms.NumberInput(
            attrs={"class": "form-control", "step": "0.01", "min": "0"}
        ),
    )
    destino = forms.ChoiceField(
        label="Destino",
        choices=DestinoDoItem.choices,
        initial=DestinoDoItem.ESTOQUE,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    item_estoque = forms.ModelChoiceField(
        label="Item do estoque",
        queryset=ItemEstoque.objects.none(),
        required=False,
        empty_label="Escolha o item",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    quantidade_uso_imediato = forms.DecimalField(
        label="Quanto é consumido no ato",
        required=False,
        min_value=0,
        max_digits=14,
        decimal_places=3,
        error_messages={"min_value": "A quantidade não pode ser negativa."},
        widget=forms.NumberInput(
            attrs={"class": "form-control", "step": "0.001", "min": "0"}
        ),
    )
    manutencao = forms.ModelChoiceField(
        label="Manutenção",
        queryset=Manutencao.objects.none(),
        required=False,
        empty_label="Sem manutenção",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    validade = forms.DateField(
        label="Validade",
        required=False,
        input_formats=["%Y-%m-%d", "%d/%m/%Y"],
        widget=forms.DateInput(
            attrs={"class": "form-control", "type": "date"}, format="%Y-%m-%d"
        ),
    )

    def __init__(self, *args, condominio=None, linha=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["item_estoque"].queryset = _escolhas_do_condominio(
            ItemEstoque, condominio, getattr(linha, "item_estoque", None)
        )
        self.fields["manutencao"].queryset = _manutencoes_do_condominio(
            condominio, getattr(linha, "manutencao", None)
        )

    def clean_descricao(self):
        descricao = " ".join((self.cleaned_data["descricao"] or "").split())
        if not descricao:
            raise forms.ValidationError("Escreva o que foi comprado.")
        return descricao

    def clean_quantidade(self):
        quantidade = self.cleaned_data["quantidade"]
        if quantidade <= 0:
            raise forms.ValidationError("A quantidade precisa ser maior que zero.")
        return quantidade

    def clean(self):
        """Recusa a linha que não diz para onde vai o que foi comprado."""
        dados = super().clean()
        destino = dados.get("destino")
        quantidade = dados.get("quantidade")
        item = dados.get("item_estoque")
        uso = dados.get("quantidade_uso_imediato")

        if destino and destino != DestinoDoItem.USO_IMEDIATO and item is None:
            self.add_error(
                "item_estoque",
                "Escolha o item de estoque que esta linha repõe — "
                "se ele ainda não existe, cadastre-o primeiro.",
            )

        if destino == DestinoDoItem.MISTO and quantidade and uso is not None:
            if uso <= 0 or uso >= quantidade:
                self.add_error(
                    "quantidade_uso_imediato",
                    "Informe quanto é consumido no ato — o restante vai para o "
                    "estoque.",
                )
        return dados


class FiltroDeItensForm(forms.Form):
    """Filtros essenciais da lista de itens. Nunca obrigatórios."""

    condominio = forms.ModelChoiceField(
        label="Condomínio",
        queryset=Condominio.objects.none(),
        required=False,
        empty_label="Todos os condomínios",
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    situacao = forms.ChoiceField(
        label="Situação",
        choices=FILTROS_DE_ITEM,
        required=False,
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    busca = forms.CharField(
        label="Buscar",
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control form-control-sm",
                "placeholder": "Item, categoria ou descrição",
                "autocomplete": "off",
            }
        ),
    )

    def __init__(self, *args, condominios=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["condominio"].queryset = (
            condominios if condominios is not None else Condominio.objects.none()
        )


class FiltroDeComprasForm(forms.Form):
    """Filtros essenciais da lista de compras. Nunca obrigatórios."""

    condominio = forms.ModelChoiceField(
        label="Condomínio",
        queryset=Condominio.objects.none(),
        required=False,
        empty_label="Todos os condomínios",
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    status = forms.ChoiceField(
        label="Situação",
        choices=(),
        required=False,
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    fornecedor = forms.ModelChoiceField(
        label="Fornecedor",
        queryset=Fornecedor.objects.none(),
        required=False,
        empty_label="Todos os fornecedores",
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    busca = forms.CharField(
        label="Buscar",
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control form-control-sm",
                "placeholder": "Documento, fornecedor ou observação",
                "autocomplete": "off",
            }
        ),
    )

    def __init__(self, *args, condominios=None, fornecedores=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["condominio"].queryset = (
            condominios if condominios is not None else Condominio.objects.none()
        )
        self.fields["status"].choices = [("", "Todas as situações")] + list(
            StatusCompra.choices
        )
        self.fields["fornecedor"].queryset = (
            fornecedores if fornecedores is not None else Fornecedor.objects.none()
        )
