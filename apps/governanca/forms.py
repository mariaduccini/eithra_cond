"""Formulários da governança.

Contrato, garantia e documento são registrados aos poucos: o essencial primeiro,
o resto quando a gestão souber. Por isso quase tudo é opcional, e o que é
obrigatório é o que dá sentido ao registro — o que foi contratado, o que a
garantia assegura, o título do documento.

Duas regras valem em todos:

- **O condomínio manda nas opções.** Fornecedor, equipamento, local, rotina,
  manutenção, item de compra, contrato, garantia e tipo só aparecem se forem do
  condomínio em vista. A lista vem do domínio, nunca do navegador.
- **Nada some em silêncio.** As opções novas vêm do que está ativo, mas o que já
  estava escolhido continua na lista mesmo que tenha sido desativado depois —
  abrir a tela para corrigir outra coisa não pode apagar um vínculo existente
  sem ninguém perceber.

Os vínculos — o que o contrato cobre, o que a garantia assegura, os contextos de
um documento — são escolhidos aqui e confirmados pelo domínio, em uma transação
só. A validação de coerência é a do modelo: não existe uma segunda lista de
regras no formulário para divergir com o tempo.
"""
from django import forms
from django.contrib.auth import get_user_model
from django.db.models import Q

from apps.cadastros.models import Equipamento, Fornecedor, LocalOperacional
from apps.condominios.models import AcessoUsuarioCondominio
from apps.estoque.models import ItemCompra
from apps.manutencoes.models import Manutencao
from apps.rotinas.models import RotinaRecorrente

from .constants import PeriodicidadeCobranca
from .models import (
    ContratoFornecedor,
    DocumentoOperacional,
    Garantia,
    TipoContrato,
    TipoDocumento,
)

# Aparência é assunto do tema Sneat: aqui só se diz qual classe de campo usar.
TEXTO = {"class": "form-control"}
SELECAO = {"class": "form-select"}
MULTIPLA = {"class": "form-select", "size": 6}
DATA = {"class": "form-control", "type": "date"}
INTEIRO = {"class": "form-control", "min": 0, "step": 1, "inputmode": "numeric"}
DINHEIRO = {"class": "form-control", "min": 0, "step": "0.01", "inputmode": "decimal"}


def _do_condominio(modelo, condominio):
    """Todos os registros deste condomínio. Sem condomínio, conjunto vazio."""
    if condominio is None or not getattr(condominio, "pk", None):
        return modelo.objects.none()
    return modelo.objects.filter(condominio=condominio)


def _escolhiveis(modelo, condominio, ids_escolhidos=()):
    """O que pode ser escolhido agora, mais o que já estava escolhido.

    Desativar um cadastro o retira das opções novas — mas não pode fazer
    desaparecer um vínculo que já existia no registro que a gestão está
    corrigindo.
    """
    escolhidos = [chave for chave in ids_escolhidos if chave is not None]
    return _do_condominio(modelo, condominio).filter(
        Q(ativo=True) | Q(pk__in=escolhidos)
    )


def _itens_de_compra(condominio, ids_escolhidos=()):
    """Os itens de compra do condomínio.

    O item de compra não tem condomínio próprio — ele pertence à compra, e é a
    compra que diz de qual condomínio ele é. Não há campo de ativo aqui: um item
    de compra de uma compra já registrada continua sendo o que foi comprado.
    """
    if condominio is None or not getattr(condominio, "pk", None):
        return ItemCompra.objects.none()
    return ItemCompra.objects.filter(compra__condominio=condominio)


def _pessoas_do_condominio(condominio):
    """Quem tem vínculo ativo com este condomínio, para responder por algo."""
    Usuario = get_user_model()
    if condominio is None or not getattr(condominio, "pk", None):
        return Usuario.objects.none()
    ids = AcessoUsuarioCondominio.objects.filter(
        condominio=condominio, ativo=True
    ).values_list("usuario_id", flat=True)
    return Usuario.objects.filter(pk__in=list(ids)).order_by("full_name")


class TipoContratoForm(forms.ModelForm):
    """A classificação que o condomínio dá aos seus contratos."""

    class Meta:
        model = TipoContrato
        fields = ("nome", "descricao")
        widgets = {
            "nome": forms.TextInput(
                attrs={**TEXTO, "placeholder": "Ex.: Manutenção preventiva, Seguro", "autocomplete": "off"}
            ),
            "descricao": forms.Textarea(
                attrs={**TEXTO, "rows": 3, "placeholder": "Detalhe opcional do que entra neste tipo"}
            ),
        }


