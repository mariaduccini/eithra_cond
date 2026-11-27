"""Formulários da entrada e da correção do balanço.

Na entrada, pede-se apenas o que o sistema ainda não sabe: qual condomínio e qual
arquivo. Nada de mês, fornecedor, categoria, observação ou valores — a
competência será lida do próprio documento, mais adiante. Pedir agora seria
trabalho para a gestão sem retorno.

Na correção, pede-se o que a gestão decidiu e por quê. Toda correção exige
justificativa, e os campos de categoria e natureza saem do catálogo do Eithra:
não se digita categoria livre, para que os meses continuem comparáveis.
"""
from decimal import Decimal

from django import forms

from apps.balancos.categorias import CategoriaEithra
from apps.balancos.constants import TipoVinculoEvidencia
from apps.balancos.correcoes import LIMITE_DA_JUSTIFICATIVA
from apps.balancos.evidencias import LIMITE_DA_LEGENDA, ErroDeEvidencia, validar_foto
from apps.balancos.natureza_despesa import NaturezaDespesa
from apps.balancos.normalizadores import texto_para_decimal
from apps.balancos.services import ErroDeValidacaoDeArquivo, validar_arquivo
from apps.condominios.models import Condominio

# Valor nenhum faz sentido abaixo disto: uma despesa de zero não é despesa.
ZERO = Decimal("0.00")


