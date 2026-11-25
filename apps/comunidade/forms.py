"""Formulários do cadastro de comunidade.

Cada formulário pede apenas o que o cadastro precisa: a identificação da
unidade, o nome da pessoa, a placa do veículo. O resto é opcional e pode ser
completado aos poucos.

Os campos são os do próprio cadastro. Assim a validação é a mesma do domínio —
o modelo recusa o que não faz sentido — e não existe uma segunda lista de
regras no formulário para divergir com o tempo.

Sobre os vínculos: um vínculo liga duas pontas, e a tela sempre conhece uma
delas — a unidade aberta, a pessoa aberta, o veículo aberto. A ponta conhecida
vem por parâmetro, fica desabilitada no formulário e é preenchida pelo domínio;
só a outra ponta é escolhida.
"""
from django import forms

from apps.cadastros.constants import TipoPessoa

from .models import (
    Pessoa,
    Unidade,
    Veiculo,
    VinculoPessoaUnidade,
    VinculoVeiculoPessoa,
    VinculoVeiculoUnidade,
)

# Recorte de entrada de texto, repetido em quase todo campo. Aparência é
# assunto do tema Sneat: aqui só se diz qual classe de campo usar.
TEXTO = {"class": "form-control"}
SELECAO = {"class": "form-select"}
DATA = {"class": "form-control", "type": "date"}


def _do_condominio(modelo, condominio):
    """Todos os registros deste condomínio. Sem condomínio, conjunto vazio."""
    if condominio is None or not getattr(condominio, "pk", None):
        return modelo.objects.none()
    return modelo.objects.filter(condominio=condominio)


def _unidades_ativas(condominio):
    """As unidades deste condomínio que ainda estão em uso."""
    return _do_condominio(Unidade, condominio).filter(ativa=True).order_by(
        "bloco_torre", "identificador"
    )


def _pessoas_ativas(condominio):
    """As pessoas deste condomínio que ainda estão em uso."""
    return _do_condominio(Pessoa, condominio).filter(ativa=True).order_by("nome")


def _veiculos_ativos(condominio):
    """Os veículos deste condomínio que ainda circulam."""
    return _do_condominio(Veiculo, condominio).filter(ativo=True).order_by("placa")


def _fixar(campo, objeto):
    """Deixa uma ponta do vínculo conhecida, mostrada e não editável."""
    campo.queryset = campo.queryset.model.objects.filter(pk=objeto.pk)
    campo.initial = objeto.pk
    campo.disabled = True


class UnidadeForm(forms.ModelForm):
    """Cadastro de uma unidade do condomínio."""

    class Meta:
        model = Unidade
        fields = (
            "identificador",
            "bloco_torre",
            "andar",
            "complemento",
            "observacao_interna",
        )
        widgets = {
            "identificador": forms.TextInput(
                attrs={
                    **TEXTO,
                    "placeholder": "Ex.: 101, A-101, Casa 03, Loja 02",
                    "autocomplete": "off",
                }
            ),
            "bloco_torre": forms.TextInput(
                attrs={**TEXTO, "placeholder": "Ex.: Bloco A, Torre 2", "autocomplete": "off"}
            ),
            "andar": forms.TextInput(
                attrs={**TEXTO, "placeholder": "Ex.: 10, Térreo", "autocomplete": "off"}
            ),
            "complemento": forms.TextInput(attrs={**TEXTO, "autocomplete": "off"}),
            "observacao_interna": forms.Textarea(
                attrs={**TEXTO, "rows": 2, "placeholder": "Anotação interna da gestão"}
            ),
        }

    def __init__(self, *args, condominio=None, **kwargs):
        super().__init__(*args, **kwargs)
        # O condomínio não é campo do formulário — quem decide é o vínculo da
        # pessoa, na view. Mas o cadastro precisa saber a que condomínio
        # pertence para o domínio recusar, na própria validação, uma
        # identificação que já existe ali.
        if condominio is not None:
            self.instance.condominio = condominio