class TipoDocumentoForm(forms.ModelForm):
    """A classificação dos documentos, com a regra de validade do tipo.

    A antecedência mora no tipo, e não em cada documento: é a mesma pergunta
    para todos os laudos do condomínio. Um tipo que exige validade obriga o
    documento a ter data de validade.
    """

    class Meta:
        model = TipoDocumento
        fields = (
            "nome",
            "exige_validade",
            "antecedencia_alerta_padrao_dias",
            "descricao",
        )
        widgets = {
            "nome": forms.TextInput(
                attrs={**TEXTO, "placeholder": "Ex.: Ata, Laudo, Certificado", "autocomplete": "off"}
            ),
            "exige_validade": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "antecedencia_alerta_padrao_dias": forms.NumberInput(attrs=INTEIRO),
            "descricao": forms.Textarea(
                attrs={**TEXTO, "rows": 3, "placeholder": "Detalhe opcional do que entra neste tipo"}
            ),
        }


class ContratoForm(forms.ModelForm):
    """O contrato e o que ele cobre, registrados juntos.

    O fornecedor é obrigatório: um contrato sem fornecedor não diz com quem o
    condomínio contratou. O resto pode chegar depois — prazo indeterminado é um
    contrato válido, e valor é informação operacional, não contábil.
    """

    equipamentos = forms.ModelMultipleChoiceField(
        label="Equipamentos cobertos",
        queryset=Equipamento.objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs=MULTIPLA),
    )
    locais = forms.ModelMultipleChoiceField(
        label="Locais cobertos",
        queryset=LocalOperacional.objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs=MULTIPLA),
    )
    rotinas = forms.ModelMultipleChoiceField(
        label="Preventivas cobertas",
        queryset=RotinaRecorrente.objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs=MULTIPLA),
    )

    class Meta:
        model = ContratoFornecedor
        fields = (
            "fornecedor",
            "tipo_contrato",
            "numero_referencia",
            "objeto",
            "descricao",
            "data_inicio",
            "data_fim",
            "valor",
            "periodicidade_cobranca",
            "renovacao_automatica",
            "antecedencia_alerta_dias",
            "prazo_aviso_rescisao_dias",
            "sla_dias",
            "data_proximo_reajuste",
            "indice_reajuste_texto",
            "responsavel_interno",
            "observacao",
        )
        widgets = {
            "fornecedor": forms.Select(attrs=SELECAO),
            "tipo_contrato": forms.Select(attrs=SELECAO),
            "numero_referencia": forms.TextInput(attrs={**TEXTO, "autocomplete": "off"}),
            "objeto": forms.TextInput(
                attrs={**TEXTO, "placeholder": "O que foi contratado, em uma linha", "autocomplete": "off"}
            ),
            "descricao": forms.Textarea(
                attrs={**TEXTO, "rows": 3, "placeholder": "O que mais importa saber deste contrato"}
            ),
            "data_inicio": forms.DateInput(attrs=DATA),
            "data_fim": forms.DateInput(attrs=DATA),
            "valor": forms.NumberInput(attrs=DINHEIRO),
            "periodicidade_cobranca": forms.Select(
                attrs=SELECAO, choices=PeriodicidadeCobranca.choices
            ),
            "renovacao_automatica": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "antecedencia_alerta_dias": forms.NumberInput(attrs=INTEIRO),
            "prazo_aviso_rescisao_dias": forms.NumberInput(attrs=INTEIRO),
            "sla_dias": forms.NumberInput(attrs=INTEIRO),
            "data_proximo_reajuste": forms.DateInput(attrs=DATA),
            "indice_reajuste_texto": forms.TextInput(
                attrs={**TEXTO, "placeholder": "IGP-M, IPCA…", "autocomplete": "off"}
            ),
            "responsavel_interno": forms.Select(attrs=SELECAO),
            "observacao": forms.Textarea(
                attrs={**TEXTO, "rows": 3, "placeholder": "O que se sabe e não cabe nos outros campos"}
            ),
        }

    def __init__(self, *args, condominio=None, **kwargs):
        super().__init__(*args, **kwargs)
        contrato = self.instance

        self.fields["fornecedor"].queryset = _escolhiveis(
            Fornecedor,
            condominio,
            [contrato.fornecedor_id] if contrato.pk else (),
        )
        self.fields["fornecedor"].empty_label = "Escolha o fornecedor"
        self.fields["tipo_contrato"].queryset = _escolhiveis(
            TipoContrato,
            condominio,
            [contrato.tipo_contrato_id] if contrato.pk else (),
        )
        self.fields["tipo_contrato"].empty_label = "Sem tipo definido"
        self.fields["responsavel_interno"].queryset = _pessoas_do_condominio(condominio)
        self.fields["responsavel_interno"].empty_label = "Sem responsável definido"

        # Prazo indeterminado, valor não informado e ausência de reajuste são
        # respostas legítimas: sem isto, o formulário obrigaria a preencher.
        for campo in (
            "tipo_contrato",
            "data_fim",
            "valor",
            "prazo_aviso_rescisao_dias",
            "sla_dias",
            "data_proximo_reajuste",
            "responsavel_interno",
        ):
            self.fields[campo].required = False

        ids_equipamentos = (
            list(contrato.equipamentos_vinculados.values_list("equipamento_id", flat=True))
            if contrato.pk
            else []
        )
        ids_locais = (
            list(contrato.locais_vinculados.values_list("local_id", flat=True))
            if contrato.pk
            else []
        )
        ids_rotinas = (
            list(contrato.rotinas_vinculadas.values_list("rotina_id", flat=True))
            if contrato.pk
            else []
        )

        self.fields["equipamentos"].queryset = _escolhiveis(
            Equipamento, condominio, ids_equipamentos
        )
        self.fields["locais"].queryset = _escolhiveis(
            LocalOperacional, condominio, ids_locais
        )
        self.fields["rotinas"].queryset = _escolhiveis(
            RotinaRecorrente, condominio, ids_rotinas
        )

        if not self.is_bound and contrato.pk:
            # O que já está coberto aparece marcado: corrigir o contrato não
            # pode começar com a lista de vínculos em branco.
            self.initial["equipamentos"] = ids_equipamentos
            self.initial["locais"] = ids_locais
            self.initial["rotinas"] = ids_rotinas