class ImportacaoBalancoForm(forms.Form):
    """Recebe o PDF do balanço.

    O conjunto de condomínios possíveis vem do vínculo ativo da pessoa, e não
    do que o navegador enviar. Quando há mais de um, a escolha é dela; quando
    há apenas um, o campo continua sendo validado, mas não é pedido de novo.
    """

    condominio = forms.ModelChoiceField(
        label="Condomínio",
        queryset=Condominio.objects.none(),
        empty_label=None,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    arquivo = forms.FileField(
        label="Arquivo do balanço (PDF)",
        widget=forms.ClearableFileInput(
            attrs={"class": "form-control", "accept": "application/pdf"}
        ),
    )

    def __init__(self, *args, condominios=None, **kwargs):
        super().__init__(*args, **kwargs)

        permitidos = condominios if condominios is not None else Condominio.objects.none()
        campo = self.fields["condominio"]
        campo.queryset = permitidos

        elegiveis = list(permitidos)
        if len(elegiveis) == 1:
            campo.initial = elegiveis[0].pk
            campo.widget = forms.HiddenInput()

    def clean_arquivo(self):
        arquivo = self.cleaned_data["arquivo"]
        try:
            validar_arquivo(arquivo)
        except ErroDeValidacaoDeArquivo as erro:
            raise forms.ValidationError(str(erro)) from erro
        return arquivo


# -- Conferência antes de publicar ----------------------------------------------
#
# A tela única é onde a gestão confere o que foi lido — ou digita o mês que não
# tem PDF — antes de o balanço passar a valer. Tudo é editável: os totais e as
# linhas. Duas conferências automáticas ajudam, mas não trancam a porta: a
# decisão de publicar continua sendo de quem responde pelo mês.

NOMES_DOS_MESES = (
    (1, "Janeiro"),
    (2, "Fevereiro"),
    (3, "Março"),
    (4, "Abril"),
    (5, "Maio"),
    (6, "Junho"),
    (7, "Julho"),
    (8, "Agosto"),
    (9, "Setembro"),
    (10, "Outubro"),
    (11, "Novembro"),
    (12, "Dezembro"),
)


def _valor_do_campo(texto, *, rotulo: str, obrigatorio: bool):
    """Lê um valor digitado pela gestão; devolve ``None`` quando ele é opcional.

    O obrigatório é o total do mês: sem receitas e sem despesas não há o que
    publicar. Saldos em branco continuam sendo ausência — e ausência é resposta
    honesta, não zero inventado.
    """
    valor = texto_para_decimal(texto) if (texto or "").strip() else None
    if valor is None:
        if obrigatorio:
            raise forms.ValidationError(f"Informe {rotulo} em reais, como 1.234,56.")
        return None
    if obrigatorio and valor <= ZERO:
        raise forms.ValidationError(f"{rotulo.capitalize()} precisa ser maior que zero.")
    return valor


class ConferenciaDeBalancoForm(forms.Form):
    """Os valores que a gestão confere antes de o mês passar a valer.

    Vale para o balancete antigo conferido e para o mês lançado sem PDF. As
    linhas de receitas e despesas viajam como listas à parte — são opcionais, e
    a tela as pareia antes de entregar ao domínio.
    """

    condominio = forms.ModelChoiceField(
        label="Condomínio",
        queryset=Condominio.objects.none(),
        empty_label=None,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    mes = forms.ChoiceField(
        label="Mês",
        choices=NOMES_DOS_MESES,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    ano = forms.IntegerField(
        label="Ano",
        min_value=2000,
        max_value=2100,
        widget=forms.NumberInput(attrs={"class": "form-control", "inputmode": "numeric"}),
    )
    saldo_anterior = forms.CharField(
        label="Saldo anterior",
        required=False,
        widget=forms.TextInput(
            attrs={"class": "form-control", "inputmode": "decimal", "placeholder": "0,00"}
        ),
    )
    receitas_total = forms.CharField(
        label="Total de receitas",
        error_messages={"required": "Informe o total de receitas do mês."},
        widget=forms.TextInput(
            attrs={"class": "form-control", "inputmode": "decimal", "placeholder": "0,00"}
        ),
    )
    despesas_total = forms.CharField(
        label="Total de despesas",
        error_messages={"required": "Informe o total de despesas do mês."},
        widget=forms.TextInput(
            attrs={"class": "form-control", "inputmode": "decimal", "placeholder": "0,00"}
        ),
    )
    saldo_final = forms.CharField(
        label="Saldo final",
        required=False,
        widget=forms.TextInput(
            attrs={"class": "form-control", "inputmode": "decimal", "placeholder": "0,00"}
        ),
    )

    def __init__(self, *args, condominios=None, **kwargs):
        super().__init__(*args, **kwargs)

        permitidos = condominios if condominios is not None else Condominio.objects.none()
        campo = self.fields["condominio"]
        campo.queryset = permitidos

        elegiveis = list(permitidos)
        if len(elegiveis) == 1:
            campo.initial = elegiveis[0].pk
            campo.widget = forms.HiddenInput()

    def clean_saldo_anterior(self):
        return _valor_do_campo(
            self.cleaned_data.get("saldo_anterior"),
            rotulo="o saldo anterior",
            obrigatorio=False,
        )

    def clean_receitas_total(self):
        return _valor_do_campo(
            self.cleaned_data.get("receitas_total"),
            rotulo="o total de receitas",
            obrigatorio=True,
        )

    def clean_despesas_total(self):
        return _valor_do_campo(
            self.cleaned_data.get("despesas_total"),
            rotulo="o total de despesas",
            obrigatorio=True,
        )

    def clean_saldo_final(self):
        return _valor_do_campo(
            self.cleaned_data.get("saldo_final"),
            rotulo="o saldo final",
            obrigatorio=False,
        )


# -- Correção do detalhamento ---------------------------------------------------
#
# A correção é uma decisão da gestão sobre o que a leitura trouxe. Os campos de
# categoria e natureza saem do catálogo do Eithra — não se digita categoria livre
# —, a justificativa é obrigatória, e o valor é sempre ``Decimal``: dinheiro não é
# número de ponto flutuante.


class _FormularioComJustificativa(forms.Form):
    """Base das correções: toda decisão pede justificativa e a versão do mês."""

    versao = forms.CharField(required=False, widget=forms.HiddenInput())
    justificativa = forms.CharField(
        label="Justificativa",
        max_length=LIMITE_DA_JUSTIFICATIVA,
        error_messages={"required": "A correção precisa de uma justificativa."},
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2}),
    )

    def clean_justificativa(self):
        texto = " ".join(self.cleaned_data["justificativa"].split())
        if not texto:
            raise forms.ValidationError("A correção precisa de uma justificativa.")
        return texto


class _FormularioDoLancamento(_FormularioComJustificativa):
    """Campos que descrevem um lançamento, lidos ou decididos à mão."""

    descricao = forms.CharField(
        label="Descrição",
        max_length=300,
        error_messages={"required": "Informe a descrição do lançamento."},
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    valor = forms.CharField(
        label="Valor",
        error_messages={"required": "Informe o valor do lançamento."},
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "inputmode": "decimal",
                "autocomplete": "off",
                "placeholder": "0,00",
            }
        ),
    )
    categoria = forms.ChoiceField(
        label="Categoria",
        choices=CategoriaEithra.choices,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    natureza = forms.ChoiceField(
        label="Natureza",
        choices=NaturezaDespesa.choices,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    grupo = forms.ChoiceField(
        label="Grupo",
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    def __init__(self, *args, grupos=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["grupo"].choices = [("", "Sem grupo")] + [
            (nome, nome) for nome in grupos
        ]

    def clean_descricao(self):
        texto = " ".join(self.cleaned_data["descricao"].split())
        if not texto:
            raise forms.ValidationError("Informe a descrição do lançamento.")
        return texto

    def clean_valor(self):
        """Lê o valor como a gestão o escreve, e não como o banco o guarda.

        ``1.234,56``, ``R$ 1.234,56`` e ``1234.56`` são o mesmo número. A
        conversão é a mesma que o resto do sistema usa para dinheiro — quem
        digita não deve traduzir o formato.
        """
        valor = texto_para_decimal(self.cleaned_data["valor"])
        if valor is None:
            raise forms.ValidationError("Informe um valor em reais, como 1.234,56.")
        if valor <= ZERO:
            raise forms.ValidationError("O valor precisa ser maior que zero.")
        return valor


class CorrecaoDeLancamentoForm(_FormularioDoLancamento):
    """Corrige um lançamento já lido, alcançado pelo seu identificador estável."""

    lancamento_id = forms.CharField(widget=forms.HiddenInput())


class LancamentoManualForm(_FormularioDoLancamento):
    """Acrescenta um lançamento que o documento não trouxe."""


class MarcarNaoDespesaForm(_FormularioComJustificativa):
    """Tira um lançamento do detalhamento, guardando o porquê."""

    lancamento_id = forms.CharField(widget=forms.HiddenInput())


class ClassificarDespesaForm(forms.Form):
    """Confirma a categoria de uma linha em aberto, em um toque.

    A categoria vem do próprio botão da tela — a sugestão, ou o chip que a
    gestão escolheu —, e a justificativa é do sistema, não de quem clica: a
    decisão aqui é sobre a categoria, e o registro de por quê fica com a tela.
    Nenhum valor é editado por aqui: a confirmação só classifica.
    """

    lancamento_id = forms.CharField(widget=forms.HiddenInput())
    versao = forms.CharField(required=False, widget=forms.HiddenInput())
    categoria = forms.ChoiceField(choices=CategoriaEithra.choices)


# -- Evidência visual -----------------------------------------------------------
#
# A foto explica um lançamento, uma categoria ou o destaque do mês. O vínculo
# viaja em campos escondidos porque a tela já sabe a que a foto se refere — mas
# ele não é acreditado por si: o domínio confere se aquele lançamento ou aquela
# categoria existem neste balanço antes de guardar qualquer coisa.


class EvidenciaVisualForm(forms.Form):
    """Recebe a foto, a legenda curta e a decisão sobre exibi-la aos moradores."""

    tipo_vinculo = forms.ChoiceField(
        choices=TipoVinculoEvidencia.choices,
        widget=forms.HiddenInput(),
    )
    referencia = forms.CharField(required=False, widget=forms.HiddenInput())
    imagem = forms.FileField(
        label="Foto",
        error_messages={"required": "Escolha a foto que explica este gasto."},
        widget=forms.ClearableFileInput(
            attrs={"class": "form-control", "accept": "image/jpeg,image/png,image/webp"}
        ),
    )
    legenda = forms.CharField(
        label="Legenda",
        required=False,
        max_length=LIMITE_DA_LEGENDA,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Ex.: Substituição do equipamento da área comum",
                "autocomplete": "off",
            }
        ),
    )
    exibir_aos_moradores = forms.BooleanField(
        label="Exibir aos moradores",
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    def clean_imagem(self):
        """Confere a foto pelo conteúdo real, antes de qualquer gravação."""
        arquivo = self.cleaned_data["imagem"]
        try:
            validar_foto(arquivo)
        except ErroDeEvidencia as erro:
            raise forms.ValidationError(str(erro)) from erro
        return arquivo

    def clean_legenda(self):
        return " ".join((self.cleaned_data["legenda"] or "").split())


class EdicaoDeEvidenciaForm(forms.Form):
    """Muda a legenda e a decisão de exibir. A foto em si não é trocada."""

    legenda = forms.CharField(
        label="Legenda",
        required=False,
        max_length=LIMITE_DA_LEGENDA,
        widget=forms.TextInput(attrs={"class": "form-control", "autocomplete": "off"}),
    )
    exibir_aos_moradores = forms.BooleanField(
        label="Exibir aos moradores",
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    def clean_legenda(self):
        return " ".join((self.cleaned_data["legenda"] or "").split())