class PessoaForm(forms.ModelForm):
    """Cadastro de uma pessoa do condomínio.

    Não há campo de conta aqui: ligar a pessoa a uma conta no Eithra é uma
    decisão à parte, e ter conta não faz ninguém morar em lugar nenhum.
    """

    class Meta:
        model = Pessoa
        fields = (
            "tipo_pessoa",
            "nome",
            "nome_social",
            "telefone",
            "whatsapp",
            "aceita_avisos_whatsapp",
            "email",
            "documento",
            "observacao_interna",
        )
        widgets = {
            "tipo_pessoa": forms.Select(attrs=SELECAO, choices=TipoPessoa.choices),
            "nome": forms.TextInput(
                attrs={
                    **TEXTO,
                    "placeholder": "Nome completo ou razão social",
                    "autocomplete": "off",
                }
            ),
            "nome_social": forms.TextInput(
                attrs={**TEXTO, "placeholder": "Como prefere ser chamada", "autocomplete": "off"}
            ),
            "telefone": forms.TextInput(
                attrs={**TEXTO, "placeholder": "(00) 00000-0000", "autocomplete": "off"}
            ),
            "whatsapp": forms.TextInput(
                attrs={**TEXTO, "placeholder": "(00) 00000-0000", "autocomplete": "off"}
            ),
            "aceita_avisos_whatsapp": forms.CheckboxInput(
                attrs={"class": "form-check-input"}
            ),
            "email": forms.EmailInput(attrs={**TEXTO, "autocomplete": "off"}),
            "documento": forms.TextInput(
                attrs={**TEXTO, "placeholder": "CPF ou CNPJ", "autocomplete": "off"}
            ),
            "observacao_interna": forms.Textarea(
                attrs={**TEXTO, "rows": 2, "placeholder": "Anotação interna da gestão"}
            ),
        }

    def __init__(self, *args, condominio=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Não saber se é pessoa física ou jurídica é comum no primeiro contato:
        # quem não escolhe fica com "Não informado", o padrão do domínio.
        self.fields["tipo_pessoa"].required = False
        self.fields["tipo_pessoa"].choices = [("", "Não informado")] + list(
            TipoPessoa.choices
        )
        if condominio is not None:
            self.instance.condominio = condominio

    def clean_tipo_pessoa(self):
        return self.cleaned_data.get("tipo_pessoa") or TipoPessoa.NAO_INFORMADO


class VeiculoForm(forms.ModelForm):
    """Cadastro de um veículo que circula pelo condomínio."""

    class Meta:
        model = Veiculo
        fields = ("placa", "marca", "modelo", "cor", "observacao_interna")
        widgets = {
            "placa": forms.TextInput(
                attrs={
                    **TEXTO,
                    "placeholder": "Ex.: ABC1234 ou ABC1D23",
                    "autocomplete": "off",
                }
            ),
            "marca": forms.TextInput(attrs={**TEXTO, "autocomplete": "off"}),
            "modelo": forms.TextInput(attrs={**TEXTO, "autocomplete": "off"}),
            "cor": forms.TextInput(attrs={**TEXTO, "autocomplete": "off"}),
            "observacao_interna": forms.Textarea(
                attrs={**TEXTO, "rows": 2, "placeholder": "Anotação interna da gestão"}
            ),
        }

    def __init__(self, *args, condominio=None, **kwargs):
        super().__init__(*args, **kwargs)
        if condominio is not None:
            self.instance.condominio = condominio


class VinculoPessoaUnidadeForm(forms.ModelForm):
    """Liga uma pessoa a uma unidade, com o papel e a data de início quando conhecida.

    Uma das pontas já é conhecida pela tela e chega por parâmetro; a outra é
    escolhida entre os cadastros ativos do condomínio.
    """

    class Meta:
        model = VinculoPessoaUnidade
        fields = (
            "pessoa",
            "unidade",
            "tipo_vinculo",
            "data_inicio",
            "data_fim",
            "observacao_interna",
        )
        widgets = {
            "pessoa": forms.Select(attrs=SELECAO),
            "unidade": forms.Select(attrs=SELECAO),
            "tipo_vinculo": forms.Select(attrs=SELECAO),
            "data_inicio": forms.DateInput(attrs=DATA),
            "data_fim": forms.DateInput(attrs=DATA),
            "observacao_interna": forms.Textarea(attrs={**TEXTO, "rows": 2}),
        }

    def __init__(self, *args, condominio=None, pessoa=None, unidade=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["pessoa"].queryset = _pessoas_ativas(condominio)
        self.fields["unidade"].queryset = _unidades_ativas(condominio)
        self.fields["data_inicio"].required = False
        self.fields["data_inicio"].help_text = (
            "Deixe em branco se a data histórica de início não for conhecida."
        )
        self.fields["data_fim"].required = False
        if pessoa is not None:
            _fixar(self.fields["pessoa"], pessoa)
        if unidade is not None:
            _fixar(self.fields["unidade"], unidade)
        if condominio is not None:
            self.instance.condominio = condominio


class TransferirUnidadeForm(forms.ModelForm):
    """Muda a pessoa de unidade, encerrando o vínculo por onde ela estava.

    Não é um formulário do vínculo: o que ele pede é só o destino, porque o
    papel e a ponta de origem vêm do próprio vínculo que a gestão mandou mudar.
    Pedir o papel de novo aqui daria margem a trocá-lo sem querer no meio de
    uma mudança de endereço.
    """

    class Meta:
        model = VinculoPessoaUnidade
        fields = ("unidade",)
        widgets = {"unidade": forms.Select(attrs=SELECAO)}

    def __init__(self, *args, condominio=None, vinculo=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["unidade"].label = "Nova unidade"
        self.fields["unidade"].queryset = _unidades_ativas(condominio)
        if vinculo is not None:
            # A unidade de origem não é destino possível: mudar para onde já se
            # está não é mudança.
            self.fields["unidade"].queryset = self.fields["unidade"].queryset.exclude(
                pk=vinculo.unidade_id
            )


class VinculoVeiculoPessoaForm(forms.ModelForm):
    """Registra quem utiliza um veículo."""

    class Meta:
        model = VinculoVeiculoPessoa
        fields = ("veiculo", "pessoa", "data_inicio", "data_fim", "observacao")
        widgets = {
            "veiculo": forms.Select(attrs=SELECAO),
            "pessoa": forms.Select(attrs=SELECAO),
            "data_inicio": forms.DateInput(attrs=DATA),
            "data_fim": forms.DateInput(attrs=DATA),
            "observacao": forms.Textarea(attrs={**TEXTO, "rows": 2}),
        }

    def __init__(self, *args, condominio=None, veiculo=None, pessoa=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["veiculo"].queryset = _veiculos_ativos(condominio)
        self.fields["pessoa"].queryset = _pessoas_ativas(condominio)
        self.fields["data_inicio"].required = False
        self.fields["data_inicio"].help_text = (
            "Deixe em branco se a data histórica de início não for conhecida."
        )
        self.fields["data_fim"].required = False
        if veiculo is not None:
            _fixar(self.fields["veiculo"], veiculo)
        if pessoa is not None:
            _fixar(self.fields["pessoa"], pessoa)
        if condominio is not None:
            self.instance.condominio = condominio


class VinculoVeiculoUnidadeForm(forms.ModelForm):
    """Relaciona um veículo a uma unidade.

    É uma pergunta diferente de "quem utiliza este veículo": registrar uma não
    responde pela outra.
    """

    class Meta:
        model = VinculoVeiculoUnidade
        fields = ("veiculo", "unidade", "data_inicio", "data_fim", "observacao")
        widgets = {
            "veiculo": forms.Select(attrs=SELECAO),
            "unidade": forms.Select(attrs=SELECAO),
            "data_inicio": forms.DateInput(attrs=DATA),
            "data_fim": forms.DateInput(attrs=DATA),
            "observacao": forms.Textarea(attrs={**TEXTO, "rows": 2}),
        }

    def __init__(self, *args, condominio=None, veiculo=None, unidade=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["veiculo"].queryset = _veiculos_ativos(condominio)
        self.fields["unidade"].queryset = _unidades_ativas(condominio)
        self.fields["data_inicio"].required = False
        self.fields["data_inicio"].help_text = (
            "Deixe em branco se a data histórica de início não for conhecida."
        )
        self.fields["data_fim"].required = False
        if veiculo is not None:
            _fixar(self.fields["veiculo"], veiculo)
        if unidade is not None:
            _fixar(self.fields["unidade"], unidade)
        if condominio is not None:
            self.instance.condominio = condominio