class GarantiaForm(forms.ModelForm):
    """A garantia e o que ela assegura, registrados juntos.

    Uma garantia em uso precisa dizer o que cobre — pelo menos um equipamento,
    uma manutenção ou um item de compra. A regra é do domínio, e é ele que
    recusa a operação que terminaria com uma garantia ativa sem cobertura.
    """

    equipamentos = forms.ModelMultipleChoiceField(
        label="Equipamentos cobertos",
        queryset=Equipamento.objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs=MULTIPLA),
    )
    manutencoes = forms.ModelMultipleChoiceField(
        label="Manutenções cobertas",
        queryset=Manutencao.objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs=MULTIPLA),
    )
    itens_de_compra = forms.ModelMultipleChoiceField(
        label="Itens de compra cobertos",
        queryset=ItemCompra.objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs=MULTIPLA),
    )

    class Meta:
        model = Garantia
        fields = (
            "fornecedor",
            "descricao",
            "numero_referencia",
            "data_inicio",
            "data_fim",
            "antecedencia_alerta_dias",
            "observacao",
        )
        widgets = {
            "fornecedor": forms.Select(attrs=SELECAO),
            "descricao": forms.TextInput(
                attrs={**TEXTO, "placeholder": "O que esta garantia assegura", "autocomplete": "off"}
            ),
            "numero_referencia": forms.TextInput(attrs={**TEXTO, "autocomplete": "off"}),
            "data_inicio": forms.DateInput(attrs=DATA),
            "data_fim": forms.DateInput(attrs=DATA),
            "antecedencia_alerta_dias": forms.NumberInput(attrs=INTEIRO),
            "observacao": forms.Textarea(
                attrs={**TEXTO, "rows": 3, "placeholder": "O que se sabe e não cabe nos outros campos"}
            ),
        }

    def __init__(self, *args, condominio=None, **kwargs):
        super().__init__(*args, **kwargs)
        garantia = self.instance

        self.fields["fornecedor"].queryset = _escolhiveis(
            Fornecedor,
            condominio,
            [garantia.fornecedor_id] if garantia.pk else (),
        )
        self.fields["fornecedor"].empty_label = "Sem fornecedor definido"
        self.fields["fornecedor"].required = False
        self.fields["data_fim"].required = False

        ids_equipamentos = (
            list(garantia.equipamentos.values_list("equipamento_id", flat=True))
            if garantia.pk
            else []
        )
        ids_manutencoes = (
            list(garantia.manutencoes.values_list("manutencao_id", flat=True))
            if garantia.pk
            else []
        )
        ids_itens = (
            list(garantia.itens_de_compra.values_list("item_compra_id", flat=True))
            if garantia.pk
            else []
        )

        self.fields["equipamentos"].queryset = _escolhiveis(
            Equipamento, condominio, ids_equipamentos
        )
        self.fields["manutencoes"].queryset = _do_condominio(Manutencao, condominio)
        self.fields["itens_de_compra"].queryset = _itens_de_compra(
            condominio, ids_itens
        )

        if not self.is_bound and garantia.pk:
            self.initial["equipamentos"] = ids_equipamentos
            self.initial["manutencoes"] = ids_manutencoes
            self.initial["itens_de_compra"] = ids_itens


class DocumentoForm(forms.ModelForm):
    """O documento e os contextos em que ele vale, registrados juntos.

    O mesmo arquivo pode valer para um contrato e para um equipamento ao mesmo
    tempo, e não é guardado duas vezes. Sem vínculo nenhum o documento continua
    válido: uma ata ou um regimento pertencem só ao condomínio.
    """

    fornecedores = forms.ModelMultipleChoiceField(
        label="Fornecedores relacionados",
        queryset=Fornecedor.objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs=MULTIPLA),
    )
    contratos = forms.ModelMultipleChoiceField(
        label="Contratos relacionados",
        queryset=ContratoFornecedor.objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs=MULTIPLA),
    )
    equipamentos = forms.ModelMultipleChoiceField(
        label="Equipamentos relacionados",
        queryset=Equipamento.objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs=MULTIPLA),
    )
    garantias = forms.ModelMultipleChoiceField(
        label="Garantias relacionadas",
        queryset=Garantia.objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs=MULTIPLA),
    )

    class Meta:
        model = DocumentoOperacional
        fields = (
            "tipo_documento",
            "titulo",
            "numero_referencia",
            "data_emissao",
            "data_validade",
            "emissor",
            "observacao",
        )
        widgets = {
            "tipo_documento": forms.Select(attrs=SELECAO),
            "titulo": forms.TextInput(
                attrs={**TEXTO, "placeholder": "Como este documento é conhecido", "autocomplete": "off"}
            ),
            "numero_referencia": forms.TextInput(attrs={**TEXTO, "autocomplete": "off"}),
            "data_emissao": forms.DateInput(attrs=DATA),
            "data_validade": forms.DateInput(attrs=DATA),
            "emissor": forms.TextInput(
                attrs={**TEXTO, "placeholder": "Quem emitiu, quando não for um fornecedor", "autocomplete": "off"}
            ),
            "observacao": forms.Textarea(
                attrs={**TEXTO, "rows": 3, "placeholder": "O que se sabe e não cabe nos outros campos"}
            ),
        }

    def __init__(self, *args, condominio=None, **kwargs):
        super().__init__(*args, **kwargs)
        documento = self.instance

        self.fields["tipo_documento"].queryset = _escolhiveis(
            TipoDocumento,
            condominio,
            [documento.tipo_documento_id] if documento.pk else (),
        )
        self.fields["tipo_documento"].empty_label = "Escolha o tipo"

        for campo in ("data_emissao", "data_validade"):
            self.fields[campo].required = False

        ids_fornecedores = (
            list(documento.fornecedores_vinculados.values_list("fornecedor_id", flat=True))
            if documento.pk
            else []
        )
        ids_contratos = (
            list(documento.contratos_vinculados.values_list("contrato_id", flat=True))
            if documento.pk
            else []
        )
        ids_equipamentos = (
            list(documento.equipamentos_vinculados.values_list("equipamento_id", flat=True))
            if documento.pk
            else []
        )
        ids_garantias = (
            list(documento.garantias_vinculadas.values_list("garantia_id", flat=True))
            if documento.pk
            else []
        )

        self.fields["fornecedores"].queryset = _escolhiveis(
            Fornecedor, condominio, ids_fornecedores
        )
        self.fields["contratos"].queryset = _escolhiveis(
            ContratoFornecedor, condominio, ids_contratos
        )
        self.fields["equipamentos"].queryset = _escolhiveis(
            Equipamento, condominio, ids_equipamentos
        )
        self.fields["garantias"].queryset = _escolhiveis(
            Garantia, condominio, ids_garantias
        )

        if not self.is_bound and documento.pk:
            self.initial["fornecedores"] = ids_fornecedores
            self.initial["contratos"] = ids_contratos
            self.initial["equipamentos"] = ids_equipamentos
            self.initial["garantias"] = ids_garantias
